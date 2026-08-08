"""Tests for the ALE-10 real-data isolated E2E bugfix — three defects
found by running the merged PR #12 draft editor against an isolated COPY
of production data (never production itself):

  1. apiPost() (miniapp/app.js) does `if (!data.ok) throw new Error(...)`
     unconditionally. Every schedule mutation function that did
     `const d = await apiPost(...); if (!d.ok) { if (d.reason_code === ...) }`
     could never reach that inner branch — apiPost had already thrown and
     control jumped straight to the outer catch (a generic toast) before
     `d` could ever be inspected. Fixed by switching those six call sites
     to the pre-existing `_apiPostRaw` helper (already used elsewhere in
     this file — linkClientChild/_wsOcCreateInvite — for exactly this
     "need the raw {ok, reason_code, error} payload" need), which never
     throws on ok:false but still lets fetch()/res.json() throw naturally
     for real network/transport/invalid-JSON failures. apiPost itself is
     UNCHANGED — every other caller in the file relies on its throw
     behavior and is unaffected.
  2. Direct consequence of #1: _schedAddChildToDraft's
     pending_confirmation_requires_override branch (which calls
     uiConfirmSheet to ask the client_manager to explicitly confirm adding
     an unconfirmed-continuation child) was unreachable — the override UX
     described in the ALE-10 product rules simply never appeared. Fixed by
     the same _apiPostRaw swap; no other code change was needed (the
     retry-with-override / version-conflict-on-retry logic was already
     correctly written, just never reachable).
  3. GET .../add-candidates already supports limit/offset/total (server
     max 300/page) but the picker only ever fetched ONE page — a snapshot
     with more than 300 real candidates (576 in the production copy used
     for E2E) had the remainder permanently unreachable, "Показать ещё"
     included. Fixed by looping pages in _schedLoadAddCandidates, the same
     offset-loop + total-aware-stop + dedup + hard-page-cap + own-
     generation-counter pattern already established by
     _schedLoadChildrenPlan (tests.test_schedule_client_manager_ui_ale10).

Per explicit review instruction, static assertIn-on-source checks are NOT
sufficient here (that's exactly how bug 1/2 slipped through the original
PR #12 review) — every scenario below actually EXECUTES the real,
unmodified function bodies extracted from app.js inside a Node subprocess
against controllable mocks, proving actual runtime behavior.

Run offline:
    python -m unittest tests.test_schedule_draft_e2e_defects_ale10 -v
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
NODE_BIN = shutil.which("node")


def _full_fn(name: str) -> str:
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\([^)]*\) \{.*?\n\}\n", APP_JS, re.S)
    assert m, f"function {name} not found"
    return m.group(0)


def _const_line(name: str) -> str:
    m = re.search(r"const " + re.escape(name) + r" = [^\n]+;", APP_JS)
    assert m, f"const {name} not found"
    return m.group(0)


def _fn_body(name: str) -> str:
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\([^)]*\) \{(.*?)\n\}\n", APP_JS, re.S)
    assert m, f"function {name} not found"
    return m.group(1)


def _run_node(harness_body: str) -> subprocess.CompletedProcess:
    assert NODE_BIN, "node is required for this test (already a project dependency — see 'node --check')"
    harness = f"""
"use strict";
{harness_body}
"""
    with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w", encoding="utf-8") as f:
        f.write(harness)
        path = f.name
    try:
        return subprocess.run([NODE_BIN, path], capture_output=True, text=True, timeout=20)
    finally:
        Path(path).unlink(missing_ok=True)


def _assert_ok(proc: subprocess.CompletedProcess) -> None:
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"


# ── shared harness for the six schedule mutation functions ────────────────
# Only _apiPostRaw, uiConfirmSheet, setNotice, _schedRenderDraftEditor,
# _schedRenderAddChildPicker, _schedLoadDraftDetail, _schedLoadAddCandidates
# are stubbed — every other identifier is the REAL, unmodified function body
# extracted from the currently-shipping app.js.
_MUTATION_FN_NAMES = (
    "_schedMemberActionsBlockedByDirty",
    "_schedSaveDraftFields", "_schedSetDraftStatus",
    "_schedExcludeMember", "_schedIncludeMember", "_schedEditMemberNote",
    "_schedAddChildToDraft",
)


def _run_mutation_scenario(mock_apipostraw_js: str, driver_js: str, initial_state_js: str = "") -> subprocess.CompletedProcess:
    fns = "\n".join(_full_fn(n) for n in _MUTATION_FN_NAMES)
    harness = f"""
