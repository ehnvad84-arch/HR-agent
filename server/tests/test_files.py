"""T17: выгрузки из кабинетов (S7, S8)."""
import os

import openpyxl
import openpyxl.xml
from factories import profile

from hr import db, files

HEAD = "id;Должность;Город;Навыки;Места работы;Опыт лет;ФИО\n"
ROW1 = ("h-1;Ведущий инженер-электронщик;Краснодар;Altium, схемотехника, трассировка, ЭМС;Приборный завод;9;"
        "Иванова Анна\n")
ROW2 = "h-2;Инженер ОТК;Краснодар;AQL;Завод;4;Петров Пётр\n"


def write(folder, name, text, enc="utf-8"):
    p = folder / name
    p.write_bytes(text.encode(enc))
    return p


def test_s07_csv_from_cabinet_becomes_observations(conn, conn_factory, clock, tmp_path):
    profile(conn)
    write(tmp_path, "habr_2026-10-25.csv", HEAD + ROW1 + ROW2)
    stats = files.import_files(conn_factory, folder=str(tmp_path), clock=clock)
    assert stats.seen == 2 and stats.new == 2 and stats.errors == 0
    src = db.rows(conn, "SELECT DISTINCT s.code FROM observation o JOIN source s ON s.id = o.source_id")
    assert [r["code"] for r in src] == ["habr"]
    done = list((tmp_path / "done").iterdir())
    assert len(done) == 1 and done[0].name.endswith("_habr_2026-10-25.csv")
    assert not (tmp_path / "habr_2026-10-25.csv").exists()
    f = db.one(conn, "SELECT * FROM ops.import_file")
    assert f["state"] == "applied" and f["source_code"] == "habr"


def test_s07_xlsx_and_unknown_prefix_is_manual(conn, conn_factory, clock, tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["id", "Должность", "Город"])
    ws.append(["x-1", "Кладовщик", "Краснодар"])
    wb.save(tmp_path / "выгрузка_октябрь.xlsx")
    files.import_files(conn_factory, folder=str(tmp_path), clock=clock)
    assert db.val(conn, "SELECT s.code FROM observation o JOIN source s ON s.id = o.source_id") == "manual"


def test_s07_cp1251_csv(conn, conn_factory, clock, tmp_path):
    write(tmp_path, "avito_1.csv", HEAD + ROW2, enc="cp1251")
    stats = files.import_files(conn_factory, folder=str(tmp_path), clock=clock)
    assert stats.new == 1
    assert db.val(conn, "SELECT title FROM observation") == "Инженер ОТК"


def test_s08_same_file_again_is_skipped(conn, conn_factory, clock, tmp_path):
    write(tmp_path, "habr_a.csv", HEAD + ROW1)
    files.import_files(conn_factory, folder=str(tmp_path), clock=clock)
    before = db.val(conn, "SELECT count(*) FROM obs_version")
    write(tmp_path, "habr_b.csv", HEAD + ROW1)
    clock.advance(seconds=5)
    stats = files.import_files(conn_factory, folder=str(tmp_path), clock=clock)
    assert stats.skipped == 1 and stats.seen == 0
    assert db.val(conn, "SELECT count(*) FROM obs_version") == before
    assert db.val(conn, "SELECT count(*) FROM ops.import_file") == 1


def test_s08_bad_row_counted_rest_accepted(conn, conn_factory, clock, tmp_path):
    write(tmp_path, "habr_c.csv", HEAD + ";Без идентификатора;Краснодар;;;;\n" + ROW2)
    stats = files.import_files(conn_factory, folder=str(tmp_path), clock=clock)
    assert stats.errors == 1 and stats.new == 1


def test_same_name_does_not_overwrite_done(conn, conn_factory, clock, tmp_path):
    write(tmp_path, "habr_x.csv", HEAD + ROW1)
    files.import_files(conn_factory, folder=str(tmp_path), clock=clock)
    clock.advance(seconds=1)
    write(tmp_path, "habr_x.csv", HEAD + ROW2)
    files.import_files(conn_factory, folder=str(tmp_path), clock=clock)
    assert len(list((tmp_path / "done").iterdir())) == 2


def test_unreadable_and_too_big_go_to_error(conn, conn_factory, clock, tmp_path):
    db.run(conn, "UPDATE settings SET value = '0' WHERE key = 'import_max_file_mb'")
    write(tmp_path, "habr_big.csv", HEAD + ROW1)
    (tmp_path / "broken.xlsx").write_bytes(b"not a zip")
    stats = files.import_files(conn_factory, folder=str(tmp_path), clock=clock)
    errors = sorted(p.name.split("_", 1)[1] for p in (tmp_path / "error").iterdir())
    assert errors == ["broken.xlsx", "habr_big.csv"]
    assert stats.errors == 2
    assert {r["state"] for r in db.rows(conn, "SELECT state FROM ops.import_file")} == {"rejected"}


def test_row_limit(conn, conn_factory, clock, tmp_path):
    db.run(conn, "UPDATE settings SET value = '1' WHERE key = 'import_max_rows'")
    write(tmp_path, "habr_rows.csv", HEAD + ROW1 + ROW2)
    files.import_files(conn_factory, folder=str(tmp_path), clock=clock)
    assert (tmp_path / "error").exists() and db.val(conn, "SELECT count(*) FROM observation") == 0


def test_symlink_and_other_extensions_ignored(conn, conn_factory, clock, tmp_path):
    target = write(tmp_path / ".." , "outside.csv", HEAD + ROW1)
    os.symlink(target, tmp_path / "habr_link.csv")
    write(tmp_path, "notes.txt", "x")
    stats = files.import_files(conn_factory, folder=str(tmp_path), clock=clock)
    assert stats.seen == 0 and (tmp_path / "notes.txt").exists()


def test_openpyxl_uses_defusedxml():
    assert openpyxl.xml.DEFUSEDXML is True
