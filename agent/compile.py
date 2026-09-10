"""The compiler: turn a :class:`~agent.trajectory.Trajectory` into a
:class:`~agent.models.Capability`.

This is the seam between discovery's raw, un-compiled output and the
artifact a caller actually invokes. Two hard rules govern everything
here:

1. **The compiler never derives policy from the trajectory.** A single
   successful run only ever proves what happened on the happy path. It
   has no evidence about what business outcomes exist, what the risk
   classification is, what the escalation policy should be, or which
   routes are legitimate beyond the ones it visited. Every one of those
   fields comes from a separately hand-authored :class:`PolicySpec`
   that the compiler *merges in*, never invents. A missing required
   policy field fails compilation loudly (see :func:`compile_capability`)
   rather than defaulting to something permissive.

2. **The compiler never fabricates a step, locator strategy, or scope
   that discovery did not actually exercise.** Where the trajectory has
   a gap (see the ``search_field`` radio discussion in
   ``agent/policies/member_lookup.py``), the compiler records the gap in
   the :class:`CompileNotes` sidecar and produces a narrower capability
   -- it does not paper over the gap with a plausible-looking step.

What *is* mechanical, and is compiled straight from the trajectory:

* each ``ok`` :class:`~agent.trajectory.TrajectoryStep` (except ``done``)
  becomes a :class:`~agent.models.Step`, using the exact accessible
  role+name that resolved live as the rank-1 locator;
* checkpoints are derived from the compiled step sequence -- step *i*'s
  checkpoint asserts step *i+1*'s target becomes visible, wrapped in
  ``any_of`` with ``outcome_matched`` so a recognized business outcome
  also satisfies it; the final step's checkpoint is ``outputs_non_empty``;
* ``extract`` steps compile their locator and checkpoint from the
  field's *label* (``"Savings balance"``), never its discovered *value*
  (``"$18,750.42"``);
* literal values that came from the goal's input (``"M1001"``) are
  parameterized into ``{{input_name}}`` references;
* ``guardrails.allowlist_routes`` is narrowed to the routes actually
  visited on the successful trajectory.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.models import (
    Capability,
    Checkpoint,
    DiscoveryProvenance,
    EscalationPolicy,
    ExpectedOutcome,
    Guardrails,
    InputParam,
    LocatorStrategy,
    OutputParam,
    ParamType,
    RiskClass,
    SettleSpec,
    Step,
    Target,
)
from agent.trajectory import Trajectory, TrajectoryStep

_MONEY_RE = re.compile(r"^\s*[-+]?\$\s?[\d,]+\.\d{2}\s*$")
_DATE_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$")

# trajectory tools that carry an on-page action (everything except `done`)
_ACTION_TOOLS = ("navigate", "click", "type", "extract")
_TOOL_TO_ACTION = {"navigate": "navigate", "click": "click", "type": "fill",
                   "extract": "extract"}


class CompileError(Exception):
    """Compilation cannot proceed. Raised instead of guessing."""


# ---------------------------------------------------------------------------
# Hand-authored compiler inputs
# ---------------------------------------------------------------------------

class InputBinding(BaseModel):
    """Maps one capability :class:`InputParam` to the literal value that
    stood in for it during discovery.

    Discovery only ever recorded a free-text goal, not structured
    parameters, so the value->parameter mapping has to be authored by
    hand. It is still *mechanical* (it describes what the run was given,
    not a policy decision) -- it just cannot be recovered from the
    trajectory alone. The compiler uses ``discovered_value`` to find the
    literal in ``type`` steps and in resolved locator names and replace
    it with ``{{param.name}}``.
    """

    model_config = ConfigDict(extra="forbid")

    param: InputParam
    discovered_value: str = Field(
        description="the literal typed/observed for this param during discovery"
    )


class PolicySpec(BaseModel):
    """The hand-authored policy layer the compiler merges into the
    mechanical transcript.

    Everything here is authored deliberately, with knowledge of the
    target app's failure modes -- none of it is or can be derived from a
    single green trajectory. Required fields have **no default**: leaving
    one out is a :class:`pydantic.ValidationError` at construction, which
    :func:`compile_capability` surfaces as a clear :class:`CompileError`
    rather than silently falling back to something permissive.
    """

    model_config = ConfigDict(extra="forbid")

    # -- capability identity (hand-authored; not present in any trajectory) --
    capability_id: str
    version: str = Field(description="semver of the compiled artifact")
    description: str
    app: str = Field(description="target app name for provenance")

    # -- policy layer (never derived from a trajectory) --
    risk_class: RiskClass
    requires_confirmation: Optional[bool] = Field(
        default=None,
        description="None => derive the safe default from risk_class",
    )
    expected_outcomes: list[ExpectedOutcome]
    escalation_policy: EscalationPolicy
    denylist_text_patterns: list[str] = Field(default_factory=list)
    extra_allowlist_routes: list[str] = Field(
        default_factory=list,
        description=(
            "routes to allow *in addition to* the ones the trajectory "
            "visited -- e.g. a branch of the flow a second discovery run "
            "will cover. Authored, justified, not a wildcard."
        ),
    )
    max_steps: Optional[int] = Field(
        default=None, description="None => compiled step count plus a small margin"
    )
    output_name_mapping: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "maps each output name a trajectory happened to capture "
            "(e.g. 'member_name') to the canonical contract output name the "
            "policy author intends (e.g. 'full_name'). A normalization step, "
            "not a fabrication: the author controls the label, the compiler "
            "just applies it. Every output the trajectory actually produced "
            "must appear as a key -- an unmapped output fails compilation "
            "rather than passing through silently."
        ),
    )
    known_gaps: list[str] = Field(
        default_factory=list,
        description=(
            "trajectory gaps the author knows about and has decided NOT to "
            "have the compiler paper over (e.g. a branch of the flow this run "
            "never exercised). Copied verbatim into the compile-notes sidecar."
        ),
    )
    policy_authored_by: str
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Compiler output sidecar
# ---------------------------------------------------------------------------

class CompileNotes(BaseModel):
    """Everything the compiler decided that a reader should be able to
    audit: synthesized steps, generalizations, parameterizations,
    trajectory gaps, and guardrail narrowing.

    Written next to the artifact (``*.notes.md``). The artifact itself
    stays a clean, schema-valid :class:`Capability`; this is where the
    "why" lives.
    """

    model_config = ConfigDict(extra="forbid")

    trajectory_run_id: str
    capability_id: str
    synthesized: list[str] = Field(default_factory=list)
    generalizations: list[str] = Field(default_factory=list)
    parameterizations: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    guardrail_narrowing: list[str] = Field(default_factory=list)

    def to_markdown(self) -> str:
        def _section(title: str, items: list[str]) -> str:
            if not items:
                return f"## {title}\n\n_none_\n"
            body = "\n".join(f"- {line}" for line in items)
            return f"## {title}\n\n{body}\n"

        return "\n".join([
            f"# Compile notes -- {self.capability_id}",
            "",
            f"Compiled from trajectory `{self.trajectory_run_id}`.",
            "",
            "Every entry below is a decision the compiler made that is not a",
            "1:1 copy of the trajectory. The policy layer of the artifact",
            "(risk class, expected outcomes, escalation, denylist) comes",
            "entirely from the hand-authored PolicySpec and is not listed",
            "here.",
            "",
            _section("Synthesized steps", self.synthesized),
            _section("Locator generalizations", self.generalizations),
            _section("Value parameterizations", self.parameterizations),
            _section("Trajectory gaps (NOT filled by the compiler)", self.gaps),
            _section("Guardrail narrowing", self.guardrail_narrowing),
        ])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "step"


def _infer_type(value: str) -> ParamType:
    if _MONEY_RE.match(value):
        return ParamType.MONEY
    if _DATE_RE.match(value):
        return ParamType.DATE
    return ParamType.STRING


def _route_of(url_or_path: str, dynamic_values: list[str]) -> str:
    """Path of a visited URL, query stripped, with any path segment that
    equals a discovered input value replaced by ``*``."""
    path = urlsplit(url_or_path).path or "/"
    segs = path.split("/")
    segs = ["*" if (s and s in dynamic_values) else s for s in segs]
    route = "/".join(segs) or "/"
    return route


def _generalize_name(
    name: str,
    bindings: list[InputBinding],
    captured_values: list[str],
) -> tuple[str, bool, Optional[str]]:
    """Turn a resolved accessible name into one that works for any valid
    input.

    Two transforms, in order:

    1. Replace any discovered *input* value with its ``{{param}}``
       template -- replay fills it per invocation.
    2. If a discovered *output* value (a member's name, a balance) still
       appears, truncate the name before it and switch to a substring
       match: that value is data the caller does not know before this
       step, so it cannot be part of a stable locator.
    """
    note_bits: list[str] = []
    out = name
    for b in bindings:
        if b.discovered_value and b.discovered_value in out:
            out = out.replace(b.discovered_value, "{{%s}}" % b.param.name)
            note_bits.append(
                "templated input %r -> {{%s}}" % (b.discovered_value, b.param.name)
            )

    cut: Optional[int] = None
    for val in sorted((v for v in captured_values if v), key=len, reverse=True):
        if val in out:
            idx = out.index(val)
            cut = idx if cut is None else min(cut, idx)

    exact = True
    if cut is not None:
        truncated = out[:cut].rstrip(" ,;:-–")
        # the templating notes are moot once we truncate to a prefix
        note_bits = [
            "resolved accessible name %r contains member-specific data; "
            "substring match on the stable prefix %r (the rest -- the member "
            "name and id -- is not known before this step)" % (name, truncated)
        ]
        out = truncated
        exact = False

    return out, exact, ("; ".join(note_bits) or None)


# ---------------------------------------------------------------------------
# step compilation
# ---------------------------------------------------------------------------

def _entry_step(entry_path: str, notes: CompileNotes) -> dict:
    notes.synthesized.append(
        "step 1 `open_entry`: navigate to entry_path %r. This is the one step "
        "not sourced 1:1 from a TrajectoryStep -- the discovery loop navigates "
        "to the entry point before the first model turn, so it never appears "
        "as a tool call. It is compiled from `trajectory.entry_path`, a "
        "recorded fact, so replay starts from the same place. Mechanical, not "
        "policy." % entry_path
    )
    return dict(
        id="open_entry",
        description="Open the entry page",
        action="navigate",
        target_route=entry_path,
        locators=[],
        settle=SettleSpec(wait_for="dom_stable", max_wait_seconds=5),
    )


def _canonical_output(discovered: str, mapping: dict[str, str]) -> str:
    """The contract name for an output the trajectory captured as
    *discovered*.

    The policy author must have an entry for every output the run
    produced -- a name that fell through would ship in the artifact
    unaccounted-for.
    """
    if discovered not in mapping:
        raise CompileError(
            "trajectory captured an output named %r that PolicySpec."
            "output_name_mapping does not cover. Add a mapping entry "
            "(e.g. %r -> the canonical contract name, or %r -> %r to keep "
            "it) -- the compiler will not pass an output through unmapped."
            % (discovered, discovered, discovered, discovered)
        )
    return mapping[discovered]


def _compile_action_step(
    ts: TrajectoryStep,
    bindings: list[InputBinding],
    captured_values: list[str],
    output_name_mapping: dict[str, str],
    notes: CompileNotes,
) -> dict:
    action = _TOOL_TO_ACTION[ts.tool]
    resolved = ts.resolved
    if resolved is None:
        raise CompileError(
            f"trajectory step {ts.index} ({ts.tool}) has no resolved locator; "
            "cannot compile a locator for it"
        )

    raw: dict = dict(description=(ts.rationale or f"{ts.tool} {resolved.name!r}"),
                     action=action)

    if ts.tool == "extract":
        raw.update(_compile_extract(ts, output_name_mapping, notes))
    else:
        name, exact, gnote = _generalize_name(resolved.name, bindings, captured_values)
        loc_kwargs = dict(kind="aria_role", rank=1, role=resolved.role,
                          name=name, exact=exact)
        if not exact and ts.tool == "click":
            loc_kwargs["nth"] = 0
            gnote = (gnote or "") + \
                "; nth=0 -- a substring match can hit several result rows, " \
                "take the first"
        if gnote:
            loc_kwargs["note"] = gnote
            notes.generalizations.append(
                "step for %r: %s" % (ts.rationale or resolved.name, gnote)
            )
        raw["locators"] = [LocatorStrategy(**loc_kwargs)]
        raw["id"] = _step_id(ts, name)

    if ts.tool == "type":
        fill = _compile_fill(ts, bindings, notes)
        raw.update(fill)
        raw["settle"] = SettleSpec(wait_for="dom_stable", max_wait_seconds=2)
        if fill.get("input_name"):
            raw["id"] = "enter_" + _slug(fill["input_name"])
    elif ts.tool == "navigate":
        raw["target_route"] = ts.navigated_to or ts.params.get("path")
        raw["settle"] = SettleSpec(wait_for="dom_stable", max_wait_seconds=5)

    # settle bounds: compile defaults keyed by action kind. The click that
    # loads a member detail page can hit the target app's injected ~4s slow
    # load, so every navigating click gets a generous network-idle bound.
    if ts.tool == "click":
        raw["settle"] = SettleSpec(wait_for="network_idle", max_wait_seconds=12)
    elif ts.tool == "extract":
        raw["settle"] = SettleSpec(wait_for="dom_stable", max_wait_seconds=2)

    return raw


def _step_id(ts: TrajectoryStep, name: str) -> str:
    clean = name.replace("{{", "").replace("}}", "")
    if ts.tool == "type":
        return "enter_" + _slug(clean)
    if ts.tool == "click":
        return "click_" + _slug(clean)
    if ts.tool == "navigate":
        return "navigate_" + _slug(clean)
    return _slug(clean)


def _compile_extract(
    ts: TrajectoryStep,
    output_name_mapping: dict[str, str],
    notes: CompileNotes,
) -> dict:
    ex = ts.extraction
    if ex is None:
        raise CompileError(
            f"trajectory step {ts.index} is an extract with no ExtractionResult"
        )
    if not ex.label:
        raise CompileError(
            f"trajectory step {ts.index} ({ex.output_name}) captured no label. "
            "The compiler builds an extract locator from the field's label, "
            "never its literal value -- an artifact must work for any member. "
            "This needs a discovery run that resolves the field's label."
        )
    canonical = _canonical_output(ex.output_name, output_name_mapping)
    note = "value is the cell paired with this rowheader"
    if ex.label_source:
        note += f" (label found via {ex.label_source})"
    notes.parameterizations.append(
        "extract %r: locator + checkpoint compiled from the label %r, not the "
        "discovered value %r" % (canonical, ex.label, ex.value)
    )
    return dict(
        id=f"extract_{_slug(canonical)}",
        output_name=canonical,
        locators=[
            LocatorStrategy(kind="aria_role", rank=1, role="rowheader",
                            name=ex.label, exact=True, note=note),
            LocatorStrategy(kind="text_label", rank=2, label=ex.label,
                            note="label association confirmed live during discovery"),
        ],
    )


def _compile_fill(
    ts: TrajectoryStep,
    bindings: list[InputBinding],
    notes: CompileNotes,
) -> dict:
    text = ts.params.get("text", "")
    for b in bindings:
        if b.discovered_value == text:
            notes.parameterizations.append(
                "fill step: literal %r parameterized to input {{%s}}"
                % (text, b.param.name)
            )
            return dict(input_name=b.param.name,
                        value_template="{{%s}}" % b.param.name)
    notes.gaps.append(
        "fill step types the literal %r, which is not bound to any InputParam; "
        "left as a hardcoded literal. If a caller should supply this value, add "
        "an InputBinding for it." % text
    )
    return dict(value_template=text)


# ---------------------------------------------------------------------------
# checkpoints
# ---------------------------------------------------------------------------

def _loc_copy(loc: LocatorStrategy) -> LocatorStrategy:
    return LocatorStrategy(**loc.model_dump(exclude_none=True))


def _checkpoint_for(index: int, steps: list[dict], output_names: list[str]) -> Checkpoint:
    """Step *index*'s checkpoint.

    Non-terminal: reaching step *index+1*'s target element is what
    actually proves this step succeeded. Terminal: every declared output
    is populated. Either way it is wrapped ``any_of`` with
    ``outcome_matched`` so a recognized business outcome from the
    PolicySpec satisfies the checkpoint too, rather than looking like a
    hung page that burns the retry budget.
    """
    if index + 1 < len(steps):
        nxt = steps[index + 1]
        target = _loc_copy(nxt["locators"][0])
        return Checkpoint(
            kind="any_of",
            description=(
                f"step succeeded: the next step's target "
                f"({target.role or ''} {target.name or target.label or ''}) is "
                f"visible, OR a recognized business outcome was detected"
            ),
            checks=[
                Checkpoint(kind="element_visible", locator=target),
                Checkpoint(kind="outcome_matched"),
            ],
        )
    return Checkpoint(
        kind="any_of",
        description="all declared outputs extracted, OR a recognized business outcome",
        checks=[
            Checkpoint(kind="outputs_non_empty", outputs=output_names,
                       description="every page-extracted output is populated"),
            Checkpoint(kind="outcome_matched"),
        ],
    )


# ---------------------------------------------------------------------------
# top level
# ---------------------------------------------------------------------------

def compile_capability(
    trajectory: Trajectory,
    policy: PolicySpec,
    input_bindings: list[InputBinding],
) -> tuple[Capability, CompileNotes]:
    """Compile *trajectory* + hand-authored *policy* into a Capability.

    Raises :class:`CompileError` -- never returns a partial or
    permissive artifact -- if the trajectory did not complete, if a
    required policy field is missing, or if a step cannot be compiled
    without guessing.
    """
    if not isinstance(policy, PolicySpec):
        raise CompileError(
            "policy must be a PolicySpec instance (the hand-authored policy "
            "layer); got %r" % type(policy).__name__
        )
    if trajectory.outcome != "completed":
        raise CompileError(
            f"trajectory {trajectory.run_id} outcome is {trajectory.outcome!r}, "
            "not 'completed'. Only a successful run can be compiled -- a failed "
            "run proves nothing about the happy path."
        )
    if not policy.expected_outcomes:
        raise CompileError(
            "PolicySpec.expected_outcomes is empty. A capability with no "
            "declared business outcomes treats every non-happy page (not "
            "found, access denied) as a hard failure and escalates a human "
            "for a correct answer. Declare at least the not-found case."
        )

    notes = CompileNotes(
        trajectory_run_id=trajectory.run_id,
        capability_id=policy.capability_id,
    )
    for gap in policy.known_gaps:
        notes.gaps.append("(declared in PolicySpec) " + gap)
    dynamic_values = [b.discovered_value for b in input_bindings]
    captured_values = [r.value for r in trajectory.captured_outputs.values()]

    # Every output the run actually produced (whatever name it was
    # captured under) must have an entry in output_name_mapping. Check the
    # whole set up front so the author sees all the missing names at once,
    # not one compile failure at a time.
    mapping = policy.output_name_mapping
    produced = set(trajectory.captured_outputs) | set(trajectory.cited_outputs)
    unmapped = sorted(n for n in produced if n not in mapping)
    if unmapped:
        raise CompileError(
            "trajectory produced output(s) %s with no entry in "
            "PolicySpec.output_name_mapping. Map every output the run "
            "captured to its canonical contract name (use name -> name to "
            "keep one as-is); the compiler will not ship an unmapped output."
            % unmapped
        )
    for src, dst in mapping.items():
        if src != dst:
            notes.parameterizations.append(
                "output name normalized: %r (discovered) -> %r (contract)"
                % (src, dst)
            )

    action_steps = [
        s for s in trajectory.steps
        if s.status == "ok" and s.tool in _ACTION_TOOLS
    ]
    if not action_steps:
        raise CompileError(
            f"trajectory {trajectory.run_id} has no successful action steps"
        )

    # --- 1. mechanical: build step dicts (checkpoints assigned in pass 2) ---
    raw_steps: list[dict] = [_entry_step(trajectory.entry_path, notes)]
    for ts in action_steps:
        raw_steps.append(
            _compile_action_step(ts, input_bindings, captured_values, mapping, notes)
        )

    extracted_outputs = [
        r["output_name"] for r in raw_steps if r.get("action") == "extract"
    ]
    if not extracted_outputs:
        raise CompileError("no extract steps -- capability has no outputs")
    notes.gaps.append(
        "extract steps carry no `within` scope: discovery resolved the value "
        "cells directly and never resolved the enclosing 'Member record for ...' "
        "table, so the compiler has no verified accessible name to scope to. "
        "Locators fall back to a page-wide rowheader match. A second discovery "
        "pass that resolves the record table (or a hand-authored `within`) "
        "would harden these against a same-labelled row elsewhere on the page. "
        "The compiler does not guess the table's name."
    )

    # --- 2. checkpoints + Step construction ---
    steps: list[Step] = []
    for i, raw in enumerate(raw_steps):
        raw = dict(raw)
        raw["ordinal"] = i + 1
        raw["checkpoint"] = _checkpoint_for(i, raw_steps, extracted_outputs)
        raw.setdefault("locators", [])
        try:
            steps.append(Step(**raw))
        except ValidationError as e:  # pragma: no cover - defensive
            raise CompileError(f"step {i + 1} failed schema validation: {e}") from e

    # --- 3. outputs (names normalized via output_name_mapping) ---
    outputs: list[OutputParam] = []
    for ts in action_steps:
        if ts.tool != "extract":
            continue
        ex = ts.extraction
        outputs.append(OutputParam(
            name=mapping[ex.output_name],
            type=_infer_type(ex.value),
            required=True,
            description=f"{ex.label} from the member record.",
        ))

    # The discovery run's `done` call cited a set of captured outputs as
    # the answer. Carry that check across the rename: every cited output
    # must have become a required output of the compiled capability.
    cited_canonical = sorted(mapping[n] for n in trajectory.cited_outputs)
    required_names = sorted(o.name for o in outputs)
    if cited_canonical != required_names:
        raise CompileError(
            "the discovery run's `done` cited outputs %s (canonical: %s) but "
            "the compiled required outputs are %s. Every cited output must be "
            "an extract step in the trajectory."
            % (sorted(trajectory.cited_outputs), cited_canonical, required_names)
        )

    outputs.append(OutputParam(
        name="outcome_code", type=ParamType.STRING, required=False,
        description=(
            "Set by replay on every run: 'SUCCESS' on the happy path, "
            "otherwise the matched expected-outcome code."
        ),
    ))

    # --- 4. guardrails: narrow routes to what the trajectory actually hit ---
    visited: list[str] = [_route_of(trajectory.entry_path, dynamic_values)]
    for ts in trajectory.steps:
        for url in (ts.page_url, ts.navigated_to):
            if url:
                r = _route_of(url, dynamic_values)
                if r not in visited:
                    visited.append(r)
    for extra in policy.extra_allowlist_routes:
        if extra not in visited:
            visited.append(extra)
    allowlist_routes = sorted(visited)
    notes.guardrail_narrowing.append(
        "allowlist_routes narrowed to the routes visited on the successful "
        "trajectory: %s%s" % (
            ", ".join(sorted(visited)),
            " (plus hand-authored extras: %s)" % ", ".join(policy.extra_allowlist_routes)
            if policy.extra_allowlist_routes else "",
        )
    )
    used_actions = sorted({s.action for s in steps})
    notes.guardrail_narrowing.append(
        "allowlist_action_types set to exactly the verbs the steps use: %s"
        % ", ".join(used_actions)
    )

    max_steps = policy.max_steps or (len(steps) + 3)
    guardrails = Guardrails(
        allowlist_routes=allowlist_routes,
        allowlist_action_types=used_actions,
        denylist_text_patterns=policy.denylist_text_patterns,
        max_steps=max_steps,
        forbid_offdomain_navigation=True,
    )

    requires_confirmation = (
        policy.requires_confirmation
        if policy.requires_confirmation is not None
        else policy.risk_class == "mutating"
    )

    provenance = DiscoveryProvenance(
        run_id=trajectory.run_id,
        model=trajectory.model,
        goal=trajectory.goal,
        completed_at=trajectory.finished_at or trajectory.started_at,
    )

    try:
        capability = Capability(
            capability_id=policy.capability_id,
            version=policy.version,
            description=policy.description,
            target=Target(
                app=policy.app,
                base_url=trajectory.target_base_url,
                entry_route=trajectory.entry_path,
            ),
            inputs=[b.param for b in input_bindings],
            outputs=outputs,
            steps=steps,
            risk_class=policy.risk_class,
            requires_confirmation=requires_confirmation,
            expected_outcomes=policy.expected_outcomes,
            guardrails=guardrails,
            escalation_policy=policy.escalation_policy,
            discovery=provenance,
            policy_authored_by=policy.policy_authored_by,
        )
    except ValidationError as e:
        raise CompileError(f"assembled capability failed schema validation: {e}") from e

    return capability, notes
