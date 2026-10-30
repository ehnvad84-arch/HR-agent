"""Черновики писем и очередь подтверждений (S18, S37)."""
from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import actions, auth, contact_hh
from ..clock import Clock
from ..db import ConnFactory, val
from ..deps import get_clock, get_conn_factory, get_hh_http, get_llm_http
from .contacts import errors as contact_errors

router = APIRouter()
Taker = Annotated[auth.User, Depends(auth.require("person.take"))]
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]
Now = Annotated[Clock, Depends(get_clock)]


def _errors(fn):
    try:
        return fn()
    except actions.NotFound:
        raise HTTPException(404, {"error": "not_found", "message": "Не найдено"}) from None
    except actions.Conflict as e:
        raise HTTPException(409, {"error": "conflict", "message": f"Нельзя: {e}"}) from None
    except ValueError:
        raise HTTPException(422, {"error": "validation", "message": "Проверьте введённые данные"}) from None


class DraftIn(BaseModel):
    kind: str = "first_message"
    vacancy_id: int | None = None


@router.post("/api/candidate/{candidate_id}/draft")
def draft(candidate_id: int, body: DraftIn, user: Taker, conn_factory: Conn, clock: Now,
          http: Annotated[httpx.Client | None, Depends(get_llm_http)]):
    return _errors(lambda: actions.draft(conn_factory, candidate_id, body.kind, user.id, clock.now(),
                                         vacancy_id=body.vacancy_id, http=http))


@router.get("/api/action/{action_id}")
def action(action_id: int, user: Taker, conn_factory: Conn):
    def go():
        with conn_factory() as conn:
            return actions.get(conn, action_id)
    return _errors(go)


class ApproveIn(BaseModel):
    text: str | None = None


@router.post("/api/action/{action_id}/approve")
def approve(action_id: int, body: ApproveIn, user: Taker, conn_factory: Conn, clock: Now,
            hh_http: Annotated[httpx.Client, Depends(get_hh_http)]):
    with conn_factory() as conn:
        kind = val(conn, "SELECT kind::text FROM action_queue WHERE id = %s", (action_id,))
    if kind == "open_contact":   # платный контакт: отдельное право (48)
        if "contact.open" not in user.permissions:
            raise HTTPException(403, {"error": "forbidden", "message": "Недостаточно прав"})
        return contact_errors(lambda: contact_hh.open_contact(conn_factory, action_id, user.id, hh_http, clock))

    def go():
        with conn_factory() as conn:
            return actions.approve(conn, action_id, user.id, clock.now(), body.text)
    return _errors(go)


class DeclineIn(BaseModel):
    reason: str


@router.post("/api/action/{action_id}/decline")
def decline(action_id: int, body: DeclineIn, user: Taker, conn_factory: Conn, clock: Now):
    def go():
        with conn_factory() as conn:
            return actions.decline(conn, action_id, user.id, clock.now(), body.reason)
    return _errors(go)
