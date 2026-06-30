# GitNexus 轨调用链下沉 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 GitNexus 轨的调用链来源从「全量 cypher + Python BFS」换成 GitNexus 原生 `process trace` resource，并配套重构 entry 体系（detect ∪ process）与 authz 判定（扫全链 side-effect + ownership 段 + process entry），让 authz 召回 0→21、chains 不再空壳。

**Architecture:** 新增 `process_trace_reader`（读 process trace resource → `ProcessTrace`）+ `impact_supplement`（定向可达补充，不产 path）；重写 `build_call_graph_from_gitnexus` 为「process trace → `CallChain`」（删 Python BFS）；entry 组装改 detect ∪ process；`find_unguarded_sink_paths` 改扫全链 + ownership 段。injection/xss/ssrf 判定语义不变（吃新 chains）；authz 判定语义改（§4.8）。

**Tech Stack:** Python 3.11+ / asyncio / pydantic / pytest（asyncio mode）/ GitNexus 1.6.7 MCP（stdio JSON-RPC）。

**关联 spec：** `docs/superpowers/specs/2026-06-30-gitnexus-call-chain-offload-design.md`

---

## Global Constraints

（每个 task 的需求隐含包含本节；逐字取自 spec §7 不变量 + CLAUDE.md）

- **守 CLAUDE.md §1 双轨铁律**：只动 GitNexus 轨（调用链来源 + entry + authz 判定），不碰 LLM 轨；**不向 LLM 轨喂确定性产物**。
- **`externally_exploitable` 不被覆写**；**`CallChain` 结构不变**（`entry_point_id` / `path: list[str]` / `depth` / `has_unresolved`）；**`EntryPoint` 模型不增字段**（`gitnexus_process` 是新 `entry_type` 值，非新字段）。
- **`CODE_INDEX_RETRY(max 3)` 不动**；injection 判定语义不改，authz 判定语义改（§4.8）。
- **GitNexus 1.6.7 行为**：process trace 是 MCP **resource**（`resources/read gitnexus://repo/{name}/process/{label}`，URI 用 **label 不是 id**）；resource content 是 `{uri,mimeType,text}`（**无 `type` 字段**，区别于 tools/call 的 `{type:"text"}`）；cypher 裁剪版（支持 MATCH/IN/count/ORDER BY/UNWIND，不支持 `type()`/`elementId`）；`_send_request` 的 `stdout.readline()` 有 64KB 限制。
- **只跑改动相关测试**：全套 pytest 有预存挂起/失败（`test_worker_progress` / `test_cli follow` / `test_audit_injection` / integration 挂起）——勿广跑全套，用 `pytest -q <具体文件>::<test>`。
- **Python 不再重建图/拼路径/做 BFS**：调用链只来自 process trace；impact 只做可达性补充（不产 `chain.path`）。
- **降级契约**：process trace 拿不到 → 空 chains → injection 跳过 + authz 0 候选 → LLM 轨兜底（不引入硬失败）。
- **断点②（head-seed）不在本 plan 解**：source 识别独立化是 follow-up spec B′（见 spec §10）；本 plan 不动 `analyze_taint_llm`/`extract_typed_parameters`/`propagate` 的 seed 逻辑。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `packages/core/src/shannon_core/code_index/gitnexus_mcp.py` | MCP 客户端 | Modify：`start()` 传 `limit=4MB`（修 readline 64KB）；新增 `read_resource(uri)` |
| `packages/core/src/shannon_core/code_index/process_trace_reader.py` | 读 process trace resource → `ProcessTrace`；trace→`CallChain` 四级对齐 | **Create** |
| `packages/core/src/shannon_core/code_index/impact_supplement.py` | impact upstream/downstream 可达性补充（不产 path） | **Create** |
| `packages/core/src/shannon_core/code_index/gitnexus_call_graph.py` | `build_call_graph_from_gitnexus` 入口 | Modify：重写为 process trace；删 `_build_chains_from_edges` / `_resolve_caller_to_block_id` / `_parse_process_response`（BFS 那套）；保留 `trace_from_sink`/`find_sinks_by_patterns`/`get_function_context`（死代码但被旧测试引用，Task 5 后视情况） |
| `packages/core/src/shannon_core/code_index/__init__.py` | `build_code_index_with_gitnexus` pipeline | Modify：`:213-231` entry 组装改 detect ∪ process |
| `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py` | IDOR 候选 | Modify：`find_unguarded_sink_paths` 四处改；`IDORCandidateChain` 加 `sink_step_idx`；`build_authz_gitnexus_track` 可观测性 |
| `packages/core/tests/code_index/test_gitnexus_mcp.py` | MCP 测试 | Modify：加 readline 4MB + read_resource 测试 |
| `packages/core/tests/code_index/test_process_trace_reader.py` | reader 测试 | **Create** |
| `packages/core/tests/code_index/test_impact_supplement.py` | impact 测试 | **Create** |
| `packages/core/tests/code_index/test_gitnexus_call_graph.py` | call graph 测试 | Modify：旧 cypher/BFS 测试改写为 process trace fixture；加 chains 非空回归锚点 |
| `packages/core/tests/code_index/test_authz_dominance.py` | authz 测试 | Modify：加扫全链 + process entry + ownership 段 + sink_step_idx 测试 |

---

## Task 1: readline 4MB 修复（G4）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/gitnexus_mcp.py:63-73`（`start()`）
- Test: `packages/core/tests/code_index/test_gitnexus_mcp.py`

**Interfaces:**
- Produces: `GitNexusMCPClient.start()` 的 `create_subprocess_exec` 调用新增 `limit=4*1024*1024`，使 `stdout.readline()` 可读 >64KB 的全量 cypher 响应。

- [ ] **Step 1: 写失败测试**

在 `test_gitnexus_mcp.py` 的 `TestGitNexusMCPClient` 类内追加：

