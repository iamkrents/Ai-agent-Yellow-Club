# payment_domain.py — Canonical payment domain rules for Yellow Club
# v7.1.0
#
# Pure constants and functions only.
# No database writes, no external API calls.
# Import from here; do not redefine these values elsewhere.

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Timezone: Europe/Minsk = UTC+3, no DST since 2011
# ---------------------------------------------------------------------------

_MINSK_TZ = datetime.timezone(datetime.timedelta(hours=3))

# ---------------------------------------------------------------------------
# Status sets
# ---------------------------------------------------------------------------

PAYMENT_INTENT_ACTIVE_STATUSES: frozenset[str] = frozenset({
    "draft",
    "ready",
    "bepaid_creating",
    "bepaid_created",
    "awaiting_payment",
    "partial_ready",
    "paid",
    "posted_to_moyklass",
    "bepaid_requires_check",
})

PAYMENT_INTENT_PAID_STATUSES: frozenset[str] = frozenset({
    "paid",
    "posted_to_moyklass",
})

PAYMENT_INTENT_FINAL_STATUSES: frozenset[str] = frozenset({
    "paid",
    "posted_to_moyklass",
    "cancelled",
    "error",
})

PAYMENT_INTENT_CANCELLED_STATUSES: frozenset[str] = frozenset({
    "cancelled",
    "error",
})

# ---------------------------------------------------------------------------
# Source sets
# ---------------------------------------------------------------------------

MOYKLASS_INVOICE_INTENT_SOURCES: frozenset[str] = frozenset({
    "moyklass_invoice",
    "moyklass_invoice_automation",
})

# ---------------------------------------------------------------------------
# Channel constants
# ---------------------------------------------------------------------------

PAYMENT_CHANNEL_ERIP = "erip"
PAYMENT_CHANNEL_ACQUIRING = "acquiring"
_VALID_CHANNELS: frozenset[str] = frozenset({PAYMENT_CHANNEL_ERIP, PAYMENT_CHANNEL_ACQUIRING})

# Payment method is the initially requested method (set at creation time).
# paid_channel is the factual channel confirmed by the payment provider webhook.
# They may differ: payment_method=erip + paid_channel=acquiring is valid
# (e.g. ycpi_202607_19: erip intent, acquiring webhook).
PAYMENT_METHOD_ERIP = "erip"
PAYMENT_METHOD_ACQUIRING = "acquiring"

# ---------------------------------------------------------------------------
# Pure predicate functions
# ---------------------------------------------------------------------------

def is_moyklass_invoice_intent(intent: dict) -> bool:
    """True if this intent is backed by a MoyKlass invoice (any canonical source).

    manual and manual_input are NOT considered invoice intents.
    """
    return intent.get("source") in MOYKLASS_INVOICE_INTENT_SOURCES


def is_payment_verified(intent: dict) -> bool:
    """True if payment is confirmed: status in paid set, webhook verified, tx uid present."""
    return (
        str(intent.get("status") or "") in PAYMENT_INTENT_PAID_STATUSES
        and bool(intent.get("webhook_verified"))
        and bool(str(intent.get("paid_transaction_uid") or "").strip())
    )


def is_posted_to_moyklass(intent: dict) -> bool:
    """True if payment has been successfully recorded in MoyKlass (all three guards pass)."""
    return (
        str(intent.get("status") or "") == "posted_to_moyklass"
        and bool(intent.get("mk_payment_id"))
        and str(intent.get("mk_posting_status") or "") == "posted"
    )


def is_cancelled_intent(intent: dict) -> bool:
    """True if intent is in a terminal cancelled/error state."""
    return str(intent.get("status") or "") in PAYMENT_INTENT_CANCELLED_STATUSES


def is_active_intent(intent: dict) -> bool:
    """True if intent is in any active (non-cancelled) status."""
    return str(intent.get("status") or "") in PAYMENT_INTENT_ACTIVE_STATUSES


# ---------------------------------------------------------------------------
# Channel resolution
# ---------------------------------------------------------------------------

