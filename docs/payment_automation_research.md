# Архитектура автоматизации оплат bePaid ↔ МойКласс

> **Версия:** payment-automation-research  
> **Дата:** 2026-07-11  
> **Статус:** Исследование и проектирование. Код не изменён. Auto-post не включён.

---

## Источники

| Источник | Статус |
|----------|--------|
| `docs/reference/moyklass_openapi.json` (v1.1.8) | Прочитан полностью |
| `https://docs.bepaid.by/ru/` — Card API payment | Прочитан |
| `https://docs.bepaid.by/ru/` — ERIP create payment | Прочитан |
| `https://docs.bepaid.by/ru/` — ERIP webhook | Прочитан |
| `https://docs.bepaid.by/ru/` — Reports API | Прочитан |
| `https://docs.bepaid.by/ru/` — Idempotency | Прочитан |
| `https://docs.bepaid.by/ru/` — Pay by link / products | Прочитан |
| `https://docs.bepaid.by/ru/` — Authorization vs payment | Прочитан |
| `web_app_server.py`, `storage.py`, `config.py`, `moyklass_client.py` | Прочитаны |

---

## 1. Текущее состояние

### Что уже реализовано

| Функция | Статус |
|---------|--------|
| Импорт истории bePaid через Reports API v2 (day-by-day) | Работает |
| bePaid webhook — приём, верификация подписи, сохранение | Работает |
| Извлечение `mk_user_id` из `tracking_id` / `order_id` / `additional_data` | Работает |
| Сверка bePaid ↔ МойКласс payments (по userId+сумме) | Работает |
| Сверка bePaid ↔ МойКласс userSubscriptions (confidence-based) | Работает v7.0.76 |
| Хранилище `bepaid_transactions` в SQLite | Работает |
| UI сверки (mobile cards, chips, статусы) | Работает v7.0.76 |
| Ежемесячный отчёт с оборотом bePaid | Работает |

### Что работает как есть

- `bepaid_transactions` сохраняет `transaction_uid`, `order_id`, `tracking_id`, `mk_user_id`, `match_status`, `mk_payment_id`.
- Сверка определяет 9 статусов: `already_in_moyklass`, `found_in_subscription`, `possible_subscription_match`, `historical_subscription_match`, `user_found_no_payment_or_subscription`, `possible_payment_match`, `needs_review`, `ignored_not_successful`, `ignored_test`.
- `BEPAID_AUTO_POST_TO_MOYKLASS = false` — автосоздание оплат **выключено**.
- `mk_payment_id` хранится в `bepaid_transactions`, но создание оплаты в МК ещё не реализовано.

### Что остаётся проблемой

1. **Ручная зависимость:** менеджеры создают оплаты/абонементы в МК произвольно — дата, сумма, назначение могут не совпасть с bePaid.
2. **Нет payment_intent:** агент не знает, за что именно платит клиент до факта оплаты.
3. **Нет создания оплаты в МК:** даже при уверенном совпадении (`found_in_subscription`) агент не может автоматически создать payment.
4. **account_number для ERIP:** сейчас берётся из `tracking_id` по regex. Нет структурированного формата.
5. **Дубли:** нет защиты от повторного создания payment в МК по одному transaction_uid.
6. **Неизвестное назначение:** нет поля "за какой месяц / какую группу / долг / аванс" — только сумма и userId.

---

## 2. Правильный процесс оплаты

### Схема (целевая архитектура)

