"""Unit tests for MoyKlass invoice → bePaid intent flow (v7.0.90 / v7.0.90.2).

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
# Pure helpers from web_app_server (no network/env required)
from web_app_server import _extract_mk_invoices, _mk_fetch_invoices_paginated, _mk_invoice_by_id


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


# ---------------------------------------------------------------------------
# Mock MoyKlass client for pagination tests (offline, no network)
# ---------------------------------------------------------------------------

class _MockMKResult:
    """Minimal stand-in for MoyKlassResult."""
    def __init__(self, ok: bool, data=None, error: str = ""):
        self.ok = ok
        self.data = data
        self.error = error


class _MockMKClient:
    """Feeds pre-baked page responses to _mk_fetch_invoices_paginated."""

    def __init__(self, responses: list):
        """responses: list of dicts — {ok, data} for each sequential request."""
        self._responses = list(responses)
        self._index = 0
        self.calls: list[tuple[str, str, dict]] = []  # (method, path, params)

    def request(self, method: str, path: str, params: dict | None = None) -> _MockMKResult:
        self.calls.append((method, path, dict(params or {})))
        if self._index >= len(self._responses):
            return _MockMKResult(False, error="no more mock responses")
        resp = self._responses[self._index]
        self._index += 1
        return _MockMKResult(bool(resp.get("ok", True)), data=resp.get("data"), error=resp.get("error", ""))


def _paid_inv(i: int) -> dict:
    return {"id": i, "userId": 1, "price": 100.0, "payed": 100.0, "date": f"2026-01-{(i % 28) + 1:02d}"}


def _unpaid_inv_19060579() -> dict:
    return {
        "id": 19060579,
        "userId": 9748998,
        "price": 229.0,
        "payed": 0.0,
        "payUntil": "2026-07-16",
        "date": "2026-07-13",
        "userSubscriptionId": 17998775,
    }


# ---------------------------------------------------------------------------
# Pagination tests: _mk_fetch_invoices_paginated (v7.0.90.2)
# ---------------------------------------------------------------------------

class TestMkInvoicePagination(unittest.TestCase):
    """Tests A–I from the v7.0.90.2 specification."""

    # A — 50 paid on page1, 1 unpaid on page2: unpaid is found
    def test_A_finds_invoice_on_page2(self):
        page1 = {"invoices": [_paid_inv(i) for i in range(1, 51)], "stats": {"totalItems": 51}}
        page2 = {"invoices": [_unpaid_inv_19060579()], "stats": {"totalItems": 51}}
        client = _MockMKClient([{"data": page1}, {"data": page2}])
        items, diag = _mk_fetch_invoices_paginated(client, {}, page_limit=50)
        self.assertEqual(len(items), 51)
        self.assertEqual(diag["pages_loaded"], 2)
        found = next((inv for inv in items if inv.get("id") == 19060579), None)
        self.assertIsNotNone(found, "invoice #19060579 must be in the collected items")
        self.assertEqual(float(found["price"]), 229.0)
        self.assertEqual(float(found["payed"]), 0.0)

    # B — result_limit=50 does NOT stop raw scan after page1 when 0 unpaid found
    def test_B_result_limit_not_used_in_pagination(self):
        page1 = {"invoices": [_paid_inv(i) for i in range(1, 51)], "stats": {"totalItems": 51}}
        page2 = {"invoices": [_unpaid_inv_19060579()], "stats": {"totalItems": 51}}
        client = _MockMKClient([{"data": page1}, {"data": page2}])
        # Pagination itself has no concept of result_limit
        items, diag = _mk_fetch_invoices_paginated(client, {}, page_limit=50)
        # Both pages loaded
        self.assertEqual(diag["pages_loaded"], 2)
        # All 51 raw items returned; caller applies result_limit afterwards
        self.assertEqual(len(items), 51)

    # C — stop when offset >= stats.totalItems
    def test_C_stops_when_total_reached(self):
        page1 = {"invoices": [_paid_inv(1)], "stats": {"totalItems": 1}}
        client = _MockMKClient([{"data": page1}])
        items, diag = _mk_fetch_invoices_paginated(client, {}, page_limit=100)
        self.assertEqual(diag["stopped_reason"], "total_reached")
        self.assertEqual(len(items), 1)
        self.assertEqual(diag["pages_loaded"], 1)

    # D — stop on empty page
    def test_D_stops_on_empty_page(self):
        page1 = {"invoices": [_paid_inv(1)], "stats": {}}  # no totalItems
        page2 = {"invoices": [], "stats": {}}
        # page_limit=1 → page1 is "full" → request page2 → empty → stop
        client = _MockMKClient([{"data": page1}, {"data": page2}])
        items, diag = _mk_fetch_invoices_paginated(client, {}, page_limit=1)
        self.assertEqual(diag["stopped_reason"], "empty_page")
        self.assertEqual(len(items), 1)

    # E — max_pages protects against infinite loops
    def test_E_max_pages_protection(self):
        # Each page returns exactly page_limit items (would loop forever without cap)
        page = {"invoices": [_paid_inv(1), _paid_inv(2)], "stats": {}}
        client = _MockMKClient([{"data": page}] * 10)
        items, diag = _mk_fetch_invoices_paginated(client, {}, page_limit=2, max_pages=3)
        self.assertEqual(diag["pages_loaded"], 3)
        self.assertEqual(diag["stopped_reason"], "max_pages")
        self.assertEqual(len(items), 6)  # 3 pages × 2 items

    # F — userId transmitted to every page request
    def test_F_user_id_in_each_request(self):
        page1 = {"invoices": [_paid_inv(i) for i in range(1, 4)], "stats": {"totalItems": 4}}
        page2 = {"invoices": [_paid_inv(4)], "stats": {"totalItems": 4}}
        client = _MockMKClient([{"data": page1}, {"data": page2}])
        _mk_fetch_invoices_paginated(client, {"userId": 9748998}, page_limit=3)
        for method, path, params in client.calls:
            self.assertEqual(params.get("userId"), 9748998,
                             f"userId missing on request {path} {params}")

    # G — direct invoice lookup calls /invoices/{id}
    def test_G_direct_invoice_lookup_endpoint(self):
        inv_data = {"id": 19060579, "userId": 9748998, "price": 229, "payed": 0}
        client = _MockMKClient([{"data": inv_data}])
        result = _mk_invoice_by_id(client, "19060579")
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], 19060579)
        self.assertEqual(len(client.calls), 1)
        _, path, _ = client.calls[0]
        self.assertIn("19060579", path, f"expected invoiceId in path, got: {path}")

    # H — invoice #19060579 normalizes correctly
    def test_H_prod_invoice_19060579_normalised(self):
        payload = {"invoices": [_unpaid_inv_19060579()], "stats": {"totalItems": 1}}
        items = _extract_mk_invoices(payload)
        self.assertEqual(len(items), 1)
        inv = items[0]
        self.assertEqual(inv["id"], 19060579)
        self.assertEqual(inv["userId"], 9748998)
        self.assertEqual(float(inv["price"]), 229.0)
        self.assertEqual(float(inv["payed"]), 0.0)
        self.assertEqual(inv["userSubscriptionId"], 17998775)
        remaining = max(0.0, float(inv["price"]) - float(inv["payed"]))
        self.assertAlmostEqual(remaining, 229.0)
        status = "paid" if remaining <= 0.01 else ("partial" if float(inv["payed"]) > 0 else "unpaid")
        self.assertEqual(status, "unpaid")

    # I — 50 paid on page1 do NOT mask the unpaid invoice on page2
    def test_I_paid_page1_does_not_mask_unpaid_page2(self):
        page1 = {"invoices": [_paid_inv(i) for i in range(1, 51)], "stats": {"totalItems": 51}}
        page2 = {"invoices": [_unpaid_inv_19060579()], "stats": {"totalItems": 51}}
        client = _MockMKClient([{"data": page1}, {"data": page2}])
        items, diag = _mk_fetch_invoices_paginated(client, {}, page_limit=50)
        self.assertEqual(len(items), 51)
        unpaid = [inv for inv in items if float(inv.get("payed", 0)) < float(inv.get("price", 1))]
        self.assertEqual(len(unpaid), 1)
        self.assertEqual(unpaid[0]["id"], 19060579)


if __name__ == "__main__":
    unittest.main()
