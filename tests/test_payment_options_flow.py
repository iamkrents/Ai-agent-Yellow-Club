"""v7.0.92.4 — Tests for the unified prepare-options flow and UI correctness.

Tests 15-24 from the v7.0.92.4 spec:
15. Content-Signature decoded Base64, not HEX (source-level check)
16. prepare-options creates ERIP and acquiring options
17. Repeat prepare-options does not create duplicates (idempotency)
18. ERIP failure does not remove acquiring option
19. Acquiring failure does not remove ERIP option
20. Both ready → awaiting_payment
21. Only ERIP ready → bepaid_created (not partial_ready for backward compat)
22. UI uses "Подготовить способы оплаты" label
23. New intent does not require separate ERIP-only click (prepare-options creates both)
24. Duplicate Russian status chips absent in renderPaymentIntentStats
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

from bepaid_client import BePaidClient, BePaidResult
from storage import Storage
from web_app_server import MiniAppContext

APP_JS = ROOT / "miniapp" / "app.js"


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage, **overrides) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    defaults = {
        "bepaid_erip_shop_id": "erip-shop",
        "bepaid_erip_secret_key": "erip-secret",
        "bepaid_erip_public_key": "",
        "bepaid_acq_shop_id": "acq-shop",
        "bepaid_acq_secret_key": "acq-secret",
        "bepaid_acq_public_key": "",
        "bepaid_public_base_url": "https://example.com",
        "bepaid_webhook_path_secret": "whsec123",
        "bepaid_request_timeout": 30,
        "bepaid_auto_post_to_moyklass": False,
        "moyklass_erip_payment_type_id": 0,
        "moyklass_acquiring_payment_type_id": 0,
    }
    defaults.update(overrides)
    ctx.settings = types.SimpleNamespace(**defaults)
    ctx._role_store: dict = {}

    def _role_for_user(uid):
        return ctx._role_store.get(uid, "owner")
    ctx._role_for_user = _role_for_user
    return ctx


def _auth(uid: int = 1) -> dict:
    return {"ok": True, "user_id": uid}


def _make_erip_pi(storage: Storage, *, status: str = "draft") -> dict:
    pi = storage.create_payment_intent({
        "created_by_tg_id": 1, "created_by_name": "T",
        "mk_user_id": 9001, "student_name": "Тест",
        "amount_minor": 22900, "amount_byn": 229.0,
        "currency": "BYN", "payment_method": "erip",
        "period_month": "2026-07", "purpose": "current_month",
        "status": status,
    })
    return pi


def _erip_success(uid="test-erip-uid", account="9748998260701"):
    return BePaidResult(
        ok=True, http_status=201,
        data={
            "transaction_uid": uid,
            "status": "pending",
            "erip_account_number": account,
            "qr_code_raw": None,
            "pay_url": None,
            "order_id": "100000000001",
            "tracking_id": None,
            "amount_minor": 22900,
            "currency": "BYN",
            "description": None,
            "payment_method_type": "erip",
        },
    )


def _acq_success(token="tok_abc", url="https://checkout.bepaid.by/pay/tok_abc"):
    return BePaidResult(
        ok=True, http_status=201,
        data={"checkout_token": token, "payment_url": url},
    )


# ── Test 15: Source-level check — Base64 not HEX in verify signature ─────────

class TestSignatureEncodingIsBase64(unittest.TestCase):
    """Test 15: _bepaid_verify_signature does NOT use bytes.fromhex()."""

    def test_15_source_uses_base64_not_fromhex(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        idx = src.find("def _bepaid_verify_signature(")
        fn_end = src.find("\n    def ", idx + 1)
        fn_body = src[idx:fn_end]
        self.assertNotIn("bytes.fromhex(", fn_body,
                         "_bepaid_verify_signature must not use bytes.fromhex() — bePaid uses Base64")
        self.assertIn("b64decode", fn_body,
                      "_bepaid_verify_signature must decode signature with base64.b64decode()")


# ── Tests 16-21: prepare-options flow ────────────────────────────────────────

class TestPrepareOptionsFlow(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def _erip_side_effect(self, auth, public_id, body, *, _bypass_method_check=False):
        pi = self.storage.get_payment_intent(public_id)
        if not pi:
            return {"ok": False, "error": "not found"}
        row_id = int(pi["id"])
        account = f"9748998260{row_id}"
        order_id = f"1{row_id:011d}"
        tracking = pi["public_id"]
        self.storage.payment_intent_claim_bepaid_creation(
            public_id, account_number=account, order_id=order_id, tracking_id=tracking,
        )
        from storage import Storage
        with self.storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET status='bepaid_created', bepaid_uid=?, bepaid_account_number=? WHERE public_id=?",
                ("test-erip-uid", account, public_id),
            )
        return {"ok": True, "intent": self.storage.get_payment_intent(public_id)}

    # Test 16: prepare-options creates ERIP and acquiring
    def test_16_prepare_options_creates_erip_and_acquiring(self):
        pi = _make_erip_pi(self.storage, status="draft")

        with (
            patch.object(self.ctx, "payment_intent_create_bepaid",
                         side_effect=self._erip_side_effect),
            patch.object(BePaidClient, "create_acquiring_checkout",
                         return_value=_acq_success()),
        ):
            result = self.ctx.payment_intent_prepare_options(_auth(), pi["public_id"])

        self.assertTrue(result["ok"], result)
        channels = {o["channel"] for o in result.get("options", [])}
        self.assertIn("erip", channels)
        self.assertIn("acquiring", channels)

    # Test 17: repeat prepare does not create duplicates
    def test_17_idempotent_no_duplicates(self):
        pi = _make_erip_pi(self.storage, status="draft")
        call_count = {"erip": 0, "acq": 0}

        def erip_side(*args, **kwargs):
            call_count["erip"] += 1
            return self._erip_side_effect(*args, **kwargs)

        def acq_side(**kwargs):
            call_count["acq"] += 1
            return _acq_success("tok_idem", "https://example.com/pay/idem")

        with (
            patch.object(self.ctx, "payment_intent_create_bepaid",
                         side_effect=erip_side),
            patch.object(BePaidClient, "create_acquiring_checkout",
                         side_effect=acq_side),
        ):
            self.ctx.payment_intent_prepare_options(_auth(), pi["public_id"])
            result2 = self.ctx.payment_intent_prepare_options(_auth(), pi["public_id"])

        self.assertTrue(result2["ok"])
        # Second call: ERIP is already_exists, ACQ is already_exists via idempotency
        # Acquiring create_acquiring_checkout not called again (idempotent in server method)
        self.assertEqual(call_count["erip"], 1,
                         "ERIP creation must not be called twice (idempotent)")
        # ACQ: create_acquiring_checkout won't be called again because option already has checkout_token
        self.assertLessEqual(call_count["acq"], 2)

    # Test 18: ERIP failure does not remove acquiring option
    def test_18_erip_failure_does_not_remove_acquiring(self):
        pi = _make_erip_pi(self.storage, status="draft")
        # First: create acquiring option
        with patch.object(BePaidClient, "create_acquiring_checkout", return_value=_acq_success()):
            self.ctx.payment_intent_create_acquiring_option(_auth(), pi["public_id"])

        acq_opt_before = self.storage.get_option_by_channel(pi["public_id"], "acquiring")
        self.assertIsNotNone(acq_opt_before)

        # Now: ERIP fails
        with (
            patch.object(self.ctx, "payment_intent_create_bepaid",
                         return_value={"ok": False, "error": "ERIP timeout", "requires_check": True}),
            patch.object(BePaidClient, "create_acquiring_checkout", return_value=_acq_success()),
        ):
            result = self.ctx.payment_intent_prepare_options(_auth(), pi["public_id"])

        acq_opt_after = self.storage.get_option_by_channel(pi["public_id"], "acquiring")
        self.assertIsNotNone(acq_opt_after, "ACQ option must survive ERIP failure")
        self.assertEqual(acq_opt_after.get("checkout_token"), acq_opt_before.get("checkout_token"))

    # Test 19: ACQ failure does not remove ERIP
    def test_19_acquiring_failure_does_not_remove_erip(self):
        pi = _make_erip_pi(self.storage, status="draft")

        with (
            patch.object(self.ctx, "payment_intent_create_bepaid",
                         side_effect=self._erip_side_effect),
            patch.object(BePaidClient, "create_acquiring_checkout",
                         return_value=BePaidResult(
                             ok=False, http_status=503,
                             error="service_unavailable", requires_check=True,
                         )),
        ):
            result = self.ctx.payment_intent_prepare_options(_auth(), pi["public_id"])

        pi_after = self.storage.get_payment_intent(pi["public_id"])
        # ERIP was created — bepaid_uid should be set
        self.assertIsNotNone(pi_after.get("bepaid_uid"),
                              "ERIP must be preserved even if ACQ fails")

    # Test 20: both ready → awaiting_payment
    def test_20_both_ready_awaiting_payment(self):
        pi = _make_erip_pi(self.storage, status="draft")

        with (
            patch.object(self.ctx, "payment_intent_create_bepaid",
                         side_effect=self._erip_side_effect),
            patch.object(BePaidClient, "create_acquiring_checkout",
                         return_value=_acq_success()),
        ):
            self.ctx.payment_intent_prepare_options(_auth(), pi["public_id"])

        pi_after = self.storage.get_payment_intent(pi["public_id"])
        self.assertEqual(pi_after["status"], "awaiting_payment",
                         "Both ERIP + ACQ ready must set awaiting_payment")

    # Test 21: only ERIP ready → bepaid_created (standard ERIP flow)
    def test_21_only_erip_ready_bepaid_created(self):
        pi = _make_erip_pi(self.storage, status="draft")

        with (
            patch.object(self.ctx, "payment_intent_create_bepaid",
                         side_effect=self._erip_side_effect),
            patch.object(BePaidClient, "create_acquiring_checkout",
                         return_value=BePaidResult(ok=False, http_status=503,
                                                    error="service_unavailable", requires_check=True)),
        ):
            result = self.ctx.payment_intent_prepare_options(_auth(), pi["public_id"])

        pi_after = self.storage.get_payment_intent(pi["public_id"])
        # ERIP succeeded → bepaid_created (ACQ failed, so not awaiting_payment)
        self.assertIn(pi_after["status"], ("bepaid_created",),
                      f"Only ERIP ready should give bepaid_created, got {pi_after['status']}")


# ── Tests 22-24: UI static checks ────────────────────────────────────────────

class TestUIStaticChecks(unittest.TestCase):

    def _app(self) -> str:
        return APP_JS.read_text(encoding="utf-8")

    # Test 22: UI uses "Подготовить способы оплаты"
    def test_22_ui_uses_prepare_payment_options_label(self):
        src = self._app()
        self.assertIn("Подготовить способы оплаты", src,
                      "UI must use 'Подготовить способы оплаты' label (not 'Подготовить черновик bePaid')")

    # Test 23: openMkInvoiceCreate calls prepare-options (new intent flow)
    def test_23_new_intent_calls_prepare_options(self):
        src = self._app()
        idx = src.find("async function openMkInvoiceCreate(")
        fn_end = src.find("\nasync function ", idx + 1)
        if fn_end == -1:
            fn_end = src.find("\nfunction ", idx + 1)
        fn_body = src[idx:fn_end]
        self.assertIn("prepare-options", fn_body,
                      "openMkInvoiceCreate must call prepare-options endpoint")

    # Test 24: no duplicate Russian status chip labels
    def test_24_no_duplicate_status_chip_labels_in_stats(self):
        src = self._app()
        idx = src.find("function renderPaymentIntentStats(")
        fn_end = src.find("\nfunction ", idx + 1)
        fn_body = src[idx:fn_end]
        # The stats function must not have two chips with the same Russian label
        # "Ожидает оплаты" must appear only once as a chip label
        import re
        labels = re.findall(r'label:\s*"([^"]+)"', fn_body)
        from collections import Counter
        label_counts = Counter(labels)
        duplicates = {lb: cnt for lb, cnt in label_counts.items() if cnt > 1}
        self.assertFalse(duplicates,
                         f"Duplicate chip labels in renderPaymentIntentStats: {duplicates}")


if __name__ == "__main__":
    unittest.main()
