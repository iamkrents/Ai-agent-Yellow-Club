"""Regression tests for v7.1.6.1 step 2 — Attention tab visual pass (Client
Manager / owner / admin Payments Workspace → «Требуют внимания»), adapted from
the approved figma-yellow-club prototype ("Payments — Attention"). Visual-only
change: backend, API, capabilities, All Payments, Pilot Clients and Overview's
own renderer/layout are untouched — these tests exist to prove exactly that
boundary held, while confirming the new card structure, colour semantics and
fallback-reason behaviour match the approved spec.

Tests:
 T01  .ws-header / tabs markup reused verbatim (no new header/tab code)
 T02  Active Attention tab still uses the shared yellow .ws-subtab.active rule
 T03  Queue header (title + subtitle + count badge) present
 T04  Card structure: name+amount row, public-id+MK-id row, reason block, status+date row
 T05  Amount uses fmtByn, date uses wsFormatDate (real formatting helpers, not ad hoc)
 T06  Stage colour semantics: pending_review=yellow, requires_check/ambiguous_parent_link=orange, error/missing_parent_link=red
 T07  Real backend readable_reason wins over the fallback map when present
 T08  Fallback reason map covers all five stages and is only used as a fallback
 T09  Approve button still gated by canApprovePilotIntents + pending_review + intent id
 T10  Raw technical error text is not shown directly; friendly text always shown instead
 T11  Technical details block still gated behind canAdminPilot
 T12  Empty state uses the shared SVG green-check icon, not an emoji
 T13  Loading state renders a card-shaped skeleton, not a generic row skeleton
 T14  Bottom-safe padding class preserved on the populated list
 T15  Overview's own renderer/layout code is untouched by this phase
 T16  All Payments / Pilot Clients renderers untouched
 T17  WORKSPACE_VIEW_ROLES / PILOT_ADMIN_ROLES / PAYMENT_APPROVAL_ROLES unchanged
 T19  Version / cache-bust is v7.1.8
 (T18/T20/T21 — local dev-preview wiring — removed once dev preview was deleted)

Run:
    python -m unittest tests.test_attention_visual_v7161 -v
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


def _js_fn(name: str, *, is_async: bool = False, window: int = 3000) -> str:
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
# T01-T03: Header / tabs / queue head
# ---------------------------------------------------------------------------

class TestHeaderReuse(unittest.TestCase):

    def test_01_header_and_tabs_markup_reused_verbatim(self):
        fn = _js_fn("_renderWorkspaceSkeleton")
        self.assertIn("ws-header", fn)
        self.assertIn("ws-header__title", fn)
        self.assertIn("attention", fn)

    def test_02_active_tab_still_shared_yellow_rule(self):
        block = _css_block(".ws-subtab.active")
        self.assertIn("--yellow", block)

    def test_03_queue_head_present(self):
        fn = _js_fn("_wsRenderAttention")
        self.assertIn("_wsQueueHead", fn)
        head = _js_fn("_wsQueueHead")
        self.assertIn("Требуют внимания", head)
        self.assertIn("ws-queue-count", head)
        css = _css_block(".ws-queue-head {")
        self.assertNotEqual(css, "")
        self.assertNotEqual(_css_block(".ws-queue-count {"), "")


# ---------------------------------------------------------------------------
# T04-T05: Card structure
# ---------------------------------------------------------------------------

class TestCardStructure(unittest.TestCase):

    def test_04_card_rows_present(self):
        fn = _js_fn("_wsRenderAttentionItem")
        self.assertIn("ws-attn-name", fn)
        self.assertIn("ws-attn-amount", fn)
        self.assertIn("ws-attn-sub", fn)
        self.assertIn("ws-attn-row--bottom", fn)
        self.assertIn("ws-stage-badge", fn)
        self.assertIn("ID в МойКласс", fn)

    def test_05_real_formatting_helpers_used(self):
        fn = _js_fn("_wsRenderAttentionItem")
        self.assertIn("fmtByn(item.amount_byn)", fn)
        self.assertIn("wsFormatDate(item.updated_at)", fn)


# ---------------------------------------------------------------------------
# T06: Stage colour semantics
# ---------------------------------------------------------------------------

class TestStageColours(unittest.TestCase):

    def test_06_stage_colour_mapping(self):
        css = _css()
        yellow = re.search(r'\.ws-stage-badge-pending_review\s*\{([^}]+)\}', css)
        orange1 = re.search(r'\.ws-stage-badge-requires_check\s*\{([^}]+)\}', css)
        orange2 = re.search(r'\.ws-stage-badge-ambiguous_parent_link\s*\{([^}]+)\}', css)
        red1 = re.search(r'\.ws-stage-badge-error\s*\{([^}]+)\}', css)
        red2 = re.search(r'\.ws-stage-badge-missing_parent_link\s*\{([^}]+)\}', css)
        for m in (yellow, orange1, orange2, red1, red2):
            self.assertIsNotNone(m)
        self.assertIn("#9b6b05", yellow.group(1))
        self.assertIn("#c2570d", orange1.group(1))
        self.assertIn("#c2570d", orange2.group(1))
        self.assertIn("#b14343", red1.group(1))
        self.assertIn("#b14343", red2.group(1))


# ---------------------------------------------------------------------------
# T07-T08: Reason block / fallback map
# ---------------------------------------------------------------------------

class TestReasonFallback(unittest.TestCase):

    def test_07_real_reason_wins_over_fallback(self):
        fn = _js_fn("_wsAttentionReasonHtml")
        self.assertIn("item.readable_reason || WS_ATTENTION_FALLBACK_REASON[stage]", fn)

    def test_08_fallback_map_covers_all_five_stages(self):
        js = _js()
        start = js.find("const WS_ATTENTION_FALLBACK_REASON")
        self.assertNotEqual(start, -1)
        block = js[start:start + 900]
        for stage in ("pending_review", "requires_check", "error", "missing_parent_link", "ambiguous_parent_link"):
            self.assertIn(f"{stage}:", block)


# ---------------------------------------------------------------------------
# T09: Approve button gating unchanged
# ---------------------------------------------------------------------------

class TestApproveGating(unittest.TestCase):

    def test_09_approve_gated_correctly(self):
        fn = _js_fn("_wsRenderAttentionItem")
        self.assertIn("canApprovePilotIntents", fn)
        self.assertIn('stage === "pending_review" && canApprove && pid', fn)
        self.assertIn("approvePaymentIntent(", fn)


# ---------------------------------------------------------------------------
# T10-T11: Error handling / tech-info gating unchanged
# ---------------------------------------------------------------------------

class TestErrorHandling(unittest.TestCase):

    def test_10_raw_text_not_shown_directly_to_client_manager(self):
        fn = _js_fn("_wsFriendlyAttentionError")
        self.assertIn("looksTechnical", fn)
        self.assertIn("WS_ATTENTION_FALLBACK_REASON.error", fn)
        # escapeHtml(raw) must appear exactly once, and only inside the
        # canAdminPilot-gated <details> block — never in the friendly message
        # shown to every role.
        self.assertEqual(fn.count("escapeHtml(raw)"), 1)
        details_idx = fn.find("<details")
        raw_idx = fn.find("escapeHtml(raw)")
        self.assertNotEqual(details_idx, -1)
        self.assertGreater(raw_idx, details_idx)
        gate_idx = fn.find("canAdminPilot")
        self.assertNotEqual(gate_idx, -1)
        self.assertLess(gate_idx, details_idx)

    def test_11_tech_details_gated_by_can_admin_pilot(self):
        fn = _js_fn("_wsFriendlyAttentionError")
        self.assertIn("looksTechnical && roleCaps().canAdminPilot", fn)
        self.assertIn("Техническая информация", fn)


# ---------------------------------------------------------------------------
# T12-T14: Empty / loading / safe-area states
# ---------------------------------------------------------------------------

class TestStates(unittest.TestCase):

    def test_12_empty_state_uses_svg_not_emoji(self):
        fn = _js_fn("_wsRenderAttention")
        self.assertIn("ws-empty-state-icon--success", fn)
        self.assertIn("WS_ICON_CHECK_BIG", fn)
        self.assertNotIn('_wsEmptyState("✅"', fn)

    def test_13_loading_state_is_card_shaped(self):
        fn = _js_fn("_wsRenderAttention")
        self.assertIn("_wsAttentionSkeletonCard", fn)
        skeleton = _js_fn("_wsAttentionSkeletonCard")
        self.assertIn("ws-attention-skeleton-card", skeleton)
        self.assertNotIn("ws-skeleton-row", fn)

    def test_14_bottom_safe_padding_preserved(self):
        fn = _js_fn("_wsRenderAttention")
        self.assertIn("ws-bottom-safe-pad", fn)


# ---------------------------------------------------------------------------
# T15-T17: Scope boundaries
# ---------------------------------------------------------------------------

class TestScopeBoundaries(unittest.TestCase):

    def test_15_overview_renderer_untouched(self):
        fn = _js_fn("_wsRenderOverview")
        self.assertIn("WS_OVERVIEW_STAT_META", fn)
        self.assertIn("ws-recent-list", fn)

    def test_16_other_renderers_untouched(self):
        # NOTE: intentionally updated for v7.1.6.1 step 3 — All Payments now
        # has its own dedicated _wsRenderPaymentCard() instead of delegating
        # to renderPaymentIntentList() (shared with the legacy admin #piList
        # screen, which step 3 must not touch). This is the one expected
        # exception to this test's "other tabs unaffected" boundary — Pilot
        # Clients (this test's actual concern for the Attention phase) is
        # still unchanged below.
        # v7.1.6.2: card rendering now happens in _wsRenderAllPaymentsResults()
        # (the results-only updater used on every keystroke), called from
        # _wsRenderAllPayments() rather than inlined into it.
        all_payments = _js_fn("_wsRenderAllPaymentsResults")
        self.assertIn("_wsRenderPaymentCard", all_payments)
        pilot = _js_fn("_wsRenderPilotClients")
        self.assertIn("Пока ни один клиент не добавлен в пилот", pilot)

    def test_17_role_capability_tables_unchanged(self):
        roles = _roles("WORKSPACE_VIEW_ROLES")
        for role in ("owner", "admin", "operations", "client_manager"):
            self.assertIn(f'"{role}"', roles)
        self.assertNotIn('"client_manager"', _roles("PILOT_ADMIN_ROLES"))
        self.assertIn('"client_manager"', _roles("PAYMENT_APPROVAL_ROLES"))


# ---------------------------------------------------------------------------
# T19: Version
# ---------------------------------------------------------------------------
# NOTE: T18/T20/T21 (local preview wiring) were removed in v7.1.6.1's final
# cleanup pass — the entire temporary local preview scaffold they tested was
# deleted from production code once visual approval was complete.

class TestVersionUnchanged(unittest.TestCase):

    def test_19_version_is_v7161(self):
        html = _html()
        js = _js()
        self.assertIn("v=7.1.8", html)
        self.assertIn('console.log("MiniApp version: v7.1.8")', js)


if __name__ == "__main__":
    unittest.main(verbosity=2)
