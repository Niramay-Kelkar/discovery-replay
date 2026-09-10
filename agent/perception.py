"""Perception: how discovery sees and resolves the page.

The model reasons over exactly one representation -- Playwright's
accessibility snapshot (``locator("body").aria_snapshot()``), a text
role + accessible-name tree. This is the *same* representation the
artifact's locators are built from, so discovery and replay share one
notion of "what's on the page." Screenshots are captured elsewhere for
the evidence trail only; nothing here returns pixels to the model.

Element resolution is **exact accessible-name matching, always**
(``exact=True``). In nested/hostile table markup a leaf's exact name is
routinely a substring of an ancestor wrapper's concatenated name, so
substring matching makes correct elements ambiguous against their own
containers. Zero matches and >1 matches are both failures the caller
surfaces to the model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Locator, Page


class ResolutionError(Exception):
    """Raised when a role+name does not resolve to exactly one element."""

    def __init__(self, role: str, name: str, count: int):
        self.role = role
        self.name = name
        self.count = count
        if count == 0:
            hint = (
                "no element with that exact role and accessible name. "
                "Use a name exactly as it appears in the snapshot."
            )
        else:
            hint = (
                f"{count} elements share that exact role+name; it is "
                "ambiguous. Pick a more specific name from the snapshot."
            )
        super().__init__(f"could not resolve {role} \"{name}\": {hint}")


@dataclass
class ResolvedElement:
    locator: Locator
    role: str
    name: str
    match_count: int
    text: str
    dom_tag: str


# Roles the target app's accessibility contract actually exposes
# (verified against the live tree; see target_app/README.md). The agent
# is told to stay within these.
KNOWN_ROLES = {
    "link", "button", "radio", "textbox", "table", "row",
    "columnheader", "rowheader", "cell", "alert", "status",
    "alertdialog", "search", "radiogroup", "heading", "group",
}


class Perception:
    def __init__(self, page: Page):
        self.page = page

    # -- observation -------------------------------------------------------

    def snapshot(self) -> str:
        """The accessibility tree the model reasons over."""
        return self.page.locator("body").aria_snapshot()

    def url(self) -> str:
        return self.page.url

    # -- resolution -------------------------------------------------------

    def resolve(
        self,
        role: str,
        name: str,
        *,
        exact: bool = True,
        nth: Optional[int] = None,
    ) -> ResolvedElement:
        """Resolve a role+name to exactly one element.

        Exact accessible-name matching is the default and the only mode
        discovery ever uses (the substring-ambiguity hazard in hostile
        table markup). Replay may pass ``exact=False`` for the handful of
        locators the compiler deliberately generalized to a stable prefix
        (e.g. ``"Open detail for"``); those always carry an ``nth`` so a
        multi-match is still resolved to a single element rather than
        raising.
        """
        loc = self.page.get_by_role(role, name=name, exact=exact)
        count = loc.count()

        if nth is not None:
            if count == 0:
                raise ResolutionError(role, name, 0)
            loc = loc.nth(nth)
        elif count != 1:
            raise ResolutionError(role, name, count)

        target = loc if nth is None else loc
        try:
            text = (target.inner_text(timeout=2000) or "").strip()
        except Exception:
            text = (target.text_content() or "").strip()
        try:
            dom_tag = target.evaluate("el => el.tagName.toLowerCase()")
        except Exception:
            dom_tag = ""

        return ResolvedElement(
            locator=target,
            role=role,
            name=name,
            match_count=count,
            text=text,
            dom_tag=dom_tag,
        )

    # -- extraction ------------------------------------------------------

    def extract(self, role: str, name: str) -> tuple[str, Optional[str], Optional[str], ResolvedElement]:
        """Resolve an element and read its value *from the DOM*.

        Returns ``(value, label, label_source, resolved)``. ``value`` is
        the live text at the resolved element -- never the model's claim.
        The label is a best effort walk from the value element to its
        associated label cell / text.
        """
        resolved = self.resolve(role, name)
        value = resolved.text
        if not value:
            value = (resolved.locator.get_attribute("value") or "").strip()

        label, label_source = self._find_label(resolved.locator)
        return value, label, label_source, resolved

    def _find_label(self, loc: Locator) -> tuple[Optional[str], Optional[str]]:
        """Walk page structure from a value element to its label.

        Ordered by how reliable the association is. Runs in the page so
        it can traverse freely; returns plain text.
        """
        js = """
        (el) => {
          const clean = s => (s || '').replace(/\\s+/g, ' ').trim();

          // 1. explicit ARIA labelling
          const lb = el.getAttribute('aria-labelledby');
          if (lb) {
            const t = lb.split(/\\s+/).map(id => {
              const n = document.getElementById(id);
              return n ? n.textContent : '';
            }).join(' ');
            if (clean(t)) return {label: clean(t), source: 'aria-labelledby'};
          }
          const al = el.getAttribute('aria-label');
          if (clean(al)) return {label: clean(al), source: 'aria-label'};

          // 2. table row: value cell paired with a row header
          const cell = el.closest('td,th,[role="cell"],[role="gridcell"]');
          if (cell) {
            const row = cell.closest('tr,[role="row"]');
            if (row) {
              const rh = row.querySelector(
                'th[scope="row"],[role="rowheader"],th');
              if (rh && rh !== cell && clean(rh.textContent)) {
                return {label: clean(rh.textContent), source: 'rowheader_in_row'};
              }
            }
          }

          // 3. <label for> association
          if (el.id) {
            const lf = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
            if (lf && clean(lf.textContent))
              return {label: clean(lf.textContent), source: 'label_for'};
          }
          // 4. wrapping <label>
          const wrap = el.closest('label');
          if (wrap && clean(wrap.textContent))
            return {label: clean(wrap.textContent), source: 'wrapping_label'};

          return null;
        }
        """
        try:
            res = loc.evaluate(js)
        except Exception:
            res = None
        if not res:
            return None, None
        return res.get("label"), res.get("source")

    def read_paired_value(self, loc: Locator) -> str:
        """Read the value that sits *next to* a resolved label element.

        Discovery resolves the value cell directly and reads its own
        text; the compiler rewrites that locator to target the field's
        *label* (the ``rowheader``) so the artifact works for any member.
        Replay therefore lands on the label and has to step across to the
        paired value cell in the same row. Falls back to the element's
        own text when there is no row structure (a ``text_label``
        fallback locator may resolve straight onto the value).
        """
        js = """
        (el) => {
          const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
          const row = el.closest('tr,[role="row"]');
          if (row) {
            const cells = row.querySelectorAll(
              'td,[role="cell"],[role="gridcell"]');
            for (const c of cells) {
              if (c !== el && !el.contains(c) && clean(c.textContent))
                return clean(c.textContent);
            }
          }
          return clean(el.textContent);
        }
        """
        try:
            return (loc.evaluate(js) or "").strip()
        except Exception:
            try:
                return (loc.inner_text(timeout=2000) or "").strip()
            except Exception:
                return ""

    # -- evidence (not part of what the model sees) ---------------------

    def screenshot(self, path: str) -> None:
        try:
            self.page.screenshot(path=path, full_page=True)
        except Exception:
            pass
