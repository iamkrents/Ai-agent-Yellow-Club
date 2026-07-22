"""Tests for v7.1.0 — deadline backfill for pre-existing payment intents.

Covers (per hotfix requirements):
  01-05  Storage: get_intents_for_deadline_backfill query correctness
  06-09  Storage: get_intents_for_terminal_due_status_fix query correctness
  10     NULL status / NULL client_visibility do not block correct backfill
  11     date-only payUntil → 23:59:59 Europe/Minsk
  12     past payUntil → due_status = overdue
  13     future payUntil → due_status = upcoming
  14     no payUntil → fallback from created_at
  15     existing due_at not overwritten
  16     repeat backfill idempotent
  17     invalid invoice_snapshot_json does not break scheduler cycle
  18     withdrawn → due_status = withdrawn, due_at stays NULL
  19     paid → due_status = paid, due_at stays NULL
  20     posted_to_moyklass → due_status = paid (PAYMENT_INTENT_PAID_STATUSES semantics)
  21-22  paid / withdrawn: no new payment options
  23-24  paid / withdrawn: no payment_checkout_attempts
  25     backfill does not call bePaid client
  26     backfill does not call Telegram / bot
  27     scheduler due_status cycle works normally after backfill
  28     concurrent backfills do not corrupt data
  29-33  Version and source structure checks

Run offline (no Telegram / bePaid / MoyKlass):
    python -m unittest tests.test_deadline_backfill -v
"""
from __future__ import annotations

import datetime
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from payment_domain import (
    PAYMENT_INTENT_PAID_STATUSES,
    PAYMENT_INTENT_CANCELLED_STATUSES,
    DUE_SOURCE_MOYKLASS,
    DUE_SOURCE_FALLBACK,
    DUE_SOURCE_MISSING,
    DUE_STATUS_UPCOMING,
    DUE_STATUS_OVERDUE,
    DUE_STATUS_PAID,
    DUE_STATUS_WITHDRAWN,
    _MINSK_TZ,
)
from storage import Storage

CURRENT_VERSION = "7.1.0"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_intent(
    st: Storage,
    *,
    pay_until: str | None = "unset",  # sentinel: unset = no key in snap
    status: str = "bepaid_created",
    visibility: str = "published",
    invoice_id: str = "inv_bf_1",
    snap_override: str | None = None,  # supply raw JSON string
) -> dict:
    """Insert a payment intent with optional payUntil in invoice_snapshot_json."""
    now = _now_iso()
    if snap_override is not None:
        snap = snap_override
    elif pay_until == "unset":
        snap = json.dumps({})
    else:
        snap = json.dumps({"payUntil": pay_until})

    intent = st.create_payment_intent({
        "mk_user_id": 88888,
        "student_name": "Backfill Test",
        "amount_minor": 22900,
        "amount_byn": 229.0,
        "currency": "BYN",
        "purpose": "subscription",
        "payment_method": "erip",
        "status": status,
        "created_at": now,
        "mk_invoice_id": invoice_id,
        "source": "test",
        "invoice_snapshot_json": snap,
    })
    if visibility == "published":
        st.publish_payment_intent_to_client(intent["public_id"], "test", now)
    elif visibility == "withdrawn":
        st.publish_payment_intent_to_client(intent["public_id"], "test", now)
        st.withdraw_payment_intent_from_client(intent["public_id"], "test", now)
    return st.get_payment_intent(intent["public_id"])


def _count_checkout_attempts(st: Storage) -> int:
    """Direct DB count of payment_checkout_attempts rows."""
    with st._connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM payment_checkout_attempts").fetchone()[0]


def _make_scheduler(st: Storage):
    from web_app_server import InvoiceAutomationScheduler, MiniAppContext
    ctx = MiniAppContext.__new__(MiniAppContext)
    ctx.storage = st
    settings = MagicMock()
    settings.payment_default_due_days = 14
    ctx.settings = settings
    return InvoiceAutomationScheduler(ctx)


