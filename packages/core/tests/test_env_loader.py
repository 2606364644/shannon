"""env_loader: 共享 .env + 当前 profile 文件加载。"""
import os
from pathlib import Path

import pytest

from shannon_core.config.env_loader import load_env
from shannon_core.models.errors import ErrorCode, PentestError


def _write(path: Path, lines: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"{k}={v}" for k, v in lines.items()) + "\n")


def test_loads_shared_then_profile_with_override(tmp_path, monkeypatch):
    """先加载 .env(共享),再加载 profile 文件;profile 同名变量覆盖共享。"""
    for k in ("SHANNON_PROFILE", "SHARED_VAR", "PROFILE_VAR", "OVERRIDDEN"):
        monkeypatch.delenv(k, raising=False)

    _write(tmp_path / ".env", {
        "SHANNON_PROFILE": "glm-openai",
        "SHARED_VAR": "s",
        "OVERRIDDEN": "from-shared",
    })
    _write(tmp_path / ".env.profiles" / "glm-openai.env", {
        "PROFILE_VAR": "p",
        "OVERRIDDEN": "from-profile",
    })

    profile = load_env(base_path=tmp_path / ".env", profiles_dir=tmp_path / ".env.profiles")

    assert profile == "glm-openai"
    assert os.environ["SHARED_VAR"] == "s"
    assert os.environ["PROFILE_VAR"] == "p"
    assert os.environ["OVERRIDDEN"] == "from-profile"  # profile 覆盖共享


def test_missing_profile_env_raises(tmp_path, monkeypatch):
    """SHANNON_PROFILE 未设置 → PentestError。"""
    monkeypatch.delenv("SHANNON_PROFILE", raising=False)
    _write(tmp_path / ".env", {})

    with pytest.raises(PentestError) as exc:
        load_env(base_path=tmp_path / ".env", profiles_dir=tmp_path / ".env.profiles")

    assert exc.value.error_code == ErrorCode.CONFIG_VALIDATION_FAILED
    assert "SHANNON_PROFILE" in exc.value.message


def test_missing_profile_file_raises(tmp_path, monkeypatch):
    """profile 文件不存在 → PentestError,信息含 profile 名。"""
    monkeypatch.delenv("SHANNON_PROFILE", raising=False)
    _write(tmp_path / ".env", {"SHANNON_PROFILE": "nope"})

    with pytest.raises(PentestError) as exc:
        load_env(base_path=tmp_path / ".env", profiles_dir=tmp_path / ".env.profiles")

    assert exc.value.error_code == ErrorCode.CONFIG_VALIDATION_FAILED
    assert "nope" in exc.value.message