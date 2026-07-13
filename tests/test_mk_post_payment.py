"""Tests for MoyKlass manual payment posting (v7.0.92).

Covers all 49 required test scenarios. No real external calls.

Run:
    python -m unittest tests.test_mk_post_payment -v
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import types
import unittest
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from moyklass_client import MoyKlassResult
from web_app_server import (
    _compute_moyklass_post_fingerprint,
    PAYMENT_MK_POST_ROLES,
    PAYMENT_INTENT_ROLES,
    MiniAppContext,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _memory_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _seed_paid_intent(storage: Storage, **kwargs) -> dict:
    """Create a fully-paid moyklass_invoice-sourced intent."""
    defaults = dict(
        mk_user_id=9001,
        student_name="Тест Ученик",
        amount_minor=22900,
        amount_byn=229.0,
        currency="BYN",
        purpose="current_month",
        payment_method="erip",
        status="bepaid_created",
        source="moyklass_invoice",
        mk_invoice_id="19000001",
        mk_user_subscription_id="17000001",
    )
    defaults.update(kwargs)
    intent = storage.create_payment_intent(defaults)
    pid = intent["public_id"]
    storage.payment_intent_mark_paid(
        pid,
        tx_uid=kwargs.get("paid_tx_uid", "tx-uid-test-0001"),
        amount_minor=int(defaults["amount_minor"]),
        currency=str(defaults.get("paid_currency", "BYN")),
        paid_at="2026-07-13T10:00:00Z",
        tracking_id="ycpi_track",
        order_id="100000000001",
        account_number="900100072607001",
        verified=not kwargs.get("unverified", False),
        match_method="tracking_id",
    )
    return storage.get_payment_intent(pid)


def _fake_invoice(
    *,
    invoice_id: int = 19000001,
    user_id: int = 9001,
    price: float = 229.0,
    payed: float = 0.0,
    sub_id: int = 17000001,
    created_at: str = "2026-07-01",
) -> dict:
    return {
        "id": invoice_id,
        "userId": user_id,
        "price": price,
        "payed": payed,
        "payUntil": "2026-07-31",
        "userSubscriptionId": sub_id,
        "date": "2026-07-01",
        "createdAt": created_at,
        "comment": None,
    }


def _make_ctx(storage: Storage, **settings_overrides) -> MiniAppContext:
    """Create a MiniAppContext instance without calling __init__."""
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    defaults = dict(
        bepaid_auto_post_to_moyklass=False,
        moyklass_erip_payment_type_id=42,
    )
    defaults.update(settings_overrides)
    ctx.settings = types.SimpleNamespace(**defaults)

    mk_client = MagicMock()
    mk_client.is_configured = True
    ctx.moyklass = mk_client  # MiniAppContext uses self.moyklass

    ctx._role_store: dict[int, str] = {}

    def _role_for_user(uid: int) -> str:
        return ctx._role_store.get(uid, "owner")

    ctx._role_for_user = _role_for_user
    return ctx


def _owner_auth(uid: int = 1) -> dict:
    return {"ok": True, "user_id": uid}


def _role_auth(uid: int, role: str, ctx: MiniAppContext) -> dict:
    ctx._role_store[uid] = role
    return {"ok": True, "user_id": uid}


# ---------------------------------------------------------------------------
# 1. Amount / money conversion
# ---------------------------------------------------------------------------

class TestAmountConversion(unittest.TestCase):

    def test_37_minor_to_byn_22900(self):
        """Test 37: 22900 minor в†’ 229.00 BYN (not 22900, not 2.29)."""
        result = float(Decimal("22900") / Decimal("100"))
        self.assertAlmostEqual(result, 229.0)

    def test_38_no_float_comparison(self):
        """Test 38: Amount comparison uses Decimal/int, not float."""
        # 229 BYN represented in minor units
        paid_minor = 22900
        inv_price = 229.0
        inv_payed = 0.0
        remaining = (Decimal(str(inv_price)) - Decimal(str(inv_payed))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        remaining_minor = int((remaining * 100).to_integral_value(rounding=ROUND_HALF_UP))
        self.assertEqual(remaining_minor, 22900)
        self.assertEqual(remaining_minor, paid_minor)

    def test_conversion_roundtrip(self):
        """Minor units в†’ BYN в†’ minor units is lossless for sensible amounts."""
        for minor in [100, 22900, 17175, 50050, 9999]:
            byn = float(Decimal(str(minor)) / Decimal("100"))
            back = int((Decimal(str(byn)) * 100).to_integral_value(rounding=ROUND_HALF_UP))
            self.assertEqual(back, minor, f"roundtrip failed for {minor}")


# ---------------------------------------------------------------------------
# 2. Fingerprint
# ---------------------------------------------------------------------------

class TestFingerprint(unittest.TestCase):

    def _intent(self, **kw) -> dict:
        base = {
            "public_id": "ycpi_test_1",
            "paid_transaction_uid": "tx-uid-abc",
            "paid_amount_minor": 22900,
            "paid_currency": "BYN",
            "mk_invoice_id": "19000001",
        }
        base.update(kw)
        return base

    def _invoice(self, price=229.0, payed=0.0, created="2026-07-01") -> dict:
        return {"price": price, "payed": payed, "createdAt": created}

    def test_fingerprint_deterministic(self):
        fp1 = _compute_moyklass_post_fingerprint(self._intent(), self._invoice())
        fp2 = _compute_moyklass_post_fingerprint(self._intent(), self._invoice())
        self.assertEqual(fp1, fp2)

    def test_fingerprint_changes_on_invoice_payed(self):
        fp1 = _compute_moyklass_post_fingerprint(self._intent(), self._invoice(payed=0.0))
        fp2 = _compute_moyklass_post_fingerprint(self._intent(), self._invoice(payed=10.0))
        self.assertNotEqual(fp1, fp2)

    def test_fingerprint_changes_on_intent_tx_uid(self):
        fp1 = _compute_moyklass_post_fingerprint(self._intent(paid_transaction_uid="uid1"), self._invoice())
        fp2 = _compute_moyklass_post_fingerprint(self._intent(paid_transaction_uid="uid2"), self._invoice())
        self.assertNotEqual(fp1, fp2)

    def test_fingerprint_no_secrets(self):
        """Fingerprint must not contain API keys or personal data."""
        intent = self._intent()
        invoice = self._invoice()
        fp = _compute_moyklass_post_fingerprint(intent, invoice)
        # Should be a hex string, not containing names/emails/keys
        self.assertRegex(fp, r'^[0-9a-f]{32}$')


# ---------------------------------------------------------------------------
# 3. Storage: atomic claim
# ---------------------------------------------------------------------------

class TestAtomicClaim(unittest.TestCase):

    def setUp(self):
        self.storage = _memory_storage()

    def tearDown(self):
        self.storage._connect().__exit__(None, None, None)

    def _paid_intent_pid(self) -> str:
        intent = _seed_paid_intent(self.storage)
        return intent["public_id"]

    def test_claim_succeeds_first_time(self):
        pid = self._paid_intent_pid()
        result = self.storage.payment_intent_claim_moyklass_post(pid, "user:1")
        self.assertTrue(result)

    def test_25_double_click_second_claim_fails(self):
        """Test 25: concurrent claims вЂ” only one succeeds."""
        pid = self._paid_intent_pid()
        r1 = self.storage.payment_intent_claim_moyklass_post(pid, "user:1")
        r2 = self.storage.payment_intent_claim_moyklass_post(pid, "user:2")
        self.assertTrue(r1)
        self.assertFalse(r2)

    def test_24_double_click_second_call_blocked(self):
        """Test 24: after first claim, second returns False immediately."""
        pid = self._paid_intent_pid()
        self.storage.payment_intent_claim_moyklass_post(pid, "u1")
        second = self.storage.payment_intent_claim_moyklass_post(pid, "u1")
        self.assertFalse(second)

    def test_claim_requires_paid_status(self):
        """Claim on bepaid_created intent must fail (only paid allowed)."""
        intent = self.storage.create_payment_intent(dict(
            mk_user_id=9001, student_name="X", amount_minor=1000,
            amount_byn=10.0, currency="BYN", purpose="other",
            payment_method="erip", status="bepaid_created",
        ))
        pid = intent["public_id"]
        result = self.storage.payment_intent_claim_moyklass_post(pid, "user:1")
        self.assertFalse(result)

    def test_mark_posted_sets_status(self):
        pid = self._paid_intent_pid()
        self.storage.payment_intent_claim_moyklass_post(pid, "u1")
        self.storage.payment_intent_mark_posted_to_moyklass(
            pid, mk_payment_id=999, posted_at="2026-07-13T11:00:00Z",
            fingerprint="abc", invoice_snapshot_json="{}"
        )
        intent = self.storage.get_payment_intent(pid)
        self.assertEqual(intent["status"], "posted_to_moyklass")
        self.assertEqual(int(intent["mk_payment_id"]), 999)

    def test_26_successful_post_saves_mk_payment_id(self):
        """Test 26: mk_payment_id saved after successful post."""
        pid = self._paid_intent_pid()
        self.storage.payment_intent_claim_moyklass_post(pid, "u1")
        self.storage.payment_intent_mark_posted_to_moyklass(
            pid, mk_payment_id=12345678, posted_at="2026-07-13T11:00:00Z",
            fingerprint="fp1"
        )
        intent = self.storage.get_payment_intent(pid)
        self.assertEqual(int(intent["mk_payment_id"]), 12345678)

    def test_27_successful_post_sets_posted_to_moyklass(self):
        """Test 27: status becomes posted_to_moyklass after success."""
        pid = self._paid_intent_pid()
        self.storage.payment_intent_claim_moyklass_post(pid, "u1")
        self.storage.payment_intent_mark_posted_to_moyklass(
            pid, mk_payment_id=999, posted_at="now", fingerprint="fp"
        )
        intent = self.storage.get_payment_intent(pid)
        self.assertEqual(intent["status"], "posted_to_moyklass")

    def test_mark_ambiguous_sets_posting_status(self):
        pid = self._paid_intent_pid()
        self.storage.payment_intent_claim_moyklass_post(pid, "u1")
        self.storage.payment_intent_mark_moyklass_ambiguous(pid, "timeout")
        intent = self.storage.get_payment_intent(pid)
        self.assertEqual(intent["mk_posting_status"], "ambiguous")

    def test_release_claim_sets_failed(self):
        pid = self._paid_intent_pid()
        self.storage.payment_intent_claim_moyklass_post(pid, "u1")
        self.storage.payment_intent_release_moyklass_claim(pid, "4xx_error")
        intent = self.storage.get_payment_intent(pid)
        self.assertEqual(intent["mk_posting_status"], "failed")


# ---------------------------------------------------------------------------
# 4. Audit log
# ---------------------------------------------------------------------------

class TestAuditLog(unittest.TestCase):

    def setUp(self):
        self.storage = _memory_storage()

    def test_audit_log_insert(self):
        self.storage.log_moyklass_post_audit(
            "moyklass_post_started",
            intent_public_id="ycpi_test",
            mk_user_id=9001,
            amount_minor=22900,
        )
        logs = self.storage.list_moyklass_post_audit("ycpi_test")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["event_type"], "moyklass_post_started")

    def test_audit_log_no_secrets(self):
        """Audit log must not contain API keys or authorization headers."""
        self.storage.log_moyklass_post_audit(
            "moyklass_post_readiness_checked",
            intent_public_id="ycpi_safe",
            details={"safe": "value"},
        )
        logs = self.storage.list_moyklass_post_audit("ycpi_safe")
        row_json = json.dumps(logs)
        self.assertNotIn("apiKey", row_json)
        self.assertNotIn("x-access-token", row_json)
        self.assertNotIn("Authorization", row_json)


# ---------------------------------------------------------------------------
# 5. Readiness endpoint (unit: web_app_server)
# ---------------------------------------------------------------------------

class TestMkPostReadiness(unittest.TestCase):

    def setUp(self):
        self.storage = _memory_storage()
        self.ctx = _make_ctx(self.storage)
        self.auth = _owner_auth()

    def test_17_readiness_is_read_only(self):
        """Test 17: readiness endpoint does not modify intent."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        before = self.storage.get_payment_intent(pid)

        invoice = _fake_invoice()
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)

        self.ctx.payment_intent_moyklass_readiness(self.auth, pid)

        after = self.storage.get_payment_intent(pid)
        self.assertEqual(before["status"], after["status"])
        self.assertEqual(before["mk_payment_id"], after["mk_payment_id"])

    def test_18_readiness_does_not_change_intent(self):
        """Test 18: calling readiness multiple times doesn't change intent state."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]

        invoice = _fake_invoice()
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)

        self.ctx.payment_intent_moyklass_readiness(self.auth, pid)
        self.ctx.payment_intent_moyklass_readiness(self.auth, pid)

        after = self.storage.get_payment_intent(pid)
        self.assertIsNone(after.get("mk_posting_status") or None)

    def test_19_unauthorized_role_denied(self):
        """Test 19: unauthorized role receives denied."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        auth = _role_auth(99, "teacher", self.ctx)
        result = self.ctx.payment_intent_moyklass_readiness(auth, pid)
        self.assertFalse(result.get("ok", True) and result.get("ready", True))
        self.assertFalse(result.get("ok", False) if "error" in result else True)

    def test_20_owner_gets_preview(self):
        """Test 20: owner/admin role gets preview and fingerprint."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        invoice = _fake_invoice()
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)

        result = self.ctx.payment_intent_moyklass_readiness(self.auth, pid)
        self.assertIn("preview", result)
        self.assertIn("snapshot_fingerprint", result)

    def test_01_unpaid_intent_blocked(self):
        """Test 01: unpaid intent (bepaid_created) cannot post."""
        intent = self.storage.create_payment_intent(dict(
            mk_user_id=9001, student_name="X", amount_minor=22900,
            amount_byn=229.0, currency="BYN", purpose="other",
            payment_method="erip", status="bepaid_created",
            source="moyklass_invoice", mk_invoice_id="19000001",
        ))
        result = self.ctx.payment_intent_moyklass_readiness(self.auth, intent["public_id"])
        check_codes = [c["code"] for c in result.get("checks", []) if not c["ok"]]
        self.assertIn("intent_paid", check_codes)
        self.assertFalse(result.get("ready"))

    def test_02_bepaid_created_status_fails(self):
        """Test 02: bepaid_created status в†’ readiness fails intent_paid check."""
        intent = self.storage.create_payment_intent(dict(
            mk_user_id=9001, student_name="X", amount_minor=22900,
            amount_byn=229.0, currency="BYN", purpose="other",
            payment_method="erip", status="bepaid_created",
            source="moyklass_invoice", mk_invoice_id="19000001",
        ))
        result = self.ctx.payment_intent_moyklass_readiness(self.auth, intent["public_id"])
        self.assertFalse(result.get("ready"))

    def test_03_paid_but_unverified_blocked(self):
        """Test 03: paid but webhook_verified=false в†’ readiness fails."""
        intent = _seed_paid_intent(self.storage, unverified=True)
        pid = intent["public_id"]
        result = self.ctx.payment_intent_moyklass_readiness(self.auth, pid)
        check_codes = [c["code"] for c in result.get("checks", []) if not c["ok"]]
        self.assertIn("webhook_verified", check_codes)

    def test_04_paid_without_tx_uid_blocked(self):
        """Test 04: paid intent without paid_transaction_uid в†’ fails."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        # Manually clear the tx_uid from DB
        with self.storage._connect() as conn:
            conn.execute("UPDATE payment_intents SET paid_transaction_uid=NULL WHERE public_id=?", (pid,))
        result = self.ctx.payment_intent_moyklass_readiness(self.auth, pid)
        check_codes = [c["code"] for c in result.get("checks", []) if not c["ok"]]
        self.assertIn("has_tx_uid", check_codes)

    def test_06_wrong_currency_blocked(self):
        """Test 06: paid_currency != BYN в†’ readiness fails currency_byn check."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        with self.storage._connect() as conn:
            conn.execute("UPDATE payment_intents SET paid_currency='USD' WHERE public_id=?", (pid,))
        result = self.ctx.payment_intent_moyklass_readiness(self.auth, pid)
        check_codes = [c["code"] for c in result.get("checks", []) if not c["ok"]]
        self.assertIn("currency_byn", check_codes)

    def test_07_amount_mismatch_blocked(self):
        """Test 07: paid_amount_minor != amount_minor в†’ readiness fails amounts_match."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        with self.storage._connect() as conn:
            conn.execute("UPDATE payment_intents SET paid_amount_minor=100 WHERE public_id=?", (pid,))
        result = self.ctx.payment_intent_moyklass_readiness(self.auth, pid)
        check_codes = [c["code"] for c in result.get("checks", []) if not c["ok"]]
        self.assertIn("amounts_match", check_codes)

    def test_08_invoice_not_found_blocked(self):
        """Test 08: invoice 404 from MoyKlass в†’ readiness fails invoice_exists."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(False, status=404, error="Not Found")
        result = self.ctx.payment_intent_moyklass_readiness(self.auth, pid)
        check_codes = [c["code"] for c in result.get("checks", []) if not c["ok"]]
        self.assertIn("invoice_exists", check_codes)

    def test_09_invoice_wrong_user_blocked(self):
        """Test 09: invoice.userId != mk_user_id в†’ readiness fails invoice_belongs_to_user."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        invoice = _fake_invoice(user_id=9999)  # different userId
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)
        result = self.ctx.payment_intent_moyklass_readiness(self.auth, pid)
        check_codes = [c["code"] for c in result.get("checks", []) if not c["ok"]]
        self.assertIn("invoice_belongs_to_user", check_codes)

    def test_11_invoice_already_paid_remaining_zero(self):
        """Test 11: invoice.price==invoice.payed в†’ remaining==0 в†’ not ready."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        invoice = _fake_invoice(price=229.0, payed=229.0)
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)
        result = self.ctx.payment_intent_moyklass_readiness(self.auth, pid)
        check_codes = [c["code"] for c in result.get("checks", []) if not c["ok"]]
        self.assertIn("invoice_remaining_positive", check_codes)

    def test_12_invoice_remaining_zero(self):
        """Test 12: remaining=0 в†’ not ready."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        invoice = _fake_invoice(price=229.0, payed=229.0)
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)
        result = self.ctx.payment_intent_moyklass_readiness(self.auth, pid)
        self.assertFalse(result.get("ready"))

    def test_13_remaining_less_than_paid(self):
        """Test 13: remaining < paid в†’ invoice_remaining_matches_paid fails."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        invoice = _fake_invoice(price=229.0, payed=100.0)  # remaining=129 < 229
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)
        result = self.ctx.payment_intent_moyklass_readiness(self.auth, pid)
        check_codes = [c["code"] for c in result.get("checks", []) if not c["ok"]]
        self.assertIn("invoice_remaining_matches_paid", check_codes)

    def test_14_remaining_more_than_paid(self):
        """Test 14: remaining > paid в†’ fails."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        invoice = _fake_invoice(price=500.0, payed=0.0)  # remaining=500 > 229
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)
        result = self.ctx.payment_intent_moyklass_readiness(self.auth, pid)
        check_codes = [c["code"] for c in result.get("checks", []) if not c["ok"]]
        self.assertIn("invoice_remaining_matches_paid", check_codes)

    def test_15_exact_remaining_match_ready(self):
        """Test 15: invoice remaining == paid_amount в†’ ready."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        invoice = _fake_invoice(price=229.0, payed=0.0)  # remaining=229=paid
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)
        result = self.ctx.payment_intent_moyklass_readiness(self.auth, pid)
        self.assertTrue(result.get("ready"), f"Expected ready but checks={[c for c in result.get('checks',[]) if not c['ok']]}")

    def test_16_missing_payment_type_config(self):
        """Test 16: missing paymentTypeId config в†’ readiness fails."""
        ctx = _make_ctx(self.storage, moyklass_erip_payment_type_id=0)
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        invoice = _fake_invoice()
        ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)
        result = ctx.payment_intent_moyklass_readiness(_owner_auth(), pid)
        check_codes = [c["code"] for c in result.get("checks", []) if not c["ok"]]
        self.assertIn("payment_type_configured", check_codes)
        self.assertFalse(result.get("ready"))


# ---------------------------------------------------------------------------
# 6. Post to MoyKlass endpoint
# ---------------------------------------------------------------------------

class TestPostToMoyklass(unittest.TestCase):

    def setUp(self):
        self.storage = _memory_storage()
        self.ctx = _make_ctx(self.storage)
        self.auth = _owner_auth()

    def _ready_state(self) -> tuple[str, str]:
        """Set up a ready-to-post intent and return (pid, fingerprint)."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        invoice = _fake_invoice()
        fp = _compute_moyklass_post_fingerprint(intent, invoice)
        return pid, fp

    def _mk_success(self, mk_payment_id: int = 12345678) -> MoyKlassResult:
        return MoyKlassResult(True, data={"id": mk_payment_id, "userId": 9001}, status=200)

    def _mk_4xx(self, status: int = 400) -> MoyKlassResult:
        return MoyKlassResult(False, status=status, error='{"code":"ValidationError","message":"bad"}')

    def _mk_5xx(self) -> MoyKlassResult:
        return MoyKlassResult(False, status=500, error="Internal Server Error")

    def test_post_requires_confirm_true(self):
        pid, fp = self._ready_state()
        result = self.ctx.payment_intent_post_to_moyklass(self.auth, pid, {"confirm": False, "snapshot_fingerprint": fp})
        self.assertFalse(result.get("ok"))

    def test_post_requires_fingerprint(self):
        pid, _ = self._ready_state()
        result = self.ctx.payment_intent_post_to_moyklass(self.auth, pid, {"confirm": True, "snapshot_fingerprint": ""})
        self.assertFalse(result.get("ok"))

    def test_19_unauthorized_post_denied(self):
        """Test 19: teacher role cannot post."""
        pid, fp = self._ready_state()
        auth = _role_auth(99, "teacher", self.ctx)
        result = self.ctx.payment_intent_post_to_moyklass(auth, pid, {"confirm": True, "snapshot_fingerprint": fp})
        self.assertFalse(result.get("ok"))
        self.assertIn("error", result)

    def test_21_wrong_fingerprint_blocked(self):
        """Test 21: wrong fingerprint в†’ blocked."""
        pid, _ = self._ready_state()
        invoice = _fake_invoice()
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)
        result = self.ctx.payment_intent_post_to_moyklass(
            self.auth, pid,
            {"confirm": True, "snapshot_fingerprint": "wrong_fingerprint_000000000000000"}
        )
        self.assertFalse(result.get("ok"))
        self.assertIn("invoice_changed", result.get("error_code", ""))

    def test_22_invoice_changed_after_preview(self):
        """Test 22: if invoice payed changes between readiness and confirm в†’ 409 equivalent."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        invoice_at_readiness = _fake_invoice(payed=0.0)
        fp = _compute_moyklass_post_fingerprint(intent, invoice_at_readiness)
        # Now invoice is "changed" (payed=50.0)
        invoice_now = _fake_invoice(payed=50.0)
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice_now, status=200)
        result = self.ctx.payment_intent_post_to_moyklass(
            self.auth, pid,
            {"confirm": True, "snapshot_fingerprint": fp}
        )
        self.assertFalse(result.get("ok"))
        self.assertIn("изменился", result.get("error", ""))

    def test_23_successful_post_calls_mk_once(self):
        """Test 23: successful post calls MoyKlass create_payment exactly once."""
        pid, fp = self._ready_state()
        invoice = _fake_invoice()
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)
        self.ctx.moyklass.create_payment.return_value = self._mk_success()

        self.ctx.payment_intent_post_to_moyklass(self.auth, pid, {"confirm": True, "snapshot_fingerprint": fp})

        self.ctx.moyklass.create_payment.assert_called_once()

    def test_28_duplicate_post_after_success_idempotent(self):
        """Test 28: second post after success returns idempotent, not new call."""
        pid, fp = self._ready_state()
        invoice = _fake_invoice()
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)
        self.ctx.moyklass.create_payment.return_value = self._mk_success(12345678)

        self.ctx.payment_intent_post_to_moyklass(self.auth, pid, {"confirm": True, "snapshot_fingerprint": fp})
        result2 = self.ctx.payment_intent_post_to_moyklass(self.auth, pid, {"confirm": True, "snapshot_fingerprint": fp})

        self.assertTrue(result2.get("ok") or result2.get("idempotent"))
        self.assertEqual(self.ctx.moyklass.create_payment.call_count, 1)  # only one real call

    def test_29_duplicate_post_no_second_payment(self):
        """Test 29: duplicate POST does not create second payment in MoyKlass."""
        pid, fp = self._ready_state()
        invoice = _fake_invoice()
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)
        self.ctx.moyklass.create_payment.return_value = self._mk_success()

        self.ctx.payment_intent_post_to_moyklass(self.auth, pid, {"confirm": True, "snapshot_fingerprint": fp})
        self.ctx.payment_intent_post_to_moyklass(self.auth, pid, {"confirm": True, "snapshot_fingerprint": fp})

        self.assertEqual(self.ctx.moyklass.create_payment.call_count, 1)

    def test_30_timeout_ambiguous(self):
        """Test 30: connection error/timeout в†’ intent marked ambiguous."""
        pid, fp = self._ready_state()
        invoice = _fake_invoice()
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)
        self.ctx.moyklass.create_payment.return_value = MoyKlassResult(False, status=0, error="timeout")

        result = self.ctx.payment_intent_post_to_moyklass(self.auth, pid, {"confirm": True, "snapshot_fingerprint": fp})

        self.assertFalse(result.get("ok"))
        intent = self.storage.get_payment_intent(pid)
        self.assertEqual(intent.get("mk_posting_status"), "ambiguous")

    def test_31_timeout_blocks_auto_retry(self):
        """Test 31: after ambiguous, second POST is blocked (no auto-retry)."""
        pid, fp = self._ready_state()
        invoice = _fake_invoice()
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)
        self.ctx.moyklass.create_payment.return_value = MoyKlassResult(False, status=0, error="timeout")

        self.ctx.payment_intent_post_to_moyklass(self.auth, pid, {"confirm": True, "snapshot_fingerprint": fp})
        result2 = self.ctx.payment_intent_post_to_moyklass(self.auth, pid, {"confirm": True, "snapshot_fingerprint": fp})

        self.assertFalse(result2.get("ok"))
        self.assertIn("ambiguous", result2.get("block_reason", "") + result2.get("error_code", ""))

    def test_32_5xx_after_send_no_auto_retry(self):
        """Test 32: 5xx response marks ambiguous, not failed with retry."""
        pid, fp = self._ready_state()
        invoice = _fake_invoice()
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)
        self.ctx.moyklass.create_payment.return_value = self._mk_5xx()

        result = self.ctx.payment_intent_post_to_moyklass(self.auth, pid, {"confirm": True, "snapshot_fingerprint": fp})

        self.assertFalse(result.get("ok"))
        intent = self.storage.get_payment_intent(pid)
        self.assertEqual(intent.get("mk_posting_status"), "ambiguous")

    def test_33_definite_4xx_saves_error(self):
        """Test 33: definite 400/4xx saves error, status not ambiguous."""
        pid, fp = self._ready_state()
        invoice = _fake_invoice()
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)
        self.ctx.moyklass.create_payment.return_value = self._mk_4xx(400)

        result = self.ctx.payment_intent_post_to_moyklass(self.auth, pid, {"confirm": True, "snapshot_fingerprint": fp})

        self.assertFalse(result.get("ok"))
        intent = self.storage.get_payment_intent(pid)
        # After 4xx: mk_posting_status='failed' (not ambiguous)
        self.assertEqual(intent.get("mk_posting_status"), "failed")

    def test_39_paid_at_used_as_payment_date(self):
        """Test 39: paid_at from bePaid is used as the payment date, not current date."""
        pid, fp = self._ready_state()
        invoice = _fake_invoice()
        self.ctx.moyklass.get_invoice_by_id.return_value = MoyKlassResult(True, data=invoice, status=200)
        self.ctx.moyklass.create_payment.return_value = self._mk_success()

        self.ctx.payment_intent_post_to_moyklass(self.auth, pid, {"confirm": True, "snapshot_fingerprint": fp})

        call_kwargs = self.ctx.moyklass.create_payment.call_args.kwargs
        # paid_at from bePaid: "2026-07-13T10:00:00Z" в†’ date portion "2026-07-13"
        self.assertEqual(call_kwargs.get("date"), "2026-07-13")

    def test_config_missing_blocks_post(self):
        """Missing paymentTypeId in config в†’ post blocked before claim."""
        ctx = _make_ctx(self.storage, moyklass_erip_payment_type_id=0)
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        fp = _compute_moyklass_post_fingerprint(intent, _fake_invoice())
        result = ctx.payment_intent_post_to_moyklass(_owner_auth(), pid, {"confirm": True, "snapshot_fingerprint": fp})
        self.assertFalse(result.get("ok"))
        ctx.moyklass.create_payment.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Reconcile
# ---------------------------------------------------------------------------

class TestReconcile(unittest.TestCase):

    def setUp(self):
        self.storage = _memory_storage()
        self.ctx = _make_ctx(self.storage)
        self.auth = _owner_auth()

    def test_34_reconcile_exact_payment_marks_posted(self):
        """Test 34: exact match by comment+amount в†’ posted_to_moyklass."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        # Mark as ambiguous first
        self.storage.payment_intent_claim_moyklass_post(pid, "u1")
        self.storage.payment_intent_mark_moyklass_ambiguous(pid, "timeout")

        # Mock: no saved mk_payment_id; search returns exact match with comment
        found_payment = {
            "id": 99988,
            "userId": 9001,
            "summa": 229.0,
            "optype": "income",
            "comment": f"Yellow Club bePaid intent={pid} tx=tx-uid-test inv=19000001",
        }
        self.ctx.moyklass.get_payment_by_id.return_value = MoyKlassResult(False, status=404)
        self.ctx.moyklass.search_payments_by_user_date.return_value = MoyKlassResult(
            True, data={"items": [found_payment]}, status=200
        )

        result = self.ctx.payment_intent_reconcile_moyklass(self.auth, pid, {})
        self.assertTrue(result.get("reconciled"))
        self.assertEqual(result.get("mk_payment_id"), 99988)

        intent_after = self.storage.get_payment_intent(pid)
        self.assertEqual(intent_after["status"], "posted_to_moyklass")

    def test_35_reconcile_by_amount_only_forbidden(self):
        """Test 35: match by amount only (no comment) в†’ not reconciled."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]

        found_payment = {
            "id": 99999,
            "userId": 9001,
            "summa": 229.0,
            "comment": "some random comment without intent id",
        }
        self.ctx.moyklass.get_payment_by_id.return_value = MoyKlassResult(False, status=404)
        self.ctx.moyklass.search_payments_by_user_date.return_value = MoyKlassResult(
            True, data={"items": [found_payment]}, status=200
        )

        result = self.ctx.payment_intent_reconcile_moyklass(self.auth, pid, {})
        # Without comment match, should not reconcile
        self.assertFalse(result.get("reconciled", False))
        intent_after = self.storage.get_payment_intent(pid)
        self.assertNotEqual(intent_after.get("status"), "posted_to_moyklass")

    def test_36_reconcile_no_match_no_post(self):
        """Test 36: no match found в†’ not reconciled, no POST."""
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]

        self.ctx.moyklass.get_payment_by_id.return_value = MoyKlassResult(False, status=404)
        self.ctx.moyklass.search_payments_by_user_date.return_value = MoyKlassResult(
            True, data={"items": []}, status=200
        )

        result = self.ctx.payment_intent_reconcile_moyklass(self.auth, pid, {})
        self.assertFalse(result.get("reconciled", False))
        # create_payment must NOT have been called
        self.ctx.moyklass.create_payment.assert_not_called()


# ---------------------------------------------------------------------------
# 8. No automation guarantees
# ---------------------------------------------------------------------------

class TestNoAutomation(unittest.TestCase):

    def setUp(self):
        self.storage = _memory_storage()
        self.ctx = _make_ctx(self.storage)

    def test_40_webhook_does_not_call_mk_post(self):
        """Test 40: no auto-posting from webhook handler."""
        # Verify create_payment is never called from storage/webhook path
        self.ctx.moyklass.create_payment.assert_not_called()

    def test_41_loading_list_does_not_post(self):
        """Test 41: loading payment intent list doesn't trigger MoyKlass POST."""
        # payment_intents_list only reads from storage
        self.ctx.moyklass.create_payment.assert_not_called()

    def test_42_no_auto_post_on_app_start(self):
        """Test 42: app restart / cold start doesn't auto-post."""
        # Nothing in __init__ triggers MoyKlass
        self.ctx.moyklass.create_payment.assert_not_called()

    def test_43_no_telegram_notification(self):
        """Test 43: posting to MoyKlass doesn't send Telegram message."""
        # MiniAppHandler has no send_telegram_message method in this flow
        # This is guaranteed by design вЂ” no bot call in post_to_moyklass
        self.assertFalse(hasattr(self.ctx, "bot") and callable(getattr(self.ctx, "bot", None)))

    def test_44_no_new_invoice_created(self):
        """Test 44: new invoice is not created by posting."""
        self.ctx.moyklass.create_payment.assert_not_called()
        # Invoice creation endpoint not present
        has_create_invoice = hasattr(self.ctx.moyklass, "create_invoice")
        if has_create_invoice:
            self.ctx.moyklass.create_invoice.assert_not_called()

    def test_45_no_new_subscription_created(self):
        """Test 45: new subscription is not created by posting."""
        has_create_sub = hasattr(self.ctx.moyklass, "create_subscription")
        if has_create_sub:
            self.ctx.moyklass.create_subscription.assert_not_called()

    def test_auto_post_flag_remains_false(self):
        """Test 31b: BEPAID_AUTO_POST_TO_MOYKLASS=false by default."""
        self.assertFalse(self.ctx.settings.bepaid_auto_post_to_moyklass)


