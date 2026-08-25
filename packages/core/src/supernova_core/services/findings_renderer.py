import logging
import re
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Callable

from supernova_core.code_index.gn_collapse import (
    extract_endpoint,
    extract_param,
    parse_sink_call_site_id,
)
from supernova_core.i18n import Messages, current_lang
from supernova_core.models.config import ReportConfig
from supernova_core.models.queue_schemas import (
    Vulnerability,
    VulnerabilityQueue,
)
from supernova_core.services.code_snippet import annotate_direct, extract_snippet
from supernova_core.services.severity_rules import SEVERITY_ZH, effective_severity
from supernova_core.utils.file_io import async_path_exists, async_read_file, async_write_file
from supernova_core.utils.paths import resolve_intermediate, resolve_track_deliverable

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
CONFIDENCE_ZH = {"high": "高", "medium": "中", "low": "低"}
CONFIDENCE_EN = {"high": "High", "medium": "Medium", "low": "Low"}

# 内部标签零泄漏（spec §9）：pipeline 内部判定状态串不得出现在报告正文。
# 先吃掉「纯标签括号组」（如 "(llm-pass-failed, needs_review)" 整体删除），
# 再把残留的独立标签替换为「待复核」。
_INTERNAL_LABEL_RE = re.compile(
    r"\(\s*(?:llm-pass-failed|needs_review|unparseable-llm)"
    r"(?:\s*,\s*(?:llm-pass-failed|needs_review|unparseable-llm))*\s*\)"
    r"|llm-pass-failed|needs_review|unparseable-llm"
)
_FILE_LINE_RE = re.compile(r"[\w./-]+\.[A-Za-z]{1,5}:\d+")

