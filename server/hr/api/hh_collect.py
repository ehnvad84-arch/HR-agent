"""Ручная загрузка резюме с hh в пределах дневного лимита (S44). Тратит лимит — только руководителю подбора."""
from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import auth, collect, hh, jobs, pipeline
from ..clock import Clock
from ..config import get_config
from ..db import ConnFactory, one
from ..deps import get_clock, get_conn_factory, get_hh_http

router = APIRouter()
Manager = Annotated[auth.User, Depends(auth.require("settings.edit"))]
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]


def _limits(conn_factory, http, clock) -> dict:
    try:
        return hh.limits_today(conn_factory, http, clock.now())
    except jobs.JobError as e:
        raise HTTPException(503, {"error": "hh", "message": f"hh: {e}"}) from None
    except httpx.HTTPError:
        raise HTTPException(502, {"error": "hh", "message": "hh не ответил, повторите позже"}) from None


@router.get("/api/hh/limits")
def limits(user: Manager, conn_factory: Conn, http: Annotated[httpx.Client, Depends(get_hh_http)],
           clock: Annotated[Clock, Depends(get_clock)]):
    return _limits(conn_factory, http, clock)


class CollectIn(BaseModel):
    count: int
    role_profile_id: int | None = None
    area_id: str | None = None


# быстрый выбор в окне загрузки; остальное — подсказкой hh по названию
DEFAULT_AREAS = [{"id": "113", "name": "Вся Россия"}, {"id": "1438", "name": "Краснодарский край"},
                 {"id": "53", "name": "Краснодар"}]


@router.get("/api/hh/areas")
def areas(user: Manager, http: Annotated[httpx.Client, Depends(get_hh_http)], q: str = ""):
    """Регионы и города hh для окна загрузки (S46): без запроса — быстрый выбор, с запросом — подсказка hh."""
    q = q.strip()
    if len(q) < 2:
        return {"items": DEFAULT_AREAS}
    try:
        r = http.get(f"{get_config().hh_api}/suggests/areas", params={"text": q[:60]}, timeout=10.0)
        r.raise_for_status()
        found = [{"id": str(a["id"]), "name": a.get("text") or a.get("name")} for a in r.json().get("items") or []
                 if str(a.get("id", "")).isdigit()]
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, {"error": "hh", "message": "hh не ответил, повторите позже"}) from None
    return {"items": found[:10]}


@router.post("/api/hh/collect", status_code=202)
def start(body: CollectIn, user: Manager, conn_factory: Conn, background: BackgroundTasks,
          http: Annotated[httpx.Client, Depends(get_hh_http)], clock: Annotated[Clock, Depends(get_clock)]):
    if body.area_id is not None and not body.area_id.isdigit():
        raise HTTPException(422, {"error": "validation", "message": "Неизвестный регион"})
    if body.role_profile_id is not None:
        with conn_factory() as conn:
            if not any(p["id"] == body.role_profile_id for p in pipeline.current_profiles(conn)):
                raise HTTPException(422, {"error": "validation", "message": "Портрет должности не утверждён"})
    left = _limits(conn_factory, http, clock)["views_left"]
    if not 1 <= body.count <= left:
        msg = f"Сколько резюме: от 1 до {left} — столько просмотров hh осталось сегодня"
        raise HTTPException(422, {"error": "validation", "message": msg})
    background.add_task(jobs.run, "collect_hh", collect.collect_hh_daily, conn_factory, locks=("source:hh",),
                        http=http, clock=clock, count=body.count, profile_id=body.role_profile_id, area=body.area_id)
    return JSONResponse({"started": True, "count": body.count, "views_left": left,
                         "role_profile_id": body.role_profile_id, "area_id": body.area_id}, status_code=202)


@router.get("/api/hh/collect/status")
def status(user: Manager, conn_factory: Conn):
    """Полоса хода загрузки (S48): последний прогон кнопки или расписания. Незавершённый старше 6 часов —
    упал вместе с процессом, ходом его не считаем."""
    with conn_factory() as conn:
        run = one(conn, """SELECT id, started_at, finished_at, ok, error, stats, progress,
                                  finished_at IS NULL AND started_at > now() - interval '6 hours' AS running
                           FROM ops.job_run WHERE job IN ('collect_hh', 'collect_hh_daily')
                           ORDER BY started_at DESC, id DESC LIMIT 1""")
    if not run:
        return {"running": False, "run": None}
    running = run.pop("running")
    stats = run["stats"] or {}
    run["stats"] = {k: stats.get(k) for k in ("seen", "new", "changed", "skipped", "errors", "alerts", "note")}
    return {"running": running, "run": run}