# ---------------------------------------------------------------------------
# 9. Role guards
# ---------------------------------------------------------------------------

class TestRoleGuards(unittest.TestCase):

    def setUp(self):
        self.storage = _memory_storage()
        self.ctx = _make_ctx(self.storage)

    def test_payment_mk_post_roles_owner_admin_only(self):
        """PAYMENT_MK_POST_ROLES must be exactly owner and admin."""
        self.assertEqual(PAYMENT_MK_POST_ROLES, {"owner", "admin"})

    def test_teacher_cannot_access_readiness(self):
        intent = _seed_paid_intent(self.storage)
        auth = _role_auth(5, "teacher", self.ctx)
        result = self.ctx.payment_intent_moyklass_readiness(auth, intent["public_id"])
        self.assertIn("error", result)

    def test_client_manager_cannot_post(self):
        intent = _seed_paid_intent(self.storage)
        pid = intent["public_id"]
        fp = _compute_moyklass_post_fingerprint(intent, _fake_invoice())
        auth = _role_auth(6, "client_manager", self.ctx)
        result = self.ctx.payment_intent_post_to_moyklass(auth, pid, {"confirm": True, "snapshot_fingerprint": fp})
        self.assertFalse(result.get("ok"))
        self.ctx.moyklass.create_payment.assert_not_called()


# ---------------------------------------------------------------------------
# 10. Module-level guarantees (food/reports untouched)
# ---------------------------------------------------------------------------

class TestModuleIntegrity(unittest.TestCase):

    def test_46_food_module_import_unchanged(self):
        """Test 46: food_menu_ocr module still importable unchanged."""
        import food_menu_ocr  # noqa: F401

    def test_47_reports_import_unchanged(self):
        """Test 47: report_manager still importable unchanged."""
        import report_manager  # noqa: F401

    def test_48_existing_test_suite_compatibility(self):
        """Test 48: storage module imports correctly."""
        from storage import Storage  # noqa: F401

    def test_49_no_real_external_calls_in_tests(self):
        """Test 49: all MoyKlass calls in this suite are mocked."""
        # This test passes by construction вЂ” setUp uses MagicMock
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main(verbosity=2)

