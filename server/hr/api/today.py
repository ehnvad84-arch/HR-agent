"""«Сегодня» (S4, S5, S36, S37) и «Схема» (S34)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from .. import auth, embed, handoff, llm, quota, scope, search, tweaks, views
from ..clock import Clock
from ..db import ConnFactory, setting
from ..deps import get_clock, get_conn_factory

router = APIRouter()


@router.get("/api/today")
def today(us: Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("base.read"))],
          conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)],
          clock: Annotated[Clock, Depends(get_clock)]):
    with conn_factory() as conn:
        user, sc = us
        if sc.all:
            out = views.today(conn, clock.now())
            if "handoff.review" in user.permissions:   # S11: «Передали руководители» — первым блоком
                out["handoffs"] = handoff.inbox(conn, user.id, "handoff.all" in user.permissions)
            tweaks.attach_my(conn, user.id, out["signals"])
            return out
        out = views.manager_today(conn, sc, clock.now())
        out["handoffs"] = handoff.mine(conn, user.id)
        tweaks.attach_my(conn, user.id, out["signals"])
        return out


def _quota(conn, key: str, cap: int, now) -> dict:
    return {"used": quota.used(conn, key, now=now), "limit": cap}


@router.get("/api/schema")
def schema(user: Annotated[auth.User, Depends(auth.require("base.all"))],
           conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)],
           clock: Annotated[Clock, Depends(get_clock)]):
    """S34 и пределы, векторы, флаг модели (S8, S10–S12)."""
    now = clock.now()
    with conn_factory() as conn:
        out = views.schema(conn)
        llm_key, llm_cap_key, llm_cap = llm.QUOTA_CALLS
        out["quota"] = {"hh_full_resume": _quota(conn, quota.HH_FULL, quota.hh_full_cap(conn), now),
                        "llm": _quota(conn, llm_key, int(setting(conn, llm_cap_key, llm_cap)), now)}
        model = embed.method()
        out["vectors"] = {"model": model, "pending": search.missing_count(conn, model),
                          "total": out["counts"]["persons"], "down_since": embed.down_since(conn)}
        out["llm_assist_enabled"] = llm.assist_enabled(conn)
        return out
