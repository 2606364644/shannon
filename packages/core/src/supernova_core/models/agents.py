from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict

VulnType = Literal["injection", "xss", "auth", "ssrf", "authz"]

# 关 LLM 轨(SUPERNOVA_LLM_TRACK_ENABLED=0)时关闭的 vuln 类: taint 类, GitNexus
# chain_verdict 是主干兜底(memory dual-track-gitnexus-is-main-track)。authz/auth
# 隐式「不可降级」: GitNexus 只做 IDOR(不覆盖 Vertical/Context), auth 无确定性轨。
# recon/pre-recon LLM 也不在此(它们是 authz Vertical/Context 的输入链, GitNexus 不产)。
# 详见 plan smooth-wandering-dolphin + test_workflows_llm_track_gating.py。
DEGRADABLE_VULN_CLASSES: tuple[VulnType, ...] = ("injection", "xss", "ssrf")

class AgentName(str, Enum):
    PRE_RECON = "pre-recon"
    RECON = "recon"
    INJECTION_VULN = "injection-vuln"
    XSS_VULN = "xss-vuln"
    AUTH_VULN = "auth-vuln"
    SSRF_VULN = "ssrf-vuln"
    AUTHZ_VULN = "authz-vuln"
    RECON_BLACKBOX = "recon-blackbox"
    INJECTION_EXPLOIT = "injection-exploit"
    XSS_EXPLOIT = "xss-exploit"
    AUTH_EXPLOIT = "auth-exploit"
    SSRF_EXPLOIT = "ssrf-exploit"
    AUTHZ_EXPLOIT = "authz-exploit"
    REPORT = "report"
    VALIDATE_AUTH = "validate-authentication"
    CROSS_REPO_CORRELATION = "cross-repo-correlation"
    CROSS_REPO_ADJUDICATION = "cross-repo-adjudication"  # spec 2026-08-27 阶段 B 跨仓裁决
    CROSS_REPO_TOPOLOGY_DISCOVERY = "cross-repo-topology-discovery"
    ATTACK_CHAIN = "attack-chain"
    ENDPOINT_VERIFY = "endpoint-verify"  # spec 2026-08-03 黑盒端点 live 验证

class AgentDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: AgentName
    display_name: str
    prerequisites: list[AgentName]
    prompt_template: str
    deliverable_filename: str | None = None
    model_tier: Literal["small", "medium", "large"] = "medium"