let _schedState = {{
  currentDraft: {{ id: 1, version: 5, name: "Test", status: "draft" }},
  currentDraftMembers: [],
  editorPending: {{}}, editorDirty: false, editorSaving: false, editorVersionConflict: false,
  addingMemberId: null, addPickerOpen: true,
  {initial_state_js}
}};
let renderCalls = 0;
function _schedRenderDraftEditor() {{ renderCalls++; }}
function _schedRenderAddChildPicker() {{}}
let loadDraftDetailCalls = [];
async function _schedLoadDraftDetail(id) {{ loadDraftDetailCalls.push(id); }}
let loadAddCandidatesCalls = 0;
async function _schedLoadAddCandidates() {{ loadAddCandidatesCalls++; }}
let noticeLog = [];
function setNotice(text, type) {{ noticeLog.push({{text, type}}); }}
let confirmSheetCalls = [];
function uiConfirmSheet(opts) {{ confirmSheetCalls.push(opts); }}

{mock_apipostraw_js}

{fns}

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
    return _run_node(harness)


# ── Bug 1 — structured mutation errors reach the caller ───────────────────
class TestStructuredMutationErrorsReachCaller(unittest.TestCase):
    """Test items 1, 3, 4 from the review spec."""

    def test_1_field_save_version_conflict_sets_dedicated_conflict_state_not_generic_catch(self):
        mock = """
let calls = [];
async function _apiPostRaw(path, payload) {
  calls.push({path, payload});
  return {ok: false, reason_code: "version_conflict", error: "Черновик изменён другим сотрудником — обновите и повторите", current_version: 6};
}
"""
        driver = """
    _schedState.editorPending = {name: "New name"};
    await _schedSaveDraftFields();
    if (_schedState.editorVersionConflict !== true) throw new Error("editorVersionConflict was not set — reason_code never reached the caller");
    if (loadDraftDetailCalls.length !== 0) throw new Error("must not reload as if the save succeeded");
    if (calls.length !== 1) throw new Error("expected exactly one request, got " + calls.length);
"""
        _assert_ok(_run_mutation_scenario(mock, driver))

    def test_3_status_change_version_conflict_reaches_dedicated_branch(self):
        mock = """
async function _apiPostRaw(path, payload) {
  return {ok: false, reason_code: "version_conflict", error: "stale"};
}
"""
        driver = """
    await _schedSetDraftStatus("needs_review");
    if (_schedState.editorVersionConflict !== true) throw new Error("status-change version_conflict never reached the dedicated branch");
"""
        _assert_ok(_run_mutation_scenario(mock, driver))

    def test_4_member_exclude_stale_version_reaches_dedicated_branch(self):
        mock = """
async function _apiPostRaw(path, payload) {
  return {ok: false, reason_code: "version_conflict", error: "stale"};
}
"""
        driver = """
    await _schedExcludeMember("9001");
    if (_schedState.editorVersionConflict !== true) throw new Error("exclude version_conflict never reached the dedicated branch");
"""
        _assert_ok(_run_mutation_scenario(mock, driver))

    def test_4b_member_include_stale_version_reaches_dedicated_branch(self):
        mock = """
async function _apiPostRaw(path, payload) {
  return {ok: false, reason_code: "version_conflict", error: "stale"};
}
"""
        driver = """
    await _schedIncludeMember("9001");
    if (_schedState.editorVersionConflict !== true) throw new Error("include version_conflict never reached the dedicated branch");
"""
        _assert_ok(_run_mutation_scenario(mock, driver))

    def test_4c_member_note_stale_version_reaches_dedicated_branch(self):
        mock = """
async function _apiPostRaw(path, payload) {
  return {ok: false, reason_code: "version_conflict", error: "stale"};
}
"""
        driver = """
    _schedEditMemberNote("9001", "old note");
    await new Promise(r => setTimeout(r, 10));
    if (_schedState.editorVersionConflict !== true) throw new Error("note version_conflict never reached the dedicated branch");
"""
        # _schedEditMemberNote synchronously calls prompt() first — stub it before injection.
        harness_prefix = 'function prompt() { return "a new note"; }\n'
        proc = _run_mutation_scenario(harness_prefix + mock, driver)
        _assert_ok(proc)

    def test_4d_manual_add_stale_version_reaches_dedicated_branch(self):
        mock = """
async function _apiPostRaw(path, payload) {
  return {ok: false, reason_code: "version_conflict", error: "stale"};
}
"""
        driver = """
    await _schedAddChildToDraft("9010");
    if (_schedState.editorVersionConflict !== true) throw new Error("manual-add version_conflict never reached the dedicated branch");
"""
        _assert_ok(_run_mutation_scenario(mock, driver))

    def test_generic_ok_false_without_reason_code_still_shown_via_fallback_toast(self):
        # a non-version_conflict, non-override business error must still
        # surface to the user (the previously-dead `throw new Error(d.error)`
        # fallback is now genuinely reachable, caught by the outer catch).
        mock = """
async function _apiPostRaw(path, payload) {
  return {ok: false, reason_code: "archived", error: "Черновик в архиве — редактирование недоступно"};
}
"""
        driver = """
    _schedState.editorPending = {name: "x"};
    await _schedSaveDraftFields();
    if (_schedState.editorVersionConflict) throw new Error("must not be misclassified as version_conflict");
    if (noticeLog.length !== 1) throw new Error("expected exactly one user-facing notice, got " + noticeLog.length);
    if (!noticeLog[0].text.includes("архиве")) throw new Error("real backend error text must reach the user: " + JSON.stringify(noticeLog));
"""
        _assert_ok(_run_mutation_scenario(mock, driver))

    def test_2_network_error_still_throws_and_is_caught(self):
        # apiPost is unchanged and _apiPostRaw never swallows a genuine
        # fetch()/res.json() failure — simulated here by having the mock
        # reject, exactly like a real network failure would.
        mock = """
async function _apiPostRaw(path, payload) {
  throw new Error("Failed to fetch");
}
"""
        driver = """
    _schedState.editorPending = {name: "x"};
    await _schedSaveDraftFields();
    if (_schedState.editorVersionConflict) throw new Error("must not be misclassified as version_conflict");
    if (noticeLog.length !== 1) throw new Error("network failure must still notify the user");
    if (!noticeLog[0].text.includes("Failed to fetch")) throw new Error("real network error text lost: " + JSON.stringify(noticeLog));
"""
        _assert_ok(_run_mutation_scenario(mock, driver))


