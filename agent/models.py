"""Capability artifact schema.

The typed, versioned contract that ``agent.discover`` produces once and
``agent.replay`` consumes on every production invocation. See
``schema/DESIGN.md`` for the reasoning behind every decision here; this
module implements that document and nothing more.

Layering (DESIGN.md §1):

* **mechanical layer** -- ``inputs``, ``outputs``, ``steps``, ``target``.
  A faithful, generalized transcript of one successful discovery run.
* **policy layer** -- ``risk_class``, ``requires_confirmation``,
  ``expected_outcomes``, ``guardrails``, ``escalation_policy``. Authored
  separately, never derived from a trajectory, because a single green
  run observes only the happy path.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Shared enums / literal types
# ---------------------------------------------------------------------------

class ParamType(str, Enum):
    """Types a capability input or output may declare (DESIGN.md §4)."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    MONEY = "money"  # read as a rendered string, e.g. "$18,750.42"


# Replay-side action vocabulary (DESIGN.md §7). The discovery-side tool
# vocabulary is a separate deliverable; this is its minimal executable
# residue.
ActionType = Literal[
    "navigate",
    "fill",
    "select_option",
    "click",
    "press_key",
    "extract",
]

RiskClass = Literal["read_only", "mutating"]

# What to do when a failure trigger fires (DESIGN.md §5). ``retry`` means
# "exhaust max_retries_per_step on SETTLE/CHECK, then escalate"; ``abort``
# means stop immediately with no human handoff.
EscalationAction = Literal["retry", "escalate", "fail", "abort"]

OutcomeClassification = Literal["business_outcome", "hard_failure"]


# ---------------------------------------------------------------------------
# Locators (DESIGN.md §2)
# ---------------------------------------------------------------------------

class LocatorStrategy(BaseModel):
    """One way to find a step's target element.

    Steps carry a *ranked list* of these; replay uses the first that
    resolves to exactly one visible element. ``kind`` is an open string
    (not an enum) and extra fields are allowed, so a new strategy type
    can be added without a schema migration.

    Known ``kind`` values, ranked most- to least- semantically stable:

    1. ``aria_role``  -- ``role`` + ``name`` (+ ``exact``). Primary.
    2. ``text_label`` -- ``text`` or ``label``. Fallback.
    3. ``test_id``    -- reserved; not present in legacy apps.
    4. ``css`` / ``xpath`` -- reserved, last resort.

    ``name`` / ``text`` / ``label`` may contain ``{{input_name}}``
    templates that replay fills per invocation.
    """

    model_config = ConfigDict(extra="allow")

    kind: str = Field(description="e.g. 'aria_role', 'text_label'")
    rank: int = Field(ge=1, description="1 = try first")

    # aria_role
    role: Optional[str] = None
    name: Optional[str] = None
    exact: bool = Field(
        default=True, description="exact vs substring match on name/text"
    )

    # text_label
    text: Optional[str] = None
    label: Optional[str] = None

    # common
    within: Optional[str] = Field(
        default=None,
        description="named region / parent locator to scope the search to",
    )
    nth: Optional[int] = Field(
        default=None, description="disambiguate when several elements match"
    )
    note: Optional[str] = None

    @model_validator(mode="after")
    def _has_enough_to_resolve(self) -> "LocatorStrategy":
        if self.kind == "aria_role" and not (self.role or self.name):
            raise ValueError("aria_role locator needs role and/or name")
        if self.kind == "text_label" and not (self.text or self.label):
            raise ValueError("text_label locator needs text or label")
        return self


# ---------------------------------------------------------------------------
# Checkpoints (DESIGN.md §3)
# ---------------------------------------------------------------------------

CheckpointKind = Literal[
    "element_visible",
    "text_present",
    "outputs_non_empty",
    "outcome_matched",
    "any_of",
    "all_of",
]


class Checkpoint(BaseModel):
    """How a step's success is verified in the CHECK phase.

    ``any_of`` is load-bearing: the terminal step is satisfied if the
    happy-path element appeared **or** a recognized expected outcome was
    detected -- otherwise a legitimate "no such record" answer looks
    like a hung page. Extra fields allowed for forward-compatible kinds.
    """

    model_config = ConfigDict(extra="allow")

    kind: CheckpointKind
    description: Optional[str] = None

    # element_visible
    locator: Optional[LocatorStrategy] = None

    # text_present
    text: Optional[str] = None

    # outputs_non_empty  (None => every required output)
    outputs: Optional[list[str]] = None

    # outcome_matched  (None => any recognized expected outcome)
    outcome_code: Optional[str] = None

    # any_of / all_of
    checks: Optional[list["Checkpoint"]] = None

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> "Checkpoint":
        if self.kind == "element_visible" and self.locator is None:
            raise ValueError("element_visible checkpoint needs a locator")
        if self.kind == "text_present" and not self.text:
            raise ValueError("text_present checkpoint needs text")
        if self.kind in ("any_of", "all_of") and not self.checks:
            raise ValueError(f"{self.kind} checkpoint needs a non-empty 'checks'")
        return self


