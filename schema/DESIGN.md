# Capability artifact schema — design

This document defines the **capability artifact**: the typed, versioned
contract that `agent.discover` produces once and `agent.replay` consumes
on every production invocation. It is written before the models so the
reasoning is on record; `agent/models.py` implements exactly what is
described here, and `schema/example_artifact.json` is a hand-authored
instance of it for the target app's member-lookup flow.

The artifact is the product of this system. It is not a macro recording.
A macro says "click here, type there." A capability says "here is a
named operation with typed inputs and outputs, here is how to drive the
UI to perform it, here is how to tell a real answer from a crash, and
here is how risky it is and what to do when it goes wrong."

---

## 1. The core split: mechanical layer vs policy layer

A capability artifact has two layers with **different provenance**.

### Mechanical layer — *observed*

Everything that a single successful discovery run can legitimately
produce by watching itself succeed:

- `inputs` — the typed parameters the run was given.
- `outputs` — the typed values read off the final screen.
- `steps` — the ordered actions taken, each with locator strategies for
  its target element and a checkpoint that was true afterward.
- `target` — the app and entry point the run started from.

This layer is a faithful transcript of one happy path, generalized from
concrete values to input parameters (see §4).

### Policy layer — *authored*

Everything about **failure and risk**:

- `risk_class` — read-only vs mutating.
- `requires_confirmation` — may this run unattended?
- `expected_outcomes` — the named non-happy results that are still
  legitimate answers (not-found, permission denied), each with a
  detection rule.
- `guardrails` — the routes and action types replay is allowed to use,
  narrowed to what the successful trajectory actually needed.
- `escalation_policy` — retry budget and what to do on each failure
  trigger.

### Why they must be separate

**A single successful discovery run only ever observes the happy path.**
It never sees the access-denied page, never sees "no such member," never
sees a timeout or an unrecognized dialog — by definition, because it
succeeded. It therefore has **no evidence** about how those states look,
whether they are legitimate answers or bugs, or how a replay should
respond. Any policy "derived" from one green trajectory would be a
guess dressed up as data.

So the policy layer is authored as a deliberate, separate act — by a
human, or by a separate reasoning pass with domain knowledge and access
to the target app's failure modes — and the schema keeps it physically
separate to make that provenance obvious. The compiler fills the
mechanical layer and emits **empty policy stubs**; a capability is not
marked replayable until `policy_authored_by` is set. A future compiler
change cannot quietly start inventing escalation rules from a
trajectory, because the trajectory and the policy do not live in the
same part of the tree.

The three-way outcome taxonomy the brief asks for falls out of this
split:

| Result | Where it is defined | Meaning |
|---|---|---|
| **success** | mechanical layer (the steps all pass their checkpoints) | happy path completed, outputs extracted |
| **business outcome** | policy layer (`expected_outcomes`, classification `business_outcome`) | a legitimate non-happy answer — report it and stop, do not retry |
| **hard failure** | neither — it is the absence of both | genuinely broken: escalate or fail per `escalation_policy` |

---

## 2. Locators: ranked fallback strategies

Every step that targets an element carries a **ranked list** of
`LocatorStrategy` entries, not one selector. At replay, the resolver
tries them in order and uses the first that resolves to exactly one
visible element. If none resolve, that is a step-resolution failure and
the escalation policy takes over.

### The ranking principle: most semantically stable first

Rank by *how likely the strategy is to survive a legitimate, cosmetic
change to the app that did not change what the operation means.*

1. **`aria_role`** — accessibility role + accessible name (e.g.
   `role=link, name="Open detail for {{member_name}}, member
   {{member_id}}"`). This is the primary strategy for this project.
   The target app's whole premise is a clean accessibility tree layered
   over deliberately hostile DOM: nested layout tables, generic class
   names (`div1`, `box2`), reflowing text nodes. Role+name is tied to
   the *semantic contract* the app exposes to assistive tech, which is
   the most stable surface it has. It survives DOM restructuring, class
   renames, and wrapper changes.

