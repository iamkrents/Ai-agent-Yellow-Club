"""Tests for v7.0.92.5.2 — unmatched transaction list and reconciliation with strict security.

Security model:
- webhook_verified=1 is set immediately after cryptographic signature check,
  BEFORE matching. It never depends on matching outcome.
- list_unmatched requires webhook_verified=1 (hard filter).
- reconcile hard-blocks on webhook_verified != 1.
- webhook_verified=0 transactions are never surfaced in admin reconcile flow.

Run offline:
    python -m unittest tests.test_unmatched_transactions -v
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
# 1. list_unmatched_bepaid_transactions — strict security filter
# ═══════════════════════════════════════════════════════════════════════════

class Test01StorageUnmatchedList(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()

    def test_01_verified_successful_no_match_appears(self):
        """Correctly verified tx (webhook_verified=1) appears in unmatched list."""
        _store_tx(self.storage, uid="t01", tracking_id="ycpi_202607_14_acq",
                  webhook_verified=1, test=0)
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tracking_id"], "ycpi_202607_14_acq")

    def test_02_unverified_webhook_0_excluded(self):
        """webhook_verified=0 must be excluded — security requirement."""
        _store_tx(self.storage, uid="t02", tracking_id="ycpi_unverified_acq",
                  webhook_verified=0, test=0)
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 0,
                         "webhook_verified=0 must never appear in unmatched list")

    def test_03_transaction_156_shaped_with_verified_0_excluded(self):
        """Production tx 156 with webhook_verified=0 (as in DB) is EXCLUDED until recovered."""
        _store_tx(self.storage,
                  uid="06006e9d-ed00-47a6-8863-07d754744424",
                  tracking_id="ycpi_202607_14_acq",
                  shop_type="acquiring",
                  status="successful",
                  test=0,
                  webhook_verified=0,
                  amount_minor=100)
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 0,
                         "tx 156 with webhook_verified=0 must be excluded pending recovery")

    def test_04_transaction_156_shaped_with_verified_1_appears(self):
        """tx 156 after recovery (webhook_verified=1) correctly appears."""
        _store_tx(self.storage,
                  uid="06006e9d-ed00-47a6-8863-07d754744424",
                  tracking_id="ycpi_202607_14_acq",
                  shop_type="acquiring",
                  status="successful",
                  test=0,
                  webhook_verified=1,
                  amount_minor=100)
        result = self.storage.list_unmatched_bepaid_transactions()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["transaction_uid"],
                         "06006e9d-ed00-47a6-8863-07d754744424")

    def test_05_pending_not_returned(self):
        _store_tx(self.storage, uid="t05", tracking_id="trk5",
                  status="pending", webhook_verified=1)
        self.assertEqual(len(self.storage.list_unmatched_bepaid_transactions()), 0)

    def test_06_failed_not_returned(self):
        _store_tx(self.storage, uid="t06", tracking_id="trk6",
                  status="failed", webhook_verified=1)
        self.assertEqual(len(self.storage.list_unmatched_bepaid_transactions()), 0)

    def test_07_test_transaction_not_returned(self):
        _store_tx(self.storage, uid="t07", tracking_id="trk7",
                  status="successful", test=1, webhook_verified=1)
        self.assertEqual(len(self.storage.list_unmatched_bepaid_transactions()), 0)

    def test_08_already_matched_not_returned(self):
        _store_tx(self.storage, uid="t08", tracking_id="trk8",
                  status="successful", test=0, webhook_verified=1,
                  intent_public_id="ycpi_202607_14")
        self.assertEqual(len(self.storage.list_unmatched_bepaid_transactions()), 0)

    def test_09_refunded_not_returned(self):
        _store_tx(self.storage, uid="t09", tracking_id="trk9",
                  status="refund", webhook_verified=1)
        self.assertEqual(len(self.storage.list_unmatched_bepaid_transactions()), 0)


# ═══════════════════════════════════════════════════════════════════════════
# 2. mark_bepaid_transaction_signature_verified
# ═══════════════════════════════════════════════════════════════════════════

class Test02MarkSignatureVerified(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()

    def test_10_mark_verified_sets_webhook_verified_1(self):
        from utils import now_iso
        tx = _store_tx(self.storage, uid="sv01", tracking_id="trk_sv",
                       webhook_verified=0)
        self.assertEqual(tx.get("webhook_verified"), 0)
        self.storage.mark_bepaid_transaction_signature_verified(
            tx["id"],
            verified_at=now_iso(),
            verification_method="rsa_pkcs1v15_sha256",
        )
        updated = self.storage.get_bepaid_transaction_by_id(tx["id"])
        self.assertEqual(updated["webhook_verified"], 1)

    def test_11_mark_verified_does_not_touch_intent_fields(self):
        """mark_bepaid_transaction_signature_verified must not write intent/option fields."""
        from utils import now_iso
        tx = _store_tx(self.storage, uid="sv02", tracking_id="trk_sv2",
                       webhook_verified=0)
        self.storage.mark_bepaid_transaction_signature_verified(
            tx["id"],
            verified_at=now_iso(),
        )
        updated = self.storage.get_bepaid_transaction_by_id(tx["id"])
        self.assertIsNone(updated.get("intent_public_id"))
        self.assertIsNone(updated.get("payment_intent_id"))


# ═══════════════════════════════════════════════════════════════════════════
# 3. API endpoint bepaid_list_unmatched_transactions
# ═══════════════════════════════════════════════════════════════════════════

class Test03ApiUnmatchedEndpoint(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def test_12_api_returns_items_key(self):
        _store_tx(self.storage, uid="api01", tracking_id="trk_api",
                  status="successful", test=0, webhook_verified=1)
        result = self.ctx.bepaid_list_unmatched_transactions(_auth())
        self.assertTrue(result.get("ok"))
        self.assertIn("items", result)
        self.assertNotIn("transactions", result)

    def test_13_api_returns_count(self):
        _store_tx(self.storage, uid="api02", tracking_id="trk_c",
                  status="successful", test=0, webhook_verified=1)
        result = self.ctx.bepaid_list_unmatched_transactions(_auth())
        self.assertEqual(result.get("count"), 1)

    def test_14_api_empty_when_only_unverified_tx(self):
        """API must return empty list when only unverified tx exists."""
        _store_tx(self.storage, uid="api14", tracking_id="trk_unver",
                  status="successful", test=0, webhook_verified=0)
        result = self.ctx.bepaid_list_unmatched_transactions(_auth())
        self.assertEqual(result.get("count"), 0)
        self.assertEqual(result.get("items"), [])

    def test_15_api_includes_transaction_156_shape_verified(self):
        """tx 156 with webhook_verified=1 (after recovery) appears in API response."""
        _store_tx(self.storage,
                  uid="06006e9d-ed00-47a6-8863-07d754744424",
                  tracking_id="ycpi_202607_14_acq",
                  shop_type="acquiring",
                  status="successful",
                  test=0,
                  webhook_verified=1,
                  amount_minor=100)
        result = self.ctx.bepaid_list_unmatched_transactions(_auth())
        items = result.get("items", [])
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["tracking_id"], "ycpi_202607_14_acq")
        self.assertEqual(item["channel"], "acquiring")
        self.assertEqual(item["amount_minor"], 100)
        self.assertTrue(item["signature_verified"])

    def test_16_api_no_raw_fields_exposed(self):
        _store_tx(self.storage, uid="api16", tracking_id="trk_safe",
                  status="successful", test=0, webhook_verified=1)
        result = self.ctx.bepaid_list_unmatched_transactions(_auth())
        item = result["items"][0]
        for forbidden in ("raw_json", "customer_first_name", "customer_last_name",
                          "customer_phone", "customer_email", "billing_phone"):
            self.assertNotIn(forbidden, item, f"'{forbidden}' must not be in response")

    def test_17_api_denied_for_non_admin(self):
        ctx = _make_ctx(self.storage)
        ctx._role_store[2] = "client"
        result = ctx.bepaid_list_unmatched_transactions({"ok": True, "user_id": 2})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "access_denied")


# ═══════════════════════════════════════════════════════════════════════════
# 4. reconcile endpoint — security checks
# ═══════════════════════════════════════════════════════════════════════════

class Test04ReconcileSecurity(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        self.pi, self.opt = _make_pi_with_acq_option(
            self.storage, public_id="ycpi_202607_14", status="awaiting_payment"
        )

    def _tx(self, *, webhook_verified: int, uid_suffix: str = "") -> dict:
        return _store_tx(
            self.storage,
            uid=f"06006e9d-ed00-47a6-8863-07d75474{uid_suffix or '4424'}",
            tracking_id="ycpi_202607_14_acq",
            shop_type="acquiring",
            status="successful",
            test=0,
            webhook_verified=webhook_verified,
            amount_minor=100,
        )

    def test_18_reconcile_blocked_for_webhook_verified_0(self):
        """Reconcile must hard-block when webhook_verified=0 — security gate."""
        tx = self._tx(webhook_verified=0, uid_suffix="0001")
        result = self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        self.assertFalse(result.get("ok"), "reconcile must be blocked for unverified tx")
        self.assertEqual(result.get("reason"), "webhook_not_verified")

    def test_19_reconcile_does_not_call_matcher_when_not_verified(self):
        """When webhook_verified=0, reconcile must return before running matcher."""
        tx = self._tx(webhook_verified=0, uid_suffix="0002")
        result = self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        self.assertFalse(result.get("ok"))
        # Intent must not be touched
        pi = self.storage.get_payment_intent("ycpi_202607_14")
        self.assertNotEqual(pi["status"], "paid")

    def test_20_reconcile_does_not_change_intent_when_not_verified(self):
        """Intent status must remain unchanged when reconcile is blocked."""
        tx = self._tx(webhook_verified=0, uid_suffix="0003")
        self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        pi = self.storage.get_payment_intent("ycpi_202607_14")
        self.assertEqual(pi["status"], "awaiting_payment")

    def test_21_reconcile_succeeds_with_webhook_verified_1(self):
        """Verified tx (webhook_verified=1) can be reconciled."""
        tx = self._tx(webhook_verified=1, uid_suffix="0004")
        result = self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        self.assertTrue(result.get("ok"), f"Expected ok=True, got: {result}")

    def test_22_reconcile_marks_parent_paid(self):
        tx = self._tx(webhook_verified=1, uid_suffix="0005")
        self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        pi_after = self.storage.get_payment_intent("ycpi_202607_14")
        self.assertEqual(pi_after["status"], "paid")
        self.assertEqual(pi_after["paid_channel"], "acquiring")

    def test_23_reconcile_supersedes_erip_sibling(self):
        self.storage.create_payment_intent_option(
            payment_intent_id=self.pi["id"],
            intent_public_id="ycpi_202607_14",
            channel="erip",
            shop_type="erip",
            bepaid_tracking_id="ycpi_202607_14",
            bepaid_account_number="887565826071",
        )
        tx = self._tx(webhook_verified=1, uid_suffix="0006")
        result = self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        self.assertTrue(result.get("ok"))
        erip_opt = self.storage.get_option_by_channel("ycpi_202607_14", "erip")
        self.assertEqual(erip_opt.get("status"), "superseded")

    def test_24_reconcile_is_idempotent(self):
        tx = self._tx(webhook_verified=1, uid_suffix="0007")
        first = self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        self.assertTrue(first.get("ok"))
        second = self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        self.assertTrue(second.get("ok"))
        self.assertTrue(second.get("idempotent"))

    def test_25_after_reconcile_tx_gone_from_unmatched_list(self):
        tx = self._tx(webhook_verified=1, uid_suffix="0008")
        before = self.ctx.bepaid_list_unmatched_transactions(_auth())
        self.assertEqual(before["count"], 1)
        self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        after = self.ctx.bepaid_list_unmatched_transactions(_auth())
        self.assertEqual(after["count"], 0)

    def test_26_reconcile_blocked_for_test_transaction(self):
        tx = _store_tx(self.storage, uid="test-rec-2", tracking_id="ycpi_test_acq",
                       status="successful", test=1, webhook_verified=1)
        result = self.ctx.bepaid_reconcile_stored_transaction(_auth(), str(tx["id"]), {})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "test_transaction_ignored")

    def test_27_no_special_exception_for_id_156(self):
        """There must be no bypass logic targeting specific transaction IDs."""
        import web_app_server as was
        import inspect
        src = inspect.getsource(was.MiniAppContext.bepaid_reconcile_stored_transaction)
        self.assertNotIn("== 156", src, "No ID-specific exception allowed")
        self.assertNotIn("id == 156", src, "No ID-specific exception allowed")


# ═══════════════════════════════════════════════════════════════════════════
# 5. Frontend JS — static checks
# ═══════════════════════════════════════════════════════════════════════════

class Test05FrontendJS(unittest.TestCase):

    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_28_version_is_current(self):
        self.assertIn("v7.0.93.2.9", self.js)

    def test_29_reads_data_items(self):
        import re
        fn_match = re.search(
            r"async function loadUnmatchedTransactions\(\)(.*?)^window\.reconcileTransaction",
            self.js, re.DOTALL | re.MULTILINE
        )
        self.assertIsNotNone(fn_match)
        fn_body = fn_match.group(1)
        self.assertIn("data.items", fn_body)
        self.assertNotIn("data.transactions", fn_body)

    def test_30_uses_Array_isArray(self):
        self.assertIn("Array.isArray(data.items)", self.js)

    def test_31_reconcile_shows_not_verified_message(self):
        self.assertIn("webhook_not_verified", self.js)
        self.assertIn("Подпись сохранённого webhook не подтверждена", self.js)


# ═══════════════════════════════════════════════════════════════════════════
# 6. Index.html cache-bust
# ═══════════════════════════════════════════════════════════════════════════

class Test06IndexHtml(unittest.TestCase):

    def setUp(self):
        self.html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")

    def test_32_cache_bust_is_current(self):
        self.assertIn("v=7.0.93.2.9", self.html)
        self.assertNotIn("v=7.0.92.5.1\"", self.html)


if __name__ == "__main__":
    unittest.main()
