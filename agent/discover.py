"""CLI for the discovery agent.

    python -m agent.discover \\
        --goal "search for member M1001 and read their name and current savings balance" \\
        --base-url http://127.0.0.1:5001

Reads ANTHROPIC_API_KEY from .env (or the environment). Writes the full
trajectory, a JSONL step log, and per-step screenshots to
evidence/runs/<run_id>/.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from agent.discovery import (
    DEFAULT_MAX_STEPS,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_S,
    DiscoveryAgent,
    DiscoveryConfig,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    load_dotenv(REPO_ROOT / ".env")

    p = argparse.ArgumentParser(description="Run one discovery run against a live target app.")
    p.add_argument("--goal", required=True, help="plain-English goal for the agent")
    p.add_argument("--base-url", default=os.environ.get("TARGET_APP_BASE_URL",
                                                        "http://127.0.0.1:5001"))
    p.add_argument("--entry", default="/", help="entry path (default '/')")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    p.add_argument("--headed", action="store_true", help="show the browser")
    p.add_argument("--evidence-root", default=str(REPO_ROOT / "evidence" / "runs"))
    args = p.parse_args(argv)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set (put it in .env).", file=sys.stderr)
        return 2

    cfg = DiscoveryConfig(
        goal=args.goal,
        base_url=args.base_url,
        entry_path=args.entry,
        model=args.model,
        max_steps=args.max_steps,
        timeout_s=args.timeout,
        headed=args.headed,
        evidence_root=args.evidence_root,
    )

    agent = DiscoveryAgent(cfg)
    print(f"run_id: {agent.run_id}")
    traj = agent.run()

    print(f"\noutcome: {traj.outcome} -- {traj.outcome_detail}")
    print(f"steps: {traj.step_count}  retries: {traj.retry_count}  "
          f"resolution_errors: {traj.resolution_error_count}")
    if traj.captured_outputs:
        print("captured:")
        for name, rec in traj.captured_outputs.items():
            lbl = f"  [label: {rec.label!r}]" if rec.label else ""
            print(f"  {name} = {rec.value!r}{lbl}")
    print(f"\nevidence: {agent.run_dir}")
    return 0 if traj.outcome == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