```python
    @pytest.mark.asyncio
    async def test_start_passes_4mb_read_limit(self, tmp_path):
        """readline 64KB bug 修复：start() 必须给 create_subprocess_exec 传 limit=4MB,
        否则全量 cypher(>64KB) 会让 stdout.readline() 抛 'Separator found, chunk longer than limit'。"""
        client = GitNexusMCPClient(tmp_path)
        with patch("shannon_core.code_index.gitnexus_mcp.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = MagicMock()
            mock_proc.stdin = MagicMock()
            mock_proc.stdin.drain = AsyncMock()
            mock_proc.stdout = AsyncMock()
            mock_proc.stdout.readline = AsyncMock(return_value=json.dumps({
                "jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}
            }).encode())
            mock_proc.wait = AsyncMock()
            mock_exec.return_value = mock_proc

            await client.start()
            _, kwargs = mock_exec.call_args
            assert kwargs.get("limit") == 4 * 1024 * 1024
            await client.stop()
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest -q packages/core/tests/code_index/test_gitnexus_mcp.py::TestGitNexusMCPClient::test_start_passes_4mb_read_limit`
Expected: FAIL — `assert None == 4194304`（当前 `start()` 未传 limit）。

- [ ] **Step 3: 最小实现**

改 `gitnexus_mcp.py:68-73` 的 `create_subprocess_exec` 调用，加 `limit=4 * 1024 * 1024`：

```python
        self._process = await asyncio.create_subprocess_exec(
            "gitnexus", "mcp",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=4 * 1024 * 1024,  # readline 默认 64KB 限制会崩全量 cypher；提到 4MB
        )
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest -q packages/core/tests/code_index/test_gitnexus_mcp.py::TestGitNexusMCPClient::test_start_passes_4mb_read_limit`
Expected: PASS。

- [ ] **Step 5: 回归现有 MCP 测试**

Run: `pytest -q packages/core/tests/code_index/test_gitnexus_mcp.py`
Expected: PASS（全部，含既有 start/stop/call_tool 测试）。

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/gitnexus_mcp.py packages/core/tests/code_index/test_gitnexus_mcp.py
git commit -m "fix(gitnexus): start() pass limit=4MB to fix readline 64KB crash on full cypher"
```

---

## Task 2: `read_resource` 方法（G4）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/gitnexus_mcp.py`（新增方法）
- Test: `packages/core/tests/code_index/test_gitnexus_mcp.py`

**Interfaces:**
- Produces: `async def GitNexusMCPClient.read_resource(self, uri: str) -> str` —— 发 `resources/read`，取 `result["contents"][*]["text"]` 拼接返回（**不查 `type` 字段**，resource 格式不同于 tools/call）；空/异常返回 `""`。

- [ ] **Step 1: 写失败测试**

在 `test_gitnexus_mcp.py` 末尾新增测试类：

```python
class TestReadResource:
    @pytest.mark.asyncio
    async def test_read_resource_returns_text_without_checking_type(self, tmp_path):
        """MCP resource content 是 {uri,mimeType,text}（无 type 字段），不同于 tools/call 的 {type:'text'}。
        read_resource 必须直接取 text，不查 type。"""
        client = GitNexusMCPClient(tmp_path)

        async def fake_send(method, params):
            assert method == "resources/read"
            assert params["uri"] == "gitnexus://repo/svc/process/Init → GetOffset"
            return {"contents": [
                {"uri": "gitnexus://repo/svc/process/Init → GetOffset",
                 "mimeType": "text/yaml", "text": "trace:\n  1: init (main.go)"},
            ]}
        client._send_request = fake_send

        text = await client.read_resource("gitnexus://repo/svc/process/Init → GetOffset")
        assert "1: init (main.go)" in text

    @pytest.mark.asyncio
    async def test_read_resource_empty_when_no_contents(self, tmp_path):
        client = GitNexusMCPClient(tmp_path)
        client._send_request = AsyncMock(return_value={"contents": []})
        assert await client.read_resource("any") == ""

    @pytest.mark.asyncio
    async def test_read_resource_empty_on_exception(self, tmp_path):
        client = GitNexusMCPClient(tmp_path)
        async def boom(method, params):
            raise RuntimeError("not found")
        client._send_request = boom
        assert await client.read_resource("any") == ""
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest -q packages/core/tests/code_index/test_gitnexus_mcp.py::TestReadResource`
Expected: FAIL — `AttributeError: 'GitNexusMCPClient' object has no attribute 'read_resource'`。

- [ ] **Step 3: 最小实现**

在 `gitnexus_mcp.py` 的 `call_tool` 方法之后（`_send_request` 之前）新增：

```python
    async def read_resource(self, uri: str) -> str:
        """Read an MCP resource and return its concatenated text.

        MCP resource content items are ``{uri, mimeType, text}`` — **no ``type``
        field**, unlike tools/call's ``{type: "text", text}``. We take ``text``
        directly from every content item. Returns ``""`` on empty/missing/
        error (process traces are best-effort; one missing trace must not abort
        the whole call graph build).
        """
        try:
            result = await self._send_request("resources/read", {"uri": uri})
        except Exception as exc:
            logger.warning("GitNexus resource read failed (%s): %s", uri, exc)
            return ""
        if not result:
            return ""
        parts: list[str] = []
        for item in result.get("contents", []) or []:
            if isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
        return "\n".join(parts)
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest -q packages/core/tests/code_index/test_gitnexus_mcp.py::TestReadResource`
Expected: PASS（3 个）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/gitnexus_mcp.py packages/core/tests/code_index/test_gitnexus_mcp.py
git commit -m "feat(gitnexus): add read_resource() for MCP resources (no type field)"
```

---

## Task 3: `process_trace_reader.py` — 读 process trace（G1）

**Files:**
- Create: `packages/core/src/shannon_core/code_index/process_trace_reader.py`
- Test: `packages/core/tests/code_index/test_process_trace_reader.py`

**Interfaces:**
- Consumes: `mcp_client.call_tool("cypher", {...})` → `{"rows": [{"label": "..."}]}`；`mcp_client.read_resource(uri)` → trace YAML 文本。
- Produces:
  - `@dataclass ProcessTrace(label: str, steps: list[tuple[int,str,str]], process_type: str, step_count: int)` —— `steps` = `(idx, name, file_path)` 有序。
  - `async def read_all_process_traces(mcp_client, repo_name: str) -> list[ProcessTrace]`
  - `def parse_trace_steps(text: str) -> list[tuple[int,str,str]]`

- [ ] **Step 1: 写失败测试**

创建 `test_process_trace_reader.py`：

```python
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
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest -q packages/core/tests/code_index/test_process_trace_reader.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.code_index.process_trace_reader'`。

