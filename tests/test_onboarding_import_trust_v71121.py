"""Tests for v7.1.12.1 hotfix #2 — closing the import trust-boundary gap.

The mass-selection hotfix (test_onboarding_mass_select_hotfix_v71121.py) shipped
with one real gap: if a submitted mk_user_id had no cache hit, the endpoint fell
back to whatever child_display_name/branch_name/course_name the frontend sent —
attacker/bug-controlled input. This closes that gap completely: the request
body is trusted for exactly one field (mk_user_id). All display data comes
ONLY from server-verified sources, in priority order:
  1. the search/bulk-fetch cache (this session)
  2. an existing trusted client_parent_child_links record
  3. one live MoyKlass bulk fetch covering every still-unresolved id in a
     single call — this is what makes verification work again after a
     server restart wipes the in-memory cache, without forcing staff to
     re-click "Загрузить всех учеников".
An id nothing can verify is skipped, never imported with frontend data, and
reported per-item as candidate_not_verified under "errors" (with "failed"
as the count) — the response is never a false "fully successful" for a
partial import. If NOTHING can be verified and MoyKlass itself is
unreachable, the whole request fails closed instead.

Run:
    python -m unittest tests.test_onboarding_import_trust_v71121 -v
"""
from __future__ import annotations

import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from web_app_server import MiniAppContext
from moyklass_client import MoyKlassResult

SECRET = "test-bot-token-secret"


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


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


class _FakeMoyKlassBulk:
    """Real-shaped MoyKlass bulk client, with a call counter so tests can
    assert "exactly one bulk fetch" instead of "not obviously more than one"."""

    def __init__(self, items):
        self._items = items
        self.calls = 0

    def list_users_bulk(self, params=None, page_size=200, max_pages=30):
        self.calls += 1
        pages_loaded = (len(self._items) + page_size - 1) // max(1, page_size)
        return MoyKlassResult(True, data={
            "items": list(self._items),
            "diagnostics": {
                "pages_loaded": pages_loaded, "raw_items": len(self._items),
                "unique_items": len(self._items), "stopped_reason": "short_page",
            },
        })


class _FakeMoyKlassDown:
    def __init__(self):
        self.calls = 0

    def list_users_bulk(self, params=None, page_size=200, max_pages=30):
        self.calls += 1
        return MoyKlassResult(False, data=None, error="connection timed out")


def _students(n=1753, start=20000):
    return [
        {"id": str(start + i), "name": f"Student{i}", "lastName": "Ivanov", "filial": {"name": "YC1"}}
        for i in range(n)
    ]


