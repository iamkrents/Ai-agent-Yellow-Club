"""Tests for v7.1.10 — automatic payment-automation resume after a training
pause, without the manual "Подтвердить возобновление" confirmation step.

Covers:
  - _resolve_training_resume_stage state-machine restoration (5.1-5.6):
    pre-intent, existing intent (bePaid not yet created), payment options
    created, published, terminal states, historical
    client_resume_confirmation_required, and preserving an independent
    non-training block reason.
  - idempotency: repeated active cycles, pause -> active -> pause reopen,
    no duplicate audit/incident work.
  - pilot mode semantics after resume: observe / review / auto / disabled /
    missing pilot record.
  - Guardian integration: automatic detection without Mini App / manual
    button, financial side-effect isolation.
  - the training_resumed_automatically audit event's fields.
  - incident resolve/reopen via Diagnostics' recovered_incidents.
  - API backward compatibility (training-resume endpoint).

Most permission/ownership/MoyKlass-outage/ambiguous-join/frozen-subscription/
ordinary-Guardian-cycle coverage already lives in test_training_resume.py,
test_payment_training_gate.py, and test_payment_automation_guardian.py (all
updated for v7.1.10 automatic resume) — this file focuses on what's new:
the resume stage-restoration logic itself, pilot-mode-after-resume, and the
new audit/incident-recovery surface.

Run offline (mocked MoyKlass, temp SQLite file):
    python -m unittest tests.test_automatic_training_resume -v
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from training_state_domain import STATE_ACTIVE, STATE_PAUSED, STATE_UNKNOWN

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")


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


def _make_context(storage: Storage, settings: Optional[MagicMock] = None) -> Any:
    from web_app_server import MiniAppContext
    ctx = MiniAppContext.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = settings or _make_settings()
    mk = MagicMock()
    mk.request.return_value = MagicMock(ok=False, data={})
    ctx.moyklass = mk
    ctx._material_cache = {}
    ctx._mk_comment_cache = {}
    ctx._mk_student_name_cache = {}
    ctx._client_tasks_sync_cache = {"ts": 0.0, "result": {}}
    return ctx


def _seed_item(storage: Storage, *, inv_id="INV-AR1", mk_user_id="8801", sub_id="SUB-AR1",
                stage="requires_check", reason_code="client_training_paused",
                intent_public_id: Optional[str] = None) -> dict:
    now = _now()
    import json as _json
    snapshot = _json.dumps({"id": inv_id, "userId": mk_user_id, "userSubscriptionId": sub_id})
    item = storage.upsert_automation_item(inv_id, mk_user_id, "Тест", snapshot, now)
    storage.update_automation_item_stage(
        item["id"], stage, reason_code=reason_code, readable_reason="test",
        intent_public_id=intent_public_id, now=now,
    )
    return storage.get_automation_item_by_id(item["id"])


def _seed_intent(
    storage: Storage, public_id: str, *, mk_user_id="8801", mk_invoice_id="INV-AR1",
    mk_user_subscription_id="SUB-AR1", status="draft", client_visibility="hidden",
    bepaid_uid: Optional[str] = None, class_id: Optional[int] = 900000,
) -> None:
    now = _now()
    with storage._connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO payment_intents
               (public_id, mk_user_id, mk_invoice_id, mk_user_subscription_id, student_name,
                amount_minor, amount_byn, currency, status, client_visibility, source,
                class_id, bepaid_uid, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                public_id, int(mk_user_id), mk_invoice_id, mk_user_subscription_id, "Тест",
                23900, 239.0, "BYN", status, client_visibility, "moyklass_invoice_automation",
                class_id, bepaid_uid, now, now,
            ),
        )


def _training(state, reason_code=None, *, sub_id="SUB-AR1", class_ids=None):
    return {
        "state": state, "reason_code": reason_code, "mk_user_id": "8801",
        "mk_user_subscription_id": sub_id, "subscription_status_id": "2",
        "matched_class_ids": class_ids or ["900000"], "matched_join_ids": ["J1"],
        "matched_join_status_ids": ["2"], "checked_at": _now(),
    }


_OWNER_AUTH = {"_internal": False, "role": "owner", "user_id": "1001", "full_name": "Owner"}


# ---------------------------------------------------------------------------
# 5.1-5.6 — resume stage-restoration state machine
# ---------------------------------------------------------------------------

class TestPreIntentResume(unittest.TestCase):
    """5.1 — item blocked before any Payment Intent existed."""

    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage)

    def test_no_intent_resumes_to_discovered(self):
        item = _seed_item(self.storage, inv_id="INV-P1", stage="requires_check",
                           reason_code="client_training_paused")
        result = self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-P1", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        self.assertEqual(result["outcome"], "resumed")
        self.assertEqual(result["resulting_stage"], "discovered")
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertIsNone(stored["reason_code"])
        self.assertEqual(stored["current_stage"], "discovered")

    def test_stale_broken_intent_link_falls_back_to_discovered(self):
        item = _seed_item(self.storage, inv_id="INV-P2", stage="requires_check",
                           reason_code="client_training_paused",
                           intent_public_id="PI-DOES-NOT-EXIST")
        result = self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-P2", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        self.assertEqual(result["resulting_stage"], "discovered")


class TestExistingIntentPreserved(unittest.TestCase):
    """5.2/5.3 — Payment Intent exists; never create a second one."""

    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage)

    def test_intent_without_bepaid_resumes_to_pending_review(self):
        _seed_intent(self.storage, "PI-E1", status="draft", client_visibility="hidden", bepaid_uid=None)
        # current_stage was clobbered to "requires_check" by a force_stage
        # caller while blocked (discovery/forced_check) — the resolver must
        # reconstruct "pending_review" from the intent, not trust "requires_check".
        item = _seed_item(self.storage, inv_id="INV-E1", stage="requires_check",
                           reason_code="client_training_paused", intent_public_id="PI-E1")
        result = self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-E1", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        self.assertEqual(result["resulting_stage"], "pending_review")
        before = self.storage.payment_intents_stats()
        self.assertEqual(before["draft"], 1)  # still the ONE original intent

    def test_intent_with_bepaid_resumes_to_payment_options_created(self):
        _seed_intent(self.storage, "PI-E2", status="draft", client_visibility="hidden", bepaid_uid="bp-uid-123")
        item = _seed_item(self.storage, inv_id="INV-E2", stage="requires_check",
                           reason_code="client_training_paused", intent_public_id="PI-E2")
        result = self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-E2", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        self.assertEqual(result["resulting_stage"], "payment_options_created")

    def test_never_clobbered_stage_trusted_as_is_from_guardian(self):
        # Guardian's periodic_sync always uses force_stage=None, so
        # current_stage is never overwritten while blocked — the resolver
        # must trust it directly (not re-derive from the intent).
        _seed_intent(self.storage, "PI-E3", status="draft", client_visibility="hidden", bepaid_uid="bp-uid-1")
        item = _seed_item(self.storage, inv_id="INV-E3", stage="payment_options_created",
                           reason_code="client_training_paused", intent_public_id="PI-E3")
        result = self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-E3", _training(STATE_ACTIVE), _now(),
            context="periodic_sync", force_stage=None,
        )
        self.assertEqual(result["resulting_stage"], "payment_options_created")

    def test_bepaid_uid_not_reset(self):
        _seed_intent(self.storage, "PI-E4", status="draft", client_visibility="hidden", bepaid_uid="bp-uid-keep-me")
        item = _seed_item(self.storage, inv_id="INV-E4", stage="requires_check",
                           reason_code="client_training_paused", intent_public_id="PI-E4")
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-E4", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        pi = self.storage.get_payment_intent("PI-E4")
        self.assertEqual(pi["bepaid_uid"], "bp-uid-keep-me")


class TestPublishedPreserved(unittest.TestCase):
    """5.4 — published, unpaid invoice stays published, never re-published."""

    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage)

    def test_published_stays_published_no_intent_duplicate(self):
        _seed_intent(self.storage, "PI-PUB1", mk_invoice_id="INV-PUB1", status="awaiting_payment", client_visibility="published", bepaid_uid="bp-1")
        item = _seed_item(self.storage, inv_id="INV-PUB1", stage="requires_check",
                           reason_code="client_training_paused", intent_public_id="PI-PUB1")
        result = self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-PUB1", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        self.assertEqual(result["resulting_stage"], "published")
        pi = self.storage.get_payment_intent("PI-PUB1")
        self.assertEqual(pi["client_visibility"], "published")
        self.assertEqual(len(self.storage.find_all_active_intents_by_invoice("INV-PUB1")), 1)

    def test_published_never_withdrawn_by_resume(self):
        _seed_intent(self.storage, "PI-PUB2", status="awaiting_payment", client_visibility="published", bepaid_uid="bp-2")
        item = _seed_item(self.storage, inv_id="INV-PUB2", stage="published",
                           reason_code="client_training_paused", intent_public_id="PI-PUB2")
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-PUB2", _training(STATE_ACTIVE), _now(),
            context="periodic_sync", force_stage=None,
        )
        pi = self.storage.get_payment_intent("PI-PUB2")
        self.assertNotEqual(pi["client_visibility"], "withdrawn")

    def test_published_incident_closed_on_resume(self):
        _seed_intent(self.storage, "PI-PUB3", status="awaiting_payment", client_visibility="published", bepaid_uid="bp-3")
        item = _seed_item(self.storage, inv_id="INV-PUB3", stage="published",
                           reason_code="client_training_paused", intent_public_id="PI-PUB3")
        dedup_key = f"training_state:automation_item:{item['id']}"
        self.storage.upsert_incident(
            dedup_key, component="training_state", scope_type="automation_item",
            scope_id=str(item["id"]), reason_code="client_training_paused",
            severity="warning", now=_now(), payload={},
        )
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-PUB3", _training(STATE_ACTIVE), _now(),
            context="periodic_sync", force_stage=None,
        )
        inc = self.storage.get_incident(dedup_key)
        self.assertEqual(inc["status"], "resolved")
        self.assertIsNotNone(inc["resolved_at"])


class TestTerminalUnaffected(unittest.TestCase):
    """5.5 — paid/posted/withdrawn/cancelled/ignored are never touched by
    resume as a financial state transition."""

    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage)

    def _assert_terminal_excluded_from_candidates(self, status, visibility="hidden"):
        from training_state_domain import is_training_sync_candidate
        self.assertFalse(is_training_sync_candidate(
            current_stage="published", intent_status=status, intent_visibility=visibility,
        ))

    def test_paid_excluded_from_training_sync(self):
        self._assert_terminal_excluded_from_candidates("paid")

    def test_posted_to_moyklass_excluded_from_training_sync(self):
        self._assert_terminal_excluded_from_candidates("posted_to_moyklass")

    def test_cancelled_excluded_from_training_sync(self):
        self._assert_terminal_excluded_from_candidates("cancelled")

    def test_withdrawn_excluded_from_training_sync(self):
        from training_state_domain import is_training_sync_candidate
        self.assertFalse(is_training_sync_candidate(
            current_stage="published", intent_status="awaiting_payment", intent_visibility="withdrawn",
        ))

    def test_ignored_stage_excluded_from_training_sync(self):
        from training_state_domain import is_training_sync_candidate
        self.assertFalse(is_training_sync_candidate(current_stage="ignored"))

    def test_paid_intent_amount_unaffected_by_resume(self):
        _seed_intent(self.storage, "PI-T1", status="paid", client_visibility="published", bepaid_uid="bp-t1")
        item = _seed_item(self.storage, inv_id="INV-T1", stage="published",
                           reason_code="client_training_paused", intent_public_id="PI-T1")
        # Even if something calls the helper directly on a terminal-status
        # intent (defensive path), the intent's own financial fields must
        # never be touched.
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-T1", _training(STATE_ACTIVE), _now(),
            context="periodic_sync", force_stage=None,
        )
        pi = self.storage.get_payment_intent("PI-T1")
        self.assertEqual(pi["amount_byn"], 239.0)
        self.assertEqual(pi["status"], "paid")


class TestOtherReasonPreserved(unittest.TestCase):
    """5.6 — an independent, non-training reason must never be silently
    overwritten by a training block landing on top of it."""

    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage)

    def test_duplicate_intent_reason_not_overwritten_by_training_block(self):
        item = _seed_item(self.storage, inv_id="INV-O1", stage="requires_check",
                           reason_code="duplicate_invoice_intents")
        result = self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-O1",
            _training(STATE_PAUSED, "client_training_paused"), _now(),
            context="periodic_sync",
        )
        self.assertEqual(result["outcome"], "blocked")
        self.assertFalse(result["changed"])
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertEqual(stored["reason_code"], "duplicate_invoice_intents")

    def test_missing_parent_link_reason_not_overwritten(self):
        item = _seed_item(self.storage, inv_id="INV-O2", stage="missing_parent_link",
                           reason_code="no_parent_link")
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-O2",
            _training(STATE_PAUSED, "client_training_paused"), _now(),
            context="periodic_sync",
        )
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertEqual(stored["reason_code"], "no_parent_link")
        self.assertEqual(stored["current_stage"], "missing_parent_link")

    def test_independent_reason_item_not_touched_by_active_state_either(self):
        # An item with a non-training reason_code was never marked
        # "training-blocked" in the first place, so a fresh active read is
        # simply a no-op — never mistaken for a resume.
        item = _seed_item(self.storage, inv_id="INV-O3", stage="requires_check",
                           reason_code="student_name_missing")
        result = self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-O3", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        self.assertEqual(result["outcome"], "active")
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertEqual(stored["reason_code"], "student_name_missing")


# ---------------------------------------------------------------------------
# Section 6 — historical client_resume_confirmation_required items
# ---------------------------------------------------------------------------

class TestHistoricalConfirmationRequired(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage)

    def test_historical_item_auto_resumes_on_fresh_active(self):
        item = _seed_item(self.storage, inv_id="INV-H1", stage="requires_check",
                           reason_code="client_resume_confirmation_required")
        result = self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-H1", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        self.assertEqual(result["outcome"], "resumed")
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertIsNone(stored["reason_code"])

    def test_historical_item_stays_blocked_if_not_active(self):
        item = _seed_item(self.storage, inv_id="INV-H2", stage="requires_check",
                           reason_code="client_resume_confirmation_required")
        result = self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-H2",
            _training(STATE_PAUSED, "client_training_paused"), _now(),
            context="periodic_sync",
        )
        self.assertEqual(result["outcome"], "blocked")
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertEqual(stored["reason_code"], "client_training_paused")


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage)

    def test_repeated_active_cycle_is_noop(self):
        item = _seed_item(self.storage, inv_id="INV-I1", stage="requires_check",
                           reason_code="client_training_paused")
        r1 = self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-I1", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        self.assertEqual(r1["outcome"], "resumed")
        item2 = self.storage.get_automation_item_by_id(item["id"])
        r2 = self.ctx._apply_training_state_result(
            item2, item2["id"], "8801", "INV-I1", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        self.assertEqual(r2["outcome"], "active")
        self.assertFalse(r2["changed"])

    def test_no_duplicate_audit_across_repeated_active_cycles(self):
        item = _seed_item(self.storage, inv_id="INV-I2", stage="requires_check",
                           reason_code="client_training_paused")
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-I2", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        item2 = self.storage.get_automation_item_by_id(item["id"])
        self.ctx._apply_training_state_result(
            item2, item2["id"], "8801", "INV-I2", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM automation_audit_log WHERE mk_invoice_id='INV-I2' "
                "AND event_type='training_resumed_automatically'"
            ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_no_duplicate_incident_resolution(self):
        item = _seed_item(self.storage, inv_id="INV-I3", stage="requires_check",
                           reason_code="client_training_paused")
        dedup_key = f"training_state:automation_item:{item['id']}"
        self.storage.upsert_incident(
            dedup_key, component="training_state", scope_type="automation_item",
            scope_id=str(item["id"]), reason_code="client_training_paused",
            severity="warning", now=_now(), payload={},
        )
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-I3", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        resolved_at_1 = self.storage.get_incident(dedup_key)["resolved_at"]
        item2 = self.storage.get_automation_item_by_id(item["id"])
        self.ctx._apply_training_state_result(
            item2, item2["id"], "8801", "INV-I3", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        resolved_at_2 = self.storage.get_incident(dedup_key)["resolved_at"]
        self.assertEqual(resolved_at_1, resolved_at_2)

    def test_pause_active_pause_reopens_incident_and_reaudits(self):
        item = _seed_item(self.storage, inv_id="INV-I4", stage="requires_check",
                           reason_code=None)
        dedup_key = f"training_state:automation_item:{item['id']}"
        # 1) pause
        r1 = self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-I4",
            _training(STATE_PAUSED, "client_training_paused"), _now(),
            context="periodic_sync", force_stage="requires_check",
        )
        self.assertEqual(r1["outcome"], "blocked")
        # 2) resume
        item2 = self.storage.get_automation_item_by_id(item["id"])
        r2 = self.ctx._apply_training_state_result(
            item2, item2["id"], "8801", "INV-I4", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        self.assertEqual(r2["outcome"], "resumed")
        self.assertEqual(self.storage.get_incident(dedup_key)["status"], "resolved")
        # 3) paused again
        item3 = self.storage.get_automation_item_by_id(item2["id"])
        r3 = self.ctx._apply_training_state_result(
            item3, item3["id"], "8801", "INV-I4",
            _training(STATE_PAUSED, "client_training_paused"), _now(),
            context="periodic_sync", force_stage="requires_check",
        )
        self.assertEqual(r3["outcome"], "blocked")
        self.assertTrue(r3["changed"])
        # Incident safely reopens (not a fresh row): occurrence_count keeps
        # incrementing across the full pause/resume/pause history, status
        # flips back to open, resolved_at is cleared by the reopen.
        inc = self.storage.get_incident(dedup_key)
        self.assertEqual(inc["status"], "open")
        self.assertGreaterEqual(inc["occurrence_count"], 2)
        self.assertIsNone(inc["resolved_at"])
        # Exactly one training_resumed_automatically audit exists from step 2
        # — step 3 (blocked again) does not touch that event type.
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM automation_audit_log WHERE mk_invoice_id='INV-I4' "
                "AND event_type='training_resumed_automatically'"
            ).fetchall()
        self.assertEqual(len(rows), 1)


# ---------------------------------------------------------------------------
# Pilot modes after resume
# ---------------------------------------------------------------------------

class TestPilotModesAfterResume(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage)
        with self.storage._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO invoice_automation_settings (id) VALUES (1)")
            conn.execute(
                """INSERT INTO client_parent_child_links
                   (parent_telegram_user_id, mk_user_id, child_display_name, status,
                    linked_at, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                ("tgPM", "8801", "Тест", "active", _now(), _now(), _now()),
            )

    def _run_scan(self, inv_id, sub_id):
        inv = {
            "id": inv_id, "userId": "8801", "price": 239.0, "payed": 0.0,
            "userSubscriptionId": sub_id, "payUntil": "2026-08-17",
            "userSubscription": {"clientName": "Тест", "beginDate": "2026-08-01"},
            "comment": None,
        }
        with patch.object(self.ctx, "payment_intent_prepare_options", return_value={"ok": True}):
            return self.ctx._process_single_automation_item_from_invoice(
                inv, _now(), create_enabled=True, publish_enabled=False,
            )

    def test_auto_mode_creates_intent_after_resume(self):
        self.storage.upsert_pilot_client("8801", mode="auto", now=_now())
        _configure_moyklass(self.ctx.moyklass, "SUB-PM1", sub_status="2", join_status="2")
        r = self._run_scan("INV-PM1", "SUB-PM1")
        self.assertTrue(r.get("created"))
        self.assertEqual(len(self.storage.find_all_active_intents_by_invoice("INV-PM1")), 1)

    def test_review_mode_creates_pending_review_not_bepaid(self):
        self.storage.upsert_pilot_client("8801", mode="review", now=_now())
        _configure_moyklass(self.ctx.moyklass, "SUB-PM2", sub_status="2", join_status="2")
        r = self._run_scan("INV-PM2", "SUB-PM2")
        self.assertTrue(r.get("review_pending"))
        item = self.storage.get_automation_item_by_id(
            self.storage.upsert_automation_item("INV-PM2", "8801", "T", "{}", _now())["id"]
        )
        self.assertEqual(item["current_stage"], "pending_review")

    def test_observe_mode_never_creates_anything(self):
        self.storage.upsert_pilot_client("8801", mode="observe", now=_now())
        _configure_moyklass(self.ctx.moyklass, "SUB-PM3", sub_status="2", join_status="2")
        self._run_scan("INV-PM3", "SUB-PM3")
        self.assertEqual(len(self.storage.find_all_active_intents_by_invoice("INV-PM3")), 0)

    def test_disabled_mode_never_creates_anything_even_if_training_resumed(self):
        self.storage.upsert_pilot_client("8801", mode="disabled", now=_now())
        item = _seed_item(self.storage, inv_id="INV-PM4", mk_user_id="8801", sub_id="SUB-PM4",
                           stage="discovered", reason_code="client_training_paused")
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-PM4", _training(STATE_ACTIVE, sub_id="SUB-PM4"), _now(),
            context="periodic_sync",
        )
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertIsNone(stored["reason_code"])  # training block cleared
        _configure_moyklass(self.ctx.moyklass, "SUB-PM4", sub_status="2", join_status="2")
        self._run_scan("INV-PM4", "SUB-PM4")
        self.assertEqual(len(self.storage.find_all_active_intents_by_invoice("INV-PM4")), 0)

    def test_missing_pilot_record_fail_closed_even_after_resume(self):
        # No upsert_pilot_client call at all -> "not_in_pilot".
        item = _seed_item(self.storage, inv_id="INV-PM5", mk_user_id="8801", sub_id="SUB-PM5",
                           stage="discovered", reason_code="client_training_paused")
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-PM5", _training(STATE_ACTIVE, sub_id="SUB-PM5"), _now(),
            context="periodic_sync",
        )
        _configure_moyklass(self.ctx.moyklass, "SUB-PM5", sub_status="2", join_status="2")
        self._run_scan("INV-PM5", "SUB-PM5")
        self.assertEqual(len(self.storage.find_all_active_intents_by_invoice("INV-PM5")), 0)


