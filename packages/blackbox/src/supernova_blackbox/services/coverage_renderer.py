"""渲染 exploit 覆盖率闭环中"未动态验证"条目的 markdown 节。"""

from __future__ import annotations

import logging
from pathlib import Path

from supernova_core.models.queue_schemas import Vulnerability, VulnerabilityQueue
from supernova_core.utils.file_io import (
    async_read_file,
    async_write_file,
)
from supernova_core.utils.paths import (
    BLACKBOX_SUBDIR,
    WHITEBOX_SUBDIR,
    resolve_track_deliverable,
)

from supernova_blackbox.services.exploitation_checker import (
    CoverageResult,
    ExploitationChecker,
)

# vuln_class → 展示字段的 (标签, 属性名) 列表。属性名对齐 queue_schemas 各子类字段。
_CLASS_FIELDS: dict[str, list[tuple[str, str]]] = {
    "auth": [
        ("Source Endpoint", "source_endpoint"),
        ("Missing Defense", "missing_defense"),
        ("Suggested Exploit Technique", "suggested_exploit_technique"),
    ],
    "ssrf": [
        ("Source Endpoint", "source_endpoint"),
        ("Missing Defense", "missing_defense"),
        ("Suggested Exploit Technique", "suggested_exploit_technique"),
    ],
    "authz": [
        ("Endpoint", "endpoint"),
        ("Guard Evidence", "guard_evidence"),
    ],
    "injection": [
        ("Source", "source"),
        ("Path", "path"),
        ("Verdict", "verdict"),
    ],
    "xss": [
        ("Source", "source"),
        ("Path", "path"),
        ("Verdict", "verdict"),
    ],
}

# evidence 已含此标题则不再追加（幂等）。
_UNVERIFIED_HEADING = "## Unverified Findings (Not Dynamically Exploited)"


def render_unverified_section(result: CoverageResult, queue: VulnerabilityQueue) -> str:
    """生成未覆盖条目的 markdown 节。

    字段取自 queue（evidence 里没有这些条目的内容）。
    per-class 字段由 _CLASS_FIELDS 决定；缺失字段优雅跳过。
    """
    by_id: dict[str, Vulnerability] = {v.ID: v for v in queue.vulnerabilities}
    fields = _CLASS_FIELDS.get(result.vuln_class, [])
    lines: list[str] = [
        "",
        "---",
        "",
        _UNVERIFIED_HEADING,
        "",
        f"> 以下 {len(result.uncovered_ids)} 条漏洞经静态分析识别（见 "
        f"`{result.vuln_class}_exploitation_queue.json`），但 exploit 阶段未对其产出"
        "动态验证证据。可能原因：exploit 预算/轮次上限、agent 未穷尽、或条目被合并。"
        "**请人工复核。**",
        "",
    ]
    for vid in sorted(result.uncovered_ids):
        v = by_id.get(vid)
        lines.append(f"### {vid}")
        if v is not None:
            for label, attr in fields:
                val = getattr(v, attr, None)
                if val:
                    lines.append(f"- **{label}:** {val}")
        lines.append("")
    return "\n".join(lines)


logger = logging.getLogger(__name__)


async def close_coverage_gaps(
    deliverables_path: Path,
    vuln_classes: list[str],
) -> list[CoverageResult]:
    """对每类 exploit 计算 coverage，未覆盖则幂等追加 evidence 未覆盖节。

    返回存在未覆盖条目的 CoverageResult 列表（供调用方/测试断言）。
    evidence 或 queue 缺失 → 跳过该类（不报错）。
    """
    uncovered_results: list[CoverageResult] = []
    for vc in vuln_classes:
        queue_path = resolve_track_deliverable(
            deliverables_path, WHITEBOX_SUBDIR, f"{vc}_exploitation_queue.json"
        )
        evidence_path = resolve_track_deliverable(
            deliverables_path, BLACKBOX_SUBDIR, f"{vc}_exploitation_evidence.md"
        )
        result = await ExploitationChecker.check_coverage(queue_path, evidence_path, vc)
        if result is None or not result.uncovered_ids:
            continue
        evidence_text = await async_read_file(evidence_path)
        if _UNVERIFIED_HEADING in evidence_text:
            # 幂等：已含未覆盖节，跳过追加（assemble 重跑安全）
            uncovered_results.append(result)
            continue
        parsed = VulnerabilityQueue.parse_lenient(await async_read_file(queue_path))
        if parsed.warnings:
            logger.warning("queue %s parsed leniently: %s", queue_path.name, parsed.warnings)
        queue = parsed.queue
        section = render_unverified_section(result, queue)
        # 新结构下 evidence_path 指向 blackbox/，写前确保父目录存在；
        # 老结构 evidence_path 在根，mkdir 是无害 no-op。
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        await async_write_file(evidence_path, evidence_text.rstrip("\n") + "\n" + section)
        logger.warning(
            "exploit coverage gap: %s — %d/%d uncovered: %s",
            vc, len(result.uncovered_ids), result.total, sorted(result.uncovered_ids),
        )
        uncovered_results.append(result)
    return uncovered_results