# ---------------------------------------------------------------------------
# 1 — Storage: get_intents_for_deadline_backfill
# ---------------------------------------------------------------------------

class TestBackfillQuery(unittest.TestCase):
    """SQL correctness and NULL-safety for get_intents_for_deadline_backfill."""

    def setUp(self):
        self.st = _make_storage()

    def test_01_active_intent_with_null_due_at_returned(self):
        i = _make_intent(self.st, invoice_id="q01")
        pids = [r["public_id"] for r in self.st.get_intents_for_deadline_backfill()]
        self.assertIn(i["public_id"], pids)

    def test_02_intent_with_valid_due_at_excluded(self):
        i = _make_intent(self.st, invoice_id="q02")
        now = _now_iso()
        self.st.set_intent_due_at(i["public_id"], "2026-08-01",
                                   "2026-08-01T23:59:59+03:00", DUE_SOURCE_MOYKLASS, now)
        pids = [r["public_id"] for r in self.st.get_intents_for_deadline_backfill()]
        self.assertNotIn(i["public_id"], pids)

    def test_03_paid_excluded_from_backfill_query(self):
        i = _make_intent(self.st, status="paid", visibility="hidden", invoice_id="q03")
        pids = [r["public_id"] for r in self.st.get_intents_for_deadline_backfill()]
        self.assertNotIn(i["public_id"], pids)

    def test_04_posted_to_moyklass_excluded_from_backfill_query(self):
        i = _make_intent(self.st, status="posted_to_moyklass",
                         visibility="hidden", invoice_id="q04")
        pids = [r["public_id"] for r in self.st.get_intents_for_deadline_backfill()]
        self.assertNotIn(i["public_id"], pids)

    def test_05_withdrawn_excluded_from_backfill_query(self):
        i = _make_intent(self.st, visibility="withdrawn", invoice_id="q05")
        pids = [r["public_id"] for r in self.st.get_intents_for_deadline_backfill()]
        self.assertNotIn(i["public_id"], pids)

    def test_06_cancelled_excluded(self):
        i = _make_intent(self.st, status="cancelled", visibility="hidden", invoice_id="q06")
        pids = [r["public_id"] for r in self.st.get_intents_for_deadline_backfill()]
        self.assertNotIn(i["public_id"], pids)

    def test_07_null_coalesce_does_not_exclude_active_intent(self):
        """COALESCE(status,'draft') and COALESCE(client_visibility,'hidden') must
        not accidentally exclude a valid active intent."""
        # Insert intent and then force status/visibility to 'draft'/'hidden' via
        # the standard path. The SQL uses COALESCE defaults that are non-terminal,
        # so the intent must be INCLUDED in the query.
        i = _make_intent(self.st, visibility="hidden", invoice_id="q07")
        pids = [r["public_id"] for r in self.st.get_intents_for_deadline_backfill()]
        self.assertIn(i["public_id"], pids)

    def test_08_due_at_source_missing_still_included(self):
        """An intent whose due_at_source='missing' (the default) must be included."""
        i = _make_intent(self.st, invoice_id="q08")
        pi = self.st.get_payment_intent(i["public_id"])
        # Confirm the default source
        self.assertIn(str(pi.get("due_at_source") or ""), ("missing", None, ""))
        pids = [r["public_id"] for r in self.st.get_intents_for_deadline_backfill()]
        self.assertIn(i["public_id"], pids)


# ---------------------------------------------------------------------------
# 2 — Storage: get_intents_for_terminal_due_status_fix
# ---------------------------------------------------------------------------

