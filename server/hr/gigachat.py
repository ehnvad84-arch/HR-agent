"""GigaChat через единую точку llm.complete (AD-5): токен доступа, один поток, пауза при лимите.

Токен живёт 30 минут, держится только в памяти процесса и в базу и журналы не попадает.
Бесплатный пакет физлица отвечает в один поток: второй вызов ждёт не дольше BUSY_WAIT_S, иначе шаблон.
Ответ 429 или исчерпанный пакет — пауза: вызовы до её конца сразу уходят в шаблон.
"""
from __future__ import annotations

import ssl
import threading
import time
import uuid
from contextlib import contextmanager

import httpx

from .config import get_config

BUSY_WAIT_S = 5
REFRESH_BEFORE_S = 60
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

_lock = threading.Lock()          # один поток генерации
_token_lock = threading.Lock()
_token: tuple[str, float] | None = None   # (токен, истекает в unix-секундах)
_paused_until = 0.0


class Unavailable(RuntimeError):
    """Модель сейчас недоступна: занята, на паузе, не настроена или не ответила."""


def reset() -> None:
    global _token, _paused_until
    _token, _paused_until = None, 0.0


def tls_context() -> ssl.SSLContext:
    """Системные корневые сертификаты плюс сертификат НУЦ Минцифры из файла; проверка не отключается."""
    ctx = ssl.create_default_context()
    ca = get_config().gigachat_ca_file
    if ca:
        try:
            ctx.load_verify_locations(ca)
        except (OSError, ssl.SSLError):
            raise Unavailable("сертификат НУЦ из GIGACHAT_CA_FILE не читается") from None
    return ctx


def client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, verify=tls_context())


def pause(minutes: float) -> None:
    global _paused_until
    _paused_until = max(_paused_until, time.time() + minutes * 60)


def paused() -> bool:
    return time.time() < _paused_until


def token(http: httpx.Client, force: bool = False) -> str:
    global _token
    cfg = get_config()
    with _token_lock:
        if not force and _token and _token[1] - REFRESH_BEFORE_S > time.time():
            return _token[0]
        try:
            r = http.post(cfg.gigachat_oauth_url, data={"scope": cfg.gigachat_scope},
                          headers={"Authorization": f"Basic {cfg.gigachat_auth_key}", "RqUID": str(uuid.uuid4()),
                                   "Accept": "application/json"})
            r.raise_for_status()
            data = r.json()
            exp = float(data["expires_at"])
        except (httpx.HTTPError, KeyError, ValueError):
            raise Unavailable("GigaChat не выдал токен доступа") from None
        if exp > 1e12:          # в старых ответах — миллисекунды
            exp /= 1000
        _token = (data["access_token"], exp)
        return _token[0]


@contextmanager
def turn():
    """Очередь в один поток: занято дольше BUSY_WAIT_S — Unavailable."""
    if paused():
        raise Unavailable("GigaChat на паузе после ограничения")
    if not _lock.acquire(timeout=BUSY_WAIT_S):
        raise Unavailable("модель занята")
    try:
        yield
    finally:
        _lock.release()


def post_chat(http: httpx.Client, body: dict, pause_minutes: float) -> httpx.Response:
    """POST /chat/completions с токеном: один повтор на 401 с новым токеном; 429 и 402 — пауза."""
    url = get_config().gigachat_base_url.rstrip("/") + "/chat/completions"
    r = None
    for force in (False, True):
        r = http.post(url, json=body, headers={"Authorization": f"Bearer {token(http, force)}",
                                               "Accept": "application/json"})
        if r.status_code != 401:
            break
    if r.status_code in (402, 429):
        pause(pause_minutes)
        raise Unavailable("GigaChat ограничил запросы или пакет исчерпан")
    return r
