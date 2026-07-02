# GitNexus 轨 sanitizer 管道断链修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通 GitNexus 轨 inj/xss/ssrf 判定链的 sanitizer 信息管道(让 `transformation` 字段真正有值),并把 expression/intermediate_vars/post_concat 接进 chain_verdict prompt,使判定 LLM 真正具备防护有效性判定能力(不再「有链就报 vulnerable」)。

**Architecture:** 方案 A(正统管道修复)。修复双重断链:① `_intra_result_from_llm` 丢弃 LLM 返回的 sanitizer 信息(`local_steps=[]`)② `propagate_backward` 不合并 intra `local_steps` 进 `TaintFlow`。让 `transformation` 字段兑现设计意图,`sanitize_library`/`annotate_sanitizers`/`_detect_post_sanitize_concat` 整套机制不再空转。详见 `docs/superpowers/specs/2026-07-03-gitnexus-track-sanitizer-pipeline-fix-design.md`。

**Tech Stack:** Python 3、pydantic、pytest、pytest-asyncio。

## Global Constraints

- **双轨铁律**:本修复全部在 GitNexus 轨内部(intra/propagator/chain_verdict),**不动 LLM 轨**任何文件。不得引入「确定性产物喂 LLM 轨」的耦合。
- **向后兼容**:新增字段一律 pydantic 默认值(空 list / False),旧 `parameter_graph.json`/`code_index.json` 反序列化不破。
- **测试隔离**:只跑改动相关测试文件,勿广跑全套(shannon-py 全量 pytest 会 hang,见 memory `pytest-whitebox-hang`)。
- **TDD**:每个 task 先写失败测试,跑红,再实现,跑绿,最后 commit。
- **前置依赖(非本 plan 范围)**:本 plan 只保证「管道接通」(单元/集成测试锚点)。真机验证需 GitNexus `chains>0`(三靶场 NodeGoat 长期 chains=0,MCP 失配已修 @8734a319 待验);若 `taint_flows=0` 则 transformation 仍空属前置问题,非本 plan 缺陷。

---

## File Structure

| 文件 | 职责 | 改动 |
|---|---|---|
| `packages/core/src/shannon_core/code_index/parameter_models.py` | 数据模型 | 加 `PropagationStep.intermediate_vars` + `TaintPath.post_sanitized_concat` |
| `packages/core/src/shannon_core/code_index/llm_taint_analyzer.py` | intra LLM 分析 | `_intra_result_from_llm` 构造 `local_steps`;`build_taint_prompt` 加 `post_sanitized_concat` |
| `packages/core/src/shannon_core/code_index/chain_propagator.py` | 跨函数传播 | `propagate_backward_across_chains` 合并 intra `local_steps`(含单函数 case) |
| `packages/core/src/shannon_core/code_index/chain_verdict.py` | 候选链 + 判定 | `_DIRECTION`、`_detect_post_sanitize_concat`、`CandidateChain.sink_expressions`、`extract_candidate_chains`、prompt |
| `packages/core/src/shannon_core/code_index/vuln_chain_builders/injection_builder.py` | inj builder | 签名接收 + 透传 `sink_call_sites` |
| `packages/core/src/shannon_core/code_index/vuln_chain_builders/ssrf_builder.py` | ssrf builder | 同上 |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | chain-verdict activity | inj/ssrf 的 else 分支补传 `sink_call_sites` |
| `packages/core/tests/code_index/test_sanitizer_pipeline_e2e.py` | 端到端集成 | 新建 |

---

## Task 1: 数据模型扩展(PropagationStep.intermediate_vars + TaintPath.post_sanitized_concat)

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/parameter_models.py:47`(PropagationStep)、`:96-104`(TaintPath)
- Test: `packages/core/tests/code_index/test_parameter_models_upgrade.py`

**Interfaces:**
- Produces: `PropagationStep.intermediate_vars: list[str] = []`(默认空,旧 json 兼容);`TaintPath.post_sanitized_concat: bool = False`(默认 False,旧 json 兼容)。下游 Task 2 消费这两个字段。

- [ ] **Step 1: 写失败测试**

追加到 `packages/core/tests/code_index/test_parameter_models_upgrade.py`:

```python
from shannon_core.code_index.parameter_models import PropagationStep, TaintPath


def test_propagation_step_defaults_intermediate_vars_empty():
    """PropagationStep 新字段 intermediate_vars 默认空(旧 json 兼容)。"""
    step = PropagationStep(from_func_id="f1", from_param="x", to_func_id="f2", to_param="y")
    assert step.intermediate_vars == []


def test_propagation_step_roundtrip_intermediate_vars():
    step = PropagationStep(
        from_func_id="f1", from_param="x", to_func_id="f2", to_param="y",
        intermediate_vars=["raw", "escaped"],
    )
    restored = PropagationStep.model_validate_json(step.model_dump_json())
    assert restored.intermediate_vars == ["raw", "escaped"]


def test_taint_path_defaults_post_sanitized_concat_false():
    """TaintPath 新字段 post_sanitized_concat 默认 False(旧 json 兼容)。"""
    path = TaintPath(source_param="x", sink_id="s1", sink_arg_index=0)
    assert path.post_sanitized_concat is False


def test_taint_path_old_json_without_post_concat_field_compat():
    """旧 json 无 post_sanitized_concat 字段 → 反序列化默认 False。"""
    old = {
        "source_param": "x", "sink_id": "s1", "sink_arg_index": 0,
        "intermediate_vars": [], "sanitized": False,
        "sanitizer_description": None, "confidence": 0.9,
    }
    path = TaintPath.model_validate(old)
    assert path.post_sanitized_concat is False
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_parameter_models_upgrade.py::test_propagation_step_defaults_intermediate_vars_empty -v`
Expected: FAIL with `ValidationError` / unexpected keyword `intermediate_vars`(字段不存在)

- [ ] **Step 3: 实现(加字段)**

`parameter_models.py:47` PropagationStep 改为:

```python
    transformation: str | None = None  # "concat" / "encode" / "format" / "sanitize_hint:<name>" / None
    code_location: str = ""            # "{file}:{line}"
    intermediate_vars: list[str] = []  # 函数内中间变量(供 chain_verdict 追踪信息密度)
    confidence: float = 1.0            # 本步映射的可信度
