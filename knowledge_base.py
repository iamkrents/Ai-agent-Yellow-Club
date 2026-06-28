from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from utils import normalize, trim
from query_tools import (
    canonicalize_user_text,
    extract_course_terms,
    extract_course_keys,
    COURSE_ALIASES,
    extract_lesson_number,
    normalize_for_search_tokens,
    query_has_lesson_intent,
)


@dataclass
class KBChunk:
    source: str
    text: str
    score: int = 0


@dataclass
class KBFile:
    source: str
    title: str
    text: str
    norm_title: str
    norm_text: str
    notion_url: str = ""
    notion_path: str = ""


@dataclass
class KBFileMatch:
    source: str
    title: str
    text: str
    score: int = 0
    notion_url: str = ""
    notion_path: str = ""


_QUERY_STOPWORDS = {
    "найди", "найти", "дай", "дайте", "скинь", "скинуть", "покажи", "показать",
    "нужен", "нужна", "нужно", "материал", "материалы", "занятие", "занятия",
    "урок", "урока", "тема", "темы", "номер", "по", "про", "для", "на", "из", "в", "и",
    "полностью", "само", "сама", "целый", "целиком", "конспект", "план",
    # Часто сотрудники пишут русские слова латиницей: "9 tema", "3 lesson".
    "tema", "temy", "urok", "lesson", "lessons",
}

_COURSE_SYNONYMS = {}  # Course synonyms are handled centrally in query_tools.py
_STRONG_COURSE_SCORE_CACHE: dict[tuple[str, str], int] = {}


def _safe_text(value: str) -> str:
    return value or ""


def _canonical_query(text: str) -> str:
    return canonicalize_user_text(text or "")


def _slug_to_title(stem: str) -> str:
    stem = (stem or "").strip()
    if "__" in stem:
        stem = stem.split("__", 1)[0]
    stem = re.sub(r"_\d+$", "", stem)
    stem = stem.replace("_", " ").strip()
    return stem or "Файл базы знаний"


def _extract_h1(text: str) -> str:
    for line in (text or "").splitlines()[:20]:
        line = line.strip()
        if line.startswith("# ") and len(line) > 2:
            return line[2:].strip()
    return ""


def _tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    text = _canonical_query(text)
    tokens = re.findall(r"[a-zа-я0-9+.#-]+", text)
    out: list[str] = []
    for token in tokens:
        token = token.strip("-.#")
        if not token:
            continue
        if len(token) <= 2 and not token.isdigit():
            continue
        if not keep_stopwords and token in _QUERY_STOPWORDS:
            continue
        out.append(token)
    return out


def _expanded_tokens(query: str) -> list[str]:
    tokens = _tokenize(query)
    # Add centrally known course aliases, so all agent modes search the same way.
    for term in extract_course_terms(query):
        for part in _tokenize(term, keep_stopwords=True):
            if part not in tokens:
                tokens.append(part)
    return tokens


def _extract_numbers(query: str) -> list[str]:
    return re.findall(r"\d+", query or "")


def _extract_lesson_number_from_query(query: str) -> str:
    return extract_lesson_number(query or "")


def _file_has_exact_lesson_number(file: "KBFile", number: str) -> bool:
    if not number:
        return True
    # Do not search in raw Notion IDs. Only human-readable title/path/slug.
    readable_source = _slug_to_title(Path(file.source).stem)
    haystacks = [file.title, file.notion_path, readable_source]
    for value in haystacks:
        n = _canonical_query(value or "")
        patterns = [
            rf"(?:^|[^0-9]){re.escape(number)}\s*(?:тема|темы|занятие|занятия|урок|урока)(?:[^0-9]|$)",
            rf"(?:тема|темы|занятие|занятия|урок|урока)\s*№?\s*{re.escape(number)}(?:[^0-9]|$)",
            rf"(?:^|[^0-9]){re.escape(number)}\s*[-–—:]",
            rf"^\s*{re.escape(number)}\b",
        ]
        if any(re.search(pat, n) for pat in patterns):
            return True
    return False


