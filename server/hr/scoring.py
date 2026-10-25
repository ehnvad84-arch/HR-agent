"""Балл «человек × профиль роли». Считает код, модель только объясняет (раздел 6 ТЗ).

Балл — порядок просмотра, а не вердикт: у каждого признака видно, сколько он дал
из скольких, поэтому любую оценку можно пересчитать и объяснить.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

FEATURE_LABELS = {
    "exp_years": "Опыт по профилю",
    "keywords": "Ключевые слова и инструменты",
    "title_match": "Совпадение должности",
    "donor_company": "Опыт в компании-доноре",
    "tenure": "Держится на месте",
    "geo": "География",
    "salary": "Ожидания против вилки",
    "open_code": "Открытый код и профессиональная активность",
}

DEFAULT_WEIGHTS = {"exp_years": 20, "keywords": 25, "title_match": 15, "donor_company": 10,
                   "tenure": 10, "geo": 10, "salary": 10, "open_code": 0}

# Краснодар и ближняя агломерация: ездить на работу можно без переезда
KRASNODAR_AREA = {"краснодар", "krasnodar", "адыгейск", "яблоновский", "энем", "горячий ключ",
                  "динская", "северская", "афипский", "елизаветинская", "новая адыгея"}

STOP_CAP = 40          # стоп-фактор — балл не выше этого
MUST_PENALTY = 0.1     # за каждое неподтверждённое обязательное требование
MUST_PENALTY_MAX = 0.3


@dataclass
class Person:
    """Сведённые данные человека по всем его наблюдениям."""
    title: str | None = None
    city: str | None = None
    employers: list[str] = field(default_factory=list)
    positions: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    exp_years: float | None = None
    avg_tenure_months: int | None = None
    salary_expect: int | None = None
    relocation_ready: bool | None = None
    text: str = ""
    open_code: float = 0.0           # 1 — подтверждённый GitHub, 0.5 — вероятный

    def haystack(self) -> str:
        return " ".join([self.title or "", " ".join(self.positions), " ".join(self.skills),
                         " ".join(self.employers), self.text]).lower()


@dataclass
class Profile:
    title: str
    geo: str = "krasnodar"
    salary_min: int | None = None
    salary_max: int | None = None
    must_have: list = field(default_factory=list)
    nice_to_have: list = field(default_factory=list)
    stop_factors: list = field(default_factory=list)
    weights: dict = field(default_factory=dict)
    keywords: list[str] = field(default_factory=list)
    donor_companies: list[str] = field(default_factory=list)

    @classmethod
    def from_row(cls, r: dict, criteria: dict | None = None) -> "Profile":
        p = cls(title=r["title"], geo=r.get("geo") or "krasnodar", salary_min=r.get("salary_min"),
                salary_max=r.get("salary_max"), must_have=list(r.get("must_have") or []),
                nice_to_have=list(r.get("nice_to_have") or []), stop_factors=list(r.get("stop_factors") or []),
                weights=dict(r.get("weights") or {}), keywords=list(r.get("keywords") or []),
                donor_companies=list(r.get("donor_companies") or []))
        if criteria:
            p.apply_criteria(criteria)
        return p

    def apply_criteria(self, c: dict) -> None:
        """Правки черновика из бота накладываются поверх профиля, сам профиль не меняется."""
        self.must_have += [parse_requirement(x) if isinstance(x, str) else x for x in c.get("must_have", [])]
        self.nice_to_have += [parse_requirement(x) if isinstance(x, str) else x for x in c.get("nice_to_have", [])]
        self.stop_factors += [parse_requirement(x) if isinstance(x, str) else x for x in c.get("stop_factors", [])]
        self.keywords += [k for k in c.get("keywords", []) if k not in self.keywords]
        self.weights.update(c.get("weights", {}))
        if c.get("salary_min"):
            self.salary_min = c["salary_min"]
        if c.get("salary_max"):
            self.salary_max = c["salary_max"]


# ------------------------------------------------------------------ требования из текста

_REQ_PATTERNS = [
    (re.compile(r"опыт\D{0,40}?(?:от|не менее|больше|более|\>=?)?\s*(\d+(?:[.,]\d)?)\s*(?:год|лет)", re.I),
     lambda m: {"feature": "exp_years", "op": ">=", "value": float(m.group(1).replace(",", "."))}),
    (re.compile(r"(?:длительность|стаж на месте|места работы)\D{0,40}(?:меньше|менее)\s+года", re.I),
     lambda m: {"feature": "avg_tenure_months", "op": "<", "value": 12}),
    (re.compile(r"(?:длительность|стаж на месте|места работы)\D{0,40}(?:меньше|менее)\s+(\d+)\s*мес", re.I),
     lambda m: {"feature": "avg_tenure_months", "op": "<", "value": int(m.group(1))}),
]


def parse_requirement(line: str) -> dict:
    line = line.strip().rstrip(".")
    for rx, build in _REQ_PATTERNS:
        m = rx.search(line)
        if m:
            d = build(m)
            d["text"] = line
            return d
    return {"text": line}


def parse_requirements(text: str) -> list[dict]:
    parts = [p for p in re.split(r"[\n;]|(?<=\.)\s+", text or "") if p.strip()]
    return [parse_requirement(p) for p in parts]


def requirement_text(r: Any) -> str:
    if isinstance(r, str):
        return r
    if r.get("text"):
        return r["text"]
    return f"{FEATURE_LABELS.get(r.get('feature'), r.get('feature'))} {r.get('op')} {r.get('value')}"


# ------------------------------------------------------------------ сравнение строк

def _trigrams(s: str) -> set[str]:
    out: set[str] = set()
    for w in re.findall(r"\w+", s.lower()):
        w = f"  {w} "
        out.update(w[i:i + 3] for i in range(len(w) - 2))
    return out


def trigram_sim(a: str | None, b: str | None) -> float:
    """То же сходство, что pg_trgm similarity()."""
    if not a or not b:
        return 0.0
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _stem(w: str) -> str:
    return w[:max(4, len(w) - 2)] if len(w) > 5 else w


def phrase_found(phrase: str, hay: str) -> bool:
    p = phrase.lower().strip()
    if not p:
        return False
    if p in hay:
        return True
    words = [w for w in re.findall(r"\w+", p) if len(w) >= 4]
    return bool(words) and all(_stem(w) in hay for w in words)


def _check(feature_value: Any, op: str, value: Any) -> bool | None:
    if feature_value is None:
        return None
    return {">=": feature_value >= value, ">": feature_value > value, "<": feature_value < value,
            "<=": feature_value <= value, "==": feature_value == value}.get(op)


def requirement_met(req: Any, person: Person, hay: str) -> bool | None:
    """True — выполнено, False — нарушено, None — по резюме не понять."""
    if isinstance(req, str):
        req = {"text": req}
    feat = req.get("feature")
    if feat:
        return _check(getattr(person, feat, None), req.get("op", ">="), req.get("value"))
    return True if phrase_found(req.get("text", ""), hay) else None


# ------------------------------------------------------------------ признаки

def _f_exp(person: Person, profile: Profile) -> tuple[float, str]:
    target = next((r.get("value") for r in profile.must_have if isinstance(r, dict) and r.get("feature") == "exp_years"), 3)
    if person.exp_years is None:
        return 0.3, "опыт в резюме не указан"
    ratio = min(person.exp_years / (float(target) + 2), 1.0)
    return ratio, f"{person.exp_years:g} лет при ориентире от {float(target):g}"


def _f_keywords(person: Person, profile: Profile, hay: str) -> tuple[float, str, list[str]]:
    if not profile.keywords:
        return 0.5, "ключевые слова в профиле не заданы", []
    found = [k for k in profile.keywords if phrase_found(k, hay)]
    need = max(3, (len(profile.keywords) + 1) // 2)
    return min(len(found) / need, 1.0), (", ".join(found[:6]) if found else "совпадений нет"), found


def _f_title(person: Person, profile: Profile) -> tuple[float, str]:
    candidates = [person.title or ""] + person.positions[:3]
    best = max((trigram_sim(t, profile.title) for t in candidates if t), default=0.0)
    return min(best / 0.6, 1.0), f"«{person.title or '—'}» против «{profile.title}»"


def _f_donor(person: Person, profile: Profile) -> tuple[float, str]:
    if not profile.donor_companies:
        return 0.0, "список доноров пуст"
    emps = [e.lower() for e in person.employers]
    for d in profile.donor_companies:
        dl = d.lower().strip()
        if dl and any(dl in e or (len(e) > 3 and e in dl) or trigram_sim(dl, e) > 0.55 for e in emps):
            return 1.0, f"работал в «{d}»"
    return 0.0, "в донорах не работал"


def _f_tenure(person: Person) -> tuple[float, str]:
    m = person.avg_tenure_months
    if m is None:
        return 0.5, "даты мест работы не указаны"
    return max(0.0, min((m - 6) / 24, 1.0)), f"в среднем {m} мес. на месте"


def _f_geo(person: Person, profile: Profile) -> tuple[float, str]:
    city = (person.city or "").lower().strip()
    local = any(c in city for c in KRASNODAR_AREA)
    if profile.geo == "remote_ru":
        return 1.0, person.city or "город не указан"
    if local:
        return 1.0, person.city
    if profile.geo == "relocation_ru":
        return (0.8 if person.relocation_ready else 0.3), f"{person.city or '?'}, переезд {'готов' if person.relocation_ready else 'не указан'}"
    return (0.5 if person.relocation_ready else 0.0), f"{person.city or '?'}, переезд {'готов' if person.relocation_ready else 'не указан'}"


def _f_salary(person: Person, profile: Profile) -> tuple[float, str]:
    s, lo, hi = person.salary_expect, profile.salary_min, profile.salary_max
    if not s:
        return 0.5, "ожидания не указаны"
    if not hi:
        return 0.5, f"{s:,} ₽, вилка не задана".replace(",", " ")
    if s <= hi:
        return 1.0, f"{s:,} ₽ в вилке".replace(",", " ")
    over = (s - hi) / hi
    return max(0.0, 1 - over * 3), f"{s:,} ₽, выше вилки на {round(over * 100)}%".replace(",", " ")


@dataclass
class Score:
    score: int
    features: dict
    must_missing: list[str]
    must_broken: list[str]
    stop_hit: list[str]
    keywords_found: list[str]


def score(person: Person, profile: Profile) -> Score:
    weights = {**DEFAULT_WEIGHTS, **{k: v for k, v in (profile.weights or {}).items() if k in FEATURE_LABELS}}
    total_w = sum(max(0, w) for w in weights.values()) or 1
    hay = person.haystack()
    kw_ratio, kw_detail, kw_found = _f_keywords(person, profile, hay)
    parts = {
        "exp_years": _f_exp(person, profile),
        "keywords": (kw_ratio, kw_detail),
        "title_match": _f_title(person, profile),
        "donor_company": _f_donor(person, profile),
        "tenure": _f_tenure(person),
        "geo": _f_geo(person, profile),
        "salary": _f_salary(person, profile),
        "open_code": (person.open_code, "профиль на GitHub" if person.open_code else "открытого кода не найдено"),
    }
    features: dict[str, dict] = {}
    raw = 0.0
    for key, (ratio, detail) in parts.items():
        w = max(0, weights.get(key, 0))
        if w == 0:
            continue
        mx = round(w * 100 / total_w)
        pts = round(ratio * mx)
        raw += ratio * w * 100 / total_w
        features[key] = {"label": FEATURE_LABELS[key], "points": pts, "max": mx, "detail": detail}

    missing, broken = [], []
    for req in profile.must_have:
        met = requirement_met(req, person, hay)
        if met is False:
            broken.append(requirement_text(req))
        elif met is None:
            missing.append(requirement_text(req))
    for req in profile.nice_to_have:
        if requirement_met(req, person, hay):
            raw += 2
    penalty = min(MUST_PENALTY * len(missing) + 2 * MUST_PENALTY * len(broken), MUST_PENALTY_MAX)
    raw *= (1 - penalty)
    stop = [requirement_text(r) for r in profile.stop_factors if requirement_met(r, person, hay) is True]
    value = max(0, min(100, round(raw)))
    if stop:
        value = min(value, STOP_CAP)
    if penalty:
        features["_penalty"] = {"label": "Обязательные требования", "points": -round(penalty * 100), "max": 0,
                                "detail": "; ".join(broken + missing)[:300]}
    if stop:
        features["_stop"] = {"label": "Стоп-фактор", "points": 0, "max": 0, "detail": "; ".join(stop)}
    return Score(value, features, missing, broken, stop, kw_found)


def template_explanation(person: Person, profile: Profile, s: Score) -> dict:
    """Объяснение без модели: из тех же признаков, честно и коротко."""
    feats = [f for k, f in s.features.items() if not k.startswith("_") and f["max"]]
    strong = sorted(feats, key=lambda f: f["points"] / f["max"], reverse=True)[:3]
    weak = [f for f in sorted(feats, key=lambda f: f["points"] / f["max"]) if f["points"] / f["max"] < 0.6][:3]
    why = "Сильнее всего: " + "; ".join(f"{f['label'].lower()} — {f['detail']}" for f in strong) + "."
    gap_parts = [f"{f['label'].lower()} ({f['detail']})" for f in weak]
    if s.must_missing:
        gap_parts.append("не подтверждено: " + ", ".join(s.must_missing[:3]))
    if s.must_broken:
        gap_parts.append("не выполнено: " + ", ".join(s.must_broken[:3]))
    if s.stop_hit:
        gap_parts.append("стоп-фактор: " + ", ".join(s.stop_hit))
    gap = ("Чего не хватает: " + "; ".join(gap_parts) + ".") if gap_parts else "Явных пробелов по профилю не видно."
    questions = [f"Расскажите подробнее: {m.lower()}?" for m in s.must_missing[:2]]
    if any(f["label"] == FEATURE_LABELS["salary"] for f in weak):
        questions.append("Что входит в ожидаемый доход и есть ли гибкость?")
    if any(f["label"] == FEATURE_LABELS["geo"] for f in weak):
        questions.append("Рассматриваете ли переезд в Краснодар и на каких условиях?")
    if not questions:
        questions.append("Какой проект за последние два года считаете главным и какова в нём ваша роль?")
    return {"why": why, "gap": gap, "questions": questions[:3]}
