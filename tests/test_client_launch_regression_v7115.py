"""Tests for v7.1.15 — core regression guard: launch-readiness instrumentation
(client_onboarding_events logging, the "Подключения" dashboard, health
checks) must not have disturbed any of the features already shipped and
verified in v7.1.13/v7.1.14/v7.1.14.1-3.

Covers:
  28. Client notifications still work (gate + delivery path untouched).
  29. Payments/payment-onboarding roles and endpoints are unchanged.
  30. Availability still saves (submit_schedule_availability unaffected).
  31. The selected branch button is still visibly highlighted (v7.1.14.3).
  32. The top safe-area spacer model is still intact (v7.1.14.3).
  33. Communications ("Рассылки") access is unaffected.
  34. client_manager navigation (Рассылки tab, no "Назад в Админ") still works.
  35. The old food-only UI (YC- code form) is still present, untouched.

Run:
    python -m unittest tests.test_client_launch_regression_v7115 -v
"""
from __future__ import annotations

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
STYLES_CSS = (ROOT / "miniapp" / "styles.css").read_text(encoding="utf-8")


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


class TestNotificationsUnaffected(unittest.TestCase):
    def test_28_client_notifications_gate_and_delivery_unaffected(self):
        st = _tmp_storage()
        st.set_staff_role(1001, "owner")
        now = _now()
        with st._connect() as conn:
            conn.execute(
                """INSERT INTO client_parent_child_links
                   (parent_telegram_user_id, mk_user_id, child_display_name, status, linked_at, created_at, updated_at)
                   VALUES ('100001','S1','Child','active',?,?,?)""",
                (now, now, now),
            )
        st.create_client_notification(
            title="Тест", body="Тест", category="general", priority="normal",
            scope="family", mk_user_id="S1", action_key="none",
            created_by_telegram_id="1001", recipient_telegram_ids=["100001"],
        )
        ctx = _make_ctx(st, client_notifications_enabled=True)
        st.set_staff_role(100001, "parent")
        result = ctx.client_notifications_list(_auth(100001), {})
        self.assertTrue(result.get("ok"))
        self.assertFalse(result.get("disabled"))
        self.assertGreaterEqual(len(result.get("notifications") or []), 1)


class TestPaymentsUnaffected(unittest.TestCase):
    def test_29_payment_onboarding_roles_unchanged(self):
        self.assertEqual(srv.PAYMENT_ONBOARDING_STAFF_ROLES, {"owner", "admin", "client_manager"})
        self.assertEqual(srv.CLIENT_ONBOARDING_CAMPAIGN_ROLES, {"owner", "admin", "client_manager"})

    def test_29b_client_admin_link_and_enroll_still_never_rolls_back_link_on_pilot_failure(self):
        st = _tmp_storage()
        st.set_staff_role(2001, "owner")
        code = st.create_client_link_code("S2001", "Child", created_by="2001")["code"]
        ctx = _make_ctx(st)
        result = ctx.client_admin_link_and_enroll(_auth(2001), {"parent_telegram_user_id": "200002", "code": code})
        self.assertTrue(result.get("ok"))
        self.assertIn("payment_automation", result)
        self.assertEqual(st.get_client_kind_for_parent("200002"), "regular")


class TestAvailabilityUnaffected(unittest.TestCase):
    def test_30_availability_still_saves(self):
        st = _tmp_storage()
        camp = st.create_onboarding_campaign(name="A", academic_year="2026", created_by="9001")["campaign"]
        with st._connect() as conn:
            conn.execute("UPDATE client_onboarding_campaigns SET status='active' WHERE id=?", (camp["id"],))
        st.import_onboarding_campaign_recipients(camp["id"], [{"mk_user_id": "SAV1", "child_display_name": "Kid"}], added_by="9001")
        with st._connect() as conn:
            rid = conn.execute(
                "SELECT id FROM client_onboarding_recipients WHERE campaign_id=? AND mk_user_id='SAV1'", (camp["id"],)
            ).fetchone()["id"]
        result = st.submit_schedule_availability(
            rid, "300001", "parent", preferred_branch="YC1", available_from="2026-09-01",
            intervals=[{"weekday": 1, "start_time": "10:00", "end_time": "12:00", "preference": "preferred"}],
            source="parent_app",
        )
        self.assertTrue(result.get("ok"))
        status = st.get_availability_status_for_mk_user("SAV1")
        self.assertTrue(status.get("filled"))


class TestSelectedBranchStillVisible(unittest.TestCase):
    def test_31_selected_branch_css_still_important(self):
        idx = STYLES_CSS.find(".ws-oc-ttl-btn.active {")
        self.assertNotEqual(idx, -1)
        line = STYLES_CSS[idx:STYLES_CSS.find("\n", idx)]
        self.assertIn("!important", line)


class TestSafeAreaStillIntact(unittest.TestCase):
    def test_32_top_safe_area_spacer_still_present(self):
        self.assertIn('id="appTopSafeSpacer"', INDEX_HTML)
        self.assertIn(
            "body.is-telegram-webapp .app-top-safe-spacer {\n  height: var(--app-top-safe-offset);\n}",
            STYLES_CSS,
        )
        self.assertIn("IOS_TELEGRAM_MIN_TOP_SAFE_AREA_PX", APP_JS)


class TestCommunicationsUnaffected(unittest.TestCase):
    def test_33_owner_admin_client_manager_keep_communications_access(self):
        st = _tmp_storage()
        st.set_staff_role(4001, "owner")
        st.set_staff_role(4002, "admin")
        st.set_staff_role(4003, "client_manager")
        ctx = _make_ctx(st, client_communications_enabled=True)
        for uid in (4001, 4002, 4003):
            self.assertTrue(ctx._capabilities_for_user(uid)["canUseCommunications"])


class TestClientManagerNavigationUnaffected(unittest.TestCase):
    def test_34_client_manager_no_back_to_admin_owner_admin_keep_it(self):
        self.assertIn(
            'function canReturnToAdminFromComms() {\n  const realRole = state.me?.realRole || "";\n'
            '  return realRole === "owner" || realRole === "admin";\n}',
            APP_JS,
        )
        self.assertIn('"client_manager"', APP_JS[APP_JS.find("Staff lunch tab: show for ALL staff"):APP_JS.find("Staff lunch tab: show for ALL staff") + 400])


class TestFoodOnlyUiUnaffected(unittest.TestCase):
    def test_35_old_food_link_form_still_present(self):
        self.assertIn('id="parentLinkCodeInput"', APP_JS)
        self.assertIn('id="parentLinkBtn"', APP_JS)
        self.assertIn("YC-XXXX", APP_JS)
        self.assertIn("async function linkChild()", APP_JS)


if __name__ == "__main__":
    unittest.main()
