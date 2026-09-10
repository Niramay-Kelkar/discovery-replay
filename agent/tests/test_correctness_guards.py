"""The two correctness rules the brief calls out as easy to get wrong.

Offline: no browser, no API.
"""
from agent.discovery_tools import _ToolError, ToolExecutor
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
    ex, traj = _executor()
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


def test_resolve_uses_exact_matching():
    import inspect
    from agent.perception import Perception
    src = inspect.getsource(Perception.resolve)
    assert "exact=True" in src, "resolve must pin exact=True"
    assert "count != 1" in src, "ambiguous (>1) matches must fail"
