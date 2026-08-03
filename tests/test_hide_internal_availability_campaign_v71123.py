"""Tests for v7.1.12.3 pre-deploy hotfix — hiding/protecting the internal
standalone availability campaign.

The final report for the previous v7.1.12.3 commit (557c8dc) flagged a real
risk: the system container campaign ("Возможности по расписанию — все
клиенты"), used internally to hold client_onboarding_recipients rows for
clients linked via CL-code/staff without a real onboarding campaign, was
visible in the ordinary staff campaign list and could in theory be started/
closed/archived/exported like a real campaign.

This adds a reliable is_system flag (not name-matching) on
client_onboarding_campaigns, self-migrated via the existing _ensure_column
mechanism (no manual production DB change), excludes is_system=1 rows from
list_onboarding_campaigns, and guards every staff-facing mutation
(start/close/archive/import-recipients/create-invite/create-invites-batch/
export-csv) against targeting it — while leaving it fully usable as the
internal availability container (get_or_create_recipient_for_client calls
storage methods directly, bypassing these staff-facing guards entirely) and
still readable via the ordinary campaign detail view / availability summary
(staff must still be able to review CL-code clients' answers).

Run:
    python -m unittest tests.test_hide_internal_availability_campaign_v71123 -v
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
    ctx._role_for_user = lambda uid: ctx._role_store.get(int(uid), "other")
    ctx.moyklass = types.SimpleNamespace(request=lambda *a, **k: types.SimpleNamespace(ok=False, data=None))
    return ctx


def _auth(uid: int, role: str, ctx: MiniAppContext) -> dict:
    ctx._role_store[uid] = role
    return {"user_id": uid}


class HideTestBase(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        self.owner = _auth(1, "owner", self.ctx)

    def _real_campaign(self, **kw):
        kw.setdefault("name", "Настоящая кампания")
        kw.setdefault("academic_year", "2026/2027")
        r = self.ctx.onboarding_campaign_create(self.owner, kw)
        self.assertTrue(r["ok"], r)
        return r["campaign"]

    def _cl_link(self, mk_user_id, child_name, parent_tid):
        code = self.storage.create_client_link_code(mk_user_id, child_name, "1")
        self.assertTrue(code["ok"], code)
        r = self.storage.link_client_child(str(parent_tid), code["code"], now_iso())
        self.assertTrue(r["ok"], r)
        return r

    def _make_system_campaign(self, mk_user_id="90001", child_name="Sys Kid", parent_tid=50001):
        """Creates the system campaign the same way the real feature does —
        via a client submitting availability through the permanent entry
        point — never by calling storage internals directly, so this test
        exercises the real trigger path."""
        self._cl_link(mk_user_id, child_name, parent_tid)
        parent = _auth(parent_tid, "parent", self.ctx)
        r = self.ctx.client_schedule_availability_submit(parent, mk_user_id, {"intervals": []})
        self.assertTrue(r["ok"], r)
        return int(r["recipient"]["campaign_id"])


# ─────────────────────────────────────────────────────────────────────────────
# 1/2 — list exclusion, regular campaigns unaffected
# ─────────────────────────────────────────────────────────────────────────────

class TestListExclusion(HideTestBase):
    def test_1_system_campaign_not_in_normal_list(self):
        sys_campaign_id = self._make_system_campaign()
        listed = self.ctx.onboarding_campaigns_list(self.owner, {})
        self.assertTrue(listed["ok"], listed)
        ids = [c["id"] for c in listed["campaigns"]]
        self.assertNotIn(sys_campaign_id, ids)

    def test_2_regular_campaigns_returned_unchanged(self):
        real = self._real_campaign()
        self._make_system_campaign()
        listed = self.ctx.onboarding_campaigns_list(self.owner, {})
        ids = [c["id"] for c in listed["campaigns"]]
        self.assertIn(real["id"], ids)
        self.assertEqual(len(listed["campaigns"]), 1)

    def test_2b_storage_level_list_also_excludes_it(self):
        self._real_campaign()
        self._make_system_campaign()
        rows = self.storage.list_onboarding_campaigns()
        self.assertTrue(all(not r.get("is_system") for r in rows))
        rows_by_status = self.storage.list_onboarding_campaigns(status="active")
        self.assertTrue(all(not r.get("is_system") for r in rows_by_status))


# ─────────────────────────────────────────────────────────────────────────────
# 3/4/5/6/7 — every staff mutation is blocked
# ─────────────────────────────────────────────────────────────────────────────

class TestMutationsBlocked(HideTestBase):
    def setUp(self):
        super().setUp()
        self.sys_id = self._make_system_campaign()

    def test_3_cannot_start(self):
        r = self.ctx.onboarding_campaign_start(self.owner, str(self.sys_id))
        self.assertFalse(r["ok"], r)
        self.assertEqual(r.get("reason_code"), "system_campaign")

    def test_4_cannot_close(self):
        r = self.ctx.onboarding_campaign_close(self.owner, str(self.sys_id))
        self.assertFalse(r["ok"], r)

    def test_4b_cannot_archive(self):
        r = self.ctx.onboarding_campaign_archive(self.owner, str(self.sys_id))
        self.assertFalse(r["ok"], r)

    def test_5_cannot_create_single_invite(self):
        recipient = self.storage.list_onboarding_campaign_recipients(self.sys_id)[0]
        r = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.sys_id), {"recipient_id": recipient["id"]})
        self.assertFalse(r["ok"], r)

    def test_5b_cannot_create_batch_invites(self):
        recipient = self.storage.list_onboarding_campaign_recipients(self.sys_id)[0]
        r = self.ctx.onboarding_campaign_create_invites_batch(self.owner, str(self.sys_id), {"recipient_ids": [recipient["id"]]})
        self.assertFalse(r["ok"], r)

    def test_6_cannot_import_recipients_via_staff_endpoint(self):
        r = self.ctx.onboarding_campaign_import_recipients(self.owner, str(self.sys_id), {"recipients": [{"mk_user_id": "99999"}]})
        self.assertFalse(r["ok"], r)

    def test_7_cannot_export_csv(self):
        r = self.ctx.onboarding_campaign_export_csv(self.owner, str(self.sys_id), {})
        self.assertIsInstance(r, dict)
        self.assertFalse(r["ok"], r)

    def test_no_delete_operation_exists(self):
        # No onboarding_campaign_delete method anywhere in this codebase —
        # documented here so this stays true rather than silently rotting.
        import web_app_server
        self.assertFalse(hasattr(web_app_server.MiniAppContext, "onboarding_campaign_delete"))


# ─────────────────────────────────────────────────────────────────────────────
# 8/9 — availability itself keeps working
# ─────────────────────────────────────────────────────────────────────────────

class TestAvailabilityStillWorks(HideTestBase):
    def test_8_get_and_submit_still_work(self):
        self._cl_link("91001", "Kid", "51001")
        parent = _auth(51001, "parent", self.ctx)
        s = self.ctx.client_schedule_availability_submit(parent, "91001", {
            "preferred_branch": "YC1",
            "intervals": [{"weekday": 3, "start_time": "14:00", "end_time": "15:00", "preference": "preferred"}],
        })
        self.assertTrue(s["ok"], s)
        g = self.ctx.client_schedule_availability_get(parent, "91001")
        self.assertTrue(g["ok"], g)
        self.assertTrue(g["filled"])
        self.assertEqual(g["intervals"][0]["weekday"], 3)

    def test_9_cl_code_client_sees_form_and_saves(self):
        self._cl_link("91002", "Kid2", "51002")
        parent = _auth(51002, "parent", self.ctx)
        children = self.ctx.client_children_list(parent)
        self.assertEqual(children["client_count"], 1)
        r = self.ctx.client_schedule_availability_submit(parent, "91002", {"intervals": []})
        self.assertTrue(r["ok"], r)


# ─────────────────────────────────────────────────────────────────────────────
# 10 — staff can still review answers (read-only detail + summary)
# ─────────────────────────────────────────────────────────────────────────────

class TestStaffCanStillReview(HideTestBase):
    def test_10_staff_summary_and_detail_still_visible(self):
        sys_id = self._make_system_campaign(mk_user_id="92001", child_name="Reviewable Kid", parent_tid=52001)
        detail = self.ctx.onboarding_campaign_get(self.owner, str(sys_id), {})
        self.assertTrue(detail["ok"], detail)
        self.assertEqual(len(detail["recipients"]), 1)
        summary = self.storage.get_onboarding_campaign_availability_summary(sys_id)
        self.assertIn("by_preferred_branch", summary)


# ─────────────────────────────────────────────────────────────────────────────
# 11 — real campaigns (incl. the "464 recipients" style scenario) unaffected
# ─────────────────────────────────────────────────────────────────────────────

class TestRealCampaignsUnaffected(HideTestBase):
    def test_11_real_campaign_start_close_archive_still_work(self):
        real = self._real_campaign()
        start = self.ctx.onboarding_campaign_start(self.owner, str(real["id"]))
        self.assertTrue(start["ok"], start)
        close = self.ctx.onboarding_campaign_close(self.owner, str(real["id"]))
        self.assertTrue(close["ok"], close)
        archive = self.ctx.onboarding_campaign_archive(self.owner, str(real["id"]))
        self.assertTrue(archive["ok"], archive)

    def test_11b_real_campaign_import_and_export_still_work(self):
        real = self._real_campaign()
        self.ctx.onboarding_campaign_start(self.owner, str(real["id"]))
        cache = self.ctx._onboarding_candidates_cache_dict()
        import time as _time
        cache["93001"] = (_time.time(), {"mk_user_id": "93001", "child_display_name": "Real Kid", "branch_name": "", "course_name": ""})
        imp = self.ctx.onboarding_campaign_import_recipients(self.owner, str(real["id"]), {"recipients": [{"mk_user_id": "93001"}]})
        self.assertTrue(imp["ok"], imp)
        csv_result = self.ctx.onboarding_campaign_export_csv(self.owner, str(real["id"]), {})
        self.assertIsInstance(csv_result, tuple)


# ─────────────────────────────────────────────────────────────────────────────
# Backward-compat retrofit: a pre-hotfix standalone campaign gets recognized
# ─────────────────────────────────────────────────────────────────────────────

class TestRetrofit(HideTestBase):
    def test_retrofits_pre_existing_standalone_campaign_by_name(self):
        # Simulates a DB where the standalone campaign was already created by
        # the previous commit (557c8dc), before is_system existed at all —
        # is_system defaults to 0 for it, and it has no idempotency_key match
        # scenario here since we insert it exactly like the old code did.
        legacy = self.storage.create_onboarding_campaign(
            self.storage.STANDALONE_AVAILABILITY_CAMPAIGN_NAME, academic_year="—", created_by="system",
            collect_schedule_availability=True,
        )
        self.assertTrue(legacy["ok"], legacy)
        legacy_id = legacy["campaign"]["id"]
        self.assertEqual(self.storage.get_onboarding_campaign(legacy_id)["is_system"], 0)

        resolved_id = self.storage._ensure_standalone_availability_campaign("system")
        self.assertEqual(resolved_id, legacy_id, "must reuse the existing campaign, never create a second one")
        self.assertEqual(self.storage.get_onboarding_campaign(legacy_id)["is_system"], 1)

        # And it's excluded from the list now that it's flagged.
        listed = self.storage.list_onboarding_campaigns()
        self.assertNotIn(legacy_id, [c["id"] for c in listed])


# ─────────────────────────────────────────────────────────────────────────────
# Version unchanged
# ─────────────────────────────────────────────────────────────────────────────

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"


class TestVersionUnchanged(unittest.TestCase):
    def test_version_stays_v71123(self):
        js = APP_JS.read_text(encoding="utf-8")
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('console.log("MiniApp version: v7.1.14.3");', js)
        self.assertIn("styles.css?v=7.1.14", html)
        self.assertIn("app.js?v=7.1.14", html)


if __name__ == "__main__":
    unittest.main()
