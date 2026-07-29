"""Tests for v7.1.9 — Payment Automation Guardian diagnostics API:
GET /api/payments/diagnostics, POST /api/payments/diagnostics/run,
POST /api/payments/diagnostics/incidents/{id}/recheck.

Run offline (mocked MoyKlass, temp SQLite file):
    python -m unittest tests.test_payment_diagnostics_api -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from web_app_server import MiniAppContext, PaymentAutomationGuardian
from utils import now_iso as _now


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


class _FakeResult:
    def __init__(self, data, ok=True):
        self.data = data
        self.ok = ok


def _make_ctx(storage: Storage, role: str = "owner") -> MiniAppContext:
    ctx = MiniAppContext.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = MagicMock()
    ctx._role_for_user = MagicMock(return_value=role)
    mk = MagicMock()
    mk.get_user_subscriptions = MagicMock(return_value=_FakeResult(
        {"items": [{"id": "SUB1", "statusId": "2", "mainClassId": "900", "classIds": ["900"]}]}
    ))
    mk.get_user_joins = MagicMock(return_value=_FakeResult(
        {"items": [{"id": "J1", "classId": "900", "statusId": "99046"}]}
    ))
    ctx.moyklass = mk
    return ctx


def _auth(uid=1):
    return {"user_id": uid, "_internal": False}


def _seed_paused_incident(storage: Storage) -> dict:
    import json
    item = storage.upsert_automation_item("INV-API1", "8801", "Test", json.dumps({"userSubscriptionId": "SUB1"}), _now())
    storage.update_automation_item_stage(item["id"], "discovered", now=_now())
    ctx = _make_ctx(storage)
    guardian = PaymentAutomationGuardian(ctx)
    guardian._run_quick_cycle()
    return item


class TestPermissions(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()

    def test_01_owner_can_view(self):
        ctx = _make_ctx(self.storage, "owner")
        r = ctx.payments_diagnostics(_auth())
        self.assertTrue(r["ok"])

    def test_02_admin_can_view(self):
        ctx = _make_ctx(self.storage, "admin")
        r = ctx.payments_diagnostics(_auth())
        self.assertTrue(r["ok"])

    def test_03_operations_can_view(self):
        ctx = _make_ctx(self.storage, "operations")
        r = ctx.payments_diagnostics(_auth())
        self.assertTrue(r["ok"])

    def test_04_client_manager_can_view(self):
        ctx = _make_ctx(self.storage, "client_manager")
        r = ctx.payments_diagnostics(_auth())
        self.assertTrue(r["ok"])

    def test_05_teacher_denied_view(self):
        ctx = _make_ctx(self.storage, "teacher")
        r = ctx.payments_diagnostics(_auth())
        self.assertFalse(r["ok"])

    def test_06_parent_denied_view(self):
        ctx = _make_ctx(self.storage, "parent")
        r = ctx.payments_diagnostics(_auth())
        self.assertFalse(r["ok"])

    def test_07_owner_can_manage(self):
        ctx = _make_ctx(self.storage, "owner")
        r = ctx.payments_diagnostics(_auth())
        self.assertTrue(r["can_manage"])

    def test_08_operations_can_manage(self):
        ctx = _make_ctx(self.storage, "operations")
        r = ctx.payments_diagnostics(_auth())
        self.assertTrue(r["can_manage"])

    def test_09_client_manager_cannot_manage(self):
        ctx = _make_ctx(self.storage, "client_manager")
        r = ctx.payments_diagnostics(_auth())
        self.assertFalse(r["can_manage"])

    def test_10_client_manager_denied_run(self):
        ctx = _make_ctx(self.storage, "client_manager")
        r = ctx.payments_diagnostics_run(_auth())
        self.assertFalse(r["ok"])

    def test_11_client_manager_denied_recheck(self):
        item = _seed_paused_incident(self.storage)
        ctx = _make_ctx(self.storage, "client_manager")
        inc_id = self.storage.list_open_incidents()[0]["id"]
        r = ctx.payments_diagnostics_incident_recheck(_auth(), str(inc_id))
        self.assertFalse(r["ok"])

    def test_12_operations_allowed_run(self):
        ctx = _make_ctx(self.storage, "operations")
        r = ctx.payments_diagnostics_run(_auth())
        self.assertTrue(r["ok"])


class TestNoDataAndPayloadSafety(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_ctx(self.storage, "owner")

    def test_13_no_data_before_first_run(self):
        r = self.ctx.payments_diagnostics(_auth())
        self.assertEqual(r["guardian"]["status"], "no_data")

    def test_14_incidents_never_include_payload_json(self):
        _seed_paused_incident(self.storage)
        r = self.ctx.payments_diagnostics(_auth())
        for inc in r["incidents"]:
            self.assertNotIn("payload_json", inc)

    def test_15_incidents_never_include_raw_exception(self):
        _seed_paused_incident(self.storage)
        r = self.ctx.payments_diagnostics(_auth())
        for inc in r["incidents"]:
            self.assertNotIn("Traceback", str(inc))
            self.assertNotIn("Exception", str(inc.get("message", "")))

    def test_16_incident_has_curated_title_and_message(self):
        _seed_paused_incident(self.storage)
        r = self.ctx.payments_diagnostics(_auth())
        self.assertEqual(len(r["incidents"]), 1)
        inc = r["incidents"][0]
        self.assertTrue(inc["title"])
        self.assertTrue(inc["message"])
        self.assertNotIn(inc["reason_code"], inc["message"], "raw code must not leak into message")


class TestManualRun(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_ctx(self.storage, "owner")

    def test_17_manual_run_creates_health_run(self):
        r = self.ctx.payments_diagnostics_run(_auth())
        self.assertTrue(r["ok"])
        self.assertIsNotNone(r["run"])

    def test_18_manual_run_audited(self):
        self.ctx.payments_diagnostics_run(_auth())
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM automation_audit_log WHERE event_type='diagnostics_manual_run_requested'"
            ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_19_manual_run_conflict_when_lease_held(self):
        self.storage.start_health_run("quick_cycle", "corr-ext", "tok-ext", _now())
        r = self.ctx.payments_diagnostics_run(_auth())
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("conflict"))

    def test_20_manual_run_never_creates_payment_intent(self):
        before = self.storage.payment_intents_stats()
        self.ctx.payments_diagnostics_run(_auth())
        after = self.storage.payment_intents_stats()
        self.assertEqual(before, after)


class TestRecheck(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_ctx(self.storage, "owner")

    def test_21_recheck_unknown_incident_returns_error(self):
        r = self.ctx.payments_diagnostics_incident_recheck(_auth(), "999999")
        self.assertFalse(r["ok"])

    def test_22_recheck_invalid_id_returns_error(self):
        r = self.ctx.payments_diagnostics_incident_recheck(_auth(), "not-a-number")
        self.assertFalse(r["ok"])

    def test_23_recheck_training_incident_uses_shared_helper(self):
        _seed_paused_incident(self.storage)
        inc_id = self.storage.list_open_incidents()[0]["id"]
        r = self.ctx.payments_diagnostics_incident_recheck(_auth(), str(inc_id))
        self.assertTrue(r["ok"])
        self.assertIn("reason_code", r)

    def test_24_recheck_never_accepts_client_supplied_reason_code(self):
        import inspect
        sig = inspect.signature(MiniAppContext.payments_diagnostics_incident_recheck)
        self.assertEqual(list(sig.parameters.keys()), ["self", "auth", "incident_id"])

    def test_25_recheck_creates_repair_attempt(self):
        _seed_paused_incident(self.storage)
        inc_id = self.storage.list_open_incidents()[0]["id"]
        self.ctx.payments_diagnostics_incident_recheck(_auth(), str(inc_id))
        attempts = self.storage.list_repair_attempts(inc_id)
        self.assertGreaterEqual(len(attempts), 1)

    def test_26_recheck_audited(self):
        _seed_paused_incident(self.storage)
        inc_id = self.storage.list_open_incidents()[0]["id"]
        self.ctx.payments_diagnostics_incident_recheck(_auth(), str(inc_id))
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM automation_audit_log WHERE event_type='incident_manual_recheck'"
            ).fetchall()
        self.assertEqual(len(rows), 1)


class TestSummaryCounts(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_ctx(self.storage, "owner")

    def test_27_counts_reflect_open_incidents(self):
        _seed_paused_incident(self.storage)
        r = self.ctx.payments_diagnostics(_auth())
        self.assertEqual(r["counts"]["open_warning"] + r["counts"]["open_critical"], 1)

    def test_28_status_healthy_with_no_incidents(self):
        r = self.ctx.payments_diagnostics(_auth())
        # no run yet -> no_data, not healthy; run once with nothing to find
        self.ctx.payments_diagnostics_run(_auth())
        r2 = self.ctx.payments_diagnostics(_auth())
        self.assertEqual(r2["guardian"]["status"], "healthy")

    def test_29_status_warning_with_open_warning(self):
        _seed_paused_incident(self.storage)
        r = self.ctx.payments_diagnostics(_auth())
        self.assertIn(r["guardian"]["status"], ("warning", "critical"))

    def test_30_next_expected_run_computed_from_interval(self):
        self.ctx.payments_diagnostics_run(_auth())
        r = self.ctx.payments_diagnostics(_auth())
        self.assertIsNotNone(r["guardian"]["next_expected_run_at"])


if __name__ == "__main__":
    unittest.main()