```
[Менеджер / Агент]
    │
    ▼
1. СОЗДАТЬ PAYMENT INTENT
   Агент создаёт запись в таблице payment_intents:
   userId, amount, purpose (current_month / debt / advance / city_program),
   period_month, filialId, userSubscriptionId (если известен),
   group_classId (если известен), comment
    │
    ▼
2. СОЗДАТЬ ПЛАТЁЖНЫЙ ЗАПРОС В bePaid
   Агент вызывает bePaid API:
   - ERIP: POST /beyag/transactions/payments с account_number, tracking_id=payment_intent_id
   - Эквайринг: POST /products (pay-by-link) или /beyag/transactions/payments
   bePaid возвращает: transaction uid (ещё не оплачен), ссылку или QR
    │
    ▼
3. КЛИЕНТ ОПЛАЧИВАЕТ
   Через ERIP (банкомат/инфокиоск/ЕРИП онлайн) или по ссылке эквайринга
    │
    ▼
4. bePaid ПРИСЫЛАЕТ WEBHOOK
   POST /webhook/bepaid/{erip|acquiring}/{secret}
   Поля: transaction.uid, status=successful, paid_at, amount, tracking_id,
   erip.account_number, customer
    │
    ▼
5. АГЕНТ ОБРАБАТЫВАЕТ WEBHOOK
   a) Верификация подписи (RSA)
   b) Поиск payment_intent по tracking_id / order_id
   c) Сохранение в bepaid_transactions
   d) Проверка дубля: уже есть mk_payment_id для этого transaction_uid?
    │
    ├── ДУБЛЬ → отклонить, уведомить
    │
    └── НЕТ ДУБЛЯ → определить действие:
         - Если payment_intent найден + confidence high → предложить создать payment в МК
         - Если нет payment_intent, но userId известен → сверка + предложить действие
         - Иначе → needs_review
    │
    ▼
6. МЕНЕДЖЕР ПОДТВЕРЖДАЕТ (ручное действие в Mini App)
   UI показывает: клиент, сумма, назначение, месяц, филиал, абонемент
   Менеджер нажимает "Создать оплату в МойКласс"
    │
    ▼
7. АГЕНТ СОЗДАЁТ PAYMENT В МойКласс
   POST /v1/company/payments
   userId, date=paid_at, summa, optype=income, filialId, comment="bePaid:{transaction_uid}",
   userSubscriptionId (если применимо)
   Возвращает: mk_payment_id
    │
    ▼
8. АГЕНТ СОХРАНЯЕТ РЕЗУЛЬТАТ
   UPDATE bepaid_transactions SET mk_payment_id=?, posting_status='posted'
   INSERT INTO payment_audit_log
```

---

## 3. Какие поля передавать в bePaid

### ERIP — создание счёта

**Endpoint:** `POST https://api.bepaid.by/beyag/transactions/payments`

| Поле | Тип | Обязательно | Значение |
|------|-----|-------------|---------|
| `amount` | integer | Да | Сумма в копейках (171.75 BYN = 17175) |
| `currency` | string | Да | "BYN" |
| `description` | string | Да | Читаемое описание платежа (до 255 символов) |
| `ip` | string | Да | IP клиента или "127.0.0.1" |
| `payment_method.type` | string | Да | "erip" |
| `payment_method.account_number` | string | Да | Структурированный номер счёта (до 30 символов, см. раздел 4) |
| `notification_url` | string | Нет | URL нашего webhook-обработчика |
| `tracking_id` | string | Нет | ID нашего payment_intent (до 255 символов) |
| `order_id` | string | Нет | 12-значный числовой ID заказа |
| `expired_at` | string | Нет | ISO 8601, дедлайн оплаты |
| `customer.first_name` | string | Нет | Имя клиента |
| `customer.last_name` | string | Нет | Фамилия клиента |
| `customer.phone` | string | Нет | Телефон для SMS-уведомления |
| `customer.email` | string | Нет | Email для уведомления |
| `additional_data.notifications` | array | Нет | ["sms"] или ["email"] |
| `payment_method.service_info` | array | Нет | ["Желтый Клуб — оплата за июль 2026"] |
| `payment_method.receipt` | array | Нет | Текст квитанции |

**Пример `description`:**
```
"Желтый Клуб — Иванова Анна — июль 2026"
"Желтый Клуб — долг за май 2026 — Петров Иван"
"Желтый Клуб — аванс — Сидорова Мария"
```

### Эквайринг / Pay-by-link

**Endpoint (product):** `POST https://api.bepaid.by/products`

| Поле | Тип | Описание |
|------|-----|---------|
| `name` | string | Название продукта |
| `description` | string | Описание |
| `amount` | integer | Копейки |
| `currency` | string | BYN |
| `expired_at` | string | Дедлайн (если не immortal) |
| `transaction_type` | string | "payment" или "authorization" |
| `notification_url` | string | Webhook URL |

Ответ содержит `pay_url` — ссылка, которую отправляем клиенту.

> **Ограничение Pay-by-link:** endpoint `POST /products` **не поддерживает** `tracking_id` и `order_id` напрямую. Для передачи payment_intent_id лучше использовать прямое API (`/beyag/transactions/payments`).

**Для эквайринга с tracking_id:**
```
POST https://api.bepaid.by/beyag/transactions/payments
{
  "request": {
    "amount": 17175,
    "currency": "BYN",
    "description": "Желтый Клуб — Иванова Анна — июль 2026",
    "tracking_id": "pi_20260711_8875658_202607",
    "return_url": "https://...",
    "notification_url": "https://...",
    "payment_method": {"type": "credit_card"}
  }
}
```

---

## 4. Формат идентификаторов

### payment_intent_id

**Формат:** `pi_{date}_{userId}_{purpose_code}`

Примеры:
```
pi_20260711_8875658_202607      — текущий месяц 2026-07
pi_20260711_8875658_debt202605  — долг за май 2026
pi_20260711_8875658_adv202608   — аванс за август
pi_20260711_8875658_city202607  — городская программа
```

