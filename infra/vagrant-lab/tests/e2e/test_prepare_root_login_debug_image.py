from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "infra" / "vagrant-lab" / "real-boot-lane" / "prepare_root_login_debug_image.py"

spec = importlib.util.spec_from_file_location("prepare_root_login_debug_image", SCRIPT)
assert spec and spec.loader
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def test_read_secret_from_file(tmp_path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("abc123\n", encoding="utf-8")

    assert helper.read_secret(secret, None) == "abc123"


def test_read_secret_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("TC_SECRET_TEST", "from-env")

    assert helper.read_secret(None, "TC_SECRET_TEST") == "from-env"


def test_helper_source_does_not_print_secret_value() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "<redacted>" in text
    assert "print(boot_arg)" not in text
