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
from datetime import date, datetime
from typing import Any, Optional

SCHEDULE_CONTINUATION_STATUSES = ("continues", "unconfirmed", "discontinued", "ambiguous")
# v7.1.17.1 — finer-grained sub-classification surfaced ALONGSIDE the coarse
# status above (never replacing it — existing callers/tests/UI rely on the
# coarse vocabulary). Distinguishes "never onboarded at all" from "onboarded,
# just hasn't answered yet", which the coarse "unconfirmed" bucket conflates.
SCHEDULE_CONTINUATION_DETAILS = (
    "continues", "discontinued", "awaiting_confirmation", "status_not_found", "ambiguous_multiple_records",
)
# v7.1.17.1 — availability DATA-COMPLETENESS detail, orthogonal to the MATCH
# quality already captured by SCHEDULE_AVAILABILITY_MATCHES below. Answers
# "did the parent ever get a chance to fill this in", not "does the old slot
# still work for them".
SCHEDULE_AVAILABILITY_DETAILS = (
    "filled", "invited_not_filled", "no_onboarding_record", "parent_not_connected", "ambiguous",
)

# v7.1.17.1 — regularity classifier categories (ALE-6). Only regular_confirmed
# and regular_inferred_high are ever eligible to become a "current" group /
# feed a future auto-generated draft (still gated off entirely by
# SCHEDULE_FOUNDATION_ENABLED / SCHEDULE_DRAFT_MUTATIONS_ENABLED in this
# release — nothing here creates a draft).
SCHEDULE_REGULARITY_CATEGORIES = (
    "regular_confirmed", "regular_inferred_high", "regular_inferred_medium",
    "trial", "makeup", "one_off", "other_group_visitor", "insufficient_evidence", "ambiguous",
)
# Thresholds fixed from real-data sensitivity analysis (ALE-6 audit,
# scenario B of three tested: A=4v/0.70, B=5v/0.75, C=6v/0.80 — all three
# were stable across the same population; B was the agreed middle ground).
# n_regular counts only records that are neither trial (test=true) nor
# makeup (missedLessonRecordId set) — those are excluded per-record before
# this threshold is ever applied, never by discarding the whole pair.
REGULARITY_MIN_VISITS = 5
REGULARITY_MIN_SLOT_RATIO = 0.75
REGULARITY_OTHER_VISITOR_MAX_VISITS = 3
# "Material overlap" between two candidate strong (confirmed/high) groups for
# the same student — both conditions required, agreed explicitly rather than
# inferred from visit-count ratios (which misclassified ordinary mid-year
# group transitions as false positives during the ALE-6 audit).
REGULARITY_OVERLAP_AMBIGUOUS_MIN_DAYS = 14
REGULARITY_OVERLAP_AMBIGUOUS_MIN_VISITS_EACH = 2
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


def slot_regularity_ratio(dated_slots: list[tuple[int, str]]) -> Optional[float]:
    """dated_slots: [(weekday, start_time), ...] — one entry per REGULAR
    (already trial/makeup-excluded) attended lesson for one (student, group)
    pair. Returns the fraction landing on that pair's own single most common
    (weekday, start_time), independent of the group's dominant slot — a
    student can be perfectly regular even if the group's slot moved under
    them. None (not 0.0) when there isn't enough data to say anything —
    callers must not treat "unknown" as "irregular"."""
    if len(dated_slots) < 3:
        return None
    counts = Counter(dated_slots)
    _, top_count = counts.most_common(1)[0]
    return top_count / len(dated_slots)