- [ ] **Step 3: 实现 `process_trace_reader.py`**

创建 `packages/core/src/shannon_core/code_index/process_trace_reader.py`：

```python
"""Read GitNexus process trace resources → ProcessTrace list.

process trace = GitNexus 索引时预计算的 entry→terminal 调用链路径，通过 MCP
resource ``gitnexus://repo/{name}/process/{label}`` 读取（URI 用 label，不是 id）。
替代旧的「全量 cypher CALLS 边 + Python BFS 重建 chain」——后者慢、readline 崩、
产出空壳。

全量 label 用 cypher ``MATCH (p:Process) RETURN p.label`` 拿（processes resource
截断只给 20）。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# trace 行格式："N: <func> (<filePath>)" —— 见 memory gitnexus-1.6.7-real-machine-behavior
_TRACE_STEP_RE = re.compile(r"^\s*(\d+):\s*(.+?)\s*\(([^)]+)\)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ProcessTrace:
    """一条 process trace = entry→…→terminal 的有序函数序列。"""
    label: str
    steps: list[tuple[int, str, str]]  # (idx, name, file_path)，按 idx 升序
    process_type: str = ""
    step_count: int = 0


def parse_trace_steps(text: str) -> list[tuple[int, str, str]]:
    """解析 trace 文本的步骤行 → [(idx, name, file_path)]，按 idx 升序。"""
    steps = [
        (int(m.group(1)), m.group(2).strip(), m.group(3).strip())
        for m in _TRACE_STEP_RE.finditer(text or "")
    ]
    return sorted(steps, key=lambda s: s[0])


async def read_all_process_traces(mcp_client, repo_name: str) -> list[ProcessTrace]:
    """cypher 拿全 process label → 每 label read_resource → ProcessTrace。

    单条 trace 读失败/空 → log + 跳过（不抛）。repo_name 是 GitNexus registry
    里的仓库名（通常 = 目录名）。
    """
    result = await mcp_client.call_tool(
        "cypher",
        {"query": "MATCH (p:Process) RETURN p.label AS label"},
    )
    rows = result.get("rows", []) if isinstance(result, dict) else []
    labels = [
        r["label"] for r in rows
        if isinstance(r, dict) and r.get("label")
    ]
    logger.info("process_trace_reader: %d process labels from cypher", len(labels))

    traces: list[ProcessTrace] = []
    for label in labels:
        try:
            uri = f"gitnexus://repo/{repo_name}/process/{label}"
            text = await mcp_client.read_resource(uri)
        except Exception as exc:
            logger.warning("process trace read failed for %r: %s", label, exc)
            continue
        steps = parse_trace_steps(text)
        if not steps:
            logger.debug("process trace %r has no parseable steps; skipped", label)
            continue
        traces.append(ProcessTrace(
            label=label, steps=steps, step_count=len(steps),
        ))
    logger.info("process_trace_reader: %d/%d traces parsed", len(traces), len(labels))
    return traces
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest -q packages/core/tests/code_index/test_process_trace_reader.py`
Expected: PASS（4 个）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/process_trace_reader.py packages/core/tests/code_index/test_process_trace_reader.py
git commit -m "feat(gitnexus): process_trace_reader — read process trace resources to ProcessTrace"
```

---

## Task 4: trace → `CallChain` 四级对齐（G1）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/process_trace_reader.py`（加 `trace_to_chain`）
- Test: `packages/core/tests/code_index/test_process_trace_reader.py`

**Interfaces:**
- Consumes: `ProcessTrace`（Task 3）+ `list[FuncBlock]`（tree-sitter blocks）。
- Produces: `def trace_to_chain(trace: ProcessTrace, blocks: list[FuncBlock]) -> CallChain | None` —— 四级对齐 `(idx,name,file)` → `FuncBlock.id`；失败占位 `<file>:<name>` + `has_unresolved=True`；`entry_point_id=path[0]`。

- [ ] **Step 1: 写失败测试**

在 `test_process_trace_reader.py` 顶部 import 加 `CallChain`、`FuncBlock`，并新增测试：

```python
from shannon_core.code_index.models import CallChain, FuncBlock
from shannon_core.code_index.process_trace_reader import trace_to_chain


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
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest -q packages/core/tests/code_index/test_process_trace_reader.py -k trace_to_chain`
Expected: FAIL — `ImportError: cannot import name 'trace_to_chain'`。

- [ ] **Step 3: 实现 `trace_to_chain`**

在 `process_trace_reader.py` 末尾追加（import 区加 `from collections import defaultdict` 和 `from shannon_core.code_index.models import CallChain, FuncBlock`）：

```python
def trace_to_chain(trace: ProcessTrace, blocks: list[FuncBlock]) -> CallChain | None:
    """把 ProcessTrace 转成 CallChain —— steps 四级对齐到 FuncBlock.id。

    四级匹配（spec §4.4）：
      ① (file_path, name) 精确
      ② file_path 尾匹配（GitNexus filePath 与 tree-sitter 出入时兜底）
      ③ name 全仓唯一（文件不符也认）
      ④ 失败 → 占位 "<file>:<name>" + has_unresolved=True
    """
    by_full: dict[tuple[str, str], FuncBlock] = {}
    by_name: dict[str, list[FuncBlock]] = defaultdict(list)
    for b in blocks:
        by_full.setdefault((b.file_path, b.function_name), b)
        by_name[b.function_name].append(b)

    def resolve(name: str, fpath: str) -> tuple[str, bool]:
        # ① 精确
        b = by_full.get((fpath, name))
        if b:
            return b.id, True
        # ② 尾匹配
        for cc in by_name.get(name, []):
            if cc.file_path == fpath or cc.file_path.endswith(fpath) or fpath.endswith(cc.file_path):
                return cc.id, True
        # ③ name 唯一
        cands = by_name.get(name, [])
        if len(cands) == 1:
            return cands[0].id, True
        # ④ 占位
        return f"{fpath}:{name}", False

    path: list[str] = []
    has_unresolved = False
    for _idx, name, fpath in trace.steps:
        block_id, ok = resolve(name, fpath)
        path.append(block_id)
        if not ok:
            has_unresolved = True
    if not path:
        return None
    return CallChain(
        entry_point_id=path[0],
        path=path,
        depth=len(path) - 1,
        has_unresolved=has_unresolved,
    )
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest -q packages/core/tests/code_index/test_process_trace_reader.py`
Expected: PASS（全部，含 Task 3 的 4 个 + 本 Task 4 个）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/process_trace_reader.py packages/core/tests/code_index/test_process_trace_reader.py
git commit -m "feat(gitnexus): trace_to_chain 4-level FuncBlock alignment (exact/tail/unique/placeholder)"
```

---

## Task 5: 重写 `build_call_graph_from_gitnexus` + chains 非空回归（G1）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/gitnexus_call_graph.py:165-285`（重写 `build_call_graph_from_gitnexus`）
- Modify: `packages/core/tests/code_index/test_gitnexus_call_graph.py`（旧 cypher/BFS 测试改写）

