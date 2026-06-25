# GitNexus intra-taint 确定性 fallback 改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `analyze_taint_llm` 的 LLM-失败 fallback 从「全参数 tainted + 全 sink 命中 1.0」改成用 `SinkCallSite.dangerous_slots[].is_entry_hint` 做确定性分层(直达 0.9 / 间接 0.5 / 字面量过滤),在不引 LLM 依赖、不损失召回的前提下提升 GitNexus 轨 `parameter_graph.json` 的精度。

**Architecture:** 仅改 `llm_taint_analyzer.py` 的 fallback 分支,新增两个模块私有函数(`_is_literal_expression` 纯 helper + `_deterministic_intra_fallback` 核心)。`tainted_params` 保守保留全参数(保 `propagate_across_chains` 的 chain seed + 跨函数传播不漏召回),只对 `hits` 做精度分层与字面量过滤。LLM 成功路径、propagate、builder、chain_verdict 一律不动。

**Tech Stack:** Python(pydantic v2 模型)、pytest + pytest-asyncio。对应 spec:`docs/superpowers/specs/2026-06-26-gitnexus-intra-taint-deterministic-fallback-design.md`。

## Global Constraints

- **只跑改动相关测试**:`pytest packages/core/tests/code_index/test_llm_taint_analyzer.py`、`test_chain_propagator.py`——勿跑全套/全包(CLAUDE.md 测试陷阱:全套会 hang 在 Temporal/网络慢测试)。
- **双轨铁律(CLAUDE.md §1)**:GitNexus 轨保持确定性,**不引 LLM 依赖**。本改动是确定性 fallback,不接真 LLM。
- **召回不漏**:`tainted_params` 必须 == `set(block.parameters)`(全参数),保 `propagate_across_chains:164` 的 seed 与跨函数传播。**禁止**缩小 `tainted_params`。
- **置信度常量**(spec §3.2):直达 hit = `0.9`;间接 hit = `0.5`;字面量 sink 不进 `hits`。
- **范围控制(spec §4)**:不改 `propagate_across_chains` / builder / `chain_verdict` / LLM prompt;不注册 `run_gitnexus_chain_verdict`(独立 follow-up)。
- pydantic v2 风格:`IntraResult(tainted_params=set(...), hits={...}, local_steps=[])`。

---

## File Structure

| 文件 | 责任 | 本计划改动 |
|---|---|---|
| `packages/core/src/shannon_core/code_index/llm_taint_analyzer.py` | 逐函数 taint 分析(LLM + fallback) | 新增 `_is_literal_expression`、`_deterministic_intra_fallback`;改 `analyze_taint_llm` fallback 分支(`:278-290`) |
| `packages/core/tests/code_index/test_llm_taint_analyzer.py` | 该模块单元测试 | 新增 `TestIsLiteralExpression`、`TestDeterministicIntraFallback`;更新 `test_llm_failure_fallback_*` 反映新语义 |
| `packages/core/tests/code_index/test_chain_propagator.py` | propagate 单元测试 | 新增 propagate 集成 smoke(验证新 IntraResult 能被消费) |

现有测试已有 `_block()`/`_sink()` factory 与 `FakeLLMClient`(`test_llm_taint_analyzer.py:23-68`),新测试复用它们。

---

## Task 1: `_is_literal_expression` helper

