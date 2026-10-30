"""Оргструктура из 1С в «Базе должностей» (S47): дерево — на чтение, обновление — руководителю подбора."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from .. import auth, org_1c, profiles
from ..clock import Clock
from ..config import get_config
from ..db import ConnFactory
from ..deps import get_clock, get_conn_factory

router = APIRouter()
Conn = Annotated[ConnFactory, Depends(get_conn_factory)]
Now = Annotated[Clock, Depends(get_clock)]
Mapper = Annotated[auth.User, Depends(auth.require("org.map"))]


@router.get("/api/org")
def tree(user: Annotated[auth.User, Depends(auth.require("org.read"))], conn_factory: Conn):
    with conn_factory() as conn:
        by_code = {p["code"]: p for p in profiles.listing(conn)}
        return org_1c.tree(conn, by_code)


def _apply(conn_factory, data: bytes, name: str, user: auth.User, clock: Clock) -> dict:
    try:
        with conn_factory() as conn:
            return org_1c.apply(conn, data, name, user.id, clock.now())
    except org_1c.Invalid as e:
        with conn_factory() as conn:   # отдельной транзакцией: применение откатилось, отказ остаётся
            org_1c.reject(conn, data, name, str(e), clock.now())
        raise HTTPException(422, {"error": "validation", "message": f"Выгрузка не применена: {e}"}) from None


@router.post("/api/org/refresh")
def refresh(user: Mapper, conn_factory: Conn, clock: Now):
    """«Обновить из 1С»: самый свежий файл обменного каталога."""
    try:
        f = org_1c.latest_file(get_config().exchange_1c_dir)
    except org_1c.NotFound:
        raise HTTPException(404, {"error": "not_found",
                                  "message": "В обменном каталоге 1С нет выгрузки — выберите файл вручную"}) from None
    if f.stat().st_size > org_1c.MAX_BYTES:
        raise HTTPException(422, {"error": "validation", "message": "Файл выгрузки больше 5 МБ"})
    return _apply(conn_factory, f.read_bytes(), f.name, user, clock)


@router.post("/api/org/upload")
def upload(user: Mapper, conn_factory: Conn, clock: Now, file: Annotated[UploadFile, File()]):
    data = file.file.read(org_1c.MAX_BYTES + 1)
    if len(data) > org_1c.MAX_BYTES:
        raise HTTPException(422, {"error": "validation", "message": "Файл выгрузки больше 5 МБ"})
    return _apply(conn_factory, data, (file.filename or "выгрузка.json")[:200], user, clock)