# ---------------------------------------------------------------------------
# Guardian integration
# ---------------------------------------------------------------------------

class TestGuardianAutomaticResume(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage)
        from web_app_server import PaymentAutomationGuardian
        self.guardian = PaymentAutomationGuardian(self.ctx)

    def test_active_detected_without_mini_app_or_manual_button(self):
        item = _seed_item(self.storage, inv_id="INV-GA1", stage="discovered",
                           reason_code="client_training_paused")
        _configure_moyklass(self.ctx.moyklass, "SUB-AR1", sub_status="2", join_status="2")
        self.guardian._run_quick_cycle()
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertIsNone(stored["reason_code"])

    def test_resume_happens_within_one_quick_cycle(self):
        item = _seed_item(self.storage, inv_id="INV-GA2", stage="discovered",
                           reason_code="client_training_paused")
        _configure_moyklass(self.ctx.moyklass, "SUB-AR1", sub_status="2", join_status="2")
        run = self.guardian._run_quick_cycle()
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertIsNone(stored["reason_code"])

    def test_one_client_failure_does_not_block_another_clients_resume(self):
        bad = _seed_item(self.storage, inv_id="INV-GA3", mk_user_id="9101", sub_id="SUB-BAD",
                          stage="discovered", reason_code="client_training_paused")
        good = _seed_item(self.storage, inv_id="INV-GA4", mk_user_id="8801", sub_id="SUB-AR1",
                           stage="discovered", reason_code="client_training_paused")

        # Configure the real return_value FIRST, then capture it — the
        # wrapping side_effect mock must delegate to an already-configured
        # mock, not an empty one.
        _configure_moyklass(self.ctx.moyklass, "SUB-AR1", sub_status="2", join_status="2")
        real_get_subs = self.ctx.moyklass.get_user_subscriptions

        def get_subs(uid, **kw):
            if str(uid) == "9101":
                raise RuntimeError("boom")
            return real_get_subs(uid, **kw)

        self.ctx.moyklass.get_user_subscriptions = MagicMock(side_effect=get_subs)
        self.guardian._run_quick_cycle()

        stored_good = self.storage.get_automation_item_by_id(good["id"])
        self.assertIsNone(stored_good["reason_code"])
        # The failing client's item is never resumed (its own MoyKlass
        # error correctly fails closed to "unavailable", isolated from the
        # other client's successful resume) — the exact reason_code value
        # may legitimately change from the original pause to "unavailable",
        # but it must stay blocked, never cleared.
        stored_bad = self.storage.get_automation_item_by_id(bad["id"])
        self.assertIsNotNone(stored_bad["reason_code"])

    def test_moyklass_outage_never_resumes(self):
        item = _seed_item(self.storage, inv_id="INV-GA5", stage="discovered",
                           reason_code="client_training_paused")
        _configure_moyklass(self.ctx.moyklass, "SUB-AR1", unavailable=True)
        self.guardian._run_quick_cycle()
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertIsNotNone(stored["reason_code"])

    def test_frozen_subscription_never_resumes(self):
        item = _seed_item(self.storage, inv_id="INV-GA6", stage="discovered",
                           reason_code="client_training_paused")
        _configure_moyklass(self.ctx.moyklass, "SUB-AR1", sub_status="3", join_status="2")
        self.guardian._run_quick_cycle()
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertIsNotNone(stored["reason_code"])

    def test_ambiguous_join_status_never_resumes(self):
        item = _seed_item(self.storage, inv_id="INV-GA7", stage="discovered",
                           reason_code="client_training_paused")
        _configure_moyklass(self.ctx.moyklass, "SUB-AR1", sub_status="2", join_status="5")
        self.guardian._run_quick_cycle()
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertIsNotNone(stored["reason_code"])

    def test_finished_never_resumes(self):
        item = _seed_item(self.storage, inv_id="INV-GA8", stage="discovered",
                           reason_code="client_training_paused")
        _configure_moyklass(self.ctx.moyklass, "SUB-AR1", sub_status="2", join_status="1")
        self.guardian._run_quick_cycle()
        stored = self.storage.get_automation_item_by_id(item["id"])
        self.assertIsNotNone(stored["reason_code"])