class TrustBase(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        self.owner = _auth(1, "owner", self.ctx)

    def _campaign(self):
        r = self.ctx.onboarding_campaign_create(self.owner, {"name": "Trust test", "academic_year": "2026/2027"})
        self.assertTrue(r["ok"], r)
        s = self.ctx.onboarding_campaign_start(self.owner, str(r["campaign"]["id"]))
        self.assertTrue(s["ok"], s)
        return s["campaign"]

    def _recipient(self, campaign_id, mk_user_id):
        recs = self.storage.list_onboarding_campaign_recipients(campaign_id)
        return next(r for r in recs if r["mk_user_id"] == str(mk_user_id))


# ─────────────────────────────────────────────────────────────────────────────
# 1-3 — fabricated frontend fields are ignored, server data wins
# ─────────────────────────────────────────────────────────────────────────────

class TestFabricatedFieldsIgnored(TrustBase):
    def setUp(self):
        super().setUp()
        self.ctx.moyklass = _FakeMoyKlassBulk(_students(5, start=90000))
        self.campaign = self._campaign()
        loaded = self.ctx.onboarding_campaign_bulk_candidates(self.owner, {})
        self.assertTrue(loaded["ok"])

    def test_1_fabricated_name_ignored(self):
        r = self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(self.campaign["id"]),
            {"recipients": [{"mk_user_id": "90000", "child_display_name": "HACKED NAME"}]},
        )
        self.assertTrue(r["ok"], r)
        rec = self._recipient(self.campaign["id"], "90000")
        self.assertEqual(rec["child_display_name"], "Student0 Ivanov")
        self.assertNotIn("HACKED", rec["child_display_name"])

    def test_2_fabricated_branch_ignored(self):
        r = self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(self.campaign["id"]),
            {"recipients": [{"mk_user_id": "90001", "branch_name": "FAKE BRANCH XSS"}]},
        )
        self.assertTrue(r["ok"], r)
        rec = self._recipient(self.campaign["id"], "90001")
        self.assertEqual(rec["branch_name"], "YC1")
        self.assertNotIn("FAKE", rec["branch_name"])

    def test_3_fabricated_academic_level_input_ignored(self):
        # Candidate mapping never carries a real course_name for bulk/search
        # results (MoyKlass doesn't expose it cheaply), so the true
        # server-verified course_name is "" regardless of what the frontend
        # claims — detected academic_level must stay "unknown", not whatever
        # a manipulated course_name would have produced.
        r = self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(self.campaign["id"]),
            {"recipients": [{"mk_user_id": "90002", "course_name": "3 год обучения, продвинутый"}]},
        )
        self.assertTrue(r["ok"], r)
        rec = self._recipient(self.campaign["id"], "90002")
        self.assertEqual(rec["course_name"], "")
        self.assertEqual(rec["academic_level"], "unknown")

    def test_4_data_comes_from_server_cache(self):
        rec_before = self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(self.campaign["id"]), {"recipients": [{"mk_user_id": "90003"}]},
        )
        self.assertTrue(rec_before["ok"])
        rec = self._recipient(self.campaign["id"], "90003")
        self.assertEqual(rec["child_display_name"], "Student3 Ivanov")


# ─────────────────────────────────────────────────────────────────────────────
# 5/6 — server-restart recovery: cold cache, single bulk MoyKlass re-check
# ─────────────────────────────────────────────────────────────────────────────

class TestServerRestartRecovery(unittest.TestCase):
    def test_5_fresh_context_without_cache_still_verifies_via_moyklass(self):
        # Simulates a server restart: a brand-new MiniAppContext/Storage pair
        # sharing the same on-disk DB, with a cold (empty) candidates cache —
        # the frontend never re-clicks "Загрузить всех учеников".
        db_path = Path(tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
        storage1 = Storage(db_path)
        ctx1 = _make_ctx(storage1)
        owner1 = _auth(1, "owner", ctx1)
        campaign = ctx1.onboarding_campaign_create(owner1, {"name": "T", "academic_year": "y"})["campaign"]
        ctx1.onboarding_campaign_start(owner1, str(campaign["id"]))
        # (no bulk/search call on ctx1 — nothing cached)

        storage2 = Storage(db_path)
        ctx2 = _make_ctx(storage2)
        owner2 = _auth(1, "owner", ctx2)
        fake = _FakeMoyKlassBulk(_students(50, start=70000))
        ctx2.moyklass = fake

        r = ctx2.onboarding_campaign_import_recipients(
            owner2, str(campaign["id"]), {"recipients": [{"mk_user_id": "70005", "child_display_name": "wrong"}]},
        )
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["added"], 1)
        rec = storage2.list_onboarding_campaign_recipients(campaign["id"])[0]
        self.assertEqual(rec["child_display_name"], "Student5 Ivanov")
        self.assertGreaterEqual(fake.calls, 1)

    def test_6_1753_verified_with_exactly_one_bulk_fetch(self):
        storage = _tmp_storage()
        ctx = _make_ctx(storage)
        owner = _auth(1, "owner", ctx)
        fake = _FakeMoyKlassBulk(_students(1753, start=10000))
        ctx.moyklass = fake
        campaign = ctx.onboarding_campaign_create(owner, {"name": "T", "academic_year": "y"})["campaign"]
        ctx.onboarding_campaign_start(owner, str(campaign["id"]))

        # No prior bulk_candidates call — cache is cold for all 1753 ids.
        payload = [{"mk_user_id": str(10000 + i)} for i in range(1753)]
        r = ctx.onboarding_campaign_import_recipients(owner, str(campaign["id"]), {"recipients": payload})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["added"], 1753)
        self.assertEqual(fake.calls, 1, "must resolve all 1753 ids with exactly one bulk MoyKlass call, never one per id")


