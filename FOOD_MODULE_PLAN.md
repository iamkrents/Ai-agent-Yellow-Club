# FOOD_MODULE_PLAN.md — Модуль «Питание лагеря» Yellow Club

**Статус:** backend foundation реализован, UI не начат  
**Дата плана:** 2026-06-27  
**Дата реализации backend foundation:** 2026-06-27  
**Версия приложения при составлении плана:** v6.7.1

## Статус реализации по шагам

| Шаг | Описание | Статус |
|---|---|---|
| 1 | Config flags + Storage tables | ✅ Сделано |
| 2 | Diagnostic endpoint `POST /api/food/debug/sync-camp-children` | ✅ Сделано |
| 3 | Menu CRUD (backend) | Не начат |
| 4 | Parent link (backend) | Не начат |
| 5 | Parent orders (backend) | Не начат |
| 6 | Admin UI (frontend) | Не начат |
| 7 | Parent UI (frontend) | Не начат |
| 8 | Feature flag QA | Не начат |

---

## Что реализовано (backend foundation)

### Feature flags (config.py)
- `FOOD_MODULE_ENABLED=false` — по умолчанию выключен, безопасно для MVP
- `CAMP_CLASS_NAME_FILTER=Summer Camp` — фильтр для поиска лагерных групп в МойКласс
- `me()` теперь возвращает `foodModuleEnabled: bool`

### Новые таблицы SQLite (storage.py)
Добавлены через `_init_food_tables()` вызываемый из `Storage._init()`:
- `camp_children` — дети из лагерных групп МойКласс
- `parent_child_links` — привязки родителей к детям по коду (формат `YC-XXXX`)
- `food_menus` — меню по датам
- `food_items` — блюда меню
- `food_orders` — заказы родителей
- `food_order_items` — позиции заказа

### Storage методы (storage.py)
- `upsert_camp_child(child: dict)` — вставка/обновление ребёнка из МойКласс
- `list_camp_children(active_only=True)` — список детей
- `get_camp_child_by_mk_student_id(mk_student_id)` — поиск по ID
- `generate_child_link_code(mk_student_id)` — генерирует уникальный код `YC-XXXX`
- `get_child_by_link_code(code)` — поиск по коду
- `link_parent_to_child(parent_telegram_id, code)` — привязка родителя

### Diagnostic endpoint (web_app_server.py)
`POST /api/food/debug/sync-camp-children`

Доступ: только `owner` / `methodist` / `operations` (через `_require_admin`)

Тело запроса:
```json
{ "save": false }
```
- `save: false` (по умолчанию) — только диагностика, ничего не сохраняет
- `save: true` — дополнительно сохраняет найденных детей в `camp_children`

Ответ:
```json
{
  "ok": true,
  "foodModuleEnabled": true,
  "filter": "Summer Camp",
  "savedToDB": false,
  "classesFoundCount": 2,
  "classes": [
    {
      "classId": "123",
      "className": "Summer Camp YC2",
      "rawKeys": ["id", "name", "..."],
      "usersCount": 12,
      "usersEndpointOk": true,
      "usersSample": [
        {
          "rawKeys": ["id", "name", "firstName", "lastName"],
          "id": "456",
          "firstName": "Иван",
          "lastName": "Иванов"
        }
      ]
    }
  ],
  "savedCount": 0,
  "skippedCount": 0,
  "errors": []
}
```

Если `FOOD_MODULE_ENABLED=false`:
```json
{ "ok": false, "error": "food_module_disabled" }
```

### Как вызвать endpoint для диагностики

```bash
# Только диагностика (ничего не сохраняет)
curl -X POST https://<your-domain>/api/food/debug/sync-camp-children \
  -H "Content-Type: application/json" \
  -d '{"initData": "<telegram_initdata>", "save": false}'

# С сохранением в БД
curl -X POST https://<your-domain>/api/food/debug/sync-camp-children \
  -H "Content-Type: application/json" \
  -d '{"initData": "<telegram_initdata>", "save": true}'
```

