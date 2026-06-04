import pytest
from pathlib import Path
from shannon_core.code_index.enhanced_parameters import (
    extract_typed_parameters, mark_http_parameter_sources,
)
from shannon_core.code_index.models import TypedParameter, ParameterSource


class TestExtractTypedParametersPython:
    def test_simple_params(self, tmp_path):
        source = "def hello(name, age): pass"
        f = tmp_path / "test.py"
        f.write_text(source)
        params = extract_typed_parameters(f, "hello", 1, "python")
        assert len(params) == 2
        assert params[0].name == "name"
        assert params[1].name == "age"

    def test_typed_params(self, tmp_path):
        source = "def handler(user_id: int, name: str): pass"
        f = tmp_path / "test.py"
        f.write_text(source)
        params = extract_typed_parameters(f, "handler", 1, "python")
        assert len(params) == 2
        assert params[0].name == "user_id"
        assert params[0].type_annotation == "int"
        assert params[1].type_annotation == "str"

    def test_default_values(self, tmp_path):
        source = "def func(limit: int = 10, offset: int = 0): pass"
        f = tmp_path / "test.py"
        f.write_text(source)
        params = extract_typed_parameters(f, "func", 1, "python")
        assert params[0].default_value == "10"
        assert params[1].default_value == "0"

    def test_variadic_args(self, tmp_path):
        source = "def func(*args, **kwargs): pass"
        f = tmp_path / "test.py"
        f.write_text(source)
        params = extract_typed_parameters(f, "func", 1, "python")
        assert any(p.is_variadic and p.name == "args" for p in params)
        assert any(p.is_keyword_variadic and p.name == "kwargs" for p in params)

    def test_no_function_at_line(self, tmp_path):
        source = "x = 1\n"
        f = tmp_path / "test.py"
        f.write_text(source)
        params = extract_typed_parameters(f, "nonexistent", 1, "python")
        assert params == []

    def test_file_not_found(self, tmp_path):
        params = extract_typed_parameters(tmp_path / "nofile.py", "f", 1, "python")
        assert params == []


class TestExtractTypedParametersTypeScript:
    def test_arrow_function_params(self, tmp_path):
        source = "const handler = (req: Request, res: Response) => {};\n"
        f = tmp_path / "test.ts"
        f.write_text(source)
        # Arrow function at line 1 — function_name is "handler" from variable_declarator
        params = extract_typed_parameters(f, "handler", 1, "typescript")
        assert len(params) == 2
        assert params[0].name == "req"
        assert params[0].type_annotation == "Request"
        assert params[1].type_annotation == "Response"

    def test_optional_params(self, tmp_path):
        source = "function greet(name: string, age?: number) {}\n"
        f = tmp_path / "test.ts"
        f.write_text(source)
        params = extract_typed_parameters(f, "greet", 1, "typescript")
        assert len(params) == 2
        assert params[1].is_optional is True


class TestMarkHttpParameterSources:
    def test_flask_request_args(self):
        params = [
            TypedParameter(name="request"),
        ]
        marked = mark_http_parameter_sources(params, "python", "flask")
        assert len(marked) == 1
        assert marked[0].source == ParameterSource.UNKNOWN

    def test_express_req_res(self):
        params = [
            TypedParameter(name="req", type_annotation="Request"),
            TypedParameter(name="res", type_annotation="Response"),
        ]
        marked = mark_http_parameter_sources(params, "typescript", "express")
        assert len(marked) == 2
        assert marked[0].source == ParameterSource.UNKNOWN
        assert marked[1].source == ParameterSource.INTERNAL
