"""Tests for ALE-10 — first read-only client_manager-facing "Дети" screen
in the schedule module, built on top of the already-existing ALE-8
draft-preview and the already-existing drafts-list read endpoints. No new
backend route is introduced by this step — these tests cover:

  - permission regression for the two existing endpoints this new screen
    newly relies on (schedule_draft_preview / schedule_drafts_list),
    specifically confirming client_manager CAN read them and an
    unauthorized caller CANNOT;
  - static checks on the new app.js UI code: required Russian copy,
    the exact endpoints it calls (no new/undocumented route), no MoyKlass
    write/publish affordance anywhere, and that the backend "decision"
    field is displayed as-is (never recomputed/overridden from
    baseline_outcome/continuation_status/availability_match on the
    frontend).

Run offline:
    python -m unittest tests.test_schedule_client_manager_ui_ale10 -v
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from web_app_server import MiniAppContext

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
NODE_BIN = shutil.which("node")


def _fn_body(name: str) -> str:
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\([^)]*\) \{(.*?)\n\}\n", APP_JS, re.S)
    assert m, f"function {name} not found"
    return m.group(1)


def _full_fn(name: str) -> str:
    """Like _fn_body but returns the COMPLETE function (signature +
    body), for literal injection into a real Node execution harness —
    used only where a static text check can't prove the actual runtime
    behavior (pagination looping, de-dup, stale-response fencing)."""
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\([^)]*\) \{.*?\n\}\n", APP_JS, re.S)
    assert m, f"function {name} not found"
    return m.group(0)


def _const_line(name: str) -> str:
    m = re.search(r"const " + re.escape(name) + r" = [^\n]+;", APP_JS)
    assert m, f"const {name} not found"
    return m.group(0)


def _run_node_scenario(mock_apiget_js: str, driver_js: str) -> subprocess.CompletedProcess:
    """Builds a minimal Node harness around the REAL extracted source of
    _schedLoadChildrenPlan/_schedLoadChildrenDrafts (and their page-size/
    page-cap consts) — proving actual runtime behavior (pagination
    looping, de-dup, generation-counter fencing against stale responses),
    not just that some matching text pattern exists in the source. Only
    _schedState/_schedRenderChildren*/apiGet are stubbed; everything else
    is the untouched, currently-shipping app.js code."""
    assert NODE_BIN, "node is required for this test (already a project dependency — see 'node --check')"
    harness = f"""
"use strict";
let _schedState = {{
  childrenReqGen: 0, childrenLoading: false, childrenError: null, childrenRenderLimit: 50,
  children: null, childrenSummary: null,
  childrenDraftsReqGen: 0, childrenDraftsLoading: false, childrenDraftsError: null, childrenDrafts: null,
}};
function _schedRenderChildrenSummary() {{}}
function _schedRenderChildrenList() {{}}
function _schedRenderChildrenDrafts() {{}}

{mock_apiget_js}

{_const_line("_SCHED_CHILDREN_PAGE_LIMIT")}
{_const_line("_SCHED_CHILDREN_MAX_PAGES")}
{_const_line("_SCHED_DRAFTS_PAGE_LIMIT")}
{_const_line("_SCHED_DRAFTS_MAX_PAGES")}

{_full_fn("_schedLoadChildrenPlan")}
{_full_fn("_schedLoadChildrenDrafts")}

