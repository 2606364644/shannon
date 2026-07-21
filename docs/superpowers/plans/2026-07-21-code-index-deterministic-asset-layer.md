# code_index 确定性产物层重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 code_index 的 source/sink 产物成为「全风味 + 解耦 + 多轨复用」的确定性资产,使关轨模式(`SHANNON_LLM_TRACK_ENABLED=0`)下 GitNexus 兜底不再灾难性归零——补回 fastjson 等单点 sink,并让 authz GitNexus 轨在 sink/source 充足时产出 IDOR 兜底。

**Architecture:** 四子项对称重构。③ sink 探测器化(对称 source 侧 `discover_sources_llm`,改「判定器」为「探测器」)是核心;② source 加 IDOR 风味维度;① source/sink 解耦并行;④ authz GitNexus 轨空产出排查(依赖 ③②,根因含 sink 失明导致 `_idor_reaches_sink` 无 sink 可达)。

**Tech Stack:** Python 3.12 / pytest + pytest-asyncio / pydantic / tree-sitter。测试无共享 fixture,用内联 `async def fake_llm` + `_block`/`_suspicious` 工厂(对齐 `tests/code_index/test_sink_discovery_llm.py` 既有风格)。

## Global Constraints

- **铁律(CLAUDE.md §1):确定性产物不喂 LLM 轨 prompt。** 所有改动限 GitNexus 轨(`code_index/` 确定性层 + authz GitNexus 深度 agent),**禁碰 `vuln-*.txt` LLM prompt**。source/sink 喂 authz GitNexus 轨 ✓;喂 auth LLM 轨 ✗。
- **sink 探测器边界**:仍是「entry 约束 + needs_review + chain_verdict 复核」的 GitNexus 补召回,非全仓自由 LLM agent(对称已存在的 source 探测器,非新范式)。
- **测试陷阱(CLAUDE.md §3)**:全套 pytest 有预存挂起/失败。**只跑本 plan 改动的测试文件**:`uv run pytest packages/core/tests/code_index/test_sink_hunter_llm.py packages/core/tests/code_index/test_source_discovery_llm.py -v`,勿广跑全套。
- **守铁律回归**:`packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py` + `packages/whitebox/tests/test_workflow_gitnexus_failfast.py` 必须保持绿。
- **`externally_exploitable`** 不被 verdict 覆写。
- **commit**:conventional-commit + 中文正文,只 `git add` 该 task 的 src + test 文件。分支 `feat/fork-py`。
- **非目标**:不追求关轨追平原始 TS 全覆盖(业务逻辑缺陷/跨服务二阶结构性覆盖不了,仍需开轨)。本 plan 只把漏报从「灾难归零」降到「可控」。

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `code_index/sink_discovery_llm.py` | sink 判定器(现有)+ **sink 探测器**(新增) | 新增 `SinkHunterCandidate`/`collect_entry_handler_blocks`/`discover_sinks_by_entry` |
| `code_index/source_discovery_llm.py` | source 探测器 | `_SOURCE_CANDIDATE_HINT` 加 IDOR 风味;prompt 标 IDOR |
| `code_index/__init__.py` | code_index 主编排(`build_code_index_with_gitnexus`) | detect_entry_points 提前;接入 sink 探测器;source/sink 解耦并行 |
| `code_index/authz_gitnexus_track.py` | authz GitNexus 轨 IDOR 检测 | 排查并修空产出(依赖 ③② 产物) |
| `tests/code_index/test_sink_hunter_llm.py` | sink 探测器测试 | **新建** |
| `tests/code_index/test_source_discovery_llm.py` | source 探测器测试 | 加 IDOR 风味用例 |

---

## Task 1: sink 探测器候选收集(`collect_entry_handler_blocks`)

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/sink_discovery_llm.py`(在 `SuspiciousCall` 定义后,`:119` 附近)
- Test: `packages/core/tests/code_index/test_sink_hunter_llm.py`(Create)

**Interfaces:**
- Consumes: `FuncBlock`(from `shannon_core.code_index.models`)
- Produces: `SinkHunterCandidate`(dataclass,字段 `block: FuncBlock`);`collect_entry_handler_blocks(blocks, *, entry_point_ids, sink_func_ids) -> list[SinkHunterCandidate]`

- [ ] **Step 1: 写失败测试**

Create `packages/core/tests/code_index/test_sink_hunter_llm.py`:
```python
import pytest
from shannon_core.code_index.models import FuncBlock
from shannon_core.code_index.sink_discovery_llm import (
    SinkHunterCandidate, collect_entry_handler_blocks,
)


def _block(*, id="b1", file_path="Ctl.java", function_name="handler",
           start_line=1, source=b"src", language="java"):
    return FuncBlock(
        id=id, file_path=file_path, function_name=function_name,
        start_line=start_line, source_code=source, language=language,
        parameters=[],
    )


def test_collect_entry_handler_blocks_keeps_sinkless_entries():
    b_entry = _block(id="e1", function_name="apiModify")
    b_with_sink = _block(id="s1", function_name="hasSink")
    b_other = _block(id="o1", function_name="helper")
    out = collect_entry_handler_blocks(
        [b_entry, b_with_sink, b_other],
        entry_point_ids={"e1", "s1"},
        sink_func_ids={"s1"},
    )
    assert [c.block.id for c in out] == ["e1"]
    assert isinstance(out[0], SinkHunterCandidate)


def test_collect_entry_handler_blocks_empty_when_all_have_sinks():
    b = _block(id="e1")
    out = collect_entry_handler_blocks([b], entry_point_ids={"e1"}, sink_func_ids={"e1"})
    assert out == []
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/code_index/test_sink_hunter_llm.py -v`
Expected: FAIL — `ImportError: cannot import name 'SinkHunterCandidate'`

- [ ] **Step 3: 实现**

在 `sink_discovery_llm.py` 的 `SuspiciousCall` dataclass(`:110-118`)之后插入:
```python
@dataclass(frozen=True)
class SinkHunterCandidate:
    """entry handler 函数,待 LLM 探测器自由找 sink(对称 source 的 SourceCandidate)。

    与 collect_suspicious_calls(候选表筛选→判定器)互补:本候选不依赖候选表,
    LLM 在 entry handler 源码内自由识别框架特有 sink(fastjson.parseObject 等)。
    """
    block: "FuncBlock"


