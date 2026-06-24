# Taint 落盘实现计划（Plan 1）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 taint 通道断层——让 `parameter_graph.json` 在生产落盘并被下游读到（P0），taint 上游空桩改为触发保守回退使 pgraph 非空（P1）。

**Architecture:** `CodeIndex` 加 `parameter_graph` 字段 → `build_code_index_with_gitnexus` 把构建的 pgraph 传入 → `write_index_files` 真正落盘 `parameter_graph.json` → 空桩 `return "{}"` 改为 `raise` 触发 `analyze_taint_llm` 的保守回退（全参数 tainted，过近似，宁过报不漏报）。下游 `run_risk_scoring` / `run_render_dataflow_hints` 已有 `if param_graph_path.exists()` 读取逻辑，**无需改动**。

**Tech Stack:** Python 3.12, pydantic v2, pytest, pytest-asyncio

## Global Constraints

- 不改下游读取逻辑（`activities.py:425-433` / `545-556` 已有 `if exists` 守卫）
- `write_index_files` **保持返回 2 元组**（`activities.py:294` 解包 `json_path, summary_path` 不动）；`parameter_graph.json` 作为副作用写入
- P1 用**保守回退**（不接真实 LLM taint per-function，成本控制；符合 spec §6）
- TDD：每个改动先写失败测试；frequent commits（conventional commits：`feat(code_index):` / `fix(whitebox):`）
- 真实 GitNexus build 路径需 MCP 环境，**单元测试覆盖字段/落盘/回退/闭环，build 真实流转（pgraph 非空）由手动冒烟验证**（spec 已注明端到端冒烟待人工）

---

### Task 1: `CodeIndex` 加 `parameter_graph` 字段

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/models.py:9`（TYPE_CHECKING import）、`:86`（加字段）、`:211`（`_resolve_forward_refs` 注册前向引用）
- Test: `packages/core/tests/code_index/test_parameter_graph_field.py`（Create）

**Interfaces:**
- Produces: `CodeIndex.parameter_graph: "ParameterPropagationGraph | None" = None`（默认 None，向后兼容）

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_parameter_graph_field.py
from shannon_core.code_index.models import CodeIndex
from shannon_core.code_index.parameter_models import ParameterPropagationGraph


def _minimal_index(**overrides):
    return CodeIndex(
        repository="r",
        language="python",
        total_blocks=0,
        total_entry_points=0,
        total_chains=0,
        blocks=[],
        edges=[],
        entry_points=[],
        chains=[],
        **overrides,
    )


def test_code_index_parameter_graph_defaults_none():
    index = _minimal_index()
    assert index.parameter_graph is None


def test_code_index_round_trips_parameter_graph():
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])
    index = _minimal_index(parameter_graph=pgraph)
    restored = CodeIndex.model_validate_json(index.model_dump_json())
    assert restored.parameter_graph is not None
    assert restored.parameter_graph.language_coverage == ["python"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_parameter_graph_field.py -v`
Expected: FAIL — `CodeIndex.__init__()` got an unexpected keyword argument `parameter_graph`

- [ ] **Step 3: Add the field + forward-ref resolution**

Edit `packages/core/src/shannon_core/code_index/models.py`:

Line 9 (extend TYPE_CHECKING import):
```python
if TYPE_CHECKING:
    from shannon_core.code_index.parameter_models import ParameterPropagationGraph, SinkCallSite
```

Line 86 (add field after `sink_call_sites`):
```python
    sink_call_sites: list["SinkCallSite"] = []
    # Spec A taint propagation graph (forward ref; resolved at runtime via model_rebuild)
    parameter_graph: "ParameterPropagationGraph | None" = None
```

Line 211 (extend `_resolve_forward_refs`):
```python
def _resolve_forward_refs() -> None:
    try:
        from shannon_core.code_index.parameter_models import (  # noqa: F401
            ParameterPropagationGraph,
            SinkCallSite,
        )
        CodeIndex.model_rebuild()
    except ImportError:
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_parameter_graph_field.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/models.py packages/core/tests/code_index/test_parameter_graph_field.py
git commit -m "feat(code_index): add parameter_graph field to CodeIndex"
```

---

