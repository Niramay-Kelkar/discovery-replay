"""Discovery's raw output: the Trajectory.

This is deliberately **not** the ``Capability`` / ``Step`` schema in
``agent/models.py``. A trajectory is an un-compiled transcript of one
live discovery run -- every tool call the model made, what it resolved
to, what happened, and (for extracts) the value read off the page plus
the label the value sits next to. The compiler (not built yet) is what
turns a Trajectory into a Capability.

Keeping the two models separate means the compiler has an explicit
input contract, and discovery is never tempted to emit something that
looks replayable before anything has decided it is.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TrajectoryOutcome = Literal[
    "completed",       # the model called done and it validated
    "max_steps",       # step budget exhausted
    "timeout",         # wall-clock budget exhausted
    "error",           # unrecoverable error in the loop
]

StepStatus = Literal[
    "ok",
    "resolution_error",   # zero / ambiguous match; model may retry
    "tool_error",         # tool ran but failed (e.g. done with uncaptured name)
    "loop_error",         # exception outside the tool
]


class ResolvedLocator(BaseModel):
    """What a click/type/extract actually resolved against the live tree.

    Always an exact role+name lookup (DESIGN.md / the substring-ambiguity
    hazard in hostile table markup). Carries enough for the compiler to
    build a ranked locator set later.
    """

    model_config = ConfigDict(extra="forbid")

    strategy: Literal["aria_role"] = "aria_role"
    role: str
    name: str
    exact: bool = True
    nth: int | None = None
    match_count: int = Field(description="how many elements this matched")
    resolved_text: str | None = Field(
        default=None, description="inner_text of the resolved element"
    )
    dom_tag: str | None = None


class ExtractionResult(BaseModel):
    """The value read off the page by an ``extract`` call, plus its label."""

    model_config = ConfigDict(extra="forbid")

    output_name: str
    value: str = Field(description="read from the live DOM, not the model")
    label: str | None = Field(
        default=None,
        description="label text the value sits next to, if determinable",
    )
    label_source: str | None = Field(
        default=None,
        description="how the label was found, e.g. 'rowheader_in_row'",
    )


class TrajectoryStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1)
    tool: Literal["navigate", "click", "type", "extract", "done"]
    params: dict[str, Any]
    rationale: str | None = Field(
        default=None, description="any text the model emitted alongside the call"
    )

    status: StepStatus
    error: str | None = None

    # populated for click / type / extract
    resolved: ResolvedLocator | None = None
    # populated for extract
    extraction: ExtractionResult | None = None
    # populated for navigate
    navigated_to: str | None = None

    page_url: str | None = None
    screenshot_path: str | None = None
    snapshot_sha1: str | None = Field(
        default=None, description="sha1 of the aria snapshot the model saw"
    )
    started_at: str
    duration_ms: int
    retry_of_index: int | None = Field(
        default=None, description="set when this step re-attempts a failed one"
    )


class Trajectory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    goal: str
    target_base_url: str
    entry_path: str
    model: str

    started_at: str
    finished_at: str | None = None
    outcome: TrajectoryOutcome | None = None
    outcome_detail: str | None = None

    steps: list[TrajectoryStep] = Field(default_factory=list)

    # name -> the successful extraction for it (last one wins)
    captured_outputs: dict[str, ExtractionResult] = Field(default_factory=dict)
    # the subset of captured_outputs the model cited when it called done
    cited_outputs: list[str] = Field(default_factory=list)

    step_count: int = 0
    resolution_error_count: int = 0
    retry_count: int = 0

    def to_json(self) -> str:
        return self.model_dump_json(indent=2, exclude_none=True)
