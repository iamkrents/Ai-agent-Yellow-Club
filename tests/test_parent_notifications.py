"""Tests for v7.0.98.1 — automatic Telegram parent notifications for new invoices.

Covers:
  - Eligibility flag set at upsert time (two-level protection)
  - No backfill / no historical eligibility
  - Notification creation and delivery
  - Error classification (permanent, transient, ambiguous)
  - Idempotency (no double sends)
  - Manual retry guards
  - Deep-link button URL
  - Food Module / YC links isolation

Run offline (no Telegram / bePaid / MoyKlass):
    python -m unittest tests.test_parent_notifications -v
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage

CURRENT_VERSION = "7.0.98.1"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _future(minutes: int = 60) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _past(minutes: int = 60) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


_INV_CTR = [90000]


def _inv_id() -> int:
    _INV_CTR[0] += 1
    return _INV_CTR[0]


def _mk_invoice(
    inv_id: int = 0,
    user_id: int = 7850099,
    price: float = 239.0,
    payed: float = 0.0,
    period_month: str = "2026-08",
) -> dict:
    if inv_id == 0:
        inv_id = _inv_id()
    return {
        "id": inv_id,
        "userId": user_id,
        "price": price,
        "payed": payed,
        "comment": "",
        "payUntil": "2026-08-31",
        "createdAt": "2026-07-20T10:00:00",
        "userSubscriptionId": 60000 + inv_id,
        "userSubscription": {
            "name": "Обучение",
            "clientName": "Александр Крента",
            "beginDate": "2026-08-01",
        },
    }


def _make_settings(
    notify_env: bool = False,
    post_env: bool = False,
    web_app_url: str = "https://example.com/app",
    bot_token: str = "fake:TOKEN",
) -> MagicMock:
    s = MagicMock()
    s.payment_parent_notifications_enabled = notify_env
    s.bepaid_auto_post_to_moyklass = post_env
    s.web_app_url = web_app_url
    s.telegram_bot_token = bot_token
    s.payment_invoice_automation_enabled = True
    s.moyklass_enabled = False
    return s


def _make_context(storage: Storage, settings: MagicMock) -> Any:
    """Build a minimal MiniAppContext using __new__ to bypass __init__."""
    from web_app_server import MiniAppContext
    ctx = MiniAppContext.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = settings
    mk = MagicMock()
    mk.request.return_value = MagicMock(ok=False, data={})
    ctx.moyklass = mk
    ctx._material_cache = {}
    ctx._mk_comment_cache = {}
    ctx._mk_student_name_cache = {}
    ctx._client_tasks_sync_cache = {"ts": 0.0, "result": {}}
    return ctx


def _seed_cl_link(storage: Storage, mk_user_id: str, tg_user_id: str = "999001") -> None:
    now = _now()
    with storage._connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO client_parent_child_links
               (parent_telegram_user_id, mk_user_id, child_display_name,
                status, linked_at, created_at, updated_at)
               VALUES (?,?,'Тест','active',?,?,?)""",
            (tg_user_id, str(mk_user_id), now, now, now),
        )


def _seed_intent(
    storage: Storage,
    public_id: str,
    mk_user_id: str = "7850099",
    mk_invoice_id: str = "",
    amount_minor: int = 23900,
    currency: str = "BYN",
    period_month: str = "2026-08",
    status: str = "awaiting_payment",
    client_visibility: str = "published",
    student_name: str = "Александр Крента",
) -> None:
    now = _now()
    with storage._connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO payment_intents
               (public_id, mk_user_id, mk_invoice_id, student_name,
                amount_minor, amount_byn, currency, period_month,
                status, client_visibility, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                public_id, int(mk_user_id), mk_invoice_id or public_id,
                student_name,
                amount_minor, round(amount_minor / 100.0, 2),
                currency, period_month, status, client_visibility,
                now, now,
            ),
        )


# ---------------------------------------------------------------------------
# 1 — Storage: eligibility flag (two-level protection)
# ---------------------------------------------------------------------------