**Interfaces:**
- Consumes: `read_all_process_traces` + `trace_to_chain`（Task 3/4）。
- Produces: `build_call_graph_from_gitnexus(repo_path, mcp_client, blocks) -> CallGraphResult` —— `chains` 来自 process trace；`entry_points` = 各 chain `path[0]` 对应 FuncBlock（去重）；`edges=[]`（废弃）。repo 未索引（cypher probe 返 None）→ raise `GitNexusNotIndexedError`。

- [ ] **Step 1: 改写测试（先定义新契约）**

`test_gitnexus_call_graph.py` 的 `TestBuildCallGraphFromGitnexus` 整类替换为新 process trace 版本（旧 `query`/`cypher` 驱动的 4 个测试作废）。在文件顶部 import 区把 `process_trace_reader` 的 reader 接入 FakeMCP。替换为：

```python
class FakeTraceMCPClient:
    """Fake MCP: cypher 返 process labels；read_resource 按 label 返 trace 文本。
    cypher 返 None 表示未索引。"""
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
```

同时**删除**旧的 `test_builds_call_graph_from_mcp` / `test_cypher_rows_produce_edges_and_chains` / `test_cypher_none_or_no_rows_yields_no_edges` / `test_builds_chains_from_edges`（它们测旧 cypher/BFS 路径，已作废）。`TestImpactTracing` / `TestPipelineAutoIndexing` 保留（不动）。

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest -q packages/core/tests/code_index/test_gitnexus_call_graph.py::TestBuildCallGraphFromGitnexus`
Expected: FAIL — 旧实现仍走 query+cypher+BFS，`result.chains` 不符新契约。

- [ ] **Step 3: 重写 `build_call_graph_from_gitnexus`**

改 `gitnexus_call_graph.py`。顶部 import 加：

```python
from pathlib import Path
from shannon_core.code_index.process_trace_reader import read_all_process_traces, trace_to_chain
```

把 `build_call_graph_from_gitnexus`（:165-285）整体替换为：

```python
async def build_call_graph_from_gitnexus(
    repo_path: str,
    mcp_client: "object",
    blocks: list[FuncBlock],
) -> CallGraphResult:
    """Build a call graph from GitNexus process trace resources.

    process trace = GitNexus 索引时预计算的 entry→terminal 调用链。替代旧的
    「全量 cypher CALLS 边 + Python BFS 重建」——后者 readline 崩、空壳、不通用。

    流程：
      1. cypher probe（MATCH Process）→ None 表示未索引 → raise
      2. read_all_process_traces → ProcessTrace[]
      3. trace_to_chain 每条 → CallChain[]
      4. entry_points = 各 chain path[0] 对应 FuncBlock（去重）；edges=[]（废弃）
    """
    probe = await mcp_client.call_tool(
        "cypher",
        {"query": "MATCH (p:Process) RETURN p.label AS label"},
    )
    if probe is None:
        raise GitNexusNotIndexedError(
            f"GitNexus has not indexed repository: {repo_path}"
        )

    repo_name = Path(repo_path).name
    traces = await read_all_process_traces(mcp_client, repo_name)

    chains: list[CallChain] = []
    for trace in traces:
        chain = trace_to_chain(trace, blocks)
        if chain and chain.path:
            chains.append(chain)

    block_by_id: dict[str, FuncBlock] = {b.id: b for b in blocks}
    entry_blocks: list[FuncBlock] = []
    seen: set[str] = set()
    for ch in chains:
        head = block_by_id.get(ch.entry_point_id)
        if head and head.id not in seen:
            entry_blocks.append(head)
            seen.add(head.id)

    logger.info(
        "build_call_graph_from_gitnexus: %d traces → %d chains, %d entries",
        len(traces), len(chains), len(entry_blocks),
    )

    return CallGraphResult(
        edges=[],
        chains=chains,
        entry_points=entry_blocks,
        degradation_report=DegradationReport(
            total_edges=0, resolved_count=0, unresolved_count=0,
        ),
    )
```

- [ ] **Step 4: 删除废弃的 BFS 辅助函数**

删除 `gitnexus_call_graph.py` 里的 `_build_chains_from_edges`（:59-148）、`_resolve_caller_to_block_id`（:151-162）、`_build_upstream_chains`（:288-311，如未被 `trace_from_sink` 使用）、`_parse_process_response`（:21-56）。删前先 grep 确认无其它调用：

Run: `grep -rn "_build_chains_from_edges\|_resolve_caller_to_block_id\|_parse_process_response" packages/ --include="*.py" | grep -v test`
Expected: 只剩 `gitnexus_call_graph.py` 自身定义（无外部调用）→ 安全删。

若 `trace_from_sink` 仍依赖 `_build_upstream_chains`，保留它（`trace_from_sink` 是死代码但旧 `TestImpactTracing` 引用，本 task 不删 `trace_from_sink`）。

- [ ] **Step 5: 跑测试验证通过**

Run: `pytest -q packages/core/tests/code_index/test_gitnexus_call_graph.py`
Expected: PASS（新 4 个 + 保留的 TestImpactTracing/TestPipelineAutoIndexing）。注意 `TestPipelineAutoIndexing::test_success_path_returns_parameter_graph` patch 了 `build_call_graph_from_gitnexus`，不受影响。

- [ ] **Step 6: 回归 injection 判定语义不变（吃新 chains）**

Run: `pytest -q packages/core/tests/code_index/test_chain_propagator.py`
Expected: PASS（`propagate_across_chains` 只读 `chain.path`，新 chains 字段同构）。

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/code_index/gitnexus_call_graph.py packages/core/tests/code_index/test_gitnexus_call_graph.py
git commit -m "refactor(gitnexus): build_call_graph_from_gitnexus → process trace (drop cypher BFS)"
```

