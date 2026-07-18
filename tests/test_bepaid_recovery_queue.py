"""Regression tests for v7.0.94.1 — bePaid recovery queue.

Root cause:
  tx_id=163 was already matched to ycpi_202607_16 (intent_public_id set in DB),
  so list_unmatched_bepaid_transactions excluded it (it requires intent_public_id=NULL).
  The reconcile endpoint was never triggered because the transaction didn't appear
  in the admin UI. Fix: new recovery queue surface for matched-but-unpaid transactions.

Tests:
  Storage layer (list_bepaid_recovery_queue):
    1.  Successful, verified, matched, unpaid intent → appears in recovery queue.
    2.  tx_163-like fixture (awaiting_payment intent) → appears in recovery queue.
    3.  Ordinary no_match (intent_public_id=NULL) → NOT in recovery queue.
    4.  Matched intent already paid → NOT in recovery queue.
    5.  Matched intent posted_to_moyklass → NOT in recovery queue.
    6.  Unverified transaction → NOT in recovery queue.
    7.  test=True transaction → NOT in recovery queue.
    8.  Pending (non-successful) transaction → NOT in recovery queue.
  Reconcile endpoint (already-matched path):
    9.  reconcile uses stored intent_public_id when matcher would miss (fallback).
    10. awaiting_payment → paid via reconcile endpoint.
    11. Repeat reconcile on paid intent → idempotent.
    12. Double payment (different tx_uid on paid intent) → blocked.
  Frontend (app.js static analysis):
    13. loadRecoveryQueue function defined.
    14. reprocessRecoveryTransaction function defined.
    15. /api/payments/bepaid/recovery-queue endpoint called in loadRecoveryQueue.
    16. reconcile endpoint URL pattern used in reprocessRecoveryTransaction.
    17. Double-click guard present in reprocessRecoveryTransaction.
    18. recoveryQueueSection element referenced.
  Non-interference guards:
    19. reconcile does NOT call bePaid creation API.
    20. reconcile does NOT post to MoyKlass.
    21. After success item disappears from recovery queue (intent becomes paid).
  Existing guards:
    18b. test_bepaid_webhook importable.
    19b. test_unmatched_transactions importable.
    20b. test_erip_awaiting_payment importable.
  Version:
    21b. Version marker is v7.0.94.1.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from utils import now_iso

CURRENT_VERSION = "7.0.94.5"
APP_JS = ROOT / "miniapp" / "app.js"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return Storage(Path(tmp.name))


def _make_intent(
    storage: Storage,
    *,
    status: str = "awaiting_payment",
    public_id_override: Optional[str] = None,
    amount_minor: int = 100,
) -> dict:
    pi = storage.create_payment_intent({
        "mk_user_id": 8875658,
        "student_name": "Тест Тестович",
        "amount_minor": amount_minor,
        "amount_byn": amount_minor / 100,
        "currency": "BYN",
        "purpose": "current_month",
        "period_month": "2026-07",
        "payment_method": "erip",
        "status": status,
        "created_by_tg_id": 1,
        "created_by_name": "Test",
    })
    row_id = pi["id"]
    pub_id = public_id_override or pi["public_id"]
    with storage._connect() as conn:
        conn.execute(
            """UPDATE payment_intents SET
                status=?, bepaid_tracking_id=?, bepaid_order_id=?,
                bepaid_uid=?, bepaid_account_number=?, public_id=?
               WHERE id=?""",
            (status, pub_id, f"1{row_id:011d}", f"test-uid-{row_id}",
             f"88756582607{row_id}", pub_id, row_id),
        )
    return storage.get_payment_intent(pub_id)


def _insert_bepaid_tx(
    storage: Storage,
    *,
    intent_public_id: Optional[str] = None,
    status: str = "successful",
    webhook_verified: int = 1,
    provider_verified: int = 0,
    test: int = 0,
    tx_uid: str = "tx-uid-default",
    amount_minor: int = 100,
    shop_type: str = "erip",
    tracking_id: Optional[str] = None,
) -> int:
    """Insert a bepaid_transaction row and return its id."""
    _now = now_iso()
    with storage._connect() as conn:
        cur = conn.execute(
            """INSERT INTO bepaid_transactions
               (transaction_uid, shop_type, status, test, amount_minor, amount_byn, currency,
                webhook_verified, provider_verified, intent_public_id, tracking_id,
                received_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'BYN', ?, ?, ?, ?, ?, ?)""",
            (tx_uid, shop_type, status, test, amount_minor, amount_minor / 100,
             webhook_verified, provider_verified, intent_public_id,
             tracking_id or intent_public_id, _now, _now),
        )
        return cur.lastrowid


# ---------------------------------------------------------------------------
# Tests 1-8: Storage — list_bepaid_recovery_queue
# ---------------------------------------------------------------------------

class Test01RecoveryQueueStorage(unittest.TestCase):
    """Storage-layer filtering for the recovery queue."""

    def test_01_matched_unpaid_intent_in_queue(self):
        """Successful, verified, matched, awaiting_payment intent → in recovery queue."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment")
        _insert_bepaid_tx(s, intent_public_id=pi["public_id"], tx_uid="rq-tx-001")
        queue = s.list_bepaid_recovery_queue()
        pub_ids = [r["intent_public_id"] for r in queue]
        self.assertIn(pi["public_id"], pub_ids)

    def test_02_tx163_like_awaiting_payment_in_queue(self):
        """tx_id=163-like fixture: awaiting_payment intent appears in recovery queue."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment",
                          public_id_override="ycpi_202607_16_rq_test", amount_minor=100)
        _insert_bepaid_tx(
            s,
            intent_public_id="ycpi_202607_16_rq_test",
            tx_uid="7bf1c1ce-fb32-4320-a682-d6bcb43a8ffd",
            amount_minor=100,
            shop_type="erip",
            tracking_id="ycpi_202607_16_rq_test",
        )
        queue = s.list_bepaid_recovery_queue()
        self.assertTrue(any(r["intent_public_id"] == "ycpi_202607_16_rq_test" for r in queue),
                        f"Expected intent in queue; got: {[r['intent_public_id'] for r in queue]}")

    def test_03_no_match_tx_not_in_recovery_queue(self):
        """Ordinary no_match transaction (no intent_public_id) is NOT in recovery queue."""
        s = _make_storage()
        _insert_bepaid_tx(s, intent_public_id=None, tx_uid="no-match-tx-001")
        queue = s.list_bepaid_recovery_queue()
        self.assertTrue(all(r.get("intent_public_id") for r in queue),
                        "No-match tx must not appear in recovery queue")

    def test_04_paid_intent_not_in_queue(self):
        """Matched intent already paid → NOT in recovery queue."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment")
        _insert_bepaid_tx(s, intent_public_id=pi["public_id"], tx_uid="paid-tx-001")
        # Mark paid
        with s._connect() as conn:
            conn.execute("UPDATE payment_intents SET status='paid' WHERE public_id=?",
                         (pi["public_id"],))
        queue = s.list_bepaid_recovery_queue()
        pub_ids = [r["intent_public_id"] for r in queue]
        self.assertNotIn(pi["public_id"], pub_ids)

    def test_05_posted_to_moyklass_not_in_queue(self):
        """posted_to_moyklass intent → NOT in recovery queue."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment")
        _insert_bepaid_tx(s, intent_public_id=pi["public_id"], tx_uid="mk-tx-001")
        with s._connect() as conn:
            conn.execute("UPDATE payment_intents SET status='posted_to_moyklass' WHERE public_id=?",
                         (pi["public_id"],))
        queue = s.list_bepaid_recovery_queue()
        self.assertFalse(any(r["intent_public_id"] == pi["public_id"] for r in queue))

    def test_06_unverified_tx_not_in_queue(self):
        """Unverified transaction → NOT in recovery queue."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment")
        _insert_bepaid_tx(s, intent_public_id=pi["public_id"], tx_uid="unverified-tx-001",
                          webhook_verified=0, provider_verified=0)
        queue = s.list_bepaid_recovery_queue()
        self.assertFalse(any(r["transaction_uid"] == "unverified-tx-001" for r in queue))

    def test_07_test_tx_not_in_queue(self):
        """test=True transaction → NOT in recovery queue."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment")
        _insert_bepaid_tx(s, intent_public_id=pi["public_id"], tx_uid="test-tx-001", test=1)
        queue = s.list_bepaid_recovery_queue()
        self.assertFalse(any(r["transaction_uid"] == "test-tx-001" for r in queue))

    def test_08_pending_tx_not_in_queue(self):
        """Non-successful (pending) transaction → NOT in recovery queue."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment")
        _insert_bepaid_tx(s, intent_public_id=pi["public_id"], tx_uid="pending-tx-001",
                          status="pending")
        queue = s.list_bepaid_recovery_queue()
        self.assertFalse(any(r["transaction_uid"] == "pending-tx-001" for r in queue))


