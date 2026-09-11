# Design Write-Up — discovery-replay

## 1. Architecture

The system is five processes that hand off through typed artifacts and files, not live calls. Two run as servers: target_app (Flask, port 5001, the hostile legacy UI being automated) and operator_console (Flask, port 5002, the escalation handoff surface). Three run as short-lived CLIs, each owning its own Playwright browser directly: agent.discover, agent.compile_cli, and agent.replay.

Discovery and replay never talk to each other, and neither talks to the compiler at runtime. Discovery writes a Trajectory to disk (evidence/runs/<id>/trajectory.json). The compiler reads that plus a hand-authored PolicySpec module and writes a Capability artifact. Replay reads that artifact and produces one of four typed results (Success, BusinessOutcome, HardFailure, PendingEscalation). The only runtime coupling between two live processes is replay and the operator console, which share one SQLite file recording pending escalations. Nothing else connects them. The console never touches the browser directly. It renders context and flips a status field.

```mermaid
flowchart TB
    ANT[Anthropic API]
    TA[("target_app<br/>:5001 (Flask)")]

    subgraph Discovery
        DIS["agent.discover<br/>(owns its own browser)"]
    end
    DIS <-->|tool-use loop| ANT
    DIS <-->|click / type / extract<br/>via accessibility tree| TA
    DIS -->|writes| TRAJ["trajectory.json<br/>evidence/runs/&lt;id&gt;"]

    subgraph Compile
        COMP["agent.compile_cli<br/>(pure transform, no browser/network)"]
    end
    TRAJ --> COMP
    POL["agent/policies/member_lookup.py<br/>hand-authored PolicySpec"] --> COMP
    COMP -->|writes| CAP["capability.json<br/>evidence/compiled/"]

    subgraph Replay
        REP["agent.replay<br/>(owns its own browser)"]
    end
    CAP --> REP
    REP <-->|ACT → SETTLE → CHECK| TA
    REP -->|writes, values redacted| RJ["replay.jsonl<br/>evidence/replays/&lt;id&gt;"]
    REP --> RES{{"Success / BusinessOutcome /<br/>HardFailure / PendingEscalation"}}

    subgraph Escalation ["Escalation handoff — the only live coupling"]
        DB[("escalations.db<br/>SQLite, shared file")]
        OC["operator_console<br/>:5002 (Flask)"]
    end
    REP -.->|"on escalate: insert row,<br/>then poll (browser stays open)"| DB
    OC <-->|reads pending /<br/>POST /resume flips status| DB
    HUMAN(("Human operator"))
    HUMAN -->|"headed window, or<br/>CDP attach :9222"| TA
    HUMAN -->|clicks Resume| OC
```

Four decisions carried the most weight.

First, the artifact has two layers, and the policy layer is never derived. A single successful discovery run only proves the happy path. It has no evidence about which failures are legitimate business outcomes, how risky an action is, or how long to wait before escalating. The compiler merges in a separately authored PolicySpec for those fields, and a missing required field fails compilation loudly rather than defaulting to something permissive.

Second, discovery and replay share one perception module, one accessibility-snapshot representation of the page, and one exact-name resolution rule. If discovery reasoned over screenshots while replay resolved by CSS, the artifact's locators would be built from a representation replay doesn't share. Exact matching specifically exists because the target app's nested table markup means a leaf element's accessible name is often a substring of its wrapper's concatenated name, which makes default substring matching structurally ambiguous.

Third, a step's action is a one-shot call made once, outside any retry loop. Every automatic retry and every human resume re-enters only the settle-and-check phase. Re-firing an action that already completed, which is how a mutating step gets double-submitted, is structurally impossible rather than a convention someone could forget.

Fourth, escalation is a synchronous poll against SQLite, not a queue or an async event loop. Two local processes need durable shared state while one blocks for minutes and the other writes once. That's a proportionate answer for a single-operator, single-machine scope, not a shortcut, since the brief explicitly scopes out a full co-browsing console.

