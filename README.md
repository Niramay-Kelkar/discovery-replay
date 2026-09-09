# discovery-replay

A computer-use automation system that gives AI agents hands inside
legacy back-office software that has no API. An LLM discovers how to
complete a task once by driving a real UI, that discovery is compiled
into a typed, versioned, replayable capability artifact, and that
artifact is later replayed deterministically, with no LLM in the
decision loop, complete with error handling, safety guardrails, and
human escalation.

Built for an interface.ai take-home assignment. Full design reasoning
is in [REPORT.md](./REPORT.md).

## How it works

1. **Discovery.** Give the agent a goal in plain English and a
   starting point. It observes the screen, decides on an action,
   performs it, and repeats until the goal is met. This run is slow
   and non-deterministic, and it happens once per capability.
2. **Compile.** The successful run is turned into a structured
   artifact: typed inputs and outputs, ordered steps with ranked
   locator strategies, per-step checkpoints, declared business
   outcomes, and a safety and escalation policy.
3. **Replay.** Given the artifact and a new set of inputs, the same
   flow runs again with no model involved in any decision. Fast,
   cheap, and repeatable. It detects and reports runtime conditions
   such as validation errors, "not found" results, and permission
   denials as distinct outcomes rather than crashes.
4. **Escalate.** If replay hits a condition it cannot recover from,
   control of the live session is handed to a human operator, who can
   act and then hand control back.

## Setup

*(to be filled in once the environment/dependencies are in place)*

## Demo path

*(exact commands to run discovery on a goal, then replay the resulting
artifact — to be added once the core loop is built)*