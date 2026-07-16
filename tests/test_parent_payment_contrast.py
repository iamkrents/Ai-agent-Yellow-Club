"""Regression tests for v7.0.93.2.8 — parent payment contrast & header hotfix.

Root causes fixed:
  1. appTitle showed "Питание · Yellow Club" for parent role — now "Оплаты · Yellow Club".
  2. In Telegram dark mode, cp-card had background: rgba(255,255,255,.06) — nearly
     transparent — while the page body stays light (#f7f8fb). Text was #f0f0f0
     (light) → invisible on a cream background. Fixed: solid dark card background.
  3. Global `details > summary:hover { opacity: 0.75 }` faded the ERIP accordion
     summary. Fixed: .cp-erip-details > summary:hover { opacity: 1 }.
  4. No explicit opacity:1 guard on active cp-buttons against global button:disabled
     cascade or Telegram theme injection. Fixed.
  5. Tab click handler for client-payments now always forces parent-facing header,
     regardless of legacy DB role (restaurant/kitchen/food).

Tests:
  Frontend header (app.js):
    1.  Parent role block sets "Оплаты · Yellow Club"
    2.  "Питание · Yellow Club" NOT in parent role block
    3.  "Руководитель Ресторана" NOT in parent or payments header context
    4.  Restaurant/kitchen legacy strings absent from parent header code
    5.  Client-payments tab click sets "Оплаты · Yellow Club"
    6.  Client-payments tab click sets "Родитель" badge
    7.  Context guard comment present

  CSS contrast (styles.css):
    8.  cp-card-name has a dark color rule
    9.  cp-erip-value has a dark color rule
    10. cp-erip-steps li opacity is 1
    11. cp-copy-btn:disabled has opacity: 1
    12. cp-card-pay-btn background is yellow (#ffd84d)
    13. Nunito referenced in cp- button styles

  Dark mode fix (styles.css):
    14. data-theme=dark cp-card uses solid hex background
    15. cp-erip-details > summary:hover overrides global opacity to 1

  Header string guard (app.js):
    16. "Оплаты · Yellow Club" appears at least twice
    17. "Питание · Yellow Club" does NOT appear in parent/payments context

  Existing suite guards:
    18. test_parent_payments importable
    19. test_client_payments importable
    20. test_client_parent_links importable
    21. test_payment_restyle importable
    22. Version marker is v7.0.93.2.8
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
STYLES_CSS = ROOT / "miniapp" / "styles.css"

CURRENT_VERSION = "7.0.93.2.8"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parent_role_block(js: str) -> str:
    """Extract the `if (role === 'parent') { ... }` block from setupRoleUi.

    setupRoleUi contains the canonical block that sets appTitle and roleBadge for
    the parent role. We anchor to the parentAllowed array which is unique to that block.
    """
    # The setupRoleUi parent block contains 'parentAllowed' — use that as anchor
    anchor = "parentAllowed"
    anchor_idx = js.find(anchor)
    if anchor_idx == -1:
        return ""
    # Walk back to find the enclosing `if (role === "parent")`
    start = js.rfind('if (role === "parent")', 0, anchor_idx)
    if start == -1:
        return ""
    end = js.find("\n  }\n", start)
    return js[start: end + 6] if end != -1 else js[start: start + 600]


def _client_payments_click_block(js: str) -> str:
    """Extract the client-payments tab click handler block."""
    marker = '.tab[data-tab="client-payments"]'
    start = js.find(marker)
    if start == -1:
        return ""
    end = js.find("});", start)
    return js[start: end + 4] if end != -1 else js[start: start + 500]


# ---------------------------------------------------------------------------
# Tests 1-7: Frontend header
# ---------------------------------------------------------------------------

class Test01Header(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.parent_block = _parent_role_block(cls.js)
        cls.click_block = _client_payments_click_block(cls.js)

    def test_01_parent_role_sets_oplaty_title(self):
        """Parent role block must set appTitle to Оплаты · Yellow Club."""
        self.assertIn("Оплаты · Yellow Club", self.parent_block)

    def test_02_pitanie_not_in_parent_role_block(self):
        """Parent role block must NOT reference Питание · Yellow Club."""
        self.assertNotIn("Питание · Yellow Club", self.parent_block)

    def test_03_rukovoditel_not_in_parent_or_payments_header(self):
        """Руководитель Ресторана must not appear in parent header or payments click handler."""
        self.assertNotIn("Руководитель Ресторана", self.parent_block)
        self.assertNotIn("Руководитель Ресторана", self.click_block)

    def test_04_restaurant_kitchen_not_in_parent_payments_header(self):
        """Kitchen/restaurant role strings must not appear in parent payments header context."""
        combined = self.parent_block + "\n" + self.click_block
        self.assertNotIn("Кухня · Yellow Club", combined)

    def test_05_click_handler_sets_oplaty_title(self):
        """client-payments tab click handler must force appTitle to Оплаты · Yellow Club."""
        self.assertIn("Оплаты · Yellow Club", self.click_block)

    def test_06_click_handler_sets_roditel_badge(self):
        """client-payments tab click handler must force roleBadge to Родитель."""
        self.assertIn("Родитель", self.click_block)

    def test_07_context_guard_comment_in_click_handler(self):
        """Click handler must have a context guard comment."""
        guard_phrases = ["Context guard", "context guard", "legacy", "legacy DB"]
        self.assertTrue(
            any(p in self.click_block for p in guard_phrases),
            f"Click handler should have a context guard comment; got: {self.click_block[:300]}"
        )


# ---------------------------------------------------------------------------
# Tests 8-13: CSS contrast (light mode)
# ---------------------------------------------------------------------------

class Test02CSSContrast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_08_cp_card_name_has_dark_color(self):
        """cp-card-name must have at least one dark color rule (not light-mode invisible)."""
        matches = re.findall(r'\.cp-card-name\s*\{[^}]+color:\s*(#[0-9a-fA-F]{3,6})', self.css)
        self.assertTrue(len(matches) > 0, "cp-card-name must define a color")
        # At least one color must be dark (not a light gray or white variant)
        light_values = {"#f0f0f0", "#e0e0e0", "#94a3b8", "#d4a800", "#34d399"}
        dark_matches = [c.lower() for c in matches if c.lower() not in light_values]
        self.assertTrue(
            len(dark_matches) > 0,
            f"cp-card-name must have a dark color rule; found: {matches}"
        )

    def test_09_cp_erip_value_has_dark_color(self):
        """cp-erip-value must have at least one dark color rule."""
        matches = re.findall(r'\.cp-erip-value\s*\{[^}]+color:\s*(#[0-9a-fA-F]{3,6})', self.css)
        self.assertTrue(len(matches) > 0, "cp-erip-value must define a color")
        light_values = {"#f0f0f0", "#e0e0e0", "#94a3b8"}
        dark_matches = [c.lower() for c in matches if c.lower() not in light_values]
        self.assertTrue(
            len(dark_matches) > 0,
            f"cp-erip-value must have a dark color rule; found: {matches}"
        )

    def test_10_cp_erip_steps_li_opacity_is_1(self):
        """cp-erip-steps li must have opacity: 1 (not partially transparent)."""
        matches = re.findall(
            r'\.cp-erip-steps\s+li\s*\{[^}]+opacity:\s*([0-9.]+)', self.css
        )
        for val in matches:
            self.assertAlmostEqual(
                float(val), 1.0, delta=0.0,
                msg=f"cp-erip-steps li opacity must be 1, got: {val}"
            )

    def test_11_cp_copy_btn_disabled_opacity_is_1(self):
        """cp-copy-btn:disabled must set opacity: 1 (overrides global button:disabled 0.5)."""
        pattern = r'\.cp-copy-btn:disabled\s*\{[^}]+\}'
        matches = re.findall(pattern, self.css)
        self.assertTrue(len(matches) > 0, "cp-copy-btn:disabled must be defined")
        any_opacity_1 = any(
            re.search(r'opacity:\s*1\b', m) for m in matches
        )
        self.assertTrue(
            any_opacity_1,
            f"cp-copy-btn:disabled must set opacity: 1; found rules: {matches}"
        )

    def test_12_cp_card_pay_btn_is_yellow(self):
        """cp-card-pay-btn must use yellow background (#ffd84d)."""
        idx = self.css.find(".cp-card-pay-btn")
        self.assertNotEqual(idx, -1, "cp-card-pay-btn must be defined")
        rule_block = self.css[idx: self.css.find("}", idx) + 1]
        self.assertIn("#ffd84d", rule_block, "cp-card-pay-btn must have yellow background")

    def test_13_nunito_in_cp_buttons(self):
        """Nunito font must be referenced in cp-copy-btn or cp-card-pay-btn."""
        cp_btn_idx = self.css.find(".cp-copy-btn")
        cp_pay_idx = self.css.find(".cp-card-pay-btn")
        segment = (
            self.css[cp_btn_idx: cp_btn_idx + 400]
            + self.css[cp_pay_idx: cp_pay_idx + 400]
        )
        self.assertIn("Nunito", segment)


# ---------------------------------------------------------------------------
# Tests 14-15: Dark mode fix
# ---------------------------------------------------------------------------

class Test03DarkModeFix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_14_dark_mode_cp_card_solid_background(self):
        """Dark-mode cp-card must have a solid hex background (not near-transparent rgba)."""
        # Find all :root[data-theme="dark"] .cp-card rules
        flat_rules = re.findall(
            r':root\[data-theme="dark"\]\s+\.cp-card\s*\{([^}]+)\}',
            self.css
        )
        self.assertTrue(len(flat_rules) > 0, "Dark mode cp-card must have rules")
        # At least one rule must use a solid hex color for background
        solid = any(re.search(r'background:\s*#[0-9a-fA-F]{3,6}', block) for block in flat_rules)
        self.assertTrue(
            solid,
            f"At least one dark-mode cp-card rule must use solid hex background; "
            f"found: {flat_rules}"
        )

    def test_15_erip_summary_hover_opacity_is_1(self):
        """cp-erip-details > summary:hover must set opacity: 1."""
        pattern = r'\.cp-erip-details\s*>\s*summary:hover\s*\{([^}]+)\}'
        matches = re.findall(pattern, self.css)
        self.assertTrue(len(matches) > 0,
                        "cp-erip-details > summary:hover rule must exist")
        any_one = any(re.search(r'opacity:\s*1\b', m) for m in matches)
        self.assertTrue(any_one,
                        f"cp-erip-details > summary:hover must set opacity: 1; found: {matches}")


# ---------------------------------------------------------------------------
# Tests 16-17: Header string guard
# ---------------------------------------------------------------------------

class Test04HeaderStringGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_16_oplaty_appears_at_least_twice(self):
        """Оплаты · Yellow Club must appear in both role block and click handler."""
        count = self.js.count("Оплаты · Yellow Club")
        self.assertGreaterEqual(
            count, 2,
            f"Expected >=2 occurrences of 'Оплаты · Yellow Club', found {count}"
        )

    def test_17_pitanie_not_in_parent_payments_context(self):
        """Питание · Yellow Club must NOT appear in parent-role or client-payments context."""
        parent_idx = self.js.find('role === "parent"')
        pitanie_idx = self.js.find("Питание · Yellow Club")
        if pitanie_idx == -1:
            return  # Not found at all — pass
        # If it exists, ensure it's far from the parent role block (> 2000 chars away)
        self.assertGreater(
            abs(pitanie_idx - parent_idx), 2000,
            "'Питание · Yellow Club' must not appear near the parent role block"
        )


# ---------------------------------------------------------------------------
# Tests 18-21: Existing suite guards
# ---------------------------------------------------------------------------

class Test05ExistingGuards(unittest.TestCase):
    def test_18_parent_payments_importable(self):
        import tests.test_parent_payments  # noqa: F401

    def test_19_client_payments_importable(self):
        import tests.test_client_payments  # noqa: F401

    def test_20_client_parent_links_importable(self):
        import tests.test_client_parent_links  # noqa: F401

    def test_21_payment_restyle_importable(self):
        import tests.test_payment_restyle  # noqa: F401


# ---------------------------------------------------------------------------
# Test 22: Version marker
# ---------------------------------------------------------------------------

class Test06Version(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_22_version_marker(self):
        self.assertIn(f'console.log("MiniApp version: v{CURRENT_VERSION}")', self.js)
        self.assertIn(f"v={CURRENT_VERSION}", self.html)


if __name__ == "__main__":
    unittest.main()
