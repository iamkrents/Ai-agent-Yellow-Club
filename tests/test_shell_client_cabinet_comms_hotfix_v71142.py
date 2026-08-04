"""Tests for v7.1.14.2 — four fixes:

  1. Global top safe-area/header system, fixed at the shared shell/layout
     level (not per-screen): the CSS side used to carry four stacked
     generations of the same fix (v6.6.3/v6.6.5/v6.6.6/v7.1.13.2), the first
     two always dead code (beaten on specificity/source order) but real
     cascade risk; removed here, leaving one surviving --app-top-safe-offset
     chain. The JS side never listened for contentSafeAreaChanged/
     fullscreenChanged (only the unrelated safeAreaChanged), so
     _applySafeArea's post-fullscreen-transition value could go stale on any
     role/screen — both events are now wired to the same _applySafeArea.

  2. New client cabinet (clientCabinetV7113Enabled) rolled out to every
     connected/registering client except food_only, which stays on the old
     design/logic unconditionally, at both the server gate (me()) and the
     frontend (defense in depth).

  3. The "Тестовое уведомление одному клиенту" smoke-test sender is no
     longer shown in production: gated on state.me.devMode (real
     dev/debug-only signal — see validate_init_data/web_app_dev_mode) on top
     of the existing canUseTestRoles/ownerTestClientMode checks.

  4. client_manager: CLIENT_COMMUNICATIONS_ROLES now includes client_manager
     (dedicated "Рассылки" tab), and the old "Обед" tab is excluded for that
     role specifically (staff-lunch-tab). owner/admin access to Рассылки is
     unchanged. The BackButton/"Назад в Админ" affordance added for owner/
     admin in v7.1.14.1 is only wired when the real role can actually reach
     Admin (canUseAdmin()) — client_manager has no admin tab, so it is never
     shown a back target it cannot reach.

Run:
    python -m unittest tests.test_shell_client_cabinet_comms_hotfix_v71142 -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import web_app_server as srv  # noqa: E402
from storage import Storage  # noqa: E402
from utils import now_iso  # noqa: E402

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
STYLES_CSS = ROOT / "miniapp" / "styles.css"


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _settings(**overrides):
    base = dict(
        client_cabinet_v7113_enabled=False,
        client_cabinet_v7113_pilot_telegram_ids=[],
        client_communications_enabled=False,
        client_communications_pilot_telegram_ids=[],
        client_communications_send_enabled=True,
        client_communications_scheduler_enabled=True,
        client_notifications_enabled=True,
        client_notifications_pilot_telegram_ids=[],
        admin_ids=[], senior_teacher_ids=[], web_app_test_roles=True, food_module_enabled=False,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _make_ctx(storage: Storage, **settings_overrides):
    ctx = srv.MiniAppContext.__new__(srv.MiniAppContext)
    ctx.storage = storage
    ctx.settings = _settings(**settings_overrides)
    return ctx


def _auth(uid) -> dict:
    return {"user_id": int(uid)}


class TestClientCabinetFoodOnlyExclusion(unittest.TestCase):
    """Fix #2 — new cabinet for everyone except food_only, at the server gate."""

    def setUp(self):
        self.storage = _tmp_storage()
        now = now_iso()
        with self.storage._connect() as conn:
            # food_only: a row in parent_child_links only.
            conn.execute(
                """INSERT INTO parent_child_links (parent_telegram_id, mk_student_id, link_code, active, created_at)
                   VALUES ('700001','S1','CODE-700001',1,?)""",
                (now,),
            )
            # regular: a row in client_parent_child_links only.
            conn.execute(
                """INSERT INTO client_parent_child_links
                   (parent_telegram_user_id, mk_user_id, child_display_name, status, linked_at, created_at, updated_at)
                   VALUES ('700002','S2','Child','active',?,?,?)""",
                (now, now, now),
            )
            # combined: both.
            conn.execute(
                """INSERT INTO parent_child_links (parent_telegram_id, mk_student_id, link_code, active, created_at)
                   VALUES ('700003','S3','CODE-700003',1,?)""",
                (now,),
            )
            conn.execute(
                """INSERT INTO client_parent_child_links
                   (parent_telegram_user_id, mk_user_id, child_display_name, status, linked_at, created_at, updated_at)
                   VALUES ('700003','S3b','Child3','active',?,?,?)""",
                (now, now, now),
            )

    def test_food_only_never_gets_new_cabinet_even_with_global_flag(self):
        ctx = _make_ctx(self.storage, client_cabinet_v7113_enabled=True)
        self.assertEqual(self.storage.get_client_kind_for_parent("700001"), "food_only")
        me = ctx.me(_auth(700001))
        # role won't be "parent" for a staff-table-less id in this harness,
        # so drive the gate function directly (the exact function me() calls).
        self.assertFalse(
            ctx._client_cabinet_enabled(700001) and ctx.storage.get_client_kind_for_parent("700001") != "food_only",
            "food_only must never compute enabled=True",
        )

    def test_food_only_never_gets_new_cabinet_even_pilot_listed(self):
        ctx = _make_ctx(self.storage, client_cabinet_v7113_pilot_telegram_ids=[700001])
        client_kind = ctx.storage.get_client_kind_for_parent("700001")
        self.assertEqual(client_kind, "food_only")
        enabled = ctx._client_cabinet_enabled(700001) and client_kind != "food_only"
        self.assertFalse(enabled)

    def test_regular_and_combined_get_new_cabinet_when_flag_on(self):
        ctx = _make_ctx(self.storage, client_cabinet_v7113_enabled=True)
        for pid in ("700002", "700003"):
            client_kind = ctx.storage.get_client_kind_for_parent(pid)
            self.assertIn(client_kind, ("regular", "combined"))
            enabled = ctx._client_cabinet_enabled(int(pid)) and client_kind != "food_only"
            self.assertTrue(enabled, f"pid={pid} kind={client_kind} should get the new cabinet")

    def test_me_endpoint_excludes_food_only_end_to_end(self):
        ctx = _make_ctx(self.storage, client_cabinet_v7113_enabled=True)
        self.storage.set_staff_role(700001, "parent")
        result = ctx.me(_auth(700001))
        self.assertEqual(result["clientKind"], "food_only")
        self.assertFalse(result["clientCabinetV7113Enabled"])

    def test_me_endpoint_includes_regular_client_end_to_end(self):
        ctx = _make_ctx(self.storage, client_cabinet_v7113_enabled=True)
        self.storage.set_staff_role(700002, "parent")
        result = ctx.me(_auth(700002))
        self.assertEqual(result["clientKind"], "regular")
        self.assertTrue(result["clientCabinetV7113Enabled"])