```

`parameter_models.py:96-104` TaintPath 改为(在 `sanitizer_description` 后、`confidence` 前加字段):

```python
class TaintPath(BaseModel):
    """LLM 返回的单条 taint 传播路径。"""
    source_param: str
    sink_id: str
    sink_arg_index: int
    intermediate_vars: list[str] = []
    sanitized: bool = False
    sanitizer_description: str | None = None
    post_sanitized_concat: bool = False   # 消毒后再拼接(escape 后又 concat 污染源/多源合并)
    confidence: float = 1.0
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_parameter_models_upgrade.py -v`
Expected: PASS(4 个新测试 + 既有测试全绿)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/parameter_models.py packages/core/tests/code_index/test_parameter_models_upgrade.py
git commit -m "feat(code_index): PropagationStep/TaintPath 加 intermediate_vars/post_sanitized_concat 字段"
```

---

## Task 2: intra 层流出的 sanitizer 信息(_intra_result_from_llm + build_taint_prompt)

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/llm_taint_analyzer.py:17-22`(import)、`:147-169`(build_taint_prompt schema/Rules)、`:195-223`(_intra_result_from_llm)
- Test: `packages/core/tests/code_index/test_llm_taint_analyzer.py`

**Interfaces:**
- Consumes: Task 1 的 `TaintPath.post_sanitized_concat` + `PropagationStep.intermediate_vars`
- Produces: `_intra_result_from_llm` 返回的 `IntraResult.local_steps` 非空,每个 summary `PropagationStep` 携带 `transformation="sanitize_hint:<desc>[|post_concat]"`、`intermediate_vars`、`to_param=sink_id`。Task 3 消费 `local_steps`。

- [ ] **Step 1: 写失败测试**

追加到 `packages/core/tests/code_index/test_llm_taint_analyzer.py`:

```python
from shannon_core.code_index.llm_taint_analyzer import _intra_result_from_llm
from shannon_core.code_index.parameter_models import (
    IntraResult, SinkCallSite, SinkCategory, TaintAnalysisResult, TaintPath,
)
from shannon_core.code_index.models import FuncBlock


def _blk():
    return FuncBlock(
        id="app.py:handler", function_name="handler", file_path="app.py",
        start_line=10, end_line=20, parameters=["q"], source_code="def handler(q): ...",
    )


def _sink(sid="app.py:handler:db.execute:15:0", line=15):
    return SinkCallSite(
        id=sid, caller_id="app.py:handler", callee_name="execute",
        callee_receiver="db", category=SinkCategory.SQL, sink_subtype="sql_raw_query",
        file_path="app.py", line=line, column=8, dangerous_slots=[], rule_id="py-sql-execute",
    )


def test_intra_result_preserves_sanitizer_in_local_steps():
    """sanitized=True 的 path → local_steps summary step 携带 sanitize_hint。"""
    llm_result = TaintAnalysisResult(
        tainted_params=["q"],
        propagation_paths=[TaintPath(
            source_param="q", sink_id="app.py:handler:db.execute:15:0", sink_arg_index=0,
            intermediate_vars=["raw"], sanitized=True,
            sanitizer_description="html.escape", post_sanitized_concat=True,
            confidence=0.9,
        )],
    )
    result = _intra_result_from_llm(_blk(), llm_result, [_sink()])
    assert isinstance(result, IntraResult)
    assert len(result.local_steps) == 1
    step = result.local_steps[0]
    assert step.transformation is not None
    assert "sanitize_hint:html.escape" in step.transformation
    assert "post_concat" in step.transformation          # post_sanitized_concat 编码进 transformation
    assert step.intermediate_vars == ["raw"]
    assert step.to_param == "app.py:handler:db.execute:15:0"   # 指向 sink
    assert step.code_location == "app.py:15"
    # tainted_params / hits 仍保留(不回归)
    assert result.tainted_params == {"q"}
    assert "app.py:handler:db.execute:15:0" in result.hits


def test_intra_result_unsanitized_path_has_null_transformation():
    """sanitized=False 的 path → summary step transformation=None(无防护标注)。"""
    llm_result = TaintAnalysisResult(
        tainted_params=["q"],
        propagation_paths=[TaintPath(
            source_param="q", sink_id="app.py:handler:db.execute:15:0", sink_arg_index=0,
            sanitized=False, sanitizer_description=None, confidence=0.8,
        )],
    )
    result = _intra_result_from_llm(_blk(), llm_result, [_sink()])
    assert len(result.local_steps) == 1
    assert result.local_steps[0].transformation is None


def test_intra_result_skips_invalid_sink_or_param():
    """sink_id / source_param 不在已知集合 → 跳过(不进 local_steps/hits)。"""
    llm_result = TaintAnalysisResult(
        tainted_params=["q"],
        propagation_paths=[TaintPath(
            source_param="evil",   # 非函数参数
            sink_id="app.py:handler:db.execute:15:0", sink_arg_index=0, confidence=0.9,
        )],
    )
    result = _intra_result_from_llm(_blk(), llm_result, [_sink()])
    assert result.local_steps == []
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_llm_taint_analyzer.py::test_intra_result_preserves_sanitizer_in_local_steps -v`
Expected: FAIL(`local_steps` 为空 / `transformation` 为 None——当前实现硬编码 `local_steps=[]`)

- [ ] **Step 3: 实现**

`llm_taint_analyzer.py:17-22` import 块加 `PropagationStep`:

```python
from shannon_core.code_index.parameter_models import (
    IntraResult,
    PropagationStep,
    SinkCallSite,
    TaintAnalysisResult,
    TaintPath,
)
```

`llm_taint_analyzer.py:147-160` build_taint_prompt 的 schema JSON 加 `post_sanitized_concat`:

```python
    parts.append(json.dumps({
        "tainted_params": ["param_name"],
        "propagation_paths": [
            {
                "source_param": "param_name",
                "sink_id": "sink_id",
                "sink_arg_index": 0,
                "intermediate_vars": ["var1"],
                "sanitized": False,
                "sanitizer_description": None,
                "post_sanitized_concat": False,
                "confidence": 0.9,
            }
        ],
    }, indent=2))
