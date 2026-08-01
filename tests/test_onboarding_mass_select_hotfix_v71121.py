"""Tests for v7.1.12.1 hotfix — mass candidate selection for bulk client onboarding.

Production hit a real UX wall: "Загрузить всех учеников из МойКласс" loaded
1753 unique candidates, but adding them to a campaign required checking 1753
individual checkboxes one at a time. This hotfix adds a mass-selection panel
("Выбрать всех загруженных — N" / "Снять выбор" / "Выбрано: N"), a dynamic
Add-button label, a >100 confirmation modal (not a browser confirm()), raises
the backend import cap from 500 to ONBOARDING_IMPORT_MAX_BATCH_SIZE=2500, and
makes the backend re-verify submitted candidate name/branch/course against a
server-side MoyKlass cache instead of trusting the frontend blindly.

Explicitly NOT touched: Food Module, intern module, payments, bePaid,
MoyKlass posting, Telegram activation, campaign lifecycle, the existing
single-candidate search/select flow, existing filters/recipients behavior.

Test numbering matches the hotfix spec's 16-item checklist. Items that are
pure frontend interaction state (1-11, 14-16) are covered as static source
analysis, matching this project's established convention for miniapp/app.js
(see test_onboarding_campaign_ui_v7112.py). Items with real server-side
behavior (12, 13, and the data-integrity requirement from spec section 4)
are covered as real backend calls against an in-memory campaign.

Run:
    python -m unittest tests.test_onboarding_mass_select_hotfix_v71121 -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage, ONBOARDING_IMPORT_MAX_BATCH_SIZE
from web_app_server import MiniAppContext
from moyklass_client import MoyKlassResult


# ─────────────────────────────────────────────────────────────────────────────
# Static source helpers (matches test_onboarding_campaign_ui_v7112.py convention)
# ─────────────────────────────────────────────────────────────────────────────

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
STYLES_CSS = ROOT / "miniapp" / "styles.css"

_js_cache: str | None = None
_html_cache: str | None = None
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


def _css() -> str:
    global _css_cache
    if _css_cache is None:
        _css_cache = STYLES_CSS.read_text(encoding="utf-8")
    return _css_cache


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


# ─────────────────────────────────────────────────────────────────────────────
# Backend fixtures
# ─────────────────────────────────────────────────────────────────────────────

SECRET = "test-bot-token-secret"


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(bot_username="yellowclubagent_bot", telegram_bot_token=SECRET)
    ctx._role_store: dict[int, str] = {}

    def _role_for_user(uid: int) -> str:
        return ctx._role_store.get(int(uid), "other")

    ctx._role_for_user = _role_for_user
    ctx._onboarding_candidates_cache = {}
    return ctx


def _auth(uid: int, role: str, ctx: MiniAppContext) -> dict:
    ctx._role_store[uid] = role
    return {"user_id": uid}


class _FakeMoyKlassBulk:
    """Mirrors the real production report: 1753 unique students loaded over
    9 pages of 200 (8 full pages + a 153-item last page)."""

    def __init__(self, items):
        self._items = items

    def list_users_bulk(self, params=None, page_size=200, max_pages=30):
        pages_loaded = (len(self._items) + page_size - 1) // max(1, page_size)
        return MoyKlassResult(True, data={
            "items": list(self._items),
            "diagnostics": {
                "pages_loaded": pages_loaded,
                "raw_items": len(self._items),
                "unique_items": len(self._items),
                "stopped_reason": "short_page",
            },
        })


def _make_1753_students():
    return [
        {"id": str(20000 + i), "name": f"Student{i}", "lastName": "Ivanov", "filial": {"name": "YC1"}}
        for i in range(1753)
    ]


class OnboardingHotfixTestBase(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        self.owner = _auth(1, "owner", self.ctx)

    def _make_campaign(self, **kw):
        kw.setdefault("name", "Массовая кампания — hotfix test")
        kw.setdefault("academic_year", "2026/2027")
        r = self.ctx.onboarding_campaign_create(self.owner, kw)
        self.assertTrue(r.get("ok"), r)
        r2 = self.ctx.onboarding_campaign_start(self.owner, str(r["campaign"]["id"]))
        self.assertTrue(r2.get("ok"), r2)
        return r2["campaign"]


# ─────────────────────────────────────────────────────────────────────────────
# 1/2 — mass selection semantics (backend data layer: N is correct + already-
# present recipients don't reappear as "new" via bulk fetch's own contract).
# The actual select-all/skip-already-added LOOP is frontend state; see
# TestFrontendSelectionState below for the static-source half of these items.
# ─────────────────────────────────────────────────────────────────────────────

class TestBulkLoadCount(OnboardingHotfixTestBase):
    def test_1_bulk_load_returns_1753_unique_candidates(self):
        self.ctx.moyklass = _FakeMoyKlassBulk(_make_1753_students())
        r = self.ctx.onboarding_campaign_bulk_candidates(self.owner, {})
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(r["candidates"]), 1753)
        self.assertEqual(r["diagnostics"]["unique_items"], 1753)
        self.assertEqual(r["diagnostics"]["pages_loaded"], 9)

    def test_2_already_present_recipients_still_appear_in_bulk_result(self):
        # bulk_candidates itself never filters by campaign membership — the
        # exclusion is a frontend concern (existing recipients disabled/
        # skipped client-side). Confirms the data backend select-all reads
        # from is unfiltered/complete, matching _wsOcExistingRecipientIds'
        # job of doing the exclusion, not the server.
        students = _make_1753_students()
        self.ctx.moyklass = _FakeMoyKlassBulk(students)
        campaign = self._make_campaign()
        self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(campaign["id"]), {"recipients": [{"mk_user_id": "20000"}, {"mk_user_id": "20001"}]}
        )
        r = self.ctx.onboarding_campaign_bulk_candidates(self.owner, {})
        ids = {c["mk_user_id"] for c in r["candidates"]}
        self.assertIn("20000", ids)
        self.assertIn("20001", ids)


# ─────────────────────────────────────────────────────────────────────────────
# 3-11, 14, 16 — frontend interaction state (static source analysis, matching
# this project's established convention — no JS execution harness exists).
# ─────────────────────────────────────────────────────────────────────────────

class TestFrontendSelectionState(unittest.TestCase):
    def test_1_select_all_iterates_full_loaded_set_not_just_visible(self):
        fn = _js_fn("_wsOcImportSelectAllLoaded")
        self.assertIn("_ocState.importResults", fn)
        self.assertIn(".forEach", fn)
        self.assertNotIn("querySelectorAll", fn, "must not depend on which rows are in the DOM/scrolled into view")

    def test_2_select_all_skips_already_added_recipients(self):
        fn = _js_fn("_wsOcImportSelectAllLoaded")
        self.assertIn("_wsOcExistingRecipientIds", fn)
        self.assertIn("existingIds.has(mk)", fn)

    def test_2b_existing_recipient_checkbox_rendered_disabled(self):
        fn = _js_fn("_wsOcImportSectionHtml")
        self.assertIn("alreadyAdded", fn)
        self.assertIn('${alreadyAdded ? "disabled" : ""}', fn)

    def test_3_deselect_all_clears_selection_set(self):
        fn = _js_fn("_wsOcImportDeselectAll")
        self.assertIn("_ocState.importSelected = new Set()", fn)

    def test_4_manual_checkbox_toggle_still_keyed_by_mk_user_id(self):
        fn = _js_fn("_wsOcWireImportSection")
        self.assertIn("_ocState.importSelected.add(mk)", fn)
        self.assertIn("_ocState.importSelected.delete(mk)", fn)
        # Source of truth is the JS Set, never the DOM checkbox — required by
        # spec section 1 ("не привязывать выбор к DOM-checkbox").
        self.assertIn("dataset.importMk", fn)

    def test_5_search_and_bulk_load_never_clear_selection(self):
        search_fn = _js_fn("_wsOcSearchCandidates", is_async=True)
        bulk_fn = _js_fn("_wsOcLoadAllCandidates", is_async=True)
        for fn, label in ((search_fn, "search"), (bulk_fn, "bulk load")):
            self.assertNotIn("importSelected = new Set()", fn, f"{label} must not wipe selection")
            self.assertNotIn("importSelected.clear()", fn, f"{label} must not wipe selection")

    def test_6_add_button_label_shows_selected_count(self):
        fn = _js_fn("_wsOcImportSectionHtml")
        self.assertIn('`Добавить выбранных — ${selectedCount}`', fn)

    def test_7_add_button_disabled_when_zero_selected(self):
        fn = _js_fn("_wsOcImportSectionHtml")
        self.assertIn('${selectedCount === 0 ? "disabled" : ""}', fn)

    def test_8_confirm_modal_shown_above_threshold_100(self):
        self.assertIn("const ONBOARDING_IMPORT_CONFIRM_THRESHOLD = 100;", _js())
        fn = _js_fn("_wsOcImportAddClicked")
        self.assertIn("chosen.length > ONBOARDING_IMPORT_CONFIRM_THRESHOLD", fn)
        self.assertIn("_wsOcImportConfirmOpen(chosen)", fn)
        # Never a native browser confirm() — reuses the existing pi-modal pattern.
        self.assertNotIn("window.confirm(", fn)
        self.assertNotIn(" confirm(", fn)

    def test_9_cancel_never_sends_request(self):
        fn = _js_fn("_wsOcImportConfirmCancel")
        self.assertNotIn("_apiPostRaw", fn)
        self.assertNotIn("_wsOcImportDoSend", fn)
        self.assertIn("piModalClose", fn)

    def test_10_confirm_sends_exactly_one_request(self):
        send_fn = _js_fn("_wsOcImportDoSend", is_async=True)
        self.assertEqual(send_fn.count("_apiPostRaw("), 1, "must be exactly one HTTP request regardless of selection size")
        proceed_fn = _js_fn("_wsOcImportConfirmProceed")
        self.assertEqual(proceed_fn.count("_wsOcImportDoSend("), 1)

    def test_11_payload_includes_every_selected_mk_user_id(self):
        fn = _js_fn("_wsOcImportDoSend", is_async=True)
        self.assertIn("recipients: chosen.map(c => ({", fn)
        self.assertIn("mk_user_id: c.mk_user_id", fn)

    def test_14_success_clears_selection_and_refreshes_once(self):
        fn = _js_fn("_wsOcImportDoSend", is_async=True)
        self.assertIn("_ocState.importSelected = new Set()", fn)
        self.assertIn("_ocState.importOpen = false", fn)
        self.assertEqual(fn.count("_wsOcLoadCampaignDetail()"), 1)

    def test_16_existing_single_search_flow_untouched(self):
        # Regression: the pre-hotfix search endpoint path and its per-checkbox
        # wiring are still present and unchanged in shape.
        fn = _js_fn("_wsOcSearchCandidates", is_async=True)
        self.assertIn("/api/client/onboarding/candidates?q=", fn)
        wire_fn = _js_fn("_wsOcWireImportSection")
        self.assertIn('data-import-mk', wire_fn)
        self.assertIn("wsOcImportSearchBtn", wire_fn)
        self.assertIn("wsOcImportLoadAllBtn", wire_fn)


class TestConfirmModalMarkup(unittest.TestCase):
    def test_8b_modal_exists_with_cancel_and_confirm_actions(self):
        html = _html()
        self.assertIn('id="wsOcImportConfirmModal"', html)
        self.assertIn('id="wsOcImportConfirmText"', html)
        self.assertIn('id="wsOcImportConfirmBack"', html)
        self.assertIn('id="wsOcImportConfirmBtn"', html)

    def test_8c_confirm_text_includes_count_and_campaign_name(self):
        fn = _js_fn("_wsOcImportConfirmOpen")
        self.assertIn("chosen.length", fn)
        self.assertIn("campaignName", fn)
        self.assertIn("campaignDetail?.campaign?.name", fn)


# ─────────────────────────────────────────────────────────────────────────────
# 12/13 — real 1753-candidate import, one request, idempotent replay
# ─────────────────────────────────────────────────────────────────────────────

class TestBulkImport1753(OnboardingHotfixTestBase):
    def setUp(self):
        super().setUp()
        self.students = _make_1753_students()
        self.ctx.moyklass = _FakeMoyKlassBulk(self.students)
        self.campaign = self._make_campaign()
        # Warms the server-side verification cache exactly like the real UI
        # flow does ("Загрузить всех учеников из МойКласс" before "Добавить").
        loaded = self.ctx.onboarding_campaign_bulk_candidates(self.owner, {})
        self.assertTrue(loaded["ok"])
        self.recipients_payload = [{"mk_user_id": c["mk_user_id"]} for c in loaded["candidates"]]

    def test_12_import_1753_recipients_in_one_call(self):
        r = self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(self.campaign["id"]), {"recipients": self.recipients_payload}
        )
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["added"], 1753)
        self.assertEqual(r["already_present"], 0)
        # v7.1.12.1 hotfix #2 — "errors" is a list of {mk_user_id, error_code}
        # (per-item candidate_not_verified detail), not a count; "failed" is
        # the count. See test_onboarding_mass_select_hotfix_v71121_trust.py.
        self.assertEqual(r["failed"], 0)
        self.assertEqual(r["errors"], [])
        recipients = self.storage.list_onboarding_campaign_recipients(self.campaign["id"])
        self.assertEqual(len(recipients), 1753)

    def test_13_repeat_import_creates_no_duplicates(self):
        self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(self.campaign["id"]), {"recipients": self.recipients_payload}
        )
        r2 = self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(self.campaign["id"]), {"recipients": self.recipients_payload}
        )
        self.assertTrue(r2["ok"], r2)
        self.assertEqual(r2["added"], 0)
        self.assertEqual(r2["already_present"], 1753)
        recipients = self.storage.list_onboarding_campaign_recipients(self.campaign["id"])
        self.assertEqual(len(recipients), 1753)

    def test_12b_server_verified_name_wins_over_fabricated_frontend_name(self):
        # Spec section 4: "не доверять данным имени/филиала/уровня,
        # присланным frontend без проверки". mk_user_id 20000's real cached
        # name is "Student0 Ivanov" (from the fake MoyKlass fixture) — a
        # tampered/buggy frontend claiming a different name must be ignored.
        r = self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(self.campaign["id"]),
            {"recipients": [{"mk_user_id": "20000", "child_display_name": "FAKE INJECTED NAME", "branch_name": "FAKE"}]},
        )
        self.assertTrue(r["ok"], r)
        rec = self.storage.list_onboarding_campaign_recipients(self.campaign["id"])[0]
        self.assertEqual(rec["child_display_name"], "Student0 Ivanov")
        self.assertNotEqual(rec["child_display_name"], "FAKE INJECTED NAME")


class TestImportLimitRaised(OnboardingHotfixTestBase):
    def test_limit_constant_is_2500(self):
        self.assertEqual(ONBOARDING_IMPORT_MAX_BATCH_SIZE, 2500)
        self.assertGreaterEqual(ONBOARDING_IMPORT_MAX_BATCH_SIZE, 2000)

    def test_1753_no_longer_hits_old_500_cap(self):
        self.assertGreater(1753, 500, "sanity: this is exactly the count that broke production under the old cap")
        self.ctx.moyklass = _FakeMoyKlassBulk(_make_1753_students())
        campaign = self._make_campaign()
        loaded = self.ctx.onboarding_campaign_bulk_candidates(self.owner, {})
        payload = [{"mk_user_id": c["mk_user_id"]} for c in loaded["candidates"]]
        r = self.ctx.onboarding_campaign_import_recipients(self.owner, str(campaign["id"]), {"recipients": payload})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["added"], 1753)

    def test_over_2500_rejected(self):
        campaign = self._make_campaign()
        payload = [{"mk_user_id": str(i)} for i in range(2501)]
        r = self.ctx.onboarding_campaign_import_recipients(self.owner, str(campaign["id"]), {"recipients": payload})
        self.assertFalse(r["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# 15 — responsive at 360px / 375px
# ─────────────────────────────────────────────────────────────────────────────

class TestResponsiveWidth(unittest.TestCase):
    def test_15_mass_select_bar_stacks_under_420px(self):
        css = _css()
        start = css.find(".ws-oc-mass-select-bar")
        self.assertNotEqual(start, -1)
        block = css[start:start + 1600]
        self.assertIn("@media (max-width: 420px)", block)
        self.assertIn("flex-direction: column", block)
        # No explicit horizontal-scroll opt-in introduced for this bar.
        self.assertNotIn("overflow-x: scroll", block)
        self.assertNotIn("overflow-x: auto", block)

    def test_15b_no_position_fixed_bottom_bar_added(self):
        # Guards against a sticky/fixed footer bar that Telegram WebView is
        # known to mishandle — the hotfix deliberately stays in normal flow.
        css = _css()
        start = css.find(".ws-oc-mass-select-bar")
        block = css[start:start + 1600]
        self.assertNotIn("position: fixed", block)


# ─────────────────────────────────────────────────────────────────────────────
# Version / cache-bust for this hotfix
# ─────────────────────────────────────────────────────────────────────────────

class TestHotfixVersion(unittest.TestCase):
    # v7.1.12.2 superseded this hotfix's own version bump — these assert the
    # CURRENT marker/cache-bust, not the historical v7.1.12.1 this file was
    # originally written against.
    def test_app_js_version_is_current(self):
        self.assertIn('console.log("MiniApp version: v7.1.12.2");', _js())

    def test_index_html_cache_bust_is_current(self):
        html = _html()
        self.assertIn("styles.css?v=7.1.12.2", html)
        self.assertIn("app.js?v=7.1.12.2", html)


if __name__ == "__main__":
    unittest.main()
