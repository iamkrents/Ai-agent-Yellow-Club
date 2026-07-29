# payment_automation_reasons.py — v7.1.9
#
# Pure reason-code registry for the Payment Automation Guardian (health runs,
# incidents, safe auto-repair). Pure domain module only: no storage, no
# web_app_server, no MoyKlass/bePaid/Telegram client, no sqlite, no I/O.
#
# Training-state reason codes (client_training_paused, training_subscription_frozen,
# client_training_finished, training_join_status_unknown, training_join_status_ambiguous,
# client_resume_confirmation_required, training_state_unavailable, ...) are NOT
# redefined here — they are imported from training_state_domain (also pure) and
# wrapped with Guardian-specific metadata (component/severity/dedup scope). The
# code STRING VALUES and user-facing messages remain training_state_domain's
# single source of truth; this module never re-authors that text.

from __future__ import annotations

from typing import NamedTuple, Optional

import training_state_domain as _training

# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------

COMPONENT_SCHEDULER = "scheduler"
COMPONENT_MOYKLASS = "moyklass"
COMPONENT_DATABASE = "database"
COMPONENT_TRAINING_STATE = "training_state"
COMPONENT_AUTOMATION = "automation"
COMPONENT_MK_POSTING = "mk_posting"

# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"

# ---------------------------------------------------------------------------
# Incident dedup scope types — mirrors payment_automation_incidents.scope_type
# ---------------------------------------------------------------------------

SCOPE_SYSTEM = "system"
SCOPE_CLIENT = "client"
SCOPE_AUTOMATION_ITEM = "automation_item"
SCOPE_PAYMENT_INTENT = "payment_intent"
SCOPE_MK_INVOICE = "mk_invoice"


class ReasonInfo(NamedTuple):
    code: str
    component: str
    severity: str
    retryable: bool
    safe_auto_repair: bool
    requires_human_action: bool
    user_title_ru: str
    user_message_ru: str
    recovery_condition: str
    dedup_scope_type: str


