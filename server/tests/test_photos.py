"""T6 workplace: фото скачивается один раз и сжимается до 10 КБ (S9)."""
import io

import httpx
from factories import hh_item, profile, source
from PIL import Image

from hr import db, photos, tasks
from hr.http import RateLimiter
from hr.normalize import normalize
from hr.pipeline import process

HH_PHOTO = "https://img.hhcdn.ru/photo/1.jpeg"


def big_jpeg(w=800, h=1000) -> bytes:
    img = Image.new("RGB", (w, h))
    for x in range(0, w, 7):   # шум, чтобы JPEG не сжался сам собой
        for y in range(0, h, 7):
            img.putpixel((x, y), ((x * 13) % 256, (y * 7) % 256, (x * y) % 256))
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x010F] = "Камера-шпион"   # производитель — EXIF должен уйти
    img.save(buf, "JPEG", quality=95, exif=exif)
    return buf.getvalue()


class FakeCDN:
    def __init__(self, body=None, status=200):
        self.body, self.status, self.calls = body if body is not None else big_jpeg(), status, []

    def __call__(self, req):
        self.calls.append(str(req.url))
        return httpx.Response(self.status, content=self.body, headers={"content-type": "image/jpeg"})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


def person_with_photo(conn, clock, url=HH_PHOTO, rid="ph1"):
    if not db.val(conn, "SELECT count(*) FROM role_profile"):
        profile(conn)
    it = hh_item(id=rid, photo={"small": url, "medium": url})
    r = process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now())
    return r.person_id, r.observation_id


def run(conn_factory, fake, clock):
    return photos.fetch_photos(conn_factory, http=fake.client(), clock=clock, limiter=RateLimiter(clock))


def test_s09_photo_compressed_under_10kb_without_exif(conn, conn_factory, clock, api):
    pid, oid = person_with_photo(conn, clock)
    st = run(conn_factory, FakeCDN(), clock)
    assert st.new == 1
    row = db.one(conn, "SELECT * FROM ops.person_photo WHERE observation_id = %s", (oid,))
    assert row["state"] == "ok" and len(row["data"]) <= 10240
    img = Image.open(io.BytesIO(row["data"]))
    assert img.format == "JPEG" and not img.getexif()
    c, _, _ = api()
    r = c.get(f"/api/person/{pid}/photo")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and len(r.content) <= 10240


def test_photo_fetched_once(conn, conn_factory, clock):
    person_with_photo(conn, clock)
    fake = FakeCDN()
    run(conn_factory, fake, clock)
    run(conn_factory, fake, clock)
    assert len(fake.calls) == 1


def test_foreign_host_refused(conn, conn_factory, clock):
    _, oid = person_with_photo(conn, clock, url="https://evil.example/a.jpg")
    fake = FakeCDN()
    run(conn_factory, fake, clock)
    assert fake.calls == [] and db.val(conn, "SELECT state FROM ops.person_photo WHERE observation_id = %s",
                                       (oid,)) == "failed"


def test_failed_after_three_attempts(conn, conn_factory, clock):
    _, oid = person_with_photo(conn, clock)
    fake = FakeCDN(status=503)
    for _ in range(4):
        run(conn_factory, fake, clock)
    row = db.one(conn, "SELECT state, attempts FROM ops.person_photo WHERE observation_id = %s", (oid,))
    assert row["state"] == "failed" and row["attempts"] == 3 and len(fake.calls) == 3


def test_not_an_image(conn, conn_factory, clock):
    _, oid = person_with_photo(conn, clock)
    run(conn_factory, FakeCDN(body=b"<html>not image</html>"), clock)
    assert db.val(conn, "SELECT state FROM ops.person_photo WHERE observation_id = %s", (oid,)) != "ok"


def test_no_photo_404(conn, clock, api):
    profile(conn)
    it = hh_item(id="nophoto", photo=None)
    pid = process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now()).person_id
    c, _, _ = api()
    assert c.get(f"/api/person/{pid}/photo").status_code == 404


def test_rate_limited_and_registered(conn, conn_factory, clock):
    person_with_photo(conn, clock, rid="a")
    exp = [{"company": "КБ Другое", "position": "Инженер-электронщик", "start": "2018-01-01", "end": None}]
    it = hh_item(id="b", photo={"medium": HH_PHOTO}, experience=exp, area={"id": "2", "name": "Сочи"})
    process(conn, source(conn, "hh"), normalize("hh", it), it, clock.now())
    run(conn_factory, FakeCDN(), clock)
    assert clock.slept and all(s >= 0.99 for s in clock.slept)
    assert "photos" in tasks.TASKS
