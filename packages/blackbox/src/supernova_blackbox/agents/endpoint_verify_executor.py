"""spec 2026-08-03 黑盒端点 live 验证 executor。

黑盒 exploitation 前对白盒端点做 live 验证 + 路由转发前缀智能探测:
- 读白盒所有 {vt}_exploitation_queue.json 端点清单(跨类全集);
- 复用 preflight auth-state(AgentExecutor 基层统一注入 AUTH_STATE_FILE);
- 调端点验证 LLM agent → 产 endpoint_verify.json 到 blackbox/。

功能性失败(agent 崩/超时/无产出) → 不产 endpoint_verify.json,exploit 降级全打(零回归)。
"""
import json
from pathlib import Path
from typing import TYPE_CHECKING

from supernova_core.models.agents import AgentName
from supernova_core.utils.atomic_write import atomic_write_json
from supernova_core.utils.file_io import async_path_exists, async_read_file
from supernova_core.utils.paths import WHITEBOX_SUBDIR, blackbox_dir, resolve_track_deliverable
from supernova_core.services.playwright_config_writer import get_session_id

from supernova_core.agents.executor import AgentExecutor

if TYPE_CHECKING:
    from supernova_core.agents.tool_audit_logger import ToolAuditLogger
    from supernova_core.logging.activity_logger import ActivityLogger


ENDPOINT_VERIFY_FILENAME = "endpoint_verify.json"

# spec 5.3 schema:endpoint_key(归一化 "METHOD /path",白盒源码路径) → verdict。
# additionalProperties 因 endpoint_key 动态;resolved_path 可选(not_live 时无)。
ENDPOINT_VERIFY_SCHEMA = {
    "type": "object",
    "additionalProperties": {
        "type": "object",
        "properties": {
            "live_status": {"type": "string", "enum": ["live", "not_live", "param_invalid"]},
            "resolved_path": {"type": "string"},
            "source_path": {"type": "string"},
            "evidence": {"type": "string"},
        },
        "required": ["live_status", "source_path", "evidence"],
    },
}


class EndpointVerifyExecutor:
    def __init__(self, agent_executor: AgentExecutor):
        self._executor = agent_executor

    async def _collect_endpoint_manifest(
        self, deliverables_path: Path, vuln_classes: list[str]
    ) -> str:
        """读白盒所有 {vt}_exploitation_queue.json,合并成端点清单(json 字符串)。

        endpoint_verify agent 跨类验证(端点全集),故读所有 vuln class 的 queue 而非单类
        (区别于 ExploitExecutor 只读单类 queue)。缺失 queue 跳过;全部缺失 → 空串
        (execute 据此降级,不跑验证 agent)。
        """
        manifest: dict[str, dict] = {}
        for vt in vuln_classes:
            queue_path = resolve_track_deliverable(
                deliverables_path, WHITEBOX_SUBDIR, f"{vt}_exploitation_queue.json"
            )
            if await async_path_exists(queue_path):
                manifest[vt] = json.loads(await async_read_file(queue_path))
        return json.dumps(manifest, ensure_ascii=False) if manifest else ""

    async def execute(
        self,
        deliverables_path: Path,
        workspace_path: Path,
        web_url: str,
        vuln_classes: list[str],
        config_path: str | None = None,
        api_key: str | None = None,
        pipeline_testing: bool = False,
        audit_logger: "ActivityLogger | None" = None,
        tool_audit_logger: "ToolAuditLogger | None" = None,
    ) -> dict:
        """跑端点 live 验证 agent,产 endpoint_verify.json 到 blackbox/。

        返回 {endpoint_verify: path|None, ...}:path=验证产出落盘;None=降级
        (无端点 / agent 无 structured_output),exploit 据此照打(零回归)。
        workspace_path 保留对称签名(auth-state 由 AgentExecutor 基层按 deliverables.parent
        注入,本方法不直接用)。
        """
        manifest = await self._collect_endpoint_manifest(deliverables_path, vuln_classes)
        if not manifest:
            return {"endpoint_verify": None, "reason": "no whitebox endpoints"}
        metrics = await self._executor.execute(
            agent_name=AgentName.ENDPOINT_VERIFY,
            repo_path=str(deliverables_path),
            deliverables_path=str(deliverables_path),
            web_url=web_url,
            config_path=config_path,
            api_key=api_key,
            pipeline_testing=pipeline_testing,
            prompt_variables={
                "endpoints_manifest": manifest,
                "browser_session_id": get_session_id(AgentName.ENDPOINT_VERIFY.value),
            },
            structured_output_schema=ENDPOINT_VERIFY_SCHEMA,
            skip_artifact_postprocess=True,  # activity 自落盘 blackbox/,非顶层 queue
            audit_logger=audit_logger,
            tool_audit_logger=tool_audit_logger,
        )
        if not metrics.structured_output:
            return {"endpoint_verify": None, "reason": "agent produced no structured output"}
        out_path = blackbox_dir(deliverables_path) / ENDPOINT_VERIFY_FILENAME
        out_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(out_path, metrics.structured_output)
        return {
            "endpoint_verify": str(out_path),
            "verified_count": len(metrics.structured_output),
            "duration_ms": metrics.duration_ms,
            "cost_usd": metrics.cost_usd,
            "cost_currency": metrics.cost_currency,
        }
