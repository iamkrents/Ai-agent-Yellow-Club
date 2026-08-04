"""Tests for v7.1.16 — Client & Client Manager UX Stabilization: mobile
shell / safe-area regression checks.

Covers (MOBILE 25-32 from the launch-readiness spec):
  25. Safe-area model (top spacer + --app-top-safe-offset) is preserved.
  26. #appTopSafeSpacer is still the literal first child of .app-shell.
  27. No double top offset: .app-shell's own padding-top rule is unchanged
      (the spacer is the only element that reserves Telegram chrome space).
  28. Bottom nav still respects env(safe-area-inset-bottom) and .app-shell
      still reserves room above it — unmodified by this release.
  29. New/touched submit controls stay in normal document flow (no new
      fixed/sticky footer that a keyboard could permanently cover).
  30. New modal (uiConfirmSheet) reuses .pi-modal-sheet, which already caps
      itself to the viewport height and scrolls its own content.
  31. New CSS added in this release uses flex/grid, not fixed pixel widths
      (can't force horizontal overflow on a narrow viewport).
  32. New primary interactive controls added in this release meet the
      ~44px minimum tap target.

Real-browser confirmation (390x844 / 375x812 / 360x800, zero horizontal
overflow) was additionally run via Playwright — see the final report.

Run:
    python -m unittest tests.test_mobile_shell_regression_v7116 -v
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest  # noqa: E402

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
STYLES_CSS = (ROOT / "miniapp" / "styles.css").read_text(encoding="utf-8")


class TestSafeAreaPreserved(unittest.TestCase):
    def test_25_safe_area_spacer_and_offset_model_intact(self):
        self.assertIn('id="appTopSafeSpacer"', INDEX_HTML)
        self.assertIn(
            "body.is-telegram-webapp .app-top-safe-spacer {\n  height: var(--app-top-safe-offset);\n}",
            STYLES_CSS,
        )
        self.assertIn("IOS_TELEGRAM_MIN_TOP_SAFE_AREA_PX", APP_JS)

    def test_26_spacer_is_first_child_of_app_shell(self):
        # allow an HTML comment between the opening tag and the spacer, but
        # no other element (no sibling div/header/etc. before it)
        m = re.search(r'<div class="app-shell">\s*(?:<!--.*?-->\s*)?<div id="appTopSafeSpacer"', INDEX_HTML, re.S)
        self.assertIsNotNone(m, "appTopSafeSpacer must remain the literal first child of .app-shell")

    def test_27_no_second_top_offset_introduced(self):
        m = re.search(r"\.app-shell \{([^}]*)\}", STYLES_CSS)
        self.assertIsNotNone(m)
        # exactly one env(safe-area-inset-top) reference inside .app-shell's
        # own rule (the pre-existing padding-top calc()) — a second one
        # would mean a competing top-offset was added.
        self.assertEqual(m.group(1).count("safe-area-inset-top"), 1)


class TestBottomNavUnaffected(unittest.TestCase):
    def test_28_bottom_nav_respects_safe_area_and_shell_reserves_space(self):
        self.assertIn(".tabs.bottom-tabbar {", STYLES_CSS)
        idx = STYLES_CSS.find(".tabs.bottom-tabbar {")
        block = STYLES_CSS[idx:idx + 300]
        self.assertIn("env(safe-area-inset-bottom)", block)
        m = re.search(r"\.app-shell \{([^}]*)\}", STYLES_CSS)
        self.assertIn("env(safe-area-inset-bottom)", m.group(1))


class TestKeyboardDoesNotHideSubmit(unittest.TestCase):
    def test_29_availability_footer_stays_in_normal_flow(self):
        # No dedicated #availScreenFooter CSS rule exists — it is never
        # given position:fixed/sticky, so it can't end up pinned under an
        # open keyboard; it simply scrolls into view with the rest of the
        # form like every other cabinet subpage.
        self.assertNotIn("#availScreenFooter", STYLES_CSS)


class TestModalFitsViewport(unittest.TestCase):
    def test_30_confirm_sheet_reuses_capped_modal_sheet(self):
        self.assertIn('<div id="uiConfirmModal" class="pi-modal hidden">', INDEX_HTML)
        self.assertIn('<div class="pi-modal-sheet pi-modal-sheet-sm">', INDEX_HTML)
        m = re.search(r"\.pi-modal-sheet \{([^}]*)\}", STYLES_CSS)
        self.assertIsNotNone(m)
        self.assertIn("max-height: calc(100dvh", m.group(1))
        self.assertIn("overflow: hidden", m.group(1))


class TestNoFixedWidthsInNewCss(unittest.TestCase):
    def test_31_new_v7116_blocks_have_no_fixed_pixel_widths(self):
        m = re.search(r"/\* ── v7\.1\.16 shared patterns.*?(?=\n/\* )", STYLES_CSS, re.S)
        self.assertIsNotNone(m, "v7.1.16 shared-patterns CSS block not found")
        block = m.group(0)
        self.assertNotRegex(block, r"width:\s*\d+px")


class TestTapTargets(unittest.TestCase):
    def test_32_new_primary_buttons_meet_44px(self):
        self.assertIn(".form-actions-bar button { flex: 1; min-height: 44px; }", STYLES_CSS)
        self.assertIn("#uiConfirmFooter button { min-height: 44px; }", STYLES_CSS)


if __name__ == "__main__":
    unittest.main()
