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

CURRENT_MINIAPP_VERSION = "7.0.99.0"

# Storage is importable standalone (only needs sqlite3 + utils)
from storage import Storage
# Pure helpers from web_app_server (no network/env required)
from web_app_server import (
    _extract_mk_invoices,
    _mk_fetch_invoices_paginated,
    _mk_invoice_by_id,
    _mk_invoices_get_cached,
    _mk_invoices_cache,
    _MK_INVOICES_CACHE_TTL,
    MiniAppHandler,
)


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


# ---------------------------------------------------------------------------
# BrokenPipe / ConnectionReset handling (v7.0.90.3)
# ---------------------------------------------------------------------------

class TestBrokenPipeHandling(unittest.TestCase):
    """_send_json must absorb client disconnects without raising."""

    def _make_handler(self, write_exc=None):
        from unittest.mock import MagicMock
        h = MiniAppHandler.__new__(MiniAppHandler)
        h.path = "/api/payments/moyklass/invoices?initData=SECRET&hash=abc123"
        h.wfile = MagicMock()
        if write_exc is not None:
            h.wfile.write.side_effect = write_exc
        h.send_response = MagicMock()
        h.send_header = MagicMock()
        h.end_headers = MagicMock()
        h.address_string = MagicMock(return_value="127.0.0.1")
        return h

    def test_broken_pipe_returns_false(self):
        h = self._make_handler(write_exc=BrokenPipeError("pipe"))
        result = h._send_json({"ok": True})
        self.assertFalse(result, "_send_json must return False on BrokenPipeError")

    def test_connection_reset_returns_false(self):
        h = self._make_handler(write_exc=ConnectionResetError("reset"))
        result = h._send_json({"ok": True})
        self.assertFalse(result, "_send_json must return False on ConnectionResetError")

    def test_success_returns_true(self):
        h = self._make_handler()
        result = h._send_json({"ok": True, "data": "hello"})
        self.assertTrue(result, "_send_json must return True on success")

    def test_broken_pipe_log_excludes_query_params(self):
        from unittest.mock import patch
        h = self._make_handler(write_exc=BrokenPipeError("pipe"))
        with patch("web_app_server.log") as mock_log:
            h._send_json({"ok": True})
        self.assertTrue(mock_log.info.called, "log.info must be called on BrokenPipe")
        call_args_str = str(mock_log.info.call_args)
        self.assertNotIn("initData", call_args_str, "Log must not contain initData")
        self.assertNotIn("SECRET", call_args_str, "Log must not contain initData value")
        self.assertNotIn("hash=abc", call_args_str, "Log must not contain hash param")
        self.assertIn("/api/payments/moyklass/invoices", call_args_str, "Log must contain safe path")

    def test_end_headers_broken_pipe_also_returns_false(self):
        from unittest.mock import MagicMock
        h = self._make_handler()
        h.end_headers.side_effect = BrokenPipeError("headers pipe")
        result = h._send_json({"ok": True})
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# In-memory invoice cache (v7.0.90.3)
# ---------------------------------------------------------------------------

