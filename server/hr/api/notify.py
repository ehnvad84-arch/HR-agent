"""Сообщения о загрузках для отдела персонала (manager-cabinet S20): колокольчик и полоса тревоги."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from .. import auth, load_check
from ..clock import Clock
from ..db import ConnFactory
from ..deps import get_clock, get_conn_factory

router = APIRouter()
HR = Annotated[auth.User, Depends(auth.require("handoff.review"))]
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]
Now = Annotated[Clock, Depends(get_clock)]
NOT_FOUND = HTTPException(404, {"error": "not_found", "message": "Сообщение не найдено"})


@router.get("/api/notifications")
def listing(user: HR, conn_factory: Conn):
    with conn_factory() as conn:
        return load_check.listing(conn, user.id)


@router.post("/api/notification/{nid}/read")
def read(nid: int, user: HR, conn_factory: Conn, clock: Now):
    with conn_factory() as conn:
        if not load_check.read(conn, user.id, nid, clock.now()):
            raise NOT_FOUND
    return {"ok": True}


@router.post("/api/notification/{nid}/ack")
def ack(nid: int, user: HR, conn_factory: Conn, clock: Now):
    with conn_factory() as conn:
        if not load_check.ack(conn, user.id, user.full_name, nid, clock.now()):
            raise NOT_FOUND
    return {"ok": True, "by": user.full_name}
