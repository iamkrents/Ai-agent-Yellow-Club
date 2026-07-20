"""
Tests for Storage.audit_payment_integrity() — read-only integrity audit.
v7.0.96.1
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage

CURRENT_VERSION = "7.0.97.0"

_NOW = "2026-07-18T10:00:00"
_MK_USER = 9748998
_COUNTER = [0]


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _uid() -> str:
    _COUNTER[0] += 1
    return f"ycpi_aud_{_COUNTER[0]:04d}"


def _raw_insert(storage: Storage, **kwargs) -> None:
    """Insert a minimal valid payment_intent row directly via SQL."""
    defaults = {
        "public_id": _uid(),
        "mk_user_id": _MK_USER,
        "amount_minor": 100,
        "amount_byn": 1.0,
        "currency": "BYN",
        "purpose": "current_month",
        "payment_method": "erip",
        "status": "draft",
        "source": "manual",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" * len(defaults))
    with storage._connect() as conn:
        conn.execute(
            f"INSERT INTO payment_intents ({cols}) VALUES ({placeholders})",
            list(defaults.values()),
        )


def _make_intent(storage: Storage, **kwargs) -> dict:
    """Create an intent via the public API (for tests that need a real public_id)."""
    defaults = {
        "mk_user_id": _MK_USER,
        "student_name": "Test Student",
        "amount_minor": 500,
        "amount_byn": 5.0,
        "currency": "BYN",
        "purpose": "current_month",
        "payment_method": "erip",
        "status": "draft",
        "source": "manual",
        "source_reference": None,
        "mk_invoice_id": None,
    }
    defaults.update(kwargs)
    return storage.create_payment_intent(defaults)


def _get_codes(issues: list) -> list:
    return [i["code"] if isinstance(i, dict) else i for i in issues]


# ---------------------------------------------------------------------------
# 01–03  Baseline — clean storage
# ---------------------------------------------------------------------------

class TestAuditCleanStorage(unittest.TestCase):

    def setUp(self):
        self.s = _make_storage()

    def test_01_empty_storage_returns_zero_checked(self):
        result = self.s.audit_payment_integrity()
        self.assertEqual(result["checked"], 0)

    def test_02_empty_storage_no_issues(self):
        result = self.s.audit_payment_integrity()
        self.assertFalse(result["critical"])
        self.assertFalse(result["warning"])
        self.assertFalse(result["info"])

    def test_03_result_has_required_keys(self):
        result = self.s.audit_payment_integrity()
        for k in ("checked", "critical", "warning", "info"):
            self.assertIn(k, result)


# ---------------------------------------------------------------------------
# 04–06  Single clean intent — no issues
# ---------------------------------------------------------------------------

class TestAuditSingleCleanIntent(unittest.TestCase):

    def setUp(self):
        self.s = _make_storage()

    def test_04_clean_draft_intent_no_issues(self):
        _raw_insert(self.s)
        result = self.s.audit_payment_integrity()
        self.assertEqual(result["checked"], 1)
        self.assertFalse(result["critical"])
        self.assertFalse(result["warning"])

    def test_05_cancelled_intent_no_issues(self):
        pi = _make_intent(self.s)
        self.s.cancel_payment_intent(pi["public_id"], reason="test", now=_NOW)
        result = self.s.audit_payment_integrity()
        self.assertEqual(result["checked"], 1)
        self.assertFalse(result["critical"])

    def test_06_checked_count_equals_total_intents(self):
        for _ in range(5):
            _raw_insert(self.s)
        result = self.s.audit_payment_integrity()
        self.assertEqual(result["checked"], 5)


# ---------------------------------------------------------------------------
# 07–09  Duplicate active intents per mk_invoice_id
# ---------------------------------------------------------------------------

class TestAuditDuplicateActiveIntents(unittest.TestCase):

    def setUp(self):
        self.s = _make_storage()

    def _mk_intent(self, mk_invoice_id: str) -> dict:
        return _make_intent(
            self.s,
            source="moyklass_invoice_automation",
            mk_invoice_id=mk_invoice_id,
            source_reference=f"mk_invoice_{mk_invoice_id}",
            mk_user_id=_MK_USER,
        )

    def test_07_two_active_intents_same_invoice_critical(self):
        self._mk_intent("19000001")
        # Bypass dedup guard by inserting second intent directly via SQL
        _raw_insert(
            self.s,
            public_id="ycpi_dupe_test",
            source="moyklass_invoice_automation",
            mk_invoice_id="19000001",
            source_reference="mk_invoice_19000001",
            status="ready",
        )
        result = self.s.audit_payment_integrity()
        codes = _get_codes(result["critical"])
        self.assertIn("duplicate_active_intent", codes)

    def test_08_one_cancelled_one_active_same_invoice_no_critical(self):
        pi1 = self._mk_intent("19000002")
        self.s.cancel_payment_intent(pi1["public_id"], reason="test", now=_NOW)
        self._mk_intent("19000002")
        result = self.s.audit_payment_integrity()
        codes = _get_codes(result["critical"])
        self.assertNotIn("duplicate_active_intent", codes)

    def test_09_different_invoices_no_critical(self):
        self._mk_intent("19000003")
        self._mk_intent("19000004")
        result = self.s.audit_payment_integrity()
        codes = _get_codes(result["critical"])
        self.assertNotIn("duplicate_active_intent", codes)


# ---------------------------------------------------------------------------
# 10–12  posted_to_moyklass without mk_payment_id
# ---------------------------------------------------------------------------

class TestAuditPostedNoMkPaymentId(unittest.TestCase):

    def setUp(self):
        self.s = _make_storage()

    def test_10_posted_without_mk_payment_id_is_critical(self):
        _raw_insert(self.s, status="posted_to_moyklass")
        result = self.s.audit_payment_integrity()
        codes = _get_codes(result["critical"])
        self.assertIn("posted_no_mk_payment_id", codes)

    def test_11_posted_with_mk_payment_id_no_posted_critical(self):
        _raw_insert(
            self.s,
            status="posted_to_moyklass",
            mk_payment_id=12345,
            mk_posting_status="posted",
            paid_channel="erip",
            webhook_verified=1,
            paid_transaction_uid="tx-ok-11",
        )
        result = self.s.audit_payment_integrity()
        codes = _get_codes(result["critical"])
        self.assertNotIn("posted_no_mk_payment_id", codes)

    def test_12_mk_posting_status_posted_without_payment_id_is_critical(self):
        _raw_insert(self.s, status="paid", mk_posting_status="posted")
        result = self.s.audit_payment_integrity()
        codes = _get_codes(result["critical"])
        self.assertIn("posting_status_posted_no_id", codes)


# ---------------------------------------------------------------------------
# 13–15  webhook_verified without paid_transaction_uid
# ---------------------------------------------------------------------------

class TestAuditVerifiedNoTxUid(unittest.TestCase):

    def setUp(self):
        self.s = _make_storage()

    def test_13_verified_without_tx_uid_is_critical(self):
        _raw_insert(self.s, status="paid", webhook_verified=1)
        result = self.s.audit_payment_integrity()
        codes = _get_codes(result["critical"])
        self.assertIn("verified_no_tx_uid", codes)

    def test_14_verified_with_tx_uid_no_verified_critical(self):
        _raw_insert(
            self.s,
            status="paid",
            webhook_verified=1,
            paid_transaction_uid="tx-abc-123",
            paid_channel="erip",
        )
        result = self.s.audit_payment_integrity()
        codes = _get_codes(result["critical"])
        self.assertNotIn("verified_no_tx_uid", codes)

    def test_15_paid_awaiting_payment_is_critical(self):
        _raw_insert(
            self.s,
            status="awaiting_payment",
            paid_transaction_uid="tx-stale",
            paid_at=_NOW,
        )
        result = self.s.audit_payment_integrity()
        codes = _get_codes(result["critical"])
        self.assertIn("paid_but_awaiting", codes)


# ---------------------------------------------------------------------------
# 16–18  Warnings
# ---------------------------------------------------------------------------

class TestAuditWarnings(unittest.TestCase):

    def setUp(self):
        self.s = _make_storage()

    def test_16_paid_no_channel_is_warning(self):
        _raw_insert(
            self.s,
            status="paid",
            webhook_verified=1,
            paid_transaction_uid="tx-noc",
        )
        result = self.s.audit_payment_integrity()
        codes = _get_codes(result["warning"])
        self.assertIn("paid_no_channel", codes)

    def test_17_source_reference_mismatch_is_warning(self):
        _raw_insert(
            self.s,
            source="moyklass_invoice_automation",
            mk_invoice_id="19000099",
            source_reference="wrong_ref",
        )
        result = self.s.audit_payment_integrity()
        codes = _get_codes(result["warning"])
        self.assertIn("source_reference_mismatch", codes)

    def test_18_automation_without_mk_invoice_id_is_critical(self):
        _raw_insert(self.s, source="moyklass_invoice_automation")
        result = self.s.audit_payment_integrity()
        codes = _get_codes(result["critical"])
        self.assertIn("automation_no_mk_invoice_id", codes)


# ---------------------------------------------------------------------------
# 19–20  Info: channel/method mismatch
# ---------------------------------------------------------------------------

class TestAuditInfoChannelMismatch(unittest.TestCase):

    def setUp(self):
        self.s = _make_storage()

    def test_19_erip_method_acquiring_channel_is_info(self):
        # Mirrors ycpi_202607_19: erip intent paid via acquiring
        _raw_insert(
            self.s,
            source="moyklass_invoice_automation",
            status="posted_to_moyklass",
            payment_method="erip",
            paid_channel="acquiring",
            webhook_verified=1,
            paid_transaction_uid="tx-info",
        )
        result = self.s.audit_payment_integrity()
        codes = _get_codes(result["info"])
        self.assertIn("channel_method_mismatch", codes)

    def test_20_same_method_and_channel_no_mismatch_info(self):
        _raw_insert(
            self.s,
            status="paid",
            payment_method="erip",
            paid_channel="erip",
            webhook_verified=1,
            paid_transaction_uid="tx-same",
        )
        result = self.s.audit_payment_integrity()
        codes = _get_codes(result["info"])
        self.assertNotIn("channel_method_mismatch", codes)


# ---------------------------------------------------------------------------
# 21–22  Production fixture ycpi_202607_19 — must pass without critical/warning
# ---------------------------------------------------------------------------

class TestAuditProductionFixture(unittest.TestCase):

    def setUp(self):
        self.s = _make_storage()
        _raw_insert(
            self.s,
            public_id="ycpi_202607_19",
            source="moyklass_invoice_automation",
            source_reference="mk_invoice_19102120",
            status="posted_to_moyklass",
            amount_minor=100,
            amount_byn=1.0,
            payment_method="erip",
            bepaid_shop_type="erip",
            paid_channel="acquiring",
            webhook_verified=1,
            paid_transaction_uid="tx-prod-19102120",
            paid_at=_NOW,
            paid_amount_minor=100,
            mk_invoice_id="19102120",
            mk_user_id=9748998,
            mk_user_subscription_id="18037719",
            mk_payment_id=37473176,
            mk_posting_status="posted",
        )

    def test_21_production_fixture_no_critical(self):
        result = self.s.audit_payment_integrity()
        self.assertEqual(result["checked"], 1)
        self.assertFalse(result["critical"],
                         msg=f"Unexpected critical: {result['critical']}")

    def test_22_production_fixture_no_warning(self):
        result = self.s.audit_payment_integrity()
        self.assertFalse(result["warning"],
                         msg=f"Unexpected warning: {result['warning']}")


if __name__ == "__main__":
    unittest.main()
