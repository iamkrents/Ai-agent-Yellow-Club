from __future__ import annotations

import re
import html
from dataclasses import dataclass
from typing import Optional

from knowledge_base import KnowledgeBase, KBFileMatch, clean_kb_output, strip_notion_noise
from llm import OllamaClient
from rules import postprocess_answer, strict_response
from storage import Storage
from trial_manager import make_trial_public_reply
from report_manager import (
    REPORT_GUIDE,
    EXAMPLE_REPORT,
    clean_report_output,
    has_enough_report_details,
    is_parent_report_request,
    report_need_details_message,
    strip_report_command,
    resolve_lesson_from_request,
    build_report_from_lesson,
    lesson_hint_message,
    report_variant_from_instruction,
)
from utils import trim, normalize
from query_tools import (
    build_query_profile,
    canonicalize_user_text,
    extract_course_keys,
    extract_lesson_number,
    query_has_lesson_intent,
    query_has_report_intent,
    query_has_regulation_intent,
    query_has_manager_reply_intent,
)


SYSTEM_PROMPT = """
Ты внутренний AI-помощник Yellow Club для сотрудников компании.
Отвечай по-русски, спокойно, понятно и по делу.
Используй базу знаний, подтверждённые факты, сохранённые рабочие решения и историю чата.
Не выдумывай цены, расписание, адреса, имена, телефоны и решения.
Актуальные факты: 4 занятия - 239 BYN, пробное бесплатно, адреса Кульман 1/1 и Мстиславца 6, максимум 6 учеников, возраст 7-17 лет.
Если вопрос про клиента, дай готовую формулировку или порядок действий.
Если данных недостаточно, честно скажи, что нужно уточнить у ответственного.
Мой Класс упоминай только для вопросов про конкретного ученика, расписание, посещаемость, темы отработки или CRM-данные. Не упоминай Мой Класс в общих вопросах про программу, инструменты, нейросети, цены и адреса.
Не пиши технические фразы: "сохранил черновик", "записал в базу", "обновил заявку".
Для заявок на пробное отвечай как помощник сотруднику: что уточнить у родителя и что делать дальше. Не рассказывай о внутренних черновиках.
""".strip()


@dataclass
class AnswerContext:
    chat_id: int
    thread_id: Optional[int]
    user_text: str


