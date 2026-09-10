"""Replay engine tests that need no browser and no API.

Covers the parts of Section 3.3 that are logic, not I/O: the checkpoint
evaluator's kind coverage, outcome detection's kind coverage, redaction,
guardrail route matching, and the four-way result contract.
"""
from typing import get_args

import pytest

from agent.checkpoints import CheckEnv, evaluate_checkpoint
from agent.models import (
    Capability,
    Checkpoint,
    CheckpointKind,
    DetectionKind,
    ExpectedOutcome,
    LocatorStrategy,
)
from agent.outcome_detection import _HANDLERS as _DET_HANDLERS
from agent.replay import (
    BusinessOutcome,
    HardFailure,
    PendingEscalation,
    Replayer,
    Success,
    _fill_template,
    redact,
)

CAP_PATH = "evidence/compiled/member_lookup.capability.json"


def _cap() -> Capability:
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    return Capability.model_validate_json((root / CAP_PATH).read_text())


# --- coverage: every schema kind has a handler -----------------------------

def test_checkpoint_evaluator_covers_every_kind():
    from agent.checkpoints import _HANDLERS
    assert set(get_args(CheckpointKind)) <= set(_HANDLERS)


def test_outcome_detection_covers_every_kind():
    assert set(get_args(DetectionKind)) <= set(_DET_HANDLERS)


# --- checkpoint evaluator behaviour ---------------------------------------

def _env(**kw):
    base = dict(
        visible=lambda loc: False,
        page_text="",
        extracted={},
        required_outputs=["full_name", "savings_balance"],
        matched_outcome=None,
    )
    base.update(kw)
    return CheckEnv(**base)


def test_any_of_true_when_outcome_matched_even_if_element_missing():
    oc = _cap().expected_outcomes[0]
    cp = Checkpoint(kind="any_of", checks=[
        Checkpoint(kind="element_visible",
                   locator=LocatorStrategy(kind="aria_role", rank=1,
                                           role="rowheader", name="Full name")),
        Checkpoint(kind="outcome_matched"),
    ])
    assert evaluate_checkpoint(cp, _env(matched_outcome=oc)) is True
    assert evaluate_checkpoint(cp, _env()) is False


def test_outputs_non_empty_uses_required_when_unspecified():
    cp = Checkpoint(kind="outputs_non_empty")
    assert evaluate_checkpoint(cp, _env(extracted={"full_name": "x"})) is False
    assert evaluate_checkpoint(
        cp, _env(extracted={"full_name": "x", "savings_balance": "y"})
    ) is True


def test_outcome_matched_respects_a_specific_code():
    a, b = _cap().expected_outcomes[0], _cap().expected_outcomes[1]
    cp = Checkpoint(kind="outcome_matched", outcome_code=b.code)
    assert evaluate_checkpoint(cp, _env(matched_outcome=b)) is True
    assert evaluate_checkpoint(cp, _env(matched_outcome=a)) is False


# --- redaction -----------------------------------------------------------

def test_redact_hides_the_value_but_keeps_shape():
    r = redact("Alice Nguyen")
    assert "Alice" not in r and "Nguyen" not in r
    assert "len 12" in r
    assert redact("") == ""
    assert redact("$18,750.42").count("…") == 1


def test_fill_template_substitutes_named_inputs():
    assert _fill_template("Open detail for {{search_term}}",
                          {"search_term": "M1001"}) == "Open detail for M1001"
    assert _fill_template("no tokens", {}) == "no tokens"


# --- guardrails --------------------------------------------------------

def test_route_allowlist_matches_globs_not_arbitrary_paths():
    r = Replayer(_cap(), evidence_root="/tmp/replay-test-ev")
    assert r._route_allowed("/")
    assert r._route_allowed("/search")
    assert r._route_allowed("/member/M1001")
    assert not r._route_allowed("/admin")
    assert not r._route_allowed("/transfer")
    r.ev.close()


def test_preflight_refuses_unauthored_policy():
    cap = _cap().model_copy(update={"policy_authored_by": None})
    r = Replayer(cap, evidence_root="/tmp/replay-test-ev")
    res = r.run({"search_term": "M1001"})
    assert isinstance(res, HardFailure)
    assert res.trigger == "policy_unauthored"


def test_preflight_requires_declared_inputs():
    r = Replayer(_cap(), evidence_root="/tmp/replay-test-ev")
    res = r.run({})
    assert isinstance(res, HardFailure)
    assert res.trigger == "missing_input"


def test_preflight_gates_a_capability_that_requires_confirmation():
    cap = _cap().model_copy(update={"requires_confirmation": True})
    inputs = {"search_field": "Member ID", "search_term": "M1001"}

    # without --confirmed: hard-fail in preflight, no browser launched
    res = Replayer(cap, evidence_root="/tmp/replay-test-ev").run(inputs)
    assert isinstance(res, HardFailure)
    assert res.trigger == "confirmation_required"
    assert res.phase == "preflight"
    assert cap.capability_id in res.observed

    # with confirmed=True: preflight passes (returns None, run proceeds past it)
    r = Replayer(cap, confirmed=True, evidence_root="/tmp/replay-test-ev")
    assert r._preflight(inputs) is None


def test_preflight_unaffected_when_confirmation_not_required():
    cap = _cap()  # member_lookup: requires_confirmation is False
    assert cap.requires_confirmation is False
    inputs = {"search_field": "Member ID", "search_term": "M1001"}
    assert Replayer(cap, evidence_root="/tmp/replay-test-ev")._preflight(inputs) is None
    assert Replayer(cap, confirmed=True,
                    evidence_root="/tmp/replay-test-ev")._preflight(inputs) is None


# --- result contract is four distinct structural types -------------------

def test_result_types_are_distinct_and_serialisable():
    common = dict(capability_id="c", run_id="r", steps_executed=1,
                  duration_s=0.1, evidence_path="e")
    assert Success(**common, outputs={}).status == "success"
    assert BusinessOutcome(**common, outcome_code="X").status == "business_outcome"
    assert HardFailure(**common).status == "hard_failure"
    assert PendingEscalation(**common).status == "pending_escalation"
    for cls in (Success, BusinessOutcome, HardFailure, PendingEscalation):
        inst = cls(**common)
        assert inst.to_dict()["status"] == cls.status
