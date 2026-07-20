"""Tests for MoyKlass posting readiness — v7.0.96.1.

Covers:
- moyklass_invoice_automation source passes readiness (was blocked before v7.0.96.1)
- MOYKLASS_INVOICE_INTENT_SOURCES constant
- source_reference_valid check for automation intents
- Payment type routing: acquiring → 111861, ERIP → 55948
- Security guards: manual/manual_input sources, missing fields, auto-post OFF
- URL correctness
- Production fixture: ycpi_202607_19

Run:
    python -m unittest tests.test_mk_post_readiness -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from moyklass_client import MoyKlassResult
from web_app_server import (
    MOYKLASS_INVOICE_INTENT_SOURCES,
    MiniAppContext,
    _REQUIRED_ACQUIRING_TYPE_NAME,
    _REQUIRED_ERIP_TYPE_NAME,
)

CURRENT_VERSION = "7.0.98.1"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mem_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(
    storage: Storage,
    *,
    auto_post: bool = False,
    erip_type_id: int = 55948,
    acq_type_id: int = 111861,
    mk_configured: bool = False,
    mock_payment_type_name: str = "BePaid эквайринг",
    mock_invoice: dict | None = None,
) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(
        bepaid_auto_post_to_moyklass=auto_post,
        moyklass_erip_payment_type_id=erip_type_id,
        moyklass_acquiring_payment_type_id=acq_type_id,
    )
    mk = MagicMock()
    mk.is_configured = mk_configured
    mk.get_payment_type_by_id.return_value = MoyKlassResult(
        True,
        data={"id": acq_type_id, "name": mock_payment_type_name, "active": True, "deleted": False},
        status=200,
    )
    if mock_invoice is not None:
        mk.get_invoice_by_id.return_value = MoyKlassResult(
            True, data=mock_invoice, status=200
        )
    else:
        mk.get_invoice_by_id.return_value = MoyKlassResult(
            False, data={}, status=404, error="not found"
        )
    ctx.moyklass = mk

    def _role_for_user(uid: int) -> str:
        return "owner"

    ctx._role_for_user = _role_for_user
    return ctx


def _owner_auth() -> dict:
    return {"ok": True, "user_id": 1}


def _create_and_pay(
    storage: Storage,
    *,
    source: str = "moyklass_invoice_automation",
    source_reference: str = "mk_invoice_1",
    mk_invoice_id: str = "1",
    mk_user_id: int = 9001,
    amount_minor: int = 100,
    payment_method: str = "acquiring",
    paid_channel: str = "acquiring",
    tx_uid: str = "tx-test-0001",
) -> str:
    """Create an intent and mark it paid. Returns public_id."""
    row = storage.create_payment_intent({
        "mk_user_id": mk_user_id,
        "student_name": "Test Student",
        "amount_minor": amount_minor,
        "amount_byn": amount_minor / 100,
        "currency": "BYN",
        "purpose": "current_month",
        "payment_method": payment_method,
        "status": "bepaid_created",
        "source": source,
        "source_reference": source_reference,
        "mk_invoice_id": mk_invoice_id,
    })
    pid = row["public_id"]
    storage.payment_intent_mark_paid(
        pid,
        tx_uid=tx_uid,
        amount_minor=amount_minor,
        currency="BYN",
        paid_at="2026-07-18T12:00:00Z",
        channel=paid_channel,
        verified=True,
        match_method="tracking_id",
    )
    return pid


def _get_check(result: dict, code: str) -> dict | None:
    for c in result.get("checks", []):
        if c["code"] == code:
            return c
    return None


def _run_readiness(
    storage: Storage,
    ctx: MiniAppContext,
    pid: str,
) -> dict:
    return ctx.payment_intent_moyklass_readiness(_owner_auth(), pid)


# ---------------------------------------------------------------------------
# 1. MOYKLASS_INVOICE_INTENT_SOURCES constant
# ---------------------------------------------------------------------------

class TestSourcesConstant(unittest.TestCase):

    def test_01_automation_source_in_set(self):
        """moyklass_invoice_automation must be in MOYKLASS_INVOICE_INTENT_SOURCES."""
        self.assertIn("moyklass_invoice_automation", MOYKLASS_INVOICE_INTENT_SOURCES)

    def test_02_legacy_source_in_set(self):
        """moyklass_invoice must remain in MOYKLASS_INVOICE_INTENT_SOURCES."""
        self.assertIn("moyklass_invoice", MOYKLASS_INVOICE_INTENT_SOURCES)

    def test_03_manual_not_in_set(self):
        """manual source must NOT be in MOYKLASS_INVOICE_INTENT_SOURCES."""
        self.assertNotIn("manual", MOYKLASS_INVOICE_INTENT_SOURCES)

    def test_04_manual_input_not_in_set(self):
        """manual_input must NOT be in MOYKLASS_INVOICE_INTENT_SOURCES."""
        self.assertNotIn("manual_input", MOYKLASS_INVOICE_INTENT_SOURCES)

    def test_05_constant_is_frozenset(self):
        """Constant must be a frozenset (immutable)."""
        self.assertIsInstance(MOYKLASS_INVOICE_INTENT_SOURCES, frozenset)


# ---------------------------------------------------------------------------
# 2. Source check in readiness
# ---------------------------------------------------------------------------

class TestSourceReadinessCheck(unittest.TestCase):

    def _source_check_ok(self, source: str, source_reference: str = "mk_invoice_1", mk_invoice_id: str = "1") -> bool:
        storage = _mem_storage()
        ctx = _make_ctx(storage, mk_configured=False)
        pid = _create_and_pay(
            storage,
            source=source,
            source_reference=source_reference,
            mk_invoice_id=mk_invoice_id,
        )
        result = _run_readiness(storage, ctx, pid)
        chk = _get_check(result, "source_is_invoice")
        return bool(chk and chk["ok"])

    def test_06_automation_source_passes(self):
        """source=moyklass_invoice_automation must pass source_is_invoice check."""
        self.assertTrue(
            self._source_check_ok("moyklass_invoice_automation"),
            "moyklass_invoice_automation must pass source_is_invoice",
        )

    def test_07_legacy_source_passes(self):
        """source=moyklass_invoice must still pass source_is_invoice check."""
        self.assertTrue(
            self._source_check_ok("moyklass_invoice"),
            "moyklass_invoice must still pass source_is_invoice",
        )

    def test_08_manual_source_fails(self):
        """source=manual must NOT pass source readiness — even with mk_invoice_id."""
        self.assertFalse(
            self._source_check_ok("manual"),
            "manual source must be rejected",
        )

    def test_09_manual_input_source_fails(self):
        """source=manual_input must NOT pass source readiness."""
        self.assertFalse(
            self._source_check_ok("manual_input"),
            "manual_input source must be rejected",
        )


# ---------------------------------------------------------------------------
# 3. source_reference_valid check (automation-specific)
# ---------------------------------------------------------------------------

class TestSourceReferenceValidation(unittest.TestCase):

    def _ref_check(self, mk_invoice_id: str, source_reference: str) -> bool | None:
        storage = _mem_storage()
        ctx = _make_ctx(storage, mk_configured=False)
        pid = _create_and_pay(
            storage,
            source="moyklass_invoice_automation",
            source_reference=source_reference,
            mk_invoice_id=mk_invoice_id,
        )
        result = _run_readiness(storage, ctx, pid)
        chk = _get_check(result, "source_reference_valid")
        return chk["ok"] if chk else None

    def test_10_correct_source_reference_passes(self):
        """source_reference=mk_invoice_19102120 with mk_invoice_id=19102120 passes."""
        ok = self._ref_check("19102120", "mk_invoice_19102120")
        self.assertIsNotNone(ok, "source_reference_valid check must be present for automation intents")
        self.assertTrue(ok)

    def test_11_mismatched_source_reference_fails(self):
        """source_reference that doesn't match mk_invoice_id must fail."""
        ok = self._ref_check("19102120", "mk_invoice_99999999")
        self.assertIsNotNone(ok)
        self.assertFalse(ok)

    def test_12_missing_source_reference_fails(self):
        """Empty source_reference for automation intent must fail."""
        storage = _mem_storage()
        ctx = _make_ctx(storage, mk_configured=False)
        row = storage.create_payment_intent({
            "mk_user_id": 9001,
            "student_name": "Test",
            "amount_minor": 100,
            "amount_byn": 1.0,
            "currency": "BYN",
            "purpose": "current_month",
            "payment_method": "acquiring",
            "status": "bepaid_created",
            "source": "moyklass_invoice_automation",
            "mk_invoice_id": "19102120",
        })
        pid = row["public_id"]
        storage.payment_intent_mark_paid(
            pid, tx_uid="tx-ref-empty", amount_minor=100, currency="BYN",
            paid_at="2026-07-18T10:00:00Z", channel="acquiring",
            verified=True, match_method="tracking_id",
        )
        result = _run_readiness(storage, ctx, pid)
        chk = _get_check(result, "source_reference_valid")
        self.assertIsNotNone(chk)
        self.assertFalse(chk["ok"])

    def test_13_missing_mk_invoice_id_fails(self):
        """Automation intent without mk_invoice_id must fail source_reference_valid."""
        ok = self._ref_check("", "mk_invoice_")
        self.assertIsNotNone(ok)
        self.assertFalse(ok)

    def test_14_legacy_source_has_no_source_reference_check(self):
        """source=moyklass_invoice must NOT have source_reference_valid check."""
        storage = _mem_storage()
        ctx = _make_ctx(storage, mk_configured=False)
        pid = _create_and_pay(
            storage,
            source="moyklass_invoice",
            source_reference="mk_invoice_12345",
            mk_invoice_id="12345",
            payment_method="erip",
            paid_channel="erip",
        )
        result = _run_readiness(storage, ctx, pid)
        chk = _get_check(result, "source_reference_valid")
        self.assertIsNone(chk, "source_reference_valid must not appear for moyklass_invoice source")