class AgentCore:
    def __init__(self, kb: KnowledgeBase, storage: Storage, llm: OllamaClient, kb_top_k: int = 6, kb_max_context_chars: int = 9000):
        self.kb = kb
        self.storage = storage
        self.llm = llm
        self.kb_top_k = kb_top_k
        self.kb_max_context_chars = kb_max_context_chars

    def build_answer(self, ctx: AnswerContext) -> str:
        strict = strict_response(ctx.user_text)
        if strict:
            return postprocess_answer(strict)

        fact_answer = self._answer_from_confirmed_facts(ctx.user_text)
        if fact_answer:
            return postprocess_answer(fact_answer)

        trial_reply = make_trial_public_reply(ctx.user_text)
        if trial_reply:
            # Trial/lead-processing answers must stay stable and operational.
            # A small local model sometimes adds meta text, separators and placeholders,
            # so we do not rewrite these answers through Ollama.
            return postprocess_answer(trial_reply)

        if is_parent_report_request(ctx.user_text):
            return self.build_parent_report(ctx.user_text)

        if self._is_lesson_material_request(ctx.user_text):
            material_answer = self.build_lesson_material_answer(ctx.user_text)
            if material_answer:
                return material_answer

        return self.build_smart_answer(ctx, include_sources=False)

    def _canonical_user_query(self, text: str) -> str:
        return canonicalize_user_text(text or "")

    def _is_lesson_material_request(self, text: str) -> bool:
        if query_has_report_intent(text) or query_has_regulation_intent(text) or query_has_manager_reply_intent(text):
            return False
        return query_has_lesson_intent(text)

    def build_lesson_material_answer(self, user_text: str) -> str:
        query = (user_text or "").strip()
        if not query:
            return (
                "Напишите, какой материал нужен.\n\n"
                "Примеры:\n"
                "/lesson_material Photoshop 3 тема Фигуры\n"
                "/lesson_material Python 4 занятие\n"
                "/lesson_material Blender 2 тема"
            )
        match = self.kb.find_lesson_material(query, max_chars=28000)
        if not match:
            results = self.kb.search_files(query, top_k=5, max_chars_per_file=700)
            if results:
                lines = [
                    "Точный материал занятия не нашёл. Нашёл похожие страницы:",
                    "",
                ]
                for idx, item in enumerate(results, start=1):
                    lines.append(f"{idx}. {item.title}")
                    if item.notion_path:
                        lines.append(f"Путь: {item.notion_path}")
                    lines.append(f"Источник: {item.source}")
                    if item.notion_url:
                        lines.append(f"Notion: {item.notion_url}")
                    lines.append("")
                lines.append("Уточните запрос: курс + номер темы + название темы. Например: Photoshop 3 тема Фигуры.")
                return "\n".join(lines).strip()
            return "Не нашёл материал занятия в базе. Проверьте, что нужная страница есть в Notion и выполнена /notion_sync."

        return self._format_lesson_material_summary(match, query)

    def _format_lesson_material_summary(self, match, query: str) -> str:
        title = (match.title or "Материал занятия").strip()
        text = clean_kb_output(match.text)
        text = self._remove_duplicate_title(text, title)
        text = self._prepare_lesson_text_for_summary(text)

        sections = self._extract_lesson_sections(text)
        goal = self._short_section(sections.get("goal") or "", max_lines=2, max_chars=320)
        tasks = self._short_section(sections.get("tasks") or "", max_lines=5, max_chars=560)
        result = self._short_section(sections.get("result") or "", max_lines=2, max_chars=320)
        plan = self._short_section(sections.get("plan") or "", max_lines=6, max_chars=620)
        if not plan:
            plan = self._extract_lesson_outline(text, max_items=6)

        video = self._extract_first_video_link(text)
        summary = self._build_lesson_summary_text(text, sections, max_lines=5, max_chars=650)
        display_path = self._format_display_notion_path(match.notion_path)

        lines: list[str] = [
            "📘 Материал занятия",
            "",
            f"**{title}**",
        ]
        if display_path:
            lines.append(f"📍 {display_path}")
        if match.notion_url:
            lines.extend(["", f"🔗 Полная версия в Notion:\n{match.notion_url}"])
        else:
            lines.extend(["", "🔗 Ссылка на Notion пока не сохранена. Выполните /notion_sync после обновления файлов."])

        if goal:
            lines.extend(["", "🎯 Цель", goal])
        if tasks:
            lines.extend(["", "✅ Ключевые задачи", tasks])
        if result:
            lines.extend(["", "🏁 Что должно получиться", result])
        if plan:
            lines.extend(["", "🧭 Короткий план", plan])
        if video:
            lines.extend(["", f"🎬 Видео:\n{video}"])
        if summary:
            lines.extend(["", "🧠 Кратко по содержанию", summary])

        lines.extend([
            "",
            "Полный сценарий, картинки и вложения - на странице Notion по ссылке выше.",
        ])
        return "\n".join(lines).strip()

    def _format_display_notion_path(self, path: str) -> str:
        path = (path or "").strip()
        if not path:
            return ""
        parts = [p.strip() for p in path.split("/") if p.strip()]
        cleaned: list[str] = []
        for idx, part in enumerate(parts):
            normalized = part.lower().replace("-", "").replace("_", "")
            # Old sync versions sometimes wrote the root page as a short Notion id.
            if idx == 0 and re.fullmatch(r"[0-9a-f]{6,32}", normalized):
                continue
            cleaned.append(part)
        return " / ".join(cleaned)

    def _extract_first_video_link(self, text: str) -> str:
        # Notion markdown may store a video as: Видео: https://...
        match = re.search(r"(?:Видео|Video)\s*:\s*(https?://\S+)", text or "", flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().rstrip(").,;")
        match = re.search(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/\S+", text or "", flags=re.IGNORECASE)
        return match.group(0).strip().rstrip(").,;") if match else ""

    def _remove_duplicate_title(self, text: str, title: str) -> str:
        lines = text.splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines:
            first = lines[0].strip().lstrip("# ").strip()
            if first.lower().replace("ё", "е") == title.lower().replace("ё", "е"):
                lines = lines[1:]
        return "\n".join(lines).strip()

    def _prepare_lesson_text_for_summary(self, text: str) -> str:
        text = strip_notion_noise(text, keep_links=True)
        # Metadata is shown separately.
        text = re.sub(r"^Путь:\s*.+$", "", text, flags=re.MULTILINE)
        text = re.sub(r"^Запрос:\s*.+$", "", text, flags=re.MULTILINE)
        text = re.sub(r"^Источник:\s*.+$", "", text, flags=re.MULTILINE)
        text = re.sub(r"^Notion page ID:\s*.+$", "", text, flags=re.MULTILINE)
        text = re.sub(r"^Notion URL:\s*.+$", "", text, flags=re.MULTILINE)
        # Sometimes Notion Markdown glues a bold heading and its content into one line after cleaning.
        # Split such lines so sections like "Ожидаемый результат урока" are detected correctly.
        text = re.sub(
            r"(?i)(Цель|Задачи|Ожидаемый результат(?: урока| занятия)?|Результат урока|Результат занятия|План урока|План занятия)(?=[А-ЯЁA-Z0-9])",
            r"\1\n",
            text,
        )
        # Remove very long lines that are usually signed image/file links.
        kept: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            low = stripped.lower().replace("ё", "е")
            if len(stripped) > 240 and ("http://" in stripped or "https://" in stripped):
                continue
            if stripped.startswith("!"):
                continue
            if any(bad in low for bad in ["prod-files-secure", "amazonaws", "x-amz-", "discussion-urls"]):
                continue
            kept.append(line)
        text = "\n".join(kept)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


    def _extract_lesson_sections(self, text: str) -> dict[str, str]:
        aliases = {
            "goal": ["цель", "цели"],
            "tasks": ["задачи", "задача"],
            "result": ["ожидаемый результат", "результат урока", "результат занятия", "итог урока", "итог занятия"],
            "plan": ["план урока", "план занятия", "структура урока", "ход урока"],
        }
        heading_to_key: dict[str, str] = {}
        for key, names in aliases.items():
            for name in names:
                heading_to_key[name] = key

        sections: dict[str, list[str]] = {"intro": []}
        current = "intro"
        for raw in text.splitlines():
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped:
                if current in sections and sections[current] and sections[current][-1] != "":
                    sections[current].append("")
                continue
            if self._is_service_lesson_line(stripped):
                if current in {"goal", "tasks", "result", "plan"}:
                    current = "other"
                    sections.setdefault(current, [])
                continue
            heading = self._normalize_lesson_heading(stripped)
            matched_key = None
            for heading_name, key_name in heading_to_key.items():
                if heading == heading_name or heading.startswith(heading_name + " "):
                    matched_key = key_name
                    break
            if matched_key:
                current = matched_key
                sections.setdefault(current, [])
                continue
            # Stop dedicated sections at the next strong/service heading.
            if self._is_any_heading(stripped) and current in {"goal", "tasks", "result", "plan"}:
                current = "other"
                sections.setdefault(current, [])
                continue
            sections.setdefault(current, []).append(line)
        return {k: "\n".join(v).strip() for k, v in sections.items() if "\n".join(v).strip()}


    def _normalize_lesson_heading(self, line: str) -> str:
        line = line.strip()
        line = re.sub(r"^#{1,6}\s*", "", line)
        # Notion pages often use emoji headings: 🎯 Цель, 🛠️ Задачи, ✅ Ожидаемый результат.
        # Remove leading symbols but keep the human heading text.
        line = re.sub(r"^[^A-Za-zА-Яа-я0-9]+", "", line)
        line = line.strip("* _:-—")
        line = re.sub(r"\s+", " ", line)
        return line.lower().replace("ё", "е")

    def _is_any_heading(self, line: str) -> bool:
        stripped = line.strip()
        if re.match(r"^#{1,6}\s+", stripped):
            return True
        if stripped.startswith("**") and stripped.endswith("**") and len(stripped) <= 100:
            return True
        normalized = self._normalize_lesson_heading(stripped)
        common = [
            "цель", "задачи", "ожидаемый результат", "результат урока", "результат занятия",
            "план урока", "план занятия", "структура урока", "ход урока",
            "видеоурок", "теория", "практика", "фигуры", "логотип", "итог", "рефлексия",
            "материалы", "яндекс", "yandex", "сообщение для родителей", "сообщение для родителей в чаты",
            "домашнее задание", "дополнительно", "ссылки", "файлы", "вложения",
        ]
        return any(normalized == x or normalized.startswith(x + " ") for x in common)


    def _short_section(self, text: str, *, max_lines: int, max_chars: int) -> str:
        if not text:
            return ""
        lines: list[str] = []
        seen: set[str] = set()
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            line = self._clean_lesson_line(line)
            if not line or self._is_service_lesson_line(line):
                continue
            if line in seen:
                continue
            seen.add(line)
            if len(line) > 170:
                line = line[:167].rstrip() + "..."
            lines.append(line)
            if len(lines) >= max_lines:
                break
        out = "\n".join(lines).strip()
        if len(out) > max_chars:
            out = out[: max_chars - 3].rstrip() + "..."
        return out


    def _extract_lesson_outline(self, text: str, *, max_items: int = 8) -> str:
        items: list[str] = []
        for raw in text.splitlines():
            stripped = raw.strip()
            if not re.match(r"^#{1,6}\s+", stripped):
                continue
            title = self._clean_lesson_line(stripped)
            norm = title.lower().replace("ё", "е")
            if not title or norm in {"цель", "задачи", "ожидаемый результат", "план урока", "план занятия"}:
                continue
            if len(title) > 90:
                title = title[:87].rstrip() + "..."
            if title not in items:
                items.append(title)
            if len(items) >= max_items:
                break
        if not items:
            return ""
        return "\n".join(f"{idx}. {item}" for idx, item in enumerate(items, start=1))

    def _build_lesson_summary_text(self, text: str, sections: dict[str, str], *, max_lines: int, max_chars: int) -> str:
        skip_blocks = [sections.get("goal", ""), sections.get("tasks", ""), sections.get("result", ""), sections.get("plan", "")]
        source = text
        for block in skip_blocks:
            if block:
                source = source.replace(block, "")
        lines: list[str] = []
        seen: set[str] = set()
        for raw in source.splitlines():
            line = self._clean_lesson_line(raw.strip())
            if not line:
                continue
            if self._is_any_heading(line) or self._is_service_lesson_line(line):
                continue
            low = line.lower().replace("ё", "е")
            if any(bad in low for bad in ["x-amz-", "prod-files-secure", "amazonaws", "discussion-urls"]):
                continue
            if "http://" in low or "https://" in low:
                continue
            if line in seen:
                continue
            seen.add(line)
            # Prefer meaningful theory/practice lines, skip one-word answers when possible.
            if len(line) < 18 and not line.startswith(("-", "•")):
                continue
            if len(line) > 185:
                line = line[:182].rstrip() + "..."
            if not line.startswith(("-", "•", "1.", "2.", "3.", "4.", "5.", "6.")):
                line = "- " + line
            lines.append(line)
            if len(lines) >= max_lines:
                break
        out = "\n".join(lines).strip()
        if len(out) > max_chars:
            out = out[: max_chars - 3].rstrip() + "..."
        return out


    def _clean_lesson_line(self, line: str) -> str:
        line = re.sub(r"^[-•]\s*", "- ", line.strip())
        line = re.sub(r"^#{1,6}\s*", "", line)
        # If the line is an emoji heading, keep the heading text without emoji.
        line = re.sub(r"^[^A-Za-zА-Яа-я0-9#\-/]+(?=[A-Za-zА-Яа-я0-9])", "", line)
        line = line.strip("*_ ")
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        return line

    def _is_service_lesson_line(self, line: str) -> bool:
        plain = self._clean_lesson_line(line) if "<" in line or line.strip().startswith("#") else line.strip()
        normalized = plain.lower().replace("ё", "е").strip(" -*_:")
        if not normalized:
            return True
        if any(bad in normalized for bad in ["x-amz-", "prod-files-secure", "amazonaws", "discussion-urls"]):
            return True
        if normalized in {
            "материалы", "yandex", "яндекс", "сообщение для родителей", "сообщение для родителей в чаты",
            "empty-block", "column", "columns", "table_of_contents", "файлы", "вложения", "ссылки",
            "python", "пайтон", "питон", "c", "си", "cpp", "c++", "javascript", "js", "html", "css", "java", "kotlin",
        }:
            return True
        if normalized.startswith(("материалы ", "yandex ", "яндекс ", "сообщение для родителей")):
            return True
        return False


    def build_teacher_checklist_menu(self) -> str:
        return (
            "✅ Чек-лист преподавателя\n\n"
            "Выберите ситуацию кнопкой ниже.\n\n"
            "🟢 Перед занятием - подготовка темы, материалов и класса.\n"
            "🔵 Проведение занятия - структура урока и контроль качества.\n"
            "🟣 После занятия - МойКласс, отчёт, работы учеников, порядок.\n"
            "🟡 Пробное / отработка - отдельные правила для нестандартных форматов.\n"
            "🔴 Нестандартная ситуация - один ученик, проблема с техникой, замена, неготовность."
        )

    def build_teacher_checklist(self, section: str) -> str:
        key = normalize(section)
        if "перед" in key:
            return "\n".join([
                "🟢 Чек-лист перед занятием",
                "",
                "1. Проверить расписание и группу в МойКласс.",
                "2. Проверить тему занятия и тему следующего занятия.",
                "3. Открыть материал занятия в Notion.",
                "4. Посмотреть видео/инструкцию к теме, если они есть.",
                "5. Выполнить упражнение или итоговую работу самому.",
                "6. Понять, что дети должны сделать к концу занятия.",
                "7. Проверить, что нужные программы, файлы и доступы работают.",
                "8. Прийти за 10-15 минут до начала.",
                "9. Подготовить рабочие места и открыть нужные материалы.",
                "",
                "Контрольный вопрос для себя:",
                "Что дети должны сделать, какой инструмент изучают и какой результат должен быть в конце?",
            ])
        if "после" in key:
            return "\n".join([
                "🟣 Чек-лист после занятия",
                "",
                "1. Отметить занятие в МойКласс как проведённое.",
                "2. Отметить присутствующих и отсутствующих учеников.",
                "3. Для отсутствующих указать комментарий в формате: Имя - отработка.",
                "4. Поставить тему следующего занятия.",
                "5. Если текущую тему не закончили - указать, где остановились.",
                "6. Сохранить работы учеников на Яндекс.Диск в правильную папку.",
                "7. Проверить названия файлов: работа должна быть подписана по ученику.",
                "8. Сформировать и отправить отчёт в родительский чат.",
                "9. Убрать рабочие места, очистить рабочие столы, привести кабинет в порядок.",
                "10. Если есть проблемы по технике/расходникам - сообщить ответственному.",
                "",
                "Минимум для закрытия занятия: МойКласс заполнен, отчёт отправлен, работы сохранены, кабинет в порядке.",
            ])
        if "провед" in key:
            return "\n".join([
                "🔵 Чек-лист проведения занятия",
                "",
                "1. Поздороваться с детьми и проверить готовность группы.",
                "2. Коротко поговорить с учениками и вернуть внимание к занятию.",
                "3. Повторить, что было на прошлом занятии.",
                "4. Объяснить цель и результат сегодняшнего занятия.",
                "5. Провести первую часть занятия по материалу Notion.",
                "6. Сделать перерыв по ситуации и возрасту группы.",
                "7. Провести вторую часть занятия и довести детей до результата.",
                "8. В конце повторить, что изучили и что получилось.",
                "9. Похвалить учеников и спокойно завершить занятие.",
                "",
                "Важно: если дети сильно не успевают, не перескакивать хаотично. Зафиксировать, где остановились, и указать это в МойКласс/комментарии.",
            ])
        if "проб" in key or "отработ" in key:
            return "\n".join([
                "🟡 Чек-лист: пробное занятие / отработка",
                "",
                "Пробное занятие:",
                "1. Прийти за 10-15 минут и подготовить место.",
                "2. Поздороваться с ребёнком и родителями.",
                "3. Спросить интересы ребёнка и опыт в IT/играх/творчестве.",
                "4. Провести занятие по инструкции и возрасту.",
                "5. Сохранить работу ребёнка.",
                "6. Дать родителям короткую обратную связь: что получилось, как ребёнок работал, какое направление подходит.",
                "7. Отметить пробное в МойКласс.",
                "",
                "Отработка:",
                "1. Проверить, какую тему пропустил ученик.",
                "2. Подготовить материал именно по пропущенной теме.",
                "3. После отработки зафиксировать результат и тему в МойКласс/комментарии.",
                "4. Если ученик не успел тему - указать, где остановился.",
            ])
        if "нестандарт" in key or "красн" in key or "ситуац" in key:
            return "\n".join([
                "🔴 Нестандартная ситуация",
                "",
                "Если пришёл только 1 ученик:",
                "1. Подождать 5 минут после начала занятия.",
                "2. Провести занятие 1 час.",
                "3. Предупредить ребёнка.",
                "4. Написать в чат преподавания:",
                "На занятии 14:00 YC2 присутствует только Вася - занятие будет длиться час. Ребенка предупредил. Сообщите, пожалуйста, родителям.",
                "",
                "Если преподаватель не готов к теме:",
                "1. Не скрывать проблему.",
                "2. Открыть материал в Notion и сообщить, какая помощь нужна.",
                "3. Если до занятия мало времени - срочно написать руководителю/методисту.",
                "",
                "Если проблема с техникой или расходниками:",
                "1. Зафиксировать, что не работает или чего не хватает.",
                "2. Сообщить ответственному в рабочий чат.",
                "3. Если это повлияло на занятие - указать в комментарии к занятию.",
                "",
                "Если занятие проводит заменяющий преподаватель:",
                "1. Проверить тему прошлого и следующего занятия в МойКласс.",
                "2. Открыть материал в Notion.",
                "3. После занятия подробно указать, что прошли и где остановились.",
            ])
        return self.build_teacher_checklist_menu()

    def build_teacher_regulation_answer(self, question: str, include_sources: bool = False) -> str:
        q = (question or "").strip()
        if not q:
            return (
                "Напишите вопрос по регламенту преподавателя.\n\n"
                "Примеры:\n"
                "Что делать, если пришёл один ученик?\n"
                "Как отметить занятие в МойКласс?\n"
                "Что писать после занятия?\n"
                "Как подготовиться к теме?"
            )
        prompt = (
            "Ответь строго по регламентам преподавателя Yellow Club. "
            "Нужен короткий рабочий ответ: что сделать по шагам, что отметить в МойКласс, "
            "кого предупредить и где нужна осторожность. Вопрос: " + q
        )
        return self.build_smart_answer(AnswerContext(chat_id=0, thread_id=None, user_text=prompt), include_sources=include_sources)

    def build_clean_kb_search_answer(self, query: str) -> str:
        q = (query or "").strip()
        if not q:
            return (
                "Напишите, что найти в базе.\n\n"
                "Примеры:\n"
                "Photoshop 3 тема Фигуры\n"
                "регламент после занятия\n"
                "пробное занятие"
            )
        matches = self.kb.search_files(q, top_k=6, max_chars_per_file=900)
        if not matches:
            return "В базе знаний ничего не найдено по этому запросу. Уточните курс, номер темы или название регламента."
        lines = ["🔎 Поиск в базе", "", f"Запрос: {q}", ""]
        for idx, m in enumerate(matches, start=1):
            lines.append(f"{idx}. {m.title}")
            if m.notion_path:
                lines.append(f"📍 {self._format_display_notion_path(m.notion_path)}")
            if m.notion_url:
                lines.append(f"🔗 {m.notion_url}")
            snippet = trim(clean_kb_output(m.text).replace("\n\n", "\n"), 420)
            if snippet:
                lines.append(snippet)
            lines.append("")
        return "\n".join(lines).strip()



    def build_smart_answer(self, ctx: AnswerContext, include_sources: bool = False) -> str:
        """Answer a staff question with intent-aware Notion/KB retrieval."""
        question = (ctx.user_text or "").strip()
        if not question:
            return "Напишите вопрос или задачу для агента."

        intent = self._classify_query_intent(question)

        if intent == "parent_report":
            return self.build_parent_report(question)
        if intent == "lesson_material":
            material = self.build_lesson_material_answer(question)
            if material:
                return material

        fact_answer = self._answer_from_confirmed_facts(question)
        if fact_answer and intent in {"commercial", "general"}:
            return postprocess_answer(fact_answer)

        sources = self._smart_sources(question, intent=intent, top_k=5, max_chars_per_file=2600)
        min_score = 24 if intent != "general" else 18
        if not sources or sources[0].score < min_score:
            return self._no_exact_knowledge_answer(question, intent, sources)

        source_context = self._format_smart_source_context(sources)
        facts = self.storage.list_facts(limit=20)
        fact_text = "\n".join(f"- {f['text']}" for f in facts)
        decisions = self.storage.search_decisions(question, chat_id=ctx.chat_id, thread_id=ctx.thread_id, limit=6)
        if not decisions and ctx.thread_id is not None:
            decisions = self.storage.search_decisions(question, chat_id=ctx.chat_id, thread_id=None, limit=6)
        decision_text = "\n".join(f"- #{d.get('id')}: {d.get('text')}" for d in decisions)

        format_rule = self._answer_format_rule(intent)
        prompt = f"""
Вопрос сотрудника:
{question}

Тип запроса: {intent}

Подтверждённые факты Yellow Club:
{fact_text or 'Нет сохранённых фактов.'}

Сохранённые рабочие решения:
{decision_text or 'Нет сохранённых решений по этому вопросу.'}

Найденные источники Notion / базы знаний:
{source_context}

Сформируй ответ для сотрудника Yellow Club.

Главные правила:
- используй только найденные источники, подтверждённые факты, данные из сообщения сотрудника и рабочие решения;
- если сотрудник уже написал, что реально сделали/что произошло, эти данные важнее общего сценария из Notion; Notion используй как контекст и проверку;
- если точного ответа нет, честно скажи, что информации недостаточно, и перечисли, что нужно уточнить;
- не выдумывай цены, расписание, адреса, имена, телефоны, оплаты, обещания клиентам и внутренние решения;
- если вопрос связан с конкретным учеником, расписанием, посещаемостью или оплатами, укажи, что точность нужно проверять в МойКласс;
- не пересказывай весь источник, дай рабочий ответ;
- не пиши технические фразы про базу данных, векторный поиск, chunks или промпт;
- не начинай с "Конечно" или "Вот".

Формат ответа:
{format_rule}
""".strip()
        result = self.llm.generate(prompt=prompt, system=SYSTEM_PROMPT)
        if result.ok and result.text:
            answer = postprocess_answer(result.text)
            if include_sources:
                answer = answer.rstrip() + self._format_sources_footer(sources)
            return answer

        fallback = self._fallback_answer_from_sources(question, intent, sources)
        if include_sources:
            fallback = fallback.rstrip() + self._format_sources_footer(sources)
        return postprocess_answer(fallback)


    def build_staff_chat_answer(
        self,
        ctx: AnswerContext,
        *,
        role_label: str = "",
        work_context: str = "",
        chat_history: str = "",
        include_sources: bool = False,
    ) -> str:
        """Answer a Mini App chat question with role/work context, but retrieve KB by the raw question."""
        question = (ctx.user_text or "").strip()
        if not question:
            return "Напишите вопрос для агента."

        intent = self._classify_query_intent(question)

        if intent == "parent_report":
            return self.build_parent_report(question)
        if intent == "lesson_material":
            material = self.build_lesson_material_answer(question)
            if material:
                return material

        fact_answer = self._answer_from_confirmed_facts(question)
        if fact_answer and intent in {"commercial", "general"}:
            return postprocess_answer(fact_answer)

        sources = self._smart_sources(question, intent=intent, top_k=6, max_chars_per_file=2600)
        min_score = 24 if intent != "general" else 18
        has_work_context = bool((work_context or "").strip())
        if (not sources or sources[0].score < min_score) and not has_work_context:
            return self._no_exact_knowledge_answer(question, intent, sources)

        source_context = self._format_smart_source_context(sources) if sources else "Нет точных источников. Используй только рабочий контекст и попроси уточнить данные, если их не хватает."
        facts = self.storage.list_facts(limit=20)
        fact_text = "\n".join(f"- {f['text']}" for f in facts)
        decisions = self.storage.search_decisions(question, chat_id=ctx.chat_id, thread_id=ctx.thread_id, limit=6)
        if not decisions and ctx.thread_id is not None:
            decisions = self.storage.search_decisions(question, chat_id=ctx.chat_id, thread_id=None, limit=6)
        decision_text = "\n".join(f"- #{d.get('id')}: {d.get('text')}" for d in decisions)

        format_rule = self._answer_format_rule(intent)
        prompt = f"""
Ты отвечаешь внутри Mini App Yellow Club на странице "Чат".
Роль сотрудника: {role_label or 'сотрудник'}.

Вопрос сотрудника:
{question}

Тип запроса: {intent}

Рабочий контекст из Mini App:
{work_context or 'Нет текущего контекста занятий/задач.'}

История последних сообщений в этом чате:
{chat_history or 'Истории нет.'}

Подтверждённые факты Yellow Club:
{fact_text or 'Нет сохранённых фактов.'}

Сохранённые рабочие решения:
{decision_text or 'Нет сохранённых решений по этому вопросу.'}

Найденные источники Notion / базы знаний:
{source_context}

Сформируй ответ для сотрудника Yellow Club.

Главные правила:
- отвечай как сильный рабочий помощник, а не как общий чат;
- не отвечай в стиле письма: без обращений "уважаемый коллега", без подписи, без "с уважением";
- сначала дай прямой ответ, потом конкретные шаги;
- используй рабочий контекст занятий и задач только как подсказку, не выдумывай отсутствующие данные;
- если вопрос просит количество учеников, посещений, оплат, занятий или статистику за месяц/период, не считай по видимым карточкам Mini App и не придумывай цифры; отвечай только по точным данным МойКласс, которые сервер передал отдельным результатом, иначе скажи, что нужно получить отчёт МойКласс;
- если вопрос про ближайшие занятия/задачи, опирайся на блок "Рабочий контекст из Mini App";
- если вопрос про рабочие возможности преподавателей, считай преподавателей Yellow Club универсальными: курс и тип занятия не ограничивают подбор, важны дата, время, филиал/онлайн, комментарий и конфликт с МойКласс;
- если сотрудник просит текст родителям, дай готовую формулировку без HTML и markdown-разметки;
- если данных для отчёта не хватает, задай короткие уточняющие вопросы: тема, что сделали, чему научились;
- если вопрос связан с конкретным учеником, расписанием, посещаемостью или оплатами, укажи, что точность нужно проверить в МойКласс;
- не выдумывай цены, расписание, адреса, имена, телефоны, оплаты, обещания клиентам и внутренние решения;
- не пиши технические фразы про базу данных, chunks, промпт, модель или поиск;
- не начинай с "Конечно", "Хорошо" или "Вот";
- если точного ответа нет, честно скажи, что нужно уточнить у старшего преподавателя, менеджера или ответственного.

Формат ответа:
{format_rule or 'Коротко, структурно, с конкретными действиями. Без длинной воды.'}
""".strip()
        result = self.llm.generate(prompt=prompt, system=SYSTEM_PROMPT)
        if result.ok and result.text:
            answer = postprocess_answer(result.text)
            if include_sources and sources:
                answer = answer.rstrip() + self._format_sources_footer(sources)
            return answer

        if sources:
            fallback = self._fallback_answer_from_sources(question, intent, sources)
            if include_sources:
                fallback = fallback.rstrip() + self._format_sources_footer(sources)
            return postprocess_answer(fallback)
        return "По текущему контексту не хватает точных данных. Уточните тему занятия, группу и что уже сделано, либо проверьте данные в МойКласс."

    def build_kb_debug_answer(self, query: str) -> str:
        query = (query or "").strip()
        if not query:
            return "Формат: /kb_debug запрос"
        intent = self._classify_query_intent(query)
        profile = build_query_profile(query)
        exact_lesson = self.kb.find_lesson_material(query, max_chars=700) if profile.has_lesson_intent else None
        sources = self._smart_sources(query, intent=intent, top_k=8, max_chars_per_file=900)
        lines = [
            "🧪 Диагностика поиска базы",
            "",
            f"Запрос: {query}",
            f"Нормализовано: {profile.normalized}",
            f"Тип запроса: {intent}",
            f"Курс: {', '.join(profile.course_keys) if profile.course_keys else 'не определён'}",
            f"Номер темы: {profile.lesson_number or 'не определён'}",
            f"Точная страница занятия: {exact_lesson.title if exact_lesson else 'не найдена'}",
            "",
        ]
        if not sources:
            lines.append("Подходящих источников не найдено.")
            return "\n".join(lines).strip()
        for idx, src in enumerate(sources, start=1):
            lines.append(f"{idx}. {src.title}")
            lines.append(f"score: {src.score}")
            if src.notion_path:
                lines.append(f"Путь: {self._format_display_notion_path(src.notion_path)}")
            lines.append(f"Источник: {src.source}")
            if src.notion_url:
                lines.append(f"Notion: {src.notion_url}")
            snippet = trim(clean_kb_output(src.text).replace("\n\n", "\n"), 500)
            if snippet:
                lines.append(snippet)
            lines.append("")
        return "\n".join(lines).strip()

    def _classify_query_intent(self, text: str) -> str:
        n = self._canonical_user_query(text)
        profile = build_query_profile(text)
        if is_parent_report_request(text) or profile.has_report_intent:
            return "parent_report"
        if profile.has_manager_reply_intent:
            return "manager_reply"
        if profile.has_regulation_intent or any(x in n for x in ["пришел один", "пришел 1", "пришёл один", "пришёл 1", "пробник не пришел", "пробник не пришёл", "отработка", "замена", "замещение", "мойкласс", "мой класс"]):
            return "regulation"
        if any(x in n for x in ["цена", "стоимость", "сколько стоит", "оплата", "абонемент", "пробное", "адрес", "кульман", "мстиславца", "сколько занятий", "возраст"]):
            return "commercial"
        if self._is_lesson_material_request(text):
            return "lesson_material"
        if any(x in n for x in ["методика", "провести занятие", "структура занятия", "подготовиться к занятию", "как объяснить", "как провести тему"]):
            return "teacher_method"
        return "general"

    def _smart_sources(self, query: str, *, intent: str, top_k: int = 5, max_chars_per_file: int = 2400) -> list[KBFileMatch]:
        raw_matches = self.kb.search_files(query, top_k=40, max_chars_per_file=max_chars_per_file)
        scored: list[KBFileMatch] = []

        # If the question points to a concrete lesson/topic, protect all smart modes
        # from using the first similar topic. The exact lesson page must be first.
        lesson_match: KBFileMatch | None = None
        if intent in {"lesson_material", "parent_report", "teacher_method", "general"} and query_has_lesson_intent(query):
            lesson_match = self.kb.find_lesson_material(query, max_chars=max_chars_per_file)
            if lesson_match:
                scored.append(KBFileMatch(
                    source=lesson_match.source,
                    title=lesson_match.title,
                    text=lesson_match.text,
                    score=lesson_match.score + 900,
                    notion_url=lesson_match.notion_url,
                    notion_path=lesson_match.notion_path,
                ))

        seen: set[str] = {lesson_match.source} if lesson_match else set()
        requested_number = self._requested_lesson_number(query)
        for m in raw_matches:
            if m.source in seen:
                continue
            # If a concrete lesson number is requested, do not mix in another lesson
            # number unless this is a regulation question where the lesson mention is incidental.
            if requested_number and intent in {"lesson_material", "parent_report", "teacher_method", "general"}:
                if not self._match_has_requested_lesson_number(m, requested_number):
                    target = normalize(" ".join([m.title or "", m.notion_path or "", m.source or ""]))
                    if any(w in target for w in ["тема", "занятие", "урок"]):
                        continue
            adjusted = m.score + self._intent_boost(m, intent, query)
            if adjusted <= 0:
                continue
            scored.append(
                KBFileMatch(
                    source=m.source,
                    title=m.title,
                    text=m.text,
                    score=adjusted,
                    notion_url=m.notion_url,
                    notion_path=m.notion_path,
                )
            )
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]

    def _intent_boost(self, match: KBFileMatch, intent: str, query: str) -> int:
        hay = normalize(" ".join([match.source, match.title, match.notion_path, match.text[:1600]]))
        boost = 0
        if match.source.startswith("notion/"):
            boost += 5
        if intent == "lesson_material":
            if any(x in hay for x in ["программа обучения", "темы", "тема", "занятие", "урок"]):
                boost += 35
            if any(x in hay for x in ["regulations", "регламент", "client_manager"]):
                boost -= 80
        elif intent == "parent_report":
            if any(x in hay for x in ["программа обучения", "темы", "тема", "занятие", "урок"]):
                boost += 35
            if any(x in hay for x in ["сообщение для родителей", "цель", "задачи", "результат"]):
                boost += 20
        elif intent == "regulation":
            if any(x in hay for x in ["regulations", "регламент", "правила", "преподаватель", "работа с преподавателями", "операцион", "отработк", "замен", "мой класс", "мойкласс"]):
                boost += 45
            if any(x in hay for x in ["программа обучения", "темы /", "тема -", "видеоурок"]):
                boost -= 20
        elif intent == "manager_reply":
            if any(x in hay for x in ["client_manager", "клиент", "родител", "менеджер", "продаж", "возраж", "коммер", "пробное", "стоимость"]):
                boost += 45
            if any(x in hay for x in ["программа обучения", "темы /", "видеоурок"]):
                boost -= 15
        elif intent == "commercial":
            if any(x in hay for x in ["current_facts", "цена", "стоимость", "byn", "пробное", "бесплат", "кульман", "мстиславца", "адрес", "форматы продукта", "продукт"]):
                boost += 50
            if any(x in hay for x in ["команда", "преподаватель:", "сотрудник"]):
                boost -= 20
        elif intent == "teacher_method":
            if any(x in hay for x in ["teacher", "преподаватель", "метод", "регламент занятия", "структура занятия", "пробное занятие", "подготовка"]):
                boost += 45
            if any(x in hay for x in ["client_manager", "продаж"]):
                boost -= 25
        else:
            if any(x in hay for x in ["current_facts", "регламент", "продукт", "программа обучения", "миссия", "цели"]):
                boost += 12
        return boost

    def _format_smart_source_context(self, sources: list[KBFileMatch]) -> str:
        parts: list[str] = []
        for idx, src in enumerate(sources, start=1):
            header = [f"Источник {idx}: {src.title}", f"Файл: {src.source}", f"score: {src.score}"]
            if src.notion_path:
                header.append(f"Путь Notion: {self._format_display_notion_path(src.notion_path)}")
            if src.notion_url:
                header.append(f"Notion URL: {src.notion_url}")
            body = trim(clean_kb_output(src.text), 2200)
            parts.append("\n".join(header) + "\n" + body)
        return "\n\n---\n\n".join(parts)

    def _answer_format_rule(self, intent: str) -> str:
        if intent == "regulation":
            return "Короткий ответ, затем блок 'Что сделать:' со списком шагов. Если есть исключения - укажи их отдельно."
        if intent == "manager_reply":
            return "Сначала 'Ответ клиенту:' - готовая формулировка. Потом 'Внутренне:' - что сотруднику проверить или сделать."
        if intent == "commercial":
            return "Коротко и точно. Цены/адреса/условия пиши только если они есть в подтверждённых фактах или источниках."
        if intent == "teacher_method":
            return "Дай практический ответ для преподавателя: как подготовиться, что сделать на занятии, что отметить после занятия."
        return "Ответь структурировано и по делу. Если это инструкция - дай шаги. Если это вопрос по данным - отдели точные данные от того, что нужно уточнить."

    def _format_sources_footer(self, sources: list[KBFileMatch]) -> str:
        if not sources:
            return ""
        lines = ["", "", "Источники:"]
        for src in sources[:3]:
            label = src.title or src.source
            if src.notion_url:
                lines.append(f"- {label}: {src.notion_url}")
            else:
                lines.append(f"- {label} ({src.source})")
        return "\n".join(lines)

    def _no_exact_knowledge_answer(self, question: str, intent: str, sources: list[KBFileMatch]) -> str:
        lines = ["Не нашёл точной информации в базе знаний по этому вопросу.", ""]
        if sources:
            lines.append("Похожие источники:")
            for idx, src in enumerate(sources[:3], start=1):
                lines.append(f"{idx}. {src.title}")
                if src.notion_path:
                    lines.append(f"   Путь: {self._format_display_notion_path(src.notion_path)}")
            lines.append("")
        if intent in {"commercial", "regulation", "manager_reply"}:
            lines.append("Для точного ответа лучше уточнить у ответственного или добавить подтверждённую информацию в Notion/память агента.")
        else:
            lines.append("Попробуйте уточнить запрос: направление, номер темы, название страницы или конкретную ситуацию.")
        return "\n".join(lines).strip()

    def _fallback_answer_from_sources(self, question: str, intent: str, sources: list[KBFileMatch]) -> str:
        lines = ["Нашёл в базе похожую информацию:", ""]
        for src in sources[:3]:
            lines.append(f"Источник: {src.title}")
            if src.notion_path:
                lines.append(f"Путь: {self._format_display_notion_path(src.notion_path)}")
            snippet = trim(clean_kb_output(src.text).replace("\n\n", "\n"), 900)
            if snippet:
                lines.append(snippet)
            lines.append("")
        lines.append("Если нужен точный рабочий ответ, уточните ситуацию одним сообщением.")
        return "\n".join(lines).strip()

    def _requested_lesson_number(self, text: str) -> str:
        return extract_lesson_number(text or "")

    def _match_has_requested_lesson_number(self, match: KBFileMatch, requested_number: str) -> bool:
        if not requested_number:
            return True
        # Human-readable fields only. Notion IDs and signed file links can contain random digits.
        target = self._canonical_user_query(" ".join([match.title or "", match.notion_path or ""]))
        patterns = [
            rf"(?:^|[^0-9]){re.escape(requested_number)}\s*(?:тема|темы|занятие|занятия|урок|урока)(?:[^0-9]|$)",
            rf"(?:тема|темы|занятие|занятия|урок|урока)\s*№?\s*{re.escape(requested_number)}(?:[^0-9]|$)",
            rf"(?:^|[^0-9]){re.escape(requested_number)}\s*[-–—:]",
            rf"^\s*{re.escape(requested_number)}\b",
        ]
        return any(re.search(pat, target) for pat in patterns)

    def _try_build_parent_report_from_notion(self, details: str, variant: str = "normal") -> str:
        details = (details or "").strip()
        if not details:
            return ""
        n = self._canonical_user_query(details)
        looks_like_lesson = query_has_lesson_intent(details) or bool(extract_course_keys(details) and self._requested_lesson_number(details))
        if not looks_like_lesson:
            return ""
        match = self.kb.find_lesson_material(details, max_chars=28000)
        if not match:
            return ""
        # Safety check: for requests like "GDevelop 9 тема" the report must not be
        # built from "1 тема" or another random page. If exact Notion page is not found,
        # build_parent_report will use the structured fallback or ask to clarify.
        requested_number = self._requested_lesson_number(details)
        if requested_number and not self._match_has_requested_lesson_number(match, requested_number):
            return ""

        teacher_actions = self._extract_teacher_report_actions(details)
        if teacher_actions:
            return self._format_parent_report_from_teacher_actions(match, teacher_actions, variant=variant)

        return self._format_parent_report_from_material(match, variant=variant)

    def _extract_teacher_report_actions(self, details: str) -> list[str]:
        """Extract concrete facts from a teacher's free-form report request.

        Example:
        "GDevelop 9 тема. Сегодня на занятии мы добавили джойстик,
        нарисовали кнопку прыжка, настроили управление и скрыли для компьютера."

        These teacher-provided facts must override generic Notion tasks. Notion is
        still used as context for the final learned phrase and topic verification.
        """
        raw = (details or "").strip()
        if not raw:
            return []

        # Prefer the part where the teacher describes the actual lesson result.
        low = raw.lower().replace("ё", "е")
        starts: list[int] = []
        for marker in [
            "сегодня на занятии", "на занятии мы", "сегодня мы", "сегодня ребята",
            "сегодня дети", "мы успели", "мы сделали", "дети сделали", "ребята сделали",
            "примерно сделали", "что сделали", "делали:", "сделали:", "прошли:",
        ]:
            idx = low.find(marker)
            if idx >= 0:
                starts.append(idx)
        if starts:
            raw = raw[min(starts):]
        else:
            # If the first sentence is only a course/topic locator, remove it.
            parts = re.split(r"(?<=[.!?])\s+", raw, maxsplit=1)
            if len(parts) == 2:
                first_norm = self._canonical_user_query(parts[0])
                if (extract_course_keys(first_norm) or self._requested_lesson_number(first_norm)) and len(parts[1]) >= 20:
                    raw = parts[1]

        # Remove common leading report/request phrases and lesson locator fragments.
        raw = re.sub(r"(?i)^\s*(напиши|сделай|составь|подготовь)\s+отч[её]т[^:.-]*[:.\-—]?\s*", "", raw).strip()
        raw = re.sub(r"(?i)^\s*(по\s+)?[a-zа-яё+ ]{2,35}\s+\d{1,2}\s*(?:тема|tema|урок|занятие)[:.\-—]?\s*", "", raw).strip()
        raw = re.sub(r"(?i)^\s*(?:фактически\s+)?(?:на\s+занятии\s+)?(?:что\s+)?(?:реально\s+)?(?:сделали|делали|прошли)\s*[:—-]\s*", "", raw).strip()

        prefixes = [
            r"^сегодня\s+на\s+занятии\s+мы\s+", r"^на\s+занятии\s+мы\s+",
            r"^сегодня\s+мы\s+", r"^сегодня\s+ребята\s+", r"^сегодня\s+дети\s+",
            r"^мы\s+успели\s+", r"^мы\s+сделали\s+", r"^дети\s+сделали\s+",
            r"^ребята\s+сделали\s+", r"^примерно\s+сделали\s+",
        ]
        for pat in prefixes:
            raw = re.sub(pat, "", raw, flags=re.IGNORECASE).strip()

        # Split by punctuation and by "и" only before likely past-tense action verbs.
        action_verbs = (
            "добавили", "нарисовали", "настроили", "скрыли", "открыли", "создали", "сделали",
            "закрепили", "изучили", "разобрали", "прошли", "познакомились", "научились",
            "потренировались", "поработали", "протестировали", "проверили", "исправили",
            "доработали", "адаптировали", "подготовили", "собрали", "разместили",
            "перенесли", "подключили", "оформили", "изменили", "попробовали",
        )
        text = raw.replace("\n", ". ")
        text = re.sub(r"\s+и\s+(?=(?:" + "|".join(map(re.escape, action_verbs)) + r")\b)", ". ", text, flags=re.IGNORECASE)
        chunks = re.split(r"[.;\n]+|,(?=\s*(?:" + "|".join(map(re.escape, action_verbs)) + r")\b)", text, flags=re.IGNORECASE)

        service_words = [
            "отчет", "отчёт", "родител", "gdevelop", "photoshop", "python", "blender", "roblox",
            "wordpress", "figma", "html", "css", "тема", "tema", "занятие", "урок",
        ]
        out: list[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            line = self._clean_lesson_line(chunk)
            line = line.strip(" -—:;,.\t")
            if not line:
                continue
            line = re.sub(r"(?i)^(сегодня\s+)?(на\s+занятии\s+)?(мы|ребята|дети)\s+", "", line).strip()
            line = line.strip(" -—:;,.\t")
            if len(line) < 5:
                continue
            n = self._canonical_user_query(line)
            # Drop pure locator fragments and low-value notes. Teacher details like
            # "Сделали" or "прошли пайтон" should not override the real Notion topic.
            generic_patterns = [
                r"^(?:сделали|делали|прошли|изучили|разобрали)$",
                r"^(?:прошли|изучили|разобрали)\s+(?:python|пайтон|питон|gdevelop|роблокс|blender|блендер|photoshop|фотошоп)$",
                r"^(?:сделали|делали)\s+(?:python|пайтон|питон|gdevelop|роблокс|blender|блендер|photoshop|фотошоп)$",
            ]
            if any(re.fullmatch(pat, n) for pat in generic_patterns):
                continue
            # Drop pure locator fragments, but keep real actions that mention the software.
            if not any(v in n for v in action_verbs):
                if any(w in n for w in service_words) and len(n.split()) <= 6:
                    continue
            if any(bad in n for bad in ["напиши отчет", "сделай отчет", "составь отчет"]):
                continue
            line = self._polish_teacher_report_action(line)
            key = self._canonical_user_query(line)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(line)
            if len(out) >= 8:
                break
        return out

    def _polish_teacher_report_action(self, line: str) -> str:
        line = (line or "").strip().strip(".;")
        if not line:
            return ""
        fixes = [
            (r"\bскрыли\s+для\s+компьютера\b", "скрыли элементы управления для компьютерной версии"),
            (r"\bна\s+телефоне\s+по\s+qr\b", "на телефоне через QR-код"),
            (r"\bqr\s*код\b", "QR-код"),
            (r"\bджойстик\b", "джойстик"),
        ]
        out = line
        for pat, repl in fixes:
            out = re.sub(pat, repl, out, flags=re.IGNORECASE)
        return out[:1].lower() + out[1:] if out else out

    def _report_escape(self, text: str) -> str:
        text = html.unescape((text or "").strip())
        text = re.sub(r"<[^>]+>", "", text)
        return re.sub(r"\s+", " ", text).strip()

    def _report_style_index(self, seed: str) -> int:
        seed = self._canonical_user_query(seed or "")
        if not seed:
            return 0
        return sum(ord(ch) for ch in seed) % 3

    def _normalize_report_item_for_parents(self, item: str) -> str:
        """Turn Notion/task language into a clean parent-facing action line."""
        text = " ".join((item or "").strip().strip(".;").split())
        if not text:
            return ""

        # Remove internal/meta wording that looks bad in parent chats.
        text = re.sub(r"(?i)^[-•\d.\s]+", "", text).strip()
        text = re.sub(r"(?i)^(задача|цель|план|результат)\s*[:—-]\s*", "", text).strip()
        text = re.sub(r"(?i)^ученик\s+", "ребята ", text).strip()
        text = re.sub(r"(?i)^ребенок\s+", "ребята ", text).strip()
        text = re.sub(r"(?i)^ребёнок\s+", "ребята ", text).strip()

        # Infinitive objectives from Notion -> past-tense actions.
        verb_fixes = [
            ("познакомиться", "познакомились"),
            ("научиться", "научились"),
            ("освоить", "освоили"),
            ("изучить", "изучили"),
            ("разобрать", "разобрали"),
            ("понять", "поняли"),
            ("создать", "создали"),
            ("собрать", "собрали"),
            ("добавить", "добавили"),
            ("настроить", "настроили"),
            ("нарисовать", "нарисовали"),
            ("сделать", "сделали"),
            ("закрепить", "закрепили"),
            ("применить", "применили"),
            ("протестировать", "протестировали"),
            ("проверить", "проверили"),
            ("скорректировать", "скорректировали"),
            ("доработать", "доработали"),
            ("адаптировать", "адаптировали"),
            ("использовать", "использовали"),
        ]
        low = text.lower().replace("ё", "е")
        for src, dst in verb_fixes:
            if low.startswith(src):
                text = dst + text[len(src):]
                break

        # Common raw Notion fragments.
        replacements = [
            (r"(?i)^ребята\s+сделал[аи]?\s+", "создали "),
            (r"(?i)^ребята\s+создал[аи]?\s+", "создали "),
            (r"(?i)^сделал[аи]?\s+", "создали "),
            (r"(?i)^каркас\s+модели", "создали каркас модели"),
            (r"(?i)^освоили\s+режим", "освоили режим"),
            (r"(?i)^освоили\s+инструмент", "поработали с инструментом"),
            (r"(?i)^инструментом\s+", "поработали с инструментом "),
            (r"(?i)^инструмент\s+", "поработали с инструментом "),
            (r"(?i)\bedit mode\b", "Edit Mode"),
            (r"(?i)\bsnap tool\b", "Snap Tool"),
            (r"(?i)\bloop cut\b", "Loop Cut"),
            (r"(?i)\bqr\s*код\b", "QR-код"),
            (r"(?i)\bчерез\s+qr\b", "через QR-код"),
            (r"(?i)\bскрыли\s+для\s+компьютера\b", "скрыли элементы управления для компьютерной версии"),
        ]
        for pat, repl in replacements:
            text = re.sub(pat, repl, text).strip()

        text = self._fix_parent_report_verb_chain(text)
        text = re.sub(r"\s+", " ", text).strip(" -—:;,.")
        return text[:1].lower() + text[1:] if text else text

    def _clean_practical_result_for_parents(self, result: str) -> str:
        text = self._clean_lesson_line(result or "")
        text = " ".join(text.strip().strip(".;").split())
        if not text:
            return ""
        low = text.lower().replace("ё", "е")
        if low in {"python", "пайтон", "питон", "c", "си", "cpp", "c++", "javascript", "js", "html", "css", "java", "kotlin"}:
            return ""

        # Keep only a human practical result, not an internal Notion phrase.
        text = re.sub(r"(?i)^ожидаемый\s+результат\s*[:—-]\s*", "", text).strip()
        text = re.sub(r"(?i)^результат\s+(?:урока|занятия)\s*[:—-]\s*", "", text).strip()
        text = re.sub(r"(?i)^в\s+конце\s+занятия\s+", "", text).strip()
        text = re.sub(r"(?i)^у\s+ребят\s+должен\s+получиться\s+", "", text).strip()
        text = re.sub(r"(?i)^должен\s+получиться\s+", "", text).strip()
        text = re.sub(r"(?i)^ученик\s+сделал\s+", "", text).strip()
        text = re.sub(r"(?i)^ученик\s+создал\s+", "", text).strip()
        text = re.sub(r"(?i)^ребята\s+сделали\s+", "", text).strip()
        text = re.sub(r"(?i)^ребята\s+создали\s+", "", text).strip()
        text = re.sub(r"(?i)^практический\s+результат\s*[:—-]\s*", "", text).strip()

        # If the line is still an action, make it a noun-like result.
        low = text.lower().replace("ё", "е")
        if low.startswith("собрали "):
            text = text[len("собрали "):].strip()
        if low.startswith("создали "):
            text = text[len("создали "):].strip()
        if low.startswith("сделали "):
            text = text[len("сделали "):].strip()
        text = re.sub(r"\s+", " ", text).strip(" -—:;,.")
        return text[:1].lower() + text[1:] if text else ""

    def _capitalize_course_words(self, text: str) -> str:
        text = text or ""
        replacements = {
            r"\bpython\b": "Python",
            r"\bjavascript\b": "JavaScript",
            r"\bjs\b": "JS",
            r"\bhtml\b": "HTML",
            r"\bcss\b": "CSS",
            r"\bcpp\b": "C++",
            r"\bc\b": "C",
            r"\bkotlin\b": "Kotlin",
            r"\btelegram\b": "Telegram",
            r"\broblox\b": "Roblox",
            r"\bblender\b": "Blender",
            r"\bphotoshop\b": "Photoshop",
            r"\bfigma\b": "Figma",
            r"\bgdevelop\b": "GDevelop",
        }
        out = text
        out = re.sub(r"(?i)\bc\s*\+\s*\+\b", "C++", out)
        for pattern, repl in replacements.items():
            out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
        return out

    def _report_natural_result_sentence(self, learned: str, *, variant: str = "normal") -> str:
        learned = " ".join((learned or "").strip().strip(".").split())
        if not learned:
            learned = "применять изученные инструменты на практике"
        if variant == "soft":
            return f"Благодаря такой последовательной работе ребята стали увереннее {learned}."
        if variant == "detailed":
            return f"Благодаря этому ребята не просто повторили тему, а потренировались {learned}."
        if variant == "alternate":
            return f"Главный результат занятия - ребята закрепили умение {learned}."
        return f"Благодаря этому ребята научились {learned}."

    def _format_parent_report_html(
        self,
        items: list[str],
        learned: str,
        *,
        variant: str = "normal",
        seed: str = "",
        lesson_title: str = "",
        extra: str = "",
        practical_result: str = "",
    ) -> str:
        variant = (variant or "normal").strip().lower()
        clean_items: list[str] = []
        seen = set()
        for item in items:
            item = self._normalize_report_item_for_parents(item)
            if not item:
                continue
            key = self._canonical_user_query(item)
            if not key or key in seen:
                continue
            seen.add(key)
            clean_items.append(item)

        if variant == "short":
            clean_items = clean_items[:4]
        elif variant == "detailed":
            clean_items = clean_items[:8]
        else:
            clean_items = clean_items[:6]

        if not clean_items:
            return ""

        lesson_title = (lesson_title or "").strip().strip(".")
        extra = (extra or "").strip().strip(".")
        practical_result = self._clean_practical_result_for_parents(practical_result)
        style = self._report_style_index(seed + variant)

        if variant == "short":
            intro_options = [
                "Сегодня на занятии ребята закрепили главное:",
                "Сегодня ребята поработали с ключевыми навыками:",
                "На занятии мы кратко закрепили важные действия:",
            ]
            result_labels = ["Итог:", "Главный результат:", "Что закрепили:"]
        elif variant == "detailed":
            intro_options = [
                "Сегодня на занятии ребята подробно разобрали тему и закрепили её на практике:",
                "На занятии мы пошагово поработали с темой и довели её до практического результата:",
                "Сегодня ребята не только разобрали теорию, но и применили её в работе:",
            ]
            result_labels = ["Итог и польза занятия:", "Что это дало ребятам:", "Главный результат занятия:"]
        elif variant == "soft":
            intro_options = [
                "Сегодня ребята спокойно и последовательно поработали с темой занятия:",
                "На занятии ребята аккуратно двигались по теме и закрепляли материал на практике:",
                "Сегодня мы в спокойном темпе разобрали важные элементы темы:",
            ]
            result_labels = ["Что получилось:", "Итог занятия:", "Чему это помогло:"]
        elif variant == "alternate":
            intro_options = [
                "Сегодня продолжили практическую работу и сделали несколько важных шагов:",
                "На занятии ребята продвинулись в теме и закрепили новые действия:",
                "Сегодня ребята поработали над проектом и отработали несколько важных навыков:",
            ]
            result_labels = ["Главный результат:", "Итог занятия:", "Что ребята закрепили:"]
        else:
            intro_options = [
                "Сегодня на занятии ребята:",
                "На сегодняшнем занятии мы:",
                "Сегодня ребята поработали с практической темой:",
            ]
            result_labels = ["Итог занятия:", "Что получилось в результате:", "Чему научились:"]

        intro = intro_options[style]
        result_label = result_labels[style]

        if lesson_title and variant in {"soft", "alternate"}:
            intro = f"Сегодня ребята поработали с темой «{lesson_title}»:"

        bullet_lines = "\n".join(f"• {self._report_escape(item)}" for item in clean_items)
        lines = [
            "👋 Здравствуйте, уважаемые родители!",
            "",
            f"📌 {self._report_escape(intro)}",
            bullet_lines,
            "",
            f"✨ {self._report_escape(result_label)}",
            self._report_escape(self._report_natural_result_sentence(learned, variant=variant)),
        ]
        if practical_result:
            lines.extend(["", f"🌿 Практический результат: {self._report_escape(practical_result)}."])
        if extra:
            lines.extend(["", f"🌱 {self._report_escape(extra)}."])
        return clean_report_output("\n".join(lines))

    def _format_parent_report_from_teacher_actions(
        self,
        match: KBFileMatch | None,
        teacher_actions: list[str],
        variant: str = "normal",
    ) -> str:
        actions = [self._adapt_task_for_parent_report(x) for x in teacher_actions if x]
        if variant == "short":
            actions = actions[:4]
        elif variant == "detailed":
            actions = actions[:8]
        else:
            actions = actions[:6]
        if not actions:
            return ""

        goal = ""
        result = ""
        title = ""
        if match:
            title = (match.title or "занятия").strip()
            text = clean_kb_output(match.text)
            text = self._remove_duplicate_title(text, title)
            text = self._prepare_lesson_text_for_summary(text)
            sections = self._extract_lesson_sections(text)
            goal = self._short_section(sections.get("goal") or "", max_lines=1, max_chars=220)
            result = self._short_section(sections.get("result") or "", max_lines=1, max_chars=240)

        learned = self._learned_phrase_from_teacher_actions(actions, goal=goal, result=result)
        extra = ""
        practical_result = self._clean_practical_result_for_parents(result)
        if variant == "detailed" and goal:
            extra = self._learned_phrase_from_material(goal=goal, result=result, tasks=actions)
            if extra and not extra.lower().startswith(("это", "такой", "также")):
                extra = "Также занятие помогло ребятам " + extra
        elif variant in {"soft", "alternate"} and practical_result:
            extra = "По итогу у ребят получилась понятная практическая работа"

        return self._format_parent_report_html(
            actions,
            learned,
            variant=variant,
            seed=" ".join(actions) + " " + title,
            lesson_title=title,
            extra=extra,
            practical_result=practical_result,
        )

    def _learned_phrase_from_teacher_actions(self, actions: list[str], *, goal: str = "", result: str = "") -> str:
        joined = self._canonical_user_query(" ".join(actions + [goal, result]))
        if any(x in joined for x in ["мобиль", "телефон", "qr", "джойстик", "адапт"]):
            return "адаптировать игру под мобильные устройства, настраивать сенсорное управление и проверять проект на реальном устройстве"
        if any(x in joined for x in ["фигура", "логотип", "икон", "прямоугольник", "эллипс", "photoshop"]):
            return "создавать аккуратные графические элементы из простых фигур и использовать их в иконках и логотипах"
        if any(x in joined for x in ["python", "услов", "цикл", "спис", "переменн", "функц"]):
            return "применять изученные конструкции Python для решения практических задач"
        if any(x in joined for x in ["blender", "модель", "сцена", "материал", "анимац"]):
            return "создавать и дорабатывать 3D-сцену, аккуратно работая с объектами и материалами"
        if goal:
            return self._learned_phrase_from_material(goal=goal, result=result, tasks=actions)
        return "применять изученные инструменты на практике и доводить работу до результата"

    def _format_parent_report_from_material(self, match: KBFileMatch, variant: str = "normal") -> str:
        title = (match.title or "занятия").strip()
        text = clean_kb_output(match.text)
        text = self._remove_duplicate_title(text, title)
        text = self._prepare_lesson_text_for_summary(text)
        sections = self._extract_lesson_sections(text)

        task_lines = self._section_lines_for_report(sections.get("tasks", ""), max_lines=7 if variant == "detailed" else 5)
        if not task_lines:
            task_lines = self._section_lines_for_report(sections.get("plan", ""), max_lines=6 if variant == "detailed" else 4)
        if not task_lines:
            summary = self._build_lesson_summary_text(text, sections, max_lines=6, max_chars=800)
            task_lines = self._section_lines_for_report(summary, max_lines=5)

        task_lines = [self._adapt_task_for_parent_report(x) for x in task_lines]
        if variant == "short":
            task_lines = task_lines[:4]
        elif variant == "detailed":
            task_lines = task_lines[:7]
        else:
            task_lines = task_lines[:5]

        goal = self._short_section(sections.get("goal") or "", max_lines=1, max_chars=220)
        result = self._short_section(sections.get("result") or "", max_lines=1, max_chars=240)
        learned = self._learned_phrase_from_material(goal=goal, result=result, tasks=task_lines)

        if not task_lines:
            return ""

        extra = ""
        practical_result = self._clean_practical_result_for_parents(result)
        if variant == "detailed" and goal:
            extra = "Такая работа помогает ребятам лучше понимать логику темы и увереннее применять её в проекте"
        elif variant in {"soft", "alternate"} and goal:
            extra = "Такая работа помогает ребятам спокойнее и увереннее применять изученный инструмент на практике"

        return self._format_parent_report_html(
            task_lines,
            learned,
            variant=variant,
            seed=title + " " + " ".join(task_lines),
            lesson_title=title,
            extra=extra,
            practical_result=practical_result,
        )

    def _section_lines_for_report(self, text: str, *, max_lines: int = 6) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in (text or "").splitlines():
            line = self._clean_lesson_line(raw)
            if not line or self._is_service_lesson_line(line) or self._is_any_heading(line):
                continue
            line = line.lstrip("-•0123456789. ").strip()
            if not line:
                continue
            low = line.lower().replace("ё", "е")
            if any(bad in low for bad in ["http", "yandex", "материалы", "сообщение для родителей", "домашнее задание"]):
                continue
            if line in seen:
                continue
            seen.add(line)
            out.append(line)
            if len(out) >= max_lines:
                break
        return out

    def _adapt_task_for_parent_report(self, line: str) -> str:
        line = (line or "").strip().strip(".;")
        if not line:
            return ""
        # Central normalizer: fixes Notion infinitives and dry teacher wording.
        line = self._normalize_report_item_for_parents(line)
        low = line.lower().replace("ё", "е")
        if low.startswith("поработали с инструментомом"):
            line = re.sub(r"(?i)^поработали с инструментомом", "поработали с инструментом", line)
        if low.startswith("инструментом"):
            return "поработали с " + line
        if low.startswith("инструмент "):
            return "поработали с " + line
        return line[:1].lower() + line[1:] if line else line

    def _fix_parent_report_verb_chain(self, line: str) -> str:
        fixes = {
            " и адаптировать": " и адаптировали",
            " и протестировать": " и протестировали",
            " и добавить": " и добавили",
            " и закрепить": " и закрепили",
            " и создать": " и создали",
            " и настроить": " и настроили",
            " и проверить": " и проверили",
            " и изменить": " и изменили",
            " и применить": " и применили",
            " и освоить": " и освоили",
            " и собрать": " и собрали",
            " и доработать": " и доработали",
            " и использовать": " и использовали",
        }
        out = line
        low = out.lower().replace("ё", "е")
        for src, dst in fixes.items():
            if src in low:
                # Keep original casing before the fixed fragment; fragments are lowercase in reports.
                out = re.sub(re.escape(src), dst, out, flags=re.IGNORECASE)
                low = out.lower().replace("ё", "е")
        return out

    def _learned_phrase_from_material(self, *, goal: str, result: str, tasks: list[str]) -> str:
        g = (goal or "").strip().strip(".")
        r = (result or "").strip().strip(".")
        if g:
            low = g.lower().replace("ё", "е")
            if low.startswith("формирование навыков адаптации"):
                rest = low.replace("формирование навыков адаптации", "", 1).strip()
                rest = rest.replace("игры ", "игру ", 1)
                return "адаптировать " + rest if rest else "адаптировать проект под нужный формат"
            if low.startswith("формирование навыков работы с"):
                rest = low.replace("формирование навыков работы с", "", 1).strip()
                return "работать с " + rest if rest else "работать с изученными инструментами"
            if low.startswith("формирование навыков"):
                rest = low.replace("формирование навыков", "", 1).strip()
                return "применять навыки " + self._capitalize_course_words(rest) if rest else "применять изученные навыки на практике"
            if low.startswith("формирование умения"):
                rest = low.replace("формирование умения", "", 1).strip(" .:-")
                return self._capitalize_course_words(rest) if rest else "применять изученное умение на практике"
            if low.startswith("формирование умений"):
                rest = low.replace("формирование умений", "", 1).strip(" .:-")
                return self._capitalize_course_words(rest) if rest else "применять изученные умения на практике"
            if low.startswith("формирование"):
                rest = low.replace("формирование", "", 1).strip(" .:-")
                if rest:
                    return self._capitalize_course_words(rest)
                return "закреплять материал занятия на практике"
            if low.startswith("адаптация "):
                rest = g[len("Адаптация "):].strip() if g.startswith("Адаптация ") else low[len("адаптация "):].strip()
                rest = rest.replace("игры ", "игру ", 1)
                return "адаптировать " + rest
            return g[:1].lower() + g[1:]
        if r:
            return "применять изученные инструменты на практике и создавать результат занятия: " + r[:1].lower() + r[1:]
        if tasks:
            return "применять изученные инструменты на практике"
        return "закреплять материал занятия на практике"

    def build_parent_report(self, user_text: str, variant: str = "normal") -> str:
        if not has_enough_report_details(user_text):
            return report_need_details_message()

        details = strip_report_command(user_text)

        # First use the synced Notion lesson page if the request points to a concrete
        # course/topic. This keeps parent reports aligned with the actual methodology,
        # not only with the fallback static program list.
        notion_report = self._try_build_parent_report_from_notion(details, variant=variant)
        if notion_report:
            return notion_report

        teacher_actions = self._extract_teacher_report_actions(details)
        if teacher_actions:
            return self._format_parent_report_from_teacher_actions(None, teacher_actions, variant=variant)

        # If the teacher asks by course and lesson/topic number, use the structured
        # Yellow Club program as a fallback. This fallback is allowed only after exact
        # Notion search failed; it must still respect the requested number.
        lesson = resolve_lesson_from_request(details)
        if lesson:
            return build_report_from_lesson(lesson, variant=variant)

        # If the request looks like a course + lesson number, but the exact topic is
        # absent, ask for clarification instead of generating a random report.
        maybe_lesson = any(word in self._canonical_user_query(details) for word in ["занят", "тема", "урок"])
        if maybe_lesson and any(ch.isdigit() for ch in details):
            hint = lesson_hint_message(details)
            if hint != report_need_details_message():
                return hint

        chunks = self.kb.search(details or user_text, top_k=5, max_chars=5000)
        kb_context = self.kb.build_context(chunks)
        prompt = f"""
Нужно составить сообщение в родительский чат по итогам занятия Yellow Club.

Регламент:
{REPORT_GUIDE}

Пример стиля:
{EXAMPLE_REPORT}

Данные от преподавателя / сотрудника:
{details}

Релевантная база знаний по теме, если пригодится:
{kb_context or 'Нет релевантных фрагментов.'}

Составь только итоговое сообщение для родительского чата.
Режим отчёта: {variant}. Если режим detailed - сделай отчёт чуть информативнее, но без выдуманных фактов. Если short - сделай короче. Если soft - сделай мягче.
Требования:
- без вступления "Конечно" и без пояснений для сотрудника;
- без технических комментариев;
- без Markdown-разметки;
- не добавляй того, чего не было в данных, если не уверен;
- формулировки должны быть понятны родителям;
- структура: приветствие, "Сегодня на занятии мы:", список, "Благодаря этому...".
""".strip()
        result = self.llm.generate(prompt=prompt, system=SYSTEM_PROMPT)
        if result.ok and result.text:
            return clean_report_output(result.text)
        return report_need_details_message()

    def build_parent_report_debug(self, query: str) -> str:
        q = strip_report_command(query or "").strip()
        if not q:
            return "Напишите запрос после команды. Пример: /parent_report_debug GDevelop 9 тема"
        profile = build_query_profile(q)
        match = self.kb.find_lesson_material(q, max_chars=2400)
        lesson = resolve_lesson_from_request(q)
        teacher_actions = self._extract_teacher_report_actions(q)
        matches = self.kb.search_files(q, top_k=6, max_chars_per_file=500)
        lines = [
            "🔎 Диагностика отчёта родителям",
            "",
            f"Запрос: {q}",
            f"Нормализовано: {profile.normalized}",
            f"Курс: {', '.join(profile.course_keys) if profile.course_keys else 'не определён'}",
            f"Номер темы: {profile.lesson_number or 'не определён'}",
            f"Данные преподавателя: {len(teacher_actions)} пункт(ов)",
            "",
        ]
        if match:
            lines.extend([
                "✅ Точная страница Notion найдена:",
                f"Название: {match.title}",
            ])
            if match.notion_path:
                lines.append(f"Путь: {self._format_display_notion_path(match.notion_path)}")
            if match.notion_url:
                lines.append(f"Ссылка: {match.notion_url}")
            lines.append("")
        else:
            lines.extend([
                "⚠️ Точную страницу Notion не нашёл.",
                "Если страница есть в Notion, проверь /lesson_material с этим же запросом и /notion_sync_fast.",
                "",
            ])
        if lesson:
            lines.extend([
                "Резервная программа в коде:",
                f"{lesson.course_title} - {lesson.lesson_number} тема - {lesson.title}",
                "",
            ])
        if matches:
            lines.append("Топ найденных страниц:")
            for i, m in enumerate(matches, start=1):
                lines.append(f"{i}. score={m.score} | {m.title}")
                if m.notion_path:
                    lines.append(f"   {self._format_display_notion_path(m.notion_path)}")
                if m.notion_url:
                    lines.append(f"   {m.notion_url}")
        return "\n".join(lines).strip()

    def rewrite_bot_answer(self, original_answer: str, instruction: str, chat_id: int, thread_id: Optional[int]) -> str:
        instr = (instruction or '').strip()
        original = (original_answer or '').strip()
        if not original:
            return 'Не вижу текста ответа, который нужно переделать. Ответь реплаем на сообщение агента и напиши, что изменить.'

        if original.lower().replace('ё', 'е').startswith('здравствуйте, уважаемые родители'):
            return self.rewrite_parent_report(original_report=original, instruction=instr)

        # Build small context from facts and decisions only; no need to search huge KB for every rewrite.
        facts = self.storage.list_facts(limit=15)
        fact_text = "\n".join(f"- {f['text']}" for f in facts)
        prompt = f"""
Сотрудник ответил на сообщение агента и просит переделать ответ.

Исходный ответ агента:
{original}

Что сотруднику не понравилось / что нужно изменить:
{instr}

Подтверждённые факты Yellow Club:
{fact_text or 'Нет сохранённых фактов.'}

Сделай новый вариант ответа лучше.
Правила:
- верни только новый готовый ответ;
- не объясняй, что ты переделал;
- не пиши "конечно", "вот вариант", "исправленный ответ";
- сохраняй факты: 4 занятия - 239 BYN, пробное бесплатно, адреса Кульман 1/1 и Мстиславца 6, максимум 6 учеников, возраст 7-17 лет;
- если сотрудник просит короче - сделай короче;
- если просит мягче/официальнее - измени тон;
- не добавляй лишние данные, которых не было в исходном ответе или подтверждённых фактах.
""".strip()
        result = self.llm.generate(prompt=prompt, system=SYSTEM_PROMPT)
        if result.ok and result.text:
            return postprocess_answer(result.text)
        return 'Не смог переделать через модель. Напиши, пожалуйста, что именно изменить: короче, мягче, официальнее или подробнее.'

    def rewrite_parent_report(self, original_report: str, instruction: str, source_request: str = "") -> str:
        instr = (instruction or '').strip()
        original = (original_report or '').strip()
        variant = report_variant_from_instruction(instr)

        # If we know the original teacher request, rebuild the report from that request.
        # This keeps buttons like "Подробнее" tied to the same lesson/topic, for example Blender 3.
        src = (source_request or '').strip()
        if src:
            rebuilt = self.build_parent_report('/parent_report ' + src, variant=variant)
            if rebuilt:
                tagless = re.sub(r'</?(?:b|i|u|strong|em|code)>', '', rebuilt, flags=re.IGNORECASE)
                if 'здравствуйте' in tagless.lower().replace('ё', 'е')[:220]:
                    return rebuilt

        if not original:
            return report_need_details_message()
        prompt = f"""
Нужно переделать сообщение для родительского чата Yellow Club.

Регламент отчёта:
{REPORT_GUIDE}

Исходный отчёт:
{original}

Что изменить:
{instr}

Режим изменения: {variant}

Сделай новый вариант отчёта.
Правила:
- верни только готовое сообщение для родителей;
- без фраз "конечно", "вот вариант", "переделал";
- сохрани структуру: приветствие, "Сегодня на занятии мы:", список, "Благодаря этому...";
- не добавляй личные данные учеников и внутренние комментарии;
- если просят короче - оставь 3-5 пунктов;
- если просят подробнее - добавь больше смысла для родителей, но без выдуманных фактов;
- если просят мягче - сделай формулировки теплее и спокойнее.
""".strip()
        result = self.llm.generate(prompt=prompt, system=SYSTEM_PROMPT)
        if result.ok and result.text:
            return clean_report_output(result.text)
        return clean_report_output(original)

    def _answer_from_confirmed_facts(self, user_text: str) -> str:
        n = user_text.lower().replace("ё", "е")
        facts = self.storage.list_facts(limit=50)
        fact_text = "\n".join(f.get("text", "") for f in facts)
        fact_norm = fact_text.lower().replace("ё", "е")

        if any(w in n for w in ["нейросет", "ии", "ai", "gemini", "chatgpt", "deepseek", "чатгпт"]):
            tools = []
            if "gemini" in fact_norm:
                tools.append("Gemini")
            if "chatgpt" in fact_norm or "чатгпт" in fact_norm:
                tools.append("ChatGPT")
            if "deepseek" in fact_norm or "дипсик" in fact_norm:
                tools.append("Deepseek")
            if tools:
                return (
                    "По подтверждённой информации, на занятиях Yellow Club могут использоваться: "
                    + ", ".join(tools)
                    + ".\n\n"
                    "Для сотрудника можно пояснять так: нейросети используются не как отдельная цель обучения, а как инструмент внутри практических задач - для поиска идей, работы с текстом, помощи с кодом, объяснения ошибок и улучшения проектов. Если нужен официальный список для клиента, лучше использовать именно эти названия и не добавлять другие сервисы без подтверждения методиста."
                )
        return ""

    def _improve_with_llm(self, ctx: AnswerContext, base_answer: str) -> str:
        chunks = self.kb.search(ctx.user_text, top_k=3, max_chars=3000)
        kb_context = self.kb.build_context(chunks)
        facts = self.storage.list_facts(limit=10)
        fact_text = "\n".join(f"- {f['text']}" for f in facts)
        prompt = f"""
Запрос:
{ctx.user_text}

Безопасный базовый ответ:
{base_answer}

Подтверждённые факты:
{fact_text or 'Нет.'}

База знаний:
{kb_context or 'Нет релевантного фрагмента.'}

Перепиши базовый ответ естественнее и полезнее для сотрудника Yellow Club. Верни только сам ответ, без вступлений вроде "Хорошо, вот...", без разделителей "---", без служебных комментариев. Не добавляй цену, адреса или расписание, если они не нужны. Не упоминай черновики и внутреннюю запись в базу.
""".strip()
        result = self.llm.generate(prompt=prompt, system=SYSTEM_PROMPT)
        if result.ok and result.text:
            return result.text
        return base_answer
