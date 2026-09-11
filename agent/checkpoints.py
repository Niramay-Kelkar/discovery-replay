"""Checkpoint evaluation: the CHECK phase of ACT -> SETTLE -> CHECK.

A general evaluator over every :class:`~agent.models.Checkpoint` kind the
schema defines. It is driven off ``get_args(CheckpointKind)`` and asserts
at import that each kind has a handler, so adding a kind to the schema
without teaching replay to check it is a loud failure, not a checkpoint
that silently always passes (or always fails).

The evaluator is pure with respect to the page: it is handed a small
:class:`CheckEnv` of already-resolved facts and callables (is this
locator visible? what text is on the page? which outputs got extracted?
did outcome detection match?) and never drives the browser itself. That
keeps it reusable and keeps ``outcome_matched`` a thin reference to the
outcome-detection module's verdict rather than a second copy of it.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import get_args

from agent.models import Checkpoint, CheckpointKind, ExpectedOutcome, LocatorStrategy


@dataclass
class CheckEnv:
    """Resolved facts a checkpoint may consult during CHECK."""

    #: locator -> is it currently resolvable to one visible element
    visible: Callable[[LocatorStrategy], bool]
    #: full visible text of the page
    page_text: str
    #: output name -> extracted value (only successful, non-empty extracts)
    extracted: dict[str, str]
    #: every output the capability declares required
    required_outputs: list[str]
    #: the expected outcome outcome-detection matched this CHECK, if any
    matched_outcome: ExpectedOutcome | None = None
    #: trace of leaf results, for the evidence log
    trace: list[str] = field(default_factory=list)


_HANDLERS: dict[str, Callable[[Checkpoint, CheckEnv], bool]] = {}


def _handler(kind: str):
    def deco(fn):
        _HANDLERS[kind] = fn
        return fn

    return deco


@_handler("element_visible")
def _element_visible(cp: Checkpoint, env: CheckEnv) -> bool:
    ok = cp.locator is not None and env.visible(cp.locator)
    env.trace.append(f"element_visible({_loc_label(cp.locator)})={ok}")
    return ok


@_handler("text_present")
def _text_present(cp: Checkpoint, env: CheckEnv) -> bool:
    ok = bool(cp.text) and cp.text.lower() in env.page_text.lower()
    env.trace.append(f"text_present({cp.text!r})={ok}")
    return ok


@_handler("outputs_non_empty")
def _outputs_non_empty(cp: Checkpoint, env: CheckEnv) -> bool:
    names = cp.outputs or env.required_outputs
    missing = [n for n in names if not (env.extracted.get(n) or "").strip()]
    env.trace.append(
        f"outputs_non_empty({names})={'ok' if not missing else 'missing ' + str(missing)}"
    )
    return not missing


@_handler("outcome_matched")
def _outcome_matched(cp: Checkpoint, env: CheckEnv) -> bool:
    if env.matched_outcome is None:
        env.trace.append("outcome_matched=none")
        return False
    if cp.outcome_code is not None:
        ok = env.matched_outcome.code == cp.outcome_code
        env.trace.append(
            f"outcome_matched(want {cp.outcome_code})="
            f"{env.matched_outcome.code if ok else 'no (' + env.matched_outcome.code + ')'}"
        )
        return ok
    env.trace.append(f"outcome_matched(any)={env.matched_outcome.code}")
    return True


@_handler("any_of")
def _any_of(cp: Checkpoint, env: CheckEnv) -> bool:
    return any(evaluate_checkpoint(c, env) for c in (cp.checks or []))


@_handler("all_of")
def _all_of(cp: Checkpoint, env: CheckEnv) -> bool:
    checks = cp.checks or []
    return bool(checks) and all(evaluate_checkpoint(c, env) for c in checks)


_MISSING = set(get_args(CheckpointKind)) - set(_HANDLERS)
if _MISSING:  # pragma: no cover - schema drift guard
    raise RuntimeError(
        f"checkpoints has no handler for CheckpointKind(s): {sorted(_MISSING)}"
    )


def evaluate_checkpoint(cp: Checkpoint, env: CheckEnv) -> bool:
    handler = _HANDLERS.get(cp.kind)
    if handler is None:  # pragma: no cover - see import guard
        raise RuntimeError(f"no checkpoint handler for kind {cp.kind!r}")
    return handler(cp, env)


def _loc_label(loc: LocatorStrategy | None) -> str:
    if loc is None:
        return "?"
    return f"{loc.role or ''} {loc.name or loc.text or loc.label or ''}".strip()
