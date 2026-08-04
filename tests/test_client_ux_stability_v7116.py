"""Tests for v7.1.16 — Client & Client Manager UX Stabilization: CLIENT
cabinet checks.

Covers (CLIENT 1-16 from the launch-readiness spec):
   1. Regular cabinet tab set unchanged.
   2. Combined cabinet shares the regular tab set (no separate branch).
   3. Food-only stays on its own old-flow tab set.
   4. Child switcher hidden for exactly one child.
   5. Child switcher shown for 2+ children.
   6. Switching child chips updates the active child id.
   7. Selected child / booted flags reset on role switch (v7.1.16 fix).
   8. Availability uses the selected child.
   9. Payments can be filtered by selected child (v7.1.16, additive).
  10. Notifications are fetched from exactly one endpoint (no duplication).
  11. Loading states present (uiLoadingRows).
  12. Empty states present (uiEmptyState).
  13. Error states present with a retry callback (uiErrorState).
  14. Retry button actually wired inside the shared error state.
  15. Double-submit guards present on client loaders.
  16. Unsaved-changes warning on the Availability screen.

Static source-regex checks are used for pure-frontend (JS-only) behavior,
matching the precedent set by tests/test_client_launch_regression_v7115.py
— this project has no JS test runner, and the same technique was already
accepted for the v7.1.14.3/v7.1.15 regressions.

Run:
    python -m unittest tests.test_client_ux_stability_v7116 -v
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest  # noqa: E402

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")


class TestTabSets(unittest.TestCase):
    def test_1_regular_and_combined_share_tab_set(self):
        # v7.1.13 branch: cabinetEnabled && clientKind !== "food_only" ->
        # one shared array of NAV TABS for both regular and combined (no
        # separate combined tab-set branch). Combined only differs in the
        # Home header's subtitle copy (test_2), never in navigation.
        self.assertIn('["home", "client-payments", "notifications", "more"]', APP_JS)

    def test_2_combined_only_differs_in_header_copy_not_navigation(self):
        m = re.search(r'if \(state\.me\?\.clientKind === "combined"\) \{(.*?)\n  \}', APP_JS, re.S)
        self.assertIsNotNone(m, "combined header-copy branch not found")
        self.assertIn("Курсы и городская программа", m.group(1))

    def test_3_food_only_keeps_its_own_tab_set(self):
        self.assertIn('["food", "notifications", "help", "profile"]', APP_JS)
        # old food-only entry point (link-by-code form) still present
        self.assertIn("function renderParentFoodMenu", APP_JS)


class TestChildSwitcher(unittest.TestCase):
    def test_4_hidden_for_one_child(self):
        self.assertIn('if (!children || children.length <= 1) return "";', APP_JS)

    def test_5_shown_for_multiple(self):
        m = re.search(r"function _cabChildSwitcherHtml\(children, activeId\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn("cab-switch-chip", m.group(1))
        self.assertIn(".map(", m.group(1))

    def test_6_chip_click_updates_active_child(self):
        self.assertIn("state.clientHomeActiveChildId = chip.dataset.mk;", APP_JS)


class TestRoleSwitchResets(unittest.TestCase):
    def test_7_role_switch_resets_client_home_and_filter_state(self):
        m = re.search(r"async function reloadCabinetAfterRoleChange\(\) \{(.*?)\n  renderLessons", APP_JS, re.S)
        self.assertIsNotNone(m, "reloadCabinetAfterRoleChange body not found")
        body = m.group(1)
        for field in (
            "state.clientHomeBooted = false;",
            "state.clientHomeActiveChildId = null;",
            "state.clientPaymentsFilterChildId = null;",
            "state.clientPayments = [];",
            "state.notifications = null;",
        ):
            self.assertIn(field, body, f"missing reset: {field}")


class TestAvailabilityAndPaymentsUseSelectedChild(unittest.TestCase):
    def test_8_availability_card_uses_active_child(self):
        m = re.search(r"function _cabAvailabilityCardHtml\(activeChild\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn("activeChild.mk_user_id", m.group(1))
        self.assertIn("state.clientHomeActiveChildId", APP_JS)

    def test_9_payments_filter_by_child_exists(self):
        self.assertIn("function _renderClientPaymentsList", APP_JS)
        self.assertIn("clientPaymentsFilterChildId", APP_JS)
        self.assertIn("data-cp-filter", APP_JS)


class TestNotificationsSingleEndpoint(unittest.TestCase):
    def test_10_notifications_fetched_from_one_place(self):
        hits = APP_JS.count('"/api/client/notifications"')
        self.assertEqual(hits, 1, "expected exactly one GET call site for the notifications list endpoint")


class TestLoadingEmptyErrorRetry(unittest.TestCase):
    def test_11_loading_states_present(self):
        for fn_name in ("loadClientNotifications", "loadClientPayments", "loadClientHomeData"):
            m = re.search(rf"function {fn_name}\(.*?\n\}}", APP_JS, re.S)
            self.assertIsNotNone(m, fn_name)
            self.assertIn("uiLoadingRows(", m.group(0))

    def test_12_empty_states_present(self):
        self.assertIn('uiEmptyState("💳"', APP_JS)
        self.assertIn("Пока нет уведомлений", APP_JS)

    def test_13_error_states_with_retry_callback(self):
        for call in (
            'uiErrorState(data.error || "Ошибка загрузки", "loadClientPayments()")',
            'uiErrorState(safeUserError(err), "loadClientPayments()")',
            'uiErrorState(data.error || "Ошибка загрузки", "loadClientNotifications(false)")',
        ):
            self.assertIn(call, APP_JS)

    def test_14_retry_button_wired_in_shared_error_state(self):
        m = re.search(r"function _wsErrorState\(message, retryOnclick\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn('onclick="${retryOnclick}"', m.group(1))
        self.assertIn(">Повторить<", m.group(1))
        # uiErrorState must be a thin passthrough, not a second parallel implementation
        m2 = re.search(r"function uiErrorState\(message, retryOnclick\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m2)
        self.assertIn("_wsErrorState(message, retryOnclick)", m2.group(1))


class TestDoubleSubmitGuards(unittest.TestCase):
    def test_15_busy_guards_present(self):
        for guard in (
            "if (state.notificationsBusy) return;",
            "if (state.clientPaymentsBusy) return;",
            "if (_clientHomeLoadBusy) return;",
            "let _notifDetailReqToken = 0;",
        ):
            self.assertIn(guard, APP_JS)


class TestAvailabilityUnsavedWarning(unittest.TestCase):
    def test_16_unsaved_changes_guard_wired(self):
        self.assertIn("function _ocAvailIsDirty()", APP_JS)
        self.assertIn("function _availScreenLeave()", APP_JS)
        m = re.search(r"function _availScreenLeave\(\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn("uiConfirmSheet(", m.group(1))
        self.assertIn("_ocAvailIsDirty()", m.group(1))
        # wired to all three exit points: in-page back, secondary button, hardware BackButton
        self.assertIn('$("availScreenBack")?.addEventListener("click", _availScreenLeave);', APP_JS)
        self.assertIn('$("availSecondaryBtn")?.addEventListener("click", _availScreenLeave);', APP_JS)
        self.assertIn('_appSetBackButton(_availScreenLeave);', APP_JS)
        # baseline is captured on load and cleared on successful save, both save paths
        self.assertIn("_ocAvailMarkClean();", APP_JS)


if __name__ == "__main__":
    unittest.main()
