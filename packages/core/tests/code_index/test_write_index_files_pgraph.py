import json

from shannon_core.code_index import write_index_files
from shannon_core.code_index.models import CodeIndex
from shannon_core.code_index.parameter_models import ParameterPropagationGraph


def _minimal_index(**overrides):
    return CodeIndex(
        repository="r", language="python", total_blocks=0, total_entry_points=0,
        total_chains=0, blocks=[], edges=[], entry_points=[], chains=[], **overrides,
    )


def test_write_index_files_writes_parameter_graph_when_present(tmp_path):
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])
    index = _minimal_index(parameter_graph=pgraph)
    json_path, summary_path = write_index_files(index, str(tmp_path))
    pgraph_path = tmp_path / "parameter_graph.json"
    assert pgraph_path.exists()
    data = json.loads(pgraph_path.read_text())
    assert data["language_coverage"] == ["python"]
    assert json_path.name == "code_index.json"
    assert summary_path.name == "code_index_summary.md"


def test_write_index_files_skips_parameter_graph_when_none(tmp_path):
    index = _minimal_index()
    write_index_files(index, str(tmp_path))
    assert not (tmp_path / "parameter_graph.json").exists()


def test_write_index_files_removes_stale_parameter_graph_when_none(tmp_path):
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])
    write_index_files(_minimal_index(parameter_graph=pgraph), str(tmp_path))

    pgraph_path = tmp_path / "parameter_graph.json"
    assert pgraph_path.exists()

    write_index_files(_minimal_index(), str(tmp_path))

    assert not pgraph_path.exists()
