"""T3 funnel-report: библиотека этапов, стандартный набор вакансии, наборы с утверждением (S1–S5)."""
from datetime import UTC, datetime

import pytest
from factories import profile, user

from hr import db, reports, stages, vacancies

NOW = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
ROWS = ["selected", "contact", "screening", "interview", "meeting", "security", "offer", "first_day"]


@pytest.fixture
def people(conn):
    return {"rec": user(conn, "rec", "recruiter"), "head": user(conn, "head", "head_recruiter"),
            "boss": user(conn, "boss", "hiring_manager")}


@pytest.fixture
def vid(conn, people):
    prof = profile(conn)
    return vacancies.create(conn, prof["id"], "Ведущий инженер-электронщик", people["rec"],
                            hiring_manager_id=people["boss"])


def sid(conn, title):
    return db.val(conn, "SELECT id FROM ops.stage WHERE title = %s", (title,))


def current_items(conn, vacancy_id):
    return [(i["slot"], i["title"], i["label"]) for i in stages.current_set(conn, vacancy_id)["items"]]


def items_of(conn, vacancy_id):
    """Действующий набор как вход предложения: все этапы со своими slot."""
    return [{"slot": i["slot"], "stage_id": i["stage_id"], "label": i["label"]}
            for i in stages.current_set(conn, vacancy_id)["items"]]


def journal(conn):
    return db.rows(conn, "SELECT before, after, applied_by, note FROM agent_change WHERE kind = 'stages' ORDER BY id")


def enter_open(conn, vacancy_id, slot):
    """Человек на этапе без исхода (отметки ставит T4 — здесь напрямую)."""
    cid = db.val(conn, """INSERT INTO candidate (fio, retention_until) VALUES ('Вымышленный В. В.', current_date + 100)
                          RETURNING id""")
    st = db.one(conn, """SELECT i.stage_id, s.report_row FROM ops.stage_set_item i JOIN ops.stage s ON s.id = i.stage_id
                         WHERE i.set_id = %s AND i.slot = %s""", (stages.current_set(conn, vacancy_id)["id"], slot))
    db.run(conn, """INSERT INTO ops.candidate_stage (candidate_id, vacancy_id, slot, stage_id, report_row)
                    VALUES (%s, %s, %s, %s, %s)""", (cid, vacancy_id, slot, st["stage_id"], st["report_row"]))
    return cid


# ------------------------------------------------------------------ S1: библиотека

def test_s1_library_rows_standard_and_extra(conn):
    lib = stages.library(conn)
    assert [r["code"] for r in lib["rows"]] == ROWS
    by_title = {s["title"]: s for s in lib["stages"]}
    assert [s["report_row"] for s in lib["stages"] if s["is_standard"]] == ROWS
    assert by_title["Тестовое задание"]["report_row"] == "interview"
    assert by_title["Оплачиваемое тестовое"]["report_row"] == "interview"
    assert by_title["Собеседование с руководителем"]["report_row"] == "meeting"
    assert not by_title["Тестовое задание"]["is_standard"]
    assert [r["code"] for r in lib["reasons"]][-1] == "other"


def test_s1_head_adds_stage_with_report_row(conn, people):
    new = stages.add_stage(conn, "  Собеседование с директором ", "meeting", people["head"], NOW)
    st = db.one(conn, "SELECT title, report_row, is_standard, is_active, created_by FROM ops.stage WHERE id = %s",
                (new,))
    assert st == {"title": "Собеседование с директором", "report_row": "meeting", "is_standard": False,
                  "is_active": True, "created_by": people["head"]}
    [entry] = journal(conn)
    assert entry["applied_by"] == people["head"] and entry["before"] is None
    assert entry["after"] == {"stage_id": new, "title": "Собеседование с директором", "report_row": "meeting"}
    assert "Собеседование с директором" in entry["note"] and "Собеседование" in entry["note"]


def test_s1_stage_needs_title_and_known_row_and_unique_title(conn, people):
    with pytest.raises(stages.Invalid):
        stages.add_stage(conn, "Новый этап", "probation", people["head"], NOW)
    with pytest.raises(stages.Invalid):
        stages.add_stage(conn, "   ", "meeting", people["head"], NOW)
    with pytest.raises(stages.Conflict) as e:
        stages.add_stage(conn, "Тестовое задание", "interview", people["head"], NOW)
    assert e.value.code == "duplicate"
    assert journal(conn) == []


