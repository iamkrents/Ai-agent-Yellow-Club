"""Tests for v7.1.17 — "Расписание" schedule module: permissions & feature
flags.

Covers spec section 23 UI checks 51-56 (real separate page, separate
client_manager bottom tab, owner/admin/client_manager-pilot access,
everyone else forbidden) plus section 18's feature-flag requirements
(default OFF, pilot allowlist, sync/mutations independent switches, real
role never substituted by test role for backend permission checks).

Mixes real backend-permission tests (MiniAppContext + a real Storage, no
MoyKlass network) with static source checks against app.js/config.py/
web_app_server.py, consistent with this repo's existing frontend test
convention for anything that's fundamentally a markup/wiring check.

Run offline:
    python -m unittest tests.test_schedule_permissions_flags_v7117 -v
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
CONFIG_PY = (ROOT / "config.py").read_text(encoding="utf-8")
WEB_APP_SERVER_PY = (ROOT / "web_app_server.py").read_text(encoding="utf-8")


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_settings(**overrides) -> SimpleNamespace:
    base = dict(
        admin_ids=[], senior_teacher_ids=[], web_app_test_roles=True, food_module_enabled=False,
        schedule_foundation_enabled=False, schedule_foundation_pilot_telegram_ids=[],
        schedule_moyklass_sync_enabled=False, schedule_draft_mutations_enabled=False,
        food_location_yc1="Кульман 1/1", food_location_yc2="Мстиславца 6",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_context(storage: Storage, settings: SimpleNamespace):
    from web_app_server import MiniAppContext
    ctx = MiniAppContext.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = settings
    ctx.moyklass = MagicMock()
    return ctx


def _fn_body(js: str, name: str) -> str:
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\([^)]*\) \{(.*?)\n\}\n", js, re.S)
    assert m, f"function {name} not found"
    return m.group(1)


class TestRoleAccess(unittest.TestCase):
    """Checks 53, 54, 55, 56 — real backend role gate."""

    def setUp(self):
        self.storage = _make_storage()
        self.settings = _make_settings(schedule_foundation_enabled=True)
        self.ctx = _make_context(self.storage, self.settings)

    def _auth(self, user_id, role):
        self.storage.set_staff_role(user_id, role)
        return {"user_id": user_id, "full_name": "T"}

    def test_53_owner_access(self):
        auth = self._auth(1, "owner")
        self.assertIsNone(self.ctx._require_schedule_module_access(auth))

    def test_54_admin_access(self):
        auth = self._auth(2, "admin")
        self.assertIsNone(self.ctx._require_schedule_module_access(auth))

    def test_55_client_manager_access(self):
        auth = self._auth(3, "client_manager")
        self.assertIsNone(self.ctx._require_schedule_module_access(auth))

    def test_56_other_roles_forbidden(self):
        for role in ("teacher", "methodist", "intern", "director", "kitchen", "restaurant", "operations", "parent"):
            with self.subTest(role=role):
                auth = self._auth(100 + hash(role) % 1000, role)
                denied = self.ctx._require_schedule_module_access(auth)
                self.assertIsNotNone(denied, f"{role} must be denied")
                self.assertEqual(denied["reason_code"], "forbidden")


class TestFeatureFlags(unittest.TestCase):
    """Section 18 — default OFF, pilot allowlist, independent sync/mutations
    switches, and the flag is enforced server-side regardless of frontend
    state."""

    def test_flag_default_off_denies_even_owner(self):
        storage = _make_storage()
        settings = _make_settings(schedule_foundation_enabled=False)
        ctx = _make_context(storage, settings)
        storage.set_staff_role(1, "owner")
        denied = ctx._require_schedule_module_access({"user_id": 1, "full_name": "Owner"})
        self.assertIsNotNone(denied)
        self.assertEqual(denied["reason_code"], "feature_disabled")

    def test_pilot_allowlist_opens_access_without_global_flag(self):
        storage = _make_storage()
        settings = _make_settings(schedule_foundation_enabled=False, schedule_foundation_pilot_telegram_ids=[555])
        ctx = _make_context(storage, settings)
        storage.set_staff_role(555, "owner")
        self.assertIsNone(ctx._require_schedule_module_access({"user_id": 555, "full_name": "Pilot Owner"}))
        storage.set_staff_role(556, "owner")
        denied = ctx._require_schedule_module_access({"user_id": 556, "full_name": "Non-pilot Owner"})
        self.assertIsNotNone(denied)

    def test_sync_and_mutations_flags_are_independent(self):
        storage = _make_storage()
        settings = _make_settings(schedule_foundation_enabled=True, schedule_moyklass_sync_enabled=False, schedule_draft_mutations_enabled=True)
        ctx = _make_context(storage, settings)
        storage.set_staff_role(1, "owner")
        auth = {"user_id": 1, "full_name": "Owner"}
        sync_result = ctx.schedule_sync_start(auth, {})
        self.assertFalse(sync_result["ok"])
        self.assertEqual(sync_result["reason_code"], "sync_disabled")
        # mutations independently enabled -> foundation generate reaches
        # the "no active snapshot" branch, not "mutations_disabled"
        gen_result = ctx.schedule_foundation_generate(auth, {})
        self.assertNotEqual(gen_result.get("reason_code"), "mutations_disabled")

    def test_env_vars_default_false_in_config(self):
        self.assertIn('schedule_foundation_enabled=_bool(os.getenv("SCHEDULE_FOUNDATION_ENABLED", "false"), False)', CONFIG_PY)
        self.assertIn('schedule_moyklass_sync_enabled=_bool(os.getenv("SCHEDULE_MOYKLASS_SYNC_ENABLED", "false"), False)', CONFIG_PY)
        self.assertIn('schedule_draft_mutations_enabled=_bool(os.getenv("SCHEDULE_DRAFT_MUTATIONS_ENABLED", "false"), False)', CONFIG_PY)

    def test_never_uses_test_role_for_backend_permission(self):
        # _require_schedule_module_access must resolve role via
        # _role_for_user (which itself only substitutes the test role for
        # already-privileged real staff — see _can_use_role_test), never a
        # frontend-supplied role string.
        src = WEB_APP_SERVER_PY[WEB_APP_SERVER_PY.index("def _require_schedule_module_access"):]
        src = src[:src.index("\n\n    def ", 20)]
        self.assertIn("self._role_for_user(", src)
        self.assertNotIn('auth.get("role")', src)
        self.assertNotIn('auth["role"]', src)


class TestRealSeparatePageAndNav(unittest.TestCase):
    """Checks 51, 52 — a genuine peer bottom-nav tab, not a stub, and not
    the same data-tab as the pre-existing unrelated work-schedule feature."""

    def test_51_dedicated_tab_panel_exists(self):
        self.assertIn('id="tab-schedule-foundation"', INDEX_HTML)
        self.assertIn('id="scheduleFoundationRoot"', INDEX_HTML)

    def test_51b_distinct_from_preexisting_work_schedule_tab(self):
        self.assertIn('data-tab="schedule"', INDEX_HTML)          # the old teacher work-hours tab, untouched
        self.assertIn('data-tab="schedule-foundation"', INDEX_HTML)  # the new module, different value
        self.assertNotIn('data-tab="schedule-foundation"', INDEX_HTML.replace('data-tab="schedule-foundation"', "", 1))

    def test_52_client_manager_gets_the_tab_in_mvp_mode(self):
        m = re.search(r"client_manager:\s*\[([^\]]*)\]", APP_JS)
        assert m
        self.assertIn("schedule-foundation", m.group(1))
        self.assertIn("payments-workspace", m.group(1))

    def test_52b_capability_flag_gates_visibility(self):
        self.assertIn('roleCaps().canUseScheduleFoundation', APP_JS)
        self.assertIn('".schedule-foundation-only"', APP_JS.replace("'", '"'))

    def test_no_admin_exit_button_for_client_manager_schedule_tab(self):
        # the schedule module reuses the plain peer-tab pattern (like
        # payments-workspace), never comms's "exit to admin" back-button.
        self.assertNotIn("_schedExitToAdmin", APP_JS)


if __name__ == "__main__":
    unittest.main()