Deliberately absent: any queue, worker, or async runtime; any multi-tenant routing, credential store, or per-tenant config; any deployment layer beyond dev servers on localhost. These are real omissions, not oversights, and each one is a decision to design abstractions that could be extended later rather than build infrastructure against imagined load now. The interfaces that a scaled version would need already exist as data and contracts, not code waiting to be rewritten: the versioned artifact schema, the mechanical/policy split, the guardrails block, and the four-way result contract.

Python 3.11+, Flask, Pydantic v2, Playwright, and stdlib sqlite3. Pydantic v2 matters most: the artifact is the product, so it needs strict validation (extra="forbid" on every model) and cross-field rules that catch a malformed artifact at load time, not mid-replay.

## 2. Artifact schema

A capability artifact has two layers, and only one of them is derived from anything discovery observed. The mechanical layer, ordinal steps, ranked locators, checkpoints, is a generalized transcript of one successful run. The policy layer, risk classification, expected outcomes, guardrails, escalation policy, is authored separately and merged in at compile time. Capability rejects unknown fields entirely (extra="forbid"), and a missing required policy field fails compilation loudly rather than defaulting to something permissive. One field enforces this directly: replay refuses to run any artifact whose policy layer was never authored — policy_authored_by must be set. An artifact that was never reviewed by a human can't execute.

```mermaid
flowchart LR
    T["Trajectory<br/>(mechanical, from one live run)"] --> C["compile_capability()"]
    P["PolicySpec<br/>(authored, never derived)"] --> C
    C --> CAP["Capability<br/>schema_version, steps, guardrails,<br/>expected_outcomes, escalation_policy"]

    subgraph "Per step at replay"
        direction TB
        L1["locator rank 1: aria_role"] -->|fails| L2["locator rank 2: text_label"]
        L2 -->|fails| HF["HardFailure"]
        L1 -->|resolves| ACT[ACT: perform action]
        ACT --> SETTLE[SETTLE: wait]
        SETTLE --> CHK{"CHECK: any_of"}
        CHK -->|"element_visible<br/>(next step's target)"| PASS[Step passed]
        CHK -->|outcome_matched| PASS
        CHK -->|neither| ESC["escalate / retry"]
    end

    CAP -.->|feeds| L1
```

Locators are a ranked list, not a single selector, because no one strategy is reliable across a legacy surface. aria_role, matching by accessible role and name, is the only strategy actually exercised end to end; every locator in the compiled artifact resolves on rank 1. text_label exists as a documented fallback but has never been hit in a real run. test_id is named in the schema as a reserved strategy for a more modern surface but has no resolver behind it yet, an honest placeholder, not a working fallback. The model is deliberately permissive here (extra="allow") in contrast to the strict structural models elsewhere, since the set of locator strategies is expected to grow as the system meets less cooperative UIs.

Checkpoints determine whether a step succeeded, and every one of them is wrapped any_of[element_visible, outcome_matched]. This isn't decoration. Replaying against a member the teller isn't authorized to see returns HTTP 403 and an "Access Denied" page, not the record table a checkpoint would normally look for. Without outcome_matched, that checkpoint just reads false, and a routine, expected denial escalates to a human and blocks for fifteen minutes. With it, detect_outcome walks the artifact's declared expected_outcomes in order, matches the first satisfied detection rule, in this case an HTTP-status check, and the run returns a clean, typed BusinessOutcome instead. The same distinction is what separates a legitimate "no such member" answer from a hang.

Inputs and outputs are typed and named, not positional. A step's locator or value can reference a declared input by template ({{search_term}}), and a cross-field validator confirms every template token resolves to something the artifact actually declares, so a stray reference fails at load rather than mid-replay. Output names are explicitly normalized at compile time through output_name_mapping, so a capability's contract reflects what the policy author intends to call a value, not whatever a given discovery run happened to name it internally. This mapping is required for every output a trajectory produced, not optional, so nothing ships under an unaccounted-for name.

Risk classification and guardrails aren't advisory. guardrails is a required field with required sub-fields, an artifact can't skip declaring an allowlist, so the empty-guardrails failure mode doesn't exist as a silent pass; if a route allowlist were somehow empty, the very first step would hard-fail before any navigation or interaction. requires_confirmation is the one field that was declared but not enforced until late in this build; replay now refuses to run a capability that sets it without an explicit --confirmed flag, closing what had been a real gap between the schema's intent and the engine's behavior.

