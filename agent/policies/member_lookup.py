"""Hand-authored policy layer for the ``member_lookup`` capability.

Consumed by ``agent/compile.py`` alongside the discovery trajectory
``disc-20260910-084016``. The compiler fills the mechanical layer from
that trajectory; everything in this file is the policy layer, authored
by hand.

Why this is separate from the trajectory
----------------------------------------
The disc-20260910-084016 run succeeded: it switched the search field to
"Last name", looked up member Okafor, opened James Okafor's record, read
his name and $2,219.75 balance, and stopped. By definition it never saw
the access-denied page, never saw "no such member", never hit the
supervisor-review interstitial or the slow detail page. It therefore
has *no evidence* about:

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

The search-field radio: gap closed
----------------------------------
An earlier discovery run (``disc-20260909-182538``) drove an ID lookup
and never touched the "Member ID" / "Last name" radio -- "Member ID" is
pre-checked, so an ID search works without it. That trajectory only ever
exercised one of the two search modes, and the compiled capability was
honestly narrowed to ID-only with the gap recorded here.

``disc-20260910-084016`` closes that gap the right way -- with a real
run, not a fabricated step. Its goal forced the agent to click the
"Last name" radio before searching, so the trajectory now contains a
genuine, live-resolved ``click`` on the radio (resolved role ``radio``,
accessible name ``"Last name"``). The compiler:

* templates that resolved name against the ``search_field`` InputBinding
  below, emitting a ``click_search_field`` step whose locator is
  ``role=radio name={{search_field}}`` -- the same shape the
  hand-authored ``schema/example_artifact.json`` uses, but now derived
  mechanically from an observed element rather than asserted;
* declares ``search_field`` as a closed enum input (``"Member ID"`` /
  ``"Last name"``) -- the two accessible names the radio actually
  exposes.

Replay picks the radio by the ``search_field`` value it is handed, so
the one compiled step covers both modes. The capability is now genuinely
two-mode, proven end to end (see ``*.notes.md`` and BUILD_LOG.md for the
both-modes replay verification), not two-mode by hand-assertion.

``extra_allowlist_routes`` and the member detail route
-----------------------------------------------------
Route narrowing keys off path segments that equal a *discovered input
value*. In this run the search term was ``"Okafor"`` (a last name), so
the matched member's id ``M1004`` in ``/member/M1004`` is not an input
value and does not get generalized to ``/member/*`` automatically -- the
compiler would ship the literal ``/member/M1004``. That id is data the
run happened to land on, not a fixed route, so ``/member/*`` is added
here explicitly as an authored, justified allowlist entry (every seeded
member detail page lives under ``/member/<id>``; replay must reach the
one its own search returns, whichever member that is).
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

# --- mechanical: what the discovered inputs actually were -------------------
#
# Order here is the order the inputs appear in the compiled artifact;
# it matches schema/example_artifact.json (search_field, then search_term).

INPUT_BINDINGS: list[InputBinding] = [
    InputBinding(
        param=InputParam(
            name="search_field",
            type=ParamType.STRING,
            required=True,
            description=(
                "Which field to search on -- the accessible name of the "
                "search-field radio to select before searching."
            ),
            example="Last name",
            allowed_values=["Member ID", "Last name"],
        ),
        # the accessible name of the radio the disc-20260910-084016 run
        # clicked; the compiler templates this literal to {{search_field}}
        discovered_value="Last name",
    ),
    InputBinding(
        param=InputParam(
            name="search_term",
            type=ParamType.STRING,
            required=True,
            description=(
                "The member ID or last name to look up, matching the "
                "search_field selection."
            ),
            example="Okafor",
        ),
        discovered_value="Okafor",
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
    version="1.1.0",
    description=(
        "Look up a bank member by member ID or by last name and return their "
        "record and current savings balance. The search_field input selects "
        "which mode; both are exercised by the compiled steps."
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
    # The last-name run reaches /member/<id> where <id> is the id its own
    # search returned (M1004 this run), not an input value -- so it is not
    # auto-generalized to /member/*. Authorize the family of member detail
    # routes explicitly; replay must reach whichever member it looks up.
    extra_allowlist_routes=["/member/*"],
    max_steps=20,
    # disc-20260910-084016 captured the outputs under their contract names
    # already ("full_name", "savings_balance"), so this is an identity map --
    # but every produced output must still be listed or compilation fails.
    output_name_mapping={
        "full_name": "full_name",
        "savings_balance": "savings_balance",
    },
    known_gaps=[
        "RESOLVED (was: the search_field radio was never exercised). "
        "disc-20260910-084016 drives the 'Last name' radio for real, so the "
        "compiled capability now has a mechanically-derived choose-search-field "
        "step and a closed-enum search_field input covering both modes. Verified "
        "by replaying the recompiled artifact against the live app with "
        "search_field='Last name' and search_field='Member ID' -- see the "
        "'What changed in this recompile' section of the *.notes.md sidecar "
        "and BUILD_LOG.md.",
    ],
    policy_authored_by="policy_team (hand-authored policy for disc-20260910-084016)",
    notes=(
        "Expected outcomes verified against target_app during schema work. "
        "search_field radio path now exercised by discovery "
        "(disc-20260910-084016); supersedes the ID-only disc-20260909-182538."
    ),
)
