"""Номер и дата версии без входа (S1): только они — без путей, настроек и состояния базы."""
from __future__ import annotations

from fastapi import APIRouter

from .. import version

router = APIRouter()


@router.get("/api/version")
def get_version() -> dict:
    code = version.code_version()
    return {"version": code, "date": version.release_date(code)}