```

`llm_taint_analyzer.py:163-169` Rules 段加一条:

```python
    parts.append(
        "Rules:\n"
        "- tainted_params: list all parameters that can reach a sink\n"
        "- propagation_paths: one entry per param->sink path\n"
        "- post_sanitized_concat: true if the path is sanitized but then re-tainted "
        "(e.g. escape() result concatenated with raw input, or merged with another source)\n"
        "- confidence: 0.0-1.0, how certain the taint reaches the sink\n"
        "- Only include paths you are confident about"
    )
```

`llm_taint_analyzer.py:195-223` `_intra_result_from_llm` 整体替换为:

```python
def _intra_result_from_llm(
    block: FuncBlock,
    llm_result: TaintAnalysisResult,
    sinks_in_func: list[SinkCallSite],
) -> IntraResult:
    """Convert TaintAnalysisResult to IntraResult.

    Validates tainted_params against block.parameters and sink_ids against
    known sinks. Preserves sanitizer info (sanitized/sanitizer_description/
    intermediate_vars/post_sanitized_concat) into local_steps as summary
    PropagationStep — 之前硬编码 local_steps=[] 导致 sanitizer 管道断链。
    """
    valid_params = set(block.parameters)
    valid_sink_ids = {s.id for s in sinks_in_func}
    sink_line_map = {s.id: s.line for s in sinks_in_func}

    tainted = {p for p in llm_result.tainted_params if p in valid_params}

    hits: dict[str, float] = {}
    local_steps: list[PropagationStep] = []
    for path in llm_result.propagation_paths:
        if path.sink_id not in valid_sink_ids or path.source_param not in valid_params:
            continue
        existing = hits.get(path.sink_id, 0.0)
        hits[path.sink_id] = max(existing, path.confidence)

        # summary step:函数内 param→sink 路径,transformation 编码 sanitizer + post_concat
        tf: str | None = None
        if path.sanitized:
            desc = path.sanitizer_description or "unknown"
            tf = f"sanitize_hint:{desc}"
            if path.post_sanitized_concat:
                tf += "|post_concat"
        local_steps.append(PropagationStep(
            from_func_id=block.id,
            from_param=path.source_param,
            to_func_id=block.id,            # sink 在本函数内
            to_param=path.sink_id,
            transformation=tf,
            code_location=f"{block.file_path}:{sink_line_map.get(path.sink_id, block.start_line)}",
            intermediate_vars=list(path.intermediate_vars),
            confidence=path.confidence,
        ))

    return IntraResult(
        tainted_params=tainted,
        hits=hits,
        local_steps=local_steps,
    )
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_llm_taint_analyzer.py -v`
Expected: PASS(3 个新测试 + 既有测试全绿)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/llm_taint_analyzer.py packages/core/tests/code_index/test_llm_taint_analyzer.py
git commit -m "feat(code_index): _intra_result_from_llm 流出 sanitizer/intermediate_vars 到 local_steps"
```

---

## Task 3: propagate_backward 合并 intra local_steps(含单函数场景)

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/chain_propagator.py:413-461`(`propagate_backward_across_chains` 的 sink 段 + flow 构造)
- Test: `packages/core/tests/code_index/test_chain_propagator_backward.py`

**Interfaces:**
- Consumes: Task 2 的 `IntraResult.local_steps`(summary step,`to_param == sink.id`)
- Produces: `TaintFlow.propagation_steps` 末尾含 sink 所在函数的 intra summary step(带 `transformation`)。覆盖单函数(`sink_step=0`)与多函数两种 case。Task 5/6 经 `extract_candidate_chains` 消费。

- [ ] **Step 1: 写失败测试**

追加到 `packages/core/tests/code_index/test_chain_propagator_backward.py`(若该文件无现成 `FuncBlock`/`SinkCallSite` helper,复用本 task 自带的 `_blk`/`_sink` 构造;参考其既有风格):

```python
from shannon_core.code_index.chain_propagator import propagate_backward_across_chains
from shannon_core.code_index.models import FuncBlock, CallChain
from shannon_core.code_index.parameter_models import (
    IntraResult, PropagationStep, SinkCallSite, SinkCategory, SourcePoint,
)
from shannon_core.code_index.models import ParameterSource


def _blk(fid="app.py:handler", line=10):
    return FuncBlock(
        id=fid, function_name=fid.split(":")[-1], file_path="app.py",
        start_line=line, end_line=line + 10, parameters=["q"], source_code="x",
    )


def _sink(sid="app.py:handler:db.execute:12:0", caller="app.py:handler", line=12):
    return SinkCallSite(
        id=sid, caller_id=caller, callee_name="execute", callee_receiver="db",
        category=SinkCategory.SQL, sink_subtype="sql_raw_query",
        file_path="app.py", line=line, column=4, dangerous_slots=[], rule_id="r",
    )


def _src(entry="app.py:handler"):
    return SourcePoint(
        id=f"{entry}::q::10", entry_point_id=entry, param_name="q",
        source_type=ParameterSource.QUERY_PARAM, expression="req.query.q",
        file_path="app.py", line=10,
    )


def _intra_with_sanitizer(func_id, sink_id):
    """intra 产出一个带 sanitizer 的 summary step(Task 2 的产物形态)。"""
    return IntraResult(
        tainted_params={"q"},
        hits={sink_id: 0.9},
        local_steps=[PropagationStep(
            from_func_id=func_id, from_param="q", to_func_id=func_id, to_param=sink_id,
            transformation="sanitize_hint:html.escape|post_concat",
            code_location="app.py:12", intermediate_vars=["raw"], confidence=0.9,
        )],
    )


