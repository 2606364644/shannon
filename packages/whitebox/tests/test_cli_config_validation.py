"""CLI 启动 wiring 回归: profile 配置错误必须让 whitebox CLI 启动即失败(PentestError)。

覆盖 Important #3: 防止未来重构意外去掉 cli() 中的 load_env()/validate_active_profile()
调用, 导致错配 profile 静默放行。被测单元是 supernova_whitebox.cli.main.cli 的 wiring,
不是 provider/validator 内部逻辑(那些有各自的单测)。
"""
import pytest
from click.testing import CliRunner

from supernova_core.models.errors import ErrorCode, PentestError
from supernova_whitebox.cli.main import cli


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_misconfigured_profile_aborts_cli_startup(tmp_path, monkeypatch):
    """缺必填变量的 profile → cli() 启动即抛 PentestError(CONFIG_VALIDATION_FAILED)。

    profile 'bad' 声明 SUPERNOVA_AI_PROVIDER=anthropic_api, 但缺必填的 ANTHROPIC_BASE_URL,
    profile_validator 应在 cli() group 回调里启动即失败。
    """
    # 清掉可能从宿主进程继承的 profile 变量, 确保 wiring 只读到 tmp_path 下的文件。
    for var in ("SUPERNOVA_PROFILE", "SUPERNOVA_AI_PROVIDER", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)

    _write(tmp_path / ".env", "SUPERNOVA_PROFILE=bad\n")
    _write(
        tmp_path / ".env.profiles" / "bad.env",
        "SUPERNOVA_AI_PROVIDER=anthropic_api\n"
        # 故意缺 ANTHROPIC_BASE_URL(required)
        "ANTHROPIC_AUTH_TOKEN=tok\n"
        "SUPERNOVA_SMALL_MODEL=glm-4.5-air\n"
        "SUPERNOVA_MEDIUM_MODEL=glm-5.2\n"
        "SUPERNOVA_LARGE_MODEL=glm-5.2\n",
    )
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    # standalone_mode=False 让 group 回调抛出的异常向上传播而非被 click 吞成 SystemExit(2)。
    # 触发任一已注册子命令即可让 group body(load_env + validate_active_profile)先执行。
    with pytest.raises(PentestError) as exc:
        runner.invoke(cli, ["infra", "status"], standalone_mode=False, catch_exceptions=False)

    assert exc.value.error_code == ErrorCode.CONFIG_VALIDATION_FAILED
    assert "ANTHROPIC_BASE_URL" in exc.value.message
