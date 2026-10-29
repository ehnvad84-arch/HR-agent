"""Зависимости FastAPI, общие для маршрутов: тесты подменяют их через app.dependency_overrides."""
from __future__ import annotations

import httpx

from . import db
from .clock import SYSTEM, Clock
from .config import get_config
from .http import client


def get_conn_factory() -> db.ConnFactory:
    return db.tx


def get_hh_http() -> httpx.Client:
    return client(get_config().hh_user_agent)


def get_clock() -> Clock:
    return SYSTEM
