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

VERSION = "7.1.6.1"
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
            subscription_id: int = 50, sell_date: str = "2026-07-01",
            visit_count: int = 4) -> dict:
    return {
        "id": sub_id,
        "statusId": status_id,
        "price": price,
        "subscriptionId": subscription_id,
        "sellDate": sell_date,
        "beginDate": "2026-07-01",
        "endDate": "2026-07-31",
        "visitCount": visit_count,
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
# 01-16 — Domain: select_moyklass_subscription_for_terms (v7.1.2 algorithm)
# ---------------------------------------------------------------------------

class Test01DomainNotFound(unittest.TestCase):
    def test_01_empty_list_returns_not_found(self):
        r = select_moyklass_subscription_for_terms([])
        self.assertEqual(r["state"], "not_found")
        self.assertIsNone(r["subscription"])

    def test_02_no_active_subscriptions_returns_not_found(self):
        subs = [_mk_sub(status_id="1"), _mk_sub(status_id="4")]
        r = select_moyklass_subscription_for_terms(subs)
        self.assertEqual(r["state"], "not_found")

    def test_03_single_active_sub_selected(self):
        r = select_moyklass_subscription_for_terms([_mk_sub(sub_id=100, price=199.0)])
        self.assertIn(r["state"], ("new_source", "unchanged"))
        self.assertIsNotNone(r["subscription"])
        self.assertEqual(r["price_minor"], 19900)


class Test02DomainMultipleActive(unittest.TestCase):
    def test_04_two_active_newer_by_sell_date_wins(self):
        subs = [
            _mk_sub(sub_id=100, price=229.0, sell_date="2026-06-01"),
            _mk_sub(sub_id=200, price=199.0, sell_date="2026-07-01"),
        ]
        r = select_moyklass_subscription_for_terms(subs)
        self.assertIn(r["state"], ("new_source", "unchanged"))
        self.assertEqual(r["price_minor"], 19900)
        self.assertEqual(r["subscription"]["id"], 200)

    def test_05_three_active_newest_wins_not_ambiguous(self):
        subs = [
            _mk_sub(sub_id=100, price=229.0, sell_date="2026-05-01"),
            _mk_sub(sub_id=200, price=209.0, sell_date="2026-06-01"),
            _mk_sub(sub_id=300, price=199.0, sell_date="2026-07-01"),
        ]
        r = select_moyklass_subscription_for_terms(subs)
        self.assertNotEqual(r["state"], "ambiguous")
        self.assertEqual(r["subscription"]["id"], 300)
        self.assertEqual(r["price_minor"], 19900)
        self.assertEqual(r["candidates_count"], 3)

    def test_06_newer_lower_price_sub_wins(self):
        subs = [
            _mk_sub(sub_id=100, price=229.0, sell_date="2026-06-01"),
            _mk_sub(sub_id=200, price=199.0, sell_date="2026-07-01"),
        ]
        r = select_moyklass_subscription_for_terms(subs)
        self.assertEqual(r["price_minor"], 19900)

    def test_07_newer_higher_price_sub_wins(self):
        subs = [
            _mk_sub(sub_id=100, price=199.0, sell_date="2026-06-01"),
            _mk_sub(sub_id=200, price=239.0, sell_date="2026-07-01"),
        ]
        r = select_moyklass_subscription_for_terms(subs)
        self.assertEqual(r["price_minor"], 23900)

    def test_08_same_sell_date_higher_id_wins(self):
        subs = [
            _mk_sub(sub_id=100, price=229.0, sell_date="2026-07-01"),
            _mk_sub(sub_id=200, price=199.0, sell_date="2026-07-01"),
        ]
        r = select_moyklass_subscription_for_terms(subs)
        self.assertNotEqual(r["state"], "ambiguous")
        self.assertEqual(r["subscription"]["id"], 200)

    def test_09_same_sell_date_same_id_is_ambiguous(self):
        subs = [
            _mk_sub(sub_id=100, price=229.0, sell_date="2026-07-01"),
            _mk_sub(sub_id=100, price=199.0, sell_date="2026-07-01"),
        ]
        r = select_moyklass_subscription_for_terms(subs)
        self.assertEqual(r["state"], "ambiguous")
        self.assertIsNone(r["subscription"])

    def test_10_input_order_does_not_affect_result(self):
        sub_a = _mk_sub(sub_id=100, price=229.0, sell_date="2026-06-01")
        sub_b = _mk_sub(sub_id=200, price=199.0, sell_date="2026-07-01")
        r1 = select_moyklass_subscription_for_terms([sub_a, sub_b])
        r2 = select_moyklass_subscription_for_terms([sub_b, sub_a])
        self.assertEqual(r1["subscription"]["id"], r2["subscription"]["id"])

    def test_11_cancelled_sub_ignored(self):
        subs = [
            _mk_sub(sub_id=300, price=199.0, sell_date="2026-07-10", status_id="1"),
            _mk_sub(sub_id=200, price=229.0, sell_date="2026-06-01", status_id="2"),
        ]
        r = select_moyklass_subscription_for_terms(subs)
        self.assertEqual(r["subscription"]["id"], 200)

    def test_12_zero_price_sub_ignored(self):
        subs = [
            _mk_sub(sub_id=300, price=0.0, sell_date="2026-07-10"),
            _mk_sub(sub_id=200, price=229.0, sell_date="2026-06-01"),
        ]
        r = select_moyklass_subscription_for_terms(subs)
        self.assertEqual(r["subscription"]["id"], 200)

    def test_13_zero_visit_count_sub_ignored(self):
        subs = [
            _mk_sub(sub_id=300, price=199.0, sell_date="2026-07-10", visit_count=0),
            _mk_sub(sub_id=200, price=229.0, sell_date="2026-06-01", visit_count=4),
        ]
        r = select_moyklass_subscription_for_terms(subs)
        self.assertEqual(r["subscription"]["id"], 200)

    def test_14_no_sell_date_uses_id_fallback(self):
        sub_old = _mk_sub(sub_id=100, price=229.0)
        sub_new = _mk_sub(sub_id=200, price=199.0)
        del sub_old["sellDate"]
        del sub_new["sellDate"]
        r = select_moyklass_subscription_for_terms([sub_old, sub_new])
        self.assertNotEqual(r["state"], "ambiguous")
        self.assertEqual(r["subscription"]["id"], 200)
        self.assertEqual(r["selection_field"], "id")

    def test_15_numeric_id_sorted_numerically_not_lexically(self):
        subs = [
            _mk_sub(sub_id=99, price=229.0, sell_date="2026-07-01"),
            _mk_sub(sub_id=100, price=199.0, sell_date="2026-07-01"),
        ]
        r = select_moyklass_subscription_for_terms(subs)
        self.assertEqual(r["subscription"]["id"], 100)

    def test_16_lessons_count_from_sub_used(self):
        r = select_moyklass_subscription_for_terms([_mk_sub(visit_count=8)])
        self.assertEqual(r["lessons_count"], 8)


class Test03DomainInvalid(unittest.TestCase):
    def test_17_zero_price_returns_invalid(self):
        r = select_moyklass_subscription_for_terms([_mk_sub(price=0.0)])
        self.assertEqual(r["state"], "invalid")
        self.assertIsNotNone(r["subscription"])

    def test_18_negative_price_returns_invalid(self):
        r = select_moyklass_subscription_for_terms([_mk_sub(price=-10.0)])
        self.assertEqual(r["state"], "invalid")

    def test_19_missing_price_returns_invalid(self):
        sub = _mk_sub(price=0.0)
        del sub["price"]
        r = select_moyklass_subscription_for_terms([sub])
        self.assertEqual(r["state"], "invalid")

    def test_20_zero_visit_count_only_sub_returns_invalid(self):
        r = select_moyklass_subscription_for_terms([_mk_sub(visit_count=0)])
        self.assertEqual(r["state"], "invalid")


class Test04DomainState(unittest.TestCase):
    def test_21_unchanged_when_same_sub_price_and_lessons(self):
        r = select_moyklass_subscription_for_terms(
            [_mk_sub(sub_id=1001, price=239.0, visit_count=4)],
            current_price_minor=23900,
            current_lessons_count=4,
            current_source_sub_id="1001",
        )
        self.assertEqual(r["state"], "unchanged")

    def test_22_new_source_when_price_differs(self):
        r = select_moyklass_subscription_for_terms(
            [_mk_sub(sub_id=1001, price=239.0, visit_count=4)],
            current_price_minor=20000,
            current_lessons_count=4,
            current_source_sub_id="1001",
        )
        self.assertEqual(r["state"], "new_source")
        self.assertEqual(r["price_minor"], 23900)

    def test_23_new_source_when_lessons_differ(self):
        r = select_moyklass_subscription_for_terms(
            [_mk_sub(sub_id=1001, price=239.0, visit_count=8)],
            current_price_minor=23900,
            current_lessons_count=4,
            current_source_sub_id="1001",
        )
        self.assertEqual(r["state"], "new_source")
        self.assertEqual(r["lessons_count"], 8)

    def test_24_new_source_when_sub_id_differs(self):
        r = select_moyklass_subscription_for_terms(
            [_mk_sub(sub_id=9999, price=239.0, visit_count=4)],
            current_price_minor=23900,
            current_lessons_count=4,
            current_source_sub_id="1001",
        )
        self.assertEqual(r["state"], "new_source")


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

    def test_14_mk_api_error_returns_api_error_state(self):
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_err("connection_error")
        r = self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        self.assertFalse(r["ok"])
        self.assertEqual(r["state"], "api_error")

    def test_15_not_found_state_returns_ok_no_update(self):
        _upsert_terms(self.st, "u1", 23900)
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([])
        r = self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "not_found")
        row = self.st.get_payment_client_terms("u1")
        self.assertEqual(row["base_price_minor"], 23900)

    def test_16_ambiguous_tie_returns_ok_no_price_update(self):
        """Genuine tie (same sellDate AND same id) → ambiguous, terms unchanged."""
        _upsert_terms(self.st, "u1", 23900)
        # Same id=100 and same sellDate → genuine tie
        subs = [
            _mk_sub(sub_id=100, price=239.0, sell_date="2026-07-01"),
            _mk_sub(sub_id=100, price=200.0, sell_date="2026-07-01"),
        ]
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok(subs)
        r = self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "ambiguous")
        row = self.st.get_payment_client_terms("u1")
        self.assertEqual(row["base_price_minor"], 23900)

    def test_17_unchanged_state_updates_source_status(self):
        _upsert_terms(self.st, "u1", 23900)
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok(
            [_mk_sub(sub_id=1001, price=239.0, visit_count=4)]
        )
        # Set source to same sub so it's unchanged
        self.st.update_payment_client_terms_source(
            mk_user_id="u1", terms_source="moyklass_subscription",
            source_subscription_id="1001", source_subscription_type_id="50",
            source_synced_at=NOW, source_snapshot_json=None,
            source_sync_status="new_source", source_ambiguity_reason=None, now_str=NOW,
        )
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

    def test_18_new_source_updates_price_and_lessons(self):
        _upsert_terms(self.st, "u1", 23900)
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok(
            [_mk_sub(price=200.0, visit_count=8)]
        )
        r = self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "new_source")
        row = self.st.get_payment_client_terms("u1")
        self.assertEqual(row["base_price_minor"], 20000)
        self.assertEqual(row["base_lessons_count"], 8)

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
        # v7.1.3: flag check must gate the auto-sync (window extended for allowlist block)
        idx = self.server.find("sync_payment_terms_auto")
        segment = self.server[max(0, idx - 1600):idx + 200]
        self.assertIn("payment_mk_subscription_terms_sync_enabled", segment)

    def test_30_auto_trigger_only_for_new_invoices(self):
        # v7.1.3: is_new check must gate the auto-sync (window extended for allowlist block)
        idx = self.server.find("sync_payment_terms_auto")
        segment = self.server[max(0, idx - 1600):idx + 200]
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
        self.assertIn("Обновление...", self.js)

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
        """v7.1.3: flag=true + user in allowlist + new invoice → MK subscriptions ARE fetched."""
        import web_app_server as _srv
        ctx = _srv.MiniAppContext.__new__(_srv.MiniAppContext)
        ctx.storage = self.st
        ctx.settings = MagicMock()
        ctx.settings.payment_mk_subscription_terms_sync_enabled = True
        ctx.settings.payment_mk_subscription_terms_sync_user_ids = ("7001",)
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
        block = js[idx:idx + 700]
        for state in ("new_source", "unchanged", "ambiguous", "not_found", "invalid", "api_error", "sync_disabled"):
            self.assertIn(state, block, f"stateMap missing: {state}")

    def test_45_frontend_no_raw_mk_api_error_key(self):
        """Frontend must not display the raw 'mk_api_error' string to the user."""
        self.assertNotIn('"mk_api_error"', self.js)

    def test_46_default_feature_flag_is_false(self):
        """PAYMENT_MK_SUBSCRIPTION_TERMS_SYNC_ENABLED must default to False in config source."""
        cfg = (ROOT / "config.py").read_text(encoding="utf-8")
        self.assertIn("payment_mk_subscription_terms_sync_enabled: bool = False", cfg)


