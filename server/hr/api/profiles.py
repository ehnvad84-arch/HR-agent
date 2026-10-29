"""Профили должностей (S25–S27)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .. import auth, jobs, pipeline, profiles
from ..clock import Clock
from ..db import ConnFactory
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


@router.get("/api/profiles")
def listing(user: Annotated[auth.User, Depends(auth.require("base.read"))], conn_factory: Conn,
            q: str | None = None):
    with conn_factory() as conn:
        return {"items": profiles.listing(conn, q)}


@router.get("/api/profile/{profile_id}")
def get(profile_id: int, user: Annotated[auth.User, Depends(auth.require("base.read"))], conn_factory: Conn):
    def go():
        with conn_factory() as conn:
            return profiles.get(conn, profile_id)
    return _errors(go)


@router.post("/api/profile/{profile_id}")
def edit(profile_id: int, user: Annotated[auth.User, Depends(auth.require("profile.edit"))], conn_factory: Conn,
         clock: Now, changes: Annotated[dict, Body()]):
    def go():
        with conn_factory() as conn:
            return profiles.edit(conn, profile_id, changes, user.id, clock.now())
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
def preview(profile_id: int, user: Annotated[auth.User, Depends(auth.require("profile.edit"))],
            conn_factory: Conn):
    def go():
        with conn_factory() as conn:
            return profiles.preview(conn, profile_id)
    return _errors(go)


@router.get("/api/approvers")
def approvers(user: Annotated[auth.User, Depends(auth.require("base.read"))], conn_factory: Conn):
    with conn_factory() as conn:
        return profiles.approvers(conn)


@router.post("/api/profile/{profile_id}/doc")
def upload_doc(profile_id: int, user: Annotated[auth.User, Depends(auth.require("profile.edit"))],
               conn_factory: Conn, clock: Now, file: Annotated[UploadFile, File()],
               revision: Annotated[str | None, Form()] = None):
    data = file.file.read(64 * 1024 * 1024 + 1)   # предел размера проверяет profiles.upload_doc

    def go():
        with conn_factory() as conn:
            return profiles.upload_doc(conn, profile_id, file.filename or "ДИ", data, revision, user.id, clock.now())
    return _errors(go)
