"""Tests for v7.1.17.1 — ALE-6 section 5: bulk MoyKlass ID -> name
resolution wired into the schedule sync, with a concrete no-N+1 proof, and
moyklass_client.list_joins_for_classes' client-side filtering correctness
(the API's classIds array filter is confirmed NOT honored server-side —
see the method's own docstring — so this client-side behavior is load-
bearing, not incidental).

Run offline:
    python -m unittest tests.test_schedule_name_resolution_v71171 -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import schedule_sync
from moyklass_client import MoyKlassClient
from storage import Storage, SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _mk_result(ok=True, data=None, status=200, error=""):
    r = MagicMock()
    r.ok = ok
    r.data = data or {}
    r.status = status
    r.error = error
    return r


def _lesson_record(user_id, lesson_id, date_str, class_id="500"):
    # deliberately no name-shaped field anywhere — matches the real MoyKlass
    # lessonRecords shape confirmed by the ALE-6 audit (no className/
    # userName/etc. field ever present on a raw record).
    return {
        "id": f"rec-{lesson_id}-{user_id}", "userId": user_id, "visit": True, "test": False,
        "missedLessonRecordId": None, "userSubscription": None,
        "lesson": {"id": lesson_id, "classId": class_id, "date": date_str, "beginTime": "17:00", "endTime": "18:00", "status": "1", "filialId": "1", "teacherIds": ["200"]},
    }


class TestBulkNameResolutionWiring(unittest.TestCase):
    def test_names_backfilled_from_bulk_batch_endpoint(self):
        storage = _make_storage()
        d = date(2025, 9, 4)
        records = [_lesson_record("9001", "L0", d.isoformat()), _lesson_record("9002", "L0b", d.isoformat())]
        mk = MagicMock()
        mk.is_configured = True
        mk.get_classes.return_value = _mk_result(data={"classes": [{"id": "500", "name": "Группа"}]})
        mk.list_lesson_records_between.return_value = _mk_result(data={"lessonRecords": records})
        mk.list_joins_for_classes.return_value = _mk_result(data={"joins": []})
        mk._lookup_maps_cached.return_value = {"filials": {}, "teachers": {}, "classes": {}, "rooms": {}}
        mk.request.return_value = _mk_result(data={"users": [
            {"id": "9001", "fullName": "Иванов Иван"},
            {"id": "9002", "fullName": "Петров Пётр"},
        ]})

        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])

        groups, _ = storage.list_schedule_source_groups(snap["id"])
        students = storage.list_schedule_source_group_students(groups[0]["id"])
        names = {s["mk_user_id"]: s["child_display_name"] for s in students}
        self.assertEqual(names["9001"], "Иванов Иван")
        self.assertEqual(names["9002"], "Петров Пётр")

    def test_unresolved_name_falls_back_to_explicit_placeholder_not_the_id(self):
        # schedule_sync itself never fabricates "ID <n>" as a name — that's
        # purely a frontend rendering fallback (_schedMemberNameHtml); the
        # stored child_display_name for a genuinely unresolved child is "".
        storage = _make_storage()
        d = date(2025, 9, 4)
        records = [_lesson_record("9001", "L0", d.isoformat())]
        mk = MagicMock()
        mk.is_configured = True
        mk.get_classes.return_value = _mk_result(data={"classes": [{"id": "500", "name": "Группа"}]})
        mk.list_lesson_records_between.return_value = _mk_result(data={"lessonRecords": records})
        mk.list_joins_for_classes.return_value = _mk_result(data={"joins": []})
        mk._lookup_maps_cached.return_value = {"filials": {}, "teachers": {}, "classes": {}, "rooms": {}}
        mk.request.return_value = _mk_result(data={"users": []})  # nobody resolvable

        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        groups, _ = storage.list_schedule_source_groups(snap["id"])
        students = storage.list_schedule_source_group_students(groups[0]["id"])
        self.assertEqual(students[0]["child_display_name"], "")

    def test_name_resolution_call_count_bounded_not_one_per_student(self):
        """The concrete no-N+1 proof: 130 distinct students needing name
        resolution must not produce anywhere near 130 HTTP calls — the
        batch endpoint (50 ids/request, 2 encodings tried) bounds it to a
        small, N-independent-ish constant, never O(n) with n=130."""
        storage = _make_storage()
        d = date(2025, 9, 4)
        n_students = 130
        records = [_lesson_record(str(9000 + i), f"L{i}", d.isoformat()) for i in range(n_students)]
        mk = MagicMock()
        mk.is_configured = True
        mk.get_classes.return_value = _mk_result(data={"classes": [{"id": "500", "name": "Группа"}]})
        mk.list_lesson_records_between.return_value = _mk_result(data={"lessonRecords": records})
        mk.list_joins_for_classes.return_value = _mk_result(data={"joins": []})
        mk._lookup_maps_cached.return_value = {"filials": {}, "teachers": {}, "classes": {}, "rooms": {}}
        mk.request.return_value = _mk_result(data={"users": [
            {"id": str(9000 + i), "fullName": f"Ребёнок {i}"} for i in range(n_students)
        ]})

        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])

        # 130 ids / 50-per-batch = 3 batches, tried in up to 2 param
        # encodings each -> at most 6 calls to resolve ALL 130 names, far
        # below one-call-per-student.
        self.assertLessEqual(mk.request.call_count, 10, f"expected a small bounded call count, got {mk.request.call_count} for {n_students} students")
        self.assertGreater(mk.request.call_count, 0)

        groups, _ = storage.list_schedule_source_groups(snap["id"])
        students = storage.list_schedule_source_group_students(groups[0]["id"])
        self.assertEqual(len(students), n_students)
        self.assertTrue(all(s["child_display_name"] for s in students), "every student's name resolved via the bulk batch")


class TestListJoinsForClassesClientSideFilter(unittest.TestCase):
    """moyklass_client.list_joins_for_classes — the classIds server-side
    filter is confirmed NOT honored by the real API (verified empirically
    during the ALE-6 audit: a classIds=[X] request returned the same
    unfiltered set as no filter at all), so correctness here depends
    entirely on the client-side filter, not the request params."""

    def _client_with_mocked_request(self, pages):
        client = MoyKlassClient("https://api.example.test", "fake-key", 10)
        client._access_token = "fake-token"
        call_log = []

        def fake_request(method, path, payload=None, params=None):
            call_log.append(dict(params or {}))
            idx = len(call_log) - 1
            if idx < len(pages):
                return _mk_result(data={"joins": pages[idx]})
            return _mk_result(data={"joins": []})

        client.request = fake_request
        return client, call_log

    def test_filters_to_only_requested_class_ids(self):
        all_joins = (
            [{"id": i, "userId": 1000 + i, "classId": 500, "stats": {"totalPayed": 10}} for i in range(3)]
            + [{"id": 100 + i, "userId": 2000 + i, "classId": 999, "stats": {"totalPayed": 10}} for i in range(3)]
        )
        client, calls = self._client_with_mocked_request([all_joins])
        result = client.list_joins_for_classes(["500"])
        self.assertTrue(result.ok)
        joins = result.data["joins"]
        self.assertTrue(all(j["classId"] == 500 for j in joins), "must filter out classId=999 client-side")
        self.assertEqual(len(joins), 3)

    def test_paginates_until_short_page(self):
        page1 = [{"id": i, "userId": 1000 + i, "classId": 500, "stats": {}} for i in range(500)]
        page2 = [{"id": 1000 + i, "userId": 3000 + i, "classId": 500, "stats": {}} for i in range(10)]
        client, calls = self._client_with_mocked_request([page1, page2])
        result = client.list_joins_for_classes(["500"], page_size=500)
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["joins"]), 510)
        self.assertEqual(len(calls), 2, "must stop after the first short page")

    def test_no_classid_filter_param_sent_server_side(self):
        # the whole point: don't rely on classIds/classId query params at
        # all, since neither is guaranteed correct for this use case.
        client, calls = self._client_with_mocked_request([[]])
        client.list_joins_for_classes(["500", "600"])
        self.assertNotIn("classIds", calls[0])
        self.assertNotIn("classId", calls[0])

    def test_empty_class_ids_short_circuits_without_a_call(self):
        client, calls = self._client_with_mocked_request([[]])
        result = client.list_joins_for_classes([])
        self.assertTrue(result.ok)
        self.assertEqual(result.data["joins"], [])
        self.assertEqual(len(calls), 0)


if __name__ == "__main__":
    unittest.main()