def resolve_effective_payment_channel(
    intent: dict,
    payment_options: list | None = None,  # reserved, not yet used
) -> str:
    """Return the factual payment channel for an intent.

    Priority order:
    1. paid_channel — set by webhook/acquiring confirmation; this is reality.
    2. payment_method — the initially requested channel (creation-time intent).
    3. Default: erip (safe fallback).

    For ycpi_202607_19: payment_method=erip, paid_channel=acquiring → returns 'acquiring'
    because paid_channel reflects what actually happened.
    """
    raw = str(intent.get("paid_channel") or "").strip().lower()
    if raw in _VALID_CHANNELS:
        return raw
    pm = str(intent.get("payment_method") or "").strip().lower()
    if pm in _VALID_CHANNELS:
        return pm
    return PAYMENT_CHANNEL_ERIP


# ---------------------------------------------------------------------------
# Guard predicates (pure — no DB, no API)
# ---------------------------------------------------------------------------

def can_create_payment_options(intent: dict) -> bool:
    """True if it is safe to attempt creating bePaid payment options.

    Blocked for: paid, posted_to_moyklass, cancelled, error.
    """
    return str(intent.get("status") or "") not in (
        PAYMENT_INTENT_PAID_STATUSES | PAYMENT_INTENT_CANCELLED_STATUSES
    )


def can_publish_to_parent(intent: dict) -> bool:
    """True if the intent can be shown to the parent (not cancelled/error)."""
    return not is_cancelled_intent(intent)


def can_post_to_moyklass(intent: dict) -> bool:
    """Local guard: can this intent proceed to a MoyKlass posting attempt?

    Does NOT check live MK API or payment type validity — those belong in the
    readiness endpoint. This is the fast pre-flight check.
    """
    if str(intent.get("status") or "") != "paid":
        return False
    if intent.get("mk_payment_id"):
        return False
    mk_ps = str(intent.get("mk_posting_status") or "")
    if mk_ps in ("claiming", "ambiguous", "posted"):
        return False
    return True


# ---------------------------------------------------------------------------
# Idempotency key builders
# ---------------------------------------------------------------------------

def build_invoice_deduplication_key(intent: dict) -> str | None:
    """Canonical dedup key: one active intent per MK invoice.

    Returns None if intent is not backed by a MK invoice or lacks mk_invoice_id.
    """
    if not is_moyklass_invoice_intent(intent):
        return None
    mk_id = str(intent.get("mk_invoice_id") or "").strip()
    if not mk_id:
        return None
    return f"mk_invoice:{mk_id}"


def build_posting_idempotency_key(intent: dict) -> str | None:
    """Canonical idempotency key for MoyKlass posting.

    Combines public_id + paid_transaction_uid.
    Returns None if any required field is missing.
    """
    pid = str(intent.get("public_id") or "").strip()
    tx_uid = str(intent.get("paid_transaction_uid") or "").strip()
    if not pid or not tx_uid:
        return None
    return f"post:{pid}:{tx_uid}"


# ---------------------------------------------------------------------------
# Integrity predicates (for audit)
# ---------------------------------------------------------------------------

def is_source_reference_valid(intent: dict) -> bool:
    """For moyklass_invoice_automation intents: source_reference must match mk_invoice_id.

    Returns True for non-automation intents (check is N/A).
    """
    if intent.get("source") != "moyklass_invoice_automation":
        return True
    mk_id = str(intent.get("mk_invoice_id") or "").strip()
    expected = f"mk_invoice_{mk_id}" if mk_id else ""
    actual = str(intent.get("source_reference") or "").strip()
    return bool(mk_id) and actual == expected


# ---------------------------------------------------------------------------
# Due date / due status (v7.0.99.0)
# ---------------------------------------------------------------------------

DUE_SOURCE_MOYKLASS = "moyklass"
DUE_SOURCE_FALLBACK = "fallback"
DUE_SOURCE_MISSING = "missing"

DUE_STATUS_UPCOMING = "upcoming"
DUE_STATUS_DUE_TODAY = "due_today"
DUE_STATUS_OVERDUE = "overdue"
DUE_STATUS_PAID = "paid"
DUE_STATUS_WITHDRAWN = "withdrawn"