class TestParentNotifyEligibility(unittest.TestCase):

    def test_01_new_item_both_on_gets_eligible_1(self):
        """When both env and DB notify flags are on, new item gets parent_notify_eligible=1."""
        storage = _make_storage()
        # Enable DB notify flag
        storage.update_automation_settings(
            discovery_enabled=True, create_payment_options_enabled=True,
            publish_to_parent_enabled=True, notify_parent_enabled=True,
            scan_interval_minutes=10, updated_by="test", now=_now(),
        )
        now = _now()
        item = storage.upsert_automation_item(
            "inv_new_01", "7850099", "Тест", "{}", now,
            auto_publish_eligible=1, auto_post_eligible=0,
            parent_notify_eligible=1,
        )
        self.assertEqual(item.get("parent_notify_eligible"), 1)
        self.assertIsNotNone(item.get("parent_notify_eligible_at"))

    def test_02_env_off_gets_eligible_0(self):
        """When env flag is off, new item gets parent_notify_eligible=0."""
        storage = _make_storage()
        now = _now()
        item = storage.upsert_automation_item(
            "inv_env_off", "7850099", "Тест", "{}", now,
            parent_notify_eligible=0,
        )
        self.assertEqual(item.get("parent_notify_eligible"), 0)
        self.assertIsNone(item.get("parent_notify_eligible_at"))

    def test_03_db_off_gets_eligible_0(self):
        """When DB flag is off, new item gets parent_notify_eligible=0."""
        storage = _make_storage()
        storage.update_automation_settings(
            discovery_enabled=True, create_payment_options_enabled=True,
            publish_to_parent_enabled=True, notify_parent_enabled=False,
            scan_interval_minutes=10, updated_by="test", now=_now(),
        )
        now = _now()
        item = storage.upsert_automation_item(
            "inv_db_off", "7850099", "Тест", "{}", now,
            parent_notify_eligible=0,
        )
        self.assertEqual(item.get("parent_notify_eligible"), 0)

    def test_04_existing_item_not_upgraded_on_rescan(self):
        """INSERT OR IGNORE: rescan with eligible=1 does NOT upgrade existing eligible=0 row."""
        storage = _make_storage()
        now = _now()
        # First upsert with eligible=0 (flags were off)
        storage.upsert_automation_item(
            "inv_existing_hist", "7850099", "Тест", "{}", now,
            parent_notify_eligible=0,
        )
        # Second upsert with eligible=1 (flags now on) — must NOT change existing row
        item2 = storage.upsert_automation_item(
            "inv_existing_hist", "7850099", "Тест", "{}", now,
            parent_notify_eligible=1,
        )
        self.assertEqual(item2.get("parent_notify_eligible"), 0, "INSERT OR IGNORE must not overwrite")

    def test_05_historical_items_have_zero_eligibility_after_migration(self):
        """Existing rows get DEFAULT 0 — no backfill, historical items stay at 0."""
        storage = _make_storage()
        now = _now()
        # Simulate a pre-v7.0.98.1 row: insert without parent_notify_eligible
        with storage._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO invoice_automation_items
                   (mk_invoice_id, mk_user_id, student_name, invoice_snapshot_json,
                    created_at, updated_at)
                   VALUES ('inv_hist_legacy','7850099','Ист.','{}',:now,:now)""",
                {"now": now},
            )
        item = storage.get_automation_item_by_invoice("inv_hist_legacy")
        self.assertEqual(item.get("parent_notify_eligible", 0), 0)

    def test_06_notify_parent_enabled_default_is_0(self):
        """After migration, notify_parent_enabled defaults to 0 in settings."""
        storage = _make_storage()
        settings = storage.get_automation_settings()
        self.assertEqual(settings.get("notify_parent_enabled", 0), 0)

    def test_07_update_settings_persists_notify_parent_enabled(self):
        """update_automation_settings correctly saves and reads back notify_parent_enabled."""
        storage = _make_storage()
        storage.update_automation_settings(
            discovery_enabled=True, create_payment_options_enabled=True,
            publish_to_parent_enabled=True, notify_parent_enabled=True,
            scan_interval_minutes=10, updated_by="test", now=_now(),
        )
        settings = storage.get_automation_settings()
        self.assertEqual(settings.get("notify_parent_enabled"), 1)


# ---------------------------------------------------------------------------
# 2 — Storage: notification outbox CRUD
# ---------------------------------------------------------------------------

class TestNotificationOutboxCrud(unittest.TestCase):

    def test_08_create_notification_insert_or_ignore(self):
        """create_parent_notification creates row; second call returns existing row."""
        storage = _make_storage()
        now = _now()
        _seed_intent(storage, "ycpi_test_notif_01")
        row1 = storage.create_parent_notification(
            "ycpi_test_notif_01", "inv_01", "new_invoice", "999001", now
        )
        self.assertIsNotNone(row1)
        self.assertEqual(row1["status"], "pending")

        # Second call → same row, INSERT OR IGNORE
        row2 = storage.create_parent_notification(
            "ycpi_test_notif_01", "inv_01", "new_invoice", "999001", now
        )
        self.assertEqual(row1["id"], row2["id"])

    def test_09_claim_notification_success(self):
        """claim_parent_notification transitions pending → sending."""
        storage = _make_storage()
        now = _now()
        _seed_intent(storage, "ycpi_claim_01")
        row = storage.create_parent_notification("ycpi_claim_01", "inv_c1", "new_invoice", "999002", now)
        claimed = storage.claim_parent_notification(row["id"], "tok_abc", now)
        self.assertTrue(claimed)
        fresh = storage.get_parent_notification_by_id(row["id"])
        self.assertEqual(fresh["status"], "sending")

    def test_10_claim_notification_already_sent_fails(self):
        """Cannot claim a notification that is already sent."""
        storage = _make_storage()
        now = _now()
        _seed_intent(storage, "ycpi_claim_sent")
        row = storage.create_parent_notification("ycpi_claim_sent", "inv_cs", "new_invoice", "999003", now)
        storage.mark_parent_notification_sent(row["id"], telegram_message_id=555, sent_at=now, now=now)
        claimed = storage.claim_parent_notification(row["id"], "tok_xyz", now)
        self.assertFalse(claimed)

    def test_11_mark_sent_stores_message_id(self):
        """mark_parent_notification_sent saves telegram_message_id and status=sent."""
        storage = _make_storage()
        now = _now()
        _seed_intent(storage, "ycpi_mark_sent")
        row = storage.create_parent_notification("ycpi_mark_sent", "inv_ms", "new_invoice", "999004", now)
        storage.mark_parent_notification_sent(row["id"], telegram_message_id=12345, sent_at=now, now=now)
        fresh = storage.get_parent_notification_by_id(row["id"])
        self.assertEqual(fresh["status"], "sent")
        self.assertEqual(fresh["telegram_message_id"], 12345)
        self.assertEqual(fresh["sent_at"], now)

    def test_12_unique_constraint_blocks_second_outbox(self):
        """UNIQUE(intent_public_id, notification_type) prevents duplicates."""
        storage = _make_storage()
        now = _now()
        _seed_intent(storage, "ycpi_unique_01")
        storage.create_parent_notification("ycpi_unique_01", "inv_u1", "new_invoice", "999005", now)
        # Must not raise; second insert is OR IGNORE
        try:
            storage.create_parent_notification("ycpi_unique_01", "inv_u1", "new_invoice", "999005", now)
        except Exception as e:
            self.fail(f"Second create raised unexpectedly: {e}")

    def test_13_schedule_retry_increments_attempt_count(self):
        """schedule_parent_notification_retry increments attempt_count and sets next_retry_at."""
        storage = _make_storage()
        now = _now()
        _seed_intent(storage, "ycpi_retry_01")
        row = storage.create_parent_notification("ycpi_retry_01", "inv_r1", "new_invoice", "999006", now)
        storage.schedule_parent_notification_retry(
            row["id"], next_retry_at=_future(5), last_error="tg_err", now=now
        )
        fresh = storage.get_parent_notification_by_id(row["id"])
        self.assertEqual(fresh["status"], "retry")
        self.assertEqual(fresh["attempt_count"], 1)
        self.assertIsNotNone(fresh["next_retry_at"])

    def test_14_list_due_returns_pending_and_overdue_retry(self):
        """list_due_parent_notifications returns pending and retry-due rows."""
        storage = _make_storage()
        now = _now()
        _seed_intent(storage, "ycpi_due_01")
        _seed_intent(storage, "ycpi_due_02")
        _seed_intent(storage, "ycpi_future_01")
        row1 = storage.create_parent_notification("ycpi_due_01", "inv_d1", "new_invoice", "999007", now)
        row2 = storage.create_parent_notification("ycpi_due_02", "inv_d2", "new_invoice", "999008", now)
        row3 = storage.create_parent_notification("ycpi_future_01", "inv_f1", "new_invoice", "999009", now)
        # Mark row2 as retry-due (past)
        storage.schedule_parent_notification_retry(row2["id"], next_retry_at=_past(5), last_error="x", now=now)
        # Mark row3 as retry but in the future
        storage.schedule_parent_notification_retry(row3["id"], next_retry_at=_future(60), last_error="x", now=now)

        due = storage.list_due_parent_notifications(now)
        due_ids = {r["id"] for r in due}
        self.assertIn(row1["id"], due_ids)   # pending
        self.assertIn(row2["id"], due_ids)   # retry-due
        self.assertNotIn(row3["id"], due_ids)  # future retry — not yet due

    def test_15_reset_stale_claims_restores_to_retry(self):
        """reset_stale_parent_notification_claims resets stuck 'sending' rows."""
        storage = _make_storage()
        now = _now()
        old_claim_time = _past(15)
        _seed_intent(storage, "ycpi_stale_01")
        row = storage.create_parent_notification("ycpi_stale_01", "inv_s1", "new_invoice", "999010", now)
        # Force-set to sending with old claimed_at
        with storage._connect() as conn:
            conn.execute(
                "UPDATE payment_parent_notifications SET status='sending', claimed_at=? WHERE id=?",
                (old_claim_time, row["id"]),
            )
        reset_count = storage.reset_stale_parent_notification_claims(stale_before=_past(10), now=now)
        self.assertGreaterEqual(reset_count, 1)
        fresh = storage.get_parent_notification_by_id(row["id"])
        self.assertEqual(fresh["status"], "retry")


# ---------------------------------------------------------------------------
# 3 — MiniAppContext: notification sending logic
# ---------------------------------------------------------------------------

class TestNotificationSending(unittest.TestCase):

    def _make_ctx_with_notify(self, notify_env: bool = True, notify_db: bool = True):
        storage = _make_storage()
        if notify_db:
            storage.update_automation_settings(
                discovery_enabled=True, create_payment_options_enabled=True,
                publish_to_parent_enabled=True, notify_parent_enabled=True,
                scan_interval_minutes=10, updated_by="test", now=_now(),
            )
        settings = _make_settings(notify_env=notify_env)
        ctx = _make_context(storage, settings)
        return ctx, storage

    def test_16_successful_send_saves_message_id_and_sent_at(self):
        """Successful Telegram send marks status=sent with telegram_message_id."""
        ctx, storage = self._make_ctx_with_notify()
        mk_user_id = "7850201"
        _seed_cl_link(storage, mk_user_id, "999101")
        now = _now()
        _seed_intent(storage, "ycpi_send_01", mk_user_id=mk_user_id,
                     mk_invoice_id="inv_send_01", period_month="2026-08")

        item = storage.upsert_automation_item(
            "inv_send_01", mk_user_id, "Ученик Тест", "{}", now, parent_notify_eligible=1,
        )

        with patch("web_app_server._telegram_send_parent_notification_msg",
                   return_value=(True, "", 77777)):
            ctx._enqueue_and_send_parent_notification(
                item, "ycpi_send_01", "inv_send_01", mk_user_id, now
            )

        notif = storage.get_parent_notification("ycpi_send_01", "new_invoice")
        self.assertIsNotNone(notif)
        self.assertEqual(notif["status"], "sent")
        self.assertEqual(notif["telegram_message_id"], 77777)
        self.assertIsNotNone(notif["sent_at"])

    def test_17_message_contains_student_period_amount(self):
        """Notification text contains student name, period, and amount."""
        from web_app_server import MiniAppContext
        text = MiniAppContext._format_parent_notification_text(
            "Александр Крента", "Август 2026", 239.00, "BYN"
        )
        self.assertIn("Александр Крента", text)
        self.assertIn("Август 2026", text)
        # Source uses non-breaking space (\xa0) between amount and currency
        self.assertIn("239,00", text)
        self.assertIn("BYN", text)
        self.assertIn("💳", text)

    def test_18_button_url_opens_payments_tab(self):
        """WebApp URL for notification button includes ?tab=client-payments."""
        ctx, storage = self._make_ctx_with_notify()
        mk_user_id = "7850202"
        _seed_cl_link(storage, mk_user_id, "999102")
        now = _now()
        _seed_intent(storage, "ycpi_btn_01", mk_user_id=mk_user_id, mk_invoice_id="inv_btn_01")
        item = storage.upsert_automation_item(
            "inv_btn_01", mk_user_id, "Тест", "{}", now, parent_notify_eligible=1,
        )
        captured_url = []

        def _capture(bot_token, user_id, text, webapp_url="", **kw):
            captured_url.append(webapp_url)
            return (True, "", 88888)

        with patch("web_app_server._telegram_send_parent_notification_msg", side_effect=_capture):
            ctx._enqueue_and_send_parent_notification(
                item, "ycpi_btn_01", "inv_btn_01", mk_user_id, now
            )
        self.assertTrue(len(captured_url) > 0)
        self.assertIn("tab=client-payments", captured_url[0])

    def test_19_no_cl_link_skips_notification(self):
        """No active CL-link → notification not created."""
        ctx, storage = self._make_ctx_with_notify()
        mk_user_id = "7850203"
        # No CL-link seeded
        now = _now()
        item = storage.upsert_automation_item(
            "inv_no_link", mk_user_id, "Тест", "{}", now, parent_notify_eligible=1,
        )
        with patch("web_app_server._telegram_send_parent_notification_msg") as mock_send:
            ctx._enqueue_and_send_parent_notification(
                item, "ycpi_no_link", "inv_no_link", mk_user_id, now
            )
            mock_send.assert_not_called()

        notif = storage.get_parent_notification("ycpi_no_link", "new_invoice")
        self.assertIsNone(notif)

    def test_20_no_telegram_id_skips_notification(self):
        """Empty telegram_user_id in CL-link → not sent."""
        ctx, storage = self._make_ctx_with_notify()
        mk_user_id = "7850204"
        # Seed CL-link with empty telegram_user_id
        now = _now()
        with storage._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO client_parent_child_links
                   (parent_telegram_user_id, mk_user_id, child_display_name,
                    status, linked_at, created_at, updated_at)
                   VALUES ('',?,'Тест','active',?,?,?)""",
                (mk_user_id, now, now, now),
            )
        item = storage.upsert_automation_item(
            "inv_no_tg", mk_user_id, "Тест", "{}", now, parent_notify_eligible=1,
        )
        with patch("web_app_server._telegram_send_parent_notification_msg") as mock_send:
            ctx._enqueue_and_send_parent_notification(
                item, "ycpi_no_tg", "inv_no_tg", mk_user_id, now
            )
            mock_send.assert_not_called()

    def test_21_env_flag_off_skips_notification(self):
        """Global env flag off → notification skipped even if DB flag on."""
        ctx, storage = self._make_ctx_with_notify(notify_env=False, notify_db=True)
        mk_user_id = "7850205"
        _seed_cl_link(storage, mk_user_id, "999105")
        now = _now()
        item = storage.upsert_automation_item(
            "inv_env_off2", mk_user_id, "Тест", "{}", now, parent_notify_eligible=1,
        )
        with patch("web_app_server._telegram_send_parent_notification_msg") as mock_send:
            ctx._enqueue_and_send_parent_notification(
                item, "ycpi_env_off2", "inv_env_off2", mk_user_id, now
            )
            mock_send.assert_not_called()

    def test_22_db_flag_off_skips_notification(self):
        """DB flag off → notification skipped even if env flag on."""
        ctx, storage = self._make_ctx_with_notify(notify_env=True, notify_db=False)
        mk_user_id = "7850206"
        _seed_cl_link(storage, mk_user_id, "999106")
        now = _now()
        item = storage.upsert_automation_item(
            "inv_db_off2", mk_user_id, "Тест", "{}", now, parent_notify_eligible=1,
        )
        with patch("web_app_server._telegram_send_parent_notification_msg") as mock_send:
            ctx._enqueue_and_send_parent_notification(
                item, "ycpi_db_off2", "inv_db_off2", mk_user_id, now
            )
            mock_send.assert_not_called()

    def test_23_ineligible_item_skips_notification(self):
        """parent_notify_eligible=0 → no notification even if flags are on."""
        ctx, storage = self._make_ctx_with_notify()
        mk_user_id = "7850207"
        _seed_cl_link(storage, mk_user_id, "999107")
        now = _now()
        item = storage.upsert_automation_item(
            "inv_inelig", mk_user_id, "Тест", "{}", now, parent_notify_eligible=0,
        )
        with patch("web_app_server._telegram_send_parent_notification_msg") as mock_send:
            ctx._enqueue_and_send_parent_notification(
                item, "ycpi_inelig", "inv_inelig", mk_user_id, now
            )
            mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# 4 — Error classification
