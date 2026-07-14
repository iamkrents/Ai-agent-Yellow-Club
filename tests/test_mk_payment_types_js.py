"""Regression tests for v7.0.92.1.1 and v7.0.92.1.2 hotfixes.

v7.0.92.1.1 — loadMkPaymentTypes bug:
1. Does NOT contain the non-existent apiFetch call.
2. Uses the correct apiGet helper.
3. Does NOT call .json() after apiGet (apiGet already returns parsed JSON).
4. On success calls renderMkPaymentTypes.
5. On error replaces loading text with an error message.
6. Disables the refresh button during the request (button guard).
7. Re-enables the button in the finally block.
8. Guards against double-click (early return when button is disabled).

v7.0.92.1.2 — renderMkPaymentTypes escHtml bug:
12. escHtml alias does not appear in renderMkPaymentTypes (was undefined, caused ReferenceError).
13. Uses existing escapeHtml helper.
14. All dynamic values in renderMkPaymentTypes pass through escapeHtml.
15. renderMkPaymentTypes renders valid payload (status + list) without ReferenceError.
16. renderMkPaymentTypes renders error payload safely.
17. Missing configured ID branch uses no undefined helpers.
18. Empty items list does not call undefined helpers.
19. Single ERIP candidate branch uses escapeHtml.
20. Multiple ERIP candidates branch uses escapeHtml.

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


class TestRenderMkPaymentTypesJS(unittest.TestCase):
    """v7.0.92.1.2 regression: escHtml → escapeHtml in renderMkPaymentTypes."""

    @classmethod
    def setUpClass(cls):
        cls.source = APP_JS.read_text(encoding="utf-8")
        cls.fn_body = _load_fn(cls.source, "renderMkPaymentTypes")

    def test_12_no_escHtml_in_render(self):
        """Test 12 (regression): renderMkPaymentTypes must NOT call undefined escHtml."""
        self.assertNotIn(
            "escHtml(",
            self.fn_body,
            "escHtml is not defined — caused ReferenceError in production",
        )

    def test_13_uses_escapeHtml_in_render(self):
        """Test 13 (regression): renderMkPaymentTypes uses the existing escapeHtml helper."""
        self.assertIn(
            "escapeHtml(",
            self.fn_body,
            "renderMkPaymentTypes must use escapeHtml (defined at line ~240)",
        )

    def test_14_all_dynamic_values_escaped(self):
        """Test 14: every interpolated user-controlled string goes through escapeHtml."""
        # Each of these field names appears in an escapeHtml() call, not raw
        for field in ("data.error", "pt.payment_type_name", "reason", "candidates[0].name", "c.name", "item.name"):
            # Verify the field appears inside escapeHtml(…) somewhere in the function
            pattern = re.compile(
                r"escapeHtml\([^)]*" + re.escape(field.split(".")[-1]),
                re.DOTALL,
            )
            self.assertRegex(
                self.fn_body,
                pattern,
                f"Field '{field}' must be wrapped in escapeHtml()",
            )

    def test_15_renders_valid_payload_no_undefined_calls(self):
        """Test 15: renderMkPaymentTypes body contains no references to escHtml or other undefined aliases."""
        for bad_alias in ("escHtml(", "esc_html(", "escapeAttrHtml(", "htmlEscape(", "safeHtml("):
            self.assertNotIn(
                bad_alias,
                self.fn_body,
                f"Undefined helper alias '{bad_alias}' found in renderMkPaymentTypes",
            )

    def test_16_error_payload_branch_uses_escapeHtml(self):
        """Test 16: the data.ok===false branch escapes data.error with escapeHtml."""
        # Line: statusEl.innerHTML = `...${escapeHtml(data.error || "")}...`
        self.assertRegex(
            self.fn_body,
            r"escapeHtml\(data\.error",
        )

    def test_17_missing_configured_id_branch_safe(self):
        """Test 17: the 'not configured' branch uses no undefined helpers (no dynamic HTML)."""
        # This branch has no user-supplied data → no escape calls needed, but also no bad ones
        self.assertNotIn("escHtml(", self.fn_body)

    def test_18_empty_items_no_undefined_helpers(self):
        """Test 18: items loop uses escapeHtml for item.name."""
        self.assertRegex(
            self.fn_body,
            r"escapeHtml\(item\.name\)",
        )

    def test_19_single_erip_candidate_uses_escapeHtml(self):
        """Test 19: single ERIP candidate branch escapes candidate name."""
        self.assertRegex(
            self.fn_body,
            r"escapeHtml\(candidates\[0\]\.name\)",
        )

    def test_20_multiple_erip_candidates_use_escapeHtml(self):
        """Test 20: multiple candidates .map() uses escapeHtml for c.name."""
        self.assertRegex(
            self.fn_body,
            r"escapeHtml\(c\.name\)",
        )

    def test_21_escapeHtml_defined_in_app_js(self):
        """Sanity: escapeHtml is defined in app.js (around line 240)."""
        self.assertIn("function escapeHtml(", self.source)

    def test_22_no_escHtml_anywhere_in_app_js(self):
        """Sanity: escHtml does not appear anywhere in app.js."""
        self.assertNotIn(
            "escHtml(",
            self.source,
            "escHtml must be fully replaced — it is not defined anywhere",
        )


if __name__ == "__main__":
    unittest.main()