纯函数:保守判断一个 sink 实参表达式是否为字面量常量(明确非注入源)。是 `_deterministic_intra_fallback`(Task 2)的依赖。

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/llm_taint_analyzer.py`(在文件末尾、`analyze_taint_llm` 之前新增)
- Test: `packages/core/tests/code_index/test_llm_taint_analyzer.py`(新增 `TestIsLiteralExpression`)

**Interfaces:**
- Produces: `_is_literal_expression(expr: str) -> bool`

- [ ] **Step 1: 写失败测试**

在 `test_llm_taint_analyzer.py` 顶部 import 块(`:15-20`)追加 `_is_literal_expression`:

```python
from shannon_core.code_index.llm_taint_analyzer import (
    _is_literal_expression,
    analyze_taint_llm,
    build_taint_prompt,
    parse_llm_response,
    truncate_source,
)
```

在文件末尾追加测试类:

```python
class TestIsLiteralExpression:
    def test_quoted_string(self):
        assert _is_literal_expression("'SELECT * FROM users'") is True
        assert _is_literal_expression('"hello"') is True

    def test_integer(self):
        assert _is_literal_expression("42") is True
        assert _is_literal_expression("-7") is True
        assert _is_literal_expression("+3") is True

    def test_float(self):
        assert _is_literal_expression("3.14") is True
        assert _is_literal_expression("-0.5") is True

    def test_boolean_and_null(self):
        for lit in ("true", "false", "null", "None", "True", "False"):
            assert _is_literal_expression(lit) is True

    def test_empty(self):
        assert _is_literal_expression("") is True
        assert _is_literal_expression("   ") is True

    def test_variable_is_not_literal(self):
        assert _is_literal_expression("user_input") is False
        assert _is_literal_expression("processed") is False
        assert _is_literal_expression("request.body") is False
        assert _is_literal_expression("data.x") is False
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest packages/core/tests/code_index/test_llm_taint_analyzer.py::TestIsLiteralExpression -v`
Expected: FAIL — `ImportError: cannot import name '_is_literal_expression'`

- [ ] **Step 3: 实现 helper**

在 `llm_taint_analyzer.py` 的 `analyze_taint_llm` 函数定义之前(约 `:228` `# 5. Main entry point` 注释块之前)插入:

```python
# ---------------------------------------------------------------------------
# 4b. Deterministic intra fallback helpers (spec 改动: 立场 B)
# ---------------------------------------------------------------------------

def _is_literal_expression(expr: str) -> bool:
    """保守判断 expression 是否为字面量常量(明确非注入源)。

    仅认明确字面量形态(引号字符串 / 数字 / 布尔 / null / 空);任何变量、
    属性访问、表达式返回 False(留给 is_entry_hint / LLM 判断)。
    """
    e = expr.strip()
    if not e:
        return True
    # 引号字符串
    if len(e) >= 2 and e[0] in "\"'" and e[-1] == e[0]:
        return True
    # 数字(整数 / 浮点,含正负号)
    cleaned = e.lstrip("+-")
    if cleaned.isdigit():
        return True
    if cleaned.count(".") == 1 and cleaned.replace(".", "", 1).isdigit():
        return True
    # 布尔 / 空常量
    return e in {"true", "false", "null", "None", "True", "False"}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest packages/core/tests/code_index/test_llm_taint_analyzer.py::TestIsLiteralExpression -v`
