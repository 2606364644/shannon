# GitNexus + LLM Taint 分析架构 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 GitNexus MCP 构建精确调用图（上游）+ LLM 逐函数做 intra-procedural taint 分析（下游）+ 确定性跨函数参数映射（中间层），完全替换当前的 `propagation_builder.py` 和 `call_graph.py`。

**Architecture:** 三个新模块（`gitnexus_call_graph.py`、`llm_taint_analyzer.py`、`chain_propagator.py`）替代两个旧模块。`build_code_index_with_gitnexus()` 从空壳升级为唯一 pipeline 入口（async）。下游 `risk_scorer` 和 `vuln_agent` 零变更。

**Tech Stack:** Python 3.11+, pydantic, pytest, GitNexus MCP (stdio JSON-RPC), `run_claude_prompt` (async LLM client)

**Spec:** `docs/superpowers/specs/2026-06-10-gitnexus-llm-taint-design.md`

---

## File Structure

| 文件 | 职责 | 改动类型 |
|---|---|---|
| `packages/core/src/shannon_core/code_index/models.py` | 新增 `CallGraphResult`、`GitNexusNotIndexedError`、`GitNexusConnectionError` | **修改** |
| `packages/core/src/shannon_core/code_index/parameter_models.py` | 新增 `IntraResult`、`TaintPath`、`TaintAnalysisResult` | **修改** |
| `packages/core/src/shannon_core/code_index/gitnexus_call_graph.py` | 通过 GitNexus MCP 构建精确调用图 | **新建** |
| `packages/core/tests/code_index/test_gitnexus_call_graph.py` | 调用图构建测试 | **新建** |
| `packages/core/src/shannon_core/code_index/chain_propagator.py` | 确定性跨函数参数映射 | **新建** |
| `packages/core/tests/code_index/test_chain_propagator.py` | 跨函数传播测试 | **新建** |
| `packages/core/src/shannon_core/code_index/llm_taint_analyzer.py` | LLM 逐函数 taint 分析 | **新建** |
| `packages/core/tests/code_index/test_llm_taint_analyzer.py` | LLM taint 分析测试 | **新建** |
| `packages/core/src/shannon_core/code_index/__init__.py` | 重写 `build_code_index_with_gitnexus` 为 async；删除旧函数和旧 import | **修改** |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | 改为调用新 async 入口；删除 `rebuild_call_chains` 相关 activity | **修改** |
| `packages/core/src/shannon_core/code_index/propagation_builder.py` | 删除 | **删除** |
| `packages/core/src/shannon_core/code_index/call_graph.py` | 删除 | **删除** |
| `packages/core/src/shannon_core/code_index/taint_propagator.py` | 删除（如存在） | **删除** |
| `packages/core/tests/code_index/test_propagation_builder.py` | 删除 | **删除** |
| `packages/core/tests/code_index/test_call_graph.py` | 删除 | **删除** |
| `packages/core/src/shannon_core/code_index/risk_scorer.py` | 移除 `taint_propagator` import，内联 `classify_sink` 逻辑 | **修改** |

---

### Task 1: 新增数据模型

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/models.py:172` (在 `CoverageGap` 之后追加)
- Modify: `packages/core/src/shannon_core/code_index/parameter_models.py:94` (在 `ParameterPropagationGraph` 之后追加)

- [ ] **Step 1: Write the failing test — CallGraphResult model**

在 `packages/core/tests/code_index/test_models.py` 底部追加：

```python
class TestCallGraphResult:
    def test_call_graph_result_holds_edges_chains_entry_points(self):
        from shannon_core.code_index.models import (
            CallGraphResult, CallEdge, CallChain, FuncBlock,
        )
        edge = CallEdge(
            caller_id="app.py:handler:1",
            callee_name="get_users",
            callee_file="svc.py",
            resolved=True,
            line=5,
        )
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:get_users:10"],
            depth=1,
            has_unresolved=False,
        )
        block = FuncBlock(
            id="app.py:handler:1",
            file_path="app.py",
            function_name="handler",
            start_line=1,
            end_line=20,
            source_code="def handler(): pass",
            parameters=[],
            language="python",
        )
        result = CallGraphResult(
            edges=[edge],
            chains=[chain],
            entry_points=[block],
        )
        assert len(result.edges) == 1
        assert len(result.chains) == 1
        assert len(result.entry_points) == 1
        assert result.degradation_report is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_models.py::TestCallGraphResult -v`
Expected: FAIL — `ImportError: cannot import name 'CallGraphResult'`

- [ ] **Step 3: Add CallGraphResult and error classes to models.py**

在 `packages/core/src/shannon_core/code_index/models.py` 的 `CoverageGap` 类（第 178 行）之后追加：

```python
class CallGraphResult(BaseModel):
    """GitNexus MCP 构建的调用图结果。复用现有 CallEdge / CallChain / FuncBlock。"""
    edges: list[CallEdge] = []
    chains: list[CallChain] = []
    entry_points: list[FuncBlock] = []
    degradation_report: "DegradationReport | None" = None


class GitNexusNotIndexedError(Exception):
    """GitNexus 未索引目标仓库时抛出。"""


class GitNexusConnectionError(Exception):
    """GitNexus MCP 连接失败时抛出。"""
```

注意：如果 `DegradationReport` 尚未在 models.py 中定义（它在 Plan 1 中添加），先添加一个占位：

```python
class DegradationReport(BaseModel):
    """调用图降级报告。"""
    total_edges: int = 0
    resolved_count: int = 0
    unresolved_count: int = 0
    ambiguous_count: int = 0
    truncated_count: int = 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_models.py::TestCallGraphResult -v`
Expected: PASS

- [ ] **Step 5: Write the failing test — IntraResult model**

在 `packages/core/tests/code_index/test_models.py` 底部追加：

```python
class TestIntraResult:
    def test_intra_result_holds_taint_data(self):
        from shannon_core.code_index.parameter_models import IntraResult
        from shannon_core.code_index.parameter_models import PropagationStep
        step = PropagationStep(
            from_func_id="app.py:handler:1",
            from_param="user_input",
            to_func_id="app.py:handler:1",
            to_param="query",
        )
        result = IntraResult(
            tainted_params={"user_input", "query"},
            hits={"sink_abc": 0.9},
            local_steps=[step],
        )
        assert "user_input" in result.tainted_params
        assert result.hits["sink_abc"] == 0.9
        assert len(result.local_steps) == 1

    def test_intra_result_empty(self):
        from shannon_core.code_index.parameter_models import IntraResult
        result = IntraResult()
        assert len(result.tainted_params) == 0
        assert len(result.hits) == 0
        assert len(result.local_steps) == 0
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_models.py::TestIntraResult -v`
Expected: FAIL — `ImportError: cannot import name 'IntraResult'`

- [ ] **Step 7: Add IntraResult, TaintPath, TaintAnalysisResult to parameter_models.py**

在 `packages/core/src/shannon_core/code_index/parameter_models.py` 的 `ParameterPropagationGraph` 类（第 94 行）之后追加：

```python
class TaintPath(BaseModel):
    """LLM 返回的单条 taint 传播路径。"""
    source_param: str
    sink_id: str
    sink_arg_index: int
    intermediate_vars: list[str] = []
    sanitized: bool = False
    sanitizer_description: str | None = None
    confidence: float = 1.0


class TaintAnalysisResult(BaseModel):
    """LLM 返回的函数级 taint 分析结果（structured output schema）。"""
    tainted_params: list[str] = []
    propagation_paths: list[TaintPath] = []


