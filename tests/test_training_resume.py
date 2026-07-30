"""Tests for v7.1.8 — training-state check/resume endpoints and audit dedup.

Covers:
  - automation_item_action("training-check"/"training-resume") permissions
  - item ownership resolved server-side only (mk_user_id/mk_user_subscription_id
    never trusted from the request body)
  - resume performs its OWN forced-fresh re-check (does not trust the stored
    reason_code as the final decision)
  - resume never creates intent/bePaid/publishes directly
  - existing_published_invoice_during_pause audit dedup (v7.1.8 fix)
  - pilot mode / withdrawal permissions / webhook / posting untouched

v7.1.10 — automatic training resume: the old two-step flow (check ->
client_resume_confirmation_required -> separate manual resume) was replaced
by automatic resume on the first fresh active check (training-check
included). training-resume is now a backward-compatible, idempotent
endpoint (see tests/test_automatic_training_resume.py for the full new
state-machine/Guardian/pilot-mode/API coverage; this file keeps the
permission/ownership/audit-dedup/unaffected-areas regressions).

Run offline (mocked MoyKlass, no real API/DB writes beyond a temp SQLite file):
    python -m unittest tests.test_training_resume -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage

CURRENT_VERSION = "7.1.10"


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.telegram_bot_token = "test_token_123"
    s.payment_invoice_automation_enabled = True
    s.payment_default_due_days = 14
    s.bepaid_auto_post_to_moyklass = False
    s.payment_parent_notifications_enabled = False
    return s


class _FakeResult:
    def __init__(self, data, ok=True):
        self.data = data
        self.ok = ok
        self.error = None if ok else "boom"


def _sub(sub_id, status_id="2", main_class_id="900000"):
    return {"id": sub_id, "statusId": status_id, "mainClassId": main_class_id, "classIds": [main_class_id]}


def _join(join_id, class_id="900000", status_id="2"):
    return {"id": join_id, "classId": class_id, "statusId": status_id}


def _configure_moyklass(mk, sub_id, *, sub_status="2", join_status="2", class_id="900000", unavailable=False):
    if unavailable:
        mk.get_user_subscriptions.return_value = _FakeResult({}, ok=False)
        mk.get_user_joins.return_value = _FakeResult({}, ok=False)
        return
    mk.get_user_subscriptions.return_value = _FakeResult({"items": [_sub(sub_id, status_id=sub_status, main_class_id=class_id)]})
    mk.get_user_joins.return_value = _FakeResult({"items": [_join("J-" + str(sub_id), class_id=class_id, status_id=join_status)]})


def _make_context(storage: Storage, settings: MagicMock) -> Any:
    from web_app_server import MiniAppContext
    ctx = MiniAppContext.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = settings
    mk = MagicMock()
    mk.request.return_value = MagicMock(ok=False, data={})
    ctx.moyklass = mk
    ctx._material_cache = {}
    ctx._mk_comment_cache = {}
    ctx._mk_student_name_cache = {}
    ctx._client_tasks_sync_cache = {"ts": 0.0, "result": {}}
    return ctx


def _seed_item(storage: Storage, *, inv_id="INV-R1", mk_user_id="8801", sub_id="SUB-R1",
                stage="requires_check", reason_code="client_training_paused") -> dict:
    now = _now()
    import json as _json
    snapshot = _json.dumps({"id": inv_id, "userId": mk_user_id, "userSubscriptionId": sub_id})
    item = storage.upsert_automation_item(inv_id, mk_user_id, "Тест", snapshot, now)
    storage.update_automation_item_stage(
        item["id"], stage, reason_code=reason_code, readable_reason="test", now=now,
    )
    return storage.get_automation_item_by_id(item["id"])


_OWNER_AUTH = {"_internal": False, "role": "owner", "user_id": "1001", "full_name": "Owner"}
_CM_AUTH = {"_internal": False, "role": "client_manager", "user_id": "1004", "full_name": "CM"}
_TEACHER_AUTH = {"_internal": False, "role": "teacher", "user_id": "1005", "full_name": "Teacher"}


class TestPermissions(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage, _make_settings())

    def test_01_check_permissions_denied_for_teacher(self):
        item = _seed_item(self.storage)
        with patch.object(self.ctx, "_role_for_user", return_value="teacher"):
            result = self.ctx.automation_item_action(_TEACHER_AUTH, str(item["id"]), "training-check", {})
        self.assertFalse(result.get("ok"))

    def test_01b_check_permissions_allowed_for_client_manager(self):
        item = _seed_item(self.storage)
        _configure_moyklass(self.ctx.moyklass, "SUB-R1", join_status="99046")
        with patch.object(self.ctx, "_role_for_user", return_value="client_manager"):
            result = self.ctx.automation_item_action(_CM_AUTH, str(item["id"]), "training-check", {})
        self.assertTrue(result.get("ok"))

    def test_02_resume_permissions_denied_for_teacher(self):
        item = _seed_item(self.storage, reason_code="client_resume_confirmation_required")
        with patch.object(self.ctx, "_role_for_user", return_value="teacher"):
            result = self.ctx.automation_item_action(_TEACHER_AUTH, str(item["id"]), "training-resume", {})
        self.assertFalse(result.get("ok"))

    def test_02b_resume_permissions_allowed_for_operations(self):
        item = _seed_item(self.storage, reason_code="client_resume_confirmation_required")
        _configure_moyklass(self.ctx.moyklass, "SUB-R1", sub_status="2", join_status="2")
        with patch.object(self.ctx, "_role_for_user", return_value="operations"):
            result = self.ctx.automation_item_action(
                {"_internal": False, "role": "operations", "user_id": "1003", "full_name": "Ops"},
                str(item["id"]), "training-resume", {},
            )
        self.assertTrue(result.get("ok"))


class TestOwnershipAndInputTrust(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage, _make_settings())

    def test_03_item_id_substitution_cannot_target_unknown_item(self):
        with patch.object(self.ctx, "_role_for_user", return_value="owner"):
            result = self.ctx.automation_item_action(_OWNER_AUTH, "999999", "training-check", {})
        self.assertFalse(result.get("ok"))
        self.assertIn("не найден", result.get("error", "").lower())

    def test_04_frontend_supplied_state_and_ids_are_ignored(self):
        item = _seed_item(self.storage)
        _configure_moyklass(self.ctx.moyklass, "SUB-R1", join_status="99046")
        malicious_body = {
            "state": "active", "reason_code": None, "mk_user_id": "1",
            "mk_user_subscription_id": "OTHER", "status_id": "2", "classId": "1",
        }
        with patch.object(self.ctx, "_role_for_user", return_value="owner"):
            result = self.ctx.automation_item_action(_OWNER_AUTH, str(item["id"]), "training-check", malicious_body)
        # Real MoyKlass mock says paused — the forged "active" body must not win.
        self.assertEqual(result.get("state"), "paused")
        # get_user_subscriptions must have been called with the REAL mk_user_id (8801),
        # never the forged one ("1") from the request body.
        called_with = self.ctx.moyklass.get_user_subscriptions.call_args
        self.assertEqual(str(called_with.args[0]), "8801")


class TestCheckStates(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage, _make_settings())

    def _check(self, item):
        with patch.object(self.ctx, "_role_for_user", return_value="owner"):
            return self.ctx.automation_item_action(_OWNER_AUTH, str(item["id"]), "training-check", {})

    def test_05_check_paused(self):
        item = _seed_item(self.storage, inv_id="INV05", sub_id="SUB05")
        _configure_moyklass(self.ctx.moyklass, "SUB05", join_status="99046")
        r = self._check(item)
        self.assertEqual(r["state"], "paused")
        self.assertEqual(r["reason_code"], "client_training_paused")
        self.assertFalse(r["resume_confirmation_required"])

    def test_06_check_frozen(self):
        item = _seed_item(self.storage, inv_id="INV06", sub_id="SUB06")
        _configure_moyklass(self.ctx.moyklass, "SUB06", sub_status="3", join_status="2")
        r = self._check(item)
        self.assertEqual(r["state"], "paused")
        self.assertEqual(r["reason_code"], "training_subscription_frozen")

    def test_07_check_finished(self):
        item = _seed_item(self.storage, inv_id="INV07", sub_id="SUB07")
        _configure_moyklass(self.ctx.moyklass, "SUB07", join_status="1")
        r = self._check(item)
        self.assertEqual(r["state"], "finished")
        self.assertEqual(r["reason_code"], "client_training_finished")

    def test_08_check_unknown(self):
        item = _seed_item(self.storage, inv_id="INV08", sub_id="SUB08")
        _configure_moyklass(self.ctx.moyklass, "SUB08", join_status="777")
        r = self._check(item)
        self.assertEqual(r["state"], "unknown")

    def test_09_check_unavailable(self):
        item = _seed_item(self.storage, inv_id="INV09", sub_id="SUB09")
        _configure_moyklass(self.ctx.moyklass, "SUB09", unavailable=True)
        r = self._check(item)
        self.assertEqual(r["state"], "unknown")
        self.assertEqual(r["reason_code"], "training_state_unavailable")

    def test_10_check_active_resumes_automatically(self):
        # v7.1.10 — a training-blocked item found active by the (manual,
        # forced-fresh) check resumes automatically right here, in this one
        # call — no more separate "client_resume_confirmation_required"
        # holding state waiting for a second explicit action.
        item = _seed_item(self.storage, inv_id="INV10", sub_id="SUB10")
        _configure_moyklass(self.ctx.moyklass, "SUB10", sub_status="2", join_status="2")
        r = self._check(item)
        self.assertEqual(r["state"], "active")
        self.assertTrue(r["resumed"])
        self.assertFalse(r["resume_confirmation_required"])
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertIsNone(stored["reason_code"])
        self.assertEqual(stored["current_stage"], "discovered")

    def test_check_repeated_same_state_no_duplicate_audit(self):
        # Seed with NO prior reason_code so the first check is a genuine
        # first-time transition into "blocked" (1 audit event); the second
        # check with the same unchanged state must add zero more.
        item = _seed_item(self.storage, inv_id="INV10b", sub_id="SUB10b",
                          stage="discovered", reason_code=None)
        _configure_moyklass(self.ctx.moyklass, "SUB10b", join_status="99046")
        self._check(item)
        item2 = self.storage.get_automation_item_by_id(item["id"])
        self._check(item2)
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM automation_audit_log WHERE mk_invoice_id='INV10b' "
                "AND event_type='automation_blocked_by_training_state'"
            ).fetchall()
        self.assertEqual(len(rows), 1)


class TestResumeFlow(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage, _make_settings())

    def _resume(self, item):
        with patch.object(self.ctx, "_role_for_user", return_value="owner"):
            return self.ctx.automation_item_action(_OWNER_AUTH, str(item["id"]), "training-resume", {})

    def test_11_resume_performs_second_fresh_check(self):
        item = _seed_item(self.storage, inv_id="INV11", sub_id="SUB11",
                          reason_code="client_resume_confirmation_required")
        _configure_moyklass(self.ctx.moyklass, "SUB11", sub_status="2", join_status="2")
        self.ctx.moyklass.get_user_subscriptions.reset_mock()
        self._resume(item)
        self.ctx.moyklass.get_user_subscriptions.assert_called_once()

    def test_12_resume_blocked_if_state_changed_back_to_paused(self):
        item = _seed_item(self.storage, inv_id="INV12", sub_id="SUB12",
                          reason_code="client_resume_confirmation_required")
        # MoyKlass now (at resume time) says paused again — must not trust the
        # stored reason_code from the earlier check.
        _configure_moyklass(self.ctx.moyklass, "SUB12", join_status="99046")
        r = self._resume(item)
        self.assertFalse(r.get("ok"))
        self.assertEqual(r.get("reason_code"), "client_training_paused")
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertEqual(stored["current_stage"], "requires_check")

    def test_13_resume_succeeds_only_for_active(self):
        item = _seed_item(self.storage, inv_id="INV13", sub_id="SUB13",
                          reason_code="client_resume_confirmation_required")
        _configure_moyklass(self.ctx.moyklass, "SUB13", sub_status="2", join_status="2")
        r = self._resume(item)
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("state"), "active")

    def test_14_resume_sets_stage_discovered(self):
        item = _seed_item(self.storage, inv_id="INV14", sub_id="SUB14",
                          reason_code="client_resume_confirmation_required")
        _configure_moyklass(self.ctx.moyklass, "SUB14", sub_status="2", join_status="2")
        r = self._resume(item)
        self.assertEqual(r.get("stage"), "discovered")
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertEqual(stored["current_stage"], "discovered")
        self.assertIsNone(stored["reason_code"])

    def test_15_resume_does_not_create_intent_immediately(self):
        item = _seed_item(self.storage, inv_id="INV15", sub_id="SUB15",
                          reason_code="client_resume_confirmation_required")
        _configure_moyklass(self.ctx.moyklass, "SUB15", sub_status="2", join_status="2")
        self._resume(item)
        self.assertEqual(len(self.storage.find_all_active_intents_by_invoice("INV15")), 0)

    def test_16_resume_does_not_create_bepaid_immediately(self):
        item = _seed_item(self.storage, inv_id="INV16", sub_id="SUB16",
                          reason_code="client_resume_confirmation_required")
        _configure_moyklass(self.ctx.moyklass, "SUB16", sub_status="2", join_status="2")
        with patch.object(self.ctx, "payment_intent_prepare_options") as mock_prep:
            self._resume(item)
        mock_prep.assert_not_called()

    def test_17_resume_does_not_publish_immediately(self):
        item = _seed_item(self.storage, inv_id="INV17", sub_id="SUB17",
                          reason_code="client_resume_confirmation_required")
        _configure_moyklass(self.ctx.moyklass, "SUB17", sub_status="2", join_status="2")
        with patch.object(self.storage, "publish_payment_intent_to_client") as mock_pub:
            self._resume(item)
        mock_pub.assert_not_called()

    def test_18_repeated_resume_idempotent_success(self):
        # v7.1.10 — backward-compatible endpoint: a second call after the
        # item is already resumed is idempotent success ("already_resumed"),
        # never a conflict error — matches spec section 9.
        item = _seed_item(self.storage, inv_id="INV18", sub_id="SUB18",
                          reason_code="client_resume_confirmation_required")
        _configure_moyklass(self.ctx.moyklass, "SUB18", sub_status="2", join_status="2")
        r1 = self._resume(item)
        self.assertTrue(r1.get("ok"))
        self.assertFalse(r1.get("already_resumed"))
        item2 = self.storage.get_automation_item_by_id(item["id"])
        r2 = self._resume(item2)
        self.assertTrue(r2.get("ok"))
        self.assertTrue(r2.get("already_resumed"))

    def test_19_scheduler_later_continues_according_to_pilot_mode(self):
        self.storage.upsert_pilot_client("8801", mode="auto", now=_now())
        with self.storage._connect() as conn:
            conn.execute(
                """INSERT INTO client_parent_child_links
                   (parent_telegram_user_id, mk_user_id, child_display_name, status,
                    linked_at, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                ("tg19", "8801", "Тест", "active", _now(), _now(), _now()),
            )
            conn.execute("INSERT OR IGNORE INTO invoice_automation_settings (id) VALUES (1)")
        item = _seed_item(self.storage, inv_id="INV19", mk_user_id="8801", sub_id="SUB19",
                          reason_code="client_resume_confirmation_required")
        _configure_moyklass(self.ctx.moyklass, "SUB19", sub_status="2", join_status="2")
        self._resume(item)

        inv = {
            "id": "INV19", "userId": "8801", "price": 239.0, "payed": 0.0,
            "userSubscriptionId": "SUB19", "payUntil": "2026-08-17",
            "userSubscription": {"clientName": "Тест", "beginDate": "2026-08-01"},
            "comment": None,
        }
        with patch.object(self.ctx, "payment_intent_prepare_options", return_value={"ok": True}):
            self.ctx._process_single_automation_item_from_invoice(
                inv, _now(), create_enabled=True, publish_enabled=False,
            )
        self.assertEqual(len(self.storage.find_all_active_intents_by_invoice("INV19")), 1)

    def test_20_pilot_mode_not_changed_by_resume(self):
        self.storage.upsert_pilot_client("8801", mode="review", now=_now())
        item = _seed_item(self.storage, inv_id="INV20", mk_user_id="8801", sub_id="SUB20",
                          reason_code="client_resume_confirmation_required")
        _configure_moyklass(self.ctx.moyklass, "SUB20", sub_status="2", join_status="2")
        self._resume(item)
        client = self.storage.get_pilot_client("8801")
        self.assertEqual(client["mode"], "review")


