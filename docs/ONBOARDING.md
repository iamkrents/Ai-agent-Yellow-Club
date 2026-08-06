# Onboarding второго разработчика

Этот документ — стартовая точка для нового разработчика, подключающегося к репозиторию Yellow Club Agent со своим Claude Code.

## 1. Необходимые программы

- **Git**
- **Python 3.12** (проект таргетирует 3.12; см. `start_windows.bat`)
- **VS Code** (или другой редактор на ваш выбор)
- **Claude Code**
- **Node.js** — только для `node --check` и фронтенд-tooling, если он понадобится задаче; отдельного сборочного шага для `miniapp/` в проекте нет (vanilla JS без бандлера)

## 2. Как получить доступ к GitHub

Доступ к репозиторию `iamkrents/Ai-agent-Yellow-Club` выдаёт владелец проекта. Обратитесь к владельцу за приглашением в репозиторий (или за доступом через организацию, если применимо). Учётные данные, ссылки-приглашения и токены доступа передаются владельцем лично, не через документацию.

## 3. Как клонировать репозиторий

```bash
git clone <repository-url>
cd yellow_club_agent
git fetch origin
```

## 4. Как создать worktree для задачи

Каждая задача выполняется в отдельном `git worktree` от актуального `origin/main` — это позволяет вам и другому разработчику работать параллельно, не мешая друг другу.

```bash
git fetch origin
git worktree add ../yellow_club_agent_<task> \
  -b feat/<task> origin/main
```

Замените `<task>` на короткое имя задачи (например, `feat/schedule-ui-fix`, `chore/docs-update`). Тип префикса ветки — по смыслу задачи: `feat/`, `fix/`, `chore/`, `test/`, `integration/`.

## 5. Как открыть worktree в VS Code

```bash
code ../yellow_club_agent_<task>
```

Или через меню VS Code: **File → Open Folder…** и выбрать папку нового worktree.

## 6. Как запустить Claude Code в этом worktree

Откройте терминал внутри папки worktree и запустите:

```bash
claude --continue
```

Если задаче нужен доступ к соседней Figma-папке проекта (макеты дизайна), добавьте её отдельным флагом:

```bash
claude --continue --add-dir "<path-to-figma-project>"
```

Figma-папка **не является частью этого репозитория** и подключается только по отдельной задаче, где это явно требуется — не подключайте её «на всякий случай».

## 7. Что читать в первую очередь

В таком порядке:

1. [`CLAUDE.md`](../CLAUDE.md) — общие правила проекта, архитектура, conventions, хрупкие места.
2. [`docs/WORKFLOW.md`](WORKFLOW.md) — процесс работы над задачей от начала до merge.
3. [`docs/ACTIVE_WORK.md`](ACTIVE_WORK.md) — кто сейчас что делает и какие области заняты.
4. Ваш точный task prompt — конкретное задание с разрешёнными и запрещёнными файлами.

## 8. Как создать локальное окружение без production secrets

```bash
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Скопируйте `.env.example` (если он есть в репозитории на момент клонирования) в `.env` и заполните только тем, что нужно для вашей задачи — тестовым `TELEGRAM_BOT_TOKEN`, локальным `DB_PATH` и т.д. Никогда не используйте production-токены, production `MOYKLASS_API_KEY` или production `.env` локально. Для локальной разработки Mini App без реального Telegram используйте `WEB_APP_DEV_MODE=true` — это включает вход через `?dev_user_id=<id>` вместо Telegram `initData`.

Локальная БД — файл `storage/messages.db`, создаётся автоматически при первом запуске, не является production-БД и не коммитится в git.

## 9. Как запускать targeted tests

```bash
python -m unittest tests.test_<имя_файла> -v
```

Перед коммитом также обязательны:

```bash
node --check miniapp/app.js
python -m py_compile <изменённые .py файлы>
git diff --check
```

Для UI-изменений — реальная проверка рендера (Playwright или браузер), а не только строковые/CSS unittest-проверки.

## 10. Как подготовить commit и push

1. Просмотрите `git diff` построчно.
2. Добавьте только конкретные файлы задачи (никогда `git add .` / `git add -A`).
3. Commit с понятным сообщением по смыслу изменения.
4. `git push -u origin <ваша-ветка>`.
5. Проверьте `git rev-parse HEAD` и `git rev-parse origin/<ваша-ветка>` совпадают.

## 11. Как передать отчёт на проверку

Финальный отчёт по задаче должен соответствовать формату из [`CLAUDE.md`, раздел 12](../CLAUDE.md#12-формат-финального-отчёта-claude): что реализовано, impact map, изменённые файлы, migrations, tests, реальные результаты, failure modes, commit hash, push, git status, подтверждение отсутствия production deploy, известные ограничения. Отчёт передаётся координатору задачи (владельцу/GPT-координатору) для review перед integration/merge.

## 12. Production

Deploy на production и доступ к production-серверу выполняет **только владелец проекта**. Разработчики не подключаются к production, не выполняют deploy самостоятельно и не запрашивают эти действия у Claude Code без отдельного явного разрешения владельца.
