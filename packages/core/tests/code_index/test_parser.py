import pytest
from pathlib import Path

from shannon_core.code_index.parser import detect_language, discover_source_files


class TestDetectLanguage:
    def test_detects_python(self, tmp_path):
        (tmp_path / "app.py").write_text("print('hi')")
        (tmp_path / "utils.py").write_text("def f(): pass")
        assert detect_language(tmp_path) == "python"

    def test_detects_typescript(self, tmp_path):
        (tmp_path / "app.ts").write_text("console.log('hi')")
        assert detect_language(tmp_path) == "typescript"

    def test_detects_go(self, tmp_path):
        (tmp_path / "main.go").write_text("package main")
        assert detect_language(tmp_path) == "go"

    def test_detects_java(self, tmp_path):
        (tmp_path / "App.java").write_text("class App {}")
        assert detect_language(tmp_path) == "java"

    def test_detects_php(self, tmp_path):
        (tmp_path / "index.php").write_text("<?php echo 'hi';")
        assert detect_language(tmp_path) == "php"

    def test_mixed_language_picks_majority(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1")
        (tmp_path / "utils.py").write_text("y = 2")
        (tmp_path / "helper.ts").write_text("z = 3")
        assert detect_language(tmp_path) == "python"

    def test_no_source_files_raises(self, tmp_path):
        (tmp_path / "README.md").write_text("# Hello")
        with pytest.raises(ValueError, match="No source files found"):
            detect_language(tmp_path)

    def test_custom_extensions_counted(self, tmp_path):
        (tmp_path / "app.pyx").write_text("cdef int x")
        assert detect_language(tmp_path) == "python"


class TestDiscoverSourceFiles:
    def test_finds_python_files(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1")
        (tmp_path / "README.md").write_text("# Hi")
        files = discover_source_files(tmp_path, "python")
        paths = [str(f) for f in files]
        assert any("app.py" in p for p in paths)

    def test_skips_hidden_dirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "hook.py").write_text("x = 1")
        (tmp_path / "app.py").write_text("x = 1")
        files = discover_source_files(tmp_path, "python")
        paths = [str(f) for f in files]
        assert not any(".git" in p for p in paths)

    def test_skips_node_modules(self, tmp_path):
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "lib.ts").write_text("export {}")
        (tmp_path / "app.ts").write_text("export {}")
        files = discover_source_files(tmp_path, "typescript")
        paths = [str(f) for f in files]
        # Check if any path contains /node_modules/ (as a directory component)
        assert not any("/node_modules/" in p for p in paths)

    def test_skips_vendor_dir(self, tmp_path):
        (tmp_path / "vendor").mkdir()
        (tmp_path / "vendor" / "lib.go").write_text("package lib")
        (tmp_path / "main.go").write_text("package main")
        files = discover_source_files(tmp_path, "go")
        paths = [str(f) for f in files]
        # Check if any path contains /vendor/ (as a directory component)
        assert not any("/vendor/" in p for p in paths)

    def test_finds_nested_files(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("x = 1")
        files = discover_source_files(tmp_path, "python")
        assert len(files) == 1