"""Tests for v7.1.9 — payment_automation_reasons pure reason-code registry.

Covers: registry completeness, severity/retryable/safe_auto_repair flags,
recovery metadata, no duplicate codes, and that training-state reason codes
are reused verbatim from training_state_domain (never redefined/re-authored).

Run offline (no I/O, no DB, no MoyKlass):
    python -m unittest tests.test_payment_automation_reasons -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import payment_automation_reasons as par
import training_state_domain as training


class TestPurity(unittest.TestCase):
    def test_01_no_forbidden_imports(self):
        src = (ROOT / "payment_automation_reasons.py").read_text(encoding="utf-8")
        for forbidden in ("import storage", "import web_app_server", "import sqlite3",
                          "moyklass_client", "bepaid_client", "telegram"):
            self.assertNotIn(forbidden, src, f"forbidden import/reference: {forbidden}")

    def test_02_imports_training_state_domain_only(self):
        self.assertTrue(hasattr(par, "_training"))


class TestRegistryCompleteness(unittest.TestCase):
    REQUIRED_CODES = [
        "scheduler_cycle_failed", "scheduler_cycle_delayed", "scheduler_cycle_overlapping",
        "scheduler_heartbeat_missing", "moyklass_unavailable", "moyklass_timeout",
        "database_unavailable", "database_write_failed", "parent_link_missing",
        "subscription_not_found", "multiple_subscriptions_found", "invoice_not_found",
        "duplicate_invoice_detected", "duplicate_intent_detected",
        "client_training_paused", "client_training_finished",
        "training_join_status_unknown", "training_join_status_ambiguous",
        "training_subscription_frozen", "client_resume_confirmation_required",
        "paid_not_posted_to_mk", "stale_automation_item", "inconsistent_local_state",
        "manual_review_required",
    ]

    def test_03_all_required_codes_present(self):
        for code in self.REQUIRED_CODES:
            self.assertIn(code, par.REASON_REGISTRY, f"missing reason code: {code}")

    def test_04_no_duplicate_codes(self):
        codes = list(par.REASON_REGISTRY.keys())
        self.assertEqual(len(codes), len(set(codes)))

    def test_05_code_field_matches_key(self):
        for key, info in par.REASON_REGISTRY.items():
            self.assertEqual(info.code, key, f"registry key/code mismatch for {key}")

    def test_06_severity_is_valid(self):
        valid = {par.SEVERITY_INFO, par.SEVERITY_WARNING, par.SEVERITY_CRITICAL}
        for code, info in par.REASON_REGISTRY.items():
            self.assertIn(info.severity, valid, f"invalid severity for {code}: {info.severity}")

    def test_07_dedup_scope_type_is_valid(self):
        valid = {par.SCOPE_SYSTEM, par.SCOPE_CLIENT, par.SCOPE_AUTOMATION_ITEM,
                 par.SCOPE_PAYMENT_INTENT, par.SCOPE_MK_INVOICE}
        for code, info in par.REASON_REGISTRY.items():
            self.assertIn(info.dedup_scope_type, valid, f"invalid scope for {code}")

    def test_08_user_message_never_empty(self):
        for code, info in par.REASON_REGISTRY.items():
            self.assertTrue(info.user_message_ru.strip(), f"empty user_message_ru for {code}")

    def test_09_user_title_never_empty(self):
        for code, info in par.REASON_REGISTRY.items():
            self.assertTrue(info.user_title_ru.strip(), f"empty user_title_ru for {code}")

    def test_10_recovery_condition_never_empty(self):
        for code, info in par.REASON_REGISTRY.items():
            self.assertTrue(info.recovery_condition.strip(), f"empty recovery_condition for {code}")

    def test_11_component_never_empty(self):
        for code, info in par.REASON_REGISTRY.items():
            self.assertTrue(info.component.strip(), f"empty component for {code}")


class TestFlags(unittest.TestCase):
    def test_12_critical_codes_require_human_action(self):
        for code, info in par.REASON_REGISTRY.items():
            if info.severity == par.SEVERITY_CRITICAL:
                self.assertTrue(
                    info.requires_human_action or info.safe_auto_repair,
                    f"critical code {code} must require human action or be safe-auto-repairable",
                )

    def test_13_safe_auto_repair_codes_are_retryable_or_self_resolving(self):
        # Safe-auto-repair codes are either explicitly retryable (transient
        # provider/db blips) or self-resolving without a retry counter
        # (e.g. scheduler_cycle_overlapping just means "try again next cycle").
        _self_resolving = {"scheduler_cycle_overlapping"}
        for code, info in par.REASON_REGISTRY.items():
            if info.safe_auto_repair and code not in _self_resolving:
                self.assertTrue(info.retryable, f"safe_auto_repair code {code} should be retryable")

    def test_14_duplicate_detection_codes_are_critical_and_not_auto_repaired(self):
        for code in ("duplicate_invoice_detected", "duplicate_intent_detected"):
            info = par.REASON_REGISTRY[code]
            self.assertEqual(info.severity, par.SEVERITY_CRITICAL)
            self.assertFalse(info.safe_auto_repair)
            self.assertTrue(info.requires_human_action)

    def test_15_paid_not_posted_is_not_auto_repaired(self):
        info = par.REASON_REGISTRY["paid_not_posted_to_mk"]
        self.assertFalse(info.safe_auto_repair)
        self.assertTrue(info.requires_human_action)


class TestTrainingCodesReusedNotRedefined(unittest.TestCase):
    def test_16_client_training_paused_matches_domain_constant(self):
        info = par.REASON_REGISTRY["client_training_paused"]
        self.assertEqual(info.code, training.REASON_CLIENT_TRAINING_PAUSED)

    def test_17_training_subscription_frozen_matches_domain_constant(self):
        info = par.REASON_REGISTRY["training_subscription_frozen"]
        self.assertEqual(info.code, training.REASON_TRAINING_SUBSCRIPTION_FROZEN)

    def test_18_client_resume_confirmation_required_matches_domain_constant(self):
        info = par.REASON_REGISTRY["client_resume_confirmation_required"]
        self.assertEqual(info.code, training.REASON_CLIENT_RESUME_CONFIRMATION_REQUIRED)

    def test_19_message_text_identical_to_training_domain(self):
        # The registry must never re-author training message text — it must
        # be pulled verbatim from training_state_domain.TRAINING_REASON_MESSAGES.
        for code in (
            training.REASON_CLIENT_TRAINING_PAUSED,
            training.REASON_TRAINING_SUBSCRIPTION_FROZEN,
            training.REASON_CLIENT_TRAINING_FINISHED,
            training.REASON_TRAINING_JOIN_STATUS_UNKNOWN,
            training.REASON_TRAINING_JOIN_STATUS_AMBIGUOUS,
            training.REASON_CLIENT_RESUME_CONFIRMATION_REQUIRED,
        ):
            self.assertEqual(
                par.REASON_REGISTRY[code].user_message_ru,
                training.TRAINING_REASON_MESSAGES[code],
                f"registry message for {code} diverged from training_state_domain",
            )

    def test_20_all_training_blocked_reason_codes_present_in_registry(self):
        for code in training.TRAINING_BLOCKED_REASON_CODES:
            self.assertIn(code, par.REASON_REGISTRY, f"training-blocked code {code} missing from registry")


class TestLookupHelpers(unittest.TestCase):
    def test_21_get_reason_info_known_code(self):
        info = par.get_reason_info("client_training_paused")
        self.assertIsNotNone(info)
        self.assertEqual(info.code, "client_training_paused")

    def test_22_get_reason_info_unknown_code_returns_none(self):
        self.assertIsNone(par.get_reason_info("totally_made_up_code"))

    def test_23_get_reason_info_none_returns_none(self):
        self.assertIsNone(par.get_reason_info(None))

    def test_24_reason_user_message_unknown_code_is_safe_fallback(self):
        msg = par.reason_user_message("totally_made_up_code")
        self.assertTrue(msg)
        self.assertNotIn("Traceback", msg)
        self.assertNotIn("Exception", msg)

    def test_25_reason_user_title_unknown_code_is_safe_fallback(self):
        title = par.reason_user_title("totally_made_up_code")
        self.assertTrue(title)

    def test_26_no_raw_code_leaks_into_message(self):
        # Guard against accidentally interpolating the raw code string into
        # a user-facing message (would look like a technical/internal leak).
        for code, info in par.REASON_REGISTRY.items():
            self.assertNotIn(code, info.user_message_ru, f"raw code leaked into message for {code}")

    def test_27_overlapping_cycle_title_is_previous_check_still_running(self):
        # v7.1.9 visual review: "Пропущен пересекающийся цикл" read as an
        # internal/technical phrase; replaced with plain-language copy.
        info = par.get_reason_info("scheduler_cycle_overlapping")
        self.assertEqual(info.user_title_ru, "Предыдущая проверка ещё выполнялась")
        self.assertNotIn("пересекающ", info.user_title_ru)


if __name__ == "__main__":
    unittest.main()