# ---------------------------------------------------------------------------
# 4. Payment type routing
# ---------------------------------------------------------------------------

class TestPaymentTypeRouting(unittest.TestCase):

    def test_15_acquiring_uses_111861(self):
        """Acquiring channel must use payment type ID 111861 (BePaid эквайринг)."""
        storage = _mem_storage()
        ctx = _make_ctx(storage, mk_configured=False, erip_type_id=55948, acq_type_id=111861)
        pid = _create_and_pay(
            storage,
            source="moyklass_invoice_automation",
            source_reference="mk_invoice_1",
            mk_invoice_id="1",
            paid_channel="acquiring",
        )
        result = _run_readiness(storage, ctx, pid)
        self.assertEqual(result.get("preview", {}).get("payment_type_id"), 111861)

    def test_16_erip_uses_55948(self):
        """ERIP channel must use payment type ID 55948 (BePaid ЕРИП)."""
        storage = _mem_storage()
        ctx = _make_ctx(storage, mk_configured=False, erip_type_id=55948, acq_type_id=111861)
        pid = _create_and_pay(
            storage,
            source="moyklass_invoice",
            source_reference="mk_invoice_1",
            mk_invoice_id="1",
            payment_method="erip",
            paid_channel="erip",
        )
        result = _run_readiness(storage, ctx, pid)
        self.assertEqual(result.get("preview", {}).get("payment_type_id"), 55948)

    def test_17_acquiring_required_name(self):
        """_REQUIRED_ACQUIRING_TYPE_NAME must be 'BePaid эквайринг'."""
        self.assertEqual(_REQUIRED_ACQUIRING_TYPE_NAME, "BePaid эквайринг")

    def test_18_erip_required_name(self):
        """_REQUIRED_ERIP_TYPE_NAME must be 'BePaid ЕРИП'."""
        self.assertEqual(_REQUIRED_ERIP_TYPE_NAME, "BePaid ЕРИП")