# 漏洞卡 / 报告文案双语（zh/en 可配，跟随 SUPERNOVA_AGENT_NARRATION_LANG）。
_M = Messages({
    # 通用 entry 标签（技术细节折叠区沿用 _label 行式）
    "vulnerable_location": {"zh": "脆弱位置", "en": "Vulnerable Location"},
    "source_detail": {"zh": "来源详情", "en": "Source Detail"},
    "verdict": {"zh": "判定", "en": "Verdict"},
    "witness_payload": {"zh": "验证 payload", "en": "Witness Payload"},
    "sink_call": {"zh": "Sink 调用", "en": "Sink Call"},
    "concat_occurrences": {"zh": "拼接出现", "en": "Concat Occurrences"},
    "sanitization_observed": {"zh": "净化情况", "en": "Sanitization Observed"},
    "sink_function": {"zh": "Sink 函数", "en": "Sink Function"},
    "render_context": {"zh": "渲染上下文", "en": "Render Context"},
    "encoding_observed": {"zh": "编码情况", "en": "Encoding Observed"},
    "source_endpoint": {"zh": "来源端点", "en": "Source Endpoint"},
    "vulnerable_code_location": {"zh": "脆弱代码位置", "en": "Vulnerable Code Location"},
    "missing_defense": {"zh": "缺失防护", "en": "Missing Defense"},
    "exploitation_hypothesis": {"zh": "利用假设", "en": "Exploitation Hypothesis"},
    "suggested_exploit_technique": {"zh": "建议利用技术", "en": "Suggested Exploit Technique"},
    "endpoint": {"zh": "端点", "en": "Endpoint"},
    "role_context": {"zh": "角色上下文", "en": "Role Context"},
    "guard_evidence": {"zh": "防护证据", "en": "Guard Evidence"},
    "side_effect": {"zh": "副作用", "en": "Side Effect"},
    "reason": {"zh": "原因", "en": "Reason"},
    "minimal_witness": {"zh": "最小见证", "en": "Minimal Witness"},
    "vulnerable_parameter": {"zh": "脆弱参数", "en": "Vulnerable Parameter"},
    "cvss": {"zh": "CVSS", "en": "CVSS"},
    "owasp_category": {"zh": "OWASP 分类", "en": "OWASP Category"},
    "dataflow": {"zh": "数据流", "en": "Dataflow"},
    "evidence_chain": {"zh": "证据链", "en": "Evidence Chain"},
    # CLASS_CONFIG heading（value = message key）
    "heading_injection": {"zh": "注入漏洞", "en": "Injection Vulnerabilities"},
    "heading_xss": {"zh": "跨站脚本 (XSS)", "en": "Cross-Site Scripting (XSS)"},
    "heading_auth": {"zh": "认证漏洞", "en": "Authentication Vulnerabilities"},
    "heading_authz": {"zh": "授权漏洞", "en": "Authorization Vulnerabilities"},
    "heading_ssrf": {"zh": "服务端请求伪造 (SSRF)", "en": "Server-Side Request Forgery (SSRF)"},
    # none_found_label（value = message key）
    "none_injection": {"zh": "未发现注入漏洞。", "en": "No injection vulnerabilities found."},
    "none_xss": {"zh": "未发现 XSS 漏洞。", "en": "No XSS vulnerabilities found."},
    "none_auth": {"zh": "未发现认证漏洞。", "en": "No authentication vulnerabilities found."},
    "none_authz": {"zh": "未发现授权漏洞。", "en": "No authorization vulnerabilities found."},
    "none_ssrf": {"zh": "未发现 SSRF 漏洞。", "en": "No SSRF vulnerabilities found."},
    # 四要素统一卡片（spec 2026-08-25 §5/§6）
    "cardname_injection": {"zh": "注入漏洞", "en": "Injection"},
    "cardname_xss": {"zh": "跨站脚本 (XSS)", "en": "XSS"},
    "cardname_auth": {"zh": "认证漏洞", "en": "Authentication"},
    "cardname_authz": {"zh": "授权漏洞", "en": "Authorization"},
    "cardname_ssrf": {"zh": "服务端请求伪造 (SSRF)", "en": "SSRF"},
    "meta_severity": {"zh": "严重程度：", "en": "Severity: "},
    "meta_verification": {"zh": "验证：", "en": "Verification: "},
    "meta_confidence": {"zh": "置信度：", "en": "Confidence: "},
    "meta_sep": {"zh": " ｜ ", "en": " | "},
    "meta_dual_track": {"zh": "（双轨确认）", "en": " (dual-track confirmed)"},
    "verif_static": {"zh": "静态分析", "en": "static analysis"},
    "verif_dynamic": {"zh": "已动态验证", "en": "dynamically verified"},
    "gn_pending_review": {"zh": "待复核", "en": "pending review"},
    "gn_static_hint": {
        "zh": "静态链路发现，建议人工确认后修复。",
        "en": "Static-chain finding; confirm manually before remediation.",
    },
    "gn_rem_hint": {
        "zh": "静态链路发现，建议人工确认。",
        "en": "Static-chain finding; confirm manually.",
    },
    "meta_affected_entries": {"zh": "**受影响入口**", "en": "**Affected Entries**"},
    "tbl_param": {"zh": "参数", "en": "Parameter"},
    "tbl_sink_loc": {"zh": "Sink 位置", "en": "Sink Location"},
    "tbl_chain_id": {"zh": "链 ID", "en": "Chain ID"},
    "suspected_indirect": {"zh": "（疑似间接）", "en": " (suspected indirect)"},
    "sec_description": {"zh": "**漏洞说明**", "en": "**Description**"},
    "sec_impact": {"zh": "**危害**", "en": "**Impact**"},
    "sec_code": {"zh": "**问题代码**", "en": "**Vulnerable Code**"},
    "sec_remediation": {"zh": "**修复建议**", "en": "**Remediation**"},
    "sec_tech_detail": {"zh": "#### 技术细节", "en": "#### Technical Detail"},
    "desc_endpoint": {"zh": "受影响接口：", "en": "Affected endpoint: "},
    "det_into": {"zh": "传入", "en": "flows into"},
    "det_unfiltered_into": {
        "zh": "{param} 未经过滤传入 {sink}",
        "en": "{param} reaches {sink} without filtering",
    },
    "det_unfiltered_sink": {
        "zh": "{sink} 收到未经过滤的输入",
        "en": "{sink} receives unfiltered input",
    },
    "det_missing_protection": {"zh": "缺少必要的防护", "en": "missing required protection"},
    "code_input_generic": {"zh": "用户输入", "en": "user input"},
    "code_issue_line": {
        "zh": "问题所在：{param} 在此处未经校验即进入 {sink}。",
        "en": "Issue: {param} reaches {sink} here without validation.",
    },
    # 五类确定性危害兜底（_IMPACT_FALLBACK 引用）
    "impact_injection": {
        "zh": "攻击者可注入恶意代码或查询，可能导致数据泄露、篡改或服务器接管。",
        "en": "An attacker can inject malicious code or queries, potentially causing data disclosure, tampering, or full server takeover.",
    },
    "impact_xss": {
        "zh": "攻击者可在受害者浏览器中执行任意脚本，窃取会话或凭据、伪造页面操作，最坏情况可接管账户。",
        "en": "An attacker can run arbitrary scripts in victims' browsers to steal sessions or credentials and forge page actions; at worst, account takeover.",
    },
    "impact_ssrf": {
        "zh": "攻击者可让服务器向任意目标发起请求，访问内部服务或云元数据端点，进而以服务器为跳板渗透内网。",
        "en": "An attacker can make the server issue requests to arbitrary targets, reaching internal services or cloud metadata endpoints to pivot into the internal network.",
    },
    "impact_auth": {
        "zh": "攻击者可绕过或攻破认证机制，冒充任意用户访问系统，最坏情况可获得管理员权限。",
        "en": "An attacker can bypass or break authentication and impersonate any user; at worst, gain administrative access.",
    },
    "impact_authz": {
        "zh": "攻击者可越权访问或篡改其他用户的数据与功能，造成横向越权的数据泄露或破坏。",
        "en": "An attacker can access or modify other users' data and functions, causing unauthorized data disclosure or tampering.",
    },
    # 五类确定性修复兜底（_REM_FALLBACK 引用；LLM 未携带 remediation 的过渡文案）
    "rem_injection": {
        "zh": "将用户输入与命令/查询结构分离：SQL 用参数化查询，命令用数组传参调用，禁止 eval/exec 直接执行用户输入。",
        "en": "Separate user input from command/query structure: parameterized queries for SQL, array-argument calls for commands; never feed user input to eval/exec.",
    },
    "rem_xss": {
        "zh": "输出点按上下文转义（HTML 用实体编码、JS 用 \\uXXXX 转义），或改用 textContent / 框架自动转义，禁止把用户输入直接拼入 innerHTML。",
        "en": "Escape output by context (HTML entity encoding, \\uXXXX for JS) or switch to textContent / framework auto-escaping; never concatenate user input into innerHTML.",
    },
    "rem_ssrf": {
        "zh": "对外发 URL 做协议与目标校验（仅允许 http/https，解析后 IP 不落在内网/环回段），不允许直接请求用户提供的 URL。",
        "en": "Validate outbound URLs by protocol and target (http/https only, resolved IP outside internal/loopback ranges); never fetch user-supplied URLs directly.",
    },
    "rem_auth": {
        "zh": "补齐缺失的认证控制（登录失败限流与锁定、安全的会话与凭据存储），并删除可绕过的认证路径。",
        "en": "Add the missing authentication controls (login throttling/lockout, secure session and credential storage) and remove bypassable auth paths.",
    },
    "rem_authz": {
        "zh": "在服务端对每次对象访问做属主/权限校验（优先行级过滤，如按 user_id 条件查询），不信任客户端传入的 ID。",
        "en": "Enforce server-side ownership/permission checks on every object access (prefer row-level filtering, e.g. filtering by user_id); never trust client-supplied IDs.",
    },
    # disclaimer + 降级文案
    "disclaimer": {
        "zh": "> **免责声明：** 本报告由自动化安全评估工具生成。所有发现须经合格安全专业人员核实后再采取行动。",
        "en": "> **Disclaimer:** This report was generated by an automated security assessment tool. All findings should be verified by a qualified security professional before taking action.",
    },
    "queue_unreadable": {
        "zh": "> ⚠️ {heading} 队列不可读；该类发现不可用。见日志。",
        "en": "> ⚠️ {heading} queue unreadable; findings unavailable for this class. See logs.",
    },
    "queue_auto_recovered": {
        "zh": "> ⚠️ 队列已自动恢复（{warnings}）。原始队列保留在 `{queue_file}`；请核实数据完整性。",
        "en": "> ⚠️ Queue auto-recovered ({warnings}). Raw queue preserved at `{queue_file}`; verify data integrity.",
    },
    "no_renderable_entries": {
        "zh": "> ⚠️ `{queue_file}` 无可渲染条目（{reason}）。",
        "en": "> ⚠️ No renderable entries in `{queue_file}` ({reason}).",
    },
    "no_parseable_entries": {"zh": "无可解析条目", "en": "no parseable entries"},
    "render_error": {"zh": "### {id} — 渲染错误\n", "en": "### {id} — render error\n"},
})

