"""Tests for v7.0.92.3 — bePaid acquiring (hosted checkout) implementation.

Covers:
- create_acquiring_checkout validation and payload building
- Checkout response parsing (200/4xx/5xx/timeout/connection)
- X-API-Version: 2 header presence
- BEPAID_CHECKOUT_ENDPOINT constant
- checkout_token column in payment_intent_options
- update_option_checkout storage method
- payment_intent_update_status storage method
- payment_intent_create_acquiring_option server method
- Status transitions: bepaid_created→awaiting_payment, draft→partial_ready
- Idempotency
- /payment-return route
- JS version bump and new status labels
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bepaid_client import (
    BEPAID_CHECKOUT_ENDPOINT,
    BEPAID_ERIP_ENDPOINT,
    BePaidClient,
    BePaidResult,
)
from storage import Storage
from web_app_server import MiniAppContext

APP_JS = ROOT / "miniapp" / "app.js"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _fake_response(status_code: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


def _checkout_success_body(token: str = "tok_abc123", redirect_url: str = "https://checkout.bepaid.by/pay/tok_abc123") -> dict:
    return {"checkout": {"token": token, "redirect_url": redirect_url, "status": "pending"}}


def _make_ctx(storage: Storage, **settings_overrides) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    defaults = {
        "bepaid_acq_shop_id": "acq-shop-001",
        "bepaid_acq_secret_key": "acq-secret-key",
        "bepaid_acq_public_key": "",
        "bepaid_erip_shop_id": "",
        "bepaid_erip_secret_key": "",
        "bepaid_public_base_url": "https://example.com",
        "bepaid_webhook_path_secret": "whsec123",
        "bepaid_request_timeout": 30,
        "bepaid_auto_post_to_moyklass": False,
        "moyklass_erip_payment_type_id": 0,
        "moyklass_acquiring_payment_type_id": 0,
    }
    defaults.update(settings_overrides)
    ctx.settings = types.SimpleNamespace(**defaults)
    ctx._role_store: dict[int, str] = {}

    def _role_for_user(uid: int) -> str:
        return ctx._role_store.get(uid, "owner")

    ctx._role_for_user = _role_for_user
    return ctx


def _auth(uid: int = 1) -> dict:
    return {"ok": True, "user_id": uid}


def _make_pi(storage: Storage, *, status: str = "draft", payment_method: str = "acquiring") -> dict:
    pi = storage.create_payment_intent({
        "created_by_tg_id": 1,
        "created_by_name": "Test",
        "mk_user_id": 9001,
        "student_name": "Тест",
        "amount_minor": 22900,
        "amount_byn": 229.0,
        "currency": "BYN",
        "payment_method": payment_method,
        "period_month": "2026-07",
        "purpose": "current_month",
        "status": status,
    })
    return pi


# ─────────────────────────────────────────────────────────────────────────────
# 1. Constant
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckoutEndpointConstant(unittest.TestCase):

    def test_01_bepaid_checkout_endpoint_correct(self):
        """Test 1: BEPAID_CHECKOUT_ENDPOINT has the confirmed URL."""
        self.assertEqual(
            BEPAID_CHECKOUT_ENDPOINT,
            "https://checkout.bepaid.by/ctp/api/checkouts",
        )

    def test_02_old_unconfirmed_constant_removed(self):
        """Test 2: BEPAID_ACQ_ENDPOINT_UNCONFIRMED no longer exists in bepaid_client."""
        import bepaid_client
        self.assertFalse(
            hasattr(bepaid_client, "BEPAID_ACQ_ENDPOINT_UNCONFIRMED"),
            "BEPAID_ACQ_ENDPOINT_UNCONFIRMED should have been removed in v7.0.92.3",
        )

    def test_03_erip_endpoint_unchanged(self):
        """Test 3: ERIP endpoint constant is not touched."""
        self.assertEqual(BEPAID_ERIP_ENDPOINT, "https://api.bepaid.by/beyag/payments")


# ─────────────────────────────────────────────────────────────────────────────
# 2. create_acquiring_checkout — validation
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateAcquiringCheckoutValidation(unittest.TestCase):

    def setUp(self):
        self.client = BePaidClient(shop_id="s1", secret_key="k1", timeout=5)

    def test_04_amount_minor_not_int_raises(self):
        """Test 4: non-int amount_minor raises ValueError."""
        with self.assertRaises(ValueError):
            self.client.create_acquiring_checkout(
                amount_minor=22.9, currency="BYN",
                description="Test", tracking_id="t1",
                notification_url="https://example.com/hook",
                return_url="https://example.com/return",
            )

    def test_05_amount_minor_zero_raises(self):
        """Test 5: amount_minor=0 raises ValueError."""
        with self.assertRaises(ValueError):
            self.client.create_acquiring_checkout(
                amount_minor=0, currency="BYN",
                description="Test", tracking_id="t1",
                notification_url="https://example.com/hook",
                return_url="https://example.com/return",
            )

    def test_06_amount_minor_negative_raises(self):
        """Test 6: negative amount_minor raises ValueError."""
        with self.assertRaises(ValueError):
            self.client.create_acquiring_checkout(
                amount_minor=-100, currency="BYN",
                description="Test", tracking_id="t1",
                notification_url="https://example.com/hook",
                return_url="https://example.com/return",
            )

    def test_07_currency_not_byn_raises(self):
        """Test 7: currency other than BYN raises ValueError."""
        with self.assertRaises(ValueError):
            self.client.create_acquiring_checkout(
                amount_minor=1000, currency="USD",
                description="Test", tracking_id="t1",
                notification_url="https://example.com/hook",
                return_url="https://example.com/return",
            )

    def test_08_notification_url_not_https_raises(self):
        """Test 8: http:// notification_url raises ValueError."""
        with self.assertRaises(ValueError):
            self.client.create_acquiring_checkout(
                amount_minor=1000, currency="BYN",
                description="Test", tracking_id="t1",
                notification_url="http://example.com/hook",
                return_url="https://example.com/return",
            )

    def test_09_return_url_not_https_raises(self):
        """Test 9: http:// return_url raises ValueError."""
        with self.assertRaises(ValueError):
            self.client.create_acquiring_checkout(
                amount_minor=1000, currency="BYN",
                description="Test", tracking_id="t1",
                notification_url="https://example.com/hook",
                return_url="http://example.com/return",
            )


