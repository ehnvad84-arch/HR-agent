"""T5: варианты имени кириллицей и латиницей (S28)."""
from hr.names import name_variants, translit


def test_s28_ambiguous_translit_gives_4_to_12_variants():
    v = name_variants("Дьячкова Юлия")
    assert 4 <= len(v) <= 12
    low = [x.lower() for x in v]
    assert "дьячкова юлия" in low and "юлия дьячкова" in low
    assert any("iuliia" in x for x in low) and any("yulia" in x or "yuliya" in x for x in low)
    assert any(x.startswith("iuliia ") or x.startswith("yulia ") for x in low), "обратный порядок латиницей"
    assert any(x.endswith(" iuliia") for x in low), "прямой порядок латиницей"


def test_s28_ii_to_y_and_separators():
    low = [x.lower() for x in name_variants("Петров Дмитрий")]
    assert any("dmitrii" in x for x in low) and any("dmitry" in x for x in low)
    assert any("." in x for x in low) and any("_" in x for x in low)


def test_latin_input_and_single_word():
    assert len(name_variants("Ivanov Petr")) >= 4
    assert name_variants("Иван") == ["Иван"]
    assert name_variants(None) == [] and name_variants("") == []


def test_translit_passport_rules():
    assert translit("Щукин") == "shchukin"
    assert translit("Юлия") == "iuliia"


def test_variants_are_unique():
    v = name_variants("Ким Ия")
    assert len({x.lower() for x in v}) == len(v)