Максимум 255 символов (limit bePaid tracking_id). Рекомендуется ≤50.

### tracking_id (bePaid)

Устанавливать равным `payment_intent_id`. При получении webhook агент находит payment_intent по `tracking_id`.

### order_id (bePaid)

**Формат:** 12-значный числовой. Рекомендуется: `{userId:07d}{YYMM:04d}{seq:1d}` — например, `0887565826071`.

> **Важно:** `order_id` в bePaid не гарантирует уникальность на уровне API — уникальность обеспечивает только `tracking_id` и `transaction.uid`. `duplicate_check: true` (карты) — 30-секундное окно.

### account_number для ERIP

**Проблема:** клиент вводит account_number вручную в ERIP. Если создать новый счёт с тем же account_number, пока старый ещё не оплачен, bePaid может заменить старый счёт (поведение зависит от настроек сервиса ERIP).

**Рекомендуемый формат:** `{userId}` — просто ID клиента МойКласс.

Пример: клиент 8875658 → `account_number = "8875658"` (7 символов, ≤30).

**Почему только userId:**
- Клиент приходит в ЕРИП, вводит свой номер — ему не нужно знать месяц.
- Менеджер заранее создал payment_intent с нужным назначением.
- Назначение хранится в нашей БД, привязанное к account_number+userId.
- Клиент оплачивает → webhook → агент ищет payment_intent по account_number / tracking_id.

**Альтернативный формат (с месяцем):** `{userId}{YYMM}` — например, `88756582607` (11 символов). Позволяет различить несколько незакрытых счётов (долг + текущий), но клиент должен вводить правильный номер.

> **Открытый вопрос №1:** нужно уточнить у bePaid — что происходит при создании нового ERIP-счёта с тем же `account_number`, пока старый счёт `pending`? Заменяется ли он, сосуществуют ли оба, или возвращается ошибка?

---

## 5. Что хранить в локальной базе

### Таблица: `payment_intents`

```sql
CREATE TABLE payment_intents (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id           TEXT NOT NULL UNIQUE,  -- pi_20260711_8875658_202607
    mk_user_id          TEXT NOT NULL,          -- MoyKlass userId
    mk_user_name        TEXT,                   -- для отображения
    amount_minor        INTEGER NOT NULL,       -- в копейках
    amount_byn          REAL NOT NULL,
    currency            TEXT NOT NULL DEFAULT 'BYN',
    filial_id           INTEGER,                -- MoyKlass filialId
    purpose             TEXT NOT NULL,          -- current_month / debt / advance / city_program / other
    period_month        TEXT,                   -- YYYY-MM (за какой месяц)
    class_id            INTEGER,                -- MoyKlass classId (группа)
    user_subscription_id INTEGER,              -- если уже известен абонемент
    description         TEXT,                   -- читаемое описание
    comment             TEXT,                   -- внутренний комментарий менеджера
    bepaid_shop_type    TEXT,                   -- erip / acquiring
    bepaid_account_number TEXT,                 -- account_number для ERIP
    bepaid_order_id     TEXT,                   -- order_id, отправленный в bePaid
    bepaid_request_uid  TEXT,                   -- transaction uid, вернувшийся из bePaid при создании
    bepaid_pay_url      TEXT,                   -- ссылка для оплаты (эквайринг)
    bepaid_qr_raw       TEXT,                   -- QR для ERIP
    status              TEXT NOT NULL DEFAULT 'draft',
                                                -- draft / sent / paid / posted / cancelled / expired
    created_by          TEXT,                   -- Telegram user_id или логин менеджера
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    expires_at          TEXT,                   -- когда истекает
    bepaid_transaction_uid TEXT,               -- uid фактической оплаты из webhook
    mk_payment_id       INTEGER                 -- ID платежа, созданного в МойКласс
);

CREATE INDEX idx_pi_user ON payment_intents(mk_user_id);
CREATE INDEX idx_pi_status ON payment_intents(status);
CREATE INDEX idx_pi_account ON payment_intents(bepaid_account_number);
CREATE INDEX idx_pi_bp_uid ON payment_intents(bepaid_transaction_uid);
```

### Таблица: `bepaid_transactions` (уже существует, расширить)

Добавить столбцы (миграция при следующей версии):
```sql
ALTER TABLE bepaid_transactions ADD COLUMN payment_intent_id TEXT;
ALTER TABLE bepaid_transactions ADD COLUMN purpose TEXT;
ALTER TABLE bepaid_transactions ADD COLUMN period_month TEXT;
ALTER TABLE bepaid_transactions ADD COLUMN action_status TEXT;
  -- proposed / confirmed / rejected / posted / error
ALTER TABLE bepaid_transactions ADD COLUMN action_at TEXT;
ALTER TABLE bepaid_transactions ADD COLUMN action_by TEXT;
```

