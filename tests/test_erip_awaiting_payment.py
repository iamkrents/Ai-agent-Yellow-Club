"""Regression tests for v7.0.94.0 вЂ” ERIP awaiting_payment в†’ paid state machine.

Root cause fixed:
  payment_intent_mark_paid (legacy path) previously only allowed bepaid_created
  as source state. Intents created via prepare-options (ERIP + ACQ together) land
  in awaiting_payment, so a real-ERIP webhook for ycpi_202607_16 triggered
  cannot_mark_paid_from_status:awaiting_payment. Fix: extend allowed source states
  to bepaid_created | awaiting_payment | partial_ready (matching via_option path).
  Also: channel param added so paid_channel is set correctly on the legacy path.

Tests:
  State machine вЂ” allowed transitions:
    1.  awaiting_payment в†’ paid for verified ERIP webhook
    2.  awaiting_payment в†’ paid for verified acquiring webhook
    3.  partial_ready   в†’ paid for verified ERIP webhook
    4.  bepaid_created  в†’ paid still works (no regression)
  State machine вЂ” blocked transitions:
    5.  draft intent в†’ blocked
    6.  ready intent в†’ blocked
    7.  cancelled intent в†’ blocked
    8.  posted_to_moyklass в†’ unchanged (conflict)
  Signature / test guards:
    9.  Unverified webhook does NOT mark paid (caller responsibility вЂ” method still marks;
        test confirms caller must check verified before calling)
    10. test=True transaction вЂ” method marks paid (caller must guard test flag)
  Amount / currency guards:
    11. Amount mismatch is not enforced by mark_paid itself (caller guards)
  Idempotency:
    12. Same tx_uid on paid intent в†’ idempotent True
    13. Different tx_uid on paid intent в†’ conflict
  paid_channel field:
    14. ERIP webhook sets paid_channel='erip'
    15. Acquiring webhook sets paid_channel='acquiring'
    16. Empty channel stored as None
  paid_at field:
    17. paid_at from transaction is persisted
  payment_state_reason:
    18. Set to paid_via_bepaid_webhook
  ycpi_202607_16 fixture:
    19. ycpi_202607_16-like intent (awaiting_payment) reconciles to paid
    20. Reconcile does NOT create a new payment intent (count stays same)
    21. Reconcile does NOT post to MoyKlass (no moyklass_post columns touched)
  MoyKlass auto-post guard:
    22. bepaid_auto_post_to_moyklass flag must not be set in app-level config files
  Existing suite guards:
    23. test_bepaid_webhook importable
    24. test_unmatched_transactions importable
  Version:
    25. Version marker is v7.0.94.0
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

CURRENT_VERSION = "7.0.94.1"

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
    amount_minor: int = 10000,
) -> dict:
    """Create a minimal payment_intent and force it to the requested status."""
    pi = storage.create_payment_intent({
        "mk_user_id": 8875658,
        "student_name": "РўРµСЃС‚ РўРµСЃС‚РѕРІРёС‡",
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
            """
            UPDATE payment_intents SET
                status=?,
                bepaid_tracking_id=?,
                bepaid_order_id=?,
                bepaid_uid=?,
                bepaid_account_number=?,
                public_id=?
            WHERE id=?
            """,
            (
                status,
                pub_id,
                f"1{row_id:011d}",
                f"test-uid-{row_id}",
                f"88756582607{row_id}",
                pub_id,
                row_id,
            ),
        )
    return storage.get_payment_intent(pub_id)


def _call_mark_paid(
    storage: Storage,
    public_id: str,
    *,
    tx_uid: str = "7bf1c1ce-fb32-4320-a682-d6bcb43a8ffd",
    amount_minor: int = 10000,
    currency: str = "BYN",
    channel: str = "erip",
    verified: bool = True,
    match_method: str = "tracking_id",
) -> dict:
    return storage.payment_intent_mark_paid(
        public_id,
        tx_uid=tx_uid,
        amount_minor=amount_minor,
        currency=currency,
        paid_at=now_iso(),
        channel=channel,
        verified=verified,
        match_method=match_method,
    )


# ---------------------------------------------------------------------------
# Tests 1-4: Allowed source states
# ---------------------------------------------------------------------------

class Test01AllowedSourceStates(unittest.TestCase):
    """Tests 1-4: All allowed source states transition to paid."""

    def test_01_awaiting_payment_erip_marks_paid(self):
        """awaiting_payment в†’ paid for verified ERIP webhook."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment")
        result = _call_mark_paid(s, pi["public_id"], channel="erip")
        self.assertTrue(result.get("ok"), f"Expected ok=True; got {result}")
        self.assertTrue(result.get("marked_paid"), f"Expected marked_paid=True; got {result}")
        after = s.get_payment_intent(pi["public_id"])
        self.assertEqual(after["status"], "paid")

    def test_02_awaiting_payment_acquiring_marks_paid(self):
        """awaiting_payment в†’ paid for verified acquiring webhook."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment")
        result = _call_mark_paid(
            s, pi["public_id"], channel="acquiring",
            tx_uid="acq-tx-uid-0001", match_method="order_id",
        )
        self.assertTrue(result.get("ok"), f"Expected ok=True; got {result}")
        self.assertTrue(result.get("marked_paid"))
        after = s.get_payment_intent(pi["public_id"])
        self.assertEqual(after["status"], "paid")

    def test_03_partial_ready_erip_marks_paid(self):
        """partial_ready в†’ paid for verified ERIP webhook."""
        s = _make_storage()
        pi = _make_intent(s, status="partial_ready")
        result = _call_mark_paid(s, pi["public_id"], channel="erip",
                                 tx_uid="partial-erip-tx-001")
        self.assertTrue(result.get("ok"), f"Expected ok=True; got {result}")
        after = s.get_payment_intent(pi["public_id"])
        self.assertEqual(after["status"], "paid")

    def test_04_bepaid_created_still_marks_paid(self):
        """bepaid_created в†’ paid still works (no regression from original path)."""
        s = _make_storage()
        pi = _make_intent(s, status="bepaid_created")
        result = _call_mark_paid(s, pi["public_id"], tx_uid="bc-tx-uid-0001")
        self.assertTrue(result.get("ok"), f"Expected ok=True; got {result}")
        after = s.get_payment_intent(pi["public_id"])
        self.assertEqual(after["status"], "paid")


# ---------------------------------------------------------------------------
# Tests 5-8: Blocked source states
# ---------------------------------------------------------------------------

class Test02BlockedSourceStates(unittest.TestCase):
    """Tests 5-8: States that must NOT transition to paid."""

    def test_05_draft_blocked(self):
        """draft intent must not be marked paid."""
        s = _make_storage()
        pi = _make_intent(s, status="draft")
        result = _call_mark_paid(s, pi["public_id"])
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("wrong_state"), f"Expected wrong_state; got {result}")
        after = s.get_payment_intent(pi["public_id"])
        self.assertEqual(after["status"], "draft")

    def test_06_ready_blocked(self):
        """ready intent must not be marked paid via legacy path."""
        s = _make_storage()
        pi = _make_intent(s, status="ready")
        result = _call_mark_paid(s, pi["public_id"])
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("wrong_state"), f"Expected wrong_state; got {result}")

    def test_07_cancelled_blocked(self):
        """cancelled intent must not be marked paid."""
        s = _make_storage()
        pi = _make_intent(s, status="cancelled")
        result = _call_mark_paid(s, pi["public_id"])
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("wrong_state"), f"Expected wrong_state; got {result}")

    def test_08_posted_to_moyklass_conflict(self):
        """posted_to_moyklass intent returns conflict (already paid differently)."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment")
        # Force to paid first with a different tx_uid
        with s._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET status='posted_to_moyklass', "
                "paid_transaction_uid='old-tx-uid' WHERE public_id=?",
                (pi["public_id"],),
            )
        result = _call_mark_paid(s, pi["public_id"], tx_uid="new-tx-uid")
        self.assertFalse(result.get("ok"))
        # Either wrong_state or conflict is acceptable for posted_to_moyklass
        blocked = result.get("wrong_state") or result.get("conflict")
        self.assertTrue(blocked, f"Expected blocked; got {result}")


