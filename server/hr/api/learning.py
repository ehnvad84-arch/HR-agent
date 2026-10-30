"""Обучение агента: чат (S29), недельная сводка (S30, S31)."""
from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, learning, scope
from ..clock import Clock
from ..db import ConnFactory
from ..deps import get_clock, get_conn_factory, get_llm_http

router = APIRouter()
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]
Now = Annotated[Clock, Depends(get_clock)]


class FeedbackIn(BaseModel):
    body: str
    tag: str = "remark"
    person_id: int | None = None
    role_profile_id: int | None = None


@router.get("/api/feedback")
def feed(us: Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("base.read"))],
         conn_factory: Conn):
    with conn_factory() as conn:
        return {"items": learning.feed(conn)}


@router.post("/api/feedback")
def post(body: FeedbackIn, user: Annotated[auth.User, Depends(auth.require("feedback.write"))],
         conn_factory: Conn, clock: Now, http: Annotated[httpx.Client | None, Depends(get_llm_http)]):
    try:
        return learning.post(conn_factory, user.id, body.body, body.tag, clock.now(), body.person_id,
                             body.role_profile_id, http=http)
    except learning.Invalid as e:
        raise HTTPException(422, {"error": "validation", "message": f"Проверьте: {e}"}) from None


@router.get("/api/digest/current")
def digest(user: Annotated[auth.User, Depends(auth.require("digest.read"))], conn_factory: Conn):
    with conn_factory() as conn:
        return learning.current(conn) or {"proposals": []}


class DecideIn(BaseModel):
    decision: str
    note: str | None = None


@router.post("/api/digest/{proposal_id}/decide")
def decide(proposal_id: int, body: DecideIn, user: Annotated[auth.User, Depends(auth.require("digest.decide"))],
           conn_factory: Conn, clock: Now):
    try:
        with conn_factory() as conn:
            return {"decision": learning.decide(conn, proposal_id, body.decision, body.note, user.id, clock.now())}
    except learning.Invalid as e:
        raise HTTPException(422, {"error": "validation", "message": f"Проверьте: {e}"}) from None
    except learning.Conflict as e:
        raise HTTPException(409, {"error": "conflict", "message": f"Нельзя: {e}"}) from None
    except LookupError:
        raise HTTPException(404, {"error": "not_found", "message": "Предложение не найдено"}) from None
