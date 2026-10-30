"""«Сегодня» (S4, S5, S36, S37) и «Схема» (S34)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from .. import auth, scope, views
from ..clock import Clock
from ..db import ConnFactory
from ..deps import get_clock, get_conn_factory

router = APIRouter()


@router.get("/api/today")
def today(us: Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("base.read"))],
          conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)],
          clock: Annotated[Clock, Depends(get_clock)]):
    with conn_factory() as conn:
        if us[1].all:
            return views.today(conn, clock.now())
        return views.manager_today(conn, us[1], clock.now())


@router.get("/api/schema")
def schema(user: Annotated[auth.User, Depends(auth.require("base.all"))],
           conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)]):
    with conn_factory() as conn:
        return views.schema(conn)