# ─────────────────────────────────────────────────────────────────────────────
# 3. build_checkout_payload
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildCheckoutPayload(unittest.TestCase):

    def test_10_payload_structure(self):
        """Test 10: build_checkout_payload produces correct nested structure."""
        p = BePaidClient.build_checkout_payload(
            amount_minor=22900,
            currency="BYN",
            description="Жёлтый Клуб — Тест",
            tracking_id="ycpi_202607_1_acq",
            notification_url="https://example.com/webhook/acquiring/secret",
            return_url="https://example.com/payment-return",
        )
        self.assertIn("checkout", p)
        co = p["checkout"]
        self.assertEqual(co["transaction_type"], "payment")
        self.assertEqual(co["order"]["amount"], 22900)
        self.assertEqual(co["order"]["currency"], "BYN")
        self.assertEqual(co["order"]["tracking_id"], "ycpi_202607_1_acq")
        self.assertEqual(co["settings"]["language"], "ru")
        self.assertEqual(co["settings"]["auto_return"], 0)
        self.assertEqual(co["settings"]["notification_url"], "https://example.com/webhook/acquiring/secret")
        self.assertEqual(co["settings"]["return_url"], "https://example.com/payment-return")
        self.assertEqual(co["payment_method"]["types"], ["credit_card"])

    def test_11_test_flag_included(self):
        """Test 11: test=True adds test:true to checkout payload."""
        p = BePaidClient.build_checkout_payload(
            amount_minor=1000, currency="BYN", description="T",
            tracking_id="t1",
            notification_url="https://a.com/n",
            return_url="https://a.com/r",
            test=True,
        )
        self.assertTrue(p["checkout"].get("test"))

    def test_12_test_false_not_included(self):
        """Test 12: test=False (default) does not add test key."""
        p = BePaidClient.build_checkout_payload(
            amount_minor=1000, currency="BYN", description="T",
            tracking_id="t1",
            notification_url="https://a.com/n",
            return_url="https://a.com/r",
        )
        self.assertNotIn("test", p["checkout"])

    def test_13_customer_included_when_provided(self):
        """Test 13: customer dict is included in checkout payload."""
        p = BePaidClient.build_checkout_payload(
            amount_minor=1000, currency="BYN", description="T",
            tracking_id="t1",
            notification_url="https://a.com/n",
            return_url="https://a.com/r",
            customer={"first_name": "Иван", "last_name": "Иванов"},
        )
        self.assertIn("customer", p["checkout"])
        self.assertEqual(p["checkout"]["customer"]["first_name"], "Иван")


