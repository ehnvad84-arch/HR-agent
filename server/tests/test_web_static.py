"""T22 workplace: оболочка server/web — раздача файлов, CSP, безопасный вывод, контраст (S1, S2, S35)."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hr import config
from hr.main import app

WEB = Path(__file__).resolve().parents[1] / "web"
SOURCES = sorted(p for p in WEB.rglob("*") if p.suffix in {".html", ".js", ".mjs"})
CSS = (WEB / "app.css").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def session_key(monkeypatch):
    monkeypatch.setenv("SESSION_KEY", "k" * 40)
    config.get_config.cache_clear()
    yield
    config.get_config.cache_clear()


@pytest.fixture
def client():
    return TestClient(app, base_url="https://localhost:8000", raise_server_exceptions=False)


# ------------------------------------------------------------------ раздача и CSP

@pytest.mark.parametrize("path,mime", [("/", "text/html"), ("/app.css", "text/css"),
                                       ("/app.js", "text/javascript"), ("/js/api.js", "text/javascript"),
                                       ("/js/dom.js", "text/javascript")])
def test_s2_shell_files_are_served_without_session(client, path, mime):
    r = client.get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith(mime)
    assert "script-src 'self'" in r.headers["content-security-policy"]


def test_s2_shell_carries_no_person_data(client):
    # страница входа и оболочка открыты всем: людей в них нет, данные приходят только из /api/* с сессией
    assert client.get("/api/today").status_code == 401
    html = client.get("/").text
    assert 'src="app.js"' in html and "type=\"module\"" in html


def test_csp_compatible_markup():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert not inline, "inline-скрипты запрещены CSP: script-src 'self'"
    for f in SOURCES:
        text = f.read_text(encoding="utf-8")
        if f.suffix == ".html":
            assert not re.search(r"\son[a-z]+\s*=", text), f"{f.name}: обработчик on…= в разметке запрещён"
            assert "javascript:" not in text, f"{f.name}: javascript:-адрес"
            assert not re.search(r"<script[^>]*src=[\"']https?:", text), f"{f.name}: чужой скрипт"
        else:   # в скриптах — только addEventListener (в h() это атрибуты onClick и т. п.)
            direct = re.search(r"setAttribute\(\s*[\"']on|\.on[a-z]+\s*=[^=]", text)
            assert not direct, f"{f.name}: обработчик не через addEventListener"
    assert not re.search(r"https?://", html), "index.html не должен тянуть ничего с чужих адресов"
    assert not re.search(r"@import|url\(\s*[\"']?https?:", CSS), "app.css не должен тянуть ничего с чужих адресов"


def test_every_import_resolves():
    for f in SOURCES:
        text = f.read_text(encoding="utf-8")
        for rel in re.findall(r"""(?:import|export)[^'"\n]*?from\s+['"](\.[^'"]+)['"]""", text):
            assert (f.parent / rel).resolve().is_file(), f"{f.relative_to(WEB)}: нет файла {rel}"


# ------------------------------------------------------------------ вывод данных только текстом

FORBIDDEN = re.compile(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\s*\(|new\s+Function|"
                       r"createContextualFragment|DOMParser", re.I)


def test_output_has_no_html_sinks():
    for f in SOURCES:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            assert not FORBIDDEN.search(line), f"{f.relative_to(WEB)}:{n}: HTML из строки запрещён: {line.strip()}"


# ------------------------------------------------------------------ контраст токенов (AA)

def _hex(s):
    s = s.strip()
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", s), f"токен должен быть #rrggbb: {s}"
    return [int(s[i:i + 2], 16) for i in (1, 3, 5)]


def _lum(rgb):
    def f(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (f(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _over(rgba, base):
    r, g, b, a = rgba
    return [round(c * a + k * (1 - a)) for c, k in zip((r, g, b), base, strict=True)]


def _tokens(block: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"--([\w-]+)\s*:\s*([^;]+);", block)}


def _blocks():
    light = re.search(r"(?m)^:root\s*\{(.*?)\}", CSS, re.S).group(1)
    media = re.search(r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*"
                      r":root:not\(\[data-theme=\"light\"\]\)\s*\{(.*?)\}", CSS, re.S).group(1)
    attr = re.search(r":root\[data-theme=\"dark\"\]\s*\{(.*?)\}", CSS, re.S).group(1)
    return _tokens(light), _tokens(media), _tokens(attr)


def test_dark_branches_are_identical():
    _, media, attr = _blocks()
    assert media == attr, "тёмная тема в @media и в [data-theme=dark] разошлась"


def _theme(t):
    def tint(tok):
        m = re.fullmatch(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", tok)
        assert m, tok
        return [int(m[1]), int(m[2]), int(m[3]), float(m[4])]
    surf = {k: _hex(t[k]) for k in ("bg", "card", "card2")}
    tints = {c: tint(t[f"{c}-tint"]) for c in ("green", "blue", "orange", "red", "violet")}
    return surf, tints


@pytest.mark.parametrize("theme", [0, 1])
def test_text_contrast_aa(theme):
    light, dark, _ = _blocks()
    t = light if theme == 0 else {**light, **dark}
    surf, tints = _theme(t)
    for tok in ("ink", "ink2", "muted"):
        for name, bg in surf.items():
            assert _ratio(_hex(t[tok]), bg) >= 4.5, f"--{tok} на --{name}"
    # серый текст бывает и на цветной подложке карточки (значок балла, пузырь агента)
    for c, rgba in tints.items():
        assert _ratio(_hex(t["muted"]), _over(rgba, surf["card"])) >= 4.5, f"--muted на --{c}-tint"
    for c, rgba in tints.items():
        ink = _hex(t[f"{c}-ink"])
        for name, bg in surf.items():
            assert _ratio(ink, _over(rgba, bg)) >= 4.5, f"--{c}-ink на --{c}-tint поверх --{name}"
            assert _ratio(ink, bg) >= 4.5, f"--{c}-ink на --{name}"
        # заливка кнопок и номеров зон: текст --on-fill на --{c}-ink
        assert _ratio(_hex(t["on-fill"]), ink) >= 4.5, f"--on-fill на --{c}-ink"


@pytest.mark.parametrize("theme", [0, 1])
def test_ui_edges_contrast_3_to_1(theme):
    light, dark, _ = _blocks()
    t = light if theme == 0 else {**light, **dark}
    surf, _ = _theme(t)
    for tok in ("field-line", "blue-ink"):   # рамка поля и кольцо фокуса
        for name, bg in surf.items():
            assert _ratio(_hex(t[tok]), bg) >= 3, f"--{tok} на --{name}"


def test_focus_ring_and_dialog_styles_present():
    assert re.search(r":focus-visible\s*\{[^}]*outline:\s*2px solid var\(--blue-ink\)", CSS)
    assert "dialog.sheet" in CSS and "::backdrop" in CSS


def test_network_icons_keep_white_letter_readable():
    # значки сетей в «следах в сети»: белая буква на фоне бренда, ≥ 4,5:1
    icons = re.findall(r"\.soc \.(\w+)\{background:(#[0-9a-fA-F]{6})", CSS)
    assert len(icons) >= 12
    for name, color in icons:
        assert _ratio([255, 255, 255], _hex(color)) >= 4.5, f".soc .{name} {color}"