### Task 2: `write_index_files` 落盘 `parameter_graph.json`

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py:299-312`（`write_index_files`）
- Test: `packages/core/tests/code_index/test_write_index_files_pgraph.py`（Create）

**Interfaces:**
- Consumes: `CodeIndex.parameter_graph`（Task 1）
- Produces: `write_index_files(index, output_dir)` 副作用写 `<output_dir>/parameter_graph.json`（当 `index.parameter_graph` 非 None）；返回值不变 `tuple[Path, Path]`

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_write_index_files_pgraph.py
import json
from pathlib import Path

from shannon_core.code_index import write_index_files
from shannon_core.code_index.models import CodeIndex
from shannon_core.code_index.parameter_models import ParameterPropagationGraph


def _minimal_index(**overrides):
    return CodeIndex(
        repository="r", language="python", total_blocks=0, total_entry_points=0,
        total_chains=0, blocks=[], edges=[], entry_points=[], chains=[], **overrides,
    )


def test_write_index_files_writes_parameter_graph_when_present(tmp_path):
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])
    index = _minimal_index(parameter_graph=pgraph)
    json_path, summary_path = write_index_files(index, str(tmp_path))
    pgraph_path = tmp_path / "parameter_graph.json"
    assert pgraph_path.exists()
    data = json.loads(pgraph_path.read_text())
    assert data["language_coverage"] == ["python"]
    # return value unchanged (2-tuple)
    assert json_path.name == "code_index.json"
    assert summary_path.name == "code_index_summary.md"


def test_write_index_files_skips_parameter_graph_when_none(tmp_path):
    index = _minimal_index()  # parameter_graph defaults None
    write_index_files(index, str(tmp_path))
    assert not (tmp_path / "parameter_graph.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_write_index_files_pgraph.py -v`
Expected: FAIL — `parameter_graph.json` not created

- [ ] **Step 3: Implement the write**

Edit `packages/core/src/shannon_core/code_index/__init__.py:299-312`:

```python
def write_index_files(index: CodeIndex, output_dir: str) -> tuple[Path, Path]:
    """Write code_index.json, code_index_summary.md, and parameter_graph.json."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "code_index.json"
    json_path.write_text(index.model_dump_json(indent=2))

    summary_path = out / "code_index_summary.md"
    summary_path.write_text(generate_summary(index))

    # Spec A: persist taint propagation graph when present
    if index.parameter_graph is not None:
        pgraph_path = out / "parameter_graph.json"
        pgraph_path.write_text(index.parameter_graph.model_dump_json(indent=2))

    return json_path, summary_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_write_index_files_pgraph.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/__init__.py packages/core/tests/code_index/test_write_index_files_pgraph.py
git commit -m "feat(code_index): persist parameter_graph.json in write_index_files"
```

---

### Task 3: `build_code_index_with_gitnexus` 传入 pgraph

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py:214-227`（`CodeIndex(...)` 构造）
- Test: `packages/core/tests/code_index/test_gitnexus_call_graph.py`（扩展 `TestPipelineAutoIndexing`）

**Interfaces:**
- Consumes: `CodeIndex.parameter_graph` 字段（Task 1）、`pgraph` 局部变量（`__init__.py:182-185`，当前被丢弃）
- Produces: GitNexus 成功路径产出的 `CodeIndex` 携带 `parameter_graph`；fallback 路径（`_build_code_index_fallback`）保持 `None`

- [ ] **Step 1: Write the failing test (fallback path asserts None)**

Append to `packages/core/tests/code_index/test_gitnexus_call_graph.py` inside `TestPipelineAutoIndexing`:

```python
    @pytest.mark.asyncio
    async def test_fallback_path_has_no_parameter_graph(self, tmp_path):
        """Fallback (MINIMAL) path never builds a pgraph → parameter_graph is None."""
        from shannon_core.code_index import build_code_index_with_gitnexus

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("def handler(): pass\n")

        with patch("shannon_core.code_index.gitnexus_engine.GitNexusEngine.is_available", return_value=False):
            with patch("shannon_core.code_index._build_code_index_fallback") as mock_fallback:
                from shannon_core.code_index.models import CodeIndex, DegradationLevel
                mock_fallback.return_value = CodeIndex(
                    repository=str(tmp_path), language="python",
                    total_blocks=0, total_entry_points=0, total_chains=0,
                    blocks=[], edges=[], entry_points=[], chains=[],
                    degradation_level=DegradationLevel.MINIMAL,
                )
                mcp = FakeImpactMCPClient(responses={})
                index = await build_code_index_with_gitnexus(
                    str(tmp_path), mcp_client=mcp,
                    llm_client=AsyncMock(return_value="{}"), auto_index=True,
                )
                assert index.parameter_graph is None
```

- [ ] **Step 2: Run test to verify it fails (or passes vacuously — confirms baseline)**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_gitnexus_call_graph.py::TestPipelineAutoIndexing::test_fallback_path_has_no_parameter_graph -v`
Expected: PASS（fallback 本就不传 parameter_graph，默认 None）— 此测试锁定 fallback 不回归。

- [ ] **Step 3: Wire pgraph into the GitNexus success path**

