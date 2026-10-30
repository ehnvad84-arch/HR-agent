"""HTTP-приложение: обратный вызов OAuth hh и API рабочего места (фича workplace)."""
from __future__ import annotations

import logging
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from . import db, hh, web
from .api import access as access_api
from .api import actions as actions_api
from .api import base as base_api
from .api import contacts as contacts_api
from .api import decisions as decisions_api
from .api import funnel as funnel_api
from .api import handoff as handoff_api
from .api import hh_collect as hh_collect_api
from .api import learning as learning_api
from .api import my as my_api
from .api import notify as notify_api
from .api import org as org_api
from .api import person as person_api
from .api import profiles as profiles_api
from .api import reports as reports_api
from .api import session as session_api
from .api import today as today_api
from .api import tweaks as tweaks_api
from .api import vacancies as vacancies_api
from .clock import Clock
from .deps import get_clock, get_conn_factory, get_hh_http

log = logging.getLogger(__name__)
app = FastAPI(title="Агент подбора", docs_url=None, redoc_url=None, openapi_url=None)
for _r in (session_api.router, today_api.router, person_api.router, decisions_api.router,
           funnel_api.router, actions_api.router, contacts_api.router,
           base_api.router, vacancies_api.router, profiles_api.router,
           learning_api.router, reports_api.router,
           hh_collect_api.router, org_api.router, my_api.router,
           access_api.router, handoff_api.router, tweaks_api.router,
           notify_api.router):
    app.include_router(_r)


@app.get("/api/oauth/hh/callback", response_class=HTMLResponse)
def hh_callback(code: str, state: str,
                conn_factory: Annotated[db.ConnFactory, Depends(get_conn_factory)],
                http: Annotated[httpx.Client, Depends(get_hh_http)],
                clock: Annotated[Clock, Depends(get_clock)]):
    now = clock.now()
    try:
        with conn_factory() as conn:
            hh.consume_state(conn, state, now)
    except hh.StateError:
        raise HTTPException(400, "Ссылка недействительна: получите новую командой hr.cli hh-auth") from None
    try:
        tokens = hh.exchange_code(http, code)
        employer = hh.check_employer(http, tokens["access_token"])
    except hh.EmployerMismatch:
        raise HTTPException(403, "Учётная запись не относится к работодателю компании") from None
    except (httpx.HTTPError, KeyError, ValueError):
        log.warning("hh не обменял код авторизации")
        raise HTTPException(502, "hh не подтвердил авторизацию, попробуйте ещё раз") from None
    with conn_factory() as conn:
        hh.store_tokens(conn, tokens, employer, now)
    return "<!doctype html><meta charset=utf-8><title>hh подключён</title><p>hh подключён. Окно можно закрыть.</p>"


web.install(app)   # после всех маршрутов: раздача страниц и обработчики ошибок
