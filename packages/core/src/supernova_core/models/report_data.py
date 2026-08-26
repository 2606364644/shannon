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
    witness_payload: str | None = None
    request: PocRequest | None = None
    preconditions: str | None = None
    expected_response: PocExpectedResponse | None = None
    # curl/raw_http 由 request 确定性生成（导出/复制用）
    curl: str | None = None
    raw_http: str | None = None


class VulnNarrative(BaseModel):
    """卡片叙事三段（cause=漏洞成因/impact=危害/remediation=修复建议）。"""

    cause: str | None = None
    impact: str | None = None
    remediation: str | None = None


class VulnEvidence(BaseModel):
    verification: Literal["static", "dynamic"] = "static"
    dynamic_evidence: str | None = None    # 黑盒实测输出；白盒为 None
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
    endpoints: list[EndpointEntry] = Field(default_factory=list)
    affected_entries: list[dict[str, Any]] = Field(default_factory=list)
    dataflow_steps: list[dict[str, Any]] = Field(default_factory=list)
    poc: PocBlock | None = None
    evidence: VulnEvidence | None = None
    attack_chain_refs: list[str] = Field(default_factory=list)
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


class ReportData(BaseModel):
    schema_version: int = 1
    scan: ScanMeta
    executive_summary: ExecutiveSummary | None = None
    stats: ReportStats | None = None
    vulnerabilities: list[ReportVulnerability] = Field(default_factory=list)
    attack_chains: list[AttackChainEntry] = Field(default_factory=list)
    qa: ReportQA | None = None
