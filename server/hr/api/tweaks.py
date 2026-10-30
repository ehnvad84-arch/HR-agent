"""Личные поправки и «мой балл» (manager-cabinet S15): только свои."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, jobs, tweaks
from ..clock import Clock
from ..db import ConnFactory
from ..deps import get_clock, get_conn_factory

router = APIRouter()
Owner = Annotated[auth.User, Depends(auth.require("tweak.own"))]
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]
Now = Annotated[Clock, Depends(get_clock)]


def _rescore(conn_factory: ConnFactory, user_id: int) -> None:
    jobs.run("user_rescore", tweaks.user_rescore, conn_factory, locks=(f"user_rescore:{user_id}",), user_id=user_id)


def _errors(fn):
    try:
        return fn()
    except tweaks.NotFound:
        raise HTTPException(404, {"error": "not_found", "message": "Поправка не найдена"}) from None
    except tweaks.Invalid as e:
        raise HTTPException(422, {"error": "validation", "message": f"Проверьте: {e}"}) from None


@router.get("/api/tweaks")
def listing(user: Owner, conn_factory: Conn):
    with conn_factory() as conn:
        return tweaks.listing(conn, user.id)


class TweakIn(BaseModel):
    kind: str
    value: dict


@router.post("/api/tweaks")
def add(body: TweakIn, user: Owner, conn_factory: Conn, clock: Now, background: BackgroundTasks):
    def go():
        with conn_factory() as conn:
            return {"id": tweaks.add(conn, user.id, body.kind, body.value, clock.now())}
    out = _errors(go)
    background.add_task(_rescore, conn_factory, user.id)   # «мой балл» пересчитывается в фоне
    return out


def _state(tweak_id: int, state: str, user, conn_factory, clock, background):
    def go():
        with conn_factory() as conn:
            tweaks.set_state(conn, user.id, tweak_id, state, clock.now())
            return {"state": state}
    out = _errors(go)
    background.add_task(_rescore, conn_factory, user.id)
    return out


@router.post("/api/tweak/{tweak_id}/confirm")
def confirm(tweak_id: int, user: Owner, conn_factory: Conn, clock: Now, background: BackgroundTasks):
    return _state(tweak_id, "active", user, conn_factory, clock, background)


@router.post("/api/tweak/{tweak_id}/off")
def off(tweak_id: int, user: Owner, conn_factory: Conn, clock: Now, background: BackgroundTasks):
    return _state(tweak_id, "off", user, conn_factory, clock, background)
