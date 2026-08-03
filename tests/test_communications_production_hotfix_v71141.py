"""Tests for v7.1.14.1 — production hotfix: guaranteed «Рассылки» entry
point on the Admin screen, and the global top safe-area chain.

Context: after the v7.1.14 deploy, staff reported the "Рассылки" bottom-nav
tab was effectively undiscoverable (a 7th squeezed icon in an already-full
bottom-tabbar) even though the backend gate itself was correct, and a
separately-reported global header safe-area regression that a full diff
audit (f4f0a1f..385d0e5) showed had no code-level cause in this repo —
every changed line across config.py/storage.py/web_app_server.py/
miniapp/{app.js,index.html,styles.css} was a pure addition, and the one
real (but narrowly-scoped, non-global) inconsistency found was the
confirmation sheet using a bare env(safe-area-inset-top) instead of the
app's own proven --app-top-safe-offset chain — fixed here alongside the
new guaranteed entry point.

Covers:
  1.  a real owner with a pilot Telegram id sees the card (capability
      true even with the global flag off).
  2.  a real admin sees the card when the global flag is on.
  3.  unauthorized roles (teacher/intern/kitchen/restaurant/client_manager/
      client/food-only client) do not.
  4.  test-role substitution is never the source of this permission.
  5.  the card opens communications home (wired to activateTab('comms')).
  6.  "back" returns to «Админ» (in-page button + Telegram BackButton).
  7.  the old single-client test notification sender stays separate.
  8.  the communications UI is not swapped for the old test block.
  9.  the global header still uses the proven --app-top-safe-offset chain.
  10. the new CSS introduces no unscoped/global selector.
  11. the confirmation sheet keeps its own safe-area (via the same chain).
  12. no double top offset (the chain is defined exactly once).
  13. no new fixed global +56px offset.
  14. 360/375px width, no horizontal overflow from the new card.
  15. version/cache-bust is v7.1.14.1 everywhere required.

Run:
    python -m unittest tests.test_communications_production_hotfix_v71141 -v
"""
from __future__ import annotations

import re
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import web_app_server as srv  # noqa: E402
from storage import Storage  # noqa: E402

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
STYLES_CSS = ROOT / "miniapp" / "styles.css"

