"""Tests for v7.1.13.1 — owner/admin-only "Клиент / родитель" test role.

Production-smoke blocker this hotfix fixes: the pilot allowlist correctly
opens the new client cabinet for the owner's real Telegram id, but a pilot
allowlist only changes WHICH FEATURES a user can reach — it never changes
WHO they are. The owner's real Telegram id has no rows in
client_parent_child_links/parent_child_links (the owner is not a real
client), so flipping their effective role to "parent" alone always shows an
empty cabinet. This hotfix adds a server-resolved, re-validated "trusted
client context" so the owner can preview an EXISTING linked client's real
cabinet without creating/modifying any links, without touching payment
automation, and without ever being spoofable by a real client via
query/body/frontend.

Covers checklist items 1-18 from the hotfix spec.

Run:
    python -m unittest tests.test_owner_test_client_context_v71131 -v
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
WEB_APP_SERVER_PY = ROOT / "web_app_server.py"

OWNER_UID = 900001
ADMIN_UID = 900002
TEACHER_UID = 900003
PARENT_A_UID = 700001
PARENT_B_UID = 700002


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage) -> MiniAppContext:
    """Real _role_for_user/_can_use_role_test/_base_role_for_user (NOT
    monkey-patched) — this hotfix is precisely about that real machinery,
    so tests must exercise it, not a test-harness shortcut."""
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(
        bot_username="yellowclubagent_bot", telegram_bot_token="test-secret",
        admin_ids=[OWNER_UID], senior_teacher_ids=[],
        web_app_test_roles=True,
        client_food_entry_visible=True, food_module_enabled=True,
        mvp_release_mode=False,
        client_cabinet_v7113_enabled=False, client_cabinet_v7113_pilot_telegram_ids=[OWNER_UID],
        client_notifications_enabled=False, client_notifications_pilot_telegram_ids=[OWNER_UID],
    )
    ctx.moyklass = types.SimpleNamespace(request=lambda *a, **k: types.SimpleNamespace(ok=False, data=None))
    return ctx


def _auth(uid: int) -> dict:
    return {"user_id": uid}


def _cl_link(storage: Storage, mk_user_id, child_name, parent_tid, staff_uid="1"):
    code = storage.create_client_link_code(mk_user_id, child_name, staff_uid)
    assert code["ok"], code
    r = storage.link_client_child(str(parent_tid), code["code"], now_iso())
    assert r["ok"], r
    return r


def _food_link(storage: Storage, mk_student_id, full_name, parent_tid):
    with storage._connect() as conn:
        conn.execute(
            "INSERT INTO camp_children (created_at, updated_at, mk_student_id, full_name, active) VALUES (?, ?, ?, ?, 1)",
            (now_iso(), now_iso(), mk_student_id, full_name),
        )
        conn.execute(
            "INSERT INTO parent_child_links (created_at, confirmed_at, parent_telegram_id, mk_student_id, link_code, active) VALUES (?, ?, ?, ?, ?, 1)",
            (now_iso(), now_iso(), str(parent_tid), mk_student_id, f"YC-{mk_student_id}"),
        )


def _link_counts(storage: Storage) -> tuple[int, int]:
    with storage._connect() as conn:
        n_client = conn.execute("SELECT COUNT(*) FROM client_parent_child_links").fetchone()[0]
        n_food = conn.execute("SELECT COUNT(*) FROM parent_child_links").fetchone()[0]
    return n_client, n_food


class OwnerTestClientContextBase(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)


class Test01RoleVisibility(OwnerTestClientContextBase):
    def test_1_owner_sees_client_parent_role(self):
        me = self.ctx.me(_auth(OWNER_UID))
        values = {o["value"] for o in me.get("roleOptions", [])}
        self.assertIn("parent", values)
        opt = next(o for o in me["roleOptions"] if o["value"] == "parent")
        self.assertEqual(opt["label"], "Клиент / родитель")

    def test_2_admin_sees_it_per_existing_policy(self):
        """2. _can_use_role_test already treats admin as full-admin — this
        hotfix deliberately reuses that existing policy rather than
        inventing a narrower one just for this role."""
        self.storage.set_staff_role(ADMIN_UID, "admin")
        me = self.ctx.me(_auth(ADMIN_UID))
        values = {o["value"] for o in me.get("roleOptions", [])}
        self.assertIn("parent", values)

    def test_3_staff_and_client_do_not_see_it(self):
        self.storage.set_staff_role(TEACHER_UID, "teacher")
        me_teacher = self.ctx.me(_auth(TEACHER_UID))
        self.assertNotIn("roleOptions", me_teacher)  # can_test gate itself is False

        _cl_link(self.storage, "50001", "Kid", PARENT_A_UID)
        me_parent = self.ctx.me(_auth(PARENT_A_UID))
        self.assertNotIn("roleOptions", me_parent)


class Test04RejectWithoutContext(OwnerTestClientContextBase):
    def test_4_direct_role_switch_to_parent_rejected(self):
        r = self.ctx.set_test_role(_auth(OWNER_UID), {"role": "parent", "enabled": True})
        self.assertFalse(r["ok"], r)
        # And no test-client mode got activated as a side effect.
        self.assertFalse(self.ctx._owner_test_client_mode_active(_auth(OWNER_UID)))


class Test05SpoofingRejected(OwnerTestClientContextBase):
    def test_5_nonexistent_parent_id_rejected(self):
        r = self.ctx.owner_test_client_select(_auth(OWNER_UID), {"parent_telegram_id": "9999999"})
        self.assertFalse(r["ok"], r)
        self.assertFalse(self.ctx._owner_test_client_mode_active(_auth(OWNER_UID)))

    def test_5b_non_owner_cannot_call_lookup_or_select(self):
        _cl_link(self.storage, "50002", "Kid", PARENT_A_UID)
        r1 = self.ctx.owner_test_client_lookup(_auth(PARENT_A_UID), {"query": "50002"})
        self.assertFalse(r1["ok"], r1)
        r2 = self.ctx.owner_test_client_select(_auth(PARENT_A_UID), {"parent_telegram_id": str(PARENT_A_UID)})
        self.assertFalse(r2["ok"], r2)

    def test_5c_effective_identity_never_substituted_for_real_parent(self):
        """A real parent's own auth["user_id"] must always resolve to
        itself — _effective_client_identity only ever substitutes for the
        real owner/admin path, regardless of any staff_users state."""
        _cl_link(self.storage, "50003", "Kid", PARENT_A_UID)
        self.assertEqual(self.ctx._effective_client_identity(_auth(PARENT_A_UID)), str(PARENT_A_UID))


class Test06And07ContextSelectionAndIsolation(OwnerTestClientContextBase):
    def test_6_owner_opens_real_linked_parent_cabinet(self):
        _cl_link(self.storage, "60001", "Тимофей Волков", PARENT_A_UID)
        lookup = self.ctx.owner_test_client_lookup(_auth(OWNER_UID), {"query": "60001"})
        self.assertTrue(lookup["ok"], lookup)
        candidates = lookup["candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["parent_telegram_id"], str(PARENT_A_UID))

        sel = self.ctx.owner_test_client_select(_auth(OWNER_UID), {"parent_telegram_id": str(PARENT_A_UID)})
        self.assertTrue(sel["ok"], sel)
        self.assertTrue(self.ctx._owner_test_client_mode_active(_auth(OWNER_UID)))

        children = self.ctx.client_children_list(_auth(OWNER_UID))
        self.assertTrue(children["ok"], children)
        self.assertEqual(children["client_count"], 1)
        self.assertEqual(str(children["children"][0]["mk_user_id"]), "60001")

    def test_7_multiple_children_do_not_mix_across_parents(self):
        _cl_link(self.storage, "70001", "Kid A", PARENT_A_UID)
        _cl_link(self.storage, "70002", "Kid B", PARENT_B_UID)
        self.ctx.owner_test_client_select(_auth(OWNER_UID), {"parent_telegram_id": str(PARENT_A_UID)})
        children = self.ctx.client_children_list(_auth(OWNER_UID))
        mk_ids = {str(c["mk_user_id"]) for c in children["children"]}
        self.assertEqual(mk_ids, {"70001"})
        self.assertNotIn("70002", mk_ids)

    def test_7b_lookup_by_telegram_id_returns_all_their_children(self):
        _cl_link(self.storage, "70003", "Kid A", PARENT_A_UID)
        _cl_link(self.storage, "70004", "Kid B", PARENT_A_UID)
        lookup = self.ctx.owner_test_client_lookup(_auth(OWNER_UID), {"query": str(PARENT_A_UID)})
        self.assertTrue(lookup["ok"], lookup)
        cand = lookup["candidates"][0]
        mk_ids = {c["mk_user_id"] for c in cand["children"]}
        self.assertEqual(mk_ids, {"70003", "70004"})


class Test08And18Reset(OwnerTestClientContextBase):
    def test_8_reset_returns_owner_ops(self):
        _cl_link(self.storage, "80001", "Kid", PARENT_A_UID)
        self.ctx.owner_test_client_select(_auth(OWNER_UID), {"parent_telegram_id": str(PARENT_A_UID)})
        self.assertTrue(self.ctx._owner_test_client_mode_active(_auth(OWNER_UID)))

        r = self.ctx.set_test_role(_auth(OWNER_UID), {"enabled": False})
        self.assertTrue(r["ok"], r)
        self.assertFalse(self.ctx._owner_test_client_mode_active(_auth(OWNER_UID)))
        self.assertEqual(self.ctx._role_for_user(OWNER_UID), "owner")
        me = self.ctx.me(_auth(OWNER_UID))
        self.assertFalse(me["ownerTestClientMode"])

    def test_18_back_button_reuses_same_reset_path(self):
        """The frontend's "Вернуться в кабинет владельца" button calls the
        exact same clearTestRole()/set_test_role(enabled:false) path as
        "Сбросить" — verified at the JS source level since both buttons
        must behave identically per the spec."""
        js = APP_JS.read_text(encoding="utf-8")
        idx = js.find('$("ownerTestClientBackBtn")')
        self.assertNotEqual(idx, -1)
        line = js[idx:idx + 120]
        self.assertIn("clearTestRole", line)


class Test09And10NoMutationOfRealData(OwnerTestClientContextBase):
    def test_9_real_owner_role_unchanged_in_db(self):
        _cl_link(self.storage, "90001", "Kid", PARENT_A_UID)
        self.ctx.owner_test_client_select(_auth(OWNER_UID), {"parent_telegram_id": str(PARENT_A_UID)})
        # Real role is admin_ids-derived ("owner") and untouched by test mode.
        self.assertEqual(self.ctx._base_role_for_user(OWNER_UID), "owner")

    def test_10_no_links_created_or_modified(self):
        _cl_link(self.storage, "90002", "Kid", PARENT_A_UID)
        _food_link(self.storage, "food-90002", "Food Kid", PARENT_B_UID)
        before = _link_counts(self.storage)
        self.ctx.owner_test_client_lookup(_auth(OWNER_UID), {"query": "90002"})
        self.ctx.owner_test_client_select(_auth(OWNER_UID), {"parent_telegram_id": str(PARENT_A_UID)})
        self.ctx.client_children_list(_auth(OWNER_UID))
        self.ctx.client_payments_list(_auth(OWNER_UID))
        after = _link_counts(self.storage)
        self.assertEqual(before, after)


class Test11PaymentActionsBlocked(OwnerTestClientContextBase):
    def test_11_card_token_blocked_in_test_mode(self):
        _cl_link(self.storage, "11001", "Kid", PARENT_A_UID)
        with self.storage._connect() as conn:
            conn.execute(
                """INSERT INTO payment_intents
                   (public_id, mk_user_id, student_name, amount_minor, amount_byn, purpose,
                    status, client_visibility, created_at, updated_at)
                   VALUES ('pi_11001', '11001', 'Kid', 13500, 135.0, 'other', 'ready', 'published', ?, ?)""",
                (now_iso(), now_iso()),
            )
        self.ctx.owner_test_client_select(_auth(OWNER_UID), {"parent_telegram_id": str(PARENT_A_UID)})
        r = self.ctx.client_payment_card_token(_auth(OWNER_UID), "pi_11001")
        self.assertFalse(r["ok"], r)
        self.assertEqual(r["error"], "owner_test_mode_payment_blocked")

    def test_11b_link_child_blocked_in_test_mode(self):
        _cl_link(self.storage, "11002", "Kid", PARENT_A_UID)
        self.ctx.owner_test_client_select(_auth(OWNER_UID), {"parent_telegram_id": str(PARENT_A_UID)})
        r = self.ctx.client_link_child(_auth(OWNER_UID), {"code": "CL-DOESNOTMATTER"})
        self.assertFalse(r["ok"], r)
        self.assertEqual(r["error"], "owner_test_mode_link_blocked")

    def test_11c_food_order_blocked_in_test_mode(self):
        if not getattr(self.ctx.settings, "food_module_enabled", False):
            self.skipTest("food module disabled")
        _food_link(self.storage, "11003", "Food Kid", PARENT_A_UID)
        self.ctx.owner_test_client_select(_auth(OWNER_UID), {"parent_telegram_id": str(PARENT_A_UID)})
        r = self.ctx.food_submit_order(_auth(OWNER_UID), {"menu_id": 1, "mk_student_id": "11003", "items": []})
        self.assertFalse(r["ok"], r)
        self.assertEqual(r["error"], "owner_test_mode_food_order_blocked")


class Test12AvailabilityScopedToSelectedChild(OwnerTestClientContextBase):
    def test_12_availability_get_works_for_selected_childs_own_link(self):
        _cl_link(self.storage, "12001", "Kid A", PARENT_A_UID)
        _cl_link(self.storage, "12002", "Kid B", PARENT_B_UID)
        self.ctx.owner_test_client_select(_auth(OWNER_UID), {"parent_telegram_id": str(PARENT_A_UID)})
        ok_result = self.ctx.client_schedule_availability_get(_auth(OWNER_UID), "12001")
        self.assertTrue(ok_result.get("ok", True), ok_result)  # blank-form shape is also ok:true implicitly (no ok key on some paths)

    def test_12b_availability_get_denied_for_other_parents_child(self):
        _cl_link(self.storage, "12003", "Kid A", PARENT_A_UID)
        _cl_link(self.storage, "12004", "Kid B", PARENT_B_UID)
        self.ctx.owner_test_client_select(_auth(OWNER_UID), {"parent_telegram_id": str(PARENT_A_UID)})
        denied = self.ctx.client_schedule_availability_get(_auth(OWNER_UID), "12004")
        self.assertFalse(denied["ok"], denied)

    def test_12c_availability_save_requires_explicit_confirm_in_test_mode(self):
        _cl_link(self.storage, "12005", "Kid A", PARENT_A_UID)
        self.ctx.owner_test_client_select(_auth(OWNER_UID), {"parent_telegram_id": str(PARENT_A_UID)})
        no_confirm = self.ctx.client_schedule_availability_submit(_auth(OWNER_UID), "12005", {
            "preferred_branch": "YC1",
            "intervals": [{"weekday": 1, "start_time": "16:00", "end_time": "17:00", "preference": "preferred"}],
        })
        self.assertFalse(no_confirm["ok"], no_confirm)
        self.assertEqual(no_confirm["error"], "owner_confirm_required")

        with_confirm = self.ctx.client_schedule_availability_submit(_auth(OWNER_UID), "12005", {
            "preferred_branch": "YC1",
            "intervals": [{"weekday": 1, "start_time": "16:00", "end_time": "17:00", "preference": "preferred"}],
            "ownerTestConfirm": True,
        })
        self.assertTrue(with_confirm["ok"], with_confirm)


class Test13RealClientFlowUnchanged(OwnerTestClientContextBase):
    def test_13_real_parent_own_data_unaffected_by_owner_test_machinery(self):
        _cl_link(self.storage, "13001", "Kid", PARENT_A_UID)
        with self.storage._connect() as conn:
            conn.execute(
                """INSERT INTO payment_intents
                   (public_id, mk_user_id, student_name, amount_minor, amount_byn, purpose,
                    status, client_visibility, created_at, updated_at)
                   VALUES ('pi_13001', '13001', 'Kid', 13500, 135.0, 'other', 'ready', 'published', ?, ?)""",
                (now_iso(), now_iso()),
            )
        payments = self.ctx.client_payments_list(_auth(PARENT_A_UID))
        self.assertTrue(payments["ok"], payments)
        self.assertEqual(len(payments["payments"]), 1)
        # A real parent is never in owner-test-client mode.
        self.assertFalse(self.ctx._owner_test_client_mode_active(_auth(PARENT_A_UID)))


class Test14ClientKindUnchanged(OwnerTestClientContextBase):
    def test_14_food_only_client_kind_resolved_normally_via_preview(self):
        _food_link(self.storage, "14001", "Food Kid", PARENT_A_UID)
        self.ctx.owner_test_client_select(_auth(OWNER_UID), {"parent_telegram_id": str(PARENT_A_UID)})
        me = self.ctx.me(_auth(OWNER_UID))
        self.assertEqual(me["clientKind"], "food_only")

    def test_14b_combined_client_kind_resolved_normally_via_preview(self):
        _cl_link(self.storage, "14002", "Kid", PARENT_A_UID)
        _food_link(self.storage, "14003", "Food Kid", PARENT_A_UID)
        self.ctx.owner_test_client_select(_auth(OWNER_UID), {"parent_telegram_id": str(PARENT_A_UID)})
        me = self.ctx.me(_auth(OWNER_UID))
        self.assertEqual(me["clientKind"], "combined")


class Test15PilotGatesUnaffected(OwnerTestClientContextBase):
    def test_15_gate_booleans_stay_keyed_on_real_owner_id(self):
        """settings pilot list contains OWNER_UID only — a previewed
        client (PARENT_A_UID, not in any pilot list) must not change the
        gate outcome; it must stay True because the OWNER is allowlisted,
        regardless of who they're previewing."""
        _cl_link(self.storage, "15001", "Kid", PARENT_A_UID)
        self.ctx.owner_test_client_select(_auth(OWNER_UID), {"parent_telegram_id": str(PARENT_A_UID)})
        me = self.ctx.me(_auth(OWNER_UID))
        self.assertTrue(me["clientCabinetV7113Enabled"])
        self.assertTrue(me["clientNotificationsEnabled"])