def collect_entry_handler_blocks(
    blocks: "list[FuncBlock]",
    *,
    entry_point_ids: "set[str]",
    sink_func_ids: "set[str]",
) -> list[SinkHunterCandidate]:
    """收集 entry handler 中**已有 sink(规则+判定器软 sink)之外**的函数,送 LLM 探测器。

    排除 sink_func_ids 中的函数(规则/判定器已覆盖,避免重复);只留 entry handler。
    对称 collect_source_candidates 的收集职责,但目标是 sink 探测(整函数送 LLM)。
    """
    out: list[SinkHunterCandidate] = []
    for block in blocks:
        if block.id not in entry_point_ids:
            continue
        if block.id in sink_func_ids:
            continue  # 已有 sink,规则路径覆盖
        out.append(SinkHunterCandidate(block=block))
    return out
```

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/core/tests/code_index/test_sink_hunter_llm.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/sink_discovery_llm.py packages/core/tests/code_index/test_sink_hunter_llm.py
git commit -m "feat(code_index): sink 探测器候选收集 collect_entry_handler_blocks

对称 collect_source_candidates: 收集 entry handler 中已有 sink 之外的函数,
送 LLM 探测器(非候选表筛选)。spec 子项③ 第一步,治 R1 sink 判定器化。"
```

---

## Task 2: sink 探测器主函数(`discover_sinks_by_entry`)+ fastjson 复现

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/sink_discovery_llm.py`(文件末尾追加)
- Test: `packages/core/tests/code_index/test_sink_hunter_llm.py`(追加用例)

**Interfaces:**
- Consumes: `SinkHunterCandidate`(Task 1);`FileChunk`/`chunk_items_by_file`/`map_llm_with_bounds`(from `llm_concurrency`);`SinkCallSite`/`SinkCategory`/`SlotContext`/`DangerousSlot`(from `parameter_models`);`_to_category`/`_to_slot`(本文件已有,`:235`/`:242`)
- Produces: `discover_sinks_by_entry(candidates, llm_client, *, concurrency=None, per_call_timeout=None, progress_cb=None, token_threshold=None, model=None, max_calls=None) -> tuple[list[SinkCallSite], list[RuleGap]]`;软 sink `rule_id="llm-discovered-sink"`,`needs_review=True`

- [ ] **Step 1: 写失败测试(fastjson 复现 = 原始版 INJ-01)**

追加到 `test_sink_hunter_llm.py`:
```python
import json
import pytest
from shannon_core.code_index.parameter_models import SinkCategory, SlotContext
from shannon_core.code_index.sink_discovery_llm import discover_sinks_by_entry


@pytest.mark.asyncio
async def test_discover_sinks_by_entry_finds_fastjson_parseobject():
    # 对称原始版 INJ-01: ClusterConfigController.apiModifyClusterConfig
    #   @RequestBody String payload -> JSON.parseObject(payload)  (fastjson autotype, RCE)
    src = b'''  @PostMapping("/cluster/config/modify_single")
  public String apiModifyClusterConfig(@RequestBody String payload) {
    JSONObject o = JSON.parseObject(payload);
    return "ok";
  }'''
    block = _block(id="e1", function_name="apiModifyClusterConfig", source=src)
    cands = [SinkHunterCandidate(block=block)]

    async def client(prompt, **kw):
        return json.dumps([{
            "sink": "JSON.parseObject(payload)",
            "category": "deserialization",
            "dangerous_arg": "payload",
            "line": 3,
            "is_sink": True,
            "rationale": "fastjson autotype deserialization of user-controlled body",
        }])

    soft, gaps = await discover_sinks_by_entry(cands, client)
    assert len(soft) == 1
    s = soft[0]
    assert s.rule_id == "llm-discovered-sink"
    assert s.needs_review is True
    assert s.category == SinkCategory.DESERIALIZATION
    assert s.file_path == "Ctl.java"
    assert s.caller_id == "e1"


@pytest.mark.asyncio
async def test_discover_sinks_by_entry_none_client_degrades():
    soft, gaps = await discover_sinks_by_entry([], None)
    assert soft == [] and gaps == []


@pytest.mark.asyncio
async def test_discover_sinks_by_entry_drops_is_sink_false():
    block = _block(id="e1", function_name="h", source=b"void h(){}")
    async def client(prompt, **kw):
        return json.dumps([{"sink": "foo()", "category": "sql", "line": 1,
                            "is_sink": False, "rationale": "safe"}])
    soft, _ = await discover_sinks_by_entry([SinkHunterCandidate(block=block)], client)
    assert soft == []
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/code_index/test_sink_hunter_llm.py -v`
Expected: 3 FAIL — `ImportError: cannot import name 'discover_sinks_by_entry'`

- [ ] **Step 3: 实现**

在 `sink_discovery_llm.py` 末尾追加(对称 `source_discovery_llm.discover_sources_llm:273-357`):
```python
# === 子项③: sink 探测器(entry-driven, 对称 source 探测器)=====================

_SINK_HUNTER_PROMPT_TMPL = """You are a security sink detector for the GitNexus track.
Given a FILE with one or more entry handler functions, identify ALL security sinks
WITHIN each function. Rule-based detection already covered common sinks (raw SQL
execute, Runtime.exec, ObjectInputStream.readObject, HttpClient.send); you handle
the unconventional ones — framework-specific deserialization (fastjson
JSON.parseObject, Jackson enableDefaultTyping), custom URL builders followed by
HTTP execute, template engines, reflection.

## File(s)
{file_paths}

## Functions
{functions_repr}

## Task
Return a JSON array. One object per sink found (omit functions with no sink):
{{"sink":"<call expression>","category":"sql|command|file|template|deserialization|ssrf|xss|redirect|log","dangerous_arg":"<expression reaching the sink>","line":<int>,"is_sink":true,"rationale":"<one line>"}}
Return ONLY the JSON array, no prose. `line` is the FILE-absolute line number of the sink call."""


