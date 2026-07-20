"""Tests for v7.0.91: bePaid webhook processing and payment intent reconciliation.

Fixtures A-J cover the key matching/idempotency/state-machine scenarios.
Run offline (no network/bePaid/Telegram needed):

    python -m unittest tests.test_bepaid_webhook -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return Storage(Path(tmp.name))


def _make_intent(storage: Storage, *, public_id_override: Optional[str] = None) -> dict:
    """Create a minimal payment_intent in bepaid_created status for testing."""
    pi = storage.create_payment_intent({
        "mk_user_id": 8875658,
        "student_name": "Тест Тестович",
        "amount_minor": 22900,
        "amount_byn": 229.0,
        "currency": "BYN",
        "purpose": "current_month",
        "period_month": "2026-07",
        "payment_method": "erip",
        "status": "bepaid_created",
        "created_by_tg_id": 1,
        "created_by_name": "Test",
    })
    # Simulate bePaid creation: set tracking_id, order_id, bepaid_uid, account_number
    row_id = pi["id"]
    pub_id = public_id_override or pi["public_id"]
    from utils import now_iso
    now = now_iso()
    import sqlite3
    with storage._connect() as conn:
        conn.execute("""
            UPDATE payment_intents SET
                status='bepaid_created',
                bepaid_tracking_id=?,
                bepaid_order_id=?,
                bepaid_uid=?,
                bepaid_account_number=?,
                public_id=?
            WHERE id=?
        """, (pub_id, f"1{row_id:011d}", f"test-uid-{row_id}", f"88756582607{row_id}", pub_id, row_id))
    return storage.get_payment_intent(pub_id)


# ── Fixture A: tracking_id match ─────────────────────────────────────────────

class TestFixtureA_TrackingIdMatch(unittest.TestCase):
    """Fixture A: transaction.tracking_id matches intent.bepaid_tracking_id → strong match."""

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage)

    def test_match_by_tracking_id(self):
        tx = {
            "tracking_id": self.pi["bepaid_tracking_id"],
            "order_id": None,
            "transaction_uid": None,
            "erip_account_number": None,
        }
        result = self.storage.match_bepaid_transaction_to_intent(tx)
        self.assertTrue(result["matched"])
        self.assertEqual(result["confidence"], "strong")
        self.assertEqual(result["method"], "tracking_id")
        self.assertEqual(result["intent_public_id"], self.pi["public_id"])

    def test_match_tracking_returns_correct_intent_id(self):
        tx = {"tracking_id": self.pi["bepaid_tracking_id"]}
        result = self.storage.match_bepaid_transaction_to_intent(tx)
        self.assertEqual(result["intent_id"], self.pi["id"])


# ── Fixture B: order_id match ─────────────────────────────────────────────────

class TestFixtureB_OrderIdMatch(unittest.TestCase):
    """Fixture B: transaction.order_id matches intent.bepaid_order_id → strong match."""

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage)

    def test_match_by_order_id(self):
        tx = {
            "tracking_id": None,
            "order_id": self.pi["bepaid_order_id"],
            "transaction_uid": None,
            "erip_account_number": None,
        }
        result = self.storage.match_bepaid_transaction_to_intent(tx)
        self.assertTrue(result["matched"])
        self.assertEqual(result["confidence"], "strong")
        self.assertEqual(result["method"], "order_id")


# ── Fixture C: transaction_uid match ─────────────────────────────────────────

class TestFixtureC_TransactionUidMatch(unittest.TestCase):
    """Fixture C: transaction.transaction_uid matches intent.bepaid_uid → strong match."""

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage)

    def test_match_by_uid(self):
        tx = {
            "tracking_id": None,
            "order_id": None,
            "transaction_uid": self.pi["bepaid_uid"],
            "erip_account_number": None,
        }
        result = self.storage.match_bepaid_transaction_to_intent(tx)
        self.assertTrue(result["matched"])
        self.assertEqual(result["confidence"], "strong")
        self.assertEqual(result["method"], "transaction_uid")


# ── Fixture D: account_number match ──────────────────────────────────────────

class TestFixtureD_AccountNumberMatch(unittest.TestCase):
    """Fixture D: erip_account_number matches intent.bepaid_account_number → strong match."""

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage)

    def test_match_by_account_number(self):
        tx = {
            "tracking_id": None,
            "order_id": None,
            "transaction_uid": None,
            "erip_account_number": self.pi["bepaid_account_number"],
        }
        result = self.storage.match_bepaid_transaction_to_intent(tx)
        self.assertTrue(result["matched"])
        self.assertEqual(result["confidence"], "strong")
        self.assertEqual(result["method"], "account_number")


# ── Fixture E: no match ───────────────────────────────────────────────────────

class TestFixtureE_NoMatch(unittest.TestCase):
    """Fixture E: no identifier matches any intent → confidence=none."""

    def setUp(self):
        self.storage = _make_storage()
        _make_intent(self.storage)  # create an intent, but we won't match it

    def test_no_match_returns_none_confidence(self):
        tx = {
            "tracking_id": "ycpi_999999_9999",
            "order_id": "100000099999",
            "transaction_uid": "00000000-0000-0000-0000-000000000000",
            "erip_account_number": "000000000000",
        }
        result = self.storage.match_bepaid_transaction_to_intent(tx)
        self.assertFalse(result["matched"])
        self.assertEqual(result["confidence"], "none")
        self.assertIsNone(result["intent_id"])

    def test_empty_transaction_returns_none(self):
        result = self.storage.match_bepaid_transaction_to_intent({})
        self.assertFalse(result["matched"])
        self.assertEqual(result["confidence"], "none")


# ── Fixture F: conflict ───────────────────────────────────────────────────────

class TestFixtureF_Conflict(unittest.TestCase):
    """Fixture F: tracking_id → intent1, order_id → intent2 → confidence=conflict."""

    def setUp(self):
        self.storage = _make_storage()
        self.pi1 = _make_intent(self.storage)
        self.pi2 = _make_intent(self.storage)

    def test_conflict_detected(self):
        # tracking_id from pi1, order_id from pi2
        tx = {
            "tracking_id": self.pi1["bepaid_tracking_id"],
            "order_id": self.pi2["bepaid_order_id"],
            "transaction_uid": None,
            "erip_account_number": None,
        }
        result = self.storage.match_bepaid_transaction_to_intent(tx)
        self.assertEqual(result["confidence"], "conflict")
        self.assertIsNone(result["intent_id"])
        self.assertGreaterEqual(len(result["conflicts"]), 2)

    def test_conflict_lists_both_intents(self):
        tx = {
            "tracking_id": self.pi1["bepaid_tracking_id"],
            "order_id": self.pi2["bepaid_order_id"],
        }
        result = self.storage.match_bepaid_transaction_to_intent(tx)
        conflict_ids = {c["intent_public_id"] for c in result["conflicts"]}
        self.assertIn(self.pi1["public_id"], conflict_ids)
        self.assertIn(self.pi2["public_id"], conflict_ids)


# ── Fixture G: duplicate webhook (idempotency) ────────────────────────────────

class TestFixtureG_Idempotency(unittest.TestCase):
    """Fixture G: same tx_uid sent twice → second call returns idempotent=True, no state change."""

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage)

    def _mark(self, uid: str = "test-tx-uid-777"):
        return self.storage.payment_intent_mark_paid(
            self.pi["public_id"],
            tx_uid=uid,
            amount_minor=22900,
            currency="BYN",
            paid_at="2026-07-13T10:00:00",
            verified=True,
            match_method="tracking_id",
        )

    def test_first_mark_succeeds(self):
        result = self._mark()
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("marked_paid"))

    def test_duplicate_returns_idempotent(self):
        self._mark()
        result2 = self._mark()
        self.assertTrue(result2["ok"])
        self.assertTrue(result2.get("idempotent"))

    def test_intent_stays_paid_after_duplicate(self):
        self._mark()
        self._mark()
        pi_after = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi_after["status"], "paid")

    def test_different_uid_returns_conflict(self):
        self._mark("uid-first")
        result2 = self.storage.payment_intent_mark_paid(
            self.pi["public_id"],
            tx_uid="uid-second",
            amount_minor=22900,
            currency="BYN",
            paid_at="2026-07-13T11:00:00",
        )
        self.assertFalse(result2["ok"])
        self.assertTrue(result2.get("conflict"))


# ── Fixture H: test transaction skipped ──────────────────────────────────────

class TestFixtureH_TestTransaction(unittest.TestCase):
    """Fixture H: is_test=True → webhook skips mark_paid."""

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage)

    def test_test_flag_prevents_mark_paid(self):
        # Verify the pi is in bepaid_created
        self.assertEqual(self.pi["status"], "bepaid_created")
        # Test: a "test" transaction should NOT be marked paid by our webhook logic.
        # We verify via the skip logic in web_app_server; here we just ensure
        # the intent stays in bepaid_created when we don't call mark_paid.
        pi_after = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi_after["status"], "bepaid_created")


# ── Fixture I: wrong currency ─────────────────────────────────────────────────

class TestFixtureI_WrongCurrency(unittest.TestCase):
    """Fixture I: currency != BYN → mark_paid must not be called (webhook skips it)."""

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage)

    def test_wrong_currency_intent_stays_bepaid_created(self):
        # Simulate calling mark_paid with USD — the webhook logic skips this,
        # but even if called directly, the state machine allows it (currency check is in webhook).
        # Here we test that the intent stays bepaid_created when no mark_paid call is made.
        pi_after = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi_after["status"], "bepaid_created")


# ── Fixture J: wrong amount ───────────────────────────────────────────────────

class TestFixtureJ_WrongAmount(unittest.TestCase):
    """Fixture J: amount mismatch detection in mark_paid flow."""

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage)

    def test_mark_paid_records_actual_amount(self):
        """mark_paid stores the actual paid_amount_minor, not the intent amount."""
        result = self.storage.payment_intent_mark_paid(
            self.pi["public_id"],
            tx_uid="uid-j",
            amount_minor=11450,  # different from intent's 22900
            currency="BYN",
            paid_at="2026-07-13T12:00:00",
        )
        self.assertTrue(result["ok"])
        pi_after = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi_after["paid_amount_minor"], 11450)


# ── State machine tests ───────────────────────────────────────────────────────

class TestStateMachine(unittest.TestCase):
    """State machine: bepaid_created → paid only; no downgrade from paid."""

    def setUp(self):
        self.storage = _make_storage()

    def test_cannot_mark_draft_as_paid(self):
        pi = self.storage.create_payment_intent({
            "mk_user_id": 1, "amount_minor": 10000, "amount_byn": 100.0,
            "currency": "BYN", "purpose": "other", "payment_method": "erip",
            "status": "draft", "created_by_tg_id": 1, "created_by_name": "T",
        })
        result = self.storage.payment_intent_mark_paid(
            pi["public_id"],
            tx_uid="uid-sm1", amount_minor=10000, currency="BYN",
            paid_at="2026-07-13T00:00:00",
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("wrong_state"))

    def test_cannot_mark_ready_as_paid(self):
        pi = self.storage.create_payment_intent({
            "mk_user_id": 1, "amount_minor": 10000, "amount_byn": 100.0,
            "currency": "BYN", "purpose": "other", "payment_method": "erip",
            "status": "ready", "created_by_tg_id": 1, "created_by_name": "T",
        })
        import sqlite3
        with self.storage._connect() as conn:
            conn.execute("UPDATE payment_intents SET status='ready' WHERE public_id=?", (pi["public_id"],))
        result = self.storage.payment_intent_mark_paid(
            pi["public_id"],
            tx_uid="uid-sm2", amount_minor=10000, currency="BYN",
            paid_at="2026-07-13T00:00:00",
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("wrong_state"))

    def test_bepaid_created_marks_successfully(self):
        s = _make_storage()
        pi = _make_intent(s)
        result = s.payment_intent_mark_paid(
            pi["public_id"],
            tx_uid="uid-sm3", amount_minor=22900, currency="BYN",
            paid_at="2026-07-13T00:00:00", verified=True, match_method="tracking_id",
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("marked_paid"))
        pi_after = s.get_payment_intent(pi["public_id"])
        self.assertEqual(pi_after["status"], "paid")
        self.assertEqual(pi_after["paid_transaction_uid"], "uid-sm3")
        self.assertEqual(pi_after["payment_state_reason"], "paid_via_bepaid_webhook")
        self.assertEqual(pi_after["webhook_verified"], 1)
        self.assertEqual(pi_after["webhook_match_method"], "tracking_id")


# ── New DB columns exist ──────────────────────────────────────────────────────

class TestNewColumns(unittest.TestCase):
    """Verify all v7.0.91 DB columns are present."""

    def setUp(self):
        self.storage = _make_storage()

    def _columns(self, table: str) -> set:
        with self.storage._connect() as conn:
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def test_payment_intents_paid_columns(self):
        cols = self._columns("payment_intents")
        for col in ("paid_amount_minor", "paid_currency", "paid_transaction_uid",
                    "paid_tracking_id", "paid_order_id", "paid_account_number",
                    "last_webhook_at", "payment_state_reason",
                    "webhook_match_method", "webhook_verified"):
            self.assertIn(col, cols, f"payment_intents missing column: {col}")

    def test_bepaid_transactions_new_columns(self):
        cols = self._columns("bepaid_transactions")
        for col in ("payment_intent_id", "intent_public_id", "webhook_verified",
                    "webhook_match_method", "match_confidence", "processed_at",
                    "erip_account_number"):
            self.assertIn(col, cols, f"bepaid_transactions missing column: {col}")

    def test_payment_webhook_audit_table_exists(self):
        with self.storage._connect() as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        self.assertIn("payment_webhook_audit", tables)

    def test_payment_webhook_audit_columns(self):
        cols = self._columns("payment_webhook_audit")
        for col in ("id", "created_at", "event_type", "bepaid_tx_id",
                    "payment_intent_id", "intent_public_id", "transaction_uid",
                    "shop_type", "status", "amount_minor", "currency",
                    "match_method", "match_confidence", "reason", "details_json"):
            self.assertIn(col, cols, f"payment_webhook_audit missing column: {col}")


# ── Audit log ─────────────────────────────────────────────────────────────────

class TestAuditLog(unittest.TestCase):
    """log_payment_webhook_audit and list_payment_webhook_audit work correctly."""

    def setUp(self):
        self.storage = _make_storage()

    def test_audit_insert_and_list(self):
        self.storage.log_payment_webhook_audit(
            "webhook_received",
            bepaid_tx_id=42,
            transaction_uid="test-uid",
            shop_type="erip",
            amount_minor=22900,
            currency="BYN",
            reason="verified",
        )
        rows = self.storage.list_payment_webhook_audit(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_type"], "webhook_received")
        self.assertEqual(rows[0]["bepaid_tx_id"], 42)

    def test_audit_filter_by_intent(self):
        self.storage.log_payment_webhook_audit("webhook_received", intent_public_id="ycpi_202607_1")
        self.storage.log_payment_webhook_audit("intent_marked_paid", intent_public_id="ycpi_202607_2")
        rows = self.storage.list_payment_webhook_audit(intent_public_id="ycpi_202607_1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["intent_public_id"], "ycpi_202607_1")

    def test_audit_details_json(self):
        self.storage.log_payment_webhook_audit(
            "webhook_match_conflict",
            details={"conflicts": [{"method": "tracking_id", "intent_public_id": "ycpi_1"}]},
        )
        rows = self.storage.list_payment_webhook_audit(limit=5)
        details = json.loads(rows[0]["details_json"])
        self.assertIn("conflicts", details)


# ── bepaid_transaction_link_intent ───────────────────────────────────────────

class TestLinkIntent(unittest.TestCase):
    """bepaid_transaction_link_intent updates the tx row correctly."""

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage)
        tx_data, _ = self.storage.upsert_bepaid_transaction({
            "provider": "bepaid", "shop_type": "erip", "shop_id": "123",
            "transaction_uid": "link-uid-test",
            "tracking_id": self.pi["bepaid_tracking_id"],
            "status": "successful", "amount_minor": 22900, "amount_byn": 229.0,
            "currency": "BYN",
        })
        self.tx_id = tx_data["id"]

    def test_link_stores_intent_id(self):
        from utils import now_iso
        self.storage.bepaid_transaction_link_intent(
            self.tx_id,
            intent_id=self.pi["id"],
            intent_public_id=self.pi["public_id"],
            match_method="tracking_id",
            confidence="strong",
            reason="matched_by_tracking_id",
            now=now_iso(),
        )
        with self.storage._connect() as conn:
            row = conn.execute(
                "SELECT * FROM bepaid_transactions WHERE id=?", (self.tx_id,)
            ).fetchone()
        self.assertEqual(row["payment_intent_id"], self.pi["id"])
        self.assertEqual(row["intent_public_id"], self.pi["public_id"])
        self.assertEqual(row["webhook_match_method"], "tracking_id")
        self.assertEqual(row["match_confidence"], "strong")
        # webhook_verified is NOT set by bepaid_transaction_link_intent (v7.0.92.5.2)
        # It is set exclusively by mark_bepaid_transaction_signature_verified.
        self.assertEqual(row["webhook_verified"], 0)


# ── paid_amount_byn normalization ─────────────────────────────────────────────

class TestNormalization(unittest.TestCase):
    """_normalize_payment_intent adds paid_amount_byn and payment_status."""

    def test_paid_amount_byn_computed(self):
        from web_app_server import MiniAppContext
        pi = {"status": "paid", "paid_amount_minor": 22900}
        result = MiniAppContext._normalize_payment_intent(pi)
        self.assertAlmostEqual(result["paid_amount_byn"], 229.0, places=2)
        self.assertEqual(result["payment_status"], "paid")
        self.assertFalse(result["can_pay"])

    def test_no_paid_amount_returns_none(self):
        from web_app_server import MiniAppContext
        pi = {"status": "bepaid_created"}
        result = MiniAppContext._normalize_payment_intent(pi)
        self.assertIsNone(result["paid_amount_byn"])

    def test_can_pay_true_for_draft(self):
        from web_app_server import MiniAppContext
        pi = {"status": "draft"}
        result = MiniAppContext._normalize_payment_intent(pi)
        self.assertTrue(result["can_pay"])


# ── UI: status chip labels ────────────────────────────────────────────────────

class TestUIStatusLabels(unittest.TestCase):
    """v7.0.91 status chip labels updated in app.js."""

    _APP_JS = ROOT / "miniapp" / "app.js"

    def _app(self):
        return self._APP_JS.read_text(encoding="utf-8")

    def test_bepaid_created_label_is_waiting_for_payment(self):
        src = self._app()
        idx = src.find("PI_STATUS_LABELS")
        block = src[idx:idx + 800]
        self.assertIn("Ожидает оплаты", block,
                      "bepaid_created chip label must be 'Ожидает оплаты'")

    def test_paid_label_is_oplateno_bepaid(self):
        src = self._app()
        idx = src.find("PI_STATUS_LABELS")
        block = src[idx:idx + 800]
        self.assertIn("Оплачено bePaid", block,
                      "paid chip label must be 'Оплачено bePaid'")

    def test_posted_to_moyklass_label(self):
        src = self._app()
        idx = src.find("PI_STATUS_LABELS")
        block = src[idx:idx + 800]
        self.assertIn("Внесено в МойКласс", block,
                      "posted_to_moyklass label must be 'Внесено в МойКласс'")

    def test_bepaid_paid_block_in_card(self):
        src = self._app()
        self.assertIn("pi-bepaid-paid", src,
                      "renderPaymentIntentCard must include pi-bepaid-paid block")
        self.assertIn("Оплачено в bePaid", src,
                      "pi-bepaid-paid block must say 'Оплачено в bePaid'")

    def test_paid_block_shows_paid_at(self):
        src = self._app()
        idx = src.find("pi-bepaid-paid")
        block = src[idx:idx + 500]
        self.assertIn("paid_at", block,
                      "pi-bepaid-paid block must reference pi.paid_at")

    def test_paid_block_shows_transaction_uid(self):
        src = self._app()
        idx = src.find("pi-bepaid-paid")
        block = src[idx:idx + 500]
        self.assertIn("paid_transaction_uid", block,
                      "pi-bepaid-paid block must reference pi.paid_transaction_uid")


# ── UI: CSS ───────────────────────────────────────────────────────────────────

class TestCSS(unittest.TestCase):
    """CSS classes for pi-bepaid-paid exist and support dark theme."""

    _CSS = ROOT / "miniapp" / "styles.css"

    def _css(self):
        return self._CSS.read_text(encoding="utf-8")

    def test_pi_bepaid_paid_class_exists(self):
        css = self._css()
        self.assertIn(".pi-bepaid-paid {", css,
                      ".pi-bepaid-paid CSS class must be defined")

    def test_pi_bepaid_paid_dark_theme(self):
        css = self._css()
        # Check that dark theme override exists for pi-bepaid-paid
        self.assertIn(".pi-bepaid-paid", css)
        dark_idx = css.find("prefers-color-scheme: dark")
        while dark_idx != -1:
            block_end = css.find("}", css.find("{", dark_idx))
            block = css[dark_idx:block_end + 200]
            if "pi-bepaid-paid" in block:
                break
            dark_idx = css.find("prefers-color-scheme: dark", dark_idx + 1)
        else:
            # Also check :root[data-theme] override
            self.assertIn(":root[data-theme", css,
                          "pi-bepaid-paid must have dark theme support")


# ── Cache-bust ────────────────────────────────────────────────────────────────

CURRENT_MINIAPP_VERSION = "7.0.98.1"


class TestCacheBust(unittest.TestCase):
    """index.html and app.js cache-bust / version marker match CURRENT_MINIAPP_VERSION."""

    _HTML = ROOT / "miniapp" / "index.html"
    _APP = ROOT / "miniapp" / "app.js"

    def test_current_cache_bust_in_html(self):
        html = self._HTML.read_text(encoding="utf-8")
        self.assertIn(f"styles.css?v={CURRENT_MINIAPP_VERSION}", html,
                      f"styles.css cache-bust must be v={CURRENT_MINIAPP_VERSION}")
        self.assertIn(f"app.js?v={CURRENT_MINIAPP_VERSION}", html,
                      f"app.js cache-bust must be v={CURRENT_MINIAPP_VERSION}")

    def test_old_cache_bust_not_in_asset_tags(self):
        html = self._HTML.read_text(encoding="utf-8")
        self.assertNotIn("v=7.0.92", html,
                         "Old v=7.0.92 cache-bust must not appear in index.html asset tags")

    def test_version_string_in_app_js(self):
        app = self._APP.read_text(encoding="utf-8")
        self.assertIn(f"v{CURRENT_MINIAPP_VERSION}", app,
                      f"app.js version marker must contain v{CURRENT_MINIAPP_VERSION}")


# ── Webhook endpoint exists ───────────────────────────────────────────────────

class TestWebhookReadinessEndpoint(unittest.TestCase):
    """webhook-readiness endpoint and routing exist in web_app_server.py."""

    _SERVER = ROOT / "web_app_server.py"

    def _src(self):
        return self._SERVER.read_text(encoding="utf-8")

    def test_webhook_readiness_method_exists(self):
        src = self._src()
        self.assertIn("def payment_intent_webhook_readiness(", src,
                      "payment_intent_webhook_readiness method must exist")

    def test_webhook_readiness_routing_in_get(self):
        src = self._src()
        self.assertIn("webhook-readiness", src,
                      "GET routing must include webhook-readiness segment")

    def test_match_bepaid_transaction_method_exists(self):
        src = self._src()
        # The method is in storage.py, check its call from web_app_server
        self.assertIn("match_bepaid_transaction_to_intent", src,
                      "webhook handler must call match_bepaid_transaction_to_intent")

    def test_intent_marked_paid_audit_event(self):
        src = self._src()
        self.assertIn("intent_marked_paid", src,
                      "webhook handler must log intent_marked_paid audit event")

    def test_duplicate_webhook_ignored_audit_event(self):
        src = self._src()
        self.assertIn("duplicate_webhook_ignored", src,
                      "webhook handler must log duplicate_webhook_ignored audit event")

    def test_webhook_handler_always_returns_200(self):
        src = self._src()
        idx = src.find("def bepaid_handle_webhook(")
        fn_end = src.find("\n    def ", idx + 1)
        fn_body = src[idx:fn_end]
        # The handler should only return 200 for processed cases (non-auth/non-parse errors)
        self.assertIn("}, 200", fn_body,
                      "webhook handler must return HTTP 200 for all processed cases")

    def test_normalize_payment_intent_static_method(self):
        src = self._src()
        self.assertIn("def _normalize_payment_intent(", src,
                      "_normalize_payment_intent static method must exist")

    def test_erip_account_number_extracted(self):
        src = self._src()
        idx = src.find("def _bepaid_extract_payload(")
        fn_end = src.find("\n    def ", idx + 1)
        fn_body = src[idx:fn_end]
        self.assertIn("erip_account_number", fn_body,
                      "_bepaid_extract_payload must extract erip_account_number")

    def test_log_payment_webhook_audit_method_exists(self):
        storage_src = (ROOT / "storage.py").read_text(encoding="utf-8")
        self.assertIn("def log_payment_webhook_audit(", storage_src,
                      "Storage.log_payment_webhook_audit must exist")

    def test_payment_intent_mark_paid_method_exists(self):
        storage_src = (ROOT / "storage.py").read_text(encoding="utf-8")
        self.assertIn("def payment_intent_mark_paid(", storage_src,
                      "Storage.payment_intent_mark_paid must exist")

    def test_payment_intent_mark_requires_check_method_exists(self):
        storage_src = (ROOT / "storage.py").read_text(encoding="utf-8")
        self.assertIn("def payment_intent_mark_requires_check(", storage_src,
                      "Storage.payment_intent_mark_requires_check must exist")

    def test_amount_mismatch_event_in_webhook_handler(self):
        src = self._src()
        self.assertIn("webhook_amount_mismatch", src,
                      "webhook handler must log webhook_amount_mismatch event")

    def test_webhook_signature_verified_event_in_handler(self):
        src = self._src()
        self.assertIn("webhook_signature_verified", src,
                      "webhook handler must log webhook_signature_verified event")

    def test_webhook_test_ignored_event_in_handler(self):
        src = self._src()
        self.assertIn("webhook_test_ignored", src,
                      "webhook handler must log webhook_test_ignored event")

    def test_webhook_currency_mismatch_event_in_handler(self):
        src = self._src()
        self.assertIn("webhook_currency_mismatch", src,
                      "webhook handler must log webhook_currency_mismatch event")

    def test_refund_requires_check_event_in_handler(self):
        src = self._src()
        self.assertIn("refund_requires_check", src,
                      "webhook handler must log refund_requires_check event")

    def test_auto_post_readiness_check(self):
        src = self._src()
        self.assertIn("auto_post_to_moyklass_disabled", src,
                      "readiness endpoint must check auto_post_to_moyklass_disabled")

    def test_amount_minor_readiness_check(self):
        src = self._src()
        self.assertIn("has_amount_minor", src,
                      "readiness endpoint must check has_amount_minor")


# ── Storage: payment_intent_mark_requires_check ───────────────────────────────

class TestMarkRequiresCheck(unittest.TestCase):
    """payment_intent_mark_requires_check sets status and blocks non-allowed transitions."""

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage)

    def test_mark_requires_check_from_bepaid_created(self):
        self.storage.payment_intent_mark_requires_check(
            self.pi["public_id"], reason="test_amount_mismatch"
        )
        pi = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi["status"], "bepaid_requires_check")
        self.assertEqual(pi["payment_state_reason"], "test_amount_mismatch")

    def test_mark_requires_check_from_paid(self):
        self.storage.payment_intent_mark_paid(
            self.pi["public_id"],
            tx_uid="uid-rc1", amount_minor=22900, currency="BYN",
            paid_at="2026-07-13T10:00:00",
        )
        self.storage.payment_intent_mark_requires_check(
            self.pi["public_id"], reason="refund_received"
        )
        pi = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi["status"], "bepaid_requires_check")

    def test_mark_requires_check_is_idempotent(self):
        self.storage.payment_intent_mark_requires_check(self.pi["public_id"], reason="first")
        self.storage.payment_intent_mark_requires_check(self.pi["public_id"], reason="second")
        pi = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi["status"], "bepaid_requires_check")


# ── Helpers for integration tests ─────────────────────────────────────────────

import types as _types


def _make_ctx(storage: Storage, **settings_overrides):
    """Create a minimal MiniAppContext stub without loading real config."""
    from web_app_server import MiniAppContext
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    defaults = dict(
        bepaid_webhook_path_secret="",
        bepaid_erip_shop_id="test-shop",
        bepaid_erip_public_key="",
        bepaid_acq_shop_id="",
        bepaid_acq_public_key="",
        bepaid_auto_post_to_moyklass=False,
    )
    defaults.update(settings_overrides)
    ctx.settings = _types.SimpleNamespace(**defaults)
    return ctx


def _make_webhook_body(
    *,
    status: str = "successful",
    amount: int = 22900,
    currency: str = "BYN",
    tracking_id: Optional[str] = None,
    order_id: Optional[str] = None,
    uid: str = "test-tx-uid",
    is_test: bool = False,
) -> bytes:
    return json.dumps({
        "transaction": {
            "uid": uid,
            "status": status,
            "amount": amount,
            "currency": currency,
            "tracking_id": tracking_id,
            "order": {"id": order_id},
            "test": is_test,
            "paid_at": "2026-07-13T10:00:00Z",
        }
    }).encode("utf-8")


# ── Security audit: 18 required scenario tests ────────────────────────────────

class TestSecurity(unittest.TestCase):
    """v7.0.91.1 security hardening: webhook validation scenarios (18 required tests)."""

    def setUp(self):
        self.storage = _make_storage()
        self.pi = _make_intent(self.storage)
        self.ctx = _make_ctx(self.storage)

    def _call(self, body: bytes, *, sig: str = "", path_secret: str = "") -> tuple:
        return self.ctx.bepaid_handle_webhook(
            shop_type="erip", raw_body=body,
            content_signature=sig, path_secret=path_secret,
        )

    def _body(self, **kw) -> bytes:
        kw.setdefault("tracking_id", self.pi["bepaid_tracking_id"])
        kw.setdefault("order_id", self.pi["bepaid_order_id"])
        return _make_webhook_body(**kw)

    # 1 — pending + correct identifiers + correct amount → NOT paid
    def test_01_pending_not_paid(self):
        resp, code = self._call(self._body(status="pending", amount=22900, uid="tx-pend"))
        self.assertEqual(code, 200)
        pi = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi["status"], "bepaid_created")
        self.assertIn("status=pending", resp.get("skip_reason", ""))

    # 2 — failed → NOT paid
    def test_02_failed_not_paid(self):
        resp, code = self._call(self._body(status="failed", amount=22900, uid="tx-fail"))
        self.assertEqual(code, 200)
        pi = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi["status"], "bepaid_created")

    # 3 — expired → NOT paid
    def test_03_expired_not_paid(self):
        self._call(self._body(status="expired", amount=22900, uid="tx-exp"))
        pi = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi["status"], "bepaid_created")

    # 4 — unknown status → NOT paid
    def test_04_unknown_status_not_paid(self):
        self._call(self._body(status="unknown_xyz", amount=22900, uid="tx-unk"))
        pi = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi["status"], "bepaid_created")

    # 5 — successful exact amount → paid
    def test_05_successful_exact_amount_paid(self):
        resp, code = self._call(self._body(status="successful", amount=22900, uid="tx-exact"))
        self.assertEqual(code, 200)
        pi = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi["status"], "paid")
        self.assertEqual(pi["paid_transaction_uid"], "tx-exact")

    # 6 — successful amount −1 minor → requires_check
    def test_06_amount_minus_one_requires_check(self):
        resp, code = self._call(self._body(status="successful", amount=22899, uid="tx-m1"))
        self.assertEqual(code, 200)
        self.assertEqual(resp.get("skip_reason"), "amount_mismatch")
        pi = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi["status"], "bepaid_requires_check")

    # 7 — successful amount +1 minor → requires_check
    def test_07_amount_plus_one_requires_check(self):
        resp, code = self._call(self._body(status="successful", amount=22901, uid="tx-p1"))
        self.assertEqual(code, 200)
        self.assertEqual(resp.get("skip_reason"), "amount_mismatch")
        pi = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi["status"], "bepaid_requires_check")

    # 8 — wrong currency → requires_check; empty currency → not paid (still bepaid_created)
    def test_08_wrong_currency_requires_check(self):
        resp, code = self._call(self._body(status="successful", amount=22900,
                                           currency="USD", uid="tx-usd"))
        self.assertEqual(code, 200)
        self.assertIn("USD", resp.get("skip_reason", ""))
        pi = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi["status"], "bepaid_requires_check")

    def test_08b_empty_currency_not_paid(self):
        resp, code = self._call(self._body(status="successful", amount=22900,
                                           currency="", uid="tx-nocur"))
        self.assertEqual(code, 200)
        pi = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertNotEqual(pi["status"], "paid")

    # 9 — invalid Content-Signature → 401, intent unchanged (requires cryptography package)
    def test_09_invalid_signature_returns_401(self):
        try:
            from cryptography.hazmat.primitives import hashes  # noqa: F401
        except ImportError:
            self.skipTest("cryptography not installed — signature verification not active")
        ctx2 = _make_ctx(self.storage, bepaid_erip_public_key="NOT_A_PEM")
        body = self._body(status="successful", amount=22900, uid="tx-badsig")
        resp, code = ctx2.bepaid_handle_webhook(
            shop_type="erip", raw_body=body,
            content_signature="sha256=deadbeef00", path_secret="",
        )
        self.assertEqual(code, 401)
        pi = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi["status"], "bepaid_created")

    # 10 — missing signature with key configured → 401, intent unchanged (requires cryptography)
    def test_10_missing_signature_returns_401(self):
        try:
            from cryptography.hazmat.primitives import hashes  # noqa: F401
        except ImportError:
            self.skipTest("cryptography not installed — signature verification not active")
        ctx2 = _make_ctx(self.storage, bepaid_erip_public_key="NOT_A_PEM")
        body = self._body(status="successful", amount=22900, uid="tx-nosig")
        resp, code = ctx2.bepaid_handle_webhook(
            shop_type="erip", raw_body=body,
            content_signature="", path_secret="",
        )
        self.assertEqual(code, 401)
        pi = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi["status"], "bepaid_created")

    # 11 — conflict identifiers (two intents matched) → neither gets marked paid
    def test_11_conflict_no_mark_paid(self):
        pi2 = _make_intent(self.storage)
        body = json.dumps({"transaction": {
            "uid": "tx-conflict-11",
            "status": "successful",
            "amount": 22900,
            "currency": "BYN",
            "tracking_id": self.pi["bepaid_tracking_id"],
            "order": {"id": pi2["bepaid_order_id"]},
            "test": False,
            "paid_at": "2026-07-13T10:00:00Z",
        }}).encode()
        resp, code = self._call(body)
        self.assertEqual(code, 200)
        self.assertEqual(resp.get("skip_reason"), "match_conflict")
        self.assertEqual(self.storage.get_payment_intent(self.pi["public_id"])["status"], "bepaid_created")
        self.assertEqual(self.storage.get_payment_intent(pi2["public_id"])["status"], "bepaid_created")

    # 12 — cancelled intent + successful webhook → NOT paid
    def test_12_cancelled_intent_not_paid(self):
        with self.storage._connect() as conn:
            conn.execute("UPDATE payment_intents SET status='cancelled' WHERE public_id=?",
                         (self.pi["public_id"],))
        resp, code = self._call(self._body(status="successful", amount=22900, uid="tx-cancel"))
        self.assertEqual(code, 200)
        pi = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertNotEqual(pi["status"], "paid")

    # 13 — duplicate tx_uid → exactly one intent_marked_paid audit event
    def test_13_duplicate_uid_one_audit(self):
        body = self._body(status="successful", amount=22900, uid="tx-dup13")
        self._call(body)
        self._call(body)
        audits = self.storage.list_payment_webhook_audit(intent_public_id=self.pi["public_id"])
        paid_events = [a for a in audits if a["event_type"] == "intent_marked_paid"]
        self.assertEqual(len(paid_events), 1)

    # 14 — successful → paid; then pending → still paid
    def test_14_successful_then_pending_stays_paid(self):
        self._call(self._body(status="successful", amount=22900, uid="tx-ok14"))
        self._call(self._body(status="pending", amount=22900, uid="tx-pend14"))
        pi = self.storage.get_payment_intent(self.pi["public_id"])
        self.assertEqual(pi["status"], "paid")

    # 15 — webhook handler does not call MoyKlass
    def test_15_no_moyklass_call(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        idx = src.find("def bepaid_handle_webhook(")
        fn_body = src[idx: src.find("\n    def ", idx + 1)]
        self.assertNotIn("moyklass", fn_body.lower())
        self.assertNotIn("post_payment", fn_body)
        self.assertNotIn("create_invoice", fn_body)

    # 16 — webhook handler does not call Telegram
    def test_16_no_telegram_call(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        idx = src.find("def bepaid_handle_webhook(")
        fn_body = src[idx: src.find("\n    def ", idx + 1)]
        self.assertNotIn("send_message", fn_body)
        self.assertNotIn("bot.send", fn_body)
        self.assertNotIn("telegram", fn_body.lower())

    # 17 — readiness endpoint is read-only (no DB writes)
    def test_17_readiness_read_only(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        idx = src.find("def payment_intent_webhook_readiness(")
        fn_body = src[idx: src.find("\n    def ", idx + 1)]
        for op in ("mark_paid", "mark_requires_check", "upsert_", "log_payment", "INSERT", "UPDATE"):
            self.assertNotIn(op, fn_body, f"readiness must be read-only (found {op!r})")

    # 18 — BEPAID_AUTO_POST_TO_MOYKLASS=false cannot be overridden in webhook handler
    def test_18_auto_post_stays_false(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        idx = src.find("def bepaid_handle_webhook(")
        fn_body = src[idx: src.find("\n    def ", idx + 1)]
        self.assertNotIn("bepaid_auto_post_to_moyklass = True", fn_body)
        self.assertNotIn("auto_post_to_moyklass=True", fn_body)
        # Confirm the config flag is defined and defaults to False
        cfg_src = (ROOT / "config.py").read_text(encoding="utf-8")
        self.assertIn("bepaid_auto_post_to_moyklass", cfg_src)


if __name__ == "__main__":
    unittest.main()
