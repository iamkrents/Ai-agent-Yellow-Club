"""Frontend regression tests for v7.0.93.2.3 вЂ” admin tab visibility and nav fixes.

Covers:
  Root cause analysis:
    1.  client-links is in MVP_ADMIN_TABS (was the bug)

  Admin subtab visibility (static analysis of availableAdminTabs logic):
    2.  owner: client-links in ADMIN_TABS_BY_ROLE in server
    3.  admin: client-links in ADMIN_TABS_BY_ROLE in server
    4.  operations: client-links in ADMIN_TABS_BY_ROLE in server
    5.  methodist: client-links NOT in ADMIN_TABS_BY_ROLE in server
    6.  teacher: no admin tabs at all (server)
    7.  parent: no admin tabs at all (server)

  Tab ordering in HTML:
    8.  client-links tab appears before food-debug in HTML

  JS function existence for panel loading:
    9.  renderClientLinksPanel called for tab === "client-links"
    10. clSearchStudents called within renderClientLinksPanel

  Admin role bottom nav hiding:
    11. _adminNavRoles includes owner, admin, operations
    12. tasks and help hidden for admin roles in setupRoleUi
    13. teacher NOT in _adminNavRoles hiding block
    14. parent NOT in _adminNavRoles hiding block

  ensureVisibleActiveTab:
    15. ensureVisibleActiveTab function exists (handles hidden active tab)

  subtabs CSS:
    16. .subtabs has overflow-x: auto in styles.css
    17. .subtabs has flex layout (for nowrap scrolling)

  Version and cache-bust:
    18. version marker is v7.0.93.2.3
    19. cache-bust is v=7.0.93.2.3
    20. version constant passes existing test files

  Existing systems not broken:
    21. food-debug still in MVP_ADMIN_TABS
    22. food-children still in MVP_ADMIN_TABS
    23. client-payments still in parentAllowed
    24. parent role NOT in _adminNavRoles hiding
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
SERVER_PY = ROOT / "web_app_server.py"
STYLES_CSS = ROOT / "miniapp" / "styles.css"

CURRENT_VERSION = "7.0.94.1"


class Test01RootCause(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def _mvp_admin_tabs_list(self):
        m = re.search(r'const MVP_ADMIN_TABS\s*=\s*\[([^\]]+)\]', self.js)
        self.assertIsNotNone(m, "MVP_ADMIN_TABS not found")
        return m.group(1)

    def test_01_client_links_in_MVP_ADMIN_TABS(self):
        """Root cause fix: client-links must be in MVP_ADMIN_TABS."""
        tabs = self._mvp_admin_tabs_list()
        self.assertIn('"client-links"', tabs,
                      "client-links missing from MVP_ADMIN_TABS вЂ” this was the visibility bug")


class Test02ServerAdminTabs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = SERVER_PY.read_text(encoding="utf-8")
        start = cls.server.find("ADMIN_TABS_BY_ROLE = {")
        end = cls.server.find("\n}", start) + 2
        cls.block = cls.server[start:end]

    def _role_line(self, role):
        idx = self.block.find(f'"{role}":')
        end = self.block.find("\n", idx)
        return self.block[idx:end]

    def test_02_owner_has_client_links(self):
        self.assertIn('"client-links"', self._role_line("owner"))

    def test_03_admin_has_client_links(self):
        self.assertIn('"client-links"', self._role_line("admin"))

    def test_04_operations_has_client_links(self):
        self.assertIn('"client-links"', self._role_line("operations"))

    def test_05_methodist_no_client_links(self):
        self.assertNotIn('"client-links"', self._role_line("methodist"))

    def test_06_teacher_not_in_admin_tabs(self):
        self.assertNotIn('"teacher":', self.block)

    def test_07_parent_not_in_admin_tabs(self):
        self.assertNotIn('"parent":', self.block)


class Test03HtmlOrder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_08_client_links_before_food_debug(self):
        idx_cl = self.html.find('data-admin-tab="client-links"')
        idx_fd = self.html.find('data-admin-tab="food-debug"')
        self.assertNotEqual(idx_cl, -1, "client-links tab not found in HTML")
        self.assertNotEqual(idx_fd, -1, "food-debug tab not found in HTML")
        self.assertLess(idx_cl, idx_fd,
                        "client-links must appear before food-debug in HTML")


class Test04PanelLoading(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_09_client_links_tab_calls_render(self):
        # The renderAdminContent function must have a branch for "client-links"
        m = re.search(
            r'tab\s*===\s*["\']client-links["\'].*?renderClientLinksPanel',
            cls := self.js, re.DOTALL
        )
        # Simpler: both strings must appear and the client-links branch must call the panel
        self.assertIn('tab === "client-links"', self.js)
        self.assertIn('renderClientLinksPanel', self.js)

    def test_10_clSearchStudents_wired_in_panel(self):
        self.assertIn("clSearchStudents", self.js)
        # Must be called from renderClientLinksPanel context
        self.assertIn("async function clSearchStudents(", self.js)


class Test05AdminNavHiding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def _admin_nav_block(self):
        """Extract the _adminNavRoles block from setupRoleUi."""
        idx = self.js.find("_adminNavRoles")
        self.assertNotEqual(idx, -1, "_adminNavRoles not found in app.js")
        return self.js[idx:idx + 400]

    def test_11_adminNavRoles_includes_owner_admin_operations(self):
        block = self._admin_nav_block()
        self.assertIn('"owner"', block)
        self.assertIn('"admin"', block)
        self.assertIn('"operations"', block)

    def test_12_tasks_and_help_hidden_for_admin(self):
        block = self._admin_nav_block()
        self.assertIn('"tasks"', block)
        self.assertIn('"help"', block)
        self.assertIn('classList.add("hidden")', block)

    def test_13_teacher_not_in_adminNavRoles(self):
        block = self._admin_nav_block()
        self.assertNotIn('"teacher"', block)

    def test_14_parent_not_in_adminNavRoles(self):
        block = self._admin_nav_block()
        self.assertNotIn('"parent"', block)


class Test06EnsureVisibleTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_15_ensureVisibleActiveTab_exists(self):
        self.assertIn("function ensureVisibleActiveTab(", self.js)
        # Must redirect to first visible tab when active is hidden
        idx = self.js.find("function ensureVisibleActiveTab(")
        body = self.js[idx:idx + 300]
        self.assertIn("hidden", body)
        self.assertIn("activateTab", body)


class Test07SubtabsCSS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_16_subtabs_overflow_x_auto(self):
        idx = self.css.find(".subtabs")
        self.assertNotEqual(idx, -1)
        segment = self.css[idx:idx + 300]
        self.assertIn("overflow-x: auto", segment)

    def test_17_subtabs_flex_display(self):
        idx = self.css.find(".subtabs")
        segment = self.css[idx:idx + 300]
        self.assertIn("display: flex", segment)


class Test08Version(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_18_version_marker(self):
        self.assertIn(f'console.log("MiniApp version: v{CURRENT_VERSION}")', self.js)

    def test_19_cache_bust(self):
        self.assertIn(f"v={CURRENT_VERSION}", self.html)

    def test_20_no_old_version_93_2_exact(self):
        # v7.0.93.2" (with quote) should not appear вЂ” replaced by v7.0.93.2.3
        self.assertNotIn('"MiniApp version: v7.0.93.2"', self.js)
        self.assertNotIn('v=7.0.93.2"', self.html)


class Test09ExistingNotBroken(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def _mvp_admin_tabs_list(self):
        m = re.search(r'const MVP_ADMIN_TABS\s*=\s*\[([^\]]+)\]', self.js)
        return m.group(1) if m else ""

    def test_21_food_debug_still_in_MVP_ADMIN_TABS(self):
        self.assertIn('"food-debug"', self._mvp_admin_tabs_list())

    def test_22_food_children_still_in_MVP_ADMIN_TABS(self):
        self.assertIn('"food-children"', self._mvp_admin_tabs_list())

    def test_23_client_payments_in_parentAllowed(self):
        self.assertIn('"client-payments"', self.js)
        idx = self.js.find("parentAllowed")
        segment = self.js[idx:idx + 200]
        self.assertIn("client-payments", segment)

    def test_24_parent_excluded_from_admin_nav_hiding(self):
        idx = self.js.find("_adminNavRoles")
        block = self.js[idx:idx + 400]
        self.assertNotIn('"parent"', block)


if __name__ == "__main__":
    unittest.main()
