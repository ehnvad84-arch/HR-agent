"""Этапы вакансии (funnel-report, S1–S5): справочник этапов и наборы этапов вакансии с утверждением.

Справочник пополняет и выводит из работы руководитель ОП (stage.approve); набор предлагает и отзывает рекрутер
(vacancy.edit), утверждает или отклоняет руководитель ОП. Чтение — с границей видимости (AD-7): вакансия вне
скоупа — 404, как будто её нет.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, scope, stages
from ..clock import Clock
from ..db import ConnFactory, val
from ..deps import get_clock, get_conn_factory

router = APIRouter()
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]
Now = Annotated[Clock, Depends(get_clock)]
Reader = Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("base.read"))]
Editor = Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("vacancy.edit"))]
Approver = Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("stage.approve"))]
NOT_FOUND = HTTPException(404, {"error": "not_found", "message": "Не найдено"})


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _errors(fn):
    try:
        return fn()
    except stages.NotFound:
        raise NOT_FOUND from None
    except stages.Conflict as e:
        raise HTTPException(409, {"error": e.code, "message": _cap(str(e)), **e.data}) from None
    except stages.Invalid as e:
        raise HTTPException(422, {"error": "validation", "message": f"Проверьте: {e}"}) from None


# ------------------------------------------------------------------ справочник (S1)

@router.get("/api/stages")
def library(us: Reader, conn_factory: Conn):
    with conn_factory() as conn:
        return stages.library(conn)


class StageIn(BaseModel):
    title: str
    report_row: str


@router.post("/api/stages")
def add_stage(body: StageIn, us: Approver, conn_factory: Conn, clock: Now):
    def go():
        with conn_factory() as conn:
            return {"id": stages.add_stage(conn, body.title, body.report_row, us[0].id, clock.now())}
    return _errors(go)


@router.post("/api/stages/{stage_id}/retire")
def retire_stage(stage_id: int, us: Approver, conn_factory: Conn, clock: Now):
    def go():
        with conn_factory() as conn:
            stages.retire_stage(conn, stage_id, us[0].id, clock.now())
        return {"id": stage_id}
    return _errors(go)


# ------------------------------------------------------------------ наборы вакансии (S2–S5)

@router.get("/api/vacancy/{vacancy_id}/stage-sets")
def vacancy_sets(vacancy_id: int, us: Reader, conn_factory: Conn):
    if not us[1].vacancy_ok(vacancy_id):
        raise NOT_FOUND

    def go():
        with conn_factory() as conn:
            return stages.sets(conn, vacancy_id)
    return _errors(go)


class SetItemIn(BaseModel):
    slot: int | None = None
    stage_id: int
    label: str | None = None


class SetIn(BaseModel):
    items: list[SetItemIn]
    note: str | None = None


@router.post("/api/vacancy/{vacancy_id}/stage-sets")
def propose(vacancy_id: int, body: SetIn, us: Editor, conn_factory: Conn, clock: Now):
    if not us[1].vacancy_ok(vacancy_id):
        raise NOT_FOUND

    def go():
        with conn_factory() as conn:
            return stages.propose(conn, vacancy_id, [i.model_dump() for i in body.items], body.note, us[0].id,
                                  clock.now())
    return _errors(go)


def _decide(set_id: int, us: tuple[auth.User, scope.Scope], conn_factory: ConnFactory, act):
    """Набор проверяется по своей вакансии (AD-7): вне скоупа — 404."""
    def go():
        with conn_factory() as conn:
            vid = val(conn, "SELECT vacancy_id FROM ops.stage_set WHERE id = %s", (set_id,))
            if vid is None or not us[1].vacancy_ok(vid):
                raise stages.NotFound
            return {"state": act(conn)}
    return _errors(go)


@router.post("/api/stage-sets/{set_id}/withdraw")
def withdraw(set_id: int, us: Editor, conn_factory: Conn, clock: Now):
    return _decide(set_id, us, conn_factory, lambda conn: stages.withdraw(conn, set_id, us[0].id, clock.now()))


@router.post("/api/stage-sets/{set_id}/approve")
def approve(set_id: int, us: Approver, conn_factory: Conn, clock: Now):
    return _decide(set_id, us, conn_factory, lambda conn: stages.approve(conn, set_id, us[0].id, clock.now()))


class DeclineIn(BaseModel):
    comment: str


@router.post("/api/stage-sets/{set_id}/decline")
def decline(set_id: int, body: DeclineIn, us: Approver, conn_factory: Conn, clock: Now):
    return _decide(set_id, us, conn_factory,
                   lambda conn: stages.decline(conn, set_id, body.comment, us[0].id, clock.now()))
