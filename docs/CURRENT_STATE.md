# Yellow Club Agent — Current State

> Последнее обновление: 2026-07-16 (v7.0.93.2.3)
> Цель файла: позволить возобновить работу из любого нового чата без потери контекста.
> **Этот файл — только документация. Production-код не менять через этот файл.**

---

## 1. Project

**Репозиторий:** `https://github.com/iamkrents/Ai-agent-Yellow-Club` (ветка `main`)

**Серверный путь:** `/home/ycagent/yellow_club_agent/`  
**Пользователь на сервере:** `ycagent`

**Сервисы (systemd):**
- `yellow-bot.service` — Telegram-бот (`bot.py`), запускается как daemon, отвечает в личке и группах
- `yellow-miniapp.service` — Mini App сервер (`web_app_server.py`), слушает на `127.0.0.1:8088` (за nginx reverse proxy)
- nginx — TLS-терминация и проксирование на `localhost:8088`

**Основной workflow (единственный безопасный):**
```
Claude Code (локально) → редактирование кода → git commit → git push
    → SSH на сервер → git pull → py_compile проверка → restart сервисов
```

---

## 2. Current version

| Параметр | Значение |
|---|---|
| Последняя задеплоенная версия | **v7.0.81** (commit `db0f1e9`) — НЕ развёрнут, production-дата неизвестна |
| Последний коммит в `main` | **v7.0.93.2.3** — Fix parent ERIP payment display |
| Frontend cache-bust | **`v=7.0.93.2.3`** (app.js и styles.css) |
| `console.log` в app.js | `MiniApp version: v7.0.93.2.3` |

> Все версии начиная с v7.0.82 запушены, но **НЕ деплоились** на production-сервер. Деплой — только по команде владельца.

### v7.0.92.5.3 — Verify unmatched acquiring payments with bePaid

**Задача:** Безопасное восстановление успешной acquiring-оплаты через официальный bePaid checkout status query. Решает production transaction 156 (webhook_verified=0) без ручного SQL.

**Новый trust path — provider_verified:**
- `provider_verified=1` устанавливается `mark_bepaid_transaction_provider_verified()` после успешного GET checkout status query.
- `webhook_verified` НЕ меняется — это строго криптографический флаг RSA-подписи webhook.
- `list_unmatched` теперь показывает транзакции с `webhook_verified=1 OR provider_verified=1`.
- `bepaid_reconcile_stored_transaction` разрешает reconcile при `webhook_verified=1 OR provider_verified=1`.

**Новый endpoint:** `POST /api/payments/intents/{id}/verify-acquiring`
- Owner/admin only.
- `checkout_token` берётся ТОЛЬКО из DB (из acquiring option), никогда из frontend.
- Статус-запрос делается с ACQ credentials (никогда ERIP).
- Валидируются: `shop.id`, `finished`, `test=false`, `status`, `order.amount`, `order.currency`, `order.tracking_id`, `gateway_response.payment.uid`, `payment.status`, `payment.amount`, `payment.currency`.
- Любое несовпадение → блок, без изменения статусов.
- Timeout/5xx → `retry=True`, без изменения статусов.
- При успехе: acquiring option → paid, parent intent → paid (`paid_channel=acquiring`), ERIP sibling → superseded, transaction → linked. MoyKlass не вызывается.
- Повторный вызов → idempotent=True.

**UI:** Кнопка «Подтвердить оплату через bePaid» на карточке intent (owner/admin, когда есть acquiring option с checkout_token и статус не terminal).

**Исправления:**
- **`bepaid_client.py`**: `get_checkout_status(payment_token)`, `_get_checkout(url)`, `_parse_checkout_status_response(resp)`.
- **`storage.py`**: новые колонки `provider_verified`, `provider_verified_at`, `provider_verification_method` в `bepaid_transactions`; метод `mark_bepaid_transaction_provider_verified()`; `list_unmatched_bepaid_transactions` — `OR provider_verified=1`.
- **`web_app_server.py`**: метод `bepaid_verify_acquiring_payment()`; reconcile block обновлён на `not webhook_verified AND not provider_verified`; route `verify-acquiring`; поле `provider_verified` в unmatched list response.
- **`miniapp/app.js`**: версия v7.0.92.5.3; `verifyAcquiringPayment()`; кнопка «Подтвердить оплату через bePaid».
- **`miniapp/index.html`**: cache-bust → `v=7.0.92.5.3`.
- **`tests/test_provider_verify.py`**: новый файл, 24 теста.

**Production transaction 156:** Вызов `POST /api/payments/intents/ycpi_202607_14/verify-acquiring` безопасно восстановит оплату без ручного SQL и без повторной оплаты.

**Итого тестов: 593/593 OK (+24 новых).**

### v7.0.92.5.2 — Security: restore webhook_verified as cryptographic property

**Проблема (критическая, до production-деплоя):**
В v7.0.92.5.1 были сделаны небезопасные изменения: `webhook_verified=1` устанавливался немедленно после upsert через `bepaid_transaction_set_verified()`, не завися от криптографии; фильтр `AND webhook_verified=1` в `list_unmatched` был удалён; hard block в reconcile на `webhook_verified=0` был заменён warning-ом. Это позволяло неверифицированным транзакциям попасть в reconcile flow.