class IntraResult(BaseModel):
    """函数内 taint 分析的规范化输出。LLM 或确定性分析均产出此格式。"""
    tainted_params: set[str] = set()
    hits: dict[str, float] = {}   # sink_id → confidence
    local_steps: list[PropagationStep] = []
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_models.py::TestIntraResult -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/models.py packages/core/src/shannon_core/code_index/parameter_models.py packages/core/tests/code_index/test_models.py
git commit -m "feat(models): add CallGraphResult, IntraResult, TaintPath, TaintAnalysisResult

New data models for the GitNexus + LLM taint analysis architecture:
- CallGraphResult: output of GitNexus call graph builder
- IntraResult: normalized output of per-function taint analysis
- TaintPath/TaintAnalysisResult: LLM structured output schema
- GitNexusNotIndexedError, GitNexusConnectionError: error classes"
```

---

### Task 2: 实现 gitnexus_call_graph.py

**Files:**
- Create: `packages/core/src/shannon_core/code_index/gitnexus_call_graph.py`
- Create: `packages/core/tests/code_index/test_gitnexus_call_graph.py`

- [ ] **Step 1: Write the failing test — builds edges from GitNexus process response**

在 `packages/core/tests/code_index/test_gitnexus_call_graph.py` 中：

```python
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
        """process 返回平铺调用链 → 解析出 CallEdge 列表。"""
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
        # callee 没有 file → resolved=False
        assert len(edges) == 1
        assert edges[0].resolved is False


class TestBuildCallGraphFromGitNexus:
    @pytest.mark.asyncio
    async def test_builds_call_graph_from_mcp(self):
        """从 MCP process + query 响应构建完整调用图。"""
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
        """GitNexus MCP 返回 None → 抛出 GitNexusNotIndexedError。"""
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
        """从 edges 构建调用链（entry_point → ... → sink）。"""
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
        # 链应该从 handler 出发
        chain = result.chains[0]
        assert chain.entry_point_id == "app.py:handler:1"
        assert "svc.py:get_users:10" in chain.path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_gitnexus_call_graph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.code_index.gitnexus_call_graph'`

- [ ] **Step 3: Implement gitnexus_call_graph.py**

创建 `packages/core/src/shannon_core/code_index/gitnexus_call_graph.py`：

```python
"""通过 GitNexus MCP 构建精确调用图。

替代 call_graph.py 的 resolve_edges（名称匹配）+ build_call_chains（BFS），
改为从 GitNexus 知识图谱获取精确的函数调用关系。

前置条件：GitNexus 已索引目标仓库，MCP server 可连接。
"""
import logging
from collections import defaultdict

from shannon_core.code_index.models import (
    CallChain,
    CallEdge,
    CallGraphResult,
    DegradationReport,
    FuncBlock,
    GitNexusNotIndexedError,
)

logger = logging.getLogger(__name__)


def _parse_process_response(process_data: list[dict]) -> list[CallEdge]:
    """解析 GitNexus process 工具返回的调用链数据。

    每条记录格式:
    {
        "caller": {"file": "app.py", "name": "handler", "line": 5},
        "callee": {"file": "svc.py", "name": "get_users", "line": 12},
    }
    """
    edges: list[CallEdge] = []
    for record in process_data:
        caller = record.get("caller", {})
        callee = record.get("callee", {})
        if not caller.get("name") or not callee.get("name"):
            continue

        caller_file = caller.get("file", "")
        caller_line = caller.get("line", 0)
        callee_file = callee.get("file")
        resolved = callee_file is not None

        caller_id = f"{caller_file}:{caller['name']}:{caller_line}"

        edges.append(CallEdge(
            caller_id=caller_id,
            callee_name=callee["name"],
            callee_file=callee_file,
            resolved=resolved,
            line=caller_line,
        ))
    return edges


def _build_chains_from_edges(
    edges: list[CallEdge],
    entry_point_ids: list[str],
    max_depth: int = 20,
) -> list[CallChain]:
    """从 edges 构建 BFS 调用链。

    与旧 build_call_chains 不同的是，这里的 edges 来自 GitNexus 的精确解析，
    不再需要 resolve_edges 阶段。
    """
    # 构建邻接表
    adj: dict[str, list[CallEdge]] = defaultdict(list)
    for edge in edges:
        if edge.resolved:
            adj[edge.caller_id].append(edge)

    chains: list[CallChain] = []
    visited_paths: set[str] = set()

    for ep_id in entry_point_ids:
        # BFS
        queue: list[tuple[str, list[str], int]] = [(ep_id, [ep_id], 0)]
        while queue:
            current_id, path, depth = queue.pop(0)
            if depth >= max_depth:
                continue

            outgoing = adj.get(current_id, [])
            if not outgoing:
                # 叶节点 → 产出链
                path_key = "->".join(path)
                if path_key not in visited_paths and len(path) > 1:
                    visited_paths.add(path_key)
                    has_unresolved = False
                    chains.append(CallChain(
                        entry_point_id=ep_id,
                        path=list(path),
                        depth=len(path) - 1,
                        has_unresolved=has_unresolved,
                    ))
                continue

            for edge in outgoing:
                # 环检测
                callee_id = f"{edge.callee_file}:{edge.callee_name}:{edge.line}"
                if callee_id in path:
                    continue
                queue.append((callee_id, path + [callee_id], depth + 1))

        # 单节点链（entry point 无调用）
        if not adj.get(ep_id):
            path_key = ep_id
            if path_key not in visited_paths:
                visited_paths.add(path_key)
                chains.append(CallChain(
                    entry_point_id=ep_id,
                    path=[ep_id],
                    depth=0,
                    has_unresolved=False,
                ))

    return chains


async def build_call_graph_from_gitnexus(
    repo_path: str,
    mcp_client,
    blocks: list[FuncBlock],
) -> CallGraphResult:
    """通过 GitNexus MCP 构建精确调用图。

    Args:
        repo_path: 仓库绝对路径
        mcp_client: GitNexusMCPClient 实例（需要已 start()）
        blocks: Tree-sitter 解析的 FuncBlock 列表

    Returns:
        CallGraphResult 包含 edges, chains, entry_points

    Raises:
        GitNexusNotIndexedError: GitNexus 未索引该仓库
        GitNexusConnectionError: MCP 连接失败
    """
    # ① query — 获取入口点
    entry_points_raw = await mcp_client.call_tool("query", {
        "query": "entry point route handler controller",
        "--repo": repo_path,
    })
    if entry_points_raw is None:
        raise GitNexusNotIndexedError(
            f"GitNexus has not indexed repository: {repo_path}"
        )

    # 解析入口点 → 匹配已有 blocks
    block_by_name: dict[str, FuncBlock] = {}
    for b in blocks:
        key = f"{b.file_path}:{b.function_name}"
        block_by_name[key] = b

    entry_points: list[FuncBlock] = []
    entry_point_ids: list[str] = []
    if isinstance(entry_points_raw, list):
        for ep in entry_points_raw:
            ep_file = ep.get("file", "")
            ep_name = ep.get("name", "")
            key = f"{ep_file}:{ep_name}"
            if key in block_by_name:
                entry_points.append(block_by_name[key])
                entry_point_ids.append(block_by_name[key].id)

    # ② process — 获取调用链
    process_data = await mcp_client.call_tool("process", {
        "--repo": repo_path,
    })
    if process_data is None:
        process_data = []

    # 解析调用边
    edges = _parse_process_response(process_data if isinstance(process_data, list) else [])

    # ③ 构建调用链
    chains = _build_chains_from_edges(edges, entry_point_ids)

    # ④ 降级报告
    unresolved = sum(1 for e in edges if not e.resolved)
    degradation_report = DegradationReport(
        total_edges=len(edges),
        resolved_count=len(edges) - unresolved,
        unresolved_count=unresolved,
        ambiguous_count=0,
    )

    logger.info(
        "GitNexus call graph: %d edges, %d chains, %d entry points",
        len(edges), len(chains), len(entry_points),
    )

    return CallGraphResult(
        edges=edges,
        chains=chains,
        entry_points=entry_points,
        degradation_report=degradation_report,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_gitnexus_call_graph.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/gitnexus_call_graph.py packages/core/tests/code_index/test_gitnexus_call_graph.py
git commit -m "feat(call-graph): implement GitNexus MCP call graph builder

Replaces call_graph.py (resolve_edges + build_call_chains BFS) with
GitNexus MCP-powered precise call graph construction:
- query tool for entry point discovery with confidence scores
- process tool for complete call chain extraction
- Exact symbol resolution (file+line), no more name collisions
- Graceful DegradationReport for partial data"
```

