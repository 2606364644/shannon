"""process_trace_reader 单元测试。"""
import pytest
from shannon_core.code_index.process_trace_reader import (
    ProcessTrace, parse_trace_steps, read_all_process_traces,
)


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
