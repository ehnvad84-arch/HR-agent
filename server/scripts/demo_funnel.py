"""Сквозной прогон воронки по этапам (funnel-report, S1–S16) на вымышленных данных, без сети и ключей.

    DATABASE_URL=postgresql://…/ruware_hr_funnel_demo python scripts/demo_funnel.py [--keep]

Пересоздаёт схемы базы *_demo и проживает 03.11–23.11.2026 через API рабочего места (внутри процесса, часы
подменены): руководитель ОП, два рекрутера, вакансия с четырьмя собеседованиями без «Проверки СБ» (набор
предложен, отозван, отклонён, утверждён), вакансия со стандартным набором до выхода и закрытия, вакансия v1.2.0
с переносом кандидатов. Кандидаты проходят этапы, отказываются и не проходят с причинами; ошибочные отметки
отменяются; вакансия стоит на паузе. Итог печатается в форме отчёта ОП и сверяется с эталоном REFERENCE.

Без --keep данные прогона удаляются (схемы базы пустые); с --keep база остаётся заполненной для стенда экранов —
логины и пароль демо-пользователей печатаются. Люди, вакансии и комментарии вымышлены.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hr import auth, db  # noqa: E402
from hr.clock import Clock  # noqa: E402
from hr.db import ConnFactory  # noqa: E402

MSK = ZoneInfo("Europe/Moscow")
YEAR, MONTH = 2026, 11
PASSWORD = secrets.token_urlsafe(9)   # пароль демо-пользователей — свой на каждый прогон, печатается с --keep
ORIGIN = {"Origin": "http://localhost:8000"}

USERS = [  # логин, имя, роль; руководитель-заказчик без пароля — его кабинет без оргструктуры пуст
    ("demo_head", "Орлова Светлана (демо)", "head_recruiter"),
    ("demo_rec1", "Кравцова Анна (демо)", "recruiter"),
    ("demo_rec2", "Белов Игорь (демо)", "recruiter"),
]
BOSS = ("demo_boss", "Демидов Павел (демо)", "hiring_manager")

PROFILES = {  # ключ: (подразделение, название подразделения, код, должность)
    "eng": ("rnd_hw", "Инженерные разработки", "engineer_hw_lead", "Ведущий инженер-электронщик"),
    "sales": ("sales", "Продажи", "sales_project", "Менеджер проектных продаж"),
}
# Вымышленные люди из сбора: ключ — (профиль, фамилия, имя, город)
PEOPLE = {
    "A": ("eng", "Арбузов", "Глеб"), "B": ("eng", "Брусникина", "Дарья"), "C": ("eng", "Вишняков", "Олег"),
    "D": ("eng", "Грушина", "Ксения"), "E": ("eng", "Дынин", "Семён"), "F": ("eng", "Ежевикин", "Тимур"),
    "G": ("eng", "Жимолостева", "Алла"), "H": ("eng", "Зёрнов", "Матвей"), "I": ("eng", "Ирисова", "Нина"),
    "J": ("eng", "Калинин", "Роман"), "X1": ("eng", "Клюквин", "Фёдор"), "X2": ("eng", "Лаймова", "Вера"),
    "K": ("sales", "Малинин", "Пётр"), "L": ("sales", "Нектаринова", "Ольга"), "M": ("sales", "Облепихин", "Лев"),
    "N": ("sales", "Персикова", "Юлия"), "O": ("sales", "Рябинин", "Борис"), "P": ("sales", "Смородинова", "Ия"),
    "Q": ("sales", "Тыквин", "Артём"), "X3": ("sales", "Финикова", "Зоя"),
}
LEGACY = [("Хурмин Илья", "phone_interview"), ("Цитронова Ева", "offer"), ("Черешнев Марк", "reserve"),
          ("Шиповникова Лада", "hired")]

OTHER_LONG = ("Кандидат после первичного интервью сообщил, что переезжает в другой город по семейным "
              "обстоятельствам и не сможет работать в офисе, удалёнку не рассматриваем.")
CLOSE_COMMENT = ("Вакансия закрыта: кандидат вышел в первый день и приступил к работе в отделе продаж. Число ставок "
                 "по заявке — одна, она занята. Остальных кандидатов по вакансии благодарим и сохраняем в резерве, "
                 "отказы по ним отмечены с причинами в воронке.")
assert len(OTHER_LONG) >= 100 and len(CLOSE_COMMENT) >= 200

# Эталон — посчитан руками по сценарию прогона (строка: вошли, прошли, %).
# v1 — 4 собеседования, без СБ (как в tests/test_funnel_report.py): A вышел; B не вышел в первый день; C отказ от
# оффера; D не прошёл собеседование с директором; E на первом собеседовании; F не прошёл тестовое; G — условия;
# H — не вышел на связь; I — не прошёл по навыкам; J на «Отобранных». v2 — стандартный набор: K вышел; L не прошёл
# СБ после закрытия; M — «другое»; N — не вышел на связь; O — навыки; P — отказ от оффера; Q — тестовое.
# v3 — вакансия v1.2.0: перенос «Телефонное интервью» → «Пройденное первичное интервью», «Оффер» → «Оффер».
REFERENCE = {
    "v1": {"rows": {"selected": (10, 8, 80), "contact": (8, 7, 88), "screening": (7, 6, 86),
                    "interview": (6, 5, 83), "meeting": (5, 3, 60), "security": (3, 3, 100),
                    "offer": (3, 2, 67), "first_day": (2, 1, 50)},
           "meetings": [("Собеседование", 5, 4), ("Собеседование: главный инженер", 4, 4),
                        ("Собеседование: директор", 4, 3), ("Собеседование: ОТК", 3, 3)],
           "conversion": (1, 10, 10), "days": (14, 6), "carried": 0, "not_in_set": ["security"]},
    "v2": {"rows": {"selected": (7, 6, 86), "contact": (6, 5, 83), "screening": (5, 4, 80),
                    "interview": (4, 3, 75), "meeting": (3, 3, 100), "security": (3, 2, 67),
                    "offer": (2, 1, 50), "first_day": (1, 1, 100)},
           "conversion": (1, 7, 14), "days": (15, 0), "carried": 0, "not_in_set": []},
    "v3": {"rows": {"selected": (0, 0, None), "contact": (0, 0, None), "screening": (1, 0, 0),
                    "interview": (0, 0, None), "meeting": (0, 0, None), "security": (0, 0, None),
                    "offer": (1, 0, 0), "first_day": (0, 0, None)},
           "conversion": (0, 0, None), "days": (53, 0), "carried": 2, "not_in_set": []},
    "rec1": {"rows": {"selected": (10, 8, 80), "contact": (8, 7, 88), "screening": (8, 6, 75),
                      "interview": (6, 5, 83), "meeting": (5, 3, 60), "security": (3, 3, 100),
                      "offer": (4, 2, 50), "first_day": (2, 1, 50)},
             "conversion": (1, 10, 10), "vacancies": 2},
    "rec2": {"rows": {"selected": (7, 6, 86), "contact": (6, 5, 83), "screening": (5, 4, 80),
                      "interview": (4, 3, 75), "meeting": (3, 3, 100), "security": (3, 2, 67),
                      "offer": (2, 1, 50), "first_day": (1, 1, 100)},
             "conversion": (1, 7, 14), "vacancies": 1},
    "department": {"rows": {"selected": (17, 14, 82), "contact": (14, 12, 86), "screening": (13, 10, 77),
                            "interview": (10, 8, 80), "meeting": (8, 6, 75), "security": (6, 5, 83),
                            "offer": (6, 3, 50), "first_day": (3, 2, 67)},
                   "conversion": (2, 17, 12),
                   "recruiters": [("Белов Игорь (демо)", 1, 14), ("Кравцова Анна (демо)", 2, 10)]},
}


class DemoError(RuntimeError):
    """Шаг прогона не прошёл так, как его проходит рекрутер: прогон остановлен."""


class DemoClock(Clock):
    """Часы прогона: время ставит сценарий (день ноября 2026 и час по Москве)."""

    def __init__(self):
        self._now = datetime(YEAR, MONTH, 3, 8, 0, tzinfo=MSK)

    def at(self, day: int, hour: int, minute: int = 0) -> None:
        self._now = datetime(YEAR, MONTH, day, hour, minute, tzinfo=MSK)

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        pass


class Desk:
    """Рабочее место рекрутера: запросы к API под сессией пользователя (новая сессия — каждый день)."""

    def __init__(self, conn_factory: ConnFactory, clock: DemoClock, users: dict[str, int]):
        from fastapi.testclient import TestClient

        from hr.main import app, get_clock, get_conn_factory
        self.app, self.client_cls = app, TestClient
        self.cf, self.clock, self.users = conn_factory, clock, users
        self.sessions: dict[str, tuple] = {}
        app.dependency_overrides[get_conn_factory] = lambda: conn_factory
        app.dependency_overrides[get_clock] = lambda: clock

    def close(self) -> None:
        self.app.dependency_overrides.clear()

    def _session(self, login: str):
        day = self.clock.now().date()
        s = self.sessions.get(login)
        if not s or s[2] != day:
            with self.cf() as conn:
                cookie, csrf = auth.create_session(conn, self.users[login], self.clock.now())
            c = self.client_cls(self.app, base_url="https://localhost:8000")
            c.cookies.set(auth.COOKIE, cookie)
            s = self.sessions[login] = (c, {**ORIGIN, auth.CSRF_HEADER: csrf}, day)
        return s

    def get(self, login: str, url: str, **params):
        return self._session(login)[0].get(url, params=params)

    def post(self, login: str, url: str, body: dict | None = None):
        c, h, _ = self._session(login)
        return c.post(url, json=body if body is not None else {}, headers=h)

    def login(self, login: str, password: str) -> int:
        c = self.client_cls(self.app, base_url="https://localhost:8000")
        return c.post("/api/login", json={"login": login, "password": password}, headers=ORIGIN).status_code


def ok(r, what: str) -> dict:
    if r.status_code != 200:
        raise DemoError(f"{what}: ответ {r.status_code} {r.text}")
    return r.json()


# ------------------------------------------------------------------ данные до прогона

def seed(conn_factory: ConnFactory) -> dict:
    """Пользователи с паролем, утверждённые профили, люди из сбора с сигналами «новый высокий балл»."""
    out: dict = {"users": {}, "profiles": {}, "persons": {}}
    with conn_factory() as conn:
        for login, name, role in USERS:
            out["users"][login] = auth.create_user(conn, login, name, role, PASSWORD)
        out["users"][BOSS[0]] = db.val(conn, "INSERT INTO app_user (login, full_name, role) VALUES (%s, %s, %s) "
                                             "RETURNING id", BOSS)
        head = out["users"]["demo_head"]
        for key, (dept, dtitle, code, title) in PROFILES.items():
            d = db.val(conn, """INSERT INTO department (code, title) VALUES (%s, %s)
                                ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title RETURNING id""", (dept, dtitle))
            out["profiles"][key] = db.val(conn, """
                INSERT INTO role_profile (department_id, code, title, version, keywords, approved_by, approved_at)
                VALUES (%s, %s, %s, '1.0', %s, %s, now()) RETURNING id""", (d, code, title, [title], head))
        habr = db.val(conn, "SELECT id FROM source WHERE code = 'habr'")
        for n, (key, (prof, last, first)) in enumerate(PEOPLE.items(), 1):
            title = PROFILES[prof][3]
            pid = db.val(conn, "INSERT INTO person (label, city) VALUES (%s, 'Краснодар') RETURNING id",
                         (f"{title}, Краснодар",))
            raw = {"last_name": last, "first_name": first, "title": title, "demo": True}
            db.run(conn, """INSERT INTO observation (source_id, external_id, person_id, title, city, raw, content_hash,
                                                     exp_years)
                            VALUES (%s, %s, %s, %s, 'Краснодар', %s, %s, %s)""",
                   (habr, f"funnel-demo-{n}", pid, title, db.j(raw), f"funnel-demo-{n}", 3 + n % 7))
            db.run(conn, """INSERT INTO alert (person_id, role_profile_id, reason, headline)
                            VALUES (%s, %s, 'new_high_score', %s)""",
                   (pid, out["profiles"][prof], f"Новое резюме: {title}"))
            out["persons"][key] = pid
    return out


def legacy_vacancy(conn_factory: ConnFactory, ids: dict) -> tuple[int, dict, dict]:
    """S15: вакансия, заведённая v1.2.0 без набора, с кандидатами в старых статусах; перенос и его повтор."""
    with conn_factory() as conn:
        vid = db.val(conn, """INSERT INTO vacancy (role_profile_id, title, recruiter_id, hiring_manager_id, opened_at)
                              VALUES (%s, 'Инженер-схемотехник', %s, %s, %s) RETURNING id""",
                     (ids["profiles"]["eng"], ids["users"]["demo_rec1"], ids["users"]["demo_boss"],
                      date(YEAR, 10, 1)))
        for fio, status in LEGACY:
            pid = db.val(conn, "INSERT INTO person (label, city) VALUES ('Инженер-схемотехник, Краснодар', "
                               "'Краснодар') RETURNING id")
            cid = db.val(conn, """INSERT INTO candidate (person_id, fio, status, owner_id, retention_until)
                                  VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                         (pid, fio, status, ids["users"]["demo_rec1"], date(YEAR + 3, 10, 1)))
            db.run(conn, "UPDATE person SET candidate_id = %s WHERE id = %s", (cid, pid))
            db.run(conn, "INSERT INTO ops.candidate_vacancy (candidate_id, vacancy_id, added_by) VALUES (%s, %s, %s)",
                   (cid, vid, ids["users"]["demo_rec1"]))
        first = db.one(conn, "SELECT * FROM ops.funnel_carry()")      # то же, что python -m hr.cli funnel-carry
        again = db.one(conn, "SELECT * FROM ops.funnel_carry()")
    return vid, first, again


