"""Профессиональные следы в сети (раздел 7.2 ТЗ, S28–S30).

GitHub — через официальное API. Остальные площадки из social_sources в core не
подключены: по ним пишется результат «источник не подключён», чтобы в досье было
видно, где не искали. Берётся только профессиональное: репозитории, языки, активность.
"""
from __future__ import annotations

import logging
from datetime import datetime

import httpx

from . import names, pipeline
from .clock import SYSTEM, Clock
from .config import get_config
from .db import ConnFactory, j, rows, run, setting
from .http import RateLimiter, client
from .jobs import JobStats

log = logging.getLogger(__name__)

GITHUB = "https://api.github.com"
NOT_CONNECTED = "источник не подключён"
MAX_NAME_QUERIES = 4
MAX_NAMESAKES = 3

_limiter = RateLimiter()


class GitHubLimited(Exception):
    """Исчерпан лимит GitHub: до следующей ночи не ищем, отрицательных результатов не пишем."""


class GitHub:
    def __init__(self, http: httpx.Client, limiter: RateLimiter):
        self.http, self.limiter = http, limiter
        token = get_config().github_token
        self.headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.search_interval = 2.0 if token else 6.0     # поиск: 30 запросов в минуту с токеном, 10 без

    def _get(self, path: str, params: dict | None = None, search: bool = False) -> httpx.Response | None:
        if search:
            self.limiter.wait("github_search", self.search_interval)
        else:
            self.limiter.wait("github")
        r = self.http.get(f"{GITHUB}{path}", params=params, headers=self.headers)
        limited = r.headers.get("X-RateLimit-Remaining") == "0" or "Retry-After" in r.headers
        if r.status_code in (403, 429) and limited:
            raise GitHubLimited
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r

    def user(self, login: str) -> dict | None:
        r = self._get(f"/users/{login}")
        return r.json() if r else None

    def search(self, query: str) -> list[str]:
        r = self._get("/search/users", {"q": query, "per_page": MAX_NAMESAKES + 1}, search=True)
        return [i["login"] for i in r.json().get("items", [])] if r else []

    def repos(self, login: str) -> list[dict]:
        r = self._get(f"/users/{login}/repos", {"sort": "pushed", "per_page": 10})
        return r.json() if r else []


def _profile_row(gh: GitHub, login: str, user: dict) -> dict:
    repos = [x for x in gh.repos(login) if not x.get("fork")]
    langs = list(dict.fromkeys(x["language"] for x in repos if x.get("language")))
    pushed = max((x["pushed_at"] for x in repos if x.get("pushed_at")), default=None)
    last = pushed[:10] if pushed else None
    summary = f"{user.get('public_repos', 0)} репозиториев"
    if langs:
        summary += ", языки: " + ", ".join(langs[:5])
    if last:
        summary += f", последняя активность {last}"
    return {"url": user.get("html_url") or f"https://github.com/{login}", "handle": login, "topics": langs[:10],
            "summary": summary, "last_activity": last, "since": (user.get("created_at") or "")[:10] or None,
            "activity": f"{len(repos)} своих репозиториев за последние обновления", "raw": user}


def _save_profile(conn, person_id: int, p: dict, confidence: str, matched_by: list[str], found_by: str,
                  now: datetime) -> None:
    run(conn, """
        INSERT INTO social_profile (person_id, kind, url, handle, found_by, profile_since, activity, topics,
            confidence, matched_by, summary, last_activity, raw, first_seen, last_seen)
        VALUES (%s, 'github', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (person_id, url) DO UPDATE SET topics = EXCLUDED.topics, summary = EXCLUDED.summary,
            activity = EXCLUDED.activity, last_activity = EXCLUDED.last_activity, raw = EXCLUDED.raw,
            last_seen = EXCLUDED.last_seen,
            confidence = CASE WHEN social_profile.confidence = 'rejected' THEN social_profile.confidence
                              ELSE EXCLUDED.confidence END,
            matched_by = EXCLUDED.matched_by""",
        (person_id, p["url"], p["handle"], found_by, p["since"], p["activity"], p["topics"], confidence,
         matched_by, p["summary"], p["last_activity"], j(p["raw"]), now, now))