# ---------------------------------------------------------------------------
# 5. Auto-post stays OFF; readiness is read-only
# ---------------------------------------------------------------------------

class TestAutoPostAndReadOnly(unittest.TestCase):

    def test_19_auto_post_check_passes_when_disabled(self):
        """v7.0.96.1: auto_post_disabled check removed; manual posting unaffected when flag=False."""
        storage = _mem_storage()
        ctx = _make_ctx(storage, auto_post=False, mk_configured=False)
        pid = _create_and_pay(
            storage,
            source="moyklass_invoice_automation",
            source_reference="mk_invoice_1",
            mk_invoice_id="1",
        )
        result = _run_readiness(storage, ctx, pid)
        chk = _get_check(result, "auto_post_disabled")
        self.assertIsNone(chk, "auto_post_disabled check was removed in v7.0.96.1 (now informational warning)")
        self.assertFalse(
            any("auto_post" in c["code"] for c in result.get("checks", [])),
            "No auto_post-related check must block manual posting",
        )

    def test_20_readiness_does_not_call_create_payment(self):
        """Readiness endpoint must not call moyklass.create_payment."""
        storage = _mem_storage()
        ctx = _make_ctx(storage, mk_configured=False)
        pid = _create_and_pay(
            storage,
            source="moyklass_invoice_automation",
            source_reference="mk_invoice_1",
            mk_invoice_id="1",
        )
        _run_readiness(storage, ctx, pid)
        ctx.moyklass.create_payment.assert_not_called()

    def test_21_posting_requires_confirm_true(self):
        """post-to-moyklass must require confirm=true in body."""
        storage = _mem_storage()
        ctx = _make_ctx(storage, mk_configured=False)
        pid = _create_and_pay(
            storage,
            source="moyklass_invoice_automation",
            source_reference="mk_invoice_1",
            mk_invoice_id="1",
        )
        result = ctx.payment_intent_post_to_moyklass(_owner_auth(), pid, {})
        self.assertFalse(result.get("ok"))
        self.assertIn("confirm", result.get("error", "").lower())

    def test_22_posting_requires_fingerprint(self):
        """post-to-moyklass must require snapshot_fingerprint."""
        storage = _mem_storage()
        ctx = _make_ctx(storage, mk_configured=False)
        pid = _create_and_pay(
            storage,
            source="moyklass_invoice_automation",
            source_reference="mk_invoice_1",
            mk_invoice_id="1",
        )
        result = ctx.payment_intent_post_to_moyklass(_owner_auth(), pid, {"confirm": True})
        self.assertFalse(result.get("ok"))
        self.assertIn("fingerprint", result.get("error", "").lower())