# ------------------------------------------------------------------ сценарий

class Run:
    def __init__(self, conn_factory: ConnFactory, say: Callable[[str], None]):
        self.cf, self.say = conn_factory, say
        self.clock = DemoClock()
        self.checks: dict[str, bool] = {}
        self.events: list[tuple[int, int, int, int, Callable[[], None]]] = []
        self.cand: dict[str, int] = {}
        self.vac: dict[str, int] = {}

    def check(self, name: str, cond: bool, detail: str = "") -> None:
        self.checks[name] = bool(cond)
        self.say(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail else ''}")

    def on(self, day: int, hour: int, fn: Callable[[], None], minute: int = 0) -> None:
        self.events.append((day, hour, minute, len(self.events), fn))

    def play(self) -> None:
        for day, hour, minute, _, fn in sorted(self.events, key=lambda e: e[:4]):
            self.clock.at(day, hour, minute)
            fn()

    # --- запросы

    def open_slot(self, key: str, vkey: str) -> int | None:
        with self.cf() as conn:
            return db.val(conn, """SELECT slot FROM ops.candidate_stage WHERE candidate_id = %s AND vacancy_id = %s
                                   AND outcome IS NULL AND cancelled_at IS NULL""", (self.cand[key], self.vac[vkey]))

    def row_of(self, key: str, vkey: str) -> str | None:
        with self.cf() as conn:
            return db.val(conn, """SELECT report_row FROM ops.candidate_stage WHERE candidate_id = %s
                                   AND vacancy_id = %s AND outcome IS NULL AND cancelled_at IS NULL""",
                          (self.cand[key], self.vac[vkey]))

    def mark(self, who: str, key: str, vkey: str, outcome: str, reason: str | None = None,
             comment: str | None = None, slot: int | None = None):
        if outcome == "passed" and comment is None and self.row_of(key, vkey) == "meeting":
            comment = "Резолюция руководителя: прошёл, зовём дальше"         # S8
        body = {"slot": slot if slot is not None else self.open_slot(key, vkey), "outcome": outcome,
                "reason": reason, "comment": comment}
        return self.desk.post(who, f"/api/vacancy/{self.vac[vkey]}/candidate/{self.cand[key]}/mark", body)

    def take(self, who: str, key: str, vkey: str):
        r = self.desk.post(who, f"/api/person/{self.ids['persons'][key]}/take", {"vacancy_id": self.vac[vkey]})
        if r.status_code == 200:
            self.cand[key] = r.json()["candidate_id"]
        return r

    def path(self, who: str, key: str, vkey: str, take_day: int, first: int, passes: int,
             end: tuple[str, str] | None = None, end_day: int | None = None) -> None:
        """Кандидат берётся в работу, проходит passes этапов по дню на этап с дня first; end — исход открытого."""
        self.on(take_day, 10, lambda: ok(self.take(who, key, vkey), f"«В работу» {key}"))
        for k in range(passes):
            self.on(first + k, 11, lambda: ok(self.mark(who, key, vkey, "passed"), f"{key}: прошёл"))
        if end:
            outcome, reason = end
            self.on(end_day or first + passes, 12,
                    lambda: ok(self.mark(who, key, vkey, outcome, reason), f"{key}: {outcome} {reason}"))

    # --- сцены

    def setup(self) -> None:
        self.say("03.11 · переход на v1.3.0 и справочник этапов")
        vid, first, again = legacy_vacancy(self.cf, self.ids)
        self.vac["v3"] = vid
        self.check("S15 перенос", (first["sets"], first["marks"]) == (1, 2),
                   f"наборов {first['sets']}, отметок {first['marks']} (телефонное интервью, оффер; резерв и "
                   "вышедший — без отметок)")
        self.check("S15 повтор ничего не меняет", (again["sets"], again["marks"]) == (0, 0))
        r = self.desk.post("demo_head", "/api/stages", {"title": "Техническое интервью", "report_row": "interview"})
        self.check("S1 этап добавлен", r.status_code == 200, "«Техническое интервью» → строка «Интервью»")
        r = self.desk.post("demo_rec1", "/api/stages", {"title": "Ещё этап", "report_row": "interview"})
        self.check("S1 рекрутер не добавляет этап", r.status_code == 403)
        lib = ok(self.desk.get("demo_rec1", "/api/stages"), "справочник")
        self.meet_id = next(s["id"] for s in lib["stages"] if s["title"] == "Собеседование с руководителем")
        self.say(f"  справочник: {len(lib['stages'])} этапов, из них стандартных "
                 f"{sum(s['is_standard'] for s in lib['stages'])}")

    def create(self, who: str, vkey: str, prof: str, title: str, headcount: int, day: int) -> None:
        body = {"role_profile_id": self.ids["profiles"][prof], "title": title, "headcount": headcount,
                "hiring_manager_id": self.ids["users"]["demo_boss"]}
        vid = self.vac[vkey] = ok(self.desk.post(who, "/api/vacancy", body), f"вакансия {title}")["id"]
        with self.cf() as conn:   # дата заведения в портале — день сценария
            db.run(conn, "UPDATE vacancy SET opened_at = %s WHERE id = %s", (date(YEAR, MONTH, day), vid))
        cur = ok(self.desk.get(who, f"/api/vacancy/{vid}/stage-sets"), "набор")["current"]
        self.check("S2 стандартный набор" + (" (вторая вакансия)" if vkey == "v2" else ""),
                   len(cur["items"]) == 8 and cur["version"] == 1, f"«{title}»: 8 этапов, версия 1")

    def items(self, vkey: str, meetings: list[str], drop_rows: tuple[str, ...] = ("security",),
              drop_slots: tuple[int, ...] = ()) -> list[dict]:
        cur = ok(self.desk.get("demo_rec1", f"/api/vacancy/{self.vac[vkey]}/stage-sets"), "набор")["current"]
        items = [{"slot": i["slot"], "stage_id": i["stage_id"], "label": i["label"]} for i in cur["items"]
                 if i["report_row"] not in drop_rows and i["slot"] not in drop_slots]
        if meetings:
            at = next(n for n, i in enumerate(cur["items"]) if i["report_row"] == "meeting") + 1
            items[at:at] = [{"stage_id": self.meet_id, "label": f"Собеседование: {m}"} for m in meetings]
        return items

    def stage_set(self) -> None:
        """S3–S4: набор с четырьмя собеседованиями без СБ — предложен, отозван, отклонён, утверждён."""
        v = self.vac["v1"]
        three = ["главный инженер", "директор", "ОТК"]
        r = ok(self.desk.post("demo_rec1", f"/api/vacancy/{v}/stage-sets",
                              {"items": self.items("v1", three), "note": "четыре собеседования, СБ не нужна"}),
               "предложение набора")
        listing = ok(self.desk.get("demo_head", "/api/vacancies"), "вакансии")
        pending = next(x for x in listing["open"] if x["id"] == v)["stages_pending"]
        self.check("S3 ждёт утверждения", pending, "до утверждения вакансия идёт по стандартному набору")
        r2 = self.desk.post("demo_rec1", f"/api/vacancy/{v}/stage-sets", {"items": self.items("v1", three)})
        self.check("S3 второй набор — pending_exists",
                   r2.status_code == 409 and r2.json().get("error") == "pending_exists")
        w = ok(self.desk.post("demo_rec1", f"/api/stage-sets/{r['id']}/withdraw"), "отзыв")
        self.check("S3 отзыв", w["state"] == "withdrawn")
        r = ok(self.desk.post("demo_rec1", f"/api/vacancy/{v}/stage-sets",
                              {"items": self.items("v1", three[:2]), "note": "без собеседования в ОТК"}),
               "предложение набора")
        ok(self.desk.post("demo_head", f"/api/stage-sets/{r['id']}/decline",
                          {"comment": "Нужно и собеседование в ОТК: инженер принимает их платы."}), "отклонение")
        seen = ok(self.desk.get("demo_rec1", f"/api/vacancy/{v}/stage-sets"), "набор")["declined"]
        self.check("S4 отклонён с комментарием", bool(seen and seen["decision_comment"]),
                   f"рекрутер видит: «{seen and seen['decision_comment']}»")
        r = ok(self.desk.post("demo_rec1", f"/api/vacancy/{v}/stage-sets",
                              {"items": self.items("v1", three), "note": "четыре собеседования, СБ не нужна"}),
               "предложение набора")
        ok(self.desk.post("demo_head", f"/api/stage-sets/{r['id']}/approve"), "утверждение")
        cur = ok(self.desk.get("demo_rec1", f"/api/vacancy/{v}/stage-sets"), "набор")["current"]
        self.check("S4 утверждён", cur["version"] == r["version"] and len(cur["items"]) == 10,
                   f"версия {cur['version']}: " + " · ".join(i["label"] or i["title"] for i in cur["items"]))

    def scene_v1(self) -> None:
        rec = "demo_rec1"
        self.on(3, 9, lambda: self.create(rec, "v1", "eng", "Ведущий инженер-электронщик", 2, 3))
        self.on(3, 9, self.stage_set, 30)
        self.path(rec, "A", "v1", 3, 8, 10)                                  # вышел 17.11
        self.path(rec, "B", "v1", 3, 4, 9, ("refused", "no_first_day"))
        self.path(rec, "C", "v1", 3, 4, 8, ("refused", "offer_declined"))
        self.path(rec, "D", "v1", 4, 5, 6)
        self.on(11, 12, lambda: ok(self.mark(rec, "D", "v1", "failed", "skills",
                                             "Резолюция директора: схемотехника слабее, чем нужно на ведущего"),
                                   "D: не прошёл"))
        self.path(rec, "E", "v1", 4, 5, 4)
        self.on(9, 13, self.undo_same_day)
        self.path(rec, "F", "v1", 4, 5, 3, ("failed", "test_task"))
        self.path(rec, "G", "v1", 5, 6, 2, ("refused", "conditions"))
        self.path(rec, "H", "v1", 5, 6, 1)
        self.on(7, 12, self.other_short)
        self.path(rec, "I", "v1", 5, 6, 0, ("failed", "skills"))
        self.path(rec, "J", "v1", 5, 6, 0)
        self.on(6, 12, lambda: ok(self.mark(rec, "J", "v1", "refused", "no_contact"), "J: ошибочный отказ"))
        self.on(7, 13, self.undo_yesterday)
        self.on(10, 9, lambda: ok(self.desk.post(rec, f"/api/vacancy/{self.vac['v1']}/pause",
                                                 {"comment": "заказчик пересматривает требования"}), "пауза"))
        self.on(12, 9, self.take_on_pause)
        self.on(16, 9, self.resume)
        self.on(17, 12, self.hired_a)
        self.on(21, 10, self.busy_stage)

    def undo_same_day(self) -> None:
        """S6, S16: через этап нельзя; своя ошибочная отметка отменяется в тот же день."""
        rec, cur = "demo_rec1", self.open_slot("E", "v1")
        r = self.mark(rec, "E", "v1", "passed", slot=cur + 2)
        self.check("S6 через этап — stale", r.status_code == 409 and r.json().get("error") == "stale")
        ok(self.mark(rec, "E", "v1", "passed"), "E: ошибочно «прошёл»")
        self.clock.at(9, 13, 10)
        r = self.desk.post(rec, f"/api/vacancy/{self.vac['v1']}/candidate/{self.cand['E']}/undo",
                           {"comment": "отметила не того кандидата"})
        self.check("S16 своя сегодня", r.status_code == 200 and self.open_slot("E", "v1") == cur,
                   "кандидат снова на первом собеседовании без исхода")

    def undo_yesterday(self) -> None:
        url = f"/api/vacancy/{self.vac['v1']}/candidate/{self.cand['J']}/undo"
        r = self.desk.post("demo_rec1", url, {"comment": "кандидат ответил на письмо — отказ ошибочный"})
        self.check("S16 вчерашняя — undo_expired", r.status_code == 403 and r.json().get("error") == "undo_expired",
                   r.json().get("message", ""))
        r = self.desk.post("demo_head", url, {"comment": "по просьбе рекрутера: кандидат ответил на письмо"})
        self.check("S16 руководитель ОП отменяет", r.status_code == 200 and self.row_of("J", "v1") == "selected")

    def other_short(self) -> None:
        """S7: «другое» с коротким комментарием — нельзя; затем отказ с причиной."""
        r = self.mark("demo_rec1", "H", "v1", "refused", "other", "не подошло")
        self.check("S7 «другое» короче 100 — 422", r.status_code == 422)
        ok(self.mark("demo_rec1", "H", "v1", "refused", "no_contact"), "H: не вышел на связь")

    def take_on_pause(self) -> None:
        r = self.take("demo_rec1", "X1", "v1")
        self.check("S14 на паузе новых людей нет", r.status_code == 409, "человек остаётся в «Отобраны агентом»")

    def resume(self) -> None:
        ok(self.desk.post("demo_rec1", f"/api/vacancy/{self.vac['v1']}/resume"), "снятие с паузы")
        with self.cf() as conn:
            n = db.val(conn, """SELECT count(*) FROM ops.candidate_stage WHERE vacancy_id = %s AND outcome IS NOT NULL
                                AND decided_at >= %s AND decided_at < %s""",
                       (self.vac["v1"], datetime(YEAR, MONTH, 10, tzinfo=MSK), datetime(YEAR, MONTH, 16, tzinfo=MSK)))
        self.check("S14 на паузе отметки можно", n > 0, f"за паузу 10–16.11 отмечено итогов: {n}")

    def hired_a(self) -> None:
        self.say("  17.11 · A вышел в первый день; ставок две — вакансия остаётся открытой")

    def busy_stage(self) -> None:
        """S5: убрать этап, на котором стоит человек, нельзя."""
        cur = self.open_slot("E", "v1")
        r = self.desk.post("demo_rec1", f"/api/vacancy/{self.vac['v1']}/stage-sets",
                           {"items": self.items("v1", [], drop_slots=(cur,))})
        body = r.json()
        self.check("S5 этап с людьми — stage_busy",
                   r.status_code == 409 and body.get("error") == "stage_busy" and body.get("count") == 1,
                   body.get("message", ""))

    def scene_v2(self) -> None:
        rec = "demo_rec2"
        self.on(5, 9, lambda: self.create(rec, "v2", "sales", "Менеджер проектных продаж", 1, 5))
        self.path(rec, "K", "v2", 5, 6, 7)
        self.on(20, 13, self.k_first_day)
        self.path(rec, "L", "v2", 5, 6, 5)
        self.on(20, 14, self.after_close)
        self.path(rec, "M", "v2", 5, 6, 2)
        self.on(8, 12, self.offer_declined_wrong_row)
        self.path(rec, "N", "v2", 6, 7, 1, ("refused", "no_contact"))
        self.path(rec, "O", "v2", 6, 7, 0, ("failed", "skills"))
        self.path(rec, "P", "v2", 6, 7, 6, ("refused", "offer_declined"))
        self.path(rec, "Q", "v2", 6, 7, 3, ("failed", "test_task"))

    def offer_declined_wrong_row(self) -> None:
        r = self.mark("demo_rec2", "M", "v2", "refused", "offer_declined")
        self.check("S7 отказ от оффера не на «Оффере» — 422", r.status_code == 422)
        ok(self.mark("demo_rec2", "M", "v2", "refused", "other", OTHER_LONG), "M: другое")

    def k_first_day(self) -> None:
        """S9: вышел в первый день → можно закрыть; рекрутер закрывает."""
        res = ok(self.mark("demo_rec2", "K", "v2", "passed"), "K: вышел")
        self.check("S9 вышел — можно закрыть", res["offer_close"] is True)
        ok(self.desk.post("demo_rec2", f"/api/vacancy/{self.vac['v2']}/close", {"comment": CLOSE_COMMENT}),
           "закрытие")

    def after_close(self) -> None:
        r = self.mark("demo_rec2", "L", "v2", "passed")
        self.check("S9 закрыта: прошёл — 409", r.status_code == 409 and r.json().get("error") == "vacancy_closed")
        r = self.mark("demo_rec2", "L", "v2", "failed", "security")
        self.check("S9 закрыта: не прошёл — можно", r.status_code == 200, "без «зависших»")

    # --- отчёты

    def reports(self) -> dict:
        self.clock.at(23, 15)
        d, v = self.desk, self.vac
        rec1, rec2 = self.ids["users"]["demo_rec1"], self.ids["users"]["demo_rec2"]
        rep = {"v1": ok(d.get("demo_rec1", f"/api/vacancy/{v['v1']}/funnel-report"), "отчёт v1"),
               "v3": ok(d.get("demo_rec1", f"/api/vacancy/{v['v3']}/funnel-report"), "отчёт v3"),
               "v2": ok(d.get("demo_rec2", f"/api/vacancy/{v['v2']}/funnel-report"), "отчёт v2"),
               "rec1": ok(d.get("demo_rec1", f"/api/funnel/recruiter/{rec1}", year=YEAR), "отчёт рекрутера"),
               "rec2": ok(d.get("demo_rec2", f"/api/funnel/recruiter/{rec2}", year=YEAR), "отчёт рекрутера"),
               "department": ok(d.get("demo_head", "/api/funnel/department", year=YEAR), "отчёт отдела")}
        self.say("23.11 · права на отчёты")
        self.check("S12 чужая вакансия — 404", d.get("demo_rec1", f"/api/vacancy/{v['v2']}/funnel-report")
                   .status_code == 404)
        self.check("S12 чужой отчёт рекрутера — 404", d.get("demo_rec2", f"/api/funnel/recruiter/{rec1}")
                   .status_code == 404)
        self.check("S13 отдел рекрутеру — 404", d.get("demo_rec1", "/api/funnel/department").status_code == 404)
        with self.cf() as conn:
            waiting = db.val(conn, "SELECT count(*) FROM alert WHERE state = 'new'")
        self.check("S10 отобранные агентом не в отчёте",
                   waiting == 3 and rep["v1"]["rows"][0]["entered"] == 10,
                   f"ждут решения рекрутера: {waiting}; «Отобранные резюме» — только взятые в работу")
        return rep


