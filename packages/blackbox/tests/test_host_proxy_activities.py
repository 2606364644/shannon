"""Task 7: host_proxy setup/cleanup activities + preflight 用映射 + exploit 传 proxy。

per-scan 本地代理（host_profile Phase 2）：
- ``run_host_proxy_setup``：consumes ``BlackboxActivityInput.host_mappings``，
  无映射返回 ``""``（向后兼容），有映射起 ``start_host_proxy`` 返 proxy_url；
  PentestError/Exception → ``ApplicationFailure``（fail-fast）。
- ``stop_host_proxy``：best-effort 停（绝不 raise）。
- ``run_blackbox_preflight``：``validate_target_url`` 透传 host_mappings。
- exploit / endpoint_verify / report activity 透传 ``input.proxy_url``。
- ``write_engine_config_for_session`` 透传 ``proxy_url`` 给 ``engine.write_config``。

GAP 1（wrapper executors）：exploit / endpoint_verify activity 调的是
``ExploitExecutor.execute`` / ``EndpointVerifyExecutor.execute``（WRAPPER），
非 ``AgentExecutor.execute`` 直调——wrapper 须 forward proxy_url 到内部
``self._executor.execute(..., proxy_url=...)``，否则 proxy_url 断在 wrapper 层。
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from temporalio.exceptions import ApplicationError

from supernova_core.models.agents import AgentName
from supernova_core.models.metrics import AgentMetrics
from supernova_blackbox.pipeline import activities
from supernova_blackbox.agents.exploit_executor import ExploitExecutor
from supernova_blackbox.agents.endpoint_verify_executor import EndpointVerifyExecutor


# ─── run_host_proxy_setup ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_setup_no_mappings_returns_empty():
    """无 host_mappings → 不起代理，返回 ''（向后兼容）。"""
    inp = MagicMock()
    inp.host_mappings = {}
    with patch("supernova_blackbox.pipeline.activities.start_host_proxy") as m:
        result = await activities.run_host_proxy_setup(inp)
        assert result == ""
        m.assert_not_called()


@pytest.mark.asyncio
async def test_setup_starts_proxy_returns_url():
    """有 mappings → 起代理，注册 handle，返回 proxy_url。"""
    inp = MagicMock()
    inp.host_mappings = {"x.test": "10.0.0.1"}
    fake_handle = MagicMock(proxy_url="http://127.0.0.1:9090", port=9090)
    with patch("supernova_blackbox.pipeline.activities.start_host_proxy",
               AsyncMock(return_value=fake_handle)), \
         patch.dict("supernova_blackbox.pipeline.activities._PROXY_HANDLES", {}, clear=True):
        result = await activities.run_host_proxy_setup(inp)
        assert result == "http://127.0.0.1:9090"
        # handle 已注册到模块级 registry，供 stop_host_proxy 清理
        assert "http://127.0.0.1:9090" in activities._PROXY_HANDLES


@pytest.mark.asyncio
async def test_setup_fail_fast_on_error():
    """代理起不来 → ApplicationFailure（扫描 fail-fast；不静默继续）。"""
    from supernova_core.models.errors import PentestError
    inp = MagicMock()
    inp.host_mappings = {"x.test": "10.0.0.1"}
    with patch("supernova_blackbox.pipeline.activities.start_host_proxy",
               AsyncMock(side_effect=PentestError("nope", category="preflight"))):
        with pytest.raises(ApplicationError):
            await activities.run_host_proxy_setup(inp)


@pytest.mark.asyncio
async def test_setup_fail_fast_on_generic_exception():
    """非 PentestError 异常也包装成 ApplicationFailure（fail-fast 不漏）。"""
    inp = MagicMock()
    inp.host_mappings = {"x.test": "10.0.0.1"}
    with patch("supernova_blackbox.pipeline.activities.start_host_proxy",
               AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(ApplicationError):
            await activities.run_host_proxy_setup(inp)


# ─── stop_host_proxy ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_proxy_empty_url_noop():
    """空 url → 直接 return（不动 registry，不调 stop）。"""
    with patch("supernova_blackbox.pipeline.activities.stop_host_proxy_func") as m:
        await activities.stop_host_proxy("")
        m.assert_not_called()


@pytest.mark.asyncio
async def test_stop_proxy_unknown_url_noop():
    """url 不在 registry → 不调 stop_host_proxy_func（无 handle 可清）。"""
    with patch("supernova_blackbox.pipeline.activities.stop_host_proxy_func") as m, \
         patch.dict("supernova_blackbox.pipeline.activities._PROXY_HANDLES", {}, clear=True):
        await activities.stop_host_proxy("http://127.0.0.1:9090")
        m.assert_not_called()


@pytest.mark.asyncio
async def test_stop_proxy_best_effort_no_raise():
    """GAP 2：stop 永不 raise（cleanup best-effort）。

    必须 SEED _PROXY_HANDLES 让 handle 被找到，否则 stop_host_proxy_func
    永不调用——patched exception 不触发，测试空转无意义。SEED 后路径真跑，
    内部 swallow 了 stop_host_proxy_func 的 Exception。
    """
    fake_handle = MagicMock()
    mock_stop = AsyncMock(side_effect=Exception("boom"))
    with patch("supernova_blackbox.pipeline.activities.stop_host_proxy_func", mock_stop), \
         patch.dict("supernova_blackbox.pipeline.activities._PROXY_HANDLES",
                    {"http://127.0.0.1:9090": fake_handle}, clear=True):
        # 不 raise —— 内部 swallow
        await activities.stop_host_proxy("http://127.0.0.1:9090")
        # 路径真跑了（不是空转）：stop_host_proxy_func 被 await + handle 被 pop
        mock_stop.assert_awaited_once_with(fake_handle)
        assert "http://127.0.0.1:9090" not in activities._PROXY_HANDLES


# ─── GAP 1: wrapper executors forward proxy_url ──────────────────────────────

@pytest.mark.asyncio
async def test_exploit_executor_forwards_proxy_url(tmp_path):
    """GAP 1：ExploitExecutor.execute 接 proxy_url 并 forward 到 AgentExecutor.execute。

    exploit activity 不直调 AgentExecutor——经此 wrapper。不 forward 就断在 wrapper。
    """
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    mock_executor = AsyncMock()
    mock_executor.execute.return_value = AgentMetrics(duration_ms=1, cost_usd=0.0, num_turns=1)
    exploit = ExploitExecutor(mock_executor)
    await exploit.execute(
        agent_name=AgentName.INJECTION_EXPLOIT,
        vuln_type="injection",
        workspace_path=tmp_path,
        deliverables_path=deliverables,
        web_url="https://example.com",
        proxy_url="http://127.0.0.1:9090",
    )
    assert mock_executor.execute.call_args.kwargs["proxy_url"] == "http://127.0.0.1:9090"


@pytest.mark.asyncio
async def test_exploit_executor_proxy_url_defaults_none(tmp_path):
    """proxy_url 默认 None → forward None（backward compat）。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    mock_executor = AsyncMock()
    mock_executor.execute.return_value = AgentMetrics(duration_ms=1, cost_usd=0.0, num_turns=1)
    exploit = ExploitExecutor(mock_executor)
    await exploit.execute(
        agent_name=AgentName.INJECTION_EXPLOIT,
        vuln_type="injection",
        workspace_path=tmp_path,
        deliverables_path=deliverables,
        web_url="https://example.com",
    )
    assert mock_executor.execute.call_args.kwargs["proxy_url"] is None


