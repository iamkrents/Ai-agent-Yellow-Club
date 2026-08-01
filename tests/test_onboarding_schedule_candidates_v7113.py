"""Tests for v7.1.13 research/feature — "Ученики из расписания за период"
candidate source for mass client-onboarding campaigns.

Real MoyKlass account audit (2025-09-01..today, live GET-only queries, no
PII logged) found: lesson.status is reliably 0/1 in this account — no
cancelled (status="3") lesson or cancelled (skip=true) enrollment was ever
observed; lessonRecord.visit (attendance) DID show real variance (10408
true / 2191 false) and is treated as reliable. Both cancel-exclusion
filters are still implemented (defensive/forward-compatible) but their
correctness against a genuine positive example is untested by this file
too — these tests use synthetic cancelled records to prove the FILTERING
LOGIC works, which is a different claim from "this account uses these
fields for real cancellations".

Never creates a recipient/invite/pilot/payment record — this is a pure
read-only discovery source, same contract as onboarding_campaign_bulk_
candidates. Reuses the exact same candidate shape/selection UI.

Run:
    python -m unittest tests.test_onboarding_schedule_candidates_v7113 -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from web_app_server import MiniAppContext
from moyklass_client import MoyKlassClient, MoyKlassResult

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
    return ctx


def _auth(uid: int, role: str, ctx: MiniAppContext) -> dict:
    ctx._role_store[uid] = role
    return {"user_id": uid}


def _lesson(id_, status=1, d="2025-09-05"):
    return {"id": id_, "classId": 100, "status": status, "date": d}


def _record(rec_id, user_id, lesson_status=1, skip=False, visit=None, test_flag=False, lesson_date="2025-09-05"):
    rec = {"id": rec_id, "userId": user_id, "skip": skip, "test": test_flag,
            "lesson": {"id": rec_id * 10, "classId": 100 + (rec_id % 3), "status": lesson_status, "date": lesson_date}}
    if visit is not None:
        rec["visit"] = visit
    return rec


class _FakeMoyKlassSchedule:
    """Stands in for MoyKlassClient at the boundary this feature actually
    calls: get_lessons_between + list_lesson_records_between. Real pagination
    correctness for those two methods is covered separately (this file's
    TestScheduleRecordsPagination class + the already-existing get_lessons_
    between/list_users_bulk pagination coverage) — this fake lets the
    aggregation-logic tests be fast, deterministic, and offline."""

    def __init__(self, lessons=None, records=None, lessons_ok=True, records_ok=True, records_error=""):
        self.lessons = lessons if lessons is not None else []
        self.records = records if records is not None else []
        self.lessons_ok = lessons_ok
        self.records_ok = records_ok
        self.records_error = records_error
        self.calls = {"lessons": 0, "records": 0}

    def get_lessons_between(self, date_from, date_to, limit=500):
        self.calls["lessons"] += 1
        return MoyKlassResult(self.lessons_ok, data={"items": self.lessons})

    def list_lesson_records_between(self, date_from, date_to, limit=20000):
        self.calls["records"] += 1
        return MoyKlassResult(self.records_ok, data={"lessonRecords": self.records}, error=self.records_error)

    def list_users_bulk(self, params=None, page_size=200, max_pages=30):
        # onboarding_campaign_candidates_from_schedule resolves display names
        # via the same bulk-users path "all clients" uses — auto-derive a
        # synthetic user entry for every mk_user_id seen in self.records so
        # tests don't need to hand-author a separate matching user list.
        ids = sorted({str(r.get("userId")) for r in self.records if r.get("userId")})
        items = [{"id": uid, "name": f"Student{uid}", "lastName": "Test"} for uid in ids]
        return MoyKlassResult(True, data={"items": items, "diagnostics": {
            "pages_loaded": 1, "raw_items": len(items), "unique_items": len(items), "stopped_reason": "short_page",
        }})


class ScheduleTestBase(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        self.owner = _auth(1, "owner", self.ctx)

    def _params(self, **overrides):
        p = {
            "date_from": "2025-09-01", "date_to": "2026-08-01",
            "minimum_lessons": "1", "attended_only": "false",
            "exclude_cancelled_classes": "true", "exclude_cancelled_enrollments": "true",
        }
        p.update({k: str(v) for k, v in overrides.items()})
        return p


# ─────────────────────────────────────────────────────────────────────────────
# 1/2 — dedup across multiple lessons / multiple groups for the same student
# ─────────────────────────────────────────────────────────────────────────────

class TestDedup(ScheduleTestBase):
    def test_1_multiple_lessons_same_student_one_candidate(self):
        records = [_record(1, "5001", visit=True), _record(2, "5001", visit=True), _record(3, "5001", visit=True)]
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=records)
        r = self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, self._params())
        self.assertTrue(r["ok"], r)
        mk_ids = [c["mk_user_id"] for c in r["candidates"]] if r.get("candidates") else []
        self.assertEqual(r["diagnostics"]["unique_students_any_record"], 1)

    def test_2_multiple_groups_same_student_one_candidate(self):
        records = [
            _record(1, "5002", visit=True),
            {**_record(2, "5002", visit=True), "lesson": {"id": 20, "classId": 999, "status": 1, "date": "2025-09-06"}},
        ]
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=records)
        r = self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, self._params())
        self.assertEqual(r["diagnostics"]["unique_students_any_record"], 1)


# ─────────────────────────────────────────────────────────────────────────────
# 3/4/5 — cancellation and missing-user-id handling
# ─────────────────────────────────────────────────────────────────────────────

class TestExclusions(ScheduleTestBase):
    def test_3_cancelled_lesson_excluded(self):
        records = [_record(1, "6001", lesson_status=1, visit=True), _record(2, "6001", lesson_status=3, visit=True)]
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=records)
        r = self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, self._params(minimum_lessons=2))
        # only 1 non-cancelled record for this student -> excluded at threshold 2
        self.assertEqual(r["diagnostics"]["unique_students_eligible"], 0)
        # cancelled_lessons counts the separate /lessons endpoint scan (not
        # populated by this fake); cancelled_class_records is the count
        # derived from lessonRecords' own nested lesson.status, which is
        # what actually drives per-candidate exclusion here.
        self.assertEqual(r["diagnostics"]["cancelled_class_records"], 1)

    def test_3b_cancelled_lesson_not_excluded_when_flag_off(self):
        records = [_record(1, "6002", lesson_status=3, visit=True), _record(2, "6002", lesson_status=3, visit=True)]
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=records)
        r = self.ctx.onboarding_campaign_candidates_from_schedule(
            self.owner, self._params(minimum_lessons=2, exclude_cancelled_classes="false"))
        self.assertEqual(r["diagnostics"]["unique_students_eligible"], 1)

    def test_4_cancelled_enrollment_excluded(self):
        records = [_record(1, "6003", skip=False, visit=True), _record(2, "6003", skip=True, visit=True)]
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=records)
        r = self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, self._params(minimum_lessons=2))
        self.assertEqual(r["diagnostics"]["unique_students_eligible"], 0)
        self.assertEqual(r["diagnostics"]["cancelled_enrollments"], 1)

    def test_4b_cancelled_enrollment_not_excluded_when_flag_off(self):
        records = [_record(1, "6004", skip=True, visit=True), _record(2, "6004", skip=True, visit=True)]
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=records)
        r = self.ctx.onboarding_campaign_candidates_from_schedule(
            self.owner, self._params(minimum_lessons=2, exclude_cancelled_enrollments="false"))
        self.assertEqual(r["diagnostics"]["unique_students_eligible"], 1)

    def test_5_record_without_user_id_safely_skipped(self):
        records = [_record(1, "6005", visit=True), {"id": 2, "skip": False, "lesson": {"status": 1, "date": "2025-09-06"}}]
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=records)
        r = self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, self._params())
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["diagnostics"]["records_without_mk_user_id"], 1)
        self.assertEqual(r["diagnostics"]["unique_students_any_record"], 1)


# ─────────────────────────────────────────────────────────────────────────────
# 6-9 — thresholds
# ─────────────────────────────────────────────────────────────────────────────

class TestThresholds(ScheduleTestBase):
    def _student_with_n_records(self, uid, n):
        return [_record(i, uid, visit=True) for i in range(1, n + 1)]

    def test_6_threshold_1(self):
        records = self._student_with_n_records("7001", 1)
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=records)
        r = self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, self._params(minimum_lessons=1))
        self.assertEqual(r["diagnostics"]["unique_students_eligible"], 1)

    def test_7_threshold_2(self):
        one = self._student_with_n_records("7002", 1)
        two = [{**r, "userId": "7003"} for r in self._student_with_n_records("7003", 2)]
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=one + two)
        r = self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, self._params(minimum_lessons=2))
        self.assertEqual(r["diagnostics"]["unique_students_eligible"], 1)

    def test_8_threshold_4(self):
        three = [{**rec, "id": rec["id"] + 100} for rec in self._student_with_n_records("7004", 3)]
        four = [{**rec, "id": rec["id"] + 200, "userId": "7005"} for rec in self._student_with_n_records("7005", 4)]
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=three + four)
        r = self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, self._params(minimum_lessons=4))
        self.assertEqual(r["diagnostics"]["unique_students_eligible"], 1)

    def test_9_threshold_8(self):
        seven = [{**rec, "id": rec["id"] + 300} for rec in self._student_with_n_records("7006", 7)]
        eight = [{**rec, "id": rec["id"] + 400, "userId": "7007"} for rec in self._student_with_n_records("7007", 8)]
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=seven + eight)
        r = self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, self._params(minimum_lessons=8))
        self.assertEqual(r["diagnostics"]["unique_students_eligible"], 1)


# ─────────────────────────────────────────────────────────────────────────────
# 10/11 — attendance criterion
# ─────────────────────────────────────────────────────────────────────────────

class TestAttendance(ScheduleTestBase):
    def test_10_attended_only_filters_by_visit_true(self):
        records = [
            _record(1, "8001", visit=True),
            _record(2, "8002", visit=False),
        ]
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=records)
        r = self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, self._params(attended_only="true", minimum_lessons=1))
        self.assertEqual(r["diagnostics"]["unique_students_eligible"], 1)
        self.assertEqual(r["diagnostics"]["unique_students_attended"], 1)

    def test_11_attendance_field_absent_is_unsupported_not_crash(self):
        # No "visit" key at all on any record — attended_only must return an
        # empty, well-formed result (0 eligible), never raise or silently
        # treat missing data as "attended".
        records = [_record(1, "8003", visit=None), _record(2, "8004", visit=None)]
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=records)
        r = self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, self._params(attended_only="true", minimum_lessons=1))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["diagnostics"]["unique_students_attended"], 0)
        self.assertEqual(len(r["candidates"]), 0)


# ─────────────────────────────────────────────────────────────────────────────
# 12-15 — real pagination behavior of the new client-level wrapper
# ─────────────────────────────────────────────────────────────────────────────

class TestScheduleRecordsPagination(unittest.TestCase):
    def setUp(self):
        self.client = MoyKlassClient("https://fake", "fake-key")

    def _stub(self, responder):
        self.client.request = responder

    def test_12_pagination_across_pages(self):
        def responder(method, path, payload=None, params=None):
            offset = int(params.get("offset", 0))
            if offset == 0:
                items = [{"id": i, "userId": str(i), "lesson": {"date": "2025-09-05", "status": 1}} for i in range(500)]
            elif offset == 500:
                items = [{"id": i, "userId": str(i), "lesson": {"date": "2025-09-05", "status": 1}} for i in range(500, 550)]
            else:
                items = []
            return MoyKlassResult(True, data={"lessonRecords": items})
        self._stub(responder)
        r = self.client.list_lesson_records_between(date(2025, 9, 1), date(2025, 9, 6), limit=2000)
        self.assertTrue(r.ok)
        self.assertEqual(len(r.data["lessonRecords"]), 550)

    def test_13_repeated_page_safe_stop(self):
        def responder(method, path, payload=None, params=None):
            items = [{"id": i, "userId": str(i), "lesson": {"date": "2025-09-05", "status": 1}} for i in range(500)]
            return MoyKlassResult(True, data={"lessonRecords": items})
        self._stub(responder)
        r = self.client.list_lesson_records_between(date(2025, 9, 1), date(2025, 9, 6), limit=2000)
        self.assertTrue(r.ok)
        self.assertEqual(len(r.data["lessonRecords"]), 500)  # never loops forever collecting duplicates

    def test_14_error_on_a_later_page_truncates_silently_known_limitation(self):
        # Documents a genuine, PRE-EXISTING behavior of the underlying
        # _scan_lesson_records_for_month (unmodified by this feature, also
        # used by get_month_analytics/get_monthly_children_report): once the
        # first page of a date-param variant succeeds, a later page's
        # failure does NOT flip the outer result to ok=False — the scan
        # just stops and returns ok=True with whatever was collected so far.
        # This differs from list_users_bulk's contract (which does propagate
        # a page failure as ok=False with partial data). Not fixed here —
        # changing this shared, multi-feature scan function's error semantics
        # is out of scope for this read-only research feature. Reported
        # honestly as a data-quality/reliability caveat in the final report.
        def responder(method, path, payload=None, params=None):
            offset = int(params.get("offset", 0))
            if offset == 0:
                items = [{"id": i, "userId": str(i), "lesson": {"date": "2025-09-05", "status": 1}} for i in range(500)]
                return MoyKlassResult(True, data={"lessonRecords": items})
            return MoyKlassResult(False, error="upstream timeout", status=504)
        self._stub(responder)
        r = self.client.list_lesson_records_between(date(2025, 9, 1), date(2025, 9, 6), limit=2000)
        self.assertTrue(r.ok)  # known limitation — NOT a false-full-success in the sense of wrong data, just an unsignaled truncation
        self.assertEqual(len(r.data["lessonRecords"]), 500)  # only page 1 — page 2's failure silently stopped the scan

    def test_15_duplicates_between_pages_deduped(self):
        def responder(method, path, payload=None, params=None):
            offset = int(params.get("offset", 0))
            if offset == 0:
                items = [{"id": i, "userId": str(i), "lesson": {"date": "2025-09-05", "status": 1}} for i in range(1, 501)]
            elif offset == 500:
                # overlaps ids 480-500 with page 1
                items = [{"id": i, "userId": str(i), "lesson": {"date": "2025-09-05", "status": 1}} for i in range(480, 551)]
            else:
                items = []
            return MoyKlassResult(True, data={"lessonRecords": items})
        self._stub(responder)
        r = self.client.list_lesson_records_between(date(2025, 9, 1), date(2025, 9, 6), limit=2000)
        self.assertEqual(len(r.data["lessonRecords"]), 550)


# ─────────────────────────────────────────────────────────────────────────────
# 16 — realistic full-year range doesn't hit the max-period guard
# ─────────────────────────────────────────────────────────────────────────────

class TestFullRange(ScheduleTestBase):
    def test_16_sep_2025_to_today_accepted(self):
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=[_record(1, "9001", visit=True)])
        params = self._params(date_from="2025-09-01", date_to=date.today().isoformat())
        r = self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, params)
        self.assertTrue(r["ok"], r)

    def test_16b_over_max_period_rejected(self):
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=[])
        params = self._params(date_from="2020-01-01", date_to="2026-08-01")
        r = self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, params)
        self.assertFalse(r["ok"])

    def test_16c_date_from_after_date_to_rejected(self):
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=[])
        r = self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, self._params(date_from="2026-01-01", date_to="2025-01-01"))
        self.assertFalse(r["ok"])

    def test_16d_invalid_minimum_lessons_rejected(self):
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=[])
        r = self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, self._params(minimum_lessons=3))
        self.assertFalse(r["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# 17 — permission matrix
# ─────────────────────────────────────────────────────────────────────────────

class TestPermissions(ScheduleTestBase):
    def test_17_owner_admin_client_manager_allowed_others_denied(self):
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=[])
        for role in ("owner", "admin", "client_manager"):
            actor = _auth(10, role, self.ctx)
            r = self.ctx.onboarding_campaign_candidates_from_schedule(actor, self._params())
            self.assertTrue(r["ok"], f"{role} should be allowed: {r}")
        for role in ("teacher", "operations", "intern", "other"):
            actor = _auth(11, role, self.ctx)
            r = self.ctx.onboarding_campaign_candidates_from_schedule(actor, self._params())
            self.assertFalse(r.get("ok", True), f"{role} should be denied: {r}")


# ─────────────────────────────────────────────────────────────────────────────
# 21/22/23 — regression + side-effect safety
# ─────────────────────────────────────────────────────────────────────────────

class TestSideEffectSafety(ScheduleTestBase):
    def test_21_existing_all_clients_source_unaffected(self):
        class _BulkFake:
            def list_users_bulk(self, params=None, page_size=200, max_pages=30):
                return MoyKlassResult(True, data={"items": [{"id": "1", "name": "A", "lastName": "B"}],
                                                   "diagnostics": {"pages_loaded": 1, "raw_items": 1, "unique_items": 1, "stopped_reason": "short_page"}})
        self.ctx.moyklass = _BulkFake()
        r = self.ctx.onboarding_campaign_bulk_candidates(self.owner, {})
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(r["candidates"]), 1)

    def test_22_campaign_not_modified_by_search(self):
        c = self.ctx.onboarding_campaign_create(self.owner, {"name": "Sched test", "academic_year": "2026/2027"})["campaign"]
        self.ctx.onboarding_campaign_start(self.owner, str(c["id"]))
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=[_record(1, "9101", visit=True), _record(2, "9102", visit=True)])
        before = self.storage.get_onboarding_campaign(c["id"])
        self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, self._params())
        after = self.storage.get_onboarding_campaign(c["id"])
        self.assertEqual(before, after)
        self.assertEqual(len(self.storage.list_onboarding_campaign_recipients(c["id"])), 0)

    def test_23_no_invitations_pilot_or_payment_intents_created(self):
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=[_record(1, "9201", visit=True)])
        self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, self._params())
        with self.storage._connect() as conn:
            invites = conn.execute("SELECT COUNT(*) c FROM client_onboarding_invites").fetchone()["c"]
            pilots = conn.execute("SELECT COUNT(*) c FROM payment_automation_pilot_clients").fetchone()["c"]
            intents = conn.execute("SELECT COUNT(*) c FROM payment_intents").fetchone()["c"]
        self.assertEqual(invites, 0)
        self.assertEqual(pilots, 0)
        self.assertEqual(intents, 0)

    def test_24_diagnostics_dict_contains_only_aggregate_keys_no_pii(self):
        self.ctx.moyklass = _FakeMoyKlassSchedule(records=[_record(1, "9301", visit=True)])
        r = self.ctx.onboarding_campaign_candidates_from_schedule(self.owner, self._params())
        pii_markers = ("name", "phone", "email", "address", "фио", "телефон")
        for key in r["diagnostics"].keys():
            self.assertNotIn(key.lower(), pii_markers)
        # values must all be ints, dicts-of-ints, or None/str error messages — never a list of records
        for k, v in r["diagnostics"].items():
            self.assertNotIsInstance(v, list, f"diagnostics['{k}'] must never be a raw record list")


# ─────────────────────────────────────────────────────────────────────────────
# 18/19/20 — frontend: aggregated diagnostics UI, select-all reuse, single import request
# ─────────────────────────────────────────────────────────────────────────────

APP_JS = ROOT / "miniapp" / "app.js"
_js_cache = None


def _js():
    global _js_cache
    if _js_cache is None:
        _js_cache = APP_JS.read_text(encoding="utf-8")
    return _js_cache


def _js_fn(name, is_async=False, window=4000):
    js = _js()
    needle = f"{'async ' if is_async else ''}function {name}("
    start = js.find(needle)
    assert start != -1, f"{needle} not found in app.js"
    end = js.find("\nasync function ", start + 1)
    end2 = js.find("\nfunction ", start + 1)
    candidates = [e for e in (end, end2) if e != -1]
    end = min(candidates) if candidates else start + window
    return js[start:end]


class TestFrontendScheduleUI(unittest.TestCase):
    def test_18_diagnostics_html_shows_aggregated_counts_only(self):
        fn = _js_fn("_wsOcScheduleDiagnosticsHtml")
        for key in ("lessons_found", "lesson_records_found", "unique_students_eligible", "cancelled_class_records", "cancelled_enrollments", "records_without_mk_user_id"):
            self.assertIn(key, fn)
        self.assertNotIn("child_display_name", fn)

    def test_19_select_all_is_source_agnostic(self):
        fn = _js_fn("_wsOcImportSelectAllLoaded")
        self.assertNotIn("importSource", fn, "select-all must operate on importResults regardless of which source populated it")
        self.assertIn("_ocState.importResults", fn)

    def test_20_schedule_search_populates_shared_import_results(self):
        fn = _js_fn("_wsOcSearchScheduleCandidates", is_async=True)
        self.assertIn("_ocState.importResults = data.candidates", fn)
        self.assertIn("/api/client/onboarding/candidates/from-schedule", fn)
        add_fn = _js_fn("_wsOcImportDoSend", is_async=True)
        self.assertEqual(add_fn.count("_apiPostRaw("), 1)

    def test_endpoint_route_registered(self):
        server_src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn('"/api/client/onboarding/candidates/from-schedule"', server_src)
        self.assertIn("onboarding_campaign_candidates_from_schedule", server_src)

    def test_source_toggle_present(self):
        fn = _js_fn("_wsOcImportSourceToggleHtml")
        self.assertIn('data-import-source="all"', fn)
        self.assertIn('data-import-source="schedule"', fn)


if __name__ == "__main__":
    unittest.main()
