"""Задачи сбора резюме: hh (официальное API). Сбора со страниц нет (раздел 7 ТЗ, never.md)."""
from __future__ import annotations

import csv
import logging
from datetime import timedelta

import httpx

from . import hh, jobs, photos, pipeline
from .clock import SYSTEM, Clock
from .config import get_config
from .db import ConnFactory, j, one, rows, run, setting, val
from .http import PageBudget, RateLimiter, client
from .jobs import JobError, JobNotConfigured, JobStats
from .normalize import normalize, trudvsem_csv_row

log = logging.getLogger(__name__)

_limiter = RateLimiter()


def _queue(conn_factory: ConnFactory, source_id: int, profile_id: int | None, params: dict) -> int:
    with conn_factory() as conn:
        return val(conn, """INSERT INTO crawl_queue (source_id, role_profile_id, params, state, attempts)
                            VALUES (%s, %s, %s, 'running', 1) RETURNING id""", (source_id, profile_id, j(params)))


def _finish(conn_factory: ConnFactory, qid: int, ok: bool, error: str | None = None) -> None:
    with conn_factory() as conn:
        run(conn, "UPDATE crawl_queue SET state = %s, last_error = %s, finished_at = now() WHERE id = %s",
            ("done" if ok else "error", error, qid))


class _HHItems:
    """Приём одного элемента выдачи с экономным запросом полного резюме (S32)."""

    def __init__(self, conn_factory, api: hh.Api, src: dict, profiles: list[dict], stats: JobStats,
                 full_left: int, clock: Clock):
        self.cf, self.api, self.src, self.profiles, self.stats = conn_factory, api, src, profiles, stats
        self.full_left, self.clock = full_left, clock
        self.full_done = 0

    def handle(self, item: dict) -> None:
        self.stats.seen += 1
        with self.cf() as conn:
            old = one(conn, "SELECT raw, person_id FROM observation WHERE source_id = %s AND external_id = %s",
                      (self.src["id"], str(item["id"])))
            # выдача поиска без навыков: накладываем её на сохранённое полное резюме, чтобы не было ложных изменений
            merged = {**old["raw"], **item} if old else item
            updated = not old or old["raw"].get("updated_at") != item.get("updated_at")
            # skill_set есть только в полном резюме: без него полное ещё не получено (например, упёрлись в лимит)
            want = (updated or "skill_set" not in old["raw"]) and (bool(old and old["person_id"]) or
                                pipeline.passes_threshold(conn, normalize("hh", merged), self.profiles,
                                                          partial="skill_set" not in merged) is not None)
        raw = merged
        if want and self.full_left > 0:
            try:
                full = self.api.get(f"/resumes/{item['id']}")
            except hh.ViewLimitExceeded as e:
                self.full_left = 0
                self.stats.note = f"{e} — дальше по выдаче поиска, без полных резюме"
            else:
                self.full_left -= 1
                self.full_done += 1
                raw = {**merged, **full}
        with self.cf() as conn:
            r = pipeline.process(conn, self.src, normalize("hh", raw), raw, self.clock.now())
        self.stats.new += int(r.new)
        self.stats.changed += int(r.changed)
        self.stats.persons += int(r.person_created)
        self.stats.alerts += r.alerts
        jobs.progress(full_done=self.full_done, seen=self.stats.seen, new=self.stats.new, changed=self.stats.changed)


