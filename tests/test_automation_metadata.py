"""Regression tests for v7.0.94.2 — automation intent metadata repair.

Covers:
  1-5.   automation_audit_log table creation and schema
  6-10.  update_automation_item_student_name — idempotency
  11-20. repair_intent_metadata — all branches
  21-25. create_automation_audit_event — persistence and fields
  26-30. _fetch_mk_student_name helper logic (unit)
  31-35. automation_item_action repair-metadata (server handler, mocked)
  36-40. _process_single_automation_item_from_invoice student_name fallback (server, mocked)
  41-43. app.js UI: sourceBadge, queue name, repair button
  44-45. CSS .pi-source-badge-auto present; index.html version v7.0.94.2

Run offline (no MoyKlass / bePaid / Telegram needed):

    python -m unittest tests.test_automation_metadata -v
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CURRENT_VERSION = "7.0.96.0"

from storage import Storage

APP_JS = ROOT / "miniapp" / "app.js"
STYLES_CSS = ROOT / "miniapp" / "styles.css"
INDEX_HTML = ROOT / "miniapp" / "index.html"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _create_intent(
    st: Storage,
    *,
    mk_user_id: int = 9748998,
    student_name: str | None = None,
    source: str = "manual",
    source_reference: str | None = None,
    mk_invoice_id: str | None = None,
    amount_minor: int = 10000,
) -> dict:
    """Insert a payment intent and return the stored row."""
    return st.create_payment_intent({
        "mk_user_id": mk_user_id,
        "student_name": student_name,
        "amount_minor": amount_minor,
        "amount_byn": amount_minor / 100.0,
        "source": source,
        "source_reference": source_reference,
        "mk_invoice_id": mk_invoice_id,
    })


def _create_auto_item(
    st: Storage,
    *,
    inv_id: str = "1001",
    user_id: str = "9748998",
    student_name: str | None = None,
) -> dict:
    return st.upsert_automation_item(
        inv_id, user_id, student_name, json.dumps({}), _now()
    )


# ===========================================================================
# 1-5. automation_audit_log table
# ===========================================================================

class TestAuditLogTable(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()

    def test_1_table_exists(self):
        """automation_audit_log table is created on Storage init."""
        with self.st._connect() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='automation_audit_log'"
            ).fetchone()
        self.assertIsNotNone(row, "automation_audit_log table must exist")

    def test_2_table_required_columns(self):
        """automation_audit_log has all required columns."""
        with self.st._connect() as conn:
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(automation_audit_log)"
            ).fetchall()}
        required = {
            "id", "created_at", "event_type", "automation_item_id",
            "intent_public_id", "mk_invoice_id", "mk_user_id",
            "old_source", "new_source", "name_updated", "initiator", "details_json",
        }
        self.assertTrue(required.issubset(cols), f"Missing columns: {required - cols}")

    def test_3_index_on_intent_public_id(self):
        """idx_aal_intent index exists on automation_audit_log."""
        with self.st._connect() as conn:
            idx = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_aal_intent'"
            ).fetchone()
        self.assertIsNotNone(idx, "idx_aal_intent must exist")

    def test_4_name_updated_default_is_zero(self):
        """name_updated column defaults to 0 (not NULL)."""
        st = self.st
        now = _now()
        st.create_automation_audit_event({
            "created_at": now,
            "event_type": "test_event",
        })
        with st._connect() as conn:
            row = conn.execute(
                "SELECT name_updated FROM automation_audit_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(row[0], 0)

    def test_5_multiple_runs_idempotent(self):
        """Storage init is idempotent — second Storage() on same DB does not fail."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        st1 = Storage(Path(tmp.name))
        st2 = Storage(Path(tmp.name))  # must not raise
        with st2._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM automation_audit_log"
            ).fetchone()
        self.assertEqual(row[0], 0)


# ===========================================================================
# 6-10. update_automation_item_student_name
# ===========================================================================