### Таблица: `payment_reconciliation_actions`

```sql
CREATE TABLE payment_reconciliation_actions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    bepaid_tx_id        INTEGER NOT NULL,       -- FK → bepaid_transactions.id
    payment_intent_id   TEXT,                   -- FK → payment_intents.intent_id
    action_type         TEXT NOT NULL,
                        -- propose_create / confirm_create / reject_create
                        -- propose_update / confirm_update / reject_update
                        -- manual_override / skip
    proposed_mk_user_id TEXT,
    proposed_summa      REAL,
    proposed_date       TEXT,
    proposed_filial_id  INTEGER,
    proposed_user_subscription_id INTEGER,
    proposed_comment    TEXT,
    proposed_optype     TEXT DEFAULT 'income',
    mk_payment_id       INTEGER,               -- создан в МК
    status              TEXT NOT NULL,         -- pending / done / error / skipped
    error_message       TEXT,
    created_by          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX idx_pra_tx ON payment_reconciliation_actions(bepaid_tx_id);
CREATE INDEX idx_pra_status ON payment_reconciliation_actions(status);
```

### Таблица: `payment_audit_log`

```sql
CREATE TABLE payment_audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT NOT NULL,
                    -- intent_created / bepaid_request_sent / webhook_received
                    -- action_proposed / action_confirmed / action_rejected
                    -- mk_payment_created / mk_payment_failed / duplicate_prevented
    entity_type     TEXT,   -- payment_intent / bepaid_transaction / mk_payment
    entity_id       TEXT,
    mk_user_id      TEXT,
    amount_byn      REAL,
    actor           TEXT,   -- system / user:{telegram_id}
    details         TEXT,   -- JSON
    created_at      TEXT NOT NULL
);
```

---

## 6. Как webhook должен определять назначение платежа

### Алгоритм (приоритет по убыванию)

```
1. Найти payment_intent по tracking_id (точное совпадение)
   → Назначение: payment_intent.purpose, period_month, filial_id, class_id, user_subscription_id
   → Уверенность: HIGH

2. Найти payment_intent по bepaid_account_number = erip.account_number
   → Если ровно один открытый intent для этого userId → HIGH
   → Если несколько интентов (долг + текущий) → MEDIUM, предложить выбор

3. Найти userId по tracking_id / order_id (regex \d{4,9}) → mk_user_id_source
   → Найти payment_intent по mk_user_id + status='sent' + amount_minor совпадает
   → Уверенность: HIGH

4. Только mk_user_id известен, payment_intent не найден
   → Сверка по МойКласс payments + userSubscriptions (текущий алгоритм v7.0.76)
   → Уверенность: confidence из алгоритма

5. mk_user_id не определён
   → needs_review
```

### Поле purpose и period_month

```python
PURPOSE_MAP = {
    "current_month": "Текущий месяц",
    "debt":          "Долг",
    "advance":       "Аванс",
    "city_program":  "Городская программа",
    "partial":       "Частичная оплата",
    "refund":        "Возврат",
    "other":         "Другое",
}
```

---

## 7. Как избежать дублей

### Правило №1 — один transaction_uid, один payment в МК

```
transaction.uid (bePaid) → уникальный идентификатор транзакции
```

Перед созданием payment в МойКласс:
1. Проверить `bepaid_transactions.mk_payment_id IS NOT NULL WHERE transaction_uid = ?`  
   Если есть → **стоп**, вернуть существующий mk_payment_id.
2. Проверить `payment_reconciliation_actions WHERE bepaid_tx_id = ? AND status = 'done'`  
   Если есть → **стоп**.

### Правило №2 — проверка в МойКласс по комментарию

Перед созданием `GET /v1/company/payments?userId={uid}&date={date}` и искать в `comment` строку `bePaid:{transaction_uid}`.  
Если найдено → **стоп**, сохранить `mk_payment_id`.

### Правило №3 — дедлайн создания

Создавать payment в МК только при `status = 'successful'` в bePaid.  
Статусы `pending`, `failed`, `expired` — не создавать.

### Правило №4 — проверка по сумме и дате (дополнительная)

```
GET /v1/company/payments?userId={uid}&date=[{date_start},{date_end}]&summa=[{summa}]
```

Окно `±1 день` относительно `paid_at`. Если в МК уже есть payment с такой же суммой и userId в окне — запросить подтверждение у менеджера.

### Хранение `mk_payment_id`