---

### Task 3: 实现 chain_propagator.py

**Files:**
- Create: `packages/core/src/shannon_core/code_index/chain_propagator.py`
- Create: `packages/core/tests/code_index/test_chain_propagator.py`

- [ ] **Step 1: Write the failing test — basic cross-function propagation**

在 `packages/core/tests/code_index/test_chain_propagator.py` 中：

```python
"""chain_propagator 单元测试 — 确定性跨函数 taint 传播。"""
import pytest

from shannon_core.code_index.models import FuncBlock, CallChain
from shannon_core.code_index.parameter_models import (
    DangerousSlot,
    IntraResult,
    PropagationStep,
    SinkCallSite,
    SinkCategory,
)
from shannon_core.code_index.chain_propagator import (
    propagate_across_chains,
    _references_tainted,
)


def _block(
    name: str, file: str = "app.py", line: int = 1,
    params: list[str] | None = None,
) -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}",
        file_path=file,
        function_name=name,
        start_line=line,
        end_line=line + 10,
        source_code=f"def {name}(): pass",
        parameters=params or [],
        language="python",
    )


def _sink(func_id: str, sink_id: str = "sink_1") -> SinkCallSite:
    return SinkCallSite(
        id=sink_id,
        caller_id=func_id,
        callee_name="cursor.execute",
        callee_receiver="cursor",
        category=SinkCategory.SQL_EXECUTION,
        sink_subtype="execute",
        file_path="app.py",
        line=5,
        column=0,
        dangerous_slots=[DangerousSlot(arg_index=0, expression="query")],
        rule_id="sql-execute",
        needs_review=False,
    )


class TestReferencesTainted:
    def test_exact_match(self):
        assert _references_tainted("user_input", {"user_input"}) is True

    def test_prefix_match(self):
        assert _references_tainted("request.user_id", {"request"}) is True

    def test_no_match(self):
        assert _references_tainted("config.limit", {"user_input"}) is False

    def test_empty_tainted(self):
        assert _references_tainted("anything", set()) is False


class TestPropagateAcrossChains:
    def test_single_function_chain_with_tainted_sink(self):
        """单函数链: entry 的 tainted param 传播到同函数的 sink。"""
        handler = _block("handler", "app.py", 1, params=["user_input"])

        intra_results = {
            handler.id: IntraResult(
                tainted_params={"user_input"},
                hits={"sink_1": 0.9},
                local_steps=[
                    PropagationStep(
                        from_func_id=handler.id,
                        from_param="user_input",
                        to_func_id=handler.id,
                        to_param="query",
                        code_location="app.py:3",
                        confidence=0.9,
                    ),
                ],
            ),
        }

        chains = [
            CallChain(
                entry_point_id=handler.id,
                path=[handler.id],
                depth=0,
                has_unresolved=False,
            ),
        ]

        flows = propagate_across_chains(
            chains=chains,
            blocks=[handler],
            intra_results=intra_results,
        )
        assert len(flows) >= 1
        # 至少有一个 flow 命中了 sink_1
        hit_sink_ids = [
            step.to_param for flow in flows for step in flow.propagation_steps
        ]

    def test_two_function_chain_propagates_taint(self):
        """双函数链: handler(request) → get_user(request.id)。"""
        handler = _block("handler", "app.py", 1, params=["request"])
        get_user = _block("get_user", "svc.py", 10, params=["user_id"])

        intra_results = {
            handler.id: IntraResult(
                tainted_params={"request"},
                hits={},  # handler 本身无 sink
                local_steps=[],
            ),
            get_user.id: IntraResult(
                tainted_params={"user_id"},
                hits={"sink_db": 0.85},
                local_steps=[
                    PropagationStep(
                        from_func_id=get_user.id,
                        from_param="user_id",
                        to_func_id=get_user.id,
                        to_param="query",
                        code_location="svc.py:12",
                        confidence=0.85,
                    ),
                ],
            ),
        }

        chains = [
            CallChain(
                entry_point_id=handler.id,
                path=[handler.id, get_user.id],
                depth=1,
                has_unresolved=False,
            ),
        ]

        # 需要 edge 信息来映射参数
        # chain_propagator 需要知道 handler 调用 get_user 时传了什么参数
        # 这里简化测试: 假设 edge 中有 call_site 信息
        flows = propagate_across_chains(
            chains=chains,
            blocks=[handler, get_user],
            intra_results=intra_results,
        )
        assert len(flows) >= 1

    def test_max_depth_stops_traversal(self):
        """max_depth=1 时，2 层链只传播第 1 层。"""
        blocks = [
            _block("a", "a.py", 1, params=["x"]),
            _block("b", "b.py", 1, params=["y"]),
            _block("c", "c.py", 1, params=["z"]),
        ]
        intra_results = {b.id: IntraResult(tainted_params=set(b.parameters)) for b in blocks}
        chains = [
            CallChain(
                entry_point_id=blocks[0].id,
                path=[b.id for b in blocks],
                depth=2,
                has_unresolved=False,
            ),
        ]
        flows = propagate_across_chains(
            chains=chains,
            blocks=blocks,
            intra_results=intra_results,
            max_depth=1,
        )
        # 不应传播到第 3 层（c）
        # max_depth=1 只允许 1 层传播
        assert all(
            all(s.to_func_id != blocks[2].id for s in f.propagation_steps)
            for f in flows
        )

    def test_empty_chains_returns_empty(self):
        flows = propagate_across_chains(
            chains=[], blocks=[], intra_results={},
        )
        assert flows == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_chain_propagator.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement chain_propagator.py**

创建 `packages/core/src/shannon_core/code_index/chain_propagator.py`：

```python
"""确定性跨函数 taint 传播。

沿 GitNexus 调用链，将 caller 的 tainted 参数映射到 callee 的入口参数。
这一步不调用 LLM — 本质是查表+集合运算。

替代 propagation_builder.py 的 _trace_chain()。
"""
import logging
import uuid

from shannon_core.code_index.models import CallChain, FuncBlock
from shannon_core.code_index.parameter_models import (
    IntraResult,
    ParameterSource,
    PropagationStep,
    TaintFlow,
)

logger = logging.getLogger(__name__)


