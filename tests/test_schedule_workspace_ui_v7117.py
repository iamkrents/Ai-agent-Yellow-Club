"""Tests for v7.1.17 — "Расписание" schedule module: workspace UI states.

Static text/AST-style checks against app.js/index.html/styles.css (this
repo's existing frontend test convention — no browser, no real fetch; real
rendered evidence is gathered separately via Playwright, see the release
report). Covers spec section 23 UI checks 57-61, 65-71: loading/empty/
error+retry states, sync progress, group cards, group detail, server-side
filters/search/pagination, no fixed-pixel-width overflow risk, and the
existing bottom-nav/safe-area shell being reused rather than reinvented.

Run:
    python -m unittest tests.test_schedule_workspace_ui_v7117 -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
STYLES_CSS = (ROOT / "miniapp" / "styles.css").read_text(encoding="utf-8")


def _fn_body(name: str) -> str:
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\([^)]*\) \{(.*?)\n\}\n", APP_JS, re.S)
    assert m, f"function {name} not found"
    return m.group(1)


SCHED_CSS_BLOCK = STYLES_CSS[STYLES_CSS.index(".sched-header {"):]


class TestLoadingEmptyErrorStates(unittest.TestCase):
    def test_57_loading_state_used_in_overview_and_lists(self):
        for name in ("_schedRenderOverview", "_schedRenderGroupsListBody", "_schedRenderDraftsListBody"):
            body = _fn_body(name)
            self.assertIn("uiLoadingRows(", body)

    def test_58_empty_states_present(self):
        overview = _fn_body("_schedRenderOverview")
        self.assertIn("uiEmptyState(", overview)
        self.assertIn("Синхронизация ещё не выполнялась", overview)
        groups = _fn_body("_schedRenderGroupsListBody")
        self.assertIn("uiEmptyState(", groups)
        drafts = _fn_body("_schedRenderDraftsListBody")
        self.assertIn("uiEmptyState(", drafts)

    def test_59_error_states_have_retry(self):
        overview = _fn_body("_schedRenderOverview")
        self.assertIn("uiErrorState(", overview)
        self.assertIn("_schedLoadStatus()", overview)
        groups = _fn_body("_schedRenderGroupsListBody")
        self.assertIn('uiErrorState(_schedState.groupsError, "_schedLoadGroups()")', groups)


class TestSyncProgress(unittest.TestCase):
    def test_60_progress_shows_real_counters_not_a_fake_bar(self):
        body = _fn_body("_schedSyncProgressHtml")
        for field in ("groups_found", "groups_processed", "lessons_fetched", "students_found", "errors_count"):
            self.assertIn(field, body)
        self.assertNotIn("width:", body, "must not render a fabricated percentage progress bar")

    def test_60b_overview_polls_while_sync_running(self):
        overview = _fn_body("_schedRenderOverview")
        self.assertIn("s.runningSnapshot", overview)
        self.assertIn("_schedSyncProgressHtml(", overview)
        status_loader = _fn_body("_schedLoadStatus")
        self.assertIn("_schedStartPolling(", status_loader)


class TestGroupCardsAndDetail(unittest.TestCase):
    def test_61_group_card_shows_confidence_and_actions(self):
        body = _fn_body("_schedGroupCardHtml")
        self.assertIn("SCHED_CONFIDENCE_LABELS", body)
        self.assertIn("SCHED_CONFIDENCE_TONE", body)
        self.assertIn("_schedOpenGroupDetail(", body)
        self.assertIn("g.mk_class_id", body, "MoyKlass id shown as technical detail, not the main title")

    def test_group_detail_shows_evidence_and_status(self):
        body = _fn_body("_schedRenderGroupDetail")
        self.assertIn("SCHED_CONTINUATION_LABELS", body)
        self.assertIn("SCHED_MATCH_LABELS", body)
        self.assertIn("d.linkedDraft", body)


class TestFiltersSearchPagination(unittest.TestCase):
    def test_65_filters_are_sent_server_side(self):
        loader = _fn_body("_schedLoadGroups")
        self.assertIn('apiGet(`/api/schedule/groups?', loader)
        self.assertIn('qp.set("confidence"', loader)
        self.assertIn('qp.set("weekday"', loader)

    def test_66_search_is_server_side_not_client_filtered(self):
        loader = _fn_body("_schedLoadGroups")
        self.assertIn('qp.set("search", f.search)', loader)
        self.assertNotIn(".filter(g => g.name", APP_JS)

    def test_67_bounded_pagination_never_loads_thousands(self):
        for loader in ("_schedLoadGroups", "_schedLoadDrafts", "_schedLoadMembers"):
            body = _fn_body(loader)
            self.assertRegex(body, r'limit[\'":\s]')
        # server clamps to <=200 regardless of what the client asks for
        web_app_server = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("min(_safe_int(params.get(\"limit\"), 50), 200)", web_app_server)


class TestNoOverflowAndSharedShell(unittest.TestCase):
    def test_68_71_no_fixed_pixel_widths_in_new_css(self):
        # exclude min-width/max-width (tap targets, responsive caps are
        # intentional) — only a bare "width: Npx" risks horizontal overflow.
        self.assertNotRegex(SCHED_CSS_BLOCK, r"(?<!-)width:\s*\d+px")

    def test_69_reuses_existing_safe_area_shell_no_new_fixed_positioning(self):
        self.assertNotIn("position: fixed", SCHED_CSS_BLOCK)
        self.assertNotIn("position:fixed", SCHED_CSS_BLOCK)

    def test_70_bottom_nav_tab_present(self):
        self.assertIn('data-tab="schedule-foundation"', INDEX_HTML)

    def test_71_subtabs_scroll_instead_of_wrapping_or_overflowing(self):
        self.assertIn("overflow-x: auto", SCHED_CSS_BLOCK)


if __name__ == "__main__":
    unittest.main()