**Что прислать после первого теста:**
1. `classesFoundCount` — сколько групп найдено с фильтром "Summer Camp"
2. Для каждого класса: `usersCount` и `rawKeys` первого пользователя
3. Есть ли в `usersSample` поля `firstName`, `lastName` или только `name`/`fullName`
4. Если `usersCount: 0` — поле `usersEndpointError`

---

## 1. Цель модуля

Дать родителям детей из лагерных групп МойКласс (Summer Camp) возможность выбирать питание для своего ребёнка прямо в Telegram Mini App. Администратор видит сводную заявку по всем блюдам и список кто не выбрал питание.

**Что это решает:**
- Сейчас выбор питания происходит вручную/устно → ошибки и потери данных.
- Родители смогут выбирать из опубликованного меню по датам.
- Администратор получает автоматическую сводку для заказа у поставщика.

---

## 2. Роли в модуле

| Роль | Тип | Откуда | Что может |
|---|---|---|---|
| `parent` | Клиентский (новый) | Telegram user_id, НЕ сотрудник | Привязать ребёнка, видеть его, выбирать питание |
| `owner` / `methodist` / `operations` | Сотрудник (существующий) | `staff_users.role` | Управлять детьми, меню, блюдами, видеть заказы |
| `intern` / `teacher` | Сотрудник (существующий) | `staff_users.role` | Не видят модуль питания |

**Новая роль `parent` НЕ входит в `staff_users`** — это отдельная таблица `parent_links`. В `me()` она определяется отдельной логикой: если `user_id` есть в `parent_links` и не в `staff_users` — возвращается `role: "parent"`.

---

## 3. Сценарии использования

### 3.1 Родитель — привязка ребёнка (one-time)
1. Администратор синхронизирует детей из МойКласс (Summer Camp).
2. Для каждого ребёнка система генерирует уникальный 8-символьный код.
3. Администратор передаёт код родителю (в чате/распечатке).
4. Родитель открывает Mini App → видит экран «Введите код ребёнка».
5. Вводит код → ребёнок привязывается к его Telegram ID.
6. Один родитель может привязать несколько детей.

### 3.2 Родитель — выбор питания
1. Открывает Mini App → видит: «Питание», «Мои дети».
2. На экране «Питание» — опубликованное меню с датой дедлайна.
3. Для каждого ребёнка выбирает позиции из меню (категории, блюда, количество).
4. Подтверждает заказ. До дедлайна — может редактировать.
5. После дедлайна — заказ заморожен, родитель видит «Заказ принят».

### 3.3 Администратор — управление детьми
1. Открывает Админ → Питание.
2. Нажимает «Синхронизировать из МойКласс».
3. Система вызывает `search_classes("Summer Camp")` → находит группы.
4. Для каждой группы: `get_class_users(class_id)` → получает детей.
5. Дети сохраняются/обновляются в таблице `camp_children`.
6. Администратор видит список детей с кодами, может скопировать код или сгенерировать новый.

### 3.4 Администратор — создание меню
1. Создаёт меню (название, дата, дедлайн).
2. Добавляет блюда по категориям (завтрак, обед, полдник; название, вес, цена).
3. Публикует меню → родители видят его в своём приложении.
4. После дедлайна — закрывает меню (заказы замораживаются).

### 3.5 Администратор — просмотр заказов
1. Открывает меню → Сводка: по каждому блюду — суммарное количество.
2. Список кто не выбрал питание (привязанные дети без заказа).
3. Можно скачать/скопировать для передачи поставщику.

---

## 4. Data Model

### 4.1 Новые таблицы SQLite

#### `camp_children`
Дети из Summer Camp, синхронизированные из МойКласс.

