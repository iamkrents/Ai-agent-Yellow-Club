"""Tests for v7.1.12 — mass onboarding campaign frontend UI wiring.

Covers the UI/API portion of the v7.1.12 test checklist:
  - mode toggle exists inside the existing Connection tab, single flow's own
    markup/behavior is untouched (regression against test_payments_connection_v717.py)
  - mass flow is visible/reachable only behind the role gate
  - human-readable status labels (continuation + campaign status)
  - CSV export link wiring
  - endpoints called match the backend routes
  - version/cache-bust bumped to v7.1.12
  - no dev-preview markers introduced

Static analysis only (reads source as text), matching this project's existing
test convention (see test_payments_connection_v717.py, test_staff_payment_onboarding_ui_v7111.py).

Run:
    python -m unittest tests.test_onboarding_campaign_ui_v7112 -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
SERVER_PY = ROOT / "web_app_server.py"

_js_cache: str | None = None
_html_cache: str | None = None
_server_cache: str | None = None


def _js() -> str:
    global _js_cache
    if _js_cache is None:
        _js_cache = APP_JS.read_text(encoding="utf-8")
    return _js_cache


def _html() -> str:
    global _html_cache
    if _html_cache is None:
        _html_cache = INDEX_HTML.read_text(encoding="utf-8")
    return _html_cache


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


class TestModeToggle(unittest.TestCase):
    def test_toggle_present_in_connection_render(self):
        fn = _js_fn("_wsRenderConnection")
        self.assertIn("_wsOcModeToggleHtml", fn)
        self.assertIn('data-oc-mode="single"', _js_fn("_wsOcModeToggleHtml"))
        self.assertIn('data-oc-mode="mass"', _js_fn("_wsOcModeToggleHtml"))

    def test_single_flow_markup_untouched(self):
        # Regression: the exact same literal markup/behavior asserted by
        # test_payments_connection_v717.py must still be present verbatim
        # inside _wsRenderConnection after the mode-toggle wrapper was added.
        fn = _js_fn("_wsRenderConnection")
        self.assertIn('id="wsConnSearchInput"', fn)
        self.assertIn("_wsConnSearch()", fn)
        self.assertIn('id="wsConnBody"', fn)

    def test_mass_mode_delegates_and_returns_early(self):
        fn = _js_fn("_wsRenderConnection")
        idx = fn.find('_ocState.mode === "mass"')
        self.assertNotEqual(idx, -1)
        segment = fn[idx:idx + 150]
        self.assertIn("_wsRenderCampaignsRoot", segment)
        self.assertIn("return", segment)

    def test_mode_switch_triggers_full_tab_rerender(self):
        fn = _js_fn("_wsOcWireModeToggle")
        self.assertIn("_wsRenderCurrentTab()", fn)


class TestRoleGate(unittest.TestCase):
    def test_frontend_roles_match_backend(self):
        js = _js()
        idx = js.find("const ONBOARDING_CAMPAIGN_ROLES = ")
        self.assertNotEqual(idx, -1)
        line = js[idx:js.find("\n", idx)]
        for role in ('"owner"', '"admin"', '"client_manager"'):
            self.assertIn(role, line)
        server = _server()
        idx2 = server.find("CLIENT_ONBOARDING_CAMPAIGN_ROLES = ")
        self.assertNotEqual(idx2, -1)
        backend_line = server[idx2:server.find("\n", idx2)]
        for role in ('"owner"', '"admin"', '"client_manager"'):
            self.assertIn(role, backend_line)

    def test_mass_mode_gated_by_role(self):
        fn = _js_fn("_wsRenderCampaignsRoot")
        self.assertIn("canManageOnboardingCampaigns()", fn)

    def test_other_roles_excluded(self):
        js = _js()
        idx = js.find("const ONBOARDING_CAMPAIGN_ROLES = ")
        line = js[idx:js.find("\n", idx)]
        for role in ('"operations"', '"teacher"', '"methodist"', '"intern"', '"kitchen"', '"restaurant"', '"parent"'):
            self.assertNotIn(role, line)


class TestHumanLabels(unittest.TestCase):
    def test_continuation_labels_present(self):
        js = _js()
        idx = js.find("const ONBOARDING_CONTINUATION_LABELS = {")
        end = js.find("};", idx)
        mapping = js[idx:end]
        for label in ("Не уточнено", "Продолжает обучение", "Пока не решили", "Нужна консультация", "Не продолжает"):
            self.assertIn(label, mapping)

    def test_campaign_status_labels_present(self):
        js = _js()
        idx = js.find("const ONBOARDING_CAMPAIGN_STATUS_LABELS = {")
        end = js.find("};", idx)
        mapping = js[idx:end]
        for label in ("Черновик", "Активна", "Завершена", "В архиве"):
            self.assertIn(label, mapping)

    def test_csv_header_labels_match_spec(self):
        server = _server()
        idx = server.find("_ONBOARDING_CSV_HEADER = [")
        end = server.find("]", idx)
        header = server[idx:end]
        for col in ("Ребёнок", "MoyKlass ID", "Филиал", "Группа/курс", "Родитель",
                    "Статус продолжения", "Статус подключения", "Статус приглашения",
                    "Срок действия", "Персональная ссылка"):
            self.assertIn(col, header)


class TestEndpointsWired(unittest.TestCase):
    def test_create_campaign_endpoint(self):
        fn = _js_fn("_wsOcSubmitCreateCampaign", is_async=True)
        self.assertIn("/api/client/onboarding/campaigns", fn)

    def test_import_endpoint(self):
        # v7.1.12.1 hotfix split the single _wsOcImportSelected send into
        # _wsOcImportAddClicked (threshold check) -> _wsOcImportDoSend (the
        # actual POST) — see test_onboarding_mass_select_hotfix_v71121.py
        # for the dedicated coverage of that split.
        fn = _js_fn("_wsOcImportDoSend", is_async=True)
        self.assertIn("recipients/import", fn)

    def test_continuation_status_endpoint(self):
        fn = _js_fn("_wsOcBulkContinuation", is_async=True)
        self.assertIn("continuation-status", fn)
        fn2 = _js_fn("_wsOcSingleContinuation", is_async=True)
        self.assertIn("continuation-status", fn2)

    def test_invite_create_revoke_regenerate_endpoints(self):
        self.assertIn("/invites", _js_fn("_wsOcCreateInvite", is_async=True))
        self.assertIn("/revoke", _js_fn("_wsOcRevokeInvite", is_async=True))
        self.assertIn("/regenerate", _js_fn("_wsOcRegenerateInvite", is_async=True))

    def test_csv_export_url(self):
        fn = _js_fn("_wsOcExportCsvUrl")
        self.assertIn("export.csv", fn)

    def test_candidates_search_endpoint(self):
        fn = _js_fn("_wsOcSearchCandidates", is_async=True)
        self.assertIn("/api/client/onboarding/candidates", fn)

    def test_backend_routes_exist_for_every_frontend_call(self):
        server = _server()
        for route_fragment in (
            '"/api/client/onboarding/campaigns"', "/api/client/onboarding/campaigns/",
            "/api/client/onboarding/invites/", "/api/client/onboarding/candidates",
        ):
            self.assertIn(route_fragment, server)


class TestIdempotentCreate(unittest.TestCase):
    def test_idempotency_key_generated_per_modal_open(self):
        fn = _js_fn("_wsOcOpenCreateModal")
        self.assertIn("_ocCreateIdempotencyKey", fn)

    def test_idempotency_key_sent_in_payload(self):
        fn = _js_fn("_wsOcSubmitCreateCampaign", is_async=True)
        self.assertIn("idempotency_key: _ocCreateIdempotencyKey", fn)


class TestNoPreviewMarkers(unittest.TestCase):
    def test_no_dev_preview_markers(self):
        for marker in ("dev_preview", "LOCAL PREVIEW", "__YC_DEV_PREVIEW__", "mock", "Preview Client Manager"):
            self.assertNotIn(marker, _js_fn("_wsRenderCampaignsRoot"))
            self.assertNotIn(marker, _js_fn("_wsOcOpenCreateModal"))

    def test_no_stray_fetch_override(self):
        self.assertEqual(_js().count("window.fetch ="), 0)


class TestVersionCacheBust(unittest.TestCase):
    def test_app_js_version_marker(self):
        # v7.1.12.3 bumped the marker; see test_onboarding_mass_select_hotfix_v71121.py
        # for the dedicated version/cache-bust assertions of that earlier hotfix.
        self.assertIn('console.log("MiniApp version: v7.1.16.1");', _js())

    def test_index_html_cache_bust(self):
        html = _html()
        self.assertIn("styles.css?v=7.1.16.1", html)
        self.assertIn("app.js?v=7.1.16.1", html)
        self.assertNotIn("v=7.1.11", html)


if __name__ == "__main__":
    unittest.main()
