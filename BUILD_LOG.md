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

---

## 2026-09-10 — REPORT.md Architecture section

Wrote `## 1. Architecture` in `REPORT.md` (was an empty placeholder): the
five-process layout, a Mermaid flowchart of the artifact/file handoffs
and the one live coupling (replay <-> operator console over the shared
SQLite file), the four load-bearing decisions (mechanical/policy split,
shared perception + exact-name resolution, one-shot ACT, synchronous
SQLite-poll escalation), what is deliberately not built, and the
language/framework choices.

Checked the diagram against `agent/discovery.py`, `agent/compile.py`,
`agent/replay.py`, `agent/escalation.py`, `agent/operator_console.py`:
process ports, browser ownership, file paths, the poll-not-push
escalation loop, and the console touching only the DB all hold. Known
simplifications left in at the diagram's altitude: the operator's
"CDP attach :9222" edge points at target_app, but the attach is really
to the Chromium `agent.replay` owns (which is displaying target_app) and
only when replay was started with `--cdp-port`; :9222 is the port used
in `VERIFICATION.md`, not a default (`cdp_port` defaults to `None`); and
the compiler also emits a `*.notes.md` sidecar next to `capability.json`
that the diagram doesn't show. None of these are wrong, they are the
same abstraction level as the discover/replay -> target_app edges.

### Committed

- `REPORT.md`, this BUILD_LOG entry

---

## 2026-09-10 — Enforce requires_confirmation in replay

`Capability.requires_confirmation` was declared and set per capability
but nothing in the replay decision path read it: an artifact with
`requires_confirmation: true` would have run unattended with no gate —
a real hole in the "handle the risky class conservatively" requirement.

### Changed

- `agent/replay.py`: `Replayer` takes a new `confirmed: bool = False`.
  `_preflight` now hard-fails with trigger `confirmation_required`
  (phase `preflight`, no browser launched) when the artifact declares
  `requires_confirmation` and `confirmed` is not set. Message names the
  capability. Ordered after the `policy_unauthored` check and before
  input/guardrail checks.
- `agent/replay_cli.py`: new `--confirmed` flag (default False), threaded
  into `Replayer`.
- `agent/tests/test_replay.py`: three cases — a `requires_confirmation`
  capability is rejected without `--confirmed`, accepted with it, and a
  `requires_confirmation=False` capability (member_lookup) is unaffected
  either way.

### Verified

Full suite: 44 passed (was 41). `member_lookup` has
`requires_confirmation: false`, so its replay path is unchanged.

### Committed

- `agent/replay.py`, `agent/replay_cli.py`, `agent/tests/test_replay.py`,
  this BUILD_LOG entry

---

## 2026-09-10 — Correct schema/DESIGN.md drift from the v1.1.0 artifact

`schema/DESIGN.md` still described the schema as it stood when first
written; several concrete examples no longer matched `agent/models.py`
or `evidence/compiled/member_lookup.capability.json`.

### Changed (`schema/DESIGN.md`)

- §4 member-lookup inputs/outputs: was `search_field` (`member_id` |
  `last_name`) and outputs `member_id, full_name, savings_balance,
  date_of_birth, address, outcome_code`. Now the real set: `search_field`
  with `allowed_values` `["Member ID", "Last name"]`, `search_term`;
  outputs `full_name`, `savings_balance`, and `outcome_code`
  (`required: false`, appended by the compiler).
- §4 `InputParam` list now mentions `allowed_values`; `OutputParam` note
  clarifies `outcome_code` is compiler-appended, not an `extract` target.
- §8 top-level tree: `version` `1.0.0` → `1.1.0`; `target.app`
  `cornerstone-teller-console` → `acme-teller-console`.
- §2 locator example: replaced the invented
  `name="Open detail for {{member_name}}, member {{member_id}}"` with the
  artifact's real strategies (`role=rowheader name="Savings balance"`,
  and the `"Open detail for"` substring match with `exact=false, nth=0`).
- §3 `any_of` example aligned to the compiled checkpoint
  (`role=rowheader name="Full name"`).
- §5 `requires_confirmation`: corrected the "defaults true for mutating"
  claim (the field defaults to `false`) and documented that replay now
  enforces it via `--confirmed` (see the entry above).
- §7 `allowlist_routes` example now includes `/member/M1004`, which the
  compiler pins from the discovery trajectory.