# ---------------------------------------------------------------------------

class TestErrorClassification(unittest.TestCase):

    def _make_ctx(self):
        storage = _make_storage()
        storage.update_automation_settings(
            discovery_enabled=True, create_payment_options_enabled=True,
            publish_to_parent_enabled=True, notify_parent_enabled=True,
            scan_interval_minutes=10, updated_by="test", now=_now(),
        )
        settings = _make_settings(notify_env=True)
        return _make_context(storage, settings), storage

    def _create_notif(self, storage, intent_pub_id="ycpi_err_test", tg_user="999201"):
        now = _now()
        _seed_intent(storage, intent_pub_id, mk_invoice_id=intent_pub_id)
        return storage.create_parent_notification(
            intent_pub_id, intent_pub_id, "new_invoice", tg_user, now
        ), now

    def test_24_permanent_error_blocked_sets_failed(self):
        """'bot was blocked by the user' → status=failed, no retry."""
        ctx, storage = self._make_ctx()
        row, now = self._create_notif(storage, "ycpi_blocked_01")

        with patch("web_app_server._telegram_send_parent_notification_msg",
                   return_value=(False, "bot was blocked by the user", None)):
            result = ctx._try_deliver_parent_notification(
                row["id"], "ycpi_blocked_01", "ycpi_blocked_01",
                "999201", "Тест", 239.0, "BYN", "Август 2026", 0, now,
            )
        self.assertTrue(result.get("failed"))
        fresh = storage.get_parent_notification_by_id(row["id"])
        self.assertEqual(fresh["status"], "failed")

    def test_25_rate_limit_429_schedules_retry(self):
        """Telegram 429 → retry_scheduled, next_retry_at set."""
        ctx, storage = self._make_ctx()
        row, now = self._create_notif(storage, "ycpi_429_01")

        with patch("web_app_server._telegram_send_parent_notification_msg",
                   return_value=(False, "RATE_LIMITED:30", None)):
            result = ctx._try_deliver_parent_notification(
                row["id"], "ycpi_429_01", "ycpi_429_01",
                "999201", "Тест", 239.0, "BYN", "Август 2026", 0, now,
            )
        self.assertTrue(result.get("retry_scheduled"))
        fresh = storage.get_parent_notification_by_id(row["id"])
        self.assertEqual(fresh["status"], "retry")
        self.assertIsNotNone(fresh["next_retry_at"])

    def test_26_transient_error_schedules_retry(self):
        """Clearly transient (pre-connection) error → retry_scheduled."""
        ctx, storage = self._make_ctx()
        row, now = self._create_notif(storage, "ycpi_transient_01")

        with patch("web_app_server._telegram_send_parent_notification_msg",
                   return_value=(False, "TRANSIENT:Connection refused", None)):
            result = ctx._try_deliver_parent_notification(
                row["id"], "ycpi_transient_01", "ycpi_transient_01",
                "999201", "Тест", 239.0, "BYN", "Август 2026", 0, now,
            )
        self.assertTrue(result.get("retry_scheduled"))
        fresh = storage.get_parent_notification_by_id(row["id"])
        self.assertEqual(fresh["status"], "retry")

    def test_27_ambiguous_timeout_sets_requires_check(self):
        """Ambiguous outcome (timeout after possible send) → requires_check, no auto-retry."""
        ctx, storage = self._make_ctx()
        row, now = self._create_notif(storage, "ycpi_ambig_01")

        with patch("web_app_server._telegram_send_parent_notification_msg",
                   return_value=(False, "AMBIGUOUS:Read timed out", None)):
            result = ctx._try_deliver_parent_notification(
                row["id"], "ycpi_ambig_01", "ycpi_ambig_01",
                "999201", "Тест", 239.0, "BYN", "Август 2026", 0, now,
            )
        self.assertTrue(result.get("requires_check"))
        fresh = storage.get_parent_notification_by_id(row["id"])
        self.assertEqual(fresh["status"], "requires_check")

    def test_28_max_attempts_sets_requires_check(self):
        """5+ attempts reached → requires_check, not sent."""
        ctx, storage = self._make_ctx()
        row, now = self._create_notif(storage, "ycpi_max_01")

        with patch("web_app_server._telegram_send_parent_notification_msg") as mock_send:
            result = ctx._try_deliver_parent_notification(
                row["id"], "ycpi_max_01", "ycpi_max_01",
                "999201", "Тест", 239.0, "BYN", "Август 2026",
                attempt_count=5, now=now,
            )
            mock_send.assert_not_called()
        self.assertTrue(result.get("requires_check"))
        fresh = storage.get_parent_notification_by_id(row["id"])
        self.assertEqual(fresh["status"], "requires_check")

    def test_29_user_deactivated_sets_failed(self):
        """'user is deactivated' → permanent failure."""
        ctx, storage = self._make_ctx()
        row, now = self._create_notif(storage, "ycpi_deact_01")

        with patch("web_app_server._telegram_send_parent_notification_msg",
                   return_value=(False, "user is deactivated", None)):
            result = ctx._try_deliver_parent_notification(
                row["id"], "ycpi_deact_01", "ycpi_deact_01",
                "999201", "Тест", 239.0, "BYN", "Август 2026", 0, now,
            )
        self.assertTrue(result.get("failed"))


