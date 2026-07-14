"""NullAuditSession 必须镜像真实 AuditSession 的 activity 调用面。

回归守卫：当 worker 容器未绑 AuditSession（``get_audit_session()`` 返回 Null）时，
whitebox/blackbox activities 调用 ``log_phase_start(steps=...)`` / ``track_step()`` 等
不得抛异常——恢复 ``session_registry.py`` docstring 承诺的契约：
"every method is a no-op so callers never null-check"。

根因现场（2026-07-14，scan hr_1783997208）：部署的 worker 镜像早于 C1 Phase B，
无 ``setup_display`` activity → AuditSession 从未 ``set_audit_session()`` 绑定 →
``log_phase_start_activity`` 传 ``steps=`` → ``NullAuditSession.log_phase_start(phase)``
不收 ``steps`` → TypeError → temporal 重试 3 次失败 → workflow failed → live 页"已中断"。

镜像范围覆盖真实 ``AuditSession`` 的全部公开方法，使未来签名 drift 在此处即被抓住。
"""
import pytest

from shannon_core.audit.session_registry import NullAuditSession


@pytest.mark.asyncio
async def test_null_session_mirrors_activity_call_surface():
    """activities 在 session 未绑定时调用的全部方法都应 no-op 不崩。"""
    s = NullAuditSession()

    # log_phase_start 带 steps/step_intents —— 直接回归点（曾 TypeError）
    await s.log_phase_start("setup", steps=("a", "b"), step_intents=(None, None))
    await s.log_phase_start("recon")  # 无参仍兼容
    await s.log_phase_complete("setup")

    # log_info（activities 诊断通道）
    await s.log_info("msg")
    await s.log_info("msg", level="warning")

    # track_step —— activities 大量 `async with get_audit_session().track_step(...)`
    async with s.track_step("setup", "preflight", intent="x"):
        pass
    async with s.track_step("setup", "credential-check"):
        pass

    # log_step（track_step 内部 + 直接调用）
    await s.log_step("preflight", "setup", "start")
    await s.log_step("preflight", "setup", "complete", duration_ms=10, error=None)

    # 其余真实 AuditSession 公开方法（防签名 drift 复发）
    await s.log_llm_turn("recon", 1, "content")
    await s.log_tool_call("recon", "Read", {"path": "/x"})
    await s.log_gitnexus_progress("pre-recon", "chunk", 1, 10, 0, detail="d")
    await s.log_workflow_complete({})
    await s.update_session_status("running")
    await s.add_resume_attempt("wf-1", ["terminated-agent"], checkpoint="cp")
    await s.log_error(ValueError("boom"), context="ctx", attempt=1, max_attempts=3)
    await s.log_resume_header({})
    await s.initialize("wf-1", event_file="/tmp/events.ndjson")
    await s.close()

    # get_metrics 在 Null 上返回空 dict（与真实 session 无 tracker 时一致）
    assert await s.get_metrics() == {}


def test_null_session_dispatcher_attribute_exists():
    """setup_display 读 ``session.dispatcher``（LogBus.attach）；Null 上访问不得 AttributeError。"""
    # 返回值不强约束（Null 无真实 dispatcher），仅守"属性存在、可取"
    _ = NullAuditSession().dispatcher