# ─────────────────────────────────────────────────────────────────────────────
# 4. _parse_checkout_response
# ─────────────────────────────────────────────────────────────────────────────

class TestParseCheckoutResponse(unittest.TestCase):

    def test_14_http200_success(self):
        """Test 14: HTTP 200 with token and redirect_url → ok=True, data fields set."""
        resp = _fake_response(200, _checkout_success_body("tok123", "https://pay.bepaid.by/tok123"))
        result = BePaidClient._parse_checkout_response(resp)
        self.assertTrue(result.ok)
        self.assertEqual(result.http_status, 200)
        self.assertEqual(result.data["checkout_token"], "tok123")
        self.assertEqual(result.data["payment_url"], "https://pay.bepaid.by/tok123")
        self.assertFalse(result.requires_check)

    def test_15_http200_missing_token(self):
        """Test 15: HTTP 200 but no token → ok=False, requires_check=False."""
        resp = _fake_response(200, {"checkout": {"redirect_url": "https://pay.bepaid.by/x"}})
        result = BePaidClient._parse_checkout_response(resp)
        self.assertFalse(result.ok)
        self.assertFalse(result.requires_check)
        self.assertIn("missing_checkout_fields", result.error)

    def test_16_http200_missing_redirect_url(self):
        """Test 16: HTTP 200 but no redirect_url → ok=False, requires_check=False."""
        resp = _fake_response(200, {"checkout": {"token": "tok123"}})
        result = BePaidClient._parse_checkout_response(resp)
        self.assertFalse(result.ok)
        self.assertFalse(result.requires_check)

    def test_17_http4xx_definitive_failure(self):
        """Test 17: HTTP 4xx → ok=False, requires_check=False."""
        resp = _fake_response(422, {"errors": {"amount": ["is invalid"]}})
        result = BePaidClient._parse_checkout_response(resp)
        self.assertFalse(result.ok)
        self.assertFalse(result.requires_check)
        self.assertEqual(result.http_status, 422)

    def test_18_http5xx_requires_check(self):
        """Test 18: HTTP 5xx → ok=False, requires_check=True."""
        resp = _fake_response(500, {"message": "Internal Server Error"})
        result = BePaidClient._parse_checkout_response(resp)
        self.assertFalse(result.ok)
        self.assertTrue(result.requires_check)
        self.assertEqual(result.http_status, 500)


# ─────────────────────────────────────────────────────────────────────────────
# 5. _post_checkout — network errors and headers
# ─────────────────────────────────────────────────────────────────────────────

class TestPostCheckout(unittest.TestCase):

    def setUp(self):
        self.client = BePaidClient(shop_id="shop1", secret_key="sec1", timeout=10)

    def test_19_timeout_requires_check(self):
        """Test 19: requests.Timeout → requires_check=True."""
        with patch("requests.post", side_effect=requests.Timeout()):
            result = self.client._post_checkout(BEPAID_CHECKOUT_ENDPOINT, {})
        self.assertFalse(result.ok)
        self.assertTrue(result.requires_check)
        self.assertEqual(result.error, "timeout")

    def test_20_connection_error_requires_check(self):
        """Test 20: requests.ConnectionError → requires_check=True."""
        with patch("requests.post", side_effect=requests.ConnectionError("refused")):
            result = self.client._post_checkout(BEPAID_CHECKOUT_ENDPOINT, {})
        self.assertFalse(result.ok)
        self.assertTrue(result.requires_check)
        self.assertIn("connection_error", result.error)

    def test_21_request_exception_no_requires_check(self):
        """Test 21: generic RequestException → requires_check=False."""
        with patch("requests.post", side_effect=requests.RequestException("misc")):
            result = self.client._post_checkout(BEPAID_CHECKOUT_ENDPOINT, {})
        self.assertFalse(result.ok)
        self.assertFalse(result.requires_check)
        self.assertIn("network_error", result.error)

    def test_22_x_api_version_header_sent(self):
        """Test 22: POST includes X-API-Version: 2 header."""
        with patch("requests.post") as mock_post:
            mock_post.return_value = _fake_response(200, _checkout_success_body())
            self.client._post_checkout(BEPAID_CHECKOUT_ENDPOINT, {"checkout": {}})
        call_kwargs = mock_post.call_args[1]
        headers = call_kwargs.get("headers", {})
        self.assertEqual(headers.get("X-API-Version"), "2")

    def test_23_posts_to_checkout_endpoint(self):
        """Test 23: _post_checkout posts to the acquiring checkout URL."""
        with patch("requests.post") as mock_post:
            mock_post.return_value = _fake_response(200, _checkout_success_body())
            self.client._post_checkout(BEPAID_CHECKOUT_ENDPOINT, {})
        call_args = mock_post.call_args[0]
        self.assertEqual(call_args[0], BEPAID_CHECKOUT_ENDPOINT)

    def test_24_erip_post_does_not_send_x_api_version(self):
        """Test 24: _post (ERIP) does NOT include X-API-Version header."""
        with patch("requests.post") as mock_post:
            mock_post.return_value = _fake_response(200, {"transaction": {}})
            self.client._post(BEPAID_ERIP_ENDPOINT, {})
        call_kwargs = mock_post.call_args[1]
        headers = call_kwargs.get("headers", {})
        self.assertNotIn("X-API-Version", headers)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Storage — checkout_token column and update_option_checkout