# ─────────────────────────────────────────────────────────────────────────────
# 7/8 — unknown id and MoyKlass-unavailable handling
# ─────────────────────────────────────────────────────────────────────────────

class TestUnverifiedAndUnavailable(TrustBase):
    def test_7_unknown_mk_user_id_reported_as_candidate_not_verified(self):
        self.ctx.moyklass = _FakeMoyKlassBulk(_students(5, start=50000))
        campaign = self._campaign()
        r = self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(campaign["id"]), {"recipients": [{"mk_user_id": "99999999", "child_display_name": "Ghost"}]},
        )
        self.assertTrue(r["ok"], r)  # request itself succeeds; this candidate just isn't imported
        self.assertEqual(r["added"], 0)
        self.assertEqual(r["failed"], 1)
        self.assertEqual(r["errors"], [{"mk_user_id": "99999999", "error_code": "candidate_not_verified"}])
        self.assertEqual(len(self.storage.list_onboarding_campaign_recipients(campaign["id"])), 0)

    def test_7b_error_payload_has_no_extra_personal_data(self):
        self.ctx.moyklass = _FakeMoyKlassBulk(_students(1, start=51000))
        campaign = self._campaign()
        r = self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(campaign["id"]),
            {"recipients": [{"mk_user_id": "88888888", "child_display_name": "Should Not Leak", "branch_name": "Nope"}]},
        )
        self.assertEqual(set(r["errors"][0].keys()), {"mk_user_id", "error_code"})
        self.assertNotIn("Should Not Leak", str(r["errors"]))

    def test_8_moyklass_down_and_no_cache_fails_closed(self):
        self.ctx.moyklass = _FakeMoyKlassDown()
        campaign = self._campaign()
        r = self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(campaign["id"]), {"recipients": [{"mk_user_id": "1", "child_display_name": "Attacker Data"}]},
        )
        self.assertFalse(r["ok"], r)
        self.assertEqual(r.get("reason_code"), "moyklass_unavailable")
        self.assertEqual(len(self.storage.list_onboarding_campaign_recipients(campaign["id"])), 0)

    def test_8b_moyklass_raises_exception_also_fails_closed_not_crashes(self):
        class _Explodes:
            def list_users_bulk(self, params=None, page_size=200, max_pages=30):
                raise ConnectionError("network unreachable")
        self.ctx.moyklass = _Explodes()
        campaign = self._campaign()
        r = self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(campaign["id"]), {"recipients": [{"mk_user_id": "1"}]},
        )
        self.assertFalse(r["ok"], r)
        self.assertEqual(r.get("reason_code"), "moyklass_unavailable")

    def test_8c_partial_verification_still_imports_what_it_can(self):
        # cache has "1" verified; MoyKlass is down for the rest — the
        # verified one must still go through, only the rest fail.
        self.ctx.moyklass = _FakeMoyKlassDown()
        campaign = self._campaign()
        self.ctx._onboarding_candidates_cache_dict()["1"] = (
            time.time(), {"mk_user_id": "1", "child_display_name": "Real Name", "branch_name": "", "course_name": ""}
        )
        r = self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(campaign["id"]), {"recipients": [{"mk_user_id": "1"}, {"mk_user_id": "2"}]},
        )
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["added"], 1)
        self.assertEqual(r["failed"], 1)
        rec = self.storage.list_onboarding_campaign_recipients(campaign["id"])[0]
        self.assertEqual(rec["child_display_name"], "Real Name")


