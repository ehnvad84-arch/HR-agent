"""Контакты кандидата: подготовка открытия и показ (S16, S17). Открытие — через /api/action/{id}/approve."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from .. import auth, contact_hh
from ..clock import Clock
from ..db import ConnFactory
from ..deps import get_clock, get_conn_factory

router = APIRouter()
Opener = Annotated[auth.User, Depends(auth.require("contact.open"))]
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]


def errors(fn):
    try:
        return fn()
    except contact_hh.NotFound:
        raise HTTPException(404, {"error": "not_found", "message": "Не найдено"}) from None
    except contact_hh.Conflict as e:
        raise HTTPException(409, {"error": "conflict", "message": str(e)[:1].upper() + str(e)[1:]}) from None
    except contact_hh.Upstream as e:
        raise HTTPException(e.status, {"error": "hh", "message": str(e)}) from None


@router.post("/api/candidate/{candidate_id}/contact/prepare")
def prepare(candidate_id: int, user: Opener, conn_factory: Conn,
            clock: Annotated[Clock, Depends(get_clock)]):
    def go():
        with conn_factory() as conn:
            return contact_hh.prepare(conn, candidate_id, user.id, clock.now())
    return errors(go)


@router.get("/api/candidate/{candidate_id}/contacts")
def contacts(candidate_id: int, user: Opener, conn_factory: Conn):
    with conn_factory() as conn:
        return {"contacts": contact_hh.contacts(conn, candidate_id, user.id)}
