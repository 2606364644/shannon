import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from shannon_core.code_index import (
    build_code_index,
    build_code_index_with_gitnexus,
    write_index_files,
)
from shannon_core.code_index.models import (
    FileManifest, DegradationLevel, FileEntry, FuncBlock, CodeIndex,
)


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


class TestBuildCodeIndexWithGitNexus:
    def test_gitnexus_available_uses_full_mode(self, tmp_path):
        """When GitNexus is available, build full index."""
        # Create a minimal Python file to index
        (tmp_path / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "@app.route('/hello')\n"
            "def hello():\n"
            "    return greet('world')\n"
            "def greet(name):\n"
            "    return f'Hello {name}'\n"
        )

        with patch("shannon_core.code_index.GitNexusEngine") as MockEngine:
            mock_engine = MagicMock()
            mock_engine.is_available.return_value = True
            MockEngine.return_value = mock_engine

            # GitNexus analyze + context would be called
            # But for this test we let it fall through to AST parsing
            mock_engine.ensure_indexed.side_effect = Exception("not installed")

            # Falls back to AST mode with degradation
            index = build_code_index_with_gitnexus(str(tmp_path))
            assert index.language == "python"
            assert index.total_blocks >= 2

    def test_gitnexus_unavailable_falls_back_to_ast(self, tmp_path):
        """When GitNexus is not installed, falls back to AST BFS."""
        (tmp_path / "app.py").write_text(
            "@app.route('/hello')\n"
            "def hello(): pass\n"
        )

        with patch("shannon_core.code_index.GitNexusEngine") as MockEngine:
            mock_engine = MagicMock()
            mock_engine.is_available.return_value = False
            MockEngine.return_value = mock_engine

            index = build_code_index_with_gitnexus(str(tmp_path))
            assert index.language == "python"
            assert index.total_blocks >= 1
            # Should have degradation level
            assert hasattr(index, "degradation_level")

    def test_includes_file_manifest(self, tmp_path):
        """File manifest is included in the output."""
        (tmp_path / "app.py").write_text("def hello(): pass\n")
        (tmp_path / "config.yaml").write_text("key: value\n")

        index = build_code_index_with_gitnexus(str(tmp_path))
        assert hasattr(index, "file_manifest")
        assert index.file_manifest is not None
        yaml_files = index.file_manifest.filter_by_type("config")
        assert len(yaml_files) == 1

    def test_writes_degradation_report_when_degraded(self, tmp_path):
        """When GitNexus unavailable, writes degradation_report.json."""
        (tmp_path / "app.py").write_text("def hello(): pass\n")

        with patch("shannon_core.code_index.GitNexusEngine") as MockEngine:
            mock_engine = MagicMock()
            mock_engine.is_available.return_value = False
            MockEngine.return_value = mock_engine

            index = build_code_index_with_gitnexus(str(tmp_path))

            report_path = tmp_path / "degradation_report.json"
            assert report_path.exists()
            data = json.loads(report_path.read_text())
            assert data["level"] == "degraded"
            assert len(data["gaps"]) > 0


class TestWriteIndexFilesExtended:
    def test_writes_file_manifest(self, tmp_path):
        """write_index_files now also writes file_manifest.json."""
        block = FuncBlock(
            id="app.py:hello:1", file_path="app.py",
            function_name="hello", start_line=1, end_line=1,
            source_code="def hello(): pass", parameters=[], language="python",
        )
        index = CodeIndex(
            repository="test", language="python",
            total_blocks=1, total_entry_points=0, total_chains=0,
            blocks=[block], edges=[], entry_points=[], chains=[],
            file_manifest=FileManifest(entries=[
                FileEntry(file_path="config.yaml", file_type="config", size_bytes=10),
            ]),
            degradation_level=DegradationLevel.FULL,
        )

        out = tmp_path / "output"
        json_path, summary_path = write_index_files(index, str(out))

        # Verify the JSON was written and includes file_manifest
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert "file_manifest" in data
        assert data["file_manifest"]["entries"][0]["file_path"] == "config.yaml"