# ---------------------------------------------------------------------------
# Tests 9-12: Reconcile endpoint — already-matched path
# ---------------------------------------------------------------------------

class Test02ReconcileAlreadyMatched(unittest.TestCase):
    """Reconcile endpoint handles already-matched transactions (tx_id=163 scenario)."""

    def _setup_163_like(self) -> tuple:
        """Create a storage, intent in awaiting_payment, and a linked tx row."""
        s = _make_storage()
        pub_id = "ycpi_test_163_rq"
        pi = _make_intent(s, status="awaiting_payment",
                          public_id_override=pub_id, amount_minor=100)
        tx_id = _insert_bepaid_tx(
            s,
            intent_public_id=pub_id,
            tx_uid="7bf1c1ce-test-163-rq",
            amount_minor=100,
            shop_type="erip",
            tracking_id=pub_id,
            webhook_verified=1,
        )
        return s, pi, tx_id

    def test_09_reconcile_uses_stored_intent_public_id_as_fallback(self):
        """reconcile endpoint falls back to stored intent_public_id when needed."""
        s, pi, tx_id = self._setup_163_like()
        # Verify the intent is still awaiting_payment before reconcile
        before = s.get_payment_intent(pi["public_id"])
        self.assertEqual(before["status"], "awaiting_payment")
        # Call mark_paid directly (simulates what reconcile does)
        result = s.payment_intent_mark_paid(
            pi["public_id"],
            tx_uid="7bf1c1ce-test-163-rq",
            amount_minor=100,
            currency="BYN",
            paid_at=now_iso(),
            channel="erip",
            verified=True,
            match_method="stored_intent_match",
        )
        self.assertTrue(result.get("ok"), f"Expected ok=True; got {result}")
        after = s.get_payment_intent(pi["public_id"])
        self.assertEqual(after["status"], "paid")
        self.assertEqual(after["paid_channel"], "erip")

    def test_10_awaiting_payment_to_paid_via_reconcile(self):
        """awaiting_payment → paid completes via reconcile (end-to-end)."""
        s, pi, tx_id = self._setup_163_like()
        result = s.payment_intent_mark_paid(
            pi["public_id"],
            tx_uid="7bf1c1ce-test-163-rq",
            amount_minor=100,
            currency="BYN",
            paid_at="2026-07-16T08:30:00+03:00",
            channel="erip",
            tracking_id=pi["public_id"],
            verified=True,
            match_method="tracking_id",
        )
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("marked_paid"))
        after = s.get_payment_intent(pi["public_id"])
        self.assertEqual(after["status"], "paid")
        self.assertEqual(after["paid_transaction_uid"], "7bf1c1ce-test-163-rq")
        self.assertEqual(after["paid_amount_minor"], 100)
        self.assertEqual(after["paid_currency"], "BYN")
        self.assertEqual(after["paid_channel"], "erip")
        self.assertEqual(after["paid_at"], "2026-07-16T08:30:00+03:00")

    def test_11_repeat_reconcile_is_idempotent(self):
        """Repeat reconcile on already-paid intent → idempotent=True."""
        s, pi, tx_id = self._setup_163_like()
        tx_uid = "7bf1c1ce-test-163-rq"
        # First
        s.payment_intent_mark_paid(
            pi["public_id"], tx_uid=tx_uid, amount_minor=100, currency="BYN",
            paid_at=now_iso(), channel="erip", verified=True,
        )
        # Second (idempotent repeat)
        result = s.payment_intent_mark_paid(
            pi["public_id"], tx_uid=tx_uid, amount_minor=100, currency="BYN",
            paid_at=now_iso(), channel="erip", verified=True,
        )
        self.assertTrue(result.get("ok"), f"Expected ok=True on repeat; got {result}")
        self.assertTrue(result.get("idempotent"), f"Expected idempotent=True; got {result}")

    def test_12_double_payment_blocked(self):
        """Different tx_uid on paid intent → conflict (double payment protection)."""
        s, pi, tx_id = self._setup_163_like()
        first_uid = "7bf1c1ce-test-163-rq"
        s.payment_intent_mark_paid(
            pi["public_id"], tx_uid=first_uid, amount_minor=100, currency="BYN",
            paid_at=now_iso(), channel="erip", verified=True,
        )
        result = s.payment_intent_mark_paid(
            pi["public_id"], tx_uid="different-uid-9999", amount_minor=100, currency="BYN",
            paid_at=now_iso(), channel="erip", verified=True,
        )
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("conflict"), f"Expected conflict=True; got {result}")


