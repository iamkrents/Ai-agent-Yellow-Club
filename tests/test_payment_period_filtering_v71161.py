"""Tests for v7.1.16.1 — Payment Period Filters: canonical period data model.

Covers (DATA 1-10 from the launch spec):
   1. Canonical period is determined (period_month, falling back to the
      month of created_at — never updated_at, never paid_at).
   2. An August invoice paid in September stays in August.
   3. updated_at never changes the computed period.
   4. Legacy rows with no period_month/created_at are counted separately
      ("undated"), never silently attributed to the current month.
   5. "All time" mode includes undated legacy rows.
   6. Period start is inclusive.
   7. Period end has no off-by-one/last-day error (exclusive next-day
      boundary).
   8. The configured app timezone (Europe/Minsk) is used for defaults.
   9. An invalid custom range is rejected.
  10. No mixing of different date semantics (billing month vs payment date
      vs last-touched date) within one query.

Run:
    python -m unittest tests.test_payment_period_filtering_v71161 -v
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest  # noqa: E402

from storage import Storage  # noqa: E402


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _mk_intent(st: Storage, pid, mk_user_id, period_month, created_at, status, updated_at=None):
    with st._connect() as conn:
        conn.execute(
            """INSERT INTO payment_intents
               (public_id, mk_user_id, amount_minor, amount_byn, currency, purpose, period_month,
                payment_method, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, mk_user_id, 10000, 100.0, "BYN", "other", period_month,
             "erip", status, created_at, updated_at or created_at),
        )


class TestCanonicalPeriodDetermination(unittest.TestCase):
    def test_1_explicit_period_month_wins(self):
        st = _tmp_storage()
        _mk_intent(st, "P1", 1, "2026-08", "2026-08-05T10:00:00", "paid")
        rows, total = st.list_payment_intents_by_period("2026-08-01", "2026-09-01")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["public_id"], "P1")

    def test_1b_legacy_row_falls_back_to_created_at_month(self):
        st = _tmp_storage()
        _mk_intent(st, "P2", 2, None, "2026-06-15T10:00:00", "paid")
        rows, total = st.list_payment_intents_by_period("2026-06-01", "2026-07-01")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["public_id"], "P2")

    def test_2_august_invoice_paid_in_september_stays_in_august(self):
        st = _tmp_storage()
        # billed for August, but paid_at falls in September
        with st._connect() as conn:
            conn.execute(
                """INSERT INTO payment_intents
                   (public_id, mk_user_id, amount_minor, amount_byn, currency, purpose, period_month,
                    payment_method, status, created_at, updated_at, paid_at)
                   VALUES ('P3',3,10000,100.0,'BYN','other','2026-08','erip','paid',
                           '2026-08-05T10:00:00','2026-09-03T09:00:00','2026-09-03T09:00:00')"""
            )
        stats_aug = st.get_payments_workspace_stats("2026-08-01", "2026-09-01")
        stats_sep = st.get_payments_workspace_stats("2026-09-01", "2026-10-01")
        self.assertEqual(stats_aug["paid"], 1, "invoice must appear in August (its billing month)")
        self.assertEqual(stats_sep["paid"], 0, "invoice must NOT appear in September just because it was paid there")

    def test_3_updated_at_never_changes_computed_period(self):
        st = _tmp_storage()
        # period_month says July, but updated_at is way in the future (December) —
        # the invoice must still be classified as July, proving updated_at is ignored.
        _mk_intent(st, "P4", 4, "2026-07", "2026-07-01T10:00:00", "paid", updated_at="2026-12-31T23:59:59")
        stats_jul = st.get_payments_workspace_stats("2026-07-01", "2026-08-01")
        stats_dec = st.get_payments_workspace_stats("2026-12-01", "2027-01-01")
        self.assertEqual(stats_jul["paid"], 1)
        self.assertEqual(stats_dec["paid"], 0)