# 五类确定性危害一句话（spec §5「危害」要素兜底；value = message key）
_IMPACT_FALLBACK = {
    "injection": "impact_injection",
    "xss": "impact_xss",
    "ssrf": "impact_ssrf",
    "auth": "impact_auth",
    "authz": "impact_authz",
}

# 五类确定性修复一句话（remediation 字段缺省的过渡兜底；value = message key）
_REM_FALLBACK = {
    "injection": "rem_injection",
    "xss": "rem_xss",
    "ssrf": "rem_ssrf",
    "auth": "rem_auth",
    "authz": "rem_authz",
}


def _label(key: str) -> str:
    """渲染 `- **<label>:**` 前缀。"""
    return f"- **{_M.get(key)}:**"


def _strip_internal(text: str) -> str:
    """内部标签零泄漏（spec §9）：纯标签括号组整体删除，孤立标签→「待复核」。"""
    def _repl(m: re.Match) -> str:
        return "" if m.group(0).startswith("(") else _M.get("gn_pending_review")
    return _INTERNAL_LABEL_RE.sub(_repl, text)


def _snippet_lang_tag(loc: str) -> str:
    """从 `file.ext:line` 位置串提 fence 语言标注（无则空）。"""
    m = _FILE_LINE_RE.search(loc or "")
    if not m:
        return ""
    name = m.group(0).split(":", 1)[0]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext if ext.isalnum() else ""


