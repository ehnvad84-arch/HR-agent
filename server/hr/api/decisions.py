"""Решения по человеку (S11–S15)."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, decisions
from ..clock import Clock
from ..db import ConnFactory
from ..deps import get_clock, get_conn_factory

router = APIRouter()
Taker = Annotated[auth.User, Depends(auth.require("person.take"))]
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]
Now = Annotated[Clock, Depends(get_clock)]


def _errors(fn):
    try:
        return fn()
    except decisions.NotFound:
        raise HTTPException(404, {"error": "not_found", "message": "Не найдено"}) from None
    except decisions.Conflict as e:
        raise HTTPException(409, {"error": "conflict", "message": f"Нельзя: {e}"}) from None
    except decisions.TooShort as e:
        raise HTTPException(422, {"error": "comment_too_short",
                                  "message": f"Комментарий не короче {e.minimum} знаков"}) from None


class TakeIn(BaseModel):
    vacancy_id: int | None = None


@router.post("/api/person/{person_id}/take")
def take(person_id: int, body: TakeIn, user: Taker, conn_factory: Conn, clock: Now):
    def go():
        with conn_factory() as conn:
            return {"candidate_id": decisions.take(conn, person_id, user.id, clock.now(), body.vacancy_id)}
    return _errors(go)


class DecisionIn(BaseModel):
    person_id: int
    kind: Literal["postpone", "reject"]
    comment: str
    role_profile_id: int | None = None


@router.post("/api/decision")
def decision(body: DecisionIn, user: Taker, conn_factory: Conn, clock: Now):
    def go():
        with conn_factory() as conn:
            decisions.decide(conn, body.person_id, body.kind, body.comment, user.id, clock.now(), body.role_profile_id)
        return {"ok": True}
    return _errors(go)


class WatchIn(BaseModel):
    on: bool


@router.post("/api/person/{person_id}/watch")
def watch(person_id: int, body: WatchIn, user: Taker, conn_factory: Conn, clock: Now):
    def go():
        with conn_factory() as conn:
            return {"watch": decisions.set_watch(conn, person_id, body.on, user.id, clock.now())}
    return _errors(go)


@router.post("/api/social/{social_id}/confirm")
def confirm(social_id: int, user: Taker, conn_factory: Conn, clock: Now):
    def go():
        with conn_factory() as conn:
            return {"confidence": decisions.confirm_social(conn, social_id, user.id, clock.now())}
    return _errors(go)