# ---------------------------------------------------------------------------
# Steps (DESIGN.md §3, §7)
# ---------------------------------------------------------------------------

class SettleSpec(BaseModel):
    """The SETTLE phase between acting and checking."""

    model_config = ConfigDict(extra="forbid")

    wait_for: Literal["dom_stable", "network_idle", "checkpoint"] = "dom_stable"
    max_wait_seconds: float = Field(
        default=10.0,
        gt=0,
        description=(
            "upper bound on SETTLE; set above any known injected delay "
            "(the target app's slow detail page sleeps ~4s)"
        ),
    )


class Step(BaseModel):
    """One ordered ACT -> SETTLE -> CHECK unit.

    Retries (automatic or human-resumed) only ever redo SETTLE/CHECK,
    never re-run the action.
    """

    model_config = ConfigDict(extra="forbid")

    ordinal: int = Field(ge=1)
    id: str
    description: str
    action: ActionType

    # ranked fallback locators; empty only for 'navigate'
    locators: list[LocatorStrategy] = Field(default_factory=list)

    # action-specific payloads
    target_route: Optional[str] = Field(
        default=None, description="for action='navigate'"
    )
    input_name: Optional[str] = Field(
        default=None, description="for 'fill'/'select_option': the input to use"
    )
    value_template: Optional[str] = Field(
        default=None,
        description="literal or '{{input_name}}' value for 'fill'/'select_option'",
    )
    key: Optional[str] = Field(default=None, description="for action='press_key'")
    output_name: Optional[str] = Field(
        default=None, description="for 'extract': the output to populate"
    )

    settle: SettleSpec = Field(default_factory=SettleSpec)
    checkpoint: Checkpoint
    retryable: bool = Field(
        default=True, description="may SETTLE/CHECK be redone on failure"
    )

    @model_validator(mode="after")
    def _shape_matches_action(self) -> "Step":
        if self.action == "navigate" and not self.target_route:
            raise ValueError("navigate step needs target_route")
        if self.action in ("fill", "select_option") and not self.value_template:
            raise ValueError(f"{self.action} step needs value_template")
        if self.action == "extract" and not self.output_name:
            raise ValueError("extract step needs output_name")
        if self.action == "press_key" and not self.key:
            raise ValueError("press_key step needs key")
        if self.action != "navigate" and not self.locators:
            raise ValueError(f"{self.action} step needs at least one locator")
        return self

    @model_validator(mode="after")
    def _locators_ranked(self) -> "Step":
        ranks = [loc.rank for loc in self.locators]
        if ranks and sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError("locator ranks must be 1..N with no gaps or dupes")
        return self


# ---------------------------------------------------------------------------
# Typed inputs / outputs (DESIGN.md §4)
# ---------------------------------------------------------------------------

class InputParam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: ParamType
    required: bool = True
    description: str
    example: Optional[str] = None
    allowed_values: Optional[list[str]] = Field(
        default=None, description="closed set, if the input is an enum"
    )


class OutputParam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: ParamType
    required: bool = Field(
        default=True, description="must replay produce this on the happy path"
    )
    description: str


# ---------------------------------------------------------------------------
# Expected business outcomes (DESIGN.md §6)
# ---------------------------------------------------------------------------

DetectionKind = Literal[
    "text_present",
    "aria_visible",
    "http_status",
    "url_matches",
    "any_of",
    "all_of",
]


class DetectionRule(BaseModel):
    """How replay recognizes an expected outcome on the live page.

    Mirrors the checkpoint composite shape. Keys off label / structural
    signals, never a specific member's data. Extra fields allowed.
    """

    model_config = ConfigDict(extra="allow")

    kind: DetectionKind

    # text_present
    text: Optional[str] = None

    # aria_visible
    role: Optional[str] = None
    name: Optional[str] = None
    exact: bool = False

    # http_status
    status: Optional[int] = None

    # url_matches
    pattern: Optional[str] = None

    # any_of / all_of
    rules: Optional[list["DetectionRule"]] = None

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> "DetectionRule":
        need = {
            "text_present": self.text,
            "aria_visible": self.role or self.name,
            "http_status": self.status,
            "url_matches": self.pattern,
        }
        if self.kind in need and not need[self.kind]:
            raise ValueError(f"{self.kind} detection rule is missing its field")
        if self.kind in ("any_of", "all_of") and not self.rules:
            raise ValueError(f"{self.kind} detection rule needs non-empty 'rules'")
        return self


class ExpectedOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="stable identifier, e.g. 'MEMBER_NOT_FOUND'")
    description: str
    classification: OutcomeClassification
    terminal: bool = Field(
        default=True, description="does detecting this end the run"
    )
    detection: DetectionRule


# ---------------------------------------------------------------------------
# Policy: guardrails + escalation (DESIGN.md §5, §7)
# ---------------------------------------------------------------------------

