"""Tests for ALE-10 — safe SINGLE-group draft creation.

Audit finding: neither existing draft-creation write path
(generate_schedule_draft_foundation / persist_schedule_draft_preview) can
create just one draft — both are deliberately whole-snapshot operations
(one draft per eligible group across the ENTIRE snapshot). This adds a
narrow, additive method (storage.create_schedule_draft_for_group) reusing
the exact same per-group insert logic, scoped to a single group_id, plus
its API endpoint (POST /api/schedule/groups/<id>/create-draft) — so a
client_manager/owner can get exactly one real, visible local draft to
manually review the editor against, without mass-generating drafts for
every historical group.

Run offline:
    python -m unittest tests.test_schedule_single_group_draft_ale10 -v
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

STORAGE_PY = (ROOT / "storage.py").read_text(encoding="utf-8")
YC1, YC2 = "Кульман 1/1", "Мстиславца 6"


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
        food_location_yc1=YC1, food_location_yc2=YC2,
    )
    return ctx


def _seed_snapshot_with_two_groups(storage: Storage) -> dict:
    """Two real groups in ONE snapshot — group A has students (one of
    them discontinued), group B has students too, so tests can prove
    creating a draft for A never touches B."""
    snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
    group_a = storage.upsert_schedule_source_group(
        snap["id"], "500", name="Группа A", branch_name=YC1, course_name="Группа A",
        teacher_mk_id="200", teacher_name="Преподаватель A",
    )
    storage.finalize_schedule_source_group_slot(
        group_a, weekday=4, start_time="17:00", duration_minutes=60, confidence="high",
        confidence_reason="test", lessons_count=9, lessons_considered=9,
        first_lesson_date="2025-09-04", last_lesson_date="2025-11-06", students_count=2,
    )
    storage.upsert_schedule_source_group_student(snap["id"], group_a, "9001", child_display_name="Ребёнок 9001", lessons_attended=9, evidence_source="attendance", confidence="high")
    storage.upsert_schedule_source_group_student(snap["id"], group_a, "9002", child_display_name="Ребёнок 9002", lessons_attended=9, evidence_source="attendance", confidence="high")

    group_b = storage.upsert_schedule_source_group(
        snap["id"], "600", name="Группа B", branch_name=YC1, course_name="Группа B",
        teacher_mk_id="201", teacher_name="Преподаватель B",
    )
    storage.finalize_schedule_source_group_slot(
        group_b, weekday=2, start_time="10:00", duration_minutes=60, confidence="high",
        confidence_reason="test", lessons_count=9, lessons_considered=9,
        first_lesson_date="2025-09-04", last_lesson_date="2025-11-06", students_count=1,
    )
    storage.upsert_schedule_source_group_student(snap["id"], group_b, "9101", child_display_name="Ребёнок 9101", lessons_attended=9, evidence_source="attendance", confidence="high")

    storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
    return {"snapshot_id": snap["id"], "group_a": group_a, "group_b": group_b}


def _seed_not_continuing(storage: Storage, mk_user_id: str) -> None:
    now = "2026-06-01T00:00:00"
    with storage._connect() as conn:
        conn.execute(
            "INSERT INTO client_onboarding_recipients "
            "(campaign_id, mk_user_id, child_display_name, continuation_status, added_by, created_at, updated_at) "
            "VALUES (1, ?, 'Ребёнок', 'not_continuing', 'test', ?, ?)",
            (mk_user_id, now, now),
        )


class TestExactlyOneDraftCreated(unittest.TestCase):
    def test_1_creates_exactly_one_draft_for_the_chosen_group(self):
        storage = _make_storage()
        ids = _seed_snapshot_with_two_groups(storage)
        result = storage.create_schedule_draft_for_group(ids["group_a"], 1, "T", YC1, YC2)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["created"])
        drafts, total = storage.list_schedule_drafts(source_snapshot_id=ids["snapshot_id"])
        self.assertEqual(total, 1)
        self.assertEqual(drafts[0]["source_group_id"], ids["group_a"])

    def test_3_the_other_group_is_never_touched(self):
        storage = _make_storage()
        ids = _seed_snapshot_with_two_groups(storage)
        storage.create_schedule_draft_for_group(ids["group_a"], 1, "T", YC1, YC2)
        drafts, _total = storage.list_schedule_drafts(source_snapshot_id=ids["snapshot_id"])
        group_ids_with_drafts = {d["source_group_id"] for d in drafts}
        self.assertNotIn(ids["group_b"], group_ids_with_drafts)

    def test_members_added_and_discontinued_skipped(self):
        storage = _make_storage()
        ids = _seed_snapshot_with_two_groups(storage)
        _seed_not_continuing(storage, "9002")
        result = storage.create_schedule_draft_for_group(ids["group_a"], 1, "T", YC1, YC2)
        self.assertEqual(result["members_added"], 1)
        self.assertEqual(result["members_skipped_discontinued"], 1)
        members = storage.list_schedule_draft_members(result["draft"]["id"])
        self.assertEqual({m["mk_user_id"] for m in members}, {"9001"})


class TestIdempotency(unittest.TestCase):
    def test_2_repeat_call_does_not_create_a_duplicate(self):
        storage = _make_storage()
        ids = _seed_snapshot_with_two_groups(storage)
        r1 = storage.create_schedule_draft_for_group(ids["group_a"], 1, "T", YC1, YC2)
        r2 = storage.create_schedule_draft_for_group(ids["group_a"], 1, "T", YC1, YC2)
        self.assertTrue(r1["created"])
        self.assertFalse(r2["created"])
        self.assertEqual(r1["draft"]["id"], r2["draft"]["id"])
        _drafts, total = storage.list_schedule_drafts(source_snapshot_id=ids["snapshot_id"])
        self.assertEqual(total, 1)

    def test_2b_repeat_call_does_not_duplicate_members(self):
        storage = _make_storage()
        ids = _seed_snapshot_with_two_groups(storage)
        storage.create_schedule_draft_for_group(ids["group_a"], 1, "T", YC1, YC2)
        r2 = storage.create_schedule_draft_for_group(ids["group_a"], 1, "T", YC1, YC2)
        members = storage.list_schedule_draft_members(r2["draft"]["id"])
        self.assertEqual(len(members), 2)


class TestValidation(unittest.TestCase):
    def test_6_invalid_group_id_rejected(self):
        storage = _make_storage()
        result = storage.create_schedule_draft_for_group(999999, 1, "T", YC1, YC2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "not_found")


class TestPermissionsAndFlag(unittest.TestCase):
    def test_4_mutation_flag_false_forbids_creation_via_api(self):
        storage = _make_storage()
        ids = _seed_snapshot_with_two_groups(storage)
        cm_id = 910201
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, mutations_enabled=False)
        result = ctx.schedule_group_create_draft({"user_id": cm_id}, str(ids["group_a"]), {})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "mutations_disabled")
        _drafts, total = storage.list_schedule_drafts(source_snapshot_id=ids["snapshot_id"])
        self.assertEqual(total, 0)

    def test_5_unauthorized_role_forbidden(self):
        storage = _make_storage()
        ids = _seed_snapshot_with_two_groups(storage)
        ctx = _make_ctx(storage, mutations_enabled=True)
        for role, uid in (("teacher", 910202), ("client", 910203)):
            storage.set_staff_role(uid, role)
            result = ctx.schedule_group_create_draft({"user_id": uid}, str(ids["group_a"]), {})
            self.assertFalse(result.get("ok"))
            self.assertEqual(result.get("reason_code"), "forbidden")
        _drafts, total = storage.list_schedule_drafts(source_snapshot_id=ids["snapshot_id"])
        self.assertEqual(total, 0)

    def test_owner_admin_client_manager_allowed(self):
        storage = _make_storage()
        ids = _seed_snapshot_with_two_groups(storage)
        cm_id = 910204
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, mutations_enabled=True)
        result = ctx.schedule_group_create_draft({"user_id": cm_id}, str(ids["group_a"]), {})
        self.assertTrue(result.get("ok"), result)

    def test_6b_invalid_group_id_rejected_via_api(self):
        storage = _make_storage()
        cm_id = 910205
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, mutations_enabled=True)
        result = ctx.schedule_group_create_draft({"user_id": cm_id}, "999999", {})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "not_found")


class TestAudit(unittest.TestCase):
    def test_7_audit_created_entry_present(self):
        storage = _make_storage()
        ids = _seed_snapshot_with_two_groups(storage)
        result = storage.create_schedule_draft_for_group(ids["group_a"], 1, "T", YC1, YC2)
        audit = storage.list_schedule_draft_audit_log(result["draft"]["id"], limit=50)
        actions = [a["action"] for a in audit]
        self.assertIn("created", actions)


class TestNoMoyKlassWrites(unittest.TestCase):
    def test_8_no_moyklass_reference_in_new_method(self):
        start = STORAGE_PY.index("def create_schedule_draft_for_group(")
        end = STORAGE_PY.index("\n    def get_schedule_draft(", start)
        body = STORAGE_PY[start:end]
        self.assertNotIn("moyklass", body.lower())


if __name__ == "__main__":
    unittest.main()
