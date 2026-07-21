# payment_domain.py — Canonical payment domain rules for Yellow Club
# v7.0.99.0
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
