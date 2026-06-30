"""GitNexus impact 定向可达性补充（spec §4.5，决策 2）。

impact 提供 upstream/downstream 的 byDepth 分层可达闭包 + risk + affected_processes，
用于 sink/source 消歧、可达性确认、risk 标注。**不产出 chain.path**（path 只来自
process trace）——这是纯补充层。

Go 仓纯 name ambiguous 率极高，**必带 file_path** 消歧（见 memory
gitnexus-1.6.7-real-machine-behavior）。失败/超时/None → {}（best-effort）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def _impact(mcp_client, name: str, file_path: str, direction: str) -> dict:
    try:
        result = await mcp_client.call_tool("impact", {
            "target": name,
            "file_path": file_path,
            "direction": direction,
        })
    except Exception as exc:
        logger.warning("impact %s %s (%s) failed: %s", direction, name, file_path, exc)
        return {}
    if not isinstance(result, dict):
        return {}
    return {
        "byDepth": result.get("byDepth", {}) or {},
        "risk": result.get("risk"),
        "affected_processes": result.get("affected_processes", []) or [],
    }


async def impact_upstream(mcp_client, name: str, file_path: str) -> dict:
    """谁依赖 name（caller→name 方向）。不产 path。"""
    return await _impact(mcp_client, name, file_path, "upstream")


async def impact_downstream(mcp_client, name: str, file_path: str) -> dict:
    """name 依赖谁（name→callee 方向）。不产 path。"""
    return await _impact(mcp_client, name, file_path, "downstream")
