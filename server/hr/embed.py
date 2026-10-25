"""Векторы для поиска по смыслу (person_embedding, vector(1024)).

Если задан EMBED_BASE_URL, вектор берётся у провайдера с OpenAI-совместимым
/embeddings и моделью размерности 1024. Иначе считается в коде: хеширование
словоформ и триграмм. Он слабее модели, но работает без внешних сервисов и
находит «схемотехник Altium» по резюме со словами «схемотехника» и «Altium».
Смешивать два способа в одной базе нельзя: при смене способа запускается
пересчёт `python -m hr.cli reembed`.
"""
from __future__ import annotations

import hashlib
import math
import re

import httpx

from .config import get_config

DIM = 1024


def _hash_vec(text: str) -> list[float]:
    v = [0.0] * DIM
    words = re.findall(r"\w+", text.lower())
    feats: list[tuple[str, float]] = []
    for w in words:
        stem = w[:max(4, len(w) - 2)] if len(w) > 5 else w
        feats.append(("w:" + stem, 1.0))
        padded = f"_{w}_"
        feats += [("t:" + padded[i:i + 3], 0.3) for i in range(len(padded) - 2)]
    for f, weight in feats:
        h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
        v[h % DIM] += weight if (h >> 63) & 1 else -weight
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def embed(text: str) -> list[float]:
    cfg = get_config()
    if cfg.embed_base_url and cfg.embed_model:
        headers = {"Authorization": f"Bearer {cfg.embed_api_key}"} if cfg.embed_api_key else {}
        r = httpx.post(cfg.embed_base_url.rstrip("/") + "/embeddings",
                       json={"model": cfg.embed_model, "input": text[:8000]}, headers=headers, timeout=60)
        r.raise_for_status()
        vec = r.json()["data"][0]["embedding"]
        if len(vec) != DIM:
            raise ValueError(f"модель эмбеддингов отдаёт {len(vec)} измерений, схема ждёт {DIM}")
        return vec
    return _hash_vec(text)


def literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
