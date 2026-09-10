# Build log

Append-only, one entry per work session. Covers what was built, what was
verified and against which seeded `target_app` cases, bugs found and how
they were fixed, and what got committed. This is session narrative/history
— for the current architectural state and the reasoning behind it, see
`CLAUDE.md`, which does not accumulate this kind of entry going forward.

---

## 2026-09-09 — Legacy bank demo target (`target_app/`)

### Built

`target_app/`, the deliberately hostile automation target. Flask +
SQLite, server-rendered HTML, no JS, no frontend framework.

- `schema.sql` / `seed.py` — single `members` table, dropped and
  reseeded on every `seed.py` run. Six seeded rows. Per-row flags
  (`access_denied`, `slow_load`, `interstitial`) drive the hostile
  behaviors rather than special-casing IDs in the app.
- `app.py` — three routes: `/` (search form), `/search` (results or a
  distinct "no matches" page), `/member/<id>` (detail, or access-denied
  / interstitial / slow depending on the row). Exact case-insensitive
  match. Balance stored in cents, formatted at render.
- `templates/` — `base.html` carries the hostile markup: nested
  `role="presentation"` layout tables, generic class names (`div1`,
  `box2`, `c1`…), and an unrelated `<iframe title="Branch bulletin
  board">`. Real data is rendered in genuine `<table>`s with
  `<th scope="row">` / `columnheader` so the a11y tree stays clean.
  Interstitial is a server-rendered `role="alertdialog"` with
  Continue/Cancel links (no JS `confirm()`, which would block
  automation).
- `README.md` — run steps, the seeded IDs/last names and what each
  demonstrates, and the accessibility contract (the exact roles and
  accessible names automation should rely on).

### Verified

Ran locally on port 5001, checked every required case by `curl` and
again through the Chrome accessibility tree:

- **Happy path by ID** — `M1001` → results table → detail page, balance
  `$18,750.42`. Detail exposes `table "Member record for Alice
  Nguyen"` with `rowheader`/`cell` pairs.
- **Happy path by last name** — `Nguyen` → one match → same detail page.
- **Access denied** — `M1002` → "Access Denied" page, HTTP 403,
  `role="alert"` naming the member. No data rendered.
- **Slow load** — `M1003` detail page returns after ~4.0s (measured);
  search results for it are instant.
- **Interstitial** — `M1006` → `role="alertdialog"` "Supervisor review
  required" with `button "Continue to member record"` /
  `button "Cancel and return to search"`. `?confirm=yes` proceeds to
  the real record (Pat Ashwood).
- **Not found** — search `M9999` and last name `Zzzznope` → "No members
  matched that search." at HTTP 200; direct `/member/M9999` → dedicated
  "No such member." page at HTTP 404. No stack traces.
- Empty query re-renders the form with an inline `role="alert"` error.

### Bugs found and fixed

- **Results "City" column showed the wrong segment.** City was derived
  as `address.split(",")[1]`, which for an address with a unit line
  ("1200 Lakeshore Drive, Apt 5B, Chicago, IL 60611") produced
  "Apt 5B". Changed to take the second-to-last comma segment, which is
  always the city given the `street, City, ST ZIP` format. Reverified
  M1001/M1003/M1005/M1006 → Springfield/Chicago/Peoria/Akron.
- **Radio buttons' accessible names.** A wrapping `<label>` alone left
  the field selector's name ambiguous in the tree. Switched to explicit
  `id` + `<label for>` plus `aria-label` so the radios report
  "Member ID" / "Last name".

### Committed

- `target_app/` (app, schema, seed, templates, README, requirements)
- `.gitignore` — ignore `target_app/bank.db`
- this BUILD_LOG entry

---

## 2026-09-09 — target_app: move injected conditions to the app layer, add branch chrome

### What changed and why

**`slow_load` and `interstitial` are no longer DB columns.** They were
modeled as per-row flags on the `members` table, which put an injected
test condition on the same footing as real record data. They are now
hardcoded member-ID sets checked in the `/member/<id>` route handler:

- `SLOW_LOAD_IDS = {"M1003"}` — route sleeps 4s before rendering.
- `INTERSTITIAL_IDS = {"M1006"}` — route serves the `role="alertdialog"`
  confirmation unless `?confirm=yes`.

