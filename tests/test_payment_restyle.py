"""Regression tests for v7.0.93.2.5 — client payment page Yellow Club rebrand.

Covers:
  CSS structure:
    1.  cp-card class uses white or branded background
    2.  Primary payment button has no purple color
    3.  Primary payment button uses yellow (#ffd84d)
    4.  Card background is white (#fff)
    5.  Student name has dark (#172033) text color
    6.  ERIP block (cp-erip-block) has yellow-tinted background
    7.  Order number (cp-erip-value) has dark readable color
    8.  ERIP code field has dark readable color (same class)
    9.  Copy buttons disabled state does not use opacity < 1
    10. Accordion (cp-erip-details) has distinct CSS
    11. Instruction steps do not have opacity less than 1
    12. Nunito font is referenced in global body styles
    13. Mobile word-wrap style exists for student name
    14. Long student name protected by overflow-wrap or word-break
    15. Long order number protected by word-break on cp-erip-value
    16. Bottom padding >= 60px protects content from bottom nav

  JavaScript structure:
    17. Clipboard copy functions preserved
    18. Card payment href link preserved in renderClientPaymentCard
    19. ERIP account number is dynamic (safeAcct from pi.erip_account_number)
    20. ERIP code 7485856 constant present

  Regression:
    21. Existing client payments tests pass (importable)
    22. Existing parent payments tests pass (importable)
    23. Existing ERIP instruction tests pass (importable)
    24. Food module not touched
    25. Version marker is v7.0.93.2.5
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

CURRENT_VERSION = "7.0.99.0"
PURPLE_HEX = "#6366f1"
PURPLE_RGBA = "rgba(99,102,241"
YELLOW_HEX = "#ffd84d"
DARK_TEXT = "#172033"


def _css_block(css: str, selector: str) -> str:
    """Extract the first rule block for the given selector."""
    idx = css.find(selector + " {")
    if idx == -1:
        idx = css.find(selector + "{")
    if idx == -1:
        return ""
    end = css.find("}", idx)
    return css[idx: end + 1] if end != -1 else ""


def _extract_fn(js: str, fn_name: str) -> str:
    marker = f"function {fn_name}("
    start = js.find(marker)
    if start == -1:
        return ""
    depth = 0
    i = start
    while i < len(js):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                return js[start: i + 1]
        i += 1
    return js[start:]


class TestCSSBrandColors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = STYLES_CSS.read_text(encoding="utf-8")
        cls.cp_card_block = _css_block(cls.css, ".cp-card")
        cls.cp_pay_btn_block = _css_block(cls.css, ".cp-card-pay-btn")
        cls.cp_erip_block_block = _css_block(cls.css, ".cp-erip-block")
        cls.cp_erip_value_block = _css_block(cls.css, ".cp-erip-value")
        cls.cp_copy_disabled_block = _css_block(cls.css, ".cp-copy-btn:disabled")
        cls.cp_erip_details_block = _css_block(cls.css, ".cp-erip-details")
        cls.cp_erip_steps_li_block = _css_block(cls.css, ".cp-erip-steps li")

    def test_01_card_uses_branded_class(self):
        self.assertIn(".cp-card", self.css,
                      "cp-card CSS class must be defined")

    def test_02_primary_button_no_purple(self):
        self.assertNotIn(PURPLE_HEX, self.cp_pay_btn_block,
                         "cp-card-pay-btn must not use purple (#6366f1)")
        self.assertNotIn(PURPLE_RGBA, self.cp_pay_btn_block,
                         "cp-card-pay-btn must not use purple rgba(99,102,241)")

    def test_03_primary_button_uses_yellow(self):
        self.assertIn(YELLOW_HEX, self.cp_pay_btn_block,
                      f"cp-card-pay-btn must use Yellow Club yellow ({YELLOW_HEX})")

    def test_04_card_has_white_background(self):
        self.assertIn("#fff", self.cp_card_block,
                      "cp-card must have white (#fff) background")

    def test_05_student_name_has_dark_color(self):
        name_block = _css_block(self.css, ".cp-card-name")
        self.assertIn(DARK_TEXT, name_block,
                      f"cp-card-name must use dark text ({DARK_TEXT})")

    def test_06_erip_block_has_yellow_tint(self):
        self.assertNotEqual(self.cp_erip_block_block, "",
                            "cp-erip-block CSS class must be defined")
        # Must contain yellow-tinted background (rgba with 255,248 or 255,216 or similar)
        has_yellow = (
            "rgba(255,248" in self.cp_erip_block_block or
            "rgba(255,216" in self.cp_erip_block_block or
            "#fff8" in self.cp_erip_block_block.lower() or
            "#fffbd" in self.cp_erip_block_block.lower()
        )
        self.assertTrue(has_yellow,
                        "cp-erip-block must have a yellow-tinted background")

    def test_07_order_number_has_dark_color(self):
        self.assertIn(DARK_TEXT, self.cp_erip_value_block,
                      f"cp-erip-value must use dark text ({DARK_TEXT})")

    def test_08_erip_code_same_dark_class(self):
        # ERIP code uses the same cp-erip-value class
        self.assertIn(".cp-erip-value", self.css)
        self.assertIn(DARK_TEXT, self.cp_erip_value_block)

    def test_09_copy_disabled_not_faded(self):
        # disabled state must NOT have opacity < 1 (opacity: .6, opacity: 0.6, etc.)
        self.assertNotIn(": .6", self.cp_copy_disabled_block,
                         "cp-copy-btn:disabled must not use opacity: .6")
        self.assertNotIn(": 0.6", self.cp_copy_disabled_block,
                         "cp-copy-btn:disabled must not use opacity: 0.6")
        # Verify opacity:1 or no opacity set — both are fine
        if "opacity" in self.cp_copy_disabled_block:
            self.assertIn("opacity: 1", self.cp_copy_disabled_block,
                          "If opacity is set on disabled, it must be 1")

    def test_10_accordion_has_distinct_styles(self):
        self.assertNotEqual(self.cp_erip_details_block, "",
                            "cp-erip-details CSS block must be defined")
        self.assertIn(".cp-erip-details > summary", self.css,
                      "cp-erip-details > summary must be styled")
        self.assertIn("::-webkit-details-marker", self.css,
                      "Browser default marker must be hidden")

    def test_11_instruction_not_transparent(self):
        # Steps must not have opacity < 1
        if "opacity" in self.cp_erip_steps_li_block:
            self.assertIn("opacity: 1", self.cp_erip_steps_li_block,
                          "cp-erip-steps li opacity must be 1 (not faded)")
        # Also no global opacity on the details block
        if "opacity" in self.cp_erip_details_block:
            self.assertNotIn(": .5", self.cp_erip_details_block)
            self.assertNotIn(": 0.5", self.cp_erip_details_block)


class TestCSSMobile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = STYLES_CSS.read_text(encoding="utf-8")
        cls.name_block = _css_block(cls.css, ".cp-card-name")
        cls.value_block = _css_block(cls.css, ".cp-erip-value")

    def test_12_nunito_globally_applied(self):
        # Nunito is imported and applied on body — find the body rule that contains Nunito
        self.assertIn("Nunito", self.css,
                      "Nunito must appear somewhere in styles.css")
        # Find a body rule that actually sets the font-family with Nunito
        idx = 0
        found = False
        while True:
            idx = self.css.find("body", idx)
            if idx == -1:
                break
            end = self.css.find("}", idx)
            if end == -1:
                break
            block = self.css[idx: end + 1]
            if "Nunito" in block and "font-family" in block:
                found = True
                break
            idx += 1
        self.assertTrue(found, "A body rule with font-family: Nunito must exist in styles.css")

    def test_13_mobile_word_wrap_in_css(self):
        # At least one of these protective wrap styles must be present
        has_wrap = (
            "word-break" in self.name_block or
            "overflow-wrap" in self.name_block
        )
        self.assertTrue(has_wrap,
                        "cp-card-name must have word-break or overflow-wrap for long names")

    def test_14_long_name_protected(self):
        self.assertIn(".cp-card-name", self.css)
        has_break = (
            "word-break: break-word" in self.name_block or
            "word-break: break-all" in self.name_block or
            "overflow-wrap: anywhere" in self.name_block or
            "overflow-wrap: break-word" in self.name_block
        )
        self.assertTrue(has_break,
                        "cp-card-name must have overflow-wrap or word-break value")

    def test_15_long_order_number_protected(self):
        self.assertIn(".cp-erip-value", self.css)
        has_break = (
            "word-break: break-all" in self.value_block or
            "word-break: break-word" in self.value_block or
            "overflow-wrap: anywhere" in self.value_block
        )
        self.assertTrue(has_break,
                        "cp-erip-value must have word-break: break-all or overflow-wrap for long numbers")

    def test_16_bottom_padding_for_nav(self):
        # cp-list or cp-card must have sufficient padding-bottom
        list_block = _css_block(self.css, ".cp-list")
        card_block = _css_block(self.css, ".cp-card")
        combined = list_block + card_block
        # Must have padding-bottom with at least 60px
        m = re.search(r"padding-bottom:\s*(\d+)px", combined)
        self.assertIsNotNone(m, "cp-list or cp-card must define padding-bottom (for bottom nav)")
        val = int(m.group(1))
        self.assertGreaterEqual(val, 60,
                                f"padding-bottom must be >= 60px to protect from bottom nav, got {val}px")


class TestJSStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.card_fn = _extract_fn(cls.js, "renderClientPaymentCard")

    def test_17_clipboard_functions_preserved(self):
        self.assertIn("function cpCopyOrderNum(", self.js)
        self.assertIn("function cpCopyEripCode(", self.js)
        self.assertIn("navigator.clipboard.writeText", self.js)

    def test_18_card_link_preserved(self):
        # v7.0.99.0: card pay is a <button> calling cpOpenCardPay (no longer an <a href>)
        self.assertIn("acquiring_payment_url", self.card_fn)
        self.assertIn("cpOpenCardPay", self.card_fn)
        self.assertIn("cp-card-pay-btn", self.card_fn)
        self.assertIn("Оплатить банковской картой", self.card_fn)

    def test_19_erip_account_is_dynamic(self):
        self.assertIn("erip_account_number", self.card_fn)
        self.assertIn("safeAcct", self.card_fn)
        # safeAcct must come from pi.erip_account_number
        self.assertIn("pi.erip_account_number", self.card_fn)

    def test_20_erip_code_constant_intact(self):
        # The constant is declared outside the function; the function references ERIP_CODE
        self.assertIn('ERIP_CODE = "7485856"', self.js,
                      "ERIP_CODE constant must be declared with value 7485856")
        self.assertIn("ERIP_CODE", self.card_fn,
                      "renderClientPaymentCard must reference ERIP_CODE constant")


class TestRegressionSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_21_client_payments_importable(self):
        import tests.test_client_payments  # noqa: F401

    def test_22_parent_payments_importable(self):
        import tests.test_parent_payments  # noqa: F401

    def test_23_erip_instructions_importable(self):
        import tests.test_erip_instructions  # noqa: F401

    def test_24_food_not_touched(self):
        # renderClientPaymentCard must not reference food-specific fields
        card_fn = _extract_fn(self.js, "renderClientPaymentCard")
        self.assertNotIn("food", card_fn.lower())
        self.assertNotIn("mk_user_id", card_fn)

    def test_25_version_marker(self):
        self.assertIn(f'console.log("MiniApp version: v{CURRENT_VERSION}")', self.js)
        self.assertIn(f"v={CURRENT_VERSION}", self.html)


if __name__ == "__main__":
    unittest.main()
