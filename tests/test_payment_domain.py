"""
Tests for payment_domain.py — canonical payment domain rules.
v7.0.96.1
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from payment_domain import (
    PAYMENT_INTENT_ACTIVE_STATUSES,
    PAYMENT_INTENT_PAID_STATUSES,
    PAYMENT_INTENT_FINAL_STATUSES,
    PAYMENT_INTENT_CANCELLED_STATUSES,
    MOYKLASS_INVOICE_INTENT_SOURCES,
    PAYMENT_CHANNEL_ERIP,
    PAYMENT_CHANNEL_ACQUIRING,
    is_moyklass_invoice_intent,
    is_payment_verified,
    is_posted_to_moyklass,
    is_cancelled_intent,
    is_active_intent,
    resolve_effective_payment_channel,
    can_create_payment_options,
    can_publish_to_parent,
    can_post_to_moyklass,
    build_invoice_deduplication_key,
    build_posting_idempotency_key,
    is_source_reference_valid,
)

CURRENT_VERSION = "7.0.98.0"


# ---------------------------------------------------------------------------
# 01–05  Status constants
# ---------------------------------------------------------------------------

class TestStatusConstants(unittest.TestCase):

    def test_01_active_statuses_is_frozenset(self):
        self.assertIsInstance(PAYMENT_INTENT_ACTIVE_STATUSES, frozenset)

    def test_02_active_statuses_contains_nine_entries(self):
        self.assertEqual(len(PAYMENT_INTENT_ACTIVE_STATUSES), 9)

    def test_03_paid_statuses_subset_of_active(self):
        # paid and posted_to_moyklass are considered active
        for s in PAYMENT_INTENT_PAID_STATUSES:
            self.assertIn(s, PAYMENT_INTENT_ACTIVE_STATUSES)

    def test_04_cancelled_statuses_not_in_active(self):
        for s in PAYMENT_INTENT_CANCELLED_STATUSES:
            self.assertNotIn(s, PAYMENT_INTENT_ACTIVE_STATUSES)

    def test_05_final_statuses_include_paid_and_cancelled(self):
        self.assertIn("paid", PAYMENT_INTENT_FINAL_STATUSES)
        self.assertIn("posted_to_moyklass", PAYMENT_INTENT_FINAL_STATUSES)
        self.assertIn("cancelled", PAYMENT_INTENT_FINAL_STATUSES)
        self.assertIn("error", PAYMENT_INTENT_FINAL_STATUSES)


# ---------------------------------------------------------------------------
# 06–08  Source constants
# ---------------------------------------------------------------------------

class TestSourceConstants(unittest.TestCase):

    def test_06_moyklass_invoice_sources_contains_both(self):
        self.assertIn("moyklass_invoice", MOYKLASS_INVOICE_INTENT_SOURCES)
        self.assertIn("moyklass_invoice_automation", MOYKLASS_INVOICE_INTENT_SOURCES)

    def test_07_manual_source_not_in_sources(self):
        self.assertNotIn("manual", MOYKLASS_INVOICE_INTENT_SOURCES)
        self.assertNotIn("manual_input", MOYKLASS_INVOICE_INTENT_SOURCES)

    def test_08_channel_constants(self):
        self.assertEqual(PAYMENT_CHANNEL_ERIP, "erip")
        self.assertEqual(PAYMENT_CHANNEL_ACQUIRING, "acquiring")


# ---------------------------------------------------------------------------
# 09–12  is_moyklass_invoice_intent
# ---------------------------------------------------------------------------

class TestIsMoyklassInvoiceIntent(unittest.TestCase):

    def test_09_manual_source_returns_false(self):
        self.assertFalse(is_moyklass_invoice_intent({"source": "manual"}))

    def test_10_moyklass_invoice_returns_true(self):
        self.assertTrue(is_moyklass_invoice_intent({"source": "moyklass_invoice"}))

    def test_11_automation_source_returns_true(self):
        self.assertTrue(is_moyklass_invoice_intent({"source": "moyklass_invoice_automation"}))

    def test_12_empty_source_returns_false(self):
        self.assertFalse(is_moyklass_invoice_intent({}))


# ---------------------------------------------------------------------------
# 13–16  is_payment_verified
# ---------------------------------------------------------------------------

class TestIsPaymentVerified(unittest.TestCase):

    def _paid(self, **kwargs):
        base = {"status": "paid", "webhook_verified": True, "paid_transaction_uid": "uid-abc"}
        base.update(kwargs)
        return base

    def test_13_paid_with_all_fields_returns_true(self):
        self.assertTrue(is_payment_verified(self._paid()))

    def test_14_posted_to_moyklass_with_all_fields_returns_true(self):
        self.assertTrue(is_payment_verified(self._paid(status="posted_to_moyklass")))

    def test_15_missing_tx_uid_returns_false(self):
        self.assertFalse(is_payment_verified(self._paid(paid_transaction_uid="")))

    def test_16_not_verified_webhook_returns_false(self):
        self.assertFalse(is_payment_verified(self._paid(webhook_verified=False)))


# ---------------------------------------------------------------------------
# 17–20  is_posted_to_moyklass
# ---------------------------------------------------------------------------

class TestIsPostedToMoyklass(unittest.TestCase):

    def _posted(self, **kwargs):
        base = {
            "status": "posted_to_moyklass",
            "mk_payment_id": 37473176,
            "mk_posting_status": "posted",
        }
        base.update(kwargs)
        return base

    def test_17_fully_posted_returns_true(self):
        self.assertTrue(is_posted_to_moyklass(self._posted()))

    def test_18_missing_mk_payment_id_returns_false(self):
        self.assertFalse(is_posted_to_moyklass(self._posted(mk_payment_id=None)))

    def test_19_wrong_mk_posting_status_returns_false(self):
        self.assertFalse(is_posted_to_moyklass(self._posted(mk_posting_status="claiming")))

    def test_20_wrong_status_returns_false(self):
        self.assertFalse(is_posted_to_moyklass(self._posted(status="paid")))


# ---------------------------------------------------------------------------
# 21–23  is_cancelled_intent / is_active_intent
# ---------------------------------------------------------------------------

class TestCancelledAndActive(unittest.TestCase):

    def test_21_cancelled_returns_true(self):
        self.assertTrue(is_cancelled_intent({"status": "cancelled"}))

    def test_22_error_status_is_cancelled(self):
        self.assertTrue(is_cancelled_intent({"status": "error"}))

    def test_23_paid_not_cancelled_but_active(self):
        self.assertFalse(is_cancelled_intent({"status": "paid"}))
        self.assertTrue(is_active_intent({"status": "paid"}))


# ---------------------------------------------------------------------------
# 24–28  resolve_effective_payment_channel
# ---------------------------------------------------------------------------

class TestResolveEffectivePaymentChannel(unittest.TestCase):

    def test_24_paid_channel_wins_over_payment_method(self):
        # Production fixture ycpi_202607_19: payment_method=erip, paid_channel=acquiring
        intent = {"payment_method": "erip", "paid_channel": "acquiring"}
        self.assertEqual(resolve_effective_payment_channel(intent), "acquiring")

    def test_25_no_paid_channel_uses_payment_method(self):
        intent = {"payment_method": "erip"}
        self.assertEqual(resolve_effective_payment_channel(intent), "erip")

    def test_26_empty_paid_channel_uses_payment_method(self):
        intent = {"payment_method": "acquiring", "paid_channel": ""}
        self.assertEqual(resolve_effective_payment_channel(intent), "acquiring")

    def test_27_unknown_values_default_to_erip(self):
        intent = {"payment_method": "unknown", "paid_channel": "unknown"}
        self.assertEqual(resolve_effective_payment_channel(intent), "erip")

    def test_28_empty_intent_defaults_to_erip(self):
        self.assertEqual(resolve_effective_payment_channel({}), "erip")


# ---------------------------------------------------------------------------
# 29–32  Guard predicates
# ---------------------------------------------------------------------------

class TestGuardPredicates(unittest.TestCase):

    def test_29_can_create_payment_options_blocked_for_paid(self):
        self.assertFalse(can_create_payment_options({"status": "paid"}))

    def test_30_can_create_payment_options_allowed_for_draft(self):
        self.assertTrue(can_create_payment_options({"status": "draft"}))

    def test_31_can_post_to_moyklass_requires_paid_status(self):
        self.assertFalse(can_post_to_moyklass({"status": "awaiting_payment"}))
        self.assertTrue(can_post_to_moyklass({"status": "paid"}))

    def test_32_can_post_to_moyklass_blocked_if_already_has_mk_payment_id(self):
        self.assertFalse(can_post_to_moyklass({"status": "paid", "mk_payment_id": 12345}))


# ---------------------------------------------------------------------------
# 33–36  Idempotency key builders
# ---------------------------------------------------------------------------

class TestIdempotencyKeys(unittest.TestCase):

    def test_33_dedup_key_automation_intent(self):
        intent = {"source": "moyklass_invoice_automation", "mk_invoice_id": "19102120"}
        self.assertEqual(build_invoice_deduplication_key(intent), "mk_invoice:19102120")

    def test_34_dedup_key_manual_returns_none(self):
        intent = {"source": "manual", "mk_invoice_id": "19102120"}
        self.assertIsNone(build_invoice_deduplication_key(intent))

    def test_35_dedup_key_missing_mk_invoice_id_returns_none(self):
        intent = {"source": "moyklass_invoice_automation"}
        self.assertIsNone(build_invoice_deduplication_key(intent))

    def test_36_posting_idempotency_key_format(self):
        intent = {"public_id": "ycpi_202607_19", "paid_transaction_uid": "tx-uid-abc"}
        self.assertEqual(
            build_posting_idempotency_key(intent), "post:ycpi_202607_19:tx-uid-abc"
        )


# ---------------------------------------------------------------------------
# 37–40  is_source_reference_valid
# ---------------------------------------------------------------------------

class TestSourceReferenceValid(unittest.TestCase):

    def test_37_valid_automation_intent(self):
        intent = {
            "source": "moyklass_invoice_automation",
            "mk_invoice_id": "19102120",
            "source_reference": "mk_invoice_19102120",
        }
        self.assertTrue(is_source_reference_valid(intent))

    def test_38_invalid_automation_intent(self):
        intent = {
            "source": "moyklass_invoice_automation",
            "mk_invoice_id": "19102120",
            "source_reference": "wrong_value",
        }
        self.assertFalse(is_source_reference_valid(intent))

    def test_39_non_automation_intent_always_valid(self):
        # source_reference check is N/A for manual intents
        intent = {"source": "manual", "source_reference": "anything"}
        self.assertTrue(is_source_reference_valid(intent))

    def test_40_production_fixture_ycpi_202607_19(self):
        # Canonical production intent: verify domain rules are consistent
        intent = {
            "source": "moyklass_invoice_automation",
            "source_reference": "mk_invoice_19102120",
            "mk_invoice_id": "19102120",
            "mk_user_id": "9748998",
            "mk_user_subscription_id": "18037719",
            "amount_minor": 100,
            "payment_method": "erip",
            "bepaid_shop_type": "erip",
            "paid_channel": "acquiring",
            "status": "posted_to_moyklass",
            "mk_payment_id": 37473176,
            "mk_posting_status": "posted",
            "webhook_verified": True,
            "paid_transaction_uid": "tx-prod-19102120",
        }
        self.assertTrue(is_moyklass_invoice_intent(intent))
        self.assertTrue(is_source_reference_valid(intent))
        self.assertTrue(is_posted_to_moyklass(intent))
        self.assertFalse(is_cancelled_intent(intent))
        self.assertTrue(is_active_intent(intent))
        self.assertEqual(resolve_effective_payment_channel(intent), "acquiring")
        self.assertFalse(can_post_to_moyklass(intent))  # already posted
        dedup = build_invoice_deduplication_key(intent)
        self.assertEqual(dedup, "mk_invoice:19102120")


if __name__ == "__main__":
    unittest.main()