# ---------------------------------------------------------------------------
# 47-56 — v7.1.2: multi-candidate, snapshot, audit, regression
# ---------------------------------------------------------------------------

class Test13V712MultiCandidate(unittest.TestCase):
    def setUp(self):
        self.st = _tmp_storage()
        self.ctx = _make_ctx(self.st)

    def test_47_three_active_subs_picks_newest_not_ambiguous(self):
        """Three active subscriptions must not produce ambiguous when orderable."""
        subs = [
            _mk_sub(sub_id=100, price=229.0, sell_date="2026-05-01"),
            _mk_sub(sub_id=200, price=209.0, sell_date="2026-06-01"),
            _mk_sub(sub_id=300, price=199.0, sell_date="2026-07-01"),
        ]
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok(subs)
        _upsert_terms(self.st, "u1", 22900)
        r = self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        self.assertTrue(r["ok"])
        self.assertNotEqual(r["state"], "ambiguous")
        self.assertEqual(r["source_subscription_id"], "300")
        row = self.st.get_payment_client_terms("u1")
        self.assertEqual(row["base_price_minor"], 19900)

    def test_48_snapshot_json_stored_after_new_source(self):
        """source_snapshot_json must contain selected sub's id after new_source."""
        subs = [_mk_sub(sub_id=4242, price=199.0)]
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok(subs)
        _upsert_terms(self.st, "u1", 23900)
        self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        row = self.st.get_payment_client_terms("u1")
        import json as _json
        snap = _json.loads(row["source_snapshot_json"] or "{}")
        self.assertEqual(str(snap.get("id")), "4242")

    def test_49_ambiguity_reason_cleared_after_new_source(self):
        """source_ambiguity_reason must be NULL after a successful new_source sync."""
        self.st.update_payment_client_terms_source(
            mk_user_id="u1", terms_source="manual", source_subscription_id=None,
            source_subscription_type_id=None, source_synced_at=NOW,
            source_snapshot_json=None, source_sync_status="ambiguous",
            source_ambiguity_reason="tie_2_candidates", now_str=NOW,
        )
        _upsert_terms(self.st, "u1", 23900)
        subs = [_mk_sub(sub_id=500, price=199.0)]
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok(subs)
        self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        row = self.st.get_payment_client_terms("u1")
        self.assertIsNone(row["source_ambiguity_reason"])

    def test_50_manual_source_replaced_by_moyklass(self):
        """Manual terms_source must be replaced by moyklass_subscription after sync."""
        _upsert_terms(self.st, "u1", 23900)
        self.st.update_payment_client_terms_source(
            mk_user_id="u1", terms_source="manual", source_subscription_id=None,
            source_subscription_type_id=None, source_synced_at=NOW,
            source_snapshot_json=None, source_sync_status="unchanged", source_ambiguity_reason=None,
            now_str=NOW,
        )
        subs = [_mk_sub(sub_id=777, price=199.0)]
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok(subs)
        self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        row = self.st.get_payment_client_terms("u1")
        self.assertEqual(row["terms_source"], "moyklass_subscription")

    def test_51_unchanged_no_duplicate_audit(self):
        """unchanged state must not create new audit entries."""
        _upsert_terms(self.st, "u1", 23900)
        self.st.update_payment_client_terms_source(
            mk_user_id="u1", terms_source="moyklass_subscription",
            source_subscription_id="1001", source_subscription_type_id="50",
            source_synced_at=NOW, source_snapshot_json=None,
            source_sync_status="unchanged", source_ambiguity_reason=None, now_str=NOW,
        )
        before = self.st.list_payment_pricing_audit("u1")
        subs = [_mk_sub(sub_id=1001, price=239.0, visit_count=4)]
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok(subs)
        self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        after = self.st.list_payment_pricing_audit("u1")
        self.assertEqual(len(before), len(after))

    def test_52_api_error_does_not_corrupt_terms(self):
        """MK API error must not modify existing payment_client_terms."""
        _upsert_terms(self.st, "u1", 23900)
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_err("timeout")
        r = self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        self.assertFalse(r["ok"])
        row = self.st.get_payment_client_terms("u1")
        self.assertEqual(row["base_price_minor"], 23900)

    def test_53_candidates_count_in_response(self):
        """Response must include candidates_count for multi-sub scenarios."""
        subs = [
            _mk_sub(sub_id=100, price=229.0, sell_date="2026-06-01"),
            _mk_sub(sub_id=200, price=199.0, sell_date="2026-07-01"),
        ]
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok(subs)
        r = self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        self.assertTrue(r["ok"])
        self.assertEqual(r.get("candidates_count"), 2)

    def test_54_selection_field_in_response(self):
        """Response must include selection_field to explain sort criterion used."""
        subs = [_mk_sub(sub_id=100, price=199.0, sell_date="2026-07-01")]
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok(subs)
        r = self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        self.assertTrue(r["ok"])
        self.assertIn(r.get("selection_field"), ("sellDate", "id", None))