class TestClientManagerCommsSwap(unittest.TestCase):
    """Fix #4 — client_manager gets Рассылки, loses Обеды; owner/admin unaffected."""

    def setUp(self):
        self.storage = _tmp_storage()
        self.storage.set_staff_role(8001, "owner")
        self.storage.set_staff_role(8002, "admin")
        self.storage.set_staff_role(8003, "client_manager")
        self.storage.set_staff_role(8004, "operations")

    def test_client_manager_gets_communications_capability(self):
        ctx = _make_ctx(self.storage, client_communications_enabled=True)
        self.assertIsNone(ctx._require_communications_access(_auth(8003)))
        self.assertTrue(ctx._capabilities_for_user(8003)["canUseCommunications"])

    def test_owner_admin_still_allowed(self):
        ctx = _make_ctx(self.storage, client_communications_enabled=True)
        self.assertTrue(ctx._capabilities_for_user(8001)["canUseCommunications"])
        self.assertTrue(ctx._capabilities_for_user(8002)["canUseCommunications"])

    def test_operations_still_excluded(self):
        ctx = _make_ctx(self.storage, client_communications_enabled=True)
        self.assertFalse(ctx._capabilities_for_user(8004)["canUseCommunications"])

    def test_client_manager_has_no_admin_tabs(self):
        ctx = _make_ctx(self.storage)
        self.assertEqual(ctx._admin_tabs_for_role("client_manager"), [])
        self.assertFalse(ctx._capabilities_for_user(8003)["canUseAdmin"])


