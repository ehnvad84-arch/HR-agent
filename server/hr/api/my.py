"""Кабинет руководителя: «Мои должности» (manager-cabinet S8) — дерево должностей поддерева без повторов."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from .. import auth, org_1c, profiles, scope
from ..db import ConnFactory
from ..deps import get_conn_factory

router = APIRouter()


@router.get("/api/my/positions")
def positions(us: Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("base.read"))],
              conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)]):
    """Тот же ответ, что /api/org, но только подразделения поддерева; у отдела персонала — вся структура."""
    sc = us[1]
    with conn_factory() as conn:
        tree = org_1c.tree(conn, {p["code"]: p for p in profiles.listing(conn)})
    if not sc.all:
        tree["units"] = [{**u, "parent_id": u["parent_id"] if u["parent_id"] in sc.units else None}
                         for u in tree["units"] if u["id"] in sc.units]
    return {**tree, "scope": sc.public()}
