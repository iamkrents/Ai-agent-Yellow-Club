"""Tests for MoyKlass payment type discovery (v7.0.92.1).

Verifies:
- GET /api/payments/moyklass/payment-types role guards
- Response normalization
- ERIP candidate detection
- Configured ID validation states
- Integration with readiness endpoint
- No POST to MoyKlass, no .env modification
- All MoyKlass API calls mocked
"""
from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from moyklass_client import MoyKlassResult
from web_app_server import (
    MiniAppContext,
    _normalize_payment_type,
    _is_erip_candidate,
    _build_payment_type_readiness,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage, payment_type_id: int = 0, **settings_overrides) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(
        bepaid_auto_post_to_moyklass=False,
        moyklass_erip_payment_type_id=payment_type_id,
        **settings_overrides,
    )
    mk = MagicMock()
    mk.is_configured = True
    ctx.moyklass = mk
    ctx._role_store: dict[int, str] = {}

    def _role_for_user(uid: int) -> str:
        return ctx._role_store.get(uid, "owner")

    ctx._role_for_user = _role_for_user
    return ctx


def _auth(uid: int = 1) -> dict:
    return {"ok": True, "user_id": uid}


_FAKE_TYPES = [
    {"id": 1, "name": "Наличные"},
    {"id": 2, "name": "ЕРИП"},
    {"id": 3, "name": "Карта"},
    {"id": 4, "name": "Безналичный перевод"},
]


# ---------------------------------------------------------------------------
# 1. Role guards
# ---------------------------------------------------------------------------

