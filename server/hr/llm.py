"""Языковая модель через OpenAI-совместимый интерфейс (AD-5, раздел 6 ТЗ).

Единственная точка интеграции — complete(messages). Текст ответа модели в
исключения и логи не попадает (security.md).
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import httpx

from .config import get_config

PROMPTS = Path(__file__).parent / "prompts"


class LLMUnavailable(RuntimeError):
    pass


@cache
def prompt(code: str) -> tuple[str, str]:
    """(текст промпта, версия = первые 8 знаков sha256): версия пишется в match.model."""
    text = (PROMPTS / f"{code}.md").read_text(encoding="utf-8").strip()
    return text, hashlib.sha256(text.encode()).hexdigest()[:8]


def _json(text: str) -> Any:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    text = (m.group(1) if m else text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if 0 <= a < b:
            return json.loads(text[a:b + 1])
        raise


def complete(messages: list[dict], *, http: httpx.Client | None = None, max_tokens: int = 700) -> Any:
    cfg = get_config()
    if not cfg.llm_enabled:
        raise LLMUnavailable("модель не настроена: LLM_BASE_URL и LLM_MODEL пусты")
    client = http or httpx.Client(timeout=cfg.llm_timeout_s)
    url = cfg.llm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.llm_api_key}"} if cfg.llm_api_key else {}
    body: dict[str, Any] = {"model": cfg.llm_model, "messages": messages, "temperature": cfg.llm_temperature,
                            "max_tokens": max_tokens, "response_format": {"type": "json_object"}}
    try:
        r = client.post(url, json=body, headers=headers)
        if r.status_code == 400:
            # часть шлюзов не знает response_format — один повтор без него
            body.pop("response_format")
            r = client.post(url, json=body, headers=headers)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        raise LLMUnavailable(f"модель не ответила: {type(e).__name__}") from None
    try:
        return _json(content)
    except (json.JSONDecodeError, ValueError):
        raise LLMUnavailable("модель вернула не JSON") from None
