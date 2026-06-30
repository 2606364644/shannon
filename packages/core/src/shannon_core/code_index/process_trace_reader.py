"""Read GitNexus process trace resources → ProcessTrace list.

process trace = GitNexus 索引时预计算的 entry→terminal 调用链路径，通过 MCP
resource ``gitnexus://repo/{name}/process/{label}`` 读取（URI 用 label，不是 id）。
替代旧的「全量 cypher CALLS 边 + Python BFS 重建 chain」——后者慢、readline 崩、
产出空壳。

全量 label 用 cypher ``MATCH (p:Process) RETURN p.label`` 拿（processes resource
截断只给 20）。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# trace 行格式："N: <func> (<filePath>)" —— 见 memory gitnexus-1.6.7-real-machine-behavior
_TRACE_STEP_RE = re.compile(r"^\s*(\d+):\s*(.+?)\s*\(([^)]+)\)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ProcessTrace:
    """一条 process trace = entry→…→terminal 的有序函数序列。"""
    label: str
    steps: list[tuple[int, str, str]]  # (idx, name, file_path)，按 idx 升序
    process_type: str = ""
    step_count: int = 0


def parse_trace_steps(text: str) -> list[tuple[int, str, str]]:
    """解析 trace 文本的步骤行 → [(idx, name, file_path)]，按 idx 升序。"""
    steps = [
        (int(m.group(1)), m.group(2).strip(), m.group(3).strip())
        for m in _TRACE_STEP_RE.finditer(text or "")
    ]
    return sorted(steps, key=lambda s: s[0])


async def read_all_process_traces(mcp_client, repo_name: str) -> list[ProcessTrace]:
    """cypher 拿全 process label → 每 label read_resource → ProcessTrace。

    单条 trace 读失败/空 → log + 跳过（不抛）。repo_name 是 GitNexus registry
    里的仓库名（通常 = 目录名）。
    """
    result = await mcp_client.call_tool(
        "cypher",
        {"query": "MATCH (p:Process) RETURN p.label AS label"},
    )
    rows = result.get("rows", []) if isinstance(result, dict) else []
    labels = [
        r["label"] for r in rows
        if isinstance(r, dict) and r.get("label")
    ]
    logger.info("process_trace_reader: %d process labels from cypher", len(labels))

    traces: list[ProcessTrace] = []
    for label in labels:
        try:
            uri = f"gitnexus://repo/{repo_name}/process/{label}"
            text = await mcp_client.read_resource(uri)
        except Exception as exc:
            logger.warning("process trace read failed for %r: %s", label, exc)
            continue
        steps = parse_trace_steps(text)
        if not steps:
            logger.debug("process trace %r has no parseable steps; skipped", label)
            continue
        traces.append(ProcessTrace(
            label=label, steps=steps, step_count=len(steps),
        ))
    logger.info("process_trace_reader: %d/%d traces parsed", len(traces), len(labels))
    return traces
