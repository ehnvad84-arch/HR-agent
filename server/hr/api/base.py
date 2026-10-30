"""База, поиск по смыслу, подходящие к вакансии (S19, S20, S22)."""
from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException

from .. import auth, scope, search, views
from ..db import ConnFactory, one, setting
from ..deps import get_conn_factory

router = APIRouter()
Reader = Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("base.read"))]
NOT_FOUND = {"error": "not_found", "message": "Вакансия не найдена"}
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]
BAD_SORT = HTTPException(422, {"error": "validation", "message": "Неизвестная колонка или направление сортировки"})


@router.get("/api/base")
def base(user: Reader, conn_factory: Conn, dept: str | None = None, city: str | None = None,
         role: str | None = None, score_min: int | None = None, where: str | None = None,
         source: str | None = None, sort: str = "score", dir: str = "desc", page: int = 0, fit: str | None = None):
    """fit — «Подходящие» из «Базы должностей» (S49): балл именно по этой должности, а не лучший из всех;
    без «не наш» по ней и без выбывших — как подбор под вакансию (S22)."""
    sc = user[1]
    if fit and not sc.code_ok(fit):
        raise HTTPException(404, {"error": "not_found", "message": "Должность не найдена"})
    try:
        with conn_factory() as conn:
            return views.base(conn, dept=dept, city=city, role=role, score_min=score_min, where=where,
                              source=source, sort=sort, direction=dir, page=page, role_code=fit or None,
                              codes=None if sc.all else sc.codes)
    except ValueError:
        raise BAD_SORT from None


def _matching(vacancy_id: int, conn_factory, sort: str, dir: str, page: int, city: str | None,
              score_min: int | None, sc: scope.Scope) -> dict:
    if not sc.vacancy_ok(vacancy_id):
        raise HTTPException(404, NOT_FOUND)
    try:
        with conn_factory() as conn:
            v = one(conn, """SELECT v.id, v.title, rp.code FROM vacancy v
                             JOIN role_profile rp ON rp.id = v.role_profile_id WHERE v.id = %s""", (vacancy_id,))
            if not v:
                raise HTTPException(404, NOT_FOUND)
            floor = score_min if score_min is not None else int(setting(conn, "watch_threshold", 60))
            out = views.base(conn, role_code=v["code"], sort=sort, direction=dir, page=page, city=city,
                             score_min=floor)
    except ValueError:
        raise BAD_SORT from None
    return {"vacancy": {"id": v["id"], "title": v["title"]}, **out}


@router.get("/api/vacancy/{vacancy_id}/matching")
def matching(vacancy_id: int, user: Reader, conn_factory: Conn, sort: str = "score", dir: str = "desc",
             page: int = 0, city: str | None = None, score_min: int | None = None):
    """S22: «Показать подходящих» — фильтры вакансии, по убыванию балла."""
    return _matching(vacancy_id, conn_factory, sort, dir, page, city, score_min, user[1])


@router.get("/api/vacancy/{vacancy_id}/candidates")
def candidates(vacancy_id: int, user: Reader, conn_factory: Conn, page: int = 0):
    """Подходящие из накопленной базы — те же, что «Показать подходящих», порог — watch_threshold."""
    return _matching(vacancy_id, conn_factory, "score", "desc", page, None, None, user[1])


@router.get("/api/search")
def semantic(user: Reader, conn_factory: Conn, q: str = ""):
    if not q.strip():
        raise HTTPException(422, {"error": "validation", "message": "Пустой запрос"})
    try:
        with conn_factory() as conn:
            found = search.similar(conn, q, limit=30)
            if not user[1].all:
                found = [r for r in found if scope.person_ok(conn, user[1], r["person_id"])]
            info = views.cards(conn, [r["person_id"] for r in found])
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, {"error": "embed", "message": "Поиск по смыслу сейчас недоступен"}) from None
    for r in found:
        r.update(views._card(info.get(r["person_id"])))
    return {"items": found}