# ---------------------------------------------------------------------------
# Attention-tab visibility after automatic resume — a resumed item (cleared
# reason_code) must disappear from «Требуют внимания», never surfacing raw
# internal stage codes, while genuinely-blocked items keep showing.
# ---------------------------------------------------------------------------

class TestAttentionVisibilityAfterResume(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()

    def _ids(self, items):
        return {i["id"] for i in items}

    def test_01_payment_options_created_cleared_reason_absent_from_attention(self):
        item = _seed_item(self.storage, inv_id="INV-ATT1", stage="payment_options_created",
                           reason_code=None, intent_public_id="PI-ATT1")
        queue = self.storage.get_payments_attention_queue(limit=50)
        self.assertNotIn(item["id"], self._ids(queue))

    def test_02_published_cleared_reason_absent_from_attention(self):
        item = _seed_item(self.storage, inv_id="INV-ATT2", stage="published",
                           reason_code=None, intent_public_id="PI-ATT2")
        queue = self.storage.get_payments_attention_queue(limit=50)
        self.assertNotIn(item["id"], self._ids(queue))

    def test_03_existing_intent_after_resume_absent_from_attention(self):
        # 5.2/5.3 shape: intent already exists, resume lands on
        # payment_options_created with reason_code cleared — no dup intent.
        item = _seed_item(self.storage, inv_id="INV-ATT3", stage="payment_options_created",
                           reason_code=None, intent_public_id="PI-ATT3")
        _seed_intent(self.storage, "PI-ATT3", mk_invoice_id="INV-ATT3", bepaid_uid="bp-att3")
        queue = self.storage.get_payments_attention_queue(limit=50)
        self.assertNotIn(item["id"], self._ids(queue))

    def test_04_pending_review_remains_in_attention(self):
        item = _seed_item(self.storage, inv_id="INV-ATT4", stage="pending_review",
                           reason_code=None, intent_public_id="PI-ATT4")
        queue = self.storage.get_payments_attention_queue(limit=50)
        self.assertIn(item["id"], self._ids(queue))

    def test_05_independent_reason_remains_in_attention(self):
        item = _seed_item(self.storage, inv_id="INV-ATT5", stage="requires_check",
                           reason_code="duplicate_invoice_intents")
        queue = self.storage.get_payments_attention_queue(limit=50)
        self.assertIn(item["id"], self._ids(queue))

    def test_06_mk_unavailable_remains_in_attention(self):
        item = _seed_item(self.storage, inv_id="INV-ATT6", stage="requires_check",
                           reason_code="training_state_unavailable")
        queue = self.storage.get_payments_attention_queue(limit=50)
        self.assertIn(item["id"], self._ids(queue))

    def test_07_ambiguous_remains_in_attention(self):
        item = _seed_item(self.storage, inv_id="INV-ATT7", stage="requires_check",
                           reason_code="training_join_status_ambiguous")
        queue = self.storage.get_payments_attention_queue(limit=50)
        self.assertIn(item["id"], self._ids(queue))

    def test_08_genuinely_blocked_payment_options_created_still_visible(self):
        # Root-cause regression: Guardian's periodic_sync uses force_stage=None,
        # so a training block landing on an item already at
        # payment_options_created keeps that stage (not forced to
        # 'requires_check') — the query must still surface it via reason_code.
        item = _seed_item(self.storage, inv_id="INV-ATT8", stage="payment_options_created",
                           reason_code="client_training_paused", intent_public_id="PI-ATT8")
        queue = self.storage.get_payments_attention_queue(limit=50)
        self.assertIn(item["id"], self._ids(queue))

    def test_09_raw_payment_options_created_not_shown_in_production_card(self):
        start = APP_JS.find("const WS_ATTENTION_STAGE_LABELS")
        self.assertNotEqual(start, -1)
        block = APP_JS[start:start + 700]
        self.assertIn("payment_options_created:", block)

    def test_10_raw_published_not_shown_in_production_card(self):
        start = APP_JS.find("const WS_ATTENTION_STAGE_LABELS")
        self.assertNotEqual(start, -1)
        block = APP_JS[start:start + 700]
        self.assertIn("published:", block)

    def test_11_attention_queue_is_a_pure_read_no_financial_side_effects(self):
        _seed_item(self.storage, inv_id="INV-ATT11", stage="payment_options_created",
                   reason_code="client_training_paused", intent_public_id="PI-ATT11")
        with patch("storage.Storage.upsert_automation_item") as up, \
             patch("storage.Storage.update_automation_item_stage") as upd:
            self.storage.get_payments_attention_queue(limit=50)
            up.assert_not_called()
            upd.assert_not_called()


# ---------------------------------------------------------------------------
# Financial side-effect proof
# ---------------------------------------------------------------------------

class TestFinancialSideEffects(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage)

    def test_resume_helper_never_calls_intent_creation(self):
        item = _seed_item(self.storage, inv_id="INV-F1", stage="requires_check",
                           reason_code="client_training_paused")
        with patch.object(self.ctx, "_automation_create_intent") as mock_create:
            self.ctx._apply_training_state_result(
                item, item["id"], "8801", "INV-F1", _training(STATE_ACTIVE), _now(),
                context="periodic_sync",
            )
        mock_create.assert_not_called()

    def test_resume_helper_never_calls_publish(self):
        item = _seed_item(self.storage, inv_id="INV-F2", stage="requires_check",
                           reason_code="client_training_paused")
        with patch.object(self.storage, "publish_payment_intent_to_client") as mock_pub:
            self.ctx._apply_training_state_result(
                item, item["id"], "8801", "INV-F2", _training(STATE_ACTIVE), _now(),
                context="periodic_sync",
            )
        mock_pub.assert_not_called()

    def test_resume_helper_never_calls_mk_posting(self):
        import inspect
        from web_app_server import MiniAppContext
        src = inspect.getsource(MiniAppContext._apply_training_state_result)
        for forbidden in ("_try_auto_post_automation_item", "post_payment_to_moyklass", "mk_post"):
            self.assertNotIn(forbidden, src)

    def test_resume_helper_never_sends_telegram(self):
        import inspect
        from web_app_server import MiniAppContext
        src = inspect.getsource(MiniAppContext._apply_training_state_result)
        self.assertNotIn("_enqueue_and_send_parent_notification", src)
        self.assertNotIn("send_message", src.lower())

    def test_resume_helper_never_withdraws(self):
        import inspect
        from web_app_server import MiniAppContext
        src = inspect.getsource(MiniAppContext._apply_training_state_result)
        self.assertNotIn("withdraw_payment_intent_from_parent", src)

    def test_resume_never_changes_amount(self):
        _seed_intent(self.storage, "PI-F1", status="draft", client_visibility="hidden", bepaid_uid="bp-f1")
        item = _seed_item(self.storage, inv_id="INV-F3", stage="requires_check",
                           reason_code="client_training_paused", intent_public_id="PI-F1")
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-F3", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        pi = self.storage.get_payment_intent("PI-F1")
        self.assertEqual(pi["amount_byn"], 239.0)
        self.assertEqual(pi["amount_minor"], 23900)


# ---------------------------------------------------------------------------
# Audit event fields (section 7)
# ---------------------------------------------------------------------------

class TestAuditEventFields(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage)

    def _last_resume_audit(self, mk_invoice_id):
        with self.storage._connect() as conn:
            row = conn.execute(
                "SELECT * FROM automation_audit_log WHERE mk_invoice_id=? "
                "AND event_type='training_resumed_automatically' ORDER BY id DESC LIMIT 1",
                (mk_invoice_id,),
            ).fetchone()
        return dict(row) if row else None

    def test_audit_created_only_on_real_transition(self):
        item = _seed_item(self.storage, inv_id="INV-A1", stage="requires_check",
                           reason_code="client_training_paused")
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-A1", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        self.assertIsNotNone(self._last_resume_audit("INV-A1"))

    def test_audit_has_automation_item_id_and_intent_public_id(self):
        _seed_intent(self.storage, "PI-A2", status="draft", client_visibility="hidden", bepaid_uid="bp-a2")
        item = _seed_item(self.storage, inv_id="INV-A2", stage="requires_check",
                           reason_code="client_training_paused", intent_public_id="PI-A2")
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-A2", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        row = self._last_resume_audit("INV-A2")
        self.assertEqual(row["automation_item_id"], item["id"])
        self.assertEqual(row["intent_public_id"], "PI-A2")

    def test_audit_details_contain_stage_transition_and_pilot_mode(self):
        import json as _json
        self.storage.upsert_pilot_client("8801", mode="auto", now=_now())
        item = _seed_item(self.storage, inv_id="INV-A3", stage="requires_check",
                           reason_code="client_training_paused")
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-A3", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        row = self._last_resume_audit("INV-A3")
        details = _json.loads(row["details_json"])
        self.assertEqual(details["previous_stage"], "requires_check")
        self.assertEqual(details["resulting_stage"], "discovered")
        self.assertEqual(details["previous_reason_code"], "client_training_paused")
        self.assertEqual(details["pilot_mode"], "auto")
        self.assertEqual(details["state"], "active")
        self.assertIn("mk_user_subscription_id", details)

    def test_audit_source_maps_periodic_sync_to_guardian(self):
        import json as _json
        item = _seed_item(self.storage, inv_id="INV-A4", stage="requires_check",
                           reason_code="client_training_paused")
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-A4", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        row = self._last_resume_audit("INV-A4")
        details = _json.loads(row["details_json"])
        self.assertEqual(details["source"], "guardian_periodic_sync")

    def test_audit_source_maps_forced_check_to_manual(self):
        import json as _json
        item = _seed_item(self.storage, inv_id="INV-A5", stage="requires_check",
                           reason_code="client_training_paused")
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-A5", _training(STATE_ACTIVE), _now(),
            context="forced_check",
        )
        row = self._last_resume_audit("INV-A5")
        details = _json.loads(row["details_json"])
        self.assertEqual(details["source"], "manual_forced_check")

    def test_audit_never_contains_pii_or_secrets(self):
        import json as _json
        item = _seed_item(self.storage, inv_id="INV-A6", stage="requires_check",
                           reason_code="client_training_paused")
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-A6", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        row = self._last_resume_audit("INV-A6")
        blob = _json.dumps(row)
        for forbidden in ("token", "secret", "phone", "initData"):
            self.assertNotIn(forbidden, blob.lower())


