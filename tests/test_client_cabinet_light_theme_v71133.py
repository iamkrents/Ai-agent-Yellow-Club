"""Tests for v7.1.13.3 — force the client cabinet to stay light regardless
of Telegram/system dark theme (production hotfix; theme/contrast only, no
business-logic or structural changes).

Root cause covered: .cp-* (client payment card) rules had their own
dedicated @media (prefers-color-scheme: dark) / :root[data-theme="dark"]
variants dating back to v7.0.93.2.5 / v7.0.93.3.0 — added when the payments
page was standalone and expected to track device theme, before the client
cabinet existed. Every other cabinet screen never got a dark variant, so
only "Оплаты" went dark on a dark-themed device. v7.1.13.2's
`color-scheme: light only` only affects native UA form-control chrome, not
these literal author colors, so it did not fix this.

Run:
    python -m unittest tests.test_client_cabinet_light_theme_v71133 -v
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


class TestClientCabinetForcedLight(unittest.TestCase):
    def setUp(self):
        self.css = STYLES_CSS.read_text(encoding="utf-8")
        self.js = APP_JS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")

    def _override_block(self):
        idx = self.css.find("body.role-parent-cabinet { color-scheme: light; }")
        self.assertNotEqual(idx, -1, "role-parent-cabinet light override block missing")
        return self.css[idx:idx + 3500]

    def test_1_role_parent_cabinet_color_scheme_light(self):
        block = self._override_block()
        self.assertIn("body.role-parent-cabinet { color-scheme: light; }", block)

    def test_2_no_reliance_on_telegram_theme_vars(self):
        # The fix must not introduce any new dependency on --tg-theme-* as
        # the final source of cabinet background/text color.
        self.assertNotIn("--tg-theme-", self.css)
        self.assertNotIn("--tg-theme-", self.js)

    def test_3_dark_media_query_cannot_darken_payment_cards(self):
        block = self._override_block()
        self.assertIn('body.role-parent-cabinet .cp-card { background: #fff !important', block)
        self.assertIn("border-color: #ece9dd !important", block)

    def test_4_payment_card_background_light(self):
        block = self._override_block()
        idx = block.find(".cp-card {")
        self.assertNotEqual(idx, -1)
        segment = block[idx:idx + 200]
        self.assertIn("#fff", segment)
        self.assertIn("!important", segment)

    def test_5_erip_and_card_payment_blocks_light(self):
        block = self._override_block()
        for selector in [".cp-erip-block", ".cp-erip-details", ".cp-erip-value",
                          ".cp-erip-label", ".cp-erip-steps li", ".cp-erip-hint-label",
                          ".cp-erip-path", ".cp-pay-method"]:
            self.assertIn(selector, block, f"missing light override for {selector}")

    def test_6_primary_and_secondary_text_have_explicit_light_colors(self):
        block = self._override_block()
        self.assertIn("#172033 !important", block)  # primary ink
        self.assertIn("#657089 !important", block)  # secondary/muted

    def test_7_status_badges_remain_distinguishable(self):
        block = self._override_block()
        self.assertIn(".cp-status-pending { background: rgba(255,216,77,.30) !important; color: #6b4e00 !important; }", block)
        self.assertIn(".cp-status-paid { background: rgba(31,165,107,.13) !important; color: #116643 !important; }", block)

    def test_7b_due_badge_now_styled_and_distinguishable(self):
        # Previously .cp-due-badge had zero CSS anywhere — nearly invisible
        # on a dark card. Must now have explicit, distinct colors per state.
        self.assertIn(".cp-due-badge {", self.css)
        overdue_idx = self.css.find(".cp-due-overdue {")
        today_idx = self.css.find(".cp-due-today {")
        upcoming_idx = self.css.find(".cp-due-upcoming {")
        self.assertNotEqual(overdue_idx, -1)
        self.assertNotEqual(today_idx, -1)
        self.assertNotEqual(upcoming_idx, -1)
        overdue_color = self.css[overdue_idx:overdue_idx + 120]
        today_color = self.css[today_idx:today_idx + 120]
        upcoming_color = self.css[upcoming_idx:upcoming_idx + 120]
        colors = {overdue_color, today_color, upcoming_color}
        self.assertEqual(len(colors), 3, "due-badge states must use distinct styling")

    def test_8_disabled_owner_test_cta_stays_readable(self):
        # .cp-card-pay-btn{opacity:1} (unconditional) loses specificity to
        # button:disabled{opacity:.5} once actually disabled — must have a
        # dedicated :disabled override that wins.
        self.assertIn(".cp-card-pay-btn:disabled { opacity: 1", self.css)

    def test_9_bottom_navigation_untouched_and_light(self):
        # Bottom nav for the cabinet was never dark-themed in the first
        # place (no .tabs.bottom-tabbar entry in any dark block) — assert
        # that remains true, i.e. this hotfix didn't need to (and doesn't)
        # touch it.
        for m in re.finditer(r":root\[data-theme=\"dark\"\][^\{]*\{[^}]*\}", self.css):
            self.assertNotIn("bottom-tabbar", m.group(0))

    def test_10_staff_admin_css_not_overridden_by_client_rules(self):
        # The new override block must be scoped under body.role-parent-cabinet
        # everywhere — never a bare selector that could leak into staff/admin
        # views which never carry that body class.
        idx = self.css.find("body.role-parent-cabinet { color-scheme: light; }")
        end_idx = self.css.find("/* v7.0.94.0 — hide empty notice strip */")
        self.assertNotEqual(idx, -1)
        self.assertNotEqual(end_idx, -1)
        block = self.css[idx:end_idx]
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("/*") or stripped.startswith("*") or stripped.startswith("*/"):
                continue
            if "{" not in stripped:
                continue
            self.assertTrue(
                stripped.startswith("body.role-parent-cabinet") or stripped.startswith(".cp-due")
                or stripped.startswith(".cp-card-pay-btn:disabled"),
                f"unscoped rule could leak outside client cabinet: {stripped}",
            )

    def test_11_version_and_cache_bust(self):
        self.assertIn('console.log("MiniApp version: v7.1.15")', self.js)
        self.assertIn('styles.css?v=7.1.15', self.html)
        self.assertIn('app.js?v=7.1.15', self.html)

    def test_12_no_stray_horizontal_scroll_selectors_introduced(self):
        block = self._override_block()
        self.assertNotIn("width: 100vw", block)
        self.assertNotIn("min-width: 100%", block)


class TestStaffAdminUnaffected(unittest.TestCase):
    def setUp(self):
        self.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_13_staff_tabs_still_present(self):
        for tab in ["lessons", "reports", "schedule", "tasks", "admin", "payments-workspace"]:
            self.assertIn(f'data-tab="{tab}"', self.html)


if __name__ == "__main__":
    unittest.main()