def test_s1_retire_stage_keeps_row_and_journal(conn, people):
    extra = sid(conn, "Оплачиваемое тестовое")
    stages.retire_stage(conn, extra, people["head"], NOW)
    assert db.one(conn, "SELECT report_row, is_active FROM ops.stage WHERE id = %s", (extra,)) == {
        "report_row": "interview", "is_active": False}
    [entry] = journal(conn)
    assert entry["before"] == {"stage_id": extra, "is_active": True}
    assert entry["after"] == {"stage_id": extra, "is_active": False}
    with pytest.raises(stages.Conflict):
        stages.retire_stage(conn, extra, people["head"], NOW)            # уже выведен
    with pytest.raises(stages.Conflict):
        stages.retire_stage(conn, sid(conn, "Интервью"), people["head"], NOW)   # стандартный — основа набора v1
    with pytest.raises(stages.NotFound):
        stages.retire_stage(conn, 999999, people["head"], NOW)


# ------------------------------------------------------------------ S2: стандартный набор

def test_s2_new_vacancy_gets_standard_set(conn, vid):
    cur = stages.current_set(conn, vid)
    assert cur["version"] == 1 and cur["state"] == "approved"
    assert [(i["pos"], i["slot"], i["report_row"]) for i in cur["items"]] == [(n, n, r) for n, r in enumerate(ROWS, 1)]
    assert all(i["is_standard"] for i in cur["items"])
    assert stages.ensure_set(conn, vid) == cur["id"]                       # повтор не плодит наборы
    assert db.val(conn, "SELECT count(*) FROM ops.stage_set WHERE vacancy_id = %s", (vid,)) == 1


def test_s2_vacancy_without_set_gets_v1_on_demand(conn, people):
    """Вакансия, заведённая v1.2.0 после переноса: набор v1 появляется при первом обращении."""
    prof = profile(conn)
    old = db.val(conn, "INSERT INTO vacancy (role_profile_id, title) VALUES (%s, 'Старая') RETURNING id", (prof["id"],))
    assert stages.current_set(conn, old) is None
    stages.ensure_set(conn, old)
    assert [i["report_row"] for i in stages.current_set(conn, old)["items"]] == ROWS


# ------------------------------------------------------------------ S3: предложение

def four_meetings(conn, vacancy_id):
    """Набор с тестовым и четырьмя собеседованиями с разными руководителями."""
    items = items_of(conn, vacancy_id)
    meet = sid(conn, "Собеседование с руководителем")
    at = next(n for n, i in enumerate(items) if i["slot"] == 5) + 1
    extra = [{"stage_id": meet, "label": f"Собеседование: {who}"} for who in ("главный инженер", "директор", "ОТК")]
    test_task = {"stage_id": sid(conn, "Тестовое задание")}
    items = items[:at] + extra + items[at:]
    items.insert(4, test_task)                                             # после «Интервью» — тестовое
    return items


def test_s3_propose_goes_to_head_and_old_set_works(conn, vid, people):
    before = current_items(conn, vid)
    res = stages.propose(conn, vid, four_meetings(conn, vid), "у заказчика четыре собеседования", people["rec"], NOW)
    assert res["version"] == 2
    s = db.one(conn, "SELECT state, note, proposed_by, proposed_at FROM ops.stage_set WHERE id = %s", (res["id"],))
    assert s == {"state": "proposed", "note": "у заказчика четыре собеседования", "proposed_by": people["rec"],
                 "proposed_at": NOW}
    assert current_items(conn, vid) == before                              # до утверждения — прежний набор
    view = stages.sets(conn, vid)
    assert view["current"]["version"] == 1 and view["proposed"]["id"] == res["id"]
    new = [(i["slot"], i["title"], i["label"]) for i in view["proposed"]["items"]]
    assert [x[0] for x in new] == [1, 2, 3, 4, 9, 5, 10, 11, 12, 6, 7, 8]  # прежние slot сохранены, новым — следующие
    assert new[6] == (10, "Собеседование с руководителем", "Собеседование: главный инженер")
    [entry] = journal(conn)
    assert entry["applied_by"] == people["rec"] and entry["after"]["state"] == "proposed"
    assert entry["before"]["version"] == 1 and len(entry["after"]["stages"]) == 12


def test_s3_one_pending_and_withdraw(conn, vid, people):
    first = stages.propose(conn, vid, four_meetings(conn, vid), None, people["rec"], NOW)
    with pytest.raises(stages.Conflict) as e:
        stages.propose(conn, vid, four_meetings(conn, vid), None, people["rec"], NOW)
    assert e.value.code == "pending_exists"
    assert stages.withdraw(conn, first["id"], people["rec"], NOW) == "withdrawn"
    assert stages.sets(conn, vid)["proposed"] is None
    with pytest.raises(stages.Conflict) as e:
        stages.withdraw(conn, first["id"], people["rec"], NOW)
    assert e.value.code == "not_proposed"
    again = stages.propose(conn, vid, four_meetings(conn, vid), None, people["rec"], NOW)
    assert again["version"] == 3
    assert [x["after"]["state"] for x in journal(conn)] == ["proposed", "withdrawn", "proposed"]


