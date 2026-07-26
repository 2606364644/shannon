"""P3c 阶段 3 端到端：两 workflow 并发 setup_display 不串台（降级单元集成）。

不起完整 temporalio workflow（避免 hang + mock provider 重），直接调 setup_display
activity（mock activity.info 返不同 workflow_id），断言三单例（AuditSession/LogBus/
heartbeat）按 workflow_id 隔离、events.ndjson 各自归属。验证 Task1-3 contextvar 化
在 setup_display 集成路径下并发不串台——这是阶段 3 的验收点。
"""
from __future__ import annotations

import asyncio
import logging

import pytest


@pytest.fixture(autouse=True)
async def _clean_singletons():
    """清 setup_display 的全局副作用（root LogBusHandler + 三单例注册表）。"""
    yield
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_shannon_configured", False):
            root.removeHandler(h)
            h.close()
    from supernova_core.audit.session_registry import _SESSIONS, _current_wf_id
    from supernova_core.logging.log_bus import _BUSES, drain_and_detach
    from supernova_core.runtime.heartbeat import _HEARTBEATS, stop_heartbeat

    for wf in list(_HEARTBEATS):
        await stop_heartbeat(workflow_id=wf)
    for wf in list(_BUSES):
        await drain_and_detach(workflow_id=wf)
    _current_wf_id.set(None)
    _SESSIONS.clear()
    _HEARTBEATS.clear()
    _BUSES.clear()


async def _setup_display_as(input, wf_id: str) -> None:
    """模拟 setup_display 在 activity(wf_id) 体内执行（设 contextvar _current_wf_id）。

    用 contextvar（非全局 patch activity.info）：并发下全局 patch 会互相覆盖，
    contextvar 经 asyncio.create_task 的 per-task context 天然隔离，对齐真实
    temporalio activity context（per-activity contextvar）。
    """
    from supernova_core.audit.session_registry import _current_wf_id
    from supernova_whitebox.pipeline.activities import setup_display

    _current_wf_id.set(wf_id)
    await setup_display(input)


@pytest.mark.asyncio
async def test_two_workflows_setup_display_no_crosstalk(tmp_path):
    """两 wf 并发 setup_display：AuditSession/LogBus/heartbeat 各自隔离，不串台。"""
    from supernova_whitebox.pipeline.shared import ActivityInput
    from supernova_core.audit.session_registry import (
        get_audit_session_for,
        NullAuditSession,
    )
    from supernova_core.runtime.heartbeat import _HEARTBEATS
    from supernova_core.logging.log_bus import _BUSES

    dirA = tmp_path / "wsA"
    dirB = tmp_path / "wsB"
    dirA.mkdir()
    dirB.mkdir()
    inputA = ActivityInput(
        repo_path=str(dirA),
        workspace_name="wsA",
        workspace_path=str(dirA),
        event_file=str(dirA / "events.ndjson"),
    )
    inputB = ActivityInput(
        repo_path=str(dirB),
        workspace_name="wsB",
        workspace_path=str(dirB),
        event_file=str(dirB / "events.ndjson"),
    )

    # create_task 让每个 setup_display 在独立 context（_current_wf_id per-task 隔离），
    # 对齐真实 temporalio worker per-activity contextvar。
    await asyncio.gather(
        asyncio.create_task(_setup_display_as(inputA, "wf-A")),
        asyncio.create_task(_setup_display_as(inputB, "wf-B")),
    )

    # AuditSession 按 workflow_id 隔离
    sessA = get_audit_session_for("wf-A")
    sessB = get_audit_session_for("wf-B")
    assert not isinstance(sessA, NullAuditSession)
    assert not isinstance(sessB, NullAuditSession)
    assert sessA is not sessB

    # heartbeat 各自 daemon，指向各自 ws_dir
    assert _HEARTBEATS["wf-A"]._ws_dir == dirA
    assert _HEARTBEATS["wf-B"]._ws_dir == dirB

    # LogBus 各自独立实例
    assert _BUSES["wf-A"] is not _BUSES["wf-B"]


@pytest.mark.asyncio
async def test_two_workflows_events_ndjson_separate(tmp_path):
    """两 wf 的 events.ndjson 各自归属：wf-A 的 LogEvent 不写进 wf-B 的 events 文件。"""
    from supernova_whitebox.pipeline.shared import ActivityInput
    from supernova_core.logging.log_bus import _BUSES, drain_and_detach

    dirA = tmp_path / "wsA"
    dirB = tmp_path / "wsB"
    dirA.mkdir()
    dirB.mkdir()
    await _setup_display_as(
        ActivityInput(
            repo_path=str(dirA),
            workspace_name="wsA",
            workspace_path=str(dirA),
            event_file=str(dirA / "events.ndjson"),
        ),
        "wf-A",
    )
    await _setup_display_as(
        ActivityInput(
            repo_path=str(dirB),
            workspace_name="wsB",
            workspace_path=str(dirB),
            event_file=str(dirB / "events.ndjson"),
        ),
        "wf-B",
    )

    # 通过 wf-A 的 bus 发一条事件，确认只进 wf-A 的 events 文件（不串到 B）
    from supernova_core.display.events import LogEvent

    evt = LogEvent(
        timestamp="t",
        category="WARNING",
        logger_name="x",
        level="WARNING",
        message="from-A-only",
        exc_txt=None,
    )
    _BUSES["wf-A"].queue.put_nowait(evt)
    await asyncio.sleep(0.15)  # 等 drain
    await drain_and_detach(workflow_id="wf-A")
    await drain_and_detach(workflow_id="wf-B")

    eventsA = (dirA / "events.ndjson").read_text() if (dirA / "events.ndjson").exists() else ""
    eventsB = (dirB / "events.ndjson").read_text() if (dirB / "events.ndjson").exists() else ""
    assert "from-A-only" in eventsA
    assert "from-A-only" not in eventsB  # 不串台
