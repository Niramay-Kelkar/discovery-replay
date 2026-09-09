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
- this BUILD_LOG entry