"""T11 funnel-report: сквозной прогон scripts/demo_funnel.py (ядро run_demo) на базе тестов — три вакансии двух
рекрутеров через API рабочего места, путь кандидатов, отмена, пауза, закрытие и перенос; цифры — эталон скрипта."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from test_funnel_report import REFERENCE, REFERENCE_MEETINGS

from hr import config, db

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "demo_funnel.py"


def _demo():
    spec = importlib.util.spec_from_file_location("demo_funnel", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_s1_s16_demo_funnel_matches_reference(conn, conn_factory, monkeypatch):
    demo = _demo()
    monkeypatch.setenv("SESSION_KEY", "k" * 40)
    config.get_config.cache_clear()
    lines: list[str] = []
    try:
        out = demo.run_demo(conn_factory, say=lines.append)
    finally:
        config.get_config.cache_clear()
    text = "\n".join(lines)

    assert out["mismatch"] == [], text
    assert out["checks"] and all(out["checks"].values()), {k: v for k, v in out["checks"].items() if not v}
    expected = (
        "S1 этап добавлен", "S1 рекрутер не добавляет этап", "S2 стандартный набор", "S3 ждёт утверждения",
        "S3 второй набор — pending_exists", "S3 отзыв", "S4 отклонён с комментарием", "S4 утверждён",
        "S5 этап с людьми — stage_busy", "S6 через этап — stale", "S7 «другое» короче 100 — 422",
        "S7 отказ от оффера не на «Оффере» — 422", "S9 вышел — можно закрыть", "S9 закрыта: прошёл — 409",
        "S9 закрыта: не прошёл — можно", "S10 отобранные агентом не в отчёте", "S12 чужой отчёт рекрутера — 404",
        "S12 чужая вакансия — 404", "S13 отдел рекрутеру — 404", "S14 на паузе новых людей нет",
        "S14 на паузе отметки можно", "S15 перенос", "S15 повтор ничего не меняет", "S16 своя сегодня",
        "S16 вчерашняя — undo_expired", "S16 руководитель ОП отменяет", "вход по паролю")
    for name in expected:
        assert name in out["checks"], name

    # эталон вакансии с четырьмя собеседованиями без СБ — тот же, что у счёта (T8)
    assert demo.REFERENCE["v1"]["rows"] == REFERENCE
    assert demo.REFERENCE["v1"]["meetings"] == REFERENCE_MEETINGS
    assert out["reports"]["v1"]["rows"] == REFERENCE

    # S16: отменённые отметки остаются в базе помеченными, в отчёт не входят
    assert db.val(conn, "SELECT count(*) FROM ops.candidate_stage WHERE cancelled_at IS NOT NULL") >= 3
    assert db.val(conn, """SELECT count(*) FROM audit_log WHERE object_type = 'candidate_stage'
                           AND detail ? 'undo'""") == 2
    # S14: пауза в журнале, S9: вакансия закрыта
    assert db.val(conn, "SELECT count(*) FROM ops.vacancy_pause WHERE kind = 'pause'") == 1
    assert db.val(conn, "SELECT count(*) FROM vacancy WHERE state = 'closed'") == 1
    # в выводе — форма отчёта ОП
    for s in ("Воронка вакансии", "Отобранные резюме", "Пришедшие в 1 день", "нет в наборе", "Конверсия воронки",
              "Воронка отдела", "Эталон: совпадает"):
        assert s in text, s
    assert out["logins"] and out["password"]
