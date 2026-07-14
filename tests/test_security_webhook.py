"""Security tests for v7.0.92.5.2 — webhook signature verification pipeline.

These tests verify the security invariants that were broken in v7.0.92.5.1
and restored in v7.0.92.5.2:

    webhook_verified is a CRYPTOGRAPHIC property, set BEFORE matching.
    It must never be set based on matching success or intent state.
    Unverified transactions must never be surfaced for reconciliation.

Run offline:
    python -m unittest tests.test_security_webhook -v
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

# ─── helpers ─────────────────────────────────────────────────────────────────

def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(
        bepaid_acq_shop_id="acq-001",
        bepaid_acq_secret_key="secret",
        bepaid_acq_public_key="",
        bepaid_erip_shop_id="",
        bepaid_erip_secret_key="",
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


def _auth() -> dict:
    return {"ok": True, "user_id": 1}


def _store_tx(storage: Storage, *, uid: str, tracking_id: str,
              shop_type: str = "acquiring", status: str = "successful",
              test: int = 0, webhook_verified: int = 0,
              intent_public_id: str = "") -> dict:
    tx, _ = storage.upsert_bepaid_transaction({
        "provider": "bepaid",
        "shop_type": shop_type,
        "transaction_uid": uid,
        "tracking_id": tracking_id,
        "order_id": f"ORD-{uid}",
        "status": status,
        "amount_minor": 100,
        "amount_byn": 1.0,
        "currency": "BYN",
        "test": test,
    })
    with storage._connect() as conn:
        conn.execute(
            "UPDATE bepaid_transactions SET webhook_verified=?, intent_public_id=? WHERE id=?",
            (webhook_verified, intent_public_id or None, tx["id"]),
        )
    return storage.get_bepaid_transaction_by_id(tx["id"])


def _make_pi_with_acq_option(storage: Storage, *, public_id: str) -> tuple:
    pi = storage.create_payment_intent({
        "mk_user_id": 1,
        "student_name": "Security Test",
        "amount_minor": 100,
        "amount_byn": 1.0,
        "currency": "BYN",
        "purpose": "current_month",
        "period_month": "2026-07",
        "payment_method": "acquiring",
        "status": "awaiting_payment",
        "created_by_tg_id": 1,
        "created_by_name": "Test",
    })
    with storage._connect() as conn:
        conn.execute("UPDATE payment_intents SET public_id=? WHERE id=?",
                     (public_id, pi["id"]))
    pi = storage.get_payment_intent(public_id)
    opt = storage.create_payment_intent_option(
        payment_intent_id=pi["id"],
        intent_public_id=public_id,
        channel="acquiring",
        shop_type="acquiring",
        bepaid_tracking_id=f"{public_id}_acq",
        bepaid_order_id=f"ORD-{public_id}",
    )
    return pi, opt


# ═══════════════════════════════════════════════════════════════════════════
# Security invariant tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSecurityWebhookVerification(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    # 1. Cryptographic verification → webhook_verified=1, no match required
    def test_sec_01_valid_signed_no_match_tx_gets_verified_1(self):
        """After mark_bepaid_transaction_signature_verified, tx has webhook_verified=1
        even when no matching intent exists (no_match)."""
        from utils import now_iso
        tx = _store_tx(self.storage, uid="sec-01", tracking_id="ycpi_202607_99_acq",
                       webhook_verified=0)
        self.storage.mark_bepaid_transaction_signature_verified(
            tx["id"], verified_at=now_iso(), verification_method="rsa_pkcs1v15_sha256"
        )
        updated = self.storage.get_bepaid_transaction_by_id(tx["id"])
        self.assertEqual(updated["webhook_verified"], 1,
                         "Signature verification must set webhook_verified=1 for no_match tx")

    # 2. Matching success must NOT set webhook_verified
    def test_sec_02_matching_does_not_set_webhook_verified(self):
        """bepaid_transaction_link_intent must not write webhook_verified=1."""
        import inspect
        from storage import Storage as S
        src = inspect.getsource(S.bepaid_transaction_link_intent)
        self.assertNotIn("webhook_verified", src,
                         "bepaid_transaction_link_intent must not touch webhook_verified")

    # 3. Invalid signature tx (webhook_verified=0) absent from unmatched list
    def test_sec_03_invalid_signature_not_in_unmatched(self):
        _store_tx(self.storage, uid="sec-03", tracking_id="trk_inv",
                  status="successful", test=0, webhook_verified=0)
        result = self.storage.list_unmatched_bepaid_transactions()
        uids = [r["transaction_uid"] for r in result]
        self.assertNotIn("sec-03", uids,
                         "tx with invalid/missing signature must not appear in unmatched list")

    # 4. webhook_verified=0 excluded from storage-level list
    def test_sec_04_verified_0_excluded_from_storage_list(self):
        _store_tx(self.storage, uid="sec-04a", tracking_id="trk4a",
                  status="successful", test=0, webhook_verified=0)
        _store_tx(self.storage, uid="sec-04b", tracking_id="trk4b",
                  status="successful", test=0, webhook_verified=1)
        result = self.storage.list_unmatched_bepaid_transactions()
        uids = [r["transaction_uid"] for r in result]
        self.assertNotIn("sec-04a", uids)
        self.assertIn("sec-04b", uids)

    # 5. Reconcile hard-blocks webhook_verified=0
    def test_sec_05_reconcile_hard_blocks_webhook_verified_0(self):
        tx = _store_tx(self.storage, uid="sec-05", tracking_id="trk5",
                       status="successful", test=0, webhook_verified=0)
        result = self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason"), "webhook_not_verified")

    # 6. Reconcile does not call matcher when webhook_verified=0
    def test_sec_06_reconcile_skips_matcher_when_not_verified(self):
        """Reconcile must return before calling match logic for unverified tx."""
        _make_pi_with_acq_option(self.storage, public_id="ycpi_202607_t06")
        tx = _store_tx(self.storage, uid="sec-06", tracking_id="ycpi_202607_t06_acq",
                       status="successful", test=0, webhook_verified=0)
        # If matcher was called, it would find the option and try to mark paid
        result = self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        self.assertFalse(result.get("ok"))
        # Intent must still be unpaid
        pi = self.storage.get_payment_intent("ycpi_202607_t06")
        self.assertNotEqual(pi["status"], "paid",
                            "Matcher must not have been called for webhook_verified=0 tx")

    # 7. Reconcile does not change intent when webhook_verified=0
    def test_sec_07_reconcile_does_not_change_intent_when_not_verified(self):
        _make_pi_with_acq_option(self.storage, public_id="ycpi_202607_t07")
        tx = _store_tx(self.storage, uid="sec-07", tracking_id="ycpi_202607_t07_acq",
                       status="successful", test=0, webhook_verified=0)
        self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        pi = self.storage.get_payment_intent("ycpi_202607_t07")
        self.assertEqual(pi["status"], "awaiting_payment")

    # 8. webhook_verified reflects crypto result, not match status
    def test_sec_08_verified_1_then_no_match_stays_verified(self):
        """Verified tx that fails matching must retain webhook_verified=1."""
        from utils import now_iso
        tx = _store_tx(self.storage, uid="sec-08", tracking_id="ycpi_nonexistent_acq",
                       webhook_verified=0)
        self.storage.mark_bepaid_transaction_signature_verified(
            tx["id"], verified_at=now_iso()
        )
        updated = self.storage.get_bepaid_transaction_by_id(tx["id"])
        self.assertEqual(updated["webhook_verified"], 1,
                         "webhook_verified must stay 1 after failed match")

    # 9. Verified tx with no_match appears in unmatched list
    def test_sec_09_verified_no_match_tx_appears_in_unmatched_list(self):
        from utils import now_iso
        tx = _store_tx(self.storage, uid="sec-09", tracking_id="ycpi_unmatched_9",
                       webhook_verified=0)
        self.storage.mark_bepaid_transaction_signature_verified(
            tx["id"], verified_at=now_iso()
        )
        result = self.storage.list_unmatched_bepaid_transactions()
        uids = [r["transaction_uid"] for r in result]
        self.assertIn("sec-09", uids)

    # 10. mark_bepaid_transaction_signature_verified must NOT change intent_public_id
    def test_sec_10_mark_verified_does_not_overwrite_intent_public_id(self):
        from utils import now_iso
        tx = _store_tx(self.storage, uid="sec-10", tracking_id="trk10",
                       webhook_verified=0, intent_public_id="ycpi_202607_10")
        self.storage.mark_bepaid_transaction_signature_verified(
            tx["id"], verified_at=now_iso()
        )
        updated = self.storage.get_bepaid_transaction_by_id(tx["id"])
        self.assertEqual(updated.get("intent_public_id"), "ycpi_202607_10",
                         "mark_verified must not touch intent_public_id")

    # 11. Multiple unverified tx all excluded from list
    def test_sec_11_multiple_unverified_tx_all_excluded(self):
        for i in range(5):
            _store_tx(self.storage, uid=f"sec-11-{i}", tracking_id=f"trk11{i}",
                      status="successful", test=0, webhook_verified=0)
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 0)

    # 12. Mixed verified/unverified — only verified appears
    def test_sec_12_only_verified_tx_in_mixed_list(self):
        _store_tx(self.storage, uid="sec-12-bad", tracking_id="trk12bad",
                  status="successful", test=0, webhook_verified=0)
        _store_tx(self.storage, uid="sec-12-good", tracking_id="trk12good",
                  status="successful", test=0, webhook_verified=1)
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["transaction_uid"], "sec-12-good")

    # 13. API endpoint result matches storage-level security filter
    def test_sec_13_api_honors_same_security_filter_as_storage(self):
        _store_tx(self.storage, uid="sec-13-bad", tracking_id="trk13bad",
                  status="successful", test=0, webhook_verified=0)
        _store_tx(self.storage, uid="sec-13-ok", tracking_id="trk13ok",
                  status="successful", test=0, webhook_verified=1)
        result = self.ctx.bepaid_list_unmatched_transactions(_auth())
        items = result.get("items", [])
        uids = [i["transaction_uid"] for i in items]
        self.assertNotIn("sec-13-bad", uids)
        self.assertIn("sec-13-ok", uids)

    # 14. signature_verified field in API response reflects DB truth
    def test_sec_14_api_item_signature_verified_reflects_db(self):
        _store_tx(self.storage, uid="sec-14", tracking_id="trk14",
                  status="successful", test=0, webhook_verified=1)
        result = self.ctx.bepaid_list_unmatched_transactions(_auth())
        item = result["items"][0]
        self.assertTrue(item.get("signature_verified"),
                        "signature_verified in API must be True for webhook_verified=1")

    # 15. Reconcile returns specific error code for not-verified case
    def test_sec_15_reconcile_error_code_is_reconcile_blocked(self):
        tx = _store_tx(self.storage, uid="sec-15", tracking_id="trk15",
                       status="successful", test=0, webhook_verified=0)
        result = self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        self.assertEqual(result.get("error"), "reconcile_blocked")

    # 16. Reconcile logs audit event for blocked attempt
    def test_sec_16_reconcile_logs_audit_event_for_blocked_attempt(self):
        tx = _store_tx(self.storage, uid="sec-16", tracking_id="trk16",
                       status="successful", test=0, webhook_verified=0)
        self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        logs = self.storage.list_payment_webhook_audit(limit=10)
        event_types = [e["event_type"] for e in logs]
        self.assertIn("stored_transaction_reconcile_blocked", event_types,
                      "Blocked reconcile must log audit event")

    # 17. Test transactions are rejected even when webhook_verified=1
    def test_sec_17_test_transaction_rejected_even_if_verified(self):
        tx = _store_tx(self.storage, uid="sec-17", tracking_id="trk17",
                       status="successful", test=1, webhook_verified=1)
        result = self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        self.assertFalse(result.get("ok"),
                         "Test transactions must never be reconciled")

    # 18. Transaction_uid IS NOT NULL required by storage filter
    def test_sec_18_null_transaction_uid_excluded(self):
        tx, _ = self.storage.upsert_bepaid_transaction({
            "provider": "bepaid",
            "shop_type": "acquiring",
            "transaction_uid": None,
            "tracking_id": "trk18",
            "order_id": "ORD-18",
            "status": "successful",
            "amount_minor": 100,
            "amount_byn": 1.0,
            "currency": "BYN",
            "test": 0,
        })
        with self.storage._connect() as conn:
            conn.execute(
                "UPDATE bepaid_transactions SET webhook_verified=1, intent_public_id=NULL WHERE id=?",
                (tx["id"],)
            )
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 0,
                         "Tx with NULL transaction_uid must be excluded from unmatched list")

    # 19. Verified=1 tx with valid uid is included — positive control
    def test_sec_19_positive_control_verified_with_uid_included(self):
        tx = _store_tx(self.storage, uid="sec-19-control", tracking_id="trk19",
                       status="successful", test=0, webhook_verified=1)
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["transaction_uid"], "sec-19-control")


if __name__ == "__main__":
    unittest.main()
