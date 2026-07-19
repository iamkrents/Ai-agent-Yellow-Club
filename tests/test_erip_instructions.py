"""Regression tests for v7.0.93.2.4 — ERIP payment instruction UX.

Covers:
  Frontend static analysis:
    1.  «Как оплатить через ЕРИП» text present in renderClientPaymentCard
    2.  ERIP code 7485856 declared as frontend constant ERIP_CODE
    3.  Button «Скопировать код ЕРИП» present in renderClientPaymentCard
    4.  Button «Скопировать номер заказа» present in renderClientPaymentCard
    5.  Dynamic order number taken from pi.erip_account_number (safeAcct variable)
    6.  Literal «9748998260715» not hardcoded in frontend source
    7.  Full navigation path present in app.js:
        Образование и развитие, Дополнительное образование и развитие,
        Обучение ИТ, Минск, Еллоу клаб, Обучение
    8.  Term «Номер заказа» used in card (correct terminology)
    9.  Old text «введите номер платежа» absent
    10. cpCopyOrderNum uses navigator.clipboard.writeText
    11. cpCopyEripCode exists and references ERIP_CODE
    12. ERIP instruction does not call checkout endpoint
    13. ERIP instruction does not call bepaid API endpoint

  Regression:
    14. test_client_payments importable (existing suite intact)
    15. test_parent_payments importable (existing suite intact)
    16. Food module functions not referenced inside copy/ERIP functions
    17. Version marker is v7.0.93.2.4
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

CURRENT_VERSION = "7.0.96.0"
ERIP_CODE_EXPECTED = "7485856"


def _extract_fn(js: str, fn_name: str) -> str:
    """Extract the source of a JS function by name (greedy brace matching)."""
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
                return js[start : i + 1]
        i += 1
    return js[start:]


class TestEripInstructionContent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.card_fn = _extract_fn(cls.js, "renderClientPaymentCard")

    def test_01_how_to_pay_text_in_card(self):
        self.assertIn("Как оплатить через ЕРИП", self.card_fn,
                      "renderClientPaymentCard must contain «Как оплатить через ЕРИП»")

    def test_02_erip_code_constant_declared(self):
        self.assertIn(f'ERIP_CODE = "{ERIP_CODE_EXPECTED}"', self.js,
                      f"ERIP_CODE must be declared as const ERIP_CODE = \"{ERIP_CODE_EXPECTED}\"")

    def test_03_copy_erip_code_button_in_card(self):
        self.assertIn("Скопировать код ЕРИП", self.card_fn,
                      "Button «Скопировать код ЕРИП» must be in renderClientPaymentCard")

    def test_04_copy_order_num_button_in_card(self):
        self.assertIn("Скопировать номер заказа", self.card_fn,
                      "Button «Скопировать номер заказа» must be in renderClientPaymentCard")

    def test_05_order_number_is_dynamic(self):
        # The card function must use safeAcct (derived from pi.erip_account_number)
        self.assertIn("erip_account_number", self.card_fn)
        self.assertIn("safeAcct", self.card_fn)

    def test_06_test_account_not_hardcoded(self):
        self.assertNotIn("9748998260715", self.js,
                         "Literal 9748998260715 must not be hardcoded in frontend source")

    def test_07_full_navigation_path_present(self):
        self.assertIn("Образование и развитие", self.card_fn)
        self.assertIn("Дополнительное образование и развитие", self.card_fn)
        self.assertIn("Обучение ИТ", self.card_fn)
        self.assertIn("Минск", self.card_fn)
        self.assertIn("Еллоу клаб", self.card_fn)
        self.assertIn("Обучение", self.card_fn)

    def test_08_correct_terminology_order_number(self):
        self.assertIn("Номер заказа", self.card_fn,
                      "Must use term «Номер заказа»")

    def test_09_old_text_removed(self):
        self.assertNotIn("введите номер платежа", self.js,
                         "Old text «введите номер платежа» must be removed")


class TestCopyFunctions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.order_fn = _extract_fn(cls.js, "cpCopyOrderNum")
        cls.erip_fn = _extract_fn(cls.js, "cpCopyEripCode")
        cls.base_fn = _extract_fn(cls.js, "_cpCopy")

    def test_10_copy_order_num_uses_clipboard(self):
        self.assertIn("navigator.clipboard.writeText", self.base_fn,
                      "_cpCopy must use navigator.clipboard.writeText")
        self.assertIn("cpCopyOrderNum", self.js)
        # cpCopyOrderNum must delegate to _cpCopy
        self.assertIn("_cpCopy", self.order_fn)

    def test_11_copy_erip_code_exists_and_uses_constant(self):
        self.assertNotEqual(self.erip_fn, "",
                            "cpCopyEripCode function must exist")
        self.assertIn("ERIP_CODE", self.erip_fn,
                      "cpCopyEripCode must reference ERIP_CODE constant")
        self.assertIn("_cpCopy", self.erip_fn)


class TestNoExternalCalls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.card_fn = _extract_fn(cls.js, "renderClientPaymentCard")
        cls.order_fn = _extract_fn(cls.js, "cpCopyOrderNum")
        cls.erip_fn = _extract_fn(cls.js, "cpCopyEripCode")

    def test_12_instruction_has_no_checkout_call(self):
        for fn in (self.card_fn, self.order_fn, self.erip_fn):
            self.assertNotIn("checkout", fn,
                             "ERIP instruction functions must not call checkout endpoint")

    def test_13_instruction_has_no_bepaid_api_call(self):
        for fn in (self.card_fn, self.order_fn, self.erip_fn):
            self.assertNotIn("bepaid", fn,
                             "ERIP instruction functions must not call bePaid API")


class TestRegressionSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_14_client_payments_importable(self):
        import tests.test_client_payments  # noqa: F401

    def test_15_parent_payments_importable(self):
        import tests.test_parent_payments  # noqa: F401

    def test_16_food_not_touched_in_copy_functions(self):
        js = self.js
        # Extract all three copy/ERIP functions and verify no food-module references
        for fn_name in ("cpCopyOrderNum", "cpCopyEripCode", "_cpCopy"):
            fn_src = _extract_fn(js, fn_name)
            self.assertNotIn("food", fn_src.lower(),
                             f"{fn_name} must not reference Food Module")
            self.assertNotIn("mk_user_id", fn_src,
                             f"{fn_name} must not reference mk_user_id")

    def test_17_version_marker(self):
        self.assertIn(f'console.log("MiniApp version: v{CURRENT_VERSION}")', self.js)
        self.assertIn(f"v={CURRENT_VERSION}", self.html)


if __name__ == "__main__":
    unittest.main()