def _card_param(vuln) -> str | None:
    """参数名：extract_param(source) → affected_parameters[0] → vulnerable_parameter。"""
    p = extract_param(getattr(vuln, "source", None))
    if not p:
        ap = getattr(vuln, "affected_parameters", None) or []
        p = ap[0] if ap else None
    return p or getattr(vuln, "vulnerable_parameter", None)


def _card_sink(vuln) -> tuple[str | None, str | None]:
    """(sink 显示名, file:line 位置)：优先 LLM sink_function，回退 GN sink_call 解析。"""
    sink_call = getattr(vuln, "sink_call", None)
    func = loc = None
    if isinstance(sink_call, str) and sink_call:
        func, loc = parse_sink_call_site_id(sink_call)
    name = getattr(vuln, "sink_function", None) or func
    if not name and isinstance(sink_call, str) and sink_call:
        name = sink_call.split(":", 1)[0]
    return name, loc


def _card_loc(vuln, sink_loc: str | None) -> str | None:
    """file:line 位置：GN sink_call 解析 → affected_entries[0] → 代码位置字段 → 正则。"""
    if sink_loc:
        return sink_loc
    entries = getattr(vuln, "affected_entries", None) or []
    if entries and isinstance(entries[0], dict):
        sl = entries[0].get("sink_location")
        if isinstance(sl, str) and sl:
            return sl
    vcl = getattr(vuln, "vulnerable_code_location", None)
    if isinstance(vcl, str) and vcl:
        return vcl
    for attr in ("path", "sink_call"):
        val = getattr(vuln, attr, None)
        if isinstance(val, str):
            m = _FILE_LINE_RE.search(val)
            if m:
                return m.group(0)
    return None


def _deterministic_core(vuln) -> str | None:
    """title 缺省时的确定性一句话主干：`{param} 传入 {sink}`（退化容忍单边）。"""
    param = _card_param(vuln)
    sink, _ = _card_sink(vuln)
    if param and sink:
        return f"{param} {_M.get('det_into')} {sink}"
    if sink:
        return sink
    if param:
        return param
    return None


def _gn_description(cls_name: str, colon: str, param, sink, loc_part: str) -> str:
    """GN-only 确定性漏洞说明（spec §6）：`{类名}：{param} 未经过滤传入 {sink}（{loc}）`。"""
    if param and sink:
        body = _M.get("det_unfiltered_into", param=param, sink=sink)
    elif sink:
        body = _M.get("det_unfiltered_sink", sink=sink)
    else:
        body = _M.get("det_missing_protection")
    return f"{cls_name}{colon}{body}{loc_part}"


