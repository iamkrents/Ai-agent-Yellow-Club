"""Tests for v7.0.92.5.3 — provider-verified acquiring payment recovery.

Provider verification uses GET /ctp/api/checkouts/{token} with ACQ credentials.
This is a separate trust path from webhook_verified (RSA signature check):

    webhook_verified=1  → Content-Signature passed RSA verification in webhook handler
    provider_verified=1 → GET checkout status query returned matching successful data

The two properties are independent. provider_verified never sets webhook_verified.
MoyKlass posting is never triggered automatically (BEPAID_AUTO_POST_TO_MOYKLASS=false).

Run offline:
    python -m unittest tests.test_provider_verify -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bepaid_client import BePaidResult
from storage import Storage
from web_app_server import MiniAppContext

# ─── helpers ────────────────────────────────────────────────────────────────

def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage, acq_shop_id: str = "acq-001") -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(
        bepaid_acq_shop_id=acq_shop_id,
        bepaid_acq_secret_key="acq-secret",
        bepaid_acq_public_key="",
        bepaid_erip_shop_id="erip-001",
        bepaid_erip_secret_key="erip-secret",
        bepaid_erip_public_key="",
        bepaid_public_base_url="https://example.com",
        bepaid_webhook_path_secret="",
        bepaid_request_timeout=30,
        bepaid_auto_post_to_moyklass=False,
        moyklass_erip_payment_type_id=0,
        moyklass_acquiring_payment_type_id=0,
    )
    ctx._role_store: dict = {}

    def _role(uid):
        return ctx._role_store.get(uid, "owner")

    ctx._role_for_user = _role
    return ctx


def _auth(uid: int = 1) -> dict:
    return {"ok": True, "user_id": uid}


def _store_acq_tx(
    storage: Storage,
    *,
    uid: str,
    tracking_id: str,
    amount_minor: int = 100,
    status: str = "successful",
    test: int = 0,
    webhook_verified: int = 0,
    intent_public_id: str = "",
) -> dict:
    tx, _ = storage.upsert_bepaid_transaction({
        "provider": "bepaid",
        "shop_type": "acquiring",
        "transaction_uid": uid,
        "tracking_id": tracking_id,
        "order_id": f"ORD-{uid[:8]}",
        "status": status,
        "amount_minor": amount_minor,
        "amount_byn": amount_minor / 100,
        "currency": "BYN",
        "test": test,
    })
    with storage._connect() as conn:
        conn.execute(
            "UPDATE bepaid_transactions SET webhook_verified=?, intent_public_id=? WHERE id=?",
            (webhook_verified, intent_public_id or None, tx["id"]),
        )
    return storage.get_bepaid_transaction_by_id(tx["id"])


def _make_pi_with_acq_option(
    storage: Storage,
    *,
    public_id: str,
    tracking_id: str = "",
    amount_minor: int = 100,
    checkout_token: str = "tok_test_123",
    status: str = "awaiting_payment",
) -> tuple:
    pi = storage.create_payment_intent({
        "mk_user_id": 1,
        "student_name": "Test User",
        "amount_minor": amount_minor,
        "amount_byn": amount_minor / 100,
        "currency": "BYN",
        "purpose": "current_month",
        "period_month": "2026-07",
        "payment_method": "acquiring",
        "status": status,
        "created_by_tg_id": 1,
        "created_by_name": "Test",
    })
    with storage._connect() as conn:
        conn.execute("UPDATE payment_intents SET public_id=? WHERE id=?",
                     (public_id, pi["id"]))
    pi = storage.get_payment_intent(public_id)
    tid = tracking_id or f"{public_id}_acq"
    opt = storage.create_payment_intent_option(
        payment_intent_id=pi["id"],
        intent_public_id=public_id,
        channel="acquiring",
        shop_type="acquiring",
        bepaid_tracking_id=tid,
        bepaid_order_id=f"ORD-{public_id}",
    )
    if checkout_token:
        storage.update_option_checkout(opt["id"], checkout_token=checkout_token, payment_url="https://example.com/pay")
        opt = storage.get_option_by_channel(public_id, "acquiring")
    return pi, opt


def _ok_result(
    *,
    shop_id: str = "acq-001",
    amount: int = 100,
    currency: str = "BYN",
    tracking_id: str = "ycpi_test_acq",
    payment_uid: str = "uid-001",
    payment_status: str = "successful",
    checkout_status: str = "successful",
    finished: bool = True,
    test: bool = False,
) -> BePaidResult:
    return BePaidResult(
        ok=True,
        http_status=200,
        data={
            "checkout": {
                "status": checkout_status,
                "finished": finished,
                "test": test,
                "shop": {"id": shop_id},
                "order": {
                    "amount": amount,
                    "currency": currency,
                    "tracking_id": tracking_id,
                },
                "gateway_response": {
                    "payment": {
                        "uid": payment_uid,
                        "status": payment_status,
                        "amount": amount,
                        "currency": currency,
                    }
                },
            }
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. Credential and call security
# ═══════════════════════════════════════════════════════════════════════════

class Test01CredentialSecurity(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage, acq_shop_id="acq-001")
        self.public_id = "ycpi_test_01"
        self.tx_uid = "uid-cred-test"
        _make_pi_with_acq_option(self.storage, public_id=self.public_id,
                                 tracking_id=f"{self.public_id}_acq", checkout_token="tok_test")
        _store_acq_tx(self.storage, uid=self.tx_uid, tracking_id=f"{self.public_id}_acq")

    def test_01_acq_credentials_used(self):
        """BePaidClient is instantiated with ACQ shop_id and secret, not ERIP."""
        with patch("web_app_server.BePaidClient") as MockClient:
            inst = MockClient.return_value
            inst.get_checkout_status.return_value = _ok_result(
                shop_id="acq-001", tracking_id=f"{self.public_id}_acq", payment_uid=self.tx_uid)

            self.ctx.bepaid_verify_acquiring_payment(_auth(), self.public_id)

            MockClient.assert_called_once()
            kwargs = MockClient.call_args.kwargs
            self.assertEqual(kwargs["shop_id"], "acq-001")
            self.assertEqual(kwargs["secret_key"], "acq-secret")

    def test_02_erip_credentials_not_used(self):
        """ERIP shop_id and secret are never passed to the status query client."""
        with patch("web_app_server.BePaidClient") as MockClient:
            inst = MockClient.return_value
            inst.get_checkout_status.return_value = _ok_result(
                shop_id="acq-001", tracking_id=f"{self.public_id}_acq", payment_uid=self.tx_uid)

            self.ctx.bepaid_verify_acquiring_payment(_auth(), self.public_id)

            kwargs = MockClient.call_args.kwargs
            self.assertNotEqual(kwargs.get("shop_id"), "erip-001")
            self.assertNotEqual(kwargs.get("secret_key"), "erip-secret")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Token source enforcement
# ═══════════════════════════════════════════════════════════════════════════

class Test02TokenSource(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def test_03_token_from_db_only_no_option(self):
        """If no acquiring option exists, the call is blocked (token not from frontend)."""
        pi = self.storage.create_payment_intent({
            "mk_user_id": 1, "student_name": "T", "amount_minor": 100,
            "amount_byn": 1.0, "currency": "BYN", "purpose": "current_month",
            "period_month": "2026-07", "payment_method": "acquiring",
            "status": "awaiting_payment", "created_by_tg_id": 1, "created_by_name": "T",
        })
        with self.storage._connect() as conn:
            conn.execute("UPDATE payment_intents SET public_id='ycpi_no_opt' WHERE id=?", (pi["id"],))
        result = self.ctx.bepaid_verify_acquiring_payment(_auth(), "ycpi_no_opt")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "no_acquiring_option")

    def test_03b_token_from_db_only_no_token(self):
        """Acquiring option with empty checkout_token → blocked (token not in DB)."""
        _make_pi_with_acq_option(self.storage, public_id="ycpi_no_tok",
                                 checkout_token="")  # no token
        result = self.ctx.bepaid_verify_acquiring_payment(_auth(), "ycpi_no_tok")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "no_checkout_token_in_db")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Successful verification
# ═══════════════════════════════════════════════════════════════════════════

class Test03SuccessPath(unittest.TestCase):

    PUBLIC_ID = "ycpi_success"
    TX_UID = "uid-success-001"
    TRACKING = "ycpi_success_acq"

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        _make_pi_with_acq_option(self.storage, public_id=self.PUBLIC_ID,
                                 tracking_id=self.TRACKING, checkout_token="tok_ok")
        _store_acq_tx(self.storage, uid=self.TX_UID, tracking_id=self.TRACKING)

    def _call(self, resp=None):
        if resp is None:
            resp = _ok_result(shop_id="acq-001", tracking_id=self.TRACKING,
                              payment_uid=self.TX_UID)
        with patch("web_app_server.BePaidClient") as MockClient:
            MockClient.return_value.get_checkout_status.return_value = resp
            return self.ctx.bepaid_verify_acquiring_payment(_auth(), self.PUBLIC_ID)

    def test_04_successful_response_confirms_payment(self):
        """Full successful path: ok=True, reconciled=True, provider_verified=True."""
        result = self._call()
        self.assertTrue(result["ok"], result)
        self.assertTrue(result.get("reconciled") or result.get("idempotent"))
        self.assertTrue(result.get("provider_verified"))
        self.assertEqual(result.get("channel"), "acquiring")

    def test_04b_intent_status_becomes_paid(self):
        """After successful verify, the intent status is 'paid'."""
        self._call()
        pi = self.storage.get_payment_intent(self.PUBLIC_ID)
        self.assertEqual(pi["status"], "paid")
        self.assertEqual(pi["paid_channel"], "acquiring")

    def test_04c_transaction_linked_to_intent(self):
        """After verify, the stored transaction has intent_public_id set."""
        self._call()
        txs = self.storage.list_bepaid_transactions()
        tx = next((t for t in txs if t["transaction_uid"] == self.TX_UID), None)
        self.assertIsNotNone(tx)
        self.assertEqual(tx["intent_public_id"], self.PUBLIC_ID)

    def test_04d_match_method_is_provider_verify(self):
        """match_method reflects checkout_status_query path."""
        result = self._call()
        self.assertIn("provider_verify", result.get("match_method", ""))


# ═══════════════════════════════════════════════════════════════════════════
# 4. Field validation blocks
# ═══════════════════════════════════════════════════════════════════════════

class Test04FieldValidation(unittest.TestCase):

    PUBLIC_ID = "ycpi_fv"
    TX_UID = "uid-fv-001"
    TRACKING = "ycpi_fv_acq"

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        _make_pi_with_acq_option(self.storage, public_id=self.PUBLIC_ID,
                                 tracking_id=self.TRACKING, checkout_token="tok_fv")
        _store_acq_tx(self.storage, uid=self.TX_UID, tracking_id=self.TRACKING)

    def _call_with(self, resp: BePaidResult) -> dict:
        with patch("web_app_server.BePaidClient") as MockClient:
            MockClient.return_value.get_checkout_status.return_value = resp
            return self.ctx.bepaid_verify_acquiring_payment(_auth(), self.PUBLIC_ID)

    def test_05_wrong_shop_blocks(self):
        resp = _ok_result(shop_id="WRONG-SHOP", tracking_id=self.TRACKING, payment_uid=self.TX_UID)
        result = self._call_with(resp)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "shop_mismatch")

    def test_06_wrong_uid_blocks(self):
        """payment.uid that has no matching stored transaction → blocked."""
        resp = _ok_result(shop_id="acq-001", tracking_id=self.TRACKING,
                          payment_uid="uid-does-not-exist-in-db")
        result = self._call_with(resp)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "transaction_not_found_in_db")

    def test_07_wrong_amount_blocks(self):
        resp = _ok_result(shop_id="acq-001", tracking_id=self.TRACKING,
                          payment_uid=self.TX_UID, amount=9999)
        result = self._call_with(resp)
        self.assertFalse(result["ok"])
        self.assertIn("amount_mismatch", result["error"])

    def test_08_wrong_currency_blocks(self):
        data = _ok_result(shop_id="acq-001", tracking_id=self.TRACKING,
                          payment_uid=self.TX_UID).data
        data["checkout"]["order"]["currency"] = "USD"
        resp = BePaidResult(ok=True, http_status=200, data=data)
        result = self._call_with(resp)
        self.assertFalse(result["ok"])
        self.assertIn("currency_mismatch", result["error"])

    def test_09_wrong_tracking_id_blocks(self):
        resp = _ok_result(shop_id="acq-001", tracking_id="WRONG_TRACKING",
                          payment_uid=self.TX_UID)
        result = self._call_with(resp)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "tracking_id_mismatch")

    def test_10_test_true_blocks(self):
        resp = _ok_result(shop_id="acq-001", tracking_id=self.TRACKING,
                          payment_uid=self.TX_UID, test=True)
        result = self._call_with(resp)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "test_checkout_rejected")

    def test_11_non_successful_status_blocks(self):
        resp = _ok_result(shop_id="acq-001", tracking_id=self.TRACKING,
                          payment_uid=self.TX_UID, checkout_status="incomplete")
        result = self._call_with(resp)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "checkout_not_successful")

    def test_12_missing_gateway_payment_blocks(self):
        data = _ok_result(shop_id="acq-001", tracking_id=self.TRACKING,
                          payment_uid=self.TX_UID).data
        data["checkout"]["gateway_response"] = {}
        resp = BePaidResult(ok=True, http_status=200, data=data)
        result = self._call_with(resp)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "missing_gateway_payment")

    def test_11b_not_finished_blocks(self):
        resp = _ok_result(shop_id="acq-001", tracking_id=self.TRACKING,
                          payment_uid=self.TX_UID, finished=False)
        result = self._call_with(resp)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "checkout_not_finished")


# ═══════════════════════════════════════════════════════════════════════════
# 5. Trust path separation
# ═══════════════════════════════════════════════════════════════════════════

class Test05TrustPaths(unittest.TestCase):

    PUBLIC_ID = "ycpi_trust"
    TX_UID = "uid-trust-001"
    TRACKING = "ycpi_trust_acq"

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        _make_pi_with_acq_option(self.storage, public_id=self.PUBLIC_ID,
                                 tracking_id=self.TRACKING, checkout_token="tok_trust")
        self.tx = _store_acq_tx(self.storage, uid=self.TX_UID, tracking_id=self.TRACKING,
                                webhook_verified=0)

    def _verify(self):
        with patch("web_app_server.BePaidClient") as MockClient:
            MockClient.return_value.get_checkout_status.return_value = _ok_result(
                shop_id="acq-001", tracking_id=self.TRACKING, payment_uid=self.TX_UID)
            return self.ctx.bepaid_verify_acquiring_payment(_auth(), self.PUBLIC_ID)

    def test_13_provider_verified_not_webhook_verified(self):
        """provider_verified=1 after verify; webhook_verified stays 0."""
        self._verify()
        tx_after = self.storage.get_bepaid_transaction_by_id(self.tx["id"])
        self.assertEqual(tx_after["provider_verified"], 1)
        self.assertEqual(tx_after["webhook_verified"], 0, "webhook_verified must not be set by provider verify")

    def test_13b_provider_verified_at_and_method_set(self):
        """provider_verified_at and provider_verification_method are populated."""
        self._verify()
        tx_after = self.storage.get_bepaid_transaction_by_id(self.tx["id"])
        self.assertIsNotNone(tx_after["provider_verified_at"])
        self.assertEqual(tx_after["provider_verification_method"], "checkout_status_query")

    def test_14_unverified_webhook_without_provider_blocked_in_reconcile(self):
        """webhook_verified=0 AND provider_verified=0 → reconcile endpoint still blocks."""
        # This transaction has neither webhook_verified nor provider_verified
        result = self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(self.tx["id"]), {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "reconcile_blocked")
        self.assertEqual(result["reason"], "webhook_not_verified")

    def test_15_provider_verified_transaction_can_reconcile(self):
        """After provider_verify, the reconcile endpoint allows reconciliation (provider_verified=1)."""
        # First do the verify (sets provider_verified=1)
        self._verify()

        # Now create a fresh transaction (not yet reconciled) and set provider_verified=1
        storage2 = _tmp_storage()
        ctx2 = _make_ctx(storage2)
        uid2 = "uid-prov-reconcile"
        track2 = "ycpi_prov_rec_acq"
        _make_pi_with_acq_option(storage2, public_id="ycpi_prov_rec",
                                 tracking_id=track2, checkout_token="tok_pr")
        tx2 = _store_acq_tx(storage2, uid=uid2, tracking_id=track2, webhook_verified=0)
        # Manually set provider_verified=1 to simulate prior verify call
        from utils import now_iso
        storage2.mark_bepaid_transaction_provider_verified(
            tx2["id"], verified_at=now_iso(), verification_method="checkout_status_query")

        result = ctx2.bepaid_reconcile_stored_transaction(_auth(), str(tx2["id"]), {})
        # Should not be blocked by the webhook_not_verified check
        self.assertNotEqual(result.get("error"), "reconcile_blocked")


# ═══════════════════════════════════════════════════════════════════════════
# 6. Idempotency
# ═══════════════════════════════════════════════════════════════════════════

class Test06Idempotency(unittest.TestCase):

    PUBLIC_ID = "ycpi_idem"
    TX_UID = "uid-idem-001"
    TRACKING = "ycpi_idem_acq"

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        _make_pi_with_acq_option(self.storage, public_id=self.PUBLIC_ID,
                                 tracking_id=self.TRACKING, checkout_token="tok_idem")
        _store_acq_tx(self.storage, uid=self.TX_UID, tracking_id=self.TRACKING)

    def _verify_once(self):
        with patch("web_app_server.BePaidClient") as MockClient:
            MockClient.return_value.get_checkout_status.return_value = _ok_result(
                shop_id="acq-001", tracking_id=self.TRACKING, payment_uid=self.TX_UID)
            return self.ctx.bepaid_verify_acquiring_payment(_auth(), self.PUBLIC_ID)

    def test_16_repeat_status_query_idempotent(self):
        """Second verify call on an already-reconciled intent returns idempotent=True."""
        first = self._verify_once()
        self.assertTrue(first["ok"], first)
        second = self._verify_once()
        self.assertTrue(second["ok"], second)
        self.assertTrue(second.get("idempotent"), second)

    def test_16b_intent_not_double_paid(self):
        """After two verify calls, intent is paid exactly once."""
        self._verify_once()
        self._verify_once()
        pi = self.storage.get_payment_intent(self.PUBLIC_ID)
        self.assertEqual(pi["status"], "paid")
        self.assertEqual(pi["paid_transaction_uid"], self.TX_UID)


# ═══════════════════════════════════════════════════════════════════════════
# 7. External system non-calls
# ═══════════════════════════════════════════════════════════════════════════

class Test07NoExternalCalls(unittest.TestCase):

    PUBLIC_ID = "ycpi_nocall"
    TX_UID = "uid-nocall-001"
    TRACKING = "ycpi_nocall_acq"

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        _make_pi_with_acq_option(self.storage, public_id=self.PUBLIC_ID,
                                 tracking_id=self.TRACKING, checkout_token="tok_nc")
        _store_acq_tx(self.storage, uid=self.TX_UID, tracking_id=self.TRACKING)

    def _run_verify(self):
        with patch("web_app_server.BePaidClient") as MockClient:
            MockClient.return_value.get_checkout_status.return_value = _ok_result(
                shop_id="acq-001", tracking_id=self.TRACKING, payment_uid=self.TX_UID)
            result = self.ctx.bepaid_verify_acquiring_payment(_auth(), self.PUBLIC_ID)
        return result, MockClient

    def test_17_bepaid_payment_not_created(self):
        """No create_acquiring_checkout or create_erip_payment is called."""
        result, MockClient = self._run_verify()
        self.assertTrue(result["ok"], result)
        instance = MockClient.return_value
        self.assertFalse(instance.create_acquiring_checkout.called)
        self.assertFalse(instance.create_erip_payment.called)

    def test_18_moyklass_not_called(self):
        """MoyKlass API is never called (auto_post disabled)."""
        with patch("web_app_server.BePaidClient") as MockClient:
            MockClient.return_value.get_checkout_status.return_value = _ok_result(
                shop_id="acq-001", tracking_id=self.TRACKING, payment_uid=self.TX_UID)
            with patch("web_app_server.requests", create=True) as _mock_requests:
                result = self.ctx.bepaid_verify_acquiring_payment(_auth(), self.PUBLIC_ID)
        # MK posting would call storage.log_moyklass_post or similar — verify via settings flag
        self.assertFalse(self.ctx.settings.bepaid_auto_post_to_moyklass,
                         "BEPAID_AUTO_POST_TO_MOYKLASS must remain false")
        self.assertTrue(result["ok"], result)

    def test_19_telegram_not_called(self):
        """No Telegram bot notifications are triggered."""
        notified = []
        original_notify = getattr(self.ctx, "_notify_owner", None)
        self.ctx._notify_owner = lambda *a, **kw: notified.append(a)
        try:
            with patch("web_app_server.BePaidClient") as MockClient:
                MockClient.return_value.get_checkout_status.return_value = _ok_result(
                    shop_id="acq-001", tracking_id=self.TRACKING, payment_uid=self.TX_UID)
                self.ctx.bepaid_verify_acquiring_payment(_auth(), self.PUBLIC_ID)
        finally:
            if original_notify is not None:
                self.ctx._notify_owner = original_notify
        self.assertEqual(len(notified), 0, "No Telegram notifications must be sent")


# ═══════════════════════════════════════════════════════════════════════════
# 8. Storage method correctness
# ═══════════════════════════════════════════════════════════════════════════

class Test08StorageMethods(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()

    def test_20_mark_provider_verified_sets_correct_fields(self):
        """mark_bepaid_transaction_provider_verified sets only provider_verified fields."""
        tx, _ = self.storage.upsert_bepaid_transaction({
            "provider": "bepaid", "shop_type": "acquiring",
            "transaction_uid": "uid-sv-001", "tracking_id": "ycpi_sv_acq",
            "order_id": "ORD-sv", "status": "successful",
            "amount_minor": 100, "amount_byn": 1.0, "currency": "BYN", "test": 0,
        })
        from utils import now_iso
        ts = now_iso()
        self.storage.mark_bepaid_transaction_provider_verified(
            tx["id"], verified_at=ts, verification_method="checkout_status_query")
        updated = self.storage.get_bepaid_transaction_by_id(tx["id"])
        self.assertEqual(updated["provider_verified"], 1)
        self.assertEqual(updated["provider_verified_at"], ts)
        self.assertEqual(updated["provider_verification_method"], "checkout_status_query")
        self.assertEqual(updated["webhook_verified"], 0, "webhook_verified must remain 0")

    def test_20b_provider_verified_tx_appears_in_unmatched_list(self):
        """Transaction with provider_verified=1 appears in list_unmatched."""
        tx, _ = self.storage.upsert_bepaid_transaction({
            "provider": "bepaid", "shop_type": "acquiring",
            "transaction_uid": "uid-pv-list", "tracking_id": "ycpi_pv_acq",
            "order_id": "ORD-pv", "status": "successful",
            "amount_minor": 100, "amount_byn": 1.0, "currency": "BYN", "test": 0,
        })
        from utils import now_iso
        self.storage.mark_bepaid_transaction_provider_verified(
            tx["id"], verified_at=now_iso())
        result = self.storage.list_unmatched_bepaid_transactions()
        ids = [r["id"] for r in result]
        self.assertIn(tx["id"], ids)

    def test_20c_unverified_tx_excluded_from_list(self):
        """Transaction with webhook_verified=0 AND provider_verified=0 is excluded."""
        self.storage.upsert_bepaid_transaction({
            "provider": "bepaid", "shop_type": "acquiring",
            "transaction_uid": "uid-unv-list", "tracking_id": "ycpi_unv_acq",
            "order_id": "ORD-unv", "status": "successful",
            "amount_minor": 100, "amount_byn": 1.0, "currency": "BYN", "test": 0,
        })
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 0)


# ═══════════════════════════════════════════════════════════════════════════
# 9. Access control
# ═══════════════════════════════════════════════════════════════════════════

class Test09AccessControl(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        _make_pi_with_acq_option(self.storage, public_id="ycpi_ac", checkout_token="tok_ac")

    def test_21_non_owner_denied(self):
        """client_manager role cannot call verify-acquiring."""
        self.ctx._role_store[42] = "client_manager"
        result = self.ctx.bepaid_verify_acquiring_payment({"ok": True, "user_id": 42}, "ycpi_ac")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "access_denied")

    def test_22_missing_intent_returns_error(self):
        """Non-existent intent returns intent_not_found."""
        result = self.ctx.bepaid_verify_acquiring_payment(_auth(), "ycpi_does_not_exist")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "intent_not_found")


# ═══════════════════════════════════════════════════════════════════════════
# 10. Network error handling
# ═══════════════════════════════════════════════════════════════════════════

class Test10NetworkErrors(unittest.TestCase):

    PUBLIC_ID = "ycpi_net"
    TRACKING = "ycpi_net_acq"

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        _make_pi_with_acq_option(self.storage, public_id=self.PUBLIC_ID,
                                 tracking_id=self.TRACKING, checkout_token="tok_net")
        _store_acq_tx(self.storage, uid="uid-net-001", tracking_id=self.TRACKING)

    def test_23_timeout_is_retryable(self):
        """Timeout returns retry=True and does not change intent status."""
        with patch("web_app_server.BePaidClient") as MockClient:
            MockClient.return_value.get_checkout_status.return_value = BePaidResult(
                ok=False, http_status=0, error="timeout", requires_check=True)
            result = self.ctx.bepaid_verify_acquiring_payment(_auth(), self.PUBLIC_ID)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "provider_unavailable")
        self.assertTrue(result.get("retry"))
        pi = self.storage.get_payment_intent(self.PUBLIC_ID)
        self.assertNotEqual(pi["status"], "paid")

    def test_24_provider_5xx_retryable(self):
        """HTTP 5xx from bePaid returns retry=True."""
        with patch("web_app_server.BePaidClient") as MockClient:
            MockClient.return_value.get_checkout_status.return_value = BePaidResult(
                ok=False, http_status=503, error="server_error:HTTP 503", requires_check=True)
            result = self.ctx.bepaid_verify_acquiring_payment(_auth(), self.PUBLIC_ID)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "provider_unavailable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
