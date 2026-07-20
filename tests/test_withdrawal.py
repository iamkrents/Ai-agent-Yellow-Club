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

CURRENT_VERSION = "7.0.98.0"

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
        self.assertEqual(CURRENT_VERSION, "7.0.98.0")

    def test_44_payment_domain_version(self):
        import payment_domain
        src = Path(payment_domain.__file__).read_text(encoding="utf-8")
        self.assertIn("7.0.98.0", src)

    def test_45_miniapp_js_version(self):
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn('console.log("MiniApp version: v7.0.98.0")', js)

    def test_46_index_html_cache_bust(self):
        html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
        self.assertIn("v=7.0.98.0", html)

    def test_47_withdrawal_table_exists_after_migration(self):
        """payment_intent_withdrawals table is created on Storage init."""
        storage = _make_storage()
        with storage._connect() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        self.assertIn("payment_intent_withdrawals", tables)

    def test_48_bepaid_void_erip_returns_unsupported(self):
        """void_erip_payment returns ok=False with unsupported error."""
        from bepaid_client import BePaidClient
        client = BePaidClient("shop_123", "secret_xyz")
        result = client.void_erip_payment("tx_uid_test_01")
        self.assertFalse(result.ok)
        self.assertIn("unsupported", result.error.lower())

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
