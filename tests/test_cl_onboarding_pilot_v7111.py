"""Tests for v7.1.11 — payment automation pilot onboarding.

v7.1.11 correction: /api/client/children/link (client_link_child) is the
generic self-service parent flow (role=parent only, used for ordinary
CL-code linking) and must NEVER trigger pilot automation. A new staff-only
endpoint, /api/client/admin/link-and-enroll (client_admin_link_and_enroll,
owner/admin/client_manager), reuses the same storage.link_client_child
logic and is the ONLY place pilot enrollment happens.

Covers:
  storage.ensure_payment_pilot_from_client_link (pure storage layer):
    1.  missing pilot -> created, mode=review, enabled=True
    2.  missing pilot -> audit event payment_pilot_created_from_client_code recorded
    3.  existing review preserved (mode/note/enabled untouched, no new audit)
    4.  existing auto preserved
    5.  existing observe preserved
    6.  existing disabled preserved
    7.  idempotent: calling twice on a missing pilot only creates once

  Generic parent flow (client_link_child) — must NOT trigger automation:
    8.  parent links a child by valid CL code -> link created, no
        payment_automation key in the response, no pilot row created
    9.  permissions unchanged: only role=parent may call client_link_child

  Staff Payments onboarding (client_admin_link_and_enroll):
    10. owner/admin/client_manager can call it; teacher/operations/parent denied
    11. missing pilot -> link created + pilot created, mode=review, enabled=True
    12. existing review/auto/observe/disabled preserved, not recreated,
        no duplicate audit event
    13. invalid CL code -> no link, no pilot
    14. duplicate (already_linked) redemption is idempotent -> no duplicate pilot
    15. no Payment Intent / bePaid / publish / Telegram / MK posting side effects
    16. no mass backfill: other students' pilot rows untouched
    17. missing parent_telegram_user_id -> rejected before touching storage

  Fail-safe pilot creation (section 2 of the v7.1.11 fix):
    18. link succeeds but pilot insert raises -> link is NOT rolled back,
        automation_status="failed", pilot_created=False, no false success
    19. failure does not create a Payment Intent/bePaid/MK posting side effect
    20. failure log/response never contains the plaintext CL code
    21. retrying the same (now already_linked) code after a transient failure
        successfully creates the pilot and becomes idempotent from then on

Run:
    python -m unittest tests.test_cl_onboarding_pilot_v7111 -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from web_app_server import MiniAppContext, PAYMENT_ONBOARDING_STAFF_ROLES


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace()
    ctx._role_store: dict[int, str] = {}

    def _role_for_user(uid: int) -> str:
        return ctx._role_store.get(int(uid), "other")

    ctx._role_for_user = _role_for_user
    return ctx


def _auth(uid: int, role: str, ctx: MiniAppContext) -> dict:
    ctx._role_store[uid] = role
    return {"user_id": uid}


def _audit_events(storage: Storage, mk_user_id: str) -> list:
    with storage._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM payment_pilot_audit_log WHERE mk_user_id=? AND event_type=?",
            (str(mk_user_id), "payment_pilot_created_from_client_code"),
        ).fetchall()
    return [dict(r) for r in rows]


def _pilot_count(storage: Storage, mk_user_id: str | None = None) -> int:
    with storage._connect() as conn:
        if mk_user_id is None:
            row = conn.execute("SELECT COUNT(*) c FROM payment_automation_pilot_clients").fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) c FROM payment_automation_pilot_clients WHERE mk_user_id=?", (str(mk_user_id),)
            ).fetchone()
    return row["c"]


# ─────────────────────────────────────────────────────────────────────────────
# 1-7: storage.ensure_payment_pilot_from_client_link (unaffected by routing fix)
# ─────────────────────────────────────────────────────────────────────────────

class TestEnsurePilotStorage(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()

    def test_01_missing_pilot_created_review_enabled(self):
        result = self.storage.ensure_payment_pilot_from_client_link("9001", "42")
        self.assertTrue(result["created"])
        self.assertEqual(result["mode"], "review")
        self.assertTrue(result["enabled"])
        row = self.storage.get_pilot_client("9001")
        self.assertEqual(row["mode"], "review")
        self.assertEqual(row["note"], "source=payments_client_code")

    def test_02_audit_event_recorded_on_creation(self):
        self.storage.ensure_payment_pilot_from_client_link("9002", "42")
        events = _audit_events(self.storage, "9002")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["new_mode"], "review")
        self.assertEqual(events[0]["note"], "source=payments_client_code")
        self.assertEqual(events[0]["actor_tg_id"], "42")

    def test_03_existing_review_preserved(self):
        self.storage.upsert_pilot_client("9003", mode="review", note="manual note", added_by_name="Owner")
        result = self.storage.ensure_payment_pilot_from_client_link("9003", "42")
        self.assertFalse(result["created"])
        self.assertEqual(result["mode"], "review")
        row = self.storage.get_pilot_client("9003")
        self.assertEqual(row["note"], "manual note")  # untouched
        self.assertEqual(len(_audit_events(self.storage, "9003")), 0)

    def test_04_existing_auto_preserved(self):
        self.storage.upsert_pilot_client("9004", mode="auto")
        result = self.storage.ensure_payment_pilot_from_client_link("9004", "42")
        self.assertFalse(result["created"])
        self.assertEqual(result["mode"], "auto")
        self.assertEqual(self.storage.get_pilot_client("9004")["mode"], "auto")

    def test_05_existing_observe_preserved(self):
        self.storage.upsert_pilot_client("9005", mode="observe")
        result = self.storage.ensure_payment_pilot_from_client_link("9005", "42")
        self.assertFalse(result["created"])
        self.assertEqual(result["mode"], "observe")

    def test_06_existing_disabled_preserved(self):
        self.storage.upsert_pilot_client("9006", mode="disabled")
        result = self.storage.ensure_payment_pilot_from_client_link("9006", "42")
        self.assertFalse(result["created"])
        self.assertEqual(result["mode"], "disabled")

    def test_07_idempotent_second_call_does_not_duplicate(self):
        first = self.storage.ensure_payment_pilot_from_client_link("9007", "42")
        second = self.storage.ensure_payment_pilot_from_client_link("9007", "42")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(second["mode"], "review")
        self.assertEqual(len(_audit_events(self.storage, "9007")), 1)
        self.assertEqual(_pilot_count(self.storage, "9007"), 1)


# ─────────────────────────────────────────────────────────────────────────────
# 8-9: generic parent flow must NOT trigger automation
# ─────────────────────────────────────────────────────────────────────────────

class TestGenericParentLinkNoAutomation(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        self.owner_auth = _auth(1, "owner", self.ctx)

    def _new_code(self, mk_user_id: str, name: str = "Child") -> str:
        gen = self.ctx.admin_client_generate_code(self.owner_auth, {"mk_user_id": mk_user_id, "child_display_name": name})
        self.assertTrue(gen.get("ok"), gen)
        return gen["code"]

    def test_08_parent_link_creates_link_but_no_pilot(self):
        code = self._new_code("9101")
        parent = _auth(101, "parent", self.ctx)
        result = self.ctx.client_link_child(parent, {"code": code})
        self.assertTrue(result.get("ok"), result)
        self.assertNotIn("payment_automation", result)
        self.assertEqual(_pilot_count(self.storage, "9101"), 0)
        children = self.storage.list_client_children_for_parent("101")
        self.assertEqual(len(children), 1)

    def test_09_permissions_unchanged_only_parent_may_redeem(self):
        code = self._new_code("9107")
        for role in ("owner", "admin", "operations", "client_manager", "teacher"):
            auth = _auth(hash(role) % 100000 + 200, role, self.ctx)
            result = self.ctx.client_link_child(auth, {"code": code})
            self.assertFalse(result.get("ok"), f"role={role} should be denied client_link_child")
            self.assertEqual(result.get("error"), "Доступ только для родителей.")


# ─────────────────────────────────────────────────────────────────────────────
# 10-17: staff Payments onboarding (client_admin_link_and_enroll)
# ─────────────────────────────────────────────────────────────────────────────

class TestStaffOnboarding(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        self.owner_auth = _auth(1, "owner", self.ctx)

    def _new_code(self, mk_user_id: str, name: str = "Child") -> str:
        gen = self.ctx.admin_client_generate_code(self.owner_auth, {"mk_user_id": mk_user_id, "child_display_name": name})
        self.assertTrue(gen.get("ok"), gen)
        return gen["code"]

    def _onboard(self, actor_uid: int, actor_role: str, code: str, parent_tid: str = "555") -> dict:
        auth = _auth(actor_uid, actor_role, self.ctx)
        return self.ctx.client_admin_link_and_enroll(auth, {"code": code, "parent_telegram_user_id": parent_tid})

    def test_10_role_gate_matches_spec(self):
        self.assertEqual(set(PAYMENT_ONBOARDING_STAFF_ROLES), {"owner", "admin", "client_manager"})
        for i, role in enumerate(("owner", "admin", "client_manager")):
            code = self._new_code(f"930{i}")
            result = self._onboard(300 + i, role, code, parent_tid=f"{600 + i}")
            self.assertTrue(result.get("ok"), f"role={role}: {result}")
        for i, role in enumerate(("teacher", "operations", "parent")):
            code2 = self._new_code(f"931{i}")
            result = self._onboard(310 + i, role, code2, parent_tid="700")
            self.assertFalse(result.get("ok"), f"role={role} should be denied")

    def test_11_missing_pilot_created_review_enabled(self):
        code = self._new_code("9401")
        result = self._onboard(401, "client_manager", code)
        self.assertTrue(result.get("ok"), result)
        auto = result["payment_automation"]
        self.assertEqual(auto["automation_status"], "connected")
        self.assertTrue(auto["pilot_created"])
        self.assertEqual(auto["mode"], "review")
        self.assertTrue(auto["enabled"])
        row = self.storage.get_pilot_client("9401")
        self.assertEqual(row["mode"], "review")
        self.assertTrue(bool(row["enabled"]))

    def test_12_existing_modes_preserved_no_duplicate_audit(self):
        for mode, mk_user_id in (("review", "9501"), ("auto", "9502"), ("observe", "9503"), ("disabled", "9504")):
            self.storage.upsert_pilot_client(mk_user_id, mode=mode)
            code = self._new_code(mk_user_id)
            result = self._onboard(int(mk_user_id), "client_manager", code, parent_tid=mk_user_id)
            self.assertTrue(result.get("ok"), result)
            auto = result["payment_automation"]
            self.assertFalse(auto["pilot_created"], f"mode={mode} should not be recreated")
            self.assertEqual(auto["mode"], mode)
            self.assertEqual(self.storage.get_pilot_client(mk_user_id)["mode"], mode)
            self.assertEqual(len(_audit_events(self.storage, mk_user_id)), 0)

    def test_13_invalid_code_no_link_no_pilot(self):
        result = self._onboard(601, "client_manager", "CL-BOGUS0001")
        self.assertFalse(result.get("ok"))
        self.assertNotIn("payment_automation", result)
        self.assertEqual(_pilot_count(self.storage), 0)

    def test_14_duplicate_already_linked_idempotent(self):
        code = self._new_code("9701")
        first = self._onboard(701, "client_manager", code, parent_tid="800")
        second = self._onboard(702, "client_manager", code, parent_tid="800")
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertTrue(second.get("already_linked"))
        self.assertFalse(second["payment_automation"]["pilot_created"])
        self.assertEqual(_pilot_count(self.storage, "9701"), 1)

    def test_15_no_financial_side_effects(self):
        code = self._new_code("9801")
        self._onboard(801, "client_manager", code)
        with self.storage._connect() as conn:
            intents = conn.execute("SELECT COUNT(*) c FROM payment_intents").fetchone()
        self.assertEqual(intents["c"], 0)

    def test_16_no_mass_backfill_other_students_untouched(self):
        self.storage.upsert_pilot_client("9200", mode="disabled", note="pre-existing, unrelated")
        code = self._new_code("9901")
        self._onboard(901, "client_manager", code)
        untouched = self.storage.get_pilot_client("9200")
        self.assertEqual(untouched["mode"], "disabled")
        self.assertEqual(untouched["note"], "pre-existing, unrelated")
        self.assertEqual(_pilot_count(self.storage), 2)  # 9200 (pre-existing) + 9901 (this onboarding) only

    def test_17_missing_parent_telegram_user_id_rejected(self):
        code = self._new_code("9001a")
        auth = _auth(1001, "client_manager", self.ctx)
        result = self.ctx.client_admin_link_and_enroll(auth, {"code": code})
        self.assertFalse(result.get("ok"))
        self.assertEqual(_pilot_count(self.storage), 0)


# ─────────────────────────────────────────────────────────────────────────────
# 18-21: fail-safe pilot creation
# ─────────────────────────────────────────────────────────────────────────────

class TestFailSafePilotCreation(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        self.owner_auth = _auth(1, "owner", self.ctx)

    def _new_code(self, mk_user_id: str, name: str = "Child") -> str:
        gen = self.ctx.admin_client_generate_code(self.owner_auth, {"mk_user_id": mk_user_id, "child_display_name": name})
        self.assertTrue(gen.get("ok"), gen)
        return gen["code"]

    def test_18_pilot_failure_no_false_success_link_not_rolled_back(self):
        code = self._new_code("9601")
        auth = _auth(1601, "client_manager", self.ctx)
        with patch.object(self.storage, "ensure_payment_pilot_from_client_link", side_effect=RuntimeError("db unavailable")):
            result = self.ctx.client_admin_link_and_enroll(auth, {"code": code, "parent_telegram_user_id": "900"})
        self.assertTrue(result.get("ok"))  # link itself succeeded
        self.assertEqual(result["payment_automation"]["automation_status"], "failed")
        self.assertFalse(result["payment_automation"]["pilot_created"])
        # link was NOT rolled back
        children = self.storage.list_client_children_for_parent("900")
        self.assertEqual(len(children), 1)
        self.assertEqual(_pilot_count(self.storage, "9601"), 0)

    def test_19_failure_creates_no_financial_side_effects(self):
        code = self._new_code("9602")
        auth = _auth(1602, "client_manager", self.ctx)
        with patch.object(self.storage, "ensure_payment_pilot_from_client_link", side_effect=RuntimeError("boom")):
            self.ctx.client_admin_link_and_enroll(auth, {"code": code, "parent_telegram_user_id": "901"})
        with self.storage._connect() as conn:
            intents = conn.execute("SELECT COUNT(*) c FROM payment_intents").fetchone()
        self.assertEqual(intents["c"], 0)

    def test_20_failure_does_not_leak_code(self):
        code = self._new_code("9603")
        auth = _auth(1603, "client_manager", self.ctx)
        with self.assertLogs("yellow_club_miniapp", level="WARNING") as cm:
            with patch.object(self.storage, "ensure_payment_pilot_from_client_link", side_effect=RuntimeError("boom")):
                result = self.ctx.client_admin_link_and_enroll(auth, {"code": code, "parent_telegram_user_id": "902"})
        combined_log = " ".join(cm.output)
        self.assertNotIn(code, combined_log)
        self.assertNotIn(code, str(result))

    def test_21_retry_after_transient_failure_creates_pilot(self):
        code = self._new_code("9604")
        auth = _auth(1604, "client_manager", self.ctx)
        with patch.object(self.storage, "ensure_payment_pilot_from_client_link", side_effect=RuntimeError("boom")):
            first = self.ctx.client_admin_link_and_enroll(auth, {"code": code, "parent_telegram_user_id": "903"})
        self.assertEqual(first["payment_automation"]["automation_status"], "failed")
        self.assertEqual(_pilot_count(self.storage, "9604"), 0)

        # real retry: same code, now already_linked, pilot ensure runs for real this time
        second = self.ctx.client_admin_link_and_enroll(auth, {"code": code, "parent_telegram_user_id": "903"})
        self.assertTrue(second.get("ok"), second)
        self.assertTrue(second.get("already_linked"))
        self.assertEqual(second["payment_automation"]["automation_status"], "connected")
        self.assertTrue(second["payment_automation"]["pilot_created"])
        self.assertEqual(second["payment_automation"]["mode"], "review")
        self.assertEqual(_pilot_count(self.storage, "9604"), 1)

        # a further retry stays idempotent (no duplicate pilot, no new audit event)
        third = self.ctx.client_admin_link_and_enroll(auth, {"code": code, "parent_telegram_user_id": "903"})
        self.assertTrue(third.get("ok"), third)
        self.assertFalse(third["payment_automation"]["pilot_created"])
        self.assertEqual(_pilot_count(self.storage, "9604"), 1)
        self.assertEqual(len(_audit_events(self.storage, "9604")), 1)


if __name__ == "__main__":
    unittest.main()
