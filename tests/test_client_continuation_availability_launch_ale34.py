"""Tests for ALE-34 — client-facing continuation-status entry point + the
"Мои дети" Availability entry point, closing the two real launch blockers
found by an audit + isolated E2E of the full onboarding/continuation/
Availability flow ahead of the first real pilot client:

  1. A parent previously had NO persistent, revisitable way to answer
     "продолжает/не продолжает обучение" from the cabinet — the only path
     was a one-shot Telegram bot chat survey message right after invite
     activation (handlers.py, storage.submit_continuation_response), never
     shown again and not sent at all for campaigns with survey_enabled=
     False. Adds GET/POST /api/client/continuation-status/{mk_user_id},
     mirroring client_schedule_availability_get/submit exactly (same
     _require_availability_access ownership gate, same get_or_create_
     recipient_for_client lazy-create pattern) and reusing storage.
     update_recipient_continuation_status unchanged — no new table, no new
     storage method, no new audit mechanism.
  2. The fully-built, already-working Availability screen
     (#tab-availability) had NO entry point at all for a client whose
     Telegram id is not on the client_cabinet_v7113_enabled pilot
     allowlist (the production default) — its only entry points
     (Главная dashboard card, "Ещё" menu) live exclusively inside the new
     cabinet shell. Adds an "Возможности для расписания" button directly
     to each client's card on "Мои дети" (renderMyChildren) — the one
     screen reachable by EVERY client regardless of that flag — reusing
     the existing openClientScheduleAvailabilityModal(mkUserId) opener
     unchanged.

Run offline:
    python -m unittest tests.test_client_continuation_availability_launch_ale34 -v
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

from storage import Storage
from web_app_server import MiniAppContext
from utils import now_iso

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")


def _fn_body(name: str) -> str:
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\([^)]*\) \{(.*?)\n\}\n", APP_JS, re.S)
    assert m, f"function {name} not found"
    return m.group(1)


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
        client_cabinet_v7113_enabled=False, client_cabinet_v7113_pilot_telegram_ids=[],
        client_notifications_enabled=True, client_notifications_pilot_telegram_ids=[],
    )
    ctx._role_store: dict[int, str] = {}
    ctx._role_for_user = lambda uid: ctx._role_store.get(int(uid), "other")
    ctx.moyklass = types.SimpleNamespace(request=lambda *a, **k: types.SimpleNamespace(ok=False, data=None))
    return ctx


def _auth(uid: int, role: str, ctx: MiniAppContext) -> dict:
    ctx._role_store[uid] = role
    return {"user_id": uid}


class Base(unittest.TestCase):
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

    def _audit_rows(self, mk_user_id: str, event_type: str):
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM client_onboarding_audit_log WHERE mk_user_id=? AND event_type=? ORDER BY id",
                (str(mk_user_id), event_type),
            ).fetchall()
        return [dict(r) for r in rows]


# ── Backend: happy path + persistence ──────────────────────────────────
class TestContinuationHappyPath(Base):
    def test_get_before_any_answer_is_unknown(self):
        self._cl_link("40001", "Kid", "8001")
        parent = _auth(8001, "parent", self.ctx)
        r = self.ctx.client_continuation_status_get(parent, "40001")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["continuation_status"], "unknown")

    def test_set_continues_then_get_reflects_it(self):
        self._cl_link("40002", "Kid", "8002")
        parent = _auth(8002, "parent", self.ctx)
        r = self.ctx.client_continuation_status_submit(parent, "40002", {"continuation_status": "continues"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["new_status"], "continues")
        g = self.ctx.client_continuation_status_get(parent, "40002")
        self.assertEqual(g["continuation_status"], "continues")

    def test_set_not_continuing(self):
        self._cl_link("40003", "Kid", "8003")
        parent = _auth(8003, "parent", self.ctx)
        r = self.ctx.client_continuation_status_submit(parent, "40003", {"continuation_status": "not_continuing"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["new_status"], "not_continuing")

    def test_reusing_an_existing_real_onboarding_recipient_not_a_second_row(self):
        # a child who came through a real onboarding campaign already has a
        # client_onboarding_recipients row — the standalone-cabinet entry
        # must reuse it (get_or_create_recipient_for_client), never fork a
        # second, disconnected row for the same student.
        camp = self.storage.create_onboarding_campaign("Real campaign", "2026/2027", "T")
        self.storage.import_onboarding_campaign_recipients(camp["campaign"]["id"], [{"mk_user_id": "40004", "child_display_name": "Kid"}], "T")
        self._cl_link("40004", "Kid", "8004")
        parent = _auth(8004, "parent", self.ctx)
        self.ctx.client_continuation_status_submit(parent, "40004", {"continuation_status": "continues"})
        recipients = self.storage.find_onboarding_recipients_by_mk_user("40004")
        self.assertEqual(len(recipients), 1, "must reuse the existing recipient row, not create a duplicate")
        self.assertEqual(recipients[0]["campaign_id"], camp["campaign"]["id"])


class TestIdempotency(Base):
    def test_repeat_submit_same_status_is_safe(self):
        self._cl_link("40010", "Kid", "8010")
        parent = _auth(8010, "parent", self.ctx)
        r1 = self.ctx.client_continuation_status_submit(parent, "40010", {"continuation_status": "continues"})
        r2 = self.ctx.client_continuation_status_submit(parent, "40010", {"continuation_status": "continues"})
        self.assertTrue(r1["ok"] and r2["ok"])
        recipients = self.storage.find_onboarding_recipients_by_mk_user("40010")
        self.assertEqual(len(recipients), 1, "no duplicate recipient row on repeat submit")

    def test_changing_mind_continues_then_not_continuing_then_back(self):
        self._cl_link("40011", "Kid", "8011")
        parent = _auth(8011, "parent", self.ctx)
        self.ctx.client_continuation_status_submit(parent, "40011", {"continuation_status": "continues"})
        self.ctx.client_continuation_status_submit(parent, "40011", {"continuation_status": "not_continuing"})
        final = self.ctx.client_continuation_status_submit(parent, "40011", {"continuation_status": "continues"})
        self.assertEqual(final["new_status"], "continues")
        g = self.ctx.client_continuation_status_get(parent, "40011")
        self.assertEqual(g["continuation_status"], "continues")


class TestMultiChild(Base):
    def test_two_children_independent_status(self):
        self._cl_link("40020", "Kid A", "8020")
        self._cl_link("40021", "Kid B", "8020")
        parent = _auth(8020, "parent", self.ctx)
        self.ctx.client_continuation_status_submit(parent, "40020", {"continuation_status": "continues"})
        self.ctx.client_continuation_status_submit(parent, "40021", {"continuation_status": "not_continuing"})
        a = self.ctx.client_continuation_status_get(parent, "40020")
        b = self.ctx.client_continuation_status_get(parent, "40021")
        self.assertEqual(a["continuation_status"], "continues")
        self.assertEqual(b["continuation_status"], "not_continuing")


class TestValidation(Base):
    def test_unknown_status_rejected(self):
        self._cl_link("40030", "Kid", "8030")
        parent = _auth(8030, "parent", self.ctx)
        r = self.ctx.client_continuation_status_submit(parent, "40030", {"continuation_status": "unknown"})
        self.assertFalse(r["ok"])
        self.assertEqual(r.get("reason_code"), "invalid_status")

    def test_garbage_status_rejected_by_storage_validation(self):
        self._cl_link("40031", "Kid", "8031")
        parent = _auth(8031, "parent", self.ctx)
        r = self.ctx.client_continuation_status_submit(parent, "40031", {"continuation_status": "bogus"})
        self.assertFalse(r["ok"])
        self.assertEqual(r.get("reason_code"), "invalid_status")

    def test_empty_mk_user_id_rejected(self):
        parent = _auth(8032, "parent", self.ctx)
        r = self.ctx.client_continuation_status_get(parent, "")
        self.assertFalse(r["ok"])


# ── Security / IDOR ──────────────────────────────────────────────────────
class TestSecurityIDOR(Base):
    def test_foreign_parent_cannot_read_another_familys_child(self):
        self._cl_link("40040", "Kid", "8040")
        other_parent = _auth(8041, "parent", self.ctx)
        r = self.ctx.client_continuation_status_get(other_parent, "40040")
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("error"), "a denied request must still carry a real, non-empty user-facing message")

    def test_foreign_parent_cannot_write_another_familys_child(self):
        self._cl_link("40041", "Kid", "8042")
        other_parent = _auth(8043, "parent", self.ctx)
        r = self.ctx.client_continuation_status_submit(other_parent, "40041", {"continuation_status": "not_continuing"})
        self.assertFalse(r["ok"])
        recipients = self.storage.find_onboarding_recipients_by_mk_user("40041")
        self.assertEqual(recipients, [], "a denied write must never create or touch a recipient row")

    def test_unrelated_staff_role_denied(self):
        self._cl_link("40042", "Kid", "8044")
        teacher = _auth(8045, "teacher", self.ctx)
        r = self.ctx.client_continuation_status_get(teacher, "40042")
        self.assertFalse(r["ok"])

    def test_client_manager_can_read_and_write_as_staff(self):
        self._cl_link("40043", "Kid", "8046")
        cm = _auth(8047, "client_manager", self.ctx)
        r = self.ctx.client_continuation_status_submit(cm, "40043", {"continuation_status": "continues"})
        self.assertTrue(r["ok"], r)

    def test_owner_test_mode_requires_explicit_confirm(self):
        # Surgical unit test of THIS endpoint's own owner_test handling —
        # not a re-test of _require_availability_access's own owner-test-
        # mode detection (already covered by test_owner_test_client_
        # context_v71131.py and the availability tests), so the gate is
        # stubbed directly to report source="owner_test".
        self._cl_link("40044", "Kid", "8048")
        owner = _auth(1, "owner", self.ctx)
        orig = self.ctx._require_availability_access
        self.ctx._require_availability_access = lambda auth, mk: ("owner_test", None)
        try:
            blocked = self.ctx.client_continuation_status_submit(owner, "40044", {"continuation_status": "continues"})
            self.assertFalse(blocked["ok"])
            self.assertEqual(blocked.get("error"), "owner_confirm_required")
            allowed = self.ctx.client_continuation_status_submit(owner, "40044", {"continuation_status": "continues", "ownerTestConfirm": True})
            self.assertTrue(allowed["ok"], allowed)
        finally:
            self.ctx._require_availability_access = orig


# ── Audit ─────────────────────────────────────────────────────────────
class TestAudit(Base):
    def test_status_change_writes_continuation_status_changed_event(self):
        self._cl_link("40050", "Kid", "8050")
        parent = _auth(8050, "parent", self.ctx)
        self.ctx.client_continuation_status_submit(parent, "40050", {"continuation_status": "continues"})
        rows = self._audit_rows("40050", "continuation_status_changed")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["new_status"], "continues")
        self.assertEqual(rows[0]["actor_role"], "parent")
        self.assertEqual(rows[0]["actor_telegram_user_id"], "8050")


# ── Frontend statics ──────────────────────────────────────────────────
class TestFrontendContinuationUi(unittest.TestCase):
    def test_my_children_card_has_continuation_buttons(self):
        body = _fn_body("renderMyChildren")
        self.assertIn("setMyChildContinuationConfirm(", body)
        self.assertIn("continues", body)
        self.assertIn("not_continuing", body)

    def test_my_children_card_has_availability_entry_point(self):
        body = _fn_body("renderMyChildren")
        self.assertIn("openClientScheduleAvailabilityModal(", body)

    def test_continuation_setter_uses_apiPostRaw_not_apiPost(self):
        # same bug class found in the schedule module (apiPost throws on
        # ok:false, silently making any `if (!d.ok)` branch unreachable) —
        # this new code must not repeat it.
        body = _fn_body("setMyChildContinuation")
        self.assertIn("_apiPostRaw(", body)
        self.assertNotIn("apiPost(", body)

    def test_not_continuing_goes_through_a_confirm_step(self):
        body = _fn_body("setMyChildContinuationConfirm")
        self.assertIn("uiConfirmSheet(", body)
        self.assertIn('"not_continuing"', body)

    def test_continues_does_not_require_confirm(self):
        body = _fn_body("setMyChildContinuationConfirm")
        # the early-return guard must fire for anything other than
        # not_continuing, so "continues" never reaches uiConfirmSheet.
        self.assertIn('if (status !== "not_continuing")', body)

    def test_load_my_children_fetches_continuation_status_per_child(self):
        body = _fn_body("loadMyChildren")
        self.assertIn("/api/client/continuation-status/", body)

    def test_no_moyklass_reference_in_new_frontend_functions(self):
        for fn in ("setMyChildContinuation", "setMyChildContinuationConfirm", "loadMyChildren", "renderMyChildren"):
            body = _fn_body(fn)
            self.assertNotIn("moyklass", body.lower())

    def test_cache_bust_bumped_for_both_changed_assets(self):
        self.assertIn("app.js?v=7.1.18.6", INDEX_HTML)
        self.assertIn("styles.css?v=7.1.18.1", INDEX_HTML)


class TestNoMoyKlassInBackend(unittest.TestCase):
    def test_no_moyklass_reference_in_new_endpoints(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        start = src.index("def client_continuation_status_get(")
        end = src.index("\n    def _onboarding_signing_secret(", start)
        body = src[start:end]
        self.assertNotIn("moyklass", body.lower())


if __name__ == "__main__":
    unittest.main()
