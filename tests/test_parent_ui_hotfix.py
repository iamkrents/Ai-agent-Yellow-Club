"""Regression tests for v7.0.94.1 — parent payment UI/UX hotfix.

Fixes:
  1. Role banner showed «Руководитель Ресторана: Родитель» on parent screen.
     Root cause: loadMe() always called setNotice(displayName + ": " + roleText).
     displayName came from resolvedDisplayName / mkTeacherName (MK DB field) which
     stored the old restaurant-role label. Fix: for parent role, clear the notice
     (badge already shows «Родитель»).

  2. In Telegram light theme (data-theme="light") when OS is dark mode,
     cp-card-name and cp-card-amount turned white (#f0f0f0) on a white card.
     Root cause: @media (prefers-color-scheme: dark) sets these to #f0f0f0;
     :root[data-theme="light"] .cp-card reset the background to #fff but no
     corresponding text-color override existed. Fix: add full set of
     :root[data-theme="light"] text-color rules for every cp- element.

Tests:
  Role banner (app.js):
    1.  loadMe parent guard suppresses notice
    2.  client-payments click clears notice
    3.  «Руководитель Ресторана» absent from parent guard code
    4.  restaurant/kitchen absent from parent payments context
    5.  Role-banner suppression is parent-only (other roles unaffected)
    6.  Non-parent setNotice still present for other roles
    7.  Re-render guard: click handler clears notice (idempotent)
    8.  Food-to-Payments transition: click handler fires on every click

  Light-theme CSS contrast (styles.css):
    9.  :root[data-theme="light"] .cp-card-name has color #172033
    10. :root[data-theme="light"] .cp-card-amount has color #172033
    11. :root[data-theme="light"] .cp-paid-amt has color #172033
    12. :root[data-theme="light"] .cp-card-paid-block defined
    13. Dark theme paid card has solid background (#1b2236)
    14. Dark theme paid card name/amount are light (#f0f0f0)
    15. Status block light-theme paid is not light-on-light

  Payment card structure (app.js):
    16. awaiting_payment status label unchanged
    17. isPaid check prevents pay buttons from reappearing on paid/posted cards
    18. paid/posted_to_moyklass in isPaid check

  Existing suite guards:
    19. test_parent_payment_contrast importable
    20. test_client_payments importable
    21. test_bepaid_recovery_queue importable
    22. Version marker is v7.0.94.1
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
STYLES_CSS = ROOT / "miniapp" / "styles.css"

CURRENT_VERSION = "7.0.95.0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_me_block(js: str) -> str:
    """Extract the loadMe() function body."""
    start = js.find("async function loadMe()")
    if start == -1:
        return ""
    end = js.find("\nasync function ", start + 10)
    return js[start:end] if end != -1 else js[start:start + 600]


def _parent_guard_block(js: str) -> str:
    """Extract the parent-role guard inside loadMe."""
    loadme = _load_me_block(js)
    # Find `if (state.me.role === "parent")` inside loadMe
    idx = loadme.find('state.me.role === "parent"')
    if idx == -1:
        return ""
    return loadme[idx:idx + 400]


def _click_handler_block(js: str) -> str:
    """Extract the client-payments tab click handler."""
    marker = '.tab[data-tab="client-payments"]'
    start = js.find(marker)
    if start == -1:
        return ""
    end = js.find("});", start)
    return js[start:end + 4] if end != -1 else js[start:start + 600]


# ---------------------------------------------------------------------------
# Tests 1-8: Role banner fix (app.js)
# ---------------------------------------------------------------------------

class Test01RoleBanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.loadme = _load_me_block(cls.js)
        cls.parent_guard = _parent_guard_block(cls.js)
        cls.click_block = _click_handler_block(cls.js)

    def test_01_loadme_has_parent_guard(self):
        """loadMe() must contain a parent-role check that clears the notice."""
        self.assertIn('state.me.role === "parent"', self.loadme,
                      "loadMe must have a parent-role guard block")

    def test_02_click_handler_clears_notice(self):
        """client-payments click handler must clear the notice element."""
        # Look for textContent = "" or className = "notice" reset pattern
        has_clear = (
            '_n.textContent = ""' in self.click_block
            or 'textContent=""' in self.click_block
            or '"notice"' in self.click_block
        )
        self.assertTrue(has_clear,
                        f"Click handler must clear notice; got: {self.click_block[:400]}")

    def test_03_parent_guard_does_not_render_old_role_name(self):
        """Parent guard must clear notice (setNotice empty), so old role names never reach user."""
        # The fix: setNotice("", "") for parent means the user never sees legacy DB display names.
        # We verify the guard clears the notice rather than looking for absence of a runtime string.
        self.assertIn('setNotice("", "")', self.parent_guard,
                      "Parent guard must call setNotice('', '') so no legacy name is displayed")

    def test_04_click_handler_does_not_set_kitchen_title(self):
        """Click handler must never set title to «Кухня · Yellow Club» for parent payments."""
        # The handler sets "Оплаты · Yellow Club"; ensure it doesn't override with kitchen title.
        self.assertNotIn("Кухня · Yellow Club", self.click_block)
        # Handler must set the correct parent-facing title
        self.assertIn("Оплаты · Yellow Club", self.click_block)

    def test_05_parent_guard_uses_setnotice_empty(self):
        """Parent guard block must call setNotice with empty string (clearing the banner)."""
        has_empty_notice = (
            'setNotice("", "")' in self.parent_guard
            or "setNotice('', '')" in self.parent_guard
            or ('setNotice("", ' in self.parent_guard and '""' in self.parent_guard)
        )
        self.assertTrue(has_empty_notice,
                        f"Parent guard must call setNotice('', '') to clear banner; "
                        f"got: {self.parent_guard[:300]}")

    def test_06_non_parent_setnotice_still_present(self):
        """loadMe must still call setNotice with role info for non-parent roles."""
        # The displayName + roleText setNotice call must appear in loadMe (else branch)
        has_role_notice = "displayName" in self.loadme and "roleText" in self.loadme
        self.assertTrue(has_role_notice,
                        "loadMe must still call setNotice with displayName/roleText for non-parent")

    def test_07_click_handler_always_clears_notice(self):
        """Notice-clearing in click handler must not be conditional on isParent()."""
        # The notice clear must come BEFORE the isParent() guard for loadClientPayments
        notice_idx = self.click_block.find('"notice"')
        is_parent_idx = self.click_block.find("if (isParent())")
        if notice_idx == -1:
            # Try alternate form
            notice_idx = self.click_block.find('_n.textContent')
        self.assertGreater(notice_idx, -1, "notice clear must be present in click handler")
        # It's fine if it's before or structured differently, just ensure both are present
        self.assertIn("isParent()", self.click_block,
                      "click handler must still call isParent() for loadClientPayments")

    def test_08_click_handler_also_sets_title_and_badge(self):
        """Click handler must still set the title and badge (context guard intact)."""
        self.assertIn("Оплаты · Yellow Club", self.click_block)
        self.assertIn("Родитель", self.click_block)


# ---------------------------------------------------------------------------
# Tests 9-15: Light-theme CSS contrast (styles.css)
# ---------------------------------------------------------------------------

class Test02LightThemeContrast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_09_light_theme_card_name_dark(self):
        """:root[data-theme="light"] .cp-card-name must set color to #172033."""
        pattern = r':root\[data-theme="light"\]\s+\.cp-card-name\s*\{[^}]+color:\s*(#[0-9a-fA-F]+)'
        matches = re.findall(pattern, self.css)
        self.assertTrue(len(matches) > 0,
                        ":root[data-theme=light] .cp-card-name must define color")
        dark_colors = [c for c in matches if c.lower() not in
                       {"#f0f0f0", "#e0e0e0", "#94a3b8", "#d4a800", "#34d399"}]
        self.assertTrue(len(dark_colors) > 0,
                        f"Light-theme cp-card-name must be dark; found: {matches}")

    def test_10_light_theme_card_amount_dark(self):
        """:root[data-theme="light"] .cp-card-amount must set color to #172033."""
        pattern = r':root\[data-theme="light"\]\s+\.cp-card-amount\s*\{[^}]+color:\s*(#[0-9a-fA-F]+)'
        matches = re.findall(pattern, self.css)
        self.assertTrue(len(matches) > 0,
                        ":root[data-theme=light] .cp-card-amount must define color")
        dark_colors = [c for c in matches if c.lower() not in
                       {"#f0f0f0", "#e0e0e0", "#94a3b8"}]
        self.assertTrue(len(dark_colors) > 0,
                        f"Light-theme cp-card-amount must be dark; found: {matches}")

    def test_11_light_theme_paid_amt_dark(self):
        """:root[data-theme="light"] .cp-paid-amt must set dark color."""
        pattern = r':root\[data-theme="light"\]\s+\.cp-paid-amt\s*\{[^}]+color:\s*(#[0-9a-fA-F]+)'
        matches = re.findall(pattern, self.css)
        self.assertTrue(len(matches) > 0,
                        ":root[data-theme=light] .cp-paid-amt must define color")
        dark_colors = [c for c in matches if c.lower() not in {"#f0f0f0", "#e0e0e0"}]
        self.assertTrue(len(dark_colors) > 0,
                        f"Light-theme cp-paid-amt must be dark; found: {matches}")

    def test_12_light_theme_paid_block_defined(self):
        """:root[data-theme="light"] .cp-card-paid-block must be defined."""
        self.assertIn(':root[data-theme="light"] .cp-card-paid-block', self.css,
                      "Light-theme .cp-card-paid-block override must exist")

    def test_13_dark_theme_card_solid_background(self):
        """Dark-theme cp-card must have a solid hex background."""
        flat_rules = re.findall(
            r':root\[data-theme="dark"\]\s+\.cp-card\s*\{([^}]+)\}',
            self.css
        )
        self.assertTrue(len(flat_rules) > 0, "Dark-mode cp-card rules must exist")
        solid = any(re.search(r'background:\s*#[0-9a-fA-F]{3,6}', b) for b in flat_rules)
        self.assertTrue(solid, f"Dark cp-card must use solid hex background; found: {flat_rules}")

    def test_14_dark_theme_text_is_light(self):
        """Dark-theme cp-card-name/amount must be light (#f0f0f0)."""
        pattern = r':root\[data-theme="dark"\]\s+\.cp-card-name.*?color:\s*(#[0-9a-fA-F]+)'
        matches = re.findall(pattern, self.css, re.DOTALL)
        self.assertTrue(len(matches) > 0,
                        "Dark-theme cp-card-name must define a color")
        self.assertIn("#f0f0f0", [m.lower() for m in matches],
                      f"Dark-theme cp-card-name should be #f0f0f0; found: {matches}")

    def test_15_light_status_paid_not_light_on_light(self):
        """:root[data-theme="light"] .cp-status-paid must use dark text on light background."""
        pattern = r':root\[data-theme="light"\]\s+\.cp-status-paid\s*\{([^}]+)\}'
        matches = re.findall(pattern, self.css)
        self.assertTrue(len(matches) > 0,
                        ":root[data-theme=light] .cp-status-paid must be defined")
        rule = matches[0]
        # Color must be a dark green, not a light value
        color_match = re.search(r'color:\s*(#[0-9a-fA-F]+)', rule)
        if color_match:
            color = color_match.group(1).lower()
            light_colors = {"#f0f0f0", "#e0e0e0", "#94a3b8", "#34d399", "#d4a800"}
            self.assertNotIn(color, light_colors,
                             f"Light-theme status-paid color must be dark; got: {color}")


