"""Integration test verifying the CODE_INDEX → PRE_RECON handoff.

This test creates a fixture repo, runs build_code_index on it,
and verifies that the output files are suitable for PRE_RECON consumption.
"""
import json
from pathlib import Path

import pytest

from shannon_core.code_index import build_code_index, write_index_files


@pytest.fixture
def flask_repo(tmp_path):
    """Create a Flask-style Python repository."""
    (tmp_path / "app.py").write_text(
        'from flask import Flask, request, jsonify\n'
        '\n'
        'app = Flask(__name__)\n'
        '\n'
        '@app.route("/api/users", methods=["GET"])\n'
        'def list_users():\n'
        '    users = get_users()\n'
        '    return jsonify(users)\n'
        '\n'
        '@app.route("/api/users/<int:user_id>", methods=["POST"])\n'
        'def update_user(user_id):\n'
        '    data = request.get_json()\n'
        '    result = save_user(user_id, data)\n'
        '    return jsonify(result)\n'
        '\n'
        'def get_users():\n'
        '    return db_query("SELECT * FROM users")\n'
        '\n'
        'def save_user(user_id, data):\n'
        '    return db_update("users", user_id, data)\n'
        '\n'
        'def db_query(sql):\n'
        '    return []\n'
        '\n'
        'def db_update(table, id, data):\n'
        '    return {}\n'
    )
    return tmp_path


class TestCodeIndexToPreReconHandoff:
    def test_code_index_json_is_valid_pre_recon_input(self, flask_repo, tmp_path):
        """Verify code_index.json can be loaded and has required structure."""
        index = build_code_index(str(flask_repo))
        output_dir = tmp_path / "deliverables"
        json_path, summary_path = write_index_files(index, str(output_dir))

        data = json.loads(json_path.read_text())

        # Pre-recon expects these top-level keys
        assert "blocks" in data
        assert "edges" in data
        assert "entry_points" in data
        assert "chains" in data
        assert "total_blocks" in data
        assert "total_entry_points" in data
        assert "total_chains" in data

    def test_summary_has_all_three_sections(self, flask_repo, tmp_path):
        """Verify the summary has Entry Points, Needs Review, and Coverage."""
        index = build_code_index(str(flask_repo))
        output_dir = tmp_path / "deliverables"
        _, summary_path = write_index_files(index, str(output_dir))

        content = summary_path.read_text()
        assert "## Entry Points" in content
        assert "## Entry Points Needing LLM Review" in content
        assert "## Coverage Metrics" in content

    def test_entry_points_include_flask_routes(self, flask_repo):
        """Verify Flask routes are detected as entry points."""
        index = build_code_index(str(flask_repo))

        routes = [
            (ep.route, ep.http_method)
            for ep in index.entry_points
            if ep.entry_type == "http_route"
        ]
        assert ("/api/users", "GET") in routes
        assert ("/api/users/<int:user_id>", "POST") in routes

    def test_call_chains_reach_db_functions(self, flask_repo):
        """Verify call chains reach the database layer."""
        index = build_code_index(str(flask_repo))

        all_funcs_in_chains = set()
        for chain in index.chains:
            for block_id in chain.path:
                all_funcs_in_chains.add(block_id)

        has_db = any("db_query" in bid or "db_update" in bid for bid in all_funcs_in_chains)
        assert has_db, f"Expected db_query/db_update in chains. Got: {all_funcs_in_chains}"

    def test_no_entry_points_proceeds_gracefully(self, tmp_path):
        """Verify that a repo with no entry points still produces valid output."""
        (tmp_path / "utils.py").write_text(
            'def helper(x):\n'
            '    return x * 2\n'
        )
        index = build_code_index(str(tmp_path))
        assert index.total_entry_points == 0
        assert index.total_blocks >= 1
        from shannon_core.code_index.summary import generate_summary
        summary = generate_summary(index)
        assert "No entry points" in summary or "_No entry points" in summary
