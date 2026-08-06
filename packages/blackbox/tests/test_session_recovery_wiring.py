"""worker 重启后可观测恢复:黑盒 activity 入口接线断言。

ensure_audit_session 必须在每个用 get_audit_session() 的 activity 入口被 await。用「spy 抛
sentinel」证明(对齐 whitebox test_session_recovery_wiring)。覆盖 run_exploit_agent +
run_report_agent(LLM)+ finalize_summary(终态)。
"""
import pytest

from supernova_blackbox.pipeline.shared import BlackboxActivityInput


@pytest.mark.asyncio
async def test_run_exploit_agent_calls_ensure_at_entry(tmp_path, monkeypatch):
    """run_exploit_agent 入口须 await ensure_audit_session(input)。"""
    from supernova_blackbox.pipeline import activities as act

    received = []

    async def spy(inp):
        received.append(inp)
        raise RuntimeError("ensure-called")

    monkeypatch.setattr(act, "ensure_audit_session", spy)
    inp = BlackboxActivityInput(
        web_url="https://example.com", vuln_type="injection",
        workspace_path=str(tmp_path))
    with pytest.raises(RuntimeError, match="ensure-called"):
        await act.run_exploit_agent(inp)
    assert received and received[0] is inp


@pytest.mark.asyncio
async def test_run_report_agent_calls_ensure_at_entry(tmp_path, monkeypatch):
    """run_report_agent 入口须 await ensure_audit_session(input)。"""
    from supernova_blackbox.pipeline import activities as act

    received = []

    async def spy(inp):
        received.append(inp)
        raise RuntimeError("ensure-called")

    monkeypatch.setattr(act, "ensure_audit_session", spy)
    inp = BlackboxActivityInput(
        web_url="https://example.com", workspace_path=str(tmp_path))
    with pytest.raises(RuntimeError, match="ensure-called"):
        await act.run_report_agent(inp)
    assert received and received[0] is inp


@pytest.mark.asyncio
async def test_finalize_summary_calls_ensure_at_entry(tmp_path, monkeypatch):
    """finalize_summary(终态)入口接入--worker 重启后需重建 session 写 scan_end 收尾。"""
    from supernova_blackbox.pipeline import activities as act

    received = []

    async def spy(inp):
        received.append(inp)
        raise RuntimeError("ensure-called")

    monkeypatch.setattr(act, "ensure_audit_session", spy)
    inp = BlackboxActivityInput(
        web_url="https://example.com", workspace_path=str(tmp_path))
    with pytest.raises(RuntimeError, match="ensure-called"):
        await act.finalize_summary(inp, {"status": "completed"})
    assert received and received[0] is inp


@pytest.mark.asyncio
async def test_setup_display_uses_build_helper_not_ensure(tmp_path, monkeypatch):
    """setup_display 调 build_headless_audit_session(非 ensure),对齐 whitebox。"""
    from supernova_blackbox.pipeline import activities as act

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
    inp = BlackboxActivityInput(
        web_url="https://example.com", workspace_path=str(tmp_path))
    with pytest.raises(RuntimeError, match="build-called"):
        await act.setup_display(inp)
    assert build_received and build_received[0] is inp
    assert not ensure_received, "setup_display 不应调 ensure(它是 builder)"