def test_backward_merges_intra_sanitizer_step_single_function():
    """单函数注入(sink 在 entry,sink_step=0):flow.propagation_steps 必须含 intra summary step。

    回归锚点:之前 sink_step=0 时 steps_fwd=[] → sanitizer 流不通(最常见注入场景)。
    """
    handler = _blk("app.py:handler", line=10)
    sink = _sink("app.py:handler:db.execute:12:0", "app.py:handler", 12)
    chains = [CallChain(entry_point_id="app.py:handler", path=["app.py:handler"],
                        depth=0, has_unresolved=False)]   # 单节点:entry 即 sink 函数
    intra = {"app.py:handler": _intra_with_sanitizer("app.py:handler", sink.id)}

    flows = propagate_backward_across_chains(
        chains=chains, blocks=[handler], intra_results=intra,
        sink_call_sites=[sink], source_points=[_src()],
    )
    assert len(flows) == 1
    steps = flows[0].propagation_steps
    assert any(s.transformation and "sanitize_hint:html.escape" in s.transformation for s in steps), \
        "单函数场景下 intra summary step 必须被合并进 TaintFlow"


def test_backward_merges_intra_sanitizer_step_multi_function():
    """多函数(entry→sink_func):跨函数 hop + sink_func 内 summary step 都在。"""
    entry = _blk("app.py:entry", line=1)
    callee = _blk("app.py:db", line=50)
    sink = _sink("app.py:db:execute:52:0", "app.py:db", 52)
    chains = [CallChain(entry_point_id="app.py:entry", path=["app.py:entry", "app.py:db"],
                        depth=1, has_unresolved=False)]
    intra = {"app.py:db": _intra_with_sanitizer("app.py:db", sink.id)}

    flows = propagate_backward_across_chains(
        chains=chains, blocks=[entry, callee], intra_results=intra,
        sink_call_sites=[sink], source_points=[_src("app.py:entry")],
    )
    assert len(flows) == 1
    steps = flows[0].propagation_steps
    # 既有跨函数 hop,又有带 sanitizer 的 summary step
    assert any(s.transformation and "sanitize_hint" in s.transformation for s in steps)
```

> 注:`CallChain` 必填字段为 `entry_point_id/path/depth/has_unresolved`(models.py:63-69,均无默认值),构造时四个都给。

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_chain_propagator_backward.py::test_backward_merges_intra_sanitizer_step_single_function -v`
Expected: FAIL(当前 `steps_fwd=[]`,断言 `any(... sanitize_hint ...)` 为 False)

- [ ] **Step 3: 实现**

`chain_propagator.py` 的 `propagate_backward_across_chains`,在 `flows.append(TaintFlow(...))` 处(chain_propagator.py:449-461)合并 intra summary step。把当前:

```python
                for sp in anchored:
                    steps_fwd = list(reversed(steps_rev))
                    flows.append(TaintFlow(
                        flow_id=f"{sp.entry_point_id}->{sink.id}",
                        entry_point_id=sp.entry_point_id,
                        source_param=sp.param_name,
                        source_type=sp.source_type,
                        propagation_steps=steps_fwd,
                        sink_call_site_id=sink.id,
                        confidence=min(
                            (s.confidence for s in steps_fwd),
                            default=0.9,
                        ),
                        notes="backward-anchored",
                    ))
```

改为(在 `steps_fwd` 末尾 append 该 sink 的 intra summary step,`to_param == sink.id`):

```python
                for sp in anchored:
                    steps_fwd = list(reversed(steps_rev))
                    # 合并 sink 所在函数的 intra summary step(携带 sanitizer/transformation)。
                    # 统一覆盖单函数(sink_step=0,跨函数 hop 为空)与多函数场景——
                    # 之前 local_steps 不被消费导致 sanitizer 管道断链。
                    sink_intra = intra_results.get(sid)
                    if sink_intra:
                        steps_fwd.extend(
                            s for s in sink_intra.local_steps if s.to_param == sink.id
                        )
                    flows.append(TaintFlow(
                        flow_id=f"{sp.entry_point_id}->{sink.id}",
                        entry_point_id=sp.entry_point_id,
                        source_param=sp.param_name,
                        source_type=sp.source_type,
                        propagation_steps=steps_fwd,
                        sink_call_site_id=sink.id,
                        confidence=min(
                            (s.confidence for s in steps_fwd),
                            default=0.9,
                        ),
                        notes="backward-anchored",
                    ))
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_chain_propagator_backward.py packages/core/tests/code_index/test_chain_propagator.py -v`
Expected: PASS(2 个新测试 + 既有 backward/forward 测试全绿)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/chain_propagator.py packages/core/tests/code_index/test_chain_propagator_backward.py
git commit -m "feat(code_index): propagate_backward 合并 intra local_steps(含单函数 sink_step=0 场景)"
```

---

## Task 4: direction_hint 标注修复 + _detect_post_sanitize_concat 识别 post_concat 标记

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/chain_verdict.py:46`(`_DIRECTION`)、`:141-156`(`_detect_post_sanitize_concat`)
- Test: `packages/core/tests/code_index/test_chain_verdict.py`(改 line 53 现有断言 + 加 post_concat 标记测试)

**Interfaces:**
- Consumes: 无新接口(独立小修)
- Produces: `extract_candidate_chains` 对 injection 返回 `direction_hint="backward"`;`_detect_post_sanitize_concat` 识别 summary step 的 `|post_concat` 标记。Task 6 的 prompt 经 `post_sanitize_concat` 字段消费。

- [ ] **Step 1: 写失败测试 + 更新现有断言**

`test_chain_verdict.py:45-53` 现有测试 `test_extract_injection_routes_sql_and_command_sinks` 断言 `direction_hint == "forward"`,改为 `backward`(因为实际链构造是 backward,见 spec §1.4):

```python
def test_extract_injection_routes_sql_and_command_sinks():
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value"), _flow("cmd_argument")],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="injection")
    assert len(chains) == 2
    assert all(c.vuln_class == "injection" for c in chains)
    assert all(c.direction_hint == "backward" for c in chains)   # was "forward"
```

追加新测试(识别 summary step 的 post_concat 标记):