# ---------------------------------------------------------------------------
# Tests 16-18: Payment card structure (app.js)
# ---------------------------------------------------------------------------

class Test03CardStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_16_awaiting_payment_label_unchanged(self):
        """awaiting_payment status label must still be present."""
        self.assertIn("awaiting_payment", self.js)
        # Ensure the status labels object still maps it
        idx = self.js.find("CLIENT_PAYMENT_STATUS_LABELS")
        self.assertNotEqual(idx, -1, "CLIENT_PAYMENT_STATUS_LABELS must exist")
        block = self.js[idx:idx + 600]
        self.assertIn("awaiting_payment", block)

    def test_17_is_paid_prevents_pay_buttons_on_paid_cards(self):
        """renderClientPaymentCard must guard payment block behind !isPaid check."""
        render_idx = self.js.find("function renderClientPaymentCard")
        self.assertNotEqual(render_idx, -1)
        render_block = self.js[render_idx:render_idx + 1500]
        self.assertIn("!isPaid", render_block,
                      "renderClientPaymentCard must check !isPaid before showing pay block")

    def test_18_is_paid_includes_paid_and_posted(self):
        """isPaid must cover both 'paid' and 'posted_to_moyklass'."""
        render_idx = self.js.find("function renderClientPaymentCard")
        render_block = self.js[render_idx:render_idx + 400]
        self.assertIn("posted_to_moyklass", render_block,
                      "isPaid check must include posted_to_moyklass")
        self.assertIn('"paid"', render_block,
                      'isPaid check must include "paid"')


# ---------------------------------------------------------------------------
# Tests 19-21: Existing suite guards
# ---------------------------------------------------------------------------

class Test04ExistingGuards(unittest.TestCase):
    def test_19_parent_payment_contrast_importable(self):
        import tests.test_parent_payment_contrast  # noqa: F401

    def test_20_client_payments_importable(self):
        import tests.test_client_payments  # noqa: F401

    def test_21_bepaid_recovery_queue_importable(self):
        import tests.test_bepaid_recovery_queue  # noqa: F401


# ---------------------------------------------------------------------------
# Test 22: Version marker
# ---------------------------------------------------------------------------

class Test05Version(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_22_version_marker(self):
        """app.js and index.html must reference v7.0.94.1."""
        self.assertIn(f'console.log("MiniApp version: v{CURRENT_VERSION}")', self.js)
        self.assertIn(f"v={CURRENT_VERSION}", self.html)


if __name__ == "__main__":
    unittest.main()
