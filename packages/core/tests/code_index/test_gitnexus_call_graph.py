"""gitnexus_call_graph 单元测试。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shannon_core.code_index.models import (
    CallChain, CallEdge, FuncBlock,
)
from shannon_core.code_index.gitnexus_call_graph import (
    build_call_graph_from_gitnexus,
    _parse_process_response,
    trace_from_sink,
    find_sinks_by_patterns,
    get_function_context,
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
            "query": {
                "processes": [{"summary": "HandlerFlow", "priority": 0.9}],
                "process_symbols": [
                    {"name": "handler", "type": "Function", "filePath": "app.py", "startLine": 1},
                ],
                "definitions": [],
            },
            "cypher": [
                {
                    "caller_file": "app.py", "caller_name": "handler",
                    "caller_line": 5, "callee_file": "svc.py",
                    "callee_name": "get_users", "confidence": 0.9,
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
            "query": {
                "processes": [{"summary": "HandlerFlow", "priority": 0.9}],
                "process_symbols": [
                    {"name": "handler", "type": "Function", "filePath": "app.py", "startLine": 1},
                ],
                "definitions": [],
            },
            "cypher": [
                {
                    "caller_file": "app.py", "caller_name": "handler",
                    "caller_line": 5, "callee_file": "svc.py",
                    "callee_name": "get_users", "confidence": 0.9,
                },
                {
                    "caller_file": "svc.py", "caller_name": "get_users",
                    "caller_line": 15, "callee_file": "db.py",
                    "callee_name": "execute", "confidence": 0.85,
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


class FakeImpactMCPClient:
    """Fake MCP client with separate responses per tool+arguments."""

    def __init__(self, responses: dict[str, list | dict | str | None]):
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, tool_name: str, arguments: dict):
        self.calls.append((tool_name, arguments))
        key = tool_name
        return self._responses.get(key)


class TestImpactTracing:
    @pytest.mark.asyncio
    async def test_trace_from_sink_builds_chains(self):
        """trace_from_sink uses impact tool to build upstream chains."""
        mcp = FakeImpactMCPClient(responses={
            "impact": {
                "target": {"name": "execute_sql", "kind": "Function", "file": "db.py", "line": 30},
                "upstream": [
                    {"depth": 1, "name": "get_users", "kind": "Function", "file": "svc.py", "line": 15, "relation": "CALLS", "confidence": 0.9},
                    {"depth": 2, "name": "handler", "kind": "Function", "file": "app.py", "line": 5, "relation": "CALLS", "confidence": 0.85},
                ],
            },
        })
        result = await trace_from_sink(
            mcp_client=mcp,
            sink_name="execute_sql",
            sink_file="db.py",
            sink_line=30,
        )
        assert len(result.edges) == 2
        assert len(result.chains) == 2
        # Should have called impact tool
        assert any(c[0] == "impact" for c in mcp.calls)

    @pytest.mark.asyncio
    async def test_trace_from_sink_returns_empty_on_none(self):
        """trace_from_sink returns empty result when impact returns None."""
        mcp = FakeImpactMCPClient(responses={"impact": None})
        result = await trace_from_sink(
            mcp_client=mcp,
            sink_name="nonexistent",
            sink_file="f.py",
            sink_line=1,
        )
        assert result.edges == []
        assert result.chains == []

    @pytest.mark.asyncio
    async def test_find_sinks_by_patterns(self):
        """find_sinks_by_patterns uses query tool to discover sinks."""
        mcp = FakeImpactMCPClient(responses={
            "query": [
                {"name": "execute_sql", "kind": "Function", "filePath": "db.py", "startLine": 30},
                {"name": "eval", "kind": "Function", "filePath": "utils.py", "startLine": 10},
            ],
        })
        sinks = await find_sinks_by_patterns(mcp, ["execute_sql", "eval"])
        assert len(sinks) >= 1
        assert any(s["name"] == "execute_sql" for s in sinks)

    @pytest.mark.asyncio
    async def test_find_sinks_returns_empty_on_none(self):
        """find_sinks_by_patterns returns empty list when query returns None."""
        mcp = FakeImpactMCPClient(responses={"query": None})
        sinks = await find_sinks_by_patterns(mcp, ["nonexistent"])
        assert sinks == []

    @pytest.mark.asyncio
    async def test_get_function_context(self):
        """get_function_context retrieves symbol details via context tool."""
        mcp = FakeImpactMCPClient(responses={
            "context": {
                "symbol": {"uid": "Function:get_users", "kind": "Function", "filePath": "svc.py", "startLine": 10},
                "incoming": {"calls": [{"name": "handler"}]},
                "outgoing": {"calls": [{"name": "execute_sql"}]},
                "processes": [{"name": "UserFlow"}],
            },
        })
        ctx = await get_function_context(mcp, "get_users")
        assert ctx is not None
        assert "symbol" in ctx


class TestPipelineAutoIndexing:
    @pytest.mark.asyncio
    async def test_unavailable_gitnexus_raises(self, tmp_path):
        """GitNexus CLI 不可用时,build_code_index_with_gitnexus 必须硬失败,
        不再降级到 minimal AST-only mode。"""
        from shannon_core.code_index import build_code_index_with_gitnexus
        from shannon_core.models.errors import PentestError

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("def handler(): pass\n")

        with patch("shannon_core.code_index.gitnexus_engine.GitNexusEngine.is_available", return_value=False):
            mcp = FakeImpactMCPClient(responses={})
            with pytest.raises(PentestError, match="GitNexus"):
                await build_code_index_with_gitnexus(
                    str(tmp_path),
                    mcp_client=mcp,
                    llm_client=AsyncMock(return_value="{}"),
                    auto_index=True,
                )

    @pytest.mark.asyncio
    async def test_hard_fail_path_does_not_build_parameter_graph(self, tmp_path):
        """Hard-fail path never reaches pgraph construction."""
        from shannon_core.code_index import build_code_index_with_gitnexus
        from shannon_core.models.errors import PentestError

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("def handler(): pass\n")

        with patch("shannon_core.code_index.gitnexus_engine.GitNexusEngine.is_available", return_value=False):
            with patch("shannon_core.code_index.ParameterPropagationGraph") as mock_pgraph:
                mcp = FakeImpactMCPClient(responses={})
                with pytest.raises(PentestError, match="GitNexus"):
                    await build_code_index_with_gitnexus(
                        str(tmp_path),
                        mcp_client=mcp,
                        llm_client=AsyncMock(return_value="{}"),
                        auto_index=True,
                    )

        mock_pgraph.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_path_returns_parameter_graph(self, tmp_path):
        """GitNexus success path attaches the constructed pgraph to CodeIndex."""
        from shannon_core.code_index import build_code_index_with_gitnexus
        from shannon_core.code_index.models import CallGraphResult

        source_file = tmp_path / "app.py"
        source_file.write_text("def handler(): pass\n")
        handler = _block("handler", "app.py", 1)
        parser = MagicMock()
        parser.parse_file.return_value = [handler]

        with patch("shannon_core.code_index.detect_language", return_value="python"):
            with patch("shannon_core.code_index.discover_source_files", return_value=[source_file]):
                with patch("shannon_core.code_index.get_parser", return_value=parser):
                    with patch(
                        "shannon_core.code_index.build_call_graph_from_gitnexus",
                        new=AsyncMock(return_value=CallGraphResult(entry_points=[handler])),
                    ):
                        with patch("shannon_core.code_index.detect_sinks", return_value=[]):
                            with patch("shannon_core.code_index.detect_entry_points", return_value=[]):
                                with patch("shannon_core.code_index.propagate_across_chains", return_value=[]):
                                    index, rule_gaps = await build_code_index_with_gitnexus(
                                        str(tmp_path),
                                        mcp_client=FakeImpactMCPClient(responses={}),
                                        llm_client=AsyncMock(return_value="{}"),
                                    )

        assert index.parameter_graph is not None
        assert index.parameter_graph.language_coverage == ["python"]
        assert index.parameter_graph.taint_flows == []

    @pytest.mark.asyncio
    async def test_indexing_failure_raises(self, tmp_path):
        """ensure_indexed() 失败时,build_code_index_with_gitnexus 必须硬失败。"""
        from shannon_core.code_index import build_code_index_with_gitnexus
        from shannon_core.models.errors import PentestError

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("def handler(): pass\n")

        with patch("shannon_core.code_index.gitnexus_engine.GitNexusEngine") as mock_engine_cls:
            mock_engine = MagicMock()
            mock_engine.is_available.return_value = True
            mock_engine.ensure_indexed.return_value = MagicMock(
                success=False, error_message="boom"
            )
            mock_engine_cls.return_value = mock_engine
            mcp = FakeImpactMCPClient(responses={})
            with pytest.raises(PentestError, match="GitNexus"):
                await build_code_index_with_gitnexus(
                    str(tmp_path),
                    mcp_client=mcp,
                    llm_client=AsyncMock(return_value="{}"),
                    auto_index=True,
                )
