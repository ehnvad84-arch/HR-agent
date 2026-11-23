"""Воронка и переводы кандидата (S24, S36, S37)."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, funnel, learning, scope
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


USE_STAGES = HTTPException(409, {"error": "use_stages",
                                  "message": "Этапы отмечаются в окне «Итог этапа» на доске вакансии"})


@router.post("/api/candidate/{candidate_id}/status")
def move(candidate_id: int, body: StatusIn, user: Annotated[auth.User, Depends(auth.require("person.take"))],
         conn_factory: Conn, clock: Now):
    """funnel-report: здесь только «в резерв», и только без открытого этапа — переводы по воронке и отказы
    отмечает stages.mark. Резерв — вне вакансии: vacancy_id не учитывается."""
    if body.to_status not in funnel.ALL:
        raise HTTPException(422, {"error": "bad_stage", "message": "Неизвестный этап воронки"})
    if body.to_status != "reserve":
        raise USE_STAGES
    try:
        with conn_factory() as conn:
            if funnel.has_open_stage(conn, candidate_id):
                raise USE_STAGES
            st = funnel.move(conn, candidate_id, "reserve", user.id, clock.now(), comment=body.comment)
    except funnel.NotFound:
        raise NOT_FOUND from None
    return {"status": st}


@router.get("/api/vacancy/{vacancy_id}/funnel")
def vacancy_funnel(vacancy_id: int,
                   us: Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("base.read"))],
                   conn_factory: Conn, clock: Now):
    if not us[1].vacancy_ok(vacancy_id):
        raise NOT_FOUND
    try:
        with conn_factory() as conn:
            out = funnel.funnel(conn, vacancy_id, clock.now())
    except funnel.NotFound:
        raise NOT_FOUND from None
    if not us[1].all:   # руководителю — заметки без телефонов, почты и ссылок (M2)
        for st in out["stages"]:
            for p in st["people"]:
                for n in p.get("notes", []):
                    n["comment"] = learning.strip_contacts(n["comment"])
    return out