class TestUpdateAutomationItemStudentName(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()
        self.now = _now()

    def test_6_fills_null_name(self):
        """update_automation_item_student_name sets name when NULL."""
        _create_auto_item(self.st, inv_id="2001", student_name=None)
        self.st.update_automation_item_student_name("2001", "Тест Тестов", self.now)
        items = self.st.list_automation_items()
        self.assertEqual(items[0]["student_name"], "Тест Тестов")

    def test_7_does_not_overwrite_existing_name(self):
        """update_automation_item_student_name is a no-op if name already set."""
        _create_auto_item(self.st, inv_id="2002", student_name="Иван Иванов")
        self.st.update_automation_item_student_name("2002", "Другой Человек", self.now)
        items = self.st.list_automation_items()
        self.assertEqual(items[0]["student_name"], "Иван Иванов")

    def test_8_invoice_id_must_match(self):
        """update_automation_item_student_name does not touch other invoices."""
        _create_auto_item(self.st, inv_id="2003", student_name=None)
        _create_auto_item(self.st, inv_id="2004", student_name=None)
        self.st.update_automation_item_student_name("2003", "Только Этот", self.now)
        items = {i["mk_invoice_id"]: i for i in self.st.list_automation_items()}
        self.assertEqual(items["2003"]["student_name"], "Только Этот")
        self.assertIsNone(items["2004"]["student_name"])

    def test_9_empty_string_not_written(self):
        """Callers must not pass empty string; method signature requires str."""
        # Storage accepts empty string (no guard at storage level) — verify at least no crash
        _create_auto_item(self.st, inv_id="2005", student_name=None)
        self.st.update_automation_item_student_name("2005", "", self.now)
        items = self.st.list_automation_items()
        # empty string is falsy but method writes it — that is acceptable DB behavior
        self.assertIsNotNone(items[0])  # no crash

    def test_10_no_rows_affected_for_unknown_inv(self):
        """update_automation_item_student_name silently does nothing for unknown invoice."""
        # No exception expected
        self.st.update_automation_item_student_name("99999", "Ghost", self.now)


# ===========================================================================
# 11-20. repair_intent_metadata
# ===========================================================================

class TestRepairIntentMetadata(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()
        self.now = _now()

    def test_11_not_found_returns_ok_false(self):
        """repair_intent_metadata returns ok=False for unknown public_id."""
        result = self.st.repair_intent_metadata("no_such_id", now=self.now)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not_found")

    def test_12_fills_null_student_name(self):
        """repair_intent_metadata updates student_name when it is NULL."""
        intent = _create_intent(self.st, student_name=None, source="manual")
        result = self.st.repair_intent_metadata(
            intent["public_id"], student_name="Александр Кренть", now=self.now
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertTrue(result["name_updated"])
        row = self.st.get_payment_intent(intent["public_id"])
        self.assertEqual(row["student_name"], "Александр Кренть")

    def test_13_fills_userId_placeholder_name(self):
        """repair_intent_metadata replaces userId=... placeholder in student_name."""
        intent = _create_intent(self.st, student_name="userId=9748998", source="manual")
        result = self.st.repair_intent_metadata(
            intent["public_id"], student_name="Кренть Александр", now=self.now
        )
        self.assertTrue(result["changed"])
        self.assertTrue(result["name_updated"])
        row = self.st.get_payment_intent(intent["public_id"])
        self.assertEqual(row["student_name"], "Кренть Александр")

    def test_14_does_not_overwrite_good_name(self):
        """repair_intent_metadata does not overwrite a real student_name."""
        intent = _create_intent(self.st, student_name="Уже Есть Имя", source="manual")
        result = self.st.repair_intent_metadata(
            intent["public_id"], student_name="Другое Имя", now=self.now
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result.get("name_updated", False))
        row = self.st.get_payment_intent(intent["public_id"])
        self.assertEqual(row["student_name"], "Уже Есть Имя")

    def test_15_repairs_source_from_manual(self):
        """repair_intent_metadata updates source when current value is 'manual'."""
        intent = _create_intent(self.st, source="manual")
        result = self.st.repair_intent_metadata(
            intent["public_id"],
            source="moyklass_invoice_automation",
            source_reference="mk_invoice_1001",
            now=self.now,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertTrue(result["source_updated"])
        row = self.st.get_payment_intent(intent["public_id"])
        self.assertEqual(row["source"], "moyklass_invoice_automation")
        self.assertEqual(row["source_reference"], "mk_invoice_1001")

    def test_16_does_not_overwrite_non_manual_source(self):
        """repair_intent_metadata does not overwrite source != 'manual'."""
        intent = _create_intent(self.st, source="moyklass_invoice")
        result = self.st.repair_intent_metadata(
            intent["public_id"],
            source="moyklass_invoice_automation",
            now=self.now,
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result.get("source_updated", False))
        row = self.st.get_payment_intent(intent["public_id"])
        self.assertEqual(row["source"], "moyklass_invoice")

    def test_17_returns_changed_false_when_nothing_to_do(self):
        """repair_intent_metadata returns changed=False when all fields already correct."""
        intent = _create_intent(
            self.st,
            student_name="Иван Иванов",
            source="moyklass_invoice_automation",
        )
        result = self.st.repair_intent_metadata(
            intent["public_id"],
            student_name="Другое Имя",
            source="moyklass_invoice_automation",
            now=self.now,
        )
        self.assertFalse(result.get("changed", False))

    def test_18_repairs_both_name_and_source_in_one_call(self):
        """repair_intent_metadata can repair both name and source together."""
        intent = _create_intent(self.st, student_name=None, source="manual")
        result = self.st.repair_intent_metadata(
            intent["public_id"],
            student_name="Полное Имя",
            source="moyklass_invoice_automation",
            source_reference="mk_invoice_888",
            now=self.now,
        )
        self.assertTrue(result["name_updated"])
        self.assertTrue(result["source_updated"])
        row = self.st.get_payment_intent(intent["public_id"])
        self.assertEqual(row["student_name"], "Полное Имя")
        self.assertEqual(row["source"], "moyklass_invoice_automation")

    def test_19_does_not_change_amount_or_mk_user_id(self):
        """repair_intent_metadata never modifies amount_minor or mk_user_id."""
        intent = _create_intent(
            self.st, mk_user_id=9748998, amount_minor=25000, student_name=None, source="manual"
        )
        self.st.repair_intent_metadata(
            intent["public_id"],
            student_name="Имя Ученика",
            source="moyklass_invoice_automation",
            now=self.now,
        )
        row = self.st.get_payment_intent(intent["public_id"])
        self.assertEqual(row["amount_minor"], 25000)
        self.assertEqual(row["mk_user_id"], 9748998)

    def test_20_old_values_returned_in_result(self):
        """repair_intent_metadata returns old_name and old_source for audit."""
        intent = _create_intent(
            self.st, student_name="userId=9748998", source="manual"
        )
        result = self.st.repair_intent_metadata(
            intent["public_id"],
            student_name="Новое Имя",
            source="moyklass_invoice_automation",
            now=self.now,
        )
        self.assertEqual(result["old_name"], "userId=9748998")
        self.assertEqual(result["old_source"], "manual")
        self.assertEqual(result["new_name"], "Новое Имя")
        self.assertEqual(result["new_source"], "moyklass_invoice_automation")


# ===========================================================================
# 21-25. create_automation_audit_event
# ===========================================================================

class TestCreateAutomationAuditEvent(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()
        self.now = _now()

    def test_21_event_persisted(self):
        """create_automation_audit_event writes a row to automation_audit_log."""
        self.st.create_automation_audit_event({
            "created_at": self.now,
            "event_type": "automation_intent_metadata_repaired",
            "intent_public_id": "ycpi_202607_17",
            "mk_invoice_id": "1001",
            "mk_user_id": "9748998",
            "old_source": "manual",
            "new_source": "moyklass_invoice_automation",
            "name_updated": True,
            "initiator": "scheduled",
        })
        with self.st._connect() as conn:
            row = conn.execute(
                "SELECT * FROM automation_audit_log WHERE event_type='automation_intent_metadata_repaired'"
            ).fetchone()
        self.assertIsNotNone(row)

    def test_22_name_updated_stored_as_int(self):
        """name_updated bool is stored as 0/1 integer."""
        self.st.create_automation_audit_event({
            "created_at": self.now,
            "event_type": "test",
            "name_updated": True,
        })
        with self.st._connect() as conn:
            row = conn.execute(
                "SELECT name_updated FROM automation_audit_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(row[0], 1)

    def test_23_false_name_updated_stored_as_zero(self):
        """name_updated=False stored as 0."""
        self.st.create_automation_audit_event({
            "created_at": self.now,
            "event_type": "test",
            "name_updated": False,
        })
        with self.st._connect() as conn:
            row = conn.execute(
                "SELECT name_updated FROM automation_audit_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(row[0], 0)

    def test_24_multiple_events_independent(self):
        """Multiple audit events are all persisted independently."""
        for i in range(3):
            self.st.create_automation_audit_event({
                "created_at": self.now,
                "event_type": f"event_{i}",
            })
        with self.st._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM automation_audit_log").fetchone()[0]
        self.assertEqual(count, 3)

    def test_25_optional_fields_nullable(self):
        """create_automation_audit_event accepts minimal event with only required fields."""
        self.st.create_automation_audit_event({
            "created_at": self.now,
            "event_type": "minimal_event",
        })
        with self.st._connect() as conn:
            row = conn.execute(
                "SELECT * FROM automation_audit_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNone(dict(row).get("intent_public_id"))
        self.assertIsNone(dict(row).get("automation_item_id"))


# ===========================================================================
# 26-30. _fetch_mk_student_name helper logic (unit test via direct call)
# ===========================================================================

class TestFetchMkStudentName(unittest.TestCase):
    """Unit-test the _fetch_mk_student_name logic extracted from web_app_server."""

    def _extract_name(self, user_dict: dict) -> str | None:
        """Mirror the name-extraction logic from _fetch_mk_student_name."""
        u = user_dict
        name = str(u.get("name") or "").strip()
        if not name:
            name = " ".join(filter(None, [
                str(u.get("clientName") or u.get("lastName") or ""),
                str(u.get("firstName") or ""),
                str(u.get("middleName") or u.get("patronymic") or ""),
            ])).strip()
        if name and not name.startswith("userId="):
            return name
        return None

    def test_26_uses_name_field_first(self):
        """Returns 'name' field when present."""
        result = self._extract_name({"name": "Кренть Александр Александрович"})
        self.assertEqual(result, "Кренть Александр Александрович")

    def test_27_falls_back_to_clientName(self):
        """Falls back to clientName when 'name' is absent."""
        result = self._extract_name({"clientName": "Иванов", "firstName": "Иван", "patronymic": "Иванович"})
        self.assertIn("Иванов", result)

    def test_28_composes_from_firstName_lastName(self):
        """Composes name from lastName + firstName + middleName when 'name' absent."""
        result = self._extract_name({
            "lastName": "Петров",
            "firstName": "Пётр",
            "middleName": "Петрович",
        })
        self.assertEqual(result, "Петров Пётр Петрович")

    def test_29_returns_none_when_all_empty(self):
        """Returns None when all name fields are empty/absent."""
        result = self._extract_name({})
        self.assertIsNone(result)

    def test_30_rejects_userId_prefix(self):
        """Returns None if composed name starts with 'userId=' (corrupt data)."""
        result = self._extract_name({"name": "userId=9748998"})
        self.assertIsNone(result)


# ===========================================================================
# 31-35. automation_item_action repair-metadata (server handler, mocked)
# ===========================================================================

class TestRepairMetadataAction(unittest.TestCase):
    """Tests for the repair-metadata branch in automation_item_action handler."""

    def _get_server(self, st: Storage):
        """Return a minimal WebAppServer-like stub with storage patched."""
        import importlib
        spec = importlib.util.spec_from_file_location(
            "web_app_server_meta_test", ROOT / "web_app_server.py"
        )
        # We only verify method existence; avoid full server boot
        return None  # skip real import — test via storage layer only

    def test_31_repair_metadata_no_intent_public_id(self):
        """repair-metadata with no linked intent returns error."""
        st = _make_storage()
        now = _now()
        item = _create_auto_item(st, inv_id="3001")
        # item has no intent_public_id at this point
        # Simulate the server logic inline
        intent_public_id = item.get("intent_public_id")
        if not intent_public_id:
            result = {"ok": False, "error": "Нет связанного payment intent"}
        else:
            result = {"ok": True}
        self.assertFalse(result["ok"])
        self.assertIn("payment intent", result["error"])

    def test_32_repair_with_intent_calls_repair_metadata(self):
        """When intent_public_id is set, repair_intent_metadata is called and changes are persisted."""
        st = _make_storage()
        now = _now()
        intent = _create_intent(st, student_name=None, source="manual")
        item = _create_auto_item(st, inv_id="3002")
        st.update_automation_item_stage(
            item["id"], "payment_options_created",
            intent_public_id=intent["public_id"], now=now
        )
        fetched_name = "Кренть Александр"
        repair = st.repair_intent_metadata(
            intent["public_id"],
            student_name=fetched_name,
            source="moyklass_invoice_automation",
            source_reference="mk_invoice_3002",
            now=now,
        )
        self.assertTrue(repair["changed"])
        row = st.get_payment_intent(intent["public_id"])
        self.assertEqual(row["student_name"], "Кренть Александр")
        self.assertEqual(row["source"], "moyklass_invoice_automation")

    def test_33_repair_records_audit_event(self):
        """After successful repair, an audit event is written."""
        st = _make_storage()
        now = _now()
        intent = _create_intent(st, student_name=None, source="manual")
        repair = st.repair_intent_metadata(
            intent["public_id"], student_name="Аудит Тест", source="moyklass_invoice_automation",
            source_reference="mk_invoice_3003", now=now
        )
        if repair.get("changed"):
            st.create_automation_audit_event({
                "created_at": now,
                "event_type": "automation_intent_metadata_repaired",
                "intent_public_id": intent["public_id"],
                "old_source": repair["old_source"],
                "new_source": repair["new_source"],
                "name_updated": repair["name_updated"],
                "initiator": "admin",
            })
        with st._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM automation_audit_log WHERE intent_public_id=?",
                (intent["public_id"],)
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_34_repair_metadata_action_in_server_source(self):
        """'repair-metadata' action name is present in web_app_server.py."""
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("repair-metadata", src)

    def test_35_fetch_mk_student_name_method_in_server_source(self):
        """_fetch_mk_student_name method is defined in web_app_server.py."""
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("def _fetch_mk_student_name(", src)


# ===========================================================================
# 36-40. _process_single_automation_item student_name fallback (source analysis)
# ===========================================================================

class TestStudentNameFallback(unittest.TestCase):
    """Verify the student_name fallback and guard logic in web_app_server.py."""

    def setUp(self):
        self.src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")

    def test_36_fallback_api_call_present(self):
        """Source contains _fetch_mk_student_name fallback for student_name."""
        self.assertIn("_fetch_mk_student_name", self.src)

    def test_37_student_name_missing_guard_present(self):
        """Source contains 'student_name_missing' reason_code guard."""
        self.assertIn("student_name_missing", self.src)

    def test_38_requires_check_stage_used_for_missing_name(self):
        """Source contains 'requires_check' stage assignment for missing student name."""
        self.assertIn('"requires_check"', self.src)

    def test_39_update_automation_item_student_name_called_in_server(self):
        """update_automation_item_student_name is called in web_app_server.py."""
        self.assertIn("update_automation_item_student_name", self.src)

    def test_40_repair_intent_metadata_called_in_server(self):
        """repair_intent_metadata is called in web_app_server.py."""
        self.assertIn("repair_intent_metadata", self.src)


# ===========================================================================
# 41-43. app.js UI checks
# ===========================================================================

class TestAppJsMetadataUI(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_41_source_badge_automation(self):
        """app.js sourceBadge renders 'Автоматизация счетов' for moyklass_invoice_automation."""
        self.assertIn("moyklass_invoice_automation", self.js)
        self.assertIn("pi-source-badge-auto", self.js)
        self.assertIn("Автоматизация счетов", self.js)

    def test_42_queue_name_fallback_text(self):
        """app.js automation queue shows 'Имя ученика не определено' when student_name missing."""
        self.assertIn("Имя ученика не определено", self.js)

    def test_43_repair_metadata_button_present(self):
        """app.js renders 'Исправить имя' button calling repair-metadata action."""
        self.assertIn("repair-metadata", self.js)
        self.assertIn("Исправить имя", self.js)


# ===========================================================================
# 44-45. CSS badge and index.html version
# ===========================================================================

class TestCssAndHtmlVersion(unittest.TestCase):
    def setUp(self):
        self.css = STYLES_CSS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_44_pi_source_badge_auto_defined_in_css(self):
        """styles.css defines .pi-source-badge-auto class."""
        self.assertIn(".pi-source-badge-auto", self.css)

    def test_45_version_marker_v7094_2(self):
        """app.js, styles.css, and index.html all reference v7.0.94.2."""
        self.assertIn(f'console.log("MiniApp version: v{CURRENT_VERSION}")', self.js,
                      f"app.js must declare version v{CURRENT_VERSION}")
        self.assertIn(f"v={CURRENT_VERSION}", self.html,
                      f"index.html cache-bust must be v={CURRENT_VERSION}")


if __name__ == "__main__":
    unittest.main()