---

## Task 6: `impact_supplement.py`（G1 补充，决策 2）

**Files:**
- Create: `packages/core/src/shannon_core/code_index/impact_supplement.py`
- Test: `packages/core/tests/code_index/test_impact_supplement.py`

**Interfaces:**
- Consumes: `mcp_client.call_tool("impact", {target, file_path, direction})` → `{byDepth, risk, affected_processes}`。
- Produces:
  - `async def impact_upstream(mcp_client, name, file_path) -> dict`
  - `async def impact_downstream(mcp_client, name, file_path) -> dict`
  - 返回 `{byDepth, risk, affected_processes}`（**不产 path**）；失败/超时/None → `{}`。

- [ ] **Step 1: 写失败测试**

创建 `test_impact_supplement.py`：

```python
"""impact_supplement 单元测试。"""
import pytest
from shannon_core.code_index.impact_supplement import (
    impact_upstream, impact_downstream,
)


class FakeImpactMCP:
    def __init__(self, response=None, error=False):
        self._response = response
        self._error = error
        self.calls = []

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        if self._error:
            raise RuntimeError("timeout")
        return self._response


@pytest.mark.asyncio
async def test_impact_upstream_returns_bydepth_risk():
    """必带 file_path 消歧（Go 仓纯 name ambiguous 率高）。"""
    mcp = FakeImpactMCP(response={
        "byDepth": {"1": [{"name": "caller"}]},
        "risk": "HIGH",
        "affected_processes": [{"name": "Flow"}],
    })
    out = await impact_upstream(mcp, name="Save", file_path="repo.go")
    assert out["risk"] == "HIGH"
    assert "1" in out["byDepth"]
    assert len(out["affected_processes"]) == 1
    # 确认带了 file_path
    args = mcp.calls[0][1]
    assert args["target"] == "Save"
    assert args["file_path"] == "repo.go"
    assert args["direction"] == "upstream"


@pytest.mark.asyncio
async def test_impact_downstream_empty_on_none():
    mcp = FakeImpactMCP(response=None)
    assert await impact_downstream(mcp, "x", "f.go") == {}


@pytest.mark.asyncio
async def test_impact_upstream_empty_on_exception():
    """超时/异常 → log + {}，不抛（补充层 best-effort）。"""
    mcp = FakeImpactMCP(error=True)
    assert await impact_upstream(mcp, "x", "f.go") == {}
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest -q packages/core/tests/code_index/test_impact_supplement.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.code_index.impact_supplement'`。

- [ ] **Step 3: 实现 `impact_supplement.py`**

创建 `packages/core/src/shannon_core/code_index/impact_supplement.py`：

```python
"""GitNexus impact 定向可达性补充（spec §4.5，决策 2）。

impact 提供 upstream/downstream 的 byDepth 分层可达闭包 + risk + affected_processes，
用于 sink/source 消歧、可达性确认、risk 标注。**不产出 chain.path**（path 只来自
process trace）——这是纯补充层。

Go 仓纯 name ambiguous 率极高，**必带 file_path** 消歧（见 memory
gitnexus-1.6.7-real-machine-behavior）。失败/超时/None → {}（best-effort）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def _impact(mcp_client, name: str, file_path: str, direction: str) -> dict:
    try:
        result = await mcp_client.call_tool("impact", {
            "target": name,
            "file_path": file_path,
            "direction": direction,
        })
    except Exception as exc:
        logger.warning("impact %s %s (%s) failed: %s", direction, name, file_path, exc)
        return {}
    if not isinstance(result, dict):
        return {}
    return {
        "byDepth": result.get("byDepth", {}) or {},
        "risk": result.get("risk"),
        "affected_processes": result.get("affected_processes", []) or [],
    }


async def impact_upstream(mcp_client, name: str, file_path: str) -> dict:
    """谁依赖 name（caller→name 方向）。不产 path。"""
    return await _impact(mcp_client, name, file_path, "upstream")


async def impact_downstream(mcp_client, name: str, file_path: str) -> dict:
    """name 依赖谁（name→callee 方向）。不产 path。"""
    return await _impact(mcp_client, name, file_path, "downstream")
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest -q packages/core/tests/code_index/test_impact_supplement.py`
Expected: PASS（3 个）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/impact_supplement.py packages/core/tests/code_index/test_impact_supplement.py
git commit -m "feat(gitnexus): impact_supplement — upstream/downstream reachability (no path)"
```

---

## Task 7: entry 组装 detect ∪ process（G2）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py:210-231`（entry 组装段）
- Test: 见下方（通过 `build_code_index_with_gitnexus` 集成测，或新增 entry 组装单测）

**Interfaces:**
- Consumes: `call_graph.entry_points`（FuncBlock[]，来自 Task 5）+ `detect_entry_points(blocks)`。
- Produces: `CodeIndex.entry_points` = `detect_entry_points(...) ∪ {process entry}`，按 `func_block_id` 去重；同 id detect 优先（保留 route/http_method）；process entry 用 `entry_type="gitnexus_process"` / `route=None` / `http_method=None` / `source="gitnexus"`。

- [ ] **Step 1: 写失败测试**

在 `test_gitnexus_call_graph.py` 的 `TestPipelineAutoIndexing` 内（或新建 `test_entry_assembly.py`）追加。这里加到 `TestPipelineAutoIndexing`：

```python
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
                                with patch("shannon_core.code_index.propagate_across_chains", return_value=[]):
                                    index, _ = await build_code_index_with_gitnexus(
                                        str(tmp_path), mcp_client=FakeImpactMCPClient(responses={}),
                                        llm_client=AsyncMock(return_value="{}"),
                                    )

        by_id = {ep.func_block_id: ep for ep in index.entry_points}
        assert cli.id in by_id and by_id[cli.id].entry_type == "cli"           # detect 优先
        assert upload.id in by_id and by_id[upload.id].entry_type == "gitnexus_process"  # process 补
        assert by_id[upload.id].route is None
        assert by_id[upload.id].source == "gitnexus"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest -q packages/core/tests/code_index/test_gitnexus_call_graph.py::TestPipelineAutoIndexing::test_entry_points_union_detect_and_process`
