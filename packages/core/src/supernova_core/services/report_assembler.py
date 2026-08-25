import json
import logging
import re
from pathlib import Path
from typing import Any

from supernova_core.code_index.gn_collapse import extract_endpoint
from supernova_core.i18n import Messages, current_lang
from supernova_core.models.queue_schemas import VulnerabilityQueue
from supernova_core.services.findings_renderer import (
    CLASS_CONFIG as _FINDINGS_CLASS_CONFIG,
)
from supernova_core.services.findings_renderer import _M as _FINDINGS_MESSAGES
from supernova_core.services.severity_rules import (
    SEVERITY_ORDER,
    SEVERITY_ZH,
    effective_severity,
)
from supernova_core.utils.file_io import async_path_exists, async_read_file, async_write_file
from supernova_core.utils.paths import resolve_intermediate

logger = logging.getLogger(__name__)

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
    # 漏洞速查表（spec 2026-08-25 §7）——正文第一章，确定性渲染
    "summary_table_h2": {"zh": "## 漏洞速查表",
                         "en": "## Vulnerability Summary Table"},
    "summary_table_header": {
        "zh": "| ID | 漏洞 | 接口 | 参数 | 严重度 | 验证 | 置信度 |",
        "en": ("| ID | Vulnerability | Endpoint | Parameters | Severity | "
               "Verification | Confidence |"),
    },
    "params_join": {"zh": "、", "en": ", "},
    "params_more_suffix": {"zh": " 等 {n} 个", "en": " +{n} more"},
    "no_findings_generic": {"zh": "本类无发现。", "en": "No findings in this class."},
    "verif_static": {"zh": "静态分析", "en": "Static Analysis"},
    "verif_dynamic": {"zh": "已动态验证", "en": "Dynamically Verified"},
    "conf_high": {"zh": "高", "en": "High"},
    "conf_medium": {"zh": "中", "en": "Medium"},
    "conf_low": {"zh": "低", "en": "Low"},
    # 内部置信度标签（merger 单轨分支写 needs_review 等）→ 读者可读文案，
    # 不让流水线内部术语泄漏进报告正文（spec 2026-08-25 §9）。
    "conf_pending_review": {"zh": "待复核", "en": "Pending Review"},
})

# 速查表分隔行（7 列，语言中性）
_SUMMARY_TABLE_SEP = "|---|---|---|---|---|---|---|"
_PLACEHOLDER = "-"

_VERIFICATION_KEYS = {"static_analysis": "verif_static",
                      "dynamically_verified": "verif_dynamic"}
_CONFIDENCE_KEYS = {"high": "conf_high", "medium": "conf_medium", "low": "conf_low"}


def _type_title(vuln) -> str:
    """title 缺省回退：vulnerability_type → 类中文名。

    复用 findings_renderer 的 CLASS_CONFIG heading message key（勿自造映射，
    与类小标题同源）；未知类型显示原值。
    """
    vtype = (getattr(vuln, "vulnerability_type", None) or "").strip().lower()
    cfg = _FINDINGS_CLASS_CONFIG.get(vtype)
    if cfg is not None:
        return _FINDINGS_MESSAGES.get(cfg.heading)
    return vtype or _PLACEHOLDER


def _endpoint_cell(vuln) -> str:
    """接口列：vuln.endpoint（extract 归一化，失败用原值）→ extract_endpoint(path) → '-'。"""
    endpoint = getattr(vuln, "endpoint", None)
    if endpoint:
        # endpoint 可能带 " → file:line" 尾巴（GN 归并产物），归一化成 METHOD /route
        return extract_endpoint(endpoint) or endpoint
    return extract_endpoint(getattr(vuln, "path", None)) or _PLACEHOLDER


def _params_cell(vuln) -> str:
    """参数列：affected_parameters join；>3 个取前 3 + "等 N 个"。"""
    params = [str(p) for p in (getattr(vuln, "affected_parameters", None) or []) if p]
    if not params:
        return _PLACEHOLDER
    if len(params) > 3:
        return (_M.get("params_join").join(params[:3])
                + _M.get("params_more_suffix", n=len(params)))
    return _M.get("params_join").join(params)


def _severity_cell(vuln) -> str:
    """严重度列：effective_severity（含 Task 1 兜底）→ 语言本地化
    （zh 中文档位 / en 首字母大写——F7a：en 报告速查表不再夹中文）。
    对齐 findings_renderer.render_vuln_card 的 sev_disp 双语模式。"""
    severity = effective_severity(vuln)
    if current_lang() == "zh":
        return SEVERITY_ZH.get(severity, severity)
    return severity.capitalize()


