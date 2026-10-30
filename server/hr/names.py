"""Варианты написания имени для поиска следов в сети (раздел 7.2 ТЗ, S28).

Половина профилей заведена латиницей, а пишут по-разному: Юлия — Iuliia, Yulia,
Yuliya; Дмитрий — Dmitrii, Dmitry. Варианты хранятся в person.name_variants,
чтобы поиск был воспроизводим.
"""
from __future__ import annotations

import re

# Транслитерация как в загранпаспорте (приказ МВД № 889) — основной вариант
TRANSLIT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
            "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
            "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
            "щ": "shch", "ъ": "ie", "ы": "y", "ь": "", "э": "e", "ю": "iu", "я": "ia"}

# Частые написания, которые люди и площадки выбирают сами
ALT = [("iu", "yu"), ("ia", "ya"), ("iia", "ia"), ("ii", "y"), ("kh", "h"), ("ks", "x")]

MAX_VARIANTS = 12


def translit(s: str) -> str:
    return "".join(TRANSLIT.get(ch, ch) for ch in s.lower())


def _alts(word: str) -> list[str]:
    """Слово латиницей: паспортный вариант первым, затем частые замены."""
    out = [translit(word)]
    for a, b in ALT:
        for v in list(out):
            if a in v:
                w = v.replace(a, b)
                if w not in out:
                    out.append(w)
    return out


def is_cyrillic(s: str) -> bool:
    return bool(re.search(r"[а-яё]", s, re.I))


def name_variants(fio: str | None) -> list[str]:
    """«Фамилия Имя [Отчество]» → варианты кириллицей и латиницей, прямой и обратный порядок."""
    parts = (fio or "").split()
    if not parts:
        return []
    if len(parts) < 2:
        return [parts[0]]
    last, first = parts[0], parts[1]
    out: list[str] = []

    def add(v: str) -> None:
        if v.lower() not in {x.lower() for x in out} and len(out) < MAX_VARIANTS:
            out.append(v)

    add(f"{last} {first}")
    add(f"{first} {last}")
    if not is_cyrillic(last + first):
        add(f"{first}.{last}".lower())
        add(f"{first}_{last}".lower())
        return out
    lat_last, lat_first = _alts(last), _alts(first)
    add(f"{lat_first[0]} {lat_last[0]}")
    add(f"{lat_last[0]} {lat_first[0]}")
    add(f"{lat_first[0]}.{lat_last[0]}")
    add(f"{lat_first[0]}_{lat_last[0]}")
    for f in lat_first[1:]:
        add(f"{f} {lat_last[0]}")
    for la in lat_last[1:]:
        for f in lat_first:
            add(f"{f} {la}")
    return out