# ---------------------------------------------------------------------------
# Diagnostics recovered_incidents surface
# ---------------------------------------------------------------------------

class TestDiagnosticsRecoveredIncidents(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage)

    def test_recovered_incident_appears_after_resume(self):
        item = _seed_item(self.storage, inv_id="INV-R1", stage="requires_check",
                           reason_code="client_training_paused")
        dedup_key = f"training_state:automation_item:{item['id']}"
        self.storage.upsert_incident(
            dedup_key, component="training_state", scope_type="automation_item",
            scope_id=str(item["id"]), reason_code="client_training_paused",
            severity="warning", now=_now(), payload={},
        )
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-R1", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        from datetime import datetime as _dt
        since = _dt.now().strftime("%Y-%m-%dT00:00:00")
        recovered = self.storage.list_recently_resolved_incidents(since)
        self.assertEqual(len(recovered), 1)

    def test_diagnostics_response_includes_recovered_incidents_key(self):
        with patch.object(self.ctx, "_role_for_user", return_value="owner"):
            r = self.ctx.payments_diagnostics(_OWNER_AUTH)
        self.assertTrue(r["ok"])
        self.assertIn("recovered_incidents", r)

    def test_recovered_training_incident_has_curated_text(self):
        item = _seed_item(self.storage, inv_id="INV-R2", stage="requires_check",
                           reason_code="client_training_paused")
        dedup_key = f"training_state:automation_item:{item['id']}"
        self.storage.upsert_incident(
            dedup_key, component="training_state", scope_type="automation_item",
            scope_id=str(item["id"]), reason_code="client_training_paused",
            severity="warning", now=_now(), payload={},
        )
        self.ctx._apply_training_state_result(
            item, item["id"], "8801", "INV-R2", _training(STATE_ACTIVE), _now(),
            context="periodic_sync",
        )
        with patch.object(self.ctx, "_role_for_user", return_value="owner"):
            r = self.ctx.payments_diagnostics(_OWNER_AUTH)
        rec = [i for i in r["recovered_incidents"] if str(i.get("automation_item_id")) == str(item["id"])]
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]["title"], "Обучение возобновлено")
        self.assertIn("Учится", rec[0]["message"])
        self.assertIsNotNone(rec[0]["resolved_at"])
        self.assertNotIn("payload_json", rec[0])


