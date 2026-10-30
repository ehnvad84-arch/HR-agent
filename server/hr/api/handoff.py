"""Передачи кандидатов руководителями в отдел персонала (manager-cabinet S9–S11)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, handoff, scope
from ..clock import Clock
from ..db import ConnFactory
from ..deps import get_clock, get_conn_factory

router = APIRouter()
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]
Now = Annotated[Clock, Depends(get_clock)]
Sender = Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("handoff.create"))]
Reviewer = Annotated[auth.User, Depends(auth.require("handoff.review"))]


def _errors(fn):
    try:
        return fn()
    except handoff.NotFound:
        raise HTTPException(404, {"error": "not_found", "message": "Не найдено"}) from None
    except handoff.Forbidden as e:
        raise HTTPException(403, {"error": "forbidden", "message": f"Нельзя: {e}"}) from None
    except handoff.Conflict as e:
        raise HTTPException(409, {"error": "conflict", "message": f"Нельзя: {e}"}) from None
    except handoff.Invalid as e:
        raise HTTPException(422, {"error": "validation", "message": f"Проверьте: {e}"}) from None


@router.get("/api/handoff/options")
def options(person_id: int, us: Sender, conn_factory: Conn):
    def go():
        with conn_factory() as conn:
            return handoff.options(conn, us[1], person_id)
    return _errors(go)


class HandoffIn(BaseModel):
    person_id: int
    vacancy_id: int
    comment: str


@router.post("/api/handoff")
def send(body: HandoffIn, us: Sender, conn_factory: Conn, clock: Now):
    def go():
        with conn_factory() as conn:
            return handoff.send(conn, us[1], us[0].id, body.person_id, body.vacancy_id, body.comment, clock.now())
    return _errors(go)


@router.get("/api/handoffs")
def listing(user: Annotated[auth.User, Depends(auth.require(auth.SESSION_ONLY))], conn_factory: Conn,
            state: str | None = None):
    """Руководителю — свои передачи; отделу персонала — входящие (ответственному свои, с handoff.all — все)."""
    with conn_factory() as conn:
        if "handoff.review" in user.permissions:
            return {"items": handoff.inbox(conn, user.id, "handoff.all" in user.permissions, state)}
        if "handoff.create" in user.permissions:
            return {"items": handoff.mine(conn, user.id)}
    raise HTTPException(403, {"error": "forbidden", "message": "Недостаточно прав"})


@router.post("/api/handoff/{handoff_id}/take")
def take(handoff_id: int, user: Reviewer, conn_factory: Conn, clock: Now):
    if "person.take" not in user.permissions:
        raise HTTPException(403, {"error": "forbidden", "message": "Недостаточно прав"})

    def go():
        with conn_factory() as conn:
            return handoff.take(conn, handoff_id, user.id, "handoff.all" in user.permissions, clock.now())
    return _errors(go)


class RejectIn(BaseModel):
    reason: str


@router.post("/api/handoff/{handoff_id}/reject")
def reject(handoff_id: int, body: RejectIn, user: Reviewer, conn_factory: Conn, clock: Now):
    def go():
        with conn_factory() as conn:
            return handoff.reject(conn, handoff_id, user.id, "handoff.all" in user.permissions, body.reason,
                                  clock.now())
    return _errors(go)