class TestPaymentTypesRoleGuards(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def _set_role(self, uid: int, role: str) -> None:
        self.ctx._role_store[uid] = role

    def test_01_owner_can_access(self):
        """Test 1: payment types endpoint accessible by owner."""
        self._set_role(1, "owner")
        self.ctx.moyklass.get_payment_types.return_value = MoyKlassResult(
            True, data=_FAKE_TYPES, status=200
        )
        result = self.ctx.moyklass_payment_types(_auth(1))
        self.assertTrue(result.get("ok"), result)

    def test_02_admin_can_access(self):
        """Test 2: payment types endpoint accessible by admin."""
        self._set_role(2, "admin")
        self.ctx.moyklass.get_payment_types.return_value = MoyKlassResult(
            True, data=_FAKE_TYPES, status=200
        )
        result = self.ctx.moyklass_payment_types(_auth(2))
        self.assertTrue(result.get("ok"), result)

    def test_03_teacher_denied(self):
        """Test 3: teacher role cannot access payment types."""
        self._set_role(3, "teacher")
        result = self.ctx.moyklass_payment_types(_auth(3))
        self.assertFalse(result.get("ok"))

    def test_04_client_manager_denied(self):
        """Test 4: client_manager role cannot access payment types."""
        self._set_role(4, "client_manager")
        result = self.ctx.moyklass_payment_types(_auth(4))
        self.assertFalse(result.get("ok"))

    def test_05_director_denied(self):
        """Test 5: director role cannot access payment types."""
        self._set_role(5, "director")
        result = self.ctx.moyklass_payment_types(_auth(5))
        self.assertFalse(result.get("ok"))


# ---------------------------------------------------------------------------
# 2. Read-only: no POST
# ---------------------------------------------------------------------------

class TestPaymentTypesReadOnly(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        self.ctx.moyklass.get_payment_types.return_value = MoyKlassResult(
            True, data=_FAKE_TYPES, status=200
        )

    def test_06_endpoint_does_not_call_create_payment(self):
        """Test 6: endpoint is read-only — create_payment is never called."""
        self.ctx.moyklass_payment_types(_auth())
        self.ctx.moyklass.create_payment.assert_not_called()

    def test_07_endpoint_does_not_call_post(self):
        """Test 7: no raw_write or POST call is made."""
        self.ctx.moyklass_payment_types(_auth())
        if hasattr(self.ctx.moyklass, "raw_write"):
            self.ctx.moyklass.raw_write.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Normalization
# ---------------------------------------------------------------------------

class TestPaymentTypeNormalization(unittest.TestCase):

    def test_08_minimal_fields_id_and_name(self):
        """Test 8: normalization handles minimal schema (id + name only)."""
        raw = {"id": 5, "name": "Онлайн-оплата"}
        out = _normalize_payment_type(raw)
        self.assertEqual(out["id"], 5)
        self.assertEqual(out["name"], "Онлайн-оплата")
        self.assertTrue(out["active"])    # default True if field absent
        self.assertFalse(out["deleted"])  # default False if field absent

    def test_09_extra_fields_passed_through(self):
        """Test 9: optional fields (active, deleted, legalEntityId) preserved."""
        raw = {"id": 7, "name": "Test", "active": False, "deleted": True, "legalEntityId": 99}
        out = _normalize_payment_type(raw)
        self.assertFalse(out["active"])
        self.assertTrue(out["deleted"])
        self.assertEqual(out["legal_entity_id"], 99)

    def test_10_list_normalized_from_api(self):
        """Test 10: full API list is normalized correctly."""
        ctx = _make_ctx(_tmp_storage(), payment_type_id=2)
        ctx.moyklass.get_payment_types.return_value = MoyKlassResult(
            True, data=_FAKE_TYPES, status=200
        )
        result = ctx.moyklass_payment_types(_auth())
        self.assertTrue(result.get("ok"))
        items = result.get("items", [])
        self.assertEqual(len(items), 4)
        self.assertIn("id", items[0])
        self.assertIn("name", items[0])
        self.assertIn("active", items[0])


# ---------------------------------------------------------------------------
# 4. ERIP candidate detection
# ---------------------------------------------------------------------------

class TestEripCandidates(unittest.TestCase):

    def test_11_single_erip_candidate_highlighted(self):
        """Test 11: single ERIP candidate is identified but not auto-saved."""
        ctx = _make_ctx(_tmp_storage(), payment_type_id=0)
        # Only one ERIP keyword match: "ЕРИП" only (Безналичный also matches)
        single_types = [
            {"id": 1, "name": "Наличные"},
            {"id": 2, "name": "ЕРИП"},
            {"id": 3, "name": "Карта"},
        ]
        ctx.moyklass.get_payment_types.return_value = MoyKlassResult(
            True, data=single_types, status=200
        )
        result = ctx.moyklass_payment_types(_auth())
        diag = result.get("diagnostics", {})
        self.assertEqual(diag.get("possible_erip_matches"), 1)
        # Single candidate → env_hint provided, but never auto-selected
        self.assertFalse(diag.get("auto_select"))
        self.assertIsNotNone(diag.get("env_hint"))
        # manual_selection_required=False when exactly 1 candidate
        self.assertFalse(diag.get("manual_selection_required"))

    def test_12_multiple_candidates_not_auto_selected(self):
        """Test 12: multiple ERIP candidates → manual_selection_required=True."""
        types_multi = [
            {"id": 1, "name": "ЕРИП"},
            {"id": 2, "name": "bePaid ERIP"},
            {"id": 3, "name": "Наличные"},
        ]
        ctx = _make_ctx(_tmp_storage(), payment_type_id=0)
        ctx.moyklass.get_payment_types.return_value = MoyKlassResult(
            True, data=types_multi, status=200
        )
        result = ctx.moyklass_payment_types(_auth())
        diag = result.get("diagnostics", {})
        self.assertEqual(diag.get("possible_erip_matches"), 2)
        self.assertFalse(diag.get("auto_select"))
        self.assertTrue(diag.get("manual_selection_required"))

    def test_erip_keyword_detection(self):
        """_is_erip_candidate returns True for known ERIP keywords."""
        self.assertTrue(_is_erip_candidate("ЕРИП"))
        self.assertTrue(_is_erip_candidate("ERIP payment"))
        self.assertTrue(_is_erip_candidate("bePaid ЕРИП"))
        self.assertTrue(_is_erip_candidate("Безналичный перевод"))
        self.assertTrue(_is_erip_candidate("Онлайн-оплата"))
        self.assertFalse(_is_erip_candidate("Наличные"))
        self.assertFalse(_is_erip_candidate("Карта"))


# ---------------------------------------------------------------------------
# 5. Configured ID validation states
# ---------------------------------------------------------------------------

class TestConfiguredIdValidation(unittest.TestCase):

    def test_13_missing_config_valid_false(self):
        """Test 13: MOYKLASS_ERIP_PAYMENT_TYPE_ID=0 → valid=False."""
        readiness = _build_payment_type_readiness(0, None)
        self.assertFalse(readiness["configured"])
        self.assertFalse(readiness["valid"])
        self.assertIn("payment_type_not_configured", readiness["blocking_reasons"])

    def test_14_unknown_id_valid_false(self):
        """Test 14: configured ID not found in MoyKlass → valid=False."""
        readiness = _build_payment_type_readiness(999, None)
        self.assertTrue(readiness["configured"])
        self.assertFalse(readiness["valid"])
        self.assertIn("payment_type_not_found", readiness["blocking_reasons"])

    def test_15_inactive_type_valid_false(self):
        """Test 15: configured type has active=False → valid=False."""
        pt = {"id": 5, "name": "ЕРИП", "active": False, "deleted": False}
        readiness = _build_payment_type_readiness(5, _normalize_payment_type(pt))
        self.assertFalse(readiness["valid"])
        self.assertIn("payment_type_inactive", readiness["blocking_reasons"])

    def test_16_deleted_type_valid_false(self):
        """Test 16: configured type has deleted=True → valid=False."""
        pt = {"id": 5, "name": "ЕРИП", "active": True, "deleted": True}
        readiness = _build_payment_type_readiness(5, _normalize_payment_type(pt))
        self.assertFalse(readiness["valid"])
        self.assertIn("payment_type_deleted", readiness["blocking_reasons"])

    def test_17_valid_active_type_valid_true(self):
        """Test 17: configured type exists and active → valid=True."""
        pt = {"id": 2, "name": "ЕРИП"}  # no active/deleted → defaults True/False
        readiness = _build_payment_type_readiness(2, _normalize_payment_type(pt))
        self.assertTrue(readiness["configured"])
        self.assertTrue(readiness["valid"])
        self.assertEqual(readiness["blocking_reasons"], [])
        self.assertEqual(readiness["payment_type_name"], "ЕРИП")


# ---------------------------------------------------------------------------
# 6. Readiness endpoint integration + safety checks
# ---------------------------------------------------------------------------

class TestReadinessIntegration(unittest.TestCase):

    def setUp(self):
        self.storage = _tmp_storage()

    def test_18_readiness_false_for_new_intent(self):
        """Test 18: newly created intent (bepaid_created) → readiness=False."""
        intent = self.storage.create_payment_intent({
            "created_by_tg_id": 1,
            "created_by_name": "Test",
            "mk_user_id": 9001,
            "student_name": "Test",
            "amount_minor": 22900,
            "amount_byn": 229.0,
            "currency": "BYN",
            "payment_method": "erip",
            "period_month": "2026-07",
            "purpose": "current_month",
        })
        pid = intent["public_id"]
        ctx = _make_ctx(self.storage, payment_type_id=42)
        ctx.moyklass.get_payment_types.return_value = MoyKlassResult(
            True, data=[{"id": 42, "name": "ЕРИП"}], status=200
        )
        result = ctx.payment_intent_moyklass_readiness(_auth(), pid)
        self.assertFalse(result.get("ready"))

    def test_19_payment_posting_not_triggered(self):
        """Test 19: payment types endpoint never triggers create_payment."""
        ctx = _make_ctx(self.storage, payment_type_id=42)
        ctx.moyklass.get_payment_types.return_value = MoyKlassResult(
            True, data=[{"id": 42, "name": "ЕРИП"}], status=200
        )
        ctx.moyklass_payment_types(_auth())
        ctx.moyklass.create_payment.assert_not_called()

    def test_20_external_api_fully_mocked(self):
        """Test 20: all MoyKlass calls are mocked — no real HTTP requests."""
        ctx = _make_ctx(self.storage, payment_type_id=2)
        ctx.moyklass.get_payment_types.return_value = MoyKlassResult(
            True, data=_FAKE_TYPES, status=200
        )
        # The ctx.moyklass is a MagicMock — no real network calls happen.
        # Patch MoyKlassClient.request to ensure it's never used:
        with patch("moyklass_client.MoyKlassClient.request") as mock_req:
            result = ctx.moyklass_payment_types(_auth())
        mock_req.assert_not_called()
        self.assertTrue(result.get("ok"))


if __name__ == "__main__":
    unittest.main()