`schema_version` stays `"1.0"` (unchanged, matches the `Literal` in
`models.py`). Left `schema/example_artifact.json` as-is: it is a separate
hand-authored schema sample, still validates, and is not the compiled
artifact DESIGN.md is being reconciled against.

### Verified

Re-read DESIGN.md against `member_lookup.capability.json`: app, version,
schema_version, both inputs (+ allowed_values), all three outputs,
allowlist routes, action types, and the three expected-outcome codes and
their detection rules all agree. Full suite: 44 passed.

### Committed

- `schema/DESIGN.md`, this BUILD_LOG entry

---

## 2026-09-10 — Write REPORT.md §2 "Artifact schema"

Section 2 of REPORT.md was an empty stub. Filled it in with the schema
write-up: the mechanical/policy split and its enforcement, the ranked
locator list, why every checkpoint is `any_of[element_visible,
outcome_matched]`, typed named inputs/outputs and template validation,
guardrails and `requires_confirmation`, and the `schema_version` vs
`version` distinction.

### Changed (`REPORT.md`)

- `## 2. Artifact schema`: six paragraphs plus a Mermaid flowchart
  (Trajectory + PolicySpec → `compile_capability()` → Capability, and
  the per-step ACT → SETTLE → CHECK path with ranked-locator fallback).
  Diagram placed after the opening paragraph, before the locators
  paragraph.

### Verified

Claims checked against `agent/models.py`, `agent/compile.py`,
`agent/replay.py`, `agent/checkpoints.py`, `agent/outcome_detection.py`:
`extra="forbid"` on `Capability`, `extra="allow"` on `LocatorStrategy`;
`_Resolver` handles `aria_role`/`text_label`/`css`/`xpath` but not
`test_id`; `detect_outcome` walks `expected_outcomes` in order;
`ACCESS_DENIED` detection is `http_status 403`; `human_handoff_timeout`
default 900s; `_steps_reference_declared_params` validates `{{token}}`
references; `output_name_mapping` rejects unmapped outputs;
`Guardrails.allowlist_routes` is required; `_preflight` enforces
`policy_authored_by` and `requires_confirmation` + `--confirmed`;
`schema_version` is a hard `Literal`; nothing reads `version`.

One wording caveat left in as written: the prose says a compiler-only
artifact has `policy_authored_by` empty, matching DESIGN.md §1's "emits
empty policy stubs". The current `compile.py` actually requires
`PolicySpec.policy_authored_by` and always sets it, so today the empty
state only arises on a hand-edited or partial artifact. The replay-side
refusal (`_preflight`) is real either way.

### Committed

- `REPORT.md`, this BUILD_LOG entry

---

## 2026-09-10 — Align schema/example_artifact.json with the v1.1.0 artifact

The previous DESIGN.md reconciliation deliberately left
`schema/example_artifact.json` untouched. That left the hand-authored
sample describing an older shape: `version` `1.0.0`, and five extract
outputs (`member_id`, `full_name`, `savings_balance`, `date_of_birth`,
`address`). The real capability returns two, plus the compiler-appended
`outcome_code`. Brought the sample in line so it illustrates the schema
as it actually stands.

### Changed (`schema/example_artifact.json`)

- `version` `1.0.0` → `1.1.0`.
- outputs: dropped `member_id`, `date_of_birth`, `address`; kept
  `full_name`, `savings_balance`, `outcome_code`.
- removed the three corresponding `extract` steps; renumbered the
  remaining steps to a contiguous 1..7.
- terminal `outputs_non_empty` checkpoint list narrowed to
  `["full_name", "savings_balance"]`.
- `inputs` (`search_field` / `search_term`) and `target.app`
  (`acme-teller-console`) already matched; left as-is.

The `discovery` block stays as its own illustrative run (`M1001`, ID
lookup) — the sample is not the compiled artifact and does not need to
share its provenance.

### Verified

`Capability.model_validate(json.load(...))` passes: `capability_id`
`member_lookup`, `version` `1.1.0`, outputs `['full_name',
'savings_balance', 'outcome_code']`, step ordinals `[1..7]`, exactly one
`outputs_non_empty` terminal step. Full suite: 44 passed.

### Committed

- `schema/example_artifact.json`, this BUILD_LOG entry

---

## 2026-09-10 — REPORT.md §2 precision corrections