Expected: FAIL — 旧逻辑是 detect ∩ gitnexus（intersect），process-only 的 `upload` 会被旧 `entry_type="gitnexus"` 且交集逻辑漏掉/类型不符。

- [ ] **Step 3: 改 entry 组装（`__init__.py:210-231`）**

把 `__init__.py` 的 `⑦ Convert GitNexus entry_point ...` 段（:210-231）替换为：

```python
    # ⑦ entry 组装：detect_entry_points ∪ process entry（G2）
    #    process entry = call_graph.entry_points(path[0] FuncBlock) 中 detect 未识别的，
    #    entry_type="gitnexus_process"（SRPC/RPC 业务入口，非 HTTP）；同 id 时 detect 优先
    #    （保留其 route/http_method）。替代旧的 detect ∩ gitnexus（intersect）。
    all_entry_points = detect_entry_points(all_blocks, language, repo_path=str(repo))
    detected_ids = {ep.func_block_id for ep in all_entry_points}
    process_entries: list[EntryPoint] = []
    for ep_block in call_graph.entry_points:
        if ep_block.id not in detected_ids:
            process_entries.append(EntryPoint(
                func_block_id=ep_block.id,
                entry_type="gitnexus_process",
                route=None,
                http_method=None,
                confidence=0.9,
                evidence=f"GitNexus process entry: {ep_block.function_name}",
                needs_llm_review=False,
                source="gitnexus",
            ))
    gitnexus_entry_points = list(all_entry_points) + process_entries
    logger.info(
        "entry assembly: %d detect + %d gitnexus_process = %d total",
        len(all_entry_points), len(process_entries), len(gitnexus_entry_points),
    )
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest -q packages/core/tests/code_index/test_gitnexus_call_graph.py::TestPipelineAutoIndexing`
Expected: PASS（含新的 union 测试 + 既有 success/hard-fail 测试）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/__init__.py packages/core/tests/code_index/test_gitnexus_call_graph.py
git commit -m "feat(gitnexus): entry assembly = detect ∪ process (gitnexus_process type)"
```

---

## Task 8: authz `find_unguarded_sink_paths` 四处改（G3）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py`（`find_unguarded_sink_paths` + `IDORCandidateChain` + `build_authz_gitnexus_track` 可观测性）
- Test: `packages/core/tests/code_index/test_authz_dominance.py`

**Interfaces:**
- Consumes: `index.entry_points`（含 `gitnexus_process`，Task 7）+ `index.chains`（process trace，Task 5）。
- Produces: `find_unguarded_sink_paths(index) -> list[IDORCandidateChain]`，四处改：
  1. entry 过滤扩 `http_route`/`rpc`/`gitnexus_process` + **`gitnexus_process` 放宽 `route is not None`**（断点①）。
  2. sink 扫**全链任意步** side-effect（替 `path[-1]`）。
  3. ownership guard 扫 `entry→sink_step` 段（替只查 handler）。
  4. 可观测性：`build_authz_gitnexus_track` log 加 `gitnexus_process` entry 统计（断点③）。
- `IDORCandidateChain` 加 `sink_step_idx: int`。

- [ ] **Step 1: 写失败测试（新行为）**

在 `test_authz_dominance.py` 追加（顶部 import 加 `gitnexus_process` entry 构造辅助）：

```python
def _proc_ep(handler_id):
    """process entry: entry_type='gitnexus_process', route=None（SRPC 业务入口）。"""
    return EntryPoint(
        func_block_id=handler_id, entry_type="gitnexus_process", route=None,
        http_method=None, confidence=0.9, evidence="GitNexus process entry",
        needs_llm_review=False, source="gitnexus",
    )


def test_process_entry_route_none_is_admitted():
    """断点①: process entry route=None 必须进候选（不能被 route is not None 挡）。"""
    handler = _block("h.js:f:1", "function f(req){ await s(req.id); }")
    sink = _block("s.js:g:1", "function g(){ db.user.update(); }")
    chain = CallChain(entry_point_id=handler.id, path=[handler.id, sink.id], depth=1, has_unresolved=False)
    index = _idx([handler, sink], [], [chain], [_proc_ep(handler.id)])
    cands = find_unguarded_sink_paths(index)
    assert len(cands) == 1
    assert cands[0].endpoint_id == handler.id


def test_side_effect_sink_in_middle_of_chain_is_found():
    """断点②(决策7): sink 在链中间(非 terminal) → 扫全链命中。模拟 0→21 的核心。
    链: entry → middle(side-effect sink) → leaf(非 sink)。terminal 非 sink。"""
    entry = _block("e.js:e:1", "function e(req){ m(req); leaf(); }")
    middle = _block("m.js:m:1", "function m(){ db.user.update(); }")   # side-effect sink 在中间
    leaf = _block("l.js:l:1", "function l(){ return 1; }")             # terminal 非 sink
    chain = CallChain(entry_point_id=entry.id, path=[entry.id, middle.id, leaf.id], depth=2, has_unresolved=False)
    index = _idx([entry, middle, leaf], [], [chain], [_ep(entry.id, "/api/x")])
    cands = find_unguarded_sink_paths(index)
    assert len(cands) == 1
    c = cands[0]
    assert c.sink_id == middle.id
    assert c.sink_step_idx == 1   # middle 是 path[1]


def test_ownership_guard_on_segment_blocks_candidate():
    """决策6: ownership 守卫出现在 entry→sink_step 段 → 不产候选。"""
    entry = _block("e.js:e:1", "function e(req){ const o = db.find({where:{userId:req.user.id}}); m(o); }")
    middle = _block("m.js:m:1", "function m(){ db.user.update(); }")
    chain = CallChain(entry_point_id=entry.id, path=[entry.id, middle.id], depth=1, has_unresolved=False)
    index = _idx([entry, middle], [], [chain], [_ep(entry.id, "/api/x")])
    # entry 源码含 ownership 谓词 → handler_has_ownership_guard 短路（既有逻辑）→ 无候选
    assert find_unguarded_sink_paths(index) == []
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest -q packages/core/tests/code_index/test_authz_dominance.py -k "process_entry_route_none or side_effect_sink_in_middle"`
Expected: FAIL — 旧实现 entry 过滤要 `route is not None`（process entry 被挡）+ 看 `path[-1]`（terminal 非 sink → 漏 middle）。