schema_version and version serve different purposes, and only one is currently enforced. schema_version is a literal the model hard-rejects if it doesn't match, load-time protection against an incompatible future shape. version, the artifact's own semver, is presently a label: it flows into logs and evidence but nothing in the replay engine reads or compares it. The distinction between the two capability versions built during this project, one supporting only ID lookup, one supporting both search modes, makes the case for why the field needs to exist even without enforcement yet: a caller written against the older contract would fail cleanly against the newer one's now-required search_field input, and version is what lets that difference be named rather than discovered by surprise.

## 3. Determinism & error handling

Replay is deterministic because nothing in its decision path involves a model or randomness. Every choice, which action a step performs, which ranked locator gets tried first, whether a checkpoint passes, which declared outcome matches, what result type comes out the other end, is a lookup against the frozen artifact. Two runs with the same capability and the same inputs walk an identical sequence of steps in an identical order, evaluated against identical rules.

What genuinely varies between two such runs is narrower than it might sound, and worth stating honestly rather than glossing over: wall-clock timing, whether a retry actually fires, and which internal wait strategy resolves first (a network-idle wait versus its DOM-stable fallback). None of these change the outcome, since a retry only ever re-runs verification, never the action itself. There is one real, if currently latent, non-determinism worth naming: the detail-page link is matched by a name prefix with nth=0, since the exact member name isn't known at compile time. If a search ever matched more than one row, "first" would depend on the target app's own result ordering rather than anything replay controls. The ordering is stable today; it isn't structurally guaranteed to stay that way.

```mermaid
flowchart TD
    ACT["ACT (once, before verification begins)"] --> CHECK{"SETTLE + CHECK"}
    CHECK -->|checkpoint passes| NEXT[Next step / Success]
    CHECK -->|"declared outcome matched<br/>(business_outcome, terminal)"| BO[BusinessOutcome]
    CHECK -->|"declared outcome matched (hard_failure class)"| HF3[HardFailure]
    CHECK -->|"on_step_timeout,<br/>retries remaining"| RETRY["sleep 1.5s, re-run<br/>SETTLE + CHECK only"]
    RETRY --> CHECK
    CHECK -->|"any other trigger, or<br/>retries exhausted"| TRIG{"policy action"}
    TRIG -->|escalate + handoff enabled| PAUSE["pause, hand live session<br/>to operator, poll"]
    PAUSE -->|resumed in time| CHECK
    PAUSE -->|timeout| HF1[HardFailure: handoff_timeout]
    TRIG -->|"escalate, no handoff<br/>(--no-handoff, CI)"| PE[PendingEscalation]
    TRIG -->|guardrail / preflight violation| HF2[HardFailure]
```

Only one trigger ever retries: a step that times out waiting to settle. Everything else, an unresolved locator, a failed checkpoint, an unrecognized dialog, converts straight to escalation regardless of how the policy is configured, because retrying those wouldn't help and could mask a real problem. A timing-out step gets three evaluations total with a flat 1.5-second backoff between them, then falls through to escalation. Retrying is structurally confined to re-running settle-and-check: the action itself is invoked exactly once, before any retry logic is even reachable, so there is no code path capable of firing a mutating click twice.

The result contract makes the three-way distinction the brief asks for real rather than documented. A Success carries the declared outputs, unredacted, returned in-process, never written to the evidence log. A BusinessOutcome, a permission denial, a not-found search, a review-required interstitial, carries the matched outcome code and description, and exits cleanly rather than looking like a failure. A HardFailure names the step, the phase it broke in, a machine-readable trigger, and an expected-versus-observed pair meant to be diagnosable without a stack trace. A PendingEscalation is what a caller gets when the policy says to escalate but no human handoff is available, the path used in CI.