def _description_lines(vuln, gn_only: bool, cls_name: str, colon: str,
                       param, sink, loc_part: str) -> list[str]:
    """漏洞说明：LLM 卡走 title/notes/source→sink 叙述；GN-only/无线索走确定性描述。"""
    if gn_only or not (vuln.title or vuln.notes):
        return [_gn_description(cls_name, colon, param, sink, loc_part)]
    desc: list[str] = []
    if vuln.title:
        desc.append(vuln.title)
    if vuln.notes:
        desc.append(vuln.notes)
    source = getattr(vuln, "source", None)
    path = getattr(vuln, "path", None)
    if source or path:
        desc.append(f"{source or 'N/A'} → {path or 'N/A'}")
    endpoint = getattr(vuln, "endpoint", None) or extract_endpoint(path)
    if endpoint and not any(endpoint in d for d in desc):
        desc.append(f"{_M.get('desc_endpoint')}{endpoint}")
    return desc


def _tech_detail_lines(vuln) -> list[str]:
    """技术细节折叠区（spec §5）：现有判定字段全量降级收纳，沿用 _label 行式。"""
    lines: list[str] = []

    def add(key: str, value) -> None:
        if value:
            lines.append(f"{_label(key)} {value}")

    if getattr(vuln, "source", None) or getattr(vuln, "path", None):
        add("vulnerable_location",
            f"{getattr(vuln, 'source', None) or 'N/A'} → "
            f"{getattr(vuln, 'path', None) or 'N/A'}")
    add("source_detail", getattr(vuln, "source_detail", None))
    # sink：优先 LLM 实际输出的 sink_function，回退旧 sink_call（语义相同）
    if getattr(vuln, "sink_function", None):
        add("sink_function", vuln.sink_function)
    else:
        add("sink_call", getattr(vuln, "sink_call", None))
    add("render_context", getattr(vuln, "render_context", None))
    add("concat_occurrences", getattr(vuln, "concat_occurrences", None))
    # 编码/sanitizer：优先 LLM 输出的 encoding_observed，回退旧 sanitization_observed
    if getattr(vuln, "encoding_observed", None):
        add("encoding_observed", vuln.encoding_observed)
    else:
        add("sanitization_observed", getattr(vuln, "sanitization_observed", None))
    add("source_endpoint", getattr(vuln, "source_endpoint", None))
    add("endpoint", getattr(vuln, "endpoint", None))
    add("vulnerable_parameter", getattr(vuln, "vulnerable_parameter", None))
    add("vulnerable_code_location", getattr(vuln, "vulnerable_code_location", None))
    add("missing_defense", getattr(vuln, "missing_defense", None))
    add("exploitation_hypothesis", getattr(vuln, "exploitation_hypothesis", None))
    add("suggested_exploit_technique", getattr(vuln, "suggested_exploit_technique", None))
    add("role_context", getattr(vuln, "role_context", None))
    add("guard_evidence", getattr(vuln, "guard_evidence", None))
    add("side_effect", getattr(vuln, "side_effect", None))
    add("reason", getattr(vuln, "reason", None))
    add("minimal_witness", getattr(vuln, "minimal_witness", None))
    add("verdict", getattr(vuln, "verdict", None))
    add("witness_payload", getattr(vuln, "witness_payload", None))
    add("cvss", getattr(vuln, "cvss", None))
    add("owasp_category", getattr(vuln, "owasp_category", None))
    add("evidence_chain", getattr(vuln, "evidence_chain", None))
    steps = getattr(vuln, "dataflow_steps", None)
    if steps:
        segs: list[str] = []
        for s in steps:
            if not isinstance(s, dict):
                continue
            label = str(s.get("label") or "?")
            f = s.get("file")
            if f:
                label += f" ({f}:{s.get('line')})" if s.get("line") else f" ({f})"
            segs.append(label)
        add("dataflow", " → ".join(segs))
    return lines