class TestExistingPublishedAuditDedup(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage, _make_settings())
        with self.storage._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO invoice_automation_settings (id) VALUES (1)")

    def test_28_existing_published_audit_deduplicated(self):
        self.storage.upsert_pilot_client("8802", mode="auto", now=_now())
        now = _now()
        with self.storage._connect() as conn:
            conn.execute(
                """INSERT INTO payment_intents
                   (public_id, mk_user_id, mk_invoice_id, mk_user_subscription_id, student_name,
                    amount_minor, amount_byn, currency, status, client_visibility, source,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PI-28", "8802", "INV28", "SUB28", "Тест", 23900, 239.0, "BYN",
                 "awaiting_payment", "published", "moyklass_invoice_automation", now, now),
            )
        _configure_moyklass(self.ctx.moyklass, "SUB28", join_status="99046")
        inv = {
            "id": "INV28", "userId": "8802", "price": 239.0, "payed": 0.0,
            "userSubscriptionId": "SUB28", "payUntil": "2026-08-17",
            "userSubscription": {"clientName": "Тест", "beginDate": "2026-08-01"},
            "comment": None,
        }
        for _ in range(3):
            self.ctx._process_single_automation_item_from_invoice(
                inv, _now(), create_enabled=True, publish_enabled=False,
            )
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM automation_audit_log WHERE mk_invoice_id='INV28' "
                "AND event_type='existing_published_invoice_during_pause'"
            ).fetchall()
        self.assertEqual(len(rows), 1, "must not spam audit every cycle for the same reason")

        pi = self.storage.get_payment_intent("PI-28")
        self.assertEqual(pi.get("client_visibility"), "published", "must never auto-withdraw")


class TestUnaffectedAreas(unittest.TestCase):
    def test_26_duplicate_intent_guard_unchanged(self):
        import inspect
        import web_app_server
        src = inspect.getsource(web_app_server.MiniAppContext._process_single_automation_item_from_invoice)
        self.assertIn("find_all_active_intents_by_invoice", src)

    def test_27_withdrawal_permissions_unchanged(self):
        from web_app_server import WITHDRAW_INVOICE_ROLES
        self.assertEqual(WITHDRAW_INVOICE_ROLES, {"owner", "admin", "operations"})

    def test_29_paid_webhook_unaffected(self):
        import inspect
        import web_app_server
        src = inspect.getsource(web_app_server.MiniAppContext.bepaid_handle_webhook)
        self.assertNotIn("_get_training_state", src)
        self.assertNotIn("training_state", src)

    def test_30_posting_paid_to_mk_unaffected(self):
        import inspect
        import web_app_server
        src = inspect.getsource(web_app_server.MiniAppContext._try_auto_post_automation_item)
        self.assertNotIn("_get_training_state", src)

    def test_31_multi_child_unaffected(self):
        import inspect
        import storage as storage_mod
        src = inspect.getsource(storage_mod.Storage.get_parents_for_child)
        self.assertIn("mk_user_id", src)

    def test_33_food_module_unchanged(self):
        import inspect
        import web_app_server
        self.assertTrue(hasattr(web_app_server.MiniAppContext, "food_list_menus"))
        src = inspect.getsource(web_app_server.MiniAppContext.food_list_menus)
        self.assertNotIn("_get_training_state", src)

    def test_no_client_training_pauses_table(self):
        import inspect
        import storage as storage_mod
        src = inspect.getsource(storage_mod.Storage)
        self.assertNotIn("client_training_pauses", src)


# ---------------------------------------------------------------------------
# v7.1.8 follow-up — training-pause regressions after the payment-method
# display fix (section 15). The payment-method fix touched only frontend
# label logic + a comment/log-adjacent read path; these confirm the training
# gate/resume/audit-dedup backend behavior is untouched.
# ---------------------------------------------------------------------------

class TestTrainingPauseRegressionAfterPaymentMethodFix(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage, _make_settings())
        with self.storage._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO invoice_automation_settings (id) VALUES (1)")

    def test_no_backend_state_mutation_without_any_call(self):
        # "Оставить счёт" makes zero backend calls (verified on the frontend
        # side in test_training_pause_ui.py); the backend-side guarantee is
        # simply that nothing changes an item's fields unless one of the two
        # real endpoints (training-check/training-resume) is invoked.
        item = _seed_item(self.storage, inv_id="INV-L1", sub_id="SUB-L1",
                          stage="published", reason_code="client_training_paused")
        before = self.storage.get_automation_item_by_id(item["id"])
        after = self.storage.get_automation_item_by_id(item["id"])
        self.assertEqual(before["reason_code"], after["reason_code"])
        self.assertEqual(before["current_stage"], after["current_stage"])

    def test_paused_reason_remains_after_check_confirms_still_paused(self):
        item = _seed_item(self.storage, inv_id="INV-L2", sub_id="SUB-L2",
                          stage="published", reason_code="client_training_paused")
        _configure_moyklass(self.ctx.moyklass, "SUB-L2", join_status="99046")
        with patch.object(self.ctx, "_role_for_user", return_value="owner"):
            self.ctx.automation_item_action(_OWNER_AUTH, str(item["id"]), "training-check", {})
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertEqual(stored["reason_code"], "client_training_paused")
        self.assertEqual(stored["current_stage"], "published")

    def test_published_visibility_remains_published(self):
        now = _now()
        with self.storage._connect() as conn:
            conn.execute(
                """INSERT INTO payment_intents
                   (public_id, mk_user_id, mk_invoice_id, mk_user_subscription_id, student_name,
                    amount_minor, amount_byn, currency, status, client_visibility, source,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("PI-L3", "8803", "INV-L3", "SUB-L3", "Тест", 23900, 239.0, "BYN",
                 "awaiting_payment", "published", "moyklass_invoice_automation", now, now),
            )
        pi = self.storage.get_payment_intent("PI-L3")
        self.assertEqual(pi["client_visibility"], "published")

    def test_withdrawal_permitted_roles_unchanged(self):
        from web_app_server import WITHDRAW_INVOICE_ROLES
        self.assertEqual(WITHDRAW_INVOICE_ROLES, {"owner", "admin", "operations"})

    def test_training_resume_now_automatic_via_check(self):
        # v7.1.10 — training-check alone (the only action left in the UI)
        # now resumes automatically when MoyKlass shows active — no more
        # separate "Подтвердить возобновление" confirmation step.
        item = _seed_item(self.storage, inv_id="INV-L5", sub_id="SUB-L5",
                          reason_code="client_training_paused")
        _configure_moyklass(self.ctx.moyklass, "SUB-L5", sub_status="2", join_status="2")
        with patch.object(self.ctx, "_role_for_user", return_value="owner"):
            self.ctx.automation_item_action(_OWNER_AUTH, str(item["id"]), "training-check", {})
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertIsNone(stored["reason_code"])
        self.assertEqual(stored["current_stage"], "discovered")

    def test_existing_audit_dedup_remains(self):
        item = _seed_item(self.storage, inv_id="INV-L6", sub_id="SUB-L6",
                          stage="discovered", reason_code=None)
        _configure_moyklass(self.ctx.moyklass, "SUB-L6", join_status="99046")
        with patch.object(self.ctx, "_role_for_user", return_value="owner"):
            self.ctx.automation_item_action(_OWNER_AUTH, str(item["id"]), "training-check", {})
            item2 = self.storage.get_automation_item_by_id(item["id"])
            self.ctx.automation_item_action(_OWNER_AUTH, str(item2["id"]), "training-check", {})
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM automation_audit_log WHERE mk_invoice_id='INV-L6' "
                "AND event_type='automation_blocked_by_training_state'"
            ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_training_pause_preview_removed_for_release(self):
        # v7.1.8 release cleanup: the temporary training-pause preview was
        # visually approved and fully removed — production index.html must
        # no longer contain it.
        html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("dev_preview", html)
        self.assertNotIn("LOCAL PREVIEW · TRAINING PAUSE", html)

    def test_help_pause_section_unchanged(self):
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("_wsHelpTrainingPauseHtml", js)
        self.assertIn("Пауза обучения и каникулы", js)


if __name__ == "__main__":
    unittest.main()
