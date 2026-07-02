"""Tests for OpenAPI / Swagger schema file parser → EntryPoint list (G5a, spec-1b)."""

import textwrap

from shannon_core.code_index.schema_entry_parser import parse_openapi_schema_files


def _write_json(repo, name, payload_str):
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(payload_str)
    return p


def _write_yaml(repo, name, payload_str):
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(payload_str))
    return p


def test_parse_openapi_json_basic(tmp_path):
    """openapi.json 的 paths → EntryPoint（schema_file 源）。"""
    _write_json(tmp_path, "openapi.json", textwrap.dedent("""\
        {
          "openapi": "3.0.0",
          "paths": {
            "/users/{id}": {
              "get": {}
            }
          }
        }
    """))
    eps = parse_openapi_schema_files(str(tmp_path))
    assert any(
        e.route == "/users/{id}" and e.http_method == "GET"
        and e.source == "schema_file" and e.confidence == 0.80
        and e.entry_type == "http_route" and e.needs_llm_review for e in eps
    ), f"应产 schema 源 EntryPoint，实际 {[(e.route, e.http_method, e.source) for e in eps]}"


def test_parse_openapi_yaml_security_marks_auth_required(tmp_path):
    """operation 有 security → authentication='required'。"""
    _write_yaml(tmp_path, "openapi.yaml", """\
        openapi: 3.0.0
        paths:
          /admin:
            post:
              security:
                - bearerAuth: []
    """)
    eps = parse_openapi_schema_files(str(tmp_path))
    admin = next(e for e in eps if e.route == "/admin")
    assert admin.authentication == "required", f"有 security 应标 required，实际 {admin.authentication}"


def test_parse_skips_non_path_files_and_malformed(tmp_path):
    """非 OpenAPI 文件 / 解析失败 → 不崩，返回空或跳过。"""
    (tmp_path / "openapi.json").write_text("{ not valid json ")  # 解析失败
    (tmp_path / "openapi.yaml").write_text("swagger: '2.0'\n")    # 无 paths
    eps = parse_openapi_schema_files(str(tmp_path))
    assert eps == [], "解析失败 / 无 paths 应跳过，返回空列表"


def test_parse_ignores_node_modules(tmp_path):
    """node_modules 下的 OpenAPI 不扫。"""
    _write_json(tmp_path, "node_modules/lib/openapi.json", textwrap.dedent("""\
        {
          "openapi": "3.0.0",
          "paths": {
            "/x": {
              "get": {}
            }
          }
        }
    """))
    assert parse_openapi_schema_files(str(tmp_path)) == []