def collect_hh(conn_factory: ConnFactory, *, http: httpx.Client | None = None, clock: Clock | None = None,
               limiter: RateLimiter | None = None, full_cap: int | None = None, profile_id: int | None = None,
               area: str | None = None, **_) -> JobStats:
    """Обход hh по действующим профилям. full_cap — предел полных резюме вместо hh_full_resume_per_run
    (остаток дневного лимита просмотров); предел страниц тогда — поиск по всем профилям плюс full_cap.
    profile_id — искать только по этой должности; area — регион hh вместо региона из портрета (S46).
    Балл и порог считаются по всем действующим портретам, как при обычном обходе."""
    clock = clock or SYSTEM
    http = http or client(get_config().hh_user_agent)
    stats = JobStats()
    with conn_factory() as conn:
        src = one(conn, "SELECT * FROM source WHERE code = 'hh'")
        if not src or not src["is_active"]:
            raise JobNotConfigured("источник hh выключен или не заведён")
        profiles = pipeline.current_profiles(conn)
        per_profile = int(setting(conn, "crawl_per_profile_pages", 5))
        full_left = int(setting(conn, "hh_full_resume_per_run", 200)) if full_cap is None else full_cap
        max_pages = int(setting(conn, "crawl_max_pages", 2000)) if full_cap is None else \
            len(profiles) * per_profile + full_cap
        budget = PageBudget(max_pages, stats)
        params = {p["id"]: hh.search_params(conn, p) for p in profiles}
    targets = profiles
    if profile_id is not None:
        targets = [p for p in profiles if p["id"] == profile_id]
        if not targets:
            raise JobNotConfigured("портрет должности не утверждён или не действует")
        if full_cap is not None:   # поиск только по этой должности — и предел страниц под неё
            budget.limit = len(targets) * per_profile + full_left
    if area:
        for p in targets:
            params[p["id"]]["area"] = str(area)
            params[p["id"]].pop("relocation", None)
    api = hh.Api(conn_factory, http, clock, limiter or _limiter, budget)
    items = _HHItems(conn_factory, api, src, profiles, stats, full_left, clock)
    for i, pr in enumerate(targets, 1):
        jobs.progress(force=True, stage="search", full_total=full_left, full_done=items.full_done,
                      profile_index=i, profiles=len(targets), profile_title=pr["title"])
        qid = _queue(conn_factory, src["id"], pr["id"], params[pr["id"]])
        try:
            for page in range(per_profile):
                data = api.get("/resumes", {**params[pr["id"]], "page": page})
                for item in data.get("items", []):
                    try:
                        items.handle(item)
                    except (hh.BudgetExhausted, JobError):
                        raise
                    except Exception:  # noqa: BLE001 — одно битое резюме не роняет обход
                        log.exception("резюме hh не принято")
                        stats.errors += 1
                if page + 1 >= int(data.get("pages", 0)):
                    break
            _finish(conn_factory, qid, True)
        except hh.BudgetExhausted:
            _finish(conn_factory, qid, True, stats.note)
            break
        except JobError as e:
            _finish(conn_factory, qid, False, str(e))
            raise
        except httpx.HTTPError as e:
            _finish(conn_factory, qid, False, f"hh не ответил: {type(e).__name__}")
            stats.errors += 1
    return stats


def _disappear(conn_factory: ConnFactory, obs_id: int, now) -> None:
    with conn_factory() as conn:
        o = one(conn, "UPDATE observation SET is_gone = true WHERE id = %s RETURNING person_id", (obs_id,))
        if o and o["person_id"]:
            pipeline.add_event(conn, obs_id, o["person_id"], "disappeared", {}, now)   # сигнал — задачей alerts


def mark_gone(conn_factory: ConnFactory, *, http: httpx.Client | None = None, clock: Clock | None = None,
              limiter: RateLimiter | None = None, **_) -> JobStats:
    """Резюме не видно дольше stale_observation_days (S11). hh перепроверяется по id в пределах
    hh_recheck_per_run: из-за предела страниц на профиль «не видели» ещё не значит «пропало»."""
    clock = clock or SYSTEM
    now = clock.now()
    stats = JobStats()
    with conn_factory() as conn:
        days = int(setting(conn, "stale_observation_days", 45))
        recheck = int(setting(conn, "hh_recheck_per_run", 100))
        budget = PageBudget(int(setting(conn, "crawl_max_pages", 2000)), stats)
        stale = rows(conn, """
            SELECT o.id, o.external_id, s.code FROM observation o JOIN source s ON s.id = o.source_id
            WHERE s.mode = 'api' AND NOT o.is_gone AND o.last_seen < %s
            ORDER BY o.last_seen""", (now - timedelta(days=days),))
    api = hh.Api(conn_factory, http or client(get_config().hh_user_agent), clock, limiter or _limiter, budget)
    for o in stale:
        stats.seen += 1
        if o["code"] != "hh":
            _disappear(conn_factory, o["id"], now)
            stats.changed += 1
            continue
        if recheck <= 0:
            stats.skipped += 1
            continue
        recheck -= 1
        try:
            api.get(f"/resumes/{o['external_id']}")
        except hh.BudgetExhausted:
            break
        except hh.ViewLimitExceeded as e:
            stats.note = f"{e} — перепроверка продолжится в следующий прогон"
            recheck = 0   # остальные hh — в следующий прогон, другие API-источники обрабатываются дальше
            continue
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 410):
                _disappear(conn_factory, o["id"], now)
                stats.changed += 1
            else:
                stats.errors += 1
            continue
        with conn_factory() as conn:
            run(conn, "UPDATE observation SET last_seen = %s WHERE id = %s", (now, o["id"]))
    return stats


