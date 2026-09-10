"""The replay engine: execute a compiled Capability deterministically.

Given a :class:`~agent.models.Capability` artifact and a set of input
parameters, drive the live target app through the recorded steps with
**no LLM anywhere in the decision loop**. Every choice replay makes is a
lookup against the artifact.

Execution model, per step: **ACT -> SETTLE -> CHECK**.

* **ACT** resolves the step's rank-ordered locators (falling down the
  list on failure) and performs the one action. It runs *exactly once*.
* **SETTLE** waits for the page to stabilise, bounded by the step's
  ``SettleSpec``.
* **CHECK** is a dialog check plus the step's checkpoint, evaluated
  against the live page (see :mod:`agent.checkpoints`,
  :mod:`agent.outcome_detection`).

Retries -- automatic (``escalation_policy.max_retries_per_step``) or, in
a later phase, a human-resumed run -- only ever redo SETTLE/CHECK. ACT
is a separate method called once, before the retry loop is entered, so
re-firing an action that already completed (double-submitting a mutating
step) is *structurally* impossible, not merely avoided by convention.

Result contract (Section 3.3), made structural via four distinct types:

* :class:`Success` -- the capability completed with every declared output.
* :class:`BusinessOutcome` -- a recognised ``expected_outcomes`` entry
  matched. A legitimate answer for the caller, not a failure.
* :class:`HardFailure` -- something genuinely broke; carries the step,
  what was expected, and what was observed.
* :class:`PendingEscalation` -- the escalation policy says *escalate*
  (not *retry*) and the human-handoff mechanism is switched off
  (``handoff_enabled=False``, e.g. in CI). Replay pauses cleanly and
  returns this. With handoff *enabled* (the default) replay instead
  records the blocked step in :class:`~agent.escalation.SessionStore`,
  keeps the live browser open, and blocks polling for an operator's
  resume up to ``escalation_policy.human_handoff_timeout_seconds`` --
  then either re-enters SETTLE/CHECK for the paused step (never ACT) or
  hard-fails on timeout.

Guardrails (``guardrails``) are enforced here at run time, not just
documented: route and action-type allowlists are checked before every
step (a violation hard-fails, it does not retry), and
``denylist_text_patterns`` is checked against the live page after each
settle and before any mutating action.
"""
from __future__ import annotations

import fnmatch
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Optional
from urllib.parse import urlsplit

from playwright.sync_api import Locator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout

from agent.checkpoints import CheckEnv, evaluate_checkpoint
from agent.escalation import DEFAULT_DB_PATH, SessionStore
from agent.models import Capability, LocatorStrategy, Step
from agent.outcome_detection import DetectionContext, detect_outcome
from agent.perception import Perception, ResolutionError

_MUTATING_ACTIONS = {"fill", "select_option", "click", "press_key"}
_REDACT_PLACEHOLDER = "<redacted>"


# ---------------------------------------------------------------------------
# Result contract
# ---------------------------------------------------------------------------

@dataclass
class ReplayResult:
    """Base for the four outcome types. ``status`` is the discriminator."""

    status: ClassVar[str] = "?"

    capability_id: str
    run_id: str
    steps_executed: int
    duration_s: float
    evidence_path: str

    def to_dict(self) -> dict[str, Any]:
        d = {"status": self.status}
        d.update(asdict(self))
        return d


@dataclass
class Success(ReplayResult):
    status: ClassVar[str] = "success"
    #: every declared output, plus ``outcome_code = "SUCCESS"``
    outputs: dict[str, str] = field(default_factory=dict)


@dataclass
class BusinessOutcome(ReplayResult):
    status: ClassVar[str] = "business_outcome"
    outcome_code: str = ""
    description: str = ""
    classification: str = "business_outcome"
    detected_at_step: str = ""
    #: any outputs captured before the outcome was recognised, plus
    #: ``outcome_code``
    outputs: dict[str, str] = field(default_factory=dict)


@dataclass
class HardFailure(ReplayResult):
    status: ClassVar[str] = "hard_failure"
    at_step_id: str = ""
    at_step_ordinal: int = 0
    phase: str = ""
    trigger: str = ""
    expected: str = ""
    observed: str = ""


