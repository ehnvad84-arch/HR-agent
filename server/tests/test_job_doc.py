"""T16 workplace: замена должностной инструкции (S28; пункт 29)."""
import pytest
from factories import profile

from hr import config, db

DOCX = b"PK\x03\x04" + b"\x00" * 200          # начало zip: .docx
PDF = b"%PDF-1.7\n" + b"0" * 200


@pytest.fixture
def storage(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    config.get_config.cache_clear()
    return tmp_path


def upload(c, h, pid, name="ДИ инженер.docx", data=DOCX, revision="ред. от 25.09.2026"):
    return c.post(f"/api/profile/{pid}/doc", files={"file": (name, data)}, data={"revision": revision}, headers=h)


def test_s28_replace_job_doc(conn, api, storage):
    prof = profile(conn)
    c, h, uid = api()
    r = upload(c, h, prof["id"])
    assert r.status_code == 200
    doc = db.one(conn, "SELECT * FROM job_doc WHERE id = %s", (r.json()["job_doc_id"],))
    assert doc["file_name"] == "ДИ инженер.docx" and doc["uploaded_by"] == uid and doc["revision"].startswith("ред.")
    stored = storage / doc["storage_key"]
    assert stored.read_bytes() == DOCX and "ДИ" not in doc["storage_key"], "имя файла не попадает в путь"
    draft = db.one(conn, "SELECT * FROM role_profile WHERE id = %s", (r.json()["draft_version_id"],))
    assert draft["code"] == prof["code"] and draft["approved_at"] is None
    assert draft["di_storage_key"] == doc["storage_key"]
    assert c.get(f"/api/profile/{draft['id']}").json()["state"] == "draft"


def test_pdf_accepted_bad_type_and_size_rejected(conn, api, storage):
    prof = profile(conn)
    c, h, _ = api()
    assert upload(c, h, prof["id"], name="ди.pdf", data=PDF).status_code == 200
    assert upload(c, h, prof["id"], name="ди.exe", data=b"MZ" + b"0" * 10).status_code == 422
    assert upload(c, h, prof["id"], name="ди.docx", data=b"not a zip").status_code == 422
    db.run(conn, "UPDATE settings SET value = '0' WHERE key = 'import_max_file_mb'")
    assert upload(c, h, prof["id"]).status_code == 422


def test_path_traversal_name_is_harmless(conn, api, storage):
    prof = profile(conn)
    c, h, _ = api()
    r = upload(c, h, prof["id"], name="../../etc/passwd.docx")
    key = db.val(conn, "SELECT storage_key FROM job_doc WHERE id = %s", (r.json()["job_doc_id"],))
    assert ".." not in key and (storage / key).exists()


def test_edit_after_upload_is_pending_not_draft(conn, api, storage):
    prof = profile(conn)
    c, h, _ = api()
    draft = upload(c, h, prof["id"]).json()["draft_version_id"]
    new = c.post(f"/api/profile/{draft}", json={"keywords": ["Altium"]}, headers=h).json()
    assert new["state"] == "pending" and c.get(f"/api/profile/{new['id']}").json()["state"] == "pending"