```sql
-- В bepaid_transactions
UPDATE bepaid_transactions 
SET mk_payment_id = ?,
    posting_status = 'posted',
    updated_at = ?
WHERE transaction_uid = ?;

-- В payment_intents
UPDATE payment_intents 
SET mk_payment_id = ?,
    status = 'posted',
    updated_at = ?
WHERE bepaid_transaction_uid = ?;
```

---

## 8. Как создавать оплату в МойКласс

### Endpoint

```
POST https://api.moyklass.com/v1/company/payments
Header: x-access-token: {api_key}
```

### Тело запроса

| Поле | Тип | Обязательно | Значение |
|------|-----|-------------|---------|
| `userId` | integer | **Да** | MoyKlass userId |
| `date` | string | **Да** | Дата оплаты из bePaid (`paid_at[:10]`) |
| `summa` | number | **Да** | Сумма в BYN (float) |
| `optype` | string | **Да** | `"income"` |
| `filialId` | integer | Нет | MoyKlass filialId (из payment_intent или профиля ученика) |
| `userSubscriptionId` | integer | Нет | ID абонемента (если уверенно определён) |
| `comment` | string | Нет | `"bePaid:{transaction_uid}; {description}"` |

**Пример:**
```json
{
  "userId": 8875658,
  "date": "2026-06-17",
  "summa": 171.75,
  "optype": "income",
  "filialId": 3,
  "userSubscriptionId": 17863561,
  "comment": "bePaid:a1b2c3d4-e5f6-...; Желтый Клуб — июль 2026"
}
```

### Ответ

```json
{
  "id": 12345678,
  "userId": 8875658,
  "date": "2026-06-17",
  "summa": 171.75,
  "userSubscriptionId": 17863561,
  "optype": "income",
  "filialId": 3,
  "comment": "bePaid:a1b2c3d4-..."
}
```

Поле `id` в ответе — это `mk_payment_id`, который нужно сохранить.

### Дата платежа

Использовать `paid_at` из bePaid (дата фактической оплаты клиентом), а не дату создания payment_intent.

Если `paid_at` позже периода (например, платёж за июнь пришёл 2 июля) — менеджер должен подтвердить, какую дату указывать:
- `paid_at` (реальная дата) — для учёта фактического поступления
- первый день `period_month` — для соответствия периоду

### Если нельзя связать с абонементом

Создавать payment **без** `userSubscriptionId`. МойКласс примет платёж как "свободный" — баланс ученика пополнится, но абонемент не уточнён. Менеджер позже может вручную привязать к абонементу в интерфейсе МК.

### Как узнать filialId

Приоритет:
1. Из `payment_intent.filial_id` (явно указан при создании)
2. Из абонемента ученика (`userSubscription.mainClassId` → через classes → filialId)
3. Из `mk_filial_id` в `bepaid_transactions` (если ранее сохранён)
4. Из профиля ученика (GET /v1/company/users/{userId})
5. Спросить у менеджера

### paymentTypeId

Поле `paymentTypeId` не входит в **POST** /v1/company/payments (только в **UPDATE** POST /payments/{id}). При создании тип оплаты определяется автоматически или по умолчанию. При необходимости — обновить отдельным вызовом.

> **Открытый вопрос №2:** какой `paymentTypeId` в МойКласс соответствует "ЕРИП" и "эквайринг" для Yellow Club? Нужно запросить у МойКласс через `GET /v1/company/paymentTypes`.

---

## 9. Нужно ли создавать/обновлять абонементы

### Анализ возможных стратегий

#### Стратегия A: Создавать только payment (РЕКОМЕНДУЕТСЯ)

**Что делаем:** только `POST /v1/company/payments` с `userSubscriptionId` если известен.

**Плюсы:**
- Минимальное вмешательство в данные МК
- Не ломаем уже созданные вручную абонементы
- Безопасно при частичных оплатах
- Нет риска дублирования абонементов

**Минусы:**
- Если абонемент не создан в МК — платёж "висит" без привязки

#### Стратегия B: Создавать абонемент + payment

**Что делаем:** `POST /v1/company/userSubscriptions` + `POST /v1/company/payments?userSubscriptionId`.

**Обязательные поля:** `userId`, `subscriptionId` (тип абонемента, catalog ID), `sellDate`, `classIds` (группы), `mainClassId`.

**Проблема:** нужно знать `subscriptionId` (ID из каталога абонементов МК) и `classIds` (группы ученика) — это не всегда доступно автоматически.

**Плюсы:**
- Полная автоматизация для новых учеников

**Минусы:**
- Высокий риск дублирования абонементов
- Требует знания `subscriptionId` и `classIds` заранее
- Нельзя применять без однозначного контекста

#### Стратегия C: Обновлять существующий абонемент

`POST /v1/company/userSubscriptions/{id}` — обновить `sellDate`, `price`, `comment`.

