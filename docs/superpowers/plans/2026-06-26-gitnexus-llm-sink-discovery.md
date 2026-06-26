# GitNexus 轨 LLM sink 补召回 + 双轨可配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 GitNexus 轨加 LLM sink 补召回（规则未命中的可疑 call → 软 SinkCallSite + rule_gap_report 反哺规则），接通两个 llm_client stub，并加 LLM 轨可配置开关（默认开）。

**Architecture:** 在 `build_code_index_with_gitnexus` 的 `detect_sinks` 之后插入 `collect_suspicious_calls`（纯 AST，复用 parser 遍历 + 规则索引判断未命中）→ `discover_sinks_llm`（per-function LLM，产 `rule_id="llm-discovered"` 软 SinkCallSite 与 RuleGap 聚合）→ 软 sink 并入 `sink_call_sites` 走现有 intra/propagate/verdict 同流。两个 llm_client stub 用 `run_claude_prompt` 封装接通，受 `SHANNON_GITNEXUS_LLM_ENABLED`（默认开）控制；LLM 轨受 `SHANNON_LLM_TRACK_ENABLED`（默认开）控制。LLM 任一环节不可用 → 降级到纯规则 + `is_entry_hint`（立场 B 成果）。

**Tech Stack:** Python 3.11+ / pydantic v2 / tree-sitter parsers / temporalio / Claude Agent SDK (`run_claude_prompt`)。测试 pytest。

**Spec:** `docs/superpowers/specs/2026-06-26-gitnexus-llm-sink-discovery-design.md`（已审通过）。

## Global Constraints

- **双轨消费模型铁律**：LLM 轨（`vuln-*.txt` agent）不吃任何确定性产物；本计划**只动 GitNexus 轨 + LLM 轨开关**，不改任何 LLM 轨 prompt、不喂确定性 hints。
- **`SinkCallSite` 结构不动**：软 sink 用 `rule_id="llm-discovered"` 区分，不新增字段（共享模型零侵入，下游 chain_verdict/merger 无感）。
- **降级原则（宁可漏不可崩）**：`code_index` 必须产出；LLM 任一环节（discover/intra/verdict）挂都不阻断确定性骨架，退回纯规则 + `is_entry_hint`。
- **默认值**：`SHANNON_LLM_TRACK_ENABLED` 默认开（`"1"`）；`SHANNON_GITNEXUS_LLM_ENABLED` 默认开。两者都是「关 = 省 LLM」：前者关重型 vuln agent，后者关 GitNexus 轻量 LLM（退回确定性 fallback）。
- **测试纪律（feat/fork-py）**：全套 pytest 有预存挂起/失败——**只跑改动相关测试文件**，不广跑全套。运行命令里给出精确文件。
- **LLMClient 契约**：`Callable[..., Awaitable[str]]`，即 `async (prompt, **kwargs) -> raw_str`（与现有 `analyze_taint_llm` 一致）。

---

## File Structure

**新建：**
- `packages/core/src/shannon_core/code_index/sink_discovery_llm.py` — LLM sink 补召回模块：`SuspiciousCall` / `RuleGap` 数据类、`_SUSPICIOUS_CALLEE_RE`、`collect_suspicious_calls`、`discover_sinks_llm`、软 sink 构造、gap 聚合。单一职责：把规则盲区的可疑 call 经 LLM 转成软 SinkCallSite + 规则缺口。
- `packages/core/tests/code_index/test_sink_discovery_llm.py` — 该模块单测（mock LLM）。

**修改：**
- `packages/core/src/shannon_core/code_index/__init__.py` — `build_code_index_with_gitnexus` 插入补召回 + 返回 `(CodeIndex, list[RuleGap])`；`write_index_files` 加写 `rule_gap_report.json`。
- `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` — `run_code_index` 解包 tuple + 写 gap 报告；两个 stub 接通真 client（受 env 控制）。
- `packages/core/src/shannon_core/config/concurrency.py` — 加 `is_llm_track_enabled()` / `is_gitnexus_llm_enabled()`。
- `packages/whitebox/src/shannon_whitebox/pipeline/shared.py` — `PipelineInput` 加 `enable_llm_track`。
- `packages/whitebox/src/shannon_whitebox/cli/main.py` — 注入 `enable_llm_track`。
- `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` — vuln_tasks 循环前判断 `enable_llm_track`。
- `packages/core/tests/test_concurrency_config.py` — 加两个开关的 env 测试。

---

## Task 1: `collect_suspicious_calls` — 半 sink 收集器（纯 AST，无 LLM）

**Files:**
- Create: `packages/core/src/shannon_core/code_index/sink_discovery_llm.py`
- Test: `packages/core/tests/code_index/test_sink_discovery_llm.py`

**Interfaces:**
- Consumes: `parser.iter_calls(block, source) -> call_nodes`、`parser.destructure_call(call) -> (callee, receiver)`、`parser.extract_arg_expressions(call, source) -> list[str]`（均来自现有 BaseParser）；`sink_detector._RULE_INDEX` / `_rule_matches`（判断规则是否已命中，与 `detect_sinks` 一致）。
- Produces: `SuspiciousCall`（dataclass）；`collect_suspicious_calls(blocks, parser, *, source_provider) -> list[SuspiciousCall]`。

- [ ] **Step 1: Write failing tests** — 创建 `test_sink_discovery_llm.py`：

