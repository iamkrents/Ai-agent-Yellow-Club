"""Tests for v7.1.8 — training-state gate wired into the payment automation pipeline.

Covers the integration of training_state_domain.resolve_training_state into:
  - _process_single_automation_item_from_invoice (discovery + pre-create gate)
  - approve_payment_intent (manual approval re-check)
  - _try_publish_automation_item (publish re-check)
  - per-scan-cycle cache in process_new_moyklass_invoices
  - already-paid/webhook/posting/withdrawal paths remaining unaffected

Uses a real (mocked) MoyKlassClient double so the resolver actually runs on
realistic userSubscriptions/joins payloads shaped like the confirmed
production contract (id/statusId/mainClassId/classIds on subscriptions;
id/classId/statusId on joins). No real MoyKlass/HTTP calls.

Run offline:
    python -m unittest tests.test_payment_training_gate -v
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

CURRENT_VERSION = "7.1.9"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.telegram_bot_token = "test_token_123"
    s.payment_parent_notifications_enabled = True
    s.payment_invoice_automation_enabled = True
    s.web_app_url = "https://t.me/app"
    s.bepaid_erip_shop_id = "erip_shop"
    s.bepaid_erip_secret_key = "erip_secret"
    s.bepaid_request_timeout = 30
    s.payment_default_due_days = 14
    s.bepaid_auto_post_to_moyklass = False
    s.payment_parent_notifications_enabled = False
    return s


class _FakeResult:
    def __init__(self, data, ok=True):
        self.data = data
        self.ok = ok
        self.error = None if ok else "boom"


def _sub(sub_id, status_id="2", main_class_id="900000", class_ids=None):
    return {
        "id": sub_id, "statusId": status_id, "mainClassId": main_class_id,
        "classIds": class_ids if class_ids is not None else [main_class_id],
    }


def _join(join_id, class_id="900000", status_id="2"):
    return {"id": join_id, "classId": class_id, "statusId": status_id}


def _configure_moyklass(
    mk: MagicMock, sub_id: str, *, sub_status="2", join_status="2",
    class_id="900000", unavailable=False,
) -> None:
    """Configure mk.get_user_subscriptions/get_user_joins to describe ONE
    subscription/join pair matching sub_id/class_id, with the given statuses."""
    if unavailable:
        mk.get_user_subscriptions.return_value = _FakeResult({}, ok=False)
        mk.get_user_joins.return_value = _FakeResult({}, ok=False)
        return
    mk.get_user_subscriptions.return_value = _FakeResult(
        {"items": [_sub(sub_id, status_id=sub_status, main_class_id=class_id)]}
    )
    mk.get_user_joins.return_value = _FakeResult(
        {"items": [_join("J-" + str(sub_id), class_id=class_id, status_id=join_status)]}
    )


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


def _make_invoice(inv_id="INV900", mk_user_id="9900", sub_id="SUB900", price=239.0) -> dict:
    return {
        "id": inv_id, "userId": mk_user_id, "price": price, "payed": 0.0,
        "userSubscriptionId": sub_id, "payUntil": "2026-08-17",
        "userSubscription": {"clientName": "Тест Ученик", "beginDate": "2026-08-01"},
        "comment": None,
    }


def _seed_intent(
    storage: Storage, public_id: str, mk_user_id="9900", mk_invoice_id="INV900",
    mk_user_subscription_id="SUB900", status="draft", client_visibility="hidden",
    source="moyklass_invoice_automation",
) -> None:
    now = _now()
    with storage._connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO payment_intents
               (public_id, mk_user_id, mk_invoice_id, mk_user_subscription_id, student_name,
                amount_minor, amount_byn, currency, status,
                client_visibility, source, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                public_id, int(mk_user_id), mk_invoice_id, mk_user_subscription_id, "Тест",
                23900, 239.0, "BYN", status, client_visibility, source, now, now,
            ),
        )


_OWNER_AUTH = {"_internal": False, "role": "owner", "user_id": "1001", "full_name": "Owner"}
_CM_AUTH = {"_internal": False, "role": "client_manager", "user_id": "1004", "full_name": "CM"}


def _run_single(ctx, inv: dict, cache=None, **kwargs) -> dict:
    defaults = dict(create_enabled=True, publish_enabled=False, post_enabled=False, notify_enabled=False)
    defaults.update(kwargs)
    return ctx._process_single_automation_item_from_invoice(
        inv, _now(), training_cache=cache, **defaults
    )


def _seed_parent_link(storage: Storage, mk_user_id: str, parent_tg_id: str) -> None:
    now = _now()
    with storage._connect() as conn:
        conn.execute(
            """INSERT INTO client_parent_child_links
               (parent_telegram_user_id, mk_user_id, child_display_name, status,
                linked_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (parent_tg_id, mk_user_id, "Тест", "active", now, now, now),
        )


