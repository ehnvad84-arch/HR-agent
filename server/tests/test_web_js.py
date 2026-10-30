"""T22 workplace: логика оболочки без DOM (клиент API, сессия, адреса в хэше) — тесты node:test в tests/js."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parent / "js"


@pytest.mark.skipif(shutil.which("node") is None, reason="нужен node 20+ для тестов клиента")
def test_shell_js_logic():
    files = sorted(str(p) for p in JS_DIR.glob("*.test.mjs"))
    assert files
    r = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=120)   # noqa: S603, S607
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]