def test_s3_set_checks(conn, vid, people):
    items = items_of(conn, vid)
    swapped = items[:3] + [items[4], items[3]] + items[5:]                 # «Собеседование» раньше «Интервью»
    with pytest.raises(stages.Invalid):
        stages.propose(conn, vid, swapped, None, people["rec"], NOW)
    with pytest.raises(stages.Invalid):
        stages.propose(conn, vid, [*items[:3], {"slot": 3, "stage_id": items[2]["stage_id"]}, *items[3:]], None,
                       people["rec"], NOW)                                   # slot дважды
    with pytest.raises(stages.Invalid):                                    # slot не своего этапа
        stages.propose(conn, vid, [*items[:3], {"slot": 4, "stage_id": sid(conn, "Тестовое задание")}, *items[4:]],
                       None, people["rec"], NOW)
    with pytest.raises(stages.Invalid):                                    # чужой slot
        stages.propose(conn, vid, [*items, {"slot": 77, "stage_id": items[-1]["stage_id"]}], None, people["rec"], NOW)
    with pytest.raises(stages.Invalid):                                    # нет такого этапа
        stages.propose(conn, vid, [*items[:4], {"stage_id": 999999}, *items[4:]], None, people["rec"], NOW)
    stages.retire_stage(conn, sid(conn, "Оплачиваемое тестовое"), people["head"], NOW)
    with pytest.raises(stages.Invalid):                                    # выведенный этап в новый набор не берут
        stages.propose(conn, vid, [*items[:4], {"stage_id": sid(conn, "Оплачиваемое тестовое")}, *items[4:]],
                       None, people["rec"], NOW)
    with pytest.raises(stages.Invalid):                                    # ничего не изменилось
        stages.propose(conn, vid, items, None, people["rec"], NOW)
    with pytest.raises(stages.NotFound):
        stages.propose(conn, 999999, items, None, people["rec"], NOW)
    assert stages.sets(conn, vid)["proposed"] is None


# ------------------------------------------------------------------ S4: утверждение

def test_s4_approve_makes_set_current(conn, vid, people):
    res = stages.propose(conn, vid, four_meetings(conn, vid), None, people["rec"], NOW)
    assert stages.approve(conn, res["id"], people["head"], NOW) == "approved"
    cur = stages.current_set(conn, vid)
    assert cur["id"] == res["id"] and cur["version"] == 2 and len(cur["items"]) == 12
    s = db.one(conn, "SELECT decided_by, decided_at FROM ops.stage_set WHERE id = %s", (res["id"],))
    assert s == {"decided_by": people["head"], "decided_at": NOW}
    assert stages.sets(conn, vid)["proposed"] is None
    with pytest.raises(stages.Conflict) as e:
        stages.approve(conn, res["id"], people["head"], NOW)
    assert e.value.code == "not_proposed"
    assert [x["after"]["state"] for x in journal(conn)] == ["proposed", "approved"]
    assert journal(conn)[-1]["applied_by"] == people["head"]


def test_s4_decline_with_comment_seen_by_recruiter(conn, vid, people):
    res = stages.propose(conn, vid, four_meetings(conn, vid), None, people["rec"], NOW)
    with pytest.raises(stages.Invalid):
        stages.decline(conn, res["id"], "   ", people["head"], NOW)
    assert stages.decline(conn, res["id"], "Два собеседования объедините в одно", people["head"], NOW) == "declined"
    view = stages.sets(conn, vid)
    assert view["current"]["version"] == 1 and view["proposed"] is None
    assert view["declined"]["id"] == res["id"]
    assert view["declined"]["decision_comment"] == "Два собеседования объедините в одно"
    assert view["declined"]["decided_by_name"] == "Проверочный head"
    assert journal(conn)[-1]["after"]["state"] == "declined"
    assert journal(conn)[-1]["after"]["comment"] == "Два собеседования объедините в одно"
    stages.propose(conn, vid, four_meetings(conn, vid), None, people["rec"], NOW)
    assert stages.sets(conn, vid)["declined"] is None                      # «Править заново» — новое предложение


def test_s4_missing_set_not_found(conn, people):
    for fn in (stages.withdraw, stages.approve):
        with pytest.raises(stages.NotFound):
            fn(conn, 999999, people["head"], NOW)
    with pytest.raises(stages.NotFound):
        stages.decline(conn, 999999, "нет", people["head"], NOW)


# ------------------------------------------------------------------ S5: этап с людьми, строка вне набора

