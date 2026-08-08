"""Tests for ALE-10 — member composition editing + manual unresolved
assignment for a LOCAL schedule draft (client_manager).

Audit finding (see PR description / final report): exclude/include/note
member mutations already existed (storage.exclude_schedule_draft_member /
include_schedule_draft_member / update_schedule_draft_member_note +
matching API endpoints + frontend buttons) but had NO optimistic
versioning at all (draft.version was never bumped, expected_version was
never checked) — unlike every other draft mutation. include_schedule_
draft_member's "insert new row" branch already technically allowed
inserting an arbitrary, unvalidated mk_user_id (proven by the pre-existing
test test_42b_include_brand_new_child_not_in_source_roster in
test_schedule_draft_conflicts_v7117.py) — that primitive is left
UNCHANGED (still used only by the "Включить"/restore button) and this PR
adds a SEPARATE, validated path (storage.add_schedule_draft_member_manual
+ /api/schedule/drafts/<id>/members/add) for the new "Добавить ребёнка"
UI action, with the product-required safety checks (same snapshot only,
no duplicates, not_continuing hard-blocked, pending_confirmation requires
explicit override).

This file covers:
  - EXISTING MEMBER actions (exclude/restore/note) now carry optimistic
    versioning end-to-end (version bump, audit, stale-version rejection);
  - MANUAL ADD validation (same snapshot, duplicate, not_continuing,
    pending override, real Availability compatibility against the
    draft's OWN slot, backend recompute);
  - cross-draft conflict semantics are unaffected (reused, not
    reimplemented) after a manual add;
  - permission model + SCHEDULE_DRAFT_MUTATIONS_ENABLED gate for every
    new/changed endpoint;
  - frontend statics for the new "Добавить ребёнка" UI and the
    editorDirty guard on all member actions;
  - no MoyKlass calls anywhere in the new/changed code.

Run offline:
    python -m unittest tests.test_schedule_draft_member_composition_ale10 -v
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

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
STORAGE_PY = (ROOT / "storage.py").read_text(encoding="utf-8")


def _fn_body(name: str) -> str:
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\([^)]*\) \{(.*?)\n\}\n", APP_JS, re.S)
    assert m, f"function {name} not found"
    return m.group(1)


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage, owner_id: int = 900001, mutations_enabled: bool = False) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(
        admin_ids=[owner_id], senior_teacher_ids=[], web_app_test_roles=False,
        schedule_foundation_enabled=True, schedule_foundation_pilot_telegram_ids=[owner_id],
        schedule_draft_mutations_enabled=mutations_enabled,
        food_location_yc1="Кульман 1/1", food_location_yc2="Мстиславца 6",
    )
    return ctx


def _seed_draft(storage: Storage, mk_class_id: str, mk_user_ids: list[str], weekday: int = 4,
                 start_time: str = "17:00", teacher_mk_id: str = "200", branch: str = "Кульман 1/1") -> dict:
    snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
    group_id = storage.upsert_schedule_source_group(
        snap["id"], mk_class_id, name=f"Группа {mk_class_id}", branch_name=branch,
        course_name=f"Группа {mk_class_id}", teacher_mk_id=teacher_mk_id, teacher_name="Преподаватель",
    )
    storage.finalize_schedule_source_group_slot(
        group_id, weekday=weekday, start_time=start_time, duration_minutes=60, confidence="high",
        confidence_reason="test", lessons_count=9, lessons_considered=9,
        first_lesson_date="2025-09-04", last_lesson_date="2025-11-06", students_count=len(mk_user_ids),
    )
    for uid in mk_user_ids:
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, uid, child_display_name=f"Ребёнок {uid}", lessons_attended=9,
            evidence_source="attendance", confidence="high",
        )
    storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
    storage.generate_schedule_draft_foundation(snap["id"], 1, "T", "Кульман 1/1", "Мстиславца 6")
    draft = storage.list_schedule_drafts(source_snapshot_id=snap["id"])[0][0]
    return draft


def _seed_candidate_in_same_snapshot(
    storage: Storage, snapshot_id: int, mk_class_id: str, mk_user_id: str, name: str, *,
    weekday: int = 4, start_time: str = "17:00", branch: str = "Кульман 1/1",
    regularity: str = "regular_confirmed", is_current: int = 1,
) -> int:
    """Adds a student to a NEW source group within an EXISTING snapshot —
    a real, addressable candidate for manual add (same snapshot as the
    target draft) who is not automatically a member of any draft."""
    group_id = storage.upsert_schedule_source_group(
        snapshot_id, mk_class_id, name=f"Группа {mk_class_id}", branch_name=branch, course_name="Курс",
    )
    storage.finalize_schedule_source_group_slot(
        group_id, weekday=weekday, start_time=start_time, duration_minutes=60, confidence="high",
        confidence_reason="test", lessons_count=9, lessons_considered=9,
        first_lesson_date="2025-09-04", last_lesson_date="2025-11-06", students_count=1,
    )
    storage.upsert_schedule_source_group_student(
        snapshot_id, group_id, mk_user_id, child_display_name=name, lessons_attended=9,
        evidence_source="attendance", confidence="high", regularity_category=regularity,
        membership_evidence=1, is_current_group=is_current,
    )
    return group_id


def _seed_recipient(storage: Storage, mk_user_id: str, continuation_status: str, campaign_id: int = 1) -> int:
    now = "2026-06-01T00:00:00"
    with storage._connect() as conn:
        cur = conn.execute(
            "INSERT INTO client_onboarding_recipients "
            "(campaign_id, mk_user_id, child_display_name, continuation_status, added_by, created_at, updated_at) "
            "VALUES (?, ?, 'Ребёнок', ?, 'test', ?, ?)",
            (campaign_id, mk_user_id, continuation_status, now, now),
        )
        return int(cur.lastrowid)


def _seed_availability(storage: Storage, recipient_id: int, weekday: int, start_time: str, end_time: str, preference: str = "possible") -> None:
    now = "2026-06-01T00:00:00"
    with storage._connect() as conn:
        conn.execute(
            "INSERT INTO client_schedule_availability "
            "(campaign_id, recipient_id, weekday, start_time, end_time, preference, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
            (recipient_id, weekday, start_time, end_time, preference, now, now),
        )


YC1, YC2 = "Кульман 1/1", "Мстиславца 6"


# ── EXISTING MEMBER — tests 1-6 ─────────────────────────────────────────
class TestExistingMemberActionsVersioning(unittest.TestCase):
    def test_exclude_existing_member_via_api(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        cm_id = 910001
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, mutations_enabled=True)
        result = ctx.schedule_draft_member_exclude(
            {"user_id": cm_id}, str(draft["id"]), {"mk_user_id": "9001", "expected_version": draft["version"]},
        )
        self.assertTrue(result.get("ok"), result)
        member = storage.list_schedule_draft_members(draft["id"])[0]
        self.assertEqual(member["manually_excluded"], 1)

    def test_restore_previously_excluded_member_via_api(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        cm_id = 910002
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, mutations_enabled=True)
        r1 = ctx.schedule_draft_member_exclude(
            {"user_id": cm_id}, str(draft["id"]), {"mk_user_id": "9001", "expected_version": draft["version"]},
        )
        self.assertTrue(r1["ok"])
        new_version = r1["draft"]["version"]
        r2 = ctx.schedule_draft_member_include(
            {"user_id": cm_id}, str(draft["id"]), {"mk_user_id": "9001", "expected_version": new_version},
        )
        self.assertTrue(r2["ok"], r2)
        member = storage.list_schedule_draft_members(draft["id"])[0]
        self.assertEqual(member["manually_excluded"], 0)

    def test_note_update_via_api(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        cm_id = 910003
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, mutations_enabled=True)
        result = ctx.schedule_draft_member_note(
            {"user_id": cm_id}, str(draft["id"]),
            {"mk_user_id": "9001", "note": "Родитель просил четверг", "expected_version": draft["version"]},
        )
        self.assertTrue(result.get("ok"), result)
        member = storage.list_schedule_draft_members(draft["id"])[0]
        self.assertEqual(member["internal_note"], "Родитель просил четверг")

    def test_version_increases_on_each_member_mutation(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        v0 = draft["version"]
        r1 = storage.exclude_schedule_draft_member(draft["id"], "9001", 1, "T", v0)
        self.assertEqual(r1["draft"]["version"], v0 + 1)
        r2 = storage.include_schedule_draft_member(draft["id"], "9001", "Ребёнок 9001", None, 1, "T", YC1, YC2, v0 + 1)
        self.assertEqual(r2["draft"]["version"], v0 + 2)
        r3 = storage.update_schedule_draft_member_note(draft["id"], "9001", "заметка", 1, "T", v0 + 2)
        self.assertEqual(r3["draft"]["version"], v0 + 3)

    def test_audit_written_for_each_member_mutation(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        storage.exclude_schedule_draft_member(draft["id"], "9001", 1, "T", draft["version"])
        storage.include_schedule_draft_member(draft["id"], "9001", "Ребёнок 9001", None, 1, "T", YC1, YC2, draft["version"] + 1)
        storage.update_schedule_draft_member_note(draft["id"], "9001", "заметка", 1, "T", draft["version"] + 2)
        audit = storage.list_schedule_draft_audit_log(draft["id"], limit=50)
        actions = [a["action"] for a in audit]
        self.assertIn("member_excluded", actions)
        self.assertIn("member_included", actions)
        self.assertIn("note_added", actions)

    def test_stale_expected_version_rejected_for_exclude(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001", "9002"])
        ok1 = storage.exclude_schedule_draft_member(draft["id"], "9001", 1, "T", draft["version"])
        self.assertTrue(ok1["ok"])
        stale = storage.exclude_schedule_draft_member(draft["id"], "9002", 1, "T", draft["version"])
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["reason_code"], "version_conflict")
        # the stale call must not have touched 9002 at all
        member = next(m for m in storage.list_schedule_draft_members(draft["id"]) if m["mk_user_id"] == "9002")
        self.assertEqual(member["manually_excluded"], 0)

    def test_stale_expected_version_rejected_for_note(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        storage.update_schedule_draft_member_note(draft["id"], "9001", "первая", 1, "T", draft["version"])
        stale = storage.update_schedule_draft_member_note(draft["id"], "9001", "вторая", 1, "T", draft["version"])
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["reason_code"], "version_conflict")
        member = storage.list_schedule_draft_members(draft["id"])[0]
        self.assertEqual(member["internal_note"], "первая")


# ── MANUAL ADD — tests 7-14 ─────────────────────────────────────────────
class TestManualAddValidation(unittest.TestCase):
    def test_child_from_same_snapshot_can_be_added(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        _seed_candidate_in_same_snapshot(storage, draft["source_snapshot_id"], "600", "9010", "Новый Ребёнок")
        _seed_recipient(storage, "9010", "continues")
        result = storage.add_schedule_draft_member_manual(draft["id"], "9010", 1, "T", draft["version"], YC1, YC2)
        self.assertTrue(result["ok"], result)
        ids = {m["mk_user_id"] for m in storage.list_schedule_draft_members(draft["id"])}
        self.assertIn("9010", ids)

    def test_duplicate_in_same_draft_rejected(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        result = storage.add_schedule_draft_member_manual(draft["id"], "9001", 1, "T", draft["version"], YC1, YC2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "already_member")
        self.assertEqual(len(storage.list_schedule_draft_members(draft["id"])), 1)

    def test_child_from_a_different_snapshot_is_rejected(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        # "9999" exists, but in a DIFFERENT snapshot/draft entirely.
        _other_draft = _seed_draft(storage, "700", ["9999"])
        result = storage.add_schedule_draft_member_manual(draft["id"], "9999", 1, "T", draft["version"], YC1, YC2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "not_in_snapshot")
        ids = {m["mk_user_id"] for m in storage.list_schedule_draft_members(draft["id"])}
        self.assertNotIn("9999", ids)

    def test_not_continuing_child_cannot_be_added(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        _seed_candidate_in_same_snapshot(storage, draft["source_snapshot_id"], "600", "9011", "Остановился")
        _seed_recipient(storage, "9011", "not_continuing")
        result = storage.add_schedule_draft_member_manual(draft["id"], "9011", 1, "T", draft["version"], YC1, YC2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "not_continuing")
        ids = {m["mk_user_id"] for m in storage.list_schedule_draft_members(draft["id"])}
        self.assertNotIn("9011", ids)

    def test_pending_confirmation_requires_explicit_override(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        # no client_onboarding_recipients row at all -> continuation unconfirmed.
        _seed_candidate_in_same_snapshot(storage, draft["source_snapshot_id"], "600", "9012", "Ждём ответа")
        blocked = storage.add_schedule_draft_member_manual(draft["id"], "9012", 1, "T", draft["version"], YC1, YC2)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["reason_code"], "pending_confirmation_requires_override")
        self.assertNotIn("9012", {m["mk_user_id"] for m in storage.list_schedule_draft_members(draft["id"])})

        allowed = storage.add_schedule_draft_member_manual(
            draft["id"], "9012", 1, "T", draft["version"], YC1, YC2, override_pending=True,
        )
        self.assertTrue(allowed["ok"], allowed)
        self.assertIn("9012", {m["mk_user_id"] for m in storage.list_schedule_draft_members(draft["id"])})

    def test_compatible_child_gets_a_real_match_against_the_draft_slot(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"], weekday=4, start_time="17:00")
        _seed_candidate_in_same_snapshot(storage, draft["source_snapshot_id"], "600", "9013", "Подходит")
        rid = _seed_recipient(storage, "9013", "continues")
        _seed_availability(storage, rid, weekday=4, start_time="16:00", end_time="19:00")
        result = storage.add_schedule_draft_member_manual(draft["id"], "9013", 1, "T", draft["version"], YC1, YC2)
        self.assertTrue(result["ok"], result)
        member = next(m for m in storage.list_schedule_draft_members(draft["id"]) if m["mk_user_id"] == "9013")
        self.assertIn(member["availability_match"], ("preferred_match", "possible_match"))

    def test_incompatible_availability_does_not_block_add_but_is_recorded(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"], weekday=4, start_time="17:00")
        _seed_candidate_in_same_snapshot(storage, draft["source_snapshot_id"], "600", "9014", "Не подходит по времени")
        rid = _seed_recipient(storage, "9014", "continues")
        _seed_availability(storage, rid, weekday=2, start_time="10:00", end_time="11:00")
        result = storage.add_schedule_draft_member_manual(draft["id"], "9014", 1, "T", draft["version"], YC1, YC2)
        self.assertTrue(result["ok"], f"an Availability conflict must never block a manual add: {result}")
        member = next(m for m in storage.list_schedule_draft_members(draft["id"]) if m["mk_user_id"] == "9014")
        self.assertEqual(member["availability_match"], "time_conflict", "the real conflict must be visible, not hidden")

    def test_inserted_member_matches_a_fresh_backend_recompute(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"], weekday=3, start_time="10:00", branch="Кульман 1/1")
        _seed_candidate_in_same_snapshot(storage, draft["source_snapshot_id"], "600", "9015", "Проверка recompute")
        rid = _seed_recipient(storage, "9015", "continues")
        _seed_availability(storage, rid, weekday=3, start_time="09:00", end_time="12:00")
        storage.add_schedule_draft_member_manual(draft["id"], "9015", 1, "T", draft["version"], YC1, YC2)
        member = next(m for m in storage.list_schedule_draft_members(draft["id"]) if m["mk_user_id"] == "9015")
        fresh = storage.resolve_schedule_student_status(
            "9015", weekday=draft["weekday"], start_time=draft["start_time"],
            duration_minutes=draft["duration_minutes"],
            group_branch_code=__import__("schedule_domain").branch_code_from_name(draft["branch_name"], YC1, YC2),
            planned_start_date=draft["planned_start_date"],
        )
        self.assertEqual(member["continuation_status"], fresh["continuation_status"])
        self.assertEqual(member["availability_match"], fresh["availability_match"])

    def test_manual_add_bumps_version_and_writes_audit(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        _seed_candidate_in_same_snapshot(storage, draft["source_snapshot_id"], "600", "9016", "Аудит")
        _seed_recipient(storage, "9016", "continues")
        result = storage.add_schedule_draft_member_manual(draft["id"], "9016", 1, "T", draft["version"], YC1, YC2)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["draft"]["version"], draft["version"] + 1)
        audit = storage.list_schedule_draft_audit_log(draft["id"], limit=50)
        added = [a for a in audit if a["action"] == "member_added"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["new_value"], "9016")

    def test_manual_add_stale_version_rejected(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        _seed_candidate_in_same_snapshot(storage, draft["source_snapshot_id"], "600", "9017", "A")
        _seed_recipient(storage, "9017", "continues")
        _seed_candidate_in_same_snapshot(storage, draft["source_snapshot_id"], "601", "9018", "B")
        _seed_recipient(storage, "9018", "continues")
        ok1 = storage.add_schedule_draft_member_manual(draft["id"], "9017", 1, "T", draft["version"], YC1, YC2)
        self.assertTrue(ok1["ok"], ok1)
        stale = storage.add_schedule_draft_member_manual(draft["id"], "9018", 1, "T", draft["version"], YC1, YC2)
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["reason_code"], "version_conflict")
        self.assertNotIn("9018", {m["mk_user_id"] for m in storage.list_schedule_draft_members(draft["id"])})


# ── CROSS-DRAFT — test 15 ────────────────────────────────────────────────
class TestCrossDraftConflictsAfterManualAdd(unittest.TestCase):
    def test_manually_added_child_triggers_existing_cross_draft_conflict(self):
        storage = _make_storage()
        draft_a = _seed_draft(storage, "500", ["9001"], weekday=4, start_time="17:00")
        draft_b = _seed_draft(storage, "600", ["9020"], weekday=4, start_time="17:15")
        # move "9020" from draft_b's own snapshot roster? Not needed —
        # add "9020" from draft_a's OWN snapshot as a fresh candidate, then
        # a manual overlap is created against draft_b via a same-mk_user_id
        # coincidence is unrealistic; instead prove the REUSED conflict
        # engine picks up a manually-added member the same way it already
        # does for an auto-generated one (test_45 in test_schedule_draft_
        # conflicts_v7117.py) — add "9001" (draft_a's own member) to
        # draft_b manually where the roster overlaps in the same snapshot
        # is not necessary; the conflict engine keys off mk_user_id alone.
        _seed_candidate_in_same_snapshot(storage, draft_b["source_snapshot_id"], "601", "9001", "Ребёнок 9001")
        _seed_recipient(storage, "9001", "continues")
        result = storage.add_schedule_draft_member_manual(draft_b["id"], "9001", 1, "T", draft_b["version"], YC1, YC2)
        self.assertTrue(result["ok"], result)
        conflicts = storage.get_schedule_draft_conflicts(draft_a["id"])
        self.assertEqual(len(conflicts["child_conflicts"]), 1)
        self.assertEqual(conflicts["child_conflicts"][0]["mk_user_id"], "9001")
        self.assertEqual(conflicts["child_conflicts"][0]["other_draft_id"], draft_b["id"])


# ── SECURITY — tests 16-18 ───────────────────────────────────────────────
class TestSecurityFlagAndPermissions(unittest.TestCase):
    def test_flag_off_blocks_add(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        _seed_candidate_in_same_snapshot(storage, draft["source_snapshot_id"], "600", "9021", "X")
        cm_id = 910101
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, mutations_enabled=False)
        result = ctx.schedule_draft_member_add(
            {"user_id": cm_id}, str(draft["id"]), {"mk_user_id": "9021", "expected_version": draft["version"]},
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "mutations_disabled")
        self.assertNotIn("9021", {m["mk_user_id"] for m in storage.list_schedule_draft_members(draft["id"])})

    def test_flag_off_blocks_exclude_include_note(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        cm_id = 910102
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, mutations_enabled=False)
        body = {"mk_user_id": "9001", "expected_version": draft["version"]}
        for fn in (ctx.schedule_draft_member_exclude, ctx.schedule_draft_member_include, ctx.schedule_draft_member_note):
            result = fn({"user_id": cm_id}, str(draft["id"]), dict(body, note="x"))
            self.assertFalse(result.get("ok"))
            self.assertEqual(result.get("reason_code"), "mutations_disabled")

    def test_client_and_teacher_roles_forbidden_from_add(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        _seed_candidate_in_same_snapshot(storage, draft["source_snapshot_id"], "600", "9022", "X")
        ctx = _make_ctx(storage, mutations_enabled=True)
        for role, uid in (("teacher", 910103), ("client", 910104)):
            storage.set_staff_role(uid, role)
            result = ctx.schedule_draft_member_add(
                {"user_id": uid}, str(draft["id"]), {"mk_user_id": "9022", "expected_version": draft["version"]},
            )
            self.assertFalse(result.get("ok"))
            self.assertEqual(result.get("reason_code"), "forbidden")
        self.assertNotIn("9022", {m["mk_user_id"] for m in storage.list_schedule_draft_members(draft["id"])})

    def test_owner_admin_client_manager_allowed_to_add(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        _seed_candidate_in_same_snapshot(storage, draft["source_snapshot_id"], "600", "9023", "X")
        _seed_recipient(storage, "9023", "continues")
        owner_id = 900001
        ctx = _make_ctx(storage, owner_id=owner_id, mutations_enabled=True)
        result = ctx.schedule_draft_member_add(
            {"user_id": owner_id}, str(draft["id"]), {"mk_user_id": "9023", "expected_version": draft["version"]},
        )
        self.assertTrue(result.get("ok"), result)

    def test_candidate_list_endpoint_also_permission_gated(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        ctx = _make_ctx(storage, mutations_enabled=True)
        teacher_id = 910105
        storage.set_staff_role(teacher_id, "teacher")
        result = ctx.schedule_draft_add_candidates({"user_id": teacher_id}, str(draft["id"]), {})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "forbidden")

    def test_candidate_list_endpoint_not_gated_by_mutations_flag(self):
        # read-only, same convention as schedule_draft_preview — a
        # client_manager must be able to SEE candidates even before
        # mutations are enabled (the "Добавить" button itself is what's
        # disabled by the flag on the frontend).
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        cm_id = 910106
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, mutations_enabled=False)
        result = ctx.schedule_draft_add_candidates({"user_id": cm_id}, str(draft["id"]), {})
        self.assertTrue(result.get("ok"), result)


class TestCandidateListStorage(unittest.TestCase):
    def test_candidates_exclude_existing_members_and_scope_to_own_snapshot(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        _seed_candidate_in_same_snapshot(storage, draft["source_snapshot_id"], "600", "9030", "Кандидат")
        other_draft = _seed_draft(storage, "700", ["9999"])  # different snapshot entirely
        result = storage.get_schedule_draft_add_candidates(draft["id"], YC1, YC2)
        ids = {c["mk_user_id"] for c in result["candidates"]}
        self.assertIn("9030", ids)
        self.assertNotIn("9001", ids, "already a member of this draft — must not appear as a candidate")
        self.assertNotIn("9999", ids, "belongs to a different snapshot entirely")
        for c in result["candidates"]:
            self.assertIn("candidate_group", c)
            self.assertIn(c["candidate_group"], ("assignable", "needs_review", "pending_confirmation", "stopped"))

    def test_candidate_group_reuses_real_backend_decision(self):
        import schedule_domain
        self.assertEqual(schedule_domain.candidate_group_for_decision("keep_historical_slot"), "assignable")
        self.assertEqual(schedule_domain.candidate_group_for_decision("needs_reassignment"), "needs_review")
        self.assertEqual(schedule_domain.candidate_group_for_decision("manual_review"), "needs_review")
        self.assertEqual(schedule_domain.candidate_group_for_decision("pending_confirmation"), "pending_confirmation")
        self.assertEqual(schedule_domain.candidate_group_for_decision("stopped"), "stopped")


# ── UI statics — tests 19-27 ─────────────────────────────────────────────
class TestFrontendAddChildUi(unittest.TestCase):
    def test_19_candidate_list_fetched_from_the_draft_scoped_endpoint(self):
        body = _fn_body("_schedLoadAddCandidates")
        self.assertIn("/add-candidates", body)

    def test_20_stopped_candidates_get_a_disabled_add_button(self):
        body = _fn_body("_schedAddCandidateRowHtml")
        self.assertIn('c.candidate_group === "stopped"', body)
        self.assertIn("disabled", body)

    def test_21_pending_confirmation_triggers_explicit_confirm_dialog(self):
        body = _fn_body("_schedAddChildToDraft")
        self.assertIn('"pending_confirmation_requires_override"', body)
        self.assertIn("uiConfirmSheet(", body)
        self.assertIn("Родитель ещё не подтвердил продолжение обучения", body)

    def test_22_existing_member_action_buttons_still_present(self):
        render = _fn_body("_schedRenderDraftEditor")
        self.assertIn("_schedIncludeMember(", render)
        self.assertIn("_schedExcludeMember(", render)
        self.assertIn("_schedEditMemberNote(", render)
        self.assertIn("_schedOpenAddChildPicker()", render)

    def test_23_candidate_card_reuses_shared_name_component_not_raw_ids(self):
        body = _fn_body("_schedAddCandidateRowHtml")
        self.assertIn("_schedMemberNameHtml(c)", body)

    def test_24_all_member_mutations_are_guarded_by_dirty_check(self):
        for fn in ("_schedExcludeMember", "_schedIncludeMember", "_schedEditMemberNote", "_schedOpenAddChildPicker", "_schedAddChildToDraft"):
            body = _fn_body(fn)
            self.assertIn("_schedMemberActionsBlockedByDirty()", body, f"{fn} must guard against editorDirty")

    def test_24b_add_button_disabled_while_editor_dirty(self):
        render = _fn_body("_schedRenderDraftEditor")
        add_btn_line = next(line for line in render.splitlines() if "_schedOpenAddChildPicker()" in line)
        self.assertIn("_schedState.editorDirty", add_btn_line)

    def test_24c_member_action_buttons_disabled_while_editor_dirty(self):
        render = _fn_body("_schedRenderDraftEditor")
        include_line = next(line for line in render.splitlines() if "_schedIncludeMember(" in line)
        exclude_line = next(line for line in render.splitlines() if "_schedExcludeMember(" in line)
        note_line = next(line for line in render.splitlines() if "_schedEditMemberNote(" in line)
        for line in (include_line, exclude_line, note_line):
            self.assertIn("_schedState.editorDirty", line)

    def test_25_member_mutations_send_and_then_pick_up_the_new_version(self):
        for fn in ("_schedExcludeMember", "_schedIncludeMember", "_schedEditMemberNote", "_schedAddChildToDraft"):
            body = _fn_body(fn)
            self.assertIn("expected_version: dr.version", body)
            self.assertIn("_schedLoadDraftDetail(dr.id)", body)

    def test_26_version_conflict_handled_by_every_member_mutation(self):
        for fn in ("_schedExcludeMember", "_schedIncludeMember", "_schedEditMemberNote", "_schedAddChildToDraft"):
            body = _fn_body(fn)
            self.assertIn('"version_conflict"', body)
            self.assertIn("editorVersionConflict = true", body)

    def test_27_children_readonly_screen_functions_unchanged(self):
        # regression guard — the read-only "Дети" screen (ALE-10 round 1)
        # must still exist and still be untouched by this member-
        # composition slice.
        self.assertIn("function _schedRenderChildrenPlan(", APP_JS)
        self.assertIn("function _schedRenderChildrenList(", APP_JS)
        body = _fn_body("_schedRenderChildrenList")
        self.assertNotIn("addCandidates", body)
        self.assertNotIn("_schedAddChildToDraft", body)


# ── NO MOYKLASS — test 28 ────────────────────────────────────────────────
class TestNoMoyKlassWrites(unittest.TestCase):
    def test_new_storage_methods_never_reference_moyklass(self):
        # Ends the slice at the next TOP-LEVEL (4-space-indented) banner
        # comment or "def" after the function's own body — never at a
        # generic "next def" scan, which would swallow the NEXT method's
        # own leading comment block (e.g. get_schedule_overview_stats's
        # own "no MoyKlass calls" comment) and produce a false positive.
        # A blank line followed by a 4-space-indented "#" or "def" can
        # only be a NEW top-level member (every in-body comment in these
        # methods is indented 8+ spaces), so this reliably finds the true
        # end of the target method's own body.
        boundary_re = re.compile(r"\n\n(?:    #|    def )")
        for name in ("_schedule_draft_member_mutation_precheck", "add_schedule_draft_member_manual", "get_schedule_draft_add_candidates"):
            start = STORAGE_PY.index(f"def {name}(")
            m = boundary_re.search(STORAGE_PY, start)
            assert m, f"could not find end boundary for {name}"
            body = STORAGE_PY[start:m.start()]
            self.assertNotIn("moyklass", body.lower(), f"{name} must never touch MoyKlass")

    def test_new_frontend_functions_never_reference_moyklass(self):
        for fn in ("_schedOpenAddChildPicker", "_schedLoadAddCandidates", "_schedAddChildToDraft", "_schedRenderAddChildPicker"):
            body = _fn_body(fn)
            self.assertNotIn("moyklass", body.lower())


if __name__ == "__main__":
    unittest.main()