Edit `packages/core/src/shannon_core/code_index/__init__.py:214-227`. Replace the `return CodeIndex(...)` block:

```python
    # ⑧ Assemble CodeIndex
    return CodeIndex(
        repository=str(repo),
        language=language,
        total_blocks=len(all_blocks),
        total_entry_points=len(gitnexus_entry_points),
        total_chains=len(call_graph.chains),
        blocks=all_blocks,
        edges=call_graph.edges,
        entry_points=gitnexus_entry_points,
        chains=call_graph.chains,
        sink_call_sites=sink_call_sites,
        file_manifest=file_manifest,
        degradation_level=DegradationLevel.FULL,
        parameter_graph=pgraph,
    )
```

（删除 `:212-213` 的旧 NOTE 注释——字段现已存在。`_build_code_index_fallback` 保持不传 `parameter_graph`，默认 None。）

- [ ] **Step 4: Run the full code_index test suite to verify no regression**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/ -v`
Expected: PASS（含 Task 1/2 新测试 + 现有 test_gitnexus_call_graph / test_chain_propagator / test_render_dataflow_hints）

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/__init__.py packages/core/tests/code_index/test_gitnexus_call_graph.py
git commit -m "feat(code_index): wire parameter_graph into build_code_index_with_gitnexus"
```

> **手动冒烟（本 plan 外）**：GitNexus 成功路径（`DegradationLevel.FULL`）产出非空 pgraph 需真实 MCP 环境，单元测试无法覆盖。完成后跑一次真实白盒扫描，确认 `parameter_graph.json` 非空且含 taint_flows。

---

### Task 4: P1 — 空桩改为触发保守回退

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:246-248`（`_llm_taint_client`）
- Test: `packages/core/tests/code_index/test_llm_taint_analyzer.py`（已有 `test_llm_failure_returns_conservative` 锁定回退行为；本 task 新增 propagate 非空断言）

**Interfaces:**
- Consumes: `analyze_taint_llm` 的保守回退（`llm_taint_analyzer.py:278-288`，`raw_response is None` 时全参数 tainted）
- Produces: 生产 `_llm_taint_client` raise → `analyze_taint_llm` 走回退 → `propagate_across_chains` 产非空 taint_flows

- [ ] **Step 1: Write the failing test (回退 → propagate 产非空 flow)**

Append to `packages/core/tests/code_index/test_chain_propagator.py`:

```python
@pytest.mark.asyncio
async def test_raising_llm_client_yields_nonempty_flows_via_fallback():
    """P1: when the LLM taint client raises, analyze_taint_llm falls back to
    all-params-tainted, so propagate_across_chains produces non-empty flows."""
    from shannon_core.code_index.llm_taint_analyzer import analyze_taint_llm
    from shannon_core.code_index.chain_propagator import propagate_across_chains
    from shannon_core.code_index.models import FuncBlock, CallChain

    block = FuncBlock(
        id="app.py:handler:1", file_path="app.py", function_name="handler",
        start_line=1, end_line=3, source_code="def handler(q):\n  db(q)\n",
        parameters=["q"], language="python",
    )

    async def raising_client(prompt, **kwargs):
        raise RuntimeError("LLM taint client not wired in production")

    intra = await analyze_taint_llm(block, sinks_in_func=[], llm_client=raising_client)
    # conservative fallback: all params tainted
    assert "q" in intra.tainted_params

    chain = CallChain(entry_point_id="app.py:handler:1", path=["app.py:handler:1"], depth=1, has_unresolved=False)
    flows = propagate_across_chains(chains=[chain], blocks={block.id: block}, intra_results={block.id: intra})
    # non-empty because head_intra.tainted_params is non-empty (no longer skipped)
    assert len(flows) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_chain_propagator.py::test_raising_llm_client_yields_nonempty_flows_via_fallback -v`
Expected: 当前空桩在 `activities.py`，本测试用独立 raising_client，应已 PASS（验证回退→propagate 链路）。若 FAIL，说明 `analyze_taint_llm` 回退或 `propagate_across_chains` 行为异常，先修这两个。

- [ ] **Step 3: Change the production stub to raise (trigger the fallback)**

Edit `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:246-248`:

```python
            async def _llm_taint_client(prompt: str, **kwargs) -> str:
                # P1: real LLM taint per-function is not wired yet (cost).
                # Raising (not returning "{}") lets analyze_taint_llm take its
                # conservative fallback (all params tainted) so the taint channel
                # is non-empty instead of silently dead.
                raise RuntimeError(
                    "LLM taint client not wired in production; "
                    "analyze_taint_llm will use conservative fallback"
                )