def classify_group_student_regularity(
    *, n_regular: int, n_trial: int, n_makeup: int,
    group_specific_evidence: bool, slot_ratio: Optional[float],
    is_primary_group_for_student: bool = True,
) -> dict[str, Any]:
    """ALE-6 classifier — pure decision, no I/O. n_regular/n_trial/n_makeup
    are per-record counts for ONE (student, group) pair, already split by
    the caller (schedule_sync.py) using MoyKlass's own per-record markers
    (test=true -> trial, missedLessonRecordId set -> makeup) BEFORE this
    function ever sees them — a single trial/makeup record never drags an
    otherwise-regular pair's whole history into the wrong bucket.

    group_specific_evidence must already be scoped to THIS exact group (a
    userSubscription whose classIds/mainClassId names this classId, or a
    real MoyKlass join row for this exact (userId, classId) with paid
    stats) — never "has a subscription somewhere" in general.

    MEMBERSHIP vs SCHEDULE CONFIDENCE ARE SEPARATE QUESTIONS:
    regular_confirmed means ONLY "there is reliable group-specific
    membership evidence" — it carries NO attendance-count or slot-ratio
    threshold. A confirmed member with zero/weak attendance history is
    still regular_confirmed; whether that pair is additionally usable for
    dominant-slot / foundation inference is a SEPARATE question, answered
    by is_foundation_eligible() below, never by this category alone.
    group_specific_evidence, n_regular and slot_ratio are always returned
    as independent fields — never blended into one score.

    regular_inferred_high / regular_inferred_medium are the OPPOSITE case:
    they only ever apply when group_specific_evidence is False — they are
    attendance-pattern inference used specifically BECAUSE no membership
    evidence exists, never a weaker version of regular_confirmed."""
    if group_specific_evidence:
        # Membership evidence alone is sufficient and final — no attendance
        # threshold gates the category itself (see docstring above).
        return {"category": "regular_confirmed", "membership_evidence": True, "slot_ratio": slot_ratio}

    if n_regular == 0:
        if n_trial > 0 and n_makeup == 0:
            category = "trial"
        elif n_makeup > 0 and n_trial == 0:
            category = "makeup"
        else:
            # both zero, or mixed trial+makeup with no real attendance at
            # all — including the "lesson relation exists but every record
            # is visit=false" case: never silently defaults to "trial".
            category = "insufficient_evidence"
        return {"category": category, "membership_evidence": False, "slot_ratio": None}

    if not is_primary_group_for_student and n_regular <= REGULARITY_OTHER_VISITOR_MAX_VISITS:
        # more specific than the medium-confidence bucket below — a short
        # secondary-group visit history must never become "medium".
        return {"category": "other_group_visitor", "membership_evidence": False, "slot_ratio": slot_ratio}

    if n_regular == 1:
        return {"category": "one_off", "membership_evidence": False, "slot_ratio": slot_ratio}

    if n_regular >= REGULARITY_MIN_VISITS and slot_ratio is not None and slot_ratio >= REGULARITY_MIN_SLOT_RATIO:
        category = "regular_inferred_high"
    else:
        # n_regular in [2, MIN_VISITS) primary/non-visitor, OR n_regular >=
        # MIN_VISITS but the slot pattern is unknown/insufficient (< 0.75).
        category = "regular_inferred_medium"

    return {"category": category, "membership_evidence": False, "slot_ratio": slot_ratio}


def is_foundation_eligible(
    *, category: str, is_current_group: Optional[bool], n_regular: int, slot_ratio: Optional[float],
) -> bool:
    """ALE-6 point 2 — foundation/dominant-slot eligibility is NEVER implied
    by regularity_category alone (a category is a membership/attendance
    classification, not a scheduling readiness signal). A pair is only
    eligible once ALL of the following hold:
    - category is regular_confirmed or regular_inferred_high (never medium
      or below — a confirmed member with weak attendance is real
      membership, but not enough schedule signal to seed a slot from);
    - it is that student's CURRENT group (never an ambiguous or superseded
      one — resolve_current_and_ambiguous_groups already decided this);
    - the attendance pattern itself clears the same visits/ratio bar used
      for regular_inferred_high, regardless of how membership was decided.

    Purely advisory in this release — SCHEDULE_FOUNDATION_ENABLED and
    SCHEDULE_DRAFT_MUTATIONS_ENABLED stay off, so nothing currently acts on
    this value; it exists so the persisted classification already carries
    the right answer once that gate opens, instead of being recomputed
    ad hoc at that point."""
    if category not in ("regular_confirmed", "regular_inferred_high"):
        return False
    if is_current_group is not True:
        return False
    if slot_ratio is None or slot_ratio < REGULARITY_MIN_SLOT_RATIO:
        return False
    if n_regular < REGULARITY_MIN_VISITS:
        return False
    return True


_REGULARITY_STRONG_CATEGORIES = frozenset({"regular_confirmed", "regular_inferred_high"})


