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
  error / exceptional state** — `replay/`, three runs covering the
  full outcome taxonomy, the third being a real human-in-the-loop
  handoff.

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

### 3. `replay/` — three runs, LLM fully out of the loop

Same artifact, `replay_cli`, deterministic. The three-way outcome
taxonomy, one run each:

| dir | inputs | `run_end` status | what it shows |
|---|---|---|---|
| `01-success/` | `Last name` / `Okafor` | `success` | happy path: 7 steps, both values extracted, `outcome_code = SUCCESS` |
| `02-business-outcome-access-denied/` | `Member ID` / `M1002` | `business_outcome` | step 5 check matches the declared `ACCESS_DENIED` outcome; run ends cleanly, not a failure |
| `03-escalation-handoff/` | `Member ID` / `M1007` | `success` (after handoff) | the exceptional-state run — see below |

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
   the maintenance-hold dialog);
4. `human_intervention` — an operator attaches to the live session over
   CDP, acknowledges the dialog, and resumes (`resumed_by`,
   `operator_note` recorded);
5. `resume` — replay re-enters **SETTLE/CHECK for step 5 only**; ACT is
   not re-invoked;
6. steps 6–7 extract normally; `run_end` status `success`.

`escalation_record.json` is the row the escalation store persisted for
this run (exported from `evidence/sessions/escalations.db`), showing
the same open→resumed lifecycle with the handoff deadline.

## How the pieces connect

`discovery/` (run once) → compiled into `compiled/` → `compiled/`
replayed deterministically in every `replay/` run. `01-success`
replays the exact path discovery took; `02` and `03` are inputs
discovery never saw, showing the artifact handles a declared business
outcome and an undeclared exceptional state without an LLM in the
decision loop.

## Sanitization

Every file in `discovery/` and `replay/` was checked before commit:

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
  `J…r (len 12)`) holds on all three replay runs: inputs and every
  extracted value are redacted in `replay.jsonl`. `escalation_record.json`
  carries no extracted values.
- **Screenshots** — all are page-only Playwright captures with no OS or
  browser chrome (no window title, no file dialog, no profile info).
  They do show the synthetic member record on screen (name, ID, DOB,
  address for `M1004`); that data is fictional seed data
  (`example.com` email, `555` phone) and is the same data already
  present in `target_app/seed.py` and `VERIFICATION.md`.
- **Discovery trajectory** — `discovery/` deliberately keeps the sample
  values it captured, matching `compiled/member_lookup.capability.notes.md`,
  which is already committed and cites the same values. The redaction
  convention is a property of the replay evidence log (which runs in
  production against real data); the discovery trajectory is the
  compiler's one-time source input against the synthetic target.