2. **`text_label`** — visible text or `<label>`-association match. More
   stable than structural selectors: survives class and layout churn.
   But it breaks on copy edits and localization, and it is ambiguous
   when the same text appears more than once. Good fallback, poor
   primary.

3. **`test_id`** *(reserved)* — an explicit automation attribute. Would
   rank at the top if it existed, because it is a deliberate contract —
   but legacy bank software does not have these, so it is reserved, not
   used.

4. **`css` / `xpath`** *(reserved, last resort)* — positional and
   structural. Brittle against exactly the cosmetic churn we expect.
   Only ever a final fallback, and a replay that has to fall through to
   one should log it loudly as locator debt.

Each strategy can also carry a `within` scope (restrict the search to a
named region, e.g. the results table) and an `exact` flag (exact vs
substring name match). `kind` is an open string, not a closed enum, so a
new strategy type can be added later without a schema migration — see
§7.

**Compile from the label, not the value.** A locator extracted during
discovery must target the field by its *label* ("Savings balance"), not
the specific value it happened to hold ("$18,750.42"). The compiled
artifact has to work for any valid input, not just the one used during
discovery. Locator `name` fields may contain `{{input}}` templates that
replay fills per invocation.

---

## 3. Checkpoints: verifying a step succeeded

Every step runs **ACT → SETTLE → CHECK**. ACT resolves a locator and
performs the action. SETTLE waits for the page to stabilize (bounded by
`SettleSpec.max_wait_seconds`). CHECK is a dialog check plus the step's
`checkpoint`. Retries — automatic or human-resumed — only ever redo
SETTLE/CHECK, never re-run ACT.

Checkpoint kinds:

- **`element_visible`** — a given locator resolves to a visible element.
- **`text_present`** — the page contains a given string.
- **`outputs_non_empty`** — named outputs were successfully extracted
  (used on the terminal step).
- **`outcome_matched`** — a named `expected_outcome` is currently
  detected on the page (or, with no code, *any* recognized expected
  outcome). This is the bridge between the mechanical and policy layers.
- **`any_of`** / **`all_of`** — composites over a list of child
  checkpoints.

### Why `any_of` is load-bearing

The terminal step of the member-lookup flow — "open the member's detail
page" — has this checkpoint:

```
any_of:
  - element_visible: <the "Member record for …" table>
  - outcome_matched:  (any recognized expected outcome)
```

Without the `any_of`, the checkpoint only knows the happy-path element.
When a teller looks up a member who does not exist, or one their profile
cannot view, the detail table never appears — so a naive checkpoint
sees "expected element missing," reports the step as unsatisfied, burns
its retry budget, times out, and escalates a human for what is actually
**a clean, correct answer**: "no such member" / "access denied."

`any_of` lets the step say: *I am satisfied if the happy-path element
appeared **or** if replay recognized a legitimate business outcome.*
The outcome-detection module (kept separate — see the project's
`CLAUDE.md`) is what evaluates `outcome_matched`; the checkpoint just
references its verdict. Replay then reports `success` or the specific
business-outcome code to the caller, and in neither case does it treat
the screen as hung.

---

## 4. Typed inputs and outputs

A capability is a contract. The caller does not know or care which
fields get typed where; they supply named parameters and receive named
values.

- **`InputParam`** — `name`, `type`, `required`, `description`,
  optional `example`. Steps reference inputs by name, and locator/value
  templates interpolate them as `{{name}}`.
- **`OutputParam`** — `name`, `type`, `description`, `required`. An
  `extract` step names the output it populates. `required` outputs feed
  the terminal `outputs_non_empty` checkpoint.

`type` is a small closed set: `string`, `integer`, `number`, `boolean`,
`date`, `money`. `money` is called out separately from `number` because
balances are read as rendered strings ("$18,750.42") and callers should
know that is what they are getting.

