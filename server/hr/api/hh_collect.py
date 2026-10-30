"""Ручная загрузка резюме с hh в пределах дневного лимита (S44). Тратит лимит — только руководителю подбора."""
from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import auth, collect, hh, jobs
from ..clock import Clock
from ..db import ConnFactory
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


@router.post("/api/hh/collect", status_code=202)
def start(body: CollectIn, user: Manager, conn_factory: Conn, background: BackgroundTasks,
          http: Annotated[httpx.Client, Depends(get_hh_http)], clock: Annotated[Clock, Depends(get_clock)]):
    left = _limits(conn_factory, http, clock)["views_left"]
    if not 1 <= body.count <= left:
        msg = f"Сколько резюме: от 1 до {left} — столько просмотров hh осталось сегодня"
        raise HTTPException(422, {"error": "validation", "message": msg})
    background.add_task(jobs.run, "collect_hh", collect.collect_hh_daily, conn_factory, locks=("source:hh",),
                        http=http, clock=clock, count=body.count)
    return JSONResponse({"started": True, "count": body.count, "views_left": left}, status_code=202)