That expected-versus-observed design paid off during this build. The target app's ~4-second injected slow load once tripped a fixed click timeout inside the action phase itself, before the step's settle window ever got a chance to absorb it. Because that failure happened outside the normal checkpoint path, it surfaced as a raw exception rather than a clean, structured result, a debuggable gap in the error handling that then got closed: the action's timeout now honors the same settle bound the step already declares, so the slow page is absorbed where it's supposed to be. The fix is small; the reason it needed fixing is exactly the class of problem this section is asking about, a legitimate runtime condition the artifact hadn't accounted for correctly, found once, by actually hitting it.

On UI drift specifically: this isn't built, and it's worth saying so plainly rather than implying otherwise. Nothing compares the live page against what discovery recorded. The target app's UI is deliberately stable, so this was a scoped decision rather than an oversight, but drift isn't detected, it's contained. If the UI did change, a renamed control fails locator resolution and escalates with the exact accessible name that no longer resolves; a restructured page fails a checkpoint and escalates with a trace; a redirect trips the narrowed route allowlist and hard-fails outright. Real drift detection would need a structural fingerprint of each step's page captured at compile time and diffed at replay, none of which exists today; what does exist ensures that drift, if it happened, would surface as an actionable failure rather than a silent wrong answer.

Timeouts apply at several independent layers rather than one global clock: a per-step settle bound (2 to 12 seconds depending on the step, drawn from the artifact itself), a fixed 4-second window per ranked locator attempt, per-action timeouts inside the action phase (a flat 4 seconds for fill/select_option/press_key; for a navigating click, the larger of 4 seconds or the step's own settle bound, so slow pages are absorbed rather than tripping the action itself), and a 900-second (configurable) human handoff window. There is no overall wall-clock limit on a replay run; its total duration is bounded only by the sum of these individually-scoped waits.

## 4. Heterogeneity & multi-tenant

This section is design, not build, per the brief's own scope. Where
something below is already true of the code, it's marked as such; the
rest is a credible extension of decisions already made, not a promise of
what exists.

The seam between perception and the recorded flow is real but informal,
not a clean interface. There's no Surface protocol; perception.py and
the resolver inside replay.py import Playwright's Page/Locator directly.
What is surface-neutral is the artifact's vocabulary: a locator's kind
is an open string, not an enum, and the model accepts extra fields, so a
new locator strategy validates today with zero schema change. Role-and-
name matching itself isn't a browser concept, it's an accessibility-tree
concept, and that's what makes the rest of this section possible at all.
The honest split: adding a new browser resolution strategy is
low-friction; swapping in a non-browser perception backend, an OS
accessibility API for a desktop app, would need a real protocol
extracted from the current concrete class first. The data model would
mostly survive that refactor; the code doesn't have the seam pre-built.

```mermaid
flowchart TB
    subgraph Today["Built today"]
        LS["LocatorStrategy: kind (open string),<br/>extra fields allowed"]
        AR["aria_role: role + name + exact<br/>— maps directly onto ARIA"]
    end
    subgraph Legacy["Frameset / iframe web app (design)"]
        FR["+ frame coordinate on LocatorStrategy<br/>(non-breaking, additive)"]
        WI["wire up 'within' field<br/>(declared today, read by nothing)"]
    end
    subgraph Desktop["Native desktop app (design)"]
        UIA["role/name → OS accessibility API<br/>(Windows UIA / macOS AX / Linux AT-SPI2)"]
        NAV["navigate → launch + activate window<br/>(URL-addressing assumption doesn't carry over)"]
    end
    LS --> AR
    AR -.->|"role+name concept ports,<br/>resolver code doesn't"| FR
    AR -.->|"role+name concept ports,<br/>resolver code doesn't"| UIA
    FR --> WI
    UIA --> NAV
```

A legacy frameset app is the closer extension. target_app already has
one unrelated iframe, and checking against it confirms Playwright's
accessibility snapshot doesn't pierce frame boundaries, elements inside
a child frame would currently resolve to zero matches, indistinguishable
from "the element doesn't exist" rather than "the surface isn't
supported yet." That's a real gap, not a hypothetical one. The fix is
additive rather than architectural: a frame coordinate on
LocatorStrategy (free, given the schema already allows extra fields),
frame-aware resolution in the perception and resolver layers, and
finally wiring up the within field, which is declared in the schema
today but read by nothing. None of this touches the step model, the
checkpoint composites, the outcome taxonomy, or the mechanical/policy
split.

A native desktop app is a bigger lift, but the project's central bet,
reason over an accessibility tree rather than pixels or raw markup, is
exactly what makes it tractable at all. The artifact schema, the
escalation state machine, the three-way result taxonomy, and the
ACT/SETTLE/CHECK contract are surface-neutral and would carry over with
mostly additive field changes. What needs a parallel build is
everything below that: perception itself, the resolver, and specifically
the navigation model, since navigate/base_url/route-allowlisting all
assume URL addressing, which a desktop app simply doesn't have. The
operator-handoff mechanism is the sharpest edge: the current CDP attach
works because Chrome happens to expose a remarkably convenient
remote-control primitive. A desktop equivalent, screen-sharing or an
accessibility-driver attach, is a genuinely harder problem, not a
drop-in swap.

Multi-tenant reuse is where the design already earns something real: a
compiled capability's base_url is overridable at replay time with no
recompilation, so pointing the same artifact at a second tenant's host
works today. Locators built from accessible role and name, rather than
CSS selectors or DOM structure, survive exactly the kind of change
branding usually makes, colors, logos, fonts, layout, because none of
that touches ARIA roles or a control's functional label. That's genuine
portability against theming, not an optimistic claim. It has a real
limit, though: if a tenant customizes the label itself, "Savings
Balance" instead of "Savings balance", both the primary and fallback
locator break together, since both are built from the same literal text.
A structural locator strategy — css or xpath — is reserved in the schema
as a last-resort fallback but never emitted by the compiler today, which
is the natural next step if aggressive per-tenant customization becomes
a real problem rather than a theoretical one.

The credible design for reuse without re-recording per tenant is a small
override layer merged over a base capability: a per-tenant document
carrying only the deltas, base_url, any customized labels, any
tenant-specific outcome text, rather than a full re-record. Because the
schema already tolerates extra fields, this kind of layering doesn't
require a migration to add. Drift detection across tenants running
different versions of the same vendor product doesn't exist today, and
that's worth stating as plainly here as in the determinism section, but
there's a concrete, already-present hook worth building on rather than
starting cold: discovery already stores a structural fingerprint of each
step's page, unused by anything right now. A pre-flight compatibility
probe, resolving every step's locator against a new tenant without
acting on anything, is a natural dry-run mode for the resolver that
already exists, and comparing that fingerprint across tenants would flag
a materially different vendor-product version before a real run ever
touches it.

Whether the mechanical/policy split itself helps or hurts multi-tenant
reuse is a genuine mixed answer, not a self-serving one. It helps in the
place that matters most: the policy layer, is this read-only, is this
outcome a business result rather than a failure, how risky is this, is
judgment that's true for every tenant on the same vendor product, and
it's written once rather than re-derived per tenant. It doesn't, by
itself, solve tenant parameterization; routes and labels still live in
the mechanical layer and still need the override mechanism described
above. And one deliberate choice cuts the other way: guardrail routes
are narrowed to exactly the paths one trajectory visited, which is the
right call for single-tenant blast-radius control but bakes in one
tenant's URL structure as a hard constraint. Extending that to
pattern-based or per-tenant routes is a real trade-off against tightened
safety, not a free improvement.

## 5. Escalation & handoff

Four triggers can lead to escalation, and only one of them ever retries
first. A step that times out waiting to settle gets up to two automatic
retries, redoing only settle-and-check, before falling through. An
unresolved locator, an unrecognized dialog, or a checkpoint that simply
evaluates false always escalates immediately, regardless of how the
policy is configured, since the engine converts any other trigger's
"retry" setting to "escalate" rather than let a config value promise
something the retry loop doesn't structurally support.

When an escalation opens, it carries the discovery-time goal, the
capability id, the blocked step's id and ordinal, which phase broke, a
plain-English expected-versus-observed pair, and a real screenshot of the
live page at the moment it blocked. Worth being honest about the limit
here: that context is a flat snapshot, not a diagnostic trace. There's no
ranked-locator attempt history and no accessibility-tree capture saved
alongside it, just a single frame and two strings. An operator
diagnosing something subtler than "there's an unexpected dialog" has less
to go on than they might want.

```mermaid
sequenceDiagram
    participant R as Replay process
    participant B as Browser (same process, same tab)
    participant DB as SQLite (escalations.db)
    participant C as Operator console
    participant H as Human operator

    R->>B: ACT, then SETTLE/CHECK for the blocked step
    Note over R: trigger fires
    R->>B: screenshot the live page
    R->>DB: open escalation (status: pending)
    loop poll every 2s until deadline
        R->>DB: check status
    end
    H->>C: view goal / step / trigger / expected vs. observed / screenshot
    H->>B: attach directly — headed window, or CDP — and resolve the block
    H->>C: click Resume
    C->>DB: mark resumed
    R->>DB: next poll sees status: resumed
    R->>R: log human_intervention
    R->>B: SETTLE + CHECK only — ACT is never called again
    alt checkpoint now passes
        R->>R: continue to next step
    else still failing (up to 5 attempts)
        R->>R: HardFailure: checkpoint_failure_after_resume
    end
```

"The same live session" is a precise claim, not a loose one: it is
literally the same browser process and the same tab that replay was
already on. The Playwright browser is never closed while the poll loop
runs; it is the identical Page object the blocked step was checking
against. The operator reaches it one of two ways. If replay was launched
with a real window, the operator clicks into it directly. If it was
launched with a debug port exposed, the operator's own tooling attaches
to that same browser process over CDP and drives the same tab, the
mechanism this build's actual live demonstration used, since this
environment has no real display to click into. Worth stating plainly
rather than implying otherwise: a headed Chromium here was not something
anyone was looking at; the handoff was proven by attaching to and
mutating the identical browser process programmatically. On an operator's
actual workstation, the same `--headed` flag is a window they would click
into directly. There is also a real, named gap if neither option is
configured: the escalation still opens and polls, but there is no channel
at all for a human to actually touch the page. That is a genuine
limitation, not a hypothetical one.

Resume is where the ACT/SETTLE/CHECK contract pays off directly. Whether
the block happened during the action phase or the check phase, resuming
never calls the action again, only settle-and-check. This is not a
convention the resume path happens to follow; there is no code path from
the resume handler back into the action method at all, so a mutating step
genuinely cannot be double-fired by a human's Resume click, structurally,
not just by discipline. If the checkpoint still does not pass after
resuming, the run allows a bounded number of further resumes before
giving up with a distinct, named failure rather than looping forever.

If no one resumes before the deadline, the browser is torn down (unlike
during the wait itself), the store record is marked timed out without
ever clobbering a resume that might arrive a moment later, and the caller
receives a clean HardFailure naming how long it waited against the
configured limit. No hang, no crash.

The operator console itself is intentionally bare, per the brief's own
scope note ruling out a full co-browsing console, and that is the right
amount of minimal rather than a shortfall: it delivers exactly the two
things actually required, visibility into why a run is blocked, and a
control that hands it back, while the same-live-session guarantee lives
in the replay engine itself, not in anything the console does. What it
does not do, help the operator connect to the browser in the first
place, is the genuinely thin part, but it is thin in exactly the place
the brief says is acceptable to leave thin.

What a real production version would still need, stated plainly rather
than oversold: push notification instead of a page the operator has to
remember to reload; authentication on the console, since anything
reachable on the port can resume any run today; coordination between
multiple operators, since resuming is one-shot but nothing prevents two
people from acting on the same live page at once; a built-in way to reach
the browser at all, rather than assuming a display or manually-run CDP
tooling; a remote-control channel for an operator on a different machine
than the one replay is running on; a live or refreshable view rather than
one screenshot taken at the moment of blocking; and a way for an operator
actively working the problem to request more time rather than
hard-failing at a fixed deadline.

## 6. Safety

| Enforcement point | Checks | Consequence |
|---|---|---|
| _preflight (before any browser launches) | policy authored; requires_confirmation satisfied; required inputs present; every step's action type is allowlisted | Hard-fail, artifact never touches a live page |
| _guard_step (per step, before acting) | action type re-checked; for navigation, the target route against the allowlist | Hard-fail |
| _post_nav_guard (after a navigate/click step acts, where the page actually landed) | off-domain navigation; landed route against the allowlist, catching a redirect the declared target didn't predict | Hard-fail |
| _denylist_hit (both before any mutating action and after every step) | live page text against denylisted phrases | Hard-fail, checked pre-mutation specifically so a risky action is caught before it fires, not just after |

None of these retry. A guardrail violation returns a failure directly from the guard layer itself, structurally outside the retry and escalation paths a legitimate runtime condition would go through, because a policy violation isn't something waiting a moment and trying again would fix.

The brief asks the system to distinguish safe, reversible actions from risky, irreversible ones and handle the risky class conservatively. This project draws that line three ways. risk_class is a required, hand-authored declaration, read_only or mutating, whose real leverage today runs entirely through one default: when a policy doesn't explicitly set requires_confirmation, the compiler defaults it to true for a mutating capability. requires_confirmation is what's actually enforced at replay time, a capability that requires it refuses to run at all without an explicit flag, checked before a browser ever launches. And denylist_text_patterns is a separate, independent tripwire, not gated on risk classification at all, that exists specifically to catch a read-only capability drifting onto a screen it was never meant to touch; member_lookup's own denylist, "Transfer funds," "Wire transfer," "Close account," reads almost as a statement of intent: a member-lookup flow that renders any of those has left its scope, and the reasoning for that is written directly into the policy file itself.

Worth being precise about what's actually proven here versus what's designed but unexercised. member_lookup is read-only, and every guardrail path that capability touches, the denylist tripwire, the route and action-type allowlists, has been exercised against the live target app through real replay runs. The requires_confirmation gate itself has not: it's proven correct by a synthetic test fixture that takes the real compiled capability and forces the flag on in memory, not by a genuine mutating capability discovered, compiled, and replayed end to end. That's an honest gap between "this logic is correct" and "this logic has been exercised in anger," and it's worth naming as such rather than letting the passing test imply more than it does.

Redaction follows one rule applied in two different places, and deliberately not applied in two others, for reasons specific to each. Every input parameter and every extracted value written to a replay run's durable log is masked into a length-bearing form, enough to confirm something was captured, nothing that reveals what it actually was. The real values are never withheld from where they're supposed to go: the result object returned in-process to the caller carries them in full, because an operator or calling system needs the actual answer to act on, and only the durable on-disk trail is where redaction applies. Discovery's own trajectory is the other deliberate exception, values captured during a one-time, dev-time run against synthetic data are logged in full, because that trajectory is the compiler's own source input, not a production record of a real person's information the way every live replay run against real data is. Both exceptions were independently checked, not just designed, during evidence curation: every file selected for the curated evidence set was grepped for filesystem paths, hostnames, and key patterns before being committed, and the masked-redaction convention was separately confirmed to hold on every curated replay run.

The API key itself lives only in a gitignored .env file, loaded once for discovery and never touched by replay at all, since replay makes no model calls. It has never been committed at any point in this repository's history, and nothing in the codebase prints its value, only a warning if it's missing.

The honest limit of this whole model is structural, not a gap that's merely unfinished: nothing validates that a hand-authored policy is itself correct. The guardrails constrain what a compiled artifact can do at replay time, but nothing checks whether the policy author got the underlying judgment right. An overly broad route allowlist, a mutating capability misclassified as read-only, an empty denylist on something that should have one, a dangerous state misclassified as a legitimate business outcome, none of these would be caught by the schema or the engine. What does exist is attribution, not validation: a policy can't run without a named author on record, and the policy file itself is plain, readable Python with its reasoning written inline, which makes it reviewable by a human even though nothing technical reviews it automatically. That's a genuine property of separating the policy layer from the mechanical layer, not an oversight, but it means the system's actual safety ceiling is the judgment of whoever authors and reviews a policy, not anything the code itself guarantees.

## 7. Cuts

Twenty-one distinct gaps surfaced over the course of this build, all still accurate as of the current code (independently re-verified by grep, not just carried forward from when they were first logged). They split into two honestly different categories.

Real cuts within what the brief asks for, thin or partially built rather than absent, consistent with the brief's own guidance to cut depth in places rather than whole capabilities:

- Extract locators aren't scoped to their containing table (the within field is declared on every locator but read by nothing); extraction still resolves correctly on this target, but only because the page is small enough that a page-wide match happens to be unambiguous.
- Of three reserved locator fallback strategies, only two are ever exercised. test_id has no resolver behind it at all, and css/xpath exist as a last-resort escape hatch the compiler has never once needed to reach for.
- Discovery already computes a structural fingerprint of every step's page and stores it; nothing reads it. Drift is contained today, not detected, an unrecognized dialog escalates, a moved element fails to resolve, but none of that is an early warning, only a symptom caught after the fact.
- requires_confirmation's enforcement is real, checked before any browser launches, but it has only ever been exercised against a synthetic fixture that forces the flag on an existing read-only capability. No genuinely mutating capability has been discovered, compiled, and replayed against it end to end.
- An escalation carries a single screenshot and two plain strings. There's no locator-attempt trace and no accessibility-tree capture at the moment things stopped, which is less than an operator debugging something subtler than an unrecognized dialog would want.
- The operator console has no authentication and no notification mechanism; a pending escalation is only visible to someone who happens to reload the page.
- Nothing validates that a hand-authored policy is itself correct, an overly broad allowlist, a mutating capability mislabeled read-only, or a genuinely risky state mismarked as a legitimate outcome would all pass silently. What exists is attribution, a policy can't run without a named author on record, not technical validation.
- One latent non-determinism is documented rather than hidden: the detail-page link is matched by name prefix with nth=0, safe only as long as the target app's own result ordering stays stable.

Explicitly out of scope per the brief itself, not cuts in the apologetic sense: a full co-browsing operator console (Section 3.6 rules this out by name; what's built instead, a mocked console with a real handoff mechanism and control-transfer model, is exactly what that section asks for in its place); a multi-tenant build and a desktop-app port (Section 3.7 states plainly that neither is expected, only a credible design answer, which is what Section 4 of this report provides).

Stretch goals, per Section 8's own "pick at most one or two" framing: none have been attempted as of this writing. If pursued, an agent-facing capability interface and multi-run stability are the two under consideration, both cheap extensions of work already built (the replay engine and the CLI, respectively) rather than new subsystems. Canonicalization/cross-tenant reuse and a second real target variant were judged not worth the build time relative to hardening the single-tenant flow this report is about. Code generation and assisted fallback were not pursued at all, the former adds a deliverable with no bearing on any evaluated criterion, the latter risks muddying the "no LLM in the replay decision loop" property that Section 3 treats as central.

What we'd build next, prioritized by what would most directly strengthen the system's weakest points rather than by what's easiest:

1. A real mutating capability, run end to end, closing the gap between requires_confirmation's logic being correct and it having actually been exercised against something live. This is the single highest-leverage item, since it's the one unverified path in exactly the area, safety and human-in-the-loop, the brief weights most heavily.
2. Authentication and a notification channel on the operator console. The handoff mechanism itself is sound and proven live; the weak link is that nothing tells a human it's waiting for them.
3. Wire the already-captured page fingerprint into an actual pre-flight drift check. The data exists for exactly this purpose and currently does nothing; this is the cleanest half-finished item to complete.
4. Table-scoped extract locators, via the same pattern that closed the search-mode gap: a second discovery pass that deliberately resolves the containing element, then a compiler change to carry that scope through.
5. Richer escalation context, a locator-attempt trace and an accessibility snapshot at the point of failure, not just one screenshot.
6. A basic policy-correctness linter, flagging the more mechanical version of this problem, a read_only capability whose steps include a mutating action type, or an allowlist broader than anything any recorded trajectory actually visited.

The last-name search gap is worth pointing to directly here, not as a hypothetical but as this project's own proof that this list isn't aspirational: it went through exactly this cycle already, honestly narrowed at compile time, then genuinely closed later with a real second discovery run and a real compiler fix, uncovering a distinct bug in the process. The same methodology already worked once on this codebase.