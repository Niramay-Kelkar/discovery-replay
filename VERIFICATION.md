# VERIFICATION.md — manual verification runbook

A step-by-step runbook for exercising every seeded scenario in
`target_app` against the current codebase. Every command below was run
and its result confirmed before being written down (2026-09-10, on
`main`, capability `member_lookup` v1.1.0).

Each scenario gives: the exact command, what a correct result looks
like, and what a wrong result would look like (so this is useful for
catching a regression, not just confirming the happy path).

Conventions:

- All commands run from the repo root, using the project venv
  (`.venv/bin/python`).
- `target_app` runs on port **5001**, the operator console on **5002**,
  replay's Chrome DevTools endpoint (escalation scenario only) on
  **9222**.
- Discovery needs `ANTHROPIC_API_KEY` in `.env` (already git-ignored).
  Replay needs no key — no LLM is in its loop.
- Replay exit codes: `0` = Success or BusinessOutcome (both are
  legitimate answers), `1` = HardFailure, `3` = PendingEscalation.
- Raw per-run evidence lands under `evidence/runs/`, `evidence/replays/`,
  `evidence/sessions/` — all git-ignored. Extracted values are redacted
  in the JSONL logs; the real values are returned to the caller and
  printed to stdout only.

---

## 1. Start `target_app`

```bash
.venv/bin/python target_app/seed.py
.venv/bin/python -m flask --app target_app.app run --port 5001
```

`seed.py` drops and recreates the table on every run, so the target
always starts in a known state (7 members, M1001–M1007). Leave the
`flask` process running in its own terminal for every scenario below.

**Correct:** `seed.py` prints `Seeded 7 members into .../target_app/bank.db`;
`flask` prints `Running on http://127.0.0.1:5001`. `curl -s -o /dev/null
-w '%{http_code}' http://127.0.0.1:5001/` returns `200`.

**Wrong:** `seed.py` reports a row count other than 7 (schema/seed drift);
`flask` fails to bind (port 5001 already in use — `lsof -ti:5001` and
kill the stale process); `curl` returns anything but `200`.

Quick check of the seeded rows (optional):

```bash
.venv/bin/python -c "import sqlite3; c=sqlite3.connect('target_app/bank.db'); [print(r) for r in c.execute('select member_id,first_name,last_name,savings_balance,access_denied from members')]"
```

Expected:

```
('M1001', 'Alice', 'Nguyen', 1875042, 0)
('M1002', 'Robert', 'Delgado', 540388, 1)
('M1003', 'Sandra', 'Kim', 9032150, 0)
('M1004', 'James', 'Okafor', 221975, 0)
('M1005', 'Maria', 'Santos', 4500000, 0)
('M1006', 'Pat', 'Ashwood', 1200000, 0)
('M1007', 'Dana', 'Whitfield', 760514, 0)
```

(Balances are stored in cents. All records are obviously fictional demo
data for a fictional "Acme Savings" bank.)

---

## 2. Replay — happy path via Member ID (M1001)

```bash
.venv/bin/python -m agent.replay_cli \
  --capability evidence/compiled/member_lookup.capability.json \
  --input search_field="Member ID" \
  --input search_term=M1001 \
  --no-handoff
```

**Correct:**

```
result: SUCCESS
steps executed: 7   duration: ~3–4s
outputs:
  full_name = 'Alice Nguyen'
  savings_balance = '$18,750.42'
  outcome_code = 'SUCCESS'
```

Exit code `0`.

**Wrong:** any `result:` other than `SUCCESS`; a `full_name` /
`savings_balance` that doesn't match Alice Nguyen / `$18,750.42`;
`steps executed` < 7 (a step failed or an outcome fired early); a
`HARD_FAILURE` at `enter_search_term` or `click_search_field` (locator
drift against the search form).

---

## 3. Replay — happy path via Last name (Okafor → M1004)

Proves the *other* search mode. Only this exact input pair was observed
during discovery; the Member-ID pair in scenario 2 was not — so the two
together are the evidence that `search_field` genuinely generalizes.