(async () => {{
  try {{
{driver_js}
    console.log("OK");
    process.exit(0);
  }} catch (e) {{
    console.error("SCENARIO FAILURE:", (e && e.stack) || e);
    process.exit(1);
  }}
}})();
"""
    with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w", encoding="utf-8") as f:
        f.write(harness)
        path = f.name
    try:
        return subprocess.run([NODE_BIN, path], capture_output=True, text=True, timeout=20)
    finally:
        Path(path).unlink(missing_ok=True)


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage, owner_id: int = 900001) -> MiniAppContext:
    # object.__new__ bypasses MiniAppContext.__init__ — same pattern as
    # tests.test_client_rollout_gates_v7113_round2._make_ctx and every
    # other schedule test file's _make_ctx.
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(
        admin_ids=[owner_id], senior_teacher_ids=[], web_app_test_roles=False,
        schedule_foundation_enabled=True, schedule_foundation_pilot_telegram_ids=[owner_id],
        schedule_draft_mutations_enabled=False,
        food_location_yc1="Кульман 1/1", food_location_yc2="Мстиславца 6",
    )
    return ctx


class TestClientManagerCanReadExistingEndpoints(unittest.TestCase):
    """This screen introduces no new backend route — it must reuse the
    same server-side role gate every other schedule endpoint already has.
    client_manager is one of the three roles that gate allows through."""

    def test_client_manager_can_read_draft_preview(self):
        storage = _make_storage()
        cm_id = 900101
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, owner_id=900001)
        # schedule_foundation_pilot_telegram_ids only lists the owner in
        # this fixture; make the module enabled globally instead so the
        # client_manager's real (non-owner) role is what's under test.
        ctx.settings.schedule_foundation_enabled = True

        result = ctx.schedule_draft_preview({"user_id": cm_id}, {})
        self.assertTrue(result.get("ok"), result)
        self.assertIn("summary", result)

    def test_client_manager_can_read_drafts_list(self):
        storage = _make_storage()
        cm_id = 900102
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, owner_id=900001)
        ctx.settings.schedule_foundation_enabled = True

        result = ctx.schedule_drafts_list({"user_id": cm_id}, {})
        self.assertTrue(result.get("ok"), result)
        self.assertIn("drafts", result)


class TestUnauthorizedRoleCannotReadEndpoints(unittest.TestCase):
    """A regular client/teacher (or any caller not in SCHEDULE_MODULE_
    ROLES) must never be able to read this data, matching the existing
    schedule-module contract — not something new introduced here, but
    newly exercised end-to-end by this screen's two API calls."""

    def test_unauthorized_role_cannot_read_draft_preview(self):
        storage = _make_storage()
        teacher_id = 900201
        storage.set_staff_role(teacher_id, "teacher")
        ctx = _make_ctx(storage, owner_id=900001)
        ctx.settings.schedule_foundation_enabled = True

        result = ctx.schedule_draft_preview({"user_id": teacher_id}, {})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "forbidden")

    def test_unauthorized_role_cannot_read_drafts_list(self):
        storage = _make_storage()
        teacher_id = 900202
        storage.set_staff_role(teacher_id, "teacher")
        ctx = _make_ctx(storage, owner_id=900001)
        ctx.settings.schedule_foundation_enabled = True

        result = ctx.schedule_drafts_list({"user_id": teacher_id}, {})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "forbidden")

    def test_unrecognized_caller_with_module_disabled_gets_feature_disabled(self):
        # module enabled but no pilot access at all for a random id — the
        # existing feature_disabled path, unaffected by this screen.
        storage = _make_storage()
        random_id = 900301
        ctx = _make_ctx(storage, owner_id=900001)
        ctx.settings.schedule_foundation_enabled = False
        ctx.settings.schedule_foundation_pilot_telegram_ids = []

        result = ctx.schedule_draft_preview({"user_id": random_id}, {})
        self.assertFalse(result.get("ok"))
        self.assertIn(result.get("reason_code"), ("forbidden", "feature_disabled"))


class TestNewTabRegistration(unittest.TestCase):
    def test_children_tab_registered_in_skeleton(self):
        body = _fn_body("_schedRenderSkeleton")
        self.assertIn('{ id: "children", label: "Дети" }', body)

    def test_children_tab_dispatched_in_current_tab_router(self):
        body = _fn_body("_schedRenderCurrentTab")
        self.assertIn('_schedState.tab === "children"', body)
        self.assertIn("_schedRenderChildrenPlan(root)", body)
        self.assertIn("_schedLoadChildrenPlan()", body)
        self.assertIn("_schedLoadChildrenDrafts()", body)

    def test_preexisting_tabs_still_present(self):
        # regression: adding the new tab must not have disturbed the
        # existing owner/admin technical tabs.
        body = _fn_body("_schedRenderSkeleton")
        for tab_id in ("overview", "groups", "foundation", "needs-review", "no-availability", "unconfirmed", "drafts"):
            self.assertIn(f'id: "{tab_id}"', body)


