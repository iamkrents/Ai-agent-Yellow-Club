"""Tests for v7.1.13 — "Единый кабинет клиента и центр уведомлений",
client cabinet shell: 4-item bottom navigation, Главная dashboard replacing
the old "my-children is home" model, child switcher, long-name handling,
and the temporary food-entry-visibility feature flag.

Covers checklist §17.A (items 1-8). JS behavior that has no backend
equivalent (nav array contents, day-chips, etc.) is verified via static
source-text assertions on miniapp/app.js/index.html/styles.css, following
this codebase's existing convention (no JS execution harness) — see e.g.
tests/test_client_schedule_availability_entry_v71123.py.

Run:
    python -m unittest tests.test_client_cabinet_shell_v7113 -v
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
INDEX_HTML = ROOT / "miniapp" / "index.html"
STYLES_CSS = ROOT / "miniapp" / "styles.css"


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage, food_entry_visible: bool = True) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(
        bot_username="yellowclubagent_bot",
        telegram_bot_token="test-secret",
        client_food_entry_visible=food_entry_visible,
        food_module_enabled=True,
        admin_ids=[],
        senior_teacher_ids=[],
        web_app_test_roles=False,
        client_cabinet_v7113_enabled=True,
        client_cabinet_v7113_pilot_telegram_ids=[],
        client_notifications_enabled=True,
        client_notifications_pilot_telegram_ids=[],
    )
    ctx._role_store: dict[int, str] = {}

    def _role_for_user(uid: int) -> str:
        return ctx._role_store.get(int(uid), "other")

    ctx._role_for_user = _role_for_user
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


def _food_link(storage: Storage, mk_student_id, full_name, parent_tid):
    """Legacy Food Module link — completely separate table/mechanism."""
    with storage._connect() as conn:
        conn.execute(
            "INSERT INTO camp_children (created_at, updated_at, mk_student_id, full_name, active) VALUES (?, ?, ?, ?, 1)",
            (now_iso(), now_iso(), mk_student_id, full_name),
        )
        conn.execute(
            "INSERT INTO parent_child_links (created_at, confirmed_at, parent_telegram_id, mk_student_id, link_code, active) VALUES (?, ?, ?, ?, ?, 1)",
            (now_iso(), now_iso(), str(parent_tid), mk_student_id, f"YC-{mk_student_id}"),
        )


class TestNavAndDashboardBackend(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def test_1_client_bootstrap_includes_client_kind_and_unread_count(self):
        """1-2. /api/me exposes clientKind/unreadNotificationCount so the
        4-item nav can render correctly on first paint (no extra request)."""
        _cl_link(self.storage, "20001", "Regular Kid", "6001")
        parent = _auth(6001, "parent", self.ctx)
        me = self.ctx.me(parent)
        self.assertEqual(me["clientKind"], "regular")
        self.assertIn("unreadNotificationCount", me)
        self.assertIn("clientFoodEntryVisible", me)
        self.assertIn("clientCabinetV7113Enabled", me)
        self.assertIn("clientNotificationsEnabled", me)

    def test_3_single_child_no_switcher_needed_data_available(self):
        _cl_link(self.storage, "20002", "Only Kid", "6002")
        parent = _auth(6002, "parent", self.ctx)
        children = self.ctx.client_children_list(parent)
        self.assertEqual(children["client_count"], 1)

    def test_4_multiple_children_all_listed_for_switcher(self):
        _cl_link(self.storage, "20003", "Kid A", "6003")
        _cl_link(self.storage, "20004", "Kid B", "6003")
        parent = _auth(6003, "parent", self.ctx)
        children = self.ctx.client_children_list(parent)
        self.assertEqual(children["client_count"], 2)

    def test_5_payments_carry_mk_user_id_for_per_child_context(self):
        """5. Home dashboard filters payments by the active child — requires
        mk_user_id on each payment item (additive passthrough field)."""
        _cl_link(self.storage, "20005", "Kid", "6005")
        with self.storage._connect() as conn:
            conn.execute(
                """INSERT INTO payment_intents
                   (public_id, mk_user_id, student_name, amount_minor, amount_byn, purpose,
                    status, client_visibility, created_at, updated_at)
                   VALUES ('pi_x1', 20005, 'Kid', 13500, 135.0, 'other', 'ready', 'published', ?, ?)""",
                (now_iso(), now_iso()),
            )
        parent = _auth(6005, "parent", self.ctx)
        payments = self.ctx.client_payments_list(parent)
        self.assertTrue(payments["ok"], payments)
        self.assertEqual(len(payments["payments"]), 1)
        self.assertEqual(str(payments["payments"][0]["mk_user_id"]), "20005")

    def test_6_long_child_name_preserved_verbatim_by_backend(self):
        """6. Backend never truncates a long name — 2-line wrap is a pure
        frontend/CSS concern (verified via static CSS assertion below)."""
        long_name = "Вероника-Александра Ковалёва-Штайнберг-Александрова"
        _cl_link(self.storage, "20006", long_name, "6006")
        parent = _auth(6006, "parent", self.ctx)
        children = self.ctx.client_children_list(parent)
        self.assertEqual(children["children"][0]["display_name"], long_name)

    def test_7_food_card_visible_by_default_for_combined_client(self):
        _cl_link(self.storage, "20007", "Kid", "6007")
        _food_link(self.storage, "food-6007", "Food Kid", "6007")
        parent = _auth(6007, "parent", self.ctx)
        me = self.ctx.me(parent)
        self.assertEqual(me["clientKind"], "combined")
        self.assertTrue(me["clientFoodEntryVisible"])

    def test_8_food_entry_flag_off_hides_card_without_deleting_data(self):
        ctx_off = _make_ctx(self.storage, food_entry_visible=False)
        _cl_link(self.storage, "20008", "Kid", "6008")
        _food_link(self.storage, "food-6008", "Food Kid", "6008")
        parent = _auth(6008, "parent", ctx_off)
        me = ctx_off.me(parent)
        self.assertFalse(me["clientFoodEntryVisible"])
        # Underlying food link/child rows are completely untouched.
        food_children = self.storage.list_children_for_parent("6008")
        self.assertEqual(len(food_children), 1)


class TestStaffNavUnaffected(unittest.TestCase):
    """2. Staff nav (data-tab list outside parent-only) must not change."""

    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_staff_tabs_still_present(self):
        for tab in ["lessons", "reports", "schedule", "tasks", "admin", "payments-workspace"]:
            self.assertIn(f'data-tab="{tab}"', self.html)

    def test_admin_nav_roles_unchanged(self):
        idx = self.js.find("_adminNavRoles")
        block = self.js[idx:idx + 200]
        self.assertIn('"owner"', block)
        self.assertIn('"admin"', block)
        self.assertIn('"operations"', block)


class TestParentNavStatic(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")
        self.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_four_cabinet_tabs_exist_in_html(self):
        for tab in ["home", "client-payments", "notifications", "more", "profile"]:
            self.assertIn(f'data-tab="{tab}"', self.html)

    def test_parent_allowed_arrays_are_exactly_four_items(self):
        idx = self.js.find("const parentAllowed = !cabinetEnabled")
        self.assertNotEqual(idx, -1, "dynamic parentAllowed logic not found")
        block = self.js[idx:idx + 400]
        self.assertIn('"home", "client-payments", "notifications", "more"', block)
        self.assertIn('"food", "notifications", "help", "profile"', block)
        self.assertIn('"my-children", "food", "client-payments", "help"', block)

    def test_owner_test_role_panel_pattern_reused_for_notifications(self):
        self.assertIn('id="ownerTestNotificationPanel"', self.html)
        self.assertIn("canUseTestRoles", self.js)

    def test_bottom_nav_opaque_for_cabinet_only(self):
        idx = self.css.find("body.role-parent-cabinet .tabs.bottom-tabbar")
        self.assertNotEqual(idx, -1)
        segment = self.css[idx:idx + 200]
        self.assertIn("background: var(--card", segment)
        self.assertIn("backdrop-filter: none", segment)

    def test_long_name_gets_two_line_clamp_not_truncation(self):
        # v7.1.13 appends its own .parent-child-name rule at the END of the
        # file — CSS cascade means it wins over the older 1-line rule
        # earlier in the file, so this asserts on the LAST occurrence.
        idx = self.css.rfind(".parent-child-name {")
        self.assertNotEqual(idx, -1)
        segment = self.css[idx:idx + 200]
        self.assertIn("-webkit-line-clamp: 2", segment)


class TestRound2HeaderAndNavStatic(unittest.TestCase):
    """§A items 1-3, 6-7 — stale header removal, monochrome nav/more icons,
    reworded headline, child-switcher fixes. Static source assertions,
    consistent with this file's existing convention."""

    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_1_topbar_hidden_for_whole_cabinet_no_stale_title(self):
        idx = self.css.find("body.role-parent-cabinet .topbar { display: none; }")
        self.assertNotEqual(idx, -1)
        # The old bug: a payments-tab click listener force-set #appTitle to
        # a stale "Оплаты · Yellow Club" that never got reset on other tabs.
        idx2 = self.js.find('.tab[data-tab="client-payments"]')
        self.assertNotEqual(idx2, -1)
        block = self.js[idx2:idx2 + 400]
        self.assertNotIn('appTitle").textContent = "Оплаты', block)

    def test_2_client_nav_scoped_block_no_dark_pill_monochrome(self):
        idx = self.css.find("body.role-parent-cabinet .tabs.bottom-tabbar .tab {")
        self.assertNotEqual(idx, -1)
        segment = self.css[idx:idx + 1200]
        self.assertIn("color: #8a8f9c", segment)  # gray inactive
        self.assertIn("background: var(--yellow", segment)  # yellow active icon container
        self.assertIn("color: var(--ink", segment)  # dark active icon/text
        # Staff's separate .tab.active rules must not be touched by this block.
        self.assertNotIn("body.role-parent-cabinet .tab.active {", self.css)

    def test_3_more_row_icons_use_shared_monochrome_icon_set(self):
        idx = self.css.find(".cab-more-row-icon {")
        self.assertNotEqual(idx, -1)
        segment = self.css[idx:idx + 300]
        self.assertIn("width: 36px", segment)
        self.assertIn("CAB_ICONS", self.js)
        self.assertIn("function _applyCabinetNavIcons", self.js)
        # More rows for all 5 approved entries pull from the same object —
        # no per-row emoji literal left behind.
        for key in ["food", "help", "profile", "myChildren", "availability"]:
            self.assertIn(f"{key}:", self.js[self.js.find("const CAB_ICONS"):self.js.find("const CAB_ICONS") + 3000])

    def test_6_child_switcher_active_state_and_long_name_handling(self):
        idx = self.css.find(".cab-switch-chip")
        self.assertNotEqual(idx, -1)
        segment = self.css[idx:idx + 600]
        self.assertIn("max-width", segment)
        self.assertIn("function _cabShortChildName", self.js)
        idx2 = self.js.find("function _cabChildSwitcherHtml")
        block = self.js[idx2:idx2 + 700]
        self.assertIn("title=", block)
        self.assertIn("aria-label=", block)
        self.assertIn("_cabShortChildName(fullName)", block)

    def test_7_headline_wording_is_client_friendly_not_technical(self):
        idx = self.js.find("function _cabHeadline")
        self.assertNotEqual(idx, -1)
        body = self.js[idx:idx + 700]
        self.assertIn("Всё в порядке — открытых вопросов нет.", body)
        self.assertIn("вопрос, требующий внимания.", body)
        self.assertIn("вопроса, требующих внимания.", body)
        # The old internal/technical phrasing must be fully gone.
        self.assertNotIn("дело, которое стоит закрыть", body)

    def test_14_long_name_two_line_wrap_does_not_touch_switcher_data_scope(self):
        # 14. Graceful shortening lives in the compact chip only; the "Мои
        # дети" screen's own name element gets the 2-line clamp instead of
        # truncation (already covered by test_long_name_gets_two_line_clamp_
        # not_truncation above) — this asserts switching chips carries an
        # explicit per-child mk id, so no cross-child data bleed is possible
        # at the DOM level.
        idx = self.js.find("function _cabChildSwitcherHtml")
        block = self.js[idx:idx + 700]
        self.assertIn('data-mk="${escapeAttr(c.mk_user_id)}"', block)


