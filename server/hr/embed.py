"""Векторы для поиска по смыслу (person_embedding, vector(1024), S31).

Способ записывается в person_embedding.embed_model: векторы разных способов
несравнимы, поиск идёт только по текущему. Без провайдера — hash-v1: хеширование
основ слов и триграмм в коде, без внешних сервисов. Смена способа — hr.cli reembed.
"""
from __future__ import annotations

import hashlib
import math
import re

import httpx

from .config import get_config

DIM = 1024
HASH = "hash-v1"


def method() -> str:
    cfg = get_config()
    return cfg.embed_model if cfg.embed_base_url and cfg.embed_model else HASH


def hash_vector(text: str) -> list[float]:
    v = [0.0] * DIM
    for w in re.findall(r"\w+", text.lower()):
        stem = w[:max(4, len(w) - 3)] if len(w) > 6 else w
        feats = [("w:" + stem, 1.0)] + [("t:" + f"_{w}_"[i:i + 3], 0.3) for i in range(len(w))]
        for f, weight in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
            v[h % DIM] += weight if (h >> 63) & 1 else -weight
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def api_vector(text: str, http: httpx.Client | None = None) -> list[float]:
    cfg = get_config()
    headers = {"Authorization": f"Bearer {cfg.embed_api_key}"} if cfg.embed_api_key else {}
    r = (http or httpx.Client(timeout=20)).post(cfg.embed_base_url.rstrip("/") + "/embeddings",
                                                json={"model": cfg.embed_model, "input": text[:8000]}, headers=headers)
    r.raise_for_status()
    vec = r.json()["data"][0]["embedding"]
    if len(vec) != DIM:
        raise ValueError(f"модель эмбеддингов отдаёт {len(vec)} измерений, схема ждёт {DIM}")
    return vec


def vector(text: str, http: httpx.Client | None = None) -> list[float]:
    return hash_vector(text) if method() == HASH else api_vector(text, http)


def literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