@pytest.mark.asyncio
async def test_endpoint_verify_executor_forwards_proxy_url(tmp_path):
    """GAP 1：EndpointVerifyExecutor.execute 接 proxy_url 并 forward 到 AgentExecutor.execute。

    endpoint_verify activity 不直调 AgentExecutor——经此 wrapper。不 forward 就断在 wrapper。
    """
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    mock_executor = AsyncMock()
    mock_executor.execute.return_value = AgentMetrics(duration_ms=1, cost_usd=0.0, num_turns=1)
    verifier = EndpointVerifyExecutor(mock_executor)
    # 无白盒 queue → 早返；但 proxy_url 仍应在到达 AgentExecutor.execute 之前不需要被 forward。
    # 这里 seed 一个 queue 让它真调到 AgentExecutor.execute。
    (deliverables / "whitebox").mkdir()
    (deliverables / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities": [{"ID": "INJ-1", "source_endpoint": "/api/x"}]}'
    )
    await verifier.execute(
        deliverables_path=deliverables,
        workspace_path=tmp_path,
        web_url="https://example.com",
        vuln_classes=["injection"],
        proxy_url="http://127.0.0.1:9090",
    )
    assert mock_executor.execute.call_args.kwargs["proxy_url"] == "http://127.0.0.1:9090"


