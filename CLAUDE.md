# interface.ai take-home — Computer-Use Automation System (discovery-replay)

This file is a running design log for this project: the decisions made
and the reasoning behind them, captured as they were made, starting
before any code existed. It doubles as project context for AI-assisted
development (Claude Code) and as supporting evidence for the reasoning
behind REPORT.md.

For Claude Code specifically: treat this as the source of truth for
"why," not just "what." Read this in full before making architectural
changes.

## The assignment, in one sentence

Build the layer that gives an AI agent hands inside legacy bank software
with no API: an LLM discovers how to complete a task once by driving the
real UI, that discovery is compiled into a typed, versioned, replayable
"capability" artifact, and that artifact is later replayed
deterministically — no LLM in the decision loop — with proper error
handling, safety guardrails, and human escalation.

Full brief: see the assignment PDF (Assignment A — Computer-Use
Automation System). Do not deviate from the required deliverable paths
and structure.

## The core mental model

Two separate programs share one data structure:

- **Discovery** (`agent.discover`): LLM-driven observe → decide → act
  loop against a live browser. Slow, non-deterministic, run once per
  capability. Produces a typed artifact.
- **Replay** (`agent.replay`): given a saved artifact + input params,
  executes the recorded steps against the live UI with the LLM
  completely out of the decision loop. Fast, deterministic, run on
  every production invocation.

The artifact is the product — a capability contract, not a macro
recording: typed inputs, typed outputs, ordered steps with ranked
locator fallbacks, per-step checkpoints, declared expected business
outcomes, a risk classification, guardrails, and an escalation policy.

## What's actually being graded (in weighted order)

1. System design — the artifact schema and replay contract are called
   out as a focal point of the evaluation.
2. Correctness of the core loop.
3. Robustness & error handling — three-way outcome taxonomy (success /
   business outcome / hard failure).
4. Human-in-the-loop escalation — must transfer control of the SAME
   live session, not a fresh one.
5. Generalization to the real environment (design-only, in REPORT.md).
6. Safety & data handling.
7. Code quality, then Communication.

## Context this build carries forward

This is a second, independent build of the same public brief, for a
different role at the same company. The core design below is
deliberately similar to that earlier work — same mental model, same
schema shape — because it held up under real scrutiny (an onsite CTO
review). What's different this time is depth in three specific places
that were exposed as soft under live questioning last round, and this
build should not repeat those gaps:

1. **Tool vocabulary.** The set of actions the discovery LLM can call
   must be a deliberate, small, named set — decided and written down
   here before the agent loop is built, not left implicit in the
   prompt. Be able to justify inclusion/exclusion of each action.
2. **Escalation state machine.** Pause / notify / timeout / resume as
   explicit named states, documented here as a diagram before any code,
   not derived after the fact from control flow.
3. **Outcome detection.** Kept in its own clearly named module, not
   folded into the replay loop, so it can be pointed to and explained
   directly.

## Standing implementation lessons (apply from the start, not after a bug)

- Never assume an accessibility role/attribute exists — verify against
  the live accessibility tree before writing a locator strategy against
  it.
- Every step is ACT (resolve + act) then SETTLE then CHECK
  (dialog check + checkpoint). Retries — automatic or human-resumed —
  only ever redo SETTLE/CHECK, never re-run the action.
- Compile checkpoints and extract locators from a discovered field's
  *label*, not its literal value — a compiled artifact must work for
  any valid input, not just the one used during discovery.
- `guardrails.allowlist_routes` on a compiled artifact should be
  narrowed to routes actually visited during the successful trajectory,
  not left as discovery-time's wide-open scope.
- Redaction must be verified end-to-end (evidence files, logs, any
  read-only interface), not assumed from the redaction function's
  existence.
- `.env` / API keys never touch a terminal transcript, commit, or log
  line.

## Commit conventions

- No AI-attribution trailers or footers of any kind (no
  "Co-Authored-By: Claude", no "Generated with Claude Code", no
  emoji signatures). Commits are authored under Niramay's name only.
- Commit messages should read like a competent engineer wrote them:
  clear about what changed and why, but not a jargon dump. State the
  behavior or capability that changed, not implementation minutiae.
  - Good: "Add locator strategy for label-less form fields (hostile
    table layout has no <label> association)"
  - Too technical/noisy: "feat(locators): implement label_proximity
    LOCATOR_REGISTRY.register decorator w/ scope.locator tr:has-text
    XPath traversal fallback"
  - Too vague: "fix stuff" / "wip" / "updates"
- One logical change per commit. Don't bundle unrelated fixes into a
  single commit "to save time" — this is a small project, there's no
  cost to committing granularly, and it makes the history legible.

## Open / not yet decided

(fill in as decisions arise)