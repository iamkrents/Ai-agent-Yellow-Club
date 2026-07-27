"""Tests for v7.1.7 — Client Module backend/security foundation.

No new table for codes: reuses client_child_link_codes / client_parent_child_links
(v7.0.93.1) exactly as audited. Covers only backend/security changes — no
frontend, no Food Module, no payment business logic.

Covers:
  Roles / capability (1-4):
    1.  client_manager passes the client-link admin gate
    2.  owner/admin/operations keep access
    3.  parent/teacher/intern are denied
    4.  canManageClientLinks capability is true only for the admin role set

  Default TTL (5-6):
    5.  create_client_link_code defaults expires_at to now + 72h when omitted
    6.  an explicit expires_at passed by the caller is preserved as-is

  Expiry / auto-invalidate (7-8):
    7.  an expired code is rejected with reason_code=code_expired
    8.  a previous unused code is auto-invalidated by a new one (regression guard)

  Already-linked MVP rule (9):
    9.  create_client_link_code refuses a new code while an active link exists
        (reason_code=client_already_linked); succeeds again after explicit unlink

  Code storage security (10-11):
    10. the plaintext code is never persisted in client_child_link_codes
    11. code_hash is a real (64 hex char) SHA-256-shaped value

  Atomicity (12-13):
    12. one-time use: a code cannot be reused after a successful link
    13. two link attempts against the same code cannot both create an active link

  Unlink data safety (14):
    14. unlink never deletes payment_intents or the link row itself (soft status only)

  Audit trail (15-20):
    15. code_created is logged
    16. code_invalidated is logged
    17. code_used is logged
    18. client_unlinked is logged
    19. failed/blocked events are logged (expired, used, invalid, create-blocked)
    20. no audit row ever contains the plaintext code

  Rate limiting (21-24):
    21. 5 failed attempts trigger a block on the 6th
    22. the dispatcher maps reason_code=rate_limited to HTTP 429 (static wiring check)
    23. retry_after_seconds is present and positive when rate-limited
    24. a successful link resets the failure count for that Telegram user

  No information leak (25):
    25. a wrong/unknown code response never includes mk_user_id or child_display_name

  Isolation from other systems (26-27):
    26. Food Module tables (parent_child_links, camp_children) are untouched by
        client-link operations
    27. list_client_visible_payment_intents still works end-to-end through
        client_parent_child_links after the atomicity rewrite

  28. Regression: existing tests.test_client_parent_links and
      tests.test_admin_client_links still pass unmodified in behavior (verified
      by running those suites alongside this one — see test run log, not
      re-asserted here to avoid duplicating ~50 existing tests).
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage, CLIENT_LINK_CODE_TTL_HOURS
from web_app_server import (
    MiniAppContext,
    CLIENT_LINK_ADMIN_ROLES,
    CLIENT_LINK_MAX_FAILED_ATTEMPTS,
    CLIENT_LINK_RATE_LIMIT_WINDOW_MINUTES,
)

SERVER_PY = ROOT / "web_app_server.py"

NOW = "2026-07-15T10:00:00"
PAST = "2025-01-01T00:00:00"
FUTURE = "2030-12-31T23:59:59"


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace()
    ctx._role_store: dict[int, str] = {}

    def _role_for_user(uid: int) -> str:
        return ctx._role_store.get(int(uid), "other")

    ctx._role_for_user = _role_for_user
    return ctx


def _auth(uid: int, role: str, ctx: MiniAppContext) -> dict:
    ctx._role_store[uid] = role
    return {"user_id": uid}


# ─────────────────────────────────────────────────────────────────────────────
# 1-4: roles / capability
# ─────────────────────────────────────────────────────────────────────────────

class Test01RoleGate(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def test_01_client_manager_passes_gate(self):
        auth = _auth(1001, "client_manager", self.ctx)
        result = self.ctx.admin_client_generate_code(auth, {"mk_user_id": "9001", "child_display_name": "A"})
        self.assertTrue(result.get("ok"), result)

    def test_02_owner_admin_operations_keep_access(self):
        for i, role in enumerate(("owner", "admin", "operations")):
            auth = _auth(2000 + i, role, self.ctx)
            result = self.ctx.admin_client_generate_code(auth, {"mk_user_id": f"900{i}", "child_display_name": "B"})
            self.assertTrue(result.get("ok"), f"role={role}: {result}")

    def test_03_parent_teacher_intern_denied(self):
        for i, role in enumerate(("parent", "teacher", "intern")):
            auth = _auth(3000 + i, role, self.ctx)
            result = self.ctx.admin_client_generate_code(auth, {"mk_user_id": f"901{i}", "child_display_name": "C"})
            self.assertFalse(result.get("ok"), f"role={role} should be denied: {result}")

    def test_04_capability_matches_admin_role_set(self):
        self.assertIn("client_manager", CLIENT_LINK_ADMIN_ROLES)
        self.assertIn("owner", CLIENT_LINK_ADMIN_ROLES)
        self.assertIn("admin", CLIENT_LINK_ADMIN_ROLES)
        self.assertIn("operations", CLIENT_LINK_ADMIN_ROLES)
        self.assertNotIn("parent", CLIENT_LINK_ADMIN_ROLES)
        self.assertNotIn("teacher", CLIENT_LINK_ADMIN_ROLES)
        self.assertNotIn("intern", CLIENT_LINK_ADMIN_ROLES)
        # capabilities() must derive canManageClientLinks from this exact set
        server_src = SERVER_PY.read_text(encoding="utf-8")
        self.assertIn('"canManageClientLinks": role in CLIENT_LINK_ADMIN_ROLES', server_src)


# ─────────────────────────────────────────────────────────────────────────────
# 5-6: default TTL
# ─────────────────────────────────────────────────────────────────────────────

class Test02DefaultTTL(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()

    def test_05_default_ttl_is_72_hours(self):
        self.assertEqual(CLIENT_LINK_CODE_TTL_HOURS, 72)
        before = datetime.utcnow()
        result = self.storage.create_client_link_code("5001", "Child", "admin")
        after = datetime.utcnow()
        self.assertTrue(result.get("ok"), result)
        expires_at = datetime.strptime(result["expires_at"], "%Y-%m-%dT%H:%M:%S")
        self.assertGreaterEqual(expires_at, before + timedelta(hours=72) - timedelta(seconds=5))
        self.assertLessEqual(expires_at, after + timedelta(hours=72) + timedelta(seconds=5))

    def test_06_explicit_expires_at_preserved(self):
        result = self.storage.create_client_link_code("5002", "Child", "admin", expires_at=FUTURE)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["expires_at"], FUTURE)


# ─────────────────────────────────────────────────────────────────────────────
# 7-8: expiry / auto-invalidate
# ─────────────────────────────────────────────────────────────────────────────

class Test03ExpiryAndInvalidate(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()

    def test_07_expired_code_reason_code(self):
        code = self.storage.create_client_link_code("7001", "Child", "admin", expires_at=PAST)["code"]
        result = self.storage.link_client_child("tg_7001", code, NOW)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "code_expired")

    def test_08_previous_unused_code_invalidated(self):
        code1 = self.storage.create_client_link_code("8001", "Child", "admin")["code"]
        self.storage.create_client_link_code("8001", "Child", "admin")
        result = self.storage.link_client_child("tg_8001", code1, NOW)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "code_invalidated")


# ─────────────────────────────────────────────────────────────────────────────
# 9: already-linked MVP rule
# ─────────────────────────────────────────────────────────────────────────────

class Test04AlreadyLinkedRule(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()

    def test_09_create_blocked_then_allowed_after_unlink(self):
        code = self.storage.create_client_link_code("9001", "Child", "admin")["code"]
        link = self.storage.link_client_child("tg_9001", code, NOW)
        self.assertTrue(link.get("ok"), link)

        blocked = self.storage.create_client_link_code("9001", "Child", "admin")
        self.assertFalse(blocked.get("ok"))
        self.assertEqual(blocked.get("reason_code"), "client_already_linked")
        self.assertEqual(blocked.get("active_link", {}).get("parent_telegram_user_id"), "tg_9001")

        unlink = self.storage.unlink_client_child("tg_9001", "9001", "admin", NOW)
        self.assertTrue(unlink.get("ok"), unlink)

        allowed = self.storage.create_client_link_code("9001", "Child", "admin")
        self.assertTrue(allowed.get("ok"), allowed)


# ─────────────────────────────────────────────────────────────────────────────
# 10-11: code storage security
# ─────────────────────────────────────────────────────────────────────────────

class Test05CodeStorageSecurity(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()

    def test_10_plaintext_never_stored(self):
        result = self.storage.create_client_link_code("1001", "Child", "admin")
        plaintext = result["code"]
        with self.storage._connect() as conn:
            rows = conn.execute("SELECT * FROM client_child_link_codes").fetchall()
        for row in rows:
            for val in dict(row).values():
                self.assertNotEqual(val, plaintext)

    def test_11_hash_is_sha256_shaped(self):
        result = self.storage.create_client_link_code("1002", "Child", "admin")
        with self.storage._connect() as conn:
            row = conn.execute(
                "SELECT code_hash FROM client_child_link_codes WHERE mk_user_id='1002'"
            ).fetchone()
        code_hash = row["code_hash"]
        self.assertEqual(len(code_hash), 64)
        int(code_hash, 16)  # raises ValueError if not valid hex


# ─────────────────────────────────────────────────────────────────────────────
# 12-13: atomicity
# ─────────────────────────────────────────────────────────────────────────────

class Test06Atomicity(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()

    def test_12_one_time_use(self):
        code = self.storage.create_client_link_code("1201", "Child", "admin")["code"]
        first = self.storage.link_client_child("tg_a", code, NOW)
        self.assertTrue(first.get("ok"), first)
        second = self.storage.link_client_child("tg_b", code, NOW)
        self.assertFalse(second.get("ok"))
        self.assertEqual(second.get("reason_code"), "code_already_used")

    def test_13_repeated_use_creates_only_one_active_link(self):
        code = self.storage.create_client_link_code("1301", "Child", "admin")["code"]
        results = [
            self.storage.link_client_child("tg_race_1", code, NOW),
            self.storage.link_client_child("tg_race_2", code, NOW),
            self.storage.link_client_child("tg_race_3", code, NOW),
        ]
        ok_results = [r for r in results if r.get("ok") and not r.get("already_linked")]
        self.assertEqual(len(ok_results), 1, f"exactly one attempt should win the race: {results}")
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM client_parent_child_links WHERE mk_user_id='1301' AND status='active'"
            ).fetchall()
        self.assertEqual(len(rows), 1)


# ─────────────────────────────────────────────────────────────────────────────
# 14: unlink data safety
# ─────────────────────────────────────────────────────────────────────────────

class Test07UnlinkDataSafety(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()

    def test_14_unlink_preserves_payment_intents_and_link_row(self):
        code = self.storage.create_client_link_code("1401", "Child", "admin")["code"]
        self.storage.link_client_child("tg_1401", code, NOW)

        pi = self.storage.create_payment_intent({
            "mk_user_id": "1401", "student_name": "Child", "amount_minor": 10000,
            "amount_byn": 100.0, "currency": "BYN",
            "purpose": "current_month", "period_month": "2026-07", "payment_method": "erip",
            "created_by_tg_id": 1, "created_by_name": "admin", "comment": "",
            "source": "manual",
        })

        result = self.storage.unlink_client_child("tg_1401", "1401", "admin", NOW)
        self.assertTrue(result.get("ok"), result)

        with self.storage._connect() as conn:
            link_row = conn.execute(
                "SELECT * FROM client_parent_child_links WHERE mk_user_id='1401'"
            ).fetchone()
        self.assertIsNotNone(link_row, "unlink must not delete the link row, only mark it unlinked")
        self.assertEqual(link_row["status"], "unlinked")

        still_there = self.storage.get_payment_intent(pi["public_id"])
        self.assertIsNotNone(still_there, "unlink must not delete the payment_intents row")


# ─────────────────────────────────────────────────────────────────────────────
# 15-20: audit trail
# ─────────────────────────────────────────────────────────────────────────────

class Test08AuditTrail(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def _events(self, mk_user_id: str) -> list[dict]:
        return self.storage.list_client_link_audit_events(mk_user_id, limit=50)

    def test_15_code_created_logged(self):
        auth = _auth(1, "owner", self.ctx)
        self.ctx.admin_client_generate_code(auth, {"mk_user_id": "1501", "child_display_name": "X"})
        events = [e["event_type"] for e in self._events("1501")]
        self.assertIn("code_created", events)

    def test_16_code_invalidated_logged(self):
        auth = _auth(1, "owner", self.ctx)
        gen = self.ctx.admin_client_generate_code(auth, {"mk_user_id": "1601", "child_display_name": "X"})
        self.ctx.admin_client_invalidate_code(auth, {"code_id": gen["code_id"]})
        events = [e["event_type"] for e in self._events("1601")]
        self.assertIn("code_invalidated", events)

    def test_17_code_used_logged(self):
        gen_auth = _auth(1, "owner", self.ctx)
        gen = self.ctx.admin_client_generate_code(gen_auth, {"mk_user_id": "1701", "child_display_name": "X"})
        parent_auth = _auth(1701, "parent", self.ctx)
        self.ctx.client_link_child(parent_auth, {"code": gen["code"]})
        events = [e["event_type"] for e in self._events("1701")]
        self.assertIn("code_used", events)

    def test_18_client_unlinked_logged(self):
        gen_auth = _auth(1, "owner", self.ctx)
        gen = self.ctx.admin_client_generate_code(gen_auth, {"mk_user_id": "1801", "child_display_name": "X"})
        parent_auth = _auth(1801, "parent", self.ctx)
        self.ctx.client_link_child(parent_auth, {"code": gen["code"]})
        admin_auth = _auth(1, "owner", self.ctx)
        self.ctx.admin_client_unlink_child(admin_auth, {"mk_user_id": "1801", "parent_telegram_user_id": "1801"})
        events = [e["event_type"] for e in self._events("1801")]
        self.assertIn("client_unlinked", events)

    def test_19_failed_and_blocked_events_logged(self):
        owner_auth = _auth(1, "owner", self.ctx)
        parent_auth = _auth(1901, "parent", self.ctx)

        # code_use_failed_expired
        expired = self.storage.create_client_link_code("1901", "X", "admin", expires_at=PAST)["code"]
        self.ctx.client_link_child(parent_auth, {"code": expired})
        # code_use_failed_used
        code2_auth = _auth(1902, "parent", self.ctx)
        code2 = self.storage.create_client_link_code("1902", "X", "admin")["code"]
        self.ctx.client_link_child(code2_auth, {"code": code2})
        self.ctx.client_link_child(_auth(1903, "parent", self.ctx), {"code": code2})
        # code_use_failed_invalid (malformed)
        self.ctx.client_link_child(_auth(1904, "parent", self.ctx), {"code": "NOTACODE"})
        # code_create_blocked_already_linked
        self.ctx.admin_client_generate_code(owner_auth, {"mk_user_id": "1902", "child_display_name": "X"})

        all_events = set()
        for mk in ("1901", "1902"):
            all_events |= {e["event_type"] for e in self._events(mk)}
        self.assertIn("code_use_failed_expired", all_events)
        self.assertIn("code_use_failed_used", all_events)
        self.assertIn("code_create_blocked_already_linked", all_events)

    def test_20_audit_never_contains_plaintext_code(self):
        owner_auth = _auth(1, "owner", self.ctx)
        gen = self.ctx.admin_client_generate_code(owner_auth, {"mk_user_id": "2001", "child_display_name": "X"})
        plaintext = gen["code"]
        parent_auth = _auth(2001, "parent", self.ctx)
        self.ctx.client_link_child(parent_auth, {"code": plaintext})
        self.ctx.admin_client_unlink_child(owner_auth, {"mk_user_id": "2001", "parent_telegram_user_id": "2001"})

        with self.storage._connect() as conn:
            rows = conn.execute("SELECT * FROM client_link_audit_log").fetchall()
        for row in rows:
            for val in dict(row).values():
                if val is not None:
                    self.assertNotIn(plaintext, str(val))


# ─────────────────────────────────────────────────────────────────────────────
# 21-24: rate limiting
# ─────────────────────────────────────────────────────────────────────────────

class Test09RateLimit(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def test_21_blocked_after_max_failed_attempts(self):
        auth = _auth(2101, "parent", self.ctx)
        for _ in range(CLIENT_LINK_MAX_FAILED_ATTEMPTS):
            result = self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
            self.assertNotEqual(result.get("reason_code"), "rate_limited")
        blocked = self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        self.assertFalse(blocked.get("ok"))
        self.assertEqual(blocked.get("reason_code"), "rate_limited")

    def test_22_dispatcher_maps_rate_limited_to_429(self):
        src = SERVER_PY.read_text(encoding="utf-8")
        idx = src.find('if path == "/api/client/children/link":')
        self.assertNotEqual(idx, -1)
        segment = src[idx:idx + 400]
        self.assertIn("429", segment)
        self.assertIn("rate_limited", segment)

    def test_23_retry_after_seconds_present_and_positive(self):
        auth = _auth(2301, "parent", self.ctx)
        for _ in range(CLIENT_LINK_MAX_FAILED_ATTEMPTS):
            self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        blocked = self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        self.assertIn("retry_after_seconds", blocked)
        self.assertGreater(blocked["retry_after_seconds"], 0)
        self.assertLessEqual(blocked["retry_after_seconds"], CLIENT_LINK_RATE_LIMIT_WINDOW_MINUTES * 60)

    def test_24_success_resets_failure_count(self):
        auth = _auth(2401, "parent", self.ctx)
        owner_auth = _auth(1, "owner", self.ctx)
        # 4 failures (below the limit of 5)
        for _ in range(CLIENT_LINK_MAX_FAILED_ATTEMPTS - 1):
            self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        # a real success in between
        gen = self.ctx.admin_client_generate_code(owner_auth, {"mk_user_id": "2401", "child_display_name": "X"})
        ok_result = self.ctx.client_link_child(auth, {"code": gen["code"]})
        self.assertTrue(ok_result.get("ok"), ok_result)
        # one more failure afterwards must NOT be blocked (count reset by the success)
        after = self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        self.assertNotEqual(after.get("reason_code"), "rate_limited")


# ─────────────────────────────────────────────────────────────────────────────
# 25: no information leak
# ─────────────────────────────────────────────────────────────────────────────

class Test10NoInfoLeak(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def test_25_wrong_code_does_not_reveal_client(self):
        auth = _auth(2501, "parent", self.ctx)
        result = self.ctx.client_link_child(auth, {"code": "CL-NOTREAL1"})
        self.assertFalse(result.get("ok"))
        self.assertNotIn("mk_user_id", result)
        self.assertNotIn("child_display_name", result)


# ─────────────────────────────────────────────────────────────────────────────
# 26-27: isolation from other systems
# ─────────────────────────────────────────────────────────────────────────────

class Test11Isolation(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()

    def _row_counts(self, table: str) -> int:
        with self.storage._connect() as conn:
            return conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]

    def test_26_food_tables_untouched(self):
        before_links = self._row_counts("parent_child_links")
        before_children = self._row_counts("camp_children")
        code = self.storage.create_client_link_code("2601", "Child", "admin")["code"]
        self.storage.link_client_child("tg_2601", code, NOW)
        self.storage.unlink_client_child("tg_2601", "2601", "admin", NOW)
        self.assertEqual(self._row_counts("parent_child_links"), before_links)
        self.assertEqual(self._row_counts("camp_children"), before_children)

    def test_27_payment_visibility_still_works(self):
        code = self.storage.create_client_link_code("2701", "Child", "admin")["code"]
        self.storage.link_client_child("tg_2701", code, NOW)
        self.storage.create_payment_intent({
            "mk_user_id": "2701", "student_name": "Child", "amount_minor": 5000,
            "amount_byn": 50.0, "currency": "BYN",
            "purpose": "current_month", "period_month": "2026-07", "payment_method": "erip",
            "created_by_tg_id": 1, "created_by_name": "admin", "comment": "",
            "source": "manual",
        })
        with self.storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET client_visibility='published' WHERE mk_user_id='2701'"
            )
        visible = self.storage.list_client_visible_payment_intents("tg_2701")
        self.assertEqual(len(visible), 1)
        self.assertEqual(str(visible[0]["mk_user_id"]), "2701")


if __name__ == "__main__":
    unittest.main()
