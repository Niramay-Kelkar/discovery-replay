"""Tests for the --repeat stability aggregation (agent/replay_cli.py).

These exercise ``_aggregate_stability`` directly against constructed
``ReplayResult`` objects -- no browser, no N real replay runs needed to
test the aggregation math itself.
"""
from agent.replay import BusinessOutcome, HardFailure, PendingEscalation, Success
from agent.replay_cli import _aggregate_stability

_BASE = {"capability_id": "member_lookup", "steps_executed": 7, "evidence_path": "x"}


def _success(run_id: str, duration_s: float) -> dict:
    return Success(
        **_BASE, run_id=run_id, duration_s=duration_s,
        outputs={"outcome_code": "SUCCESS"},
    ).to_dict()


def _business_outcome(run_id: str, duration_s: float) -> dict:
    return BusinessOutcome(
        **_BASE, run_id=run_id, duration_s=duration_s,
        outcome_code="ACCESS_DENIED", description="denied",
        detected_at_step="click_open_detail_for",
    ).to_dict()


def _hard_failure(run_id: str, duration_s: float, at_step_id: str, trigger: str) -> dict:
    return HardFailure(
        **_BASE, run_id=run_id, duration_s=duration_s,
        at_step_id=at_step_id, at_step_ordinal=3, phase="check",
        trigger=trigger, expected="e", observed="o",
    ).to_dict()


def _pending_escalation(run_id: str, duration_s: float) -> dict:
    return PendingEscalation(
        **_BASE, run_id=run_id, duration_s=duration_s,
        at_step_id="s3", at_step_ordinal=3, phase="check",
        trigger="on_checkpoint_failure", configured_action="escalate",
        expected="e", observed="o",
    ).to_dict()


def test_success_rate_counts_success_and_business_outcome_as_success():
    results = [
        _success("r-0", 3.0),
        _business_outcome("r-1", 2.5),
        _hard_failure("r-2", 4.0, "s3", "on_checkpoint_failure"),
        _success("r-3", 3.2),
    ]
    agg = _aggregate_stability(results)
    assert agg["n"] == 4
    assert agg["success_rate"] == 0.75
    assert agg["status_counts"] == {
        "success": 2, "business_outcome": 1, "hard_failure": 1,
    }


def test_success_rate_is_one_when_every_run_succeeds():
    results = [_success(f"r-{i}", 3.0) for i in range(5)]
    agg = _aggregate_stability(results)
    assert agg["success_rate"] == 1.0
    assert agg["failure_determinism"] == "n/a"


def test_determinism_is_na_with_no_hard_failures():
    results = [_success("r-0", 3.0), _pending_escalation("r-1", 2.0)]
    agg = _aggregate_stability(results)
    assert agg["failure_determinism"] == "n/a"


def test_determinism_is_deterministic_when_failures_share_step_and_trigger():
    results = [
        _success("r-0", 3.0),
        _hard_failure("r-1", 4.0, "click_open_detail_for", "on_checkpoint_failure"),
        _hard_failure("r-2", 4.1, "click_open_detail_for", "on_checkpoint_failure"),
    ]
    agg = _aggregate_stability(results)
    assert agg["failure_determinism"] == "deterministic"


def test_determinism_is_non_deterministic_when_failures_differ_by_step():
    results = [
        _hard_failure("r-0", 4.0, "click_open_detail_for", "on_checkpoint_failure"),
        _hard_failure("r-1", 4.1, "type_search_term", "on_step_timeout"),
    ]
    agg = _aggregate_stability(results)
    assert agg["failure_determinism"] == "non-deterministic"


def test_determinism_is_non_deterministic_when_same_step_different_trigger():
    results = [
        _hard_failure("r-0", 4.0, "click_open_detail_for", "on_checkpoint_failure"),
        _hard_failure("r-1", 4.1, "click_open_detail_for", "on_step_timeout"),
    ]
    agg = _aggregate_stability(results)
    assert agg["failure_determinism"] == "non-deterministic"


def test_duration_min_max_mean():
    results = [_success("r-0", 2.0), _success("r-1", 4.0), _success("r-2", 6.0)]
    agg = _aggregate_stability(results)
    assert agg["duration_s"] == {"min": 2.0, "max": 6.0, "mean": 4.0}


def test_run_ids_carried_for_traceability():
    results = [_success("replay-20260911-000000-r0", 3.0),
               _success("replay-20260911-000000-r1", 3.1)]
    agg = _aggregate_stability(results)
    assert agg["run_ids"] == ["replay-20260911-000000-r0", "replay-20260911-000000-r1"]