# ---------------------------------------------------------------------------
# 6. URL correctness
# ---------------------------------------------------------------------------

class TestURLCorrectness(unittest.TestCase):

    def test_23_readiness_url_has_slash(self):
        """Frontend must call /api/payments/intents/{id}/moyklass-post-readiness with slash."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/moyklass-post-readiness`", js)
        self.assertNotIn(
            "${encodeURIComponent(publicId)}moyklass-post-readiness",
            js,
            "publicId and moyklass-post-readiness must be separated by /",
        )

    def test_24_posting_url_has_slash(self):
        """Frontend must call /api/payments/intents/{id}/post-to-moyklass with slash."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/post-to-moyklass`", js)
        self.assertNotIn(
            "${encodeURIComponent(publicId)}post-to-moyklass",
            js,
            "publicId and post-to-moyklass must be separated by /",
        )

    def test_25_version_is_7_0_94_6(self):
        """app.js must log version v7.0.96.1."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("v7.0.98.1", js)

    def test_26_cache_bust_is_7_0_94_6(self):
        """index.html cache-bust must be v=7.0.96.1."""
        html = (ROOT / "miniapp" / "index.html").read_bytes().decode("utf-8-sig")
        self.assertIn("v=7.0.98.1", html)


# ---------------------------------------------------------------------------
# 7. Production fixture: ycpi_202607_19
# ---------------------------------------------------------------------------

class TestProductionFixture(unittest.TestCase):
    """Fixture mirrors the real production intent ycpi_202607_19."""

    def setUp(self):
        self.storage = _mem_storage()
        mock_invoice = {
            "id": 19102120,
            "userId": 9748998,
            "price": 1.0,
            "payed": 0.0,
            "payUntil": "2026-07-31",
            "userSubscriptionId": None,
            "date": "2026-07-01",
            "createdAt": "2026-07-01",
        }
        self.ctx = _make_ctx(
            self.storage,
            auto_post=False,
            erip_type_id=55948,
            acq_type_id=111861,
            mk_configured=True,
            mock_payment_type_name="BePaid эквайринг",
            mock_invoice=mock_invoice,
        )
        row = self.storage.create_payment_intent({
            "mk_user_id": 9748998,
            "student_name": "Test Student",
            "amount_minor": 100,
            "amount_byn": 1.0,
            "currency": "BYN",
            "purpose": "current_month",
            "payment_method": "acquiring",
            "status": "bepaid_created",
            "source": "moyklass_invoice_automation",
            "source_reference": "mk_invoice_19102120",
            "mk_invoice_id": "19102120",
        })
        self.pid = row["public_id"]
        self.storage.payment_intent_mark_paid(
            self.pid,
            tx_uid="20e9ee15-878e-41b6-b066-f47f31eb9ad0",
            amount_minor=100,
            currency="BYN",
            paid_at="2026-07-18T12:00:00Z",
            channel="acquiring",
            tracking_id=self.pid,
            order_id="100000000019",
            account_number="",
            verified=True,
            match_method="tracking_id",
        )

    def test_27_fixture_source_check_passes(self):
        """ycpi_202607_19 fixture: source_is_invoice must pass."""
        result = _run_readiness(self.storage, self.ctx, self.pid)
        chk = _get_check(result, "source_is_invoice")
        self.assertIsNotNone(chk)
        self.assertTrue(chk["ok"], f"source_is_invoice failed: {chk}")

    def test_28_fixture_source_reference_check_passes(self):
        """ycpi_202607_19 fixture: source_reference_valid must pass."""
        result = _run_readiness(self.storage, self.ctx, self.pid)
        chk = _get_check(result, "source_reference_valid")
        self.assertIsNotNone(chk)
        self.assertTrue(chk["ok"], f"source_reference_valid failed: {chk}")

    def test_29_fixture_fully_ready(self):
        """ycpi_202607_19 fixture: all checks pass, ready=True."""
        result = _run_readiness(self.storage, self.ctx, self.pid)
        failed = [c for c in result.get("checks", []) if not c["ok"]]
        self.assertTrue(result.get("ready"), f"Not ready. Failed checks: {failed}")

    def test_30_fixture_payment_type_111861(self):
        """ycpi_202607_19 fixture: preview must show payment_type_id=111861."""
        result = _run_readiness(self.storage, self.ctx, self.pid)
        self.assertEqual(result.get("preview", {}).get("payment_type_id"), 111861)

    def test_31_fixture_no_autopost(self):
        """ycpi_202607_19 fixture: v7.0.96.1 removed auto_post_disabled check."""
        result = _run_readiness(self.storage, self.ctx, self.pid)
        chk = _get_check(result, "auto_post_disabled")
        self.assertIsNone(chk, "auto_post_disabled check was removed in v7.0.96.1 (now a warning)")

    def test_32_fixture_moyklass_not_called_during_readiness_only(self):
        """Readiness must not call moyklass.create_payment on the fixture."""
        _run_readiness(self.storage, self.ctx, self.pid)
        self.ctx.moyklass.create_payment.assert_not_called()


# ---------------------------------------------------------------------------
# 8. Regression: existing manual (moyklass_invoice) intents not broken
# ---------------------------------------------------------------------------

class TestLegacyIntentNotBroken(unittest.TestCase):

    def test_33_legacy_moyklass_invoice_still_passes_source_check(self):
        """source=moyklass_invoice intents must continue to pass source_is_invoice."""
        storage = _mem_storage()
        ctx = _make_ctx(storage, mk_configured=False)
        pid = _create_and_pay(
            storage,
            source="moyklass_invoice",
            source_reference="mk_invoice_19000001",
            mk_invoice_id="19000001",
            payment_method="erip",
            paid_channel="erip",
            amount_minor=22900,
        )
        result = _run_readiness(storage, ctx, pid)
        chk = _get_check(result, "source_is_invoice")
        self.assertIsNotNone(chk)
        self.assertTrue(chk["ok"])

    def test_34_constant_exported_from_web_app_server(self):
        """MOYKLASS_INVOICE_INTENT_SOURCES must be importable from web_app_server."""
        import web_app_server
        self.assertTrue(hasattr(web_app_server, "MOYKLASS_INVOICE_INTENT_SOURCES"))

    def test_35_source_is_invoice_check_detail_shows_source(self):
        """source_is_invoice check detail must include the actual source value."""
        storage = _mem_storage()
        ctx = _make_ctx(storage, mk_configured=False)
        pid = _create_and_pay(
            storage,
            source="moyklass_invoice_automation",
            source_reference="mk_invoice_1",
            mk_invoice_id="1",
        )
        result = _run_readiness(storage, ctx, pid)
        chk = _get_check(result, "source_is_invoice")
        self.assertIsNotNone(chk)
        self.assertIn("moyklass_invoice_automation", chk.get("detail", ""))


if __name__ == "__main__":
    unittest.main()