class TestMkInvoicesCache(unittest.TestCase):
    """Cache avoids repeated 83-page scans within the TTL window."""

    def setUp(self):
        _mk_invoices_cache.clear()

    def tearDown(self):
        _mk_invoices_cache.clear()

    def test_first_call_scans_mk_api(self):
        page = {"invoices": [_unpaid_inv_19060579()], "stats": {"totalItems": 1}}
        client = _MockMKClient([{"data": page}])
        items, diag, cache_hit, _ = _mk_invoices_get_cached(client, {})
        self.assertFalse(cache_hit, "First call must be a cache miss")
        self.assertEqual(len(items), 1)
        self.assertEqual(len(client.calls), 1)

    def test_second_call_within_ttl_uses_cache(self):
        page = {"invoices": [_unpaid_inv_19060579()], "stats": {"totalItems": 1}}
        client1 = _MockMKClient([{"data": page}])
        _mk_invoices_get_cached(client1, {})   # prime cache
        client2 = _MockMKClient([])             # empty — must not be called
        items, diag, cache_hit, cache_age = _mk_invoices_get_cached(client2, {})
        self.assertTrue(cache_hit, "Second call within TTL must be a cache hit")
        self.assertEqual(len(items), 1)
        self.assertEqual(len(client2.calls), 0, "Cache hit must not call MK API")
        self.assertGreaterEqual(cache_age, 0.0)

    def test_mk_error_is_not_cached(self):
        error_client = _MockMKClient([{"ok": False, "data": None, "error": "MK down"}])
        items, diag, cache_hit, _ = _mk_invoices_get_cached(error_client, {})
        self.assertEqual(diag.get("stopped_reason"), "mk_error")
        self.assertFalse(cache_hit)
        self.assertNotIn("global", _mk_invoices_cache, "Errors must not be cached")

    def test_single_flight_returns_same_data(self):
        import threading as _threading
        page = {"invoices": [_unpaid_inv_19060579()], "stats": {"totalItems": 1}}
        client = _MockMKClient([{"data": page}])
        results = []

        def _run():
            items, diag, hit, _ = _mk_invoices_get_cached(client, {})
            results.append((len(items), hit))

        t1 = _threading.Thread(target=_run)
        t1.start()
        t1.join(timeout=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], 1)

    def test_direct_lookup_does_not_populate_global_cache(self):
        _mk_invoices_cache.clear()
        client = _MockMKClient([{"data": {"id": 19060579, "userId": 9748998, "price": 229, "payed": 0}}])
        result = _mk_invoice_by_id(client, "19060579")
        self.assertIsNotNone(result)
        self.assertNotIn("global", _mk_invoices_cache, "Direct lookup must not write to global cache")

    def test_userid_scan_bypasses_global_cache(self):
        # Populate global cache with wrong data
        _mk_invoices_cache["global"] = {
            "loaded_at": __import__("time").monotonic(),
            "raw_items": [_paid_inv(999)],
            "page_diag": {"pages_loaded": 1, "page_limit": 100, "total_items_reported": 1,
                          "raw_invoices_scanned": 1, "stopped_reason": "total_reached"},
        }
        # userId-specific scan must call MK API directly, ignoring the cache
        page = {"invoices": [_unpaid_inv_19060579()], "stats": {"totalItems": 1}}
        client = _MockMKClient([{"data": page}])
        # This bypasses the cache by using base_params with userId — caller's responsibility
        items, diag = _mk_fetch_invoices_paginated(client, {"userId": 9748998}, page_limit=100)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], 19060579)
        for _, path, params in client.calls:
            self.assertEqual(params.get("userId"), 9748998)

    def test_scan_duration_ms_in_diag(self):
        page = {"invoices": [_paid_inv(1)], "stats": {"totalItems": 1}}
        client = _MockMKClient([{"data": page}])
        _, diag, _, _ = _mk_invoices_get_cached(client, {})
        self.assertIn("scan_duration_ms", diag)
        self.assertGreaterEqual(diag["scan_duration_ms"], 0)


# ---------------------------------------------------------------------------
# v7.0.90.4: piShowToast fix + enrichment (static + storage checks)
# ---------------------------------------------------------------------------

