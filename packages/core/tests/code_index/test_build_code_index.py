import json
import pytest
from pathlib import Path

from shannon_core.code_index import build_code_index, write_index_files


@pytest.fixture
def python_repo(tmp_path):
    """Create a minimal Python repo with a Flask app."""
    app = tmp_path / "app.py"
    app.write_text(
        'from flask import Flask\n'
        'app = Flask(__name__)\n'
        '\n'
        '@app.route("/hello")\n'
        'def hello():\n'
        '    return greet("world")\n'
        '\n'
        'def greet(name):\n'
        '    return f"Hello {name}"\n'
    )
    return tmp_path


class TestBuildCodeIndex:
    def test_returns_code_index(self, python_repo):
        index = build_code_index(str(python_repo))
        assert index.repository == str(python_repo)
        assert index.language == "python"
        assert index.total_blocks >= 2

    def test_detects_entry_points(self, python_repo):
        index = build_code_index(str(python_repo))
        assert index.total_entry_points >= 1
        ep_names = set()
        for ep in index.entry_points:
            for b in index.blocks:
                if b.id == ep.func_block_id:
                    ep_names.add(b.function_name)
        assert "hello" in ep_names

    def test_defers_call_chains(self, python_repo):
        index = build_code_index(str(python_repo))
        assert index.total_chains == 0
        assert index.chains == []

    def test_resolves_edges(self, python_repo):
        index = build_code_index(str(python_repo))
        resolved = [e for e in index.edges if e.resolved]
        assert len(resolved) >= 1

    def test_blocks_have_valid_ids(self, python_repo):
        index = build_code_index(str(python_repo))
        for block in index.blocks:
            assert ":" in block.id
            assert block.language == "python"

    def test_empty_repo_raises_pentest_error(self, tmp_path):
        from shannon_core.models.errors import PentestError
        with pytest.raises(PentestError, match="No source files"):
            build_code_index(str(tmp_path))


class TestWriteIndexFiles:
    def test_writes_json_and_summary(self, python_repo, tmp_path):
        index = build_code_index(str(python_repo))
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        json_path, summary_path = write_index_files(index, str(output_dir))

        assert json_path.exists()
        assert summary_path.exists()

    def test_json_is_valid(self, python_repo, tmp_path):
        index = build_code_index(str(python_repo))
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        json_path, _ = write_index_files(index, str(output_dir))

        data = json.loads(json_path.read_text())
        assert data["repository"] == str(python_repo)
        assert data["language"] == "python"

    def test_summary_is_markdown(self, python_repo, tmp_path):
        index = build_code_index(str(python_repo))
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        _, summary_path = write_index_files(index, str(output_dir))

        content = summary_path.read_text()
        assert content.startswith("# ")
        assert "Entry Points" in content
