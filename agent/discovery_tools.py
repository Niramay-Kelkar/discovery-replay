"""The five tools the discovery LLM can call, and their executor.

The vocabulary and the rationale for it are in CLAUDE.md
("Discovery tool vocabulary"). This module is the machine side: the
Anthropic tool schemas, and a ToolExecutor that runs each call against
the live browser, records a TrajectoryStep, and returns a tool_result
string for the next turn.

Two correctness rules enforced here:

* click / type / extract resolve by **exact** accessible role+name
  (Perception.resolve); zero or ambiguous matches are surfaced to the
  model as errors it can retry.
* ``done`` only accepts ``output_names`` that a prior successful
  ``extract`` captured this run. It can never take a self-reported
  value.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Page

from agent.perception import KNOWN_ROLES, Perception, ResolutionError
from agent.trajectory import (
    ExtractionResult,
    ResolvedLocator,
    Trajectory,
    TrajectoryStep,
)

SETTLE_TIMEOUT_MS = 8000

# Every tool carries a required one-line `why`, so the trajectory / JSONL
# log records the model's reason for each step even though forced tool
# use means it rarely emits free text.
_WHY = {
    "type": "string",
    "description": "one short sentence: why this action, now",
}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "navigate",
        "description": (
            "Go to a path on the target app (same origin only), e.g. \"/\" "
            "or \"/search?field=member_id&q=M1001\". Use this to reach the "
            "starting page or follow a known URL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "path beginning with '/'"},
                "why": _WHY,
            },
            "required": ["path", "why"],
        },
    },
    {
        "name": "click",
        "description": (
            "Click a link, button, or radio. Identify it by its role and "
            "its EXACT accessible name as shown in the accessibility "
            "snapshot -- not a substring."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "name": {"type": "string", "description": "exact accessible name"},
                "why": _WHY,
            },
            "required": ["role", "name", "why"],
        },
    },
    {
        "name": "type",
        "description": (
            "Type text into a form field, identified by exact role and "
            "accessible name. Replaces any existing value."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "name": {"type": "string", "description": "exact accessible name"},
                "text": {"type": "string"},
                "why": _WHY,
            },
            "required": ["role", "name", "text", "why"],
        },
    },
    {
        "name": "extract",
        "description": (
            "Read a value off the page into a named slot. Identify the "
            "element holding the value by exact role and accessible name. "
            "The value is read from the live page, not from your "
            "description of it. Do this for every value the goal asks "
            "for BEFORE calling done."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "name": {"type": "string", "description": "exact accessible name of the value element"},
                "output_name": {
                    "type": "string",
                    "description": "snake_case slot name, e.g. 'savings_balance'",
                },
                "why": _WHY,
            },
            "required": ["role", "name", "output_name", "why"],
        },
    },
    {
        "name": "done",
        "description": (
            "End the run. 'output_names' must list slots you already "
            "captured with a successful extract call this run -- done "
            "cannot accept a value you just state."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "output_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "names of previously-extracted slots",
                },
                "summary": {"type": "string", "description": "one line: what was accomplished"},
                "why": _WHY,
            },
            "required": ["output_names", "summary", "why"],
        },
    },
]


class DoneSignal(Exception):
    """Raised by the executor to tell the loop the run finished cleanly."""

    def __init__(self, summary: str, output_names: list[str]):
        self.summary = summary
        self.output_names = output_names


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ToolExecutor:
    def __init__(
        self,
        page: Page,
        trajectory: Trajectory,
        base_url: str,
        screenshot_dir: str,
        on_step=None,
    ):
        self.page = page
        self.perception = Perception(page)
        self.trajectory = trajectory
        self.base_url = base_url.rstrip("/")
        self.screenshot_dir = screenshot_dir
        self.on_step = on_step  # callback(TrajectoryStep) for JSONL logging
        self._index = 0

    # -- public --------------------------------------------------------

    def run_tool(self, tool: str, params: dict[str, Any], rationale: str | None,
                 snapshot_seen: str) -> tuple[str, bool]:
        """Execute one tool call. Returns (tool_result_text, is_error)."""
        self._index += 1
        idx = self._index
        started = _now_iso()
        t0 = time.time()

        status = "ok"
        error: str | None = None
        resolved_model: ResolvedLocator | None = None
        extraction_model: ExtractionResult | None = None
        navigated_to: str | None = None
        result_text = ""
        is_error = False

        try:
            if tool == "navigate":
                navigated_to, result_text = self._navigate(params["path"])
            elif tool == "click":
                resolved_model, result_text = self._click(params["role"], params["name"])
            elif tool == "type":
                resolved_model, result_text = self._type(
                    params["role"], params["name"], params["text"]
                )
            elif tool == "extract":
                resolved_model, extraction_model, result_text = self._extract(
                    params["role"], params["name"], params["output_name"]
                )
            elif tool == "done":
                self._validate_done(params["output_names"])
                # record the step before raising
                self._record(
                    idx, tool, params, rationale, "ok", None, None, None, None,
                    snapshot_seen, started, t0,
                )
                raise DoneSignal(params["summary"], params["output_names"])
            else:
                status, error = "tool_error", f"unknown tool {tool!r}"
                result_text, is_error = error, True
        except DoneSignal:
            raise
        except ResolutionError as e:
            status = "resolution_error"
            error = str(e)
            result_text = f"ERROR: {e}"
            is_error = True
            self.trajectory.resolution_error_count += 1
        except _ToolError as e:
            status = "tool_error"
            error = str(e)
            result_text = f"ERROR: {e}"
            is_error = True
        except Exception as e:  # noqa: BLE001 - loop must not die on a tool
            status = "loop_error"
            error = f"{type(e).__name__}: {e}"
            result_text = f"ERROR: {error}"
            is_error = True

        step = self._record(
            idx, tool, params, rationale, status, error,
            resolved_model, extraction_model, navigated_to,
            snapshot_seen, started, t0,
        )

        # if this step succeeded but a prior step with the same intent
        # failed, mark it a retry
        if not is_error and idx > 1:
            prev = self.trajectory.steps[-2]
            if prev.status == "resolution_error" and prev.tool == tool:
                step.retry_of_index = prev.index
                self.trajectory.retry_count += 1

        return result_text, is_error

    # -- individual tools --------------------------------------------

    def _resolve_path(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = urljoin(self.base_url + "/", path.lstrip("/"))
        if urlparse(url).netloc != urlparse(self.base_url).netloc:
            raise _ToolError(
                f"refusing to navigate off-origin to {url!r}; "
                "use a path on the target app"
            )
        return url

    def _settle(self) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
        except Exception:
            try:
                self.page.wait_for_load_state("domcontentloaded", timeout=2000)
            except Exception:
                pass

    def _navigate(self, path: str) -> tuple[str, str]:
        url = self._resolve_path(path)
        resp = self.page.goto(url, wait_until="domcontentloaded")
        self._settle()
        code = resp.status if resp else "?"
        return url, f"navigated to {url} (HTTP {code})\n\n{self._observe()}"

    def _click(self, role: str, name: str) -> tuple[ResolvedLocator, str]:
        self._check_role(role)
        el = self.perception.resolve(role, name)
        el.locator.scroll_into_view_if_needed(timeout=2000)
        el.locator.click(timeout=4000)
        self._settle()
        rl = _to_locator_model(el)
        return rl, f"clicked {role} \"{name}\"\n\n{self._observe()}"

    def _type(self, role: str, name: str, text: str) -> tuple[ResolvedLocator, str]:
        self._check_role(role)
        el = self.perception.resolve(role, name)
        el.locator.fill(text, timeout=4000)
        self._settle()
        rl = _to_locator_model(el)
        return rl, f"typed into {role} \"{name}\"\n\n{self._observe()}"

    def _extract(self, role: str, name: str, output_name: str):
        self._check_role(role)
        value, label, label_source, el = self.perception.extract(role, name)
        if value == "":
            raise _ToolError(
                f"resolved {role} \"{name}\" but it has no readable text value"
            )
        rec = ExtractionResult(
            output_name=output_name,
            value=value,
            label=label,
            label_source=label_source,
        )
        self.trajectory.captured_outputs[output_name] = rec
        rl = _to_locator_model(el)
        lbl = f" (label: \"{label}\" via {label_source})" if label else " (no label found)"
        msg = (
            f"extracted {output_name} = \"{value}\"{lbl}\n"
            f"This value was read from the live page.\n\n{self._observe()}"
        )
        return rl, rec, msg

    def _validate_done(self, output_names: list[str]) -> None:
        captured = set(self.trajectory.captured_outputs)
        missing = [n for n in output_names if n not in captured]
        if missing:
            have = sorted(captured) or ["(none)"]
            raise _ToolError(
                f"done references {missing} which were never successfully "
                f"extracted. Captured slots this run: {have}. "
                "Call extract for each missing value first."
            )

    # -- helpers ---------------------------------------------------

    def _check_role(self, role: str) -> None:
        if role not in KNOWN_ROLES:
            raise _ToolError(
                f"role {role!r} is not one this app exposes. Known roles: "
                f"{sorted(KNOWN_ROLES)}"
            )

    def _observe(self) -> str:
        snap = self.perception.snapshot()
        return f"--- accessibility snapshot ({self.page.url}) ---\n{snap}"

    def _record(self, idx, tool, params, rationale, status, error,
                resolved_model, extraction_model, navigated_to,
                snapshot_seen, started, t0) -> TrajectoryStep:
        shot = f"{self.screenshot_dir}/step_{idx:02d}.png"
        self.perception.screenshot(shot)
        step = TrajectoryStep(
            index=idx,
            tool=tool,
            params=params,
            rationale=rationale,
            status=status,
            error=error,
            resolved=resolved_model,
            extraction=extraction_model,
            navigated_to=navigated_to,
            page_url=self.page.url,
            screenshot_path=shot,
            snapshot_sha1=hashlib.sha1(snapshot_seen.encode()).hexdigest(),
            started_at=started,
            duration_ms=int((time.time() - t0) * 1000),
        )
        self.trajectory.steps.append(step)
        self.trajectory.step_count = idx
        if self.on_step:
            self.on_step(step)
        return step


class _ToolError(Exception):
    pass


def _to_locator_model(el) -> ResolvedLocator:
    return ResolvedLocator(
        role=el.role,
        name=el.name,
        exact=True,
        match_count=el.match_count,
        resolved_text=el.text or None,
        dom_tag=el.dom_tag or None,
    )
