"""Tests for v7.1.15 — launch-readiness UI: CL-code linking states (loading/
success/error/retry) and the "Подключения" diagnostics dashboard.

Static text/AST-style checks only (reads app.js/index.html/styles.css/
web_app_server.py as text), consistent with this repo's existing frontend
test convention. No browser, no real fetch.

Covers:
  21. Double submit is blocked.
  22. There is a loading/success/error/retry state sequence.
  23. A temporary error does not clear the typed code.
  24. A repeat success opens the (already-created) cabinet.
  25. Roles are restricted (owner/admin/client_manager only).
  26. Invite tokens/signatures are never exposed by the diagnostics endpoints.
  27. Mobile 360/375px — no fixed-pixel-width overflow risk in the new CSS.

Run:
    python -m unittest tests.test_client_onboarding_ui_v7115 -v
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
WEB_APP_SERVER_PY = (ROOT / "web_app_server.py").read_text(encoding="utf-8")


def _fn_body(js: str, name: str) -> str:
    m = re.search(r"async function " + re.escape(name) + r"\(\) \{(.*?)\n\}\n", js, re.S)
    assert m, f"function {name} not found"
    return m.group(1)


class TestClCodeFormStates(unittest.TestCase):
    def setUp(self):
        self.body = _fn_body(APP_JS, "linkClientChild")

    def test_21_double_submit_blocked(self):
        idx = self.body.find("btn.disabled = true")
        self.assertNotEqual(idx, -1)
        # Must happen before the await, not after.
        await_idx = self.body.find("await _apiPostRaw")
        self.assertLess(idx, await_idx)

    def test_22_loading_success_error_retry_states_present(self):
        self.assertIn("Проверяем код…", self.body)
        self.assertIn("Подключаем кабинет…", self.body)
        self.assertIn("кабинет подключён", self.body)
        self.assertIn("CLIENT_LINK_REASON_MESSAGES", self.body)
        self.assertIn("Временная ошибка сервера", self.body)

    def test_23_temporary_error_does_not_clear_code(self):
        catch_idx = self.body.find("} catch (e) {")
        self.assertNotEqual(catch_idx, -1)
        finally_idx = self.body.find("} finally {")
        self.assertNotEqual(finally_idx, -1)
        catch_block = self.body[catch_idx:finally_idx]
        self.assertNotIn("input.value", catch_block)
        # The else (application-level failure) branch must not clear it either.
        else_idx = self.body.find("} else {")
        self.assertNotEqual(else_idx, -1)
        else_block = self.body[else_idx:catch_idx]
        self.assertNotIn("input.value", else_block)

    def test_24_repeat_success_opens_existing_cabinet(self):
        ok_idx = self.body.find("if (data.ok) {")
        self.assertNotEqual(ok_idx, -1)
        else_idx = self.body.find("} else {", ok_idx)
        ok_block = self.body[ok_idx:else_idx]
        self.assertIn("already_linked", ok_block)
        self.assertIn("await loadMyChildren()", ok_block)
        # loadMyChildren() must be called unconditionally in the success
        # branch (not only for brand-new links) — same call whether this is
        # a first-time link or a safe replay.
        already_linked_idx = ok_block.find("already_linked")
        load_idx = ok_block.find("await loadMyChildren()")
        self.assertGreater(load_idx, already_linked_idx)

    def test_reason_code_messages_cover_required_states(self):
        for reason in (
            "code_not_found", "code_already_used", "code_invalidated",
            "code_expired", "telegram_already_linked", "invalid_code_format",
        ):
            self.assertIn(f"{reason}:", APP_JS)


class TestConnectionsDiagnosticsUI(unittest.TestCase):
    def setUp(self):
        self.idx = APP_JS.find("function _wsRenderConnectionsDiagnostics")
        self.assertNotEqual(self.idx, -1)
        self.section = APP_JS[self.idx:self.idx + 2000]

    def test_25_roles_restricted(self):
        self.assertIn("canManageOnboardingCampaigns()", self.section)
        self.assertIn("только owner, admin и client_manager", self.section)

    def test_26_no_invite_tokens_in_frontend_or_backend(self):
        self.assertNotIn("signature", self.section.lower())
        self.assertNotIn("token", self.section.lower())
        # Backend endpoints themselves must never select/return raw tokens.
        for fn_name in ("onboarding_connections_summary", "onboarding_connections_errors", "onboarding_launch_health"):
            fidx = WEB_APP_SERVER_PY.find(f"def {fn_name}(")
            self.assertNotEqual(fidx, -1)
            fbody = WEB_APP_SERVER_PY[fidx:fidx + 1800]
            self.assertNotIn("token_hash", fbody)
            self.assertNotIn("signature", fbody)

    def test_27_no_fixed_pixel_width_in_new_css(self):
        start = STYLES_CSS.find("/* v7.1.15 — launch-readiness")
        self.assertNotEqual(start, -1)
        end = STYLES_CSS.find("\n\n", start + 2000) if STYLES_CSS.find("\n\n", start + 2000) != -1 else start + 3000
        block = STYLES_CSS[start:end]
        self.assertNotRegex(block, r"(?<!-)width:\s*\d+px")

    def test_mode_toggle_has_third_diagnostics_mode(self):
        self.assertIn('data-oc-mode="diagnostics"', APP_JS)
        self.assertIn(">Подключения<", APP_JS)

    def test_endpoints_wired_to_role_gated_backend(self):
        self.assertIn('apiGet("/api/client/onboarding/connections/summary")', APP_JS)
        self.assertIn('apiGet("/api/client/onboarding/health")', APP_JS)
        self.assertIn('apiGet("/api/client/onboarding/connections/errors', APP_JS)
        for path in (
            "/api/client/onboarding/connections/summary",
            "/api/client/onboarding/connections/errors",
            "/api/client/onboarding/health",
        ):
            self.assertIn(path, WEB_APP_SERVER_PY)


if __name__ == "__main__":
    unittest.main()