# ─────────────────────────────────────────────────────────────────────────────

class TestStorageCheckoutToken(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()

    def _make_intent(self) -> dict:
        return self.storage.create_payment_intent({
            "created_by_tg_id": 1, "created_by_name": "T",
            "mk_user_id": 1001, "student_name": "T",
            "amount_minor": 22900, "amount_byn": 229.0,
            "currency": "BYN", "payment_method": "acquiring",
            "period_month": "2026-07", "purpose": "current_month",
            "status": "draft",
        })

    def test_25_checkout_token_column_exists(self):
        """Test 25: payment_intent_options has checkout_token column."""
        pi = self._make_intent()
        option = self.storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="acquiring",
            shop_type="acquiring",
            bepaid_tracking_id=f"{pi['public_id']}_acq",
        )
        self.assertIn("checkout_token", option)
        self.assertIsNone(option["checkout_token"])

    def test_26_update_option_checkout_sets_token_and_url(self):
        """Test 26: update_option_checkout stores token and payment_url."""
        pi = self._make_intent()
        option = self.storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="acquiring",
            shop_type="acquiring",
            bepaid_tracking_id=f"{pi['public_id']}_acq",
        )
        updated = self.storage.update_option_checkout(
            int(option["id"]),
            checkout_token="tok_xyz",
            payment_url="https://checkout.bepaid.by/pay/tok_xyz",
        )
        self.assertEqual(updated["checkout_token"], "tok_xyz")
        self.assertEqual(updated["payment_url"], "https://checkout.bepaid.by/pay/tok_xyz")

    def test_27_update_option_checkout_persists(self):
        """Test 27: updated checkout token is readable from storage after reload."""
        pi = self._make_intent()
        option = self.storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="acquiring",
            shop_type="acquiring",
        )
        self.storage.update_option_checkout(
            int(option["id"]),
            checkout_token="persisted-tok",
            payment_url="https://example.com/pay",
        )
        reloaded = self.storage.get_option_by_channel(pi["public_id"], "acquiring")
        self.assertEqual(reloaded["checkout_token"], "persisted-tok")

    def test_28_payment_intent_update_status(self):
        """Test 28: payment_intent_update_status changes status and returns True."""
        pi = self._make_intent()
        public_id = pi["public_id"]
        changed = self.storage.payment_intent_update_status(public_id, "partial_ready")
        self.assertTrue(changed)
        reloaded = self.storage.get_payment_intent(public_id)
        self.assertEqual(reloaded["status"], "partial_ready")

    def test_29_payment_intent_update_status_nonexistent_returns_false(self):
        """Test 29: update_status for unknown public_id returns False."""
        result = self.storage.payment_intent_update_status("nonexistent_id", "partial_ready")
        self.assertFalse(result)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Server: payment_intent_create_acquiring_option
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateAcquiringOption(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def _make_pi(self, status: str = "draft", payment_method: str = "acquiring") -> dict:
        return _make_pi(self.storage, status=status, payment_method=payment_method)

    def _mock_success(self, token: str = "tok_ok", url: str = "https://checkout.bepaid.by/pay/tok_ok"):
        return BePaidResult(
            ok=True, http_status=201,
            data={"checkout_token": token, "payment_url": url},
        )

    def test_30_missing_config_returns_error(self):
        """Test 30: missing ACQ credentials → error, no API call."""
        ctx = _make_ctx(self.storage, bepaid_acq_shop_id="", bepaid_acq_secret_key="")
        pi = self._make_pi()
        result = ctx.payment_intent_create_acquiring_option(_auth(), pi["public_id"])
        self.assertFalse(result["ok"])
        self.assertIn("BEPAID_ACQ_SHOP_ID", result["error"])

    def test_31_pi_not_found(self):
        """Test 31: non-existent public_id → error."""
        result = self.ctx.payment_intent_create_acquiring_option(_auth(), "nonexistent")
        self.assertFalse(result["ok"])

    def test_32_cancelled_status_blocked(self):
        """Test 32: cancelled intent → error."""
        pi = self._make_pi(status="cancelled")
        result = self.ctx.payment_intent_create_acquiring_option(_auth(), pi["public_id"])
        self.assertFalse(result["ok"])
        self.assertIn("cancelled", result["error"])

    def test_33_success_draft_creates_option_and_updates_status(self):
        """Test 33: success on draft intent → partial_ready status, payment_url returned."""
        pi = self._make_pi(status="draft")
        with patch.object(BePaidClient, "create_acquiring_checkout", return_value=self._mock_success()):
            result = self.ctx.payment_intent_create_acquiring_option(_auth(), pi["public_id"])
        self.assertTrue(result["ok"], result)
        self.assertIn("payment_url", result)
        updated_pi = self.storage.get_payment_intent(pi["public_id"])
        self.assertEqual(updated_pi["status"], "partial_ready")

    def test_34_success_bepaid_created_sets_awaiting_payment(self):
        """Test 34: ERIP already created (bepaid_created) → awaiting_payment after acquiring."""
        pi = self._make_pi(status="bepaid_created", payment_method="acquiring")
        with patch.object(BePaidClient, "create_acquiring_checkout", return_value=self._mock_success()):
            result = self.ctx.payment_intent_create_acquiring_option(_auth(), pi["public_id"])
        self.assertTrue(result["ok"], result)
        updated_pi = self.storage.get_payment_intent(pi["public_id"])
        self.assertEqual(updated_pi["status"], "awaiting_payment")

    def test_35_idempotent_returns_existing(self):
        """Test 35: calling twice returns existing payment_url without new API call."""
        pi = self._make_pi(status="draft")
        with patch.object(BePaidClient, "create_acquiring_checkout", return_value=self._mock_success("tok_first", "https://example.com/pay/first")) as mock_api:
            self.ctx.payment_intent_create_acquiring_option(_auth(), pi["public_id"])
            result2 = self.ctx.payment_intent_create_acquiring_option(_auth(), pi["public_id"])
        self.assertTrue(result2["ok"])
        self.assertEqual(result2["payment_url"], "https://example.com/pay/first")
        self.assertTrue(result2.get("already_exists"))
        self.assertEqual(mock_api.call_count, 1)

    def test_36_notification_url_uses_acquiring_path(self):
        """Test 36: notification_url built for acquiring webhook route."""
        pi = self._make_pi(status="draft")
        captured_kwargs = {}
        def capture(**kwargs):
            captured_kwargs.update(kwargs)
            return self._mock_success()
        with patch.object(BePaidClient, "create_acquiring_checkout", side_effect=capture):
            self.ctx.payment_intent_create_acquiring_option(_auth(), pi["public_id"])
        n_url = captured_kwargs["notification_url"]
        self.assertIn("/webhook/acquiring/", n_url)
        self.assertTrue(n_url.startswith("https://"))

    def test_37_return_url_is_https(self):
        """Test 37: return_url passed to checkout is HTTPS."""
        pi = self._make_pi(status="draft")
        captured_kwargs = {}
        def capture(**kwargs):
            captured_kwargs.update(kwargs)
            return self._mock_success()
        with patch.object(BePaidClient, "create_acquiring_checkout", side_effect=capture):
            self.ctx.payment_intent_create_acquiring_option(_auth(), pi["public_id"])
        r_url = captured_kwargs["return_url"]
        self.assertTrue(r_url.startswith("https://"))
        self.assertIn("payment-return", r_url)


if __name__ == "__main__":
    unittest.main()
