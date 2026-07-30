"""Tests for v7.1.9 — PaymentAutomationGuardian: fixed 600s quick-cycle,
overlap/lease protection, heartbeat, training-sync integration, and the
"never a financial side effect" safety guarantee.

Run offline (mocked MoyKlass, temp SQLite file, no real threads.sleep waits
beyond what's explicitly patched):
    python -m unittest tests.test_payment_automation_guardian -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
import web_app_server
from web_app_server import MiniAppContext, PaymentAutomationGuardian
from utils import now_iso as _now


class _FakeResult:
    def __init__(self, data, ok=True):
        self.data = data
        self.ok = ok


def _sub(sub_id="SUB1", status_id="2", class_id="900"):
    return {"id": sub_id, "statusId": status_id, "mainClassId": class_id, "classIds": [class_id]}


def _join(join_id="J1", class_id="900", status_id="2"):
    return {"id": join_id, "classId": class_id, "statusId": status_id}


def _make_ctx(storage: Storage) -> MiniAppContext:
    ctx = MiniAppContext.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = MagicMock()
    mk = MagicMock()
    mk.get_user_subscriptions = MagicMock(return_value=_FakeResult({"items": [_sub()]}))
    mk.get_user_joins = MagicMock(return_value=_FakeResult({"items": [_join()]}))
    ctx.moyklass = mk
    return ctx


def _seed_item(storage: Storage, inv_id: str, mk_user_id: str = "8801", *,
                stage: str = "discovered", sub_id: str = "SUB1") -> dict:
    now = _now()
    import json
    snap = json.dumps({"userSubscriptionId": sub_id})
    item = storage.upsert_automation_item(inv_id, mk_user_id, "Test", snap, now)
    storage.update_automation_item_stage(item["id"], stage, now=now)
    return storage.get_automation_item_by_id(item["id"])


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


class TestConstants(unittest.TestCase):
    def test_01_interval_is_600_seconds(self):
        self.assertEqual(PaymentAutomationGuardian.QUICK_CYCLE_INTERVAL_SECONDS, 600)

    def test_02_startup_delay_at_most_90s(self):
        self.assertLessEqual(PaymentAutomationGuardian.STARTUP_DELAY_SECONDS, 90)

    def test_03_independent_of_discovery_scan_interval(self):
        # The Guardian's interval must never be computed from
        # invoice_automation_settings.scan_interval_minutes — only mentioned
        # in the docstring's explanatory rationale, never in actual code.
        import inspect
        for name, member in vars(PaymentAutomationGuardian).items():
            if not inspect.isfunction(member):
                continue
            body = inspect.getsource(member)
            body = body.split('"""', 2)[-1] if '"""' in body else body  # strip leading docstring
            self.assertNotIn("get_automation_settings", body, f"{name} must not read discovery settings")


class TestQuickCycleCore(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_ctx(self.storage)
        self.guardian = PaymentAutomationGuardian(self.ctx)

    def test_04_first_cycle_creates_health_run(self):
        self.guardian._run_quick_cycle()
        run = self.storage.get_last_health_run()
        self.assertIsNotNone(run)
        self.assertIn(run["status"], ("ok", "degraded", "failed"))
        self.assertIsNotNone(run["finished_at"])

    def test_05_paused_item_detected_without_manual_button(self):
        self.ctx.moyklass.get_user_joins.return_value = _FakeResult(
            {"items": [_join(status_id="99046")]}
        )
        item = _seed_item(self.storage, "INV-G1")
        self.guardian._run_quick_cycle()
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertEqual(stored["reason_code"], "client_training_paused")

    def test_06_resume_detected_automatically(self):
        # v7.1.10 — Guardian clears the training block automatically (no
        # more "client_resume_confirmation_required" holding state waiting
        # for a manual button).
        item = _seed_item(self.storage, "INV-G2")
        self.storage.update_automation_item_stage(
            item["id"], "discovered", reason_code="client_training_paused", now=_now(),
        )
        self.ctx.moyklass.get_user_joins.return_value = _FakeResult({"items": [_join(status_id="2")]})
        self.guardian._run_quick_cycle()
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertIsNone(stored["reason_code"])
        self.assertEqual(stored["current_stage"], "discovered")

    def test_07_no_manual_button_required_no_mini_app_open(self):
        # The whole point: Guardian runs independent of any HTTP request /
        # Mini App interaction. _run_quick_cycle takes no auth/request args.
        import inspect
        sig = inspect.signature(PaymentAutomationGuardian._run_quick_cycle)
        self.assertEqual(list(sig.parameters.keys()), ["self"])

    def test_08_published_options_created_publish_off_is_rechecked(self):
        # v7.1.9 asymmetry fix: payment_options_created is a training-sync
        # candidate regardless of publish_enabled (Guardian doesn't even
        # look at that flag).
        now = _now()
        with self.storage._connect() as conn:
            conn.execute(
                """INSERT INTO payment_intents
                   (public_id, mk_user_id, mk_invoice_id, mk_user_subscription_id, student_name,
                    amount_minor, amount_byn, currency, status, client_visibility, source,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PI-G8", "8801", "INV-G8", "SUB1", "Test", 23900, 239.0, "BYN",
                 "ready", "hidden", "moyklass_invoice_automation", now, now),
            )
        item = _seed_item(self.storage, "INV-G8", stage="payment_options_created")
        self.storage.relink_automation_item_intent(item["id"], "PI-G8", now)
        self.ctx.moyklass.get_user_joins.return_value = _FakeResult({"items": [_join(status_id="99046")]})
        self.guardian._run_quick_cycle()
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertEqual(stored["reason_code"], "client_training_paused")
        self.assertEqual(stored["current_stage"], "payment_options_created", "stage must never regress")

    def test_09_published_unpaid_post_off_is_rechecked(self):
        now = _now()
        with self.storage._connect() as conn:
            conn.execute(
                """INSERT INTO payment_intents
                   (public_id, mk_user_id, mk_invoice_id, mk_user_subscription_id, student_name,
                    amount_minor, amount_byn, currency, status, client_visibility, source,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PI-G9", "8801", "INV-G9", "SUB1", "Test", 23900, 239.0, "BYN",
                 "awaiting_payment", "published", "moyklass_invoice_automation", now, now),
            )
        item = _seed_item(self.storage, "INV-G9", stage="published")
        self.storage.relink_automation_item_intent(item["id"], "PI-G9", now)
        self.ctx.moyklass.get_user_joins.return_value = _FakeResult({"items": [_join(status_id="99046")]})
        self.guardian._run_quick_cycle()
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertEqual(stored["reason_code"], "client_training_paused")
        pi = self.storage.get_payment_intent("PI-G9")
        self.assertEqual(pi["client_visibility"], "published", "must never auto-withdraw")

    def test_10_paid_item_never_rechecked(self):
        now = _now()
        with self.storage._connect() as conn:
            conn.execute(
                """INSERT INTO payment_intents
                   (public_id, mk_user_id, mk_invoice_id, mk_user_subscription_id, student_name,
                    amount_minor, amount_byn, currency, status, client_visibility, source,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PI-G10", "8801", "INV-G10", "SUB1", "Test", 23900, 239.0, "BYN",
                 "paid", "published", "moyklass_invoice_automation", now, now),
            )
        item = _seed_item(self.storage, "INV-G10", stage="published")
        self.storage.relink_automation_item_intent(item["id"], "PI-G10", now)
        self.guardian._run_quick_cycle()
        # moyklass must never even be called for a paid, terminal item
        self.ctx.moyklass.get_user_subscriptions.assert_not_called()

    def test_11_unrelated_active_course_unaffected(self):
        item_a = _seed_item(self.storage, "INV-G11A", sub_id="SUB-A")
        item_b = _seed_item(self.storage, "INV-G11B", sub_id="SUB-B")

        def get_subs(uid, limit=100):
            return _FakeResult({"items": [_sub("SUB-A", class_id="A"), _sub("SUB-B", class_id="B")]})

        def get_joins(uid, limit=100):
            return _FakeResult({"items": [
                _join("JA", class_id="A", status_id="99046"),
                _join("JB", class_id="B", status_id="2"),
            ]})
        self.ctx.moyklass.get_user_subscriptions = get_subs
        self.ctx.moyklass.get_user_joins = get_joins
        self.guardian._run_quick_cycle()
        stored_a = self.storage.get_automation_item_by_id(item_a["id"])
        stored_b = self.storage.get_automation_item_by_id(item_b["id"])
        self.assertEqual(stored_a["reason_code"], "client_training_paused")
        self.assertIsNone(stored_b["reason_code"], "unrelated active course must be unaffected")

    def test_12_multiple_children_independent(self):
        item_child1 = _seed_item(self.storage, "INV-G12A", mk_user_id="9001", sub_id="SUB-C1")
        item_child2 = _seed_item(self.storage, "INV-G12B", mk_user_id="9002", sub_id="SUB-C2")

        def get_subs(uid, limit=100):
            if uid == "9001":
                return _FakeResult({"items": [_sub("SUB-C1", class_id="C1")]})
            return _FakeResult({"items": [_sub("SUB-C2", class_id="C2")]})

        def get_joins(uid, limit=100):
            if uid == "9001":
                return _FakeResult({"items": [_join("J-C1", class_id="C1", status_id="99046")]})
            return _FakeResult({"items": [_join("J-C2", class_id="C2", status_id="2")]})
        self.ctx.moyklass.get_user_subscriptions = get_subs
        self.ctx.moyklass.get_user_joins = get_joins
        self.guardian._run_quick_cycle()
        s1 = self.storage.get_automation_item_by_id(item_child1["id"])
        s2 = self.storage.get_automation_item_by_id(item_child2["id"])
        self.assertEqual(s1["reason_code"], "client_training_paused")
        self.assertIsNone(s2["reason_code"])

    def test_13_moyklass_outage_never_uses_cached_active(self):
        item = _seed_item(self.storage, "INV-G13")
        self.storage.update_automation_item_stage(
            item["id"], "discovered", reason_code=None, now=_now(),
        )
        self.ctx.moyklass.get_user_subscriptions.return_value = _FakeResult({}, ok=False)
        self.ctx.moyklass.get_user_joins.return_value = _FakeResult({}, ok=False)
        self.guardian._run_quick_cycle()
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertEqual(stored["reason_code"], "training_state_unavailable")

    def test_14_one_client_moyklass_exception_does_not_stop_others(self):
        item_a = _seed_item(self.storage, "INV-G14A", mk_user_id="9101")
        item_b = _seed_item(self.storage, "INV-G14B", mk_user_id="9102")

        def get_subs(uid, limit=100):
            if uid == "9101":
                raise RuntimeError("boom")
            return _FakeResult({"items": [_sub()]})
        self.ctx.moyklass.get_user_subscriptions = get_subs
        self.ctx.moyklass.get_user_joins.return_value = _FakeResult({"items": [_join(status_id="99046")]})
        self.guardian._run_quick_cycle()  # must not raise
        stored_b = self.storage.get_automation_item_by_id(item_b["id"])
        self.assertEqual(stored_b["reason_code"], "client_training_paused")

    def test_15_incident_created_for_paused_item(self):
        self.ctx.moyklass.get_user_joins.return_value = _FakeResult({"items": [_join(status_id="99046")]})
        item = _seed_item(self.storage, "INV-G15")
        self.guardian._run_quick_cycle()
        incidents = self.storage.list_open_incidents()
        keys = [i["dedup_key"] for i in incidents]
        self.assertIn(f"training_state:automation_item:{item['id']}", keys)

    def test_16_incident_dedup_no_duplicate_across_cycles(self):
        self.ctx.moyklass.get_user_joins.return_value = _FakeResult({"items": [_join(status_id="99046")]})
        _seed_item(self.storage, "INV-G16")
        self.guardian._run_quick_cycle()
        self.guardian._run_quick_cycle()
        self.guardian._run_quick_cycle()
        incidents = self.storage.list_open_incidents()
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["occurrence_count"], 3)

    def test_17_audit_not_spammed_every_cycle_for_unchanged_state(self):
        self.ctx.moyklass.get_user_joins.return_value = _FakeResult({"items": [_join(status_id="99046")]})
        _seed_item(self.storage, "INV-G17")
        self.guardian._run_quick_cycle()
        self.guardian._run_quick_cycle()
        self.guardian._run_quick_cycle()
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM automation_audit_log WHERE mk_invoice_id='INV-G17' "
                "AND event_type='training_state_changed_by_guardian'"
            ).fetchall()
        self.assertEqual(len(rows), 1)


