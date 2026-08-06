"""Worker 重启后可观测信号恢复(方案 A)。

背景:可观测信号(AuditSession 写 events.ndjson + HeartbeatManager 写 heartbeat)是进程内
全局单例,由 ``setup_display``(workflow 首个 activity)初始化一次。worker OOM 重启后 temporal
恢复 workflow 时只重新调度**失败/在途的 activity**,**不重跑已 completed 的 setup_display**。
新 worker 进程的 ``_SESSIONS`` 空 -> ``get_audit_session()`` 返 ``NullAuditSession`` ->
所有事件 no-op 静默丢弃、heartbeat 不写 -> live 页失明(非停摆)。

本模块提供幂等恢复:

- ``build_headless_audit_session(input)``:抽 ``setup_display`` 的 AuditSession 构造段(meta +
  configure_logging + Console + initialize + LogBus.attach + set_audit_session + start_heartbeat),
  ``setup_display``(首次)与 ``ensure_audit_session``(重启恢复)共用,避免逻辑重复。
- ``ensure_audit_session(input)``:每个 activity 入口调一次。检测进程内无本 workflow 的
  AuditSession 则按 workflow_id 重建 + 重启 heartbeat,恢复 events.ndjson(append 接旧流)+
  heartbeat 写入。

设计要点:

- **严格守卫**:仅在真实 temporal activity 上下文(``activity.info()`` 不抛 + ``workflow_id`` 为
  非空 str)下才检查重建。CLI 上下文(``activity.info()`` 抛 RuntimeError,CLI 自行 inline session)
  与单测上下文(``activity.info`` 被 patch 成 MagicMock -> workflow_id 非 str,测试自行 set/patch
  session)直接跳过--避免误重建破坏现有单测 + 引入 daemon/LogBus 副作用泄漏。生产 worker 重启后
  重投的 activity 必在真实 activity 上下文(workflow_id 为 str),故恢复路径不受影响。
- **幂等**:非 Null 直接返回(setup_display 已建)。
- **并发安全**:per-workflow_id ``asyncio.Lock`` 串行化重建--worker 重启后 temporal 并发重投多个
  在途 activity(如 3 个 vuln agent),都会同时看到 NullAuditSession;锁保证只首个重建,其余
  double-check 命中跳过,避免并发构造多个 AuditSession/WorkflowLogger/文件句柄孤儿。
- **best-effort**:build 失败(磁盘满/权限等)吞掉 + warning,不阻断 activity--可观测恢复失败 =
  现状(blind),扫描仍跑完、漏洞仍产出(对齐 ``log_info_activity`` 等「显示侧通道失败绝不影响扫描」)。
- **append 接旧流**:events.ndjson 为 append 模式(``StructureduredEventRenderer`` ``aiofiles.open("a")``),
  重建后新事件追加到原文件末尾,接旧流不覆盖。

已知代价(方案 A 取舍,见 plan「取舍」):重建的 session metrics 从 0 累积,跨重启的 cost/total
偏低(重启前 pre-recon/recon 的 metrics 不在新 session 里);同一 agent 重启前后两条 start 事件
(语义上 agent 确实被重试,前端按 agent+attempt 去重展示)。彻底修需 metrics 持久化(方案 B,不做)。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from rich.console import Console

from supernova_core.audit.session import AuditSession
from supernova_core.audit.session_registry import (
    NullAuditSession,
    get_audit_session_for,
    set_audit_session,
)
from supernova_core.logging import configure_logging
from supernova_core.logging.log_bus import LogBus, drain_and_detach  # noqa: F401 (drain_and_detach 供测试/收尾复用)
from supernova_core.models.metrics import SessionMetadata
from supernova_core.runtime.heartbeat import start_heartbeat

logger = logging.getLogger(__name__)

# per-workflow_id 恢复锁。worker 重启后 temporal 并发重投多个在途 activity,都会在入口同时
# 看到 NullAuditSession;锁串行化重建,首个建完后其余 double-check 命中跳过。
_RECOVERY_LOCKS: dict[str, asyncio.Lock] = {}


def _ws_path(input: Any) -> Path:
    """workspace 目录:优先 input.workspace_path(web 路径恒设);否则回落 repo 父/workspaces/<name>。

    对齐 whitebox/blackbox ``setup_display`` 的同款解析。
    """
    if input.workspace_path:
        return Path(input.workspace_path)
    return Path(input.repo_path).parent / "workspaces" / (input.workspace_name or "scan")


async def build_headless_audit_session(input: Any) -> AuditSession:
    """构造 headless AuditSession(worker 容器路径)+ 注册 + 启动 heartbeat。

    ``setup_display``(首次,workflow 首个 activity)与 ``ensure_audit_session``(重启恢复)共用
    本构造逻辑。步骤对齐两轨 ``setup_display``:

      SessionMetadata + configure_logging(幂等) + Console()(非 TTY -> 纯文本) +
      AuditSession.initialize(workflow_id, event_file) + LogBus.attach(dispatcher) +
      set_audit_session + start_heartbeat(幂等)。

    events.ndjson 为 append 模式 -> 重建后新事件追加到原文件末尾,接旧流不覆盖。
    event_file 透传到 WorkflowLogger -> StructuredEventRenderer 写 events.ndjson(web live 页可见)。

    Args:
        input: duck-typed,需 ``workspace_path`` / ``workspace_name`` / ``repo_path`` /
            ``web_url`` / ``event_file``(白盒 ``ActivityInput`` 与黑盒 ``BlackboxActivityInput``
            均具备,故 core helper 不耦合具体 dataclass)。
    """
    ws_path = _ws_path(input)
    meta = SessionMetadata(
        id=input.workspace_name or ws_path.name,
        web_url=input.web_url,
        repo_path=input.repo_path,
        output_path=str(ws_path.parent),
    )
    # worker 容器入口挂 LogBusHandler + per-scan diagnostic.log;幂等(setup.py)。
    configure_logging(log_dir=ws_path / "logs")
    console = Console()  # auto-detects non-TTY in pipes -> plain text per event
    session = AuditSession(meta, use_rich=False, console=console)
    await session.initialize(workflow_id=meta.id, event_file=input.event_file)
    # attach 把散落 getLogger 诊断汇入 dispatcher(起 drain task),否则裸 logger 走 lastResort stderr。
    await LogBus.attach(session.dispatcher)
    set_audit_session(session)
    # heartbeat daemon 线程持续写 <ws>/heartbeat;幂等(同 wf_id+ws_dir 跳过)。
    await start_heartbeat(ws_path)
    return session


async def ensure_audit_session(input: Any) -> None:
    """幂等:进程内无本 workflow 的 AuditSession(worker 重启后)则重建。

    每个 LLM/确定性 activity 入口调一次(在首个 ``get_audit_session()`` 之前)。检测 NullAuditSession
    则按 workflow_id 重建 AuditSession + 重启 heartbeat,恢复 events.ndjson(append 接旧流)+ heartbeat。

    严格守卫(见模块 docstring):仅真实 temporal activity 上下文才重建,CLI/单测上下文跳过。
    并发安全:per-workflow_id 锁 + double-check。best-effort:build 失败吞掉不阻断扫描。
    """
    # ── 严格守卫:仅真实 temporal activity 上下文才考虑重建 ──
    # CLI/纯单测无 temporal 上下文 -> activity.info() 抛 RuntimeError -> 跳过(CLI 自行 inline session)。
    # 单测 patch activity.info=MagicMock(attempt=N) -> workflow_id 为 MagicMock(非 str) -> 跳过
    # (那些测试自行 set/patch session,误重建会引入 daemon/LogBus 副作用泄漏 + 破坏断言)。
    # 生产 worker 重启后重投的 activity 必在真实上下文(workflow_id 为非空 str)。
    try:
        from temporalio import activity

        wf_id = activity.info().workflow_id
    except RuntimeError:
        return
    if not isinstance(wf_id, str) or not wf_id:
        return

    # 快路径:session 已存在(setup_display 已建 / 并发先到者已重建)。
    if not isinstance(get_audit_session_for(wf_id), NullAuditSession):
        return

    # per-wf_id 锁串行化重建:并发重投的多个 activity 同时撞 Null 时只首个重建。
    lock = _RECOVERY_LOCKS.setdefault(wf_id, asyncio.Lock())
    async with lock:
        # double-check:持锁期间并发先到者可能已建完。
        if not isinstance(get_audit_session_for(wf_id), NullAuditSession):
            return
        try:
            await build_headless_audit_session(input)
        except Exception as exc:  # noqa: BLE001 - 可观测恢复 best-effort,不阻断扫描
            logger.warning(
                "ensure_audit_session rebuild failed (wf=%s); scan continues blind: %s",
                wf_id, exc,
            )