def _verification_cell(vuln) -> str:
    """验证列：static_analysis/dynamically_verified 枚举映射中文；缺省静态分析。"""
    verification = (getattr(vuln, "verification", None) or "").strip()
    key = _VERIFICATION_KEYS.get(verification.lower())
    if key:
        return _M.get(key)
    return verification or _M.get("verif_static")


def _confidence_cell(vuln) -> str:
    """置信度列：high/medium/low → 高/中/低；needs_review 及其它未知非空值 →
    待复核（内部标签不进正文——泄漏源=dual_track_merger 单轨分支给条目写
    confidence="needs_review"）；空值显示 '-'。"""
    confidence = (getattr(vuln, "confidence", None) or "").strip().lower()
    key = _CONFIDENCE_KEYS.get(confidence)
    if key:
        return _M.get(key)
    if confidence:
        return _M.get("conf_pending_review")
    return _PLACEHOLDER


def render_summary_table(queues_by_class: dict[str, list]) -> str:
    """漏洞速查表（spec 2026-08-25 §7）：确定性渲染，注入正文第一章。

    每类一小节（``###`` 类标题复用 findings_renderer CLASS_CONFIG heading
    message key——渲染层生成，根治 LLM 手写 ``### Xss``）+ 本类表格
    （ID/漏洞/接口/参数/严重度/验证/置信度），行按 effective severity 降序
    （同档稳定序，保持队列原序）；空类输出一行 none_* 文案。已知类按
    CLASS_CONFIG 配置序输出，不受调用方 dict 序影响。
    """
    lines: list[str] = [_M.get("summary_table_h2")]
    ordered = [c for c in _FINDINGS_CLASS_CONFIG if c in queues_by_class]
    ordered += [c for c in queues_by_class if c not in _FINDINGS_CLASS_CONFIG]
    for vuln_class in ordered:
        cfg = _FINDINGS_CLASS_CONFIG.get(vuln_class)
        heading = (_FINDINGS_MESSAGES.get(cfg.heading) if cfg is not None
                   else str(vuln_class))
        lines.extend(["", f"### {heading}", ""])
        vulns = queues_by_class.get(vuln_class) or []
        if not vulns:
            lines.append(_FINDINGS_MESSAGES.get(cfg.none_found_label)
                         if cfg is not None else _M.get("no_findings_generic"))
            continue
        lines.append(_M.get("summary_table_header"))
        lines.append(_SUMMARY_TABLE_SEP)
        ranked = sorted(vulns, key=lambda v: -SEVERITY_ORDER.get(effective_severity(v), 0))
        for vuln in ranked:
            title = getattr(vuln, "title", None) or _type_title(vuln)
            lines.append(
                f"| {vuln.ID} | {title} | {_endpoint_cell(vuln)} | {_params_cell(vuln)} "
                f"| {_severity_cell(vuln)} | {_verification_cell(vuln)} "
                f"| {_confidence_cell(vuln)} |")
    return "\n".join(lines)


class ReportAssembler:
    @staticmethod
    async def _read_queues_by_class(
        deliverables_path: Path,
        vuln_classes: list[str],
    ) -> dict[str, list]:
        """读全部 vuln queue（intermediate/ 优先 + 平铺兜底，同 findings_renderer 读法）。

        缺 queue 的类直接跳过（不进速查表）；读失败 graceful 跳过该类不致命。
        """
        queues: dict[str, list] = {}
        for vuln_class in vuln_classes:
            cfg = _FINDINGS_CLASS_CONFIG.get(vuln_class)
            if cfg is None:
                continue
            queue_path = resolve_intermediate(deliverables_path, cfg.queue_file)
            if queue_path is None or not await async_path_exists(queue_path):
                continue
            try:
                content = await async_read_file(queue_path)
                parsed = VulnerabilityQueue.parse_lenient(
                    content, vuln_class=vuln_class)
            except Exception as exc:  # noqa: BLE001 — 速查表缺一类不致命
                logger.warning(
                    "summary table: queue %s unreadable: %s", cfg.queue_file, exc)
                continue
            queues[vuln_class] = list(parsed.queue.vulnerabilities)
        return queues

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
        # 漏洞速查表（spec 2026-08-25 §7）：正文第一章——report-executive 之前由
        # 渲染层确定性注入（后续 agent 在其上加执行摘要）。queue 全缺（analysis-only
        # 底稿兜底 / 黑盒 blackbox/ 目录——黑盒 queue 在 whitebox/ 子目录）时不注入，
        # 保持旧输出零回归。速查表行非 ### ID 节，verify_vuln_block_coverage 口径不受影响。
        queues_by_class = await ReportAssembler._read_queues_by_class(
            deliverables_path, vuln_classes)
        if queues_by_class:
            sections.append(render_summary_table(queues_by_class))
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