def _file_has_different_lesson_number(file: "KBFile", requested_number: str) -> bool:
    if not requested_number:
        return False
    readable_source = _slug_to_title(Path(file.source).stem)
    compact = _canonical_query(" ".join([file.title, file.notion_path, readable_source]))
    nums: list[str] = []
    for pat in [
        r"(?:тема|темы|занятие|занятия|урок|урока)\s*№?\s*(\d{1,2})",
        r"(\d{1,2})\s*(?:тема|темы|занятие|занятия|урок|урока)",
        r"^\s*(\d{1,2})\b",
    ]:
        nums.extend(re.findall(pat, compact))
    return bool(nums and requested_number not in nums)


def _specific_title_tokens_from_query(query: str) -> list[str]:
    """Tokens that describe the lesson title, not the course or number.

    Used as a safe fallback for pages whose Notion path does not contain a
    parent course name. Example: "GDevelop - 4 тема - Сбор ресурсов и счетчики"
    can still match a page titled "4 тема - Сбор ресурсов и счетчики".
    """
    q = _canonical_query(query)
    for key, aliases in COURSE_ALIASES.items():
        for alias in aliases:
            a = _canonical_query(alias)
            if a:
                q = re.sub(rf"(?<![a-zа-я0-9]){re.escape(a)}(?![a-zа-я0-9])", " ", q)
        q = re.sub(rf"(?<![a-zа-я0-9]){re.escape(key)}(?![a-zа-я0-9])", " ", q)
    q = re.sub(r"\b(?:тема|темы|урок|урока|занятие|занятия|материал|notion|ноушн|номер|по|для|и|в|на|с)\b", " ", q)
    q = re.sub(r"\b\d{1,2}\b", " ", q)
    tokens = []
    for t in re.findall(r"[a-zа-я0-9+#-]+", q):
        t = t.strip("-+#")
        if len(t) >= 4 and t not in _QUERY_STOPWORDS and t not in tokens:
            tokens.append(t)
    return tokens


def _file_matches_specific_title_tokens(file: "KBFile", query: str) -> bool:
    tokens = _specific_title_tokens_from_query(query)
    if not tokens:
        return False
    readable = _canonical_query(" ".join([file.title, file.notion_path, _slug_to_title(Path(file.source).stem)]))
    full = _canonical_query(" ".join([file.title, file.notion_path, _slug_to_title(Path(file.source).stem), file.text[:2500]]))
    title_hits = sum(1 for t in tokens if t in readable)
    full_hits = sum(1 for t in tokens if t in full)
    # Two title/path hits are strong enough. For short titles, require all tokens.
    if title_hits >= min(2, len(tokens)):
        return True
    # Body text fallback is weaker but still useful for synced pages with poor titles.
    return full_hits >= min(3, len(tokens))


def _course_tokens_from_query(query: str) -> list[str]:
    return extract_course_terms(query)


def _course_phrase_in_text(phrase: str, text: str) -> bool:
    phrase = _canonical_query(phrase or "").strip()
    text = _canonical_query(text or "")
    if not phrase or not text:
        return False
    # Very short aliases like "py", "c", "js" are useful when parsing the
    # user's query, but they are too noisy for matching Notion lesson pages.
    # Example: "Python 3" must not match "C 3" only because some URL/text has "py".
    if len(phrase) <= 2:
        return bool(re.search(rf"(?<![a-zа-я0-9]){re.escape(phrase)}(?![a-zа-я0-9])", text))
    if re.fullmatch(r"[a-zа-я0-9+#.-]+", phrase):
        return bool(re.search(rf"(?<![a-zа-я0-9]){re.escape(phrase)}(?![a-zа-я0-9])", text))
    return phrase in text


