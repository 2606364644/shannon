"""组合扫描融合报告渲染器（2026-08-18 重做：黑盒报告为主体 + 白盒字段融入）。

理念：融合报告基于**黑盒实测验证过**的漏洞——黑盒正式报告
``comprehensive_security_assessment_report.md`` 原文作主体，补充白盒报告的
脆弱代码位置与缺失防护，让报告更完善：

- **主体**：黑盒报告正文一字不动（仅 H1 换成融合报告标题）。
- **卡片级融入**：``### {ID}:`` 漏洞卡标题后插两行白盒结构化字段（按 ID 匹配；
  黑盒接力读白盒 queue，两边 ID 同源）：
  - ``- **脆弱代码位置（白盒）:**`` ← ``vulnerable_code_location`` /
    ``sink_function`` / ``path`` / ``source`` / ``source_endpoint`` / ``endpoint``
    （按优先级取首个非空；inj/xss 无 location 字段时回落 sink）。
  - ``- **缺失防护（白盒）:**`` ← ``missing_defense`` / ``guard_evidence``
    （authz）/ ``sanitization_observed``（inj）/ ``encoding_observed``（xss）。
- **头部**：组合摘要表前置（5 类含 auth；黑盒列只数 exploited——与
  ``workspace.get_workspace_vuln_counts`` 同口径）。
- **尾部附录**：「白盒独有发现（黑盒未实证）」= 白盒 queue 有、黑盒报告无卡
  的条目，逐条标注黑盒 verdict 状态（无 verdict → 未尝试）——不丢白盒召回。
- **占位过滤**：ID 含 PLACEHOLDER 的脏条目不渲染、不计数（真实 run 见
  AUTHULN_PLACEHOLDER「占位：请忽略本条」泄漏进报告）。
- **韧性降级**：黑盒报告缺失 / 空 / 不可读 → 回退旧机械交叉表（按类详述
  bullet），摘要表仍产出。

双轨读不同产物（与 ``workspace.get_workspace_vuln_counts`` 同口径）：
- 白盒：``{vt}_exploitation_queue.json``，count = ``len(vulnerabilities)``。
- 黑盒：``{vt}_exploit_verdicts.json``，count = ``verdicts`` 中 ``status=="exploited"``
  的条目数（成功 exploit 数；见 ``workspace.py:127-160``、``executor.py:221``、
  ``exploitation_checker.py:222``）。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from supernova_core.i18n import Messages
from supernova_core.utils.paths import resolve_intermediate

logger = logging.getLogger(__name__)

# 交叉范围 5 类，含 auth（2026-08-18 重做纳入）：黑盒接力虽以 taint 四类为主，
# 但 auth queue 同样被黑盒验证（真实 run 产 auth_exploit_verdicts.json + 报告
# auth 卡片），旧版排除 auth 会让整批 auth 发现从融合报告消失。
_VULN_CLASSES: tuple[str, ...] = ("injection", "xss", "ssrf", "authz", "auth")

# 黑盒 verdict status 枚举（见 collectors/exploit.py::_SINGLE_VERDICT_SCHEMA），
# status=="exploited" 计为「黑盒验证」（成功 exploit），其余为尝试但未利用。
_VERDICT_EXPLOITED = "exploited"

_COMBINED_REPORT_FILENAME = "combined_report.md"

# 黑盒正式报告（白盒同名，各自落各轨 deliverables 根；见
# models/deliverables.py::DeliverableType.REPORT）。
_REPORT_FILENAME = "comprehensive_security_assessment_report.md"

# 占位脏条目标记（ID 含此串即过滤，大小写不敏感）。
_PLACEHOLDER_MARKER = "PLACEHOLDER"

# 白盒字段优先级：脆弱代码位置（auth/ssrf/authz 有专用 location 字段，
# inj/xss 回落 sink/path/source）。
_LOCATION_FIELDS: tuple[str, ...] = (
    "vulnerable_code_location", "sink_function", "path",
    "source", "source_endpoint", "endpoint",
)

# 白盒字段优先级：缺失防护（auth/ssrf=missing_defense、authz=guard_evidence、
# injection=sanitization_observed、xss=encoding_observed）。
_DEFENSE_FIELDS: tuple[str, ...] = (
    "missing_defense", "guard_evidence",
    "sanitization_observed", "encoding_observed",
)

# 黑盒报告漏洞卡标题：``### {ID}: 标题``（ID 内无冒号/空白；不匹配 #### /
# 无冒号的普通 ### 小节标题，如 ``### Injection``、``### llm-chain-1:`` 仅当
# ID 命中白盒 map 才注入）。
_CARD_HEADING_RE = re.compile(r"^### ([^:\s]+):")

# 融合报告文案双语（zh/en 可配，跟随 SUPERNOVA_AGENT_NARRATION_LANG）。
_M = Messages({
    "title": {"zh": "# 组合扫描融合报告", "en": "# Combined Scan Fused Report"},
    "fusion_note": {
        "zh": "> 本报告以黑盒实测验证报告为主体，融入白盒分析的脆弱代码位置与"
              "缺失防护（标注「（白盒）」的字段）；尾部附录列出白盒独有、黑盒未"
              "实证的发现。",
        "en": "> This report is based on the blackbox-verified report, enriched "
              "with whitebox vulnerable code locations and missing defenses "
              "(fields marked \"(whitebox)\"); the appendix lists whitebox-only "
              "findings not verified by blackbox.",
    },
    "wb_loc_line": {"zh": "- **脆弱代码位置（白盒）:** {value}",
                    "en": "- **Vulnerable code location (whitebox):** {value}"},
    "wb_def_line": {"zh": "- **缺失防护（白盒）:** {value}",
                    "en": "- **Missing defense (whitebox):** {value}"},
    "appendix_h2": {"zh": "## 白盒独有发现（黑盒未实证）",
                    "en": "## Whitebox-Only Findings (Not Verified by Blackbox)"},
    "appendix_note": {
        "zh": "> 以下发现来自白盒代码分析，黑盒实测未产出对应漏洞卡（状态来自"
              "黑盒 exploit verdict；「未尝试」= 黑盒未验证）。",
        "en": "> Findings from whitebox code analysis with no corresponding "
              "blackbox report card (status from blackbox exploit verdicts; "
              "\"not attempted\" = not verified by blackbox).",
    },
    "not_attempted": {"zh": "[未尝试]", "en": "[not attempted]"},
    "wb_only_bullet": {"zh": "- **{id}** {tag} {extra}",
                       "en": "- **{id}** {tag} {extra}"},
    # ── 摘要表（两路径共用）────────────────────────────────────────────
    "summary_h2": {"zh": "## 组合摘要", "en": "## Summary"},
    "col_class": {"zh": "漏洞类", "en": "Vulnerability Class"},
    "col_wb": {"zh": "白盒发现", "en": "Whitebox Found"},
    "col_bb": {"zh": "黑盒验证", "en": "Blackbox Verified"},
    "col_cross": {"zh": "双轨交叉", "en": "Cross-Track"},
    "yes": {"zh": "是", "en": "yes"},
    "no": {"zh": "否", "en": "no"},
    # ── 降级路径（黑盒报告缺失时的旧机械交叉表）───────────────────────
    "detail_h2": {"zh": "## 按漏洞类详述", "en": "## Per-Class Detail"},
    "wb_view_h4": {"zh": "#### 白盒视角（代码证据）",
                   "en": "#### Whitebox View (Code Evidence)"},
    "bb_view_h4": {"zh": "#### 黑盒视角（利用验证）",
                   "en": "#### Blackbox View (Exploit Verification)"},
    "no_findings": {"zh": "_无发现_", "en": "_no findings_"},
    "wb_entry_bullet": {"zh": "- **{id}** {extra}", "en": "- **{id}** {extra}"},
    "bb_entry_bullet": {"zh": "- **{id}** {extra}", "en": "- **{id}** {extra}"},
    "generated_note": {
        "zh": "> 本报告由组合扫描自动生成，交叉白盒（代码分析）与黑盒（利用验证）"
              "发现（黑盒正式报告缺失，降级为机械交叉表）。",
        "en": "> Generated by the combined scan; cross-references whitebox (code "
              "analysis) and blackbox (exploit verification) findings "
              "(blackbox report missing; degraded to mechanical cross-table).",
    },
})


def _vid(vuln: dict) -> str:
    """取白盒 queue 条目 ID（ID / id 双键，韧性）。"""
    return str(vuln.get("ID") or vuln.get("id") or "").strip()


def _is_placeholder(vuln: dict) -> bool:
    """占位脏条目（ID 含 PLACEHOLDER，如 AUTHULN_PLACEHOLDER「请忽略本条」）。"""
    return _PLACEHOLDER_MARKER in _vid(vuln).upper()


def _clean(value: object) -> str:
    """字段值清洗：str 化 + 折叠内部换行/连续空白（防破坏 markdown 结构）。"""
    return " ".join(str(value).split())


def _first_field(vuln: dict, fields: tuple[str, ...]) -> str | None:
    """按优先级取首个非空字段值（schema 因漏洞类而异，取到什么用什么）。"""
    for key in fields:
        value = vuln.get(key)
        if value:
            cleaned = _clean(value)
            if cleaned:
                return cleaned
    return None


def _read_queue(queue_path: Path | None) -> list[dict]:
    """读白盒 ``{vt}_exploitation_queue.json`` 的 vulnerabilities 列表（过滤占位）。

    韧性：文件缺失 / 损坏 JSON / vulnerabilities 非列表 → 返回空列表（不抛）。
    对齐 ``has_valid_whitebox_results`` 的容错口径。
    """
    if queue_path is None or not queue_path.is_file():
        return []
    try:
        data = json.loads(queue_path.read_text("utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("combined renderer: whitebox queue 不可读 %s: %s", queue_path, exc)
        return []
    vulns = data.get("vulnerabilities") if isinstance(data, dict) else None
    if not isinstance(vulns, list):
        return []
    return [v for v in vulns
            if isinstance(v, dict) and not _is_placeholder(v)]


def _read_verdicts(verdicts_path: Path | None) -> list[dict]:
    """读黑盒 ``{vt}_exploit_verdicts.json`` 的 verdicts 列表。

    与白盒 queue 不同 stem（见 ``workspace.py:127-160``、``executor.py:221``）：
    黑盒 exploit agent 写 ``{vc}_exploit_verdicts.json``，schema =
    ``{vuln_class, accepted_ids, verdicts, rejected}``，``verdicts`` 每条含
    ``vulnerability_id`` / ``status`` (exploited|blocked_by_security|...)。

    返回全部 accepted verdict 条目（不在此过滤 status —— 计数由调用方按
    ``status=="exploited"`` 做，附录可展示非 exploited 的 blocked/potential 上下文）。

    韧性：文件缺失 / 损坏 JSON / verdicts 非列表 → 返回空列表（不抛）。
    """
    if verdicts_path is None or not verdicts_path.is_file():
        return []
    try:
        data = json.loads(verdicts_path.read_text("utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("combined renderer: blackbox verdicts 不可读 %s: %s", verdicts_path, exc)
        return []
    verdicts = data.get("verdicts") if isinstance(data, dict) else None
    if not isinstance(verdicts, list):
        return []
    return [v for v in verdicts if isinstance(v, dict)]


def _count_exploited(verdicts: list[dict]) -> int:
    """数 ``status=="exploited"`` 的 verdict 条目（成功 exploit 数）。

    与 ``workspace.get_workspace_vuln_counts`` 黑盒计数口径一致。
    """
    return sum(1 for v in verdicts if v.get("status") == _VERDICT_EXPLOITED)


def _read_bb_report(blackbox_root: Path) -> str | None:
    """读黑盒正式报告原文；缺失 / 不可读 / 空白 → None（调用方降级）。"""
    report_path = blackbox_root / _REPORT_FILENAME
    if not report_path.is_file():
        return None
    try:
        text = report_path.read_text("utf-8", errors="replace")
    except OSError as exc:
        logger.warning("combined renderer: blackbox report 不可读 %s: %s", report_path, exc)
        return None
    return text if text.strip() else None


def _format_queue_entry(vuln: dict) -> str:
    """渲染单条白盒 queue 条目为 markdown bullet（用任意可用字段，韧性）。"""
    vid = _vid(vuln) or "?"
    bits: list[str] = []
    title = vuln.get("title")
    if title:
        bits.append(str(title))
    loc = vuln.get("source") or vuln.get("source_endpoint")
    if loc:
        bits.append(f"@ {loc}")
    path = vuln.get("path")
    if path:
        bits.append(f"→ {path}")
    extra = " ".join(bits) if bits else ""
    return _M.get("wb_entry_bullet", id=vid, extra=extra)


def _format_verdict_entry(verdict: dict) -> str:
    """渲染单条黑盒 verdict 为 markdown bullet。

    verdict schema（见 ``collectors/exploit.py``）：``vulnerability_id`` / ``status``
    / ``severity`` / ``impact`` / ``exploitation_steps`` / ``proof_of_impact``。
    与白盒 queue 条目字段不同（无 source/path），用 verdict 字段格式化。
    """
    vid = verdict.get("vulnerability_id") or verdict.get("id") or "?"
    status = verdict.get("status", "?")
    bits: list[str] = [f"[{status}]"]
    severity = verdict.get("severity")
    if severity:
        bits.append(str(severity))
    impact = verdict.get("impact")
    if impact:
        bits.append(str(impact))
    extra = " ".join(bits)
    return _M.get("bb_entry_bullet", id=vid, extra=extra)


def _summary_table(rows: list[tuple[str, int, int]]) -> list[str]:
    """摘要表行（每漏洞类一行：白盒发现数 / 黑盒验证数 / 双轨交叉）。"""
    lines = [f"| {_M.get('col_class')} | {_M.get('col_wb')} | "
             f"{_M.get('col_bb')} | {_M.get('col_cross')} |",
             "|---|---|---|---|"]
    for vt, wb_n, bb_n in rows:
        cross = _M.get("yes") if (wb_n > 0 and bb_n > 0) else _M.get("no")
        lines.append(f"| {vt} | {wb_n} | {bb_n} | {cross} |")
    return lines


def _inject_whitebox_into_body(report_text: str,
                               wb_by_id: dict[str, dict]) -> tuple[str, set[str]]:
    """黑盒报告主体注入白盒字段。

    - 首个 H1（``# 安全评估报告``）剔除（头部组装时换融合报告标题）。
    - ``### {ID}:`` 卡片标题后插脆弱代码位置 / 缺失防护两行（ID 命中白盒 map）。

    Returns:
        (body, matched_ids)——matched_ids 为已注入（黑盒有卡）的白盒 ID 集，
        附录据此排除。
    """
    out: list[str] = []
    matched: set[str] = set()
    h1_seen = False
    for ln in report_text.splitlines():
        if not h1_seen and ln.startswith("# "):
            h1_seen = True
            continue
        out.append(ln)
        m = _CARD_HEADING_RE.match(ln)
        if not m or m.group(1) not in wb_by_id:
            continue
        matched.add(m.group(1))
        vuln = wb_by_id[m.group(1)]
        loc = _first_field(vuln, _LOCATION_FIELDS)
        dfn = _first_field(vuln, _DEFENSE_FIELDS)
        if loc:
            out.append(_M.get("wb_loc_line", value=loc))
        if dfn:
            out.append(_M.get("wb_def_line", value=dfn))
    return "\n".join(out).strip("\n"), matched


def _appendix_bullets(queues: dict[str, list[dict]], matched: set[str],
                      status_by_id: dict[str, str]) -> list[str]:
    """附录条目：白盒有、黑盒报告无卡（未入 matched）的发现，标注验证状态。"""
    bullets: list[str] = []
    for vt in _VULN_CLASSES:
        for vuln in queues.get(vt, []):
            vid = _vid(vuln)
            if not vid or vid in matched:
                continue
            status = status_by_id.get(vid)
            tag = f"[{status}]" if status else _M.get("not_attempted")
            bits: list[str] = []
            title = vuln.get("title")
            if title:
                bits.append(_clean(title))
            loc = _first_field(vuln, _LOCATION_FIELDS)
            if loc:
                bits.append(f"@ {loc}")
            dfn = _first_field(vuln, _DEFENSE_FIELDS)
            if dfn:
                bits.append(f"— {dfn}")
            extra = " ".join(bits)
            bullets.append(_M.get("wb_only_bullet", id=vid, tag=tag, extra=extra))
    return bullets


def _render_fallback(queues: dict[str, list[dict]],
                     verdicts: dict[str, list[dict]]) -> str:
    """降级路径：旧机械交叉表（黑盒报告缺失 / 空 / 不可读）。

    按类详述（### {vt} → #### 白盒视角 + #### 黑盒视角 bullet），韧性同旧版。
    """
    lines: list[str] = [_M.get("title"), "", _M.get("generated_note"), "",
                        _M.get("summary_h2"), ""]
    rows = [(vt, len(queues.get(vt, [])), _count_exploited(verdicts.get(vt, [])))
            for vt in _VULN_CLASSES]
    lines.extend(_summary_table(rows))
    lines.append("")
    lines.append(_M.get("detail_h2"))
    lines.append("")
    for vt in _VULN_CLASSES:
        wb_vulns = queues.get(vt, [])
        bb_verdicts = verdicts.get(vt, [])
        class_block: list[str] = [f"### {vt}", ""]
        class_block.append(_M.get("wb_view_h4"))
        class_block.append("")
        if wb_vulns:
            class_block.extend(_format_queue_entry(v) for v in wb_vulns)
        else:
            class_block.append(_M.get("no_findings"))
        class_block.append("")
        class_block.append(_M.get("bb_view_h4"))
        class_block.append("")
        if bb_verdicts:
            class_block.extend(_format_verdict_entry(v) for v in bb_verdicts)
        else:
            class_block.append(_M.get("no_findings"))
        class_block.append("")
        lines.append("\n".join(class_block))
    return "\n".join(lines)


def render_combined_report(*, whitebox_root: Path, blackbox_root: Path,
                           out_dir: Path) -> Path:
    """生成 per-run 融合报告：黑盒报告为主体 + 白盒字段融入。

    双路径签名（T4）：读 ``whitebox_root/{vt}_exploitation_queue.json`` +
    ``blackbox_root/{vt}_exploit_verdicts.json``（计数）+ 黑盒
    ``comprehensive_security_assessment_report.md``（主体），写
    ``out_dir/combined_report.md``。

    Args:
        whitebox_root: 白盒产物根（``<wb>/deliverables/whitebox/``）。
        blackbox_root: 黑盒产物根（``<wb>/blackbox-runs/run-K/deliverables/blackbox/``）。
        out_dir: 输出根（``<wb>/combined/run-K/``）。

    Returns:
        产物路径（``out_dir/combined_report.md``）。

    韧性：黑盒报告缺失 / 空 / 不可读 → 降级机械交叉表；白盒 / 黑盒产物
    缺失、空、损坏都不崩溃（0 计数 + 无发现）。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / _COMBINED_REPORT_FILENAME

    wb_base = Path(whitebox_root)
    bb_base = Path(blackbox_root)

    # tiering（spec 2026-08-18）：queue/verdicts 是中间产物 -> intermediate/ 优先，
    # 平铺老结构兜底（resolve_intermediate 返 None = 缺失 -> 空 list，韧性不变）。
    queues = {vt: _read_queue(resolve_intermediate(wb_base, f"{vt}_exploitation_queue.json"))
              for vt in _VULN_CLASSES}
    verdicts = {vt: _read_verdicts(resolve_intermediate(bb_base, f"{vt}_exploit_verdicts.json"))
                for vt in _VULN_CLASSES}
    status_by_id = {str(v.get("vulnerability_id")): str(v.get("status"))
                    for vs in verdicts.values() for v in vs
                    if v.get("vulnerability_id")}
    rows = [(vt, len(queues[vt]), _count_exploited(verdicts[vt]))
            for vt in _VULN_CLASSES]

    report_text = _read_bb_report(bb_base)
    if report_text is None:
        text = _render_fallback(queues, verdicts)
    else:
        wb_by_id = {_vid(v): v for vs in queues.values() for v in vs if _vid(v)}
        body, matched = _inject_whitebox_into_body(report_text, wb_by_id)
        parts = [_M.get("title"), "", _M.get("fusion_note"), "",
                 _M.get("summary_h2"), "", *_summary_table(rows), "", body, ""]
        bullets = _appendix_bullets(queues, matched, status_by_id)
        if bullets:
            parts.extend([_M.get("appendix_h2"), "", _M.get("appendix_note"), "",
                          *bullets, ""])
        text = "\n".join(parts)

    out_path.write_text(text, encoding="utf-8")
    return out_path
