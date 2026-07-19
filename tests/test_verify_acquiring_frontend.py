"""Frontend hotfix tests — v7.0.92.5.4.

Verifies that verifyAcquiringPayment and reconcileTransaction in app.js
use existing helpers (_apiPostRaw) instead of the undefined apiFetch,
and that the request structure, error handling, and button guards are correct.

Tests parse JS source as plain text — no JS runtime needed.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "miniapp" / "app.js"
INDEX_HTML = Path(__file__).resolve().parent.parent / "miniapp" / "index.html"


def _extract_window_fn(source: str, name: str) -> str:
    """Extract the body of window.<name> = async function <name>(...) { ... }"""
    pattern = re.compile(
        r"window\." + re.escape(name) + r"\s*=\s*async\s+function\s+" + re.escape(name) + r"\s*\(",
        re.MULTILINE,
    )
    m = pattern.search(source)
    if not m:
        raise AssertionError(f"window.{name} not found in app.js")
    start = m.start()
    depth = 0
    i = source.index("{", start)
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start: i + 1]
        i += 1
    raise AssertionError(f"Could not find closing brace for window.{name}")


class Test01NoApiFetch(unittest.TestCase):
    """1. apiFetch must not exist anywhere in app.js."""

    @classmethod
    def setUpClass(cls):
        cls.source = APP_JS.read_text(encoding="utf-8")

    def test_01_apiFetch_not_called_anywhere(self):
        """apiFetch is undefined — any call causes ReferenceError at runtime."""
        self.assertNotIn(
            "apiFetch(",
            self.source,
            "apiFetch( found in app.js — will throw ReferenceError in browser",
        )

    def test_02_apiFetch_not_defined_anywhere(self):
        """apiFetch must also not be defined (no silent re-introduction)."""
        self.assertNotIn(
            "function apiFetch",
            self.source,
            "apiFetch is defined — it must not exist; use _apiPostRaw or apiPost",
        )


class Test02VerifyAcquiringFunction(unittest.TestCase):
    """2–6. verifyAcquiringPayment correctness."""

    @classmethod
    def setUpClass(cls):
        cls.source = APP_JS.read_text(encoding="utf-8")
        cls.fn = _extract_window_fn(cls.source, "verifyAcquiringPayment")

    def test_03_uses_apiPostRaw(self):
        """Button handler uses _apiPostRaw (existing POST helper)."""
        self.assertIn(
            "_apiPostRaw(",
            self.fn,
            "verifyAcquiringPayment must use _apiPostRaw for POST requests",
        )

    def test_04_does_not_use_apiFetch(self):
        """verifyAcquiringPayment must not reference the undefined apiFetch."""
        self.assertNotIn(
            "apiFetch",
            self.fn,
            "apiFetch found in verifyAcquiringPayment — will throw ReferenceError",
        )

    def test_05_correct_endpoint(self):
        """Endpoint must be /api/payments/intents/${...}/verify-acquiring."""
        self.assertIn(
            "/api/payments/intents/",
            self.fn,
            "Endpoint prefix /api/payments/intents/ not found",
        )
        self.assertIn(
            "/verify-acquiring",
            self.fn,
            "Endpoint suffix /verify-acquiring not found",
        )
        # Must not have double slash
        self.assertNotIn(
            "//api/",
            self.fn,
            "Double slash found in endpoint URL",
        )

    def test_06_no_double_slash_in_endpoint(self):
        """No accidental double slash in the API path."""
        # Extract the string containing verify-acquiring
        m = re.search(r"`[^`]*verify-acquiring[^`]*`", self.fn)
        if m:
            url_template = m.group(0)
            self.assertNotIn("//", url_template.replace("https://", "").replace("http://", ""),
                             "Double slash in verify-acquiring URL template")

    def test_07_checks_result_ok(self):
        """Handler inspects result.ok from the response."""
        self.assertIn(
            "result.ok",
            self.fn,
            "verifyAcquiringPayment must check result.ok",
        )

    def test_08_button_disabled_during_request(self):
        """Button is disabled before the API call (prevents double-click)."""
        self.assertIn(
            "btn.disabled = true",
            self.fn,
            "Button must be disabled at the start of the request",
        )

    def test_09_button_reenabled_on_error(self):
        """Button is re-enabled when the request fails or result.ok is false."""
        # Must appear at least twice: once in else branch, once in catch
        count = self.fn.count("btn.disabled = false")
        self.assertGreaterEqual(
            count, 2,
            f"btn.disabled = false appears {count} times — expected at least 2 (else + catch)",
        )

    def test_10_loads_payment_intents_on_success(self):
        """On success, loadPaymentIntents() is called to refresh the list."""
        self.assertIn(
            "loadPaymentIntents()",
            self.fn,
            "verifyAcquiringPayment must call loadPaymentIntents() after success",
        )

    def test_11_error_message_from_result(self):
        """Error message is extracted from result.reason or result.error."""
        self.assertRegex(
            self.fn,
            r"result\.(reason|error)",
            "Error message must come from result.reason or result.error",
        )

    def test_12_double_click_blocked_by_disabled_btn(self):
        """publicId guard + btn.disabled ensures rapid double-click is blocked."""
        # publicId guard
        self.assertIn("if (!publicId) return", self.fn)
        # btn disabled set at start
        self.assertIn("btn.disabled = true", self.fn)


class Test03ReconcileFunction(unittest.TestCase):
    """reconcileTransaction also had apiFetch — verify it's fixed too."""

    @classmethod
    def setUpClass(cls):
        cls.source = APP_JS.read_text(encoding="utf-8")
        cls.fn = _extract_window_fn(cls.source, "reconcileTransaction")

    def test_13_no_apiFetch_in_reconcile(self):
        """reconcileTransaction must not use apiFetch."""
        self.assertNotIn(
            "apiFetch",
            self.fn,
            "apiFetch found in reconcileTransaction — will throw ReferenceError",
        )

    def test_14_reconcile_uses_apiPostRaw(self):
        """reconcileTransaction uses _apiPostRaw for the POST call."""
        self.assertIn(
            "_apiPostRaw(",
            self.fn,
            "reconcileTransaction must use _apiPostRaw",
        )


class Test04HelpersDefined(unittest.TestCase):
    """Sanity: verify required helpers exist in app.js."""

    @classmethod
    def setUpClass(cls):
        cls.source = APP_JS.read_text(encoding="utf-8")

    def test_15_apiPostRaw_defined(self):
        """_apiPostRaw is defined in app.js."""
        self.assertIn("async function _apiPostRaw(", self.source)

    def test_16_apiPost_defined(self):
        """apiPost is defined in app.js."""
        self.assertIn("async function apiPost(", self.source)

    def test_17_apiGet_defined(self):
        """apiGet is defined in app.js."""
        self.assertIn("async function apiGet(", self.source)


class Test05VersionAndCacheBust(unittest.TestCase):
    """Version marker and cache-bust updated (now v7.0.93.1)."""

    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_18_version_is_current(self):
        """app.js version marker is v7.0.94.1."""
        self.assertIn("v7.0.96.0", self.js)

    def test_19_cache_bust_is_current(self):
        """index.html cache-bust parameter is v=7.0.94.1."""
        self.assertIn("v=7.0.96.0", self.html)

    def test_20_old_version_not_in_js(self):
        """Old version marker v7.0.92.5.3 is gone from version log line."""
        self.assertNotIn(
            'MiniApp version: v7.0.92.5.3',
            self.js,
            "Old version string still present in app.js",
        )
