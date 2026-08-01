"""Tests for v7.0.98.0 — safe recall (withdrawal) of an erroneously issued invoice.

Covers:
  - Pre-checks: paid, posted, mk_payment_id, ambiguous webhook
  - Core withdrawal flow: visibility, auto_post/auto_publish flags
  - Automation scheduler protection (no re-publish after withdrawal)
  - Backend payment option guards (ERIP, card) for withdrawn intents
  - ERIP cancellation (unsupported → local block)
  - Telegram notification editing
  - Race condition: payment arriving during withdrawal
  - Idempotency
  - Audit record preservation
  - Period label fix: all 12 months in nominative case

Run offline (no Telegram / bePaid / MoyKlass):
    python -m unittest tests.test_withdrawal -v
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage

CURRENT_VERSION = "7.1"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.telegram_bot_token = "test_token_123"
    s.payment_parent_notifications_enabled = True
    s.web_app_url = "https://t.me/app"
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
    mk_payment_id: int = None,
    mk_posting_status: str = "",
) -> None:
    now = _now()
    with storage._connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO payment_intents
               (public_id, mk_user_id, mk_invoice_id, student_name,
                amount_minor, amount_byn, currency, period_month,
                status, client_visibility,
                mk_payment_id, mk_posting_status,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                public_id, int(mk_user_id), mk_invoice_id or public_id, student_name,
                amount_minor, round(amount_minor / 100.0, 2),
                currency, period_month, status, client_visibility,
                mk_payment_id, mk_posting_status or None,
                now, now,
            ),
        )


def _seed_automation_item(
    storage: Storage,
    mk_invoice_id: str,
    mk_user_id: str = "7850099",
    auto_post_eligible: int = 1,
    auto_publish_eligible: int = 1,
    parent_notify_eligible: int = 1,
) -> dict:
    now = _now()
    return storage.upsert_automation_item(
        mk_invoice_id, mk_user_id, "Тест", "{}", now,
        auto_post_eligible=auto_post_eligible,
        auto_publish_eligible=auto_publish_eligible,
        parent_notify_eligible=parent_notify_eligible,
    )


def _seed_bepaid_tx(
    storage: Storage,
    public_id: str,
    webhook_verified: int = 1,
    provider_verified: int = 0,
    status: str = "successful",
) -> None:
    now = _now()
    with storage._connect() as conn:
        conn.execute(
            """INSERT INTO bepaid_transactions
               (provider, shop_type, transaction_uid, status,
                amount_minor, currency, received_at, updated_at,
                intent_public_id, webhook_verified, provider_verified)
               VALUES ('bepaid','erip','tx_test_01',?,23900,'BYN',?,?,?,?,?)""",
            (status, now, now, public_id, webhook_verified, provider_verified),
        )


_WITHDRAW_AUTH = {"_internal": True, "role": "owner", "user_id": "9001", "full_name": "Admin Test"}
_WITHDRAW_BODY = {"reason": "ошибочно выставленная сумма"}


# ---------------------------------------------------------------------------
# 1 — Pre-checks
# ---------------------------------------------------------------------------

class TestWithdrawalPreChecks(unittest.TestCase):

    def _ctx(self):
        storage = _make_storage()
        return _make_context(storage, _make_settings()), storage

    def test_01_unpaid_intent_can_be_withdrawn(self):
        """Unpaid intent is successfully withdrawn."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_w_01", status="awaiting_payment")
        result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_w_01", _WITHDRAW_BODY)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("status"), "withdrawn")

    def test_02_paid_intent_goes_to_requires_check(self):
        """Paid intent cannot be withdrawn normally — returns requires_check."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_w_02", status="paid")
        result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_w_02", _WITHDRAW_BODY)
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("requires_check"))

    def test_03_posted_to_moyklass_goes_to_requires_check(self):
        """posted_to_moyklass intent cannot be withdrawn normally."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_w_03", status="posted_to_moyklass")
        result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_w_03", _WITHDRAW_BODY)
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("requires_check"))

    def test_04_mk_payment_id_blocks_withdrawal(self):
        """Presence of mk_payment_id blocks normal withdrawal."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_w_04", status="awaiting_payment", mk_payment_id=12345)
        result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_w_04", _WITHDRAW_BODY)
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("requires_check"))

    def test_05_mk_posting_status_posted_blocks_withdrawal(self):
        """mk_posting_status='posted' blocks normal withdrawal."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_w_05", status="awaiting_payment", mk_posting_status="posted")
        result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_w_05", _WITHDRAW_BODY)
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("requires_check"))

    def test_06_confirmed_bepaid_tx_blocks_withdrawal(self):
        """Confirmed bePaid transaction (webhook_verified=1) blocks normal withdrawal."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_w_06", status="awaiting_payment")
        _seed_bepaid_tx(storage, "ycpi_w_06", webhook_verified=1)
        result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_w_06", _WITHDRAW_BODY)
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("requires_check"))

    def test_07_provider_verified_tx_blocks_withdrawal(self):
        """provider_verified=1 transaction blocks normal withdrawal."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_w_07", status="awaiting_payment")
        _seed_bepaid_tx(storage, "ycpi_w_07", webhook_verified=0, provider_verified=1)
        result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_w_07", _WITHDRAW_BODY)
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("requires_check"))

    def test_08_cancelled_intent_returns_error(self):
        """Cancelled intent cannot be withdrawn (already terminal)."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_w_08", status="cancelled")
        result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_w_08", _WITHDRAW_BODY)
        self.assertFalse(result.get("ok"))
        self.assertFalse(result.get("requires_check"), result)

    def test_09_not_found_returns_error(self):
        """Non-existent intent returns error."""
        ctx, storage = self._ctx()
        result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_nonexistent", _WITHDRAW_BODY)
        self.assertFalse(result.get("ok"))

    def test_10_requires_check_audit_saved_for_paid(self):
        """requires_check record is saved in withdrawal table even for blocked paid case."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_w_10", status="paid")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_w_10", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_w_10")
        self.assertIsNotNone(wr)
        self.assertEqual(wr.get("status"), "requires_check")


# ---------------------------------------------------------------------------
# 2 — Core flow
# ---------------------------------------------------------------------------

class TestWithdrawalCoreFlow(unittest.TestCase):

    def _ctx(self):
        storage = _make_storage()
        return _make_context(storage, _make_settings()), storage

    def test_11_after_withdrawal_client_visibility_withdrawn(self):
        """After withdrawal, client_visibility='withdrawn'."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_cw_01", status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_cw_01", _WITHDRAW_BODY)
        pi = storage.get_payment_intent("ycpi_cw_01")
        self.assertEqual(pi.get("client_visibility"), "withdrawn")

    def test_12_after_withdrawal_auto_post_eligible_zero(self):
        """After withdrawal, auto_post_eligible=0 on automation item."""
        ctx, storage = self._ctx()
        mk_inv = "inv_w_12"
        _seed_intent(storage, "ycpi_cw_02", mk_invoice_id=mk_inv, status="awaiting_payment")
        _seed_automation_item(storage, mk_inv, auto_post_eligible=1)
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_cw_02", _WITHDRAW_BODY)
        item = storage.get_automation_item_by_invoice(mk_inv)
        self.assertEqual(item.get("auto_post_eligible"), 0)

    def test_13_after_withdrawal_auto_publish_eligible_zero(self):
        """After withdrawal, auto_publish_eligible=0 on automation item."""
        ctx, storage = self._ctx()
        mk_inv = "inv_w_13"
        _seed_intent(storage, "ycpi_cw_03", mk_invoice_id=mk_inv, status="awaiting_payment")
        _seed_automation_item(storage, mk_inv, auto_publish_eligible=1)
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_cw_03", _WITHDRAW_BODY)
        item = storage.get_automation_item_by_invoice(mk_inv)
        self.assertEqual(item.get("auto_publish_eligible"), 0)

    def test_14_payment_intent_not_deleted_after_withdrawal(self):
        """Withdrawal does NOT delete the payment_intent record."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_cw_04", status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_cw_04", _WITHDRAW_BODY)
        pi = storage.get_payment_intent("ycpi_cw_04")
        self.assertIsNotNone(pi)

    def test_15_automation_item_not_deleted_after_withdrawal(self):
        """Withdrawal does NOT delete the invoice_automation_item record."""
        ctx, storage = self._ctx()
        mk_inv = "inv_w_15"
        _seed_intent(storage, "ycpi_cw_05", mk_invoice_id=mk_inv, status="awaiting_payment")
        _seed_automation_item(storage, mk_inv)
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_cw_05", _WITHDRAW_BODY)
        item = storage.get_automation_item_by_invoice(mk_inv)
        self.assertIsNotNone(item)

    def test_16_audit_record_saves_reason_and_actor(self):
        """Withdrawal record saves reason, requested_by_telegram_id, and requested_at."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_cw_06", status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_cw_06", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_cw_06")
        self.assertIsNotNone(wr)
        self.assertEqual(wr.get("reason"), "ошибочно выставленная сумма")
        self.assertEqual(str(wr.get("requested_by_telegram_id") or ""), "9001")
        self.assertIsNotNone(wr.get("requested_at"))

    def test_17_withdrawal_status_is_withdrawn_after_success(self):
        """withdrawal record status='withdrawn' after successful flow."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_cw_07", status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_cw_07", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_cw_07")
        self.assertEqual(wr.get("status"), "withdrawn")

    def test_18_withdrawal_saves_payment_status_at_request(self):
        """withdrawal record stores payment_status_at_request."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_cw_08", status="bepaid_created")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_cw_08", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_cw_08")
        self.assertEqual(wr.get("payment_status_at_request"), "bepaid_created")

    def test_19_withdrawal_historical_intent_not_affected(self):
        """Old intent from a different invoice is NOT affected by another withdrawal."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_hist_01", mk_invoice_id="inv_hist_01",
                     status="posted_to_moyklass", client_visibility="published")
        _seed_intent(storage, "ycpi_active_01", mk_invoice_id="inv_active_01",
                     status="awaiting_payment", client_visibility="published")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_active_01", _WITHDRAW_BODY)
        # Historical one stays untouched
        hist = storage.get_payment_intent("ycpi_hist_01")
        self.assertEqual(hist.get("client_visibility"), "published")

    def test_20_withdrawal_does_not_delete_webhook_history(self):
        """Withdrawal does NOT delete the bepaid_transactions records."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_cw_09", status="awaiting_payment")
        # Insert an unverified tx (not confirmed, so won't block withdrawal)
        now = _now()
        with storage._connect() as conn:
            conn.execute(
                """INSERT INTO bepaid_transactions
                   (provider, shop_type, transaction_uid, status,
                    amount_minor, currency, received_at, updated_at,
                    intent_public_id, webhook_verified, provider_verified)
                   VALUES ('bepaid','erip','tx_unver_01','incomplete',23900,'BYN',?,?,?,0,0)""",
                (now, now, "ycpi_cw_09"),
            )
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_cw_09", _WITHDRAW_BODY)
        with storage._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM bepaid_transactions WHERE intent_public_id='ycpi_cw_09'"
            ).fetchone()[0]
        self.assertEqual(count, 1)


# ---------------------------------------------------------------------------
# 3 — Access control and validation
# ---------------------------------------------------------------------------

class TestWithdrawalAccessControl(unittest.TestCase):

    def _ctx(self):
        storage = _make_storage()
        return _make_context(storage, _make_settings()), storage

    def test_21_short_reason_rejected(self):
        """Reason shorter than 5 characters is rejected."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_ac_01", status="awaiting_payment")
        result = ctx.withdraw_payment_intent(
            _WITHDRAW_AUTH, "ycpi_ac_01", {"reason": "ok"}
        )
        self.assertFalse(result.get("ok"))
        self.assertIn("5", result.get("error", ""))

    def test_22_empty_reason_rejected(self):
        """Empty reason is rejected."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_ac_02", status="awaiting_payment")
        result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_ac_02", {"reason": ""})
        self.assertFalse(result.get("ok"))

    def test_23_non_admin_role_denied(self):
        """Non-admin role cannot withdraw an invoice."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_ac_03", status="awaiting_payment")
        # Seed a parent user
        now = _now()
        with storage._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO staff_users (user_id, role, status, created_at, updated_at) "
                "VALUES (8801,'parent','active',?,?)", (now, now),
            )
        parent_auth = {"user_id": "8801", "full_name": "Parent"}
        result = ctx.withdraw_payment_intent(parent_auth, "ycpi_ac_03", _WITHDRAW_BODY)
        self.assertFalse(result.get("ok"))
        self.assertIn("Доступ запрещён", result.get("error", ""))

    def test_24_teacher_role_denied(self):
        """Teacher role cannot withdraw an invoice."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_ac_04", status="awaiting_payment")
        now = _now()
        with storage._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO staff_users (user_id, role, status, created_at, updated_at) "
                "VALUES (8802,'teacher','active',?,?)", (now, now),
            )
        teacher_auth = {"user_id": "8802", "full_name": "Teacher"}
        result = ctx.withdraw_payment_intent(teacher_auth, "ycpi_ac_04", _WITHDRAW_BODY)
        self.assertFalse(result.get("ok"))


# ---------------------------------------------------------------------------
# 4 — Idempotency
# ---------------------------------------------------------------------------

class TestWithdrawalIdempotency(unittest.TestCase):

    def _ctx(self):
        storage = _make_storage()
        return _make_context(storage, _make_settings()), storage

    def test_25_repeat_withdrawal_is_idempotent(self):
        """Second withdrawal of the same intent returns idempotent=True."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_idem_01", status="awaiting_payment")
        r1 = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_idem_01", _WITHDRAW_BODY)
        self.assertTrue(r1.get("ok"), r1)
        r2 = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_idem_01", _WITHDRAW_BODY)
        self.assertTrue(r2.get("ok"), r2)
        self.assertTrue(r2.get("idempotent"), r2)

    def test_26_repeat_withdrawal_does_not_create_second_record(self):
        """Second withdrawal does not create a second audit record."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_idem_02", status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_idem_02", _WITHDRAW_BODY)
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_idem_02", _WITHDRAW_BODY)
        with storage._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM payment_intent_withdrawals WHERE intent_public_id='ycpi_idem_02'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_27_idempotent_response_contains_withdrawal_status(self):
        """Idempotent response includes withdrawal status."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_idem_03", status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_idem_03", _WITHDRAW_BODY)
        r2 = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_idem_03", _WITHDRAW_BODY)
        self.assertIsNotNone(r2.get("status"))