def render_vuln_card(vuln, vuln_class: str, snippet: str | None = None) -> str:
    """四要素统一漏洞卡（spec 2026-08-25 §5/§6）。

    结构：`### {ID} {类名}：{title}` → 元信息行（严重程度/CWE/验证/置信度，
    双轨确认、GN-only 追加待复核）→ 受影响入口表 → 漏洞说明 → 危害 →
    问题代码（snippet fence）→ 修复建议 → 技术细节（判定字段全量收纳）。
    内部标签（llm-pass-failed/needs_review/unparseable-llm）零泄漏。
    """
    zh = current_lang() == "zh"
    colon = "：" if zh else ": "
    cls_name = _M.get(f"cardname_{vuln_class}")
    gn_only = (getattr(vuln, "source_track", None) == "gitnexus"
               and getattr(vuln, "merge_source", None) != "both")
    param = _card_param(vuln)
    sink_name, sink_loc = _card_sink(vuln)
    loc = _card_loc(vuln, sink_loc)
    loc_part = f"（{loc}）" if zh and loc else (f" ({loc})" if loc else "")

    lines: list[str] = []

    # 标题：有 title 用 title；缺省走确定性描述 `{param} 传入 {sink}（{loc}）`
    core = _deterministic_core(vuln)
    title = vuln.title or (core + loc_part if core else None)
    lines.append(f"### {vuln.ID} {cls_name}{colon}{title}" if title
                 else f"### {vuln.ID} {cls_name}")
    lines.append("")

    # 元信息行：严重程度 ｜ CWE ｜ 验证 ｜ 置信度（双轨确认）[｜ 待复核]
    sev = effective_severity(vuln)
    sev_disp = SEVERITY_ZH.get(sev, sev) if zh else sev.capitalize()
    meta_parts = [f"{_M.get('meta_severity')}{sev_disp}"]
    if vuln.cwe_id:
        meta_parts.append(vuln.cwe_id)
    verif = (_M.get("verif_dynamic")
             if getattr(vuln, "verification", None) == "dynamically_verified"
             else _M.get("verif_static"))
    meta_parts.append(f"{_M.get('meta_verification')}{verif}")
    conf = vuln.confidence or ""
    conf_disp = (CONFIDENCE_ZH if zh else CONFIDENCE_EN).get(conf, _strip_internal(conf))
    conf_line = f"{_M.get('meta_confidence')}{conf_disp}"
    if getattr(vuln, "merge_source", None) == "both":
        conf_line += _M.get("meta_dual_track")
    meta_parts.append(conf_line)
    if gn_only:
        meta_parts.append(_M.get("gn_pending_review"))
    lines.append(_M.get("meta_sep").join(meta_parts))
    lines.append("")

    # 受影响入口表（有 affected_entries 时）
    entries = getattr(vuln, "affected_entries", None) or []
    if entries:
        lines.append(_M.get("meta_affected_entries"))
        lines.append(
            f"| {_M.get('tbl_param')} | {_M.get('tbl_sink_loc')} | "
            f"{_M.get('tbl_chain_id')} |")
        lines.append("|---|---|---|")
        for e in entries:
            if not isinstance(e, dict):
                continue
            p = str(e.get("parameter") or "")
            if e.get("direct") is False:
                p += _M.get("suspected_indirect")
            lines.append(
                f"| {p} | {e.get('sink_location') or ''} | {e.get('chain_id') or ''} |")
        lines.append("")

    # 漏洞说明
    lines.append(_M.get("sec_description"))
    lines.extend(_description_lines(vuln, gn_only, cls_name, colon, param, sink_name, loc_part))
    lines.append("")

    # 危害：impact（并行任务加的字段，此刻可能不存在）→ notes → 类级兜底；
    # GN-only 统一明示「静态链路发现，建议人工确认后修复」
    impact = getattr(vuln, "impact", None)
    if gn_only:
        impact = impact or vuln.notes or _M.get("gn_static_hint")
    else:
        impact = impact or vuln.notes or _M.get(_IMPACT_FALLBACK[vuln_class])
    lines.append(_M.get("sec_impact"))
    lines.append(impact)
    lines.append("")

    # 问题代码：snippet 非空时 fence（按扩展名语言标注）+ 一句指出问题
    if snippet:
        lines.append(_M.get("sec_code"))
        lines.append(f"```{_snippet_lang_tag(loc or '')}")
        lines.append(snippet)
        lines.append("```")
        if sink_name:
            p_disp = param or _M.get("code_input_generic")
            lines.append(_M.get("code_issue_line", param=p_disp, sink=sink_name))
        lines.append("")

    # 修复建议：remediation（并行任务加的字段）→ GN-only 人工确认提示 → 类级兜底
    remediation = getattr(vuln, "remediation", None)
    if not remediation:
        remediation = _M.get("gn_rem_hint" if gn_only else _REM_FALLBACK[vuln_class])
    lines.append(_M.get("sec_remediation"))
    lines.append(remediation)
    lines.append("")

    # 技术细节（折叠附录区）
    lines.append(_M.get("sec_tech_detail"))
    lines.extend(_tech_detail_lines(vuln))

    # 行级 rstrip：吃掉内部标签剥离留下的行尾空白
    return "\n".join(
        l.rstrip() for l in _strip_internal("\n".join(lines)).splitlines())