- [ ] **Step 3: 改 `IDORCandidateChain` 加 `sink_step_idx`**

`authz_gitnexus_track.py:50-57` 的 `IDORCandidateChain`：

```python
@dataclass(frozen=True)
class IDORCandidateChain:
    """A handler→sink path flagged as a potential IDOR (no ownership guard)."""
    endpoint_id: str          # EntryPoint.func_block_id of the handler
    handler_id: str           # FuncBlock.id of the handler (= endpoint_id here)
    sink_id: str              # FuncBlock.id of the side-effect sink
    sink_step_idx: int        # sink 在 path 中的下标（spec §4.8 决策7，扫全链）
    path: tuple[str, ...]     # ordered FuncBlock.id list, handler→sink
    guard_nodes_on_path: tuple[str, ...]  # ownership-guard node ids on path (empty=none)
```

- [ ] **Step 4: 重写 `find_unguarded_sink_paths`（四处改）**

替换 `authz_gitnexus_track.py:84-145` 的 `find_unguarded_sink_paths`：

```python
def _segment_has_ownership_guard(segment_ids: list[str], blocks_by_id: dict[str, FuncBlock]) -> bool:
    """entry→sink_step 段（含两端）任一 FuncBlock 源码含 ownership 谓词 → True（决策6）。"""
    from shannon_core.code_index.patterns import OWNERSHIP_PREDICATE_RE
    for sid in segment_ids:
        b = blocks_by_id.get(sid)
        if b is not None and OWNERSHIP_PREDICATE_RE.search(b.source_code):
            return True
    return False


# authz 判定的 entry 类型白名单（spec §4.8 改1）。gitnexus_process 放宽 route 守卫。
_AUTHZ_ENTRY_TYPES = ("http_route", "rpc", "gitnexus_process")


def find_unguarded_sink_paths(
    index: CodeIndex,
    *,
    max_paths_per_endpoint: int = 20,
) -> list[IDORCandidateChain]:
    """Find handler→sink paths lacking an ownership guard (IDOR candidates).

    四处改（spec §4.8）：
      1. entry 过滤扩 http_route/rpc/gitnexus_process；gitnexus_process 放宽 route 守卫。
      2. sink 扫全链任意步 side-effect（替 path[-1]）。
      3. ownership guard 扫 entry→sink_step 段（替只查 handler）。
    handler 自身含 ownership 谓词 → 短路（dominance，既有逻辑保留）。
    Dedup by (endpoint_id, sink_id). Capped per endpoint.
    """
    blocks_by_id: dict[str, FuncBlock] = {b.id: b for b in index.blocks}
    # 改1: entry 过滤 + gitnexus_process 放宽 route 守卫（断点①）
    entry_eps = [
        ep for ep in index.entry_points
        if ep.entry_type in _AUTHZ_ENTRY_TYPES
        and (ep.entry_type == "gitnexus_process" or ep.route is not None)
    ]

    candidates: list[IDORCandidateChain] = []
    seen: set[tuple[str, str]] = set()  # (endpoint_id, sink_id)

    for ep in entry_eps:
        handler = blocks_by_id.get(ep.func_block_id)
        if handler is None:
            continue
        if _handler_has_ownership_guard(handler):
            continue
        count_for_ep = 0
        for chain in index.chains:
            if chain.entry_point_id != ep.func_block_id or not chain.path:
                continue
            # 改2: 扫全链找 side-effect sink（替 path[-1]）
            for step_idx, sid in enumerate(chain.path):
                if not _is_side_effect_sink(blocks_by_id.get(sid)):
                    continue
                key = (ep.func_block_id, sid)
                if key in seen:
                    continue
                # 改3: ownership 扫 entry→sink_step 段
                if _segment_has_ownership_guard(chain.path[: step_idx + 1], blocks_by_id):
                    continue
                seen.add(key)
                candidates.append(IDORCandidateChain(
                    endpoint_id=ep.func_block_id,
                    handler_id=ep.func_block_id,
                    sink_id=sid,
                    sink_step_idx=step_idx,
                    path=tuple(chain.path),
                    guard_nodes_on_path=(),
                ))
                count_for_ep += 1
                if count_for_ep >= max_paths_per_endpoint:
                    break
            if count_for_ep >= max_paths_per_endpoint:
                break

    logger.info(
        "authz GitNexus track: %d entry endpoints (%d gitnexus_process), %d IDOR candidate chains",
        len(entry_eps),
        sum(1 for ep in entry_eps if ep.entry_type == "gitnexus_process"),
        len(candidates),
    )
    return candidates
```

- [ ] **Step 5: 修既有 dominance 测试的 `sink_step_idx`**

既有 `test_authz_dominance.py` 测试构造的 `IDORCandidateChain` 断言不涉及 `sink_step_idx`，但 `test_candidate_when_no_ownership_guard_reaches_sink` 等若断言 `IDORCandidateChain` 字段需注意新增的 `sink_step_idx`（dataclass frozen，构造处理路径=[handler,sink] 时 step_idx=1）。检查既有测试断言不直接构造 `IDORCandidateChain`（只读返回值）→ 无需改。Run 回归确认。

- [ ] **Step 6: 改 4 可观测性（`build_authz_gitnexus_track` log）**

`authz_gitnexus_track.py:346-351` 的 log 加 `gitnexus_process` 统计（断点③）：

```python
    gn_process_count = sum(
        1 for ep in index.entry_points if ep.entry_type == "gitnexus_process"
    )
    logger.info(
        "authz GitNexus track built: %d dominance + %d framework candidates "
        "(entry points: http_route=%d, gitnexus_process=%d, total=%d)",
        len(dominance_cands), len(framework_cands),
        http_route_count, gn_process_count, entry_point_total,
    )
```

- [ ] **Step 7: 跑测试验证通过**

Run: `pytest -q packages/core/tests/code_index/test_authz_dominance.py`
Expected: PASS（新 3 个 + 既有 6 个；既有 `test_candidate_when_no_ownership_guard_reaches_sink` 等 sink 在 terminal，扫全链仍命中）。

