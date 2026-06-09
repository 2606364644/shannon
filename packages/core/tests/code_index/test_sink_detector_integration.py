"""End-to-end integration tests for Spec B sink detector.

Covers: build_code_index → detect_sinks → sink_call_sites in code_index.json
        → risk_scorer uses SinkCallSite (chain-wide max danger).
"""
import json
from pathlib import Path

import pytest

from shannon_core.code_index import build_code_index, write_index_files
from shannon_core.code_index.models import CallChain
from shannon_core.code_index.parameter_models import SinkCategory
from shannon_core.code_index.risk_scorer import ChainRiskScore


@pytest.fixture
def python_repo(tmp_path) -> Path:
    """Python repo with SQL / Command / SSTI sinks."""
    repo = tmp_path / "pyrepo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "import os\n"
        "def handler(user_input):\n"
        "    cursor.execute(user_input)\n"
        "    os.system('echo ' + user_input)\n"
        "    return render_template_string(user_input)\n"
    )
    return repo


@pytest.fixture
def typescript_repo(tmp_path) -> Path:
    """TypeScript repo with eval + document.write sinks."""
    repo = tmp_path / "tsrepo"
    repo.mkdir()
    (repo / "service.ts").write_text(
        "function processInput(input: string) {\n"
        "    eval(input);\n"
        "    document.write(input);\n"
        "}\n"
    )
    return repo


class TestEndToEndPython:
    def test_python_sinks_detected(self, python_repo):
        index = build_code_index(str(python_repo))
        # Should detect at least: SQL (cursor.execute), Command (os.system), SSTI
        rules = {s.rule_id for s in index.sink_call_sites}
        assert "py-db-cursor-execute" in rules
        assert "py-os-system" in rules
        assert "py-render-template-string" in rules

    def test_code_index_json_serializes_sink_call_sites(self, python_repo, tmp_path):
        index = build_code_index(str(python_repo))
        out = tmp_path / "out"
        json_path, _ = write_index_files(index, str(out))
        data = json.loads(json_path.read_text())
        assert "sink_call_sites" in data
        assert len(data["sink_call_sites"]) >= 3

    def test_sink_call_site_id_format_in_json(self, python_repo, tmp_path):
        index = build_code_index(str(python_repo))
        out = tmp_path / "out"
        json_path, _ = write_index_files(index, str(out))
        data = json.loads(json_path.read_text())
        ids = [s["id"] for s in data["sink_call_sites"]]
        # All ids follow "{file}:{caller_func}:{callee}:{line}:{col}"
        for sid in ids:
            parts = sid.split(":")
            assert len(parts) == 5, f"Bad id format: {sid}"

    def test_risk_scorer_uses_sink_call_sites(self, python_repo):
        """Verify risk_scorer correctly consumes sink_call_sites from index."""
        index = build_code_index(str(python_repo))
        # Build a simple chain: handler → (handler itself is the sink caller)
        handler_block = next(b for b in index.blocks if b.function_name == "handler")
        chain = CallChain(
            entry_point_id=handler_block.id,
            path=[handler_block.id],
            depth=0, has_unresolved=False,
        )
        score = ChainRiskScore.score(
            chain,
            {b.id: b for b in index.blocks},
            [], set(),
            sink_call_sites=index.sink_call_sites,
        )
        # Handler has SQL + Command + SSTI in body → max danger = 10
        assert score.sink_danger == 10


class TestEndToEndTypeScript:
    def test_typescript_sinks_detected(self, typescript_repo):
        index = build_code_index(str(typescript_repo))
        rules = {s.rule_id for s in index.sink_call_sites}
        assert "ts-eval" in rules
        assert "ts-document-write" in rules

    def test_needs_review_propagated(self, typescript_repo):
        """document.write is needs_review_default=True."""
        index = build_code_index(str(typescript_repo))
        xss_sites = [s for s in index.sink_call_sites if s.category == SinkCategory.XSS]
        assert len(xss_sites) >= 1
        assert all(s.needs_review for s in xss_sites)


class TestFalsePositives:
    def test_commented_sink_not_detected(self, tmp_path):
        """Sink in a comment must not trigger a hit (tree-sitter skips comments)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text(
            "# cursor.execute(user_sql)  -- this is commented out\n"
            "def f():\n"
            "    pass\n"
        )
        index = build_code_index(str(repo))
        rules = {s.rule_id for s in index.sink_call_sites}
        # The commented execute is not in a function body, so won't be visited
        # at all. But just to be safe, no SQL hit expected.
        assert "py-db-cursor-execute" not in rules

    def test_variable_named_query_not_hit(self, tmp_path):
        """A variable named `query` (not a call) must not match the SQL rule."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text(
            "def f():\n"
            "    query = 'SELECT * FROM users'  # assignment, not call\n"
            "    return query\n"
        )
        index = build_code_index(str(repo))
        rules = {s.rule_id for s in index.sink_call_sites}
        assert "py-db-cursor-execute" not in rules
