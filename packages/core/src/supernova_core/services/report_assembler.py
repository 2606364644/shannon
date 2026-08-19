import json
import re
from pathlib import Path
from typing import Any

from supernova_core.i18n import Messages, current_lang
from supernova_core.utils.file_io import async_path_exists, async_read_file, async_write_file

# 对齐前端报告页解析契约(packages/web/frontend/src/lib/vuln-block.ts 的
# VULN_HEADING_RE = /^### ([A-Z]+)(?:-[A-Z]+)+-(\d+)\b/):统计完全依赖节级标题结构,
# 兼容 -VULN-/-GN- 双轨 ID;小写 chain 节(### llm-chain-N)与非漏洞标题不匹配。
VULN_HEADING_RE = re.compile(r"^### [A-Z]+(?:-[A-Z]+)+-\d+\b", re.MULTILINE)


def count_vuln_headings(markdown: str) -> int:
    """数独立结构化漏洞节数(### TYPE-VULN-NN / ### TYPE-GN-NN)。"""
    return len(VULN_HEADING_RE.findall(markdown))

# 报告渲染标签双语（zh/en 可配，跟随 SUPERNOVA_AGENT_NARRATION_LANG）。
_M = Messages({
    "chain_section_h2": {"zh": "## 攻击链（多步利用路径）",
                         "en": "## Attack Chains (Multi-step Exploitation Paths)"},
    "label_type": {"zh": "- **类型:**", "en": "- **Type:**"},
    "label_severity": {"zh": "- **严重程度:**", "en": "- **Severity:**"},
    "label_confidence": {"zh": "- **置信度:**", "en": "- **Confidence:**"},
    "label_steps": {"zh": "- **步骤:**", "en": "- **Steps:**"},
    "model_label": {"zh": "- **模型:**", "en": "- **Model:**"},
    "exec_summary_h2": {"zh": "## 执行摘要", "en": "## Executive Summary"},
    "assessment_date_label": {"zh": "- 评估日期:", "en": "- Assessment Date:"},
})


