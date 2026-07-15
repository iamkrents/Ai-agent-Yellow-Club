#!/usr/bin/env python3
"""Safely cancel test payment intents from production.

Operates on a fixed allowlist — ycpi_202607_9 through ycpi_202607_13 — and
never touches any other intent. ycpi_202607_14 (the successful production
payment) is hard-protected: the script aborts if it somehow appears in the
allowlist.

Usage:
    python scripts/archive_test_payment_intents.py          # dry-run (default)
    python scripts/archive_test_payment_intents.py --apply  # real changes

What the script does on --apply:
    - Sets intent status → 'cancelled'  (cancel_reason='test_cleanup')
    - Marks all active options → 'cancelled'
    - Logs a payment_webhook_audit event per intent
    - Leaves ALL rows intact: bepaid_transactions, payment_intent_options,
      payment_webhook_audit, ERIP account numbers, checkout tokens, MK IDs

What the script never does:
    - bePaid API calls (no checkout create/cancel/refund)
    - MoyKlass API calls
    - Telegram messages
    - Physical row deletion
    - Touching ycpi_202607_14

Idempotency: already-cancelled intents are skipped; re-running exits 0.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from config import load_settings
    from storage import Storage as YellowClubStorage
except ImportError as exc:
    print(f"[ERROR] Cannot import project modules: {exc}")
    print(f"        Run from project root: python scripts/{Path(__file__).name}")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ALLOWLIST: list[str] = [
    "ycpi_202607_9",
    "ycpi_202607_10",
    "ycpi_202607_11",
    "ycpi_202607_12",
    "ycpi_202607_13",
]

PROTECTED: str = "ycpi_202607_14"
CANCEL_REASON: str = "test_cleanup"

# Statuses that must never be cancelled by this script
HARD_BLOCK_STATUSES: frozenset[str] = frozenset(
    {"paid", "posted_to_moyklass", "double_payment_requires_check"}
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _fmt_amount(amount_minor: int | None) -> str:
    if amount_minor is None:
        return "?"
    return f"{amount_minor / 100:.2f} BYN"


def _safety_checks() -> None:
    """Abort immediately if any safety invariant is violated."""
    if PROTECTED in ALLOWLIST:
        print(f"\n[ABORT] PROTECTED intent {PROTECTED!r} found in ALLOWLIST. "
              f"This is a code bug — fix the allowlist before re-running.")
        sys.exit(2)
    duplicates = [p for p in ALLOWLIST if ALLOWLIST.count(p) > 1]
    if duplicates:
        print(f"\n[ABORT] Duplicate entries in ALLOWLIST: {set(duplicates)}")
        sys.exit(2)


def _report_intent(
    public_id: str,
    pi: dict | None,
    options: list[dict],
    transactions: list[dict],
    planned_action: str,
) -> None:
    if pi is None:
        print(f"  {public_id}: NOT FOUND in database")
        return

    status = pi.get("status", "?")
    amount = _fmt_amount(pi.get("amount_minor"))
    mk_invoice = pi.get("mk_invoice_id") or "-"

    erip_opt = next((o for o in options if o.get("channel") == "erip"), None)
    acq_opt = next((o for o in options if o.get("channel") == "acquiring"), None)
    successful_txs = [t for t in transactions if t.get("status") == "successful"]
    failed_txs = [t for t in transactions if t.get("status") not in ("successful", None)]

    print(f"  {public_id}:")
    print(f"    status          : {status}")
    print(f"    amount          : {amount}")
    print(f"    mk_invoice_id   : {mk_invoice}")
    print(f"    erip_option     : {'yes (status=' + (erip_opt or {}).get('status', '?') + ')' if erip_opt else 'none'}")
    print(f"    acquiring_option: {'yes (status=' + (acq_opt or {}).get('status', '?') + ')' if acq_opt else 'none'}")
    print(f"    successful_tx   : {len(successful_txs)} row(s)")
    print(f"    failed_tx       : {len(failed_txs)} row(s) (kept in history)")
    print(f"    planned_action  : {planned_action}")
    print()


def run(apply: bool, storage: YellowClubStorage) -> int:
    """Iterate allowlist, print report, optionally apply changes.

    Returns exit code (0 = success, 1 = blocked by paid intent).
    """
    _safety_checks()

    now = _now_iso()
    blocked_ids: list[str] = []
    to_cancel: list[str] = []
    already_done: list[str] = []

    print("=" * 60)
    print(f"  archive_test_payment_intents — {'APPLY' if apply else 'DRY-RUN'}")
    print(f"  timestamp: {now}")
    print("=" * 60)
    print()
    print(f"Protected (never touched): {PROTECTED}")
    print(f"Allowlist ({len(ALLOWLIST)} intents):")
    for pid in ALLOWLIST:
        print(f"    {pid}")
    print()
    print("Per-intent preview:")
    print()

    for public_id in ALLOWLIST:
        pi = storage.get_payment_intent(public_id)
        options = storage.get_options_for_intent(public_id) if pi else []
        transactions = storage.get_bepaid_transactions_for_intent(public_id) if pi else []

        if pi is None:
            _report_intent(public_id, None, [], [], "SKIP — not found")
            continue

        status = pi.get("status", "")

        # Already cancelled — idempotent
        if status == "cancelled":
            _report_intent(public_id, pi, options, transactions, "SKIP — already cancelled (idempotent)")
            already_done.append(public_id)
            continue

        # Block: paid or posted
        if status in HARD_BLOCK_STATUSES:
            _report_intent(public_id, pi, options, transactions,
                           f"BLOCKED — status={status!r} is protected (paid/posted)")
            blocked_ids.append(public_id)
            continue

        # Also block if a successful/verified transaction exists but intent isn't in hard-block
        # (e.g. a successful tx but intent status is still bepaid_created due to race)
        successful_txs = [t for t in transactions if t.get("status") == "successful"
                          and (t.get("webhook_verified") or t.get("provider_verified"))]
        if successful_txs:
            _report_intent(public_id, pi, options, transactions,
                           f"BLOCKED — verified successful transaction exists; manual admin review required")
            blocked_ids.append(public_id)
            continue

        active_options = [o for o in options if o.get("status") not in ("paid", "cancelled", "superseded")]
        action_desc = (
            f"cancel intent → test_cleanup; "
            f"cancel {len(active_options)} active option(s)"
        )
        _report_intent(public_id, pi, options, transactions, action_desc)
        to_cancel.append(public_id)

    # Summary before applying
    print("=" * 60)
    print(f"  Summary:")
    print(f"    to cancel  : {len(to_cancel)} — {to_cancel}")
    print(f"    already done: {len(already_done)} — {already_done}")
    print(f"    blocked    : {len(blocked_ids)} — {blocked_ids}")
    print("=" * 60)
    print()

    if blocked_ids:
        print(f"[ERROR] {len(blocked_ids)} intent(s) blocked — paid or verified transaction present.")
        print(f"        Manual admin review required before cleanup: {blocked_ids}")
        return 1

    if not apply:
        print("DRY-RUN complete — no changes made.")
        print("Re-run with --apply to execute the cancellations.")
        return 0

    # ---------- APPLY ----------
    errors: list[str] = []
    for public_id in to_cancel:
        result = storage.cancel_payment_intent_for_cleanup(public_id, CANCEL_REASON, now)
        if not result.get("ok"):
            if result.get("idempotent"):
                print(f"  {public_id}: already cancelled (idempotent)")
                continue
            err = result.get("error", "unknown")
            print(f"  {public_id}: FAILED — {err}")
            errors.append(public_id)
            continue

        changed_opts = storage.cancel_options_for_cleanup(public_id, now)

        storage.log_payment_webhook_audit(
            "test_intent_cancelled",
            intent_public_id=public_id,
            reason=CANCEL_REASON,
            details={"changed_options": changed_opts, "applied_at": now},
        )
        print(f"  {public_id}: cancelled (options_cancelled={changed_opts})")

    if errors:
        print(f"\n[ERROR] {len(errors)} intent(s) failed to cancel: {errors}")
        return 1

    print(f"\nDone. {len(to_cancel)} intent(s) cancelled, "
          f"{len(already_done)} already done, "
          f"{len(blocked_ids)} blocked.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Safely cancel test payment intents (dry-run by default)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually apply changes. Default is dry-run.",
    )
    args = parser.parse_args()

    try:
        cfg = load_settings()
    except Exception as exc:
        print(f"[ERROR] Cannot load settings: {exc}")
        sys.exit(1)

    storage = YellowClubStorage(cfg.db_path)
    exit_code = run(apply=args.apply, storage=storage)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