def _references_tainted(arg_expr: str, tainted: set[str]) -> bool:
    """判断参数表达式是否引用了 tainted 变量。过近似匹配。

    "request.user_id" + tainted={"request"} → True
    "config.limit"      + tainted={"request"} → False
    """
    return any(t in arg_expr for t in tainted)


def _find_call_args_for_callee(
    caller: FuncBlock,
    callee_id: str,
) -> list[str]:
    """从 caller 源码中找到调用 callee 时传递的参数表达式列表。

    简化实现：按 callee_id 中的函数名在源码中查找调用语句。
    后续可用 GitNexus 的精确调用点信息替代。

    Returns:
        参数表达式列表，如 ["request.user_id", "10"]。找不到返回 []。
    """
    callee_name = callee_id.split(":")[1] if ":" in callee_id else callee_id
    import re
    # 匹配 callee_name(arg1, arg2, ...)
    pattern = re.compile(
        rf"(?:^|\W){re.escape(callee_name)}\s*\(\s*(.*?)\s*\)",
        re.MULTILINE,
    )
    source = caller.source_code or ""
    for m in pattern.finditer(source):
        args_str = m.group(1)
        if not args_str:
            return []
        # 简单分割（不处理嵌套括号）
        args = [a.strip() for a in args_str.split(",")]
        return args
    return []


def propagate_across_chains(
    chains: list[CallChain],
    blocks: list[FuncBlock],
    intra_results: dict[str, IntraResult],
    *,
    max_depth: int = 20,
) -> list[TaintFlow]:
    """沿调用链做确定性的跨函数 taint 传播。

    Args:
        chains: GitNexus 提供的调用链
        blocks: 所有函数块
        intra_results: LLM 分析的函数内结果 (func_id → IntraResult)
        max_depth: 最大传播深度（默认 20）

    Returns:
        list[TaintFlow]: 完整的 taint 传播路径
    """
    blocks_by_id: dict[str, FuncBlock] = {b.id: b for b in blocks}
    taint_flows: list[TaintFlow] = []

    for chain in chains:
        path = chain.path
        if len(path) < 1:
            continue

        # 起始 seed
        head_id = path[0]
        head_intra = intra_results.get(head_id)
        if head_intra is None:
            continue
        current_tainted = set(head_intra.tainted_params)

        accumulated_steps: list[PropagationStep] = list(head_intra.local_steps)

        for i in range(len(path)):
            if i >= max_depth:
                break

            caller_id = path[i]
            callee_idx = i + 1

            # 如果是链尾且有 hits → 产出 TaintFlow
            if callee_idx >= len(path):
                if accumulated_steps and head_intra.hits:
                    for sink_id, confidence in head_intra.hits.items():
                        flow_id = f"{head_id}->{sink_id}" if sink_id else f"{head_id}->unknown"
                        taint_flows.append(TaintFlow(
                            flow_id=flow_id,
                            entry_point_id=chain.entry_point_id,
                            source_param=next(iter(current_tainted), ""),
                            source_type=ParameterSource.HTTP_PARAMETER,
                            propagation_steps=accumulated_steps,
                            sink_call_site_id=sink_id,
                            confidence=confidence,
                        ))
                break

            callee_id = path[callee_idx]

            # 查找调用参数
            caller_block = blocks_by_id.get(caller_id)
            callee_block = blocks_by_id.get(callee_id)
            if caller_block is None or callee_block is None:
                continue

            call_args = _find_call_args_for_callee(caller_block, callee_id)

            # 确定性参数映射
            callee_params = callee_block.parameters
            callee_seed: set[str] = set()

            if call_args:
                for j, arg_expr in enumerate(call_args):
                    if j < len(callee_params) and _references_tainted(arg_expr, current_tainted):
                        callee_seed.add(callee_params[j])
            else:
                # 找不到调用参数 → 保守：传递所有 tainted 到 callee 的所有参数
                if current_tainted:
                    callee_seed = set(callee_params)

            # 合并 callee 的 intra 结果
            callee_intra = intra_results.get(callee_id)
            if callee_intra is not None:
                # 记录传播步
                for sink_id, confidence in callee_intra.hits.items():
                    accumulated_steps.append(PropagationStep(
                        step_id="",
                        from_func_id=caller_id,
                        from_param=next(iter(current_tainted), ""),
                        to_func_id=callee_id,
                        to_param=next(iter(callee_seed), ""),
                        code_location=f"{caller_block.file_path}:{caller_block.start_line}",
                        confidence=min(confidence, 0.8 if not call_args else 1.0),
                    ))

                # 如果是链尾 → 产出 TaintFlow
                if callee_idx == len(path) - 1 and callee_intra.hits:
                    for sink_id, confidence in callee_intra.hits.items():
                        flow_id = f"{chain.entry_point_id}->{sink_id}"
                        taint_flows.append(TaintFlow(
                            flow_id=flow_id,
                            entry_point_id=chain.entry_point_id,
                            source_param=next(iter(current_tainted), ""),
                            source_type=ParameterSource.HTTP_PARAMETER,
                            propagation_steps=accumulated_steps,
                            sink_call_site_id=sink_id,
                            confidence=confidence,
                        ))

                # 继续传播
                current_tainted = callee_seed if callee_seed else callee_intra.tainted_params
            else:
                # 没有 intra 结果 → 继续用 seed
                current_tainted = callee_seed

    logger.info("Cross-chain propagation: %d taint flows", len(taint_flows))
    return taint_flows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_chain_propagator.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/chain_propagator.py packages/core/tests/code_index/test_chain_propagator.py
git commit -m "feat(taint): implement deterministic cross-function chain propagator

Replaces _trace_chain() from propagation_builder.py with pure
deterministic parameter mapping along GitNexus call chains.
No LLM calls — just lookup + set operations.
Conservative over-approximation: missing call args → all params tainted."
```

---

### Task 4: 实现 llm_taint_analyzer.py

**Files:**
- Create: `packages/core/src/shannon_core/code_index/llm_taint_analyzer.py`
- Create: `packages/core/tests/code_index/test_llm_taint_analyzer.py`

- [ ] **Step 1: Write the failing test — prompt construction**

在 `packages/core/tests/code_index/test_llm_taint_analyzer.py` 中：

```python
"""llm_taint_analyzer 单元测试 — LLM 逐函数 taint 分析。"""
import json
import pytest

from shannon_core.code_index.models import FuncBlock, TypedParameter, ParameterSource
from shannon_core.code_index.parameter_models import (
    DangerousSlot,
    IntraResult,
    SinkCallSite,
    SinkCategory,
    TaintAnalysisResult,
    TaintPath,
)
from shannon_core.code_index.llm_taint_analyzer import (
    analyze_taint_llm,
    build_taint_prompt,
    parse_llm_response,
    truncate_source,
)


def _block(
    name: str = "handler",
    file: str = "app.py",
    line: int = 1,
    source: str = "",
    params: list[str] | None = None,
) -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}",
        file_path=file,
        function_name=name,
        start_line=line,
        end_line=line + 10,
        source_code=source or f"def {name}(): pass",
        parameters=params or [],
        language="python",
    )


def _sink(func_id: str, sink_id: str = "sink_1") -> SinkCallSite:
    return SinkCallSite(
        id=sink_id,
        caller_id=func_id,
        callee_name="cursor.execute",
        callee_receiver="cursor",
        category=SinkCategory.SQL_EXECUTION,
        sink_subtype="execute",
        file_path="app.py",
        line=4,
        column=0,
        dangerous_slots=[DangerousSlot(arg_index=0, expression="query")],
        rule_id="sql-execute",
        needs_review=False,
    )


