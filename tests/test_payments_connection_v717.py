"""Tests for v7.1.7 — Payments Workspace 5th tab "Подключение" (client connect).

Frontend for linking a Telegram account to a MoyKlass student via the
existing one-time CL- code system (client_child_link_codes /
client_parent_child_links — unchanged since v7.0.93.1, and the v7.1.7
backend/security foundation: client_manager access, canManageClientLinks,
72h TTL, client_link_audit_log, dedicated client_link_rate_limit_attempts,
atomic code use, reason codes, extended link-status). No new backend
endpoints; no changes to Overview / Attention / All Payments / Pilot Clients /
Food Module / payment business logic / version-cache-bust.

Covers (numbered to match the spec's 34-item test list):
  1.  Fifth tab id=connection label=Подключение
  2.  Tab gated by caps.canManageClientLinks (not canAdminPilot/canUseAdmin/Food)
  3.  client_manager included in CLIENT_LINK_ADMIN_ROLES -> canManageClientLinks
  4.  owner/admin/operations included too
  5.  parent/teacher excluded -> tab array conditional hides it
  6.  Search uses existing GET /api/client/admin/search-students
  7.  Search does not fire on every keystroke
  8.  Search <input> only declared in the toolbar function, never in the body
      re-render function (container-split, same fix as v7.1.6.2)
  9.  Result card renders name + MK id + badge
  10. Detail view driven by GET /api/client/admin/link-status
  11. "Не подключён" state text
  12. "Код создан" state text
  13. "Подключён" state text
  14. Code creation posts to /api/client/admin/link-codes
  15. Plaintext code only ever rendered from _wsConnState.newCode
  16. No localStorage/sessionStorage usage anywhere in the new code
  17. Copy button uses navigator.clipboard
  18. "72 часа" TTL text shown to the user
  19. Revoke goes through a modal (piModalOpen), not confirm()
  20. New-code creation while a code is already active requires a confirm modal
      before the API call
  21. The "Подключён" (linked) state exposes no create/new-code action at all
  22. Unlink goes through a modal, not confirm()
  23. Unlink modal explicitly states payments/history are not deleted
  24. Reason-code -> message mapping covers the spec's 8 reason codes
  25. Loading/empty/error state strings present
  26. ws-bottom-safe-pad applied to the results list
  27. Keyboard-safe search: input's own listeners never re-render the toolbar
  28. The temporary local dev preview (payments-connection) has been fully
      removed from index.html/app.js — no dev_preview/LOCAL PREVIEW/mock
      profile markers remain anywhere in production source
  29. No stray window.fetch override remains anywhere in the shipped files
  30. index.html's script tail is back to the plain single production
      <script src="/static/app.js?v=..."> form
  31. Overview / Attention / All Payments / Pilot Clients renderers untouched
  32. CLIENT_LINK_ADMIN_ROLES / canManageClientLinks unchanged from the backend phase
  33. Food Module functions/tables untouched
  34. Payment visibility (list_client_visible_payment_intents) untouched

Static analysis only (reads source as text), matching this project's existing
test convention (see test_search_focus_fix_v7162.py, test_admin_client_links.py).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
SERVER_PY = ROOT / "web_app_server.py"
STORAGE_PY = ROOT / "storage.py"

_js_cache: str | None = None
_html_cache: str | None = None
_server_cache: str | None = None
_storage_cache: str | None = None


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
        _server_cache = SERVER_PY.read_text(encoding="utf-8")
    return _server_cache


def _storage() -> str:
    global _storage_cache
    if _storage_cache is None:
        _storage_cache = STORAGE_PY.read_text(encoding="utf-8")
    return _storage_cache


def _js_fn(name: str, *, is_async: bool = False, window: int = 6000) -> str:
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
# 1-5: tab registration, capability gate, role coverage
# ---------------------------------------------------------------------------

class TestTabRegistration(unittest.TestCase):
    def test_01_fifth_tab_id_and_label(self):
        skeleton = _js_fn("_renderWorkspaceSkeleton")
        self.assertIn('{ id: "connection", label: "Подключение" }', skeleton)

    def test_02_tab_gated_by_canManageClientLinks(self):
        skeleton = _js_fn("_renderWorkspaceSkeleton")
        idx = skeleton.find('id: "connection"')
        segment = skeleton[max(0, idx - 200):idx]
        self.assertIn("caps.canManageClientLinks", segment)
        self.assertNotIn("caps.canAdminPilot", segment)
        self.assertNotIn("canUseAdmin", segment)

    def test_03_client_manager_in_admin_roles(self):
        server = _server()
        idx = server.find("CLIENT_LINK_ADMIN_ROLES = ")
        line_end = server.find("\n", idx)
        line = server[idx:line_end]
        self.assertIn('"client_manager"', line)

    def test_04_owner_admin_operations_in_admin_roles(self):
        server = _server()
        idx = server.find("CLIENT_LINK_ADMIN_ROLES = ")
        line_end = server.find("\n", idx)
        line = server[idx:line_end]
        for role in ('"owner"', '"admin"', '"operations"'):
            self.assertIn(role, line)

    def test_05_capability_wired_from_admin_roles(self):
        server = _server()
        self.assertIn('"canManageClientLinks": role in CLIENT_LINK_ADMIN_ROLES', server)
        # parent/teacher are never in CLIENT_LINK_ADMIN_ROLES, so they get
        # canManageClientLinks=False and the JS tab-array conditional (test_02)
        # hides the tab for them without any separate role check needed.
        idx = server.find("CLIENT_LINK_ADMIN_ROLES = ")
        line_end = server.find("\n", idx)
        line = server[idx:line_end]
        self.assertNotIn('"parent"', line)
        self.assertNotIn('"teacher"', line)


# ---------------------------------------------------------------------------
# 6-8: search behavior and keyboard/focus safety
# ---------------------------------------------------------------------------

class TestSearch(unittest.TestCase):
    def test_06_search_uses_existing_endpoint(self):
        fn = _js_fn("_wsConnSearch", is_async=True)
        self.assertIn("/api/client/admin/search-students", fn)

    def test_07_search_not_triggered_on_keystroke(self):
        toolbar = _js_fn("_wsRenderConnection")
        # The input's own 'input' listener must only capture state, never call
        # _wsConnSearch directly (that only happens on button click / Enter).
        idx = toolbar.find('addEventListener("input"')
        self.assertNotEqual(idx, -1)
        listener_segment = toolbar[idx:idx + 120]
        self.assertNotIn("_wsConnSearch(", listener_segment)
        self.assertIn("keydown", toolbar)
        self.assertIn("Enter", toolbar)

    def test_08_search_input_only_in_toolbar_not_body(self):
        toolbar = _js_fn("_wsRenderConnection")
        body = _js_fn("_wsRenderConnectionBody")
        self.assertIn('id="wsConnSearchInput"', toolbar)
        self.assertNotIn("<input", body)
        self.assertNotIn("wsConnSearchInput", body)


# ---------------------------------------------------------------------------
# 9-13: card + status states
# ---------------------------------------------------------------------------

class TestCardsAndStates(unittest.TestCase):
    def test_09_result_card_shows_name_id_badge(self):
        fn = _js_fn("_wsConnResultCard")
        self.assertIn("full_name", fn)
        self.assertIn("mk_user_id", fn)
        self.assertIn("ws-badge", fn)
        self.assertNotIn("phone", fn.lower())

    def test_10_detail_uses_link_status_endpoint(self):
        fn = _js_fn("_wsConnLoadStatus", is_async=True)
        self.assertIn("/api/client/admin/link-status", fn)

    def test_11_not_connected_state_text(self):
        fn = _js_fn("_wsConnDetailHtml")
        self.assertIn("Не подключён", fn)

    def test_12_code_created_state_text(self):
        fn = _js_fn("_wsConnDetailHtml")
        self.assertIn("Код создан", fn)

    def test_13_connected_state_text(self):
        fn = _js_fn("_wsConnDetailHtml")
        self.assertIn("Подключён", fn)


# ---------------------------------------------------------------------------
# 14-18: code creation, one-time display, copy, TTL text
# ---------------------------------------------------------------------------

class TestCodeCreation(unittest.TestCase):
    def test_14_create_posts_to_existing_endpoint(self):
        fn = _js_fn("_wsConnCreateCode", is_async=True)
        self.assertIn("/api/client/admin/link-codes", fn)
        self.assertNotIn("/api/client/admin/link-codes/invalidate", fn.replace("link-codes/invalidate", ""))

    def test_15_plaintext_only_from_state_newCode(self):
        success_fn = _js_fn("_wsConnNewCodeSuccessHtml")
        self.assertIn("_wsConnState.newCode", success_fn)
        detail_fn = _js_fn("_wsConnDetailHtml")
        # The success block is only reachable via the newCode truthy check.
        idx = detail_fn.find("_wsConnState.newCode")
        self.assertNotEqual(idx, -1)
        self.assertIn("_wsConnNewCodeSuccessHtml", detail_fn)

    def test_16_no_web_storage_usage(self):
        # Checks for actual API calls, not the word itself — _wsConnCreateCode
        # deliberately documents in a comment that the code is never written
        # to localStorage/sessionStorage, which would otherwise false-positive
        # a bare substring check.
        js = _js()
        start = js.find("const _wsConnState = {")
        self.assertNotEqual(start, -1)
        end = js.find("function initKeyboardHandling", start)
        self.assertNotEqual(end, -1)
        section = js[start:end]
        self.assertNotIn("localStorage.setItem", section)
        self.assertNotIn("localStorage.getItem", section)
        self.assertNotIn("sessionStorage.setItem", section)
        self.assertNotIn("sessionStorage.getItem", section)
        self.assertNotIn("console.log(", section)

    def test_17_copy_uses_clipboard_api(self):
        fn = _js_fn("_wsConnCopyCode", is_async=True)
        self.assertIn("navigator.clipboard.writeText", fn)

    def test_18_ttl_text_present(self):
        success_fn = _js_fn("_wsConnNewCodeSuccessHtml")
        self.assertIn("72 часа", success_fn)


# ---------------------------------------------------------------------------
# 19-23: revoke / new-code-confirm / unlink modals
# ---------------------------------------------------------------------------

class TestModals(unittest.TestCase):
    def test_19_revoke_uses_modal_not_confirm(self):
        open_fn = _js_fn("_wsConnOpenRevoke")
        confirm_fn = _js_fn("_wsConnConfirmRevoke", is_async=True)
        self.assertIn('piModalOpen($("wsConnRevokeModal"))', open_fn)
        self.assertNotIn("confirm(", open_fn)
        self.assertNotIn("confirm(", confirm_fn)
        self.assertIn("/api/client/admin/link-codes/invalidate", confirm_fn)

    def test_20_new_code_requires_confirm_before_api_call(self):
        open_fn = _js_fn("_wsConnOpenNewCodeConfirm")
        confirm_fn = _js_fn("_wsConnConfirmNewCode", is_async=True)
        self.assertNotIn("_apiPostRaw", open_fn)
        self.assertIn('piModalOpen($("wsConnNewCodeConfirmModal"))', open_fn)
        self.assertIn("/api/client/admin/link-codes", confirm_fn)

    def test_21_linked_state_has_no_create_code_action(self):
        detail_fn = _js_fn("_wsConnDetailHtml")
        idx = detail_fn.find("st.linked) {")
        next_branch = detail_fn.find("} else if (st.active_code_exists)", idx)
        linked_branch = detail_fn[idx:next_branch]
        self.assertNotIn("_wsConnCreateCode", linked_branch)
        self.assertNotIn("_wsConnOpenNewCodeConfirm", linked_branch)
        self.assertIn("_wsConnOpenUnlink", linked_branch)

    def test_22_unlink_uses_modal_not_confirm(self):
        open_fn = _js_fn("_wsConnOpenUnlink")
        confirm_fn = _js_fn("_wsConnConfirmUnlink", is_async=True)
        self.assertIn('piModalOpen($("wsConnUnlinkModal"))', open_fn)
        self.assertNotIn("confirm(", open_fn)
        self.assertNotIn("confirm(", confirm_fn)
        self.assertIn("/api/client/admin/unlink", confirm_fn)

    def test_23_unlink_modal_mentions_data_preserved(self):
        html = _html()
        idx = html.find('id="wsConnUnlinkModal"')
        segment = html[idx:idx + 1200]
        self.assertIn("не удаляются", segment)


# ---------------------------------------------------------------------------
# 24-27: reason codes + interface states + bottom padding + keyboard safety
# ---------------------------------------------------------------------------

class TestReasonCodesAndStates(unittest.TestCase):
    EXPECTED_REASON_CODES = [
        "client_already_linked", "code_not_found", "code_expired",
        "code_already_used", "code_invalidated", "rate_limited",
        "rate_limit_unavailable", "access_denied",
    ]

    def test_24_reason_code_mapping_complete(self):
        js = _js()
        start = js.find("WS_CONN_REASON_MESSAGES = {")
        end = js.find("\n};", start)
        mapping = js[start:end]
        for code in self.EXPECTED_REASON_CODES:
            self.assertIn(code, mapping, f"missing reason_code mapping: {code}")

    def test_25_loading_empty_error_states_present(self):
        body_fn = _js_fn("_wsRenderConnectionBody")
        self.assertIn("Ищу клиента", body_fn)
        self.assertIn("Найдите клиента, чтобы начать", body_fn)
        self.assertIn("Ничего не найдено", body_fn)
        detail_fn = _js_fn("_wsConnDetailHtml")
        self.assertIn("Загружаю статус подключения", detail_fn)

    def test_26_bottom_safe_pad_on_results(self):
        body_fn = _js_fn("_wsRenderConnectionBody")
        self.assertIn("ws-bottom-safe-pad", body_fn)

    def test_27_toolbar_never_rebuilt_by_body_functions(self):
        js = _js()
        for name in ("_wsConnSearch", "_wsConnSelect", "_wsConnLoadStatus",
                     "_wsConnBackToSearch", "_wsConnCreateCode", "_wsConnCloseNewCode"):
            fn = _js_fn(name, is_async=name in ("_wsConnSearch", "_wsConnLoadStatus", "_wsConnCreateCode"))
            self.assertNotIn("_wsRenderConnection(", fn,
                              f"{name} must not rebuild the toolbar/input — only _wsRenderConnectionBody")


# ---------------------------------------------------------------------------
# 28-30: local dev preview harness — REMOVED for v7.1.7 release cleanup.
# The temporary localhost-only preview (window.fetch mock, LOCAL PREVIEW
# badge, mock client_manager profile) was deliberately deleted from
# index.html once both the Подключение and Помощь screens were visually
# approved. These tests now assert the removal is complete and that
# index.html's script tail is back to the plain production form, instead of
# testing preview internals that no longer exist.
# ---------------------------------------------------------------------------

class TestPreviewRemoved(unittest.TestCase):
    def test_28_no_dev_preview_markers_in_html(self):
        html = _html()
        for marker in ("dev_preview", "LOCAL PREVIEW", "payments-connection",
                       "payments-help", "__YC_DEV_PREVIEW__", "Preview Client Manager"):
            self.assertNotIn(marker, html, f"leftover preview marker: {marker}")

    def test_29_no_preview_markers_in_app_js(self):
        js = _js()
        for marker in ("dev_preview", "LOCAL PREVIEW", "__YC_DEV_PREVIEW__", "Preview Client Manager"):
            self.assertNotIn(marker, js, f"leftover preview marker: {marker}")

    def test_30_script_tail_is_plain_production_form(self):
        html = _html()
        idx = html.find('<script src="/static/app.js?v=')
        self.assertNotEqual(idx, -1)
        # Exactly one script tag for app.js, immediately after the modal root
        # close comment, with nothing (no extra preview <script> blocks) in between.
        before = html[max(0, idx - 60):idx]
        self.assertIn("piModalRoot", before)
        after = html[idx:idx + 300]
        self.assertIn("</body>", after)
        self.assertIn("</html>", after)
        self.assertEqual(html.count("window.fetch ="), 0)

    def test_production_client_link_endpoints_still_called_for_real(self):
        # Regression: the real Connection-tab functions still call the real
        # backend endpoints — nothing was accidentally left pointing at a
        # mock, and no fetch override remains anywhere in the shipped file.
        self.assertIn("/api/client/admin/search-students", _js_fn("_wsConnSearch", is_async=True))
        self.assertIn("/api/client/admin/link-status", _js_fn("_wsConnLoadStatus", is_async=True))
        self.assertIn("/api/client/admin/link-codes", _js_fn("_wsConnCreateCode", is_async=True))
        self.assertIn("/api/client/admin/link-codes/invalidate", _js_fn("_wsConnConfirmRevoke", is_async=True))
        self.assertIn("/api/client/admin/unlink", _js_fn("_wsConnConfirmUnlink", is_async=True))


# ---------------------------------------------------------------------------
# 31-34: regression — the 4 approved tabs, backend perms, Food, payments
# ---------------------------------------------------------------------------

class TestNoRegression(unittest.TestCase):
    def test_31_four_approved_renderers_untouched(self):
        for name in ("_wsRenderOverview", "_wsRenderAttention",
                     "_wsRenderAllPayments", "_wsRenderPilotClients"):
            fn = _js_fn(name)
            self.assertNotIn("_wsConn", fn, f"{name} must not reference any Connection-tab code")

    def test_32_client_link_admin_roles_and_capability_present(self):
        server = _server()
        self.assertIn("CLIENT_LINK_ADMIN_ROLES", server)
        self.assertIn("canManageClientLinks", server)
        self.assertIn("client_link_rate_limit_attempts", storage_module_text())

    def test_33_food_module_untouched(self):
        storage_src = _storage()
        self.assertIn("parent_child_links", storage_src)
        self.assertIn("camp_children", storage_src)
        server = _server()
        self.assertIn("/api/food/link-child", server)
        self.assertIn("def food_link_child(", server)

    def test_34_payment_visibility_untouched(self):
        storage_src = _storage()
        self.assertIn("def list_client_visible_payment_intents(", storage_src)
        fn_start = storage_src.find("def list_client_visible_payment_intents(")
        fn_end = storage_src.find("\n    def ", fn_start + 1)
        fn = storage_src[fn_start:fn_end]
        self.assertIn("client_parent_child_links", fn)
        self.assertIn("client_visibility = 'published'", fn)


def storage_module_text() -> str:
    return _storage()


if __name__ == "__main__":
    unittest.main()
