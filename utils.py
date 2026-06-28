from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Optional


def clean_text(text: str) -> str:
    text = text or ""
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def remove_markdown_noise(text: str) -> str:
    if not text:
        return ""
    text = text.replace("**", "")
    text = text.replace("###", "")
    text = text.replace("##", "")
    return text.strip()


def normalize(text: str) -> str:
    return clean_text(text).lower().replace("ё", "е")


def trim(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + "..."


def chunk_telegram(text: str, limit: int = 3900) -> list[str]:
    text = text or ""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for para in text.split("\n"):
        if len(current) + len(para) + 1 <= limit:
            current += ("\n" if current else "") + para
        else:
            if current:
                chunks.append(current)
            while len(para) > limit:
                chunks.append(para[:limit])
                para = para[limit:]
            current = para
    if current:
        chunks.append(current)
    return chunks


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def extract_command(text: str) -> tuple[Optional[str], str]:
    """Return command without slash/bot suffix and args."""
    text = clean_text(text)
    if not text.startswith("/"):
        return None, text
    first, _, rest = text.partition(" ")
    cmd = first[1:]
    if "@" in cmd:
        cmd = cmd.split("@", 1)[0]
    return cmd.lower(), rest.strip()


def safe_html(text: str) -> str:
    return html.escape(text or "")
