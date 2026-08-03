"""Tests for v7.1.12.3 — permanent "Возможности для расписания" entry point
for ALL already-linked clients, regardless of how they were linked:
  - CL-code (the original client_parent_child_links self-service flow);
  - onboarding-campaign personal invite link;
  - staff/manual link (client_admin_link_and_enroll, which itself reuses
    storage.link_client_child — the same write path as CL-code).

Prior to this release, client_schedule_availability was only reachable via
a campaign-recipient_id (client_onboarding_recipients.id), which only ever
exists for students imported into a campaign — a CL-code-only or
staff-linked client had no such row and no way to reach the form. This adds
new mk_user_id-keyed endpoints (client_schedule_availability_get/submit)
that lazily bridge to the EXACT SAME, unmodified storage layer (submit_
schedule_availability/get_schedule_availability), creating a
client_onboarding_recipients row on first submit inside one reusable
"standalone" system campaign — never a second parallel availability model,
never a new table, never a separate staff view.

Run:
    python -m unittest tests.test_client_schedule_availability_entry_v71123 -v
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

SECRET = "test-bot-token-secret"


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(bot_username="yellowclubagent_bot", telegram_bot_token=SECRET)
    ctx._role_store: dict[int, str] = {}

    def _role_for_user(uid: int) -> str:
        return ctx._role_store.get(int(uid), "other")

    ctx._role_for_user = _role_for_user
    ctx.moyklass = types.SimpleNamespace(request=lambda *a, **k: types.SimpleNamespace(ok=False, data=None))
    return ctx


def _auth(uid: int, role: str, ctx: MiniAppContext) -> dict:
    ctx._role_store[uid] = role
    return {"user_id": uid}


class EntryTestBase(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        self.owner = _auth(1, "owner", self.ctx)

    def _cl_link(self, mk_user_id, child_name, parent_tid, staff_uid="1"):
        """The original CL-code self-service flow — no campaign involved."""
        code = self.storage.create_client_link_code(mk_user_id, child_name, staff_uid)
        self.assertTrue(code["ok"], code)
        r = self.storage.link_client_child(str(parent_tid), code["code"], now_iso())
        self.assertTrue(r["ok"], r)
        return r

    def _staff_link(self, mk_user_id, child_name, parent_tid):
        """Staff/manual link — client_admin_link_and_enroll, which reuses
        link_client_child internally (confirmed by audit), never a second
        parallel link-writing mechanism."""
        code = self.storage.create_client_link_code(mk_user_id, child_name, "1")
        self.assertTrue(code["ok"], code)
        staff = _auth(900, "owner", self.ctx)
        r = self.ctx.client_admin_link_and_enroll(staff, {"parent_telegram_user_id": str(parent_tid), "code": code["code"]})
        self.assertTrue(r["ok"], r)
        return r

    def _invite_link(self, mk_user_id, child_name, parent_tid):
        """Onboarding-campaign personal invite link flow."""
        camp = self.ctx.onboarding_campaign_create(self.owner, {"name": "Entry test", "academic_year": "2026/2027"})
        self.assertTrue(camp["ok"], camp)
        campaign_id = camp["campaign"]["id"]
        self.ctx.onboarding_campaign_start(self.owner, str(campaign_id))
        cache = self.ctx._onboarding_candidates_cache_dict()
        import time as _time
        cache[str(mk_user_id)] = (_time.time(), {"mk_user_id": str(mk_user_id), "child_display_name": child_name, "branch_name": "", "course_name": ""})
        imp = self.ctx.onboarding_campaign_import_recipients(self.owner, str(campaign_id), {"recipients": [{"mk_user_id": str(mk_user_id)}]})
        self.assertTrue(imp["ok"], imp)
        recipient = self.storage.list_onboarding_campaign_recipients(campaign_id)[0]
        inv = self.ctx.onboarding_campaign_create_invite(self.owner, str(campaign_id), {"recipient_id": recipient["id"]})
        self.assertTrue(inv["ok"], inv)
        invite_id_str, _sep, sig = inv["invite_link"].split("start=c_")[1].partition("_")
        act = self.storage.activate_onboarding_invite(int(invite_id_str), sig, str(parent_tid), SECRET)
        self.assertTrue(act["ok"], act)
        return campaign_id, recipient


# ─────────────────────────────────────────────────────────────────────────────
# 1-4 — access for all three link mechanisms, denial without a link
# ─────────────────────────────────────────────────────────────────────────────

class TestAccessByLinkMechanism(EntryTestBase):
    def test_1_cl_code_client_sees_card_and_has_access(self):
        self._cl_link("11001", "CL Kid", "5001")
        parent = _auth(5001, "parent", self.ctx)
        children = self.ctx.client_children_list(parent)
        self.assertTrue(children["ok"], children)
        self.assertEqual(children["client_count"], 1)
        r = self.ctx.client_schedule_availability_get(parent, "11001")
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["filled"])

    def test_2_onboarding_invite_client_sees_card_and_has_access(self):
        self._invite_link("11002", "Invite Kid", "5002")
        parent = _auth(5002, "parent", self.ctx)
        children = self.ctx.client_children_list(parent)
        self.assertEqual(children["client_count"], 1)
        r = self.ctx.client_schedule_availability_get(parent, "11002")
        self.assertTrue(r["ok"], r)

    def test_3_staff_manual_link_client_sees_card_and_has_access(self):
        self._staff_link("11003", "Staff Kid", "5003")
        parent = _auth(5003, "parent", self.ctx)
        children = self.ctx.client_children_list(parent)
        self.assertEqual(children["client_count"], 1)
        r = self.ctx.client_schedule_availability_get(parent, "11003")
        self.assertTrue(r["ok"], r)

    def test_4_no_client_link_denied(self):
        parent = _auth(5099, "parent", self.ctx)
        r = self.ctx.client_schedule_availability_get(parent, "99999")
        self.assertFalse(r["ok"], r)
        s = self.ctx.client_schedule_availability_submit(parent, "99999", {"intervals": []})
        self.assertFalse(s["ok"], s)


# ─────────────────────────────────────────────────────────────────────────────
# 5/6 — single vs multiple children (frontend routing — static source check)
# ─────────────────────────────────────────────────────────────────────────────

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
_js_cache = None
_html_cache = None


def _js():
    global _js_cache
    if _js_cache is None:
        _js_cache = APP_JS.read_text(encoding="utf-8")
    return _js_cache


def _html():
    global _html_cache
    if _html_cache is None:
        _html_cache = INDEX_HTML.read_text(encoding="utf-8")
    return _html_cache


def _js_fn(name, is_async=False, window=6000):
    js = _js()
    needle = f"{'async ' if is_async else ''}function {name}("
    start = js.find(needle)
    assert start != -1, f"{needle} not found in app.js"
    end = js.find("\nasync function ", start + 1)
    end2 = js.find("\nfunction ", start + 1)
    candidates = [e for e in (end, end2) if e != -1]
    end = min(candidates) if candidates else start + window
    return js[start:end]


class TestSingleVsMultiChildRouting(unittest.TestCase):
    """v7.1.13 — the availability entry card moved from "Мои дети" onto the
    new Главная dashboard (renderClientHome). Single-vs-multi-child routing
    still holds, just via the dashboard's own always-visible child switcher
    (_cabChildSwitcherHtml) instead of a click-triggered in-place picker —
    see the client-cabinet-v7113 approved design and the final report for
    v7.1.13. _wsScheduleAvailCardHtml/_wsScheduleAvailWire (the old
    picker) were retired, not just renamed."""

    def test_5_single_child_opens_form_directly(self):
        fn = _js_fn("_cabAvailabilityCardHtml")
        # data-mk is always set directly on the card — clicking it opens the
        # form for the active child with no intermediate picker, whether
        # there's 1 child or several (the switcher already picked one).
        self.assertIn("data-mk=\"${escapeAttr(activeChild.mk_user_id)}\"", fn)

    def test_6_multiple_children_show_picker(self):
        # The "picker" is now the always-visible Home child switcher, not a
        # secondary in-place list triggered from inside the availability card.
        switcher_fn = _js_fn("_cabChildSwitcherHtml")
        self.assertIn("children.length <= 1", switcher_fn)
        self.assertIn("cab-switch-chip", switcher_fn)
        home_fn = _js_fn("renderClientHome")
        self.assertIn("_cabChildSwitcherHtml(children", home_fn)
        self.assertIn("_cabAvailabilityCardHtml(activeChild)", home_fn)


# ─────────────────────────────────────────────────────────────────────────────
# 7/8/9 — load existing values, update without duplicate, per-child isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadUpdateIsolation(EntryTestBase):
    def test_7_existing_values_load_on_reopen(self):
        self._cl_link("12001", "Kid A", "6001")
        parent = _auth(6001, "parent", self.ctx)
        self.ctx.client_schedule_availability_submit(parent, "12001", {
            "preferred_branch": "YC1", "available_from": "2026-09-01",
            "intervals": [{"weekday": 2, "start_time": "15:00", "end_time": "16:00", "preference": "preferred"}],
        })
        r = self.ctx.client_schedule_availability_get(parent, "12001")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["filled"])
        self.assertEqual(r["preferred_branch"], "YC1")
        self.assertEqual(len(r["intervals"]), 1)
        self.assertEqual(r["intervals"][0]["start_time"], "15:00")

    def test_8_repeat_save_updates_without_duplicate(self):
        self._cl_link("12002", "Kid B", "6002")
        parent = _auth(6002, "parent", self.ctx)
        self.ctx.client_schedule_availability_submit(parent, "12002", {
            "preferred_branch": "YC1",
            "intervals": [{"weekday": 1, "start_time": "10:00", "end_time": "11:00", "preference": "possible"}],
        })
        self.ctx.client_schedule_availability_submit(parent, "12002", {
            "preferred_branch": "YC2",
            "intervals": [{"weekday": 3, "start_time": "14:00", "end_time": "15:00", "preference": "preferred"}],
        })
        r = self.ctx.client_schedule_availability_get(parent, "12002")
        self.assertEqual(r["preferred_branch"], "YC2")
        self.assertEqual(len(r["intervals"]), 1)
        self.assertEqual(r["intervals"][0]["weekday"], 3)
        # No duplicate recipient row was created for this mk_user_id either.
        with self.storage._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) c FROM client_onboarding_recipients WHERE mk_user_id=?", ("12002",)
            ).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_9_different_children_not_mixed(self):
        self._cl_link("12003", "Kid C1", "6003")
        self._cl_link("12004", "Kid C2", "6003")  # same parent, two children
        parent = _auth(6003, "parent", self.ctx)
        self.ctx.client_schedule_availability_submit(parent, "12003", {
            "preferred_branch": "YC1",
            "intervals": [{"weekday": 1, "start_time": "09:00", "end_time": "10:00", "preference": "preferred"}],
        })
        self.ctx.client_schedule_availability_submit(parent, "12004", {
            "preferred_branch": "YC2",
            "intervals": [{"weekday": 5, "start_time": "17:00", "end_time": "18:00", "preference": "possible"}],
        })
        r1 = self.ctx.client_schedule_availability_get(parent, "12003")
        r2 = self.ctx.client_schedule_availability_get(parent, "12004")
        self.assertEqual(r1["preferred_branch"], "YC1")
        self.assertEqual(r1["intervals"][0]["weekday"], 1)
        self.assertEqual(r2["preferred_branch"], "YC2")
        self.assertEqual(r2["intervals"][0]["weekday"], 5)


# ─────────────────────────────────────────────────────────────────────────────
# 10/11 — cross-child protection
# ─────────────────────────────────────────────────────────────────────────────

class TestCrossChildProtection(EntryTestBase):
    def setUp(self):
        super().setUp()
        self._cl_link("13001", "Victim Kid", "7001")
        self.victim = _auth(7001, "parent", self.ctx)
        self.ctx.client_schedule_availability_submit(self.victim, "13001", {
            "preferred_branch": "YC1",
            "intervals": [{"weekday": 2, "start_time": "12:00", "end_time": "13:00", "preference": "preferred"}],
        })
        self._cl_link("13002", "Attacker's Own Kid", "7002")
        self.attacker = _auth(7002, "parent", self.ctx)

    def test_10_cannot_read_another_childs_data(self):
        r = self.ctx.client_schedule_availability_get(self.attacker, "13001")
        self.assertFalse(r["ok"], r)

    def test_11_cannot_modify_another_childs_data(self):
        s = self.ctx.client_schedule_availability_submit(self.attacker, "13001", {
            "preferred_branch": "YC2",
            "intervals": [{"weekday": 6, "start_time": "08:00", "end_time": "09:00", "preference": "possible"}],
        })
        self.assertFalse(s["ok"], s)
        # Confirm the victim's real record is untouched.
        real = self.ctx.client_schedule_availability_get(self.victim, "13001")
        self.assertEqual(real["preferred_branch"], "YC1")
        self.assertEqual(real["intervals"][0]["weekday"], 2)


# ─────────────────────────────────────────────────────────────────────────────
# 12/13/14 — nothing about campaign/invite/continuation state blocks the form
# ─────────────────────────────────────────────────────────────────────────────

class TestNothingBlocksTheForm(EntryTestBase):
    def test_12_continuation_status_unknown_never_blocks_or_changes(self):
        self._cl_link("14001", "Kid", "8001")
        parent = _auth(8001, "parent", self.ctx)
        r = self.ctx.client_schedule_availability_submit(parent, "14001", {"intervals": []})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["recipient"]["continuation_status"], "unknown")

    def test_13_standalone_campaign_status_does_not_block_resubmission(self):
        self._cl_link("14002", "Kid", "8002")
        parent = _auth(8002, "parent", self.ctx)
        first = self.ctx.client_schedule_availability_submit(parent, "14002", {"intervals": []})
        self.assertTrue(first["ok"], first)
        campaign_id = first["recipient"]["campaign_id"]
        # Close (then archive) the standalone campaign entirely — resubmitting
        # for an EXISTING recipient must still work; submit_schedule_
        # availability itself never checks campaign status.
        self.storage.close_onboarding_campaign(campaign_id, "1")
        self.storage.archive_onboarding_campaign(campaign_id, "1")
        again = self.ctx.client_schedule_availability_submit(parent, "14002", {
            "intervals": [{"weekday": 4, "start_time": "16:00", "end_time": "17:00", "preference": "possible"}],
        })
        self.assertTrue(again["ok"], again)

    def test_14_no_invite_ever_needed_for_cl_code_client(self):
        self._cl_link("14003", "Kid", "8003")
        parent = _auth(8003, "parent", self.ctx)
        with self.storage._connect() as conn:
            invite_count = conn.execute(
                "SELECT COUNT(*) c FROM client_onboarding_invites WHERE mk_user_id=?", ("14003",)
            ).fetchone()["c"]
        self.assertEqual(invite_count, 0)
        r = self.ctx.client_schedule_availability_submit(parent, "14003", {"intervals": []})
        self.assertTrue(r["ok"], r)


# ─────────────────────────────────────────────────────────────────────────────
# 15 — existing onboarding-invite flow (recipient_id-keyed) unbroken
# ─────────────────────────────────────────────────────────────────────────────

class TestOnboardingFlowUnbroken(EntryTestBase):
    def test_15_recipient_id_keyed_endpoints_still_work(self):
        campaign_id, recipient = self._invite_link("15001", "Kid", "9001")
        parent = _auth(9001, "parent", self.ctx)
        r = self.ctx.onboarding_recipient_availability_submit(parent, str(recipient["id"]), {
            "preferred_branch": "YC1",
            "intervals": [{"weekday": 1, "start_time": "15:00", "end_time": "16:00", "preference": "preferred"}],
        })
        self.assertTrue(r["ok"], r)
        got = self.ctx.onboarding_recipient_availability_get(parent, str(recipient["id"]))
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["preferred_branch"], "YC1")
        # Another parent still denied via the OLD endpoint too.
        other = _auth(9999, "parent", self.ctx)
        denied = self.ctx.onboarding_recipient_availability_submit(other, str(recipient["id"]), {"intervals": []})
        self.assertFalse(denied["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# 16 — staff summary/export sees CL-code (non-campaign) client data too
# ─────────────────────────────────────────────────────────────────────────────

class TestStaffVisibility(EntryTestBase):
    def test_16_staff_sees_cl_code_clients_data_via_existing_campaign_ui(self):
        self._cl_link("16001", "CL Kid For Staff", "10001")
        parent = _auth(10001, "parent", self.ctx)
        r = self.ctx.client_schedule_availability_submit(parent, "16001", {
            "preferred_branch": "YC1",
            "intervals": [{"weekday": 2, "start_time": "13:00", "end_time": "14:00", "preference": "preferred"}],
        })
        self.assertTrue(r["ok"], r)
        campaign_id = r["recipient"]["campaign_id"]
        # No new staff-side table/endpoint — the existing campaign detail
        # (filters/summary) and recipient listing already show it.
        staff = _auth(11000, "admin", self.ctx)
        detail = self.ctx.onboarding_campaign_get(staff, str(campaign_id), {})
        self.assertTrue(detail["ok"], detail)
        mk_ids = [rec["mk_user_id"] for rec in detail["recipients"]]
        self.assertIn("16001", mk_ids)
        summary = self.storage.get_onboarding_campaign_availability_summary(campaign_id)
        self.assertGreaterEqual(summary["by_preferred_branch"].get("YC1", 0), 1)
        self.assertTrue(any(slot["weekday"] == 2 for slot in summary["grid"]))


# ─────────────────────────────────────────────────────────────────────────────
# 17 — API error never clears entered values (frontend — static source check)
# ─────────────────────────────────────────────────────────────────────────────

class TestErrorDoesNotClearValues(unittest.TestCase):
    def test_17_save_error_path_never_resets_intervals_or_closes_modal(self):
        fn = _js_fn("_ocAvailSave", is_async=True)
        # The only place the modal is closed or intervals reset is inside the
        # `if (data.ok)` success branch — never in the catch/else error paths.
        error_branch = fn.split("} else {", 1)[1].split("} catch", 1)[0] if "} else {" in fn else ""
        self.assertIn("_ocAvailError(data.error", error_branch)
        self.assertNotIn("piModalClose", error_branch)
        self.assertNotIn("intervals = []", error_branch)
        catch_branch = fn.split("} catch (e) {", 1)[1] if "} catch (e) {" in fn else ""
        self.assertIn("_ocAvailError(safeUserError(e))", catch_branch)
        self.assertNotIn("piModalClose", catch_branch)
        self.assertNotIn("intervals = []", catch_branch)


# ─────────────────────────────────────────────────────────────────────────────
# 18 — version / cache-bust
# ─────────────────────────────────────────────────────────────────────────────

class TestVersion(unittest.TestCase):
    def test_18_version_and_cache_bust(self):
        self.assertIn('console.log("MiniApp version: v7.1.14.1");', _js())
        html = _html()
        self.assertIn("styles.css?v=7.1.14", html)
        self.assertIn("app.js?v=7.1.14", html)


if __name__ == "__main__":
    unittest.main()
