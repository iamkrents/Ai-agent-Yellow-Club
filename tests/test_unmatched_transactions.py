"""Tests for v7.0.92.5.1 — unmatched bePaid transaction list and reconciliation.

Covers the production bug where transaction 156 (successful, verified, no_match)
was invisible in the unmatched list because:
  1. webhook_verified was only set in bepaid_transaction_link_intent (match path).
  2. list_unmatched_bepaid_transactions required webhook_verified=1.
  3. bepaid_reconcile_stored_transaction blocked on webhook_verified=0.
  4. API key was "transactions" but frontend read "data.items".

Run offline:
    python -m unittest tests.test_unmatched_transactions -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from web_app_server import MiniAppContext

APP_JS = ROOT / "miniapp" / "app.js"


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


def _auth(uid: int = 1) -> dict:
    return {"ok": True, "user_id": uid}


def _store_tx(storage: Storage, *, uid: str, tracking_id: str,
              shop_type: str = "acquiring", status: str = "successful",
              test: int = 0, webhook_verified: int = 0,
              amount_minor: int = 100, intent_public_id: str = "",
              match_status: str = "") -> dict:
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
    # Directly set controlled columns
    with storage._connect() as conn:
        conn.execute(
            """UPDATE bepaid_transactions SET
               webhook_verified=?, intent_public_id=?, match_status=?
               WHERE id=?""",
            (webhook_verified, intent_public_id or None, match_status or None, tx["id"]),
        )
    return storage.get_bepaid_transaction_by_id(tx["id"])


def _make_pi_with_acq_option(storage: Storage, *, public_id: str,
                              status: str = "awaiting_payment") -> tuple:
    pi = storage.create_payment_intent({
        "mk_user_id": 1,
        "student_name": "Test",
        "amount_minor": 100,
        "amount_byn": 1.0,
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
# 1. list_unmatched_bepaid_transactions — storage level
# ═══════════════════════════════════════════════════════════════════════════

class Test01StorageUnmatchedList(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()

    def test_01_successful_verified_no_match_appears(self):
        """Production-shape tx: successful, webhook_verified=1, no intent_public_id."""
        _store_tx(self.storage, uid="t01", tracking_id="ycpi_202607_14_acq",
                  webhook_verified=1, test=0)
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tracking_id"], "ycpi_202607_14_acq")

    def test_02_successful_unverified_no_match_appears(self):
        """webhook_verified=0 (old no_match bug) but status=successful → MUST appear."""
        _store_tx(self.storage, uid="t02", tracking_id="ycpi_unverified_acq",
                  webhook_verified=0, test=0)
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 1, "no_match bug: tx with webhook_verified=0 must still appear")

    def test_03_transaction_156_shaped_fixture_appears(self):
        """Production-exact fixture for transaction 156."""
        _store_tx(self.storage,
                  uid="06006e9d-ed00-47a6-8863-07d754744424",
                  tracking_id="ycpi_202607_14_acq",
                  shop_type="acquiring",
                  status="successful",
                  test=0,
                  webhook_verified=0,   # as it exists in production DB
                  amount_minor=100)
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["transaction_uid"],
                         "06006e9d-ed00-47a6-8863-07d754744424")

    def test_04_pending_not_returned(self):
        _store_tx(self.storage, uid="t04", tracking_id="trk4", status="pending")
        self.assertEqual(len(self.storage.list_unmatched_bepaid_transactions()), 0)

    def test_05_failed_not_returned(self):
        _store_tx(self.storage, uid="t05", tracking_id="trk5", status="failed")
        self.assertEqual(len(self.storage.list_unmatched_bepaid_transactions()), 0)

    def test_06_test_transaction_not_returned(self):
        _store_tx(self.storage, uid="t06", tracking_id="trk6",
                  status="successful", test=1)
        self.assertEqual(len(self.storage.list_unmatched_bepaid_transactions()), 0)

    def test_07_already_matched_not_returned(self):
        _store_tx(self.storage, uid="t07", tracking_id="trk7",
                  status="successful", test=0,
                  intent_public_id="ycpi_202607_14")
        self.assertEqual(len(self.storage.list_unmatched_bepaid_transactions()), 0)

    def test_08_refunded_not_returned(self):
        _store_tx(self.storage, uid="t08", tracking_id="trk8", status="refund")
        self.assertEqual(len(self.storage.list_unmatched_bepaid_transactions()), 0)

    def test_09_duplicate_matched_not_returned(self):
        _store_tx(self.storage, uid="t09", tracking_id="trk9",
                  status="successful", test=0,
                  intent_public_id="ycpi_some_intent",
                  match_status="duplicate")
        self.assertEqual(len(self.storage.list_unmatched_bepaid_transactions()), 0)


# ═══════════════════════════════════════════════════════════════════════════
# 2. bepaid_transaction_set_verified
# ═══════════════════════════════════════════════════════════════════════════

class Test02SetVerified(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()

    def test_10_set_verified_updates_flag(self):
        tx = _store_tx(self.storage, uid="sv01", tracking_id="trk_sv",
                       webhook_verified=0)
        self.assertEqual(tx.get("webhook_verified"), 0)
        self.storage.bepaid_transaction_set_verified(tx["id"])
        updated = self.storage.get_bepaid_transaction_by_id(tx["id"])
        self.assertEqual(updated["webhook_verified"], 1)


# ═══════════════════════════════════════════════════════════════════════════
# 3. API endpoint bepaid_list_unmatched_transactions
# ═══════════════════════════════════════════════════════════════════════════

class Test03ApiUnmatchedEndpoint(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def test_11_api_returns_items_key(self):
        _store_tx(self.storage, uid="api01", tracking_id="trk_api",
                  status="successful", test=0)
        result = self.ctx.bepaid_list_unmatched_transactions(_auth())
        self.assertTrue(result.get("ok"))
        self.assertIn("items", result, "Response must use key 'items', not 'transactions'")
        self.assertNotIn("transactions", result, "Old key 'transactions' must be removed")

    def test_12_api_returns_count(self):
        _store_tx(self.storage, uid="api02", tracking_id="trk_c",
                  status="successful", test=0)
        result = self.ctx.bepaid_list_unmatched_transactions(_auth())
        self.assertEqual(result.get("count"), 1)

    def test_13_api_items_is_array(self):
        result = self.ctx.bepaid_list_unmatched_transactions(_auth())
        self.assertIsInstance(result.get("items"), list)

    def test_14_api_includes_transaction_156_shape(self):
        _store_tx(self.storage,
                  uid="06006e9d-ed00-47a6-8863-07d754744424",
                  tracking_id="ycpi_202607_14_acq",
                  shop_type="acquiring",
                  status="successful",
                  test=0,
                  webhook_verified=0,
                  amount_minor=100)
        result = self.ctx.bepaid_list_unmatched_transactions(_auth())
        items = result.get("items", [])
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["tracking_id"], "ycpi_202607_14_acq")
        self.assertEqual(item["status"], "successful")
        self.assertEqual(item["channel"], "acquiring")
        self.assertEqual(item["currency"], "BYN")
        self.assertEqual(item["amount_minor"], 100)

    def test_15_api_no_raw_fields_exposed(self):
        _store_tx(self.storage, uid="api05", tracking_id="trk_safe",
                  status="successful", test=0)
        result = self.ctx.bepaid_list_unmatched_transactions(_auth())
        item = result["items"][0]
        # Must NOT expose sensitive fields
        for forbidden in ("raw_json", "customer_first_name", "customer_last_name",
                          "customer_phone", "customer_email", "billing_phone"):
            self.assertNotIn(forbidden, item, f"Field '{forbidden}' must not be in response")

    def test_16_api_denied_for_non_admin(self):
        ctx = _make_ctx(self.storage)
        ctx._role_store[2] = "client"
        result = ctx.bepaid_list_unmatched_transactions({"ok": True, "user_id": 2})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "access_denied")

    def test_17_api_items_include_match_status(self):
        _store_tx(self.storage, uid="api07", tracking_id="trk_ms",
                  status="successful", test=0)
        result = self.ctx.bepaid_list_unmatched_transactions(_auth())
        item = result["items"][0]
        self.assertIn("match_status", item)
        self.assertEqual(item["match_status"], "no_match")

    def test_18_api_items_include_signature_verified(self):
        _store_tx(self.storage, uid="api08", tracking_id="trk_sv2",
                  status="successful", test=0, webhook_verified=1)
        result = self.ctx.bepaid_list_unmatched_transactions(_auth())
        item = result["items"][0]
        self.assertIn("signature_verified", item)
        self.assertTrue(item["signature_verified"])


# ═══════════════════════════════════════════════════════════════════════════
# 4. reconcile endpoint
# ═══════════════════════════════════════════════════════════════════════════

class Test04Reconcile(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        self.pi, self.opt = _make_pi_with_acq_option(
            self.storage, public_id="ycpi_202607_14", status="awaiting_payment"
        )

    def _tx_156_shaped(self, *, webhook_verified: int = 0) -> dict:
        return _store_tx(
            self.storage,
            uid="06006e9d-ed00-47a6-8863-07d754744424",
            tracking_id="ycpi_202607_14_acq",
            shop_type="acquiring",
            status="successful",
            test=0,
            webhook_verified=webhook_verified,
            amount_minor=100,
        )

    def test_19_reconcile_succeeds_with_webhook_verified_0(self):
        """Production tx 156: webhook_verified=0 due to no_match bug — reconcile must work."""
        tx = self._tx_156_shaped(webhook_verified=0)
        result = self.ctx.bepaid_reconcile_stored_transaction(
            _auth(), str(tx["id"]), {}
        )
        self.assertTrue(result.get("ok"), f"Expected ok=True, got: {result}")

    def test_20_reconcile_marks_parent_intent_paid(self):
        tx = self._tx_156_shaped()
        self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        pi_after = self.storage.get_payment_intent("ycpi_202607_14")
        self.assertEqual(pi_after["status"], "paid")

    def test_21_reconcile_sets_paid_channel_acquiring(self):
        tx = self._tx_156_shaped()
        self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        pi_after = self.storage.get_payment_intent("ycpi_202607_14")
        self.assertEqual(pi_after["paid_channel"], "acquiring")

    def test_22_reconcile_supersedes_erip_sibling(self):
        # Add ERIP sibling option
        self.storage.create_payment_intent_option(
            payment_intent_id=self.pi["id"],
            intent_public_id="ycpi_202607_14",
            channel="erip",
            shop_type="erip",
            bepaid_tracking_id="ycpi_202607_14",
            bepaid_account_number="887565826071",
        )
        tx = self._tx_156_shaped()
        result = self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        self.assertTrue(result.get("ok"))
        erip_opt = self.storage.get_option_by_channel("ycpi_202607_14", "erip")
        self.assertEqual(erip_opt.get("status"), "superseded")

    def test_23_reconcile_is_idempotent(self):
        tx = self._tx_156_shaped()
        first = self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        self.assertTrue(first.get("ok"))
        second = self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        self.assertTrue(second.get("ok"))
        self.assertTrue(second.get("idempotent"))

    def test_24_after_reconcile_tx_disappears_from_unmatched_list(self):
        tx = self._tx_156_shaped()
        # Before reconcile: appears in unmatched list
        before = self.ctx.bepaid_list_unmatched_transactions(_auth())
        self.assertEqual(before["count"], 1)
        # Reconcile
        self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        # After reconcile: gone from unmatched list
        after = self.ctx.bepaid_list_unmatched_transactions(_auth())
        self.assertEqual(after["count"], 0)

    def test_25_reconcile_blocked_for_test_transaction(self):
        tx = _store_tx(self.storage, uid="test-rec", tracking_id="ycpi_test_acq",
                       status="successful", test=1)
        result = self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "test_transaction_ignored")


# ═══════════════════════════════════════════════════════════════════════════
# 5. Frontend JS — static checks
# ═══════════════════════════════════════════════════════════════════════════

class Test05FrontendJS(unittest.TestCase):

    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_26_version_is_92_5_1(self):
        self.assertIn("v7.0.92.5.1", self.js)

    def test_27_reads_data_items_not_data_transactions(self):
        self.assertIn("data.items", self.js)
        # The old bug: data.transactions in loadUnmatchedTransactions
        # Check that it's no longer used for the unmatched list
        import re
        # Find loadUnmatchedTransactions function body
        fn_match = re.search(
            r"async function loadUnmatchedTransactions\(\)(.*?)^window\.reconcileTransaction",
            self.js, re.DOTALL | re.MULTILINE
        )
        self.assertIsNotNone(fn_match, "loadUnmatchedTransactions function not found")
        fn_body = fn_match.group(1)
        self.assertIn("data.items", fn_body, "Must read data.items not data.transactions")
        self.assertNotIn("data.transactions", fn_body,
                         "Old key data.transactions must not be in loadUnmatchedTransactions")

    def test_28_uses_Array_isArray_guard(self):
        self.assertIn("Array.isArray(data.items)", self.js)

    def test_29_reconcile_function_exists(self):
        self.assertIn("window.reconcileTransaction", self.js)
        self.assertIn("reconcileTransaction", self.js)

    def test_30_checks_data_ok(self):
        self.assertIn("data.ok", self.js)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Index.html cache-bust
# ═══════════════════════════════════════════════════════════════════════════

class Test06IndexHtml(unittest.TestCase):

    def setUp(self):
        self.html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")

    def test_31_cache_bust_is_92_5_1(self):
        self.assertIn("v=7.0.92.5.1", self.html)
        self.assertNotIn("v=7.0.92.5\"", self.html)


if __name__ == "__main__":
    unittest.main()