def _parse_iso(s: str) -> Optional[datetime.datetime]:
    """Parse ISO 8601 datetime string with optional timezone."""
    if not s:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def normalize_due_at(
    pay_until_raw: Optional[str],
    created_at_iso: Optional[str],
    default_due_days: int = 14,
) -> Tuple[Optional[str], str]:
    """Compute canonical due_at (ISO 8601, Europe/Minsk) and its source label.

    Rules:
    1. payUntil from MoyKlass present → parse; if date-only, use 23:59:59 Minsk.
    2. No payUntil but created_at present → created_at + default_due_days at 23:59:59 Minsk.
    3. Neither → (None, "missing").

    Returns (due_at_iso_str, source) where source ∈ {"moyklass","fallback","missing"}.
    Due_at_iso_str uses +03:00 offset.
    """
    raw = str(pay_until_raw or "").strip()

    if raw:
        # Date-only (YYYY-MM-DD or YYYY-MM)?
        if len(raw) <= 10 and "T" not in raw:
            try:
                parts = raw[:10].split("-")
                year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                dt = datetime.datetime(year, month, day, 23, 59, 59, tzinfo=_MINSK_TZ)
                return dt.isoformat(), DUE_SOURCE_MOYKLASS
            except (IndexError, ValueError):
                pass
        # Full datetime
        dt = _parse_iso(raw)
        if dt is not None:
            dt_minsk = dt.astimezone(_MINSK_TZ)
            return dt_minsk.isoformat(), DUE_SOURCE_MOYKLASS

    # Fallback: created_at + default_due_days
    if created_at_iso:
        created = _parse_iso(str(created_at_iso))
        if created:
            fallback_dt = (created + datetime.timedelta(days=default_due_days)).astimezone(_MINSK_TZ)
            fallback_dt = fallback_dt.replace(hour=23, minute=59, second=59, microsecond=0)
            return fallback_dt.isoformat(), DUE_SOURCE_FALLBACK

    return None, DUE_SOURCE_MISSING


def compute_due_status(
    due_at_iso: Optional[str],
    client_visibility: Optional[str],
    status: Optional[str],
    now_utc: Optional[datetime.datetime] = None,
) -> str:
    """Compute the due_status for a payment intent.

    Terminal states (paid, withdrawn) take precedence over date-based status.
    An overdue intent is still open for payment — due_status does NOT block it.
    """
    if str(client_visibility or "") == "withdrawn":
        return DUE_STATUS_WITHDRAWN
    if str(status or "") in ("paid", "posted_to_moyklass"):
        return DUE_STATUS_PAID

    if not due_at_iso:
        return DUE_STATUS_UPCOMING

    if now_utc is None:
        now_utc = datetime.datetime.now(datetime.timezone.utc)

    due = _parse_iso(str(due_at_iso))
    if due is None:
        return DUE_STATUS_UPCOMING

    now_minsk = now_utc.astimezone(_MINSK_TZ)
    due_minsk = due.astimezone(_MINSK_TZ)

    if now_minsk.date() > due_minsk.date():
        return DUE_STATUS_OVERDUE
    if now_minsk.date() == due_minsk.date():
        return DUE_STATUS_DUE_TODAY
    return DUE_STATUS_UPCOMING


def due_at_for_bepaid(ttl_hours: int, now_utc: Optional[datetime.datetime] = None) -> str:
    """Compute explicit expired_at for bePaid ERIP/checkout in ISO 8601 UTC."""
    if now_utc is None:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
    expires = now_utc + datetime.timedelta(hours=int(ttl_hours))
    return expires.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Payment terms and pricing resolution (v7.1.0)
# ---------------------------------------------------------------------------

PRICING_SOURCE_ONE_TIME = "one_time"
PRICING_SOURCE_DATE_RANGE = "date_range"
PRICING_SOURCE_PERMANENT = "permanent"
PRICING_SOURCE_BASE = "base"

DEFAULT_BASE_PRICE_MINOR = 23900
DEFAULT_LESSONS_COUNT = 4
DEFAULT_DUE_DAYS = 17
DEFAULT_CURRENCY = "BYN"

VALID_DISCOUNT_TYPES = frozenset({"one_time", "date_range", "permanent"})
VALID_DISCOUNT_STATUSES = frozenset({"active", "cancelled", "consumed", "expired"})
VALID_CALCULATION_TYPES = frozenset({"fixed_price"})