AGENTS: dict[AgentName, AgentDefinition] = {
    AgentName.PRE_RECON: AgentDefinition(
        name=AgentName.PRE_RECON,
        display_name="Pre-recon agent",
        prerequisites=[],
        prompt_template="pre-recon-code",
        deliverable_filename="pre_recon_deliverable.md",
        model_tier="large",
    ),
    AgentName.RECON: AgentDefinition(
        name=AgentName.RECON,
        display_name="Recon agent",
        prerequisites=[AgentName.PRE_RECON],
        prompt_template="recon-static",
        deliverable_filename="recon_deliverable.md",
    ),
    AgentName.INJECTION_VULN: AgentDefinition(
        name=AgentName.INJECTION_VULN,
        display_name="Injection vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-injection",
        deliverable_filename="injection_analysis_deliverable.md",
    ),
    AgentName.XSS_VULN: AgentDefinition(
        name=AgentName.XSS_VULN,
        display_name="XSS vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-xss",
        deliverable_filename="xss_analysis_deliverable.md",
    ),
    AgentName.AUTH_VULN: AgentDefinition(
        name=AgentName.AUTH_VULN,
        display_name="Auth vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-auth",
        deliverable_filename="auth_analysis_deliverable.md",
    ),
    AgentName.SSRF_VULN: AgentDefinition(
        name=AgentName.SSRF_VULN,
        display_name="SSRF vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-ssrf",
        deliverable_filename="ssrf_analysis_deliverable.md",
    ),
    AgentName.AUTHZ_VULN: AgentDefinition(
        name=AgentName.AUTHZ_VULN,
        display_name="Authz vuln agent",
        prerequisites=[AgentName.RECON],
        prompt_template="vuln-authz",
        deliverable_filename="authz_analysis_deliverable.md",
    ),
    AgentName.RECON_BLACKBOX: AgentDefinition(
        name=AgentName.RECON_BLACKBOX,
        display_name="Reconnaissance (Black-Box)",
        prerequisites=[],
        prompt_template="recon-blackbox",
        deliverable_filename="recon_deliverable.md",
    ),
    AgentName.INJECTION_EXPLOIT: AgentDefinition(
        name=AgentName.INJECTION_EXPLOIT,
        display_name="Injection Exploitation",
        prerequisites=[AgentName.RECON],
        prompt_template="injection-exploit",
        deliverable_filename="injection_exploitation_evidence.md",
    ),
    AgentName.XSS_EXPLOIT: AgentDefinition(
        name=AgentName.XSS_EXPLOIT,
        display_name="XSS Exploitation",
        prerequisites=[AgentName.RECON],
        prompt_template="xss-exploit",
        deliverable_filename="xss_exploitation_evidence.md",
    ),
    AgentName.AUTH_EXPLOIT: AgentDefinition(
        name=AgentName.AUTH_EXPLOIT,
        display_name="Auth Exploitation",
        prerequisites=[AgentName.RECON],
        prompt_template="auth-exploit",
        deliverable_filename="auth_exploitation_evidence.md",
    ),
    AgentName.SSRF_EXPLOIT: AgentDefinition(
        name=AgentName.SSRF_EXPLOIT,
        display_name="SSRF Exploitation",
        prerequisites=[AgentName.RECON],
        prompt_template="ssrf-exploit",
        deliverable_filename="ssrf_exploitation_evidence.md",
    ),
    AgentName.AUTHZ_EXPLOIT: AgentDefinition(
        name=AgentName.AUTHZ_EXPLOIT,
        display_name="Authz Exploitation",
        prerequisites=[AgentName.RECON],
        prompt_template="authz-exploit",
        deliverable_filename="authz_exploitation_evidence.md",
    ),
    AgentName.REPORT: AgentDefinition(
        name=AgentName.REPORT,
        display_name="Report Generator",
        prerequisites=[AgentName.INJECTION_EXPLOIT, AgentName.XSS_EXPLOIT,
                        AgentName.AUTH_EXPLOIT, AgentName.SSRF_EXPLOIT,
                        AgentName.AUTHZ_EXPLOIT],
        prompt_template="report-executive",
        deliverable_filename="comprehensive_security_assessment_report.md",
    ),
    AgentName.VALIDATE_AUTH: AgentDefinition(
        name=AgentName.VALIDATE_AUTH,
        display_name="Authentication Validation",
        prerequisites=[],
        prompt_template="validate-authentication",
        deliverable_filename=None,
        model_tier="medium",
    ),
    AgentName.CROSS_REPO_CORRELATION: AgentDefinition(
        name=AgentName.CROSS_REPO_CORRELATION,
        display_name="Cross-Repo Correlation",
        prerequisites=[],  # 关联由编排器在外部触发,不在单仓流水线内
        prompt_template="cross-repo-correlation",
        deliverable_filename=None,  # 产物由编排器从 LLM 输出解析落盘
        model_tier="large",
    ),
    AgentName.CROSS_REPO_ADJUDICATION: AgentDefinition(
        name=AgentName.CROSS_REPO_ADJUDICATION,
        display_name="Cross-Repo Adjudication",
        prerequisites=[],  # 阶段 B 裁决由编排器在关联产物落盘后触发(spec 2026-08-27 §7.1)
        prompt_template="cross-repo-adjudication",
        deliverable_filename=None,  # adjudication-log 由编排器从 LLM 输出解析落盘
        model_tier="large",
    ),
    AgentName.CROSS_REPO_TOPOLOGY_DISCOVERY: AgentDefinition(
        name=AgentName.CROSS_REPO_TOPOLOGY_DISCOVERY,
        display_name="Cross-Repo Topology Discovery",
        prerequisites=[],
        prompt_template="cross-repo-topology-discovery",
        deliverable_filename=None,
        model_tier="large",
    ),
    AgentName.ATTACK_CHAIN: AgentDefinition(
        name=AgentName.ATTACK_CHAIN,
        display_name="Attack Chain Analysis",
        prerequisites=[AgentName.INJECTION_VULN, AgentName.XSS_VULN,
                       AgentName.SSRF_VULN, AgentName.AUTHZ_VULN],
        prompt_template="attack-chain",
        deliverable_filename=None,   # 产 queue（attack_chains_llm_queue.json），不产 md
        model_tier="medium",
    ),
    AgentName.ENDPOINT_VERIFY: AgentDefinition(
        name=AgentName.ENDPOINT_VERIFY,
        display_name="Endpoint Live Verification (Black-Box)",
        prerequisites=[],  # 黑盒 exploitation 内阶段,不参与白盒 DAG
        prompt_template="blackbox-endpoint-verify",
        deliverable_filename=None,  # 产 endpoint_verify.json(activity 自落盘 blackbox/),非 md
        model_tier="medium",
    ),
}