# ---------------------------------------------------------------------------
# API backward compatibility (training-resume endpoint)
# ---------------------------------------------------------------------------

class TestBackwardCompatibleResumeEndpoint(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()
        self.ctx = _make_context(self.storage)

    def _call(self, item, action="training-resume"):
        with patch.object(self.ctx, "_role_for_user", return_value="owner"):
            return self.ctx.automation_item_action(_OWNER_AUTH, str(item["id"]), action, {})

    def test_endpoint_still_exists_and_responds(self):
        item = _seed_item(self.storage, inv_id="INV-B1", sub_id="SUB-AR1",
                           reason_code="client_training_paused")
        _configure_moyklass(self.ctx.moyklass, "SUB-AR1", sub_status="2", join_status="2")
        r = self._call(item)
        self.assertTrue(r.get("ok"))

    def test_idempotent_already_resumed_on_second_call(self):
        item = _seed_item(self.storage, inv_id="INV-B2", sub_id="SUB-AR1",
                           reason_code="client_training_paused")
        _configure_moyklass(self.ctx.moyklass, "SUB-AR1", sub_status="2", join_status="2")
        self._call(item)
        item2 = self.storage.get_automation_item_by_id(item["id"])
        r2 = self._call(item2)
        self.assertTrue(r2.get("ok"))
        self.assertTrue(r2.get("already_resumed"))

    def test_active_uses_the_common_shared_helper(self):
        import inspect
        from web_app_server import MiniAppContext
        src = inspect.getsource(MiniAppContext._training_state_resume_item)
        self.assertIn("_apply_training_state_result", src)

    def test_paused_fails_closed(self):
        item = _seed_item(self.storage, inv_id="INV-B3", sub_id="SUB-AR1",
                           reason_code="client_training_paused")
        _configure_moyklass(self.ctx.moyklass, "SUB-AR1", join_status="99046")
        r = self._call(item)
        self.assertFalse(r.get("ok"))

    def test_unknown_fails_closed(self):
        item = _seed_item(self.storage, inv_id="INV-B4", sub_id="SUB-AR1",
                           reason_code="client_training_paused")
        _configure_moyklass(self.ctx.moyklass, "SUB-AR1", unavailable=True)
        r = self._call(item)
        self.assertFalse(r.get("ok"))

    def test_ambiguous_fails_closed(self):
        item = _seed_item(self.storage, inv_id="INV-B5", sub_id="SUB-AR1",
                           reason_code="client_training_paused")
        _configure_moyklass(self.ctx.moyklass, "SUB-AR1", sub_status="2", join_status="5")
        r = self._call(item)
        self.assertFalse(r.get("ok"))

    def test_frontend_supplied_state_ignored(self):
        item = _seed_item(self.storage, inv_id="INV-B6", sub_id="SUB-AR1",
                           reason_code="client_training_paused")
        _configure_moyklass(self.ctx.moyklass, "SUB-AR1", join_status="99046")
        with patch.object(self.ctx, "_role_for_user", return_value="owner"):
            r = self.ctx.automation_item_action(
                _OWNER_AUTH, str(item["id"]), "training-resume",
                {"state": "active", "reason_code": None},
            )
        self.assertFalse(r.get("ok"))

    def test_permissions_unchanged_teacher_denied(self):
        item = _seed_item(self.storage, inv_id="INV-B7", sub_id="SUB-AR1",
                           reason_code="client_training_paused")
        with patch.object(self.ctx, "_role_for_user", return_value="teacher"):
            r = self.ctx.automation_item_action(
                {"_internal": False, "role": "teacher", "user_id": "9", "full_name": "T"},
                str(item["id"]), "training-resume", {},
            )
        self.assertFalse(r.get("ok"))

    def test_no_duplicate_audit_between_endpoint_and_repeat_call(self):
        item = _seed_item(self.storage, inv_id="INV-B8", sub_id="SUB-AR1",
                           reason_code="client_training_paused")
        _configure_moyklass(self.ctx.moyklass, "SUB-AR1", sub_status="2", join_status="2")
        self._call(item)
        item2 = self.storage.get_automation_item_by_id(item["id"])
        self._call(item2)
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM automation_audit_log WHERE mk_invoice_id='INV-B8' "
                "AND event_type='training_resumed_automatically'"
            ).fetchall()
        self.assertEqual(len(rows), 1)