```python
def test_post_sanitize_concat_detected_from_summary_step_marker():
    """summary step 的 transformation 含 |post_concat 标记 → post_sanitize_concat=True。

    覆盖 Task 2/3 产的 summary step 形态(sanitize_hint:<desc>|post_concat)。
    """
    steps = [_step("sanitize_hint:html.escape|post_concat")]
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value", steps=steps)],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="injection")
    assert len(chains) == 1
    assert chains[0].post_sanitize_concat is True
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_chain_verdict.py::test_extract_injection_routes_sql_and_command_sinks packages/core/tests/code_index/test_chain_verdict.py::test_post_sanitize_concat_detected_from_summary_step_marker -v`
Expected: FAIL(`direction_hint == "forward"` 当前成立但断言改 backward;post_concat 标记当前 `_detect_post_sanitize_concat` 不识别)

- [ ] **Step 3: 实现**

`chain_verdict.py:46` 改:

```python
_DIRECTION = {"injection": "backward", "xss": "backward", "ssrf": "backward"}
```

`chain_verdict.py:141-156` `_detect_post_sanitize_concat` 加 summary step 标记识别(保留原多 step 序列逻辑向后兼容):

```python
def _detect_post_sanitize_concat(steps: list[PropagationStep]) -> bool:
    """True if a sanitizer is followed by re-tainting concatenation.

    两种形态都认:
    1. summary step 编码标记(transformation 含 '|post_concat',由 _intra_result_from_llm 产)
    2. 多 step 序列: sanitize/escape/encode/quote step 后跟 concat step(原逻辑,向后兼容)
    """
    for s in steps:
        tf = (s.transformation or "").lower()
        if "post_concat" in tf:          # summary step 标记(Task 2/3 产物)
            return True
    seen_sanitizer = False
    for s in steps:
        tf = (s.transformation or "").lower()
        if "sanitize" in tf or "escape" in tf or "encode" in tf or "quote" in tf:
            seen_sanitizer = True
            continue
        if seen_sanitizer and tf == "concat":
            return True
    return False
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_chain_verdict.py -v`
Expected: PASS(改动测试 + 新测试 + 既有全绿;注意 `test_post_sanitize_concat_detected_when_concat_after_sanitizer`/`..._false_when_no_concat_after` 这两个原多 step 测试仍绿)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/chain_verdict.py packages/core/tests/code_index/test_chain_verdict.py
git commit -m "fix(code_index): direction_hint injection 改 backward + post_concat 标记识别"
```

---

## Task 5: CandidateChain.sink_expressions + extract 填充 + inj/ssrf builder 透传 sink_call_sites

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/chain_verdict.py:72-86`(CandidateChain)、`:159-208`(extract_candidate_chains)
- Modify: `packages/core/src/shannon_core/code_index/vuln_chain_builders/injection_builder.py`(签名 + 透传)、`ssrf_builder.py`(同)
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:1271-1273`(else 分支补传)
- Test: `packages/core/tests/code_index/test_chain_verdict.py`、`test_injection_builder.py`、`test_ssrf_builder.py`

**Interfaces:**
- Consumes: `SinkCallSite.dangerous_slots[].expression`(从 `code_index.json` 的 `sink_call_sites` 取,经 activity 透传)
- Produces: `CandidateChain.sink_expressions: list[str]`;inj/ssrf builder 接收 `sink_call_sites` 参数并透传给 `extract_candidate_chains`。Task 6 prompt 消费 `sink_expressions`。

- [ ] **Step 1: 写失败测试**

追加到 `test_chain_verdict.py`(需要带 `dangerous_slots` 的 sink,扩展现有 `_xss_sink` 风格):

```python
from shannon_core.code_index.parameter_models import DangerousSlot, SlotContext


def _slot_sink(sink_id, slot=SlotContext.SQL_VALUE, expr="req.query.q"):
    return SinkCallSite(
        id=sink_id, caller_id="app.py:handler", callee_name="execute",
        callee_receiver="db", category=SinkCategory.SQL, sink_subtype="sql_raw_query",
        file_path="app.py", line=5, column=10,
        dangerous_slots=[DangerousSlot(arg_index=0, slot=slot, expression=expr, is_entry_hint=True)],
        rule_id="py-sql-execute",
    )


def test_extract_fills_sink_expressions_from_dangerous_slots():
    """injection 路径:sink_call_sites 的 dangerous_slots.expression → CandidateChain.sink_expressions。"""
    sid = "app.py:handler:db.execute:5:0"
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value", sink_id=sid)],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(
        pgraph, vuln_class="injection",
        sink_call_sites={sid: _slot_sink(sid, SlotContext.SQL_VALUE, "q + suffix")},
    )
    assert len(chains) == 1
    assert chains[0].sink_expressions == ["q + suffix"]


def test_extract_sink_expressions_empty_when_no_sink_call_sites():
    """inj/ssrf 未传 sink_call_sites → sink_expressions 默认空(向后兼容,不报错)。"""
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value")],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="injection")   # 不传 sink_call_sites
    assert len(chains) == 1
    assert chains[0].sink_expressions == []
```

追加到 `test_injection_builder.py`(验证 builder 透传):

```python
@pytest.mark.asyncio
async def test_build_injection_forwards_sink_call_sites_to_expressions():
    """inj builder 接收 sink_call_sites → finding 的 sink expression 可达(经 CandidateChain)。"""
    from shannon_core.code_index.parameter_models import DangerousSlot, SlotContext, SinkCallSite, SinkCategory

    sid = "app.py:handler:db.execute:5:0"
    sink = SinkCallSite(
        id=sid, caller_id="app.py:handler", callee_name="execute", callee_receiver="db",
        category=SinkCategory.SQL, sink_subtype="sql_raw_query", file_path="app.py",
        line=5, column=10,
        dangerous_slots=[DangerousSlot(arg_index=0, slot=SlotContext.SQL_VALUE,
                                       expression="'SELECT * FROM t WHERE id=' + q", is_entry_hint=True)],
        rule_id="py-sql-execute",
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value")], language_coverage=["python"],
    )
    captured = {}

    async def fake_llm(prompt, **kw):
        captured["prompt"] = prompt
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db","mismatch_reason":"x","confidence":"high"}')

    await build_injection_findings(
        pgraph, llm_client=fake_llm, sink_call_sites={sid: sink})
    # builder 透传后,判定 LLM 的 prompt 应含 sink 实参表达式
    assert "'SELECT * FROM t WHERE id=' + q" in captured["prompt"]
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_chain_verdict.py::test_extract_fills_sink_expressions_from_dangerous_slots packages/core/tests/code_index/test_injection_builder.py::test_build_injection_forwards_sink_call_sites_to_expressions -v`
Expected: FAIL(`CandidateChain.sink_expressions` 不存在 / inj builder 不接收 `sink_call_sites`)

- [ ] **Step 3: 实现**

`chain_verdict.py:72-86` CandidateChain 加字段(在 `render_context` 后):

```python
    post_sanitize_concat: bool
    render_context: str = ""   # xss only; derived from SinkCallSite.sink_subtype
    sink_expressions: list[str] = []   # sink dangerous_slots 的实参源码表达式(供判定 LLM)