class TestPremiumHeaderStatic(unittest.TestCase):
    """Visual polish pass — one shared _cabHeaderHtml() component, two
    variants (home: eyebrow+avatar+greeting+bell; sub: title+subtitle,
    optional back button), reused by every cabinet screen instead of the
    old plain .section-head or the removed global .topbar."""

    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.css = STYLES_CSS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_shared_header_function_has_both_variants(self):
        idx = self.js.find("function _cabHeaderHtml")
        self.assertNotEqual(idx, -1)
        body = self.js[idx:idx + 1800]
        self.assertIn('o.mode === "home"', body)
        self.assertIn("cab-header-eyebrow", body)
        self.assertIn("cab-header-avatar", body)
        self.assertIn("CAB_ICONS.notifications", body)
        self.assertIn("cab-header-sub-title", body)
        self.assertIn("cab-header-sub-desc", body)
        # No emoji bell left anywhere in the header renderer.
        self.assertNotIn("🔔", body)

    def test_home_uses_shared_header_not_bespoke_markup(self):
        fn = self.js[self.js.find("function renderClientHome"):]
        fn = fn[:fn.find("\nfunction ")]
        self.assertIn("_cabHeaderHtml({", fn)
        self.assertIn('mode: "home"', fn)
        self.assertNotIn("🔔", fn)

    def test_payments_notifications_availability_use_sub_header(self):
        # Payments: JS-rendered (dynamic child-name subtitle).
        self.assertIn("function _cabRenderPaymentsHeader", self.js)
        self.assertIn('mode: "sub"', self.js[self.js.find("function _cabRenderPaymentsHeader"):self.js.find("function _cabRenderPaymentsHeader") + 700])
        self.assertIn('id="paymentsHeaderSlot"', self.html)
        # Notifications/More/Profile/My children: static premium markup.
        for slot_hint in ['id="notifListSubtitle"', "Профиль и настройки", 'id="clientProfileContent"']:
            self.assertIn(slot_hint, self.html)
        self.assertIn('cab-header cab-header--sub', self.html)
        # Availability full-page screen reuses the same icon-button + title classes.
        idx = self.html.find('id="tab-availability"')
        segment = self.html[idx:idx + 900]
        self.assertIn("cab-icon-btn", segment)
        self.assertIn("cab-header-sub-title", segment)
        self.assertNotIn(">‹<", segment)  # old plain-text back glyph is gone

    def test_food_only_landing_gets_greeting_header_combined_gets_sub_header(self):
        idx = self.js.find("function _cabRenderFoodHeader")
        self.assertNotEqual(idx, -1)
        body = self.js[idx:idx + 1100]
        self.assertIn('clientKind !== "food_only"', body)
        self.assertIn('mode: "sub"', body)
        self.assertIn('mode: "home"', body)
        self.assertIn('id="foodHeaderSlot"', self.html)

    def test_shared_icon_button_class_used_by_bell_and_back(self):
        idx = self.css.find(".cab-icon-btn {")
        self.assertNotEqual(idx, -1)
        segment = self.css[idx:idx + 400]
        self.assertIn("box-shadow", segment)
        self.assertIn("43px", segment)

    def test_more_nav_icon_is_stroke_not_fill_for_consistency(self):
        idx = self.js.find("const CAB_ICONS")
        block = self.js[idx:idx + 3000]
        more_idx = block.find("more:")
        more_line = block[more_idx:block.find("\n", more_idx)]
        self.assertIn('stroke="currentColor"', more_line)
        self.assertIn('stroke-width="2"', more_line)
        self.assertNotIn('fill="currentColor"', more_line)