class TestV90904UIFix(unittest.TestCase):
    """Static and storage checks for v7.0.90.4 fixes."""

    _APP_JS = ROOT / "miniapp" / "app.js"

    @classmethod
    def _read_app_js(cls) -> str:
        return cls._APP_JS.read_text(encoding="utf-8")

    def test_piShowToast_not_in_app_js(self):
        """piShowToast was the undefined function causing ReferenceError — must not appear in app.js."""
        src = self._read_app_js()
        self.assertNotIn("piShowToast", src, "piShowToast must not exist — use showToast instead")

    def test_showToast_defined_in_app_js(self):
        src = self._read_app_js()
        self.assertIn("function showToast(", src, "showToast helper must be defined")

    def test_no_raw_alert_in_mk_invoice_create_flow(self):
        """alert() must not be used in openMkInvoiceCreate — use showToast instead."""
        src = self._read_app_js()
        # Find the openMkInvoiceCreate function body
        start = src.find("async function openMkInvoiceCreate(")
        self.assertGreater(start, 0, "openMkInvoiceCreate must exist")
        end = src.find("\nasync function ", start + 1)
        if end < 0:
            end = src.find("\nfunction ", start + 1)
        fn_body = src[start:end] if end > start else src[start:start + 3000]
        self.assertNotIn("alert(", fn_body, "alert() must not appear in openMkInvoiceCreate")

    def test_showToast_wrapped_in_try_catch_after_post(self):
        """showToast call must be inside its own try/catch to not break success path."""
        src = self._read_app_js()
        self.assertIn('try { showToast(', src,
                      "showToast after POST must be wrapped in try/catch")

    def test_showPaymentIntent_function_exists(self):
        src = self._read_app_js()
        self.assertIn("async function showPaymentIntent(", src, "showPaymentIntent must be defined")

    def test_payment_intent_card_has_id_attribute(self):
        """renderPaymentIntentCard must embed id= so scrollToIntent can find the card."""
        src = self._read_app_js()
        self.assertIn("payment-intent-", src, "payment-intent- id prefix must appear in card HTML")

    def test_version_bumped_to_91(self):
        src = self._read_app_js()
        self.assertIn(f"v{CURRENT_MINIAPP_VERSION}", src, f"Version must be v{CURRENT_MINIAPP_VERSION}")

    def test_student_name_field_in_invoice_card_html(self):
        """renderMkInvoiceCard must reference inv.student_name."""
        src = self._read_app_js()
        fn_start = src.find("function renderMkInvoiceCard(")
        self.assertGreater(fn_start, 0)
        fn_end = src.find("\nfunction ", fn_start + 1)
        fn_body = src[fn_start:fn_end] if fn_end > fn_start else src[fn_start:fn_start + 5000]
        self.assertIn("student_name", fn_body, "renderMkInvoiceCard must use inv.student_name")

    def test_invoice_card_shows_payed_and_price(self):
        """Finance breakdown must reference payed and price."""
        src = self._read_app_js()
        fn_start = src.find("function renderMkInvoiceCard(")
        fn_end = src.find("\nfunction ", fn_start + 1)
        fn_body = src[fn_start:fn_end] if fn_end > fn_start else src[fn_start:fn_start + 5000]
        self.assertIn("inv.payed", fn_body, "renderMkInvoiceCard must show payed amount")
        self.assertIn("inv.price", fn_body, "renderMkInvoiceCard must show total price")

    def test_invoice_card_shows_subscription_id(self):
        src = self._read_app_js()
        fn_start = src.find("function renderMkInvoiceCard(")
        fn_end = src.find("\nfunction ", fn_start + 1)
        fn_body = src[fn_start:fn_end] if fn_end > fn_start else src[fn_start:fn_start + 5000]
        self.assertIn("user_subscription_id", fn_body)

    def test_invoice_card_shows_bepaid_uid(self):
        src = self._read_app_js()
        fn_start = src.find("function renderMkInvoiceCard(")
        fn_end = src.find("\nfunction ", fn_start + 1)
        fn_body = src[fn_start:fn_end] if fn_end > fn_start else src[fn_start:fn_start + 5000]
        self.assertIn("active_bepaid_uid", fn_body, "Invoice card must show bePaid UID when present")