```sql
CREATE TABLE camp_children (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    mk_student_id TEXT NOT NULL,       -- ID из МойКласс (userId/studentId)
    full_name TEXT NOT NULL,           -- ФИО ребёнка
    group_name TEXT,                   -- Название группы (Summer Camp ...)
    mk_class_id TEXT,                  -- ID класса/группы в МойКласс
    camp_lesson_id TEXT,               -- ID занятия (если привязан к конкретному)
    link_code TEXT UNIQUE,             -- 8-символьный код для привязки родителем
    link_code_created_at TEXT,         -- Когда сгенерирован код
    active INTEGER NOT NULL DEFAULT 1, -- 0 = ребёнок убран из лагеря
    synced_at TEXT                     -- Последняя синхронизация с МойКласс
);
CREATE UNIQUE INDEX idx_camp_children_mk ON camp_children(mk_student_id);
CREATE INDEX idx_camp_children_code ON camp_children(link_code);
CREATE INDEX idx_camp_children_active ON camp_children(active, group_name);
```

#### `parent_links`
Привязки родителей к детям.

```sql
CREATE TABLE parent_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    parent_telegram_id INTEGER NOT NULL,  -- Telegram user_id родителя
    camp_child_id INTEGER NOT NULL,       -- FK → camp_children.id
    mk_student_id TEXT NOT NULL,          -- Денормализовано для быстрых запросов
    confirmed_at TEXT,                    -- Когда привязка подтверждена (NULL = ожидает)
    UNIQUE(parent_telegram_id, camp_child_id)
);
CREATE INDEX idx_parent_links_telegram ON parent_links(parent_telegram_id, confirmed_at);
CREATE INDEX idx_parent_links_child ON parent_links(camp_child_id);
```

#### `food_menus`
Меню по датам.

```sql
CREATE TABLE food_menus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by INTEGER,                   -- Telegram user_id создателя
    date TEXT NOT NULL,                   -- Дата питания (YYYY-MM-DD)
    title TEXT NOT NULL,                  -- Название: "Питание 10 июля"
    deadline_at TEXT NOT NULL,            -- Дедлайн заказа (ISO datetime)
    status TEXT NOT NULL DEFAULT 'draft', -- draft / published / closed
    notes TEXT                            -- Комментарий для родителей
);
CREATE INDEX idx_food_menus_date ON food_menus(date, status);
```

#### `food_items`
Блюда в меню.

```sql
CREATE TABLE food_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    menu_id INTEGER NOT NULL,             -- FK → food_menus.id
    category TEXT NOT NULL,              -- breakfast / lunch / snack / dinner / other
    name TEXT NOT NULL,                  -- Название блюда
    description TEXT,                    -- Описание (необязательно)
    weight_g INTEGER,                    -- Вес в граммах
    price INTEGER NOT NULL DEFAULT 0,    -- Цена в копейках (0 = включено)
    is_available INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_food_items_menu ON food_items(menu_id, category, sort_order);
```

#### `food_orders`
Заказы родителей.

```sql
CREATE TABLE food_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    menu_id INTEGER NOT NULL,             -- FK → food_menus.id
    parent_telegram_id INTEGER NOT NULL,  -- Telegram user_id родителя
    camp_child_id INTEGER NOT NULL,       -- FK → camp_children.id
    mk_student_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft', -- draft / submitted / cancelled
    total_price INTEGER NOT NULL DEFAULT 0,
    submitted_at TEXT,
    UNIQUE(menu_id, camp_child_id)        -- Один заказ на ребёнка на меню
);
CREATE INDEX idx_food_orders_menu ON food_orders(menu_id, status);
CREATE INDEX idx_food_orders_parent ON food_orders(parent_telegram_id, menu_id);
CREATE INDEX idx_food_orders_child ON food_orders(camp_child_id, menu_id);
```

#### `food_order_items`
Позиции в заказе.

