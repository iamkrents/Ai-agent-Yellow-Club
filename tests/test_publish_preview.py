"""Regression tests for v7.0.93.2.3 — publish-preview ERIP readiness fix.

Root cause: publish-preview read ERIP account number only from payment_intent_options
rows, while the admin card reads from payment_intents.bepaid_account_number (the legacy
field set by payment_intent_save_bepaid_success).  For intents created before the
dual-channel option system (v7.0.92.2), no erip option row exists, so the preview
always returned erip_account=missing even when the ERIP invoice was fully created.

Covers:
  Static analysis (server code):
    1.  Fallback to legacy intent fields present in get_intent_publish_preview
    2.  Inactive option filter (cancelled/superseded/expired) present
    3.  Preview method does NOT call BePaidClient (no external bePaid calls)
    4.  Preview method does NOT call create_acquiring (no checkout creation)
    5.  Preview method does NOT call moyklass / MoyKlassClient (no MK calls)
    6.  parents field uses parent_telegram_user_id (correct field name)

  Storage scenarios (temp DB):
    7.  ERIP account in payment_intent_options row → erip_opt found with account
    8.  No erip option row but legacy pi.bepaid_account_number+bepaid_uid set → account reachable
    9.  Acquiring option with payment_url present in DB → acq_opt found with url
    10. Legacy ERIP field set, no options row → test helper confirms data model
    11. ERIP option with status=cancelled → not selected by active filter
    12. ERIP option with status=superseded → not selected by active filter

  Publish-preview endpoint logic (MiniAppContext with patched storage):
    13. Legacy erip intent (no option row) → erip_option_ready=True, ready=True
    14. Legacy erip intent, legacy field missing (bepaid_uid absent) → erip_option_ready=False
    15. Dual-channel fixture with account 9748998260715 → can_publish (erip ready, parent linked)
    16. Acquiring intent, acq option with payment_url → acquiring_option_ready=True

  Existing suite guard:
    17. Existing parent_payments tests still import and run (static check)
    18. Existing client_link tests still import (static check)
    19. Full suite version constant matches v7.0.93.2.3
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

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
SERVER_PY = ROOT / "web_app_server.py"

CURRENT_VERSION = "7.0.94.4"
NOW = "2026-07-16T10:00:00"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _seed_erip_intent(storage: Storage, *, mk_user_id: int = 5001,
                      bepaid_uid: str | None = "uid_test",
                      bepaid_account_number: str | None = "9748998260715") -> dict:
    pi = storage.create_payment_intent({
        "mk_user_id": mk_user_id,
        "student_name": "Тест Студент",
        "amount_minor": 5000,
        "amount_byn": 50.0,
        "currency": "BYN",
        "purpose": "current_month",
        "period_month": "2026-07",
        "payment_method": "erip",
        "created_by_tg_id": 1,
        "created_by_name": "admin",
        "comment": "",
        "source": "manual",
    })
    public_id = pi["public_id"]
    # Simulate legacy ERIP creation (saves to payment_intents row only)
    if bepaid_uid or bepaid_account_number:
        storage.payment_intent_update_status(public_id, "bepaid_creating")
        storage.payment_intent_save_bepaid_success(
            public_id,
            bepaid_uid=bepaid_uid or "uid_placeholder",
            bepaid_order_id="ord_test",
            bepaid_account_number=bepaid_account_number or "",
            bepaid_payment_url="",
            bepaid_status="pending",
        )
    return storage.get_payment_intent(public_id)


def _seed_parent_link(storage: Storage, mk_user_id: str, parent_telegram_id: str) -> None:
    result = storage.create_client_link_code(mk_user_id, "Тест Ученик", "test_admin")
    storage.link_client_child(parent_telegram_id, result["code"], NOW)


def _make_ctx(storage: Storage):
    """Build a minimal MiniAppContext-like object for testing get_intent_publish_preview."""
    import web_app_server as _srv

    ctx = _srv.MiniAppContext.__new__(_srv.MiniAppContext)
    ctx.storage = storage

    # Patch auth-related methods so tests don't need real staff users or settings
    ctx._require_payment_intent_access = lambda auth: None  # always allow
    ctx._role_for_user = lambda uid: "owner"
    return ctx


# ---------------------------------------------------------------------------
# Static analysis tests
# ---------------------------------------------------------------------------

class Test01StaticAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = SERVER_PY.read_text(encoding="utf-8")
        # Extract the get_intent_publish_preview method body
        start = cls.src.find("def get_intent_publish_preview(")
        end = cls.src.find("\n    def ", start + 1)
        cls.method = cls.src[start:end]

    def test_01_fallback_to_legacy_intent_fields(self):
        """Fix: fallback to pi.bepaid_account_number when no option row has it."""
        self.assertIn("bepaid_uid", self.method,
                      "must check pi.bepaid_uid as fallback signal")
        self.assertIn('pi.get("bepaid_account_number")', self.method,
                      "must read legacy bepaid_account_number from pi when no option")

    def test_02_inactive_option_filter_present(self):
        """Active options only: exclude cancelled, superseded, expired."""
        self.assertIn("cancelled", self.method)
        self.assertIn("superseded", self.method)
        self.assertIn("expired", self.method)

    def test_03_no_bepaid_client_call_in_preview(self):
        """Preview must not call bePaid — it is read-only."""
        self.assertNotIn("BePaidClient(", self.method)
        self.assertNotIn("create_bepaid", self.method)
        self.assertNotIn("bepaid.create", self.method)

    def test_04_no_create_acquiring_in_preview(self):
        """Preview must not create an acquiring checkout."""
        self.assertNotIn("create_acquiring", self.method)
        self.assertNotIn("create_payment_intent_option", self.method)

    def test_05_no_moyklass_call_in_preview(self):
        """Preview must not call MoyKlass API."""
        self.assertNotIn("self.moyklass", self.method)
        self.assertNotIn("MoyKlassClient", self.method)

    def test_06_parents_field_uses_correct_key(self):
        """parents response must use parent_telegram_user_id (client_parent_child_links field)."""
        self.assertIn("parent_telegram_user_id", self.method)
        # Old wrong key should not appear in the parents dict literal
        self.assertNotIn('"parent_telegram_id"', self.method)


# ---------------------------------------------------------------------------
# Storage scenario tests
# ---------------------------------------------------------------------------

class Test02StorageScenarios(unittest.TestCase):

    def test_07_erip_account_in_option_row(self):
        """payment_intent_options row with bepaid_account_number → found by get_options_for_intent."""
        storage = _tmp_storage()
        pi = _seed_erip_intent(storage, bepaid_uid=None, bepaid_account_number=None)
        # Create an erip option row with account number
        storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="erip",
            shop_type="erip",
            bepaid_account_number="9748998260715",
        )
        opts = storage.get_options_for_intent(pi["public_id"])
        erip_opt = next((o for o in opts if o.get("channel") == "erip"), None)
        self.assertIsNotNone(erip_opt)
        self.assertEqual(erip_opt.get("bepaid_account_number"), "9748998260715")

    def test_08_legacy_intent_no_option_row(self):
        """Legacy intent: bepaid_account_number on payment_intents row, no option row."""
        storage = _tmp_storage()
        pi = _seed_erip_intent(storage, bepaid_uid="uid_legacy", bepaid_account_number="9748998260715")
        opts = storage.get_options_for_intent(pi["public_id"])
        # No option row for erip
        erip_opt = next((o for o in opts if o.get("channel") == "erip"), None)
        self.assertIsNone(erip_opt)
        # But pi itself has the account number
        self.assertEqual(str(pi.get("bepaid_account_number") or ""), "9748998260715")
        self.assertEqual(str(pi.get("bepaid_uid") or ""), "uid_legacy")

    def test_09_acquiring_option_with_payment_url(self):
        """Acquiring option row with payment_url found by get_options_for_intent."""
        storage = _tmp_storage()
        pi = _seed_erip_intent(storage)
        storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="acquiring",
            shop_type="acquiring",
            payment_url="https://checkout.example.com/pay/abc123",
        )
        opts = storage.get_options_for_intent(pi["public_id"])
        acq_opt = next((o for o in opts if o.get("channel") == "acquiring"), None)
        self.assertIsNotNone(acq_opt)
        self.assertTrue(str(acq_opt.get("payment_url") or "").startswith("https://"))

    def test_10_legacy_erip_field_accessible(self):
        """Confirm get_payment_intent returns bepaid_account_number from the intents table."""
        storage = _tmp_storage()
        pi = _seed_erip_intent(storage, bepaid_uid="uid_x", bepaid_account_number="9748998260715")
        fetched = storage.get_payment_intent(pi["public_id"])
        self.assertEqual(fetched.get("bepaid_account_number"), "9748998260715")
        self.assertEqual(fetched.get("bepaid_uid"), "uid_x")

    def test_11_cancelled_option_excluded_by_active_filter(self):
        """An erip option with status=cancelled must not be selected as the active option."""
        storage = _tmp_storage()
        pi = _seed_erip_intent(storage, bepaid_uid=None, bepaid_account_number=None)
        storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="erip",
            shop_type="erip",
            bepaid_account_number="9748998260715",
        )
        # Cancel the option directly (cancel_options_for_cleanup only touches options)
        storage.cancel_options_for_cleanup(pi["public_id"], NOW)
        opts = storage.get_options_for_intent(pi["public_id"])
        _inactive = {"cancelled", "superseded", "expired"}
        erip_opt_active = next(
            (o for o in opts if o.get("channel") == "erip" and o.get("status") not in _inactive),
            None,
        )
        self.assertIsNone(erip_opt_active,
                          "cancelled erip option must not appear as active")

    def test_12_superseded_option_excluded_by_active_filter(self):
        """An erip option with status=superseded must not be selected as the active option."""
        storage = _tmp_storage()
        pi = _seed_erip_intent(storage, bepaid_uid=None, bepaid_account_number=None)
        # Create two erip options; supersede the first by marking siblings relative to the second
        opt1 = storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="erip",
            shop_type="erip",
            bepaid_account_number="9748998260715",
        )
        opt2 = storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="erip",
            shop_type="erip",
            bepaid_account_number="9748998260716",
        )
        # opt2 wins → opt1 is superseded
        storage.supersede_sibling_options(pi["public_id"], int(opt2["id"]))
        opts = storage.get_options_for_intent(pi["public_id"])
        _inactive = {"cancelled", "superseded", "expired"}
        # opt1 must be superseded — filter must exclude it
        opt1_row = next((o for o in opts if int(o["id"]) == int(opt1["id"])), None)
        self.assertIsNotNone(opt1_row)
        self.assertEqual(opt1_row["status"], "superseded")
        # Active filter should return only opt2
        erip_active = [o for o in opts if o.get("channel") == "erip" and o.get("status") not in _inactive]
        self.assertEqual(len(erip_active), 1)
        self.assertEqual(int(erip_active[0]["id"]), int(opt2["id"]))


# ---------------------------------------------------------------------------
# Endpoint logic tests (MiniAppContext with patched storage)
# ---------------------------------------------------------------------------

class Test03EndpointLogic(unittest.TestCase):

    def _auth(self):
        return {"user_id": 1}

    def test_13_legacy_erip_intent_no_option_row_is_ready(self):
        """Legacy intent: bepaid_uid + bepaid_account_number on pi row → erip_option_ready=True."""
        storage = _tmp_storage()
        pi = _seed_erip_intent(storage, bepaid_uid="uid_legacy", bepaid_account_number="9748998260715")
        _seed_parent_link(storage, str(pi["mk_user_id"]), "tg_parent_test")

        ctx = _make_ctx(storage)
        result = ctx.get_intent_publish_preview(self._auth(), pi["public_id"])

        self.assertTrue(result.get("ok"), result)
        erip_check = next((c for c in result["checks"] if c["name"] == "erip_option_ready"), None)
        self.assertIsNotNone(erip_check, "erip_option_ready check must be present")
        self.assertTrue(erip_check["ok"],
                        f"erip_option_ready must be True for legacy intent with account set; "
                        f"detail={erip_check.get('detail')}")

    def test_14_legacy_erip_no_uid_is_blocked(self):
        """Intent with no bepaid_uid (ERIP not created) → erip_option_ready=False."""
        storage = _tmp_storage()
        # Create intent without ERIP creation (draft state)
        pi_raw = storage.create_payment_intent({
            "mk_user_id": 5002,
            "student_name": "Другой Студент",
            "amount_minor": 3000,
            "amount_byn": 30.0,
            "currency": "BYN",
            "purpose": "current_month",
            "period_month": "2026-07",
            "payment_method": "erip",
            "created_by_tg_id": 1,
            "created_by_name": "admin",
            "comment": "",
            "source": "manual",
        })
        _seed_parent_link(storage, "5002", "tg_parent_other")

        ctx = _make_ctx(storage)
        result = ctx.get_intent_publish_preview(self._auth(), pi_raw["public_id"])

        self.assertTrue(result.get("ok"), result)
        erip_check = next((c for c in result["checks"] if c["name"] == "erip_option_ready"), None)
        self.assertIsNotNone(erip_check)
        self.assertFalse(erip_check["ok"],
                         "erip_option_ready must be False when no ERIP created")

    def test_15_dual_channel_9748998260715_fixture(self):
        """Reproduces ycpi_202607_15: legacy ERIP + acquiring option → erip ready, can_publish."""
        storage = _tmp_storage()
        pi = _seed_erip_intent(storage, bepaid_uid="uid_real",
                               bepaid_account_number="9748998260715")
        # Add acquiring option (as prepare_options would have created it)
        storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="acquiring",
            shop_type="acquiring",
            payment_url="https://checkout.bepaid.by/pay/abc",
        )
        _seed_parent_link(storage, str(pi["mk_user_id"]), "tg_parent_real")

        ctx = _make_ctx(storage)
        result = ctx.get_intent_publish_preview(self._auth(), pi["public_id"])

        self.assertTrue(result.get("ok"), result)
        # ERIP must be ready
        erip_check = next((c for c in result["checks"] if c["name"] == "erip_option_ready"), None)
        self.assertIsNotNone(erip_check)
        self.assertTrue(erip_check["ok"],
                        f"erip_option_ready must be True for 9748998260715; "
                        f"detail={erip_check.get('detail')}")
        # Parent must be found
        parent_check = next((c for c in result["checks"] if c["name"] == "has_parent_link"), None)
        self.assertIsNotNone(parent_check)
        self.assertTrue(parent_check["ok"])
        # Overall: ready to publish
        blocking_checks = [c for c in result["checks"] if not c["ok"] and c.get("blocker", True)]
        self.assertEqual(blocking_checks, [],
                         f"No blockers expected; got: {blocking_checks}")
        self.assertTrue(result.get("ready"),
                        "dual-channel intent with both options must be ready to publish")

    def test_16_acquiring_intent_option_url_ready(self):
        """Acquiring payment_method with payment_url in option → acquiring_option_ready=True."""
        storage = _tmp_storage()
        pi_raw = storage.create_payment_intent({
            "mk_user_id": 5003,
            "student_name": "Acq Студент",
            "amount_minor": 4000,
            "amount_byn": 40.0,
            "currency": "BYN",
            "purpose": "current_month",
            "period_month": "2026-07",
            "payment_method": "acquiring",
            "created_by_tg_id": 1,
            "created_by_name": "admin",
            "comment": "",
            "source": "manual",
        })
        public_id = pi_raw["public_id"]
        storage.create_payment_intent_option(
            payment_intent_id=int(pi_raw["id"]),
            intent_public_id=public_id,
            channel="acquiring",
            shop_type="acquiring",
            payment_url="https://checkout.bepaid.by/pay/xyz",
        )
        _seed_parent_link(storage, "5003", "tg_parent_acq")

        ctx = _make_ctx(storage)
        result = ctx.get_intent_publish_preview(self._auth(), public_id)

        self.assertTrue(result.get("ok"), result)
        acq_check = next((c for c in result["checks"] if c["name"] == "acquiring_option_ready"), None)
        self.assertIsNotNone(acq_check, "acquiring_option_ready check must be present")
        self.assertTrue(acq_check["ok"],
                        f"acquiring_option_ready must be True; detail={acq_check.get('detail')}")


# ---------------------------------------------------------------------------
# Guard: existing suites still importable
# ---------------------------------------------------------------------------

class Test04ExistingGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_17_parent_payments_module_importable(self):
        import tests.test_parent_payments  # noqa: F401

    def test_18_client_link_module_importable(self):
        import tests.test_client_parent_links  # noqa: F401

    def test_19_version_constant_is_v7_0_93_2_2(self):
        self.assertIn(f'console.log("MiniApp version: v{CURRENT_VERSION}")', self.js,
                      f"app.js version marker must be v{CURRENT_VERSION}")
        self.assertIn(f"v={CURRENT_VERSION}", self.html,
                      f"index.html cache-bust must be v={CURRENT_VERSION}")


if __name__ == "__main__":
    unittest.main()