# ── Bug 2 — pending-confirmation override flow ─────────────────────────────
class TestPendingOverrideFlow(unittest.TestCase):
    """Test items 5, 6, 7, 8, 9 from the review spec."""

    def test_5_pending_candidate_triggers_confirm_ui_with_exact_text(self):
        mock = """
async function _apiPostRaw(path, payload) {
  return {ok: false, reason_code: "pending_confirmation_requires_override", error: "Родитель ещё не подтвердил продолжение обучения. Всё равно добавить ребёнка в этот черновик?"};
}
"""
        driver = """
    await _schedAddChildToDraft("9012");
    if (confirmSheetCalls.length !== 1) throw new Error("uiConfirmSheet was never called — override dialog unreachable");
    const opts = confirmSheetCalls[0];
    if (!opts.message.includes("не подтвердил")) throw new Error("wrong confirm message: " + JSON.stringify(opts));
    if (opts.confirmLabel !== "Всё равно добавить") throw new Error("wrong confirm label: " + opts.confirmLabel);
    if (typeof opts.onConfirm !== "function") throw new Error("onConfirm handler missing");
"""
        _assert_ok(_run_mutation_scenario(mock, driver))

    def test_6_cancel_pending_override_sends_no_second_request(self):
        mock = """
let calls = [];
async function _apiPostRaw(path, payload) {
  calls.push(payload);
  return {ok: false, reason_code: "pending_confirmation_requires_override", error: "x"};
}
"""
        driver = """
    await _schedAddChildToDraft("9012");
    // Simulating "Отмена": uiConfirmSheet's own default cancel behavior is
    // to just close the sheet — onCancel is never even passed, so the
    // caller's contract is simply "never invoke onConfirm".
    if (calls.length !== 1) throw new Error("expected exactly one (rejected) request before any cancel/confirm, got " + calls.length);
"""
        _assert_ok(_run_mutation_scenario(mock, driver))

    def test_7_and_8_confirm_sends_override_pending_true_with_same_expected_version(self):
        mock = """
let calls = [];
async function _apiPostRaw(path, payload) {
  calls.push(payload);
  if (calls.length === 1) return {ok: false, reason_code: "pending_confirmation_requires_override", error: "x"};
  return {ok: true, draft: {id: 1, version: 6, name: "Test", status: "draft"}};
}
"""
        driver = """
    await _schedAddChildToDraft("9012");
    if (confirmSheetCalls.length !== 1) throw new Error("confirm dialog never appeared");
    await confirmSheetCalls[0].onConfirm();
    if (calls.length !== 2) throw new Error("expected exactly 2 requests (initial + override), got " + calls.length);
    if (calls[1].override_pending !== true) throw new Error("second request must set override_pending=true: " + JSON.stringify(calls[1]));
    if (calls[1].expected_version !== calls[0].expected_version) throw new Error("second request must reuse the SAME expected_version — a rejected first attempt must never fabricate a new one: " + JSON.stringify(calls));
    if (calls[1].mk_user_id !== calls[0].mk_user_id) throw new Error("second request must target the same child");
"""
        _assert_ok(_run_mutation_scenario(mock, driver))

    def test_9_successful_override_reloads_draft_and_candidates(self):
        mock = """
let calls = 0;
async function _apiPostRaw(path, payload) {
  calls++;
  if (calls === 1) return {ok: false, reason_code: "pending_confirmation_requires_override", error: "x"};
  return {ok: true, draft: {id: 1, version: 6, name: "Test", status: "draft"}};
}
"""
        driver = """
    await _schedAddChildToDraft("9012");
    await confirmSheetCalls[0].onConfirm();
    if (loadDraftDetailCalls.length !== 1) throw new Error("successful override must reload the draft (picks up the new version)");
    if (loadAddCandidatesCalls !== 1) throw new Error("successful override must refresh the candidate list");
    if (_schedState.addingMemberId !== null) throw new Error("addingMemberId must be cleared after completion");
"""
        _assert_ok(_run_mutation_scenario(mock, driver))

    def test_retry_hitting_a_real_concurrent_change_gets_version_conflict_not_bypassed(self):
        # Between the rejected first attempt and the user's confirm click,
        # someone else changed the draft — optimistic locking must still
        # apply on the retry, never silently overwritten.
        mock = """
let calls = 0;
async function _apiPostRaw(path, payload) {
  calls++;
  if (calls === 1) return {ok: false, reason_code: "pending_confirmation_requires_override", error: "x"};
  return {ok: false, reason_code: "version_conflict", error: "stale", current_version: 9};
}
"""
        driver = """
    await _schedAddChildToDraft("9012");
    await confirmSheetCalls[0].onConfirm();
    if (_schedState.editorVersionConflict !== true) throw new Error("retry hitting a real conflict must surface version_conflict, not silently succeed or loop back into another override prompt");
    if (confirmSheetCalls.length !== 1) throw new Error("must not show a second override confirm on a version_conflict retry result");
"""
        _assert_ok(_run_mutation_scenario(mock, driver))


