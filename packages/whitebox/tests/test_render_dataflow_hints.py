"""Spec C: run_render_dataflow_hints activity —— 生成 static_dataflow_hints.md。"""
import json

import pytest

from shannon_core.code_index.models import CallChain, CodeIndex
from shannon_core.code_index.parameter_models import (
    DangerousSlot, ParameterPropagationGraph, SinkCallSite, SinkCategory,
    SlotContext,
)
from shannon_core.code_index.risk_scorer import ChainRiskScore
from shannon_core.code_index.tiered_audit import AuditPlan
from shannon_whitebox.pipeline import activities
from shannon_whitebox.pipeline.shared import ActivityInput


def _write_fixture(deliverables):
    """Write minimal code_index.json / parameter_graph.json / audit_plan.json."""
    chains = [CallChain(entry_point_id="db.py:h:1", path=["db.py:h:1"],
                        depth=0, has_unresolved=False)]
    index = CodeIndex(
        repository="demo", language="python",
        total_blocks=1, total_entry_points=1, total_chains=1,
        blocks=[], edges=[], entry_points=[], chains=chains,
        sink_call_sites=[
            SinkCallSite(
                id="db.py:h:execute:10:4", caller_id="db.py:h:1",
                callee_name="execute", callee_receiver="cursor",
                category=SinkCategory.SQL, sink_subtype="sql_raw",
                file_path="db.py", line=10, column=4,
                dangerous_slots=[DangerousSlot(arg_index=0, slot=SlotContext.SQL_VALUE,
                                               expression="q", is_entry_hint=True)],
                rule_id="py-db-cursor-execute",
            ),
        ],
    )
    (deliverables / "code_index.json").write_text(index.model_dump_json(indent=2),
                                                   encoding="utf-8")

    pgraph = ParameterPropagationGraph(
        taint_flows=[], language_coverage=["python"], skipped_languages=["go"],
    )
    (deliverables / "parameter_graph.json").write_text(pgraph.model_dump_json(indent=2),
                                                       encoding="utf-8")

    plan = AuditPlan(
        total_chains=1,
        scores=[ChainRiskScore(chain_id="db.py:h:1", sink_danger=10,
                               taint_completeness=10, auth_gap=8, depth=1)],
    )
    (deliverables / "audit_plan.json").write_text(plan.to_json(indent=2),
                                                   encoding="utf-8")


def _make_input(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return ActivityInput(repo_path=str(repo))


def _deliverables(input):
    _, deliverables, _ = activities._get_paths(input)
    deliverables.mkdir(parents=True, exist_ok=True)
    return deliverables


@pytest.mark.asyncio
async def test_writes_static_dataflow_hints_md(tmp_path):
    input = _make_input(tmp_path)
    deliverables = _deliverables(input)
    _write_fixture(deliverables)

    result = await activities.run_render_dataflow_hints(input)

    md_path = deliverables / "static_dataflow_hints.md"
    assert md_path.exists()
    md = md_path.read_text(encoding="utf-8")
    assert "# Static Dataflow Hints" in md
    assert "## Sink 调用点" in md
    assert result["written"] is True


@pytest.mark.asyncio
async def test_skips_in_pipeline_testing_mode(tmp_path):
    input = _make_input(tmp_path)
    input.pipeline_testing_mode = True
    deliverables = _deliverables(input)
    _write_fixture(deliverables)

    result = await activities.run_render_dataflow_hints(input)

    # pipeline-testing 下不写文件
    assert not (deliverables / "static_dataflow_hints.md").exists()
    assert result["written"] is False


@pytest.mark.asyncio
async def test_missing_code_index_returns_not_written(tmp_path):
    input = _make_input(tmp_path)
    deliverables = _deliverables(input)
    # 不写任何 fixture —— code_index.json 不存在

    result = await activities.run_render_dataflow_hints(input)

    assert result["written"] is False
    assert not (deliverables / "static_dataflow_hints.md").exists()
