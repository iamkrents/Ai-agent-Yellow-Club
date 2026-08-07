"""Read-only MoyKlass sync engine for the v7.1.17 "Расписание" module.

Pulls last academic year's classes + lesson attendance records from
MoyKlass, reconstructs each group's regular weekly slot and student
membership via schedule_domain's pure functions, and persists everything
into schedule_source_* tables through a versioned snapshot (storage.py's
schedule methods). Never writes anything back to MoyKlass — every call
used here is a GET.

Runs in a background thread so the triggering HTTP request returns
immediately; progress lives in schedule_source_snapshots (polled by the
frontend), not in memory, so a sync interrupted by a process restart can
be resumed by re-running against the same snapshot_id — every write in
this module is an idempotent upsert (see storage.py's
upsert_schedule_source_group/lesson/group_student), so re-processing
already-written data is always safe.

Expected MoyKlass request volume for one full sync of one academic year:
get_classes() (1 call) + list_lesson_records_between() (1 call, internally
paginated — this project's own prior testing recorded ~12.6k records over
an 11-month window completing in ~38s in that one call) + list_joins_for_
classes() (v7.1.17.1 — a handful of paginated calls, ~9 for this account's
~4.4k total joins; independent of how many groups are being classified,
see moyklass_client.list_joins_for_classes docstring for why it can't be
server-side filtered by classId) + a small, bounded number of
get_class_by_id() calls, one per distinct classId that shows up in lesson
records but is missing from get_classes() (a deleted/renamed group) — 0 in
the common case. Deliberately NOT used: get_class_users() (one GET per
enrolled student, per group — a real N+1 this module avoids entirely for
the bulk sync; membership evidence comes from lesson attendance records
and the bulk joins fetch instead, which already cover any student who had
at least one lesson or one enrollment record in the whole academic year).

v7.1.17.1 (ALE-6) — regularity classifier. Every attended lessonRecord is
classified per-record BEFORE any aggregation: test=true -> trial,
missedLessonRecordId set -> makeup, otherwise -> regular. Only "regular"
records ever increment a (student, group) pair's visit count or feed the
per-pair dominant-slot/regularity-ratio math — a single trial or makeup
record embedded in an otherwise-regular attendance history never drags the
whole pair into the wrong bucket (see schedule_domain.
classify_group_student_regularity). "Membership evidence" is deliberately
scoped to the EXACT group being classified (userSubscription.classIds/
mainClassId naming this classId, or a MoyKlass join row for this exact
(userId, classId) with paid stats) — never "has a subscription somewhere".
When one student has more than one regular_confirmed/regular_inferred_high
group in the same snapshot, schedule_domain.resolve_current_and_ambiguous_
groups decides, per real date-overlap (not visit-count ratios, which
misclassify ordinary mid-year group transitions): non-overlapping groups
resolve to one "current" group with earlier ones kept as real history;
groups with material overlap are never auto-resolved and both become
"ambiguous" for manual review.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from datetime import date
from typing import Any, Callable, Optional

import schedule_domain
from moyklass_client import (
    MoyKlassClient,
    extract_items,
    _v3991_user_name_from_item as _mk_record_user_name,
    _v3991_fetch_user_names as _mk_fetch_user_names,
)

log = logging.getLogger("yellow_club_schedule_sync")

_SYNC_LOCK = threading.Lock()

_CANCELLED_LESSON_STATUSES = frozenset({"3", "cancelled", "отменено", "canceled"})
_RETRYABLE_STATUSES = frozenset({0, 429, 500, 502, 503, 504})
_RETRY_BACKOFF_SECONDS = (1, 3, 8)

STAGE_LABELS_RU = {
    "classes": "Получение групп",
    "lesson_records": "Получение занятий",
    "grouping": "Восстановление слотов и состава",
    "integrity": "Проверка целостности",
    "done": "Активация",
}


def is_sync_running() -> bool:
    return _SYNC_LOCK.locked()


def _call_with_retry(fn: Callable[[], Any], label: str) -> Any:
    last = None
    for attempt, delay in enumerate((0, *_RETRY_BACKOFF_SECONDS)):
        if delay:
            time.sleep(delay)
        result = fn()
        last = result
        if result.ok:
            return result
        if result.status not in _RETRYABLE_STATUSES:
            return result
        log.warning(
            "schedule sync: %s failed (attempt %s/%s, status=%s) — %s",
            label, attempt + 1, len(_RETRY_BACKOFF_SECONDS) + 1, result.status, result.error,
        )
    return last


def _pick_first(d: Optional[dict], keys: tuple[str, ...]) -> Any:
    if not d:
        return None
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "да"}


def _is_makeup_record(rec: dict) -> bool:
    """True when this attended record replaces a specific missed lesson
    elsewhere (real MoyKlass field, confirmed present on 395/11868 records
    in the ALE-6 audit — not a guess)."""
    v = rec.get("missedLessonRecordId")
    return v not in (None, "", 0, "0")


def _record_has_group_specific_subscription(rec: dict, class_id: str) -> bool:
    """True only if THIS record's userSubscription actually names this
    exact classId (via classIds or mainClassId) — confirmed by the ALE-6
    audit that 96.4% of records carrying a userSubscription have their
    lesson.classId inside subscription.classIds, so this is real evidence,
    not "has a subscription somewhere" (courseIds, by contrast, is a huge
    near-company-wide list and must never be used for this check)."""
    sub = rec.get("userSubscription")
    if not isinstance(sub, dict):
        return False
    try:
        cid = int(class_id)
    except (TypeError, ValueError):
        return False
    main_cid = sub.get("mainClassId")
    if main_cid is not None:
        try:
            if int(main_cid) == cid:
                return True
        except (TypeError, ValueError):
            pass
    raw_classids = sub.get("classIds")
    if isinstance(raw_classids, list):
        for x in raw_classids:
            try:
                if int(x) == cid:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _record_date_str(rec: dict, lesson: dict) -> str:
    raw = _pick_first(lesson, ("date", "lessonDate", "startDate")) or _pick_first(rec, ("date", "lessonDate", "createdAt", "created"))
    if not raw:
        return ""
    s = str(raw)
    return s[:10] if len(s) >= 10 else ""


def _duration_minutes(start_time: str, end_time: str) -> Optional[int]:
    if not start_time or not end_time:
        return None
    try:
        sh, sm = (int(p) for p in start_time.split(":")[:2])
        eh, em = (int(p) for p in end_time.split(":")[:2])
        diff = (eh * 60 + em) - (sh * 60 + sm)
        return diff if diff > 0 else None
    except ValueError:
        return None


def start_schedule_sync(
    storage, moyklass: MoyKlassClient, actor_user_id: Optional[int], actor_name: str,
    resume_snapshot_id: Optional[int] = None,
) -> dict[str, Any]:
    """Kicks off a sync in a background thread and returns immediately with
    the snapshot row (status='running'). Only one sync may run at a time
    (in-process lock, checked before any DB write) — a second call while
    one is in flight gets reason_code=sync_already_running rather than
    racing the first."""
    if not moyklass.is_configured:
        return {"ok": False, "error": "МойКласс не настроен (нет API-ключа)", "reason_code": "moyklass_unavailable"}

    if not _SYNC_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "Синхронизация уже выполняется", "reason_code": "sync_already_running"}

    released_early = False
    try:
        if resume_snapshot_id:
            snapshot = storage.get_schedule_snapshot(resume_snapshot_id)
            if not snapshot:
                released_early = True
                return {"ok": False, "error": "Snapshot для повтора не найден", "reason_code": "not_found"}
            if snapshot["status"] not in ("failed", "partial") or not snapshot["resumable"]:
                released_early = True
                return {"ok": False, "error": "Этот snapshot нельзя продолжить", "reason_code": "not_resumable"}
            storage.resume_schedule_sync_snapshot(int(snapshot["id"]))
            snapshot_id = int(snapshot["id"])
        else:
            running = storage.get_running_schedule_snapshot()
            if running:
                released_early = True
                return {"ok": False, "error": "Синхронизация уже выполняется", "reason_code": "sync_already_running"}
            from storage import SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END
            snapshot = storage.create_schedule_sync_snapshot(
                SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, actor_user_id, actor_name,
            )
            snapshot_id = int(snapshot["id"])
    finally:
        if released_early:
            _SYNC_LOCK.release()

    if released_early:
        return {"ok": False, "error": "Не удалось запустить синхронизацию", "reason_code": "start_failed"}

    thread = threading.Thread(target=_run_sync_thread, args=(storage, moyklass, snapshot_id), daemon=True)
    thread.start()
    return {"ok": True, "snapshot": storage.get_schedule_snapshot(snapshot_id)}


def _run_sync_thread(storage, moyklass: MoyKlassClient, snapshot_id: int) -> None:
    try:
        _execute_sync(storage, moyklass, snapshot_id)
    except Exception:
        log.exception("schedule sync crashed for snapshot_id=%s", snapshot_id)
        try:
            storage.log_schedule_sync_error(
                snapshot_id, "crash", "sync", str(snapshot_id), "unhandled_exception",
                "Синхронизация прервана внутренней ошибкой",
            )
            storage.finish_schedule_sync_snapshot(snapshot_id, "failed", activate=False)
        except Exception:
            log.exception("schedule sync: failed to record crash for snapshot_id=%s", snapshot_id)
    finally:
        if _SYNC_LOCK.locked():
            _SYNC_LOCK.release()


def _execute_sync(storage, moyklass: MoyKlassClient, snapshot_id: int) -> None:
    from storage import SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END

    period_start = date.fromisoformat(SCHEDULE_SOURCE_PERIOD_START)
    period_end_inclusive = date.fromisoformat(SCHEDULE_SOURCE_PERIOD_END)

    # ── Stage 1 — classes ────────────────────────────────────────────
    storage.update_schedule_sync_progress(snapshot_id, stage="classes")
    classes_result = _call_with_retry(lambda: moyklass.get_classes("limit=500"), "get_classes")
    if not classes_result.ok:
        storage.log_schedule_sync_error(
            snapshot_id, "classes", "classes", "", str(classes_result.status),
            classes_result.error or "Не удалось получить список групп",
        )
        storage.finish_schedule_sync_snapshot(snapshot_id, "failed", activate=False)
        return
    raw_classes = [c for c in extract_items(classes_result.data) if isinstance(c, dict)]
    classes_by_id: dict[str, dict[str, Any]] = {}
    for c in raw_classes:
        cid = str(_pick_first(c, ("id", "classId")) or "").strip()
        if cid:
            classes_by_id[cid] = c
    storage.update_schedule_sync_progress(snapshot_id, groups_found=len(classes_by_id))

    # ── Stage 2 — lesson records for the whole academic year, one call ─
    storage.update_schedule_sync_progress(snapshot_id, stage="lesson_records")
    records_result = _call_with_retry(
        lambda: moyklass.list_lesson_records_between(period_start, period_end_inclusive),
        "list_lesson_records_between",
    )
    if not records_result.ok:
        storage.log_schedule_sync_error(
            snapshot_id, "lesson_records", "lessonRecords", "", str(records_result.status),
            records_result.error or "Не удалось получить занятия за период",
        )
        storage.finish_schedule_sync_snapshot(snapshot_id, "failed", activate=False)
        return
    all_records = [r for r in extract_items(records_result.data) if isinstance(r, dict)]
    storage.update_schedule_sync_progress(snapshot_id, lessons_fetched=len(all_records))

    # ── Stage 3 — group everything by classId, classifying each attended
    # record as trial/makeup/regular BEFORE any aggregation ─────────────
    storage.update_schedule_sync_progress(snapshot_id, stage="grouping")
    try:
        lookup = moyklass._lookup_maps_cached()
    except Exception:
        lookup = {"filials": {}, "teachers": {}, "classes": {}, "rooms": {}}

    by_class: dict[str, dict[str, Any]] = {}
    for rec in all_records:
        lesson = rec.get("lesson") if isinstance(rec.get("lesson"), dict) else {}
        status = str(_pick_first(lesson, ("status",)) or rec.get("lessonStatus") or "").strip().lower()
        class_id = str(_pick_first(lesson, ("classId", "groupId")) or _pick_first(rec, ("classId", "groupId")) or "").strip()
        if not class_id:
            storage.log_schedule_sync_error(
                snapshot_id, "grouping", "lessonRecord",
                str(_pick_first(rec, ("id", "recordId")) or ""), "missing_class_id",
                "Запись занятия без classId — пропущена",
            )
            continue
        bucket = by_class.setdefault(class_id, {"lessons": {}, "students": {}, "filial_votes": Counter(), "teacher_votes": Counter(), "name_votes": Counter()})

        lesson_id = str(_pick_first(lesson, ("id", "lessonId")) or _pick_first(rec, ("lessonId",)) or "")
        lesson_date = _record_date_str(rec, lesson)
        start_time = str(_pick_first(lesson, ("beginTime", "startTime", "time")) or "").strip()[:5]
        end_time = str(_pick_first(lesson, ("endTime", "finishTime")) or "").strip()[:5]
        duration = _duration_minutes(start_time, end_time)
        filial_id = str(_pick_first(lesson, ("filialId", "branchId")) or "").strip()
        teacher_ids = lesson.get("teacherIds") if isinstance(lesson.get("teacherIds"), list) else None
        teacher_id = str(_pick_first(lesson, ("teacherId",)) or (teacher_ids[0] if teacher_ids else "") or "").strip()
        raw_name = _pick_first(lesson, ("className", "groupName", "groupTitle", "classTitle"))

        if lesson_id and lesson_id not in bucket["lessons"]:
            bucket["lessons"][lesson_id] = {
                "mk_lesson_id": lesson_id, "lesson_date": lesson_date,
                "weekday": schedule_domain.weekday_from_date(lesson_date),
                "start_time": start_time or None, "duration_minutes": duration,
                "status": "cancelled" if status in _CANCELLED_LESSON_STATUSES else "held",
                "teacher_mk_id": teacher_id or None, "branch_name": lookup.get("filials", {}).get(filial_id, ""),
            }
        if filial_id:
            bucket["filial_votes"][filial_id] += 1
        if teacher_id:
            bucket["teacher_votes"][teacher_id] += 1
        if raw_name:
            bucket["name_votes"][str(raw_name)] += 1

        student_id = str(_pick_first(rec, ("userId", "studentId", "clientId", "customerId", "idUser")) or "").strip()
        if not student_id:
            continue
        student_bucket = bucket["students"].setdefault(student_id, {
            "mk_user_id": student_id, "child_display_name": _mk_record_user_name(rec) or "",
            "n_regular": 0, "n_trial": 0, "n_makeup": 0,
            "regular_visits": [],  # [{"date": ..., "start_time": ...}, ...] — regular attended only
            "group_specific_sub_evidence": False,
            "first_seen_date": lesson_date or None, "last_seen_date": lesson_date or None,
        })
        if not student_bucket["child_display_name"]:
            fallback_name = _mk_record_user_name(rec)
            if fallback_name:
                student_bucket["child_display_name"] = fallback_name
        if lesson_date:
            if not student_bucket["first_seen_date"] or lesson_date < student_bucket["first_seen_date"]:
                student_bucket["first_seen_date"] = lesson_date
            if not student_bucket["last_seen_date"] or lesson_date > student_bucket["last_seen_date"]:
                student_bucket["last_seen_date"] = lesson_date

        if not _truthy(rec.get("visit")):
            continue

        is_trial = _truthy(rec.get("test"))
        is_makeup = (not is_trial) and _is_makeup_record(rec)
        if is_trial:
            student_bucket["n_trial"] += 1
        elif is_makeup:
            student_bucket["n_makeup"] += 1
        else:
            student_bucket["n_regular"] += 1
            if lesson_date:
                student_bucket["regular_visits"].append({"date": lesson_date, "start_time": start_time or None})
            if _record_has_group_specific_subscription(rec, class_id):
                student_bucket["group_specific_sub_evidence"] = True

    # ── Membership evidence — bulk joins fetch, one call set for every
    # classId seen above, never one call per group (see moyklass_client.
    # list_joins_for_classes docstring) ─────────────────────────────────
    join_evidence: dict[tuple[str, str], bool] = {}
    class_ids_list = list(by_class.keys())
    if class_ids_list:
        joins_result = _call_with_retry(lambda: moyklass.list_joins_for_classes(class_ids_list), "list_joins_for_classes")
        if joins_result.ok:
            joins = joins_result.data.get("joins") if isinstance(joins_result.data, dict) else None
            for j in (joins or []):
                if not isinstance(j, dict):
                    continue
                uid = str(_pick_first(j, ("userId",)) or "").strip()
                cid = str(_pick_first(j, ("classId",)) or "").strip()
                if not uid or not cid:
                    continue
                stats = j.get("stats") if isinstance(j.get("stats"), dict) else {}
                try:
                    paid_positive = float(stats.get("totalPayed") or 0) > 0
                except (TypeError, ValueError):
                    paid_positive = False
                if paid_positive:
                    join_evidence[(uid, cid)] = True
        else:
            storage.log_schedule_sync_error(
                snapshot_id, "grouping", "joins", "", str(joins_result.status),
                joins_result.error or "Не удалось получить данные о зачислении (joins) — оценка регулярности будет менее точной для этого запуска",
            )

    # ── Bulk name resolution for anyone whose display name never showed up
    # on any raw lessonRecord field (common — those records carry no name
    # field at all in this account's shape) — one batched request set, not
    # one GET per child (see moyklass_client._v3991_fetch_user_names). ──
    missing_name_ids = sorted({
        sid for bucket in by_class.values() for sid, sb in bucket["students"].items() if not sb["child_display_name"]
    })
    if missing_name_ids:
        try:
            names_map = _mk_fetch_user_names(moyklass, missing_name_ids)
        except Exception:
            log.exception("schedule sync: bulk name resolution failed for snapshot_id=%s", snapshot_id)
            names_map = {}
        if names_map:
            for bucket in by_class.values():
                for sid, sb in bucket["students"].items():
                    if not sb["child_display_name"] and names_map.get(sid):
                        sb["child_display_name"] = names_map[sid]

    # ── Write groups/lessons, accumulate per-student cross-group data ──
    groups_processed = 0
    by_student: dict[str, list[dict[str, Any]]] = {}
    for class_id, bucket in by_class.items():
        raw_class = classes_by_id.get(class_id)
        if raw_class:
            class_name = str(_pick_first(raw_class, ("name", "title")) or "") or f"Группа {class_id}"
        elif bucket["name_votes"]:
            class_name = bucket["name_votes"].most_common(1)[0][0]
        else:
            class_name = f"Группа {class_id}"
            storage.log_schedule_sync_error(
                snapshot_id, "grouping", "class", class_id, "class_missing_from_listing",
                "Группа отсутствует в текущем списке МойКласс (закрыта/переименована/удалена) — данные восстановлены только по записям занятий",
            )

        teacher_id = bucket["teacher_votes"].most_common(1)[0][0] if bucket["teacher_votes"] else ""
        filial_id = bucket["filial_votes"].most_common(1)[0][0] if bucket["filial_votes"] else ""
        branch_name = lookup.get("filials", {}).get(filial_id, "")
        teacher_name = lookup.get("teachers", {}).get(teacher_id, "")

        group_id = storage.upsert_schedule_source_group(
            snapshot_id, class_id, name=class_name, branch_name=branch_name, course_name=class_name,
            teacher_mk_id=teacher_id or None, teacher_name=teacher_name or None,
            is_closed=0 if raw_class else 1,
        )
        lessons = list(bucket["lessons"].values())
        for lesson_row in lessons:
            storage.upsert_schedule_source_lesson(
                snapshot_id, group_id, lesson_row["mk_lesson_id"],
                **{k: v for k, v in lesson_row.items() if k != "mk_lesson_id"},
            )
        slot = schedule_domain.detect_dominant_slot(lessons)
        held_dates = sorted(l["lesson_date"] for l in lessons if l["status"] != "cancelled" and l["lesson_date"])

        storage.finalize_schedule_source_group_slot(
            group_id, weekday=slot["weekday"], start_time=slot["start_time"], duration_minutes=slot["duration_minutes"],
            confidence=slot["confidence"], confidence_reason=slot["confidence_reason"],
            lessons_count=len(lessons), lessons_considered=slot["lessons_considered"],
            first_lesson_date=held_dates[0] if held_dates else None, last_lesson_date=held_dates[-1] if held_dates else None,
            students_count=len(bucket["students"]),
        )
        groups_processed += 1
        storage.update_schedule_sync_progress(snapshot_id, groups_processed=groups_processed)

        for student_id, sb in bucket["students"].items():
            group_specific_evidence = bool(sb["group_specific_sub_evidence"]) or bool(join_evidence.get((student_id, class_id)))
            slot_ratio = schedule_domain.slot_regularity_ratio([
                (schedule_domain.weekday_from_date(v["date"]), v["start_time"])
                for v in sb["regular_visits"] if v["date"] and v["start_time"]
            ])
            by_student.setdefault(student_id, []).append({
                "class_id": class_id, "group_id": group_id, "sb": sb,
                "group_specific_evidence": group_specific_evidence, "slot_ratio": slot_ratio,
                "regular_dates": [v["date"] for v in sb["regular_visits"] if v["date"]],
            })

    # ── Stage 3b — cross-group regularity classification per student ───
    students_found = 0
    for student_id, entries in by_student.items():
        primary_entry = max(entries, key=lambda e: (e["sb"]["n_regular"], e["group_specific_evidence"]))
        primary_class_id = primary_entry["class_id"]

        classified: dict[str, dict[str, Any]] = {}
        for e in entries:
            sb = e["sb"]
            classified[e["class_id"]] = schedule_domain.classify_group_student_regularity(
                n_regular=sb["n_regular"], n_trial=sb["n_trial"], n_makeup=sb["n_makeup"],
                group_specific_evidence=e["group_specific_evidence"], slot_ratio=e["slot_ratio"],
                is_primary_group_for_student=(e["class_id"] == primary_class_id),
            )

        candidates = [
            {"group_key": e["class_id"], "category": classified[e["class_id"]]["category"], "regular_dates": e["regular_dates"]}
            for e in entries
        ]
        resolution = schedule_domain.resolve_current_and_ambiguous_groups(candidates)

        for e in entries:
            cid = e["class_id"]
            sb = e["sb"]
            base = classified[cid]
            res = resolution.get(cid, {})
            final_category = res.get("final_category", base["category"])
            is_current = res.get("is_current")
            peer_class_ids = res.get("ambiguous_peer_keys") or []
            evidence = "attendance" if sb["n_regular"] > 0 else "membership"
            confidence = "high" if sb["n_regular"] >= 4 else ("medium" if sb["n_regular"] > 0 else "low")
            storage.upsert_schedule_source_group_student(
                snapshot_id, e["group_id"], student_id,
                child_display_name=sb["child_display_name"], lessons_attended=sb["n_regular"],
                first_seen_date=sb["first_seen_date"], last_seen_date=sb["last_seen_date"],
                evidence_source=evidence, confidence=confidence,
                n_trial_visits=sb["n_trial"], n_makeup_visits=sb["n_makeup"],
                regularity_category=final_category,
                membership_evidence=1 if e["group_specific_evidence"] else 0,
                slot_regularity_ratio=base["slot_ratio"],
                is_current_group=(1 if is_current is True else (0 if is_current is False else None)),
                ambiguous_peer_group_ids=",".join(peer_class_ids),
            )
            students_found += 1
        storage.update_schedule_sync_progress(snapshot_id, students_found=students_found)

    # ── Stage 4 — integrity check (nothing to repair, just observe) ────
    storage.update_schedule_sync_progress(snapshot_id, stage="integrity")
    snapshot = storage.get_schedule_snapshot(snapshot_id)
    had_errors = int(snapshot["errors_count"] or 0) > 0

    # ── Stage 5 — activation (only on a fully clean run) ────────────────
    storage.update_schedule_sync_progress(snapshot_id, stage="done")
    storage.finish_schedule_sync_snapshot(snapshot_id, "partial" if had_errors else "completed", activate=not had_errors)
