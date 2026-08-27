"""跨仓两阶段 prompt 契约（spec 2026-08-27 §5/§7.4）。

- cross-repo-correlation.txt：artifacts-guide 注入点 + 方法论五步 + vuln_refs 扩展
- cross-repo-adjudication.txt（新）：裁决卡输出契约 + read-only 工具
- PromptManager：{{ARTIFACTS_GUIDE}} 变量渲染
- AgentName.CROSS_REPO_ADJUDICATION 注册
"""
from pathlib import Path

from supernova_core.models.agents import AGENTS, AgentName
from supernova_core.prompts.manager import PromptManager

PROMPTS = Path(__file__).resolve().parents[4] / "prompts"


def test_correlation_prompt_has_artifacts_guide_placeholder():
    t = (PROMPTS / "cross-repo-correlation.txt").read_text(encoding="utf-8")
    assert "{{ARTIFACTS_GUIDE}}" in t


def test_correlation_prompt_methodology_anchors():
    """五步方法论的语义锚（读导读→入口调用点→handler→攻击链→拓扑）。"""
    t = (PROMPTS / "cross-repo-correlation.txt").read_text(encoding="utf-8")
    for kw in ("artifacts-guide", "entry_points", "vuln_id",
               "agent-discovered", "handler"):
        assert kw in t, f"methodology anchor missing: {kw}"


def test_correlation_prompt_vuln_refs_schema_extended():
    t = (PROMPTS / "cross-repo-correlation.txt").read_text(encoding="utf-8")
    assert '"vuln_id"' in t
    assert '"source"' in t


def test_adjudication_prompt_card_contract():
    t = (PROMPTS / "cross-repo-adjudication.txt").read_text(encoding="utf-8")
    for kw in ("direction", "finding_ref", "conclusion", "cross_service_context",
               "analysis_process", "verification_evidence", "reasoning",
               "confidence"):
        assert kw in t, f"card field missing: {kw}"
    # read-only 工具集声明（对齐 §9 引擎兼容约束）
    assert "grep" in t
    # 三向方向值
    for d in ("upgrade", "downgrade", "confirm"):
        assert d in t


def test_adjudication_agent_registered():
    d = AGENTS[AgentName.CROSS_REPO_ADJUDICATION]
    assert d.prompt_template == "cross-repo-adjudication"
    assert d.prerequisites == []      # 阶段 B 由编排器触发，不在单仓流水线


def test_pipeline_testing_stubs_exist():
    assert (PROMPTS / "pipeline-testing" / "cross-repo-adjudication.txt").exists()
    assert (PROMPTS / "pipeline-testing" / "cross-repo-correlation.txt").exists()


def test_interpolate_replaces_artifacts_guide():
    """PromptManager 渲染 artifacts_guide 变量 → {{ARTIFACTS_GUIDE}}。"""
    pm = PromptManager(PROMPTS)
    rendered = pm.load_sync("cross-repo-adjudication",
                            {"artifacts_guide": "GUIDE-BODY-MARKER",
                             "deliverables_path": "/d",
                             "batch_json": "[]",
                             "correlation_context": "{}"})
    assert "GUIDE-BODY-MARKER" in rendered
    assert "{{ARTIFACTS_GUIDE}}" not in rendered