# ── Bug 3 — candidate list pagination beyond 300 ───────────────────────────
class TestCandidatePaginationBeyond300(unittest.TestCase):
    """Test items 10-16 from the review spec."""

    def _run_picker_scenario(self, mock_apiget_js: str, driver_js: str) -> subprocess.CompletedProcess:
        harness = f"""
let _schedState = {{
  currentDraft: {{ id: 1, version: 1 }},
  addPickerOpen: true, addCandidates: null, addCandidatesTotal: 0,
  addCandidatesLoading: false, addCandidatesError: null, addCandidatesRenderLimit: 30,
  addCandidatesReqGen: 0,
}};
function _schedRenderAddChildPicker() {{}}

{mock_apiget_js}

{_const_line("_SCHED_ADD_CANDIDATES_PAGE_LIMIT")}
{_const_line("_SCHED_ADD_CANDIDATES_MAX_PAGES")}

{_full_fn("_schedLoadAddCandidates")}

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
        return _run_node(harness)

    def test_10_11_12_576_candidates_across_two_pages_all_loaded(self):
        mock = """
async function apiGet(url) {
  const u = new URL(url, "http://x");
  const offset = parseInt(u.searchParams.get("offset"), 10);
  const limit = parseInt(u.searchParams.get("limit"), 10);
  const total = 576;
  const candidates = [];
  for (let i = offset; i < Math.min(offset + limit, total); i++) {
    candidates.push({mk_user_id: "u" + i, candidate_group: i % 2 === 0 ? "assignable" : "pending_confirmation", child_display_name: "C" + i});
  }
  return {ok: true, candidates, total, limit, offset};
}
"""
        driver = """
    await _schedLoadAddCandidates();
    if (_schedState.addCandidates.length !== 576) throw new Error("expected all 576 real candidates loaded, got " + _schedState.addCandidates.length);
    if (_schedState.addCandidatesTotal !== 576) throw new Error("addCandidatesTotal must reflect the true total");
