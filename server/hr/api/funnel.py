"""Воронка и переводы кандидата (S24, S36, S37)."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, funnel
from ..clock import Clock
from ..db import ConnFactory
from ..deps import get_clock, get_conn_factory

router = APIRouter()
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]
Now = Annotated[Clock, Depends(get_clock)]
NOT_FOUND = HTTPException(404, {"error": "not_found", "message": "Не найдено"})


class StatusIn(BaseModel):
    to_status: str
    vacancy_id: int | None = None
    comment: str | None = None
    scheduled_at: datetime | None = None


@router.post("/api/candidate/{candidate_id}/status")
def move(candidate_id: int, body: StatusIn, user: Annotated[auth.User, Depends(auth.require("person.take"))],
         conn_factory: Conn, clock: Now):
    try:
        with conn_factory() as conn:
            st = funnel.move(conn, candidate_id, body.to_status, user.id, clock.now(), vacancy_id=body.vacancy_id,
                             comment=body.comment, scheduled_at=body.scheduled_at)
    except funnel.BadStage:
        raise HTTPException(422, {"error": "bad_stage", "message": "Неизвестный этап воронки"}) from None
    except funnel.NotFound:
        raise NOT_FOUND from None
    return {"status": st}


@router.get("/api/vacancy/{vacancy_id}/funnel")
def vacancy_funnel(vacancy_id: int, user: Annotated[auth.User, Depends(auth.require("base.read"))],
                   conn_factory: Conn, clock: Now):
    try:
        with conn_factory() as conn:
            return funnel.funnel(conn, vacancy_id, clock.now())
    except funnel.NotFound:
        raise NOT_FOUND from None