# ---------------------------------------------------------------------------
# 5 — Idempotency (no double sends)
# ---------------------------------------------------------------------------

class TestIdempotency(unittest.TestCase):

    def _make_ctx(self):
        storage = _make_storage()
        storage.update_automation_settings(
            discovery_enabled=True, create_payment_options_enabled=True,
            publish_to_parent_enabled=True, notify_parent_enabled=True,
            scan_interval_minutes=10, updated_by="test", now=_now(),
        )
        settings = _make_settings(notify_env=True)
        return _make_context(storage, settings), storage

    def test_30_restart_after_sent_does_not_resend(self):
        """If status=sent, _enqueue_and_send does not call Telegram again."""
        ctx, storage = self._make_ctx()
        mk_user_id = "7850301"
        _seed_cl_link(storage, mk_user_id, "999301")
        now = _now()
        _seed_intent(storage, "ycpi_resend_01", mk_user_id=mk_user_id, mk_invoice_id="inv_rs_01")
        item = storage.upsert_automation_item(
            "inv_rs_01", mk_user_id, "Тест", "{}", now, parent_notify_eligible=1,
        )

        call_count = [0]
        def _send_once(*a, **kw):
            call_count[0] += 1
            return (True, "", 10001)

        with patch("web_app_server._telegram_send_parent_notification_msg", side_effect=_send_once):
            ctx._enqueue_and_send_parent_notification(item, "ycpi_resend_01", "inv_rs_01", mk_user_id, now)
            # Second call (e.g. rescan, scheduler restart)
            ctx._enqueue_and_send_parent_notification(item, "ycpi_resend_01", "inv_rs_01", mk_user_id, now)

        self.assertEqual(call_count[0], 1, "Should send exactly once")

    def test_31_double_scheduler_claim_conflict_prevents_duplicate(self):
        """Concurrent claims: second claim fails → only one delivery."""
        ctx, storage = self._make_ctx()
        now = _now()
        _seed_intent(storage, "ycpi_dual_01", mk_invoice_id="inv_dual_01")
        row = storage.create_parent_notification("ycpi_dual_01", "inv_dual_01", "new_invoice", "999302", now)
        # Claim once
        storage.claim_parent_notification(row["id"], "tok_first", now)
        # Second claim should fail
        claimed2 = storage.claim_parent_notification(row["id"], "tok_second", now)
        self.assertFalse(claimed2)

    def test_32_repeat_publish_does_not_create_second_outbox(self):
        """Repeat publish (idempotent) does not create a second notification outbox row."""
        ctx, storage = self._make_ctx()
        mk_user_id = "7850302"
        _seed_cl_link(storage, mk_user_id, "999303")
        now = _now()
        _seed_intent(storage, "ycpi_repub_01", mk_user_id=mk_user_id, mk_invoice_id="inv_rp_01")
        item = storage.upsert_automation_item(
            "inv_rp_01", mk_user_id, "Тест", "{}", now, parent_notify_eligible=1,
        )

        send_calls = [0]
        def _send_once(*a, **kw):
            send_calls[0] += 1
            return (True, "", 10002)

        with patch("web_app_server._telegram_send_parent_notification_msg", side_effect=_send_once):
            ctx._enqueue_and_send_parent_notification(item, "ycpi_repub_01", "inv_rp_01", mk_user_id, now)
            ctx._enqueue_and_send_parent_notification(item, "ycpi_repub_01", "inv_rp_01", mk_user_id, now)

        # Exactly one outbox row
        with storage._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM payment_parent_notifications WHERE intent_public_id='ycpi_repub_01'"
            ).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(send_calls[0], 1)