class Test14V712Regression(unittest.TestCase):
    def test_55_food_module_not_referenced_in_new_domain_code(self):
        """payment_domain.py must not reference food module functions."""
        src = (ROOT / "payment_domain.py").read_text(encoding="utf-8")
        self.assertNotIn("food_module", src)
        self.assertNotIn("food_menu", src)

    def test_56_cache_bust_format(self):
        """Cache-bust format exists in index.html and version marker in app.js."""
        html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("v=7.1", html, "Cache-bust must start with v=7.1")
        self.assertIn("v7.1", js, "Version marker must start with v7.1")


# ---------------------------------------------------------------------------
# 57-71 — v7.1.2.1: atomic audit correctness (new_source writes one complete row)
# ---------------------------------------------------------------------------

class Test15AuditAtomicity(unittest.TestCase):
    """Verify that new_source sync writes base + source fields in one transaction
    and the resulting audit captures the final state in new_value_json."""

    def setUp(self):
        self.st = _tmp_storage()
        self.ctx = _make_ctx(self.st)

    def _pre_set_mk_source(self, mk_user_id: str, sub_id: str, type_id: str,
                            price_minor: int, snapshot: str | None = None) -> None:
        _upsert_terms(self.st, mk_user_id, price_minor)
        self.st.update_payment_client_terms_source(
            mk_user_id=mk_user_id, terms_source="moyklass_subscription",
            source_subscription_id=sub_id, source_subscription_type_id=type_id,
            source_synced_at=NOW, source_snapshot_json=snapshot,
            source_sync_status="new_source", source_ambiguity_reason=None, now_str=NOW,
        )

    def _terms_updated_audits(self, mk_user_id: str) -> list:
        return [a for a in self.st.list_payment_pricing_audit(mk_user_id)
                if a["event_type"] == "terms_updated"]

    # --- test 57: manual → moyklass ---

    def test_57_manual_to_moyklass_audit_sources_correct(self):
        """After manual→moyklass sync: old_value has terms_source=manual,
        new_value has terms_source=moyklass_subscription with correct sub ID."""
        _upsert_terms(self.st, "u1", 23900)
        self.st.update_payment_client_terms_source(
            mk_user_id="u1", terms_source="manual",
            source_subscription_id=None, source_subscription_type_id=None,
            source_synced_at=NOW, source_snapshot_json=None,
            source_sync_status="unchanged", source_ambiguity_reason=None, now_str=NOW,
        )
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok(
            [_mk_sub(sub_id=5001, price=200.0, subscription_id=99)]
        )
        self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        audits = self._terms_updated_audits("u1")
        self.assertEqual(len(audits), 1)
        import json as _json
        old_val = _json.loads(audits[0]["old_value_json"])
        new_val = _json.loads(audits[0]["new_value_json"])
        self.assertEqual(old_val.get("terms_source"), "manual")
        self.assertEqual(new_val.get("terms_source"), "moyklass_subscription")
        self.assertEqual(new_val.get("source_subscription_id"), "5001")

    # --- test 58: mk old sub → mk newer sub ---

    def test_58_old_mk_sub_to_newer_mk_sub_audit_ids_correct(self):
        """Audit must capture old sub ID in old_value and new sub ID in new_value."""
        self._pre_set_mk_source("u1", sub_id="18024837", type_id="265878",
                                 price_minor=100,
                                 snapshot='{"id": 18024837, "subscriptionId": 265878}')
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([
            _mk_sub(sub_id=18074747, price=57.25, subscription_id=245319,
                    sell_date="2026-07-24", visit_count=1),
            _mk_sub(sub_id=18024837, price=1.0, subscription_id=265878,
                    sell_date="2026-07-01", visit_count=1),
        ])
        self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        audits = self._terms_updated_audits("u1")
        self.assertEqual(len(audits), 1)
        import json as _json
        old_val = _json.loads(audits[0]["old_value_json"])
        new_val = _json.loads(audits[0]["new_value_json"])
        self.assertEqual(old_val.get("source_subscription_id"), "18024837")
        self.assertEqual(old_val.get("source_subscription_type_id"), "265878")
        self.assertEqual(new_val.get("source_subscription_id"), "18074747")
        self.assertEqual(new_val.get("source_subscription_type_id"), "245319")
        self.assertIsNotNone(new_val.get("source_synced_at"))

    # --- test 59: production scenario ---

    def test_59_production_example_new_value_has_new_sub_id(self):
        """Production: client 9748998 old sub 18024837 (100 minor) →
        new sub 18074747 (5725 minor). new_value must NOT contain old sub ID."""
        self._pre_set_mk_source("u1", sub_id="18024837", type_id="265878",
                                 price_minor=100)
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([
            _mk_sub(sub_id=18074747, price=57.25, subscription_id=245319,
                    sell_date="2026-07-24", visit_count=1),
            _mk_sub(sub_id=18024837, price=1.0, subscription_id=265878,
                    sell_date="2026-07-01", visit_count=1),
        ])
        r = self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "new_source")
        audits = self._terms_updated_audits("u1")
        self.assertEqual(len(audits), 1)
        import json as _json
        new_val = _json.loads(audits[0]["new_value_json"])
        self.assertEqual(new_val.get("source_subscription_id"), "18074747")
        self.assertNotEqual(new_val.get("source_subscription_id"), "18024837")
        self.assertEqual(new_val.get("base_price_minor"), 5725)

    # --- test 60: new_value_json completeness ---

    def test_60_new_value_json_has_all_required_source_fields(self):
        """new_value_json in audit must contain the final state of ALL source fields."""
        _upsert_terms(self.st, "u1", 23900)
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok(
            [_mk_sub(sub_id=9999, price=199.0, subscription_id=111)]
        )
        self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        audits = self._terms_updated_audits("u1")
        self.assertEqual(len(audits), 1)
        import json as _json
        new_val = _json.loads(audits[0]["new_value_json"])
        self.assertEqual(new_val.get("terms_source"), "moyklass_subscription")
        self.assertEqual(new_val.get("source_subscription_id"), "9999")
        self.assertEqual(new_val.get("source_subscription_type_id"), "111")
        self.assertIsNotNone(new_val.get("source_snapshot_json"))
        self.assertEqual(new_val.get("source_sync_status"), "new_source")
        self.assertIsNone(new_val.get("source_ambiguity_reason"))

    # --- test 61: exactly one audit per new_source ---

    def test_61_exactly_one_terms_updated_audit_per_new_source(self):
        """new_source sync must create exactly one terms_updated audit — not two."""
        _upsert_terms(self.st, "u1", 23900)
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok(
            [_mk_sub(sub_id=1234, price=200.0)]
        )
        self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        audits = self._terms_updated_audits("u1")
        self.assertEqual(len(audits), 1, "Expected exactly 1 terms_updated audit, not 2")

    # --- test 62: ambiguous creates no terms_updated audit ---

    def test_62_ambiguous_state_creates_no_terms_updated_audit(self):
        """Genuine tie (ambiguous) must not create terms_updated audit."""
        _upsert_terms(self.st, "u1", 23900)
        subs = [
            _mk_sub(sub_id=100, price=229.0, sell_date="2026-07-01"),
            _mk_sub(sub_id=100, price=200.0, sell_date="2026-07-01"),
        ]
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok(subs)
        before = len(self._terms_updated_audits("u1"))
        r = self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        self.assertEqual(r["state"], "ambiguous")
        after = len(self._terms_updated_audits("u1"))
        self.assertEqual(before, after)

    # --- test 63: rollback on audit failure ---

    def test_63_rollback_on_audit_failure_leaves_terms_unchanged(self):
        """If audit INSERT fails, the entire transaction rolls back.
        Terms must stay at old values and no audit must be created."""
        _upsert_terms(self.st, "u1", 23900)
        original_audit = self.st._append_pricing_audit_conn

        def _fail_audit(*args, **kwargs):
            raise RuntimeError("simulated_audit_failure")

        self.st._append_pricing_audit_conn = _fail_audit
        try:
            with self.assertRaises(RuntimeError):
                self.st.upsert_payment_client_terms_moyklass(
                    mk_user_id="u1",
                    base_lessons_count=4,
                    base_price_minor=99900,
                    currency="BYN",
                    default_due_days=17,
                    automation_enabled=False,
                    automation_paused_reason=None,
                    base_subscription_type_id="777",
                    terms_source="moyklass_subscription",
                    source_subscription_id="9999",
                    source_subscription_type_id="777",
                    source_synced_at=NOW,
                    source_snapshot_json='{"id": 9999}',
                    source_sync_status="new_source",
                    source_ambiguity_reason=None,
                    actor_tg_id=555,
                    actor_name="Test Admin",
                    now_str=NOW,
                    audit_reason="newer_moyklass_subscription",
                )
        finally:
            self.st._append_pricing_audit_conn = original_audit

        row = self.st.get_payment_client_terms("u1")
        self.assertEqual(row["base_price_minor"], 23900,
                         "Terms must be unchanged after rollback")
        self.assertEqual(len(self._terms_updated_audits("u1")), 0,
                         "No terms_updated audit must exist after rollback")

    # --- test 64: manual save unaffected ---

    def test_64_manual_upsert_still_creates_correct_audit(self):
        """Existing upsert_payment_client_terms (used by manual save) must still work."""
        _upsert_terms(self.st, "u1", 23900)
        self.st.upsert_payment_client_terms(
            mk_user_id="u1",
            base_lessons_count=8,
            base_price_minor=19900,
            currency="BYN",
            default_due_days=17,
            automation_enabled=False,
            automation_paused_reason=None,
            base_subscription_type_id="manual_type",
            actor_tg_id=555,
            actor_name="Test Admin",
            now_str=NOW,
        )
        row = self.st.get_payment_client_terms("u1")
        self.assertEqual(row["base_price_minor"], 19900)
        self.assertEqual(row["base_lessons_count"], 8)
        audits = self._terms_updated_audits("u1")
        self.assertEqual(len(audits), 1)

    # --- test 65: audit reason field ---

    def test_65_audit_reason_is_newer_moyklass_subscription(self):
        """terms_updated audit reason must be 'newer_moyklass_subscription'."""
        _upsert_terms(self.st, "u1", 23900)
        self.ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok(
            [_mk_sub(sub_id=8888, price=199.0)]
        )
        self.ctx.payment_client_terms_sync(_auth(), "u1", {})
        audits = self._terms_updated_audits("u1")
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["reason"], "newer_moyklass_subscription")

    # --- test 66: storage has atomic method ---

    def test_66_storage_has_upsert_payment_client_terms_moyklass(self):
        import storage as _st_mod
        self.assertTrue(
            hasattr(_st_mod.Storage, "upsert_payment_client_terms_moyklass"),
            "Storage must expose upsert_payment_client_terms_moyklass",
        )

    # --- test 67: server uses atomic method ---

    def test_67_server_uses_atomic_moyklass_upsert_not_two_calls(self):
        """_sync_payment_terms_from_moyklass must call the atomic method,
        not upsert_payment_client_terms + update_payment_client_terms_source."""
        src = SERVER_PY.read_text(encoding="utf-8")
        idx = src.find("def _sync_payment_terms_from_moyklass(")
        next_def = src.find("\n    def ", idx + 1)
        method = src[idx:next_def]
        self.assertIn("upsert_payment_client_terms_moyklass", method)