`M1003` and `M1006` are now ordinary rows; the database says nothing
special about them.

**`access_denied` stays a real column.** The distinction is deliberate:
`access_denied` is a genuine fact about that member's record — an
authorization *business outcome* the replay layer should detect and
report as a distinct result. The slow load and the interstitial are
*runtime conditions* (transient slowness, an unrecognized dialog) that
the replay layer must cope with but that are not properties of the data.
Data-backed vs app-layer-injected now matches that split in the code.
`schema.sql`, `seed.py`, and the README seeded-records table were
updated to match, including a short "why the split" note in the README.

**Added decorative branch chrome to `base.html`.** A right-hand column
(`.col2`), built with the same nested `role="presentation"` tables and
generic class names as the rest of the markup: branch name/address,
lobby hours, and a generic FDIC / member-agreement disclaimer. Tied to
no member data — pure structural/visual noise, the same category as the
existing unrelated iframe (which moved into this column). It carries no
interactive elements and no tables/landmarks, so in the accessibility
tree it appears only as loose text nodes for automation to ignore.

### Verified

Reseeded, restarted on port 5001, rechecked all six cases by `curl` and
the detail + sidebar via the Chrome accessibility tree:

- **Happy path by ID** (`M1001`) — results → detail, balance
  `$18,750.42`; detail still exposes `table "Member record for Alice
  Nguyen"` with clean `rowheader`/`cell` pairs despite the new sidebar.
- **Happy path by last name** (`Nguyen`) — one match → same detail.
- **Access denied** (`M1002`) — HTTP 403, "Access Denied", no data.
- **Slow load** (`M1003`) — detail returns after ~4.0s (measured);
  confirmed the delay is now ID-driven (`M1001` detail returns in
  ~0.03s, `M1003` search result is instant).
- **Interstitial** (`M1006`) — `role="alertdialog"` "Supervisor review
  required"; `?confirm=yes` → real record (Pat Ashwood).
- **Not found** — search `M9999` / `Zzzznope` → "No members matched
  that search." (HTTP 200); `/member/M9999` → "No such member."
  (HTTP 404).
- Sidebar noise (branch address, lobby hours, "Member FDIC") renders on
  every page and surfaces only as `generic` text nodes — no new
  landmarks, tables, or interactive elements.

### Bugs found and fixed

None. Straight refactor; all six cases behaved correctly on first
re-verify.

### Committed

- `target_app/schema.sql`, `seed.py`, `app.py` — drop the two columns,
  add `SLOW_LOAD_IDS` / `INTERSTITIAL_IDS` route checks
- `target_app/templates/base.html` — decorative branch column
- `target_app/README.md` — updated seeded-records table, the
  data-backed-vs-injected note, and the accessibility contract
- this BUILD_LOG entry

---

## 2026-09-09 — Capability artifact schema: design doc + Pydantic models

The typed contract discovery produces and replay consumes. Called out
in the brief as a focal point, so it was done design-first.

### Designed (`schema/DESIGN.md`)

Written before any code. Covers:

- **The mechanical / policy split and why it exists.** The mechanical
  layer (inputs, outputs, steps, locators, checkpoints) is an observed,
  generalized transcript of one successful run. The policy layer (risk
  class, expected outcomes, guardrails, escalation policy) is *authored
  separately* — a single green trajectory never observes a failure
  state, so it cannot be the source of truth for how to handle one. The
  schema keeps the two physically separate; the compiler emits empty
  policy stubs and `policy_authored_by` gates replay. The three-way
  outcome taxonomy (success / business outcome / hard failure) falls out
  of this split.
- **Ranked locator strategies.** Each step target carries an ordered
  list, not one selector. Ranking = most semantically stable first:
  `aria_role` (role + accessible name — tied to the app's a11y
  contract, survives the hostile DOM) primary, `text_label` fallback,
  `test_id` / `css` / `xpath` reserved. Compile from the field label,
  not the discovered value.