def test_s5_stage_with_people_cannot_be_removed(conn, vid, people):
    enter_open(conn, vid, 6)
    enter_open(conn, vid, 6)
    without_sb = [i for i in items_of(conn, vid) if i["slot"] != 6]
    with pytest.raises(stages.Conflict) as e:
        stages.propose(conn, vid, without_sb, None, people["rec"], NOW)
    assert e.value.code == "stage_busy" and e.value.data == {"slot": 6, "count": 2}
    assert "2" in str(e.value) and "сначала переведите" in str(e.value)
    assert stages.sets(conn, vid)["current"]["people"] == {6: 2}


def test_s5_whole_row_can_be_removed_but_not_selected_or_first_day(conn, vid, people):
    items = items_of(conn, vid)
    res = stages.propose(conn, vid, [i for i in items if i["slot"] != 6], "СБ не нужна", people["rec"], NOW)
    stages.approve(conn, res["id"], people["head"], NOW)
    assert "security" not in [i["report_row"] for i in stages.current_set(conn, vid)["items"]]
    for gone in (1, 8):
        with pytest.raises(stages.Invalid):
            stages.propose(conn, vid, [i for i in items_of(conn, vid) if i["slot"] != gone], None, people["rec"], NOW)


def test_s5_busy_checked_again_on_approve(conn, vid, people):
    res = stages.propose(conn, vid, [i for i in items_of(conn, vid) if i["slot"] != 6], None, people["rec"], NOW)
    enter_open(conn, vid, 6)                                               # пока ждал — человек дошёл до СБ
    with pytest.raises(stages.Conflict) as e:
        stages.approve(conn, res["id"], people["head"], NOW)
    assert e.value.code == "stage_busy" and e.value.data == {"slot": 6, "count": 1}
    assert db.val(conn, "SELECT state FROM ops.stage_set WHERE id = %s", (res["id"],)) == "proposed"


def test_s5_people_counted_only_open_and_not_cancelled(conn, vid, people):
    cid = enter_open(conn, vid, 6)
    db.run(conn, "UPDATE ops.candidate_stage SET outcome = 'failed', reason = 'security' WHERE candidate_id = %s",
           (cid,))
    other = enter_open(conn, vid, 6)
    db.run(conn, "UPDATE ops.candidate_stage SET cancelled_at = now() WHERE candidate_id = %s", (other,))
    res = stages.propose(conn, vid, [i for i in items_of(conn, vid) if i["slot"] != 6], None, people["rec"], NOW)
    assert res["version"] == 2


# ------------------------------------------------------------------ движение кандидата (T4: S6–S10)

OTHER_100 = "Кандидат попросил отложить выход на два месяца из-за переезда семьи, ждать вакансия не может, " \
            "решили завершить сейчас."


def candidate(conn):
    return db.val(conn, """INSERT INTO candidate (fio, retention_until) VALUES ('Вымышленный В. В.', current_date + 100)
                           RETURNING id""")


def open_mark(conn, cid, vacancy_id):
    return db.one(conn, """SELECT slot, report_row, entered_by FROM ops.candidate_stage
                           WHERE candidate_id = %s AND vacancy_id = %s AND outcome IS NULL AND cancelled_at IS NULL""",
                  (cid, vacancy_id))


def status(conn, cid):
    return db.val(conn, "SELECT status::text FROM candidate WHERE id = %s", (cid,))


def history(conn, cid):
    return [r["to_status"] for r in db.rows(conn, """SELECT to_status::text FROM status_history
                                                     WHERE candidate_id = %s ORDER BY id""", (cid,))]


def walk(conn, cid, vacancy_id, user_id, steps):
    """steps раз «прошёл» с текущего этапа; возвращает последний ответ mark."""
    res = None
    for _ in range(steps):
        res = stages.mark(conn, cid, vacancy_id, open_mark(conn, cid, vacancy_id)["slot"], "passed", user_id, NOW)
    return res


def test_s10_taken_candidate_enters_first_stage(conn, vid, people):
    cid = candidate(conn)
    assert stages.enter(conn, cid, vid, people["rec"], NOW) == 1
    assert open_mark(conn, cid, vid) == {"slot": 1, "report_row": "selected", "entered_by": people["rec"]}
    assert db.val(conn, "SELECT 1 FROM ops.candidate_vacancy WHERE candidate_id = %s AND vacancy_id = %s",
                  (cid, vid))
    assert status(conn, cid) == "picked_by_agent" and history(conn, cid) == ["picked_by_agent"]
    assert stages.enter(conn, cid, vid, people["rec"], NOW) == 1                # повтор ничего не меняет
    assert db.val(conn, "SELECT count(*) FROM ops.candidate_stage WHERE candidate_id = %s", (cid,)) == 1
    assert history(conn, cid) == ["picked_by_agent"]