def _training_reason(code: str, *, component: str, severity: str, retryable: bool,
                      safe_auto_repair: bool, requires_human_action: bool,
                      user_title_ru: str, recovery_condition: str,
                      dedup_scope_type: str) -> ReasonInfo:
    """Wrap an existing training_state_domain reason code with Guardian metadata.

    The message text is taken verbatim from training_state_domain.TRAINING_REASON_MESSAGES
    — never re-authored here, so there is exactly one place that owns training
    wording (see training_reason_message()).
    """
    message = _training.TRAINING_REASON_MESSAGES.get(code, "")
    return ReasonInfo(
        code=code, component=component, severity=severity, retryable=retryable,
        safe_auto_repair=safe_auto_repair, requires_human_action=requires_human_action,
        user_title_ru=user_title_ru, user_message_ru=message,
        recovery_condition=recovery_condition, dedup_scope_type=dedup_scope_type,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REASON_REGISTRY: dict[str, ReasonInfo] = {

    # ── Scheduler / Guardian health ──────────────────────────────────────
    "scheduler_cycle_failed": ReasonInfo(
        code="scheduler_cycle_failed", component=COMPONENT_SCHEDULER,
        severity=SEVERITY_WARNING, retryable=True, safe_auto_repair=True,
        requires_human_action=False,
        user_title_ru="Сбой цикла проверки",
        user_message_ru="Один из циклов автоматической проверки завершился с ошибкой. Agent повторит попытку в следующем цикле.",
        recovery_condition="Следующий цикл завершается со статусом ok.",
        dedup_scope_type=SCOPE_SYSTEM,
    ),
    "scheduler_cycle_delayed": ReasonInfo(
        code="scheduler_cycle_delayed", component=COMPONENT_SCHEDULER,
        severity=SEVERITY_WARNING, retryable=False, safe_auto_repair=False,
        requires_human_action=False,
        user_title_ru="Цикл проверки занял больше времени, чем обычно",
        user_message_ru="Цикл проверки статусов обучения занял больше 10 минут. Следующий цикл начнётся сразу после завершения текущего.",
        recovery_condition="Следующий цикл укладывается в ожидаемую длительность.",
        dedup_scope_type=SCOPE_SYSTEM,
    ),
    "scheduler_cycle_overlapping": ReasonInfo(
        code="scheduler_cycle_overlapping", component=COMPONENT_SCHEDULER,
        severity=SEVERITY_INFO, retryable=False, safe_auto_repair=True,
        requires_human_action=False,
        user_title_ru="Предыдущая проверка ещё выполнялась",
        user_message_ru="Новая проверка не запускалась, потому что предыдущая ещё выполнялась. Это защищает систему от одновременной обработки данных.",
        recovery_condition="Следующий цикл стартует нормально после освобождения lease.",
        dedup_scope_type=SCOPE_SYSTEM,
    ),
    "scheduler_heartbeat_missing": ReasonInfo(
        code="scheduler_heartbeat_missing", component=COMPONENT_SCHEDULER,
        severity=SEVERITY_CRITICAL, retryable=False, safe_auto_repair=False,
        requires_human_action=True,
        user_title_ru="Проверка статусов обучения остановилась",
        user_message_ru="Agent не подавал признаков активности дольше ожидаемого времени. Автоматическая проверка статусов обучения могла остановиться.",
        recovery_condition="Появился новый heartbeat свежее порога.",
        dedup_scope_type=SCOPE_SYSTEM,
    ),

    # ── MoyKlass availability ────────────────────────────────────────────
    "moyklass_unavailable": ReasonInfo(
        code="moyklass_unavailable", component=COMPONENT_MOYKLASS,
        severity=SEVERITY_WARNING, retryable=True, safe_auto_repair=True,
        requires_human_action=False,
        user_title_ru="МойКласс временно недоступен",
        user_message_ru="Не удалось получить данные из МойКласс. Проверка будет повторена автоматически.",
        recovery_condition="Следующий успешный запрос к МойКласс.",
        dedup_scope_type=SCOPE_CLIENT,
    ),
    "moyklass_timeout": ReasonInfo(
        code="moyklass_timeout", component=COMPONENT_MOYKLASS,
        severity=SEVERITY_WARNING, retryable=True, safe_auto_repair=True,
        requires_human_action=False,
        user_title_ru="МойКласс не ответил вовремя",
        user_message_ru="Запрос к МойКласс превысил время ожидания. Проверка будет повторена автоматически.",
        recovery_condition="Следующий успешный запрос к МойКласс.",
        dedup_scope_type=SCOPE_CLIENT,
    ),

    # ── Database ─────────────────────────────────────────────────────────
    "database_unavailable": ReasonInfo(
        code="database_unavailable", component=COMPONENT_DATABASE,
        severity=SEVERITY_CRITICAL, retryable=True, safe_auto_repair=False,
        requires_human_action=True,
        user_title_ru="База данных недоступна",
        user_message_ru="Локальная база данных недоступна для чтения или записи. Требуется проверка администратором.",
        recovery_condition="Следующая успешная операция с базой данных.",
        dedup_scope_type=SCOPE_SYSTEM,
    ),
    "database_write_failed": ReasonInfo(
        code="database_write_failed", component=COMPONENT_DATABASE,
        severity=SEVERITY_WARNING, retryable=True, safe_auto_repair=True,
        requires_human_action=False,
        user_title_ru="Кратковременная ошибка записи в базу",
        user_message_ru="Запись в базу данных временно не удалась (например, база была занята). Agent повторит попытку.",
        recovery_condition="Следующая успешная запись.",
        dedup_scope_type=SCOPE_SYSTEM,
    ),

    # ── Automation / invoice reconciliation ──────────────────────────────
    "parent_link_missing": ReasonInfo(
        code="parent_link_missing", component=COMPONENT_AUTOMATION,
        severity=SEVERITY_WARNING, retryable=False, safe_auto_repair=False,
        requires_human_action=True,
        user_title_ru="Нет привязки родителя",
        user_message_ru="У клиента нет активной привязки родителя (CL-код). Публикация счёта невозможна, пока привязка не появится.",
        recovery_condition="Родитель привязан через CL-код.",
        dedup_scope_type=SCOPE_CLIENT,
    ),
    "subscription_not_found": ReasonInfo(
        code="subscription_not_found", component=COMPONENT_AUTOMATION,
        severity=SEVERITY_WARNING, retryable=False, safe_auto_repair=False,
        requires_human_action=True,
        user_title_ru="Абонемент не найден",
        user_message_ru="Не удалось сопоставить счёт с конкретным абонементом клиента. Требуется проверка клиент-менеджером.",
        recovery_condition="Абонемент найден и однозначно сопоставлен.",
        dedup_scope_type=SCOPE_MK_INVOICE,
    ),
    "multiple_subscriptions_found": ReasonInfo(
        code="multiple_subscriptions_found", component=COMPONENT_AUTOMATION,
        severity=SEVERITY_WARNING, retryable=False, safe_auto_repair=False,
        requires_human_action=True,
        user_title_ru="Найдено несколько подходящих абонементов",
        user_message_ru="У клиента несколько абонементов, подходящих под этот счёт. Требуется ручной выбор клиент-менеджером.",
        recovery_condition="Остался единственный однозначный кандидат.",
        dedup_scope_type=SCOPE_MK_INVOICE,
    ),
    "invoice_not_found": ReasonInfo(
        code="invoice_not_found", component=COMPONENT_AUTOMATION,
        severity=SEVERITY_WARNING, retryable=True, safe_auto_repair=False,
        requires_human_action=False,
        user_title_ru="Счёт МойКласс не найден",
        user_message_ru="Счёт МойКласс, связанный с этой операцией, сейчас не найден. Возможно, он был удалён или изменён.",
        recovery_condition="Счёт снова найден в МойКласс.",
        dedup_scope_type=SCOPE_MK_INVOICE,
    ),
    "duplicate_invoice_detected": ReasonInfo(
        code="duplicate_invoice_detected", component=COMPONENT_AUTOMATION,
        severity=SEVERITY_CRITICAL, retryable=False, safe_auto_repair=False,
        requires_human_action=True,
        user_title_ru="Обнаружен дублирующий счёт",
        user_message_ru="Найдено несколько платёжных черновиков для одного и того же счёта МойКласс. Автоматизация остановлена до ручной проверки.",
        recovery_condition="Дубликат разрешён вручную (лишний intent отменён).",
        dedup_scope_type=SCOPE_MK_INVOICE,
    ),
    "duplicate_intent_detected": ReasonInfo(
        code="duplicate_intent_detected", component=COMPONENT_AUTOMATION,
        severity=SEVERITY_CRITICAL, retryable=False, safe_auto_repair=False,
        requires_human_action=True,
        user_title_ru="Обнаружен дублирующий Payment Intent",
        user_message_ru="Найдено несколько активных Payment Intent для одного счёта. Требуется ручная проверка.",
        recovery_condition="Дубликат разрешён вручную.",
        dedup_scope_type=SCOPE_PAYMENT_INTENT,
    ),

    # ── MK posting ───────────────────────────────────────────────────────
    "paid_not_posted_to_mk": ReasonInfo(
        code="paid_not_posted_to_mk", component=COMPONENT_MK_POSTING,
        severity=SEVERITY_WARNING, retryable=False, safe_auto_repair=False,
        requires_human_action=True,
        user_title_ru="Оплата не внесена в МойКласс",
        user_message_ru="Платёж подтверждён, но ещё не внесён в МойКласс. Используйте существующую ручную операцию внесения оплаты.",
        recovery_condition="mk_posting_status становится posted (mk_payment_id заполнен).",
        dedup_scope_type=SCOPE_PAYMENT_INTENT,
    ),
    "stale_automation_item": ReasonInfo(
        code="stale_automation_item", component=COMPONENT_AUTOMATION,
        severity=SEVERITY_WARNING, retryable=False, safe_auto_repair=False,
        requires_human_action=True,
        user_title_ru="Операция не обновлялась долгое время",
        user_message_ru="Эта операция не менялась продолжительное время и может требовать внимания.",
        recovery_condition="Операция снова обновилась (updated_at свежее).",
        dedup_scope_type=SCOPE_AUTOMATION_ITEM,
    ),
    "inconsistent_local_state": ReasonInfo(
        code="inconsistent_local_state", component=COMPONENT_AUTOMATION,
        severity=SEVERITY_CRITICAL, retryable=False, safe_auto_repair=False,
        requires_human_action=True,
        user_title_ru="Несогласованное состояние",
        user_message_ru="Обнаружено внутреннее противоречие между связанными записями. Требуется проверка администратором.",
        recovery_condition="Противоречие устранено вручную.",
        dedup_scope_type=SCOPE_PAYMENT_INTENT,
    ),
    "manual_review_required": ReasonInfo(
        code="manual_review_required", component=COMPONENT_AUTOMATION,
        severity=SEVERITY_WARNING, retryable=False, safe_auto_repair=False,
        requires_human_action=True,
        user_title_ru="Требуется проверка клиент-менеджера",
        user_message_ru="Эта операция требует ручной проверки клиент-менеджером.",
        recovery_condition="Проверка выполнена, состояние подтверждено.",
        dedup_scope_type=SCOPE_AUTOMATION_ITEM,
    ),

    # ── Training-state codes — reused verbatim from training_state_domain ──
    "client_training_paused": _training_reason(
        _training.REASON_CLIENT_TRAINING_PAUSED, component=COMPONENT_TRAINING_STATE,
        severity=SEVERITY_WARNING, retryable=False, safe_auto_repair=False,
        requires_human_action=True, user_title_ru="Обучение приостановлено",
        recovery_condition="join.statusId снова активен (2) для того же курса.",
        dedup_scope_type=SCOPE_AUTOMATION_ITEM,
    ),
    "training_subscription_frozen": _training_reason(
        _training.REASON_TRAINING_SUBSCRIPTION_FROZEN, component=COMPONENT_TRAINING_STATE,
        severity=SEVERITY_WARNING, retryable=False, safe_auto_repair=False,
        requires_human_action=True, user_title_ru="Абонемент заморожен",
        recovery_condition="userSubscription.statusId больше не 3.",
        dedup_scope_type=SCOPE_AUTOMATION_ITEM,
    ),
    "client_training_finished": _training_reason(
        _training.REASON_CLIENT_TRAINING_FINISHED, component=COMPONENT_TRAINING_STATE,
        severity=SEVERITY_INFO, retryable=False, safe_auto_repair=False,
        requires_human_action=True, user_title_ru="Обучение завершено",
        recovery_condition="Требуется новая активная запись клиент-менеджером.",
        dedup_scope_type=SCOPE_AUTOMATION_ITEM,
    ),
    "training_join_status_unknown": _training_reason(
        _training.REASON_TRAINING_JOIN_STATUS_UNKNOWN, component=COMPONENT_TRAINING_STATE,
        severity=SEVERITY_WARNING, retryable=False, safe_auto_repair=False,
        requires_human_action=True, user_title_ru="Статус обучения не распознан",
        recovery_condition="join.statusId становится одним из известных значений.",
        dedup_scope_type=SCOPE_AUTOMATION_ITEM,
    ),
    "training_join_status_ambiguous": _training_reason(
        _training.REASON_TRAINING_JOIN_STATUS_AMBIGUOUS, component=COMPONENT_TRAINING_STATE,
        severity=SEVERITY_WARNING, retryable=False, safe_auto_repair=False,
        requires_human_action=True, user_title_ru="Найдено несколько записей с разными статусами",
        recovery_condition="Остаётся единственный нормализованный статус для курса.",
        dedup_scope_type=SCOPE_AUTOMATION_ITEM,
    ),
    "training_state_unavailable": _training_reason(
        _training.REASON_TRAINING_STATE_UNAVAILABLE, component=COMPONENT_MOYKLASS,
        severity=SEVERITY_WARNING, retryable=True, safe_auto_repair=True,
        requires_human_action=False, user_title_ru="Не удалось проверить статус обучения",
        recovery_condition="Следующая успешная проверка МойКласс.",
        dedup_scope_type=SCOPE_AUTOMATION_ITEM,
    ),
    "client_resume_confirmation_required": _training_reason(
        _training.REASON_CLIENT_RESUME_CONFIRMATION_REQUIRED, component=COMPONENT_TRAINING_STATE,
        severity=SEVERITY_INFO, retryable=False, safe_auto_repair=False,
        requires_human_action=True, user_title_ru="Можно возобновить",
        recovery_condition="Клиент-менеджер подтвердил возобновление (training-resume).",
        dedup_scope_type=SCOPE_AUTOMATION_ITEM,
    ),
    "training_join_completed": _training_reason(
        _training.REASON_TRAINING_JOIN_COMPLETED, component=COMPONENT_TRAINING_STATE,
        severity=SEVERITY_INFO, retryable=False, safe_auto_repair=False,
        requires_human_action=True, user_title_ru="Запись по этому курсу завершена",
        recovery_condition="Требуется новая активная запись клиент-менеджером.",
        dedup_scope_type=SCOPE_AUTOMATION_ITEM,
    ),
    "training_subscription_not_found": _training_reason(
        _training.REASON_TRAINING_SUBSCRIPTION_NOT_FOUND, component=COMPONENT_TRAINING_STATE,
        severity=SEVERITY_WARNING, retryable=False, safe_auto_repair=False,
        requires_human_action=True, user_title_ru="Абонемент, связанный со счётом, не найден",
        recovery_condition="Абонемент найден и однозначно сопоставлен со счётом.",
        dedup_scope_type=SCOPE_AUTOMATION_ITEM,
    ),
    "training_subscription_class_unknown": _training_reason(
        _training.REASON_TRAINING_SUBSCRIPTION_CLASS_UNKNOWN, component=COMPONENT_TRAINING_STATE,
        severity=SEVERITY_WARNING, retryable=False, safe_auto_repair=False,
        requires_human_action=True, user_title_ru="Не удалось определить группу абонемента",
        recovery_condition="У абонемента появляется mainClassId/classIds.",
        dedup_scope_type=SCOPE_AUTOMATION_ITEM,
    ),
    "training_join_not_found": _training_reason(
        _training.REASON_TRAINING_JOIN_NOT_FOUND, component=COMPONENT_TRAINING_STATE,
        severity=SEVERITY_WARNING, retryable=False, safe_auto_repair=False,
        requires_human_action=True, user_title_ru="Не найдена запись ученика для этого абонемента",
        recovery_condition="Найдена запись (join) с classId, совпадающим с абонементом.",
        dedup_scope_type=SCOPE_AUTOMATION_ITEM,
    ),
    "training_join_status_requires_review": _training_reason(
        _training.REASON_TRAINING_JOIN_STATUS_REVIEW, component=COMPONENT_TRAINING_STATE,
        severity=SEVERITY_WARNING, retryable=False, safe_auto_repair=False,
        requires_human_action=True, user_title_ru="Статус записи требует проверки",
        recovery_condition="join.statusId становится одним из однозначных значений.",
        dedup_scope_type=SCOPE_AUTOMATION_ITEM,
    ),
}


def get_reason_info(code: Optional[str]) -> Optional[ReasonInfo]:
    """Look up metadata for a reason code. Returns None for unknown codes —
    callers must fail closed (manual_review_required-style handling), never
    guess metadata for a code outside the registry."""
    if not code:
        return None
    return REASON_REGISTRY.get(str(code))


def reason_user_message(code: Optional[str]) -> str:
    """Safe user-facing text. Never returns a raw exception or provider code."""
    info = get_reason_info(code)
    if info is None:
        return "Требуется проверка администратором."
    return info.user_message_ru


def reason_user_title(code: Optional[str]) -> str:
    info = get_reason_info(code)
    if info is None:
        return "Неизвестная проблема"
    return info.user_title_ru