```

- [ ] **Step 4: Run the taint analyzer + chain propagator tests**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_llm_taint_analyzer.py packages/core/tests/code_index/test_chain_propagator.py -v`
Expected: PASS（含 `test_llm_failure_returns_conservative` + Task 4 新测试）

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/core/tests/code_index/test_chain_propagator.py
git commit -m "fix(whitebox): taint stub raises to trigger conservative fallback (P1)"
```

---

### Task 5: 集成验证 — 落盘闭环可被下游读回

**Files:**
- Test: `packages/core/tests/code_index/test_taint_persist_integration.py`（Create）

**Interfaces:**
- Consumes: Task 1（字段）、Task 2（write）、Task 4（回退非空 pgraph 语义）
- Produces: 验证 `write_index_files` 产出的 `parameter_graph.json` 能被 `ParameterPropagationGraph.model_validate_json` 读回，且 taint_flows 非空时 hints 渲染非空

- [ ] **Step 1: Write the integration test**

```python
# packages/core/tests/code_index/test_taint_persist_integration.py
import json

from shannon_core.code_index import write_index_files
from shannon_core.code_index.models import CodeIndex
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    ParameterSource,
    TaintFlow,
)


def _minimal_index(pgraph):
    return CodeIndex(
        repository="r", language="python", total_blocks=0, total_entry_points=0,
        total_chains=0, blocks=[], edges=[], entry_points=[], chains=[],
        parameter_graph=pgraph,
    )


def test_persisted_parameter_graph_round_trips_through_disk(tmp_path):
    """P0 闭环: write 落盘的 parameter_graph.json 能被下游 model_validate_json 读回。"""
    flow = TaintFlow(
        entry_point_id="app.py:handler:1",
        source_param="q",
        source_type=ParameterSource.QUERY_PARAM,
    )
    pgraph = ParameterPropagationGraph(taint_flows=[flow], language_coverage=["python"])
    index = _minimal_index(pgraph)

    write_index_files(index, str(tmp_path))

    pgraph_path = tmp_path / "parameter_graph.json"
    assert pgraph_path.exists()

    # 下游 run_risk_scoring / run_render_dataflow_hints 的读取方式
    restored = ParameterPropagationGraph.model_validate_json(pgraph_path.read_text())
    assert len(restored.taint_flows) == 1
    assert restored.taint_flows[0].source_param == "q"
```

> 注：`TaintFlow` 的必填字段以 `parameter_models.py:52-81` 实际定义为准；若 `source_type` 取值名不同，调整为 `ParameterSource.QUERY` 等。先跑一次确认字段名。

- [ ] **Step 2: Run test to verify it fails (or passes)**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_taint_persist_integration.py -v`
Expected: 若 `TaintFlow` 字段名不符 → FAIL（ImportError/TypeError），按 `parameter_models.py` 实际定义修正后 PASS。

- [ ] **Step 3: Run the full code_index + whitebox hints test suite**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/ packages/whitebox/tests/test_render_dataflow_hints.py -v`
Expected: PASS（全绿；确认下游 `run_render_dataflow_hints` 读 pgraph 的现有测试仍过）

- [ ] **Step 4: Commit**

```bash
cd /root/shannon-py
git add packages/core/tests/code_index/test_taint_persist_integration.py
git commit -m "test(code_index): integration test for parameter_graph persist round-trip"
```

---

## Self-Review

**1. Spec coverage**（对照 spec §6 P0/P1）：
- P0-a CodeIndex 字段 → Task 1 ✓
- P0-b build 传 pgraph → Task 3 ✓（真实路径冒烟标注）
- P0-c write_index_files 落盘 → Task 2 ✓
- P0-d 调用者解包 → 无需（保持 2 元组，Global Constraint）✓
- P1 空桩改触发回退 → Task 4 ✓（raise 触发已测回退）
- 下游读取 → 已有 `if exists`，无需改 ✓
- framework-analyzer 接通 / 通用合并器 / GitNexus 索引降级 → **不在本 plan**（Plan 2/3/4）

**2. Placeholder scan**：无 TBD/TODO；TaintFlow 字段在 Task 5 注明"以实际定义为准并先跑确认"——这是诚实标注动态类型风险，非占位符。

**3. Type consistency**：`CodeIndex.parameter_graph` / `ParameterPropagationGraph` / `write_index_files` 返回 `tuple[Path, Path]` 在所有 task 一致；`_llm_taint_client` raise 语义在 Task 4 测试与实现一致。

**已知缺口（诚实）**：Task 3 真实 GitNexus 成功路径（FULL degradation）产出非空 pgraph 需 MCP 环境，单元测试只覆盖 fallback（None）+ 落盘闭环（手动 pgraph）。真实流转由手动冒烟验证。