```python
"""sink_discovery_llm 单测 — 半 sink 收集 + LLM 补召回(spec 方案 A)."""
import pytest

from shannon_core.code_index.models import FuncBlock
from shannon_core.code_index.sink_discovery_llm import (
    SuspiciousCall,
    collect_suspicious_calls,
    discover_sinks_llm,
    RuleGap,
)


class _FakeCall:
    def __init__(self, line, column=0):
        self.line = line
        self.column = column


class _FakeParser:
    """记录预设的 call → (callee, receiver, arg_exprs), 命中 sink_detector 规则的用真规则名."""
    def __init__(self, calls):
        self._calls = calls  # [(callee, receiver, arg_exprs, line), ...]

    def iter_calls(self, block, source):
        return [_FakeCall(line) for *_, line in self._calls]

    def destructure_call(self, call):
        callee, receiver, _, line = self._calls[call.line - 1]
        return callee, receiver

    def extract_arg_expressions(self, call, source):
        _, _, arg_exprs, _ = self._calls[call.line - 1]
        return arg_exprs


def _block(name="handler", file="app.py", language="python", source="def handler(): pass"):
    return FuncBlock(
        id=f"{file}:{name}:1", file_path=file, function_name=name,
        start_line=1, end_line=10, source_code=source,
        parameters=["uid"], language=language,
    )


def test_collects_sinkish_unmatched_call():
    # raw_query 是 sink-ish(query) 但规则库无 raw_query@custom_db → 收集
    block = _block()
    parser = _FakeParser([("raw_query", "custom_db", ["\"SELECT \" + uid"], 1)])
    out = collect_suspicious_calls([block], parser, source_provider=lambda b: b"src")
    assert len(out) == 1
    assert out[0].callee == "raw_query"
    assert out[0].receiver == "custom_db"


def test_skips_rule_hit_call():
    # cursor.execute 命中 py-db-cursor-execute 规则 → 不收集
    block = _block()
    parser = _FakeParser([("execute", "cursor", ["uid"], 1)])
    out = collect_suspicious_calls([block], parser, source_provider=lambda b: b"src")
    assert out == []


def test_skips_non_sinkish_call():
    block = _block()
    parser = _FakeParser([("helper", None, ["uid"], 1)])
    out = collect_suspicious_calls([block], parser, source_provider=lambda b: b"src")
    assert out == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_sink_discovery_llm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.code_index.sink_discovery_llm'`

- [ ] **Step 3: Implement `collect_suspicious_calls`** — 创建 `sink_discovery_llm.py`：

```python
"""LLM sink discovery for GitNexus track (spec §3.1, 方案 A 半 sink 精准).

规则库没命中的可疑 call(callee/receiver 命中 sink-ish 模式)→ 送 LLM 判定 →
软 SinkCallSite(rule_id="llm-discovered")。与 detect_sinks 独立遍历, 复用
parser.iter_calls / destructure_call / extract_arg_expressions, 接受双遍历
开销换 detect_sinks 零改动。LLM 不可用时 discover_sinks_llm 返回空(降级)。
"""
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Awaitable

from shannon_core.code_index.parameter_models import (
    DangerousSlot,
    SinkCallSite,
    SinkCategory,
    SlotContext,
)
from shannon_core.code_index.sink_detector import (
    _RULE_INDEX,
    _make_id,
    _rule_matches,
    is_entry_hint,
)

if TYPE_CHECKING:
    from shannon_core.code_index.models import FuncBlock
    from shannon_core.code_index.parsers.base import BaseParser

logger = logging.getLogger(__name__)

LLMClient = Callable[..., Awaitable[str]]


# sink-ish callee/receiver 模式(spec §3.1 初稿): 比规则库宽松, 精确判定交 LLM。
_SUSPICIOUS_CALLEE_RE = re.compile(
    r"(query|exec(ute)?|render|redirect|include|require|unserialize|"
    r"pickle|loads|system|popen|raw|where|format|template|open|fetch)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SuspiciousCall:
    block: "FuncBlock"
    callee: str
    receiver: str | None
    arg_exprs: list[str]
    file_path: str
    line: int
    column: int


def _is_rule_hit(language: str, callee: str, receiver: str | None) -> bool:
    """该 call 是否已被 detect_sinks 规则库命中(避免与规则 sink 重复)。"""
    candidates = _RULE_INDEX.get((language, callee), [])
    return any(_rule_matches(rule, receiver) for rule in candidates)


def collect_suspicious_calls(
    blocks: "list[FuncBlock]",
    parser: "BaseParser",
    *,
    source_provider: "Callable[[FuncBlock], bytes | None]",
) -> list[SuspiciousCall]:
    """遍历所有函数的 call, 收集『sink-ish 但规则未命中』的可疑 call。"""
    out: list[SuspiciousCall] = []
    for block in blocks:
        source = source_provider(block)
        if source is None:
            continue
        try:
            call_nodes = list(parser.iter_calls(block, source))
        except Exception:
            logger.debug("suspicious scan: iter_calls failed for %s", block.id, exc_info=True)
            continue
        for call in call_nodes:
            try:
                callee, receiver = parser.destructure_call(call)
            except Exception:
                continue
            if not callee:
                continue
            if _is_rule_hit(block.language, callee, receiver):
                continue  # 规则已命中, detect_sinks 会产 SinkCallSite, 不重复
            target = callee if receiver is None else f"{receiver}.{callee}"
            if not _SUSPICIOUS_CALLEE_RE.search(target):
                continue
            try:
                arg_exprs = parser.extract_arg_expressions(call, source)
            except Exception:
                arg_exprs = []
            out.append(SuspiciousCall(
                block=block, callee=callee, receiver=receiver, arg_exprs=arg_exprs,
                file_path=block.file_path, line=call.line, column=call.column,
            ))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_sink_discovery_llm.py -v`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/sink_discovery_llm.py packages/core/tests/code_index/test_sink_discovery_llm.py