def test_s10_enter_vacancy_without_set_gets_v1(conn, people):
    prof = profile(conn)
    v = db.val(conn, """INSERT INTO vacancy (role_profile_id, title, recruiter_id) VALUES (%s, 'Из v1.2.0', %s)
                        RETURNING id""", (prof["id"], people["rec"]))
    cid = candidate(conn)
    assert stages.enter(conn, cid, v, people["rec"], NOW) == 1
    assert [i["report_row"] for i in stages.current_set(conn, v)["items"]] == ROWS


def test_s10_enter_needs_open_vacancy(conn, vid, people):
    for state in ("on_hold", "closed"):
        db.run(conn, "UPDATE vacancy SET state = %s, close_comment = repeat('з', 200) WHERE id = %s", (state, vid))
        with pytest.raises(stages.Conflict) as e:
            stages.enter(conn, candidate(conn), vid, people["rec"], NOW)
        assert e.value.code == "vacancy_not_open"
    with pytest.raises(stages.NotFound):
        stages.enter(conn, candidate(conn), 999999, people["rec"], NOW)


def test_s6_passed_moves_to_next_stage_of_set(conn, vid, people):
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    res = stages.mark(conn, cid, vid, 1, "passed", people["rec"], NOW)
    assert res == {"next_slot": 2, "offer_close": False}
    assert open_mark(conn, cid, vid)["report_row"] == "contact"
    done = db.one(conn, """SELECT outcome, reason, decided_by, decided_at FROM ops.candidate_stage
                           WHERE candidate_id = %s AND slot = 1""", (cid,))
    assert done == {"outcome": "passed", "reason": None, "decided_by": people["rec"], "decided_at": NOW}
    assert status(conn, cid) == "first_message_sent"


def test_s6_no_back_and_no_skip(conn, vid, people):
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    with pytest.raises(stages.Conflict) as e:                                 # через этап
        stages.mark(conn, cid, vid, 3, "passed", people["rec"], NOW)
    assert e.value.code == "stale"
    walk(conn, cid, vid, people["rec"], 2)
    with pytest.raises(stages.Conflict) as e:                                 # назад
        stages.mark(conn, cid, vid, 1, "failed", people["rec"], NOW, reason="skills")
    assert e.value.code == "stale"
    assert open_mark(conn, cid, vid)["slot"] == 3
    with pytest.raises(stages.NotFound):                                      # не в воронке вакансии
        stages.mark(conn, candidate(conn), vid, 1, "passed", people["rec"], NOW)
    with pytest.raises(stages.Invalid):
        stages.mark(conn, cid, vid, 3, "moved", people["rec"], NOW)


def test_s6_next_stage_follows_approved_set(conn, vid, people):
    items = items_of(conn, vid)
    test_task = {"stage_id": sid(conn, "Тестовое задание")}
    res = stages.propose(conn, vid, items[:4] + [test_task] + items[4:], None, people["rec"], NOW)
    stages.approve(conn, res["id"], people["head"], NOW)
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    walk(conn, cid, vid, people["rec"], 4)
    mark = db.one(conn, """SELECT s.title, cs.report_row FROM ops.candidate_stage cs
                           JOIN ops.stage s ON s.id = cs.stage_id
                           WHERE cs.candidate_id = %s AND cs.outcome IS NULL""", (cid,))
    assert mark == {"title": "Тестовое задание", "report_row": "interview"}
    walk(conn, cid, vid, people["rec"], 1)
    assert open_mark(conn, cid, vid)["report_row"] == "meeting"


def test_s7_reason_of_own_outcome_and_row(conn, vid, people):
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    walk(conn, cid, vid, people["rec"], 1)                                    # «Первичный контакт»
    for outcome, reason in (("failed", None), ("refused", None), ("failed", "no_contact"), ("refused", "skills"),
                            ("refused", "offer_declined"), ("failed", "unknown")):
        with pytest.raises(stages.Invalid):
            stages.mark(conn, cid, vid, 2, outcome, people["rec"], NOW, reason=reason)
    assert open_mark(conn, cid, vid)["slot"] == 2
    stages.mark(conn, cid, vid, 2, "refused", people["rec"], NOW, reason="no_contact")
    assert open_mark(conn, cid, vid) is None
    assert status(conn, cid) == "refused_contact"
    with pytest.raises(stages.Conflict) as e:                                 # исход пишется один раз
        stages.mark(conn, cid, vid, 2, "passed", people["rec"], NOW)
    assert e.value.code == "stale"


def test_s7_offer_declined_only_on_offer(conn, vid, people):
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    walk(conn, cid, vid, people["rec"], 6)
    assert open_mark(conn, cid, vid)["report_row"] == "offer"
    res = stages.mark(conn, cid, vid, 7, "refused", people["rec"], NOW, reason="offer_declined")
    assert res == {"next_slot": None, "offer_close": False}
    assert status(conn, cid) == "rejected_by_candidate"
    assert "Отказ от оффера" in db.val(conn, "SELECT reject_reason FROM candidate WHERE id = %s", (cid,))