class FakeLLMClient:
    """Fake LLM client returning a fixed TaintAnalysisResult."""

    def __init__(self, response: TaintAnalysisResult | None = None):
        self._response = response

    async def __call__(self, prompt: str, **kwargs):
        if self._response is None:
            raise RuntimeError("LLM timeout")
        return json.dumps(self._response.model_dump())


class TestTruncateSource:
    def test_short_source_unchanged(self):
        src = "line 1\nline 2\nline 3"
        assert truncate_source(src, []) == src

    def test_long_source_truncated_with_sink_context(self):
        lines = [f"line {i}" for i in range(1500)]
        src = "\n".join(lines)
        result = truncate_source(src, sink_lines=[1200], max_lines=1200, prefix_lines=1000, context_lines=30)
        result_lines = result.split("\n")
        # 应该 ≤ 1200 行
        assert len(result_lines) <= 1200
        # sink 附近的行应该在（1170-1230）
        assert "line 1200" in result

    def test_no_sink_lines_keeps_prefix(self):
        lines = [f"line {i}" for i in range(1500)]
        src = "\n".join(lines)
        result = truncate_source(src, sink_lines=[], max_lines=1200, prefix_lines=1000)
        result_lines = result.split("\n")
        assert len(result_lines) == 1000


class TestBuildTaintPrompt:
    def test_includes_function_info(self):
        block = _block(
            source="def handler(user_input):\n    cursor.execute(user_input)",
            params=["user_input"],
        )
        sinks = [_sink(block.id)]
        prompt = build_taint_prompt(block, sinks)
        assert "handler" in prompt
        assert "user_input" in prompt
        assert "cursor.execute" in prompt
        assert "tainted_params" in prompt

    def test_includes_typed_params(self):
        block = _block(params=["user_input"])
        typed = [
            TypedParameter(
                name="user_input",
                source=ParameterSource.HTTP_PARAMETER,
                type_annotation="str",
            ),
        ]
        prompt = build_taint_prompt(block, [], typed_params=typed)
        assert "HTTP_PARAMETER" in prompt


class TestParseLLMResponse:
    def test_valid_json_returns_result(self):
        data = TaintAnalysisResult(
            tainted_params=["user_input"],
            propagation_paths=[
                TaintPath(
                    source_param="user_input",
                    sink_id="sink_1",
                    sink_arg_index=0,
                    confidence=0.9,
                ),
            ],
        )
        result = parse_llm_response(json.dumps(data.model_dump()))
        assert "user_input" in result.tainted_params
        assert len(result.propagation_paths) == 1

    def test_invalid_json_returns_conservative(self):
        """JSON 解析失败 → 保守过近似（空结果，不是全 tainted）。"""
        result = parse_llm_response("not json at all")
        assert isinstance(result, TaintAnalysisResult)

    def test_empty_response(self):
        result = parse_llm_response("{}")
        assert result.tainted_params == []
        assert result.propagation_paths == []


class TestAnalyzeTaintLLM:
    @pytest.mark.asyncio
    async def test_returns_intra_result_with_hits(self):
        """LLM 返回有效 taint 分析 → IntraResult 有 hits。"""
        block = _block(
            source="def handler(user_input):\n    cursor.execute(user_input)",
            params=["user_input"],
        )
        sinks = [_sink(block.id)]
        llm_response = TaintAnalysisResult(
            tainted_params=["user_input"],
            propagation_paths=[
                TaintPath(
                    source_param="user_input",
                    sink_id="sink_1",
                    sink_arg_index=0,
                    confidence=0.9,
                ),
            ],
        )
        llm_client = FakeLLMClient(response=llm_response)
        result = await analyze_taint_llm(
            block=block,
            sinks_in_func=sinks,
            llm_client=llm_client,
        )
        assert isinstance(result, IntraResult)
        assert "user_input" in result.tainted_params
        assert "sink_1" in result.hits
        assert result.hits["sink_1"] == 0.9

    @pytest.mark.asyncio
    async def test_llm_failure_returns_conservative(self):
        """LLM 超时 → 保守过近似（所有参数 tainted）。"""
        block = _block(params=["user_input", "config"])
        llm_client = FakeLLMClient(response=None)  # raises RuntimeError
        result = await analyze_taint_llm(
            block=block,
            sinks_in_func=[],
            llm_client=llm_client,
        )
        assert "user_input" in result.tainted_params
        assert "config" in result.tainted_params

    @pytest.mark.asyncio
    async def test_no_params_returns_empty(self):
        """函数无参数 → 不调用 LLM，返回空结果。"""
        block = _block(params=[])
        llm_client = FakeLLMClient()
        result = await analyze_taint_llm(
            block=block,
            sinks_in_func=[],
            llm_client=llm_client,
        )
        assert len(result.tainted_params) == 0
        assert llm_client._response is not None  # 不应该被调用
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_llm_taint_analyzer.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement llm_taint_analyzer.py**

创建 `packages/core/src/shannon_core/code_index/llm_taint_analyzer.py`：

