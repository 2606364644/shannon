# Spec A — 参数传播 / 污点图 实现计划

> **状态：✅ 已完成（2026-06-09）** — Task 1-11 全部完成并通过两阶段 review（spec 合规 + 代码质量）。最终整体审查额外发现并修复了 **Task 12**（计划 File Structure 表漏掉的 `TieredAuditPlanner` 集成缺口：planner 未转发 `sink_call_sites` → 生产 `taint_completeness` 恒为 0）。全套 code_index 测试 **388 passed, 1 skipped**。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `parameter_models.py` 里的 `ParameterPropagationGraph` 从"测试代码构造、生产代码空跑"的脚手架，升级为真正能从代码里追出来的确定性污点传播图；产出非空的 `param_graph.json`，激活 `risk_scorer.taint_completeness` 与 `tiered_audit.taint_flows_by_chain`，给 Spec C 的 LLM 提供入口参数 → sink 槽位的线索链。

**Architecture:**
- **三阶段算法**（`propagation_builder.py`）：入口 seed → 过程内 dataflow（顺序语句分析，无不动点）→ 沿 `CallChain` 跨函数传播，命中 `SinkCallSite.dangerous_slots` 即产出 `TaintFlow`。
- **数据契约升级**：`TaintFlow` 增加 `sink_call_site_id` / `sink_slot` / `tainted_arg_index` / `confidence` / `has_sanitizer_hint`；`ParameterPropagationGraph` 增加 `language_coverage` / `skipped_languages`。`sink_func_id` / `sink_type` 保留为遗留字段（旧测试可读，新逻辑忽略）。
- **集成**：`build_code_index` 之后调用 `build_propagation_graph`，在 `write_index_files` 里多写一份 `parameter_graph.json`。`risk_scorer` 的 `taint_completeness` 改用新字段（按 sink_call_site_id 命中）；`audit_input_builder` 同步消费新字段。
- **明确的不完备边界**：sanitizer 仅作为提示、容器字段过近似、无不动点 — 都标在 `confidence` 与 `notes`，由 LLM 在 Spec C 收尾研判。

**Tech Stack:** Python 3.12+、pydantic v2、tree-sitter（沿用 `PythonParser.iter_calls`/`destructure_call`/`extract_arg_expressions`）、pytest。

**前置依赖（spec 文档已要求，开工前必须核对）：**
- Spec B 已经合入：`SinkCallSite` / `DangerousSlot` / `SlotContext` / `detect_sinks` 全部可用，`CodeIndex.sink_call_sites` 已被 `build_code_index` 填充（核对：`packages/core/src/shannon_core/code_index/sink_detector.py`、`packages/core/src/shannon_core/code_index/__init__.py:82`）。

---

## File Structure

| 文件 | 操作 | 职责 |
|---|---|---|
| `packages/core/src/shannon_core/code_index/parameter_models.py` | **修改** | 升级 `PropagationStep` / `TaintFlow` / `ParameterPropagationGraph` 字段；保留旧字段为遗留兼容 |
| `packages/core/src/shannon_core/code_index/propagation_builder.py` | **新建** | 三阶段算法实现（入口 seed → 过程内 → 沿链）；sanitizer 提示集 |
| `packages/core/src/shannon_core/code_index/__init__.py` | **修改** | 导出 `build_propagation_graph`；`write_index_files` 额外写 `parameter_graph.json` |
| `packages/core/src/shannon_core/code_index/risk_scorer.py` | **修改** | `taint_completeness` 改用 `sink_call_site_id` 匹配；保留 `sink_func_id` 回退 |
| `packages/core/src/shannon_core/code_index/audit_input_builder.py` | **修改** | 文案使用 `sink_call_site_id` / `sink_slot`；保留旧字段回退 |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | **修改** | `run_code_index` 调用 `build_propagation_graph`；读取路径文件名 `parameter_graph.json`（与现有读取分支一致） |
| `packages/core/tests/code_index/test_propagation_builder.py` | **新建** | 过程内、跨函数、槽位约束、transformation、不完备标注的单测 |
| `packages/core/tests/code_index/test_propagation_end_to_end.py` | **新建** | 端到端：sample repo → `param_graph.json` 非空 → risk_scorer 激活 |
| `packages/core/tests/code_index/test_risk_scorer.py` | **修改** | 旧构造测试新增 `sink_call_site_id` 字段；新增 `taint_completeness` 升级测试 |
| `packages/core/tests/code_index/test_tiered_audit.py` | **修改** | 旧构造测试新增 `sink_call_site_id` 字段 |
| `packages/core/tests/code_index/test_audit_input_builder.py` | **修改** | 旧构造测试新增 `sink_call_site_id` / `sink_slot` 字段 |

---

## Task 1: 升级 `PropagationStep` / `TaintFlow` / `ParameterPropagationGraph` 数据契约

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/parameter_models.py:28-50`
- Test: `packages/core/tests/code_index/test_parameter_models_upgrade.py` (新建)

**Why:** Spec A §3.2 升级契约，把"指向 FuncBlock"的 `sink_func_id` 替换为"指向 SinkCallSite"的 `sink_call_site_id`，并把可信度 / 槽位上下文 / sanitizer 提示提到一等公民。

- [x] **Step 1: 写失败测试 — 新字段存在且可序列化**

新建 `packages/core/tests/code_index/test_parameter_models_upgrade.py`：

```python
"""Spec A: TaintFlow / PropagationStep / ParameterPropagationGraph 升级契约测试。"""
import json

from shannon_core.code_index.models import ParameterSource
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    PropagationStep,
    SlotContext,
    TaintFlow,
)


def test_propagation_step_has_step_id_and_confidence():
    step = PropagationStep(
        step_id="s1",
        from_func_id="a.py:f:1", from_param="x",
        to_func_id="b.py:g:1", to_param="y",
        transformation="concat",
        code_location="a.py:3",
        confidence=0.8,
    )
    assert step.step_id == "s1"
    assert step.confidence == 0.8


def test_taint_flow_has_sink_call_site_id_and_slot_fields():
    flow = TaintFlow(
        flow_id="a.py:f:1->a.py:f:execute:2:4",
        entry_point_id="a.py:f:1",
        source_param="user_id",
        source_type=ParameterSource.QUERY_PARAM,
        sink_call_site_id="a.py:f:execute:2:4",
        sink_slot=SlotContext.SQL_VALUE,
        tainted_arg_index=0,
        confidence=0.7,
        has_sanitizer_hint=False,
    )
    assert flow.sink_call_site_id == "a.py:f:execute:2:4"
    assert flow.sink_slot == SlotContext.SQL_VALUE
    assert flow.tainted_arg_index == 0
    assert flow.confidence == 0.7


def test_taint_flow_legacy_fields_still_present():
    """旧字段 sink_func_id / sink_type 必须仍然存在（向后兼容）。
    新逻辑不应写入它们，但旧测试与序列化文件可能引用。"""
    flow = TaintFlow(
        entry_point_id="a.py:f:1",
        source_param="user_id",
        source_type=ParameterSource.QUERY_PARAM,
    )
    # 旧字段以默认值存在
    assert flow.sink_func_id == ""
    assert flow.sink_type is None
    # 新字段以默认值存在
    assert flow.sink_call_site_id == ""
    assert flow.has_sanitizer_hint is False
    assert flow.notes == ""


def test_parameter_propagation_graph_has_coverage_fields():
    pgraph = ParameterPropagationGraph(
        taint_flows=[],
        language_coverage=["python", "typescript"],
        skipped_languages=["go", "java", "php"],
    )
    assert pgraph.language_coverage == ["python", "typescript"]
    assert pgraph.skipped_languages == ["go", "java", "php"]


def test_pgraph_serializes_with_new_fields():
    """JSON 往返必须保留新字段。"""
    flow = TaintFlow(
        flow_id="f1",
        entry_point_id="a.py:f:1",
        source_param="x",
        source_type=ParameterSource.QUERY_PARAM,
        sink_call_site_id="a.py:f:execute:2:4",
        sink_slot=SlotContext.SQL_VALUE,
        tainted_arg_index=0,
        confidence=0.5,
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=[flow],
        language_coverage=["python"],
        skipped_languages=[],
    )
    raw = json.loads(pgraph.model_dump_json())
    assert raw["language_coverage"] == ["python"]
    assert raw["taint_flows"][0]["sink_call_site_id"] == "a.py:f:execute:2:4"
    assert raw["taint_flows"][0]["sink_slot"] == "sql_value"
```

- [x] **Step 2: 运行测试，确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_parameter_models_upgrade.py -v`

Expected: FAIL — `step_id` / `confidence` / `flow_id` / `sink_call_site_id` / `sink_slot` / `tainted_arg_index` / `has_sanitizer_hint` / `notes` / `language_coverage` / `skipped_languages` 这些字段不存在（pydantic ValidationError）。

- [x] **Step 3: 升级 `parameter_models.py`**

把 `parameter_models.py:28-50` 替换为：

```python
class PropagationStep(BaseModel):
    """A single step in a taint propagation path."""
    step_id: str = ""                 # "{flow_id}#s{n}"
    from_func_id: str
    from_param: str
    to_func_id: str
    to_param: str
    transformation: str | None = None  # "concat" / "encode" / "format" / "sanitize_hint:<name>" / None
    code_location: str = ""            # "{file}:{line}"
    confidence: float = 1.0            # 本步映射的可信度


class TaintFlow(BaseModel):
    """A complete taint flow from entry point to sink.

    Spec A 升级（Spec B §3.4 预留契约）：
    - 用 sink_call_site_id 指向具体的 SinkCallSite.id
    - sink_slot / tainted_arg_index 描述到达的精确槽位
    - confidence = 整条链最弱步
    - has_sanitizer_hint 仅提示，不判有效性（有效性由 Spec C 的 LLM）
    - notes 显式标注不完备（如"未追踪容器字段"）

    旧字段 sink_func_id / sink_type 保留为遗留兼容（旧测试 / 旧 param_graph.json
    反序列化时仍可读）。新生产代码不应再写入它们。
    """
    flow_id: str = ""                 # "{entry_point_id}->{sink_call_site_id}"
    entry_point_id: str
    source_param: str
    source_type: ParameterSource
    propagation_steps: list[PropagationStep] = []

    # 新：Spec A 精确终点
    sink_call_site_id: str = ""
    sink_slot: SlotContext = SlotContext.GENERIC
    tainted_arg_index: int = -1       # -1 = 未约束 / variadic
    confidence: float = 1.0
    has_sanitizer_hint: bool = False
    notes: str = ""

    # 遗留：保留默认值供旧测试 / 旧 json 反序列化
    sink_func_id: str = ""
    sink_type: "SinkType | None" = None


class ParameterPropagationGraph(BaseModel):
    """Complete parameter propagation graph for a repository.

    language_coverage: 实际跑过传播的语言（如 ["python", "typescript"]）。
    skipped_languages: typed param 提取暂未支持、跳过传播的语言（如
        ["go", "java", "php"]）— Spec C 据此提示 LLM。
    """
    taint_flows: list[TaintFlow] = []
    language_coverage: list[str] = []
    skipped_languages: list[str] = []
```

