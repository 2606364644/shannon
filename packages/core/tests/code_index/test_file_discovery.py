import pytest
from pathlib import Path
from shannon_core.code_index.file_discovery import (
    classify_security_file, discover_security_files,
    SECURITY_FILE_TYPES,
)
from shannon_core.code_index.models import FileManifest


class TestClassifySecurityFile:
    def test_html_is_template(self):
        assert classify_security_file(".html") == "template"

    def test_ejs_is_template(self):
        assert classify_security_file(".ejs") == "template"

    def test_jinja2_is_template(self):
        assert classify_security_file(".jinja2") == "template"

    def test_vue_is_template(self):
        assert classify_security_file(".vue") == "template"

    def test_yaml_is_config(self):
        assert classify_security_file(".yaml") == "config"

    def test_json_is_config(self):
        assert classify_security_file(".json") == "config"

    def test_env_is_config(self):
        assert classify_security_file(".env") == "config"

    def test_graphql_is_schema(self):
        assert classify_security_file(".graphql") == "schema"

    def test_proto_is_schema(self):
        assert classify_security_file(".proto") == "schema"

    def test_sql_is_query(self):
        assert classify_security_file(".sql") == "query"

    def test_py_is_not_security(self):
        assert classify_security_file(".py") is None

    def test_ts_is_not_security(self):
        assert classify_security_file(".ts") is None


class TestDiscoverSecurityFiles:
    def test_discovers_templates(self, tmp_path):
        (tmp_path / "views").mkdir()
        (tmp_path / "views" / "index.html").write_text("<h1>Hello</h1>")
        (tmp_path / "views" / "show.ejs").write_text("<%= name %>")
        manifest = discover_security_files(tmp_path)
        assert manifest.total_count == 2
        assert all(e.file_type == "template" for e in manifest.entries)

    def test_discovers_configs(self, tmp_path):
        (tmp_path / "config.yaml").write_text("key: value")
        (tmp_path / "app.json").write_text("{}")
        manifest = discover_security_files(tmp_path)
        assert manifest.total_count == 2
        assert all(e.file_type == "config" for e in manifest.entries)

    def test_discovers_schemas(self, tmp_path):
        (tmp_path / "schema.graphql").write_text("type Query { hello: String }")
        manifest = discover_security_files(tmp_path)
        assert manifest.total_count == 1
        assert manifest.entries[0].file_type == "schema"

    def test_skips_source_files(self, tmp_path):
        (tmp_path / "app.py").write_text("print('hello')")
        (tmp_path / "index.ts").write_text("console.log('hello')")
        manifest = discover_security_files(tmp_path)
        assert manifest.total_count == 0

    def test_skips_git_and_node_modules(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("[core]")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg.json").write_text("{}")
        manifest = discover_security_files(tmp_path)
        assert manifest.total_count == 0

    def test_mixed_file_types(self, tmp_path):
        (tmp_path / "index.html").write_text("<h1>Hello</h1>")
        (tmp_path / "config.yaml").write_text("key: value")
        (tmp_path / "schema.graphql").write_text("type Query")
        (tmp_path / "app.py").write_text("pass")
        manifest = discover_security_files(tmp_path)
        assert manifest.total_count == 3
        assert manifest.by_type == {"template": 1, "config": 1, "schema": 1}

    def test_subdirectory_discovery(self, tmp_path):
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "base.html").write_text("<html></html>")
        (tmp_path / "templates" / "sub").mkdir()
        (tmp_path / "templates" / "sub" / "page.html").write_text("<p>page</p>")
        manifest = discover_security_files(tmp_path)
        assert manifest.total_count == 2

    def test_by_type_filter(self, tmp_path):
        (tmp_path / "a.html").write_text("<h1>A</h1>")
        (tmp_path / "b.yaml").write_text("key: val")
        manifest = discover_security_files(tmp_path)
        templates = manifest.filter_by_type("template")
        assert len(templates) == 1
        assert templates[0].file_path.endswith("a.html")
