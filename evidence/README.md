# evidence/ — curated submission set

This directory is a deliberate, small selection of run output, not the
raw evidence tree. Every discovery and replay invocation writes a full
trail under `evidence/runs/`, `evidence/replays/`, `evidence/sessions/`
(all git-ignored); what is committed here is one coherent thread that
demonstrates the whole system end to end, once.

It covers what the brief's Section 6.3 asks for:

- **a saved example capability artifact** — `compiled/`
- **logs from a discovery run** — `discovery/`
- **logs from a replay run, including one that hits an
  error / exceptional state** — `replay/`, five runs covering the
  full outcome taxonomy (a success, all three declared business
  outcomes, and a real human-in-the-loop handoff).

It also includes `risk_gating/`, a standalone demonstration of the
`requires_confirmation` preflight gate (Safety, see REPORT.md) — real
runs, not a hand-written illustration.

All paths in the JSON/JSONL here are repo-relative. No absolute paths,
machine username, hostname, or API-key material appears in any file —
see "Sanitization" at the bottom.

## Read it in this order

### 1. `compiled/member_lookup.capability.json` (+ `.notes.md`)

The product: the `member_lookup` capability, v1.1.0. A `read_only`,
no-confirmation capability with two typed inputs (`search_field`,
`search_term`), three typed outputs (`full_name`, `savings_balance`,
and `outcome_code` set by replay on every run), ordered steps with
ranked locator fallbacks and per-step checkpoints, three declared
business outcomes (`MEMBER_NOT_FOUND`, `ACCESS_DENIED`,
`SUPERVISOR_REVIEW_REQUIRED`), narrowed `allowlist_routes`, and an
escalation policy. `.notes.md` records the compile decisions —
label→locator compilation, parameterization of the discovered literal,
route-allowlist narrowing, and the second search mode.

### 2. `discovery/disc-20260910-084016/`

The discovery run this artifact was compiled from: the last-name search
run. LLM-driven observe → decide → act against the live target.