def test_s7_other_needs_comment_from_setting(conn, vid, people):
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    with pytest.raises(stages.Invalid):
        stages.mark(conn, cid, vid, 1, "failed", people["rec"], NOW, reason="other", comment=OTHER_100[:99])
    stages.mark(conn, cid, vid, 1, "failed", people["rec"], NOW, reason="other", comment=OTHER_100)
    done = db.one(conn, "SELECT outcome, reason, comment FROM ops.candidate_stage WHERE candidate_id = %s", (cid,))
    assert done == {"outcome": "failed", "reason": "other", "comment": OTHER_100}
    assert status(conn, cid) == "rejected_by_us"
    db.run(conn, "UPDATE settings SET value = '10' WHERE key = 'stage_fail_other_min'")
    other = candidate(conn)
    stages.enter(conn, other, vid, people["rec"], NOW)
    stages.mark(conn, other, vid, 1, "refused", people["rec"], NOW, reason="other", comment="Уехал в Казань")
    assert status(conn, other) == "rejected_by_candidate"


def test_s7_refused_reasons_map_to_status(conn, vid, people):
    for reason, expected in (("conditions", "rejected_by_candidate"), ("no_show", "no_show"),
                             ("no_first_day", "no_show"), ("no_contact", "refused_contact")):
        cid = candidate(conn)
        stages.enter(conn, cid, vid, people["rec"], NOW)
        stages.mark(conn, cid, vid, 1, "refused", people["rec"], NOW, reason=reason)
        assert status(conn, cid) == expected, reason
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    stages.mark(conn, cid, vid, 1, "failed", people["rec"], NOW, reason="security")
    assert status(conn, cid) == "rejected_by_us"


def test_s8_manager_resolution_is_recruiter_mark_with_comment(conn, vid, people):
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    walk(conn, cid, vid, people["rec"], 4)
    assert open_mark(conn, cid, vid)["report_row"] == "meeting"
    stages.mark(conn, cid, vid, 5, "passed", people["rec"], NOW, comment="Резолюция руководителя: берём")
    done = db.one(conn, "SELECT decided_by, comment FROM ops.candidate_stage WHERE candidate_id = %s AND slot = 5",
                  (cid,))
    assert done == {"decided_by": people["rec"], "comment": "Резолюция руководителя: берём"}


def test_s9_status_follows_stages_and_first_day_hires(conn, vid, people):
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    seen = [status(conn, cid)]
    for _ in range(7):
        walk(conn, cid, vid, people["rec"], 1)
        seen.append(status(conn, cid))
    assert seen == ["picked_by_agent", "first_message_sent", "phone_interview", "phone_interview",
                    "manager_interview", "security_check", "offer", "offer"]
    res = stages.mark(conn, cid, vid, 8, "passed", people["rec"], NOW)
    assert res == {"next_slot": None, "offer_close": True}                   # headcount 1 — вышли все ставки
    assert status(conn, cid) == "hired" and history(conn, cid)[-1] == "hired"
    assert db.val(conn, "SELECT state::text FROM vacancy WHERE id = %s", (vid,)) == "open"   # закрывает рекрутер


def test_s9_offer_close_when_hired_reach_headcount(conn, vid, people):
    db.run(conn, "UPDATE vacancy SET headcount = 2 WHERE id = %s", (vid,))
    first, second = candidate(conn), candidate(conn)
    for cid in (first, second):
        stages.enter(conn, cid, vid, people["rec"], NOW)
    assert walk(conn, first, vid, people["rec"], 8) == {"next_slot": None, "offer_close": False}
    assert walk(conn, second, vid, people["rec"], 8) == {"next_slot": None, "offer_close": True}


def test_s9_closed_vacancy_no_passed_but_failed_and_refused(conn, vid, people):
    a, b = candidate(conn), candidate(conn)
    for cid in (a, b):
        stages.enter(conn, cid, vid, people["rec"], NOW)
    db.run(conn, "UPDATE vacancy SET state = 'closed', close_comment = repeat('з', 200) WHERE id = %s", (vid,))
    with pytest.raises(stages.Conflict) as e:
        stages.mark(conn, a, vid, 1, "passed", people["rec"], NOW)
    assert e.value.code == "vacancy_closed"
    stages.mark(conn, a, vid, 1, "failed", people["rec"], NOW, reason="skills")
    stages.mark(conn, b, vid, 1, "refused", people["rec"], NOW, reason="conditions")
    assert db.val(conn, """SELECT count(*) FROM ops.candidate_stage WHERE vacancy_id = %s AND outcome IS NULL""",
                  (vid,)) == 0


