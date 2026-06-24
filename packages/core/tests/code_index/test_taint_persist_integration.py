# packages/core/tests/code_index/test_taint_persist_integration.py
from shannon_core.code_index import write_index_files
from shannon_core.code_index.models import CodeIndex
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    ParameterSource,
    TaintFlow,
)


def _minimal_index(pgraph):
    return CodeIndex(
        repository="r", language="python", total_blocks=0, total_entry_points=0,
        total_chains=0, blocks=[], edges=[], entry_points=[], chains=[],
        parameter_graph=pgraph,
    )


def test_persisted_parameter_graph_round_trips_through_disk(tmp_path):
    """P0 闭环: write 落盘的 parameter_graph.json 能被下游 model_validate_json 读回。"""
    flow = TaintFlow(
        entry_point_id="app.py:handler:1",
        source_param="q",
        source_type=ParameterSource.QUERY_PARAM,
    )
    pgraph = ParameterPropagationGraph(taint_flows=[flow], language_coverage=["python"])
    index = _minimal_index(pgraph)

    write_index_files(index, str(tmp_path))

    pgraph_path = tmp_path / "parameter_graph.json"
    assert pgraph_path.exists()

    # 下游 run_risk_scoring / run_render_dataflow_hints 的读取方式
    restored = ParameterPropagationGraph.model_validate_json(pgraph_path.read_text())
    assert len(restored.taint_flows) == 1
    assert restored.taint_flows[0].source_param == "q"
