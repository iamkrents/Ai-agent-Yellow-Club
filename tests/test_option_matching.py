"""Tests for v7.0.92.5 — option-aware bePaid webhook matching and reconciliation.

Covers:
- match_bepaid_transaction_to_payment_target (31 tests)
  - Acquiring channel: tracking_id / order_id / tx_uid / no-match
  - ERIP channel: tracking_id / account_number
  - Channel scoping (acquiring webhook never matches ERIP option and vice-versa)
  - Legacy fallback for ERIP-only intents
  - Conflict detection
  - Idempotency
  - payment_intent_mark_paid_via_option from awaiting_payment / partial_ready
  - list_unmatched_bepaid_transactions / get_bepaid_transaction_by_id
  - ycpi_202607_13 (failed intent) not affected

Run offline:
    python -m unittest tests.test_option_matching -v
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


# ─── helpers ─────────────────────────────────────────────────────────────────

def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_intent(storage: Storage, *, status: str = "awaiting_payment",
                 public_id: Optional[str] = None) -> dict:
    pi = storage.create_payment_intent({
        "mk_user_id": 8875658,
        "student_name": "Тест Тестович",
        "amount_minor": 100,
        "amount_byn": 1.00,
        "currency": "BYN",
        "purpose": "current_month",
        "period_month": "2026-07",
        "payment_method": "acquiring",
        "status": status,
        "created_by_tg_id": 1,
        "created_by_name": "Test",
    })
    if public_id:
        with storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET public_id=? WHERE id=?",
                (public_id, pi["id"]),
            )
        return storage.get_payment_intent(public_id)
    return storage.get_payment_intent(pi["public_id"])


def _make_acq_option(storage: Storage, pi: dict, *, tracking_id: str,
                     order_id: str = "ORD001", uid: str = "uid-001") -> dict:
    return storage.create_payment_intent_option(
        payment_intent_id=pi["id"],
        intent_public_id=pi["public_id"],
        channel="acquiring",
        shop_type="acquiring",
        bepaid_order_id=order_id,
        bepaid_tracking_id=tracking_id,
        bepaid_uid=uid,
    )


def _make_erip_option(storage: Storage, pi: dict, *, tracking_id: str,
                      order_id: str = "ORD002", account_number: str = "88756582607001") -> dict:
    return storage.create_payment_intent_option(
        payment_intent_id=pi["id"],
        intent_public_id=pi["public_id"],
        channel="erip",
        shop_type="erip",
        bepaid_order_id=order_id,
        bepaid_tracking_id=tracking_id,
        bepaid_account_number=account_number,
    )


def _tx(*, tracking_id: str = "", order_id: str = "", uid: str = "",
        account: str = "") -> dict:
    return {
        "tracking_id": tracking_id or None,
        "order_id": order_id or None,
        "transaction_uid": uid or None,
        "erip_account_number": account or None,
    }


def _store_bepaid_tx(storage: Storage, *, uid: str, tracking_id: str,
                     shop_type: str = "acquiring", status: str = "successful",
                     test: int = 0, webhook_verified: int = 1,
                     amount_minor: int = 100, intent_public_id: str = "") -> dict:
    tx, _ = storage.upsert_bepaid_transaction({
        "provider": "bepaid",
        "shop_type": shop_type,
        "transaction_uid": uid,
        "tracking_id": tracking_id,
        "order_id": f"ORD-{uid}",
        "status": status,
        "amount_minor": amount_minor,
        "amount_byn": amount_minor / 100,
        "currency": "BYN",
        "test": test,
    })
    if webhook_verified or intent_public_id:
        with storage._connect() as conn:
            conn.execute(
                "UPDATE bepaid_transactions SET webhook_verified=?, intent_public_id=? WHERE id=?",
                (webhook_verified, intent_public_id or None, tx["id"]),
            )
        tx = storage.get_bepaid_transaction_by_id(tx["id"])
    return tx


# ─── 1. Acquiring: match by tracking_id ─────────────────────────────────────

class Test01AcqMatchByTrackingId(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage)
        self.opt = _make_acq_option(self.storage, self.pi,
                                    tracking_id="ycpi_202607_14_acq",
                                    order_id="ORD-001", uid="uid-001")

    def test_01_match_by_tracking_id_returns_payment_option(self):
        result = self.storage.match_bepaid_transaction_to_payment_target(
            _tx(tracking_id="ycpi_202607_14_acq"), "acquiring"
        )
        self.assertTrue(result["matched"])
        self.assertEqual(result["target_type"], "payment_option")
        self.assertEqual(result["option_id"], self.opt["id"])
        self.assertEqual(result["parent_public_id"], self.pi["public_id"])

    def test_02_match_method_is_tracking_id(self):
        result = self.storage.match_bepaid_transaction_to_payment_target(
            _tx(tracking_id="ycpi_202607_14_acq"), "acquiring"
        )
        self.assertEqual(result["method"], "tracking_id")
        self.assertEqual(result["confidence"], "strong")


# ─── 2. Acquiring: match by order_id ────────────────────────────────────────

class Test02AcqMatchByOrderId(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage)
        self.opt = _make_acq_option(self.storage, self.pi,
                                    tracking_id="ycpi_202607_14_acq",
                                    order_id="ORD-ACQ-001")

    def test_03_match_by_order_id(self):
        result = self.storage.match_bepaid_transaction_to_payment_target(
            _tx(order_id="ORD-ACQ-001"), "acquiring"
        )
        self.assertTrue(result["matched"])
        self.assertEqual(result["target_type"], "payment_option")
        self.assertEqual(result["option_id"], self.opt["id"])
        self.assertEqual(result["method"], "order_id")


# ─── 3. Acquiring: match by transaction_uid ──────────────────────────────────

class Test03AcqMatchByTxUid(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage)
        self.opt = _make_acq_option(self.storage, self.pi,
                                    tracking_id="ycpi_202607_14_acq",
                                    uid="06006e9d-ed00-47a6-8863-07d754744424")

    def test_04_match_by_tx_uid(self):
        result = self.storage.match_bepaid_transaction_to_payment_target(
            _tx(uid="06006e9d-ed00-47a6-8863-07d754744424"), "acquiring"
        )
        self.assertTrue(result["matched"])
        self.assertEqual(result["method"], "transaction_uid")


# ─── 4. Acquiring channel: no match → no_match ──────────────────────────────

class Test04AcqNoMatch(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()

    def test_05_no_option_no_intent_returns_unmatched(self):
        result = self.storage.match_bepaid_transaction_to_payment_target(
            _tx(tracking_id="unknown_tracking_id"), "acquiring"
        )
        self.assertFalse(result["matched"])
        self.assertEqual(result["target_type"], "none")
        self.assertIsNone(result["option_id"])


# ─── 5. Channel scoping: acquiring webhook NEVER matches ERIP option ─────────

class Test05ChannelScopingAcqVsErip(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        pi = _make_intent(self.storage)
        # ERIP option with the same tracking_id suffix as acquiring
        _make_erip_option(self.storage, pi, tracking_id="ycpi_202607_14",
                          order_id="ORD-ERIP-001", account_number="887565826071")

    def test_06_acquiring_webhook_does_not_match_erip_option(self):
        # tracking_id "ycpi_202607_14" exists only on erip option,
        # but we're searching with channel="acquiring" → must not match
        result = self.storage.match_bepaid_transaction_to_payment_target(
            _tx(tracking_id="ycpi_202607_14"), "acquiring"
        )
        self.assertFalse(result["matched"])

    def test_07_erip_webhook_does_not_match_acquiring_option(self):
        s = _make_storage()
        pi2 = _make_intent(s)
        _make_acq_option(s, pi2, tracking_id="ycpi_202607_15_acq")
        result = s.match_bepaid_transaction_to_payment_target(
            _tx(tracking_id="ycpi_202607_15_acq"), "erip"
        )
        self.assertFalse(result["matched"])


# ─── 6. ERIP option match by tracking_id ─────────────────────────────────────

class Test06EripMatchByTrackingId(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage)
        self.opt = _make_erip_option(self.storage, self.pi,
                                     tracking_id="ycpi_202607_14",
                                     order_id="ORD-E-001", account_number="887565826071")

    def test_08_erip_match_by_tracking_id(self):
        result = self.storage.match_bepaid_transaction_to_payment_target(
            _tx(tracking_id="ycpi_202607_14"), "erip"
        )
        self.assertTrue(result["matched"])
        self.assertEqual(result["target_type"], "payment_option")
        self.assertEqual(result["method"], "tracking_id")


# ─── 7. ERIP option match by account_number ──────────────────────────────────

class Test07EripMatchByAccountNumber(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage)
        self.opt = _make_erip_option(self.storage, self.pi,
                                     tracking_id="ycpi_acct_test",
                                     account_number="887565826071")

    def test_09_erip_match_by_account_number(self):
        result = self.storage.match_bepaid_transaction_to_payment_target(
            _tx(account="887565826071"), "erip"
        )
        self.assertTrue(result["matched"])
        self.assertEqual(result["method"], "account_number")
        self.assertEqual(result["option_id"], self.opt["id"])


# ─── 8. Acquiring channel: account_number never used ─────────────────────────

class Test08AcqNoAccountNumberMatch(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        pi = _make_intent(self.storage)
        _make_acq_option(self.storage, pi, tracking_id="ycpi_acq_test")

    def test_10_acquiring_webhook_ignores_account_number_field(self):
        # Even if the field is present, acquiring channel never uses it
        result = self.storage.match_bepaid_transaction_to_payment_target(
            _tx(account="887565826071"), "acquiring"
        )
        self.assertFalse(result["matched"])


# ─── 9. Legacy fallback: ERIP intent without options row ─────────────────────

class Test09LegacyFallback(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        pi = storage = self.storage
        self.pi = pi.create_payment_intent({
            "mk_user_id": 1,
            "student_name": "Legacy",
            "amount_minor": 100,
            "amount_byn": 1.0,
            "currency": "BYN",
            "purpose": "current_month",
            "period_month": "2026-07",
            "payment_method": "erip",
            "status": "bepaid_created",
            "created_by_tg_id": 1,
            "created_by_name": "Test",
        })
        pub = self.pi["public_id"]
        with storage._connect() as conn:
            conn.execute(
                """UPDATE payment_intents SET
                   bepaid_tracking_id=?,
                   bepaid_order_id='LEG-001',
                   bepaid_uid='legacy-uid'
                   WHERE public_id=?""",
                (pub, pub),
            )
        self.pi = storage.get_payment_intent(pub)

    def test_11_legacy_intent_matched_as_legacy_intent(self):
        result = self.storage.match_bepaid_transaction_to_payment_target(
            _tx(tracking_id=self.pi["bepaid_tracking_id"]), "erip"
        )
        self.assertTrue(result["matched"])
        self.assertEqual(result["target_type"], "legacy_intent")


# ─── 10. Conflict: two different options match same transaction ───────────────

class Test10OptionConflict(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        pi1 = _make_intent(self.storage, public_id="ycpi_conflict_1")
        pi2 = _make_intent(self.storage, public_id="ycpi_conflict_2")
        # pi1 option: tracking_id=conflict_track, order_id=ORD-C1
        _make_acq_option(self.storage, pi1,
                         tracking_id="conflict_track", order_id="ORD-C1")
        # pi2 option: tracking_id=other_track, order_id=ORD-C2
        _make_acq_option(self.storage, pi2,
                         tracking_id="other_track", order_id="ORD-C2")

    def test_12_conflict_when_identifiers_point_to_different_options(self):
        # tracking_id "conflict_track" matches pi1's option
        # order_id "ORD-C2" matches pi2's option → conflict
        result = self.storage.match_bepaid_transaction_to_payment_target(
            _tx(tracking_id="conflict_track", order_id="ORD-C2"), "acquiring"
        )
        self.assertTrue(result["matched"])
        self.assertEqual(result["confidence"], "conflict")
        self.assertIsNone(result["option_id"])
        self.assertGreater(len(result["conflicts"]), 1)


# ─── 11. mark_paid_via_option from awaiting_payment ──────────────────────────

class Test11MarkPaidViaOptionFromAwaitingPayment(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage, status="awaiting_payment")
        self.opt = _make_acq_option(self.storage, self.pi,
                                    tracking_id="ycpi_202607_14_acq",
                                    order_id="ORD-MKP-001")

    def test_13_mark_paid_from_awaiting_payment_succeeds(self):
        result = self.storage.payment_intent_mark_paid_via_option(
            self.pi["public_id"],
            option_id=self.opt["id"],
            channel="acquiring",
            tx_uid="tx-awaiting-001",
            amount_minor=100,
            currency="BYN",
            paid_at="2026-07-14T12:00:00",
            tracking_id="ycpi_202607_14_acq",
            order_id="ORD-MKP-001",
        )
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("marked_paid"))
        self.assertEqual(result["intent"]["status"], "paid")
        self.assertEqual(result["intent"]["paid_channel"], "acquiring")


# ─── 12. mark_paid_via_option from partial_ready ─────────────────────────────

class Test12MarkPaidViaOptionFromPartialReady(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage, status="partial_ready")
        self.opt = _make_acq_option(self.storage, self.pi,
                                    tracking_id="ycpi_partial_acq",
                                    order_id="ORD-PR-001")

    def test_14_mark_paid_from_partial_ready_succeeds(self):
        result = self.storage.payment_intent_mark_paid_via_option(
            self.pi["public_id"],
            option_id=self.opt["id"],
            channel="acquiring",
            tx_uid="tx-partial-001",
            amount_minor=100,
            currency="BYN",
            paid_at="2026-07-14T12:00:00",
        )
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["intent"]["status"], "paid")


# ─── 13. mark_paid_via_option from bepaid_created ────────────────────────────

class Test13MarkPaidViaOptionFromBePaidCreated(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage, status="bepaid_created")
        self.opt = _make_acq_option(self.storage, self.pi,
                                    tracking_id="ycpi_bc_acq",
                                    order_id="ORD-BC-001")

    def test_15_mark_paid_from_bepaid_created_succeeds(self):
        result = self.storage.payment_intent_mark_paid_via_option(
            self.pi["public_id"],
            option_id=self.opt["id"],
            channel="acquiring",
            tx_uid="tx-bc-001",
            amount_minor=100,
            currency="BYN",
            paid_at="2026-07-14T12:00:00",
        )
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["intent"]["status"], "paid")


# ─── 14. mark_paid_via_option: wrong state (cancelled) ───────────────────────

class Test14MarkPaidWrongState(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage, status="cancelled")
        self.opt = _make_acq_option(self.storage, self.pi,
                                    tracking_id="ycpi_cancelled_acq",
                                    order_id="ORD-CXL-001")

    def test_16_mark_paid_from_cancelled_fails(self):
        result = self.storage.payment_intent_mark_paid_via_option(
            self.pi["public_id"],
            option_id=self.opt["id"],
            channel="acquiring",
            tx_uid="tx-cxl-001",
            amount_minor=100,
            currency="BYN",
            paid_at="2026-07-14T12:00:00",
        )
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("wrong_state"))


# ─── 15. Idempotency: repeat webhook same tx_uid ─────────────────────────────

class Test15IdempotencyRepeatWebhook(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage, status="awaiting_payment")
        self.opt = _make_acq_option(self.storage, self.pi,
                                    tracking_id="ycpi_idem_acq",
                                    order_id="ORD-IDEM-001")

    def test_17_idempotent_on_repeat_webhook(self):
        kwargs = dict(
            option_id=self.opt["id"],
            channel="acquiring",
            tx_uid="tx-idem-001",
            amount_minor=100,
            currency="BYN",
            paid_at="2026-07-14T12:00:00",
        )
        first = self.storage.payment_intent_mark_paid_via_option(
            self.pi["public_id"], **kwargs
        )
        self.assertTrue(first.get("ok"))
        second = self.storage.payment_intent_mark_paid_via_option(
            self.pi["public_id"], **kwargs
        )
        self.assertTrue(second.get("ok"))
        self.assertTrue(second.get("idempotent"))
        # Status is still paid, not double_payment
        self.assertEqual(second["intent"]["status"], "paid")


# ─── 16. Sibling superseded after option paid ────────────────────────────────

class Test16SiblingSuperseded(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage, status="awaiting_payment")
        self.acq_opt = _make_acq_option(self.storage, self.pi,
                                        tracking_id="ycpi_sibling_acq",
                                        order_id="ORD-SIB-001")
        self.erip_opt = _make_erip_option(self.storage, self.pi,
                                          tracking_id="ycpi_sibling_erip",
                                          order_id="ORD-SIB-002",
                                          account_number="887565826072")

    def test_18_erip_option_superseded_after_acquiring_paid(self):
        result = self.storage.payment_intent_mark_paid_via_option(
            self.pi["public_id"],
            option_id=self.acq_opt["id"],
            channel="acquiring",
            tx_uid="tx-sib-001",
            amount_minor=100,
            currency="BYN",
            paid_at="2026-07-14T12:00:00",
        )
        self.assertTrue(result.get("ok"))
        self.assertGreaterEqual(result.get("siblings_superseded", 0), 1)
        # ERIP sibling should be superseded
        erip_after = self.storage.get_option_by_channel(
            self.pi["public_id"], "erip"
        )
        self.assertIsNotNone(erip_after)
        self.assertEqual(erip_after.get("status"), "superseded")


# ─── 17. paid_channel stored correctly ───────────────────────────────────────

class Test17PaidChannelStored(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage, status="awaiting_payment")
        self.opt = _make_acq_option(self.storage, self.pi,
                                    tracking_id="ycpi_paid_ch_acq",
                                    order_id="ORD-CH-001")

    def test_19_paid_channel_is_acquiring(self):
        self.storage.payment_intent_mark_paid_via_option(
            self.pi["public_id"],
            option_id=self.opt["id"],
            channel="acquiring",
            tx_uid="tx-ch-001",
            amount_minor=100,
            currency="BYN",
            paid_at="2026-07-14T12:00:00",
        )
        pi_after = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi_after["paid_channel"], "acquiring")


# ─── 18. list_unmatched_bepaid_transactions ──────────────────────────────────

class Test18ListUnmatched(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()

    def test_20_unmatched_tx_appears_in_list(self):
        _store_bepaid_tx(self.storage, uid="unm-001", tracking_id="ycpi_unmatched_acq",
                         webhook_verified=1, test=0, intent_public_id="")
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tracking_id"], "ycpi_unmatched_acq")

    def test_21_matched_tx_excluded_from_list(self):
        _store_bepaid_tx(self.storage, uid="m-001", tracking_id="ycpi_matched",
                         webhook_verified=1, test=0, intent_public_id="ycpi_202607_14")
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 0)

    def test_22_test_tx_excluded_from_list(self):
        _store_bepaid_tx(self.storage, uid="test-001", tracking_id="ycpi_test",
                         webhook_verified=1, test=1, intent_public_id="")
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 0)

    def test_23_unverified_tx_excluded_from_list(self):
        # webhook_verified=0 must be excluded: only cryptographically verified webhooks
        # are eligible for the admin reconcile flow. signature verification is independent
        # of matching and is now persisted BEFORE matching (v7.0.92.5.2).
        _store_bepaid_tx(self.storage, uid="unverified-001", tracking_id="ycpi_unverified",
                         webhook_verified=0, test=0, intent_public_id="")
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 0, "webhook_verified=0 must be excluded from unmatched list")

    def test_24_failed_tx_excluded_from_list(self):
        _store_bepaid_tx(self.storage, uid="failed-001", tracking_id="ycpi_failed",
                         status="failed", webhook_verified=1, test=0, intent_public_id="")
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 0)


# ─── 19. get_bepaid_transaction_by_id ────────────────────────────────────────

class Test19GetTransactionById(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()

    def test_25_get_tx_by_id_returns_dict(self):
        tx = _store_bepaid_tx(self.storage, uid="gtx-001",
                               tracking_id="ycpi_gtx_acq")
        fetched = self.storage.get_bepaid_transaction_by_id(tx["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["transaction_uid"], "gtx-001")

    def test_26_get_tx_by_nonexistent_id_returns_none(self):
        result = self.storage.get_bepaid_transaction_by_id(99999)
        self.assertIsNone(result)


# ─── 20. Empty transaction fields: no empty-string false matches ──────────────

class Test20EmptyFieldsNoFalseMatch(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        pi = _make_intent(self.storage)
        _make_acq_option(self.storage, pi,
                         tracking_id="ycpi_real_tracking",
                         order_id="ORD-REAL-001")

    def test_27_empty_transaction_no_match(self):
        result = self.storage.match_bepaid_transaction_to_payment_target(
            _tx(), "acquiring"
        )
        self.assertFalse(result["matched"])


# ─── 21. ycpi_202607_13 (failed intent) is not affected ─────────────────────

class Test21FailedIntentNotAffected(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        self.pi_failed = _make_intent(self.storage, status="cancelled",
                                      public_id="ycpi_202607_13")
        _make_acq_option(self.storage, self.pi_failed,
                         tracking_id="ycpi_202607_13_acq",
                         order_id="ORD-FAIL-001")

        self.pi_active = _make_intent(self.storage, status="awaiting_payment",
                                      public_id="ycpi_202607_14")
        self.opt_active = _make_acq_option(self.storage, self.pi_active,
                                           tracking_id="ycpi_202607_14_acq",
                                           order_id="ORD-ACT-001")

    def test_28_webhook_for_14_does_not_touch_13(self):
        # Webhook for ycpi_202607_14_acq should only match the active intent
        result = self.storage.match_bepaid_transaction_to_payment_target(
            _tx(tracking_id="ycpi_202607_14_acq"), "acquiring"
        )
        self.assertTrue(result["matched"])
        self.assertEqual(result["parent_public_id"], "ycpi_202607_14")

    def test_29_failed_intent_cannot_be_marked_paid_via_option(self):
        result = self.storage.payment_intent_mark_paid_via_option(
            "ycpi_202607_13",
            option_id=self.opt_active["id"],
            channel="acquiring",
            tx_uid="tx-fail-001",
            amount_minor=100,
            currency="BYN",
            paid_at="2026-07-14T12:00:00",
        )
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("wrong_state"))

    def test_30_failed_intent_status_unchanged(self):
        self.storage.payment_intent_mark_paid_via_option(
            "ycpi_202607_13",
            option_id=self.opt_active["id"],
            channel="acquiring",
            tx_uid="tx-fail-002",
            amount_minor=100,
            currency="BYN",
            paid_at="2026-07-14T12:00:00",
        )
        pi_check = self.storage.get_payment_intent("ycpi_202607_13")
        self.assertEqual(pi_check["status"], "cancelled")


# ─── 22. Target type in match result when option found ───────────────────────

class Test22TargetTypeFields(unittest.TestCase):

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage, public_id="ycpi_target_test")
        self.opt = _make_acq_option(self.storage, self.pi,
                                    tracking_id="ycpi_target_test_acq",
                                    order_id="ORD-TT-001")

    def test_31_payment_intent_id_returned_in_match(self):
        result = self.storage.match_bepaid_transaction_to_payment_target(
            _tx(tracking_id="ycpi_target_test_acq"), "acquiring"
        )
        self.assertEqual(result["payment_intent_id"], self.pi["id"])
        self.assertEqual(result["parent_public_id"], "ycpi_target_test")
        self.assertEqual(result["channel"], "acquiring")
        self.assertEqual(result["conflicts"], [])


if __name__ == "__main__":
    unittest.main()
