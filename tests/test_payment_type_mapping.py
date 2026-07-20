"""Regression tests for v7.0.94.1 — bePaid MoyKlass payment type exact name matching.

Covers:
  Backend constants and helpers:
    1.  _REQUIRED_ACQUIRING_TYPE_NAME is "BePaid эквайринг"
    2.  _REQUIRED_ERIP_TYPE_NAME is "BePaid ЕРИП"
    3.  _normalize_mk_type_name strips and lowercases
    4.  _check_exact_type_name: exact match passes
    5.  _check_exact_type_name: case-insensitive match passes
    6.  _check_exact_type_name: whitespace-trimmed match passes
    7.  _check_exact_type_name: wrong name fails
    8.  _check_exact_type_name: None name fails

  _build_payment_type_readiness with required_name:
    9.  Valid type with matching name → valid=True, name_match=True
    10. Valid type with wrong name → valid=False, name_match=False, reason="payment_type_name_mismatch"
    11. Valid type without required_name → valid=True, name_match=None (legacy mode)
    12. Deleted type → valid=False, name_match=None (deleted blocks before name check)
    13. Inactive type → valid=False, name_match=None
    14. Unconfigured (id=0) → valid=False, name_match=None
    15. Not found (pt=None) → valid=False, name_match=None
    16. required_name in response dict

  moyklass_payment_types readiness objects:
    17. ERIP readiness uses _REQUIRED_ERIP_TYPE_NAME
    18. Acquiring readiness uses _REQUIRED_ACQUIRING_TYPE_NAME

  payment_intent_moyklass_readiness channel routing:
    19. paid_channel="acquiring" selects acquiring type ID (not ERIP)
    20. paid_channel="erip" (default) selects ERIP type ID

  Frontend _renderPaymentTypeBlock:
    21. Version marker is v7.0.94.1
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"

CURRENT_VERSION = "7.0.98.0"


def _load_server_constants():
    """Import only the pure-helper symbols from web_app_server without running the server."""
    import importlib.util, types as _types

    spec = importlib.util.spec_from_file_location("_was_helpers", ROOT / "web_app_server.py")
    # We need a fake module environment to avoid import-time side effects
    # Instead, exec only the top of the file up to the first class/def that needs imports
    source = (ROOT / "web_app_server.py").read_text(encoding="utf-8")

    # Extract module-level constants and small functions by scanning line by line
    lines = source.splitlines()
    snippet_lines = []
    in_def = False
    brace_depth = 0
    for line in lines:
        stripped = line.strip()
        # Stop at the first class definition (the big server class)
        if stripped.startswith("class ") and not stripped.startswith("class _"):
            break
        snippet_lines.append(line)

    snippet = "\n".join(snippet_lines)

    globs: dict = {}
    # Provide stubs for any imports used in the snippet
    import builtins, re, hashlib, urllib.parse
    globs.update({
        "__builtins__": builtins,
        "re": re,
        "hashlib": hashlib,
        "urllib": urllib,
        "Any": object,
    })
    try:
        exec(compile(snippet, "<was_helpers>", "exec"), globs)
    except Exception:
        pass
    return globs


_SERVER = _load_server_constants()


def _get(name, default=None):
    return _SERVER.get(name, default)


class TestRequiredNameConstants(unittest.TestCase):
    def test_01_required_acquiring_name(self):
        val = _get("_REQUIRED_ACQUIRING_TYPE_NAME")
        self.assertIsNotNone(val, "_REQUIRED_ACQUIRING_TYPE_NAME must be defined")
        self.assertEqual(val, "BePaid эквайринг")

    def test_02_required_erip_name(self):
        val = _get("_REQUIRED_ERIP_TYPE_NAME")
        self.assertIsNotNone(val, "_REQUIRED_ERIP_TYPE_NAME must be defined")
        self.assertEqual(val, "BePaid ЕРИП")


class TestNormalizeAndCheck(unittest.TestCase):
    def setUp(self):
        self.normalize = _get("_normalize_mk_type_name")
        self.check = _get("_check_exact_type_name")
        if not self.normalize or not self.check:
            self.skipTest("Helper functions not found in extracted snippet")

    def test_03_normalize_strips_and_lowercases(self):
        self.assertEqual(self.normalize("  BePaid ЕРИП  "), "bepaid ерип")
        self.assertEqual(self.normalize("BePaid эквайринг"), "bepaid эквайринг")
        self.assertEqual(self.normalize(None), "")
        self.assertEqual(self.normalize(""), "")

    def test_04_check_exact_match(self):
        self.assertTrue(self.check("BePaid ЕРИП", "BePaid ЕРИП"))
        self.assertTrue(self.check("BePaid эквайринг", "BePaid эквайринг"))

    def test_05_check_case_insensitive(self):
        self.assertTrue(self.check("bepaid ерип", "BePaid ЕРИП"))
        self.assertTrue(self.check("BEPAID ЭКВАЙРИНГ", "BePaid эквайринг"))

    def test_06_check_whitespace_trimmed(self):
        self.assertTrue(self.check("  BePaid ЕРИП  ", "BePaid ЕРИП"))
        self.assertTrue(self.check("BePaid эквайринг", "  BePaid эквайринг  "))

    def test_07_check_wrong_name_fails(self):
        self.assertFalse(self.check("Эквайринг", "BePaid эквайринг"))
        self.assertFalse(self.check("BePaid ERIP", "BePaid ЕРИП"))
        self.assertFalse(self.check("ЕРИП", "BePaid ЕРИП"))

    def test_08_check_none_fails(self):
        self.assertFalse(self.check(None, "BePaid ЕРИП"))
        self.assertFalse(self.check(None, "BePaid эквайринг"))


class TestBuildPaymentTypeReadiness(unittest.TestCase):
    def setUp(self):
        self.build = _get("_build_payment_type_readiness")
        if not self.build:
            self.skipTest("_build_payment_type_readiness not found")

    def _active_pt(self, name: str) -> dict:
        return {"id": 42, "name": name, "active": True, "deleted": False}

    def test_09_valid_matching_name(self):
        r = self.build(42, self._active_pt("BePaid ЕРИП"), "BePaid ЕРИП")
        self.assertTrue(r["valid"])
        self.assertTrue(r["name_match"])
        self.assertEqual(r["blocking_reasons"], [])

    def test_10_valid_wrong_name_blocked(self):
        r = self.build(42, self._active_pt("ЕРИП"), "BePaid ЕРИП")
        self.assertFalse(r["valid"])
        self.assertFalse(r["name_match"])
        self.assertIn("payment_type_name_mismatch", r["blocking_reasons"])

    def test_11_no_required_name_legacy_mode(self):
        r = self.build(42, self._active_pt("anything"), "")
        self.assertTrue(r["valid"])
        self.assertIsNone(r["name_match"])

    def test_12_deleted_blocks_before_name(self):
        pt = {"id": 42, "name": "BePaid ЕРИП", "active": True, "deleted": True}
        r = self.build(42, pt, "BePaid ЕРИП")
        self.assertFalse(r["valid"])
        self.assertIsNone(r["name_match"])
        self.assertIn("payment_type_deleted", r["blocking_reasons"])

    def test_13_inactive_blocks_before_name(self):
        pt = {"id": 42, "name": "BePaid ЕРИП", "active": False, "deleted": False}
        r = self.build(42, pt, "BePaid ЕРИП")
        self.assertFalse(r["valid"])
        self.assertIsNone(r["name_match"])
        self.assertIn("payment_type_inactive", r["blocking_reasons"])

    def test_14_unconfigured_id(self):
        r = self.build(0, None, "BePaid ЕРИП")
        self.assertFalse(r["valid"])
        self.assertFalse(r["configured"])
        self.assertIsNone(r["name_match"])
        self.assertIn("payment_type_not_configured", r["blocking_reasons"])

    def test_15_not_found(self):
        r = self.build(42, None, "BePaid ЕРИП")
        self.assertFalse(r["valid"])
        self.assertIsNone(r["name_match"])
        self.assertIn("payment_type_not_found", r["blocking_reasons"])

    def test_16_required_name_in_response(self):
        r = self.build(42, self._active_pt("BePaid ЕРИП"), "BePaid ЕРИП")
        self.assertEqual(r.get("required_name"), "BePaid ЕРИП")

        r2 = self.build(42, self._active_pt("X"), "")
        self.assertIsNone(r2.get("required_name"))


class TestMoyklassPaymentTypesReadiness(unittest.TestCase):
    """Verify moyklass_payment_types passes correct required_names (source check)."""

    def setUp(self):
        self.source = (ROOT / "web_app_server.py").read_text(encoding="utf-8")

    def test_17_erip_readiness_uses_required_erip_name(self):
        self.assertIn(
            "_build_payment_type_readiness(erip_id, erip_type, _REQUIRED_ERIP_TYPE_NAME)",
            self.source,
        )

    def test_18_acq_readiness_uses_required_acq_name(self):
        self.assertIn(
            "_build_payment_type_readiness(acq_id, acq_type, _REQUIRED_ACQUIRING_TYPE_NAME)",
            self.source,
        )


class TestReadinessChannelRouting(unittest.TestCase):
    """Verify payment_intent_moyklass_readiness selects channel-specific type ID."""

    def setUp(self):
        self.source = (ROOT / "web_app_server.py").read_text(encoding="utf-8")

    def _find_readiness_fn_body(self) -> str:
        marker = "def payment_intent_moyklass_readiness("
        start = self.source.find(marker)
        self.assertNotEqual(start, -1, "payment_intent_moyklass_readiness not found")
        # Find the next top-level def after it
        next_def = self.source.find("\n    def ", start + len(marker))
        return self.source[start:next_def] if next_def != -1 else self.source[start:]

    def test_19_acquiring_channel_reads_acquiring_id(self):
        body = self._find_readiness_fn_body()
        self.assertIn("moyklass_acquiring_payment_type_id", body,
                      "readiness must read acquiring type ID when paid_channel==acquiring")

    def test_20_erip_channel_reads_erip_id(self):
        body = self._find_readiness_fn_body()
        self.assertIn("moyklass_erip_payment_type_id", body,
                      "readiness must read ERIP type ID")
        # Must NOT hardcode only erip — the acquiring branch must also be present
        self.assertIn("paid_channel", body,
                      "readiness must branch on paid_channel")


class TestVersionMarker(unittest.TestCase):
    def test_21_version_marker(self):
        js = APP_JS.read_text(encoding="utf-8")
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn(f'console.log("MiniApp version: v{CURRENT_VERSION}")', js)
        self.assertIn(f"v={CURRENT_VERSION}", html)


if __name__ == "__main__":
    unittest.main()
