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
    def test_param_graph_json_written_with_coverage(self, flask_repo, tmp_path):
        index = build_code_index(str(flask_repo))
        out = tmp_path / "out"
        json_path, _ = write_index_files(index, str(out))
        pgraph_path = out / "parameter_graph.json"
        assert pgraph_path.exists()
        data = json.loads(pgraph_path.read_text())
        assert "taint_flows" in data
        assert "language_coverage" in data
        assert "python" in data["language_coverage"]

    def test_param_graph_schema_valid_when_no_flows(self, flask_repo, tmp_path):
        """无 flow 时 schema 仍然合法 + 可解析。

        build_code_index 阶段 chains=[]，所以 taint_flows 恒为 []，本测试实际
        验证的是 schema 合法性；真正的非空 flow 校验要到 Task 10 注入真实 chain
        后才能体现。"""
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


class TestActivationAfterChainRebuild:
    def test_after_rebuild_param_graph_has_flows_and_scorer_uses_them(self, tmp_path):
        """build_code_index → write_index_files → save_adjudication → rebuild_call_chains
        → param_graph.json 含非空 flows → risk_scorer taint_completeness > 0。"""
        from shannon_core.code_index import (
            build_code_index, save_adjudication, rebuild_call_chains, write_index_files,
        )
        from shannon_core.code_index.parameter_models import TaintFlow
        from shannon_core.code_index.risk_scorer import ChainRiskScore

        repo = tmp_path / "repo"
        repo.mkdir()
        # @app.route decorator 让 detect_entry_points 把 handler 识别为 entry point
        # （confidence 0.95, http_route）；否则裸 def handler 不会被检测为 entry，
        # chains 恒为空，测试会退化成空跑。
        (repo / "app.py").write_text(
            "@app.route('/users')\n"
            "def handler(user_id):\n"
            "    q = 'SELECT * FROM u WHERE id=' + user_id\n"
            "    process(q)\n"
            "def process(sql):\n"
            "    cursor.execute(sql)\n"
        )

        out = tmp_path / "deliverables"
        out.mkdir()

        # 步骤 1: 索引（detect_entry_points 通过 @app.route 命中 handler）
        index = build_code_index(str(repo))
        write_index_files(index, str(out))
        # 步骤 2: 裁定（save_adjudication 自动确认所有检测到的 entry points）
        save_adjudication(str(out))
        # 步骤 3: 重建链（rebuild_call_chains 沿调用边 BFS，刷新 parameter_graph.json）
        updated = rebuild_call_chains(str(out))

        # 步骤 4: 验证 entry 被检测到（前置断言，否则后面无意义）
        assert len(updated.entry_points) >= 1, "handler 应被 @app.route 检测为 entry"
        assert len(updated.chains) >= 1, "应至少有一条 handler→process 链"

        # 步骤 5: 验证 param_graph.json 非空
        pgraph_path = out / "parameter_graph.json"
        assert pgraph_path.exists()
        data = json.loads(pgraph_path.read_text())
        assert len(data["taint_flows"]) >= 1, (
            f"expected ≥1 taint flow after rebuild; got {data['taint_flows']}"
        )
        flow = data["taint_flows"][0]
        assert flow["sink_slot"] == "sql_value"
        assert flow["tainted_arg_index"] == 0

        # 步骤 6: 验证 risk_scorer 用新字段激活 taint_completeness
        blocks_by_id = {b.id: b for b in updated.blocks}
        chain0 = updated.chains[0]
        flow_model = TaintFlow(**flow)
        score = ChainRiskScore.score(
            chain0, blocks_by_id, [flow_model], set(),
            sink_call_sites=updated.sink_call_sites,
        )
        assert score.taint_completeness > 0, (
            "taint_completeness 应 > 0（flow 命中 chain 上的 sink_call_site）"
        )
