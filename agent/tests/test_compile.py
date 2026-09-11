"""Compiler tests: Trajectory -> Capability.

Offline: no browser, no API. Uses the real two-mode discovery trajectory
on disk (``disc-20260910-084016`` -- drives the "Last name" radio) plus a
couple of hand-built ones for the failure paths.
"""
import json
from pathlib import Path

import pytest

from agent.compile import (
    CompileError,
    PolicySpec,
    compile_capability,
)
from agent.models import EscalationPolicy
from agent.policies.member_lookup import INPUT_BINDINGS, POLICY_SPEC
from agent.trajectory import Trajectory

REAL_TRAJ = (
    Path(__file__).resolve().parents[2]
    / "evidence/runs/disc-20260910-084016/trajectory.json"
)


def _real_trajectory() -> Trajectory:
    return Trajectory.model_validate_json(REAL_TRAJ.read_text())


def _minimal_policy(**overrides) -> PolicySpec:
    base = {
        "capability_id": "c",
        "version": "1.0.0",
        "description": "d",
        "app": "a",
        "risk_class": "read_only",
        "expected_outcomes": POLICY_SPEC.expected_outcomes,
        "escalation_policy": EscalationPolicy(),
        "extra_allowlist_routes": ["/member/*"],
        "output_name_mapping": {
            "full_name": "full_name",
            "savings_balance": "savings_balance",
        },
        "policy_authored_by": "test",
    }
    base.update(overrides)
    return PolicySpec(**base)


# --- happy path -------------------------------------------------------------

def test_compiles_the_real_trajectory():
    cap, notes = compile_capability(_real_trajectory(), POLICY_SPEC, INPUT_BINDINGS)
    assert cap.capability_id == "member_lookup"
    assert cap.version == "1.1.0"
    # 6 trajectory action steps (click, type, click, click, extract, extract)
    # + 1 synthesized entry navigation; `done` is dropped.
    assert [s.action for s in cap.steps] == [
        "navigate", "click", "fill", "click", "click", "extract", "extract"
    ]
    assert cap.steps[0].id == "open_entry"
    assert cap.policy_authored_by
    assert cap.discovery.run_id == "disc-20260910-084016"


def test_search_field_radio_step_is_compiled_from_a_real_click():
    cap, _ = compile_capability(_real_trajectory(), POLICY_SPEC, INPUT_BINDINGS)
    radio = cap.steps[1]
    assert radio.id == "click_search_field"
    assert radio.action == "click"
    loc = radio.locators[0]
    assert loc.role == "radio"
    assert loc.name == "{{search_field}}"        # templated, not "Last name"
    assert loc.exact is True


def test_search_field_is_a_closed_enum_input():
    cap, _ = compile_capability(_real_trajectory(), POLICY_SPEC, INPUT_BINDINGS)
    assert [i.name for i in cap.inputs] == ["search_field", "search_term"]
    sf = next(i for i in cap.inputs if i.name == "search_field")
    assert sf.allowed_values == ["Member ID", "Last name"]
    assert sf.required is True


def test_fill_value_is_parameterized_not_hardcoded():
    cap, _ = compile_capability(_real_trajectory(), POLICY_SPEC, INPUT_BINDINGS)
    fill = next(s for s in cap.steps if s.action == "fill")
    assert fill.value_template == "{{search_term}}"
    assert fill.input_name == "search_term"
    # the discovered literals appear in no step locator or value template
    # (InputParam.example / human-readable descriptions may still carry
    # them -- those are not what replay resolves against).
    for s in cap.steps:
        assert "Okafor" not in (s.value_template or "")
        for loc in s.locators:
            resolvable = loc.model_dump(exclude_none=True)
            resolvable.pop("note", None)  # note quotes the original for audit
            assert "Okafor" not in json.dumps(resolvable)