def resolve_current_and_ambiguous_groups(candidates: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    """candidates: [{"group_key": <hashable>, "category": str,
    "regular_dates": ["YYYY-MM-DD", ...]}, ...] — every group one student
    was classified into, for ONE snapshot. Only groups already classified
    regular_confirmed/regular_inferred_high ("strong") participate in
    current/ambiguous resolution; every other category passes through with
    is_current=None (not applicable — the concept only means something for
    a student's regular group(s)).

    Never assumes exactly one current group exists (that invariant is
    explicitly rejected — see module docstring/ALE-6 point 6): a student
    with zero strong groups gets nothing marked current; a student with one
    gets it marked current; a student with several resolves per-pair via
    real date-overlap, not by picking a single winner outright.

    "Material overlap" (both required, agreed thresholds — not a guess):
    the two groups' regular-attendance date spans overlap for more than
    REGULARITY_OVERLAP_AMBIGUOUS_MIN_DAYS days, AND each group has at least
    REGULARITY_OVERLAP_AMBIGUOUS_MIN_VISITS_EACH of its own regular visits
    falling inside that overlap window (not just spanning it on paper).
    Such pairs are never auto-resolved — both sides become "ambiguous",
    flagged for manual review, current=False. Non-overlapping strong groups
    are a sequential transition: the one with the latest last-regular-date
    is current=True, earlier ones keep their own confirmed/high category
    but current=False (real history, not silently dropped or merged)."""
    result: dict[Any, dict[str, Any]] = {}
    for c in candidates:
        result[c["group_key"]] = {
            "is_current": None, "final_category": c["category"], "ambiguous_peer_keys": [],
        }

    def _parsed_dates(dates: list[str]) -> list[date]:
        out = []
        for d in dates:
            try:
                out.append(datetime.strptime(str(d)[:10], "%Y-%m-%d").date())
            except (ValueError, TypeError):
                continue
        return out

    strong = []
    for c in candidates:
        if c["category"] not in _REGULARITY_STRONG_CATEGORIES:
            continue
        dates = sorted(_parsed_dates(c.get("regular_dates") or []))
        if not dates:
            continue
        strong.append({"group_key": c["group_key"], "dates": dates, "first": dates[0], "last": dates[-1]})

    if not strong:
        return result

    if len(strong) == 1:
        result[strong[0]["group_key"]]["is_current"] = True
        return result

    ambiguous_keys: set = set()
    ambiguous_peers: dict[Any, set] = {}
    for i in range(len(strong)):
        for j in range(i + 1, len(strong)):
            a, b = strong[i], strong[j]
            overlap_start = max(a["first"], b["first"])
            overlap_end = min(a["last"], b["last"])
            overlap_days = (overlap_end - overlap_start).days + 1 if overlap_end >= overlap_start else 0
            if overlap_days <= REGULARITY_OVERLAP_AMBIGUOUS_MIN_DAYS:
                continue
            a_in_window = sum(1 for d in a["dates"] if overlap_start <= d <= overlap_end)
            b_in_window = sum(1 for d in b["dates"] if overlap_start <= d <= overlap_end)
            if a_in_window >= REGULARITY_OVERLAP_AMBIGUOUS_MIN_VISITS_EACH and b_in_window >= REGULARITY_OVERLAP_AMBIGUOUS_MIN_VISITS_EACH:
                ambiguous_keys.add(a["group_key"])
                ambiguous_keys.add(b["group_key"])
                ambiguous_peers.setdefault(a["group_key"], set()).add(b["group_key"])
                ambiguous_peers.setdefault(b["group_key"], set()).add(a["group_key"])

    for key in ambiguous_keys:
        result[key]["final_category"] = "ambiguous"
        result[key]["is_current"] = False
        result[key]["ambiguous_peer_keys"] = sorted(ambiguous_peers.get(key, set()), key=str)

    non_overlapping = [s for s in strong if s["group_key"] not in ambiguous_keys]
    if non_overlapping:
        current = max(non_overlapping, key=lambda s: s["last"])
        for s in non_overlapping:
            result[s["group_key"]]["is_current"] = (s["group_key"] == current["group_key"])

    return result


def resolve_continuation(status_values: list[str], has_any_recipient: Optional[bool] = None) -> dict:
    """status_values: raw continuation_status values (storage.CONTINUATION_
    STATUSES vocabulary: unknown/continues/undecided/needs_consultation/
    not_continuing) collected from every client_onboarding_recipients row
    found for one mk_user_id (possibly across several campaigns).

    Priority: an explicit refusal (not_continuing) always wins, even if
    another row says continues. Conflicting non-refusal signals (continues
    vs. undecided/needs_consultation from different rows) resolve to
    ambiguous rather than guessing either way. No data at all — or only
    'unknown' — resolves to unconfirmed, never to continues.

    has_any_recipient (v7.1.17.1, additive, optional): whether the caller
    found ANY client_onboarding_recipients row at all for this mk_user_id
    — lets 'detail' distinguish "never onboarded" (status_not_found) from
    "onboarded but hasn't answered yet" (awaiting_confirmation), which the
    coarse 'status' field (kept unchanged for existing callers/tests) can't
    tell apart on its own. Defaults to inferring from status_values when
    not passed, for callers that predate this parameter."""
    non_unknown = {s for s in status_values if s and s != "unknown"}
    if has_any_recipient is None:
        has_any_recipient = bool(status_values)

    if "not_continuing" in non_unknown:
        return {"status": "discontinued", "detail": "discontinued", "reason": "Явно указано: не продолжает обучение"}
    has_continues = "continues" in non_unknown
    has_pending = bool(non_unknown & {"undecided", "needs_consultation"})
    if has_continues and has_pending:
        return {"status": "ambiguous", "detail": "ambiguous_multiple_records", "reason": "Разные записи дают противоречивый статус"}
    if has_continues:
        return {"status": "continues", "detail": "continues", "reason": "Подтверждено родителем/сотрудником"}
    if has_pending:
        return {"status": "unconfirmed", "detail": "awaiting_confirmation", "reason": "Ожидает подтверждения"}
    if not has_any_recipient:
        return {"status": "unconfirmed", "detail": "status_not_found", "reason": "Нет ни одной записи об onboarding для этого ребёнка"}
    return {"status": "unconfirmed", "detail": "awaiting_confirmation", "reason": "Статус продолжения обучения не указан"}


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
    has_any_recipient: Optional[bool] = None,
    has_parent_link: Optional[bool] = None,
) -> dict:
    """Never invents a match: continuation is checked first (an explicit
    refusal or an unconfirmed status short-circuits real interval matching
    — those are signals in their own right, per SCHEDULE_AVAILABILITY_
    MATCHES). Partial time overlap is explicitly never counted as a match.

    has_any_recipient / has_parent_link (v7.1.17.1, additive, optional):
    feed the 'detail' field (SCHEDULE_AVAILABILITY_DETAILS) — a data-
    COMPLETENESS classification orthogonal to 'match' (match quality).
    Never conflates "parent never connected at all" (no client_
    parent_child_links row) with "connected, invited, just hasn't filled
    the form yet" — those were previously indistinguishable, both landing
    in a single 'no_availability' bucket. Omit either to skip that
    distinction (detail falls back to intervals-based filled/invited_not_filled)."""
    detail = "filled" if intervals else "invited_not_filled"
    if has_any_recipient is False:
        detail = "no_onboarding_record"
    elif has_parent_link is False:
        detail = "parent_not_connected"
    if continuation_status == "ambiguous":
        detail = "ambiguous"

    if continuation_status == "discontinued":
        return {"match": "discontinued", "reason": "Не продолжает обучение", "detail": detail}
    if continuation_status in ("unconfirmed", "ambiguous"):
        return {"match": "continuation_unconfirmed", "reason": "Продолжение обучения не подтверждено", "detail": detail}

    if not weekday or not start_time:
        return {"match": "ambiguous_availability", "reason": "Прежний слот группы не определён однозначно", "detail": detail}

    if not intervals:
        return {"match": "no_availability", "reason": "Родитель не заполнил возможности", "detail": detail}

    branch_conflict = (
        preferred_branch not in ("either", "unknown")
        and group_branch_code != "unknown"
        and preferred_branch != group_branch_code
    )
    if branch_conflict:
        return {
            "match": "branch_conflict",
            "reason": f"Предпочтение филиала не совпадает с прежним ({preferred_branch} ≠ {group_branch_code})",
            "detail": detail,
        }

    if planned_start_date and available_from and str(available_from) > str(planned_start_date):
        return {
            "match": "start_date_conflict",
            "reason": f"Готовность с {available_from}, начало занятий {planned_start_date}",
            "detail": detail,
        }

    end_time = add_minutes(start_time, duration_minutes or 60)
    day_intervals = [iv for iv in intervals if int(iv.get("weekday") or 0) == weekday]
    if not day_intervals:
        return {"match": "time_conflict", "reason": "Нет доступности в этот день недели", "detail": detail}

    full_fit = [
        iv for iv in day_intervals
        if str(iv.get("start_time")) <= start_time and end_time <= str(iv.get("end_time"))
    ]
    if full_fit:
        preferred = any(iv.get("preference") == "preferred" for iv in full_fit)
        return {
            "match": "preferred_match" if preferred else "possible_match",
            "reason": "Прежнее время полностью укладывается в возможности",
            "detail": detail,
        }

    partial_fit = any(
        not (str(iv.get("end_time")) <= start_time or str(iv.get("start_time")) >= end_time)
        for iv in day_intervals
    )
    if partial_fit:
        return {"match": "time_conflict", "reason": "Есть только частичное пересечение по времени — это не считается совпадением", "detail": detail}

    return {"match": "time_conflict", "reason": "Нет пересечения по времени в этот день", "detail": detail}


