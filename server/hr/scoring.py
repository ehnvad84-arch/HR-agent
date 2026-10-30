"""Балл «человек × профиль роли». Считает код, модель только объясняет (AD-2).

Балл — порядок просмотра, а не вердикт: у каждого признака видно, сколько он дал
из скольких. Чистые функции: без базы и сети, пороги приходят параметрами из settings.
"""
from __future__ import annotations

import re
from collections.abc import Callable
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
    "open_code": "Открытый код",
}

# Только для профиля без весов; это видно в Score.default_weights
_DEFAULT_WEIGHTS = {"exp_years": 20, "keywords": 25, "title_match": 15, "donor_company": 10,
                    "tenure": 10, "geo": 10, "salary": 10, "open_code": 0}
_OPEN_CODE_DEPTS = {"rnd_hw", "rnd_sw"}   # инженерные разработки и разработка ПО (Решения спеки)

NEGATIONS = ("нет", "без", "не", "отсутствует", "отсутствие")


def default_weights(dept_code: str | None) -> dict[str, int]:
    w = dict(_DEFAULT_WEIGHTS)
    if dept_code in _OPEN_CODE_DEPTS:
        w["open_code"] = 10
    return w


@dataclass
class Params:
    """Пороги из settings: stop_factor_cap, must_penalty_pct, must_penalty_max_pct, local_area_cities."""
    stop_cap: int = 40
    must_penalty_pct: int = 10
    must_penalty_max_pct: int = 30
    local_cities: set[str] = field(default_factory=lambda: {"краснодар"})

    @classmethod
    def load(cls, get: Callable[[str, Any], Any]) -> Params:
        return cls(stop_cap=int(get("stop_factor_cap", 40)),
                   must_penalty_pct=int(get("must_penalty_pct", 10)),
                   must_penalty_max_pct=int(get("must_penalty_max_pct", 30)),
                   local_cities={c.lower() for c in get("local_area_cities", ["краснодар"])})


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
    open_code: bool = False          # есть подтверждённый профиль GitHub или GitLab

    def haystack(self) -> str:
        return " ".join([self.title or "", " ".join(self.positions), " ".join(self.skills),
                         " ".join(self.employers), self.text]).lower()


@dataclass
class Profile:
    title: str
    geo: str = "krasnodar"
    dept_code: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    must_have: list = field(default_factory=list)
    nice_to_have: list = field(default_factory=list)
    stop_factors: list = field(default_factory=list)
    weights: dict = field(default_factory=dict)
    keywords: list[str] = field(default_factory=list)
    donor_companies: list[str] = field(default_factory=list)

    @classmethod
    def from_row(cls, r: dict, criteria: dict | None = None) -> Profile:
        p = cls(title=r["title"], geo=r.get("geo") or "krasnodar", dept_code=r.get("dept_code"),
                salary_min=r.get("salary_min"), salary_max=r.get("salary_max"),
                must_have=_reqs(r.get("must_have")), nice_to_have=_reqs(r.get("nice_to_have")),
                stop_factors=_reqs(r.get("stop_factors")), weights=dict(r.get("weights") or {}),
                keywords=list(r.get("keywords") or []), donor_companies=list(r.get("donor_companies") or []))
        if criteria:
            p.apply_criteria(criteria)
        return p

    def apply_criteria(self, c: dict) -> None:
        """Правки черновика из бота поверх профиля; сам профиль не меняется."""
        self.must_have += _reqs(c.get("must_have"))
        self.nice_to_have += _reqs(c.get("nice_to_have"))
        self.stop_factors += _reqs(c.get("stop_factors"))
        self.keywords += [k for k in c.get("keywords", []) if k not in self.keywords]
        minus = {m.lower() for m in c.get("keywords_minus", [])}   # личное «не в счёт» (manager-cabinet S15)
        if minus:
            self.keywords = [k for k in self.keywords if k.lower() not in minus]
            self.nice_to_have = [r for r in self.nice_to_have if str(r.get("text", "")).lower() not in minus]
        self.weights.update(c.get("weights", {}))
        self.salary_min = c.get("salary_min") or self.salary_min
        self.salary_max = c.get("salary_max") or self.salary_max


# ------------------------------------------------------------------ требования

