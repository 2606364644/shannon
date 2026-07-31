"""vuln deliverable renderer(纯函数,对齐 TS services/vuln-renderer.ts::renderVulnDeliverable)。

5 class(injection / xss / auth / ssrf / authz)共用 render_vuln,按 vuln_class branching。
4 张 per-class 映射表 + 5 个 section 渲染函数。输入 data = collector.get_all() 子集
(缺键 = skipped -> placeholder,不 fail)。本模块不 import GitNexus / 确定性层(守 §1)。

双语(zh/en)：展示文案(标题/标签)走 _M；field_name 与 collector schema 对齐保持英文(代码消费)。
"""
from __future__ import annotations

from supernova_core.i18n import Messages
from ._helpers import as_dict, as_dict_list, as_list, placeholder, render_table

# ── 双语文案（zh/en 可配，跟随 SUPERNOVA_AGENT_NARRATION_LANG）──────────
_M = Messages({
    # TITLES（value = message key）
    "title_injection": {"zh": "注入分析报告", "en": "Injection Analysis Report"},
    "title_xss": {"zh": "跨站脚本 (XSS) 分析报告", "en": "Cross-Site Scripting (XSS) Analysis Report"},
    "title_auth": {"zh": "认证分析报告", "en": "Authentication Analysis Report"},
    "title_ssrf": {"zh": "SSRF 分析报告", "en": "SSRF Analysis Report"},
    "title_authz": {"zh": "授权分析报告", "en": "Authorization Analysis Report"},
    # SECTION_FOUR_HEADING（value = message key）
    "sec4_vectors": {"zh": "4. 已分析并确认安全的向量", "en": "4. Vectors Analyzed and Confirmed Secure"},
    "sec4_secure": {"zh": "4. 安全设计：已验证组件", "en": "4. Secure by Design: Validated Components"},
    # section 标题
    "sec1_exec": {"zh": "## 1. 执行摘要", "en": "## 1. Executive Summary"},
    "sec2_patterns": {"zh": "## 2. 主要漏洞模式", "en": "## 2. Dominant Vulnerability Patterns"},
    "sec3_intel": {"zh": "## 3. 利用情报", "en": "## 3. Strategic Intelligence for Exploitation"},
    "sec5_blind": {"zh": "## 5. 分析约束与盲区", "en": "## 5. Analysis Constraints and Blind Spots"},
    # 空态/占位句
    "no_patterns": {"zh": "*未识别出主要模式。*", "en": "*No dominant patterns identified.*"},
    "no_vectors": {"zh": "*分析中无向量被确认安全。*",
                   "en": "*No vectors confirmed secure during analysis.*"},
    "no_blind": {"zh": "*未识别出分析约束或盲区。*",
                 "en": "*No analysis constraints or blind spots identified.*"},
    "pattern_n": {"zh": "### 模式 {i}：{name}", "en": "### Pattern {i}: {name}"},
    "implication": {"zh": "**含义：**", "en": "**Implication:**"},
    "findings": {"zh": "**发现：**", "en": "**Findings:**"},
    # STRATEGIC_INTEL_SUBHEADERS headers（value = message key；field_name 保持英文）
    "intel_defensive_evasion": {"zh": "防御规避（WAF 分析）", "en": "Defensive Evasion (WAF Analysis)"},
    "intel_error_based": {"zh": "基于错误的注入潜力", "en": "Error-Based Injection Potential"},
    "intel_db_tech": {"zh": "已确认数据库技术", "en": "Confirmed Database Technology"},
    "intel_csp": {"zh": "内容安全策略 (CSP) 分析", "en": "Content Security Policy (CSP) Analysis"},
    "intel_cookie": {"zh": "Cookie 安全", "en": "Cookie Security"},
    "intel_auth_method": {"zh": "认证方式", "en": "Authentication Method"},
    "intel_session_token": {"zh": "会话令牌详情", "en": "Session Token Details"},
    "intel_password_policy": {"zh": "密码策略", "en": "Password Policy"},
    "intel_http_client": {"zh": "HTTP 客户端库", "en": "HTTP Client Library"},
    "intel_request_arch": {"zh": "请求架构", "en": "Request Architecture"},
    "intel_internal_services": {"zh": "内部服务", "en": "Internal Services"},
    "intel_session_mgmt": {"zh": "会话管理架构", "en": "Session Management Architecture"},
    "intel_role_model": {"zh": "角色/权限模型", "en": "Role/Permission Model"},
    "intel_resource_access": {"zh": "资源访问模式", "en": "Resource Access Patterns"},
    "intel_workflow": {"zh": "工作流实现", "en": "Workflow Implementation"},
    # SECTION_FOUR_COLUMNS 列名（value = message key）
    "col_source": {"zh": "来源", "en": "Source"},
    "col_endpoint_loc": {"zh": "端点/文件位置", "en": "Endpoint/File Location"},
    "col_component": {"zh": "组件/流程", "en": "Component/Flow"},
    "col_endpoint": {"zh": "端点", "en": "Endpoint"},
    "col_guard_loc": {"zh": "防护位置", "en": "Guard Location"},
    "col_defense": {"zh": "防御机制", "en": "Defense Mechanism"},
    "col_render_ctx": {"zh": "渲染上下文", "en": "Render Context"},
})

