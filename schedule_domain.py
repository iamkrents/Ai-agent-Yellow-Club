"""Pure decision logic for the v7.1.17 "Расписание" module — continuing-
students schedule foundation.

No I/O, no MoyKlass calls, no SQL: every function here takes already-
fetched plain dicts/lists and returns a decision. This mirrors the role
payment_domain.py plays for the payments module — keeping slot detection,
continuation resolution, and availability matching independently unit-
testable, and trivially removable if the schedule module itself is ever
deleted (nothing outside schedule_sync.py / storage.py's schedule methods /
web_app_server.py's schedule routes imports this module).
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Optional

SCHEDULE_CONTINUATION_STATUSES = ("continues", "unconfirmed", "discontinued", "ambiguous")
SCHEDULE_AVAILABILITY_MATCHES = (
    "preferred_match", "possible_match", "branch_conflict", "time_conflict",
    "start_date_conflict", "no_availability", "ambiguous_availability",
    "continuation_unconfirmed", "discontinued",
)
SCHEDULE_WEEKDAY_NAMES_RU = {
    1: "понедельник", 2: "вторник", 3: "среда", 4: "четверг",
    5: "пятница", 6: "суббота", 7: "воскресенье",
}
SCHEDULE_MATCH_LABELS_RU = {
    "preferred_match": "Прежний слот подходит (предпочтительно)",
    "possible_match": "Прежний слот подходит (возможно)",
    "branch_conflict": "Конфликт по филиалу",
    "time_conflict": "Конфликт по времени",
    "start_date_conflict": "Конфликт по дате начала",
    "no_availability": "Возможности не заполнены",
    "ambiguous_availability": "Неоднозначные данные",
    "continuation_unconfirmed": "Продолжение не подтверждено",
    "discontinued": "Прекратил(а) обучение",
}

LESSON_STATUSES_EXCLUDED_FROM_SLOT = frozenset({
    "cancelled", "canceled", "moved", "postponed", "deleted",
})

# A slot change mid-year is only treated as "the new stable slot" (rather
# than noise / a one-off reschedule) once it has held for this many
# consecutive lessons immediately before the end of the source period —
# see detect_dominant_slot.
RECENT_STABLE_MIN_RUN = 4


def weekday_from_date(date_str: Optional[str]) -> Optional[int]:
    """ISO weekday (Mon=1..Sun=7) for a 'YYYY-MM-DD' string. Calendar dates
    carry no timezone ambiguity by themselves — converting a raw MoyKlass
    timestamp to Europe/Minsk local date is the caller's job, before the
    date string ever reaches here."""
    try:
        y, m, d = (int(p) for p in str(date_str)[:10].split("-"))
        return date(y, m, d).isoweekday()
    except (ValueError, TypeError):
        return None


def add_minutes(hhmm: str, minutes: int) -> str:
    h, m = (int(p) for p in str(hhmm).split(":")[:2])
    total = h * 60 + m + int(minutes or 0)
    total %= 24 * 60
    return f"{total // 60:02d}:{total % 60:02d}"


def times_overlap(
    weekday_a: Optional[int], start_a: Optional[str], duration_a: Optional[int],
    weekday_b: Optional[int], start_b: Optional[str], duration_b: Optional[int],
) -> bool:
    if not weekday_a or not weekday_b or weekday_a != weekday_b or not start_a or not start_b:
        return False
    end_a = add_minutes(start_a, duration_a or 60)
    end_b = add_minutes(start_b, duration_b or 60)
    return start_a < end_b and start_b < end_a


def branch_code_from_name(raw_name: Optional[str], yc1_hint: str, yc2_hint: str) -> str:
    """Map a raw MoyKlass filial name to the YC1/YC2 vocabulary already used
    by SCHEDULE_PREFERRED_BRANCHES, using the same configured address hints
    (settings.food_location_yc1/yc2) already used elsewhere in this codebase
    to recognize the two real branches. Never guesses a branch from an
    unmapped name — returns 'unknown' rather than fabricating a match."""
    raw = str(raw_name or "").strip().lower()
    if not raw:
        return "unknown"
    if yc1_hint and yc1_hint.strip().lower() in raw:
        return "YC1"
    if yc2_hint and yc2_hint.strip().lower() in raw:
        return "YC2"
    return "unknown"


def detect_dominant_slot(lessons: list[dict]) -> dict:
    """lessons: [{"lesson_date": "YYYY-MM-DD", "weekday": int|None,
    "start_time": "HH:MM", "duration_minutes": int|None, "status": str}].

    Cancelled lessons are excluded outright. The remaining lessons are
    grouped by (weekday, start_time); the most frequent combination wins —
    a lone reschedule or a single extra lesson essentially never becomes
    the single most frequent combination, so it never wins by construction.

    If the group's slot changed once during the year and the new slot held
    for RECENT_STABLE_MIN_RUN+ consecutive lessons right up to the end of
    the source period, that later slot is preferred over the historically
    more frequent one (business priority: reflect the "last stable spring
    2026" pattern, since that's what's actually continuing).
    """
    considered = []
    for lesson in lessons:
        status = str(lesson.get("status") or "").strip().lower()
        if status in LESSON_STATUSES_EXCLUDED_FROM_SLOT:
            continue
        lesson_date = str(lesson.get("lesson_date") or "").strip()
        wd = lesson.get("weekday") or weekday_from_date(lesson_date)
        start_time = str(lesson.get("start_time") or "").strip()
        if not wd or not start_time or not lesson_date:
            continue
        considered.append({
            "weekday": int(wd), "start_time": start_time, "date": lesson_date,
            "duration_minutes": lesson.get("duration_minutes"),
        })

    total = len(considered)
    if total == 0:
        return {
            "weekday": None, "start_time": None, "duration_minutes": None,
            "confidence": "ambiguous",
            "confidence_reason": "Нет учитываемых занятий за период",
            "lessons_considered": 0,
        }

    considered.sort(key=lambda c: c["date"])
    combo_counter: Counter = Counter((c["weekday"], c["start_time"]) for c in considered)
    ranked = combo_counter.most_common()
    (top_wd, top_st), top_count = ranked[0]
    second_count = ranked[1][1] if len(ranked) > 1 else 0

    last_combo = (considered[-1]["weekday"], considered[-1]["start_time"])
    run_length = 0
    for c in reversed(considered):
        if (c["weekday"], c["start_time"]) == last_combo:
            run_length += 1
        else:
            break

    chosen = (top_wd, top_st)
    changed_to_recent = False
    if last_combo != (top_wd, top_st) and run_length >= RECENT_STABLE_MIN_RUN:
        chosen = last_combo
        changed_to_recent = True

    chosen_wd, chosen_st = chosen
    durations = [
        c["duration_minutes"] for c in considered
        if (c["weekday"], c["start_time"]) == chosen and c["duration_minutes"]
    ]
    duration = Counter(durations).most_common(1)[0][0] if durations else None
    chosen_count = combo_counter[chosen]
    share = chosen_count / total

    if not changed_to_recent and len(ranked) > 1 and top_count == second_count:
        confidence = "ambiguous"
        reason = f"Два одинаково частых слота ({top_count} занятий каждый) — нужна проверка вручную"
    elif total < 4:
        confidence = "low"
        reason = f"Недостаточно занятий за период ({total}) для уверенного вывода"
    elif changed_to_recent:
        confidence = "medium"
        reason = (
            f"Слот изменился в течение года — учтён последний стабильный "
            f"({run_length} занятий подряд перед окончанием периода)"
        )
    elif share >= 0.75 and chosen_count >= 8:
        wd_name = SCHEDULE_WEEKDAY_NAMES_RU.get(chosen_wd, str(chosen_wd))
        confidence = "high"
        reason = f"{chosen_count} из {total} занятий — {wd_name} {chosen_st}"
    elif share >= 0.5:
        confidence = "medium"
        reason = f"{chosen_count} из {total} занятий на основном слоте, есть отклонения"
    else:
        confidence = "low"
        reason = f"Основной слот встречается только в {chosen_count} из {total} занятий"

    return {
        "weekday": chosen_wd, "start_time": chosen_st, "duration_minutes": duration,
        "confidence": confidence, "confidence_reason": reason,
        "lessons_considered": total,
    }


def resolve_continuation(status_values: list[str]) -> dict:
    """status_values: raw continuation_status values (storage.CONTINUATION_
    STATUSES vocabulary: unknown/continues/undecided/needs_consultation/
    not_continuing) collected from every client_onboarding_recipients row
    found for one mk_user_id (possibly across several campaigns).

    Priority: an explicit refusal (not_continuing) always wins, even if
    another row says continues. Conflicting non-refusal signals (continues
    vs. undecided/needs_consultation from different rows) resolve to
    ambiguous rather than guessing either way. No data at all — or only
    'unknown' — resolves to unconfirmed, never to continues."""
    non_unknown = {s for s in status_values if s and s != "unknown"}
    if "not_continuing" in non_unknown:
        return {"status": "discontinued", "reason": "Явно указано: не продолжает обучение"}
    has_continues = "continues" in non_unknown
    has_pending = bool(non_unknown & {"undecided", "needs_consultation"})
    if has_continues and has_pending:
        return {"status": "ambiguous", "reason": "Разные записи дают противоречивый статус"}
    if has_continues:
        return {"status": "continues", "reason": "Подтверждено родителем/сотрудником"}
    if has_pending:
        return {"status": "unconfirmed", "reason": "Ожидает подтверждения"}
    return {"status": "unconfirmed", "reason": "Статус продолжения обучения не указан"}


def match_availability(
    continuation_status: str,
    weekday: Optional[int],
    start_time: Optional[str],
    duration_minutes: Optional[int],
    group_branch_code: str,
    intervals: list[dict],
    preferred_branch: str,
    available_from: Optional[str] = None,
    planned_start_date: Optional[str] = None,
) -> dict:
    """Never invents a match: continuation is checked first (an explicit
    refusal or an unconfirmed status short-circuits real interval matching
    — those are signals in their own right, per SCHEDULE_AVAILABILITY_
    MATCHES). Partial time overlap is explicitly never counted as a match."""
    if continuation_status == "discontinued":
        return {"match": "discontinued", "reason": "Не продолжает обучение"}
    if continuation_status in ("unconfirmed", "ambiguous"):
        return {"match": "continuation_unconfirmed", "reason": "Продолжение обучения не подтверждено"}

    if not weekday or not start_time:
        return {"match": "ambiguous_availability", "reason": "Прежний слот группы не определён однозначно"}

    if not intervals:
        return {"match": "no_availability", "reason": "Родитель не заполнил возможности"}

    branch_conflict = (
        preferred_branch not in ("either", "unknown")
        and group_branch_code != "unknown"
        and preferred_branch != group_branch_code
    )
    if branch_conflict:
        return {
            "match": "branch_conflict",
            "reason": f"Предпочтение филиала не совпадает с прежним ({preferred_branch} ≠ {group_branch_code})",
        }

    if planned_start_date and available_from and str(available_from) > str(planned_start_date):
        return {
            "match": "start_date_conflict",
            "reason": f"Готовность с {available_from}, начало занятий {planned_start_date}",
        }

    end_time = add_minutes(start_time, duration_minutes or 60)
    day_intervals = [iv for iv in intervals if int(iv.get("weekday") or 0) == weekday]
    if not day_intervals:
        return {"match": "time_conflict", "reason": "Нет доступности в этот день недели"}

    full_fit = [
        iv for iv in day_intervals
        if str(iv.get("start_time")) <= start_time and end_time <= str(iv.get("end_time"))
    ]
    if full_fit:
        preferred = any(iv.get("preference") == "preferred" for iv in full_fit)
        return {
            "match": "preferred_match" if preferred else "possible_match",
            "reason": "Прежнее время полностью укладывается в возможности",
        }

    partial_fit = any(
        not (str(iv.get("end_time")) <= start_time or str(iv.get("start_time")) >= end_time)
        for iv in day_intervals
    )
    if partial_fit:
        return {"match": "time_conflict", "reason": "Есть только частичное пересечение по времени — это не считается совпадением"}

    return {"match": "time_conflict", "reason": "Нет пересечения по времени в этот день"}