The member-lookup capability:

- inputs: `search_field` (`"member_id"` | `"last_name"`),
  `search_term` (string, required).
- outputs: `member_id`, `full_name`, `savings_balance` (money),
  `date_of_birth`, `address`, plus `outcome_code` (string — always
  populated, `"SUCCESS"` on the happy path).

---

## 5. Risk classification and escalation policy

### Risk

- **`risk_class`**: `read_only` | `mutating`. Member lookup is
  `read_only` — it reads records, changes nothing.
- **`requires_confirmation`**: may replay run this unattended, or must a
  human approve each run? Defaults to `false` for `read_only`, `true`
  for `mutating`.

### `EscalationPolicy`

- **`max_retries_per_step`** — how many times SETTLE/CHECK may be
  redone before the step is declared failed. The action is never
  re-run.
- **`retry_backoff_seconds`** — delay between those retries.
- **`human_handoff_timeout_seconds`** — once control is handed to a
  human operator (in the **same live session** — brief requirement),
  how long replay waits for them before auto-failing the run.
- Per-trigger actions, each one of `retry` / `escalate` / `fail` /
  `abort`:
  - **`on_step_timeout`** — SETTLE exceeded `max_wait_seconds`. Default
    `retry` (then escalate when retries are exhausted). This is what
    covers the target app's deliberately slow detail page: the settle
    timeout for that step is set comfortably above the injected 4s
    delay, and a genuine overrun past that still retries before
    bothering a human.
  - **`on_hard_failure`** — locator resolved nothing, or an action
    threw. Default `escalate`.
  - **`on_unrecognized_dialog`** — a modal/interstitial appeared that
    matches no `expected_outcome`. Default `escalate` — replay cannot
    know whether dismissing it is safe. (A *recognized* dialog, like the
    target app's "Supervisor review required" interstitial, is an
    `expected_outcome`, not this trigger.)
  - **`on_checkpoint_failure`** — the checkpoint was false after
    retries and no business outcome matched. Default `escalate`.

`retry` as a trigger action means "exhaust `max_retries_per_step` on
SETTLE/CHECK, then escalate." `abort` means stop immediately without a
handoff (used when continuing could be unsafe).

---

## 6. Expected business outcomes

`expected_outcomes` is a list of named, distinct results that are **not
the happy path but are still legitimate answers**. Each entry:

- **`code`** — stable machine identifier, e.g. `MEMBER_NOT_FOUND`,
  `ACCESS_DENIED`, `SUPERVISOR_REVIEW_REQUIRED`.
- **`description`** — human-readable.
- **`classification`** — `business_outcome` (a real answer; report and
  stop) or `hard_failure` (declared here only so replay can *name* a
  known breakage instead of reporting a generic crash).
- **`terminal`** — does detecting this end the run?
- **`detection`** — a `DetectionRule` that tells replay how to spot this
  state on the live page.

### `DetectionRule`

Mirrors the checkpoint composite shape so detection can combine signals:

- **`text_present`** — `text`.
- **`aria_visible`** — `role` + `name` (substring unless `exact`).
- **`http_status`** — `status` (the response code for the navigation).
- **`url_matches`** — `pattern`.
- **`any_of`** / **`all_of`** — `rules: [...]`.

Detection rules key off the *label / structural* signal, never a
specific member's data.

### For the target app

| code | classification | terminal | detection |
|---|---|---|---|
| `MEMBER_NOT_FOUND` | business_outcome | yes | `any_of`: text "No members matched that search." / text "No such member." |
| `ACCESS_DENIED` | business_outcome | yes | `any_of`: `http_status 403` / `aria_visible role=alert name~="not authorized"` |
| `SUPERVISOR_REVIEW_REQUIRED` | business_outcome | yes | `aria_visible role=alertdialog name~="Supervisor review required"` — replay reports this so a supervisor can decide; it does **not** auto-click Continue |