```

`chain_verdict.py:159-208` `extract_candidate_chains` 构造 `CandidateChain` 时填 `sink_expressions`。在 `chains.append(CandidateChain(...))` 处(chain_verdict.py:194-207)加:

```python
        # sink dangerous_slots 的实参表达式(inj/ssrf 也需 sink_call_sites 透传)
        sink_expressions: list[str] = []
        if sink_call_sites is not None:
            scs = sink_call_sites.get(flow.sink_call_site_id)
            if scs is not None:
                sink_expressions = [slot.expression for slot in scs.dangerous_slots if slot.expression]
        chains.append(CandidateChain(
            vuln_class=vuln_class,
            flow_id=flow.flow_id,
            entry_point_id=flow.entry_point_id,
            source_param=flow.source_param,
            source_type=_slot_value(flow.source_type),
            sink_call_site_id=flow.sink_call_site_id,
            sink_slot=slot_value,
            propagation_steps=list(flow.propagation_steps),
            sanitizer_annotations=annots,
            direction_hint=direction,
            post_sanitize_concat=_detect_post_sanitize_concat(flow.propagation_steps),
            render_context=render_context,
            sink_expressions=sink_expressions,
        ))
```

> 注:`injection`/`ssrf` 路径当前 `sink_call_sites` 参数为 `None`(见 `extract_candidate_chains` 签名 chain_verdict.py:163 `sink_call_sites: dict | None = None`),所以 `sink_expressions` 默认空,需 builder 透传(下文)。

`injection_builder.py` `build_injection_findings` 签名加 `sink_call_sites`,透传给 `extract_candidate_chains`:

```python
async def build_injection_findings(
    pgraph, *, llm_client, sink_call_sites=None, progress_cb=None,
):
    ...
    candidates = extract_candidate_chains(
        pgraph, vuln_class="injection", sink_call_sites=sink_call_sites,
    )
    ...
```

`ssrf_builder.py` 同样改造(`extract_candidate_chains(pgraph, vuln_class="ssrf", sink_call_sites=sink_call_sites)`)。

`activities.py:1261-1273` 的 else 分支(inj/ssrf)补传 `sink_call_sites`:

```python
            for vc, builder in (
                ("injection", build_injection_findings),
                ("xss", build_xss_findings),
                ("ssrf", build_ssrf_findings),
            ):
                try:
                    findings = await builder(pgraph, llm_client=llm,
                                             sink_call_sites=sink_call_sites,
                                             progress_cb=_chain_cb)
                except Exception as exc:
                    ...
```

(xss/inj/ssrf 三个统一传 `sink_call_sites`,去掉 if vc=="xss" 分支)

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_chain_verdict.py packages/core/tests/code_index/test_injection_builder.py packages/core/tests/code_index/test_ssrf_builder.py packages/core/tests/code_index/test_xss_builder.py -v`
Expected: PASS(新测试 + 既有全绿;xss 原本就传 `sink_call_sites`,行为不变)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/chain_verdict.py packages/core/src/shannon_core/code_index/vuln_chain_builders/injection_builder.py packages/core/src/shannon_core/code_index/vuln_chain_builders/ssrf_builder.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/core/tests/code_index/test_chain_verdict.py packages/core/tests/code_index/test_injection_builder.py packages/core/tests/code_index/test_ssrf_builder.py
git commit -m "feat(code_index): CandidateChain.sink_expressions + inj/ssrf builder 透传 sink_call_sites"
```

---

## Task 6: chain_verdict prompt 接入 sink_expressions + steps_repr 含 intermediate_vars

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/chain_verdict.py:49-69`(prompt 模板)、`:228-231`(steps_repr 格式)
- Test: `packages/core/tests/code_index/test_chain_verdict.py`

**Interfaces:**
- Consumes: Task 5 的 `CandidateChain.sink_expressions` + Task 1/2 的 `PropagationStep.intermediate_vars`
- Produces: `judge_chain_verdict` 的 prompt 含非空 `sink_expressions` + steps_repr 含 intermediate_vars。Task 7 端到端断言 prompt 含这些字段。

- [ ] **Step 1: 写失败测试**

追加到 `test_chain_verdict.py`:

```python
@pytest.mark.asyncio
async def test_judge_chain_verdict_prompt_includes_sink_expressions_and_intermediate_vars():
    """prompt 含 sink_expressions + steps_repr 含 intermediate_vars(判定信息密度)。"""
    chain = CandidateChain(
        vuln_class="injection", flow_id="f1", entry_point_id="ep",
        source_param="q", source_type="query", sink_call_site_id="db.execute:1",
        sink_slot="sql_value",
        propagation_steps=[PropagationStep(
            step_id="s1", from_func_id="f", from_param="q", to_func_id="f", to_param="sink",
            transformation="sanitize_hint:html.escape", code_location="app.py:5",
            intermediate_vars=["raw", "esc"],
        )],
        sanitizer_annotations=[], direction_hint="backward",
        post_sanitize_concat=False,
        sink_expressions=["'sel ' + q"],
    )
    captured = {}

    async def fake_llm(prompt, **kw):
        captured["prompt"] = prompt
        return '{"verdict":"safe","witness_payload":null,"evidence_chain":"q->db","mismatch_reason":null,"confidence":"high"}'

    await judge_chain_verdict(chain, llm_client=fake_llm)
    assert "'sel ' + q" in captured["prompt"]            # sink_expressions 进 prompt
    assert "raw" in captured["prompt"] and "esc" in captured["prompt"]   # intermediate_vars 进 steps_repr
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_chain_verdict.py::test_judge_chain_verdict_prompt_includes_sink_expressions_and_intermediate_vars -v`
Expected: FAIL(prompt 当前不含 `sink_expressions`/`intermediate_vars`)

