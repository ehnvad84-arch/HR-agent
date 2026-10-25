"""T4: балл по профилю (S16, S18), признаки «сколько из скольких», угроза T2."""
from hr.scoring import Params, Person, Profile, default_weights, parse_requirement, score, template_explanation

P = Params(stop_cap=40, must_penalty_pct=10, must_penalty_max_pct=30, local_cities={"краснодар"})


def engineer(**kw) -> Person:
    base = dict(title="Ведущий инженер-электронщик", city="Краснодар",
                employers=["Приборный завод «Пример»", "Болид"], positions=["Инженер-электронщик"],
                skills=["Altium Designer", "схемотехника", "ЭМС", "трассировка"], exp_years=9.0,
                avg_tenure_months=40, salary_expect=180000, relocation_ready=False)
    base.update(kw)
    return Person(**base)


def profile(**kw) -> Profile:
    base = dict(title="Ведущий инженер-электронщик", geo="krasnodar", salary_min=150000, salary_max=200000,
                keywords=["Altium", "схемотехника", "трассировка", "ЭМС", "OrCAD"], donor_companies=["Болид"],
                weights={"exp_years": 20, "keywords": 25, "title_match": 15, "donor_company": 10, "tenure": 10,
                         "geo": 10, "salary": 10})
    base.update(kw)
    return Profile(**base)


def test_s16_score_and_features_breakdown():
    s = score(engineer(), profile(), P)
    assert 0 <= s.score <= 100 and s.score >= 85
    for f in s.features.values():
        assert set(f) == {"label", "points", "max", "detail"}
        assert 0 <= f["points"] <= f["max"]
    assert sum(f["max"] for f in s.features.values()) == 100
    assert s.features["donor_company"]["points"] == s.features["donor_company"]["max"]


def test_s16_weights_only_from_profile():
    s = score(engineer(), profile(weights={"keywords": 50, "geo": 50}), P)
    assert set(s.features) == {"keywords", "geo"}


def test_s16_defaults_when_profile_has_no_weights_are_visible():
    s = score(engineer(), profile(weights={}), P)
    assert s.default_weights is True
    assert set(s.features) == {k for k, v in default_weights(None).items() if v}


def test_open_code_default_weight_for_engineering_only():
    assert default_weights("rnd_hw")["open_code"] == 10
    assert default_weights("rnd_sw")["open_code"] == 10
    assert default_weights("sales")["open_code"] == 0


def test_s29_open_code_counts_only_confirmed():
    pr = profile(weights={"open_code": 50, "geo": 50})
    assert score(engineer(open_code=False), pr, P).features["open_code"]["points"] == 0
    assert score(engineer(open_code=True), pr, P).features["open_code"]["points"] == 50


def test_s18_stop_factor_caps_score_at_40():
    pr = profile(stop_factors=[parse_requirement("Средняя длительность места работы меньше года")])
    s = score(engineer(avg_tenure_months=8), pr, P)
    assert s.score <= 40 and s.stop_hit
    assert "длительность" in s.features["_stop"]["detail"].lower()


def test_s18_text_stop_factor_ignores_negation():
    pr = profile(stop_factors=[parse_requirement("судимость")])
    assert not score(engineer(text="нет судимости"), pr, P).stop_hit
    assert not score(engineer(text="без судимость"), pr, P).stop_hit
    assert score(engineer(text="имеется судимость"), pr, P).stop_hit


def test_s18_must_penalty_10pct_each_capped_at_30():
    base = score(engineer(), profile(), P).score
    one_missing = score(engineer(), profile(must_have=[parse_requirement("Вывод изделия в серию")]), P)
    assert one_missing.must_missing == ["Вывод изделия в серию"]
    assert one_missing.score == round(base * 0.9) or abs(one_missing.score - base * 0.9) <= 1
    broken = score(engineer(exp_years=1.0), profile(must_have=[parse_requirement("Опыт от 2 лет")]), P)
    assert broken.must_broken and broken.features["_penalty"]["points"] == -10
    many = profile(must_have=[parse_requirement(t) for t in ("ПЛИС", "Verilog", "радиолокация", "СВЧ", "VHDL")])
    assert score(engineer(), many, P).features["_penalty"]["points"] == -30


def test_t2_keyword_stuffing_counts_once():
    pr = profile(keywords=["Altium", "OrCAD", "KiCad", "Eagle", "P-CAD", "Proteus"])
    honest = score(engineer(skills=["Altium"]), pr, P)
    stuffed = score(engineer(skills=["Altium"] * 100, text="Altium " * 100), pr, P)
    assert stuffed.features["keywords"]["points"] == honest.features["keywords"]["points"]


def test_geo_and_salary():
    far = score(engineer(city="Самара", relocation_ready=False), profile(), P)
    assert far.features["geo"]["points"] == 0
    remote = score(engineer(city="Самара"), profile(geo="remote_ru"), P)
    assert remote.features["geo"]["points"] == remote.features["geo"]["max"]
    pricey = score(engineer(salary_expect=260000), profile(), P)
    assert pricey.features["salary"]["points"] < pricey.features["salary"]["max"]


def test_s19_template_explanation_in_russian():
    pr = profile(must_have=[parse_requirement("Вывод изделия в серию")])
    t = template_explanation(score(engineer(), pr, P))
    assert t["why"].startswith("Сильнее всего")
    assert "Вывод изделия в серию" in t["gap"]
    assert t["questions"]


def test_parse_requirement_structured():
    assert parse_requirement("Профильный опыт от 3 лет")["feature"] == "exp_years"
    assert parse_requirement("Средняя длительность места работы меньше года") == {
        "feature": "avg_tenure_months", "op": "<", "value": 12,
        "text": "Средняя длительность места работы меньше года"}
    assert parse_requirement("Altium Designer") == {"text": "Altium Designer"}