@dataclass
class VulnClassConfig:
    heading: str        # message key（运行时 _M.get 解析）
    none_found_label: str  # message key
    queue_file: str
    findings_file: str
    render_entry: Callable


CLASS_CONFIG: dict[str, VulnClassConfig] = {
    "injection": VulnClassConfig(
        heading="heading_injection",
        none_found_label="none_injection",
        queue_file="injection_exploitation_queue.json",
        findings_file="injection_findings.md",
        render_entry=partial(render_vuln_card, vuln_class="injection"),
    ),
    "xss": VulnClassConfig(
        heading="heading_xss",
        none_found_label="none_xss",
        queue_file="xss_exploitation_queue.json",
        findings_file="xss_findings.md",
        render_entry=partial(render_vuln_card, vuln_class="xss"),
    ),
    "auth": VulnClassConfig(
        heading="heading_auth",
        none_found_label="none_auth",
        queue_file="auth_exploitation_queue.json",
        findings_file="auth_findings.md",
        render_entry=partial(render_vuln_card, vuln_class="auth"),
    ),
    "authz": VulnClassConfig(
        heading="heading_authz",
        none_found_label="none_authz",
        queue_file="authz_exploitation_queue.json",
        findings_file="authz_findings.md",
        render_entry=partial(render_vuln_card, vuln_class="authz"),
    ),
    "ssrf": VulnClassConfig(
        heading="heading_ssrf",
        none_found_label="none_ssrf",
        queue_file="ssrf_exploitation_queue.json",
        findings_file="ssrf_findings.md",
        render_entry=partial(render_vuln_card, vuln_class="ssrf"),
    ),
}


def _passes_filter(vuln: Vulnerability, config: ReportConfig) -> bool:
    if config.min_confidence:
        vuln_level = CONFIDENCE_ORDER.get(vuln.confidence, 0)
        min_level = CONFIDENCE_ORDER.get(config.min_confidence, 0)
        if vuln_level < min_level:
            return False
    if config.min_severity:
        severity = getattr(vuln, "severity", None)
        if severity is not None:
            vuln_level = SEVERITY_ORDER.get(severity, 0)
            min_level = SEVERITY_ORDER.get(config.min_severity, 0)
            if vuln_level < min_level:
                return False
    return True


def filter_vulnerabilities(queue: VulnerabilityQueue, config: ReportConfig) -> list[Vulnerability]:
    return [v for v in queue.vulnerabilities if _passes_filter(v, config)]


def _first_sink_location(vuln) -> str | None:
    """渲染循环取 snippet 用的首个 sink 位置：affected_entries[0] 优先，
    LLM-only 回退 path/sink_call/代码位置字段正则提首个 `file.ext:line`。"""
    entries = getattr(vuln, "affected_entries", None) or []
    if entries and isinstance(entries[0], dict):
        sl = entries[0].get("sink_location")
        if isinstance(sl, str) and sl:
            return sl
    for attr in ("path", "sink_call", "vulnerable_code_location"):
        val = getattr(vuln, attr, None)
        if isinstance(val, str):
            m = _FILE_LINE_RE.search(val)
            if m:
                return m.group(0)
    return None