def test_s14_marks_allowed_on_pause(conn, vid, people):
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    db.run(conn, "UPDATE vacancy SET state = 'on_hold' WHERE id = %s", (vid,))
    assert stages.mark(conn, cid, vid, 1, "passed", people["rec"], NOW)["next_slot"] == 2


# ------------------------------------------------------------------ журнал

def test_journal_kind_title():
    assert reports.KIND_TITLE["stages"] == "Этапы вакансий"


# ------------------------------------------------------------------ S16: отмена ошибочной отметки

UNDO = "ошиблась кандидатом"


def marks(conn, cid, vacancy_id):
    return db.rows(conn, """SELECT slot, outcome, entered_by, entered_at, carried, cancelled_by, cancelled_at,
                                   cancel_comment
                            FROM ops.candidate_stage WHERE candidate_id = %s AND vacancy_id = %s ORDER BY id""",
                   (cid, vacancy_id))


def audit(conn, cid):
    return db.rows(conn, """SELECT user_id, action, object_type, detail FROM audit_log
                            WHERE object_type = 'candidate_stage' AND (detail->>'candidate_id')::bigint = %s
                            ORDER BY id""", (cid,))


def test_s16_own_mark_same_day_undone(conn, vid, people):
    cid = candidate(conn)
    entered = datetime(2026, 11, 24, 7, 0, tzinfo=UTC)
    stages.enter(conn, cid, vid, people["rec"], entered)
    stages.mark(conn, cid, vid, 1, "passed", people["rec"], NOW)
    later = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)
    assert stages.undo(conn, cid, vid, people["rec"], later, comment=UNDO) == {"slot": 1}
    got = marks(conn, cid, vid)
    assert [(m["slot"], m["outcome"], m["cancelled_at"] is not None) for m in got] == \
        [(1, "passed", True), (2, None, True), (1, None, False)]
    for m in got[:2]:
        assert (m["cancelled_by"], m["cancelled_at"], m["cancel_comment"]) == (people["rec"], later, UNDO)
    assert (got[2]["entered_by"], got[2]["entered_at"]) == (people["rec"], entered)     # прежний вход
    assert open_mark(conn, cid, vid)["slot"] == 1
    assert status(conn, cid) == "picked_by_agent"
    assert history(conn, cid) == ["picked_by_agent", "first_message_sent", "picked_by_agent"]
    log = audit(conn, cid)
    assert len(log) == 1 and log[0]["user_id"] == people["rec"] and log[0]["action"] == "edit"
    assert log[0]["detail"]["slot"] == 1 and log[0]["detail"]["outcome"] == "passed"
    assert log[0]["detail"]["vacancy_id"] == vid and log[0]["detail"]["comment"] == UNDO
    assert stages.mark(conn, cid, vid, 1, "passed", people["rec"], later)["next_slot"] == 2   # снова по набору


def test_s16_undo_refusal_returns_to_stage(conn, vid, people):
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    walk(conn, cid, vid, people["rec"], 2)
    stages.mark(conn, cid, vid, 3, "refused", people["rec"], NOW, reason="no_contact")
    assert status(conn, cid) == "refused_contact"
    assert stages.undo(conn, cid, vid, people["rec"], NOW, comment=UNDO) == {"slot": 3}
    assert open_mark(conn, cid, vid)["slot"] == 3 and status(conn, cid) == "phone_interview"
    assert stages.undo(conn, cid, vid, people["rec"], NOW, comment=UNDO) == {"slot": 2}    # и предыдущую — своя
    assert open_mark(conn, cid, vid)["slot"] == 2 and status(conn, cid) == "first_message_sent"


def test_s16_undo_hired_back_to_first_day(conn, vid, people):
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    walk(conn, cid, vid, people["rec"], 8)
    assert status(conn, cid) == "hired"
    assert stages.undo(conn, cid, vid, people["rec"], NOW, comment=UNDO) == {"slot": 8}
    assert open_mark(conn, cid, vid)["report_row"] == "first_day" and status(conn, cid) == "offer"


def test_s16_yesterday_by_moscow_only_head(conn, vid, people):
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    evening = datetime(2026, 11, 24, 20, 30, tzinfo=UTC)             # 23:30 по Москве
    stages.mark(conn, cid, vid, 1, "passed", people["rec"], evening)
    after_midnight = datetime(2026, 11, 24, 21, 30, tzinfo=UTC)      # 00:30 следующего дня по Москве
    with pytest.raises(stages.Forbidden) as e:
        stages.undo(conn, cid, vid, people["rec"], after_midnight, comment=UNDO)
    assert e.value.code == "undo_expired" and "руководитель ОП" in str(e.value)
    assert open_mark(conn, cid, vid)["slot"] == 2                             # ничего не изменилось
    assert stages.undo(conn, cid, vid, people["head"], after_midnight, comment=UNDO) == {"slot": 1}
    assert audit(conn, cid)[0]["user_id"] == people["head"]


