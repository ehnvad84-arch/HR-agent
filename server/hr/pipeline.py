"""Конвейер: наблюдение → версия и дифф → события → порог → сшивка → балл → сигналы.

Узлы 1–5 раздела 3.2 архитектуры. Функции работают внутри переданной транзакции:
одно резюме применяется целиком или никак. Модель здесь не вызывается (решение
спеки «модель вне транзакции»): объяснения досчитывает explain после фиксации.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import psycopg

from .db import j, one, run, setting, val
from .normalize import Norm

# Поля наблюдения, которые сравниваются между версиями
TRACKED = ("title", "city", "employer_current", "employers", "skills", "exp_years", "avg_tenure_months",
           "salary_expect", "relocation_ready", "open_for_search", "updated_at_src")


@dataclass
class ObsResult:
    id: int
    new: bool
    changed: bool
    person_id: int | None
    was_gone: bool = False
    diff: dict = field(default_factory=dict)


def _fields(n: Norm) -> dict:
    return {k: getattr(n, k) for k in TRACKED} | {"url": n.url}


def _aware(d: datetime) -> datetime:
    """Дата без пояса из файла выгрузки считается UTC, как её сохранит timestamptz."""
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _same(a, b) -> bool:
    if isinstance(a, datetime) and isinstance(b, datetime):
        return _aware(a) == _aware(b)
    if a is not None and b is not None and isinstance(b, float):
        return abs(float(a) - b) < 0.05
    if a in (None, "", []) and b in (None, "", []):
        return True
    return a == b


def diff_of(old: dict, n: Norm) -> dict:
    out: dict = {}
    for k in TRACKED:
        a, b = old.get(k), getattr(n, k)
        if k in ("employers", "skills"):
            sa, sb = set(a or []), set(b or [])
            if sa != sb:
                out[k] = {"added": sorted(sb - sa), "removed": sorted(sa - sb)}
        elif not _same(a, b):
            out[k] = {"from": a.isoformat() if isinstance(a, datetime) else a,
                      "to": b.isoformat() if isinstance(b, datetime) else b}
    return out


def events_from_diff(diff: dict, keywords: set[str]) -> list[tuple[str, dict]]:
    """Дифф версии → события obs_event (S10). resume_updated — только если других нет."""
    ev: list[tuple[str, dict]] = []
    if diff.get("employer_current", {}).get("to"):
        ev.append(("employer_changed", diff["employer_current"]))
    if "salary_expect" in diff:
        ev.append(("salary_changed", diff["salary_expect"]))
    if diff.get("open_for_search", {}).get("to") is True:
        ev.append(("opened_for_search", {}))
    added = diff.get("skills", {}).get("added", [])
    kw = {k.lower() for k in keywords}
    hit = [s for s in added if s.lower() in kw or any(len(k) > 3 and k in s.lower() for k in kw)]
    if hit:
        ev.append(("skill_added", {"skills": hit}))
    if "city" in diff or diff.get("relocation_ready", {}).get("to") is True:
        ev.append(("geo_changed", {k: diff[k] for k in ("city", "relocation_ready") if k in diff}))
    if not ev and diff:
        ev.append(("resume_updated", {"fields": sorted(diff)}))
    return ev


def upsert_observation(conn: psycopg.Connection, source: dict, n: Norm, raw: dict, now: datetime) -> ObsResult:
    """Записывает резюме. Неизменное — только last_seen; изменившееся — новая версия с диффом."""
    keep = (now + timedelta(days=30 * int(setting(conn, "retention_observation_months", 12)))).date()
    h = n.content_hash()
    old = one(conn, "SELECT * FROM observation WHERE source_id = %s AND external_id = %s FOR UPDATE",
              (source["id"], n.external_id))
    f = _fields(n) | {"raw": j(raw), "hash": h, "keep": keep, "now": now}
    if old is None:
        oid = val(conn, """
            INSERT INTO observation (source_id, external_id, url, title, city, employer_current, employers, skills,
                exp_years, avg_tenure_months, salary_expect, relocation_ready, open_for_search, updated_at_src,
                raw, content_hash, retention_until, first_seen, last_seen)
            VALUES (%(sid)s, %(ext)s, %(url)s, %(title)s, %(city)s, %(employer_current)s, %(employers)s, %(skills)s,
                %(exp_years)s, %(avg_tenure_months)s, %(salary_expect)s, %(relocation_ready)s, %(open_for_search)s,
                %(updated_at_src)s, %(raw)s, %(hash)s, %(keep)s, %(now)s, %(now)s)
            RETURNING id""", f | {"sid": source["id"], "ext": n.external_id})
        run(conn, "INSERT INTO obs_version (observation_id, content_hash, raw, captured_at) VALUES (%s, %s, %s, %s)",
            (oid, h, j(raw), now))
        return ObsResult(oid, True, False, None)
    if old["content_hash"] == h:
        run(conn, "UPDATE observation SET last_seen = %s, is_gone = false, retention_until = %s WHERE id = %s",
            (now, keep, old["id"]))
        return ObsResult(old["id"], False, False, old["person_id"], old["is_gone"])
    diff = diff_of(old, n)
    run(conn, """
        UPDATE observation SET url = %(url)s, title = %(title)s, city = %(city)s,
            employer_current = %(employer_current)s, employers = %(employers)s, skills = %(skills)s,
            exp_years = %(exp_years)s, avg_tenure_months = %(avg_tenure_months)s, salary_expect = %(salary_expect)s,
            relocation_ready = %(relocation_ready)s, open_for_search = %(open_for_search)s,
            updated_at_src = %(updated_at_src)s, raw = %(raw)s, content_hash = %(hash)s,
            last_seen = %(now)s, is_gone = false, retention_until = %(keep)s
        WHERE id = %(id)s""", f | {"id": old["id"]})
    run(conn, """INSERT INTO obs_version (observation_id, content_hash, raw, diff, captured_at)
                 VALUES (%s, %s, %s, %s, %s)""", (old["id"], h, j(raw), j(diff), now))
    return ObsResult(old["id"], False, bool(diff), old["person_id"], old["is_gone"], diff)
