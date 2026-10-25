"""Языковая модель через OpenAI-совместимый интерфейс (раздел 6 ТЗ).

Единственная точка интеграции: complete(messages, schema) -> dict | str.
Модель объясняет посчитанное и пишет черновики текстов; балл она не считает.
Если модель не настроена или недоступна, вызывающий код собирает текст по шаблону.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from .config import get_config

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    pass


# Промпты по умолчанию. При первом запуске они кладутся в agent_prompt версией 1,
# дальше действует активная версия из базы — её правят без выкладки кода.
DEFAULT_PROMPTS: dict[str, str] = {
    "explain_score": (
        "Ты помощник рекрутера компании-производителя IP-камер в Краснодаре. Тебе дают профиль должности, "
        "резюме из открытого источника и уже посчитанные кодом признаки с баллами. Балл не пересчитывай и "
        "не спорь с ним. Напиши по-русски коротко, без канцелярита: почему балл такой (2–3 предложения), "
        "чего не хватает до профиля, и 2–3 вопроса для первого разговора. Не делай выводов о возрасте, "
        "поле, семье, здоровье, национальности. Ответ строго JSON: "
        '{"why": "...", "gap": "...", "questions": ["...", "..."]}'
    ),
    "first_message": (
        "Напиши первое сообщение кандидату от рекрутера компании «Рувер» (российский разработчик и "
        "производитель IP-камер, Краснодар). Тон уважительный и деловой, без восторгов и давления, "
        "5–8 предложений: кто пишет, какая вакансия и чем предстоит заниматься, почему пишем именно ему "
        "(по фактам из резюме), график и адрес, предложение коротко созвониться. Зарплату не называй. "
        'Ответ строго JSON: {"subject": "...", "body": "..."}'
    ),
    "draft_extract": (
        "Руководитель подразделения описывает, кого ему нужно нанять. Извлеки поля черновика заявки. "
        "Неизвестное оставь null, ничего не придумывай. Ответ строго JSON: "
        '{"title": str|null, "headcount": int|null, "wanted_by": "YYYY-MM-DD"|null, '
        '"reason": str|null, "salary_min": int|null, "salary_max": int|null, '
        '"must_have": [str], "nice_to_have": [str], "stop_factors": [str], "keywords": [str]}'
    ),
    "criteria_from_reasons": (
        "Руководитель отметил кандидатов «нравится» и «не нравится» и объяснил почему. Предложи правку "
        "критериев подбора: что добавить в обязательные требования, в стоп-факторы и в ключевые слова. "
        "Только то, что прямо следует из причин. Ответ строго JSON: "
        '{"add_must": [str], "add_stop": [str], "add_keywords": [str], "text": "вопрос руководителю одной фразой"}'
    ),
    "feedback_group": (
        "Это сообщения рекрутеров о работе агента подбора за неделю. Сгруппируй повторяющееся и "
        "предложи изменения. Каждое предложение — одно конкретное изменение одного из видов: "
        'weight (вес признака в профиле), stop_factor, keyword, threshold (настройка), template (шаблон письма), '
        "note (нужно обсудить, автоматически не применяется). Ответ строго JSON: "
        '{"proposals": [{"title": "...", "rationale": "на скольких сообщениях и почему", '
        '"question": "вопрос руководителю", "message_ids": [int], '
        '"target": {"kind": "weight|stop_factor|keyword|threshold|template|note", '
        '"role_code": str|null, "feature": str|null, "key": str|null, "value": any}}]}'
    ),
    "di_parse": (
        "Тебе дают текст должностной инструкции. Извлеки критерии отбора. Ответ строго JSON: "
        '{"title": str, "must_have": [str], "nice_to_have": [str], "tools": [str], '
        '"education": str|null, "reports_to": str|null, "keywords": [str]}'
    ),
}


def prompt(conn, code: str) -> str:
    from .db import one
    r = one(conn, "SELECT body FROM agent_prompt WHERE code = %s AND is_active ORDER BY version DESC LIMIT 1", (code,))
    return r["body"] if r else DEFAULT_PROMPTS[code]


def seed_prompts(conn) -> None:
    from .db import run
    for code, body in DEFAULT_PROMPTS.items():
        run(conn, "INSERT INTO agent_prompt (code, version, body) VALUES (%s, 1, %s) ON CONFLICT DO NOTHING",
            (code, body))


def model_name() -> str:
    cfg = get_config()
    return cfg.llm_model if cfg.llm_enabled else "template"


def _extract_json(text: str) -> Any:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def complete(messages: list[dict], as_json: bool = True, max_tokens: int | None = None) -> Any:
    cfg = get_config()
    if not cfg.llm_enabled:
        raise LLMUnavailable("LLM_BASE_URL и LLM_MODEL не заданы")
    if cfg.llm_provider not in ("openai", ""):
        raise LLMUnavailable(f"адаптер {cfg.llm_provider} не подключён: допишите hr/llm_{cfg.llm_provider}.py")
    body: dict[str, Any] = {
        "model": cfg.llm_model,
        "messages": messages,
        "temperature": cfg.llm_temperature,
        "max_tokens": max_tokens or cfg.llm_max_tokens,
    }
    if as_json:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {cfg.llm_api_key}"} if cfg.llm_api_key else {}
    url = cfg.llm_base_url.rstrip("/") + "/chat/completions"
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=cfg.llm_timeout_s)
        if r.status_code == 400 and as_json:
            # часть шлюзов не знает response_format — повторяем без него
            body.pop("response_format", None)
            r = httpx.post(url, json=body, headers=headers, timeout=cfg.llm_timeout_s)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        raise LLMUnavailable(f"модель не ответила: {e}") from e
    if not as_json:
        return text
    try:
        return _extract_json(text)
    except (json.JSONDecodeError, ValueError) as e:
        raise LLMUnavailable(f"модель вернула не JSON: {text[:200]}") from e


def ask(conn, code: str, payload: Any, max_tokens: int | None = None) -> Any:
    """Системный промпт из agent_prompt плюс данные в JSON."""
    return complete([
        {"role": "system", "content": prompt(conn, code)},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ], max_tokens=max_tokens)


def health() -> str:
    cfg = get_config()
    if not cfg.llm_enabled:
        return "off"
    try:
        headers = {"Authorization": f"Bearer {cfg.llm_api_key}"} if cfg.llm_api_key else {}
        r = httpx.get(cfg.llm_base_url.rstrip("/") + "/models", headers=headers, timeout=10)
        return "ok" if r.status_code < 500 else f"error {r.status_code}"
    except httpx.HTTPError as e:
        return f"error {type(e).__name__}"
