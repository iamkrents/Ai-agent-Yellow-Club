from __future__ import annotations

import json
import urllib.request
import urllib.error
from dataclasses import dataclass


@dataclass
class LLMResult:
    ok: bool
    text: str
    error: str = ""


class OllamaClient:
    def __init__(self, url: str, model: str, timeout: int = 180):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate(self, prompt: str, system: str = "") -> LLMResult:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {
                "temperature": 0.25,
                "top_p": 0.9,
                "num_ctx": 8192,
            },
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.url}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            parsed = json.loads(raw)
            text = (parsed.get("response") or "").strip()
            return LLMResult(ok=bool(text), text=text)
        except Exception as e:
            return LLMResult(ok=False, text="", error=str(e))

    def ping(self) -> LLMResult:
        return self.generate("Ответь одним словом: ok")
