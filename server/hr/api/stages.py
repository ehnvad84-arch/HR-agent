"""Этапы вакансии (funnel-report): справочник этапов и наборы с утверждением (S1–S5), движение кандидата по этапам
(S6–S9, S16), пауза вакансии (S14) и отчёты воронки (S11–S13).

Справочник пополняет и выводит из работы руководитель ОП (stage.approve); набор предлагает и отзывает рекрутер
(vacancy.edit), утверждает или отклоняет руководитель ОП. Исходы этапов и их отмену ставит тот, кто берёт людей в
работу (person.take); чужую или вчерашнюю отметку отменяет руководитель ОП — это проверяет stages.undo. Чтение — с
границей видимости (AD-7): вакансия вне скоупа — 404, как будто её нет. Отчёты: рекрутер — свои вакансии и свой
отчёт; чужие и отдел — report.read вместе со всей базой (руководитель ОП); директор без всей базы их не видит.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, funnel_report, scope, stages, vacancies
from ..clock import Clock
from ..db import ConnFactory, val
from ..deps import get_clock, get_conn_factory
from .vacancies import _errors as _vacancy_errors

router = APIRouter()
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]
Now = Annotated[Clock, Depends(get_clock)]
Reader = Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("base.read"))]
Editor = Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("vacancy.edit"))]
Approver = Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("stage.approve"))]
Taker = Annotated[auth.User, Depends(auth.require("person.take"))]
VacancyEditor = Annotated[auth.User, Depends(auth.require("vacancy.edit"))]
NOT_FOUND = HTTPException(404, {"error": "not_found", "message": "Не найдено"})


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _errors(fn):
    try:
        return fn()
    except (stages.NotFound, funnel_report.NotFound):
        raise NOT_FOUND from None
    except stages.Forbidden as e:
        raise HTTPException(403, {"error": e.code, "message": str(e)}) from None
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


# ------------------------------------------------------------------ движение кандидата (S6–S9, S16)

class MarkIn(BaseModel):
    slot: int
    outcome: str
    reason: str | None = None
    comment: str | None = None
    scheduled_at: datetime | None = None


@router.post("/api/vacancy/{vacancy_id}/candidate/{candidate_id}/mark")
def mark(vacancy_id: int, candidate_id: int, body: MarkIn, user: Taker, conn_factory: Conn, clock: Now):
    def go():
        with conn_factory() as conn:
            return stages.mark(conn, candidate_id, vacancy_id, body.slot, body.outcome, user.id, clock.now(),
                               reason=body.reason, comment=body.comment, scheduled_at=body.scheduled_at)
    return _errors(go)


class UndoIn(BaseModel):
    comment: str


@router.post("/api/vacancy/{vacancy_id}/candidate/{candidate_id}/undo")
def undo(vacancy_id: int, candidate_id: int, body: UndoIn, user: Taker, conn_factory: Conn, clock: Now):
    def go():
        with conn_factory() as conn:
            return stages.undo(conn, candidate_id, vacancy_id, user.id, clock.now(), comment=body.comment)
    return _errors(go)


# ------------------------------------------------------------------ пауза вакансии (S14)

class PauseIn(BaseModel):
    comment: str | None = None


@router.post("/api/vacancy/{vacancy_id}/pause")
def pause(vacancy_id: int, user: VacancyEditor, conn_factory: Conn, clock: Now, body: PauseIn | None = None):
    def go():
        with conn_factory() as conn:
            return {"state": vacancies.pause(conn, vacancy_id, user.id, clock.now(), body.comment if body else None)}
    return _vacancy_errors(go)


@router.post("/api/vacancy/{vacancy_id}/resume")
def resume(vacancy_id: int, user: VacancyEditor, conn_factory: Conn, clock: Now):
    def go():
        with conn_factory() as conn:
            return {"state": vacancies.resume(conn, vacancy_id, user.id, clock.now())}
    return _vacancy_errors(go)


# ------------------------------------------------------------------ отчёты (S11–S13)

def _today(clock: Clock) -> date:
    return clock.now().astimezone(stages.MSK).date()


def _department(us: tuple[auth.User, scope.Scope]) -> bool:
    """Любой отчёт и отдел — report.read вместе со всей базой (руководитель ОП); директор без base.all — нет."""
    return us[1].all and "report.read" in us[0].permissions


@router.get("/api/vacancy/{vacancy_id}/funnel-report")
def vacancy_report(vacancy_id: int, us: Reader, conn_factory: Conn, clock: Now):
    """S11: рекрутер — своя вакансия (ответственный), руководитель ОП — любая, прочие — вакансии своего скоупа."""
    def go():
        with conn_factory() as conn:
            if not _department(us):
                if us[1].all:
                    owner = val(conn, "SELECT recruiter_id FROM vacancy WHERE id = %s", (vacancy_id,))
                    if owner != us[0].id:
                        raise stages.NotFound
                elif not us[1].vacancy_ok(vacancy_id):
                    raise stages.NotFound
            return funnel_report.vacancy_report(conn, vacancy_id, _today(clock))
    return _errors(go)


@router.get("/api/funnel/recruiter/{user_id}")
def recruiter_report(user_id: int, us: Reader, conn_factory: Conn, clock: Now, year: int | None = None):
    """S12: свой отчёт; чужой — только руководителю ОП."""
    def go():
        if user_id != us[0].id and not _department(us):
            raise stages.NotFound
        with conn_factory() as conn:
            if not val(conn, "SELECT 1 FROM app_user WHERE id = %s", (user_id,)):
                raise stages.NotFound
            today = _today(clock)
            return funnel_report.recruiter_report(conn, user_id, year or today.year, today)
    return _errors(go)


@router.get("/api/funnel/department")
def department_report(us: Reader, conn_factory: Conn, clock: Now, year: int | None = None):
    """S13: сумма отдела и сравнение рекрутеров — только руководителю ОП."""
    def go():
        if not _department(us):
            raise stages.NotFound
        with conn_factory() as conn:
            today = _today(clock)
            return funnel_report.department_report(conn, year or today.year, today)
    return _errors(go)
