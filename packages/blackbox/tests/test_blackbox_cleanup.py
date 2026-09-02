"""Blackbox cleanup_engine_configs 同时清理 config 文件 + browser 进程。

2026-09-03 xss 40min 事故（274 chromium 压穿 4G worker）B 层测试：
- exploit agent 结束（成功/失败）即回收自己的浏览器 session（不再死占到扫描级 finally）
- engine_name 缺省（CLI 直跑）不清理，零回归
- resolve_blackbox_engine 注入 agent-browser 官方 idle 自愈（AGENT_BROWSER_IDLE_TIMEOUT_MS）
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from supernova_core.models.metrics import AgentMetrics
from supernova_blackbox.pipeline import activities as act
from supernova_blackbox.pipeline.shared import BlackboxActivityInput


class TestCleanupEngineConfigsAlsoKillsProcesses:
    async def test_cleanup_calls_cleanup_processes(self, monkeypatch):
        """cleanup_engine_configs 应在删 config 后调 engine.cleanup_processes。"""
        from supernova_blackbox.pipeline import activities as activities_mod

        calls = {}

        class FakeEngine:
            def cleanup_config(self, source_dir, session_id=None):
                calls.setdefault("cleanup_config", []).append(session_id)

            def cleanup_processes(self, source_dir=None, session_ids=None):
                calls["cleanup_processes"] = (source_dir, session_ids)
                return {"closed": [], "killed": [], "errors": []}

        monkeypatch.setattr(
            "supernova_core.services.browser_engine.BrowserEngineFactory.get_engine",
            lambda name: FakeEngine(),
        )
        monkeypatch.setattr(
            "supernova_core.services.playwright_config_writer.AGENT_SESSION_MAPPING",
            {"a": "agent1", "b": "agent2"},
        )

        await activities_mod.cleanup_engine_configs("/tmp/repo", "agent-browser")

        assert "cleanup_processes" in calls
        src, sids = calls["cleanup_processes"]
        assert src == "/tmp/repo"
        assert set(sids) == {"agent1", "agent2"}


# ---------------------------------------------------------------------------
# B 层：agent 结束即回收自己的浏览器 session（2026-09-03 xss 40min 事故）
# ---------------------------------------------------------------------------


def _patch_activity_env(monkeypatch):
    """打 temporal activity 上下文 + audit 依赖（同 test_activities_exploit_queue_root）。"""
    monkeypatch.setattr(act.activity, "info", lambda: SimpleNamespace(attempt=1))
    monkeypatch.setattr(act, "ensure_audit_session", AsyncMock())
    fake_session = AsyncMock()
    monkeypatch.setattr(
        "supernova_core.audit.session_registry.get_audit_session", lambda: fake_session)
    tool_logger = AsyncMock()
    monkeypatch.setattr(
        "supernova_core.audit.session_tool_audit_logger.SessionToolAuditLogger",
        MagicMock(return_value=tool_logger))
    return fake_session


def _patch_fake_engine(monkeypatch):
    """FakeEngine 记录 cleanup 调用；返回 calls dict。"""
    calls = {}

    class FakeEngine:
        def cleanup_config(self, source_dir, session_id=None):
            calls.setdefault("cleanup_config", []).append((source_dir, session_id))

        def cleanup_processes(self, source_dir=None, session_ids=None):
            calls["cleanup_processes"] = (source_dir, session_ids)
            return {"closed": [], "killed": [], "errors": []}

    monkeypatch.setattr(
        "supernova_core.services.browser_engine.BrowserEngineFactory.get_engine",
        lambda name: FakeEngine(),
    )
    return calls


class TestExploitAgentPerSessionCleanup:
    @pytest.mark.asyncio
    async def test_cleans_own_session_on_success(self, tmp_path, monkeypatch):
        """成功路径：agent 结束 finally 立即回收自己的 session（agent-injection），
        不等扫描级 finally（治死占）。"""
        _patch_activity_env(monkeypatch)
        calls = _patch_fake_engine(monkeypatch)

        fake_executor = MagicMock()
        fake_executor.execute = AsyncMock(
            return_value=AgentMetrics(duration_ms=10, cost_usd=0.0, num_turns=1, model="stub"))
        monkeypatch.setattr(
            "supernova_blackbox.agents.exploit_executor.ExploitExecutor",
            MagicMock(return_value=fake_executor))

        await act.run_exploit_agent(BlackboxActivityInput(
            web_url="https://x.com", repo_path=str(tmp_path), vuln_type="injection",
            deliverables_subdir="deliverables", workspace_path=str(tmp_path / "ws"),
            engine_name="agent-browser"))

        src, sids = calls["cleanup_processes"]
        assert src == str(tmp_path)
        assert sids == ["agent-injection"], "只回收自己的 session，不碰并行 agent"
        assert (str(tmp_path), "agent-injection") in calls["cleanup_config"]

    @pytest.mark.asyncio
    async def test_cleans_own_session_on_failure(self, tmp_path, monkeypatch):
        """失败路径（PentestError → ApplicationFailure）：finally 清理仍触发。
        真机事故形态即此——xss agent 40min 超时撞死，浏览器却死占到扫描收尾。"""
        from temporalio.exceptions import ApplicationError as ApplicationFailure

        from supernova_core.models.errors import PentestError

        _patch_activity_env(monkeypatch)
        calls = _patch_fake_engine(monkeypatch)

        fake_executor = MagicMock()
        fake_executor.execute = AsyncMock(side_effect=PentestError("boom", "agent"))
        monkeypatch.setattr(
            "supernova_blackbox.agents.exploit_executor.ExploitExecutor",
            MagicMock(return_value=fake_executor))

        with pytest.raises(ApplicationFailure):
            await act.run_exploit_agent(BlackboxActivityInput(
                web_url="https://x.com", repo_path=str(tmp_path), vuln_type="xss",
                deliverables_subdir="deliverables", workspace_path=str(tmp_path / "ws"),
                engine_name="agent-browser"))

        _, sids = calls["cleanup_processes"]
        assert sids == ["agent-xss"]

    @pytest.mark.asyncio
    async def test_no_cleanup_without_engine_name(self, tmp_path, monkeypatch):
        """engine_name 缺省（CLI 直跑 / 独立 auth workflow 未透传）→ 不清理，零回归。"""
        _patch_activity_env(monkeypatch)

        get_engine_called = []

        def _fail_get_engine(name):
            get_engine_called.append(name)
            raise AssertionError("engine_name=None 时不应触碰 engine factory")

        monkeypatch.setattr(
            "supernova_core.services.browser_engine.BrowserEngineFactory.get_engine",
            _fail_get_engine)

        fake_executor = MagicMock()
        fake_executor.execute = AsyncMock(
            return_value=AgentMetrics(duration_ms=10, cost_usd=0.0, num_turns=1, model="stub"))
        monkeypatch.setattr(
            "supernova_blackbox.agents.exploit_executor.ExploitExecutor",
            MagicMock(return_value=fake_executor))

        await act.run_exploit_agent(BlackboxActivityInput(
            web_url="https://x.com", repo_path=str(tmp_path), vuln_type="injection",
            deliverables_subdir="deliverables", workspace_path=str(tmp_path / "ws")))

        assert not get_engine_called


class TestAuthValidationAndEndpointVerifyCleanup:
    @pytest.mark.asyncio
    async def test_auth_validation_cleans_agent1_session(self, tmp_path, monkeypatch):
        """validate-auth 结束回收 agent1（BROWSER_SESSION_MAPPING 回落口径）。"""
        _patch_activity_env(monkeypatch)
        calls = _patch_fake_engine(monkeypatch)

        async def _fake_validate(**kwargs):
            return SimpleNamespace(success=True, failure_detail=None)

        # 注意：services/__init__.py re-export 了同名函数，字符串路径会解析到函数上；
        # 经 importlib 拿 sys.modules 里的真模块对象再 patch。
        import importlib

        va_mod = importlib.import_module(
            "supernova_core.services.validate_authentication")
        monkeypatch.setattr(va_mod, "validate_authentication", _fake_validate)

        await act.run_blackbox_auth_validation(BlackboxActivityInput(
            web_url="https://x.com", repo_path=str(tmp_path),
            deliverables_subdir="deliverables", workspace_path=str(tmp_path / "ws"),
            engine_name="agent-browser"))

        _, sids = calls["cleanup_processes"]
        assert sids == ["agent1"]

    @pytest.mark.asyncio
    async def test_endpoint_verify_cleans_default_session(self, tmp_path, monkeypatch):
        """endpoint-verify 结束回收 default（get_session_id 回落口径，
        与 endpoint_verify_executor 注入 prompt 的 id 一致）。"""
        _patch_activity_env(monkeypatch)
        calls = _patch_fake_engine(monkeypatch)

        fake_verifier = MagicMock()
        fake_verifier.execute = AsyncMock(return_value={"cost_usd": 0.0, "duration_ms": 5})
        monkeypatch.setattr(
            "supernova_blackbox.agents.endpoint_verify_executor.EndpointVerifyExecutor",
            MagicMock(return_value=fake_verifier))

        await act.run_endpoint_verify(BlackboxActivityInput(
            web_url="https://x.com", repo_path=str(tmp_path),
            deliverables_subdir="deliverables", workspace_path=str(tmp_path / "ws"),
            engine_name="agent-browser"))

        _, sids = calls["cleanup_processes"]
        assert sids == ["default"]


# ---------------------------------------------------------------------------
# 治本主力：agent-browser 官方 idle 自愈注入（2026-09-03）
# ---------------------------------------------------------------------------


class TestResolveBlackboxEngineIdleTimeout:
    @pytest.mark.asyncio
    async def test_injects_default_idle_timeout(self, tmp_path, monkeypatch):
        """agent-browser 引擎 → setdefault AGENT_BROWSER_IDLE_TIMEOUT_MS=300000（5min，
        对齐云浏览器厂 session TTL 行业默认；官方默认 1h 对扫描死占窗口太长）。"""
        import os

        monkeypatch.delenv("AGENT_BROWSER_IDLE_TIMEOUT_MS", raising=False)
        self._patch_engine_available(monkeypatch, tmp_path)

        name = await act.resolve_blackbox_engine(BlackboxActivityInput(web_url="https://x.com"))

        assert name == "agent-browser"
        assert os.environ["AGENT_BROWSER_IDLE_TIMEOUT_MS"] == "300000"
        monkeypatch.delenv("AGENT_BROWSER_IDLE_TIMEOUT_MS", raising=False)

    @pytest.mark.asyncio
    async def test_explicit_config_not_overridden(self, tmp_path, monkeypatch):
        """部署者显式配置 idle timeout → setdefault 完全尊重，不覆盖。"""
        import os

        monkeypatch.setenv("AGENT_BROWSER_IDLE_TIMEOUT_MS", "600000")
        self._patch_engine_available(monkeypatch, tmp_path)

        await act.resolve_blackbox_engine(BlackboxActivityInput(web_url="https://x.com"))

        assert os.environ["AGENT_BROWSER_IDLE_TIMEOUT_MS"] == "600000"

    @staticmethod
    def _patch_engine_available(monkeypatch, tmp_path):
        """engine 可用 + repo 无 config：走 agent-browser 默认分支。"""

        class FakeEngine:
            name = "agent-browser"

            def check_available(self):
                return True

            def write_config(self, source_dir, session_id=None, proxy_url=None):
                return {"result": "wrote", "configPath": str(tmp_path)}

        monkeypatch.setattr(
            "supernova_core.services.browser_engine.BrowserEngineFactory.get_engine",
            lambda name: FakeEngine(),
        )