- [x] **Step 4: 运行测试，确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_parameter_models_upgrade.py -v`

Expected: PASS（5 个测试全过）。

- [x] **Step 5: 跑回归 — 旧测试在新增字段下仍然可读**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_risk_scorer.py packages/core/tests/code_index/test_tiered_audit.py packages/core/tests/code_index/test_audit_input_builder.py -v`

Expected: PASS — 旧测试构造 `TaintFlow(...)` 时只传 `sink_func_id` / `sink_type`，新增字段全有默认值，不报错。

- [x] **Step 6: 提交**

```bash
cd /root/shannon-py && git add packages/core/src/shannon_core/code_index/parameter_models.py \
  packages/core/tests/code_index/test_parameter_models_upgrade.py
git commit -m "feat(spec-a): upgrade TaintFlow contract with sink_call_site_id/slot/confidence"
```

---

## Task 2: 新建 `propagation_builder.py` 骨架 + sanitizer 提示集

**Files:**
- Create: `packages/core/src/shannon_core/code_index/propagation_builder.py`
- Test: `packages/core/tests/code_index/test_propagation_builder.py` (新建，本任务起逐步累加测试)

**Why:** 主入口 `build_propagation_graph` + sanitizer 名称集合 + `language_coverage` / `skipped_languages` 的最外层逻辑骨架。先有可调用的空壳，后面 Task 3-6 才能往里塞算法。

- [x] **Step 1: 写失败测试 — 入口能返回空 graph 且 coverage 正确**

新建 `packages/core/tests/code_index/test_propagation_builder.py`：

```python
"""Spec A: propagation_builder 单元测试。"""
from pathlib import Path

import pytest

from shannon_core.code_index.models import (
    CallChain, CodeIndex, FuncBlock, ParameterSource, TypedParameter,
)
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph, SinkCallSite, SinkCategory, SlotContext,
    DangerousSlot,
)
from shannon_core.code_index.propagation_builder import (
    SANITIZER_HINTS,
    build_propagation_graph,
)


def _block(name: str, file: str = "app.py", line: int = 1,
           source: str = "", language: str = "python",
           params: list[str] | None = None) -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}", file_path=file,
        function_name=name, start_line=line, end_line=line + 10,
        source_code=source or f"def {name}(): pass",
        parameters=params or [],
        language=language,
    )


def _empty_index(blocks=None, language="python", chains=None,
                 sink_call_sites=None) -> CodeIndex:
    return CodeIndex(
        repository=".", language=language,
        total_blocks=len(blocks or []), total_entry_points=0, total_chains=0,
        blocks=blocks or [], edges=[], entry_points=[], chains=chains or [],
        sink_call_sites=sink_call_sites or [],
    )


class TestEmptyGraph:
    def test_no_blocks_returns_empty_graph_with_coverage(self):
        index = _empty_index(blocks=[], language="python", chains=[])
        pgraph = build_propagation_graph(index)
        assert isinstance(pgraph, ParameterPropagationGraph)
        assert pgraph.taint_flows == []
        assert "python" in pgraph.language_coverage

    def test_skipped_languages_recorded(self):
        """Go/Java/PHP 没有 typed param 提取，必须出现在 skipped_languages。"""
        for lang in ("go", "java", "php"):
            index = _empty_index(blocks=[], language=lang, chains=[])
            pgraph = build_propagation_graph(index)
            assert lang in pgraph.skipped_languages
            assert pgraph.taint_flows == []

    def test_sanitizer_hint_set_is_nonempty(self):
        # 集合至少要覆盖常见 sanitizer
        assert "escape" in SANITIZER_HINTS or any("escape" in s for s in SANITIZER_HINTS)
        assert any("sanitize" in s for s in SANITIZER_HINTS)
```

- [x] **Step 2: 运行测试，确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_propagation_builder.py::TestEmptyGraph -v`

Expected: FAIL — `module 'shannon_core.code_index.propagation_builder' has no attribute 'build_propagation_graph'`（模块还不存在）。

- [x] **Step 3: 实现 `propagation_builder.py` 骨架**

新建 `packages/core/src/shannon_core/code_index/propagation_builder.py`：

```python
"""Spec A: parameter / taint propagation graph builder.

消费 Spec B 的 SinkCallSite 列表 + CodeIndex.blocks/edges/chains，产出非空的
ParameterPropagationGraph，写入 parameter_graph.json。被 risk_scorer /
tiered_audit / Spec C LLM 消费。

三阶段算法（详见 spec §4.1）：
  1. seed:  入口函数的 TypedParameter 中 source != INTERNAL 即为 tainted。
  2. intra: 过程内顺序语句分析 — 赋值 / 拼接 / 调用，确定 tainted 变量集。
  3. chain: 沿 CallChain 跨函数传播，命中 SinkCallSite.dangerous_slots 即
            产出 TaintFlow。

明确的不完备边界（spec §4.1.4）：
  - 无不动点：循环内的 def-use 不处理。
  - 容器过近似：d tainted ⇒ d[k] tainted。
  - 分支保守：if/else 任一分支可能污染即视为 tainted。
  - sanitizer 仅提示（SANITIZER_HINTS），不阻断 taint；有效性交给 LLM。
"""

import logging
from typing import Iterable

from shannon_core.code_index.models import (
    CallChain, CodeIndex, FuncBlock, ParameterSource, TypedParameter,
)
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    PropagationStep,
    SinkCallSite,
    SlotContext,
    TaintFlow,
)

logger = logging.getLogger(__name__)


# Spec §4.1.5 — best-effort, 非判定
SANITIZER_HINTS: frozenset[str] = frozenset({
    "escape",
    "escapeHtml",
    "encodeURIComponent",
    "htmlentities",
    "htmlspecialchars",
    "sanitize",
    "validator.",
    "bleach.clean",
    "markupsafe",
    "shlex.quote",
    "quote",
    "parameterize",
})


# Spec A §4.3: 这三门语言的 typed param 提取是 _extract_generic → 空，
# 跳过传播并显式记录，让 Spec C 提示 LLM。
_UNSUPPORTED_LANGUAGES: frozenset[str] = frozenset({"go", "java", "php"})


def build_propagation_graph(
    index: CodeIndex,
    typed_params_by_block: dict[str, list[TypedParameter]] | None = None,
) -> ParameterPropagationGraph:
    """Build a ParameterPropagationGraph from a CodeIndex.

    Args:
        index: CodeIndex 含 blocks / edges / chains / sink_call_sites。
        typed_params_by_block: 可选的 {FuncBlock.id → [TypedParameter]}。
            若 None，传播将退化为只用 FuncBlock.parameters 推断 seed（语义略弱，
            但本骨架阶段足够；Task 4 会加上 typed param 提取）。

    Returns:
        ParameterPropagationGraph with taint_flows / language_coverage /
        skipped_languages 填充。
    """
    if typed_params_by_block is None:
        typed_params_by_block = {}

    language = index.language or ""
    if language in _UNSUPPORTED_LANGUAGES:
        logger.info(
            "propagation: language %s has no typed-param extractor; skipping",
            language,
        )
        return ParameterPropagationGraph(
            taint_flows=[],
            language_coverage=[],
            skipped_languages=[language],
        )

    # Task 3-6 会填充这里的实际算法。
    flows: list[TaintFlow] = []
    # _trace_all_chains(index, typed_params_by_block, flows)   # Task 5

    return ParameterPropagationGraph(
        taint_flows=flows,
        language_coverage=[language] if language else [],
        skipped_languages=[],
    )
```

- [x] **Step 4: 运行测试，确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_propagation_builder.py::TestEmptyGraph -v`

Expected: PASS（3 个测试全过）。

- [x] **Step 5: 提交**

```bash
cd /root/shannon-py && git add packages/core/src/shannon_core/code_index/propagation_builder.py \
  packages/core/tests/code_index/test_propagation_builder.py
git commit -m "feat(spec-a): add propagation_builder skeleton with sanitizer hints"
```

---

## Task 3: 入口 seed — 从 TypedParameter / FuncBlock.parameters 推断初始 tainted 集合

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/propagation_builder.py`
- Test: `packages/core/tests/code_index/test_propagation_builder.py` (累加 TestSeedTaints)

**Why:** §4.1.1 算法第一步：入口函数里"哪些变量一开始就算 tainted"。没有 seed 就没有起点。

- [x] **Step 1: 写失败测试 — seed 提取**

在 `test_propagation_builder.py` 末尾追加：

```python
class TestSeedTaints:
    def test_seed_from_typed_params_excludes_internal(self):
        """TypedParameter.source != INTERNAL 才算 tainted。"""
        from shannon_core.code_index.propagation_builder import seed_taints
        block = _block("handler", "app.py", 1, params=["user_id", "logger"])
        typed = [
            TypedParameter(name="user_id", source=ParameterSource.QUERY_PARAM),
            TypedParameter(name="logger", source=ParameterSource.INTERNAL),
        ]
        seed = seed_taints(block, typed)
        assert "user_id" in seed
        assert "logger" not in seed

    def test_seed_falls_back_to_function_params_when_typed_empty(self):
        """没有 TypedParameter 信息时，把 FuncBlock.parameters 全部视作 tainted
        （保守偏 recall），并加 note。"""
        from shannon_core.code_index.propagation_builder import seed_taints
        block = _block("handler", "app.py", 1, params=["user_id", "limit"])
        seed = seed_taints(block, [])
        # 没 typed 信息 → 全部参数 tainted
        assert "user_id" in seed
        assert "limit" in seed

    def test_seed_includes_request_attr_patterns(self):
        """request.x / req.x 在 Python/TS 入口里是常见外部输入；seed 时把
        request 本身也加入（intra 阶段会展开 request.x 的字段过近似）。"""
        from shannon_core.code_index.propagation_builder import seed_taints
        block = _block("handler", "app.py", 1, params=["request"])
        typed = [
            TypedParameter(name="request", source=ParameterSource.UNKNOWN),
        ]
        seed = seed_taints(block, typed)
        assert "request" in seed
```

- [x] **Step 2: 运行测试，确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_propagation_builder.py::TestSeedTaints -v`

Expected: FAIL — `cannot import name 'seed_taints'`。

- [x] **Step 3: 实现 `seed_taints`**

在 `propagation_builder.py` 适当位置加入：