**Правильная архитектура (восстановлена в v7.0.92.5.2):**
1. `webhook_verified=1` — ТОЛЬКО криптографический результат проверки Content-Signature, устанавливается ДО matching.
2. `list_unmatched` ТРЕБУЕТ `webhook_verified=1` — без этого транзакция не видна в reconcile.
3. Reconcile ЖЁСТКО БЛОКИРУЕТСЯ при `webhook_verified!=1`, возвращает `{"ok": false, "reason": "webhook_not_verified"}`.
4. `bepaid_transaction_link_intent` — НЕ трогает `webhook_verified` (убрано из UPDATE).

**Исправления:**
- **`storage.py`**: переименован `bepaid_transaction_set_verified` → `mark_bepaid_transaction_signature_verified(tx_id, *, verified_at, verification_method)`. `bepaid_transaction_link_intent` — удалён параметр `verified` и поле `webhook_verified` из UPDATE (matching не должен трогать криптографический флаг). `list_unmatched_bepaid_transactions` — восстановлен `AND webhook_verified=1`, добавлен `AND transaction_uid IS NOT NULL`.
- **`web_app_server.py`**: `bepaid_handle_webhook` — `mark_bepaid_transaction_signature_verified()` вызывается сразу после upsert, если `sig_verified=True`, ПЕРЕД matching. `bepaid_reconcile_stored_transaction` — восстановлен hard block: при `webhook_verified=0` логирует `stored_transaction_reconcile_blocked`, возвращает `{"ok": False, "error": "reconcile_blocked", "reason": "webhook_not_verified"}`.
- **`miniapp/app.js`**: версия v7.0.92.5.2; reconcile показывает security-сообщение при `webhook_not_verified`.
- **`miniapp/index.html`**: cache-bust → `v=7.0.92.5.2`.
- **`tests/test_option_matching.py`**: test_23 восстановлен — `len=0` для `webhook_verified=0`.
- **`tests/test_unmatched_transactions.py`**: полностью переписан под strict security model (32 теста).
- **`tests/test_security_webhook.py`**: новый файл, 19 security тестов.

**Production transaction 156 (webhook_verified=0 в DB):**
- Content-Signature не хранится в DB → криптографическая пере-верификация невозможна.
- Безопасное восстановление: запросить у bePaid повторный webhook replay ИЛИ admin SQL:
  `UPDATE bepaid_transactions SET webhook_verified=1 WHERE id=156 AND transaction_uid='06006e9d-ed00-47a6-8863-07d754744424' AND status='successful' AND test=0;`
- После восстановления — transaction 156 появится в «Несопоставленные транзакции».

**Итого тестов: 569/569 OK (+51 новых от v7.0.92.5.1 + security tests).**

### v7.0.92.5.1 — Fix: unmatched transaction list invisible + reconcile blocked

**Проблема (production):**
- `GET /api/payments/bepaid/unmatched` возвращал HTTP 200 с пустым списком — transaction 156 не отображалась.
- Кнопка «Повторно сопоставить оплату» не появлялась. Reconcile не запускался.

**Точные причины:**
1. **`webhook_verified` не записывался при no_match**: `bepaid_transaction_link_intent` (единственное место записи `webhook_verified=1`) вызывался только при успешном совпадении. При `no_match` транзакция сохранялась с `webhook_verified=0`.
2. **Фильтр `AND webhook_verified=1`** в `list_unmatched_bepaid_transactions` исключал все no_match транзакции — в том числе production transaction 156.
3. **Reconcile endpoint блокировал** на `webhook_verified=0` жёстким return error.
4. **Ключ API `"transactions"`** вместо стандартного `"items"` (frontend читал `data.transactions`, API возвращал `"transactions"` — совпадали между собой, но нарушали стандарт API); frontend не читал `data.items`, не проверял `data.ok`, не использовал `Array.isArray`.

**Исправления:**
- **`storage.py`**: `bepaid_transaction_set_verified(tx_id)` — новый метод, записывает `webhook_verified=1`. `list_unmatched_bepaid_transactions` — удалён фильтр `AND webhook_verified=1`; теперь `status='successful' AND test=0 AND intent_public_id IS NULL/empty` — достаточно для eligibility. Комментарий о причине изменения.
- **`web_app_server.py`**: `bepaid_handle_webhook` — немедленно вызывает `bepaid_transaction_set_verified(tx_id)` сразу после upsert при `sig_verified=True`, до любого match/no_match пути. `bepaid_reconcile_stored_transaction` — убран hard block на `webhook_verified=0`, заменён предупреждением в лог (`log.warning`); security gate остаётся через `status='successful'` + `test=0`. `bepaid_list_unmatched_transactions` — ключ `"transactions"` → `"items"`; добавлены поля `signature_verified`, `channel`, `match_status`.
- **`miniapp/app.js`**: `loadUnmatchedTransactions` читает `data.items`, проверяет `data.ok`, использует `Array.isArray(data.items)`, читает `tx.signature_verified` вместо `tx.webhook_verified`; версия v7.0.92.5.1.
- **`miniapp/index.html`**: cache-bust → `v=7.0.92.5.1`.
- **`tests/test_unmatched_transactions.py`**: 31 новый тест.

**Итого тестов: 549/549 OK (+31 новых).**

**Для production transaction 156 после deploy:** открыть раздел платёжных черновиков → transaction 156 появится в секции «Несопоставленные транзакции bePaid» → нажать «Повторно сопоставить оплату» → `ycpi_202607_14` перейдёт в status=paid, paid_channel=acquiring, ERIP-опция→superseded.

### v7.0.92.5 — Fix: bePaid webhook→option matching + reconciliation