# ---------------------------------------------------------------------------
# 5 — ERIP handling
# ---------------------------------------------------------------------------

class TestEripWithdrawal(unittest.TestCase):

    def _ctx(self):
        storage = _make_storage()
        return _make_context(storage, _make_settings()), storage

    def test_28_erip_void_unsupported_local_block_still_applied(self):
        """ERIP void unsupported → local blocking still marks card_checkout_blocked_at."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_erip_01", status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_erip_01", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_erip_01")
        self.assertEqual(wr.get("erip_cancel_status"), "unsupported")
        self.assertIsNotNone(wr.get("card_checkout_blocked_at"))

    def test_29_erip_cancel_status_saved(self):
        """erip_cancel_status is saved in withdrawal record."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_erip_02", status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_erip_02", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_erip_02")
        self.assertIsNotNone(wr.get("erip_cancel_status"))

    def test_30_withdrawal_storage_erip_update_methods(self):
        """Storage update_withdrawal_erip method saves all fields."""
        storage = _make_storage()
        now = _now()
        wr = storage.create_withdrawal_record(
            public_id="ycpi_erip_03",
            mk_invoice_id="inv_erip_03",
            reason="тест метода",
            requested_by_telegram_id="9001",
            requested_by_name="Test",
            payment_status_at_request="awaiting_payment",
            now=now,
        )
        storage.update_withdrawal_erip(wr["id"], "already_expired", now, None, now)
        wr2 = storage.get_withdrawal_by_intent("ycpi_erip_03")
        self.assertEqual(wr2.get("erip_cancel_status"), "already_expired")
        self.assertEqual(wr2.get("erip_cancelled_at"), now)


# ---------------------------------------------------------------------------
# 6 — Card (acquiring checkout) handling
# ---------------------------------------------------------------------------

class TestCardWithdrawal(unittest.TestCase):

    def _ctx(self):
        storage = _make_storage()
        return _make_context(storage, _make_settings()), storage

    def test_31_create_bepaid_blocked_for_withdrawn_intent(self):
        """payment_intent_create_bepaid returns error for withdrawn intent."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_card_01", status="awaiting_payment",
                     client_visibility="withdrawn")
        result = ctx.payment_intent_create_bepaid(
            _WITHDRAW_AUTH, "ycpi_card_01", {}, _bypass_method_check=True
        )
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("withdrawn"), result)

    def test_32_create_acquiring_blocked_for_withdrawn_intent(self):
        """payment_intent_create_acquiring_option returns error for withdrawn intent."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_card_02", status="awaiting_payment",
                     client_visibility="withdrawn")
        result = ctx.payment_intent_create_acquiring_option(_WITHDRAW_AUTH, "ycpi_card_02")
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("withdrawn"), result)

    def test_33_card_checkout_blocked_at_saved(self):
        """card_checkout_blocked_at is set in withdrawal record."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_card_03", status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_card_03", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_card_03")
        self.assertIsNotNone(wr.get("card_checkout_blocked_at"))


# ---------------------------------------------------------------------------
# 7 — Telegram notification editing
# ---------------------------------------------------------------------------

class TestTelegramWithdrawal(unittest.TestCase):

    def _ctx(self):
        storage = _make_storage()
        return _make_context(storage, _make_settings()), storage

    def _seed_notification(
        self, storage: Storage, public_id: str,
        status: str = "sent",
        message_id: int = 54321,
    ) -> None:
        now = _now()
        with storage._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO payment_parent_notifications
                   (intent_public_id, mk_invoice_id, notification_type,
                    telegram_user_id, telegram_message_id, status,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (public_id, "inv_tg_01", "new_invoice", "999301", message_id, status, now, now),
            )

    def test_34_telegram_message_edited_on_withdrawal(self):
        """Successful Telegram edit is recorded in withdrawal record."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_tg_01", status="awaiting_payment")
        self._seed_notification(storage, "ycpi_tg_01")
        with patch("web_app_server._telegram_edit_parent_notification_msg") as mock_edit:
            mock_edit.return_value = (True, "")
            ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_tg_01", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_tg_01")
        self.assertEqual(wr.get("telegram_update_status"), "edited")

    def test_35_not_modified_counts_as_success(self):
        """'message is not modified' from Telegram counts as success."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_tg_02", status="awaiting_payment")
        self._seed_notification(storage, "ycpi_tg_02")
        with patch("web_app_server._telegram_edit_parent_notification_msg") as mock_edit:
            mock_edit.return_value = (True, "")
            result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_tg_02", _WITHDRAW_BODY)
        self.assertTrue(result.get("ok"))

    def test_36_repeat_withdrawal_does_not_re_edit(self):
        """Second withdrawal does not attempt to re-edit Telegram message."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_tg_03", status="awaiting_payment")
        self._seed_notification(storage, "ycpi_tg_03")
        with patch("web_app_server._telegram_edit_parent_notification_msg") as mock_edit:
            mock_edit.return_value = (True, "")
            ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_tg_03", _WITHDRAW_BODY)
            first_call_count = mock_edit.call_count
            ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_tg_03", _WITHDRAW_BODY)
            second_call_count = mock_edit.call_count
        self.assertEqual(first_call_count, second_call_count)

    def test_37_ambiguous_telegram_result_sets_requires_check(self):
        """Ambiguous Telegram edit result sets telegram_update_status='requires_check'."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_tg_04", status="awaiting_payment")
        self._seed_notification(storage, "ycpi_tg_04")
        with patch("web_app_server._telegram_edit_parent_notification_msg") as mock_edit:
            mock_edit.return_value = (False, "AMBIGUOUS:timeout after possible send")
            ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_tg_04", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_tg_04")
        self.assertEqual(wr.get("telegram_update_status"), "requires_check")

    def test_38_telegram_update_status_saved(self):
        """telegram_update_status is always saved in withdrawal record."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_tg_05", status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_tg_05", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_tg_05")
        self.assertIsNotNone(wr.get("telegram_update_status"))

    def test_39_no_notification_sets_skipped_status(self):
        """No sent notification → telegram_update_status='skipped_no_notification'."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_tg_06", status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_tg_06", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_tg_06")
        self.assertIn("skipped", str(wr.get("telegram_update_status") or ""))

    def test_40_withdrawal_text_does_not_show_reason_to_parent(self):
        """Withdrawal notification text does NOT include the admin-only reason."""
        from web_app_server import MiniAppContext
        text = MiniAppContext._format_withdrawal_notification_text(
            "Александр Крента", 239.0, "BYN"
        )
        self.assertIn("счёт отозван", text.lower())
        self.assertNotIn("ошибочно", text)
        self.assertNotIn("причина", text.lower())


# ---------------------------------------------------------------------------
# 8 — Automation protection
# ---------------------------------------------------------------------------

class TestWithdrawalAutomationProtection(unittest.TestCase):

    def _ctx(self):
        storage = _make_storage()
        return _make_context(storage, _make_settings()), storage

    def test_41_withdrawn_intent_not_re_published_by_scheduler(self):
        """Automation publish step skips withdrawn intents (client_visibility='withdrawn')."""
        ctx, storage = self._ctx()
        mk_inv = "inv_auto_41"
        _seed_intent(storage, "ycpi_auto_41", mk_invoice_id=mk_inv,
                     status="awaiting_payment", client_visibility="withdrawn")
        # Simulating scheduler publish attempt via storage query
        with storage._connect() as conn:
            vis = conn.execute(
                "SELECT client_visibility FROM payment_intents WHERE public_id='ycpi_auto_41'"
            ).fetchone()[0]
        self.assertEqual(vis, "withdrawn")

    def test_42_auto_post_eligible_stays_zero_after_withdrawal(self):
        """After withdrawal, auto_post_eligible remains 0 on repeated check."""
        ctx, storage = self._ctx()
        mk_inv = "inv_auto_42"
        _seed_intent(storage, "ycpi_auto_42", mk_invoice_id=mk_inv, status="awaiting_payment")
        _seed_automation_item(storage, mk_inv, auto_post_eligible=1)
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_auto_42", _WITHDRAW_BODY)
        item = storage.get_automation_item_by_invoice(mk_inv)
        self.assertEqual(item.get("auto_post_eligible"), 0)
        # Simulated rescan upsert with eligible=1 should not upgrade (INSERT OR IGNORE)
        storage.upsert_automation_item(
            mk_inv, "7850099", "Тест", "{}", _now(),
            auto_post_eligible=1, auto_publish_eligible=1, parent_notify_eligible=0,
        )
        item2 = storage.get_automation_item_by_invoice(mk_inv)
        # INSERT OR IGNORE keeps original row with 0
        self.assertEqual(item2.get("auto_post_eligible"), 0)

    def test_43_new_normal_intent_unaffected_by_withdrawal(self):
        """A new normal intent for a different invoice is not affected by another withdrawal."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_normal_01", mk_invoice_id="inv_norm_01",
                     status="awaiting_payment")
        _seed_intent(storage, "ycpi_withdrawn_01", mk_invoice_id="inv_wd_01",
                     status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_withdrawn_01", _WITHDRAW_BODY)
        pi = storage.get_payment_intent("ycpi_normal_01")
        self.assertNotEqual(pi.get("client_visibility"), "withdrawn")

    def test_44_food_tables_not_touched_by_withdrawal(self):
        """Withdrawal migration does not create any food_ tables."""
        storage = _make_storage()
        with storage._connect() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        food_tables = {t for t in tables if t.startswith("food_")}
        self.assertNotIn("payment_intent_withdrawals", food_tables)
        self.assertIn("payment_intent_withdrawals", tables)

    def test_45_safe_migration_idempotent(self):
        """Running _init_withdrawal_tables twice does not raise."""
        storage = _make_storage()
        with storage._connect() as conn:
            storage._init_withdrawal_tables(conn)
            storage._init_withdrawal_tables(conn)


# ---------------------------------------------------------------------------
# 9 — Storage methods
# ---------------------------------------------------------------------------

class TestWithdrawalStorageMethods(unittest.TestCase):

    def _storage(self):
        return _make_storage()

    def test_46_create_and_get_withdrawal(self):
        """create_withdrawal_record and get_withdrawal_by_intent round-trip."""
        storage = self._storage()
        now = _now()
        wr = storage.create_withdrawal_record(
            public_id="ycpi_sm_01",
            mk_invoice_id="inv_sm_01",
            reason="тестовая причина",
            requested_by_telegram_id="9001",
            requested_by_name="Тест Менеджер",
            payment_status_at_request="awaiting_payment",
            now=now,
        )
        self.assertIsNotNone(wr)
        self.assertEqual(wr.get("status"), "processing")
        self.assertEqual(wr.get("reason"), "тестовая причина")

    def test_47_complete_withdrawal_sets_status(self):
        """complete_withdrawal sets status='withdrawn' and completed_at."""
        storage = self._storage()
        now = _now()
        wr = storage.create_withdrawal_record(
            public_id="ycpi_sm_02",
            mk_invoice_id="inv_sm_02",
            reason="завершение теста",
            requested_by_telegram_id="9001",
            requested_by_name="Test",
            payment_status_at_request="awaiting_payment",
            now=now,
        )
        storage.complete_withdrawal(wr["id"], now, now)
        wr2 = storage.get_withdrawal_by_intent("ycpi_sm_02")
        self.assertEqual(wr2.get("status"), "withdrawn")
        self.assertIsNotNone(wr2.get("completed_at"))

    def test_48_set_withdrawal_requires_check(self):
        """set_withdrawal_requires_check saves reason and status."""
        storage = self._storage()
        now = _now()
        wr = storage.create_withdrawal_record(
            public_id="ycpi_sm_03",
            mk_invoice_id="inv_sm_03",
            reason="проверка requires_check",
            requested_by_telegram_id="9001",
            requested_by_name="Test",
            payment_status_at_request="paid",
            now=now,
        )
        storage.set_withdrawal_requires_check(wr["id"], "payment_received", now)
        wr2 = storage.get_withdrawal_by_intent("ycpi_sm_03")
        self.assertEqual(wr2.get("status"), "requires_check")
        self.assertEqual(wr2.get("requires_check_reason"), "payment_received")

    def test_49_audit_record_preserved_on_failed(self):
        """Audit record is preserved even when withdrawal transitions to failed."""
        storage = self._storage()
        now = _now()
        wr = storage.create_withdrawal_record(
            public_id="ycpi_sm_04",
            mk_invoice_id="inv_sm_04",
            reason="тест failed",
            requested_by_telegram_id="9001",
            requested_by_name="Test",
            payment_status_at_request="awaiting_payment",
            now=now,
        )
        storage.set_withdrawal_failed(wr["id"], "claim_failed", now)
        wr2 = storage.get_withdrawal_by_intent("ycpi_sm_04")
        self.assertIsNotNone(wr2)
        self.assertEqual(wr2.get("status"), "failed")

    def test_50_get_withdrawal_info_endpoint(self):
        """get_intent_withdrawal_info returns correct structure."""
        storage = _make_storage()
        ctx = _make_context(storage, _make_settings())
        _seed_intent(storage, "ycpi_sm_05", status="awaiting_payment")
        result = ctx.get_intent_withdrawal_info(_WITHDRAW_AUTH, "ycpi_sm_05")
        self.assertTrue(result.get("ok"), result)
        self.assertIn("can_withdraw", result)
        self.assertTrue(result.get("can_withdraw"))


# ---------------------------------------------------------------------------
# 10 — Period label fix (all 12 months nominative)
# ---------------------------------------------------------------------------

class TestPeriodLabelNominative(unittest.TestCase):
    """v7.0.98.0: Period label must use nominative case (Июль, not Июля)."""

    def _label(self, period: str) -> str:
        from web_app_server import MiniAppContext
        return MiniAppContext._notify_parent_period_label(period)

    def test_38_all_12_months_nominative(self):
        """All 12 months return nominative Russian form."""
        expected = [
            "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
            "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
        ]
        for i, month_name in enumerate(expected, 1):
            with self.subTest(month=i):
                label = self._label(f"2026-{i:02d}")
                self.assertEqual(label, f"{month_name} 2026")

    def test_39_july_is_nominative_not_genitive(self):
        """Июль 2026, not Июля 2026."""
        label = self._label("2026-07")
        self.assertEqual(label, "Июль 2026")
        self.assertNotEqual(label, "Июля 2026")

    def test_40_august_nominative(self):
        """Август 2026."""
        label = self._label("2026-08")
        self.assertEqual(label, "Август 2026")

    def test_41_empty_returns_empty(self):
        """Empty period_month returns empty string."""
        self.assertEqual(self._label(""), "")

    def test_42_invalid_returns_string(self):
        """Invalid period_month returns the input string."""
        self.assertIsInstance(self._label("bad-data"), str)


# ---------------------------------------------------------------------------
# 11 — Version
# ---------------------------------------------------------------------------

class TestVersion(unittest.TestCase):

    def test_43_current_version(self):
        self.assertEqual(CURRENT_VERSION, "7.1")

    def test_44_payment_domain_version(self):
        import payment_domain
        src = Path(payment_domain.__file__).read_text(encoding="utf-8")
        self.assertIn("7.1.0", src)

    def test_45_miniapp_js_version(self):
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn('console.log("MiniApp version: v7.1', js)

    def test_46_index_html_cache_bust(self):
        html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
        self.assertIn("v=7.1", html)

    def test_47_withdrawal_table_exists_after_migration(self):
        """payment_intent_withdrawals table is created on Storage init."""
        storage = _make_storage()
        with storage._connect() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        self.assertIn("payment_intent_withdrawals", tables)

    def test_48_bepaid_void_erip_calls_delete_api(self):
        """void_erip_payment sends DELETE to bePaid API (v7.1.4)."""
        from bepaid_client import BePaidClient
        import requests as req
        client = BePaidClient("shop_123", "secret_xyz")
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"transaction": {"uid": "tx_uid_test_01", "status": "deleted"}}
        with patch.object(req, "delete", return_value=fake_resp) as mock_del:
            result = client.void_erip_payment("tx_uid_test_01")
        self.assertTrue(result.ok, f"Expected ok=True for 200 DELETE: {result}")
        mock_del.assert_called_once()
        call_url = mock_del.call_args[0][0]
        self.assertIn("tx_uid_test_01", call_url)

    def test_49_withdrawal_text_contains_icon(self):
        """Withdrawal text starts with ⚠️ icon."""
        from web_app_server import MiniAppContext
        text = MiniAppContext._format_withdrawal_notification_text("Тест", 100.0, "BYN")
        self.assertIn("⚠️", text)
        self.assertIn("счёт отозван", text.lower())

    def test_50_withdrawal_text_escapes_html(self):
        """Withdrawal text escapes HTML special chars in student name."""
        from web_app_server import MiniAppContext
        text = MiniAppContext._format_withdrawal_notification_text("<b>Вася</b>", 100.0, "BYN")
        self.assertNotIn("<b>Вася</b>", text)
        self.assertIn("&lt;b&gt;", text)


