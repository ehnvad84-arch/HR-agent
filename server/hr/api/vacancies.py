"""Вакансии (S21, S23, S38)."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, scope, vacancies
from ..clock import Clock
from ..db import ConnFactory
from ..deps import get_clock, get_conn_factory

router = APIRouter()
Editor = Annotated[auth.User, Depends(auth.require("vacancy.edit"))]
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]
Now = Annotated[Clock, Depends(get_clock)]


def _errors(fn):
    try:
        return fn()
    except vacancies.NotFound:
        raise HTTPException(404, {"error": "not_found", "message": "Вакансия не найдена"}) from None
    except vacancies.Conflict as e:
        raise HTTPException(409, {"error": "conflict", "message": f"Нельзя: {e}"}) from None
    except vacancies.Invalid as e:
        raise HTTPException(422, {"error": "validation", "message": f"Проверьте: {e}"}) from None


@router.get("/api/vacancies")
def listing(us: Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("base.read"))],
            conn_factory: Conn):
    with conn_factory() as conn:
        return vacancies.listing(conn, None if us[1].all else us[1].vacancies)


class VacancyIn(BaseModel):
    role_profile_id: int
    title: str
    headcount: int = 1
    target_date: date | None = None


@router.post("/api/vacancy")
def create(body: VacancyIn, user: Editor, conn_factory: Conn):
    def go():
        with conn_factory() as conn:
            return {"id": vacancies.create(conn, body.role_profile_id, body.title, user.id, body.headcount,
                                           body.target_date)}
    return _errors(go)


class CloseIn(BaseModel):
    comment: str


@router.post("/api/vacancy/{vacancy_id}/close")
def close(vacancy_id: int, body: CloseIn, user: Editor, conn_factory: Conn, clock: Now):
    def go():
        with conn_factory() as conn:
            return {"state": vacancies.close(conn, vacancy_id, body.comment, user.id, clock.now())}
    return _errors(go)


@router.post("/api/vacancy/{vacancy_id}/reopen")
def reopen(vacancy_id: int, user: Editor, conn_factory: Conn, clock: Now):
    def go():
        with conn_factory() as conn:
            return {"state": vacancies.reopen(conn, vacancy_id, user.id, clock.now())}
    return _errors(go)
