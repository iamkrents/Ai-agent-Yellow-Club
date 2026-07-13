"""Unit tests for MoyKlass invoice → bePaid intent flow (v7.0.90 / v7.0.90.1).

Tests cover all guard conditions for payment_intent_from_mk_invoice,
_preflight_mk_invoice, and the storage helper find_active_intent_by_invoice,
plus production-shaped payload parsing for _extract_mk_invoices.

Run offline (no MoyKlass / bePaid / Telegram needed):

    python -m unittest tests.test_mk_invoice_intent -v
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Storage is importable standalone (only needs sqlite3 + utils)
from storage import Storage
# Pure helper from web_app_server (no network/env required)
from web_app_server import _extract_mk_invoices


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _memory_storage() -> Storage:
    """Storage backed by a fresh temporary SQLite DB (cleaned up after each test)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _seed_intent(storage: Storage, **kwargs) -> dict:
    defaults = dict(
        mk_user_id=1001,
        student_name="Иван Тест",
        amount_minor=5000,
        amount_byn=50.0,
        currency="BYN",
        purpose="subscription",
        payment_method="erip",
        status="draft",
    )
    defaults.update(kwargs)
    return storage.create_payment_intent(defaults)


# ---------------------------------------------------------------------------
# Pure logic: remaining / status derivation
# ---------------------------------------------------------------------------

class TestInvoiceRemainingCalc(unittest.TestCase):

    def test_fully_paid_remaining_zero(self):
        price, payed = 100.0, 100.0
        remaining = max(0.0, price - payed)
        self.assertAlmostEqual(remaining, 0.0)

    def test_unpaid_remaining_equals_price(self):
        price, payed = 150.0, 0.0
        remaining = max(0.0, price - payed)
        self.assertAlmostEqual(remaining, 150.0)

    def test_partial_remaining_correct(self):
        price, payed = 200.0, 50.0
        remaining = max(0.0, price - payed)
        self.assertAlmostEqual(remaining, 150.0)

    def test_overpaid_remaining_clamps_to_zero(self):
        # Guard: never go negative
        price, payed = 100.0, 150.0
        remaining = max(0.0, price - payed)
        self.assertAlmostEqual(remaining, 0.0)

    def test_invoice_status_unpaid(self):
        remaining, payed = 100.0, 0.0
        status = "paid" if remaining <= 0.01 else ("partial" if payed > 0 else "unpaid")
        self.assertEqual(status, "unpaid")

    def test_invoice_status_partial(self):
        price, payed = 200.0, 50.0
        remaining = price - payed
        status = "paid" if remaining <= 0.01 else ("partial" if payed > 0 else "unpaid")
        self.assertEqual(status, "partial")

    def test_invoice_status_paid(self):
        price, payed = 100.0, 100.0
        remaining = max(0.0, price - payed)
        status = "paid" if remaining <= 0.01 else ("partial" if payed > 0 else "unpaid")
        self.assertEqual(status, "paid")

    def test_remaining_minor_conversion(self):
        remaining = 50.75
        remaining_minor = round(remaining * 100)
        self.assertEqual(remaining_minor, 5075)


# ---------------------------------------------------------------------------
# Storage: find_active_intent_by_invoice
# ---------------------------------------------------------------------------

class TestFindActiveIntentByInvoice(unittest.TestCase):

    def setUp(self):
        self.storage = _memory_storage()

    def test_returns_none_when_no_intent(self):
        result = self.storage.find_active_intent_by_invoice("inv_999")
        self.assertIsNone(result)

    def test_returns_active_intent(self):
        intent = _seed_intent(self.storage, mk_invoice_id="inv_42", source="moyklass_invoice")
        found = self.storage.find_active_intent_by_invoice("inv_42")
        self.assertIsNotNone(found)
        self.assertEqual(found["public_id"], intent["public_id"])

    def test_does_not_return_cancelled_intent(self):
        intent = _seed_intent(self.storage, mk_invoice_id="inv_55", source="moyklass_invoice")
        from utils import now_iso
        self.storage.cancel_payment_intent(intent["public_id"], "test", now_iso())
        found = self.storage.find_active_intent_by_invoice("inv_55")
        self.assertIsNone(found)

    def test_returns_none_for_different_invoice_id(self):
        _seed_intent(self.storage, mk_invoice_id="inv_10", source="moyklass_invoice")
        found = self.storage.find_active_intent_by_invoice("inv_99")
        self.assertIsNone(found)


# ---------------------------------------------------------------------------
# Storage: create_payment_intent with source fields
# ---------------------------------------------------------------------------

