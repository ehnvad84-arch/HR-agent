"""Обучение агента: чат (S29), недельная сводка (S30, S31)."""
from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, learning
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
def feed(user: Annotated[auth.User, Depends(auth.require("base.read"))], conn_factory: Conn):
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