"""
        _assert_ok(self._run_picker_scenario(mock, driver))

    def test_13_no_duplicates_between_pages(self):
        mock = """
async function apiGet(url) {
  const u = new URL(url, "http://x");
  const offset = parseInt(u.searchParams.get("offset"), 10);
  if (offset === 0) {
    const candidates = [];
    for (let i = 0; i < 300; i++) candidates.push({mk_user_id: "u" + i, candidate_group: "pending_confirmation"});
    return {ok: true, candidates, total: 305, limit: 300, offset: 0};
  }
  // second page re-returns the last 5 of page one plus 5 genuinely new (data shifted mid-pagination)
  const candidates = [];
  for (let i = 295; i < 300; i++) candidates.push({mk_user_id: "u" + i, candidate_group: "pending_confirmation"});
  for (let i = 300; i < 305; i++) candidates.push({mk_user_id: "u" + i, candidate_group: "pending_confirmation"});
  return {ok: true, candidates, total: 305, limit: 300, offset: 300};
}
"""
        driver = """
    await _schedLoadAddCandidates();
    const ids = _schedState.addCandidates.map(c => c.mk_user_id);
    if (new Set(ids).size !== ids.length) throw new Error("duplicate candidate present: " + ids.length + " rows, " + new Set(ids).size + " unique");
    if (_schedState.addCandidates.length !== 305) throw new Error("expected 305 unique candidates after de-dup, got " + _schedState.addCandidates.length);
"""
        _assert_ok(self._run_picker_scenario(mock, driver))

    def test_14_show_more_can_sequentially_reach_the_last_candidate_of_the_full_set(self):
        mock = """
async function apiGet(url) {
  const u = new URL(url, "http://x");
  const offset = parseInt(u.searchParams.get("offset"), 10);
  const limit = parseInt(u.searchParams.get("limit"), 10);
  const total = 576;
  const candidates = [];
  for (let i = offset; i < Math.min(offset + limit, total); i++) candidates.push({mk_user_id: "u" + i, candidate_group: "pending_confirmation"});
  return {ok: true, candidates, total, limit, offset};
}
"""
        driver = """
    await _schedLoadAddCandidates();
    // simulate repeatedly clicking "Показать ещё" (client-side render-limit bump — the render function itself is static/exercised elsewhere; here we prove the underlying full dataset actually contains candidate #575, the last real one).
    let renderLimit = 30;
    while (renderLimit < _schedState.addCandidates.length) renderLimit += 30;
    const lastVisible = _schedState.addCandidates.slice(0, renderLimit);
    if (!lastVisible.some(c => c.mk_user_id === "u575")) throw new Error("the last real candidate (position 575) is never reachable even after exhausting all 'Показать ещё' clicks");
"""
        _assert_ok(self._run_picker_scenario(mock, driver))

    def test_15_candidate_beyond_position_300_present_and_correctly_grouped(self):
        mock = """
async function apiGet(url) {
  const u = new URL(url, "http://x");
  const offset = parseInt(u.searchParams.get("offset"), 10);
  const limit = parseInt(u.searchParams.get("limit"), 10);
  const total = 576;
  const candidates = [];
  for (let i = offset; i < Math.min(offset + limit, total); i++) {
    candidates.push({mk_user_id: "u" + i, candidate_group: i === 450 ? "assignable" : "pending_confirmation"});
  }
  return {ok: true, candidates, total, limit, offset};
}
"""
        driver = """
    await _schedLoadAddCandidates();
    const c450 = _schedState.addCandidates.find(c => c.mk_user_id === "u450");
    if (!c450) throw new Error("candidate at position 450 (beyond the old 300 cap) missing entirely");
    if (c450.candidate_group !== "assignable") throw new Error("candidate beyond position 300 lost its real candidate_group");