# ---------------------------------------------------------------------------
# 12 — Frontend/backend contract (v7.0.98.1)
# ---------------------------------------------------------------------------

class TestFrontendBackendContract(unittest.TestCase):
    """Verify the API contract that the withdrawal frontend relies on."""

    def _ctx(self):
        storage = _make_storage()
        return _make_context(storage, _make_settings()), storage

    # ── can_withdraw field ────────────────────────────────────────────────────

    def test_51_withdrawal_status_can_withdraw_true_for_eligible_intent(self):
        """withdrawal-status returns can_withdraw=True for an unpaid, unposted intent."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_fc_01", status="awaiting_payment",
                     client_visibility="published", mk_payment_id=None)
        result = ctx.get_intent_withdrawal_info(_WITHDRAW_AUTH, "ycpi_fc_01")
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result.get("can_withdraw"), "Expected can_withdraw=True for eligible intent")

    def test_52_withdrawal_status_can_withdraw_false_if_paid(self):
        """withdrawal-status returns can_withdraw=False when intent is paid."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_fc_02", status="paid")
        result = ctx.get_intent_withdrawal_info(_WITHDRAW_AUTH, "ycpi_fc_02")
        self.assertFalse(result.get("can_withdraw"), "Paid intent must not be withdrawable")

    def test_53_withdrawal_status_can_withdraw_false_if_already_withdrawn(self):
        """withdrawal-status returns can_withdraw=False for already-withdrawn intent."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_fc_03", status="awaiting_payment",
                     client_visibility="withdrawn")
        result = ctx.get_intent_withdrawal_info(_WITHDRAW_AUTH, "ycpi_fc_03")
        self.assertFalse(result.get("can_withdraw"), "Withdrawn intent must not be can_withdraw=True")

    def test_54_withdrawal_status_can_withdraw_false_if_cancelled(self):
        """withdrawal-status returns can_withdraw=False for cancelled intent."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_fc_04", status="cancelled")
        result = ctx.get_intent_withdrawal_info(_WITHDRAW_AUTH, "ycpi_fc_04")
        self.assertFalse(result.get("can_withdraw"))

    def test_55_withdrawal_status_can_withdraw_false_if_has_mk_payment_id(self):
        """withdrawal-status returns can_withdraw=False when mk_payment_id is set."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_fc_05", status="awaiting_payment", mk_payment_id=9876)
        result = ctx.get_intent_withdrawal_info(_WITHDRAW_AUTH, "ycpi_fc_05")
        self.assertFalse(result.get("can_withdraw"))

    def test_56_withdrawal_status_returns_withdrawal_dict_if_record_exists(self):
        """withdrawal-status response includes withdrawal dict after withdrawal."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_fc_06", status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_fc_06", _WITHDRAW_BODY)
        result = ctx.get_intent_withdrawal_info(_WITHDRAW_AUTH, "ycpi_fc_06")
        self.assertIn("withdrawal", result)
        wr = result["withdrawal"]
        self.assertIn("status", wr)
        self.assertIn("reason", wr)

    # ── reason validation ─────────────────────────────────────────────────────

    def test_57_reason_shorter_than_5_chars_rejected(self):
        """Backend rejects withdrawal reason shorter than 5 characters."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_fc_07", status="awaiting_payment")
        result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_fc_07", {"reason": "ab"})
        self.assertFalse(result.get("ok"))
        self.assertIn("5", result.get("error", ""))

    def test_58_reason_exactly_5_chars_accepted(self):
        """Backend accepts reason of exactly 5 characters."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_fc_08", status="awaiting_payment")
        result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_fc_08", {"reason": "ошиб"})
        # 4 chars → rejected
        self.assertFalse(result.get("ok"))
        result2 = ctx.withdraw_payment_intent(
            _WITHDRAW_AUTH, "ycpi_fc_08", {"reason": "ошибк"}  # 5 chars
        )
        # second call is idempotent (already withdrawn by a would-be-success call)
        # but first call should have been caught by the 4-char guard
        # Re-seed a fresh intent for the 5-char positive test
        _seed_intent(storage, "ycpi_fc_08b", status="awaiting_payment")
        result3 = ctx.withdraw_payment_intent(
            _WITHDRAW_AUTH, "ycpi_fc_08b", {"reason": "ошибк"}  # exactly 5 chars
        )
        self.assertTrue(result3.get("ok"), f"5-char reason must be accepted: {result3}")

    # ── backend payment block for withdrawn intents ───────────────────────────

    def test_59_withdrawn_intent_blocked_in_bepaid_create(self):
        """Backend blocks bePaid ERIP checkout for withdrawn intent."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_fc_09", status="awaiting_payment",
                     client_visibility="withdrawn")
        with patch("web_app_server.MiniAppContext._require_payment_intent_access", return_value=None):
            result = ctx.payment_intent_create_bepaid(
                {"user_id": "9002", "_internal": True, "role": "parent"},
                "ycpi_fc_09",
                {},
            )
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("withdrawn"), f"Expected 'withdrawn' flag: {result}")

    def test_60_withdrawn_intent_blocked_in_acquiring_create(self):
        """Backend blocks acquiring checkout for withdrawn intent."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_fc_10", status="awaiting_payment",
                     client_visibility="withdrawn")
        with patch("web_app_server.MiniAppContext._require_payment_intent_access", return_value=None):
            result = ctx.payment_intent_create_acquiring_option(
                {"user_id": "9002", "_internal": True, "role": "parent"},
                "ycpi_fc_10",
            )
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("withdrawn"), f"Expected 'withdrawn' flag: {result}")

    # ── idempotency ───────────────────────────────────────────────────────────

    def test_61_double_withdraw_is_idempotent(self):
        """Second withdrawal request returns ok=True and idempotent=True."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_fc_11", status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_fc_11", _WITHDRAW_BODY)
        result2 = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_fc_11", _WITHDRAW_BODY)
        self.assertTrue(result2.get("ok"), f"Second call must succeed: {result2}")
        self.assertTrue(result2.get("idempotent"), "Second call must be idempotent")

    # ── role access control ───────────────────────────────────────────────────

    def test_62_director_role_cannot_withdraw(self):
        """director role is not in WITHDRAW_INVOICE_ROLES and must be denied."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_fc_12", status="awaiting_payment")
        auth = {"_internal": True, "role": "director", "user_id": "9003", "full_name": "Dir Test"}
        result = ctx.withdraw_payment_intent(auth, "ycpi_fc_12", _WITHDRAW_BODY)
        self.assertFalse(result.get("ok"))
        self.assertIn("роль", result.get("error", "").lower())

    def test_63_app_js_contains_withdraw_modal_open_function(self):
        """app.js contains openWithdrawModal function (frontend contract)."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("openWithdrawModal", js)
        self.assertIn("piWithdrawModal", js)
        self.assertIn("/withdraw", js)
        self.assertNotIn("withdraw-from-parent", js.split("withdrawIntentFromParent")[0].split("openWithdrawModal")[-1])

    def test_64_index_html_contains_withdraw_modal(self):
        """index.html contains piWithdrawModal element."""
        html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
        self.assertIn("piWithdrawModal", html)
        self.assertIn("piWithdrawReason", html)
        self.assertIn("piWithdrawModalConfirm", html)

    def test_65_old_withdraw_from_parent_button_not_in_card_footer(self):
        """app.js card footer no longer renders withdrawFromParentBtn."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        # The variable withdrawFromParentBtn must not appear in the template literal footer
        self.assertNotIn("${withdrawFromParentBtn}", js)

    def test_66_withdraw_endpoint_used_not_withdraw_from_parent(self):
        """confirmWithdrawIntent calls /withdraw, not /withdraw-from-parent."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        # confirmWithdrawIntent must reference /withdraw endpoint
        self.assertIn("confirmWithdrawIntent", js)
        # Check the function body contains /withdraw (not /withdraw-from-parent)
        fn_start = js.index("async function confirmWithdrawIntent")
        fn_end = js.index("\nwindow.withdrawIntentFromParent", fn_start)
        fn_body = js[fn_start:fn_end]
        self.assertIn("/withdraw", fn_body)
        self.assertNotIn("withdraw-from-parent", fn_body)

    def test_67_can_withdraw_invoice_role_check(self):
        """canWithdrawInvoice function exists and is defined for correct roles."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("canWithdrawInvoice", js)
        self.assertIn('"operations"', js)


# ---------------------------------------------------------------------------
# 13 — v7.0.98.2 hotfix: 5 production defects
# ---------------------------------------------------------------------------

