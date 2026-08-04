"""Static-analysis tests for v7.1.9 — Diagnostics tab UI (Payments Workspace)
and the dev_preview=payment-diagnostics preview harness.

Static text/AST-style checks only (reads app.js/index.html/styles.css as
text) — consistent with this repo's existing frontend test convention.
No browser, no real fetch. Run offline:
    python -m unittest tests.test_payment_diagnostics_ui -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
STYLES_CSS = (ROOT / "miniapp" / "styles.css").read_text(encoding="utf-8")


class TestNewInternalTab(unittest.TestCase):
    def test_01_diagnostics_tab_added_to_skeleton(self):
        m = re.search(r"function _renderWorkspaceSkeleton\b.*?const tabs = \[(.*?)\];", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn('"diagnostics"', m.group(1))
        self.assertIn("Диагностика", m.group(1))

    def test_02_diagnostics_tab_gated_by_capability(self):
        m = re.search(r"function _renderWorkspaceSkeleton\b.*?const tabs = \[(.*?)\];", APP_JS, re.S)
        block = m.group(1)
        tab_line = [l for l in block.splitlines() if "diagnostics" in l][0]
        self.assertIn("canViewPaymentDiagnostics", tab_line)

    def test_03_five_existing_tabs_unchanged(self):
        m = re.search(r"function _renderWorkspaceSkeleton\b.*?const tabs = \[(.*?)\];", APP_JS, re.S)
        block = m.group(1)
        for tab_id in ("overview", "attention", "all-payments", "pilot-clients", "connection"):
            self.assertIn(tab_id, block)

    def test_04_no_new_global_bottom_tab(self):
        # The global bottom nav tab list lives in index.html's <nav id="tabs">-
        # style buttons; diagnostics must NOT appear there, only as a ws-subtab.
        nav_section = INDEX_HTML[:INDEX_HTML.find("piModalRoot")]
        self.assertNotIn('data-tab="diagnostics"', nav_section)

    def test_05_dispatch_wired_in_render_current_tab(self):
        m = re.search(r"function _wsRenderCurrentTab\(\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn('"diagnostics"', m.group(1))
        self.assertIn("_wsRenderDiagnostics", m.group(1))
        self.assertIn("_loadWorkspaceDiagnostics", m.group(1))

    def test_37_internal_tab_uses_ws_activate_tab_helper(self):
        # Diagnostics must open via the same production helper as every other
        # internal Payments Workspace tab (_wsActivateTab), never a top-level
        # activateTab(...) call reserved for global nav tabs.
        m = re.search(r"function _renderWorkspaceSkeleton\(root\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn("_wsActivateTab('${escapeHtml(t.id)}')", m.group(1))

    def test_38_ws_activate_tab_sets_active_state_on_subtab(self):
        m = re.search(r"function _wsActivateTab\(tabId\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn(".ws-subtab", body)
        self.assertIn('classList.toggle("active"', body)


# Canonical global bottom-nav tab order, unchanged since v7.1.8. Diagnostics
# must never appear in this list — it is a Payments Workspace internal
# ws-subtab only (see TestNewInternalTab above).
GLOBAL_NAV_TAB_ORDER = [
    "intern", "lessons", "reports", "windows", "schedule", "tasks",
    # v7.1.13 — client cabinet shell: "home"/"notifications"/"more" are new,
    # "my-children"/"food"/"client-payments" unchanged, "profile" is new
    # (inserted after "help"). See the v7.1.13 final report.
    "home", "my-children", "food", "client-payments", "notifications", "more",
    "kitchen", "kitchen-editor",
    "restaurant", "my-lunch", "help", "profile",
    # v7.1.13 round 2 — never visible nav items, exist only so
    # activateTab("availability"/"notification-detail") has a matching
    # .tab[data-tab] element (see index.html comments at those buttons).
    "availability", "notification-detail",
    "ask", "payments-workspace",
    # v7.1.14 — staff "Рассылки" (communications center) bottom-nav tab.
    "comms",
    "admin",
]


class TestGlobalNavRegression(unittest.TestCase):
    """v7.1.9 visual review flagged a gear-like icon next to "Платежи" in the
    global bottom nav and asked to confirm the global nav is untouched. These
    tests pin the global nav to its exact v7.1.8 shape."""

    def _nav_section(self):
        return INDEX_HTML[:INDEX_HTML.find("piModalRoot")]

    def test_39_diagnostics_absent_from_global_bottom_nav(self):
        self.assertNotIn('data-tab="diagnostics"', self._nav_section())

    def test_40_global_nav_count_and_order_matches_v718(self):
        found = re.findall(r'data-tab="([a-z0-9-]+)"', self._nav_section())
        self.assertEqual(found, GLOBAL_NAV_TAB_ORDER)

    def test_41_admin_tab_follows_payments_workspace_via_comms_unchanged(self):
        # Pre-existing v7.1.8 adjacency, updated for the v7.1.14 "Рассылки"
        # tab (comms) that now sits between them: confirms nothing else new
        # was inserted between "Платежи" and "Админ".
        nav_section = self._nav_section()
        idx = nav_section.find('data-tab="payments-workspace"')
        self.assertGreater(idx, -1)
        next_tab = re.search(r'data-tab="([a-z0-9-]+)"', nav_section[idx + 1:])
        self.assertIsNotNone(next_tab)
        self.assertEqual(next_tab.group(1), "comms")
        after_comms = re.search(r'data-tab="([a-z0-9-]+)"', nav_section[idx + 1 + next_tab.end():])
        self.assertIsNotNone(after_comms)
        self.assertEqual(after_comms.group(1), "admin")

    def test_42_food_reports_tasks_help_chat_tabs_unchanged(self):
        nav_section = self._nav_section()
        for tab_id, label in (
            ("food", "Питание"), ("reports", "Отчёты"), ("tasks", "Задачи"),
            ("help", "Помощь"), ("ask", "Чат"),
        ):
            block_start = nav_section.find(f'data-tab="{tab_id}"')
            self.assertGreater(block_start, -1, f"tab {tab_id} missing")
            self.assertIn(label, nav_section[block_start:block_start + 200])


class TestLoaderAndRenderer(unittest.TestCase):
    def test_06_loader_calls_diagnostics_endpoint(self):
        m = re.search(r"async function _loadWorkspaceDiagnostics\(\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn("/api/payments/diagnostics", m.group(1))

    def test_07_renderer_shows_skeleton_before_data(self):
        m = re.search(r"function _wsRenderDiagnostics\(root\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn("_wsDiagSkeleton", m.group(1))

    def test_08_renderer_uses_status_meta_map(self):
        self.assertIn("WS_DIAG_STATUS_META", APP_JS)
        for key in ("no_data", "healthy", "warning", "critical"):
            self.assertIn(f"{key}:", APP_JS[APP_JS.find("WS_DIAG_STATUS_META"):APP_JS.find("WS_DIAG_STATUS_META") + 700])

    def test_09_run_button_gated_by_can_manage(self):
        m = re.search(r"function _wsRenderDiagnostics\(root\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIn("canManage", m.group(1))
        self.assertIn("wsDiagRunBtn", m.group(1))

    def test_10_incident_card_shows_required_fields(self):
        m = re.search(r"function _wsDiagIncidentCard\(inc, canManage\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        for field in ("inc.title", "inc.message", "inc.first_seen_at", "inc.last_seen_at", "inc.occurrence_count"):
            self.assertIn(field, body)

    def test_11_recheck_button_gated_by_can_manage(self):
        m = re.search(r"function _wsDiagIncidentCard\(inc, canManage\) \{(.*?)\n\}", APP_JS, re.S)
        body = m.group(1)
        recheck_line = [l for l in body.splitlines() if "recheckBtn" in l and "canManage" in l]
        self.assertTrue(recheck_line)

    def test_12_open_intent_button_only_when_public_id_present(self):
        m = re.search(r"function _wsDiagIncidentCard\(inc, canManage\) \{(.*?)\n\}", APP_JS, re.S)
        body = m.group(1)
        self.assertIn("inc.intent_public_id", body)

    def test_13_no_raw_provider_code_or_payload_rendered(self):
        for fn_name in ("_wsRenderDiagnostics", "_wsDiagIncidentCard"):
            start = APP_JS.find(f"function {fn_name}(")
            end = APP_JS.find("\nfunction ", start + 1)
            body = APP_JS[start:end]
            self.assertNotIn("payload_json", body)
            self.assertNotIn(".traceback", body.lower())
            self.assertNotIn("raw_", body.lower())

    def test_14_empty_state_when_no_incidents(self):
        m = re.search(r"function _wsRenderDiagnostics\(root\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIn("incidents.length", m.group(1))
        self.assertIn("Открытых проблем нет", m.group(1))

    def test_43_empty_state_before_first_run_does_not_claim_no_issues(self):
        # Scenario 9-empty-no-data: guardian never ran -> we cannot know
        # whether there are open problems. Must not show "Открытых проблем
        # нет" for the no_data status; must show a distinct "not yet
        # checked" copy instead.
        m = re.search(r"function _wsRenderDiagnostics\(root\) \{(.*?)\n\}", APP_JS, re.S)
        body = m.group(1)
        self.assertIn('g.status === "no_data"', body)
        no_data_branch = body[body.find('g.status === "no_data"'):body.find("} else if (!incidents.length)")]
        self.assertIn("Состояние ещё не проверялось", no_data_branch)
        self.assertIn("Запустите диагностику, чтобы проверить автоматизацию и получить актуальное состояние.", no_data_branch)
        self.assertNotIn("Открытых проблем нет", no_data_branch)

    def test_44_empty_state_after_completed_run_shows_no_issues(self):
        # Once a real health run has completed (status != no_data) with zero
        # incidents, "Открытых проблем нет" is the correct, already-approved copy.
        m = re.search(r"function _wsRenderDiagnostics\(root\) \{(.*?)\n\}", APP_JS, re.S)
        body = m.group(1)
        healthy_branch = body[body.find("} else if (!incidents.length)"):body.find("} else {")]
        self.assertIn("Открытых проблем нет", healthy_branch)

    def test_45_next_check_label_is_not_next_expected(self):
        m = re.search(r"function _wsRenderDiagnostics\(root\) \{(.*?)\n\}", APP_JS, re.S)
        body = m.group(1)
        self.assertIn("Следующая проверка", body)
        self.assertNotIn("Следующая ожидается", body)

    def test_46_critical_count_label_is_genitive_plural(self):
        m = re.search(r"function _wsRenderDiagnostics\(root\) \{(.*?)\n\}", APP_JS, re.S)
        body = m.group(1)
        self.assertIn("Критических:", body)
        self.assertNotIn("Критично:", body)


class TestActions(unittest.TestCase):
    def test_15_run_now_posts_to_run_endpoint(self):
        m = re.search(r"async function _wsDiagnosticsRunNow\(btn\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn("/api/payments/diagnostics/run", m.group(1))

    def test_16_run_now_handles_conflict(self):
        m = re.search(r"async function _wsDiagnosticsRunNow\(btn\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIn("conflict", m.group(1))

    def test_17_recheck_posts_to_recheck_endpoint(self):
        m = re.search(r"async function _wsDiagnosticsRecheck\(incidentId, btn\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn("/recheck", m.group(1))

    def test_18_open_intent_switches_to_all_payments_tab(self):
        m = re.search(r"function _wsDiagnosticsOpenIntent\(publicId\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn('_wsActivateTab("all-payments")', m.group(1))


class TestCssMobile(unittest.TestCase):
    def test_19_diag_summary_css_present(self):
        self.assertIn(".ws-diag-summary", STYLES_CSS)

    def test_20_diag_status_tone_classes_present(self):
        for cls in ("--neutral", "--healthy", "--warning", "--critical"):
            self.assertIn(f".ws-diag-status{cls}", STYLES_CSS)

    def test_21_diag_severity_classes_present(self):
        for cls in ("--critical", "--warning", "--info"):
            self.assertIn(f".ws-diag-severity{cls}", STYLES_CSS)

    def test_22_mobile_media_query_stacks_summary_top(self):
        self.assertIn("@media (max-width: 390px)", STYLES_CSS)
        idx = STYLES_CSS.find(".ws-diag-summary__top { flex-direction: column")
        self.assertGreater(idx, -1)

    def test_23_recheck_button_touch_target(self):
        self.assertIn("[data-ws-diag-recheck]", STYLES_CSS)
        idx = STYLES_CSS.find("[data-ws-diag-recheck]")
        self.assertIn("min-height: 44px", STYLES_CSS[idx:idx + 60])

    def test_24_grid_no_horizontal_overflow_class(self):
        self.assertIn(".ws-diag-summary__grid", STYLES_CSS)
        self.assertIn("grid-template-columns", STYLES_CSS[STYLES_CSS.find(".ws-diag-summary__grid"):])


class TestVersionMarker(unittest.TestCase):
    def test_25_cache_bust_is_v7110(self):
        self.assertIn("app.js?v=7.1.16", INDEX_HTML)
        self.assertIn("styles.css?v=7.1.16", INDEX_HTML)
        self.assertIn('console.log("MiniApp version: v7.1.16")', APP_JS)


class TestPreviewRemoved(unittest.TestCase):
    """The temporary localhost-only payment-diagnostics preview
    (dev_preview=payment-diagnostics) was visually approved (all 9
    scenarios, mobile layout, texts, permissions, global navigation) and
    has now been fully removed. These replace the old TestPreviewHarness
    class, which asserted the preview's presence — now we assert its
    absence, and that production boot()/navigation/the real Diagnostics
    tab are untouched (covered by the other test classes in this file:
    TestNewInternalTab, TestLoaderAndRenderer, TestGlobalNavRegression, etc.).
    """

    def test_26_no_preview_markers_in_index_html(self):
        for marker in (
            "dev_preview", "LOCAL PREVIEW", "payment-diagnostics", "blocked_in_preview",
            "__YC_DEV_PREVIEW__", "Preview Operations", "PREVIEW_ME",
            "_wsPreviewAssert", "_wsPreviewFail", "diagPreviewSwitcher",
            "TEMPORARY",
        ):
            self.assertNotIn(marker, INDEX_HTML, f"leftover preview marker: {marker}")

    def test_27_no_preview_markers_in_app_js(self):
        for marker in ("dev_preview", "payment-diagnostics", "__YC_DEV_PREVIEW__", "blocked_in_preview"):
            self.assertNotIn(marker, APP_JS, f"leftover preview marker: {marker}")

    def test_28_script_tail_is_plain_production_form(self):
        tail = INDEX_HTML[INDEX_HTML.rfind("<script"):]
        self.assertIn('src="/static/app.js?v=7.1.16"', tail)
        self.assertNotIn("(function ()", tail)

    def test_29_production_boot_and_diagnostics_endpoint_still_wired(self):
        # The preview's fetch/DOM mocks are gone, but the real production
        # calls they were mocking must still be present and unmocked.
        self.assertIn('apiGet("/api/payments/diagnostics")', APP_JS)
        self.assertIn("_wsActivateTab", APP_JS)

    def test_30_nine_preview_scenarios_removed_with_harness(self):
        for key in (
            "1-healthy", "2-warning-moyklass-timeout", "3-critical-paid-not-posted",
            "4-training-pause", "5-training-resume-required", "6-heartbeat-missing",
            "7-overlap-skipped", "8-recovered-incident", "9-empty-no-data",
        ):
            self.assertNotIn(f'"{key}"', INDEX_HTML)

    def test_31_other_previews_stay_removed(self):
        # v7.1.9 does not reintroduce the retired v7.1.8 previews either.
        self.assertNotIn('"training-pause"', INDEX_HTML)
        self.assertNotIn('"payment-method"', INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
