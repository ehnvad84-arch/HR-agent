"""Разбор ответа площадки в единый набор полей наблюдения (Norm).

Площадки отдают резюме по-разному; здесь всё приводится к одному виду, по нему
считаются хеш версии, дифф, признаки и балл. Сам сырой ответ не меняется и
хранится как пришёл (S9, решение 2026-10-25: вычистку личных данных не проводим).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

# Ограничения длины: ответы площадок — недоверенные данные (раздел «Безопасность» спеки)
LIMITS = {"title": 200, "skill": 100, "skills": 100, "text": 20000, "employer": 200, "employers": 50}

# hh «job_search_status», при которых человек ищет работу
SEARCH_OPEN = {"active_search", "looking_for_offers"}


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
    # в поля observation не пишутся: нужны сшивке и поиску следов
    name: str | None = None
    nicknames: list[str] = field(default_factory=list)

    def significant(self) -> dict:
        """То, что сравнивается между версиями."""
        d = asdict(self)
        for k in ("name", "nicknames", "url"):
            d.pop(k)
        d["skills"] = sorted({s.lower() for s in self.skills})
        d["updated_at_src"] = self.updated_at_src.isoformat() if self.updated_at_src else None
        return d

    def content_hash(self) -> str:
        blob = json.dumps(self.significant(), ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()


# ------------------------------------------------------------------ мелкие разборы

def _cut(s: Any, n: int) -> str | None:
    if s is None or s == "":
        return None
    return str(s).strip()[:n] or None


def _dt(v: Any) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)       # +0300 → +03:00
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%d.%m.%Y")
    except ValueError:
        return None


def _months_between(start: Any, end: Any) -> int | None:
    a = _dt(start)
    b = _dt(end) if end else datetime.now()
    if not a or not b:
        return None
    return max(0, (b.year - a.year) * 12 + b.month - a.month)


def _int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).replace(" ", "").replace(" ", "").replace(",", ".")))
    except ValueError:
        return None


def _float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ".").strip())
    except ValueError:
        return None


def _split(v: Any) -> list[str]:
    if v is None or v == "":
        return []
    items = v if isinstance(v, list) else re.split(r"[;,\n]", str(v))
    return [str(x).strip() for x in items if str(x).strip()]


def _bool(v: Any) -> bool | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "да", "yes", "y", "+")


def _tenure(jobs: list[tuple[Any, Any]]) -> int | None:
    months = [m for m in (_months_between(s, e) for s, e in jobs) if m is not None]
    return round(sum(months) / len(months)) if months else None


def _limit(n: Norm) -> Norm:
    n.title = _cut(n.title, LIMITS["title"])
    n.employer_current = _cut(n.employer_current, LIMITS["employer"])
    n.employers = [e[:LIMITS["employer"]] for e in n.employers if e][:LIMITS["employers"]]
    n.positions = [p[:LIMITS["title"]] for p in n.positions if p][:LIMITS["employers"]]
    n.skills = [s[:LIMITS["skill"]] for s in n.skills if s][:LIMITS["skills"]]
    n.about = _cut(n.about, LIMITS["text"])
    return n


# ------------------------------------------------------------------ hh.ru

def from_hh(item: dict) -> Norm:
    """Элемент поиска /resumes или полное резюме /resumes/{id}."""
    exp = item.get("experience") or []
    employers = [e.get("company") or (e.get("employer") or {}).get("name") for e in exp]
    employers = [e for e in employers if e]
    current = employers[0] if exp and employers and not exp[0].get("end") else None
    salary = item.get("salary") or {}
    total = (item.get("total_experience") or {}).get("months")
    reloc = ((item.get("relocation") or {}).get("type") or {}).get("id")
    status = (item.get("job_search_status") or {}).get("id")
    edu = item.get("education") or {}
    name = " ".join(filter(None, [item.get("last_name"), item.get("first_name")])) or None
    return _limit(Norm(
        external_id=str(item["id"]),
        url=item.get("alternate_url") or item.get("url"),
        title=item.get("title"),
        city=(item.get("area") or {}).get("name"),
        employer_current=current,
        employers=employers,
        positions=[e.get("position") for e in exp if e.get("position")],
        skills=list(item.get("skill_set") or []),
        exp_years=round(total / 12, 1) if total is not None else None,
        avg_tenure_months=_tenure([(e.get("start"), e.get("end")) for e in exp]),
        salary_expect=_int(salary.get("amount") if isinstance(salary, dict) else salary),
        relocation_ready=None if reloc is None else reloc != "no_relocation",
        open_for_search=None if status is None else status in SEARCH_OPEN,
        updated_at_src=_dt(item.get("updated_at") or item.get("updated")),
        education=((edu.get("level") or {}).get("name")) if isinstance(edu, dict) else None,
        about=item.get("skills"),
        name=name,
    ))


# ------------------------------------------------------------------ Работа России

def from_trudvsem(item: dict) -> Norm:
    """Резюме из открытых данных trudvsem. Формат не подтверждён (T15), поэтому разбор терпимый."""
    cv = item.get("cv") or item
    exp = [e for e in (cv.get("workExperienceList") or []) if isinstance(e, dict)]
    employers = [e.get("companyName") for e in exp if e.get("companyName")]
    jobs = [(e.get("dateFrom"), e.get("dateTo")) for e in exp]
    months = sum(m for m in (_months_between(s, e) for s, e in jobs) if m)
    region = cv.get("region") if isinstance(cv.get("region"), dict) else {}
    skills = cv.get("hardSkills") or cv.get("skills") or []
    if isinstance(skills, list) and skills and isinstance(skills[0], dict):
        skills = [s.get("hardSkillName") for s in skills if s.get("hardSkillName")]
    return _limit(Norm(
        external_id=str(cv.get("id") or cv.get("cvId")),
        url=cv.get("url"),
        title=cv.get("positionName") or cv.get("title"),
        city=cv.get("localityName") or region.get("name"),
        employer_current=employers[0] if employers and jobs and not jobs[0][1] else None,
        employers=employers,
        positions=[e.get("jobTitle") for e in exp if e.get("jobTitle")],
        skills=_split(skills),
        exp_years=_float(cv.get("experience")) or (round(months / 12, 1) if months else None),
        avg_tenure_months=_tenure(jobs),
        salary_expect=_int(cv.get("salary")),
        relocation_ready=_bool(cv.get("relocation")),
        updated_at_src=_dt(cv.get("dateModify") or cv.get("dateCreate")),
        education=cv.get("educationType"),
    ))


# Открытые данные «Работы России» (csv/cv.csv, разделитель «|»): в простых текстовых полях пробелы
# заменены дефисами, списки — JSON. Дата рождения, пол и возраст для подбора не нужны и не хранятся.
TRUDVSEM_PLAIN = ("positionName", "localityName", "relocation", "scheduleType", "busyType", "typicalPosition")
TRUDVSEM_JSON = ("workExperienceList", "educationList", "hardSkills", "skills", "softSkills")
TRUDVSEM_DROP = ("birthday", "gender", "age")


def trudvsem_csv_row(cells: dict) -> dict:
    """Строка выгрузки → словарь для from_trudvsem и для хранения как raw."""
    out = {k: v for k, v in cells.items() if k not in TRUDVSEM_DROP and v not in (None, "")}
    for k in TRUDVSEM_PLAIN:
        if isinstance(out.get(k), str):
            out[k] = out[k].replace("-", " ").strip()
    for k in TRUDVSEM_JSON:
        if isinstance(out.get(k), str):
            try:
                out[k] = json.loads(out[k])
            except ValueError:
                out.pop(k)
    reloc = (out.get("relocation") or "").lower()
    out["relocation"] = None if not reloc else not reloc.startswith("не ")
    salary = _int(out.get("salary")) or _int(out.get("salaryMin"))
    out["salary"] = salary or None
    if out.get("candidateId") and out.get("id"):
        out["url"] = f"https://trudvsem.ru/cv/card/{out['candidateId']}/{out['id']}"
    return out


# ------------------------------------------------------------------ выгрузка из кабинета, ручной ввод

FILE_ALIASES = {
    "external_id": ["external_id", "id", "resume_id", "идентификатор", "id резюме"],
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
            v = low.get(alias)
            if v not in (None, ""):
                return v
        return None

    ext = pick("external_id") or pick("url")
    if not ext:
        raise ValueError("в строке нет идентификатора резюме и ссылки")
    employers = _split(pick("employers"))
    current = pick("employer_current")
    if current and str(current) not in employers:
        employers.insert(0, str(current))
    return _limit(Norm(
        external_id=str(ext),
        url=pick("url"),
        title=pick("title"),
        city=pick("city"),
        employer_current=str(current) if current else None,
        employers=employers,
        skills=_split(pick("skills")),
        exp_years=_float(pick("exp_years")),
        avg_tenure_months=_int(pick("avg_tenure_months")),
        salary_expect=_int(pick("salary_expect")),
        relocation_ready=_bool(pick("relocation_ready")),
        open_for_search=_bool(pick("open_for_search")),
        updated_at_src=_dt(pick("updated_at_src")),
        education=pick("education"),
        about=pick("about"),
        name=pick("name"),
        nicknames=_split(pick("nicknames")),
    ))


PARSERS = {"hh": from_hh, "trudvsem": from_trudvsem}


def normalize(source_code: str, item: dict) -> Norm:
    return PARSERS.get(source_code, from_row)(item)


def label_of(n: Norm) -> str:
    """Ярлык карточки: «должность, город» — без имени (AD-3)."""
    title = (n.title or "Без должности").strip()
    title = title[:1].upper() + title[1:]
    return f"{title}, {n.city}" if n.city else title
