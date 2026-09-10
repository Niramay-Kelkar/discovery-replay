"""Hand-authored policy layer for the ``member_lookup`` capability.

Consumed by ``agent/compile.py`` alongside the Phase 3 discovery
trajectory ``disc-20260909-182538``. The compiler fills the mechanical
layer from that trajectory; everything in this file is the policy layer,
authored by hand.

Why this is separate from the trajectory
----------------------------------------
The disc-20260909-182538 run succeeded: it looked up member M1001, read
Alice Nguyen's name and $18,750.42 balance, and stopped. By definition
it never saw the access-denied page, never saw "no such member", never
hit the supervisor-review interstitial or the slow detail page. It
therefore has *no evidence* about:

* which non-happy results are legitimate answers vs. bugs
  (``expected_outcomes``),
* whether this operation is safe to run unattended (``risk_class`` /
  ``requires_confirmation``),
* how many times to retry, how long to wait for a human, and what to do
  on each failure trigger (``escalation_policy``),
* what strings on a page mean "you are on the wrong screen, stop"
  (``denylist_text_patterns``).

Those are authored below from knowledge of the target app
(``target_app/README.md`` documents every seeded hostile case; the three
expected outcomes here were verified against the live app during the
schema work and match ``schema/example_artifact.json``).

Trajectory gap: the ``search_field`` radio
------------------------------------------
The discovery run typed "M1001" and clicked "Look Up" without ever
touching the "Member ID" / "Last name" radio -- "Member ID" is
pre-checked and valid by default for an ID search (the resulting URL was
``/search?field=member_id&q=M1001``). So the run only ever exercised
**one** of the two search modes.

The compiler does **not** invent a "click the search-field radio" step
to cover the other mode -- a fabricated step that was never resolved
against the live tree is exactly the kind of thing that breaks in
production. Consequences, by design:

* ``INPUT_BINDINGS`` below declares only ``search_term``. There is no
  ``search_field`` input. The compiled capability searches by member ID,
  because that is the only path discovery proved.
* ``schema/example_artifact.json`` (hand-authored) *does* have a
  ``choose_search_field`` step and a ``search_field`` enum input -- that
  is a human asserting both modes work, which is legitimate for a
  hand-authored artifact but not something a compiler may do from one
  run.

To get a full two-mode capability, do one of:

1. a second discovery run whose goal forces the "Last name" radio path,
   then compile both trajectories together; or
2. hand-author the ``choose_search_field`` step and the ``search_field``
   enum input directly onto the compiled artifact, reviewed as a
   deliberate act.

This compiler takes neither shortcut automatically: it emits the
narrower, honest single-mode capability and records the gap in the
``*.notes.md`` sidecar.
"""
from __future__ import annotations

from agent.compile import InputBinding, PolicySpec
from agent.models import (
    DetectionRule,
    EscalationPolicy,
    ExpectedOutcome,
    InputParam,
    ParamType,
)

# --- mechanical: what the single discovered input actually was ---------------

INPUT_BINDINGS: list[InputBinding] = [
    InputBinding(
        param=InputParam(
            name="search_term",
            type=ParamType.STRING,
            required=True,
            description="The member ID to look up (search is by member ID).",
            example="M1001",
        ),
        discovered_value="M1001",
    ),
]

# --- policy: authored from knowledge of the target app's failure modes ------

_EXPECTED_OUTCOMES = [
    ExpectedOutcome(
        code="MEMBER_NOT_FOUND",
        description="No member matched the search term.",
        classification="business_outcome",
        terminal=True,
        detection=DetectionRule(
            kind="any_of",
            rules=[
                DetectionRule(kind="text_present",
                              text="No members matched that search."),
                DetectionRule(kind="text_present", text="No such member."),
            ],
        ),
    ),
    ExpectedOutcome(
        code="ACCESS_DENIED",
        description=(
            "The teller profile is not authorized to view this member's record."
        ),
        classification="business_outcome",
        terminal=True,
        detection=DetectionRule(
            kind="any_of",
            rules=[
                DetectionRule(kind="http_status", status=403),
                DetectionRule(kind="aria_visible", role="alert",
                              name="not authorized", exact=False),
            ],
        ),
    ),
    ExpectedOutcome(
        code="SUPERVISOR_REVIEW_REQUIRED",
        description=(
            "The record is flagged for supervisor review; a confirmation "
            "interstitial blocks the detail page. Replay reports this rather "
            "than clicking through."
        ),
        classification="business_outcome",
        terminal=True,
        detection=DetectionRule(
            kind="any_of",
            rules=[
                DetectionRule(kind="aria_visible", role="alertdialog",
                              name="Supervisor review required", exact=False),
                DetectionRule(kind="text_present",
                              text="Supervisor review required"),
            ],
        ),
    ),
]

POLICY_SPEC = PolicySpec(
    capability_id="member_lookup",
    version="1.0.0",
    description=(
        "Look up a bank member by member ID and return their record and "
        "current savings balance. (Member-ID search only -- see this module's "
        "docstring for the last-name path.)"
    ),
    app="acme-teller-console",

    risk_class="read_only",          # reads records, changes nothing
    requires_confirmation=False,     # read-only, safe to run unattended
    expected_outcomes=_EXPECTED_OUTCOMES,
    escalation_policy=EscalationPolicy(
        max_retries_per_step=2,
        retry_backoff_seconds=1.5,
        human_handoff_timeout_seconds=900.0,
        on_step_timeout="retry",           # covers the injected ~4s slow load
        on_hard_failure="escalate",
        on_unrecognized_dialog="escalate",  # a *recognized* dialog is an outcome
        on_checkpoint_failure="escalate",
    ),
    denylist_text_patterns=[
        # a read-only member lookup that suddenly renders any of these is on
        # the wrong page -- abort, do not click.
        "Transfer funds",
        "Wire transfer",
        "Close account",
    ],
    # No extra allowlist routes: the last-name search path is deliberately
    # NOT pre-authorized here. It shares the /search and /member/* routes the
    # ID path already visited, so nothing extra is needed even once it is
    # added -- but if it ever needed a new route, that route would be listed
    # here with a justification, not wildcarded in.
    extra_allowlist_routes=[],
    max_steps=20,
    # The disc-20260909-182538 run captured the name field as "member_name".
    # The capability contract (and schema/example_artifact.json) calls it
    # "full_name". This is a rename of a label the policy author controls,
    # not a claim about data the run did not prove. Every output the run
    # produced must be listed here or compilation fails.
    output_name_mapping={
        "member_name": "full_name",
        "savings_balance": "savings_balance",
    },
    known_gaps=[
        "The 'search_field' radio (Member ID / Last name) was never clicked "
        "during discovery -- 'Member ID' is pre-checked and was valid for the "
        "M1001 lookup. This capability therefore searches by member ID only. A "
        "two-mode capability needs either a second discovery run forcing the "
        "'Last name' path, or a hand-authored 'choose_search_field' step "
        "reviewed as a deliberate act. The compiler adds neither: it emits the "
        "narrower single-mode capability. (Contrast schema/example_artifact.json, "
        "which is hand-authored and does assert both modes.)",
    ],
    policy_authored_by="niramay (hand-authored policy for disc-20260909-182538)",
    notes=(
        "Expected outcomes verified against target_app during schema work. "
        "search_field radio path not exercised by discovery; see module "
        "docstring."
    ),
)
