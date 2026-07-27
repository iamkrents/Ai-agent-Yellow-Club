"""Regression tests for v7.1.6.1 step 3 — All Payments visual pass (Client
Manager / owner / admin Payments Workspace → «Все платежи»), adapted from the
approved figma-yellow-club prototype ("Payments — All Payments"). Visual-only
change: backend, API, capabilities, Overview's own structure/CSS, Attention
and Pilot Clients are untouched — these tests exist to prove exactly that
boundary held, while confirming the new compact card, collapsible details,
light filter sheet and unified status mapping match the approved spec.

Tests:
 T01  .ws-header / tabs markup reused verbatim (no new header/tab code)
 T02  Active All Payments tab still uses the shared yellow .ws-subtab.active rule
 T03  Search bar uses SVG icon (not emoji) and real fields (name/MK id/public_id/mk_invoice_id)
 T04  Results count text "Найдено: N"
 T05  Compact card structure: name+amount row, sub row, meta row, bottom row
 T06  Name + amount use real fields (student_name / paymentIntentAmountByn)
 T07  Public ID + MK ID both shown in the sub row
 T08  Period, method and date all present in the meta row
 T09  User-facing status mapping (WS_PI_STATUS_LABELS) — no internal codes
 T10  Overview's raw "requires_check" text is fixed (label map only, structure untouched)
 T11  Parent visibility shown via _wsPiVisibilityBadge (eye / eye-off SVG)
 T12  Collapsible "Подробнее" — closed by default, holds technical blocks
 T13  Existing action capability checks copied verbatim into the new card
 T14  Filter bottom sheet forced light regardless of device theme (scoped to #wsFiltersModal)
 T15  Reset / Apply buttons — Apply shows live "Показать: N", Reset doesn't close the sheet
 T16  Empty state — no payments at all
 T17  Empty state — search/filter found nothing (+ reset button)
 T18  Loading state — search skeleton + card skeletons
 T19  Error state reuses _wsErrorState with retry, no raw traceback
 T20  Bottom-safe padding class preserved on the populated list
 T23  Overview changed only in the status-label fix, nothing else
 T24  Attention tab renderer untouched
 T25  Pilot Clients renderer untouched
 T26  WORKSPACE_VIEW_ROLES / PILOT_ADMIN_ROLES / PAYMENT_APPROVAL_ROLES unchanged
 T27  Version / cache-bust is v7.1.7
 T28  Search placeholder shortened; search logic fields unchanged
 T29  bePaid technical ids (order_id/tracking_id/UID) gated behind canAdminPilot + collapsible
 T30  ERIP account number stays visible to everyone under "Номер ЕРИП"
 T31  "Подробнее" info-block readability improved without growing the card much
 (T21/T22 — local dev-preview wiring — removed once dev preview was deleted)

Run:
    python -m unittest tests.test_all_payments_visual_v7161 -v
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
        self.assertIn("all-payments", fn)

    def test_02_active_tab_still_shared_yellow_rule(self):
        block = _css_block(".ws-subtab.active")
        self.assertIn("--yellow", block)


# ---------------------------------------------------------------------------
# T03-T04: Search + results count
# ---------------------------------------------------------------------------

class TestSearchAndCount(unittest.TestCase):

    def test_03_search_bar_svg_and_real_fields(self):
        fn = _js_fn("_wsRenderAllPayments")
        self.assertIn("WS_ICON_SEARCH", fn)
        self.assertNotIn("🔎", fn)
        filtered = _js_fn("_wsFilteredAllPayments")
        for field in ("pi.student_name", "pi.mk_user_id", "pi.public_id", "pi.mk_invoice_id"):
            self.assertIn(field, filtered)

    def test_04_results_count_text(self):
        # v7.1.6.2: the count now lives in _wsRenderAllPaymentsResults(), the
        # results-only updater used on every keystroke/filter change so the
        # search <input> itself is never rebuilt (see TestSearchFocus below).
        fn = _js_fn("_wsRenderAllPaymentsResults")
        self.assertIn("Найдено: ${filtered.length}", fn)


# ---------------------------------------------------------------------------
# T05-T08: Compact card structure
# ---------------------------------------------------------------------------

class TestCardStructure(unittest.TestCase):

    def test_05_card_rows_present(self):
        fn = _js_fn("_wsRenderPaymentCard")
        self.assertIn("ws-pi-name", fn)
        self.assertIn("ws-pi-amount", fn)
        self.assertIn("ws-pi-sub", fn)
        self.assertIn("ws-pi-meta", fn)
        self.assertIn("ws-pi-row--bottom", fn)

    def test_06_name_and_amount_use_real_fields(self):
        fn = _js_fn("_wsRenderPaymentCard")
        self.assertIn("paymentIntentAmountByn(pi)", fn)
        self.assertIn("pi.student_name", fn)

    def test_07_public_and_mk_id_shown(self):
        fn = _js_fn("_wsRenderPaymentCard")
        self.assertIn("pi.public_id", fn)
        self.assertIn("pi.mk_user_id", fn)
        self.assertIn("ID в МойКласс", fn)

    def test_08_period_method_date_in_meta(self):
        fn = _js_fn("_wsRenderPaymentCard")
        self.assertIn("period_month", fn)
        self.assertIn("PI_METHOD_LABELS", fn)
        self.assertIn("wsFormatDate(pi.created_at)", fn)
        self.assertIn("Период:", fn)
        self.assertIn("Способ:", fn)
        self.assertIn("Дата:", fn)


# ---------------------------------------------------------------------------
# T09-T10: Status mapping (All Payments + Overview fix)
# ---------------------------------------------------------------------------

class TestStatusMapping(unittest.TestCase):

    def test_09_unified_status_mapping_used_no_raw_codes(self):
        js = _js()
        start = js.find("const WS_PI_STATUS_LABELS")
        self.assertNotEqual(start, -1)
        block = js[start:start + 1400]
        for raw in ("draft", "ready", "bepaid_created", "requires_check", "paid", "posted_to_moyklass", "cancelled", "error"):
            self.assertIn(f'{raw}:', block)
        card = _js_fn("_wsRenderPaymentCard")
        self.assertIn("_wsPiStatusBadge(pi)", card)

    def test_10_overview_requires_check_label_fixed(self):
        # The permitted Overview fix: PI_STATUS_LABELS (the map Overview's
        # recent-ops preview already reads) now has a plain "requires_check"
        # entry, so a real intent with that status shows the friendly label
        # instead of the raw code. _wsRenderOverview's own body is untouched.
        js = _js()
        start = js.find("const PI_STATUS_LABELS")
        block = js[start:start + 700]
        self.assertIn('requires_check:', block)
        self.assertIn("Требует проверки", block)
        overview_fn = _js_fn("_wsRenderOverview")
        self.assertIn("PI_STATUS_LABELS[pi.status]", overview_fn)
        self.assertIn("WS_OVERVIEW_STAT_META", overview_fn)  # structure unchanged


# ---------------------------------------------------------------------------
# T11: Parent visibility
# ---------------------------------------------------------------------------

class TestVisibility(unittest.TestCase):

    def test_11_visibility_badge_uses_svg(self):
        fn = _js_fn("_wsPiVisibilityBadge")
        self.assertIn("WS_ICON_EYE", fn)
        self.assertIn("WS_ICON_EYE_OFF", fn)
        self.assertIn("Видно родителю", fn)
        self.assertIn("Скрыто от родителя", fn)
        card = _js_fn("_wsRenderPaymentCard")
        self.assertIn("_wsPiVisibilityBadge(pi)", card)


# ---------------------------------------------------------------------------
# T12-T13: Collapsible details + preserved actions
# ---------------------------------------------------------------------------

class TestDetailsAndActions(unittest.TestCase):

    def test_12_details_closed_by_default(self):
        fn = _js_fn("_wsRenderPaymentCard")
        self.assertIn('<details class="ws-pi-more">', fn)
        self.assertNotIn('<details class="ws-pi-more" open', fn)
        self.assertIn("Подробнее", fn)
        self.assertIn("ws-pi-more-body", fn)

    def test_13_action_capability_checks_preserved_verbatim(self):
        fn = _js_fn("_wsRenderPaymentCard")
        checks = [
            'pi.payment_method === "erip"',
            "canUsePaymentIntents()",
            "canPostToMoyklass()",
            "canWithdrawInvoice()",
            "roleCaps().canApprovePilotIntents",
            "openBePaidConfirm(",
            "openAcquiringCheckout(",
            "verifyAcquiringPayment(",
            "openMkPostModal(",
            "openPublishToParentModal(",
            "openWithdrawModal(",
            "openCancelIntent(",
            "approvePaymentIntent(",
            "retryRemoteCancel(",
        ]
        for c in checks:
            self.assertIn(c, fn, f"missing preserved check/action: {c}")


# ---------------------------------------------------------------------------
# T14-T15: Light filter sheet
# ---------------------------------------------------------------------------

class TestFilterSheet(unittest.TestCase):

    def test_14_sheet_forced_light_scoped_to_modal(self):
        css = _css()
        self.assertIn("#wsFiltersModal .pi-modal-sheet", css)
        self.assertIn("#wsFiltersModal .pi-modal-sheet::before", css)  # drag handle
        block = _css_block("#wsFiltersModal .pi-modal-sheet {")
        self.assertIn("#fff", block)
        select_block = _css_block("#wsFiltersModal .pi-modal-body select {")
        self.assertIn("44px", select_block)

    def test_15_reset_and_apply_buttons(self):
        apply_fn = _js_fn("applyWsFilters")
        self.assertIn("closeWsFiltersModal()", apply_fn)
        reset_fn = _js_fn("resetWsFilters")
        self.assertNotIn("closeWsFiltersModal()", reset_fn)  # doesn't close before applying
        preview_fn = _js_fn("_wsUpdateFiltersPreviewCount")
        self.assertIn("Показать: ${", preview_fn)
        open_fn = _js_fn("openWsFiltersModal")
        self.assertIn("_wsUpdateFiltersPreviewCount()", open_fn)


# ---------------------------------------------------------------------------
# T16-T19: Empty / loading / error states
# ---------------------------------------------------------------------------

class TestStates(unittest.TestCase):

    def test_16_empty_state_no_payments(self):
        fn = _js_fn("_wsRenderAllPayments")
        self.assertIn("Платёжных счетов пока нет", fn)
        self.assertIn("WS_ICON_INBOX", fn)

    def test_17_empty_state_no_results_with_reset(self):
        # v7.1.6.2: moved into _wsRenderAllPaymentsResults() (results-only
        # updater) — see TestSearchFocus for why.
        fn = _js_fn("_wsRenderAllPaymentsResults")
        self.assertIn("Ничего не найдено", fn)
        self.assertIn("Измените поиск или сбросьте фильтры", fn)
        self.assertIn("_wsResetAllPaymentsSearchAndFilters()", fn)
        self.assertIn("Сбросить фильтры", fn)

    def test_18_loading_state_has_search_and_card_skeletons(self):
        fn = _js_fn("_wsRenderAllPayments")
        self.assertIn("_wsSearchSkeleton()", fn)
        self.assertIn("_wsAllPaymentsSkeletonCard", fn)

    def test_19_error_state_reuses_shared_helper(self):
        fn = _js_fn("_loadWorkspaceAllPayments", is_async=True)
        self.assertIn("_wsErrorState(", fn)
        error_state = _js_fn("_wsErrorState")
        self.assertIn("Повторить", error_state)
        self.assertNotIn("Traceback", error_state)


# ---------------------------------------------------------------------------
# T20: Safe bottom padding
# ---------------------------------------------------------------------------

class TestSafeArea(unittest.TestCase):

    def test_20_bottom_safe_padding_preserved(self):
        # v7.1.6.2: the card list (and its safe-pad class) is written by
        # _wsRenderAllPaymentsResults() now, not the toolbar-rendering
        # _wsRenderAllPayments().
        fn = _js_fn("_wsRenderAllPaymentsResults")
        self.assertIn("ws-bottom-safe-pad", fn)


# ---------------------------------------------------------------------------
# T23-T26: Scope boundaries
# ---------------------------------------------------------------------------
# NOTE: T21/T22 (local preview wiring / preview-only POST-PUT block) were
# removed in v7.1.6.1's final cleanup pass along with the entire temporary
# local preview scaffold they tested.

class TestScopeBoundaries(unittest.TestCase):

    def test_23_overview_structure_untouched(self):
        fn = _js_fn("_wsRenderOverview")
        self.assertIn("WS_OVERVIEW_STAT_META", fn)
        self.assertIn("ws-recent-list", fn)
        self.assertIn("ws-empty-state-icon--success", fn)

    def test_24_attention_tab_untouched(self):
        fn = _js_fn("_wsRenderAttention")
        self.assertIn("_wsQueueHead", fn)
        self.assertIn("_wsAttentionSkeletonCard", fn)
        item_fn = _js_fn("_wsRenderAttentionItem")
        self.assertIn("ws-attn-name", item_fn)

    def test_25_pilot_clients_untouched(self):
        fn = _js_fn("_wsRenderPilotClients")
        self.assertIn("Пока ни один клиент не добавлен в пилот", fn)

    def test_26_role_capability_tables_unchanged(self):
        roles = _roles("WORKSPACE_VIEW_ROLES")
        for role in ("owner", "admin", "operations", "client_manager"):
            self.assertIn(f'"{role}"', roles)
        self.assertNotIn('"client_manager"', _roles("PILOT_ADMIN_ROLES"))
        self.assertIn('"client_manager"', _roles("PAYMENT_APPROVAL_ROLES"))


# ---------------------------------------------------------------------------
# T27: Version
# ---------------------------------------------------------------------------

class TestVersionUnchanged(unittest.TestCase):

    def test_27_version_is_v7161(self):
        html = _html()
        js = _js()
        self.assertIn("v=7.1.7", html)
        self.assertIn('console.log("MiniApp version: v7.1.7")', js)


# ---------------------------------------------------------------------------
# T28-T31: point fixes (search placeholder, tech-id gating, readability)
# ---------------------------------------------------------------------------

class TestPointFixes(unittest.TestCase):

    def test_28_search_placeholder_shortened_logic_unchanged(self):
        fn = _js_fn("_wsRenderAllPayments")
        self.assertIn("Имя, ID или номер счёта", fn)
        self.assertNotIn("Имя, ID в МойКласс или номер счёта", fn)
        filtered = _js_fn("_wsFilteredAllPayments")
        for field in ("pi.student_name", "pi.mk_user_id", "pi.public_id", "pi.mk_invoice_id"):
            self.assertIn(field, filtered)

    def test_29_bepaid_tech_ids_gated_behind_can_admin_pilot(self):
        card = _js_fn("_wsRenderPaymentCard")
        self.assertIn("const canSeeBePaidTech = roleCaps().canAdminPilot", card)
        self.assertIn('<details class="ws-tech-details"><summary>Техническая информация</summary>', card)

        # Every <details class="ws-tech-details">...</details> span must be
        # reached only through a canSeeBePaidTech check (either the ternary
        # guarding the whole span, or — for the requires_check block — a
        # variable that is itself gated the same way).
        tech_spans = re.findall(r'<details class="ws-tech-details">.*?</details>', card, re.S)
        self.assertGreaterEqual(len(tech_spans), 2, "expected at least 2 gated tech-details spans")
        tech_content = "".join(tech_spans)
        outside = card
        for span in tech_spans:
            outside = outside.replace(span, "")

        # order_id / tracking_id / bePaid UID / paid transaction UID must
        # appear ONLY inside a tech-details span, never unconditionally
        # rendered in the always-visible part of the card.
        for raw_field, display in (
            ("bepaid_order_id", "order_id: ${escapeHtml(pi.bepaid_order_id)}"),
            ("bepaid_tracking_id", "tracking_id: ${escapeHtml(pi.bepaid_tracking_id)}"),
            ("bepaid_uid", "UID: ${escapeHtml(pi.bepaid_uid)}"),
            ("paid_transaction_uid", "UID: ${escapeHtml(pi.paid_transaction_uid)}"),
        ):
            self.assertIn(display, tech_content, f"{raw_field} display string not found inside a tech-details span")
            self.assertNotIn(display, outside, f"{raw_field} display string leaked outside the tech-details gating")

        # The variable feeding the requires_check block's tech span is itself
        # defined as canSeeBePaidTech && (...) — the gate that actually
        # decides whether that span exists in the output at all.
        self.assertIn("const bePaidRequiresCheckTech = canSeeBePaidTech &&", card)
        # The bePaidInfo/bePaidPaidBlock spans are gated inline.
        self.assertIn('${canSeeBePaidTech ? `<details class="ws-tech-details"', card)
        self.assertIn("pi.paid_transaction_uid && canSeeBePaidTech", card)

    def test_30_erip_account_number_stays_visible_to_everyone(self):
        card = _js_fn("_wsRenderPaymentCard")
        self.assertIn("Номер ЕРИП", card)
        # bepaid_account_number is rendered outside any canSeeBePaidTech gate
        idx = card.find("pi.bepaid_account_number")
        self.assertNotEqual(idx, -1)
        # the nearest preceding "canSeeBePaidTech ?" ternary (if any) must not
        # wrap this specific reference — check the immediate line context.
        line_start = card.rfind("\n", 0, idx)
        line_end = card.find("\n", idx)
        line = card[line_start:line_end]
        self.assertNotIn("canSeeBePaidTech", line)

    def test_31_info_block_readability_improved(self):
        line_block = _css_block(".ws-pi-info-line {")
        self.assertIn("12px", line_block)
        self.assertIn("1.45", line_block)
        self.assertNotIn("var(--muted)", line_block)  # bumped to a higher-contrast fixed colour
        block = _css_block(".ws-pi-info-block {")
        self.assertIn("13px", block)
        self.assertIn("1.5", block)
        # padding kept modest — card must not grow substantially taller
        self.assertIn("9px 10px", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
