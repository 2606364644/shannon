"""Spec C end-to-end: Spec B/A products → static_dataflow_hints.md → prompt."""
from pathlib import Path

from shannon_core.code_index.audit_input_builder import build_static_dataflow_hints
from shannon_core.code_index.models import CallChain, CodeIndex, ParameterSource
from shannon_core.code_index.parameter_models import (
    DangerousSlot, ParameterPropagationGraph, PropagationStep, SinkCallSite,
    SinkCategory, SlotContext, TaintFlow,
)
from shannon_core.code_index.risk_scorer import ChainRiskScore
from shannon_core.code_index.tiered_audit import AuditPlan
from shannon_core.prompts.manager import PromptManager

PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"


def _full_fixture():
    """A tier3 SQLi chain: query param → concat → execute sink, with sanitizer hint."""
    caller = "src/db/user.py:getUser:10"
    chains = [CallChain(entry_point_id=caller, path=[caller],
                        depth=0, has_unresolved=False)]
    index = CodeIndex(
        repository="demo", language="python",
        total_blocks=1, total_entry_points=1, total_chains=1,
        blocks=[], edges=[], entry_points=[], chains=chains,
        sink_call_sites=[
            SinkCallSite(
                id="src/db/user.py:getUser:execute:42:18", caller_id=caller,
                callee_name="execute", callee_receiver="cursor",
                category=SinkCategory.SQL, sink_subtype="sql_raw",
                file_path="src/db/user.py", line=42, column=18,
                dangerous_slots=[DangerousSlot(arg_index=0, slot=SlotContext.SQL_VALUE,
                                               expression="q", is_entry_hint=True)],
                rule_id="py-db-cursor-execute",
            ),
        ],
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=[
            TaintFlow(
                flow_id=f"{caller}->src/db/user.py:getUser:execute:42:18",
                entry_point_id=caller, source_param="uid",
                source_type=ParameterSource.QUERY_PARAM,
                propagation_steps=[
                    PropagationStep(step_id="s1", from_func_id=caller, from_param="uid",
                                    to_func_id=caller, to_param="q",
                                    transformation="concat", code_location="src/db/user.py:40"),
                ],
                sink_call_site_id="src/db/user.py:getUser:execute:42:18",
                sink_slot=SlotContext.SQL_VALUE, tainted_arg_index=0,
                confidence=0.6, has_sanitizer_hint=True,
                notes="容器字段过近似",
            ),
        ],
        language_coverage=["python"], skipped_languages=["go", "java", "php"],
    )
    plan = AuditPlan(
        total_chains=1,
        scores=[ChainRiskScore(chain_id=caller, sink_danger=10,
                               taint_completeness=10, auth_gap=8, depth=2)],  # total=30 → tier3
    )
    return index, pgraph, plan


def test_e2e_md_contains_tier3_sink_and_honest_caveats():
    index, pgraph, plan = _full_fixture()
    md = build_static_dataflow_hints(index, pgraph, plan)
    # tier3 sink 置顶
    assert "Tier 3" in md
    assert "src/db/user.py:42:18" in md
    assert "sql_value" in md
    # 污点流
    assert "src/db/user.py:getUser:10" in md
    assert "concat" in md
    assert "0.60" in md
    # 诚实边界
    assert "sanitize_hint" in md and "不代表有效" in md
    assert "go" in md and "java" in md and "php" in md


def test_e2e_vuln_prompt_renders_hints_block():
    """Non-pipeline-testing vuln-injection prompt contains the static hints guidance."""
    mgr = PromptManager(PROMPTS_DIR)
    rendered = mgr.load_sync("vuln-injection", {"repo_path": "/repo"}, pipeline_testing=False)
    assert "<static_dataflow_hints>" in rendered
    assert "线索，不是结论" in rendered


def test_e2e_pipeline_testing_excludes_hints_block():
    """pipeline-testing mode → no hints block in vuln prompt (Spec §6.4)."""
    mgr = PromptManager(PROMPTS_DIR)
    rendered = mgr.load_sync("vuln-injection", {"repo_path": "/repo"}, pipeline_testing=True)
    assert "<static_dataflow_hints>" not in rendered


def test_e2e_vuln_prompt_preserves_two_layer_model():
    """Regression: the main vuln agent still must delegate code reads to Task agent
    (Spec §4.3 — two-layer model unchanged). The hints block does not replace it."""
    mgr = PromptManager(PROMPTS_DIR)
    rendered = mgr.load_sync("vuln-injection", {"repo_path": "/repo"}, pipeline_testing=False)
    # 原有"禁止主 agent 直接 Read 源码、委派 Task agent"约束仍在
    assert "Task Agent" in rendered or "Task agent" in rendered
    assert "recon_deliverable.md" in rendered  # single source of truth 不变
