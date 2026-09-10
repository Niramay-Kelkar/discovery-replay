"""CLI for the compiler: Trajectory + PolicySpec -> compiled Capability.

    python -m agent.compile_cli \\
        --trajectory evidence/runs/disc-20260909-182538/trajectory.json \\
        --policy member_lookup \\
        --out evidence/compiled/member_lookup.capability.json

``--policy NAME`` imports ``agent.policies.NAME`` and expects it to
expose ``POLICY_SPEC`` (a :class:`agent.compile.PolicySpec`) and
``INPUT_BINDINGS`` (a list of :class:`agent.compile.InputBinding`).

Writes two files: the artifact, and a ``*.notes.md`` sidecar recording
every compile-time decision that is not a 1:1 copy of the trajectory.
Exits non-zero with a clear message on any compilation failure -- it
never writes a partial or permissive artifact.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from pydantic import ValidationError

from agent.compile import CompileError, compile_capability
from agent.trajectory import Trajectory

REPO_ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--trajectory", required=True, type=Path,
                   help="path to a discovery trajectory.json")
    p.add_argument("--policy", required=True,
                   help="name of a module in agent.policies (e.g. 'member_lookup')")
    p.add_argument("--out", required=True, type=Path,
                   help="where to write the compiled capability JSON")
    p.add_argument("--notes-out", type=Path, default=None,
                   help="where to write the compile-notes markdown "
                        "(default: <out>.notes.md)")
    args = p.parse_args(argv)

    if not args.trajectory.exists():
        print(f"trajectory not found: {args.trajectory}", file=sys.stderr)
        return 2
    try:
        trajectory = Trajectory.model_validate_json(args.trajectory.read_text())
    except ValidationError as e:
        print(f"trajectory failed to parse: {e}", file=sys.stderr)
        return 2

    try:
        mod = importlib.import_module(f"agent.policies.{args.policy}")
    except ModuleNotFoundError as e:
        print(f"no policy module 'agent.policies.{args.policy}': {e}", file=sys.stderr)
        return 2
    except ValidationError as e:
        # a required PolicySpec field is missing / malformed at construction
        print(f"policy 'agent.policies.{args.policy}' is invalid:\n{e}",
              file=sys.stderr)
        return 2

    try:
        policy = mod.POLICY_SPEC
        bindings = mod.INPUT_BINDINGS
    except AttributeError as e:
        print(f"policy module must expose POLICY_SPEC and INPUT_BINDINGS: {e}",
              file=sys.stderr)
        return 2

    try:
        capability, notes = compile_capability(trajectory, policy, bindings)
    except CompileError as e:
        print(f"compile failed: {e}", file=sys.stderr)
        return 1

    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(capability.to_json() + "\n")

    notes_out: Path = args.notes_out or out.with_suffix(".notes.md")
    notes_out.parent.mkdir(parents=True, exist_ok=True)
    notes_out.write_text(notes.to_markdown())

    print(f"compiled {capability.capability_id} v{capability.version}")
    print(f"  {len(capability.steps)} steps, {len(capability.inputs)} inputs, "
          f"{len(capability.outputs)} outputs, "
          f"{len(capability.expected_outcomes)} expected outcomes")
    print(f"  artifact -> {out}")
    print(f"  notes    -> {notes_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
