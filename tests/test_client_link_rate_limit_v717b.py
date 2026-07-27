"""Tests for v7.1.7 (security correction) — dedicated rate-limit storage for
POST /api/client/children/link.

Context: the original v7.1.7 implementation counted failed attempts by
querying client_link_audit_log, which is fail-open by design (a write failure
there is logged and swallowed so it never blocks the main operation). That
made the rate limiter's integrity depend on an intentionally-unreliable
system: if the audit table stopped accepting writes, failed guesses would
stop being counted and the limiter could be bypassed silently.

This correction adds client_link_rate_limit_attempts as a dedicated, hard-fail
store for rate-limit state. client_link_audit_log remains for investigation/
history only and is never read by the rate limiter.

Covers:
  1.  the rate limiter reads/writes client_link_rate_limit_attempts, not
      client_link_audit_log
  2.  5 failed attempts are allowed
  3.  the 6th is blocked with reason_code=rate_limited
  4.  retry_after_seconds is correct (positive, bounded by the window)
  5.  a successful link resets the failure count
  6.  attempts older than the window are not counted
  7.  an audit-log write failure does not disable the rate limiter
  8.  a rate-limit storage failure returns reason_code=rate_limit_unavailable (503)
  9.  the rate-limit table never contains the plaintext code
  10. the rate-limit table never contains code_hash
  11. one failed request is counted exactly once
  12. code_use_rate_limited is still written to the audit log when the audit
      system is healthy (even though rate limiting no longer depends on it)
  13. Client Module / payment visibility / Food Module regression check
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from web_app_server import (
    MiniAppContext,
    CLIENT_LINK_MAX_FAILED_ATTEMPTS,
    CLIENT_LINK_RATE_LIMIT_WINDOW_MINUTES,
)

SERVER_PY = ROOT / "web_app_server.py"
STORAGE_PY = ROOT / "storage.py"

NOW = "2026-07-15T10:00:00"


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


class Test01SeparateTable(unittest.TestCase):
    """1: rate limiting uses its own table, not the audit log."""

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def _tables(self):
        with self.storage._connect() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {r["name"] for r in rows}

    def test_01a_dedicated_table_exists(self):
        self.assertIn("client_link_rate_limit_attempts", self._tables())

    def test_01b_client_link_child_does_not_call_audit_based_counter(self):
        src = SERVER_PY.read_text(encoding="utf-8")
        start = src.find("def client_link_child(")
        end = src.find("\n    def ", start + 1)
        body = src[start:end]
        self.assertIn("count_recent_client_link_rate_limit_failures", body)
        self.assertNotIn("count_recent_client_link_failures(", body)

    def test_01c_counter_reads_dedicated_table_only(self):
        storage_src = STORAGE_PY.read_text(encoding="utf-8")
        start = storage_src.find("def count_recent_client_link_rate_limit_failures(")
        end = storage_src.find("\n    def ", start + 1)
        body = storage_src[start:end]
        self.assertIn("FROM client_link_rate_limit_attempts", body)
        self.assertNotIn("FROM client_link_audit_log", body)

    def test_01d_old_audit_based_counter_removed(self):
        # The old fail-open-adjacent counting method must no longer exist —
        # otherwise it's dead code that could be mistakenly reused later.
        storage_src = STORAGE_PY.read_text(encoding="utf-8")
        self.assertNotIn("def count_recent_client_link_failures(", storage_src)


class Test02BasicLimit(unittest.TestCase):
    """2-4: allow 5, block the 6th, correct retry_after_seconds."""

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def test_02_five_failed_attempts_allowed(self):
        auth = _auth(3001, "parent", self.ctx)
        for i in range(CLIENT_LINK_MAX_FAILED_ATTEMPTS):
            result = self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
            self.assertNotEqual(result.get("reason_code"), "rate_limited", f"attempt {i+1} should not be blocked")

    def test_03_sixth_attempt_blocked(self):
        auth = _auth(3002, "parent", self.ctx)
        for _ in range(CLIENT_LINK_MAX_FAILED_ATTEMPTS):
            self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        blocked = self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        self.assertFalse(blocked.get("ok"))
        self.assertEqual(blocked.get("reason_code"), "rate_limited")

    def test_04_retry_after_seconds_bounded(self):
        auth = _auth(3003, "parent", self.ctx)
        for _ in range(CLIENT_LINK_MAX_FAILED_ATTEMPTS):
            self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        blocked = self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        self.assertIn("retry_after_seconds", blocked)
        self.assertGreater(blocked["retry_after_seconds"], 0)
        self.assertLessEqual(blocked["retry_after_seconds"], CLIENT_LINK_RATE_LIMIT_WINDOW_MINUTES * 60)


class Test03SuccessResetsAndWindow(unittest.TestCase):
    """5-6: success resets the count; old attempts age out."""

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def test_05_success_resets_failure_count(self):
        auth = _auth(3101, "parent", self.ctx)
        owner_auth = _auth(1, "owner", self.ctx)
        for _ in range(CLIENT_LINK_MAX_FAILED_ATTEMPTS - 1):
            self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        gen = self.ctx.admin_client_generate_code(owner_auth, {"mk_user_id": "3101", "child_display_name": "X"})
        ok_result = self.ctx.client_link_child(auth, {"code": gen["code"]})
        self.assertTrue(ok_result.get("ok"), ok_result)
        after = self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        self.assertNotEqual(after.get("reason_code"), "rate_limited")

    def test_06_old_attempts_not_counted(self):
        actor = "3102"
        # Directly insert 5 "failed" rows timestamped well outside the window.
        old_ts = (datetime.utcnow() - timedelta(minutes=CLIENT_LINK_RATE_LIMIT_WINDOW_MINUTES + 5)).strftime("%Y-%m-%dT%H:%M:%S")
        with self.storage._connect() as conn:
            for _ in range(CLIENT_LINK_MAX_FAILED_ATTEMPTS):
                conn.execute(
                    "INSERT INTO client_link_rate_limit_attempts (actor_telegram_user_id, attempted_at, result, reason_code) VALUES (?, ?, 'failed', 'invalid_code_format')",
                    (actor, old_ts),
                )
        auth = _auth(int(actor), "parent", self.ctx)
        result = self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        self.assertNotEqual(result.get("reason_code"), "rate_limited",
                            "attempts older than the window must not count toward the limit")


class Test04AuditIndependence(unittest.TestCase):
    """7, 12: audit failures don't affect rate limiting; audit still fires when healthy."""

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def test_07_audit_write_failure_does_not_disable_rate_limit(self):
        auth = _auth(3201, "parent", self.ctx)
        with patch.object(self.storage, "log_client_link_audit_event", side_effect=RuntimeError("audit db is down")):
            for i in range(CLIENT_LINK_MAX_FAILED_ATTEMPTS):
                result = self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
                self.assertNotEqual(result.get("reason_code"), "rate_limit_unavailable",
                                    f"attempt {i+1} must not fail just because audit logging is broken")
            blocked = self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        self.assertEqual(blocked.get("reason_code"), "rate_limited",
                          "rate limit must still trigger on the 6th attempt despite audit being broken")

    def test_12_rate_limited_event_still_audited_when_healthy(self):
        auth = _auth(3202, "parent", self.ctx)
        for _ in range(CLIENT_LINK_MAX_FAILED_ATTEMPTS):
            self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})  # triggers the block
        events = self.storage.list_client_link_audit_events(mk_user_id="", limit=200)
        # mk_user_id is empty for this event (no real code was ever found), so
        # query the raw table directly instead of the mk_user_id-scoped helper.
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM client_link_audit_log WHERE actor_telegram_user_id=? AND event_type='code_use_rate_limited'",
                ("3202",),
            ).fetchall()
        self.assertGreaterEqual(len(rows), 1)