class TestOverlapAndLease(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_ctx(self.storage)
        self.guardian = PaymentAutomationGuardian(self.ctx)

    def test_18_overlap_skipped_when_lease_held(self):
        now = _now()
        self.storage.start_health_run("quick_cycle", "corr-external", "tok-external", now)
        self.guardian._run_quick_cycle()
        # the guardian's own cycle must not have created a second "running"
        # row of its own — get_running-equivalent: only one row w/ status
        runs = self.storage.list_health_runs()
        running = [r for r in runs if r["status"] == "running"]
        self.assertEqual(len(running), 1)
        self.assertEqual(running[0]["correlation_id"], "corr-external")

    def test_19_overlap_creates_dedup_incident(self):
        now = _now()
        self.storage.start_health_run("quick_cycle", "corr-external2", "tok-external2", now)
        self.guardian._run_quick_cycle()
        inc = self.storage.get_incident(PaymentAutomationGuardian.OVERLAP_DEDUP_KEY)
        self.assertIsNotNone(inc)
        self.assertEqual(inc["status"], "open")

    def test_20_overlap_resolved_once_lease_freed(self):
        now = _now()
        r = self.storage.start_health_run("quick_cycle", "corr-external3", "tok-external3", now)
        self.guardian._run_quick_cycle()  # sees it running -> overlap incident opens
        self.storage.finish_health_run("corr-external3", "tok-external3", status="ok", finished_at=_now())
        self.guardian._run_quick_cycle()  # lease now free -> cycle runs -> resolves overlap incident
        inc = self.storage.get_incident(PaymentAutomationGuardian.OVERLAP_DEDUP_KEY)
        self.assertEqual(inc["status"], "resolved")

    def test_21_no_second_running_lease_created_during_overlap(self):
        now = _now()
        self.storage.start_health_run("quick_cycle", "corr-x", "tok-x", now)
        before = len(self.storage.list_health_runs())
        self.guardian._run_quick_cycle()
        after = len(self.storage.list_health_runs())
        self.assertEqual(before, after, "an overlap-skip must not insert a new health_runs row")


class TestNoFinancialSideEffects(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_ctx(self.storage)
        self.guardian = PaymentAutomationGuardian(self.ctx)

    def test_22_never_creates_payment_intent(self):
        _seed_item(self.storage, "INV-G22")
        self.ctx.moyklass.get_user_joins.return_value = _FakeResult({"items": [_join(status_id="99046")]})
        before = self.storage.payment_intents_stats()
        self.guardian._run_quick_cycle()
        after = self.storage.payment_intents_stats()
        self.assertEqual(before, after)

    def test_23_never_calls_bepaid_or_publish_methods(self):
        # Static guard: the Guardian's source must never reference bePaid
        # creation, publish-to-parent, or withdrawal methods.
        import inspect
        src = inspect.getsource(PaymentAutomationGuardian)
        for forbidden in (
            "_automation_create_intent", "publish_payment_intent_to_client",
            "create-bepaid", "payment_intent_prepare_options",
            "withdraw_intent_from_parent", "withdraw_payment_intent",
        ):
            self.assertNotIn(forbidden, src, f"Guardian source references forbidden action: {forbidden}")

    def test_24_resumes_automatically_without_financial_side_effect(self):
        # v7.1.10 — Guardian DOES clear the training block automatically
        # (superseding the old "requires a separate manual confirmation"
        # invariant), but resuming is a state-machine change only: still
        # zero Payment Intents/bePaid/publish/withdrawal created by it
        # (test_22/test_23 already cover the "never" side generally; this
        # confirms it holds specifically across an actual resume transition).
        item = _seed_item(self.storage, "INV-G24")
        self.storage.update_automation_item_stage(
            item["id"], "discovered", reason_code="client_training_paused", now=_now(),
        )
        self.ctx.moyklass.get_user_joins.return_value = _FakeResult({"items": [_join(status_id="2")]})
        before = self.storage.payment_intents_stats()
        self.guardian._run_quick_cycle()
        after = self.storage.payment_intents_stats()
        self.assertEqual(before, after)
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertIsNone(stored["reason_code"])
        self.assertEqual(stored["current_stage"], "discovered")


if __name__ == "__main__":
    unittest.main()
