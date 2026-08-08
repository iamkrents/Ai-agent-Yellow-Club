"""Tests for ALE-8 — persisting the already-accepted draft-preview into
REAL local schedule_drafts/schedule_draft_members rows.

Only decision == "keep_historical_slot" children are ever persisted,
grouped by their resolved historical_group_id (== schedule_drafts.
source_group_id — no new schema). stopped/pending_confirmation/needs_
reassignment/manual_review are NEVER written here — only counted/returned
in the result. The server always recomputes the preview itself
(storage._compute_schedule_draft_preview) from current DB state; no
caller-supplied member list can influence what gets persisted. Nothing is
written to MoyKlass. Deliberately not built on generate_schedule_draft_
foundation (group-centric, no regularity-aware baseline filtering).

Review-gate fix: persistence is INCREMENTAL, not skip-if-exists — an
already-existing draft still accepts newly-eligible children on a later
call (continuation/Availability answers arrive gradually), while never
touching an existing member's manual fields and never removing a member
whose decision later changes (additive-only in this step).

Run offline:
    python -m unittest tests.test_schedule_draft_persistence_ale8 -v
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from web_app_server import MiniAppContext

WEB_APP_SERVER_PY = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
STORAGE_PY = (ROOT / "storage.py").read_text(encoding="utf-8")


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage, owner_id: int = 900001, mutations_enabled: bool = True) -> MiniAppContext:
    # object.__new__ bypasses MiniAppContext.__init__ (which loads real
    # settings/opens the real configured DB) — same pattern already used by
    # tests.test_client_rollout_gates_v7113_round2._make_ctx and
    # tests.test_schedule_sql_bulk_resolution_v71171._make_ctx.
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(
        admin_ids=[owner_id], senior_teacher_ids=[], web_app_test_roles=False,
        schedule_foundation_enabled=True, schedule_foundation_pilot_telegram_ids=[owner_id],
        schedule_draft_mutations_enabled=mutations_enabled,
        food_location_yc1="Кульман 1/1", food_location_yc2="Мстиславца 6",
    )
    return ctx


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


def _seed_group(storage: Storage, snapshot_id: int, mk_class_id: str, *, weekday: int = 4, start_time: str = "17:00",
                 duration_minutes: int = 60, branch_name: str = "Кульман 1/1") -> int:
    group_id = storage.upsert_schedule_source_group(
        snapshot_id, mk_class_id, name=f"Группа {mk_class_id}", branch_name=branch_name, course_name="Курс",
    )
    storage.finalize_schedule_source_group_slot(
        group_id, weekday=weekday, start_time=start_time, duration_minutes=duration_minutes, confidence="high",
        confidence_reason="test", lessons_count=9, lessons_considered=9,
        first_lesson_date="2025-09-04", last_lesson_date="2025-11-06", students_count=1,
    )
    return group_id


def _seed_keep_child(storage: Storage, snapshot_id: int, group_id: int, mk_user_id: str, name: str, weekday: int = 4,
                      start_time: str = "17:00") -> None:
    """A child who will resolve to decision=keep_historical_slot: strong,
    current historical group + continues + a fitting Availability window."""
    storage.upsert_schedule_source_group_student(
        snapshot_id, group_id, mk_user_id, child_display_name=name, lessons_attended=9,
        evidence_source="attendance", confidence="high", regularity_category="regular_confirmed",
        membership_evidence=1, is_current_group=1,
    )
    recipient_id = _seed_recipient(storage, mk_user_id, "continues")
    _seed_availability(storage, recipient_id, weekday=weekday, start_time="00:00", end_time="23:59")


class TestPersistKeepHistoricalSlot(unittest.TestCase):
    def test_one_keep_child_saved_into_correct_group_draft(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        _seed_keep_child(storage, snap["id"], group_id, "9001", "Аня")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        result = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["drafts_created"], 1)
        self.assertEqual(result["members_added"], 1)

        drafts, total = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        self.assertEqual(total, 1)
        self.assertEqual(drafts[0]["source_group_id"], group_id)
        members = storage.list_schedule_draft_members(drafts[0]["id"])
        self.assertEqual([m["mk_user_id"] for m in members], ["9001"])

    def test_multiple_keep_children_same_group_one_draft_many_members(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        for i in range(6):
            _seed_keep_child(storage, snap["id"], group_id, f"90{i:02d}", f"Ребёнок {i}")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        result = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(result["drafts_created"], 1)
        self.assertEqual(result["members_added"], 6)

        drafts, total = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        self.assertEqual(total, 1)
        members = storage.list_schedule_draft_members(drafts[0]["id"])
        self.assertEqual(len(members), 6)

    def test_two_historical_groups_two_drafts(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_a = _seed_group(storage, snap["id"], "500", weekday=3, start_time="10:00")
        group_b = _seed_group(storage, snap["id"], "501", weekday=5, start_time="12:00")
        _seed_keep_child(storage, snap["id"], group_a, "9101", "Боря", weekday=3, start_time="10:00")
        _seed_keep_child(storage, snap["id"], group_b, "9102", "Вика", weekday=5, start_time="12:00")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        result = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(result["drafts_created"], 2)
        self.assertEqual(result["members_added"], 2)

        drafts, total = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        self.assertEqual(total, 2)
        self.assertCountEqual([d["source_group_id"] for d in drafts], [group_a, group_b])


class TestUnresolvedNeverPersisted(unittest.TestCase):
    def _snapshot_with_one_child(self, continuation: str, regularity: str = "regular_confirmed",
                                  is_current: int = 1, availability_weekday=None) -> tuple[Storage, dict, int]:
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500", weekday=4, start_time="17:00")
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, "9200", child_display_name="Гриша", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category=regularity,
            membership_evidence=1, is_current_group=is_current,
        )
        if continuation:
            recipient_id = _seed_recipient(storage, "9200", continuation)
            if availability_weekday is not None:
                _seed_availability(storage, recipient_id, weekday=availability_weekday, start_time="00:00", end_time="23:59")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        return storage, snap, group_id

    def test_stopped_is_not_a_member(self):
        storage, snap, _group_id = self._snapshot_with_one_child("not_continuing")
        result = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(result["drafts_created"], 0)
        self.assertEqual(result["members_added"], 0)
        self.assertEqual(result["unresolved_breakdown"]["stopped"], 1)
        self.assertEqual(result["unresolved_total"], 1)

    def test_pending_confirmation_is_not_a_member(self):
        storage, snap, _group_id = self._snapshot_with_one_child("unknown")
        result = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(result["drafts_created"], 0)
        self.assertEqual(result["members_added"], 0)
        self.assertEqual(result["unresolved_breakdown"]["pending_confirmation"], 1)

    def test_needs_reassignment_is_not_a_member(self):
        # continues, but only available on a day that conflicts with the
        # historical slot (weekday=4) -> needs_reassignment.
        storage, snap, _group_id = self._snapshot_with_one_child("continues", availability_weekday=2)
        result = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(result["drafts_created"], 0)
        self.assertEqual(result["members_added"], 0)
        self.assertEqual(result["unresolved_breakdown"]["needs_reassignment"], 1)

    def test_manual_review_is_not_a_member(self):
        # continues, but no strong current historical group at all.
        storage, snap, _group_id = self._snapshot_with_one_child("continues", regularity="trial", is_current=None)
        result = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(result["drafts_created"], 0)
        self.assertEqual(result["members_added"], 0)
        self.assertEqual(result["unresolved_breakdown"]["manual_review"], 1)

    def test_trial_makeup_visitor_never_persisted_even_with_continues(self):
        # rule 8 end-to-end through the persistence path specifically —
        # never via the untouched legacy generate_schedule_draft_foundation.
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, "9201", child_display_name="Женя", lessons_attended=0,
            evidence_source="membership", confidence="low", regularity_category="makeup",
            n_makeup_visits=4, membership_evidence=0, is_current_group=None,
        )
        _seed_recipient(storage, "9201", "continues")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        result = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(result["drafts_created"], 0)
        self.assertEqual(result["members_added"], 0)
        drafts, total = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        self.assertEqual(total, 0)


class TestFeatureFlagGate(unittest.TestCase):
    def test_mutations_disabled_writes_nothing(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        _seed_keep_child(storage, snap["id"], group_id, "9301", "Клим")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        ctx = _make_ctx(storage, owner_id=900001, mutations_enabled=False)
        result = ctx.schedule_draft_preview_persist({"user_id": 900001}, {})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "mutations_disabled")

        drafts, total = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        self.assertEqual(total, 0, "flag off must write zero drafts")


class TestUnauthorizedRole(unittest.TestCase):
    def test_unauthorized_user_writes_nothing(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        _seed_keep_child(storage, snap["id"], group_id, "9401", "Лена")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        ctx = _make_ctx(storage, owner_id=900001, mutations_enabled=True)
        # 900002 is not in admin_ids/senior_teacher_ids/pilot list — real
        # role resolves to "" (not staff), well outside SCHEDULE_MODULE_ROLES.
        result = ctx.schedule_draft_preview_persist({"user_id": 900002}, {})
        self.assertFalse(result.get("ok"))
        self.assertIn(result.get("reason_code"), ("forbidden", "feature_disabled"))

        drafts, total = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        self.assertEqual(total, 0, "unauthorized caller must write zero drafts")


class TestIdempotentRepeatSubmit(unittest.TestCase):
    def test_repeat_submit_creates_no_duplicates(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        _seed_keep_child(storage, snap["id"], group_id, "9501", "Миша")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        r1 = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        r2 = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")

        self.assertEqual(r1["drafts_created"], 1)
        self.assertEqual(r2["drafts_created"], 0)
        self.assertEqual(r2["drafts_already_existed"], 1)
        self.assertEqual(r2["members_added"], 0, "no NEW eligible children between calls -> nothing added")

        drafts, total = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        self.assertEqual(total, 1, "no duplicate draft on repeat submit")
        members = storage.list_schedule_draft_members(drafts[0]["id"])
        self.assertEqual(len(members), 1, "no duplicate member on repeat submit")

    def test_repeat_submit_preserves_manual_exclusion(self):
        # a client_manager's prior manual edit on an existing draft must
        # never be silently reset by a repeat persist call.
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        _seed_keep_child(storage, snap["id"], group_id, "9502", "Настя")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        drafts, _ = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        draft_id = drafts[0]["id"]
        storage.exclude_schedule_draft_member(draft_id, "9502", 1, "Тест", drafts[0]["version"])

        storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        members = storage.list_schedule_draft_members(draft_id)
        row = next(m for m in members if m["mk_user_id"] == "9502")
        self.assertEqual(row["manually_excluded"], 1, "repeat persist must not resurrect a manually-excluded member")

    def test_repeat_submit_does_not_create_duplicate_for_manually_excluded_child(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        _seed_keep_child(storage, snap["id"], group_id, "9503", "Ольга")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        drafts, _ = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        draft_id = drafts[0]["id"]
        storage.exclude_schedule_draft_member(draft_id, "9503", 1, "Тест", drafts[0]["version"])

        result = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(result["members_added"], 0, "an already-present member (even excluded) is never re-added")
        members = storage.list_schedule_draft_members(draft_id)
        self.assertEqual(len(members), 1, "no duplicate row for the excluded child")

    def test_repeat_submit_does_not_overwrite_manual_note_or_inclusion_flag(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        _seed_keep_child(storage, snap["id"], group_id, "9504", "Павел")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        drafts, _ = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        draft_id = drafts[0]["id"]
        storage.update_schedule_draft_member_note(draft_id, "9504", "Родитель просил вторник", 1, "Тест", drafts[0]["version"])
        with storage._connect() as conn:
            conn.execute(
                "UPDATE schedule_draft_members SET manually_included=1 WHERE draft_id=? AND mk_user_id=?",
                (draft_id, "9504"),
            )

        storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        members = storage.list_schedule_draft_members(draft_id)
        row = next(m for m in members if m["mk_user_id"] == "9504")
        self.assertEqual(row["internal_note"], "Родитель просил вторник", "manual note must survive repeat persist")
        self.assertEqual(row["manually_included"], 1, "manual inclusion flag must survive repeat persist")


class TestIncrementalPersist(unittest.TestCase):
    """Review-gate fix: continuation/Availability answers arrive gradually
    — an existing draft must accept newly-eligible children on a later
    persist call, never lose them because the group was already
    'skipped'."""

    def test_first_persist_six_children_creates_draft_with_six_members(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        for i in range(6):
            _seed_keep_child(storage, snap["id"], group_id, f"91{i:02d}", f"Ребёнок {i}")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        result = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(result["drafts_created"], 1)
        self.assertEqual(result["members_added"], 6)
        drafts, _ = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        self.assertEqual(len(storage.list_schedule_draft_members(drafts[0]["id"])), 6)

    def test_second_persist_same_data_still_six_members_zero_duplicates(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        for i in range(6):
            _seed_keep_child(storage, snap["id"], group_id, f"92{i:02d}", f"Ребёнок {i}")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        r2 = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")

        self.assertEqual(r2["drafts_created"], 0)
        self.assertEqual(r2["members_added"], 0)
        drafts, total = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        self.assertEqual(total, 1)
        self.assertEqual(len(storage.list_schedule_draft_members(drafts[0]["id"])), 6)

    def test_new_eligible_child_appears_later_gets_added_to_existing_draft(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        for i in range(6):
            _seed_keep_child(storage, snap["id"], group_id, f"93{i:02d}", f"Ребёнок {i}")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        drafts, _ = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        draft_id = drafts[0]["id"]
        self.assertEqual(len(storage.list_schedule_draft_members(draft_id)), 6)

        # A day later, a 7th child answers continues + compatible Availability.
        _seed_keep_child(storage, snap["id"], group_id, "9307", "Седьмой")
        r2 = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(r2["drafts_created"], 0, "must not recreate the draft")
        self.assertEqual(r2["drafts_already_existed"], 1)
        self.assertEqual(r2["created_draft_ids"], [])
        self.assertEqual(r2["members_added"], 1, "only the newly-eligible child is added")

        members = storage.list_schedule_draft_members(draft_id)
        self.assertEqual(len(members), 7)
        self.assertIn("9307", [m["mk_user_id"] for m in members])

        # A third call with no further new data changes nothing.
        r3 = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(r3["members_added"], 0)
        self.assertEqual(len(storage.list_schedule_draft_members(draft_id)), 7)

    def test_two_existing_drafts_new_eligible_members_in_both(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_a = _seed_group(storage, snap["id"], "500", weekday=3, start_time="10:00")
        group_b = _seed_group(storage, snap["id"], "501", weekday=5, start_time="12:00")
        _seed_keep_child(storage, snap["id"], group_a, "9611", "Олег", weekday=3, start_time="10:00")
        _seed_keep_child(storage, snap["id"], group_b, "9612", "Полина", weekday=5, start_time="12:00")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")

        _seed_keep_child(storage, snap["id"], group_a, "9613", "Рита", weekday=3, start_time="10:00")
        _seed_keep_child(storage, snap["id"], group_b, "9614", "Саша", weekday=5, start_time="12:00")
        result = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")

        self.assertEqual(result["drafts_created"], 0, "no new drafts — both groups already had one")
        self.assertEqual(result["drafts_already_existed"], 2)
        self.assertEqual(result["members_added"], 2)

        drafts, total = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        self.assertEqual(total, 2, "still exactly two drafts, not four")
        all_member_ids = set()
        for d in drafts:
            for m in storage.list_schedule_draft_members(d["id"]):
                all_member_ids.add(m["mk_user_id"])
        self.assertEqual(all_member_ids, {"9611", "9612", "9613", "9614"})

    def test_child_becoming_stopped_after_persist_is_not_removed_additive_only(self):
        # explicit, intentional additive-only behavior: automatic removal/
        # reconciliation is a separate future rule, not part of this step.
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        _seed_keep_child(storage, snap["id"], group_id, "9403", "Вика")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        drafts, _ = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        draft_id = drafts[0]["id"]
        self.assertEqual(len(storage.list_schedule_draft_members(draft_id)), 1)

        # Family later withdraws — an explicit refusal record always wins
        # over the earlier "continues" (schedule_domain.resolve_continuation
        # priority), so this child's preview decision becomes "stopped".
        _seed_recipient(storage, "9403", "not_continuing", campaign_id=2)

        result = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(result["members_added"], 0)
        self.assertEqual(result["unresolved_breakdown"]["stopped"], 1)
        members = storage.list_schedule_draft_members(draft_id)
        self.assertEqual(len(members), 1, "additive-only: an existing member is never removed by this step")
        self.assertEqual(members[0]["mk_user_id"], "9403")

    def test_unresolved_breakdown_correct_alongside_incremental_add(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        _seed_keep_child(storage, snap["id"], group_id, "9701", "Кеша")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")

        # add one of each unresolved kind, plus one more genuinely new
        # keep_historical_slot child, before the second call.
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, "9702", child_display_name="Стоп", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category="regular_confirmed",
            membership_evidence=1, is_current_group=1,
        )
        _seed_recipient(storage, "9702", "not_continuing")
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, "9703", child_display_name="Пендинг", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category="regular_confirmed",
            membership_evidence=1, is_current_group=1,
        )
        _seed_recipient(storage, "9703", "unknown")
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, "9704", child_display_name="Манрев", lessons_attended=0,
            evidence_source="membership", confidence="low", regularity_category="trial",
            n_trial_visits=3, membership_evidence=0, is_current_group=None,
        )
        _seed_recipient(storage, "9704", "continues")
        _seed_keep_child(storage, snap["id"], group_id, "9705", "Новенький")

        result = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(result["members_added"], 1, "only 9705 is newly eligible")
        self.assertEqual(result["unresolved_breakdown"]["stopped"], 1)
        self.assertEqual(result["unresolved_breakdown"]["pending_confirmation"], 1)
        self.assertEqual(result["unresolved_breakdown"]["manual_review"], 1)
        self.assertEqual(result["unresolved_breakdown"]["needs_reassignment"], 0)
        self.assertEqual(result["unresolved_total"], 3)


class TestTransactionSafety(unittest.TestCase):
    def test_exception_midway_rolls_back_entire_batch(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_a = _seed_group(storage, snap["id"], "500", weekday=3, start_time="10:00")
        group_b = _seed_group(storage, snap["id"], "501", weekday=5, start_time="12:00")
        _seed_keep_child(storage, snap["id"], group_a, "9601", "Олег", weekday=3, start_time="10:00")
        _seed_keep_child(storage, snap["id"], group_b, "9602", "Полина", weekday=5, start_time="12:00")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        with _raise_on_nth_insert(storage, "INSERT INTO schedule_drafts", 2):
            with self.assertRaises(RuntimeError):
                storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")

        # group A's draft (inserted BEFORE the injected failure on group B)
        # must have been rolled back too — the whole batch is one transaction.
        drafts, total = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        self.assertEqual(total, 0, "a mid-batch exception must leave zero partially-created drafts")

    def test_exception_during_incremental_add_rolls_back_whole_call(self):
        # review-gate scenario: exception after a new member was added to
        # ONE existing draft, but before the other group finished — the
        # newly-added member must be rolled back too, not left committed.
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_a = _seed_group(storage, snap["id"], "500", weekday=3, start_time="10:00")
        group_b = _seed_group(storage, snap["id"], "501", weekday=5, start_time="12:00")
        _seed_keep_child(storage, snap["id"], group_a, "9621", "Артём", weekday=3, start_time="10:00")
        _seed_keep_child(storage, snap["id"], group_b, "9622", "Дина", weekday=5, start_time="12:00")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")

        drafts, _ = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        members_before = {d["id"]: len(storage.list_schedule_draft_members(d["id"])) for d in drafts}
        self.assertEqual(list(members_before.values()), [1, 1])

        # new eligible children in both existing groups' drafts
        _seed_keep_child(storage, snap["id"], group_a, "9623", "Рита", weekday=3, start_time="10:00")
        _seed_keep_child(storage, snap["id"], group_b, "9624", "Саша", weekday=5, start_time="12:00")

        # 3rd matching statement = group A's 9621 (ignored) + 9623 (real
        # insert, succeeds) + group B's 9622 (would-be-ignored) -> raise
        # right as group B starts, AFTER group A's new member (9623) was
        # already written (uncommitted) in this same call.
        with _raise_on_nth_insert(storage, "INSERT OR IGNORE INTO schedule_draft_members", 3):
            with self.assertRaises(RuntimeError):
                storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")

        for draft_id, before_count in members_before.items():
            after_count = len(storage.list_schedule_draft_members(draft_id))
            self.assertEqual(after_count, before_count, "mid-batch exception must roll back the already-added new member too")


class TestServerRecomputesNoArbitraryMembers(unittest.TestCase):
    def test_arbitrary_request_body_is_ignored(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        _seed_keep_child(storage, snap["id"], group_id, "9701", "Рома")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        ctx = _make_ctx(storage, owner_id=900001, mutations_enabled=True)
        forged_body = {
            "mk_user_ids": ["totally-fake-id", "9999999"],
            "members": [{"mk_user_id": "totally-fake-id", "child_display_name": "Injected"}],
            "snapshot_id": 999999,
        }
        result = ctx.schedule_draft_preview_persist({"user_id": 900001}, forged_body)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["members_added"], 1)

        drafts, _ = storage.list_schedule_drafts(source_snapshot_id=snap["id"], limit=50)
        members = storage.list_schedule_draft_members(drafts[0]["id"])
        member_ids = [m["mk_user_id"] for m in members]
        self.assertEqual(member_ids, ["9701"])
        self.assertNotIn("totally-fake-id", member_ids)
        self.assertNotIn("9999999", member_ids)

    def test_api_method_never_reads_body_for_membership(self):
        start = WEB_APP_SERVER_PY.index("def schedule_draft_preview_persist(")
        end = WEB_APP_SERVER_PY.index("\n    def schedule_drafts_list(", start)
        api_body = WEB_APP_SERVER_PY[start:end]
        self.assertNotIn("body.get(\"mk_user", api_body)
        self.assertNotIn("body.get(\"members\"", api_body)
        self.assertNotIn('body["mk_user', api_body)

    def test_storage_method_takes_no_member_list_parameter(self):
        start = STORAGE_PY.index("def persist_schedule_draft_preview(")
        signature_line = STORAGE_PY[start:STORAGE_PY.index(")", start) + 1]
        self.assertNotIn("member", signature_line.lower())
        self.assertNotIn("mk_user_id", signature_line)


class TestAuditLog(unittest.TestCase):
    def test_audit_written_after_successful_creation(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        _seed_keep_child(storage, snap["id"], group_id, "9801", "Сева")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        result = storage.persist_schedule_draft_preview(snap["id"], 42, "Сотрудник Т", "Кульман 1/1", "Мстиславца 6")
        draft_id = result["created_draft_ids"][0]
        audit = storage.list_schedule_draft_audit_log(draft_id, limit=10)
        self.assertTrue(any(a["action"] == "created" for a in audit))
        created_entry = next(a for a in audit if a["action"] == "created")
        self.assertEqual(created_entry["actor_user_id"], 42)
        self.assertEqual(created_entry["actor_name"], "Сотрудник Т")
        self.assertIn("ALE-8", created_entry["details"] or "")

    def test_no_audit_written_when_nothing_persisted(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, "9802", child_display_name="Таня", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category="regular_confirmed",
            membership_evidence=1, is_current_group=1,
        )
        _seed_recipient(storage, "9802", "not_continuing")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        result = storage.persist_schedule_draft_preview(snap["id"], 1, "Тест", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(result["created_draft_ids"], [])


@contextlib.contextmanager
def _raise_on_nth_insert(storage: Storage, needle: str, after_calls: int):
    """Monkeypatches Storage._connect so the (after_calls)-th execute()
    whose SQL contains `needle` raises — proving the surrounding
    `with self._connect() as conn:` block in persist_schedule_draft_preview
    rolls back everything written earlier in the SAME block (sqlite3's
    Connection context-manager commit/rollback semantics)."""
    orig_connect = Storage._connect
    counter = {"n": 0}

    class _FaultyConn:
        def __init__(self, real_conn):
            self._real = real_conn

        def execute(self, sql, params=()):
            if needle in sql:
                counter["n"] += 1
                if counter["n"] == after_calls:
                    raise RuntimeError("injected failure for rollback test")
            return self._real.execute(sql, params)

        def __getattr__(self, item):
            return getattr(self._real, item)

    @contextlib.contextmanager
    def faulty_connect(self):
        with orig_connect(self) as conn:
            yield _FaultyConn(conn)

    Storage._connect = faulty_connect
    try:
        yield
    finally:
        Storage._connect = orig_connect


class TestNoMoyKlassWrites(unittest.TestCase):
    def test_storage_persist_method_never_references_moyklass(self):
        start = STORAGE_PY.index("def persist_schedule_draft_preview(")
        end = STORAGE_PY.index("\n    # ── Conflict detection", start)
        body = STORAGE_PY[start:end]
        self.assertNotIn("moyklass", body.lower())

    def test_api_persist_method_never_references_moyklass_client(self):
        start = WEB_APP_SERVER_PY.index("def schedule_draft_preview_persist(")
        end = WEB_APP_SERVER_PY.index("\n    def schedule_drafts_list(", start)
        body = WEB_APP_SERVER_PY[start:end]
        self.assertNotIn("self.moyklass", body)

    def test_persist_method_never_calls_legacy_group_centric_generator(self):
        start = STORAGE_PY.index("def persist_schedule_draft_preview(")
        end = STORAGE_PY.index("\n    # ── Conflict detection", start)
        body = STORAGE_PY[start:end]
        self.assertNotIn("generate_schedule_draft_foundation", body)


if __name__ == "__main__":
    unittest.main()