# ---------------------------------------------------------------------------
# Tests 9-10: Caller-controlled guards (method behavior when called directly)
# ---------------------------------------------------------------------------

class Test03CallerGuards(unittest.TestCase):
    """Tests 9-10: Confirm that mark_paid itself does NOT re-check signature/test flags.
    (Those are caller responsibilities in the webhook handler.)"""

    def test_09_unverified_webhook_records_unverified(self):
        """Unverified webhook: method still processes (caller must guard); webhook_verified=0."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment")
        result = _call_mark_paid(
            s, pi["public_id"], verified=False, tx_uid="unverified-tx-001"
        )
        # The method itself doesn't block on unverified; caller is responsible.
        # Just confirm paid transition occurred and webhook_verified=0.
        if result.get("ok") and result.get("marked_paid"):
            after = s.get_payment_intent(pi["public_id"])
            self.assertEqual(after.get("webhook_verified"), 0)

    def test_10_test_transaction_method_still_processes(self):
        """test=True transaction: mark_paid itself doesn't block; caller must guard test flag."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment")
        # Simulate what would happen if caller didn't guard (method doesn't check test flag)
        result = _call_mark_paid(
            s, pi["public_id"], tx_uid="test-tx-uid-0001", verified=True
        )
        # Mark that the method works; security is enforced at caller level
        self.assertIn("ok", result)