- [ ] **Step 3: 实现**

`chain_verdict.py:49-69` prompt 模板加 `sink_expressions` 字段(在 `post-sanitize concatenation detected` 后):

```python
_VERDICT_PROMPT = """You are a lightweight chain-verdict pass for the {vuln_class} GitNexus track.
Given ONE candidate source->sink chain with deterministic sanitizer annotations,
judge ONLY whether it is vulnerable. Do NOT re-run full analysis methodology.

Candidate chain:
- source: {source_param} ({source_type})
- sink: {sink_call_site_id}
- slot/render_context: {sink_slot}
- sink arg expressions (source code reaching the dangerous slot): {sink_expressions}
- direction: {direction_hint}
- propagation steps: {steps_repr}
- sanitizer annotations (best-effort, NOT judged for effectiveness): {sanitizers_repr}
- post-sanitize concatenation detected: {post_sanitize_concat}

Rules:
- post-sanitize concatenation = sanitizer considered INEFFECTIVE (tainted again).
- A defense is effective ONLY if it matches the slot/render_context AND no concat after.
- Inspect sink arg expressions to judge whether the sanitizer actually covers the tainted segment.
- Be decisive: return vulnerable OR safe.

Respond with a compact JSON object ONLY:
{{"verdict":"safe|vulnerable","witness_payload":"<minimal>","evidence_chain":"<source->sink with sanitizer notes>","mismatch_reason":"<if vulnerable>","confidence":"high|medium|low"}}
"""
```

`chain_verdict.py:221-237` `judge_chain_verdict` 的 `prompt.format(...)` 加 `sink_expressions`,并把 `steps_repr` 扩展含 `intermediate_vars`:

```python
    prompt = _VERDICT_PROMPT.format(
        vuln_class=candidate.vuln_class,
        source_param=candidate.source_param,
        source_type=candidate.source_type,
        sink_call_site_id=candidate.sink_call_site_id,
        sink_slot=candidate.render_context or candidate.sink_slot,
        sink_expressions="; ".join(candidate.sink_expressions) or "(none)",
        direction_hint=candidate.direction_hint,
        steps_repr="; ".join(
            f"{s.code_location}:{s.transformation or 'noop'}"
            + (f"|vars={','.join(s.intermediate_vars)}" if s.intermediate_vars else "")
            for s in candidate.propagation_steps
        ) or "(none)",
        sanitizers_repr="; ".join(
            f"{a.defense_type}@{a.applies_to}({a.code_location})"
            for a in candidate.sanitizer_annotations
        ) or "(none)",
        post_sanitize_concat=str(candidate.post_sanitize_concat),
    )
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_chain_verdict.py -v`
Expected: PASS(新测试 + 既有全绿)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/chain_verdict.py packages/core/tests/code_index/test_chain_verdict.py
git commit -m "feat(code_index): chain_verdict prompt 接入 sink_expressions + intermediate_vars"
```

---

## Task 7: 端到端集成测试 + 防回退锚点

**Files:**
- Create: `packages/core/tests/code_index/test_sanitizer_pipeline_e2e.py`

**Interfaces:**
- Consumes: Task 1-6 全部产物
- Produces: 端到端集成测试(mock LLM 全链)+ 防回退锚点(防 `_intra_result_from_llm` 再次 `local_steps=[]` / propagator 再次不合并)。这是本 plan 的核心验收:证明 sanitizer 管道在生产路径(非手动构造 step)真正流通。

- [ ] **Step 1: 写集成测试**

创建 `packages/core/tests/code_index/test_sanitizer_pipeline_e2e.py`:

```python
"""端到端:sanitizer 管道接通验证(防测试绿生产空转)。

从 mock LLM 返回的 TaintAnalysisResult(含 sanitized/sanitizer_description/
intermediate_vars/post_sanitized_concat)出发,经 analyze_taint_llm →
propagate_backward_across_chains → extract_candidate_chains → judge_chain_verdict,
断言 sanitizer 信息整条管道流通(非手动构造 step)。
"""
import pytest

from shannon_core.code_index.llm_taint_analyzer import analyze_taint_llm
from shannon_core.code_index.chain_propagator import propagate_backward_across_chains
from shannon_core.code_index.chain_verdict import extract_candidate_chains, judge_chain_verdict
from shannon_core.code_index.models import FuncBlock, CallChain, ParameterSource
from shannon_core.code_index.parameter_models import (
    DangerousSlot, SinkCallSite, SinkCategory, SlotContext,
    TaintAnalysisResult, TaintPath,
)


SINK_ID = "app.py:handler:db.execute:12:0"


def _handler_block():
    return FuncBlock(
        id="app.py:handler", function_name="handler", file_path="app.py",
        start_line=10, end_line=20, parameters=["q"], source_code="def handler(q): db.execute(q)",
    )


def _sink():
    return SinkCallSite(
        id=SINK_ID, caller_id="app.py:handler", callee_name="execute", callee_receiver="db",
        category=SinkCategory.SQL, sink_subtype="sql_raw_query", file_path="app.py",
        line=12, column=8,
        dangerous_slots=[DangerousSlot(arg_index=0, slot=SlotContext.SQL_VALUE,
                                       expression="'sel ' + q", is_entry_hint=True)],
        rule_id="py-sql-execute",
    )


def _source():
    from shannon_core.code_index.parameter_models import SourcePoint
    return SourcePoint(
        id="app.py:handler::q::10", entry_point_id="app.py:handler", param_name="q",
        source_type=ParameterSource.QUERY_PARAM, expression="req.query.q",
        file_path="app.py", line=10,
    )


