# PROJECT STATUS — Yellow Club Mini App

_Последнее обновление: 2026-07-14 (v7.0.92.4)_

---

## Что сделано

### v7.0.92.4 — Fix: bePaid webhook signature (Base64) + unified prepare-options (текущая)

**Критическое исправление:** Все ERIP-вебхуки отклонялись с HTTP 401. Причина: `bytes.fromhex()` — код ожидал hex, bePaid присылает RSA-подпись в Base64. После исправления вебхуки проходят верификацию.

**Ключевые изменения:**

- **`web_app_server.py`:** `_bepaid_verify_signature` переписан — `bytes.fromhex()` → `base64.b64decode(validate=True)`; поддержка PEM и Base64-DER ключей; опциональный prefix `sha256=`/`sha1=` снимается перед декодом. `payment_intent_create_bepaid` получил `_bypass_method_check=False` param для внутреннего вызова из `prepare-options`. Новый метод `payment_intent_prepare_options` — идемпотентный, создаёт ERIP + acquiring за один вызов, preflight-проверка invoice. Маршрут `POST /api/payments/intents/{id}/prepare-options`. `payment_intents_list` добавляет `payment_options` к каждому intent (без `checkout_token`, только `has_checkout`).
- **`miniapp/app.js`:** кнопка «Подготовить способы оплаты»; `openMkInvoiceCreate` авто-вызывает `prepare-options`; `acqReadyBadge` в карточке; дублирующийся чип «Ожидает оплаты» устранён (суммируются `bepaid_created + awaiting_payment`). Версия v7.0.92.4.
- **`miniapp/index.html`:** cache-bust → `v=7.0.92.4`.
- **`tests/test_bepaid_signature.py`:** 14 тестов (RSA PKCS#1 v1.5 + SHA-256, Base64, PEM/DER, reject hex, reject modified body).
- **`tests/test_payment_options_flow.py`:** 10 тестов (flow, idempotency, fault isolation, status transitions, UI static checks).

**Итого тестов: 487/487 OK (16 skipped). +24 новых теста.**

---

### v7.0.92.3 — Feature: реальный bePaid acquiring (hosted checkout)

**Endpoint:** `POST https://checkout.bepaid.by/ctp/api/checkouts` с HTTP Basic Auth (ACQ credentials), заголовком `X-API-Version: 2`.

**Ключевые изменения:**

- **`bepaid_client.py`:** `BEPAID_ACQ_ENDPOINT_UNCONFIRMED` удалён → `BEPAID_CHECKOUT_ENDPOINT = "https://checkout.bepaid.by/ctp/api/checkouts"`. `create_acquiring_checkout(*, amount_minor, currency, description, tracking_id, notification_url, return_url, customer=None, test=False)` — реальная реализация с валидацией (int amount, BYN currency, HTTPS URLs). `_post_checkout` добавляет `X-API-Version: 2`. `_parse_checkout_response` парсит `{"checkout": {"token": ..., "redirect_url": ...}}`. `build_checkout_payload` static method.
- **`storage.py`:** `checkout_token TEXT` в `payment_intent_options` (в схеме + _ensure_column). `update_option_checkout(option_id, *, checkout_token, payment_url)`. `payment_intent_update_status(public_id, new_status)`.
- **`web_app_server.py`:** `payment_intent_create_acquiring_option(auth, public_id)` — idempotent; статус PI: draft/ready→partial_ready, bepaid_created→awaiting_payment. GET `/payment-return` — статическая страница. POST `/api/payments/intents/{id}/create-acquiring`.
- **`miniapp/app.js`:** версия v7.0.92.3; статусы `partial_ready`/`awaiting_payment`; кнопка "Открыть страницу оплаты картой"; `openAcquiringCheckout()`.
- **`miniapp/index.html`:** cache-bust v7.0.92.3.
- **`tests/test_bepaid_acquiring.py`:** 37 тестов (все pass). `tests/test_dual_channel.py` обновлён (2 теста заменены для нового stub → реальная реализация).

**Итого тестов: 463/463 OK.**

---

### v7.0.92.1.2 — Hotfix: ReferenceError escHtml в renderMkPaymentTypes

**Причина:** `renderMkPaymentTypes()` в `miniapp/app.js` использовала несуществующий alias `escHtml()` (6 вызовов) вместо проектного helper `escapeHtml()` (строка 240). После исправления v7.0.92.1.1 (загрузка заработала, данные стали приходить) — при попытке отрисовать ответ браузер бросал `ReferenceError: Can't find variable: escHtml` в `renderMkPaymentTypes`, и UI оставался пустым несмотря на HTTP 200.

**Ключевые изменения:**

- **`miniapp/app.js`:** 6 вызовов `escHtml(...)` в `renderMkPaymentTypes` → `escapeHtml(...)` (проектный helper, строка 240). Другие undefined aliases (`escAttr`, `esc_html` и др.) — не использовались, проверено.
- **Cache-bust:** `app.js?v=7.0.92.1.1` → `app.js?v=7.0.92.1.2`. CSS не менялась.
- **`tests/test_mk_payment_types_js.py`:** +11 тестов класса `TestRenderMkPaymentTypesJS` (тесты 12–22): нет `escHtml`, есть `escapeHtml`, все dynamic values экранируются, нет других undefined aliases, каждая ветка (error, no-config, single/multi candidate, items list) проверена. Итого 22/22 OK.

---

### v7.0.92.1.1 — Hotfix: ReferenceError в loadMkPaymentTypes

**Причина:** `loadMkPaymentTypes()` в `miniapp/app.js` вызывала несуществующую функцию `apiFetch()` вместо существующей `apiGet()`, а также дополнительно вызывала `.json()` на результате, хотя `apiGet` уже возвращает разобранный JSON. Это вызывало `ReferenceError` до отправки HTTP-запроса: backend-логи не фиксировали ни одного `GET /api/payments/moyklass/payment-types`, интерфейс навсегда зависал в «Загрузка…». `.catch()` не перехватывал ошибку, потому что исходная функция не была `async` — цепочка `.then/.catch` на `undefined` (возврат `apiFetch()`) выбрасывала синхронно.

**Ключевые изменения:**

- **`miniapp/app.js`:** `loadMkPaymentTypes` переписана как `async function`, `apiFetch` → `apiGet`, убран лишний `.then(r => r.json())`, добавлены `try/catch/finally`, блокировка кнопки `refreshMkPaymentTypes` во время запроса (защита от двойного клика), `finally` всегда разблокирует кнопку, `catch` заменяет «Загрузка…» на текст ошибки.
- **Cache-bust:** `app.js?v=7.0.92.1` → `app.js?v=7.0.92.1.1` (CSS не менялась).
- **`tests/test_mk_payment_types_js.py`:** 11 regression-тестов (статический анализ JS-текста): нет `apiFetch`, есть `apiGet`, нет `.json()`, `async`, кнопка блокируется/разблокируется, guard двойного клика, `catch` пишет ошибку, `renderMkPaymentTypes` вызывается при успехе.
- **Backend, webhook, MoyKlass posting, `.env` — не тронуты.**

---

### v7.0.92.1 — Feature: безопасное определение и проверка типа оплаты ЕРИП в МойКласс

**Причина:** Перед внесением оплаты в МойКласс нужно убедиться, что `MOYKLASS_ERIP_PAYMENT_TYPE_ID` указан верно, соответствует существующему, активному, неудалённому типу оплаты в МойКласс. Также — инструмент для owner/admin, чтобы найти правильный `paymentTypeId` без изменения `.env` вручную.

**Ключевые изменения:**

- **`moyklass_client.py`:** 2 новых метода: `get_payment_types()` (GET `/v1/company/paymentTypes`) и `get_payment_type_by_id(id)` (поиск по полному списку, т.к. OpenAPI не имеет per-ID endpoint).
- **`web_app_server.py`:** 3 helper-функции: `_is_erip_candidate(name)`, `_normalize_payment_type(raw)`, `_build_payment_type_readiness(id, pt)`. Новый метод `moyklass_payment_types()` — read-only, owner/admin. `payment_intent_moyklass_readiness` дополнен live-проверкой `paymentTypeId` через `get_payment_type_by_id`. Routing: GET `/api/payments/moyklass/payment-types`.
- **ERIP-кандидаты:** Поиск по ключевым словам (`ЕРИП`, `ERIP`, `BEPAID`, `БЕЗНАЛИЧНЫЙ`, `ОНЛАЙН-ОПЛАТА`, `ОНЛАЙН ОПЛАТА`). Отображаются все совпадения. Никогда не сохраняются автоматически. `env_hint` показывается при единственном кандидате.
- **UI** (`miniapp/`): Блок «Тип оплаты МойКласс (ЕРИП)» в разделе payment intents: статус настроенного типа, кандидаты ЕРИП, полный список, кнопка «Обновить список». Cache-bust: `v=7.0.92.1`.
- **`tests/test_mk_payment_types.py`:** 21 тест (роли, read-only, нормализация, ERIP-детекция, валидность ID, readiness, безопасность). Все OK.
- **Итого тестов:** 21 новых + 333 existing = **354/354 OK**

---

### v7.0.92 — Feature: ручное внесение подтверждённой оплаты bePaid в МойКласс

**Причина:** После того как bePaid подтверждал платёж (`paid`), оплата нигде не фиксировалась в МойКласс — это нужно было делать вручную через другой интерфейс. v7.0.92 добавляет безопасный ручной flow «Внести в МойКласс» для owner/admin из карточки платёжного намерения.

**Ключевые изменения:**

- **Новый статус intent:** `posted_to_moyklass` (конечный, после `paid`)
- **Новые поля в `payment_intents`:** `mk_posting_status`, `mk_posting_at`, `mk_posting_by`, `mk_posting_fingerprint`, `mk_posting_error`, `mk_posting_invoice_snapshot_json`
- **Новая audit-таблица:** `payment_mk_post_audit` (16 типов событий)
- **`config.py`:** `moyklass_erip_payment_type_id` (`MOYKLASS_ERIP_PAYMENT_TYPE_ID` env var)
- **`moyklass_client.py`:** 4 новых метода: `get_invoice_by_id`, `create_payment`, `search_payments_by_user_date`, `get_payment_by_id`
- **`storage.py`:** 6 новых методов: `payment_intent_claim_moyklass_post`, `payment_intent_release_moyklass_claim`, `payment_intent_mark_posted_to_moyklass`, `payment_intent_mark_moyklass_ambiguous`, `log_moyklass_post_audit`, `list_moyklass_post_audit`
- **`web_app_server.py`:** `PAYMENT_MK_POST_ROLES={"owner","admin"}`, `_compute_moyklass_post_fingerprint()`, 3 новых endpoint-метода: `payment_intent_moyklass_readiness`, `payment_intent_post_to_moyklass`, `payment_intent_reconcile_moyklass`. Routing: GET `moyklass-post-readiness`, POST `post-to-moyklass`, POST `reconcile-moyklass-payment`
- **UI** (`miniapp/`): MK post modal (`#piMkPostModal`), кнопка «Внести в МойКласс» в карточке `paid`, панели readiness/confirm/success, стили dark/light
- **`tests/test_mk_post_payment.py`:** 67 тестов (49 spec + 18 дополнительных). Все OK.
- **Безопасность:** atomic claim (`UPDATE WHERE mk_posting_status IS NULL AND status='paid'`), snapshot fingerprint (SHA256 из invoice price+payed+intent fields), idempotency guard, ambiguous state (timeout/5xx после отправки), reconciliation через поиск по comment+amount. `BEPAID_AUTO_POST_TO_MOYKLASS=false` не тронуто.
- **Итого тестов:** 67 новых + 266 existing = **333/333 OK**

---

### v7.0.91.1 — Security: hardening bePaid webhook validation

**Причина:** Security-аудит v7.0.91 выявил критические уязвимости в обработке webhook:

**Исправления (storage.py, web_app_server.py, tests/test_bepaid_webhook.py):**
- **CRITICAL**: Добавлена проверка суммы — webhook `amount_minor` сравнивается с `payment_intent.amount_minor`; несовпадение → `bepaid_requires_check` + аудит `webhook_amount_mismatch`
- **HIGH**: Исправлена проверка валюты: `if currency and currency != "BYN"` → пустая currency теперь не пропускается; пустая → skip без paid; ненулевая != BYN → `bepaid_requires_check` + `webhook_currency_mismatch`
- Нормализация currency в uppercase в `_bepaid_extract_payload` (byn → BYN)
- Новые audit-события: `webhook_signature_verified`, `webhook_test_ignored`, `webhook_currency_mismatch`, `webhook_amount_mismatch`, `refund_requires_check`
- Обработка refund/chargeback статусов → матч + `bepaid_requires_check` + `refund_requires_check` аудит
- `payment_intent_mark_requires_check` расширен: поддерживает `reason=` для webhook-контекста (обновляет `payment_state_reason`, `last_webhook_at`)
- Readiness endpoint: +2 обязательные проверки (`has_amount_minor`, `auto_post_to_moyklass_disabled`), +2 условные для `moyklass_invoice` источника (`has_mk_user_id`, `has_mk_invoice_id`)
- +30 тестов в `tests/test_bepaid_webhook.py` (18 security-сценариев + TestMarkRequiresCheck + расширение TestWebhookReadinessEndpoint)
- **Итого тестов: 266/266 OK**

---

### v7.0.91 — Feature: bePaid webhook processing and payment intent reconciliation

**Причина:** После создания платёжного намерения и отправки в bePaid — webhook принимался, транзакция сохранялась, но статус intent никогда не обновлялся до «paid». Не было audit-trail, нет защиты от дублей.

### v7.0.90.6 — Fix: список счетов МойКласс / highlight / контраст

**Причина:** После v7.0.90.5 проверено на реальном iPhone. Найдены три проблемы: (1) «Показать все неоплаченные» иногда падало с «Сервер вернул некорректный ответ» — python `json.dumps` без `allow_nan=False` мог сериализовать `NaN`/`Infinity` в тело ответа, что браузер отклонял; (2) жёлтая highlight-анимация не была видна — `animation:` на `.pi-card-highlight` перебивалась более специфичным правилом `.pi-list > .pi-card { animation: ycCardEnter }` (specificity 0,2,0 > 0,1,0); (3) финансовые значения в invoice card плохо читались — вся строка была бледно-серой (color: var(--muted) на контейнере), структура не давала явного контраста.

**Ключевые изменения:**
- `web_app_server.py`: `_send_json` добавлен `allow_nan=False` — `ValueError` при `NaN`/`Infinity` перехватывается, возвращается `{"ok": false, "stage": "json_encode"}` со статусом 500 вместо невалидного JSON.
- `miniapp/app.js`: `loadMkInvoices` — JSON parse error показывает stage=json_parse + диагностику (HTTP status, Content-Type, length, preview) для owner/admin/operations. Fetch error показывает stage=fetch + тип ошибки. Поддержка dual-key `data.invoices || data.items`. Проверка payload на null с `stage=payload_validation`. Per-card safe rendering — одна сломанная карточка показывает inline error, остальные рендерятся нормально.
- `miniapp/app.js`: `renderMkInvoiceCard` — новая структура финансовой секции с `.mk-invoice-finance__item`, `.mk-invoice-finance__label`, `.mk-invoice-finance__value`, `.mk-invoice-finance__value--remaining`.
- `miniapp/app.js`: `showPaymentIntent` — timeout увеличен до 2000ms для совместимости с новой 1900ms анимацией.
- `miniapp/styles.css`: Highlight переделан через `::after` pseudo-element (`piIntentHighlightRing` keyframe) — не конфликтует с `animation` на самой карточке. `.pi-card-highlight` только меняет `background-color`. `.pi-card` получил `position: relative`. `prefers-reduced-motion` показывает статическую жёлтую рамку.
- `miniapp/styles.css`: `.mk-invoice-finance` переделан на CSS grid (3 колонки). Явные цвета: label `#687083`, value `#172033` (light) / `#f4f7fb` (dark), remaining `#15996f` / `#5ad8a6`.
- `tests/test_mk_invoice_intent.py`: +20 тестов `TestV90906InvoiceListFixes`.

**Cache-bust:** v=7.0.90.6

---

### v7.0.90.5 — Fix: кнопка «Показать черновик» в карточке счёта МойКласс

**Причина:** При нажатии «Показать черновик» визуально ничего не происходило — `scrollToIntent` не открывала аккордеон, не устанавливала фильтры, использовала ненадёжный `setTimeout(300ms)` вместо ожидания рендера DOM.

**Ключевые изменения:**
- `miniapp/app.js`: Новый helper `paymentIntentDomId(publicId)` — единственное место генерации DOM id карточки (символ `-` вместо `_` в замене).
- `miniapp/app.js`: Новая async функция `showPaymentIntent(publicId, periodMonth)` — открывает аккордеон, устанавливает фильтры (month + status=all), awaits `loadPaymentIntents()`, double rAF, scroll + жёлтый highlight. При ненахождении карточки — toast с диагностикой.
- `miniapp/app.js`: `renderPaymentIntentCard` — использует `paymentIntentDomId`, добавлен атрибут `data-intent-public-id`.
- `miniapp/app.js`: `renderMkInvoiceCard` — кнопка переписана с `onclick="scrollToIntent(...)"` на event delegation (`data-action="show-payment-intent"`, `data-intent-public-id`, `data-period-month`). Label: "Показать черновик" (draft/ready) / "Открыть платёж" (bepaid_created/paid/...).
- `miniapp/app.js`: `openMkInvoiceCreate` — после создания вызывает `showPaymentIntent(publicId, "")` вместо `scrollToIntent`.
- `miniapp/app.js`: Удалена `scrollToIntent`. Event delegation на `document` для `[data-action='show-payment-intent']`.
- `miniapp/styles.css`: Анимация переименована в `piIntentHighlight` (жёлтый: `rgba(255,204,0,...)`). `.pi-card` получил `scroll-margin-top: 72px`.
- `web_app_server.py`: Поле `active_intent_period_month` добавлено в оба пути ответа (direct lookup + paginated scan).
- `tests/test_mk_invoice_intent.py`: +15 тестов `TestV90905ShowPaymentIntent`.

**Cache-bust:** v=7.0.90.5

---

### v7.0.90.4 — Hotfix: UI ошибка после создания черновика + информативные карточки счетов

**Причина:** После успешного создания payment_intent из счёта МойКласс frontend вызывал `piShowToast(...)` — функция не существует (есть `showToast`). Это вызывало `ReferenceError` в iOS/Safari, который выглядел как провал операции, хотя backend уже создал черновик. Дополнительно: карточки показывали только userId, без имени ученика и разбивки по суммам.

**Ключевые изменения:**
- `miniapp/app.js`: `piShowToast` → `showToast` (правильная функция). Toast call обёрнут в отдельный `try/catch` — ошибка уведомления не попадает в catch API-запроса. `alert()` заменены на `showToast()`.
- `miniapp/app.js`: `renderMkInvoiceCard` полностью переписан: имя ученика крупным заголовком, разбивка Выставлено/Оплачено/Остаток, статус, срок оплаты, абонемент №, userId вторичным текстом, badge «Черновик создан» с кнопкой «Показать черновик».
- `miniapp/app.js`: `openMkInvoiceCreate` — confirm показывает имя ученика; success state показывает `public_id`; scroll к созданному intent после загрузки.
- `miniapp/app.js`: `scrollToIntent(publicId)` — новая функция плавной прокрутки к карточке intent. `renderPaymentIntentCard` получает `id="payment-intent-{public_id}"` для точного позиционирования. Анимация highlight при прокрутке.
- `miniapp/app.js`: `loadMkInvoices(mode)` — три отдельных режима: `"byId"`, `"byUser"`, `""` (все неоплаченные). Три кнопки.
- `miniapp/index.html`: Три отдельных действия поиска с labels и кнопками «Найти счёт» / «Найти счета ученика» / «Показать все неоплаченные». Cache-bust `v=7.0.90.4`.
- `miniapp/styles.css`: `.mk-search-grid` / `.mk-search-row` / `.mk-search-label` — контрастные поля ввода на iOS (белый фон, тёмный текст, правильный placeholder). Новые стили `.mk-invoice-student-name`, `.mk-invoice-finance`, `.mk-invoice-intent-badge`, `.mk-invoice-bepaid-row`, `.pi-card-highlight` (pulse animation).
- `web_app_server.py`: `moyklass_invoices_list` теперь batch-resolves имена учеников через `_mk_student_names_by_ids()` — для всех returned карточек. Поле `student_name` добавлено к прямому lookup и paginated scan. В каждый invoice card добавлены `active_bepaid_uid` и `active_bepaid_account`. Diagnostics: `names_resolved_count` / `names_missing_count`.
- `tests/test_mk_invoice_intent.py`: +15 тестов (`TestV90904UIFix` × 11, `TestFindActiveIntentBePaidFields` × 4).

**Подтверждено в production:**
- Цикл: счёт МойКласс #19060579 → intent `ycpi_202607_9` → bePaid ERIP 974899826079 (UID 779fe891-...) — всё работает.
- После hotfix: toast показывается без ReferenceError, черновик не создаётся повторно.

**Что НЕ менялось:** расчёт remaining, создание payment_intent, создание bePaid, webhook, Food Module, Reports, роли, .env.

**Cache-bust:** v=7.0.90.4

---

### v7.0.90.3 — Hotfix: BrokenPipeError + кеш счетов МойКласс

**Причина:** При скане 83 страниц МойКласс (8278 счетов) iOS/Safari отключался (~60 с), backend получал `BrokenPipeError` при записи ответа, затем `do_GET` повторно вызывал `_send_json(500)` → второй `BrokenPipeError`. Фронтенд получал пустой/оборванный JSON → `SyntaxError`.

**Ключевые изменения:**
- `web_app_server.py`: `_send_json` теперь возвращает `bool`; перехватывает `BrokenPipeError`/`ConnectionResetError` внутри `end_headers`/`wfile.write`, логирует путь без query-параметров, возвращает `False`.
- `web_app_server.py`: `do_GET`/`do_POST` — добавлен `except (BrokenPipeError, ConnectionResetError): return` до общего `Exception`; fallback `_send_json(500)` обёрнут в `try/except`.
- `web_app_server.py`: `log_message` переопределён — URL в access-логах обрезается до `?`, `initData`/токены не попадают в journalctl.
- `web_app_server.py`: модульный 5-минутный кеш (`_mk_invoices_cache`, `_mk_invoices_scan_lock`, TTL 300 с) с паттерном single-flight; для глобальных запросов (без `userId`) избегает повторных 83 вызовов МК API. Ошибки не кешируются.
- `web_app_server.py`: прямой поиск по `invoiceId` (`GET /v1/company/invoices/{id}`) открыт для всех `PAYMENT_INTENT_ROLES` (не только admin). Поиск по `userId` обходит кеш, передаёт `userId` напрямую в МК API (1–2 страницы).
- `miniapp/app.js`: `loadMkInvoices` — `AbortController` 120 с для запроса счетов; поля `mkInvoiceSearchId` / `mkInvoiceSearchUserId`; раздельная обработка timeout/HTTP/JSON/render ошибок; `console.info` с диагностикой после получения ответа.
- `miniapp/index.html`: поля поиска по `№ счёта` и `userId МойКласс`; cache-bust `v=7.0.90.3`.
- `miniapp/styles.css`: `.mk-invoices-search-row` — flex-строка для полей поиска, light/dark.
- `tests/test_mk_invoice_intent.py`: +12 тестов (`TestBrokenPipeHandling` × 5, `TestMkInvoicesCache` × 7).

**Что НЕ менялось:** создание bePaid, webhook, Food Module, отчёты, роли, .env.

**Cache-bust:** v=7.0.90.3

---

### v7.0.90.2 — Hotfix: пагинация счетов МойКласс

**Причина:** `moyklass_invoices_list` делал один запрос с `limit=50` (из фронтенда) и получал только первую страницу. Все 50 счетов первой страницы оказались оплачены; неоплаченный счёт #19060579 находился на странице 2.

**Ключевые изменения:**
- `web_app_server.py`: добавлены `_MK_INVOICE_PAGE_LIMIT=100`, `_MK_INVOICE_MAX_PAGES=100`.
- `web_app_server.py`: `_mk_fetch_invoices_paginated(mk_client, base_params, page_limit, max_pages)` — постраничная загрузка через `offset`; читает `stats.totalItems`; останавливается при `total_reached` / `empty_page` / `partial_page` / `mk_error` / `max_pages`.
- `web_app_server.py`: `_mk_invoice_by_id(mk_client, invoice_id)` — прямой GET `/v1/company/invoices/{id}` без пагинации.
- `web_app_server.py`: `moyklass_invoices_list` переписан: `result_limit` (из frontend) отделён от `page_limit` МК; пагинация сканирует все страницы; результаты сортируются по `date desc, payUntil desc, id desc`; в `diagnostics`: `pages_loaded`, `total_items_reported`, `raw_invoices_scanned`, `stopped_reason`.
- `web_app_server.py`: `_preflight_mk_invoice` использует `_mk_invoice_by_id` с fallback на пагинацию.
- `web_app_server.py`: `payment_intent_from_mk_invoice` использует `_mk_invoice_by_id` с fallback на пагинацию (исправлен также старый баг `raw.get("items") or raw.get("invoices")`).
- `miniapp/app.js`: `loadMkInvoices` — состояние загрузки «Загрузка счетов МойКласс…»; диагностика показывает `pages_loaded`, `total_items_reported`, `raw_invoices_scanned`; пустые состояния корректно различают «всё просмотрено» vs «ещё не сканировалось».
- `tests/test_mk_invoice_intent.py`: 9 новых тестов `TestMkInvoicePagination` (A–I) с `_MockMKClient`; тест A подтверждает нахождение invoice #19060579 на странице 2.

**Что НЕ менялось:** создание bePaid, webhook, Food Module, отчёты, роли, .env.

**Cache-bust:** v=7.0.90.2

---

### v7.0.90.1 — Hotfix: парсинг ответа МойКласс invoices + диагностика

**Цель:** исправить загрузку счетов МойКласс — 0 счетов отображалось несмотря на HTTP 200.

**Ключевые изменения:**
- `web_app_server.py`: добавлен `_extract_mk_invoices()` — новый module-level helper, приоритет ключу `"invoices"` (официальный ключ OpenAPI `GET /v1/company/invoices`). Ранее extractor проверял `"items"` раньше `"invoices"` — потенциальный источник ошибок при любом изменении ответа МК.
- `web_app_server.py`: `moyklass_invoices_list` переведён на `_extract_mk_invoices`, добавлены счётчики диагностики (`raw_invoices_count`, `filtered_paid_count`, `filtered_invalid_count`, `missing_price_count`, `missing_user_id_count`), возвращает `diagnostics` для owner/admin/operations, безопасный log без персональных данных.
- `web_app_server.py`: `_preflight_mk_invoice` тоже переведён на `_extract_mk_invoices`.
- `web_app_server.py`: новый метод `_check_subscription_debt(mk_user_id)` — если для переданного `userId` нет счетов, проверяет долги по абонементам через `GET /v1/company/userSubscriptions`, возвращает `subscription_debt_warning` с суммой долга.
- `miniapp/app.js`: `loadMkInvoices` отображает diagnostics-блок (owner/admin), 3 варианта пустого состояния: «нет счетов», «есть долг по абонементу без счёта», «счета есть но все оплачены».
- `miniapp/styles.css`: `.mk-invoices-diag`, `.mk-diag-warn`, `.mk-empty-debt-warn`.
- `miniapp/index.html`: добавлен `<div id="mkInvoicesDiag">`.
- `tests/test_mk_invoice_intent.py`: 15 новых тестов — `TestExtractMkInvoices` (8 тестов) + `TestInvoiceFilterLogic` (7 тестов) с production-shaped payload `userId=9748998 price=229 payed=0`.

**Что НЕ менялось:** создание bePaid, webhook, Food Module, отчёты, роли, .env.

**Cache-bust:** v=7.0.90.1

---

### v7.0.90 — Feature: создание черновиков bePaid из счетов МойКласс

**Цель:** связать первый контролируемый платёж bePaid с реальным учеником, абонементом и счётом МойКласс.

**Backend:**
- `storage.py`: 9 новых колонок через `_ensure_column` в `payment_intents` — `mk_invoice_id`, `mk_user_subscription_id`, `source` (moyklass_invoice/manual), `source_reference`, `invoice_amount_minor`, `invoice_remaining_minor`, `invoice_snapshot_json`, `verified_mk_user_at`, `verified_invoice_at`; индекс `idx_pi_mk_invoice`
- `storage.py`: новый метод `find_active_intent_by_invoice(mk_invoice_id)` — ищет активный черновик (не отменённый/не ошибочный) по ID счёта
- `web_app_server.py`: `GET /api/payments/moyklass/invoices` — возвращает неоплаченные/частично оплаченные счета из МойКласс с нормализацией и проверкой дублей
- `web_app_server.py`: `POST /api/payments/intents/from-moyklass-invoice` — перепроверяет счёт в МойКласс, запрещает доверять суммам фронтенда, создаёт черновик с `source=moyklass_invoice`
- `web_app_server.py`: preflight `_preflight_mk_invoice()` перед выставлением счёта bePaid: проверяет существование счёта, суммы, отсутствие дублей
- Защита от дублей: один активный черновик на `mk_invoice_id`

**Frontend:**
- `miniapp/index.html`: блок «Счета МойКласс» с кнопкой «Загрузить счета» и списком карточек; предупреждение для ручной формы
- `miniapp/app.js`: `loadMkInvoices()`, `renderMkInvoiceCard()`, `openMkInvoiceCreate()` — двухшаговый flow (просмотр счёта → подтверждение → создание черновика)
- `miniapp/app.js`: source badge на карточках черновиков — «Данные проверены в МойКласс» / «Ручной ввод»
- `miniapp/styles.css`: стили `.mk-invoices-section`, `.mk-invoice-card`, `.pi-source-badge`, `.pi-manual-form-warning`

**Тесты:** 14 unit-тестов в `tests/test_mk_invoice_intent.py` — 8 чистых тестов расчёта остатка/статуса, 4 теста `find_active_intent_by_invoice`, 2 теста `create_payment_intent` с source-полями

**Что НЕ менялось:** Food Module, отчёты, существующий импорт bePaid, webhook, клиентские роли, .env, автоматическая отправка сообщений

**Cache-bust:** v=7.0.90

---

### v7.0.89 — Tune: увеличение длительности анимаций

**Цель:** сделать анимации более заметными на реальном iPhone в Telegram Mini App.

**Изменены motion tokens:**
- `--motion-fast`: 140ms → 180ms
- `--motion-normal`: 200ms → 260ms
- `--motion-slow`: 280ms → 340ms
- `--yc-fast`: 120ms → 160ms (синхронизирован с motion-fast, чуть меньше для press-feedback)
- `--yc-normal`: 180ms → 260ms
- `--yc-slow`: 240ms → 340ms

**Модальные окна (`.modal` + PI Modal):**
- Открытие overlay: 160ms → 240ms; backdrop PI: 180ms → 240ms
- Открытие bottom sheet: 220ms → 300ms; piSheetUp: 220ms → 300ms
- Закрытие overlay: 170ms → 200ms; backdrop PI: 215ms → 200ms
- Закрытие bottom sheet: 170ms → 240ms; piSheetDown: 215ms → 240ms
- Desktop scale modal: piScaleIn 160ms → 240ms; piScaleOut 150ms → 200ms

**Аккордеоны:** авто-обновились через `--motion-normal` (стрелка: 200ms → 260ms) и `--motion-fast` (контент: 140ms → 180ms)

**Вкладки:** `.tab-panel.active` теперь использует `--motion-normal (260ms)` и `--ease-enter` (было `--yc-fast = 120ms`)

**Карточки:** translateY 6px → 8px в `ycCardEnter`; stagger шаг 25ms → 30ms; max задержка 155ms → 200ms

**Toast:** авто-обновился через `--motion-normal` (transition 200ms → 260ms)

**Кнопки:** `transform: 140ms → 150ms; background/border: 160ms → 180ms` — остаются быстрыми

**Что НЕ менялось:** архитектура анимаций, модалки, month picker, навигация, бизнес-логика, API, backend, bePaid, food module, reports, webhook, storage, роли, production DB, .env

**Что НЕ реализовано:** анимация закрытия `<details>` аккордеона — браузер мгновенно скрывает контент при закрытии нативного `<details>`; требует JS-рефакторинга (отдельная задача)

**Cache-bust:** v=7.0.89

---

### v7.0.88 — Fix: белый month picker в тёмной модалке + единая система анимаций

**Баг: белый month picker в тёмной модалке:**
- **Причина:** `.pi-modal-body .yc-month-picker { background: #f6f7fb }` имеет специфичность 0,2,0 — выше, чем `@media (prefers-color-scheme: dark) { .yc-month-picker { background: #263047 } }` (0,1,0). Результат: светлый фон отображался в тёмной модалке.
- **Дополнительная причина:** `data-theme` никогда не устанавливался на `:root`, поэтому `:root[data-theme="dark"]` правила не срабатывали.
- **Исправление:** добавлены `@media (prefers-color-scheme: dark) .pi-modal-body .yc-month-picker { background: #263047 }` и `:root[data-theme="dark"] .pi-modal-body .yc-month-picker { ... }` с нужной специфичностью. В app.js добавлено `document.documentElement.setAttribute("data-theme", tg.colorScheme)` при инициализации и при `themeChanged`.

**Единая система анимаций (motion system v7.0.88):**
- `:root` tokens: `--motion-fast: 140ms`, `--motion-normal: 200ms`, `--motion-slow: 280ms`, `--ease-standard`, `--ease-enter`, `--ease-exit`, `--motion-distance-sm/md`
- Аккордеоны: стрелка вращается `90deg` с `transition`, контент появляется через `ycFadeIn`
- Toast: появление `opacity 0→1 + translateY(8px→0)`, классовый подход (`pi-toast-visible`), анимация через `requestAnimationFrame`
- Карточки PI: stagger `ycCardEnter` для первых 8 карточек (0–155ms delay)
- `prefers-reduced-motion`: расширено до глобального `*, *::before, *::after` с `animation-iteration-count: 1`
- Cache-bust: `v=7.0.88`

**Что НЕ менялось:** бизнес-логика, API, backend, bePaid, food module, reports, webhook, storage

---

### v7.0.87 — Fix: замена нативного month input на .yc-month-picker, устранение наложения фильтров

**Корневая причина v7.0.86 не решила проблемы:** iOS Safari рендерит нативный `input[type="month"]` с минимальной шириной ~220px, которая выходит за CSS layout box вне зависимости от `min-width:0`. CSS не может ограничить нативный control renderer — он рисует поверх CSS box.

**Решение — `.yc-month-picker` паттерн:**
- Обёртка `.yc-month-picker` определяет визуальный размер (участвует в layout)
- `.yc-month-picker__native` (`position:absolute; inset:0; opacity:0`) — невидимый нативный input, получает клики и открывает picker, но НЕ участвует в layout
- `.yc-month-picker__value` — видимый `<span>` отображает отформатированный месяц

**JS helpers:**
- `isValidMonthValue(v)` — regex `/^\d{4}-(0[1-9]|1[0-2])$/`
- `formatMonthLabel(value)` — `toLocaleDateString("ru-RU", {month:"long", year:"numeric"})`
- `syncMonthPicker(input)` — устанавливает значение по умолчанию + обновляет span
- `initMonthPicker(input, preferred)` — инициализирует + bindит change/input события (один раз, через `dataset.monthPickerBound`)

**HTML заменены:** `piMonthFilter`, `childrenReportMonth`, `reportsMonth`, `piPeriodMonth`, `bepaidMonth`

**Toolbar:** `.pi-toolbar label` → `.pi-toolbar-field` div; CSS Grid переписан на `v7.0.87` блок с `.pi-toolbar-field`, `.pi-toolbar-field-label`, `.pi-toolbar-field > select`, `.pi-toolbar .yc-month-picker`

- Cache-bust: `v=7.0.87`

**Что НЕ менялось:** API, backend, bePaid, food module, reports, webhook, storage, бизнес-логика

---

### v7.0.86 — Fix: адаптивность month input, стабильная сетка фильтров, инициализация месяца

**Баг 1 (month overflow):** `input[type="month"]` имеет нативную min-content ширину (~220px), которая в flex-контейнере без `min-width: 0` выходит за правую границу. Добавлены глобальное правило `input[type="month"] { min-width: 0; min-inline-size: 0; }` и те же свойства внутри `.pi-modal-body input` и `.reports-controls input`.

**Баг 2 (toolbar jumps):** `.pi-toolbar` был `display: flex; flex-wrap: wrap` без фиксированных размеров. Переписан на 2-строчный CSS Grid (`pi-toolbar-filters` + `pi-toolbar-actions`), добавлена переменная `--pi-control-h: 40px` для единой высоты полей и кнопок, `min-width: 90px` на кнопки.

**Баг 3 (childrenReportMonth empty):** `renderChildrenReport()` не инициализировала поле `#childrenReportMonth`. Добавлен вызов `ensureMonthInputValue($("childrenReportMonth"), state.childrenReportMonth)` в начале функции.

**Дополнительно:**
- `ensureMonthInputValue(input, preferred)` — единый helper вместо разрозненных реализаций
- `piMonthFilter` инициализируется при открытии аккордеона (до `loadPaymentIntents()`)
- `piPeriodMonth` использует `currentMonthValue()` вместо `toISOString()` (исправлен timezone bug)
- Cache-bust: `v=7.0.86`

**Что НЕ менялось:** API, backend, bePaid, food module, reports, webhook, storage

---

### v7.0.85 — Fix: позиционирование модальных окон на iPhone

**Причина бага:** модалки платёжных черновиков были вложены в section отчётов с CSS-анимациями. `position: fixed` в iOS Telegram WebApp фиксировалось к анимированному ancestor, а не к viewport → форма уходила за экран, overlay не покрывал экран полностью.

**Исправления:**
- Три модалки перенесены в `<div id="piModalRoot">` — прямой потомок `<body>` (DOM portal)
- `.pi-modal` = сам backdrop: `position: fixed; inset: 0; width: 100dvw; height: 100dvh; z-index: 10000; background: rgba(8,14,27,.62)`
- `.pi-modal-sheet` (переименован с `.pi-modal-box`): `flex-direction: column; overflow: hidden`; body единственная прокручиваемая часть
- `.pi-modal-overlay` удалён из HTML (больше не нужен)
- iOS scroll lock: `piLockPageScroll()` — `body.style.position="fixed"; body.style.top="-${scrollY}px"`; `piUnlockPageScroll()` — `window.scrollTo(0, piLockedScrollY)`
- `piOpenModalCount` — предотвращает преждевременный разлок при вложенных open/close
- Backdrop click: только если `e.target === modal` (не `.pi-modal-sheet`)
- `@media (prefers-reduced-motion: reduce)`, `env(safe-area-inset-bottom)` в footer
- Cache-bust: `v=7.0.85`

**Что НЕ менялось:** bePaid, payment_intents backend, storage schema, food module, reports, webhook, роли, production DB, .env

---

### v7.0.84 — Fix: фильтрация меню питания по ребёнку, смене и филиалу

**Причина бага:** `food_active_menus()` возвращал ALL published меню без фильтрации по ребёнку, дате и локации. Родитель видел меню от 01.07 для ребёнка из смены 13.07–17.07.

**Исправления:**
- Новая функция `_get_child_week_period(child)` в `storage.py`:
  - Приоритет A: парсит `(DD.MM-DD.MM)` из `group_name` / `mk_class_name` (напр. `"Yellow Summer Week 3 (13.07-17.07), YC1"`)
  - Приоритет B: вычисляет понедельник–пятницу недели по `camp_lesson_date`
  - Приоритет C: возвращает `(None, None, location_code)` — меню не показывается
- `food_active_menus()` теперь:
  - Для каждого меню вычисляет `eligibleChildIds` (список `mk_student_id`, которым оно подходит)
  - Меню подходит ребёнку: дата в диапазоне смены + location_code совпадает
  - YC1 ребёнок не видит YC2 меню (и наоборот)
  - Меню без `location_code` — показывается по дате + warning `missing_menu_location`
  - Ребёнок без известной локации — меню не показывается + warning `missing_child_location`
  - Возвращает детям поля `weekStart`, `weekEnd`, `locationCode` для отображения в UI
  - Диагностика: `publishedMenusTotal`, `eligibleMenusTotal`, `warnings[]`
- `_check_order_preconditions()` теперь проверяет `menu_not_for_child`:
  - Дата меню входит в неделю ребёнка
  - Локация меню совпадает с локацией ребёнка
  - Защита работает даже если frontend отправит старый `menu_id`
- Frontend `renderParentFoodMenu()`:
  - Фильтрует `menus` по `eligibleChildIds` для выбранного ребёнка
  - Показывает контекст ребёнка: имя + смена + филиал
  - При смене вкладки ребёнка — список меню меняется
  - «Для смены этого ребёнка меню ещё не опубликовано.» — когда нет подходящих меню
- Старые заказы (`food_orders`) не затрагиваются, не удаляются
- 15 новых тестов в `tests/test_food_child_week.py` (все проходят)
- Общий счёт тестов: 72 (все проходят)
- Cache-bust: `v=7.0.84`

**Что НЕ менялось:** bePaid, payment_intents, сверка, MoyKlass, отчёты, kitchen, teacher lunch, staff orders, food_orders в БД

---

### v7.0.83 — UI/UX: animated modals, bottom sheet, safe-area, toast

### v7.0.82.1 — Hotfix: формат order_id для bePaid
- **Причина:** bePaid возвращал HTTP 422 `order_id: ["should not begin with 0"]` при `pi_row_id` < 10
- **Старый формат:** `f"{pi_row_id:012d}"` → `"000000000008"` для `pi_row_id=8` — отклонено bePaid
- **Новый формат:** `f"1{pi_row_id:011d}"` → `"100000000008"` — ровно 12 цифр, первая всегда `1`
- **Счёт НЕ был создан** при 422 — atomic claim снимался через `payment_intent_release_claim`, статус возвращался в `draft`/`ready`
- **После деплоя:** повторное создание счёта из существующего черновика работает штатно
- `account_number` не менялся (формула `{mk_user_id}{YYMM}{pi_row_id}` корректна и уникальна)
- Frontend не менялся, cache-bust остался `v=7.0.82`
- Добавлены регрессионные тесты: `erip_order_id(8) == "100000000008"`, `not startswith("0")`, ValueError для `<=0` и `>99_999_999_999`
- Всего тестов: 47 (все проходят)

### v7.0.82 — Укрепление идемпотентности bePaid ERIP
- Исправлен endpoint: `POST https://api.bepaid.by/beyag/payments` (подтверждён по официальной документации)
- ERIP-данные теперь извлекаются из `transaction.erip` (не `transaction.payment_method`)
- `account_number` включает `pi_row_id`: `{mk_user_id}{YYMM}{pi_row_id}` — уникальность per-intent гарантирована
- Атомарный захват: `UPDATE ... WHERE status IN ('draft','ready') AND COALESCE(bepaid_uid,'')=''` + `rowcount==1`
- Новые статусы: `bepaid_creating` (промежуточный) и `bepaid_requires_check` (требует ручной проверки)
- Timeout / ConnectionError / HTTP 5xx / пустой UID → `bepaid_requires_check` (повторное создание заблокировано)
- Валидация ответа bePaid: UID, amount, currency, tracking_id, order_id — несовпадение → `bepaid_requires_check`
- Все 4 конфига обязательны до вызова bePaid: `BEPAID_PUBLIC_BASE_URL`, `BEPAID_WEBHOOK_PATH_SECRET`, `BEPAID_ERIP_SHOP_ID`, `BEPAID_ERIP_SECRET_KEY`
- `notification_url` всегда передаётся в запросе
- Новая колонка `bepaid_qr_code_raw TEXT`, `qr_code_raw` сохраняется из `transaction.erip.qr_code_raw`
- UI: кнопка скрыта при `bepaid_creating` / `bepaid_requires_check`; предупреждающий блок при `bepaid_requires_check`
- 42 unit-теста (нет реальных HTTP-запросов), в том числе storage-тесты с in-memory SQLite
- Cache-bust: `v=7.0.82`

### v7.0.81 — Выставление счёта bePaid ERIP из платёжного черновика
- Новый модуль `bepaid_client.py`: `BePaidClient`, `build_erip_description`, статик-хелперы (`erip_account_number`, `erip_order_id`, `build_erip_payload`)
- Endpoint `POST /api/payments/intents/{public_id}/create-bepaid` — создаёт ERIP-счёт в bePaid
- Идемпотентность: если `bepaid_uid` уже выставлен → возвращает существующие данные
- Таймаут → статус `requires_check` (не ретраит)
- Кнопка «Выставить счёт bePaid» в карточке черновика (только ERIP, только draft/ready, пока нет uid)
- Модальное подтверждение выставления счёта
- После создания карточка показывает номер счёта ERIP и bePaid UID
- Миграция БД: 4 новых колонки (`bepaid_account_number`, `bepaid_created_at`, `bepaid_error`, `bepaid_request_attempts`)
- Новые env: `BEPAID_PUBLIC_BASE_URL`, `BEPAID_REQUEST_TIMEOUT`
- 24 unit-теста (нет реальных HTTP-запросов)
- Cache-bust: `v=7.0.81`

### v7.0.80 — Исправление formatByn (v7.0.79 regression)
- Исправлен `ReferenceError: fmtByn` — переведён из локальной const в глобальную функцию

### v7.0.79 — Исправление фильтрации Payment Intents и роли
- Исправлен доступ к роли: `state.me?.role` вместо `state.role`
- Исправлен рендер списка и фильтров

### v7.0.78 — Исправление UI Payment Intents

### v7.0.77 — Фундамент Payment Intents
- Таблица `payment_intents` в SQLite
- Создание / просмотр / отмена черновиков через Mini App

### v6.3.0 — Улучшенный админ-контроль стажёров
- Раздел "Стажёры" полностью переделан: фильтры + раскрытые карточки
- Фильтры: Все / Ждут проверки / Ждут решения / Допущены / Проблемные
- Каждая карточка стажёра: имя, бейдж статуса, прогресс наблюдений, МК-привязка
- Раскрытие карточки: наблюдения, работа (с кнопками Принять/Отклонить), пробное (с ОС + Допустить/Не допустить)
- История работ через `<details>` если более одной версии
- Demo-заявки теперь включают `conducted` и `approved` статусы (не только `requested`)
- Уведомления в Telegram:
  - Стажёр загрузил работу → owner/methodist/operations
  - Стажёр отправил самооценку → owner/methodist/operations
  - Работа принята → стажёру
  - Работа отклонена с комментарием → стажёру
  - Допущен → стажёру
  - Не допущен с комментарием → стажёру
- Helpers: `_intern_notify_admins()`, `_intern_notify_user()`
- Тест-панель сохранена внутри раскрытой карточки

### v5.12.0 — Базовый UX стажёра
- Исправлен iOS zoom (`font-size: 16px` глобально, `user-scalable=no`)
- Упрощён таббар для роли `intern` (только Стажировка / Помощь / Чат)
- Карточки этапов с цветовой индикацией состояния
- Загрузка ближайших занятий для наблюдения (`/api/intern/upcoming-lessons`)
- Загрузка файла подготовительной работы (multipart/form-data → `/api/intern/work-upload`)
- Скачивание файла работы для admin (`/api/intern/work-download`)
- Скрытие таббара при открытой клавиатуре в формах стажёра

### v6.0.2 — Полная защита от zoom + тест-режим стажёра
- Глобальный запрет zoom: viewport meta, CSS `touch-action`, JS gesture events
- `POST /api/intern/test-reset` — сброс прогресса (только admin в тест-режиме)
- `POST /api/intern/test-set-stage` — установка любого этапа (только admin в тест-режиме)
- UI панель "Тест стажировки" видна только admin в тест-режиме (в кабинете и в adminPanel)
- Защита: реальная роль в ADMIN_ROLES + `WEB_APP_TEST_ROLES=true`

### v6.1.0 — Accordion UX стажёра
- Карточки этапов раскрываются inline (accordion), только одна открыта за раз
- Активный этап открывается автоматически
- Умные рекомендации занятий: топ-5 с пометками ("Ближайшее · Групповое")
- Кнопка "Открыть материал" через `tg.openLink()` (URL из env `INTERN_TRIAL_MATERIAL_URL`)
- Все формы внутри карточки этапа, без секций ниже
- Заблокированные этапы раскрываются с объяснением причины
- Compact стиль: `.intern-acc-*` классы

### v6.2.0 — ОС стажёра после пробного + compact styles (текущая)
- Самооценка стажёра после пробного занятия (5 полей: как прошло / плюсы / минусы / улучшения / комментарий)
- Хранится в `intern_demo_bookings.trainee_feedback_json` (TEXT, JSON)
- `POST /api/intern/demo-feedback` — только role=intern, только своя запись, блокируется после `passed`/`failed`
- Форма показывается в карточке "Запись на пробное" пока нет финального решения
- После сохранения: зелёный блок с `<details>` "Редактировать"
- Admin видит ОС в "Заявки на пробное" перед решением Допустить/Не допустить
- Глобальный `padding-bottom: 90px` на `.tab-panel.active` — контент не перекрывается табом
- Добавлены `.yc-badge-*`, `.yc-section-title`, `.yc-card` utility-классы
- Лёгкое уплотнение `.card`, `.card-title`, `.pill`

---

## Изменённые файлы (суммарно по всем версиям)

| Файл | Что изменено |
|------|-------------|
| `config.py` | + `intern_trial_material_url: str` |
| `storage.py` | + таблицы intern (obs/works/bookings), + `save_intern_demo_feedback`, + `feedback_submitted` в track |
| `web_app_server.py` | + все intern endpoints, + `internTrialMaterialUrl` в `me()`, + `intern_submit_demo_feedback` |
| `miniapp/app.js` | Полный рефакторинг intern раздела: accordion, feedback form, admin view |
| `miniapp/styles.css` | + intern accordion, + feedback styles, + compact utilities |
| `miniapp/index.html` | Кэш v6.2.0 |

---

## Что НЕ сделано / осталось

1. **`INTERN_TRIAL_MATERIAL_URL`** — в `.env` не установлен реальный URL Notion-материала. Сейчас кнопка показывает "Ссылка не настроена". Нужно найти правильный URL и прописать.

2. **Этап "Проверка работы" (step 4) в accordion** — визуально это просто "ждёт проверки методиста". Нет механизма, чтобы методист пометил работу как "conducted" (промежуточный статус). Демо-переход сразу `requested → passed/failed`.

3. **KPI — не тронуто** — намеренно, по требованию задачи.

4. **Compact style на остальных страницах** — добавлены только CSS utility-классы, HTML в JS не изменён. Чтобы применить `.yc-badge` и `.yc-card` к карточкам уроков/задач/окон, нужно обновить рендер-функции.

5. **Тест-панель в admin view** — панель "Тест стажировки" в adminPanel показывает inline-тест для каждого стажёра, но кнопки "Применить" используют устаревший `state.internSection = null` вместо `state.internOpenStep = null`. Нужно проверить, не сбивается ли accordion после test-set-stage из admin-панели.

6. **Уведомления в Telegram** — при отправке ОС стажёра, при загрузке работы и при решении методиста нет push-уведомлений боту. Это потенциальная доработка.

---

## Известные проблемы

- **`node --check`** не работает в текущей среде (node не установлен). JS проверяется только визуально.
- **`pending_demos` в adminPanel** фильтрует только `status="requested"`. Если booking перешёл в `conducted` (промежуточный), он пропадёт из очереди на рассмотрение.
- Если стажёр бронирует демо дважды (declined → повторная бронь), `latest_booking` берёт последнюю. ОС предыдущего бронирования теряется.

---

## Как начать следующую сессию

1. Прочитать этот файл
2. Прочитать актуальный `web_app_server.py` (intern endpoints) и `miniapp/app.js` (intern section, ~строки 2056–2650)
3. Для задач со стажёром — смотреть `storage.get_intern_track()` как точку входа
4. Если продолжать compact style на других страницах — читать рендер-функции `renderLessons`, `renderTasks`, `renderWindows` в `app.js`

---

## Переменные окружения (важные для стажёра)

```
INTERN_TRIAL_MATERIAL_URL=   # URL Notion-материала для подготовки (пусто = кнопка недоступна)
WEB_APP_TEST_ROLES=true      # включить тест-режим для admin
```