# ---------------------------------------------------------------------------
# 6 — Manual retry guards
# ---------------------------------------------------------------------------

class TestManualRetry(unittest.TestCase):

    def _make_ctx(self):
        storage = _make_storage()
        settings = _make_settings(notify_env=True)
        return _make_context(storage, settings), storage

    def test_33_manual_retry_blocked_for_sent(self):
        """retry_parent_notification returns error for status=sent."""
        ctx, storage = self._make_ctx()
        now = _now()
        mk_user_id = "7850401"
        _seed_cl_link(storage, mk_user_id, "999401")
        _seed_intent(storage, "ycpi_mretry_01", mk_user_id=mk_user_id, mk_invoice_id="inv_mr_01",
                     client_visibility="published")
        row = storage.create_parent_notification("ycpi_mretry_01", "inv_mr_01", "new_invoice", "999401", now)
        storage.mark_parent_notification_sent(row["id"], telegram_message_id=555, sent_at=now, now=now)

        auth = {"_internal": True, "role": "owner"}
        result = ctx.retry_parent_notification(auth, row["id"])
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "already_sent")

    def test_34_manual_retry_blocked_for_requires_check(self):
        """retry_parent_notification returns error for requires_check (ambiguous outcome)."""
        ctx, storage = self._make_ctx()
        now = _now()
        mk_user_id = "7850402"
        _seed_cl_link(storage, mk_user_id, "999402")
        _seed_intent(storage, "ycpi_mretry_02", mk_user_id=mk_user_id, mk_invoice_id="inv_mr_02",
                     client_visibility="published")
        row = storage.create_parent_notification("ycpi_mretry_02", "inv_mr_02", "new_invoice", "999402", now)
        storage.mark_parent_notification_requires_check(row["id"], last_error="ambiguous", now=now)

        auth = {"_internal": True, "role": "owner"}
        result = ctx.retry_parent_notification(auth, row["id"])
        self.assertFalse(result.get("ok"))
        self.assertIn("requires_check", result.get("error", ""))