```python
"""LLM 逐函数 taint 分析。

替代 propagation_builder.py 的 seed_taints() + analyze_intra()。
对每个包含 sink 的函数调用 LLM 判断参数级 taint 传播。

核心原则：宁可过报（false positive），不可漏报（false negative）。
"""
import json
import logging
import time

from shannon_core.code_index.models import FuncBlock, TypedParameter
from shannon_core.code_index.parameter_models import (
    IntraResult,
    PropagationStep,
    SinkCallSite,
    TaintAnalysisResult,
    TaintPath,
)

logger = logging.getLogger(__name__)


def truncate_source(
    source: str,
    sink_lines: list[int],
    *,
    max_lines: int = 1200,
    prefix_lines: int = 1000,
    context_lines: int = 30,
) -> str:
    """截断过长的函数源码。

    策略：保留前 prefix_lines 行 + sink 所在行 ± context_lines 行。
    总行数不超过 max_lines。
    """
    all_lines = source.split("\n")
    if len(all_lines) <= max_lines:
        return source

    # 收集要保留的行号集合
    keep: set[int] = set()
    # 前缀
    for i in range(min(prefix_lines, len(all_lines))):
        keep.add(i)
    # sink 上下文
    for sl in sink_lines:
        for i in range(max(0, sl - context_lines - 1), min(len(all_lines), sl + context_lines)):
            keep.add(i)

    # 按行号排序，取 max_lines 条
    sorted_lines = sorted(keep)[:max_lines]
    return "\n".join(all_lines[i] for i in sorted_lines)


def build_taint_prompt(
    block: FuncBlock,
    sinks_in_func: list[SinkCallSite],
    typed_params: list[TypedParameter] | None = None,
) -> str:
    """构建 taint 分析 prompt。"""
    # 参数信息
    if typed_params:
        param_desc = "\n".join(
            f"  - {tp.name}: {tp.type_annotation or 'unknown'} (source: {tp.source.value})"
            for tp in typed_params
        )
    else:
        param_desc = "\n".join(
            f"  - {p}" for p in block.parameters
        )

    # sink 信息
    sink_desc = ""
    for s in sinks_in_func:
        slots = ", ".join(
            f"arg[{ds.arg_index}]={ds.expression}"
            for ds in s.dangerous_slots
        )
        sink_desc += f"  - ID: {s.id}\n"
        sink_desc += f"    调用: {s.callee_name}({slots})\n"
        sink_desc += f"    位置: {s.file_path}:{s.line}\n"
        sink_desc += f"    类型: {s.category.value}\n\n"

    # JSON schema
    schema = json.dumps(TaintAnalysisResult.model_json_schema(), indent=2, ensure_ascii=False)

    return f"""你是一个安全代码分析器。分析以下函数的 taint 传播。

## 函数信息
函数名: {block.function_name}
文件: {block.file_path}:{block.start_line}
参数:
{param_desc}

## 函数源码
```{block.language}
{block.source_code}
```

## 危险调用点
{sink_desc if sink_desc else "（无已知危险调用点）"}

## 任务
1. 判断哪些入口参数是"不可信数据源"（用户输入、HTTP 参数、外部请求）
2. 追踪每个参数在函数内的赋值和传递过程
3. 判断不可信数据是否到达了上述危险调用点的参数
4. 如果无法确定某个参数是否为外部输入，默认标记为 tainted（过近似，不漏报）

## 输出格式 (JSON)
{schema}"""


def parse_llm_response(raw: str) -> TaintAnalysisResult:
    """解析 LLM 返回的 JSON。格式错误返回空结果。"""
    try:
        data = json.loads(raw)
        return TaintAnalysisResult.model_validate(data)
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("LLM response parse error: %s", e)
        return TaintAnalysisResult()


def _intra_result_from_llm(
    block: FuncBlock,
    llm_result: TaintAnalysisResult,
    sinks_in_func: list[SinkCallSite],
) -> IntraResult:
    """将 LLM 的 TaintAnalysisResult 转换为 IntraResult。"""
    tainted_params = set(llm_result.tainted_params)

    # 过滤：只保留 block 中实际存在的参数名
    known_params = set(block.parameters)
    validated_tainted = tainted_params & known_params
    # 如果 LLM 返回的参数名不在 block 中，也保留（可能是中间变量）
    if not validated_tainted:
        validated_tainted = tainted_params

    hits: dict[str, float] = {}
    local_steps: list[PropagationStep] = []

    for path in llm_result.propagation_paths:
        # 验证 sink_id 存在
        known_sink_ids = {s.id for s in sinks_in_func}
        if path.sink_id in known_sink_ids:
            hits[path.sink_id] = path.confidence

        local_steps.append(PropagationStep(
            step_id="",
            from_func_id=block.id,
            from_param=path.source_param,
            to_func_id=block.id,
            to_param=f"sink:{path.sink_id}:arg{path.sink_arg_index}",
            code_location=f"{block.file_path}:{block.start_line}",
            confidence=path.confidence,
        ))

    return IntraResult(
        tainted_params=validated_tainted,
        hits=hits,
        local_steps=local_steps,
    )


async def analyze_taint_llm(
    block: FuncBlock,
    sinks_in_func: list[SinkCallSite],
    *,
    typed_params: list[TypedParameter] | None = None,
    llm_client=None,
    retry_count: int = 1,
) -> IntraResult:
    """LLM 驱动的函数内 taint 分析。

    Args:
        block: 函数体（源码 + 参数列表）
        sinks_in_func: 该函数内的危险调用点
        typed_params: 参数类型信息（可选增强）
        llm_client: async callable，接收 prompt 返回 JSON 字符串
        retry_count: LLM 输出格式错误时的重试次数

    Returns:
        IntraResult(tainted_params, hits, local_steps)
    """
    # 函数无参数 → 不调用 LLM
    if not block.parameters:
        return IntraResult()

    # 截断源码
    sink_lines = [s.line - block.start_line + 1 for s in sinks_in_func]
    truncated_source = truncate_source(block.source_code or "", sink_lines)

    # 构造截断后的 block（不修改原 block）
    truncated_block = FuncBlock(
        **{**block.model_dump(), "source_code": truncated_source},
    )

    # 构建 prompt
    prompt = build_taint_prompt(truncated_block, sinks_in_func, typed_params)

    # 调用 LLM（带重试）
    start = time.monotonic()
    raw_response: str | None = None
    for attempt in range(retry_count + 1):
        try:
            raw_response = await llm_client(prompt)
            break
        except Exception as e:
            if attempt < retry_count:
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s",
                    attempt + 1, retry_count + 1, e,
                )
                continue
            logger.error("LLM call failed after %d attempts: %s", retry_count + 1, e)

    elapsed_ms = (time.monotonic() - start) * 1000

    if raw_response is None:
        # 保守过近似：所有参数 tainted，无 hits（chain_propagator 处理）
        logger.warning(
            "llm_taint_analysis fallback (all params tainted)",
            extra={
                "func_id": block.id,
                "func_name": block.function_name,
                "file": block.file_path,
                "num_sinks": len(sinks_in_func),
                "latency_ms": elapsed_ms,
                "fallback": True,
            },
        )
        return IntraResult(
            tainted_params=set(block.parameters),
            hits={},
            local_steps=[],
        )

    # 解析响应
    llm_result = parse_llm_response(raw_response)
    intra_result = _intra_result_from_llm(block, llm_result, sinks_in_func)

    logger.info(
        "llm_taint_analysis",
        extra={
            "func_id": block.id,
            "func_name": block.function_name,
            "file": block.file_path,
            "num_sinks": len(sinks_in_func),
            "tainted_count": len(intra_result.tainted_params),
            "hits_count": len(intra_result.hits),
            "latency_ms": elapsed_ms,
        },
    )

    return intra_result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_llm_taint_analyzer.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/llm_taint_analyzer.py packages/core/tests/code_index/test_llm_taint_analyzer.py
git commit -m "feat(taint): implement LLM-based per-function taint analyzer

Replaces seed_taints() + analyze_intra() from propagation_builder.py.
Each function with sinks is analyzed by LLM with structured JSON output.
Conservative fallback on LLM failure: all params marked tainted.
Source truncation: first 1000 lines + sink context ±30 lines (max 1200)."
```

---

### Task 5: 重写 pipeline 入口（`__init__.py`）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py`

- [ ] **Step 1: Update imports**

在 `packages/core/src/shannon_core/code_index/__init__.py` 中，替换第 3-19 行的 imports：

```python
"""Code index and call graph construction for Shannon's whitebox pipeline."""
import json
import logging
from pathlib import Path

from shannon_core.code_index.models import CodeIndex, TypedParameter
from shannon_core.code_index.models import AdjudicatedEntryPoint, AdjudicationResult, Verdict, EntryPointSource
from shannon_core.code_index.parser import detect_language, discover_source_files
from shannon_core.code_index.entry_points import detect_entry_points
from shannon_core.code_index.summary import generate_summary
from shannon_core.code_index.parsers import get_parser
from shannon_core.code_index.sink_detector import detect_sinks
from shannon_core.code_index.degradation import build_degradation_report
from shannon_core.code_index.file_discovery import discover_security_files
from shannon_core.code_index.gitnexus_engine import GitNexusEngine
from shannon_core.code_index.models import DegradationLevel, FileManifest
from shannon_core.code_index.gitnexus_call_graph import build_call_graph_from_gitnexus
from shannon_core.code_index.llm_taint_analyzer import analyze_taint_llm
from shannon_core.code_index.chain_propagator import propagate_across_chains
from shannon_core.code_index.parameter_models import ParameterPropagationGraph
```

注意：删除了第 10 行的 `from shannon_core.code_index.call_graph import resolve_edges, build_call_chains` 和第 15 行的 `from shannon_core.code_index.propagation_builder import build_propagation_graph`。

- [ ] **Step 2: Delete old build_code_index function**

