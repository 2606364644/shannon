from supernova_core.code_index.models import CodeIndex
from supernova_core.code_index.parameter_models import ParameterPropagationGraph


def _minimal_index(**overrides):
    return CodeIndex(
        repository="r",
        language="python",
        total_blocks=0,
        total_entry_points=0,
        total_chains=0,
        blocks=[],
        edges=[],
        entry_points=[],
        chains=[],
        **overrides,
    )


def test_code_index_parameter_graph_defaults_none():
    index = _minimal_index()
    assert index.parameter_graph is None


def test_code_index_round_trips_parameter_graph():
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])
    index = _minimal_index(parameter_graph=pgraph)
    restored = CodeIndex.model_validate_json(index.model_dump_json())
    assert restored.parameter_graph is not None
    assert restored.parameter_graph.language_coverage == ["python"]