@dataclass
class PendingEscalation(ReplayResult):
    status: ClassVar[str] = "pending_escalation"
    at_step_id: str = ""
    at_step_ordinal: int = 0
    phase: str = ""
    trigger: str = ""
    configured_action: str = ""
    expected: str = ""
    observed: str = ""
    note: str = (
        "escalation_policy says 'escalate' for this trigger; the human-handoff "
        "mechanism is a later phase. Replay has paused cleanly at this step."
    )


# ---------------------------------------------------------------------------
# internal signalling
# ---------------------------------------------------------------------------

class _StepFailure(Exception):
    """An ACT-phase failure: locator resolved nothing, or the action threw."""

    def __init__(self, expected: str, observed: str):
        self.expected = expected
        self.observed = observed
        super().__init__(observed)


@dataclass
class _CheckReport:
    ok: bool
    matched_outcome: Optional[Any]  # ExpectedOutcome
    unrecognized_dialog: bool
    settle_timed_out: bool
    detail: str


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    return "replay-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def _fill_template(text: str, inputs: dict[str, str]) -> str:
    import re

    return re.sub(
        r"\{\{\s*([^}]+?)\s*\}\}",
        lambda m: str(inputs.get(m.group(1).strip(), m.group(0))),
        text or "",
    )


def redact(value: str) -> str:
    """A masked, length-bearing form of an extracted value.

    The real value still goes back to the caller through the result
    contract; this is the only form allowed into the JSONL log, which is
    for debugging structure and timing, not for holding the data.
    """
    s = str(value)
    if not s:
        return ""
    if len(s) <= 2:
        return "*" * len(s)
    return f"{s[0]}…{s[-1]} (len {len(s)})"


# ---------------------------------------------------------------------------
# evidence log
# ---------------------------------------------------------------------------