```bash
.venv/bin/python -m agent.replay_cli \
  --capability evidence/compiled/member_lookup.capability.json \
  --input search_field="Last name" \
  --input search_term=Okafor \
  --no-handoff
```

**Correct:**

```
result: SUCCESS
steps executed: 7   duration: ~3s
outputs:
  full_name = 'James Okafor'
  savings_balance = '$2,219.75'
  outcome_code = 'SUCCESS'
```

Exit code `0`.

**Wrong:** `MEMBER_NOT_FOUND` (the `click_search_field` step didn't
actually select the "Last name" radio, so the app searched the ID column
for "Okafor" and found nothing — this is the specific regression this
scenario exists to catch); a `HARD_FAILURE` resolving
`radio {{search_field}}` (the radio's accessible name changed);
wrong member returned.

---

## 4. Replay — access denied (M1002)

```bash
.venv/bin/python -m agent.replay_cli \
  --capability evidence/compiled/member_lookup.capability.json \
  --input search_field="Member ID" \
  --input search_term=M1002 \
  --no-handoff
```

**Correct:**

```
result: BUSINESS_OUTCOME
steps executed: 5   duration: ~3s
outcome code: ACCESS_DENIED
description:  The teller profile is not authorized to view this member's record.
detected at:  step click_open_detail_for
```

Exit code `0` (a business outcome is a legitimate answer, not a
failure). `M1002` has a real `access_denied=1` column and `/member/M1002`
returns HTTP 403.

**Wrong:** `SUCCESS` with a member record for Robert Delgado (the 403 /
denial page was not recognized and replay clicked through to data it
should never surface — a safety regression); `HARD_FAILURE` /
`PendingEscalation` (the denial was treated as an unrecognized state
instead of the declared `ACCESS_DENIED` outcome — a detection-rule
regression).

---

## 5. Replay — not found (nonexistent ID)

```bash
.venv/bin/python -m agent.replay_cli \
  --capability evidence/compiled/member_lookup.capability.json \
  --input search_field="Member ID" \
  --input search_term=M9999 \
  --no-handoff
```

`M9999` is not seeded (verify-not-seeded, per the target README).

**Correct:**

```
result: BUSINESS_OUTCOME
steps executed: 4   duration: ~3s
outcome code: MEMBER_NOT_FOUND
description:  No member matched the search term.
detected at:  step click_look_up
```

Exit code `0`. Note it is detected at `click_look_up` (the results page
says "No members matched that search."), one step earlier than the
access-denied case.

**Wrong:** `HARD_FAILURE` at `click_open_detail_for` (the "no matches"
results page was not recognized, so replay tried to click a result link
that isn't there); `SUCCESS` (impossible unless the search matched
something it shouldn't).

---

## 6. Replay — slow load (M1003)

`M1003` is an ordinary row, but `/member/M1003` sleeps ~4s server-side
before rendering (`SLOW_LOAD_SECONDS = 4` in `target_app/app.py`),
standing in for transient slowness. Search results are instant; only the
detail page is slow.

```bash
time .venv/bin/python -m agent.replay_cli \
  --capability evidence/compiled/member_lookup.capability.json \
  --input search_field="Member ID" \
  --input search_term=M1003 \
  --no-handoff
```

**Correct:**

```
result: SUCCESS
steps executed: 7   duration: ~8s
outputs:
  full_name = 'Sandra Kim'
  savings_balance = '$90,321.50'
  outcome_code = 'SUCCESS'
```

Exit code `0`. Measured wall clock ~8–9s total, of which ~4s is the
injected sleep on the `click_open_detail_for` navigation — the rest of
the run is comparable to scenario 2. The `click_open_detail_for` step's
compiled settle bound (`network_idle`, 12s) is wide enough to absorb it,
and replay's click honours that bound rather than a fixed 4s.

**Wrong:** `HARD_FAILURE` with `TimeoutError: Locator.click: Timeout ...
exceeded` at `click_open_detail_for` (the click's navigation wait is
back to a fixed 4s and the slow page trips it — this is the exact bug
fixed on 2026-09-10; a reappearance means that fix regressed); total
duration ~4s and a `MEMBER_NOT_FOUND` (the search itself somehow got the
delay); duration >> 15s (the delay is no longer bounded).

---

## 7. Replay — supervisor-review interstitial (M1006)

**This is a *recognized business outcome*, not an escalation.** `M1006`
serves an `alertdialog` titled "Supervisor review required" whose
wording a PolicySpec detection rule matches. Replay reports it and
stops; it does **not** click "Continue", and it does **not** hand off to
a human. Contrast scenario 8 (M1007), which looks superficially similar
— an `alertdialog` blocking the detail page — but is deliberately
un-declared, so it escalates. The whole point of having both is that
this one must *not* escalate.

```bash
.venv/bin/python -m agent.replay_cli \
  --capability evidence/compiled/member_lookup.capability.json \
  --input search_field="Member ID" \
  --input search_term=M1006 \
  --no-handoff
```

**Correct:**

```
result: BUSINESS_OUTCOME
steps executed: 5   duration: ~3s
outcome code: SUPERVISOR_REVIEW_REQUIRED
description:  The record is flagged for supervisor review; a confirmation interstitial blocks the detail page. Replay reports this rather than clicking through.
detected at:  step click_open_detail_for
```

Exit code `0`.

**Wrong:** `PENDING_ESCALATION` or `HARD_FAILURE` with trigger
`on_unrecognized_dialog` (the interstitial stopped being recognized and
is now going down the escalation path — a detection-rule regression, and
exactly the M1006/M1007 confusion this scenario guards against);
`SUCCESS` with Pat Ashwood's record (replay clicked "Continue" through a
review gate it was supposed to report).

---

## 8. Replay — full escalation / handoff (M1007)

`M1007` serves an `alertdialog` named "Account maintenance hold" whose
wording and `aria-label` match **no** declared outcome. Replay cannot
classify it, so it escalates: it screenshots the page, records a pending
escalation in the shared SQLite store, keeps the live browser open, and
blocks — polling for an operator's resume up to the handoff timeout.

This scenario uses two extra processes (operator console, and an
operator attaching over Chrome DevTools) and runs replay **headed** with
a DevTools port so the operator drives the *same* browser.

### 8a. Start the operator console (separate terminal)

```bash
.venv/bin/python -m agent.operator_console --port 5002
```

**Correct:** prints `Running on http://127.0.0.1:5002`. Opening
`http://127.0.0.1:5002/` shows "Replay operator console" with
"Pending (0)".

### 8b. Start replay headed, with a DevTools port (separate terminal)

```bash
.venv/bin/python -m agent.replay_cli \
  --capability evidence/compiled/member_lookup.capability.json \
  --input search_field="Member ID" \
  --input search_term=M1007 \
  --headed --cdp-port 9222 --handoff-timeout 240 --poll-interval 2
```

**Correct:** prints `run_id: replay-<timestamp>`, runs steps 1–4
normally, then goes quiet — it is blocked at step 5
(`click_open_detail_for`) polling the store. Within a few seconds the
console's "Pending" count goes to 1, showing the goal, the trigger
(`on_unrecognized_dialog`, `check` phase), expected vs. observed
(`element_visible(rowheader Full name)=False; ... unrecognized
alertdialog on page`), and a screenshot of the hold dialog.

(In this build environment a headed Chromium has no real display, but
the handoff below is genuine — the operator attaches to and drives the
identical browser process and tab replay is paused on.)

**Wrong:** replay prints `result: BUSINESS_OUTCOME` or `SUCCESS` and
exits (the hold page was matched by some rule or clicked through — it
should be unrecognized); nothing appears in the console (the replay
process and console are pointed at different `--session-db` paths — both
default to `evidence/sessions/escalations.db`, so don't override one and
not the other); replay exits immediately with `PENDING_ESCALATION`
(the `--headed`/handoff flags were dropped and it took the CI path).

### 8c. Find the pending escalation id

Either read it off the console page (`#1 · member_lookup · step 5`), or:

```bash
.venv/bin/python -c "
from agent.escalation import SessionStore
s = SessionStore('evidence/sessions/escalations.db')
for r in s.list_pending():
    print(r['id'], r['run_id'], r['step_id'], r['trigger'], '|', r['current_url'] if 'current_url' in r.keys() else r['observed'])
"
```

**Correct:** one row, e.g. `1 replay-<ts> click_open_detail_for
on_unrecognized_dialog | ...`.

### 8d. Attach as the operator over CDP and clear the hold

This is the real Phase 6 approach: connect to replay's browser over the
DevTools endpoint, find the exact tab it is blocked on, and drive it by
hand. Closing the CDP connection at the end detaches — it does **not**
close replay's browser.

```bash
.venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://localhost:9222")
    ctx = browser.contexts[0]
    page = next(p for p in ctx.pages if "/member/M1007" in p.url)
    print("attached to:", page.url)
    print("dialog:", " ".join(page.get_by_role("alertdialog").inner_text().split())[:70])

    # do what the step needs: acknowledge the maintenance hold
    page.get_by_role("button", name="Acknowledge hold and open record").click()
    page.wait_for_load_state("networkidle")

    print("after ack, url:", page.url)
    print("record visible:", page.get_by_role("rowheader", name="Full name").is_visible())
    browser.close()  # detaches CDP; replay's browser stays open
PY
```

**Correct:**

```
attached to: http://127.0.0.1:5001/member/M1007
dialog: Account maintenance hold Member M1007's record is locked pending ...
after ack, url: http://127.0.0.1:5001/member/M1007?ack=yes
record visible: True
```

**Wrong:** `connect_over_cdp` raises `ECONNREFUSED` (replay wasn't
started with `--cdp-port 9222`, or already exited); `StopIteration` on
the `next(...)` (no tab is on `/member/M1007` — replay isn't actually
paused there); `record visible: False` after ack (the hold page's button
name or `?ack=yes` behaviour changed).

### 8e. Resume the run from the console

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:5002/resume/1 \
  -d 'operator=operator1' \
  -d 'note=attached over CDP, acknowledged the maintenance hold, record now renders'
```

(Replace `/resume/1` with the id from 7c. Or fill the "Resume run" form
on the console page — same effect.)

**Correct:** HTTP `302` (redirects back to the console index). The
console moves the escalation from "Pending" to "Recent" as `resumed`,
with the operator name and note.

### 8f. Confirm the run completes

Back in the replay terminal, within one poll interval it prints:

```
result: SUCCESS
steps executed: 7   duration: <however long the handoff took>
outputs:
  full_name = 'Dana Whitfield'
  savings_balance = '$7,605.14'
  outcome_code = 'SUCCESS'
```

Exit code `0`. On resume, replay re-runs **SETTLE/CHECK for step 5
only** — the record table is now visible so the checkpoint passes — then
runs steps 6–7 (the extracts). It never re-runs the step 5 ACT (the
click). The run's JSONL evidence
(`evidence/replays/<run_id>/replay.jsonl`) contains an
`escalation_opened` record, then a `human_intervention` record
(`human_intervened: true`, `resumed_by`, `operator_note`,
`paused_seconds`), then a `resume` record, then the passing step
records.

**Wrong:** replay stays blocked after the resume (console and replay on
different `--session-db` paths; or the resume POST hit a 404 because the
id was wrong); `HARD_FAILURE` with trigger
`on_unrecognized_dialog:handoff_timeout` (nobody resumed within
`--handoff-timeout` seconds — expected only if you deliberately wait it
out); `steps executed: 8+` or a second `escalation_opened` (ACT got
re-run and re-hit the dialog — the "never re-run ACT on resume"
guarantee regressed); the member's name/balance appearing verbatim in
`replay.jsonl` (redaction regressed — the log should only ever hold the
masked `redact()` form).

### 8g. CI variant (no human, no browser)

```bash
.venv/bin/python -m agent.replay_cli \
  --capability evidence/compiled/member_lookup.capability.json \
  --input search_field="Member ID" --input search_term=M1007 --no-handoff
```

**Correct:** `result: PENDING_ESCALATION`, `trigger: on_unrecognized_dialog
-> configured action: escalate`, exit code `3`. Replay pauses cleanly
and returns instead of blocking for an operator.

---

## 9. Full discovery → compile → replay, end to end

For completeness: run the whole pipeline from a plain-English goal.
This uses a goal that reproduces the last-name trajectory the canonical
`member_lookup` policy is bound to, and writes to a scratch path so it
doesn't disturb the committed artifact under `evidence/compiled/`.

### 9a. Discovery (needs `ANTHROPIC_API_KEY`, model `claude-sonnet-4-6`)

```bash
.venv/bin/python -m agent.discover \
  --goal "Using the last-name search option (click the 'Last name' radio button), look up the member whose last name is Okafor, open their record, and read their full name and current savings balance."
```

**Correct:**

```
run_id: disc-<timestamp>
outcome: completed -- ...
steps: 7  retries: 0  resolution_errors: 0
captured:
  full_name = 'James Okafor'  [label: 'Full name']
  savings_balance = '$2,219.75'  [label: 'Savings balance']
evidence: evidence/runs/disc-<timestamp>
```

Exit code `0`. The trajectory's steps should be: `click radio "Last
name"` → `type "Okafor"` → `click "Look Up"` → `click "Open detail for
James Okafor, member M1004"` → two `extract`s → `done`.

**Wrong:** `outcome: max_steps` / `timeout` / `error` (the model got
stuck — inspect `evidence/runs/disc-<ts>/steps.jsonl`);
`resolution_errors > 0` repeatedly on the same step (an accessible name
in the app drifted from what the system prompt leads the model to
expect); no `click` on the `radio "Last name"` in the trajectory (the
goal wording no longer forces the radio — compilation would then
silently produce an ID-only capability).

### 9b. Compile

```bash
.venv/bin/python -m agent.compile_cli \
  --trajectory evidence/runs/disc-<timestamp>/trajectory.json \
  --policy member_lookup \
  --out /tmp/member_lookup.demo.capability.json
```

**Correct:**

```
compiled member_lookup v1.1.0
  7 steps, 2 inputs, 3 outputs, 3 expected outcomes
  artifact -> /tmp/member_lookup.demo.capability.json
  notes    -> /tmp/member_lookup.demo.capability.notes.md
```

Exit code `0`. `2 inputs` = `search_field` (enum) + `search_term`.
`7 steps` = 1 synthesized `open_entry` + 6 action steps (`done` is
dropped).

**Wrong:** `compile failed: ...` — most likely
`output_name_mapping does not cover ...` (the run captured an output
under a name the policy's mapping doesn't list) or `captured no label`
(an `extract` didn't resolve the field's label). `1 input` only (the
radio click is missing from the trajectory — see 8a wrong case).

### 9c. Replay the freshly compiled artifact

```bash
.venv/bin/python -m agent.replay_cli \
  --capability /tmp/member_lookup.demo.capability.json \
  --input search_field="Last name" --input search_term=Okafor \
  --no-handoff
```

**Correct:** `result: SUCCESS`, `James Okafor` / `$2,219.75`, 7 steps,
exit code `0` — same as scenario 3, but against an artifact just
compiled from a fresh run rather than the committed one.

**Wrong:** any divergence from scenario 3's result means the
discovery→compile path produced something different from the committed
artifact — diff `/tmp/member_lookup.demo.capability.json` against
`evidence/compiled/member_lookup.capability.json` (the `discovery`
provenance block and step `description` strings will differ; the
`steps`/`locators`/`inputs`/`outputs`/`guardrails` should not).

---

## Teardown

```bash
# stop the flask target_app and the operator console (Ctrl-C in their terminals)
# raw evidence is safe to delete between passes; it is all git-ignored:
rm -rf evidence/runs/* evidence/replays/* evidence/sessions/*
```

`evidence/compiled/` is tracked and should **not** be cleared.