`SUPERVISOR_REVIEW_REQUIRED` is the interesting one: it is a dialog, but
a *recognized* one, so it is an expected outcome (reported), not an
`on_unrecognized_dialog` escalation. The schema draws that line by
whether a `DetectionRule` matches.

---

## 7. Guardrails

`Guardrails` narrows replay's authority to what the successful
trajectory actually used:

- **`allowlist_routes`** — URL path patterns replay may navigate to or
  land on. Narrowed at compile time to the routes the successful run
  visited (`/`, `/search`, `/member/*`) — **not** left at discovery's
  wide-open scope.
- **`allowlist_action_types`** — the action verbs replay may perform.
  Must be a superset of every action the steps use. A `read_only`
  capability's list contains no mutating verbs.
- **`denylist_text_patterns`** — if any of these strings appear on a
  page mid-replay, abort. A tripwire: a `read_only` member lookup that
  suddenly renders "Transfer funds" or "Close account" is on a wrong
  page and should stop, not click.
- **`max_steps`** — hard ceiling on total steps executed, loop
  protection.
- **`forbid_offdomain_navigation`** — default `true`; replay never
  leaves the target app's origin.

### Action-type vocabulary (replay side)

The schema needs a closed set of action verbs for `Step.action` and the
guardrail allowlist. The **discovery-side tool vocabulary** — the full
deliberate set of actions the discovery LLM may call, with the
justification for including/excluding each — is a separate deliverable
(see `CLAUDE.md`). The replay-side set is the minimal executable
residue of that:

| action | why it exists |
|---|---|
| `navigate` | go to a route (entry point, and any in-flow navigation) |
| `fill` | enter an input parameter's value into a text field |
| `select_option` | choose from a native `<select>` (legacy apps lean on these) |
| `click` | activate a link, button, or radio |
| `press_key` | keyboard-only interactions some legacy widgets require |
| `extract` | read a value off the page into a named output |

No `scroll` (replay resolves elements by locator, not viewport), no
`screenshot` (an evidence concern, not a decision action), no raw
`type`-at-coordinates (defeats the locator contract).

---

## 8. Top-level `Capability`

```
Capability
├── schema_version            "1.0"
├── capability_id             "member_lookup"
├── version                   "1.0.0"  (semver of this artifact)
├── description
├── target
│   ├── app                   "cornerstone-teller-console"
│   ├── base_url
│   └── entry_route           "/"
│  ── mechanical layer ───────────────────────────────
├── inputs[]                  InputParam
├── outputs[]                 OutputParam
├── steps[]                   Step (ordinal, action, locators[], checkpoint, settle)
│  ── policy layer (authored separately) ─────────────
├── risk_class                read_only | mutating
├── requires_confirmation
├── expected_outcomes[]       ExpectedOutcome (code, classification, terminal, detection)
├── guardrails                Guardrails
├── escalation_policy         EscalationPolicy
│  ── provenance ─────────────────────────────────────
├── discovery                 { run_id, model, goal, completed_at }  (nullable)
└── policy_authored_by        str | None   — replay refuses to run until set
```

### Validation the model enforces

- step `ordinal`s are unique and contiguous from 1;
- every `fill` step references a declared input; every `extract` step
  references a declared output;
- `guardrails.allowlist_action_types` covers every action the steps use;
- `element_visible` checkpoints carry a locator; `any_of`/`all_of`
  carry a non-empty `checks` list;
- `outcome_matched` checkpoints and detection-referenced codes resolve
  to a declared `expected_outcome`;
- a non-empty `steps` list has exactly one terminal step whose
  checkpoint can satisfy on `outputs_non_empty`.

### Extensibility

`LocatorStrategy`, `Checkpoint`, and `DetectionRule` allow extra fields
and use open `kind` strings, so new locator/checkpoint/detection types
are additive — no migration. The structural models (`Capability`,
`Step`, params) forbid extras, so typos in an artifact are caught at
load.
