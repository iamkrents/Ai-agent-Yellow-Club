"""Tests for v7.1.14 — staff "Рассылки": audience snapshot integrity and
the send step's idempotency.

Covers:
  21. backend snapshot/count is the source of truth (frontend-supplied
      recipient_count is only ever compared against it, never trusted).
  22. a draft with no frozen snapshot cannot be sent.
  23. changing audience_type/audience_config/scope after freezing
      invalidates the snapshot (must be re-frozen before send/schedule).
  24. an exact recipient_count mismatch is rejected even with a correct hash.
  25. the frozen recipient snapshot is immutable — freezing again replaces
      it explicitly, nothing else silently rewrites it.
  26. calling send twice on the same campaign_id never sends twice.
  27. a duplicate/retried send never creates a second client_notification row.
  28. one family notification reaches a two-children parent exactly once.
  29. the existing client notification center (list/get/mark-read) keeps
      working unchanged on a campaign-sent notification.
  30. a per-recipient write failure is recorded and yields status='partial'.
  31. resuming a 'sending' campaign only touches still-pending recipients.
  32. an arbitrary/non-whitelisted action_key is rejected at draft-update time.
  33. title/body containing HTML/script markup is stored and returned
      verbatim (no server-side execution; escaping is a display-time
      concern already covered by the existing client notification center).
  34. a sent campaign can no longer be edited or deleted.

Run:
    python -m unittest tests.test_communications_snapshot_send_v7114 -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import web_app_server as srv  # noqa: E402
from storage import Storage  # noqa: E402
from utils import now_iso  # noqa: E402


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


class _Base(unittest.TestCase):
    OWNER = 9001

    def setUp(self):
        self.storage = _tmp_storage()
        self.storage.set_staff_role(self.OWNER, "owner")
        _link(self.storage, "910001", "SN1", "Ребёнок")
        self.ctx = _make_ctx(self.storage)

    def _new_draft(self, *, title="Заголовок", body="Текст"):
        campaign = self.ctx.communications_campaign_create(_auth(self.OWNER))["campaign"]
        self.ctx.communications_campaign_update(_auth(self.OWNER), str(campaign["id"]), {
            "audienceType": "all_parents", "title": title, "body": body,
        })
        return campaign["id"]

    def _freeze(self, campaign_id):
        return self.ctx.communications_campaign_freeze(_auth(self.OWNER), str(campaign_id))


class TestSnapshotIntegrity(unittest.TestCase):
    def setUp(self):
        self.OWNER = 9001
        self.storage = _tmp_storage()
        self.storage.set_staff_role(self.OWNER, "owner")
        _link(self.storage, "910001", "SN1")
        self.ctx = _make_ctx(self.storage)
        campaign = self.ctx.communications_campaign_create(_auth(self.OWNER))["campaign"]
        self.campaign_id = campaign["id"]
        self.ctx.communications_campaign_update(_auth(self.OWNER), str(self.campaign_id), {
            "audienceType": "all_parents", "title": "T", "body": "B",
        })

    def test_22_unfrozen_draft_cannot_be_sent(self):
        result = self.ctx.communications_campaign_send_now(_auth(self.OWNER), str(self.campaign_id), {
            "snapshot_hash": "whatever", "recipient_count": 1, "confirm": True,
        })
        self.assertFalse(result["ok"])
        self.assertIn("рассчитайте", result["error"])
        self.assertEqual(result["error_code"], "stale_snapshot")

    def test_21_and_24_count_mismatch_rejected_even_with_hash(self):
        freeze = self.ctx.communications_campaign_freeze(_auth(self.OWNER), str(self.campaign_id))
        self.assertTrue(freeze["ok"])
        result = self.ctx.communications_campaign_send_now(_auth(self.OWNER), str(self.campaign_id), {
            "snapshot_hash": freeze["snapshotHash"], "recipient_count": freeze["eligibleCount"] + 5, "confirm": True,
        })
        self.assertFalse(result["ok"])
        self.assertIn("изменилось", result["error"])
        self.assertEqual(result["error_code"], "count_mismatch")

    def test_stale_snapshot_hash_mismatch_error_code(self):
        freeze = self.ctx.communications_campaign_freeze(_auth(self.OWNER), str(self.campaign_id))
        result = self.ctx.communications_campaign_send_now(_auth(self.OWNER), str(self.campaign_id), {
            "snapshot_hash": "not-the-real-hash", "recipient_count": freeze["eligibleCount"], "confirm": True,
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "stale_snapshot")

    def test_send_disabled_error_code(self):
        ctx = _make_ctx(self.storage, client_communications_send_enabled=False)
        freeze = ctx.communications_campaign_freeze(_auth(self.OWNER), str(self.campaign_id))
        result = ctx.communications_campaign_send_now(_auth(self.OWNER), str(self.campaign_id), {
            "snapshot_hash": freeze["snapshotHash"], "recipient_count": freeze["eligibleCount"], "confirm": True,
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "send_disabled")

    def test_scheduler_disabled_error_code(self):
        ctx = _make_ctx(self.storage, client_communications_scheduler_enabled=False)
        freeze = ctx.communications_campaign_freeze(_auth(self.OWNER), str(self.campaign_id))
        result = ctx.communications_campaign_schedule(_auth(self.OWNER), str(self.campaign_id), {
            "snapshot_hash": freeze["snapshotHash"], "recipient_count": freeze["eligibleCount"], "confirm": True,
            "date": "2099-09-03", "time": "09:00",
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "scheduler_disabled")

    def test_23_changing_audience_invalidates_snapshot(self):
        freeze = self.ctx.communications_campaign_freeze(_auth(self.OWNER), str(self.campaign_id))
        self.assertIsNotNone(freeze["campaign"]["snapshotHash"])
        _link(self.storage, "910002", "SN2")
        self.ctx.communications_campaign_update(_auth(self.OWNER), str(self.campaign_id), {
            "audienceType": "all_parents",  # same type, but touches audience_type -> invalidate path
        })
        after = self.ctx.communications_campaign_get(_auth(self.OWNER), str(self.campaign_id))["campaign"]
        self.assertIsNone(after["snapshotHash"])
        self.assertIsNone(after["eligibleCount"])

    def test_25_refreezing_replaces_snapshot_explicitly(self):
        freeze1 = self.ctx.communications_campaign_freeze(_auth(self.OWNER), str(self.campaign_id))
        self.assertEqual(freeze1["eligibleCount"], 1)
        _link(self.storage, "910002", "SN2")
        # A second explicit freeze call (not any incidental write) picks up the new parent.
        freeze2 = self.ctx.communications_campaign_freeze(_auth(self.OWNER), str(self.campaign_id))
        self.assertEqual(freeze2["eligibleCount"], 2)
        recipients = self.storage.list_staff_communication_recipients(self.campaign_id)
        self.assertEqual(len(recipients), 2)

    def test_32_arbitrary_action_key_rejected(self):
        result = self.ctx.communications_campaign_update(_auth(self.OWNER), str(self.campaign_id), {
            "actionKey": "javascript:alert(1)",
        })
        self.assertFalse(result["ok"])

    def test_33_html_script_stored_and_returned_verbatim(self):
        payload = "<script>alert(1)</script>"
        self.ctx.communications_campaign_update(_auth(self.OWNER), str(self.campaign_id), {"body": payload})
        got = self.ctx.communications_campaign_get(_auth(self.OWNER), str(self.campaign_id))["campaign"]
        self.assertEqual(got["body"], payload)  # stored as literal text, not stripped/executed


class TestSendIdempotencyAndDelivery(_Base):
    def test_26_and_27_repeat_send_never_duplicates(self):
        campaign_id = self._new_draft()
        freeze = self._freeze(campaign_id)
        send1 = self.ctx.communications_campaign_send_now(_auth(self.OWNER), str(campaign_id), {
            "snapshot_hash": freeze["snapshotHash"], "recipient_count": freeze["eligibleCount"], "confirm": True,
        })
        self.assertTrue(send1["ok"])
        self.assertEqual(send1["createdCount"], 1)
        send2 = self.ctx.communications_campaign_send_now(_auth(self.OWNER), str(campaign_id), {
            "snapshot_hash": freeze["snapshotHash"], "recipient_count": freeze["eligibleCount"], "confirm": True,
        })
        self.assertFalse(send2["ok"])  # status is no longer draft -> rejected, not re-sent
        with self.storage._connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM client_notifications").fetchone()["c"]
        self.assertEqual(count, 1)

    def test_28_two_children_parent_gets_one_notification(self):
        _link(self.storage, "910001", "SN1B", "Второй ребёнок того же родителя")
        campaign_id = self._new_draft()
        freeze = self._freeze(campaign_id)
        self.assertEqual(freeze["eligibleCount"], 1)  # deduped to one parent
        send = self.ctx.communications_campaign_send_now(_auth(self.OWNER), str(campaign_id), {
            "snapshot_hash": freeze["snapshotHash"], "recipient_count": freeze["eligibleCount"], "confirm": True,
        })
        self.assertTrue(send["ok"])
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) AS c FROM client_notification_recipients WHERE recipient_telegram_id='910001'"
            ).fetchone()
        self.assertEqual(rows["c"], 1)

    def test_29_notification_center_read_unread_still_works(self):
        campaign_id = self._new_draft()
        freeze = self._freeze(campaign_id)
        self.ctx.communications_campaign_send_now(_auth(self.OWNER), str(campaign_id), {
            "snapshot_hash": freeze["snapshotHash"], "recipient_count": freeze["eligibleCount"], "confirm": True,
        })
        items, has_more = self.storage.list_client_notifications_for_recipient("910001")
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["read_at"])
        ok = self.storage.mark_client_notification_read(items[0]["id"], "910001")
        self.assertTrue(ok)
        items2, _ = self.storage.list_client_notifications_for_recipient("910001")
        self.assertIsNotNone(items2[0]["read_at"])

    def test_30_partial_failure_recorded(self):
        _link(self.storage, "910002", "SN2", "Второй родитель")
        campaign_id = self._new_draft()
        freeze = self._freeze(campaign_id)
        self.assertEqual(freeze["eligibleCount"], 2)
        original = self.storage.add_client_notification_recipient
        calls = {"n": 0}

        def flaky(notification_id, recipient_telegram_id):
            calls["n"] += 1
            if recipient_telegram_id == "910002":
                raise RuntimeError("simulated write error")
            return original(notification_id, recipient_telegram_id)

        with mock.patch.object(self.storage, "add_client_notification_recipient", side_effect=flaky):
            self.ctx.storage.claim_staff_communication_campaign(campaign_id, ("draft",), "sending", now_iso())
            outcome = self.ctx._communications_execute_send(campaign_id)
        self.assertEqual(outcome["campaign"]["status"], "partial")
        self.assertEqual(outcome["counts"]["createdCount"], 1)
        self.assertEqual(outcome["counts"]["failedCount"], 1)

    def test_31_resume_only_touches_pending_recipients(self):
        _link(self.storage, "910002", "SN2", "Второй родитель")
        campaign_id = self._new_draft()
        freeze = self._freeze(campaign_id)
        self.assertEqual(freeze["eligibleCount"], 2)
        # Simulate a crash mid-send: notification already created, one
        # recipient already 'created', the other still 'pending'.
        notification_id = self.storage.create_client_notification_message(
            title="T", body="B", category="general", priority="normal", scope="family",
            mk_user_id=None, action_key="none", created_by_telegram_id=str(self.OWNER),
        )
        self.storage.set_staff_communication_client_notification_id(campaign_id, notification_id)
        recipients = self.storage.list_staff_communication_recipients(campaign_id, eligibility_status="eligible")
        already_done = recipients[0]
        still_pending = recipients[1]
        recip_row_id = self.storage.add_client_notification_recipient(notification_id, already_done["parent_telegram_id"])
        self.storage.mark_staff_communication_recipient_delivery(already_done["id"], "created", client_notification_recipient_id=recip_row_id)
        with self.storage._connect() as conn:
            conn.execute("UPDATE staff_communication_campaigns SET status='sending' WHERE id=?", (campaign_id,))

        with mock.patch.object(self.storage, "add_client_notification_recipient", wraps=self.storage.add_client_notification_recipient) as spy:
            outcome = self.ctx._communications_execute_send(campaign_id)
            spy.assert_called_once_with(notification_id, still_pending["parent_telegram_id"])
        self.assertEqual(outcome["campaign"]["status"], "sent")
        with self.storage._connect() as conn:
            total_notifications = conn.execute("SELECT COUNT(*) AS c FROM client_notifications").fetchone()["c"]
        self.assertEqual(total_notifications, 1)  # never created a second one on resume

    def test_34_sent_campaign_cannot_be_edited_or_deleted(self):
        campaign_id = self._new_draft()
        freeze = self._freeze(campaign_id)
        self.ctx.communications_campaign_send_now(_auth(self.OWNER), str(campaign_id), {
            "snapshot_hash": freeze["snapshotHash"], "recipient_count": freeze["eligibleCount"], "confirm": True,
        })
        upd = self.ctx.communications_campaign_update(_auth(self.OWNER), str(campaign_id), {"title": "Изменено"})
        self.assertFalse(upd["ok"])
        deleted = self.ctx.communications_campaign_delete(_auth(self.OWNER), str(campaign_id))
        self.assertFalse(deleted["ok"])


if __name__ == "__main__":
    unittest.main()
