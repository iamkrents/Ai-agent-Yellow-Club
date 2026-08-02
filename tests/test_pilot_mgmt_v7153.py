"""Regression tests for v7.1.5.3 — Pilot management capability, mobile UI, audit, keyboard.

Tests:
 T01  client_manager gets canManagePilotClients=true
 T02  client_manager does NOT get canAdminPilot
 T03  owner gets both canAdminPilot and canManagePilotClients
 T04  admin gets both canAdminPilot and canManagePilotClients
 T05  operations gets canManagePilotClients=true
 T06  teacher does NOT get canManagePilotClients
 T07  parent does NOT get canManagePilotClients
 T08  kitchen does NOT get canManagePilotClients
 T09  POST pilot/clients accessible to client_manager (PILOT_MANAGE_ROLES)
 T10  POST pilot/clients/{id}/mode accessible to client_manager
 T11  POST pilot/clients/{id}/remove accessible to client_manager
 T12  pilot_upsert_client uses PILOT_MANAGE_ROLES not PILOT_ADMIN_ROLES
 T13  pilot_update_mode uses PILOT_MANAGE_ROLES not PILOT_ADMIN_ROLES
 T14  pilot_remove_client uses PILOT_MANAGE_ROLES not PILOT_ADMIN_ROLES
 T15  canAdminPilot remains owner/admin only (PILOT_ADMIN_ROLES)
 T16  PILOT_MANAGE_ROLES includes operations
 T17  PILOT_MANAGE_ROLES does not include teacher
 T18  Pilot form uses canManagePilotClients in JS (not canAdminPilot)
 T19  Mobile pilot cards CSS exists (ws-pilot-card)
 T20  ws-pilot-cards hidden on desktop (min-width 541px)
 T21  ws-pilot-table-wrap hidden on mobile (max-width 540px)
 T22  Mobile card shows pilot mode badge classes
 T23  Remove confirm mentions Payment Intents are not deleted
 T24  Remove confirm does not just say "Удалить из пилота?" without context
 T25  Audit pilot_client_id passed in upsert
 T26  Audit pilot_client_id passed in mode change
 T27  Audit pilot_client_id passed in remove
 T28  actor_name resolved from auth.full_name in upsert
 T29  actor_name resolved in mode change
 T30  keyboard-open class toggled by initKeyboardHandling IIFE
 T31  bottom-tabbar hidden when keyboard-open (CSS)
 T32  visualViewport resize used in keyboard handler
 T33  Pilot fail-closed (not_in_pilot / disabled) preserved
 T34  Cache-bust and version marker are v7.1.8

Run:
    python -m unittest tests.test_pilot_mgmt_v7153 -v
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


def _pilot_manage_roles() -> str:
    m = re.search(r'PILOT_MANAGE_ROLES\s*=\s*\{([^}]+)\}', _server())
    assert m, "PILOT_MANAGE_ROLES not found in web_app_server.py"
    return m.group(1)


def _pilot_admin_roles() -> str:
    m = re.search(r'PILOT_ADMIN_ROLES\s*=\s*\{([^}]+)\}', _server())
    assert m, "PILOT_ADMIN_ROLES not found in web_app_server.py"
    return m.group(1)


def _capabilities_block() -> str:
    src = _server()
    start = src.find("def _capabilities_for_user(")
    end = src.find("\n    def ", start + 1)
    return src[start:end]


def _pilot_upsert_fn() -> str:
    src = _server()
    start = src.find("def pilot_upsert_client(")
    end = src.find("\n    def ", start + 1)
    return src[start:end]


def _pilot_mode_fn() -> str:
    src = _server()
    start = src.find("def pilot_update_mode(")
    end = src.find("\n    def ", start + 1)
    return src[start:end]


def _pilot_remove_fn() -> str:
    src = _server()
    start = src.find("def pilot_remove_client(")
    end = src.find("\n    def ", start + 1)
    return src[start:end]


def _render_pilot_fn() -> str:
    js = _js()
    start = js.find("function _wsRenderPilotClients(")
    end = js.find("\nfunction ", start + 1)
    return js[start:end]


def _pilot_client_card_fn() -> str:
    """v7.1.6.1 step 4: the per-client card markup was extracted out of
    _wsRenderPilotClients into its own _wsPilotClientCard() function."""
    js = _js()
    start = js.find("function _wsPilotClientCard(")
    end = js.find("\nfunction ", start + 1)
    return js[start:end]


def _remove_fn() -> str:
    """v7.1.6: _pilotRemove() now just opens the managed #pilotRemoveModal; the
    actual destructive call moved to confirmPilotRemove(). Slice covers both so
    tests can check the whole open->confirm flow, not just the entry point."""
    js = _js()
    start = js.find("function _pilotRemove(")
    assert start != -1, "_pilotRemove not found in app.js"
    conf_start = js.find("async function confirmPilotRemove(", start)
    if conf_start == -1:
        return js[start : start + 1500]
    conf_end = js.find("\nasync function ", conf_start + 1)
    if conf_end == -1:
        conf_end = js.find("\nfunction ", conf_start + 1)
    if conf_end == -1:
        conf_end = conf_start + 1500
    return js[start:conf_end]


def _keyboard_iife() -> str:
    js = _js()
    start = js.find("initKeyboardHandling")
    if start == -1:
        return ""
    # v7.1.6: the handler grew (visualViewport-driven scroll retries, swipe-dismiss
    # recovery) — widened from 2000 to comfortably cover the whole IIFE.
    return js[start : start + 6000]


# ---------------------------------------------------------------------------
# T01–T08: Capability role assignment
# ---------------------------------------------------------------------------

class TestCanManagePilotClientsCapability(unittest.TestCase):

    def test_01_client_manager_gets_can_manage_pilot_clients(self):
        """canManagePilotClients must be true for client_manager."""
        self.assertIn('"client_manager"', _pilot_manage_roles())

    def test_02_client_manager_not_gets_can_admin_pilot(self):
        """client_manager must NOT be in PILOT_ADMIN_ROLES (no canAdminPilot)."""
        self.assertNotIn('"client_manager"', _pilot_admin_roles())

    def test_03_owner_gets_both_capabilities(self):
        """owner must be in both PILOT_ADMIN_ROLES and PILOT_MANAGE_ROLES."""
        self.assertIn('"owner"', _pilot_admin_roles())
        self.assertIn('"owner"', _pilot_manage_roles())

    def test_04_admin_gets_both_capabilities(self):
        """admin must be in both PILOT_ADMIN_ROLES and PILOT_MANAGE_ROLES."""
        self.assertIn('"admin"', _pilot_admin_roles())
        self.assertIn('"admin"', _pilot_manage_roles())

    def test_05_operations_gets_can_manage_pilot_clients(self):
        """operations must be in PILOT_MANAGE_ROLES."""
        self.assertIn('"operations"', _pilot_manage_roles())

    def test_06_teacher_not_in_pilot_manage_roles(self):
        """teacher must NOT be in PILOT_MANAGE_ROLES."""
        self.assertNotIn('"teacher"', _pilot_manage_roles())

    def test_07_parent_not_in_pilot_manage_roles(self):
        """parent must NOT be in PILOT_MANAGE_ROLES."""
        self.assertNotIn('"parent"', _pilot_manage_roles())

    def test_08_kitchen_not_in_pilot_manage_roles(self):
        """kitchen must NOT be in PILOT_MANAGE_ROLES."""
        self.assertNotIn('"kitchen"', _pilot_manage_roles())


# ---------------------------------------------------------------------------
# T09–T17: Backend access control
# ---------------------------------------------------------------------------

class TestBackendAccessControl(unittest.TestCase):

    def test_09_pilot_upsert_accessible_to_client_manager(self):
        """pilot_upsert_client must check PILOT_MANAGE_ROLES, not PILOT_ADMIN_ROLES."""
        fn = _pilot_upsert_fn()
        self.assertIn("PILOT_MANAGE_ROLES", fn, "upsert must use PILOT_MANAGE_ROLES")
        self.assertNotIn("PILOT_ADMIN_ROLES", fn, "upsert must not use PILOT_ADMIN_ROLES")

    def test_10_pilot_update_mode_accessible_to_client_manager(self):
        """pilot_update_mode must check PILOT_MANAGE_ROLES, not PILOT_ADMIN_ROLES."""
        fn = _pilot_mode_fn()
        self.assertIn("PILOT_MANAGE_ROLES", fn, "update_mode must use PILOT_MANAGE_ROLES")
        self.assertNotIn("PILOT_ADMIN_ROLES", fn, "update_mode must not use PILOT_ADMIN_ROLES")

    def test_11_pilot_remove_accessible_to_client_manager(self):
        """pilot_remove_client must check PILOT_MANAGE_ROLES, not PILOT_ADMIN_ROLES."""
        fn = _pilot_remove_fn()
        self.assertIn("PILOT_MANAGE_ROLES", fn, "remove must use PILOT_MANAGE_ROLES")
        self.assertNotIn("PILOT_ADMIN_ROLES", fn, "remove must not use PILOT_ADMIN_ROLES")

    def test_12_pilot_upsert_denied_for_non_manage_roles(self):
        """pilot_upsert_client must return error when role not in PILOT_MANAGE_ROLES."""
        fn = _pilot_upsert_fn()
        self.assertIn("not in PILOT_MANAGE_ROLES", fn)
        self.assertIn("ok", fn.lower())
        self.assertIn("False", fn)

    def test_13_pilot_mode_denied_for_non_manage_roles(self):
        """pilot_update_mode must return error when role not in PILOT_MANAGE_ROLES."""
        fn = _pilot_mode_fn()
        self.assertIn("not in PILOT_MANAGE_ROLES", fn)

    def test_14_pilot_remove_denied_for_non_manage_roles(self):
        """pilot_remove_client must return error when role not in PILOT_MANAGE_ROLES."""
        fn = _pilot_remove_fn()
        self.assertIn("not in PILOT_MANAGE_ROLES", fn)

    def test_15_can_admin_pilot_remains_owner_admin_only(self):
        """canAdminPilot capability must only be True for PILOT_ADMIN_ROLES (owner/admin)."""
        caps = _capabilities_block()
        self.assertIn("canAdminPilot", caps, "canAdminPilot must exist in capabilities")
        self.assertIn("PILOT_ADMIN_ROLES", caps, "canAdminPilot must reference PILOT_ADMIN_ROLES")

    def test_16_pilot_manage_roles_includes_operations(self):
        """PILOT_MANAGE_ROLES must include 'operations'."""
        self.assertIn('"operations"', _pilot_manage_roles())

    def test_17_pilot_manage_roles_excludes_teacher(self):
        """PILOT_MANAGE_ROLES must NOT include 'teacher'."""
        self.assertNotIn('"teacher"', _pilot_manage_roles())


# ---------------------------------------------------------------------------
# T18–T24: Frontend UI
# ---------------------------------------------------------------------------

class TestFrontendPilotUI(unittest.TestCase):

    def test_18_pilot_form_uses_can_manage_pilot_clients(self):
        """_wsRenderPilotClients must gate on canManagePilotClients, not canAdminPilot."""
        fn = _render_pilot_fn()
        self.assertIn("canManagePilotClients", fn, "Must use canManagePilotClients")
        self.assertNotIn(
            "canAdminPilot", fn,
            "_wsRenderPilotClients must not reference canAdminPilot",
        )

    def test_19_mobile_pilot_cards_css_exists(self):
        """ws-pilot-card CSS must be defined for mobile card layout."""
        css = _css()
        self.assertIn(".ws-pilot-card", css, ".ws-pilot-card must be defined in styles.css")
        self.assertIn(".ws-pilot-cards", css, ".ws-pilot-cards container must be defined")

    def test_20_ws_pilot_cards_hidden_on_desktop(self):
        """ws-pilot-cards must be hidden at min-width >= 541px."""
        css = _css()
        m = re.search(
            r'@media[^{]*min-width\s*:\s*541px[^{]*\{[^}]*\.ws-pilot-cards[^}]*display\s*:\s*none',
            css, re.DOTALL
        )
        self.assertIsNotNone(
            m,
            "Expected @media (min-width: 541px) { .ws-pilot-cards { display: none } }",
        )

    def test_21_ws_pilot_table_hidden_on_mobile(self):
        """ws-pilot-table-wrap must be hidden at max-width <= 540px."""
        css = _css()
        m = re.search(
            r'@media[^{]*max-width\s*:\s*540px[^{]*\{[^}]*\.ws-pilot-table-wrap[^}]*display\s*:\s*none',
            css, re.DOTALL
        )
        self.assertIsNotNone(
            m,
            "Expected @media (max-width: 540px) { .ws-pilot-table-wrap { display: none } }",
        )

    def test_22_mobile_card_mode_badge_classes(self):
        """The per-client card must output mode-specific badge CSS classes.

        v7.1.6.1 step 4: this markup moved from inline in _wsRenderPilotClients
        into its own _wsPilotClientCard() function, and the class names became
        data-driven via the WS_PILOT_MODE_CLS lookup (same pattern used for
        Overview's WS_OVERVIEW_STAT_META) rather than inline per-mode literals.
        """
        fn = _pilot_client_card_fn()
        self.assertIn("WS_PILOT_MODE_CLS[c.mode]", fn)
        js = _js()
        idx = js.find("const WS_PILOT_MODE_CLS")
        self.assertNotEqual(idx, -1, "WS_PILOT_MODE_CLS not found in app.js")
        cls_block = js[idx : idx + 300]
        for cls in ("ws-pilot-mode-observe", "ws-pilot-mode-review", "ws-pilot-mode-auto", "ws-pilot-mode-disabled"):
            self.assertIn(cls, cls_block, f"Mobile card must reference mode badge class: {cls}")

    def test_23_remove_confirm_mentions_payment_intents_not_deleted(self):
        """Pilot removal flow must explicitly state payments/invoices are not deleted.

        v7.1.6: the warning text moved from a browser confirm() string into the
        static #pilotRemoveModal markup in index.html.
        """
        html = _html()
        modal_start = html.find('id="pilotRemoveModal"')
        self.assertNotEqual(modal_start, -1, "#pilotRemoveModal not found in index.html")
        modal_chunk = html[modal_start : modal_start + 1200]
        self.assertTrue(
            "платёж" in modal_chunk.lower() or "счет" in modal_chunk.lower() or "счёт" in modal_chunk.lower(),
            "pilotRemoveModal must mention payments/invoices are not deleted",
        )
        self.assertIn("не удаляются", modal_chunk, "pilotRemoveModal must state existing data is not deleted")

    def test_24_remove_confirm_explains_automation_only(self):
        """v7.1.6: destructive pilot removal must use the managed modal, not browser confirm()."""
        fn = _remove_fn()
        self.assertNotIn("confirm(", fn, "_pilotRemove must no longer use browser confirm()")
        self.assertIn("piModalOpen", fn, "_pilotRemove must open the managed #pilotRemoveModal")
        self.assertIn(
            "/remove", fn,
            "confirmPilotRemove must still call the existing pilot remove endpoint",
        )
        html = _html()
        modal_start = html.find('id="pilotRemoveModal"')
        modal_chunk = html[modal_start : modal_start + 1200]
        self.assertTrue(
            "автоматизац" in modal_chunk or "участие" in modal_chunk,
            "pilotRemoveModal text must clarify that only automation participation is removed",
        )


# ---------------------------------------------------------------------------
# T25–T29: Audit completeness
# ---------------------------------------------------------------------------

class TestAuditCompleteness(unittest.TestCase):

    def test_25_audit_upsert_passes_pilot_client_id(self):
        """pilot_upsert_client must pass pilot_client_id to create_pilot_audit_event."""
        fn = _pilot_upsert_fn()
        self.assertIn("pilot_client_id", fn, "upsert audit must include pilot_client_id")
        # Must come from the returned client row, not hardcoded None
        self.assertIn('client.get("id")', fn, "pilot_client_id must be taken from returned client row")

    def test_26_audit_mode_change_passes_pilot_client_id(self):
        """pilot_update_mode must pass pilot_client_id to create_pilot_audit_event."""
        fn = _pilot_mode_fn()
        self.assertIn("pilot_client_id", fn, "mode change audit must include pilot_client_id")
        self.assertIn('existing.get("id")', fn, "pilot_client_id must be taken from existing record")

    def test_27_audit_remove_passes_pilot_client_id(self):
        """pilot_remove_client must pass pilot_client_id to create_pilot_audit_event."""
        fn = _pilot_remove_fn()
        self.assertIn("pilot_client_id", fn, "remove audit must include pilot_client_id")
        self.assertIn("pilot_client_id", fn)

    def test_28_actor_name_resolved_in_upsert(self):
        """pilot_upsert_client must derive actor_name from auth.full_name."""
        fn = _pilot_upsert_fn()
        self.assertIn("actor_name", fn)
        self.assertIn('full_name', fn, "actor_name must be derived from auth.get('full_name')")

    def test_29_actor_name_resolved_in_mode_change(self):
        """pilot_update_mode must derive actor_name from auth.full_name."""
        fn = _pilot_mode_fn()
        self.assertIn("actor_name", fn)
        self.assertIn("full_name", fn, "actor_name must be derived from auth.get('full_name')")


# ---------------------------------------------------------------------------
# T30–T32: Keyboard handling
# ---------------------------------------------------------------------------

class TestKeyboardHandling(unittest.TestCase):

    def test_30_keyboard_open_class_in_iife(self):
        """initKeyboardHandling IIFE must toggle keyboard-open class on body."""
        iife = _keyboard_iife()
        self.assertNotEqual(iife, "", "initKeyboardHandling must exist in app.js")
        self.assertIn("keyboard-open", iife, "IIFE must toggle 'keyboard-open' class")
        self.assertIn("classList.toggle", iife, "IIFE must use classList.toggle")

    def test_31_bottom_tabbar_hidden_when_keyboard_open(self):
        """body.keyboard-open .tabs.bottom-tabbar must be hidden in CSS."""
        css = _css()
        self.assertIn(
            "body.keyboard-open .tabs.bottom-tabbar",
            css,
            "CSS must hide .tabs.bottom-tabbar when body has keyboard-open class",
        )

    def test_32_visual_viewport_used_in_keyboard_handler(self):
        """initKeyboardHandling must use visualViewport for keyboard detection."""
        iife = _keyboard_iife()
        self.assertIn(
            "visualViewport", iife,
            "initKeyboardHandling must use window.visualViewport",
        )


# ---------------------------------------------------------------------------
# T33: Pilot safety
# ---------------------------------------------------------------------------

class TestPilotSafety(unittest.TestCase):

    def test_33_pilot_fail_closed_preserved(self):
        """Pilot gate must remain fail-closed: skip on disabled or not_in_pilot."""
        src = _server()
        self.assertIn(
            '"disabled", "not_in_pilot"',
            src,
            "Pilot fail-closed gate must be unchanged",
        )


# ---------------------------------------------------------------------------
# T34: Version
# ---------------------------------------------------------------------------

class TestVersionV7153(unittest.TestCase):

    def test_34_cache_bust_and_version_are_v7153(self):
        """index.html cache-bust and app.js console version must be v7.1.8."""
        html = _html()
        js = _js()
        self.assertIn("v=7.1.13", html, "index.html cache-bust must be v=7.1.13")
        self.assertIn(
            'console.log("MiniApp version: v7.1.13.1")',
            js,
            "app.js must log v7.1.13",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