# ---------------------------------------------------------------------------
# Discovery / pre-create gate
# ---------------------------------------------------------------------------

class TestDiscoveryGate(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.settings = _make_settings()
        self.ctx = _make_context(self.storage, self.settings)
        with self.storage._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO invoice_automation_settings (id) VALUES (1)")

    def _enroll(self, mk_user_id, mode="auto"):
        self.storage.upsert_pilot_client(mk_user_id, mode=mode, now=_now())

    def test_01_active_client_unchanged_flow(self):
        self._enroll("9901")
        _seed_parent_link(self.storage, "9901", "tg1")
        _configure_moyklass(self.ctx.moyklass, "SUB901", sub_status="2", join_status="2")
        inv = _make_invoice(inv_id="INV901", mk_user_id="9901", sub_id="SUB901")
        result = _run_single(self.ctx, inv)
        self.assertTrue(result.get("created"), f"active client must create intent: {result}")

    def test_02_paused_before_discovery_no_intent(self):
        self._enroll("9902")
        _seed_parent_link(self.storage, "9902", "tg2")
        _configure_moyklass(self.ctx.moyklass, "SUB902", join_status="99046")
        inv = _make_invoice(inv_id="INV902", mk_user_id="9902", sub_id="SUB902")
        result = _run_single(self.ctx, inv)
        self.assertTrue(result.get("requires_check"))
        self.assertEqual(len(self.storage.find_all_active_intents_by_invoice("INV902")), 0)

    def test_03_frozen_subscription_before_discovery_no_intent(self):
        self._enroll("9903")
        _seed_parent_link(self.storage, "9903", "tg3")
        _configure_moyklass(self.ctx.moyklass, "SUB903", sub_status="3", join_status="2")
        inv = _make_invoice(inv_id="INV903", mk_user_id="9903", sub_id="SUB903")
        result = _run_single(self.ctx, inv)
        self.assertTrue(result.get("requires_check"))
        item = self.storage.get_automation_item_by_id(
            self.storage.upsert_automation_item("INV903", "9903", "T", "{}", _now())["id"]
        )
        self.assertEqual(item.get("reason_code"), "training_subscription_frozen")
        self.assertEqual(len(self.storage.find_all_active_intents_by_invoice("INV903")), 0)

    def test_04_finished_before_discovery_no_intent(self):
        self._enroll("9904")
        _seed_parent_link(self.storage, "9904", "tg4")
        _configure_moyklass(self.ctx.moyklass, "SUB904", join_status="1")
        inv = _make_invoice(inv_id="INV904", mk_user_id="9904", sub_id="SUB904")
        result = _run_single(self.ctx, inv)
        self.assertTrue(result.get("requires_check"))
        self.assertEqual(len(self.storage.find_all_active_intents_by_invoice("INV904")), 0)

    def test_05_unknown_before_discovery_no_intent(self):
        self._enroll("9905")
        _seed_parent_link(self.storage, "9905", "tg5")
        _configure_moyklass(self.ctx.moyklass, "SUB905", join_status="777777")
        inv = _make_invoice(inv_id="INV905", mk_user_id="9905", sub_id="SUB905")
        result = _run_single(self.ctx, inv)
        self.assertTrue(result.get("requires_check"))
        self.assertEqual(len(self.storage.find_all_active_intents_by_invoice("INV905")), 0)

    def test_06_training_api_unavailable_fail_closed(self):
        self._enroll("9906")
        _seed_parent_link(self.storage, "9906", "tg6")
        _configure_moyklass(self.ctx.moyklass, "SUB906", unavailable=True)
        inv = _make_invoice(inv_id="INV906", mk_user_id="9906", sub_id="SUB906")
        result = _run_single(self.ctx, inv)
        self.assertTrue(result.get("requires_check"))
        self.assertEqual(len(self.storage.find_all_active_intents_by_invoice("INV906")), 0)

    def test_07_one_api_failure_does_not_stop_other_invoices(self):
        # Two different clients in the same cache dict; one MoyKlass call fails.
        self._enroll("9907")
        self._enroll("9908")
        _seed_parent_link(self.storage, "9907", "tg7")
        _seed_parent_link(self.storage, "9908", "tg8")
        cache: dict = {}

        _configure_moyklass(self.ctx.moyklass, "SUB907", unavailable=True)
        inv1 = _make_invoice(inv_id="INV907", mk_user_id="9907", sub_id="SUB907")
        r1 = _run_single(self.ctx, inv1, cache=cache)
        self.assertTrue(r1.get("requires_check"))

        _configure_moyklass(self.ctx.moyklass, "SUB908", sub_status="2", join_status="2")
        inv2 = _make_invoice(inv_id="INV908", mk_user_id="9908", sub_id="SUB908")
        r2 = _run_single(self.ctx, inv2, cache=cache)
        self.assertTrue(r2.get("created"), f"second client must succeed independently: {r2}")

    def test_08_multiple_invoices_one_client_use_per_cycle_cache(self):
        self._enroll("9909")
        _seed_parent_link(self.storage, "9909", "tg9")
        _configure_moyklass(self.ctx.moyklass, "SUB909", sub_status="2", join_status="2")
        cache: dict = {}
        inv1 = _make_invoice(inv_id="INV909A", mk_user_id="9909", sub_id="SUB909")
        _run_single(self.ctx, inv1, cache=cache)
        calls_after_first = self.ctx.moyklass.get_user_subscriptions.call_count
        inv2 = _make_invoice(inv_id="INV909B", mk_user_id="9909", sub_id="SUB909")
        _run_single(self.ctx, inv2, cache=cache)
        calls_after_second = self.ctx.moyklass.get_user_subscriptions.call_count
        # 1st invoice: 1 call for the cached discovery check + 1 forced-fresh call
        # before bePaid creation = 2. 2nd invoice (same client, same cycle): the
        # discovery check is served from cache (0 calls), only the mandatory
        # forced-fresh pre-bePaid check hits the API again (+1) — delta of 1,
        # not 2, proving the per-cycle cache is actually being reused.
        self.assertEqual(
            calls_after_second - calls_after_first, 1,
            "second invoice for the same client within one cycle must reuse the "
            "cache for the discovery check (only the mandatory forced-fresh "
            "pre-bePaid check should add a new API call)",
        )

    def test_09_repeated_scheduler_no_duplicate_intent(self):
        self._enroll("9910")
        _seed_parent_link(self.storage, "9910", "tg10")
        _configure_moyklass(self.ctx.moyklass, "SUB910", sub_status="2", join_status="2")
        inv = _make_invoice(inv_id="INV910", mk_user_id="9910", sub_id="SUB910")
        _run_single(self.ctx, inv)
        _run_single(self.ctx, inv)
        intents = self.storage.find_all_active_intents_by_invoice("INV910")
        self.assertEqual(len(intents), 1)

    def test_10_repeated_scheduler_no_duplicate_identical_audit_events(self):
        self._enroll("9911")
        _seed_parent_link(self.storage, "9911", "tg11")
        _configure_moyklass(self.ctx.moyklass, "SUB911", join_status="99046")
        inv = _make_invoice(inv_id="INV911", mk_user_id="9911", sub_id="SUB911")
        _run_single(self.ctx, inv)
        _run_single(self.ctx, inv)
        _run_single(self.ctx, inv)
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM automation_audit_log WHERE mk_invoice_id='INV911' "
                "AND event_type='automation_blocked_by_training_state'"
            ).fetchall()
        self.assertEqual(len(rows), 1, "unchanged reason_code must not re-log every cycle")

    def test_11_observe_mode_detects_state_creates_nothing(self):
        self._enroll("9912", mode="observe")
        _configure_moyklass(self.ctx.moyklass, "SUB912", join_status="99046")
        inv = _make_invoice(inv_id="INV912", mk_user_id="9912", sub_id="SUB912")
        result = _run_single(self.ctx, inv)
        self.assertTrue(result.get("skip"))
        self.assertEqual(len(self.storage.find_all_active_intents_by_invoice("INV912")), 0)

    def test_12_review_mode_paused_creates_no_bepaid(self):
        self._enroll("9913", mode="review")
        _seed_parent_link(self.storage, "9913", "tg13")
        _configure_moyklass(self.ctx.moyklass, "SUB913", join_status="99046")
        inv = _make_invoice(inv_id="INV913", mk_user_id="9913", sub_id="SUB913")
        result = _run_single(self.ctx, inv)
        self.assertTrue(result.get("requires_check"))
        self.assertEqual(len(self.storage.find_all_active_intents_by_invoice("INV913")), 0)

    def test_13_auto_mode_paused_creates_no_bepaid(self):
        self._enroll("9914", mode="auto")
        _seed_parent_link(self.storage, "9914", "tg14")
        _configure_moyklass(self.ctx.moyklass, "SUB914", join_status="99046")
        inv = _make_invoice(inv_id="INV914", mk_user_id="9914", sub_id="SUB914")
        with patch.object(self.ctx, "payment_intent_prepare_options") as mock_prep:
            _run_single(self.ctx, inv)
        mock_prep.assert_not_called()

    def test_14_disabled_not_in_pilot_no_training_api_call(self):
        # not_in_pilot: never even enrolled
        inv = _make_invoice(inv_id="INV915", mk_user_id="9915", sub_id="SUB915")
        _run_single(self.ctx, inv)
        self.ctx.moyklass.get_user_subscriptions.assert_not_called()
        self.ctx.moyklass.get_user_joins.assert_not_called()

    def test_15_pause_after_intent_creation_before_approve_blocks_approve(self):
        self._enroll("9916", mode="review")
        _seed_parent_link(self.storage, "9916", "tg16")
        _configure_moyklass(self.ctx.moyklass, "SUB916", sub_status="2", join_status="2")
        inv = _make_invoice(inv_id="INV916", mk_user_id="9916", sub_id="SUB916")
        create_result = _run_single(self.ctx, inv)
        self.assertTrue(create_result.get("review_pending"))
        intents = self.storage.find_all_active_intents_by_invoice("INV916")
        self.assertEqual(len(intents), 1)
        public_id = intents[0]["public_id"]

        # Student goes on pause before the manager approves.
        _configure_moyklass(self.ctx.moyklass, "SUB916", join_status="99046")
        with patch.object(self.ctx, "_role_for_user", return_value="owner"):
            approve_result = self.ctx.approve_payment_intent(_OWNER_AUTH, public_id)
        self.assertFalse(approve_result.get("ok"))
        self.assertEqual(approve_result.get("reason_code"), "client_training_paused")
        pi = self.storage.get_payment_intent(public_id)
        self.assertNotEqual(pi.get("client_visibility"), "published")

    def test_16_pause_after_bepaid_before_publish_blocks_publish(self):
        self._enroll("9917", mode="auto")
        _seed_parent_link(self.storage, "9917", "tg17")
        _configure_moyklass(self.ctx.moyklass, "SUB917", sub_status="2", join_status="2")
        inv = _make_invoice(inv_id="INV917", mk_user_id="9917", sub_id="SUB917")
        with patch.object(self.ctx, "payment_intent_prepare_options", return_value={"ok": True}):
            _run_single(self.ctx, inv, publish_enabled=False)
        intents = self.storage.find_all_active_intents_by_invoice("INV917")
        self.assertEqual(len(intents), 1)
        item = self.storage.upsert_automation_item("INV917", "9917", "T", "{}", _now())

        # Now student pauses before a later cycle attempts to publish.
        _configure_moyklass(self.ctx.moyklass, "SUB917", join_status="99046")
        result = self.ctx._try_publish_automation_item(
            item["id"], intents[0], "9917", _now(), False, auto_publish_eligible=1,
        )
        self.assertNotIn("published", result)
        pi = self.storage.get_payment_intent(intents[0]["public_id"])
        self.assertNotEqual(pi.get("client_visibility"), "published")

    def test_17_already_published_invoice_not_withdrawn_automatically(self):
        self._enroll("9918", mode="auto")
        _seed_intent(self.storage, "PI-918", mk_user_id="9918", mk_invoice_id="INV918",
                     mk_user_subscription_id="SUB918", status="awaiting_payment",
                     client_visibility="published")
        _configure_moyklass(self.ctx.moyklass, "SUB918", join_status="99046")
        inv = _make_invoice(inv_id="INV918", mk_user_id="9918", sub_id="SUB918")
        _run_single(self.ctx, inv)
        pi = self.storage.get_payment_intent("PI-918")
        self.assertEqual(pi.get("client_visibility"), "published",
                          "must never auto-withdraw a published invoice due to pause")

    def test_18_already_paid_webhook_path_unaffected_by_pause(self):
        # Simulated by directly checking payment_domain state predicates are
        # untouched by training state — no webhook call routes through the
        # training gate at all (bepaid_handle_webhook never calls
        # _get_training_state). Structural guarantee, verified via source scan.
        import inspect
        import web_app_server
        src = inspect.getsource(web_app_server.MiniAppContext.bepaid_handle_webhook)
        self.assertNotIn("_get_training_state", src)

    def test_19_posting_paid_intent_to_mk_unaffected_by_pause(self):
        import inspect
        import web_app_server
        src = inspect.getsource(web_app_server.MiniAppContext._try_auto_post_automation_item)
        self.assertNotIn("_get_training_state", src)

    def test_20_withdrawn_invoice_remains_withdrawn(self):
        self._enroll("9919", mode="auto")
        _seed_intent(self.storage, "PI-919", mk_user_id="9919", mk_invoice_id="INV919",
                     mk_user_subscription_id="SUB919", status="awaiting_payment",
                     client_visibility="published")
        self.storage.withdraw_payment_intent_from_client("PI-919", withdrawn_by="admin", now=_now())
        _configure_moyklass(self.ctx.moyklass, "SUB919", sub_status="2", join_status="2")
        inv = _make_invoice(inv_id="INV919", mk_user_id="9919", sub_id="SUB919")
        _run_single(self.ctx, inv)
        pi = self.storage.get_payment_intent("PI-919")
        self.assertEqual(pi.get("client_visibility"), "withdrawn")

    def test_21_active_sibling_class_unaffected_by_paused_unrelated_class(self):
        self._enroll("9920", mode="auto")
        _seed_parent_link(self.storage, "9920", "tg20")
        mk = self.ctx.moyklass
        mk.get_user_subscriptions.return_value = _FakeResult(
            {"items": [_sub("SUB920", status_id="2", main_class_id="CLASS_A")]}
        )
        mk.get_user_joins.return_value = _FakeResult({"items": [
            _join("JA", class_id="CLASS_A", status_id="2"),
            _join("JB", class_id="CLASS_B", status_id="99046"),
        ]})
        inv = _make_invoice(inv_id="INV920", mk_user_id="9920", sub_id="SUB920")
        result = _run_single(self.ctx, inv)
        self.assertTrue(result.get("created"), f"unrelated paused class must not block: {result}")

    def test_22_parent_telegram_link_remains_active(self):
        self._enroll("9921", mode="auto")
        _seed_parent_link(self.storage, "9921", "tg21")
        _configure_moyklass(self.ctx.moyklass, "SUB921", join_status="99046")
        inv = _make_invoice(inv_id="INV921", mk_user_id="9921", sub_id="SUB921")
        _run_single(self.ctx, inv)
        parents = self.storage.get_parents_for_child("9921")
        self.assertEqual(len(parents), 1)
        self.assertEqual(parents[0]["status"], "active")

    def test_23_no_duplicate_bepaid(self):
        self._enroll("9922", mode="auto")
        _seed_parent_link(self.storage, "9922", "tg22")
        _configure_moyklass(self.ctx.moyklass, "SUB922", sub_status="2", join_status="2")
        inv = _make_invoice(inv_id="INV922", mk_user_id="9922", sub_id="SUB922")
        with patch.object(self.ctx, "payment_intent_prepare_options", return_value={"ok": True}) as mock_prep:
            _run_single(self.ctx, inv)
            _run_single(self.ctx, inv)
        self.assertEqual(mock_prep.call_count, 1)

    def test_24_no_duplicate_publish(self):
        self._enroll("9923", mode="auto")
        _seed_parent_link(self.storage, "9923", "tg23")
        _configure_moyklass(self.ctx.moyklass, "SUB923", sub_status="2", join_status="2")
        inv = _make_invoice(inv_id="INV923", mk_user_id="9923", sub_id="SUB923")
        with patch.object(self.ctx, "payment_intent_prepare_options", return_value={"ok": True}):
            _run_single(self.ctx, inv, publish_enabled=True)
        intents = self.storage.find_all_active_intents_by_invoice("INV923")
        item = self.storage.upsert_automation_item("INV923", "9923", "T", "{}", _now())
        result2 = self.ctx._try_publish_automation_item(
            item["id"], intents[0], "9923", _now(), False, auto_publish_eligible=1,
        )
        self.assertNotIn("published", result2, "already-published intent must not re-publish")

    def test_25_raw_status_from_frontend_ignored(self):
        # approve_payment_intent takes no client-supplied training-state field at all.
        import inspect
        import web_app_server
        sig = inspect.signature(web_app_server.MiniAppContext.approve_payment_intent)
        self.assertNotIn("training_state", sig.parameters)
        self.assertNotIn("state", sig.parameters)

    def test_26_unauthorized_approve_remains_denied(self):
        self._enroll("9924", mode="review")
        _seed_intent(self.storage, "PI-924", mk_user_id="9924", mk_invoice_id="INV924",
                     mk_user_subscription_id="SUB924", status="draft")
        with patch.object(self.ctx, "_role_for_user", return_value="teacher"):
            result = self.ctx.approve_payment_intent(
                {"_internal": False, "role": "teacher", "user_id": "1005", "full_name": "T"},
                "PI-924",
            )
        self.assertFalse(result.get("ok"))
        self.ctx.moyklass.get_user_subscriptions.assert_not_called()

    def test_27_existing_permissions_unchanged(self):
        from web_app_server import PAYMENT_APPROVAL_ROLES
        self.assertEqual(
            PAYMENT_APPROVAL_ROLES, {"owner", "admin", "operations", "client_manager"},
        )

    def test_28_food_module_unaffected(self):
        import inspect
        import web_app_server
        self.assertTrue(hasattr(web_app_server.MiniAppContext, "food_list_menus"))
        src = inspect.getsource(web_app_server.MiniAppContext.food_list_menus)
        self.assertNotIn("_get_training_state", src)

    def test_29_pilot_fail_closed_unchanged(self):
        # not enrolled -> still skip regardless of training state mock
        _configure_moyklass(self.ctx.moyklass, "SUB925", sub_status="2", join_status="2")
        inv = _make_invoice(inv_id="INV925", mk_user_id="9925", sub_id="SUB925")
        result = _run_single(self.ctx, inv)
        self.assertTrue(result.get("skip"))

    def test_30_payment_visibility_joins_unchanged(self):
        # get_parents_for_child / client_visible payment query still keyed by mk_user_id,
        # never touched by this feature.
        import inspect
        import storage as storage_mod
        src = inspect.getsource(storage_mod.Storage.get_parents_for_child)
        self.assertIn("mk_user_id", src)


