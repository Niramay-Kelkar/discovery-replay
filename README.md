# discovery-replay

A computer-use automation system that gives AI agents hands inside
legacy back-office software that has no API. An LLM discovers how to
complete a task once by driving a real UI, that discovery is compiled
into a typed, versioned, replayable capability artifact, and that
artifact is later replayed deterministically, with no LLM in the
decision loop, complete with error handling, safety guardrails, and
human escalation.

Built for an interface.ai take-home assignment. Full design reasoning
is in [REPORT.md](./REPORT.md).

## How it works

1. **Discovery.** Give the agent a goal in plain English and a
   starting point. It observes the page, decides on an action,
   performs it, and repeats until the goal is met. This run is slow
   and non-deterministic, and it happens once per capability.
2. **Compile.** The successful run is turned into a structured
   artifact: typed inputs and outputs, ordered steps with ranked
   locator strategies, per-step checkpoints, declared business
   outcomes, and a safety and escalation policy.
3. **Replay.** Given the artifact and a new set of inputs, the same
   flow runs again with no model involved in any decision. Fast,
   cheap, and repeatable. It detects and reports runtime conditions
   such as slow pages, "not found" results, permission denials, and
   review gates as distinct outcomes rather than crashes.
4. **Escalate.** If replay hits a state it cannot classify or recover
   from, it pauses and hands the *same* live browser session to a
   human operator through a small console. The operator acts, then
   resumes the run; replay re-checks the paused step — without
   re-running its action — and carries on, or fails cleanly if no one
   responds in time.

## Setup

Python 3.11+ recommended (built and tested on 3.13), plus a one-time
Chromium download for Playwright. From the repo root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

Discovery calls the Anthropic API; replay does not. Copy the env
template and add a key:

```bash
cp .env.example .env
# then edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

`.env` is git-ignored. `TARGET_APP_BASE_URL` can be set there too if you
run the target app on a non-default port.

### Start the target app

Everything below drives `target_app` — a deliberately hostile
legacy-style bank UI with no API, no JS, and messy table markup, but a
clean accessibility tree (see
[target_app/README.md](./target_app/README.md)). It runs on port 5001
and must be up for both discovery and replay:

```bash
.venv/bin/python target_app/seed.py
.venv/bin/python -m flask --app target_app.app run --port 5001
```

`seed.py` drops and recreates the SQLite database on every run, so the
target always starts in a known state (7 members, `M1001`–`M1007`).
Leave `flask` running in its own terminal.

## Demo path

One clean pass through discovery → compile → replay, for the
`member_lookup` capability (look up a bank member and read their name
and savings balance). With the target app running (see above), from the
repo root:

**1. Discover** — drive the live UI once from a plain-English goal. Slow
(~30s), non-deterministic, needs `ANTHROPIC_API_KEY`:

```bash
.venv/bin/python -m agent.discover \
  --goal "Using the last-name search option (click the 'Last name' radio button), look up the member whose last name is Okafor, open their record, and read their full name and current savings balance."
```

Ends with `outcome: completed` and the captured values, and writes a
trajectory to `evidence/runs/disc-<timestamp>/`.

**2. Compile** — turn that successful run into a typed, versioned
capability artifact (`agent/policies/member_lookup.py` supplies the
hand-authored policy layer — risk class, business outcomes, guardrails,
escalation policy — that a single run can't prove):

```bash
RUN=$(ls -1dt evidence/runs/disc-* | head -1)
.venv/bin/python -m agent.compile_cli \
  --trajectory "$RUN/trajectory.json" \
  --policy member_lookup \
  --out /tmp/member_lookup.demo.capability.json
```

Prints `compiled member_lookup v1.1.0` with `7 steps, 2 inputs,
3 outputs, 3 expected outcomes`, plus a `.notes.md` sidecar recording
every compile-time decision.

**3. Replay** — run the flow deterministically from the artifact, no LLM
in the loop. Fast (~3s):

```bash
.venv/bin/python -m agent.replay_cli \
  --capability /tmp/member_lookup.demo.capability.json \
  --input search_field="Last name" \
  --input search_term=Okafor \
  --no-handoff
```

Expected:

```
result: SUCCESS
steps executed: 7   duration: ~3s
outputs:
  full_name = 'James Okafor'
  savings_balance = '$2,219.75'
  outcome_code = 'SUCCESS'
```

The same artifact also replays by member ID — swap the inputs for
`--input search_field="Member ID" --input search_term=M1001` and it
returns Alice Nguyen's record, though only the last-name path was ever
observed during discovery.

A committed copy of this capability lives at
`evidence/compiled/member_lookup.capability.json`; the demo writes to
`/tmp` so it doesn't disturb it.

## Stretch goals

Two Section 8 stretch goals are built on top of the same replay engine
— no new execution logic in either, just thin wrappers around
`agent.replay`'s `Replayer`. Both need the target app running (see
above).

**Agent-facing capability interface** — `agent.capability_api` (Flask,
port 5003) lets a caller discover capabilities by name and invoke them
over HTTP instead of driving the CLI directly:

```bash
.venv/bin/python -m agent.capability_api --port 5003
```

```bash
curl http://127.0.0.1:5003/capabilities

curl -X POST http://127.0.0.1:5003/capabilities/member_lookup/invoke \
  -H "Content-Type: application/json" \
  -d '{"search_field": "Member ID", "search_term": "M1001"}'
```

Real request/response pairs from a live run: `evidence/capability_api/`.

**Multi-run stability** — `agent.replay_cli --repeat N` replays the same
capability and inputs N times sequentially and reports a success-rate /
failure-determinism signal instead of a single result:

```bash
.venv/bin/python -m agent.replay_cli \
  --capability capabilities/member_lookup.capability.json \
  --input search_field="Member ID" --input search_term=M1001 \
  --repeat 5 --json
```

Real output from a 5-run pass: `evidence/stability/`.

### Full verification runbook

[VERIFICATION.md](./VERIFICATION.md) is the scenario-by-scenario
runbook — every seeded case with exact, already-run commands: the two
happy paths, the three business outcomes (access denied, not found,
supervisor review), the slow-load case, and the complete
escalation / operator-console / live-session-handoff sequence. Each
step documents both the correct result and what a regression looks
like.