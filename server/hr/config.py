"""Переменные окружения из .env (раздел 5.1 ТЗ). Секреты живут только здесь."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    v = _env(name)
    return int(v) if v else default


def _float(name: str, default: float) -> float:
    v = _env(name)
    return float(v) if v else default


@dataclass(frozen=True)
class Config:
    domain: str = field(default_factory=lambda: _env("DOMAIN", "localhost"))
    tz: str = field(default_factory=lambda: _env("TZ", "Europe/Moscow"))
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "postgresql://ruware@localhost/ruware_hr"))

    # объектное хранилище; пусто — файлы складываются в STORAGE_DIR
    s3_endpoint: str = field(default_factory=lambda: _env("S3_ENDPOINT"))
    s3_key: str = field(default_factory=lambda: _env("S3_KEY"))
    s3_secret: str = field(default_factory=lambda: _env("S3_SECRET"))
    s3_bucket: str = field(default_factory=lambda: _env("S3_BUCKET", "resumes"))
    storage_dir: str = field(default_factory=lambda: _env("STORAGE_DIR", "./data/files"))

    # языковая модель, раздел 6 ТЗ; пусто — объяснения и письма собираются по шаблону
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "openai"))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL"))
    llm_temperature: float = field(default_factory=lambda: _float("LLM_TEMPERATURE", 0.1))
    llm_max_tokens: int = field(default_factory=lambda: _int("LLM_MAX_TOKENS", 4096))
    llm_timeout_s: int = field(default_factory=lambda: _int("LLM_TIMEOUT_S", 90))
    # эмбеддинги для поиска по смыслу; пусто — хеширование признаков в коде
    embed_base_url: str = field(default_factory=lambda: _env("EMBED_BASE_URL"))
    embed_api_key: str = field(default_factory=lambda: _env("EMBED_API_KEY"))
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL"))

    hh_client_id: str = field(default_factory=lambda: _env("HH_CLIENT_ID"))
    hh_client_secret: str = field(default_factory=lambda: _env("HH_CLIENT_SECRET"))
    hh_refresh_token: str = field(default_factory=lambda: _env("HH_REFRESH_TOKEN"))
    hh_employer_id: str = field(default_factory=lambda: _env("HH_EMPLOYER_ID"))
    hh_user_agent: str = field(default_factory=lambda: _env("HH_USER_AGENT", "RuwareHR/1.0 (hr@example.ru)"))
    hh_api: str = field(default_factory=lambda: _env("HH_API", "https://api.hh.ru"))
    hh_oauth: str = field(default_factory=lambda: _env("HH_OAUTH", "https://hh.ru"))

    trudvsem_base: str = field(default_factory=lambda: _env("TRUDVSEM_BASE", "https://opendata.trudvsem.ru/api/v1"))

    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: _int("SMTP_PORT", 587))
    smtp_user: str = field(default_factory=lambda: _env("SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    smtp_from: str = field(default_factory=lambda: _env("SMTP_FROM") or _env("SMTP_USER"))
    digest_to: str = field(default_factory=lambda: _env("DIGEST_TO"))
    report_to: str = field(default_factory=lambda: _env("REPORT_TO"))

    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))
    manager_bot_token: str = field(default_factory=lambda: _env("MANAGER_BOT_TOKEN"))
    bot_api_token: str = field(default_factory=lambda: _env("BOT_API_TOKEN"))
    stt_url: str = field(default_factory=lambda: _env("STT_URL"))

    github_token: str = field(default_factory=lambda: _env("GITHUB_TOKEN"))
    vk_service_token: str = field(default_factory=lambda: _env("VK_SERVICE_TOKEN"))
    tgstat_token: str = field(default_factory=lambda: _env("TGSTAT_TOKEN"))
    firecrawl_base_url: str = field(default_factory=lambda: _env("FIRECRAWL_BASE_URL"))
    firecrawl_api_key: str = field(default_factory=lambda: _env("FIRECRAWL_API_KEY"))

    exchange_dir: str = field(default_factory=lambda: _env("EXCHANGE_DIR", "./data/exchange"))
    import_dir: str = field(default_factory=lambda: _env("IMPORT_DIR", "./data/import"))

    jwt_secret: str = field(default_factory=lambda: _env("JWT_SECRET", "dev-secret-change-me"))
    session_hours: int = field(default_factory=lambda: _int("SESSION_HOURS", 12))
    cookie_secure: bool = field(default_factory=lambda: _env("COOKIE_SECURE", "1") == "1")
    # 1 — исходящие письма и сообщения не уходят, а пишутся в журнал (для стенда и тестов)
    dry_run_outbound: bool = field(default_factory=lambda: _env("DRY_RUN_OUTBOUND", "0") == "1")

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)


@lru_cache
def get_config() -> Config:
    return Config()


def reset_config() -> None:
    get_config.cache_clear()
