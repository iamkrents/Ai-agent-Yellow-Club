"""Tests for scripts/archive_test_payment_intents.py — v7.0.92.5.4+.

Tests:
  1.  ALLOWLIST contains exactly ycpi_202607_9..13
  2.  PROTECTED (ycpi_202607_14) is not in ALLOWLIST
  3.  _safety_checks() aborts when PROTECTED in allowlist
  4.  dry-run leaves DB unchanged
  5.  --apply cancels only the allowlist intents
  6.  paid intent blocks the run and returns exit code 1
  7.  failed (non-successful) transaction is preserved; intent still cancellable
  8.  option rows are not deleted — just marked cancelled
  9.  audit event is written on apply
  10. second run (idempotent) exits 0 with no new audit rows
  11. no external API calls (bePaid, MoyKlass, Telegram)
  12. default list_payment_intents hides cancelled (exclude_cancelled)
  13. explicit status=cancelled filter shows cancelled intents
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage

SCRIPT_PATH = str(ROOT / "scripts" / "archive_test_payment_intents.py")

import importlib.util as _ilu


def _script_mod():
    spec = _ilu.spec_from_file_location("archive_script", SCRIPT_PATH)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    _SCRIPT = _script_mod()
    ALLOWLIST = _SCRIPT.ALLOWLIST
    PROTECTED = _SCRIPT.PROTECTED
    CANCEL_REASON = _SCRIPT.CANCEL_REASON
    HARD_BLOCK_STATUSES = _SCRIPT.HARD_BLOCK_STATUSES
    _run = _SCRIPT.run
    _safety_checks = _SCRIPT._safety_checks
except Exception as _e:
    raise ImportError(f"Cannot load archive script: {_e}") from _e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_storage() -> Storage:
    """Create a temp SQLite db; delete=False avoids Windows file-lock on cleanup."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _seed_intent(storage: Storage, status: str = "bepaid_created",
                 amount_minor: int = 100) -> dict:
    """Create intent via storage API; returns the dict with generated public_id."""
    pi = storage.create_payment_intent({
        "mk_user_id": 9999,
        "student_name": "Test Student",
        "amount_minor": amount_minor,
        "amount_byn": amount_minor / 100,
        "currency": "BYN",
        "purpose": "current_month",
        "period_month": "2026-07",
        "payment_method": "erip",
        "created_by_tg_id": 1,
        "created_by_name": "test_script",
    })
    # create_payment_intent always starts as 'draft'; update to desired status
    if status != "draft":
        storage.payment_intent_update_status(pi["public_id"], status)
        pi = storage.get_payment_intent(pi["public_id"])
    return pi


def _seed_option(storage: Storage, pi: dict, channel: str = "erip",
                 status: str = "active") -> dict:
    """Create a payment option via the storage API."""
    opt = storage.create_payment_intent_option(
        payment_intent_id=int(pi["id"]),
        intent_public_id=pi["public_id"],
        channel=channel,
        shop_type=channel,
    )
    if status not in ("created", opt.get("status")):
        with storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intent_options SET status=? WHERE id=?",
                (status, opt["id"]),
            )
        opt["status"] = status
    return opt


def _seed_bepaid_tx(storage: Storage, intent_public_id: str,
                    tx_status: str = "failed", webhook_verified: int = 0) -> int:
    with storage._connect() as conn:
        cur = conn.execute(
            """INSERT INTO bepaid_transactions
               (provider, shop_type, transaction_uid, status, amount_minor, currency,
                test, received_at, updated_at, intent_public_id, webhook_verified, match_status)
               VALUES ('bepaid','acquiring','uid-test-001',?,100,'BYN',
                       0, datetime('now'), datetime('now'), ?, ?, 'no_match')""",
            (tx_status, intent_public_id, webhook_verified),
        )
        return cur.lastrowid


def _count_audit(storage: Storage, intent_public_id: str, event_type: str) -> int:
    with storage._connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM payment_webhook_audit WHERE intent_public_id=? AND event_type=?",
            (intent_public_id, event_type),
        ).fetchone()
    return int(row[0])


def _quiet_run(apply: bool, storage: Storage, allowlist: list[str]):
    """Run the script with stdout suppressed and ALLOWLIST patched."""
    with patch.object(_SCRIPT, "ALLOWLIST", allowlist):
        with redirect_stdout(io.StringIO()):
            return _run(apply=apply, storage=storage)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class Test01Allowlist(unittest.TestCase):
    """1-2. ALLOWLIST and PROTECTED constants."""

    def test_01_allowlist_exact_members(self):
        expected = {
            "ycpi_202607_9", "ycpi_202607_10", "ycpi_202607_11",
            "ycpi_202607_12", "ycpi_202607_13",
        }
        self.assertEqual(set(ALLOWLIST), expected)

    def test_02_allowlist_no_duplicates(self):
        self.assertEqual(len(ALLOWLIST), len(set(ALLOWLIST)))

    def test_03_protected_not_in_allowlist(self):
        self.assertNotIn(PROTECTED, ALLOWLIST)

    def test_04_protected_is_ycpi_202607_14(self):
        self.assertEqual(PROTECTED, "ycpi_202607_14")

    def test_05_cancel_reason_is_test_cleanup(self):
        self.assertEqual(CANCEL_REASON, "test_cleanup")


