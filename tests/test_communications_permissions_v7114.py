"""Tests for v7.1.14 — staff "Рассылки" (communications center): permissions
and rollout kill switches.

Covers:
  Permissions:
    1.  owner/admin see the section (canUseCommunications / me() gate).
    2.  staff without access (teacher) / regular client do NOT.
    3.  frontend/query/body role override cannot grant access (role comes
        only from auth["user_id"] -> storage, never trusted input).
    4.  test-role substitution never grants access without a real
        owner/admin role — and never revokes it for a real owner/admin
        previewing a lower-privilege test role.

  Rollout:
    5.  all four kill switches default False (config.py).
    6.  pilot Telegram-id allowlist opens the section while the global
        flag is off.
    7.  CLIENT_COMMUNICATIONS_SEND_ENABLED=false blocks send/schedule
        mutations while draft/preview/freeze keep working.
    8.  CLIENT_COMMUNICATIONS_SCHEDULER_ENABLED=false blocks the schedule
        mutation itself (not just the worker).
    9.  a parent whose CLIENT_NOTIFICATIONS_ENABLED gate is closed is
        excluded from audience resolution, never silently reachable.

Run:
    python -m unittest tests.test_communications_permissions_v7114 -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import web_app_server as srv  # noqa: E402
from storage import Storage  # noqa: E402
from utils import now_iso  # noqa: E402

CONFIG_PY = ROOT / "config.py"


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _settings(**overrides):
    base = dict(
        client_communications_enabled=True,
        client_communications_pilot_telegram_ids=[],
        client_communications_send_enabled=True,
        client_communications_scheduler_enabled=True,
        client_notifications_enabled=True,
        client_notifications_pilot_telegram_ids=[],
        admin_ids=[],
        senior_teacher_ids=[],
        web_app_test_roles=True,
        food_module_enabled=False,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _make_ctx(storage: Storage, **settings_overrides) -> "srv.MiniAppContext":
    ctx = srv.MiniAppContext.__new__(srv.MiniAppContext)
    ctx.storage = storage
    ctx.settings = _settings(**settings_overrides)
    return ctx


def _auth(uid) -> dict:
    return {"user_id": int(uid)}


class TestSectionVisibility(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.storage.set_staff_role(1001, "owner")
        self.storage.set_staff_role(1002, "admin")
        self.storage.set_staff_role(1003, "teacher")
        self.storage.set_staff_role(1004, "client_manager")
        self.storage.set_staff_role(1005, "operations")

    def test_1_owner_and_admin_see_section(self):
        ctx = _make_ctx(self.storage)
        self.assertIsNone(ctx._require_communications_access(_auth(1001)))
        self.assertIsNone(ctx._require_communications_access(_auth(1002)))
        self.assertTrue(ctx._capabilities_for_user(1001)["canUseCommunications"])
        self.assertTrue(ctx.me(_auth(1001))["communicationsEnabled"])

    def test_2_other_staff_and_client_do_not(self):
        ctx = _make_ctx(self.storage)
        for uid in (1003, 1005, 999999):  # teacher, operations, unknown/client
            denied = ctx._require_communications_access(_auth(uid))
            self.assertIsNotNone(denied, f"uid={uid} should be denied")
            self.assertFalse(denied.get("ok"))
        self.assertFalse(ctx._capabilities_for_user(1003)["canUseCommunications"])
        self.assertFalse(ctx._capabilities_for_user(1005)["canUseCommunications"])

    def test_2b_client_manager_now_allowed_v71142(self):
        # v7.1.14.2 — client_manager gets a dedicated "Рассылки" tab
        # (replacing "Обеды"); owner/admin access is unchanged.
        ctx = _make_ctx(self.storage)
        self.assertIsNone(ctx._require_communications_access(_auth(1004)))
        self.assertTrue(ctx._capabilities_for_user(1004)["canUseCommunications"])

    def test_3_frontend_supplied_role_cannot_grant_access(self):
        # There is no code path that reads a role from body/params for this
        # gate — _require_communications_access only ever takes auth["user_id"].
        ctx = _make_ctx(self.storage)
        spoofed_auth = {"user_id": 1003, "role": "owner", "dev_user_id": 1001}
        denied = ctx._require_communications_access(spoofed_auth)
        self.assertIsNotNone(denied)

    def test_4a_test_role_cannot_grant_access_without_real_role(self):
        # Even if somehow test-mode were enabled for a non-owner/admin real
        # role (not reachable via the real _can_use_role_test gate, but
        # defended here anyway since _communications_access_allowed uses
        # _base_role_for_user, never _role_for_user).
        ctx = _make_ctx(self.storage)
        self.storage.set_staff_test_client_context = getattr(self.storage, "set_staff_test_client_context", None)
        real_role = ctx._base_role_for_user(1003)
        self.assertNotIn(real_role, srv.CLIENT_COMMUNICATIONS_ROLES)
        # Force _role_for_user to report "owner" (as if a test role were
        # active) while the REAL role stays "teacher" — access must still
        # be denied because the gate checks _base_role_for_user.
        ctx._role_for_user = lambda uid: "owner"
        self.assertIsNotNone(ctx._require_communications_access(_auth(1003)))

    def test_4b_owner_keeps_access_while_previewing_lower_role(self):
        ctx = _make_ctx(self.storage)
        ctx._role_for_user = lambda uid: "teacher"  # simulates an active test-role preview
        self.assertIsNone(ctx._require_communications_access(_auth(1001)))


class TestRolloutDefaults(unittest.TestCase):
    def test_5_defaults_false_in_config(self):
        src = CONFIG_PY.read_text(encoding="utf-8")
        for var in (
            "CLIENT_COMMUNICATIONS_ENABLED", "CLIENT_COMMUNICATIONS_SEND_ENABLED",
            "CLIENT_COMMUNICATIONS_SCHEDULER_ENABLED",
        ):
            idx = src.find(f'os.getenv("{var}"')
            self.assertNotEqual(idx, -1, f"{var} not read from env")
            line = src[idx:src.find("\n", idx)]
            self.assertIn('"false"', line)
            self.assertIn("False),", line)
        self.assertIn('os.getenv("CLIENT_COMMUNICATIONS_PILOT_TELEGRAM_IDS", "")', src)

    def test_6_pilot_allowlist_opens_section_with_global_flag_off(self):
        storage = _tmp_storage()
        storage.set_staff_role(2001, "owner")
        ctx = _make_ctx(
            storage, client_communications_enabled=False,
            client_communications_pilot_telegram_ids=[2001],
        )
        self.assertIsNone(ctx._require_communications_access(_auth(2001)))
        storage2 = _tmp_storage()
        storage2.set_staff_role(2002, "owner")
        ctx2 = _make_ctx(storage2, client_communications_enabled=False, client_communications_pilot_telegram_ids=[])
        self.assertIsNotNone(ctx2._require_communications_access(_auth(2002)))


class TestSendAndSchedulerKillSwitches(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.storage.set_staff_role(3001, "owner")
        now = now_iso()
        with self.storage._connect() as conn:
            conn.execute(
                """INSERT INTO client_parent_child_links
                   (parent_telegram_user_id, mk_user_id, child_display_name, status, linked_at, created_at, updated_at)
                   VALUES ('500001','S1','Child','active',?,?,?)""",
                (now, now, now),
            )

    def _draft_with_snapshot(self, ctx):
        campaign = ctx.communications_campaign_create(_auth(3001))["campaign"]
        ctx.communications_campaign_update(_auth(3001), str(campaign["id"]), {
            "audienceType": "all_parents", "title": "T", "body": "B",
        })
        freeze = ctx.communications_campaign_freeze(_auth(3001), str(campaign["id"]))
        self.assertTrue(freeze.get("ok"), freeze)
        return campaign["id"], freeze

    def test_7_send_disabled_blocks_send_but_not_draft_preview_freeze(self):
        ctx = _make_ctx(self.storage, client_communications_send_enabled=False)
        campaign_id, freeze = self._draft_with_snapshot(ctx)
        result = ctx.communications_campaign_send_now(_auth(3001), str(campaign_id), {
            "snapshot_hash": freeze["snapshotHash"], "recipient_count": freeze["eligibleCount"], "confirm": True,
        })
        self.assertFalse(result.get("ok"))
        self.assertIn("отключена", result.get("error", ""))
        after = ctx.communications_campaign_get(_auth(3001), str(campaign_id))
        self.assertEqual(after["campaign"]["status"], "draft")

    def test_8_scheduler_disabled_blocks_schedule_mutation(self):
        ctx = _make_ctx(self.storage, client_communications_scheduler_enabled=False)
        campaign_id, freeze = self._draft_with_snapshot(ctx)
        result = ctx.communications_campaign_schedule(_auth(3001), str(campaign_id), {
            "snapshot_hash": freeze["snapshotHash"], "recipient_count": freeze["eligibleCount"], "confirm": True,
            "date": "2099-01-01", "time": "09:00",
        })
        self.assertFalse(result.get("ok"))
        self.assertIn("Планирование", result.get("error", ""))


class TestNotificationGateExclusion(unittest.TestCase):
    def test_9_notification_disabled_parent_excluded_from_audience(self):
        storage = _tmp_storage()
        storage.set_staff_role(4001, "owner")
        now = now_iso()
        with storage._connect() as conn:
            conn.execute(
                """INSERT INTO client_parent_child_links
                   (parent_telegram_user_id, mk_user_id, child_display_name, status, linked_at, created_at, updated_at)
                   VALUES ('600001','S1','A','active',?,?,?)""",
                (now, now, now),
            )
            conn.execute(
                """INSERT INTO client_parent_child_links
                   (parent_telegram_user_id, mk_user_id, child_display_name, status, linked_at, created_at, updated_at)
                   VALUES ('600002','S2','B','active',?,?,?)""",
                (now, now, now),
            )
        # Global flag OFF, only 600001 pilot-allowlisted for notifications.
        ctx = _make_ctx(
            storage, client_notifications_enabled=False,
            client_notifications_pilot_telegram_ids=[600001],
        )
        result = ctx._resolve_audience_all_parents()
        eligible_ids = {e["parent_telegram_id"] for e in result["eligible"]}
        excluded_ids = {e["parent_telegram_id"] for e in result["excluded"]}
        self.assertIn("600001", eligible_ids)
        self.assertIn("600002", excluded_ids)
        self.assertNotIn("600002", eligible_ids)
        reason = next(e["exclusion_reason"] for e in result["excluded"] if e["parent_telegram_id"] == "600002")
        self.assertIn("уведомления", reason.lower())


if __name__ == "__main__":
    unittest.main()
