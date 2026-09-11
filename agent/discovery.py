"""The discovery loop: LLM observe -> decide -> act against a live browser.

Slow, non-deterministic, run once per capability. Produces a
``Trajectory`` (agent/trajectory.py), not a Capability -- compiling that
comes later.

Contract with the model:

* It sees exactly one representation of the page: the accessibility
  snapshot (agent/perception.py).
* Every turn it must call exactly one tool
  (``tool_choice={"type": "any"}``, parallel disabled). No free-form
  reasoning is parsed for intent.
* Element resolution is exact role+name. ``extract`` is the only trusted
  path to a value; ``done`` can only cite already-extracted slots.

Stopping: ``done`` (clean), max steps, or wall-clock timeout.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from playwright.sync_api import sync_playwright

from agent.discovery_tools import TOOLS, DoneSignal, ToolExecutor
from agent.trajectory import Trajectory

DEFAULT_MODEL = "claude-sonnet-4-6"  # this project's current Claude Sonnet
DEFAULT_MAX_STEPS = 20
DEFAULT_TIMEOUT_S = 240
MAX_TOKENS = 1024

SYSTEM_PROMPT = """\
You are a discovery agent driving a real web browser to work out how a \
task is done in a legacy back-office web app that has no API. You will \
do this once; the steps you take are recorded and later replayed \
without you.

HOW YOU SEE THE PAGE
You are given the page as an accessibility snapshot: a tree of roles \
and accessible names. That is the only view you get. There are no \
screenshots. Reason only from the snapshot.

HOW YOU ACT
Every turn you MUST call exactly one tool:
- navigate(path): go to a path on the app.
- click(role, name): click a link/button/radio.
- type(role, name, text): type into a field.
- extract(role, name, output_name): read a value off the page into a \
named slot. The value is read from the live page, not from what you \
say it is.
- done(output_names, summary): finish. output_names must be slots you \
already filled with extract this run.

