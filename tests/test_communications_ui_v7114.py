"""Tests for v7.1.14 — staff "Рассылки": static UI checks (index.html/app.js).

Covers:
  41. UI labels are Russian-only in the new markup/JS.
  42. the approved 5-step structure exists (step labels, stepper).
  43. server-count preview states (calculating/calculated/zero/error/disabled)
      are all rendered distinctly.
  44. draft-save status texts are present ("Черновик сохраняется…" /
      "Черновик сохранён" / "Не удалось сохранить").
  45. send-now vs scheduled options both exist on the content/schedule step.
  46. the confirmation step shows the exact recipient count.
  47. Step 4 (Проверка) actions are a vertical stack, primary action first.
  48. the new panel doesn't introduce any competing fixed-position layout
      that would need its own safe-area handling (relies on the existing
      app-wide safe-area system, unchanged).
  49. no Telegram mass-channel toggle exists — Step 2 explicitly states
      in-app-only delivery.
  50. the existing owner single-client test sender stays a separate,
      untouched tool (markup + naming never overlap with the new module).
  51. staff navigation regression — pre-existing tabs unaffected.
  52. no new fixed pixel width > 360px / width:100vw introduced by the
      comms module (mobile-width static guard).

  v7.1.14 hotfix — a real full-height confirmation sheet replaces the
  window.confirm()/confirm() the send/schedule flow used before:
  59. no native confirm() anywhere in the send/schedule/close/finish/
      recalculate functions.
  60. #commsConfirmModal exists (index.html) and is opened via piModalOpen.
  61. the sheet shows the exact server-computed recipient count.
  62. the mass-operation warning text is present.
  63. both send-now and scheduled modes are handled by the same sheet.
  64. send kill switch -> disabled primary button with the required text.
  65. scheduler kill switch -> disabled primary button with the required text.
  66. submitting state disables both footer buttons and relabels the primary one.
  67. stale-snapshot state shows the recalculate error + button.
  68. count-mismatch state is distinguished from stale-snapshot via error_code.
  69. a second tap while submitting is a no-op (guarded at the top of the function).
  70. Telegram BackButton is wired to close the sheet first, and detached on every close path.
  71. footer buttons are a vertical, full-width stack (not the old row layout).
  72. safe-area insets are respected (top via sheet sizing, bottom via the shared footer rule).
  73. no fixed pixel widths / width:100vw in the new CSS or JS (360-375px safe).
  74. every new CSS rule is scoped under .comms-* / #commsConfirmModal.
  75. styles.css?v=7.1.14 / app.js?v=7.1.14 cache-busts are still in place.
  76. native confirm is not used for either the send or the schedule call.

Run:
    python -m unittest tests.test_communications_ui_v7114 -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
STYLES_CSS = ROOT / "miniapp" / "styles.css"


class TestCommsUI(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")
        self.css = STYLES_CSS.read_text(encoding="utf-8")
        idx = self.js.find("// ── v7.1.14 — staff")
        self.assertNotEqual(idx, -1, "v7.1.14 comms module marker missing")
        end = self.js.find("async function boot()")
        self.comms_js = self.js[idx:end]

    def test_nav_tab_and_panel_exist(self):
        self.assertIn('data-tab="comms"', self.html)
        self.assertIn('id="tab-comms"', self.html)
        self.assertIn("Рассылки", self.html)

    def test_41_russian_labels_present(self):
        for label in [
            "Кому отправляем", "Канал", "Содержание уведомления", "Проверка",
            "Черновики", "Запланированные", "Отправленные", "Создать рассылку",
            "Один клиент", "Все родители с активным кабинетом", "Кампания подключения",
            "Заполнили возможности для расписания", "Не заполнили возможности для расписания",
        ]:
            self.assertIn(label, self.comms_js, f"missing Russian label: {label}")

    def test_42_five_step_structure(self):
        self.assertIn('COMMS_STEP_LABELS = ["Получатели", "Канал", "Содержание", "Проверка", "Отправка"]', self.comms_js)
        self.assertIn("Шаг ${idx} из 5", self.comms_js)

    def test_43_preview_states_rendered_distinctly(self):
        for state_marker in ['"calculating"', '"error"', '"disabled"']:
            self.assertIn(state_marker, self.comms_js)
        self.assertIn("Считаем получателей", self.comms_js)
        self.assertIn("Количество рассчитано системой", self.comms_js)

    def test_44_draft_save_status_texts(self):
        for text in ["Черновик сохраняется…", "Черновик сохранён", "Не удалось сохранить"]:
            self.assertIn(text, self.comms_js)

    def test_45_send_now_and_scheduled_options(self):
        self.assertIn('value="now"', self.comms_js)
        self.assertIn('value="scheduled"', self.comms_js)
        self.assertIn("Отправить сразу", self.comms_js)
        self.assertIn("Запланировать", self.comms_js)

    def test_46_confirmation_shows_exact_count(self):
        m = re.search(r"function _commsConfirmFooterHtml\(\) \{(.*?)\n\}", self.comms_js, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("eligibleCount", body)
        self.assertIn("_commsRecipientsWord(n)", body)

    def test_47_step4_actions_vertical_primary_first(self):
        m = re.search(r"function _commsRenderStep4\(root, c\) \{(.*?)\n\}", self.comms_js, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("comms-step-actions-column", body)
        self.assertIn(".comms-step-actions-column { display: flex; flex-direction: column;", self.css)
        primary_idx = body.find("Перейти к подтверждению")
        secondary_idx = body.find("Изменить получателей")
        self.assertNotEqual(primary_idx, -1)
        self.assertNotEqual(secondary_idx, -1)
        self.assertLess(primary_idx, secondary_idx)
        self.assertIn('class="primary wide"', body[max(0, primary_idx - 200):primary_idx + 50])

    def test_48_no_new_fixed_position_layout_introduced(self):
        # The panel relies on the existing app-wide safe-area system
        # (.app-shell padding) — it must not introduce its own
        # position:fixed sheet that would need separate safe-area math.
        self.assertNotIn("position:fixed", self.comms_js)
        self.assertNotIn("position: fixed", self.comms_js)

    def test_49_no_telegram_mass_channel_toggle(self):
        m = re.search(r"function _commsRenderStep2\(root\) \{(.*?)\n\}", self.comms_js, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("В этой версии рассылки доставляются только в личный кабинет клиента", body)
        # "Telegram" legitimately appears once, in future tense, inside that
        # same disclaimer sentence ("...в Telegram будет добавлена позже") —
        # what must NOT exist is any interactive channel-selection control.
        self.assertNotIn("<input", body)
        self.assertNotIn("<select", body)
        self.assertNotIn("checkbox", body)

    def test_50_owner_test_sender_untouched_and_separate(self):
        self.assertIn('id="ownerTestNotificationPanel"', self.html)
        # No shared function names between the two tools.
        owner_fns = set(re.findall(r"function (ownerTestClient\w+|renderOwnerTestNotificationPanel)", self.js))
        comms_fns = set(re.findall(r"function (_comms\w+)", self.comms_js))
        self.assertTrue(owner_fns)
        self.assertTrue(comms_fns)
        self.assertEqual(owner_fns & comms_fns, set())

    def test_51_staff_navigation_regression(self):
        for tab in ["lessons", "reports", "schedule", "tasks", "admin", "payments-workspace"]:
            self.assertIn(f'data-tab="{tab}"', self.html)

    def test_52_no_stray_wide_fixed_widths_in_comms_module(self):
        for bad in ["width:100vw", "width: 100vw", "min-width:400px", "min-width: 400px"]:
            self.assertNotIn(bad, self.comms_js)


# ── Confirmation sheet — replaces the old window.confirm()/confirm() this
# feature used before. See _commsOpenConfirm/_commsConfirmSubmit/
# _commsRenderConfirmSheet in app.js and #commsConfirmModal in index.html.
class TestCommsConfirmSheet(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")
        self.css = STYLES_CSS.read_text(encoding="utf-8")
        idx = self.js.find("// ── v7.1.14 — staff")
        self.assertNotEqual(idx, -1, "v7.1.14 comms module marker missing")
        end = self.js.find("async function boot()")
        self.comms_js = self.js[idx:end]

    def _fn_body(self, signature_regex):
        m = re.search(signature_regex + r" \{(.*?)\n\}", self.comms_js, re.S)
        self.assertIsNotNone(m, f"function not found: {signature_regex}")
        return m.group(1)

    def test_59_no_native_confirm_in_send_or_schedule_flow(self):
        for fn in ("_commsOpenConfirm", "_commsConfirmSubmit", "_commsConfirmRecalculate", "_commsConfirmClose", "_commsConfirmFinish"):
            body = self._fn_body(rf"(?:async )?function {fn}\(\)")
            self.assertNotRegex(body, r"(?<!\w)confirm\(", f"{fn} must not call confirm()")
            self.assertNotIn("window.confirm(", body)

    def test_60_custom_confirmation_sheet_exists(self):
        self.assertIn('id="commsConfirmModal"', self.html)
        self.assertIn('class="pi-modal-sheet comms-confirm-sheet"', self.html)
        self.assertIn('id="commsConfirmBody"', self.html)
        self.assertIn('id="commsConfirmFooter"', self.html)
        self.assertIn("piModalOpen(document.getElementById(\"commsConfirmModal\"))", self.comms_js)

    def test_61_sheet_shows_exact_recipient_count(self):
        body = self._fn_body(r"function _commsConfirmReviewHtml\(\)")
        self.assertIn("comms-confirm-count", body)
        self.assertIn("c.eligibleCount || 0", body)
        footer = self._fn_body(r"function _commsConfirmFooterHtml\(\)")
        self.assertIn("${n} ${_commsRecipientsWord(n)}", footer)

    def test_62_mass_operation_warning_present(self):
        body = self._fn_body(r"function _commsConfirmReviewHtml\(\)")
        self.assertIn("Это массовая операция. После отправки отменить уведомления нельзя.", body)

    def test_63_send_now_and_scheduled_variants(self):
        open_fn = self._fn_body(r"async function _commsOpenConfirm\(\)")
        self.assertIn('cf.mode = scheduled ? "schedule" : "send"', open_fn)
        submit = self._fn_body(r"async function _commsConfirmSubmit\(\)")
        self.assertIn('cf.mode === "schedule"', submit)
        self.assertIn("/schedule`", submit)
        self.assertIn("/send`", submit)

    def test_64_send_disabled_state(self):
        footer = self._fn_body(r"function _commsConfirmFooterHtml\(\)")
        self.assertIn('"send_disabled"', footer)
        self.assertIn("Отправка отключена безопасным режимом.", footer)

    def test_65_scheduler_disabled_state(self):
        footer = self._fn_body(r"function _commsConfirmFooterHtml\(\)")
        self.assertIn('"scheduler_disabled"', footer)
        self.assertIn("Планирование отключено безопасным режимом.", footer)

    def test_66_submitting_state_disables_and_relabels_buttons(self):
        footer = self._fn_body(r"function _commsConfirmFooterHtml\(\)")
        self.assertIn('cf.state === "submitting"', footer)
        self.assertIn("Отправляем…", footer)
        self.assertIn("Планируем…", footer)
        self.assertIn('${submitting ? "disabled" : ""}', footer)

    def test_67_stale_snapshot_state(self):
        self.assertIn('"stale_snapshot"', self.comms_js)
        footer = self._fn_body(r"function _commsConfirmFooterHtml\(\)")
        self.assertIn('cf.state === "stale"', footer)
        review = self._fn_body(r"function _commsConfirmReviewHtml\(\)")
        self.assertIn("Аудитория изменилась. Пересчитайте получателей перед отправкой.", review)
        self.assertIn("_commsConfirmRecalculate()", footer)

    def test_68_count_mismatch_state(self):
        self.assertIn('"count_mismatch"', self.comms_js)
        footer = self._fn_body(r"function _commsConfirmFooterHtml\(\)")
        self.assertIn('cf.state === "mismatch"', footer)

    def test_69_double_submit_is_blocked(self):
        submit = self._fn_body(r"async function _commsConfirmSubmit\(\)")
        lines = [l.strip() for l in submit.strip().splitlines() if l.strip()]
        self.assertRegex(lines[1], r'if \(cf\.state === "submitting"')
        self.assertIn("return;", lines[1])

    def test_70_backbutton_closes_sheet_first(self):
        # v7.1.14.1 — _commsBackButtonShow/_commsBackButtonHide now delegate
        # to a shared single-handler stack (_commsSetBackButton) so the same
        # Telegram BackButton can also exit the whole comms section back to
        # «Админ» when no sheet is open (see test_6 in
        # test_communications_production_hotfix_v71141.py) — the actual
        # onClick/show/offClick/hide calls now live in that shared helper.
        self.assertIn("tg?.BackButton", self.comms_js)
        setter = self._fn_body(r"function _commsSetBackButton\(handler\)")
        self.assertIn("tg.BackButton.onClick(handler)", setter)
        self.assertIn("tg.BackButton.show()", setter)
        self.assertIn("tg.BackButton.hide()", setter)
        self.assertIn("function _commsBackButtonHandler() { _commsConfirmClose(); }", self.comms_js)
        self.assertIn("function _commsBackButtonShow() { _commsSetBackButton(_commsBackButtonHandler); }", self.comms_js)
        self.assertIn(
            "function _commsBackButtonHide() { _commsSetBackButton(_commsSectionActive ? _commsExitToAdmin : null); }",
            self.comms_js,
        )
        # BackButton is attached only while the sheet is open, and detached
        # (back to the section-level handler, or fully off) on every close path.
        open_fn = self._fn_body(r"async function _commsOpenConfirm\(\)")
        self.assertIn("_commsBackButtonShow()", open_fn)
        close_fn = self._fn_body(r"function _commsConfirmClose\(\)")
        self.assertIn("_commsBackButtonHide()", close_fn)
        finish_fn = self._fn_body(r"function _commsConfirmFinish\(\)")
        self.assertIn("_commsBackButtonHide()", finish_fn)

    def test_71_footer_buttons_vertical_full_width(self):
        self.assertIn(".comms-confirm-footer { flex-direction: column; }", self.css)
        self.assertIn(".comms-confirm-footer button { flex: 0 0 auto; width: 100%; }", self.css)
        self.assertIn('class="pi-modal-footer comms-confirm-footer"', self.html)

    def test_72_safe_area_respected(self):
        # Base .pi-modal-footer (design-system-wide) already pads for the
        # bottom safe area; the sheet itself sizes against the top inset,
        # via the app's own proven --app-top-safe-offset chain (v7.1.14.1 —
        # see test_communications_production_hotfix_v71141.py test_11),
        # not a bare env() — neither is duplicated/overridden for this module.
        self.assertIn("env(safe-area-inset-bottom", self.css)
        idx = self.css.find(".comms-confirm-sheet {")
        self.assertNotEqual(idx, -1)
        self.assertIn("var(--app-top-safe-offset, env(safe-area-inset-top, 0px))", self.css[idx:idx + 700])

    def test_73_no_horizontal_overflow_introduced(self):
        idx = self.css.find(".comms-confirm-sheet {")
        self.assertNotEqual(idx, -1)
        block = self.css[idx:self.css.find("/* the #commsConfirmModal")]
        # A fixed px WIDTH would risk horizontal overflow at 360px; a fixed
        # px HEIGHT (used for the >=600px desktop variant, alongside min())
        # does not, so only flag bare "width:" declarations here.
        for bad in re.findall(r"(?<!-)width:\s*\d+px", block):
            self.fail(f"fixed pixel width in confirm sheet CSS: {bad}")
        self.assertNotIn("width:100vw", self.comms_js)

    def test_74_comms_css_is_scoped(self):
        # Every selector this module adds is either .comms-* or scoped under
        # the #commsConfirmModal id — never a bare/global selector that could
        # leak into client cabinet, payments, Food Module or other staff screens.
        start = self.css.find("/* ── v7.1.14 — staff \"Рассылки\"")
        self.assertNotEqual(start, -1)
        block = self.css[start:]
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("/*") or stripped.startswith("*") or stripped.startswith("@media") or stripped == "}":
                continue
            if "{" not in stripped:
                continue
            selector = stripped.split("{")[0].strip()
            for sel in selector.split(","):
                sel = sel.strip()
                if not sel:
                    continue
                self.assertTrue(
                    sel.startswith(".comms-") or sel.startswith("#commsConfirmModal") or " .comms-" in sel or ".comms-" in sel,
                    f"unscoped selector in comms CSS block: {sel!r}",
                )

    def test_75_version_markers_preserved(self):
        self.assertIn("styles.css?v=7.1.14", self.html)
        self.assertIn("app.js?v=7.1.14", self.html)
        self.assertIn('console.log("MiniApp version: v7.1.14.3");', self.js)

    def test_76_native_confirm_not_used_for_send_nor_schedule(self):
        submit = self._fn_body(r"async function _commsConfirmSubmit\(\)")
        self.assertNotIn("confirm(", submit)
        schedule_call_idx = submit.find("/schedule`")
        send_call_idx = submit.find("/send`")
        self.assertNotEqual(schedule_call_idx, -1)
        self.assertNotEqual(send_call_idx, -1)


if __name__ == "__main__":
    unittest.main()
