"""Варианты написания имени для поиска следов в сети (раздел 7.2 ТЗ).

Половина профилей заведена латиницей, а площадки транслитерируют по-разному:
Юлия — Iuliia, Yulia, Julia; Дмитрий — Dmitrii, Dmitry. Варианты сохраняются в
person.name_variants, чтобы поиск был воспроизводим.
"""
from __future__ import annotations

import itertools
import re

# Транслитерация по приказу МВД № 889 (как в загранпаспорте) — основной вариант
TRANSLIT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
            "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
            "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
            "щ": "shch", "ъ": "ie", "ы": "y", "ь": "", "э": "e", "ю": "iu", "я": "ia"}

# Частые альтернативы, которые люди и площадки пишут сами
ALT = [("iu", "yu"), ("ia", "ya"), ("ii", "y"), ("kh", "h"), ("ie", ""), ("iy", "y"), ("ks", "x"),
       ("zh", "j"), ("iu", "ju")]


def translit(s: str) -> str:
    out = []
    for ch in s.lower():
        out.append(TRANSLIT.get(ch, ch))
    t = "".join(out)
    # «ий» на конце имени обычно пишут как y или ii
    return t


def _alts(word: str) -> set[str]:
    """Слово латиницей плюс его частые варианты (не больше двух замен разом)."""
    base = translit(word)
    seen = {base}
    if base.endswith("ii"):
        seen.add(base[:-2] + "y")
        seen.add(base[:-2] + "iy")
    if base.endswith("iy"):
        seen.add(base[:-2] + "y")
    for a, b in ALT:
        for v in list(seen):
            if a in v:
                seen.add(v.replace(a, b))
    return {v for v in seen if v}


def is_cyrillic(s: str) -> bool:
    return bool(re.search(r"[а-яё]", s, re.I))


def name_variants(fio: str | None, limit: int = 24) -> list[str]:
    """Фамилия Имя [Отчество] → варианты кириллицей и латиницей, прямой и обратный порядок."""
    if not fio:
        return []
    parts = [p for p in re.split(r"\s+", fio.strip()) if p]
    if len(parts) < 2:
        return [fio.strip()]
    last, first = parts[0], parts[1]
    out: list[str] = []

    def add(v: str) -> None:
        v = v.strip()
        if v and v.lower() not in {x.lower() for x in out}:
            out.append(v)

    if is_cyrillic(last + first):
        add(f"{last} {first}")
        add(f"{first} {last}")
        lat_last = sorted(_alts(last), key=len)
        lat_first = sorted(_alts(first), key=len)
        main_l, main_f = translit(last), translit(first)
        add(f"{main_l} {main_f}")
        add(f"{main_f} {main_l}")
        add(f"{main_f}.{main_l}")
        add(f"{main_f}_{main_l}")
        for fl, ff in itertools.product(lat_last, lat_first):
            add(f"{ff} {fl}")
            if len(out) >= limit:
                break
    else:
        add(f"{last} {first}")
        add(f"{first} {last}")
        add(f"{first}.{last}".lower())
        add(f"{first}_{last}".lower())
    return out[:limit]
