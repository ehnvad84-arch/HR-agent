"""Экран «Доступ» (manager-cabinet S1–S3): пользователи, роли, одноразовые коды."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from .. import access, auth, staff_1c
from ..clock import Clock
from ..config import get_config
from ..db import ConnFactory, one
from ..deps import get_clock, get_conn_factory

router = APIRouter()
Manager = Annotated[auth.User, Depends(auth.require("access.manage"))]
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]
Now = Annotated[Clock, Depends(get_clock)]


def _errors(fn):
    try:
        return fn()
    except access.NotFound:
        raise HTTPException(404, {"error": "not_found", "message": "Пользователь не найден"}) from None
    except access.Forbidden as e:
        raise HTTPException(403, {"error": "forbidden", "message": f"Нельзя: {e}"}) from None
    except access.Conflict as e:
        raise HTTPException(409, {"error": "conflict", "message": f"Нельзя: {e}"}) from None
    except access.Invalid as e:
        raise HTTPException(422, {"error": "validation", "message": f"Проверьте: {e}"}) from None


@router.get("/api/access/users")
def users(user: Annotated[auth.User, Depends(auth.require("invite.issue"))], conn_factory: Conn):
    """Руководителю отдела персонала — все; специалисту — только руководители и директор: им он выдаёт коды."""
    with conn_factory() as conn:
        items = access.users(conn)
        if "access.manage" not in user.permissions:
            return {"items": [u for u in items if u["role"] in ("hiring_manager", "director")], "sync": None}
        sync = one(conn, """SELECT received_at, file_name, users, changes, state, error FROM ops.user_sync
                            ORDER BY received_at DESC, id DESC LIMIT 1""")
        return {"items": items, "sync": sync}


class NewUser(BaseModel):
    login: str
    full_name: str
    org_unit_id: int | None = None
    position: str | None = None
    role: str


@router.post("/api/access/user")
def create(body: NewUser, user: Manager, conn_factory: Conn, clock: Now):
    def go():
        with conn_factory() as conn:
            return {"id": access.create(conn, body.login, body.full_name, body.org_unit_id, body.position,
                                        body.role, user.id, clock.now())}
    return _errors(go)


class Change(BaseModel):
    role: str | None = None
    org_unit_id: int | None = None
    manual: bool | None = None


@router.post("/api/access/user/{user_id}")
def change(user_id: int, body: Change, user: Manager, conn_factory: Conn, clock: Now):
    def go():
        with conn_factory() as conn:
            return access.update(conn, user_id, user.id, clock.now(), role=body.role, org_unit_id=body.org_unit_id,
                                 manual=body.manual)
    return _errors(go)


@router.post("/api/access/user/{user_id}/code")
def code(user_id: int, user: Annotated[auth.User, Depends(auth.require("invite.issue"))], conn_factory: Conn,
         clock: Now):
    def go():
        with conn_factory() as conn:
            return access.issue_code(conn, user_id, user, clock.now())
    return _errors(go)


def _apply(conn_factory, data: bytes, name: str, user: auth.User, clock: Clock) -> dict:
    try:
        with conn_factory() as conn:
            return staff_1c.apply(conn, data, name, user.id, clock.now())
    except staff_1c.Invalid as e:
        with conn_factory() as conn:   # отдельной транзакцией: применение откатилось, отказ остаётся
            staff_1c.reject(conn, data, name, str(e), clock.now())
        raise HTTPException(422, {"error": "validation", "message": f"Выгрузка не применена: {e}"}) from None


@router.post("/api/access/sync")
def sync(user: Manager, conn_factory: Conn, clock: Now):
    """S1: «Обновить из 1С» — самый свежий employees_*.json обменного каталога."""
    try:
        f = staff_1c.latest_file(get_config().exchange_1c_dir)
    except staff_1c.NotFound:
        raise HTTPException(404, {"error": "not_found", "message": "В обменном каталоге 1С нет выгрузки сотрудников "
                                                                   "— выберите файл вручную"}) from None
    if f.stat().st_size > staff_1c.MAX_BYTES:
        raise HTTPException(422, {"error": "validation", "message": "Файл выгрузки больше 5 МБ"})
    return _apply(conn_factory, f.read_bytes(), f.name, user, clock)


@router.post("/api/access/upload")
def upload(user: Manager, conn_factory: Conn, clock: Now, file: Annotated[UploadFile, File()]):
    data = file.file.read(staff_1c.MAX_BYTES + 1)
    if len(data) > staff_1c.MAX_BYTES:
        raise HTTPException(422, {"error": "validation", "message": "Файл выгрузки больше 5 МБ"})
    return _apply(conn_factory, data, (file.filename or "employees.json")[:200], user, clock)