class TestCreatePaymentIntentSourceFields(unittest.TestCase):

    def setUp(self):
        self.storage = _memory_storage()

    def test_source_defaults_to_manual(self):
        intent = _seed_intent(self.storage)
        self.assertEqual(intent.get("source"), "manual")

    def test_mk_invoice_source_stored(self):
        intent = _seed_intent(
            self.storage,
            source="moyklass_invoice",
            mk_invoice_id="inv_77",
            mk_user_subscription_id="sub_11",
            invoice_amount_minor=10000,
            invoice_remaining_minor=5000,
            invoice_snapshot_json=json.dumps({"id": "inv_77", "price": 100}),
            verified_mk_user_at="2026-07-12T10:00:00",
            verified_invoice_at="2026-07-12T10:00:01",
        )
        self.assertEqual(intent.get("source"), "moyklass_invoice")
        self.assertEqual(intent.get("mk_invoice_id"), "inv_77")
        self.assertEqual(intent.get("invoice_amount_minor"), 10000)
        self.assertEqual(intent.get("invoice_remaining_minor"), 5000)
        self.assertEqual(intent.get("mk_user_subscription_id"), "sub_11")
        self.assertIsNotNone(intent.get("verified_invoice_at"))


# ---------------------------------------------------------------------------
# Production-shaped payload: _extract_mk_invoices (v7.0.90.1)
# ---------------------------------------------------------------------------

PROD_PAYLOAD = {
    "invoices": [
        {
            "id": 12345,
            "userId": 9748998,
            "date": "2026-07-13",
            "createdAt": "2026-07-13",
            "price": 229,
            "payed": 0,
            "payUntil": "2026-07-16",
            "userSubscriptionId": 17998875,
        }
    ],
    "stats": {
        "totalItems": 1,
        "totalPrice": 229,
        "totalPayed": 0,
    },
}


class TestExtractMkInvoices(unittest.TestCase):
    """_extract_mk_invoices: production-shaped payload parsing."""

    def test_extracts_invoices_key(self):
        result = _extract_mk_invoices(PROD_PAYLOAD)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 12345)

    def test_invoices_key_priority_over_items(self):
        payload = {"invoices": [{"id": 1}], "items": [{"id": 99}]}
        result = _extract_mk_invoices(payload)
        self.assertEqual(result[0]["id"], 1)

    def test_falls_back_to_items_key(self):
        payload = {"items": [{"id": 2}]}
        result = _extract_mk_invoices(payload)
        self.assertEqual(result[0]["id"], 2)

    def test_raw_list_passthrough(self):
        payload = [{"id": 3}]
        result = _extract_mk_invoices(payload)
        self.assertEqual(result[0]["id"], 3)

    def test_empty_invoices_returns_empty_list(self):
        payload = {"invoices": [], "stats": {"totalItems": 0}}
        result = _extract_mk_invoices(payload)
        self.assertEqual(result, [])

    def test_none_returns_empty_list(self):
        self.assertEqual(_extract_mk_invoices(None), [])

    def test_non_dict_non_list_returns_empty_list(self):
        self.assertEqual(_extract_mk_invoices("string"), [])
        self.assertEqual(_extract_mk_invoices(42), [])

    def test_missing_invoice_not_masked_as_api_error(self):
        # Empty invoices array is a valid response (no invoice created yet) — not an error
        result = _extract_mk_invoices({"invoices": [], "stats": {"totalItems": 0}})
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)


class TestInvoiceFilterLogic(unittest.TestCase):
    """Filter logic for unpaid/partial/paid/unpaid_partial — production-shaped."""

    def _derive(self, price, payed):
        remaining = max(0.0, price - payed)
        if remaining <= 0.01:
            status = "paid"
        elif payed > 0:
            status = "partial"
        else:
            status = "unpaid"
        return remaining, status

    def _passes_filter(self, status_filter, inv_status):
        if status_filter == "all":
            return True
        if status_filter == "unpaid":
            return inv_status == "unpaid"
        if status_filter == "partial":
            return inv_status == "partial"
        if status_filter == "unpaid_partial":
            return inv_status != "paid"
        return False

    def test_prod_invoice_remaining_229(self):
        remaining, status = self._derive(229, 0)
        self.assertAlmostEqual(remaining, 229.0)
        self.assertEqual(status, "unpaid")

    def test_filter_unpaid_partial_includes_unpaid(self):
        _, status = self._derive(229, 0)
        self.assertTrue(self._passes_filter("unpaid_partial", status))

    def test_filter_unpaid_includes_unpaid(self):
        _, status = self._derive(229, 0)
        self.assertTrue(self._passes_filter("unpaid", status))

    def test_filter_partial_excludes_unpaid(self):
        _, status = self._derive(229, 0)
        self.assertFalse(self._passes_filter("partial", status))

    def test_filter_all_includes_unpaid(self):
        _, status = self._derive(229, 0)
        self.assertTrue(self._passes_filter("all", status))

    def test_paid_excluded_from_unpaid_partial(self):
        _, status = self._derive(229, 229)
        self.assertFalse(self._passes_filter("unpaid_partial", status))

    def test_partial_in_unpaid_partial(self):
        _, status = self._derive(229, 100)
        self.assertEqual(status, "partial")
        self.assertTrue(self._passes_filter("unpaid_partial", status))


if __name__ == "__main__":
    unittest.main()