class Test05StorageFailure(unittest.TestCase):
    """8: a rate-limit storage failure fails closed with 503."""

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def test_08a_read_failure_returns_unavailable(self):
        auth = _auth(3301, "parent", self.ctx)
        with patch.object(self.storage, "count_recent_client_link_rate_limit_failures",
                          side_effect=RuntimeError("db locked")):
            result = self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "rate_limit_unavailable")

    def test_08b_write_failure_returns_unavailable(self):
        auth = _auth(3302, "parent", self.ctx)
        with patch.object(self.storage, "record_client_link_rate_limit_attempt",
                          side_effect=RuntimeError("disk full")):
            result = self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "rate_limit_unavailable")

    def test_08c_unavailable_does_not_leak_code_or_client(self):
        auth = _auth(3303, "parent", self.ctx)
        with patch.object(self.storage, "count_recent_client_link_rate_limit_failures",
                          side_effect=RuntimeError("db locked")):
            result = self.ctx.client_link_child(auth, {"code": "CL-SECRET01"})
        self.assertNotIn("mk_user_id", result)
        self.assertNotIn("child_display_name", result)
        self.assertNotIn("CL-SECRET01", str(result))

    def test_08d_dispatcher_maps_unavailable_to_503(self):
        src = SERVER_PY.read_text(encoding="utf-8")
        idx = src.find('if path == "/api/client/children/link":')
        segment = src[idx:idx + 500]
        self.assertIn("503", segment)
        self.assertIn("rate_limit_unavailable", segment)


class Test06NoSecretsInTable(unittest.TestCase):
    """9-10: no plaintext code or code_hash ever lands in the rate-limit table."""

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def test_09_10_no_plaintext_or_hash(self):
        owner_auth = _auth(1, "owner", self.ctx)
        gen = self.ctx.admin_client_generate_code(owner_auth, {"mk_user_id": "3401", "child_display_name": "X"})
        plaintext = gen["code"]
        code_hash = Storage._hash_client_code(plaintext)

        auth = _auth(3401, "parent", self.ctx)
        self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})  # a failure first
        self.ctx.client_link_child(auth, {"code": plaintext})       # then the real success

        with self.storage._connect() as conn:
            rows = conn.execute("SELECT * FROM client_link_rate_limit_attempts").fetchall()
        self.assertGreater(len(rows), 0)
        for row in rows:
            for val in dict(row).values():
                if val is not None:
                    self.assertNotEqual(str(val), plaintext)
                    self.assertNotEqual(str(val), code_hash)


