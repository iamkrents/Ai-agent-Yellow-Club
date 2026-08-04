"""Tests for v7.1.16.1 — Payment Period Filters: frontend UI.

Static source-regex checks — same technique used by the v7.1.16 UI test
files (this project has no JS test runner); functional/visual behavior was
additionally confirmed via a real Playwright render (see the final report).

Covers (UI 21-34 from the launch spec):
  21. Defaults to the current month.
  22. Previous month navigation.
  23. Next month navigation.
  24. "Этот месяц" quick preset.
  25. "Прошлый месяц" quick preset.
  26. Custom date range.
  27. "Всё время".
  28. Period persists across tabs (Обзор/Требуют внимания/Все платежи).
  29. Retry preserves the selected period.
  30. An old response can't overwrite a newer period (request-token fencing).
  31. No horizontal overflow at 360/375/390 (confirmed via Playwright render,
      referenced here as a static safeguard against reintroducing fixed
      pixel widths in the new CSS).
  32. Buttons have an adequate tap target (44px).
  33. "Без даты" (undated) is clearly shown.
  34. The global attention-outside-period notice opens all-time + attention.

Run:
    python -m unittest tests.test_payment_period_ui_v71161 -v
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest  # noqa: E402

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
STYLES_CSS = (ROOT / "miniapp" / "styles.css").read_text(encoding="utf-8")


def _fn_body(name_pattern: str) -> str:
    m = re.search(rf"{name_pattern} \{{(.*?)\n\}}", APP_JS, re.S)
    return m.group(1) if m else ""


class TestDefaultsToCurrentMonth(unittest.TestCase):
    def test_21_default_mode_is_month_and_server_resolved(self):
        self.assertIn('mode: "month",        // "month" | "custom" | "all"', APP_JS)
        # never guesses "today" from the browser clock — synced from the
        # server's period.start in the response instead.
        self.assertIn("function _wsPeriodSyncFromResponse(period)", APP_JS)
        body = _fn_body(r"function _wsPeriodSyncFromResponse\(period\)")
        self.assertIn("period.start.slice(0, 7)", body)


class TestMonthNavigation(unittest.TestCase):
    def test_22_prev_month_button_wired(self):
        body = _fn_body(r"function _wsWirePeriodBar\(\)")
        self.assertIn('$("wsPeriodPrev")?.addEventListener("click"', body)
        self.assertIn("_wsPeriodMonthAdd(_wsPeriodState.month, -1)", body)

    def test_23_next_month_button_wired(self):
        body = _fn_body(r"function _wsWirePeriodBar\(\)")
        self.assertIn('$("wsPeriodNext")?.addEventListener("click"', body)
        self.assertIn("_wsPeriodMonthAdd(_wsPeriodState.month, 1)", body)


class TestQuickPresets(unittest.TestCase):
    def test_24_this_month_preset(self):
        self.assertIn('data-ws-period-quick="this-month"', APP_JS)
        self.assertIn('key === "this-month"', APP_JS)

    def test_25_last_month_preset(self):
        self.assertIn('data-ws-period-quick="last-month"', APP_JS)
        self.assertIn('_wsPeriodMonthAdd(_wsPeriodState.defaultMonth || _wsPeriodState.month, -1)', APP_JS)

    def test_27_all_time_preset(self):
        self.assertIn('data-ws-period-quick="all"', APP_JS)


class TestCustomRange(unittest.TestCase):
    def test_26_custom_range_inputs_and_validation(self):
        self.assertIn('id="wsPeriodCustomStart"', APP_JS)
        self.assertIn('id="wsPeriodCustomEnd"', APP_JS)
        body = _fn_body(r"function _wsPeriodValidateCustomRange\(\)")
        self.assertIn('customEnd < customStart', body)
        self.assertIn("Дата окончания не может быть раньше даты начала.", body)
        # apply-button handler validates before firing any request
        apply_body = re.search(r'\$\("wsPeriodCustomApply"\)\?\.addEventListener\("click", \(\) => \{(.*?)\}\);', APP_JS, re.S)
        self.assertIsNotNone(apply_body)
        self.assertIn("_wsPeriodValidateCustomRange()", apply_body.group(1))
        self.assertIn("return;", apply_body.group(1))


class TestPeriodPersistsAcrossTabs(unittest.TestCase):
    def test_28_period_bar_rendered_on_all_three_tabs(self):
        for fn_name in (r"function _wsRenderOverview\(root\)", r"function _wsRenderAttention\(root\)", r"function _wsRenderAllPayments\(root\)"):
            m = re.search(rf"{fn_name} \{{(.*?)\n\}}", APP_JS, re.S)
            self.assertIsNotNone(m, fn_name)
            self.assertIn("_wsPeriodBarHtml()", m.group(1))
        # _wsPeriodState itself is a single module-level object, never
        # re-initialized per tab switch — that IS the persistence mechanism.
        self.assertEqual(APP_JS.count("const _wsPeriodState = {"), 1)

    def test_28b_pilot_clients_tab_not_period_scoped(self):
        body = _fn_body(r"function _wsRenderPilotClients\(root\)")
        self.assertNotIn("_wsPeriodBarHtml", body)


class TestRetryPreservesPeriod(unittest.TestCase):
    def test_29_retry_calls_reenter_the_same_period_aware_loader(self):
        for retry_call in ("_loadWorkspaceStats()", "_loadWorkspaceAttention()", "_loadWorkspaceAllPayments()"):
            self.assertIn(f'"{retry_call}"', APP_JS)
            fn_name = retry_call.split("(")[0]
            body = _fn_body(rf"async function {fn_name}\(\)")
            self.assertIn("_wsPeriodQueryParams()", body)


class TestRequestTokenFencing(unittest.TestCase):
    def test_30_central_token_bump_and_capture_compare_pattern(self):
        changed_body = _fn_body(r"function _wsPeriodChanged\(\)")
        self.assertIn("_wsPeriodState.reqToken++;", changed_body)
        for fn_name in (r"async function _loadWorkspaceStats\(\)", r"async function _loadWorkspaceAttention\(\)", r"async function _loadWorkspaceAllPayments\(\)"):
            body = _fn_body(fn_name)
            self.assertIn("const myToken = _wsPeriodState.reqToken;", body)
            self.assertIn("if (myToken !== _wsPeriodState.reqToken) return;", body)
            # loaders must never themselves increment the shared token
            self.assertNotIn("_wsPeriodState.reqToken++", body)
            self.assertNotIn("++_wsPeriodState.reqToken", body)


class TestNoFixedWidthsInNewCss(unittest.TestCase):
    def test_31_period_bar_css_has_no_fixed_pixel_widths(self):
        m = re.search(r"/\* v7\.1\.16\.1 — Payments Workspace period filter bar.*?(?=\n/\* )", STYLES_CSS, re.S)
        self.assertIsNotNone(m, "v7.1.16.1 period-bar CSS block not found")
        # exclude min-width/max-width (44px tap targets are intentional) —
        # only a bare "width: Npx" (forcing a fixed element width) is a
        # horizontal-overflow risk on a narrow viewport.
        self.assertNotRegex(m.group(0), r"(?<!-)width:\s*\d+px")


class TestTapTargets(unittest.TestCase):
    def test_32_nav_and_quick_buttons_meet_44px(self):
        self.assertIn("min-width: 44px; min-height: 44px;", STYLES_CSS.replace("\n", " "))
        self.assertIn(".ws-oc-mode-btn--lg { min-height: 44px; }", STYLES_CSS)
        self.assertIn(".ws-period-custom-apply { min-height: 44px", STYLES_CSS)


class TestUndatedDisplayed(unittest.TestCase):
    def test_33_undated_count_shown_in_all_time_mode(self):
        body = _fn_body(r"function _wsRenderOverview\(root\)")
        self.assertIn("s.undated_count", body)
        self.assertIn("без даты", body)
        # only surfaced in all-time mode, per spec (never implied for a
        # specific month)
        self.assertIn('period.mode === "all"', body)


class TestGlobalAttentionNotice(unittest.TestCase):
    def test_34_notice_switches_to_all_time_and_attention_tab(self):
        self.assertIn("attentionOutsidePeriodCount", APP_JS)
        self.assertIn('id="wsPeriodOutsideNotice"', APP_JS)
        handler = re.search(r'\$\("wsPeriodOutsideNotice"\)\?\.addEventListener\("click", \(\) => \{(.*?)\}\);', APP_JS, re.S)
        self.assertIsNotNone(handler)
        body = handler.group(1)
        self.assertIn('_wsPeriodState.mode = "all";', body)
        self.assertIn("_wsPeriodChanged();", body)
        self.assertIn('_wsActivateTab("attention");', body)
        # only rendered when count > 0 — never an empty/misleading notice
        overview_body = _fn_body(r"function _wsRenderOverview\(root\)")
        self.assertIn("(s.attentionOutsidePeriodCount || 0) > 0", overview_body)


if __name__ == "__main__":
    unittest.main()