def collect_hh_daily(conn_factory: ConnFactory, *, http: httpx.Client | None = None, clock: Clock | None = None,
                     limiter: RateLimiter | None = None, count: int | None = None, profile_id: int | None = None,
                     area: str | None = None, **_) -> JobStats:
    """Задача collect_hh_daily (18:00 по Москве) и кнопка «Загрузить резюме»: обход hh с полными резюме
    на остаток дневного лимита просмотров (count — не больше остатка), затем фото новых людей."""
    clock = clock or SYSTEM
    http = http or client(get_config().hh_user_agent)
    limiter = limiter or _limiter
    left = hh.limits_today(conn_factory, http, clock.now(), limiter=limiter)["views_left"]
    cap = left if count is None else min(count, left)
    jobs.progress(force=True, stage="start", full_total=max(cap, 0), full_done=0)
    if cap <= 0:
        stats = JobStats(note="дневной лимит просмотров hh исчерпан — сбор завтра")
        stats.extra = {"views_left": left}
        return stats
    stats = collect_hh(conn_factory, http=http, clock=clock, limiter=limiter, full_cap=cap, profile_id=profile_id,
                       area=area)
    stats.extra = {**stats.extra, "views_left": left, "full_cap": cap}
    # фото — тем же проходом: новые карточки сразу с лицом
    jobs.progress(force=True, stage="photos")
    stats.extra["photos"] = photos.fetch_photos(conn_factory, clock=clock, limiter=limiter).new
    return stats


# ------------------------------------------------------------------ «Работа России»

TRUDVSEM_CV = "https://opendata.trudvsem.ru/csv/cv.csv"


def _csv_lines(response: httpx.Response):
    """Строки потока с концами строк: csv сам склеит поля с переносами внутри кавычек."""
    tail = ""
    for chunk in response.iter_text():
        tail += chunk
        *lines, tail = tail.split("\n")
        for line in lines:
            yield line + "\n"
    if tail:
        yield tail


def collect_trudvsem(conn_factory: ConnFactory, *, http: httpx.Client | None = None, clock: Clock | None = None,
                     **_) -> JobStats:
    """Задача collect_trudvsem (ночь с понедельника на вторник): открытые данные «Работы России» — один файл
    резюме по всей стране (около 32 ГБ), читается потоком без сохранения. В базу идут только резюме региона
    trudvsem_region, обновлённые за trudvsem_max_age_days, и только прошедшие порог по действующим портретам."""
    clock = clock or SYSTEM
    now = clock.now()
    http = http or client(get_config().hh_user_agent)
    stats = JobStats()
    with conn_factory() as conn:
        src = one(conn, "SELECT * FROM source WHERE code = 'trudvsem'")
        if not src or not src["is_active"]:
            raise JobNotConfigured("источник «Работа России» выключен")
        region = str(setting(conn, "trudvsem_region", "2300000000000"))
        oldest = now - timedelta(days=int(setting(conn, "trudvsem_max_age_days", 365)))
        profiles = pipeline.current_profiles(conn)
    if not profiles:
        stats.note = "нет утверждённых портретов должностей"
        return stats
    csv.field_size_limit(16 * 1024 * 1024)
    with http.stream("GET", TRUDVSEM_CV, timeout=httpx.Timeout(60.0, read=600.0)) as r:
        r.raise_for_status()
        rows = csv.reader(_csv_lines(r), delimiter="|")
        header = next(rows, None)
        if not header or "stateRegionCode" not in header:
            raise JobNotConfigured("формат выгрузки резюме «Работы России» изменился")
        col = header.index("stateRegionCode")
        for cells in rows:
            if len(cells) != len(header) or cells[col] != region:
                continue
            stats.seen += 1
            try:
                raw = trudvsem_csv_row(dict(zip(header, cells, strict=True)))
                n = normalize("trudvsem", raw)
                if not n.updated_at_src or n.updated_at_src < oldest:
                    stats.skipped += 1
                    continue
                with conn_factory() as conn:
                    if pipeline.passes_threshold(conn, n, profiles) is None:
                        stats.skipped += 1
                        continue
                    res = pipeline.process(conn, src, n, raw, now)
                stats.new += int(res.new)
                stats.changed += int(res.changed)
                stats.persons += int(res.person_created)
                stats.alerts += res.alerts
            except Exception:  # noqa: BLE001 — одно битое резюме не роняет выгрузку
                log.exception("резюме «Работы России» не принято")
                stats.errors += 1
    stats.extra = {"region": region}
    return stats
