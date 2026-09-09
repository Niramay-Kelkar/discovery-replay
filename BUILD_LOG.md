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