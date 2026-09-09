"""Construct one hand-authored Capability and serialize it.

This is a worked example alongside the abstract models: the target
app's member-lookup flow, authored directly via ``agent/models.py`` and
written to ``schema/example_artifact.json``. It is not compiler output --
it exists so the schema has a concrete instance to read against
``schema/DESIGN.md``.

    python schema/build_example.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.models import (  # noqa: E402
    Capability,
    Checkpoint,
    DetectionRule,
    DiscoveryProvenance,
    EscalationPolicy,
    ExpectedOutcome,
    Guardrails,
    InputParam,
    LocatorStrategy,
    OutputParam,
    ParamType,
    SettleSpec,
    Step,
    Target,
)

OUT_PATH = os.path.join(os.path.dirname(__file__), "example_artifact.json")

RESULTS_TABLE = "table[name='Member search results']"
RECORD_TABLE = "table[name^='Member record for']"


def _extract_field_step(ordinal: int, output_name: str, label: str,
                        terminal: bool) -> Step:
    """One 'read a labelled cell into an output' step.

    Target the row by its *label* (rowheader), never the value -- the
    artifact must work for any member. Replay reads the cell paired with
    the rowheader.
    """
    checkpoint = (
        Checkpoint(
            kind="outputs_non_empty",
            outputs=[
                "member_id",
                "full_name",
                "savings_balance",
                "date_of_birth",
                "address",
            ],
            description="all page-extracted outputs are populated",
        )
        if terminal
        else Checkpoint(
            kind="element_visible",
            locator=LocatorStrategy(
                kind="aria_role", rank=1, role="rowheader", name=label
            ),
        )
    )
    return Step(
        ordinal=ordinal,
        id=f"extract_{output_name}",
        description=f"Read '{label}' from the member record",
        action="extract",
        output_name=output_name,
        locators=[
            LocatorStrategy(
                kind="aria_role", rank=1, role="rowheader", name=label,
                within=RECORD_TABLE,
                note="value is the cell paired with this rowheader",
            ),
            LocatorStrategy(
                kind="text_label", rank=2, label=label, within=RECORD_TABLE
            ),
        ],
        settle=SettleSpec(wait_for="dom_stable", max_wait_seconds=2),
        checkpoint=checkpoint,
    )


def build() -> Capability:
    steps = [
        Step(
            ordinal=1,
            id="open_search",
            description="Open the teller search page",
            action="navigate",
            target_route="/",
            settle=SettleSpec(wait_for="dom_stable", max_wait_seconds=5),
            checkpoint=Checkpoint(
                kind="element_visible",
                locator=LocatorStrategy(
                    kind="aria_role", rank=1, role="textbox",
                    name="Search term (member ID or last name)",
                ),
            ),
        ),
        Step(
            ordinal=2,
            id="choose_search_field",
            description="Select whether to search by member ID or last name",
            action="click",
            locators=[
                LocatorStrategy(
                    kind="aria_role", rank=1, role="radio",
                    name="{{search_field}}", exact=True,
                ),
                LocatorStrategy(
                    kind="text_label", rank=2, label="{{search_field}}",
                ),
            ],
            settle=SettleSpec(wait_for="dom_stable", max_wait_seconds=2),
            checkpoint=Checkpoint(
                kind="element_visible",
                locator=LocatorStrategy(
                    kind="aria_role", rank=1, role="radio",
                    name="{{search_field}}",
                ),
                description="the chosen radio is present (replay also asserts checked)",
            ),
        ),
        Step(
            ordinal=3,
            id="enter_search_term",
            description="Type the member ID or last name to look up",
            action="fill",
            input_name="search_term",
            value_template="{{search_term}}",
            locators=[
                LocatorStrategy(
                    kind="aria_role", rank=1, role="textbox",
                    name="Search term (member ID or last name)",
                ),
                LocatorStrategy(
                    kind="text_label", rank=2,
                    label="Search term (member ID or last name)",
                ),
            ],
            settle=SettleSpec(wait_for="dom_stable", max_wait_seconds=2),
            checkpoint=Checkpoint(
                kind="element_visible",
                locator=LocatorStrategy(
                    kind="aria_role", rank=1, role="textbox",
                    name="Search term (member ID or last name)",
                ),
            ),
        ),
        Step(
            ordinal=4,
            id="submit_search",
            description="Submit the search",
            action="click",
            locators=[
                LocatorStrategy(
                    kind="aria_role", rank=1, role="button", name="Look Up"
                ),
                LocatorStrategy(kind="text_label", rank=2, text="Look Up"),
            ],
            settle=SettleSpec(wait_for="network_idle", max_wait_seconds=8),
            checkpoint=Checkpoint(
                kind="any_of",
                description=(
                    "results table appeared, OR a legitimate 'not found' answer"
                ),
                checks=[
                    Checkpoint(
                        kind="element_visible",
                        locator=LocatorStrategy(
                            kind="aria_role", rank=1, role="table",
                            name="Member search results",
                        ),
                    ),
                    Checkpoint(
                        kind="outcome_matched", outcome_code="MEMBER_NOT_FOUND"
                    ),
                ],
            ),
        ),
        Step(
            ordinal=5,
            id="open_member_detail",
            description="Open the first matching member's detail page",
            action="click",
            locators=[
                LocatorStrategy(
                    kind="aria_role", rank=1, role="link",
                    name="Open detail for", exact=False,
                    within=RESULTS_TABLE, nth=0,
                ),
                LocatorStrategy(
                    kind="text_label", rank=2, text="View",
                    within=RESULTS_TABLE, nth=0,
                ),
            ],
            settle=SettleSpec(
                wait_for="network_idle",
                max_wait_seconds=12,  # above the app's injected ~4s slow load
            ),
            checkpoint=Checkpoint(
                kind="any_of",
                description=(
                    "record table appeared, OR a recognized business outcome "
                    "(access denied / supervisor review) -- not a hung page"
                ),
                checks=[
                    Checkpoint(
                        kind="element_visible",
                        locator=LocatorStrategy(
                            kind="aria_role", rank=1, role="table",
                            name="Member record for", exact=False,
                        ),
                    ),
                    Checkpoint(kind="outcome_matched"),  # any recognized outcome
                ],
            ),
        ),
        _extract_field_step(6, "member_id", "Member ID", terminal=False),
        _extract_field_step(7, "full_name", "Full name", terminal=False),
        _extract_field_step(8, "date_of_birth", "Date of birth", terminal=False),
        _extract_field_step(9, "address", "Address", terminal=False),
        _extract_field_step(10, "savings_balance", "Savings balance", terminal=True),
    ]

    return Capability(
        capability_id="member_lookup",
        version="1.0.0",
        description=(
            "Look up a credit-union member by member ID or last name and "
            "return their record and current savings balance."
        ),
        target=Target(
            # Fully fictional. Not a real core-banking product or vendor.
            app="acme-teller-console",
            base_url="http://127.0.0.1:5001",
            entry_route="/",
        ),
        # --- mechanical layer ---
        inputs=[
            InputParam(
                name="search_field",
                type=ParamType.STRING,
                required=True,
                description="Which field to search on.",
                allowed_values=["Member ID", "Last name"],
                example="Member ID",
            ),
            InputParam(
                name="search_term",
                type=ParamType.STRING,
                required=True,
                description="The member ID or last name to look up.",
                example="M1001",
            ),
        ],
        outputs=[
            OutputParam(name="member_id", type=ParamType.STRING,
                        description="The member's ID."),
            OutputParam(name="full_name", type=ParamType.STRING,
                        description="The member's full name."),
            OutputParam(name="savings_balance", type=ParamType.MONEY,
                        description="Current savings balance, as rendered."),
            OutputParam(name="date_of_birth", type=ParamType.DATE,
                        description="The member's date of birth."),
            OutputParam(name="address", type=ParamType.STRING,
                        description="The member's mailing address."),
            OutputParam(
                name="outcome_code", type=ParamType.STRING, required=False,
                description=(
                    "Always set by replay: 'SUCCESS' on the happy path, "
                    "otherwise the matched expected-outcome code."
                ),
            ),
        ],
        steps=steps,
        # --- policy layer (authored separately, not derived) ---
        risk_class="read_only",
        requires_confirmation=False,
        expected_outcomes=[
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
                        DetectionRule(kind="text_present",
                                      text="No such member."),
                    ],
                ),
            ),
            ExpectedOutcome(
                code="ACCESS_DENIED",
                description=(
                    "The teller profile is not authorized to view this "
                    "member's record."
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
                    "The record is flagged for supervisor review; a "
                    "confirmation interstitial blocks the detail page. "
                    "Replay reports this rather than clicking through."
                ),
                classification="business_outcome",
                terminal=True,
                detection=DetectionRule(
                    # The alertdialog carries an accessible name, but the
                    # name-computation path (aria-labelledby to a <td>) is
                    # less certain across engines than a plain text match,
                    # so accept either signal.
                    kind="any_of",
                    rules=[
                        DetectionRule(
                            kind="aria_visible", role="alertdialog",
                            name="Supervisor review required", exact=False,
                        ),
                        DetectionRule(
                            kind="text_present",
                            text="Supervisor review required",
                        ),
                    ],
                ),
            ),
        ],
        guardrails=Guardrails(
            allowlist_routes=["/", "/search", "/member/*"],
            allowlist_action_types=["navigate", "click", "fill", "extract"],
            denylist_text_patterns=[
                "Transfer funds", "Wire transfer", "Close account",
            ],
            max_steps=20,
            forbid_offdomain_navigation=True,
        ),
        escalation_policy=EscalationPolicy(
            max_retries_per_step=2,
            retry_backoff_seconds=1.5,
            human_handoff_timeout_seconds=900,
            on_step_timeout="retry",
            on_hard_failure="escalate",
            on_unrecognized_dialog="escalate",
            on_checkpoint_failure="escalate",
        ),
        # --- provenance ---
        discovery=DiscoveryProvenance(
            run_id="disc-2026-09-09-member-lookup-01",
            model="claude-sonnet-5",
            goal=(
                "Look up member M1001 and report their savings balance, "
                "starting from the teller console home page."
            ),
            completed_at="2026-09-09T15:20:00Z",
        ),
        policy_authored_by="niramay (hand-authored example)",
    )


if __name__ == "__main__":
    cap = build()
    with open(OUT_PATH, "w") as fh:
        fh.write(cap.to_json())
        fh.write("\n")
    print(f"Wrote {OUT_PATH} ({len(cap.steps)} steps, "
          f"{len(cap.expected_outcomes)} expected outcomes)")
