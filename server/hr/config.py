"""Переменные окружения из .env (раздел 5.1 ТЗ). Секреты живут только там."""
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
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "postgresql://ruware@localhost/ruware_hr"))
    # ключ шифрования токенов внешних сервисов в ops.oauth_token
    secrets_key: str = field(default_factory=lambda: _env("SECRETS_KEY"))

    # языковая модель (раздел 6 ТЗ); пусто — объяснения по шаблону
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL"))
    llm_temperature: float = field(default_factory=lambda: _float("LLM_TEMPERATURE", 0.1))
    llm_timeout_s: int = field(default_factory=lambda: _int("LLM_TIMEOUT_S", 90))
    # эмбеддинги; пусто — вектор считается в коде (hash-v1)
    embed_base_url: str = field(default_factory=lambda: _env("EMBED_BASE_URL"))
    embed_api_key: str = field(default_factory=lambda: _env("EMBED_API_KEY"))
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL"))

    hh_client_id: str = field(default_factory=lambda: _env("HH_CLIENT_ID"))
    hh_client_secret: str = field(default_factory=lambda: _env("HH_CLIENT_SECRET"))
    hh_refresh_token: str = field(default_factory=lambda: _env("HH_REFRESH_TOKEN"))
    hh_employer_id: str = field(default_factory=lambda: _env("HH_EMPLOYER_ID"))
    hh_user_agent: str = field(default_factory=lambda: _env("HH_USER_AGENT", "RuwareHR/1.0 (hr@example.ru)"))
    hh_redirect_uri: str = field(default_factory=lambda: _env("HH_REDIRECT_URI"))
    hh_api: str = field(default_factory=lambda: _env("HH_API", "https://api.hh.ru"))
    hh_oauth: str = field(default_factory=lambda: _env("HH_OAUTH", "https://hh.ru"))

    trudvsem_base: str = field(default_factory=lambda: _env("TRUDVSEM_BASE",
                                                            "https://opendata.trudvsem.ru/api/v1/integration"))
    github_token: str = field(default_factory=lambda: _env("GITHUB_TOKEN"))
    import_dir: str = field(default_factory=lambda: _env("IMPORT_DIR", "./data/import"))

    session_key: str = field(default_factory=lambda: _env("SESSION_KEY"))
    # хосты, с которых открывают рабочее место: проверка Origin и TrustedHost (AD-6, через туннель)
    web_hosts: tuple[str, ...] = field(default_factory=lambda: tuple(
        h.strip() for h in _env("WEB_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)


@lru_cache
def get_config() -> Config:
    return Config()
