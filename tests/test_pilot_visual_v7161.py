"""Regression tests for v7.1.6.1 step 4 — Pilot Clients visual pass (Client
Manager / owner / admin Payments Workspace → «Клиенты пилота»), adapted from
the approved figma-yellow-club prototype ("Payments — Pilot Clients"). Visual
+ frontend-behaviour change only: pilot API, canManagePilotClients,
canAdminPilot, the four modes, fail-closed semantics and Overview/Attention/
All Payments are untouched — these tests exist to prove exactly that boundary
held, while confirming the new form, card, mode-change and delete UX match
the approved spec.

Tests:
 T01  .ws-header / tabs markup reused verbatim (no new header/tab code)
 T02  Active Pilot Clients tab still uses the shared yellow .ws-subtab.active rule
 T03  Vertical add-client form (one field per row, no side-by-side fields)
 T04  User-facing label "ID клиента в МойКласс" (no "MK User ID")
 T05  All four mode explanations present and hint updates on mode change
 T06  Add button is full-width, ≥44px
 T07  Client card structure: header, hint, grid, mode-select, delete button
 T08  Mode badge CSS classes are data-driven (WS_PILOT_MODE_CLS)
 T09  User-facing mode mapping — no raw review/observe/auto/disabled leak to user
 T10  Success/error dates formatted via wsFormatDate/wsFormatDateTime, empty → "—"
 T11  Long note wraps (word-break) instead of overflowing
 T12  Mode-change select has its own label, loading-disable and revert-on-error
 T13  canManagePilotClients gate preserved (not canAdminPilot)
 T14  Deletion still uses the managed #pilotRemoveModal bottom sheet
 T15  Cancelling delete never calls the API
 T16  Confirming delete uses the existing /remove endpoint
 T17  Delete confirmation text states payments/invoices are not deleted
 T18  Empty state uses SVG users icon, not emoji
 T19  Loading state shows card skeletons (not a premature empty state)
 T20  Error state reuses _wsErrorState with retry, no raw traceback
 T21  Safe bottom padding preserved on the populated list
 T22  Form fields are plain INPUT/SELECT — covered by existing global keyboard-safe logic
 T25  Overview untouched
 T26  Attention untouched
 T27  All Payments untouched
 T28  WORKSPACE_VIEW_ROLES / PILOT_ADMIN_ROLES / PILOT_MANAGE_ROLES unchanged
 T29  Pilot fail-closed gate unchanged
 T30  No pilot auto-enrollment added
 (T23/T24 — local dev-preview wiring — removed once dev preview was deleted)

Run:
    python -m unittest tests.test_pilot_visual_v7161 -v
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
# T01-T02: Header / tabs
# ---------------------------------------------------------------------------

class TestHeaderReuse(unittest.TestCase):

    def test_01_header_and_tabs_markup_reused_verbatim(self):
        fn = _js_fn("_renderWorkspaceSkeleton")
        self.assertIn("ws-header", fn)
        self.assertIn("pilot-clients", fn)

    def test_02_active_tab_still_shared_yellow_rule(self):
        block = _css_block(".ws-subtab.active")
        self.assertIn("--yellow", block)


# ---------------------------------------------------------------------------
# T03-T06: Add-client form
# ---------------------------------------------------------------------------

class TestForm(unittest.TestCase):

    def test_03_form_is_vertical(self):
        block = _css_block(".ws-pilot-form {")
        self.assertIn("flex-direction: column", block)
        form_fn = _js_fn("_wsPilotFormCard")
        # one label per row (ID / mode / note) — no two inputs sharing a row
        self.assertEqual(form_fn.count("<label>"), 3)
        self.assertIn('<input id="pilotAddUserId"', form_fn)
        self.assertIn('<select id="pilotAddMode"', form_fn)
        self.assertIn('<input id="pilotAddNote"', form_fn)

    def test_04_label_id_klienta_v_moyklass_no_mk_user_id(self):
        form_fn = _js_fn("_wsPilotFormCard")
        self.assertIn("ID клиента в МойКласс", form_fn)
        self.assertNotIn("MK User ID", form_fn)
        self.assertNotIn("mk_user_id", form_fn.replace('id="pilotAddUserId"', ""))

    def test_05_mode_hints_present_and_update_on_change(self):
        js = _js()
        idx = js.find("const WS_PILOT_MODE_HINTS")
        block = js[idx : idx + 400]
        self.assertIn("действие подтверждает менеджер", block)
        self.assertIn("агент только анализирует", block)
        self.assertIn("разрешён полный автоматический сценарий", block)
        self.assertIn("новые действия не выполняются", block)
        form_fn = _js_fn("_wsPilotFormCard")
        self.assertIn('onchange="_wsUpdatePilotModeHint()"', form_fn)
        update_fn = _js_fn("_wsUpdatePilotModeHint")
        self.assertIn("WS_PILOT_MODE_HINTS[sel.value]", update_fn)

    def test_06_add_button_full_width_44px(self):
        block = _css_block(".ws-pilot-form > button {")
        self.assertIn("44px", block)
        form_fn = _js_fn("_wsPilotFormCard")
        self.assertIn('<button class="primary" onclick="_pilotAddClient()">Добавить в пилот</button>', form_fn)


# ---------------------------------------------------------------------------
# T07-T11: Client card
# ---------------------------------------------------------------------------

class TestCard(unittest.TestCase):

    def test_07_card_structure(self):
        fn = _js_fn("_wsPilotClientCard")
        self.assertIn("ws-pilot-card-header", fn)
        self.assertIn("ws-pilot-card-hint", fn)
        self.assertIn("ws-pilot-card-grid", fn)
        self.assertIn("ws-pilot-mode-change", fn)
        self.assertIn("ws-pilot-remove-btn", fn)

    def test_08_mode_badge_classes_data_driven(self):
        fn = _js_fn("_wsPilotClientCard")
        self.assertIn("WS_PILOT_MODE_CLS[c.mode]", fn)
        js = _js()
        idx = js.find("const WS_PILOT_MODE_CLS")
        block = js[idx : idx + 300]
        for cls in ("ws-pilot-mode-observe", "ws-pilot-mode-review", "ws-pilot-mode-auto", "ws-pilot-mode-disabled"):
            self.assertIn(cls, block)

    def test_09_user_facing_mode_mapping_no_raw_values(self):
        js = _js()
        idx = js.find("const WS_PILOT_MODE_LABEL")
        block = js[idx : idx + 250]
        for label in ("Наблюдение", "Проверка", "Авто", "Отключён"):
            self.assertIn(label, block)
        fn = _js_fn("_wsPilotClientCard")
        self.assertIn("WS_PILOT_MODE_LABEL[c.mode]", fn)

    def test_10_dates_formatted_empty_as_dash(self):
        fn = _js_fn("_wsPilotClientCard")
        self.assertIn("wsFormatDateTime(c.last_success_at)", fn)
        self.assertIn("wsFormatDateTime(c.last_error_at)", fn)
        self.assertIn("wsFormatDate(c.created_at)", fn)
        self.assertIn("wsFormatDate(c.updated_at)", fn)
        self.assertIn('<span>—</span>', fn)

    def test_11_long_note_wraps(self):
        fn = _js_fn("_wsPilotClientCard")
        self.assertIn("c.note", fn)
        row_block = _css_block(".ws-pilot-row > span:last-child {")
        self.assertIn("break-word", row_block)


# ---------------------------------------------------------------------------
# T12-T13: Mode change
# ---------------------------------------------------------------------------

class TestModeChange(unittest.TestCase):

    def test_12_mode_select_own_label_loading_and_revert(self):
        card_fn = _js_fn("_wsPilotClientCard")
        self.assertIn("Режим автоматизации", card_fn)
        self.assertIn('id="pilotMode-${midAttr}"', card_fn)
        change_fn = _js_fn("_pilotChangeMode", is_async=True)
        self.assertIn("selectEl.disabled = true", change_fn)
        self.assertIn("selectEl.value = prevMode", change_fn)  # revert on failure
        self.assertIn("setNotice(", change_fn)
        select_block = _css_block(".ws-pilot-mode-change select {")
        self.assertIn("44px", select_block)

    def test_13_can_manage_pilot_clients_gate_preserved(self):
        fn = _js_fn("_wsRenderPilotClients")
        self.assertIn("canManagePilotClients", fn)
        self.assertNotIn("canAdminPilot", fn)


# ---------------------------------------------------------------------------
# T14-T17: Deletion
# ---------------------------------------------------------------------------

class TestDeletion(unittest.TestCase):

    def test_14_uses_managed_modal(self):
        remove_fn = _js_fn("_pilotRemove")
        self.assertIn('piModalOpen($("pilotRemoveModal"))', remove_fn)
        self.assertNotIn("confirm(", remove_fn)

    def test_15_cancel_does_not_call_api(self):
        close_fn = _js_fn("closePilotRemoveModal")
        self.assertIn("piModalClose", close_fn)
        self.assertNotIn("_apiPostRaw", close_fn)

    def test_16_confirm_uses_existing_endpoint(self):
        confirm_fn = _js_fn("confirmPilotRemove", is_async=True)
        self.assertIn("/api/pilot/clients/${encodeURIComponent(_pilotRemoveTarget)}/remove", confirm_fn)

    def test_17_delete_text_states_data_preserved(self):
        html = _html()
        idx = html.find('id="pilotRemoveModal"')
        block = html[idx : idx + 1200]
        self.assertIn("исключён только из дальнейшей автоматизации", block)
        self.assertIn("Существующие платёжные", block)
        self.assertIn("не удаляются", block)
        self.assertIn("Отмена", block)
        self.assertIn("Удалить из пилота", block)


# ---------------------------------------------------------------------------
# T18-T21: Empty / loading / error / safe-area
# ---------------------------------------------------------------------------

class TestStates(unittest.TestCase):

    def test_18_empty_state_svg_not_emoji(self):
        fn = _js_fn("_wsRenderPilotClients")
        self.assertIn("WS_ICON_USERS", fn)
        self.assertIn("ws-empty-state-icon--neutral", fn)
        self.assertNotIn("👥", fn)
        self.assertIn("Пока ни один клиент не добавлен в пилот", fn)
        self.assertIn("Добавьте клиента с помощью формы выше", fn)

    def test_19_loading_state_card_skeletons(self):
        fn = _js_fn("_wsRenderPilotClients")
        self.assertIn("clients === null", fn)
        self.assertIn("_wsPilotCardSkeleton", fn)
        skeleton_fn = _js_fn("_wsPilotCardSkeleton")
        self.assertIn("ws-attention-skeleton-card", skeleton_fn)
        # initial state must be null (not []) so the skeleton actually shows on first load
        state_decl = re.search(r'const _wsState\s*=\s*\{([^}]+)\}', _js())
        self.assertIn("pilotClients: null", state_decl.group(1))

    def test_20_error_state_reuses_shared_helper(self):
        load_fn = _js_fn("_loadPilotClients", is_async=True)
        self.assertIn("_wsErrorState(", load_fn)
        error_state = _js_fn("_wsErrorState")
        self.assertIn("Повторить", error_state)
        self.assertNotIn("Traceback", error_state)

    def test_21_safe_bottom_padding_preserved(self):
        fn = _js_fn("_wsRenderPilotClients")
        self.assertIn("ws-bottom-safe-pad", fn)


# ---------------------------------------------------------------------------
# T22: Keyboard UX
# ---------------------------------------------------------------------------

class TestKeyboardSafe(unittest.TestCase):

    def test_22_form_fields_are_plain_native_elements(self):
        # No changes needed to the global keyboard state machine — it already
        # matches on tag name (_isFormField), so plain INPUT/SELECT elements
        # in the pilot form are automatically covered.
        form_fn = _js_fn("_wsPilotFormCard")
        self.assertIn("<input", form_fn)
        self.assertIn("<select", form_fn)
        iife_start = _js().find("function initKeyboardHandling")
        iife = _js()[iife_start : iife_start + 2000]
        self.assertIn('["INPUT", "TEXTAREA", "SELECT"]', iife)


# ---------------------------------------------------------------------------
# T25-T28: Scope boundaries
# ---------------------------------------------------------------------------
# NOTE: T23/T24 (local preview wiring / in-memory mutation simulation) were
# removed in v7.1.6.1's final cleanup pass along with the entire temporary
# local preview scaffold they tested, including its preview-only mock
# network layer and the in-memory guards inside _pilotAddClient/
# _pilotChangeMode/confirmPilotRemove.

class TestScopeBoundaries(unittest.TestCase):

    def test_25_overview_untouched(self):
        fn = _js_fn("_wsRenderOverview")
        self.assertIn("WS_OVERVIEW_STAT_META", fn)
        self.assertIn("ws-recent-list", fn)

    def test_26_attention_untouched(self):
        fn = _js_fn("_wsRenderAttention")
        self.assertIn("_wsQueueHead", fn)
        self.assertIn("_wsAttentionSkeletonCard", fn)

    def test_27_all_payments_untouched(self):
        fn = _js_fn("_wsRenderAllPayments")
        self.assertIn("_wsRenderPaymentCard", fn)
        self.assertIn("WS_ICON_SEARCH", fn)

    def test_28_role_capability_tables_unchanged(self):
        roles = _roles("WORKSPACE_VIEW_ROLES")
        for role in ("owner", "admin", "operations", "client_manager"):
            self.assertIn(f'"{role}"', roles)
        self.assertNotIn('"client_manager"', _roles("PILOT_ADMIN_ROLES"))
        self.assertIn('"client_manager"', _roles("PILOT_MANAGE_ROLES"))


# ---------------------------------------------------------------------------
# T29-T30: Fail-closed / auto-enrollment invariants
# ---------------------------------------------------------------------------

class TestInvariantsUnchanged(unittest.TestCase):

    def test_29_pilot_fail_closed_unchanged(self):
        server = _server()
        self.assertIn("PILOT_MANAGE_ROLES", server)
        self.assertIn("PILOT_ADMIN_ROLES", server)

    def test_30_no_pilot_auto_enrollment(self):
        server = _server()
        # upsert_pilot_client must remain an explicit, manager-initiated call —
        # no new automatic-enrollment call site was added anywhere in the file.
        self.assertEqual(server.count("upsert_pilot_client("), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
