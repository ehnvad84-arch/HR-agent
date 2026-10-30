"""Экран «Доступ» (manager-cabinet S1–S3): пользователи, роли, одноразовые коды."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import access, auth
from ..clock import Clock
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
def users(user: Manager, conn_factory: Conn):
    with conn_factory() as conn:
        sync = one(conn, """SELECT received_at, file_name, users, changes, state, error FROM ops.user_sync
                            ORDER BY received_at DESC, id DESC LIMIT 1""")
        return {"items": access.users(conn), "sync": sync}


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
