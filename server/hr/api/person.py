"""Досье человека (S6). Просмотр нанимающим руководителем пишется в audit_log (security.md)."""
from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from .. import assist, auth, dossier_docx, photos, scope, views
from ..clock import Clock
from ..db import ConnFactory, j, run, val
from ..deps import get_clock, get_conn_factory, get_llm_http

router = APIRouter()
NOT_FOUND = {"error": "not_found", "message": "Человек не найден"}


Scoped = Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("base.read"))]


def _visible(conn, sc: scope.Scope, person_id: int) -> None:
    """Вне скоупа — 404, как будто человека нет (manager-cabinet M1)."""
    if not scope.person_ok(conn, sc, person_id):
        raise HTTPException(404, NOT_FOUND)


def _read(person_id: int, us: tuple, conn_factory: ConnFactory, full: bool) -> dict:
    user, sc = us
    with conn_factory() as conn:
        _visible(conn, sc, person_id)
        d = views.dossier(conn, person_id, full=full)
        if d is None:
            raise HTTPException(404, NOT_FOUND)
        if not sc.all:   # просмотр досье вне отдела персонала — всегда в журнал (security.md, M7)
            run(conn, """INSERT INTO audit_log (user_id, action, object_type, object_id, detail)
                         VALUES (%s, 'view', 'person', %s, %s)""", (user.id, person_id, j({"channel": "web"})))
            d = views.for_manager(d, sc)
    return d


@router.get("/api/person/{person_id}")
def person(person_id: int, us: Scoped, conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)]):
    return _read(person_id, us, conn_factory, full=False)


@router.get("/api/person/{person_id}/dossier")
def dossier(person_id: int, us: Scoped, conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)]):
    return _read(person_id, us, conn_factory, full=True)


@router.get("/api/person/{person_id}/photo")
def photo(person_id: int, us: Scoped, conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)]):
    with conn_factory() as conn:
        _visible(conn, us[1], person_id)
        data = photos.photo_of(conn, person_id)
    if data is None:
        raise HTTPException(404, {"error": "not_found", "message": "Фото нет"})
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


class ExplainIn(BaseModel):
    role_profile_id: int


@router.post("/api/person/{person_id}/explain")
def explain(person_id: int, body: ExplainIn, us: Scoped,
            conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)],
            http: Annotated[httpx.Client | None, Depends(get_llm_http)]):
    """S7, S8: разбор моделью отдельно от досье — досье не ждёт модель."""
    sc = us[1]
    if not sc.all:
        with conn_factory() as conn:
            _visible(conn, sc, person_id)
            if not sc.code_ok(val(conn, "SELECT code FROM role_profile WHERE id = %s", (body.role_profile_id,))):
                raise HTTPException(404, {"error": "not_found", "message": "Нет оценки по этому профилю"})
    try:
        return assist.explain_pair(conn_factory, person_id, body.role_profile_id, http=http)
    except assist.NotFound:
        raise HTTPException(404, {"error": "not_found", "message": "Нет оценки по этому профилю"}) from None


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/api/person/{person_id}/export")
def export(person_id: int, f: str,
           user: Annotated[auth.User, Depends(auth.require("dossier.export", csrf=True))],
           conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)],
           clock: Annotated[Clock, Depends(get_clock)]):
    """S10: docx — файл; pdf — запись выгрузки, сам PDF печатает браузер. GET с CSRF (security.md)."""
    if f not in ("docx", "pdf"):
        raise HTTPException(422, {"error": "validation", "message": "Формат — docx или pdf"})
    now = clock.now()
    with conn_factory() as conn:
        built = dossier_docx.build(conn, person_id, now) if f == "docx" else None
        d = built[1] if built else views.dossier(conn, person_id, full=False)
        if d is None:
            raise HTTPException(404, NOT_FOUND)
        cid = d["candidate"]["id"] if d["candidate"] else None
        eid = dossier_docx.record(conn, person_id, cid, f, user.id, now)
    if f == "pdf":
        return {"export_id": eid}
    return Response(built[0], media_type=DOCX,
                    headers={"Content-Disposition": f'attachment; filename="dossier-{person_id}.docx"'})