# ── ALE-8 — draft-planning preview (read-only decision layer) ──────────────
# Product model clarified for ALE-8: MoyKlass is historical evidence ONLY
# (who attended which group last year, when, with whom) — it is never
# written to, and its live join/subscription status is never trusted for
# the NEW academic year (a join can be legitimately closed out and that
# means nothing about whether the family is continuing). The actual plan
# for the new year is driven entirely by the client's own continuation +
# Availability answers, layered on top of the historical baseline. This
# module never picks a brand-new slot on its own initiative (rule 6) and
# is allowed to leave a case unresolved rather than guess (rule 9) — that
# is a feature, not a gap, in this first version.
SCHEDULE_PREVIEW_DECISIONS = (
    "keep_historical_slot", "pending_confirmation", "stopped", "needs_reassignment", "manual_review",
)

# Only these two regularity categories may ever become a student's
# historical baseline group for next year's placement — the same category
# gate is_foundation_eligible already uses (never trial/makeup/one_off/
# other_group_visitor/insufficient_evidence/regular_inferred_medium/
# ambiguous). See ALE-8 rule 8.
_PREVIEW_BASELINE_STRONG_CATEGORIES = frozenset({"regular_confirmed", "regular_inferred_high"})

# ── ALE-10 — member composition manual-add candidate grouping ──────────────
# Purely a DISPLAY grouping over the existing, real SCHEDULE_PREVIEW_DECISIONS
# value already computed by build_schedule_draft_preview_decision — never a
# second decision engine, never invents a new status. Maps the same 5 real
# backend decisions onto the 4 buckets the "Добавить ребёнка" picker groups
# candidates into.
SCHEDULE_ADD_CANDIDATE_GROUPS = {
    "keep_historical_slot": "assignable",
    "needs_reassignment": "needs_review",
    "manual_review": "needs_review",
    "pending_confirmation": "pending_confirmation",
    "stopped": "stopped",
}


