"""report_data schema（spec 2026-08-26-report-generation-agent-design §4）。

三轨（whitebox/blackbox/combined）统一的报告 SSOT：管线内确定性组装
（agent 富化写回 queue SSOT 之后）产 ``report_data.json``；md 导出与
web 前端渲染都吃同一份。schema 是纯数据契约——聚合（stats）在组装器
（services/report_data_builder.py）算，模型本身无行为。

设计要点：
- agent 产物字段（executive_summary/qa/poc/endpoints 结构化）全部可缺省
  ——组装时 LLM 步骤可能未跑/失败（每步独立降级），报告永远完整产出；
- ``ReportVulnerability.raw`` 保留原始 queue entry dict——md 导出复用
  findings_renderer.render_vuln_card（零视觉回归），JSON 消费方忽略它；
- ``externally_exploitable`` 是可达性标签（CLAUDE.md §1 铁律）：schema
  只承载不解释，agent 富化白名单在写入侧约束（不覆写）。
"""
from typing import Any, Literal

from pydantic import BaseModel, Field


class ScanMeta(BaseModel):
    """扫描元信息（activity 层从 session/输入组装传入）。"""

    id: str
    track: Literal["whitebox", "blackbox", "combined"]
    repo: str | None = None
    date: str | None = None
    duration_ms: int | None = None
    cost: float | None = None
    currency: str | None = None
    model: str | None = None


class EndpointEntry(BaseModel):
    """接口一体表行（spec §4 ★核心诉求）：接口 + 参数 + 认证 + 三处行号。

    ``route_registered_at`` / ``source_location`` / ``sink_location`` 为
    ``file:line`` 形态；组装期（T1）从现有 queue 字段确定性派生，富化期
    （T3）由 agent 素材包补全。
    """

    method: str | None = None
    path: str
    role: str | None = None          # write/trigger/read
    auth: str | None = None          # isLoggedIn/public/isAdmin
    params: list[str] = Field(default_factory=list)
    route_registered_at: str | None = None
    source_location: str | None = None
    sink_location: str | None = None


class PocRequest(BaseModel):
    """完整可复现 HTTP 请求（T4 agent 产物；黑盒为实际发出的请求）。"""

    method: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None


class PocExpectedResponse(BaseModel):
    """预期响应特征（判定依据）。"""

    indicator: str
    success_criteria: str | None = None


class PocBlock(BaseModel):
    """POC 块——双轨共用模型（ReportVulnerability.poc 单一字段类型）。

    白盒（spec 2026-08-27-poc-agent-direct-design）：poc-agent 直产文本透传——
    curl/raw_http/steps/self_check("pass"|"fail")/notes 全是 agent 原文，渲染层
    不改写不重排版；expected_response 为 str。

    黑盒（重放证据转录，spec 非目标不动）：request 对象 + expected_response
    对象（PocExpectedResponse）+ curl/raw_http 由 request 确定性渲染。
    两轨字段互不填充；渲染端对 expected_response 兼容 str | PocExpectedResponse
    （对象取 .indicator）。白盒旧字段（witness_payload）已随确定性拼装退役。
    """
    curl: str | None = None
    raw_http: str | None = None
    steps: list[str] = Field(default_factory=list)
    preconditions: str | None = None
    self_check: str | None = None
    notes: str | None = None
    # 黑盒专属（白盒不产）
    request: PocRequest | None = None
    expected_response: "str | PocExpectedResponse | None" = None


class VulnNarrative(BaseModel):
    """卡片叙事三段（cause=漏洞成因/impact=危害/remediation=修复建议）。"""

    cause: str | None = None
    impact: str | None = None
    remediation: str | None = None


class ProblemPoint(BaseModel):
    """问题点三要素（spec 2026-08-26-vuln-card-seven-sections §3 节 3）：
    位置 + 说明 + 代码片段。endpoint 富化 agent 读源码产出，写回 queue 的
    ``report_problem_points``（builder 纯透传，不合成不推断）。"""

    location: str
    description: str | None = None
    snippet: str | None = None


class VerifyStep(BaseModel):
    """黑盒验证单步（验证证据展示优化，2026-08-27）：生成层结构化。

    黑盒 exploit verdict 的 ``exploitation_steps`` 逐字映射——报告（黑盒 + 融合）
    天然分步骤；``command`` 独立字段（实际执行的完整命令，可复制人工复验），
    渲染层直取进代码块，不靠正则从散文反解。
    """

    action: str                            # 这步做了什么（短散文）
    command: str | None = None             # 实际命令（含认证上下文，可重放）
    result: str | None = None              # 观察到的结果


