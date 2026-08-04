"""Tests for v7.1.16 — Client & Client Manager UX Stabilization.

This file intentionally covers two things the spec's 5-file/5-category
list doesn't map 1:1 (5 files were requested for 5 checklist categories —
CLIENT, CLIENT_MANAGER, MOBILE, RACES, REGRESSION — but the 5th file was
named "schedule_readiness" rather than "regression"). Given that mismatch,
REGRESSION (38-47) — proving the v7.1.16 UI-stabilization pass didn't
disturb anything v7.1.12-v7.1.15 already shipped — is kept here, since
"regression-free" is exactly the precondition the schedule-readiness audit
depends on: you can't safely build a schedule feature on top of a base that
might have silently broken CL-linking, invites, or payment automation.

Covers:
  REGRESSION (38-47):
    38. CL-code linking still works (untouched this release).
    39. Invite linking still works (untouched this release).
    40. Onboarding diagnostic events are still written (v7.1.15, unchanged).
    41. Duplicate links are still prevented (atomic claim, unchanged).
    42. Client notifications still work.
    43. Payment automation role/constant set is unchanged.
    44. Availability still saves.
    45. Communications access is unchanged.
    46. Food orders / food-only linking path is unchanged.
    47. Version marker + both cache-bust query strings bumped to v7.1.16.
  SCHEDULE READINESS (audit-only, no schedule code was added in v7.1.16):
    - No schedule/lesson/group/class table was added this release.
    - The MoyKlass client wrapper stays read-only (no booking/enrollment
      write method exists) — confirms schedule data is still fetched live,
      never mirrored locally, so v7.1.16 didn't change that risk surface.

Run:
    python -m unittest tests.test_schedule_readiness_v7116 -v
"""
from __future__ import annotations

import re
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest  # noqa: E402

import web_app_server as srv  # noqa: E402
from storage import Storage  # noqa: E402

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
MOYKLASS_SRC = (ROOT / "moyklass_client.py").read_text(encoding="utf-8")


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def _settings(**overrides):
    base = dict(
        client_cabinet_v7113_enabled=True, client_cabinet_v7113_pilot_telegram_ids=[],
        client_communications_enabled=True, client_communications_pilot_telegram_ids=[],
        client_communications_send_enabled=True, client_communications_scheduler_enabled=True,
        client_notifications_enabled=True, client_notifications_pilot_telegram_ids=[],
        admin_ids=[], senior_teacher_ids=[], web_app_test_roles=True, food_module_enabled=False,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _make_ctx(storage: Storage, **settings_overrides):
    ctx = srv.MiniAppContext.__new__(srv.MiniAppContext)
    ctx.storage = storage
    ctx.settings = _settings(**settings_overrides)
    return ctx


def _auth(uid) -> dict:
    return {"user_id": int(uid)}


class TestClLinkingUnaffected(unittest.TestCase):
    def test_38_cl_code_linking_still_works(self):
        st = _tmp_storage()
        code = st.create_client_link_code("S7001", "Child", created_by="9001")["code"]
        result = st.link_client_child("700001", code, _now())
        self.assertTrue(result.get("ok"))
        self.assertEqual(st.get_client_kind_for_parent("700001"), "regular")


class TestInviteLinkingUnaffected(unittest.TestCase):
    def test_39_invite_linking_still_works(self):
        st = _tmp_storage()
        camp = st.create_onboarding_campaign(name="B", academic_year="2026", created_by="9001")["campaign"]
        with st._connect() as conn:
            conn.execute("UPDATE client_onboarding_campaigns SET status='active' WHERE id=?", (camp["id"],))
        st.import_onboarding_campaign_recipients(camp["id"], [{"mk_user_id": "S7002", "child_display_name": "K"}], added_by="9001")
        with st._connect() as conn:
            rid = conn.execute(
                "SELECT id FROM client_onboarding_recipients WHERE campaign_id=? AND mk_user_id='S7002'", (camp["id"],)
            ).fetchone()["id"]
        invite = st.create_onboarding_invite(camp["id"], rid, "9001", "secret")
        result = st.activate_onboarding_invite(invite["invite_id"], invite["signature"], "700002", "secret")
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("mk_user_id"), "S7002")


class TestOnboardingEventsStillWritten(unittest.TestCase):
    def test_40_onboarding_events_still_logged(self):
        st = _tmp_storage()
        st.log_onboarding_event("link_created", "cl_code", "succeeded", mk_user_id="S7003")
        with st._connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM client_onboarding_events").fetchone()[0]
        self.assertEqual(n, 1)


class TestDuplicateLinksStillPrevented(unittest.TestCase):
    def test_41_repeat_code_submit_does_not_duplicate(self):
        st = _tmp_storage()
        code = st.create_client_link_code("S7004", "Child", created_by="9001")["code"]
        st.link_client_child("700004", code, _now())
        second = st.link_client_child("700004", code, _now())
        self.assertTrue(second.get("ok"))
        self.assertTrue(second.get("already_linked"))
        with st._connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM client_parent_child_links WHERE mk_user_id='S7004' AND status='active'"
            ).fetchone()[0]
        self.assertEqual(n, 1)


