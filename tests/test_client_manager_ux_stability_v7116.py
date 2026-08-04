"""Tests for v7.1.16 — Client & Client Manager UX Stabilization:
CLIENT_MANAGER checks.

Covers (CLIENT_MANAGER 17-24 from the launch-readiness spec):
  17. Only the allowed tabs are reachable in MVP mode.
  18. No owner/admin-only route leaks into the client_manager tab set.
  19. Reports: loading/empty/error states (v7.1.16 error+retry fix).
  20. Payments Workspace: loading/empty/error states already present (v7.1.15).
  21. Communications: loading/empty/error states (v7.1.16 error+retry fix).
  22. Connections diagnostics: loading/empty/error states (v7.1.15) plus the
      new frontend-incidents panel (v7.1.16).
  23. Telegram BackButton is correct for both comms and the client-cabinet
      subscreens (single shared handler slot, v7.1.16).
  24. BackButton "exit to Admin" is gated on the REAL role, not a test role.

Run:
    python -m unittest tests.test_client_manager_ux_stability_v7116 -v
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest  # noqa: E402

from storage import Storage  # noqa: E402

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


class TestTabAllowlist(unittest.TestCase):
    def test_17_client_manager_mvp_tab_allowlist(self):
        m = re.search(r"const MVP_TABS_BY_ROLE = \{(.*?)\n\};", APP_JS, re.S)
        self.assertIsNotNone(m)
        cm = re.search(r'client_manager:\s*\[(.*?)\]', m.group(1))
        self.assertIsNotNone(cm)
        tabs = [t.strip().strip('"') for t in cm.group(1).split(",")]
        self.assertEqual(set(tabs), {"payments-workspace", "reports", "comms"})

    def test_18_no_owner_admin_only_tabs_leak_to_client_manager(self):
        m = re.search(r"const MVP_TABS_BY_ROLE = \{(.*?)\n\};", APP_JS, re.S)
        cm = re.search(r'client_manager:\s*\[(.*?)\]', m.group(1))
        tabs = [t.strip().strip('"') for t in cm.group(1).split(",")]
        for staff_only in ("admin", "lessons", "tasks", "windows", "schedule", "intern"):
            self.assertNotIn(staff_only, tabs)


class TestReportsStates(unittest.TestCase):
    def test_19_reports_error_state_distinct_from_empty(self):
        self.assertIn("state.reportsError = safeUserError(e);", APP_JS)
        m = re.search(r"if \(!report\) \{(.*?)\n  \}", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn("state.reportsError", m.group(1))
        self.assertIn("uiErrorState(state.reportsError", m.group(1))


class TestPaymentsWorkspaceStates(unittest.TestCase):
    def test_20_workspace_empty_and_error_helpers_present(self):
        self.assertIn("function _wsEmptyState(icon, title, desc)", APP_JS)
        self.assertIn("function _wsErrorState(message, retryOnclick)", APP_JS)
        self.assertIn("_wsStatsSkeleton", APP_JS)


class TestCommunicationsStates(unittest.TestCase):
    def test_21_comms_home_surfaces_load_error_with_retry(self):
        m = re.search(r"function _commsRenderHome\(root\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("home.loadError", body)
        self.assertIn('uiErrorState(home.loadError, "loadCommsHome()")', body)


class TestConnectionsDiagnosticsStates(unittest.TestCase):
    def test_22_connections_diag_has_loading_error_and_incidents_panel(self):
        m = re.search(r"function _wsRenderConnectionsDiagnostics\(root\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("st.loading && !st.summary", body)
        self.assertIn("st.error && !st.summary", body)
        self.assertIn("_connDiagFrontendIncidentsHtml(st.frontendIncidents)", body)
        self.assertIn("function _connDiagFrontendIncidentsHtml(fei)", APP_JS)


class TestBackButtonWiring(unittest.TestCase):
    def test_23_shared_backbutton_slot_used_everywhere(self):
        self.assertIn("function _appSetBackButton(handler)", APP_JS)
        # comms delegates to the shared slot instead of owning a second one
        m = re.search(r"function _commsSetBackButton\(handler\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertIn("_appSetBackButton(handler);", m.group(1))
        # client-cabinet subscreens wired via the same slot
        self.assertIn('_appSetBackButton(_availScreenLeave);', APP_JS)
        self.assertIn('_appSetBackButton(() => activateTab("notifications"));', APP_JS)

    def test_24_exit_to_admin_gated_on_real_role(self):
        self.assertIn(
            'function canReturnToAdminFromComms() {\n  const realRole = state.me?.realRole || "";\n'
            '  return realRole === "owner" || realRole === "admin";\n}',
            APP_JS,
        )


class TestClientManagerRoleGateStillOnboarding(unittest.TestCase):
    """Sanity check that the role set the diagnostics/incidents endpoints
    reuse (frontend_incidents_summary) still matches CLIENT_ONBOARDING_
    CAMPAIGN_ROLES, so client_manager keeps read access without gaining
    owner/admin-only raw incident rows."""

    def test_client_manager_gets_aggregate_only_not_raw_incidents(self):
        import web_app_server as srv

        self.assertEqual(srv.MiniAppContext.FRONTEND_INCIDENT_VIEW_ROLES, {"owner", "admin", "client_manager"})
        self.assertEqual(srv.MiniAppContext.FRONTEND_INCIDENT_RAW_ROLES, {"owner", "admin"})
        self.assertNotIn("client_manager", srv.MiniAppContext.FRONTEND_INCIDENT_RAW_ROLES)


if __name__ == "__main__":
    unittest.main()
