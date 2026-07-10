"""gitnexus_call_graph 单元测试。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shannon_core.code_index.models import (
    CallChain, CallEdge, FuncBlock,
)
from shannon_core.code_index.gitnexus_call_graph import (
    build_call_graph_from_gitnexus,
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


class FakeTraceMCPClient:
    """Fake MCP: cypher 返 process labels；read_resource 按 label 返 trace 文本。

    cypher 返 None 表示未索引（GitNexusNotIndexedError 路径）。
    匹配 process_trace_reader.read_all_process_traces 的协议：
    ``call_tool("cypher", ...)`` → ``{"rows": [{"label": <lb>}, ...]}``，
    ``read_resource(<…>/process/<label>)`` → trace 文本。
    """

    def __init__(self, labels=None, traces=None, cypher_none=False):
        self._labels = labels or []
        self._traces = traces or {}
        self._cypher_none = cypher_none

    async def call_tool(self, tool_name, arguments):
        if self._cypher_none:
            return None
        return {"rows": [{"label": lb} for lb in self._labels]}

    async def read_resource(self, uri):
        for lb, text in self._traces.items():
            if uri.endswith(lb):
                return text
        return ""


class TestBuildCallGraphFromGitnexus:
    @pytest.mark.asyncio
    async def test_chains_nonempty_from_process_traces(self):
        """核心回归锚点：process trace → 非空 chains（生产一直空壳=chains=0）。"""
        blocks = [
            _block("init", "main.go", 1),
            _block("Search", "svc.go", 10),
            _block("GetOffset", "repo.go", 30),
        ]
        mcp = FakeTraceMCPClient(
            labels=["Init → GetOffset"],
            traces={"Init → GetOffset": "1: init (main.go)\n2: Search (svc.go)\n3: GetOffset (repo.go)\n"},
        )
        result = await build_call_graph_from_gitnexus(
            repo_path="/tmp/svc", mcp_client=mcp, blocks=blocks,
        )
        assert len(result.chains) == 1
        chain = result.chains[0]
        assert chain.entry_point_id == "main.go:init:1"
        assert chain.path == ["main.go:init:1", "svc.go:Search:10", "repo.go:GetOffset:30"]
        # entry_points = path[0] 对应 FuncBlock（去重）
        assert len(result.entry_points) == 1
        assert result.entry_points[0].function_name == "init"
        # edges 废弃（process trace 不产 edges）
        assert result.edges == []

    @pytest.mark.asyncio
    async def test_raises_when_not_indexed(self):
        """cypher probe 返 None（未索引）→ GitNexusNotIndexedError。"""
        from shannon_core.code_index.models import GitNexusNotIndexedError
        mcp = FakeTraceMCPClient(cypher_none=True)
        with pytest.raises(GitNexusNotIndexedError):
            await build_call_graph_from_gitnexus(
                repo_path="/tmp/svc", mcp_client=mcp, blocks=[],
            )

    @pytest.mark.asyncio
    async def test_empty_when_no_processes(self):
        """有索引但 0 process → 空 chains（不抛，降级由上游处理）。"""
        mcp = FakeTraceMCPClient(labels=[])
        result = await build_call_graph_from_gitnexus(
            repo_path="/tmp/svc", mcp_client=mcp, blocks=[_block("init", "main.go", 1)],
        )
        assert result.chains == []
        assert result.entry_points == []

    @pytest.mark.asyncio
    async def test_multiple_traces_distinct_entries(self):
        blocks = [_block("init", "main.go", 1), _block("Upload", "h.go", 5), _block("Save", "s.go", 9)]
        mcp = FakeTraceMCPClient(
            labels=["Flow1", "Flow2"],
            traces={
                "Flow1": "1: init (main.go)\n2: Save (s.go)\n",
                "Flow2": "1: Upload (h.go)\n2: Save (s.go)\n",
            },
        )
        result = await build_call_graph_from_gitnexus("/tmp/svc", mcp, blocks)
        assert len(result.chains) == 2
        entry_ids = {b.id for b in result.entry_points}
        assert entry_ids == {"main.go:init:1", "h.go:Upload:5"}


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
                                # pipeline 已切 backward(B3):patch target 须跟到
                                # propagate_backward_across_chains,否则 patch 是 no-op(失效)。
                                with patch("shannon_core.code_index.propagate_backward_across_chains", return_value=[]):
                                    index, rule_gaps, _source_gaps = await build_code_index_with_gitnexus(
                                        str(tmp_path),
                                        mcp_client=FakeImpactMCPClient(responses={}),
                                        llm_client=AsyncMock(return_value="{}"),
                                    )

        assert index.parameter_graph is not None
        assert index.parameter_graph.language_coverage == ["python"]
        assert index.parameter_graph.taint_flows == []

    @pytest.mark.asyncio
    async def test_entry_points_union_detect_and_process(self, tmp_path):
        """G2: CodeIndex.entry_points = detect_entry_points ∪ process entry。
        process entry 用 entry_type='gitnexus_process'；同 id 时 detect 优先。"""
        from shannon_core.code_index import build_code_index_with_gitnexus
        from shannon_core.code_index.models import CallGraphResult, EntryPoint

        source_file = tmp_path / "app.py"
        source_file.write_text("def cli_main(): pass\ndef Upload(): pass\n")
        cli = _block("cli_main", "app.py", 1)        # detect 会识别为 cli
        upload = _block("Upload", "app.py", 5)        # process entry（detect 不识别）
        parser = MagicMock()
        parser.parse_file.return_value = [cli, upload]

        detected = [EntryPoint(
            func_block_id=cli.id, entry_type="cli", route=None, http_method=None,
            confidence=0.9, evidence="cli", needs_llm_review=False, source="code_index",
        )]

        with patch("shannon_core.code_index.detect_language", return_value="python"):
            with patch("shannon_core.code_index.discover_source_files", return_value=[source_file]):
                with patch("shannon_core.code_index.get_parser", return_value=parser):
                    with patch(
                        "shannon_core.code_index.build_call_graph_from_gitnexus",
                        new=AsyncMock(return_value=CallGraphResult(entry_points=[upload])),
                    ):
                        with patch("shannon_core.code_index.detect_sinks", return_value=[]):
                            with patch("shannon_core.code_index.detect_entry_points", return_value=detected):
                                # pipeline 已切 backward(B3):patch target 须跟到
                                # propagate_backward_across_chains,否则 patch 是 no-op(失效)。
                                with patch("shannon_core.code_index.propagate_backward_across_chains", return_value=[]):
                                    index, _rg, _sg = await build_code_index_with_gitnexus(
                                        str(tmp_path), mcp_client=FakeImpactMCPClient(responses={}),
                                        llm_client=AsyncMock(return_value="{}"),
                                    )

        by_id = {ep.func_block_id: ep for ep in index.entry_points}
        assert cli.id in by_id and by_id[cli.id].entry_type == "cli"           # detect 优先
        assert upload.id in by_id and by_id[upload.id].entry_type == "gitnexus_process"  # process 补
        assert by_id[upload.id].route is None
        assert by_id[upload.id].source == "gitnexus"

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
