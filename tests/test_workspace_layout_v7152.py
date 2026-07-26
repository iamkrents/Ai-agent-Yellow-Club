"""Regression tests for v7.1.5.2 — Payments Workspace mobile layout polish.

Tests:
 T01  ws-stats-grid uses CSS Grid
 T02  ws-stats-grid 2-column default (mobile)
 T03  ws-stats-grid 3-column at min-width 560px (desktop)
 T04  ws-stats-grid 1-column at max-width 319px (very narrow)
 T05  ws-stat-card has border and border-radius
 T06  ws-stat-card has min-height
 T07  ws-stat-value has large font-size
 T08  ws-stat-value uses tabular-nums
 T09  ws-stat-label has muted color
 T10  ws-subtabs uses flex with nowrap
 T11  ws-subtabs has overflow-x: auto
 T12  ws-subtabs hides scrollbar (scrollbar-width: none)
 T13  ws-subtab has white-space: nowrap
 T14  ws-subtab.active has dark background
 T15  topbar h1 reduced font on narrow mobile (max-width: 420px)
 T16  topbar h1 further reduced on very narrow (max-width: 360px)
 T17  _loadWorkspaceAllPayments calls /api/payments/intents
 T18  _wsRenderAllPayments uses renderPaymentIntentList
 T19  _wsState includes allPayments field
 T20  _wsRenderCurrentTab triggers _loadWorkspaceAllPayments on all-payments tab
 T21  Cache-bust and version marker are v7.1.5.2
 T22  testRolePanel still not inside tab-payments-workspace section

Run:
    python -m unittest tests.test_workspace_layout_v7152 -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CSS      = ROOT / "miniapp" / "styles.css"
APP_JS   = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"

_css_cache: str | None = None
_js_cache: str | None = None
_html_cache: str | None = None


def _css() -> str:
    global _css_cache
    if _css_cache is None:
        _css_cache = CSS.read_text(encoding="utf-8")
    return _css_cache


def _js() -> str:
    global _js_cache
    if _js_cache is None:
        _js_cache = APP_JS.read_text(encoding="utf-8")
    return _js_cache


def _html() -> str:
    global _html_cache
    if _html_cache is None:
        _html_cache = INDEX_HTML.read_text(encoding="utf-8")
    return _html_cache


def _css_block(selector: str) -> str:
    """Extract first CSS rule block for the given selector."""
    css = _css()
    idx = css.find(selector)
    if idx == -1:
        return ""
    start = css.find("{", idx)
    end = css.find("}", start)
    if start == -1 or end == -1:
        return ""
    return css[start:end + 1]


# ---------------------------------------------------------------------------
# T01–T04: ws-stats-grid
# ---------------------------------------------------------------------------

class TestWsStatsGrid(unittest.TestCase):

    def test_01_ws_stats_grid_uses_css_grid(self):
        """ws-stats-grid must use display:grid."""
        block = _css_block(".ws-stats-grid")
        self.assertIn("display", block)
        self.assertIn("grid", block)

    def test_02_ws_stats_grid_two_col_default(self):
        """ws-stats-grid default must be 2-column layout."""
        css = _css()
        idx = css.find(".ws-stats-grid")
        self.assertNotEqual(idx, -1, ".ws-stats-grid not found in styles.css")
        block_start = css.find("{", idx)
        block_end = css.find("}", block_start)
        block = css[block_start:block_end]
        self.assertIn("repeat(2", block, "Default grid must be repeat(2, ...)")

    def test_03_ws_stats_grid_three_col_desktop(self):
        """ws-stats-grid must use 3 columns at min-width 560px."""
        css = _css()
        m = re.search(
            r'@media[^{]*min-width\s*:\s*560px[^{]*\{[^}]*\.ws-stats-grid[^}]*repeat\(3',
            css,
            re.DOTALL,
        )
        self.assertIsNotNone(
            m,
            "Expected @media (min-width: 560px) { .ws-stats-grid { grid-template-columns: repeat(3, ...) } }",
        )

    def test_04_ws_stats_grid_one_col_very_narrow(self):
        """ws-stats-grid must collapse to 1 column at max-width 319px."""
        css = _css()
        m = re.search(
            r'@media[^{]*max-width\s*:\s*319px[^{]*\{[^}]*\.ws-stats-grid[^}]*1fr',
            css,
            re.DOTALL,
        )
        self.assertIsNotNone(
            m,
            "Expected @media (max-width: 319px) { .ws-stats-grid { grid-template-columns: 1fr } }",
        )


# ---------------------------------------------------------------------------
# T05–T09: ws-stat-card / value / label
# ---------------------------------------------------------------------------

class TestWsStatCard(unittest.TestCase):

    def test_05_ws_stat_card_has_border_and_radius(self):
        """ws-stat-card must define border and border-radius."""
        block = _css_block(".ws-stat-card")
        self.assertIn("border", block, "ws-stat-card must have border")
        self.assertIn("border-radius", block, "ws-stat-card must have border-radius")

    def test_06_ws_stat_card_has_min_height(self):
        """ws-stat-card must define min-height so cards have visual substance."""
        block = _css_block(".ws-stat-card")
        self.assertIn("min-height", block, "ws-stat-card must define min-height")

    def test_07_ws_stat_value_large_font(self):
        """ws-stat-value must have font-size >= 20px."""
        block = _css_block(".ws-stat-value")
        m = re.search(r'font-size\s*:\s*(\d+)px', block)
        self.assertIsNotNone(m, "ws-stat-value must declare font-size in px")
        self.assertGreaterEqual(
            int(m.group(1)), 20,
            "ws-stat-value font-size must be >= 20px for readability",
        )

    def test_08_ws_stat_value_tabular_nums(self):
        """ws-stat-value must use tabular-nums for aligned digit columns."""
        block = _css_block(".ws-stat-value")
        self.assertIn("tabular-nums", block, "ws-stat-value must use font-variant-numeric: tabular-nums")

    def test_09_ws_stat_label_muted_color(self):
        """ws-stat-label must use muted color variable."""
        block = _css_block(".ws-stat-label")
        self.assertIn("--muted", block, "ws-stat-label must use var(--muted) for color")


# ---------------------------------------------------------------------------
# T10–T14: ws-subtabs / ws-subtab
# ---------------------------------------------------------------------------

class TestWsSubtabs(unittest.TestCase):

    def test_10_ws_subtabs_flex_nowrap(self):
        """ws-subtabs must use flex layout with nowrap."""
        block = _css_block(".ws-subtabs")
        self.assertIn("flex", block, "ws-subtabs must use display: flex")
        self.assertIn("nowrap", block, "ws-subtabs must use flex-wrap: nowrap")

    def test_11_ws_subtabs_overflow_x_auto(self):
        """ws-subtabs must have overflow-x: auto for horizontal scroll."""
        block = _css_block(".ws-subtabs")
        self.assertIn("overflow-x", block)
        self.assertIn("auto", block)

    def test_12_ws_subtabs_hides_scrollbar(self):
        """ws-subtabs must hide the native scrollbar via scrollbar-width: none."""
        css = _css()
        idx = css.find(".ws-subtabs")
        self.assertNotEqual(idx, -1)
        chunk = css[idx: idx + 400]
        self.assertIn("scrollbar-width", chunk, "ws-subtabs must set scrollbar-width: none")

    def test_13_ws_subtab_nowrap(self):
        """ws-subtab must use white-space: nowrap so labels stay on one line."""
        css = _css()
        m = re.search(r'\.ws-subtab\s*\{([^}]+)\}', css)
        self.assertIsNotNone(m, ".ws-subtab rule not found in styles.css")
        block = m.group(1)
        self.assertIn("white-space", block, ".ws-subtab must set white-space")
        self.assertIn("nowrap", block, ".ws-subtab must use white-space: nowrap")

    def test_14_ws_subtab_active_dark_background(self):
        """ws-subtab.active must have a dark background (var(--ink))."""
        css = _css()
        idx = css.find(".ws-subtab.active")
        self.assertNotEqual(idx, -1, ".ws-subtab.active not found in styles.css")
        block_start = css.find("{", idx)
        block_end = css.find("}", block_start)
        block = css[block_start:block_end]
        self.assertIn("background", block, ".ws-subtab.active must set background")
        self.assertIn("--ink", block, ".ws-subtab.active background must use var(--ink)")


# ---------------------------------------------------------------------------
# T15–T16: topbar h1 mobile font
# ---------------------------------------------------------------------------

class TestTopbarH1Mobile(unittest.TestCase):

    def test_15_topbar_h1_reduced_font_420px(self):
        """topbar h1 must have reduced font-size inside max-width: 420px media query."""
        css = _css()
        m = re.search(
            r'@media[^{]*max-width\s*:\s*420px[^{]*\{[^}]*\.topbar\s+h1[^}]*font-size',
            css,
            re.DOTALL,
        )
        self.assertIsNotNone(
            m,
            "Expected @media (max-width: 420px) { .topbar h1 { font-size: ... } }",
        )

    def test_16_topbar_h1_reduced_font_360px(self):
        """topbar h1 must have further reduced font-size inside max-width: 360px media query."""
        css = _css()
        m = re.search(
            r'@media[^{]*max-width\s*:\s*360px[^{]*\{[^}]*\.topbar\s+h1[^}]*font-size',
            css,
            re.DOTALL,
        )
        self.assertIsNotNone(
            m,
            "Expected @media (max-width: 360px) { .topbar h1 { font-size: ... } }",
        )


# ---------------------------------------------------------------------------
# T17–T20: JS — all-payments implementation
# ---------------------------------------------------------------------------

class TestAllPaymentsImpl(unittest.TestCase):

    def test_17_load_workspace_all_payments_calls_intents_api(self):
        """_loadWorkspaceAllPayments must call /api/payments/intents."""
        js = _js()
        fn_start = js.find("async function _loadWorkspaceAllPayments(")
        self.assertNotEqual(fn_start, -1, "_loadWorkspaceAllPayments not found in app.js")
        fn_end = js.find("\nasync function ", fn_start + 1)
        if fn_end == -1:
            fn_end = js.find("\nfunction ", fn_start + 1)
        fn_body = js[fn_start : fn_end if fn_end != -1 else fn_start + 600]
        self.assertIn(
            "/api/payments/intents", fn_body,
            "_loadWorkspaceAllPayments must call /api/payments/intents",
        )

    def test_18_ws_render_all_payments_uses_render_payment_intent_list(self):
        """_wsRenderAllPayments must delegate to renderPaymentIntentList."""
        js = _js()
        fn_start = js.find("function _wsRenderAllPayments(")
        self.assertNotEqual(fn_start, -1)
        fn_end = js.find("\nfunction ", fn_start + 1)
        fn_body = js[fn_start : fn_end if fn_end != -1 else fn_start + 600]
        self.assertIn(
            "renderPaymentIntentList", fn_body,
            "_wsRenderAllPayments must call renderPaymentIntentList",
        )
        self.assertNotIn(
            "используйте раздел",
            fn_body,
            "_wsRenderAllPayments must not redirect to Reports tab anymore",
        )

    def test_19_ws_state_has_all_payments_field(self):
        """_wsState must have allPayments field (null default)."""
        js = _js()
        m = re.search(r'const _wsState\s*=\s*\{([^}]+)\}', js)
        self.assertIsNotNone(m, "_wsState not found in app.js")
        self.assertIn("allPayments", m.group(1), "_wsState must include allPayments field")

    def test_20_ws_render_current_tab_triggers_load_for_all_payments(self):
        """_wsRenderCurrentTab must call _loadWorkspaceAllPayments for all-payments tab."""
        js = _js()
        fn_start = js.find("function _wsRenderCurrentTab(")
        self.assertNotEqual(fn_start, -1)
        fn_end = js.find("\nasync function ", fn_start + 1)
        if fn_end == -1:
            fn_end = js.find("\nfunction ", fn_start + 1)
        fn_body = js[fn_start : fn_end if fn_end != -1 else fn_start + 400]
        self.assertIn(
            "_loadWorkspaceAllPayments",
            fn_body,
            "_wsRenderCurrentTab must trigger _loadWorkspaceAllPayments when tab is all-payments",
        )


# ---------------------------------------------------------------------------
# T21: Version / cache-bust
# ---------------------------------------------------------------------------

class TestVersionV7152(unittest.TestCase):

    def test_21_cache_bust_and_version_are_v7153(self):
        """index.html cache-bust and app.js version marker must be v7.1.5.3."""
        html = _html()
        js = _js()
        self.assertIn("v=7.1.5.3", html, "index.html cache-bust must be v=7.1.5.3")
        self.assertIn(
            'console.log("MiniApp version: v7.1.5.3")',
            js,
            "app.js must contain version marker v7.1.5.3",
        )


# ---------------------------------------------------------------------------
# T22: testRolePanel regression
# ---------------------------------------------------------------------------

class TestTestRolePanelRegression(unittest.TestCase):

    def test_22_test_role_panel_not_inside_workspace_section(self):
        """testRolePanel must remain outside tab-payments-workspace (regression from v7.1.5.1)."""
        html = _html()
        ws_start = html.find('id="tab-payments-workspace"')
        self.assertNotEqual(ws_start, -1, "tab-payments-workspace section not found")
        ws_end = html.find("</section>", ws_start)
        workspace_chunk = html[ws_start:ws_end]
        self.assertNotIn(
            'id="testRolePanel"', workspace_chunk,
            "testRolePanel must NOT be nested inside tab-payments-workspace (v7.1.5.1 regression guard)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