def test_extract_locators_come_from_label_not_value():
    cap, _ = compile_capability(_real_trajectory(), POLICY_SPEC, INPUT_BINDINGS)
    balance = next(s for s in cap.steps if s.output_name == "savings_balance")
    assert balance.locators[0].name == "Savings balance"
    assert balance.locators[0].role == "rowheader"
    assert "$2,219.75" not in cap.to_json()


def test_link_name_with_member_data_is_generalized_to_a_substring():
    cap, notes = compile_capability(_real_trajectory(), POLICY_SPEC, INPUT_BINDINGS)
    link = cap.steps[4]  # click open detail
    assert link.locators[0].name == "Open detail for"
    assert link.locators[0].exact is False
    assert any("substring" in g for g in notes.generalizations)


def test_last_name_inside_full_name_does_not_defeat_truncation():
    """Regression: the search term "Okafor" is a substring of the extracted
    full name "James Okafor". Templating it first would split the name and
    hide it from the member-data truncation check, leaving a broken
    exact-match locator like 'Open detail for James {{search_term}}, ...'.
    The compiler must truncate to the stable prefix regardless.
    """
    cap, _ = compile_capability(_real_trajectory(), POLICY_SPEC, INPUT_BINDINGS)
    link = cap.steps[4]
    assert link.locators[0].name == "Open detail for"
    assert "{{search_term}}" not in link.locators[0].name
    assert link.locators[0].exact is False
    assert link.locators[0].nth == 0


def test_checkpoints_reference_the_next_steps_target():
    cap, _ = compile_capability(_real_trajectory(), POLICY_SPEC, INPUT_BINDINGS)
    # step 1 (navigate) -> checkpoint asserts step 2's radio
    cp = cap.steps[0].checkpoint
    assert cp.kind == "any_of"
    vis = next(c for c in cp.checks if c.kind == "element_visible")
    assert vis.locator.role == "radio"
    assert vis.locator.name == "{{search_field}}"
    # step 2 (radio) -> checkpoint asserts step 3's textbox
    vis2 = next(c for c in cap.steps[1].checkpoint.checks
                if c.kind == "element_visible")
    assert vis2.locator.role == "textbox"
    # every non-terminal checkpoint also accepts a business outcome
    for s in cap.steps[:-1]:
        assert any(c.kind == "outcome_matched" for c in s.checkpoint.checks)
    # terminal step is outputs_non_empty over all extracted outputs
    term = cap.steps[-1].checkpoint
    onc = next(c for c in term.checks if c.kind == "outputs_non_empty")
    assert set(onc.outputs) == {"full_name", "savings_balance"}


def test_allowlist_routes_narrowed_to_visited_plus_authored_member_glob():
    cap, _ = compile_capability(_real_trajectory(), POLICY_SPEC, INPUT_BINDINGS)
    routes = cap.guardrails.allowlist_routes
    assert "/" in routes and "/search" in routes
    # /member/* is the hand-authored extra so replay can reach whichever
    # member the search returns (the run itself only hit /member/M1004)
    assert "/member/*" in routes
    assert set(cap.guardrails.allowlist_action_types) == {
        "navigate", "fill", "click", "extract"
    }


def test_policy_layer_is_taken_verbatim_from_the_spec():
    cap, _ = compile_capability(_real_trajectory(), POLICY_SPEC, INPUT_BINDINGS)
    assert [o.code for o in cap.expected_outcomes] == [
        "MEMBER_NOT_FOUND", "ACCESS_DENIED", "SUPERVISOR_REVIEW_REQUIRED"
    ]
    assert cap.risk_class == "read_only"
    assert cap.escalation_policy.on_hard_failure == "escalate"


def test_output_name_mapping_identity_is_still_explicit():
    cap, _ = compile_capability(_real_trajectory(), POLICY_SPEC, INPUT_BINDINGS)
    assert [o.name for o in cap.outputs] == [
        "full_name", "savings_balance", "outcome_code"
    ]
    name_step = next(s for s in cap.steps if s.output_name == "full_name")
    assert name_step.id == "extract_full_name"
    term = cap.steps[-1].checkpoint
    onc = next(c for c in term.checks if c.kind == "outputs_non_empty")
    assert set(onc.outputs) == {"full_name", "savings_balance"}


