"""Tests for v7.1.17.1 — ALE-6 sections 5/8/9: name-primary/ID-secondary
rendering, the Data Quality block, and the scoped (never global) light
theme for the schedule workspace. Static text/AST-style checks against
app.js/styles.css (this repo's existing frontend test convention — real
rendered evidence gathered separately via Playwright, see the release
report).

Run:
    python -m unittest tests.test_schedule_scoped_light_theme_v71171 -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
STYLES_CSS = (ROOT / "miniapp" / "styles.css").read_text(encoding="utf-8")


def _fn_body(name: str) -> str:
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\([^)]*\) \{(.*?)\n\}\n", APP_JS, re.S)
    assert m, f"function {name} not found"
    return m.group(1)


SCHED_CSS_BLOCK = STYLES_CSS[STYLES_CSS.index(".sched-header {"):]


class TestNamePrimaryIdSecondary(unittest.TestCase):
    def test_shared_helper_shows_name_primary_id_secondary_with_fallback(self):
        body = _fn_body("_schedMemberNameHtml")
        self.assertIn("sched-member-row__name", body)
        self.assertIn("sched-member-row__tech", body)
        self.assertIn("Имя не найдено", body, "explicit fallback text, never silently showing the raw ID as if it were a name")
        self.assertIn("MoyKlass ID", body)

    def test_no_remaining_id_fallback_used_as_name_text_in_schedule_module(self):
        # the old `s.child_display_name || \`ID ${s.mk_user_id}\`` pattern
        # must be gone from every schedule member-row render site (other
        # unrelated features — teacher binding, food module — use a similar
        # pattern for a different entity and are out of ALE-6's scope).
        sched_start = APP_JS.index("const _schedState = {")
        sched_js = APP_JS[sched_start:]
        self.assertNotIn("|| `ID ${", sched_js)

    def test_all_three_member_row_sites_use_the_shared_helper(self):
        for fn_name in ("_schedRenderGroupDetail", "_schedRenderMembersListBody", "_schedRenderDraftEditor"):
            if f"function {fn_name}(" not in APP_JS:
                continue
            body = _fn_body(fn_name)
            self.assertIn("_schedMemberNameHtml(", body, f"{fn_name} must use the shared name-primary helper")


class TestDataQualityBlock(unittest.TestCase):
    def test_data_quality_block_rendered_in_overview(self):
        overview = _fn_body("_schedRenderOverview")
        self.assertIn("_schedDataQualityHtml(", overview)

    def test_data_quality_block_shows_raw_vs_regular_distinction(self):
        body = _fn_body("_schedDataQualityHtml")
        self.assertIn("raw_unique_student_ids_count", body)
        self.assertIn("regular_confirmed_count", body)
        self.assertIn("regular_inferred_high_count", body)
        self.assertIn("regular_inferred_medium_count", body)
        self.assertIn("excluded_irregular_count", body)
        self.assertIn("insufficient_evidence_count", body)
        self.assertIn("ambiguous_regularity_count", body)
        # explicit "this is NOT the regular roster" framing
        self.assertIn("НЕ значит", body)

    def test_data_quality_block_shows_continuation_and_availability_detail(self):
        body = _fn_body("_schedDataQualityHtml")
        self.assertIn("continuation_detail_breakdown", body)
        self.assertIn("availability_detail_breakdown", body)
        for key in ("status_not_found", "awaiting_confirmation", "ambiguous_multiple_records"):
            self.assertIn(key, body)
        for key in ("no_onboarding_record", "parent_not_connected", "invited_not_filled"):
            self.assertIn(key, body)

    def test_data_quality_block_surfaces_sync_errors(self):
        body = _fn_body("_schedDataQualityHtml")
        self.assertIn("sync_errors_count", body)


class TestScopedLightThemeCss(unittest.TestCase):
    def test_scheduleFoundationRoot_pins_color_scheme_light(self):
        self.assertIn("#scheduleFoundationRoot { color-scheme: light; }", STYLES_CSS)

    def test_no_dark_media_query_targets_sched_classes_anymore(self):
        # the OLD bug: a @media(prefers-color-scheme:dark) block flipped
        # .sched-* backgrounds dark WITHOUT a matching text-color override,
        # making cards unreadable. That whole incomplete override must be
        # gone, not patched with more conditional branches.
        for m in re.finditer(r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}", STYLES_CSS, re.S):
            self.assertNotIn(".sched-", m.group(1), "no dark-mode media query may target .sched-* anymore")

    def test_no_data_theme_dark_selector_targets_sched_classes(self):
        for line in STYLES_CSS.splitlines():
            if ':root[data-theme="dark"]' in line:
                self.assertNotIn(".sched-", line, f"scoped light theme must not have a dark variant: {line!r}")

    def test_scoped_root_forces_light_on_core_components(self):
        for selector_fragment in (
            "#scheduleFoundationRoot .sched-stat-card",
            "#scheduleFoundationRoot .sched-group-card",
            "#scheduleFoundationRoot .sched-member-row",
            "#scheduleFoundationRoot .sched-sync-progress",
            "#scheduleFoundationRoot .sched-quality-block",
        ):
            self.assertIn(selector_fragment, STYLES_CSS)

    def test_scoped_root_forces_light_on_inputs_selects_and_focus_state(self):
        self.assertIn("#scheduleFoundationRoot .sched-search-input", STYLES_CSS)
        self.assertIn("#scheduleFoundationRoot .sched-filter-bar select", STYLES_CSS)
        self.assertIn("#scheduleFoundationRoot .sched-search-input:focus", STYLES_CSS)

    def test_scoped_root_forces_light_on_disabled_state(self):
        self.assertIn(":disabled", SCHED_CSS_BLOCK)

    def test_confirm_modal_scope_class_only_forces_light_when_opened_from_schedule(self):
        # the shared confirm modal lives outside #scheduleFoundationRoot in
        # the DOM, so it needs its own opt-in scope class rather than a
        # descendant selector — never a global change to every confirm sheet.
        self.assertIn("#uiConfirmModal.sched-scope", STYLES_CSS)
        confirm_fn = _fn_body("uiConfirmSheet")
        self.assertIn("scopeClass", confirm_fn)
        foundation_confirm = _fn_body("_schedConfirmGenerateFoundation")
        self.assertIn('scopeClass: "sched-scope"', foundation_confirm)

    def test_confirm_modal_scope_class_is_cleared_before_every_open(self):
        # must never leak forward onto an unrelated screen's confirm sheet.
        confirm_fn = _fn_body("uiConfirmSheet")
        self.assertIn('classList.remove("sched-scope")', confirm_fn)

    def test_data_quality_table_styles_exist(self):
        self.assertIn(".sched-quality-block {", STYLES_CSS)
        self.assertIn(".sched-quality-table", STYLES_CSS)

    def test_global_app_theme_mechanism_untouched(self):
        # ALE-6 point 9 — explicitly NOT a global forced-light change: the
        # rest of the app's :root color-scheme / data-theme plumbing must
        # be completely unaffected by this feature.
        self.assertIn("color-scheme: light only;", STYLES_CSS)


class TestRegularityBadgeVocabulary(unittest.TestCase):
    def test_regularity_labels_cover_every_classifier_category(self):
        categories = (
            "regular_confirmed", "regular_inferred_high", "regular_inferred_medium",
            "trial", "makeup", "one_off", "other_group_visitor", "insufficient_evidence", "ambiguous",
        )
        for cat in categories:
            self.assertIn(f'{cat}:', APP_JS.split("SCHED_REGULARITY_LABELS")[1][:2000])


if __name__ == "__main__":
    unittest.main()
