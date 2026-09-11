"""Outcome detection: recognizing an expected business outcome on a live page.

Deliberately its own module (see CLAUDE.md, "Outcome detection"). Replay's
loop asks one question of this module -- "is a recognized expected
outcome on the screen right now, and if so which one?" -- and never
inlines the answer. Keeping it separate means the rules that turn a live
page into a business-outcome code can be pointed at and explained on
their own, and the ``outcome_matched`` checkpoint kind is just a
reference to this module's verdict.

What it evaluates: the :class:`~agent.models.DetectionRule` tree attached
to each :class:`~agent.models.ExpectedOutcome`. Every rule kind in the
schema is handled; a kind with no handler fails loudly at import rather
than silently returning ``False`` (a missed business outcome would be
escalated to a human as a fake hard failure).

Detection keys off label / structural / transport signals only
(``text_present``, ``aria_visible``, ``http_status``, ``url_matches``) --
never a specific member's data.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import get_args

from playwright.sync_api import Page

from agent.models import DetectionKind, DetectionRule, ExpectedOutcome


@dataclass
class DetectionContext:
    """The live-page facts a detection rule may consult."""

    page: Page
    #: HTTP status of the most recent main-frame document response, if known.
    http_status: int | None = None
    #: cached body text, filled lazily
    _text: str | None = field(default=None, repr=False)

    def page_text(self) -> str:
        if self._text is None:
            try:
                self._text = self.page.locator("body").inner_text(timeout=2000)
            except Exception:
                try:
                    self._text = self.page.content()
                except Exception:
                    self._text = ""
        return self._text


# --- per-kind handlers ------------------------------------------------------

_HANDLERS: dict[str, Callable[[DetectionRule, DetectionContext], bool]] = {}


def _handler(kind: str):
    def deco(fn):
        _HANDLERS[kind] = fn
        return fn

    return deco


@_handler("text_present")
def _text_present(rule: DetectionRule, ctx: DetectionContext) -> bool:
    needle = (rule.text or "").strip()
    if not needle:
        return False
    return needle.lower() in ctx.page_text().lower()


@_handler("aria_visible")
def _aria_visible(rule: DetectionRule, ctx: DetectionContext) -> bool:
    name = rule.name or None
    try:
        if rule.role:
            loc = ctx.page.get_by_role(rule.role, name=name, exact=rule.exact)
        elif name:
            loc = ctx.page.get_by_text(name, exact=rule.exact)
        else:
            return False
        if loc.count() < 1:
            return False
        return bool(loc.first.is_visible())
    except Exception:
        return False


@_handler("http_status")
def _http_status(rule: DetectionRule, ctx: DetectionContext) -> bool:
    return rule.status is not None and ctx.http_status == rule.status


@_handler("url_matches")
def _url_matches(rule: DetectionRule, ctx: DetectionContext) -> bool:
    if not rule.pattern:
        return False
    try:
        return re.search(rule.pattern, ctx.page.url) is not None
    except re.error:
        return False


@_handler("any_of")
def _any_of(rule: DetectionRule, ctx: DetectionContext) -> bool:
    return any(evaluate_detection_rule(r, ctx) for r in (rule.rules or []))


@_handler("all_of")
def _all_of(rule: DetectionRule, ctx: DetectionContext) -> bool:
    rules = rule.rules or []
    return bool(rules) and all(evaluate_detection_rule(r, ctx) for r in rules)


# Fail at import if the schema grows a detection kind nothing here handles.
_MISSING = set(get_args(DetectionKind)) - set(_HANDLERS)
if _MISSING:  # pragma: no cover - guards against a silent schema drift
    raise RuntimeError(
        f"outcome_detection has no handler for DetectionKind(s): {sorted(_MISSING)}"
    )


# --- public API -----------------------------------------------------------

def evaluate_detection_rule(rule: DetectionRule, ctx: DetectionContext) -> bool:
    """True iff *rule* matches the live page described by *ctx*."""
    handler = _HANDLERS.get(rule.kind)
    if handler is None:  # pragma: no cover - schema drift, see import guard
        raise RuntimeError(f"no detection handler for kind {rule.kind!r}")
    return handler(rule, ctx)


def detect_outcome(
    outcomes: list[ExpectedOutcome], ctx: DetectionContext
) -> ExpectedOutcome | None:
    """The first declared expected outcome currently detectable, or ``None``.

    Order follows the artifact's ``expected_outcomes`` list, so a policy
    author controls precedence when two rules could both fire.
    """
    for outcome in outcomes:
        if evaluate_detection_rule(outcome.detection, ctx):
            return outcome
    return None