class TestHumanReadableCopy(unittest.TestCase):
    def test_screen_title_present(self):
        body = _fn_body("_schedRenderChildrenPlan")
        self.assertIn("Расписание нового учебного года", body)

    def test_summary_cards_use_exact_required_labels(self):
        for label in (
            "Всего детей", "Можно сохранить прошлую группу", "Ждут ответа",
            "Нужно распределить", "Требуют проверки", "Не продолжают",
        ):
            self.assertIn(label, APP_JS.split("SCHED_PREVIEW_SUMMARY_CARDS")[1][:600])

    def test_filters_use_exact_required_labels(self):
        block = APP_JS.split("SCHED_PREVIEW_FILTERS")[1][:700]
        for label in ("Все", "Ждут ответа", "Сохранить прошлую группу", "Нужно распределить", "Проверить вручную", "Не продолжают"):
            self.assertIn(label, block)

    def test_decision_labels_match_exact_required_russian_phrases(self):
        block = APP_JS.split("SCHED_PREVIEW_DECISION_LABELS = {")[1][:700]
        self.assertIn("Можно сохранить прошлую группу", block)
        self.assertIn("Ждём ответа", block)
        self.assertIn("Нужно подобрать время", block)
        self.assertIn("Нужно проверить", block)
        self.assertIn("Не продолжает", block)

    def test_no_raw_technical_decision_values_shown_as_labels(self):
        # the raw enum values themselves (keep_historical_slot etc.) must
        # only ever appear as object KEYS (mapping FROM them), never as
        # user-facing text on their own outside that map.
        row_body = _fn_body("_schedChildDecisionRowHtml")
        self.assertNotIn(">keep_historical_slot<", row_body)
        self.assertNotIn(">pending_confirmation<", row_body)
        self.assertNotIn(">manual_review<", row_body)

    def test_no_historical_baseline_text(self):
        body = _fn_body("_schedChildBaselineText")
        self.assertIn("Основная группа не определена", body)

    def test_ambiguous_baseline_text(self):
        body = _fn_body("_schedChildBaselineText")
        self.assertIn("Несколько возможных групп", body)

    def test_empty_drafts_state_uses_exact_required_copy(self):
        body = _fn_body("_schedRenderChildrenDrafts")
        self.assertIn(
            "Черновики появятся после того, как родители подтвердят продолжение обучения и укажут доступное время.",
            body,
        )


class TestReusesExistingEndpointsOnly(unittest.TestCase):
    def test_uses_existing_draft_preview_endpoint(self):
        body = _fn_body("_schedLoadChildrenPlan")
        self.assertIn("/api/schedule/draft-preview", body)

    def test_uses_existing_drafts_list_endpoint(self):
        body = _fn_body("_schedLoadChildrenDrafts")
        self.assertIn("/api/schedule/drafts", body)

    def test_no_new_undocumented_endpoint_introduced(self):
        new_section_start = APP_JS.index("// ── ALE-10")
        new_section_end = APP_JS.index("// ── Status + overview")
        new_section = APP_JS[new_section_start:new_section_end]
        endpoint_calls = re.findall(r'apiGet\(`(/api/[a-zA-Z0-9/_-]+)', new_section)
        for call in endpoint_calls:
            self.assertIn(call, ("/api/schedule/draft-preview", "/api/schedule/drafts"))


class TestNoMoyKlassWriteAffordance(unittest.TestCase):
    def test_no_moyklass_publish_or_sync_button_in_rendered_html(self):
        # scoped to the actual HTML-producing function bodies (rendered,
        # user-facing UI), not the surrounding explanatory code comments —
        # those legitimately mention "MoyKlass" to document why nothing is
        # written there.
        rendered = "".join(
            _fn_body(name) for name in (
                "_schedRenderChildrenPlan", "_schedRenderChildrenSummary", "_schedRenderChildrenFilters",
                "_schedChildDecisionRowHtml", "_schedRenderChildrenList", "_schedRenderChildrenDrafts",
                "_schedChildBaselineText",
            )
        )
        for forbidden in ("публиковать", "Опубликовать", "МойКласс", "MoyKlass", "moyklass", "sync-to-moyklass"):
            self.assertNotIn(forbidden, rendered)

    def test_new_section_never_calls_apiPost(self):
        # purely read-only screen — no mutation call of any kind.
        new_section_start = APP_JS.index("// ── ALE-10")
        new_section_end = APP_JS.index("// ── Status + overview")
        new_section = APP_JS[new_section_start:new_section_end]
        self.assertNotIn("apiPost(", new_section)