class Guardrails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowlist_routes: list[str] = Field(
        description="URL path patterns replay may visit; narrowed to the "
        "successful trajectory, not discovery-time scope"
    )
    allowlist_action_types: list[ActionType] = Field(
        description="must cover every action the steps use"
    )
    denylist_text_patterns: list[str] = Field(
        default_factory=list,
        description="abort if any of these appear on a page mid-replay",
    )
    max_steps: int = Field(default=25, gt=0)
    forbid_offdomain_navigation: bool = True


class EscalationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_retries_per_step: int = Field(
        default=2, ge=0, description="SETTLE/CHECK redos; the action is never re-run"
    )
    retry_backoff_seconds: float = Field(default=1.5, ge=0)
    human_handoff_timeout_seconds: float = Field(
        default=900.0,
        gt=0,
        description="how long replay waits for the operator (same live session)",
    )

    on_step_timeout: EscalationAction = "retry"
    on_hard_failure: EscalationAction = "escalate"
    on_unrecognized_dialog: EscalationAction = "escalate"
    on_checkpoint_failure: EscalationAction = "escalate"


# ---------------------------------------------------------------------------
# Provenance + top-level Capability (DESIGN.md §8)
# ---------------------------------------------------------------------------

class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: str
    base_url: str
    entry_route: str = "/"


class DiscoveryProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    model: str
    goal: str
    completed_at: str  # ISO 8601


class Capability(BaseModel):
    """The whole capability artifact."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    capability_id: str
    version: str = Field(description="semver of this artifact")
    description: str
    target: Target

    # --- mechanical layer (observed) ---
    inputs: list[InputParam] = Field(default_factory=list)
    outputs: list[OutputParam] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)

    # --- policy layer (authored separately) ---
    risk_class: RiskClass
    requires_confirmation: bool = False
    expected_outcomes: list[ExpectedOutcome] = Field(default_factory=list)
    guardrails: Guardrails
    escalation_policy: EscalationPolicy = Field(default_factory=EscalationPolicy)

    # --- provenance ---
    discovery: Optional[DiscoveryProvenance] = None
    policy_authored_by: Optional[str] = Field(
        default=None,
        description="replay refuses to run until this is set",
    )

    # -- cross-field validation (DESIGN.md §8) --

    @model_validator(mode="after")
    def _ordinals_contiguous(self) -> "Capability":
        ords = [s.ordinal for s in self.steps]
        if ords and sorted(ords) != list(range(1, len(ords) + 1)):
            raise ValueError("step ordinals must be 1..N with no gaps or dupes")
        return self

    @model_validator(mode="after")
    def _steps_reference_declared_params(self) -> "Capability":
        input_names = {p.name for p in self.inputs}
        output_names = {p.name for p in self.outputs}
        for s in self.steps:
            if s.input_name and s.input_name not in input_names:
                raise ValueError(f"step {s.id}: unknown input {s.input_name!r}")
            templated = list(_template_tokens(s.value_template or ""))
            for loc in s.locators:
                for field in (loc.name, loc.text, loc.label):
                    templated.extend(_template_tokens(field or ""))
            for tok in templated:
                if tok not in input_names:
                    raise ValueError(f"step {s.id}: unknown input {tok!r}")
            if s.output_name and s.output_name not in output_names:
                raise ValueError(f"step {s.id}: unknown output {s.output_name!r}")
        return self

    @model_validator(mode="after")
    def _guardrails_cover_actions(self) -> "Capability":
        used = {s.action for s in self.steps}
        missing = used - set(self.guardrails.allowlist_action_types)
        if missing:
            raise ValueError(
                f"guardrails.allowlist_action_types missing {sorted(missing)}"
            )
        return self

    @model_validator(mode="after")
    def _outcome_codes_resolve(self) -> "Capability":
        codes = {o.code for o in self.expected_outcomes}
        for s in self.steps:
            for cp in _walk_checkpoints(s.checkpoint):
                if (
                    cp.kind == "outcome_matched"
                    and cp.outcome_code is not None
                    and cp.outcome_code not in codes
                ):
                    raise ValueError(
                        f"step {s.id}: outcome_matched references undeclared "
                        f"code {cp.outcome_code!r}"
                    )
        return self

    @model_validator(mode="after")
    def _has_one_terminal_extract(self) -> "Capability":
        if not self.steps:
            return self
        satisfiers = [
            s
            for s in self.steps
            if any(
                cp.kind == "outputs_non_empty"
                for cp in _walk_checkpoints(s.checkpoint)
            )
        ]
        if len(satisfiers) != 1:
            raise ValueError(
                "expected exactly one step with an outputs_non_empty checkpoint, "
                f"found {len(satisfiers)}"
            )
        return self

    def to_json(self) -> str:
        return self.model_dump_json(indent=2, exclude_none=True)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _template_tokens(s: str) -> list[str]:
    """Pull ``name`` out of every ``{{ name }}`` in *s*."""
    import re

    return [m.strip() for m in re.findall(r"\{\{\s*([^}]+?)\s*\}\}", s)]


def _walk_checkpoints(cp: Checkpoint):
    yield cp
    for child in cp.checks or []:
        yield from _walk_checkpoints(child)


# rebuild models that use forward refs
Checkpoint.model_rebuild()
DetectionRule.model_rebuild()
