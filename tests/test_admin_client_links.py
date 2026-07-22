"""Tests for v7.0.93.2 — Admin UI for client link codes.

Covers:
  Frontend static analysis:
    1.  Admin sees 'client-links' tab (in index.html subtabs)
    2.  Version marker is v7.0.93.2 in app.js
    3.  Cache-bust is v=7.0.93.2 in index.html
    4.  renderClientLinksPanel function exists in app.js
    5.  clSearchStudents function exists in app.js
    6.  _clGenerateCode function exists in app.js
    7.  Copy code button action exists in app.js
    8.  _clInvalidateCode function exists in app.js
    9.  _clUnlinkParent function exists in app.js
    10. /api/client/admin/search-students endpoint called in app.js
    11. Clipboard copy uses navigator.clipboard
    12. Unlink confirm dialog mentions питание not изменится
    13. parent-only class not on client-links tab (admin-only)
    14. clientLinksSearchQuery in state
    15. CL- code hint text present (explains to parent what to do)

  Backend (server method tests via storage):
    16. admin_client_search_students route exists in server (GET routing)
    17. admin_client_generate_code uses client link admin roles (not payment roles)
    18. CLIENT_LINK_ADMIN_ROLES constant exists in web_app_server
    19. client-links in adminTabs for owner role
    20. client-links not in adminTabs for methodist role
    21. client-links not in adminTabs for parent / teacher roles
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
SERVER_PY = ROOT / "web_app_server.py"


class Test01Frontend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_01_client_links_tab_in_html(self):
        self.assertIn('data-admin-tab="client-links"', self.html)

    def test_02_version_marker(self):
        self.assertIn('console.log("MiniApp version: v7.0.99.1")', self.js)

    def test_03_cache_bust(self):
        self.assertIn("v=7.0.99.1", self.html)

    def test_04_renderClientLinksPanel_exists(self):
        self.assertIn("function renderClientLinksPanel(", self.js)

    def test_05_clSearchStudents_exists(self):
        self.assertIn("async function clSearchStudents(", self.js)

    def test_06_clGenerateCode_exists(self):
        self.assertIn("async function _clGenerateCode(", self.js)

    def test_07_copy_action_exists(self):
        self.assertIn('"copy-code"', self.js)
        self.assertIn("📋 Копировать", self.js)

    def test_08_clInvalidateCode_exists(self):
        self.assertIn("async function _clInvalidateCode(", self.js)

    def test_09_clUnlinkParent_exists(self):
        self.assertIn("async function _clUnlinkParent(", self.js)

    def test_10_search_students_endpoint_called(self):
        self.assertIn("/api/client/admin/search-students", self.js)

    def test_11_clipboard_api_used(self):
        self.assertIn("navigator.clipboard.writeText", self.js)

    def test_12_unlink_dialog_mentions_food_unchanged(self):
        self.assertIn("Питание не изменилось", self.js)

    def test_13_client_links_tab_not_parent_only(self):
        # The admin subtab button must not have class parent-only
        idx = self.html.find('data-admin-tab="client-links"')
        self.assertNotEqual(idx, -1)
        segment = self.html[max(0, idx - 100):idx + 100]
        self.assertNotIn("parent-only", segment)

    def test_14_clientLinksSearchQuery_in_state(self):
        self.assertIn("clientLinksSearchQuery", self.js)

    def test_15_cl_hint_text_present(self):
        self.assertIn("Родитель открывает Mini App", self.js)
        self.assertIn("CL-", self.js)


class Test02Backend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = SERVER_PY.read_text(encoding="utf-8")

    def test_16_search_students_route_in_server(self):
        self.assertIn("/api/client/admin/search-students", self.server)
        self.assertIn("admin_client_search_students", self.server)

    def test_17_admin_generate_uses_client_link_admin_check(self):
        # Must use _require_client_link_admin_access, not _require_payment_intent_access
        # Find the admin_client_generate_code method body
        start = self.server.find("def admin_client_generate_code(")
        end = self.server.find("\n    def ", start + 1)
        method_body = self.server[start:end]
        self.assertIn("_require_client_link_admin_access", method_body)
        self.assertNotIn("_require_payment_intent_access", method_body)

    def test_18_client_link_admin_roles_constant_exists(self):
        self.assertIn("CLIENT_LINK_ADMIN_ROLES", self.server)
        # Must include owner, admin, operations
        idx = self.server.find("CLIENT_LINK_ADMIN_ROLES")
        segment = self.server[idx:idx + 200]
        self.assertIn('"owner"', segment)
        self.assertIn('"admin"', segment)
        self.assertIn('"operations"', segment)

    def test_19_client_links_in_owner_admin_tabs(self):
        # Find ADMIN_TABS_BY_ROLE dict block, then look for owner entry within it
        start = self.server.find("ADMIN_TABS_BY_ROLE = {")
        end = self.server.find("\n}", start) + 2
        block = self.server[start:end]
        # owner line must contain "client-links"
        owner_idx = block.find('"owner":')
        owner_line_end = block.find("\n", owner_idx)
        owner_line = block[owner_idx:owner_line_end]
        self.assertIn('"client-links"', owner_line, f"owner adminTabs must include client-links. Got: {owner_line}")

    def test_20_client_links_not_in_methodist_tabs(self):
        start = self.server.find("ADMIN_TABS_BY_ROLE = {")
        end = self.server.find("\n}", start) + 2
        block = self.server[start:end]
        meth_idx = block.find('"methodist":')
        meth_line_end = block.find("\n", meth_idx)
        meth_line = block[meth_idx:meth_line_end]
        self.assertNotIn('"client-links"', meth_line)

    def test_21_teacher_and_parent_have_no_client_links(self):
        # teacher and parent are not keys in ADMIN_TABS_BY_ROLE
        idx = self.server.find("ADMIN_TABS_BY_ROLE")
        segment = self.server[idx:idx + 800]
        self.assertNotIn('"teacher":', segment)
        self.assertNotIn('"parent":', segment)


if __name__ == "__main__":
    unittest.main()