def candidate_group_for_decision(decision: str) -> str:
    """Buckets a real preview `decision` value into one of the 4 manual-add
    candidate groups (assignable/needs_review/pending_confirmation/stopped).
    Falls back to "needs_review" for any unrecognized value — the safest
    bucket, never silently treated as freely assignable."""
    return SCHEDULE_ADD_CANDIDATE_GROUPS.get(decision, "needs_review")


def select_historical_baseline_group(group_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """group_rows: every schedule_source_group_students row for ONE student
    within ONE snapshot — each needs at least "group_id",
    "regularity_category", "is_current_group" (True/False/None). These
    are expected to already be the FINAL values schedule_sync.py persisted
    (regularity_category is the post-cross-group-resolution category,
    already overridden to 'ambiguous' when resolve_current_and_ambiguous_
    groups found material overlap; is_current_group is already resolved
    there too) — this function never re-derives current/ambiguous status
    itself, it only picks which (if any) of a student's already-classified
    rows is usable as the ONE historical baseline for next year.

    Returns one of:
      {"outcome": "found", "group_id": <id>}
      {"outcome": "ambiguous", "candidate_group_ids": [<id>, ...]}
      {"outcome": "none"}
    """
    ambiguous_rows = [r for r in group_rows if r.get("regularity_category") == "ambiguous"]
    if ambiguous_rows:
        return {"outcome": "ambiguous", "candidate_group_ids": [r["group_id"] for r in ambiguous_rows]}

    current_strong = [
        r for r in group_rows
        if r.get("regularity_category") in _PREVIEW_BASELINE_STRONG_CATEGORIES and r.get("is_current_group") is True
    ]
    if len(current_strong) > 1:
        # ALE-6's own cross-group resolution should never leave more than
        # one non-ambiguous current=True row for the same student — but
        # this never silently picks one if that invariant is ever
        # violated; a human looks at it instead of the algorithm guessing.
        return {"outcome": "ambiguous", "candidate_group_ids": [r["group_id"] for r in current_strong]}
    if len(current_strong) == 1:
        return {"outcome": "found", "group_id": current_strong[0]["group_id"]}
    return {"outcome": "none"}


def build_schedule_draft_preview_decision(
    *, baseline_outcome: str, continuation_status: str, continuation_detail: str,
    availability_match: str, availability_detail: str,
) -> dict[str, Any]:
    """Pure decision combining a student's baseline outcome (select_
    historical_baseline_group), continuation (resolve_continuation) and
    availability match (match_availability) into ONE of
    SCHEDULE_PREVIEW_DECISIONS, plus reason_codes explaining why.

    Priority, per the agreed ALE-8 rules: continuation is checked FIRST
    (rules 1-3) — an explicit stop always wins regardless of historical
    data quality; only once continuation=continues do the historical
    baseline (rules 4/7/8) and Availability (rules 5/6) get consulted.

    continuation_status == "unconfirmed" (no answer yet at all — unknown/
    undecided/needs_consultation, no conflicting records) is a simple
    "waiting on the parent" case: pending_confirmation. continuation_
    status == "ambiguous" (resolve_continuation's own conflicting-records
    case — one onboarding record says continues, another says undecided/
    needs_consultation) is a DIFFERENT case — data already exists and
    disagrees with itself, which needs a human decision rather than more
    waiting, so it goes to manual_review instead (review-gate finding).

    availability_match == "no_availability" (Availability never filled in
    at all, as opposed to filled-but-incompatible) is treated as a safe
    unresolved case (pending_confirmation) — the same bucket as an
    unanswered continuation question, since both mean "waiting on the
    parent for more information", never an automatic placement (rule 9).
    A real conflict once Availability data DOES exist (branch/time/
    start_date) is the only case that becomes needs_reassignment (rule 6).
    """
    if continuation_status == "discontinued":
        return {"decision": "stopped", "reason_codes": [f"continuation_{continuation_detail}"]}

    if continuation_status == "ambiguous":
        return {"decision": "manual_review", "reason_codes": [f"continuation_{continuation_detail}"]}

    if continuation_status == "unconfirmed":
        return {"decision": "pending_confirmation", "reason_codes": [f"continuation_{continuation_detail}"]}

    # continuation_status == "continues" from here on.
    if baseline_outcome == "ambiguous":
        return {"decision": "manual_review", "reason_codes": ["historical_group_ambiguous"]}
    if baseline_outcome == "none":
        return {"decision": "manual_review", "reason_codes": ["no_historical_baseline"]}

    # baseline_outcome == "found"
    if availability_match in ("preferred_match", "possible_match"):
        return {"decision": "keep_historical_slot", "reason_codes": [f"availability_{availability_match}"]}
    if availability_match in ("branch_conflict", "time_conflict", "start_date_conflict"):
        return {"decision": "needs_reassignment", "reason_codes": [f"availability_{availability_match}"]}
    if availability_match == "no_availability":
        return {"decision": "pending_confirmation", "reason_codes": ["availability_not_filled"]}
    # ambiguous_availability (the old group's own slot was never determined
    # with confidence) or any future unrecognized match value — safest
    # bucket, never auto-place a child on unclear historical data.
    return {"decision": "manual_review", "reason_codes": [f"availability_{availability_match}"]}