class VulnEvidence(BaseModel):
    verification: Literal["static", "dynamic"] = "static"
    dynamic_evidence: str | None = None    # 黑盒实测输出；白盒为 None
    # 黑盒验证步骤（新采集结构化 / 旧落盘归一化）；白盒 static 轨为空列表。
    steps: list[VerifyStep] = Field(default_factory=list)
    verdict: str | None = None
    code_snippet: str | None = None
    notes: str | None = None


class ReportVulnerability(BaseModel):
    """报告漏洞卡（queue SSOT 条目的报告视图超集）。"""

    id: str
    type: str                           # injection/xss/ssrf/auth/authz
    vulnerability_type: str | None = None
    title: str | None = None
    severity: str | None = None         # critical/high/medium/low（effective 后）
    confidence: str | None = None       # high/needs_review/unadjudicated
    cvss: str | None = None
    cwe_id: str | None = None
    owasp_category: str | None = None
    externally_exploitable: bool | None = None
    authentication_required: str | None = None
    merge_source: str | None = None     # both/llm-only/gitnexus-only
    merged_from: list[str] = Field(default_factory=list)   # ①归并终审产物
    narrative: VulnNarrative | None = None
    problem_points: list[ProblemPoint] = Field(default_factory=list)
    endpoints: list[EndpointEntry] = Field(default_factory=list)
    affected_entries: list[dict[str, Any]] = Field(default_factory=list)
    dataflow_steps: list[dict[str, Any]] = Field(default_factory=list)
    poc: PocBlock | None = None
    evidence: VulnEvidence | None = None
    attack_chain_refs: list[str] = Field(default_factory=list)
    # T8 融合版专属（spec §6.2）：白盒发现 × 黑盒实测三态 + 黑盒独有第四态；
    # 白盒/黑盒单轨产物为 None。
    cross_verification: Literal[
        "verified", "untested", "failed-to-verify", "blackbox-only"] | None = None
    # 原始 queue entry（md 导出复用 render_vuln_card；JSON 消费方忽略）
    raw: dict[str, Any] | None = None


class TopRisk(BaseModel):
    vuln_id: str
    reason: str | None = None
    priority: Literal["P0", "P1"] | None = None


class ExecutiveSummary(BaseModel):
    """④执行摘要 agent 产物（组装期缺省；LLM 失败回退确定性摘要）。"""

    narrative: str | None = None
    risk_level: str | None = None
    top_risks: list[TopRisk] = Field(default_factory=list)
    remediation_order: str | None = None


class TypeStats(BaseModel):
    count: int = 0
    severity_range: str | None = None
    key_findings: str | None = None


class ReportStats(BaseModel):
    """确定性聚合（组装器算）——替代前端 report-stats 的推断/零计数补全。"""

    by_type: dict[str, TypeStats] = Field(default_factory=dict)
    by_severity: dict[str, int] = Field(default_factory=dict)


class QACheck(BaseModel):
    check: str
    failed_ids: list[str] = Field(default_factory=list)


class ReportQA(BaseModel):
    """⑤QA agent 产物：失败不阻塞（qa.passed=false 显式呈现）。"""

    passed: bool = True
    checks: list[QACheck] = Field(default_factory=list)
    reworked_ids: list[str] = Field(default_factory=list)


class AttackChainEntry(BaseModel):
    id: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    narrative: str | None = None


class QuickReferenceRow(BaseModel):
    """漏洞速查表行（spec 2026-08-26-report-single-source-rendering §5）：
    builder 从 vulnerabilities + affected_parameters 确定性产；前端与 md
    都只渲染不派生（守「渲染层纯渲染」——md 现速查表从 queue 现算的口径
    收编进 schema）。"""

    id: str
    title: str | None = None
    params: list[str] = Field(default_factory=list)
    endpoints: list[str] = Field(default_factory=list)
    severity: str | None = None
    verification: str | None = None
    confidence: str | None = None


class ReportData(BaseModel):
    schema_version: int = 1
    scan: ScanMeta
    executive_summary: ExecutiveSummary | None = None
    stats: ReportStats | None = None
    vulnerabilities: list[ReportVulnerability] = Field(default_factory=list)
    attack_chains: list[AttackChainEntry] = Field(default_factory=list)
    quick_reference: list[QuickReferenceRow] = Field(default_factory=list)
    qa: ReportQA | None = None
    # T8 融合版专属：白盒发现黑盒未覆盖清单 [{vuln_id, reason}]
    verification_gaps: list[dict[str, Any]] = Field(default_factory=list)