git commit -m "feat(code_index): add collect_suspicious_calls (半 sink 收集, 方案 A) (spec Task 1)"
```

---

## Task 2: `discover_sinks_llm` — LLM 补召回 → 软 SinkCallSite + RuleGap 聚合

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/sink_discovery_llm.py`（追加）
- Test: `packages/core/tests/code_index/test_sink_discovery_llm.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `SuspiciousCall`；`SinkCategory` / `SlotContext` / `DangerousSlot` / `SinkCallSite`（`parameter_models`）；`_make_id`（`sink_detector`，软 sink id 格式）；`is_entry_hint`（`sink_detector`）。
- Produces: `RuleGap`（dataclass）；`discover_sinks_llm(suspicious, llm_client) -> tuple[list[SinkCallSite], list[RuleGap]]`。软 sink `rule_id="llm-discovered"`、`needs_review=True`。

- [ ] **Step 1: Write failing tests** — 追加到 `test_sink_discovery_llm.py`：

```python
class _FakeLLMClient:
    """按 call_ref 映射返回 LLM 判定 JSON。"""
    def __init__(self, verdicts_by_ref):
        self._by_ref = verdicts_by_ref  # {"raw_query:1": {is_sink, category, slot, arg_index, rationale}}

    async def __call__(self, prompt, **kwargs):
        # 简化: prompt 里含可疑 call 的 callee+line, 返回该函数所有判定
        # 测试用: 直接返回预设的 verdicts 列表(JSON)
        return self._by_ref.get("__prompt__", "[]")


def _suspicious(callee="raw_query", receiver="custom_db", line=1, arg="uid"):
    from shannon_core.code_index.models import FuncBlock
    block = FuncBlock(
        id=f"app.py:handler:{line}", file_path="app.py", function_name="handler",
        start_line=1, end_line=10, source_code="def handler(): pass",
        parameters=["uid"], language="python",
    )
    return SuspiciousCall(block=block, callee=callee, receiver=receiver,
                          arg_exprs=[arg], file_path="app.py", line=line, column=0)


def test_discover_produces_soft_sink(monkeypatch):
    # LLM 判 is_sink=True → 软 SinkCallSite, rule_id=llm-discovered, needs_review=True
    async def client(prompt, **kw):
        return json.dumps([{"call_ref": "raw_query:1", "is_sink": True,
                            "category": "sql", "slot": "sql_value", "arg_index": 0,
                            "rationale": "raw SQL concat"}])
    soft, gaps = await discover_sinks_llm([_suspicious()], client)
    assert len(soft) == 1
    s = soft[0]
    assert s.rule_id == "llm-discovered"
    assert s.needs_review is True
    assert s.category == SinkCategory.SQL
    assert s.dangerous_slots[0].slot == SlotContext.SQL_VALUE
    assert s.dangerous_slots[0].arg_index == 0


def test_discover_skips_non_sink(monkeypatch):
    async def client(prompt, **kw):
        return json.dumps([{"call_ref": "raw_query:1", "is_sink": False}])
    soft, gaps = await discover_sinks_llm([_suspicious()], client)
    assert soft == []


def test_discover_degrades_when_llm_unavailable():
    # llm_client=None → 返回空(降级), 不抛
    soft, gaps = await discover_sinks_llm([_suspicious()], None)
    assert soft == [] and gaps == []

    async def raising(prompt, **kw):
        raise RuntimeError("timeout")
    soft, gaps = await discover_sinks_llm([_suspicious()], raising)
    assert soft == [] and gaps == []


def test_gap_aggregation():
    # 同 pattern 的两个软 sink → 聚合成 1 条 gap, count=2
    async def client(prompt, **kw):
        return json.dumps([
            {"call_ref": "raw_query:1", "is_sink": True, "category": "sql",
             "slot": "sql_value", "arg_index": 0, "rationale": "x"},
        ])
    calls = [_suspicious(line=1), _suspicious(line=2)]
    soft, gaps = await discover_sinks_llm(calls, client)
    assert len(gaps) == 1
    assert gaps[0].count == 1  # client 每次只判 1 个 → gap 聚合按实际产出的软 sink
    # 补一个真两软 sink 的场景:
    async def client2(prompt, **kw):
        return json.dumps([
            {"call_ref": "raw_query:1", "is_sink": True, "category": "sql",
             "slot": "sql_value", "arg_index": 0, "rationale": "x"},
            {"call_ref": "raw_query:2", "is_sink": True, "category": "sql",
             "slot": "sql_value", "arg_index": 0, "rationale": "y"},
        ])
    calls2 = [_suspicious(line=1), _suspicious(line=2)]
    soft2, gaps2 = await discover_sinks_llm(calls2, client2)
    assert len(soft2) == 2
    assert len(gaps2) == 1
    assert gaps2[0].count == 2
    assert gaps2[0].pattern == "raw_query@custom_db"
```

> 注：测试里 `call_ref` 用 `"{callee}:{line}"`，实现里按此对回 `SuspiciousCall`。`_FakeLLMClient` 类删掉（改用内联 `async def client`），或保留——上面已用内联 async 函数，删掉未用的 `_FakeLLMClient` 类定义。

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_sink_discovery_llm.py -v`
Expected: FAIL — `discover_sinks_llm` / `RuleGap` / `SinkCategory` / `json` 未导入或未定义。

- [ ] **Step 3: Implement `discover_sinks_llm`** — 追加到 `sink_discovery_llm.py`。同时在文件顶部 import 补 `json`（已有）。追加：

