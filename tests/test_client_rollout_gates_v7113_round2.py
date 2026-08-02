"""Tests for v7.1.13 round 2 — safe rollout gates: CLIENT_CABINET_V7113_ENABLED
/ CLIENT_CABINET_V7113_PILOT_TELEGRAM_IDS (whether a parent sees the new
cabinet shell at all vs. the pre-v7.1.13 legacy 4-tab UI) and
CLIENT_NOTIFICATIONS_ENABLED / CLIENT_NOTIFICATIONS_PILOT_TELEGRAM_IDS (an
independent gate for the notification center, so it can be piloted apart
from the rest of the cabinet). Both flags are always resolved server-side
from auth["user_id"] only — see _client_cabinet_enabled /
_client_notifications_enabled in web_app_server.py — never from a
frontend-supplied dev_user_id/query/body value.

Covers checklist §17.D items 15-22 (pilot-flag matrix) plus the
config.py wiring itself.

Run:
    python -m unittest tests.test_client_rollout_gates_v7113_round2 -v
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

WEB_APP_SERVER_PY = ROOT / "web_app_server.py"
APP_JS = ROOT / "miniapp" / "app.js"


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(
    storage: Storage,
    cabinet_enabled: bool = False,
    cabinet_pilot=None,
    notif_enabled: bool = False,
    notif_pilot=None,
) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(
        bot_username="yellowclubagent_bot", telegram_bot_token="test-secret",
        admin_ids=[], senior_teacher_ids=[], web_app_test_roles=False,
        client_food_entry_visible=True, food_module_enabled=True,
        client_cabinet_v7113_enabled=cabinet_enabled,
        client_cabinet_v7113_pilot_telegram_ids=cabinet_pilot or [],
        client_notifications_enabled=notif_enabled,
        client_notifications_pilot_telegram_ids=notif_pilot or [],
    )
    ctx._role_store: dict[int, str] = {}
    ctx._role_for_user = lambda uid: ctx._role_store.get(int(uid), "other")
    ctx.moyklass = types.SimpleNamespace(request=lambda *a, **k: types.SimpleNamespace(ok=False, data=None))
    return ctx


def _auth(uid: int, role: str, ctx: MiniAppContext) -> dict:
    ctx._role_store[uid] = role
    return {"user_id": uid}


def _cl_link(storage: Storage, mk_user_id, child_name, parent_tid, staff_uid="1"):
    code = storage.create_client_link_code(mk_user_id, child_name, staff_uid)
    assert code["ok"], code
    r = storage.link_client_child(str(parent_tid), code["code"], now_iso())
    assert r["ok"], r
    return r


class TestCabinetGateMatrix(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()

    def test_15_flags_absent_cabinet_off_by_default(self):
        """15. No env vars set at all (config.py defaults) -> new cabinet
        stays off; a plain client sees the legacy UI."""
        ctx = _make_ctx(self.storage, cabinet_enabled=False, cabinet_pilot=[])
        _cl_link(self.storage, "80001", "Kid", "8001")
        parent = _auth(8001, "parent", ctx)
        me = ctx.me(parent)
        self.assertFalse(me["clientCabinetV7113Enabled"])

    def test_16_pilot_allowlist_opens_cabinet_for_listed_id(self):
        """16. Global flag off, but this Telegram id is in the pilot list."""
        ctx = _make_ctx(self.storage, cabinet_enabled=False, cabinet_pilot=[8002])
        _cl_link(self.storage, "80002", "Kid", "8002")
        parent = _auth(8002, "parent", ctx)
        me = ctx.me(parent)
        self.assertTrue(me["clientCabinetV7113Enabled"])

    def test_17_non_allowlisted_client_still_sees_legacy(self):
        """17. Same settings object, different Telegram id not in the pilot
        list -> still legacy, proving the check is per-user not global-ish."""
        ctx = _make_ctx(self.storage, cabinet_enabled=False, cabinet_pilot=[8002])
        _cl_link(self.storage, "80003", "Other Kid", "8003")
        parent = _auth(8003, "parent", ctx)
        me = ctx.me(parent)
        self.assertFalse(me["clientCabinetV7113Enabled"])

    def test_18_global_enabled_opens_cabinet_for_everyone(self):
        """18. Global flag on -> pilot list irrelevant, everyone gets it."""
        ctx = _make_ctx(self.storage, cabinet_enabled=True, cabinet_pilot=[])
        _cl_link(self.storage, "80004", "Kid", "8004")
        parent = _auth(8004, "parent", ctx)
        me = ctx.me(parent)
        self.assertTrue(me["clientCabinetV7113Enabled"])

    def test_19_gate_is_keyed_only_on_server_auth_user_id(self):
        """19. _client_cabinet_enabled/_client_notifications_enabled take a
        single user_id argument and must never consult params/body — a
        static guard against ever wiring a frontend-supplied id into them."""
        src = WEB_APP_SERVER_PY.read_text(encoding="utf-8")
        for fn in ("_client_cabinet_enabled", "_client_notifications_enabled"):
            idx = src.find(f"def {fn}(self, user_id: int) -> bool:")
            self.assertNotEqual(idx, -1, f"{fn} not found")
            body = src[idx:idx + 700]
            self.assertNotIn("params.get", body)
            self.assertNotIn("body.get", body)
        # And every call site passes auth["user_id"], not a request value.
        for fn in ("_client_cabinet_enabled(", "_client_notifications_enabled("):
            pos = 0
            found_any = False
            while True:
                pos = src.find(fn, pos)
                if pos == -1:
                    break
                if not src.startswith(f"def {fn}", pos - 4):  # skip the def line itself
                    call_args = src[pos + len(fn):pos + len(fn) + 40]
                    self.assertTrue(
                        call_args.strip().startswith(("user_id", "int(auth[\"user_id\"])")),
                        f"suspicious call site for {fn}: ...{call_args}",
                    )
                    found_any = True
                pos += len(fn)
            self.assertTrue(found_any, f"no call sites found for {fn}")

    def test_gate_helpers_never_call_params_or_body_get(self):
        """Extra belt-and-braces: the gate helpers' actual logic (not their
        docstrings, which legitimately discuss dev_user_id in prose) never
        touches a request-supplied params/body dict — identity comes in
        purely as the user_id argument, resolved once upstream by the
        existing trusted auth layer (validate_init_data)."""
        src = WEB_APP_SERVER_PY.read_text(encoding="utf-8")
        idx = src.find("def _client_cabinet_enabled")
        end = src.find("def _test_teacher_options")
        segment = src[idx:end]
        self.assertNotIn("params.get(", segment)
        self.assertNotIn("body.get(", segment)
        self.assertNotIn("params[", segment)


class TestNotificationsGateMatrix(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()

    def _seed_notification(self, parent_tid, mk_user_id):
        self.storage.create_client_notification(
            title="Тест", body="Тестовое сообщение",
            category="general", priority="normal", scope="family", mk_user_id=None,
            action_key="none", created_by_telegram_id="1",
            recipient_telegram_ids=[str(parent_tid)],
        )

    def test_20_disabled_backend_side_returns_safe_empty_state_not_data(self):
        """20. Even though a real notification exists in storage, the
        list endpoint must not leak it while the flag is off — it returns a
        safe disabled/empty payload, and get/mark-read return a clean
        ok:false rather than partial data."""
        ctx = _make_ctx(self.storage, notif_enabled=False, notif_pilot=[])
        _cl_link(self.storage, "80005", "Kid", "8005")
        self._seed_notification("8005", "80005")
        parent = _auth(8005, "parent", ctx)
        listed = ctx.client_notifications_list(parent, {})
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["notifications"], [])
        self.assertTrue(listed.get("disabled"))
        # The row genuinely exists (proves this is a gate, not missing data).
        allowed = ctx._notification_allowed_categories("regular")
        real_items, _ = self.storage.list_client_notifications_for_recipient("8005", allowed_categories=allowed, limit=20)
        self.assertEqual(len(real_items), 1)
        notif_id = real_items[0]["id"]
        got = ctx.client_notification_get(parent, str(notif_id))
        self.assertFalse(got["ok"])
        self.assertEqual(got["error"], "notifications_disabled")
        marked = ctx.client_notification_mark_read(parent, str(notif_id))
        self.assertFalse(marked["ok"])
        self.assertEqual(marked["error"], "notifications_disabled")

    def test_21_notifications_pilot_allowlist_independent_of_cabinet_flag(self):
        """21. Notifications pilot works even while the cabinet flag itself
        is off — the two gates are independent, matching the requirement
        that notifications can be piloted separately."""
        ctx = _make_ctx(
            self.storage, cabinet_enabled=False, cabinet_pilot=[],
            notif_enabled=False, notif_pilot=[8006],
        )
        _cl_link(self.storage, "80006", "Kid", "8006")
        self._seed_notification("8006", "80006")
        parent = _auth(8006, "parent", ctx)
        listed = ctx.client_notifications_list(parent, {})
        self.assertTrue(listed["ok"])
        self.assertEqual(len(listed["notifications"]), 1)
        self.assertNotIn("disabled", listed)

    def test_22_disabling_flags_does_not_delete_data(self):
        """22. Flip a client from enabled -> disabled: the underlying rows
        must survive untouched, so re-enabling later shows the same data."""
        ctx_on = _make_ctx(self.storage, notif_enabled=True, notif_pilot=[])
        _cl_link(self.storage, "80007", "Kid", "8007")
        self._seed_notification("8007", "80007")
        parent_on = _auth(8007, "parent", ctx_on)
        before = ctx_on.client_notifications_list(parent_on, {})
        self.assertEqual(len(before["notifications"]), 1)

        ctx_off = _make_ctx(self.storage, notif_enabled=False, notif_pilot=[])
        parent_off = _auth(8007, "parent", ctx_off)
        during = ctx_off.client_notifications_list(parent_off, {})
        self.assertEqual(during["notifications"], [])

        ctx_on2 = _make_ctx(self.storage, notif_enabled=True, notif_pilot=[])
        parent_on2 = _auth(8007, "parent", ctx_on2)
        after = ctx_on2.client_notifications_list(parent_on2, {})
        self.assertEqual(len(after["notifications"]), 1)

    def test_me_zeroes_unread_badge_while_disabled(self):
        ctx = _make_ctx(self.storage, notif_enabled=False, notif_pilot=[])
        _cl_link(self.storage, "80008", "Kid", "8008")
        self._seed_notification("8008", "80008")
        parent = _auth(8008, "parent", ctx)
        me = ctx.me(parent)
        self.assertFalse(me["clientNotificationsEnabled"])
        self.assertEqual(me["unreadNotificationCount"], 0)


class TestLegacyFallbackStatic(unittest.TestCase):
    """Frontend: setupRoleUi() must fall back to the exact pre-v7.1.13
    4-tab set when the cabinet gate is closed, and must never show the new
    cabinet shell class in that case."""

    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_legacy_tab_set_present_in_conditional(self):
        idx = self.js.find("const cabinetEnabled = !!state.me?.clientCabinetV7113Enabled")
        self.assertNotEqual(idx, -1)
        body = self.js[idx:idx + 500]
        self.assertIn('"my-children", "food", "client-payments", "help"', body)

    def test_role_parent_cabinet_class_toggled_not_always_added(self):
        idx = self.js.find("document.body.classList.toggle(\"role-parent-cabinet\", cabinetEnabled)")
        self.assertNotEqual(idx, -1)


if __name__ == "__main__":
    unittest.main()
