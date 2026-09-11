# evidence/stability/ — multi-run stability signal, live proof

Section 8 stretch goal: `agent/replay_cli.py --repeat N` replays the same
compiled capability + inputs N times sequentially -- a fresh `Replayer`
per run, no retry-on-failure, no parallelism -- and reports whether the
artifact is stable, not just whether one run happened to pass.

`summary.json` is the real aggregate from an actual run of the command
below, not hand-written illustrative numbers:

```
python -m agent.replay_cli \
    --capability capabilities/member_lookup.capability.json \
    --input search_field="Member ID" --input search_term=M1001 \
    --repeat 5 --json
```

| field | meaning |
|---|---|
| `n` | number of runs (5) |
| `success_rate` | `(Success + BusinessOutcome count) / n` -- both are legitimate completions per the existing three-way outcome taxonomy, so a run that consistently reaches a declared business outcome is not "flaky" |
| `status_counts` | per-status tally across the n runs |
| `failure_determinism` | computed only over `hard_failure` runs, grouped by `(at_step_id, trigger)`: `"deterministic"` if every hard failure shares the same step+trigger (a real, reproducible bug), `"non-deterministic"` if they differ (genuine flakiness), `"n/a"` if there were no hard failures at all |
| `duration_s` | min / max / mean across all n runs, free signal on timing-related flakiness |
| `run_ids` | the n `run_id`s (each suffixed `-r{i}` to avoid collisions between fast runs landing in the same wall-clock second), for tracing back into the raw, gitignored trail under `evidence/replays/<run_id>/` |

This run: all 5 replays of `member_lookup` against `search_field=Member
ID, search_term=M1001` (the same happy-path member used in
`evidence/replay/01-success/` and `evidence/capability_api/`) came back
`success`, so `success_rate` is `1.0` and `failure_determinism` is
`"n/a"` -- there is nothing to attribute a cause to when nothing failed.
Duration ranged `2.947s`–`3.508s` (mean `3.195s`), consistent with the
single-run timings already captured elsewhere in `evidence/`. This
particular curated run was 5/5 success, so it never exercises the
`failure_determinism` grouping branch (`"deterministic"` vs.
`"non-deterministic"`) — that logic is verified by the 8 unit tests in
`agent/tests/test_replay_repeat.py`, not just asserted here.

## Design notes carried into the implementation

- **No parallelism.** The loop is a plain sequential `for i in
  range(N)`, matching the brief's explicit exclusion of scaling
  infrastructure (queues, thread pools, retries) from this project's
  scope.
- **No retry-on-failure inside the loop.** Each of the N runs reaches
  its natural conclusion once. Retrying a failed run before counting it
  would hide the exact flakiness this feature exists to measure.
- **Handoff defaults off for repeat runs**, the same reasoning as
  `agent/capability_api.py`: an automated stability check blocking N
  times for a human operator defeats its own purpose. `--repeat-
  allow-handoff` opts back in per invocation if needed.
- **`run_id` collision fix.** `_run_id()` is second-resolution
  (`replay-YYYYMMDD-HHMMSS`, no random suffix); a run that fails at
  preflight returns near-instantly, so two such runs in the same repeat
  loop could otherwise land in the same second and silently overwrite
  each other's `evidence/replays/<run_id>/replay.jsonl`. Each repeat
  run now gets a `-r{i}` suffix appended to its `run_id`
  (`agent/replay.py`'s `Replayer.__init__` takes an additive
  `run_id_suffix` parameter, default `""`, so every other caller is
  unaffected).

## Sanitization

No redaction was needed: `summary.json` carries only aggregate counts,
timings, and `run_id`s -- no extracted member data. `evidence_path` and
per-run raw output stay entirely out of this file and live only under
the gitignored `evidence/replays/` trail, matching the convention
already documented in `evidence/README.md` and `evidence/capability_api/
README.md`.
