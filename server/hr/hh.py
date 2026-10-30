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
from .jobs import JobBlocked, JobLimit, JobNotConfigured

STATE_TTL = timedelta(minutes=10)
AUTH_NEEDED = "нет токена hh, пройдите авторизацию: python -m hr.cli hh-auth"
REAUTH_NEEDED = "hh не принял токен — пройдите авторизацию: python -m hr.cli hh-auth"
SEARCH_GROUP = "4"   # method_access: просмотр резюме, найденных поиском по базе


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


def access_token(conn: psycopg.Connection, http: httpx.Client, now: datetime, force_refresh: bool = False,
                 limiter: RateLimiter | None = None) -> str:
    """Действующий access_token; просроченный (или отвергнутый hh, force_refresh) обновляется один раз.
    limiter — общий с обходом, чтобы POST /token тоже шёл не чаще запроса в секунду."""
    row = one(conn, "SELECT * FROM ops.oauth_token WHERE provider = 'hh' FOR UPDATE")
    if row and not force_refresh and row["expires_at"] > now:
        return crypto.decrypt(row["access_token_enc"])
    refresh = crypto.decrypt(row["refresh_token_enc"]) if row and row["refresh_token_enc"] else \
        get_config().hh_refresh_token
    if not refresh:
        raise JobNotConfigured(AUTH_NEEDED)
    if limiter:
        limiter.wait("hh")
    try:
        data = _token_request(http, {"grant_type": "refresh_token", "refresh_token": refresh})
    except httpx.HTTPError:
        raise JobNotConfigured("токен hh не обновился — пройдите авторизацию: python -m hr.cli hh-auth") from None
    store_tokens(conn, data, row["account_id"] if row else None, now)
    return data["access_token"]


# ------------------------------------------------------------------ запросы к API

class BudgetExhausted(Exception):
    """Бюджет страниц прогона исчерпан — прогон завершается с пометкой."""


class ViewLimitExceeded(JobLimit):
    """Суточный лимит просмотров резюме исчерпан: искать можно, открывать полные резюме — нет."""


RETRY_DELAYS = (5, 15)

# коды из errors[].type, errors[].value и oauth_error ответа hh
TOKEN_EXPIRED = {"token_expired", "token-expired"}
TOKEN_DEAD = {"token-revoked", "token_revoked", "bad_authorization", "bad-auth-type", "client-id-deleted",
              "application_not_found", "used_manager_account_forbidden"}
SEARCH_LIMIT = {"daily_resume_search_limit_exceeded", "daily_resume_search_limit_without_services_exceeded"}
NOT_PAID = {"api_access_payment", "action_must_be_payed"}


def error_codes(r: httpx.Response) -> set[str]:
    try:
        body = r.json()
    except ValueError:
        return set()
    if not isinstance(body, dict):
        return set()
    codes = {str(e[k]) for e in body.get("errors") or [] if isinstance(e, dict) for k in ("type", "value") if e.get(k)}
    if body.get("oauth_error"):
        codes.add(str(body["oauth_error"]))
    return codes


def raise_for_hh(r: httpx.Response) -> None:
    """Отказ hh — в понятную причину: лимит, капча, токен, оплата. Остальное — HTTPStatusError."""
    if r.status_code not in (401, 403, 429):
        r.raise_for_status()
        return
    codes = error_codes(r)
    if r.status_code == 401 or codes & (TOKEN_EXPIRED | TOKEN_DEAD):
        raise JobNotConfigured(REAUTH_NEEDED)
    if "view_limit_exceeded" in codes:
        raise ViewLimitExceeded("исчерпан суточный лимит просмотров резюме hh")
    if codes & SEARCH_LIMIT:
        raise JobLimit("исчерпан суточный лимит поиска резюме hh — обход продолжится в следующий прогон")
    if "captcha_required" in codes:
        raise JobBlocked("hh просит пройти проверку на робота — обход остановлен")
    if r.status_code == 429:
        raise JobBlocked("hh ограничил частоту запросов — обход остановлен")
    if codes & NOT_PAID:
        raise JobNotConfigured("hh: у работодателя нет оплаченного доступа к базе резюме через API")
    raise JobNotConfigured("hh: нет доступа к базе резюме у этой учётной записи")


class Api:
    """GET к api.hh.ru: лимит 1 запрос/с, бюджет страниц, токен с обновлением, остановка на капче и лимитах."""

    def __init__(self, conn_factory: ConnFactory, http: httpx.Client, clock: Clock, limiter: RateLimiter,
                 budget: PageBudget):
        self.cf, self.http, self.clock, self.limiter, self.budget = conn_factory, http, clock, limiter, budget

    def _token(self, force: bool = False) -> str:
        with self.cf() as conn:
            return access_token(conn, self.http, self.clock.now(), force_refresh=force, limiter=self.limiter)

    def get(self, path: str, params: dict | None = None) -> dict:
        token = self._token()
        refreshed = False
        for attempt in range(len(RETRY_DELAYS) + 1):
            if not self.budget.take():
                raise BudgetExhausted
            self.limiter.wait("hh")
            r = self.http.get(f"{get_config().hh_api}{path}", params=params,
                              headers={"Authorization": f"Bearer {token}"})
            # истёкший токен hh отдаёт как 403 token_expired, а не 401
            if not refreshed and (r.status_code == 401 or error_codes(r) & TOKEN_EXPIRED):
                token, refreshed = self._token(force=True), True
                continue
            if r.status_code >= 500 and attempt < len(RETRY_DELAYS):
                self.clock.sleep(RETRY_DELAYS[attempt])
                continue
            raise_for_hh(r)
            return r.json()
        r.raise_for_status()
        return r.json()


