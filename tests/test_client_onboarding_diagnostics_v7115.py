"""Tests for v7.1.15 — launch-readiness diagnostics: the client_onboarding_
events table, its writer/reader methods, and the "Подключения" dashboard
aggregation (get_onboarding_connections_summary/get_food_onboarding_summary/
get_launch_health_snapshot).

Covers:
  15. Successful events are counted (connected == real active links).
  16. Errors are counted by reason_code.
  17. Distinct parent/child counts are correct.
  18. A multi-child parent is not double-counted as two parents.
  19. Campaign metrics are isolated per campaign.
  20. Food metrics are reported separately, never merged into the regular
      conversion funnel.

Run:
    python -m unittest tests.test_client_onboarding_diagnostics_v7115 -v
"""
from __future__ import annotations

import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest  # noqa: E402

from storage import Storage  # noqa: E402


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def _make_code(st: Storage, mk_user_id: str) -> str:
    return st.create_client_link_code(mk_user_id, "Child", created_by="9001")["code"]


class TestEventLogWriterReader(unittest.TestCase):
    def setUp(self):
        self.st = _tmp_storage()

    def test_table_exists_and_writer_never_raises(self):
        # Fail-open contract: even garbage inputs must not raise.
        self.st.log_onboarding_event("link_created", "cl_code", "succeeded", mk_user_id="X1")
        self.st.log_onboarding_event("onboarding_failed", "invite", "failed", reason_code="invite_not_found")
        with self.st._connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM client_onboarding_events").fetchone()[0]
        self.assertEqual(n, 2)

    def test_has_onboarding_event_dedup_check(self):
        self.assertFalse(self.st.has_onboarding_event("cabinet_opened", parent_telegram_id="P1"))
        self.st.log_onboarding_event("cabinet_opened", "existing_link", "succeeded", parent_telegram_id="P1")
        self.assertTrue(self.st.has_onboarding_event("cabinet_opened", parent_telegram_id="P1"))
        self.assertFalse(self.st.has_onboarding_event("cabinet_opened", parent_telegram_id="P2"))


class TestConnectionsSummary(unittest.TestCase):
    def setUp(self):
        self.st = _tmp_storage()

    def test_15_connected_reflects_real_active_links(self):
        code1 = _make_code(self.st, "S9001")
        code2 = _make_code(self.st, "S9002")
        self.st.link_client_child("500001", code1, _now())
        self.st.link_client_child("500002", code2, _now())
        summary = self.st.get_onboarding_connections_summary()
        self.assertEqual(summary["connected"], 2)
        self.assertEqual(summary["connectedToday"], 2)

    def test_16_errors_counted_by_reason_code(self):
        self.st.log_onboarding_event("code_invalid", "cl_code", "failed", reason_code="code_not_found")
        self.st.log_onboarding_event("code_invalid", "cl_code", "failed", reason_code="code_not_found")
        self.st.log_onboarding_event("onboarding_failed", "invite", "failed", reason_code="invite_expired")
        summary = self.st.get_onboarding_connections_summary()
        self.assertEqual(summary["errors"], 3)
        errors = self.st.list_recent_onboarding_connection_errors(limit=10)
        reasons = [e["reason_code"] for e in errors]
        self.assertEqual(reasons.count("code_not_found"), 2)
        self.assertIn("invite_expired", reasons)

    def test_17_distinct_parent_and_child_counts(self):
        code_a = _make_code(self.st, "S9101")
        code_b = _make_code(self.st, "S9102")
        code_c = _make_code(self.st, "S9103")
        self.st.link_client_child("600001", code_a, _now())
        self.st.link_client_child("600002", code_b, _now())
        self.st.link_client_child("600002", code_c, _now())  # same parent, 2nd child
        summary = self.st.get_onboarding_connections_summary()
        self.assertEqual(summary["distinctChildren"], 3)
        self.assertEqual(summary["distinctParents"], 2)

    def test_18_multi_child_parent_not_double_counted(self):
        code_a = _make_code(self.st, "S9201")
        code_b = _make_code(self.st, "S9202")
        self.st.link_client_child("700001", code_a, _now())
        self.st.link_client_child("700001", code_b, _now())
        summary = self.st.get_onboarding_connections_summary()
        self.assertEqual(summary["distinctParents"], 1, "one parent, two children => one distinct parent")
        self.assertEqual(summary["multiChildParents"], 1)
        self.assertEqual(summary["distinctChildren"], 2)

    def test_19_campaign_metrics_isolated(self):
        camp_a = self.st.create_onboarding_campaign(name="A", academic_year="2026", created_by="9001")["campaign"]
        camp_b = self.st.create_onboarding_campaign(name="B", academic_year="2026", created_by="9001")["campaign"]
        with self.st._connect() as conn:
            conn.execute("UPDATE client_onboarding_campaigns SET status='active' WHERE id IN (?,?)", (camp_a["id"], camp_b["id"]))
        self.st.import_onboarding_campaign_recipients(camp_a["id"], [{"mk_user_id": "SA1", "child_display_name": "A1"}], added_by="9001")
        self.st.import_onboarding_campaign_recipients(camp_a["id"], [{"mk_user_id": "SA2", "child_display_name": "A2"}], added_by="9001")
        self.st.import_onboarding_campaign_recipients(camp_b["id"], [{"mk_user_id": "SB1", "child_display_name": "B1"}], added_by="9001")

        summary_a = self.st.get_onboarding_connections_summary(camp_a["id"])
        summary_b = self.st.get_onboarding_connections_summary(camp_b["id"])
        self.assertEqual(summary_a["totalPrepared"], 2)
        self.assertEqual(summary_b["totalPrepared"], 1)
        self.assertEqual(summary_a["connected"], 0)
        self.assertEqual(summary_b["connected"], 0)

        # Link one recipient from campaign A via an invite; campaign B must
        # be completely unaffected.
        with self.st._connect() as conn:
            rid = conn.execute(
                "SELECT id FROM client_onboarding_recipients WHERE campaign_id=? AND mk_user_id='SA1'", (camp_a["id"],)
            ).fetchone()["id"]
        invite = self.st.create_onboarding_invite(camp_a["id"], rid, "9001", "secret")
        self.st.activate_onboarding_invite(invite["invite_id"], invite["signature"], "800001", "secret")

        summary_a2 = self.st.get_onboarding_connections_summary(camp_a["id"])
        summary_b2 = self.st.get_onboarding_connections_summary(camp_b["id"])
        self.assertEqual(summary_a2["connected"], 1)
        self.assertEqual(summary_a2["notConnected"], 1)
        self.assertEqual(summary_b2["connected"], 0, "campaign B must be unaffected by campaign A activity")

    def test_20_food_metrics_separate_from_regular(self):
        code = _make_code(self.st, "S9301")
        self.st.link_client_child("900001", code, _now())
        food_code = self.st.get_or_create_link_code_for_student("F9301")
        self.st.link_parent_to_child("900002", food_code)

        summary = self.st.get_onboarding_connections_summary()
        food = self.st.get_food_onboarding_summary()
        self.assertEqual(summary["connected"], 1, "food link must not inflate the regular connected count")
        self.assertEqual(summary["distinctParents"], 1)
        self.assertEqual(food["confirmed"], 1)
        self.assertEqual(food["distinctParents"], 1)