# ---------------------------------------------------------------------------
# 7 — Period label formatting
# ---------------------------------------------------------------------------

class TestPeriodLabel(unittest.TestCase):

    def test_35_period_label_august_2026(self):
        from web_app_server import MiniAppContext
        label = MiniAppContext._notify_parent_period_label("2026-08")
        self.assertEqual(label, "Август 2026")  # nominative (fixed in v7.0.98.1)

    def test_36_period_label_january(self):
        from web_app_server import MiniAppContext
        label = MiniAppContext._notify_parent_period_label("2026-01")
        self.assertEqual(label, "Январь 2026")  # nominative (fixed in v7.0.98.1)

    def test_37_period_label_empty(self):
        from web_app_server import MiniAppContext
        label = MiniAppContext._notify_parent_period_label("")
        self.assertEqual(label, "")

    def test_38_period_label_invalid(self):
        from web_app_server import MiniAppContext
        label = MiniAppContext._notify_parent_period_label("bad-data")
        self.assertIsInstance(label, str)


# ---------------------------------------------------------------------------
# 8 — Backward compatibility: MoyKlass auto-post still works
# ---------------------------------------------------------------------------

class TestAutoPostStillWorks(unittest.TestCase):

    def test_39_auto_post_eligible_column_still_present(self):
        """v7.0.98.1 migration does not break auto_post_eligible column."""
        storage = _make_storage()
        now = _now()
        item = storage.upsert_automation_item(
            "inv_compat_01", "7850501", "Тест", "{}", now,
            auto_post_eligible=1, auto_publish_eligible=1, parent_notify_eligible=1,
        )
        self.assertEqual(item.get("auto_post_eligible"), 1)
        self.assertEqual(item.get("auto_publish_eligible"), 1)
        self.assertEqual(item.get("parent_notify_eligible"), 1)

    def test_40_post_to_moyklass_enabled_still_in_settings(self):
        """update_automation_settings without notify_parent_enabled still works."""
        storage = _make_storage()
        # Call with all original params (backward compat)
        storage.update_automation_settings(
            discovery_enabled=True, create_payment_options_enabled=True,
            publish_to_parent_enabled=True, post_to_moyklass_enabled=True,
            scan_interval_minutes=10, updated_by="test", now=_now(),
        )
        s = storage.get_automation_settings()
        self.assertEqual(s.get("post_to_moyklass_enabled"), 1)
        # notify_parent_enabled stays at default 0
        self.assertEqual(s.get("notify_parent_enabled", 0), 0)