```python
@dataclass(frozen=True)
class RuleGap:
    """一条规则缺口 —— 聚合同模式的 LLM 软 sink, 驱动规则库迭代(spec §3.1 层 2)。"""
    pattern: str            # "{callee}@{receiver}" 或 "{callee}"
    language: str
    category: str
    slot: str
    count: int
    sample_evidence: list[str] = field(default_factory=list)


_DISCOVERY_PROMPT_TMPL = """You are a security sink classifier for the GitNexus track.
Given ONE function and its suspicious call list (callee/receiver that look sink-ish
but were NOT matched by the deterministic rule library), judge whether each is a
real security sink.

## Function
{func_name} ({file}:{line})
Parameters: {params}

## Source
```
{source}
```

## Suspicious calls (judge each by call_ref)
{suspicious_repr}

## Task
For EACH call above, return a JSON array. One object per call:
{{"call_ref": "<callee>:<line>", "is_sink": true|false, "category": "sql|command|file|template|deserialization|ssrf|xss|redirect|log", "slot": "sql_value|sql_identifier|cmd_argument|file_path|template_expr|url|deserialize|generic", "arg_index": <0-based int or -1>, "rationale": "<one line>"}}
Return ONLY the JSON array, no prose."""


def _build_discovery_prompt(block, calls: list[SuspiciousCall]) -> str:
    lines = []
    for sc in calls:
        target = sc.callee if sc.receiver is None else f"{sc.receiver}.{sc.callee}"
        lines.append(f"- call_ref: {sc.callee}:{sc.line}  call: {target}  args: {sc.arg_exprs}")
    return _DISCOVERY_PROMPT_TMPL.format(
        func_name=block.function_name, file=block.file_path, line=block.start_line,
        params=list(block.parameters), source=block.source_code,
        suspicious_repr="\n".join(lines),
    )


def _parse_verdicts(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
        return [d for d in data if isinstance(d, dict)]
    except Exception:
        logger.debug("discover_sinks_llm: failed to parse LLM JSON: %s", raw[:120])
        return []


# category / slot 字符串 → 枚举(容错: 不认识回落到安全中性值)
def _to_category(v: str) -> SinkCategory:
    try:
        return SinkCategory(v)
    except ValueError:
        return SinkCategory.SQL  # 最常见, 回落后 needs_review 仍会过 LLM 复核


def _to_slot(v: str) -> SlotContext:
    try:
        return SlotContext(v)
    except ValueError:
        return SlotContext.GENERIC


def _to_soft_sink(sc: SuspiciousCall, verdict: dict) -> SinkCallSite:
    arg_index = int(verdict.get("arg_index", -1))
    expr = sc.arg_exprs[arg_index] if 0 <= arg_index < len(sc.arg_exprs) else (
        sc.arg_exprs[0] if sc.arg_exprs else ""
    )
    slot = _to_slot(verdict.get("slot", "generic"))
    category = _to_category(verdict.get("category", "sql"))
    # 复用 _make_id 的格式契约(Spec A: TaintFlow.sink_call_site_id 须匹配)
    sink_id = f"{sc.file_path}:{sc.block.function_name}:{sc.callee}:{sc.line}:{sc.column}"
    return SinkCallSite(
        id=sink_id,
        caller_id=sc.block.id,
        callee_name=sc.callee,
        callee_receiver=sc.receiver,
        category=category,
        sink_subtype=verdict.get("subtype") or category.value,
        file_path=sc.file_path,
        line=sc.line,
        column=sc.column,
        dangerous_slots=[DangerousSlot(
            arg_index=arg_index, slot=slot, expression=expr,
            is_entry_hint=is_entry_hint(expr, sc.block),
        )],
        rule_id="llm-discovered",
        needs_review=True,
    )


def _aggregate_gaps(soft_sinks: list[SinkCallSite]) -> list[RuleGap]:
    buckets: dict[tuple, RuleGap] = {}
    for s in soft_sinks:
        pattern = s.callee_name if s.callee_receiver is None else f"{s.callee_name}@{s.callee_receiver}"
        slot = s.dangerous_slots[0].slot.value if s.dangerous_slots else "generic"
        # language 不在 SinkCallSite 上; 留空, 由调用方补(spec: gap 报告语言维度可选)
        key = (pattern, s.category.value, slot)
        evidence = f"{s.file_path}:{s.line}  {s.callee_name}"
        if key in buckets:
            b = buckets[key]
            buckets[key] = RuleGap(
                pattern=b.pattern, language=b.language, category=b.category,
                slot=b.slot, count=b.count + 1,
                sample_evidence=(b.sample_evidence + [evidence])[:5],
            )
        else:
            buckets[key] = RuleGap(
                pattern=pattern, language="", category=s.category.value,
                slot=slot, count=1, sample_evidence=[evidence],
            )
    return list(buckets.values())


async def discover_sinks_llm(
    suspicious: list[SuspiciousCall],
    llm_client: LLMClient | None,
) -> tuple[list[SinkCallSite], list[RuleGap]]:
    """对含可疑 call 的函数逐个调 LLM, 判定哪些是真 sink → 软 SinkCallSite + RuleGap。

    LLM 不可用(None / raise / 不可解析)→ 该函数跳过, 返回空(降级, spec §3.5)。
    调用粒度 = function 级(去重分组, 一函数一次 LLM 调用)。
    """
    if llm_client is None or not suspicious:
        return [], []
    by_func: dict[str, list[SuspiciousCall]] = defaultdict(list)
    for sc in suspicious:
        by_func[sc.block.id].append(sc)

    soft_sinks: list[SinkCallSite] = []
    for func_id, calls in by_func.items():
        block = calls[0].block
        prompt = _build_discovery_prompt(block, calls)
        try:
            raw = await llm_client(prompt)
        except Exception as exc:
            logger.warning("discover_sinks_llm failed for %s: %s", func_id, exc)
            continue  # 降级: 该函数跳过
        verdicts = _parse_verdicts(raw)
        # 按 call_ref(callee:line) 对回 SuspiciousCall
        vmap = {str(v.get("call_ref")): v for v in verdicts}
        for sc in calls:
            v = vmap.get(f"{sc.callee}:{sc.line}")
            if v is None or not v.get("is_sink"):
                continue
            soft_sinks.append(_to_soft_sink(sc, v))
    return soft_sinks, _aggregate_gaps(soft_sinks)
```

