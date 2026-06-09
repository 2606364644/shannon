"""Spec A 端到端：build_code_index → param_graph.json 非空。"""
import json
from pathlib import Path

import pytest

from shannon_core.code_index import build_code_index, write_index_files


@pytest.fixture
def flask_repo(tmp_path) -> Path:
    repo = tmp_path / "flaskrepo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def handler(user_id):\n"
        "    q = 'SELECT * FROM u WHERE id=' + user_id\n"
        "    process(q)\n"
        "def process(sql):\n"
        "    cursor.execute(sql)\n"
    )
    return repo


class TestPropagationEndToEnd:
    def test_param_graph_json_written_and_nonempty(self, flask_repo, tmp_path):
        index = build_code_index(str(flask_repo))
        out = tmp_path / "out"
        json_path, _ = write_index_files(index, str(out))
        pgraph_path = out / "parameter_graph.json"
        assert pgraph_path.exists()
        data = json.loads(pgraph_path.read_text())
        assert "taint_flows" in data
        assert "language_coverage" in data
        assert "python" in data["language_coverage"]

    def test_param_graph_includes_sink_call_site_id(self, flask_repo, tmp_path):
        """非空 flow 的 sink_call_site_id 必须形如
        '{file}:{caller_func}:{callee}:{line}:{col}'。"""
        index = build_code_index(str(flask_repo))
        out = tmp_path / "out"
        write_index_files(index, str(out))
        pgraph_path = out / "parameter_graph.json"
        data = json.loads(pgraph_path.read_text())
        for flow in data["taint_flows"]:
            sid = flow["sink_call_site_id"]
            assert sid.count(":") >= 4
            assert flow["sink_slot"] in (
                "sql_value", "sql_identifier", "cmd_argument", "file_path",
                "template_expr", "url", "deserialize", "generic",
            )

    def test_skipped_languages_recorded_for_ts_repo(self, tmp_path):
        """TypeScript repo — coverage=['typescript']，skipped_languages=[]。"""
        repo = tmp_path / "tsrepo"
        repo.mkdir()
        (repo / "service.ts").write_text(
            "function processInput(input: string) {\n"
            "    eval(input);\n"
            "}\n"
        )
        index = build_code_index(str(repo))
        out = tmp_path / "out"
        write_index_files(index, str(out))
        data = json.loads((out / "parameter_graph.json").read_text())
        assert "typescript" in data["language_coverage"]
        assert "go" not in data["language_coverage"]