@pytest.mark.asyncio
async def test_endpoint_verify_executor_proxy_url_defaults_none(tmp_path):
    """proxy_url 默认 None → forward None（backward compat）。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    mock_executor = AsyncMock()
    mock_executor.execute.return_value = AgentMetrics(duration_ms=1, cost_usd=0.0, num_turns=1)
    verifier = EndpointVerifyExecutor(mock_executor)
    (deliverables / "whitebox").mkdir()
    (deliverables / "whitebox" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities": [{"ID": "INJ-1", "source_endpoint": "/api/x"}]}'
    )
    await verifier.execute(
        deliverables_path=deliverables,
        workspace_path=tmp_path,
        web_url="https://example.com",
        vuln_classes=["injection"],
    )
    assert mock_executor.execute.call_args.kwargs["proxy_url"] is None


# ─── preflight 透传 host_mappings ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preflight_passes_host_mappings_to_validate_target_url():
    """run_blackbox_preflight 把 input.host_mappings 透传给 validate_target_url。"""
    inp = MagicMock()
    inp.web_url = "https://x.test"
    inp.config_path = None
    inp.host_mappings = {"x.test": "10.0.0.1"}
    with patch("supernova_blackbox.pipeline.activities.validate_target_url",
               return_value="10.0.0.1") as v_url, \
         patch("supernova_blackbox.pipeline.activities.check_url_reachable",
               AsyncMock(return_value=True)):
        await activities.run_blackbox_preflight(inp)
        _, kwargs = v_url.call_args
        assert kwargs.get("host_mappings") == {"x.test": "10.0.0.1"}


# ─── write_engine_config_for_session 透传 proxy_url ──────────────────────────

@pytest.mark.asyncio
async def test_write_engine_config_forwards_proxy_url(tmp_path):
    """write_engine_config_for_session 把 proxy_url 透传给 engine.write_config。

    BrowserEngineFactory 是函数内 import（非模块顶），故 patch 源模块。
    """
    fake_engine = MagicMock()
    with patch("supernova_core.services.browser_engine.BrowserEngineFactory") as factory:
        factory.get_engine.return_value = fake_engine
        await activities.write_engine_config_for_session(
            repo_path=str(tmp_path),
            session_id="sess-1",
            engine_name="playwright",
            proxy_url="http://127.0.0.1:9090",
        )
        _, kwargs = fake_engine.write_config.call_args
        assert kwargs.get("proxy_url") == "http://127.0.0.1:9090"


@pytest.mark.asyncio
async def test_write_engine_config_proxy_url_defaults_none(tmp_path):
    """无 proxy_url（旧调用方）→ write_config 拿到 None（backward compat）。"""
    fake_engine = MagicMock()
    with patch("supernova_core.services.browser_engine.BrowserEngineFactory") as factory:
        factory.get_engine.return_value = fake_engine
        # 旧签名（3 参）仍可调——proxy_url 默认 None
        await activities.write_engine_config_for_session(
            repo_path=str(tmp_path),
            session_id="sess-1",
            engine_name="playwright",
        )
        _, kwargs = fake_engine.write_config.call_args
        assert kwargs.get("proxy_url") is None
