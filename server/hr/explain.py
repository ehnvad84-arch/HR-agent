"""Объяснение балла моделью — после фиксации транзакции резюме (S16, S19).

В match сразу лежит шаблонный разбор (model = 'template'). Эта задача берёт пары
выше порога, спрашивает модель вне транзакции, проверяет ответ и пишет его,
только если балл и версия профиля за это время не изменились.
"""
from __future__ import annotations

import json
import re

import httpx

from . import llm
from .config import get_config
from .db import ConnFactory, one, rows, run, setting
from .jobs import JobStats
from .pipeline import person_data

MAX_LEN = 1500
FAILS_TO_STOP = 3
_BAD = re.compile(r"https?://|www\.|<[a-z/!]|\]\(", re.I)
_SCORE_NEAR = re.compile(r"балл\w*\D{0,15}?(\d{1,3})", re.I)


def validate(reply: object, score: int) -> dict | None:
    """Ответ модели принимается, только если он ровно такой, как просили (угроза T1)."""
    if not isinstance(reply, dict) or set(reply) != {"why", "gap", "questions"}:
        return None
    why, gap, qs = reply["why"], reply["gap"], reply["questions"]
    if not (isinstance(why, str) and isinstance(gap, str) and isinstance(qs, list)
            and all(isinstance(q, str) for q in qs)):
        return None
    text = " ".join([why, gap, *qs])
    if len(text) > MAX_LEN or _BAD.search(text):
        return None
    if any(int(m) != score for m in _SCORE_NEAR.findall(text)):
        return None
    return {"why": why.strip(), "gap": gap.strip(), "questions": [q.strip() for q in qs[:3]]}


def payload(conn, m: dict) -> dict:
    """Только структурные поля: без имени, контактов и свободного текста (угрозы T1, T6)."""
    p = person_data(conn, m["person_id"])
    must = [r.get("text") for r in m["must_have"] or [] if isinstance(r, dict)]
    return {
        "profile": {"title": m["title"], "must_have": must,
                    "keywords": m["keywords"], "salary_max": m["salary_max"]},
        "resume": {"title": p.title, "city": p.city, "exp_years": p.exp_years, "employers": p.employers[:6],
                   "skills": p.skills[:40], "salary_expect": p.salary_expect},
        "score": m["score"],
        "features": m["features"],
    }


def explain_pending(conn_factory: ConnFactory, *, http: httpx.Client | None = None, **_) -> JobStats:
    stats = JobStats()
    if not get_config().llm_enabled:
        stats.note = "модель не настроена — разбор по шаблону"
        return stats
    system, version = llm.prompt("explain_score")
    model = f"{get_config().llm_model}/explain_score@{version}"
    with conn_factory() as conn:
        limit = int(setting(conn, "llm_explain_per_run", 500))
        threshold = int(setting(conn, "watch_threshold", 60))
        todo = rows(conn, """
            SELECT m.id, m.person_id, m.score, m.profile_version, m.features,
                   rp.title, rp.must_have, rp.keywords, rp.salary_max
            FROM match m JOIN role_profile rp ON rp.id = m.role_profile_id
            WHERE m.model = 'template' AND m.score >= %s AND rp.is_active AND rp.approved_at IS NOT NULL
            ORDER BY m.score DESC, m.id LIMIT %s""", (threshold, limit))
        jobs = [(m, payload(conn, m)) for m in todo]
    fails = 0
    for m, data in jobs:
        if fails >= FAILS_TO_STOP:
            stats.note = "модель не отвечает — остальные пары остались с шаблоном"
            break
        stats.seen += 1
        try:
            reply = llm.complete([{"role": "system", "content": system},
                                  {"role": "user", "content": json.dumps(data, ensure_ascii=False, default=str)}],
                                 http=http)
        except llm.LLMUnavailable:
            fails += 1
            stats.errors += 1
            continue
        fails = 0
        ok = validate(reply, m["score"])
        if ok is None:
            stats.skipped += 1
            continue
        text = "\n".join([ok["why"], ok["gap"], "Спросить: " + "; ".join(ok["questions"])])
        with conn_factory() as conn:
            cur = one(conn, "SELECT score, profile_version FROM match WHERE id = %s FOR UPDATE", (m["id"],))
            if cur and cur["score"] == m["score"] and cur["profile_version"] == m["profile_version"]:
                run(conn, "UPDATE match SET explanation = %s, model = %s WHERE id = %s", (text, model, m["id"]))
                stats.changed += 1
    return stats
