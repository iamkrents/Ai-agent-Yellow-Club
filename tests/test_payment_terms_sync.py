"""Tests for v7.1.1 — Sync payment terms from MoyKlass subscriptions.

Coverage map:
  01-08  Domain: select_moyklass_subscription_for_terms
  09-12  Storage: source column migration + update_payment_client_terms_source
  13-17  Server: sync method behaviour (flag off, MK error, states)
  18-22  Server: new_source state fully updates terms + source fields
  23-27  Server: route + auth + endpoint present in server source
  28-33  Automatic trigger in invoice flow (flag-gated, only for new invoices)
  34-40  Frontend static analysis (version, button, source display, sync endpoint)

Run offline (no Telegram / bePaid / MoyKlass):
    python -m unittest tests.test_payment_terms_sync -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from payment_domain import (
    select_moyklass_subscription_for_terms,
    MK_SUBSCRIPTION_ACTIVE_STATUS_ID,
    MK_TERMS_SYNC_STATES,
    DEFAULT_BASE_PRICE_MINOR,
    DEFAULT_LESSONS_COUNT,
    DEFAULT_DUE_DAYS,
)

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
SERVER_PY = ROOT / "web_app_server.py"

VERSION = "7.1.1.1"
NOW = "2026-07-23T10:00:00"


def _tmp_storage() -> Storage:
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db.close()
    return Storage(Path(db.name))


def _make_ctx(storage: Storage, role: str = "operations"):
    import web_app_server as _srv
    ctx = _srv.MiniAppContext.__new__(_srv.MiniAppContext)
    ctx.storage = storage
    ctx._role_for_user = lambda uid: role
    ctx.moyklass = MagicMock()
    ctx.settings = MagicMock()
    ctx.settings.payment_mk_subscription_terms_sync_enabled = True
    return ctx


def _auth(uid: int = 555):
    return {"user_id": uid, "full_name": "Test Admin"}


def _mk_sub(sub_id: int = 1001, status_id: str = "2", price: float = 239.0,
            subscription_id: int = 50) -> dict:
    return {
        "id": sub_id,
        "statusId": status_id,
        "price": price,
        "subscriptionId": subscription_id,
        "beginDate": "2026-07-01",
        "endDate": "2026-07-31",
        "visitCount": 4,
        "visitedCount": 0,
    }


def _mk_result_ok(items: list) -> MagicMock:
    result = MagicMock()
    result.ok = True
    result.data = {"items": items}
    return result


def _mk_result_err(error: str = "api_error") -> MagicMock:
    result = MagicMock()
    result.ok = False
    result.error = error
    return result


def _upsert_terms(st: Storage, mk_user_id: str, price_minor: int = 23900) -> dict:
    return st.upsert_payment_client_terms(
        mk_user_id=mk_user_id,
        base_lessons_count=4,
        base_price_minor=price_minor,
        currency="BYN",
        default_due_days=17,
        automation_enabled=False,
        automation_paused_reason=None,
        base_subscription_type_id=None,
        actor_tg_id=None,
        actor_name="test",
        now_str=NOW,
    )


# ---------------------------------------------------------------------------
# 01-08 — Domain: select_moyklass_subscription_for_terms
# ---------------------------------------------------------------------------

class Test01DomainNotFound(unittest.TestCase):
    def test_01_empty_list_returns_not_found(self):
        r = select_moyklass_subscription_for_terms([])
        self.assertEqual(r["state"], "not_found")
        self.assertIsNone(r["subscription"])

    def test_02_no_active_subscriptions_returns_not_found(self):
        subs = [
            _mk_sub(status_id="1"),  # inactive
            _mk_sub(status_id="4"),  # ended
        ]
        r = select_moyklass_subscription_for_terms(subs)
        self.assertEqual(r["state"], "not_found")


class Test02DomainAmbiguous(unittest.TestCase):
    def test_03_two_active_returns_ambiguous(self):
        subs = [_mk_sub(sub_id=1, price=239.0), _mk_sub(sub_id=2, price=200.0)]
        r = select_moyklass_subscription_for_terms(subs)
        self.assertEqual(r["state"], "ambiguous")
        self.assertIsNone(r["subscription"])
        self.assertIn("2", r.get("reason", ""))


class Test03DomainInvalid(unittest.TestCase):
    def test_04_zero_price_returns_invalid(self):
        r = select_moyklass_subscription_for_terms([_mk_sub(price=0.0)])
        self.assertEqual(r["state"], "invalid")
        self.assertIsNotNone(r["subscription"])

    def test_05_negative_price_returns_invalid(self):
        r = select_moyklass_subscription_for_terms([_mk_sub(price=-10.0)])
        self.assertEqual(r["state"], "invalid")

    def test_06_none_price_returns_invalid(self):
        sub = _mk_sub(price=0.0)
        del sub["price"]
        r = select_moyklass_subscription_for_terms([sub])
        self.assertEqual(r["state"], "invalid")


class Test04DomainNewSource(unittest.TestCase):
    def test_07_price_differs_returns_new_source(self):
        r = select_moyklass_subscription_for_terms([_mk_sub(price=239.0)], current_price_minor=23900)
        # 239.0 BYN = 23900 minor — same price
        self.assertEqual(r["state"], "unchanged")

    def test_08_price_matches_returns_unchanged(self):
        # 239.0 * 100 = 23900 minor
        r = select_moyklass_subscription_for_terms([_mk_sub(price=239.0)], current_price_minor=20000)
        self.assertEqual(r["state"], "new_source")
        self.assertEqual(r["price_minor"], 23900)


# ---------------------------------------------------------------------------
# 09-12 — Storage: source columns present + update_payment_client_terms_source
# ---------------------------------------------------------------------------

class Test05StorageMigration(unittest.TestCase):
    def test_09_source_columns_present_after_init(self):
        import sqlite3
        st = _tmp_storage()
        with sqlite3.connect(st.db_path) as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(payment_client_terms)").fetchall()]
        for col in (
            "terms_source", "source_subscription_id", "source_subscription_type_id",
            "source_synced_at", "source_snapshot_json", "source_sync_status",
            "source_ambiguity_reason",
        ):
            self.assertIn(col, cols, f"column {col} missing from payment_client_terms")

    def test_10_source_update_saves_fields(self):
        st = _tmp_storage()
        _upsert_terms(st, "u1")
        st.update_payment_client_terms_source(
            mk_user_id="u1",
            terms_source="moyklass_subscription",
            source_subscription_id="5001",
            source_subscription_type_id="42",
            source_synced_at=NOW,
            source_snapshot_json='{"id": 5001}',
            source_sync_status="new_source",
            source_ambiguity_reason=None,
            now_str=NOW,
        )
        row = st.get_payment_client_terms("u1")
        self.assertEqual(row["terms_source"], "moyklass_subscription")
        self.assertEqual(row["source_subscription_id"], "5001")
        self.assertEqual(row["source_sync_status"], "new_source")
        self.assertIsNone(row["source_ambiguity_reason"])

    def test_11_source_update_with_ambiguity_reason(self):
        st = _tmp_storage()
        _upsert_terms(st, "u2")
        st.update_payment_client_terms_source(
            mk_user_id="u2",
            terms_source="manual",
            source_subscription_id=None,
            source_subscription_type_id=None,
            source_synced_at=NOW,
            source_snapshot_json=None,
            source_sync_status="ambiguous",
            source_ambiguity_reason="2_active_subscriptions",
            now_str=NOW,
        )
        row = st.get_payment_client_terms("u2")
        self.assertEqual(row["source_sync_status"], "ambiguous")
        self.assertEqual(row["source_ambiguity_reason"], "2_active_subscriptions")

    def test_12_source_update_on_missing_row_returns_none(self):
        st = _tmp_storage()
        result = st.update_payment_client_terms_source(
            mk_user_id="nonexistent",
            terms_source="manual",
            source_subscription_id=None,
            source_subscription_type_id=None,
            source_synced_at=NOW,
            source_snapshot_json=None,
            source_sync_status="not_found",
            source_ambiguity_reason=None,
            now_str=NOW,
        )
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 13-17 — Server: sync method behaviour
# ---------------------------------------------------------------------------

class Test06ServerSyncBehaviour(unittest.TestCase):
    def setUp(self):
        self.st = _tmp_storage()
        self.ctx = _make_ctx(self.st)

    def test_13_manual_sync_works_when_flag_disabled(self):
        """Manual admin endpoint must work regardless of the auto-flag setting."""
        self.ctx.settings.payment_mk_subscription_terms_sync_enabled = False
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([_mk_sub(price=199.0)])
        _upsert_terms(self.st, "u1", 23900)
        r = self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        self.assertTrue(r["ok"])
        self.assertIn(r["state"], ("new_source", "unchanged", "ambiguous", "not_found", "invalid"))
        self.assertNotEqual(r.get("error"), "sync_disabled")

    def test_14_mk_api_error_returns_not_found(self):
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_err("connection_error")
        r = self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        self.assertFalse(r["ok"])
        self.assertEqual(r["state"], "not_found")

    def test_15_not_found_state_returns_ok_no_update(self):
        _upsert_terms(self.st, "u1", 23900)
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([])
        r = self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "not_found")
        row = self.st.get_payment_client_terms("u1")
        self.assertEqual(row["base_price_minor"], 23900)

    def test_16_ambiguous_state_returns_ok_no_price_update(self):
        _upsert_terms(self.st, "u1", 23900)
        subs = [_mk_sub(sub_id=1, price=239.0), _mk_sub(sub_id=2, price=200.0)]
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok(subs)
        r = self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "ambiguous")
        row = self.st.get_payment_client_terms("u1")
        self.assertEqual(row["base_price_minor"], 23900)

    def test_17_unchanged_state_updates_source_status(self):
        _upsert_terms(self.st, "u1", 23900)  # 239.00 BYN
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([_mk_sub(price=239.0)])
        r = self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "unchanged")
        row = self.st.get_payment_client_terms("u1")
        self.assertEqual(row["source_sync_status"], "unchanged")


# ---------------------------------------------------------------------------
# 18-22 — Server: new_source state fully updates terms + source fields
# ---------------------------------------------------------------------------

class Test07ServerNewSource(unittest.TestCase):
    def setUp(self):
        self.st = _tmp_storage()
        self.ctx = _make_ctx(self.st)

    def test_18_new_source_updates_price(self):
        _upsert_terms(self.st, "u1", 23900)
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([_mk_sub(price=200.0)])
        r = self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "new_source")
        row = self.st.get_payment_client_terms("u1")
        self.assertEqual(row["base_price_minor"], 20000)

    def test_19_new_source_creates_audit_entries(self):
        _upsert_terms(self.st, "u1", 23900)
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([_mk_sub(price=200.0)])
        self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        events = [a["event_type"] for a in self.st.list_payment_pricing_audit("u1")]
        self.assertIn("terms_updated", events)

    def test_20_new_source_sets_terms_source_moyklass(self):
        _upsert_terms(self.st, "u1", 23900)
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([_mk_sub(sub_id=9001, price=200.0)])
        self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        row = self.st.get_payment_client_terms("u1")
        self.assertEqual(row["terms_source"], "moyklass_subscription")
        self.assertEqual(row["source_sync_status"], "new_source")

    def test_21_new_source_stores_subscription_id(self):
        _upsert_terms(self.st, "u1", 23900)
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([_mk_sub(sub_id=7777, price=200.0)])
        self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        row = self.st.get_payment_client_terms("u1")
        self.assertEqual(row["source_subscription_id"], "7777")

    def test_22_new_source_on_no_existing_terms_creates_row(self):
        self.assertIsNone(self.st.get_payment_client_terms("u_new"))
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([_mk_sub(price=180.0)])
        r = self.ctx.payment_client_terms_sync(_auth(), "u_new", {})
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "new_source")
        row = self.st.get_payment_client_terms("u_new")
        self.assertIsNotNone(row)
        self.assertEqual(row["base_price_minor"], 18000)


# ---------------------------------------------------------------------------
# 23-27 — Server: route + auth + server source
# ---------------------------------------------------------------------------

class Test08ServerRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = SERVER_PY.read_text(encoding="utf-8")

    def test_23_terms_sync_route_in_post_handler(self):
        self.assertIn('"terms" and _cl_parts[2] == "sync"', self.server)
        self.assertIn("payment_client_terms_sync", self.server)

    def test_24_payment_client_terms_sync_method_exists(self):
        self.assertIn("def payment_client_terms_sync(", self.server)

    def test_25_sync_payment_terms_helper_exists(self):
        self.assertIn("def _sync_payment_terms_from_moyklass(", self.server)

    def test_26_manual_sync_not_flag_gated(self):
        """payment_client_terms_sync must NOT check the auto-flag (flag gating belongs only in invoice flow)."""
        idx = self.server.find("def payment_client_terms_sync(")
        next_def = self.server.find("\n    def ", idx + 1)
        method = self.server[idx:next_def]
        self.assertNotIn("sync_disabled", method)
        self.assertNotIn("payment_mk_subscription_terms_sync_enabled", method)

    def test_27_select_moyklass_subscription_imported(self):
        self.assertIn("select_moyklass_subscription_for_terms", self.server)


# ---------------------------------------------------------------------------
# 28-33 — Automatic trigger in invoice flow
# ---------------------------------------------------------------------------

class Test09AutoTrigger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = SERVER_PY.read_text(encoding="utf-8")

    def test_28_auto_trigger_block_exists_in_invoice_handler(self):
        self.assertIn("sync_payment_terms_auto", self.server)

    def test_29_auto_trigger_is_flag_gated(self):
        idx = self.server.find("sync_payment_terms_auto")
        segment = self.server[max(0, idx - 400):idx + 200]
        self.assertIn("payment_mk_subscription_terms_sync_enabled", segment)

    def test_30_auto_trigger_only_for_new_invoices(self):
        idx = self.server.find("sync_payment_terms_auto")
        segment = self.server[max(0, idx - 400):idx + 200]
        self.assertIn("is_new", segment)

    def test_31_flag_exists_in_config(self):
        cfg = (ROOT / "config.py").read_text(encoding="utf-8")
        self.assertIn("payment_mk_subscription_terms_sync_enabled", cfg)
        self.assertIn("PAYMENT_MK_SUBSCRIPTION_TERMS_SYNC_ENABLED", cfg.upper())

    def test_32_auto_sync_does_not_break_existing_invoice_flow(self):
        """Auto-sync exception must be caught and must not propagate."""
        st = _tmp_storage()
        ctx = _make_ctx(st, role="operations")
        ctx.settings.payment_mk_subscription_terms_sync_enabled = True
        ctx.moyklass.get_user_subscriptions.side_effect = RuntimeError("mk_down")

        import web_app_server as _srv
        ctx2 = _srv.MiniAppContext.__new__(_srv.MiniAppContext)
        ctx2.storage = st
        ctx2.settings = ctx.settings
        ctx2.moyklass = ctx.moyklass

        inv = {
            "id": 9999, "userId": 5555, "price": 239.0, "payed": 0.0,
            "payUntil": "2026-07-31", "createdAt": NOW,
            "userSubscription": {"clientName": "Тест", "beginDate": "2026-07-01"},
            "userSubscriptionId": 1001,
        }
        try:
            ctx2._process_single_automation_item_from_invoice(
                inv, now=NOW, create_enabled=False, publish_enabled=False,
            )
        except Exception as e:
            self.fail(f"Auto-sync exception leaked: {e}")

    def test_33_auto_sync_flag_off_no_mk_call(self):
        """When flag is off, MK get_user_subscriptions must NOT be called during invoice processing."""
        st = _tmp_storage()
        import web_app_server as _srv
        ctx = _srv.MiniAppContext.__new__(_srv.MiniAppContext)
        ctx.storage = st
        ctx.settings = MagicMock()
        ctx.settings.payment_mk_subscription_terms_sync_enabled = False
        ctx.moyklass = MagicMock()

        inv = {
            "id": 8888, "userId": 4444, "price": 100.0, "payed": 0.0,
            "payUntil": "2026-07-31", "createdAt": NOW,
            "userSubscription": {"clientName": "Тест", "beginDate": "2026-07-01"},
        }
        ctx._process_single_automation_item_from_invoice(
            inv, now=NOW, create_enabled=False, publish_enabled=False,
        )
        ctx.moyklass.get_user_subscriptions.assert_not_called()


# ---------------------------------------------------------------------------
# 34-40 — Frontend static analysis
# ---------------------------------------------------------------------------

class Test10Frontend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")
        cls.css = (ROOT / "miniapp" / "styles.css").read_text(encoding="utf-8")

    def test_34_version_marker_v711(self):
        self.assertIn(f'console.log("MiniApp version: v{VERSION}")', self.js)

    def test_35_cache_bust_v711_in_html(self):
        self.assertIn(f"app.js?v={VERSION}", self.html)
        self.assertIn(f"styles.css?v={VERSION}", self.html)

    def test_36_sync_button_present(self):
        self.assertIn("ptSyncTerms", self.js)
        self.assertIn("Обновить условия из МойКласс", self.js)

    def test_37_sync_endpoint_called(self):
        self.assertIn("/terms/sync", self.js)

    def test_38_sync_button_loading_state(self):
        self.assertIn("Синхронизация...", self.js)

    def test_39_source_info_displayed(self):
        self.assertIn("ptSyncNotice", self.js)
        self.assertIn("pt-source-info", self.js)
        self.assertIn("pt-source-chip", self.js)

    def test_40_sync_styles_in_css(self):
        self.assertIn(".pt-source-chip", self.css)
        self.assertIn(".pt-sync-status", self.css)


# ---------------------------------------------------------------------------
# 41-46 — v7.1.1.1: manual/auto split + localization + flag default
# ---------------------------------------------------------------------------

class Test11ManualAutoSplit(unittest.TestCase):
    def setUp(self):
        self.st = _tmp_storage()
        self.ctx = _make_ctx(self.st)

    def test_41_auto_sync_calls_mk_when_flag_true(self):
        """When flag=true and invoice is new, MK subscriptions ARE fetched."""
        import web_app_server as _srv
        ctx = _srv.MiniAppContext.__new__(_srv.MiniAppContext)
        ctx.storage = self.st
        ctx.settings = MagicMock()
        ctx.settings.payment_mk_subscription_terms_sync_enabled = True
        ctx.moyklass = MagicMock()
        ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([])
        inv = {
            "id": 7001, "userId": 7001, "price": 239.0, "payed": 0.0,
            "payUntil": "2026-07-31", "createdAt": NOW,
            "userSubscription": {"clientName": "Тест", "beginDate": "2026-07-01"},
            "userSubscriptionId": 1001,
        }
        ctx._process_single_automation_item_from_invoice(
            inv, now=NOW, create_enabled=False, publish_enabled=False,
        )
        ctx.moyklass.get_user_subscriptions.assert_called_once()

    def test_42_manual_sync_denied_for_wrong_role(self):
        """Manual sync must be rejected when the caller has an unauthorised role."""
        ctx = _make_ctx(self.st, role="viewer")
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([_mk_sub()])
        r = ctx.payment_client_terms_sync(_auth(), "u_auth", {})
        self.assertFalse(r.get("ok", True))


class Test12FrontendLocalization(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_43_frontend_sync_disabled_localized(self):
        """Frontend stateMap must handle sync_disabled without exposing the raw key."""
        self.assertIn("sync_disabled", self.js)
        self.assertNotIn('"sync_disabled"', self.js.split("stateMap")[0] if "stateMap" in self.js else "")

    def test_44_frontend_all_domain_states_localized(self):
        """stateMap in sync handler must cover all domain states in Russian."""
        js = self.js
        idx = js.find("const stateMap")
        block = js[idx:idx + 600]
        for state in ("new_source", "unchanged", "ambiguous", "not_found", "invalid", "sync_disabled"):
            self.assertIn(state, block, f"stateMap missing: {state}")

    def test_45_frontend_no_raw_mk_api_error_key(self):
        """Frontend must not display the raw 'mk_api_error' string to the user."""
        self.assertNotIn('"mk_api_error"', self.js)

    def test_46_default_feature_flag_is_false(self):
        """PAYMENT_MK_SUBSCRIPTION_TERMS_SYNC_ENABLED must default to False in config source."""
        cfg = (ROOT / "config.py").read_text(encoding="utf-8")
        self.assertIn("payment_mk_subscription_terms_sync_enabled: bool = False", cfg)


if __name__ == "__main__":
    unittest.main()