class TestPremiumHeaderCardRound2(unittest.TestCase):
    """Round 2 of the visual polish pass — the header is now a real
    branded card surface (gradient/border/shadow), not typography floating
    on the plain page background; the client bottom nav no longer has a
    dark-mode override (was the actual root cause of the "heavy navy
    panel" feedback, since the approved prototype has no dark variant)."""

    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_header_is_a_card_surface_not_bare_typography(self):
        idx = self.css.find(".cab-header {")
        self.assertNotEqual(idx, -1)
        segment = self.css[idx:idx + 400]
        self.assertIn("linear-gradient", segment)
        self.assertIn("border-radius: 22px", segment)
        self.assertIn("box-shadow", segment)
        self.assertIn("border: 1px solid", segment)

    def test_subpage_header_shares_surface_but_more_compact(self):
        idx = self.css.find(".cab-header--sub {")
        self.assertNotEqual(idx, -1)
        segment = self.css[idx:idx + 200]
        self.assertIn("padding: 13px 15px", segment)

    def test_client_nav_has_no_dark_mode_override(self):
        # The nav's base rule (light, opaque) must be the only rule setting
        # its background under body.role-parent-cabinet — no dark-theme
        # variant left that could turn it navy again. (The hex code may
        # still appear in an explanatory code comment about the removal.)
        self.assertNotIn('body.role-parent-cabinet .tabs.bottom-tabbar { background: #171c2b; }', self.css)
        self.assertEqual(self.css.count("body.role-parent-cabinet .tabs.bottom-tabbar {"), 1)

    def test_unread_badge_caps_at_99_plus(self):
        idx = self.js.find("function _cabHeaderHtml")
        body = self.js[idx:idx + 1800]
        self.assertIn('o.unread > 99 ? "99+"', body)

    def test_home_subtitle_reflects_client_kind_and_child_count(self):
        fn = self.js[self.js.find("function renderClientHome"):]
        fn = fn[:fn.find("\nfunction ")]
        self.assertIn('clientKind === "combined"', fn)
        self.assertIn("Курсы и городская программа", fn)
        self.assertIn("Выбран ребёнок:", fn)

    def test_food_only_header_shows_programme_not_child_name(self):
        idx = self.js.find("function _cabRenderFoodHeader")
        body = self.js[idx:idx + 1300]
        self.assertIn("campClassNameFilter", body)
        self.assertIn("Городская программа", body)


if __name__ == "__main__":
    unittest.main()
