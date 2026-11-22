"""Сквозной прогон на вымышленных данных, без сети и ключей.

    DATABASE_URL=postgresql://…/ruware_hr_demo python scripts/demo_core.py

Пересоздаёт схемы в базе *_demo, заводит три утверждённых профиля и синонимы ключей инженера-электронщика,
принимает выгрузку scripts/demo/habr_demo.csv, считает векторы заглушкой bge-m3 (внутри процесса), оценки
и сигналы и печатает итог: кто вошёл в базу, «Altium ≈ P-CAD», близость по смыслу, предел полных резюме hh.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hr import db, embed, pipeline, quota, scoring, search, synonyms, tasks  # noqa: E402
from hr.db import ConnFactory  # noqa: E402

PROFILES = [
    ("rnd_hw", "Инженерные разработки", "engineer_hw_lead", "Ведущий инженер-электронщик",
     ["Altium", "схемотехника", "трассировка", "ЭМС"], ["Болид"], 150000, 200000),
    ("qc", "ОТК", "otk_engineer", "Инженер ОТК", ["AQL", "входной контроль", "8D"], [], 80000, 110000),
    ("sales", "Продажи", "sales_project", "Менеджер проектных продаж", ["44-ФЗ", "CRM", "проектные продажи"],
     ["Дистрибьютор СБ"], 120000, 160000),
]
# S1–S5: синонимы ключей инженера-электронщика, как их сохранил бы рекрутер в «Базе должностей»
SYNONYMS = [{"keyword": "Altium", "synonyms": ["P-CAD", "KiCad"]},
            {"keyword": "трассировка", "synonyms": ["разводка печатных плат"]},
            {"keyword": "ЭМС", "synonyms": ["электромагнитная совместимость"]}]
OTHER_WORDS = "d-6"   # резюме с «P-CAD» и без «Altium» в habr_demo.csv
CSV = Path(__file__).parent / "demo" / "habr_demo.csv"

# Заглушка bge-m3: близкие по смыслу слова дают одно измерение «понятия», остальные слова — слабый шум.
EMBED_URL, EMBED_MODEL = "http://bge-m3.demo/v1", "bge-m3"
CONCEPTS = {
    "электроника": ("электрон", "микроконтрол", "stm32"),
    "платы": ("altium", "p-cad", "kicad", "трассиров", "разводк", "печатн", "плат"),
    "схемы": ("схемотехн", "схем"),
    "эмс": ("эмс", "помех", "электромагнит", "совместим"),
    "качество": ("aql", "отк", "контрол", "8d", "iso"),
    "продажи": ("продаж", "crm", "44-фз", "тендер", "дистрибьют", "касс"),
}


def stub_vector(text: str) -> list[float]:
    v = [0.0] * embed.DIM
    for w in re.findall(r"[\w-]+", text.lower()):
        hits = [i for i, stems in enumerate(CONCEPTS.values()) if any(w.startswith(s) for s in stems)]
        for i in hits:
            v[i] += 1.0
        if not hits:
            h = int.from_bytes(hashlib.blake2b(w.encode(), digest_size=8).digest(), "little")
            v[len(CONCEPTS) + h % (embed.DIM - len(CONCEPTS))] += 0.2
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def bge_stub() -> httpx.Client:
    """httpx-клиент со службой /embeddings внутри процесса: запрос в сеть не уходит."""
    def handler(req: httpx.Request) -> httpx.Response:
        if not req.url.path.endswith("/embeddings"):
            return httpx.Response(404)
        texts = json.loads(req.content)["input"]
        return httpx.Response(200, json={"data": [{"index": i, "embedding": stub_vector(t)}
                                                  for i, t in enumerate(texts)]})
    return httpx.Client(transport=httpx.MockTransport(handler))


def seed(conn_factory: ConnFactory, now: datetime) -> dict[str, int]:
    """Подразделения, утверждённые профили, синонимы (через synonyms.save — с записью в agent_change)."""
    ids: dict[str, int] = {}
    with conn_factory() as conn:
        uid = db.val(conn, """INSERT INTO app_user (login, full_name, role) VALUES ('demo', 'Демо-рекрутер',
                              'head_recruiter') ON CONFLICT (login) DO UPDATE SET role = EXCLUDED.role
                              RETURNING id""")
        for dept, dtitle, code, title, kws, donors, lo, hi in PROFILES:
            d = db.val(conn, "INSERT INTO department (code, title) VALUES (%s, %s) RETURNING id", (dept, dtitle))
            ids[code] = db.val(conn, """INSERT INTO role_profile (department_id, code, title, version, salary_min,
                                            salary_max, keywords, donor_companies, approved_by, approved_at)
                                        VALUES (%s, %s, %s, '1.0', %s, %s, %s, %s, %s, now()) RETURNING id""",
                               (d, code, title, lo, hi, kws, donors, uid))
        synonyms.save(conn, ids["engineer_hw_lead"], SYNONYMS, uid, now, base_change_id=None)
    for pid in ids.values():
        search.embed_profile(conn_factory, pid, now=now)
    return ids


def run_demo(conn_factory: ConnFactory, say: Callable[[str], None] = print,
             now: datetime | None = None) -> dict:
    """Ядро прогона на готовой схеме. Выгрузку берёт из IMPORT_DIR, векторы — у embed.client() (заглушка).
    Возвращает факты для проверки: карточки, разбор инженера с другой терминологией, предел S11."""
    now = now or datetime.now(UTC)
    ids = seed(conn_factory, now)
    say("профили: " + ", ".join(p[3] for p in PROFILES) + "; синонимы: Altium → P-CAD, KiCad; "
        "трассировка → разводка печатных плат; ЭМС → электромагнитная совместимость")
    for name in ("import_files", "score", "alerts"):
        res = tasks.run(name, conn_factory)
        note = f"; {res.stats.note}" if res.stats.note else ""
        extra = f"; векторов {res.stats.extra.get('vectors', 0)}" if name == "score" and res.stats.extra else ""
        say(f"{name}: {'готово' if res.ok else 'ошибка'} — просмотрено {res.stats.seen}, новых {res.stats.new}, "
            f"карточек {res.stats.persons}, сигналов {res.stats.alerts}{extra}{note}")
    out: dict = {}
    with conn_factory() as conn:
        say("\nКарточки наблюдения (лучший балл):")
        cards = db.rows(conn, """SELECT p.label, max(m.score) AS score FROM person p
                                 JOIN match m ON m.person_id = p.id GROUP BY p.id, p.label ORDER BY score DESC""")
        for r in cards:
            say(f"  {r['score']:>3}  {r['label']}")
        below = db.val(conn, "SELECT count(*) FROM observation WHERE person_id IS NULL")
        say(f"  ниже порога, без карточки: {below}")
        out["cards"], out["below"] = len(cards), below
        say("\nОбратите внимание:")
        for r in db.rows(conn, "SELECT reason, headline FROM alert WHERE state = 'new' ORDER BY id"):
            say(f"  [{r['reason']}] {r['headline']}")
        say(f"\nПоиск по смыслу «схемотехник Altium» ({embed.method()}):")
        for r in search.similar(conn, "схемотехник Altium", limit=3):
            say(f"  {r['similarity']:.2f}  {r['label']}")
        out.update(_other_words(conn, ids["engineer_hw_lead"], say))
        out.update(_hh_full_limit(conn_factory, conn, now, say))
        m = db.one(conn, "SELECT explanation FROM match ORDER BY score DESC LIMIT 1")
        say("\nРазбор лучшего балла:\n  " + m["explanation"].replace("\n", "\n  "))
    return out


def _other_words(conn: psycopg.Connection, profile_id: int, say: Callable[[str], None]) -> dict:
    """S5, S7: инженер с «P-CAD» без «Altium» — в базе, ключ засчитан по синониму, смысл в балле."""
    o = db.one(conn, """SELECT o.person_id, s.code AS source_code, o.raw FROM observation o
                        JOIN source s ON s.id = o.source_id WHERE o.external_id = %s""", (OTHER_WORDS,))
    m = db.one(conn, "SELECT score, features FROM match WHERE person_id = %s AND role_profile_id = %s",
               (o["person_id"], profile_id)) if o and o["person_id"] else None
    say("\nДругая терминология (P-CAD вместо Altium) — «Ведущий инженер-электронщик»:")
    if not m:
        say("  в базу не вошёл")
        return {"other_words": None}
    kw, sem = m["features"].get("keywords", {}), m["features"].get("semantic", {})
    pr = pipeline.current_profiles(conn)
    bare = [{**p, "synonyms": {}} for p in pr]
    n = pipeline.normalize(o["source_code"], o["raw"])
    without = max(scoring.score(pipeline.norm_person(n), scoring.Profile.from_row(p), pipeline.params(conn)).score
                  for p in bare)
    thr = int(db.setting(conn, "watch_threshold", 60))
    say(f"  балл {m['score']}; ключи: {kw.get('detail', '')}")
    say(f"  {scoring.FEATURE_LABELS['semantic']}: {sem.get('detail', 'нет')} ({sem.get('points', 0)} из "
        f"{sem.get('max', 0)})")
    say(f"  без синонимов и смысла лучший балл был бы {without} при пороге {thr} — "
        f"{'в базу бы не вошёл' if without < thr else 'вошёл бы и так'}")
    return {"other_words": {"score": m["score"], "keywords": kw.get("detail", ""),
                            "semantic": sem.get("detail", ""), "sim": sem.get("sim"),
                            "without": without, "threshold": thr}}


def _hh_full_limit(conn_factory: ConnFactory, conn: psycopg.Connection, now: datetime,
                   say: Callable[[str], None]) -> dict:
    """S11: файлы предел не тратят; сбор hh, дошедший до предела, ждёт следующих суток."""
    cap = quota.hh_full_cap(conn)
    before = quota.used(conn, quota.HH_FULL, now=now)
    say(f"\nПолные резюме hh сегодня: {before} из {cap} (файлы и этот прогон предел не тратят)")
    taken = sum(quota.take(conn_factory, quota.HH_FULL, cap, now=now) for _ in range(cap - before))
    extra = quota.take(conn_factory, quota.HH_FULL, cap, now=now)
    used = quota.used(conn, quota.HH_FULL, now=now)
    say(f"  имитация сборов за сутки: взято {taken}, следующее полное резюме — "
        f"{'взято' if extra else 'отказ'}; «Схема»: {used} / {cap}")
    if not extra:
        say("  " + quota.hh_full_note(cap))
    return {"hh_full": {"cap": cap, "used": used, "refused": not extra}}


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not urlparse(dsn).path.endswith("_demo"):
        print("DATABASE_URL должен указывать на базу *_demo: скрипт пересоздаёт её схемы")
        return 2
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS ops CASCADE")
        c.execute("DROP SCHEMA IF EXISTS public CASCADE")
        c.execute("CREATE SCHEMA public")
    print("схема:", ", ".join(db.migrate(dsn)))
    folder = Path(tempfile.mkdtemp(prefix="hr-demo-"))
    shutil.copy(CSV, folder / CSV.name)
    for k in ("LLM_BASE_URL", "EMBED_API_KEY"):   # без сети: модель — по шаблону
        os.environ.pop(k, None)
    os.environ.update(IMPORT_DIR=str(folder), EMBED_BASE_URL=EMBED_URL, EMBED_MODEL=EMBED_MODEL)
    from hr.config import get_config
    get_config.cache_clear()
    embed.client = bge_stub
    try:
        run_demo(db.tx)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
