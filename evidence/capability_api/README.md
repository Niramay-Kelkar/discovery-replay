# evidence/capability_api/ — agent-facing capability interface, live proof

Section 8 stretch goal: `agent/capability_api.py`, a Flask app on port
5003 that lists `capabilities/*.capability.json` and invokes them by id
over HTTP, wrapping the same `Replayer` engine `agent.replay_cli` already
drives -- no new execution logic.

These three files are real request/response pairs from an actual run of
the live stack (`target_app` on :5001, `capability_api` on :5003),
captured verbatim except for the redaction noted in
`02-invoke-success.txt`. They are not illustrative examples.

| file | endpoint | shows |
|---|---|---|
| `01-list-capabilities.txt` | `GET /capabilities` | the discovery listing: one capability, trimmed to `capability_id`/`version`/`description`/`risk_class`/`requires_confirmation`/`inputs`/`outputs` -- no `steps`, `guardrails`, `escalation_policy`, or `discovery` provenance |
| `02-invoke-success.txt` | `POST /capabilities/member_lookup/invoke` | `search_field=Member ID`, `search_term=M1001` (a real, unflagged member) -> `Success`, HTTP 200 |
| `03-invoke-business-outcome.txt` | `POST /capabilities/member_lookup/invoke` | `search_field=Member ID`, `search_term=M1002` (`access_denied=1` in `target_app/seed.py`) -> `BusinessOutcome` (`ACCESS_DENIED`), HTTP 200 -- a legitimate answer, not a failure |

Both invocations ran with `handoff_enabled=False` (capability_api's
unconditional default) and `confirmed=False` (`member_lookup` doesn't
require confirmation, so this had no effect); each opened its own
`evidence/replays/<run_id>/` trail via the same `Replayer` machinery the
CLI uses, visible under the (gitignored) raw evidence tree.

## Sanitization

Same convention as `evidence/README.md`: `evidence_path` in the two
invoke responses was repointed from an absolute path under the author's
home directory to the repo-relative form. `full_name` and
`savings_balance` in `02-invoke-success.txt` are shown in the masked,
length-bearing form `agent.replay.redact` already produces for
`replay.jsonl` (e.g. `A…n (len 12)`) -- the live HTTP response the
caller actually received carries the real values; this file is curated
evidence, not the wire response. `03-invoke-business-outcome.txt` needed
no value redaction: its `outputs` carries only `outcome_code`, no member
PII.