def _course_keys_from_tokens(course_tokens: list[str]) -> list[str]:
    keys: list[str] = []
    for token in course_tokens:
        token_c = _canonical_query(token)
        for key, aliases in COURSE_ALIASES.items():
            alias_set = {_canonical_query(a) for a in aliases} | {key}
            if token == key or token_c in alias_set:
                if key not in keys:
                    keys.append(key)
    if not keys:
        keys = extract_course_keys(" ".join(course_tokens))
    return keys


def _file_readable_course_keys(file: "KBFile") -> list[str]:
    readable = " ".join([file.title, file.notion_path, _slug_to_title(Path(file.source).stem)])
    return extract_course_keys(readable)


def _file_has_conflicting_course(file: "KBFile", requested_course_keys: list[str]) -> bool:
    """Return True when a lesson page explicitly belongs to another course.

    Example: request Python 3 must not return a page whose title/path says
    "Язык C - 3 тема", even if the body mentions Python somewhere.
    """
    requested = set(requested_course_keys or [])
    if not requested:
        return False
    file_keys = set(_file_readable_course_keys(file))
    if not file_keys:
        return False
    return file_keys.isdisjoint(requested)


def _course_boundary_pattern(term: str) -> str:
    return rf"(?<![a-zа-я0-9+.#]){re.escape(term)}(?![a-zа-я0-9+.#])"


def _file_strong_course_score(file: "KBFile", course_key: str) -> int:
    """Estimate a page's real course from strong signals, not incidental words.

    MoyKlass can give only "Python 3", while synced Notion pages may have generic
    paths like "Темы / 3 тема". A C lesson can still mention Python in a short
    explanation, so we need stronger evidence than a single token hit.
    """
    course_key = (course_key or "").strip().lower()
    if not course_key:
        return 0
    cache_key = (file.source, course_key)
    cached = _STRONG_COURSE_SCORE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    readable = _canonical_query(" ".join([file.title, file.notion_path, _slug_to_title(Path(file.source).stem)]))
    body_raw = " ".join([file.text[:7000], file.source, file.notion_path])
    body = _canonical_query(body_raw)
    score = 0

    # Course in title/path/source is the strongest signal.
    for alias in COURSE_ALIASES.get(course_key, ()):  # type: ignore[arg-type]
        a = _canonical_query(alias)
        if not a:
            continue
        if _course_phrase_in_text(a, readable):
            score += 12
            break

    strong_patterns: dict[str, tuple[str, ...]] = {
        "python": (
            r"язык(?:е|а|ом)?\s+python\b",
            r"на\s+python\b",
            r"```\s*python\b",
            r"\bprint\s*\(",
            r"\binput\s*\(",
            r"\btype\s*\(",
        ),
        "c": (
            r"язык(?:е|а|ом)?\s+c\b",
            r"язык(?:е|а|ом)?\s+си\b",
            r"```\s*c\b",
            r"#include\s*<stdio",
            r"\bprintf\s*\(",
            r"\bscanf\s*\(",
            r"\bint\s+main\s*\(",
        ),
        "cpp": (
            r"c\+\+",
            r"си\s+плюс\s+плюс",
            r"```\s*(?:cpp|c\+\+)\b",
            r"#include\s*<iostream",
            r"\bstd::",
            r"\bcout\s*<<",
        ),
        "javascript": (
            r"javascript",
            r"java\s*script",
            r"```\s*(?:js|javascript)\b",
            r"\bconsole\.log\s*\(",
        ),
        "gdevelop": (r"gdevelop", r"джидевелоп", r"гдевелоп", r"мобильн.{0,20}игр", r"джойстик"),
        "roblox": (r"roblox", r"роблокс", r"roblox\s+studio"),
        "blender": (r"blender", r"блендер", r"3d", r"edit\s+mode"),
        "photoshop": (r"photoshop", r"фотошоп", r"кисть", r"слои", r"маска"),
        "html": (r"html", r"<html", r"<body", r"```\s*html\b"),
        "css": (r"css", r"селектор", r"```\s*css\b"),
        "wordpress": (r"wordpress", r"вордпресс"),
        "figma": (r"figma", r"фигма"),
        "telegram_bot": (r"telegram", r"телеграм", r"bot", r"aiogram"),
        "kotlin": (r"kotlin", r"котлин"),
        "blockbench": (r"blockbench", r"блокбенч"),
    }
    for pat in strong_patterns.get(course_key, ()):  # raw regexes can include code syntax
        score += min(3, len(re.findall(pat, body_raw, flags=re.IGNORECASE))) * 5

    # Long aliases in body are useful; very short aliases (c, py, js, ps) are too noisy.
    for alias in COURSE_ALIASES.get(course_key, ()):  # type: ignore[arg-type]
        a = _canonical_query(alias)
        if len(a) <= 2:
            continue
        if _course_phrase_in_text(a, body):
            score += 3

    _STRONG_COURSE_SCORE_CACHE[cache_key] = score
    return score