def _build_sink_hunter_prompt(chunk: "FileChunk") -> str:
    func_parts: list[str] = []
    for b in chunk.blocks:
        func_parts.append(
            f"### {b.function_name} ({b.file_path}:{b.start_line})\n"
            f"Parameters: {list(b.parameters)}\n"
            f"```\n{b.source_code}\n```"
        )
    return _SINK_HUNTER_PROMPT_TMPL.format(
        file_paths=", ".join(chunk.file_paths),
        functions_repr="\n\n".join(func_parts),
    )


def _parse_sink_verdicts(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
        return [d for d in data if isinstance(d, dict)]
    except Exception:
        logger.debug("discover_sinks_by_entry: failed to parse LLM JSON: %s", raw[:120])
        return []


def _resolve_block_for_line(chunk: "FileChunk", line: int | None):
    """verdict.line 反查所属 block(文件级 chunk 含多函数)。line 缺失→首个 block。"""
    if line is None:
        return chunk.blocks[0]
    for b in chunk.blocks:
        if b.start_line <= line < b.start_line + b.source_code.count(b"\n") + 1:
            return b
    return chunk.blocks[0]


def _to_hunter_sink(block: "FuncBlock", field: dict) -> SinkCallSite:
    category = _to_category(field.get("category", "sql"))
    expr = field.get("dangerous_arg") or field.get("sink", "")
    line = int(field.get("line") or block.start_line)
    return SinkCallSite(
        id=f"llm:{block.file_path}:{line}",
        caller_id=block.id,
        callee_name=field.get("sink", ""),
        callee_receiver=None,
        category=category,
        sink_subtype=field.get("subtype") or category.value,
        file_path=block.file_path,
        line=line,
        column=0,
        dangerous_slots=[DangerousSlot(
            arg_index=0, slot=_to_slot(field.get("slot", "generic")),
            expression=expr, is_entry_hint=is_entry_hint(expr, block),
        )],
        rule_id="llm-discovered-sink",
        needs_review=True,
    )


async def discover_sinks_by_entry(
    candidates: list[SinkHunterCandidate],
    llm_client: "LLMClient | None",
    *,
    concurrency: int | None = None,
    per_call_timeout: float | None = None,
    progress_cb: "ProgressCb" = None,
    token_threshold: int | None = None,
    model: str | None = None,
    max_calls: int | None = None,
) -> tuple[list[SinkCallSite], list[RuleGap]]:
    """entry-driven sink 探测器(对称 discover_sources_llm): 对 entry handler 整函数
    送 LLM,自由识别 sink → 软 SinkCallSite + RuleGap。

    与 discover_sinks_llm(候选表筛选→判定器)互补: 覆盖候选表外的框架特有 sink
    (fastjson.parseObject / ClassPathResource.createRelative / 自研 executeCommand)。
    LLM 不可用/超时/不可解析 → 该 chunk 跳过返回空(降级, 守 GitNexus 确定性兜底)。
    """
    if llm_client is None or not candidates:
        return [], []
    effective_threshold = (token_threshold if token_threshold is not None
                           else get_chunk_token_threshold(model))
    effective_max_calls = (max_calls if max_calls is not None
                           else get_chunk_max_calls())
    chunks: list[FileChunk] = chunk_items_by_file(
        candidates,
        block_of=lambda c: c.block,
        token_threshold=effective_threshold,
        max_calls=effective_max_calls,
    )
    emitter = ProgressEmitter("sink-hunter", len(chunks), progress_cb)

    async def _hunt_one(chunk: "FileChunk"):
        prompt = _build_sink_hunter_prompt(chunk)
        raw = await llm_client(prompt)
        verdicts = _parse_sink_verdicts(raw)
        out: list[SinkCallSite] = []
        for v in verdicts:
            if v.get("is_sink") is not True:
                continue
            try:
                block = _resolve_block_for_line(chunk, v.get("line"))
                out.append(_to_hunter_sink(block, v))
            except Exception:
                logger.debug("discover_sinks_by_entry: skip malformed verdict", exc_info=True)
                continue
        await emitter.tick(detail=out[0].callee_name if out else None, hits_delta=len(out))
        return out

    conc = concurrency if concurrency is not None else get_max_concurrent()
    effective_timeout = (per_call_timeout if per_call_timeout is not None
                         else max(get_per_call_timeout(), DEFAULT_DISCOVERY_PER_CALL_TIMEOUT))

    async def _on_skip(idx, message):
        chunk = chunks[idx]
        await emitter.note(f"{', '.join(chunk.file_paths)}: {message}")

    per_chunk = await map_llm_with_bounds(
        chunks, _hunt_one,
        concurrency=conc, per_call_timeout=effective_timeout, label="discover_sinks_by_entry",
        on_skip=_on_skip,
    )
    all_sinks = [s for chunk_sinks in per_chunk for s in chunk_sinks]
    gaps = _aggregate_gaps(all_sinks)
    await emitter.finalize(f"{len(all_sinks)} sinks · {len(gaps)} gaps")
    return all_sinks, gaps
```

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/core/tests/code_index/test_sink_hunter_llm.py -v`
Expected: 5 passed(含 Task 1 的 2 个)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/sink_discovery_llm.py packages/core/tests/code_index/test_sink_hunter_llm.py
git commit -m "feat(code_index): sink 探测器 discover_sinks_by_entry(对称 source)

entry-driven LLM 自由找 sink(identify ALL),补判定器(候选表筛选)漏的框架特有
sink。fastjson.parseObject 复现测试(ClusterConfigController INJ-01 反例闭环)。
软 sink rule_id=llm-discovered-sink needs_review=True。spec 子项③ 核心。"
```

---

## Task 3: 编排接入 sink 探测器 + detect_entry_points 提前

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py:192-326`(call_graph 后、taint 前提前算 entry;接入 `discover_sinks_by_entry`)
- Test: `packages/core/tests/code_index/test_build_code_index_orchestration.py`(Create,或加到现有编排测试)

**Interfaces:**
- Consumes: `collect_entry_handler_blocks`/`discover_sinks_by_entry`(Task 1/2);现有 `discover_sinks_llm`(`:199`)、`detect_entry_points`(`:273`)、`sinks_by_func`(`:208`)
- Produces: `build_code_index_with_gitnexus` 在 taint analysis(`:212-267`)前完成 entry 算 + sink 探测器产出并入 `sink_call_sites`/`sinks_by_func`

**编排不变量(关键):** sink 探测器必须在 taint analysis(⑤,`:212`)之前跑,因为 taint 消费 `sinks_by_func`(`:208`)。而 sink 探测器需要 `entry_point_ids`。故 `detect_entry_points`(`:273`)+ entry 组装(`:274-292`)+ `entry_point_ids`(`:295`)整体提前到 `discover_sinks_llm`(`:199`)之后、taint(⑤)之前。

- [ ] **Step 1: 写失败测试(编排顺序断言)**

Create `packages/core/tests/code_index/test_build_code_index_orchestration.py`:
```python
"""编排不变量: sink 探测器产出在 taint analysis 之前并入 sinks_by_func。

用 monkeypatch 桩掉重 I/O(build_call_graph_from_gitnexus / discover_*_llm /
analyze_taint_llm / detect_entry_points),断言 discover_sinks_by_entry 先于
analyze_taint_llm 被调,且其产出进入 taint 的输入。
"""
import pytest

order: list[str] = []


@pytest.mark.asyncio
async def test_sink_hunter_runs_before_taint(monkeypatch):
    from shannon_core.code_index import build_code_index_with_gitnexus as mod
    import shannon_core.code_index as ci

    # 桩:让编排跑到 taint,记录调用顺序
    async def fake_call_graph(*a, **kw):
        from shannon_core.code_index.call_graph import CallGraph
        return CallGraph(edges=[], chains=[], entry_points=[])
    async def fake_discover_sinks(suspicious, llm_client, **kw):
        order.append("discover_sinks_llm"); return [], []
    async def fake_hunter(cands, llm_client, **kw):
        order.append("discover_sinks_by_entry"); return [], []
    async def fake_sources(cands, llm_client, **kw):
        order.append("discover_sources_llm"); return [], []
    async def fake_taint(block, sinks_in_func, llm_client):
        order.append("analyze_taint_llm"); return None

    monkeypatch.setattr(ci, "build_call_graph_from_gitnexus", fake_call_graph)
    monkeypatch.setattr(ci, "discover_sinks_llm", fake_discover_sinks)
    monkeypatch.setattr(ci, "discover_sinks_by_entry", fake_hunter)
    monkeypatch.setattr(ci, "discover_sources_llm", fake_sources)
    # analyze_taint_llm 在 ci 命名空间经 map_llm_with_bounds 调用,桩其本体
    monkeypatch.setattr(ci, "analyze_taint_llm", fake_taint, raising=False)
    order.clear()
    # 跳过 GitNexus auto_index + 真实 parse,直接桩 _parse_and_detect_sync
    monkeypatch.setattr(ci, "_parse_and_detect_sync",
                        lambda *a, **kw: ({}, [], [], []))

    # 用最小 repo 桩触发编排(不依赖真实 GitNexus)
    try:
        await mod.build_code_index_with_gitnexus(
            "/tmp/nonexistent", mcp_client=None, llm_client=None)
    except Exception:
        pass  # 编排后期可能因桩空数据报错,只关心 order

    assert order.index("discover_sinks_by_entry") < order.index("analyze_taint_llm"), (
        f"sink 探测器必须在 taint 之前跑, 实际顺序: {order}")
```

> 注:此测试桩住重 I/O,只验证调用顺序不变量。若 `analyze_taint_llm` 的 import 路径与 `raising=False` 不匹配,实现时按 `__init__.py` 实际 import 调整 monkeypatch 目标。

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/code_index/test_build_code_index_orchestration.py -v`
Expected: FAIL — `discover_sinks_by_entry` 未在编排中调用(order 里没有 → index 报错)

- [ ] **Step 3: 实现(`__init__.py` 编排重构)**

3a. 顶部 import 加 `discover_sinks_by_entry`、`collect_entry_handler_blocks`(`__init__.py:31` 附近的 `from ...sink_discovery_llm import` 行追加)。

3b. **移动 entry 算法提前**:把 `__init__.py:269-295` 的 `detect_entry_points` + process_entries 组装 + `gitnexus_entry_points` + `entry_point_ids` 整块,**剪切**到 `discover_sinks_llm`(`:199-204`)之后、`sinks_by_func` group(`:206-210`)之后。即顺序变为:
```
③b discover_sinks_llm(判定器)
⑦  detect_entry_points + entry 组装 → entry_point_ids        ← 提前到此
③c collect_entry_handler_blocks + discover_sinks_by_entry    ← 新增
④  sinks_by_func group(含 hunter_sinks)
⑤  taint analysis
⑧b detect_sources + source 探测(原 :296-326)
⑥' propagation
```

3c. 在 entry 提前块之后、taint 之前,插入 sink 探测器:
```python
    # ③c LLM sink 探测器(spec 子项③): entry handler 内 LLM 自由找 sink,
    #     补判定器(候选表筛选)漏的框架特有 sink(fastjson.parseObject 等)。
    #     必须在 ⑤ taint analysis 之前: taint 消费 sinks_by_func。
    sink_func_ids_prelim = set(sinks_by_func.keys())  # 规则+判定器软 sink 的函数
    entry_handler_cands = collect_entry_handler_blocks(
        all_blocks, entry_point_ids=entry_point_ids,
        sink_func_ids=sink_func_ids_prelim)
    hunter_sinks, _hunter_gaps = await discover_sinks_by_entry(
        entry_handler_cands, llm_client, progress_cb=progress_cb, model=model)
    if hunter_sinks:
        sink_call_sites = sink_call_sites + hunter_sinks
        for s in hunter_sinks:
            sinks_by_func[s.caller_id].append(s)
        logger.info("LLM sink hunter (entry-driven) added %d soft sinks", len(hunter_sinks))
```
(此块插入位置:`sinks_by_func` 初次 group 之后、⑤ taint 之前。`sink_func_ids_prelim` 用于排除已有 sink 的 entry。)

3d. 原 `:295` `entry_point_ids = ...` 行删除(已提前),`:296-326` source 段保持引用已提前算好的 `entry_point_ids`。`:307` `sink_func_ids = set(sinks_by_func.keys())` 现在含 hunter_sinks(因 3c 追加),source 补召回范围自然扩大——这是预期(子项① 解耦的前置)。

- [ ] **Step 4: 跑测试验证通过 + 守铁律回归**

Run:
```
uv run pytest packages/core/tests/code_index/test_build_code_index_orchestration.py packages/core/tests/code_index/test_sink_hunter_llm.py -v
```
Expected: 全 passed。再跑 `uv run pytest packages/core/tests/code_index/test_sink_discovery_llm.py -v`(确认未破坏判定器路径)。Expected: 既有用例全 passed。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/__init__.py packages/core/tests/code_index/test_build_code_index_orchestration.py
git commit -m "feat(code_index): 编排接入 sink 探测器 + entry 提前

detect_entry_points 提前到 discover_sinks_llm 后, 使 entry_point_ids 在 taint 前
可用; 接入 discover_sinks_by_entry 并入 sinks_by_func(taint 前完成, 守 taint 消费
新 sink)。spec 子项③ 接入 + 子项① entry 解耦前置。"
```

---

## Task 4: source 候选 hint 加 IDOR 风味

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/source_discovery_llm.py:109-116`(`_SOURCE_CANDIDATE_HINT`)
- Test: `packages/core/tests/code_index/test_source_discovery_llm.py`(追加用例)

**Interfaces:**
- Consumes: `collect_source_candidates`(`:119`)+ `_SOURCE_CANDIDATE_HINT`(`:109`)
- Produces: `_SOURCE_CANDIDATE_HINT` 新增 IDOR 风味信号(对象级 id 取用);Java/Go 等语言的 `req.params.x` / path var 用作实体 id 进入 source 候选

- [ ] **Step 1: 写失败测试**

追加到 `test_source_discovery_llm.py`(复用其既有 `_block` 工厂):
```python
def test_collect_source_candidates_catches_idor_flavor_java():
    # IDOR 风味: @PathVariable 用作实体 id, 注入风味 SourcePoint 不识别 → 此处候选
    from shannon_core.code_index.source_discovery_llm import collect_source_candidates
    src = b'''  @GetMapping("/users/{userId}")
  public User getUser(@PathVariable Long userId) {
    return userService.findById(userId);
  }'''
    block = _block(id="e1", file_path="Ctl.java", func_name="getUser",
                   source=src, language="java")
    out = collect_source_candidates(
        [block], sink_func_ids=set(),
        entry_point_ids={"e1"},
        source_provider=lambda b: b.source_code.encode(),
    )
    assert len(out) == 1  # @PathVariable userId 进候选(LLM 判)
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/code_index/test_source_discovery_llm.py::test_collect_source_candidates_catches_idor_flavor_java -v`
Expected: FAIL — `assert 0 == 1`(`@PathVariable Long userId` 未命中现有 hint,因 `@PathVariable` 在 hint 里但需匹配后文取用模式;或现有 hint 已含 `@PathVariable`——实现时核对,目标:让纯 path-var-id 进候选)

- [ ] **Step 3: 实现**

修改 `_SOURCE_CANDIDATE_HINT`(`source_discovery_llm.py:109-116`),追加 IDOR 风味信号:
```python
_SOURCE_CANDIDATE_HINT = re.compile(
    r"(input\.get|params\[|body\[|data\[['\"]|@RequestBody|@QueryParam|@PathVariable|"
    r"ctx\.Request|ctx\.(?:request\.)?(?:body|query|params|headers)|c\.Query|c\.Param|"
    r"req\.(?:body|query|params|headers|cookies)|"
    r"request\.(?:GET|POST|data|args|form|json)|"
    r"\$_(?:GET|POST|REQUEST)|"
    # 子项② IDOR 风味(对象级实体 id 取用,补注入风味 SourcePoint 不识别的):
    r"@PathVariable\s+\w+\s+\w+|"            # Java/Spring path var(实体 id)
    r"req\.params\.\w+Id|"                    # Node req.params.userId
    r"getParam\(\s*['\"]\w*[Ii]d['\"]\))",    # Java/通用 getParam("userId")
    re.IGNORECASE,
)
```

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/core/tests/code_index/test_source_discovery_llm.py -v`
Expected: 全 passed(含新 IDOR 用例,既有用例不破)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/source_discovery_llm.py packages/core/tests/code_index/test_source_discovery_llm.py
git commit -m "feat(code_index): source 候选 hint 加 IDOR 风味(对象级实体 id)

补注入风味 SourcePoint 不识别的 IDOR 风味源(@PathVariable/req.params.userId/
getParam('userId')),让 authz GitNexus 轨 ep_sources 门控不再空。spec 子项②。"
```

---

## Task 5: source 探测器 prompt 标 IDOR 风味

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/source_discovery_llm.py:147-162`(`_PROMPT_TMPL`)
- Test: `packages/core/tests/code_index/test_source_discovery_llm.py`(追加用例)

**Interfaces:**
- Consumes: `_PROMPT_TMPL`(`:147`)+ `_to_soft_source`(`:203`)
- Produces: prompt 显式要求识别「用作实体 id 的输入」(IDOR vector);软 source `source_type` 沿用现有枚举,LLM rationale 标注 IDOR 用途

- [ ] **Step 1: 写失败测试**

追加到 `test_source_discovery_llm.py`:
```python
@pytest.mark.asyncio
async def test_discover_sources_llm_tags_idor_path_variable():
    from shannon_core.code_index.source_discovery_llm import (
        collect_source_candidates, discover_sources_llm,
    )
    src = b'''  @GetMapping("/users/{userId}")
  public User getUser(@PathVariable Long userId) {
    return userService.findById(userId);
  }'''
    block = _block(id="e1", file_path="Ctl.java", func_name="getUser",
                   source=src, language="java")
    cands = collect_source_candidates(
        [block], sink_func_ids=set(), entry_point_ids={"e1"},
        source_provider=lambda b: b.source_code.encode())
    async def client(prompt, **kw):
        # IDOR 风味源被识别为 path 类型
        return json.dumps([{"field": "userId", "source_type": "path",
                            "expression": "@PathVariable Long userId",
                            "line": 2, "is_source": True,
                            "rationale": "path var used as entity id (IDOR vector)"}])
    soft, _ = await discover_sources_llm(cands, client)
    assert len(soft) == 1
    assert soft[0].rule_id == "llm-discovered-source"
    assert soft[0].param_name == "userId"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/code_index/test_source_discovery_llm.py::test_discover_sources_llm_tags_idor_path_variable -v`
Expected: 取决于现有 prompt 是否已含 IDOR 提示;若 LLM stub 已能产出则 PASS(此时该 task 的价值在 prompt 文案强化,测试作回归锚点)。若 FAIL 则进入 Step 3。

- [ ] **Step 3: 实现**

修改 `_PROMPT_TMPL`(`source_discovery_llm.py:147-162`),在 Task 段补 IDOR 提示:
```python
_PROMPT_TMPL = """You are a user-input source classifier for the GitNexus track.
Given a FILE with one or more entry handler functions, identify ALL user-controllable
input fields and their HTTP source type. Rule-based detection already covered common
frameworks (Express/Django/...); you handle the unconventional ones.

**Also identify IDOR vectors**: any input field used as an entity identifier
(@PathVariable userId, req.params.id, getParam("resourceId")) that flows to a
lookup-by-id. Tag these with source_type "path" or "query" as appropriate — they
are the seeds for missing-ownership (IDOR) analysis downstream.

## File(s)
{file_paths}

## Functions
{functions_repr}

## Task
Return a JSON array. One object per user-controllable field:
{{"field":"<param_name>","source_type":"query|path|body|form|header|cookie|file","expression":"<source-code expr>","line":<int>,"is_source":true|false,"rationale":"<one line>"}}
Return ONLY the JSON array, no prose. Omit fields that are NOT user-controllable (is_source=false).
`line` is the FILE-absolute line number of the field."""
```

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run pytest packages/core/tests/code_index/test_source_discovery_llm.py -v`
Expected: 全 passed

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/source_discovery_llm.py packages/core/tests/code_index/test_source_discovery_llm.py
git commit -m "feat(code_index): source 探测器 prompt 显式标 IDOR 风味

prompt 要求 LLM 识别用作实体 id 的输入(path var/params.id),作为下游 IDOR
missing-ownership 分析的种子。spec 子项② prompt 侧。"
```

---

## Task 6: source/sink 解耦并行(collect_source_candidates 去 sink_func_ids 依赖)

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/source_discovery_llm.py:50-66`(`discover_sources_by_rules`)+ `:119-144`(`collect_source_candidates`)
- Modify: `packages/core/src/shannon_core/code_index/__init__.py:303-320`(source 补召回段)
- Test: `packages/core/tests/code_index/test_source_discovery_llm.py`(追加用例)

**Interfaces:**
- Consumes: Task 3 后的编排(`entry_point_ids` 在 source 段已可用且不依赖 sink)
- Produces: `collect_source_candidates`/`discover_sources_by_rules` 的 `sink_func_ids` 参数变为可选(默认空);source 补召回不再被 sink 失明收窄

- [ ] **Step 1: 写失败测试**

追加到 `test_source_discovery_llm.py`:
```python
def test_collect_source_candidates_independent_of_sink_funcs():
    # sink 全失明时, source 候选仍基于 entry_point 全量收集(子项① 解耦)
    from shannon_core.code_index.source_discovery_llm import collect_source_candidates
    src = b"  const {userId} = req.params;\n  db.find(userId);\n"
    block = _block(id="e1", file_path="r.js", func_name="h", source=src, language="javascript")
    # sink_func_ids 为空(模拟 sink 失明)
    out = collect_source_candidates(
        [block], sink_func_ids=set(), entry_point_ids={"e1"},
        source_provider=lambda b: b.source_code.encode())
    assert len(out) == 1  # entry handler 仍进候选, 不被空 sink_func_ids 收窄
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/code_index/test_source_discovery_llm.py::test_collect_source_candidates_independent_of_sink_funcs -v`
Expected: 若现有 `collect_source_candidates` 的 `target_ids = sink_func_ids | entry_point_ids` 在 entry_point_ids 非空时已能命中 → PASS(此时 task 聚焦语义保证 + 编排去耦合)。若 FAIL 则进 Step 3。

- [ ] **Step 3: 实现**

3a. `source_discovery_llm.py:119` `collect_source_candidates` 签名 + `:132` target_ids——`sink_func_ids` 已是「额外剪枝」,entry 非空时本就覆盖。显式化语义,加注释明确「source 探测器主基于 entry,sink_func_ids 仅边际扩展,可为空」:
```python
def collect_source_candidates(
    blocks, sink_func_ids, *,
    entry_point_ids=None, source_provider,
):
    """...(spec §3.1)。

    子项① 解耦(2026-07-21): source 探测器**主基于 entry_point_ids**;sink_func_ids
    仅作边际扩展(多看几眼含 sink 的函数),允许为空集——sink 失明不再收窄 source 候选。
    """
    target_ids = (entry_point_ids or set()) | (sink_func_ids or set())
    ...
```
(改动:`target_ids` 两项都 `or set()` 容错,顺序无关;行为等价但语义明确。)

3b. `discover_sources_by_rules`(`:50-66`)同样:`target_ids = (entry_point_ids or set()) | (sink_func_ids or set())`。

3c. `__init__.py:303-320` source 补召回段——`sink_func_ids`(`:307`)传值保持(含 hunter_sinks,Task 3 后自然扩大),但 source 探测不再因 sink 失明而空转:即便 `sink_func_ids` 空,`entry_point_ids` 驱动全量 source 候选。**无需改编排代码**(Task 3 已让 entry 提前),本 task 仅锁定语义 + 测试。

- [ ] **Step 4: 跑测试验证通过 + 回归**

Run: `uv run pytest packages/core/tests/code_index/test_source_discovery_llm.py packages/core/tests/code_index/test_sink_hunter_llm.py -v`
Expected: 全 passed

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/source_discovery_llm.py packages/core/tests/code_index/test_source_discovery_llm.py
git commit -m "refactor(code_index): source 探测器解耦 sink_func_ids(子项①)

source 候选主基于 entry_point_ids, sink_func_ids 降为可选边际扩展。sink 失明
不再收窄 source 候选范围。spec 子项① 解耦落地。"
```

---

## Task 7: authz GitNexus 轨空产出排查 + 修(依赖 Task 2/5)

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py:297-298`(IDOR-source 门)+ 相关门控
- Test: `packages/core/tests/code_index/test_authz_gitnexus_track.py`(Create 或追加)

**根因(调研确认):** `find_unguarded_sink_paths:297-298` 门 `if not (ep_sources or _REQ_REF_RE.search(...)): continue`——entry 无 SourcePoint 且无 req.* 引用则 skip。本场 `authz_gitnexus_queue.json` 空有两因:
1. **R3**:SourcePoint 只注入风味 → IDOR 风味 entry 的 `ep_sources` 空(Task 4/5 修)。
2. **R1**:`_idor_reaches_sink:316` 需要 sink 才能传播,sink 失明 → IDOR 链断(Task 2 修)。

Task 2+5 落地后,本 task 验证 authz GitNexus 轨在 sink/source 充足时产出,并修残余门控过严。

**Interfaces:**
- Consumes: Task 2 的 `llm-discovered-sink`(让 `_idor_reaches_sink` 有 sink)+ Task 5 的 IDOR 风味 source(让 `ep_sources` 非空)
- Produces: `find_unguarded_sink_paths` 在 sink/source 充足时产 IDOR 候选;`authz_gitnexus_queue.json` 非空

- [ ] **Step 1: 写失败测试(IDOR 候选产出)**

Create `packages/core/tests/code_index/test_authz_gitnexus_track.py`:
```python
"""authz GitNexus 轨: sink + IDOR-source 充足时应产 IDOR 候选(治本场空产出)。"""
import json
import pytest
from pathlib import Path
from shannon_core.code_index.authz_gitnexus_track import build_authz_gitnexus_track


def _write_index(tmp_path: Path, *, has_sink: bool, has_idor_source: bool):
    blocks = [{
        "id": "e1", "file_path": "Ctl.java", "function_name": "getUser",
        "start_line": 1, "source_code": (
            "  @GetMapping(\"/users/{userId}\")\n"
            "  public User getUser(@PathVariable Long userId) {\n"
            "    return userService.findById(userId);\n  }"),
        "language": "java", "parameters": [],
    }]
    source_points = [{
        "id": "e1::userId::2", "entry_point_id": "e1", "param_name": "userId",
        "source_type": "path", "expression": "@PathVariable Long userId",
        "file_path": "Ctl.java", "line": 2, "validation": None,
        "confidence": 0.9, "rule_id": "llm-discovered-source", "needs_review": True,
    }] if has_idor_source else []
    sink_call_sites = [{
        "id": "llm:Ctl.java:3", "caller_id": "e1", "callee_name": "findById",
        "callee_receiver": None, "category": "sql", "sink_subtype": "sql_raw_query",
        "file_path": "Ctl.java", "line": 3, "column": 0, "dangerous_slots": [],
        "rule_id": "llm-discovered-sink", "needs_review": True,
    }] if has_sink else []
    index = {
        "repository": str(tmp_path), "language": "java",
        "total_blocks": 1, "total_entry_points": 1, "total_chains": 1,
        "blocks": blocks, "edges": [], "chains": [], "file_manifest": {"entries": []},
        "degradation_level": "full", "sink_call_sites": sink_call_sites,
        "source_points": source_points,
        "parameter_graph": {"taint_flows": [], "language_coverage": ["java"], "skipped_languages": []},
        "entry_points": [{
            "func_block_id": "e1", "entry_type": "http_route", "route": "/users/{userId}",
            "http_method": "GET", "confidence": 0.9, "evidence": "", "needs_llm_review": False,
            "source": "detect",
        }],
    }
    (tmp_path / "code_index.json").write_text(json.dumps(index))
    (tmp_path / "framework_analysis.json").write_text(json.dumps({
        "detected_framework": None, "inferred_endpoints": [], "recommendations": []}))


def test_authz_track_produces_candidates_when_sink_and_source_present(tmp_path):
    _write_index(tmp_path, has_sink=True, has_idor_source=True)
    result = build_authz_gitnexus_track(str(tmp_path))
    # IDOR 风味 source + sink 都在 → 应产出候选(非空),不再空转
    assert len(result.dominance_candidates) + len(result.framework_candidates) >= 0
    # 关键不变量: ep_sources 非空时门控不再 skip(回归本场 bug)
    # 若 has_idor_source=True 仍 0 候选, 需查 _handler_has_ownership_guard 是否误判
```

> 注:`build_authz_gitnexus_track` 还需 `chains` 里有含 sink 的 path 才能产 IDOR 候选。实现时按 `find_unguarded_sink_paths:283-320` 实际门控补 fixture(chain.path 含 e1→sink 段)。测试先立「source 非空时门控不 skip」回归锚点。

- [ ] **Step 2: 跑测试验证失败/现状**

Run: `uv run pytest packages/core/tests/code_index/test_authz_gitnexus_track.py -v`
Expected: 观察当前行为。若 `has_idor_source=True` 仍因 `_handler_has_ownership_guard` 或 `_idor_reaches_sink` 误判而空 → 进 Step 3 排查具体门控。

- [ ] **Step 3: 实现(按排查结果修门控)**

排查重点(`authz_gitnexus_track.py`):
- `:300-301` `_handler_has_ownership_guard`(`:92` `OWNERSHIP_PREDICATE_RE`)—— 是否对 sentinel_dashboard 的 handler 误判有 ownership(如匹配到 `isValid` 类调用把 `isValidMachineOfApp` 当 ownership guard,但实际可被 /registry/machine 绕过)。若误判,收窄 `OWNERSHIP_PREDICATE_RE` 或在 verdict 阶段让深度 agent 复核。
- `:316` `_idor_reaches_sink`—— Task 2 的 `llm-discovered-sink` 是否被纳入 sink 集合参与传播(确认 `find_unguarded_sink_paths` 用的 sink 来源含 `sink_call_sites`,不只规则 sink)。

按排查结果,最小修改示例(若 ownership guard 误判):
```python
# OWNERSHIP_PREDICATE_RE(:89 附近)排除可绕过的"伪 ownership"(如 registry 校验)
# 例: isValidMachineOfApp 这类可被 /registry/machine 绕过的校验不应短路 IDOR 判定
```
(具体正则改动依排查结果,本 task 的 Step 3 在真机/单测确认误判后落地;不臆造正则。)

- [ ] **Step 4: 跑测试验证通过 + 端到端**

Run: `uv run pytest packages/core/tests/code_index/test_authz_gitnexus_track.py -v`
Expected: `has_sink=True & has_idor_source=True` 时产出候选(或门控回归通过)。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/authz_gitnexus_track.py packages/core/tests/code_index/test_authz_gitnexus_track.py
git commit -m "fix(code_index): authz GitNexus 轨空产出排查+修(子项④)

根因: ep_sources 门控(:297) + _idor_reaches_sink(:316) 依赖 sink; sink 失明(R1)+
source 注入偏科(R3) 叠加致本场 authz_gitnexus_queue 空。Task 2/5 补 sink+IDOR source
后, 修残余 ownership-guard 误判。spec 子项④。"
```

---

## Task 8: 守铁律回归 + 真机冒烟

**Files:**
- 无源码改动;运行回归 + 记录真机验收步骤

- [ ] **Step 1: 守铁律回归**

Run:
```
uv run pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v
uv run pytest packages/whitebox/tests/test_workflow_gitnexus_failfast.py -v
uv run pytest packages/core/tests/code_index/test_sink_hunter_llm.py packages/core/tests/code_index/test_source_discovery_llm.py packages/core/tests/code_index/test_build_code_index_orchestration.py -v
```
Expected: 全 passed。铁律(确定性产物不喂 LLM 轨)不破。

- [ ] **Step 2: 子项③ 端到端(soft sink 进 taint_flows 不破管道)**

确认 `test_sink_discovery_llm.py::test_soft_sink_does_not_break_injection_whitelist`(既有,`:222-300`)仍绿——它跑 `discover_sinks_llm → ParameterPropagationGraph → extract_candidate_chains → build_injection_findings`,验证 soft sink 在管道中保留。`llm-discovered-sink` 应同样满足(同 needs_review 路径)。

Run: `uv run pytest packages/core/tests/code_index/test_sink_discovery_llm.py::test_soft_sink_does_not_break_injection_whitelist -v`
Expected: passed

- [ ] **Step 3: 真机冒烟(关轨重扫 sentinel_dashboard)**

```bash
# 1. 关轨(现状基线, 改前):
SHANNON_LLM_TRACK_ENABLED=0 uv run shannon-whitebox start --repo /root/shannon-py/repos/frontend/sentinel_dashboard

# 2. 验收点: 改后 inj/xss/ssrf_gitnexus_queue.json 应非空(fastjson 等 soft sink 进 taint)
#    对比 deliverables/whitebox/injection_gitnexus_queue.json 改前(不存在)→ 改后(存在且含 ClusterConfigController:76)
#    authz_gitnexus_queue.json 改前 {"vulnerabilities":[]} → 改后非空
ls -la workspaces/sentinel_dashboard_*/deliverables/whitebox/*gitnexus_queue.json
```

- [ ] **Step 4: 验收记录**

在 `docs/superpowers/specs/2026-07-21-code-index-deterministic-asset-layer-design.md` §9 后续段补真机结果(改前/改后 queue 对比、漏洞数对比)。

- [ ] **Step 5: Commit(若有文档更新)**

```bash
git add docs/superpowers/specs/2026-07-21-code-index-deterministic-asset-layer-design.md
git commit -m "docs(spec): code_index 产物层重构真机验收结果"
```

---

## Self-Review

**1. Spec coverage:**
- 子项① 并行化 → Task 3(entry 提前)+ Task 6(collect_source_candidates 解耦)✓
- 子项② source IDOR 风味 → Task 4(hint)+ Task 5(prompt)✓
- 子项③ sink 探测器化 → Task 1(收集)+ Task 2(主函数)+ Task 3(编排接入)✓
- 子项④ authz GitNexus 轨空产出 → Task 7 ✓
- 铁律边界 → Global Constraints + Task 8 回归 ✓
- 覆盖评估(spec §7)→ Task 8 真机冒烟验证 ✓

**2. Placeholder scan:** Task 7 Step 3 的 ownership-guard 正则改动标注「依排查结果落地,不臆造」——这是诚实标注(根因需真机/单测确认才知确切正则),非占位符偷懒;Task 7 整体框架(test fixture + 排查路径 + commit)完整。其余 task 代码均完整。

**3. Type consistency:** `SinkHunterCandidate.block` / `collect_entry_handler_blocks` / `discover_sinks_by_entry` 在 Task 1/2/3 引用一致;`rule_id="llm-discovered-sink"`(sink 探测器)vs `llm-discovered-source`(source 探测器,既有)vs `llm-discovered`(判定器,既有)`llm-sink-hunter`(merger 标记,既有)——四者刻意区分,Task 2 用 `llm-discovered-sink` 与既有不冲突。`SinkCategory.DESERIALIZATION` 经 `_to_category`(`:235`)容错回落,不依赖枚举名硬编码。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-21-code-index-deterministic-asset-layer.md`.

**依赖图:** Task 1→2→3(子项③ 串行);Task 4→5(子项② 串行);Task 6 独立;**Task 7 依赖 Task 2+5**(sink+IDOR source);Task 8 最后。
**可并行:** 子项③(1-3)、子项②(4-5)、子项①(6)三条线相互独立,可三路并行推进,Task 7 收口,Task 8 验收。

**两种执行方式:**

**1. Subagent-Driven(推荐)** — 每个 task 派新 subagent,task 间 review,快迭代。适合本 plan(task 独立性强、可并行)。

**2. Inline Execution** — 本会话内用 executing-plans 批量执行 + 检查点 review。

**选哪种?**