def _search_result(conn, person_id: int, kind: str, queries: list[str], found: bool, note: str | None,
                   now: datetime) -> None:
    run(conn, """INSERT INTO social_search (person_id, kind, queries, found, note, checked_at)
                 VALUES (%s, %s, %s, %s, %s, %s)
                 ON CONFLICT (person_id, kind) DO UPDATE SET queries = EXCLUDED.queries, found = EXCLUDED.found,
                     note = EXCLUDED.note, checked_at = EXCLUDED.checked_at""",
        (person_id, kind, queries, found, note, now))


def _search_github(conn_factory: ConnFactory, gh: GitHub, person: dict, now: datetime) -> bool:
    """True — нашли хотя бы подтверждённый профиль (нужен пересчёт балла)."""
    queries: list[str] = []
    found: list[tuple[str, dict, str, list[str]]] = []
    for nick in person["nicknames"]:
        queries.append(f"user:{nick}")
        user = gh.user(nick)
        if user:
            found.append((nick, user, "confirmed", ["nick"]))
            break
    if not found:
        city = names.translit(person["city"]).split(",")[0].strip() if person["city"] else None
        variants = [v for v in person["name_variants"] if "." not in v and "_" not in v][:MAX_NAME_QUERIES]
        for v in variants:
            q = f'"{v}" in:fullname' + (f" location:{city}" if city else "")
            queries.append(q)
            logins = gh.search(q)
            if not logins:
                continue
            single = len(logins) == 1 and city
            for login in logins[:MAX_NAMESAKES]:
                user = gh.user(login)
                if user:
                    found.append((q, user, "confirmed" if single else "probable",
                                  ["name", "city"] if city else ["name"]))
            break
    rows_ = [(q, _profile_row(gh, u["login"], u), conf, by) for q, u, conf, by in found]
    with conn_factory() as conn:
        for q, p, conf, by in rows_:
            _save_profile(conn, person["id"], p, conf, by, q, now)
        _search_result(conn, person["id"], "github", queries, bool(rows_), None, now)
    return any(conf == "confirmed" for _, _, conf, _ in rows_)


def collect_social(conn_factory: ConnFactory, *, http: httpx.Client | None = None, clock: Clock | None = None,
                   limiter: RateLimiter | None = None, person_ids: list[int] | None = None, **_) -> JobStats:
    clock = clock or SYSTEM
    now = clock.now()
    stats = JobStats()
    gh = GitHub(http or client("RuwareHR/1.0"), limiter or _limiter)
    with conn_factory() as conn:
        if not setting(conn, "social_search_enabled", True):
            stats.note = "поиск следов выключен настройкой social_search_enabled"
            return stats
        sources = list(setting(conn, "social_sources", []))
        limit = int(setting(conn, "social_max_persons_per_run", 500))
        people = rows(conn, """
            SELECT p.id, p.city, p.name_variants, p.nicknames FROM person p
            WHERE p.watch <> 'dropped' AND (%(ids)s::bigint[] IS NULL OR p.id = ANY(%(ids)s))
              AND (%(ids)s::bigint[] IS NOT NULL
                   OR (SELECT count(*) FROM social_search s WHERE s.person_id = p.id) < %(n)s
                   OR p.last_event_at > (SELECT min(checked_at) FROM social_search s WHERE s.person_id = p.id))
            ORDER BY p.last_event_at DESC NULLS LAST LIMIT %(limit)s""",
            {"ids": person_ids, "n": len(sources), "limit": limit})
    github_on = "github" in sources
    for p in people:
        stats.seen += 1
        with conn_factory() as conn:
            for kind in sources:
                if kind != "github":
                    _search_result(conn, p["id"], kind, [], False, NOT_CONNECTED, now)
        if not github_on:
            continue
        try:
            confirmed = _search_github(conn_factory, gh, p, now)
        except GitHubLimited:
            stats.note = "лимит GitHub исчерпан — остальные люди будут найдены в следующий прогон"
            github_on = False
            continue
        except httpx.HTTPError:
            log.warning("GitHub не ответил по человеку %s", p["id"])
            stats.errors += 1
            continue
        if confirmed:
            stats.changed += 1
            with conn_factory() as conn:
                pipeline.rescore_person(conn, p["id"])
    return stats
