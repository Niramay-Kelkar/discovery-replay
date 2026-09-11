"""CLI for the replay engine: compiled Capability + inputs -> deterministic run.

    python -m agent.replay_cli \\
        --capability evidence/compiled/member_lookup.capability.json \\
        --input search_term=M1001

No LLM is involved. Exit code: 0 for a Success or a BusinessOutcome
(both are legitimate answers the caller asked for), 1 for a HardFailure,
3 for a PendingEscalation.

Extracted values are printed to stdout (the caller asked for them) but
never written to the JSONL evidence log -- see ``evidence/replays/``.

With ``--repeat N`` (N > 1), the same capability + inputs are replayed N
times sequentially -- a fresh ``Replayer`` per run, no retry-on-failure,
no parallelism -- to measure flakiness rather than hide it. Each run's
``run_id`` gets a ``-r{i}`` suffix so two fast (e.g. preflight-failure)
runs landing in the same wall-clock second can't overwrite each other's
evidence directory. Repeat runs default to no human handoff (an
automated stability check blocking N times for an operator defeats its
own purpose); pass ``--repeat-allow-handoff`` to opt back in. Exit code
for ``--repeat`` mode: 0 if every run succeeded (success rate 1.0),
1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent.models import Capability
from agent.replay import (
    BusinessOutcome,
    HardFailure,
    PendingEscalation,
    Replayer,
    Success,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _parse_inputs(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"--input must be name=value, got {p!r}")
        k, v = p.split("=", 1)
        out[k.strip()] = v
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--capability", required=True, type=Path)
    p.add_argument("--input", action="append", default=[], metavar="NAME=VALUE",
                   help="a capability input; repeatable")
    p.add_argument("--base-url", default=None,
                   help="override the artifact's target.base_url")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--confirmed", action="store_true", default=False,
                   help="explicit operator confirmation; required before a "
                        "capability whose artifact declares requires_confirmation "
                        "will run")
    p.add_argument("--evidence-root", default=str(REPO_ROOT / "evidence" / "replays"))
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    p.add_argument("--no-handoff", action="store_true",
                   help="on an 'escalate' trigger, return PendingEscalation "
                        "immediately instead of blocking for an operator")
    p.add_argument("--session-db", default=str(REPO_ROOT / "evidence" / "sessions"
                                               / "escalations.db"),
                   help="SQLite escalation store shared with the operator console")
    p.add_argument("--handoff-timeout", type=float, default=None,
                   help="override escalation_policy.human_handoff_timeout_seconds")
    p.add_argument("--poll-interval", type=float, default=2.0,
                   help="how often to check the store for a resume, seconds")
    p.add_argument("--cdp-port", type=int, default=None,
                   help="expose Chrome DevTools on this port so an operator can "
                        "attach to and drive this exact browser during a handoff")
    p.add_argument("--repeat", type=int, default=1,
                   help="replay the same capability + inputs this many times "
                        "sequentially and report a success-rate / determinism "
                        "signal instead of a single result (default 1: normal "
                        "single-run behavior)")
    p.add_argument("--repeat-allow-handoff", action="store_true",
                   help="with --repeat, allow individual runs to block for a "
                        "human handoff on an 'escalate' trigger, instead of the "
                        "default (return PendingEscalation immediately, same as "
                        "--no-handoff)")
    args = p.parse_args(argv)

    if not args.capability.exists():
        print(f"capability not found: {args.capability}", file=sys.stderr)
        return 2
    capability = Capability.model_validate_json(args.capability.read_text())
    inputs = _parse_inputs(args.input)

    if args.repeat > 1:
        return _run_repeat(args, capability, inputs)

    replayer = Replayer(
        capability, base_url=args.base_url, headed=args.headed,
        evidence_root=args.evidence_root,
        handoff_enabled=not args.no_handoff,
        confirmed=args.confirmed,
        session_db_path=args.session_db,
        handoff_timeout_override=args.handoff_timeout,
        poll_interval_s=args.poll_interval,
        cdp_port=args.cdp_port,
    )
    print(f"run_id: {replayer.run_id}")
    result = replayer.run(inputs)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        _print_human(result)
    print(f"\nevidence: {result.evidence_path}")

    if isinstance(result, (Success, BusinessOutcome)):
        return 0
    if isinstance(result, PendingEscalation):
        return 3
    return 1


def _run_repeat(args, capability: Capability, inputs: dict[str, str]) -> int:
    """Replay the same capability + inputs ``args.repeat`` times sequentially.

    A fresh ``Replayer`` per iteration (same pattern every other caller
    uses), no retry-on-failure -- each run reaches its natural conclusion
    once, so the loop measures flakiness instead of papering over it.
    """
    handoff_enabled = args.repeat_allow_handoff and not args.no_handoff
    results: list[dict] = []
    for i in range(args.repeat):
        replayer = Replayer(
            capability, base_url=args.base_url, headed=args.headed,
            evidence_root=args.evidence_root,
            handoff_enabled=handoff_enabled,
            confirmed=args.confirmed,
            session_db_path=args.session_db,
            handoff_timeout_override=args.handoff_timeout,
            poll_interval_s=args.poll_interval,
            cdp_port=args.cdp_port,
            run_id_suffix=f"-r{i}",
        )
        print(f"[{i + 1}/{args.repeat}] run_id: {replayer.run_id}")
        result = replayer.run(inputs)
        print(f"[{i + 1}/{args.repeat}] -> {result.status}"
              f" ({result.duration_s}s)")
        results.append(result.to_dict())

    agg = _aggregate_stability(results)

    if args.json:
        print(json.dumps(agg, indent=2, default=str))
    else:
        _print_stability_summary(agg)

    return 0 if agg["success_rate"] == 1.0 else 1


def _aggregate_stability(results: list[dict]) -> dict:
    """The multi-run stability signal: success rate, per-status counts,
    failure determinism (grouped by (at_step_id, trigger) over hard
    failures only), and the duration_s spread across all N runs.
    """
    n = len(results)
    status_counts: dict[str, int] = {}
    for r in results:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1

    success_count = status_counts.get("success", 0) + status_counts.get("business_outcome", 0)
    success_rate = success_count / n if n else 0.0

    durations = [r["duration_s"] for r in results]
    duration_stats = {
        "min": min(durations) if durations else 0.0,
        "max": max(durations) if durations else 0.0,
        "mean": round(sum(durations) / len(durations), 3) if durations else 0.0,
    }

    hard_failures = [r for r in results if r["status"] == "hard_failure"]
    if not hard_failures:
        determinism = "n/a"
    else:
        pairs = {(r.get("at_step_id"), r.get("trigger")) for r in hard_failures}
        determinism = "deterministic" if len(pairs) == 1 else "non-deterministic"

    return {
        "n": n,
        "success_rate": success_rate,
        "status_counts": status_counts,
        "failure_determinism": determinism,
        "duration_s": duration_stats,
        "run_ids": [r["run_id"] for r in results],
    }


def _print_stability_summary(agg: dict) -> None:
    print(f"\n{agg['n']} runs, success rate: {agg['success_rate']:.0%}")
    print(f"status counts: {agg['status_counts']}")
    print(f"failure determinism: {agg['failure_determinism']}")
    d = agg["duration_s"]
    print(f"duration_s: min {d['min']}  max {d['max']}  mean {d['mean']}")


def _print_human(result) -> None:
    print(f"\nresult: {result.status.upper()}")
    print(f"steps executed: {result.steps_executed}   "
          f"duration: {result.duration_s}s")
    if isinstance(result, Success):
        print("outputs:")
        for k, v in result.outputs.items():
            print(f"  {k} = {v!r}")
    elif isinstance(result, BusinessOutcome):
        print(f"outcome code: {result.outcome_code}")
        print(f"description:  {result.description}")
        print(f"detected at:  step {result.detected_at_step}")
    elif isinstance(result, HardFailure):
        print(f"failed at:   step {result.at_step_ordinal} ({result.at_step_id}), "
              f"phase {result.phase}")
        print(f"trigger:     {result.trigger}")
        print(f"expected:    {result.expected}")
        print(f"observed:    {result.observed}")
    elif isinstance(result, PendingEscalation):
        print(f"paused at:   step {result.at_step_ordinal} ({result.at_step_id}), "
              f"phase {result.phase}")
        print(f"trigger:     {result.trigger}  ->  configured action: "
              f"{result.configured_action}")
        print(f"expected:    {result.expected}")
        print(f"observed:    {result.observed}")
        print(f"note:        {result.note}")


if __name__ == "__main__":
    raise SystemExit(main())
