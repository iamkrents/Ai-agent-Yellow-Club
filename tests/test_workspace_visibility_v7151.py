"""Regression tests for v7.1.5.1 — Payments Workspace visibility and role gate.

Tests:
 T01  tab-payments-workspace panel has payments-workspace-only class
 T02  Owner allowed by WORKSPACE_VIEW_ROLES
 T03  Admin allowed by WORKSPACE_VIEW_ROLES
 T04  Operations allowed by WORKSPACE_VIEW_ROLES
 T05  Client_manager allowed by WORKSPACE_VIEW_ROLES
 T06  Teacher NOT in WORKSPACE_VIEW_ROLES
 T07  Parent NOT in WORKSPACE_VIEW_ROLES
 T08  Kitchen NOT in WORKSPACE_VIEW_ROLES
 T09  activateTab blocks navigation when both tab and panel are hidden
 T10  loadPaymentsWorkspace triggered on tab activation
 T11  testRolePanel is NOT inside tab-payments-workspace section
 T12  renderTestRolePanel gated by canUseTestRoles
 T13  activateTab does not touch testRolePanel
 T14  Real client_manager has no canUseTestRoles by default
 T15  Empty pilot state shows human-readable message
 T16  _loadWorkspaceStats shows error message and retry on failure
 T17  Automation pipeline never auto-enrolls clients in pilot
 T18  Pilot gate remains fail-closed for not_in_pilot / disabled
 T19  Payments workspace JS does not reference food module
 T20  pilot_auto_mk_terms_sync defaults to False
 T21  Cache-bust and version marker are v7.1.5.1

Run:
    python -m unittest tests.test_workspace_visibility_v7151 -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS   = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
SERVER   = ROOT / "web_app_server.py"
CONFIG   = ROOT / "config.py"

_js_cache: str | None = None
_html_cache: str | None = None
_server_cache: str | None = None


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


def _workspace_view_roles() -> str:
    m = re.search(r'WORKSPACE_VIEW_ROLES\s*=\s*\{([^}]+)\}', _server())
    assert m, "WORKSPACE_VIEW_ROLES not found in web_app_server.py"
    return m.group(1)


# ---------------------------------------------------------------------------
# T01: Panel class
# ---------------------------------------------------------------------------

class TestPanelRoleGateClass(unittest.TestCase):

    def test_01_tab_panel_has_payments_workspace_only_class(self):
        """tab-payments-workspace section must carry payments-workspace-only class."""
        m = re.search(r'<section[^>]+id="tab-payments-workspace"[^>]*>', _html())
        self.assertIsNotNone(m, "tab-payments-workspace section not found in index.html")
        self.assertIn(
            "payments-workspace-only", m.group(0),
            "tab-payments-workspace must have class 'payments-workspace-only' so the "
            "role gate can remove 'hidden' before activateTab fires",
        )


# ---------------------------------------------------------------------------
# T02–T08: Role membership
# ---------------------------------------------------------------------------

class TestWorkspaceViewRoles(unittest.TestCase):

    def test_02_owner_in_workspace_roles(self):
        """WORKSPACE_VIEW_ROLES must include 'owner'."""
        self.assertIn('"owner"', _workspace_view_roles())

    def test_03_admin_in_workspace_roles(self):
        """WORKSPACE_VIEW_ROLES must include 'admin'."""
        self.assertIn('"admin"', _workspace_view_roles())

    def test_04_operations_in_workspace_roles(self):
        """WORKSPACE_VIEW_ROLES must include 'operations'."""
        self.assertIn('"operations"', _workspace_view_roles())

    def test_05_client_manager_in_workspace_roles(self):
        """WORKSPACE_VIEW_ROLES must include 'client_manager'."""
        self.assertIn('"client_manager"', _workspace_view_roles())

    def test_06_teacher_not_in_workspace_roles(self):
        """WORKSPACE_VIEW_ROLES must NOT include 'teacher'."""
        self.assertNotIn('"teacher"', _workspace_view_roles())

    def test_07_parent_not_in_workspace_roles(self):
        """WORKSPACE_VIEW_ROLES must NOT include 'parent'."""
        self.assertNotIn('"parent"', _workspace_view_roles())

    def test_08_kitchen_not_in_workspace_roles(self):
        """WORKSPACE_VIEW_ROLES must NOT include 'kitchen'."""
        self.assertNotIn('"kitchen"', _workspace_view_roles())


# ---------------------------------------------------------------------------
# T09–T10: activateTab
# ---------------------------------------------------------------------------

class TestActivateTab(unittest.TestCase):

    def _activate_tab_body(self) -> str:
        js = _js()
        start = js.find("function activateTab(")
        end = js.find("\nfunction ", start + 1)
        return js[start:end]

    def test_09_activate_tab_guard_blocks_when_both_hidden(self):
        """activateTab returns early only when BOTH tab AND panel have 'hidden'."""
        body = self._activate_tab_body()
        self.assertIn(
            'tab.classList.contains("hidden") && panel.classList.contains("hidden")',
            body,
            "Guard must use && so that one-visible / one-hidden navigation still proceeds",
        )

    def test_10_load_payments_workspace_triggered(self):
        """activateTab('payments-workspace') calls loadPaymentsWorkspace()."""
        body = self._activate_tab_body()
        self.assertIn(
            'if (name === "payments-workspace") loadPaymentsWorkspace()',
            body,
        )


# ---------------------------------------------------------------------------
# T11–T14: testRolePanel preservation
# ---------------------------------------------------------------------------

class TestTestRolePanelPreservation(unittest.TestCase):

    def test_11_test_role_panel_not_inside_workspace_section(self):
        """testRolePanel is NOT nested inside tab-payments-workspace."""
        html = _html()
        ws_start = html.find('id="tab-payments-workspace"')
        self.assertNotEqual(ws_start, -1, "tab-payments-workspace section not found")
        ws_end = html.find("</section>", ws_start)
        workspace_chunk = html[ws_start:ws_end]
        self.assertNotIn(
            'id="testRolePanel"', workspace_chunk,
            "testRolePanel must NOT be nested inside tab-payments-workspace",
        )

    def test_12_render_test_role_panel_gated_by_capability(self):
        """renderTestRolePanel hides panel for users without canUseTestRoles."""
        js = _js()
        fn_start = js.find("function renderTestRolePanel()")
        self.assertNotEqual(fn_start, -1, "renderTestRolePanel not found")
        fn_body = js[fn_start : fn_start + 500]
        self.assertIn(
            "canUseTestRoles", fn_body,
            "renderTestRolePanel must check canUseTestRoles capability",
        )

    def test_13_activate_tab_does_not_touch_test_role_panel(self):
        """activateTab must not add or remove classes on testRolePanel."""
        js = _js()
        fn_start = js.find("function activateTab(")
        fn_end = js.find("\nfunction ", fn_start + 1)
        fn_body = js[fn_start:fn_end]
        self.assertNotIn(
            "testRolePanel", fn_body,
            "activateTab must not reference testRolePanel — panel visibility is "
            "managed by renderTestRolePanel / setupRoleUi only",
        )

    def test_14_can_use_test_roles_is_role_gated(self):
        """canUseTestRoles capability must not be unconditionally True for all roles."""
        src = _server()
        self.assertIn("canUseTestRoles", src, "canUseTestRoles must exist in server")
        m = re.search(r'"canUseTestRoles"\s*:\s*(.+?)(?:[,\n\r])', src)
        self.assertIsNotNone(m, "canUseTestRoles capability assignment not found")
        val = m.group(1).strip()
        self.assertNotEqual(
            val, "True",
            "canUseTestRoles must not be hardcoded True for all roles",
        )


# ---------------------------------------------------------------------------
# T15: Empty pilot state
# ---------------------------------------------------------------------------

class TestEmptyPilotState(unittest.TestCase):

    def test_15_empty_pilot_shows_human_readable_message(self):
        """_wsRenderPilotClients shows 'Пока ни один клиент не добавлен в пилот'."""
        js = _js()
        fn_start = js.find("function _wsRenderPilotClients(")
        self.assertNotEqual(fn_start, -1)
        fn_end = js.find("\nfunction ", fn_start + 1)
        fn_body = js[fn_start:fn_end]
        self.assertIn(
            "Пока ни один клиент не добавлен в пилот",
            fn_body,
            "Empty pilot state must show human-readable message",
        )


# ---------------------------------------------------------------------------
# T16: Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling(unittest.TestCase):

    def test_16_stats_error_shows_message_and_retry(self):
        """_loadWorkspaceStats shows Ошибка + retry button on API failure."""
        js = _js()
        fn_start = js.find("async function _loadWorkspaceStats(")
        self.assertNotEqual(fn_start, -1)
        fn_end = js.find("\nasync function _loadWorkspaceAttention", fn_start + 1)
        if fn_end == -1:
            fn_end = js.find("\nfunction ", fn_start + 1)
        fn_body = js[fn_start:fn_end]

        self.assertIn("catch", fn_body, "Error must be caught")
        self.assertNotIn(
            "/* stats are optional */", fn_body,
            "Error must not be silently discarded",
        )
        self.assertIn("Ошибка", fn_body, "Error message must be shown to user")
        self.assertIn(
            "loadPaymentsWorkspace()", fn_body,
            "Retry button must call loadPaymentsWorkspace()",
        )


# ---------------------------------------------------------------------------
# T17: No auto-enrollment
# ---------------------------------------------------------------------------

class TestNoPilotAutoCreation(unittest.TestCase):

    def test_17_automation_pipeline_does_not_auto_enroll(self):
        """_process_single_automation_item_from_invoice must not call upsert_pilot_client."""
        src = _server()
        fn_start = src.find("def _process_single_automation_item_from_invoice(")
        self.assertNotEqual(fn_start, -1)
        fn_end = src.find("\n    def ", fn_start + 1)
        if fn_end == -1:
            fn_end = fn_start + 8000
        fn_body = src[fn_start:fn_end]
        self.assertNotIn(
            "upsert_pilot_client", fn_body,
            "Automation pipeline must never auto-enroll clients in pilot",
        )


# ---------------------------------------------------------------------------
# T18: Pilot gate preserved
# ---------------------------------------------------------------------------

class TestPilotGatePreserved(unittest.TestCase):

    def test_18_pilot_gate_fail_closed(self):
        """Pilot gate skips when client is not_in_pilot or disabled."""
        src = _server()
        self.assertIn(
            '"disabled", "not_in_pilot"',
            src,
            "Pilot gate must remain fail-closed: skip on disabled or not_in_pilot",
        )


# ---------------------------------------------------------------------------
# T19: Food module not changed
# ---------------------------------------------------------------------------

class TestFoodModuleUntouched(unittest.TestCase):

    def test_19_workspace_js_does_not_reference_food_module(self):
        """Payments workspace JS functions must not call food module functions."""
        js = _js()
        ws_start = js.find("const _wsState")
        self.assertNotEqual(ws_start, -1)
        ws_chunk = js[ws_start : ws_start + 5000]
        for bad in ("food_module", "food_menu", "loadKitchenEditor", "renderParentFoodMenu"):
            self.assertNotIn(
                bad, ws_chunk,
                f"Workspace code must not reference food module symbol: {bad}",
            )


# ---------------------------------------------------------------------------
# T20: MK terms sync default
# ---------------------------------------------------------------------------

class TestMkTermsSyncDefault(unittest.TestCase):

    def test_20_pilot_auto_mk_terms_sync_defaults_false(self):
        """MK subscription terms auto-sync must default to disabled."""
        cfg = CONFIG.read_text(encoding="utf-8")
        m = re.search(r'payment_mk_subscription_terms_sync_enabled\s*:\s*bool\s*=\s*(\w+)', cfg)
        self.assertIsNotNone(m, "payment_mk_subscription_terms_sync_enabled not found in config.py")
        self.assertEqual(
            m.group(1), "False",
            "payment_mk_subscription_terms_sync_enabled must default to False",
        )


# ---------------------------------------------------------------------------
# T21: Version / cache-bust
# ---------------------------------------------------------------------------

class TestVersionV7151(unittest.TestCase):

    def test_21_cache_bust_and_version_are_v7153(self):
        """index.html cache-bust and app.js version marker must be v7.1.7."""
        html = _html()
        js = _js()
        self.assertIn("v=7.1.7", html, "index.html cache-bust must be v=7.1.7")
        self.assertIn("v7.1.7", js, "app.js version marker must be v7.1.7")


if __name__ == "__main__":
    unittest.main(verbosity=2)