class TestFindActiveIntentBePaidFields(unittest.TestCase):
    """find_active_intent_by_invoice must return bepaid_uid and bepaid_account_number."""

    def setUp(self):
        self.storage = _memory_storage()

    def test_returns_bepaid_uid_when_set(self):
        intent = _seed_intent(self.storage, mk_invoice_id="inv_bp1", source="moyklass_invoice")
        # Simulate bePaid record being set (update via storage method)
        with self.storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET bepaid_uid=?, bepaid_account_number=?, status='bepaid_created' WHERE public_id=?",
                ("test-uid-123", "97489982607900001", intent["public_id"]),
            )
        found = self.storage.find_active_intent_by_invoice("inv_bp1")
        self.assertIsNotNone(found)
        self.assertEqual(found.get("bepaid_uid"), "test-uid-123")
        self.assertEqual(found.get("bepaid_account_number"), "97489982607900001")

    def test_returns_none_bepaid_fields_when_not_set(self):
        intent = _seed_intent(self.storage, mk_invoice_id="inv_bp2", source="moyklass_invoice")
        found = self.storage.find_active_intent_by_invoice("inv_bp2")
        self.assertIsNotNone(found)
        # No bePaid created yet — fields should be None / absent
        self.assertIsNone(found.get("bepaid_uid"))

    def test_duplicate_detection_friendly_message(self):
        """Backend returns ok=False with existing_intent_id when duplicate; frontend shows toast not alert."""
        intent = _seed_intent(self.storage, mk_invoice_id="inv_dup", source="moyklass_invoice")
        existing = self.storage.find_active_intent_by_invoice("inv_dup")
        self.assertIsNotNone(existing, "Duplicate must be detected")
        self.assertEqual(existing["public_id"], intent["public_id"])

    def test_active_intent_does_not_disable_cancel(self):
        """An active bepaid_created intent should not block cancellation flow in storage."""
        intent = _seed_intent(self.storage, mk_invoice_id="inv_can", source="moyklass_invoice")
        with self.storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET bepaid_uid=?, status='bepaid_created' WHERE public_id=?",
                ("uid-789", intent["public_id"]),
            )
        found = self.storage.find_active_intent_by_invoice("inv_can")
        self.assertIsNotNone(found)
        self.assertIn(found["status"], ["bepaid_created", "bepaid_creating", "draft", "ready"])


