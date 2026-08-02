"""Tests for v7.1.13 — client notification center backend/API: storage
model (client_notifications + client_notification_recipients,
self-migrating, no manual production migration), ownership enforcement via
client_parent_child_links (never a frontend-supplied telegram_id/mk_user_id),
idempotent mark-read, whitelisted action_key, and the temporary owner-only
smoke-test sender (explicitly NOT the staff communications center).

Covers checklist §17.C (items 22-34).

Run:
    python -m unittest tests.test_client_notifications_v7113 -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from web_app_server import MiniAppContext
from utils import now_iso

APP_JS = ROOT / "miniapp" / "app.js"
WEB_APP_SERVER_PY = ROOT / "web_app_server.py"


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(
        bot_username="yellowclubagent_bot", telegram_bot_token="test-secret",
        admin_ids=[], senior_teacher_ids=[], web_app_test_roles=False,
        client_food_entry_visible=True, food_module_enabled=True,
        # v7.1.13 round 2 rollout gates default ON here so this file keeps
        # testing notification behavior itself; gate mechanics have their
        # own dedicated tests in test_client_rollout_gates_v7113_round2.py.
        client_cabinet_v7113_enabled=True, client_cabinet_v7113_pilot_telegram_ids=[],
        client_notifications_enabled=True, client_notifications_pilot_telegram_ids=[],
    )
    ctx._role_store: dict[int, str] = {}
    ctx._role_for_user = lambda uid: ctx._role_store.get(int(uid), "other")
    ctx.moyklass = types.SimpleNamespace(request=lambda *a, **k: types.SimpleNamespace(ok=False, data=None))
    return ctx


def _auth(uid: int, role: str, ctx: MiniAppContext) -> dict:
    ctx._role_store[uid] = role
    return {"user_id": uid}


class NotificationTestBase(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        self.owner = _auth(1, "owner", self.ctx)

    def _cl_link(self, mk_user_id, child_name, parent_tid):
        code = self.storage.create_client_link_code(mk_user_id, child_name, "1")
        self.assertTrue(code["ok"], code)
        r = self.storage.link_client_child(str(parent_tid), code["code"], now_iso())
        self.assertTrue(r["ok"], r)
        return r


class TestUnicodeRoundTrip(NotificationTestBase):
    """13. Round 2 — diagnosed as a one-off shell/curl transit corruption in
    an earlier manual test (Windows Git-Bash → curl.exe mangled literal
    Cyrillic before it ever reached the server), NOT a backend/storage
    defect. These tests prove the real round-trip end-to-end: owner-sender
    handler -> SQLite storage -> raw bytes -> API JSON response, using
    Cyrillic, emoji, and em/en dashes, entirely in-process (no shell)."""

    CYRILLIC_TITLE = "Проверка уведомления — важно"
    CYRILLIC_BODY = "Сообщение для родителей: занятие перенесено на вторник. 🎉 Спасибо — до встречи!"

    def test_13_owner_sender_to_sqlite_round_trip_no_replacement_chars(self):
        self._cl_link("40001", "Тестовый Ребёнок", "9001")
        result = self.ctx.owner_test_notification_create(self.owner, {
            "mk_user_id": "40001", "scope": "child", "category": "schedule",
            "priority": "normal", "action_key": "open_availability",
            "title": self.CYRILLIC_TITLE, "body": self.CYRILLIC_BODY,
        })
        self.assertTrue(result["ok"], result)
        notification_id = result["id"]

        # Raw SQLite round-trip — bypasses any Python-level string caching,
        # reads exactly what's on disk.
        with self.storage._connect() as conn:
            row = conn.execute(
                "SELECT title, body FROM client_notifications WHERE id = ?", (notification_id,)
            ).fetchone()
        self.assertEqual(row[0], self.CYRILLIC_TITLE)
        self.assertEqual(row[1], self.CYRILLIC_BODY)
        self.assertNotIn("�", row[0])
        self.assertNotIn("�", row[1])

        # API JSON round-trip (client-facing GET), including emoji/dashes.
        parent = _auth(9001, "parent", self.ctx)
        listed = self.ctx.client_notifications_list(parent, {})
        self.assertTrue(listed["ok"], listed)
        item = next(n for n in listed["notifications"] if n["id"] == notification_id)
        self.assertEqual(item["title"], self.CYRILLIC_TITLE)
        self.assertNotIn("�", item["title"])

        detail = self.ctx.client_notification_get(parent, str(notification_id))
        self.assertTrue(detail["ok"], detail)
        self.assertEqual(detail["notification"]["body"], self.CYRILLIC_BODY)
        self.assertNotIn("�", detail["notification"]["body"])

        # json.dumps with ensure_ascii=False is what _send_json in
        # web_app_server.py uses — verify that path doesn't escape/corrupt
        # the text either (defense in depth, matches production serializer).
        import json
        encoded = json.dumps(detail, ensure_ascii=False)
        self.assertIn(self.CYRILLIC_TITLE, encoded)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["notification"]["title"], self.CYRILLIC_TITLE)


class TestStorageModel(NotificationTestBase):
    def test_tables_self_migrate_no_manual_migration(self):
        with self.storage._connect() as conn:
            cols_n = {r[1] for r in conn.execute("PRAGMA table_info(client_notifications)").fetchall()}
            cols_r = {r[1] for r in conn.execute("PRAGMA table_info(client_notification_recipients)").fetchall()}
        for c in ["id", "title", "body", "category", "priority", "scope", "mk_user_id",
                  "action_key", "created_by_telegram_id", "created_at", "expires_at", "metadata_json"]:
            self.assertIn(c, cols_n)
        for c in ["id", "notification_id", "recipient_telegram_id", "read_at", "in_app_status", "created_at"]:
            self.assertIn(c, cols_r)

    def test_22_empty_list_for_client_with_no_notifications(self):
        self._cl_link("40001", "Kid", "8001")
        parent = _auth(8001, "parent", self.ctx)
        r = self.ctx.client_notifications_list(parent, {})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["notifications"], [])


class TestScopeVisibility(NotificationTestBase):
    def test_23_family_notification_visible_to_linked_parent(self):
        self._cl_link("40002", "Kid", "8002")
        parent = _auth(8002, "parent", self.ctx)
        created = self.storage.create_client_notification(
            title="Семейное", body="Текст", category="general", priority="normal",
            scope="family", mk_user_id=None, action_key="none",
            created_by_telegram_id="1", recipient_telegram_ids=["8002"],
        )
        self.assertTrue(created["ok"], created)
        r = self.ctx.client_notifications_list(parent, {})
        self.assertEqual(len(r["notifications"]), 1)
        self.assertTrue(r["notifications"][0]["unread"])

    def test_24_child_notification_visible_to_that_childs_parent(self):
        self._cl_link("40003", "Kid", "8003")
        parent = _auth(8003, "parent", self.ctx)
        self.storage.create_client_notification(
            title="Детское", body="Текст", category="schedule", priority="normal",
            scope="child", mk_user_id="40003", action_key="open_availability",
            created_by_telegram_id="1", recipient_telegram_ids=["8003"],
        )
        r = self.ctx.client_notifications_list(parent, {})
        self.assertEqual(len(r["notifications"]), 1)
        self.assertEqual(r["notifications"][0]["mk_user_id"], "40003")

    def test_25_other_childs_notification_not_accessible(self):
        self._cl_link("40004", "Victim Kid", "8004")
        self._cl_link("40005", "Attacker Kid", "8005")
        attacker = _auth(8005, "parent", self.ctx)
        created = self.storage.create_client_notification(
            title="Приватное", body="Текст", category="schedule", priority="normal",
            scope="child", mk_user_id="40004", action_key="none",
            created_by_telegram_id="1", recipient_telegram_ids=["8004"],
        )
        notif_id = created["id"]
        r = self.ctx.client_notification_get(attacker, str(notif_id))
        self.assertFalse(r["ok"], r)

    def test_deleted_unlink_revokes_access_even_with_existing_recipient_row(self):
        """Ownership is re-verified dynamically — an unlink after the fact
        must revoke access even though the recipient row itself is untouched."""
        self._cl_link("40006", "Kid", "8006")
        parent = _auth(8006, "parent", self.ctx)
        created = self.storage.create_client_notification(
            title="Т", body="Т", category="schedule", priority="normal",
            scope="child", mk_user_id="40006", action_key="none",
            created_by_telegram_id="1", recipient_telegram_ids=["8006"],
        )
        notif_id = created["id"]
        self.assertTrue(self.ctx.client_notification_get(parent, str(notif_id))["ok"])
        self.storage.unlink_client_child("8006", "40006", "1", now_iso())
        r = self.ctx.client_notification_get(parent, str(notif_id))
        self.assertFalse(r["ok"], r)

    def test_26_another_telegram_user_cannot_read_or_mark_read(self):
        self._cl_link("40007", "Kid", "8007")
        stranger = _auth(9999, "parent", self.ctx)
        created = self.storage.create_client_notification(
            title="Т", body="Т", category="general", priority="normal",
            scope="family", mk_user_id=None, action_key="none",
            created_by_telegram_id="1", recipient_telegram_ids=["8007"],
        )
        notif_id = created["id"]
        self.assertFalse(self.ctx.client_notification_get(stranger, str(notif_id))["ok"])
        mark = self.ctx.client_notification_mark_read(stranger, str(notif_id))
        self.assertFalse(mark["ok"], mark)


class TestUnreadAndMarkRead(NotificationTestBase):
    def test_27_unread_count_correct(self):
        self._cl_link("40008", "Kid", "8008")
        parent = _auth(8008, "parent", self.ctx)
        for i in range(3):
            self.storage.create_client_notification(
                title=f"N{i}", body="Т", category="general", priority="normal",
                scope="family", mk_user_id=None, action_key="none",
                created_by_telegram_id="1", recipient_telegram_ids=["8008"],
            )
        me = self.ctx.me(parent)
        self.assertEqual(me["unreadNotificationCount"], 3)

    def test_28_mark_read_idempotent(self):
        self._cl_link("40009", "Kid", "8009")
        parent = _auth(8009, "parent", self.ctx)
        created = self.storage.create_client_notification(
            title="N", body="Т", category="general", priority="normal",
            scope="family", mk_user_id=None, action_key="none",
            created_by_telegram_id="1", recipient_telegram_ids=["8009"],
        )
        notif_id = str(created["id"])
        r1 = self.ctx.client_notification_mark_read(parent, notif_id)
        self.assertTrue(r1["ok"], r1)
        r2 = self.ctx.client_notification_mark_read(parent, notif_id)
        self.assertTrue(r2["ok"], r2)
        me = self.ctx.me(parent)
        self.assertEqual(me["unreadNotificationCount"], 0)

    def test_29_badge_updates_without_full_reload_static(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn("_updateNotifNavBadge", js)
        idx = js.find("async function openNotificationDetail")
        body = js[idx:idx + 1400]
        self.assertIn("notifNavBadge", js)
        self.assertIn("renderClientNotifications()", body)


class TestActionWhitelist(NotificationTestBase):
    def test_30_valid_action_key_accepted(self):
        self._cl_link("40010", "Kid", "8010")
        r = self.ctx.owner_test_notification_create(self.owner, {
            "mk_user_id": "40010", "scope": "family", "category": "general",
            "priority": "normal", "action_key": "open_payments",
            "title": "T", "body": "B",
        })
        self.assertTrue(r["ok"], r)

    def test_31_arbitrary_action_key_rejected(self):
        self._cl_link("40011", "Kid", "8011")
        r = self.ctx.owner_test_notification_create(self.owner, {
            "mk_user_id": "40011", "scope": "family", "category": "general",
            "priority": "normal", "action_key": "javascript:alert(1)",
            "title": "T", "body": "B",
        })
        self.assertFalse(r["ok"], r)

    def test_31b_arbitrary_scope_and_category_rejected(self):
        self._cl_link("40012", "Kid", "8012")
        bad_scope = self.ctx.owner_test_notification_create(self.owner, {
            "mk_user_id": "40012", "scope": "everyone", "category": "general",
            "priority": "normal", "action_key": "none", "title": "T", "body": "B",
        })
        self.assertFalse(bad_scope["ok"], bad_scope)
        bad_category = self.ctx.owner_test_notification_create(self.owner, {
            "mk_user_id": "40012", "scope": "family", "category": "not_a_real_category",
            "priority": "normal", "action_key": "none", "title": "T", "body": "B",
        })
        self.assertFalse(bad_category["ok"], bad_category)

    def test_32_long_message_not_truncated_at_data_level(self):
        self._cl_link("40013", "Kid", "8013")
        parent = _auth(8013, "parent", self.ctx)
        long_body = "А" * 3000
        created = self.storage.create_client_notification(
            title="Длинное", body=long_body, category="general", priority="normal",
            scope="family", mk_user_id=None, action_key="none",
            created_by_telegram_id="1", recipient_telegram_ids=["8013"],
        )
        got = self.ctx.client_notification_get(parent, str(created["id"]))
        self.assertEqual(len(got["notification"]["body"]), 3000)


class TestOwnerTestSenderGuards(NotificationTestBase):
    def test_33_owner_test_sender_denied_for_plain_client_manager(self):
        role = _auth(500, "client_manager", self.ctx)
        r = self.ctx.owner_test_notification_create(role, {
            "mk_user_id": "1", "scope": "family", "category": "general",
            "priority": "normal", "action_key": "none", "title": "T", "body": "B",
        })
        self.assertFalse(r["ok"], r)
        self.assertEqual(r.get("error"), "access_denied")

    def test_33b_owner_test_sender_denied_for_teacher(self):
        role = _auth(501, "teacher", self.ctx)
        r = self.ctx.owner_test_notification_create(role, {
            "mk_user_id": "1", "scope": "family", "category": "general",
            "priority": "normal", "action_key": "none", "title": "T", "body": "B",
        })
        self.assertFalse(r["ok"], r)

    def test_34_regular_client_cannot_create_notifications(self):
        self._cl_link("40014", "Kid", "8014")
        parent = _auth(8014, "parent", self.ctx)
        self.assertFalse(hasattr(MiniAppContext, "client_notification_create"))
        r = self.ctx.owner_test_notification_create(parent, {
            "mk_user_id": "40014", "scope": "family", "category": "general",
            "priority": "normal", "action_key": "none", "title": "T", "body": "B",
        })
        self.assertFalse(r["ok"], r)

    def test_sender_targets_exactly_one_real_linked_client_no_broadcast(self):
        """§12: no audience/segment parameter exists — an mk_user_id with no
        active link is refused outright, and there is structurally no way
        to target more than the parents actually linked to that one child."""
        r = self.ctx.owner_test_notification_create(self.owner, {
            "mk_user_id": "99999-not-linked", "scope": "family", "category": "general",
            "priority": "normal", "action_key": "none", "title": "T", "body": "B",
        })
        self.assertFalse(r["ok"], r)
        self.assertNotIn("recipients", r)
        self.assertNotIn("audience", r)

    def test_no_bulk_send_ui_only_single_test_client_selector(self):
        html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
        idx = html.find('id="ownerTestNotificationPanel"')
        self.assertNotEqual(idx, -1)
        panel = html[idx:idx + 2500]
        self.assertIn("otnMkUserId", panel)
        self.assertNotIn("segment", panel.lower())
        self.assertNotIn("broadcast", panel.lower())
        self.assertNotIn("all clients", panel.lower())


if __name__ == "__main__":
    unittest.main()