**Проблема (production, 2026-07-14):**
- Вебхук эквайринга для `ycpi_202607_14` (transaction id=156, tracking_id=`ycpi_202607_14_acq`) всегда возвращал `no_match`. Причина: `match_bepaid_transaction_to_intent` искал только в таблице `payment_intents.bepaid_tracking_id`, но значение `ycpi_202607_14_acq` хранится в `payment_intent_options.bepaid_tracking_id`. Intent оставался в `awaiting_payment`, опция не помечалась оплаченной, кнопка МойКласс не показывалась.
- `payment_intent_mark_paid_via_option` допускал только статус `bepaid_created`, хотя intent находился в `awaiting_payment`.

**Исправления:**
- **`storage.py`**: новый метод `match_bepaid_transaction_to_payment_target(transaction, channel)` — сначала ищет совпадение в `payment_intent_options` (channel-scoped), затем fallback на legacy `match_bepaid_transaction_to_intent`. Acquiring-вебхук никогда не совпадает с ERIP-опцией и наоборот. Добавлены `get_bepaid_transaction_by_id(tx_id)` и `list_unmatched_bepaid_transactions(limit)`. `payment_intent_mark_paid_via_option` теперь принимает `bepaid_created`, `awaiting_payment`, `partial_ready` как исходные статусы (и в UPDATE WHERE IN).
- **`web_app_server.py`**: `bepaid_handle_webhook` использует `match_bepaid_transaction_to_payment_target` вместо legacy; для `target_type=payment_option` вызывает `payment_intent_mark_paid_via_option`, для `legacy_intent` — `payment_intent_mark_paid`. Новые методы: `bepaid_list_unmatched_transactions` (`GET /api/payments/bepaid/unmatched`) и `bepaid_reconcile_stored_transaction` (`POST /api/payments/bepaid/transactions/{id}/reconcile`) — повторная обработка сохранённой транзакции без внешних API-вызовов.
- **`miniapp/app.js`**: версия v7.0.92.5; `bePaidPaidBlock` показывает `paid_channel` ("Оплачено банковской картой (эквайринг)" / "Оплачено через ЕРИП"); `loadUnmatchedTransactions()` / `reconcileTransaction(txId)` / секция «Несопоставленные транзакции bePaid» в UI.
- **`miniapp/index.html`**: cache-bust → `v=7.0.92.5`; div `unmatchedTxSection`.
- **`tests/test_option_matching.py`**: 31 новый тест (option matching, channel scoping, mark_paid from awaiting_payment/partial_ready/bepaid_created, sibling superseding, idempotency, conflict detection, unmatched list, legacy fallback, failed intent protection).

**Итого тестов: 518/518 OK (+31 новых).**

**Production safety:** нет внешних API-вызовов в reconcile; BEPAID_AUTO_POST_TO_MOYKLASS=false не тронут; production intents 13, 14 не тронуты кодом.

### v7.0.92.4 — Fix: bePaid webhook signature (Base64) + unified prepare-options

**Проблема:**
- Все входящие ERIP-вебхуки отклонялись с HTTP 401: `verify_failed: non-hexadecimal number found in fromhex()` — код ожидал hex, а bePaid присылает RSA-подпись в Base64.
- В UI не было единой кнопки для создания обоих способов оплаты (ERIP + эквайринг) за один клик.
- Статистика Payment Intents показывала дублирующиеся чипы «Ожидает оплаты» (из `bepaid_created` и `awaiting_payment`).