# ------------------------------------------------------------------ печать и сверка

def _d(s: str | None) -> str:
    return date.fromisoformat(s).strftime("%d.%m.%Y") if s else "—"


def _pct(p) -> str:
    return "—" if p is None else str(p)


def print_table(say: Callable[[str], None], rep: dict, expand: bool = True) -> None:
    say(f"  {'Строка отчёта':<36}{'вошли':>6}{'прошли':>8}{'%':>6}")
    for r in rep["rows"]:
        note = "  нет в наборе" if not r["in_set"] else ""
        say(f"  {r['title']:<36}{r['entered']:>6}{r['passed']:>8}{_pct(r['pct']):>6}{note}")
        stages = r.get("stages") or []
        if expand and len(stages) > 1:
            for s in stages:
                say(f"    · {s['title']:<32}{s['entered']:>6}{s['passed']:>8}{_pct(s['pct']):>6}")
    c = rep["conversion"]
    say(f"  Конверсия воронки: вышли {c['hired']} из {c['selected']} отобранных — "
        + ("—" if c["pct"] is None else f"{c['pct']}%"))


def print_vacancy(say: Callable[[str], None], rep: dict) -> None:
    state = {"open": "в работе", "on_hold": "на паузе"}.get(rep["state"], f"закрыта {_d(rep['closed_at'])}")
    say(f"\nВоронка вакансии «{rep['title']}» · рекрутер {rep['recruiter']}")
    say(f"  открыта {_d(rep['opened_at'])} · {state} · срок ведения {rep['days']} дн. (паузы {rep['paused_days']} дн.)"
        f" · учёт с {_d(rep['since'])}" + (f" · перенесено при выпуске: {rep['carried']}" if rep["carried"] else ""))
    print_table(say, rep)