_REQ_PATTERNS = [
    (re.compile(r"опыт\D{0,40}?(?:от|не менее|более|больше)?\s*(\d+(?:[.,]\d)?)\s*(?:год|лет)", re.I),
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
            return {**build(m), "text": line}
    return {"text": line}


def _reqs(items: Any) -> list[dict]:
    return [parse_requirement(x) if isinstance(x, str) else dict(x) for x in (items or [])]


def requirement_text(r: dict) -> str:
    return r.get("text") or f"{FEATURE_LABELS.get(r.get('feature'), r.get('feature'))} {r.get('op')} {r.get('value')}"


# ------------------------------------------------------------------ сравнение строк

def _trigrams(s: str) -> set[str]:
    out: set[str] = set()
    for w in re.findall(r"\w+", s.lower()):
        w = f"  {w} "
        out.update(w[i:i + 3] for i in range(len(w) - 2))
    return out


def trigram_sim(a: str | None, b: str | None) -> float:
    """То же сходство, что pg_trgm similarity()."""
    ta, tb = _trigrams(a or ""), _trigrams(b or "")
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def _stem(w: str) -> str:
    return w[:max(4, len(w) - 2)] if len(w) > 5 else w


def phrase_found(phrase: str, hay: str) -> bool:
    """Фраза или все её значимые слова (по основам) есть в тексте."""
    p = phrase.lower().strip()
    if not p:
        return False
    if p in hay:
        return True
    words = [w for w in re.findall(r"\w+", p) if len(w) >= 4]
    return bool(words) and all(_stem(w) in hay for w in words)


def exact_without_negation(phrase: str, hay: str) -> bool:
    """Точная фраза, перед которой нет «нет», «без», «не»: «нет судимости» не равно «судимость»."""
    p = phrase.lower().strip()
    if not p:
        return False
    for m in re.finditer(re.escape(p), hay):
        before = re.findall(r"\w+", hay[max(0, m.start() - 20):m.start()])
        if not before or before[-1] not in NEGATIONS:
            return True
    return False


def _check(v: Any, op: str, target: Any) -> bool | None:
    if v is None:
        return None
    return {">=": v >= target, ">": v > target, "<": v < target, "<=": v <= target, "==": v == target}.get(op)


def requirement_met(req: dict, person: Person, hay: str) -> bool | None:
    """True — выполнено, False — нарушено, None — по резюме не понять."""
    if req.get("feature"):
        return _check(getattr(person, req["feature"], None), req.get("op", ">="), req.get("value"))
    return True if phrase_found(req.get("text", ""), hay) else None


def stop_hit(req: dict, person: Person, hay: str) -> bool:
    if req.get("feature"):
        return _check(getattr(person, req["feature"], None), req.get("op", ">="), req.get("value")) is True
    return exact_without_negation(req.get("text", ""), hay)


# ------------------------------------------------------------------ признаки

def _f_exp(person: Person, profile: Profile) -> tuple[float, str]:
    target = next((r["value"] for r in profile.must_have if r.get("feature") == "exp_years"), 3)
    if person.exp_years is None:
        return 0.3, "опыт в резюме не указан"
    detail = f"{person.exp_years:g} лет при ориентире от {float(target):g}"
    return min(person.exp_years / (float(target) + 2), 1.0), detail


def _f_keywords(profile: Profile, hay: str) -> tuple[float, str]:
    if not profile.keywords:
        return 0.5, "ключевые слова в профиле не заданы"
    found = [k for k in dict.fromkeys(profile.keywords) if phrase_found(k, hay)]
    need = max(3, (len(profile.keywords) + 1) // 2)
    return min(len(found) / need, 1.0), (", ".join(found[:6]) if found else "совпадений нет")


def _f_title(person: Person, profile: Profile) -> tuple[float, str]:
    best = max((trigram_sim(t, profile.title) for t in [person.title, *person.positions[:3]] if t), default=0.0)
    return min(best / 0.6, 1.0), f"«{person.title or '—'}» против «{profile.title}»"


def _f_donor(person: Person, profile: Profile) -> tuple[float, str]:
    emps = [e.lower() for e in person.employers]
    for d in profile.donor_companies:
        dl = d.lower().strip()
        if dl and any(dl in e or trigram_sim(dl, e) > 0.55 for e in emps):
            return 1.0, f"работал в «{d}»"
    if not profile.donor_companies:
        return 0.5, "список доноров не задан"
    return 0.0, "в донорах не работал"


def _f_tenure(person: Person) -> tuple[float, str]:
    m = person.avg_tenure_months
    if m is None:
        return 0.5, "даты мест работы не указаны"
    return max(0.0, min((m - 6) / 24, 1.0)), f"в среднем {m} мес. на месте"


def _f_geo(person: Person, profile: Profile, params: Params) -> tuple[float, str]:
    city = (person.city or "").lower().strip()
    reloc = "готов к переезду" if person.relocation_ready else "переезд не указан"
    if profile.geo == "remote_ru":
        return 1.0, person.city or "город не указан"
    if any(c in city for c in params.local_cities):
        return 1.0, person.city or ""
    if profile.geo == "relocation_ru":
        return (0.8 if person.relocation_ready else 0.3), f"{person.city or '?'}, {reloc}"
    return (0.5 if person.relocation_ready else 0.0), f"{person.city or '?'}, {reloc}"


def _rub(v: int) -> str:
    return f"{v:,}".replace(",", " ") + " ₽"


def _f_salary(person: Person, profile: Profile) -> tuple[float, str]:
    s, hi = person.salary_expect, profile.salary_max
    if not s:
        return 0.5, "ожидания не указаны"
    if not hi:
        return 0.5, f"{_rub(s)}, вилка не задана"
    if s <= hi:
        return 1.0, f"{_rub(s)} в вилке"
    over = (s - hi) / hi
    return max(0.0, 1 - over * 3), f"{_rub(s)}, выше вилки на {round(over * 100)}%"


@dataclass
class Score:
    score: int
    features: dict
    must_missing: list[str]
    must_broken: list[str]
    stop_hit: list[str]
    default_weights: bool = False


def _f_keywords_partial(profile: Profile, hay: str) -> tuple[float, str]:
    """Выдача поиска без навыков: не найденное ещё не значит «нет», решит полное резюме."""
    ratio, detail = _f_keywords(profile, hay)
    return (ratio, detail) if ratio >= 0.5 else (0.5, "навыки видны только в полном резюме")


def score(person: Person, profile: Profile, params: Params, partial: bool = False) -> Score:
    """partial — неполные данные (выдача поиска): только для решения, запрашивать ли полное резюме."""
    use_defaults = not profile.weights
    weights = default_weights(profile.dept_code) if use_defaults else \
        {k: v for k, v in profile.weights.items() if k in FEATURE_LABELS}
    weights = {k: max(0, int(v)) for k, v in weights.items() if v}
    total_w = sum(weights.values()) or 1
    hay = person.haystack()
    parts = {
        "exp_years": lambda: _f_exp(person, profile),
        "keywords": lambda: (_f_keywords_partial if partial else _f_keywords)(profile, hay),
        "title_match": lambda: _f_title(person, profile),
        "donor_company": lambda: _f_donor(person, profile),
        "tenure": lambda: _f_tenure(person),
        "geo": lambda: _f_geo(person, profile, params),
        "salary": lambda: _f_salary(person, profile),
        "open_code": lambda: (1.0, "подтверждённый профиль") if person.open_code
        else (0.0, "не найден или не подтверждён"),
    }
    features: dict[str, dict] = {}
    raw = 0.0
    for key, w in weights.items():
        ratio, detail = parts[key]()
        mx = round(w * 100 / total_w)
        features[key] = {"label": FEATURE_LABELS[key], "points": round(ratio * mx), "max": mx, "detail": detail}
        raw += ratio * w * 100 / total_w

    missing, broken = [], []
    for req in profile.must_have:
        met = requirement_met(req, person, hay)
        if met is False:
            broken.append(requirement_text(req))
        elif met is None:
            missing.append(requirement_text(req))
    raw += 2 * sum(1 for req in profile.nice_to_have if requirement_met(req, person, hay))
    penalty = min(params.must_penalty_pct * (len(missing) + len(broken)), params.must_penalty_max_pct)
    raw *= 1 - penalty / 100
    stops = [requirement_text(r) for r in profile.stop_factors if stop_hit(r, person, hay)]
    value = max(0, min(100, round(raw)))
    if stops:
        value = min(value, params.stop_cap)
        features["_stop"] = {"label": "Стоп-фактор", "points": 0, "max": 0, "detail": "; ".join(stops)}
    if penalty:
        features["_penalty"] = {"label": "Обязательные требования", "points": -penalty, "max": 0,
                                "detail": "; ".join(broken + missing)[:300]}
    return Score(value, features, missing, broken, stops, use_defaults)


def template_explanation(s: Score) -> dict:
    """Разбор без модели: из тех же признаков, честно и коротко (S19)."""
    feats = [f for k, f in s.features.items() if not k.startswith("_") and f["max"]]
    strong = sorted(feats, key=lambda f: f["points"] / f["max"], reverse=True)[:3]
    weak = [f for f in sorted(feats, key=lambda f: f["points"] / f["max"]) if f["points"] / f["max"] < 0.6][:3]
    why = "Сильнее всего: " + "; ".join(f"{f['label'].lower()} — {f['detail']}" for f in strong) + "."
    gap = [f"{f['label'].lower()} ({f['detail']})" for f in weak]
    if s.must_missing:
        gap.append("не подтверждено: " + ", ".join(s.must_missing[:3]))
    if s.must_broken:
        gap.append("не выполнено: " + ", ".join(s.must_broken[:3]))
    if s.stop_hit:
        gap.append("стоп-фактор: " + ", ".join(s.stop_hit))
    questions = [f"Расскажите подробнее: {m[:1].lower()}{m[1:]}?" for m in s.must_missing[:2]]
    labels = {f["label"] for f in weak}
    if FEATURE_LABELS["salary"] in labels:
        questions.append("Что входит в ожидаемый доход и есть ли гибкость?")
    if FEATURE_LABELS["geo"] in labels:
        questions.append("Рассматриваете ли переезд в Краснодар и на каких условиях?")
    if not questions:
        questions.append("Какой проект за последние два года считаете главным и какова в нём ваша роль?")
    return {"why": why,
            "gap": ("Чего не хватает: " + "; ".join(gap) + ".") if gap else "Явных пробелов по профилю не видно.",
            "questions": questions[:3]}