# ─────────────────────────────────────────────────────────────────────────────
# 9/10 — existing recipients untouched, idempotency preserved
# ─────────────────────────────────────────────────────────────────────────────

class TestExistingRecipientProtectedAndIdempotent(TrustBase):
    def setUp(self):
        super().setUp()
        self.ctx.moyklass = _FakeMoyKlassBulk(_students(5, start=60000))
        self.campaign = self._campaign()
        self.ctx.onboarding_campaign_bulk_candidates(self.owner, {})

    def test_9_existing_recipient_not_overwritten_by_fabricated_resubmit(self):
        r1 = self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(self.campaign["id"]), {"recipients": [{"mk_user_id": "60000"}]},
        )
        self.assertTrue(r1["ok"])
        rec = self._recipient(self.campaign["id"], "60000")
        self.assertEqual(rec["child_display_name"], "Student0 Ivanov")

        r2 = self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(self.campaign["id"]),
            {"recipients": [{"mk_user_id": "60000", "child_display_name": "OVERWRITE ATTEMPT"}]},
        )
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["already_present"], 1)
        rec_after = self._recipient(self.campaign["id"], "60000")
        self.assertEqual(rec_after["child_display_name"], "Student0 Ivanov")

    def test_10_repeat_import_idempotent_no_duplicates(self):
        payload = {"recipients": [{"mk_user_id": "60001"}, {"mk_user_id": "60002"}]}
        r1 = self.ctx.onboarding_campaign_import_recipients(self.owner, str(self.campaign["id"]), payload)
        r2 = self.ctx.onboarding_campaign_import_recipients(self.owner, str(self.campaign["id"]), payload)
        self.assertEqual(r1["added"], 2)
        self.assertEqual(r2["added"], 0)
        self.assertEqual(r2["already_present"], 2)
        self.assertEqual(len(self.storage.list_onboarding_campaign_recipients(self.campaign["id"])), 2)


# ─────────────────────────────────────────────────────────────────────────────
# 11 — response shape
# ─────────────────────────────────────────────────────────────────────────────

class TestResponseShape(TrustBase):
    def test_11_response_contains_added_already_present_failed(self):
        self.ctx.moyklass = _FakeMoyKlassBulk(_students(2, start=80000))
        campaign = self._campaign()
        r = self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(campaign["id"]),
            {"recipients": [{"mk_user_id": "80000"}, {"mk_user_id": "99999"}]},
        )
        for key in ("ok", "added", "already_present", "failed", "errors"):
            self.assertIn(key, r)
        self.assertEqual(r["added"], 1)
        self.assertEqual(r["failed"], 1)


# ─────────────────────────────────────────────────────────────────────────────
# 12 — existing single-candidate search-then-import flow keeps working
# ─────────────────────────────────────────────────────────────────────────────

class TestSingleSearchImportRegression(TrustBase):
    def test_12_search_then_import_single_candidate_still_works(self):
        self.ctx.moyklass = types.SimpleNamespace(
            request=lambda method, path, params=None: MoyKlassResult(
                True, data={"items": [{"id": "40001", "name": "Anna", "lastName": "Petrova"}]},
            )
        )
        campaign = self._campaign()
        found = self.ctx.onboarding_campaign_search_candidates(self.owner, {"q": "Anna"})
        self.assertTrue(found["ok"], found)
        self.assertEqual(len(found["candidates"]), 1)
        mk = found["candidates"][0]["mk_user_id"]

        r = self.ctx.onboarding_campaign_import_recipients(
            self.owner, str(campaign["id"]), {"recipients": [{"mk_user_id": mk, "child_display_name": found["candidates"][0]["child_display_name"]}]},
        )
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["added"], 1)
        rec = self._recipient(campaign["id"], mk)
        self.assertEqual(rec["child_display_name"], "Anna Petrova")


if __name__ == "__main__":
    unittest.main()
