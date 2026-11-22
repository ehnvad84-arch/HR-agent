"""Сквозной прогон scripts/demo_core.py (ядро run_demo) на базе тестов: синонимы, заглушка bge-m3, предел S11."""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

from hr import config, embed

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "demo_core.py"


def _demo():
    spec = importlib.util.spec_from_file_location("demo_core", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_s5_s7_s11_demo_engineer_with_other_terms_in_base(conn_factory, clock, monkeypatch, tmp_path):
    demo = _demo()
    shutil.copy(demo.CSV, tmp_path / demo.CSV.name)
    monkeypatch.setenv("IMPORT_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_BASE_URL", demo.EMBED_URL)
    monkeypatch.setenv("EMBED_MODEL", demo.EMBED_MODEL)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setattr(embed, "client", demo.bge_stub)
    config.get_config.cache_clear()
    lines: list[str] = []
    try:
        out = demo.run_demo(conn_factory, say=lines.append, now=clock.now())
    finally:
        config.get_config.cache_clear()
    text = "\n".join(lines)

    other = out["other_words"]
    assert other, text                                         # вошёл в базу
    assert other["score"] >= other["threshold"] > other["without"]   # без синонимов и смысла — ниже порога
    assert "Altium ≈ P-CAD" in other["keywords"]               # S5
    assert other["sim"] and "близко к профилю по смыслу" in other["semantic"]   # S7: признак посчитан
    assert "Altium ≈ P-CAD" in text and "Разработчик электроники" in text
    assert out["hh_full"] == {"cap": 300, "used": 300, "refused": True}       # S11
    assert "Предел полных резюме hh на сегодня исчерпан: 300 из 300" in text