```sql
CREATE TABLE food_order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    order_id INTEGER NOT NULL,            -- FK → food_orders.id
    item_id INTEGER NOT NULL,             -- FK → food_items.id
    quantity INTEGER NOT NULL DEFAULT 1,
    price_snapshot INTEGER NOT NULL DEFAULT 0  -- Цена на момент заказа
);
CREATE INDEX idx_food_order_items_order ON food_order_items(order_id);
CREATE INDEX idx_food_order_items_item ON food_order_items(item_id);
```

### 4.2 Изменения в существующих таблицах

**Нет.** Все новые данные — в отдельных таблицах. Существующие таблицы не меняются.

---

## 5. Feature Flag

В `config.py` добавить одно поле в `Settings`:

```python
food_module_enabled: bool  # FOOD_MODULE_ENABLED=true/false
```

В `load_settings()`:
```python
food_module_enabled=_bool(os.getenv("FOOD_MODULE_ENABLED", "false"), False),
```

**Поведение при `FOOD_MODULE_ENABLED=false`:**
- `me()` НЕ возвращает `role: "parent"` (родители видят стандартный экран «нет роли»)
- Admin tabs не включают «Питание»
- Все `/api/food/*` endpoints возвращают `{"ok": false, "error": "Модуль питания отключён"}`
- Никаких UI изменений в текущем staff MVP

**Поведение при `FOOD_MODULE_ENABLED=true`:**
- Логика `parent` активна
- Новые endpoints доступны
- Frontend показывает клиентский и/или административный UI питания

---

## 6. Backend Endpoints

### 6.1 Определение роли parent в `me()`

В `WebAppHandler.me()` ПОСЛЕ проверки staff role:
```python
food_enabled = bool(getattr(self.settings, "food_module_enabled", False))
if food_enabled and not role:
    # Не сотрудник — проверяем, является ли пользователь родителем
    parent_children = self.storage.get_parent_children(user_id)
    if parent_children or self.storage.has_any_parent_link(user_id):
        # Родитель с хотя бы одной ссылкой (даже ожидающей)
        data["role"] = "parent"
        data["roleLabel"] = "Родитель"
        # Остальные capabilities = False (не staff)
```

### 6.2 Admin Endpoints

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/food/sync-camp-children` | Синхронизация детей из МойКласс (Summer Camp) |
| GET | `/api/food/camp-children` | Список детей с кодами |
| POST | `/api/food/camp-children/{id}/regenerate-code` | Перегенерировать код для ребёнка |
| POST | `/api/food/menus` | Создать меню |
| GET | `/api/food/menus` | Список меню (draft/published/closed) |
| GET | `/api/food/menus/{id}` | Меню с блюдами |
| POST | `/api/food/menus/{id}/items` | Добавить блюдо |
| PATCH | `/api/food/menus/{id}/items/{item_id}` | Изменить блюдо |
| DELETE | `/api/food/menus/{id}/items/{item_id}` | Удалить блюдо |
| POST | `/api/food/menus/{id}/publish` | Опубликовать меню |
| POST | `/api/food/menus/{id}/close` | Закрыть меню (заморозить заказы) |
| GET | `/api/food/menus/{id}/summary` | Сводка по блюдам (кол-во каждого) |
| GET | `/api/food/menus/{id}/missing` | Кто не выбрал питание |
| GET | `/api/food/menus/{id}/orders` | Все заказы к меню |

### 6.3 Parent Endpoints

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/food/link-child` | Привязать ребёнка по коду |
| DELETE | `/api/food/unlink-child/{child_id}` | Отвязать ребёнка |
| GET | `/api/food/my-children` | Список своих детей |
| GET | `/api/food/active-menus` | Активные (опубликованные) меню |
| GET | `/api/food/menus/{id}` | Меню с блюдами (read-only для родителя) |
| POST | `/api/food/orders` | Создать/обновить заказ |
| GET | `/api/food/my-orders` | Мои заказы |
| GET | `/api/food/orders/{id}` | Конкретный заказ |

### 6.4 Синхронизация детей из МойКласс

