"""worker 重启后可观测恢复:白盒 activity 入口接线断言。

ensure_audit_session 必须在每个用 get_audit_session() 的 activity 入口(首个 get_audit_session
之前)被 await,且传入该 activity 的 input。用「spy 抛 sentinel」证明:spy 一抛即从 activity
propagate 出来,反证 ensure 在 activity 任何其它逻辑之前被调(无需 mock 整个 activity 依赖)。

覆盖代表性 activity:run_agent(LLM,含 run_vuln_agent/run_attack_chain_llm_agent 委派)+
run_merge_dual_track_queues(确定性,代表 code_index/merge/risk-scoring 等同构接线)。
"""
import pytest
from unittest.mock import MagicMock

from supernova_whitebox.pipeline.shared import ActivityInput


@pytest.mark.asyncio
async def test_run_agent_calls_ensure_audit_session_at_entry(tmp_path, monkeypatch):
    """run_agent 入口须 await ensure_audit_session(input)(在任何 get_audit_session 之前)。"""
    from supernova_whitebox.pipeline import activities as act

    received = []

    async def spy(inp):
        received.append(inp)
        raise RuntimeError("ensure-called")  # sentinel:一调即抛,反证在入口

    monkeypatch.setattr(act, "ensure_audit_session", spy)
    inp = ActivityInput(repo_path=str(tmp_path), workspace_name="recon")
    with pytest.raises(RuntimeError, match="ensure-called"):
        await act.run_agent(inp)
    assert received and received[0] is inp, "ensure_audit_session 须以 activity 的 input 为参"


@pytest.mark.asyncio
async def test_run_merge_dual_track_queues_calls_ensure_at_entry(tmp_path, monkeypatch):
    """确定性 activity(run_merge_dual_track_queues 代表)入口同样接入 ensure。"""
    from supernova_whitebox.pipeline import activities as act

    received = []

    async def spy(inp):
        received.append(inp)
        raise RuntimeError("ensure-called")

    monkeypatch.setattr(act, "ensure_audit_session", spy)
    inp = ActivityInput(repo_path=str(tmp_path))
    with pytest.raises(RuntimeError, match="ensure-called"):
        await act.run_merge_dual_track_queues(inp)
    assert received and received[0] is inp


@pytest.mark.asyncio
async def test_finalize_summary_calls_ensure_at_entry(tmp_path, monkeypatch):
    """finalize_summary(终态 activity,写 scan_end)入口接入--worker 重启后若 workflow 推进到
    finalize,需重建 session 才能写 scan_end,否则 live 页不收尾。"""
    from supernova_whitebox.pipeline import activities as act

    received = []

    async def spy(inp):
        received.append(inp)
        raise RuntimeError("ensure-called")

    monkeypatch.setattr(act, "ensure_audit_session", spy)
    inp = ActivityInput(repo_path=str(tmp_path))
    with pytest.raises(RuntimeError, match="ensure-called"):
        await act.finalize_summary(inp, {"status": "completed"})
    assert received and received[0] is inp


@pytest.mark.asyncio
async def test_setup_display_uses_build_helper_not_ensure(tmp_path, monkeypatch):
    """setup_display 是首次建 session 的入口,应调 build_headless_audit_session(非 ensure)。
    守 setup_display 不误接 ensure(ensure 的快路径虽会跳过,但语义上 setup_display 是 builder)。"""
    from supernova_whitebox.pipeline import activities as act

    build_received = []
    ensure_received = []

    async def build_spy(inp):
        build_received.append(inp)
        raise RuntimeError("build-called")

    async def ensure_spy(inp):
        ensure_received.append(inp)
        raise RuntimeError("ensure-called")

    monkeypatch.setattr(act, "build_headless_audit_session", build_spy)
    monkeypatch.setattr(act, "ensure_audit_session", ensure_spy)
    inp = ActivityInput(repo_path=str(tmp_path), workspace_name="ws")
    with pytest.raises(RuntimeError, match="build-called"):
        await act.setup_display(inp)
    assert build_received and build_received[0] is inp
    assert not ensure_received, "setup_display 不应调 ensure(它是 builder,非恢复 guard)"