ALL_VULN_CLASSES: list[VulnType] = ["injection", "xss", "auth", "ssrf", "authz"]

BROWSER_SESSION_MAPPING: dict[str, str] = {name.value: f"agent{i}" for i, name in enumerate(AgentName, 1)}
# VALIDATE_AUTH shares agent1 slot (same browser session as preflight)
BROWSER_SESSION_MAPPING[AgentName.VALIDATE_AUTH.value] = "agent1"

# Backward-compatible alias
PLAYWRIGHT_SESSION_MAPPING = BROWSER_SESSION_MAPPING

AGENT_PHASE_MAP: dict[str, str] = {
    "pre-recon": "pre-recon",
    "recon": "recon",
    "injection-vuln": "vulnerability-analysis",
    "xss-vuln": "vulnerability-analysis",
    "auth-vuln": "vulnerability-analysis",
    "ssrf-vuln": "vulnerability-analysis",
    "authz-vuln": "vulnerability-analysis",
    # GN 深判/富化 agent（run_gitnexus_verdict_agent 记账，2026-08-27 修成本
    # 漏记）：authz judge 跑在 vulnerability-analysis 相。富化唯一名
    # （gn-enrich-*/endpoint-enrich-*）不进 map——不聚合 phase，只进 agents+totals。
    "gitnexus-verdict": "vulnerability-analysis",
    # 轻量单次调用记账（spec 2026-08-27 §8，AccountedLlmClient finalize 名）——
    # 此前这批调用的 cost 被闭包剥 str 时整笔丢弃，session 总账不可见。
    "track-parity": "vulnerability-analysis",
    "poc-gapfill": "exploitation",
    "expected-response": "exploitation",
    "report-summary": "reporting",
    "recon-summary": "recon",
    "recon-blackbox": "recon",
    "injection-exploit": "exploitation",
    "xss-exploit": "exploitation",
    "auth-exploit": "exploitation",
    "ssrf-exploit": "exploitation",
    "authz-exploit": "exploitation",
    "report": "reporting",
    "validate-authentication": "pre-recon",
    "cross-repo-correlation": "correlation",
    # 在途阶段 B（跨仓裁决）枚举补映射——对齐相邻 correlation 语义（枚举已入
    # AgentName 但 map 漏，test_all_agent_names_have_phase_mapping 红）。
    "cross-repo-adjudication": "correlation",
    "cross-repo-topology-discovery": "correlation",
    AgentName.ATTACK_CHAIN: "attack-chain",
    "endpoint-verify": "endpoint-verify",
}