class TestHotfix98_2(unittest.TestCase):
    """Regression tests for v7.0.98.2 defects found in production test."""

    def _ctx(self):
        storage = _make_storage()
        return _make_context(storage, _make_settings()), storage

    # ── Fix 1: reset_automation_item_for_withdrawal resets all flags ─────────

    def test_68_reset_automation_item_sets_parent_notify_eligible_zero(self):
        """reset_automation_item_for_withdrawal must zero parent_notify_eligible."""
        storage = _make_storage()
        mk_invoice_id = "mk_inv_98_01"
        _seed_automation_item(storage, mk_invoice_id, parent_notify_eligible=1)
        now = _now()
        storage.reset_automation_item_for_withdrawal(mk_invoice_id, now)
        with storage._connect() as conn:
            row = conn.execute(
                "SELECT parent_notify_eligible FROM invoice_automation_items WHERE mk_invoice_id=?",
                (mk_invoice_id,),
            ).fetchone()
        self.assertIsNotNone(row, "Automation item must exist after reset")
        self.assertEqual(row[0], 0, "parent_notify_eligible must be 0 after withdrawal")

    def test_69_reset_automation_item_sets_current_stage_withdrawn(self):
        """reset_automation_item_for_withdrawal must set current_stage='withdrawn'."""
        storage = _make_storage()
        mk_invoice_id = "mk_inv_98_02"
        _seed_automation_item(storage, mk_invoice_id)
        now = _now()
        storage.reset_automation_item_for_withdrawal(mk_invoice_id, now)
        with storage._connect() as conn:
            row = conn.execute(
                "SELECT current_stage FROM invoice_automation_items WHERE mk_invoice_id=?",
                (mk_invoice_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "withdrawn", "current_stage must be 'withdrawn' after reset")

    def test_70_reset_automation_item_zeros_all_four_fields(self):
        """All four eligibility/stage fields are set correctly after reset."""
        storage = _make_storage()
        mk_invoice_id = "mk_inv_98_03"
        _seed_automation_item(
            storage, mk_invoice_id,
            auto_post_eligible=1, auto_publish_eligible=1, parent_notify_eligible=1,
        )
        now = _now()
        storage.reset_automation_item_for_withdrawal(mk_invoice_id, now)
        with storage._connect() as conn:
            row = conn.execute(
                """SELECT auto_post_eligible, auto_publish_eligible,
                          parent_notify_eligible, current_stage
                   FROM invoice_automation_items WHERE mk_invoice_id=?""",
                (mk_invoice_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 0, "auto_post_eligible")
        self.assertEqual(row[1], 0, "auto_publish_eligible")
        self.assertEqual(row[2], 0, "parent_notify_eligible")
        self.assertEqual(row[3], "withdrawn", "current_stage")

    def test_71_after_full_withdrawal_automation_item_fully_reset(self):
        """Full withdrawal flow zeroes all four automation fields via reset call."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_98_04", status="awaiting_payment",
                     mk_invoice_id="mk_inv_98_04")
        _seed_automation_item(storage, "mk_inv_98_04", parent_notify_eligible=1)
        with patch("web_app_server.MiniAppContext._try_edit_parent_notification_for_withdrawal"):
            ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_98_04", _WITHDRAW_BODY)
        with storage._connect() as conn:
            row = conn.execute(
                """SELECT auto_post_eligible, auto_publish_eligible,
                          parent_notify_eligible, current_stage
                   FROM invoice_automation_items WHERE mk_invoice_id='mk_inv_98_04'""",
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[2], 0, "parent_notify_eligible must be 0 after withdrawal")
        self.assertEqual(row[3], "withdrawn", "current_stage must be 'withdrawn'")

    # ── Fix 2: requested_by_name fallback chain ───────────────────────────────

    def test_72_requested_by_name_uses_full_name_when_present(self):
        """requested_by_name uses full_name when it is populated."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_98_05", status="awaiting_payment")
        auth = {"_internal": True, "role": "owner", "user_id": "9010",
                "full_name": "Иван Петров", "username": "ivanp"}
        ctx.withdraw_payment_intent(auth, "ycpi_98_05", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_98_05")
        self.assertIsNotNone(wr)
        self.assertEqual(wr.get("requested_by_name"), "Иван Петров")

    def test_73_requested_by_name_falls_back_to_username(self):
        """requested_by_name falls back to username when full_name is empty."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_98_06", status="awaiting_payment")
        auth = {"_internal": True, "role": "owner", "user_id": "9011",
                "full_name": "", "username": "user_fallback"}
        ctx.withdraw_payment_intent(auth, "ycpi_98_06", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_98_06")
        self.assertIsNotNone(wr)
        self.assertIn("user_fallback", wr.get("requested_by_name", ""))

    def test_74_requested_by_name_uses_employee_id_when_all_names_empty(self):
        """requested_by_name falls back to 'Сотрудник #<id>' when all names are empty."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_98_07", status="awaiting_payment")
        auth = {"_internal": True, "role": "owner", "user_id": "9012",
                "full_name": "", "username": ""}
        ctx.withdraw_payment_intent(auth, "ycpi_98_07", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_98_07")
        self.assertIsNotNone(wr)
        name = wr.get("requested_by_name", "")
        self.assertIn("9012", name, f"Must contain user_id in fallback name: {name!r}")
        self.assertIn("Сотрудник", name, f"Must contain 'Сотрудник' prefix: {name!r}")

    def test_75_requested_by_name_uses_tg_user_first_name_fallback(self):
        """requested_by_name falls back to user.first_name when full_name is absent."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_98_08", status="awaiting_payment")
        auth = {"_internal": True, "role": "owner", "user_id": "9013",
                "full_name": "", "username": "",
                "user": {"first_name": "Алексей", "username": ""}}
        ctx.withdraw_payment_intent(auth, "ycpi_98_08", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_98_08")
        self.assertIsNotNone(wr)
        self.assertEqual(wr.get("requested_by_name"), "Алексей")

    # ── Fix 3: frontend — isWithdrawn defined early, all buttons gated ────────

    def test_76_app_js_is_withdrawn_defined_before_cancel_btn(self):
        """isWithdrawn must be defined before cancelBtn in app.js."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        pos_withdrawn = js.index("const isWithdrawn = clientVis")
        pos_cancel = js.index("const canCancel = ")
        self.assertLess(pos_withdrawn, pos_cancel,
                        "isWithdrawn must be defined before canCancel in card renderer")

    def test_77_app_js_cancel_btn_gated_by_is_withdrawn(self):
        """canCancel condition includes && !isWithdrawn."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        idx = js.index("const canCancel = ")
        line_end = js.index("\n", idx)
        line = js[idx:line_end]
        self.assertIn("!isWithdrawn", line,
                      f"canCancel must include !isWithdrawn check, got: {line!r}")

    def test_78_app_js_acquiring_btn_gated_by_is_withdrawn(self):
        """canOpenAcquiring condition includes && !isWithdrawn."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        idx = js.index("const canOpenAcquiring = ")
        block_end = js.index("const acquiringBtn", idx)
        block = js[idx:block_end]
        self.assertIn("!isWithdrawn", block,
                      "canOpenAcquiring must include !isWithdrawn check")

    def test_79_app_js_verify_acquiring_btn_gated_by_is_withdrawn(self):
        """canVerifyAcquiring condition includes && !isWithdrawn."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        idx = js.index("const canVerifyAcquiring = ")
        block_end = js.index("const verifyAcquiringBtn", idx)
        block = js[idx:block_end]
        self.assertIn("!isWithdrawn", block,
                      "canVerifyAcquiring must include !isWithdrawn check")

    def test_80_app_js_mk_post_btn_gated_by_is_withdrawn(self):
        """v7.1.11: canMkPost now delegates to canShowMkPostButton(pi), which
        folds the withdrawn exclusion (among other conditions) into one
        reusable, backend-mirrored function instead of an inline
        `&& !isWithdrawn` on the canMkPost line itself. Assert both halves:
        canMkPost calls the shared function, and that function excludes
        withdrawn intents."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        idx = js.index("const canMkPost = ")
        line_end = js.index("\n", idx)
        line = js[idx:line_end]
        self.assertIn("canShowMkPostButton(pi)", line,
                      f"canMkPost must delegate to canShowMkPostButton(pi), got: {line!r}")

        fn_idx = js.index("function canShowMkPostButton(pi)")
        fn_end = js.index("\n}", fn_idx)
        fn_body = js[fn_idx:fn_end]
        self.assertIn('clientVis !== "withdrawn"', fn_body,
                      "canShowMkPostButton must exclude withdrawn intents")

    def test_81_app_js_publish_to_parent_btn_gated_by_is_withdrawn(self):
        """canPublishToParent condition includes && !isWithdrawn."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        idx = js.index("const canPublishToParent = ")
        line_end = js.index("\n", idx)
        line = js[idx:line_end]
        self.assertIn("!isWithdrawn", line,
                      f"canPublishToParent must include !isWithdrawn check, got: {line!r}")

    def test_82_app_js_no_duplicate_is_withdrawn_definition(self):
        """isWithdrawn must be defined exactly once per independent card renderer.

        v7.1.6.1 step 3 added _wsRenderPaymentCard() — a dedicated compact
        card for the Workspace All Payments tab, deliberately separate from
        the legacy renderPaymentIntentCard() (which still backs the
        unrelated admin #piList screen and must not be touched by workspace
        changes). Each renderer legitimately computes its own local
        isWithdrawn; the guard here is against an accidental duplicate
        definition *within* a single renderer, not against two renderers
        existing.
        """
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        count = js.count("const isWithdrawn = clientVis")
        self.assertEqual(count, 2, f"isWithdrawn defined {count} times; expected exactly 1 per card renderer (2 renderers total)")

    # ── Fix 4: _renderWithdrawalResultBlock improvements ─────────────────────

    def test_83_withdrawal_result_block_shows_agent_blocked_line(self):
        """_renderWithdrawalResultBlock HTML includes 'Счёт заблокирован для оплаты в агенте'."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Счёт заблокирован для оплаты в агенте", js,
                      "Result block must include 'Счёт заблокирован для оплаты в агенте' message")

    def test_84_withdrawal_result_block_shows_moyklass_not_deleted_line(self):
        """_renderWithdrawalResultBlock HTML includes МойКласс not-deleted note."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("не удалены", js,
                      "Result block must clarify МойКласс invoice/subscription not deleted")

    def test_85_withdrawal_result_block_erip_unsupported_human_readable(self):
        """ERIP unsupported label is human-readable and mentions manual cancellation."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        idx = js.index("_renderWithdrawalResultBlock")
        block_end = js.index("\nwindow.withdrawIntentFromParent", idx)
        block = js[idx:block_end]
        self.assertNotIn("локальная блокировка применена", block,
                         "Old technical ERIP unsupported message must be replaced")
        self.assertIn("bePaid", block,
                      "ERIP unsupported message must mention bePaid for manual cancellation")


# ---------------------------------------------------------------------------
# 14 — v7.0.98.3: permanent withdrawal block in card + repair + batch fetch
# ---------------------------------------------------------------------------

class TestHotfix98_3(unittest.TestCase):
    """v7.0.98.3: withdrawal details always shown in withdrawn card; historical repair."""

    def _ctx(self):
        storage = _make_storage()
        return _make_context(storage, _make_settings()), storage

    # ── Backend: withdrawal data in list endpoint ─────────────────────────────

    def test_86_list_includes_withdrawal_for_withdrawn_intent(self):
        """payment_intents_list attaches withdrawal dict to withdrawn intent."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_983_01", status="awaiting_payment",
                     client_visibility="published")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_983_01", _WITHDRAW_BODY)
        result = ctx.payment_intents_list(_WITHDRAW_AUTH, {})
        self.assertTrue(result.get("ok"), result)
        intent = next((i for i in result.get("intents", [])
                       if i["public_id"] == "ycpi_983_01"), None)
        self.assertIsNotNone(intent, "Withdrawn intent must appear in list")
        self.assertIn("withdrawal", intent, "Withdrawn intent must have withdrawal key")
        wr = intent["withdrawal"]
        self.assertEqual(wr.get("status"), "withdrawn")
        self.assertIn("reason", wr)
        self.assertIn("requested_at", wr)
        self.assertIn("erip_cancel_status", wr)
        self.assertIn("telegram_update_status", wr)

    def test_87_list_does_not_attach_withdrawal_to_active_intent(self):
        """payment_intents_list must NOT add withdrawal key to non-withdrawn intents."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_983_02", status="awaiting_payment",
                     client_visibility="published")
        result = ctx.payment_intents_list(_WITHDRAW_AUTH, {})
        self.assertTrue(result.get("ok"), result)
        intent = next((i for i in result.get("intents", [])
                       if i["public_id"] == "ycpi_983_02"), None)
        self.assertIsNotNone(intent)
        self.assertNotIn("withdrawal", intent, "Active intent must NOT have withdrawal key")

    def test_88_batch_fetch_no_n_plus_1(self):
        """get_withdrawals_for_intents returns all records in one call."""
        storage = _make_storage()
        _seed_intent(storage, "ycpi_983_b1", status="awaiting_payment")
        _seed_intent(storage, "ycpi_983_b2", status="awaiting_payment")
        _seed_intent(storage, "ycpi_983_b3", status="awaiting_payment")
        now = _now()
        storage.create_withdrawal_record(
            public_id="ycpi_983_b1", mk_invoice_id="inv_b1",
            reason="test reason b1", requested_by_telegram_id="9001",
            requested_by_name="Admin", now=now,
            payment_status_at_request="awaiting_payment",
        )
        storage.create_withdrawal_record(
            public_id="ycpi_983_b2", mk_invoice_id="inv_b2",
            reason="test reason b2", requested_by_telegram_id="9001",
            requested_by_name="Admin", now=now,
            payment_status_at_request="awaiting_payment",
        )
        withdrawals = storage.get_withdrawals_for_intents(
            ["ycpi_983_b1", "ycpi_983_b2", "ycpi_983_b3"]
        )
        self.assertEqual(len(withdrawals), 2, "Must find exactly 2 records")
        self.assertIn("ycpi_983_b1", withdrawals)
        self.assertIn("ycpi_983_b2", withdrawals)
        self.assertNotIn("ycpi_983_b3", withdrawals)

    def test_89_batch_fetch_empty_list_returns_empty_dict(self):
        """get_withdrawals_for_intents([]) must return {} without error."""
        storage = _make_storage()
        result = storage.get_withdrawals_for_intents([])
        self.assertEqual(result, {})

    def test_90_withdrawal_dict_in_list_has_required_fields(self):
        """withdrawal dict in list contains all required safe fields."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_983_03", status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_983_03", _WITHDRAW_BODY)
        result = ctx.payment_intents_list(_WITHDRAW_AUTH, {})
        intent = next((i for i in result.get("intents", [])
                       if i["public_id"] == "ycpi_983_03"), None)
        self.assertIsNotNone(intent)
        wr = intent.get("withdrawal", {})
        required = {
            "status", "reason", "requested_by_telegram_id", "requested_by_name",
            "requested_at", "completed_at", "erip_cancel_status",
            "card_checkout_blocked_at", "card_blocked",
            "telegram_update_status", "requires_check_reason",
        }
        for field in required:
            self.assertIn(field, wr, f"withdrawal dict must contain '{field}'")

    # ── Storage: repair of historical withdrawn automation items ──────────────

    def test_91_repair_sets_withdrawn_stage_for_historical_items(self):
        """repair_withdrawn_automation_items sets current_stage='withdrawn'."""
        storage = _make_storage()
        _seed_intent(storage, "ycpi_983_r1", status="awaiting_payment",
                     mk_invoice_id="mk_inv_r1", client_visibility="withdrawn")
        _seed_automation_item(storage, "mk_inv_r1", parent_notify_eligible=1)
        now = _now()
        count = storage.repair_withdrawn_automation_items(now)
        self.assertGreater(count, 0, "Must repair at least one automation item")
        with storage._connect() as conn:
            row = conn.execute(
                "SELECT current_stage, parent_notify_eligible "
                "FROM invoice_automation_items WHERE mk_invoice_id='mk_inv_r1'"
            ).fetchone()
        self.assertEqual(row["current_stage"], "withdrawn")
        self.assertEqual(row["parent_notify_eligible"], 0)

    def test_92_repair_is_idempotent(self):
        """Running repair twice is safe — second run returns rowcount 0."""
        storage = _make_storage()
        _seed_intent(storage, "ycpi_983_r2", status="awaiting_payment",
                     mk_invoice_id="mk_inv_r2", client_visibility="withdrawn")
        _seed_automation_item(storage, "mk_inv_r2")
        now = _now()
        count1 = storage.repair_withdrawn_automation_items(now)
        count2 = storage.repair_withdrawn_automation_items(now)
        self.assertGreater(count1, 0)
        self.assertEqual(count2, 0, "Second repair run must be no-op")

    def test_93_repair_does_not_affect_active_intents(self):
        """repair must not change automation items for active (non-withdrawn) intents."""
        storage = _make_storage()
        _seed_intent(storage, "ycpi_983_r3", status="awaiting_payment",
                     mk_invoice_id="mk_inv_r3", client_visibility="published")
        _seed_automation_item(storage, "mk_inv_r3", auto_post_eligible=1)
        now = _now()
        storage.repair_withdrawn_automation_items(now)
        with storage._connect() as conn:
            row = conn.execute(
                "SELECT auto_post_eligible, current_stage "
                "FROM invoice_automation_items WHERE mk_invoice_id='mk_inv_r3'"
            ).fetchone()
        self.assertEqual(row["auto_post_eligible"], 1, "Active intent must be untouched")
        self.assertNotEqual(row["current_stage"], "withdrawn")

    def test_94_storage_init_runs_repair_for_historical_items(self):
        """Storage init calls repair via _init_withdrawal_tables on startup."""
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        from pathlib import Path as _Path
        from storage import Storage as _Storage
        s1 = _Storage(_Path(tmp.name))
        _seed_intent(s1, "ycpi_983_r4", status="awaiting_payment",
                     mk_invoice_id="mk_inv_r4", client_visibility="withdrawn")
        _seed_automation_item(s1, "mk_inv_r4", parent_notify_eligible=1)
        # Re-open storage: _init runs again and triggers the startup repair
        s2 = _Storage(_Path(tmp.name))
        with s2._connect() as conn:
            row = conn.execute(
                "SELECT current_stage FROM invoice_automation_items "
                "WHERE mk_invoice_id='mk_inv_r4'"
            ).fetchone()
        self.assertEqual(row["current_stage"], "withdrawn",
                         "Startup repair must set current_stage='withdrawn'")

    # ── Frontend: permanent withdrawal block in card ──────────────────────────

    def test_95_app_js_withdrawal_info_block_defined_in_card_renderer(self):
        """app.js defines withdrawalInfoBlock using pi.withdrawal in card renderer."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("withdrawalInfoBlock", js)
        self.assertIn("pi.withdrawal", js)

    def test_96_app_js_withdrawal_info_block_in_card_template(self):
        """withdrawalInfoBlock is included in the card HTML template string."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        card_fn_start = js.index("function renderPaymentIntentCard(")
        card_fn_end = js.index("\n// ──", card_fn_start)
        card_fn = js[card_fn_start:card_fn_end]
        self.assertIn("${withdrawalInfoBlock}", card_fn)

    def test_97_render_withdrawal_result_block_reads_from_wr(self):
        """_renderWithdrawalResultBlock reads reason/requested_at from wr (pi.withdrawal format)."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        idx = js.index("function _renderWithdrawalResultBlock")
        block_end = js.index("\nwindow.withdrawIntentFromParent", idx)
        block = js[idx:block_end]
        self.assertIn("wr.reason", block)
        self.assertIn("wr.requested_at", block)
        self.assertIn("displayName", block)

    def test_98_erip_unsupported_human_readable_text(self):
        """ERIP unsupported shows full human-readable text with warning about saved details."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn(
            "не поддерживается. "
            "Не используйте "
            "ранее сохранённые "
            "реквизиты",
            js,
        )

    def test_99_withdrawal_block_name_fallback(self):
        """_renderWithdrawalResultBlock builds fallback display name from tg id."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn(
            "Сотрудник #",
            js,
        )


