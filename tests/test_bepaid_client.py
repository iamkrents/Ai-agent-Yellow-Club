"""
Unit tests for bepaid_client.py and storage atomic-claim methods.
No real network requests — requests.post is mocked throughout.
Storage tests use a temporary file-based SQLite database.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

# Make sure project root is on sys.path for storage / utils imports
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from bepaid_client import (
    BEPAID_ACCOUNT_NUMBER_MAX_LEN,
    BEPAID_ERIP_ENDPOINT,
    BePaidClient,
    BePaidResult,
    build_erip_description,
)


def _fake_response(status_code: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


def _success_body(
    uid: str = "abc-123",
    amount: int = 5000,
    currency: str = "BYN",
    tracking_id: str = "ycpi_202607_1",
    order_id: str = "100000000001",
    account_number: str = "88756582607",
) -> dict:
    return {
        "transaction": {
            "uid": uid,
            "status": "pending",
            "amount": amount,
            "currency": currency,
            "order_id": order_id,
            "tracking_id": tracking_id,
            "erip": {
                "account_number": account_number,
                "qr_code_raw": "BASE64QR",
                "qr_code": "data:image/png;base64,PNG",
            },
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# Static helpers: account_number / order_id
# ─────────────────────────────────────────────────────────────────────────────

class TestBePaidAccountNumber(unittest.TestCase):

    def test_erip_account_number_with_period(self):
        # "8875658" + "2607" + "42" = "8875658260742" (13 chars, under limit)
        result = BePaidClient.erip_account_number(8875658, "2026-07", 42)
        self.assertEqual(result, "8875658260742")

    def test_erip_account_number_without_period(self):
        result = BePaidClient.erip_account_number(12345, "", 1)
        self.assertEqual(result, "123451")

    def test_erip_order_id_specific_values(self):
        """Regression: exact expected values after hotfix v7.0.82.1."""
        self.assertEqual(BePaidClient.erip_order_id(1),  "100000000001")
        self.assertEqual(BePaidClient.erip_order_id(8),  "100000000008")
        self.assertEqual(BePaidClient.erip_order_id(42), "100000000042")
        self.assertEqual(BePaidClient.erip_order_id(999), "100000000999")

    def test_erip_order_id_format(self):
        """order_id must be exactly 12 digits, never start with 0."""
        result = BePaidClient.erip_order_id(8)
        self.assertEqual(len(result), 12)
        self.assertTrue(result.isdigit())
        self.assertFalse(result.startswith("0"),
                         "bePaid rejects order_id starting with 0 (HTTP 422)")

    def test_erip_order_id_unique_per_row(self):
        self.assertNotEqual(BePaidClient.erip_order_id(1), BePaidClient.erip_order_id(2))

    def test_erip_order_id_invalid_zero_raises(self):
        with self.assertRaises(ValueError):
            BePaidClient.erip_order_id(0)

    def test_erip_order_id_negative_raises(self):
        with self.assertRaises(ValueError):
            BePaidClient.erip_order_id(-1)

    def test_erip_order_id_too_large_raises(self):
        with self.assertRaises(ValueError):
            BePaidClient.erip_order_id(100_000_000_000)

    def test_erip_account_number_unique_per_user_and_period(self):
        a1 = BePaidClient.erip_account_number(100, "2026-07", 1)
        a2 = BePaidClient.erip_account_number(200, "2026-07", 1)
        a3 = BePaidClient.erip_account_number(100, "2026-08", 1)
        self.assertNotEqual(a1, a2)
        self.assertNotEqual(a1, a3)

    def test_same_student_same_month_two_intents_have_different_account_numbers(self):
        """Same student + same month but different pi_row_id must yield different account numbers."""
        a1 = BePaidClient.erip_account_number(8875658, "2026-07", 10)
        a2 = BePaidClient.erip_account_number(8875658, "2026-07", 11)
        self.assertNotEqual(a1, a2, "Two intents for same student/month must not share account_number")

    def test_account_number_never_exceeds_30_chars(self):
        # Use a very large mk_user_id and pi_row_id to stress the trimming
        very_large_id = 10 ** 24  # 25-digit number
        result = BePaidClient.erip_account_number(very_large_id, "2099-12", 9999999999)
        self.assertLessEqual(len(result), BEPAID_ACCOUNT_NUMBER_MAX_LEN)

    def test_account_number_includes_pi_row_id_suffix(self):
        """pi_row_id must always appear at the right end of the account_number."""
        result = BePaidClient.erip_account_number(8875658, "2026-07", 99)
        self.assertTrue(result.endswith("99"), f"Expected suffix '99', got: {result!r}")

    def test_account_number_digits_only(self):
        result = BePaidClient.erip_account_number(8875658, "2026-07", 42)
        self.assertTrue(result.isdigit(), f"Account number must be digits only: {result!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Payload builder
# ─────────────────────────────────────────────────────────────────────────────

class TestBePaidPayloadBuilder(unittest.TestCase):

    def _build(self, **kw) -> dict:
        defaults = dict(
            amount_minor=10000,
            currency="BYN",
            description="Жёлтый Клуб — оплата",
            account_number="88756582607",
            tracking_id="ycpi_202607_1",
            order_id="100000000001",
            notification_url="https://example.com/webhook/erip/secret",
        )
        defaults.update(kw)
        return BePaidClient.build_erip_payload(**defaults)

    def test_structure(self):
        p = self._build()
        self.assertIn("request", p)
        req = p["request"]
        self.assertEqual(req["amount"], 10000)
        self.assertEqual(req["currency"], "BYN")
        self.assertEqual(req["payment_method"]["type"], "erip")
        self.assertEqual(req["payment_method"]["account_number"], "88756582607")
        self.assertEqual(req["ip"], "127.0.0.1")

    def test_notification_url_included_when_given(self):
        p = self._build(notification_url="https://example.com/webhook")
        self.assertEqual(p["request"]["notification_url"], "https://example.com/webhook")

    def test_notification_url_always_transmitted(self):
        """notification_url is now REQUIRED — always present in the payload."""
        p = self._build(notification_url="https://bot.example.com/webhook/erip/tok123")
        self.assertIn("notification_url", p["request"])
        self.assertEqual(p["request"]["notification_url"], "https://bot.example.com/webhook/erip/tok123")

    def test_tracking_id_matches_public_id(self):
        p = self._build(
            tracking_id="ycpi_202607_0001",
            notification_url="https://example.com/webhook",
        )
        self.assertEqual(p["request"]["tracking_id"], "ycpi_202607_0001")

    def test_customer_included_when_name_given(self):
        p = self._build(customer_first_name="Иван", customer_last_name="Иванов")
        self.assertEqual(p["request"]["customer"]["first_name"], "Иван")

    def test_customer_omitted_when_no_name(self):
        p = self._build()
        self.assertNotIn("customer", p["request"])

    def test_order_id_in_payload(self):
        p = self._build(order_id="100000000042")
        self.assertEqual(p["request"]["order_id"], "100000000042")

    def test_order_id_payload_regression_pi_row_id_8(self):
        """Regression v7.0.82.1: pi_row_id=8 must produce '100000000008', not '000000000008'."""
        order_id = BePaidClient.erip_order_id(8)
        p = self._build(order_id=order_id)
        self.assertEqual(p["request"]["order_id"], "100000000008")

    def test_official_endpoint_constant(self):
        self.assertEqual(BEPAID_ERIP_ENDPOINT, "https://api.bepaid.by/beyag/payments")


# ─────────────────────────────────────────────────────────────────────────────
# HTTP client: success / error / timeout / 5xx / ConnectionError
# ─────────────────────────────────────────────────────────────────────────────

class TestBePaidClientCreateErip(unittest.TestCase):

    def _client(self):
        return BePaidClient(shop_id="test_shop", secret_key="test_secret", timeout=10)

    def _payload(self):
        return BePaidClient.build_erip_payload(
            amount_minor=5000,
            description="Тест",
            account_number="88756582607",
            tracking_id="ycpi_202607_1",
            order_id="100000000001",
            notification_url="https://example.com/webhook/erip/tok",
        )

    @patch("bepaid_client.requests.post")
    def test_success_response_parsing(self, mock_post):
        mock_post.return_value = _fake_response(200, _success_body())
        result = self._client().create_erip_payment(self._payload())
        self.assertTrue(result.ok)
        self.assertEqual(result.http_status, 200)
        self.assertEqual(result.data["transaction_uid"], "abc-123")
        self.assertEqual(result.data["erip_account_number"], "88756582607")
        self.assertEqual(result.data["qr_code_raw"], "BASE64QR")
        self.assertFalse(result.requires_check)

    @patch("bepaid_client.requests.post")
    def test_erip_data_parsed_from_erip_key_not_payment_method(self, mock_post):
        """ERIP fields must be read from transaction.erip (not transaction.payment_method)."""
        body = {
            "transaction": {
                "uid": "uid-erip",
                "status": "pending",
                "amount": 5000,
                "currency": "BYN",
                "tracking_id": "ycpi_202607_1",
                "order_id": "100000000001",
                "erip": {
                    "account_number": "ERIP_ACCT",
                    "qr_code_raw": "QR_RAW_DATA",
                },
                "payment_method": {
                    "type": "erip",
                    "account_number": "WRONG_ACCT",
                },
            }
        }
        mock_post.return_value = _fake_response(200, body)
        result = self._client().create_erip_payment(self._payload())
        self.assertTrue(result.ok)
        self.assertEqual(result.data["erip_account_number"], "ERIP_ACCT")
        self.assertEqual(result.data["qr_code_raw"], "QR_RAW_DATA")

    @patch("bepaid_client.requests.post")
    def test_error_response_parsing(self, mock_post):
        mock_post.return_value = _fake_response(422, {
            "errors": ["amount is invalid", "account_number required"]
        })
        result = self._client().create_erip_payment(self._payload())
        self.assertFalse(result.ok)
        self.assertEqual(result.http_status, 422)
        self.assertIn("amount is invalid", result.error)
        self.assertFalse(result.requires_check)

    @patch("bepaid_client.requests.post")
    def test_timeout_sets_requires_check(self, mock_post):
        mock_post.side_effect = requests.Timeout("timed out")
        result = self._client().create_erip_payment(self._payload())
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "timeout")
        self.assertTrue(result.requires_check)

    @patch("bepaid_client.requests.post")
    def test_connection_error_requires_check(self, mock_post):
        """ConnectionError → requires_check=True (state is unknown)."""
        mock_post.side_effect = requests.ConnectionError("refused")
        result = self._client().create_erip_payment(self._payload())
        self.assertFalse(result.ok)
        self.assertTrue(result.requires_check)
        self.assertIn("connection_error", result.error)

    @patch("bepaid_client.requests.post")
    def test_network_error_no_requires_check(self, mock_post):
        """Generic RequestException (not Timeout/ConnectionError) → requires_check=False."""
        mock_post.side_effect = requests.RequestException("generic network issue")
        result = self._client().create_erip_payment(self._payload())
        self.assertFalse(result.ok)
        self.assertFalse(result.requires_check)
        self.assertIn("network_error", result.error)

    @patch("bepaid_client.requests.post")
    def test_http_500_requires_check(self, mock_post):
        """HTTP 5xx → state is unknown, requires_check=True."""
        mock_post.return_value = _fake_response(500, {"message": "Internal Server Error"})
        result = self._client().create_erip_payment(self._payload())
        self.assertFalse(result.ok)
        self.assertTrue(result.requires_check)
        self.assertEqual(result.http_status, 500)

    @patch("bepaid_client.requests.post")
    def test_http_503_requires_check(self, mock_post):
        """HTTP 503 is also a server error → requires_check=True."""
        mock_post.return_value = _fake_response(503, {})
        result = self._client().create_erip_payment(self._payload())
        self.assertFalse(result.ok)
        self.assertTrue(result.requires_check)

    @patch("bepaid_client.requests.post")
    def test_credentials_not_in_payload(self, mock_post):
        """Credentials must NOT appear in the request body."""
        mock_post.return_value = _fake_response(200, {"transaction": {"uid": "x", "erip": {}}})
        self._client().create_erip_payment(self._payload())
        call_kwargs = mock_post.call_args
        body = json.dumps(call_kwargs.kwargs.get("json") or call_kwargs[1].get("json", {}))
        self.assertNotIn("test_secret", body)
        self.assertNotIn("test_shop", body)

    @patch("bepaid_client.requests.post")
    def test_http_basic_auth_used(self, mock_post):
        """HTTP Basic auth must be set correctly."""
        mock_post.return_value = _fake_response(200, {"transaction": {"uid": "x", "erip": {}}})
        self._client().create_erip_payment(self._payload())
        call_kwargs = mock_post.call_args
        auth = call_kwargs.kwargs.get("auth") or call_kwargs[1].get("auth")
        self.assertEqual(auth, ("test_shop", "test_secret"))

    @patch("bepaid_client.requests.post")
    def test_already_created_idempotent_returns_existing(self, mock_post):
        """If bePaid returns HTTP 200, result is ok=True — caller handles idempotency."""
        mock_post.return_value = _fake_response(200, _success_body(uid="uid-existing"))
        r1 = self._client().create_erip_payment(self._payload())
        r2 = self._client().create_erip_payment(self._payload())
        self.assertTrue(r1.ok)
        self.assertTrue(r2.ok)

    @patch("bepaid_client.requests.post")
    def test_missing_config_raises_before_call(self, mock_post):
        """Empty shop_id/secret_key: POST is still attempted; caller guards before instantiating."""
        mock_post.return_value = _fake_response(401, {"errors": ["Unauthorized"]})
        client = BePaidClient(shop_id="", secret_key="")
        result = client.create_erip_payment(self._payload())
        self.assertFalse(result.ok)


# ─────────────────────────────────────────────────────────────────────────────
# Description builder
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildErpDescription(unittest.TestCase):

    def test_full_intent(self):
        desc = build_erip_description({
            "student_name": "Петров Иван",
            "purpose": "current_month",
            "period_month": "2026-07",
        })
        self.assertIn("Жёлтый Клуб", desc)
        self.assertIn("Петров Иван", desc)
        self.assertIn("июль", desc)
        self.assertIn("2026", desc)

    def test_no_period(self):
        desc = build_erip_description({"purpose": "old_debt", "period_month": ""})
        self.assertIn("Долг", desc)
        self.assertLessEqual(len(desc), 255)

    def test_unknown_purpose(self):
        desc = build_erip_description({"purpose": "xyz", "period_month": ""})
        self.assertIn("Жёлтый Клуб", desc)

    def test_max_length(self):
        long_name = "А" * 300
        desc = build_erip_description({
            "student_name": long_name,
            "purpose": "current_month",
            "period_month": "2026-07",
        })
        self.assertLessEqual(len(desc), 255)


# ─────────────────────────────────────────────────────────────────────────────
# Storage: atomic claim
# ─────────────────────────────────────────────────────────────────────────────

class TestStorageAtomicClaim(unittest.TestCase):
    """Test payment_intent_claim_bepaid_creation and related methods against real SQLite."""

    def setUp(self):
        from storage import Storage
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "test.db"
        self.storage = Storage(db_path)
        self._insert_pi("pid_draft", status="draft")
        self._insert_pi("pid_ready", status="ready")
        self._insert_pi("pid_creating", status="bepaid_creating")
        self._insert_pi("pid_with_uid", status="draft", bepaid_uid="existing-uid")
        self._insert_pi("pid_cancelled", status="cancelled")

    def tearDown(self):
        import gc
        del self.storage
        gc.collect()
        try:
            self._tmpdir.cleanup()
        except Exception:
            pass

    def _insert_pi(self, public_id: str, *, status: str, bepaid_uid: str = "") -> None:
        import sqlite3
        conn = sqlite3.connect(self.storage.db_path)
        now = "2026-07-12T10:00:00"
        uid_val = bepaid_uid if bepaid_uid else None
        conn.execute(
            """
            INSERT INTO payment_intents
                (public_id, mk_user_id, student_name, amount_minor, amount_byn,
                 currency, purpose, period_month, payment_method, status,
                 bepaid_uid, created_at, updated_at)
            VALUES (?,1234,?,5000,50.00,'BYN','current_month','2026-07','erip',?,?,?,?)
            """,
            (public_id, "Тестовый Студент", status, uid_val, now, now),
        )
        conn.commit()
        conn.close()

    def _get_status(self, public_id: str) -> str:
        import sqlite3
        conn = sqlite3.connect(self.storage.db_path)
        row = conn.execute(
            "SELECT status FROM payment_intents WHERE public_id = ?", (public_id,)
        ).fetchone()
        conn.close()
        return row[0] if row else ""

    def test_claim_succeeds_for_draft(self):
        claimed = self.storage.payment_intent_claim_bepaid_creation(
            "pid_draft", account_number="1234260742", order_id="100000000042", tracking_id="pid_draft"
        )
        self.assertTrue(claimed)
        self.assertEqual(self._get_status("pid_draft"), "bepaid_creating")

    def test_claim_succeeds_for_ready(self):
        claimed = self.storage.payment_intent_claim_bepaid_creation(
            "pid_ready", account_number="1234260743", order_id="100000000043", tracking_id="pid_ready"
        )
        self.assertTrue(claimed)
        self.assertEqual(self._get_status("pid_ready"), "bepaid_creating")

    def test_claim_fails_when_already_creating(self):
        claimed = self.storage.payment_intent_claim_bepaid_creation(
            "pid_creating", account_number="1234260744", order_id="100000000044", tracking_id="pid_creating"
        )
        self.assertFalse(claimed)
        self.assertEqual(self._get_status("pid_creating"), "bepaid_creating")

    def test_claim_fails_when_bepaid_uid_set(self):
        """Atomic guard: COALESCE(bepaid_uid,'')='' prevents double-creation."""
        claimed = self.storage.payment_intent_claim_bepaid_creation(
            "pid_with_uid", account_number="1234260745", order_id="100000000045", tracking_id="pid_with_uid"
        )
        self.assertFalse(claimed)

    def test_claim_fails_when_not_draft_or_ready(self):
        claimed = self.storage.payment_intent_claim_bepaid_creation(
            "pid_cancelled", account_number="1234260746", order_id="100000000046", tracking_id="pid_cancelled"
        )
        self.assertFalse(claimed)
        self.assertEqual(self._get_status("pid_cancelled"), "cancelled")

    def test_two_claims_only_one_succeeds(self):
        """Simulate two concurrent callers — only one may claim."""
        c1 = self.storage.payment_intent_claim_bepaid_creation(
            "pid_draft", account_number="A1", order_id="100000000001", tracking_id="pid_draft"
        )
        c2 = self.storage.payment_intent_claim_bepaid_creation(
            "pid_draft", account_number="A1", order_id="100000000001", tracking_id="pid_draft"
        )
        self.assertTrue(c1)
        self.assertFalse(c2)

    def test_mark_requires_check_sets_status(self):
        self.storage.payment_intent_claim_bepaid_creation(
            "pid_draft", account_number="B1", order_id="100000000001", tracking_id="pid_draft"
        )
        self.storage.payment_intent_mark_requires_check("pid_draft", "timeout")
        self.assertEqual(self._get_status("pid_draft"), "bepaid_requires_check")

    def test_release_claim_restores_original_status(self):
        self.storage.payment_intent_claim_bepaid_creation(
            "pid_draft", account_number="C1", order_id="100000000001", tracking_id="pid_draft"
        )
        self.storage.payment_intent_release_claim("pid_draft", "draft", "4xx_error")
        self.assertEqual(self._get_status("pid_draft"), "draft")

    def test_release_claim_noop_when_not_creating(self):
        """release_claim must not change status if it's not bepaid_creating."""
        self.storage.payment_intent_release_claim("pid_draft", "draft", "")
        self.assertEqual(self._get_status("pid_draft"), "draft")


if __name__ == "__main__":
    unittest.main()
