"""Regression tests for v7.1.6.1 — Overview visual pass (Client Manager /
owner/admin Payments Workspace → «Обзор»), adapted from the approved
figma-yellow-club prototype. Visual-only change: no backend, no API, no
capability, and no other-tab change is expected — these tests exist to prove
exactly that boundary held.

Tests:
 T01  Compact .ws-header replaces the heavy shared .section-head for this screen
 T02  .ws-header is its own CSS block, distinct from .section-head
 T03  Six stat cards defined via WS_OVERVIEW_STAT_META with correct labels
 T04  Stat card icons are inline SVG, not emoji
 T05  Stat card tones follow the approved colour mapping (success/pending/danger/info)
 T06  Stat card CSS has the icon-in-square top row + tone-coloured icon squares
 T07  Active tab is yellow, not the old dark background
 T08  Tabs stay nowrap / horizontally scrollable / 44px touch target
 T09  Four workspace tabs are unchanged (ids + labels)
 T10  Overview shows a compact empty state when the attention queue is empty
 T11  Empty-state icon is inline SVG (not emoji) for this specific case
 T12  Recent-operations preview uses dedicated .ws-recent-row markup, no fake data
 T13  Recent-operations preview only renders once real data is loaded
 T14  WORKSPACE_VIEW_ROLES unchanged (client_manager/owner/admin/operations)
 T15  PILOT_ADMIN_ROLES / PILOT_MANAGE_ROLES / PAYMENT_APPROVAL_ROLES unchanged
 T16  Attention tab renderer untouched (SVG empty-state, unchanged since step 2)
 T17  Pilot Clients renderer untouched (empty-state message unchanged)
 T18  All Payments renderer untouched (own dedicated card renderer since step 3)
 T19  Version / cache-bust is v7.1.8
 T20  Food module still not referenced by workspace code
 T21  Test-role panel mechanics unchanged

Run:
    python -m unittest tests.test_overview_visual_v7161 -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS     = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
SERVER     = ROOT / "web_app_server.py"
CSS        = ROOT / "miniapp" / "styles.css"

_js_cache: str | None = None
_html_cache: str | None = None
_server_cache: str | None = None
_css_cache: str | None = None


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


def _server() -> str:
    global _server_cache
    if _server_cache is None:
        _server_cache = SERVER.read_text(encoding="utf-8")
    return _server_cache


def _css() -> str:
    global _css_cache
    if _css_cache is None:
        _css_cache = CSS.read_text(encoding="utf-8")
    return _css_cache


def _js_fn(name: str, *, is_async: bool = False) -> str:
    js = _js()
    needle = f"{'async ' if is_async else ''}function {name}("
    start = js.find(needle)
    assert start != -1, f"{needle} not found in app.js"
    end = js.find("\nasync function ", start + 1)
    end2 = js.find("\nfunction ", start + 1)
    candidates = [e for e in (end, end2) if e != -1]
    end = min(candidates) if candidates else start + 3000
    return js[start:end]


def _css_block(selector: str) -> str:
    css = _css()
    idx = css.find(selector)
    if idx == -1:
        return ""
    start = css.find("{", idx)
    end = css.find("}", start)
    return css[start:end + 1] if start != -1 and end != -1 else ""


def _roles(name: str) -> str:
    m = re.search(rf'{name}\s*=\s*\{{([^}}]+)\}}', _server())
    assert m, f"{name} not found in web_app_server.py"
    return m.group(1)


# ---------------------------------------------------------------------------
# T01-T02: Header
# ---------------------------------------------------------------------------

class TestHeader(unittest.TestCase):

    def test_01_compact_header_replaces_section_head(self):
        fn = _js_fn("_renderWorkspaceSkeleton")
        self.assertIn("ws-header", fn)
        self.assertIn("ws-header__title", fn)
        self.assertNotIn('<h2>Рабочее пространство платежей</h2>', fn)
        self.assertNotIn('class="section-head"', fn)

    def test_02_header_is_its_own_css_block(self):
        block = _css_block(".ws-header {")
        self.assertNotEqual(block, "", ".ws-header CSS block not found")
        self.assertNotIn(".section-head", block)


# ---------------------------------------------------------------------------
# T03-T06: Stat cards
# ---------------------------------------------------------------------------

class TestStatCards(unittest.TestCase):

    def _meta_block(self) -> str:
        js = _js()
        start = js.find("const WS_OVERVIEW_STAT_META")
        self.assertNotEqual(start, -1)
        return js[start:start + 900]

    def test_03_six_cards_with_correct_labels(self):
        block = self._meta_block()
        for label in ("На проверке", "Требуют внимания", "Ожидают оплаты", "Оплачено", "Внесено в МойКласс", "Клиентов в пилоте"):
            self.assertIn(label, block, f"missing stat card label: {label}")

    def test_04_icons_are_svg_not_emoji(self):
        js = _js()
        for icon_name in ("WS_ICON_CLOCK", "WS_ICON_ALERT_TRIANGLE", "WS_ICON_WALLET", "WS_ICON_CHECK_CIRCLE", "WS_ICON_FILE_CHECK", "WS_ICON_USERS"):
            start = js.find(f"const {icon_name}")
            self.assertNotEqual(start, -1, f"{icon_name} not defined")
            decl = js[start:start + 200]
            self.assertIn("<svg", decl, f"{icon_name} must be an inline SVG")
        block = self._meta_block()
        for glyph in ("✅", "\U0001F7E1", "\U0001F534", "\U0001F535"):
            self.assertNotIn(glyph, block, "stat card icons must not be emoji")

    def test_05_tone_mapping_matches_approved_colours(self):
        block = self._meta_block()
        expectations = {
            "pending_review": "pending",
            "requires_check": "danger",
            "awaiting_payment": "pending",
            "paid": "success",
            "posted_to_moyklass": "success",
            "pilot_clients_count": "info",
        }
        for field, tone in expectations.items():
            m = re.search(r'field:\s*"' + field + r'".*?tone:\s*"(\w+)"', block)
            self.assertIsNotNone(m, f"could not find tone for {field}")
            self.assertEqual(m.group(1), tone, f"{field} should be tone={tone}")

    def test_06_icon_square_css_present(self):
        top_block = _css_block(".ws-stat-card__top")
        self.assertNotEqual(top_block, "")
        icon_block = _css_block(".ws-stat-card__icon {")
        self.assertNotEqual(icon_block, "")
        css = _css()
        for tone in ("success", "danger", "pending", "info"):
            self.assertIsNotNone(
                re.search(rf'\.ws-stat-card--{tone}\s+\.ws-stat-card__icon', css),
                f"missing tone rule for --{tone}",
            )


# ---------------------------------------------------------------------------
# T07-T09: Tabs
# ---------------------------------------------------------------------------

class TestTabs(unittest.TestCase):

    def test_07_active_tab_is_yellow(self):
        block = _css_block(".ws-subtab.active")
        self.assertIn("--yellow", block)

    def test_08_tabs_nowrap_scrollable_44px(self):
        subtabs = _css_block(".ws-subtabs {")
        self.assertIn("nowrap", subtabs)
        self.assertIn("overflow-x", subtabs)
        subtab = _css_block(".ws-subtab {")
        self.assertIn("44px", subtab)

    def test_09_four_tabs_unchanged(self):
        fn = _js_fn("_renderWorkspaceSkeleton")
        for tab_id in ("overview", "attention", "all-payments", "pilot-clients"):
            self.assertIn(tab_id, fn)


# ---------------------------------------------------------------------------
# T10-T13: Overview preview blocks
# ---------------------------------------------------------------------------

class TestOverviewPreviews(unittest.TestCase):

    def test_10_empty_state_when_attention_empty(self):
        fn = _js_fn("_wsRenderOverview")
        self.assertIn("Нет элементов, требующих внимания", fn)
        self.assertIn("Все счета в порядке.", fn)
        self.assertIn("ws-empty-state-icon--success", fn)

    def test_11_empty_state_icon_is_svg(self):
        js = _js()
        start = js.find("const WS_ICON_CHECK_BIG")
        self.assertNotEqual(start, -1)
        self.assertIn("<svg", js[start:start + 200])

    def test_12_recent_preview_uses_dedicated_markup_no_fake_data(self):
        fn = _js_fn("_wsRenderOverview")
        self.assertIn("ws-recent-list", fn)
        self.assertIn("ws-recent-row", fn)
        self.assertIn("_wsState.allPayments", fn)
        for fake in ("Тест Тестович", "userId=", "mock"):
            self.assertNotIn(fake, fn)

    def test_13_recent_preview_gated_on_loaded_data(self):
        fn = _js_fn("_wsRenderOverview")
        self.assertIn("Array.isArray(_wsState.allPayments) && _wsState.allPayments.length", fn)


# ---------------------------------------------------------------------------
# T14-T15: Capabilities unchanged
# ---------------------------------------------------------------------------

class TestCapabilitiesUnchanged(unittest.TestCase):

    def test_14_workspace_view_roles_unchanged(self):
        roles = _roles("WORKSPACE_VIEW_ROLES")
        for role in ("owner", "admin", "operations", "client_manager"):
            self.assertIn(f'"{role}"', roles)

    def test_15_pilot_and_approval_roles_unchanged(self):
        self.assertIn('"client_manager"', _roles("PILOT_MANAGE_ROLES"))
        self.assertNotIn('"client_manager"', _roles("PILOT_ADMIN_ROLES"))
        self.assertIn('"client_manager"', _roles("PAYMENT_APPROVAL_ROLES"))


# ---------------------------------------------------------------------------
# T16-T18: Other tabs untouched
# ---------------------------------------------------------------------------

class TestOtherTabsUntouched(unittest.TestCase):

    def test_16_attention_tab_renderer_untouched(self):
        # NOTE: intentionally updated for v7.1.6.1 step 2 (Attention visual
        # pass) — the Attention tab's own empty state now reuses the same
        # SVG green-check pattern as Overview's approved empty state instead
        # of an emoji. This is the ONE expected exception to the "Overview
        # unaffected" boundary tested elsewhere in this file: Overview's own
        # renderer/layout is untouched, only Attention's renderer changed.
        fn = _js_fn("_wsRenderAttention")
        self.assertIn("ws-empty-state-icon--success", fn)
        self.assertNotIn('_wsEmptyState("✅"', fn)

    def test_17_pilot_clients_renderer_untouched(self):
        fn = _js_fn("_wsRenderPilotClients")
        self.assertIn("Пока ни один клиент не добавлен в пилот", fn)
        self.assertNotIn("canAdminPilot", fn)

    def test_18_all_payments_renderer_untouched(self):
        # NOTE: intentionally updated for v7.1.6.1 step 3 — All Payments now
        # renders via its own dedicated _wsRenderPaymentCard() instead of the
        # shared renderPaymentIntentList() (that function still backs the
        # unrelated legacy admin #piList screen, untouched). This file's own
        # concern (Overview structure) is covered by the other tests here.
        # v7.1.6.2: card rendering moved into _wsRenderAllPaymentsResults().
        fn = _js_fn("_wsRenderAllPaymentsResults")
        self.assertIn("_wsRenderPaymentCard", fn)


# ---------------------------------------------------------------------------
# T19: Version
# ---------------------------------------------------------------------------

class TestVersionUnchanged(unittest.TestCase):

    def test_19_version_is_v7161(self):
        html = _html()
        js = _js()
        self.assertIn("v=7.1.12", html)
        self.assertIn('console.log("MiniApp version: v7.1.12.3")', js)


# ---------------------------------------------------------------------------
# T20-T21: Unrelated invariants
# ---------------------------------------------------------------------------

class TestUnrelatedInvariants(unittest.TestCase):

    def test_20_workspace_code_does_not_reference_food(self):
        js = _js()
        ws_start = js.find("const _wsState")
        ws_end = js.find("initKeyboardHandling")
        chunk = js[ws_start:ws_end]
        for bad in ("food_module", "food_menu", "loadKitchenEditor", "renderParentFoodMenu"):
            self.assertNotIn(bad, chunk)

    def test_21_test_role_panel_mechanics_unchanged(self):
        html = _html()
        ws_start = html.find('id="tab-payments-workspace"')
        ws_end = html.find("</section>", ws_start)
        self.assertNotIn('id="testRolePanel"', html[ws_start:ws_end])


if __name__ == "__main__":
    unittest.main(verbosity=2)