class _Evidence:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("w")
        self.path = path

    def write(self, kind: str, **fields) -> None:
        rec = {"ts": _now_iso(), "kind": kind, **fields}
        self._fh.write(json.dumps(rec, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


# ---------------------------------------------------------------------------
# locator resolution (shared with discovery via Perception.resolve)
# ---------------------------------------------------------------------------

class _Resolver:
    """Resolves a :class:`LocatorStrategy` against the live page.

    ``aria_role`` locators go straight through
    :meth:`agent.perception.Perception.resolve` -- the *same* exact
    accessible-name matcher discovery uses, with 0 / >1 matches raising
    :class:`ResolutionError` unless the locator pins an ``nth``. Other
    kinds (``text_label`` fallback, reserved ``css`` / ``xpath``) are
    resolved here but normalised to the same one-visible-element / raise
    contract.
    """

    def __init__(self, page: Page, perception: Perception, inputs: dict[str, str]):
        self.page = page
        self.perception = perception
        self.inputs = inputs

    def _filled_name(self, loc: LocatorStrategy) -> str:
        return _fill_template(loc.name or loc.text or loc.label or "", self.inputs)

    def resolve(self, loc: LocatorStrategy) -> Locator:
        if loc.kind == "aria_role":
            role = loc.role or ""
            name = _fill_template(loc.name or "", self.inputs)
            resolved = self.perception.resolve(
                role, name, exact=loc.exact, nth=loc.nth
            )
            return resolved.locator
        if loc.kind == "text_label":
            name = self._filled_name(loc)
            cand = self.page.get_by_label(name, exact=loc.exact)
            if cand.count() == 0:
                cand = self.page.get_by_text(name, exact=loc.exact)
            n = cand.count()
            if n == 0:
                raise ResolutionError("text_label", name, 0)
            if n > 1 and loc.nth is None:
                raise ResolutionError("text_label", name, n)
            return cand.nth(loc.nth or 0)
        if loc.kind in ("css", "xpath"):
            sel = _fill_template(getattr(loc, "selector", "") or loc.note or "", self.inputs)
            cand = self.page.locator(sel)
            if cand.count() == 0:
                raise ResolutionError(loc.kind, sel, 0)
            return cand.nth(loc.nth or 0)
        raise ResolutionError(loc.kind, self._filled_name(loc), 0)

    def visible(self, loc: LocatorStrategy) -> bool:
        try:
            return bool(self.resolve(loc).is_visible())
        except Exception:
            return False


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------

class Replayer:
    def __init__(
        self,
        capability: Capability,
        *,
        base_url: Optional[str] = None,
        headed: bool = False,
        evidence_root: str = "evidence/replays",
        handoff_enabled: bool = True,
        session_db_path: str = DEFAULT_DB_PATH,
        handoff_timeout_override: Optional[float] = None,
        poll_interval_s: float = 2.0,
        cdp_port: Optional[int] = None,
    ):
        self.cap = capability
        self.base_url = (base_url or capability.target.base_url).rstrip("/")
        self.headed = headed
        self.run_id = _run_id()
        self.run_dir = Path(evidence_root) / self.run_id
        self.ev = _Evidence(self.run_dir / "replay.jsonl")

        self.handoff_enabled = handoff_enabled
        self.session_db_path = session_db_path
        self.handoff_timeout_override = handoff_timeout_override
        self.poll_interval_s = poll_interval_s
        self.cdp_port = cdp_port
        self._store: Optional[SessionStore] = None

        self._last_doc_status: Optional[int] = None
        self._steps_executed = 0
        self._t0 = time.time()
        self.extracted: dict[str, str] = {}

    def _session_store(self) -> SessionStore:
        if self._store is None:
            self._store = SessionStore(self.session_db_path)
        return self._store

    def _handoff_timeout(self) -> float:
        if self.handoff_timeout_override is not None:
            return self.handoff_timeout_override
        return self.cap.escalation_policy.human_handoff_timeout_seconds

    # -- public -------------------------------------------------------

    def run(self, inputs: dict[str, str]) -> ReplayResult:
        pre = self._preflight(inputs)
        if pre is not None:
            return self._finish(pre)

        self.ev.write(
            "run_start",
            run_id=self.run_id,
            capability_id=self.cap.capability_id,
            capability_version=self.cap.version,
            base_url=self.base_url,
            inputs={k: redact(v) for k, v in inputs.items()},
        )

        with sync_playwright() as pw:
            launch_kwargs: dict[str, Any] = {"headless": not self.headed}
            if self.cdp_port:
                # expose CDP so an operator can attach to and drive this exact
                # browser during a handoff (the same live session, not a new one)
                launch_kwargs["args"] = [f"--remote-debugging-port={self.cdp_port}"]
            browser = pw.chromium.launch(**launch_kwargs)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("response", self._note_response)
            perception = Perception(page)
            resolver = _Resolver(page, perception, inputs)
            try:
                result = self._drive(page, perception, resolver, inputs)
            except Exception as e:  # noqa: BLE001 - never crash the caller
                result = HardFailure(
                    **self._base_fields(),
                    at_step_id="?",
                    phase="engine",
                    trigger="engine_error",
                    expected="replay loop to run to completion",
                    observed=f"{type(e).__name__}: {e}",
                )
            finally:
                browser.close()

        return self._finish(result)

    # -- preflight --------------------------------------------------

    def _preflight(self, inputs: dict[str, str]) -> Optional[ReplayResult]:
        if not self.cap.policy_authored_by:
            return HardFailure(
                **self._base_fields(),
                phase="preflight",
                trigger="policy_unauthored",
                expected="capability.policy_authored_by to be set",
                observed="policy_authored_by is empty; replay refuses to run "
                "an artifact whose policy layer was never authored",
            )
        missing = [
            p.name for p in self.cap.inputs
            if p.required and not (inputs.get(p.name) or "").strip()
        ]
        if missing:
            return HardFailure(
                **self._base_fields(),
                phase="preflight",
                trigger="missing_input",
                expected=f"required inputs {[p.name for p in self.cap.inputs if p.required]}",
                observed=f"missing / empty: {missing}",
            )
        used = {s.action for s in self.cap.steps}
        not_allowed = used - set(self.cap.guardrails.allowlist_action_types)
        if not_allowed:
            return HardFailure(
                **self._base_fields(),
                phase="preflight",
                trigger="guardrail_action_type",
                expected=f"every step action within allowlist "
                f"{self.cap.guardrails.allowlist_action_types}",
                observed=f"artifact uses non-allowlisted action(s): {sorted(not_allowed)}",
            )
        return None

    # -- main loop -------------------------------------------------

    def _drive(self, page, perception, resolver, inputs) -> ReplayResult:
        gr = self.cap.guardrails

        for step in sorted(self.cap.steps, key=lambda s: s.ordinal):
            if self._steps_executed >= gr.max_steps:
                return HardFailure(
                    **self._base_fields(),
                    at_step_id=step.id,
                    at_step_ordinal=step.ordinal,
                    phase="guardrail",
                    trigger="max_steps",
                    expected=f"<= {gr.max_steps} steps",
                    observed=f"step budget exhausted at step {step.ordinal}",
                )

            # --- guardrail: action type + (for navigate) target route ---
            guard = self._guard_step(step, inputs)
            if guard is not None:
                return guard

            # --- ACT (exactly once) ---
            t_step = time.time()
            try:
                act_detail = self._act(step, page, perception, resolver, inputs)
            except _StepFailure as f:
                self.ev.write(
                    "step", ordinal=step.ordinal, id=step.id, action=step.action,
                    phase="act", ok=False, expected=f.expected, observed=f.observed,
                )
                disp, res = self._dispatch_trigger(
                    step, page, perception, resolver, inputs,
                    "act", "on_hard_failure", f.expected, f.observed,
                )
                if disp == "result":
                    return res
                # resume: the operator completed the action on the live
                # session. Replay does NOT re-run ACT -- it re-enters
                # SETTLE/CHECK for this step and carries on.
                self._steps_executed += 1
                self.ev.write(
                    "step", ordinal=step.ordinal, id=step.id, action=step.action,
                    phase="act", ok=True,
                    detail="ACT not re-invoked by replay; completed by the human "
                           "operator during handoff",
                )
                disp, res = self._verify_step(
                    step, page, perception, resolver, inputs, t_step
                )
                if disp == "result":
                    return res
                continue

            self._steps_executed += 1
            self.ev.write(
                "step", ordinal=step.ordinal, id=step.id, action=step.action,
                phase="act", ok=True, detail=act_detail,
            )

            # --- guardrail: where did that action land us? ---
            if step.action in ("navigate", "click"):
                landed = self._post_nav_guard(page, step)
                if landed is not None:
                    self.ev.write("guardrail", check="landed_route", ok=False,
                                  observed=landed.observed)
                    return landed

            # --- denylist tripwire (post-ACT page) ---
            deny = self._denylist_hit(page)
            if deny is not None:
                observed = f"denylisted text present after step {step.ordinal}: {deny!r}"
                self.ev.write("guardrail", check="denylist", ok=False, observed=observed)
                return HardFailure(
                    **self._base_fields(), at_step_id=step.id,
                    at_step_ordinal=step.ordinal, phase="check", trigger="denylist",
                    expected=f"no denylisted text ({gr.denylist_text_patterns})",
                    observed=observed,
                )

            # --- SETTLE -> CHECK -> (retry / escalate / resume) ---
            disp, res = self._verify_step(
                step, page, perception, resolver, inputs, t_step
            )
            if disp == "result":
                return res
            # disp == "proceed" -> next step

        # --- all steps passed ---
        required = [o.name for o in self.cap.outputs if o.required]
        missing = [n for n in required if not (self.extracted.get(n) or "").strip()]
        if missing:
            return HardFailure(
                **self._base_fields(), at_step_id=self.cap.steps[-1].id,
                at_step_ordinal=self.cap.steps[-1].ordinal, phase="finalize",
                trigger="missing_output",
                expected=f"all required outputs {required}",
                observed=f"never extracted: {missing}",
            )
        return Success(
            **self._base_fields(),
            outputs={**{n: self.extracted[n] for n in required}, "outcome_code": "SUCCESS"},
        )

    # -- ACT -------------------------------------------------------

    def _act(self, step: Step, page, perception: Perception, resolver: _Resolver,
             inputs) -> str:
        if step.action == "navigate":
            route = _fill_template(step.target_route or "/", inputs)
            self._last_doc_status = None
            resp = page.goto(self._url_for(route), wait_until="domcontentloaded")
            if resp is not None:
                self._last_doc_status = resp.status
            return f"navigated to {route} (HTTP {self._last_doc_status})"

        # denylist must be checked before a mutating action fires
        if step.action in _MUTATING_ACTIONS:
            deny = self._denylist_hit(page)
            if deny is not None:
                raise _StepFailure(
                    f"no denylisted text before a {step.action}",
                    f"denylisted text {deny!r} on page before mutating action",
                )

        target, used = self._resolve_ranked(step, resolver)

        if step.action == "fill":
            value = _fill_template(step.value_template or "", inputs)
            target.fill(value, timeout=4000)
            return f"filled via {used}"
        if step.action == "select_option":
            value = _fill_template(step.value_template or "", inputs)
            target.select_option(value, timeout=4000)
            return f"selected {value!r} via {used}"
        if step.action == "press_key":
            target.press(step.key or "", timeout=4000)
            return f"pressed {step.key!r} via {used}"
        if step.action == "click":
            try:
                target.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass
            self._last_doc_status = None
            target.click(timeout=4000)
            return f"clicked via {used}"
        if step.action == "extract":
            value = perception.read_paired_value(target)
            if not value:
                raise _StepFailure(
                    f"a readable value at {used}",
                    "element resolved but held no readable text",
                )
            self.extracted[step.output_name] = value
            self.ev.write("extract", ordinal=step.ordinal,
                          output_name=step.output_name, value=redact(value))
            return f"extracted {step.output_name} via {used}"
        raise _StepFailure("a known action", f"unsupported action {step.action!r}")

    def _resolve_ranked(self, step: Step, resolver: _Resolver) -> tuple[Locator, str]:
        errors: list[str] = []
        for loc in sorted(step.locators, key=lambda l: l.rank):
            try:
                target = resolver.resolve(loc)
                target.wait_for(state="attached", timeout=4000)
                label = f"{loc.kind} rank{loc.rank} ({loc.role or ''} " \
                        f"{loc.name or loc.text or loc.label or ''})".strip()
                return target, label
            except (ResolutionError, PWTimeout) as e:
                errors.append(f"rank{loc.rank} {loc.kind}: {e}")
        raise _StepFailure(
            f"one of {len(step.locators)} ranked locator(s) to resolve",
            "; ".join(errors) or "no locators on step",
        )

    # -- SETTLE + CHECK ------------------------------------------

    def _settle_and_check(self, step: Step, page: Page, resolver: _Resolver) -> _CheckReport:
        timed_out = self._settle(step, page)

        det_ctx = DetectionContext(page=page, http_status=self._last_doc_status)
        matched = detect_outcome(self.cap.expected_outcomes, det_ctx)

        unrecognized_dialog = False
        if matched is None:
            try:
                dlg = page.get_by_role("alertdialog")
                if dlg.count() >= 1 and dlg.first.is_visible():
                    unrecognized_dialog = True
            except Exception:
                pass

        env = CheckEnv(
            visible=resolver.visible,
            page_text=det_ctx.page_text(),
            extracted=self.extracted,
            required_outputs=[o.name for o in self.cap.outputs if o.required],
            matched_outcome=matched,
        )
        ok = evaluate_checkpoint(step.checkpoint, env)

        detail_bits = list(env.trace)
        if timed_out:
            detail_bits.append(f"SETTLE timed out (>{step.settle.max_wait_seconds}s)")
        if unrecognized_dialog:
            detail_bits.append("unrecognized alertdialog on page")
        return _CheckReport(
            ok=ok,
            matched_outcome=matched,
            unrecognized_dialog=unrecognized_dialog,
            settle_timed_out=timed_out,
            detail="; ".join(detail_bits) or "checkpoint false",
        )

    def _settle(self, step: Step, page: Page) -> bool:
        """Returns True iff SETTLE hit its time bound."""
        spec = step.settle
        ms = int(spec.max_wait_seconds * 1000)
        state = "networkidle" if spec.wait_for == "network_idle" else "load"
        try:
            page.wait_for_load_state(state, timeout=ms)
        except PWTimeout:
            # networkidle can legitimately never arrive; fall back to DOM
            try:
                page.wait_for_load_state("domcontentloaded", timeout=2000)
                return False
            except PWTimeout:
                return True
        if spec.wait_for == "checkpoint":
            # poll the checkpoint element cheaply within the same bound
            deadline = time.time() + spec.max_wait_seconds
            while time.time() < deadline:
                if step.checkpoint.kind == "element_visible" and step.checkpoint.locator:
                    try:
                        loc = page.get_by_role(
                            step.checkpoint.locator.role or "",
                            name=step.checkpoint.locator.name or None,
                            exact=step.checkpoint.locator.exact,
                        )
                        if loc.count() and loc.first.is_visible():
                            break
                    except Exception:
                        pass
                time.sleep(0.25)
        return False

    def _trigger_for(self, report: _CheckReport) -> str:
        if report.settle_timed_out:
            return "on_step_timeout"
        if report.unrecognized_dialog:
            return "on_unrecognized_dialog"
        return "on_checkpoint_failure"

    # -- guardrails --------------------------------------------

    def _guard_step(self, step: Step, inputs) -> Optional[ReplayResult]:
        gr = self.cap.guardrails
        if step.action not in gr.allowlist_action_types:
            return HardFailure(
                **self._base_fields(), at_step_id=step.id, at_step_ordinal=step.ordinal,
                phase="guardrail", trigger="guardrail_action_type",
                expected=f"action in {gr.allowlist_action_types}",
                observed=f"step {step.ordinal} uses '{step.action}'",
            )
        if step.action == "navigate":
            route = _fill_template(step.target_route or "/", inputs)
            path = urlsplit(route).path or "/"
            if not self._route_allowed(path):
                return HardFailure(
                    **self._base_fields(), at_step_id=step.id,
                    at_step_ordinal=step.ordinal, phase="guardrail",
                    trigger="guardrail_route",
                    expected=f"target route within {gr.allowlist_routes}",
                    observed=f"step {step.ordinal} navigates to {path!r}",
                )
            self.ev.write("guardrail", check="route", ok=True, target=path)
        return None

    def _route_allowed(self, path: str) -> bool:
        return any(
            fnmatch.fnmatch(path, pat) or path == pat
            for pat in self.cap.guardrails.allowlist_routes
        )

    def _post_nav_guard(self, page: Page, step: Step) -> Optional[ReplayResult]:
        parts = urlsplit(page.url)
        base = urlsplit(self.base_url)
        gr = self.cap.guardrails
        if gr.forbid_offdomain_navigation and parts.netloc and parts.netloc != base.netloc:
            return HardFailure(
                **self._base_fields(), at_step_id=step.id, at_step_ordinal=step.ordinal,
                phase="check", trigger="offdomain_navigation",
                expected=f"stay on {base.netloc}",
                observed=f"landed on {parts.netloc}",
            )
        if not self._route_allowed(parts.path or "/"):
            return HardFailure(
                **self._base_fields(), at_step_id=step.id, at_step_ordinal=step.ordinal,
                phase="check", trigger="guardrail_route",
                expected=f"landed route within {gr.allowlist_routes}",
                observed=f"landed on {parts.path!r}",
            )
        return None

    def _denylist_hit(self, page: Page) -> Optional[str]:
        pats = self.cap.guardrails.denylist_text_patterns
        if not pats:
            return None
        try:
            text = page.locator("body").inner_text(timeout=2000).lower()
        except Exception:
            return None
        for pat in pats:
            if pat.lower() in text:
                return pat
        return None

    # -- misc -------------------------------------------------

    def _note_response(self, resp) -> None:
        try:
            req = resp.request
            if req.is_navigation_request() and req.frame.parent_frame is None:
                self._last_doc_status = resp.status
        except Exception:
            pass

    def _url_for(self, route: str) -> str:
        if route.startswith(("http://", "https://")):
            return route
        return self.base_url + "/" + route.lstrip("/")

    def _base_fields(self) -> dict[str, Any]:
        return dict(
            capability_id=self.cap.capability_id,
            run_id=self.run_id,
            steps_executed=self._steps_executed,
            duration_s=round(time.time() - self._t0, 3),
            evidence_path=str(self.ev.path),
        )

    # -- SETTLE/CHECK + escalation for one step -----------------

    def _verify_step(
        self, step: Step, page: Page, perception: Perception,
        resolver: _Resolver, inputs, t_step: float,
    ) -> tuple[str, Optional[ReplayResult]]:
        """SETTLE -> CHECK (with timeout retries) -> interpret, escalating
        and resuming as the policy directs.

        Returns ``("proceed", None)`` (go to the next step) or
        ``("result", <ReplayResult>)`` (return it). Every retry and every
        post-handoff resume re-runs SETTLE/CHECK only -- ACT is never
        re-invoked from here.
        """
        pol = self.cap.escalation_policy

        report = self._settle_and_check(step, page, resolver)
        attempts = 1
        while (
            not report.ok
            and report.matched_outcome is None
            and self._trigger_for(report) == "on_step_timeout"
            and pol.on_step_timeout == "retry"
            and attempts <= pol.max_retries_per_step
        ):
            time.sleep(pol.retry_backoff_seconds)
            report = self._settle_and_check(step, page, resolver)
            attempts += 1

        self.ev.write(
            "step", ordinal=step.ordinal, id=step.id, action=step.action,
            phase="check", ok=report.ok, attempts=attempts,
            settle_timed_out=report.settle_timed_out,
            matched_outcome=(report.matched_outcome.code
                             if report.matched_outcome else None),
            detail=report.detail,
            duration_ms=int((time.time() - t_step) * 1000),
        )

        resumes = 0
        while True:
            outcome = report.matched_outcome
            if outcome is not None:
                self.ev.write("outcome_detected", code=outcome.code,
                              classification=outcome.classification, at_step=step.id)
                if outcome.classification == "business_outcome":
                    if outcome.terminal:
                        return "result", BusinessOutcome(
                            **self._base_fields(), outcome_code=outcome.code,
                            description=outcome.description,
                            classification=outcome.classification,
                            detected_at_step=step.id,
                            outputs={**self.extracted, "outcome_code": outcome.code},
                        )
                    return "proceed", None  # non-terminal: note and continue
                return "result", HardFailure(
                    **self._base_fields(), at_step_id=step.id,
                    at_step_ordinal=step.ordinal, phase="check",
                    trigger="known_hard_failure_outcome",
                    expected="happy-path checkpoint",
                    observed=f"declared outcome {outcome.code}: {outcome.description}",
                )

            if report.ok:
                return "proceed", None

            trig_name = self._trigger_for(report)
            disp, res = self._dispatch_trigger(
                step, page, perception, resolver, inputs, "check", trig_name,
                f"checkpoint {step.checkpoint.kind} on step {step.ordinal}",
                report.detail,
            )
            if disp == "result":
                return "result", res

            # disp == "resume": operator acted on the live session
            resumes += 1
            self.ev.write("resume", step_id=step.id, resume_count=resumes,
                          note="re-running SETTLE/CHECK after human handoff; "
                               "ACT is not re-invoked")
            report = self._settle_and_check(step, page, resolver)
            if resumes >= 5 and not report.ok and report.matched_outcome is None:
                return "result", HardFailure(
                    **self._base_fields(), at_step_id=step.id,
                    at_step_ordinal=step.ordinal, phase="check",
                    trigger="checkpoint_failure_after_resume",
                    expected=f"checkpoint {step.checkpoint.kind} to pass after "
                             "operator handoff",
                    observed=f"operator resumed {resumes}x, checkpoint still "
                             f"failing: {report.detail}",
                )

    def _dispatch_trigger(
        self, step: Step, page: Page, perception: Perception, resolver: _Resolver,
        inputs, phase: str, trigger_name: str, expected: str, observed: str,
    ) -> tuple[str, Optional[ReplayResult]]:
        """Apply the ``EscalationAction`` the policy maps *trigger_name* to.

        ``retry`` -> SETTLE/CHECK retries are the caller's job and are
        already exhausted by here, so it escalates. ``escalate`` -> open a
        SessionStore entry and block for a human (or, with handoff
        disabled, return :class:`PendingEscalation`). ``fail`` / ``abort``
        -> stop now with a :class:`HardFailure`, no handoff.
        """
        action = getattr(self.cap.escalation_policy, trigger_name)
        if action == "retry":
            action = "escalate"

        if action in ("fail", "abort"):
            return "result", HardFailure(
                **self._base_fields(), at_step_id=step.id,
                at_step_ordinal=step.ordinal, phase=phase,
                trigger=f"{trigger_name}:{action}",
                expected=expected, observed=observed,
            )

        if not self.handoff_enabled:
            return "result", PendingEscalation(
                **self._base_fields(), at_step_id=step.id,
                at_step_ordinal=step.ordinal, phase=phase, trigger=trigger_name,
                configured_action=action, expected=expected, observed=observed,
            )

        return self._escalate(
            step, page, perception, phase, trigger_name, expected, observed
        )

    def _escalate(
        self, step: Step, page: Page, perception: Perception, phase: str,
        trigger_name: str, expected: str, observed: str,
    ) -> tuple[str, Optional[ReplayResult]]:
        """Record the blocked step, keep the browser open, and poll the
        SessionStore for an operator's resume up to the handoff timeout."""
        store = self._session_store()
        timeout = self._handoff_timeout()

        shot = self.run_dir / "screenshots" / f"escalation_step{step.ordinal:02d}_{step.id}.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        perception.screenshot(str(shot))

        started = time.time()
        deadline = started + timeout
        deadline_iso = datetime.fromtimestamp(deadline, timezone.utc).isoformat()
        esc_id = store.open_escalation(
            run_id=self.run_id, capability_id=self.cap.capability_id,
            goal=(self.cap.discovery.goal if self.cap.discovery else None),
            step_id=step.id, step_ordinal=step.ordinal, trigger=trigger_name,
            phase=phase, expected=expected, observed=observed,
            screenshot_path=str(shot), handoff_deadline_at=deadline_iso,
        )
        self.ev.write(
            "escalation_opened", escalation_id=esc_id, step_id=step.id,
            step_ordinal=step.ordinal, phase=phase, trigger=trigger_name,
            expected=expected, observed=observed, screenshot=str(shot),
            handoff_timeout_s=timeout, current_url=page.url,
            note="replay is blocked and polling; the live browser stays open "
                 "for an operator to drive directly",
        )

        while time.time() < deadline:
            rec = store.get(esc_id)
            if rec and rec["status"] == "resumed":
                waited = round(time.time() - started, 1)
                self.ev.write(
                    "human_intervention", escalation_id=esc_id, step_id=step.id,
                    human_intervened=True, resumed_by=rec.get("resumed_by"),
                    operator_note=rec.get("operator_note"),
                    opened_at=rec.get("created_at"), resumed_at=rec.get("resumed_at"),
                    paused_seconds=waited,
                    note="operator took control of the live session and resumed; "
                         "replay re-enters SETTLE/CHECK for this step only",
                )
                return "resume", None
            time.sleep(self.poll_interval_s)

        store.mark_timed_out(esc_id)
        waited = round(time.time() - started, 1)
        self.ev.write(
            "escalation_timed_out", escalation_id=esc_id, step_id=step.id,
            paused_seconds=waited, handoff_timeout_s=timeout,
        )
        return "result", HardFailure(
            **self._base_fields(), at_step_id=step.id, at_step_ordinal=step.ordinal,
            phase=phase, trigger=f"{trigger_name}:handoff_timeout",
            expected=expected,
            observed=(f"escalated to a human operator and waited {waited}s "
                      f"(limit {timeout}s) with no resume; run hard-failed "
                      f"cleanly. Last observed: {observed}"),
        )

    def _finish(self, result: ReplayResult) -> ReplayResult:
        # refresh the volatile fields on whatever result we return
        for k, v in self._base_fields().items():
            setattr(result, k, v)
        self.ev.write(
            "run_end", status=result.status, steps_executed=result.steps_executed,
            duration_s=result.duration_s,
            summary=_summarize(result),
        )
        self.ev.close()
        # The result object (with real, unredacted outputs) is returned to
        # the caller in-process and printed to stdout by the CLI. It is
        # deliberately NOT written to evidence/: that directory is the
        # debugging trail, and the redacted JSONL is all it should hold.
        return result


def _summarize(result: ReplayResult) -> str:
    if isinstance(result, Success):
        return "completed; outputs: " + ", ".join(sorted(result.outputs))
    if isinstance(result, BusinessOutcome):
        return f"{result.outcome_code} at {result.detected_at_step}: {result.description}"
    if isinstance(result, HardFailure):
        return f"hard failure at {result.at_step_id}/{result.phase}: {result.observed}"
    if isinstance(result, PendingEscalation):
        return f"pending escalation at {result.at_step_id}: {result.trigger}"
    return result.status


def replay(
    capability: Capability,
    inputs: dict[str, str],
    *,
    base_url: Optional[str] = None,
    headed: bool = False,
    evidence_root: str = "evidence/replays",
    handoff_enabled: bool = True,
    **kwargs: Any,
) -> ReplayResult:
    """Convenience wrapper: one capability + inputs -> one result."""
    return Replayer(
        capability, base_url=base_url, headed=headed, evidence_root=evidence_root,
        handoff_enabled=handoff_enabled, **kwargs,
    ).run(inputs)