@pytest.mark.asyncio
async def test_sanitizer_pipeline_flows_end_to_end():
    """mock LLM 返回 sanitized=True → 全链 → chain_verdict prompt 含非空 sanitizer/expression。"""
    # 1. intra:mock LLM 返回带 sanitizer 的 TaintAnalysisResult
    async def taint_llm(prompt, **kw):
        import json
        return json.dumps({
            "tainted_params": ["q"],
            "propagation_paths": [{
                "source_param": "q", "sink_id": SINK_ID, "sink_arg_index": 0,
                "intermediate_vars": ["raw"], "sanitized": True,
                "sanitizer_description": "html.escape",
                "post_sanitized_concat": True, "confidence": 0.9,
            }],
        })

    intra = await analyze_taint_llm(
        _handler_block(), [_sink()], llm_client=taint_llm)

    # 防回退锚点 A:local_steps 非空(防 _intra_result_from_llm 再次丢弃)
    assert len(intra.local_steps) == 1, "intra 必须流出 summary step(断点 A 防回退)"
    assert "sanitize_hint:html.escape" in (intra.local_steps[0].transformation or "")

    # 2. propagate_backward(单函数场景)
    flows = propagate_backward_across_chains(
        chains=[CallChain(entry_point_id="app.py:handler", path=["app.py:handler"],
                          depth=0, has_unresolved=False)],
        blocks=[_handler_block()], intra_results={"app.py:handler": intra},
        sink_call_sites=[_sink()], source_points=[_source()],
    )
    assert len(flows) == 1
    # 防回退锚点 B:TaintFlow.propagation_steps 含 summary step(防 propagator 不合并)
    assert any(
        s.transformation and "sanitize_hint" in s.transformation
        for s in flows[0].propagation_steps
    ), "TaintFlow 必须含 intra summary step(断点 B 防回退)"

    # 3. extract + judge
    from shannon_core.code_index.parameter_models import ParameterPropagationGraph
    pgraph = ParameterPropagationGraph(taint_flows=flows, language_coverage=["python"])
    candidates = extract_candidate_chains(
        pgraph, vuln_class="injection", sink_call_sites={SINK_ID: _sink()})
    assert len(candidates) == 1
    c = candidates[0]
    assert c.sink_expressions == ["'sel ' + q"]              # expression 接入
    assert c.post_sanitize_concat is True                     # post_concat 标记识别
    assert c.sanitizer_annotations                            # annotate_sanitizers 匹配到(sanitize_library 不空转)

    captured = {}

    async def verdict_llm(prompt, **kw):
        captured["prompt"] = prompt
        import json
        return json.dumps({
            "verdict": "safe", "witness_payload": None,
            "evidence_chain": "q->db", "mismatch_reason": None, "confidence": "high",
        })

    verdict = await judge_chain_verdict(c, llm_client=verdict_llm)
    # 判定 LLM 拿到了完整信息(非空 sanitizer/expression/post_concat)
    assert "html.escape" in captured["prompt"]
    assert "'sel ' + q" in captured["prompt"]
    assert "raw" in captured["prompt"]
    assert "True" in captured["prompt"]   # post_sanitize_concat=True 进 prompt
    assert verdict.verdict == "safe"      # sanitizer 流通后 LLM 能判 safe(非机械 vulnerable)
```

- [ ] **Step 2: 跑测试验证**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_sanitizer_pipeline_e2e.py -v`
Expected: PASS(Task 1-6 已实现,管道已通)。若 FAIL,根据失败点定位是哪个断点未修好(锚点 A→Task 2、锚点 B→Task 3、expression→Task 5、prompt→Task 6)。

- [ ] **Step 3: 跑全部改动相关测试做整体回归**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_parameter_models_upgrade.py packages/core/tests/code_index/test_llm_taint_analyzer.py packages/core/tests/code_index/test_chain_propagator.py packages/core/tests/code_index/test_chain_propagator_backward.py packages/core/tests/code_index/test_chain_verdict.py packages/core/tests/code_index/test_sanitizer_library.py packages/core/tests/code_index/test_injection_builder.py packages/core/tests/code_index/test_ssrf_builder.py packages/core/tests/code_index/test_xss_builder.py packages/core/tests/code_index/test_sanitizer_pipeline_e2e.py -v`
Expected: PASS(全绿。若 `test_taint_persist_integration.py` 等未改动文件失败属 pre-existing,不在本 plan 范围,记录但不修)

- [ ] **Step 4: Commit**

```bash
git add packages/core/tests/code_index/test_sanitizer_pipeline_e2e.py
git commit -m "test(code_index): sanitizer 管道端到端集成测试 + 双断点防回退锚点"
```

---

## Self-Review

**1. Spec 覆盖**:11 个改动点全部映射到 task——#1→T2,#2→T2,#3→T1,#4→T1,#5→T3,#6→T4,#7→T4,#8→T5,#9→T5,#10→T6,#11→T5。无遗漏。端到端测试(T7)对应 spec §6.1。前置依赖(spec §8 chains>0)写入 Global Constraints,不属本 plan 验收(单元/集成层已证管道接通)。

**2. Placeholder 扫描**:无 TBD/TODO。所有 step 含完整代码。Task 3 的 `CallChain` 构造注明「先读 models.py 确认 path 字段名」——这是唯一一个需实现时核对的不确定点(已显式标注,非占位)。

**3. 类型一致性**:`PropagationStep.intermediate_vars`、`TaintPath.post_sanitized_concat`、`CandidateChain.sink_expressions` 三个新字段在定义(T1/T5)与消费(T2/T3/T6/T7)中名称、类型一致。`_intra_result_from_llm` 产的 summary step `transformation="sanitize_hint:<desc>[|post_concat]"` 格式在 T2(产)、T4(检测 post_concat)、T7(断言)一致。

**4. 现有测试回归**:Task 4 显式更新 `test_chain_verdict.py:53` 的 `direction_hint` 断言(forward→backward);Task 5 的 builder 签名加可选参数 `sink_call_sites=None`,既有 builder 测试不传也能跑(向后兼容)。
