"""«Сегодня» (S4, S5, S36, S37)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from .. import auth, views
from ..clock import Clock
from ..db import ConnFactory
from ..deps import get_clock, get_conn_factory

router = APIRouter()


@router.get("/api/today")
def today(user: Annotated[auth.User, Depends(auth.require("base.read"))],
          conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)],
          clock: Annotated[Clock, Depends(get_clock)]):
    with conn_factory() as conn:
        return views.today(conn, clock.now())
