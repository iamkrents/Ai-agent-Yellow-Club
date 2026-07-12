"""
Unit tests for bepaid_client.py.
No real network requests — requests.post is mocked throughout.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

import requests

from bepaid_client import (
    BePaidClient,
    BePaidResult,
    build_erip_description,
)


def _fake_response(status_code: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


class TestBePaidClientStaticHelpers(unittest.TestCase):
    def test_erip_account_number_with_period(self):
        result = BePaidClient.erip_account_number(8875658, "2026-07")
        self.assertEqual(result, "88756582607")

    def test_erip_account_number_without_period(self):
        result = BePaidClient.erip_account_number(12345, "")
        self.assertEqual(result, "12345")

    def test_erip_order_id_pads_to_12(self):
        self.assertEqual(BePaidClient.erip_order_id(1), "000000000001")
        self.assertEqual(BePaidClient.erip_order_id(999), "000000000999")
        self.assertEqual(BePaidClient.erip_order_id(123456789012), "123456789012")

    def test_erip_account_number_unique_per_user_and_period(self):
        a1 = BePaidClient.erip_account_number(100, "2026-07")
        a2 = BePaidClient.erip_account_number(200, "2026-07")
        a3 = BePaidClient.erip_account_number(100, "2026-08")
        self.assertNotEqual(a1, a2)
        self.assertNotEqual(a1, a3)

    def test_erip_order_id_unique_per_row(self):
        self.assertNotEqual(BePaidClient.erip_order_id(1), BePaidClient.erip_order_id(2))

    def test_tracking_id_matches_public_id(self):
        """tracking_id in payload must equal the payment_intent public_id."""
        payload = BePaidClient.build_erip_payload(
            amount_minor=5000,
            description="Test",
            account_number="88756582607",
            tracking_id="ycpi_202607_0001",
            order_id="000000000001",
        )
        self.assertEqual(payload["request"]["tracking_id"], "ycpi_202607_0001")


class TestBePaidPayloadBuilder(unittest.TestCase):
    def _build(self, **kw) -> dict:
        defaults = dict(
            amount_minor=10000,
            currency="BYN",
            description="Жёлтый Клуб — оплата",
            account_number="88756582607",
            tracking_id="ycpi_202607_1",
            order_id="000000000001",
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

    def test_notification_url_omitted_when_empty(self):
        p = self._build(notification_url="")
        self.assertNotIn("notification_url", p["request"])

    def test_customer_included_when_name_given(self):
        p = self._build(customer_first_name="Иван", customer_last_name="Иванов")
        self.assertEqual(p["request"]["customer"]["first_name"], "Иван")

    def test_customer_omitted_when_no_name(self):
        p = self._build()
        self.assertNotIn("customer", p["request"])

    def test_order_id_in_payload(self):
        p = self._build(order_id="000000000042")
        self.assertEqual(p["request"]["order_id"], "000000000042")


class TestBePaidClientCreateErip(unittest.TestCase):
    def _client(self):
        return BePaidClient(shop_id="test_shop", secret_key="test_secret", timeout=10)

    def _payload(self):
        return BePaidClient.build_erip_payload(
            amount_minor=5000,
            description="Тест",
            account_number="88756582607",
            tracking_id="ycpi_202607_1",
            order_id="000000000001",
        )

    @patch("bepaid_client.requests.post")
    def test_success_response_parsing(self, mock_post):
        mock_post.return_value = _fake_response(200, {
            "transaction": {
                "uid": "abc-123",
                "status": "successful",
                "amount": 5000,
                "currency": "BYN",
                "order_id": "000000000001",
                "tracking_id": "ycpi_202607_1",
                "payment_method": {
                    "type": "erip",
                    "account_number": "88756582607",
                },
            }
        })
        result = self._client().create_erip_payment(self._payload())
        self.assertTrue(result.ok)
        self.assertEqual(result.http_status, 200)
        self.assertEqual(result.data["transaction_uid"], "abc-123")
        self.assertEqual(result.data["erip_account_number"], "88756582607")
        self.assertFalse(result.requires_check)

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
    def test_network_error_no_requires_check(self, mock_post):
        mock_post.side_effect = requests.RequestException("connection refused")
        result = self._client().create_erip_payment(self._payload())
        self.assertFalse(result.ok)
        self.assertFalse(result.requires_check)
        self.assertIn("network_error", result.error)

    @patch("bepaid_client.requests.post")
    def test_credentials_not_in_payload(self, mock_post):
        """Credentials must NOT appear in the request body."""
        mock_post.return_value = _fake_response(200, {"transaction": {"uid": "x"}})
        self._client().create_erip_payment(self._payload())
        call_kwargs = mock_post.call_args
        body = json.dumps(call_kwargs.kwargs.get("json") or call_kwargs[1].get("json", {}))
        self.assertNotIn("test_secret", body)
        self.assertNotIn("test_shop", body)

    @patch("bepaid_client.requests.post")
    def test_http_basic_auth_used(self, mock_post):
        """HTTP Basic auth must be set correctly."""
        mock_post.return_value = _fake_response(200, {"transaction": {"uid": "x"}})
        self._client().create_erip_payment(self._payload())
        call_kwargs = mock_post.call_args
        auth = call_kwargs.kwargs.get("auth") or call_kwargs[1].get("auth")
        self.assertEqual(auth, ("test_shop", "test_secret"))

    @patch("bepaid_client.requests.post")
    def test_already_created_idempotent_returns_existing(self, mock_post):
        """If bePaid returns HTTP 200, result is ok=True — caller handles idempotency."""
        mock_post.return_value = _fake_response(200, {
            "transaction": {"uid": "uid-existing", "status": "successful",
                            "payment_method": {"type": "erip", "account_number": "88756582607"}}
        })
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
        desc = build_erip_description({"student_name": long_name, "purpose": "current_month", "period_month": "2026-07"})
        self.assertLessEqual(len(desc), 255)


if __name__ == "__main__":
    unittest.main()