Алгоритм `sync_camp_children()`:
1. `mk_client.search_classes("Summer Camp")` → список групп.
2. Для каждой группы: `mk_client.get_class_users(class_id)` → список студентов.
3. Для каждого студента: `INSERT OR IGNORE INTO camp_children` (mk_student_id, full_name, group_name, mk_class_id).
4. Генерировать `link_code` (8 символов, alphanumeric upper) если нет.
5. Пометить удалённых (не появившихся в синхронизации) как `active=0`.
6. Вернуть: `{ok: true, synced: N, created: M, deactivated: K}`.

**Поле имени студента в МойКласс:** Нужно выяснить реальные ключи при первой синхронизации. Вероятные варианты: `name`, `fullName`, `firstName`+`lastName`, `fio`. Код должен пробовать все варианты аналогично тому, как `moyklass_client.py` обходит несколько endpoint-вариантов.

---

## 7. Frontend Structure

### 7.1 Клиентский интерфейс (role = parent)

**Новый набор вкладок:** полностью отдельный от staff MVP.

```javascript
MVP_TABS_BY_ROLE["parent"] = ["food", "children", "help"];
```

| Вкладка | ID | Содержание |
|---|---|---|
| Питание | `food` | Список активных меню → выбор блюд для каждого ребёнка |
| Мои дети | `children` | Список привязанных детей; форма «Добавить ребёнка» с полем кода |
| Помощь | `help` | Инструкция для родителя |

**Стартовый экран при отсутствии детей:** вместо `food` — экран «Введите код».

### 7.2 Admin subtabs

В `ADMIN_TABS_BY_ROLE` для `owner`/`operations`/`methodist` добавить `"food"` (только когда `food_module_enabled=true`, проверяется через capabilities).

Новая подвкладка **«Питание»** в Админ → структура:

```
Питание
├── Дети лагеря
│   ├── [Синхронизировать из МойКласс]
│   ├── Список детей + коды
│   └── Статус привязки каждого ребёнка
├── Меню
│   ├── [Создать меню]
│   ├── Список меню (статус: draft / published / closed)
│   └── Карточка меню → блюда, публикация, закрытие
└── Заказы (к конкретному меню)
    ├── Сводка по блюдам
    ├── Список кто не выбрал
    └── Все заказы
```

### 7.3 Routing решение

Текущий SPA (`index.html`) + `app.js` используют `state.me.role`. Добавление `parent` — это новый ветка в `setupRoleUi()`:

```javascript
if (role === "parent" && isFoodEnabled()) {
  // показать parent tabs, скрыть staff tabs
  renderParentFoodUi();
} else if (!role && isFoodEnabled()) {
  // показать экран "введите код"
  renderChildLinkScreen();
}
```

`isFoodEnabled()` → читает `state.me.foodModuleEnabled` (backend возвращает из capabilities).

---

## 8. Безопасность

### 8.1 Привязка родителей (анти-угадывание кода)

- Коды — 8 символов из `[A-Z0-9]` → 36^8 ≈ 2.8 трлн вариантов.
- Rate limit на `/api/food/link-child`: не более 5 попыток за 10 минут на Telegram user_id.
- Код однократный после подтверждения: помечаем `link_code_used=1` (можно NOT NULL после использования, либо удалять из поиска).
- Родитель НЕ видит список всех детей — только `my-children`.

### 8.2 Изоляция данных родителя

- `GET /api/food/my-children` → WHERE `parent_telegram_id = auth.user_id`.
- `POST /api/food/orders` → проверяем что `camp_child_id` принадлежит этому родителю.
- `GET /api/food/menus/{id}` для родителя → возвращает только items, без данных других заказов.

### 8.3 Admin-only операции

Все `/api/food/sync-*`, `/api/food/menus` (POST/PATCH), `/api/food/menus/{id}/publish` — проверять `role in ADMIN_ROLES`. Возвращать 403 иначе.

---

## 9. MVP Scope (первая версия)