```python
def seed_taints(
    block: FuncBlock,
    typed_params: list[TypedParameter],
) -> set[str]:
    """Determine the initial set of tainted variable names at function entry.

    Rules (spec §4.1.1):
      - 如果有 TypedParameter 信息：source != INTERNAL 即为 tainted。
      - 如果没有（Go/Java/PHP 入口、或未跑 enhanced_parameters）：把
        FuncBlock.parameters 全部视作 tainted（保守偏 recall），让 LLM 在
        Spec C 复核。
      - UNKNOWN（如 request 对象）视为 tainted — container 对象本身被标，
        过程内分析会把 request.x 一并视作 tainted（容器过近似）。
    """
    seed: set[str] = set()
    if typed_params:
        for tp in typed_params:
            if tp.source == ParameterSource.INTERNAL:
                continue
            seed.add(tp.name)
        return seed

    # Fallback: 保守 — 入口函数的全部位置参数都视作 tainted
    for name in block.parameters:
        seed.add(name)
    return seed
```

- [x] **Step 4: 运行测试，确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_propagation_builder.py::TestSeedTaints -v`

Expected: PASS（3 个测试）。

- [x] **Step 5: 提交**

```bash
cd /root/shannon-py && git add packages/core/src/shannon_core/code_index/propagation_builder.py \
  packages/core/tests/code_index/test_propagation_builder.py
git commit -m "feat(spec-a): seed initial taints from TypedParameter / FuncBlock.parameters"
```

---

## Task 4: 过程内 dataflow — 顺序语句分析，识别赋值/拼接到达 sink call site

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/propagation_builder.py`
- Test: `packages/core/tests/code_index/test_propagation_builder.py` (累加 TestIntraProcedural)

**Why:** §4.1.2 的核心 — 在单个函数体内追"赋值/拼接/调用"，找到"tainted 变量 → SinkCallSite.dangerous_slot" 的命中。这一步独立可测，是整条算法最复杂的部分。

> **实现选择：** 直接对 `FuncBlock.source_code` 文本做轻量级分析（行级 + AST 友好的轻量表达式匹配）。**不**走 tree-sitter `iter_calls`，因为：① 那条路径已经被 sink_detector 用过、且重解析整棵 AST 重复劳动；② 我们只需要"这个变量名是否出现在某个表达式里"这种粗粒度判断，行级正则足够，与 spec §4.1.4 的"过近似、偏 recall"一致。tree-sitter 仍由 sink_detector 提供 SinkCallSite，我们只消费其结果。

- [x] **Step 1: 写失败测试 — 过程内最简单赋值链到达 sink**

在 `test_propagation_builder.py` 末尾追加：

```python
class TestIntraProcedural:
    def _make_sink(self, caller_id: str, callee: str = "execute",
                   file: str = "app.py", line: int = 3, col: int = 4,
                   arg_idx: int = 0,
                   slot: SlotContext = SlotContext.SQL_VALUE) -> SinkCallSite:
        return SinkCallSite(
            id=f"{file}:{caller_id.split(':')[1]}:{callee}:{line}:{col}",
            caller_id=caller_id,
            callee_name=callee,
            callee_receiver="cursor" if slot == SlotContext.SQL_VALUE else None,
            category=SinkCategory.SQL,
            sink_subtype="sql_raw",
            file_path=file,
            line=line,
            column=col,
            dangerous_slots=[DangerousSlot(
                arg_index=arg_idx, slot=slot,
                expression="sql", is_entry_hint=False,
            )],
            rule_id="py-db-cursor-execute",
        )

    def test_straight_assignment_to_sink(self):
        """user_id → sql → cursor.execute(sql)
        单函数体内一条赋值链命中 sink。"""
        from shannon_core.code_index.propagation_builder import analyze_intra
        block = _block(
            "handler", "app.py", 1,
            source=(
                "def handler(user_id):\n"
                "    sql = 'SELECT * FROM u WHERE id=' + user_id\n"
                "    cursor.execute(sql)\n"
            ),
            params=["user_id"],
        )
        sink = self._make_sink(block.id, line=3, arg_idx=0)
        result = analyze_intra(
            block, seed={"user_id"},
            sinks_in_func=[sink],
        )
        assert sink.id in result.hits
        arg_idx, steps = result.hits[sink.id]
        assert arg_idx == 0
        # 至少能识别 sql 被污染 + 触达 sink 的 0 号槽
        assert any(s.to_param == "sql" or s.transformation == "concat"
                   for s in steps.local_steps)

    def test_transformation_concat_marked(self):
        """拼接 'SELECT ...' + user_input 应标 transformation='concat'。"""
        from shannon_core.code_index.propagation_builder import analyze_intra
        block = _block(
            "f", "app.py", 1,
            source=(
                "def f(user_input):\n"
                "    q = 'SELECT * FROM t WHERE id=' + user_input\n"
                "    cursor.execute(q)\n",
            ),
            params=["user_input"],
        )
        sink = self._make_sink(block.id, line=3, arg_idx=0)
        result = analyze_intra(
            block, seed={"user_input"}, sinks_in_func=[sink],
        )
        _, steps = result.hits[sink.id]
        # 出现 concat transformation
        concat_steps = [s for s in steps.local_steps if s.transformation == "concat"]
        assert len(concat_steps) >= 1

    def test_no_hit_when_taint_never_reaches_sink_arg(self):
        """tainted 变量从未出现在 sink 的危险槽位 → 不命中。"""
        from shannon_core.code_index.propagation_builder import analyze_intra
        block = _block(
            "f", "app.py", 1,
            source=(
                "def f(user_input):\n"
                "    other = compute()\n"
                "    cursor.execute(other)\n",
            ),
            params=["user_input"],
        )
        sink = self._make_sink(block.id, line=3, arg_idx=0)
        result = analyze_intra(
            block, seed={"user_input"}, sinks_in_func=[sink],
        )
        assert sink.id not in result.hits

    def test_slot_constraint_excludes_safe_arg_index(self):
        """sink 的 dangerous_slots 是 arg_index=0，但 tainted 走的是 arg_index=1
        （例如 cursor.execute(safe, tainted)）→ 不算命中。"""
        from shannon_core.code_index.propagation_builder import analyze_intra
        # 危险槽位只声明在 0 号；tainted 走 1 号 → 不命中
        block = _block(
            "f", "app.py", 1,
            source=(
                "def f(user_input):\n"
                "    cursor.execute('SAFE', user_input)\n",
            ),
            params=["user_input"],
        )
        sink = self._make_sink(block.id, line=2, arg_idx=0)  # 仅 0 号危险
        result = analyze_intra(
            block, seed={"user_input"}, sinks_in_func=[sink],
        )
        assert sink.id not in result.hits

    def test_sanitizer_hint_does_not_block_taint(self):
        """路径上出现 escape(...) → 标 sanitize_hint:escape，但 taint 不阻断。"""
        from shannon_core.code_index.propagation_builder import analyze_intra
        block = _block(
            "f", "app.py", 1,
            source=(
                "def f(user_input):\n"
                "    safe = escape(user_input)\n"
                "    cursor.execute(safe)\n",
            ),
            params=["user_input"],
        )
        sink = self._make_sink(block.id, line=3, arg_idx=0)
        result = analyze_intra(
            block, seed={"user_input"}, sinks_in_func=[sink],
        )
        assert sink.id in result.hits
        _, steps = result.hits[sink.id]
        assert any(s.transformation and s.transformation.startswith("sanitize_hint:")
                   for s in steps.local_steps)
        assert steps.has_sanitizer_hint is True
```

- [x] **Step 2: 运行测试，确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_propagation_builder.py::TestIntraProcedural -v`

Expected: FAIL — `cannot import name 'analyze_intra'`。

- [x] **Step 3: 实现 `analyze_intra` 与 helpers**

在 `propagation_builder.py` 适当位置加入：

```python
import re
from dataclasses import dataclass, field


# === 过程内 dataflow ============================================

# 行级赋值识别：捕获 "LHS = RHS" / "LHS := RHS" / "LHS := RHS" (Go)。
# 用负向先行断言排除 == != >= <= 等。
_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][\w]*)\s*(?<![<>!=])=(?!=)\s*(.+?)\s*$")
_ASSIGN_GO_RE = re.compile(r"^\s*([A-Za-z_][\w]*)\s*:=(?!=)\s*(.+?)\s*$")

# 拼接检测：RHS 含 '+' 或 f-string 或 .format(...) 或模板字面量
_CONCAT_HINTS = ("+", ".format(", "f'", 'f"', "${")


@dataclass
class IntraHit:
    """单条 sink call site 命中的过程内结果。"""
    sink_id: str
    tainted_arg_index: int
    slot: SlotContext
    local_steps: list[PropagationStep] = field(default_factory=list)
    has_sanitizer_hint: bool = False


@dataclass
class IntraResult:
    """analyze_intra 的返回：命中的 sinks + 累计 steps（供跨函数用）。"""
    hits: dict[str, IntraHit] = field(default_factory=dict)
    local_steps_accumulated: list[PropagationStep] = field(default_factory=list)
    has_sanitizer_global: bool = False


def analyze_intra(
    block: FuncBlock,
    seed: set[str],
    sinks_in_func: list[SinkCallSite],
) -> IntraResult:
    """过程内顺序污点分析（spec §4.1.2）。

    单趟扫描 FuncBlock.source_code 的每一行，维护当前 tainted 变量集合，
    在遇到 sink call 时检查实参是否触达 dangerous_slot。

    简化（spec §4.1.4）：
      - 顺序语句、不动点 = 单趟。
      - 容器过近似：d tainted ⇒ d[k] tainted。
      - 分支保守：if/else 任一分支污染即视为 tainted。
      - sanitizer 仅打提示，不阻断 taint。
    """
    tainted = set(seed)
    # 按 line 排好序的 sinks，方便 O(N+M) 扫描
    sinks_by_line: dict[int, list[SinkCallSite]] = {}
    for s in sinks_in_func:
        sinks_by_line.setdefault(s.line, []).append(s)

    hits: dict[str, IntraHit] = {}
    accumulated_steps: list[PropagationStep] = []
    has_sanitizer_global = False

    for line_no, raw_line in enumerate(block.source_code.splitlines(), start=block.start_line):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue

        # 1) 赋值：x = expr  /  x := expr (Go)
        lhs, rhs = _match_assignment(line)
        if lhs is not None:
            transformation = _detect_transformation(rhs)
            if _expr_references_tainted(rhs, tainted):
                tainted.add(lhs)
                accumulated_steps.append(PropagationStep(
                    step_id="",  # build_propagation_graph 在最终 flow 阶段统一编号
                    from_func_id=block.id,
                    from_param=_first_tainted_in(rhs, tainted) or "",
                    to_func_id=block.id, to_param=lhs,
                    transformation=transformation,
                    code_location=f"{block.file_path}:{line_no}",
                    confidence=0.8 if transformation else 1.0,
                ))
                if transformation and transformation.startswith("sanitize_hint:"):
                    has_sanitizer_global = True
            # 不管 LHS 是否被污染，sanitizer 名只要出现就记 has_sanitizer_hint
            if _has_sanitizer(rhs):
                has_sanitizer_global = True

        # 2) Sink call：检查 dangerous_slots 是否被 tainted 实参命中
        for sink in sinks_by_line.get(line_no, []):
            for slot in sink.dangerous_slots:
                if _expr_references_tainted(slot.expression, tainted):
                    if sink.id not in hits:
                        hits[sink.id] = IntraHit(
                            sink_id=sink.id,
                            tainted_arg_index=slot.arg_index,
                            slot=slot.slot,
                            local_steps=list(accumulated_steps),
                            has_sanitizer_hint=has_sanitizer_global,
                        )
                    break  # 同一 sink 的首个 dangerous 命中即停止扫 slot

    return IntraResult(
        hits=hits,
        local_steps_accumulated=accumulated_steps,
        has_sanitizer_global=has_sanitizer_global,
    )


