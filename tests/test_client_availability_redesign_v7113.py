"""Tests for v7.1.13 — "Возможности для расписания" visual redesign
(day-chips, Отмена/Пропустить context, read-only summary, success resume)
layered on top of the COMPLETELY UNCHANGED v7.1.12.3 backend
(client_schedule_availability_get/submit, the is_system standalone
campaign, ownership checks). This file focuses on what's NEW in v7.1.13;
CL-code/invite/staff-link access itself is already covered by
tests/test_client_schedule_availability_entry_v71123.py (re-run as
regression, not duplicated here) and the is_system protections by
tests/test_hide_internal_availability_campaign_v71123.py.

Covers checklist §17.B (items 9-21).

Run:
    python -m unittest tests.test_client_availability_redesign_v7113 -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from web_app_server import MiniAppContext
from utils import now_iso

APP_JS = ROOT / "miniapp" / "app.js"
STYLES_CSS = ROOT / "miniapp" / "styles.css"
INDEX_HTML = ROOT / "miniapp" / "index.html"
SECRET = "test-bot-token-secret"


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(
        bot_username="yellowclubagent_bot", telegram_bot_token=SECRET,
        admin_ids=[], senior_teacher_ids=[], web_app_test_roles=False,
        client_food_entry_visible=True, food_module_enabled=True,
        client_cabinet_v7113_enabled=True, client_cabinet_v7113_pilot_telegram_ids=[],
        client_notifications_enabled=True, client_notifications_pilot_telegram_ids=[],
    )
    ctx._role_store: dict[int, str] = {}
    ctx._role_for_user = lambda uid: ctx._role_store.get(int(uid), "other")
    ctx.moyklass = types.SimpleNamespace(request=lambda *a, **k: types.SimpleNamespace(ok=False, data=None))
    return ctx


def _auth(uid: int, role: str, ctx: MiniAppContext) -> dict:
    ctx._role_store[uid] = role
    return {"user_id": uid}


class AvailabilityTestBase(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        self.owner = _auth(1, "owner", self.ctx)

    def _cl_link(self, mk_user_id, child_name, parent_tid):
        code = self.storage.create_client_link_code(mk_user_id, child_name, "1")
        self.assertTrue(code["ok"], code)
        r = self.storage.link_client_child(str(parent_tid), code["code"], now_iso())
        self.assertTrue(r["ok"], r)
        return r


# ── Backend regression: multi-interval + preferred/possible round-trip ────

class TestMultiIntervalRoundTrip(AvailabilityTestBase):
    def test_13_multiple_intervals_saved_and_reloaded(self):
        self._cl_link("30001", "Kid", "7001")
        parent = _auth(7001, "parent", self.ctx)
        intervals = [
            {"weekday": 1, "start_time": "16:00", "end_time": "17:00", "preference": "preferred"},
            {"weekday": 3, "start_time": "18:00", "end_time": "19:00", "preference": "possible"},
            {"weekday": 6, "start_time": "10:00", "end_time": "12:00", "preference": "preferred"},
        ]
        save = self.ctx.client_schedule_availability_submit(parent, "30001", {
            "preferred_branch": "YC1", "available_from": "2026-09-01",
            "schedule_comment": "test", "intervals": intervals,
        })
        self.assertTrue(save["ok"], save)
        reloaded = self.ctx.client_schedule_availability_get(parent, "30001")
        self.assertTrue(reloaded["ok"], reloaded)
        self.assertEqual(len(reloaded["intervals"]), 3)

    def test_14_preferred_and_possible_persist_correctly(self):
        self._cl_link("30002", "Kid", "7002")
        parent = _auth(7002, "parent", self.ctx)
        self.ctx.client_schedule_availability_submit(parent, "30002", {
            "preferred_branch": "either",
            "intervals": [
                {"weekday": 2, "start_time": "15:00", "end_time": "16:00", "preference": "preferred"},
                {"weekday": 4, "start_time": "15:00", "end_time": "16:00", "preference": "possible"},
            ],
        })
        reloaded = self.ctx.client_schedule_availability_get(parent, "30002")
        prefs = {iv["weekday"]: iv["preference"] for iv in reloaded["intervals"]}
        self.assertEqual(prefs[2], "preferred")
        self.assertEqual(prefs[4], "possible")

    def test_15_invalid_interval_rejected_by_backend(self):
        """15. end <= start is rejected server-side too (defense in depth —
        not just the new frontend validation)."""
        self._cl_link("30003", "Kid", "7003")
        parent = _auth(7003, "parent", self.ctx)
        save = self.ctx.client_schedule_availability_submit(parent, "30003", {
            "preferred_branch": "YC1",
            "intervals": [{"weekday": 1, "start_time": "17:00", "end_time": "16:00", "preference": "possible"}],
        })
        self.assertFalse(save["ok"], save)

    def test_16_error_does_not_wipe_other_recipient_data(self):
        """16. A rejected save must not corrupt/clear a previously-saved
        valid submission for the same child."""
        self._cl_link("30004", "Kid", "7004")
        parent = _auth(7004, "parent", self.ctx)
        good = self.ctx.client_schedule_availability_submit(parent, "30004", {
            "preferred_branch": "YC1",
            "intervals": [{"weekday": 1, "start_time": "16:00", "end_time": "17:00", "preference": "preferred"}],
        })
        self.assertTrue(good["ok"], good)
        bad = self.ctx.client_schedule_availability_submit(parent, "30004", {
            "preferred_branch": "YC1",
            "intervals": [{"weekday": 1, "start_time": "18:00", "end_time": "17:00", "preference": "preferred"}],
        })
        self.assertFalse(bad["ok"], bad)
        # storage.submit_schedule_availability validates BEFORE writing, so
        # the earlier good submission must still be intact.
        current = self.ctx.client_schedule_availability_get(parent, "30004")
        self.assertEqual(len(current["intervals"]), 1)
        self.assertEqual(current["intervals"][0]["start_time"], "16:00")


class TestCrossChildAndForeignAccess(AvailabilityTestBase):
    def test_19_own_children_data_not_mixed(self):
        self._cl_link("30005", "Kid A", "7005")
        self._cl_link("30006", "Kid B", "7005")
        parent = _auth(7005, "parent", self.ctx)
        self.ctx.client_schedule_availability_submit(parent, "30005", {
            "preferred_branch": "YC1", "intervals": [{"weekday": 1, "start_time": "16:00", "end_time": "17:00", "preference": "preferred"}],
        })
        self.ctx.client_schedule_availability_submit(parent, "30006", {
            "preferred_branch": "YC2", "intervals": [{"weekday": 5, "start_time": "10:00", "end_time": "11:00", "preference": "possible"}],
        })
        a = self.ctx.client_schedule_availability_get(parent, "30005")
        b = self.ctx.client_schedule_availability_get(parent, "30006")
        self.assertEqual(a["preferred_branch"], "YC1")
        self.assertEqual(b["preferred_branch"], "YC2")
        self.assertNotEqual(a["intervals"][0]["weekday"], b["intervals"][0]["weekday"])

    def test_20_foreign_mk_user_id_denied(self):
        self._cl_link("30007", "Victim Kid", "7006")
        self._cl_link("30008", "Attacker Kid", "7007")
        attacker = _auth(7007, "parent", self.ctx)
        r = self.ctx.client_schedule_availability_get(attacker, "30007")
        self.assertFalse(r["ok"], r)
        w = self.ctx.client_schedule_availability_submit(attacker, "30007", {
            "preferred_branch": "YC1", "intervals": [{"weekday": 1, "start_time": "16:00", "end_time": "17:00", "preference": "preferred"}],
        })
        self.assertFalse(w["ok"], w)

    def test_21_standalone_system_campaign_stays_hidden(self):
        self._cl_link("30009", "Kid", "7008")
        parent = _auth(7008, "parent", self.ctx)
        self.ctx.client_schedule_availability_submit(parent, "30009", {
            "preferred_branch": "YC1", "intervals": [{"weekday": 1, "start_time": "16:00", "end_time": "17:00", "preference": "preferred"}],
        })
        campaigns = self.ctx.onboarding_campaigns_list(self.owner, {})
        names = [c["name"] for c in campaigns.get("campaigns", [])]
        self.assertNotIn(self.storage.STANDALONE_AVAILABILITY_CAMPAIGN_NAME, names)


# ── Frontend: static assertions for what's genuinely new in v7.1.13 ───────

class TestAvailabilityRedesignStatic(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_day_chips_replace_native_select(self):
        self.assertIn("_ocAvailDayChipsHtml", self.js)
        self.assertIn("oc-day-chip", self.js)
        # The old per-row native <select data-field="weekday"> must be gone.
        self.assertNotIn('<select data-field="weekday">', self.js)

    def test_17_modal_is_onboarding_only_secondary_always_skip(self):
        """17. Round 2: the bottom-sheet modal is reached ONLY via the
        onboarding-invite deep link now, so its secondary button is always
        a fixed "Пропустить" (no more Cancel/Skip branching on mkUserId —
        the standalone/cabinet entry moved to the full-page screen, which
        has its own independent "Отмена" secondary button)."""
        idx = self.js.find("function _ocAvailSetMode")
        self.assertNotEqual(idx, -1)
        body = self.js[idx:idx + 1000]
        self.assertIn('skipBtn.textContent = "Пропустить"', body)
        idx_html = INDEX_HTML.read_text(encoding="utf-8").find('id="ocAvailabilitySkip"')
        self.assertNotEqual(idx_html, -1)

    def test_18_onboarding_context_still_available(self):
        # openOnboardingAvailabilityModal sets recipientId (not mkUserId) —
        # the modal always operates in onboarding-recipient identity now.
        idx = self.js.find("async function openOnboardingAvailabilityModal")
        body = self.js[idx:idx + 250]
        self.assertIn("_ocAvailState.recipientId = recipientId", body)
        self.assertIn("_ocAvailState.mkUserId = null", body)

    def test_cabinet_entry_opens_full_page_not_modal(self):
        """Round 2 §A.4: openClientScheduleAvailabilityModal (Home-card /
        notification-action entry point) must route to the full-page
        #tab-availability screen, never re-open the bottom-sheet modal."""
        idx = self.js.find("function openClientScheduleAvailabilityModal")
        self.assertNotEqual(idx, -1)
        body = self.js[idx:idx + 300]
        self.assertIn('activateTab("availability")', body)
        self.assertIn("_availScreenOpenFor", body)
        self.assertNotIn("piModalOpen", body)

    def test_availability_tab_button_and_panel_exist(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('data-tab="availability"', html)
        self.assertIn('id="tab-availability"', html)

    def test_10_save_disabled_live_when_no_intervals(self):
        """10. _ocAvailApplyRowValidation disables the Save button live
        (not just at click time) whenever validation fails, including the
        no-intervals case (index -1, no row to flag but button still off)."""
        idx = self.js.find("function _ocAvailApplyRowValidation")
        self.assertNotEqual(idx, -1)
        body = self.js[idx:idx + 900]
        self.assertIn("btn.disabled = !!err", body)
        self.assertIn("_ocAvailValidate()", body)

    def test_11_end_after_start_validated_with_row_highlight(self):
        """11. end <= start is caught by the same shared validator and the
        offending row gets a visible highlight + inline note next to it."""
        idx = self.js.find("function _ocAvailValidate")
        body = self.js[idx:idx + 500]
        self.assertIn("Окончание должно быть позже начала", body)
        idx2 = self.js.find("function _ocAvailApplyRowValidation")
        body2 = self.js[idx2:idx2 + 900]
        self.assertIn("oc-interval-row-v2--invalid", body2)
        self.assertIn("oc-interval-row-note", body2)

    def test_12_read_only_summary_shows_all_required_fields(self):
        """12. Summary view (both modal "ocSummary" and full-page
        "availSummary" prefixes) must surface филиал/дата начала/комментарий
        /интервалы with preferred-vs-possible styling and Russian dates."""
        idx = self.js.find("function _ocAvailFillSummaryView")
        self.assertNotEqual(idx, -1)
        body = self.js[idx:idx + 1000]
        self.assertIn("${prefix}Branch", body)
        self.assertIn("${prefix}From", body)
        self.assertIn("${prefix}Comment", body)
        self.assertIn("${prefix}Intervals", body)
        self.assertIn("cabFormatDate(_ocAvailState.availableFrom)", body)
        self.assertIn("oc-summary-chip--${iv.preference", body)
        self.assertIn('"preferred" : "possible"', body)
        # Both surfaces expose an "Изменить" edit button off this summary.
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("Изменить", html)

    def test_12b_success_screen_shows_required_fields(self):
        """4. Success screen: филиал, дни, количество интервалов,
        "Изменить", "На главную"."""
        idx = self.js.find("function _ocAvailFillSuccessView")
        self.assertNotEqual(idx, -1)
        body = self.js[idx:idx + 400]
        self.assertIn("${prefix}Branch", body)
        self.assertIn("${prefix}Days", body)
        self.assertIn("${prefix}Count", body)
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("На главную", html)

    def test_full_page_screen_reuses_shared_validation_helpers(self):
        """No second backend/data model: the full-page screen's save path
        must reuse the same _ocAvailValidate/_ocAvailApplyRowValidation
        helpers the modal uses, not a parallel copy."""
        idx = self.js.find("function _availScreenSave")
        self.assertNotEqual(idx, -1)
        body = self.js[idx:idx + 900]
        self.assertIn("_ocAvailValidate", body)

    def test_at_least_one_interval_required_to_save(self):
        # Round 2: validation moved into the shared _ocAvailValidate() helper
        # (reused by both the modal and the full-page screen) so the "no
        # intervals" message is asserted there, not inline in _ocAvailSave.
        idx = self.js.find("function _ocAvailValidate")
        self.assertNotEqual(idx, -1)
        body = self.js[idx:idx + 400]
        self.assertIn("Добавьте хотя бы один интервал", body)
        idx_save = self.js.find("async function _ocAvailSave")
        save_body = self.js[idx_save:idx_save + 400]
        self.assertIn("_ocAvailValidate()", save_body)

    def test_read_only_summary_and_success_modes_exist(self):
        self.assertIn('function _ocAvailSetMode', self.js)
        self.assertIn('_ocAvailFillSummaryView', self.js)
        self.assertIn('_ocAvailFillSuccessView', self.js)

    def test_modal_footer_is_opaque_not_transparent_gradient(self):
        idx = self.css.find(".pi-modal-sheet {")
        segment = self.css[idx:idx + 400]
        self.assertIn("background: #fff", segment)


if __name__ == "__main__":
    unittest.main()
