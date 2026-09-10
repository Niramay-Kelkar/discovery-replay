"""CLI for the replay engine: compiled Capability + inputs -> deterministic run.

    python -m agent.replay_cli \\
        --capability evidence/compiled/member_lookup.capability.json \\
        --input search_term=M1001

No LLM is involved. Exit code: 0 for a Success or a BusinessOutcome
(both are legitimate answers the caller asked for), 1 for a HardFailure,
3 for a PendingEscalation.

Extracted values are printed to stdout (the caller asked for them) but
never written to the JSONL evidence log -- see ``evidence/replays/``.
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
    args = p.parse_args(argv)

    if not args.capability.exists():
        print(f"capability not found: {args.capability}", file=sys.stderr)
        return 2
    capability = Capability.model_validate_json(args.capability.read_text())
    inputs = _parse_inputs(args.input)

    replayer = Replayer(
        capability, base_url=args.base_url, headed=args.headed,
        evidence_root=args.evidence_root,
        handoff_enabled=not args.no_handoff,
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