"""
        _assert_ok(self._run_picker_scenario(mock, driver))

    def test_16_stale_picker_load_does_not_overwrite_newer_state(self):
        mock = """
let resolvers = [];
async function apiGet(url) {
  return new Promise(resolve => resolvers.push(() => {
    const u = new URL(url, "http://x");
    const offset = parseInt(u.searchParams.get("offset"), 10);
    if (resolvers.length <= 1) {
      resolve({ok: true, candidates: offset === 0 ? [{mk_user_id: "OLD1", candidate_group: "pending_confirmation"}] : [], total: 1, limit: 300, offset});
    } else {
      resolve({ok: true, candidates: offset === 0 ? [{mk_user_id: "NEW1", candidate_group: "pending_confirmation"}] : [], total: 1, limit: 300, offset});
    }
  }));
}
"""
        driver = """
    const p1 = _schedLoadAddCandidates();
    await new Promise(r => setTimeout(r, 0));
    const p2 = _schedLoadAddCandidates();
    await new Promise(r => setTimeout(r, 0));
    if (resolvers.length !== 2) throw new Error("expected exactly 2 pending apiGet calls, got " + resolvers.length);
    resolvers[1]();
    await p2;
    resolvers[0]();
    await p1;
    const ids = (_schedState.addCandidates || []).map(c => c.mk_user_id);
    if (!ids.every(id => id.startsWith("NEW"))) throw new Error("stale (OLD) picker response overwrote the newer state: " + JSON.stringify(ids));
"""
        _assert_ok(self._run_picker_scenario(mock, driver))

    def test_pathological_total_never_loops_more_than_the_hard_page_cap(self):
        mock = """
async function apiGet(url) {
  const u = new URL(url, "http://x");
  const offset = parseInt(u.searchParams.get("offset"), 10);
  return {ok: true, candidates: [{mk_user_id: "u" + offset, candidate_group: "pending_confirmation"}], total: 999999999, limit: 300, offset};
}
"""
        driver = """
    await _schedLoadAddCandidates();
    if (_schedState.addCandidates.length > _SCHED_ADD_CANDIDATES_MAX_PAGES) throw new Error("did not bound the loop: " + _schedState.addCandidates.length);
    if (_schedState.addCandidatesLoading !== false) throw new Error("addCandidatesLoading stuck true — loop never terminated");
"""
        _assert_ok(self._run_picker_scenario(mock, driver))


# ── regression guards ───────────────────────────────────────────────────
class TestRegressionGuards(unittest.TestCase):
    def test_19_cancel_still_never_calls_the_backend(self):
        # unchanged pre-existing behavior — Cancel never touched the
        # network before this fix and must not start now.
        cancel_fn = _fn_body("_schedCancelDraftEdits")
        self.assertNotIn("apiPost(", cancel_fn)
        self.assertNotIn("_apiPostRaw(", cancel_fn)
        self.assertNotIn("apiGet(", cancel_fn)

    def test_20_apiPost_itself_is_unchanged_other_callers_unaffected(self):
        apipost_body = _fn_body("apiPost")
        self.assertIn('if (!data.ok) throw new Error(data.error || "Ошибка API");', apipost_body)

    def test_21_no_moyklass_reference_in_the_changed_functions(self):
        for name in (*_MUTATION_FN_NAMES, "_schedLoadAddCandidates"):
            body = _fn_body(name)
            self.assertNotIn("moyklass", body.lower())

    def test_all_six_mutation_functions_now_use_apiPostRaw_not_apiPost(self):
        for name in ("_schedSaveDraftFields", "_schedSetDraftStatus", "_schedExcludeMember",
                     "_schedIncludeMember", "_schedEditMemberNote", "_schedAddChildToDraft"):
            body = _fn_body(name)
            self.assertIn("_apiPostRaw(", body, f"{name} must use _apiPostRaw")
            self.assertNotIn("apiPost(", body, f"{name} must not call the throwing apiPost")


if __name__ == "__main__":
    unittest.main()