class Test02SafetyAbort(unittest.TestCase):
    """3. _safety_checks() aborts when PROTECTED is in allowlist."""

    def test_06_aborts_if_protected_in_allowlist(self):
        with patch.object(_SCRIPT, "ALLOWLIST", [PROTECTED, "ycpi_202607_9"]):
            with patch.object(_SCRIPT, "PROTECTED", PROTECTED):
                with self.assertRaises(SystemExit) as cm:
                    _safety_checks()
                self.assertEqual(cm.exception.code, 2)

    def test_07_passes_with_valid_allowlist(self):
        with patch.object(_SCRIPT, "ALLOWLIST", ALLOWLIST):
            with patch.object(_SCRIPT, "PROTECTED", PROTECTED):
                try:
                    _safety_checks()
                except SystemExit:
                    self.fail("_safety_checks() raised SystemExit on a valid allowlist")


class Test03DryRun(unittest.TestCase):
    """4. dry-run leaves DB unchanged."""

    def test_08_dry_run_no_db_changes(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="bepaid_created")
        pid = pi["public_id"]
        exit_code = _quiet_run(apply=False, storage=storage, allowlist=[pid])
        self.assertEqual(exit_code, 0)
        pi_after = storage.get_payment_intent(pid)
        self.assertEqual(pi_after["status"], "bepaid_created")

    def test_09_dry_run_no_audit_written(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="bepaid_created")
        pid = pi["public_id"]
        _quiet_run(apply=False, storage=storage, allowlist=[pid])
        count = _count_audit(storage, pid, "test_intent_cancelled")
        self.assertEqual(count, 0, "dry-run must not write audit events")


class Test04Apply(unittest.TestCase):
    """5. --apply cancels only allowlist intents."""

    def test_10_apply_cancels_intent(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="bepaid_created")
        pid = pi["public_id"]
        exit_code = _quiet_run(apply=True, storage=storage, allowlist=[pid])
        self.assertEqual(exit_code, 0)
        pi_after = storage.get_payment_intent(pid)
        self.assertEqual(pi_after["status"], "cancelled")
        self.assertEqual(pi_after["cancel_reason"], "test_cleanup")
        self.assertIsNotNone(pi_after["cancelled_at"])

    def test_11_apply_does_not_touch_non_allowlist_intent(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="bepaid_created")
        pi_other = _seed_intent(storage, status="draft")
        pid = pi["public_id"]
        _quiet_run(apply=True, storage=storage, allowlist=[pid])
        pi_other_after = storage.get_payment_intent(pi_other["public_id"])
        self.assertEqual(pi_other_after["status"], "draft")

    def test_12_apply_writes_audit_event(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="bepaid_created")
        pid = pi["public_id"]
        _quiet_run(apply=True, storage=storage, allowlist=[pid])
        count = _count_audit(storage, pid, "test_intent_cancelled")
        self.assertGreaterEqual(count, 1)


class Test05PaidBlocking(unittest.TestCase):
    """6. paid / posted / verified-tx intents block run and return exit code 1."""

    def test_13_paid_intent_blocks(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="paid")
        pid = pi["public_id"]
        exit_code = _quiet_run(apply=True, storage=storage, allowlist=[pid])
        self.assertEqual(exit_code, 1)
        self.assertEqual(storage.get_payment_intent(pid)["status"], "paid")

    def test_14_posted_to_moyklass_blocks(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="posted_to_moyklass")
        pid = pi["public_id"]
        exit_code = _quiet_run(apply=True, storage=storage, allowlist=[pid])
        self.assertEqual(exit_code, 1)

    def test_15_verified_successful_tx_blocks(self):
        """Webhook-verified successful tx attached to intent blocks cancellation."""
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="bepaid_created")
        pid = pi["public_id"]
        _seed_bepaid_tx(storage, pid, tx_status="successful", webhook_verified=1)
        exit_code = _quiet_run(apply=True, storage=storage, allowlist=[pid])
        self.assertEqual(exit_code, 1)
        self.assertNotEqual(storage.get_payment_intent(pid)["status"], "cancelled")


class Test06FailedTransaction(unittest.TestCase):
    """7. Failed transaction is preserved; parent intent can still be cancelled."""

    def test_16_failed_tx_preserved_after_cancel(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="bepaid_requires_check")
        pid = pi["public_id"]
        _seed_bepaid_tx(storage, pid, tx_status="failed", webhook_verified=0)
        exit_code = _quiet_run(apply=True, storage=storage, allowlist=[pid])
        self.assertEqual(exit_code, 0)
        txs = storage.get_bepaid_transactions_for_intent(pid)
        self.assertEqual(len(txs), 1, "bepaid_transaction row must not be deleted")
        self.assertEqual(txs[0]["status"], "failed")
        self.assertEqual(storage.get_payment_intent(pid)["status"], "cancelled")