Используется только как ручная корректировка через UI, не в автоматическом потоке.

#### Стратегия D: Использовать invoices (НЕЛЬЗЯ)

`GET /v1/company/invoices` — только чтение + `POST /v1/company/invoices/{id}` — только обновление `payUntil` и `comment`. **Создать invoice через API нельзя** — они создаются системой МойКласс.

### Итоговая рекомендация

**Фаза 1 (v7.0.80):** Только стратегия A — создавать payment с `userSubscriptionId` если confidence=high.

**Фаза 2 (позже):** Стратегия B — только с явного согласия менеджера и только если `subscriptionId` + `classIds` заранее зафиксированы в payment_intent.

**Никогда не трогать автоматически:** `statusId` абонемента, `visitCount`, `autoDebit`.

---

## 10. Сценарии и обработка

### Таблица сценариев

| Сценарий | Поведение агента | Авто или ручное |
|----------|-----------------|----------------|
| Текущий месяц, userId известен, confidence=high | Предложить создать payment с `userSubscriptionId` | Ручное подтверждение |
| Предыдущий месяц / закрытие долга | Предложить, указать `period_month` в комментарии | Ручное подтверждение |
| Старый долг (>180 дней) | Флаг `historical`, предложить с предупреждением | Только ручное |
| Аванс | Создать payment без `userSubscriptionId`, period_month = следующий | Ручное подтверждение |
| Частичная оплата | Создать payment, отметить `partial: true`, уведомить | Ручное подтверждение |
| Городская программа | Создать payment, в comment = "Городская программа" | Ручное, особый `paymentTypeId` |
| Возврат (refund) | НЕ создавать payment автоматически. Отдельный процесс | Только ручное |
| Оплата за другого ребёнка | Нужно сопоставить семью. GET /v1/company/families. | Только ручное |
| userId не найден | needs_review, уведомить менеджера | Только ручное |
| Тест-транзакция (test=true) | Игнорировать автоматически | Авто-игнор |
| Неуспешная транзакция | Игнорировать | Авто-игнор |
| Дубль (mk_payment_id уже есть) | Заблокировать, уведомить | Авто-защита |
| Несколько детей у одного userId | Предупреждение, выбор менеджера | Только ручное |

### Возврат (refund)

bePaid: `status=refund` в webhook или `optype=refund` в Reports API.  
МойКласс: `POST /v1/company/payments` с `optype=debit` или `optype=refund`.  
**Не автоматизировать** — требует согласования.

---

## 11. Что можно автоматизировать сразу

| Функция | Обоснование |
|---------|-------------|
| Создание payment_intent в агенте | Менеджер вводит данные, агент сохраняет |
| Создание ERIP-счёта в bePaid через API | После создания intent, бесплатная операция |
| Сохранение bePaid webhook → bepaid_transactions | Уже реализовано |
| Поиск payment_intent по tracking_id / account_number | Алгоритм без внешних вызовов |
| Проверка дубля по transaction_uid | Локальная БД |
| Формирование предложения действия (propose_create) | Авто, но без выполнения |
| Уведомление менеджера о новом платеже | Telegram bot |
| Создание payment в МК после подтверждения менеджера | После кнопки в UI |
| Сохранение mk_payment_id + audit log | После успешного создания |

---

## 12. Что нельзя автоматизировать сразу

| Функция | Причина |
|---------|---------|
| Старые долги (>180 дней) | Неизвестен текущий статус долга в МК |
| Непонятное назначение (нет intent, нет userId) | Нет контекста |
| Совпадение только по сумме | Слишком неточно |
| userId не найден | Некуда привязывать |
| Несколько детей в семье | Неоднозначность |
| Создание абонементов | Нужен catalog ID и группы |
| Возвраты | Финансовая операция, требует согласования |
| Частичные оплаты | Нужно понять, к какому долгу |
| Городская программа | Особый тип оплаты, нужен отдельный paymentTypeId |
| Оплата за другого ребёнка | Нужно API families |

---

## 13. UI Mini App — нужные экраны

### 1. Создать платёж / payment intent

**Поля:**
- Ученик (поиск по имени / телефону / userId)
- Сумма (BYN)
- Назначение: `current_month / debt / advance / city_program / other`
- Период (месяц-год)
- Филиал (выбор из списка МойКласс)
- Группа (опционально)
- Абонемент (опционально, выбор из открытых абонементов ученика)
- Способ оплаты: ERIP / эквайринг
- Дедлайн оплаты
- Комментарий для менеджера

**Действие:** "Создать и отправить в bePaid" → получить QR / ссылку

### 2. Очередь платежей (payment intents)

Таблица / карточки:
- Клиент, сумма, назначение, период, статус (draft / sent / paid / posted)
- Иконка: 🕒 ожидает / ✅ оплачен / 📝 нужно подтвердить
- Фильтр: статус, месяц, менеджер