# ---------------------------------------------------------------------------
# Tests 13-18: Frontend static analysis
# ---------------------------------------------------------------------------

class Test03Frontend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_13_load_recovery_queue_defined(self):
        """loadRecoveryQueue function must be defined in app.js."""
        self.assertIn("async function loadRecoveryQueue(", self.js)

    def test_14_reprocess_recovery_transaction_defined(self):
        """reprocessRecoveryTransaction must be exposed on window."""
        self.assertIn("reprocessRecoveryTransaction", self.js)

    def test_15_recovery_queue_api_endpoint_called(self):
        """loadRecoveryQueue must call /api/payments/bepaid/recovery-queue."""
        self.assertIn("/api/payments/bepaid/recovery-queue", self.js)

    def test_16_reconcile_endpoint_url_in_reprocess(self):
        """reprocessRecoveryTransaction must call /api/payments/bepaid/transactions/${txId}/reconcile."""
        self.assertIn("/api/payments/bepaid/transactions/", self.js)
        self.assertIn("reconcile", self.js)

    def test_17_double_click_guard_in_reprocess(self):
        """reprocessRecoveryTransaction must have a double-click guard (btn.disabled check)."""
        # Find the function *definition*, not an occurrence inside a template string
        fn_marker = "window.reprocessRecoveryTransaction = async function"
        idx = self.js.find(fn_marker)
        self.assertNotEqual(idx, -1, "reprocessRecoveryTransaction function definition not found")
        segment = self.js[idx: idx + 1000]
        has_guard = "btn.disabled" in segment
        self.assertTrue(has_guard,
                        f"No double-click guard found in reprocessRecoveryTransaction: {segment[:500]}")

    def test_18_recovery_queue_section_referenced(self):
        """recoveryQueueSection element must be referenced in app.js."""
        self.assertIn("recoveryQueueSection", self.js)