# ── per-class 映射表(value=message key,运行时 _M.get 解析;field_name 保持英文) ──
TITLES: dict[str, str] = {
    "injection": "title_injection",
    "xss": "title_xss",
    "auth": "title_auth",
    "ssrf": "title_ssrf",
    "authz": "title_authz",
}

SECTION_FOUR_HEADING: dict[str, str] = {
    "injection": "sec4_vectors",
    "xss": "sec4_vectors",
    "auth": "sec4_secure",
    "ssrf": "sec4_secure",
    "authz": "sec4_vectors",
}

# [field_name, header_key] per class -- field_name 与 collectors/vuln.py schema 一致(英文)
STRATEGIC_INTEL_SUBHEADERS: dict[str, list[tuple[str, str]]] = {
    "injection": [
        ("defensive_evasion_waf", "intel_defensive_evasion"),
        ("error_based_potential", "intel_error_based"),
        ("confirmed_database_technology", "intel_db_tech"),
    ],
    "xss": [
        ("csp_analysis", "intel_csp"),
        ("cookie_security", "intel_cookie"),
    ],
    "auth": [
        ("authentication_method", "intel_auth_method"),
        ("session_token_details", "intel_session_token"),
        ("password_policy", "intel_password_policy"),
    ],
    "ssrf": [
        ("http_client_library", "intel_http_client"),
        ("request_architecture", "intel_request_arch"),
        ("internal_services", "intel_internal_services"),
    ],
    "authz": [
        ("session_management_architecture", "intel_session_mgmt"),
        ("role_permission_model", "intel_role_model"),
        ("resource_access_patterns", "intel_resource_access"),
        ("workflow_implementation", "intel_workflow"),
    ],
}

# §4 列形状(value=message key):XSS 多 Render Context 列;subject/location 列名 per class
SECTION_FOUR_COLUMNS: dict[str, dict] = {
    "injection": {"subject": "col_source", "location": "col_endpoint_loc",
                  "include_render_context": False},
    "xss": {"subject": "col_source", "location": "col_endpoint_loc",
            "include_render_context": True},
    "auth": {"subject": "col_component", "location": "col_endpoint_loc",
             "include_render_context": False},
    "ssrf": {"subject": "col_component", "location": "col_endpoint_loc",
             "include_render_context": False},
    "authz": {"subject": "col_endpoint", "location": "col_guard_loc",
              "include_render_context": False},
}


# ── section 渲染函数(5 个,纯函数) ─────────────────────────────────────
def _executive_summary(summary: dict | None) -> str:
    summary = as_dict(summary)
    head = _M.get("sec1_exec")
    if not summary:
        return f"{head}\n\n{placeholder('Section 1', 'set_findings_summary')}"
    return f"{head}\n\n{summary.get('key_outcome', '')}"