- `trajectory.json` — the structured discovery trajectory (the
  compiler's input).
- `steps.jsonl` — per-step event log: each `llm_response`, the tool
  call, how the element resolved, and timing.
- `screenshots/step_01..07.png` — one per step.

Why this run: it exercises the parts that a member-ID run does not —
it forces the `Last name` radio `click` (step 1), and step 5's
`extract` walks from the value cell to its `rowheader` label
(`label_source: rowheader_in_row`), which is what lets the compiler
build the checkpoint and locator from the field *label* rather than
the discovered value. Seven steps: `click` radio → `type` term →
`click` Look Up → `click` the result row → two `extract`s → `done`.

This is dev-time authoring against the synthetic target, and the
trajectory carries the sample values it read (`James Okafor`,
`$2,219.75`) exactly as `compiled/member_lookup.capability.notes.md`
already does — it is the compiler's source input. The redaction
convention below applies to the replay logs.

### 3. `replay/` — five runs, LLM fully out of the loop

Same artifact, `replay_cli`, deterministic. The three-way outcome
taxonomy, plus the two remaining declared business outcomes:

| dir | inputs | `run_end` status | what it shows |
|---|---|---|---|
| `01-success/` | `Last name` / `Okafor` | `success` | happy path: 7 steps, both values extracted, `outcome_code = SUCCESS` |
| `02-business-outcome-access-denied/` | `Member ID` / `M1002` | `business_outcome` | step 5 check matches the declared `ACCESS_DENIED` outcome; run ends cleanly, not a failure |
| `03-escalation-handoff/` | `Member ID` / `M1007` | `success` (after handoff) | the exceptional-state run — see below |
| `04-business-outcome-not-found/` | `Member ID` / `M9999` | `business_outcome` | step 4 (`click_look_up`) check matches `MEMBER_NOT_FOUND` — `M9999` is not a row in `target_app/seed.py`, so the search-results page renders "No members matched that search." before a detail page is ever reached |
| `05-business-outcome-supervisor-review/` | `Member ID` / `M1006` | `business_outcome` | step 5 (`click_open_detail_for`) check matches `SUPERVISOR_REVIEW_REQUIRED` — `M1006` is one of `target_app/app.py`'s injected IDs that serves a confirmation interstitial instead of the record |

**`03-escalation-handoff/` is the substantive error case.** `M1007`
serves an unrecognized `alertdialog` ("Account maintenance hold") that
is not one of the declared outcomes. Trace through `replay.jsonl`:

1. steps 1–4 replay normally;
2. step 5 (`click_open_detail_for`) ACT succeeds, but CHECK fails —
   `unrecognized alertdialog on page`, no checkpoint match, no outcome
   match;
3. `escalation_opened` — trigger `on_unrecognized_dialog`; replay
   blocks and polls, the **same** live browser session stays open, a
   240s handoff deadline is set, a screenshot is written
   (`screenshots/escalation_step05_click_open_detail_for.png`, showing
   the maintenance-hold dialog as rendered in the blocked replay
   browser);
4. `human_intervention` — an operator attaches to the live session over
   CDP, acknowledges the dialog, and resumes (`resumed_by`,
   `operator_note` recorded);
5. `resume` — replay re-enters **SETTLE/CHECK for step 5 only**; ACT is
   not re-invoked;
6. steps 6–7 extract normally; `run_end` status `success`.

`escalation_record.json` is the row the escalation store persisted for
this run (exported from `evidence/sessions/escalations.db`), showing
the same open→resumed lifecycle with the handoff deadline.

`screenshots/operator_console_pause.jpg` is the second half of the
picture: the operator console (`agent.operator_console`) itself, mid-pause,
for this same escalation — run id, trigger, expected/observed, the
dialog screenshot, and the "Resume run" form all visible together. The
two screenshots in this directory show both sides of the handoff: the
target app's dialog that caused it, and the console an operator actually
uses to release it.

### 4. `risk_gating/` — the `requires_confirmation` preflight gate

REPORT.md's Safety section is explicit that `requires_confirmation`'s
enforcement is proven only by a synthetic test fixture
(`agent/tests/test_replay.py::test_preflight_gates_a_capability_that_requires_confirmation`):
it takes the real, compiled `member_lookup` capability and forces
`requires_confirmation` to `True` in memory, since no genuinely
mutating capability has been discovered yet. This directory runs that
same fixture standalone, outside the test suite, so the gate has a
visible artifact rather than only a passing assertion — **this is
still that synthetic fixture, not a real mutating capability**; see
"Worth being precise about..." in REPORT.md's Safety section for the
honest framing this doesn't change.

- `result.txt` — two real invocations against the gated capability:
  attempt 1 has no `--confirmed` and hard-fails in preflight (`trigger:
  confirmation_required`, `steps_executed: 0`, no browser launched);
  attempt 2 passes `confirmed=True` and runs the same capability to
  completion against the live target app (`status: success`, 7 steps).
- `replay.jsonl` — the event log for the confirmed run (attempt 2);
  the unconfirmed attempt never launches a browser or opens an
  evidence log, so `result.txt` is its only record.
- `confirmed_run_final_page.png` — a screenshot of the live page at the
  end of the confirmed run, proof it drove the real target app rather
  than returning a canned pass.

One framing note: unlike `03-escalation-handoff/`, this gate is a
preflight check with no browser session to hand off — there's no
operator-console pause to screenshot here, because refusing to run
unattended and an escalation mid-run are different mechanisms. The
"pause" this directory documents is the hard-fail on attempt 1; the
"resume" is attempt 2 succeeding once confirmation is supplied.

## How the pieces connect

`discovery/` (run once) → compiled into `compiled/` → `compiled/`
replayed deterministically in every `replay/` run. `01-success`
replays the exact path discovery took; `02` and `03` are inputs
discovery never saw, showing the artifact handles a declared business
outcome and an undeclared exceptional state without an LLM in the
decision loop.

## Sanitization

Every file in `discovery/`, `replay/`, and `risk_gating/` was checked
before commit:

- **Absolute paths / username** — the raw logs embed absolute
  `screenshot` paths under the author's home directory. All were made
  repo-relative and repointed at the curated screenshot locations.
  `grep` for `/Users/`, `/home/`, `C:\`, and the machine username over
  the curated set: none.
- **API keys** — `grep` for `sk-ant-`, generic `sk-…` tokens,
  `ANTHROPIC_API_KEY`, and any 25+ char token-like string over the
  curated set: none. Nothing in this evidence derives from `.env`.
- **Hostname** — `grep` for the machine hostname and `.local`: none.
  URLs are `127.0.0.1:5001` / `/` only.
- **Extracted member data in replay logs** — the replay JSONL
  redaction convention (masked, length-bearing form, e.g.
  `J…r (len 12)`) holds on every replay run in `replay/` and on
  `risk_gating/replay.jsonl`: inputs and every extracted value are
  redacted. `escalation_record.json` carries no extracted values.
  `risk_gating/result.txt` is the one deliberate exception — it's a
  captured CLI stdout, and `replay_cli` prints extracted outputs to
  stdout by design (never to the JSONL log); the confirmed run's
  outputs (`Alice Nguyen`, `$18,750.42`) are the same fictional
  `M1001` seed data already public in `target_app/seed.py`.
- **Screenshots** — all are page-only Playwright captures with no OS or
  browser chrome (no window title, no file dialog, no profile info).
  They do show synthetic member records on screen (name, ID, DOB,
  address for `M1004`, `M1001`, `M1007`); that data is fictional seed
  data (`example.com` email, `555` phone) and is the same data already
  present in `target_app/seed.py` and `VERIFICATION.md`. The operator
  console screenshot in `03-escalation-handoff/` additionally shows the
  operator name typed into the console's own form (`operator1`, a
  fictional role-based name, not a customer identifier).
- **Discovery trajectory** — `discovery/` deliberately keeps the sample
  values it captured, matching `compiled/member_lookup.capability.notes.md`,
  which is already committed and cites the same values. The redaction
  convention is a property of the replay evidence log (which runs in
  production against real data); the discovery trajectory is the
  compiler's one-time source input against the synthetic target.
