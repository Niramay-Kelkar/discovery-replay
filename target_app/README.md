# target_app — "Acme Savings" legacy bank demo

The proxy target the discovery/replay system automates against. It is a
small, deliberately hostile server-rendered web app: nested `<table>`
layouts, an unrelated `<iframe>`, and non-semantic CSS class names
(`div1`, `box2`, …). Despite the messy DOM, every interactive element
and every label/value cell carries a correct ARIA role and accessible
name, so the accessibility tree stays usable (see the contract below).

Stack: Flask + SQLite, no frontend framework, no JS.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r target_app/requirements.txt

.venv/bin/python target_app/seed.py            # build target_app/bank.db
.venv/bin/python -m flask --app target_app.app run --port 5001
```

Then open http://127.0.0.1:5001/ . `seed.py` drops and recreates the
table on every run, so the target is always in a known state.
`target_app/bank.db` is git-ignored.

## Pages

| Route | What it is |
|---|---|
| `GET /` | Search form: radio for "Member ID" vs "Last name", one text field, submit. |
| `GET /search?field=<member_id\|last_name>&q=<term>` | Results table, or a distinct "No members matched" message. |
| `GET /member/<member_id>` | Member detail (ID, name, DOB, address, phone, email, savings balance), or one of the special conditions below. |

Search is exact match (case-insensitive). Unknown `field` values fall
back to `member_id`; an empty `q` re-renders the form with an inline
error.

## Seeded records and what each demonstrates

| Member ID | Last name | Purpose | How to reach it |
|---|---|---|---|
| `M1001` | `Nguyen` | **Happy path.** Normal record, found by ID and by last name. Balance $18,750.42. | search `M1001` / search `Nguyen` → View |
| `M1002` | `Delgado` | **Access denied.** Row has `access_denied=1` (a real column); `/member/M1002` returns a clear "Access Denied" page with HTTP 403, never data. | search `M1002` → View |
| `M1003` | `Kim` | **Slow load.** Ordinary DB row; `M1003` is in `SLOW_LOAD_IDS` in `app.py`, so the detail page sleeps 4s before rendering, standing in for transient slowness. Search results are instant. | search `M1003` → View (wait ~4s) |
| `M1004` | `Okafor` | Extra normal record (padding, and a second happy-path option). | search `M1004` / `Okafor` |
| `M1005` | `Santos` | Extra normal record. | search `M1005` / `Santos` |
| `M1006` | `Ashwood` | **Unexpected interstitial.** Ordinary DB row; `M1006` is in `INTERSTITIAL_IDS` in `app.py`, so `/member/M1006` shows a "Supervisor review required" confirmation dialog (`role="alertdialog"`) instead of the record. Clicking **Continue** (`/member/M1006?confirm=yes`) proceeds to the real detail page; **Cancel** returns to search. This one *is* a declared expected outcome (`SUPERVISOR_REVIEW_REQUIRED`): replay recognizes it and reports it, it does not escalate. | search `M1006` → View |
| `M1007` | `Whitfield` | **Unrecognized blocking state → human escalation.** Ordinary DB row; `M1007` is in `MAINTENANCE_HOLD_IDS` in `app.py`, so `/member/M1007` shows an **"Account maintenance hold"** screen (`role="alertdialog"`, `aria-label="Account maintenance hold"`) instead of the record. Unlike every other non-happy case, this matches **none** of the declared `expected_outcomes` — different wording, an aria-label the PolicySpec has never seen — so replay cannot classify it and escalates to a human operator. Clicking **Dismiss** (`/member/M1007?ack=yes`) clears it to the real detail page (Dana Whitfield, balance $7,605.14); **Back to search** returns to `/`. | search `M1007` → View |

**Why the split:** `access_denied` is a data-backed column because it is
a genuine fact about the member's record — an authorization business
outcome replay should report as such. The slow-load delay, the
interstitial and the maintenance hold are *injected runtime conditions*,
not record facts, so they are hardcoded ID sets checked in the route
handler (`SLOW_LOAD_IDS`, `INTERSTITIAL_IDS`, `MAINTENANCE_HOLD_IDS`)
rather than columns.

**Recognized vs. unrecognized non-happy states:** access-denied,
not-found and the supervisor-review interstitial are all things the
capability's PolicySpec declares and detects — replay returns them as
business outcomes. The `M1007` maintenance hold is the deliberate
counter-case: nothing in `expected_outcomes` matches it, so it is what
actually exercises the escalation / human-handoff path.

### Not-found behavior (verify, not seeded)

- Search for a nonexistent ID (`M9999`) or last name (`Zzzznope`) →
  results page with **"No members matched that search."** (HTTP 200),
  not an error.
- Direct hit on `/member/M9999` → dedicated "No such member." page with
  HTTP 404.

## Accessibility contract

The raw DOM is intentionally hostile; automation should drive the app
through the accessibility tree, relying on these roles and accessible
names (verified against the Chrome accessibility tree while building):

**Search page (`/`)**
- `search` landmark, name **"Member search"** — wraps the whole form.
- `radio` name **"Member ID"** and `radio` name **"Last name"** — the
  search-field selector (`name="field"`, values `member_id` /
  `last_name`).
- `textbox` name **"Search term (member ID or last name)"** — the query
  field (`name="q"`).
- `button` name **"Look Up"** — submit.

**Results page (`/search`)**
- `table` name **"Member search results"**, with `columnheader`s
  **"Member ID"**, **"Name"**, **"City"**, **"Detail"** and one `cell`
  per value.
- One `link` per result, name **"Open detail for <name>, member
  <id>"**, href `/member/<id>`.
- No matches: text node **"No members matched that search."**

**Detail page (`/member/<id>`)**
- `table` name **"Member record for <full name>"**. Each field is a
  `rowheader` (the label: **"Member ID"**, **"Full name"**, **"Date of
  birth"**, **"Address"**, **"Phone"**, **"Email"**, **"Savings
  balance"**) paired with a `cell` holding the value. Compile locators
  and checkpoints against the rowheader label, not the value.

**Access denied**
- `alert` whose text names the member and the denial. Page also returns
  HTTP 403.

**Interstitial (`/member/M1006`)**
- `alertdialog` containing `button` name **"Continue to member
  record"** (href `…?confirm=yes`) and `button` name **"Cancel and
  return to search"** (href `/`).

**Maintenance hold (`/member/M1007`)**
- `alertdialog` name **"Account maintenance hold"** containing `button`
  name **"Acknowledge hold and open record"** (href `…?ack=yes`) and
  `button` name **"Return to search"** (href `/`). Wording and label are
  deliberately unlike the interstitial's so no `expected_outcome`
  detection rule matches it.

**Every page**
- An `iframe` titled **"Branch bulletin board"** is present and
  irrelevant — automation should ignore it.
- A decorative right-hand column (branch address, lobby hours, a generic
  FDIC/member-agreement disclaimer) is `role="presentation"`: it holds
  no interactive elements and no tables/landmarks, so it surfaces only
  as loose text nodes. It is tied to no member data — pure structural
  noise, like the iframe. Automation should ignore it.
- Layout `<table>`s are marked `role="presentation"` and do not appear
  in the tree.
