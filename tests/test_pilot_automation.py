"""Tests for v7.1.5 — Payments workspace and controlled pilot automation.

Covers:
  - payment_automation_pilot_clients table CRUD
  - payment_pilot_audit_log writes
  - Pilot gate: not_in_pilot / disabled / observe / review / auto modes
  - Review mode: PI created, BePaid skipped, stage=pending_review
  - Auto mode: existing intent bypasses pilot gate
  - Workspace stats and attention queue
  - Approve endpoint: auth, BePaid creation, publish, last_success_at
  - Pilot management endpoints: list, upsert, mode change (role checks)
  - Storage helper find_automation_item_by_intent_public_id
  - Version: v7.1.5 in app.js and index.html

Run offline (no MoyKlass / bePaid / Telegram):
    python -m unittest tests.test_pilot_automation -v
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

CURRENT_VERSION = "7.1.5"

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
    s.web_app_url = "https://t.me/app"
    s.bepaid_erip_shop_id = "erip_shop"
    s.bepaid_erip_secret_key = "erip_secret"
    s.bepaid_request_timeout = 30
    s.payment_default_due_days = 14
    return s


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


def _make_invoice(inv_id: str = "INV001", mk_user_id: str = "9001", price: float = 239.0) -> dict:
    return {
        "id": inv_id,
        "userId": mk_user_id,
        "price": price,
        "payed": 0.0,
        "userSubscriptionId": f"sub_{inv_id}",
        "payUntil": "2026-08-17",
        "userSubscription": {"clientName": "Иван Тест", "beginDate": "2026-08-01"},
        "comment": None,
    }


def _seed_intent(
    storage: Storage,
    public_id: str,
    mk_user_id: str = "9001",
    mk_invoice_id: str = "INV001",
    status: str = "draft",
    client_visibility: str = "hidden",
    source: str = "moyklass_invoice_automation",
) -> None:
    now = _now()
    with storage._connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO payment_intents
               (public_id, mk_user_id, mk_invoice_id, student_name,
                amount_minor, amount_byn, currency, status,
                client_visibility, source, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                public_id, int(mk_user_id), mk_invoice_id, "Иван Тест",
                23900, 239.0, "BYN", status, client_visibility, source, now, now,
            ),
        )


_OWNER_AUTH = {"_internal": False, "role": "owner", "user_id": "1001", "full_name": "Owner"}
_ADMIN_AUTH = {"_internal": False, "role": "admin", "user_id": "1002", "full_name": "Admin"}
_OPS_AUTH = {"_internal": False, "role": "operations", "user_id": "1003", "full_name": "Ops"}
_CM_AUTH = {"_internal": False, "role": "client_manager", "user_id": "1004", "full_name": "CM"}
_TEACHER_AUTH = {"_internal": False, "role": "teacher", "user_id": "1005", "full_name": "Teacher"}


# ---------------------------------------------------------------------------
# 1 — Pilot table CRUD
# ---------------------------------------------------------------------------

class TestPilotTableCrud(unittest.TestCase):
    """Tests 1–7: storage-level CRUD for pilot clients and audit log."""

    def setUp(self):
        self.storage = _make_storage()

    def test_1_get_pilot_client_unknown_returns_none(self):
        result = self.storage.get_pilot_client("99999")
        self.assertIsNone(result, "Unknown mk_user_id must return None")

    def test_2_upsert_pilot_client_creates_record(self):
        now = _now()
        client = self.storage.upsert_pilot_client(
            mk_user_id="9001", mode="review",
            note="test note", added_by_tg_id="111", added_by_name="Admin",
            now=now,
        )
        self.assertEqual(client["mk_user_id"], "9001")
        self.assertEqual(client["mode"], "review")
        self.assertEqual(client["note"], "test note")
        self.assertEqual(client["enabled"], 1)

    def test_3_upsert_pilot_client_updates_existing_mode(self):
        self.storage.upsert_pilot_client(mk_user_id="9001", mode="observe", now=_now())
        client = self.storage.upsert_pilot_client(mk_user_id="9001", mode="auto", now=_now())
        self.assertEqual(client["mode"], "auto")

    def test_4_update_pilot_mode_changes_mode(self):
        self.storage.upsert_pilot_client(mk_user_id="9001", mode="observe", now=_now())
        updated = self.storage.update_pilot_mode("9001", "review", now=_now())
        self.assertEqual(updated["mode"], "review")

    def test_5_remove_pilot_client_deletes_record(self):
        self.storage.upsert_pilot_client(mk_user_id="9001", mode="review", now=_now())
        removed = self.storage.remove_pilot_client("9001")
        self.assertTrue(removed)
        self.assertIsNone(self.storage.get_pilot_client("9001"))

    def test_6_create_pilot_audit_event_writes_record(self):
        now = _now()
        self.storage.create_pilot_audit_event({
            "created_at": now,
            "event_type": "pilot_observe",
            "mk_user_id": "9001",
            "mk_invoice_id": "INV001",
            "note": "test",
        })
        with self.storage._connect() as conn:
            row = conn.execute(
                "SELECT * FROM payment_pilot_audit_log WHERE mk_user_id='9001'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(dict(row)["event_type"], "pilot_observe")

    def test_7_list_pilot_clients_returns_all(self):
        self.storage.upsert_pilot_client(mk_user_id="9001", mode="review", now=_now())
        self.storage.upsert_pilot_client(mk_user_id="9002", mode="auto", now=_now())
        clients = self.storage.list_pilot_clients()
        ids = [c["mk_user_id"] for c in clients]
        self.assertIn("9001", ids)
        self.assertIn("9002", ids)


# ---------------------------------------------------------------------------
# 2 — Pilot gate in automation
# ---------------------------------------------------------------------------

class TestPilotGateInAutomation(unittest.TestCase):
    """Tests 8–18: pilot gate in _process_single_automation_item_from_invoice."""

    def setUp(self):
        self.storage = _make_storage()
        self.settings = _make_settings()
        self.ctx = _make_context(self.storage, self.settings)
        self.settings.payment_invoice_automation_enabled = True
        # Upsert automation settings row so upsert_automation_item works
        with self.storage._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO invoice_automation_settings (id) VALUES (1)"
            )

    def _run_single(self, inv: dict, **kwargs) -> dict:
        defaults = dict(
            create_enabled=True, publish_enabled=False, post_enabled=False, notify_enabled=False
        )
        defaults.update(kwargs)
        return self.ctx._process_single_automation_item_from_invoice(
            inv, _now(), **defaults
        )

    def test_8_not_in_pilot_skips(self):
        """Client not in pilot table → skip (fail-closed)."""
        inv = _make_invoice("INV008", "9008")
        result = self._run_single(inv)
        self.assertTrue(result.get("skip"), "Not-in-pilot client must be skipped")
        # Verify no intent was created
        intents = self.storage.find_all_active_intents_by_invoice("INV008")
        self.assertEqual(len(intents), 0)

    def test_9_disabled_mode_skips(self):
        """Pilot mode=disabled → skip."""
        self.storage.upsert_pilot_client("9009", mode="disabled", now=_now())
        inv = _make_invoice("INV009", "9009")
        result = self._run_single(inv)
        self.assertTrue(result.get("skip"))
        intents = self.storage.find_all_active_intents_by_invoice("INV009")
        self.assertEqual(len(intents), 0)

    def test_10_observe_mode_skips_no_intent(self):
        """Observe mode → skip, no intent created."""
        self.storage.upsert_pilot_client("9010", mode="observe", now=_now())
        inv = _make_invoice("INV010", "9010")
        result = self._run_single(inv)
        self.assertTrue(result.get("skip"))
        intents = self.storage.find_all_active_intents_by_invoice("INV010")
        self.assertEqual(len(intents), 0)

    def test_11_observe_mode_creates_audit_event(self):
        """Observe mode → pilot_observe audit event written."""
        self.storage.upsert_pilot_client("9011", mode="observe", now=_now())
        inv = _make_invoice("INV011", "9011")
        self._run_single(inv)
        with self.storage._connect() as conn:
            row = conn.execute(
                "SELECT * FROM payment_pilot_audit_log WHERE mk_user_id='9011'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(dict(row)["event_type"], "pilot_observe")

    def test_12_review_mode_creates_intent(self):
        """Review mode → payment intent created in storage."""
        self.storage.upsert_pilot_client("9012", mode="review", now=_now())
        inv = _make_invoice("INV012", "9012")
        # Need parent link
        with self.storage._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO client_parent_child_links (mk_user_id, parent_telegram_user_id, status, linked_at, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                ("9012", "5001", "active", _now(), _now(), _now()),
            )
        with patch.object(self.ctx, "payment_intent_prepare_options", return_value={"ok": True}):
            result = self._run_single(inv)
        intents = self.storage.find_all_active_intents_by_invoice("INV012")
        self.assertGreaterEqual(len(intents), 1, "Intent must be created in review mode")

    def test_13_review_mode_skips_bepaid(self):
        """Review mode → payment_intent_prepare_options NOT called."""
        self.storage.upsert_pilot_client("9013", mode="review", now=_now())
        inv = _make_invoice("INV013", "9013")
        with self.storage._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO client_parent_child_links (mk_user_id, parent_telegram_user_id, status, linked_at, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                ("9013", "5002", "active", _now(), _now(), _now()),
            )
        with patch.object(self.ctx, "payment_intent_prepare_options", return_value={"ok": True}) as mock_bepaid:
            result = self._run_single(inv)
        mock_bepaid.assert_not_called()
        self.assertTrue(result.get("review_pending") or result.get("created"), "Should be review_pending or created")

    def test_14_review_mode_stage_pending_review(self):
        """Review mode → automation item stage = pending_review."""
        self.storage.upsert_pilot_client("9014", mode="review", now=_now())
        inv = _make_invoice("INV014", "9014")
        with self.storage._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO client_parent_child_links (mk_user_id, parent_telegram_user_id, status, linked_at, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                ("9014", "5003", "active", _now(), _now(), _now()),
            )
        with patch.object(self.ctx, "payment_intent_prepare_options", return_value={"ok": True}):
            self._run_single(inv)
        item = self.storage.get_automation_item_by_invoice("INV014")
        self.assertIsNotNone(item)
        self.assertEqual(item["current_stage"], "pending_review")

    def test_15_review_mode_creates_audit_event(self):
        """Review mode → pilot_review_created audit event."""
        self.storage.upsert_pilot_client("9015", mode="review", now=_now())
        inv = _make_invoice("INV015", "9015")
        with self.storage._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO client_parent_child_links (mk_user_id, parent_telegram_user_id, status, linked_at, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                ("9015", "5004", "active", _now(), _now(), _now()),
            )
        with patch.object(self.ctx, "payment_intent_prepare_options", return_value={"ok": True}):
            self._run_single(inv)
        with self.storage._connect() as conn:
            row = conn.execute(
                "SELECT * FROM payment_pilot_audit_log WHERE mk_user_id='9015' AND event_type='pilot_review_created'"
            ).fetchone()
        self.assertIsNotNone(row)

    def test_16_auto_mode_calls_bepaid(self):
        """Auto mode → payment_intent_prepare_options IS called."""
        self.storage.upsert_pilot_client("9016", mode="auto", now=_now())
        inv = _make_invoice("INV016", "9016")
        with self.storage._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO client_parent_child_links (mk_user_id, parent_telegram_user_id, status, linked_at, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                ("9016", "5005", "active", _now(), _now(), _now()),
            )
        with patch.object(self.ctx, "payment_intent_prepare_options", return_value={"ok": True}) as mock_bepaid:
            self._run_single(inv)
        mock_bepaid.assert_called_once()

    def test_17_review_mode_result_has_review_pending(self):
        """Review mode → result dict has review_pending=True."""
        self.storage.upsert_pilot_client("9017", mode="review", now=_now())
        inv = _make_invoice("INV017", "9017")
        with self.storage._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO client_parent_child_links (mk_user_id, parent_telegram_user_id, status, linked_at, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                ("9017", "5006", "active", _now(), _now(), _now()),
            )
        with patch.object(self.ctx, "payment_intent_prepare_options", return_value={"ok": True}):
            result = self._run_single(inv)
        self.assertTrue(result.get("review_pending"), "review mode must set review_pending=True in result")

    def test_18_existing_intent_bypasses_pilot_gate(self):
        """Existing intent → pilot gate not reached, existing lifecycle runs normally."""
        # NOTE: client NOT in pilot, but existing intent exists
        inv = _make_invoice("INV018", "9018")
        _seed_intent(self.storage, "PI-018", mk_user_id="9018", mk_invoice_id="INV018", status="draft")
        # Upsert automation item so find_all_active_intents_by_invoice returns the intent
        item = self.storage.upsert_automation_item(
            "INV018", "9018", "Иван Тест", "{}", _now(),
        )
        self.storage.update_automation_item_stage(
            item["id"], "payment_options_created",
            intent_public_id="PI-018",
            readable_reason="existing",
            now=_now(),
        )
        result = self._run_single(inv, publish_enabled=False)
        # Should not be skip=True from pilot gate; should be existing or similar
        self.assertFalse(result.get("skip") and not result.get("existing"),
                         "Existing intent must bypass pilot gate and return existing result")


# ---------------------------------------------------------------------------
# 3 — Workspace stats and attention queue
# ---------------------------------------------------------------------------

class TestWorkspaceStats(unittest.TestCase):
    """Tests 19–23: workspace stats and attention queue."""

    def setUp(self):
        self.storage = _make_storage()
        self.settings = _make_settings()
        self.ctx = _make_context(self.storage, self.settings)

    def _auth(self, role: str) -> dict:
        return {"_internal": False, "role": role, "user_id": "1001", "full_name": "Test"}

    def test_19_workspace_stats_returns_expected_keys(self):
        stats = self.storage.get_payments_workspace_stats()
        for key in ("ok", "awaiting_payment", "draft", "paid", "posted_to_moyklass",
                    "pending_review", "requires_check", "pilot_clients_count"):
            self.assertIn(key, stats, f"Missing key: {key}")
        self.assertTrue(stats["ok"])

    def test_20_workspace_stats_counts_pilot_clients(self):
        self.storage.upsert_pilot_client("9001", mode="review", now=_now())
        self.storage.upsert_pilot_client("9002", mode="auto", now=_now())
        stats = self.storage.get_payments_workspace_stats()
        self.assertEqual(stats["pilot_clients_count"], 2)

    def test_21_workspace_stats_endpoint_requires_workspace_role(self):
        """Teacher role should be denied workspace stats."""
        with patch.object(self.ctx, "_role_for_user", return_value="teacher"):
            result = self.ctx.payments_workspace_stats(self._auth("teacher"))
        self.assertFalse(result.get("ok"))

    def test_22_attention_queue_includes_pending_review(self):
        with self.storage._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO invoice_automation_settings (id) VALUES (1)"
            )
        item = self.storage.upsert_automation_item(
            "INV022", "9022", "Тест", "{}", _now(),
        )
        self.storage.update_automation_item_stage(
            item["id"], "pending_review", readable_reason="Test review", now=_now()
        )
        items = self.storage.get_payments_attention_queue()
        stages = [i["current_stage"] for i in items]
        self.assertIn("pending_review", stages)

    def test_23_attention_queue_includes_requires_check(self):
        with self.storage._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO invoice_automation_settings (id) VALUES (1)"
            )
        item = self.storage.upsert_automation_item(
            "INV023", "9023", "Тест", "{}", _now(),
        )
        self.storage.update_automation_item_stage(
            item["id"], "requires_check",
            reason_code="test_code",
            readable_reason="Test check",
            now=_now(),
        )
        items = self.storage.get_payments_attention_queue()
        stages = [i["current_stage"] for i in items]
        self.assertIn("requires_check", stages)


# ---------------------------------------------------------------------------
# 4 — Approve endpoint
# ---------------------------------------------------------------------------

class TestApproveEndpoint(unittest.TestCase):
    """Tests 24–29: approve_payment_intent."""

    def setUp(self):
        self.storage = _make_storage()
        self.settings = _make_settings()
        self.ctx = _make_context(self.storage, self.settings)
        with self.storage._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO invoice_automation_settings (id) VALUES (1)"
            )

    def _seed_draft_intent(self, public_id: str = "PI-APPROVE-001", mk_user_id: str = "9001") -> dict:
        _seed_intent(
            self.storage, public_id, mk_user_id=mk_user_id,
            mk_invoice_id="INV-APPROVE", status="draft", client_visibility="hidden",
            source="moyklass_invoice_automation",
        )
        return self.storage.get_payment_intent(public_id)

    def test_24_approve_denied_for_teacher(self):
        self._seed_draft_intent()
        with patch.object(self.ctx, "_role_for_user", return_value="teacher"):
            result = self.ctx.approve_payment_intent(_TEACHER_AUTH, "PI-APPROVE-001")
        self.assertFalse(result.get("ok"), "Teacher must be denied access to approve")

    def test_25_client_manager_can_approve(self):
        self._seed_draft_intent(public_id="PI-APPROVE-025")
        self.storage.upsert_pilot_client("9001", mode="review", now=_now())
        with patch.object(self.ctx, "_role_for_user", return_value="client_manager"), \
             patch.object(self.ctx, "payment_intent_prepare_options", return_value={"ok": True}), \
             patch.object(self.storage, "publish_payment_intent_to_client",
                          return_value={"ok": True, "intent": {}}):
            result = self.ctx.approve_payment_intent(_CM_AUTH, "PI-APPROVE-025")
        self.assertTrue(result.get("ok"), f"client_manager must be able to approve: {result}")

    def test_26_approve_calls_prepare_options(self):
        self._seed_draft_intent(public_id="PI-APPROVE-026")
        with patch.object(self.ctx, "_role_for_user", return_value="owner"), \
             patch.object(self.ctx, "payment_intent_prepare_options", return_value={"ok": True}) as mock_prepare, \
             patch.object(self.storage, "publish_payment_intent_to_client",
                          return_value={"ok": True, "intent": {}}):
            self.ctx.approve_payment_intent(_OWNER_AUTH, "PI-APPROVE-026")
        mock_prepare.assert_called_once()

    def test_27_approve_publishes_intent(self):
        self._seed_draft_intent(public_id="PI-APPROVE-027")
        with patch.object(self.ctx, "_role_for_user", return_value="owner"), \
             patch.object(self.ctx, "payment_intent_prepare_options", return_value={"ok": True}), \
             patch.object(self.storage, "publish_payment_intent_to_client",
                          return_value={"ok": True, "intent": {}}) as mock_pub:
            self.ctx.approve_payment_intent(_OWNER_AUTH, "PI-APPROVE-027")
        mock_pub.assert_called_once()

    def test_28_approve_fails_if_already_published(self):
        _seed_intent(
            self.storage, "PI-APPROVE-028", mk_user_id="9001",
            mk_invoice_id="INV-APPROVE", status="draft", client_visibility="published",
        )
        with patch.object(self.ctx, "_role_for_user", return_value="owner"):
            result = self.ctx.approve_payment_intent(_OWNER_AUTH, "PI-APPROVE-028")
        self.assertFalse(result.get("ok"))

    def test_29_approve_updates_pilot_last_success(self):
        self._seed_draft_intent(public_id="PI-APPROVE-029")
        self.storage.upsert_pilot_client("9001", mode="review", now=_now())
        with patch.object(self.ctx, "_role_for_user", return_value="owner"), \
             patch.object(self.ctx, "payment_intent_prepare_options", return_value={"ok": True}), \
             patch.object(self.storage, "publish_payment_intent_to_client",
                          return_value={"ok": True, "intent": {}}):
            self.ctx.approve_payment_intent(_OWNER_AUTH, "PI-APPROVE-029")
        client = self.storage.get_pilot_client("9001")
        self.assertIsNotNone(client)
        self.assertIsNotNone(client.get("last_success_at"),
                             "last_success_at must be set after approval")


# ---------------------------------------------------------------------------
# 5 — Pilot management endpoints
# ---------------------------------------------------------------------------

class TestPilotManagementEndpoints(unittest.TestCase):
    """Tests 30–33: pilot client management via MiniAppContext methods."""

    def setUp(self):
        self.storage = _make_storage()
        self.settings = _make_settings()
        self.ctx = _make_context(self.storage, self.settings)

    def test_30_pilot_list_denied_for_teacher(self):
        with patch.object(self.ctx, "_role_for_user", return_value="teacher"):
            result = self.ctx.pilot_list_clients(_TEACHER_AUTH)
        self.assertFalse(result.get("ok"))

    def test_31_pilot_upsert_allowed_for_client_manager(self):
        """v7.1.5.3: client_manager is now in PILOT_MANAGE_ROLES and may upsert pilot clients."""
        with patch.object(self.ctx, "_role_for_user", return_value="client_manager"):
            result = self.ctx.pilot_upsert_client(_CM_AUTH, {"mk_user_id": "9001", "mode": "review"})
        self.assertTrue(result.get("ok"), f"client_manager must be allowed to upsert pilot: {result}")
        self.assertEqual(result["client"]["mode"], "review")

    def test_32_pilot_upsert_by_owner_creates_client(self):
        with patch.object(self.ctx, "_role_for_user", return_value="owner"):
            result = self.ctx.pilot_upsert_client(_OWNER_AUTH, {"mk_user_id": "9001", "mode": "review"})
        self.assertTrue(result.get("ok"), f"Owner must be able to upsert: {result}")
        self.assertIn("client", result)
        self.assertEqual(result["client"]["mode"], "review")

    def test_33_find_automation_item_by_intent_public_id(self):
        with self.storage._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO invoice_automation_settings (id) VALUES (1)"
            )
        item = self.storage.upsert_automation_item(
            "INV033", "9033", "Тест", "{}", _now(),
        )
        self.storage.update_automation_item_stage(
            item["id"], "pending_review",
            intent_public_id="PI-033",
            readable_reason="Test",
            now=_now(),
        )
        found = self.storage.find_automation_item_by_intent_public_id("PI-033")
        self.assertIsNotNone(found)
        self.assertEqual(found["intent_public_id"], "PI-033")
        self.assertEqual(found["current_stage"], "pending_review")


# ---------------------------------------------------------------------------
# 6 — Version
# ---------------------------------------------------------------------------

class TestVersionV715(unittest.TestCase):
    """Test 34: version marker is v7.1.5."""

    def test_34_version_v715(self):
        """app.js has MiniApp version: v7.1.5; index.html has v=7.1.5."""
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
        self.assertIn("v7.1.5", js, "app.js version marker must contain v7.1.5")
        self.assertIn("v=7.1.5", html, "index.html cache-bust must be v=7.1.5")


if __name__ == "__main__":
    unittest.main()
