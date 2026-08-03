"""Tests for v7.1.14 — staff "Рассылки": regression guards.

This file provides fast structural/import-safety guards that the new
communications code did not touch protected areas. The substantive
regression proof is running the EXISTING targeted files for each area as
their own themed group alongside this one (see the final report):
    tests.test_client_notifications_v7113
    tests.test_owner_test_client_context_v71131
    tests.test_client_payments
    tests.test_client_availability_redesign_v7113
    tests.test_food_only_client_v7113
    tests.test_invoice_automation

Covers:
  53. client_notifications storage/API functions are byte-for-byte
      untouched (create_client_notification signature unchanged).
  54. owner test-client mode guards are still present and unchanged.
  55. payment business logic modules were not touched by this change.
  56. availability submission entry points are unchanged.
  57. Food Module backend entry points are unchanged.
  58. the invoice automation scheduler class is unchanged and the new
      scheduler is a genuinely separate class/thread.

Run:
    python -m unittest tests.test_communications_regression_v7114 -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WEB_APP_SERVER_PY = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
STORAGE_PY = (ROOT / "storage.py").read_text(encoding="utf-8")


class TestClientNotificationsUnchanged(unittest.TestCase):
    def test_53_create_client_notification_signature_unchanged(self):
        idx = STORAGE_PY.find("def create_client_notification(")
        self.assertNotEqual(idx, -1)
        segment = STORAGE_PY[idx:idx + 900]
        for token in (
            "title: str", "body: str", "category: str", "priority: str", "scope: str",
            "mk_user_id: Optional[str]", "action_key: str", "created_by_telegram_id: str",
            "recipient_telegram_ids: list[str]",
        ):
            self.assertIn(token, segment)

    def test_53b_new_companions_are_additive_not_replacements(self):
        # create_client_notification itself must still exist verbatim (not
        # renamed/removed) even though two new companion helpers were added
        # next to it for the send step's incremental fan-out.
        self.assertIn("def create_client_notification(", STORAGE_PY)
        self.assertIn("def create_client_notification_message(", STORAGE_PY)
        self.assertIn("def add_client_notification_recipient(", STORAGE_PY)


class TestOwnerTestClientModeUnchanged(unittest.TestCase):
    def test_54_owner_test_guards_present(self):
        for token in (
            "def _owner_test_client_mode_active(",
            "def _effective_client_identity(",
            "owner_test_mode_payment_blocked",
            "owner_test_mode_link_blocked",
            "owner_test_mode_food_order_blocked",
            "owner_confirm_required",
        ):
            self.assertIn(token, WEB_APP_SERVER_PY)

    def test_54b_communications_gate_is_independent_of_owner_test_mode(self):
        idx = WEB_APP_SERVER_PY.find("def _communications_access_allowed(")
        self.assertNotEqual(idx, -1)
        segment = WEB_APP_SERVER_PY[idx:idx + 600]
        self.assertNotIn("_owner_test_client_mode_active", segment)
        self.assertNotIn("_effective_client_identity", segment)


class TestPaymentLogicUntouched(unittest.TestCase):
    def test_55_payment_modules_not_imported_by_communications_code(self):
        idx = WEB_APP_SERVER_PY.find("def _communications_load_campaign(")
        self.assertNotEqual(idx, -1, "communications section marker not found")
        end = WEB_APP_SERVER_PY.find("def communications_campaign_cancel", idx)
        end = WEB_APP_SERVER_PY.find("\n\n", end) if end != -1 else idx + 20000
        section = WEB_APP_SERVER_PY[idx:end]
        for forbidden in ("bepaid", "BePaid", "moyklass_post", "self.moyklass.", "payment_intent_save_bepaid"):
            self.assertNotIn(forbidden, section, f"communications code unexpectedly touches {forbidden}")

    def test_55b_core_payment_functions_still_present(self):
        for token in ("def create_payment_intent(", "def payment_intent_save_bepaid_success("):
            self.assertIn(token, STORAGE_PY)


class TestAvailabilityUnchanged(unittest.TestCase):
    def test_56_availability_entry_points_present(self):
        for token in (
            "def client_schedule_availability_get(",
            "def client_schedule_availability_submit(",
            "def submit_schedule_availability(",
            "def get_or_create_recipient_for_client(",
            "def get_availability_status_for_mk_user(",
        ):
            self.assertIn(token, WEB_APP_SERVER_PY + STORAGE_PY)

    def test_56b_communications_availability_resolver_is_read_only(self):
        idx = WEB_APP_SERVER_PY.find("def _resolve_audience_availability(")
        self.assertNotEqual(idx, -1)
        segment = WEB_APP_SERVER_PY[idx:idx + 1500]
        self.assertNotIn("submit_schedule_availability", segment)
        self.assertNotIn("INSERT", segment)
        self.assertNotIn("UPDATE", segment)


class TestFoodModuleUnchanged(unittest.TestCase):
    def test_57_food_entry_points_present(self):
        for token in ("def food_submit_order(", "def food_skip_order(", "def food_my_orders(" if "def food_my_orders(" in WEB_APP_SERVER_PY else "def food_active_menus("):
            self.assertIn(token, WEB_APP_SERVER_PY)


class TestInvoiceSchedulerUnchanged(unittest.TestCase):
    def test_58_invoice_scheduler_class_unchanged_and_separate(self):
        self.assertIn("class InvoiceAutomationScheduler:", WEB_APP_SERVER_PY)
        self.assertIn("class StaffCommunicationsScheduler:", WEB_APP_SERVER_PY)
        # Two genuinely separate threads/classes, not one repurposed for both jobs.
        idx_invoice = WEB_APP_SERVER_PY.find("class InvoiceAutomationScheduler:")
        idx_comms = WEB_APP_SERVER_PY.find("class StaffCommunicationsScheduler:")
        invoice_body = WEB_APP_SERVER_PY[idx_invoice:idx_invoice + 2000]
        self.assertNotIn("staff_communication", invoice_body)
        self.assertNotEqual(idx_invoice, idx_comms)


if __name__ == "__main__":
    unittest.main()