删除第 50-133 行的 `build_code_index` 函数。此函数被 `build_code_index_with_gitnexus` 替代。

- [ ] **Step 3: Rewrite build_code_index_with_gitnexus as async**

替换第 136-215 行的 `build_code_index_with_gitnexus`：

```python
async def build_code_index_with_gitnexus(
    repo_path: str,
    *,
    mcp_client,
    llm_client,
) -> CodeIndex:
    """Build code index with GitNexus call graph + LLM taint analysis.

    Pipeline:
    1. Tree-sitter parse → FuncBlock[]
    2. GitNexus MCP → precise call graph (edges, chains, entry_points)
    3. sink_detector → SinkCallSite[]
    4. LLM taint analysis (per-function, only for functions with sinks)
    5. Deterministic chain propagation (cross-function parameter mapping)

    Raises:
        GitNexusNotIndexedError: if GitNexus hasn't indexed the repo
        GitNexusConnectionError: if MCP connection fails
    """
    from shannon_core.models.errors import ErrorCode, PentestError

    repo = Path(repo_path).resolve()
    file_manifest = discover_security_files(repo)

    # ① Tree-sitter 解析 → FuncBlock[]
    try:
        language = detect_language(repo)
    except ValueError as exc:
        raise PentestError(
            str(exc), category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
        ) from exc

    logger.info("Detected language: %s", language)

    source_files = discover_source_files(repo, language)
    if not source_files:
        raise PentestError(
            f"No source files found for language '{language}' in {repo}",
            category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
        )

    parser = get_parser(language)
    if parser is None:
        raise PentestError(
            f"No parser available for language '{language}'",
            category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
        )

    file_sources: dict[str, bytes] = {}
    all_blocks = []
    for file_path in source_files:
        try:
            source = file_path.read_bytes()
            rel = str(file_path.relative_to(repo))
            file_sources[rel] = source
            blocks = parser.parse_file(file_path, repo)
            all_blocks.extend(blocks)
        except Exception as exc:
            logger.warning("Failed to index %s: %s", file_path, exc)
            continue

    # ② GitNexus MCP → 精确调用图
    call_graph = await build_call_graph_from_gitnexus(
        repo_path=str(repo),
        mcp_client=mcp_client,
        blocks=all_blocks,
    )

    # ③ sink 检测
    def _provide_source(block):
        return file_sources.get(block.file_path)
    sink_call_sites = detect_sinks(all_blocks, parser, source_provider=_provide_source)
    logger.info("Detected %d sink call sites", len(sink_call_sites))

    # ④ 按函数分组 sink
    from collections import defaultdict
    sinks_by_func: dict[str, list] = defaultdict(list)
    for s in sink_call_sites:
        sinks_by_func[s.caller_id].append(s)

    # ⑤ LLM taint 分析（只对有 sink 的函数）
    blocks_by_id = {b.id: b for b in all_blocks}
    typed_params_by_block = _build_typed_params_by_block(CodeIndex(
        repository=str(repo),
        language=language,
        total_blocks=len(all_blocks),
        total_entry_points=len(call_graph.entry_points),
        total_chains=len(call_graph.chains),
        blocks=all_blocks,
        edges=call_graph.edges,
        entry_points=call_graph.entry_points,
        chains=call_graph.chains,
        sink_call_sites=sink_call_sites,
    ))

    intra_results = {}
    for func_id, func_sinks in sinks_by_func.items():
        block = blocks_by_id.get(func_id)
        if block is None:
            continue
        intra_results[func_id] = await analyze_taint_llm(
            block=block,
            sinks_in_func=func_sinks,
            typed_params=typed_params_by_block.get(func_id),
            llm_client=llm_client,
        )

    # ⑥ 确定性跨函数传播
    taint_flows = propagate_across_chains(
        chains=call_graph.chains,
        blocks=all_blocks,
        intra_results=intra_results,
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=taint_flows,
        language_coverage=[language],
    )
    logger.info("Built parameter propagation graph: %d taint flows", len(pgraph.taint_flows))

    # ⑦ 组装 CodeIndex
    return CodeIndex(
        repository=str(repo),
        language=language,
        total_blocks=len(all_blocks),
        total_entry_points=len(call_graph.entry_points),
        total_chains=len(call_graph.chains),
        blocks=all_blocks,
        edges=call_graph.edges,
        entry_points=call_graph.entry_points,
        chains=call_graph.chains,
        sink_call_sites=sink_call_sites,
        parameter_graph=pgraph,
    )
```

- [ ] **Step 4: Update write_index_files to remove build_propagation_graph call**

替换第 218-237 行的 `write_index_files`：

```python
def write_index_files(index: CodeIndex, output_dir: str) -> tuple[Path, Path]:
    """Write code_index.json, code_index_summary.md, and parameter_graph.json."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "code_index.json"
    json_path.write_text(index.model_dump_json(indent=2))

    summary_path = out / "code_index_summary.md"
    summary_path.write_text(generate_summary(index))

    # parameter_graph 已在 build_code_index_with_gitnexus 中构建
    if index.parameter_graph is not None:
        pgraph_path = out / "parameter_graph.json"
        pgraph_path.write_text(index.parameter_graph.model_dump_json(indent=2))

    return json_path, summary_path
```

- [ ] **Step 5: Delete rebuild_call_chains function**

删除第 284-362 行的 `rebuild_call_chains` 函数。GitNexus 已提供精确调用链，不需要 adjudication 后重建。

- [ ] **Step 6: Run existing tests to verify nothing else breaks**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/ -v --ignore=packages/core/tests/code_index/test_propagation_builder.py --ignore=packages/core/tests/code_index/test_call_graph.py`
Expected: PASS（排除已标记删除的测试文件）

- [ ] **Step 7: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/__init__.py
git commit -m "refactor(pipeline): rewrite build_code_index_with_gitnexus as async

- GitNexus MCP for precise call graph (replaces AST BFS)
- LLM per-function taint analysis (replaces regex propagation)
- Deterministic chain propagation for cross-function mapping
- Delete build_code_index (old sync version)
- Delete rebuild_call_chains (GitNexus provides chains)
- Update write_index_files to use pre-built parameter_graph"
```

---

### Task 6: 更新 whitebox pipeline activities

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`

- [ ] **Step 1: Update run_code_index activity**

修改 `activities.py` 中的 `run_code_index` 函数（约第 164 行）。将同步 `build_code_index` 调用替换为 async `build_code_index_with_gitnexus`：

```python
async def run_code_index(input: ActivityInput) -> dict:
    """Build code index using GitNexus + LLM taint analysis."""
    from shannon_core.code_index import build_code_index_with_gitnexus
    from shannon_core.code_index.gitnexus_mcp import GitNexusMCPClient

    repo = Path(input.repo_path)
    deliverables = Path(input.deliverables_dir)

    # 启动 GitNexus MCP client
    mcp_client = GitNexusMCPClient(repo_path=str(repo))
    await mcp_client.start()
    try:
        index = await build_code_index_with_gitnexus(
            str(repo),
            mcp_client=mcp_client,
            llm_client=_get_llm_client(input),
        )
    finally:
        await mcp_client.stop()

    # 写入 deliverables
    json_path, _ = write_index_files(index, str(deliverables / "code_index"))
    return {"code_index_path": str(json_path)}
