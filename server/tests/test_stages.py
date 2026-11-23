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


# ------------------------------------------------------------------ журнал

def test_journal_kind_title():
    assert reports.KIND_TITLE["stages"] == "Этапы вакансий"