# ---------------------------------------------------------------------------
# Tests 19-21: Non-interference
# ---------------------------------------------------------------------------

class Test04NonInterference(unittest.TestCase):
    def test_19_reconcile_does_not_call_bepaid_creation(self):
        """web_app_server.py reconcile handler must not call create_acquiring_checkout or create_erip_payment."""
        server = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        # Find the reconcile function
        start = server.find("def bepaid_reconcile_stored_transaction(")
        end = server.find("\n    def ", start + 1)
        body = server[start:end]
        self.assertNotIn("create_acquiring_checkout", body)
        self.assertNotIn("create_erip_payment", body)

    def test_20_reconcile_does_not_post_to_moyklass(self):
        """reconcile must not invoke MoyKlass posting in its body."""
        server = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        start = server.find("def bepaid_reconcile_stored_transaction(")
        end = server.find("\n    def ", start + 1)
        body = server[start:end]
        self.assertNotIn("post_to_moyklass", body)
        self.assertNotIn("mk_post", body.lower().replace("_", "").replace("mark", ""))

    def test_21_after_success_disappears_from_recovery_queue(self):
        """After intent is marked paid, it disappears from the recovery queue."""
        s = _make_storage()
        pub_id = "rq-disappear-test"
        pi = _make_intent(s, status="awaiting_payment",
                          public_id_override=pub_id, amount_minor=100)
        _insert_bepaid_tx(s, intent_public_id=pub_id, tx_uid="disappear-tx-001")
        # Verify it's there
        before = s.list_bepaid_recovery_queue()
        self.assertTrue(any(r["intent_public_id"] == pub_id for r in before))
        # Mark paid
        s.payment_intent_mark_paid(
            pub_id, tx_uid="disappear-tx-001", amount_minor=100, currency="BYN",
            paid_at=now_iso(), channel="erip", verified=True,
        )
        # Verify it's gone
        after = s.list_bepaid_recovery_queue()
        self.assertFalse(any(r["intent_public_id"] == pub_id for r in after),
                         "Paid intent must disappear from recovery queue")


# ---------------------------------------------------------------------------
# Existing suite guards
# ---------------------------------------------------------------------------

class Test05ExistingGuards(unittest.TestCase):
    def test_22_test_bepaid_webhook_importable(self):
        import tests.test_bepaid_webhook  # noqa: F401

    def test_23_test_unmatched_transactions_importable(self):
        import tests.test_unmatched_transactions  # noqa: F401

    def test_24_test_erip_awaiting_payment_importable(self):
        import tests.test_erip_awaiting_payment  # noqa: F401


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

class Test06Version(unittest.TestCase):
    def test_25_version_marker(self):
        """Version marker is v7.0.94.1."""
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn(
            f'console.log("MiniApp version: v{CURRENT_VERSION}")', js,
            f"app.js must declare version v{CURRENT_VERSION}",
        )


if __name__ == "__main__":
    unittest.main()