def _file_has_stronger_conflicting_course(file: "KBFile", requested_course_keys: list[str]) -> bool:
    requested = [x for x in (requested_course_keys or []) if x]
    if not requested:
        return False

    # Do not scan every possible Yellow Club direction for every numbered page.
    # Most dangerous false matches are between programming-language courses that
    # share words like "переменные", "типы данных" and code examples.
    competitor_map: dict[str, tuple[str, ...]] = {
        "python": ("c", "cpp", "javascript", "telegram_bot"),
        "c": ("python", "cpp", "javascript"),
        "cpp": ("python", "c", "javascript"),
        "javascript": ("python", "c", "cpp", "html", "css"),
        "html": ("css", "javascript"),
        "css": ("html", "javascript"),
        "gdevelop": ("roblox", "python", "javascript"),
        "roblox": ("gdevelop", "blender"),
        "blender": ("roblox", "blockbench"),
        "photoshop": ("figma",),
        "figma": ("photoshop",),
        "telegram_bot": ("python",),
    }
    competitors: set[str] = set()
    for key in requested:
        competitors.update(competitor_map.get(key, ()))
    if not competitors:
        return False

    requested_score = max((_file_strong_course_score(file, key) for key in requested), default=0)
    other_score = max((_file_strong_course_score(file, key) for key in competitors if key not in requested), default=0)
    # If another course has much stronger evidence, do not use this page.
    return other_score >= 10 and other_score >= requested_score + 6


def _file_has_course_context(file: "KBFile", course_tokens: list[str]) -> bool:
    if not course_tokens:
        return True

    # Work with canonical course keys instead of raw expanded aliases. The raw
    # alias list contains short terms (py, c, js, ps), which can produce false
    # positives across different courses.
    course_keys = _course_keys_from_tokens(course_tokens)
    if not course_keys:
        return True

    readable = _canonical_query(" ".join([file.title, file.notion_path, _slug_to_title(Path(file.source).stem)]))
    full = _canonical_query(" ".join([file.title, file.notion_path, _slug_to_title(Path(file.source).stem), file.text[:4500]]))

    for key in course_keys:
        aliases = list(COURSE_ALIASES.get(key, ())) + [key]
        for alias in aliases:
            a = _canonical_query(alias)
            if not a:
                continue
            # Strong signal: course in title/path/source. Allow even short aliases there.
            if _course_phrase_in_text(a, readable):
                return True
            # Weak signal: course in body text. Ignore very short aliases to avoid wrong
            # lessons like Python 3 -> C 3.
            if len(a) > 2 and _course_phrase_in_text(a, full):
                return True
    return False


def _is_lesson_file(file: "KBFile") -> bool:
    hay = _canonical_query(" ".join([file.title, file.notion_path, _slug_to_title(Path(file.source).stem), file.text[:1200]]))
    if any(w in hay for w in ["программа обучения", "темы", "тема", "занятие", "урок", "цель", "задачи", "результат занятия"]):
        if any(bad in hay for bad in ["регламент", "client manager", "менеджер", "команда"]):
            # A regulation can mention a lesson, but it is not a lesson material page.
            return False
        return True
    return False