def table(rep: dict) -> dict:
    return {r["code"]: (r["entered"], r["passed"], r["pct"]) for r in rep["rows"]}


def conversion(rep: dict) -> tuple:
    c = rep["conversion"]
    return c["hired"], c["selected"], c["pct"]


def facts(rep: dict) -> dict:
    """Отчёт в форме эталона."""
    if "total" in rep:
        out = {"rows": table(rep["total"]), "conversion": conversion(rep["total"])}
        if "vacancies" in rep:
            out["vacancies"] = len(rep["vacancies"])
        if "recruiters" in rep:
            out["recruiters"] = [(r["name"], r["vacancies"], r["total"]["conversion"]["pct"])
                                 for r in rep["recruiters"]]
        return out
    out = {"rows": table(rep), "conversion": conversion(rep), "days": (rep["days"], rep["paused_days"]),
           "carried": rep["carried"], "not_in_set": [r["code"] for r in rep["rows"] if not r["in_set"]]}
    meet = next(r for r in rep["rows"] if r["code"] == "meeting")["stages"]
    if len(meet) > 1:
        out["meetings"] = [(s["title"], s["entered"], s["passed"]) for s in meet]
    return out


def compare(got: dict, ref: dict) -> list[str]:
    bad = []
    for name, want in ref.items():
        have = got.get(name, {})
        for key, value in want.items():
            if have.get(key) != value:
                bad.append(f"{name}.{key}: ждали {value}, получили {have.get(key)}")
    return bad