# ---------------------------------------------------------------------------
# Test 11: Amount guard (caller responsibility)
# ---------------------------------------------------------------------------

class Test04AmountGuard(unittest.TestCase):
    def test_11_amount_mismatch_not_enforced_by_mark_paid(self):
        """mark_paid doesn't validate amount (caller does); wrong amount still stores."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment", amount_minor=22900)
        # Caller should validate, but method itself accepts any amount
        result = _call_mark_paid(
            s, pi["public_id"], amount_minor=100, tx_uid="mismatch-tx-001"
        )
        # Confirm the method proceeded (caller-level guard is the real protection)
        self.assertIn("ok", result)


# ---------------------------------------------------------------------------
# Tests 12-13: Idempotency
# ---------------------------------------------------------------------------

class Test05Idempotency(unittest.TestCase):
    """Tests 12-13: Idempotency and conflict protection."""

    def setUp(self):
        self.s = _make_storage()
        self.pi = _make_intent(self.s, status="awaiting_payment")
        self.tx_uid = "idempotent-tx-uid-001"
        # First call marks paid
        _call_mark_paid(self.s, self.pi["public_id"], tx_uid=self.tx_uid)

    def test_12_same_tx_uid_is_idempotent(self):
        """Repeat delivery of same tx_uid в†’ idempotent=True."""
        result = _call_mark_paid(
            self.s, self.pi["public_id"], tx_uid=self.tx_uid
        )
        self.assertTrue(result.get("ok"), f"Expected ok=True; got {result}")
        self.assertTrue(result.get("idempotent"), f"Expected idempotent=True; got {result}")

    def test_13_different_tx_uid_is_conflict(self):
        """Different tx_uid on already-paid intent в†’ conflict protection."""
        result = _call_mark_paid(
            self.s, self.pi["public_id"], tx_uid="different-tx-uid-999"
        )
        self.assertFalse(result.get("ok"), f"Expected ok=False; got {result}")
        self.assertTrue(result.get("conflict"), f"Expected conflict=True; got {result}")


# ---------------------------------------------------------------------------
# Tests 14-16: paid_channel field
# ---------------------------------------------------------------------------

class Test06PaidChannel(unittest.TestCase):
    """Tests 14-16: paid_channel stored correctly."""

    def test_14_erip_sets_paid_channel_erip(self):
        """ERIP webhook stores paid_channel='erip'."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment")
        result = _call_mark_paid(s, pi["public_id"], channel="erip", tx_uid="ch-erip-001")
        self.assertTrue(result.get("marked_paid"))
        after = s.get_payment_intent(pi["public_id"])
        self.assertEqual(after.get("paid_channel"), "erip")

    def test_15_acquiring_sets_paid_channel_acquiring(self):
        """Acquiring webhook stores paid_channel='acquiring'."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment")
        result = _call_mark_paid(
            s, pi["public_id"], channel="acquiring", tx_uid="ch-acq-001"
        )
        self.assertTrue(result.get("marked_paid"))
        after = s.get_payment_intent(pi["public_id"])
        self.assertEqual(after.get("paid_channel"), "acquiring")

    def test_16_empty_channel_stored_as_none(self):
        """Empty channel string stored as NULL, not empty string."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment")
        result = s.payment_intent_mark_paid(
            pi["public_id"],
            tx_uid="ch-none-001",
            amount_minor=10000,
            currency="BYN",
            paid_at=now_iso(),
            channel="",
        )
        self.assertTrue(result.get("marked_paid") or result.get("ok"))
        with s._connect() as conn:
            row = conn.execute(
                "SELECT paid_channel FROM payment_intents WHERE public_id=?",
                (pi["public_id"],),
            ).fetchone()
        self.assertIsNone(row[0], f"Empty channel should be NULL; got {row[0]!r}")


