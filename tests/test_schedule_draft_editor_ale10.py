"""Tests for ALE-10 — first safe slice of the LOCAL draft editor for
client_manager.

Audit finding: the backend (storage.update_schedule_draft_fields /
set_schedule_draft_status / get_schedule_draft_conflicts / audit log) and
most of the frontend editor (miniapp/app.js _schedOpenDraftEditor /
_schedRenderDraftEditor / _schedSaveDraftFields / dirty-state back-button
guard / version-conflict handling) already existed from the original
v7.1.17 "Расписание" build and are already covered by
tests/test_schedule_draft_editor_ui_v7117.py (frontend statics) and
tests/test_schedule_draft_conflicts_v7117.py (backend: audit, optimistic
versioning, cross-draft conflicts, double-submit, archive finality).

This file covers the specific gaps ALE-10 closes:
  - permission model for the editor's own endpoints (schedule_draft_
    detail / schedule_draft_update / schedule_draft_set_status) at the
    API layer specifically for client_manager vs. an unauthorized role —
    not previously exercised end-to-end for THESE endpoints;
  - SCHEDULE_DRAFT_MUTATIONS_ENABLED=false writes nothing via the API
    layer (the storage layer has no flag check at all — only the API
    wrapper enforces it, so this must be tested at that layer);
  - a slot change (weekday/start_time) actually recomputes a member's
    availability_match via the existing schedule_domain algorithm — not
    just that SOME recompute ran, but that the VALUE is correct;
  - "save with no changes" is a true no-op (no version bump, no new
    audit row) — a bug found during audit: touching a field and
    reverting it to its original value would previously still send a
    real (if value-identical) field_changed update to the backend;
  - the new members_count field on list_schedule_drafts (bulk-computed,
    no N+1) backing the "Дети" tab's draft card's "количество
    участников";
  - no MoyKlass calls anywhere in the new/changed code.

Run offline:
    python -m unittest tests.test_schedule_draft_editor_ale10 -v
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
    # object.__new__ bypasses MiniAppContext.__init__ — same pattern used
    # throughout the schedule module's tests (see e.g.
    # tests.test_schedule_client_manager_ui_ale10._make_ctx).
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
    """Same fixture idiom as tests.test_schedule_draft_conflicts_v7117 —
    a real draft created via the pre-existing (untouched, not part of
    ALE-10) generate_schedule_draft_foundation write path, used here only
    as a test fixture to get a real draft+members to edit."""
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
    return storage.list_schedule_drafts(source_snapshot_id=snap["id"])[0][0]


def _seed_continuing_recipient_with_availability(storage: Storage, mk_user_id: str, weekday: int, start_time: str, end_time: str) -> None:
    now = "2026-06-01T00:00:00"
    with storage._connect() as conn:
        cur = conn.execute(
            "INSERT INTO client_onboarding_recipients "
            "(campaign_id, mk_user_id, child_display_name, continuation_status, added_by, created_at, updated_at) "
            "VALUES (1, ?, 'Ребёнок', 'continues', 'test', ?, ?)",
            (mk_user_id, now, now),
        )
        rid = cur.lastrowid
        conn.execute(
            "INSERT INTO client_schedule_availability "
            "(campaign_id, recipient_id, weekday, start_time, end_time, preference, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?, 'possible', ?, ?)",
            (rid, weekday, start_time, end_time, now, now),
        )


class TestDraftEditorPermissions(unittest.TestCase):
    """owner/admin/client_manager per the existing SCHEDULE_MODULE_ROLES
    gate (_require_schedule_module_access) — the same gate every other
    schedule endpoint already has, now exercised specifically for the
    editor's own two endpoints."""

    def test_client_manager_can_open_draft_detail(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        cm_id = 900501
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, owner_id=900001)
        result = ctx.schedule_draft_detail({"user_id": cm_id}, str(draft["id"]))
        self.assertTrue(result.get("ok"), result)
        self.assertIn("members", result)
        self.assertIn("conflicts", result)
        self.assertIn("audit", result)

    def test_unauthorized_role_cannot_open_draft_detail(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        teacher_id = 900502
        storage.set_staff_role(teacher_id, "teacher")
        ctx = _make_ctx(storage, owner_id=900001)
        result = ctx.schedule_draft_detail({"user_id": teacher_id}, str(draft["id"]))
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "forbidden")

    def test_client_manager_can_update_when_mutations_enabled(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        cm_id = 900503
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, owner_id=900001, mutations_enabled=True)
        result = ctx.schedule_draft_update(
            {"user_id": cm_id}, str(draft["id"]),
            {"expected_version": draft["version"], "changes": {"branch_name": "Мстиславца 6"}},
        )
        self.assertTrue(result.get("ok"), result)

    def test_unauthorized_role_cannot_update_even_with_mutations_enabled(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        teacher_id = 900504
        storage.set_staff_role(teacher_id, "teacher")
        ctx = _make_ctx(storage, owner_id=900001, mutations_enabled=True)
        result = ctx.schedule_draft_update(
            {"user_id": teacher_id}, str(draft["id"]),
            {"expected_version": draft["version"], "changes": {"branch_name": "X"}},
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "forbidden")
        self.assertEqual(storage.get_schedule_draft(draft["id"])["branch_name"], draft["branch_name"])


class TestMutationFlagGate(unittest.TestCase):
    """SCHEDULE_DRAFT_MUTATIONS_ENABLED=false — the storage layer itself
    has no flag check (see update_schedule_draft_fields/
    set_schedule_draft_status), only the API wrapper enforces it, so this
    must be exercised through ctx.schedule_draft_update/
    schedule_draft_set_status specifically, not storage directly."""

    def test_update_writes_nothing_when_mutations_disabled(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        cm_id = 900601
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, owner_id=900001, mutations_enabled=False)
        result = ctx.schedule_draft_update(
            {"user_id": cm_id}, str(draft["id"]),
            {"expected_version": draft["version"], "changes": {"branch_name": "X", "weekday": 2}},
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "mutations_disabled")
        after = storage.get_schedule_draft(draft["id"])
        self.assertEqual(after["branch_name"], draft["branch_name"])
        self.assertEqual(after["weekday"], draft["weekday"])
        self.assertEqual(after["version"], draft["version"])

    def test_status_change_writes_nothing_when_mutations_disabled(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        cm_id = 900602
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, owner_id=900001, mutations_enabled=False)
        result = ctx.schedule_draft_set_status(
            {"user_id": cm_id}, str(draft["id"]),
            {"expected_version": draft["version"], "status": "ready"},
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "mutations_disabled")
        after = storage.get_schedule_draft(draft["id"])
        self.assertEqual(after["status"], draft["status"])
        self.assertEqual(after["version"], draft["version"])


class TestSlotChangeRecomputesAvailability(unittest.TestCase):
    """Uses the EXISTING schedule_domain.match_availability algorithm via
    storage._recompute_schedule_draft_matches (unchanged by ALE-10) — this
    proves the actual VALUE changes correctly, not just that some
    recompute call happened."""

    def test_weekday_and_time_change_flips_member_to_a_real_match(self):
        storage = _make_storage()
        # draft starts Thursday 17:00
        draft = _seed_draft(storage, "500", ["9001"], weekday=4, start_time="17:00")
        # child is only available Tuesday 10:00-12:00 — does not fit the
        # draft's initial slot at all.
        _seed_continuing_recipient_with_availability(storage, "9001", weekday=2, start_time="10:00", end_time="12:00")
        yc1, yc2 = "Кульман 1/1", "Мстиславца 6"
        storage._recompute_schedule_draft_matches(draft["id"], yc1, yc2)
        before = storage.list_schedule_draft_members(draft["id"])[0]
        self.assertNotIn(
            before["availability_match"], ("preferred_match", "possible_match"),
            f"must start out not matching Thursday 17:00 vs Tuesday-only availability, got {before['availability_match']!r}",
        )

        result = storage.update_schedule_draft_fields(
            draft["id"], 1, "T", draft["version"], {"weekday": 2, "start_time": "10:00"}, yc1, yc2,
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["recomputed"], "a weekday/start_time change must trigger recompute")

        after = storage.list_schedule_draft_members(draft["id"])[0]
        self.assertIn(
            after["availability_match"], ("preferred_match", "possible_match"),
            f"expected a real match after aligning the slot with the child's actual availability, got {after['availability_match']!r}",
        )

    def test_branch_change_alone_also_triggers_recompute(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"], branch="Кульман 1/1")
        _seed_continuing_recipient_with_availability(storage, "9001", weekday=4, start_time="17:00", end_time="18:00")
        yc1, yc2 = "Кульман 1/1", "Мстиславца 6"
        result = storage.update_schedule_draft_fields(
            draft["id"], 1, "T", draft["version"], {"branch_name": "Мстиславца 6"}, yc1, yc2,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["recomputed"], "a branch_name change must also trigger recompute")

    def test_teacher_only_change_does_not_trigger_recompute(self):
        # teacher_name doesn't affect Availability matching — must not
        # waste a recompute pass on every field, only slot-relevant ones.
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        result = storage.update_schedule_draft_fields(
            draft["id"], 1, "T", draft["version"], {"teacher_name": "Новый преподаватель"}, "Кульман 1/1", "Мстиславца 6",
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["recomputed"])


class TestSaveWithoutChangesIsNoOp(unittest.TestCase):
    def test_empty_changes_does_not_bump_version_or_write_audit(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        audit_before = storage.list_schedule_draft_audit_log(draft["id"], limit=50)
        result = storage.update_schedule_draft_fields(draft["id"], 1, "T", draft["version"], {}, "Кульман 1/1", "Мстиславца 6")
        self.assertTrue(result["ok"])
        self.assertFalse(result["recomputed"])
        self.assertEqual(storage.get_schedule_draft(draft["id"])["version"], draft["version"])
        audit_after = storage.list_schedule_draft_audit_log(draft["id"], limit=50)
        self.assertEqual(len(audit_after), len(audit_before), "no new audit row for a no-op save")

    def test_only_non_editable_keys_in_changes_is_also_a_no_op(self):
        # e.g. a stray/forged key that isn't in _SD_EDITABLE_FIELDS must be
        # silently filtered, not treated as a real change.
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        result = storage.update_schedule_draft_fields(
            draft["id"], 1, "T", draft["version"], {"id": 999999, "source_snapshot_id": 1}, "Кульман 1/1", "Мстиславца 6",
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["recomputed"])
        self.assertEqual(storage.get_schedule_draft(draft["id"])["version"], draft["version"])


class TestMembersCountOnDraftList(unittest.TestCase):
    def test_members_count_present_and_excludes_manually_excluded(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001", "9002", "9003"])
        storage.exclude_schedule_draft_member(draft["id"], "9002", 1, "T")
        drafts, _total = storage.list_schedule_drafts(source_snapshot_id=draft["source_snapshot_id"])
        self.assertEqual(drafts[0]["members_count"], 2, "excluded member must not count")

    def test_members_count_zero_for_a_draft_with_no_members(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = storage.upsert_schedule_source_group(snap["id"], "500", name="Группа", branch_name="Кульман 1/1", course_name="Курс")
        storage.finalize_schedule_source_group_slot(
            group_id, weekday=4, start_time="17:00", duration_minutes=60, confidence="high",
            confidence_reason="test", lessons_count=0, lessons_considered=0,
            first_lesson_date=None, last_lesson_date=None, students_count=0,
        )
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        storage.generate_schedule_draft_foundation(snap["id"], 1, "T", "Кульман 1/1", "Мстиславца 6")
        drafts, _total = storage.list_schedule_drafts(source_snapshot_id=snap["id"])
        self.assertEqual(drafts[0]["members_count"], 0)

    def test_no_per_draft_count_query_pattern(self):
        # bulk GROUP BY for the whole page, never one COUNT query per draft.
        import contextlib

        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        for i in range(10):
            gid = storage.upsert_schedule_source_group(snap["id"], f"50{i}", name=f"Группа {i}", branch_name="Кульман 1/1", course_name="Курс")
            storage.finalize_schedule_source_group_slot(
                gid, weekday=4, start_time="17:00", duration_minutes=60, confidence="high",
                confidence_reason="test", lessons_count=1, lessons_considered=1,
                first_lesson_date="2025-09-04", last_lesson_date="2025-09-04", students_count=1,
            )
            storage.upsert_schedule_source_group_student(snap["id"], gid, f"900{i}", child_display_name="Р", lessons_attended=9, evidence_source="attendance", confidence="high")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        storage.generate_schedule_draft_foundation(snap["id"], 1, "T", "Кульман 1/1", "Мстиславца 6")

        log: list[str] = []
        orig_connect = Storage._connect

        class _CountingConn:
            def __init__(self, real_conn):
                self._real = real_conn

            def execute(self, sql, params=()):
                # not truncated — the members_count bulk query is long
                # (draft_id IN (...) placeholders can run past a short cap
                # before reaching "GROUP BY"), unlike the short single-line
                # queries other tests' _count_sql helpers log.
                log.append(sql.strip().split("\n")[0])
                return self._real.execute(sql, params)

            def __getattr__(self, item):
                return getattr(self._real, item)

        @contextlib.contextmanager
        def counting_connect(self):
            with orig_connect(self) as conn:
                yield _CountingConn(conn)

        Storage._connect = counting_connect
        try:
            storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        finally:
            Storage._connect = orig_connect
        member_count_queries = [q for q in log if "GROUP BY draft_id" in q]
        self.assertEqual(len(member_count_queries), 1, f"expected exactly one bulk GROUP BY query, got {len(member_count_queries)}")


class TestChildrenDraftCardShowsNewFields(unittest.TestCase):
    def test_children_tab_draft_card_shows_teacher_and_members_count(self):
        body = _fn_body("_schedRenderChildrenDrafts")
        self.assertIn("dr.teacher_name", body)
        self.assertIn("dr.members_count", body)


class TestNoMoyKlassInEditorChanges(unittest.TestCase):
    def test_storage_members_count_addition_never_references_moyklass(self):
        start = STORAGE_PY.index("def list_schedule_drafts(")
        end = STORAGE_PY.index("\n    def list_schedule_draft_members(", start)
        body = STORAGE_PY[start:end]
        self.assertNotIn("moyklass", body.lower())

    def test_new_frontend_functions_never_reference_moyklass_or_apiPost_outside_the_documented_endpoints(self):
        for fn in ("_schedCancelDraftEdits", "_schedEditorFieldChange"):
            body = _fn_body(fn)
            self.assertNotIn("moyklass", body.lower())
            self.assertNotIn("apiPost(", body)
            self.assertNotIn("apiGet(", body)


if __name__ == "__main__":
    unittest.main()
