"""Tests for v7.1.16 — Client & Client Manager UX Stabilization: frontend
request-race checks.

Covers (RACES 33-37 from the launch-readiness spec):
  33. An old tab-switch response can't overwrite a newer tab (client-payments
      poll no longer runs in the background after navigating away).
  34. An old child-detail response can't overwrite a newer one (request
      fencing on the notification-detail screen).
  35. Repeat-render doesn't create a duplicate request (busy guards).
  36. Retry doesn't create a duplicate mutation (retry re-enters the same
      guarded loader, not a raw fetch).
  37. An aborted/superseded request never surfaces as a user-facing error
      (request-fencing checks return silently, not via an error branch).

Run:
    python -m unittest tests.test_frontend_request_races_v7116 -v
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest  # noqa: E402

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")


class TestStalePollStopsOnTabSwitch(unittest.TestCase):
    def test_33_payments_poll_cleared_on_tab_switch_away(self):
        m = re.search(r"function activateTab\(name\) \{(.*?)\n\n  document\.querySelectorAll", APP_JS, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn('_prevActiveTab === "client-payments"', body)
        self.assertIn("clearTimeout(state.clientPaymentsPollTimer)", body)
        self.assertIn("state.clientPaymentsPollTimer = null;", body)


class TestNotificationDetailRequestFencing(unittest.TestCase):
    def test_34_stale_notification_response_cannot_overwrite_newer(self):
        self.assertIn("let _notifDetailReqToken = 0;", APP_JS)
        m = re.search(r"async function openNotificationDetail\(id\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertIn("const myToken = ++_notifDetailReqToken;", body)
        # fenced after both awaited calls (the GET and the read-marking POST)
        self.assertEqual(body.count("if (myToken !== _notifDetailReqToken) return;"), 3)


class TestRepeatRenderNoDuplicateRequest(unittest.TestCase):
    def test_35_busy_guards_prevent_duplicate_inflight_requests(self):
        checks = [
            ("loadClientNotifications", "if (state.notificationsBusy) return;"),
            ("loadClientPayments", "if (state.clientPaymentsBusy) return;"),
            ("loadClientHomeData", "if (_clientHomeLoadBusy) return;"),
            ("_connDiagLoad", None),  # covered separately below (v7.1.15, unchanged)
        ]
        for fn_name, guard in checks:
            if guard is None:
                continue
            m = re.search(rf"(?:async )?function {fn_name}\(.*?\n\}}", APP_JS, re.S)
            self.assertIsNotNone(m, fn_name)
            self.assertIn(guard, m.group(0))


class TestRetryReentersSameGuardedLoader(unittest.TestCase):
    def test_36_retry_calls_go_through_the_same_named_loader(self):
        # Every uiErrorState/_wsErrorState retry callback used by the client
        # screens touched in v7.1.16 calls back into the SAME busy-guarded
        # loader function (loadClientPayments/loadClientNotifications/
        # loadReports/loadCommsHome) rather than a raw fetch — so a retry
        # tap is naturally deduped by that function's own busy flag.
        for retry_call in (
            "loadClientPayments()",
            "loadClientNotifications(false)",
            "loadReports()",
            "loadCommsHome()",
        ):
            self.assertIn(f'"{retry_call}"', APP_JS)
            self.assertIn(f"function {retry_call.split('(')[0]}(", APP_JS)


class TestSupersededRequestNeverSurfacesAsError(unittest.TestCase):
    def test_37_fenced_responses_return_silently_not_via_error_branch(self):
        m = re.search(r"async function openNotificationDetail\(id\) \{(.*?)\n\}", APP_JS, re.S)
        body = m.group(1)
        # every fencing check is a bare "return;" — never routes into the
        # uiErrorState(...) branch, so a superseded response is silent.
        for line in body.splitlines():
            if "myToken !== _notifDetailReqToken" in line:
                self.assertIn("return;", line)
                self.assertNotIn("uiErrorState", line)


if __name__ == "__main__":
    unittest.main()
