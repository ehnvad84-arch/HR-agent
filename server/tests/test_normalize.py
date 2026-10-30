"""T3: разбор ответов площадок (S9 — сырой ответ не вычищается)."""
import copy
import json
from pathlib import Path

import pytest

from hr.normalize import LIMITS, label_of, normalize

FIX = Path(__file__).parent / "fixtures"


def hh_item() -> dict:
    return json.loads((FIX / "hh" / "search_item.json").read_text(encoding="utf-8"))


def test_s09_normalize_does_not_touch_raw():
    item = hh_item()
    before = copy.deepcopy(item)
    normalize("hh", item)
    assert item == before, "сырой ответ хранится как пришёл"


def test_s09_hh_fields_and_name():
    n = normalize("hh", hh_item())
    assert n.external_id == "a1b2c3d4e5"
    assert n.city == "Краснодар"
    assert n.title == "Ведущий инженер-электронщик"
    assert n.employer_current == "Приборный завод «Пример»"
    assert n.employers[1] == "НПП Тест"
    assert n.exp_years == pytest.approx(9.3, abs=0.05)
    assert n.salary_expect == 190000
    assert n.open_for_search is True and n.relocation_ready is False
    assert n.education == "Высшее"
    assert n.name == "Тестовый Андрей"
    assert n.avg_tenure_months and n.avg_tenure_months > 20
    assert n.updated_at_src.year == 2026


def test_s09_file_row_russian_columns():
    row = {"ФИО": "Петрова Юлия", "Должность": "Инженер ОТК", "Город": "Краснодар",
           "Навыки": "AQL; входной контроль, ISO 9001", "Ссылка": "https://career.habr.com/x1",
           "Опыт лет": "4,5", "Ник": "jpetrova", "Телефон": "+7 900 111-11-11"}
    n = normalize("habr", row)
    assert n.name == "Петрова Юлия"
    assert n.title == "Инженер ОТК" and n.city == "Краснодар"
    assert n.skills == ["AQL", "входной контроль", "ISO 9001"]
    assert n.exp_years == 4.5
    assert n.external_id == "https://career.habr.com/x1"
    assert n.nicknames == ["jpetrova"]


def test_file_row_without_id_and_url_is_rejected():
    with pytest.raises(ValueError):
        normalize("manual", {"Должность": "Кладовщик"})


def test_field_length_limits():
    item = hh_item()
    item["title"] = "x" * 1000
    item["skill_set"] = ["s" * 500] * 300
    n = normalize("hh", item)
    assert len(n.title) == LIMITS["title"]
    assert len(n.skills) == LIMITS["skills"] and all(len(s) == LIMITS["skill"] for s in n.skills)


def test_trudvsem_tolerant_parse():
    n = normalize("trudvsem", {"cv": {"id": "cv-1", "positionName": "Мастер участка", "localityName": "Краснодар",
                                      "salary": "120000", "workExperienceList": [
                                          {"companyName": "Завод", "dateFrom": "2015-01-01", "dateTo": None}]}})
    assert (n.external_id, n.title, n.city, n.salary_expect, n.employer_current) == \
        ("cv-1", "Мастер участка", "Краснодар", 120000, "Завод")


def test_label():
    assert label_of(normalize("hh", hh_item())) == "Ведущий инженер-электронщик, Краснодар"


def test_content_hash_ignores_order_of_skills():
    a, b = hh_item(), hh_item()
    a["skill_set"], b["skill_set"] = ["Altium", "ЭМС"], ["ЭМС", "Altium"]
    assert normalize("hh", a).content_hash() == normalize("hh", b).content_hash()
