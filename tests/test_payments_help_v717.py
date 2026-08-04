"""Tests for v7.1.7 — Payments Workspace "Помощь по оплатам" screen.

Full-chain instructions for client_manager and other Payments Workspace
roles: MoyKlass subscription/invoice -> internal payment intent -> bePaid ->
webhook -> posting back to MoyKlass. Opened via a question-mark button in
.ws-header — deliberately NOT a 6th Workspace tab. Reuses the existing
.help-* component set (clientManagerHelpHtml()) adapted with SVG icons and
the Workspace .card/--yellow visual language; no second design system.

Covers (numbered to match the spec's 31-item test list):
  1.  Help button visible when canUsePaymentsWorkspace
  2.  Help button hidden for ineligible roles (conditional, not canAdminPilot)
  3.  SVG question icon, no emoji
  4.  Opening the help screen hides tabs/content and shows #wsHelpScreen
  5.  Closing restores the previous tab without touching _wsState
  6.  Title + subtitle text
  7.  Daily checklist (7 items)
  8.  10-step automation chain
  9.  All 6 Overview indicators
  10. All 10 main payment statuses
  11. All 4 pilot automation modes
  12. Client connection flow (12 items)
  13. CL- vs YC- code distinction called out
  14. All 10 FAQ items
  15. Escalation section
  16. Reuses .help-accordion (not a new accordion component)
  17. Reuses .help-route/.help-route-step (not a new stepper component)
  18. Search is implemented
  19. Search input is never recreated (classList-only filtering, no re-render)
  20. Empty search state
  21. Reset search
  22. Safe bottom padding (ws-bottom-safe-pad)
  23. Mobile-responsive CSS (header doesn't overflow at 360-390px)
  24. The temporary local dev preview (payments-help) has been fully removed
      from index.html — no dev_preview/LOCAL PREVIEW/mock profile markers
      remain anywhere in production source
  25. No stray preview markers remain in app.js either
  26. The help screen is reachable only via the real header button, not any
      leftover auto-open preview script
  27. Five Workspace tabs unchanged
  28. Overview / Attention / All Payments / Pilot Clients renderers untouched
  29. Connection tab renderer/state untouched
  30. Backend/security foundation untouched this phase
  31. Food Module / payment business logic untouched

Static analysis only (reads source as text), matching this project's existing
test convention.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
STYLES_CSS = ROOT / "miniapp" / "styles.css"
SERVER_PY = ROOT / "web_app_server.py"
STORAGE_PY = ROOT / "storage.py"

_js_cache: str | None = None
_html_cache: str | None = None
_css_cache: str | None = None
_server_cache: str | None = None
_storage_cache: str | None = None


def _js() -> str:
    global _js_cache
    if _js_cache is None:
        _js_cache = APP_JS.read_text(encoding="utf-8")
    return _js_cache


def _html() -> str:
    global _html_cache
    if _html_cache is None:
        _html_cache = INDEX_HTML.read_text(encoding="utf-8")
    return _html_cache


def _css() -> str:
    global _css_cache
    if _css_cache is None:
        _css_cache = STYLES_CSS.read_text(encoding="utf-8")
    return _css_cache


def _server() -> str:
    global _server_cache
    if _server_cache is None:
        _server_cache = SERVER_PY.read_text(encoding="utf-8")
    return _server_cache


def _storage() -> str:
    global _storage_cache
    if _storage_cache is None:
        _storage_cache = STORAGE_PY.read_text(encoding="utf-8")
    return _storage_cache


def _js_fn(name: str, *, is_async: bool = False, window: int = 8000) -> str:
    js = _js()
    needle = f"{'async ' if is_async else ''}function {name}("
    start = js.find(needle)
    assert start != -1, f"{needle} not found in app.js"
    end = js.find("\nasync function ", start + 1)
    end2 = js.find("\nfunction ", start + 1)
    candidates = [e for e in (end, end2) if e != -1]
    end = min(candidates) if candidates else start + window
    return js[start:end]


def _const(name: str, window: int = 4000) -> str:
    js = _js()
    start = js.find(f"const {name} = ")
    assert start != -1, f"const {name} not found in app.js"
    end = js.find("\n\n", start)
    if end == -1 or end - start > window:
        end = start + window
    return js[start:end]


# ---------------------------------------------------------------------------
# 1-3: help button
# ---------------------------------------------------------------------------

class TestHelpButton(unittest.TestCase):
    def test_01_button_gated_by_canUsePaymentsWorkspace(self):
        skeleton = _js_fn("_renderWorkspaceSkeleton")
        idx = skeleton.find('id="wsHelpBtn"')
        self.assertNotEqual(idx, -1)
        segment = skeleton[max(0, idx - 150):idx]
        self.assertIn("caps.canUsePaymentsWorkspace", segment)

    def test_02_button_not_using_canAdminPilot(self):
        skeleton = _js_fn("_renderWorkspaceSkeleton")
        idx = skeleton.find('id="wsHelpBtn"')
        segment = skeleton[max(0, idx - 150):idx]
        self.assertNotIn("canAdminPilot", segment)

    def test_03_svg_icon_no_emoji(self):
        js = _js()
        self.assertIn("const WS_ICON_HELP = `<svg", js)
        idx = js.find('id="wsHelpBtn"')
        segment = js[idx:idx + 200]
        self.assertIn("WS_ICON_HELP", segment)
        self.assertIn('aria-label="Помощь по оплатам"', segment)


# ---------------------------------------------------------------------------
# 4-5: open/close, tab preservation
# ---------------------------------------------------------------------------

class TestOpenClose(unittest.TestCase):
    def test_04_open_hides_tabs_shows_help(self):
        fn = _js_fn("_wsOpenHelp")
        self.assertIn('$("wsSubtabs")?.classList.add("hidden")', fn)
        self.assertIn('$("wsTabContent")?.classList.add("hidden")', fn)
        self.assertIn('classList.remove("hidden")', fn)

    def test_05_close_restores_without_touching_wsState(self):
        fn = _js_fn("_wsCloseHelp")
        self.assertIn('$("wsSubtabs")?.classList.remove("hidden")', fn)
        self.assertIn('$("wsTabContent")?.classList.remove("hidden")', fn)
        self.assertNotIn("_wsState.tab =", fn)
        self.assertNotIn("loadPaymentsWorkspace(", fn)
        self.assertNotIn("_wsRenderCurrentTab(", fn)


# ---------------------------------------------------------------------------
# 6: title/subtitle
# ---------------------------------------------------------------------------

class TestTitle(unittest.TestCase):
    def test_06_title_and_subtitle(self):
        fn = _js_fn("_wsHelpScreenHtml")
        self.assertIn("Помощь по оплатам", fn)
        self.assertIn("Как контролировать автоматизацию счетов и действий клиентов", fn)
        self.assertIn("Назад к оплатам", fn)


# ---------------------------------------------------------------------------
# 7-15: content sections
# ---------------------------------------------------------------------------

class TestContentSections(unittest.TestCase):
    DAILY_CHECKLIST = [
        "Откройте «Обзор»", "Проверьте «Требуют внимания»",
        "Подтвердите подготовленные счета в режиме «Проверка»",
        "Убедитесь, что ожидающие оплаты счета отправлены клиентам",
        "Проверьте оплаченные счета и внесение в МойКласс",
        "При ошибке откройте карточку и прочитайте причину",
        "Если клиент не видит счёт", "проверьте «Подключение»",
    ]
    CHAIN_TITLES = [
        "Клиент и абонемент в МойКласс", "Счёт в МойКласс",
        "Платёжный счёт в Yellow Club Agent", "Проверка данных",
        "Создание оплаты в bePaid", "Отправка клиенту", "Ожидание оплаты",
        "Получение результата bePaid", "Внесение оплаты в МойКласс",
        "Завершение и контроль",
    ]
    METRIC_LABELS = ["На проверке", "Требуют внимания", "Ожидают оплаты",
                      "Оплачено", "Внесено в МойКласс", "Сейчас в пилоте"]
    STATUS_LABELS = ["Черновик", "Готов к выставлению", "На проверке",
                      "Ожидает оплаты", "Оплачено", "Внесено в МойКласс",
                      "Требует проверки", "Ошибка", "Отозван", "Отменён"]
    PILOT_MODES = ["Проверка", "Наблюдение", "Авто", "Отключён"]
    CONNECTION_STEPS = [
        "Откройте вкладку «Подключение»", "Найдите клиента в МойКласс",
        "Проверьте статус", "CL-XXXXXXXX",
        "Передайте код клиенту или родителю",
        "После успешной привязки клиент видит свои счета",
        "Код нельзя использовать повторно", "Код действует 72 часа",
        "отзовите его и создайте новый",
        "сначала выполните явную отвязку",
        "не удаляет счета, оплаты, историю и данные МойКласс",
    ]
    FAQ_QUESTIONS = [
        "Клиент не видит счёт", "Нет привязки родителя",
        "Найдено несколько родителей", "Сумма не совпадает",
        "bePaid не создал оплату", "Оплата прошла, но не внесена в МойКласс",
        "Счёт отозван", "Код подключения не работает",
        "Слишком много попыток", "Ошибка без понятного действия",
    ]

    def test_07_daily_checklist(self):
        fn = _js_fn("_wsHelpQuickStartHtml")
        for item in self.DAILY_CHECKLIST:
            self.assertIn(item, fn, f"missing checklist item: {item}")

    def test_08_ten_step_chain(self):
        fn = _js_fn("_wsHelpChainHtml")
        for title in self.CHAIN_TITLES:
            self.assertIn(title, fn, f"missing chain step: {title}")
        # user-facing term, not the raw internal name, unexplained
        self.assertIn("платёжный счёт", fn.lower())

    def test_09_six_overview_metrics(self):
        metrics = _const("WS_HELP_METRICS")
        for label in self.METRIC_LABELS:
            self.assertIn(label, metrics, f"missing metric: {label}")
        # same icons Overview itself uses (WS_OVERVIEW_STAT_META)
        for icon in ("WS_ICON_CLOCK", "WS_ICON_ALERT_TRIANGLE", "WS_ICON_WALLET",
                     "WS_ICON_CHECK_CIRCLE", "WS_ICON_FILE_CHECK", "WS_ICON_USERS"):
            self.assertIn(icon, metrics)

    def test_10_ten_statuses(self):
        statuses = _const("WS_HELP_STATUSES")
        for label in self.STATUS_LABELS:
            self.assertIn(label, statuses, f"missing status: {label}")
        # raw internal codes must never appear as user-facing text
        for raw in ("requires_check", "pending_review", "withdrawn", "posted_to_moyklass"):
            self.assertNotIn(f'"{raw}"', statuses)
            self.assertNotIn(f"label: \"{raw}", statuses)

    def test_11_four_pilot_modes(self):
        modes = _const("WS_HELP_MODES")
        for label in self.PILOT_MODES:
            self.assertIn(label, modes, f"missing mode: {label}")

    def test_12_connection_flow(self):
        fn = _js_fn("_wsHelpConnectionHtml")
        for item in self.CONNECTION_STEPS:
            self.assertIn(item, fn, f"missing connection step: {item}")

    def test_13_cl_vs_yc_distinction(self):
        fn = _js_fn("_wsHelpConnectionHtml")
        self.assertIn("YC-XXXX", fn)
        self.assertIn("относятся только к питанию", fn)

    def test_14_ten_faq_items(self):
        faq = _const("WS_HELP_FAQ")
        for q in self.FAQ_QUESTIONS:
            self.assertIn(q, faq, f"missing FAQ question: {q}")

    def test_15_escalation_section(self):
        fn = _js_fn("_wsHelpEscalationHtml")
        for item in ("Ошибка повторяется после устранения причины",
                     "Сумма в bePaid и МойКласс различается",
                     "Клиент подключён не к тому Telegram",
                     "техническую ошибку",
                     "изменить глобальные настройки автоматизации",
                     "восстановить данные или историю"):
            self.assertIn(item, fn, f"missing escalation item: {item}")
        self.assertIn("Telegram initData", fn)


# ---------------------------------------------------------------------------
# 16-17: reuse of existing help-* components
# ---------------------------------------------------------------------------

class TestComponentReuse(unittest.TestCase):
    def test_16_reuses_help_accordion(self):
        statuses_fn = _js_fn("_wsHelpStatusesHtml")
        faq_fn = _js_fn("_wsHelpFaqHtml")
        self.assertIn("help-accordion", statuses_fn)
        self.assertIn("help-accordion", faq_fn)
        # No brand-new accordion component/class defined for this screen.
        js = _js()
        self.assertNotIn("ws-help-accordion", js)

    def test_17_reuses_help_route(self):
        quickstart = _js_fn("_wsHelpQuickStartHtml")
        chain = _js_fn("_wsHelpChainHtml")
        connection = _js_fn("_wsHelpConnectionHtml")
        for fn in (quickstart, chain, connection):
            self.assertIn("help-route", fn)
            self.assertIn("help-route-step", fn)
            self.assertIn("help-route-num", fn)


# ---------------------------------------------------------------------------
# 18-21: search
# ---------------------------------------------------------------------------

class TestSearch(unittest.TestCase):
    def test_18_search_implemented(self):
        js = _js()
        self.assertIn("function _wsHelpApplySearch(", js)
        self.assertIn('id="wsHelpSearchInput"', js)

    def test_19_search_input_never_recreated(self):
        wire_fn = _js_fn("_wsWireHelpSearch")
        apply_fn = _js_fn("_wsHelpApplySearch")
        self.assertNotIn("innerHTML", apply_fn)
        self.assertIn("classList", apply_fn)
        self.assertIn("addEventListener", wire_fn)
        # The toolbar/input is only ever built once per open, in _wsOpenHelp
        # (via screen.dataset.rendered guard) — never rebuilt by search itself.
        self.assertNotIn("_wsHelpScreenHtml", apply_fn)

    def test_20_empty_search_state(self):
        html_fn = _js_fn("_wsHelpScreenHtml")
        self.assertIn('id="wsHelpEmpty"', html_fn)
        self.assertIn("Ничего не найдено", html_fn)
        apply_fn = _js_fn("_wsHelpApplySearch")
        self.assertIn("wsHelpEmpty", apply_fn)

    def test_21_reset_search(self):
        fn = _js_fn("_wsHelpResetSearch")
        self.assertIn('$("wsHelpSearchInput")', fn)
        self.assertIn("_wsHelpApplySearch", fn)
        html_fn = _js_fn("_wsHelpScreenHtml")
        self.assertIn("wsHelpResetBtn", html_fn)


# ---------------------------------------------------------------------------
# 22-23: layout / mobile
# ---------------------------------------------------------------------------

class TestLayout(unittest.TestCase):
    def test_22_bottom_safe_pad(self):
        fn = _js_fn("_wsHelpScreenHtml")
        idx = fn.find('id="wsHelpSections"')
        segment = fn[idx:idx + 60]
        self.assertIn("ws-bottom-safe-pad", segment)

    def test_23_mobile_responsive_css(self):
        css = _css()
        self.assertIn(".ws-help-btn", css)
        idx = css.find(".ws-help-btn {")
        self.assertNotEqual(idx, -1)
        segment = css[idx:idx + 400]
        self.assertIn("44px", segment)
        self.assertIn("@media (max-width: 360px)", css)


# ---------------------------------------------------------------------------
# 24-26: local preview — REMOVED for v7.1.7 release cleanup. The temporary
# localhost-only preview (payments-connection and payments-help modes, the
# window.fetch mock, the LOCAL PREVIEW badge) was deleted from index.html
# once both screens were visually approved. These tests now assert the
# removal is complete rather than testing preview internals that no longer
# exist.
# ---------------------------------------------------------------------------

class TestPreviewRemoved(unittest.TestCase):
    def test_24_no_payments_help_preview_markers(self):
        html = _html()
        for marker in ("dev_preview", "LOCAL PREVIEW", "payments-help",
                       "payments-connection", "__YC_DEV_PREVIEW__", "Preview Client Manager"):
            self.assertNotIn(marker, html, f"leftover preview marker: {marker}")

    def test_25_no_preview_markers_in_app_js(self):
        js = _js()
        for marker in ("dev_preview", "LOCAL PREVIEW", "__YC_DEV_PREVIEW__", "Preview Client Manager"):
            self.assertNotIn(marker, js, f"leftover preview marker: {marker}")

    def test_26_help_screen_still_reachable_via_real_button_only(self):
        # Regression: _wsOpenHelp is still wired only to the real header
        # button (no preview code calls it on load anymore).
        html = _html()
        js = _js()
        self.assertNotIn("_wsOpenHelp()", html)  # no inline-script auto-open left in index.html
        self.assertIn('onclick="_wsOpenHelp()"', js)


# ---------------------------------------------------------------------------
# 27-31: regression
# ---------------------------------------------------------------------------

class TestNoRegression(unittest.TestCase):
    def test_27_five_tabs_unchanged(self):
        skeleton = _js_fn("_renderWorkspaceSkeleton")
        for tab_id in ("overview", "attention", "all-payments", "pilot-clients", "connection"):
            self.assertIn(f'id: "{tab_id}"', skeleton)
        # help is not a 6th tab entry
        self.assertNotIn('id: "help"', skeleton)
        self.assertNotIn('id: "payments-help"', skeleton)

    def test_28_four_approved_renderers_untouched(self):
        for name in ("_wsRenderOverview", "_wsRenderAttention", "_wsRenderAllPayments", "_wsRenderPilotClients"):
            fn = _js_fn(name)
            self.assertNotIn("_wsHelp", fn, f"{name} must not reference any help-screen code")
            self.assertNotIn("wsHelpScreen", fn)

    def test_29_connection_tab_untouched(self):
        fn = _js_fn("_wsRenderConnection")
        self.assertNotIn("_wsHelp", fn)
        self.assertIn('id="wsConnSearchInput"', fn)  # still the same tab, unmodified

    def test_30_backend_security_untouched(self):
        server = _server()
        storage = _storage()
        self.assertIn("CLIENT_LINK_ADMIN_ROLES", server)
        self.assertIn("CLIENT_LINK_MAX_FAILED_ATTEMPTS", server)
        self.assertIn("canManageClientLinks", server)
        self.assertIn("client_link_rate_limit_attempts", storage)
        self.assertIn("CLIENT_LINK_CODE_TTL_HOURS = 72", storage)

    def test_31_food_and_payment_logic_untouched(self):
        storage = _storage()
        server = _server()
        self.assertIn("parent_child_links", storage)
        self.assertIn("camp_children", storage)
        self.assertIn("def list_client_visible_payment_intents(", storage)
        self.assertIn("/api/food/link-child", server)


if __name__ == "__main__":
    unittest.main()
