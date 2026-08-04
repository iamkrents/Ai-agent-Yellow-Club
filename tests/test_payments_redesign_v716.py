"""Regression tests for v7.1.6 — Payments Workspace redesign from the approved
figma-yellow-club local prototype (Overview / Attention / All Payments / Pilot
Clients visual redesign, search+filters, delete confirmation modal, keyboard
UX fix). Business logic, capabilities, and API endpoints are unchanged — this
release is a UI layer on top of them.

Tests:
 T01  Cache-bust and version marker are v7.1.8
 T02  Four workspace tabs are defined (ids + labels)
 T03  Client manager can see the Pilot Clients tab (WORKSPACE_VIEW_ROLES)
 T04  Owner/admin can see the Pilot Clients tab (WORKSPACE_VIEW_ROLES)
 T05  Ineligible roles do not see Payments Workspace (teacher/parent excluded)
 T06  Client manager does not get canAdminPilot
 T07  Client manager keeps canManagePilotClients
 T08  Overview renders the real stats API fields
 T09  Attention tab uses the existing workspace/attention endpoint
 T10  Approve action is gated by canApprovePilotIntents
 T11  All Payments uses the existing payments/intents endpoint
 T12  Search matches by student name
 T13  Search matches by MK User ID
 T14  Search matches by public_id
 T15  Status filter is applied
 T16  Period filter is applied
 T17  Payment method filter is applied
 T18  Reset filters clears all four filters
 T19  Mobile filter bottom sheet opens and closes via the managed modal system
 T20  Pilot add-client form is vertical (one field per row)
 T21  User-facing label reads "ID клиента в МойКласс"
 T22  All four pilot mode descriptions are present
 T23  Pilot client card renders formatted success/error dates
 T24  Pilot removal requires confirmation (no more browser confirm())
 T25  Cancelling the delete modal does not call the API
 T26  Confirming delete uses the existing pilot remove endpoint
 T27  Delete text states existing payments/invoices are not deleted
 T28  Workspace content has a system bottom-safe-area class (not inline styles)
 T29  keyboard-open still hides the bottom navigation (regression)
 T30  visualViewport.height and .offsetTop are both used
 T31  First focus schedules a bounded scrollIntoView retry
 T32  Field-to-field focus change does not flash the nav back
 T33  Keyboard handling never clears form field values
 T34  Viewport growing back (swipe-dismiss) restores keyboard-closed layout
 T35  Empty states render via the shared _wsEmptyState helper
 T36  Loading states render via skeleton placeholders
 T37  Error states render via _wsErrorState with a retry action
 T38  Raw technical error text is gated behind canAdminPilot
 T39  Pilot fail-closed gate is unchanged
 T40  Review-approval semantics are unchanged (draft-only, single approval)
 T41  Automation pipeline still never auto-enrolls pilot clients
 T42  MK subscription terms auto-sync still defaults to False
 T43  Payments Workspace code still does not reference the Food module
 T44  Test-role mechanics are unchanged (panel outside workspace, capability-gated)

Run:
    python -m unittest tests.test_payments_redesign_v716 -v
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
CONFIG     = ROOT / "config.py"

_js_cache: str | None = None
_html_cache: str | None = None
_server_cache: str | None = None
_css_cache: str | None = None
_cfg_cache: str | None = None


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


def _cfg() -> str:
    global _cfg_cache
    if _cfg_cache is None:
        _cfg_cache = CONFIG.read_text(encoding="utf-8")
    return _cfg_cache


def _js_fn(name: str, *, is_async: bool = False) -> str:
    """Extract a top-level JS function body by name, up to the next top-level
    function/async-function declaration (or end of a generous fallback window)."""
    js = _js()
    needle = f"{'async ' if is_async else ''}function {name}("
    start = js.find(needle)
    assert start != -1, f"{needle} not found in app.js"
    end = js.find("\nasync function ", start + 1)
    end2 = js.find("\nfunction ", start + 1)
    candidates = [e for e in (end, end2) if e != -1]
    end = min(candidates) if candidates else start + 3000
    return js[start:end]


def _workspace_view_roles() -> str:
    m = re.search(r'WORKSPACE_VIEW_ROLES\s*=\s*\{([^}]+)\}', _server())
    assert m, "WORKSPACE_VIEW_ROLES not found in web_app_server.py"
    return m.group(1)


def _pilot_admin_roles() -> str:
    m = re.search(r'PILOT_ADMIN_ROLES\s*=\s*\{([^}]+)\}', _server())
    assert m, "PILOT_ADMIN_ROLES not found in web_app_server.py"
    return m.group(1)


def _pilot_manage_roles() -> str:
    m = re.search(r'PILOT_MANAGE_ROLES\s*=\s*\{([^}]+)\}', _server())
    assert m, "PILOT_MANAGE_ROLES not found in web_app_server.py"
    return m.group(1)


def _keyboard_iife() -> str:
    js = _js()
    start = js.find("initKeyboardHandling")
    assert start != -1, "initKeyboardHandling not found in app.js"
    return js[start : start + 6000]


# ---------------------------------------------------------------------------
# T01: Version
# ---------------------------------------------------------------------------

class TestVersion(unittest.TestCase):

    def test_01_cache_bust_and_version_are_v716(self):
        html = _html()
        js = _js()
        self.assertIn("styles.css?v=7.1.16", html)
        self.assertIn("app.js?v=7.1.16", html)
        self.assertIn('console.log("MiniApp version: v7.1.16")', js)


# ---------------------------------------------------------------------------
# T02-T07: Tabs and role capabilities
# ---------------------------------------------------------------------------

class TestTabsAndCapabilities(unittest.TestCase):

    def test_02_four_workspace_tabs_defined(self):
        fn = _js_fn("_renderWorkspaceSkeleton")
        for tab_id, label in (
            ("overview", "Обзор"), ("attention", "Требуют внимания"),
            ("all-payments", "Все платежи"), ("pilot-clients", "Клиенты пилота"),
        ):
            self.assertIn(tab_id, fn, f"workspace tabs must include id={tab_id}")
            self.assertIn(label, fn, f"workspace tabs must include label={label}")

    def test_03_client_manager_sees_pilot_clients_tab(self):
        self.assertIn('"client_manager"', _workspace_view_roles())
        fn = _js_fn("_renderWorkspaceSkeleton")
        self.assertIn("canUsePaymentsWorkspace", fn, "pilot-clients tab must be gated on canUsePaymentsWorkspace")

    def test_04_owner_admin_see_pilot_clients_tab(self):
        roles = _workspace_view_roles()
        self.assertIn('"owner"', roles)
        self.assertIn('"admin"', roles)

    def test_05_ineligible_roles_excluded(self):
        roles = _workspace_view_roles()
        self.assertNotIn('"teacher"', roles)
        self.assertNotIn('"parent"', roles)

    def test_06_client_manager_no_can_admin_pilot(self):
        self.assertNotIn('"client_manager"', _pilot_admin_roles())

    def test_07_client_manager_keeps_can_manage_pilot_clients(self):
        self.assertIn('"client_manager"', _pilot_manage_roles())


# ---------------------------------------------------------------------------
# T08-T11: Real API usage (no new backend / no duplicated business logic)
# ---------------------------------------------------------------------------

class TestRealApiUsage(unittest.TestCase):

    def test_08_overview_uses_real_stats_fields(self):
        """v7.1.6.1: field access is data-driven via WS_OVERVIEW_STAT_META (s[m.field])
        instead of six hardcoded s.<field> accesses — same real API fields, no new backend."""
        js = _js()
        meta_start = js.find("const WS_OVERVIEW_STAT_META")
        self.assertNotEqual(meta_start, -1, "WS_OVERVIEW_STAT_META not found in app.js")
        meta_block = js[meta_start : meta_start + 900]
        for field in ("pending_review", "requires_check", "awaiting_payment", "paid", "posted_to_moyklass", "pilot_clients_count"):
            self.assertIn(f'"{field}"', meta_block, f"WS_OVERVIEW_STAT_META must reference stats.{field}")
        fn = _js_fn("_wsRenderOverview")
        self.assertIn("WS_OVERVIEW_STAT_META", fn)
        self.assertIn("s[m.field]", fn)

    def test_09_attention_uses_existing_endpoint(self):
        fn = _js_fn("_loadWorkspaceAttention", is_async=True)
        self.assertIn("/api/payments/workspace/attention", fn)

    def test_10_approve_gated_by_can_approve_pilot_intents(self):
        fn = _js_fn("_wsRenderAttentionItem")
        self.assertIn("canApprovePilotIntents", fn)

    def test_11_all_payments_uses_existing_intents_endpoint(self):
        fn = _js_fn("_loadWorkspaceAllPayments", is_async=True)
        self.assertIn("/api/payments/intents", fn)
        # v7.1.6.1 step 3: All Payments now renders via its own dedicated
        # _wsRenderPaymentCard() (compact card + collapsible details) rather
        # than the shared renderPaymentIntentList() — the endpoint above is
        # unchanged, only the rendering delegate changed. v7.1.6.2 moved the
        # call site into _wsRenderAllPaymentsResults() (results-only updater).
        self.assertIn("_wsRenderPaymentCard", _js_fn("_wsRenderAllPaymentsResults"))


# ---------------------------------------------------------------------------
# T12-T18: Frontend search / filters
# ---------------------------------------------------------------------------

class TestSearchAndFilters(unittest.TestCase):

    def _filtered_fn(self) -> str:
        return _js_fn("_wsFilteredAllPayments")

    def test_12_search_matches_student_name(self):
        self.assertIn("pi.student_name", self._filtered_fn())

    def test_13_search_matches_mk_user_id(self):
        self.assertIn("pi.mk_user_id", self._filtered_fn())

    def test_14_search_matches_public_id(self):
        self.assertIn("pi.public_id", self._filtered_fn())

    def test_15_status_filter_applied(self):
        fn = self._filtered_fn()
        self.assertIn("_wsAllPaymentsUI.status", fn)
        self.assertIn("pi.status", fn)

    def test_16_period_filter_applied(self):
        fn = self._filtered_fn()
        self.assertIn("_wsAllPaymentsUI.period", fn)
        self.assertIn("period_month", fn)

    def test_17_method_filter_applied(self):
        fn = self._filtered_fn()
        self.assertIn("_wsAllPaymentsUI.method", fn)
        self.assertIn("payment_method", fn)

    def test_18_reset_filters_clears_all_four(self):
        fn = _js_fn("resetWsFilters")
        for key in ("status", "period", "method", "visibility"):
            self.assertIn(f'_wsAllPaymentsUI.{key} = "all"', fn)

    def test_19_filter_sheet_uses_managed_modal(self):
        html = _html()
        idx = html.find('id="wsFiltersModal"')
        self.assertNotEqual(idx, -1, "#wsFiltersModal not found in index.html")
        self.assertIn('class="pi-modal hidden"', html[max(0, idx - 10) : idx + 90])
        open_fn = _js_fn("openWsFiltersModal")
        self.assertIn('piModalOpen($("wsFiltersModal"))', open_fn)
        close_fn = _js_fn("closeWsFiltersModal")
        self.assertIn("piModalClose", close_fn)


# ---------------------------------------------------------------------------
# T20-T23: Pilot form / card
# ---------------------------------------------------------------------------

class TestPilotFormAndCard(unittest.TestCase):

    def test_20_pilot_form_is_vertical(self):
        idx = _css().find(".ws-pilot-form {")
        self.assertNotEqual(idx, -1, ".ws-pilot-form CSS rule not found")
        block = _css()[idx : _css().find("}", idx)]
        self.assertIn("flex-direction: column", block)

    def test_21_user_facing_label_id_klienta_v_moyklass(self):
        # v7.1.6.1 step 4: the add-form markup moved into its own
        # _wsPilotFormCard() function (still rendered by _wsRenderPilotClients).
        fn = _js_fn("_wsPilotFormCard")
        self.assertIn("ID клиента в МойКласс", fn)

    def test_22_all_four_mode_descriptions_present(self):
        js = _js()
        idx = js.find("WS_PILOT_MODE_HINTS")
        self.assertNotEqual(idx, -1)
        block = js[idx : idx + 600]
        self.assertIn("действие подтверждает менеджер", block)
        self.assertIn("агент только анализирует", block)
        self.assertIn("разрешён полный автоматический сценарий", block)
        self.assertIn("новые действия не выполняются", block)

    def test_23_pilot_card_shows_formatted_dates(self):
        # v7.1.6.1 step 4: per-client card markup moved into _wsPilotClientCard().
        fn = _js_fn("_wsPilotClientCard")
        self.assertIn("wsFormatDateTime(c.last_success_at)", fn)
        self.assertIn("wsFormatDateTime(c.last_error_at)", fn)


# ---------------------------------------------------------------------------
# T24-T27: Delete confirmation
# ---------------------------------------------------------------------------

class TestDeleteConfirmation(unittest.TestCase):

    def test_24_delete_requires_confirmation_modal(self):
        remove_fn = _js_fn("_pilotRemove")
        self.assertNotIn("confirm(", remove_fn)
        self.assertIn('piModalOpen($("pilotRemoveModal"))', remove_fn)
        self.assertIn('id="pilotRemoveModal"', _html())

    def test_25_cancel_delete_does_not_call_api(self):
        close_fn = _js_fn("closePilotRemoveModal")
        self.assertNotIn("_apiPostRaw", close_fn)
        self.assertNotIn("fetch(", close_fn)

    def test_26_confirm_delete_uses_existing_endpoint(self):
        confirm_fn = _js_fn("confirmPilotRemove", is_async=True)
        self.assertIn("/api/pilot/clients/", confirm_fn)
        self.assertIn("/remove", confirm_fn)

    def test_27_delete_text_states_data_preserved(self):
        html = _html()
        idx = html.find('id="pilotRemoveModal"')
        chunk = html[idx : idx + 1200]
        self.assertIn("не удаляются", chunk)
        self.assertTrue("счет" in chunk.lower() or "счёт" in chunk.lower())


# ---------------------------------------------------------------------------
# T28-T29: Layout / navigation overlap
# ---------------------------------------------------------------------------

class TestLayoutOverlap(unittest.TestCase):

    def test_28_system_bottom_safe_pad_class_exists(self):
        css = _css()
        self.assertIn(".ws-bottom-safe-pad", css)
        idx = css.find(".ws-bottom-safe-pad {")
        block = css[idx : css.find("}", idx)]
        self.assertIn("padding-bottom", block)
        self.assertIn("env(safe-area-inset-bottom)", block)
        # used by more than one render function, not a one-off inline style
        js = _js()
        self.assertGreaterEqual(js.count("ws-bottom-safe-pad"), 2)

    def test_29_keyboard_open_hides_bottom_tabbar(self):
        self.assertIn("body.keyboard-open .tabs.bottom-tabbar", _css())
        self.assertIn("keyboard-open", _keyboard_iife())


# ---------------------------------------------------------------------------
# T30-T34: Keyboard UX
# ---------------------------------------------------------------------------

class TestKeyboardUx(unittest.TestCase):

    def test_30_visual_viewport_height_and_offset_top_used(self):
        iife = _keyboard_iife()
        self.assertIn("visualViewport.height", iife)
        self.assertIn("visualViewport.offsetTop", iife)

    def test_31_first_focus_schedules_bounded_scroll_retry(self):
        iife = _keyboard_iife()
        self.assertIn("_scheduleScrollIntoView(e.target)", iife)
        self.assertIn("MAX_SCROLL_ATTEMPTS", iife)

    def test_32_field_to_field_does_not_flash_nav(self):
        iife = _keyboard_iife()
        focusout_start = iife.find('addEventListener("focusout"')
        self.assertNotEqual(focusout_start, -1)
        focusout_body = iife[focusout_start : focusout_start + 500]
        self.assertIn("_isFormField(active)", focusout_body)

    def test_33_keyboard_handling_never_clears_field_values(self):
        iife = _keyboard_iife()
        self.assertNotIn(".value = \"\"", iife)
        self.assertNotIn(".value=''", iife)

    def test_34_viewport_grow_restores_layout(self):
        iife = _keyboard_iife()
        resize_start = iife.find('addEventListener("resize"')
        self.assertNotEqual(resize_start, -1)
        resize_body = iife[resize_start : resize_start + 700]
        self.assertIn("gap < 50", resize_body)
        self.assertIn("_setKeyboardOpen(false)", resize_body)


# ---------------------------------------------------------------------------
# T35-T38: Empty / loading / error states
# ---------------------------------------------------------------------------

class TestEmptyLoadingErrorStates(unittest.TestCase):

    def test_35_empty_state_helper_used(self):
        js = _js()
        self.assertIn("function _wsEmptyState(", js)
        # v7.1.6.1 step 2: Attention's own empty state was intentionally
        # migrated off the shared _wsEmptyState() helper to the same inline
        # SVG green-check pattern Overview already uses — still a real empty
        # state (icon + title + description), just not via that helper call.
        attention = _js_fn("_wsRenderAttention")
        self.assertNotIn("_wsEmptyState(", attention)
        self.assertIn("ws-empty-state-icon--success", attention)
        # v7.1.6.1 step 3: All Payments' two empty states (no payments / no
        # search results) were built the same SVG-icon way from the start —
        # never used _wsEmptyState() — using the new neutral icon tone.
        all_payments = _js_fn("_wsRenderAllPayments")
        self.assertNotIn("_wsEmptyState(", all_payments)
        self.assertIn("ws-empty-state-icon--neutral", all_payments)
        # v7.1.6.1 step 4: Pilot Clients' empty state was migrated the same
        # way — SVG users-icon instead of the old "👥" emoji + _wsEmptyState().
        pilot = _js_fn("_wsRenderPilotClients")
        self.assertNotIn("_wsEmptyState(", pilot)
        self.assertIn("ws-empty-state-icon--neutral", pilot)
        self.assertIn("WS_ICON_USERS", pilot)

    def test_36_loading_state_uses_skeletons(self):
        js = _js()
        self.assertIn(".ws-skeleton", _css())
        self.assertIn("ws-skeleton", _js_fn("_wsStatsSkeleton"))
        # v7.1.6.1 step 2: Attention's loading state was intentionally
        # upgraded from a generic row skeleton to a card-shaped skeleton.
        self.assertIn("_wsAttentionSkeletonCard", _js_fn("_wsRenderAttention"))
        self.assertIn("ws-attention-skeleton-card", _js_fn("_wsAttentionSkeletonCard"))

    def test_37_error_state_has_retry(self):
        js = _js()
        start = js.find("function _wsErrorState(")
        self.assertNotEqual(start, -1)
        body = js[start : start + 600]
        self.assertIn("Повторить", body)
        self.assertIn("retryOnclick", body)

    def test_38_raw_technical_error_gated_by_can_admin_pilot(self):
        js = _js()
        self.assertIn("function _wsFriendlyAttentionError(", js)
        self.assertIn("function _wsPilotErrorDetail(", js)
        attn_start = js.find("function _wsFriendlyAttentionError(")
        attn_body = js[attn_start : attn_start + 700]
        self.assertIn("canAdminPilot", attn_body)
        pilot_start = js.find("function _wsPilotErrorDetail(")
        pilot_body = js[pilot_start : pilot_start + 700]
        self.assertIn("canAdminPilot", pilot_body)
        # and NOT inside the pilot list renderer itself (its own gate stays canManagePilotClients)
        self.assertNotIn("canAdminPilot", _js_fn("_wsRenderPilotClients"))


# ---------------------------------------------------------------------------
# T39-T44: Invariants — business logic must be unchanged
# ---------------------------------------------------------------------------

class TestInvariantsUnchanged(unittest.TestCase):

    def test_39_pilot_fail_closed_unchanged(self):
        self.assertIn('"disabled", "not_in_pilot"', _server())

    def test_40_review_approval_semantics_unchanged(self):
        src = _server()
        start = src.find("def approve_payment_intent(")
        self.assertNotEqual(start, -1)
        end = src.find("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn('intent.get("status") not in ("draft",)', body)
        self.assertIn('(intent.get("client_visibility") or "hidden") == "published"', body)

    def test_41_no_pilot_auto_enrollment(self):
        src = _server()
        start = src.find("def _process_single_automation_item_from_invoice(")
        self.assertNotEqual(start, -1)
        end = src.find("\n    def ", start + 1)
        if end == -1:
            end = start + 8000
        self.assertNotIn("upsert_pilot_client", src[start:end])

    def test_42_mk_terms_sync_default_false(self):
        m = re.search(r'payment_mk_subscription_terms_sync_enabled\s*:\s*bool\s*=\s*(\w+)', _cfg())
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "False")

    def test_43_workspace_code_does_not_reference_food(self):
        js = _js()
        ws_start = js.find("const _wsState")
        ws_end = js.find("initKeyboardHandling")
        chunk = js[ws_start:ws_end]
        for bad in ("food_module", "food_menu", "loadKitchenEditor", "renderParentFoodMenu"):
            self.assertNotIn(bad, chunk)

    def test_44_test_role_mechanics_unchanged(self):
        html = _html()
        ws_start = html.find('id="tab-payments-workspace"')
        ws_end = html.find("</section>", ws_start)
        self.assertNotIn('id="testRolePanel"', html[ws_start:ws_end])
        js = _js()
        fn_start = js.find("function renderTestRolePanel()")
        self.assertNotEqual(fn_start, -1)
        self.assertIn("canUseTestRoles", js[fn_start : fn_start + 500])


if __name__ == "__main__":
    unittest.main(verbosity=2)
