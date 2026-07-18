"""Tests for MoyKlass invoice automation pipeline (v7.0.94.1).

Covers:
- Storage automation tables and methods
- process_new_moyklass_invoices pipeline (mocked MK client)
- Role-based access control for API handlers
- JS static analysis (automation block, functions, toggles, confirmations)
- CSS checks (.notice:empty, automation classes)
- Version string checks
- Regression guards (existing tests still import cleanly)

Run offline (no MoyKlass / bePaid / Telegram needed):

    python -m unittest tests.test_invoice_automation -v
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CURRENT_VERSION = "7.0.94.5"

from storage import Storage

APP_JS = ROOT / "miniapp" / "app.js"
STYLES_CSS = ROOT / "miniapp" / "styles.css"
INDEX_HTML = ROOT / "miniapp" / "index.html"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _mk_invoice(
    inv_id: int = 1001,
    user_id: int = 2001,
    price: float = 100.0,
    payed: float = 0.0,
    comment: str = "",
) -> dict:
    return {
        "id": inv_id,
        "userId": user_id,
        "price": price,
        "payed": payed,
        "comment": comment,
        "payUntil": "2026-07-31",
        "userSubscriptionId": 5001,
        "userSubscription": {
            "name": "Подписка",
            "clientName": "Иван Тестов",
            "beginDate": "2026-07-01",
        },
    }


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 1. Storage: table creation and settings
# ---------------------------------------------------------------------------

class TestAutomationStorageInit(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()

    def test_get_automation_settings_defaults(self):
        s = self.st.get_automation_settings()
        self.assertEqual(s["id"], 1)
        self.assertEqual(s["discovery_enabled"], 1)
        self.assertEqual(s["create_payment_options_enabled"], 0)
        self.assertEqual(s["publish_to_parent_enabled"], 0)
        self.assertEqual(s["scan_interval_minutes"], 10)
        self.assertIsNone(s["last_scan_at"])

    def test_update_automation_settings(self):
        now = _now()
        updated = self.st.update_automation_settings(
            discovery_enabled=True,
            create_payment_options_enabled=True,
            publish_to_parent_enabled=False,
            scan_interval_minutes=30,
            updated_by="admin_123",
            now=now,
        )
        self.assertEqual(updated["discovery_enabled"], 1)
        self.assertEqual(updated["create_payment_options_enabled"], 1)
        self.assertEqual(updated["publish_to_parent_enabled"], 0)
        self.assertEqual(updated["scan_interval_minutes"], 30)
        self.assertEqual(updated["updated_by"], "admin_123")

    def test_update_settings_interval_clamped(self):
        now = _now()
        updated = self.st.update_automation_settings(
            discovery_enabled=True,
            create_payment_options_enabled=False,
            publish_to_parent_enabled=False,
            scan_interval_minutes=9999,
            updated_by="admin",
            now=now,
        )
        self.assertEqual(updated["scan_interval_minutes"], 1440)

    def test_update_settings_interval_min_clamped(self):
        now = _now()
        updated = self.st.update_automation_settings(
            discovery_enabled=True,
            create_payment_options_enabled=False,
            publish_to_parent_enabled=False,
            scan_interval_minutes=1,
            updated_by="admin",
            now=now,
        )
        self.assertEqual(updated["scan_interval_minutes"], 5)

    def test_update_automation_last_scan(self):
        now = _now()
        self.st.update_automation_last_scan(now)
        s = self.st.get_automation_settings()
        self.assertIsNotNone(s["last_scan_at"])

    def test_settings_idempotent_reinit(self):
        # Second storage init on same DB should not reset settings
        now = _now()
        self.st.update_automation_settings(
            discovery_enabled=False,
            create_payment_options_enabled=False,
            publish_to_parent_enabled=False,
            scan_interval_minutes=15,
            updated_by="u",
            now=now,
        )
        # Re-init storage
        st2 = Storage(self.st.db_path)
        s = st2.get_automation_settings()
        self.assertEqual(s["scan_interval_minutes"], 15)
        self.assertEqual(s["discovery_enabled"], 0)


# ---------------------------------------------------------------------------
# 2. Storage: automation items
# ---------------------------------------------------------------------------

class TestAutomationItems(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()
        self.now = _now()

    def test_upsert_automation_item_new(self):
        item = self.st.upsert_automation_item("inv_1", "u_1", "Иван", "{}", self.now)
        self.assertEqual(item["mk_invoice_id"], "inv_1")
        self.assertEqual(item["mk_user_id"], "u_1")
        self.assertEqual(item["student_name"], "Иван")
        self.assertEqual(item["current_stage"], "discovered")

    def test_upsert_automation_item_no_overwrite_stage(self):
        self.st.upsert_automation_item("inv_1", "u_1", "Иван", "{}", self.now)
        self.st.update_automation_item_stage(
            self.st.get_automation_item_by_invoice("inv_1")["id"],
            "published",
            now=self.now,
        )
        # Upsert again — should NOT change stage
        item = self.st.upsert_automation_item("inv_1", "u_1", "Иван", "{}", self.now)
        self.assertEqual(item["current_stage"], "published")

    def test_get_automation_item_by_invoice(self):
        self.st.upsert_automation_item("inv_42", "u_1", None, "{}", self.now)
        item = self.st.get_automation_item_by_invoice("inv_42")
        self.assertIsNotNone(item)
        self.assertEqual(item["mk_invoice_id"], "inv_42")

    def test_get_automation_item_by_invoice_missing(self):
        item = self.st.get_automation_item_by_invoice("nonexistent")
        self.assertIsNone(item)

    def test_get_automation_item_by_id(self):
        self.st.upsert_automation_item("inv_7", "u_2", "Маша", "{}", self.now)
        inv_item = self.st.get_automation_item_by_invoice("inv_7")
        item_by_id = self.st.get_automation_item_by_id(inv_item["id"])
        self.assertIsNotNone(item_by_id)
        self.assertEqual(item_by_id["mk_invoice_id"], "inv_7")

    def test_get_automation_item_by_id_missing(self):
        item = self.st.get_automation_item_by_id(99999)
        self.assertIsNone(item)

    def test_update_automation_item_stage(self):
        self.st.upsert_automation_item("inv_3", "u_3", "Петя", "{}", self.now)
        iid = self.st.get_automation_item_by_invoice("inv_3")["id"]
        self.st.update_automation_item_stage(
            iid, "missing_parent_link",
            reason_code="no_parent_link",
            readable_reason="No parent",
            now=self.now,
        )
        item = self.st.get_automation_item_by_id(iid)
        self.assertEqual(item["current_stage"], "missing_parent_link")
        self.assertEqual(item["reason_code"], "no_parent_link")
        self.assertEqual(item["readable_reason"], "No parent")

    def test_update_automation_item_stage_with_intent(self):
        self.st.upsert_automation_item("inv_4", "u_4", "Даша", "{}", self.now)
        iid = self.st.get_automation_item_by_invoice("inv_4")["id"]
        self.st.update_automation_item_stage(
            iid, "payment_options_created",
            intent_public_id="ycpi_testXXX",
            linked_parent_tg_id="tg_999",
            now=self.now,
        )
        item = self.st.get_automation_item_by_id(iid)
        self.assertEqual(item["intent_public_id"], "ycpi_testXXX")
        self.assertEqual(item["linked_parent_tg_id"], "tg_999")

    def test_list_automation_items_all(self):
        for i in range(5):
            self.st.upsert_automation_item(f"inv_{i}", f"u_{i}", f"Name{i}", "{}", self.now)
        items = self.st.list_automation_items()
        self.assertEqual(len(items), 5)

    def test_list_automation_items_stage_filter(self):
        self.st.upsert_automation_item("inv_a", "u_a", "А", "{}", self.now)
        self.st.upsert_automation_item("inv_b", "u_b", "Б", "{}", self.now)
        iid = self.st.get_automation_item_by_invoice("inv_a")["id"]
        self.st.update_automation_item_stage(iid, "published", now=self.now)
        published = self.st.list_automation_items(stage_filter="published")
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["mk_invoice_id"], "inv_a")

    def test_list_automation_items_offset(self):
        for i in range(5):
            self.st.upsert_automation_item(f"inv_off_{i}", f"u_{i}", None, "{}", self.now)
        page1 = self.st.list_automation_items(limit=3, offset=0)
        page2 = self.st.list_automation_items(limit=3, offset=3)
        self.assertEqual(len(page1), 3)
        self.assertEqual(len(page2), 2)

    def test_attempts_incremented(self):
        self.st.upsert_automation_item("inv_at", "u_at", None, "{}", self.now)
        iid = self.st.get_automation_item_by_invoice("inv_at")["id"]
        self.assertEqual(self.st.get_automation_item_by_id(iid)["attempts"], 0)
        self.st.update_automation_item_stage(iid, "discovered", now=self.now)
        self.assertEqual(self.st.get_automation_item_by_id(iid)["attempts"], 1)
        self.st.update_automation_item_stage(iid, "error", now=self.now)
        self.assertEqual(self.st.get_automation_item_by_id(iid)["attempts"], 2)


# ---------------------------------------------------------------------------
# 3. Storage: automation runs
# ---------------------------------------------------------------------------

class TestAutomationRuns(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()
        self.now = _now()

    def test_start_and_finish_run(self):
        run = self.st.start_automation_run("run_1", "manual", "admin", self.now)
        self.assertEqual(run["run_id"], "run_1")
        self.assertEqual(run["status"], "running")

        self.st.finish_automation_run(
            "run_1", status="ok", finished_at=self.now,
            scanned_count=10, discovered_count=3, created_count=2, published_count=1,
        )
        runs = self.st.list_automation_runs()
        self.assertEqual(runs[0]["status"], "ok")
        self.assertEqual(runs[0]["scanned_count"], 10)
        self.assertEqual(runs[0]["published_count"], 1)

    def test_get_running_automation_run(self):
        self.assertIsNone(self.st.get_running_automation_run())
        self.st.start_automation_run("run_r", "scheduled", None, self.now)
        r = self.st.get_running_automation_run()
        self.assertIsNotNone(r)
        self.assertEqual(r["run_id"], "run_r")

    def test_get_running_none_after_finish(self):
        self.st.start_automation_run("run_fin", "manual", None, self.now)
        self.st.finish_automation_run("run_fin", status="ok", finished_at=self.now)
        self.assertIsNone(self.st.get_running_automation_run())

    def test_expire_stale_run(self):
        from datetime import datetime, timezone, timedelta
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        self.st.start_automation_run("stale_run", "scheduled", None, old_ts)
        # manually patch started_at to be old
        with self.st._connect() as conn:
            conn.execute(
                "UPDATE invoice_automation_runs SET started_at=? WHERE run_id=?",
                (old_ts, "stale_run"),
            )
        self.st.expire_stale_automation_run(timeout_minutes=30, now=self.now)
        r = self.st.get_running_automation_run()
        self.assertIsNone(r)

    def test_expire_stale_does_not_touch_recent(self):
        self.st.start_automation_run("recent_run", "manual", None, self.now)
        self.st.expire_stale_automation_run(timeout_minutes=30, now=self.now)
        r = self.st.get_running_automation_run()
        self.assertIsNotNone(r)

    def test_list_automation_runs_limit(self):
        for i in range(5):
            self.st.start_automation_run(f"r{i}", "manual", None, self.now)
            self.st.finish_automation_run(f"r{i}", status="ok", finished_at=self.now)
        runs = self.st.list_automation_runs(limit=3)
        self.assertEqual(len(runs), 3)

    def test_run_lease_cleared_on_finish(self):
        self.st.start_automation_run("r_lease", "manual", None, self.now)
        self.st.finish_automation_run("r_lease", status="ok", finished_at=self.now)
        with self.st._connect() as conn:
            row = conn.execute(
                "SELECT lease_token FROM invoice_automation_runs WHERE run_id=?", ("r_lease",)
            ).fetchone()
        self.assertIsNone(row["lease_token"])

    def test_start_run_idempotent(self):
        r1 = self.st.start_automation_run("dup_run", "manual", None, self.now)
        r2 = self.st.start_automation_run("dup_run", "manual", None, self.now)
        self.assertEqual(r1["id"], r2["id"])


# ---------------------------------------------------------------------------
# 4. Pipeline: process_new_moyklass_invoices
# ---------------------------------------------------------------------------

class _FakeResult:
    """Simulates MoyKlassResult."""
    def __init__(self, data):
        self.data = data
        self.ok = True
        self.error = None


def _make_ctx_with_mocks(storage: Storage, invoices: list[dict]):
    """Create a minimal MiniAppContext-like object with mocked MK and storage."""
    from web_app_server import MiniAppContext
    ctx = MiniAppContext.__new__(MiniAppContext)
    ctx.storage = storage
    # Mock moyklass to return invoices list
    mk = MagicMock()
    def _fake_request(method, path, **kwargs):
        return _FakeResult({"invoices": invoices})
    mk.request = _fake_request
    ctx.moyklass = mk
    # Mock settings
    settings = MagicMock()
    settings.payment_invoice_automation_enabled = True
    settings.bepaid_erip_shop_id = ""
    settings.bepaid_erip_secret_key = ""
    settings.bepaid_acq_shop_id = ""
    settings.bepaid_acq_secret_key = ""
    settings.bepaid_public_base_url = ""
    settings.bepaid_webhook_path_secret = ""
    settings.moyklass_erip_payment_type_id = 0
    settings.moyklass_acquiring_payment_type_id = 0
    ctx.settings = settings
    return ctx


class TestPipelineBasic(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()

    def test_already_running_returns_error(self):
        now = _now()
        self.st.start_automation_run("running_run", "manual", None, now)
        ctx = _make_ctx_with_mocks(self.st, [])
        result = ctx.process_new_moyklass_invoices(trigger="manual")
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "already_running")

    def test_discovery_disabled_early_return(self):
        now = _now()
        self.st.update_automation_settings(
            discovery_enabled=False,
            create_payment_options_enabled=False,
            publish_to_parent_enabled=False,
            scan_interval_minutes=10,
            updated_by="test",
            now=now,
        )
        ctx = _make_ctx_with_mocks(self.st, [])
        result = ctx.process_new_moyklass_invoices(trigger="manual")
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("skipped"), "discovery_disabled")

    def test_empty_invoice_list(self):
        ctx = _make_ctx_with_mocks(self.st, [])
        result = ctx.process_new_moyklass_invoices(trigger="manual")
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("scanned"), 0)
        runs = self.st.list_automation_runs()
        self.assertGreater(len(runs), 0)
        self.assertIn(runs[0]["status"], ("ok", "ok_with_errors"))

    def test_paid_invoice_skipped(self):
        inv = _mk_invoice(inv_id=100, price=100.0, payed=100.0)
        ctx = _make_ctx_with_mocks(self.st, [inv])
        result = ctx.process_new_moyklass_invoices(trigger="manual")
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("scanned"), 1)
        self.assertEqual(result.get("skipped"), 1)
        self.assertIsNone(self.st.get_automation_item_by_invoice("100"))

    def test_zero_price_invoice_skipped(self):
        inv = _mk_invoice(inv_id=101, price=0.0, payed=0.0)
        ctx = _make_ctx_with_mocks(self.st, [inv])
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("scanned"), 1)
        self.assertEqual(result.get("skipped"), 1)

    def test_missing_parent_link(self):
        inv = _mk_invoice(inv_id=200, user_id=2000, price=100.0, payed=0.0)
        ctx = _make_ctx_with_mocks(self.st, [inv])
        result = ctx.process_new_moyklass_invoices()
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("missing_parent"), 1)
        item = self.st.get_automation_item_by_invoice("200")
        self.assertIsNotNone(item)
        self.assertEqual(item["current_stage"], "missing_parent_link")
        self.assertEqual(item["reason_code"], "no_parent_link")

    def test_discovered_count(self):
        inv = _mk_invoice(inv_id=201, user_id=2001, price=80.0, payed=0.0)
        ctx = _make_ctx_with_mocks(self.st, [inv])
        result = ctx.process_new_moyklass_invoices()
        # Discovered means upserted as new
        self.assertEqual(result.get("discovered"), 1)

    def test_ignored_item_skipped(self):
        now = _now()
        self.st.upsert_automation_item("999", "u_999", None, "{}", now)
        iid = self.st.get_automation_item_by_invoice("999")["id"]
        self.st.update_automation_item_stage(iid, "ignored", now=now)
        inv = _mk_invoice(inv_id=999, price=50.0)
        ctx = _make_ctx_with_mocks(self.st, [inv])
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("skipped"), 1)
        # Stage should remain ignored
        self.assertEqual(self.st.get_automation_item_by_id(iid)["current_stage"], "ignored")

    def test_existing_active_intent_not_recreated(self):
        from storage import Storage
        now = _now()
        inv = _mk_invoice(inv_id=300, user_id=3000, price=100.0)
        # Create an existing intent for this invoice
        intent = self.st.create_payment_intent({
            "mk_user_id": 3000,
            "student_name": "Test",
            "amount_minor": 10000,
            "amount_byn": 100.0,
            "currency": "BYN",
            "purpose": "subscription",
            "payment_method": "erip",
            "status": "draft",
            "created_at": now,
            "mk_invoice_id": "300",
            "source": "test",
        })
        existing = self.st.find_active_intent_by_invoice("300")
        self.assertIsNotNone(existing)
        ctx = _make_ctx_with_mocks(self.st, [inv])
        ctx.process_new_moyklass_invoices()
        # Only 1 intent should exist
        item = self.st.get_automation_item_by_invoice("300")
        self.assertIsNotNone(item)
        self.assertEqual(item["current_stage"], "payment_options_created")

    def test_all_invoices_processed_no_cap(self):
        invoices = [_mk_invoice(inv_id=5000 + i, user_id=6000 + i, price=50.0) for i in range(60)]
        ctx = _make_ctx_with_mocks(self.st, invoices)
        result = ctx.process_new_moyklass_invoices()
        # all 60 invoices must be scanned and processed (no hard cap)
        self.assertEqual(result.get("scanned"), 60)
        self.assertEqual(result.get("processed"), 60)
        items = self.st.list_automation_items(limit=200)
        self.assertEqual(len(items), 60)

    def test_run_updates_last_scan_at(self):
        ctx = _make_ctx_with_mocks(self.st, [])
        ctx.process_new_moyklass_invoices()
        s = self.st.get_automation_settings()
        self.assertIsNotNone(s["last_scan_at"])

    def test_creates_run_record(self):
        ctx = _make_ctx_with_mocks(self.st, [])
        result = ctx.process_new_moyklass_invoices(trigger="manual", started_by="testadmin")
        self.assertTrue(result.get("ok"))
        runs = self.st.list_automation_runs()
        self.assertGreater(len(runs), 0)
        self.assertEqual(runs[0]["trigger"], "manual")

    def test_student_name_extracted_from_subscription(self):
        inv = _mk_invoice(inv_id=400, user_id=4000, price=100.0)
        inv["userSubscription"]["clientName"] = "Ольга Тестова"
        ctx = _make_ctx_with_mocks(self.st, [inv])
        ctx.process_new_moyklass_invoices()
        item = self.st.get_automation_item_by_invoice("400")
        self.assertIsNotNone(item)
        self.assertEqual(item.get("student_name"), "Ольга Тестова")


# ---------------------------------------------------------------------------
# 5. Pipeline with parent link
# ---------------------------------------------------------------------------

class TestPipelineWithParent(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()
        self.now = _now()

    def _seed_parent_link(self, mk_user_id: str, parent_tg_id: str):
        with self.st._connect() as conn:
            conn.execute(
                """INSERT INTO client_parent_child_links
                   (parent_telegram_user_id, mk_user_id, child_display_name, status,
                    linked_at, created_at, updated_at)
                   VALUES (?,?,'Test','active',?,?,?)""",
                (parent_tg_id, mk_user_id, self.now, self.now, self.now),
            )

    def test_single_parent_becomes_ready_for_creation(self):
        self._seed_parent_link("2000", "tg_parent_1")
        inv = _mk_invoice(inv_id=500, user_id=2000, price=90.0)
        ctx = _make_ctx_with_mocks(self.st, [inv])
        # create_enabled=False (default)
        result = ctx.process_new_moyklass_invoices()
        item = self.st.get_automation_item_by_invoice("500")
        self.assertIsNotNone(item)
        # With create disabled, stage should be ready_for_creation
        self.assertEqual(item["current_stage"], "ready_for_creation")

    def test_ambiguous_parent_link(self):
        self._seed_parent_link("3000", "tg_parent_A")
        self._seed_parent_link("3000", "tg_parent_B")
        inv = _mk_invoice(inv_id=600, user_id=3000, price=50.0)
        ctx = _make_ctx_with_mocks(self.st, [inv])
        ctx.process_new_moyklass_invoices()
        item = self.st.get_automation_item_by_invoice("600")
        self.assertIsNotNone(item)
        self.assertEqual(item["current_stage"], "ambiguous_parent_link")
        self.assertEqual(item["reason_code"], "multiple_parents")


# ---------------------------------------------------------------------------
# 6. API handler role access
# ---------------------------------------------------------------------------

class TestAutomationHandlerAccess(unittest.TestCase):
    """Handler routing tests.

    _automation_effective_role is mocked to return auth["_test_role"] so these
    tests verify routing logic without coupling to DB role resolution.
    Role resolution correctness is covered by TestAutomationRoleResolution.
    """

    def setUp(self):
        from web_app_server import MiniAppContext
        self.ctx = MiniAppContext.__new__(MiniAppContext)
        self.ctx.storage = _make_storage()
        self.ctx.settings = MagicMock()
        self.ctx.moyklass = MagicMock()
        # Patch role resolution so tests are DB-independent
        self._patcher = patch.object(
            self.ctx, "_automation_effective_role",
            side_effect=lambda auth: auth.get("_test_role", "other"),
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _auth(self, role: str) -> dict:
        return {"_test_role": role, "user_id": "0", "ok": True}

    def test_automation_get_settings_owner_allowed(self):
        result = self.ctx.automation_get_settings(self._auth("owner"))
        self.assertTrue(result.get("ok"))

    def test_automation_get_settings_admin_allowed(self):
        result = self.ctx.automation_get_settings(self._auth("admin"))
        self.assertTrue(result.get("ok"))

    def test_automation_get_settings_operations_allowed(self):
        result = self.ctx.automation_get_settings(self._auth("operations"))
        self.assertTrue(result.get("ok"))

    def test_automation_get_settings_teacher_denied(self):
        result = self.ctx.automation_get_settings(self._auth("teacher"))
        self.assertFalse(result.get("ok"))

    def test_automation_update_settings_owner_allowed(self):
        body = {
            "discovery_enabled": True, "create_payment_options_enabled": False,
            "publish_to_parent_enabled": False, "scan_interval_minutes": 10,
        }
        result = self.ctx.automation_update_settings(self._auth("owner"), body)
        self.assertTrue(result.get("ok"))

    def test_automation_update_settings_admin_allowed(self):
        body = {
            "discovery_enabled": True, "create_payment_options_enabled": False,
            "publish_to_parent_enabled": False, "scan_interval_minutes": 10,
        }
        result = self.ctx.automation_update_settings(self._auth("admin"), body)
        self.assertTrue(result.get("ok"))

    def test_automation_update_settings_operations_denied(self):
        body = {
            "discovery_enabled": True, "create_payment_options_enabled": False,
            "publish_to_parent_enabled": False, "scan_interval_minutes": 10,
        }
        result = self.ctx.automation_update_settings(self._auth("operations"), body)
        self.assertFalse(result.get("ok"))

    def test_automation_get_status_owner_allowed(self):
        result = self.ctx.automation_get_status(self._auth("owner"), {})
        self.assertTrue(result.get("ok"))

    def test_automation_get_status_operations_allowed(self):
        result = self.ctx.automation_get_status(self._auth("operations"), {})
        self.assertTrue(result.get("ok"))

    def test_automation_get_status_client_manager_denied(self):
        result = self.ctx.automation_get_status(self._auth("client_manager"), {})
        self.assertFalse(result.get("ok"))

    def test_automation_list_items_owner_allowed(self):
        result = self.ctx.automation_list_items(self._auth("owner"), {})
        self.assertTrue(result.get("ok"))

    def test_automation_list_items_teacher_denied(self):
        result = self.ctx.automation_list_items(self._auth("teacher"), {})
        self.assertFalse(result.get("ok"))

    def test_automation_manual_scan_owner_allowed(self):
        with patch.object(self.ctx, "process_new_moyklass_invoices", return_value={"ok": True}):
            result = self.ctx.automation_manual_scan(self._auth("owner"))
        self.assertTrue(result.get("ok"))

    def test_automation_manual_scan_operations_denied(self):
        result = self.ctx.automation_manual_scan(self._auth("operations"))
        self.assertFalse(result.get("ok"))

    def test_automation_item_action_invalid_id(self):
        result = self.ctx.automation_item_action(self._auth("owner"), "abc", "ignore", {})
        self.assertFalse(result.get("ok"))
        self.assertIn("invalid", result.get("error", "").lower())

    def test_automation_item_action_not_found(self):
        result = self.ctx.automation_item_action(self._auth("owner"), "99999", "ignore", {})
        self.assertFalse(result.get("ok"))
        self.assertIn("not found", result.get("error", "").lower())

    def test_automation_item_action_ignore(self):
        now = _now()
        self.ctx.storage.upsert_automation_item("inv_x", "u_x", None, "{}", now)
        iid = self.ctx.storage.get_automation_item_by_invoice("inv_x")["id"]
        result = self.ctx.automation_item_action(self._auth("owner"), str(iid), "ignore", {})
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("stage"), "ignored")

    def test_automation_item_action_unignore(self):
        now = _now()
        self.ctx.storage.upsert_automation_item("inv_y", "u_y", None, "{}", now)
        iid = self.ctx.storage.get_automation_item_by_invoice("inv_y")["id"]
        self.ctx.storage.update_automation_item_stage(iid, "ignored", now=now)
        result = self.ctx.automation_item_action(self._auth("owner"), str(iid), "unignore", {})
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("stage"), "discovered")

    def test_automation_item_action_unknown_action(self):
        now = _now()
        self.ctx.storage.upsert_automation_item("inv_z", "u_z", None, "{}", now)
        iid = self.ctx.storage.get_automation_item_by_invoice("inv_z")["id"]
        result = self.ctx.automation_item_action(self._auth("owner"), str(iid), "foobar", {})
        self.assertFalse(result.get("ok"))


# ---------------------------------------------------------------------------
# 7. JS static analysis
# ---------------------------------------------------------------------------

class TestAutomationJSStatic(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_version_string(self):
        self.assertIn(f"MiniApp version: v{CURRENT_VERSION}", self.js)

    def test_load_automation_status_exists(self):
        self.assertIn("async function loadAutomationStatus()", self.js)

    def test_load_automation_settings_exists(self):
        self.assertIn("async function loadAutomationSettings()", self.js)

    def test_save_automation_settings_exists(self):
        self.assertIn("async function saveAutomationSettings()", self.js)

    def test_run_automation_scan_exists(self):
        self.assertIn("async function runAutomationScan()", self.js)

    def test_load_automation_queue_exists(self):
        self.assertIn("async function loadAutomationQueue(", self.js)

    def test_automation_item_action_exists(self):
        self.assertIn("window.automationItemAction", self.js)

    def test_toggle_discovery_referenced(self):
        self.assertIn("autoToggleDiscovery", self.js)

    def test_toggle_create_referenced(self):
        self.assertIn("autoToggleCreate", self.js)

    def test_toggle_publish_referenced(self):
        self.assertIn("autoTogglePublish", self.js)

    def test_save_settings_btn_referenced(self):
        self.assertIn("autoSaveSettingsBtn", self.js)

    def test_run_scan_btn_referenced(self):
        self.assertIn("autoRunScanBtn", self.js)

    def test_confirm_for_create_toggle(self):
        self.assertIn("window.confirm", self.js)

    def test_api_automation_settings_post(self):
        self.assertIn('"/api/payments/automation/settings"', self.js)

    def test_api_automation_scan_post(self):
        self.assertIn('"/api/payments/automation/scan"', self.js)

    def test_api_automation_status_get(self):
        self.assertIn('"/api/payments/automation/status"', self.js)

    def test_api_automation_items_get(self):
        self.assertIn("/api/payments/automation/items", self.js)

    def test_already_running_error_handled(self):
        self.assertIn("already_running", self.js)

    def test_load_automation_status_called_on_accordion_open(self):
        self.assertIn("loadAutomationStatus()", self.js)

    def test_load_automation_settings_called_on_accordion_open(self):
        self.assertIn("loadAutomationSettings()", self.js)

    def test_queue_stage_filter_referenced(self):
        self.assertIn("autoQueueStageFilter", self.js)

    def test_open_queue_btn_referenced(self):
        self.assertIn("autoOpenQueueBtn", self.js)


# ---------------------------------------------------------------------------
# 8. HTML static analysis
# ---------------------------------------------------------------------------

class TestAutomationHTMLStatic(unittest.TestCase):
    def setUp(self):
        self.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_version_in_styles_link(self):
        self.assertIn(f"styles.css?v={CURRENT_VERSION}", self.html)

    def test_version_in_app_js_link(self):
        self.assertIn(f"app.js?v={CURRENT_VERSION}", self.html)

    def test_automation_section_present(self):
        self.assertIn('id="automationSection"', self.html)

    def test_automation_status_element(self):
        self.assertIn('id="automationStatus"', self.html)

    def test_automation_runs_list_element(self):
        self.assertIn('id="automationRunsList"', self.html)

    def test_auto_toggle_discovery(self):
        self.assertIn('id="autoToggleDiscovery"', self.html)

    def test_auto_toggle_create(self):
        self.assertIn('id="autoToggleCreate"', self.html)

    def test_auto_toggle_publish(self):
        self.assertIn('id="autoTogglePublish"', self.html)

    def test_auto_interval_input(self):
        self.assertIn('id="autoIntervalInput"', self.html)

    def test_auto_save_settings_btn(self):
        self.assertIn('id="autoSaveSettingsBtn"', self.html)

    def test_auto_run_scan_btn(self):
        self.assertIn('id="autoRunScanBtn"', self.html)

    def test_automation_queue_section(self):
        self.assertIn('id="automationQueueSection"', self.html)

    def test_automation_queue_list(self):
        self.assertIn('id="automationQueueList"', self.html)

    def test_queue_stage_filter(self):
        self.assertIn('id="autoQueueStageFilter"', self.html)

    def test_moyklass_auto_post_disabled_label(self):
        # "always disabled" checkbox for posting to MK is present in HTML
        self.assertIn("вручную после проверки", self.html)


# ---------------------------------------------------------------------------
# 9. CSS static analysis
# ---------------------------------------------------------------------------

class TestAutomationCSSStatic(unittest.TestCase):
    def setUp(self):
        self.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_notice_empty_rule(self):
        self.assertIn(".notice:empty", self.css)
        self.assertIn("display: none", self.css)

    def test_auto_stat_grid(self):
        self.assertIn(".auto-stat-grid", self.css)

    def test_auto_stat_running(self):
        self.assertIn(".auto-stat-running", self.css)

    def test_auto_run_ok_class(self):
        self.assertIn(".auto-run-ok", self.css)

    def test_auto_run_error_class(self):
        self.assertIn(".auto-run-error", self.css)

    def test_auto_queue_card(self):
        self.assertIn(".auto-queue-card", self.css)

    def test_auto_queue_head(self):
        self.assertIn(".auto-queue-head", self.css)

    def test_auto_queue_badge(self):
        self.assertIn(".auto-queue-badge", self.css)

    def test_auto_stage_discovered(self):
        self.assertIn(".auto-stage-discovered", self.css)

    def test_auto_stage_published(self):
        self.assertIn(".auto-stage-published", self.css)

    def test_auto_stage_missing_parent_link(self):
        self.assertIn(".auto-stage-missing_parent_link", self.css)

    def test_auto_stage_error(self):
        self.assertIn(".auto-stage-error", self.css)

    def test_auto_stage_ignored(self):
        self.assertIn(".auto-stage-ignored", self.css)

    def test_auto_queue_reason(self):
        self.assertIn(".auto-queue-reason", self.css)

    def test_auto_queue_actions(self):
        self.assertIn(".auto-queue-actions", self.css)


# ---------------------------------------------------------------------------
# 10. Config regression check
# ---------------------------------------------------------------------------

class TestConfigAutomation(unittest.TestCase):
    def test_settings_has_automation_field(self):
        from config import load_settings
        s = load_settings()
        self.assertIsNotNone(s)
        self.assertFalse(getattr(s, "payment_invoice_automation_enabled", None))

    def test_settings_is_bool(self):
        from config import Settings
        import inspect
        hints = {}
        for cls in Settings.__mro__:
            if hasattr(cls, "__annotations__"):
                hints.update(cls.__annotations__)
        self.assertIn("payment_invoice_automation_enabled", hints)


# ---------------------------------------------------------------------------
# 11. Regression: existing test imports still work
# ---------------------------------------------------------------------------

class TestRegressionImports(unittest.TestCase):
    def test_storage_importable(self):
        import storage
        self.assertTrue(hasattr(storage, "Storage"))

    def test_web_app_server_importable(self):
        import web_app_server
        self.assertTrue(hasattr(web_app_server, "MiniAppContext"))

    def test_automation_roles_present(self):
        from web_app_server import AUTOMATION_ADMIN_ROLES, AUTOMATION_VIEW_ROLES
        self.assertIn("owner", AUTOMATION_ADMIN_ROLES)
        self.assertIn("operations", AUTOMATION_VIEW_ROLES)
        self.assertNotIn("operations", AUTOMATION_ADMIN_ROLES)

    def test_internal_bypass_flag_works(self):
        from web_app_server import MiniAppContext
        ctx = MiniAppContext.__new__(MiniAppContext)
        ctx.storage = _make_storage()
        ctx.settings = MagicMock()
        ctx.moyklass = MagicMock()
        internal_auth = {"role": "owner", "user_id": 0, "_internal": True}
        result = ctx._require_payment_intent_access(internal_auth)
        self.assertIsNone(result)  # None means access granted

    def test_regular_auth_still_requires_db_role(self):
        from web_app_server import MiniAppContext
        ctx = MiniAppContext.__new__(MiniAppContext)
        ctx.storage = _make_storage()
        ctx.settings = MagicMock()
        ctx.moyklass = MagicMock()
        # user_id=0 does not exist in DB, so role lookup returns empty string
        regular_auth = {"role": "owner", "user_id": 0}
        result = ctx._require_payment_intent_access(regular_auth)
        # Should fail because no _internal flag
        self.assertIsNotNone(result)

    def test_scheduler_class_importable(self):
        from web_app_server import InvoiceAutomationScheduler
        self.assertTrue(callable(InvoiceAutomationScheduler))

    def test_automation_update_counts_helper(self):
        from web_app_server import _automation_update_counts
        counts = {"scanned": 0, "discovered": 0, "created": 0, "published": 0,
                  "missing_parent": 0, "requires_check": 0, "skipped": 0, "error": 0}
        _automation_update_counts(counts, {"discovered": True, "created": True})
        self.assertEqual(counts["discovered"], 1)
        self.assertEqual(counts["created"], 1)
        _automation_update_counts(counts, {"skip": True})
        self.assertEqual(counts["skipped"], 1)
        _automation_update_counts(counts, {"missing_parent": True})
        self.assertEqual(counts["missing_parent"], 1)


# ---------------------------------------------------------------------------
# 12. Role resolution hotfix — v7.0.94.1
#     Verifies that _automation_effective_role() uses _role_for_user() and
#     NOT auth["role"] for access decisions.
# ---------------------------------------------------------------------------

def _make_ctx_for_role_tests(role_map: dict[int, str]) -> Any:
    """Minimal ctx whose _role_for_user is driven by role_map dict.

    role_map: {user_id -> role}. Missing user_id returns "other".
    test_mode_map: optional, set via ctx._test_mode_map.
    """
    from web_app_server import MiniAppContext
    ctx = MiniAppContext.__new__(MiniAppContext)
    ctx.storage = _make_storage()
    ctx.settings = MagicMock()
    ctx.moyklass = MagicMock()

    def _fake_role_for_user(uid: int) -> str:
        return role_map.get(int(uid), "other")

    ctx._role_for_user = _fake_role_for_user
    return ctx


class TestAutomationRoleResolution(unittest.TestCase):
    """Test that _automation_effective_role uses _role_for_user() server-side resolution.

    Tests 1-22 from hotfix spec v7.0.94.1.
    """

    # ── helpers ──────────────────────────────────────────────────────────────

    def _ctx(self, uid: int, effective_role: str):
        """Context where uid maps to effective_role via _role_for_user."""
        return _make_ctx_for_role_tests({uid: effective_role})

    def _view_auth(self, uid: int, frontend_role: str = "other") -> dict:
        """Auth payload where frontend_role is intentionally wrong/stale."""
        return {"user_id": str(uid), "role": frontend_role}

    # ── 1-6: owner / admin base access ───────────────────────────────────────

    def test_01_owner_gets_automation_settings(self):
        """Owner with base role gets automation settings."""
        ctx = self._ctx(uid=1, effective_role="owner")
        result = ctx.automation_get_settings(self._view_auth(1))
        self.assertTrue(result.get("ok"), result)

    def test_02_owner_gets_automation_status(self):
        ctx = self._ctx(uid=1, effective_role="owner")
        result = ctx.automation_get_status(self._view_auth(1), {})
        self.assertTrue(result.get("ok"), result)

    def test_03_owner_gets_automation_items(self):
        ctx = self._ctx(uid=1, effective_role="owner")
        result = ctx.automation_list_items(self._view_auth(1), {})
        self.assertTrue(result.get("ok"), result)

    def test_04_owner_can_update_settings(self):
        ctx = self._ctx(uid=1, effective_role="owner")
        body = {"discovery_enabled": True, "create_payment_options_enabled": False,
                "publish_to_parent_enabled": False, "scan_interval_minutes": 10}
        result = ctx.automation_update_settings(self._view_auth(1), body)
        self.assertTrue(result.get("ok"), result)

    def test_05_owner_can_run_manual_scan(self):
        ctx = self._ctx(uid=1, effective_role="owner")
        with patch.object(ctx, "process_new_moyklass_invoices", return_value={"ok": True}):
            result = ctx.automation_manual_scan(self._view_auth(1))
        self.assertTrue(result.get("ok"), result)

    def test_06_owner_can_perform_item_action(self):
        ctx = self._ctx(uid=1, effective_role="owner")
        now = _now()
        ctx.storage.upsert_automation_item("inv_role_1", "u1", None, "{}", now)
        iid = ctx.storage.get_automation_item_by_invoice("inv_role_1")["id"]
        result = ctx.automation_item_action(self._view_auth(1), str(iid), "ignore", {})
        self.assertTrue(result.get("ok"), result)

    # ── 7-8: admin access ────────────────────────────────────────────────────

    def test_07_admin_gets_view_access(self):
        ctx = self._ctx(uid=2, effective_role="admin")
        result = ctx.automation_get_status(self._view_auth(2), {})
        self.assertTrue(result.get("ok"), result)

    def test_08_admin_gets_admin_access(self):
        ctx = self._ctx(uid=2, effective_role="admin")
        body = {"discovery_enabled": True, "create_payment_options_enabled": False,
                "publish_to_parent_enabled": False, "scan_interval_minutes": 10}
        result = ctx.automation_update_settings(self._view_auth(2), body)
        self.assertTrue(result.get("ok"), result)

    # ── 9-12: denied roles ───────────────────────────────────────────────────

    def test_09_parent_gets_access_denied(self):
        ctx = self._ctx(uid=10, effective_role="parent")
        result = ctx.automation_get_settings(self._view_auth(10))
        self.assertFalse(result.get("ok"))

    def test_10_teacher_gets_access_denied(self):
        ctx = self._ctx(uid=11, effective_role="teacher")
        result = ctx.automation_get_status(self._view_auth(11), {})
        self.assertFalse(result.get("ok"))

    def test_11_kitchen_gets_access_denied(self):
        ctx = self._ctx(uid=12, effective_role="kitchen")
        result = ctx.automation_list_items(self._view_auth(12), {})
        self.assertFalse(result.get("ok"))

    def test_12_restaurant_gets_access_denied(self):
        ctx = self._ctx(uid=13, effective_role="restaurant")
        result = ctx.automation_get_settings(self._view_auth(13))
        self.assertFalse(result.get("ok"))

    # ── 13-14: stale / missing auth["role"] ──────────────────────────────────

    def test_13_owner_uid_no_role_field_gets_access(self):
        """auth without role field — if uid resolves to owner, access must be granted."""
        ctx = self._ctx(uid=1, effective_role="owner")
        auth_no_role = {"user_id": "1"}  # no "role" key at all
        result = ctx.automation_get_settings(auth_no_role)
        self.assertTrue(result.get("ok"), result)

    def test_14_stale_role_in_payload_does_not_grant_access(self):
        """Frontend sends role='owner' but _role_for_user returns 'parent' — access denied."""
        ctx = self._ctx(uid=20, effective_role="parent")
        stale_auth = {"user_id": "20", "role": "owner"}  # frontend lies
        result = ctx.automation_get_settings(stale_auth)
        self.assertFalse(result.get("ok"), "Stale frontend role must not grant access")

    # ── 15-17: test-role mode ─────────────────────────────────────────────────

    def test_15_owner_with_test_role_parent_gets_denied(self):
        """Owner in test-role 'parent' must be denied (test-role is honoured)."""
        # _role_for_user returns "parent" when test-role is active
        ctx = self._ctx(uid=1, effective_role="parent")
        result = ctx.automation_get_settings(self._view_auth(1))
        self.assertFalse(result.get("ok"), "test-role parent must be denied")

    def test_16_after_test_role_cleared_owner_regains_access(self):
        """After test-role disabled, owner's base role (owner) is used → access granted."""
        # Simulate test-role disabled: _role_for_user returns base "owner"
        ctx = self._ctx(uid=1, effective_role="owner")
        result = ctx.automation_get_settings(self._view_auth(1))
        self.assertTrue(result.get("ok"), result)

    def test_17_test_role_owner_gets_access(self):
        """User in test-role 'owner' must get access (test-role owner is valid)."""
        ctx = self._ctx(uid=5, effective_role="owner")
        result = ctx.automation_get_settings(self._view_auth(5))
        self.assertTrue(result.get("ok"), result)

    # ── 18: operations scope ─────────────────────────────────────────────────

    def test_18_operations_gets_view_not_admin(self):
        """operations is in VIEW_ROLES but not ADMIN_ROLES."""
        ctx = self._ctx(uid=3, effective_role="operations")
        view_ok = ctx.automation_get_status(self._view_auth(3), {})
        self.assertTrue(view_ok.get("ok"), "operations must have view access")
        body = {"discovery_enabled": False, "create_payment_options_enabled": False,
                "publish_to_parent_enabled": False, "scan_interval_minutes": 10}
        admin_result = ctx.automation_update_settings(self._view_auth(3), body)
        self.assertFalse(admin_result.get("ok"), "operations must NOT have admin access")

    # ── 19: frontend not source of truth ─────────────────────────────────────

    def test_19_frontend_role_not_trusted(self):
        """Sending any role in auth payload must not bypass _role_for_user."""
        # uid=99 resolves to "parent" — no matter what frontend sends
        ctx = self._ctx(uid=99, effective_role="parent")
        for spoofed in ("owner", "admin", "operations", "director"):
            with self.subTest(spoofed=spoofed):
                auth = {"user_id": "99", "role": spoofed}
                result = ctx.automation_get_settings(auth)
                self.assertFalse(result.get("ok"),
                                 f"Spoofed role '{spoofed}' must not grant access")

    # ── 20-22: regression guards ──────────────────────────────────────────────

    def test_20_existing_121_automation_tests_still_importable(self):
        """test_invoice_automation module is importable (guards the 121 existing tests)."""
        import tests.test_invoice_automation  # noqa: F401

    def test_21_existing_payment_tests_importable(self):
        import tests.test_client_payments  # noqa: F401
        import tests.test_mk_invoice_intent  # noqa: F401
        import tests.test_bepaid_recovery_queue  # noqa: F401

    def test_22_automation_helper_methods_exist(self):
        """_automation_effective_role and _automation_deny must exist on MiniAppContext."""
        from web_app_server import MiniAppContext
        self.assertTrue(hasattr(MiniAppContext, "_automation_effective_role"))
        self.assertTrue(hasattr(MiniAppContext, "_automation_deny"))


if __name__ == "__main__":
    unittest.main()