并在 `test_sink_discovery_llm.py` 顶部 import 补 `json`、`SinkCategory`、`SlotContext`：

```python
import json
import pytest
from shannon_core.code_index.models import FuncBlock
from shannon_core.code_index.parameter_models import SinkCategory, SlotContext
from shannon_core.code_index.sink_discovery_llm import (
    SuspiciousCall, collect_suspicious_calls, discover_sinks_llm, RuleGap,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_sink_discovery_llm.py -v`
Expected: PASS — 7 tests（3 from Task 1 + 4 new）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/sink_discovery_llm.py packages/core/tests/code_index/test_sink_discovery_llm.py
git commit -m "feat(code_index): add discover_sinks_llm (软 SinkCallSite + RuleGap) (spec Task 2)"
```

---

## Task 3: 接入 `build_code_index_with_gitnexus` + 写 `rule_gap_report.json`

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py:147-223`（build 函数）、`:226-243`（write_index_files）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:401-418`（run_code_index 解包 + 写 gap）
- Test: `packages/core/tests/code_index/test_sink_discovery_llm.py`（追加 integration smoke）

**Interfaces:**
- Consumes: Task 1 `collect_suspicious_calls`、Task 2 `discover_sinks_llm` / `RuleGap`。
- Produces: `build_code_index_with_gitnexus` 返回 `tuple[CodeIndex, list[RuleGap]]`（**返回类型变更**）；`write_index_files(index, output_dir, *, rule_gaps=None)` 写 `rule_gap_report.json`。

- [ ] **Step 1: Write failing integration test** — 追加到 `test_sink_discovery_llm.py`：

```python
async def test_soft_sink_flows_into_intra_hits():
    """软 sink 并入 sinks_by_func → analyze_taint_llm 能对其产 hits(集成 smoke)."""
    from collections import defaultdict
    from shannon_core.code_index.parameter_models import IntraResult
    from shannon_core.code_index import chain_propagator  # 仅验类型可达, 实际用 mock

    # 构造一个软 sink + 一个有它作 sink 的 block, 验证它进 sinks_by_func 后
    # analyze_taint_llm 的确定性 fallback 能命中它(is_entry_hint 或 indirect)。
    sc = _suspicious(arg="uid")  # uid 是参数 → is_entry_hint=True
    async def client(prompt, **kw):
        return json.dumps([{"call_ref": "raw_query:1", "is_sink": True,
                            "category": "sql", "slot": "sql_value", "arg_index": 0,
                            "rationale": "x"}])
    soft, gaps = await discover_sinks_llm([sc], client)
    assert soft and soft[0].dangerous_slots[0].is_entry_hint is True  # uid 是参数

    # 模拟 build 函数的 sinks_by_func 分组 + 确定性 intra
    sinks_by_func = defaultdict(list)
    for s in soft:
        sinks_by_func[s.caller_id].append(s)
    from shannon_core.code_index.llm_taint_analyzer import _deterministic_intra_fallback
    intra = _deterministic_intra_fallback(sc.block, sinks_by_func[sc.block.id])
    assert soft[0].id in intra.hits  # 软 sink 被 intra 命中 → 会进 TaintFlow
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_sink_discovery_llm.py::test_soft_sink_flows_into_intra_hits -v`
Expected: FAIL — `chain_propagator` import 或 `_suspicious` 作用域问题（_suspicious 是函数级定义；若 NameError，把 `_suspicious` 提到模块级）。先确认 `_suspicious` / `_block` 是模块级 helper（Task 2 已定义为模块级函数，OK）。FAIL 应来自 `soft[0].dangerous_slots[0].is_entry_hint`（Task 2 实现已支持）——若已 PASS 说明 Task 2 已覆盖，把此测试当回归保留。

- [ ] **Step 3: Wire into `build_code_index_with_gitnexus`** — 改 `code_index/__init__.py`。在 import 区（`:13` 附近）加：

```python
from shannon_core.code_index.sink_discovery_llm import (
    RuleGap,
    collect_suspicious_calls,
    discover_sinks_llm,
)
```

改 `build_code_index_with_gitnexus` 的 ③ sink detection 段（`:147-157`），在 `detect_sinks` 之后、`sinks_by_func` 之前插入补召回：

```python
    # ③ sink detection (规则)
    def _provide_source(block):
        return file_sources.get(block.file_path)
    sink_call_sites = detect_sinks(all_blocks, parser, source_provider=_provide_source)
    logger.info("Detected %d rule-based sink call sites", len(sink_call_sites))

    # ③b LLM sink 补召回 (spec §3.1): 规则未命中的可疑 call → 软 SinkCallSite
    suspicious = collect_suspicious_calls(all_blocks, parser, source_provider=_provide_source)
    soft_sinks, rule_gaps = await discover_sinks_llm(suspicious, llm_client)
    if soft_sinks:
        sink_call_sites = sink_call_sites + soft_sinks
        logger.info("LLM sink discovery added %d soft sinks (%d rule gaps)",
                    len(soft_sinks), len(rule_gaps))

    # ④ Group sinks by function (含软 sink)
    from collections import defaultdict
    sinks_by_func: dict[str, list] = defaultdict(list)
    for s in sink_call_sites:
        sinks_by_func[s.caller_id].append(s)
```

改函数签名返回类型（`:51-57`）与 return（`:209-223`）—— return 改为 `return CodeIndex(...), rule_gaps`：

```python
async def build_code_index_with_gitnexus(
    repo_path: str,
    *,
    mcp_client,
    llm_client,
    auto_index: bool = False,
) -> tuple[CodeIndex, list[RuleGap]]:
```

末尾：
```python
    # ⑧ Assemble CodeIndex
    return (
        CodeIndex(
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
        ),
        rule_gaps,
    )