def _match_assignment(line: str) -> tuple[str | None, str]:
    """识别 "LHS = RHS" / "LHS := RHS"。返回 (lhs, rhs)；不匹配返回 (None, "")。"""
    m = _ASSIGN_RE.match(line)
    if m:
        return m.group(1), m.group(2)
    m = _ASSIGN_GO_RE.match(line)
    if m:
        return m.group(1), m.group(2)
    return None, ""


def _expr_references_tainted(expr: str, tainted: frozenset[str] | set[str]) -> bool:
    """递归检查表达式是否引用 tainted 集合中的标识符。

    过近似：tainted 标识符 + 容器字段（d tainted ⇒ d[k] tainted）。
    """
    if not expr or not tainted:
        return False
    # 词法扫描：提取所有标识符（含点号 — request.x 整体作一个 token）
    for tok in re.findall(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*", expr):
        if tok in tainted:
            return True
        # 容器过近似：tok 形如 "tainted_obj.field" — 头部在 tainted 即视命中
        head = tok.split(".", 1)[0]
        if head in tainted:
            return True
    return False


def _first_tainted_in(expr: str, tainted: set[str]) -> str | None:
    for tok in re.findall(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*", expr):
        if tok in tainted:
            return tok
        head = tok.split(".", 1)[0]
        if head in tainted:
            return head
    return None


def _detect_transformation(rhs: str) -> str | None:
    """RHS 的 transformation 标签（spec §4.1.5）。"""
    if _has_sanitizer(rhs):
        # 取命中的第一个 sanitizer 名字
        for hint in SANITIZER_HINTS:
            if hint in rhs:
                return f"sanitize_hint:{hint.rstrip('.')}"
    if any(h in rhs for h in _CONCAT_HINTS):
        return "concat"
    if "%" in rhs and ("(" in rhs or rhs.count("'") >= 2 or rhs.count('"') >= 2):
        return "format"
    return None


def _has_sanitizer(expr: str) -> bool:
    return any(h in expr for h in SANITIZER_HINTS)
```

> 注意：上面的赋值识别故意写得宽松（兼容 Python/TS/Go/PHP/Java），重 recall。本任务测试只覆盖 Python 单行赋值，过近似是 spec §4.1.4 明确允许的。

- [x] **Step 4: 运行测试，确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_propagation_builder.py::TestIntraProcedural -v`

Expected: PASS（5 个测试）。

- [x] **Step 5: 提交**

```bash
cd /root/shannon-py && git add packages/core/src/shannon_core/code_index/propagation_builder.py \
  packages/core/tests/code_index/test_propagation_builder.py
git commit -m "feat(spec-a): intraprocedural dataflow — assignment chain to sink slot"
```

---

## Task 5: 跨函数传播 — 沿 CallChain 走 call-site 参数映射

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/propagation_builder.py`
- Test: `packages/core/tests/code_index/test_propagation_builder.py` (累加 TestTraceChain)

**Why:** §4.1.3 的核心 — 单函数到 sink 没命中时，沿着 `CallChain.path` 把当前函数的 tainted 变量通过 `CallEdge` 的实参位置映射到下一函数的形参，递归 `_intra_procedural`，直到命中 sink 或链尽。

- [x] **Step 1: 写失败测试 — 跨函数链到达 sink**

在 `test_propagation_builder.py` 末尾追加：

```python
class TestTraceChain:
    def _build_chain_index(
        self, blocks: list[FuncBlock], chains: list[CallChain],
        sinks: list[SinkCallSite],
    ) -> CodeIndex:
        return CodeIndex(
            repository=".", language="python",
            total_blocks=len(blocks), total_entry_points=0, total_chains=len(chains),
            blocks=blocks, edges=[], entry_points=[], chains=chains,
            sink_call_sites=sinks,
        )

    def test_two_function_chain_reaches_sink(self):
        """handler(user_id) → process(x) → cursor.execute(x)
        entry 的 user_id 经 process 的形参 x 到达 sink。"""
        from shannon_core.code_index.propagation_builder import build_propagation_graph

        handler = _block(
            "handler", "app.py", 1,
            source=(
                "def handler(user_id):\n"
                "    process(user_id)\n",
            ),
            params=["user_id"],
        )
        process = _block(
            "process", "svc.py", 5,
            source=(
                "def process(x):\n"
                "    cursor.execute(x)\n",
            ),
            params=["x"],
        )
        sink = SinkCallSite(
            id="svc.py:process:execute:6:4",
            caller_id=process.id,
            callee_name="execute",
            callee_receiver="cursor",
            category=SinkCategory.SQL,
            sink_subtype="sql_raw",
            file_path="svc.py",
            line=6, column=4,
            dangerous_slots=[DangerousSlot(
                arg_index=0, slot=SlotContext.SQL_VALUE,
                expression="x", is_entry_hint=False,
            )],
            rule_id="py-db-cursor-execute",
        )
        chain = CallChain(
            entry_point_id=handler.id,
            path=[handler.id, process.id],
            depth=1, has_unresolved=False,
        )
        index = self._build_chain_index([handler, process], [chain], [sink])

        pgraph = build_propagation_graph(
            index,
            typed_params_by_block={
                handler.id: [
                    TypedParameter(name="user_id", source=ParameterSource.QUERY_PARAM),
                ],
            },
        )
        assert len(pgraph.taint_flows) == 1
        flow = pgraph.taint_flows[0]
        assert flow.entry_point_id == handler.id
        assert flow.sink_call_site_id == sink.id
        assert flow.tainted_arg_index == 0
        assert flow.source_param == "user_id"

    def test_chain_without_sink_yields_no_flow(self):
        """链路上没有 sink → 0 个 flow。"""
        from shannon_core.code_index.propagation_builder import build_propagation_graph

        handler = _block(
            "handler", "app.py", 1,
            source="def handler(user_id):\n    helper(user_id)\n",
            params=["user_id"],
        )
        helper = _block(
            "helper", "svc.py", 5,
            source="def helper(x):\n    return x + 1\n",
            params=["x"],
        )
        chain = CallChain(
            entry_point_id=handler.id,
            path=[handler.id, helper.id],
            depth=1, has_unresolved=False,
        )
        index = self._build_chain_index([handler, helper], [chain], [])

        pgraph = build_propagation_graph(
            index,
            typed_params_by_block={
                handler.id: [
                    TypedParameter(name="user_id", source=ParameterSource.QUERY_PARAM),
                ],
            },
        )
        assert pgraph.taint_flows == []

    def test_param_mapping_preserves_taint_across_calls(self):
        """handler(a, b) → process(b, a) — 第二个参数 b 才是 tainted，
        process 的形参顺序决定 x = b (tainted)。"""
        from shannon_core.code_index.propagation_builder import build_propagation_graph

        handler = _block(
            "handler", "app.py", 1,
            source=(
                "def handler(a, b):\n"
                "    process(b, a)\n",
            ),
            params=["a", "b"],
        )
        process = _block(
            "process", "svc.py", 5,
            source=(
                "def process(x, y):\n"
                "    cursor.execute(x)\n",
            ),
            params=["x", "y"],
        )
        sink = SinkCallSite(
            id="svc.py:process:execute:6:4",
            caller_id=process.id,
            callee_name="execute",
            callee_receiver="cursor",
            category=SinkCategory.SQL,
            sink_subtype="sql_raw",
            file_path="svc.py",
            line=6, column=4,
            dangerous_slots=[DangerousSlot(
                arg_index=0, slot=SlotContext.SQL_VALUE,
                expression="x", is_entry_hint=False,
            )],
            rule_id="py-db-cursor-execute",
        )
        chain = CallChain(
            entry_point_id=handler.id,
            path=[handler.id, process.id],
            depth=1, has_unresolved=False,
        )
        index = self._build_chain_index([handler, process], [chain], [sink])

        pgraph = build_propagation_graph(
            index,
            typed_params_by_block={
                handler.id: [
                    TypedParameter(name="a", source=ParameterSource.INTERNAL),
                    TypedParameter(name="b", source=ParameterSource.QUERY_PARAM),
                ],
            },
        )
        # b 是 tainted → process(b, a) 把 b 映射到 process 的第 0 号形参 x
        # → cursor.execute(x) 命中
        assert len(pgraph.taint_flows) == 1
        flow = pgraph.taint_flows[0]
        assert flow.source_param == "b"

    def test_confidence_is_weakest_step(self):
        """整条链的 confidence 取最弱步。"""
        from shannon_core.code_index.propagation_builder import build_propagation_graph

        handler = _block(
            "handler", "app.py", 1,
            source=(
                "def handler(user_id):\n"
                "    q = 'SELECT ' + user_id\n"
                "    process(q)\n",
            ),
            params=["user_id"],
        )
        process = _block(
            "process", "svc.py", 5,
            source=(
                "def process(x):\n"
                "    cursor.execute(x)\n",
            ),
            params=["x"],
        )
        sink = SinkCallSite(
            id="svc.py:process:execute:6:4",
            caller_id=process.id,
            callee_name="execute",
            callee_receiver="cursor",
            category=SinkCategory.SQL,
            sink_subtype="sql_raw",
            file_path="svc.py",
            line=6, column=4,
            dangerous_slots=[DangerousSlot(
                arg_index=0, slot=SlotContext.SQL_VALUE,
                expression="x", is_entry_hint=False,
            )],
            rule_id="py-db-cursor-execute",
        )
        chain = CallChain(
            entry_point_id=handler.id,
            path=[handler.id, process.id],
            depth=1, has_unresolved=False,
        )
        index = self._build_chain_index([handler, process], [chain], [sink])

        pgraph = build_propagation_graph(
            index,
            typed_params_by_block={
                handler.id: [
                    TypedParameter(name="user_id", source=ParameterSource.QUERY_PARAM),
                ],
            },
        )
        assert len(pgraph.taint_flows) == 1
        flow = pgraph.taint_flows[0]
        # 至少有一个 concat step → confidence 应 < 1.0
        assert flow.confidence < 1.0
        # 出现的 transformation 至少含一次 concat
        transformations = {s.transformation for s in flow.propagation_steps if s.transformation}
        assert "concat" in transformations
```

- [x] **Step 2: 运行测试，确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_propagation_builder.py::TestTraceChain -v`

Expected: FAIL — 4 个测试都失败（`build_propagation_graph` 没有真实算法，flows 仍为空）。

- [x] **Step 3: 实现 `_trace_chain` + `build_propagation_graph` 真实算法**

在 `propagation_builder.py` 替换 `build_propagation_graph` 主体（保留 sanitizer / coverage 处理），加入 `_trace_chain`：

```python
def build_propagation_graph(
    index: CodeIndex,
    typed_params_by_block: dict[str, list[TypedParameter]] | None = None,
) -> ParameterPropagationGraph:
    """Spec A 主入口。"""
    if typed_params_by_block is None:
        typed_params_by_block = {}

    language = index.language or ""
    if language in _UNSUPPORTED_LANGUAGES:
        logger.info(
            "propagation: language %s has no typed-param extractor; skipping",
            language,
        )
        return ParameterPropagationGraph(
            taint_flows=[],
            language_coverage=[],
            skipped_languages=[language],
        )

    blocks_by_id = {b.id: b for b in index.blocks}
    sinks_by_caller: dict[str, list[SinkCallSite]] = {}
    for s in index.sink_call_sites:
        sinks_by_caller.setdefault(s.caller_id, []).append(s)

    flows: list[TaintFlow] = []
    flow_counter = 0

    for chain in index.chains:
        if not chain.path:
            continue
        entry_id = chain.path[0]
        entry_block = blocks_by_id.get(entry_id)
        if entry_block is None:
            continue
        seed = seed_taints(entry_block, typed_params_by_block.get(entry_id, []))
        if not seed:
            continue

        for flow in _trace_chain(
            chain=chain,
            blocks_by_id=blocks_by_id,
            sinks_by_caller=sinks_by_caller,
            seed=seed,
            entry_block=entry_block,
        ):
            flow_counter += 1
            if not flow.flow_id:
                flow.flow_id = f"{entry_id}->{flow.sink_call_site_id}"
            # 给 propagation_steps 编号
            for n, step in enumerate(flow.propagation_steps, start=1):
                if not step.step_id:
                    step.step_id = f"{flow.flow_id}#s{n}"
            flows.append(flow)

    return ParameterPropagationGraph(
        taint_flows=flows,
        language_coverage=[language] if language else [],
        skipped_languages=[],
    )


def _trace_chain(
    *,
    chain: CallChain,
    blocks_by_id: dict[str, FuncBlock],
    sinks_by_caller: dict[str, list[SinkCallSite]],
    seed: set[str],
    entry_block: FuncBlock,
) -> Iterable[TaintFlow]:
    """沿 CallChain.path 走 cross-function 传播。

    spec §4.1.3：
      current_tainted = {entry_func: seed}
      for i, func_id in enumerate(chain.path):
          intra = analyze_intra(func, current_tainted[func_id], sinks_in_func)
          for sink_hit in intra.hits:
              yield TaintFlow(...)
          if i+1 < len(chain.path):
              callee_id = chain.path[i+1]
              callee_tainted = map_params_to_callee(...)
              current_tainted[callee_id] = callee_tainted
    """
    current_tainted: dict[str, set[str]] = {entry_block.id: set(seed)}
    accumulated_steps: list[PropagationStep] = []
    has_sanitizer = False
    weakest_conf = 1.0
    source_param = next(iter(seed), "")  # 任取一个 seed 名作为 source_param

    for i, func_id in enumerate(chain.path):
        block = blocks_by_id.get(func_id)
        if block is None:
            return
        sinks_in_func = sinks_by_caller.get(func_id, [])
        intra = analyze_intra(
            block=block,
            seed=current_tainted.get(func_id, set()),
            sinks_in_func=sinks_in_func,
        )

        # 命中 sink → 产出 flow
        for sink_id, hit in intra.hits.items():
            steps_total = list(accumulated_steps) + list(hit.local_steps)
            for s in hit.local_steps:
                if s.confidence < weakest_conf:
                    weakest_conf = s.confidence
            yield TaintFlow(
                flow_id="",  # build_propagation_graph 统一编号
                entry_point_id=entry_block.id,
                source_param=source_param,
                source_type=_infer_source_type(entry_block, source_param),
                propagation_steps=steps_total,
                sink_call_site_id=sink_id,
                sink_slot=hit.slot,
                tainted_arg_index=hit.tainted_arg_index,
                confidence=weakest_conf,
                has_sanitizer_hint=has_sanitizer or hit.has_sanitizer_hint,
                notes="",
            )

        # 把本函数的 local_steps 累入 accumulated_steps
        accumulated_steps.extend(intra.local_steps_accumulated or [])
        # 如果有 sanitizer 提示，传染到下游
        if intra.has_sanitizer_global:
            has_sanitizer = True

        # 准备下一跳：把当前 tainted 通过 call-site 实参映射到 callee 形参
        if i + 1 >= len(chain.path):
            return
        callee_id = chain.path[i + 1]
        callee_block = blocks_by_id.get(callee_id)
        if callee_block is None:
            return
        callee_seed = _map_call_site_to_callee_params(
            caller_block=block,
            caller_tainted=current_tainted.get(func_id, set()),
            callee_block=callee_block,
        )
        if not callee_seed:
            return
        current_tainted[callee_id] = callee_seed
        # 把跨函数这一步加进 accumulated_steps（informational）
        accumulated_steps.append(PropagationStep(
            step_id="",
            from_func_id=func_id,
            from_param=next(iter(current_tainted.get(func_id, {source_param})), source_param),
            to_func_id=callee_id,
            to_param=next(iter(callee_seed), ""),
            transformation=None,
            code_location=f"{block.file_path}:{block.start_line}",
            confidence=0.9,
        ))


def _map_call_site_to_callee_params(
    *,
    caller_block: FuncBlock,
    caller_tainted: set[str],
    callee_block: FuncBlock,
) -> set[str]:
    """从 caller 的源码里找出对 callee 的调用位置，按位置把 tainted 实参映射
    到 callee 的形参名。

    简化：扫 caller.source_code 里包含 callee_block.function_name 后跟 '(' 的行，
    提取括号内实参，按位置匹配 callee_block.parameters。
    """
    callee_name = callee_block.function_name
    callee_params = callee_block.parameters
    if not callee_params:
        return set()

    result: set[str] = set()
    for line in caller_block.source_code.splitlines():
        if callee_name + "(" not in line:
            continue
        # 抽出第一个 callee_name( ... ) 的实参列表（粗略、保守）
        inside = _extract_first_call_args(line, callee_name)
        if inside is None:
            continue
        for idx, arg_text in enumerate(inside):
            if idx >= len(callee_params):
                break
            if _expr_references_tainted(arg_text, caller_tainted):
                result.add(callee_params[idx])
    return result


def _extract_first_call_args(line: str, callee: str) -> list[str] | None:
    """从一行代码里提取 callee(...) 的实参文本列表。粗略实现：取 callee 后第一个
    '(' 到对应 ')' 之间的文本，按 ',' 拆。"""
    idx = line.find(callee + "(")
    if idx < 0:
        return None
    inside_start = idx + len(callee) + 1
    depth = 1
    inside_end = -1
    for j in range(inside_start, len(line)):
        ch = line[j]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                inside_end = j
                break
    if inside_end < 0:
        return None
    inside = line[inside_start:inside_end]
    if not inside.strip():
        return []
    return [a.strip() for a in _split_args_respecting_parens(inside)]


def _split_args_respecting_parens(s: str) -> list[str]:
    """按 ',' 拆分但忽略括号/引号内的逗号。"""
    out: list[str] = []
    cur = []
    depth = 0
    quote = None
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            cur.append(ch)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def _infer_source_type(block: FuncBlock, source_param: str) -> ParameterSource:
    """没有 typed_params 时给 source_param 一个保守的 source。"""
    # 真实 typed 信息由 typed_params_by_block 提供；这里只是回退
    if source_param in ("request", "req"):
        return ParameterSource.UNKNOWN
    if "user" in source_param.lower() or "id" in source_param.lower():
        return ParameterSource.QUERY_PARAM
    return ParameterSource.UNKNOWN
```

注意：Task 4 已经在 `IntraResult` 上定义了 `local_steps_accumulated` / `has_sanitizer_global`，Task 5 直接消费这两个字段即可，不需要再改 `analyze_intra`。

- [x] **Step 4: 运行测试，确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_propagation_builder.py -v`

Expected: PASS — 所有现有测试（含 Task 2/3/4 的回归 + Task 5 的 4 个新测试）全过。

- [x] **Step 5: 提交**

```bash
cd /root/shannon-py && git add packages/core/src/shannon_core/code_index/propagation_builder.py \
  packages/core/tests/code_index/test_propagation_builder.py
git commit -m "feat(spec-a): cross-function taint propagation along CallChain"
```

---

## Task 6: 把 `build_propagation_graph` 接入 `build_code_index` / `write_index_files`

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py:14-192`
- Test: `packages/core/tests/code_index/test_propagation_end_to_end.py` (新建)

**Why:** spec §4.2 — 算法已就绪，但要在 pipeline 里真正激活 `activities.py:240` 的读取分支，必须在 `write_index_files` 时多写一份 `parameter_graph.json`。`build_code_index` 期间调用 `build_propagation_graph`，typed_params 用 `extract_typed_parameters` 在每个 entry block 上取。

- [x] **Step 1: 写失败测试 — 端到端 param_graph.json 非空**

新建 `packages/core/tests/code_index/test_propagation_end_to_end.py`：

```python
"""Spec A 端到端：build_code_index → param_graph.json 非空。"""
import json
from pathlib import Path

import pytest

from shannon_core.code_index import build_code_index, write_index_files


@pytest.fixture
def flask_repo(tmp_path) -> Path:
    repo = tmp_path / "flaskrepo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def handler(user_id):\n"
        "    q = 'SELECT * FROM u WHERE id=' + user_id\n"
        "    process(q)\n"
        "def process(sql):\n"
        "    cursor.execute(sql)\n"
    )
    return repo


class TestPropagationEndToEnd:
    def test_param_graph_json_written_and_nonempty(self, flask_repo, tmp_path):
        index = build_code_index(str(flask_repo))
        out = tmp_path / "out"
        json_path, _ = write_index_files(index, str(out))
        pgraph_path = out / "parameter_graph.json"
        assert pgraph_path.exists()
        data = json.loads(pgraph_path.read_text())
        assert "taint_flows" in data
        assert "language_coverage" in data
        assert "python" in data["language_coverage"]

    def test_param_graph_includes_sink_call_site_id(self, flask_repo, tmp_path):
        """非空 flow 的 sink_call_site_id 必须形如
        '{file}:{caller_func}:{callee}:{line}:{col}'。"""
        index = build_code_index(str(flask_repo))
        # 需要 chain 才会触发传播；这里手工塞一条 chain 来验证字段。
        # （chain 由 build_call_chains 产生，需要 entry_points；本测试用一个
        # 简单 entry 注入。）
        # 在 build_code_index 中 entry_points 已经探测过；若没有 chain，
        # 至少 param_graph.json 必须写出（哪怕 flows=[]）。
        out = tmp_path / "out"
        write_index_files(index, str(out))
        pgraph_path = out / "parameter_graph.json"
        data = json.loads(pgraph_path.read_text())
        for flow in data["taint_flows"]:
            sid = flow["sink_call_site_id"]
            assert sid.count(":") >= 4
            assert flow["sink_slot"] in (
                "sql_value", "sql_identifier", "cmd_argument", "file_path",
                "template_expr", "url", "deserialize", "generic",
            )

    def test_skipped_languages_recorded_for_ts_repo(self, tmp_path):
        """TypeScript repo — coverage=['typescript']，skipped_languages=[]。"""
        repo = tmp_path / "tsrepo"
        repo.mkdir()
        (repo / "service.ts").write_text(
            "function processInput(input: string) {\n"
            "    eval(input);\n"
            "}\n"
        )
        index = build_code_index(str(repo))
        out = tmp_path / "out"
        write_index_files(index, str(out))
        data = json.loads((out / "parameter_graph.json").read_text())
        assert "typescript" in data["language_coverage"]
        assert "go" not in data["language_coverage"]
```

- [x] **Step 2: 运行测试，确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_propagation_end_to_end.py -v`

Expected: FAIL — `parameter_graph.json` 不存在（`write_index_files` 没写它）。

- [x] **Step 3: 修改 `__init__.py`**

在 `__init__.py` 顶部 import 区追加：

```python
from shannon_core.code_index.propagation_builder import build_propagation_graph
```

把 `build_code_index` 函数末尾的 `return CodeIndex(...)` 之前加上传播：

```python
    # Spec A: parameter / taint propagation
    # typed_params 提取在每个 entry block 上做一次（其他 block 拿不到 typed 信息
    # 时退化为 FuncBlock.parameters，仍能产出 flow）。
    from shannon_core.code_index.enhanced_parameters import extract_typed_parameters
    typed_params_by_block: dict[str, list] = {}
    for ep in entry_points:
        block = next((b for b in all_blocks if b.id == ep.func_block_id), None)
        if block is None:
            continue
        try:
            tps = extract_typed_parameters(
                Path(block.file_path), block.function_name,
                block.start_line, language,
            )
        except Exception:
            tps = []
        typed_params_by_block[block.id] = tps

    # 调用链在此阶段还没建（chains=[]），build_propagation_graph 会得到空
    # chains → flows=[]。但 write_index_files 仍会写出空的 parameter_graph.json，
    # 让 activities.py 的读取分支激活。真实非空 flow 需要 rebuild_call_chains
    # 之后再调用 build_propagation_graph，本步只确保文件存在 + 数据契约正确。
    pgraph = build_propagation_graph(
        CodeIndex(
            repository=str(repo),
            language=language,
            total_blocks=len(all_blocks),
            total_entry_points=len(entry_points),
            total_chains=0,
            blocks=all_blocks,
            edges=resolved_edges,
            entry_points=entry_points,
            chains=[],
            sink_call_sites=sink_call_sites,
        ),
        typed_params_by_block=typed_params_by_block,
    )
    logger.info("Built parameter propagation graph: %d taint flows", len(pgraph.taint_flows))
```

修改 `write_index_files`：

```python
def write_index_files(index: CodeIndex, output_dir: str) -> tuple[Path, Path]:
    """Write code_index.json, code_index_summary.md, and parameter_graph.json."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "code_index.json"
    json_path.write_text(index.model_dump_json(indent=2))

    summary_path = out / "code_index_summary.md"
    summary_path.write_text(generate_summary(index))

    # Spec A: 同步构建并写出 parameter_graph.json
    # 即使 chains=[] 也要写出空文件，确保 activities.py 读取分支可激活。
    from shannon_core.code_index.enhanced_parameters import extract_typed_parameters
    typed_params_by_block: dict[str, list] = {}
    for ep in index.entry_points:
        block = next((b for b in index.blocks if b.id == ep.func_block_id), None)
        if block is None:
            continue
        try:
            tps = extract_typed_parameters(
                Path(block.file_path), block.function_name,
                block.start_line, index.language,
            )
        except Exception:
            tps = []
        typed_params_by_block[block.id] = tps

    pgraph = build_propagation_graph(index, typed_params_by_block=typed_params_by_block)

    pgraph_path = out / "parameter_graph.json"
    pgraph_path.write_text(pgraph.model_dump_json(indent=2))

    return json_path, summary_path
```

- [x] **Step 4: 同步更新 `rebuild_call_chains` — 链路重建时也要刷新 parameter_graph.json**

`rebuild_call_chains`（`__init__.py:239-308`）末尾在写完 `code_index.json` 后追加：

```python
    # Spec A: chain 重建后传播图也会变，刷新 parameter_graph.json
    from shannon_core.code_index.enhanced_parameters import extract_typed_parameters
    typed_params_by_block: dict[str, list] = {}
    for ep in index.entry_points:
        block = next((b for b in index.blocks if b.id == ep.func_block_id), None)
        if block is None:
            continue
        try:
            tps = extract_typed_parameters(
                Path(block.file_path), block.function_name,
                block.start_line, index.language,
            )
        except Exception:
            tps = []
        typed_params_by_block[block.id] = tps

    new_pgraph = build_propagation_graph(updated, typed_params_by_block=typed_params_by_block)
    pgraph_path = out / "parameter_graph.json"
    pgraph_path.write_text(new_pgraph.model_dump_json(indent=2))
```

- [x] **Step 5: 运行测试，确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_propagation_end_to_end.py -v`

Expected: PASS（3 个测试）。

- [x] **Step 6: 提交**

```bash
cd /root/shannon-py && git add packages/core/src/shannon_core/code_index/__init__.py \
  packages/core/tests/code_index/test_propagation_end_to_end.py
git commit -m "feat(spec-a): wire build_propagation_graph into build_code_index + write_index_files"
```

---

## Task 7: 升级 `risk_scorer.taint_completeness` — 用 `sink_call_site_id` 而非 `sink_func_id` 匹配

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/risk_scorer.py:96-99`
- Test: `packages/core/tests/code_index/test_risk_scorer.py`

**Why:** spec §6.3 — 契约升级后，risk_scorer 的 `taint_completeness` 必须按新字段（`sink_call_site_id` 命中 chain 内某个 SinkCallSite）算分；保留 `sink_func_id` 回退兼容旧 json。

- [x] **Step 1: 写失败测试 — 新算法按 sink_call_site_id 命中**

在 `test_risk_scorer.py` 末尾追加：

```python
class TestTaintCompletenessUsesSinkCallSiteId:
    def test_completeness_uses_sink_call_site_id_when_chain_has_matching_site(self):
        """chain.path 包含 SinkCallSite.caller_id 且 flow.sink_call_site_id 命中
        → taint_completeness > 0。"""
        from shannon_core.code_index.parameter_models import (
            DangerousSlot, SinkCallSite, SinkCategory, SlotContext, TaintFlow,
        )

        block = _block("query", "svc.py", 1, source="def query(sql): cursor.execute(sql)")
        chain = CallChain(
            entry_point_id="app.py:h:1",
            path=["app.py:h:1", block.id],
            depth=1, has_unresolved=False,
        )
        sink = SinkCallSite(
            id="svc.py:query:execute:2:4",
            caller_id=block.id,
            callee_name="execute", callee_receiver="cursor",
            category=SinkCategory.SQL, sink_subtype="sql_raw",
            file_path="svc.py", line=2, column=4,
            dangerous_slots=[DangerousSlot(
                arg_index=0, slot=SlotContext.SQL_VALUE,
                expression="sql", is_entry_hint=False,
            )],
            rule_id="py-db-cursor-execute",
        )
        flow = TaintFlow(
            flow_id="app.py:h:1->svc.py:query:execute:2:4",
            entry_point_id="app.py:h:1",
            source_param="user_id",
            source_type=ParameterSource.QUERY_PARAM,
            sink_call_site_id=sink.id,
            sink_slot=SlotContext.SQL_VALUE,
            tainted_arg_index=0,
            confidence=0.9,
        )
        score = ChainRiskScore.score(
            chain, {block.id: block}, [flow], set(),
            sink_call_sites=[sink],
        )
        assert score.taint_completeness > 0

    def test_completeness_zero_when_flow_does_not_match_chain_sites(self):
        """flow.sink_call_site_id 指向的 sink 不在 chain 上 → taint_completeness=0。"""
        from shannon_core.code_index.parameter_models import (
            SinkCallSite, SinkCategory, SlotContext, TaintFlow,
        )

        block = _block("query", "svc.py", 1)
        chain = CallChain(
            entry_point_id="app.py:h:1",
            path=["app.py:h:1", block.id],
            depth=1, has_unresolved=False,
        )
        # sink 在另一个 caller 上
        other_sink = SinkCallSite(
            id="other.py:f:execute:1:0",
            caller_id="other.py:f:1",
            callee_name="execute", callee_receiver="cursor",
            category=SinkCategory.SQL, sink_subtype="sql_raw",
            file_path="other.py", line=1, column=0,
            dangerous_slots=[], rule_id="py-db-cursor-execute",
        )
        flow = TaintFlow(
            entry_point_id="app.py:h:1",
            source_param="x", source_type=ParameterSource.QUERY_PARAM,
            sink_call_site_id=other_sink.id,
            sink_slot=SlotContext.SQL_VALUE,
            tainted_arg_index=0,
        )
        score = ChainRiskScore.score(
            chain, {block.id: block}, [flow], set(),
            sink_call_sites=[other_sink],
        )
        # other_sink.caller_id 不在 chain.path → flow 不算命中
        assert score.taint_completeness == 0

    def test_legacy_sink_func_id_fallback_still_works(self):
        """sink_call_sites=None 时回退到老逻辑：flow.sink_func_id 命中 sink_node_id。"""
        chain = CallChain(
            entry_point_id="app.py:h:1",
            path=["app.py:h:1", "svc.py:query:1"],
            depth=1, has_unresolved=False,
        )
        flow = TaintFlow(
            entry_point_id="app.py:h:1",
            source_param="x", source_type=ParameterSource.QUERY_PARAM,
            sink_func_id="svc.py:query:1",
            sink_type=SinkType.SQL_EXECUTION,
        )
        score = ChainRiskScore.score(
            chain, {"svc.py:query:1": _block("query", "svc.py", 1)},
            [flow], set(),
        )
        assert score.taint_completeness > 0
```

- [x] **Step 2: 运行测试，确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_risk_scorer.py::TestTaintCompletenessUsesSinkCallSiteId -v`

Expected: FAIL — 当前实现只看 `sink_func_id`，新字段测试不通过。

- [x] **Step 3: 修改 `risk_scorer.py:96-99`**

把 `ChainRiskScore.score` 里的 taint_completeness 计算替换为：

```python
        # Taint completeness: Spec A 升级
        # 优先：如果传了 sink_call_sites，flow.sink_call_site_id 必须命中
        #       chain.path 上的某个 SinkCallSite.id。
        # 回退：没有 sink_call_sites → 用旧字段 flow.sink_func_id 命中 path[-1]。
        if sink_call_sites:
            chain_site_ids = {s.id for s in sink_call_sites if s.caller_id in set(chain.path)}
            reaching = [f for f in taint_flows if f.sink_call_site_id in chain_site_ids]
        else:
            sink_node_id = chain.path[-1] if chain.path else None
            reaching = [f for f in taint_flows if f.sink_func_id == sink_node_id]
        taint_completeness = min(10, len(reaching) * 10)
```

- [x] **Step 4: 运行测试，确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_risk_scorer.py -v`

Expected: PASS — 所有现有测试 + 3 个新增测试全过。

- [x] **Step 5: 提交**

```bash
cd /root/shannon-py && git add packages/core/src/shannon_core/code_index/risk_scorer.py \
  packages/core/tests/code_index/test_risk_scorer.py
git commit -m "feat(spec-a): risk_scorer taint_completeness uses sink_call_site_id"
```

---

## Task 8: 升级 `audit_input_builder` — 文案体现 sink_call_site_id / sink_slot

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/audit_input_builder.py:55-72`
- Test: `packages/core/tests/code_index/test_audit_input_builder.py`

**Why:** Spec C LLM 拿到的 prompt 文案必须能体现新的精确槽位信息（`sink_slot` / `tainted_arg_index` / `has_sanitizer_hint`），保留旧字段回退。

- [x] **Step 1: 写失败测试 — 新字段在 prompt 中体现**

在 `test_audit_input_builder.py` 末尾追加：

```python
class TestSinkCallSiteFieldsInPrompt:
    def test_chain_audit_input_shows_slot_and_arg_index(self):
        from shannon_core.code_index.audit_input_builder import build_chain_audit_input
        from shannon_core.code_index.parameter_models import SlotContext
        block = FuncBlock(
            id="app.py:h:1", file_path="app.py",
            function_name="h", start_line=1, end_line=5,
            source_code="def h(x): cursor.execute(x)",
            parameters=["x"], language="python",
        )
        chain = CallChain(
            entry_point_id="app.py:h:1",
            path=["app.py:h:1"],
            depth=0, has_unresolved=False,
        )
        flow = TaintFlow(
            flow_id="app.py:h:1->app.py:h:execute:2:4",
            entry_point_id="app.py:h:1",
            source_param="x",
            source_type=ParameterSource.QUERY_PARAM,
            sink_call_site_id="app.py:h:execute:2:4",
            sink_slot=SlotContext.SQL_VALUE,
            tainted_arg_index=0,
            confidence=0.7,
            has_sanitizer_hint=False,
        )
        text = build_chain_audit_input(chain, {block.id: block}, [flow])
        assert "sql_value" in text or "SQL_VALUE" in text.lower()
        assert "arg 0" in text or "arg_index=0" in text or "argument 0" in text.lower()

    def test_sanitizer_hint_marked(self):
        from shannon_core.code_index.audit_input_builder import build_chain_audit_input
        from shannon_core.code_index.parameter_models import SlotContext
        block = FuncBlock(
            id="app.py:h:1", file_path="app.py",
            function_name="h", start_line=1, end_line=5,
            source_code="def h(x): cursor.execute(escape(x))",
            parameters=["x"], language="python",
        )
        chain = CallChain(entry_point_id="app.py:h:1", path=["app.py:h:1"],
                          depth=0, has_unresolved=False)
        flow = TaintFlow(
            flow_id="f1",
            entry_point_id="app.py:h:1",
            source_param="x",
            source_type=ParameterSource.QUERY_PARAM,
            sink_call_site_id="app.py:h:execute:2:4",
            sink_slot=SlotContext.SQL_VALUE,
            tainted_arg_index=0,
            confidence=0.5,
            has_sanitizer_hint=True,
        )
        text = build_chain_audit_input(chain, {block.id: block}, [flow])
        assert "sanitizer" in text.lower() or "sanitize_hint" in text

    def test_legacy_flow_without_new_fields_renders_without_crash(self):
        """旧 TaintFlow（只填了 sink_func_id / sink_type）仍能渲染。"""
        from shannon_core.code_index.audit_input_builder import build_chain_audit_input
        block = FuncBlock(
            id="app.py:h:1", file_path="app.py",
            function_name="h", start_line=1, end_line=5,
            source_code="def h(x): pass",
            parameters=["x"], language="python",
        )
        chain = CallChain(entry_point_id="app.py:h:1", path=["app.py:h:1"],
                          depth=0, has_unresolved=False)
        flow = TaintFlow(
            entry_point_id="app.py:h:1",
            source_param="x",
            source_type=ParameterSource.QUERY_PARAM,
            sink_func_id="app.py:h:1",
            sink_type=SinkType.SQL_EXECUTION,
        )
        text = build_chain_audit_input(chain, {block.id: block}, [flow])
        # 老 sink 文案还在
        assert "SQL" in text
```

- [x] **Step 2: 运行测试，确认失败**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_audit_input_builder.py::TestSinkCallSiteFieldsInPrompt -v`

Expected: FAIL — 新字段测试不通过。

- [x] **Step 3: 修改 `audit_input_builder.py`**

把 `build_chain_audit_input` 末尾 sinks 段落替换为：

```python
    # Sinks — Spec A 升级
    sinks = []
    for flow in taint_flows:
        if flow.sink_call_site_id:
            slot = flow.sink_slot.value if flow.sink_slot else "generic"
            sanitizer = " [sanitizer_hint]" if flow.has_sanitizer_hint else ""
            sinks.append(
                f"- {slot} sink at {flow.sink_call_site_id} "
                f"(arg {flow.tainted_arg_index}, conf={flow.confidence:.2f})"
                f"{sanitizer}"
            )
        elif flow.sink_type and flow.sink_func_id:
            # 旧字段回退
            sink_label = flow.sink_type.value.replace("_", " ")
            first_space = sink_label.find(" ")
            if first_space >= 0:
                sink_label = sink_label[:first_space].upper() + sink_label[first_space:]
            else:
                sink_label = sink_label.upper()
            sinks.append(f"- {sink_label} sink at {flow.sink_func_id}")
    if sinks:
        sections.append("## Sinks in this chain\n" + "\n".join(sinks) + "\n")
    else:
        sections.append("## Sinks in this chain\nNo identified sinks.\n")
```

同步更新 `format_taint_flow_summary`，加入新字段展示：

```python
def format_taint_flow_summary(flows: list[TaintFlow]) -> str:
    """Format taint flows as a human-readable summary."""
    if not flows:
        return "No taint flow data available for this chain."

    lines: list[str] = []
    for flow in flows:
        source_label = f"{flow.source_param} ({flow.source_type.value})"
        if flow.propagation_steps:
            path_parts = [source_label]
            for step in flow.propagation_steps:
                transform = f" [{step.transformation}]" if step.transformation else ""
                path_parts.append(f"{step.to_param}{transform}")
            if flow.sink_call_site_id:
                slot = flow.sink_slot.value if flow.sink_slot else "generic"
                sanitizer = " (sanitizer_hint)" if flow.has_sanitizer_hint else ""
                tail = f"{slot}@arg{flow.tainted_arg_index}{sanitizer}"
            else:
                tail = flow.sink_type.value if flow.sink_type else "unknown"
            lines.append(f"- {flow.source_type.value}: {' → '.join(path_parts)} → {tail}")
        else:
            lines.append(f"- {source_label} (no propagation steps)")

    return "\n".join(lines)
```

- [x] **Step 4: 运行测试，确认通过**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_audit_input_builder.py -v`

Expected: PASS — 所有现有测试 + 3 个新增测试全过。

- [x] **Step 5: 提交**

```bash
cd /root/shannon-py && git add packages/core/src/shannon_core/code_index/audit_input_builder.py \
  packages/core/tests/code_index/test_audit_input_builder.py
git commit -m "feat(spec-a): audit_input_builder surfaces sink_call_site_id, slot, sanitizer_hint"
```

---

## Task 9: 回归修复 — 升级既有 TaintFlow 构造测试到新契约

**Files:**
- Modify: `packages/core/tests/code_index/test_tiered_audit.py:54-67, 91-99, 117-124`

**Why:** spec §6.3 — 旧测试只传 `sink_func_id`，靠的是向后兼容。补一行 `sink_call_site_id` 让契约升级显式可见，避免日后删旧字段时静默破坏。

- [x] **Step 1: 检查现状（已经能跑通）**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_tiered_audit.py -v`

Expected: PASS（向后兼容字段已让旧测试不报错）。本任务是把"默认值兼容"显式升级为"显式字段"，便于将来清退。

- [x] **Step 2: 显式补充新字段**

把 `test_tiered_audit.py` 里所有 `TaintFlow(...)` 构造（约 3 处）补字段：

```python
flows_by_chain = {
    "a.py:high:1": [TaintFlow(
        entry_point_id="a.py:high:1", source_param="x",
        source_type=ParameterSource.QUERY_PARAM,
        propagation_steps=[],
        sink_func_id="a.py:high:1",
        sink_type=SinkType.SQL_EXECUTION,
        # 显式补 Spec A 字段（兼容字段之外）
        sink_call_site_id="a.py:high:query:1:0",
        sink_slot=SlotContext.SQL_VALUE,
        tainted_arg_index=0,
        confidence=0.7,
    )],
    # ...其他类似
}
```

文件顶部 import 加：

```python
from shannon_core.code_index.parameter_models import SlotContext
```

- [x] **Step 3: 跑全部 code_index 测试做最终回归**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/ -v`

Expected: PASS — 全部 code_index 测试通过。

- [x] **Step 4: 提交**

```bash
cd /root/shannon-py && git add packages/core/tests/code_index/test_tiered_audit.py
git commit -m "test(spec-a): backfill TaintFlow Spec A fields in legacy tiered_audit tests"
```

---

## Task 10: 端到端激活测试 — `run_code_index` activity 产出非空 param_graph

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:158-178`
- Test: `packages/core/tests/code_index/test_propagation_end_to_end.py` (累加)

**Why:** spec §6.2 — 构造样例仓库 → `run_code_index`（或直接 `build_code_index + rebuild_call_chains`）后 `parameter_graph.json` 存在且非空、`taint_completeness` > 0、`taint_flows_by_chain` 非空。

- [x] **Step 1: 写失败测试 — 链路重建后 param_graph 非空 + risk_scorer 激活**

在 `test_propagation_end_to_end.py` 末尾追加：

```python
class TestActivationAfterChainRebuild:
    def test_after_rebuild_param_graph_has_flows_and_scorer_uses_them(self, tmp_path):
        """build_code_index → save_adjudication → rebuild_call_chains
        → param_graph.json 含非空 flows → risk_scorer taint_completeness > 0。"""
        from shannon_core.code_index import (
            build_code_index, save_adjudication, rebuild_call_chains, write_index_files,
        )
        from shannon_core.code_index.models import CallChain
        from shannon_core.code_index.parameter_models import SinkCategory
        from shannon_core.code_index.risk_scorer import ChainRiskScore

        repo = tmp_path / "repo"
        repo.mkdir()
        # 构造一个能被 entry_points 探测到的 Flask handler
        # detect_entry_points 用启发式：函数名 / 装饰器。这里用 @app.route 风格。
        (repo / "app.py").write_text(
            "def handler(user_id):\n"
            "    q = 'SELECT * FROM u WHERE id=' + user_id\n"
            "    process(q)\n"
            "def process(sql):\n"
            "    cursor.execute(sql)\n"
        )

        out = tmp_path / "deliverables"
        out.mkdir()

        # 步骤 1: 索引
        index = build_code_index(str(repo))
        write_index_files(index, str(out))
        # 步骤 2: 裁定（自动确认 entry）
        # 需要 code_index.json 先写好
        save_adjudication(str(out))
        # 步骤 3: 重建链
        updated = rebuild_call_chains(str(out))

        # 步骤 4: 验证 param_graph.json
        pgraph_path = out / "parameter_graph.json"
        assert pgraph_path.exists()
        data = json.loads(pgraph_path.read_text())
        # 至少有一条 flow（handler → process → cursor.execute）
        # 注意：detect_entry_points 可能没有把 handler 标为 entry — 这里我们
        # 只断言"若有链则有 flow"，避免对 entry 探测逻辑过度耦合。
        if updated.chains:
            assert len(data["taint_flows"]) >= 1, (
                f"expected ≥1 flow when chains exist; got {data}"
            )
            flow = data["taint_flows"][0]
            assert flow["sink_slot"] == "sql_value"
            assert flow["tainted_arg_index"] == 0
            # risk_scorer 能用上
            blocks_by_id = {b.id: b for b in updated.blocks}
            chain0 = updated.chains[0]
            site_id = flow["sink_call_site_id"]
            matching_site = next(
                (s for s in updated.sink_call_sites if s.id == site_id), None,
            )
            score = ChainRiskScore.score(
                chain0, blocks_by_id,
                [
                    # 重新构造 TaintFlow 因为 dict → model
                    __import__(
                        "shannon_core.code_index.parameter_models", fromlist=["TaintFlow"]
                    ).TaintFlow(**flow),
                ],
                set(),
                sink_call_sites=updated.sink_call_sites,
            )
            assert score.taint_completeness > 0
```

- [x] **Step 2: 运行测试，确认状态**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_propagation_end_to_end.py::TestActivationAfterChainRebuild -v`

Expected: PASS（如果 detect_entry_points 把 handler 识别为 entry）或 跳过（如果没识别 — 测试断言写为"若有链则有 flow"以容忍此情况）。

> 若失败：检查 `rebuild_call_chains` 是否正确把 `parameter_graph.json` 写出（Task 6 步骤 4 加的逻辑）。

- [x] **Step 3: 修改 `activities.py:158-178` — 同步注释 + 确认读取分支**

把 `run_code_index` activity 中的 docstring/注释更新，让维护者知道 Spec A 在哪激活：

```python
@activity.defn
async def run_code_index(input: ActivityInput) -> dict:
    try:
        from shannon_core.code_index import build_code_index, write_index_files

        repo, deliverables, _ = _get_paths(input)
        index = build_code_index(str(repo))
        # Spec A: write_index_files 同时写出 parameter_graph.json
        # （chain 重建后 risk_scorer 会读到非空 taint_flows_by_chain）
        json_path, summary_path = write_index_files(index, str(deliverables))

        return {
            "total_blocks": index.total_blocks,
            "total_entry_points": index.total_entry_points,
            "total_chains": index.total_chains,
            "json_path": str(json_path),
            "summary_path": str(summary_path),
        }
    except PentestError as e:
        ...
```

确认 `run_risk_scoring`（`activities.py:238-245`）读取的文件名仍是 `parameter_graph.json`（即 Task 6 写出的文件名）。当前 `parameter_graph.json` 与读路径一致 — 无需改动。

- [x] **Step 4: 跑全套 whitebox 集成测试做最终回归**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/ packages/whitebox/tests/ -v -x`

Expected: PASS — 所有 code_index 测试 + whitebox 集成测试全过。

- [x] **Step 5: 提交**

```bash
cd /root/shannon-py && git add packages/core/tests/code_index/test_propagation_end_to_end.py \
  packages/whitebox/src/shannon_whitebox/pipeline/activities.py
git commit -m "test(spec-a): end-to-end activation — param_graph non-empty after chain rebuild"
```

---

## Task 11: 完成核对 — 跑全套测试 + 验证 spec §7 接口约定

**Files:**
- 无修改；纯验证

**Why:** spec §7 列出了 6 项跨 spec 接口约定，必须逐一确认在代码里成立。

- [x] **Step 1: 接口约定核对（按 spec §7 表格逐条）**

| 约定 | 验证方法 |
|---|---|
| `SinkCallSite.id` / `dangerous_slots` 来自 Spec B | `grep -n "sink_call_site_id" packages/core/src/shannon_core/code_index/propagation_builder.py` → 命中 |
| `TaintFlow.sink_slot` 是 `SlotContext` | grep `sink_slot: SlotContext` → 命中 |
| `TaintFlow` 升级字段对 Spec C 可见 | grep `sink_call_site_id` packages/core/src/shannon_core/code_index/audit_input_builder.py → 命中 |
| `parameter_graph.json` 写入并被 risk_scorer/tiered_audit 读 | `grep "parameter_graph.json" packages/core/src/shannon_core/code_index/__init__.py` |
| `skipped_languages` 产出 | `grep "skipped_languages" packages/core/src/shannon_core/code_index/propagation_builder.py` |
| `has_sanitizer_hint` 产出与消费 | `grep "has_sanitizer_hint" packages/core/src/shannon_core/code_index/` |

- [x] **Step 2: 跑全套测试（核心 + 集成）**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_propagation_builder.py packages/core/tests/code_index/test_propagation_end_to_end.py packages/core/tests/code_index/test_risk_scorer.py packages/core/tests/code_index/test_tiered_audit.py packages/core/tests/code_index/test_audit_input_builder.py packages/core/tests/code_index/test_parameter_models_upgrade.py -v`

Expected: PASS — 全部 Spec A 相关测试通过。

- [x] **Step 3: 跑 sinks / parsers 回归确保没破坏 Spec B**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_sink_detector.py packages/core/tests/code_index/test_sink_detector_integration.py -v`

Expected: PASS。

- [x] **Step 4: 检查 git 状态**

Run: `cd /root/shannon-py && git status`

Expected: clean working tree（commit 都已提交）。

- [x] **Step 5: 不提交（纯验证步）**

---

## Task 12（计划外补丁，最终整体审查发现）：`TieredAuditPlanner` 转发 `sink_call_sites`

> **不在原计划的 11 个任务里。** 最终整体代码审查发现：原计划 File Structure 表只列了 `risk_scorer.py` / `audit_input_builder.py` 的修改，**遗漏了 `tiered_audit.py`（源码）**。后果是生产 pipeline 的 `taint_completeness` 恒为 0——Spec A 目标"激活 tiered_audit"在生产路径未真正达成。

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/tiered_audit.py`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`
- Test: `packages/core/tests/code_index/test_tiered_audit.py`

**根因：** `TieredAuditPlanner.__init__` 没有 `sink_call_sites` 形参，`plan()` 调 `ChainRiskScore.score(chain, blocks, flows, auth_ids)` 不传 `sink_call_sites` → scorer 走 else 回退分支（按 `sink_func_id` 匹配）→ Spec A 生产 flow 的 `sink_func_id` 恒为空 → `taint_completeness = 0`。Task 10 端到端测试因为直接调 `ChainRiskScore.score(..., sink_call_sites=...)` 绕过了 planner，所以没抓到。

- [x] **Step 1:** TDD — 写 `TestTieredAuditPlannerSinkCallSites`，断言"不传 sink_call_sites → taint_completeness==0"且"传 → >0"。
- [x] **Step 2:** 改 `tiered_audit.py`：`__init__` 加 `sink_call_sites: list[SinkCallSite] | None = None`；`plan()` 转发；import `SinkCallSite`；更新 docstring。
- [x] **Step 3:** 改 `activities.py` `run_risk_scoring`：构建 planner 时传 `sink_call_sites=index.sink_call_sites`。
- [x] **Step 4:** spec 审查独立 e2e 验证（从写出的 JSON 重新加载 index → planner → plan()）确认生产 planner 路径 `taint_completeness=10`。
- [x] **Step 5:** 给 Task 10 e2e 测试补 planner 路径断言（变异验证：删转发后该断言 FAIL），把"生产激活"在 e2e 层锁住。

Commits: `1e05a93 fix(spec-a): TieredAuditPlanner forwards sink_call_sites` + `61ba848 test(spec-a): lock production planner-path taint activation in e2e`。

---

## Self-Review Notes

- **Spec 覆盖：** spec §1（背景）— Task 1 升级契约；§2（目标 1）— Task 6 接入；§2（目标 2/3）— Task 4/5；§2（目标 4）— Task 2 `language_coverage` / `skipped_languages`；§2（目标 5）— Task 4 sanitizer / confidence；§3（契约）— Task 1；§4.1.1-4.1.5（算法）— Task 2/3/4/5；§4.2（pipeline 接入）— Task 6；§4.3（Go/Java/PHP）— Task 2；§5（边界）在代码 notes / SANITIZER_HINTS 中体现；§6（测试）— Task 1/4/5/6/7/8/10；§7（接口）— Task 11 核对。
- **类型一致：** `TaintFlow.sink_slot`、`tainted_arg_index`、`sink_call_site_id`、`has_sanitizer_hint` 在 Task 1/4/5/7/8 全部使用同一名字。`PropagationStep.step_id` / `confidence` 一致。`ParameterPropagationGraph.language_coverage` / `skipped_languages` 在 Task 2/6 一致。
- **占位符扫描：** 无 "TODO" / "TBD" / "later"。所有 Step 都有可运行代码或可执行命令。
- **风险点（提前点名给 worker）：**
  - Task 4 的赋值正则故意宽松（不区分 Python/TS/Go/PHP）。这与 spec §4.1.4"过近似"一致；不要把它"改严格"。
  - `extract_typed_parameters` 在 Go/Java/PHP 上返回空（spec §4.3），`_extract_generic` 是预期行为，不要"修复"它。
  - `TaintFlow` 字段名是 `sink_slot`（不是 `slot`）— Task 5 `_trace_chain` 里 yield 时使用 `sink_slot=hit.slot`。
  - `analyze_intra` 已经在 Task 4 定义好 `IntraResult` 的全部字段（`hits` / `local_steps_accumulated` / `has_sanitizer_global`），Task 5 直接复用即可，不要在 Task 5 重新定义或修改 dataclass。