class Test07OptionsPreserved(unittest.TestCase):
    """8. Option rows are not deleted — only marked cancelled."""

    def test_17_active_option_marked_cancelled_not_deleted(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="bepaid_created")
        _seed_option(storage, pi, channel="erip", status="active")
        pid = pi["public_id"]
        _quiet_run(apply=True, storage=storage, allowlist=[pid])
        opts = storage.get_options_for_intent(pid)
        self.assertEqual(len(opts), 1, "option row must not be deleted")
        self.assertEqual(opts[0]["status"], "cancelled")

    def test_18_paid_option_not_touched(self):
        """Option already marked 'paid' is left untouched."""
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="bepaid_requires_check")
        _seed_option(storage, pi, channel="erip", status="paid")
        pid = pi["public_id"]
        _quiet_run(apply=True, storage=storage, allowlist=[pid])
        opts = storage.get_options_for_intent(pid)
        paid_opts = [o for o in opts if o["status"] == "paid"]
        self.assertEqual(len(paid_opts), 1, "paid option must remain paid")


class Test08Idempotency(unittest.TestCase):
    """10. Second run is idempotent: exits 0, no duplicate audit rows."""

    def test_19_second_apply_exits_0(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="bepaid_created")
        pid = pi["public_id"]
        _quiet_run(apply=True, storage=storage, allowlist=[pid])
        exit_code = _quiet_run(apply=True, storage=storage, allowlist=[pid])
        self.assertEqual(exit_code, 0)

    def test_20_no_duplicate_audit_on_second_run(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="bepaid_created")
        pid = pi["public_id"]
        _quiet_run(apply=True, storage=storage, allowlist=[pid])
        count_first = _count_audit(storage, pid, "test_intent_cancelled")
        _quiet_run(apply=True, storage=storage, allowlist=[pid])
        count_second = _count_audit(storage, pid, "test_intent_cancelled")
        self.assertEqual(count_first, count_second)


class Test09NoExternalCalls(unittest.TestCase):
    """11. No external API calls during apply."""

    def test_21_no_http_requests(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="bepaid_created")
        with patch("requests.post") as mock_post, \
             patch("requests.get") as mock_get:
            _quiet_run(apply=True, storage=storage, allowlist=[pi["public_id"]])
            mock_post.assert_not_called()
            mock_get.assert_not_called()

    def test_22_no_urllib_calls(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="bepaid_created")
        with patch("urllib.request.urlopen") as mock_url:
            _quiet_run(apply=True, storage=storage, allowlist=[pi["public_id"]])
            mock_url.assert_not_called()


class Test10ListFiltering(unittest.TestCase):
    """12-13. list_payment_intents exclude_cancelled behaviour."""

    def test_23_exclude_cancelled_hides_cancelled(self):
        storage = _tmp_storage()
        pi_cancelled = _seed_intent(storage, status="cancelled")
        pi_active = _seed_intent(storage, status="bepaid_created")
        items = storage.list_payment_intents(exclude_cancelled=True)
        public_ids = [i["public_id"] for i in items]
        self.assertNotIn(pi_cancelled["public_id"], public_ids)
        self.assertIn(pi_active["public_id"], public_ids)

    def test_24_explicit_cancelled_filter_shows_cancelled(self):
        storage = _tmp_storage()
        pi_cancelled = _seed_intent(storage, status="cancelled")
        pi_active = _seed_intent(storage, status="bepaid_created")
        items = storage.list_payment_intents(status="cancelled")
        public_ids = [i["public_id"] for i in items]
        self.assertIn(pi_cancelled["public_id"], public_ids)
        self.assertNotIn(pi_active["public_id"], public_ids)

    def test_25_default_no_exclude_returns_all(self):
        """Without exclude_cancelled, all statuses returned (default preserved)."""
        storage = _tmp_storage()
        pi_cancelled = _seed_intent(storage, status="cancelled")
        pi_active = _seed_intent(storage, status="bepaid_created")
        items = storage.list_payment_intents()
        public_ids = [i["public_id"] for i in items]
        self.assertIn(pi_cancelled["public_id"], public_ids)
        self.assertIn(pi_active["public_id"], public_ids)


class Test11StorageMethods(unittest.TestCase):
    """Direct unit tests for the three new storage methods."""

    def test_26_cleanup_method_blocks_paid(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="paid")
        result = storage.cancel_payment_intent_for_cleanup(
            pi["public_id"], "test", "2026-07-15T00:00:00"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "blocked_paid_or_posted")

    def test_27_cleanup_method_idempotent_on_already_cancelled(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="cancelled")
        result = storage.cancel_payment_intent_for_cleanup(
            pi["public_id"], "test", "2026-07-15T00:00:00"
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("idempotent"))

    def test_28_cancel_options_marks_not_deletes(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage, status="bepaid_created")
        _seed_option(storage, pi, channel="erip", status="active")
        changed = storage.cancel_options_for_cleanup(pi["public_id"], "2026-07-15T00:00:00")
        self.assertEqual(changed, 1)
        opts = storage.get_options_for_intent(pi["public_id"])
        self.assertEqual(len(opts), 1, "row must not be deleted")
        self.assertEqual(opts[0]["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
