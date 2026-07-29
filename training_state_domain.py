# training_state_domain.py — v7.1.8
#
# Pure normalization rules for the MoyKlass "training state" of a student
# (active / paused / finished / unknown), used to gate payment automation
# while a student is on pause/vacation/finished — without blocking any
# already-started or already-paid operation.
#
# Pure module only: no database writes, no HTTP calls, no MoyKlassClient
# imports. Callers are responsible for fetching userSubscriptions/joins
# from MoyKlass and passing them in as plain lists of dicts.

from __future__ import annotations

from typing import Any, Optional

# ---------------------------------------------------------------------------
# Real MoyKlass join-status reference (GET /v1/company/joinStatuses),
# confirmed via read-only production diagnostics (2026-07-28). Values are
# business-confirmed by the owner; do not hardcode names for decisions.
# ---------------------------------------------------------------------------

ACTIVE_JOIN_STATUS_IDS = frozenset({"2"})
PAUSED_JOIN_STATUS_IDS = frozenset({"99046"})
FINISHED_JOIN_STATUS_IDS = frozenset({"1", "4"})
REVIEW_JOIN_STATUS_IDS = frozenset({"5", "49850", "49851"})
FROZEN_SUBSCRIPTION_STATUS_IDS = frozenset({"3"})

# ---------------------------------------------------------------------------
# Normalized states
# ---------------------------------------------------------------------------

STATE_ACTIVE = "active"
STATE_PAUSED = "paused"
STATE_FINISHED = "finished"
STATE_UNKNOWN = "unknown"

# ---------------------------------------------------------------------------
# Reason codes
# ---------------------------------------------------------------------------

REASON_CLIENT_TRAINING_PAUSED = "client_training_paused"
REASON_TRAINING_SUBSCRIPTION_FROZEN = "training_subscription_frozen"
REASON_CLIENT_TRAINING_FINISHED = "client_training_finished"
REASON_TRAINING_JOIN_COMPLETED = "training_join_completed"
REASON_TRAINING_SUBSCRIPTION_NOT_FOUND = "training_subscription_not_found"
REASON_TRAINING_SUBSCRIPTION_CLASS_UNKNOWN = "training_subscription_class_unknown"
REASON_TRAINING_JOIN_NOT_FOUND = "training_join_not_found"
REASON_TRAINING_JOIN_STATUS_REVIEW = "training_join_status_requires_review"
REASON_TRAINING_JOIN_STATUS_UNKNOWN = "training_join_status_unknown"
REASON_TRAINING_JOIN_STATUS_AMBIGUOUS = "training_join_status_ambiguous"
REASON_TRAINING_STATE_UNAVAILABLE = "training_state_unavailable"
REASON_CLIENT_RESUME_CONFIRMATION_REQUIRED = "client_resume_confirmation_required"

# Reason codes that mean "automation is currently blocked because of training
# state" — used by the caller to decide whether a later "active" result is a
# genuine first-time check or a resume that needs manual confirmation.
TRAINING_BLOCKED_REASON_CODES = frozenset({
    REASON_CLIENT_TRAINING_PAUSED,
    REASON_TRAINING_SUBSCRIPTION_FROZEN,
    REASON_CLIENT_TRAINING_FINISHED,
    REASON_TRAINING_JOIN_COMPLETED,
    REASON_TRAINING_SUBSCRIPTION_NOT_FOUND,
    REASON_TRAINING_SUBSCRIPTION_CLASS_UNKNOWN,
    REASON_TRAINING_JOIN_NOT_FOUND,
    REASON_TRAINING_JOIN_STATUS_REVIEW,
    REASON_TRAINING_JOIN_STATUS_UNKNOWN,
    REASON_TRAINING_JOIN_STATUS_AMBIGUOUS,
    REASON_TRAINING_STATE_UNAVAILABLE,
    REASON_CLIENT_RESUME_CONFIRMATION_REQUIRED,
})