class TestLaunchHealthSnapshot(unittest.TestCase):
    def setUp(self):
        self.st = _tmp_storage()
        self.settings = types.SimpleNamespace(
            client_notifications_enabled=False, client_notifications_pilot_telegram_ids=[],
            client_communications_enabled=False, client_communications_pilot_telegram_ids=[],
        )

    def test_health_snapshot_has_all_areas_and_never_raises(self):
        health = self.st.get_launch_health_snapshot(self.settings)
        for key in ("registration", "clientLinks", "notifications", "paymentAutomation", "availability", "communications"):
            self.assertIn(key, health)
            self.assertIn("status", health[key])

    def test_health_snapshot_no_data_when_empty(self):
        health = self.st.get_launch_health_snapshot(self.settings)
        self.assertEqual(health["registration"]["status"], "no_data")
        self.assertEqual(health["clientLinks"]["status"], "no_data")
        self.assertEqual(health["notifications"]["status"], "disabled")
        self.assertEqual(health["communications"]["status"], "disabled")

    def test_health_snapshot_detects_duplicate_links(self):
        now = _now()
        with self.st._connect() as conn:
            for _ in range(2):
                conn.execute(
                    """INSERT INTO client_parent_child_links
                       (parent_telegram_user_id, mk_user_id, child_display_name, status, linked_at, created_at, updated_at)
                       VALUES (?, 'DUPS1', 'Dup', 'active', ?, ?, ?)""",
                    (f"P{_}", now, now, now),
                )
        health = self.st.get_launch_health_snapshot(self.settings)
        self.assertEqual(health["clientLinks"]["status"], "warning")
        self.assertEqual(health["clientLinks"]["duplicateMkUserIds"], 1)

    def test_health_snapshot_reuses_existing_payment_automation_health_run(self):
        self.st.start_health_run("quick_cycle", "corr-1", "lock-1", _now())
        self.st.finish_health_run(
            correlation_id="corr-1", lock_token="lock-1", status="ok", finished_at=_now(),
            checked_items=5, checked_clients=5, issues_found=0, issues_resolved=0, safe_repairs_performed=0,
        )
        health = self.st.get_launch_health_snapshot(self.settings)
        self.assertEqual(health["paymentAutomation"]["status"], "ok")
        self.assertIsNotNone(health["paymentAutomation"]["lastRunFinishedAt"])


if __name__ == "__main__":
    unittest.main()