# ---------------------------------------------------------------------------
# UI — the "Подтвердить возобновление" removal, and the temporary preview
# harness (section 8/12). Static text/AST-style checks, no browser.
# ---------------------------------------------------------------------------

class TestUIConfirmButtonRemoved(unittest.TestCase):
    def test_confirm_resume_button_text_removed_from_attention_card(self):
        m = re.search(r"function _wsRenderAttentionItem\(item\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        self.assertNotIn("Подтвердить возобновление", m.group(1))

    def test_no_bottom_sheet_confirmation_functions_remain(self):
        for fn in ("_wsTrainingOpenResume", "_wsTrainingConfirmResume", "_wsTrainingCloseResume"):
            self.assertNotIn(fn, APP_JS)

    def test_manual_recheck_button_still_present(self):
        self.assertIn("Проверить статус в МойКласс", APP_JS)
        self.assertIn("_wsTrainingCheck", APP_JS)

    def test_no_raw_provider_code_in_recovered_card(self):
        m = re.search(r"function _wsDiagRecoveredCard\(inc\) \{(.*?)\n\}", APP_JS, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertNotIn("payload_json", body)
        self.assertNotIn("raw_", body.lower())

    def test_help_center_new_instructions_present(self):
        for step in (
            "Менеджер меняет статус записи ученика в МойКласс на «Учится».",
            "Agent проверяет статусы каждые 10 минут.",
            "Никаких дополнительных действий в Agent не требуется.",
        ):
            self.assertIn(step, APP_JS)

    def test_help_center_old_confirm_instruction_removed(self):
        self.assertNotIn("Нажать «Подтвердить возобновление».", APP_JS)


class TestPreviewRemoved(unittest.TestCase):
    """v7.1.10 release cleanup: the temporary automatic-training-resume
    preview has been visually approved (all 11 scenarios) and fully
    removed. Replaces the old TestPreviewHarness class, which asserted the
    preview's presence during the review phase."""

    def test_preview_markers_absent_from_index_html(self):
        for marker in (
            "dev_preview", "LOCAL PREVIEW", "automatic-training-resume",
            "Preview info", "blocked_in_preview", "__YC_DEV_PREVIEW__",
            "Preview Operations", "atrPreviewMeta", "atrPreviewSwitcher",
            "PREVIEW_ME", "_wsPreviewFail", "_wsPreviewAssert",
        ):
            self.assertNotIn(marker, INDEX_HTML, f"leftover preview marker: {marker}")

    def test_no_bare_inline_script_left(self):
        self.assertEqual(re.findall(r"<script>", INDEX_HTML), [])

    def test_production_script_tail_preserved(self):
        self.assertIn('<script src="/static/app.js?v=7.1.12.3"></script>', INDEX_HTML)

    def test_previous_previews_stay_removed(self):
        self.assertNotIn('"payment-diagnostics"', INDEX_HTML)
        self.assertNotIn('"training-pause"', INDEX_HTML)
        self.assertNotIn('"payment-method"', INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