- **Checkpoints, including the `any_of` composite.** ACT → SETTLE →
  CHECK. A step is satisfied if its element-visibility condition holds
  **or** a recognized expected outcome is detected — without that, a
  legitimate "no such record" answer looks like a hung page and burns
  the retry budget. `outcome_matched` is the checkpoint kind that
  bridges to the policy layer's outcome-detection.
- **Typed inputs/outputs** as a contract (named, typed, required flag),
  **risk classification + escalation policy** (retry budget that only
  redoes SETTLE/CHECK, per-trigger actions for `on_step_timeout` /
  `on_hard_failure` / `on_unrecognized_dialog` / `on_checkpoint_failure`,
  same-session human handoff timeout), and **expected business
  outcomes** (named codes + composable `DetectionRule`s off label /
  structural signals).
- Notes that the full discovery-side tool vocabulary is a separate
  deliverable; the schema pins only the minimal replay-side action set.

### Built (`agent/models.py`)

Pydantic v2 models implementing DESIGN.md exactly:

- `LocatorStrategy` — `extra="allow"`, open `kind` string, so a new
  strategy type is additive with no migration. Knows `aria_role` and
  `text_label` fields; validates each kind has enough to resolve.
- `Checkpoint` — recursive; kinds `element_visible`, `text_present`,
  `outputs_non_empty`, `outcome_matched`, `any_of`, `all_of`.
- `Step` — ordinal, ranked `locators`, `ActionType`
  (navigate/fill/select_option/click/press_key/extract), `SettleSpec`,
  `checkpoint`; validates action-specific payloads and 1..N locator
  ranks.
- `InputParam` / `OutputParam` — named, `ParamType`
  (string/integer/number/boolean/date/money), required flag.
- `ExpectedOutcome` + `DetectionRule` (recursive:
  text_present/aria_visible/http_status/url_matches/any_of/all_of).
- `RiskClass` literal (`read_only` | `mutating`), `EscalationPolicy`
  (retry counts + trigger→action mapping).
- `Guardrails` (allowlist routes + action types, denylist text
  patterns, max steps, off-domain nav).
- Top-level `Capability` tying it together, with cross-field
  validators: contiguous ordinals; `fill`/`extract` steps reference
  declared params; template tokens resolve to inputs; guardrail action
  allowlist covers every step action; `outcome_matched` /
  detection-referenced codes resolve; exactly one terminal
  `outputs_non_empty` step.

### Worked example (`schema/build_example.py` → `schema/example_artifact.json`)

Hand-authored `Capability` for the target app's member-lookup flow,
constructed directly via the models and serialized to
`schema/example_artifact.json` (same role that file served in the
earlier related project). 10 steps (open search → choose field → enter
term → submit → open detail → 5 label-targeted extracts), 3 expected
outcomes (`MEMBER_NOT_FOUND`, `ACCESS_DENIED`,
`SUPERVISOR_REVIEW_REQUIRED`), `read_only`, guardrails narrowed to
`/`, `/search`, `/member/*`.

### Verified

- `python schema/build_example.py` builds and serializes with no
  validation errors; `Capability.model_validate_json` round-trips the
  written file.
- Negative checks: undeclared `outcome_matched` code, an action missing
  from the guardrail allowlist, and non-contiguous locator ranks each
  raise `ValidationError` as intended.
- The `open_member_detail` step's settle timeout (12s) sits above the
  target app's injected ~4s slow load; its checkpoint is
  `any_of[record table visible, any recognized outcome]` so
  access-denied / supervisor-review are treated as answers, not hangs.

### Bugs found and fixed

- First draft only validated template tokens in `Step.value_template`;
  the example uses `{{search_field}}` inside a locator `name`. Extended
  the param-reference validator to scan locator `name` / `text` /
  `label` too.

### Committed

- `schema/DESIGN.md`
- `agent/models.py`, `agent/__init__.py`
- `schema/build_example.py`, `schema/example_artifact.json`
- `requirements.txt` (root) — `pydantic`

---

## 2026-09-09 — Verify example_artifact.json against the live target app

`schema/example_artifact.json` was hand-authored, not derived from a
real run, so every locator, checkpoint, and detection rule was checked
against `target_app` running locally (Chrome accessibility tree + curl).

### What matched (no change)