Expected: PASS(6 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/llm_taint_analyzer.py packages/core/tests/code_index/test_llm_taint_analyzer.py
git commit -m "feat(code_index): add _is_literal_expression helper for deterministic intra fallback (spec 立场 B, Task 1)"
```

---

## Task 2: `_deterministic_intra_fallback` 核心函数

用 `SinkCallSite.dangerous_slots[].is_entry_hint` 给 sink 命中分层,字面量 sink 过滤。`tainted_params` 保守全保。

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/llm_taint_analyzer.py`(紧接 Task 1 的 helper 之后新增)
- Test: `packages/core/tests/code_index/test_llm_taint_analyzer.py`(新增 `TestDeterministicIntraFallback` + `_sink_hint` helper)

**Interfaces:**
- Consumes: `_is_literal_expression`(Task 1);`FuncBlock.parameters`;`SinkCallSite.dangerous_slots`(每个 `DangerousSlot` 有 `.is_entry_hint: bool`、`.expression: str`);`IntraResult`
- Produces: `_deterministic_intra_fallback(block: FuncBlock, sinks_in_func: list[SinkCallSite]) -> IntraResult`

- [ ] **Step 1: 写失败测试**

在 `test_llm_taint_analyzer.py` 顶部 import 追加 `_deterministic_intra_fallback`(与 Task 1 的 import 块合并):

```python
from shannon_core.code_index.llm_taint_analyzer import (
    _deterministic_intra_fallback,
    _is_literal_expression,
    analyze_taint_llm,
    build_taint_prompt,
    parse_llm_response,
    truncate_source,
)
```

在文件末尾追加 helper + 测试类:

```python
def _sink_hint(
    func_id: str, expression: str, is_hint: bool, sink_id: str = "sink_1",
) -> SinkCallSite:
    """构造带指定 dangerous_slot(is_entry_hint/expression)的 sink。"""
    return SinkCallSite(
        id=sink_id,
        caller_id=func_id,
        callee_name="cursor.execute",
        callee_receiver="cursor",
        category=SinkCategory.SQL,
        sink_subtype="execute",
        file_path="app.py",
        line=4,
        column=0,
        dangerous_slots=[DangerousSlot(
            arg_index=0, slot=SlotContext.SQL_VALUE,
            expression=expression, is_entry_hint=is_hint,
        )],
        rule_id="sql-execute",
        needs_review=False,
    )


class TestDeterministicIntraFallback:
    def test_direct_param_sink_high_confidence(self):
        block = _block(params=["user_input"])
        sink = _sink_hint(block.id, "user_input", is_hint=True)
        result = _deterministic_intra_fallback(block, [sink])
        assert result.hits["sink_1"] == 0.9

    def test_request_object_sink_high_confidence(self):
        block = _block(params=[])
        sink = _sink_hint(block.id, "request.body", is_hint=True)
        result = _deterministic_intra_fallback(block, [sink])
        assert result.hits["sink_1"] == 0.9

    def test_local_var_sink_low_confidence(self):
        block = _block(params=["user_input"])
        sink = _sink_hint(block.id, "processed", is_hint=False)
        result = _deterministic_intra_fallback(block, [sink])
        assert result.hits["sink_1"] == 0.5

    def test_literal_sink_filtered_out(self):
        block = _block(params=[])
        sink = _sink_hint(block.id, "'SELECT * FROM users'", is_hint=False)
        result = _deterministic_intra_fallback(block, [sink])
        assert "sink_1" not in result.hits

    def test_preserves_all_tainted_params(self):
        block = _block(params=["user_input", "config", "limit"])
        sink = _sink_hint(block.id, "user_input", is_hint=True)
        result = _deterministic_intra_fallback(block, [sink])
        assert result.tainted_params == {"user_input", "config", "limit"}

    def test_empty_sinks_returns_empty_hits(self):
        block = _block(params=["user_input"])
        result = _deterministic_intra_fallback(block, [])
        assert result.hits == {}
        assert result.tainted_params == {"user_input"}
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest packages/core/tests/code_index/test_llm_taint_analyzer.py::TestDeterministicIntraFallback -v`
Expected: FAIL — `ImportError: cannot import name '_deterministic_intra_fallback'`

- [ ] **Step 3: 实现核心函数**

在 `llm_taint_analyzer.py` 的 `_is_literal_expression` 之后(`analyze_taint_llm` 之前)插入:

```python
# 置信度分层(spec §3.2):直达参数→sink 用 AST 浅判断(is_entry_hint)确认;
# 间接/未跟踪流给低置信,留给 LLM 或 LLM vuln 轨复核。
_DIRECT_HIT_CONFIDENCE = 0.9
_INDIRECT_HIT_CONFIDENCE = 0.5


def _deterministic_intra_fallback(
    block: FuncBlock,
    sinks_in_func: list[SinkCallSite],
) -> IntraResult:
    """LLM 不可用时的确定性 intra 判断(spec 改动: 立场 B)。

    用 SinkCallSite.dangerous_slots[].is_entry_hint(AST 浅判断)给 sink 命中分层,
    并过滤纯字面量 sink:
      - 任一 slot is_entry_hint=True  → hits[sink.id] = 0.9(直达)
      - 否则若全部 slot 为字面量      → 不进 hits(过滤常量 sink,降噪)
      - 否则(变量引用,非直达)       → hits[sink.id] = 0.5(间接,需复核)

    tainted_params 保守保留全部参数 —— 保 propagate_across_chains 的 chain seed
    与跨函数传播,不损失召回(双轨铁律:GitNexus 轨确定性补召回)。
    """
    hits: dict[str, float] = {}
    for sink in sinks_in_func:
        slots = sink.dangerous_slots
        if any(slot.is_entry_hint for slot in slots):
            hits[sink.id] = _DIRECT_HIT_CONFIDENCE
            continue
        if slots and all(_is_literal_expression(slot.expression) for slot in slots):
            continue  # 纯字面量 sink: 明确非注入源,过滤
        hits[sink.id] = _INDIRECT_HIT_CONFIDENCE  # 间接 / 未跟踪
    return IntraResult(
        tainted_params=set(block.parameters),
        hits=hits,
        local_steps=[],
    )
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest packages/core/tests/code_index/test_llm_taint_analyzer.py::TestDeterministicIntraFallback -v`
Expected: PASS(6 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/llm_taint_analyzer.py packages/core/tests/code_index/test_llm_taint_analyzer.py
git commit -m "feat(code_index): add _deterministic_intra_fallback (is_entry_hint tiered hits) (spec 立场 B, Task 2)"
```

---

## Task 3: 接入 `analyze_taint_llm` fallback + 更新现有测试

把 `analyze_taint_llm` 的 fallback 分支(`:278-290`)从「全标」改为调用 `_deterministic_intra_fallback`,并更新断言旧「全标」行为的测试反映新语义。

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/llm_taint_analyzer.py:278-290`(fallback 分支)
- Modify: `packages/core/tests/code_index/test_llm_taint_analyzer.py:198-209`(更新 `test_llm_failure_fallback_marks_all_sinks_as_hits`)

**Interfaces:**
- Consumes: `_deterministic_intra_fallback`(Task 2)

- [ ] **Step 1: 先改测试反映新预期(TDD:先红)**

把 `test_llm_taint_analyzer.py:198-209` 的测试整体替换为(改名 + 新断言;注意 `_sink()` 默认 `expression="query"`、`is_entry_hint=False`,即「非字面量变量」→ 新逻辑给 0.5):

```python
    @pytest.mark.asyncio
    async def test_llm_failure_fallback_tiers_sink_hits(self):
        """spec 改动: LLM 失败时走确定性 fallback。_sink() 默认
        expression="query" / is_entry_hint=False → 非字面量变量 → hits @ 0.5
        (不再是旧的 1.0 全标);tainted_params 保守全保。"""
        block = _block(params=["user_input"])
        sink = _sink(block.id, sink_id="sink_1")
        llm_client = FakeLLMClient(response=None)  # raises
        result = await analyze_taint_llm(
            block=block, sinks_in_func=[sink], llm_client=llm_client,
        )
        assert result.tainted_params == {"user_input"}      # 全参数保守
        assert result.hits["sink_1"] == 0.5                 # 非字面量变量 → 0.5
```

- [ ] **Step 2: 跑该测试验证失败**

Run: `pytest packages/core/tests/code_index/test_llm_taint_analyzer.py::TestAnalyzeTaintLLM::test_llm_failure_fallback_tiers_sink_hits -v`
Expected: FAIL — `assert 1.0 == 0.5`(旧 fallback 仍返回 1.0)

- [ ] **Step 3: 改 `analyze_taint_llm` fallback 分支**

把 `llm_taint_analyzer.py:278-290` 的:

```python
    # Conservative fallback: mark all params tainted AND every sink in the
    # function as hit (over-approximate — report rather than miss), so
    # propagate_across_chains can emit flows without real LLM analysis.
    logger.warning(
        "LLM taint analysis failed for %s (last error: %s). "
        "Using conservative fallback: all params tainted, all sinks hit.",
        block.id, last_exc,
    )
    return IntraResult(
        tainted_params=set(block.parameters),
        hits={s.id: 1.0 for s in sinks_in_func},
        local_steps=[],
    )
```

替换为:

```python
    # Deterministic fallback (spec 改动: 立场 B): use is_entry_hint to tier
    # sink hits and filter literal sinks, instead of bluntly marking all
    # params tainted and all sinks hit at 1.0. tainted_params stays
    # conservative (all params) to preserve propagate seed / cross-function
    # propagation — no recall loss.
    logger.warning(
        "LLM taint analysis failed for %s (last error: %s). "
        "Using deterministic fallback (is_entry_hint tiered hits).",
        block.id, last_exc,
    )
    return _deterministic_intra_fallback(block, sinks_in_func)
```

- [ ] **Step 4: 跑整个测试文件回归**

Run: `pytest packages/core/tests/code_index/test_llm_taint_analyzer.py -v`
Expected: PASS —— 全部通过,含:
- `TestAnalyzeTaintLLM::test_returns_intra_result_with_hits`(LLM 成功路径,不变)
- `test_llm_failure_returns_conservative`(`sinks_in_func=[]`,tainted_params 全参数仍成立)
- `test_llm_failure_fallback_tiers_sink_hits`(新断言 0.5)
- `TestIsLiteralExpression`、`TestDeterministicIntraFallback`(Task 1/2)
- 其它现有测试不破

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/llm_taint_analyzer.py packages/core/tests/code_index/test_llm_taint_analyzer.py
git commit -m "refactor(code_index): wire deterministic fallback into analyze_taint_llm (spec 立场 B, Task 3)"
```

---

## Task 4: propagate 集成 smoke(契约不破坏验证)

验证 Task 2 产出的 `IntraResult` 能被 `propagate_across_chains` 正确消费、emit `TaintFlow`,且分层 confidence 传入 `TaintFlow.confidence`。这是 spec §3.4 契约的实证回归保护。

**Files:**
- Test: `packages/core/tests/code_index/test_chain_propagator.py`(新增集成测试,复用其 `_block`/`_sink` factory)

**Interfaces:**
- Consumes: `_deterministic_intra_fallback`(Task 2);`propagate_across_chains(chains, blocks, intra_results) -> list[TaintFlow]`(现有)

- [ ] **Step 1: 写集成测试**

在 `test_chain_propagator.py` 顶部 import 块追加:

```python
from shannon_core.code_index.llm_taint_analyzer import _deterministic_intra_fallback
from shannon_core.code_index.parameter_models import TaintFlow
```

在文件末尾追加测试类(复用该文件已有的 `_block` / `_sink` factory):

```python
class TestDeterministicFallbackPropagationSmoke:
    """spec §3.4: _deterministic_intra_fallback 产出的 IntraResult 必须能被
    propagate_across_chains 消费、emit TaintFlow,契约不破坏。"""

    def test_direct_hit_propagates_with_high_confidence(self):
        # head(entry point) 有参数;sink 在同一函数,dangerous_slot 直达参数。
        handler = _block("handler", "app.py", 1, params=["user_input"])
        # 用直达 slot 覆盖默认 _sink(expression="query", is_entry_hint=False)
        direct_sink = SinkCallSite(
            id="sink_direct",
            caller_id=handler.id,
            callee_name="cursor.execute",
            callee_receiver="cursor",
            category=SinkCategory.SQL,
            sink_subtype="execute",
            file_path="app.py",
            line=5,
            column=0,
            dangerous_slots=[DangerousSlot(
                arg_index=0, slot=SlotContext.SQL_VALUE,
                expression="user_input", is_entry_hint=True,
            )],
            rule_id="sql-execute",
            needs_review=False,
        )
        intra = {
            handler.id: _deterministic_intra_fallback(handler, [direct_sink]),
        }
        chains = [CallChain(
            entry_point_id=handler.id, path=[handler.id],
            depth=0, has_unresolved=False,
        )]
        flows = propagate_across_chains(chains=chains, blocks=[handler], intra_results=intra)
        assert len(flows) == 1
        assert isinstance(flows[0], TaintFlow)
        assert flows[0].sink_call_site_id == "sink_direct"
        assert flows[0].confidence == 0.9  # 直达分层传入 TaintFlow.confidence

    def test_literal_sink_produces_no_flow(self):
        # 字面量 sink 被过滤 → hits 空 → 不 emit flow(但 chain 仍 seed,不崩)。
        handler = _block("handler", "app.py", 1, params=["user_input"])
        literal_sink = SinkCallSite(
            id="sink_literal",
            caller_id=handler.id,
            callee_name="cursor.execute",
            callee_receiver="cursor",
            category=SinkCategory.SQL,
            sink_subtype="execute",
            file_path="app.py",
            line=5,
            column=0,
            dangerous_slots=[DangerousSlot(
                arg_index=0, slot=SlotContext.SQL_VALUE,
                expression="'SELECT 1'", is_entry_hint=False,
            )],
            rule_id="sql-execute",
            needs_review=False,
        )
        intra = {
            handler.id: _deterministic_intra_fallback(handler, [literal_sink]),
        }
        chains = [CallChain(
            entry_point_id=handler.id, path=[handler.id],
            depth=0, has_unresolved=False,
        )]
        flows = propagate_across_chains(chains=chains, blocks=[handler], intra_results=intra)
        assert flows == []  # 字面量 sink 过滤 → 无 flow
```

- [ ] **Step 2: 跑测试验证通过(契约 smoke)**

Run: `pytest packages/core/tests/code_index/test_chain_propagator.py::TestDeterministicFallbackPropagationSmoke -v`
Expected: PASS(2 passed)。这验证新 fallback 不破坏 propagate 契约:直达 sink emit flow @ 0.9,字面量 sink 无 flow。

> 若 FAIL:检查 `_deterministic_intra_fallback` 是否正确填 `tainted_params`(非空,保 seed)与 `hits`(直达进、字面量不进);`CallChain` 字段名是否与 `models.py` 一致(`entry_point_id`/`path`/`depth`/`has_unresolved`)。

- [ ] **Step 3: 跑两个相关测试文件做最终回归**

Run: `pytest packages/core/tests/code_index/test_llm_taint_analyzer.py packages/core/tests/code_index/test_chain_propagator.py -v`
Expected: 全部 PASS。

- [ ] **Step 4: Commit**

```bash
git add packages/core/tests/code_index/test_chain_propagator.py
git commit -m "test(code_index): propagate integration smoke for deterministic intra fallback (spec 立场 B, Task 4)"
```

---

## Self-Review

**1. Spec coverage:**
- §2 评估结论(不接 LLM)→ 全计划无 LLM 接线,符合。✓
- §3.1 改造范围(仅 fallback 分支)→ Task 3 只改 `:278-290`。✓
- §3.2 新 fallback 逻辑(三层 + tainted_params 全保)→ Task 2 实现 + 6 测试覆盖。✓
- §3.3 字面量 helper → Task 1。✓
- §3.4 propagate 契约验证 → Task 4 smoke。✓
- §4 范围控制(不改 propagate/builder/chain_verdict/不注册 activity)→ 全计划遵守。✓
- §5 测试 1-7 → Task 2(场景 1-5)、Task 3(场景 6 LLM 成功回归)、Task 4(场景 7 propagate smoke)全覆盖。✓

**2. Placeholder scan:** 无 TBD/TODO/"add error handling"/"similar to"。所有代码步骤含完整可执行代码。✓

**3. Type consistency:**
- `_is_literal_expression(expr: str) -> bool`:Task 1 定义,Task 2 消费,签名一致。✓
- `_deterministic_intra_fallback(block, sinks_in_func) -> IntraResult`:Task 2 定义,Task 3/4 消费,签名一致。✓
- 置信度常量 `0.9`/`0.5`:Task 2 实现、Task 2 测试、Task 3 测试、Task 4 测试全部一致。✓
- `IntraResult(tainted_params=set, hits=dict, local_steps=[])`:Task 2 产出,与 `parameter_models.py:113` 一致。✓
- `SinkCallSite.dangerous_slots[].is_entry_hint/expression`:Task 2/4 构造与 `parameter_models.py:124-129` 一致。✓
- `CallChain(entry_point_id/path/depth/has_unresolved)`:Task 4 与 `test_chain_propagator.py:86-90` 现有构造一致。✓

无问题。