class TestResumeConfirmation(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.settings = _make_settings()
        self.ctx = _make_context(self.storage, self.settings)
        with self.storage._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO invoice_automation_settings (id) VALUES (1)")
        self.storage.upsert_pilot_client("9930", mode="auto", now=_now())
        _seed_parent_link(self.storage, "9930", "tg30")

    def test_resume_requires_manual_confirmation_not_silent(self):
        _configure_moyklass(self.ctx.moyklass, "SUB930", join_status="99046")
        inv = _make_invoice(inv_id="INV930", mk_user_id="9930", sub_id="SUB930")
        r1 = _run_single(self.ctx, inv)
        self.assertTrue(r1.get("requires_check"))

        # MoyKlass now shows active again.
        _configure_moyklass(self.ctx.moyklass, "SUB930", sub_status="2", join_status="2")
        r2 = _run_single(self.ctx, inv)
        self.assertTrue(r2.get("requires_check"), "must not silently auto-resume")
        item = self.storage.upsert_automation_item("INV930", "9930", "T", "{}", _now())
        self.assertEqual(item.get("reason_code"), "client_resume_confirmation_required")
        self.assertEqual(len(self.storage.find_all_active_intents_by_invoice("INV930")), 0)


if __name__ == "__main__":
    unittest.main()