class Test16StaffNavUnaffected(unittest.TestCase):
    def test_16_staff_tabs_still_present(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        for tab in ["lessons", "reports", "schedule", "tasks", "admin", "payments-workspace"]:
            self.assertIn(f'data-tab="{tab}"', html)


class Test17BannerOwnerAdminOnly(OwnerTestClientContextBase):
    def test_17_real_parent_never_gets_owner_test_client_mode_true(self):
        _cl_link(self.storage, "17001", "Kid", PARENT_A_UID)
        me = self.ctx.me(_auth(PARENT_A_UID))
        self.assertFalse(me.get("ownerTestClientMode"))

    def test_17b_banner_html_gated_by_server_field_not_a_local_flag(self):
        js = APP_JS.read_text(encoding="utf-8")
        idx = js.find("function renderOwnerTestClientBanner")
        self.assertNotEqual(idx, -1)
        body = js[idx:idx + 400]
        self.assertIn("state.me?.ownerTestClientMode", body)


class TestOwnerSenderStillOwnerOnly(OwnerTestClientContextBase):
    """§6 — the existing owner-only notification smoke sender must not
    intrude into the client-context preview (it would break the "what
    would a real client see" fidelity of the test mode)."""

    def test_sender_hidden_during_client_preview(self):
        js = APP_JS.read_text(encoding="utf-8")
        idx = js.find("function renderOwnerTestNotificationPanel")
        self.assertNotEqual(idx, -1)
        body = js[idx:idx + 900]
        self.assertIn("ownerTestClientMode", body)


class TestBackendSourceGuards(unittest.TestCase):
    """Static guards: the gate helpers must never read params/body for
    identity, matching the same contract as the v7.1.13 round-2 pilot
    gates (test_client_rollout_gates_v7113_round2.py)."""

    def setUp(self):
        self.src = WEB_APP_SERVER_PY.read_text(encoding="utf-8")

    def test_effective_identity_helper_exists_and_is_documented(self):
        self.assertIn("def _effective_client_identity", self.src)
        self.assertIn("def _owner_test_client_mode_active", self.src)

    def test_select_endpoint_revalidates_against_real_links(self):
        idx = self.src.find("def owner_test_client_select")
        body = self.src[idx:idx + 900]
        self.assertIn("find_trusted_client_context_candidates", body)


if __name__ == "__main__":
    unittest.main()
