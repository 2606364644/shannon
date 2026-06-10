"""gitnexus_call_graph 单元测试。"""
import pytest

from shannon_core.code_index.models import (
    CallChain, CallEdge, FuncBlock,
)
from shannon_core.code_index.gitnexus_call_graph import (
    build_call_graph_from_gitnexus,
    _parse_process_response,
)


def _block(name: str, file: str = "app.py", line: int = 1) -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}",
        file_path=file,
        function_name=name,
        start_line=line,
        end_line=line + 10,
        source_code=f"def {name}(): pass",
        parameters=[],
        language="python",
    )


class FakeMCPClient:
    """Fake GitNexus MCP client returning canned responses."""

    def __init__(self, responses: dict[str, list | dict | None]):
        self._responses = responses

    async def call_tool(self, tool_name: str, arguments: dict):
        return self._responses.get(tool_name)


class TestParseProcessResponse:
    def test_extracts_edges_from_flat_process(self):
        process_data = [
            {
                "caller": {"file": "app.py", "name": "handler", "line": 5},
                "callee": {"file": "svc.py", "name": "get_users", "line": 12},
            },
            {
                "caller": {"file": "svc.py", "name": "get_users", "line": 15},
                "callee": {"file": "db.py", "name": "execute", "line": 30},
            },
        ]
        edges = _parse_process_response(process_data)
        assert len(edges) == 2
        assert edges[0].caller_id == "app.py:handler:5"
        assert edges[0].callee_name == "get_users"
        assert edges[0].callee_file == "svc.py"
        assert edges[0].resolved is True
        assert edges[0].line == 5

    def test_empty_process_returns_empty(self):
        edges = _parse_process_response([])
        assert edges == []

    def test_missing_fields_skipped(self):
        process_data = [
            {"caller": {"file": "app.py", "name": "handler"}, "callee": {"name": "missing_file"}},
        ]
        edges = _parse_process_response(process_data)
        assert len(edges) == 1
        assert edges[0].resolved is False


class TestBuildCallGraphFromGitNexus:
    @pytest.mark.asyncio
    async def test_builds_call_graph_from_mcp(self):
        blocks = [
            _block("handler", "app.py", 1),
            _block("get_users", "svc.py", 10),
            _block("execute", "db.py", 30),
        ]
        mcp = FakeMCPClient(responses={
            "query": [
                {"file": "app.py", "name": "handler", "line": 1, "score": 0.95},
            ],
            "process": [
                {
                    "caller": {"file": "app.py", "name": "handler", "line": 5},
                    "callee": {"file": "svc.py", "name": "get_users", "line": 12},
                },
            ],
        })
        result = await build_call_graph_from_gitnexus(
            repo_path="/tmp/repo",
            mcp_client=mcp,
            blocks=blocks,
        )
        assert len(result.edges) == 1
        assert result.edges[0].callee_name == "get_users"
        assert len(result.entry_points) == 1
        assert result.entry_points[0].function_name == "handler"

    @pytest.mark.asyncio
    async def test_raises_when_gitnexus_unavailable(self):
        from shannon_core.code_index.models import GitNexusNotIndexedError
        mcp = FakeMCPClient(responses={"query": None})
        with pytest.raises(GitNexusNotIndexedError):
            await build_call_graph_from_gitnexus(
                repo_path="/tmp/repo",
                mcp_client=mcp,
                blocks=[],
            )

    @pytest.mark.asyncio
    async def test_builds_chains_from_edges(self):
        blocks = [
            _block("handler", "app.py", 1),
            _block("get_users", "svc.py", 10),
            _block("execute", "db.py", 30),
        ]
        mcp = FakeMCPClient(responses={
            "query": [{"file": "app.py", "name": "handler", "line": 1, "score": 0.9}],
            "process": [
                {
                    "caller": {"file": "app.py", "name": "handler", "line": 5},
                    "callee": {"file": "svc.py", "name": "get_users", "line": 12},
                },
                {
                    "caller": {"file": "svc.py", "name": "get_users", "line": 15},
                    "callee": {"file": "db.py", "name": "execute", "line": 30},
                },
            ],
        })
        result = await build_call_graph_from_gitnexus(
            repo_path="/tmp/repo",
            mcp_client=mcp,
            blocks=blocks,
        )
        assert len(result.chains) >= 1
        chain = result.chains[0]
        assert chain.entry_point_id == "app.py:handler:1"
        assert "svc.py:get_users:10" in chain.path