def test_s16_same_moscow_day_across_utc_midnight(conn, vid, people):
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    stages.mark(conn, cid, vid, 1, "passed", people["rec"], datetime(2026, 11, 24, 21, 30, tzinfo=UTC))
    assert stages.undo(conn, cid, vid, people["rec"], datetime(2026, 11, 25, 9, 0, tzinfo=UTC),
                       comment=UNDO) == {"slot": 1}                          # оба — 25.11 по Москве


def test_s16_other_recruiters_mark_only_head(conn, vid, people):
    other = user(conn, "rec2", "recruiter")
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    stages.mark(conn, cid, vid, 1, "passed", people["rec"], NOW)
    with pytest.raises(stages.Forbidden) as e:
        stages.undo(conn, cid, vid, other, NOW, comment=UNDO)
    assert e.value.code == "undo_expired"
    assert stages.undo(conn, cid, vid, people["head"], NOW, comment=UNDO) == {"slot": 1}


def test_s16_nothing_to_undo_comment_closed_and_missing(conn, vid, people):
    cid = candidate(conn)
    with pytest.raises(stages.NotFound):
        stages.undo(conn, cid, vid, people["rec"], NOW, comment=UNDO)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    with pytest.raises(stages.Conflict) as e:
        stages.undo(conn, cid, vid, people["rec"], NOW, comment=UNDO)
    assert e.value.code == "nothing_to_undo"
    stages.mark(conn, cid, vid, 1, "passed", people["rec"], NOW)
    with pytest.raises(stages.Invalid):
        stages.undo(conn, cid, vid, people["rec"], NOW, comment="  ")
    db.run(conn, "UPDATE vacancy SET state = 'closed', close_comment = repeat('з', 200) WHERE id = %s", (vid,))
    with pytest.raises(stages.Conflict) as e:
        stages.undo(conn, cid, vid, people["head"], NOW, comment=UNDO)
    assert e.value.code == "vacancy_closed"
    assert audit(conn, cid) == []


def test_s16_carried_mark_keeps_carried_after_undo(conn, vid, people):
    cid = enter_open(conn, vid, 3)
    db.run(conn, "UPDATE ops.candidate_stage SET carried = true WHERE candidate_id = %s", (cid,))
    stages.mark(conn, cid, vid, 3, "passed", people["rec"], NOW)
    stages.undo(conn, cid, vid, people["rec"], NOW, comment=UNDO)
    assert [(m["slot"], m["carried"]) for m in marks(conn, cid, vid) if m["cancelled_at"] is None] == [(3, True)]


def _set_without(conn, vacancy_id, slot, replace_with=None):
    """Действующий набор без этапа slot (на нём никого) — вместо него этап replace_with той же строки."""
    items = []
    for i in items_of(conn, vacancy_id):
        if i["slot"] != slot:
            items.append(i)
        elif replace_with:
            items.append({"stage_id": sid(conn, replace_with), "label": None})
    return items


def test_s16_undo_after_stage_removed_opens_same_row(conn, vid, people):
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    walk(conn, cid, vid, people["rec"], 5)                       # прошёл «Собеседование» (slot 5), стоит на slot 6
    s = stages.propose(conn, vid, _set_without(conn, vid, 5, "Собеседование с руководителем"), None, people["rec"],
                       NOW)
    stages.approve(conn, s["id"], people["head"], NOW)
    new_slot = next(i["slot"] for i in stages.current_set(conn, vid)["items"] if i["report_row"] == "meeting")
    assert stages.undo(conn, cid, vid, people["rec"], NOW, comment=UNDO) == {"slot": new_slot}
    assert open_mark(conn, cid, vid) == {"slot": new_slot, "report_row": "meeting", "entered_by": people["rec"]}
    assert status(conn, cid) == "manager_interview"
    assert stages.mark(conn, cid, vid, new_slot, "passed", people["rec"], NOW)["next_slot"] == 6   # снова по набору


def test_s16_undo_after_row_removed_is_stage_removed(conn, vid, people):
    cid = candidate(conn)
    stages.enter(conn, cid, vid, people["rec"], NOW)
    walk(conn, cid, vid, people["rec"], 5)
    stages.approve(conn, stages.propose(conn, vid, _set_without(conn, vid, 5), None, people["rec"], NOW)["id"],
                   people["head"], NOW)
    with pytest.raises(stages.Conflict) as e:
        stages.undo(conn, cid, vid, people["rec"], NOW, comment=UNDO)
    assert e.value.code == "stage_removed" and "убран из набора" in str(e.value)
    assert open_mark(conn, cid, vid)["slot"] == 6 and audit(conn, cid) == []         # ничего не изменилось