def run_demo(conn_factory: ConnFactory, say: Callable[[str], None] = print) -> dict:
    """Ядро прогона на готовой схеме. Возвращает отчёты в форме эталона, расхождения и проверки сценариев."""
    run = Run(conn_factory, say)
    run.ids = seed(conn_factory)
    run.desk = Desk(conn_factory, run.clock, run.ids["users"])
    try:
        run.clock.at(3, 8)
        run.setup()
        run.say("03.11–21.11 · вакансии, наборы этапов, путь кандидатов, отмены, пауза, закрытие")
        run.scene_v1()
        run.scene_v2()
        run.play()
        rep = run.reports()
        logins = [u[0] for u in USERS]
        run.check("вход по паролю", all(run.desk.login(u, PASSWORD) == 200 for u in logins), ", ".join(logins))
    finally:
        run.desk.close()
    for key in ("v1", "v2", "v3"):
        print_vacancy(say, rep[key])
    for key in ("rec1", "rec2"):
        r = rep[key]
        say(f"\nВоронка рекрутера {r['vacancies'][0]['recruiter']} за {r['year']} год · вакансий: "
            f"{len(r['vacancies'])}")
        print_table(say, r["total"], expand=False)
    dep = rep["department"]
    say(f"\nВоронка отдела за {dep['year']} год")
    print_table(say, dep["total"], expand=False)
    say("  Сравнение рекрутеров (конверсия):")
    for r in dep["recruiters"]:
        say(f"    {r['name']:<28} вакансий {r['vacancies']} · вышли {r['total']['conversion']['hired']} из "
            f"{r['total']['conversion']['selected']} — {_pct(r['total']['conversion']['pct'])}%")
    got = {k: facts(v) for k, v in rep.items()}
    bad = compare(got, REFERENCE)
    failed = [k for k, v in run.checks.items() if not v]
    say("\nЭталон: " + ("совпадает" if not bad else "расхождения:\n  " + "\n  ".join(bad)))
    say(f"Проверки сценариев: {len(run.checks) - len(failed)} из {len(run.checks)}"
        + (f"; не прошли: {', '.join(failed)}" if failed else ""))
    return {"reports": got, "mismatch": bad, "checks": run.checks, "logins": [u[0] for u in USERS],
            "password": PASSWORD, "vacancies": run.vac}


def _reset(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS ops CASCADE")
        c.execute("DROP SCHEMA IF EXISTS public CASCADE")
        c.execute("CREATE SCHEMA public")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сквозной прогон воронки по этапам на вымышленных данных")
    parser.add_argument("--keep", action="store_true", help="оставить базу заполненной для стенда экранов")
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL", "")
    if not urlparse(dsn).path.endswith("_demo"):
        print("DATABASE_URL должен указывать на базу *_demo: скрипт пересоздаёт её схемы")
        return 2
    _reset(dsn)
    print("схема:", ", ".join(db.migrate(dsn)))
    os.environ.setdefault("SESSION_KEY", secrets.token_urlsafe(32))   # сессии прогона живут только в этом процессе
    from hr.config import get_config
    get_config.cache_clear()
    try:
        out = run_demo(db.tx)
    finally:
        db.close_pool()
    good = not out["mismatch"] and all(out["checks"].values())
    if args.keep:
        print(f"\nБаза оставлена для стенда. Вход: {', '.join(out['logins'])} · пароль {out['password']}")
        print("  demo_head — руководитель ОП, demo_rec1 и demo_rec2 — рекрутеры")
    else:
        _reset(dsn)
        print("\nДанные прогона удалены (схемы базы пустые)")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
