# payment_domain.py — Canonical payment domain rules for Yellow Club
# v7.0.98.3
#
# Pure constants and functions only.
# No database writes, no external API calls.
# Import from here; do not redefine these values elsewhere.

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

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