def resolve_client_payment_terms(terms_row: Optional[dict]) -> dict:
    """Return canonical payment terms, falling back to defaults if row is None."""
    if not terms_row:
        return {
            "base_price_minor": DEFAULT_BASE_PRICE_MINOR,
            "base_lessons_count": DEFAULT_LESSONS_COUNT,
            "default_due_days": DEFAULT_DUE_DAYS,
            "currency": DEFAULT_CURRENCY,
            "automation_enabled": False,
            "automation_paused_reason": None,
            "base_subscription_type_id": None,
            "is_default": True,
        }
    return {
        "base_price_minor": int(terms_row.get("base_price_minor") or DEFAULT_BASE_PRICE_MINOR),
        "base_lessons_count": int(terms_row.get("base_lessons_count") or DEFAULT_LESSONS_COUNT),
        "default_due_days": int(terms_row.get("default_due_days") or DEFAULT_DUE_DAYS),
        "currency": str(terms_row.get("currency") or DEFAULT_CURRENCY),
        "automation_enabled": bool(terms_row.get("automation_enabled")),
        "automation_paused_reason": terms_row.get("automation_paused_reason"),
        "base_subscription_type_id": terms_row.get("base_subscription_type_id"),
        "is_default": False,
    }


def resolve_active_client_discount(
    active_discounts: list,
    pricing_date: str,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    Given a list of active discount rows and a pricing_date (YYYY-MM-DD),
    return (winning_discount, conflict_reason).

    conflict_reason is non-None if there is a data integrity conflict.
    winning_discount is None if no applicable discount found.

    Priority: one_time > date_range > permanent.
    If multiple active discounts of the SAME type exist → conflict.
    """
    by_type: dict = {"one_time": [], "date_range": [], "permanent": []}
    for d in active_discounts:
        dt = str(d.get("discount_type") or "")
        if dt in by_type:
            by_type[dt].append(d)

    # Check for conflicts (multiple active of same type)
    for dtype, rows in by_type.items():
        if len(rows) > 1:
            return None, f"pricing_conflict:{dtype}:{len(rows)}_active"

    # one_time (no date filter — applies regardless of date)
    if by_type["one_time"]:
        d = by_type["one_time"][0]
        if str(d.get("status") or "") == "active":
            return d, None

    # date_range (check date bounds inclusive)
    if by_type["date_range"]:
        d = by_type["date_range"][0]
        vf = str(d.get("valid_from") or "")
        vu = str(d.get("valid_until") or "")
        if vf and vu and vf <= pricing_date <= vu:
            return d, None

    # permanent
    if by_type["permanent"]:
        d = by_type["permanent"][0]
        return d, None

    return None, None


def resolve_next_subscription_price(
    terms_row: Optional[dict],
    active_discounts: list,
    pricing_date: str,
) -> dict:
    """
    Compute the resolved price for the next subscription cycle.

    Returns a dict with ok=True on success, ok=False on conflict.
    pricing_date must be YYYY-MM-DD string — caller supplies it, no system time used here.
    """
    terms = resolve_client_payment_terms(terms_row)
    base_price = terms["base_price_minor"]
    currency = terms["currency"]
    lessons_count = terms["base_lessons_count"]
    due_days = terms["default_due_days"]
    automation_enabled = terms["automation_enabled"]
    automation_paused_reason = terms["automation_paused_reason"]

    discount, conflict = resolve_active_client_discount(active_discounts, pricing_date)

    if conflict:
        return {
            "ok": False,
            "error": "pricing_conflict",
            "conflict_detail": conflict,
            "automation_blocked": True,
            "automation_block_reason": conflict,
        }

    if discount:
        resolved_price = int(discount.get("fixed_price_minor") or base_price)
        price_source = str(discount.get("discount_type") or PRICING_SOURCE_BASE)
        discount_id = discount.get("id")
        discount_type = discount.get("discount_type")
    else:
        resolved_price = base_price
        price_source = PRICING_SOURCE_BASE
        discount_id = None
        discount_type = None

    automation_blocked = bool(automation_paused_reason)

    return {
        "ok": True,
        "base_price_minor": base_price,
        "resolved_price_minor": resolved_price,
        "currency": currency,
        "lessons_count": lessons_count,
        "due_days": due_days,
        "price_source": price_source,
        "discount_id": discount_id,
        "discount_type": discount_type,
        "automation_enabled": automation_enabled,
        "automation_blocked": automation_blocked,
        "automation_block_reason": automation_paused_reason,
    }