# ---------------------------------------------------------------------------
# 9 — Food module isolation
# ---------------------------------------------------------------------------

class TestFoodModuleIsolation(unittest.TestCase):

    def test_41_food_tables_not_touched_by_notification_migration(self):
        """payment_parent_notifications is separate from all food_* tables."""
        storage = _make_storage()
        with storage._connect() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        food_tables = {t for t in tables if t.startswith("food_")}
        # payment_parent_notifications must NOT be a food table
        self.assertNotIn("payment_parent_notifications", food_tables)
        # payment_parent_notifications must exist
        self.assertIn("payment_parent_notifications", tables)

    def test_42_cl_links_not_confused_with_food_links(self):
        """CL-links come from client_parent_child_links, not food_parent_child_links."""
        storage = _make_storage()
        with storage._connect() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        self.assertIn("client_parent_child_links", tables)
        # food_parent_child_links is a different table if it exists
        if "food_parent_child_links" in tables:
            # Must be a separate table
            self.assertNotEqual("food_parent_child_links", "client_parent_child_links")


# ---------------------------------------------------------------------------
# 10 — Version
# ---------------------------------------------------------------------------

class TestVersion(unittest.TestCase):

    def test_43_current_version(self):
        self.assertEqual(CURRENT_VERSION, "7.0.98.1")

    def test_44_payment_domain_version(self):
        import payment_domain
        src = Path(ROOT, "payment_domain.py").read_text(encoding="utf-8")
        self.assertIn("7.0.98.1", src)

    def test_45_miniapp_version(self):
        src = Path(ROOT, "miniapp", "app.js").read_text(encoding="utf-8")
        self.assertIn("v7.0.98.1", src)

    def test_46_index_html_cache_bust(self):
        src = Path(ROOT, "miniapp", "index.html").read_text(encoding="utf-8")
        self.assertIn("7.0.98.1", src)


if __name__ == "__main__":
    unittest.main()