class TestUndatedLegacyRows(unittest.TestCase):
    def test_4_truly_undated_row_counted_separately(self):
        st = _tmp_storage()
        with st._connect() as conn:
            # force a row with no period_month AND no usable created_at
            conn.execute(
                """INSERT INTO payment_intents
                   (public_id, mk_user_id, amount_minor, amount_byn, currency, purpose, period_month,
                    payment_method, status, created_at, updated_at)
                   VALUES ('P-UNDATED',5,5000,50.0,'BYN','other',NULL,'erip','draft','','')"""
            )
        stats_all = st.get_payments_workspace_stats(None, None)
        self.assertEqual(stats_all["undated_count"], 1)
        rows, _total = st.list_payment_intents_by_period("2026-08-01", "2026-09-01")
        self.assertNotIn("P-UNDATED", [r["public_id"] for r in rows], "must never be silently attributed to a specific month")

    def test_5_all_time_mode_includes_undated_rows(self):
        st = _tmp_storage()
        with st._connect() as conn:
            conn.execute(
                """INSERT INTO payment_intents
                   (public_id, mk_user_id, amount_minor, amount_byn, currency, purpose, period_month,
                    payment_method, status, created_at, updated_at)
                   VALUES ('P-UNDATED2',6,5000,50.0,'BYN','other',NULL,'erip','draft','','')"""
            )
        rows, total = st.list_payment_intents_by_period(None, None)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["public_id"], "P-UNDATED2")


class TestBoundaries(unittest.TestCase):
    def test_6_period_start_inclusive(self):
        st = _tmp_storage()
        _mk_intent(st, "P5", 7, "2026-08", "2026-08-01T00:00:01", "paid")
        rows, total = st.list_payment_intents_by_period("2026-08-01", "2026-09-01")
        self.assertEqual(total, 1)

    def test_7_period_end_exclusive_no_last_day_error(self):
        st = _tmp_storage()
        # last day of August must be included when filtering [Aug1, Sep1)
        _mk_intent(st, "P6", 8, "2026-08", "2026-08-31T23:59:59", "paid")
        rows, total = st.list_payment_intents_by_period("2026-08-01", "2026-09-01")
        self.assertEqual(total, 1)
        # first day of September must NOT be included in the August window
        _mk_intent(st, "P7", 9, "2026-09", "2026-09-01T00:00:00", "paid")
        rows, total = st.list_payment_intents_by_period("2026-08-01", "2026-09-01")
        self.assertEqual(total, 1, "September's first-day row leaked into the August window")


class TestTimezoneDefault(unittest.TestCase):
    def test_8_period_parsing_uses_configured_timezone(self):
        import web_app_server as srv
        ctx = srv.MiniAppContext.__new__(srv.MiniAppContext)
        period = ctx._parse_payments_period({})
        self.assertTrue(period["ok"])
        self.assertEqual(period["mode"], "month")
        # deterministically resolvable: whatever "today" is in Europe/Minsk right now
        import zoneinfo
        from datetime import datetime
        today_minsk = datetime.now(zoneinfo.ZoneInfo("Europe/Minsk")).date()
        self.assertEqual(period["start"], today_minsk.replace(day=1).isoformat())


class TestInvalidRangeRejected(unittest.TestCase):
    def test_9_end_before_start_rejected(self):
        import web_app_server as srv
        ctx = srv.MiniAppContext.__new__(srv.MiniAppContext)
        period = ctx._parse_payments_period({"period_mode": "custom", "period_start": "2026-08-20", "period_end": "2026-08-01"})
        self.assertFalse(period.get("ok"))
        self.assertIn("error", period)

    def test_9b_malformed_dates_rejected(self):
        import web_app_server as srv
        ctx = srv.MiniAppContext.__new__(srv.MiniAppContext)
        period = ctx._parse_payments_period({"period_mode": "custom", "period_start": "not-a-date", "period_end": "2026-08-01"})
        self.assertFalse(period.get("ok"))

    def test_9c_unknown_mode_rejected(self):
        import web_app_server as srv
        ctx = srv.MiniAppContext.__new__(srv.MiniAppContext)
        period = ctx._parse_payments_period({"period_mode": "yearly"})
        self.assertFalse(period.get("ok"))


class TestNoDateSemanticMixing(unittest.TestCase):
    def test_10_query_never_references_paid_at_or_updated_at(self):
        # the canonical period SQL fragments must only ever reference
        # period_month/created_at — a direct source-level guarantee against
        # accidentally mixing payment-date or last-touched-date semantics in.
        import inspect
        from storage import Storage as St
        for attr in ("_PI_PERIOD_START_SQL", "_IAI_PERIOD_START_SQL"):
            sql = getattr(St, attr)
            self.assertNotIn("paid_at", sql)
            self.assertNotIn("updated_at", sql)
            self.assertNotIn("due_at", sql)


if __name__ == "__main__":
    unittest.main()
