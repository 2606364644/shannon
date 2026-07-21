"""Plan 3 Task 3 — registry 注册 5 个 vuln agent 的 TDD。

测真实的工厂接口（Plan 1 落地的是两个独立工厂，没有 CollectorSpec 对象）：
- ``make_collector(agent_name) -> CollectorBase | None``
- ``render_deliverable(agent_name, data) -> str | None``

5 个 vuln AgentName 经 ``endswith("-vuln")`` + ``removesuffix("-vuln")`` 派生 vc，
两边工厂共用同一派生逻辑（无字典、无跨模块 import、无漂移）。

并发护栏：Plan 2/recon 也会改这两个 ``__init__.py``，故此文件名独立
（``test_vuln_registry.py`` vs Plan 2 的 ``test_registry.py``），互不踩。
"""
import pytest

from supernova_core.collectors import make_collector
from supernova_core.collectors.base import CollectorBase
from supernova_core.collectors.pre_recon import PreReconCollector
from supernova_core.models.agents import AgentName
from supernova_core.renderers import render_deliverable

# 与 collectors/vuln.py / renderers/vuln.py 对齐的 4 个 set_* 工具名
EXPECTED_TOOL_NAMES = [
    "set_findings_summary",
    "set_strategic_intelligence",
    "set_safe_vectors",
    "set_blind_spots",
]

# (AgentName, vuln_class, renderer 标题) — 标题来自 renderers/vuln.py::TITLES
VULN_AGENTS = [
    (AgentName.INJECTION_VULN, "injection", "Injection Analysis Report"),
    (AgentName.XSS_VULN, "xss", "Cross-Site Scripting (XSS) Analysis Report"),
    (AgentName.AUTH_VULN, "auth", "Authentication Analysis Report"),
    (AgentName.SSRF_VULN, "ssrf", "SSRF Analysis Report"),
    (AgentName.AUTHZ_VULN, "authz", "Authorization Analysis Report"),
]


# ── make_collector: 5 个 vuln agent 各返回带 4 set_* 工具的 CollectorBase ─────
@pytest.mark.parametrize("agent,vc,_title", VULN_AGENTS)
def test_make_collector_returns_collectorbase_for_each_vuln_agent(agent, vc, _title):
    c = make_collector(agent)
    assert c is not None, f"{agent} returned None"
    assert isinstance(c, CollectorBase)
    assert c.tool_names() == EXPECTED_TOOL_NAMES


@pytest.mark.parametrize("agent,vc,_title", VULN_AGENTS)
def test_make_collector_section_keys_match_vuln_contract(agent, vc, _title):
    c = make_collector(agent)
    assert [s.section_key for s in c.section_schemas] == [
        "findings_summary",
        "strategic_intelligence",
        "safe_vectors",
        "blind_spots",
    ]


# ── render_deliverable: 5 个 vuln agent 各返回非 None md 且含 class 标题 ──────
@pytest.mark.parametrize("agent,vc,title", VULN_AGENTS)
def test_render_deliverable_returns_md_with_class_title(agent, vc, title):
    md = render_deliverable(
        agent, {"findings_summary": {"key_outcome": "x", "patterns": []}}
    )
    assert md is not None, f"{agent} returned None"
    assert isinstance(md, str)
    assert title in md  # 标题(来自 renderers/vuln.py::TITLES)证实按 class branching


@pytest.mark.parametrize("agent,vc,title", VULN_AGENTS)
def test_render_deliverable_placeholder_when_section_skipped(agent, vc, title):
    """缺 section 不 fail,renderer 补 placeholder（对齐 pre-recon 契约）。"""
    md = render_deliverable(agent, {})
    assert md is not None
    assert title in md
    # Section 1 + Section 3 应出现 placeholder 文本
    assert "set_findings_summary" in md
    assert "set_strategic_intelligence" in md


# ── 工厂一致性：make_collector 非 None ⇒ render_deliverable 必非 None ─────────
# （executor.py:167-170 依赖：collector 通道开 → renderer 必配套，否则 md 永远跳写盘）
@pytest.mark.parametrize("agent,vc,_title", VULN_AGENTS)
def test_both_factories_agree_for_vuln_agents(agent, vc, _title):
    c = make_collector(agent)
    md = render_deliverable(agent, c.get_all() if c else {})
    assert c is not None and md is not None


# ── per-class strategic_intelligence schema 经 registry 取回仍是各自的 schema ─
def test_strategic_intel_schema_differs_per_class_via_registry():
    """经 registry 分发后，injection 的 set_strategic_intelligence section 仍携带
    INJECTION_STRATEGIC_INTEL，authz 的仍携带 AUTHZ_STRATEGIC_INTEL（per-class
    branching 不漂移）。"""
    from supernova_core.collectors.vuln import (
        AUTHZ_STRATEGIC_INTEL,
        INJECTION_STRATEGIC_INTEL,
    )

    inj = make_collector(AgentName.INJECTION_VULN)
    az = make_collector(AgentName.AUTHZ_VULN)
    assert inj is not None and az is not None

    inj_intel = next(
        s for s in inj.section_schemas if s.tool_name == "set_strategic_intelligence"
    )
    az_intel = next(
        s for s in az.section_schemas if s.tool_name == "set_strategic_intelligence"
    )
    assert inj_intel.json_schema is INJECTION_STRATEGIC_INTEL
    assert az_intel.json_schema is AUTHZ_STRATEGIC_INTEL
    assert inj_intel.json_schema is not az_intel.json_schema


def test_make_collector_pre_recon_branch_intact():
    pre = make_collector(AgentName.PRE_RECON)
    assert isinstance(pre, PreReconCollector)


def test_render_deliverable_pre_recon_branch_intact():
    md = render_deliverable(AgentName.PRE_RECON, {"executive_summary": {"text": "x"}})
    assert md is not None and isinstance(md, str)


# ── 回归：非 vuln 也非 pre-recon/exploit 的 agent（如 REPORT）仍 None（兜底分支不动） ───
def test_make_collector_unwired_agents_still_return_none():
    from supernova_core.models.agents import AgentName as A

    assert make_collector(A.REPORT) is None
    assert make_collector(A.ATTACK_CHAIN) is None
    # exploit agent 走 append collector（Plan 4 Task 3 已接，不再 None）
    assert make_collector(A.INJECTION_EXPLOIT) is not None


def test_render_deliverable_unwired_agents_still_return_none():
    from supernova_core.models.agents import AgentName as A

    assert render_deliverable(A.REPORT, {}) is None
    # exploit agent 有 renderer（Plan 4 Task 3 已接，不再 None）
    assert render_deliverable(A.INJECTION_EXPLOIT, {}) is not None