def search_params(conn: psycopg.Connection, profile: dict) -> dict:
    kws = [k for k in (profile.get("keywords") or [])[:6] if k]
    text = profile["title"]
    if kws:
        text += " OR " + " OR ".join(f'"{k}"' if " " in k else k for k in kws)
    params: dict = {"text": text, "per_page": 50, "order_by": "relevance"}
    geo = profile.get("geo") or "krasnodar"
    if geo == "remote_ru":
        params |= {"area": str(setting(conn, "hh_area_relocation", 113)), "schedule": "remote"}
    else:
        params["area"] = str(setting(conn, "hh_area", 53))
        if geo == "relocation_ru":
            params["relocation"] = "living_or_relocation"
    return params


def selftest(conn_factory: ConnFactory, http: httpx.Client, now: datetime,
             limiter: RateLimiter | None = None) -> dict:
    """Проверка из ТЗ 5.2, шаг 4: открыт ли поиск по базе через API, платные услуги API,
    остаток просмотров на сегодня и сколько резюме видно по тестовому запросу.
    Токен берётся в своей транзакции: обновлённый (refresh_token одноразовый) не откатится, если проверка упадёт."""
    limiter = limiter or RateLimiter()
    with conn_factory() as conn:
        token = access_token(conn, http, now, limiter=limiter)

    def get(path: str, params: dict | None = None) -> dict:
        limiter.wait("hh")
        r = http.get(f"{get_config().hh_api}{path}", params=params, headers={"Authorization": f"Bearer {token}"})
        raise_for_hh(r)
        return r.json()

    me = get("/me")
    eid, mid = (me.get("employer") or {}).get("id"), (me.get("manager") or {}).get("id")
    if not (me.get("is_employer") and eid and mid):
        raise JobNotConfigured("токен hh выдан не менеджеру работодателя — "
                               "пройдите авторизацию: python -m hr.cli hh-auth")
    groups = get(f"/employers/{eid}/managers/{mid}/method_access").get("items") or []
    search_access = any(str(g.get("id")) == SEARCH_GROUP and (g.get("access") or {}).get("has_access")
                        for g in groups)
    services = [{"type": (s.get("service_type") or {}).get("id"), "name": (s.get("service_type") or {}).get("name"),
                 "balance": (s.get("balance") or {}).get("actual"), "expires_at": s.get("expires_at")}
                for s in get(f"/employers/{eid}/services/payable_api_actions/active").get("items") or []]
    left = get(f"/employers/{eid}/managers/{mid}/limits/resume").get("left") or {}
    found = None
    if search_access:
        found = get("/resumes", {"text": "инженер", "area": "53", "per_page": 1}).get("found")
    return {"search_access": search_access, "services": services, "views_left": left.get("resume_view_from_api"),
            "views_left_site": left.get("resume_view"), "found": found}


def limits_today(conn_factory: ConnFactory, http: httpx.Client, now: datetime,
                 limiter: RateLimiter | None = None) -> dict:
    """Остаток на сегодня: просмотры резюме (полное резюме через API тратит дневной лимит сайта, 500 в сутки —
    замер 2026-10-29) и остаток пакета запросов API_LIMITED. Три запроса, без поиска и без списаний."""
    limiter = limiter or RateLimiter()
    with conn_factory() as conn:
        token = access_token(conn, http, now, limiter=limiter)

    def get(path: str) -> dict:
        limiter.wait("hh")
        r = http.get(f"{get_config().hh_api}{path}", headers={"Authorization": f"Bearer {token}"})
        raise_for_hh(r)
        return r.json()

    me = get("/me")
    eid, mid = (me.get("employer") or {}).get("id"), (me.get("manager") or {}).get("id")
    if not (eid and mid):
        raise JobNotConfigured("токен hh выдан не менеджеру работодателя — "
                               "пройдите авторизацию: python -m hr.cli hh-auth")
    left = get(f"/employers/{eid}/managers/{mid}/limits/resume").get("left") or {}
    package = [(s.get("balance") or {}).get("actual") for s in
               get(f"/employers/{eid}/services/payable_api_actions/active").get("items") or []
               if (s.get("service_type") or {}).get("id") == "API_LIMITED"]
    api_left = left.get("resume_view_from_api")
    views = api_left if api_left is not None else left.get("resume_view")
    return {"views_left": max(0, int(views or 0)), "package_left": package[0] if package else None}
