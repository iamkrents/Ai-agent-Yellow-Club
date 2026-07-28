"""Static-analysis tests for v7.1.8 — training-state UI in the Attention tab.

Covers: training badges, check/resume buttons, resume bottom sheet, the
Auto-mode warning text, the published-invoice warning (reusing the existing
withdrawal flow/permissions, no new endpoint), the new Help Center section,
mobile touch targets, and the temporary localhost-only preview harness.

Static text/AST-style checks only (reads app.js/index.html/styles.css as
text) — consistent with this repo's existing frontend test convention.
No browser, no real fetch. Run offline:
    python -m unittest tests.test_training_pause_ui -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
STYLES_CSS = (ROOT / "miniapp" / "styles.css").read_text(encoding="utf-8")


class TestTrainingBadges(unittest.TestCase):
    def test_01_training_badge_mapping_present(self):
        self.assertIn("const WS_TRAINING_BADGE", APP_JS)
        for code in (
            "client_training_paused", "training_subscription_frozen",
            "client_training_finished", "training_join_completed",
            "client_resume_confirmation_required",
        ):
            self.assertIn(code, APP_JS)

    def test_02_reason_message_never_raw_code_only(self):
        # backend readable_reason (already safe text) is rendered via
        # _wsAttentionReasonHtml; the frontend badge map never shows the raw
        # reason_code string as a label.
        self.assertIn("_wsTrainingBadgeInfo", APP_JS)
        self.assertIn('"Обучение приостановлено"', APP_JS)
        self.assertNotIn('>{reasonCode}<', APP_JS)


class TestCheckButton(unittest.TestCase):
    def test_03_check_button_present(self):
        self.assertIn("Проверить статус в МойКласс", APP_JS)
        self.assertIn("_wsTrainingCheck", APP_JS)

    def test_04_check_button_loading_state(self):
        m = re.search(r"async function _wsTrainingCheck\(itemId, btn\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("btn.disabled = true", body)
        self.assertIn("Проверяю", body)

    def test_05_check_api_call_path(self):
        self.assertIn("/training-check", APP_JS)

    def test_06_card_updates_without_full_workspace_reset(self):
        m = re.search(r"async function _wsTrainingCheck\(itemId, btn\) \{(.*?)\n\}", APP_JS, re.S)
        body = m.group(1)
        self.assertIn("_loadWorkspaceAttention", body)
        self.assertNotIn("loadPaymentsWorkspace()", body)

    def test_07_scroll_not_intentionally_reset(self):
        for fn_name in ("_wsTrainingCheck", "_wsTrainingConfirmResume", "_wsTrainingOpenResume"):
            m = re.search(rf"function {fn_name}\([^)]*\) \{{(.*?)\n\}}", APP_JS, re.S)
            self.assertIsNotNone(m, fn_name)
            self.assertNotIn("scrollTo", m.group(1))
            self.assertNotIn("scrollIntoView", m.group(1))


class TestResumeFlow(unittest.TestCase):
    def test_08_resume_button_only_for_resume_confirmation_required(self):
        m = re.search(r'if \(item\.reason_code === "client_resume_confirmation_required"\) \{(.*?)\} else \{', APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn("_wsTrainingOpenResume", m.group(1))

    def test_09_resume_bottom_sheet_present(self):
        self.assertIn('id="wsTrainingResumeModal"', INDEX_HTML)
        self.assertIn("Возобновить автоматизацию?", INDEX_HTML)

    def test_10_auto_mode_warning_text(self):
        self.assertIn(
            'В режиме «Авто» следующий цикл может создать и отправить оплату автоматически.',
            INDEX_HTML,
        )

    def test_11_resume_api_call_path(self):
        self.assertIn("/training-resume", APP_JS)

    def test_12_resume_success_notice(self):
        m = re.search(r"async function _wsTrainingConfirmResume\(\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIn('setNotice(d.message', m.group(1))
        self.assertIn('"success"', m.group(1))

    def test_13_resume_failure_notice(self):
        m = re.search(r"async function _wsTrainingConfirmResume\(\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIn("wsTrainingResumeError", m.group(1))

    def test_14_no_browser_confirm(self):
        for fn_name in ("_wsTrainingCheck", "_wsTrainingConfirmResume", "_wsTrainingOpenResume", "_wsTrainingCloseResume"):
            m = re.search(rf"(?:async )?function {fn_name}\([^)]*\) \{{(.*?)\n\}}", APP_JS, re.S)
            self.assertIsNotNone(m, fn_name)
            self.assertNotRegex(m.group(1), r"[^_]confirm\(")


class TestPublishedInvoiceWarning(unittest.TestCase):
    def test_15_published_invoice_warning_text(self):
        self.assertIn("У ученика на паузе уже есть опубликованный неоплаченный счёт.", APP_JS)
        self.assertIn("Оставить счёт", APP_JS)
        self.assertIn("Отозвать счёт", APP_JS)

    def test_16_withdraw_button_follows_existing_permission(self):
        m = re.search(r"function _wsTrainingPublishedWarningHtml\(item\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("canWithdrawInvoice()", body)
        self.assertIn("openWithdrawModal", body)
        # No new withdrawal endpoint — reuses the existing modal/global function.
        self.assertNotIn("/api/payments/intents/", body)

    def test_17_client_manager_cannot_bypass_withdrawal(self):
        m = re.search(r"function canWithdrawInvoice\(\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn('"owner"', body)
        self.assertIn('"admin"', body)
        self.assertIn('"operations"', body)
        self.assertNotIn('"client_manager"', body)

    def test_16b_no_admin_note_when_withdraw_button_shown(self):
        # withdrawNote (the "contact an admin" text) is the else-branch of the
        # same canWithdrawInvoice() check as withdrawBtn — mutually exclusive.
        m = re.search(r"function _wsTrainingPublishedWarningHtml\(item\) \{(.*?)\n\}", APP_JS, re.S)
        body = m.group(1)
        self.assertIn("Для отзыва обратитесь к администратору или операционному менеджеру.", body)


class TestHelpCenterSection(unittest.TestCase):
    def test_18_help_section_added(self):
        self.assertIn("_wsHelpTrainingPauseHtml", APP_JS)
        self.assertIn("Пауза обучения и каникулы", APP_JS)
        self.assertIn("ws-help-training-pause", APP_JS)

    def test_19_help_search_finds_pause(self):
        m = re.search(r'id="ws-help-training-pause" data-help-search="([^"]+)"', APP_JS)
        self.assertIsNotNone(m)
        self.assertIn("пауза", m.group(1))

    def test_20_help_search_input_not_recreated(self):
        # The search wiring function itself is untouched — still binds to the
        # single existing #wsHelpSearchInput, never recreated per keystroke.
        self.assertEqual(APP_JS.count('id="wsHelpSearchInput"'), 1)
        m = re.search(r"function _wsWireHelpSearch\(\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIn("addEventListener", m.group(1))

    def test_help_toc_entry_added(self):
        m = re.search(r"function _wsHelpTocHtml\(\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIn("Пауза обучения и каникулы", m.group(1))

    def test_help_section_assembled_into_screen(self):
        m = re.search(r"function _wsHelpScreenHtml\(\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIn("_wsHelpTrainingPauseHtml()", m.group(1))


class TestMobileUX(unittest.TestCase):
    def test_21_mobile_touch_targets_44px(self):
        self.assertIn("min-height: 44px", STYLES_CSS)
        self.assertIn("[data-ws-training-check]", STYLES_CSS)

    def test_21b_training_warning_css_present(self):
        self.assertIn(".ws-training-warning", STYLES_CSS)
        self.assertIn(".ws-badge--danger", STYLES_CSS)


class TestPreviewRemoved(unittest.TestCase):
    """v7.1.8 release cleanup: the temporary localhost-only training-pause
    preview (dev_preview=training-pause) has been visually approved and
    fully removed. These replace the old TestPreviewHarness /
    TestPreviewRealCapabilityContract classes, which asserted the preview's
    presence — now we assert its absence, that the real production boot()/
    navigation/capabilities wiring is untouched, and that the actual
    training-pause functionality it was previewing is still present.
    """

    def test_22_no_preview_markers_in_index_html(self):
        for marker in (
            "dev_preview", "LOCAL PREVIEW", "TEMPORARY", "blocked_in_preview",
            "PREVIEW_ME", "PREVIEW_ITEMS", "_wsPreviewAssert", "_wsPreviewFail",
            "__YC_DEV_PREVIEW__", "Preview Client Manager",
        ):
            self.assertNotIn(marker, INDEX_HTML, f"leftover preview marker: {marker}")

    def test_23_no_preview_markers_in_app_js(self):
        for marker in (
            "dev_preview", "LOCAL PREVIEW", "blocked_in_preview",
            "PREVIEW_ME", "PREVIEW_ITEMS", "_wsPreviewAssert", "_wsPreviewFail",
        ):
            self.assertNotIn(marker, APP_JS, f"leftover preview marker: {marker}")

    def test_no_inline_script_blocks_left_in_index_html(self):
        # The preview was the only inline <script> content in index.html —
        # production only ever references external app.js/styles.css.
        self.assertEqual(re.findall(r"<script>", INDEX_HTML), [])

    def test_production_boot_calls_load_me(self):
        m = re.search(r"async function boot\(\) \{(.*?)\nboot\(\);", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn("await loadMe()", m.group(1))

    def test_training_pause_help_anchor_still_present(self):
        # Real production Help Center content (not preview code) — must survive.
        self.assertIn('id="ws-help-training-pause"', APP_JS)

    def test_cache_bust_is_current_release(self):
        self.assertIn("app.js?v=7.1.8", INDEX_HTML)
        self.assertIn('console.log("MiniApp version: v7.1.8")', APP_JS)


class TestExistingScreensUnchanged(unittest.TestCase):
    def test_24_five_tabs_unchanged(self):
        m = re.search(r"function _renderWorkspaceSkeleton\b.*?const tabs = \[(.*?)\];", APP_JS, re.S)
        self.assertIsNotNone(m)
        tabs_block = m.group(1)
        for tab_id in ("overview", "attention", "all-payments", "pilot-clients", "connection"):
            self.assertIn(tab_id, tabs_block)

    def test_25_connection_tab_function_untouched(self):
        self.assertIn("function _wsRenderConnection(root)", APP_JS)

    def test_26_previous_help_sections_preserved(self):
        for anchor in (
            "ws-help-quickstart", "ws-help-chain", "ws-help-metrics",
            "ws-help-statuses", "ws-help-modes", "ws-help-connection",
            "ws-help-faq", "ws-help-escalation",
        ):
            self.assertIn(anchor, APP_JS)

    def test_27_overview_render_untouched(self):
        self.assertIn("function _wsRenderOverview", APP_JS)

    def test_28_all_payments_render_untouched(self):
        self.assertIn("_wsAllPaymentsUI", APP_JS)

    def test_29_pilot_clients_render_untouched(self):
        self.assertIn("_pilotAddClient", APP_JS)

    def test_30_food_module_frontend_untouched(self):
        self.assertIn("renderParentFoodMenu", APP_JS)
        start = APP_JS.find("function renderParentFoodMenu")
        segment = APP_JS[start:start + 2000].lower()
        self.assertNotIn("training", segment)


# ---------------------------------------------------------------------------
# v7.1.8 follow-up — "Оставить счёт" safety fix regression tests (section 1).
# The button must mean ONLY "do not withdraw this already-published invoice",
# never "lift the pause" — verified as a pure client-side dismiss with zero
# backend interaction.
# ---------------------------------------------------------------------------

class TestLeaveInvoiceButtonSemantics(unittest.TestCase):
    def _fn_body(self, name):
        m = re.search(rf"function {name}\(item\) \{{(.*?)\n\}}\n", APP_JS, re.S)
        return m.group(1) if m else ""

    def test_leave_invoice_1_no_backend_action_called(self):
        m = re.search(r'onclick="([^"]*)">Оставить счёт<', APP_JS)
        self.assertIsNotNone(m)
        onclick = m.group(1)
        self.assertNotIn("_apiPostRaw", onclick)
        self.assertNotIn("await ", onclick)
        self.assertIn(".remove()", onclick)

    def test_leave_invoice_2_does_not_reference_reason_code(self):
        body = self._fn_body("_wsTrainingPublishedWarningHtml")
        # The dismiss button's own onclick fragment must never touch item.reason_code —
        # only the surrounding render function reads it (read-only, for display).
        m = re.search(r'onclick="([^"]*)">Оставить счёт<', body)
        self.assertIsNotNone(m)
        self.assertNotIn("reason_code", m.group(1))

    def test_leave_invoice_3_does_not_reference_current_stage(self):
        m = re.search(r'onclick="([^"]*)">Оставить счёт<', APP_JS)
        self.assertNotIn("current_stage", m.group(1))
        self.assertNotIn("stage", m.group(1))

    def test_leave_invoice_4_does_not_reference_client_visibility(self):
        m = re.search(r'onclick="([^"]*)">Оставить счёт<', APP_JS)
        self.assertNotIn("client_visibility", m.group(1))
        self.assertNotIn("withdraw", m.group(1).lower())

    def test_leave_invoice_5_no_training_state_mutation_call(self):
        m = re.search(r'onclick="([^"]*)">Оставить счёт<', APP_JS)
        onclick = m.group(1)
        self.assertNotIn("_wsTrainingCheck", onclick)
        self.assertNotIn("_wsTrainingOpenResume", onclick)
        self.assertNotIn("_wsTrainingConfirmResume", onclick)

    def test_leave_invoice_6_scheduler_path_untouched(self):
        # Nothing in the button's onclick calls _loadWorkspaceAttention or any
        # reload — dismissing locally must not even refresh server state, so
        # there is nothing here the scheduler could ever interpret as "resume".
        m = re.search(r'onclick="([^"]*)">Оставить счёт<', APP_JS)
        onclick = m.group(1)
        self.assertNotIn("_loadWorkspaceAttention", onclick)
        self.assertNotIn("fetch(", onclick)

    def test_leave_invoice_7_resume_only_via_training_resume_button(self):
        # The ONLY way reason_code=client_resume_confirmation_required leads to
        # an actual resume is the separate "Подтвердить возобновление" button,
        # which is gated on a completely different reason_code branch than the
        # published-invoice warning ("Оставить счёт" belongs to the published-
        # warning branch only, never the resume-confirmation branch).
        self.assertIn("_wsTrainingOpenResume", APP_JS)
        published_block = self._fn_body("_wsTrainingPublishedWarningHtml")
        self.assertNotIn("_wsTrainingOpenResume", published_block)
        self.assertNotIn("_wsTrainingConfirmResume", published_block)


if __name__ == "__main__":
    unittest.main()