### 3. Сверка (текущий экран, расширенный)

Без изменений логики, добавить колонку "Действие":
- "Создать в МК" (кнопка при confidence=high + нет mk_payment_id)
- "Привязать к интенту" (если не найдено)

### 4. Подтверждение создания оплаты в МК

Карточка-подтверждение:
- Клиент: Иванова Анна (userId: 8875658)
- Сумма: 171.75 BYN
- Дата: 2026-06-17
- Назначение: Текущий месяц (июль 2026)
- Филиал: Центральный (id: 3)
- Абонемент №17863561 (171.75 BYN)
- Комментарий: `bePaid:a1b2c3d4-...`
- bePaid transaction: uid, paid_at, shop

Кнопки: **[Создать в МойКласс]** / [Пропустить] / [Изменить данные]

### 5. История действий

Лог: дата, клиент, сумма, действие, кто подтвердил, статус МК, mk_payment_id.  
Фильтр по дате, статусу, менеджеру.

### 6. Ошибки / дубли / нужна проверка

Специальная очередь: все транзакции со статусами `needs_review`, `error`.  
Счётчик в заголовке (badge).

---

## 14. Роли и доступы

| Действие | owner | admin / operations | director | client_manager | teacher |
|----------|-------|--------------------|----------|----------------|---------|
| Видеть сверку | ✅ | ✅ | ✅ | ✅ | — |
| Создать payment intent | ✅ | ✅ | — | ✅ | — |
| Создать ERIP-счёт в bePaid | ✅ | ✅ | — | ✅ | — |
| Подтвердить создание оплаты в МК | ✅ | ✅ | — | ✅ | — |
| Видеть очередь платежей | ✅ | ✅ | ✅ | ✅ | — |
| Видеть диагностику / audit log | ✅ | ✅ | — | — | — |
| Видеть bePaid credentials | ✅ | — | — | — | — |

Константа для кода: `PAYMENT_INTENT_ROLES = {"owner", "admin", "operations", "client_manager"}`

---

## 15. Риски

### Финансовые риски

| Риск | Описание | Митигация |
|------|----------|-----------|
| Дубль оплаты в МК | Одна bePaid-транзакция → два payment в МК | Проверка по transaction_uid перед созданием |
| Неверный месяц | paid_at июль → period_month июнь (долг) | Явное поле period_month в intent; менеджер подтверждает |
| Неверный ученик | UserID определён неточно | Проверка имени/телефона в UI перед подтверждением |
| Неверный филиал | filialId не определён | Обязательный выбор в intent; не создавать без filialId |
| Возврат без обратной проводки | refund в bePaid без debit в МК | Возвраты только вручную |
| Partial оплата → payment на полную сумму | Клиент заплатил часть | Показывать разницу в UI |

### Операционные риски

| Риск | Описание | Митигация |
|------|----------|-----------|
| Ручные правки в МК после авто-создания | Менеджер изменит сумму/дату в МК | mk_payment_id сохранён; агент не перезаписывает |
| account_number занят другим счётом | Два открытых ERIP счёта с одним account_number | Проверять статус существующих intent перед созданием |
| Rate limit МойКласс (7 req/s) | При массовой сверке превышение лимита | Throttle: max 5 req/s с очередью |
| bePaid webhook не дошёл | Клиент оплатил, агент не знает | Сверка через Reports API как резервный путь |
| payment_intent устарел (истёк) | Дедлайн прошёл, счёт в bePaid expired | Статус intent = 'expired'; создавать новый |

### Технические риски

| Риск | Описание | Митигация |
|------|----------|-----------|
| Webhook получен дважды | bePaid повторяет при отсутствии ответа 200 | Идемпотентный upsert по transaction_uid |
| mk_payment_id не сохранился | Сеть упала после POST МК, до ответа | Atomic update в БД; retry с проверкой по комментарию |
| Смена userId в МК | Редкий кейс: merge учеников | Хранить raw_json для аудита |

---

## 16. Рекомендуемый план версий