```

- [ ] **Step 4: Update `write_index_files` to emit `rule_gap_report.json`** — 改 `:226`：

```python
def write_index_files(
    index: CodeIndex,
    output_dir: str,
    *,
    rule_gaps: list | None = None,
) -> tuple[Path, Path]:
    """Write code_index.json, code_index_summary.md, parameter_graph.json,
    and (if any) rule_gap_report.json."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "code_index.json"
    json_path.write_text(index.model_dump_json(indent=2))

    summary_path = out / "code_index_summary.md"
    summary_path.write_text(generate_summary(index))

    pgraph_path = out / "parameter_graph.json"
    if index.parameter_graph is not None:
        pgraph_path.write_text(index.parameter_graph.model_dump_json(indent=2))
    elif pgraph_path.exists():
        pgraph_path.unlink()

    # 旁路: 规则缺口报告(spec §3.1 层 2, 驱动规则库迭代, 不参与 taint/verdict)
    gap_path = out / "rule_gap_report.json"
    if rule_gaps:
        import json as _json
        gap_path.write_text(_json.dumps(
            [g if isinstance(g, dict) else g.__dict__ for g in rule_gaps],
            indent=2, ensure_ascii=False,
        ))
    elif gap_path.exists():
        gap_path.unlink()

    return json_path, summary_path
```

- [ ] **Step 5: Update `run_code_index` caller** — 改 `activities.py:401-418`：

```python
            try:
                async with GitNexusMCPClient(Path(repo)) as mcp:
                    index, rule_gaps = await build_code_index_with_gitnexus(
                        str(repo),
                        mcp_client=mcp,
                        llm_client=_llm_taint_client,
                        auto_index=False,
                    )
            except PentestError:
                raise
            except Exception as exc:
                raise PentestError(
                    f"GitNexus MCP query failed: {exc}. "
                    "Code index requires a working GitNexus MCP connection.",
                    category="code_index", error_code=ErrorCode.CODE_INDEX_FAILED,
                ) from exc

            json_path, summary_path = write_index_files(
                index, str(deliverables), rule_gaps=rule_gaps,
            )
```

- [ ] **Step 6: Run tests**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_sink_discovery_llm.py packages/core/tests/code_index/test_llm_taint_analyzer.py -v`
Expected: PASS。`test_llm_taint_analyzer.py` 回归不破（analyze_taint_llm 未改）。

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/code_index/__init__.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/core/tests/code_index/test_sink_discovery_llm.py
git commit -m "feat(code_index): wire discover_sinks_llm into build_code_index + rule_gap_report (spec Task 3)"
```

---

## Task 4: 接通两个 llm_client stub（`run_claude_prompt` + env 控制）

**Files:**
- Modify: `packages/core/src/shannon_core/config/concurrency.py`（加 `is_gitnexus_llm_enabled`）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:368-376`（intra stub）、`:710-721`（verdict stub）
- Test: `packages/core/tests/test_concurrency_config.py`（追加 env 测试）

**Interfaces:**
- Consumes: `run_claude_prompt`（`runner.py:90`）；`is_gitnexus_llm_enabled()`（本 task 新增，默认 True）。
- Produces: `_make_gitnexus_llm_client(repo_path) -> LLMClient`（封装 `run_claude_prompt` 成 `async (prompt)->str`）。两个 stub 在 env 关闭时仍 raise（触发降级），开启时调真 client。

- [ ] **Step 1: Write failing tests** — 追加到 `test_concurrency_config.py`：

```python
from shannon_core.config.concurrency import is_gitnexus_llm_enabled, is_llm_track_enabled


def test_gitnexus_llm_default_on(monkeypatch):
    monkeypatch.delenv("SHANNON_GITNEXUS_LLM_ENABLED", raising=False)
    assert is_gitnexus_llm_enabled() is True


def test_gitnexus_llm_off(monkeypatch):
    monkeypatch.setenv("SHANNON_GITNEXUS_LLM_ENABLED", "0")
    assert is_gitnexus_llm_enabled() is False


def test_llm_track_default_on(monkeypatch):
    monkeypatch.delenv("SHANNON_LLM_TRACK_ENABLED", raising=False)
    assert is_llm_track_enabled() is True


def test_llm_track_off(monkeypatch):
    monkeypatch.setenv("SHANNON_LLM_TRACK_ENABLED", "0")
    assert is_llm_track_enabled() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_concurrency_config.py -v -k "gitnexus_llm or llm_track"`
Expected: FAIL — `ImportError: cannot import name 'is_gitnexus_llm_enabled'`.

- [ ] **Step 3: Implement env readers** — 追加到 `concurrency.py`：

```python
def _is_truthy_env(name: str, default: bool) -> bool:
    """读布尔 env: '0'/'false'/'no' → False, 其余非空 → True, 未设 → default。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def is_llm_track_enabled() -> bool:
    """SHANNON_LLM_TRACK_ENABLED: 是否跑 LLM 轨(重型 vuln agent). 默认开(True)."""
    return _is_truthy_env("SHANNON_LLM_TRACK_ENABLED", default=True)