def extract_notion_url(text: str) -> str:
    match = re.search(r"^Notion URL:\s*(\S+)\s*$", _safe_text(text), flags=re.MULTILINE)
    return (match.group(1).strip() if match else "")


def extract_notion_path(text: str) -> str:
    match = re.search(r"^Путь Notion:\s*(.+?)\s*$", _safe_text(text), flags=re.MULTILINE)
    return (match.group(1).strip() if match else "")


def strip_notion_noise(text: str, *, keep_links: bool = False) -> str:
    """Remove raw Notion/HTML artifacts while keeping readable lesson text."""
    text = _safe_text(text).replace("\r\n", "\n")
    text = html.unescape(text)

    # Convert common HTML-like Notion blocks to plain text boundaries.
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(?:callout|columns?|column|table_of_contents|empty-block|bookmark|embed|file|pdf)[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<video[^>]*src=[\"']([^\"']+)[\"'][^>]*>\s*</video>", r"Видео: \1", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<video[^>]*>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"</video>", "", text, flags=re.IGNORECASE)

    # Discussion spans are Notion service metadata. Keep only the visible inner text.
    text = re.sub(r"<span\b[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</span>", "", text, flags=re.IGNORECASE)

    # Remove images and huge signed file URLs from Telegram output. Full page link remains available separately.
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"^\s*https?://prod-files-secure[^\s)]*\s*$", "", text, flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r"^\s*https?://s3[^\s)]*\s*$", "", text, flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r"^\s*https?://[^\s)]*amazonaws\.com[^\s)]*\s*$", "", text, flags=re.MULTILINE | re.IGNORECASE)

    # If a raw Markdown image line survived because the URL was split, remove the start marker.
    text = re.sub(r"^\s*!\[\]\(.*$", "", text, flags=re.MULTILINE)

    if keep_links:
        # Keep readable text for normal markdown links and expose the URL only for non-empty labels.
        text = re.sub(r"\[([^\]]{1,80})\]\((https?://[^)]+)\)", r"\1: \2", text)
    else:
        text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", text)

    # Remove any remaining simple HTML tags but keep inner text.
    text = re.sub(r"</?[^>\n]+>", "", text)

    # Clean markdown emphasis without deleting content.
    text = re.sub(r"\*\*([^*\n][^*]*?)\*\*", r"\1", text)
    text = re.sub(r"__([^_\n][^_]*?)__", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # Remove leftover service attributes / long query fragments that can appear after broken markdown images.
    text = re.sub(r"\bX-Amz-[A-Za-z0-9_-]+=[^\s]+", "", text)
    text = re.sub(r"\bSignature=[^\s]+", "", text)
    text = re.sub(r"\bCredential=[^\s]+", "", text)

    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        plain = line.strip()
        if not plain:
            lines.append("")
            continue
        low = plain.lower()
        if low in {"columns", "column", "empty-block", "table_of_contents"}:
            continue
        if len(plain) > 220 and ("https://" in plain or "http://" in plain):
            continue
        lines.append(line)

    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def clean_kb_output(text: str) -> str:
    """Make synced Notion Markdown readable in Telegram."""
    text = _safe_text(text).replace("\r\n", "\n")

    # Remove technical metadata added during sync from the body. We show the source separately.
    text = re.sub(r"^Источник:\s*Notion\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^Notion page ID:\s*[0-9a-fA-F-]+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^Notion URL:\s*\S+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^Путь Notion:\s*", "Путь: ", text, flags=re.MULTILINE)

    return strip_notion_noise(text, keep_links=False)


class KnowledgeBase:
    def __init__(self, data_dir: Path, use_raw_excel: bool = False):
        self.data_dir = data_dir
        self.use_raw_excel = use_raw_excel
        self.chunks: List[KBChunk] = []
        self.files: List[KBFile] = []
        self.reload()

    def reload(self) -> int:
        self.chunks.clear()
        self.files.clear()
        if not self.data_dir.exists():
            return 0
        files = sorted(self.data_dir.rglob("*.md"))
        for path in files:
            rel = str(path.relative_to(self.data_dir)).replace("\\", "/")
            if not self.use_raw_excel and rel.startswith("raw_excel/"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            title = _extract_h1(text) or _slug_to_title(path.stem)
            clean_text = clean_kb_output(text)
            notion_url = extract_notion_url(text)
            notion_path = extract_notion_path(text)
            self.files.append(
                KBFile(
                    source=rel,
                    title=title,
                    text=clean_text,
                    norm_title=_canonical_query(title + " " + _slug_to_title(path.stem) + " " + rel + " " + notion_path),
                    norm_text=_canonical_query(clean_text + " " + rel + " " + notion_path),
                    notion_url=notion_url,
                    notion_path=notion_path,
                )
            )
            for chunk in self._split(clean_text):
                self.chunks.append(KBChunk(source=rel, text=chunk))
        return len(self.chunks)

    def _split(self, text: str) -> Iterable[str]:
        text = text.replace("\r\n", "\n")
        parts = re.split(r"\n(?=# )|\n(?=## )|\n(?=### )", text)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if len(part) <= 1800:
                yield part
            else:
                # Split long parts by paragraphs.
                current = ""
                for para in part.split("\n\n"):
                    if len(current) + len(para) + 2 <= 1800:
                        current += ("\n\n" if current else "") + para
                    else:
                        if current:
                            yield current.strip()
                        current = para
                if current:
                    yield current.strip()

    def file_list(self) -> list[str]:
        if not self.data_dir.exists():
            return []
        files = sorted(str(p.relative_to(self.data_dir)).replace("\\", "/") for p in self.data_dir.rglob("*.md"))
        if not self.use_raw_excel:
            files = [f for f in files if not f.startswith("raw_excel/")]
        return files

    def _score_file(self, file: KBFile, query: str) -> int:
        qn = _canonical_query(query)
        tokens = _expanded_tokens(query)
        if not tokens:
            tokens = _tokenize(query, keep_stopwords=True)
        numbers = _extract_numbers(query)
        title = file.norm_title
        text = file.norm_text
        combined = title + "\n" + text

        score = 0
        if qn and qn in combined:
            score += 120
        if qn and qn in title:
            score += 180

        for token in tokens:
            if not token:
                continue
            if token in title:
                score += 35
            count = text.count(token)
            if count:
                score += 2 + min(18, count)

        for number in numbers:
            # Number in title is very important for requests like "Photoshop 3 тема".
            if re.search(rf"(^|[^0-9]){re.escape(number)}([^0-9]|$)", title):
                score += 80
            elif re.search(rf"(^|[^0-9]){re.escape(number)}([^0-9]|$)", text):
                score += 20

        lesson_or_course_topic = query_has_lesson_intent(query) or bool(_extract_lesson_number_from_query(query) and _course_tokens_from_query(query))

        # Prefer lesson/material pages over regulations when user asks for a lesson.
        if lesson_or_course_topic:
            if any(w in title for w in ["тема", "занят", "урок", "lesson", "module"]):
                score += 45
            if "regulations" in file.source or "регламент" in title or "manager" in file.source:
                score -= 70
            if file.source.startswith("notion/"):
                score += 15

        # If query contains an explicit course word, pages that mention the course are better.
        course_tokens = _course_tokens_from_query(query)
        if course_tokens:
            has_course = any(t in combined for t in course_tokens)
            if has_course:
                score += 80
            elif lesson_or_course_topic:
                # Do not fully exclude: synced child pages may not contain parent course in the title.
                score -= 35

        requested_number = _extract_lesson_number_from_query(query)
        if requested_number and lesson_or_course_topic:
            if _file_has_exact_lesson_number(file, requested_number):
                # Requests like "GDevelop 9 тема" must not fall back to "1 тема" just
                # because it mentions the course more often. Exact topic number wins.
                score += 220
            elif _file_has_different_lesson_number(file, requested_number):
                score -= 260
            else:
                score -= 60

        return score

    def search_files(self, query: str, top_k: int = 8, max_chars_per_file: int = 6000) -> list[KBFileMatch]:
        results: list[KBFileMatch] = []
        for file in self.files:
            score = self._score_file(file, query)
            if score > 0:
                text = file.text
                if max_chars_per_file and len(text) > max_chars_per_file:
                    text = trim(text, max_chars_per_file)
                results.append(KBFileMatch(source=file.source, title=file.title, text=text, score=score, notion_url=file.notion_url, notion_path=file.notion_path))
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

    def get_file_text(self, source: str, max_chars: int = 18000) -> KBFileMatch | None:
        source = (source or "").strip().replace("\\", "/")
        for file in self.files:
            if file.source == source:
                text = file.text if max_chars <= 0 else trim(file.text, max_chars)
                return KBFileMatch(source=file.source, title=file.title, text=text, score=0, notion_url=file.notion_url, notion_path=file.notion_path)
        return None

    def _match_from_file(self, file: KBFile, query: str, *, max_chars: int, score_bonus: int = 0) -> KBFileMatch:
        text = file.text if max_chars <= 0 else trim(file.text, max_chars)
        return KBFileMatch(
            source=file.source,
            title=file.title,
            text=text,
            score=self._score_file(file, query) + score_bonus,
            notion_url=file.notion_url,
            notion_path=file.notion_path,
        )

    def find_lesson_material(self, query: str, max_chars: int = 18000) -> KBFileMatch | None:
        requested_number = _extract_lesson_number_from_query(query)
        course_tokens = _course_tokens_from_query(query)
        course_keys = _course_keys_from_tokens(course_tokens)
        explicit_lesson_query = bool(requested_number or course_tokens or query_has_lesson_intent(query))

        # For concrete requests such as "GDevelop 9 tema" we scan ALL files first,
        # not only the generic top-20. Otherwise a first topic may outrank the exact
        # topic just because it repeats the course name more often.
        if requested_number:
            exact_candidates: list[KBFileMatch] = []
            title_fallback_candidates: list[KBFileMatch] = []
            for file in self.files:
                if not _file_has_exact_lesson_number(file, requested_number):
                    continue
                if _file_has_conflicting_course(file, course_keys):
                    continue
                if not _is_lesson_file(file):
                    continue

                has_course_context = (not course_tokens) or _file_has_course_context(file, course_tokens)
                has_title_context = _file_matches_specific_title_tokens(file, query)

                if _file_has_stronger_conflicting_course(file, course_keys):
                    continue

                course_score = max((_file_strong_course_score(file, key) for key in course_keys), default=0)
                if has_course_context or course_score >= 10:
                    # A concrete course+number match should prefer the page whose actual
                    # lesson content belongs to the requested course, not the page that only
                    # happens to repeat a common word more often. This also supports short
                    # course names such as "C 3 тема", where the useful evidence is in code
                    # fences/includes rather than in the generic Notion path.
                    exact_candidates.append(self._match_from_file(file, query, max_chars=max_chars, score_bonus=500 + course_score * 20))
                elif has_title_context:
                    # Safe fallback: exact lesson number + exact human title words + no conflicting course.
                    # This fixes pages like "4 тема - Сбор ресурсов и счетчики" when the synced
                    # Notion path does not preserve the parent "GDevelop" course name.
                    title_fallback_candidates.append(self._match_from_file(file, query, max_chars=max_chars, score_bonus=420 + course_score * 10))
            if exact_candidates:
                exact_candidates.sort(key=lambda x: x.score, reverse=True)
                return exact_candidates[0]
            if title_fallback_candidates:
                title_fallback_candidates.sort(key=lambda x: x.score, reverse=True)
                return title_fallback_candidates[0]
            # A concrete numbered request must never silently fall back to another topic.
            return None

        matches = self.search_files(query, top_k=20, max_chars_per_file=max_chars)
        if not matches:
            return None

        if course_tokens:
            course_matches: list[KBFileMatch] = []
            for m in matches:
                tmp = KBFile(
                    source=m.source,
                    title=m.title,
                    text=m.text,
                    norm_title=_canonical_query(m.title + " " + m.source + " " + m.notion_path),
                    norm_text=_canonical_query(m.text + " " + m.source + " " + m.notion_path),
                    notion_url=m.notion_url,
                    notion_path=m.notion_path,
                )
                if (
                    not _file_has_conflicting_course(tmp, course_keys)
                    and not _file_has_stronger_conflicting_course(tmp, course_keys)
                    and _file_has_course_context(tmp, course_tokens)
                    and _is_lesson_file(tmp)
                ):
                    course_matches.append(m)
            if course_matches:
                course_matches.sort(key=lambda x: x.score, reverse=True)
                return course_matches[0]
            if explicit_lesson_query:
                return None

        best = matches[0]
        tmp_best = KBFile(
            source=best.source,
            title=best.title,
            text=best.text,
            norm_title=_canonical_query(best.title + " " + best.source + " " + best.notion_path),
            norm_text=_canonical_query(best.text + " " + best.source + " " + best.notion_path),
            notion_url=best.notion_url,
            notion_path=best.notion_path,
        )
        if _is_lesson_file(tmp_best) or best.score >= 140:
            return best
        return None

    def search(self, query: str, top_k: int = 6, max_chars: int = 9000) -> list[KBChunk]:
        qn = _canonical_query(query)
        words = _expanded_tokens(query)
        if not words:
            words = _tokenize(query, keep_stopwords=True)
        results: list[KBChunk] = []
        for chunk in self.chunks:
            cn = normalize(chunk.text + " " + chunk.source)
            score = 0
            if qn and qn in cn:
                score += 50
            for w in words:
                if w in cn:
                    score += 1 + min(8, cn.count(w))
            # Boost current facts and rules, but only for normal Q&A, not material search.
            if "11_current_facts" in chunk.source:
                score += 8
            if "agent_rules" in chunk.source or "regulations" in chunk.source:
                score += 4
            if _looks_like_lesson_query(query) and ("regulations" in chunk.source or "manager" in chunk.source):
                score -= 12
            if score > 0:
                results.append(KBChunk(source=chunk.source, text=chunk.text, score=score))
        results.sort(key=lambda c: c.score, reverse=True)
        selected: list[KBChunk] = []
        total = 0
        for r in results[: max(top_k * 3, top_k)]:
            if len(selected) >= top_k:
                break
            if total + len(r.text) > max_chars:
                r = KBChunk(source=r.source, text=trim(r.text, max(500, max_chars - total)), score=r.score)
            selected.append(r)
            total += len(r.text)
            if total >= max_chars:
                break
        return selected

    def build_context(self, chunks: list[KBChunk]) -> str:
        if not chunks:
            return ""
        parts = []
        for c in chunks:
            parts.append(f"Источник: {c.source}\n{c.text}")
        return "\n\n---\n\n".join(parts)

    def format_search_results(self, query: str, top_k: int = 8) -> str:
        matches = self.search_files(query, top_k=top_k, max_chars_per_file=900)
        if not matches:
            return "В базе знаний ничего не найдено по этому запросу."
        lines = ["🔎 Поиск по базе знаний", "", f"Запрос: {query}", ""]
        for idx, m in enumerate(matches, start=1):
            snippet = clean_kb_output(m.text)
            snippet = trim(snippet.replace("\n\n", "\n"), 700)
            lines.extend([
                f"{idx}. {m.title}",
                f"Источник: {m.source}",
                f"score: {m.score}",
                snippet,
                "",
            ])
        return "\n".join(lines).strip()


def _looks_like_lesson_query(query: str) -> bool:
    return query_has_lesson_intent(query)
