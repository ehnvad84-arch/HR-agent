"""Профили должностей (S25–S27)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .. import auth, jobs, pipeline, profiles, scope
from ..clock import Clock
from ..db import ConnFactory, val
from ..deps import get_clock, get_conn_factory

router = APIRouter()
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]
Now = Annotated[Clock, Depends(get_clock)]


def _errors(fn):
    try:
        return fn()
    except profiles.NotFound:
        raise HTTPException(404, {"error": "not_found", "message": "Профиль не найден"}) from None
    except profiles.Conflict as e:
        raise HTTPException(409, {"error": "conflict", "message": f"Нельзя: {e}"}) from None
    except profiles.Invalid as e:
        raise HTTPException(422, {"error": "validation", "message": f"Проверьте: {e}"}) from None


Scoped = Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("base.read"))]
Editor = Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("profile.edit"))]


def _own(conn, sc: scope.Scope, profile_id: int, write: bool = False) -> None:
    """Портрет вне поддерева руководителя: чтение — 404, правка — 403 (S8)."""
    if sc.all:
        return
    code = val(conn, "SELECT code FROM role_profile WHERE id = %s", (profile_id,))
    if code is None or not sc.code_ok(code):
        if write and code is not None:
            raise HTTPException(403, {"error": "forbidden",
                                      "message": "Эту должность править нельзя: она не в вашем подразделении"})
        raise HTTPException(404, {"error": "not_found", "message": "Профиль не найден"})


@router.get("/api/profiles")
def listing(us: Scoped, conn_factory: Conn, q: str | None = None):
    with conn_factory() as conn:
        items = profiles.listing(conn, q)
    sc = us[1]
    return {"items": items if sc.all else [p for p in items if sc.code_ok(p["code"])]}


@router.get("/api/profile/{profile_id}")
def get(profile_id: int, us: Scoped, conn_factory: Conn):
    def go():
        with conn_factory() as conn:
            _own(conn, us[1], profile_id)
            return profiles.get(conn, profile_id)
    return _errors(go)


@router.post("/api/profile/{profile_id}")
def edit(profile_id: int, us: Editor, conn_factory: Conn, clock: Now, changes: Annotated[dict, Body()]):
    def go():
        with conn_factory() as conn:
            _own(conn, us[1], profile_id, write=True)
            return profiles.edit(conn, profile_id, changes, us[0].id, clock.now())
    return _errors(go)


def _rescore(conn_factory: ConnFactory, profile_id: int) -> None:
    jobs.run("rescore_profile", lambda cf, **_: pipeline.rescore_profile(cf, profile_id), conn_factory,
             locks=(f"profile:{profile_id}",))


@router.post("/api/profile/{profile_id}/approve", status_code=202)
def approve(profile_id: int, user: Annotated[auth.User, Depends(auth.require("profile.approve"))],
            conn_factory: Conn, clock: Now, background: BackgroundTasks):
    def go():
        with conn_factory() as conn:
            return profiles.approve(conn, profile_id, user.id, clock.now())
    out = _errors(go)
    background.add_task(_rescore, conn_factory, profile_id)   # «Схема» показывает ход по ops.job_run
    return JSONResponse({**out, "rescore": "started"}, status_code=202)


@router.get("/api/profile/{profile_id}/preview")
def preview(profile_id: int, us: Editor, conn_factory: Conn):
    def go():
        with conn_factory() as conn:
            _own(conn, us[1], profile_id)
            return profiles.preview(conn, profile_id)
    return _errors(go)


@router.get("/api/approvers")
def approvers(us: Scoped, conn_factory: Conn):
    with conn_factory() as conn:
        return profiles.approvers(conn)


@router.post("/api/profile/{profile_id}/doc")
def upload_doc(profile_id: int, us: Editor, conn_factory: Conn, clock: Now, file: Annotated[UploadFile, File()],
               revision: Annotated[str | None, Form()] = None):
    data = file.file.read(64 * 1024 * 1024 + 1)   # предел размера проверяет profiles.upload_doc

    def go():
        with conn_factory() as conn:
            _own(conn, us[1], profile_id, write=True)
            return profiles.upload_doc(conn, profile_id, file.filename or "ДИ", data, revision, us[0].id,
                                       clock.now())
    return _errors(go)