### Входит:
- Таблицы: `camp_children`, `parent_links`, `food_menus`, `food_items`, `food_orders`, `food_order_items`
- Config flag `FOOD_MODULE_ENABLED`
- Admin: синхронизация детей, генерация кодов, создание меню/блюд, публикация, сводка
- Parent: привязка ребёнка по коду, просмотр меню, создание заказа, редактирование до дедлайна
- `me()` возвращает `role: "parent"` для привязанных родителей
- Frontend: клиентские вкладки + admin subtab «Питание»

### НЕ входит в первую версию:
- Оплата (онлайн/наличные)
- OCR меню (ручное создание блюд)
- Push-уведомления родителям о новом меню
- Telegram Bot команды для родителей
- История заказов старше текущего сезона
- Аллергии / диетические предпочтения
- Фото блюд
- Экспорт в Excel

---

## 10. Риски

| Риск | Вероятность | Влияние | Митигация |
|---|---|---|---|
| МойКласс не возвращает имена студентов через `get_class_users()` | Средняя | Высокое | Пробовать несколько endpoint-вариантов (аналогично текущей логике в `moyklass_client.py`); fallback — admin вводит имена вручную |
| Группы Summer Camp не называются именно "Summer Camp" | Средняя | Высокое | Сделать `camp_class_name_filter` в config (ENV: `CAMP_CLASS_NAME_FILTER=Summer Camp`), по умолчанию пустой (вернуть все классы для выбора) |
| Родитель теряет код | Низкая | Низкое | Admin может перегенерировать код; старые ссылки при перегенерации не ломаются |
| Один ребёнок привязан к двум родителям | Ожидаемо | Норм | UNIQUE (parent_telegram_id, camp_child_id), но parent_links разрешает двух разных родителей к одному ребёнку — оба могут делать заказы (братья/сестры, второй родитель) |
| Два заказа от разных родителей одного ребёнка | Средняя | Среднее | UNIQUE (menu_id, camp_child_id) — первый зафиксированный. Или: последний перезаписывает. Решение нужно до реализации |
| Масштаб: сотни детей, десятки меню | Низкая | Низкое | SQLite справится; нет реального-time требований |
| Ломает staff MVP | Низкая при правильной изоляции | Критическое | flag `FOOD_MODULE_ENABLED=false` по умолчанию; все изменения в `me()` только внутри `if food_enabled and not role:` |

---

## 11. Порядок внедрения по шагам

### Шаг 1 — Config + Storage (только схема, без логики)
- Добавить `food_module_enabled` в `config.py`
- Добавить `_init_food_tables()` в `Storage._init()` с 5 новыми CREATE TABLE
- `python -m py_compile` — синтаксис ОК
- **Не меняет поведение** при `FOOD_MODULE_ENABLED=false`

### Шаг 2 — Admin sync (backend only)
- Добавить `FoodModule` класс в отдельный файл `food_module.py`
- Реализовать `sync_camp_children(mk_client, config)` — вызов МойКласс, сохранение в таблицу
- Endpoint: `POST /api/food/sync-camp-children` (admin only)
- Endpoint: `GET /api/food/camp-children`
- Тестировать через curl/Postman — проверить, какие поля реально возвращает МойКласс
- **Нет frontend изменений**

### Шаг 3 — Menu CRUD (backend only)
- Реализовать CRUD для `food_menus` и `food_items`
- Endpoints: POST/GET/PATCH menus, POST/PATCH/DELETE items
- Publish / close transitions
- **Нет frontend изменений**

### Шаг 4 — Parent link (backend only)
- Реализовать `link_child(user_id, code)` с rate limit
- Endpoint: `POST /api/food/link-child`
- `me()` расширить: для пользователя без staff-роли → проверить `parent_links` → вернуть `role: "parent"` если есть
- Тестировать: создать тестового ребёнка в таблице, войти как "родитель", проверить me()

