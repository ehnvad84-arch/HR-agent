"""Профили должностей (S25–S27)."""
from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .. import auth, jobs, llm, pipeline, profiles, scope, search, synonyms
from ..clock import Clock
from ..db import ConnFactory, val
from ..deps import get_clock, get_conn_factory, get_llm_http

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
    search.refresh_profile(conn_factory, profile_id)
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


SynEditor = Annotated[tuple[auth.User, scope.Scope], Depends(auth.require_scoped("profile.synonyms"))]
Llm = Annotated[httpx.Client | None, Depends(get_llm_http)]


def _syn_errors(fn):
    """Ошибки синонимов (S4) — тексты для карточки «Синонимы ключей»."""
    try:
        return fn()
    except (synonyms.NotFound, profiles.NotFound):
        raise HTTPException(404, {"error": "not_found", "message": "Профиль не найден"}) from None
    except synonyms.NoKeywords:
        raise HTTPException(422, {"error": "no_keywords",
                                  "message": "Сначала добавьте ключевые слова и сохраните версию"}) from None
    except synonyms.Invalid as e:
        raise HTTPException(422, {"error": "validation", "message": f"Проверьте: {e}"}) from None
    except synonyms.Stale as e:
        raise HTTPException(409, {"error": "stale", "message": str(e)}) from None
    except synonyms.NotLatest as e:
        raise HTTPException(409, {"error": "not_latest", "message": str(e)}) from None
    except llm.LLMDailyLimit:   # подкласс LLMUnavailable — раньше него
        raise HTTPException(429, {"error": "llm_daily_limit",
                                  "message": "Предел запросов к модели на сегодня исчерпан"}) from None
    except llm.LLMUnavailable:
        raise HTTPException(503, {"error": "llm_unavailable",
                                  "message": "Модель сейчас недоступна — впишите синонимы сами"}) from None


@router.get("/api/profiles/{profile_id}/synonyms")
def synonyms_get(profile_id: int, us: Scoped, conn_factory: Conn):
    def go():
        with conn_factory() as conn:
            _own(conn, us[1], profile_id)
            return synonyms.view(conn, profile_id, "profile.synonyms" in us[0].permissions)
    return _syn_errors(go)


@router.post("/api/profiles/{profile_id}/synonyms/suggest")
def synonyms_suggest(profile_id: int, us: SynEditor, conn_factory: Conn, http: Llm):
    def go():
        with conn_factory() as conn:
            _own(conn, us[1], profile_id)
        return {"suggestions": synonyms.suggest(conn_factory, profile_id, http=http)}
    return _syn_errors(go)


@router.put("/api/profiles/{profile_id}/synonyms")
def synonyms_put(profile_id: int, us: SynEditor, conn_factory: Conn, clock: Now, background: BackgroundTasks,
                 body: Annotated[dict, Body()]):
    base = body.get("base_change_id")
    if base is not None and (not isinstance(base, int) or isinstance(base, bool)):
        raise HTTPException(422, {"error": "validation", "message": "Проверьте: base_change_id — номер записи"})

    def go():
        with conn_factory() as conn:
            _own(conn, us[1], profile_id)
            return synonyms.save(conn, profile_id, body.get("items"), us[0].id, clock.now(), base_change_id=base)
    cid = _syn_errors(go)
    background.add_task(synonyms.rescore, conn_factory, profile_id)   # ход — в «Схеме» по ops.job_run
    return {"change_id": cid}


@router.post("/api/profiles/{profile_id}/synonyms/rollback/{change_id}")
def synonyms_rollback(profile_id: int, change_id: int, us: SynEditor, conn_factory: Conn, clock: Now,
                      background: BackgroundTasks):
    def go():
        with conn_factory() as conn:
            _own(conn, us[1], profile_id)
            return synonyms.rollback(conn, profile_id, change_id, us[0].id, clock.now())
    cid = _syn_errors(go)
    background.add_task(synonyms.rescore, conn_factory, profile_id)
    return {"change_id": cid}


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