def test_matches_example_artifact_naming():
    cap, _ = compile_capability(_real_trajectory(), POLICY_SPEC, INPUT_BINDINGS)
    example = json.loads(
        (Path(__file__).resolve().parents[2] / "schema/example_artifact.json").read_text()
    )
    example_outputs = {o["name"] for o in example["outputs"]}
    compiled_outputs = {o.name for o in cap.outputs}
    # the compiled capability is a subset (no date_of_birth / address -- the
    # run never extracted those), but the names it does share must match.
    assert compiled_outputs <= example_outputs
    assert {"full_name", "savings_balance", "outcome_code"} <= compiled_outputs
    # both artifacts now declare the same two inputs
    assert {i["name"] for i in example["inputs"]} == {i.name for i in cap.inputs}


def test_unmapped_output_name_fails_clearly():
    policy = _minimal_policy(output_name_mapping={"savings_balance": "savings_balance"})
    with pytest.raises(CompileError, match="output_name_mapping"):
        compile_capability(_real_trajectory(), policy, INPUT_BINDINGS)


def test_empty_output_name_mapping_fails_clearly():
    policy = _minimal_policy(output_name_mapping={})
    with pytest.raises(CompileError, match="output_name_mapping"):
        compile_capability(_real_trajectory(), policy, INPUT_BINDINGS)


def test_rename_mapping_is_applied():
    # a policy may still rename a discovered output; it just has to say so
    policy = _minimal_policy(
        capability_id="member_lookup",
        output_name_mapping={"full_name": "member_name",
                             "savings_balance": "savings_balance"},
    )
    cap, notes = compile_capability(_real_trajectory(), policy, INPUT_BINDINGS)
    assert [o.name for o in cap.outputs] == [
        "member_name", "savings_balance", "outcome_code"
    ]
    assert any("member_name" in p and "full_name" in p
               for p in notes.parameterizations)


def test_search_field_gap_is_recorded_as_resolved():
    cap, notes = compile_capability(_real_trajectory(), POLICY_SPEC, INPUT_BINDINGS)
    assert [i.name for i in cap.inputs] == ["search_field", "search_term"]
    assert any(s.action == "click" and s.id == "click_search_field"
               for s in cap.steps)
    assert any("RESOLVED" in g and "search_field" in g for g in notes.gaps)


# --- failure paths ---------------------------------------------------------

def test_missing_required_policy_field_fails_loudly():
    with pytest.raises(Exception):  # pydantic ValidationError at construction
        PolicySpec(capability_id="c", version="1", description="d", app="a")


def test_empty_expected_outcomes_is_rejected():
    policy = _minimal_policy(expected_outcomes=[])
    with pytest.raises(CompileError, match="expected_outcomes is empty"):
        compile_capability(_real_trajectory(), policy, INPUT_BINDINGS)


def test_non_completed_trajectory_is_rejected():
    traj = _real_trajectory()
    traj.outcome = "max_steps"
    with pytest.raises(CompileError, match="not 'completed'"):
        compile_capability(traj, POLICY_SPEC, INPUT_BINDINGS)


def test_extract_without_a_label_is_rejected():
    traj = _real_trajectory()
    for s in traj.steps:
        if s.extraction:
            s.extraction.label = None
    with pytest.raises(CompileError, match="captured no label"):
        compile_capability(traj, POLICY_SPEC, INPUT_BINDINGS)


def test_unbound_fill_literal_is_recorded_as_a_gap():
    cap, notes = compile_capability(
        _real_trajectory(), POLICY_SPEC, input_bindings=[]
    )
    fill = next(s for s in cap.steps if s.action == "fill")
    assert fill.value_template == "Okafor"  # left literal, not invented
    assert any("not bound to any InputParam" in g for g in notes.gaps)