class ReportAssembler:
    @staticmethod
    async def _assemble_sections(
        deliverables_path: Path,
        vuln_classes: list[str],
    ) -> list[str]:
        """拼接 per-class deliverables(evidence → findings → analysis 三级回退)为 sections 列表。

        从 assemble 提取的纯读取部分,供 assemble(写盘)与 verify_vuln_block_coverage
        (内存数节,零临时文件)共用。
        """
        sections: list[str] = []
        for vuln_class in vuln_classes:
            evidence = deliverables_path / f"{vuln_class}_exploitation_evidence.md"
            findings = deliverables_path / f"{vuln_class}_findings.md"
            analysis = deliverables_path / f"{vuln_class}_analysis_deliverable.md"
            if await async_path_exists(evidence):
                content = await async_read_file(evidence)
                sections.append(content)
            elif await async_path_exists(findings):
                content = await async_read_file(findings)
                sections.append(content)
            elif await async_path_exists(analysis):
                content = await async_read_file(analysis)
                sections.append(content)
        return sections

    @staticmethod
    async def assemble(
        deliverables_path: Path,
        vuln_classes: list[str],
        report_path: Path,
        report_config: dict[str, Any] | None = None,
    ) -> None:
        sections = await ReportAssembler._assemble_sections(deliverables_path, vuln_classes)
        report_content = "\n\n---\n\n".join(sections)
        await async_write_file(report_path, report_content)

    @staticmethod
    async def verify_vuln_block_coverage(
        deliverables_path: Path,
        vuln_classes: list[str],
        report_path: Path,
    ) -> tuple[int, int]:
        """report-executive 后校验:返回 (报告实际节数, 期望节数)。

        期望数 = 把同一批 per-class deliverables 重新在内存拼接(不写盘)后数节——
        与 agent 覆盖前的底稿同源同口径(天然继承 report_config 过滤结果),幂等。
        ``actual < expected`` 即 agent 压缩/丢失了结构化漏洞节(回归 2026-08-19:
        report agent 自写 cleanup 脚本把正文压成模式汇总+行内 ID 引用,前端
        splitByVulnBlocks 解析 0 节 → 报告页统计全 0、PoC 无卡片可并)。
        调用方据此自愈(重新 assemble 覆盖 agent 版:丢执行摘要、保漏洞数据)。
        """
        sections = await ReportAssembler._assemble_sections(deliverables_path, vuln_classes)
        expected = count_vuln_headings("\n\n---\n\n".join(sections))
        if not await async_path_exists(report_path):
            return 0, expected
        actual = count_vuln_headings(await async_read_file(report_path))
        return actual, expected

    @staticmethod
    async def render_attack_chains(deliverables_path: Path) -> str:
        """Render multi-step attack chains from attack_chains.json as a markdown section.

        Reads the merged dual-track attack_chains.json (produced by
        run_attack_chain_assembly_v2) and renders each chain as a markdown
        sub-section with ordered steps.  Returns empty string when the file is
        missing or contains no chains — callers just append the result.
        """
        # tiering（spec 2026-08-18）：attack_chains.json 是中间产物 -> intermediate/
        # 优先，平铺老结构兜底；None = 缺失 -> 空章节（graceful）。
        from supernova_core.utils.paths import resolve_intermediate
        chains_path = resolve_intermediate(deliverables_path, "attack_chains.json")
        if chains_path is None:
            return ""

        try:
            raw = await async_read_file(chains_path)
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError, ValueError):
            return ""

        chains: list[dict] = data.get("chains", []) or []
        if not chains:
            return ""

        lines: list[str] = [
            "",
            "---",
            "",
            _M.get("chain_section_h2"),
            "",
        ]
        for i, chain in enumerate(chains, start=1):
            cid = chain.get("id", f"chain-{i}")
            name = chain.get("name", cid)
            desc = chain.get("description", "")
            vuln_type = chain.get("vuln_type", "")
            severity = chain.get("severity", "")
            confidence = chain.get("confidence", "")

            lines.append(f"### {cid}: {name}")
            lines.append("")
            if desc:
                lines.append(f"{desc}")
                lines.append("")
            lines.append(f"{_M.get('label_type')} {vuln_type}")
            lines.append(f"{_M.get('label_severity')} {severity}")
            lines.append(f"{_M.get('label_confidence')} {confidence}")
            lines.append(_M.get("label_steps"))
            for step in chain.get("steps", []):
                order = step.get("order", "")
                endpoint = step.get("endpoint", "")
                method = step.get("method", "-")
                sd = step.get("description", "")
                lines.append(f"  {order}. {endpoint} ({method}) — {sd}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    async def inject_model_info(report_path: Path, session_path: Path) -> None:
        if not session_path.exists():
            return

        try:
            session_data = json.loads(session_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        metrics = session_data.get("metrics", {})
        agents = metrics.get("agents", {})
        models: set[str] = set()
        for agent_data in agents.values():
            if isinstance(agent_data, dict):
                model = agent_data.get("model")
                if model:
                    models.add(str(model))

        if not models:
            return

        if not await async_path_exists(report_path):
            return

        model_line = f"{_M.get('model_label')} {', '.join(sorted(models))}"
        content = await async_read_file(report_path)
        lines = content.split("\n")
        new_lines: list[str] = []
        inserted = False

        # 双语锚点：当前 lang 优先，另一 lang 兜底（向后兼容旧报告的中英混排）
        date_anchors = [_M.get("assessment_date_label")]
        exec_anchors = [_M.get("exec_summary_h2")]
        if current_lang() == "zh":
            date_anchors.append("- Assessment Date:")
            exec_anchors.append("## Executive Summary")
        else:
            date_anchors.append("- 评估日期:")
            exec_anchors.append("## 执行摘要")

        for line in lines:
            new_lines.append(line)
            if not inserted and any(a in line for a in date_anchors):
                new_lines.append(model_line)
                inserted = True

        if not inserted:
            for i, line in enumerate(new_lines):
                if any(h in line.strip() for h in exec_anchors):
                    new_lines.insert(i + 1, model_line)
                    inserted = True
                    break

        if inserted:
            await async_write_file(report_path, "\n".join(new_lines))
