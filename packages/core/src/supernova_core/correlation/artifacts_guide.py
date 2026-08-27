"""artifacts-guide 生成（spec 2026-08-27 §5.1）——per-edge 扫描产物目录导读。

投喂形态 = 目录导读：列出各子仓扫描产物文件路径 + 作用说明 + 缺失标注，
引导关联 Agent 自己按需读（不塞内容，省 token）。确定性纯函数，编排层在
收集 queue 时顺手探测产物文件集后调用。修 P1：Agent 不再只拿到空的关联
out_dlv，而是各子仓真实产物路径。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ServiceArtifacts:
    """单个服务（子仓）的扫描产物探测结果。"""

    service: str
    role: str
    repo_path: str | None
    deliverables: Path | None
    queue_files: list[Path] = field(default_factory=list)   # {vc}_exploitation_queue.json
    entry_points: Path | None = None                        # entry_points.json（存在才有）
    dismissed: Path | None = None                           # dismissed_findings.json
    proto_roots: list[str] = field(default_factory=list)    # RepoSpec.proto_roots 透传


def _section(a: ServiceArtifacts, heading: str) -> list[str]:
    lines = [f"## {heading} ({a.service} = {a.repo_path or '?'}, role: {a.role}) 扫描产物："]
    ep = str(a.entry_points) if a.entry_points else "（缺失）"
    lines.append(
        f"- {ep} — HTTP 路由表（METHOD /path @ file:line + handler）。"
        f"定位对外入口的首选锚点，须与源码交叉验证。"
        if a.entry_points else
        f"- entry_points.json —（缺失）HTTP 路由表不可用，入口定位须直接读源码。")
    if a.queue_files:
        for q in sorted(a.queue_files):
            lines.append(
                f"- {q} — 已确认漏洞 queue（字段 ID/title/severity/location）。"
                f"flows 引用漏洞时必须优先引用这里的 ID。")
    else:
        lines.append("- {vc}_exploitation_queue.json —（缺失）该仓无可用漏洞 queue。")
    dm = str(a.dismissed) if a.dismissed else None
    if dm:
        lines.append(
            f"- {dm} — 判非漏洞留档（每条含 vuln_class / dismiss_reason / evidence）。"
            f"可用于理解该仓已排除项；其跨仓重审由阶段 B 负责，你不需要翻案。")
    else:
        lines.append(
            "- dismissed_findings.json —（缺失）该仓无非漏洞留档。")
    if a.proto_roots:
        lines.append(f"- proto 提示：proto_roots = {a.proto_roots} ——"
                     f" proto/service 定义优先在这些目录找。")
    return lines


def build_artifacts_guide(from_artifacts: ServiceArtifacts,
                          to_artifacts: ServiceArtifacts) -> str:
    """渲染单条 edge 的 artifacts-guide 文本（from 仓在前，to 仓在后）。"""
    lines = ["<artifacts-guide>"]
    lines += _section(from_artifacts, "from 仓")
    lines += _section(to_artifacts, "to 仓")
    lines.append("</artifacts-guide>")
    return "\n".join(lines)


def build_full_artifacts_guide(artifacts_by_service: dict[str, ServiceArtifacts]) -> str:
    """全仓 guide（阶段 B 裁决批用）：每 service 一节，dict 序即节序。"""
    lines = ["<artifacts-guide>"]
    for a in artifacts_by_service.values():
        lines += _section(a, a.service)
    lines.append("</artifacts-guide>")
    return "\n".join(lines)
