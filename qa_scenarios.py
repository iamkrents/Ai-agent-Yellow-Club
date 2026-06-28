from __future__ import annotations

"""Offline smoke checks for Yellow Club Agent.

Run from the project folder:
    python qa_scenarios.py

This file does not call Telegram, Notion, MoyKlass or Ollama. It only checks
query understanding and report formatting on deterministic local functions.
"""

from agent_core import AgentCore
from knowledge_base import KBFileMatch
from query_tools import build_query_profile, extract_lesson_number, extract_course_keys
from report_manager import is_parent_report_request, report_variant_from_instruction


class _DummyKB:
    def find_lesson_material(self, *args, **kwargs):
        return None

    def search(self, *args, **kwargs):
        return []

    def build_context(self, *args, **kwargs):
        return ""


class _BlenderLessonKB(_DummyKB):
    def find_lesson_material(self, *args, **kwargs):
        text = """🎯 Цель
Формирование навыков работы с геометрией объекта в режиме редактирования.

🛠️ Задачи
- разобрать, как устроены 3D-модели: вершины, ребра, грани
- освоить режим редактирования (Edit Mode)
- научиться добавлять изображение-референс
- освоить инструмент Snap Tool
- освоить инструмент Loop Cut
- собрать модель Стива из кубических элементов

✅ Ожидаемый результат
Ученик сделал каркас модели Стива.
"""
        return KBFileMatch(
            source="notion/blender_3.md",
            title="3 тема - Каркас модели Стива",
            text=text,
            score=100,
        )


class _DummyLLM:
    def generate(self, *args, **kwargs):
        class Result:
            ok = False
            text = ""
        return Result()


def _assert_contains(text: str, fragment: str, label: str) -> None:
    if fragment.lower() not in text.lower():
        raise AssertionError(f"{label}: expected fragment not found: {fragment!r}\n{text}")


def run() -> None:
    core = AgentCore(_DummyKB(), None, _DummyLLM())
    blender_core = AgentCore(_BlenderLessonKB(), None, _DummyLLM())

    blender_report = blender_core.build_parent_report("/parent_report Blender 3 тема")
    _assert_contains(blender_report, "<b>", "Blender report html")
    _assert_contains(blender_report, "освоили режим редактирования", "Blender infinitive normalization")
    _assert_contains(blender_report, "каркас модели Стива", "Blender practical result")
    if "ученик сделал" in blender_report.lower():
        raise AssertionError("Blender report must not contain raw phrase: ученик сделал")
    if "должен получиться" in blender_report.lower():
        raise AssertionError("Blender report must not contain raw phrase: должен получиться")

    query_cases = [
        ("GDevelop 9 tema", "gdevelop", 9),
        ("гдевелоп девятая тема", "gdevelop", 9),
        ("сделай отчет родителям по Photoshop 3 тема Фигуры", "photoshop", 3),
        ("материал занятия Python 4 занятие", "python", 4),
    ]
    for text, course, number in query_cases:
        profile = build_query_profile(text)
        courses = extract_course_keys(text)
        lesson_number = extract_lesson_number(text)
        assert course in courses or course in profile.course_keys, (text, courses, profile)
        assert int(lesson_number) == number, (text, lesson_number)

    report_cases = [
        "напиши отчет по GDevelop 9 tema. Сегодня на занятии мы добавили джойстик, нарисовали кнопку прыжка, настроили управление и скрыли для компьютера. Протестировали на телефоне по QR",
        "/parent_report GDevelop 9 тема",
        "сделай отчёт для родителей по фотошоп 3 тема фигуры",
    ]
    for text in report_cases:
        assert is_parent_report_request(text), text
        report = core.build_parent_report(text)
        _assert_contains(report, "Здравствуйте", "report greeting")
        _assert_contains(report, "<b>", "html bold")
        if "gdevelop" in text.lower() or "девелоп" in text.lower():
            _assert_contains(report, "мобиль", "GDevelop 9 report")

    assert report_variant_from_instruction("✂️ Короче") == "short"
    assert report_variant_from_instruction("📌 Подробнее") == "detailed"
    assert report_variant_from_instruction("🤝 Мягче") == "soft"
    assert report_variant_from_instruction("🔁 Переделать") == "alternate"

    source = "GDevelop 9 tema. Сегодня на занятии мы добавили джойстик, нарисовали кнопку прыжка, настроили управление и скрыли для компьютера. Протестировали на телефоне по QR"
    normal = core.build_parent_report("/parent_report " + source)
    detailed = core.rewrite_parent_report(normal, "📌 Подробнее", source_request=source)
    soft = core.rewrite_parent_report(normal, "🤝 Мягче", source_request=source)
    short = core.rewrite_parent_report(normal, "✂️ Короче", source_request=source)
    assert normal != detailed, "detailed button did not change the report"
    assert normal != soft, "soft button did not change the report"
    assert len(short) <= len(detailed), "short variant should not be longer than detailed"

    print("OK: QA scenarios passed")


if __name__ == "__main__":
    run()