class TestNotificationsUnaffected(unittest.TestCase):
    def test_42_client_notifications_still_work(self):
        st = _tmp_storage()
        st.set_staff_role(1001, "owner")
        now = _now()
        with st._connect() as conn:
            conn.execute(
                """INSERT INTO client_parent_child_links
                   (parent_telegram_user_id, mk_user_id, child_display_name, status, linked_at, created_at, updated_at)
                   VALUES ('700005','S7005','Child','active',?,?,?)""",
                (now, now, now),
            )
        st.create_client_notification(
            title="Тест", body="Тест", category="general", priority="normal",
            scope="family", mk_user_id="S7005", action_key="none",
            created_by_telegram_id="1001", recipient_telegram_ids=["700005"],
        )
        ctx = _make_ctx(st, client_notifications_enabled=True)
        st.set_staff_role(700005, "parent")
        result = ctx.client_notifications_list(_auth(700005), {})
        self.assertTrue(result.get("ok"))
        self.assertGreaterEqual(len(result.get("notifications") or []), 1)


class TestPaymentAutomationUnaffected(unittest.TestCase):
    def test_43_payment_automation_roles_unchanged(self):
        self.assertEqual(srv.PAYMENT_ONBOARDING_STAFF_ROLES, {"owner", "admin", "client_manager"})
        self.assertEqual(srv.CLIENT_ONBOARDING_CAMPAIGN_ROLES, {"owner", "admin", "client_manager"})


class TestAvailabilityUnaffected(unittest.TestCase):
    def test_44_availability_still_saves(self):
        st = _tmp_storage()
        camp = st.create_onboarding_campaign(name="C", academic_year="2026", created_by="9001")["campaign"]
        st.import_onboarding_campaign_recipients(camp["id"], [{"mk_user_id": "S7006", "child_display_name": "K"}], added_by="9001")
        with st._connect() as conn:
            rid = conn.execute(
                "SELECT id FROM client_onboarding_recipients WHERE campaign_id=? AND mk_user_id='S7006'", (camp["id"],)
            ).fetchone()["id"]
        result = st.submit_schedule_availability(
            rid, "700006", "parent", preferred_branch="YC1", available_from="2026-09-01",
            intervals=[{"weekday": 2, "start_time": "10:00", "end_time": "12:00", "preference": "preferred"}],
            source="parent_app",
        )
        self.assertTrue(result.get("ok"))
        self.assertTrue(st.get_availability_status_for_mk_user("S7006").get("filled"))


class TestCommunicationsUnaffected(unittest.TestCase):
    def test_45_communications_access_unchanged(self):
        st = _tmp_storage()
        st.set_staff_role(4001, "owner")
        st.set_staff_role(4003, "client_manager")
        ctx = _make_ctx(st, client_communications_enabled=True)
        for uid in (4001, 4003):
            self.assertTrue(ctx._capabilities_for_user(uid)["canUseCommunications"])


class TestFoodOrdersUnaffected(unittest.TestCase):
    def test_46_food_only_link_path_still_uses_old_table(self):
        st = _tmp_storage()
        code = st.get_or_create_link_code_for_student("F7001")
        result = st.link_parent_to_child("700007", code)
        self.assertTrue(result.get("ok"))
        with st._connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM parent_child_links WHERE mk_student_id='F7001'").fetchone()[0]
            n2 = conn.execute("SELECT COUNT(*) FROM client_parent_child_links WHERE mk_user_id='F7001'").fetchone()[0]
        self.assertEqual(n, 1)
        self.assertEqual(n2, 0)  # food-only never touches the regular-cabinet link table

    def test_46b_old_food_ui_still_present(self):
        self.assertIn('id="parentLinkCodeInput"', APP_JS)
        self.assertIn('id="parentLinkBtn"', APP_JS)
        self.assertIn("async function linkChild()", APP_JS)


class TestVersionBump(unittest.TestCase):
    def test_47_version_and_cache_bust(self):
        self.assertIn('console.log("MiniApp version: v7.1.16");', APP_JS)
        self.assertIn("styles.css?v=7.1.16", INDEX_HTML)
        self.assertIn("app.js?v=7.1.16", INDEX_HTML)


class TestScheduleReadinessAudit(unittest.TestCase):
    """No schedule feature was built in v7.1.16 — this only confirms the
    audit's starting-state claims still hold after the UI-stabilization
    changes, so the next release's schedule-readiness map is trustworthy."""

    def test_no_new_schedule_lesson_class_table_added(self):
        st = _tmp_storage()
        with st._connect() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        for forbidden in ("schedule", "lessons_local", "classes_local", "groups_local", "timetable"):
            self.assertNotIn(forbidden, tables)
        # the only genuinely new table this release added is the frontend
        # incident log — confirms v7.1.16 stayed additive/UI-only as scoped
        self.assertIn("frontend_incidents", tables)

    def test_moyklass_client_still_read_only(self):
        self.assertNotRegex(MOYKLASS_SRC, r"def (book|create_enrollment|enroll|create_lesson|add_to_class)\(")

    def test_client_schedule_availability_bridge_unchanged(self):
        self.assertIn("def get_or_create_recipient_for_client", Path(ROOT / "storage.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
