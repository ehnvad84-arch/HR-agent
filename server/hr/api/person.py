"""Досье человека (S6). Просмотр нанимающим руководителем пишется в audit_log (security.md)."""
from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from .. import assist, auth, photos, views
from ..db import ConnFactory, j, run
from ..deps import get_conn_factory, get_llm_http

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


@router.get("/api/person/{person_id}/photo")
def photo(person_id: int, user: Annotated[auth.User, Depends(auth.require("base.read"))],
          conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)]):
    with conn_factory() as conn:
        data = photos.photo_of(conn, person_id)
    if data is None:
        raise HTTPException(404, {"error": "not_found", "message": "Фото нет"})
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


class ExplainIn(BaseModel):
    role_profile_id: int


@router.post("/api/person/{person_id}/explain")
def explain(person_id: int, body: ExplainIn, user: Annotated[auth.User, Depends(auth.require("base.read"))],
            conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)],
            http: Annotated[httpx.Client | None, Depends(get_llm_http)]):
    """S7, S8: разбор моделью отдельно от досье — досье не ждёт модель."""
    try:
        return assist.explain_pair(conn_factory, person_id, body.role_profile_id, http=http)
    except assist.NotFound:
        raise HTTPException(404, {"error": "not_found", "message": "Нет оценки по этому профилю"}) from None