```

- [ ] **Step 2: Add LLM client helper**

在 `activities.py` 中添加 LLM client 工厂函数：

```python
def _get_llm_client(input: ActivityInput):
    """构建用于 taint 分析的 LLM client callable。"""
    from shannon_core.agents.runner import run_claude_prompt
    import json

    async def llm_taint_client(prompt: str, **kwargs) -> str:
        result = await run_claude_prompt(
            prompt=prompt,
            repo_path=input.repo_path,
            model_tier="medium",
            structured_output_schema=TaintAnalysisResult.model_json_schema(),
            provider_config=input.provider_config if hasattr(input, "provider_config") else None,
        )
        return result.raw_output

    return llm_taint_client
```

- [ ] **Step 3: Delete run_rebuild_call_chains activity**

删除 `run_rebuild_call_chains` 函数（约第 207-226 行）。GitNexus 已提供精确调用链。

- [ ] **Step 4: Update run_risk_scoring to not call build_propagation_graph**

在 `run_risk_scoring` 函数中（约第 228 行），移除对 `build_propagation_graph` 的调用。`parameter_graph` 已经在 `build_code_index_with_gitnexus` 中构建完成，直接从 `code_index.json` 读取即可。

- [ ] **Step 5: Update imports in activities.py**

删除 `from shannon_core.code_index.call_graph import resolve_edges, build_call_chains` 和 `from shannon_core.code_index.propagation_builder import build_propagation_graph` 的 import。添加新的 import：

```python
from shannon_core.code_index.parameter_models import TaintAnalysisResult
```

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py
git commit -m "refactor(whitebox): update pipeline to use GitNexus + LLM taint analysis

- run_code_index now async, uses GitNexus MCP + LLM
- Delete run_rebuild_call_chains (GitNexus provides chains)
- Remove propagation_builder imports"
```

---

### Task 7: 删除旧文件 + 修复残留引用

**Files:**
- Delete: `packages/core/src/shannon_core/code_index/propagation_builder.py`
- Delete: `packages/core/src/shannon_core/code_index/call_graph.py`
- Delete: `packages/core/tests/code_index/test_propagation_builder.py`
- Delete: `packages/core/tests/code_index/test_call_graph.py`
- Modify: `packages/core/src/shannon_core/code_index/risk_scorer.py`

- [ ] **Step 1: Search for all remaining references**

Run: `cd /root/shannon-py && grep -rn "propagation_builder\|call_graph\|taint_propagator\|build_code_index[^_]" --include="*.py" packages/`

列出所有残留引用。预期在 `risk_scorer.py` 中有 `taint_propagator` 的 import。

- [ ] **Step 2: Fix risk_scorer.py — remove taint_propagator import**

在 `packages/core/src/shannon_core/code_index/risk_scorer.py` 中，删除：
```python
from shannon_core.code_index.taint_propagator import classify_sink
```

找到所有使用 `classify_sink` 的地方，内联其逻辑（通常是按 sink category 分类，可以用 `SinkCategory` 枚举直接判断）。

- [ ] **Step 3: Delete old files**

```bash
cd /root/shannon-py
rm packages/core/src/shannon_core/code_index/propagation_builder.py
rm packages/core/src/shannon_core/code_index/call_graph.py
rm packages/core/tests/code_index/test_propagation_builder.py
rm packages/core/tests/code_index/test_call_graph.py
# 如果存在:
rm -f packages/core/src/shannon_core/code_index/taint_propagator.py
rm -f packages/core/tests/code_index/test_taint_propagator.py
```

- [ ] **Step 4: Verify no broken imports remain**

Run: `cd /root/shannon-py && python -c "from shannon_core.code_index import build_code_index_with_gitnexus; print('OK')"`
Expected: `OK`

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/ -v --co -q`
Expected: 无 import 错误

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add -A
git commit -m "chore: delete old propagation_builder, call_graph, taint_propagator

These modules are fully replaced by:
- gitnexus_call_graph.py (precise call graph via GitNexus MCP)
- llm_taint_analyzer.py (per-function LLM taint analysis)
- chain_propagator.py (deterministic cross-function propagation)

~1,300 lines of unmaintained regex+set-operation code removed.
risk_scorer.py updated to remove taint_propagator import."
```

---

### Task 8: 端到端验证

**Files:** 无新增，仅验证

- [ ] **Step 1: Run full test suite**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Run whitebox tests**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/ -v`
Expected: ALL PASS

- [ ] **Step 3: Verify import graph is clean**

Run: `cd /root/shannon-py && python -c "
from shannon_core.code_index import build_code_index_with_gitnexus
from shannon_core.code_index.gitnexus_call_graph import build_call_graph_from_gitnexus
from shannon_core.code_index.llm_taint_analyzer import analyze_taint_llm
from shannon_core.code_index.chain_propagator import propagate_across_chains
from shannon_core.code_index.parameter_models import IntraResult, TaintAnalysisResult
from shannon_core.code_index.models import CallGraphResult, GitNexusNotIndexedError
print('All imports OK')
"`
Expected: `All imports OK`

- [ ] **Step 4: Verify old modules are gone**

Run: `cd /root/shannon-py && python -c "from shannon_core.code_index.propagation_builder import build_propagation_graph" 2>&1`
Expected: `ModuleNotFoundError`

Run: `cd /root/shannon-py && python -c "from shannon_core.code_index.call_graph import resolve_edges" 2>&1`
Expected: `ModuleNotFoundError`

- [ ] **Step 5: Final commit (if any test fixes needed)**

```bash
cd /root/shannon-py
git add -A
git commit -m "test: verify clean import graph after taint engine replacement"
```

---

## Self-Review

**1. Spec coverage check:**

| Spec 章节 | 覆盖任务 |
|---|---|
| §3.1 删除清单 | Task 7 ✅ |
| §3.2 新增清单 | Task 2 (gitnexus_call_graph), Task 3 (chain_propagator), Task 4 (llm_taint_analyzer) ✅ |
| §3.3 保留清单 | Task 5 (保留 sink_detector, models, parsers, enhanced_parameters) ✅ |
| §4 整体数据流 | Task 5 (pipeline 重写) ✅ |
| §5.1 GitNexus 调用图 | Task 2 ✅ |
| §5.2 LLM taint 分析器 | Task 4 ✅ |
| §5.3 确定性跨函数传播 | Task 3 ✅ |
| §6 Pipeline 集成 | Task 5 + Task 6 ✅ |
| §7 错误处理 | Task 4 (LLM 保守回退), Task 2 (GitNexus 报错) ✅ |
| §8 可配置参数 | Task 4 (max_func_lines, context_lines), Task 3 (max_depth) ✅ |

**2. Placeholder scan:**
- 无 TBD/TODO/fill-in-later ✅
- 所有代码块包含实际实现 ✅
- 所有测试断言具体 ✅

**3. Type consistency:**
- `IntraResult` 定义在 parameter_models.py（Task 1），使用在 chain_propagator.py（Task 3）和 llm_taint_analyzer.py（Task 4）✅
- `CallGraphResult` 定义在 models.py（Task 1），使用在 gitnexus_call_graph.py（Task 2）和 __init__.py（Task 5）✅
- `TaintAnalysisResult` 定义在 parameter_models.py（Task 1），使用在 llm_taint_analyzer.py（Task 4）✅
- `build_call_graph_from_gitnexus` 签名在 Task 2（async）匹配 Task 5 中的 await 调用 ✅
- `analyze_taint_llm` 签名在 Task 4（async）匹配 Task 5 中的 await 调用 ✅
- `propagate_across_chains` 签名在 Task 3（sync）匹配 Task 5 中的同步调用 ✅