Two wording fixes in the Artifact schema section caught on a self-review pass against the code (not new findings): `policy_authored_by` phrased so it no longer implies `compile.py` emits an artifact with it unset, and the empty-allowlist hard-fail described as "before any navigation or interaction" rather than "before touching the browser" (the browser is already launched inside `_drive` at that point). Committed: `REPORT.md`, this entry.

---

## 2026-09-10 — REPORT.md §3 written (Determinism & error handling)

Filled in the previously empty §3. Content built from a read of the
actual code, not from memory: `agent/replay.py` (the ACT/SETTLE/CHECK
loop, `_verify_step`'s retry loop, `_dispatch_trigger`, `_escalate`),
`agent/checkpoints.py`, `agent/outcome_detection.py`,
`agent/escalation.py`, and the real configured values in
`evidence/compiled/member_lookup.capability.json`.

Covers: why replay's decision path is deterministic (no model, no RNG)
and the honest short list of what still varies run to run (timing, whether
a retry fires, the network-idle vs. DOM-stable settle branch), plus the
one latent non-determinism — the `nth=0` prefix match on the detail-page
link, safe only as long as the target app's result ordering stays
stable. A Mermaid flowchart of one step's ACT → SETTLE/CHECK →
retry/escalate/result paths. The retry rules (only `on_step_timeout`
retries; three evaluations, flat 1.5s backoff; ACT is never re-invoked).
The four-way result contract mapped to what the caller receives. The
M1003 slow-load bug as the worked example of the expected-vs-observed
design surfacing a real gap. UI drift stated plainly as not built, only
contained. The layered timeout model with the real per-step settle
bounds (2–12s) and the 900s handoff window.

### Checked against code before committing

- Retry loop (`_verify_step`, replay.py): first check, then `while
  attempts <= max_retries_per_step` (2) with a `time.sleep(1.5)` each
  pass → 3 evaluations, 2 backoffs. Matches the prose.
- `_dispatch_trigger` converts a `"retry"` policy action to `"escalate"`
  for every trigger except the `on_step_timeout` path handled in
  `_verify_step`'s loop — so "everything else converts straight to
  escalation" holds.
- `_trigger_for`: settle-timeout → `on_step_timeout`; else unrecognized
  dialog → `on_unrecognized_dialog`; else `on_checkpoint_failure`.
- `human_handoff_timeout_seconds` is 900.0 in the capability JSON;
  per-locator `wait_for("attached", timeout=4000)`; per-step settle
  bounds in the JSON are 5/12/2/12/12/2/2.
- Success outputs returned in-process and deliberately not written to
  the JSONL (`_finish` comment).

One imprecision left in deliberately, noted here so it's on record: the
diagram's "declared outcome matched → BusinessOutcome" edge omits the
`classification != "business_outcome"` case, which is a `HardFailure`
(`known_hard_failure_outcome`). Every outcome declared in the current
`member_lookup` artifact is `business_outcome`, so the edge is accurate
for this capability; the diagram is illustrative of one step's control
flow, not an exhaustive state enumeration. Same spirit for "fixed
per-action timeouts" — the click action's timeout is `max(4000,
settle_bound_ms)`, i.e. derived from the step's settle bound, not a
literal constant like `fill`'s 4000ms.

### Committed

- `REPORT.md`, this entry

---

## 2026-09-10 — REPORT.md §3 precision corrections

Two wording fixes in the Determinism & error handling section caught on a self-review pass against the code (not new findings): added the diagram edge for a matched outcome classified `hard_failure` routing to `HardFailure` (`known_hard_failure_outcome`, replay.py:817), so the chart is generally correct rather than only accidentally correct for `member_lookup`'s all-`business_outcome` outcomes; and reworded "fixed per-action timeouts" to spell out that a navigating click's timeout is `max(4s, step settle bound)`, not a uniform constant. Committed: `REPORT.md`, this entry.

---

## 2026-09-10 — REPORT.md §4 Heterogeneity & multi-tenant

Wrote the section, which was an empty placeholder. Design-only per the
brief's scope, so the prose is explicit throughout about what's built
versus what's a credible extension. A Mermaid flowchart sits between the
opening seam paragraph and the frameset discussion, showing the one
thing that's genuinely built today (open-string locator `kind`,
`aria_role` role+name) branching into the two extension paths — a
frameset/iframe web app (additive frame coordinate, wiring up `within`)
and a native desktop app (role/name onto an OS accessibility API,
`navigate` becoming launch-and-activate).

### Checked against code before committing

- Seam: no Surface protocol exists; `perception.py:22` and
  `replay.py:58` import Playwright `Page`/`Locator` directly. Locator
  `kind` is `str` not an enum (`models.py:89`) and `LocatorStrategy`
  is `extra="allow"` (`models.py:87`) — a new strategy validates with
  no schema change.
- Frameset: `Perception.snapshot()` is `page.locator("body")
  .aria_snapshot()` (`perception.py:71-73`), main-frame only;
  `Perception.resolve` / `_Resolver` use `page.get_by_role` scoped to
  the main frame. The target app's iframe is `src="about:blank"`
  (`target_app/templates/base.html:72-74`) so nothing is lost today,
  but real frame content would resolve to zero matches.
- `within` is declared on `LocatorStrategy` (`models.py:104`) and read
  by nothing (`grep` in `agent/` finds only doc-string mentions).
- `base_url` override: `--base-url` in `replay_cli.py:50`, applied at
  `replay.py:295` as `(base_url or capability.target.base_url)` — CLI
  value wins, no recompile.
- Structural locator reserved-not-emitted: `models.py` docstring lists
  `css`/`xpath` as "reserved, last resort"; `compile.py` only ever
  emits rank-1 `aria_role` and (for extracts) rank-2 `text_label`
  (`compile.py:365-378`, `442-446`).
- Drift-detection hook: `snapshot_sha1` is written at
  `discovery_tools.py:361` and read by nothing.
- CDP handoff primitive: `--cdp-port` in `replay_cli.py:69-71`.

### Wording-precision fixes made to the supplied draft (not claim changes)

- "carry over with only additive field changes" → "mostly additive":
  a desktop port also has to widen `Literal` members
  (`ResolvedLocator.strategy`, `ActionType`), which isn't purely
  additive.
- "a structural locator strategy is reserved in the schema as a third
  fallback rank" → "a structural locator strategy — css or xpath — is
  reserved in the schema as a last-resort fallback": the schema
  reserves the `css`/`xpath` kinds, it doesn't assign them a rank
  (ranks are per-step, assigned 1..N by the compiler).
- "diffing that fingerprint per tenant" → "comparing that fingerprint
  across tenants": `snapshot_sha1` is a hash, compared for equality,
  not diffed.

### Committed

- `REPORT.md`, this entry

## 2026-09-10 — REPORT.md §5 Escalation & handoff

Wrote the section, which was an empty placeholder. Covers the four
triggers and which one retries first, what an escalation carries (and
what it doesn't — flat snapshot, no locator-attempt trace, no a11y
capture), the same-live-session claim and the two ways an operator
reaches the browser, resume never re-invoking ACT, the timeout path, why
the console is deliberately bare, and the honest production gaps. A
Mermaid sequence diagram sits after the second paragraph, showing the
process boundaries — replay, the browser it owns, the shared SQLite
store, the console, the human — and the poll-then-resume flow.

### Checked against code before committing

- Retry split: only `on_step_timeout` retries, gated in `_verify_step`
  on both `_trigger_for(report) == "on_step_timeout"` and
  `pol.on_step_timeout == "retry"` (`replay.py:780-789`); the compiled
  artifact sets `max_retries_per_step=2`
  (`policies/member_lookup.py:187`), so up to two settle/check redos.
  `_dispatch_trigger` converts a leftover `"retry"` to `"escalate"`
  (`replay.py:866-868`).
- Escalation context: `store.open_escalation(...)` at
  `replay.py:905-911` — goal from `self.cap.discovery.goal`,
  `expected`/`observed` are the inline strings, `screenshot_path` from
  `perception.screenshot` (`replay.py:898-900`). Store schema
  `escalation.py:33-53`; no locator-trace or a11y column.
- Same session: `browser` launched at `replay.py:346`, closed only in
  `run()`'s `finally` (`replay.py:362-363`); `_escalate`'s poll loop
  (`replay.py:921-935`) touches only SQLite + `time.sleep`.
- Two reach mechanisms: `--headed` → `headless=False`
  (`replay.py:341`); `--cdp-port` → `--remote-debugging-port` arg
  (`replay.py:342-345`). Neither configured → escalation opens and
  polls but no human channel.
- Resume never re-runs ACT: `_act` called once at `replay.py:440`; the
  resume paths in `_verify_step` (`replay.py:837-842`) and the
  ACT-catch branch (`replay.py:452-467`) call `_settle_and_check` only.
  Bounded at `resumes >= 5` → `HardFailure`
  `checkpoint_failure_after_resume` (`replay.py:843-852`).
- Timeout: `store.mark_timed_out` is `WHERE id=? AND status='pending'`
  (`escalation.py:120-126`); `_escalate` returns a `HardFailure` naming
  `waited`/`limit` (`replay.py:943-950`); browser then closed by
  `run()`.
- Console: `GET /`, scoped `GET /screenshot/...`, `POST /resume/<id>`
  only (`operator_console.py:55-93`); no auth, default host
  `127.0.0.1` (`operator_console.py:173`); index is static, no refresh.
- Poll interval: `poll_interval_s` defaults to `2.0` (`replay.py:292`),
  matching the diagram's "poll every 2s".

### Wording-precision fixes made to the supplied draft

None. Every claim checked out against the code as written.

### Committed

- `REPORT.md`, this entry

---

## 2026-09-10 — REPORT.md §6 Safety

Wrote the section, which was an empty placeholder. Covers the four
guardrail enforcement points in a table, why none of them retry, how
risk_class / requires_confirmation / denylist_text_patterns together
draw the risky-vs-reversible line the brief asks for, an honest
statement of what's proven live for member_lookup versus what's only
proven by a synthetic test fixture, the two-places-redacted /
two-places-not-redacted shape of redaction with the reasoning for each,
how the API key is actually kept out of the repo, and the structural
limit that nothing validates a hand-authored policy's own correctness.
No diagram, per the request.

### Checked against code before committing

- Four enforcement points: `_preflight` (`replay.py:369-413`) —
  `policy_unauthored`, `confirmation_required`, `missing_input`,
  aggregate `guardrail_action_type` over all steps, all before
  `sync_playwright()` opens a browser at `replay.py:340`. `_guard_step`
  (`replay.py:676-697`) — per-step action-type recheck plus, for
  `navigate` only, `_route_allowed` against `allowlist_routes`.
  `_post_nav_guard` (`replay.py:705-723`) — called only for `navigate`/
  `click` steps (`replay.py:476-478`), checks `forbid_offdomain_navigation`
  and the *landed* path, not just the declared target. `_denylist_hit`
  (`replay.py:725-736`) — called after every step regardless of action
  (`replay.py:484`) and again before any action in `_MUTATING_ACTIONS`
  fires, inside `_act` (`replay.py:532-538`).
- None retry: all four return `HardFailure` (or raise into one) before
  `_verify_step`/`_dispatch_trigger` — the retry/escalation machinery —
  is ever reached for that step.
- risk_class's only code effect: `compile.py:694-698`, the
  `requires_confirmation` default when a `PolicySpec` leaves it `None`.
  No other file branches on `risk_class` (checked `agent/*.py`).
  `requires_confirmation` itself enforced at `replay.py:379-389`.
  `denylist_text_patterns` reasoning ("read-only... on the wrong page")
  is the actual docstring text in `agent/policies/member_lookup.py`.
- member_lookup: `risk_class="read_only"`, `requires_confirmation=False`
  explicit (`policies/member_lookup.py:183-184`), never exercises the
  `requires_confirmation=True` path live. That gate is only proven by
  `test_preflight_gates_a_capability_that_requires_confirmation`
  (`tests/test_replay.py:135-148`), which does
  `_cap().model_copy(update={"requires_confirmation": True})` on the
  real compiled artifact — a synthetic fixture, not a real mutating
  capability run end to end.
- Redaction: `redact()` (`replay.py:183-195`) applied at `run_start`
  inputs (`replay.py:337`) and the `extract` evidence write
  (`replay.py:576`); explicitly *not* applied to the `ReplayResult`
  returned to the caller (`replay.py:962-965`, comment states this
  intentionally) or to discovery's `trajectory.json`/`steps.jsonl`
  (`discovery.py:106-123`), confirmed unredacted by
  `evidence/README.md`'s note that the trajectory "carries the sample
  values it read... exactly as `compiled/member_lookup.capability.notes.md`
  already does." Evidence-curation grep sweep and per-run redaction
  check both documented in the "Curated evidence/ for submission"
  BUILD_LOG entry above.
- Secrets: `.env` loaded only by `agent/discover.py:32`
  (`load_dotenv`); `git log --all --full-history -- .env` returns
  nothing — never committed. Only `.env.example` (blank key) was ever
  committed, in `7c7ccfd`. `.gitignore:151` lists `.env`. No `print()`
  in `discover.py`/`replay_cli.py` outputs the key value, only the
  missing-key warning at `discover.py:47`.
- Policy validation gap: no `model_validator` on `Capability`
  (`models.py:448-489`) cross-checks `risk_class` against the steps'
  actual actions, and `_route_allowed` (`replay.py:699-703`) is a plain
  `fnmatch` with no notion of "too permissive." `policy_authored_by` is
  a required field on `PolicySpec` (`compile.py:166`, no default) and
  `_preflight` refuses to run an artifact where it's empty
  (`replay.py:370-378`) — attribution enforced, correctness not.

### Wording-precision fixes made to the supplied draft

- The `_post_nav_guard` table row said "after acting" without
  qualification; narrowed to "after a navigate/click step acts," since
  the guard is only invoked for those two action types
  (`replay.py:476-478`), not every step.

### Committed

- `REPORT.md`, this entry

---

## 2026-09-10 — Write REPORT.md §7 Cuts (final section)

Wrote `## 7. Cuts`, the last of REPORT.md's seven sections, closing out
the report. Built from a full re-read of `BUILD_LOG.md` from the
beginning (not from memory) plus a fresh grep pass against the current
code, so every gap named is re-verified as still true today, not just
carried forward from when it was first logged.

Covers: twenty-one gaps compiled across the project's history, split
into real cuts within the brief's own ask (thin/stubbed rather than
absent — `within`-scoped extraction, the `test_id`/`css`/`xpath`
fallback strategies, the unused `snapshot_sha1` fingerprint, the
synthetically-proven `requires_confirmation` gate, thin escalation
context, no console auth/notification, no policy-correctness
validation, the `nth=0` latent non-determinism) versus what's
genuinely out of scope per the brief itself (full co-browsing console,
multi-tenant build, desktop port, unattempted stretch goals); a
"what's next" list prioritized by which gap most directly strengthens
the weakest part of the system rather than by ease of build; and the
last-name search gap cited as the project's own precedent that this
cycle — honestly narrow, then genuinely close later — already worked
once on this codebase.

### Checked against code before committing

- `test_id`: `agent/models.py:80` docstring only, no resolver branch
  anywhere in `agent/replay.py`.
- `css`/`xpath`: resolver handles both (`replay.py:260`) but
  `agent/compile.py` never constructs a locator of either kind —
  reserved and resolvable, never emitted.
- `within`: declared on `LocatorStrategy` (`models.py:104`); grep across
  `agent/` finds no code path that reads `loc.within` — inert, matching
  the prior Heterogeneity-section finding.
- `snapshot_sha1`: written at `discovery_tools.py:361`, read by nothing.
- `requires_confirmation`: only exercised by the synthetic fixture at
  `tests/test_replay.py:135-148`, as already established in the Safety
  section's own verification.
- Operator console: `grep -n "auth\|password\|token"` and
  `grep -n "notify\|notification\|email\|sms\|slack"` across
  `agent/operator_console.py` both return nothing.
- `risk_class`: only code branch is `compile.py:697`
  (`requires_confirmation` default); no policy-correctness check
  exists anywhere in `agent/*.py`.

### Full-report consistency pass

Read all seven sections together after this insertion, specifically
checking for contradicting numbers, terminology, or claims between
sections written in separate passes. None found: the locator-strategy
claims (§2, §4, §7), the unused-fingerprint claims (§3, §4, §7), the
escalation-context claims (§5, §7), and the `requires_confirmation`
proof-status claims (§6, §7) all agree, several nearly verbatim,
across sections written on different days. No wording changes were
needed to the supplied section text — every checkable claim in it
(code and BUILD_LOG both) already held before it was committed. Two
citations to the assignment PDF's own section numbers (3.6, 3.7,
Section 8) are outside what this session can verify directly (the PDF
itself isn't in the repo or session context); they're carried as
written, consistent with how §5 and §6 already cite the same brief
sections elsewhere in the report.

### Committed

- `REPORT.md`, this entry

---

## 2026-09-11 — Agent-facing capability interface (`agent/capability_api.py`)

### Built

Section 8 stretch goal: a thin HTTP layer over the existing replay
engine so an AI agent can discover capabilities by name and invoke them
with typed args, rather than driving `replay_cli.py` directly.

- `capabilities/` — new top-level directory (peer of `agent/`,
  `evidence/`, `schema/`, `target_app/`), holding the runtime catalog.
  `capabilities/member_lookup.capability.json` is a copy (not a move)
  of `evidence/compiled/member_lookup.capability.json`; the latter
  stays curated submission proof, untouched.
- `agent/capability_api.py` — Flask app on port 5003.
  `GET /capabilities` scans `capabilities/*.capability.json` and
  returns a trimmed discovery listing (id, version, description, risk
  class, confirmation requirement, typed inputs/outputs), deliberately
  excluding internal fields (steps, guardrails, escalation policy,
  discovery provenance) an external caller has no use for.
  `POST /capabilities/<id>/invoke` constructs a `Replayer` exactly as
  `replay_cli.py` already does, but with `handoff_enabled` always
  false — a blocking HTTP request is the wrong shape for a human
  handoff regardless of whether the underlying mechanism works. An
  `escalate` trigger still opens in the `SessionStore` and stays
  visible to the operator console; it just doesn't block this request.
  `confirmed` is passed straight through to `_preflight`, unchanged.
  Status mapping: `Success` -> 200, `BusinessOutcome` -> 200 (both are
  legitimate answers to a well-formed request), `HardFailure` -> 422,
  `PendingEscalation` -> 202. No 5xx for any of the four.
- `agent/tests/test_capability_api.py` — catalog listing shape, 404 on
  an unknown `capability_id`, and the confirmation gate (422,
  `confirmation_required`) via the same synthetic gated-fixture pattern
  `test_replay.py` already uses for that path, since `member_lookup`
  itself doesn't require confirmation. A fourth test boots `target_app`
  in-process and drives a real Success run through a live browser.
- `evidence/capability_api/` — real curl request/response pairs against
  the live stack (`target_app` on :5001, `capability_api` on :5003):
  the discovery listing, a genuine Success invoke (`M1001`), and a
  genuine BusinessOutcome invoke (`M1002`, access-denied). `full_name`
  / `savings_balance` in the Success response are redacted to the same
  masked, length-bearing form `agent.replay.redact` produces for
  `replay.jsonl`; the BusinessOutcome response needed no redaction
  since it carries only `outcome_code`.

### Interruption and re-verification

This work was originally built in a prior session that was interrupted
mid-task by an unplanned machine shutdown, after the code, tests, and
evidence had been written but before anything was committed. This
session picked it up cold with no memory of that prior session, so
before trusting any of it — the pre-shutdown test run, the saved
evidence transcripts — everything was re-verified from a clean state
rather than assumed correct:

- Confirmed no stray `target_app`/`capability_api` processes were left
  running on ports 5001/5003 from before the shutdown.
- Started `target_app` fresh, cleared `agent/`'s `__pycache__` and any
  `.pytest_cache`, and re-ran the full suite
  (`python -m pytest agent/tests/ -q -p no:cacheprovider`): 48 passed,
  genuinely re-executed rather than relying on a cached prior result.
- Started `capability_api` fresh and re-ran Step 4's live demonstration
  — `GET /capabilities` and both invoke cases — comparing the new
  responses against what was already saved under
  `evidence/capability_api/`. The listing response was byte-identical;
  both invoke responses matched in exact key set, status code, and
  outcome code (only run-specific fields — `run_id`, `duration_s`,
  `evidence_path`, timestamps — differed, which is expected). No shape
  or correctness discrepancy, so the existing evidence files were left
  as they were rather than overwritten.

### Committed

- `capabilities/member_lookup.capability.json` (prior session, before
  the interruption)
- `agent/capability_api.py`
- `agent/tests/test_capability_api.py`
- `evidence/capability_api/`, this entry

---

## 2026-09-11 — Multi-run stability signal (`--repeat N`)

### Built

Section 8 stretch goal 2, "multi-run stability": the same compiled
capability + inputs replayed N times sequentially, LLM still nowhere in
the loop, to measure whether an artifact is actually stable rather than
trusting a single pass.

- `agent/replay_cli.py` — new `--repeat N` flag (default `1`, so
  existing single-run behavior and exit codes are unchanged when the
  flag is omitted). At `N > 1`, `main()` delegates to `_run_repeat()`:
  a plain sequential `for i in range(N)` — no thread pool, no asyncio,
  no subprocess parallelism, matching the brief's exclusion of scaling
  infrastructure from this project's scope. Each iteration constructs a
  fresh `Replayer` (the same pattern every existing caller already
  uses — `replay_cli`'s own single-run path, `capability_api.py`,
  `test_replay.py`), and there is no retry-on-failure inside the loop:
  each run reaches its natural conclusion once, since retrying before
  counting would hide the exact flakiness this feature exists to
  surface. Repeat runs default `handoff_enabled` to `False` for the
  same reason `capability_api.py` does — an automated stability check
  blocking N times for a human operator defeats its own purpose; a new
  `--repeat-allow-handoff` flag opts back in per invocation.
- `agent/replay.py` — `Replayer.__init__` gained an additive
  `run_id_suffix: str = ""` parameter (default preserves every other
  caller's behavior unchanged). Fixes a real collision risk: `_run_id()`
  is second-resolution (`replay-YYYYMMDD-HHMMSS`, no random component),
  and a run that fails at preflight returns near-instantly, so two such
  runs inside a repeat loop could previously land in the same
  wall-clock second and silently overwrite each other's
  `evidence/replays/<run_id>/replay.jsonl` (opened in `"w"` mode).
  `_run_repeat()` passes `run_id_suffix=f"-r{i}"` per iteration.
- `_aggregate_stability(results: list[dict]) -> dict` (`replay_cli.py`)
  — the stability signal itself, computed over each run's
  `.to_dict()`: `success_rate` (`Success` + `BusinessOutcome` count /
  N — both are legitimate completions per the existing three-way
  outcome taxonomy), per-status counts, `failure_determinism`
  (computed only over `hard_failure` runs, grouped by
  `(at_step_id, trigger)` — `"deterministic"` if every hard failure
  shares the same step+trigger, `"non-deterministic"` if they differ,
  `"n/a"` if there were none), and `duration_s` min/max/mean across all
  N runs. `_print_stability_summary()` renders it human-readable;
  `--json` prints the same aggregate as JSON, matching the existing
  single-run `--json` convention. Exit code for `--repeat` mode: `0` if
  `success_rate == 1.0`, `1` otherwise.
- `agent/tests/test_replay_repeat.py` — eight tests against
  `_aggregate_stability` directly, using constructed `ReplayResult`
  objects (`Success`/`BusinessOutcome`/`HardFailure`/
  `PendingEscalation`) rather than N real browser runs: success-rate
  math including the Success+BusinessOutcome-both-count-as-success
  case, `failure_determinism` for all three cases (`n/a`, matching
  step+trigger, differing by step, differing by trigger only),
  duration min/max/mean, and that `run_ids` are carried through for
  traceability. Full suite: 48 -> 56 passed.
- `evidence/stability/` — `summary.json` + `README.md`, following the
  same curation convention as `evidence/capability_api/`.

### Verified

Ran `--repeat 5` for real against the live stack (`target_app` on
:5001), same capability and happy-path inputs already used elsewhere in
`evidence/` (`member_lookup`, `search_field=Member ID`,
`search_term=M1001`):

```
python -m agent.replay_cli \
    --capability capabilities/member_lookup.capability.json \
    --input search_field="Member ID" --input search_term=M1001 \
    --repeat 5 --json
```

All 5 runs came back `success` — `success_rate: 1.0`,
`failure_determinism: "n/a"`, `duration_s` min `2.947s` / max `3.508s`
/ mean `3.195s`. Confirmed the five `evidence/replays/replay-
20260911-065607-r{0..4}` directories were written distinctly (no
collision) and stayed outside `git status` (gitignored, as expected).
Also spot-checked a second `--repeat 5` run against the access-denied
input (`M1002`) to confirm `BusinessOutcome` runs are counted toward
`success_rate` the same as `Success` runs — all 5 came back
`business_outcome`, `success_rate: 1.0` — before discarding that run
(not used for the committed evidence).

Full test suite re-run from a clean `__pycache__`/`.pytest_cache`
after all changes: 56 passed.

### Committed

- `agent/replay.py` (the `run_id_suffix` addition)
- `agent/replay_cli.py` (`--repeat`, aggregation, printing)
- `agent/tests/test_replay_repeat.py`
- `evidence/stability/`, this entry
