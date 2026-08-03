"""Tests for v7.1.14 — staff "Рассылки": scheduling, the background
worker, and cancellation.

Covers:
  35. Europe/Minsk -> UTC conversion is correct (UTC+3, no DST).
  36. a past date/time is rejected.
  37. claiming a due scheduled campaign (scheduled -> sending) is atomic —
      a second claim attempt on the same campaign_id never succeeds.
  38. a restart-simulated resume of a 'sending' campaign whose notification
      and all recipients were already created is a safe no-op — it does
      not create a second notification and correctly finalizes to 'sent'.
  39. cancelling a scheduled campaign works, and a cancelled campaign can
      no longer be sent.
  40. the scheduler thread's cycle body only runs when
      CLIENT_COMMUNICATIONS_SCHEDULER_ENABLED is true (source-level check,
      no real thread/sleep — avoids flaky timing-based tests).

Run:
    python -m unittest tests.test_communications_schedule_v7114 -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import web_app_server as srv  # noqa: E402
from storage import Storage  # noqa: E402
from utils import now_iso  # noqa: E402

SERVER_PY = ROOT / "web_app_server.py"


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _settings(**overrides):
    base = dict(
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


def _link(storage: Storage, parent_tid: str, mk_user_id: str, name: str = "Child") -> None:
    now = now_iso()
    with storage._connect() as conn:
        conn.execute(
            """INSERT INTO client_parent_child_links
               (parent_telegram_user_id, mk_user_id, child_display_name, status, linked_at, created_at, updated_at)
               VALUES (?,?,?,'active',?,?,?)""",
            (parent_tid, mk_user_id, name, now, now, now),
        )


class TestTimezoneConversion(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def test_35_minsk_to_utc_is_minus_3_hours(self):
        utc_str, err = self.ctx._communications_minsk_to_utc("2099-09-03", "09:00")
        self.assertIsNone(err)
        dt = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertEqual(dt.hour, 6)  # 09:00 Minsk (UTC+3) == 06:00 UTC
        self.assertEqual(dt.month, 9)
        self.assertEqual(dt.day, 3)

    def test_35b_label_round_trips_back_to_minsk_time(self):
        utc_str, _ = self.ctx._communications_minsk_to_utc("2099-09-03", "09:00")
        label = self.ctx._communications_format_minsk_label(utc_str)
        self.assertIn("09:00", label)
        self.assertIn("сентября 2099", label)
        self.assertIn("Минска", label)

    def test_36_past_date_rejected(self):
        utc_str, err = self.ctx._communications_minsk_to_utc("2020-01-01", "09:00")
        self.assertIsNone(utc_str)
        self.assertIn("прошедшее", err)

    def test_36b_empty_date_or_time_rejected(self):
        _, err1 = self.ctx._communications_minsk_to_utc("", "09:00")
        self.assertIsNotNone(err1)
        _, err2 = self.ctx._communications_minsk_to_utc("2099-01-01", "")
        self.assertIsNotNone(err2)


class TestScheduleFlow(unittest.TestCase):
    OWNER = 9101

    def setUp(self):
        self.storage = _tmp_storage()
        self.storage.set_staff_role(self.OWNER, "owner")
        _link(self.storage, "920001", "SC1")
        self.ctx = _make_ctx(self.storage)
        campaign = self.ctx.communications_campaign_create(_auth(self.OWNER))["campaign"]
        self.campaign_id = campaign["id"]
        self.ctx.communications_campaign_update(_auth(self.OWNER), str(self.campaign_id), {
            "audienceType": "all_parents", "title": "T", "body": "B",
        })
        self.freeze = self.ctx.communications_campaign_freeze(_auth(self.OWNER), str(self.campaign_id))

    def _schedule_body(self, date="2099-09-03", time="09:00"):
        return {
            "snapshot_hash": self.freeze["snapshotHash"], "recipient_count": self.freeze["eligibleCount"],
            "confirm": True, "date": date, "time": time,
        }

    def test_schedule_sets_status_and_utc_field(self):
        result = self.ctx.communications_campaign_schedule(_auth(self.OWNER), str(self.campaign_id), self._schedule_body())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["campaign"]["status"], "scheduled")
        self.assertIsNotNone(result["campaign"]["scheduledAtUtc"])

    def test_37_claim_is_atomic(self):
        self.ctx.communications_campaign_schedule(_auth(self.OWNER), str(self.campaign_id), self._schedule_body())
        now = now_iso()
        first = self.storage.claim_staff_communication_campaign(self.campaign_id, ("scheduled",), "sending", now)
        second = self.storage.claim_staff_communication_campaign(self.campaign_id, ("scheduled",), "sending", now)
        self.assertTrue(first)
        self.assertFalse(second)  # already moved past 'scheduled' -> second claim finds no matching row

    def test_38_resume_after_restart_all_already_created_is_safe_noop(self):
        self.ctx.communications_campaign_schedule(_auth(self.OWNER), str(self.campaign_id), self._schedule_body())
        self.storage.claim_staff_communication_campaign(self.campaign_id, ("scheduled",), "sending", now_iso())
        # First (real) run creates the notification and delivers to the one recipient.
        outcome1 = self.ctx._communications_execute_send(self.campaign_id)
        self.assertEqual(outcome1["campaign"]["status"], "sent")
        notification_id_1 = outcome1["campaign"]["client_notification_id"]
        # Simulate the process restarting mid-flight by re-invoking the exact
        # same idempotent step again (nothing left pending).
        outcome2 = self.ctx._communications_execute_send(self.campaign_id)
        self.assertEqual(outcome2["campaign"]["client_notification_id"], notification_id_1)
        with self.storage._connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS c FROM client_notifications").fetchone()["c"]
        self.assertEqual(total, 1)

    def test_39_cancel_scheduled_then_cannot_send(self):
        self.ctx.communications_campaign_schedule(_auth(self.OWNER), str(self.campaign_id), self._schedule_body())
        cancel = self.ctx.communications_campaign_cancel(_auth(self.OWNER), str(self.campaign_id))
        self.assertTrue(cancel["ok"])
        self.assertEqual(cancel["campaign"]["status"], "cancelled")
        self.assertIsNotNone(cancel["campaign"]["cancelledAt"])
        send = self.ctx.communications_campaign_send_now(_auth(self.OWNER), str(self.campaign_id), self._schedule_body())
        self.assertFalse(send["ok"])

    def test_39b_cancel_only_works_on_scheduled(self):
        # Still a draft (never scheduled) -> cancel is a no-op error.
        campaign = self.ctx.communications_campaign_create(_auth(self.OWNER))["campaign"]
        cancel = self.ctx.communications_campaign_cancel(_auth(self.OWNER), str(campaign["id"]))
        self.assertFalse(cancel["ok"])


class TestSchedulerKillSwitchGating(unittest.TestCase):
    def test_40_loop_checks_kill_switch_before_running_cycle(self):
        src = SERVER_PY.read_text(encoding="utf-8")
        idx = src.find("class StaffCommunicationsScheduler:")
        self.assertNotEqual(idx, -1)
        loop_idx = src.find("def _loop(self)", idx)
        cycle_def_idx = src.find("def _run_cycle(self)", idx)
        loop_body = src[loop_idx:cycle_def_idx]
        self.assertIn("client_communications_scheduler_enabled", loop_body)
        # The gate must wrap the _run_cycle() call, not just exist somewhere nearby.
        gate_idx = loop_body.find("client_communications_scheduler_enabled")
        call_idx = loop_body.find("self._run_cycle()")
        self.assertNotEqual(call_idx, -1)
        self.assertLess(gate_idx, call_idx)

    def test_40b_worker_always_starts_kill_switch_checked_internally(self):
        src = SERVER_PY.read_text(encoding="utf-8")
        idx = src.find("StaffCommunicationsScheduler(CTX).start()")
        self.assertNotEqual(idx, -1, "scheduler must always start (kill switch checked per-cycle, not at startup)")


if __name__ == "__main__":
    unittest.main()