# Safe, non-technical user-facing text. Raw reason codes must never be the
# only text shown to a manager.
TRAINING_REASON_MESSAGES: dict[str, str] = {
    REASON_CLIENT_TRAINING_PAUSED: "Обучение приостановлено. Новые оплаты не отправляются до возобновления обучения.",
    REASON_TRAINING_SUBSCRIPTION_FROZEN: "Абонемент заморожен. Создание новой оплаты заблокировано.",
    REASON_CLIENT_TRAINING_FINISHED: "Обучение завершено. Новые оплаты не создаются.",
    REASON_TRAINING_JOIN_COMPLETED: "Обучение по этой записи завершено.",
    REASON_TRAINING_JOIN_STATUS_REVIEW: "Статус записи требует проверки клиент-менеджера.",
    REASON_TRAINING_JOIN_STATUS_AMBIGUOUS: "Найдено несколько записей с разными статусами. Требуется ручная проверка.",
    REASON_TRAINING_JOIN_NOT_FOUND: "Не найдена запись ученика, связанная с этим абонементом.",
    REASON_TRAINING_SUBSCRIPTION_NOT_FOUND: "Не найден абонемент, связанный со счётом.",
    REASON_TRAINING_SUBSCRIPTION_CLASS_UNKNOWN: "Не удалось определить группу абонемента.",
    REASON_TRAINING_JOIN_STATUS_UNKNOWN: "Статус записи ученика не распознан.",
    REASON_TRAINING_STATE_UNAVAILABLE: "Не удалось проверить статус обучения в МойКласс. Автоматическое действие остановлено.",
    REASON_CLIENT_RESUME_CONFIRMATION_REQUIRED: "Статус обучения снова активен. Требуется подтверждение возобновления автоматизации.",
}


def training_reason_message(reason_code: Optional[str]) -> str:
    """Safe user-facing text for a reason_code. Never returns the raw code itself."""
    if not reason_code:
        return ""
    return TRAINING_REASON_MESSAGES.get(
        str(reason_code), "Требуется проверка статуса обучения клиент-менеджером."
    )


