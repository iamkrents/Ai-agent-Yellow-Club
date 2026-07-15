"""Tests for v7.0.93.1 — separate client parent-child link system.

Covers:
  Table creation:
    1.  client_child_link_codes table exists after init
    2.  client_parent_child_links table exists after init

  create_client_link_code:
    3.  returns plaintext code starting with CL-
    4.  plaintext code is 11 characters (CL-XXXXXXXX)
    5.  returned code is not stored in DB (only hash is)
    6.  creates row with status=active
    7.  stores mk_user_id and child_display_name
    8.  new code auto-invalidates previous active code for same student
    9.  new code does NOT invalidate codes for different students

  link_client_child:
    10. valid code creates active link in client_parent_child_links
    11. code status set to used after successful link
    12. wrong code returns ok=False
    13. already-used code returns ok=False
    14. expired code returns ok=False
    15. invalidated code returns ok=False
    16. duplicate active link (same parent+student) returns ok=False with already_linked

  list_client_children_for_parent:
    17. returns linked children
    18. returns empty for parent with no links
    19. excludes unlinked children

  unlink_client_child:
    20. sets status to unlinked, sets timestamps
    21. unlinked child no longer appears in list
    22. unlink non-existent link returns ok=False

  invalidate_client_link_code:
    23. sets status to invalidated
    24. invalidated code can no longer be used to link

  get_client_link_status_for_student:
    25. returns active codes and linked parents
    26. never exposes code_hash in response

  Security:
    27. code hash is SHA-256 of plaintext
    28. plaintext CL- code uses expected character alphabet
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT))

from storage import Storage


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


NOW = "2026-07-15T10:00:00"
LATER = "2026-07-15T11:00:00"
PAST = "2025-01-01T00:00:00"
FUTURE = "2030-12-31T23:59:59"


class Test01TablesExist(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()

    def _tables(self):
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        return {r["name"] for r in rows}

    def test_01_codes_table_exists(self):
        self.assertIn("client_child_link_codes", self._tables())

    def test_02_links_table_exists(self):
        self.assertIn("client_parent_child_links", self._tables())


class Test02CreateCode(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.result = self.storage.create_client_link_code("1001", "Иван Иванов", "admin")

    def test_03_returns_cl_code(self):
        self.assertTrue(self.result.get("ok"))
        code = self.result.get("code", "")
        self.assertTrue(code.startswith("CL-"), f"Expected CL- prefix, got: {code!r}")

    def test_04_code_length_is_11(self):
        code = self.result["code"]
        self.assertEqual(len(code), 11, f"CL-XXXXXXXX should be 11 chars, got {len(code)}: {code!r}")

    def test_05_plaintext_not_in_db(self):
        code = self.result["code"]
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT code_hash FROM client_child_link_codes"
            ).fetchall()
        hashes = [r["code_hash"] for r in rows]
        self.assertNotIn(code, hashes, "Plaintext code must not be stored in DB")

    def test_06_row_status_is_active(self):
        with self.storage._connect() as conn:
            row = conn.execute(
                "SELECT status FROM client_child_link_codes WHERE mk_user_id='1001'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "active")

    def test_07_stores_mk_user_id_and_name(self):
        with self.storage._connect() as conn:
            row = conn.execute(
                "SELECT mk_user_id, child_display_name FROM client_child_link_codes WHERE mk_user_id='1001'"
            ).fetchone()
        self.assertEqual(row["mk_user_id"], "1001")
        self.assertEqual(row["child_display_name"], "Иван Иванов")


class Test03AutoInvalidate(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.first = self.storage.create_client_link_code("2001", "Мария", "admin")
        self.second = self.storage.create_client_link_code("2001", "Мария", "admin")

    def test_08_previous_code_invalidated(self):
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT status FROM client_child_link_codes WHERE mk_user_id='2001' ORDER BY id"
            ).fetchall()
        statuses = [r["status"] for r in rows]
        self.assertEqual(statuses[0], "invalidated", "First code should be auto-invalidated")
        self.assertEqual(statuses[1], "active", "Second code should be active")

    def test_09_different_student_not_affected(self):
        self.storage.create_client_link_code("2001", "Мария", "admin")
        self.storage.create_client_link_code("9999", "Другой", "admin")
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT status FROM client_child_link_codes WHERE mk_user_id='9999'"
            ).fetchall()
        self.assertTrue(all(r["status"] == "active" for r in rows))


class Test04LinkChild(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.code_result = self.storage.create_client_link_code("3001", "Петров Пётр", "admin")
        self.code = self.code_result["code"]

    def test_10_valid_code_creates_link(self):
        result = self.storage.link_client_child("tg_parent_10", self.code, NOW)
        self.assertTrue(result.get("ok"), f"link failed: {result}")
        links = self.storage.list_client_children_for_parent("tg_parent_10")
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["mk_user_id"], "3001")

    def test_11_code_marked_used_after_link(self):
        self.storage.link_client_child("tg_parent_11", self.code, NOW)
        with self.storage._connect() as conn:
            row = conn.execute(
                "SELECT status, used_by_parent_telegram_user_id FROM client_child_link_codes WHERE mk_user_id='3001'"
            ).fetchone()
        self.assertEqual(row["status"], "used")
        self.assertEqual(row["used_by_parent_telegram_user_id"], "tg_parent_11")

    def test_12_wrong_code_returns_error(self):
        result = self.storage.link_client_child("tg_parent_12", "CL-ZZZZZZZZ", NOW)
        self.assertFalse(result.get("ok"))
        self.assertIn("error", result)

    def test_13_used_code_returns_error(self):
        self.storage.link_client_child("tg_parent_13a", self.code, NOW)
        result = self.storage.link_client_child("tg_parent_13b", self.code, NOW)
        self.assertFalse(result.get("ok"))

    def test_14_expired_code_returns_error(self):
        expired_result = self.storage.create_client_link_code(
            "3002", "Exp Child", "admin", expires_at=PAST
        )
        result = self.storage.link_client_child("tg_parent_14", expired_result["code"], NOW)
        self.assertFalse(result.get("ok"))
        self.assertIn("error", result)

    def test_15_invalidated_code_returns_error(self):
        inv_result = self.storage.create_client_link_code("3003", "Inv Child", "admin")
        with self.storage._connect() as conn:
            row = conn.execute(
                "SELECT id FROM client_child_link_codes WHERE mk_user_id='3003'"
            ).fetchone()
        self.storage.invalidate_client_link_code(row["id"], "admin", NOW)
        result = self.storage.link_client_child("tg_parent_15", inv_result["code"], NOW)
        self.assertFalse(result.get("ok"))

    def test_16_duplicate_link_returns_already_linked(self):
        # First link succeeds; second code for same student returns already_linked (ok=True)
        self.storage.link_client_child("tg_parent_16", self.code, NOW)
        code2 = self.storage.create_client_link_code("3001", "Петров Пётр", "admin")["code"]
        result = self.storage.link_client_child("tg_parent_16", code2, NOW)
        self.assertTrue(result.get("ok"), f"Expected ok=True for already_linked, got: {result}")
        self.assertTrue(result.get("already_linked"), f"Expected already_linked=True, got: {result}")


class Test05ListChildren(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        code1 = self.storage.create_client_link_code("4001", "Ребёнок 1", "admin")["code"]
        code2 = self.storage.create_client_link_code("4002", "Ребёнок 2", "admin")["code"]
        self.storage.link_client_child("tg_parent_list", code1, NOW)
        self.storage.link_client_child("tg_parent_list", code2, NOW)

    def test_17_returns_linked_children(self):
        children = self.storage.list_client_children_for_parent("tg_parent_list")
        mk_ids = {c["mk_user_id"] for c in children}
        self.assertEqual(mk_ids, {"4001", "4002"})

    def test_18_empty_for_unlinked_parent(self):
        children = self.storage.list_client_children_for_parent("tg_parent_nobody")
        self.assertEqual(children, [])

    def test_19_excludes_unlinked_children(self):
        self.storage.unlink_client_child("tg_parent_list", "4001", "admin", LATER)
        children = self.storage.list_client_children_for_parent("tg_parent_list")
        mk_ids = {c["mk_user_id"] for c in children}
        self.assertNotIn("4001", mk_ids)
        self.assertIn("4002", mk_ids)


class Test06Unlink(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        code = self.storage.create_client_link_code("5001", "Унлинк Тест", "admin")["code"]
        self.storage.link_client_child("tg_parent_unlink", code, NOW)

    def test_20_unlink_sets_status(self):
        result = self.storage.unlink_client_child("tg_parent_unlink", "5001", "admin", LATER)
        self.assertTrue(result.get("ok"), f"unlink failed: {result}")
        with self.storage._connect() as conn:
            row = conn.execute(
                "SELECT status, unlinked_at FROM client_parent_child_links WHERE mk_user_id='5001'"
            ).fetchone()
        self.assertEqual(row["status"], "unlinked")
        self.assertEqual(row["unlinked_at"], LATER)

    def test_21_unlinked_child_not_in_list(self):
        self.storage.unlink_client_child("tg_parent_unlink", "5001", "admin", LATER)
        children = self.storage.list_client_children_for_parent("tg_parent_unlink")
        self.assertEqual(children, [])

    def test_22_unlink_nonexistent_returns_error(self):
        result = self.storage.unlink_client_child("tg_parent_unlink", "9999", "admin", LATER)
        self.assertFalse(result.get("ok"))


class Test07InvalidateCode(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.code_result = self.storage.create_client_link_code("6001", "Инвалид Тест", "admin")
        with self.storage._connect() as conn:
            self.code_id = conn.execute(
                "SELECT id FROM client_child_link_codes WHERE mk_user_id='6001'"
            ).fetchone()["id"]

    def test_23_invalidate_sets_status(self):
        result = self.storage.invalidate_client_link_code(self.code_id, "admin", NOW)
        self.assertTrue(result.get("ok"), f"invalidate failed: {result}")
        with self.storage._connect() as conn:
            row = conn.execute(
                "SELECT status FROM client_child_link_codes WHERE id=?", (self.code_id,)
            ).fetchone()
        self.assertEqual(row["status"], "invalidated")

    def test_24_invalidated_code_cannot_link(self):
        self.storage.invalidate_client_link_code(self.code_id, "admin", NOW)
        result = self.storage.link_client_child("tg_parent_24", self.code_result["code"], NOW)
        self.assertFalse(result.get("ok"))


class Test08LinkStatus(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        code = self.storage.create_client_link_code("7001", "Статус Тест", "admin")["code"]
        self.storage.link_client_child("tg_parent_status", code, NOW)

    def test_25_returns_active_codes_and_links(self):
        status = self.storage.get_client_link_status_for_student("7001")
        self.assertIn("codes", status)
        self.assertIn("links", status)
        self.assertEqual(len(status["links"]), 1)
        self.assertEqual(status["links"][0]["parent_telegram_user_id"], "tg_parent_status")

    def test_26_no_code_hash_in_response(self):
        status = self.storage.get_client_link_status_for_student("7001")
        for code in status.get("codes", []):
            self.assertNotIn("code_hash", code, "code_hash must never be exposed in admin status")


class Test09Security(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.result = self.storage.create_client_link_code("8001", "Безопасность", "admin")
        self.code = self.result["code"]

    def test_27_hash_is_sha256_of_plaintext(self):
        expected_hash = hashlib.sha256(self.code.encode()).hexdigest()
        with self.storage._connect() as conn:
            row = conn.execute(
                "SELECT code_hash FROM client_child_link_codes WHERE mk_user_id='8001'"
            ).fetchone()
        self.assertEqual(row["code_hash"], expected_hash)

    def test_28_code_uses_expected_alphabet(self):
        alphabet = set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
        suffix = self.code[3:]  # remove "CL-"
        for ch in suffix:
            self.assertIn(ch, alphabet, f"Unexpected char {ch!r} in code suffix {suffix!r}")


if __name__ == "__main__":
    unittest.main()