### Шаг 5 — Parent orders (backend only)
- `POST /api/food/orders`, `GET /api/food/my-orders`, order items
- Проверка: нельзя заказать если deadline прошёл, нельзя заказать на чужого ребёнка
- Summary endpoint для admin

### Шаг 6 — Admin UI (frontend)
- Новая подвкладка «Питание» в Adminpanel
- Список детей + синхронизация
- Создание/редактирование меню
- Просмотр заказов и сводки
- cache bump

### Шаг 7 — Parent UI (frontend)
- Клиентские вкладки для `role: "parent"`
- Экран «Введите код»
- Экран «Мои дети»
- Экран «Питание» → выбор блюд
- Тест весь flow от привязки до заказа
- cache bump

### Шаг 8 — Feature flag QA
- Убедиться что `FOOD_MODULE_ENABLED=false` → никакого клиентского UI, никаких admin-tabs питания
- Убедиться что staff MVP (intern, teacher, admin) работает без изменений
- RELEASE_CHECKLIST.md обновить

---

## 12. Изменения, необходимые в существующих файлах

### `config.py`
```python
# В @dataclass Settings добавить:
food_module_enabled: bool
# В load_settings() добавить:
food_module_enabled=_bool(os.getenv("FOOD_MODULE_ENABLED", "false"), False),
```

### `storage.py`
- Добавить `_init_food_tables()` метод и вызвать его из `_init()`
- Добавить методы: `get_parent_children()`, `link_child_by_code()`, `get_camp_children()`, `upsert_camp_child()`, `create_food_menu()`, `get_food_menus()`, `create_food_order()`, и т.д.
- **Не менять существующие методы**

### `web_app_server.py`
- В `me()` добавить ветку для `parent` (в блок `if food_enabled and not role:`)
- В `_capabilities_for_user()` добавить `"canUseFood": food_enabled and role in ADMIN_ROLES`  и `"foodModuleEnabled": food_enabled`
- В `ADMIN_TABS_BY_ROLE` добавить `"food"` для admin ролей (только когда `food_enabled`, иначе список статичный — лучше добавлять динамически в `_capabilities_for_user`)
- Добавить routing для `/api/food/*` endpoints
- **Не менять существующую логику**

### Новый файл `food_module.py`
- Класс `FoodModule` с методами: `sync_camp_children`, `link_child`, `create_menu`, `add_item`, `publish_menu`, `close_menu`, `get_summary`, `create_order`

### `miniapp/app.js`
- Добавить `MVP_TABS_BY_ROLE["parent"]`
- Добавить `isFoodEnabled()` helper
- Добавить `renderParentFoodUi()`, `renderChildLinkScreen()`
- В `setupRoleUi()` добавить ветку для `parent`
- Добавить новую подвкладку «Питание» в admin panel

### `miniapp/index.html` + `miniapp/styles.css`
- Новые секции для parent UI и admin food tab
- Соответствующие стили

---

## 13. Открытые вопросы (нужно прояснить до реализации)

1. **Два заказа на одного ребёнка от двух родителей** — разрешить или нет? Если нет — кто первый тот и прав, или последний перезаписывает?
2. **Реальные имена полей студентов в МойКласс** — нужно сначала сделать `POST /api/food/sync-camp-children` и посмотреть реальный ответ API, чтобы правильно распарсить имена.
3. **Название групп** — как именно называются лагерные группы в МойКласс? Нужно env-переменная для фильтра или поиск по паттерну?
4. **Цена блюд** — в копейках или рублях? Или питание бесплатное (включено в стоимость лагеря) и `price=0`?
5. **Время дедлайна** — в какой timezone? Локальный сервер или UTC?

---

## 14. Python syntax check

```bash
python -m py_compile config.py storage.py web_app_server.py intern_track.py
# Результат: OK (exit:0), текущая кодовая база чистая, изменений нет
```

**Текущий статус:** ни один файл в этом плане ещё не изменён. Всё выше — архитектурный план.