| Версия | Содержание |
|--------|-----------|
| `payment-automation-research` | Текущий документ. Код не изменён. |
| **v7.0.77** | Таблица `payment_intents` в БД. UI draft: экран "Создать платёж" (форма, без отправки в bePaid). Роли. |
| **v7.0.78** | Создание ERIP-счёта в bePaid через API. Формирование account_number. Сохранение `bepaid_request_uid` и `bepaid_pay_url`. |
| **v7.0.79** | Webhook привязывает transaction к payment_intent. Поиск по tracking_id / account_number. Статус intent: paid. Уведомление в Telegram. |
| **v7.0.80** | UI подтверждения. Кнопка "Создать в МойКласс". POST /v1/company/payments. Сохранение mk_payment_id. Audit log. |
| **v7.0.81** | Полная защита от дублей. Проверка по transaction_uid + поиск в МК по comment. Обработка ошибок МК (400/402/500). |
| **v7.0.82** | Ограниченный auto-post только для HIGH confidence + найден payment_intent + одобрение встроено в intent (pre-approved flag). `BEPAID_AUTO_POST_TO_MOYKLASS` остаётся false по умолчанию. |
| **v7.0.83+** | Экраны истории, очереди. Фильтры. Уведомления об ошибках. Поддержка families API. |

---

## 17. Открытые вопросы

### bePaid (нужно уточнить в поддержке или через тестирование)

1. **account_number и повторные счета ERIP:**  
   Если создать новый ERIP-счёт с тем же `account_number`, пока старый `pending` — что происходит? Заменяется, сосуществует или ошибка?

2. **tracking_id для ERIP webhook:**  
   Гарантированно ли возвращается `tracking_id` в ERIP webhook, если он был передан при создании счёта?

3. **Получение ссылки/QR при создании ERIP-счёта:**  
   Возвращает ли `POST /beyag/transactions/payments` с `payment_method.type=erip` QR-код или ссылку для клиента? Судя по структуре webhook (`erip.qr_code_raw`), QR формируется — но когда?

4. **Reports API и тип эквайринга:**  
   Поле `payment_method_type` в Reports API v2 может быть `"credit_card"`, `"alternative"`, `"erip"` — правильно ли понимаем?

5. **`order_id` — уникальность:**  
   Требует ли bePaid уникальный `order_id` при создании счёта, или это справочное поле?

6. **Webhook retry policy:**  
   Сколько раз bePaid повторяет webhook при отсутствии ответа 200? Какой таймаут?

7. **`custom_fields` в ERIP:**  
   Поддерживают ли ERIP-транзакции `custom_fields` (label/value), или только card/APM?

### МойКласс (нужно уточнить у поддержки или через тест-окружение)

8. **`paymentTypeId` для ERIP и эквайринга:**  
   Какие `paymentTypeId` настроены в МойКласс для Yellow Club? Нужно вызвать `GET /v1/company/paymentTypes`.

9. **Ограничения `comment` в payments:**  
   Есть ли максимальная длина поля `comment`? Можно ли хранить UUID bePaid (36 символов)?

10. **Rate limit и пагинация:**  
    Документация говорит: 7 запросов/секунду. Есть ли лимит на `limit` в GET payments/userSubscriptions? Какой максимум?

11. **Доступность `paymentTypeId` при создании:**  
    Поле `paymentTypeId` присутствует только в POST /payments/{id} (обновление), но не в POST /payments (создание). Можно ли указать тип при создании? Не подтверждено.

12. **`externalId` в userSubscriptions:**  
    Можно ли использовать `externalId` для хранения bePaid tracking_id при создании абонемента? Нет ли конфликтов с другими интеграциями МойКласс?

13. **Одновременные платежи одного ученика:**  
    Если у ученика два долга и два открытых intent — как разграничить, к какому платёж относится, при отсутствии tracking_id?

---

## Итог исследования

| Вопрос | Ответ |
|--------|-------|
| Создан docs/payment_automation_research.md | ✅ |
| Можно создавать payment в МойКласс через API | ✅ POST /v1/company/payments |
| Нужно ли создавать userSubscriptions автоматически | ❌ Только payment; абонемент — вручную или в фазе 2 |
| Invoices можно создавать через API | ❌ Только чтение и обновление payUntil/comment |
| Рекомендуемый процесс | payment_intent → bePaid request → webhook → ручное подтверждение → POST МК |
| Основные таблицы | payment_intents, bepaid_transactions (расширить), payment_reconciliation_actions, payment_audit_log |
| UI-экраны | Создать intent, очередь, сверка+действие, подтверждение, история, ошибки |
| Первые сценарии для автоматизации | Создание ERIP-счёта, webhook→intent lookup, предложение создания, ручное подтверждение |
| Что оставить ручным | Возвраты, старые долги, families, city_program, нет userId |
| Production-код изменён | ❌ Нет |
| Auto-post включён | ❌ Нет (`BEPAID_AUTO_POST_TO_MOYKLASS` = false) |
| Ключи/токены в документе | ❌ Нет |
| Файлы добавлены | `docs/payment_automation_research.md` |

---

*Документ подготовлен на основе: `docs/reference/moyklass_openapi.json` (v1.1.8), `docs.bepaid.by` (ERIP + Card API + Reports + Webhooks + Idempotency), анализа существующего кода агента.*