class FindingsRenderer:
    @staticmethod
    async def render_findings_from_queues(
        deliverables_path: Path,
        report_config: ReportConfig | None = None,
        *,
        queue_subdir: str | None = None,
        findings_subdir: str | None = None,
        repo_root: Path | None = None,
    ) -> None:
        """Render findings MD from queue JSON.

        Shared by both whitebox and blackbox callers. The keyword-only params
        route queue READS and findings WRITES to track subdirectories without
        coupling this shared service to either caller's layout:

        - ``findings_subdir``: when set, findings MD is written to
          ``deliverables_path / findings_subdir /`` (created if missing). When
          ``None`` (the whitebox default), findings are written directly under
          ``deliverables_path`` — preserving the pre-2026-07 behaviour so the
          whitebox caller's reads/writes stay inside ``whitebox/``.
        - ``queue_subdir``: when set, queue JSON is read via
          :func:`resolve_track_deliverable` (``deliverables_path / queue_subdir /
          filename`` with legacy root fallback). When ``None``, queues are read
          via :func:`resolve_intermediate` — tiering 后 queue 落桶内
          ``intermediate/``，读侧 intermediate/ 优先 + 平铺老结构兜底。
        - ``repo_root``: when set, each vuln gets a deterministic code snippet
          (sink line ±3, spec §10.4) extracted from the repo and injected into
          the card's 问题代码 section; ``affected_entries`` get ``direct``
          annotations. ``None`` skips extraction (blackbox / tests).

        Defaults preserve the old behaviour — existing callers are unaffected.
        """
        config = report_config or ReportConfig()
        if findings_subdir:
            findings_base = deliverables_path / findings_subdir
            findings_base.mkdir(parents=True, exist_ok=True)
        else:
            findings_base = deliverables_path
        for _vuln_class, class_cfg in CLASS_CONFIG.items():
            findings_path = findings_base / class_cfg.findings_file
            if await async_path_exists(findings_path):
                continue
            if queue_subdir:
                queue_path = resolve_track_deliverable(
                    deliverables_path, queue_subdir, class_cfg.queue_file)
            else:
                # tiering（spec 2026-08-18）：queue 是中间产物 → 写侧落桶内
                # intermediate/（executor.py intermediate_path），读侧须
                # intermediate/ 优先 + 平铺老结构兜底（f4b98d38 扫尾模式同口径；
                # 平铺直读会让 findings.md 静默缺失 → assemble 回落
                # analysis_deliverable → 报告页出现分分析报告）。
                queue_path = resolve_intermediate(
                    deliverables_path, class_cfg.queue_file)
            if queue_path is None or not await async_path_exists(queue_path):
                continue

            heading = _M.get(class_cfg.heading)
            try:
                content = await async_read_file(queue_path)
                parsed = VulnerabilityQueue.parse_lenient(
                    content, vuln_class=_vuln_class)
            except Exception as exc:  # noqa: BLE001 — isolate this class
                logger.warning("queue %s unreadable: %s", class_cfg.queue_file, exc)
                await async_write_file(findings_path, "\n".join([
                    f"## {heading}", "",
                    _M.get("queue_unreadable", heading=heading),
                    "", _M.get("disclaimer"), "",
                ]))
                continue

            if parsed.warnings:
                logger.warning(
                    "queue %s parsed leniently: %s", class_cfg.queue_file, parsed.warnings
                )
            queue = parsed.queue
            filtered = filter_vulnerabilities(queue, config)

            sections: list[str] = [f"## {heading}", ""]
            if parsed.warnings:
                sections.append(
                    _M.get("queue_auto_recovered",
                           warnings="; ".join(parsed.warnings),
                           queue_file=class_cfg.queue_file)
                )
                sections.append("")

            if not filtered:
                if parsed.original_form == "object" and not parsed.warnings:
                    sections.append(_M.get(class_cfg.none_found_label))
                else:
                    reason = "; ".join(parsed.warnings) or _M.get("no_parseable_entries")
                    sections.append(
                        _M.get("no_renderable_entries",
                               queue_file=class_cfg.queue_file, reason=reason)
                    )
            else:
                for vuln in filtered:
                    try:
                        snippet: str | None = None
                        if repo_root is not None:
                            loc = _first_sink_location(vuln)
                            if loc:
                                snippet = await extract_snippet(repo_root, loc)
                            annotate_direct(
                                getattr(vuln, "affected_entries", None), snippet)
                            vuln.code_snippet = snippet
                        sections.append(class_cfg.render_entry(vuln, snippet=snippet))
                    except Exception as exc:  # noqa: BLE001 — isolate single entry
                        logger.warning(
                            "render entry %s failed: %s",
                            getattr(vuln, "ID", "?"), exc,
                        )
                        sections.append(
                            _M.get("render_error", id=getattr(vuln, "ID", "UNKNOWN"))
                        )

            sections.append("")
            sections.append(_M.get("disclaimer"))
            sections.append("")
            await async_write_file(findings_path, "\n".join(sections))
