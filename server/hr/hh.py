"""hh.ru: OAuth работодателя (раздел 5.2 ТЗ).

refresh_token у hh одноразовый: обновление идёт под блокировкой строки токена,
новый refresh_token сохраняется сразу. Токены — только в ops.oauth_token, зашифрованы.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
import psycopg

from . import crypto
from .clock import Clock
from .config import get_config
from .db import ConnFactory, one, run, setting
from .http import PageBudget, RateLimiter
from .jobs import JobBlocked, JobNotConfigured

STATE_TTL = timedelta(minutes=10)
AUTH_NEEDED = "нет токена hh, пройдите авторизацию: python -m hr.cli hh-auth"


class StateError(ValueError):
    """state неизвестен, просрочен или уже использован."""


class EmployerMismatch(PermissionError):
    pass


def _hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def auth_link(conn: psycopg.Connection, now: datetime) -> str:
    """Одноразовая ссылка для менеджера работодателя; в базе только хеш state."""
    cfg = get_config()
    if not (cfg.hh_client_id and cfg.hh_redirect_uri):
        raise JobNotConfigured("HH_CLIENT_ID и HH_REDIRECT_URI не заданы")
    state = secrets.token_urlsafe(32)
    run(conn, "INSERT INTO ops.oauth_state (state_hash, provider, expires_at) VALUES (%s, 'hh', %s)",
        (_hash(state), now + STATE_TTL))
    q = urlencode({"response_type": "code", "client_id": cfg.hh_client_id, "redirect_uri": cfg.hh_redirect_uri,
                   "state": state})
    return f"{cfg.hh_oauth}/oauth/authorize?{q}"


def consume_state(conn: psycopg.Connection, state: str, now: datetime) -> None:
    row = one(conn, "SELECT * FROM ops.oauth_state WHERE state_hash = %s AND provider = 'hh' FOR UPDATE",
              (_hash(state),))
    if not row or row["used_at"] or row["expires_at"] < now:
        raise StateError
    run(conn, "UPDATE ops.oauth_state SET used_at = %s WHERE state_hash = %s", (now, row["state_hash"]))


def _token_request(http: httpx.Client, form: dict) -> dict:
    r = http.post(f"{get_config().hh_api}/token", data=form)
    r.raise_for_status()
    return r.json()


def exchange_code(http: httpx.Client, code: str) -> dict:
    cfg = get_config()
    return _token_request(http, {"grant_type": "authorization_code", "client_id": cfg.hh_client_id,
                                 "client_secret": cfg.hh_client_secret, "code": code,
                                 "redirect_uri": cfg.hh_redirect_uri})


def check_employer(http: httpx.Client, access_token: str) -> str:
    """Токен должен принадлежать менеджеру нашего работодателя."""
    r = http.get(f"{get_config().hh_api}/me", headers={"Authorization": f"Bearer {access_token}"})
    r.raise_for_status()
    me = r.json()
    employer = str((me.get("employer") or {}).get("id") or "")
    if not me.get("is_employer") or employer != get_config().hh_employer_id:
        raise EmployerMismatch
    return employer


def store_tokens(conn: psycopg.Connection, data: dict, account_id: str | None, now: datetime) -> None:
    access = crypto.encrypt(data["access_token"])
    refresh = crypto.encrypt(data["refresh_token"]) if data.get("refresh_token") else None
    expires = now + timedelta(seconds=int(data.get("expires_in", 1209600)))
    run(conn, """
        INSERT INTO ops.oauth_token (provider, access_token_enc, refresh_token_enc, expires_at, account_id, updated_at)
        VALUES ('hh', %s, %s, %s, %s, %s)
        ON CONFLICT (provider) DO UPDATE SET access_token_enc = EXCLUDED.access_token_enc,
            refresh_token_enc = coalesce(EXCLUDED.refresh_token_enc, ops.oauth_token.refresh_token_enc),
            expires_at = EXCLUDED.expires_at, account_id = coalesce(EXCLUDED.account_id, ops.oauth_token.account_id),
            updated_at = EXCLUDED.updated_at""", (access, refresh, expires, account_id, now))


def access_token(conn: psycopg.Connection, http: httpx.Client, now: datetime, force_refresh: bool = False) -> str:
    """Действующий access_token; просроченный (или отвергнутый hh, force_refresh) обновляется один раз."""
    row = one(conn, "SELECT * FROM ops.oauth_token WHERE provider = 'hh' FOR UPDATE")
    if row and not force_refresh and row["expires_at"] > now:
        return crypto.decrypt(row["access_token_enc"])
    refresh = crypto.decrypt(row["refresh_token_enc"]) if row and row["refresh_token_enc"] else \
        get_config().hh_refresh_token
    if not refresh:
        raise JobNotConfigured(AUTH_NEEDED)
    try:
        data = _token_request(http, {"grant_type": "refresh_token", "refresh_token": refresh})
    except httpx.HTTPError:
        raise JobNotConfigured("токен hh не обновился — пройдите авторизацию: python -m hr.cli hh-auth") from None
    store_tokens(conn, data, row["account_id"] if row else None, now)
    return data["access_token"]


# ------------------------------------------------------------------ запросы к API

class BudgetExhausted(Exception):
    """Бюджет страниц прогона исчерпан — прогон завершается с пометкой."""


RETRY_DELAYS = (5, 15)


class Api:
    """GET к api.hh.ru: лимит 1 запрос/с, бюджет страниц, токен с обновлением, остановка на капче."""

    def __init__(self, conn_factory: ConnFactory, http: httpx.Client, clock: Clock, limiter: RateLimiter,
                 budget: PageBudget):
        self.cf, self.http, self.clock, self.limiter, self.budget = conn_factory, http, clock, limiter, budget

    def _token(self, force: bool = False) -> str:
        with self.cf() as conn:
            return access_token(conn, self.http, self.clock.now(), force_refresh=force)

    def get(self, path: str, params: dict | None = None) -> dict:
        token = self._token()
        refreshed = False
        for attempt in range(len(RETRY_DELAYS) + 1):
            if not self.budget.take():
                raise BudgetExhausted
            self.limiter.wait("hh")
            r = self.http.get(f"{get_config().hh_api}{path}", params=params,
                              headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 401 and not refreshed:
                token, refreshed = self._token(force=True), True
                continue
            if r.status_code == 429 or (r.status_code == 403 and "captcha" in r.text.lower()):
                raise JobBlocked("hh просит пройти проверку на робота или ограничил частоту — обход остановлен")
            if r.status_code == 403:
                raise JobNotConfigured("hh: нет доступа к базе резюме у этой учётной записи")
            if r.status_code >= 500 and attempt < len(RETRY_DELAYS):
                self.clock.sleep(RETRY_DELAYS[attempt])
                continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()
        return r.json()


def search_params(conn: psycopg.Connection, profile: dict) -> dict:
    kws = [k for k in (profile.get("keywords") or [])[:6] if k]
    text = profile["title"]
    if kws:
        text += " OR " + " OR ".join(f'"{k}"' if " " in k else k for k in kws)
    params: dict = {"text": text, "per_page": 50, "order_by": "publication_time"}
    geo = profile.get("geo") or "krasnodar"
    if geo == "remote_ru":
        params |= {"area": str(setting(conn, "hh_area_relocation", 113)), "schedule": "remote"}
    else:
        params["area"] = str(setting(conn, "hh_area", 53))
        if geo == "relocation_ru":
            params["relocation"] = "living_or_relocation"
    return params


def selftest(conn: psycopg.Connection, http: httpx.Client, now: datetime) -> dict:
    """Проверка из ТЗ 5.2, шаг 4: сколько резюме видно по тестовому запросу."""
    token = access_token(conn, http, now)
    r = http.get(f"{get_config().hh_api}/resumes", params={"text": "инженер", "area": "53", "per_page": 1},
                 headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return {"found": r.json().get("found")}