**Исправления:**
- **`web_app_server.py`**: `_bepaid_verify_signature` — `bytes.fromhex()` → `base64.b64decode(validate=True)`; поддержка PEM и Base64-DER публичных ключей; опциональный prefix `sha256=`/`sha1=` снимается; диагностика включает `error_class=` (не значение ключа). `_bypass_method_check: bool = False` в `payment_intent_create_bepaid`. Новый метод `payment_intent_prepare_options(auth, public_id)` — создаёт ERIP и acquiring за один вызов, проверяет invoice в МойКласс, идемпотентен. Маршрут `POST /api/payments/intents/{id}/prepare-options`. `payment_intents_list` — добавлено поле `payment_options` к каждому intent (channel, status, account_number, uid, payment_url, has_checkout).
- **`miniapp/app.js`**: кнопка «Подготовить способы оплаты» (было «Подготовить черновик bePaid»); `openMkInvoiceCreate` автоматически вызывает `prepare-options` после создания intent; `acqReadyBadge` в карточке; дублирующийся чип «Ожидает оплаты» убран (сумма `bepaid_created + awaiting_payment`).
- **`miniapp/index.html`**: cache-bust → `v=7.0.92.4`.
- **`tests/test_bepaid_signature.py`**: 14 тестов (RSA PKCS#1 v1.5 + SHA-256, Base64 декодинг, PEM/DER ключи, отклонение hex-подписи, отклонение изменённого тела).
- **`tests/test_payment_options_flow.py`**: 10 тестов (prepare-options flow, idempotency, ERIP/ACQ независимые сбои, статусные переходы, статика UI).

**Итого тестов: 487/487 OK (14 новых signature + 10 новых flow; 16 skipped по иным причинам).**

### v7.0.92.3 — Feature: real bePaid acquiring (hosted checkout)

**Новое:**
- `bepaid_client.py`: удалена `BEPAID_ACQ_ENDPOINT_UNCONFIRMED`; добавлена `BEPAID_CHECKOUT_ENDPOINT = "https://checkout.bepaid.by/ctp/api/checkouts"`; реализован `create_acquiring_checkout(*, amount_minor, currency, description, tracking_id, notification_url, return_url, customer=None, test=False)` с валидацией; `_post_checkout` с заголовком `X-API-Version: 2`; `_parse_checkout_response` для `{"checkout": {...}}`; `build_checkout_payload` static method
- `storage.py`: `checkout_token TEXT` в `payment_intent_options` (в схеме + _ensure_column); `update_option_checkout(option_id, *, checkout_token, payment_url)`; `payment_intent_update_status(public_id, new_status)`
- `web_app_server.py`: `payment_intent_create_acquiring_option(auth, public_id)` — idempotent, создаёт option + checkout, обновляет статус PI; GET `/payment-return` — статическая страница "Платёж обрабатывается"; маршрут `POST /api/payments/intents/{id}/create-acquiring`
- `miniapp/app.js`: версия v7.0.92.3; статусы `partial_ready` / `awaiting_payment`; кнопка "Открыть страницу оплаты картой" для acquiring-метода; `openAcquiringCheckout()` — idempotent, открывает URL через Telegram.WebApp.openLink
- `miniapp/index.html`: cache-bust v7.0.92.3
- `tests/test_bepaid_acquiring.py`: 37 тестов (все pass)

**Статусные переходы:**
- `draft`/`ready` + acquiring checkout → `partial_ready`
- `bepaid_created` (ERIP уже создан) + acquiring checkout → `awaiting_payment`
- Idempotency: repeat click возвращает existing `payment_url` без нового API-вызова

**Миграция:** только additive (`checkout_token TEXT` column via _ensure_column). Существующие DB не затронуты.

**Cache-bust:** `app.js?v=7.0.92.3`, `styles.css?v=7.0.92.3`.

### v7.0.92.2 — Feature: dual-channel payments (ERIP + acquiring)

**Новое:**
- `config.py`: добавлен `moyklass_acquiring_payment_type_id` (из `MOYKLASS_ACQUIRING_PAYMENT_TYPE_ID`, default 0)
- `storage.py`: новая таблица `payment_intent_options` (один ряд на канал: erip/acquiring); колонки `paid_channel` и `paid_option_id` в `payment_intents`; 8 новых методов (`create_payment_intent_option`, `get_options_for_intent`, `get_option_by_channel`, `get_option_by_provider_ref`, `mark_option_paid`, `mark_option_failed`, `mark_option_expired`, `supersede_sibling_options`, `payment_intent_mark_paid_via_option`)
- `bepaid_client.py`: (stub заменён в v7.0.92.3)
- `web_app_server.py`: `_ACQ_KEYWORDS` + `_is_acquiring_candidate`; `moyklass_payment_types()` возвращает обе привязки (erip + acquiring); `payment_intent_post_to_moyklass` выбирает `payment_type_id` по `paid_channel` (acquiring → `MOYKLASS_ACQUIRING_PAYMENT_TYPE_ID`, erip/None → `MOYKLASS_ERIP_PAYMENT_TYPE_ID`)
- `miniapp/app.js`: `_renderPaymentTypeBlock` (helper); `renderMkPaymentTypes` показывает оба канала; версия → v7.0.92.2
- `miniapp/styles.css`: стили для `.mk-pt-dual-channels`, `.mk-pt-channel-block`, `.mk-pt-badge-acq`
- `tests/test_dual_channel.py`: 50 тестов (все pass)
- ~~**BLOCKER**~~: resolved in v7.0.92.3 — acquiring endpoint confirmed and implemented

**Миграции (additive):** `paid_channel TEXT`, `paid_option_id INTEGER` в `payment_intents`; CREATE TABLE IF NOT EXISTS `payment_intent_options`. Legacy intents не затронуты.

**Cache-bust:** `app.js?v=7.0.92.2`, `styles.css?v=7.0.92.2`.

### v7.0.92.1.2 — Hotfix: ReferenceError escHtml в renderMkPaymentTypes

**Баг:** `renderMkPaymentTypes()` использовала несуществующий alias `escHtml()` — 6 вызовов. После того как v7.0.92.1.1 исправил загрузку (`apiGet` работает, HTTP 200 приходит), рендеринг падал с `ReferenceError: Can't find variable: escHtml`. UI оставался пустым.

**Исправление:** 6 вызовов `escHtml(...)` → `escapeHtml(...)` (проектный helper, строка 240 app.js). Другие undefined aliases отсутствуют. Cache-bust: `app.js?v=7.0.92.1.2`.

### v7.0.92.1.1 — Hotfix: ReferenceError в loadMkPaymentTypes

**Баг:** `loadMkPaymentTypes()` вызывала несуществующую `apiFetch()` → `ReferenceError` синхронно, до отправки запроса. Функция не была `async`, поэтому `.catch()` не перехватывал синхронную ошибку. Результат: UI навсегда «Загрузка…», backend не видел ни одного запроса.

**Исправление:** `apiFetch` → `apiGet` (существует на строке ~568, возвращает разобранный JSON). Функция переписана как `async function` с `try/catch/finally`. Убран лишний `.then(r => r.json())`. Кнопка «Обновить» блокируется во время запроса (защита от двойного клика), `finally` всегда разблокирует.

**Cache-bust:** `app.js?v=7.0.92.1.1`. CSS не изменялась — `styles.css?v=7.0.92.1`.

### v7.0.92.1 — Feature: определение типа оплаты ЕРИП в МойКласс

**Причина:** Owner/admin должны уметь проверить, что `MOYKLASS_ERIP_PAYMENT_TYPE_ID` указан правильно, и найти правильный ID без изменения `.env` вручную.

**Новый endpoint:** GET `/api/payments/moyklass/payment-types` (owner/admin только). Возвращает: полный список типов оплаты (нормализованный), ERIP-кандидаты (по ключевым словам), статус настроенного ID, `env_hint` при единственном кандидате.

**ERIP-кандидаты:** Поиск по ключевым словам `ЕРИП/ERIP/BEPAID/БЕЗНАЛИЧНЫЙ/ОНЛАЙН-ОПЛАТА`. Никогда не сохраняются автоматически — admin копирует `env_hint` вручную.

**Readiness:** `payment_intent_moyklass_readiness` теперь делает live-проверку `paymentTypeId` через `get_payment_type_by_id` перед проверкой счёта.

**UI:** Блок «Тип оплаты МойКласс» в разделе payment intents. Cache-bust: `v=7.0.92.1`.

**`BEPAID_AUTO_POST_TO_MOYKLASS` остался `false` — не трогать.**

### v7.0.92 — Feature: ручное внесение bePaid оплаты в МойКласс

**Новый flow:** После bePaid webhook (`paid`) owner/admin видит кнопку «Внести в МойКласс» в карточке intent. Нажатие открывает модал с live pre-flight проверкой (GET readiness), показывает preview, ждёт подтверждения, затем POST в МойКласс.

**Защиты:** atomic claim (только один POST одновременно), snapshot fingerprint (блокирует если счёт изменился между preview и confirm), idempotency (второй POST возвращает результат первого), ambiguous state (timeout/5xx после отправки → блокирует авто-retry), reconciliation.

**Новые env vars:** `MOYKLASS_ERIP_PAYMENT_TYPE_ID` (обязателен для posting).

**`BEPAID_AUTO_POST_TO_MOYKLASS` остался `false` — не трогать.**

### v7.0.86 — Fix: month input overflow, toolbar grid, childrenReportMonth init

**Баг 1 (поле периода выходит вправо):** Причина — `input[type="month"]` имеет нативную min-content ширину (~220px на iOS), которая в flex-контейнере без `min-width: 0` переполняет родителя. Исправление: глобальное `input[type="month"] { min-width: 0; min-inline-size: 0; max-width: 100%; }` + те же свойства в `.pi-modal-body input` и `.reports-controls input`. Pseudo-element `::webkit-date-and-time-value { display: flex }` убран (нестандартный).

**Баг 2 (фильтры прыгают):** `.pi-toolbar` был `flex; flex-wrap: wrap` — элементы меняли размеры при изменении текста и данных. Переписан на CSS Grid: `pi-toolbar-filters` (2 колонки `minmax(0,1fr)`) + `pi-toolbar-actions` (2 колонки `auto / 1fr`). Переменная `--pi-control-h: 40px` для единой высоты. `.pi-toolbar button { min-width: 90px; white-space: nowrap; }` — кнопки не прыгают.

**Баг 3 (childrenReportMonth пусто):** `renderChildrenReport()` не инициализировала `#childrenReportMonth`. Добавлен `ensureMonthInputValue($("childrenReportMonth"), state.childrenReportMonth)` в начало функции. Поле показывает текущий месяц при первом открытии раздела.

**Дополнительно:** `ensureMonthInputValue` — единый helper; `piMonthFilter` инициализируется при открытии аккордеона; `piPeriodMonth` использует `currentMonthValue()` (timezone-safe).

### v7.0.85 — Fix: модальные окна в правильном месте viewport на iPhone

**Причина бага:** три модалки (`piCreateModal`, `piCancelModal`, `piBePaidModal`) были вложены в section отчётов, которая создаёт новый stacking context (CSS-анимации `ycFadeIn` и `overflow`). `position: fixed` на iOS Telegram WebApp позиционировалось относительно анимированного/scroll-контейнера, а не реального viewport → форма появлялась ниже экрана, боковые полосы на overlay.

**Исправление:**
- DOM portal: `<div id="piModalRoot">` — прямой потомок `<body>` (перед `</body>`) — все 3 модалки перенесены туда
- `.pi-modal` — сам является тёмным backdrop (`position: fixed; inset: 0; width: 100dvw; height: 100dvh; z-index: 10000; background: rgba(8,14,27,.62)`)
- `.pi-modal-sheet` (переименован с `.pi-modal-box`) — белая панель внутри; header `flex:0 0 auto`, body `flex:1 1 auto; min-height:0; overflow-y:auto`, footer `flex:0 0 auto`
- `.pi-modal-overlay` удалён из HTML (backdrop = сам `.pi-modal`)
- iOS scroll lock: `piLockPageScroll()` / `piUnlockPageScroll()` — `body.style.position="fixed"; body.style.top="-${scrollY}px"` с восстановлением позиции
- `piOpenModalCount` — счётчик защищает от преждевременного разлока при вложенных вызовах
- `piModalOpen` / `piModalClose` — перезаписаны; `piModalOpen` перемещает элемент в `#piModalRoot` при необходимости
- Backdrop click: `.addEventListener("click", e => { if(e.target === el) close() })` — не реагирует на клик по sheet
- `@media (prefers-reduced-motion: reduce)` — анимации 1ms
- `env(safe-area-inset-bottom)` в footer — home indicator на iPhone
- z-index: toast 9999, modal 10000
- Cache-bust: `v=7.0.85`
- Бизнес-логика, backend, bePaid, food module, reports — не изменены

### v7.0.84 — Фильтрация меню питания по ребёнку, смене, филиалу

**Причина бага:** `food_active_menus()` возвращал ВСЕ published меню без фильтрации по дате и локации. Родитель Фоменко Владислав (смена 13.07–17.07, YC1) видел меню от 01.07.

**Исправление:**
- `_get_child_week_period(child)` — новая функция в `storage.py`, парсит `(DD.MM-DD.MM)` из `group_name`
- `food_active_menus()` — меню фильтруются по `child_week_start..child_week_end` и `location_code`
- `_check_order_preconditions()` — новая проверка `menu_not_for_child` на бэкенде
- Frontend: `eligibleChildIds`, контекст смены/филиала ребёнка, правильный "no menus" текст
- Старые заказы НЕ удаляются

### Известный production-инцидент (v7.0.82.1 hotfix)
- **Симптом:** bePaid HTTP 422 `order_id: ["should not begin with 0"]` при создании ERIP-счёта
- **Причина:** `erip_order_id(pi_row_id)` возвращал `f"{pi_row_id:012d}"` → `"000000000008"` для малых id
- **Исправление:** новый формат `f"1{pi_row_id:011d}"` → `"100000000008"` (12 цифр, первая = 1)
- **Счёт при 422 НЕ создавался** — atomic claim снимался, статус черновика возвращался в `draft`/`ready`
- **После деплоя:** повторное нажатие «Выставить счёт bePaid» на том же черновике работает штатно
- `account_number` (`{mk_user_id}{YYMM}{pi_row_id}`) не изменялся — формула корректна

---

## 3. Important rules

### Что НЕЛЬЗЯ делать Claude Code:
- НЕ запускать `bot.py`
- НЕ запускать `web_app_server.py`
- НЕ запускать Mini App
- НЕ изменять `.env`
- НЕ читать и НЕ выводить значения токенов и секретных ключей
- НЕ изменять или копировать `storage/messages.db`
- НЕ запускать reset-скрипты
- НЕ очищать или пересоздавать базу
- НЕ подключаться к production-серверу
- НЕ делать deploy
- НЕ делать force push в `main`
- НЕ создавать bePaid payment request автоматически (только ручной trigger из UI)
- НЕ создавать payment в МойКласс автоматически (`BEPAID_AUTO_POST_TO_MOYKLASS=false` — не менять)
- Не логировать Secret Key или Authorization header в логах
- Не показывать Secret Key в UI
- Не логировать полный Shop ID (только last4 и length)

### Что можно:
- Редактировать код, тесты, документацию
- Запускать `python -m py_compile` и `python -m unittest`
- Делать `git commit` и `git push` в `main`

### Обязательно перед деплоем:
```bash
cp storage/messages.db backups/messages_$(date +%Y%m%d_%H%M%S).db
```

### User-facing wording (обязательно соблюдать):
- «городская программа» — НЕ «лагерь» (в UI для родителей)
- «смена» — НЕ «лагерь» (см. исправления в v6.9.4)

### Коммит не должен включать:
- `.claude/settings.local.json`
- `.env`
- `storage/messages.db`
- `backups/`
- `logs/`
- `.venv/`

---

## 4. Food Module state

**Статус:** Backend готов, UI включён за флагом `FOOD_MODULE_ENABLED`.

### Что работает:
- 6 SQLite-таблиц: `camp_children`, `parent_child_links`, `food_menus`, `food_items`, `food_orders`, `food_order_items`
- Диагностика детей лагеря: `POST /api/food/debug/sync-camp-children`
- Admin CRUD меню, публикация, блюда по категориям
- Родительский интерфейс: привязка ребёнка по коду `YC-XXXX`, просмотр меню, выбор питания
- Напоминания родителям: `POST /api/food/menus/{id}/remind-missing` (cooldown 2 ч)
- Уведомления о публикации меню (один раз на меню per parent)
- Авто-напоминания: `FOOD_AUTO_REMINDERS_ENABLED` (default `false`)
- OCR меню по фото: `FOOD_MENU_OCR_ENABLED` (default `false`, требует Tesseract+rus)
- Сводка заказов с разбивкой по локациям (YC1/YC2/YC3) и копируемый текст
- Отчёт по стоимости питания за смену: `GET /api/food/reports/shift`

### Роли:
- `admin` / `owner` / `operations` / `methodist` — полный доступ к питанию
- `parent` — видит меню и делает заказ через роль, выданную при FOOD_MODULE_ENABLED
- `intern` / `teacher` — питание не видят

### Что важно не сломать:
- Дедупликация детей по `mk_student_id` (upsert)
- Авто-определение активной недели: `CAMP_ACTIVE_WEEK_MODE=auto`
- Фильтр занятий: `CAMP_LESSON_NAME_FILTER=Yellow Summer Week` — НЕ добавлять YC1/YC2 (ложные срабатывания)
- `1 блюдо на категорию` — логика дедупликации в submit_order
- Сводка группируется по `group_name`/`mk_class_name` для определения локации

---

## 5. Reports state

### Monthly children report
- `GET /api/reports/children/monthly` — список детей по занятиям за месяц
- Используется для учёта посещаемости

### Revenue report
- `GET /api/reports/revenue` — ежемесячный отчёт с оборотом
- Включает bePaid-транзакции, МойКласс-платежи, сводку по филиалам
- Функция: workoff включается в расчёт (с v7.0.66)

### bePaid reconciliation (сверка)
- `GET /api/bepaid/transactions` — история импортированных транзакций
- Импорт через Reports API v2 (day-by-day): `POST /api/bepaid/import`
- Статусы сверки: `already_in_moyklass`, `found_in_subscription`, `possible_subscription_match`, `historical_subscription_match`, `user_found_no_payment_or_subscription`, `possible_payment_match`, `needs_review`, `ignored_not_successful`, `ignored_test`
- `BEPAID_AUTO_POST_TO_MOYKLASS=false` — автопостинг **выключен**, ручной режим
- Таблица `bepaid_transactions`: `transaction_uid`, `order_id`, `tracking_id`, `mk_user_id`, `match_status`, `mk_payment_id`

### Payment intents
- Таблица `payment_intents` — ручные черновики счетов
- Создание/просмотр/отмена через Mini App (Admin)
- Доступ: `owner`, `admin`, `director`, `operations`, `client_manager`
- Статусы: `draft`, `ready`, `bepaid_creating`, `bepaid_created`, `bepaid_requires_check`, `paid`, `posted_to_moyklass`, `cancelled`, `error`
- Метод оплаты: `erip` (ЕРИП) — поддерживается. `acquiring` — UI есть, интеграция не реализована.

---

## 6. bePaid / MoyKlass payments state

### Что реализовано:
| Функция | Статус |
|---|---|
| Импорт истории bePaid (Reports API v2, day-by-day) | ✅ Работает |
| bePaid webhook (приём, верификация RSA, сохранение) | ✅ Работает |
| Сверка bePaid ↔ МойКласс payments (userId + сумма) | ✅ Работает |
| Сверка bePaid ↔ МойКласс userSubscriptions (confidence) | ✅ Работает (v7.0.76) |
| UI сверки (mobile cards, статусы, chips) | ✅ Работает |
| `bepaid_transactions` в SQLite | ✅ Работает |
| Создание ERIP-счёта bePaid из payment_intent | ✅ Работает (v7.0.82) |
| Атомарный claim `bepaid_creating` (race-condition guard) | ✅ Работает (v7.0.82) |
| `bepaid_requires_check` при timeout/5xx/missing UID | ✅ Работает (v7.0.82) |
| Валидация ответа bePaid (UID, amount, currency, tracking_id) | ✅ Работает (v7.0.82) |
| Сохранение `bepaid_qr_code_raw` (Base64, из `transaction.erip`) | ✅ Работает (v7.0.82) |
| Полный ручной цикл: МК invoice → intent → bePaid ERIP | ✅ **Подтверждено в production** (v7.0.90.4) |
| Автоматическое создание payment в МойКласс после webhook | ❌ Не реализовано (`BEPAID_AUTO_POST_TO_MOYKLASS=false`) |
| Billing Profile / Auto Renewal / цикл абонементов | ❌ Не реализовано |

### Production verification (2026-07-13):
- Ученик: Креньть Александр Александрович, mk_user_id=9748998
- Счёт МойКласс: #19060579, сумма 229 BYN, абонемент #17998775
- Payment intent: `ycpi_202607_9` успешно создан
- bePaid ERIP: счёт 974899826079, UID `779fe891-1be7-4318-8490-9748428b2999`

### Ключевые технические детали (bePaid):
- **Endpoint:** `POST https://api.bepaid.by/beyag/payments`
- **Auth:** HTTP Basic (`BEPAID_ERIP_SHOP_ID` : `BEPAID_ERIP_SECRET_KEY`)
- **account_number:** `{mk_user_id}{YYMM}{pi_row_id}`, max 30 символов, уникален на intent
- **Внимание:** новый запрос с тем же `account_number` аннулирует предыдущий счёт в bePaid
- **ERIP-данные в ответе:** `transaction.erip.account_number`, `transaction.erip.qr_code_raw` (НЕ `transaction.payment_method`)
- **notification_url** всегда обязателен: `{BEPAID_PUBLIC_BASE_URL}/api/integrations/bepaid/webhook/erip/{BEPAID_WEBHOOK_PATH_SECRET}`

### Текущая цель: переход к автоматическому циклу
Сейчас `payment_intents` — ручные черновики. Цель — автоматический цикл (см. раздел 7).

---

## 7. Current business goal

**Главная цель:** автоматический цикл оплат по абонементам:

```
Клиент записывается
    → Менеджер (или агент) создаёт payment_intent
    → Агент выставляет счёт bePaid (ERIP или эквайринг)
    → Клиент оплачивает
    → bePaid присылает webhook
    → Агент автоматически создаёт payment в МойКласс
    → Агент привязывает payment к userSubscription (абонемент)
    → Занятия списываются с абонемента
    → Агент отслеживает остаток занятий
    → При исчерпании абонемента → авто-продление → новый счёт
```

**Текущий уровень автоматизации:** ручное выставление счёта bePaid через Mini App (кнопка «Выставить счёт bePaid»). После оплаты — ручная сверка, автосоздания payment в МК нет.

---

## 8. Current problem / next direction

### Проблема:
`payment_intents` работают как ручные черновики. Между оплатой bePaid и созданием payment в МойКласс — ручной шаг.

### Целевая архитектура (задокументирована в `docs/payment_automation_research.md`):
```
Billing Profile
    → Auto Renewal (триггер: за N дней до окончания абонемента)
    → Payment Intent (автоматически или по кнопке менеджера)
    → bePaid Request (счёт ERIP/эквайринг)
    → Webhook Paid (верификация, сохранение)
    → MoyKlass Payment (автосоздание payment + привязка к subscription)
    → Subscription updated (занятия активны)
    → Следующий цикл при исчерпании
```

### Что нужно реализовать (по порядку важности):
1. **Webhook → MoyKlass**: после `status=successful` в webhook автоматически создавать `POST /v1/company/payments` в МойКласс с дублем-защитой по `transaction_uid`.
2. **Billing Profile**: таблица с userId, filialId, subscription template, period, amount — профиль для автовыставления.
3. **Auto Renewal**: scheduled job, проверяет истекающие абонементы и создаёт payment_intent заранее.
4. **Полный цикл**: объединение всего выше в единый автоматический поток.

---

## 9. Recent commits

```
0d118e9  fix(payments): harden bePaid invoice idempotency (v7.0.82)
db0f1e9  Add bePaid ERIP invoice creation from payment intent (v7.0.81)
8c7364f  Fix payment intents amount formatter (v7.0.80)
3b2b5fe  Fix payment intents rendering and filters (v7.0.79)
8c0858a  Fix payment intents list and UI (v7.0.78)
3486ef8  Add payment intents foundation (v7.0.77)
fef5ce4  Document payment automation architecture (payment-automation-research)
62b7485  Add confidence to bePaid subscription matching
17d0d4c  Add confidence to bePaid subscription matching (v7.0.76)
277f790  Match bePaid payments with MoyKlass subscriptions (v7.0.75)
b7a796e  Improve bePaid MoyKlass reconciliation details (v7.0.74)
4661b59  Use bePaid reports API v2 format (v7.0.73)
eb71694  Add bePaid history import for reconciliation (v7.0.69)
856aba5  Add bePaid payment reconciliation foundation (v7.0.67)
```

---

## 10. Standard deploy commands

```bash
# 1. Подключиться к серверу
ssh <user>@<server-ip>

# 2. Переключиться на ycagent
su - ycagent

# 3. Перейти в проект
cd /home/ycagent/yellow_club_agent

# 4. Сделать backup базы (ОБЯЗАТЕЛЬНО)
cp storage/messages.db backups/messages_$(date +%Y%m%d_%H%M%S).db

# 5. Забрать изменения
git pull origin main

# 6. Проверить cache-bust версию
grep -r "v=7\." miniapp/index.html

# 7. Синтаксическая проверка Python
python -m py_compile config.py storage.py web_app_server.py intern_track.py bepaid_client.py

# 8. Перезапустить сервисы
sudo systemctl restart yellow-miniapp
sudo systemctl restart yellow-bot

# 9. Проверить статус
sudo systemctl status yellow-miniapp --no-pager
sudo systemctl status yellow-bot --no-pager

# 10. Посмотреть логи
sudo journalctl -u yellow-miniapp -n 50 --no-pager
sudo journalctl -u yellow-bot -n 50 --no-pager
```

---

## 11. Next recommended task

**Создать файл:** `docs/billing_cycle_automation_plan.md`

**Содержание:** план автоматического цикла оплат по абонементам.

**Что должен включать план:**
1. Схема: payment_intent → bePaid → webhook → МойКласс payment → subscription
2. Таблица `billing_profiles`: поля, индексы, связь с mk_user_id и userSubscriptionId
3. Webhook handler: алгоритм поиска payment_intent по tracking_id/order_id + создание MK payment
4. Дублезащита: по `transaction_uid` (уникальный индекс в payment_intents или bepaid_transactions)
5. Auto Renewal job: periodic check на истекающие абонементы, создание payment_intent
6. Error handling: что делать если МойКласс недоступен, subscription не найден
7. Rollout plan: этапы от ручного к полному авто

**Правила при исследовании и реализации:**
- Не включать `BEPAID_AUTO_POST_TO_MOYKLASS=true`
- Не создавать payment в МойКласс автоматически (пока не согласована схема)
- Не создавать bePaid request (только по ручному trigger из UI)
- Не менять `.env`
- Не трогать `storage/messages.db`
- Читать `docs/payment_automation_research.md` как базу — там подробный анализ API

---

## Appendix: Key files

| Файл | Назначение |
|---|---|
| `bot.py` | Telegram-бот (запрещено запускать локально) |
| `web_app_server.py` | Mini App HTTP-сервер, все API endpoints |
| `storage.py` | SQLite: все таблицы, миграции, запросы |
| `config.py` | Конфиг из env-переменных |
| `bepaid_client.py` | bePaid API client (ERIP invoice, response parsing) |
| `moyklass_client.py` | МойКласс API client |
| `intern_track.py` | Логика маршрута стажёра |
| `food_menu_ocr.py` | OCR меню по фото (pytesseract) |
| `miniapp/index.html` | Mini App HTML (cache-bust версия здесь) |
| `miniapp/app.js` | Mini App JS (весь frontend) |
| `miniapp/styles.css` | Mini App CSS |
| `storage/messages.db` | Production база данных (НЕ коммитить) |
| `docs/payment_automation_research.md` | Детальный анализ bePaid + МойКласс API |
| `docs/CURRENT_STATE.md` | Этот файл — handoff документ |
| `PROJECT_STATUS.md` | История версий и changelog |
| `RELEASE_CHECKLIST.md` | Чеклисты деплоя и тестирования |
| `FOOD_MODULE_PLAN.md` | Детальный план модуля питания |