def is_gitnexus_llm_enabled() -> bool:
    """SHANNON_GITNEXUS_LLM_ENABLED: 是否接通 GitNexus 轨 LLM
    (discover_sinks_llm / analyze_taint_llm / chain_verdict). 默认开.
    关闭 → llm_client 走 raise → 各处降级到纯规则 + is_entry_hint(spec §3.3 边界)."""
    return _is_truthy_env("SHANNON_GITNEXUS_LLM_ENABLED", default=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_concurrency_config.py -v`
Expected: PASS — 全部（含原有 max_concurrent 测试）。

- [ ] **Step 5: Wire real client into `run_code_index`** — 改 `activities.py`。在文件顶部 import 区（`:17` 附近已有 `run_claude_prompt`）旁加配置 import：

```python
from shannon_core.config.concurrency import is_gitnexus_llm_enabled
```

改 `_llm_taint_client`（`:367-376`）为受 env 控制：

```python
            # Create LLM client for taint analysis (+ LLM sink discovery)
            def _make_gitnexus_llm_client(repo_path: str):
                """封装 run_claude_prompt 成 analyze_taint_llm/discover 期望的
                async (prompt)->str 契约。env 关时返回 raise-client 触发降级。"""
                if not is_gitnexus_llm_enabled():
                    async def _disabled(prompt: str, **kwargs) -> str:
                        raise RuntimeError(
                            "GitNexus LLM disabled (SHANNON_GITNEXUS_LLM_ENABLED=0); "
                            "using deterministic fallback"
                        )
                    return _disabled

                async def _client(prompt: str, **kwargs) -> str:
                    result = await run_claude_prompt(
                        prompt=prompt, repo_path=repo_path, model_tier="medium",
                    )
                    return result.text  # ClaudeRunResult.text (runner.py:77) = 纯文本输出
                return _client

            _llm_taint_client = _make_gitnexus_llm_client(str(repo))
```

> 注：`run_claude_prompt` 返回的 `ClaudeRunResult` 用 `.text`（`runner.py:77`）取纯文本——discover/intra/verdict 都按 JSON 字符串 prompt 走，与 `analyze_taint_llm` 的 `await llm_client(prompt)` → raw str 契约一致。

- [ ] **Step 6: Wire real client into verdict stub** — 改 `_gitnexus_verdict_llm_client`（`:710-721`）为接受 repo_path 的工厂（模块级保留一个默认 raise 的兜底，`run_gitnexus_chain_verdict` 内按 env 取）：

```python
async def _gitnexus_verdict_llm_client(prompt: str, **kwargs) -> str:
    """兜底: 未接通时 raise → judge_chain_verdict 走 needs_review保守路径。"""
    raise RuntimeError(
        "GitNexus-track chain-verdict LLM client not configured; "
        "judge_chain_verdict will mark candidates needs_review"
    )


def _make_verdict_llm_client(repo_path: str):
    """接通后: 真 client; env 关时返回 raise-client(降级)。"""
    if not is_gitnexus_llm_enabled():
        return _gitnexus_verdict_llm_client  # 模块级 raise 兜底
    from shannon_core.agents.runner import run_claude_prompt

    async def _client(prompt: str, **kwargs) -> str:
        result = await run_claude_prompt(
            prompt=prompt, repo_path=repo_path, model_tier="medium",
        )
        return result.text
    return _client
```

改 `run_gitnexus_chain_verdict` 内 `:780` `llm = _gitnexus_verdict_llm_client` → `llm = _make_verdict_llm_client(str(repo))`。

- [ ] **Step 7: Run regression tests**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_concurrency_config.py packages/core/tests/code_index/test_sink_discovery_llm.py packages/core/tests/code_index/test_llm_taint_analyzer.py -v`
Expected: PASS。stub 接通不破现有（测试都用 mock client，不走真 `run_claude_prompt`）。

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/shannon_core/config/concurrency.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/core/tests/test_concurrency_config.py
git commit -m "feat(code_index): wire run_claude_prompt into GitNexus llm_client stubs (SHANNON_GITNEXUS_LLM_ENABLED) (spec Task 4)"
```

---

## Task 5: LLM 轨开关 `SHANNON_LLM_TRACK_ENABLED`（默认开）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py:8-19`（PipelineInput）
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py:50-57`（注入）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:296-307`（开关判断）

**Interfaces:**
- Consumes: `is_llm_track_enabled()`（Task 4 已加）。
- Produces: `PipelineInput.enable_llm_track: bool = True`；workflow 在 `vuln_tasks` 循环前判断，关时不创建 vuln_tasks、merge 只消费 gitnexus queue。

- [ ] **Step 1: Add field to `PipelineInput`** — 改 `shared.py`，在 `max_concurrent`（`:19`）后加：

```python
    max_concurrent: int = 3                    # SHANNON_MAX_CONCURRENT 注入;vuln agents 并发上限
    enable_llm_track: bool = True              # SHANNON_LLM_TRACK_ENABLED 注入;False=只跑 GitNexus 轨
```

- [ ] **Step 2: Inject in CLI** — 改 `cli/main.py:50-57`，在 `PipelineInput(...)` 构造里加：

```python
    from shannon_core.config.concurrency import is_llm_track_enabled
    input = PipelineInput(
        ...
        max_concurrent=get_max_concurrent(),
        enable_llm_track=is_llm_track_enabled(),
    )
```

- [ ] **Step 3: Gate `vuln_tasks` in workflow** — 改 `workflows.py:296-307`，用 `if input.enable_llm_track:` 包住循环，并加日志：

```python
            if input.enable_llm_track:
                vuln_tasks: list[tuple[VulnType, AgentName, object]] = []
                for vt in selected_classes:
                    agent_name = AgentName(f"{vt}-vuln")
                    if agent_name.value not in self._state.completed_agents:
                        self._state.current_agent = agent_name.value
                        coro = workflow.execute_activity(
                            activities.run_vuln_agent,
                            ActivityInput(**{**act_input.__dict__, "agent_name": agent_name.value}),
                            start_to_close_timeout=timedelta(hours=2),
                            retry_policy=retry_for("vuln"),
                        )
                        vuln_tasks.append((vt, agent_name, coro))
            else:
                # LLM 轨关闭: 只跑 GitNexus 轨, merge 只消费 *_gitnexus_queue.json
                workflow.logger.info("llm_track=disabled (SHANNON_LLM_TRACK_ENABLED=0); "
                                     "running GitNexus track only")
                vuln_tasks = []
```

> temporal 确定性：`input.enable_llm_track` 在 workflow 启动前由 CLI 注入（同 `max_concurrent`），workflow 内不读 env，合规。

- [ ] **Step 4: Run tests / smoke**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_concurrency_config.py -v && python -c "from shannon_whitebox.pipeline.shared import PipelineInput; p=PipelineInput(repo_path='.'); assert p.enable_llm_track is True; print('PipelineInput OK')"`
Expected: PASS。`enable_llm_track` 默认 True。

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/shared.py packages/whitebox/src/shannon_whitebox/cli/main.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
git commit -m "feat(whitebox): LLM-track toggle SHANNON_LLM_TRACK_ENABLED (default on) (spec Task 5)"
```

---

## Task 6: 软 sink 下游白名单验证 + 回归

**Files:**
- Test: `packages/core/tests/code_index/test_sink_discovery_llm.py`（追加白名单断言）
- Verify: 现有 `test_sink_detector.py` / `test_llm_taint_analyzer.py` 回归

**Interfaces:**
- Consumes: `VALID_INJECTION_CATEGORIES`（`finding_models.py:28`，injection-recall 改动 1.2 D 引用）。
- Produces: 断言软 sink（`rule_id="llm-discovered"`、新 `sink_subtype`）过 injection 白名单、走完 builder 不被拒。

- [ ] **Step 1: 确认白名单过滤维度** — 白名单在 `packages/core/src/shannon_core/code_index/finding_models.py:28`（`VALID_INJECTION_CATEGORIES: set[str]`），按 **`VulnFinding.category`（vuln class，如 `"injection"`）** 过滤，**不是** `sink_subtype`。读 `finding_models.py` 的 `VulnFinding` validator + `injection_builder.py`，确认 builder 把 `SinkCallSite` → `VulnFinding(category="injection")`，软 sink 的 `sink_subtype`（新值）不进任何被白名单检查的字段。

Run: `cd /root/shannon-py && sed -n '28,95p' packages/core/src/shannon_core/code_index/finding_models.py && grep -n "category\|subtype\|VulnFinding\|VALID" packages/core/src/shannon_core/code_index/vuln_chain_builders/injection_builder.py`

- [ ] **Step 2: Write whitelist test** — 追加到 `test_sink_discovery_llm.py`：

```python
def test_soft_sink_does_not_break_injection_whitelist():
    """软 sink 经 injection_builder → VulnFinding.category='injection' 过白名单(spec §6).
    白名单按 vuln class(category)过滤、非按 sink_subtype —— 软 sink 的 rule_id/subtype
    不接触白名单维度, 故不被误拒。本测试锁定此不变量。"""
    from shannon_core.code_index.finding_models import VALID_INJECTION_CATEGORIES
    # injection builder 产出的 VulnFinding.category 恒为 vuln class('injection'),
    # 与 sink 的 rule_id/sink_subtype 无关:
    assert "injection" in VALID_INJECTION_CATEGORIES
    # 软 sink 的 SinkCategory(SQL)是 sink 级分类, 不等于 VulnFinding.category(vuln class),
    # 二者不混淆 → 白名单维度不接触软 sink 的 rule_id/subtype:
    sc = _suspicious(arg="uid")
    import asyncio
    async def client(prompt, **kw):
        return json.dumps([{"call_ref": "raw_query:1", "is_sink": True,
                            "category": "sql", "slot": "sql_value", "arg_index": 0,
                            "rationale": "x"}])
    soft, _ = asyncio.run(discover_sinks_llm([sc], client))
    assert soft[0].rule_id == "llm-discovered"
    assert soft[0].category.value == "sql"  # SinkCategory(sink 级), 非 VulnFinding.category
```

> 若 Step 1 发现 builder/validator **额外**按 `sink_subtype` 过滤（grep 见 `sink_subtype` 进白名单校验），则在本 task 加最小适配：把软 sink 的 subtype 归一化到现有 subtype，或扩展白名单。默认情况（仅按 category）无需改 builder。

- [ ] **Step 3: Run full relevant regression**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_sink_discovery_llm.py packages/core/tests/code_index/test_sink_detector.py packages/core/tests/code_index/test_llm_taint_analyzer.py packages/core/tests/test_concurrency_config.py -v`
Expected: PASS — 全绿。`test_sink_detector.py`（detect_sinks 未改）+ `test_llm_taint_analyzer.py`（analyze_taint_llm 未改）回归不破。

- [ ] **Step 4: Commit**

```bash
git add packages/core/tests/code_index/test_sink_discovery_llm.py
git commit -m "test(code_index): soft sink passes injection whitelist + regression (spec Task 6)"
```

---

## 真机冒烟（follow-up，人工）

1. 真实仓库跑 `run_code_index`（`SHANNON_GITNEXUS_LLM_ENABLED=1`），确认 `code_index.json` 出现 `rule_id="llm-discovered"` 条目、`rule_gap_report.json` 非空、`parameter_graph.json` 的 `taint_flows` 数较纯规则上涨。
2. `SHANNON_LLM_TRACK_ENABLED=0` 重跑，确认 vuln agent 不启动、`*_llm_queue.json` 不产、merge 只消费 `*_gitnexus_queue.json`、GitNexus 轨仍独立产 queue。
3. `SHANNON_GITNEXUS_LLM_ENABLED=0` 重跑，确认软 sink 为空、intra 走 `_deterministic_intra_fallback`（日志见 "using deterministic fallback"）、流程不崩。