class TestDecisionNeverRecomputedOnFrontend(unittest.TestCase):
    """ALE-10 explicit instruction: 169 baseline=none / 6 ambiguous
    children stay in whatever the backend decided (pending_confirmation
    today) — the frontend must show c.decision as-is, never derive its
    own decision from baseline_outcome/continuation_status/
    availability_match."""

    def test_row_renderer_reads_decision_directly_from_the_record(self):
        body = _fn_body("_schedChildDecisionRowHtml")
        self.assertIn("SCHED_PREVIEW_DECISION_LABELS[c.decision]", body)

    def test_baseline_text_helper_never_returns_a_decision_value(self):
        # the baseline helper is display-only context text, not a
        # decision — must never emit any of the actual decision strings.
        body = _fn_body("_schedChildBaselineText")
        for decision in ("keep_historical_slot", "pending_confirmation", "needs_reassignment", "manual_review", "stopped"):
            self.assertNotIn(decision, body)

    def test_row_renderer_never_reassigns_c_decision(self):
        body = _fn_body("_schedChildDecisionRowHtml")
        self.assertNotIn("c.decision =", body)
        self.assertNotIn("c.decision=", body)


@unittest.skipUnless(NODE_BIN, "node not found on PATH")
class TestChildrenPlanPaginationDynamic(unittest.TestCase):
    """Review-gate finding: production snapshot has 587 unique children,
    but GET /api/schedule/draft-preview caps at limit=300/page. These run
    the REAL extracted _schedLoadChildrenPlan/_schedLoadChildrenDrafts
    source in Node against a mocked apiGet, proving actual pagination/
    de-dup/stale-response behavior — not just a static text pattern."""

    def _assert_ok(self, proc: subprocess.CompletedProcess) -> None:
        self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}")

    def test_587_across_two_pages_loads_all_unique_including_edges_and_filter(self):
        mock = """
async function apiGet(url) {
  const u = new URL(url, "http://x");
  const offset = parseInt(u.searchParams.get("offset"), 10);
  const limit = parseInt(u.searchParams.get("limit"), 10);
  const total = 587;
  const students = [];
  for (let i = offset; i < Math.min(offset + limit, total); i++) {
    students.push({
      mk_user_id: String(i + 1), child_display_name: "Child " + (i + 1),
      decision: "pending_confirmation", continuation_status: "unconfirmed",
      availability_match: "no_availability", baseline_outcome: "found",
      historical_group_id: 1, historical_group_name: "G", historical_weekday: 2, historical_start_time: "10:00",
    });
  }
  return {
    ok: true, students, total, limit, offset,
    summary: { total, keep_historical_slot: 0, pending_confirmation: total, needs_reassignment: 0, manual_review: 0, stopped: 0 },
  };
}
"""
        driver = """
    await _schedLoadChildrenPlan();
    if (_schedState.children.length !== 587) throw new Error("expected 587 children, got " + _schedState.children.length);
    const ids = _schedState.children.map(c => c.mk_user_id);
    if (new Set(ids).size !== 587) throw new Error("duplicate mk_user_id present in loaded set");
    if (!ids.includes("301")) throw new Error("301st child missing from loaded set");
    if (!ids.includes("587")) throw new Error("587th (last) child missing from loaded set");
    const pendingCount = _schedState.children.filter(c => c.decision === "pending_confirmation").length;
    if (pendingCount !== 587) throw new Error("pending_confirmation filter would only see " + pendingCount + " of 587");
    if (_schedState.childrenSummary.total !== 587) throw new Error("summary.total mismatch: " + _schedState.childrenSummary.total);
    if (_schedState.childrenLoading !== false) throw new Error("childrenLoading not cleared after load");
"""
        self._assert_ok(_run_node_scenario(mock, driver))

    def test_duplicate_child_across_pages_does_not_produce_duplicate_row(self):
        mock = """
async function apiGet(url) {
  const u = new URL(url, "http://x");
  const offset = parseInt(u.searchParams.get("offset"), 10);
  if (offset === 0) {
    const students = [];
    for (let i = 0; i < 300; i++) students.push({ mk_user_id: "u" + i, decision: "pending_confirmation", child_display_name: "C" + i });
    return { ok: true, students, total: 305, summary: { total: 305, keep_historical_slot: 0, pending_confirmation: 305, needs_reassignment: 0, manual_review: 0, stopped: 0 } };
  }
  // Second page deliberately RE-RETURNS the last 5 ids from page one
  // (simulating data shifting mid-pagination) plus 5 genuinely new ones.
  const students = [];
  for (let i = 295; i < 300; i++) students.push({ mk_user_id: "u" + i, decision: "pending_confirmation", child_display_name: "C" + i });
  for (let i = 300; i < 305; i++) students.push({ mk_user_id: "u" + i, decision: "pending_confirmation", child_display_name: "C" + i });
  return { ok: true, students, total: 305, summary: { total: 305, keep_historical_slot: 0, pending_confirmation: 305, needs_reassignment: 0, manual_review: 0, stopped: 0 } };
}
"""
        driver = """
    await _schedLoadChildrenPlan();
    const ids = _schedState.children.map(c => c.mk_user_id);
    if (new Set(ids).size !== ids.length) throw new Error("duplicate row present: " + ids.length + " rows, " + new Set(ids).size + " unique");
    if (_schedState.children.length !== 305) throw new Error("expected 305 unique children after de-dup, got " + _schedState.children.length);
"""
        self._assert_ok(_run_node_scenario(mock, driver))

    def test_unexpected_empty_second_page_finishes_safely(self):
        mock = """
async function apiGet(url) {
  const u = new URL(url, "http://x");
  const offset = parseInt(u.searchParams.get("offset"), 10);
  if (offset === 0) {
    const students = [];
    for (let i = 0; i < 300; i++) students.push({ mk_user_id: "u" + i, decision: "pending_confirmation", child_display_name: "C" + i });
    // total claims far more exist than will ever actually be returned.
    return { ok: true, students, total: 900, summary: { total: 900, keep_historical_slot: 0, pending_confirmation: 900, needs_reassignment: 0, manual_review: 0, stopped: 0 } };
  }
  // Buggy/unexpected: server returns nothing on the next page even
  // though it claimed more rows existed.
  return { ok: true, students: [], total: 900, summary: { total: 900, keep_historical_slot: 0, pending_confirmation: 900, needs_reassignment: 0, manual_review: 0, stopped: 0 } };
}
"""
        driver = """
    await _schedLoadChildrenPlan();
    if (_schedState.children.length !== 300) throw new Error("expected to stop with the 300 rows actually received, got " + _schedState.children.length);
    if (_schedState.childrenLoading !== false) throw new Error("childrenLoading stuck true — did not finish safely");
    if (_schedState.childrenError) throw new Error("unexpected error state: " + _schedState.childrenError);
"""
        self._assert_ok(_run_node_scenario(mock, driver))

    def test_pathological_total_never_loops_more_than_the_hard_page_cap(self):
        # total that would never satisfy offset>=total with real paging
        # (e.g. a corrupted/absurd value) must still terminate, bounded
        # by _SCHED_CHILDREN_MAX_PAGES, not hang forever.
        mock = """
let calls = 0;
async function apiGet(url) {
  calls++;
  const u = new URL(url, "http://x");
  const offset = parseInt(u.searchParams.get("offset"), 10);
  const students = [{ mk_user_id: "u" + offset, decision: "pending_confirmation", child_display_name: "C" }];
  return { ok: true, students, total: 999999999, summary: { total: 999999999, keep_historical_slot: 0, pending_confirmation: 999999999, needs_reassignment: 0, manual_review: 0, stopped: 0 } };
}
"""
        driver = """
    await _schedLoadChildrenPlan();
    if (_schedState.children.length > _SCHED_CHILDREN_MAX_PAGES) throw new Error("loaded more rows than the page cap allows — did not bound the loop: " + _schedState.children.length);
    if (_schedState.childrenLoading !== false) throw new Error("childrenLoading stuck true — loop never terminated");
"""
        self._assert_ok(_run_node_scenario(mock, driver))

    def test_stale_response_does_not_overwrite_newer_state(self):
        # Call 1 starts, its request is deliberately left unresolved.
        # Call 2 starts (simulating the user leaving and re-entering the
        # "Дети" subtab), its request resolves and completes FIRST. Only
        # THEN does call 1's request resolve — its result must be
        # discarded, not overwrite call 2's already-applied newer state.
        mock = """
let resolvers = [];
async function apiGet(url) {
  return new Promise(resolve => resolvers.push(() => {
    const u = new URL(url, "http://x");
    const offset = parseInt(u.searchParams.get("offset"), 10);
    if (resolvers.length <= 1) {
      // first registered call (the stale one) — 5 children, id prefix "OLD"
      resolve({ ok: true, students: offset === 0 ? [0,1,2,3,4].map(i => ({ mk_user_id: "OLD" + i, decision: "pending_confirmation", child_display_name: "old" })) : [], total: 5, summary: { total: 5, keep_historical_slot: 0, pending_confirmation: 5, needs_reassignment: 0, manual_review: 0, stopped: 0 } });
    } else {
      // second (newer) call — 3 children, id prefix "NEW"
      resolve({ ok: true, students: offset === 0 ? [0,1,2].map(i => ({ mk_user_id: "NEW" + i, decision: "pending_confirmation", child_display_name: "new" })) : [], total: 3, summary: { total: 3, keep_historical_slot: 0, pending_confirmation: 3, needs_reassignment: 0, manual_review: 0, stopped: 0 } });
    }
  }));
}
"""
        driver = """
    const p1 = _schedLoadChildrenPlan();
    // Give call 1's apiGet a tick to register its resolver before call 2 starts.
    await new Promise(r => setTimeout(r, 0));
    const p2 = _schedLoadChildrenPlan();
    await new Promise(r => setTimeout(r, 0));
    if (resolvers.length !== 2) throw new Error("expected exactly 2 pending apiGet calls, got " + resolvers.length);
    // Resolve the NEWER call's request first (it finishes before the older one).
    resolvers[1]();
    await p2;
    // Now let the STALE call's request resolve, after newer state is already applied.
    resolvers[0]();
    await p1;
    const ids = (_schedState.children || []).map(c => c.mk_user_id);
    if (!ids.every(id => id.startsWith("NEW"))) throw new Error("stale (OLD) response overwrote the newer state: " + JSON.stringify(ids));
    if (_schedState.children.length !== 3) throw new Error("expected the newer call's 3 children, got " + _schedState.children.length);
"""
        self._assert_ok(_run_node_scenario(mock, driver))

    def test_drafts_list_pagination_beyond_single_page(self):
        # real persisted-draft count is bounded by historical group count
        # (~130 per the ALE-6 audit), which already exceeds a naive
        # single fetch at the OLD limit=100 — confirm the loop covers a
        # total larger than one page at the endpoint's own 200 cap too.
        mock = """
async function apiGet(url) {
  const u = new URL(url, "http://x");
  const offset = parseInt(u.searchParams.get("offset"), 10);
  const limit = parseInt(u.searchParams.get("limit"), 10);
  const total = 250;
  const drafts = [];
  for (let i = offset; i < Math.min(offset + limit, total); i++) drafts.push({ id: i + 1, name: "Draft " + (i + 1), status: "draft" });
  return { ok: true, drafts, total, limit, offset };
}
"""
        driver = """
    await _schedLoadChildrenDrafts();
    if (_schedState.childrenDrafts.length !== 250) throw new Error("expected 250 drafts, got " + _schedState.childrenDrafts.length);
    const ids = _schedState.childrenDrafts.map(d => d.id);
    if (new Set(ids).size !== 250) throw new Error("duplicate draft id present");
"""
        self._assert_ok(_run_node_scenario(mock, driver))


if __name__ == "__main__":
    unittest.main()
