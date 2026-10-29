"""Оболочка HTTP-приложения: страницы, заголовки безопасности, единый формат ошибок (S2, S35).

Ошибка API — всегда {"error": код, "message": текст по-русски}; подробности исключений наружу не уходят.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import get_config

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parents[1] / "web"

# стили в разметке прототипа — атрибутами style, поэтому 'unsafe-inline' только для стилей; скрипты — свои файлы
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
HEADERS = {"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
           "X-Frame-Options": "DENY"}
MESSAGES = {400: "Некорректный запрос", 401: "Войдите в рабочее место", 403: "Недостаточно прав",
            404: "Не найдено", 405: "Метод не поддерживается", 409: "Действие сейчас недоступно",
            422: "Проверьте введённые данные", 429: "Слишком много запросов"}


class DropQuery(logging.Filter):
    """Журнал доступа uvicorn без строки запроса: в поиске и фильтрах бывает имя человека (security.md)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3 and isinstance(record.args[2], str):
            args = list(record.args)
            args[2] = args[2].split("?", 1)[0]
            record.args = tuple(args)
        return True


def _error(status: int, detail) -> JSONResponse:
    if isinstance(detail, dict) and "error" in detail:
        message = detail.get("message") or MESSAGES.get(status, "Ошибка")
        return JSONResponse({"error": detail["error"], "message": message}, status_code=status)
    # строковый detail — только наш русский текст; служебные английские фразы Starlette заменяем
    own = isinstance(detail, str) and status < 500 and detail not in ("Not Found", "Method Not Allowed")
    return JSONResponse({"error": f"http_{status}", "message": detail if own else MESSAGES.get(status, "Ошибка")},
                        status_code=status)


def install(app: FastAPI) -> None:
    """Подключает обработчики и раздачу server/web. Вызывать после регистрации всех маршрутов."""
    logging.getLogger("uvicorn.access").addFilter(DropQuery())

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        resp = _error(exc.status_code, exc.detail)
        for k, v in (getattr(exc, "headers", None) or {}).items():
            resp.headers[k] = v
        return resp

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = [".".join(str(p) for p in e.get("loc", ())[1:]) for e in exc.errors()]
        return JSONResponse({"error": "validation", "message": MESSAGES[422], "fields": fields}, status_code=422)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        log.error("необработанная ошибка: %s", type(exc).__name__)   # без текста: в нём бывают данные
        return JSONResponse({"error": "internal", "message": "Внутренняя ошибка, повторите позже"}, status_code=500)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(get_config().web_hosts))
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