class Test16V7121Static(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")
        cls.server = SERVER_PY.read_text(encoding="utf-8")

    # --- test 68: BePaid not called in sync ---

    def test_68_bepaid_not_called_in_moyklass_sync(self):
        idx = self.server.find("def _sync_payment_terms_from_moyklass(")
        next_def = self.server.find("\n    def ", idx + 1)
        method = self.server[idx:next_def]
        self.assertNotIn("bepaid", method.lower())
        self.assertNotIn("create_payment_intent", method.lower())

    # --- test 69: MK write not called ---

    def test_69_moyklass_write_not_called_in_sync(self):
        idx = self.server.find("def _sync_payment_terms_from_moyklass(")
        next_def = self.server.find("\n    def ", idx + 1)
        method = self.server[idx:next_def]
        for write_fn in ("create_invoice", "update_invoice", "delete_invoice",
                         "create_subscription", "update_subscription"):
            self.assertNotIn(write_fn, method,
                             f"Sync method must not call MK write API: {write_fn}")

    # --- test 70: cache-bust v7.1.2.1 ---

    def test_70_cache_bust_v714(self):
        """Cache-bust strings must be v7.1.6.1 in index.html and app.js."""
        self.assertIn("v=7.1.6.1", self.html,
                      "index.html cache-bust must be v=7.1.6.1")
        self.assertIn("v7.1.6.1", self.js,
                      "app.js version marker must contain v7.1.6.1")

    # --- test 71: auto flag source stays False ---

    def test_71_auto_flag_default_false_in_config_source(self):
        cfg = (ROOT / "config.py").read_text(encoding="utf-8")
        self.assertIn("payment_mk_subscription_terms_sync_enabled: bool = False", cfg)


# ---------------------------------------------------------------------------
# 72-96 — v7.1.3: pilot allowlist, structured logging, safety guarantees
# ---------------------------------------------------------------------------

class Test17PilotAllowlist(unittest.TestCase):
    """72-96: v7.1.3 pilot auto-sync allowlist and safety guarantees."""

    @classmethod
    def setUpClass(cls):
        cls.server = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        cls.cfg = (ROOT / "config.py").read_text(encoding="utf-8")
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def _make_inv(self, inv_id=30001, user_id=9748998, price=57.25):
        return {
            "id": inv_id, "userId": user_id, "price": price, "payed": 0.0,
            "payUntil": "2026-08-31", "createdAt": NOW,
            "userSubscription": {"clientName": "Pilot", "beginDate": "2026-08-01"},
        }

    def _make_ctx_with_flags(self, enabled, allowlist):
        import web_app_server as _srv
        st = _tmp_storage()
        ctx = _srv.MiniAppContext.__new__(_srv.MiniAppContext)
        ctx.storage = st
        ctx.settings = MagicMock()
        ctx.settings.payment_mk_subscription_terms_sync_enabled = enabled
        ctx.settings.payment_mk_subscription_terms_sync_user_ids = allowlist
        ctx.moyklass = MagicMock()
        ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([])
        return ctx, st

    # --- test 72: enabled=False → no MK call ---

    def test_72_flag_false_no_mk_call(self):
        """enabled=False → auto-sync must not call MK at all."""
        ctx, _ = self._make_ctx_with_flags(False, ("9748998",))
        ctx._process_single_automation_item_from_invoice(
            self._make_inv(), now=NOW, create_enabled=False, publish_enabled=False,
        )
        ctx.moyklass.get_user_subscriptions.assert_not_called()

    # --- test 73: enabled=True + user in allowlist → sync called ---

    def test_73_flag_true_user_in_allowlist_mk_called(self):
        """enabled=True + mk_user_id in allowlist → MK subscriptions fetched."""
        ctx, _ = self._make_ctx_with_flags(True, ("9748998",))
        ctx._process_single_automation_item_from_invoice(
            self._make_inv(user_id=9748998), now=NOW, create_enabled=False, publish_enabled=False,
        )
        ctx.moyklass.get_user_subscriptions.assert_called_once()

    # --- test 74: enabled=True + user not in allowlist → no MK call ---

    def test_74_flag_true_user_not_in_allowlist_no_mk_call(self):
        """enabled=True + mk_user_id NOT in allowlist → no MK call."""
        ctx, _ = self._make_ctx_with_flags(True, ("99999",))
        ctx._process_single_automation_item_from_invoice(
            self._make_inv(user_id=9748998), now=NOW, create_enabled=False, publish_enabled=False,
        )
        ctx.moyklass.get_user_subscriptions.assert_not_called()

    # --- test 75: enabled=True + empty allowlist → fail-closed ---

    def test_75_empty_allowlist_fail_closed(self):
        """enabled=True + empty allowlist → fail-closed, no MK call."""
        ctx, _ = self._make_ctx_with_flags(True, ())
        ctx._process_single_automation_item_from_invoice(
            self._make_inv(), now=NOW, create_enabled=False, publish_enabled=False,
        )
        ctx.moyklass.get_user_subscriptions.assert_not_called()

    # --- test 76: manual button works when flag=False ---

    def test_76_manual_sync_works_flag_false(self):
        """Manual sync endpoint ignores feature flag (flag=False must not block it)."""
        st = _tmp_storage()
        ctx = _make_ctx(st, role="operations")
        ctx.settings.payment_mk_subscription_terms_sync_enabled = False
        ctx.settings.payment_mk_subscription_terms_sync_user_ids = ()
        ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([_mk_sub(price=57.25)])
        _upsert_terms(st, "9748998", 5725)
        r = ctx.payment_client_terms_sync(_auth(), "9748998", {})
        self.assertTrue(r.get("ok"), f"Manual sync must succeed even when flag=False: {r}")

    # --- test 77: manual button works for user outside allowlist ---

    def test_77_manual_sync_works_user_outside_allowlist(self):
        """Manual sync ignores allowlist — works for any authorised admin."""
        st = _tmp_storage()
        ctx = _make_ctx(st, role="operations")
        ctx.settings.payment_mk_subscription_terms_sync_enabled = True
        ctx.settings.payment_mk_subscription_terms_sync_user_ids = ("OTHER_USER",)
        ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([_mk_sub(price=57.25)])
        _upsert_terms(st, "9748998", 5725)
        r = ctx.payment_client_terms_sync(_auth(), "9748998", {})
        self.assertTrue(r.get("ok"), f"Manual sync must work outside allowlist: {r}")

    # --- test 78: new invoice → sync called exactly once ---

    def test_78_new_invoice_triggers_sync_once(self):
        """New invoice for allowlist user triggers auto-sync exactly once."""
        ctx, _ = self._make_ctx_with_flags(True, ("9748998",))
        ctx._process_single_automation_item_from_invoice(
            self._make_inv(inv_id=78001, user_id=9748998), now=NOW,
            create_enabled=False, publish_enabled=False,
        )
        self.assertEqual(ctx.moyklass.get_user_subscriptions.call_count, 1)

    # --- test 79: already-processed invoice → no sync ---

    def test_79_processed_invoice_no_sync(self):
        """Invoice already in payment_options_created stage must not re-trigger sync."""
        import web_app_server as _srv
        st = _tmp_storage()
        ctx = _srv.MiniAppContext.__new__(_srv.MiniAppContext)
        ctx.storage = st
        ctx.settings = MagicMock()
        ctx.settings.payment_mk_subscription_terms_sync_enabled = True
        ctx.settings.payment_mk_subscription_terms_sync_user_ids = ("5555",)
        ctx.moyklass = MagicMock()
        ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([])
        # Pre-create the item and advance its stage
        item = st.upsert_automation_item("79001", "5555", "Test", "{}", NOW)
        st.update_automation_item_stage(item["id"], "payment_options_created", now=NOW)
        # Second call with same invoice — stage is already payment_options_created → is_new=False
        inv = {"id": 79001, "userId": 5555, "price": 100.0, "payed": 0.0}
        ctx._process_single_automation_item_from_invoice(
            inv, now=NOW, create_enabled=False, publish_enabled=False,
        )
        ctx.moyklass.get_user_subscriptions.assert_not_called()

    # --- test 80: new_source → terms updated atomically (auto path) ---

    def test_80_new_source_updates_terms_atomically(self):
        """Auto-sync new_source state must update both base and source fields in one audit."""
        st = _tmp_storage()
        ctx = _make_ctx(st, role="operations")
        ctx.settings.payment_mk_subscription_terms_sync_enabled = True
        ctx.settings.payment_mk_subscription_terms_sync_user_ids = ("u80",)
        _upsert_terms(st, "u80", 5000)
        ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([
            _mk_sub(sub_id=8001, price=58.25, sell_date="2026-07-01"),
        ])
        ctx._process_single_automation_item_from_invoice(
            {"id": 800001, "userId": "u80", "price": 58.25, "payed": 0.0},
            now=NOW, create_enabled=False, publish_enabled=False,
        )
        row = st.get_payment_client_terms("u80")
        self.assertIsNotNone(row)
        self.assertEqual(int(row["base_price_minor"]), 5825)
        self.assertEqual(str(row.get("source_subscription_id") or ""), "8001")

    # --- test 81: unchanged → no audit ---

    def test_81_unchanged_no_audit(self):
        """unchanged state: no terms_updated audit should be created."""
        st = _tmp_storage()
        ctx = _make_ctx(st, role="operations")
        ctx.settings.payment_mk_subscription_terms_sync_enabled = True
        ctx.settings.payment_mk_subscription_terms_sync_user_ids = ("u81",)
        # Save terms from sub 9001 so the state becomes unchanged
        _upsert_terms(st, "u81", 5725)
        sub = _mk_sub(sub_id=9001, price=57.25, sell_date="2026-07-01")
        _internal_auth = {"_internal": True, "role": "operations", "user_id": 0}
        ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([sub])
        # First: sync to establish sub 9001 as source
        ctx._sync_payment_terms_from_moyklass("u81", _internal_auth)
        audit_before = st.list_payment_pricing_audit("u81")
        count_before = len(audit_before) if audit_before else 0
        # Now same sub → unchanged
        ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([sub])
        ctx._sync_payment_terms_from_moyklass("u81", _internal_auth)
        audit_after = st.list_payment_pricing_audit("u81")
        count_after = len(audit_after) if audit_after else 0
        self.assertEqual(count_before, count_after, "unchanged state must not create audit")

    # --- test 82: not_found → flow continues ---

    def test_82_not_found_flow_continues(self):
        """not_found: auto-sync must not break invoice flow."""
        ctx, _ = self._make_ctx_with_flags(True, ("9748998",))
        ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([])
        try:
            ctx._process_single_automation_item_from_invoice(
                self._make_inv(inv_id=82001, user_id=9748998), now=NOW,
                create_enabled=False, publish_enabled=False,
            )
        except Exception as e:
            self.fail(f"not_found must not raise: {e}")

    # --- test 83: ambiguous → flow continues ---

    def test_83_ambiguous_flow_continues(self):
        """ambiguous: auto-sync must not break invoice flow."""
        ctx, _ = self._make_ctx_with_flags(True, ("9748998",))
        ctx.moyklass.get_user_subscriptions.return_value = _mk_result_ok([
            _mk_sub(sub_id=1, price=57.25, sell_date="2026-07-01"),
            _mk_sub(sub_id=2, price=57.25, sell_date="2026-07-01"),
        ])
        try:
            ctx._process_single_automation_item_from_invoice(
                self._make_inv(inv_id=83001, user_id=9748998), now=NOW,
                create_enabled=False, publish_enabled=False,
            )
        except Exception as e:
            self.fail(f"ambiguous must not raise: {e}")

    # --- test 84: api_error → flow continues ---

    def test_84_api_error_flow_continues(self):
        """api_error: MK exception must be caught and invoice flow must continue."""
        ctx, _ = self._make_ctx_with_flags(True, ("9748998",))
        ctx.moyklass.get_user_subscriptions.side_effect = RuntimeError("mk_down")
        try:
            ctx._process_single_automation_item_from_invoice(
                self._make_inv(inv_id=84001, user_id=9748998), now=NOW,
                create_enabled=False, publish_enabled=False,
            )
        except Exception as e:
            self.fail(f"api_error must not propagate: {e}")

    # --- test 85: sync method does not touch payment intents ---

    def test_85_sync_does_not_modify_payment_intents(self):
        """_sync_payment_terms_from_moyklass must not call any payment intent update."""
        idx = self.server.find("def _sync_payment_terms_from_moyklass(")
        next_def = self.server.find("\n    def ", idx + 1)
        method = self.server[idx:next_def]
        for fn in ("update_payment_intent", "update_intent_status", "update_intent_amount",
                   "withdraw_payment_intent", "create_payment_intent"):
            self.assertNotIn(fn, method,
                             f"Sync must not touch payment intents: {fn}")

    # --- test 86: payment intent uses invoice price, not terms price ---

    def test_86_intent_uses_invoice_price(self):
        """_automation_create_intent must derive amount from invoice price, not terms."""
        idx = self.server.find("def _automation_create_intent(")
        next_def = self.server.find("\n    def ", idx + 1)
        method = self.server[idx:next_def]
        self.assertIn('inv.get("price")', method, "Intent creation must read price from invoice")
        self.assertNotIn("get_payment_client_terms", method,
                         "Intent creation must not read payment_client_terms")

    # --- test 87: duplicate payment intent not created ---

    def test_87_no_duplicate_intent(self):
        """Deduplication guard must prevent duplicate intents for the same invoice."""
        srv = self.server
        # find_all_active_intents_by_invoice is the dedup guard
        self.assertIn("find_all_active_intents_by_invoice", srv)
        # It must appear before create_payment_intent in the pipeline method
        pipeline_idx = srv.find("def _process_single_automation_item_from_invoice(")
        dedup_idx = srv.find("find_all_active_intents_by_invoice", pipeline_idx)
        create_idx = srv.find("_automation_create_intent(", pipeline_idx)
        self.assertLess(dedup_idx, create_idx,
                        "Dedup guard must run before intent creation")

    # --- test 88: BePaid not called from sync ---

    def test_88_bepaid_not_called_from_sync(self):
        """_sync_payment_terms_from_moyklass must not call bePaid."""
        idx = self.server.find("def _sync_payment_terms_from_moyklass(")
        next_def = self.server.find("\n    def ", idx + 1)
        method = self.server[idx:next_def]
        for bepaid in ("bepaid_client", "self.bepaid", "create_payment", "BePaid"):
            self.assertNotIn(bepaid, method,
                             f"Sync must not call bePaid: {bepaid}")

    # --- test 89: MoyKlass write API not called from sync ---

    def test_89_moyklass_write_not_called_from_sync(self):
        """_sync_payment_terms_from_moyklass must not call MoyKlass write endpoints."""
        idx = self.server.find("def _sync_payment_terms_from_moyklass(")
        next_def = self.server.find("\n    def ", idx + 1)
        method = self.server[idx:next_def]
        for write_fn in ("create_invoice", "update_invoice", "create_subscription",
                         "update_subscription", "delete_invoice"):
            self.assertNotIn(write_fn, method,
                             f"Sync must not call MK write API: {write_fn}")

    # --- test 90: auto audit reason differs from manual ---

    def test_90_auto_audit_reason_distinct(self):
        """Auto-sync must use audit_reason='newer_moyklass_subscription_auto'."""
        self.assertIn("newer_moyklass_subscription_auto", self.server)
        # Manual path still uses the original reason
        self.assertIn('"newer_moyklass_subscription"', self.server)
        # They must be different strings
        self.assertNotEqual("newer_moyklass_subscription_auto", "newer_moyklass_subscription")

    # --- test 91: log contains result and mk_invoice_id ---

    def test_91_log_contains_result_and_invoice_id(self):
        """Auto-sync log format must include result= and mk_invoice_id."""
        self.assertIn("payment_event=payment_terms_auto_sync", self.server)
        self.assertIn("mk_invoice_id=%s", self.server)
        self.assertIn("result=%s", self.server)

    # --- test 92: no tokens or raw API response in auto-sync log strings ---

    def test_92_no_secrets_in_log_strings(self):
        """Auto-sync log strings must not expose API tokens or raw responses."""
        idx = self.server.find("payment_event=payment_terms_auto_sync")
        # Check 500 chars around each occurrence
        while idx != -1:
            segment = self.server[max(0, idx - 20):idx + 500]
            for secret in ("api_key", "secret_key", "token", "raw_response", "api_response"):
                self.assertNotIn(secret, segment.lower(),
                                 f"Log must not expose secrets: '{secret}'")
            idx = self.server.find("payment_event=payment_terms_auto_sync", idx + 1)

    # --- test 93: default flag stays False ---

    def test_93_default_flag_false(self):
        """payment_mk_subscription_terms_sync_enabled must default to False in config."""
        self.assertIn("payment_mk_subscription_terms_sync_enabled: bool = False", self.cfg)

    # --- test 94: default allowlist is empty ---

    def test_94_default_allowlist_empty(self):
        """payment_mk_subscription_terms_sync_user_ids must default to empty tuple in config."""
        self.assertIn("payment_mk_subscription_terms_sync_user_ids: tuple = ()", self.cfg)

    # --- test 95: cache-bust v7.1.3 ---

    def test_95_cache_bust_v714(self):
        """Cache-bust strings must be v7.1.6.1."""
        self.assertIn("v=7.1.6.1", self.html, "index.html cache-bust must be v=7.1.6.1")
        self.assertIn("v7.1.6.1", self.js, "app.js version must be v7.1.6.1")

    # --- test 96: food module not changed ---

    def test_96_food_module_not_changed(self):
        """_sync_payment_terms_from_moyklass must not touch food tables."""
        idx = self.server.find("def _sync_payment_terms_from_moyklass(")
        next_def = self.server.find("\n    def ", idx + 1)
        method = self.server[idx:next_def]
        for food in ("food_order", "food_menu", "food_children", "meal_option"):
            self.assertNotIn(food, method,
                             f"Sync must not reference food module: {food}")


if __name__ == "__main__":
    unittest.main()
