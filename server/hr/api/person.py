"""Досье человека (S6). Просмотр нанимающим руководителем пишется в audit_log (security.md)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from .. import auth, views
from ..db import ConnFactory, j, run
from ..deps import get_conn_factory

router = APIRouter()
NOT_FOUND = {"error": "not_found", "message": "Человек не найден"}


def _read(person_id: int, user: auth.User, conn_factory: ConnFactory, full: bool) -> dict:
    with conn_factory() as conn:
        d = views.dossier(conn, person_id, full=full)
        if d is None:
            raise HTTPException(404, NOT_FOUND)
        if user.role == "hiring_manager":
            run(conn, """INSERT INTO audit_log (user_id, action, object_type, object_id, detail)
                         VALUES (%s, 'view', 'person', %s, %s)""", (user.id, person_id, j({"channel": "web"})))
    return d


@router.get("/api/person/{person_id}")
def person(person_id: int, user: Annotated[auth.User, Depends(auth.require("base.read"))],
           conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)]):
    return _read(person_id, user, conn_factory, full=False)


@router.get("/api/person/{person_id}/dossier")
def dossier(person_id: int, user: Annotated[auth.User, Depends(auth.require("base.read"))],
            conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)]):
    return _read(person_id, user, conn_factory, full=True)
