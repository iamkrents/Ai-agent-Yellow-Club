"""Tests for v7.1.11 — wiring the staff Payments onboarding UI.

The backend endpoint /api/client/admin/link-and-enroll (client_admin_link_and_enroll,
tested in tests/test_cl_onboarding_pilot_v7111.py) shipped with v7.1.11 but had no
real frontend caller. This closes that gap by adding a staff-only "enter a CL- code
now" action to the existing Payments Workspace "Подключение" tab (_wsRenderConnection
/ _wsConnDetailHtml, v7.1.7) — the one real staff-facing UI for CL-code client
management that owner/admin/client_manager can all reach (client_manager has no
access to the legacy admin-tab "Клиенты и родители" panel, which is why the
Connection tab and not that panel was the integration point).

No new screen, no new navigation entry, no backend changes. Parent self-service
(client_link_child / /api/client/children/link) is untouched and still creates no
pilot record — see test_cl_onboarding_pilot_v7111.py::TestGenericParentLinkNoAutomation.

Covers:
  1.  Staff onboarding action calls POST /api/client/admin/link-and-enroll
  2.  Parent UI still calls only /api/client/children/link (unchanged)
  3.  Role gate for the staff action matches backend PAYMENT_ONBOARDING_STAFF_ROLES
      (owner, admin, client_manager)
  4.  Roles outside that set (operations, teacher, parent, ...) are excluded
  5.  review/auto/observe/disabled render the spec-mandated text
  6.  automation_status=failed never shows a false success message
  7.  already_linked / retry is not special-cased as an error — same success path
  8.  canShowMkPostButton is role-correct for client_manager (unchanged v7.1.11
      behavior) and now also excludes the "ambiguous" active-claim state, mirroring
      the backend's no_active_claim guard (claiming AND ambiguous)
  9.  owner/admin MK-post + onboarding behavior unchanged by this change

Static analysis only (reads source as text), matching this project's existing
test convention (see test_payments_connection_v717.py, test_admin_client_links.py).

Run:
    python -m unittest tests.test_staff_payment_onboarding_ui_v7111 -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "miniapp" / "app.js"
SERVER_PY = ROOT / "web_app_server.py"

_js_cache: str | None = None
_server_cache: str | None = None


def _js() -> str:
    global _js_cache
    if _js_cache is None:
        _js_cache = APP_JS.read_text(encoding="utf-8")
    return _js_cache


def _server() -> str:
    global _server_cache
    if _server_cache is None:
        _server_cache = SERVER_PY.read_text(encoding="utf-8")
    return _server_cache


def _js_fn(name: str, *, is_async: bool = False, window: int = 6000) -> str:
    js = _js()
    needle = f"{'async ' if is_async else ''}function {name}("
    start = js.find(needle)
    assert start != -1, f"{needle} not found in app.js"
    end = js.find("\nasync function ", start + 1)
    end2 = js.find("\nfunction ", start + 1)
    candidates = [e for e in (end, end2) if e != -1]
    end = min(candidates) if candidates else start + window
    return js[start:end]


# ---------------------------------------------------------------------------
# 1-2: which endpoint each surface calls
# ---------------------------------------------------------------------------

class TestEndpointWiring(unittest.TestCase):
    def test_01_staff_action_calls_link_and_enroll(self):
        fn = _js_fn("_wsConnSubmitOnboard", is_async=True)
        self.assertIn("/api/client/admin/link-and-enroll", fn)

    def test_02_parent_ui_still_calls_children_link_only(self):
        fn = _js_fn("linkClientChild", is_async=True)
        self.assertIn("/api/client/children/link", fn)
        # linkClientChild's own header comment documents (for readers) that
        # staff onboarding is a *separate* endpoint/function — that mention
        # is expected. What must never appear is an actual call to it.
        self.assertNotIn('_apiPostRaw("/api/client/admin/link-and-enroll"', fn)
        self.assertNotIn('fetch("/api/client/admin/link-and-enroll"', fn)

    def test_no_frontend_staff_source_flag_sent(self):
        # Section 2 of the spec: the frontend must never send a flag that
        # tells the backend "this is a staff call" — the role check happens
        # server-side only, derived from auth. The POST payload must contain
        # exactly parent_telegram_user_id + code, nothing role/source related.
        fn = _js_fn("_wsConnSubmitOnboard", is_async=True)
        idx = fn.find("/api/client/admin/link-and-enroll")
        self.assertNotEqual(idx, -1)
        payload_segment = fn[idx:fn.find(");", idx)]
        self.assertIn("parent_telegram_user_id", payload_segment)
        self.assertIn("code,", payload_segment)
        self.assertNotIn("role", payload_segment)
        self.assertNotIn("source", payload_segment)
        self.assertNotIn("staff", payload_segment)


# ---------------------------------------------------------------------------
# 3-4: role gating
# ---------------------------------------------------------------------------

class TestRoleGate(unittest.TestCase):
    def test_03_onboarding_roles_match_backend(self):
        js = _js()
        idx = js.find("const PAYMENT_ONBOARDING_ROLES = ")
        self.assertNotEqual(idx, -1)
        line_end = js.find("\n", idx)
        line = js[idx:line_end]
        for role in ('"owner"', '"admin"', '"client_manager"'):
            self.assertIn(role, line)

        server = _server()
        idx2 = server.find("PAYMENT_ONBOARDING_STAFF_ROLES = ")
        self.assertNotEqual(idx2, -1)
        line_end2 = server.find("\n", idx2)
        backend_line = server[idx2:line_end2]
        for role in ('"owner"', '"admin"', '"client_manager"'):
            self.assertIn(role, backend_line)

    def test_04_excluded_roles_not_in_onboarding_array(self):
        js = _js()
        idx = js.find("const PAYMENT_ONBOARDING_ROLES = ")
        line_end = js.find("\n", idx)
        line = js[idx:line_end]
        for role in ('"operations"', '"teacher"', '"parent"', '"methodist"', '"intern"'):
            self.assertNotIn(role, line)

    def test_action_rendered_only_behind_role_check(self):
        fn = _js_fn("_wsConnOnboardHtml")
        self.assertIn("canUseStaffOnboarding()", fn)
        # First statement must be the gate (bail out before building any HTML).
        gate_idx = fn.find("canUseStaffOnboarding()")
        return_idx = fn.find("return \"\"")
        self.assertNotEqual(return_idx, -1)
        self.assertLess(gate_idx, fn.find("<article", gate_idx))

    def test_role_helper_reads_state_me_role(self):
        js = _js()
        idx = js.find("function canUseStaffOnboarding()")
        self.assertNotEqual(idx, -1)
        segment = js[idx:idx + 200]
        self.assertIn("state.me?.role", segment)
        self.assertIn("PAYMENT_ONBOARDING_ROLES.includes", segment)


# ---------------------------------------------------------------------------
# 5-6: response-driven messages (new/existing pilot modes, failure)
# ---------------------------------------------------------------------------

class TestResponseMessages(unittest.TestCase):
    def test_05_review_mode_text(self):
        fn = _js_fn("_wsConnOnboardResultHtml")
        self.assertIn("Клиент добавлен в автоматизацию", fn)
        js = _js()
        idx = js.find("WS_PAYMENT_ONBOARDING_MODE_LABELS = {")
        end = js.find("};", idx)
        mapping = js[idx:end]
        self.assertIn("review:", mapping)
        self.assertIn("Режим: с подтверждением менеджера", mapping)

    def test_05_auto_mode_text(self):
        js = _js()
        idx = js.find("WS_PAYMENT_ONBOARDING_MODE_LABELS = {")
        end = js.find("};", idx)
        mapping = js[idx:end]
        self.assertIn("auto:", mapping)
        self.assertIn("Режим: автоматически", mapping)

    def test_05_observe_mode_text(self):
        js = _js()
        idx = js.find("WS_PAYMENT_ONBOARDING_MODE_LABELS = {")
        end = js.find("};", idx)
        mapping = js[idx:end]
        self.assertIn("observe:", mapping)
        self.assertIn("Режим: наблюдение", mapping)

    def test_05_disabled_mode_text(self):
        fn = _js_fn("_wsConnOnboardResultHtml")
        idx = fn.find('pa.mode === "disabled"')
        self.assertNotEqual(idx, -1)
        segment = fn[idx:idx + 300]
        self.assertIn("Клиент привязан", segment)
        self.assertIn("Автоматизация оплат отключена", segment)

    def test_06_failed_automation_status_text_and_no_false_success(self):
        fn = _js_fn("_wsConnOnboardResultHtml")
        idx = fn.find('pa.automation_status === "failed"')
        self.assertNotEqual(idx, -1)
        segment = fn[idx:idx + 400]
        self.assertIn("Клиент добавлен, но автоматизацию оплат подключить не удалось", segment)
        self.assertIn("Повторите подключение или обратитесь к администратору", segment)
        # The failed-automation branch must come before the generic
        # success/mode branch below it, so a failed automation_status can
        # never fall through to the "Клиент добавлен в автоматизацию" text.
        success_idx = fn.find("Клиент добавлен в автоматизацию")
        self.assertNotEqual(success_idx, -1)
        self.assertLess(idx, success_idx)

    def test_frontend_does_not_guess_mode(self):
        # The spec explicitly forbids inferring/guessing mode client-side —
        # every label must be read from the actual response field pa.mode.
        fn = _js_fn("_wsConnOnboardResultHtml")
        self.assertIn("pa.mode", fn)
        self.assertIn("res.payment_automation", fn)

    def test_error_result_uses_backend_reason_code_mapping(self):
        fn = _js_fn("_wsConnOnboardResultHtml")
        idx = fn.find("if (!res.ok)")
        self.assertNotEqual(idx, -1)
        segment = fn[idx:idx + 200]
        self.assertIn("_wsConnErrorMessage(res)", segment)


# ---------------------------------------------------------------------------
# 7: already_linked / retry behavior
# ---------------------------------------------------------------------------

class TestRetry(unittest.TestCase):
    def test_07_already_linked_not_treated_as_special_error(self):
        fn = _js_fn("_wsConnOnboardResultHtml")
        self.assertNotIn("already_linked", fn)  # success path is driven by res.ok / payment_automation only

    def test_07_submit_never_permanently_disables_retry(self):
        fn = _js_fn("_wsConnSubmitOnboard", is_async=True)
        # busy flag must be reset to false on both the success and the
        # catch path, otherwise a failed/failed-automation attempt would
        # permanently lock the button and block the required retry.
        self.assertEqual(fn.count("_wsConnState.onboardBusy = false;"), 2)

    def test_07_resubmitting_same_code_is_possible(self):
        fn = _js_fn("_wsConnSubmitOnboard", is_async=True)
        # Fields are not force-cleared after submit, so the same code/parent
        # id can be resubmitted with a single click to retry a failed pilot
        # enrollment against the now-already_linked code.
        self.assertNotIn('_wsConnState.onboardCode = ""', fn)
        self.assertNotIn('_wsConnState.onboardParentId = ""', fn)


# ---------------------------------------------------------------------------
# 8-9: MK-post button — client_manager parity + ambiguous-state fix
# ---------------------------------------------------------------------------

class TestMkPostButtonGate(unittest.TestCase):
    def test_08_client_manager_included_in_mk_post_roles(self):
        js = _js()
        idx = js.find("const MK_POST_ROLES = ")
        line_end = js.find("\n", idx)
        line = js[idx:line_end]
        for role in ('"owner"', '"admin"', '"client_manager"'):
            self.assertIn(role, line)

    def test_08_button_excludes_claiming_and_ambiguous(self):
        fn = _js_fn("canShowMkPostButton")
        idx = fn.find("notInProgress")
        self.assertNotEqual(idx, -1)
        line = fn[idx:fn.find("\n", idx)]
        self.assertIn('"claiming"', line)
        self.assertIn('"ambiguous"', line)

    def test_08_mirrors_backend_no_active_claim_guard(self):
        server = _server()
        idx = server.find('"no_active_claim"')
        self.assertNotEqual(idx, -1)
        segment = server[idx:idx + 200]
        self.assertIn('"claiming"', segment)
        self.assertIn('"ambiguous"', segment)

    def test_09_owner_admin_still_included_unchanged(self):
        fn = _js_fn("canShowMkPostButton")
        self.assertIn("MK_POST_ROLES.includes(role)", fn)
        js = _js()
        idx = js.find("const MK_POST_ROLES = ")
        line_end = js.find("\n", idx)
        line = js[idx:line_end]
        self.assertIn('"owner"', line)
        self.assertIn('"admin"', line)

    def test_09_other_mk_post_conditions_unchanged(self):
        fn = _js_fn("canShowMkPostButton")
        self.assertIn('pi.status === "paid"', fn)
        self.assertIn('clientVis !== "withdrawn"', fn)
        self.assertIn('pi.status !== "cancelled"', fn)
        self.assertIn('pi.status !== "ignored"', fn)
        self.assertIn("mk_invoice_id", fn)
        self.assertIn("mk_user_id", fn)


if __name__ == "__main__":
    unittest.main()