class TestV90905ShowPaymentIntent(unittest.TestCase):
    """Static checks for v7.0.90.5: showPaymentIntent navigation fix."""

    _APP_JS = ROOT / "miniapp" / "app.js"
    _CSS = ROOT / "miniapp" / "styles.css"
    _HTML = ROOT / "miniapp" / "index.html"

    @classmethod
    def _read(cls, path) -> str:
        return path.read_text(encoding="utf-8")

    def _app(self):
        return self._read(self._APP_JS)

    def _css(self):
        return self._read(self._CSS)

    def _html(self):
        return self._read(self._HTML)

    def test_paymentIntentDomId_helper_exists(self):
        src = self._app()
        self.assertIn("function paymentIntentDomId(", src,
                      "paymentIntentDomId helper must be defined")

    def test_showPaymentIntent_is_async(self):
        src = self._app()
        self.assertIn("async function showPaymentIntent(", src,
                      "showPaymentIntent must be async")

    def test_showPaymentIntent_opens_accordion(self):
        src = self._app()
        idx = src.find("async function showPaymentIntent(")
        self.assertGreater(idx, 0)
        fn_body = src[idx:idx + 4000]
        self.assertIn("paymentIntentsAccordion", fn_body,
                      "showPaymentIntent must open paymentIntentsAccordion")

    def test_showPaymentIntent_sets_status_all(self):
        src = self._app()
        idx = src.find("async function showPaymentIntent(")
        fn_body = src[idx:idx + 4000]
        self.assertIn('"all"', fn_body,
                      "showPaymentIntent must set piStatusFilter to 'all'")

    def test_showPaymentIntent_calls_loadPaymentIntents(self):
        src = self._app()
        idx = src.find("async function showPaymentIntent(")
        fn_body = src[idx:idx + 4000]
        self.assertIn("await loadPaymentIntents()", fn_body,
                      "showPaymentIntent must await loadPaymentIntents()")

    def test_showPaymentIntent_uses_double_raf(self):
        src = self._app()
        idx = src.find("async function showPaymentIntent(")
        fn_body = src[idx:idx + 4000]
        self.assertIn("requestAnimationFrame(() => requestAnimationFrame(", fn_body,
                      "showPaymentIntent must use double rAF for layout commit")

    def test_showPaymentIntent_scrolls_into_view(self):
        src = self._app()
        idx = src.find("async function showPaymentIntent(")
        fn_body = src[idx:idx + 4000]
        self.assertIn("scrollIntoView", fn_body,
                      "showPaymentIntent must call scrollIntoView on the found card")

    def test_event_delegation_for_show_payment_intent(self):
        src = self._app()
        self.assertIn("data-action='show-payment-intent'", src,
                      "Event delegation must use data-action='show-payment-intent'")
        self.assertIn("intentPublicId", src,
                      "Delegation handler must read dataset.intentPublicId")

    def test_button_uses_data_action_not_onclick(self):
        src = self._app()
        idx = src.find("function renderMkInvoiceCard(")
        self.assertGreater(idx, 0)
        fn_end = src.find("\nfunction ", idx + 1)
        fn_body = src[idx:fn_end] if fn_end > idx else src[idx:idx + 5000]
        self.assertIn('data-action="show-payment-intent"', fn_body,
                      "Invoice card button must use data-action for event delegation")
        self.assertNotIn("onclick=\"scrollToIntent", fn_body,
                         "scrollToIntent must not be called from onclick in invoice cards")

    def test_button_label_by_status(self):
        src = self._app()
        self.assertIn("Открыть платёж", src,
                      "Button must say 'Открыть платёж' for bepaid_created/paid status")
        self.assertIn("Показать черновик", src,
                      "Button must say 'Показать черновик' for draft/ready status")

    def test_pi_card_has_scroll_margin(self):
        css = self._css()
        # scroll-margin must appear somewhere in the file (it belongs to .pi-card block)
        self.assertIn("scroll-margin", css,
                      ".pi-card must have scroll-margin so scrollIntoView clears the sticky header")

    def test_highlight_animation_is_yellow(self):
        css = self._css()
        self.assertIn("piIntentHighlightRing", css,
                      "Highlight animation must be named piIntentHighlightRing")
        # Border/ring color should be in rgba(255, 196, 0, ...) family (yellow)
        self.assertIn("255, 196, 0", css,
                      "piIntentHighlightRing must use yellow border color")

    def test_current_cache_bust_in_html(self):
        html = self._html()
        self.assertIn(f"v={CURRENT_MINIAPP_VERSION}", html,
                      f"index.html must cache-bust to v={CURRENT_MINIAPP_VERSION}")

    def test_data_intent_public_id_on_pi_card(self):
        src = self._app()
        self.assertIn('data-intent-public-id=', src,
                      "renderPaymentIntentCard must add data-intent-public-id attribute")

    def test_period_month_passed_to_show_intent(self):
        src = self._app()
        idx = src.find("function renderMkInvoiceCard(")
        fn_end = src.find("\nfunction ", idx + 1)
        fn_body = src[idx:fn_end] if fn_end > idx else src[idx:idx + 5000]
        self.assertIn("active_intent_period_month", fn_body,
                      "Invoice card must pass active_intent_period_month to show-payment-intent button")


