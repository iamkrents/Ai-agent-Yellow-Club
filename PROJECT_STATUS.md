# PROJECT STATUS — Yellow Club Mini App

_Последнее обновление: 2026-07-12 (v7.0.84)_

---

## Что сделано

### v7.0.84 — Fix: фильтрация меню питания по ребёнку, смене и филиалу (текущая)

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
