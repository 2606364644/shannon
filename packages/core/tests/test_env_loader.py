"""env_loader: 共享 .env + 当前 profile 文件加载。"""
import json
import os
from pathlib import Path

import pytest

from supernova_core.config.env_loader import load_env
from supernova_core.models.errors import ErrorCode, PentestError


@pytest.fixture(autouse=True)
def _isolate_pricing_override(monkeypatch):
    """SUPERNOVA_PRICING_OVERRIDE 是进程级 env,load_env 会 setdefault 写入。
    每个测试前后强制清理,避免跨测试泄漏(尤其污染 test_pricing 的 override 读取)。"""
    monkeypatch.delenv("SUPERNOVA_PRICING_OVERRIDE", raising=False)
    yield
    monkeypatch.delenv("SUPERNOVA_PRICING_OVERRIDE", raising=False)


def _write(path: Path, lines: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"{k}={v}" for k, v in lines.items()) + "\n")


def test_loads_shared_then_profile_with_override(tmp_path, monkeypatch):
    """先加载 .env(共享),再加载 profile 文件;profile 同名变量覆盖共享。"""
    for k in ("SUPERNOVA_PROFILE", "SHARED_VAR", "PROFILE_VAR", "OVERRIDDEN"):
        monkeypatch.delenv(k, raising=False)

    _write(tmp_path / ".env", {
        "SUPERNOVA_PROFILE": "glm-openai",
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
    """SUPERNOVA_PROFILE 未设置 → PentestError。"""
    monkeypatch.delenv("SUPERNOVA_PROFILE", raising=False)
    _write(tmp_path / ".env", {})

    with pytest.raises(PentestError) as exc:
        load_env(base_path=tmp_path / ".env", profiles_dir=tmp_path / ".env.profiles")

    assert exc.value.error_code == ErrorCode.CONFIG_VALIDATION_FAILED
    assert "SUPERNOVA_PROFILE" in exc.value.message


def test_missing_profile_file_raises(tmp_path, monkeypatch):
    """profile 文件不存在 → PentestError,信息含 profile 名。"""
    monkeypatch.delenv("SUPERNOVA_PROFILE", raising=False)
    _write(tmp_path / ".env", {"SUPERNOVA_PROFILE": "nope"})

    with pytest.raises(PentestError) as exc:
        load_env(base_path=tmp_path / ".env", profiles_dir=tmp_path / ".env.profiles")

    assert exc.value.error_code == ErrorCode.CONFIG_VALIDATION_FAILED
    assert "nope" in exc.value.message


def test_wires_pricing_override_from_base_pricing_json(tmp_path, monkeypatch):
    """profile=<base>-<engine>(glm-anthropic) → fallback 到 <base>.pricing.json(glm.pricing.json)。

    glm-anthropic / glm-openai 共用 glm.pricing.json(去掉引擎后缀)。spec 2026-07-09
    per-profile 定价 override: env_loader 自动 wire,无需用户手设 SUPERNOVA_PRICING_OVERRIDE。
    """
    monkeypatch.delenv("SUPERNOVA_PRICING_OVERRIDE", raising=False)
    _write(tmp_path / ".env", {"SUPERNOVA_PROFILE": "glm-anthropic"})
    _write(tmp_path / ".env.profiles" / "glm-anthropic.env", {"SUPERNOVA_AI_PROVIDER": "anthropic_api"})
    (tmp_path / ".env.profiles" / "glm.pricing.json").write_text(
        json.dumps({"currency": "CNY", "models": {}}))

    load_env(base_path=tmp_path / ".env", profiles_dir=tmp_path / ".env.profiles")

    assert os.environ["SUPERNOVA_PRICING_OVERRIDE"].endswith("glm.pricing.json")


def test_wires_exact_profile_pricing_json_first(tmp_path, monkeypatch):
    """<profile>.pricing.json 优先于 <base>.pricing.json(deepseek 无引擎后缀 → deepseek.pricing.json)。"""
    monkeypatch.delenv("SUPERNOVA_PRICING_OVERRIDE", raising=False)
    _write(tmp_path / ".env", {"SUPERNOVA_PROFILE": "deepseek"})
    _write(tmp_path / ".env.profiles" / "deepseek.env", {})
    (tmp_path / ".env.profiles" / "deepseek.pricing.json").write_text("{}")

    load_env(base_path=tmp_path / ".env", profiles_dir=tmp_path / ".env.profiles")

    assert os.environ["SUPERNOVA_PRICING_OVERRIDE"].endswith("deepseek.pricing.json")


def test_does_not_override_explicit_pricing_override(tmp_path, monkeypatch):
    """用户已显式设 SUPERNOVA_PRICING_OVERRIDE → setdefault 不覆盖。"""
    monkeypatch.setenv("SUPERNOVA_PRICING_OVERRIDE", "/custom/pricing.json")
    _write(tmp_path / ".env", {"SUPERNOVA_PROFILE": "glm-anthropic"})
    _write(tmp_path / ".env.profiles" / "glm-anthropic.env", {})
    (tmp_path / ".env.profiles" / "glm.pricing.json").write_text("{}")

    load_env(base_path=tmp_path / ".env", profiles_dir=tmp_path / ".env.profiles")

    assert os.environ["SUPERNOVA_PRICING_OVERRIDE"] == "/custom/pricing.json"


def test_no_pricing_json_leaves_override_unset(tmp_path, monkeypatch):
    """profile 无对应 pricing.json → 不设 SUPERNOVA_PRICING_OVERRIDE(回落内置 GLM_PRICING_CNY)。"""
    monkeypatch.delenv("SUPERNOVA_PRICING_OVERRIDE", raising=False)
    _write(tmp_path / ".env", {"SUPERNOVA_PROFILE": "glm-anthropic"})
    _write(tmp_path / ".env.profiles" / "glm-anthropic.env", {})

    load_env(base_path=tmp_path / ".env", profiles_dir=tmp_path / ".env.profiles")

    assert "SUPERNOVA_PRICING_OVERRIDE" not in os.environ
