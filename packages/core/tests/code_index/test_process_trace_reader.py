"""process_trace_reader 单元测试。"""
import pytest
from shannon_core.code_index.process_trace_reader import (
    ProcessTrace, parse_trace_steps, read_all_process_traces,
)
from shannon_core.code_index.models import CallChain, FuncBlock
from shannon_core.code_index.process_trace_reader import trace_to_chain


class FakeTraceMCP:
    """cypher 返回 labels；read_resource 按 label 返回 trace 文本。"""
    def __init__(self, labels: list[str], traces: dict[str, str]):
        self._labels = labels
        self._traces = traces

    async def call_tool(self, tool_name, arguments):
        assert tool_name == "cypher"
        return {"rows": [{"label": lb} for lb in self._labels]}

    async def read_resource(self, uri):
        for lb, text in self._traces.items():
            if uri.endswith(lb):
                return text
        return ""


def test_parse_trace_steps_extracts_ordered_steps():
    text = (
        "## Process Trace\n\n"
        "1: init (main.go)\n"
        "2: NewEndpoint (transport/endpoints.go)\n"
        "3: Search (service/impl.go)\n"
    )
    steps = parse_trace_steps(text)
    assert steps == [
        (1, "init", "main.go"),
        (2, "NewEndpoint", "transport/endpoints.go"),
        (3, "Search", "service/impl.go"),
    ]


def test_parse_trace_steps_empty_text():
    assert parse_trace_steps("") == []
    assert parse_trace_steps("no steps here") == []


@pytest.mark.asyncio
async def test_read_all_process_traces_cypher_labels_then_read():
    """全量召回：cypher 拿全 label（不依赖 processes resource 的 20 截断）。"""
    traces = {
        "Init → GetOffset": "1: init (main.go)\n2: GetOffset (repo.go)\n",
        "Upload": "1: UploadFile (handler.go)\n2: Save (store.go)\n",
    }
    mcp = FakeTraceMCP(labels=list(traces.keys()), traces=traces)
    result = await read_all_process_traces(mcp, repo_name="svc")
    assert len(result) == 2
    labels = {t.label for t in result}
    assert labels == {"Init → GetOffset", "Upload"}
    init = next(t for t in result if t.label == "Init → GetOffset")
    assert init.steps == [(1, "init", "main.go"), (2, "GetOffset", "repo.go")]
    assert init.step_count == 2


@pytest.mark.asyncio
async def test_read_all_process_traces_skips_empty_trace():
    """单条 trace 读失败/空 → log + 跳过，不影响其它。"""
    traces = {"Good": "1: a (a.go)\n", "Bad": ""}
    mcp = FakeTraceMCP(labels=["Good", "Bad"], traces=traces)
    result = await read_all_process_traces(mcp, repo_name="svc")
    assert len(result) == 1
    assert result[0].label == "Good"


def _blk(name, file, line=1):
    return FuncBlock(
        id=f"{file}:{name}:{line}", file_path=file, function_name=name,
        start_line=line, end_line=line + 5, source_code=f"def {name}(): pass",
        parameters=[], language="go",
    )


def test_trace_to_chain_exact_file_name_match():
    blocks = [_blk("init", "main.go", 1), _blk("Search", "service/impl.go", 10)]
    trace = ProcessTrace(label="L", steps=[(1, "init", "main.go"), (2, "Search", "service/impl.go")])
    chain = trace_to_chain(trace, blocks)
    assert chain is not None
    assert chain.path == ["main.go:init:1", "service/impl.go:Search:10"]
    assert chain.entry_point_id == "main.go:init:1"
    assert chain.has_unresolved is False
    assert chain.depth == 1


def test_trace_to_chain_tail_path_match_when_full_misses():
    """GitNexus filePath 可能与 tree-sitter 略有出入 → 尾匹配兜底。"""
    blocks = [_blk("Search", "internal/service/impl.go", 10)]  # tree-sitter 全路径
    trace = ProcessTrace(label="L", steps=[(1, "Search", "service/impl.go")])  # GitNexus 短路径
    chain = trace_to_chain(trace, blocks)
    assert chain is not None
    assert chain.path == ["internal/service/impl.go:Search:10"]


def test_trace_to_chain_unique_name_fallback():
    blocks = [_blk("GetOffset", "repo.go", 5)]
    trace = ProcessTrace(label="L", steps=[(1, "GetOffset", "different.go")])  # 文件不符但 name 唯一
    chain = trace_to_chain(trace, blocks)
    assert chain is not None
    assert chain.path == ["repo.go:GetOffset:5"]


def test_trace_to_chain_placeholder_when_unresolved():
    """name 多个候选且文件不符 → 占位 + has_unresolved。"""
    blocks = [_blk("Save", "a.go", 1), _blk("Save", "b.go", 1)]
    trace = ProcessTrace(label="L", steps=[(1, "Save", "c.go")])
    chain = trace_to_chain(trace, blocks)
    assert chain is not None
    assert chain.has_unresolved is True
    assert chain.path[0] == "c.go:Save"  # 占位格式