class TestTerminalDueStatusQuery(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()

    def test_09_withdrawn_with_wrong_due_status_returned(self):
        i = _make_intent(self.st, visibility="withdrawn", invoice_id="t09")
        pids = [r["public_id"] for r in self.st.get_intents_for_terminal_due_status_fix()]
        self.assertIn(i["public_id"], pids)

    def test_10_paid_with_wrong_due_status_returned(self):
        i = _make_intent(self.st, status="paid", visibility="hidden", invoice_id="t10")
        pids = [r["public_id"] for r in self.st.get_intents_for_terminal_due_status_fix()]
        self.assertIn(i["public_id"], pids)

    def test_11_posted_to_moyklass_with_wrong_due_status_returned(self):
        """posted_to_moyklass is in PAYMENT_INTENT_PAID_STATUSES — must be included."""
        i = _make_intent(self.st, status="posted_to_moyklass",
                         visibility="hidden", invoice_id="t11")
        pids = [r["public_id"] for r in self.st.get_intents_for_terminal_due_status_fix()]
        self.assertIn(i["public_id"], pids)

    def test_12_active_intent_not_in_terminal_query(self):
        i = _make_intent(self.st, invoice_id="t12")
        pids = [r["public_id"] for r in self.st.get_intents_for_terminal_due_status_fix()]
        self.assertNotIn(i["public_id"], pids)

    def test_13_withdrawn_already_correct_excluded(self):
        i = _make_intent(self.st, visibility="withdrawn", invoice_id="t13")
        self.st.update_intent_due_status(i["public_id"], DUE_STATUS_WITHDRAWN, None, _now_iso())
        pids = [r["public_id"] for r in self.st.get_intents_for_terminal_due_status_fix()]
        self.assertNotIn(i["public_id"], pids)

    def test_14_paid_already_correct_excluded(self):
        i = _make_intent(self.st, status="paid", visibility="hidden", invoice_id="t14")
        self.st.update_intent_due_status(i["public_id"], DUE_STATUS_PAID, None, _now_iso())
        pids = [r["public_id"] for r in self.st.get_intents_for_terminal_due_status_fix()]
        self.assertNotIn(i["public_id"], pids)


# ---------------------------------------------------------------------------
# 3 — Backfill: due_at population
# ---------------------------------------------------------------------------

class TestBackfillDueAt(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()
        self.sched = _make_scheduler(self.st)

    def test_15_pay_until_date_only_populates_due_at(self):
        _make_intent(self.st, pay_until="2026-08-15", invoice_id="b15")
        self.sched._run_deadline_backfill()
        rows = self.st.get_intents_for_deadline_backfill()
        self.assertEqual(rows, [], "Backfill candidates must be empty after run")

    def test_16_date_only_becomes_235959_minsk(self):
        i = _make_intent(self.st, pay_until="2026-08-15", invoice_id="b16")
        self.sched._run_deadline_backfill()
        pi = self.st.get_payment_intent(i["public_id"])
        self.assertIsNotNone(pi["due_at"])
        self.assertIn("2026-08-15", pi["due_at"])
        self.assertIn("23:59:59", pi["due_at"])
        self.assertIn("+03:00", pi["due_at"])
        self.assertEqual(pi["due_at_source"], DUE_SOURCE_MOYKLASS)

    def test_17_past_pay_until_gives_overdue_status(self):
        i = _make_intent(self.st, pay_until="2026-01-01", invoice_id="b17")
        self.sched._run_deadline_backfill()
        pi = self.st.get_payment_intent(i["public_id"])
        self.assertEqual(pi["due_status"], DUE_STATUS_OVERDUE)
        self.assertIsNotNone(pi["overdue_since"])

    def test_18_future_pay_until_gives_upcoming_status(self):
        future = (datetime.datetime.now(_MINSK_TZ) + datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        i = _make_intent(self.st, pay_until=future, invoice_id="b18")
        self.sched._run_deadline_backfill()
        pi = self.st.get_payment_intent(i["public_id"])
        self.assertEqual(pi["due_status"], DUE_STATUS_UPCOMING)

    def test_19_fallback_applied_without_pay_until(self):
        i = _make_intent(self.st, pay_until="unset", invoice_id="b19")
        self.sched._run_deadline_backfill()
        pi = self.st.get_payment_intent(i["public_id"])
        self.assertIsNotNone(pi["due_at"])
        self.assertEqual(pi["due_at_source"], DUE_SOURCE_FALLBACK)

    def test_20_draft_hidden_intent_included_in_backfill(self):
        """Draft status and hidden visibility are non-terminal; COALESCE defaults match them.

        payment_intents.status and client_visibility are NOT NULL in schema, so
        NULL injection is impossible at the DB level. This test instead verifies
        that the COALESCE-safe defaults ('draft', 'hidden') correctly admit
        intents with those minimal states into the backfill query.
        """
        i = _make_intent(self.st, pay_until="2026-09-01",
                         status="draft", visibility="hidden", invoice_id="b20")
        pids = [r["public_id"] for r in self.st.get_intents_for_deadline_backfill()]
        self.assertIn(i["public_id"], pids)
        self.sched._run_deadline_backfill()
        pi = self.st.get_payment_intent(i["public_id"])
        self.assertIsNotNone(pi["due_at"])

    def test_21_existing_due_at_not_overwritten(self):
        i = _make_intent(self.st, invoice_id="b21")
        now = _now_iso()
        original = "2026-09-30T23:59:59+03:00"
        self.st.set_intent_due_at(i["public_id"], "2026-09-30", original, DUE_SOURCE_MOYKLASS, now)
        self.sched._run_deadline_backfill()
        pi = self.st.get_payment_intent(i["public_id"])
        self.assertEqual(pi["due_at"], original)
        self.assertEqual(pi["due_at_source"], DUE_SOURCE_MOYKLASS)

    def test_22_repeat_backfill_idempotent(self):
        i = _make_intent(self.st, pay_until="2026-08-20", invoice_id="b22")
        self.sched._run_deadline_backfill()
        pi1 = self.st.get_payment_intent(i["public_id"])
        self.sched._run_deadline_backfill()
        pi2 = self.st.get_payment_intent(i["public_id"])
        self.assertEqual(pi1["due_at"], pi2["due_at"])
        self.assertEqual(pi1["due_at_source"], pi2["due_at_source"])
        self.assertEqual(pi1["due_status"], pi2["due_status"])

    def test_23_invalid_json_does_not_break_scheduler_cycle(self):
        good = _make_intent(self.st, pay_until="2026-08-01", invoice_id="b23a")
        _make_intent(self.st, snap_override="{not valid json {{{{", invoice_id="b23b")
        self.sched._run_deadline_backfill()  # must not raise
        pi_good = self.st.get_payment_intent(good["public_id"])
        self.assertIsNotNone(pi_good["due_at"])


# ---------------------------------------------------------------------------
# 4 — Terminal state handling (Pass 1)
# ---------------------------------------------------------------------------

class TestTerminalStatusBackfill(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()
        self.sched = _make_scheduler(self.st)

    def test_24_withdrawn_gets_withdrawn_due_status(self):
        i = _make_intent(self.st, visibility="withdrawn", invoice_id="p24")
        self.sched._run_deadline_backfill()
        pi = self.st.get_payment_intent(i["public_id"])
        self.assertEqual(pi["due_status"], DUE_STATUS_WITHDRAWN)

    def test_25_withdrawn_due_at_stays_null(self):
        i = _make_intent(self.st, visibility="withdrawn", invoice_id="p25")
        self.sched._run_deadline_backfill()
        pi = self.st.get_payment_intent(i["public_id"])
        self.assertIsNone(pi.get("due_at"))

    def test_26_paid_gets_paid_due_status(self):
        i = _make_intent(self.st, status="paid", visibility="hidden", invoice_id="p26")
        self.sched._run_deadline_backfill()
        pi = self.st.get_payment_intent(i["public_id"])
        self.assertEqual(pi["due_status"], DUE_STATUS_PAID)

    def test_27_paid_due_at_stays_null(self):
        i = _make_intent(self.st, status="paid", visibility="hidden", invoice_id="p27")
        self.sched._run_deadline_backfill()
        pi = self.st.get_payment_intent(i["public_id"])
        self.assertIsNone(pi.get("due_at"))

    def test_28_posted_to_moyklass_gets_paid_due_status(self):
        """posted_to_moyklass ∈ PAYMENT_INTENT_PAID_STATUSES → due_status must be 'paid'."""
        self.assertIn("posted_to_moyklass", PAYMENT_INTENT_PAID_STATUSES,
                      "Domain invariant: posted_to_moyklass must be in PAYMENT_INTENT_PAID_STATUSES")
        i = _make_intent(self.st, status="posted_to_moyklass",
                         visibility="hidden", invoice_id="p28")
        self.sched._run_deadline_backfill()
        pi = self.st.get_payment_intent(i["public_id"])
        self.assertEqual(pi["due_status"], DUE_STATUS_PAID)

    def test_29_posted_to_moyklass_due_at_stays_null(self):
        i = _make_intent(self.st, status="posted_to_moyklass",
                         visibility="hidden", invoice_id="p29")
        self.sched._run_deadline_backfill()
        pi = self.st.get_payment_intent(i["public_id"])
        self.assertIsNone(pi.get("due_at"))


# ---------------------------------------------------------------------------
# 5 — No side effects: no options, no checkout attempts, no bePaid, no Telegram
# ---------------------------------------------------------------------------

class TestBackfillNoSideEffects(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()
        self.sched = _make_scheduler(self.st)

    def test_30_backfill_creates_no_payment_options(self):
        i = _make_intent(self.st, pay_until="2026-08-01", invoice_id="s30")
        self.sched._run_deadline_backfill()
        opts = self.st.get_options_for_intent(i["public_id"])
        self.assertEqual(opts, [], "Backfill must not create payment options")

    def test_31_backfill_creates_no_payment_checkout_attempts(self):
        _make_intent(self.st, pay_until="2026-08-01", invoice_id="s31")
        attempts_before = _count_checkout_attempts(self.st)
        self.sched._run_deadline_backfill()
        self.assertEqual(_count_checkout_attempts(self.st), attempts_before,
                         "Backfill must not create payment_checkout_attempts rows")

    def test_32_paid_intent_no_new_options(self):
        i = _make_intent(self.st, status="paid", visibility="hidden", invoice_id="s32")
        self.sched._run_deadline_backfill()
        self.assertEqual(self.st.get_options_for_intent(i["public_id"]), [])

    def test_33_paid_intent_no_checkout_attempts(self):
        _make_intent(self.st, status="paid", visibility="hidden", invoice_id="s33")
        before = _count_checkout_attempts(self.st)
        self.sched._run_deadline_backfill()
        self.assertEqual(_count_checkout_attempts(self.st), before)

    def test_34_withdrawn_intent_no_new_options(self):
        i = _make_intent(self.st, visibility="withdrawn", invoice_id="s34")
        self.sched._run_deadline_backfill()
        self.assertEqual(self.st.get_options_for_intent(i["public_id"]), [])

    def test_35_backfill_does_not_call_bepaid(self):
        """ctx has no bepaid_client attribute — if backfill tried to call it, AttributeError."""
        _make_intent(self.st, pay_until="2026-08-01", invoice_id="s35")
        self.assertFalse(hasattr(self.sched._ctx, "bepaid"),
                         "Test ctx must not have a bepaid attribute")
        self.sched._run_deadline_backfill()  # must not raise AttributeError

    def test_36_backfill_does_not_call_telegram(self):
        """ctx has no bot attribute — if backfill tried to send a Telegram msg, AttributeError."""
        _make_intent(self.st, pay_until="2026-08-01", invoice_id="s36")
        self.assertFalse(hasattr(self.sched._ctx, "bot"),
                         "Test ctx must not have a bot attribute")
        self.sched._run_deadline_backfill()  # must not raise AttributeError


# ---------------------------------------------------------------------------
# 6 — Scheduler works normally after backfill
# ---------------------------------------------------------------------------

class TestSchedulerAfterBackfill(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()
        self.sched = _make_scheduler(self.st)

    def test_37_due_status_update_cycle_works_after_backfill(self):
        future = (datetime.datetime.now(_MINSK_TZ) + datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        i = _make_intent(self.st, pay_until=future, invoice_id="sc37")
        self.sched._run_deadline_backfill()
        pi = self.st.get_payment_intent(i["public_id"])
        self.assertIsNotNone(pi["due_at"])
        # Corrupt due_status then verify _update_due_statuses fixes it
        self.st.update_intent_due_status(i["public_id"], DUE_STATUS_OVERDUE, None, _now_iso())
        self.sched._update_due_statuses()
        pi2 = self.st.get_payment_intent(i["public_id"])
        self.assertEqual(pi2["due_status"], DUE_STATUS_UPCOMING)


# ---------------------------------------------------------------------------
# 7 — Concurrent backfill safety
# ---------------------------------------------------------------------------

class TestConcurrentBackfill(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()

    def test_38_concurrent_backfills_do_not_corrupt_data(self):
        future = (datetime.datetime.now(_MINSK_TZ) + datetime.timedelta(days=20)).strftime("%Y-%m-%d")
        i = _make_intent(self.st, pay_until=future, invoice_id="cc38")
        pid = i["public_id"]
        sched1 = _make_scheduler(self.st)
        sched2 = _make_scheduler(self.st)
        errors: list[Exception] = []

        def run(s):
            try:
                s._run_deadline_backfill()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=run, args=(sched1,))
        t2 = threading.Thread(target=run, args=(sched2,))
        t1.start(); t2.start()
        t1.join(timeout=30); t2.join(timeout=30)

        self.assertEqual(errors, [], f"Concurrent backfill raised: {errors}")
        pi = self.st.get_payment_intent(pid)
        self.assertIsNotNone(pi["due_at"])
        self.assertEqual(pi["due_at_source"], DUE_SOURCE_MOYKLASS)
        self.assertEqual(pi["due_status"], DUE_STATUS_UPCOMING)


# ---------------------------------------------------------------------------
# 8 — Version and source structure
# ---------------------------------------------------------------------------

class TestVersion(unittest.TestCase):

    def test_39_miniapp_version(self):
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn(f"v{CURRENT_VERSION}", js)

    def test_40_cache_bust_styles_in_html(self):
        html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
        self.assertIn(f"v={CURRENT_VERSION}", html)

    def test_41_cache_bust_script_in_html(self):
        html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
        self.assertIn(f"app.js?v={CURRENT_VERSION}", html)

    def test_42_payment_domain_header(self):
        src = (ROOT / "payment_domain.py").read_text(encoding="utf-8")
        self.assertIn(f"v{CURRENT_VERSION}", src)

    def test_43_backfill_method_in_server_source(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("_run_deadline_backfill", src)
        self.assertIn("payment_due_backfill_completed", src)
        self.assertIn("terminal_fixed", src)
        self.assertIn("skipped_already_filled", src)

    def test_44_sql_uses_coalesce_for_null_safety(self):
        src = (ROOT / "storage.py").read_text(encoding="utf-8")
        self.assertIn("COALESCE(client_visibility", src)
        self.assertIn("COALESCE(status", src)

    def test_45_posted_to_moyklass_in_paid_statuses(self):
        """Confirm domain invariant: posted_to_moyklass is treated as paid."""
        self.assertIn("posted_to_moyklass", PAYMENT_INTENT_PAID_STATUSES)


if __name__ == "__main__":
    unittest.main()
