"""Task 4: per-scan proxy_url 穿线 executor → runner → provider。

TDD 测试集。验证：
- AgentExecutor.execute(proxy_url=) 把 proxy_url 注入 prompt variables（供浏览器
  session_flag Task 5 + manager L146）。
- proxy_url=None 时 variables 不含 proxy_url 键（向后兼容铁律）。
- run_claude_prompt(proxy_url=) 透传给 provider.call(proxy_url=)（用 spy 验证）。

镜像 test_executor_auth_state_injection.py 的 fixture 风格（该文件已解决"调 execute
不炸"的问题：mock GitManager 三方法 + PromptManager.load_sync + run_claude_prompt
返真实形状的 _RunResult，让 is_spending_cap_behavior / 后处理顺畅跑完）。
"""
import asyncio

from supernova_core.agents import executor as exec_mod
from supernova_core.agents import runner as runner_mod
from supernova_core.models.agents import AgentName


def _run(coro):
    return asyncio.run(coro)


class _RunResult:
    """run_claude_prompt 返回的桩（execute 期望 success/turns/cost/tokens 等）。

    非 MagicMock：is_spending_cap_behavior(result.turns, result.cost, result.text)
    会做 result.cost>0 比较，MagicMock 不可比较会 raise TypeError。
    """
    success = True
    turns = 1
    cost = 0.0
    text = ""
    error = None
    retryable = True
    model = "stub"
    stop_reason = "end_turn"
    cost_currency = "USD"
    error_code = None

    class tokens:
        input_tokens = 0
        output_tokens = 0
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 0

    structured_output = None


def _patch_execute_github(monkeypatch):
    """mock GitManager 三方法（execute 会真实跑 git 仓储建/检/commit）。"""
    monkeypatch.setattr(
        exec_mod.GitManager, "ensure_repository",
        classmethod(lambda cls, p: asyncio.sleep(0)),
    )
    monkeypatch.setattr(
        exec_mod.GitManager, "create_checkpoint",
        lambda *a, **k: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        exec_mod.GitManager, "commit",
        lambda *a, **k: asyncio.sleep(0),
    )


def _make_executor(tmp_path, monkeypatch, captured):
    """构造一个最小可用的 AgentExecutor，load_sync 捕获 variables。"""
    from supernova_core.prompts.manager import PromptManager
    pm = PromptManager.__new__(PromptManager)
    pm.prompts_dir = tmp_path

    def fake_load(template, *, variables=None, **kw):
        captured["variables"] = variables
        return "PROMPT"

    monkeypatch.setattr(pm, "load_sync", fake_load)
    return exec_mod.AgentExecutor(pm)


def test_execute_threads_proxy_url_into_variables(tmp_path, monkeypatch):
    """execute(proxy_url=) 把 proxy_url 注入 prompt variables。"""
    deliverables = tmp_path / "workspaces" / "session" / "deliverables"
    deliverables.mkdir(parents=True)

    captured: dict = {}

    async def fake_run(**kw):
        return _RunResult()

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    _patch_execute_github(monkeypatch)
    ex = _make_executor(tmp_path, monkeypatch, captured)

    _run(ex.execute(
        agent_name=AgentName.RECON_BLACKBOX,
        repo_path=str(deliverables),
        web_url="https://example.com",
        deliverables_path=str(deliverables),
        proxy_url="http://127.0.0.1:9090",
        skip_artifact_postprocess=True,
    ))

    assert captured["variables"].get("proxy_url") == "http://127.0.0.1:9090", \
        "execute(proxy_url=) 必须把 proxy_url 注入 variables（供 Task 5 manager session_flag）"


def test_execute_no_proxy_means_no_proxy_in_vars(tmp_path, monkeypatch):
    """proxy_url=None → variables 无 proxy_url 键（向后兼容铁律）。"""
    deliverables = tmp_path / "workspaces" / "session" / "deliverables"
    deliverables.mkdir(parents=True)

    captured: dict = {}

    async def fake_run(**kw):
        return _RunResult()

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    _patch_execute_github(monkeypatch)
    ex = _make_executor(tmp_path, monkeypatch, captured)

    _run(ex.execute(
        agent_name=AgentName.RECON_BLACKBOX,
        repo_path=str(deliverables),
        web_url="https://example.com",
        deliverables_path=str(deliverables),
        skip_artifact_postprocess=True,
    ))

    assert "proxy_url" not in captured["variables"], \
        "proxy_url=None 时 variables 不得含 proxy_url 键（保 backward-compat）"


def test_execute_threads_proxy_url_to_run_claude_prompt(tmp_path, monkeypatch):
    """execute(proxy_url=) 把 proxy_url 透传到 run_claude_prompt 调用。"""
    deliverables = tmp_path / "workspaces" / "session" / "deliverables"
    deliverables.mkdir(parents=True)

    captured: dict = {}

    async def fake_run(**kw):
        captured["proxy_url"] = kw.get("proxy_url")
        return _RunResult()

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    _patch_execute_github(monkeypatch)
    ex = _make_executor(tmp_path, monkeypatch, captured)

    _run(ex.execute(
        agent_name=AgentName.RECON_BLACKBOX,
        repo_path=str(deliverables),
        web_url="https://example.com",
        deliverables_path=str(deliverables),
        proxy_url="http://127.0.0.1:9090",
        skip_artifact_postprocess=True,
    ))

    assert captured.get("proxy_url") == "http://127.0.0.1:9090", \
        "execute 必须把 proxy_url 透传给 run_claude_prompt"


def test_run_claude_prompt_threads_proxy_url_to_provider_call(monkeypatch):
    """run_claude_prompt(proxy_url=) 透传 provider.call(proxy_url=)。

    单元层验证 runner → provider 穿线（不实际起 provider）。
    runner.py 在函数内做 ``from .providers import create_provider`` 局部 import，
    故 monkeypatch 必须落在源模块 ``supernova_core.agents.providers`` 上。
    """
    captured: dict = {}

    class _StubProvider:
        def __init__(self, *a, **kw):
            pass

        async def call(self, **kw):
            captured["proxy_url"] = kw.get("proxy_url")
            return _RunResult()

    # providers.create_provider / build_provider_config 是 function-local import，
    # patch 源模块即可。
    from supernova_core.agents import providers as providers_mod
    monkeypatch.setattr(providers_mod, "create_provider", lambda config: _StubProvider())

    _run(runner_mod.run_claude_prompt(
        prompt="P",
        repo_path="/tmp",
        provider_config={},  # 走 ProviderConfig({}) 分支，跳过 build_provider_config
        proxy_url="http://127.0.0.1:9090",
    ))

    assert captured.get("proxy_url") == "http://127.0.0.1:9090", \
        "run_claude_prompt 必须把 proxy_url 透传给 provider.call"