- `base_url` / port `5001`; entry route `/`.
- Search page: `textbox` name "Search term (member ID or last name)";
  `radio` roles with names "Member ID" / "Last name" (the `aria-label`
  governs the computed name — the browser a11y inspector displays the
  `value` attribute, but Playwright's name computation uses the
  `aria-label`); `button` name "Look Up".
- Results: `table` name "Member search results"; result `link` name
  "Open detail for <name>, member <id>" (artifact matches the "Open
  detail for" prefix, substring).
- Detail: `table` name "Member record for <name>"; `rowheader`s exactly
  "Member ID", "Full name", "Date of birth", "Address", "Savings
  balance". **`date_of_birth` and `address` are real record fields.**
  (Phone and Email are also rows but the capability does not extract
  them — a subset, not a mismatch.)
- `MEMBER_NOT_FOUND` wording: "No members matched that search." on the
  results page (container `role="status"`), "No such member." on a
  direct `/member/<bad id>` hit. Both detected by `text_present`.
- `ACCESS_DENIED`: `role="alert"` whose accessible name contains "not
  authorized", plus HTTP 403. Both detection signals fire.

### What did not match / was fragile — and the fix

- **`SUPERVISOR_REVIEW_REQUIRED` detection was fragile.** It relied
  solely on `aria_visible role=alertdialog name~="Supervisor review
  required"`, and the dialog's accessible name came only from
  `aria-labelledby` pointing at a `<td>`. Chrome's accessibility
  inspector did not surface that as a computed name, so the rule was
  engine-dependent. Fixed both ends:
  - `target_app/templates/interstitial.html` — added an explicit
    `aria-label="Supervisor review required"` on the `alertdialog`
    (alongside the existing `aria-labelledby`), so the name is
    unambiguous.
  - `schema/build_example.py` / `example_artifact.json` — broadened the
    detection rule to `any_of[ aria_visible role=alertdialog
    name~="Supervisor review required", text_present "Supervisor review
    required" ]`.

### Other changes

- **Renamed `target.app`** from `cornerstone-teller-console` to
  `acme-teller-console`. "Cornerstone" collides with Cornerstone
  Advisors (a real core-banking consulting firm) and several real
  credit unions; `acme-` is unambiguously fictional. (Scope was the
  artifact field; the demo app's visible "Cornerstone Savings" branding
  in the templates is unchanged and could be revisited separately.)
- `target_app/app.py` — the `__main__` block served port 5000 while the
  README, the artifact, and every run command use 5001. Aligned it to
  5001 so there is one answer.

### Verified after changes

`build_example.py` regenerates cleanly; `Capability.model_validate_json`
round-trips the file. Re-ran the full six-case sweep against the live
app: happy path by ID and last name, access-denied (403 + text),
not-found (both wordings), and the interstitial (now with a direct
`aria-label`) all behave as the artifact describes.

### Committed

- `target_app/templates/interstitial.html`, `target_app/app.py`
- `schema/build_example.py`, `schema/example_artifact.json`
- this BUILD_LOG entry

---

## 2026-09-09 — Swap visible "Cornerstone Savings" branding to "Acme Savings"

Replaced the on-screen "Cornerstone Savings" text (page titles, header, decorative sidebar) in `target_app/templates/base.html` with "Acme Savings", matching the `target.app` rename; no route names, IDs, class names, or accessibility role/name contract touched, and the README accessible-name contract is unaffected (only its line-1 heading updated for consistency).
- this BUILD_LOG entry

---

## 2026-09-09 — Discovery agent: the LLM observe/decide/act loop

### Tool vocabulary (decided first, in CLAUDE.md)

Added a "Discovery tool vocabulary" section to CLAUDE.md before writing
the loop: exactly five tools — `navigate`, `click`, `type`, `extract`,
`done` — with a one-line justification for each and an explicit
"considered and rejected" list (`scroll`, `hover`, `wait`/`sleep`,
`screenshot` as a tool, `go_back`, `assert`). The agent must call
exactly one tool per turn (`tool_choice {"type":"any"}`, parallel
disabled); no free-form text is parsed for intent. Every tool also
takes a required one-line `why`, so the step log records the model's
reason even though forced tool use suppresses preamble text.

### Built

- **`agent/perception.py`** — `Perception` wraps a Playwright page. The
  model sees only `locator("body").aria_snapshot()` (role + accessible
  name tree) — the same representation replay's locators are built
  from. `resolve(role, name)` is **exact** (`exact=True`) always;
  0 matches and >1 matches both raise `ResolutionError` with a
  model-readable hint. `extract()` resolves, then reads the value from
  the live DOM (never the model's claim) and walks from the value
  element to its label (`aria-labelledby` → row `rowheader` →
  `label[for]` → wrapping `<label>`). `KNOWN_ROLES` is the set the
  target's a11y contract actually exposes (verified live).
- **`agent/discovery_tools.py`** — the Anthropic tool schemas and
  `ToolExecutor`. Runs each call, records a `TrajectoryStep`, returns a
  `tool_result` string (new snapshot appended). Resolution/tool errors
  come back as `is_error` results the model can retry from.
  `done` calls `_validate_done`: every cited `output_name` must already
  be in `captured_outputs` from a successful `extract`, else it fails
  with "extract it first" — `done` can never accept a self-reported
  value. `navigate` refuses off-origin URLs.
- **`agent/trajectory.py`** — `Trajectory` / `TrajectoryStep` /
  `ResolvedLocator` / `ExtractionResult`, deliberately separate from
  `agent/models.py`. This is discovery's raw output; the (not-yet-built)
  compiler turns a Trajectory into a Capability. A `TrajectoryStep`
  carries the resolved locator (role, name, exact, match count, DOM
  tag), the action, and for extracts both the value and the label +
  `label_source`, so compiled locators/checkpoints can later key off
  the field label, not its literal value.
- **`agent/discovery.py`** — the loop. Launches headless Chromium,
  seeds the conversation with goal + first snapshot, then per turn:
  Messages API call → one `tool_use` → execute → append `tool_result`
  with the fresh snapshot. Stops on `done` (clean), max steps, or
  wall-clock timeout. Writes `trajectory.json`, `steps.jsonl`, and
  `screenshots/step_NN.png` under `evidence/runs/<run_id>/`.
- **`agent/discover.py`** — CLI. Loads `.env`, takes `--goal`,
  `--base-url`, `--entry`, `--model` (default `claude-sonnet-4-6`,
  confirmed available via the models endpoint), `--max-steps`,
  `--timeout`, `--headed`.
- **`agent/tests/test_correctness_guards.py`** — offline (no
  browser/API): `done` rejects an uncaptured name, accepts only
  captured ones, and `resolve` pins `exact=True` / fails on >1 match.
  3 passed.

### Live run

Real run against the live target app with the real
`ANTHROPIC_API_KEY`, model `claude-sonnet-4-6`.

- **run_id: `disc-20260909-182538`**
- Goal: "search for member M1001 and read their name and current
  savings balance."
- **outcome: completed**, 6 steps, **0 retries, 0 resolution errors**,
  ~19.3k input / ~530 output tokens total.
- Steps the model took (with its stated reason):
  1. `type` textbox "Search term (member ID or last name)" ← "M1001"
     — *enter the member ID to search for*
  2. `click` button "Look Up" — *submit the search*
  3. `click` link "Open detail for Alice Nguyen, member M1001" —
     *open the member detail page*. Note: on the results page a `cell`
     and the `link` inside it share this exact accessible name; the
     role filter + `exact=True` still resolved to 1 element.
  4. `extract` cell "Alice Nguyen" → `member_name` — label resolved to
     "Full name" via `rowheader_in_row`
  5. `extract` cell "$18,750.42" → `savings_balance` — label
     "Savings balance"
  6. `done` citing `[member_name, savings_balance]` — validated
     against the two captured slots
- The model skipped clicking the "Member ID" radio (it is checked by
  default and M1001 is a valid ID search either way) — a reasonable
  shortcut, and something the compiler/policy step will need to account
  for.
- No retries or resolution failures occurred on this run; the exact-
  match resolver and the `done` guard were exercised (step 3's
  name collision, step 6's validation) but not stressed by a failure.

### Committed

- `CLAUDE.md` — discovery tool vocabulary section
- `agent/perception.py`, `agent/discovery_tools.py`,
  `agent/trajectory.py`, `agent/discovery.py`, `agent/discover.py`,
  `agent/tests/`
- `requirements.txt` — anthropic, playwright, python-dotenv, pytest;
  `.env.example`
- `evidence/runs/disc-20260909-182538/` — trajectory, JSONL log,
  6 screenshots
- this BUILD_LOG entry
---

## 2026-09-09 — The compiler (`agent/compile.py`, `agent/compile_cli.py`)

### Built

The seam between discovery's raw trajectory and the artifact a caller
invokes. Two hard rules, both enforced in code:

1. **Policy is never derived from the trajectory.** A single green run
   only proves the happy path. `risk_class`, `expected_outcomes`,
   `escalation_policy`, denylist patterns, and any route allowlisted
   beyond what was visited all come from a hand-authored `PolicySpec`
   that the compiler merges in. Required `PolicySpec` fields have no
   default — a missing one is a `ValidationError` at construction, which
   the CLI surfaces as a clear failure. `compile_capability` also hard-
   fails on an empty `expected_outcomes` (rather than treating every
   non-happy page as a hard failure) and on a non-`completed` trajectory.

2. **No fabricated steps/locators/scopes.** Each `ok` `TrajectoryStep`
   (except `done`) becomes one `Step`, using the exact role+name that
   resolved live as the rank-1 locator. The one synthesized step is the
   leading `navigate` to `entry_path` — a recorded fact, not an
   invention (documented in the notes sidecar).

- `PolicySpec` / `InputBinding` models live in `compile.py`.
  `InputBinding` carries the literal a param stood in for during
  discovery (`search_term` ← `"M1001"`), since discovery only recorded a
  free-text goal.
- `agent/policies/member_lookup.py` — the hand-authored policy + input
  binding set for the Phase 3 trajectory. Reuses the three expected
  outcomes from `schema/example_artifact.json` (already verified against
  the live app). Its docstring and a `known_gaps` entry document the
  `search_field` gap in full.
- `CompileNotes` sidecar (`*.notes.md`) — every compile-time decision
  that is not a 1:1 copy of the trajectory: synthesized steps, locator
  generalizations, value parameterizations, trajectory gaps left
  unfilled, guardrail narrowing.

Mechanical compilation details:

- **Checkpoints** derive from the compiled sequence: step *i*'s
  checkpoint asserts step *i+1*'s rank-1 locator is visible; the last
  step's is `outputs_non_empty` over the extracted outputs. Every
  checkpoint is `any_of[…, outcome_matched]` so a recognized business
  outcome satisfies it instead of hanging.
- **Extract steps** compile their locator and checkpoint from the
  captured `label` ("Savings balance"), never the discovered value
  ("$18,750.42"). Rank-2 is a `text_label` on the same label (the label
  association was confirmed live via `label_source`).
- **Link name generalization**: the discovered link name "Open detail
  for Alice Nguyen, member M1001" carries member data not known before
  the step, so it is truncated to the stable prefix "Open detail for"
  with `exact=false`, `nth=0`.
- **`allowlist_routes`** narrowed to the paths actually visited
  (`/`, `/search`, `/member/*` — the `M1001` segment is replaced with
  `*` because it matches a discovered input value), not discovery's
  wide-open scope.

### Trajectory gaps recorded (not filled)

- **`search_field` radio** — discovery never clicked it ("Member ID" was
  pre-checked). Compiled capability has only a `search_term` input and
  searches by ID. Two-mode support needs a second discovery run or a
  hand-authored step; the compiler does neither automatically.
- **No `within` scope on extract locators** — discovery resolved value
  cells directly and never resolved the "Member record for …" table, so
  the compiler has no verified table name to scope to. Left page-wide;
  hardening needs a second discovery pass.

### Verified

- New `agent/tests/test_compile.py` (16 assertions, offline): happy-path
  step shape, parameterization, label-not-value extraction, link
  generalization, checkpoint chaining, route narrowing, policy taken
  verbatim, gap surfaced; and the failure paths — missing policy field,
  empty `expected_outcomes`, non-completed trajectory, label-less
  extract, unbound fill literal.
- Full suite: 16 passed.
- Ran the compiler against the real Phase 3 trajectory
  (`disc-20260909-182538`). Output:
  `evidence/compiled/member_lookup.capability.json` (6 steps, 1 input,
  3 outputs, 3 expected outcomes) + `.notes.md`. Round-trips through the
  `Capability` model.

### Compiled-vs-hand-authored (`schema/example_artifact.json`)

- **Agree**: two-layer split, `aria_role` rank-1 locators, label-based
  extraction, `any_of[element_visible, outcome_matched]` checkpoints,
  the three expected outcomes, the escalation policy, `read_only` /
  no-confirmation, the `/`, `/search`, `/member/*` allowlist, the
  denylist tripwires.
- **Differ, informatively**:
  - The example has a `choose_search_field` step + `search_field` enum
    input; the compiled artifact does not — the example is a human
    asserting both modes work, which a compiler may not do from one run.
  - The example's extract locators carry `within="table[name^='Member
    record for']"`; the compiled ones do not — discovery never resolved
    that table.
  - The example's non-terminal extract checkpoints assert the step's
    *own* rowheader; the compiled ones assert the *next* step's target
    (reaching step *i+1* is what proves step *i* landed).
  - Output names: the trajectory used `member_name`; the example uses
    `full_name`. The compiler preserves what discovery recorded.
  - The example's "submit search" checkpoint accepts only
    `MEMBER_NOT_FOUND`; the compiled one accepts any recognized outcome
    (uniform `outcome_matched` with no code).
  These differences are the REPORT determinism argument: everything the
  compiler emits is traceable to a line in the trajectory or a field in
  the PolicySpec, and everything it *can't* know shows up as a narrower
  artifact plus a notes entry — never as a plausible guess.

### Committed

- `agent/compile.py`, `agent/compile_cli.py`
- `agent/policies/__init__.py`, `agent/policies/member_lookup.py`
- `agent/tests/test_compile.py`
- `evidence/compiled/member_lookup.capability.json` + `.notes.md`
- this BUILD_LOG entry

---

## 2026-09-10 — Compiler: output name normalization

### Changed

Added `output_name_mapping` to `PolicySpec` (`agent/compile.py`): a dict
from the name a trajectory happened to capture an output under
(`"member_name"`) to the canonical contract name the policy author
intends (`"full_name"`). This is a normalization of a label the author
controls, not a fabrication — the compiler renames, it does not invent
data or claim anything the run didn't prove.

- The mapping is applied everywhere an output name surfaces: the
  `Capability.outputs` list, the `extract_*` step ids, the terminal
  `outputs_non_empty` checkpoint, and the compile-notes sidecar.
- The discovery `done`-citation check is carried across the rename:
  every output the run cited as its answer must still be a required
  output of the compiled capability, compared on canonical names.
- **Fail-clearly:** if the trajectory produced (captured or cited) an
  output name with no entry in `output_name_mapping`, compilation
  raises `CompileError` listing every missing name at once. An empty or
  partial mapping does not pass outputs through unmapped.
- `agent/policies/member_lookup.py` now declares
  `{"member_name": "full_name", "savings_balance": "savings_balance"}`.

### Verified

- Re-ran the compiler against `disc-20260909-182538`; overwrote
  `evidence/compiled/member_lookup.capability.json` and `.notes.md`.
  The artifact now uses `full_name` consistently (0 occurrences of
  `member_name`), matching `schema/example_artifact.json`'s output
  naming. The notes sidecar records the `member_name -> full_name`
  normalization.
- `agent/tests/test_compile.py` extended: mapping applied through
  outputs/ids/checkpoint, identity mapping still required, matches the
  example artifact's names, and the fail-clearly cases (unmapped name,
  empty mapping). Full suite: 21 passed.

### Committed

- `agent/compile.py`, `agent/compile_cli.py`
- `agent/policies/__init__.py`, `agent/policies/member_lookup.py`
- `agent/tests/test_compile.py`
- `evidence/compiled/member_lookup.capability.json` + `.notes.md`
- BUILD_LOG entries for Phase 4 (the compiler and this fix)