REAL_OWNER_TELEGRAM_ID = 7850692063  # the actual reported production owner id


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _settings(**overrides):
    base = dict(
        client_communications_enabled=False,
        client_communications_pilot_telegram_ids=[REAL_OWNER_TELEGRAM_ID],
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


class TestRealOwnerPilotAccess(unittest.TestCase):
    """1, 2, 3, 4 — the exact reported scenario: global flag off, pilot
    allowlist holding the real owner's Telegram id."""

    def setUp(self):
        self.storage = _tmp_storage()
        self.storage.set_staff_role(REAL_OWNER_TELEGRAM_ID, "owner")
        self.storage.set_staff_role(2001, "admin")
        self.storage.set_staff_role(2002, "teacher")
        self.storage.set_staff_role(2003, "intern")
        self.storage.set_staff_role(2004, "kitchen")
        self.storage.set_staff_role(2005, "restaurant")
        self.storage.set_staff_role(2006, "client_manager")

    def test_1_real_owner_with_pilot_id_sees_card(self):
        ctx = _make_ctx(self.storage)  # global disabled, pilot list has the real owner id
        caps = ctx._capabilities_for_user(REAL_OWNER_TELEGRAM_ID)
        self.assertTrue(caps["canUseCommunications"], "pilot-listed real owner must see the entry point")
        self.assertTrue(ctx.me(_auth(REAL_OWNER_TELEGRAM_ID))["communicationsEnabled"])

    def test_2_real_admin_sees_card_when_globally_enabled(self):
        ctx = _make_ctx(self.storage, client_communications_enabled=True, client_communications_pilot_telegram_ids=[])
        self.assertTrue(ctx._capabilities_for_user(2001)["canUseCommunications"])

    def test_3_unauthorized_roles_do_not_see_card(self):
        # v7.1.14.2 — client_manager (uid 2006) is no longer in this list:
        # it was deliberately added to CLIENT_COMMUNICATIONS_ROLES (see
        # test_shell_client_cabinet_comms_hotfix_v71142.TestClientManagerCommsSwap).
        ctx = _make_ctx(self.storage, client_communications_enabled=True, client_communications_pilot_telegram_ids=[])
        for uid in (2002, 2003, 2004, 2005, 999999):
            self.assertFalse(
                ctx._capabilities_for_user(uid)["canUseCommunications"],
                f"uid={uid} must not see the communications card",
            )

    def test_4_test_role_is_never_the_permission_source(self):
        ctx = _make_ctx(self.storage)  # global off, only the real owner id is pilot-listed
        # A non-owner previewing "owner" via test role must still be denied —
        # the gate is keyed on _base_role_for_user, never _role_for_user.
        ctx._role_for_user = lambda uid: "owner"
        self.assertFalse(ctx._capabilities_for_user(2002)["canUseCommunications"])
        # And the real owner keeps access even while previewing a lower role.
        ctx._role_for_user = lambda uid: "teacher"
        self.assertTrue(ctx._capabilities_for_user(REAL_OWNER_TELEGRAM_ID)["canUseCommunications"])


class TestAdminEntryCardStatic(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")
        self.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_5_card_opens_communications_home(self):
        self.assertIn('id="commsAdminEntryCard"', self.html)
        self.assertIn('id="commsAdminEntryBtn"', self.html)
        self.assertIn(
            '$("commsAdminEntryBtn")?.addEventListener("click", () => activateTab("comms"));', self.js,
        )

    def test_6_back_returns_to_admin(self):
        # In-page header button on comms home.
        self.assertIn("activateTab('admin')", self.js)
        # Section-level Telegram BackButton also exits to Admin.
        self.assertIn("function _commsExitToAdmin() { activateTab(\"admin\"); }", self.js)
        idx = self.js.find('if (name === "comms")')
        self.assertNotEqual(idx, -1)
        segment = self.js[idx:idx + 500]
        # v7.1.14.2 — client_manager also reaches "Рассылки" now, but has no
        # Admin tab to return to, so the handler is only wired when the real
        # role can actually reach Admin (canUseAdmin()); owner/admin still
        # get the exact same _commsExitToAdmin behavior as before.
        self.assertIn("_commsSetBackButton(canUseAdmin() ? _commsExitToAdmin : null)", segment)

    def test_7_old_test_sender_stays_separate(self):
        self.assertIn('id="ownerTestNotificationPanel"', self.html)
        self.assertNotIn('id="commsAdminEntryCard"', self.html.split('id="ownerTestNotificationPanel"')[1].split("</section>")[0])

    def test_8_ui_not_swapped_for_old_test_block(self):
        # The new card and the old panel are distinct DOM nodes with
        # distinct ids, gated by distinct capability flags.
        self.assertIn("comms-admin-entry-card", self.html)
        self.assertIn("test-role-panel", self.html)
        card_idx = self.html.find('id="commsAdminEntryCard"')
        otn_idx = self.html.find('id="ownerTestNotificationPanel"')
        self.assertNotEqual(card_idx, -1)
        self.assertNotEqual(otn_idx, -1)
        self.assertNotEqual(card_idx, otn_idx)

    def test_9_global_header_uses_proven_safe_area_chain(self):
        self.assertIn(
            "--app-top-safe-offset:   calc(var(--tg-safe-top) + var(--tg-native-top-overlay));", self.css,
        )
        self.assertIn(
            "body.is-telegram-webapp .app-shell {\n  padding-top: var(--app-top-safe-offset) !important;\n}",
            self.css,
        )

    def test_10_no_unscoped_global_selector_in_new_css(self):
        start = self.css.find("/* ── v7.1.14 — staff \"Рассылки\"")
        self.assertNotEqual(start, -1)
        block = self.css[start:]
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("/*") or stripped.startswith("*") or stripped.startswith("@media") or stripped == "}":
                continue
            if "{" not in stripped:
                continue
            selector = stripped.split("{")[0].strip()
            for sel in selector.split(","):
                sel = sel.strip()
                if not sel:
                    continue
                self.assertTrue(
                    sel.startswith(".comms-") or sel.startswith("#commsConfirmModal") or ".comms-" in sel,
                    f"unscoped selector leaked into comms CSS: {sel!r}",
                )

    def test_11_confirm_sheet_keeps_safe_area_via_shared_chain(self):
        idx = self.css.find(".comms-confirm-sheet {")
        self.assertNotEqual(idx, -1)
        block = self.css[idx:idx + 700]
        self.assertIn("var(--app-top-safe-offset, env(safe-area-inset-top, 0px))", block)
        self.assertNotIn("calc(100dvh - env(safe-area-inset-top, 0px) - 8px)", block, "must not bypass the shared chain with a bare env()")

    def test_12_no_double_top_offset(self):
        # --app-top-safe-offset is defined in exactly one :root block —
        # the comms module never redefines or shadows it.
        self.assertEqual(self.css.count("--app-top-safe-offset:"), 1)
        self.assertNotIn("--app-top-safe-offset", self.js[self.js.find('// ── v7.1.14 — staff'):])

    def test_13_no_new_fixed_global_56px_offset(self):
        start = self.css.find("/* ── v7.1.14 — staff \"Рассылки\"")
        block = self.css[start:]
        self.assertNotIn("+ 56px", block)
        self.assertNotIn("56px", block)

    def test_14_admin_card_no_fixed_width_360_375_safe(self):
        self.assertIn(".comms-admin-entry-card { margin-bottom: 14px; }", self.css)
        idx = self.css.find(".comms-admin-entry-card")
        block = self.css[idx:idx + 300]
        self.assertNotRegex(block, r"width:\s*\d+px")

    def test_15_version_cache_bust_v71141(self):
        self.assertIn("styles.css?v=7.1.14.2", self.html)
        self.assertIn("app.js?v=7.1.14.2", self.html)
        self.assertIn('console.log("MiniApp version: v7.1.14.2");', self.js)


if __name__ == "__main__":
    unittest.main()
