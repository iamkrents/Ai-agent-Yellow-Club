"""Tests for v7.1.14.3 — narrow hotfix following real-device (iPhone) and
real-browser-render (Playwright, computed styles) verification of v7.1.14.2:

  1. Header still overlapped by Telegram's own Close/Menu controls on iOS,
     on every screen (staff, client cabinet, food-only, communications,
     availability, ...). Root cause, proven via getComputedStyle in a real
     Chromium render (not just source-reading): _applySafeArea() fully
     trusted Telegram's reported contentSafeAreaInset/safeAreaInsets even
     when they report exactly 0 — which real Telegram iOS sessions are
     known to do while their own header chrome is still physically present.
     Fixed with (a) a single, real DOM spacer element
     (#appTopSafeSpacer, the literal first child of .app-shell, before any
     hero/header/route content — replacing the old "give .app-shell itself
     a variable padding-top" model) and (b) a guarded floor, scoped to
     tg.platform === "ios" only, applied via Math.max (never summed).

  2. Availability branch buttons (Кульман 1/1 / Мстиславца 6 / Любой) look
     identical regardless of selection. Root cause, proven via
     getComputedStyle: these buttons always also carry the generic
     ".secondary" class, and ".secondary { ... !important }" (the app-wide
     secondary-button re-theme) unconditionally beat
     ".ws-oc-ttl-btn.active"'s non-!important background/color — the
     .active class WAS being toggled correctly, it just had zero visual
     effect. Fixed by adding !important to the narrowly-scoped
     .ws-oc-ttl-btn.active rule only.

  3. client_manager still saw "Назад в Админ" under Рассылки. Root cause,
     proven via a live /api/me capabilities check: canUseAdmin() is TRUE
     for client_manager whenever FOOD_MODULE_ENABLED=true, because
     client_manager is also allowed the unrelated personal "food-lunch"
     self-order admin sub-tab (_staff_food_roles in web_app_server.py) —
     canUseAdmin() was never a reliable proxy for "has real Admin-section
     access". Fixed by gating the comms "Назад в Админ" affordance (both
     the in-page button and the Telegram BackButton wiring) on a new
     canReturnToAdminFromComms() helper, keyed on the REAL role
     (state.me.realRole, immune to test-role substitution), not
     canUseAdmin() or state.me.role.

Run:
    python -m unittest tests.test_safe_area_branch_selection_hotfix_v71143 -v
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest  # noqa: E402

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
STYLES_CSS = ROOT / "miniapp" / "styles.css"
WEB_APP_SERVER_PY = ROOT / "web_app_server.py"


class TestHeaderSpacer(unittest.TestCase):
    def setUp(self):
        self.html = INDEX_HTML.read_text(encoding="utf-8")
        self.css = STYLES_CSS.read_text(encoding="utf-8")
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_1_single_app_shell_top_spacer_exists(self):
        self.assertIn('id="appTopSafeSpacer"', self.html)
        self.assertIn('class="app-top-safe-spacer"', self.html)
        self.assertEqual(self.html.count('id="appTopSafeSpacer"'), 1)

    def test_2_spacer_is_first_element_before_hero_route_content(self):
        shell_idx = self.html.find('<div class="app-shell">')
        spacer_idx = self.html.find('id="appTopSafeSpacer"')
        topbar_idx = self.html.find('<header class="topbar">')
        self.assertGreater(shell_idx, -1)
        self.assertGreater(spacer_idx, shell_idx)
        self.assertGreater(topbar_idx, spacer_idx, "spacer must come before the topbar/hero content")

    def test_3_offset_applies_to_shared_shell_not_only_modal(self):
        self.assertIn(
            "body.is-telegram-webapp .app-top-safe-spacer {\n  height: var(--app-top-safe-offset);\n}",
            self.css,
        )
        # still also used by the confirmation sheet (see test_11) — the
        # variable is shared, not modal-exclusive.
        self.assertIn("var(--app-top-safe-offset)", self.css)

    def test_4_telegram_safe_area_inset_is_read(self):
        self.assertIn("tg?.safeAreaInsets?.top", self.js)

    def test_5_telegram_content_safe_area_inset_is_read(self):
        self.assertIn("tg?.contentSafeAreaInset", self.js)

    def test_6_guarded_ios_fallback_exists(self):
        self.assertIn('tg?.platform === "ios"', self.js)
        self.assertIn("IOS_TELEGRAM_MIN_TOP_SAFE_AREA_PX", self.js)

    def test_7_fallback_not_applied_android_desktop(self):
        # the platform check must gate the floor — not be applied unconditionally.
        idx = self.js.find("function _applySafeArea()")
        self.assertNotEqual(idx, -1)
        body = self.js[idx:idx + 1400]
        m = re.search(r'if \(tg\?\.platform === "ios"\) \{\s*px = Math\.max\(px, IOS_TELEGRAM_MIN_TOP_SAFE_AREA_PX\);\s*\}', body)
        self.assertIsNotNone(m, "iOS floor must be inside an if(platform===ios) guard, not applied unconditionally")

    def test_8_uses_max_not_sum(self):
        self.assertIn("Math.max(px, IOS_TELEGRAM_MIN_TOP_SAFE_AREA_PX)", self.js)
        self.assertNotIn("px + IOS_TELEGRAM_MIN_TOP_SAFE_AREA_PX", self.js)
        self.assertNotIn("px += IOS_TELEGRAM_MIN_TOP_SAFE_AREA_PX", self.js)

    def test_9_no_double_top_padding(self):
        # .app-shell itself must no longer carry a Telegram-conditional,
        # --app-top-safe-offset-driven padding-top — only the spacer does.
        self.assertNotIn("body.is-telegram-webapp .app-shell {\n  padding-top: var(--app-top-safe-offset)", self.css)
        self.assertIn("body.is-telegram-webapp .app-shell {\n  padding-top: 14px !important;\n}", self.css)
        # exactly one rule drives the spacer's height from the variable.
        self.assertEqual(self.css.count("height: var(--app-top-safe-offset);"), 1)

    def test_10_bottom_navigation_not_offset(self):
        idx = self.css.find(".tabs.bottom-tabbar {")
        self.assertNotEqual(idx, -1)
        block = self.css[idx:idx + 400]
        self.assertNotIn("--app-top-safe-offset", block)
        self.assertNotIn("app-top-safe-spacer", block)

    def test_11_confirmation_sheet_shares_the_same_model(self):
        idx = self.css.find(".comms-confirm-sheet {")
        self.assertNotEqual(idx, -1)
        block = self.css[idx:idx + 400]
        self.assertIn("var(--app-top-safe-offset", block)


class TestBranchSelectionVisual(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_12_branch_option_gets_selected_class(self):
        self.assertIn('b.classList.toggle("active", b.dataset.branch === _ocAvailState.branch);', self.js)

    def test_13_selected_branch_has_explicit_contrast_css_important(self):
        idx = self.css.find(".ws-oc-ttl-btn.active {")
        self.assertNotEqual(idx, -1)
        line = self.css[idx:self.css.find("\n", idx)]
        self.assertIn("!important", line)
        self.assertIn("background: var(--yc-yellow, #FFCB1F) !important", line)
        self.assertIn("color: #6f5200 !important", line)

    def test_14_either_branch_uses_same_mechanism(self):
        # "Любой" (either) is just another .ws-oc-ttl-btn — no special-cased
        # exclusion from the (fixed) active-state rule.
        self.assertIn('data-branch="either"', INDEX_HTML.read_text(encoding="utf-8"))
        self.assertNotIn('data-branch="either"] { ', self.css)  # no bespoke override defeating the fix

    def test_15_selected_state_restored_after_render(self):
        idx = self.js.find("async function _availScreenOpenFor(mkUserId)")
        self.assertNotEqual(idx, -1)
        body = self.js[idx:idx + 1600]
        self.assertIn("_ocAvailState.branch = data.preferred_branch", body)
        self.assertIn('_ocAvailRenderBranchButtons("#availBranchRow")', body)

    def test_16_backend_availability_endpoint_unchanged(self):
        src = WEB_APP_SERVER_PY.read_text(encoding="utf-8")
        self.assertIn('"preferred_branch": "unknown", "available_from": None, "schedule_comment": ""', src)


class TestClientManagerBackButton(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_17_client_manager_does_not_see_back_to_admin(self):
        # The gate is realRole-based, not canUseAdmin() (proven true for
        # client_manager whenever food-lunch is enabled — see module docstring).
        self.assertIn(
            'function canReturnToAdminFromComms() {\n  const realRole = state.me?.realRole || "";\n'
            '  return realRole === "owner" || realRole === "admin";\n}',
            self.js,
        )
        # Anchored on the subtitle text, unique to the real comms-home
        # render (renderCommsDisabled's header has no subtitle/back button).
        idx = self.js.find("Отправка настоящих уведомлений в личный кабинет")
        self.assertNotEqual(idx, -1)
        segment = self.js[idx:idx + 300]
        self.assertIn("canReturnToAdminFromComms() ?", segment)
        self.assertNotIn("canUseAdmin() ?", segment)

    def test_18_owner_admin_still_see_back_to_admin(self):
        # Same helper covers both surfaces; owner/admin pass realRole checks.
        idx = self.js.find('if (name === "comms") {')
        self.assertNotEqual(idx, -1)
        segment = self.js[idx:idx + 600]
        self.assertIn("canReturnToAdminFromComms() ? _commsExitToAdmin : null", segment)

    def test_19_real_role_used_not_test_role(self):
        fn_idx = self.js.find("function canReturnToAdminFromComms()")
        self.assertNotEqual(fn_idx, -1)
        body = self.js[fn_idx:fn_idx + 200]
        self.assertIn("state.me?.realRole", body)
        self.assertNotIn("state.me?.role", body)


class TestVersionAndOverflow(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")
        self.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_20_version_cache_bust_v71143(self):
        self.assertIn("styles.css?v=7.1.16", self.html)
        self.assertIn("app.js?v=7.1.16", self.html)
        self.assertIn('console.log("MiniApp version: v7.1.16");', self.js)

    def test_21_no_fixed_pixel_width_in_new_css(self):
        for block_start in (".app-top-safe-spacer {", ".ws-oc-ttl-btn.active {"):
            idx = self.css.find(block_start)
            self.assertNotEqual(idx, -1)
            block = self.css[idx:idx + 300]
            self.assertNotRegex(block, r"(?<!-)width:\s*\d+px")


if __name__ == "__main__":
    unittest.main()
