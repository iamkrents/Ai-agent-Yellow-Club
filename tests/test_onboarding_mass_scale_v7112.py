"""Tests for v7.1.12 — final mass-scale (300+) audit.

Verifies the specific gap identified in this round's audit:
onboarding_campaign_search_candidates (and the MoyKlass client's plain
get_users()/search_users()) only ever fetch a single page (~30 results) and
are NOT a substitute for bulk recipient loading. This adds and tests a real
paginated bulk-fetch path (MoyKlassClient.list_users_bulk +
web_app_server.onboarding_campaign_bulk_candidates), reusing the existing
MoyKlassClient (no new API client) and never creating recipients without an
explicit staff selection/import call.

Also covers the full 300-client integration scenario end-to-end, Telegram
deep-link payload length at the theoretical maximum SQLite rowid, the
TELEGRAM_BOT_USERNAME-not-set startup diagnostic, and migration from the
actual committed v7.1.11 baseline (git commit 759874b, i.e. origin/main at
the start of this round — has zero client_onboarding_* tables).

Run:
    python -m unittest tests.test_onboarding_mass_scale_v7112 -v
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage, ONBOARDING_INVITE_TOKEN_PREFIX
from web_app_server import MiniAppContext
from moyklass_client import MoyKlassClient, MoyKlassResult

SECRET = "test-bot-token-secret"
V7111_BASELINE_COMMIT = "759874b"  # HEAD/origin/main at the start of the v7.1.12 work


def _tmp_db_path() -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Path(tmp.name)


def _make_ctx(storage: Storage) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(bot_username="yellowclubagent_bot", telegram_bot_token=SECRET)
    ctx._role_store: dict[int, str] = {}

    def _role_for_user(uid: int) -> str:
        return ctx._role_store.get(int(uid), "other")

    ctx._role_for_user = _role_for_user
    return ctx


def _auth(uid: int, role: str, ctx: MiniAppContext) -> dict:
    ctx._role_store[uid] = role
    return {"user_id": uid}


def _pages_of(n: int, page_size: int, start: int = 1) -> list[list[dict]]:
    """Build n items split into page_size-sized MoyKlass-shaped pages."""
    items = [{"id": str(start + i)} for i in range(n)]
    return [items[i:i + page_size] for i in range(0, len(items), page_size)]


# ─────────────────────────────────────────────────────────────────────────────
# Bulk candidate pagination (MoyKlassClient.list_users_bulk)
# ─────────────────────────────────────────────────────────────────────────────

class TestBulkPagination(unittest.TestCase):
    def setUp(self):
        self.client = MoyKlassClient("https://fake", "fake-key")

    def _stub(self, responder):
        self.client.request = responder

    def test_3_pages_of_100_yields_300_unique(self):
        pages = _pages_of(300, 100)

        def responder(method, path, payload=None, params=None):
            offset = int(params["offset"])
            idx = offset // 100
            items = pages[idx] if idx < len(pages) else []
            return MoyKlassResult(True, data={"users": items})
        self._stub(responder)
        r = self.client.list_users_bulk(page_size=100, max_pages=10)
        self.assertTrue(r.ok)
        self.assertEqual(r.data["diagnostics"]["unique_items"], 300)
        self.assertEqual(r.data["diagnostics"]["stopped_reason"], "empty_page")

    def test_last_incomplete_page(self):
        def responder(method, path, payload=None, params=None):
            offset = int(params["offset"])
            if offset == 0:
                return MoyKlassResult(True, data={"users": [{"id": str(i)} for i in range(100)]})
            if offset == 100:
                return MoyKlassResult(True, data={"users": [{"id": str(100 + i)} for i in range(37)]})
            return MoyKlassResult(True, data={"users": []})
        self._stub(responder)
        r = self.client.list_users_bulk(page_size=100, max_pages=10)
        self.assertEqual(r.data["diagnostics"]["unique_items"], 137)
        self.assertEqual(r.data["diagnostics"]["stopped_reason"], "short_page")

    def test_duplicates_between_pages_deduped(self):
        def responder(method, path, payload=None, params=None):
            offset = int(params["offset"])
            if offset == 0:
                return MoyKlassResult(True, data={"users": [{"id": str(i)} for i in range(1, 101)]})
            if offset == 100:
                # overlaps ids 90-100 with page 1
                return MoyKlassResult(True, data={"users": [{"id": str(i)} for i in range(90, 151)]})
            return MoyKlassResult(True, data={"users": []})
        self._stub(responder)
        r = self.client.list_users_bulk(page_size=100, max_pages=10)
        self.assertEqual(r.data["diagnostics"]["unique_items"], 150)
        self.assertLess(r.data["diagnostics"]["unique_items"], r.data["diagnostics"]["raw_items"])

    def test_moyklass_reports_total_field_is_ignored_safely(self):
        # Some responses may include an unrelated "total" field; pagination
        # must not depend on it (this codebase's own pagination code never
        # reads one for this endpoint — see moyklass_client.py research).
        def responder(method, path, payload=None, params=None):
            offset = int(params["offset"])
            if offset == 0:
                return MoyKlassResult(True, data={"users": [{"id": str(i)} for i in range(50)], "total": 999})
            return MoyKlassResult(True, data={"users": [], "total": 999})
        self._stub(responder)
        r = self.client.list_users_bulk(page_size=100, max_pages=10)
        self.assertEqual(r.data["diagnostics"]["unique_items"], 50)

    def test_moyklass_does_not_report_total_field(self):
        def responder(method, path, payload=None, params=None):
            offset = int(params["offset"])
            if offset == 0:
                return MoyKlassResult(True, data=[{"id": str(i)} for i in range(50)])  # bare list, no total anywhere
            return MoyKlassResult(True, data=[])
        self._stub(responder)
        r = self.client.list_users_bulk(page_size=100, max_pages=10)
        self.assertEqual(r.data["diagnostics"]["unique_items"], 50)

    def test_repeated_page_safe_stop(self):
        def responder(method, path, payload=None, params=None):
            # API ignores offset -> always returns the same first page
            return MoyKlassResult(True, data={"users": [{"id": str(i)} for i in range(1, 101)]})
        self._stub(responder)
        r = self.client.list_users_bulk(page_size=100, max_pages=10)
        self.assertEqual(r.data["diagnostics"]["stopped_reason"], "repeated_page")
        self.assertEqual(r.data["diagnostics"]["unique_items"], 100)
        self.assertEqual(r.data["diagnostics"]["pages_loaded"], 2)  # never loops forever

    def test_error_on_second_page_yields_partial_not_false_success(self):
        def responder(method, path, payload=None, params=None):
            offset = int(params["offset"])
            if offset == 0:
                return MoyKlassResult(True, data={"users": [{"id": str(i)} for i in range(100)]})
            return MoyKlassResult(False, error="upstream timeout", status=504)
        self._stub(responder)
        r = self.client.list_users_bulk(page_size=100, max_pages=10)
        self.assertFalse(r.ok)  # never a false full success
        self.assertEqual(r.data["diagnostics"]["unique_items"], 100)  # partial data preserved
        self.assertEqual(r.data["diagnostics"]["stopped_reason"], "error")
        self.assertEqual(r.error, "upstream timeout")

    def test_max_pages_safety_valve(self):
        def responder(method, path, payload=None, params=None):
            offset = int(params["offset"])
            return MoyKlassResult(True, data={"users": [{"id": str(offset + i)} for i in range(100)]})
        self._stub(responder)
        r = self.client.list_users_bulk(page_size=100, max_pages=4)
        self.assertEqual(r.data["diagnostics"]["stopped_reason"], "max_pages_reached")
        self.assertEqual(r.data["diagnostics"]["pages_loaded"], 4)

    def test_diagnostic_fields_present(self):
        def responder(method, path, payload=None, params=None):
            return MoyKlassResult(True, data={"users": []})
        self._stub(responder)
        r = self.client.list_users_bulk()
        for key in ("pages_loaded", "raw_items", "unique_items", "stopped_reason"):
            self.assertIn(key, r.data["diagnostics"])


class TestBulkCandidatesEndpointAndFilters(unittest.TestCase):
    def setUp(self):
        self.storage = Storage(_tmp_db_path())
        self.ctx = _make_ctx(self.storage)
        self.owner = _auth(1, "owner", self.ctx)
        self.ops = _auth(2, "operations", self.ctx)

    def test_endpoint_returns_all_candidates_from_pagination(self):
        class Fake:
            def list_users_bulk(self, params=None, page_size=200, max_pages=30):
                items = [{"id": str(i), "name": f"Kid{i}"} for i in range(300)]
                return MoyKlassResult(True, data={"items": items, "diagnostics": {
                    "pages_loaded": 2, "raw_items": 300, "unique_items": 300, "stopped_reason": "short_page",
                }})
        self.ctx.moyklass = Fake()
        r = self.ctx.onboarding_campaign_bulk_candidates(self.owner, {})
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["candidates"]), 300)
        self.assertEqual(r["diagnostics"]["unique_items"], 300)

    def test_role_gate(self):
        class Fake:
            def list_users_bulk(self, params=None, page_size=200, max_pages=30):
                return MoyKlassResult(True, data={"items": [], "diagnostics": {}})
        self.ctx.moyklass = Fake()
        denied = self.ctx.onboarding_campaign_bulk_candidates(self.ops, {})
        self.assertFalse(denied["ok"])

    def test_partial_error_surfaces_as_not_ok_with_partial_candidates(self):
        class Fake:
            def list_users_bulk(self, params=None, page_size=200, max_pages=30):
                items = [{"id": str(i)} for i in range(50)]
                return MoyKlassResult(False, data={"items": items, "diagnostics": {
                    "pages_loaded": 1, "raw_items": 50, "unique_items": 50, "stopped_reason": "error",
                }}, error="boom")
        self.ctx.moyklass = Fake()
        r = self.ctx.onboarding_campaign_bulk_candidates(self.owner, {})
        self.assertFalse(r["ok"])
        self.assertEqual(len(r["candidates"]), 50)  # transparent partial, not silently dropped

    def test_filters_applied_after_correct_load(self):
        class Fake:
            def list_users_bulk(self, params=None, page_size=200, max_pages=30):
                items = [{"id": str(i), "name": f"Kid{i}"} for i in range(10)]
                return MoyKlassResult(True, data={"items": items, "diagnostics": {
                    "pages_loaded": 1, "raw_items": 10, "unique_items": 10, "stopped_reason": "short_page",
                }})
        self.ctx.moyklass = Fake()
        r = self.ctx.onboarding_campaign_bulk_candidates(self.owner, {})
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["candidates"]), 10)
        # Client-side selection (e.g. by continuation/pilot state) happens on
        # this full, correctly-loaded set — nothing here truncates it early.
        selected = [c for c in r["candidates"] if not c["telegram_connected"]]
        self.assertEqual(len(selected), 10)

    def test_import_of_selected_candidates_stays_idempotent(self):
        class Fake:
            def list_users_bulk(self, params=None, page_size=200, max_pages=30):
                items = [{"id": str(i)} for i in range(20)]
                return MoyKlassResult(True, data={"items": items, "diagnostics": {
                    "pages_loaded": 1, "raw_items": 20, "unique_items": 20, "stopped_reason": "short_page",
                }})
        self.ctx.moyklass = Fake()
        cand = self.ctx.onboarding_campaign_bulk_candidates(self.owner, {})
        r = self.ctx.onboarding_campaign_create(self.owner, {"name": "T", "academic_year": "y"})
        campaign_id = r["campaign"]["id"]
        recs = [{"mk_user_id": c["mk_user_id"]} for c in cand["candidates"]]
        imp1 = self.ctx.onboarding_campaign_import_recipients(self.owner, str(campaign_id), {"recipients": recs})
        imp2 = self.ctx.onboarding_campaign_import_recipients(self.owner, str(campaign_id), {"recipients": recs})
        self.assertEqual(imp1["added"], 20)
        self.assertEqual(imp2["added"], 0)
        self.assertEqual(imp2["already_present"], 20)
        self.assertEqual(len(self.storage.list_onboarding_campaign_recipients(campaign_id)), 20)

    def test_no_recipients_created_by_candidate_load_alone(self):
        class Fake:
            def list_users_bulk(self, params=None, page_size=200, max_pages=30):
                return MoyKlassResult(True, data={"items": [{"id": "1"}], "diagnostics": {}})
        self.ctx.moyklass = Fake()
        r = self.ctx.onboarding_campaign_create(self.owner, {"name": "T2", "academic_year": "y"})
        campaign_id = r["campaign"]["id"]
        self.ctx.onboarding_campaign_bulk_candidates(self.owner, {})
        self.assertEqual(len(self.storage.list_onboarding_campaign_recipients(campaign_id)), 0)


# ─────────────────────────────────────────────────────────────────────────────
# Full 300-client scenario
# ─────────────────────────────────────────────────────────────────────────────

class Test300ClientScenario(unittest.TestCase):
    def test_full_scenario_end_to_end(self):
        db_path = _tmp_db_path()
        storage = Storage(db_path)
        ctx = _make_ctx(storage)
        owner = _auth(1, "owner", ctx)

        class FakePaginated:
            def list_users_bulk(self, params=None, page_size=200, max_pages=30):
                items = [{"id": str(10000 + i), "name": f"Student{i}"} for i in range(300)]
                return MoyKlassResult(True, data={"items": items, "diagnostics": {
                    "pages_loaded": 2, "raw_items": 300, "unique_items": 300, "stopped_reason": "short_page",
                }})
        ctx.moyklass = FakePaginated()
        timings: dict[str, float] = {}

        t0 = time.perf_counter()
        r = ctx.onboarding_campaign_create(owner, {"name": "August 2026 scenario", "academic_year": "2026/2027", "collect_schedule_availability": True})
        self.assertTrue(r["ok"], r)
        campaign = r["campaign"]
        ctx.onboarding_campaign_start(owner, str(campaign["id"]))
        timings["create_campaign"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        cand = ctx.onboarding_campaign_bulk_candidates(owner, {})
        self.assertTrue(cand["ok"])
        self.assertEqual(len(cand["candidates"]), 300)
        timings["load_candidates"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        imp = ctx.onboarding_campaign_import_recipients(owner, str(campaign["id"]), {
            "recipients": [{"mk_user_id": c["mk_user_id"], "child_display_name": c["child_display_name"]} for c in cand["candidates"]]
        })
        self.assertTrue(imp["ok"]); self.assertEqual(imp["added"], 300)
        timings["import_300"] = time.perf_counter() - t0

        recipients = storage.list_onboarding_campaign_recipients(campaign["id"])
        self.assertEqual(len(recipients), 300)
        rids = [rr["id"] for rr in recipients]

        t0 = time.perf_counter()
        bulk_status = ctx.onboarding_campaign_continuation_status(owner, str(campaign["id"]), {"recipient_ids": rids, "status": "continues"})
        self.assertTrue(bulk_status["ok"]); self.assertEqual(bulk_status["updated"], 300)
        timings["bulk_continuation_300"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        key1 = "scenario-batch-key-1"
        batch1 = ctx.onboarding_campaign_create_invites_batch(owner, str(campaign["id"]), {"recipient_ids": rids, "idempotency_key": key1})
        self.assertTrue(batch1["ok"]); self.assertEqual(batch1["counts"]["created"], 300)
        timings["batch_generate_300"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        batch2 = ctx.onboarding_campaign_create_invites_batch(owner, str(campaign["id"]), {"recipient_ids": rids, "idempotency_key": key1})
        self.assertTrue(batch2["ok"]); self.assertTrue(batch2.get("replayed"))
        timings["batch_replay_300"] = time.perf_counter() - t0
        with storage._connect() as conn:
            invite_count = conn.execute("SELECT COUNT(*) c FROM client_onboarding_invites WHERE campaign_id=?", (campaign["id"],)).fetchone()["c"]
        self.assertEqual(invite_count, 300)  # replay created no duplicates

        t0 = time.perf_counter()
        storage2 = Storage(db_path)  # brand-new instance, same db file
        ctx2 = _make_ctx(storage2)
        owner2 = _auth(1, "owner", ctx2)
        detail2 = ctx2.onboarding_campaign_get(owner2, str(campaign["id"]), {})
        self.assertTrue(detail2["ok"]); self.assertEqual(len(detail2["recipients"]), 300)
        timings["refetch_new_instance"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        csv_bytes, _filename = ctx2.onboarding_campaign_export_csv(owner2, str(campaign["id"]), {})
        timings["csv_export_300"] = time.perf_counter() - t0
        text = csv_bytes.decode("utf-8-sig")
        lines = [l for l in text.splitlines() if l.strip()]
        self.assertEqual(len(lines) - 1, 300)  # 300 data rows

        found_links = re.findall(r"https://t\.me/yellowclubagent_bot\?start=c_[A-Za-z0-9_-]+", text)
        self.assertEqual(len(found_links), 300)
        self.assertEqual(len(set(found_links)), 300)  # all unique
        for link in found_links:
            payload = link.split("start=")[1]
            self.assertLessEqual(len(payload), 64)

        with storage2._connect() as conn:
            rows = conn.execute("SELECT token_hash FROM client_onboarding_invites WHERE campaign_id=?", (campaign["id"],)).fetchall()
        self.assertTrue(all(r["token_hash"] is None for r in rows))  # no raw/plaintext secret anywhere

        with storage2._connect() as conn:
            pilot_count = conn.execute("SELECT COUNT(*) c FROM payment_automation_pilot_clients").fetchone()["c"]
        self.assertEqual(pilot_count, 0)  # pilot never created before actual activation

        with storage2._connect() as conn:
            intents = conn.execute("SELECT COUNT(*) c FROM payment_intents").fetchone()["c"]
        self.assertEqual(intents, 0)  # no financial side effects at all

        print(f"\n[300-client scenario timings] {timings}")


# ─────────────────────────────────────────────────────────────────────────────
# Telegram deep-link length + bot_username diagnostics
# ─────────────────────────────────────────────────────────────────────────────

class TestDeepLinkLength(unittest.TestCase):
    def test_max_sqlite_rowid_payload_fits_telegram_limit(self):
        max_rowid = 9223372036854775807  # 2**63 - 1, the real ceiling for SQLite INTEGER PRIMARY KEY
        sig = Storage._onboarding_invite_signature(max_rowid, max_rowid, "1234567890123", SECRET)
        payload = f"{ONBOARDING_INVITE_TOKEN_PREFIX}{max_rowid}_{sig}"
        self.assertLessEqual(len(payload), 64, f"payload too long: {len(payload)} chars: {payload}")
        self.assertRegex(payload, r"^[A-Za-z0-9_-]+$")

    def test_signature_length_is_deterministic(self):
        sig = Storage._onboarding_invite_signature(1, 1, "1", SECRET)
        self.assertEqual(len(sig), 32)  # 24 bytes base64url, no padding — always exactly 32 chars

    def test_production_url_format(self):
        db = Storage(_tmp_db_path())
        ctx = _make_ctx(db)
        owner = _auth(1, "owner", ctx)
        campaign = ctx.onboarding_campaign_create(owner, {"name": "T", "academic_year": "y"})["campaign"]
        ctx.onboarding_campaign_start(owner, str(campaign["id"]))
        ctx.onboarding_campaign_import_recipients(owner, str(campaign["id"]), {"recipients": [{"mk_user_id": "1"}]})
        rid = db.list_onboarding_campaign_recipients(campaign["id"])[0]["id"]
        inv = ctx.onboarding_campaign_create_invite(owner, str(campaign["id"]), {"recipient_id": rid})
        self.assertTrue(inv["ok"])
        self.assertTrue(inv["invite_link"].startswith("https://t.me/yellowclubagent_bot?start=c_"))


class TestBotUsernameDiagnostics(unittest.TestCase):
    def test_config_tracks_default_fallback(self):
        import config
        source = (ROOT / "config.py").read_text(encoding="utf-8")
        self.assertIn("bot_username_is_default", source)
        self.assertIn('os.getenv("TELEGRAM_BOT_USERNAME"', source)

    def test_startup_diagnostic_logs_when_default_in_use(self):
        server_src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        idx = server_src.find("bot_username_is_default")
        self.assertNotEqual(idx, -1)
        segment = server_src[idx:idx + 800]
        self.assertIn("log.warning", segment)
        self.assertIn("TELEGRAM_BOT_USERNAME not set", segment)
        # never logs the bot token
        self.assertNotIn("telegram_bot_token", segment.split("log.warning")[1][:400] if "log.warning" in segment else "")


# ─────────────────────────────────────────────────────────────────────────────
# Migration from the real committed v7.1.11 baseline
# ─────────────────────────────────────────────────────────────────────────────

def _load_legacy_storage_class():
    """Load the ACTUAL committed v7.1.11 storage.py (git show, known-good
    baseline commit) under an isolated module name, so this test proves
    migration against real historical schema, not a guess at what it was."""
    try:
        proc = subprocess.run(
            ["git", "show", f"{V7111_BASELINE_COMMIT}:storage.py"],
            cwd=str(ROOT), capture_output=True, encoding="utf-8", timeout=30, check=True,
        )
    except Exception as exc:  # pragma: no cover - environment without git/history
        return None, str(exc)
    legacy_src = proc.stdout
    if "client_onboarding_campaigns" in legacy_src:
        return None, f"baseline commit {V7111_BASELINE_COMMIT} unexpectedly already has campaign tables"
    legacy_file = Path(tempfile.mkdtemp()) / "storage_v7111_legacy.py"
    legacy_file.write_text(legacy_src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("storage_v7111_legacy_baseline", legacy_file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["storage_v7111_legacy_baseline"] = mod
    spec.loader.exec_module(mod)
    return mod.Storage, None


_LEGACY_STORAGE_CLASS, _LEGACY_LOAD_ERROR = _load_legacy_storage_class()


@unittest.skipIf(_LEGACY_STORAGE_CLASS is None, f"could not load v7.1.11 baseline: {_LEGACY_LOAD_ERROR}")
class TestMigrationFromV7111(unittest.TestCase):
    """Skips cleanly (with the git error as skip reason) in any environment
    without git history access, rather than failing — the other test
    classes in this file (and the rest of the targeted run) remain fully
    informative either way."""

    @classmethod
    def setUpClass(cls):
        cls.LegacyStorage = _LEGACY_STORAGE_CLASS

    def setUp(self):
        self.db_path = _tmp_db_path()

    def test_legacy_db_has_no_onboarding_tables(self):
        legacy = self.LegacyStorage(self.db_path)
        with legacy._connect() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for t in ("client_onboarding_campaigns", "client_onboarding_recipients",
                  "client_onboarding_invites", "client_schedule_availability"):
            self.assertNotIn(t, tables)

    def test_migration_creates_all_new_tables_and_columns(self):
        self.LegacyStorage(self.db_path)
        migrated = Storage(self.db_path)
        with migrated._connect() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for t in ("client_onboarding_campaigns", "client_onboarding_recipients", "client_onboarding_invites",
                  "client_onboarding_audit_log", "client_continuation_responses",
                  "client_schedule_availability", "client_onboarding_batch_runs"):
            self.assertIn(t, tables)
        with migrated._connect() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(client_onboarding_recipients)").fetchall()}
        for c in ("preferred_branch", "academic_level", "academic_level_confidence"):
            self.assertIn(c, cols)

    def test_token_hash_nullable_no_intermediate_schema_needed(self):
        self.LegacyStorage(self.db_path)  # v7.1.11, no invites table at all
        migrated = Storage(self.db_path)  # jumps straight to the final v7.1.12 schema
        with migrated._connect() as conn:
            row = next(r for r in conn.execute("PRAGMA table_info(client_onboarding_invites)").fetchall() if r[1] == "token_hash")
        self.assertEqual(row[3], 0)  # notnull=0 -> nullable

    def test_existing_data_survives_migration(self):
        legacy = self.LegacyStorage(self.db_path)
        code = legacy.create_client_link_code("5001", "Legacy Kid", "1")
        self.assertTrue(code["ok"])
        link = legacy.link_client_child("9999", code["code"], "2026-01-01T00:00:00")
        self.assertTrue(link["ok"])
        legacy.upsert_pilot_client("5001", mode="auto", note="legacy pilot")
        with legacy._connect() as conn:
            conn.execute(
                """INSERT INTO payment_intents (public_id, mk_user_id, status, amount_minor, amount_byn, created_at, updated_at, purpose)
                   VALUES ('ycpi_legacy1', 5001, 'paid', 10000, 100.0, '2026-01-01T00:00:00', '2026-01-01T00:00:00', 'monthly')"""
            )
        legacy.log_client_link_audit_event(event_type="code_used", mk_user_id="5001", result="success")

        migrated = Storage(self.db_path)
        children = migrated.list_client_children_for_parent("9999")
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["mk_user_id"], "5001")
        pilot = migrated.get_pilot_client("5001")
        self.assertEqual(pilot["mode"], "auto")
        self.assertEqual(pilot["note"], "legacy pilot")
        with migrated._connect() as conn:
            self.assertEqual(len(conn.execute("SELECT * FROM payment_intents WHERE public_id='ycpi_legacy1'").fetchall()), 1)
            self.assertGreaterEqual(len(conn.execute("SELECT * FROM client_link_audit_log WHERE mk_user_id='5001'").fetchall()), 1)

    def test_repeated_migration_is_idempotent(self):
        self.LegacyStorage(self.db_path)
        m1 = Storage(self.db_path)
        with m1._connect() as conn:
            tables1 = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        m2 = Storage(self.db_path)  # simulated service restart
        with m2._connect() as conn:
            tables2 = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertEqual(tables1, tables2)

    def test_integrity_check_ok(self):
        self.LegacyStorage(self.db_path)
        migrated = Storage(self.db_path)
        migrated.create_onboarding_campaign("Post-migration", "2026/2027", "1")
        with migrated._connect() as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        self.assertEqual(result, "ok")


if __name__ == "__main__":
    unittest.main()