class Test07CountedOnce(unittest.TestCase):
    """11: one failed request produces exactly one counted row."""

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def test_11_single_failed_request_counted_once(self):
        auth = _auth(3501, "parent", self.ctx)
        self.ctx.client_link_child(auth, {"code": "CL-WRONGCOD"})
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM client_link_rate_limit_attempts WHERE actor_telegram_user_id=? AND result='failed'",
                ("3501",),
            ).fetchall()
        self.assertEqual(len(rows), 1)


class Test08Regression(unittest.TestCase):
    """13: Client Module / payment visibility / Food Module still work end-to-end."""

    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def test_13a_client_module_link_flow_still_works(self):
        owner_auth = _auth(1, "owner", self.ctx)
        gen = self.ctx.admin_client_generate_code(owner_auth, {"mk_user_id": "3601", "child_display_name": "X"})
        self.assertTrue(gen.get("ok"), gen)
        parent_auth = _auth(3601, "parent", self.ctx)
        link = self.ctx.client_link_child(parent_auth, {"code": gen["code"]})
        self.assertTrue(link.get("ok"), link)

    def test_13b_payment_visibility_unaffected(self):
        code = self.storage.create_client_link_code("3602", "Child", "admin")["code"]
        self.storage.link_client_child("tg_3602", code, NOW)
        self.storage.create_payment_intent({
            "mk_user_id": "3602", "student_name": "Child", "amount_minor": 5000,
            "amount_byn": 50.0, "currency": "BYN",
            "purpose": "current_month", "period_month": "2026-07", "payment_method": "erip",
            "created_by_tg_id": 1, "created_by_name": "admin", "comment": "",
            "source": "manual",
        })
        with self.storage._connect() as conn:
            conn.execute("UPDATE payment_intents SET client_visibility='published' WHERE mk_user_id='3602'")
        visible = self.storage.list_client_visible_payment_intents("tg_3602")
        self.assertEqual(len(visible), 1)

    def test_13c_food_tables_untouched(self):
        with self.storage._connect() as conn:
            before_links = conn.execute("SELECT COUNT(*) c FROM parent_child_links").fetchone()["c"]
            before_children = conn.execute("SELECT COUNT(*) c FROM camp_children").fetchone()["c"]
        owner_auth = _auth(1, "owner", self.ctx)
        gen = self.ctx.admin_client_generate_code(owner_auth, {"mk_user_id": "3603", "child_display_name": "X"})
        parent_auth = _auth(3603, "parent", self.ctx)
        self.ctx.client_link_child(parent_auth, {"code": gen["code"]})
        self.ctx.admin_client_unlink_child(owner_auth, {"mk_user_id": "3603", "parent_telegram_user_id": "3603"})
        with self.storage._connect() as conn:
            after_links = conn.execute("SELECT COUNT(*) c FROM parent_child_links").fetchone()["c"]
            after_children = conn.execute("SELECT COUNT(*) c FROM camp_children").fetchone()["c"]
        self.assertEqual(before_links, after_links)
        self.assertEqual(before_children, after_children)


class Test09Retention(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()

    def test_cleanup_deletes_only_old_rows(self):
        old_ts = (datetime.utcnow() - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")
        recent_ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        with self.storage._connect() as conn:
            conn.execute(
                "INSERT INTO client_link_rate_limit_attempts (actor_telegram_user_id, attempted_at, result) VALUES (?, ?, 'failed')",
                ("old_actor", old_ts),
            )
            conn.execute(
                "INSERT INTO client_link_rate_limit_attempts (actor_telegram_user_id, attempted_at, result) VALUES (?, ?, 'failed')",
                ("recent_actor", recent_ts),
            )
        cutoff = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
        self.storage.cleanup_old_client_link_rate_limit_attempts(cutoff)
        with self.storage._connect() as conn:
            remaining = {r["actor_telegram_user_id"] for r in conn.execute("SELECT actor_telegram_user_id FROM client_link_rate_limit_attempts").fetchall()}
        self.assertNotIn("old_actor", remaining)
        self.assertIn("recent_actor", remaining)

    def test_cleanup_is_best_effort_and_does_not_raise(self):
        with patch.object(self.storage, "_connect", side_effect=RuntimeError("db unavailable")):
            try:
                self.storage.cleanup_old_client_link_rate_limit_attempts("2020-01-01T00:00:00")
            except Exception as exc:
                self.fail(f"cleanup must never raise, got: {exc}")


if __name__ == "__main__":
    unittest.main()