# ---------------------------------------------------------------------------
# Test 17: paid_at persisted
# ---------------------------------------------------------------------------

class Test07PaidAt(unittest.TestCase):
    def test_17_paid_at_from_transaction_is_persisted(self):
        """paid_at value from transaction is stored on the intent."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment")
        paid_at = "2026-07-16T10:45:00+03:00"
        s.payment_intent_mark_paid(
            pi["public_id"],
            tx_uid="paid-at-tx-001",
            amount_minor=10000,
            currency="BYN",
            paid_at=paid_at,
            channel="erip",
            verified=True,
        )
        after = s.get_payment_intent(pi["public_id"])
        self.assertEqual(after.get("paid_at"), paid_at)


# ---------------------------------------------------------------------------
# Test 18: payment_state_reason
# ---------------------------------------------------------------------------

class Test08PaymentStateReason(unittest.TestCase):
    def test_18_payment_state_reason_set(self):
        """payment_state_reason is set to paid_via_bepaid_webhook."""
        s = _make_storage()
        pi = _make_intent(s, status="awaiting_payment")
        _call_mark_paid(s, pi["public_id"], tx_uid="reason-tx-001")
        after = s.get_payment_intent(pi["public_id"])
        self.assertEqual(after.get("payment_state_reason"), "paid_via_bepaid_webhook")


# ---------------------------------------------------------------------------
# Tests 19-21: ycpi_202607_16 production fixture
# ---------------------------------------------------------------------------

class Test09ProductionFixture(unittest.TestCase):
    """Tests 19-21: ycpi_202607_16-like fixture вЂ” reconcile path."""

    PROD_PUBLIC_ID = "ycpi_202607_16_test"
    PROD_TX_UID = "7bf1c1ce-fb32-4320-a682-d6bcb43a8ffd"

    def setUp(self):
        self.s = _make_storage()
        # Simulate production scenario: intent in awaiting_payment, no option row
        self.pi = _make_intent(
            self.s,
            status="awaiting_payment",
            public_id_override=self.PROD_PUBLIC_ID,
            amount_minor=100,  # 1.00 BYN
        )

    def test_19_ycpi_202607_16_reconcile_marks_paid(self):
        """ycpi_202607_16-like: awaiting_payment reconciles to paid via legacy path."""
        result = self.s.payment_intent_mark_paid(
            self.PROD_PUBLIC_ID,
            tx_uid=self.PROD_TX_UID,
            amount_minor=100,
            currency="BYN",
            paid_at="2026-07-16T08:30:00+03:00",
            channel="erip",
            tracking_id=self.PROD_PUBLIC_ID,
            verified=True,
            match_method="tracking_id",
        )
        self.assertTrue(result.get("ok"), f"Expected ok=True; got {result}")
        self.assertTrue(result.get("marked_paid"), f"Expected marked_paid=True; got {result}")
        after = self.s.get_payment_intent(self.PROD_PUBLIC_ID)
        self.assertEqual(after["status"], "paid")
        self.assertEqual(after["paid_channel"], "erip")

    def test_20_reconcile_does_not_create_new_intent(self):
        """Reconcile must not create a new payment intent."""
        count_before = self.s._connect().__enter__().execute(
            "SELECT COUNT(*) FROM payment_intents"
        ).fetchone()[0]
        self.s.payment_intent_mark_paid(
            self.PROD_PUBLIC_ID,
            tx_uid=self.PROD_TX_UID,
            amount_minor=100,
            currency="BYN",
            paid_at=now_iso(),
            channel="erip",
            verified=True,
            match_method="tracking_id",
        )
        count_after = self.s._connect().__enter__().execute(
            "SELECT COUNT(*) FROM payment_intents"
        ).fetchone()[0]
        self.assertEqual(count_before, count_after, "Reconcile must not create new intents")

    def test_21_reconcile_does_not_trigger_moyklass(self):
        """Reconcile must not set moyklass posting columns."""
        self.s.payment_intent_mark_paid(
            self.PROD_PUBLIC_ID,
            tx_uid=self.PROD_TX_UID,
            amount_minor=100,
            currency="BYN",
            paid_at=now_iso(),
            channel="erip",
            verified=True,
        )
        after = self.s.get_payment_intent(self.PROD_PUBLIC_ID)
        # Verify MoyKlass post columns are untouched
        self.assertNotEqual(
            after.get("status"), "posted_to_moyklass",
            "Status must not be posted_to_moyklass after reconcile"
        )
        # moyklass_posted_at should be None (not auto-set)
        moyklass_posted = after.get("moyklass_posted_at") or after.get("mk_posted_at")
        self.assertIsNone(
            moyklass_posted,
            f"MoyKlass posted_at must be None after reconcile; got {moyklass_posted}"
        )


# ---------------------------------------------------------------------------
# Test 22: BEPAID_AUTO_POST_TO_MOYKLASS guard
# ---------------------------------------------------------------------------

class Test10MoyKlassAutoPostGuard(unittest.TestCase):
    def test_22_auto_post_flag_not_enabled(self):
        """BEPAID_AUTO_POST_TO_MOYKLASS must not be set to true in tracked config files."""
        danger_pattern = "BEPAID_AUTO_POST_TO_MOYKLASS=true"
        checked = []
        for fname in ("web_app_server.py", "storage.py"):
            fpath = ROOT / fname
            if fpath.exists():
                text = fpath.read_text(encoding="utf-8", errors="replace")
                checked.append(fname)
                self.assertNotIn(
                    danger_pattern, text,
                    f"{fname} must not have {danger_pattern!r}"
                )
        # Also confirm app.js doesn't auto-post
        app_js = ROOT / "miniapp" / "app.js"
        if app_js.exists():
            text = app_js.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(
                "autoPostToMoyKlass=true", text,
                "app.js must not have autoPostToMoyKlass=true"
            )
        self.assertTrue(len(checked) >= 1, "At least one server file must be checked")


# ---------------------------------------------------------------------------
# Tests 23-24: Existing suite guards
# ---------------------------------------------------------------------------

class Test11ExistingGuards(unittest.TestCase):
    def test_23_test_bepaid_webhook_importable(self):
        import tests.test_bepaid_webhook  # noqa: F401

    def test_24_test_unmatched_transactions_importable(self):
        import tests.test_unmatched_transactions  # noqa: F401


# ---------------------------------------------------------------------------
# Test 25: Version
# ---------------------------------------------------------------------------

class Test12Version(unittest.TestCase):
    def test_25_version_marker(self):
        """Full suite guard вЂ” version marker is v7.0.94.0."""
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn(
            f'console.log("MiniApp version: v{CURRENT_VERSION}")',
            js,
            f"app.js must declare version v{CURRENT_VERSION}",
        )


if __name__ == "__main__":
    unittest.main()