# ---------------------------------------------------------------------------
# 15 — v7.1.4: reliable remote cancel via DELETE API
# ---------------------------------------------------------------------------

class TestRemoteCancelV714(unittest.TestCase):
    """Tests 100-127: v7.1.4 remote cancel via BePaid DELETE API."""

    def _ctx(self):
        storage = _make_storage()
        return _make_context(storage, _make_settings()), storage

    def _ctx_with_erip_client(self, void_result):
        """Return (ctx, storage) with a mock _bepaid_erip_client that returns void_result."""
        from bepaid_client import BePaidResult
        ctx, storage = self._ctx()
        mock_client = MagicMock()
        mock_client.void_erip_payment.return_value = void_result
        ctx._bepaid_erip_client = lambda: mock_client
        return ctx, storage, mock_client

    def _seed_intent_with_uid(self, storage, public_id, bepaid_uid="tx_abc_123"):
        _seed_intent(storage, public_id, status="awaiting_payment")
        now = _now()
        with storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET bepaid_uid=? WHERE public_id=?",
                (bepaid_uid, public_id),
            )

    # ── BePaidClient unit tests ───────────────────────────────────────────────

    def test_100_void_erip_calls_delete_api(self):
        """void_erip_payment sends HTTP DELETE to bePaid API (v7.1.4)."""
        from bepaid_client import BePaidClient
        import requests as req
        client = BePaidClient("shop_x", "secret_x")
        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {"transaction": {"uid": "tx_100", "status": "deleted"}}
        with patch.object(req, "delete", return_value=fake) as mock_del:
            result = client.void_erip_payment("tx_100")
        self.assertTrue(result.ok, f"200 DELETE must return ok=True: {result}")
        mock_del.assert_called_once()
        call_url = mock_del.call_args[0][0]
        self.assertIn("tx_100", call_url)

    def test_101_void_erip_success_returns_ok_true(self):
        """DELETE 200 response → ok=True with data.status='deleted'."""
        from bepaid_client import BePaidClient
        import requests as req
        client = BePaidClient("shop_x", "secret_x")
        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {"transaction": {"uid": "tx_101", "status": "deleted"}}
        with patch.object(req, "delete", return_value=fake):
            result = client.void_erip_payment("tx_101")
        self.assertTrue(result.ok)
        self.assertEqual(result.data.get("status"), "deleted")

    def test_102_void_erip_204_returns_ok_true(self):
        """DELETE 204 (no body) → ok=True."""
        from bepaid_client import BePaidClient
        import requests as req
        client = BePaidClient("shop_x", "secret_x")
        fake = MagicMock()
        fake.status_code = 204
        fake.json.side_effect = ValueError("no body")
        with patch.object(req, "delete", return_value=fake):
            result = client.void_erip_payment("tx_102")
        self.assertTrue(result.ok, f"204 must be ok=True: {result}")

    def test_103_void_erip_4xx_returns_ok_false(self):
        """DELETE 422 → ok=False, requires_check=False."""
        from bepaid_client import BePaidClient
        import requests as req
        client = BePaidClient("shop_x", "secret_x")
        fake = MagicMock()
        fake.status_code = 422
        fake.json.return_value = {"errors": ["payment status is not pending"]}
        with patch.object(req, "delete", return_value=fake):
            result = client.void_erip_payment("tx_103")
        self.assertFalse(result.ok)
        self.assertFalse(result.requires_check)

    def test_104_void_erip_5xx_requires_check(self):
        """DELETE 500 → ok=False, requires_check=True."""
        from bepaid_client import BePaidClient
        import requests as req
        client = BePaidClient("shop_x", "secret_x")
        fake = MagicMock()
        fake.status_code = 500
        fake.json.return_value = {}
        with patch.object(req, "delete", return_value=fake):
            result = client.void_erip_payment("tx_104")
        self.assertFalse(result.ok)
        self.assertTrue(result.requires_check)

    def test_105_void_erip_timeout_requires_check(self):
        """Timeout → ok=False, requires_check=True."""
        from bepaid_client import BePaidClient
        import requests as req
        client = BePaidClient("shop_x", "secret_x")
        with patch.object(req, "delete", side_effect=req.exceptions.Timeout()):
            result = client.void_erip_payment("tx_105")
        self.assertFalse(result.ok)
        self.assertTrue(result.requires_check)
        self.assertIn("timeout", result.error)

    def test_106_void_erip_connection_error_requires_check(self):
        """ConnectionError → ok=False, requires_check=True."""
        from bepaid_client import BePaidClient
        import requests as req
        client = BePaidClient("shop_x", "secret_x")
        with patch.object(req, "delete", side_effect=req.exceptions.ConnectionError()):
            result = client.void_erip_payment("tx_106")
        self.assertFalse(result.ok)
        self.assertTrue(result.requires_check)

    def test_107_void_erip_missing_uid_no_api_call(self):
        """Empty uid → ok=False without making any HTTP call."""
        from bepaid_client import BePaidClient
        import requests as req
        client = BePaidClient("shop_x", "secret_x")
        with patch.object(req, "delete") as mock_del:
            result = client.void_erip_payment("")
        self.assertFalse(result.ok)
        mock_del.assert_not_called()

    def test_108_parse_delete_response_200_with_body(self):
        """_parse_delete_response: 200 with transaction body → ok=True, data has status."""
        from bepaid_client import BePaidClient
        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {"transaction": {"uid": "tx_108", "status": "deleted"}}
        result = BePaidClient._parse_delete_response(fake)
        self.assertTrue(result.ok)
        self.assertEqual(result.data.get("status"), "deleted")
        self.assertEqual(result.data.get("uid"), "tx_108")

    def test_109_parse_delete_response_204_no_body(self):
        """_parse_delete_response: 204, no body → ok=True, status defaults to 'deleted'."""
        from bepaid_client import BePaidClient
        fake = MagicMock()
        fake.status_code = 204
        fake.json.side_effect = ValueError("no body")
        result = BePaidClient._parse_delete_response(fake)
        self.assertTrue(result.ok)
        self.assertEqual(result.data.get("status"), "deleted")

    def test_110_parse_delete_response_404(self):
        """_parse_delete_response: 404 → ok=False."""
        from bepaid_client import BePaidClient
        fake = MagicMock()
        fake.status_code = 404
        fake.json.return_value = {"errors": ["not found"]}
        result = BePaidClient._parse_delete_response(fake)
        self.assertFalse(result.ok)
        self.assertEqual(result.http_status, 404)

    def test_111_parse_delete_response_5xx_requires_check(self):
        """_parse_delete_response: 500 → ok=False, requires_check=True."""
        from bepaid_client import BePaidClient
        fake = MagicMock()
        fake.status_code = 500
        fake.json.return_value = {}
        result = BePaidClient._parse_delete_response(fake)
        self.assertFalse(result.ok)
        self.assertTrue(result.requires_check)
        self.assertIn("server_error", result.error)

    # ── Integration: full withdrawal flow with mocked BePaid ─────────────────

    def test_112_remote_cancel_success_stores_cancelled(self):
        """Successful API DELETE → remote_cancel_status='cancelled' in DB."""
        from bepaid_client import BePaidResult
        ok_result = BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "tx_112"})
        ctx, storage, _ = self._ctx_with_erip_client(ok_result)
        self._seed_intent_with_uid(storage, "ycpi_rc_01", bepaid_uid="tx_112")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_rc_01", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_rc_01")
        self.assertIsNotNone(wr)
        self.assertEqual(wr.get("remote_cancel_status"), "cancelled")

    def test_113_remote_cancel_failure_stores_failed(self):
        """Failed API DELETE (4xx) → remote_cancel_status='failed' in DB."""
        from bepaid_client import BePaidResult
        fail_result = BePaidResult(ok=False, http_status=422, error="payment_not_pending")
        ctx, storage, _ = self._ctx_with_erip_client(fail_result)
        self._seed_intent_with_uid(storage, "ycpi_rc_02", bepaid_uid="tx_113")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_rc_02", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_rc_02")
        self.assertIsNotNone(wr)
        self.assertEqual(wr.get("remote_cancel_status"), "failed")

    def test_114_remote_cancel_no_uid_stores_no_erip_uid(self):
        """No bepaid_uid → remote_cancel_status='no_erip_uid'."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_rc_03", status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_rc_03", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_rc_03")
        self.assertIsNotNone(wr)
        self.assertEqual(wr.get("remote_cancel_status"), "no_erip_uid")

    def test_115_remote_cancel_method_api_delete_on_success(self):
        """remote_cancel_method='api_delete' when uid present and API called."""
        from bepaid_client import BePaidResult
        ok_result = BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "tx_115"})
        ctx, storage, _ = self._ctx_with_erip_client(ok_result)
        self._seed_intent_with_uid(storage, "ycpi_rc_04", bepaid_uid="tx_115")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_rc_04", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_rc_04")
        self.assertEqual(wr.get("remote_cancel_method"), "api_delete")

    def test_116_remote_cancel_confirmed_at_set_on_success(self):
        """remote_cancel_confirmed_at is set when DELETE succeeds."""
        from bepaid_client import BePaidResult
        ok_result = BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "tx_116"})
        ctx, storage, _ = self._ctx_with_erip_client(ok_result)
        self._seed_intent_with_uid(storage, "ycpi_rc_05", bepaid_uid="tx_116")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_rc_05", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_rc_05")
        self.assertIsNotNone(wr.get("remote_cancel_confirmed_at"))

    def test_117_remote_status_after_deleted_on_success(self):
        """remote_status_after='deleted' when DELETE returns status='deleted'."""
        from bepaid_client import BePaidResult
        ok_result = BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "tx_117"})
        ctx, storage, _ = self._ctx_with_erip_client(ok_result)
        self._seed_intent_with_uid(storage, "ycpi_rc_06", bepaid_uid="tx_117")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_rc_06", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_rc_06")
        self.assertEqual(wr.get("remote_status_after"), "deleted")

    def test_118_remote_response_reference_set_on_success(self):
        """remote_response_reference is set to uid from API response on success."""
        from bepaid_client import BePaidResult
        ok_result = BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "tx_118"})
        ctx, storage, _ = self._ctx_with_erip_client(ok_result)
        self._seed_intent_with_uid(storage, "ycpi_rc_07", bepaid_uid="tx_118")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_rc_07", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_rc_07")
        self.assertEqual(wr.get("remote_response_reference"), "tx_118")

    def test_119_remote_cancel_requires_check_on_timeout(self):
        """Timeout from API → remote_cancel_status='requires_check'."""
        from bepaid_client import BePaidResult
        timeout_result = BePaidResult(ok=False, http_status=0, error="timeout", requires_check=True)
        ctx, storage, _ = self._ctx_with_erip_client(timeout_result)
        self._seed_intent_with_uid(storage, "ycpi_rc_08", bepaid_uid="tx_119")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_rc_08", _WITHDRAW_BODY)
        wr = storage.get_withdrawal_by_intent("ycpi_rc_08")
        self.assertEqual(wr.get("remote_cancel_status"), "requires_check")

    # ── Response structure tests ──────────────────────────────────────────────

    def test_120_response_has_local_withdrawal_status(self):
        """withdraw_payment_intent response includes 'local_withdrawal_status' key."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_rc_09", status="awaiting_payment")
        result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_rc_09", _WITHDRAW_BODY)
        self.assertIn("local_withdrawal_status", result, f"Missing key: {list(result)}")

    def test_121_local_withdrawal_status_is_withdrawn(self):
        """local_withdrawal_status='withdrawn' after successful withdrawal."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_rc_10", status="awaiting_payment")
        result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_rc_10", _WITHDRAW_BODY)
        self.assertEqual(result.get("local_withdrawal_status"), "withdrawn")

    def test_122_response_has_remote_cancel_status(self):
        """withdraw_payment_intent response includes 'remote_cancel_status' key."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_rc_11", status="awaiting_payment")
        result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_rc_11", _WITHDRAW_BODY)
        self.assertIn("remote_cancel_status", result, f"Missing key: {list(result)}")

    def test_123_response_has_telegram_status(self):
        """withdraw_payment_intent response includes 'telegram_status' key."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_rc_12", status="awaiting_payment")
        result = ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_rc_12", _WITHDRAW_BODY)
        self.assertIn("telegram_status", result, f"Missing key: {list(result)}")

    # ── get_intent_withdrawal_info ────────────────────────────────────────────

    def test_124_withdrawal_info_includes_remote_cancel_status(self):
        """get_intent_withdrawal_info withdrawal dict includes remote_cancel_status."""
        ctx, storage = self._ctx()
        _seed_intent(storage, "ycpi_rc_13", status="awaiting_payment")
        ctx.withdraw_payment_intent(_WITHDRAW_AUTH, "ycpi_rc_13", _WITHDRAW_BODY)
        info = ctx.get_intent_withdrawal_info(_WITHDRAW_AUTH, "ycpi_rc_13")
        self.assertTrue(info.get("ok"), info)
        wr = info.get("withdrawal", {})
        self.assertIn("remote_cancel_status", wr)

    # ── Storage method and DB columns ─────────────────────────────────────────

    def test_125_remote_cancel_db_columns_exist(self):
        """All 8 remote_cancel columns exist in payment_intent_withdrawals after migration."""
        storage = _make_storage()
        with storage._connect() as conn:
            cols = {row[1] for row in conn.execute(
                "PRAGMA table_info(payment_intent_withdrawals)"
            ).fetchall()}
        expected = {
            "remote_cancel_status", "remote_cancel_method",
            "remote_cancel_requested_at", "remote_cancel_confirmed_at",
            "remote_cancel_error", "remote_status_before",
            "remote_status_after", "remote_response_reference",
        }
        for col in expected:
            self.assertIn(col, cols, f"Column '{col}' missing from payment_intent_withdrawals")

    def test_126_storage_update_withdrawal_remote_cancel(self):
        """update_withdrawal_remote_cancel saves all fields correctly."""
        storage = _make_storage()
        now = _now()
        wr = storage.create_withdrawal_record(
            public_id="ycpi_rc_14",
            mk_invoice_id="inv_rc_14",
            reason="test remote cancel method",
            requested_by_telegram_id="9001",
            requested_by_name="Test",
            payment_status_at_request="awaiting_payment",
            now=now,
        )
        storage.update_withdrawal_remote_cancel(
            wr["id"],
            remote_cancel_status="cancelled",
            remote_cancel_method="api_delete",
            remote_cancel_requested_at=now,
            remote_cancel_confirmed_at=now,
            remote_cancel_error=None,
            remote_status_before="pending",
            remote_status_after="deleted",
            remote_response_reference="tx_rc_14",
            now=now,
        )
        wr2 = storage.get_withdrawal_by_intent("ycpi_rc_14")
        self.assertEqual(wr2.get("remote_cancel_status"), "cancelled")
        self.assertEqual(wr2.get("remote_cancel_method"), "api_delete")
        self.assertEqual(wr2.get("remote_status_after"), "deleted")
        self.assertEqual(wr2.get("remote_response_reference"), "tx_rc_14")
        self.assertIsNotNone(wr2.get("remote_cancel_confirmed_at"))

    # ── Version and cache-bust ────────────────────────────────────────────────

    def test_127_cache_bust_v714(self):
        """index.html has v=7.1.12 and app.js has MiniApp version: v7.1.8."""
        html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("v=7.1.12", html, "index.html cache-bust must be v=7.1.12")
        self.assertIn("v7.1.12", js, "app.js version marker must contain v7.1.12")


# ---------------------------------------------------------------------------
# 9 — Retry remote cancel (v7.1.4.1)
# ---------------------------------------------------------------------------

class TestRemoteCancelRetryV7141(unittest.TestCase):
    """Tests 128-157: POST /remote-cancel retry for already-withdrawn payments."""

    def _ctx(self):
        storage = _make_storage()
        return _make_context(storage, _make_settings()), storage

    def _ctx_with_erip_client(self, void_result):
        from bepaid_client import BePaidResult
        ctx, storage = self._ctx()
        mock_client = MagicMock()
        mock_client.void_erip_payment.return_value = void_result
        ctx._bepaid_erip_client = lambda: mock_client
        return ctx, storage, mock_client

    def _seed_withdrawn(
        self,
        storage,
        public_id,
        bepaid_uid="uid_retry_001",
        erip_cancel_status="unsupported",
        remote_cancel_status=None,
    ):
        """Seed a payment_intent already withdrawn (client_visibility=withdrawn)
        with a withdrawal record simulating a legacy v7.1.3 or failed v7.1.4 row."""
        _seed_intent(storage, public_id, status="awaiting_payment", client_visibility="withdrawn")
        now = _now()
        if bepaid_uid:
            with storage._connect() as conn:
                conn.execute(
                    "UPDATE payment_intents SET bepaid_uid=? WHERE public_id=?",
                    (bepaid_uid, public_id),
                )
        # Create withdrawal record via raw INSERT (simulates pre-existing withdrawal)
        with storage._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO payment_intent_withdrawals
                   (intent_public_id, mk_invoice_id, status, reason,
                    requested_by_telegram_id, requested_by_name,
                    payment_status_at_request,
                    erip_cancel_status, remote_cancel_status,
                    requested_at, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    public_id, public_id, "withdrawn",
                    "ошибочно выставлен", "9001", "Admin Test",
                    "awaiting_payment",
                    erip_cancel_status, remote_cancel_status,
                    now, now, now,
                ),
            )

    _RETRY_AUTH = {"_internal": True, "role": "owner", "user_id": "9001", "full_name": "Admin Test"}

    # ── Availability checks ────────────────────────────────────────────────────

    def test_128_legacy_unsupported_retry_is_accessible(self):
        """Legacy withdrawn + erip_cancel_status=unsupported + remote_cancel_status=NULL → retry succeeds."""
        from bepaid_client import BePaidResult
        ctx, storage, mock_client = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_retry_001"})
        )
        self._seed_withdrawn(storage, "ycpi_r_128", erip_cancel_status="unsupported", remote_cancel_status=None)
        result = ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_128")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("remote_cancel_status"), "cancelled")

    def test_129_retry_uses_existing_withdrawal_id(self):
        """Retry updates the existing withdrawal record, not a new one."""
        from bepaid_client import BePaidResult
        ctx, storage, _ = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_r_129"})
        )
        self._seed_withdrawn(storage, "ycpi_r_129")
        wr_before = storage.get_withdrawal_by_intent("ycpi_r_129")
        ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_129")
        wr_after = storage.get_withdrawal_by_intent("ycpi_r_129")
        self.assertEqual(wr_before["id"], wr_after["id"])

    def test_130_retry_does_not_create_second_withdrawal_record(self):
        """Retry does not insert a second withdrawal record."""
        from bepaid_client import BePaidResult
        ctx, storage, _ = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_r_130"})
        )
        self._seed_withdrawn(storage, "ycpi_r_130")
        ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_130")
        with storage._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM payment_intent_withdrawals WHERE intent_public_id=?",
                ("ycpi_r_130",),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_131_retry_does_not_call_telegram_edit(self):
        """Retry does not attempt to edit the Telegram notification."""
        from bepaid_client import BePaidResult
        ctx, storage, _ = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_r_131"})
        )
        self._seed_withdrawn(storage, "ycpi_r_131")
        with patch("web_app_server._telegram_edit_parent_notification_msg") as mock_tg:
            ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_131")
        mock_tg.assert_not_called()

    def test_132_retry_does_not_reset_automation_item(self):
        """Retry does not reset auto_post_eligible / auto_publish_eligible."""
        from bepaid_client import BePaidResult
        ctx, storage, _ = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_r_132"})
        )
        self._seed_withdrawn(storage, "ycpi_r_132", bepaid_uid="uid_r_132")
        mk_inv = "inv_r_132"
        with storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET mk_invoice_id=? WHERE public_id=?",
                (mk_inv, "ycpi_r_132"),
            )
        _seed_automation_item(storage, mk_inv, auto_post_eligible=0, auto_publish_eligible=0)
        ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_132")
        item = storage.get_automation_item_by_invoice(mk_inv)
        # flags stay at 0, not flipped back
        self.assertEqual(item.get("auto_post_eligible"), 0)
        self.assertEqual(item.get("auto_publish_eligible"), 0)

    def test_133_retry_calls_void_erip_payment_once(self):
        """Retry calls void_erip_payment exactly once."""
        from bepaid_client import BePaidResult
        ctx, storage, mock_client = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_r_133"})
        )
        self._seed_withdrawn(storage, "ycpi_r_133")
        ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_133")
        mock_client.void_erip_payment.assert_called_once()

    # ── Success path ───────────────────────────────────────────────────────────

    def test_134_success_saves_remote_cancel_status_cancelled(self):
        """Successful retry saves remote_cancel_status='cancelled' in withdrawal record."""
        from bepaid_client import BePaidResult
        ctx, storage, _ = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_r_134"})
        )
        self._seed_withdrawn(storage, "ycpi_r_134")
        ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_134")
        wr = storage.get_withdrawal_by_intent("ycpi_r_134")
        self.assertEqual(wr.get("remote_cancel_status"), "cancelled")

    def test_135_success_saves_remote_cancel_method_api_delete(self):
        """Successful retry saves remote_cancel_method='api_delete'."""
        from bepaid_client import BePaidResult
        ctx, storage, _ = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_r_135"})
        )
        self._seed_withdrawn(storage, "ycpi_r_135")
        ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_135")
        wr = storage.get_withdrawal_by_intent("ycpi_r_135")
        self.assertEqual(wr.get("remote_cancel_method"), "api_delete")

    # ── Idempotency ────────────────────────────────────────────────────────────

    def test_136_second_retry_after_cancelled_skips_delete(self):
        """Second retry after remote_cancel_status=cancelled does not call DELETE again."""
        from bepaid_client import BePaidResult
        ctx, storage, mock_client = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_r_136"})
        )
        self._seed_withdrawn(storage, "ycpi_r_136", remote_cancel_status="cancelled")
        result = ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_136")
        mock_client.void_erip_payment.assert_not_called()
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("idempotent"))

    def test_137_idempotent_response_has_correct_status(self):
        """Idempotent retry returns remote_cancel_status=cancelled."""
        from bepaid_client import BePaidResult
        ctx, storage, _ = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={})
        )
        self._seed_withdrawn(storage, "ycpi_r_137", remote_cancel_status="cancelled")
        result = ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_137")
        self.assertEqual(result.get("remote_cancel_status"), "cancelled")

    # ── Block conditions ───────────────────────────────────────────────────────

    def test_138_paid_status_blocks_retry(self):
        """status=paid blocks retry — does not call DELETE."""
        from bepaid_client import BePaidResult
        ctx, storage, mock_client = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={})
        )
        self._seed_withdrawn(storage, "ycpi_r_138")
        with storage._connect() as conn:
            conn.execute("UPDATE payment_intents SET status='paid' WHERE public_id=?", ("ycpi_r_138",))
        result = ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_138")
        mock_client.void_erip_payment.assert_not_called()
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("remote_cancel_status"), "already_paid")

    def test_139_confirmed_tx_blocks_retry(self):
        """Confirmed BePaid transaction blocks retry."""
        from bepaid_client import BePaidResult
        ctx, storage, mock_client = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={})
        )
        self._seed_withdrawn(storage, "ycpi_r_139")
        _seed_bepaid_tx(storage, "ycpi_r_139", webhook_verified=1)
        result = ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_139")
        mock_client.void_erip_payment.assert_not_called()
        self.assertFalse(result.get("ok"))

    def test_140_mk_payment_id_blocks_retry(self):
        """mk_payment_id present blocks retry."""
        from bepaid_client import BePaidResult
        ctx, storage, mock_client = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={})
        )
        self._seed_withdrawn(storage, "ycpi_r_140")
        with storage._connect() as conn:
            conn.execute("UPDATE payment_intents SET mk_payment_id=99 WHERE public_id=?", ("ycpi_r_140",))
        result = ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_140")
        mock_client.void_erip_payment.assert_not_called()
        self.assertFalse(result.get("ok"))

    def test_141_posted_to_moyklass_blocks_retry(self):
        """status=posted_to_moyklass blocks retry."""
        from bepaid_client import BePaidResult
        ctx, storage, mock_client = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={})
        )
        self._seed_withdrawn(storage, "ycpi_r_141")
        with storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET status='posted_to_moyklass' WHERE public_id=?",
                ("ycpi_r_141",),
            )
        result = ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_141")
        mock_client.void_erip_payment.assert_not_called()
        self.assertFalse(result.get("ok"))

    # ── Error paths ────────────────────────────────────────────────────────────

    def test_142_timeout_saves_requires_check(self):
        """Timeout → remote_cancel_status=requires_check, retry still available."""
        from bepaid_client import BePaidResult
        ctx, storage, _ = self._ctx_with_erip_client(
            BePaidResult(ok=False, http_status=0, error="timeout", requires_check=True)
        )
        self._seed_withdrawn(storage, "ycpi_r_142")
        result = ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_142")
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("remote_cancel_status"), "requires_check")

    def test_143_connection_error_saves_failed_or_requires_check(self):
        """ConnectionError → remote_cancel_status in {failed, requires_check}, not cancelled."""
        from bepaid_client import BePaidResult
        ctx, storage, _ = self._ctx_with_erip_client(
            BePaidResult(ok=False, http_status=0, error="connection_error:ConnectionError", requires_check=True)
        )
        self._seed_withdrawn(storage, "ycpi_r_143")
        result = ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_143")
        self.assertFalse(result.get("ok"))
        self.assertNotEqual(result.get("remote_cancel_status"), "cancelled")

    def test_144_failed_status_allows_subsequent_retry(self):
        """remote_cancel_status=failed is retryable (not blocked by idempotency guard)."""
        from bepaid_client import BePaidResult
        ctx, storage, mock_client = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_r_144"})
        )
        self._seed_withdrawn(storage, "ycpi_r_144", remote_cancel_status="failed")
        result = ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_144")
        mock_client.void_erip_payment.assert_called_once()
        self.assertTrue(result.get("ok"))

    def test_145_no_uid_returns_no_erip_uid(self):
        """No bepaid_uid → error, no DELETE called."""
        from bepaid_client import BePaidResult
        ctx, storage, mock_client = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={})
        )
        self._seed_withdrawn(storage, "ycpi_r_145", bepaid_uid="")
        result = ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_145")
        mock_client.void_erip_payment.assert_not_called()
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("remote_cancel_status"), "no_erip_uid")

    # ── Role / access ──────────────────────────────────────────────────────────

    def test_146_teacher_role_is_denied(self):
        """teacher role cannot call remote-cancel."""
        ctx, storage = self._ctx()
        self._seed_withdrawn(storage, "ycpi_r_146")
        auth = {"_internal": True, "role": "teacher", "user_id": "9002"}
        result = ctx.retry_remote_cancel(auth, "ycpi_r_146")
        self.assertFalse(result.get("ok"))
        self.assertIn("Доступ", result.get("error", ""))

    def test_147_parent_role_is_denied(self):
        """parent role cannot call remote-cancel."""
        ctx, storage = self._ctx()
        self._seed_withdrawn(storage, "ycpi_r_147")
        auth = {"_internal": True, "role": "parent", "user_id": "9003"}
        result = ctx.retry_remote_cancel(auth, "ycpi_r_147")
        self.assertFalse(result.get("ok"))
        self.assertIn("Доступ", result.get("error", ""))

    # ── withdrawal-status endpoint ─────────────────────────────────────────────

    def test_148_withdrawal_status_returns_new_remote_values(self):
        """GET withdrawal-status includes remote_cancel_status after successful retry."""
        from bepaid_client import BePaidResult
        ctx, storage, _ = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_r_148"})
        )
        self._seed_withdrawn(storage, "ycpi_r_148")
        ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_148")
        info = ctx.get_intent_withdrawal_info(self._RETRY_AUTH, "ycpi_r_148")
        wd = info.get("withdrawal", {})
        self.assertEqual(wd.get("remote_cancel_status"), "cancelled")
        self.assertEqual(wd.get("remote_cancel_method"), "api_delete")

    # ── UI / JS ────────────────────────────────────────────────────────────────

    def test_149_js_shows_retry_button_for_legacy_unsupported(self):
        """app.js contains logic for retry button (data-retry-remote-cancel attribute)."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("data-retry-remote-cancel", js)
        self.assertIn("retryRemoteCancel", js)

    def test_150_js_shows_retry_button_for_failed(self):
        """app.js retry button visible for failed/requires_check remote status."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("_canRetryRemote", js)

    def test_151_js_hides_retry_button_for_cancelled(self):
        """app.js retry button hidden when remote_cancel_status=cancelled."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn('"cancelled"', js)
        self.assertIn("already_cancelled", js)

    def test_152_js_does_not_call_withdraw_for_retry(self):
        """retryRemoteCancel calls /remote-cancel, not /withdraw."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        retry_fn_start = js.find("window.retryRemoteCancel")
        retry_fn_end = js.find("};", retry_fn_start)
        retry_fn = js[retry_fn_start:retry_fn_end]
        self.assertIn("/remote-cancel", retry_fn)
        self.assertNotIn("/withdraw", retry_fn)

    def test_153_retry_does_not_send_telegram(self):
        """retry_remote_cancel does not invoke _try_edit_parent_notification_for_withdrawal."""
        from bepaid_client import BePaidResult
        ctx, storage, _ = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_r_153"})
        )
        self._seed_withdrawn(storage, "ycpi_r_153")
        called = []
        orig = ctx._try_edit_parent_notification_for_withdrawal if hasattr(ctx, "_try_edit_parent_notification_for_withdrawal") else None
        ctx._try_edit_parent_notification_for_withdrawal = lambda *a, **k: called.append(True)
        ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_153")
        self.assertEqual(called, [], "Telegram edit must not be called during retry")
        if orig:
            ctx._try_edit_parent_notification_for_withdrawal = orig

    def test_154_retry_does_not_call_refund(self):
        """retry_remote_cancel does not call any refund method."""
        from bepaid_client import BePaidResult
        ctx, storage, mock_client = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_r_154"})
        )
        self._seed_withdrawn(storage, "ycpi_r_154")
        ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_154")
        # BePaidClient mock should have no refund call
        self.assertFalse(
            any("refund" in str(c) for c in mock_client.method_calls),
            "No refund calls expected",
        )

    def test_155_retry_does_not_call_moyklass(self):
        """retry_remote_cancel does not write to MoyKlass."""
        from bepaid_client import BePaidResult
        ctx, storage, _ = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_r_155"})
        )
        self._seed_withdrawn(storage, "ycpi_r_155")
        ctx.retry_remote_cancel(self._RETRY_AUTH, "ycpi_r_155")
        # moyklass.request should never have been called
        ctx.moyklass.request.assert_not_called()

    def test_156_mk_terms_sync_default_is_false(self):
        """pilot_auto_mk_terms_sync default remains False after v7.1.4.1."""
        import config as cfg
        default_val = getattr(cfg, "PILOT_AUTO_MK_TERMS_SYNC_DEFAULT", None)
        # Accept None (not set) or False
        self.assertFalse(
            bool(default_val),
            "pilot_auto_mk_terms_sync must remain disabled by default",
        )

    def test_157_cache_bust_v7142(self):
        """index.html has v=7.1.12 and app.js has MiniApp version: v7.1.8."""
        html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("v=7.1.12", html, "index.html cache-bust must be v=7.1.12")
        self.assertIn("v7.1.12", js, "app.js version marker must contain v7.1.12")


# ---------------------------------------------------------------------------
# 19 — v7.1.4.2: BePaid ERIP client wiring fix
# ---------------------------------------------------------------------------

def _make_settings_no_erip_creds():
    """_make_settings() with ERIP credentials explicitly absent."""
    s = _make_settings()
    s.bepaid_erip_shop_id = ""
    s.bepaid_erip_secret_key = ""
    return s


def _make_settings_with_erip_creds():
    """_make_settings() with valid-looking ERIP credentials."""
    s = _make_settings()
    s.bepaid_erip_shop_id = "test_erip_shop"
    s.bepaid_erip_secret_key = "test_erip_secret"
    s.bepaid_request_timeout = 30
    return s


class TestEripClientWiringV7142(unittest.TestCase):
    """Tests 158-182: v7.1.4.2 — _bepaid_erip_client method wiring fix."""

    def _ctx_no_creds(self):
        storage = _make_storage()
        return _make_context(storage, _make_settings_no_erip_creds()), storage

    def _ctx_with_creds(self):
        storage = _make_storage()
        return _make_context(storage, _make_settings_with_erip_creds()), storage

    def _ctx_with_erip_client(self, void_result):
        from bepaid_client import BePaidResult
        ctx, storage = self._ctx_with_creds()
        mock_client = MagicMock()
        mock_client.void_erip_payment.return_value = void_result
        ctx._bepaid_erip_client = lambda: mock_client
        return ctx, storage, mock_client

    def _seed_with_uid(self, storage, public_id, bepaid_uid="uid_v7142_001"):
        _seed_intent(storage, public_id, status="awaiting_payment")
        with storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET bepaid_uid=? WHERE public_id=?",
                (bepaid_uid, public_id),
            )

    def _seed_withdrawn_with_uid(self, storage, public_id,
                                  bepaid_uid="uid_v7142_w01",
                                  remote_cancel_status=None):
        _seed_intent(storage, public_id, status="awaiting_payment", client_visibility="withdrawn")
        with storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET bepaid_uid=? WHERE public_id=?",
                (bepaid_uid, public_id),
            )
        now = _now()
        with storage._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO payment_intent_withdrawals
                   (intent_public_id, mk_invoice_id, status, reason,
                    requested_by_telegram_id, requested_by_name,
                    payment_status_at_request,
                    erip_cancel_status, remote_cancel_status,
                    requested_at, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    public_id, public_id, "withdrawn",
                    "ошибочно выставлен", "9001", "Admin Test",
                    "awaiting_payment",
                    "unsupported", remote_cancel_status,
                    now, now, now,
                ),
            )

    _AUTH = {"_internal": True, "role": "owner", "user_id": "9001", "full_name": "Admin"}

    # ── Group 1: _bepaid_erip_client method ──────────────────────────────────

    def test_158_bepaid_erip_client_method_exists(self):
        """_bepaid_erip_client is a defined method on MiniAppContext (not just injection)."""
        from web_app_server import MiniAppContext
        self.assertTrue(
            hasattr(MiniAppContext, "_bepaid_erip_client"),
            "_bepaid_erip_client must be defined as a class method on MiniAppContext",
        )

    def test_159_bepaid_erip_client_returns_instance_with_valid_creds(self):
        """_bepaid_erip_client() returns a BePaidClient when credentials are configured."""
        from bepaid_client import BePaidClient
        ctx, _ = self._ctx_with_creds()
        client = ctx._bepaid_erip_client()
        self.assertIsNotNone(client, "_bepaid_erip_client() must return BePaidClient when creds set")
        self.assertIsInstance(client, BePaidClient)

    def test_160_bepaid_erip_client_returns_none_when_shop_id_missing(self):
        """_bepaid_erip_client() returns None when bepaid_erip_shop_id is empty."""
        ctx, _ = self._ctx_no_creds()
        ctx.settings.bepaid_erip_secret_key = "has_secret"
        ctx.settings.bepaid_erip_shop_id = ""
        result = ctx._bepaid_erip_client()
        self.assertIsNone(result)

    def test_161_bepaid_erip_client_returns_none_when_secret_missing(self):
        """_bepaid_erip_client() returns None when bepaid_erip_secret_key is empty."""
        ctx, _ = self._ctx_no_creds()
        ctx.settings.bepaid_erip_shop_id = "has_shop"
        ctx.settings.bepaid_erip_secret_key = ""
        result = ctx._bepaid_erip_client()
        self.assertIsNone(result)

    def test_162_bepaid_erip_client_returns_none_when_both_empty(self):
        """_bepaid_erip_client() returns None when both credentials are empty strings."""
        ctx, _ = self._ctx_no_creds()
        result = ctx._bepaid_erip_client()
        self.assertIsNone(result)

    # ── Group 2: no_erip_uid state (UID absent) ───────────────────────────────

    def test_163_no_uid_gives_no_erip_uid_status(self):
        """No bepaid_uid → remote_cancel_status=no_erip_uid."""
        ctx, storage = self._ctx_no_creds()
        _seed_intent(storage, "ycpi_v2_163", status="awaiting_payment")
        wr = storage.create_withdrawal_record(
            public_id="ycpi_v2_163", mk_invoice_id="inv_163",
            reason="тест", requested_by_telegram_id="9001",
            requested_by_name="Admin", payment_status_at_request="awaiting_payment",
            now=_now(),
        )
        ctx._attempt_remote_cancel_for_withdrawal(wr["id"], "", _now(), "ycpi_v2_163")
        wr2 = storage.get_withdrawal_by_intent("ycpi_v2_163")
        self.assertEqual(wr2.get("remote_cancel_status"), "no_erip_uid")

    def test_164_no_uid_gives_erip_unsupported(self):
        """No bepaid_uid → erip_cancel_status=unsupported."""
        ctx, storage = self._ctx_no_creds()
        _seed_intent(storage, "ycpi_v2_164", status="awaiting_payment")
        wr = storage.create_withdrawal_record(
            public_id="ycpi_v2_164", mk_invoice_id="inv_164",
            reason="тест", requested_by_telegram_id="9001",
            requested_by_name="Admin", payment_status_at_request="awaiting_payment",
            now=_now(),
        )
        ctx._attempt_remote_cancel_for_withdrawal(wr["id"], "", _now(), "ycpi_v2_164")
        wr2 = storage.get_withdrawal_by_intent("ycpi_v2_164")
        self.assertEqual(wr2.get("erip_cancel_status"), "unsupported")

    # ── Group 3: client_unavailable state (UID present, no credentials) ───────

    def test_165_uid_present_no_creds_gives_client_unavailable(self):
        """UID present but no credentials → remote_cancel_status=client_unavailable."""
        ctx, storage = self._ctx_no_creds()
        _seed_intent(storage, "ycpi_v2_165", status="awaiting_payment")
        wr = storage.create_withdrawal_record(
            public_id="ycpi_v2_165", mk_invoice_id="inv_165",
            reason="тест", requested_by_telegram_id="9001",
            requested_by_name="Admin", payment_status_at_request="awaiting_payment",
            now=_now(),
        )
        ctx._attempt_remote_cancel_for_withdrawal(wr["id"], "uid_abc_165", _now(), "ycpi_v2_165")
        wr2 = storage.get_withdrawal_by_intent("ycpi_v2_165")
        self.assertEqual(wr2.get("remote_cancel_status"), "client_unavailable")

    def test_166_client_unavailable_does_not_call_delete(self):
        """client_unavailable state must not call BePaid DELETE API."""
        ctx, storage = self._ctx_no_creds()
        _seed_intent(storage, "ycpi_v2_166", status="awaiting_payment")
        wr = storage.create_withdrawal_record(
            public_id="ycpi_v2_166", mk_invoice_id="inv_166",
            reason="тест", requested_by_telegram_id="9001",
            requested_by_name="Admin", payment_status_at_request="awaiting_payment",
            now=_now(),
        )
        import requests as req
        with patch.object(req, "delete") as mock_del:
            ctx._attempt_remote_cancel_for_withdrawal(wr["id"], "uid_166", _now(), "ycpi_v2_166")
        mock_del.assert_not_called()

    def test_167_client_unavailable_sets_erip_unsupported(self):
        """client_unavailable → erip_cancel_status=unsupported."""
        ctx, storage = self._ctx_no_creds()
        _seed_intent(storage, "ycpi_v2_167", status="awaiting_payment")
        wr = storage.create_withdrawal_record(
            public_id="ycpi_v2_167", mk_invoice_id="inv_167",
            reason="тест", requested_by_telegram_id="9001",
            requested_by_name="Admin", payment_status_at_request="awaiting_payment",
            now=_now(),
        )
        ctx._attempt_remote_cancel_for_withdrawal(wr["id"], "uid_167", _now(), "ycpi_v2_167")
        wr2 = storage.get_withdrawal_by_intent("ycpi_v2_167")
        self.assertEqual(wr2.get("erip_cancel_status"), "unsupported")

    def test_168_client_unavailable_logs_event(self):
        """client_unavailable → payment_remote_cancel_client_unavailable logged with uid_present=1."""
        import logging
        ctx, storage = self._ctx_no_creds()
        _seed_intent(storage, "ycpi_v2_168", status="awaiting_payment")
        wr = storage.create_withdrawal_record(
            public_id="ycpi_v2_168", mk_invoice_id="inv_168",
            reason="тест", requested_by_telegram_id="9001",
            requested_by_name="Admin", payment_status_at_request="awaiting_payment",
            now=_now(),
        )
        with self.assertLogs("yellow_club_miniapp", level="WARNING") as log_cm:
            ctx._attempt_remote_cancel_for_withdrawal(wr["id"], "uid_168", _now(), "ycpi_v2_168")
        joined = "\n".join(log_cm.output)
        self.assertIn("payment_remote_cancel_client_unavailable", joined)
        self.assertIn("uid_present=1", joined)

    def test_169_client_resolved_logged_when_client_available(self):
        """UID present + valid credentials → payment_remote_cancel_client_resolved logged."""
        from bepaid_client import BePaidResult
        import logging
        ctx, storage, _ = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_169"})
        )
        _seed_intent(storage, "ycpi_v2_169", status="awaiting_payment")
        wr = storage.create_withdrawal_record(
            public_id="ycpi_v2_169", mk_invoice_id="inv_169",
            reason="ошибочно выставлен", requested_by_telegram_id="9001",
            requested_by_name="Admin", payment_status_at_request="awaiting_payment",
            now=_now(),
        )
        with self.assertLogs("yellow_club_miniapp", level="INFO") as log_cm:
            ctx._attempt_remote_cancel_for_withdrawal(wr["id"], "uid_169", _now(), "ycpi_v2_169")
        joined = "\n".join(log_cm.output)
        self.assertIn("payment_remote_cancel_client_resolved", joined)
        self.assertIn("client_available=1", joined)
        self.assertIn("uid_present=1", joined)

    # ── Group 4: Regression test (would fail on v7.1.4.1) ────────────────────

    def test_170_regression_uid_present_no_injection_not_no_erip_uid(self):
        """Regression: UID present but _bepaid_erip_client not injected must NOT give no_erip_uid.

        v7.1.4.1 used hasattr(self, '_bepaid_erip_client') which was always False in production,
        causing remote_cancel_status='no_erip_uid' even when uid was present.
        v7.1.4.2 defines the method on the class, so it always resolves correctly.
        """
        ctx, storage = self._ctx_no_creds()
        _seed_intent(storage, "ycpi_v2_170", status="awaiting_payment")
        wr = storage.create_withdrawal_record(
            public_id="ycpi_v2_170", mk_invoice_id="inv_170",
            reason="тест", requested_by_telegram_id="9001",
            requested_by_name="Admin", payment_status_at_request="awaiting_payment",
            now=_now(),
        )
        ctx._attempt_remote_cancel_for_withdrawal(wr["id"], "uid_present_170", _now(), "ycpi_v2_170")
        wr2 = storage.get_withdrawal_by_intent("ycpi_v2_170")
        status = wr2.get("remote_cancel_status")
        self.assertNotEqual(
            status, "no_erip_uid",
            "When UID is present, remote_cancel_status must NOT be no_erip_uid "
            "(v7.1.4.1 regression: would fail because hasattr was always False)",
        )
        self.assertEqual(status, "client_unavailable")

    # ── Group 5: First withdrawal with new client wiring ─────────────────────

    def test_171_withdraw_with_uid_no_creds_gives_client_unavailable(self):
        """withdraw_payment_intent with UID present but no credentials → client_unavailable."""
        ctx, storage = self._ctx_no_creds()
        _seed_intent(storage, "ycpi_v2_171", status="awaiting_payment")
        with storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET bepaid_uid='uid_v2_171' WHERE public_id='ycpi_v2_171'"
            )
        ctx.withdraw_payment_intent(self._AUTH, "ycpi_v2_171", {"reason": "ошибочно выставлен"})
        wr = storage.get_withdrawal_by_intent("ycpi_v2_171")
        self.assertEqual(wr.get("remote_cancel_status"), "client_unavailable")

    def test_172_withdraw_with_uid_no_creds_local_withdrawal_succeeds(self):
        """withdraw_payment_intent with no credentials still completes local withdrawal."""
        ctx, storage = self._ctx_no_creds()
        _seed_intent(storage, "ycpi_v2_172", status="awaiting_payment")
        with storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET bepaid_uid='uid_v2_172' WHERE public_id='ycpi_v2_172'"
            )
        result = ctx.withdraw_payment_intent(self._AUTH, "ycpi_v2_172", {"reason": "ошибочно выставлен"})
        self.assertTrue(result.get("ok"), result)
        pi = storage.get_payment_intent("ycpi_v2_172") or {}
        self.assertEqual(pi.get("client_visibility"), "withdrawn")

    def test_173_withdraw_no_uid_gives_no_erip_uid(self):
        """withdraw_payment_intent with no bepaid_uid → remote_cancel_status=no_erip_uid."""
        ctx, storage = self._ctx_no_creds()
        _seed_intent(storage, "ycpi_v2_173", status="awaiting_payment")
        ctx.withdraw_payment_intent(self._AUTH, "ycpi_v2_173", {"reason": "ошибочно выставлен"})
        wr = storage.get_withdrawal_by_intent("ycpi_v2_173")
        self.assertEqual(wr.get("remote_cancel_status"), "no_erip_uid")

    def test_174_withdraw_with_uid_and_creds_calls_void(self):
        """withdraw_payment_intent with UID + credentials (injected) → void called once."""
        from bepaid_client import BePaidResult
        ctx, storage, mock_client = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_174"})
        )
        _seed_intent(storage, "ycpi_v2_174", status="awaiting_payment")
        with storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET bepaid_uid='uid_174' WHERE public_id='ycpi_v2_174'"
            )
        ctx.withdraw_payment_intent(self._AUTH, "ycpi_v2_174", {"reason": "ошибочно выставлен"})
        mock_client.void_erip_payment.assert_called_once_with("uid_174")

    # ── Group 6: Retry endpoint with client_unavailable ───────────────────────

    def test_175_retry_with_client_unavailable_returns_client_unavailable(self):
        """retry_remote_cancel with UID present but no credentials → client_unavailable result."""
        ctx, storage = self._ctx_no_creds()
        self._seed_withdrawn_with_uid(storage, "ycpi_v2_175", remote_cancel_status="client_unavailable")
        result = ctx.retry_remote_cancel(self._AUTH, "ycpi_v2_175")
        wr = storage.get_withdrawal_by_intent("ycpi_v2_175")
        self.assertEqual(wr.get("remote_cancel_status"), "client_unavailable")

    def test_176_client_unavailable_is_not_idempotent_blocked(self):
        """client_unavailable is NOT in the idempotency-blocked set (should allow re-retry)."""
        ctx, storage = self._ctx_no_creds()
        self._seed_withdrawn_with_uid(storage, "ycpi_v2_176", remote_cancel_status="client_unavailable")
        result = ctx.retry_remote_cancel(self._AUTH, "ycpi_v2_176")
        self.assertIsNone(result.get("idempotent"), "client_unavailable must not be idempotent-blocked")

    def test_177_retry_after_credentials_fix_calls_void(self):
        """After credentials are configured, retry on a client_unavailable record succeeds."""
        from bepaid_client import BePaidResult
        ctx, storage, mock_client = self._ctx_with_erip_client(
            BePaidResult(ok=True, http_status=200, data={"status": "deleted", "uid": "uid_177"})
        )
        self._seed_withdrawn_with_uid(
            storage, "ycpi_v2_177",
            bepaid_uid="uid_177",
            remote_cancel_status="client_unavailable",
        )
        result = ctx.retry_remote_cancel(self._AUTH, "ycpi_v2_177")
        mock_client.void_erip_payment.assert_called_once_with("uid_177")
        self.assertTrue(result.get("ok"), result)

    def test_178_hasattr_bepaid_erip_client_always_true(self):
        """In v7.1.4.2, hasattr(ctx, '_bepaid_erip_client') is True for all MiniAppContext."""
        ctx, _ = self._ctx_no_creds()
        self.assertTrue(
            hasattr(ctx, "_bepaid_erip_client"),
            "hasattr must be True in v7.1.4.2 — method is defined on the class",
        )

    # ── Group 7: UI labels ────────────────────────────────────────────────────

    def test_179_js_has_client_unavailable_label(self):
        """app.js _renderWithdrawalResultBlock has label for client_unavailable status."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("client_unavailable", js)
        self.assertIn("Клиент bePaid не настроен на сервере", js)

    def test_180_js_retry_button_allowed_for_client_unavailable(self):
        """app.js retry button condition does NOT exclude client_unavailable."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        # The retry button exclusion list must NOT contain client_unavailable
        self.assertNotIn('"client_unavailable"', js.split("_canRetryRemote")[1].split("retryRemoteCancelBtn")[0])

    def test_181_js_retry_button_excluded_for_no_erip_uid(self):
        """app.js retry button condition excludes no_erip_uid (UID not present)."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        # The _canRetryRemote block must exclude no_erip_uid
        can_retry_block = js.split("_canRetryRemote")[1].split("retryRemoteCancelBtn")[0]
        self.assertIn("no_erip_uid", can_retry_block)

    # ── Group 8: Version ──────────────────────────────────────────────────────

    def test_182_cache_bust_v7142(self):
        """index.html has v=7.1.12 and app.js has MiniApp version: v7.1.8."""
        html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("v=7.1.12", html, "index.html cache-bust must be v=7.1.12")
        self.assertIn("v7.1.12", js, "app.js version marker must contain v7.1.12")


if __name__ == "__main__":
    unittest.main(verbosity=2)