RULES
- Identify every element by its role and its EXACT accessible name, \
copied from the snapshot. Not a substring, not a paraphrase. In this \
app a wrapper's name often contains a child's name, so exact matching \
matters.
- Before done, you must have called extract for every value the goal \
asks for. done cannot take a value you simply state.
- If a tool returns an ERROR, read it and try a corrected call. Do not \
repeat the same failing call.
- Keep going until the goal is met, then call done.
"""


@dataclass
class DiscoveryConfig:
    goal: str
    base_url: str
    entry_path: str = "/"
    model: str = DEFAULT_MODEL
    max_steps: int = DEFAULT_MAX_STEPS
    timeout_s: int = DEFAULT_TIMEOUT_S
    headed: bool = False
    evidence_root: str = "evidence/runs"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id() -> str:
    return "disc-" + datetime.now().strftime("%Y%m%d-%H%M%S")


class DiscoveryAgent:
    def __init__(self, cfg: DiscoveryConfig):
        self.cfg = cfg
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.run_id = _run_id()
        self.run_dir = Path(cfg.evidence_root) / self.run_id
        (self.run_dir / "screenshots").mkdir(parents=True, exist_ok=True)
        self._jsonl = (self.run_dir / "steps.jsonl").open("w")

    # -- evidence ------------------------------------------------------

    def _log(self, kind: str, **fields) -> None:
        rec = {"ts": _now_iso(), "kind": kind, **fields}
        self._jsonl.write(json.dumps(rec, default=str) + "\n")
        self._jsonl.flush()

    def _on_step(self, step) -> None:
        self._log(
            "step",
            index=step.index,
            tool=step.tool,
            params=step.params,
            status=step.status,
            error=step.error,
            rationale=step.rationale,
            resolved=step.resolved.model_dump(exclude_none=True) if step.resolved else None,
            extraction=step.extraction.model_dump(exclude_none=True) if step.extraction else None,
            page_url=step.page_url,
            screenshot=step.screenshot_path,
            retry_of_index=step.retry_of_index,
            duration_ms=step.duration_ms,
        )

    # -- loop --------------------------------------------------------

    def run(self) -> Trajectory:
        traj = Trajectory(
            run_id=self.run_id,
            goal=self.cfg.goal,
            target_base_url=self.cfg.base_url,
            entry_path=self.cfg.entry_path,
            model=self.cfg.model,
            started_at=_now_iso(),
        )
        self._log("run_start", run_id=self.run_id, goal=self.cfg.goal,
                  base_url=self.cfg.base_url, model=self.cfg.model,
                  max_steps=self.cfg.max_steps, timeout_s=self.cfg.timeout_s)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not self.cfg.headed)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            executor = ToolExecutor(
                page=page,
                trajectory=traj,
                base_url=self.cfg.base_url,
                screenshot_dir=str(self.run_dir / "screenshots"),
                on_step=self._on_step,
            )
            try:
                page.goto(self.cfg.base_url.rstrip("/") + self.cfg.entry_path,
                          wait_until="domcontentloaded")
                self._drive(page, executor, traj)
            except DoneSignal as sig:
                traj.outcome = "completed"
                traj.outcome_detail = sig.summary
                traj.cited_outputs = list(sig.output_names)
            except _Stop as stop:
                traj.outcome = stop.outcome
                traj.outcome_detail = stop.detail
            except Exception as e:  # noqa: BLE001
                traj.outcome = "error"
                traj.outcome_detail = f"{type(e).__name__}: {e}"
            finally:
                traj.finished_at = _now_iso()
                browser.close()

        (self.run_dir / "trajectory.json").write_text(traj.to_json())
        self._log("run_end", outcome=traj.outcome, detail=traj.outcome_detail,
                  steps=traj.step_count, retries=traj.retry_count,
                  resolution_errors=traj.resolution_error_count,
                  captured=list(traj.captured_outputs))
        self._jsonl.close()
        return traj

    def _drive(self, page, executor: ToolExecutor, traj: Trajectory) -> None:
        deadline = time.time() + self.cfg.timeout_s
        snapshot = executor.perception.snapshot()
        messages = [{
            "role": "user",
            "content": (
                f"GOAL: {self.cfg.goal}\n\n"
                f"You are on {page.url}\n\n"
                f"--- accessibility snapshot ---\n{snapshot}"
            ),
        }]

        while True:
            if traj.step_count >= self.cfg.max_steps:
                raise _Stop("max_steps", f"hit step budget ({self.cfg.max_steps})")
            if time.time() > deadline:
                raise _Stop("timeout", f"hit wall-clock budget ({self.cfg.timeout_s}s)")

            resp = self.client.messages.create(
                model=self.cfg.model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                tool_choice={"type": "any", "disable_parallel_tool_use": True},
                messages=messages,
            )
            self._log("llm_response", stop_reason=resp.stop_reason,
                      usage={"in": resp.usage.input_tokens, "out": resp.usage.output_tokens})

            text_parts = [b.text for b in resp.content if b.type == "text"]
            tool_uses = [b for b in resp.content if b.type == "tool_use"]

            if not tool_uses:
                raise _Stop("error", "model returned no tool call despite forced tool use")
            tu = tool_uses[0]

            # "why" is a required field on every tool; fall back to any
            # free text the model emitted alongside the call.
            rationale = (tu.input.get("why")
                         or " ".join(t.strip() for t in text_parts).strip()
                         or None)

            messages.append({"role": "assistant", "content": resp.content})
            result_text, is_error = executor.run_tool(
                tu.name, dict(tu.input), rationale, snapshot
            )
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result_text,
                    "is_error": is_error,
                }],
            })
            # the snapshot the model will reason over next turn is the one
            # embedded in result_text; keep its raw form for the next hash
            snapshot = _snapshot_tail(result_text)


class _Stop(Exception):
    def __init__(self, outcome: str, detail: str):
        self.outcome = outcome
        self.detail = detail


def _snapshot_tail(result_text: str) -> str:
    marker = "accessibility snapshot"
    i = result_text.find(marker)
    return result_text[i:] if i != -1 else result_text