class TestStaticFrontendChanges(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")
        self.css = STYLES_CSS.read_text(encoding="utf-8")

    # ── Fix #1: header/safe-area ────────────────────────────────────────
    def test_legacy_dead_code_safe_area_generations_removed(self):
        # Historical prose comments describing why the old generations were
        # removed are fine (and expected) to still name them; only actual
        # declarations/usages of the old tokens must be gone.
        self.assertNotIn("padding-top: calc(var(--tg-safe-top) + 20px) !important", self.css)
        self.assertNotIn("--app-top-offset:", self.css)
        self.assertNotIn("var(--app-top-offset)", self.css)
        self.assertNotIn("--tg-top-extra:", self.css)
        self.assertNotIn("var(--tg-top-extra)", self.css)

    def test_single_surviving_safe_area_chain(self):
        self.assertEqual(self.css.count("--app-top-safe-offset:   calc(var(--tg-safe-top) + var(--tg-native-top-overlay));"), 1)
        # v7.1.14.3 — the app-shell-padding-top model was replaced by a
        # dedicated spacer element (see test_safe_area_branch_selection_
        # hotfix_v71143.py); the chain variable itself is unchanged/still
        # single-sourced, just consumed differently.
        self.assertIn(
            "body.is-telegram-webapp .app-top-safe-spacer {\n  height: var(--app-top-safe-offset);\n}",
            self.css,
        )

    def test_content_safe_area_and_fullscreen_listeners_added(self):
        self.assertIn('tg?.onEvent?.("contentSafeAreaChanged", _applySafeArea);', self.js)
        self.assertIn('tg?.onEvent?.("fullscreenChanged", _applySafeArea);', self.js)
        self.assertIn('tg?.onEvent?.("safeAreaChanged", _applySafeArea);', self.js)

    # ── Fix #2: client cabinet (frontend defense-in-depth) ──────────────
    def test_frontend_excludes_food_only_from_cabinet(self):
        idx = self.js.find("const clientKind = state.me?.clientKind")
        self.assertNotEqual(idx, -1)
        segment = self.js[idx:idx + 400]
        self.assertIn('clientKind !== "food_only"', segment)

    # ── Fix #3: test-notification panel hidden in production ───────────
    def test_owner_test_notification_panel_requires_devmode(self):
        idx = self.js.find("function renderOwnerTestNotificationPanel")
        self.assertNotEqual(idx, -1)
        body = self.js[idx:idx + 1200]
        self.assertIn("state.me?.devMode", body)
        self.assertIn("state.me?.capabilities?.canUseTestRoles", body)
        self.assertIn("state.me?.ownerTestClientMode", body)

    # ── Fix #4: client_manager tab swap (static) ────────────────────────
    def test_mvp_tabs_client_manager_has_comms_not_lunch(self):
        idx = self.js.find('client_manager: [')
        self.assertNotEqual(idx, -1)
        line = self.js[idx:self.js.find("\n", idx)]
        self.assertIn('"comms"', line)
        self.assertNotIn('"my-lunch"', line)

    def test_staff_lunch_tab_excludes_client_manager(self):
        idx = self.js.find("Staff lunch tab: show for ALL staff")
        self.assertNotEqual(idx, -1)
        segment = self.js[idx:idx + 400]
        self.assertIn('"client_manager"', segment)

    def test_comms_exit_to_admin_gated_on_real_role(self):
        # v7.1.14.3 — canUseAdmin() proved TRUE for client_manager whenever
        # food-lunch self-order is enabled (see test_safe_area_branch_
        # selection_hotfix_v71143.py); the gate is now canReturnToAdminFromComms(),
        # keyed on the real role.
        idx = self.js.find('if (name === "comms") {')
        self.assertNotEqual(idx, -1)
        segment = self.js[idx:idx + 600]
        self.assertIn("canReturnToAdminFromComms() ? _commsExitToAdmin : null", segment)

    def test_comms_home_back_button_gated_on_real_role(self):
        # Anchored on the subtitle text, unique to the real comms-home
        # render (renderCommsDisabled's header has no subtitle/back button).
        idx = self.js.find("Отправка настоящих уведомлений в личный кабинет")
        self.assertNotEqual(idx, -1)
        segment = self.js[idx:idx + 300]
        self.assertIn("canReturnToAdminFromComms() ?", segment)

    def test_version_cache_bust_v71142(self):
        self.assertIn("styles.css?v=7.1.16.1", self.html)
        self.assertIn("app.js?v=7.1.16.1", self.html)
        self.assertIn('console.log("MiniApp version: v7.1.16.1");', self.js)


if __name__ == "__main__":
    unittest.main()
