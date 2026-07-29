"""Tests for v7.1.9 — Storage layer of the Payment Automation Guardian:
health run lease (atomic start/heartbeat/finish), incidents (upsert/resolve/
reopen/dedup), repair attempts, and training-sync candidate query.

Run offline (temp SQLite file, no MoyKlass/bePaid/Telegram):
    python -m unittest tests.test_payment_automation_health -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _now(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


class TestRepeatedInit(unittest.TestCase):
    def test_01_repeated_init_is_idempotent(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        path = Path(tmp.name)
        Storage(path)
        Storage(path)  # second init on the same file must not raise
        Storage(path)

    def test_02_guardian_tables_exist_after_init(self):
        st = _make_storage()
        with st._connect() as conn:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        for t in (
            "payment_automation_health_runs",
            "payment_automation_incidents",
            "payment_automation_repair_attempts",
        ):
            self.assertIn(t, tables)

    def test_03_legacy_db_without_guardian_tables_gets_them_added(self):
        # Simulate an old DB: create it, drop the guardian tables, then
        # re-open via Storage() again — init must recreate them additively.
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        path = Path(tmp.name)
        st = Storage(path)
        with st._connect() as conn:
            conn.execute("DROP TABLE payment_automation_health_runs")
            conn.execute("DROP TABLE payment_automation_incidents")
            conn.execute("DROP TABLE payment_automation_repair_attempts")
        st2 = Storage(path)
        with st2._connect() as conn:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertIn("payment_automation_health_runs", tables)
        self.assertIn("payment_automation_incidents", tables)


class TestHealthRunLease(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()

    def test_04_start_health_run_succeeds(self):
        r = self.st.start_health_run("quick_cycle", "corr-1", "tok-1", _now())
        self.assertTrue(r["ok"])

    def test_05_concurrent_lease_attempt_rejected(self):
        now = _now()
        self.st.start_health_run("quick_cycle", "corr-a", "tok-a", now)
        r2 = self.st.start_health_run("quick_cycle", "corr-b", "tok-b", now)
        self.assertFalse(r2["ok"])
        self.assertEqual(r2["error"], "already_running")
        self.assertEqual(r2["correlation_id"], "corr-a")

    def test_06_two_concurrent_lease_attempts_only_one_wins(self):
        # Simulates two processes racing for the lease using two separate
        # Storage/connections against the same DB file.
        now = _now()
        st_a = Storage(self.st.db_path)
        st_b = Storage(self.st.db_path)
        r_a = st_a.start_health_run("quick_cycle", "corr-race-a", "tok-a", now)
        r_b = st_b.start_health_run("quick_cycle", "corr-race-b", "tok-b", now)
        wins = [r for r in (r_a, r_b) if r["ok"]]
        losses = [r for r in (r_a, r_b) if not r["ok"]]
        self.assertEqual(len(wins), 1)
        self.assertEqual(len(losses), 1)
        self.assertEqual(losses[0]["error"], "already_running")

    def test_07_heartbeat_renews_lease(self):
        now = _now()
        self.st.start_health_run("quick_cycle", "corr-hb", "tok-hb", now, lease_seconds=60)
        ok = self.st.heartbeat_health_run("corr-hb", "tok-hb", _now(30))
        self.assertTrue(ok)
        row = self.st.get_last_health_run()
        self.assertEqual(row["correlation_id"], "corr-hb")

    def test_08_heartbeat_wrong_token_fails(self):
        now = _now()
        self.st.start_health_run("quick_cycle", "corr-hb2", "tok-hb2", now)
        ok = self.st.heartbeat_health_run("corr-hb2", "wrong-token", _now())
        self.assertFalse(ok)

    def test_09_stale_lease_expires_and_allows_new_run(self):
        # Start a run with a lease that is already expired (negative offset).
        past = _now(-1000)
        self.st.start_health_run("quick_cycle", "corr-stale", "tok-stale", past, lease_seconds=1)
        # Now attempt a new run "now" — the stale lease (expired 1000s+1s ago)
        # must be expired automatically, allowing this new run to succeed.
        r2 = self.st.start_health_run("quick_cycle", "corr-fresh", "tok-fresh", _now())
        self.assertTrue(r2["ok"])

    def test_10_finish_health_run_updates_status(self):
        now = _now()
        self.st.start_health_run("quick_cycle", "corr-fin", "tok-fin", now)
        ok = self.st.finish_health_run(
            "corr-fin", "tok-fin", status="ok", finished_at=_now(),
            duration_ms=500, checked_items=10, checked_clients=3,
            issues_found=1, issues_resolved=1, safe_repairs_performed=1,
        )
        self.assertTrue(ok)
        row = self.st.get_last_health_run()
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["duration_ms"], 500)
        self.assertEqual(row["checked_items"], 10)

    def test_11_finish_wrong_token_is_noop(self):
        now = _now()
        self.st.start_health_run("quick_cycle", "corr-fin2", "tok-fin2", now)
        ok = self.st.finish_health_run("corr-fin2", "wrong", status="ok", finished_at=_now())
        self.assertFalse(ok)

    def test_12_after_finish_new_lease_can_start(self):
        now = _now()
        self.st.start_health_run("quick_cycle", "corr-done", "tok-done", now)
        self.st.finish_health_run("corr-done", "tok-done", status="ok", finished_at=_now())
        r2 = self.st.start_health_run("quick_cycle", "corr-next", "tok-next", _now())
        self.assertTrue(r2["ok"])

    def test_13_get_last_successful_health_run_ignores_failed(self):
        now = _now()
        self.st.start_health_run("quick_cycle", "corr-fail", "tok-fail", now)
        self.st.finish_health_run("corr-fail", "tok-fail", status="failed", finished_at=_now())
        self.st.start_health_run("quick_cycle", "corr-ok", "tok-ok", _now())
        self.st.finish_health_run("corr-ok", "tok-ok", status="ok", finished_at=_now())
        last_ok = self.st.get_last_successful_health_run()
        self.assertEqual(last_ok["correlation_id"], "corr-ok")

    def test_14_list_health_runs_returns_recent_first(self):
        for i in range(3):
            cid = f"corr-list-{i}"
            self.st.start_health_run("quick_cycle", cid, f"tok-{i}", _now())
            self.st.finish_health_run(cid, f"tok-{i}", status="ok", finished_at=_now())
        runs = self.st.list_health_runs(limit=10)
        self.assertGreaterEqual(len(runs), 3)
        self.assertEqual(runs[0]["correlation_id"], "corr-list-2")

    def test_15_run_type_isolation(self):
        self.st.start_health_run("quick_cycle", "corr-q", "tok-q", _now())
        self.st.finish_health_run("corr-q", "tok-q", status="ok", finished_at=_now())
        r = self.st.start_health_run("daily_reconciliation", "corr-d", "tok-d", _now())
        self.assertTrue(r["ok"], "different run_type must not be blocked by quick_cycle's lease")


class TestIncidents(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()

    def test_16_upsert_creates_new_incident(self):
        r = self.st.upsert_incident(
            "dedup-1", component="training_state", scope_type="automation_item",
            scope_id="1", reason_code="client_training_paused", severity="warning",
            now=_now(),
        )
        self.assertTrue(r["created"])
        self.assertEqual(r["occurrence_count"], 1)

    def test_17_repeat_upsert_is_unchanged_and_bumps_occurrence(self):
        now = _now()
        self.st.upsert_incident(
            "dedup-2", component="training_state", scope_type="automation_item",
            scope_id="1", reason_code="client_training_paused", severity="warning", now=now,
        )
        r2 = self.st.upsert_incident(
            "dedup-2", component="training_state", scope_type="automation_item",
            scope_id="1", reason_code="client_training_paused", severity="warning", now=now,
        )
        self.assertFalse(r2["created"])
        self.assertTrue(r2["unchanged"])
        self.assertEqual(r2["occurrence_count"], 2)

    def test_18_severity_escalates_but_never_downgrades(self):
        now = _now()
        self.st.upsert_incident(
            "dedup-3", component="mk_posting", scope_type="payment_intent",
            scope_id="p1", reason_code="paid_not_posted_to_mk", severity="warning", now=now,
        )
        r2 = self.st.upsert_incident(
            "dedup-3", component="mk_posting", scope_type="payment_intent",
            scope_id="p1", reason_code="paid_not_posted_to_mk", severity="critical", now=now,
        )
        self.assertEqual(r2["severity"], "critical")
        r3 = self.st.upsert_incident(
            "dedup-3", component="mk_posting", scope_type="payment_intent",
            scope_id="p1", reason_code="paid_not_posted_to_mk", severity="info", now=now,
        )
        self.assertEqual(r3["severity"], "critical", "severity must never silently downgrade")

    def test_19_resolve_marks_resolved(self):
        now = _now()
        self.st.upsert_incident(
            "dedup-4", component="training_state", scope_type="automation_item",
            scope_id="1", reason_code="client_training_paused", severity="warning", now=now,
        )
        resolved = self.st.resolve_incident("dedup-4", now)
        self.assertTrue(resolved)
        inc = self.st.get_incident("dedup-4")
        self.assertEqual(inc["status"], "resolved")
        self.assertIsNotNone(inc["resolved_at"])

    def test_20_resolve_already_resolved_is_noop(self):
        now = _now()
        self.st.upsert_incident(
            "dedup-5", component="training_state", scope_type="automation_item",
            scope_id="1", reason_code="client_training_paused", severity="warning", now=now,
        )
        self.st.resolve_incident("dedup-5", now)
        second = self.st.resolve_incident("dedup-5", now)
        self.assertFalse(second, "resolving an already-resolved incident must be a no-op")

    def test_21_resolve_unknown_dedup_key_is_noop(self):
        self.assertFalse(self.st.resolve_incident("never-existed", _now()))

    def test_22_reopen_after_resolve(self):
        now = _now()
        self.st.upsert_incident(
            "dedup-6", component="training_state", scope_type="automation_item",
            scope_id="1", reason_code="client_training_paused", severity="warning", now=now,
        )
        self.st.resolve_incident("dedup-6", now)
        r = self.st.upsert_incident(
            "dedup-6", component="training_state", scope_type="automation_item",
            scope_id="1", reason_code="client_training_paused", severity="warning", now=now,
        )
        self.assertTrue(r["reopened"])
        inc = self.st.get_incident("dedup-6")
        self.assertEqual(inc["status"], "open")
        self.assertIsNone(inc["resolved_at"])

    def test_23_list_open_incidents_excludes_resolved(self):
        now = _now()
        self.st.upsert_incident(
            "dedup-7", component="training_state", scope_type="automation_item",
            scope_id="1", reason_code="client_training_paused", severity="warning", now=now,
        )
        self.st.upsert_incident(
            "dedup-8", component="training_state", scope_type="automation_item",
            scope_id="2", reason_code="client_training_paused", severity="warning", now=now,
        )
        self.st.resolve_incident("dedup-8", now)
        open_list = self.st.list_open_incidents()
        keys = {i["dedup_key"] for i in open_list}
        self.assertIn("dedup-7", keys)
        self.assertNotIn("dedup-8", keys)

    def test_24_list_open_incidents_scoped_by_ids(self):
        now = _now()
        self.st.upsert_incident(
            "dedup-9", component="training_state", scope_type="automation_item",
            scope_id="100", reason_code="client_training_paused", severity="warning", now=now,
        )
        self.st.upsert_incident(
            "dedup-10", component="training_state", scope_type="automation_item",
            scope_id="200", reason_code="client_training_paused", severity="warning", now=now,
        )
        scoped = self.st.list_open_incidents(scope_ids=["100"])
        keys = {i["dedup_key"] for i in scoped}
        self.assertIn("dedup-9", keys)
        self.assertNotIn("dedup-10", keys)

    def test_25_list_open_incidents_empty_scope_ids_returns_nothing(self):
        now = _now()
        self.st.upsert_incident(
            "dedup-11", component="training_state", scope_type="automation_item",
            scope_id="100", reason_code="client_training_paused", severity="warning", now=now,
        )
        scoped = self.st.list_open_incidents(scope_ids=[])
        self.assertEqual(scoped, [], "empty allow-list must never fall through to all scopes")

    def test_26_list_open_incidents_always_includes_system_scope(self):
        now = _now()
        self.st.upsert_incident(
            "dedup-sys", component="scheduler", scope_type="system",
            scope_id="", reason_code="scheduler_cycle_failed", severity="warning", now=now,
        )
        self.st.upsert_incident(
            "dedup-client", component="training_state", scope_type="automation_item",
            scope_id="999", reason_code="client_training_paused", severity="warning", now=now,
        )
        # client_manager scoped to only mk_user_id "1" must still see system incidents
        scoped = self.st.list_open_incidents(scope_ids=["1"])
        keys = {i["dedup_key"] for i in scoped}
        self.assertIn("dedup-sys", keys)
        self.assertNotIn("dedup-client", keys)

    def test_27_get_incident_by_id(self):
        now = _now()
        r = self.st.upsert_incident(
            "dedup-byid", component="training_state", scope_type="automation_item",
            scope_id="1", reason_code="client_training_paused", severity="warning", now=now,
        )
        inc = self.st.get_incident_by_id(r["id"])
        self.assertEqual(inc["dedup_key"], "dedup-byid")

    def test_28_payload_json_stored_and_sanitized_by_caller(self):
        now = _now()
        self.st.upsert_incident(
            "dedup-payload", component="training_state", scope_type="automation_item",
            scope_id="1", reason_code="client_training_paused", severity="warning", now=now,
            payload={"mk_user_id": "8801", "mk_invoice_id": "INV1"},
        )
        inc = self.st.get_incident("dedup-payload")
        self.assertIn("8801", inc["payload_json"])
        self.assertNotIn("token", inc["payload_json"].lower())

    def test_29_count_incidents_resolved_since(self):
        now = _now()
        self.st.upsert_incident(
            "dedup-count", component="training_state", scope_type="automation_item",
            scope_id="1", reason_code="client_training_paused", severity="warning", now=now,
        )
        self.st.resolve_incident("dedup-count", now)
        count = self.st.count_incidents_resolved_since(_now(-60))
        self.assertGreaterEqual(count, 1)


class TestRepairAttempts(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()
        r = self.st.upsert_incident(
            "dedup-repair", component="moyklass", scope_type="client",
            scope_id="8801", reason_code="moyklass_timeout", severity="warning", now=_now(),
        )
        self.incident_id = r["id"]

    def test_30_create_repair_attempt_success_updates_incident(self):
        self.st.create_repair_attempt(
            incident_id=self.incident_id, correlation_id="corr-1",
            action_code="refresh_moyklass_read", result="success", now=_now(),
        )
        inc = self.st.get_incident_by_id(self.incident_id)
        self.assertEqual(inc["repair_attempt_count"], 1)
        self.assertEqual(inc["repair_status"], "auto_repaired")

    def test_31_create_repair_attempt_failure_increments_count_only(self):
        self.st.create_repair_attempt(
            incident_id=self.incident_id, correlation_id="corr-2",
            action_code="refresh_moyklass_read", result="failed",
            now=_now(), error_summary="timeout again",
        )
        inc = self.st.get_incident_by_id(self.incident_id)
        self.assertEqual(inc["repair_attempt_count"], 1)
        self.assertNotEqual(inc["repair_status"], "auto_repaired")

    def test_32_list_repair_attempts(self):
        self.st.create_repair_attempt(
            incident_id=self.incident_id, correlation_id="corr-3",
            action_code="refresh_moyklass_read", result="success", now=_now(),
        )
        attempts = self.st.list_repair_attempts(self.incident_id)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["action_code"], "refresh_moyklass_read")

    def test_33_repair_attempt_without_incident_does_not_raise(self):
        self.st.create_repair_attempt(
            incident_id=None, correlation_id="corr-4",
            action_code="expire_stale_lease", result="success", now=_now(),
        )


class TestTrainingSyncCandidateQuery(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()

    def _seed(self, inv_id, stage, *, intent_public_id=None):
        now = _now()
        item = self.st.upsert_automation_item(inv_id, "8801", "Test", "{}", now)
        self.st.update_automation_item_stage(
            item["id"], stage,
            intent_public_id=intent_public_id, now=now,
        )
        return item

    def test_34_discovered_item_is_a_candidate(self):
        self._seed("INV-CAND-1", "discovered")
        cands = self.st.get_training_sync_candidates()
        stages = {c["current_stage"] for c in cands}
        self.assertIn("discovered", stages)

    def test_35_ignored_item_is_excluded(self):
        self._seed("INV-CAND-2", "ignored")
        cands = self.st.get_training_sync_candidates()
        invs = {c["mk_invoice_id"] for c in cands}
        self.assertNotIn("INV-CAND-2", invs)

    def test_36_payment_options_created_without_intent_is_a_candidate(self):
        self._seed("INV-CAND-3", "payment_options_created")
        cands = self.st.get_training_sync_candidates()
        invs = {c["mk_invoice_id"] for c in cands}
        self.assertIn("INV-CAND-3", invs)

    def test_37_paid_intent_is_excluded(self):
        now = _now()
        with self.st._connect() as conn:
            conn.execute(
                """INSERT INTO payment_intents
                   (public_id, mk_user_id, mk_invoice_id, student_name,
                    amount_minor, amount_byn, currency, status, client_visibility, source,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PI-CAND-4", 8801, "INV-CAND-4", "Test", 23900, 239.0, "BYN",
                 "paid", "published", "moyklass_invoice_automation", now, now),
            )
        self._seed("INV-CAND-4", "payment_options_created", intent_public_id="PI-CAND-4")
        cands = self.st.get_training_sync_candidates()
        invs = {c["mk_invoice_id"] for c in cands}
        self.assertNotIn("INV-CAND-4", invs)

    def test_38_withdrawn_intent_is_excluded(self):
        now = _now()
        with self.st._connect() as conn:
            conn.execute(
                """INSERT INTO payment_intents
                   (public_id, mk_user_id, mk_invoice_id, student_name,
                    amount_minor, amount_byn, currency, status, client_visibility, source,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PI-CAND-5", 8801, "INV-CAND-5", "Test", 23900, 239.0, "BYN",
                 "ready", "withdrawn", "moyklass_invoice_automation", now, now),
            )
        self._seed("INV-CAND-5", "payment_options_created", intent_public_id="PI-CAND-5")
        cands = self.st.get_training_sync_candidates()
        invs = {c["mk_invoice_id"] for c in cands}
        self.assertNotIn("INV-CAND-5", invs)

    def test_39_published_unpaid_intent_is_a_candidate(self):
        now = _now()
        with self.st._connect() as conn:
            conn.execute(
                """INSERT INTO payment_intents
                   (public_id, mk_user_id, mk_invoice_id, student_name,
                    amount_minor, amount_byn, currency, status, client_visibility, source,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PI-CAND-6", 8801, "INV-CAND-6", "Test", 23900, 239.0, "BYN",
                 "awaiting_payment", "published", "moyklass_invoice_automation", now, now),
            )
        self._seed("INV-CAND-6", "published", intent_public_id="PI-CAND-6")
        cands = self.st.get_training_sync_candidates()
        invs = {c["mk_invoice_id"] for c in cands}
        self.assertIn("INV-CAND-6", invs)


if __name__ == "__main__":
    unittest.main()
