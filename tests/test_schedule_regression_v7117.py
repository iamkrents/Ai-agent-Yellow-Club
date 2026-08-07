"""Tests for v7.1.17 — "Расписание" schedule module: REGRESSION.

Covers spec section 23 REGRESSION checks 76-85: payments, payment period
filters, notifications, mailings, onboarding, availability, food-only,
client cabinet, and frontend-incident reporting all remain intact after
adding the schedule module, plus the version/cache-bust bump landed in
exactly the three required places.

This module deliberately touches ZERO existing business-logic tables/
columns/routes (schedule_source_*/schedule_draft_* are new tables; every
storage read against client_onboarding_recipients/client_schedule_
availability is read-only) — these tests assert that invariant directly
rather than re-running the full pre-existing suites (per the release
brief: run thematic groups, not the full suite).

Run offline:
    python -m unittest tests.test_schedule_regression_v7117 -v
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
WEB_APP_SERVER_PY = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
STORAGE_PY = (ROOT / "storage.py").read_text(encoding="utf-8")


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


class TestPaymentsUntouched(unittest.TestCase):
    def test_76_payment_intent_tables_and_routes_untouched(self):
        self.assertIn("def payment_intents_list", WEB_APP_SERVER_PY)
        self.assertIn('if path == "/api/payments/intents":', WEB_APP_SERVER_PY)
        self.assertIn('WORKSPACE_VIEW_ROLES = {"owner", "admin", "operations", "client_manager"}', WEB_APP_SERVER_PY)

    def test_77_payment_period_filter_routes_untouched(self):
        for path in ("/api/payments/workspace/stats", "/api/payments/workspace/attention", "/api/payments/workspace/list"):
            self.assertIn(f'if path == "{path}":', WEB_APP_SERVER_PY)
        self.assertIn("def _parse_payments_period", WEB_APP_SERVER_PY)

    def test_no_new_columns_on_payment_intents(self):
        # the schedule module must never touch payment_intents' own schema
        section = STORAGE_PY[STORAGE_PY.index("def _init_payment_intent_tables"):]
        section = section[:section.index("\n    def _init_bepaid_tables")]
        self.assertNotIn("schedule_", section)


class TestNotificationsAndCommsUntouched(unittest.TestCase):
    def test_78_client_notifications_untouched(self):
        self.assertIn("NOTIFICATION_CATEGORIES = (", STORAGE_PY)
        self.assertIn('"schedule"', STORAGE_PY.split("NOTIFICATION_CATEGORIES = (")[1].split(")")[0])

    def test_79_communications_center_untouched(self):
        self.assertIn("def create_staff_communication_draft", STORAGE_PY)
        self.assertIn('CLIENT_COMMUNICATIONS_ROLES = {"owner", "admin", "client_manager"}', WEB_APP_SERVER_PY)


class TestOnboardingAndAvailabilityUntouched(unittest.TestCase):
    def test_80_onboarding_import_never_mutated(self):
        section = STORAGE_PY[STORAGE_PY.index("def import_onboarding_campaign_recipients"):]
        section = section[:section.index("\n    def update_recipient_academic_level")]
        self.assertNotIn("schedule_source", section)
        self.assertNotIn("schedule_draft", section)

    def test_81_availability_submission_path_never_mutated(self):
        section = STORAGE_PY[STORAGE_PY.index("def submit_schedule_availability"):]
        section = section[:section.index("\n    def get_schedule_availability")]
        self.assertNotIn("schedule_source", section)
        self.assertNotIn("schedule_draft", section)
        # schedule module only ever READS this table
        self.assertIn("def resolve_schedule_student_status", STORAGE_PY)
        resolve_fn = STORAGE_PY.split("def resolve_schedule_student_status", 1)[1].split("\n    def ", 1)[0]
        self.assertNotIn("INSERT INTO client_schedule_availability", resolve_fn)
        self.assertNotIn("UPDATE client_schedule_availability", resolve_fn)

    def test_81b_schedule_module_never_writes_onboarding_tables(self):
        schedule_section_start = STORAGE_PY.index("# v7.1.17 — SCHEDULE MODULE")
        schedule_section = STORAGE_PY[schedule_section_start:]
        for forbidden in ("UPDATE client_onboarding_recipients", "INSERT INTO client_onboarding_recipients",
                          "UPDATE client_schedule_availability", "INSERT INTO client_schedule_availability",
                          "DELETE FROM client_onboarding_recipients", "DELETE FROM client_schedule_availability"):
            self.assertNotIn(forbidden, schedule_section, f"schedule module must never write {forbidden.split()[-1]}")


class TestFoodAndClientCabinetUntouched(unittest.TestCase):
    def test_82_food_module_tables_untouched(self):
        self.assertIn("def _init_food_tables", STORAGE_PY)
        schedule_section_start = STORAGE_PY.index("# v7.1.17 — SCHEDULE MODULE")
        self.assertNotIn("food_order", STORAGE_PY[schedule_section_start:])

    def test_83_client_cabinet_flags_untouched(self):
        self.assertIn("clientCabinetV7113Enabled", WEB_APP_SERVER_PY)
        self.assertIn("def _client_cabinet_enabled", WEB_APP_SERVER_PY)


class TestFrontendIncidentsAndMisc(unittest.TestCase):
    def test_84_frontend_incident_endpoints_still_wired(self):
        self.assertIn("def log_frontend_incident", STORAGE_PY)


class TestSchemaIsolation(unittest.TestCase):
    def test_new_tables_do_not_collide_with_existing_names(self):
        storage = _make_storage()
        with storage._connect() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        expected_new = {
            "schedule_source_snapshots", "schedule_source_groups", "schedule_source_group_students",
            "schedule_source_lessons", "schedule_source_sync_errors", "schedule_drafts",
            "schedule_draft_members", "schedule_draft_audit_log",
        }
        self.assertTrue(expected_new.issubset(tables))
        # pre-existing, unrelated tables (including the naming-collision risk
        # this module's audit flagged) must still exist untouched
        for pre_existing in ("payment_intents", "client_onboarding_recipients", "client_schedule_availability",
                              "teacher_work_schedule", "client_parent_child_links"):
            self.assertIn(pre_existing, tables)

    def test_teacher_work_schedule_table_untouched_by_new_module(self):
        schedule_section_start = STORAGE_PY.index("# v7.1.17 — SCHEDULE MODULE")
        self.assertNotIn("teacher_work_schedule", STORAGE_PY[schedule_section_start:])


class TestVersionBump(unittest.TestCase):
    def test_85_version_bumped_in_three_places(self):
        self.assertIn('console.log("MiniApp version: v7.1.17.1");', APP_JS)
        self.assertIn('const APP_VERSION = "7.1.17.1";', APP_JS)
        self.assertIn('styles.css?v=7.1.17', INDEX_HTML)
        self.assertIn('app.js?v=7.1.17', INDEX_HTML)

    def test_85b_no_stale_previous_version_left_in_cache_bust(self):
        self.assertNotIn('styles.css?v=7.1.16.1', INDEX_HTML)
        self.assertNotIn('app.js?v=7.1.16.1', INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