def _norm_id(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_join_status(status_id: str) -> tuple[str, Optional[str]]:
    """Map one join.statusId to (state, reason_code)."""
    if status_id in ACTIVE_JOIN_STATUS_IDS:
        return STATE_ACTIVE, None
    if status_id in PAUSED_JOIN_STATUS_IDS:
        return STATE_PAUSED, REASON_CLIENT_TRAINING_PAUSED
    if status_id == "1":
        return STATE_FINISHED, REASON_CLIENT_TRAINING_FINISHED
    if status_id == "4":
        return STATE_FINISHED, REASON_TRAINING_JOIN_COMPLETED
    if status_id in REVIEW_JOIN_STATUS_IDS:
        return STATE_UNKNOWN, REASON_TRAINING_JOIN_STATUS_REVIEW
    return STATE_UNKNOWN, REASON_TRAINING_JOIN_STATUS_UNKNOWN


def _result(
    mk_user_id: str,
    mk_user_subscription_id: str,
    checked_at: str,
    state: str,
    reason_code: Optional[str],
    *,
    subscription_status_id: Optional[str] = None,
    matched_class_ids: Optional[list] = None,
    matched_join_ids: Optional[list] = None,
    matched_join_status_ids: Optional[list] = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "reason_code": reason_code,
        "mk_user_id": mk_user_id,
        "mk_user_subscription_id": mk_user_subscription_id,
        "subscription_status_id": subscription_status_id,
        "matched_class_ids": list(matched_class_ids or []),
        "matched_join_ids": list(matched_join_ids or []),
        "matched_join_status_ids": list(matched_join_status_ids or []),
        "checked_at": checked_at,
    }


def unavailable_training_state_result(
    mk_user_id: Any, mk_user_subscription_id: Any, checked_at: str
) -> dict[str, Any]:
    """Fail-closed result used by the lookup service when MoyKlass is unreachable
    or returns a malformed/unexpected response. Never treat this as 'active'."""
    return _result(
        _norm_id(mk_user_id), _norm_id(mk_user_subscription_id), checked_at,
        STATE_UNKNOWN, REASON_TRAINING_STATE_UNAVAILABLE,
    )


# ---------------------------------------------------------------------------
# v7.1.9 — training-sync candidate selection (Payment Automation Guardian)
# ---------------------------------------------------------------------------

# Payment Intent statuses for which a training-state change can no longer
# affect anything: money has already moved (paid) or the operation is fully
# terminal (posted_to_moyklass/cancelled). Re-checking training state for
# these would be wasted MoyKlass calls with zero effect — webhook/posting
# must never be gated by training state regardless.
TRAINING_SYNC_TERMINAL_INTENT_STATUSES = frozenset({"paid", "posted_to_moyklass", "cancelled"})

# current_stage values that are always excluded from training-sync, regardless
# of any linked Payment Intent.
TRAINING_SYNC_TERMINAL_STAGES = frozenset({"ignored"})


def is_training_sync_candidate(
    *,
    current_stage: Any,
    intent_status: Optional[Any] = None,
    intent_visibility: Optional[Any] = None,
) -> bool:
    """True if an automation item should still be re-checked for training
    state changes by the periodic Guardian sync (or any other caller).

    Never relies on current_stage alone — also considers the linked Payment
    Intent's status/visibility, per the v7.1.9 architecture audit finding
    that stage-only gating created gaps (payment_options_created with
    publish disabled, published-but-unpaid with MK posting disabled were
    silently skipped in the pre-Guardian pipeline).
    """
    stage = str(current_stage or "")
    if stage in TRAINING_SYNC_TERMINAL_STAGES:
        return False
    status = str(intent_status or "")
    if status in TRAINING_SYNC_TERMINAL_INTENT_STATUSES:
        return False
    visibility = str(intent_visibility or "")
    if visibility == "withdrawn":
        return False
    return True


def resolve_training_state(
    mk_user_id: Any,
    mk_user_subscription_id: Any,
    subscriptions: Optional[list],
    joins: Optional[list],
    checked_at: str,
) -> dict[str, Any]:
    """Pure resolver: normalize a client's training state for ONE specific
    userSubscription (identified by mk_user_subscription_id).

    subscriptions/joins are plain lists of dicts as returned by
    MoyKlassClient.get_user_subscriptions()/get_user_joins() (already
    extracted via extract_items — no MoyKlassResult wrapper here).

    Never performs I/O. Never trusts anything the frontend could supply
    directly — mk_user_subscription_id must come from the server's own
    payment_intent/invoice record, not from request bodies.
    """
    uid = _norm_id(mk_user_id)
    sub_id = _norm_id(mk_user_subscription_id)

    if not sub_id:
        return _result(uid, sub_id, checked_at, STATE_UNKNOWN, REASON_TRAINING_SUBSCRIPTION_NOT_FOUND)

    subscription = None
    for s in (subscriptions or []):
        if isinstance(s, dict) and _norm_id(s.get("id")) == sub_id:
            subscription = s
            break

    if subscription is None:
        return _result(uid, sub_id, checked_at, STATE_UNKNOWN, REASON_TRAINING_SUBSCRIPTION_NOT_FOUND)

    subscription_status_id = _norm_id(subscription.get("statusId"))

    # Frozen subscription is a hard block, independent of and prior to join status.
    if subscription_status_id in FROZEN_SUBSCRIPTION_STATUS_IDS:
        return _result(
            uid, sub_id, checked_at, STATE_PAUSED, REASON_TRAINING_SUBSCRIPTION_FROZEN,
            subscription_status_id=subscription_status_id,
        )

    class_ids: list[str] = []
    main_class_id = _norm_id(subscription.get("mainClassId"))
    if main_class_id:
        class_ids.append(main_class_id)
    raw_class_ids = subscription.get("classIds")
    if isinstance(raw_class_ids, list):
        for cid in raw_class_ids:
            cid_s = _norm_id(cid)
            if cid_s and cid_s not in class_ids:
                class_ids.append(cid_s)

    if not class_ids:
        return _result(
            uid, sub_id, checked_at, STATE_UNKNOWN, REASON_TRAINING_SUBSCRIPTION_CLASS_UNKNOWN,
            subscription_status_id=subscription_status_id,
        )

    matched_joins = []
    for j in (joins or []):
        if not isinstance(j, dict):
            continue
        j_class = _norm_id(j.get("classId"))
        if j_class and j_class in class_ids:
            matched_joins.append(j)

    if not matched_joins:
        return _result(
            uid, sub_id, checked_at, STATE_UNKNOWN, REASON_TRAINING_JOIN_NOT_FOUND,
            subscription_status_id=subscription_status_id, matched_class_ids=class_ids,
        )

    matched_join_ids = [_norm_id(j.get("id")) for j in matched_joins]
    matched_join_status_ids = [_norm_id(j.get("statusId")) for j in matched_joins]
    normalized = [_normalize_join_status(sid) for sid in matched_join_status_ids]
    states = {n[0] for n in normalized}

    if len(states) == 1:
        state = next(iter(states))
        reason_code = None if state == STATE_ACTIVE else next(
            (n[1] for n in normalized if n[0] == state), None
        )
        return _result(
            uid, sub_id, checked_at, state, reason_code,
            subscription_status_id=subscription_status_id,
            matched_class_ids=class_ids,
            matched_join_ids=matched_join_ids,
            matched_join_status_ids=matched_join_status_ids,
        )

    # Mixed normalized states among joins matched to THIS subscription's class ids.
    return _result(
        uid, sub_id, checked_at, STATE_UNKNOWN, REASON_TRAINING_JOIN_STATUS_AMBIGUOUS,
        subscription_status_id=subscription_status_id,
        matched_class_ids=class_ids,
        matched_join_ids=matched_join_ids,
        matched_join_status_ids=matched_join_status_ids,
    )
