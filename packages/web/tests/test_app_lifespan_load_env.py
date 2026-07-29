"""web lifespan startup 必须调 load_env() —— 锁住「web 入口加载 profile 凭证」不变量。

根因（2026-07-30 __legacy__ 扫描满屏 warning）：web 容器(uvicorn)不 load_env →
scan_manager._resolve_provider_config() 的 build_provider_config() 读不到 profile 里的
SUPERNOVA_AI_PROVIDER（该变量只在 .env.profiles/<profile>.env，不在 .env，docker env_file
也不注入）→ 回落默认 anthropic_api（凭据全空）→ 经 workflow input 传 worker → worker 实例化
Claude Code CLI 引擎，但 worker 是 openai profile、无 ANTHROPIC_AUTH_TOKEN → CLI 每轮
"Not logged in · Please run /login"。worker 入口 runner.main() 早已 load_env（对齐 CLI
blackbox/combined main.py），web 入口漏了同一步。

范式对齐 worker test_runner.test_main_loads_profile_env_before_starting_worker。
"""
from unittest.mock import patch

from starlette.testclient import TestClient


def test_lifespan_loads_profile_env(app_with_ws):
    """lifespan startup 触发时 load_env() 必被调用一次。"""
    import supernova_web.app as app_mod
    with patch.object(app_mod, "load_env", return_value="glm-openai") as mock_le:
        # TestClient 作为 context manager 触发 lifespan startup
        with TestClient(app_with_ws):
            pass
    mock_le.assert_called_once()