- [ ] **Step 8: 回归 authz build track + render**

Run: `pytest -q packages/core/tests/code_index/test_authz_build_track.py packages/core/tests/code_index/test_authz_render_candidates.py packages/core/tests/code_index/test_authz_track_integration.py`
Expected: PASS（render 读 `IDORCandidateChain.path`/`sink_id`/`handler_id`，新增 `sink_step_idx` 不破坏 render）。

- [ ] **Step 9: Commit**

```bash
git add packages/core/src/shannon_core/code_index/authz_gitnexus_track.py packages/core/tests/code_index/test_authz_dominance.py
git commit -m "feat(gitnexus): authz scan full chain + process entry + ownership segment (0->21)"
```

---

## Task 9: 全量回归 + 真机冒烟（收尾）

**Files:**
- 无代码改动；验证 + 文档。

- [ ] **Step 1: 跑全部改动相关测试**

Run:
```bash
pytest -q \
  packages/core/tests/code_index/test_gitnexus_mcp.py \
  packages/core/tests/code_index/test_process_trace_reader.py \
  packages/core/tests/code_index/test_impact_supplement.py \
  packages/core/tests/code_index/test_gitnexus_call_graph.py \
  packages/core/tests/code_index/test_authz_dominance.py \
  packages/core/tests/code_index/test_authz_build_track.py \
  packages/core/tests/code_index/test_authz_render_candidates.py \
  packages/core/tests/code_index/test_authz_track_integration.py \
  packages/core/tests/code_index/test_chain_propagator.py
```
Expected: 全 PASS。若有 FAIL，定位是改动引入还是预存（预存挂起/失败见 `feat-fork-py-test-gotchas` memory，用 `--ignore` 规避 integration）。

- [ ] **Step 2: 双轨铁律回归**

Run: `pytest -q packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py`
Expected: PASS（本 plan 未碰 LLM 轨 prompt / 确定性产物桥梁，铁律不破）。

- [ ] **Step 3: 真机冒烟（statement_template_svr，需人工 + GitNexus 已索引）**

前置：`gitnexus index /root/code/backend/statement_template_svr`（已索引可跳过）；LadybugDB binary 在（`node <npm-global>/gitnexus/node_modules/@ladybugdb/core/install.js` 修复，见 memory）。

用 `scripts/probe_process_sink_match.py` 验证：process trace 端到端读出、trace→CallChain 对齐率、chains 非空。预期日志含 `build_call_graph_from_gitnexus: N traces → M chains`（M > 0），不再 `chains=0 空壳` 警告。

authz 验证：跑 `run_authz_gitnexus_judge` 活动（或 `build_authz_gitnexus_track` 直接调），预期候选 > 0（process entry 进来 + 扫全链命中 side-effect），log 含 `gitnexus_process=N`。

injection 验证：本仓 sink_detector 只 1 sink 且不在 process（spec §2.6），injection GitNexus 轨召回 0 是预期（断点② head-seed + sink 召回限制，靠 LLM 轨兜底）——**不是回归**。

readline 验证：确认全量 cypher（如 `LIMIT 5000` 探针）不再崩 `Separator ... longer than limit`。

- [ ] **Step 4: 更新 memory**

在 `gitnexus-1.6.7-real-machine-behavior` memory 末尾补一条：process trace 下沉已落地（本 plan），生产 chains 不再空壳，authz 0→21；injection head-seed 待 follow-up B′。

- [ ] **Step 5: 最终 Commit（spec + plan + 探针）**

```bash
git add docs/superpowers/specs/2026-06-30-gitnexus-call-chain-offload-design.md \
        docs/superpowers/plans/2026-06-30-gitnexus-call-chain-offload.md \
        scripts/probe_gitnexus_*.py scripts/probe_process_*.py scripts/probe_authz_entry_compat.py
git commit -m "docs(gitnexus): spec + plan + probes for process-trace call-chain offload"
```

---

## Self-Review（plan 作者自检，执行者忽略）

**1. Spec coverage：**
- G4（readline + read_resource）→ Task 1, 2 ✅
- G1（调用链来源 process trace + impact 补充）→ Task 3, 4, 5, 6 ✅
- G2（entry detect ∪ process）→ Task 7 ✅
- G3（authz 四处改 + sink_step_idx）→ Task 8 ✅
- §0.5 断点①（route 守卫）→ Task 8 改1 + test_process_entry_route_none ✅
- §0.5 断点②（head-seed）→ 明确**不**在本 plan（Global Constraints + follow-up B′）✅
- §0.5 断点③（可观测性）→ Task 8 改4 ✅
- §6 回归锚点（chains 非空 / authz 候选>0）→ Task 5 test_chains_nonempty + Task 8 test_side_effect_sink_in_middle ✅
- 双轨铁律回归 → Task 9 Step 2 ✅

**2. Placeholder 扫描：** 无 TODO/TBD；每个 code step 有完整代码；测试有真实断言。

**3. Type consistency：**
- `CallGraphResult(edges, chains, entry_points, degradation_report)` — Task 5 构造与 `models.py:193` 一致 ✅
- `DegradationReport(total_edges, resolved_count, unresolved_count)` — 一致 ✅
- `CallChain(entry_point_id, path, depth, has_unresolved)` — Task 4 构造一致 ✅
- `EntryPoint(func_block_id, entry_type, route, http_method, confidence, evidence, needs_llm_review, source)` — Task 7/8 一致（`source` 是关键字，`authentication` 可选默认 None）✅
- `IDORCandidateChain` 加 `sink_step_idx` — Task 8 定义 + 构造一致 ✅
- `ProcessTrace(label, steps, process_type, step_count)` — Task 3 定义 + Task 4 消费一致 ✅
- `trace_to_chain` / `read_all_process_traces` / `parse_trace_steps` — Task 3 定义、Task 4/5 消费，签名一致 ✅

**注：** `gitnexus_call_graph.py` 里 `trace_from_sink`/`find_sinks_by_patterns`/`get_function_context` 是死代码但被 `TestImpactTracing` 引用——Task 5 不删它们，只删 BFS 那套（`_build_chains_from_edges` 等）。若 `trace_from_sink` 依赖已删的 `_build_upstream_chains`，Task 5 Step 4 保留 `_build_upstream_chains`。