def _dominant_patterns(summary: dict | None) -> str:
    summary = as_dict(summary)
    head = _M.get("sec2_patterns")
    if not summary:
        return f"{head}\n\n{placeholder('Section 2', 'set_findings_summary')}"
    patterns = as_dict_list(summary.get("patterns"))
    if not patterns:
        return f"{head}\n\n{_M.get('no_patterns')}"
    blocks = []
    for i, p in enumerate(patterns, 1):
        ids = ", ".join(str(x) for x in as_list(p.get("representative_finding_ids")))
        blocks.append("\n".join([
            _M.get("pattern_n", i=i, name=p.get("name", "")), "",
            p.get("description", ""), "",
            f"{_M.get('implication')} {p.get('implication', '')}", "",
            f"{_M.get('findings')} {ids}",
        ]))
    return f"{head}\n\n" + "\n\n".join(blocks)


def _strategic_intel(vuln_class: str, intel: dict | None) -> str:
    intel = as_dict(intel)
    head = _M.get("sec3_intel")
    if not intel:
        return f"{head}\n\n{placeholder('Section 3', 'set_strategic_intelligence')}"
    subheaders = STRATEGIC_INTEL_SUBHEADERS.get(vuln_class, [])
    blocks = []
    for field_name, header_key in subheaders:
        val = intel.get(field_name)
        if val is not None:
            blocks.append(f"### {_M.get(header_key)}\n\n{val}")
    if not blocks:
        return f"{head}\n\n{placeholder('Section 3', 'set_strategic_intelligence')}"
    return f"{head}\n\n" + "\n\n".join(blocks)


def _safe_vectors(vuln_class: str, data: dict | None) -> str:
    data = as_dict(data)
    cols = SECTION_FOUR_COLUMNS[vuln_class]
    head = f"## {_M.get(SECTION_FOUR_HEADING[vuln_class])}"
    if not data:
        return f"{head}\n\n{placeholder('Section 4', 'set_safe_vectors')}"
    vectors = as_dict_list(data.get("vectors"))
    if not vectors:
        return f"{head}\n\n{_M.get('no_vectors')}"
    headers = [_M.get(cols["subject"]), _M.get(cols["location"]), _M.get("col_defense")]
    if cols["include_render_context"]:
        headers.append(_M.get("col_render_ctx"))
    rows = []
    for v in vectors:
        row = [v.get("subject", ""), v.get("location", ""), v.get("defense_mechanism", "")]
        if cols["include_render_context"]:
            row.append(v.get("render_context") or "")
        rows.append(row)
    return f"{head}\n\n{render_table(headers, rows)}"


def _blind_spots(data: dict | None) -> str:
    data = as_dict(data)
    head = _M.get("sec5_blind")
    if not data:
        return f"{head}\n\n{placeholder('Section 5', 'set_blind_spots')}"
    items = as_dict_list(data.get("items"))
    if not items:
        return f"{head}\n\n{_M.get('no_blind')}"
    blocks = [f"### {it.get('heading', '')}\n\n{it.get('description', '')}" for it in items]
    return f"{head}\n\n" + "\n\n".join(blocks)


def render_vuln(vuln_class: str, data: dict) -> str:
    """渲染完整 vuln deliverable md:标题 + 5 section。data = collector.get_all() 子集。"""
    summary = as_dict(data.get("findings_summary"))
    parts = [
        f"# {_M.get(TITLES[vuln_class])}", "",
        _executive_summary(summary), "",
        _dominant_patterns(summary), "",
        _strategic_intel(vuln_class, data.get("strategic_intelligence")), "",
        _safe_vectors(vuln_class, data.get("safe_vectors")), "",
        _blind_spots(data.get("blind_spots")), "",
    ]
    return "\n".join(parts).rstrip() + "\n"
