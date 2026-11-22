"""T7: ключ засчитан по синониму (S5); синонимы — и в событиях «новый навык»."""
from datetime import UTC, datetime

from factories import profile as db_profile
from factories import user
from test_scoring import P, engineer, profile

from hr import pipeline, scoring, synonyms
from hr.pipeline import events_from_diff

NOW = datetime(2026, 11, 22, 11, 5, tzinfo=UTC)
SYN = {"altium": ["P-CAD", "KiCad"], "трассировка": ["ПП"]}


def kw(s: scoring.Score) -> dict:
    return s.features["keywords"]


def test_s05_keyword_counted_by_synonym_with_label():
    p = engineer(skills=["P-CAD", "схемотехника", "ЭМС"])
    without = score_kw(p, {})
    with_syn = score_kw(p, SYN)
    assert with_syn["points"] > without["points"]
    assert "Altium ≈ P-CAD" in with_syn["detail"]
    assert "Altium" not in without["detail"]


def score_kw(person, syn):
    return kw(scoring.score(person, profile(synonyms=syn), P))


def test_s05_denominator_does_not_grow_with_synonyms():
    p = engineer(skills=["Altium", "P-CAD", "KiCad", "схемотехника"])
    # ключ засчитан один раз, сколько бы его синонимов ни нашлось
    ratio_syn, _ = scoring._f_keywords(profile(synonyms=SYN), p.haystack())
    ratio_plain, _ = scoring._f_keywords(profile(), p.haystack())
    assert ratio_syn == ratio_plain


def test_s05_short_synonym_only_as_whole_word():
    prof = profile(keywords=["трассировка"], synonyms={"трассировка": ["ПП"]})
    inside = engineer(skills=["Эксплуатация ППУ"], title="Механик", positions=[])
    assert kw(scoring.score(inside, prof, P))["detail"] == "совпадений нет"
    alone = engineer(skills=["разводка ПП"], title="Механик", positions=[])
    assert "трассировка ≈ ПП" in kw(scoring.score(alone, prof, P))["detail"]


def test_s05_keyword_key_is_normalized():
    prof = profile(keywords=["Ёмкость"], synonyms={"емкость": ["capacitance"]})
    p = engineer(skills=["capacitance"], title="Механик", positions=[])
    assert "Ёмкость ≈ capacitance" in kw(scoring.score(p, prof, P))["detail"]


def test_s05_must_have_and_stop_factors_ignore_synonyms():
    prof = profile(synonyms={"altium": ["P-CAD"], "судимость": ["P-CAD"]},
                   must_have=[{"text": "Altium"}], stop_factors=[{"text": "судимость"}])
    p = engineer(skills=["P-CAD"])
    s = scoring.score(p, prof, P)
    assert scoring.requirement_met({"text": "Altium"}, p, p.haystack()) is None
    assert not s.stop_hit and s.must_missing == ["Altium"]


def test_s05_minus_keyword_drops_its_synonyms():
    prof = profile(synonyms=SYN)
    prof.apply_criteria({"keywords_minus": ["altium"]})
    p = engineer(skills=["P-CAD"])
    assert "P-CAD" not in kw(scoring.score(p, prof, P))["detail"]


def test_s05_profile_without_synonyms_scores_as_before():
    p = engineer()
    assert scoring.score(p, profile(), P) == scoring.score(p, profile(synonyms={}), P)


def test_s05_current_profiles_carry_synonyms(conn):
    pr = db_profile(conn, keywords=["Altium", "схемотехника"])
    rec = user(conn, "rec_t7", "recruiter")
    synonyms.save(conn, pr["id"], [{"keyword": "Altium", "synonyms": ["P-CAD"]}], rec, NOW, base_change_id=None)
    cur = pipeline.current_profiles(conn)[0]
    assert cur["synonyms"] == {"altium": ["P-CAD"]}
    p = engineer(skills=["P-CAD"])
    assert "Altium ≈ P-CAD" in kw(scoring.score(p, scoring.Profile.from_row(cur), P))["detail"]
    assert "P-CAD" in pipeline.keyword_terms([cur])


def test_s05_synonym_appearance_is_skill_added():
    diff = {"skills": {"added": ["P-CAD"], "removed": []}}
    assert [k for k, _ in events_from_diff(diff, {"altium"})] == ["resume_updated"]
    terms = pipeline.keyword_terms([{"keywords": ["Altium"], "synonyms": {"altium": ["P-CAD"]}}])
    assert [k for k, _ in events_from_diff(diff, terms)] == ["skill_added"]
