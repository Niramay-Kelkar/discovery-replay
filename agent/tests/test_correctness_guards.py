"""The two correctness rules the brief calls out as easy to get wrong.

Offline: no browser, no API.
"""
from agent.discovery_tools import ToolExecutor, _ToolError
from agent.trajectory import ExtractionResult, Trajectory


def _executor():
    traj = Trajectory(
        run_id="t", goal="g", target_base_url="http://x", entry_path="/",
        model="m", started_at="now",
    )
    ex = ToolExecutor.__new__(ToolExecutor)
    ex.trajectory = traj
    return ex, traj


def test_done_rejects_uncaptured_output_name():
    ex, _traj = _executor()
    try:
        ex._validate_done(["savings_balance"])
        assert False, "should have raised"
    except _ToolError as e:
        assert "never successfully" in str(e)
        assert "extract" in str(e).lower()


def test_done_accepts_only_captured_names():
    ex, traj = _executor()
    traj.captured_outputs["savings_balance"] = ExtractionResult(
        output_name="savings_balance", value="$1.00", label="Savings balance"
    )
    ex._validate_done(["savings_balance"])  # no raise
    try:
        ex._validate_done(["savings_balance", "member_name"])
        assert False
    except _ToolError as e:
        assert "member_name" in str(e)


def test_resolve_defaults_to_exact_and_fails_on_ambiguity():
    import inspect

    from agent.perception import Perception
    sig = inspect.signature(Perception.resolve)
    # exact matching is the default; replay may opt out per-locator, discovery
    # never does (it passes no `exact`).
    assert sig.parameters["exact"].default is True
    src = inspect.getsource(Perception.resolve)
    assert "exact=exact" in src, "resolve must pass its exact flag through"
    assert "count != 1" in src, "ambiguous (>1) matches must fail without an nth"
