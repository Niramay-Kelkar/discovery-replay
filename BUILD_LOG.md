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

---

## 2026-09-10 — Replay engine: deterministic execution of a compiled Capability

Phase 5. Takes a compiled `Capability` + input params and drives the
live target app through the recorded steps with no LLM anywhere in the
decision loop. Every choice is a lookup against the artifact.

### Built

- **`agent/replay.py`** — the engine.
  - **ACT → SETTLE → CHECK per step, with ACT structurally single-shot.**
    `_act` is a separate method called exactly once, *before* the
    settle/check retry loop is entered. Retries (automatic per
    `escalation_policy.max_retries_per_step`, or a future human-resumed
    run) re-enter only `_settle_and_check`. Re-firing a completed action
    — the way a replay double-submits a mutating step — is not possible
    to express, not just discouraged.
  - **Four-way result contract, as distinct types** (`Success`,
    `BusinessOutcome`, `HardFailure`, `PendingEscalation`), each a
    dataclass with a `status` discriminator and `to_dict()`. `Success`
    carries every declared output + `outcome_code="SUCCESS"`;
    `BusinessOutcome` the matched code/description/step;
    `HardFailure` the step, phase, trigger, expected vs observed (enough
    to debug without a stack trace); `PendingEscalation` the same debug
    fields plus the configured escalation action — returned cleanly when
    `escalation_policy` says *escalate* rather than *retry*, so the
    later human-handoff phase hooks in here without touching the loop.
  - **Locator resolution reuses `Perception.resolve`** — the same exact
    accessible-name matcher discovery uses. `resolve` gained an `exact`
    keyword (default `True`; discovery never passes it) so replay can
    honour the compiler's deliberately-generalised substring locators
    (`"Open detail for"` + `nth`). `text_label` / `css` / `xpath`
    fallbacks are normalised to the same one-visible-element-or-raise
    contract. No second resolver.
  - **Checkpoint evaluation** (`agent/checkpoints.py`) — a general
    evaluator with one handler per `CheckpointKind`, coverage asserted
    at import against `get_args(CheckpointKind)` so a new schema kind
    fails loudly instead of silently always-passing. Covers `any_of` /
    `all_of` composites and `outcome_matched` (which consults the
    outcome-detection verdict, doesn't re-implement it).
  - **Outcome detection** (`agent/outcome_detection.py`) — its own
    module, per CLAUDE.md. Evaluates the `DetectionRule` tree
    (`text_present` / `aria_visible` / `http_status` / `url_matches` /
    `any_of` / `all_of`); same import-time kind-coverage guard. Main-
    frame document status is captured off a Playwright `response`
    listener so `http_status` rules (ACCESS_DENIED = 403) work after a
    navigating click, not just a `goto`.
  - **Guardrails enforced at run time.** Action-type allowlist checked
    at preflight and per step; `navigate` target route and the landed
    URL after every navigate/click checked against
    `allowlist_routes` (glob) and `forbid_offdomain_navigation` — a
    violation hard-fails, it does not retry. `denylist_text_patterns`
    scanned on the live page after each settle and again before any
    mutating action.
  - **Preflight** refuses to run if `policy_authored_by` is unset
    (DESIGN §1) or a required input is missing/empty.
- **`agent/replay_cli.py`** — `--capability`, repeatable `--input
  NAME=VALUE`, `--base-url`, `--headed`, `--json`. Exit 0 for Success or
  BusinessOutcome (both are answers the caller asked for), 1 for
  HardFailure, 3 for PendingEscalation. Prints real output values to
  stdout; never writes them to a file.
- **`agent/perception.py`** — `read_paired_value()` added: from a
  resolved label element (the `rowheader` the compiler targets), step
  across to the value cell in the same row. Discovery reads the value
  element directly; replay lands on the label, so it needs the inverse
  walk. Kept in `perception.py` so both sides share one notion of the
  page.

### Evidence / redaction

Structured JSONL per run at `evidence/replays/<run_id>/replay.jsonl`
(step, phase, action, locator used, checkpoint trace, settle timing,
attempts, matched outcome). Extracted output values and input values are
**redacted** in the log (`"A…n (len 12)"`) — verified end-to-end: a grep
for every seeded member's name/ID/balance across `evidence/replays/`
comes back empty. The unredacted result object goes to the caller
in-process and to CLI stdout only; nothing writes it to disk.

### Verified — live runs against `target_app` on port 5001

- **Happy path** — `search_term=M1001` → **Success** in 6 steps,
  `full_name="Alice Nguyen"`, `savings_balance="$18,750.42"`,
  `outcome_code="SUCCESS"`. Evidence: `replay-20260910-071359`.
- **Business outcome** — `search_term=M1002` → **BusinessOutcome**
  `ACCESS_DENIED` at step `click_open_detail_for` in 4 steps (not a
  crash, not a hang; the `any_of[element_visible, outcome_matched]`
  checkpoint recognised the 403 + `role="alert"` page as a legitimate
  answer). Evidence: `replay-20260910-071403`.
- Spot-checked `search_term=M9999` → **BusinessOutcome**
  `MEMBER_NOT_FOUND` at `click_look_up`, exit 0.

### Tests

- `agent/tests/test_replay.py` (offline, no browser/API): checkpoint +
  detection kind coverage, `any_of` short-circuit on outcome match,
  `outputs_non_empty` default, redaction hides the value / keeps shape,
  route-glob matching rejects arbitrary paths, preflight refuses an
  unauthored policy and missing inputs, the four result types are
  distinct and serialisable.
- `agent/tests/test_correctness_guards.py` updated: `resolve` now
  asserts the `exact` param defaults to `True` and is threaded through,
  rather than string-matching the old signature.
- Full suite: 32 passed.

### Committed

- `agent/perception.py`, `agent/tests/test_correctness_guards.py`
- `agent/replay.py`, `agent/replay_cli.py`, `agent/checkpoints.py`,
  `agent/outcome_detection.py`, `agent/tests/test_replay.py`
- `evidence/replays/replay-20260910-071359`,
  `evidence/replays/replay-20260910-071403`
- this BUILD_LOG entry

---

## 2026-09-10 — Human-in-the-loop escalation and handoff (Section 3.6)

Phase 6. Replay can now detect a state it cannot classify, hand control
of the *same* live browser to a human operator, and resume the run when
the operator gives the go-ahead — re-entering SETTLE/CHECK for the
paused step only, never re-running its ACT.

### Step 1 — a genuinely unrecognized seeded state (`target_app`)

Every existing non-happy case (access-denied, not-found, the
supervisor-review interstitial) is a *declared* `expected_outcome` the
PolicySpec detects — none of them actually need to escalate. Added one
that does:

- `MAINTENANCE_HOLD_IDS = {"M1007"}` in `app.py` (same app-layer pattern
  as `SLOW_LOAD_IDS` / `INTERSTITIAL_IDS`; the DB row is ordinary).
  `/member/M1007` serves `maintenance_hold.html` — a `role="alertdialog"`
  named **"Account maintenance hold"** with wording and an aria-label no
  detection rule in the compiled capability matches. `?ack=yes` clears
  it to the real record (Dana Whitfield, $7,605.14).
- `seed.py` gains the M1007 row; `README.md` documents it in the seeded
  table and the accessibility contract, with a note drawing the line
  between recognized business outcomes and this unrecognized state.

Result: `on_unrecognized_dialog` is now actually reachable — replay sees
an `alertdialog`, outcome detection matches nothing, so it escalates.

### Step 2 — `agent/escalation.py`: `SessionStore`

SQLite (stdlib `sqlite3`, WAL, short-lived connections — two processes
touch it: replay and the console). One `escalations` table: `run_id`,
`capability_id`, `goal`, `step_id` / `step_ordinal`, `trigger` (the four
policy triggers, validated on insert), `phase`, `expected` / `observed`,
`screenshot_path`, `status` (`pending` → `resumed` | `timed_out`),
timestamps, `resumed_by`, `operator_note`, `handoff_deadline_at`.
`mark_resumed` is one-shot (only acts while `pending`); `mark_timed_out`
never clobbers a resume. DB lives under `evidence/sessions/` (git-ignored
operational state).

### Step 3 — wired into `agent/replay.py`

- On an `escalate` trigger with handoff enabled (the default),
  `_escalate` takes a screenshot into the run's evidence dir, opens a
  `SessionStore` entry, writes an `escalation_opened` evidence record,
  then **blocks** — polling the store every `poll_interval_s` up to
  `escalation_policy.human_handoff_timeout_seconds`. The Playwright
  browser stays open the whole time (the poll loop only touches SQLite),
  so it is literally the same session a person can drive.
- On `resumed`: writes a `human_intervention` evidence record (that a
  human intervened, who, when, how long paused, their note), then
  returns a `"resume"` signal. `_verify_step` re-runs `_settle_and_check`
  for the paused step and re-interprets — **ACT is not called**. If the
  checkpoint now passes (operator fixed the page), the run continues.
- On timeout: `mark_timed_out`, `escalation_timed_out` evidence, and a
  clean `HardFailure` (`<trigger>:handoff_timeout`) with how long it
  waited — no crash, no infinite loop.
- ACT-phase failures (`on_hard_failure`) escalate the same way; on
  resume, replay records that the operator completed the action and goes
  straight to SETTLE/CHECK (it cannot and does not re-fire ACT).
- `--cdp-port` on the CLI launches Chromium with a DevTools port so an
  operator can attach to *that* browser during a handoff.
  `handoff_enabled=False` (CLI `--no-handoff`) keeps the Phase-5
  behaviour of returning `PendingEscalation` immediately — used in CI.
- New CLI flags: `--no-handoff`, `--session-db`, `--handoff-timeout`,
  `--poll-interval`, `--cdp-port`.

### Step 4 — `agent/operator_console.py`

Minimal Flask app, port 5002, deliberately bare (Section 3.6 puts a full
co-browsing console out of scope — the operator is at the machine). What
is real:

- `GET /` lists pending escalations with goal / step / trigger /
  expected-vs-observed / screenshot, plus recent resolved history.
- `GET /screenshot/<run_id>/<name>` is scoped two ways: there must be an
  escalation for that `run_id` whose recorded screenshot is the file
  asked for, **and** the resolved path must sit inside that run's own
  `evidence/replays/<run_id>/` dir (no traversal, no cross-run reads).
- `POST /resume/<id>` records `resumed_by` + an optional note and flips
  the row to `resumed`, which unblocks the polling replay process. The
  human's action is recorded in the run's JSONL evidence, not just "a
  resume happened".

### Step 5 — live demonstration

Reseeded, started `target_app` (5001) and the operator console (5002),
then ran replay **headed** with `--cdp-port 9222 --handoff-timeout 240`:

    replay_cli --capability evidence/compiled/member_lookup.capability.json \
               --input search_term=M1007 --headed --cdp-port 9222

- Steps 1–3 ran normally. Step 4 (`click_open_detail_for`) landed on the
  "Account maintenance hold" `alertdialog`; SETTLE/CHECK: checkpoint
  false, outcome detection matched nothing, `alertdialog` present →
  trigger `on_unrecognized_dialog` → **escalate**. Replay wrote
  `escalation_opened` (escalation #1), screenshotted the page, and
  blocked polling.
- Console showed the pending escalation with the screenshot of the hold
  dialog and the expected/observed context.
- Acting as the operator: attached to the **same** Chromium over CDP
  (`connect_over_cdp("http://localhost:9222")`), found the exact tab
  replay was blocked on (`/member/M1007`), read the dialog, clicked
  **Dismiss** → the real record rendered. Then clicked **Resume** in the
  console (`operator=niramay`, with a note).
- Replay's next poll saw `resumed`, wrote `human_intervention`
  (`human_intervened: true`, operator + note + `paused_seconds`), and
  `resume` (`"ACT is not re-invoked"`). It re-ran SETTLE/CHECK for step 4
  only — the record table was now visible, checkpoint passed — then
  steps 5–6 extracted `full_name` / `savings_balance`. Final result:
  **Success**, `Dana Whitfield` / `$7,605.14`, `outcome_code=SUCCESS`,
  6 steps.
- Evidence: `evidence/replays/replay-20260910-080644/` (JSONL + the
  escalation screenshot). Extracted and input values are redacted in the
  log; a grep for the member's name / ID / balance across `evidence/`
  comes back empty. Not committed (git-ignored raw run evidence, per the
  existing convention).

Note on "visible": in this build environment a headed Chromium has no
real display, but the handoff is genuine — the operator attached to and
drove the *identical* browser process and tab replay was paused on, and
replay resumed on that mutated DOM without re-running ACT. On an operator
workstation the same `--headed` window is one a person clicks in
directly.

### Tests

- `agent/tests/test_escalation.py` (offline): `SessionStore` open/read,
  invalid-trigger rejection, one-shot resume, timeout-doesn't-clobber;
  operator console index, `POST /resume` flips the row, and the
  screenshot route is scoped to `run_id` (a different run_id → 404 for
  the same filename; traversal → 404).
- Full suite: 39 passed.

### Committed

- `target_app/app.py`, `target_app/seed.py`,
  `target_app/templates/maintenance_hold.html`, `target_app/README.md`
- `agent/escalation.py`, `agent/operator_console.py`,
  `agent/tests/test_escalation.py`, `requirements.txt` (flask)
- `agent/replay.py`, `agent/replay_cli.py`
- this BUILD_LOG entry

(Raw evidence for the live run stays under `evidence/replays/` and is
git-ignored — not committed.)

---

## 2026-09-10 — Closing the last-name search gap in `member_lookup`

### Why

Every prior compile of `member_lookup` came from `disc-20260909-182538`,
an ID lookup. "Member ID" is the pre-checked radio, so that run never
touched the search-field selector and never exercised a last-name
search. The compiled capability was honestly narrowed to ID-only, with
the gap recorded in the policy's `known_gaps` and the notes sidecar. The
capability was documented as incomplete rather than made complete.

### Step 1 — a real last-name discovery run

`python -m agent.discover` against the live `target_app` (port 5001),
real `ANTHROPIC_API_KEY`, model `claude-sonnet-4-6`. Goal phrased to
force the radio:

> In the member search screen, switch the search field to last name by
> clicking the 'Last name' radio button (do not use the default 'Member
> ID' option), then search for the member with last name Okafor, open
> their record, and read their full name and current savings balance.

`disc-20260910-084016`, 7 steps, outcome `completed`. Trajectory:
`click radio "Last name"` → `type "Okafor"` → `click "Look Up"` →
`click "Open detail for James Okafor, member M1004"` → `extract full_name`
→ `extract savings_balance` → `done`. Captured `full_name = "James
Okafor"`, `savings_balance = "$2,219.75"` (M1004, a seeded happy-path
member other than M1001).

### Step 2 — compiler fix so the last-name trajectory compiles honestly

`_generalize_name` (`agent/compile.py`) previously templated input
values into a resolved accessible name *before* checking whether an
extracted member value also appeared in it. On a last-name search the
search term is a substring of the extracted full name ("Okafor" inside
"James Okafor"). Templating first split the full name, so the
member-data truncation check no longer matched it, and the compiler
shipped `link name="Open detail for James {{search_term}}, member
M1004"` as an **exact** match — which resolves for nobody.

Fixed: the truncation-to-stable-prefix check now runs against the raw
resolved name first; input templating runs afterward on whatever prefix
survives. The "Open detail for …" link compiles to the `"Open detail
for"` substring locator (`exact=false`, `nth=0`) for any member, as it
already did on the ID path. Regression test added
(`test_last_name_inside_full_name_does_not_defeat_truncation`); the ID
trajectory's output is unchanged.

### Step 3 — recompiled `member_lookup` (v1.0.0 → v1.1.0)

`disc-20260910-084016` is now the canonical source — it covers the whole
flow, radio included. `agent/policies/member_lookup.py`:

- `search_field` InputBinding added (`discovered_value="Last name"`),
  declared as a closed enum input `["Member ID", "Last name"]` — the two
  accessible names the radio exposes. The compiler templates the
  discovered radio name to `{{search_field}}` and emits a
  `click_search_field` step: `role=radio name={{search_field}}`. Same
  shape as the hand-authored `schema/example_artifact.json`, but now
  mechanically derived from an observed element.
- `search_term` binding is now the last name `"Okafor"`.
- `output_name_mapping` is the identity map `{full_name, savings_balance}`
  (the run captured the contract names directly this time).
- `extra_allowlist_routes=["/member/*"]`: the last-name flow lands on
  `/member/M1004`, where `M1004` is the id the search returned, not an
  input value, so route narrowing leaves it literal. `/member/*` is
  added by hand, justified, so replay reaches whichever member it looks
  up.
- `known_gaps` entry rewritten from "not exercised" to `RESOLVED`, with
  the both-modes verification called out.

Overwrote `evidence/compiled/member_lookup.capability.json` and
`.notes.md`. The notes sidecar gets a "What changed in this recompile"
section covering all of the above.

### Step 4 — verified against the live app, both modes

Replayed the recompiled artifact (`--no-handoff`, live `target_app`):

| inputs | result | outputs |
|---|---|---|
| `search_field="Last name"`, `search_term="Okafor"` | Success | `James Okafor` / `$2,219.75` |
| `search_field="Member ID"`, `search_term="M1001"` | Success | `Alice Nguyen` / `$18,750.42` |

Only the first combination was ever literally observed during discovery.
The second — a Member-ID search — passes because the `search_field`
input drives the one compiled radio step and the `"Open detail for"`
locator generalizes across members. That is the proof the capability
generalizes, not just a claim: the ID path is now confirmed by
execution, and the last-name path exists at all, which it did not
before. Redaction still holds — a grep for the member names / ids /
balances across `evidence/replays/` comes back empty.

### Tests

`agent/tests/test_compile.py` rewritten onto `disc-20260910-084016` and
the two-mode capability: the `click_search_field` step, the
`search_field` enum input, the substring-truncation regression, routes
including `/member/*`, and the `known_gaps` "RESOLVED" record. Full
suite: 42 passed.

### Committed

- `agent/compile.py` (the `_generalize_name` ordering fix)
- `agent/policies/member_lookup.py`, `agent/compile_cli.py` (doc example)
- `evidence/compiled/member_lookup.capability.json` + `.notes.md`
- `agent/tests/test_compile.py`
- this BUILD_LOG entry

(Raw evidence for `disc-20260910-084016` and the two replays stays under
`evidence/runs/` and `evidence/replays/` and is git-ignored — not
committed.)

---

## 2026-09-10 — Verification runbook (`VERIFICATION.md`)

Added `VERIFICATION.md` at the repo root: a manual verification runbook
covering every seeded `target_app` scenario with the exact commands,
each one actually run and confirmed before being written down. Nine
sections — start the app; replay happy-path by Member ID and by Last
name; the three recognized business outcomes (access-denied, not-found,
supervisor-review); the slow-load case; the full M1007 escalation /
operator-console / CDP-handoff / resume sequence; and a discovery ->
compile -> replay end-to-end. Each step documents the correct result
and what a wrong result looks like, so it is useful for spotting a
regression, not just confirming green.

Standalone reference for now; the demo-path parts get folded into
`README.md` in a later pass. Not touching `README.md` / `REPORT.md`
yet.

### Bug surfaced and fixed while writing it

The slow-load scenario (M1003, `/member/<id>` sleeps ~4s) hard-failed:
`_act`'s `click` used a fixed 4000ms Playwright timeout, and a click
that triggers navigation is held until that navigation settles, so the
injected delay tripped ACT before SETTLE ever ran. The compiler already
widens a navigating click's settle bound to 12s for exactly this
reason; `_act` now honours `step.settle.max_wait_seconds` for the click
timeout (floored at 4s) instead of ignoring it. M1003 now replays to
Success in ~8s; the instant pages (M1001/M1002/M1006/M1007) are
unaffected. Full suite still 42 passed.

### Committed

- `agent/replay.py` (click honours the step's settle bound)
- `VERIFICATION.md`, this BUILD_LOG entry

(The discovery/replay/escalation runs done to verify the runbook stay
under `evidence/runs/`, `evidence/replays/`, `evidence/sessions/` and
are git-ignored — not committed.)

---

## 2026-09-10 — README Setup and Demo path

Filled in the two `README.md` placeholders. Setup: Python 3.11+ / venv /
`pip install -r requirements.txt` / `playwright install chromium`,
`.env.example` -> `.env` for `ANTHROPIC_API_KEY`, and starting
`target_app` on 5001. Demo path: a single copy-paste-in-order
discover -> compile -> replay for `member_lookup`, ending in a Success
with James Okafor's record; a one-line note that the same artifact also
replays by member ID; and a link to `VERIFICATION.md` as the full
scenario-by-scenario runbook (business outcomes, slow load, escalation).
Commands were pulled from `VERIFICATION.md` and re-run verbatim against
the current codebase before finalizing.

Also tightened the pre-code "How it works" section where it had drifted
from the build: discovery observes the page (not "the screen"); the
runtime conditions replay reports are the ones actually seeded (slow
pages, not-found, denials, review gates — not "validation errors"); and
escalation now describes the real mechanism — pause, hand the *same*
live browser session to an operator via the console, resume by
re-checking the paused step without re-running its action, or fail
cleanly on timeout.

No "Project structure" section exists yet, so the VERIFICATION.md
pointer lives in the Demo path for now. `REPORT.md` untouched.

### Committed

- `README.md`, this BUILD_LOG entry

---

## 2026-09-10 — Curated evidence/ for submission

Assembled the deliberate evidence set the brief's Section 6.3 asks for,
copied out of the git-ignored raw trees (`evidence/runs/`,
`evidence/replays/`, `evidence/sessions/`) into two committed
directories rather than un-ignoring the raw output.

- `evidence/discovery/disc-20260910-084016/` — the last-name discovery
  run (`trajectory.json`, `steps.jsonl`, 7 screenshots). Chosen over a
  member-ID run because it forces the `Last name` radio click and its
  `extract` steps exercise the value→label walk the compiler depends
  on.
- `evidence/replay/01-success/`, `02-business-outcome-access-denied/`,
  `03-escalation-handoff/` — one replay run per outcome class. `03` is
  the M1007 unrecognized-dialog run with a real operator handoff on the
  same live session (`replay.jsonl`, the escalation screenshot, and
  `escalation_record.json` exported from the escalation store).
- `evidence/compiled/` unchanged (already tracked).
- `evidence/README.md` — indexes the set, gives the reading order, and
  maps each piece to Section 6.3.

Sanitization pass before commit (documented in full in
`evidence/README.md`): absolute `screenshot` paths in the discovery and
escalation logs were made repo-relative and repointed at the curated
screenshot locations; grep over the curated set for `/Users/`, `/home/`,
`C:\`, the machine username, the hostname, `.local`, `sk-ant-`,
`ANTHROPIC_API_KEY`, and 25+ char token-like strings all came back
empty. Replay-log redaction (masked, length-bearing extracted values)
verified to hold on all three replay runs. Screenshots confirmed to be
page-only Playwright captures with no OS/browser chrome. The discovery
trajectory keeps its captured sample values, matching the already-
committed `compiled/…notes.md`; nothing in the set derives from `.env`.

### Committed

- `evidence/discovery/`, `evidence/replay/`, `evidence/README.md`, this
  BUILD_LOG entry
