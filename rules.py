from __future__ import annotations

import re
from typing import Optional

from utils import normalize, remove_markdown_noise

CURRENT_PRICE = "239 BYN"
TRIAL_PRICE = "бесплатное"
CURRENT_ADDRESSES = "Кульман 1/1 и Мстиславца 6"
MAX_GROUP_SIZE = "6 учеников"
AGE_RANGE = "7-17 лет"

QUESTION_MARKERS = (
    "?", "подскаж", "можно", "как", "что", "где", "куда", "к кому", "сколько", "какие", "какая", "какой",
    "почему", "зачем", "нужно", "надо", "хочу", "интересует", "можем", "можно ли", "пришел", "пришёл",
)

TRIAL_WORDS = (
    "пробн", "запис", "заявк", "хочу изучать", "хочу на", "интересует", "роблокс", "roblox", "python", "пайтон"
)


def is_question_like(text: str) -> bool:
    n = normalize(text)
    if not n:
        return False
    if n.startswith("/"):
        return True
    return any(marker in n for marker in QUESTION_MARKERS)


def should_smart_reply(text: str) -> bool:
    n = normalize(text)
    if len(n) < 4:
        return False
    if is_question_like(n):
        return True
    # Work-intent statements that are not grammatically questions.
    patterns = [
        "на занятие пришел 1", "на занятие пришел один", "на занятие пришёл 1", "на занятие пришёл один",
        "хочу изучать", "хочу roblox", "хочу роблокс", "интересует roblox", "интересует роблокс",
        "родитель спрашивает", "клиент спрашивает", "ученик пропустил", "нет отчета", "нет отчёта",
    ]
    return any(p in n for p in patterns)


def is_price_question(text: str) -> bool:
    n = normalize(text)
    return any(w in n for w in ["стоим", "цена", "оплат", "сколько стоят", "сколько стоит", "прайс"])


def is_address_question(text: str) -> bool:
    n = normalize(text)
    return any(w in n for w in ["адрес", "где проходят", "кульман", "мстиславца", "класс"])


def is_one_student_situation(text: str) -> bool:
    n = normalize(text)
    return (
        ("пришел" in n or "пришёл" in n or "пришла" in n)
        and ("1 учен" in n or "один учен" in n or "только" in n)
    ) or "на занятие пришел 1" in n or "на занятие пришёл 1" in n


def is_trial_intent(text: str) -> bool:
    n = normalize(text)
    return any(w in n for w in TRIAL_WORDS) and not is_price_question(n)


def is_roblox_interest(text: str) -> bool:
    n = normalize(text)
    return "roblox" in n or "роблокс" in n


def strict_response(text: str) -> Optional[str]:
    n = normalize(text)
    if is_one_student_situation(n):
        return (
            "Если на регулярное занятие пришёл только один ученик, занятие можно проводить.\n\n"
            "По регламенту: офлайн-занятие с 1 учеником длится 1 час, онлайн-занятие с 1 учеником - 50 минут.\n\n"
            "Если есть шанс, что остальные ученики просто опаздывают или забыли, менеджеру лучше быстро связаться с родителями. "
            "Если родители подтвердили, что дети скоро подойдут, начинайте занятие с пришедшим учеником и продолжайте по ситуации. "
            "Присутствие, опоздания и договорённости нужно зафиксировать в Мой Класс или рабочем чате."
        )
    if is_price_question(n):
        return (
            f"Актуальная стоимость обучения - 4 занятия за {CURRENT_PRICE}. Пробное занятие бесплатное.\n\n"
            f"В группе максимум {MAX_GROUP_SIZE}, возраст учеников - {AGE_RANGE}. "
            "Если вопрос от родителя, лучше дополнить: на пробном занятии ребёнок сможет познакомиться с форматом, преподавателем и сделать первую практическую работу."
        )
    if is_address_question(n):
        return f"Актуальные адреса учебных классов Yellow Club: {CURRENT_ADDRESSES}."
    return None


def postprocess_answer(text: str) -> str:
    text = remove_markdown_noise(text)
    # Remove common local-model meta framing.
    meta_prefixes = [
        r"^\s*хорошо,?\s*вот(?:\s+более)?(?:\s+естественный)?(?:\s+и)?(?:\s+полезный)?\s+ответ[^:\n]*:\s*",
        r"^\s*конечно,?\s*вот(?:\s+вариант)?(?:\s+ответа)?[^:\n]*:\s*",
        r"^\s*вариант ответа:\s*",
    ]
    for pat in meta_prefixes:
        text = re.sub(pat, "", text, flags=re.I)
    text = re.sub(r"\n\s*---\s*\n", "\n\n", text)
    text = text.replace("{Имя}", "").replace("(Имя)", "")
    # Protect current facts from old source files.
    replacements = {
        "230 BYN": "239 BYN",
        "230BYN": "239 BYN",
        "210 BYN": "239 BYN",
        "210BYN": "239 BYN",
        "190 BYN": "239 BYN",
        "190BYN": "239 BYN",
        "до 8 учеников": "до 6 учеников",
        "8 учеников": "6 учеников",
        "Кульман 1/2": "Кульман 1/1",
        "Кульман, 1/2": "Кульман 1/1",
        "Якуба Коласа,3": "Кульман 1/1",
        "Якуба Коласа 3": "Кульман 1/1",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Avoid internal implementation details in public answers.
    internal_phrases = [
        "черновик сохранен", "черновик сохранён", "создал черновик", "создан черновик",
        "заявка сохранена в базе", "сохранил в базу", "сохранил черновик",
    ]
    lowered = normalize(text)
    if any(p in lowered for p in internal_phrases):
        text = re.sub(r"(?i).*черновик.*(?:\n|$)", "", text)
        text = re.sub(r"(?i).*сохран.*(?:\n|$)", "", text)
    return text.strip()
