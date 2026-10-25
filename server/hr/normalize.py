"""Разбор ответа площадки в единый набор полей наблюдения.

Каждая площадка отдаёт резюме по-своему. Здесь всё приводится к одному виду:
по нему считаются хеш версии, дифф, признаки и балл. Контакты и ФИО в зону
наблюдения не попадают: из сырого ответа они вырезаются до записи, а имя
остаётся только в вариантах написания для поиска следов в сети.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Any

# Поля, которые не должны оказаться в observation.raw ни при каком источнике
PERSONAL_KEYS = {
    "first_name", "last_name", "middle_name", "contact", "contacts", "phone", "phones", "email",
    "emails", "birth_date", "fio", "name", "full_name", "photo", "telegram", "whatsapp", "skype",
    "owner", "actions", "download", "contactPerson", "contactPhone", "contactEmail",
}

# Значения hh «job_search_status», при которых человек ищет работу
SEARCH_OPEN = {"active_search", "looking_for_offers", "has_job_offer", "accepted_job_offer_false"}


@dataclass
class Norm:
    external_id: str
    url: str | None = None
    title: str | None = None
    city: str | None = None
    employer_current: str | None = None
    employers: list[str] = field(default_factory=list)
    positions: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    exp_years: float | None = None
    avg_tenure_months: int | None = None
    salary_expect: int | None = None
    relocation_ready: bool | None = None
    open_for_search: bool | None = None
    updated_at_src: datetime | None = None
    education: str | None = None
    about: str | None = None
    # не пишутся в observation, нужны сшивке и поиску следов
    name: str | None = None
    nicknames: list[str] = field(default_factory=list)

    def significant(self) -> dict:
        """То, что сравнивается между версиями: служебные поля не входят."""
        d = asdict(self)
        for k in ("name", "nicknames", "url"):
            d.pop(k, None)
        d["skills"] = sorted({s.lower() for s in self.skills})
        d["employers"] = list(self.employers)
        d["updated_at_src"] = self.updated_at_src.isoformat() if self.updated_at_src else None
        return d

    def content_hash(self) -> str:
        blob = json.dumps(self.significant(), ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def text(self) -> str:
        """Плоский текст для поиска ключевых слов и эмбеддинга."""
        return " ".join(filter(None, [self.title, self.city, self.employer_current, " ".join(self.employers),
                                      " ".join(self.positions), " ".join(self.skills), self.education, self.about]))


def strip_personal(raw: Any) -> Any:
    if isinstance(raw, dict):
        return {k: strip_personal(v) for k, v in raw.items() if k not in PERSONAL_KEYS}
    if isinstance(raw, list):
        return [strip_personal(v) for v in raw]
    return raw


def _dt(v: Any) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s.replace("+0300", "+03:00") if fmt.endswith("%z") else s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _months_between(start: Any, end: Any) -> int | None:
    a, b = _dt(start), _dt(end) if end else datetime.now()
    if not a or not b:
        return None
    return max(0, (b.year - a.year) * 12 + b.month - a.month)


def _int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).replace(" ", "").replace(" ", "")))
    except ValueError:
        return None


def _split(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [s.strip() for s in re.split(r"[;,\n]", str(v)) if s.strip()]


def _bool(v: Any) -> bool | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "да", "yes", "y", "+")


def _tenure(jobs: list[tuple[Any, Any]]) -> int | None:
    months = [m for m in (_months_between(s, e) for s, e in jobs) if m is not None]
    return round(sum(months) / len(months)) if months else None


# ------------------------------------------------------------------ hh.ru

def from_hh(item: dict) -> Norm:
    """Элемент поиска /resumes или полное резюме /resumes/{id}."""
    exp = item.get("experience") or []
    employers = [e.get("company") or (e.get("employer") or {}).get("name") for e in exp]
    employers = [e for e in employers if e]
    current = None
    if exp and not exp[0].get("end"):
        current = employers[0] if employers else None
    salary = item.get("salary") or {}
    amount = salary.get("amount") if isinstance(salary, dict) else salary
    total = (item.get("total_experience") or {}).get("months")
    skills = list(item.get("skill_set") or [])
    reloc = (item.get("relocation") or {}).get("type", {})
    reloc_id = reloc.get("id") if isinstance(reloc, dict) else reloc
    status = (item.get("job_search_status") or {}).get("id")
    name = " ".join(filter(None, [item.get("last_name"), item.get("first_name")])) or None
    edu = item.get("education") or {}
    edu_level = (edu.get("level") or {}).get("name") if isinstance(edu, dict) else None
    return Norm(
        external_id=str(item["id"]),
        url=item.get("alternate_url") or item.get("url"),
        title=item.get("title"),
        city=(item.get("area") or {}).get("name"),
        employer_current=current,
        employers=employers,
        positions=[e.get("position") for e in exp if e.get("position")],
        skills=skills,
        exp_years=round(total / 12, 1) if total is not None else None,
        avg_tenure_months=_tenure([(e.get("start"), e.get("end")) for e in exp]),
        salary_expect=_int(amount),
        relocation_ready=None if reloc_id is None else reloc_id != "no_relocation",
        open_for_search=None if status is None else status in SEARCH_OPEN,
        updated_at_src=_dt(item.get("updated_at") or item.get("updated")),
        education=edu_level,
        about=item.get("skills"),
        name=name,
    )


# ------------------------------------------------------------------ Работа России

def from_trudvsem(item: dict) -> Norm:
    """Резюме из открытых данных trudvsem. Поля у площадки менялись, поэтому разбор терпимый."""
    cv = item.get("cv") or item
    exp = cv.get("workExperienceList") or cv.get("experience") or []
    employers = [e.get("companyName") or e.get("company") for e in exp if isinstance(e, dict)]
    employers = [e for e in employers if e]
    jobs = [(e.get("dateFrom") or e.get("start"), e.get("dateTo") or e.get("end")) for e in exp if isinstance(e, dict)]
    years = cv.get("experience") if isinstance(cv.get("experience"), (int, float, str)) else None
    if years is None:
        months = sum(m for m in (_months_between(s, e) for s, e in jobs) if m)
        years = round(months / 12, 1) if months else None
    skills = _split(cv.get("skills") or cv.get("hardSkills") or [])
    if isinstance(cv.get("hardSkills"), list) and cv["hardSkills"] and isinstance(cv["hardSkills"][0], dict):
        skills = [s.get("hardSkillName") for s in cv["hardSkills"] if s.get("hardSkillName")]
    region = cv.get("region") if isinstance(cv.get("region"), dict) else {}
    return Norm(
        external_id=str(cv.get("id") or cv.get("cvId")),
        url=cv.get("url") or (f"https://trudvsem.ru/cv/card/{cv.get('id')}" if cv.get("id") else None),
        title=cv.get("positionName") or cv.get("title"),
        city=cv.get("localityName") or region.get("name"),
        employer_current=employers[0] if employers and jobs and not jobs[0][1] else None,
        employers=employers,
        positions=[e.get("jobTitle") or e.get("position") for e in exp if isinstance(e, dict) and (e.get("jobTitle") or e.get("position"))],
        skills=skills,
        exp_years=float(years) if years not in (None, "") else None,
        avg_tenure_months=_tenure(jobs),
        salary_expect=_int(cv.get("salary")),
        relocation_ready=_bool(cv.get("relocation")),
        open_for_search=True,
        updated_at_src=_dt(cv.get("dateModify") or cv.get("dateCreate")),
        education=cv.get("educationType"),
        about=cv.get("skills") if isinstance(cv.get("skills"), str) else None,
    )


# ------------------------------------------------------------------ выгрузка из кабинета, ручной ввод

FILE_ALIASES = {
    "external_id": ["external_id", "id", "resume_id", "идентификатор"],
    "url": ["url", "link", "ссылка"],
    "title": ["title", "position", "должность", "желаемая должность"],
    "city": ["city", "город"],
    "employer_current": ["employer_current", "employer", "работодатель", "текущий работодатель"],
    "employers": ["employers", "места работы", "компании"],
    "skills": ["skills", "навыки"],
    "exp_years": ["exp_years", "experience", "опыт", "опыт лет"],
    "avg_tenure_months": ["avg_tenure_months", "средний стаж мес"],
    "salary_expect": ["salary_expect", "salary", "зарплата", "ожидания"],
    "relocation_ready": ["relocation_ready", "relocation", "переезд"],
    "open_for_search": ["open_for_search", "ищет работу"],
    "updated_at_src": ["updated_at", "updated_at_src", "обновлено"],
    "education": ["education", "образование"],
    "about": ["about", "о себе"],
    "name": ["name", "fio", "фио"],
    "nicknames": ["nick", "nickname", "ник", "github"],
}


def from_row(row: dict) -> Norm:
    low = {str(k).strip().lower(): v for k, v in row.items() if k is not None}

    def pick(key: str) -> Any:
        for alias in FILE_ALIASES[key]:
            if alias in low and low[alias] not in (None, ""):
                return low[alias]
        return None

    employers = _split(pick("employers"))
    current = pick("employer_current")
    if current and current not in employers:
        employers.insert(0, str(current))
    ext = pick("external_id") or pick("url")
    if not ext:
        raise ValueError("в строке нет идентификатора резюме и ссылки")
    exp = pick("exp_years")
    return Norm(
        external_id=str(ext),
        url=pick("url"),
        title=pick("title"),
        city=pick("city"),
        employer_current=str(current) if current else None,
        employers=employers,
        skills=_split(pick("skills")),
        exp_years=float(str(exp).replace(",", ".")) if exp not in (None, "") else None,
        avg_tenure_months=_int(pick("avg_tenure_months")),
        salary_expect=_int(pick("salary_expect")),
        relocation_ready=_bool(pick("relocation_ready")),
        open_for_search=_bool(pick("open_for_search")),
        updated_at_src=_dt(pick("updated_at_src")),
        education=pick("education"),
        about=pick("about"),
        name=pick("name"),
        nicknames=_split(pick("nicknames")),
    )


PARSERS = {"hh": from_hh, "trudvsem": from_trudvsem}


def normalize(source_code: str, item: dict) -> Norm:
    return PARSERS.get(source_code, from_row)(item)


def label_of(n: Norm) -> str:
    title = (n.title or "Без должности").strip()
    return f"{title[:1].upper()}{title[1:]}, {n.city}" if n.city else title


def as_date(v: Any) -> date | None:
    d = _dt(v)
    return d.date() if d else None
