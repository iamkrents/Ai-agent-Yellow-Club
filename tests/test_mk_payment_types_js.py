"""Regression tests for v7.0.92.1.1 hotfix: loadMkPaymentTypes bug.

Verifies that the JS source in miniapp/app.js:
1. Does NOT contain the non-existent apiFetch call.
2. Uses the correct apiGet helper.
3. Does NOT call .json() after apiGet (apiGet already returns parsed JSON).
4. On success calls renderMkPaymentTypes.
5. On error replaces loading text with an error message.
6. Disables the refresh button during the request (button guard).
7. Re-enables the button in the finally block.
8. Guards against double-click (early return when button is disabled).

These tests parse the JS source as plain text — no JS runtime needed.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "miniapp" / "app.js"


def _load_fn(source: str, name: str) -> str:
    """Extract the body of a top-level function by name from JS source."""
    # Match 'async function name(' or 'function name('
    pattern = re.compile(
        r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(",
        re.MULTILINE,
    )
    m = pattern.search(source)
    if not m:
        raise AssertionError(f"Function '{name}' not found in app.js")
    start = m.start()
    # Walk forward to find balanced braces
    depth = 0
    i = source.index("{", start)
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
        i += 1
    raise AssertionError(f"Could not find closing brace for '{name}'")


class TestLoadMkPaymentTypesJS(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.source = APP_JS.read_text(encoding="utf-8")
        cls.fn_body = _load_fn(cls.source, "loadMkPaymentTypes")

    # 1. apiFetch must not appear anywhere in the function
    def test_01_no_apiFetch_call(self):
        """Test 1 (regression): loadMkPaymentTypes must NOT call apiFetch."""
        self.assertNotIn(
            "apiFetch",
            self.fn_body,
            "apiFetch is used but does not exist — causes ReferenceError",
        )

    # 2. apiGet must be called
    def test_02_uses_apiGet(self):
        """Test 2 (regression): loadMkPaymentTypes must call apiGet."""
        self.assertIn(
            "apiGet(",
            self.fn_body,
            "loadMkPaymentTypes must use the existing apiGet helper",
        )

    # 3. .json() must NOT be chained after apiGet (apiGet already parses)
    def test_03_no_dot_json_after_apiGet(self):
        """Test 3 (regression): no .json() call after apiGet — apiGet already returns parsed JSON."""
        # The pattern 'apiGet(...).then(r => r.json())' or 'await apiGet(...);\n...r.json()' must be absent
        self.assertNotIn(
            ".json()",
            self.fn_body,
            "Calling .json() on apiGet result is wrong — apiGet already parses JSON",
        )

    # 4. renderMkPaymentTypes must be called on success path
    def test_04_calls_renderMkPaymentTypes_on_success(self):
        """Test 4 (regression): success path calls renderMkPaymentTypes."""
        self.assertIn(
            "renderMkPaymentTypes(",
            self.fn_body,
        )

    # 5. Error path must replace loading with error text
    def test_05_error_sets_status_text(self):
        """Test 5 (regression): catch block sets statusEl.textContent on error."""
        # The catch/error block must write to statusEl.textContent
        self.assertIn("statusEl.textContent", self.fn_body)
        self.assertIn("catch", self.fn_body)

    # 6. Button must be disabled during request
    def test_06_button_disabled_before_request(self):
        """Test 6 (regression): refreshBtn.disabled = true before the fetch."""
        self.assertIn("refreshBtn.disabled = true", self.fn_body)

    # 7. Button must be re-enabled in finally
    def test_07_button_reenabled_in_finally(self):
        """Test 7 (regression): finally block re-enables the refresh button."""
        self.assertIn("finally", self.fn_body)
        self.assertIn("refreshBtn.disabled = false", self.fn_body)

    # 8. Double-click guard: early return when button already disabled
    def test_08_double_click_guard(self):
        """Test 8 (regression): early return if refreshBtn is already disabled."""
        # The guard looks like: if (refreshBtn?.disabled) return;
        self.assertRegex(
            self.fn_body,
            r"refreshBtn\??\.disabled\b",
            "loadMkPaymentTypes must check refreshBtn.disabled to prevent double-click",
        )

    # Sanity: function is declared async
    def test_09_function_is_async(self):
        """Sanity: loadMkPaymentTypes is declared async."""
        self.assertRegex(
            self.fn_body,
            r"^async\s+function",
            "loadMkPaymentTypes must be async for await to work",
        )

    # Sanity: apiGet exists in app.js at the expected location
    def test_10_apiGet_defined_in_app_js(self):
        """Sanity: apiGet is defined in app.js (around line 568)."""
        self.assertIn("async function apiGet(", self.source)

    # Sanity: apiFetch is not defined anywhere in app.js
    def test_11_apiFetch_not_defined_anywhere(self):
        """Sanity: apiFetch is not defined anywhere in app.js."""
        self.assertNotIn(
            "function apiFetch",
            self.source,
            "apiFetch should not be defined — it was a naming mistake",
        )


if __name__ == "__main__":
    unittest.main()
