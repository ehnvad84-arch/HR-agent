"""Вход, выход и «кто я» (S1)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import auth, scope
from ..clock import Clock
from ..db import ConnFactory, setting
from ..deps import get_clock, get_conn_factory

router = APIRouter()
BAD_LOGIN = {"error": "auth", "message": "Неверный логин или пароль"}


class Credentials(BaseModel):
    login: str
    password: str


def _me(user: auth.User, csrf: str, sc: scope.Scope | None = None) -> dict:
    out = {"user": {"id": user.id, "login": user.login, "full_name": user.full_name, "role": user.role},
           "permissions": sorted(user.permissions), "csrf": csrf}
    if sc is not None:
        out["scope"] = sc.public()   # S17: reason no_unit — кабинет пуст с объяснением
    return out


@router.post("/api/login")
def login(body: Credentials, request: Request, response: Response,
          conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)],
          clock: Annotated[Clock, Depends(get_clock)]):
    if not auth.same_origin(request):
        raise HTTPException(403, {"error": "csrf", "message": "Запрос отклонён: откройте рабочее место заново"})
    now = clock.now()
    locked = None
    with conn_factory() as conn:   # неудача тоже фиксируется: на ней считается блокировка
        try:
            uid = auth.check_password(conn, body.login.strip(), body.password, now)
        except auth.LoginLocked as e:
            uid, locked = None, e.minutes
        if uid is not None:
            cookie, csrf = auth.create_session(conn, uid, now)
            user, _ = auth.load_session(conn, cookie, now)
            sc = scope.for_user(conn, user)
            days = int(setting(conn, "session_max_days", 7))
    if locked:
        raise HTTPException(429, {"error": "locked",
                                  "message": f"Слишком много попыток. Повторите через {locked} мин."})
    if uid is None:
        raise HTTPException(401, BAD_LOGIN)
    response.set_cookie(auth.COOKIE, cookie, max_age=days * 86400, httponly=True, secure=True, samesite="lax",
                        path="/")
    return _me(user, csrf, sc)


@router.post("/api/logout")
def logout(request: Request, response: Response,
           user: Annotated[auth.User, Depends(auth.require(auth.SESSION_ONLY))],
           conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)],
           clock: Annotated[Clock, Depends(get_clock)]):
    with conn_factory() as conn:
        auth.revoke(conn, request.cookies.get(auth.COOKIE), clock.now())
    response.delete_cookie(auth.COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return {"ok": True}


@router.get("/api/me")
def me(user: Annotated[auth.User, Depends(auth.require(auth.SESSION_ONLY))],
       conn_factory: Annotated[ConnFactory, Depends(get_conn_factory)]):
    with conn_factory() as conn:
        return _me(user, auth.rotate_csrf(conn, user.token_hash), scope.for_user(conn, user))
