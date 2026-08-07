"""Tests for v7.1.17.1 pre-merge fix — the confirmed local SQL N+1 in
get_schedule_overview_stats / schedule_group_detail (has_active_parent_link
and find_onboarding_recipients_by_mk_user were each called once per
(student,group) row: ~2394 SQL queries for 1193 pairs on the real
account). Proves the bulk resolvers keep query count chunk-bounded, not
pair/row-bounded — instrumenting real SQL execution counts, not a fragile
exact-count assertion (the architecture may add a few extra bounded
queries; what matters is growth stays flat as the dataset grows).

Run offline:
    python -m unittest tests.test_schedule_sql_bulk_resolution_v71171 -v
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


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage, owner_id: int = 900001) -> MiniAppContext:
    # object.__new__ bypasses MiniAppContext.__init__ (which loads real
    # settings/opens the real configured DB) — same pattern already used
    # by tests.test_client_rollout_gates_v7113_round2._make_ctx.
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(
        admin_ids=[owner_id], senior_teacher_ids=[], web_app_test_roles=False,
        schedule_foundation_enabled=False, schedule_foundation_pilot_telegram_ids=[owner_id],
        food_location_yc1="Кульман 1/1", food_location_yc2="Мстиславца 6",
    )
    return ctx


@contextlib.contextmanager
def _count_sql(storage: Storage):
    """Counts every SQL statement executed against `storage` for the
    duration of the with-block, regardless of which method triggers it."""
    log: list[str] = []
    orig_connect = Storage._connect

    class _CountingConn:
        def __init__(self, real_conn):
            self._real = real_conn

        def execute(self, sql, params=()):
            log.append(sql.strip().split("\n")[0][:100])
            return self._real.execute(sql, params)

        def __getattr__(self, item):
            return getattr(self._real, item)

    @contextlib.contextmanager
    def counting_connect(self):
        with orig_connect(self) as conn:
            yield _CountingConn(conn)

    Storage._connect = counting_connect
    try:
        yield log
    finally:
        Storage._connect = orig_connect


def _seed_snapshot_with_students(storage: Storage, n_students: int, group_id_suffix: str = "500") -> tuple[int, int]:
    """One group, n_students rows in schedule_source_group_students, no
    onboarding/parent-link data at all (so every row resolves to status_
    not_found / no_onboarding_record — the point is query COUNT, not the
    resolved values)."""
    snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
    group_id = storage.upsert_schedule_source_group(
        snap["id"], group_id_suffix, name="Группа", branch_name="Кульман 1/1", course_name="Группа",
    )
    storage.finalize_schedule_source_group_slot(
        group_id, weekday=4, start_time="17:00", duration_minutes=60, confidence="high",
        confidence_reason="test", lessons_count=9, lessons_considered=9,
        first_lesson_date="2025-09-04", last_lesson_date="2025-11-06", students_count=n_students,
    )
    for i in range(n_students):
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, f"9{i:05d}", child_display_name=f"Ребёнок {i}",
            lessons_attended=6, evidence_source="attendance", confidence="high",
            regularity_category="regular_inferred_medium", membership_evidence=0,
        )
    storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
    return snap["id"], group_id


class TestOverviewStatsQueryBounded(unittest.TestCase):
    def test_query_count_does_not_grow_linearly_with_pair_count(self):
        storage = _tmp_storage()
        snap_small_id, _ = _seed_snapshot_with_students(storage, 5, group_id_suffix="500")

        storage2 = _tmp_storage()
        snap_large_id, _ = _seed_snapshot_with_students(storage2, 300, group_id_suffix="600")

        with _count_sql(storage) as log_small:
            storage.get_schedule_overview_stats(snap_small_id, "Кульман 1/1", "Мстиславца 6")
        with _count_sql(storage2) as log_large:
            storage2.get_schedule_overview_stats(snap_large_id, "Кульман 1/1", "Мстиславца 6")

        n_small, n_large = len(log_small), len(log_large)
        # the confirmed pre-fix bug produced ~2 extra queries PER ROW (2394
        # for 1193 pairs) — i.e. n_large - n_small would scale with
        # (300-5)*2 = 590 if unfixed. A generous fixed upper bound (chunk-
        # bounded architecture may add a handful of extra queries, but
        # never one pair's worth) proves it's fixed without pinning an
        # exact, brittle count.
        self.assertLess(n_large, n_small + 20, f"query count grew from {n_small} to {n_large} — looks pair-bounded, not chunk-bounded")
        self.assertLess(n_large, 60, "300 pairs must not need anywhere near 300+ queries")

    def test_no_per_row_onboarding_or_parent_link_query_pattern(self):
        storage = _tmp_storage()
        snap_id, _ = _seed_snapshot_with_students(storage, 50)
        with _count_sql(storage) as log:
            storage.get_schedule_overview_stats(snap_id, "Кульман 1/1", "Мстиславца 6")
        onboarding_queries = [q for q in log if "client_onboarding_recipients" in q]
        parent_link_queries = [q for q in log if "client_parent_child_links" in q]
        self.assertLessEqual(len(onboarding_queries), 3, f"expected a handful of chunked queries, got {len(onboarding_queries)} for 50 students")
        self.assertLessEqual(len(parent_link_queries), 3, f"expected a handful of chunked queries, got {len(parent_link_queries)} for 50 students")


class TestGroupDetailQueryBounded(unittest.TestCase):
    def test_schedule_group_detail_query_count_does_not_grow_with_group_size(self):
        storage = _tmp_storage()
        _, group_id_small = _seed_snapshot_with_students(storage, 5, group_id_suffix="500")

        storage2 = _tmp_storage()
        _, group_id_large = _seed_snapshot_with_students(storage2, 300, group_id_suffix="600")

        ctx_small = _make_ctx(storage, owner_id=900001)
        ctx_large = _make_ctx(storage2, owner_id=900002)
        auth_small = {"user_id": 900001}
        auth_large = {"user_id": 900002}

        with _count_sql(storage) as log_small:
            result_small = ctx_small.schedule_group_detail(auth_small, str(group_id_small), {})
        with _count_sql(storage2) as log_large:
            result_large = ctx_large.schedule_group_detail(auth_large, str(group_id_large), {})

        self.assertTrue(result_small.get("ok"), result_small)
        self.assertTrue(result_large.get("ok"), result_large)
        self.assertEqual(result_small["studentsTotal"], 5)
        self.assertEqual(result_large["studentsTotal"], 300)

        n_small, n_large = len(log_small), len(log_large)
        self.assertLess(n_large, n_small + 20, f"query count grew from {n_small} to {n_large} — looks pair-bounded, not chunk-bounded")
        self.assertLess(n_large, 60, "300 students in one group must not need anywhere near 300+ queries")


if __name__ == "__main__":
    unittest.main()
