"""Tests for v7.1.16.1 — Payment Period Filters: regression guard.

Proves the period-filter addition didn't disturb anything payments-adjacent
that v7.0.9x-v7.1.16 already shipped — this release is explicitly scoped as
read-only relative to payment business logic (no automation, no Guardian,
no MoyKlass write/read semantics, no status transitions changed).

Covers (REGRESSION 35-45 from the launch spec):
  35. Payment automation (InvoiceAutomationScheduler) untouched.
  36. PaymentAutomationGuardian untouched.
  37. Invoice publication (client_visibility) semantics untouched.
  38. Withdrawal semantics untouched.
  39. MoyKlass sync semantics untouched (no new write path).
  40. Client payments (parent-facing cabinet) endpoint untouched.
  41. client_manager permissions on the workspace unchanged.
  42. Frontend incident diagnostics (v7.1.16) still wired.
  43. Safe-area model (v7.1.14.3) still intact.
  44. Food-only isolation untouched.
  45. Version marker + both cache-bust query strings bumped to v7.1.16.1.

Run:
    python -m unittest tests.test_payment_period_regression_v71161 -v
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest  # noqa: E402

import web_app_server as srv  # noqa: E402
from storage import Storage  # noqa: E402

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
WEB_APP_SERVER_SRC = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
STORAGE_SRC = (ROOT / "storage.py").read_text(encoding="utf-8")


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


class TestAutomationUntouched(unittest.TestCase):
    def test_35_invoice_automation_scheduler_class_unchanged_signature(self):
        self.assertIn("class InvoiceAutomationScheduler", WEB_APP_SERVER_SRC)
        # the v7.1.16.1 additions live in MiniAppContext (_parse_payments_period,
        # payments_workspace_stats/attention/list) — never inside the
        # automation scheduler class body.
        sched_start = WEB_APP_SERVER_SRC.index("class InvoiceAutomationScheduler")
        sched_body = WEB_APP_SERVER_SRC[sched_start:sched_start + 200]
        self.assertNotIn("_parse_payments_period", sched_body)

    def test_36_guardian_never_writes_payment_intents(self):
        idx = WEB_APP_SERVER_SRC.index("class PaymentAutomationGuardian")
        # scan a generous window for the class body without pulling in
        # unrelated later classes
        end = WEB_APP_SERVER_SRC.index("\nclass ", idx + 30)
        guardian_body = WEB_APP_SERVER_SRC[idx:end]
        self.assertNotIn("UPDATE payment_intents", guardian_body)
        self.assertNotIn("INSERT INTO payment_intents", guardian_body)


class TestPublicationAndWithdrawalUnchanged(unittest.TestCase):
    def test_37_client_visibility_columns_unchanged(self):
        self.assertIn('self._ensure_column(conn, "payment_intents", "client_visibility"', STORAGE_SRC)
        self.assertIn('self._ensure_column(conn, "payment_intents", "published_at"', STORAGE_SRC)
        self.assertIn('self._ensure_column(conn, "payment_intents", "withdrawn_at"', STORAGE_SRC)

    def test_38_withdrawal_table_untouched(self):
        self.assertIn("payment_intent_withdrawals", STORAGE_SRC)
        # no v7.1.16.1 comment anywhere near the withdrawal table definition
        idx = STORAGE_SRC.index("CREATE TABLE IF NOT EXISTS payment_intent_withdrawals")
        nearby = STORAGE_SRC[max(0, idx - 400):idx]
        self.assertNotIn("v7.1.16.1", nearby)


class TestMoyKlassSyncUnchanged(unittest.TestCase):
    def test_39_no_new_moyklass_write_path(self):
        # the new period-filter code only ever touches local SQL — grep the
        # new methods' source for any actual MoyKlass client call (the
        # 'posted_to_moyklass' STATUS STRING is a legitimate, pre-existing
        # local value and must not trip this check).
        moyklass_call_pattern = re.compile(r"\bmoyklass\.|MoyKlassClient|moyklass_client\b", re.I)
        for method in ("get_payments_workspace_stats", "get_payments_attention_queue", "list_payment_intents_by_period"):
            idx = STORAGE_SRC.index(f"def {method}(")
            end_marker = "\n    def "
            end = STORAGE_SRC.index(end_marker, idx + 10) if end_marker in STORAGE_SRC[idx + 10:] else len(STORAGE_SRC)
            body = STORAGE_SRC[idx:end]
            self.assertIsNone(moyklass_call_pattern.search(body), method)


class TestClientPaymentsUnaffected(unittest.TestCase):
    def test_40_client_payments_endpoint_route_unchanged(self):
        self.assertIn('if path == "/api/client/payments":', WEB_APP_SERVER_SRC)


class TestClientManagerPermissionsUnchanged(unittest.TestCase):
    def test_41_workspace_view_roles_unchanged(self):
        self.assertIn('WORKSPACE_VIEW_ROLES = {"owner", "admin", "operations", "client_manager"}', WEB_APP_SERVER_SRC)
        # the new endpoint reuses the exact same gate, not a new one
        idx = WEB_APP_SERVER_SRC.index("def payments_workspace_list(")
        end = WEB_APP_SERVER_SRC.index("\n    def ", idx + 10)
        body = WEB_APP_SERVER_SRC[idx:end]
        self.assertIn("self._require_workspace_access(auth)", body)


class TestFrontendIncidentDiagnosticsStillWired(unittest.TestCase):
    def test_42_frontend_incident_endpoints_present(self):
        self.assertIn('if path == "/api/client/frontend-incident":', WEB_APP_SERVER_SRC)
        self.assertIn('if path == "/api/client/frontend-incidents/summary":', WEB_APP_SERVER_SRC)
        self.assertIn("function _reportFrontendIncident(errorCode, action)", APP_JS)


class TestSafeAreaIntact(unittest.TestCase):
    def test_43_safe_area_spacer_model_intact(self):
        self.assertIn('id="appTopSafeSpacer"', INDEX_HTML)
        self.assertIn("IOS_TELEGRAM_MIN_TOP_SAFE_AREA_PX", APP_JS)


class TestFoodOnlyIsolationUnaffected(unittest.TestCase):
    def test_44_food_only_never_touched_by_period_code(self):
        for method in ("get_payments_workspace_stats", "get_payments_attention_queue", "list_payment_intents_by_period", "_parse_payments_period"):
            src = STORAGE_SRC if hasattr(Storage, method) else WEB_APP_SERVER_SRC
            idx = src.index(f"def {method}(")
            end_marker = "\n    def "
            end = src.index(end_marker, idx + 10) if end_marker in src[idx + 10:] else len(src)
            body = src[idx:end]
            self.assertNotIn("food", body.lower())
            self.assertNotIn("parent_child_links", body)

    def test_44b_food_only_link_path_untouched_end_to_end(self):
        st = _tmp_storage()
        code = st.get_or_create_link_code_for_student("F9001")
        result = st.link_parent_to_child("990001", code)
        self.assertTrue(result.get("ok"))
        with st._connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM parent_child_links WHERE mk_student_id='F9001'").fetchone()[0]
        self.assertEqual(n, 1)


class TestVersionBump(unittest.TestCase):
    def test_45_version_and_cache_bust(self):
        self.assertIn('console.log("MiniApp version: v7.1.17.1");', APP_JS)
        self.assertIn("styles.css?v=7.1.17.1", INDEX_HTML)
        self.assertIn("app.js?v=7.1.17.1", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
