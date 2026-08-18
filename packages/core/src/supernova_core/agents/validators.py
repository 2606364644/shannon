from pathlib import Path

from supernova_core.models.agents import AgentName, AGENTS
from supernova_core.models.errors import ErrorCode, PentestError

async def validate_deliverable(deliverables_path: Path, agent_name: AgentName) -> bool:
    defn = AGENTS[agent_name]
    if defn.deliverable_filename is None:
        return True
    # tiering（spec 2026-08-18）：黑盒 exploit agent 的 evidence md 落 blackbox/ 桶内
    # （旧结构在根顶层，双路径都认）；白盒 md 在桶顶层（activities 传桶内路径）。
    deliverable_file = deliverables_path / defn.deliverable_filename
    if agent_name.value.endswith("-exploit"):
        from supernova_core.utils.paths import BLACKBOX_SUBDIR
        bb_file = deliverables_path / BLACKBOX_SUBDIR / defn.deliverable_filename
        deliverable_file = bb_file if bb_file.exists() else deliverable_file  # 桶内优先、根顶层兜底
    if not deliverable_file.exists():
        raise PentestError(
            f"Missing deliverable: {defn.deliverable_filename}",
            "validation",
            error_code=ErrorCode.OUTPUT_VALIDATION_FAILED,
            context={"agent_name": agent_name.value, "expected_file": defn.deliverable_filename},
        )

    # 对齐原始 TS createVulnValidator (shannon/apps/worker/src/session-manager.ts:136-146):
    # *-vuln agent 必须落盘 {vt}_exploitation_queue.json —— 它由 executor.py 从
    # result.structured_output 写盘(本函数调用之前)。agent 偶发不走结构化输出通道
    # (GLM 长任务+子代理委派后失忆)时该文件缺失 → 此处捕获 → OUTPUT_VALIDATION_FAILED
    # → classify_error_for_temporal 判 retryable=True(errors.py:126) → Temporal 重跑,
    # 而非静默漏盘(否则 run_merge_dual_track_queues continue + 黑盒 preflight
    # "No whitebox results")。-exploit 不产此文件(TS createExploitValidator 同为 no-op),
    # 故只校验 -vuln。
    if agent_name.value.endswith("-vuln"):
        # tiering（spec 2026-08-18）：queue 落桶内 intermediate/，旧结构平铺兜底
        # （resume 场景 deliverables 已有旧文件时 validator 仍通过）。
        from supernova_core.utils.paths import intermediate_path
        queue_file = intermediate_path(deliverables_path, get_queue_filename(agent_name))
        if not queue_file.exists():
            queue_file = deliverables_path / get_queue_filename(agent_name)
        if not queue_file.exists():
            raise PentestError(
                f"Missing exploitation queue for {agent_name.value}: {queue_file.name} "
                f"(agent produced no structured output — will retry)",
                "validation",
                error_code=ErrorCode.OUTPUT_VALIDATION_FAILED,
                context={"agent_name": agent_name.value, "expected_queue": queue_file.name},
            )
    return True

def get_vuln_type(agent_name: AgentName) -> str | None:
    value = agent_name.value
    if value.endswith("-vuln"):
        return value.replace("-vuln", "")
    if value.endswith("-exploit"):
        return value.replace("-exploit", "")
    return None

def get_queue_filename(agent_name: AgentName) -> str | None:
    vuln_type = get_vuln_type(agent_name)
    if vuln_type:
        return f"{vuln_type}_exploitation_queue.json"
    return None
