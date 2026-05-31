import pytest
from pydantic import ValidationError

from shannon_core.code_index.models import (
    FuncBlock,
    CallEdge,
    EntryPoint,
    CallChain,
    CodeIndex,
)


def test_func_block_creation():
    block = FuncBlock(
        id="src/app.py:hello:10",
        file_path="src/app.py",
        function_name="hello",
        start_line=10,
        end_line=15,
        source_code="def hello(name):\n    return f'Hello {name}'",
        parameters=["name"],
        language="python",
    )
    assert block.id == "src/app.py:hello:10"
    assert block.language == "python"
    assert block.decorators == []
    assert block.class_name is None


def test_func_block_with_class_and_decorators():
    block = FuncBlock(
        id="src/app.py:UserView.get:20",
        file_path="src/app.py",
        function_name="get",
        start_line=20,
        end_line=30,
        source_code="@app.route('/users')\ndef get(self): pass",
        parameters=["self"],
        class_name="UserView",
        decorators=["@app.route('/users')"],
        language="python",
    )
    assert block.class_name == "UserView"
    assert block.decorators == ["@app.route('/users')"]


def test_call_edge_resolved():
    edge = CallEdge(
        caller_id="src/app.py:hello:10",
        callee_name="greet",
        callee_file="src/utils.py",
        resolved=True,
        line=12,
    )
    assert edge.resolved is True
    assert edge.callee_file == "src/utils.py"


def test_call_edge_unresolved():
    edge = CallEdge(
        caller_id="src/app.py:hello:10",
        callee_name="dynamic_func",
        resolved=False,
        line=13,
    )
    assert edge.resolved is False
    assert edge.callee_file is None


def test_entry_point_high_confidence():
    ep = EntryPoint(
        func_block_id="src/app.py:list_users:5",
        entry_type="http_route",
        route="/api/users",
        http_method="GET",
        confidence=0.95,
        evidence="Decorated with @app.route('/api/users')",
        needs_llm_review=False,
    )
    assert ep.needs_llm_review is False
    assert ep.confidence == 0.95


def test_entry_point_needs_review():
    ep = EntryPoint(
        func_block_id="src/app.py:process:50",
        entry_type="unknown",
        confidence=0.30,
        evidence="async def with no known decorator",
        needs_llm_review=True,
    )
    assert ep.needs_llm_review is True


def test_call_chain():
    chain = CallChain(
        entry_point_id="src/app.py:list_users:5",
        path=[
            "src/app.py:list_users:5",
            "src/services.py:get_users:20",
            "src/db.py:query:30",
        ],
        depth=2,
        has_unresolved=False,
    )
    assert chain.depth == 2
    assert len(chain.path) == 3


def test_call_chain_with_unresolved():
    chain = CallChain(
        entry_point_id="src/app.py:list_users:5",
        path=[
            "src/app.py:list_users:5",
            "src/services.py:get_users:20",
        ],
        depth=1,
        has_unresolved=True,
    )
    assert chain.has_unresolved is True


def test_code_index():
    block = FuncBlock(
        id="src/app.py:hello:1",
        file_path="src/app.py",
        function_name="hello",
        start_line=1,
        end_line=3,
        source_code="def hello(): pass",
        parameters=[],
        language="python",
    )
    edge = CallEdge(
        caller_id="src/app.py:hello:1",
        callee_name="print",
        resolved=False,
        line=2,
    )
    ep = EntryPoint(
        func_block_id="src/app.py:hello:1",
        entry_type="http_route",
        confidence=0.95,
        evidence="@app.route",
        needs_llm_review=False,
    )
    chain = CallChain(
        entry_point_id="src/app.py:hello:1",
        path=["src/app.py:hello:1"],
        depth=0,
        has_unresolved=False,
    )
    index = CodeIndex(
        repository="test-repo",
        language="python",
        total_blocks=1,
        total_entry_points=1,
        total_chains=1,
        blocks=[block],
        edges=[edge],
        entry_points=[ep],
        chains=[chain],
    )
    assert index.total_blocks == 1
    assert index.total_entry_points == 1
    assert index.total_chains == 1
    assert len(index.blocks) == 1


def test_code_index_serialization():
    block = FuncBlock(
        id="a:f:1",
        file_path="a",
        function_name="f",
        start_line=1,
        end_line=1,
        source_code="def f(): pass",
        parameters=[],
        language="python",
    )
    index = CodeIndex(
        repository="repo",
        language="python",
        total_blocks=1,
        total_entry_points=0,
        total_chains=0,
        blocks=[block],
        edges=[],
        entry_points=[],
        chains=[],
    )
    data = index.model_dump()
    assert data["repository"] == "repo"
    json_str = index.model_dump_json()
    assert '"repository":"repo"' in json_str.replace(" ", "")


def test_func_block_missing_required_field():
    with pytest.raises(ValidationError):
        FuncBlock(
            id="a:f:1",
            file_path="a",
            function_name="f",
            # missing start_line, end_line, source_code, parameters, language
        )