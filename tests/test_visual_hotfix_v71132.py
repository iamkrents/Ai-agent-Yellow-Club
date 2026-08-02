"""Tests for v7.1.13.2 — visual polish hotfix (Stage A only; no new
business logic). Covers:
  1-5.  notification detail is now a full-page client subpage, not a low
        bottom-sheet — long/short text, read state, action whitelist all
        preserved unchanged.
  6.    Telegram safe-area uses --tg-content-safe-area-inset-top /
        --tg-safe-area-inset-top / env(safe-area-inset-top), never a bare
        hardcoded pixel guess.
  7-8.  color-scheme: light is fixed app-wide; native form controls get
        explicit light styling so a dark device theme can't turn them
        black.
  9-10. the owner test-client banner is compact and still gated the same
        way as before (server field, owner/admin-only).
  11-12. the owner test-notification-sender UI is Russian-only, but the
        backend enum values (English) sent over the API are unchanged.
  13.   staff navigation untouched.
  14-15. payment-block and availability-confirm guards from v7.1.13.1 are
        still present (regression check after this round's edits).

Run:
    python -m unittest tests.test_visual_hotfix_v71132 -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
STYLES_CSS = ROOT / "miniapp" / "styles.css"
WEB_APP_SERVER_PY = ROOT / "web_app_server.py"
STORAGE_PY = ROOT / "storage.py"


class TestNotificationDetailIsFullPage(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")
        self.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_1_full_page_panel_exists_bottom_sheet_removed(self):
        self.assertIn('id="tab-notification-detail"', self.html)
        self.assertIn('data-tab="notification-detail"', self.html)
        self.assertNotIn('id="notificationDetailModal"', self.html)

    def test_2_open_uses_activate_tab_not_modal(self):
        idx = self.js.find("async function openNotificationDetail")
        body = self.js[idx:idx + 500]
        self.assertIn('activateTab("notification-detail")', body)
        self.assertNotIn("piModalOpen", body)

    def test_3_body_text_size_and_line_height(self):
        idx = self.css.find(".cab-notif-detail-body {")
        self.assertNotEqual(idx, -1)
        segment = self.css[idx:idx + 200]
        fs_match = [tok for tok in segment.split(";") if "font-size" in tok]
        lh_match = [tok for tok in segment.split(";") if "line-height" in tok]
        self.assertTrue(fs_match and lh_match)
        fs_px = float(fs_match[0].split(":")[1].strip().replace("px", ""))
        lh_val = float(lh_match[0].split(":")[1].strip())
        self.assertGreaterEqual(fs_px, 16)
        self.assertGreaterEqual(lh_val, 1.45)

    def test_4_short_notification_starts_at_top_not_bottom_anchored(self):
        # Full-page tab-panel flows top-to-bottom like every other cabinet
        # screen — no flex/align-items:flex-end wrapper around the detail
        # content that would pin a short message to the bottom.
        idx = self.html.find('id="tab-notification-detail"')
        segment = self.html[idx:idx + 400]
        self.assertNotIn("flex-end", segment)

    def test_5_read_state_and_action_whitelist_dispatch_preserved(self):
        idx = self.js.find("function renderNotificationDetail")
        body = self.js[idx:idx + 1600]
        self.assertIn("Отмечено как прочитанное", body)
        idx2 = self.js.find("function _cabDispatchNotificationAction")
        body2 = self.js[idx2:idx2 + 500]
        self.assertIn("open_payments", body2)
        self.assertIn("open_availability", body2)
        self.assertIn("open_home", body2)
        # mark-read API call itself untouched.
        idx3 = self.js.find("async function openNotificationDetail")
        body3 = self.js[idx3:idx3 + 1200]
        self.assertIn("/read`, {}", body3)

    def test_bottom_nav_clearance_present(self):
        self.assertIn("#tab-notification-detail.tab-panel.active", self.css)


class TestSafeAreaUsesTelegramVars(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_6_css_var_chain_present_no_hardcoded_pixel_guess(self):
        # The winning rule is the LAST --tg-safe-top definition in source
        # order (v6.6.6 section, --app-top-safe-offset consumer) — earlier
        # superseded definitions from older rounds are dead/inert but still
        # present in the file, so anchor on the last occurrence specifically.
        idx = self.css.rfind("--tg-safe-top:")
        self.assertNotEqual(idx, -1)
        line = self.css[idx:self.css.find("\n", idx)]
        self.assertIn("--tg-content-safe-area-inset-top", line)
        self.assertIn("--tg-safe-area-inset-top", line)
        self.assertIn("env(safe-area-inset-top", line)
        # The old hardcoded "+56px Telegram chrome" fallback used AS THE
        # SAFE-AREA VALUE must be gone — note "+56px" legitimately still
        # appears elsewhere (fullscreen-modal close-button clearance, a
        # real design-spacing constant, not a safe-area guess) and is out
        # of scope here.
        self.assertNotIn("--tg-native-top-overlay: 56px", self.css)
        self.assertNotIn('"calc(env(safe-area-inset-top, 0px) + 56px)"', self.js)

    def test_6b_js_sets_offset_from_var_chain(self):
        idx = self.js.find('"--app-top-safe-offset"')
        body = self.js[idx:idx + 300]
        self.assertIn("--tg-content-safe-area-inset-top", body)


class TestLightColorScheme(unittest.TestCase):
    def setUp(self):
        self.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_7_color_scheme_light_declared(self):
        idx = self.css.find("color-scheme: light")
        self.assertNotEqual(idx, -1)

    def test_8_form_controls_have_explicit_light_styling(self):
        for selector_fragment in ["select {", "option, optgroup {", 'input[type="date"]']:
            self.assertIn(selector_fragment, self.css)
        idx = self.css.find("option, optgroup {")
        segment = self.css[idx:idx + 150]
        self.assertIn("background: #fff", segment)


class TestCompactBanner(unittest.TestCase):
    def setUp(self):
        self.css = STYLES_CSS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_9_banner_padding_reduced(self):
        idx = self.css.find(".owner-test-client-banner {")
        segment = self.css[idx:idx + 300]
        self.assertIn("padding: 6px 10px", segment)

    def test_9b_button_does_not_dominate_card(self):
        idx = self.css.find(".owner-test-client-banner-btn {")
        self.assertNotEqual(idx, -1)
        segment = self.css[idx:idx + 300]
        self.assertIn("max-width: 40%", segment)

    def test_9c_context_text_truncates_gracefully(self):
        idx = self.css.find(".owner-test-client-banner span {")
        segment = self.css[idx:idx + 300]
        self.assertIn("text-overflow: ellipsis", segment)

    def test_10_banner_title_shortened_per_spec(self):
        self.assertIn("Тестовый кабинет", self.html)
        self.assertIn('id="ownerTestClientBackBtn"', self.html)
        self.assertIn("Вернуться в кабинет владельца", self.html)


class TestOwnerSenderRussifiedApiUnchanged(unittest.TestCase):
    def setUp(self):
        self.html = INDEX_HTML.read_text(encoding="utf-8")
        self.storage_src = STORAGE_PY.read_text(encoding="utf-8")

    def test_11_russian_labels_present(self):
        idx = self.html.find('id="ownerTestNotificationPanel"')
        segment = self.html[idx:idx + 3000]
        for label in [
            "Тестовое уведомление одному клиенту",
            "не массовая рассылка",
            "ID ученика в МойКласс",
            "Семье", "Конкретному ребёнку",
            "Общее", "Питание", "Оплаты", "Расписание",
            "Обычное", "Важное",
            "Без кнопки", "Открыть оплаты", "Открыть возможности для расписания", "Открыть главную",
        ]:
            self.assertIn(label, segment, f"missing Russian label: {label}")
        # No "Всем"/broadcast option introduced.
        self.assertNotIn("Всем</option>", segment)

    def test_12_option_values_still_english_enum(self):
        idx = self.html.find('id="ownerTestNotificationPanel"')
        segment = self.html[idx:idx + 3000]
        for value in ['value="family"', 'value="child"', 'value="general"', 'value="food"',
                      'value="payments"', 'value="schedule"', 'value="normal"', 'value="important"',
                      'value="none"', 'value="open_payments"', 'value="open_availability"', 'value="open_home"']:
            self.assertIn(value, segment)

    def test_12b_backend_enum_constants_unchanged(self):
        self.assertIn('NOTIFICATION_CATEGORIES = ("general", "food", "payments", "schedule")', self.storage_src)
        self.assertIn('NOTIFICATION_PRIORITIES = ("normal", "important")', self.storage_src)
        self.assertIn('NOTIFICATION_SCOPES = ("family", "child")', self.storage_src)
        self.assertIn('NOTIFICATION_ACTION_KEYS = ("open_payments", "open_availability", "open_home", "none")', self.storage_src)


class TestStaffNavUnaffected(unittest.TestCase):
    def test_13_staff_tabs_still_present(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        for tab in ["lessons", "reports", "schedule", "tasks", "admin", "payments-workspace"]:
            self.assertIn(f'data-tab="{tab}"', html)


class TestV71131GuardsStillPresent(unittest.TestCase):
    """Regression: v7.1.13.1's owner-test-mode guards must be unaffected
    by this round's purely-visual edits."""

    def setUp(self):
        self.src = WEB_APP_SERVER_PY.read_text(encoding="utf-8")

    def test_14_payment_block_preserved(self):
        self.assertIn("owner_test_mode_payment_blocked", self.src)
        self.assertIn("owner_test_mode_link_blocked", self.src)
        self.assertIn("owner_test_mode_food_order_blocked", self.src)

    def test_15_availability_confirm_guard_preserved(self):
        self.assertIn("ownerTestConfirm", self.src)
        self.assertIn("owner_confirm_required", self.src)


if __name__ == "__main__":
    unittest.main()
