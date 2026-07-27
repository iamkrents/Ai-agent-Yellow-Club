"""Regression tests for v7.1.6.2 — fix iOS keyboard closing after one
character in Payments Workspace → All Payments → search.

Root cause (confirmed by reading the actual call chain, not assumed):
_wsAllPaymentsSearch() — the search <input>'s oninput handler — called the
FULL _wsRenderAllPayments(root), where root = $("wsTabContent") is the whole
tab-content container. That function does `root.innerHTML = controls`, and
`controls` is the string that re-declares the `<input>` itself. Every
keystroke therefore destroyed and recreated the <input> DOM node. A freshly
created node has no focus, so iOS Safari/WebView closes the on-screen
keyboard immediately after the first character — the user had to tap the
field again for every subsequent letter.

Fix: the toolbar (search <input> + filter button) is now written to the DOM
exactly once, inside _wsRenderAllPayments(). Every update that used to
trigger a full re-render — typing, applying filters, resetting filters — now
calls _wsRenderAllPaymentsResults(), which only replaces the contents of a
separate #wsAllPaymentsResults container (results count + card list / empty
state) and refreshes the filter button's badge. The <input> node itself is
never touched by that function, so its focus/selection/open keyboard survive
every keystroke. A small (150ms) debounce was added on top as a performance
nicety, not as the fix itself — the container split is what actually solves
the bug, debounced or not.

Tests:
 T01  Search <input> is declared only in _wsRenderAllPayments (the toolbar,
      rendered once), never inside _wsRenderAllPaymentsResults
 T02  _wsAllPaymentsSearch() does not call the full _wsRenderAllPayments
 T03  _wsRenderAllPaymentsResults() only targets #wsAllPaymentsResults /
      the filter button — never $("wsTabContent") or the search input
 T04  Search term is stored in _wsAllPaymentsUI.search and re-applied to the
      <input>'s value attribute only when the toolbar itself is (re)built
 T05  _wsRenderAllPaymentsResults() never calls .focus()/.select() and never
      references the search input's id — it cannot reset focus/selection
      because it never touches that node
 T06  Search still matches by student name
 T07  Search still matches by MK User ID
 T08  Search still matches by public_id
 T09  The "not found" empty state is rendered by _wsRenderAllPaymentsResults
      (results-only), not by rebuilding the toolbar
 T10  Reset filters (sheet + inline "Сбросить фильтры") update results only /
      clear the input's value directly, never rebuild the toolbar
 T11  Global keyboard-open handling (_isFormField / initKeyboardHandling) is
      unchanged
 T12  Overview / Attention / Pilot Clients renderers untouched; debounce is
      present but not the sole mechanism (container split still holds with
      or without it)

Run:
    python -m unittest tests.test_search_focus_fix_v7162 -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "miniapp" / "app.js"

_js_cache: str | None = None


def _js() -> str:
    global _js_cache
    if _js_cache is None:
        _js_cache = APP_JS.read_text(encoding="utf-8")
    return _js_cache


def _js_fn(name: str, *, is_async: bool = False, window: int = 4000) -> str:
    js = _js()
    needle = f"{'async ' if is_async else ''}function {name}("
    start = js.find(needle)
    assert start != -1, f"{needle} not found in app.js"
    end = js.find("\nasync function ", start + 1)
    end2 = js.find("\nfunction ", start + 1)
    candidates = [e for e in (end, end2) if e != -1]
    end = min(candidates) if candidates else start + window
    return js[start:end]


# ---------------------------------------------------------------------------
# T01-T03: The core structural fix
# ---------------------------------------------------------------------------

class TestContainerSplit(unittest.TestCase):

    def test_01_search_input_only_in_toolbar_function(self):
        toolbar = _js_fn("_wsRenderAllPayments")
        results = _js_fn("_wsRenderAllPaymentsResults")
        self.assertIn('id="wsAllPaymentsSearchInput"', toolbar)
        self.assertIn("<input", toolbar)
        self.assertNotIn("<input", results)
        self.assertNotIn("wsAllPaymentsSearchInput", results)

    def test_02_search_handler_does_not_call_full_render(self):
        handler = _js_fn("_wsAllPaymentsSearch")
        self.assertNotIn("_wsRenderAllPayments(", handler)
        self.assertIn("_wsRenderAllPaymentsResults", handler)

    def test_03_results_updater_scoped_to_results_container(self):
        results = _js_fn("_wsRenderAllPaymentsResults")
        self.assertIn('$("wsAllPaymentsResults")', results)
        self.assertNotIn('$("wsTabContent")', results)
        self.assertNotIn("root.innerHTML", results)


# ---------------------------------------------------------------------------
# T04-T05: Search value / focus / selection preserved
# ---------------------------------------------------------------------------

class TestSearchFocus(unittest.TestCase):

    def test_04_search_value_persisted_and_reapplied_on_toolbar_build(self):
        handler = _js_fn("_wsAllPaymentsSearch")
        self.assertIn("_wsAllPaymentsUI.search = value", handler)
        toolbar = _js_fn("_wsRenderAllPayments")
        self.assertIn("escapeAttr(_wsAllPaymentsUI.search)", toolbar)
        self.assertIn('value="${searchVal}"', toolbar)

    def test_05_results_updater_never_touches_focus_or_the_input_node(self):
        results = _js_fn("_wsRenderAllPaymentsResults")
        self.assertNotIn(".focus(", results)
        self.assertNotIn(".select(", results)
        self.assertNotIn("wsAllPaymentsSearchInput", results)


# ---------------------------------------------------------------------------
# T06-T08: Search still matches the same real fields
# ---------------------------------------------------------------------------

class TestSearchStillWorks(unittest.TestCase):

    def test_06_matches_student_name(self):
        self.assertIn("pi.student_name", _js_fn("_wsFilteredAllPayments"))

    def test_07_matches_mk_user_id(self):
        self.assertIn("pi.mk_user_id", _js_fn("_wsFilteredAllPayments"))

    def test_08_matches_public_id(self):
        self.assertIn("pi.public_id", _js_fn("_wsFilteredAllPayments"))


# ---------------------------------------------------------------------------
# T09-T10: Empty state / filter reset don't touch the toolbar
# ---------------------------------------------------------------------------

class TestEmptyStateAndReset(unittest.TestCase):

    def test_09_not_found_empty_state_is_results_only(self):
        results = _js_fn("_wsRenderAllPaymentsResults")
        self.assertIn("Ничего не найдено", results)
        toolbar = _js_fn("_wsRenderAllPayments")
        self.assertNotIn("Ничего не найдено", toolbar)

    def test_10_reset_and_apply_filters_do_not_rebuild_toolbar(self):
        apply_fn = _js_fn("applyWsFilters")
        reset_fn = _js_fn("resetWsFilters")
        self.assertIn("_wsRenderAllPaymentsResults", apply_fn)
        self.assertIn("_wsRenderAllPaymentsResults", reset_fn)
        self.assertNotIn("_wsRenderAllPayments(", apply_fn)
        self.assertNotIn("_wsRenderAllPayments(", reset_fn)
        # the inline "Сбросить фильтры" (empty-state) reset clears the live
        # DOM value directly instead of forcing a full toolbar re-render
        reset_search_fn = _js_fn("_wsResetAllPaymentsSearchAndFilters")
        self.assertIn('$("wsAllPaymentsSearchInput")', reset_search_fn)
        self.assertIn('.value = ""', reset_search_fn)


# ---------------------------------------------------------------------------
# T11-T12: No regressions elsewhere
# ---------------------------------------------------------------------------

class TestNoRegressions(unittest.TestCase):

    def test_11_keyboard_open_logic_unchanged(self):
        js = _js()
        start = js.find("function initKeyboardHandling")
        iife = js[start:start + 2500]
        self.assertIn('["INPUT", "TEXTAREA", "SELECT"]', iife)
        self.assertIn("window.visualViewport", iife)

    def test_12_other_tabs_untouched_and_debounce_is_not_the_only_fix(self):
        overview = _js_fn("_wsRenderOverview")
        self.assertIn("WS_OVERVIEW_STAT_META", overview)
        attention = _js_fn("_wsRenderAttention")
        self.assertIn("_wsQueueHead", attention)
        pilot = _js_fn("_wsRenderPilotClients")
        self.assertIn("canManagePilotClients", pilot)

        # debounce exists (100-200ms) but the container split is what
        # actually prevents input recreation, independent of the delay
        handler = _js_fn("_wsAllPaymentsSearch")
        m = re.search(r"setTimeout\(_wsRenderAllPaymentsResults,\s*(\d+)\)", handler)
        self.assertIsNotNone(m, "expected a setTimeout(...) debounce around _wsRenderAllPaymentsResults")
        delay = int(m.group(1))
        self.assertGreaterEqual(delay, 100)
        self.assertLessEqual(delay, 200)
        # even with debounce removed, the fix would still hold: the toolbar
        # function itself never runs on every keystroke
        self.assertNotIn("_wsRenderAllPayments(", handler)


if __name__ == "__main__":
    unittest.main(verbosity=2)
