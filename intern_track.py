"""Intern onboarding track — pure stage logic (стажёр: путь от нуля до преподавателя).

This module has NO external dependencies (no DB, no network), so it can be unit
tested offline and reused by both the web server and the Telegram side. It encodes
the journey from the attached flowchart and the three product decisions:

  1. After the final senior approval the intern becomes a full teacher  -> "active".
  2. Auto-access to the trial stage requires exactly 2 commented observations.
  3. Only senior teachers (role "methodist") review work and grant approval.

Stages (linear, gated):

  observing    teacher_status == "trainee": watch trial lessons, comment on each.
               >= REQUIRED_OBSERVATIONS commented -> auto-advance to "trial_allowed".
  trial_work   trial_allowed, trial work not yet accepted: get materials, submit
               work, senior accepts/rejects with a comment (loop until accepted).
  demo_booking trial_allowed, work accepted, no conducted demo yet: pick a senior's
               free slot and book a demo trial (senior may decline with a comment).
  demo_review  demo conducted, awaiting the senior's final verdict.
  done         intern has been promoted (teacher_status in DONE_STATUSES).
"""

from __future__ import annotations

from typing import Any

# --- Product decisions (single source of truth) ---
REQUIRED_OBSERVATIONS = 2
REVIEWER_ROLES = frozenset({"methodist"})
FINAL_STATUS = "active"

# Statuses that mean the intern has finished the track.
DONE_STATUSES = frozenset({"active", "regular_allowed"})

# Stage identifiers (kept in sync with the frontend stepper).
STAGE_OBSERVING = "observing"
STAGE_TRIAL_WORK = "trial_work"
STAGE_DEMO_BOOKING = "demo_booking"
STAGE_DEMO_REVIEW = "demo_review"
STAGE_DONE = "done"

STAGE_ORDER = [
    STAGE_OBSERVING,
    STAGE_TRIAL_WORK,
    STAGE_DEMO_BOOKING,
    STAGE_DEMO_REVIEW,
    STAGE_DONE,
]

STAGE_TITLES = {
    STAGE_OBSERVING: "Отсмотр занятий",
    STAGE_TRIAL_WORK: "Материалы и работа",
    STAGE_DEMO_BOOKING: "Запись на демо-пробное",
    STAGE_DEMO_REVIEW: "Проверка демо",
    STAGE_DONE: "Допуск получен",
}


def can_review_intern(reviewer_role: str | None) -> bool:
    """Decision 3: only senior teachers may review intern work and grant approval."""
    return str(reviewer_role or "").strip().lower() in REVIEWER_ROLES


def final_status() -> str:
    """Decision 1: status the intern receives after passing the demo."""
    return FINAL_STATUS


def should_unlock_trial(teacher_status: str | None, completed_observations: int) -> bool:
    """Decision 2: a trainee unlocks the trial stage after exactly 2 commented observations."""
    return str(teacher_status or "").strip().lower() == "trainee" and int(completed_observations or 0) >= REQUIRED_OBSERVATIONS


def observation_progress(completed_observations: int) -> dict[str, int]:
    done = max(0, int(completed_observations or 0))
    return {
        "done": done,
        "required": REQUIRED_OBSERVATIONS,
        "remaining": max(0, REQUIRED_OBSERVATIONS - done),
    }


def _work_accepted(work_status: str | None) -> bool:
    return str(work_status or "").strip().lower() == "accepted"


def compute_intern_stage(
    teacher_status: str | None,
    *,
    completed_observations: int = 0,
    work_status: str | None = None,
    demo_status: str | None = None,
) -> dict[str, Any]:
    """Return the current stage and a short next-action hint for the stepper.

    Inputs come straight from storage:
      teacher_status        - teacher_profiles.teacher_status
      completed_observations- Storage.count_intern_completed_observations(...)
      work_status           - latest intern_works.status ('', submitted, accepted, rejected)
      demo_status           - latest intern_demo_bookings.status
                              ('', requested, approved, declined, conducted, passed, failed)
    """
    status = str(teacher_status or "").strip().lower()
    work_status = str(work_status or "").strip().lower()
    demo_status = str(demo_status or "").strip().lower()

    if status in DONE_STATUSES:
        stage = STAGE_DONE
        action = "Стажировка пройдена. Ты теперь преподаватель."
    elif status == "trainee":
        stage = STAGE_OBSERVING
        prog = observation_progress(completed_observations)
        if prog["remaining"] > 0:
            action = f"Отсмотри пробные занятия и оставь комментарий. Осталось: {prog['remaining']} из {prog['required']}."
        else:
            action = "Условие выполнено — открываем доступ к пробному занятию."
    elif status == "trial_allowed":
        if not _work_accepted(work_status):
            stage = STAGE_TRIAL_WORK
            if work_status == "submitted":
                action = "Работа отправлена. Ждём проверки старшего преподавателя."
            elif work_status == "rejected":
                action = "Работа отправлена на доработку. Исправь по комментарию и отправь снова."
            else:
                action = "Изучи материалы пробного занятия, выполни и отправь работу."
        elif demo_status in {"passed"}:
            # Work accepted and demo passed but profile not yet promoted — transient.
            stage = STAGE_DEMO_REVIEW
            action = "Демо принято. Завершаем допуск."
        elif demo_status in {"conducted"}:
            stage = STAGE_DEMO_REVIEW
            action = "Демо проведено. Ждём вердикта старшего преподавателя."
        elif demo_status in {"requested"}:
            stage = STAGE_DEMO_BOOKING
            action = "Заявка на демо отправлена. Ждём подтверждения слота."
        elif demo_status in {"approved"}:
            stage = STAGE_DEMO_BOOKING
            action = "Слот подтверждён. Проведи демо-пробное занятие."
        else:
            # no demo yet, or previous one declined/failed -> book (again)
            stage = STAGE_DEMO_BOOKING
            if demo_status in {"declined", "failed"}:
                action = "Демо не принято. Запишись на новый слот по комментарию старшего."
            else:
                action = "Работа принята. Выбери свободный слот старшего и запишись на демо."
    else:
        # Unknown / not an intern in the onboarding sense.
        stage = STAGE_OBSERVING
        action = "Старт стажировки."

    index = STAGE_ORDER.index(stage)
    return {
        "stage": stage,
        "stageTitle": STAGE_TITLES[stage],
        "stageIndex": index,
        "totalStages": len(STAGE_ORDER) - 1,  # "done" is the terminal marker, not a working step
        "nextAction": action,
        "observations": observation_progress(completed_observations),
        "isDone": stage == STAGE_DONE,
    }