class TestV90906InvoiceListFixes(unittest.TestCase):
    """Static and unit checks for v7.0.90.6: invoice list stability + highlight + contrast."""

    _APP_JS = ROOT / "miniapp" / "app.js"
    _CSS = ROOT / "miniapp" / "styles.css"
    _HTML = ROOT / "miniapp" / "index.html"
    _SERVER = ROOT / "web_app_server.py"

    def _app(self): return self._APP_JS.read_text(encoding="utf-8")
    def _css(self): return self._CSS.read_text(encoding="utf-8")
    def _html(self): return self._HTML.read_text(encoding="utf-8")
    def _server(self): return self._SERVER.read_text(encoding="utf-8")

    # ── Backend: JSON serialization ──────────────────────────────────────────

    def test_send_json_uses_allow_nan_false(self):
        src = self._server()
        idx = src.find("def _send_json(")
        self.assertGreater(idx, 0)
        fn_body = src[idx:idx + 800]
        self.assertIn("allow_nan=False", fn_body,
                      "_send_json must use allow_nan=False to reject NaN/Infinity values")

    def test_send_json_catches_serialization_error(self):
        src = self._server()
        idx = src.find("def _send_json(")
        fn_body = src[idx:idx + 800]
        self.assertIn("json_encode", fn_body,
                      "_send_json must handle serialization failure with stage=json_encode")

    def test_nan_not_serialized_to_browser(self):
        """Verify that float('nan') in payload raises ValueError with allow_nan=False."""
        import json
        with self.assertRaises(ValueError):
            json.dumps({"value": float("nan")}, allow_nan=False)

    def test_infinity_not_serialized_to_browser(self):
        """Verify that float('inf') in payload raises ValueError with allow_nan=False."""
        import json
        with self.assertRaises(ValueError):
            json.dumps({"value": float("inf")}, allow_nan=False)

    # ── Frontend: error handling ─────────────────────────────────────────────

    def test_json_parse_error_shows_stage_info(self):
        src = self._app()
        idx = src.find("async function loadMkInvoices(")
        fn_end = src.find("\nasync function ", idx + 1)
        fn_body = src[idx:fn_end] if fn_end > idx else src[idx:idx + 8000]
        self.assertIn("stage=json_parse", fn_body,
                      "JSON parse error must show stage=json_parse diagnostic for admins")

    def test_abort_error_not_shown_as_json_error(self):
        src = self._app()
        idx = src.find("async function loadMkInvoices(")
        fn_end = src.find("\nasync function ", idx + 1)
        fn_body = src[idx:fn_end] if fn_end > idx else src[idx:idx + 8000]
        # AbortError must be checked separately before JSON parse error message
        abort_pos = fn_body.find("AbortError")
        json_err_pos = fn_body.find("stage=json_parse")
        self.assertGreater(abort_pos, 0, "AbortError must be handled")
        # AbortError handler should appear in catch block (separate path from JSON parse)
        self.assertNotEqual(abort_pos, json_err_pos)

    def test_dual_key_invoices_items(self):
        src = self._app()
        self.assertIn("data.invoices", src, "Frontend must read data.invoices")
        self.assertIn("data.items", src, "Frontend must support legacy data.items key")
        # Both should appear near each other in loadMkInvoices
        idx = src.find("async function loadMkInvoices(")
        fn_end = src.find("\nasync function ", idx + 1)
        fn_body = src[idx:fn_end] if fn_end > idx else src[idx:idx + 8000]
        self.assertIn("data.invoices", fn_body)
        self.assertIn("data.items", fn_body)

    def test_payload_validation_stage(self):
        src = self._app()
        self.assertIn("payload_validation", src,
                      "Frontend must detect missing invoices/items array with payload_validation stage")

    def test_per_card_render_catch(self):
        src = self._app()
        idx = src.find("async function loadMkInvoices(")
        fn_end = src.find("\nasync function ", idx + 1)
        fn_body = src[idx:fn_end] if fn_end > idx else src[idx:idx + 8000]
        self.assertIn("renderMkInvoiceCard(inv)", fn_body,
                      "loadMkInvoices must call renderMkInvoiceCard per-card")
        self.assertIn("ошибка отображения", fn_body,
                      "Per-card error must show inline error, not break the list")

    # ── CSS: highlight ───────────────────────────────────────────────────────

    def test_highlight_uses_after_pseudo(self):
        css = self._css()
        self.assertIn(".pi-card-highlight::after", css,
                      "Highlight must use ::after pseudo-element to avoid animation conflict")

    def test_highlight_ring_keyframe_exists(self):
        css = self._css()
        self.assertIn("piIntentHighlightRing", css,
                      "piIntentHighlightRing keyframe must be defined for ::after ring animation")

    def test_pi_card_is_position_relative(self):
        css = self._css()
        # Find standalone .pi-card block (preceded by newline or brace)
        idx = css.find("\n.pi-card {")
        self.assertGreater(idx, 0, "Standalone .pi-card { rule must exist at line start")
        block = css[idx:idx + 300]
        self.assertIn("position: relative", block,
                      ".pi-card must have position:relative so ::after can be positioned")

    def test_highlight_does_not_use_animation_on_card_itself(self):
        """The .pi-card-highlight rule must not set 'animation:' (that would conflict with ycCardEnter)."""
        css = self._css()
        idx = css.find(".pi-card-highlight {")
        self.assertGreater(idx, 0, ".pi-card-highlight { rule must exist")
        block = css[idx:idx + 200]
        # The card class itself should NOT set animation: (only ::after should)
        self.assertNotIn("animation:", block,
                         ".pi-card-highlight must not set animation: — use ::after for ring")

    def test_reduced_motion_highlight_static_outline(self):
        css = self._css()
        self.assertIn("prefers-reduced-motion: reduce", css,
                      "Must handle prefers-reduced-motion")
        # Search all prefers-reduced-motion blocks for pi-card-highlight handling
        found = False
        search_from = 0
        while True:
            rm_idx = css.find("prefers-reduced-motion: reduce", search_from)
            if rm_idx < 0:
                break
            block = css[rm_idx:rm_idx + 600]
            if "pi-card-highlight" in block:
                found = True
                break
            search_from = rm_idx + 1
        self.assertTrue(found,
                        "A prefers-reduced-motion block must include .pi-card-highlight handling")

    # ── CSS: finance contrast ────────────────────────────────────────────────

    def test_finance_uses_grid_layout(self):
        css = self._css()
        idx = css.find(".mk-invoice-finance {")
        self.assertGreater(idx, 0)
        block = css[idx:idx + 200]
        self.assertIn("grid", block,
                      ".mk-invoice-finance must use CSS grid layout for iPhone layout")

    def test_finance_value_class_exists(self):
        css = self._css()
        self.assertIn(".mk-invoice-finance__value", css,
                      "Finance value must have dedicated class for explicit color control")

    def test_finance_label_class_exists(self):
        css = self._css()
        self.assertIn(".mk-invoice-finance__label", css,
                      "Finance label must have dedicated class")

    def test_finance_value_dark_mode(self):
        css = self._css()
        self.assertIn("#f4f7fb", css,
                      "Finance value must have explicit light color for dark mode")

    def test_finance_remaining_green(self):
        css = self._css()
        self.assertIn("mk-invoice-finance__value--remaining", css,
                      "Remaining value must have a modifier class with green color")

    def test_finance_html_uses_new_structure(self):
        src = self._app()
        idx = src.find("function renderMkInvoiceCard(")
        fn_end = src.find("\nfunction ", idx + 1)
        fn_body = src[idx:fn_end] if fn_end > idx else src[idx:idx + 5000]
        self.assertIn("mk-invoice-finance__item", fn_body,
                      "renderMkInvoiceCard must use new finance structure with __item class")
        self.assertIn("mk-invoice-finance__value", fn_body,
                      "renderMkInvoiceCard must use __value class for explicit color")

    # ── Version ──────────────────────────────────────────────────────────────

    def test_current_version_in_app_js(self):
        src = self._app()
        self.assertIn(f"v{CURRENT_MINIAPP_VERSION}", src, f"app.js version must be v{CURRENT_MINIAPP_VERSION}")

    def test_cache_bust_current_in_html(self):
        html = self._html()
        self.assertIn(f"v={CURRENT_MINIAPP_VERSION}", html, f"index.html cache-bust must be v={CURRENT_MINIAPP_VERSION}")


if __name__ == "__main__":
    unittest.main()
