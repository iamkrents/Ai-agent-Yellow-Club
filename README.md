# Yellow Club Agent v3 stable

Модульная версия агента Yellow Club. Код разделён по файлам, чтобы правки одной функции не ломали весь бот.

## Что умеет

- Работает в личке и Telegram-группах.
- Smart-режим: отвечает на обычные рабочие вопросы без команд.
- Сохраняет историю сообщений в `storage/messages.db`.
- Использует базу знаний из папки `data`.
- Использует Ollama `qwen2.5:3b`.
- Хранит подтверждённые факты через `/remember`.
- Ищет по истории через `/search_history`.
- Ведёт внутренние заявки на пробное через trial_manager, но не пишет пользователю про "черновики".
- Актуальная цена: 4 занятия - 239 BYN.

## Первый запуск

1. Распаковать архив в новую папку.
2. Скопировать `.env.example` в `.env`.
3. Заполнить `TELEGRAM_BOT_TOKEN` и `ADMIN_IDS`.
4. Для первого теста оставить:

```env
ALLOWED_GROUP_IDS=0
RESPONSE_MODE=smart
```

5. Запустить `start_windows.bat`.
6. В консоли должен быть маркер:

```text
YELLOW_CLUB_AGENT_V3_STABLE_MODULAR_239_2026_06_13
```

## Проверка в группе

```text
/ping@yellowclubagent_bot
Какая актуальная стоимость обучения?
на занятие пришёл 1 ученик
Хочу изучать Roblox
```

## Команды

```text
/admin - админка в личке
/ping - проверка связи
/chatid - показать ID чата и темы
/ask вопрос - вопрос агенту
/remember факт - сохранить подтверждённый факт, только админ
/facts - список фактов
/forget_fact ID - удалить факт, только админ
/search_history запрос - поиск по истории
/trials - открытые заявки, только админ
/trial_start текст - создать/дополнить заявку, только админ
/trial_info текст - добавить данные к заявке, только админ
/trial_done ID - закрыть заявку, только админ
```

## Важно про токен

Не отправляй скрины `.env` и консоли, где виден токен Telegram. Если токен засветился, перевыпусти его в BotFather командой `/revoke`.

## Структура

```text
bot.py              - запуск Telegram
handlers.py         - обработка сообщений и команд
agent_core.py       - сбор ответа из базы, фактов, истории и Ollama
rules.py            - строгие правила и быстрые ответы
trial_manager.py    - логика заявок на пробное
storage.py          - SQLite: история, факты, заявки
knowledge_base.py   - чтение и поиск по data/*.md
llm.py              - Ollama
admin_panel.py      - админка
config.py           - настройки .env
```

## Notion Sync

Агент умеет забирать страницы Notion в Markdown и добавлять их в локальную базу знаний.

### Рекомендуемая структура

Чтобы не выдавать доступ к каждой странице отдельно, сделайте одну корневую страницу, например:

```text
Yellow Club KB
├─ Регламенты
├─ Программы занятий
├─ Шаблоны отчётов
└─ Базы данных Notion
```

Добавьте integration через `Connections` / `Add connections` именно к этой корневой странице. При `NOTION_SYNC_RECURSIVE=true` агент сам найдёт вложенные страницы и вложенные базы данных.

Если нужные страницы лежат в разных местах Notion, есть два варианта:

1. Перенести их внутрь одной корневой страницы `Yellow Club KB`.
2. Указать несколько корневых страниц/баз в `.env` через запятую.

### Настройка

1. Создайте Internal Integration в Notion Developer Portal.
2. Включите capability `Read content`.
3. Откройте корневую страницу или базу Notion и добавьте integration через `Connections` / `Add connections`.
4. В `.env` заполните:

```env
NOTION_TOKEN=secret_xxxxxxxxxxxxxxxxx
NOTION_PAGE_IDS=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_DATABASE_IDS=
NOTION_SYNC_DIR=data/notion
NOTION_SYNC_RECURSIVE=true
NOTION_RECURSIVE_MAX_DEPTH=6
NOTION_SYNC_CLEAN=false
```

Можно указывать ID страниц из URL Notion целиком или только UUID.

### Важное про сохранение файлов

Обычная команда `/notion_sync` больше не удаляет старые `.md` файлы. Она обновляет файл той же страницы по стабильному имени вида:

```text
название_страницы__32ca39a480c9.md
```

Так данные из предыдущих страниц не пропадают, даже если вы временно поменяли `NOTION_PAGE_IDS`.

Полную очистку папки `data/notion` используйте только тогда, когда нужно заново собрать базу без старых файлов:

```text
/notion_sync_clean
```

### Команды

```text
/notion_status - проверить настройки и доступ к Notion API
/notion_sync - скачать/обновить страницы в data/notion/*.md и перезагрузить базу знаний
/notion_sync_clean - очистить data/notion/*.md, затем заново скачать Notion и перезагрузить базу
/notion_files - показать сохранённые Notion-файлы
/kb_reload - вручную перезагрузить локальную базу знаний
```

После `/notion_sync` агент начинает использовать материалы Notion при обычных ответах.
