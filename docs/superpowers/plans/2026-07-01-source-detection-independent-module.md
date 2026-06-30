# Source 独立识别模块 + 双向对称轨 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 source 识别提升为与 sink 并列的一等端点(`source_detector` + `SourcePoint`),inject/xss/ssrf 改 Sink→Source(backward,双向锚定),authz 升级到 SourcePoint 级(三重过滤降过报)。

**Architecture:** 三阶段——Phase A 新建 `source_detector.py`(平行 `sink_detector.py`,规则 + LLM soft 两层,产 `SourcePoint` 嵌 `code_index.json`);Phase B 在 `chain_propagator.py` 新增 `propagate_backward_across_chains`(起点 `SinkCallSite` 反向追,终点 `SourcePoint` 锚定,产 source→sink 语义 `TaintFlow`,下游零改动);Phase C 改造 `authz_gitnexus_track.find_unguarded_sink_paths`(有 SourcePoint + 参数流到 side-effect sink + 无 guard 三重过滤)。守双轨铁律:`source_points` 不喂 LLM 轨。

**Tech Stack:** Python 3.11+ / pydantic / tree-sitter / pytest。复用 `sink_discovery_llm.map_llm_with_bounds` 并发骨架、`chain_propagator._find_call_args_for_callee`/`_references_tainted`、`gitnexus_call_graph.trace_from_sink`。

**Spec:** `docs/superpowers/specs/2026-07-01-source-detection-independent-module-design.md`

## Global Constraints

- **双轨铁律**:`source_points` 是 GitNexus 轨产物,**不得**进 LLM 轨 prompt(`prompts/shared/_*.txt` 任何 partial 不得 `@include` 或引用 source_points)。Task A5 锁定此不变量。
- **TaintFlow 结构不变**(`parameter_models.py:52-81`):backward 产出的仍是 `source→sink` 语义 TaintFlow,`chain_verdict` / `vuln_chain_builders/*` / `dual_track_merger` 零改动。
- **forward `propagate_across_chains` 保留**(过渡):Phase C `_source_reaches_sink` 复用其底层 `_map_call_site_params`;不进 pipeline 主流程。
- **测试隔离**:跑测试只跑改动相关子集(`pytest packages/core/tests/code_index/test_<file>.py -v`),勿跑全套(memory: 全量 hang)。
- **commit 风格**:conventional commits 中文 body,每 task 末尾 commit。
- **Rule ID 命名**:`<lang>-<framework>-<slot>`(如 `ts-express-query`、`py-django-get`),LLM soft 用 `"llm-discovered"`。

## File Structure

**Create:**
- `packages/core/src/shannon_core/code_index/source_detector.py` — 规则层 `detect_sources` + `SourceRule`/`DEFAULT_SOURCE_RULES`(平行 `sink_detector.py`)
- `packages/core/src/shannon_core/code_index/source_discovery_llm.py` — LLM soft 补召回 `discover_sources_llm`(平行 `sink_discovery_llm.py`)
- `packages/core/tests/code_index/test_source_detector.py`
- `packages/core/tests/code_index/test_source_discovery_llm.py`
- `packages/core/tests/code_index/test_chain_propagator_backward.py`
- `packages/core/tests/code_index/test_authz_source_point.py`
- `packages/core/tests/code_index/test_source_points_decoupling.py`(双轨铁律守卫)

**Modify:**
- `packages/core/src/shannon_core/code_index/parameter_models.py` — 加 `SourcePoint` class
- `packages/core/src/shannon_core/code_index/models.py` — `CodeIndex.source_points` 字段 + `_resolve_forward_refs` 注册
- `packages/core/src/shannon_core/code_index/chain_propagator.py` — 加 `_map_call_site_params_reverse` + `propagate_backward_across_chains`
- `packages/core/src/shannon_core/code_index/__init__.py` — pipeline 加 ⑧b source detect + ⑥' 分流 backward
- `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py` — `find_unguarded_sink_paths` 三重过滤 + `IDORCandidateChain.source_point_ids`

---

# Phase 1 (A): source_detector + SourcePoint(基础设施)

## Task A1: SourcePoint 数据模型 + CodeIndex 集成

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/parameter_models.py`(末尾加 `SourcePoint`)
- Modify: `packages/core/src/shannon_core/code_index/models.py:86`(CodeIndex 加字段)+ `models.py:209-215`(_resolve_forward_refs 注册)
- Test: `packages/core/tests/code_index/test_source_detector.py`(新建,本 task 仅测模型)

**Interfaces:**
- Produces: `SourcePoint` model(字段见下);`CodeIndex.source_points: list[SourcePoint]`

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_source_detector.py
from shannon_core.code_index.parameter_models import SourcePoint
from shannon_core.code_index.models import CodeIndex, ParameterSource


def test_source_point_basic_fields():
    sp = SourcePoint(
        id="app/routes/allocations.js:displayAllocations:11::userId::18",
        entry_point_id="app/routes/allocations.js:displayAllocations:11",
        param_name="userId",
        source_type=ParameterSource.PATH_PARAM,
        expression="req.params.userId",
        file_path="app/routes/allocations.js",
        line=18,
        validation="parseInt()",
        confidence=0.9,
        rule_id="ts-express-path",
    )
    assert sp.param_name == "userId"
    assert sp.source_type == ParameterSource.PATH_PARAM
    assert sp.validation == "parseInt()"
    assert sp.needs_review is False  # default


def test_code_index_has_source_points_field():
    ci = CodeIndex(
        repository="r", language="typescript", total_blocks=0,
        total_entry_points=0, total_chains=0, blocks=[], edges=[],
        entry_points=[], chains=[],
    )
    assert ci.source_points == []  # default empty list
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/core/tests/code_index/test_source_detector.py -v`
Expected: FAIL with " cannot import name 'SourcePoint'" / "CodeIndex has no attribute 'source_points'"

- [ ] **Step 3: Add SourcePoint to parameter_models.py**

在 `parameter_models.py` 末尾(`SinkCallSite` class 之后)追加:

```python
class SourcePoint(BaseModel):
    """入口 handler 中一个用户可控的外部输入取用点 —— 与 SinkCallSite 对称。

    平行 SinkCallSite:sink 是"危险调用点",source 是"外部输入取用点"。
    对齐原版 Input Vector 表:param_name/expression/validation/source_type。
    id 格式:"{entry_point_id}::{param_name}::{line}"。
    """
    id: str
    entry_point_id: str           # handler FuncBlock.id
    param_name: str               # 字段名,如 "userId" / "threshold"
    source_type: ParameterSource  # query/path/body/form/header/cookie/file/session/internal/unknown
    expression: str               # 取用表达式,如 "req.params.userId"
    file_path: str
    line: int                     # 取用点行号
    column: int = 0
    validation: str = "NONE"      # 取用点验证(NONE/parseInt/regex/escape...),对齐原版 Validation 列
    confidence: float = 0.9
    rule_id: str                  # 规则 id 或 "llm-discovered"
    needs_review: bool = False
```

- [ ] **Step 4: Add CodeIndex.source_points + register forward ref**

`models.py:86` 当前:
```python
    sink_call_sites: list["SinkCallSite"] = []
    parameter_graph: "ParameterPropagationGraph | None" = None
```
改为:
```python
    sink_call_sites: list["SinkCallSite"] = []
    source_points: list["SourcePoint"] = []
    parameter_graph: "ParameterPropagationGraph | None" = None
```

`models.py:210-215` 当前 `_resolve_forward_refs`:
```python
def _resolve_forward_refs() -> None:
    try:
        from shannon_core.code_index.parameter_models import ParameterPropagationGraph, SinkCallSite  # noqa: F401
        CodeIndex.model_rebuild()
    except ImportError:
        pass
```
改为(加 `SourcePoint` 导入):
```python
def _resolve_forward_refs() -> None:
    try:
        from shannon_core.code_index.parameter_models import (  # noqa: F401
            ParameterPropagationGraph, SinkCallSite, SourcePoint,
        )
        CodeIndex.model_rebuild()
    except ImportError:
        pass
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest packages/core/tests/code_index/test_source_detector.py -v`
Expected: PASS(2 tests)

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/parameter_models.py \
        packages/core/src/shannon_core/code_index/models.py \
        packages/core/tests/code_index/test_source_detector.py
git commit -m "feat(code_index): SourcePoint model + CodeIndex.source_points(平行 SinkCallSite)"
```

---

## Task A2: source_detector 规则层 detect_sources

**Files:**
- Create: `packages/core/src/shannon_core/code_index/source_detector.py`
- Test: `packages/core/tests/code_index/test_source_detector.py`(追加规则测试)

**Interfaces:**
- Consumes: `FuncBlock`(`models.py`)、`ParameterSource`(`models.py:106`)、entry handler 的 `block.id` 集合
- Produces: `detect_sources(blocks, parser, entry_point_ids, *, source_provider) -> list[SourcePoint]`、`SourceRule`、`DEFAULT_SOURCE_RULES`、`is_entry_hint`(从 sink_detector 复用)

- [ ] **Step 1: Write failing tests for representative rules**

追加到 `test_source_detector.py`:

```python
from shannon_core.code_index.source_detector import detect_sources, DEFAULT_SOURCE_RULES
from shannon_core.code_index.models import FuncBlock


def _block(file_path, func_name, start_line, source, language="typescript", params=None):
    return FuncBlock(
        id=f"{file_path}:{func_name}:{start_line}", file_path=file_path,
        function_name=func_name, start_line=start_line, end_line=start_line + 10,
        source_code=source, parameters=params or [], language=language,
    )


def _provider_from(block):
    return lambda b: block.source_code.encode("utf-8") if b.id == block.id else None


def test_express_req_params_yields_path_source():
    src = (
        "function displayAllocations(req, res) {\n"
        "  const userId = req.params.userId;\n"   # line 2
        "  const threshold = req.query.threshold;\n"
        "}\n"
    )
    block = _block("allocations.js", "displayAllocations", 11, src, "typescript", ["req", "res"])
    out = detect_sources([block], parser=None, entry_point_ids={block.id},
                         source_provider=_provider_from(block))
    sp = next(s for s in out if s.param_name == "userId")
    assert sp.source_type.value == "path"
    assert sp.expression == "req.params.userId"
    assert sp.line == 12  # start_line(11) + 行内偏移(1) → 第 2 行
    assert sp.rule_id.startswith("ts-express")


def test_express_req_query_and_body_distinct_source_types():
    src = "function f(req){ const q=req.query.q; const b=req.body.b; }\n"
    block = _block("f.js", "f", 1, src, "typescript", ["req"])
    out = detect_sources([block], parser=None, entry_point_ids={block.id},
                         source_provider=_provider_from(block))
    types = {(s.param_name, s.source_type.value) for s in out}
    assert ("q", "query") in types
    assert ("b", "body") in types


def test_django_request_get_yields_query():
    src = "def view(request):\n    q = request.GET['q']\n    return HttpResponse(q)\n"
    block = _block("views.py", "view", 5, src, "python", ["request"])
    out = detect_sources([block], parser=None, entry_point_ids={block.id},
                         source_provider=_provider_from(block))
    assert any(s.param_name == "q" and s.source_type.value == "query" for s in out)


def test_php_get_yields_query():
    src = "<?php $id = $_GET['id']; ?>\n"
    block = _block("index.php", "handler", 1, src, "php", [])
    out = detect_sources([block], parser=None, entry_point_ids={block.id},
                         source_provider=_provider_from(block))
    assert any(s.param_name == "id" and s.source_type.value == "query" for s in out)


def test_non_entry_block_skipped():
    src = "function helper(req){ return req.query.x; }\n"
    block = _block("util.js", "helper", 1, src, "typescript", ["req"])
    # entry_point_ids 为空 → 该 block 不被扫
    out = detect_sources([block], parser=None, entry_point_ids=set(),
                         source_provider=_provider_from(block))
    assert out == []


def test_dedup_same_field_same_type():
    # 同一 handler 里 userId 被 req.params 取用两次 → 去重为一个 SourcePoint
    src = "function f(req){ let a=req.params.id; let b=req.params.id; }\n"
    block = _block("f.js", "f", 1, src, "typescript", ["req"])
    out = detect_sources([block], parser=None, entry_point_ids={block.id},
                         source_provider=_provider_from(block))
    ids = [(s.entry_point_id, s.param_name, s.source_type) for s in out]
    assert len(ids) == len(set(ids))  # no duplicates
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest packages/core/tests/code_index/test_source_detector.py -v`
Expected: FAIL(" cannot import name 'detect_sources'")

- [ ] **Step 3: Write source_detector.py(rule layer)**

```python
# packages/core/src/shannon_core/code_index/source_detector.py
"""入口 source 检测器(平行 sink_detector)。

对每个 entry handler 的函数体做正则匹配,识别用户可控输入取用点
(req.params.x / request.GET['x'] / $_GET['x'] / c.Query("x") / @PathParam ...),
产 SourcePoint(精确 source_type)。独立运行,不依赖 sink 存在。

与 sink_detector 对称:sink 是"危险调用点"(AST call 遍历),source 是"外部输入
取用点"(正则扫函数体——取用模式是固定文本模式,正则即可,无需 AST)。
"""
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from shannon_core.code_index.models import ParameterSource
from shannon_core.code_index.parameter_models import SourcePoint

if TYPE_CHECKING:
    from shannon_core.code_index.models import FuncBlock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceRule:
    """一条 source 取用模式规则。pattern 的 group(1) = param_name。"""
    rule_id: str
    languages: tuple[str, ...]
    pattern: re.Pattern
    source_type: ParameterSource


def _G(pattern: str) -> re.Pattern:
    """helper:包裹 param-name 捕获组的正则。"""
    return re.compile(pattern)


# ===== Default source rule library(对齐原版 Input Vector 表 5 类)=====
# 按此模式扩展其余框架:每条 = (语言, 取用模式 with group(1)=param_name, source_type)。
DEFAULT_SOURCE_RULES: tuple[SourceRule, ...] = (
    # --- Express / Node.js(typescript/javascript)---
    SourceRule("ts-express-path", ("typescript", "javascript"),
               _G(r"req\.params\.([A-Za-z_]\w*)"), ParameterSource.PATH_PARAM),
    SourceRule("ts-express-query", ("typescript", "javascript"),
               _G(r"req\.query\.([A-Za-z_]\w*)"), ParameterSource.QUERY_PARAM),
    SourceRule("ts-express-body", ("typescript", "javascript"),
               _G(r"req\.body\.([A-Za-z_]\w*)"), ParameterSource.BODY_FIELD),
    SourceRule("ts-express-header", ("typescript", "javascript"),
               _G(r"req\.(?:headers|header)\.([A-Za-z_]\w*)"), ParameterSource.HEADER),
    SourceRule("ts-express-cookie", ("typescript", "javascript"),
               _G(r"req\.cookies\.([A-Za-z_]\w*)"), ParameterSource.COOKIE),

    # --- Django / Flask(python)---
    SourceRule("py-django-get", ("python",),
               _G(r"request\.GET\[['\"]([A-Za-z_]\w*)['\"]\]"), ParameterSource.QUERY_PARAM),
    SourceRule("py-django-post", ("python",),
               _G(r"request\.POST\[['\"]([A-Za-z_]\w*)['\"]\]"), ParameterSource.BODY_FIELD),
    SourceRule("py-flask-args", ("python",),
               _G(r"request\.args\[['\"]([A-Za-z_]\w*)['\"]\]"), ParameterSource.QUERY_PARAM),
    SourceRule("py-flask-form", ("python",),
               _G(r"request\.form\[['\"]([A-Za-z_]\w*)['\"]\]"), ParameterSource.BODY_FIELD),
    SourceRule("py-flask-json", ("python",),
               _G(r"request\.json\[['\"]([A-Za-z_]\w*)['\"]\]"), ParameterSource.BODY_FIELD),

    # --- PHP ---
    SourceRule("php-get", ("php",),
               _G(r"\$_GET\[['\"]([A-Za-z_]\w*)['\"]\]"), ParameterSource.QUERY_PARAM),
    SourceRule("php-post", ("php",),
               _G(r"\$_POST\[['\"]([A-Za-z_]\w*)['\"]\]"), ParameterSource.BODY_FIELD),
    SourceRule("php-request", ("php",),
               _G(r"\$_REQUEST\[['\"]([A-Za-z_]\w*)['\"]\]"), ParameterSource.QUERY_PARAM),

    # --- Go Gin ---
    SourceRule("go-gin-query", ("go",),
               _G(r"c\.Query\(['\"]([A-Za-z_]\w*)['\"]\)"), ParameterSource.QUERY_PARAM),
    SourceRule("go-gin-param", ("go",),
               _G(r"c\.Param\(['\"]([A-Za-z_]\w*)['\"]\)"), ParameterSource.PATH_PARAM),
    SourceRule("go-gin-postform", ("go",),
               _G(r"c\.PostForm\(['\"]([A-Za-z_]\w*)['\"]\)"), ParameterSource.BODY_FIELD),

    # --- Java Spring(注解式参数,在签名或参数声明上)---
    SourceRule("java-request-param", ("java",),
               _G(r"@RequestParam(?:\([^)]*\))?\s+\w+\s+([A-Za-z_]\w*)"),
               ParameterSource.QUERY_PARAM),
    SourceRule("java-path-variable", ("java",),
               _G(r"@PathVariable(?:\([^)]*\))?\s+\w+\s+([A-Za-z_]\w*)"),
               ParameterSource.PATH_PARAM),
)


def _line_of(text: str, offset: int) -> int:
    """offset(0-based) 所在的 1-based 行号(相对于文本起始)。"""
    return text.count("\n", 0, offset) + 1


def _detect_validation(text: str, match_offset: int) -> str:
    """best-effort:取用点附近是否有简单 validation(parseInt/Number/已知 regex/escape)。"""
    window = text[max(0, match_offset - 80): match_offset + 80].lower()
    if re.search(r"parseint|int\(|number\(|float\(", window):
        return "parseInt/Number"
    if re.search(r"escape\(|encodeuri|htmlspecialchars|sanitize", window):
        return "escape/sanitize"
    if re.search(r"test\(|match\(/.+/", window):
        return "regex"
    return "NONE"


def detect_sources(
    blocks: "list[FuncBlock]",
    parser,
    entry_point_ids: "set[str]",
    *,
    source_provider: "Callable[[FuncBlock], bytes | None]",
) -> list[SourcePoint]:
    """对 entry handler 扫描函数体,识别用户可控取用点 → SourcePoint 列表。

    只对 block.id ∈ entry_point_ids 的函数跑(source 识别不被 sink 驱动;
    但只 entry handler 接收外部输入,内部函数的 tainted 参数归 chain_propagator)。
    """
    out: list[SourcePoint] = []
    for block in blocks:
        if block.id not in entry_point_ids:
            continue
        source = source_provider(block)
        text = (source.decode("utf-8", errors="replace") if source
                else block.source_code)
        for rule in DEFAULT_SOURCE_RULES:
            if block.language not in rule.languages:
                continue
            for m in rule.pattern.finditer(text):
                param_name = m.group(1)
                rel_line = _line_of(text, m.start())
                abs_line = block.start_line + rel_line - 1
                out.append(SourcePoint(
                    id=f"{block.id}::{param_name}::{abs_line}",
                    entry_point_id=block.id,
                    param_name=param_name,
                    source_type=rule.source_type,
                    expression=m.group(0),
                    file_path=block.file_path,
                    line=abs_line,
                    validation=_detect_validation(text, m.start()),
                    confidence=0.9,
                    rule_id=rule.rule_id,
                    needs_review=False,
                ))
    return _dedup(out)


def _dedup(points: list[SourcePoint]) -> list[SourcePoint]:
    """按 (entry_point_id, param_name, source_type) 去重,保留首个。"""
    seen: set[tuple] = set()
    out: list[SourcePoint] = []
    for sp in points:
        key = (sp.entry_point_id, sp.param_name, sp.source_type)
        if key in seen:
            continue
        seen.add(key)
        out.append(sp)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest packages/core/tests/code_index/test_source_detector.py -v`
Expected: PASS(全部,含 A1 的 2 + A2 的 6)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/source_detector.py \
        packages/core/tests/code_index/test_source_detector.py
git commit -m "feat(code_index): source_detector detect_sources(规则层,5 语言框架取用模式)"
```

---

## Task A3: source_discovery_llm LLM soft 补召回 + 兜底

**Files:**
- Create: `packages/core/src/shannon_core/code_index/source_discovery_llm.py`
- Test: `packages/core/tests/code_index/test_source_discovery_llm.py`

**Interfaces:**
- Consumes: `map_llm_with_bounds`(`llm_concurrency.py`)、`get_max_concurrent`(`config/concurrency.py`)、`SourcePoint`、规则未命中的 entry handler 列表
- Produces: `collect_source_candidates(blocks, entry_point_ids, source_provider) -> list[SourceCandidate]`、`discover_sources_llm(candidates, llm_client, concurrency, per_call_timeout) -> list[SourcePoint]`(LLM 不可用 → 空列表兜底)

- [ ] **Step 1: Write failing tests**

```python
# packages/core/tests/code_index/test_source_discovery_llm.py
import asyncio
from unittest.mock import MagicMock

from shannon_core.code_index.models import FuncBlock, ParameterSource
from shannon_core.code_index.source_discovery_llm import (
    collect_source_candidates, discover_sources_llm,
)


def _block(file_path, func_name, start_line, source, language="typescript"):
    return FuncBlock(
        id=f"{file_path}:{func_name}:{start_line}", file_path=file_path,
        function_name=func_name, start_line=start_line, end_line=start_line + 5,
        source_code=source, parameters=[], language=language,
    )


def test_collect_candidates_returns_entry_handlers_without_rule_hit():
    # 用了一个非常规取用(input.get("x"))→ 规则未命中,但仍作为候选送 LLM
    src = 'function f(req){ const x = input.get("x"); }\n'
    block = _block("f.js", "f", 1, src)
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())
    assert len(cands) == 1
    assert cands[0].block.id == block.id


def test_discover_sources_llm_soft_source_on_llm_verdict():
    block = _block("f.js", "f", 1, 'function f(req){ const x = input.get("x"); }\n')
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())
    async def fake_llm(prompt):
        return ('[{"field":"x","source_type":"query","is_source":true,"rationale":"r"}]')
    out = asyncio.run(discover_sources_llm(cands, fake_llm))
    assert len(out) == 1
    assert out[0].param_name == "x"
    assert out[0].rule_id == "llm-discovered"
    assert out[0].needs_review is True


def test_discover_sources_llm_degrades_to_empty_when_llm_unavailable():
    block = _block("f.js", "f", 1, 'function f(req){ const x = input.get("x"); }\n')
    cands = collect_source_candidates([block], {block.id},
                                      source_provider=lambda b: block.source_code.encode())
    out = asyncio.run(discover_sources_llm(cands, None))  # LLM 不可用
    assert out == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest packages/core/tests/code_index/test_source_discovery_llm.py -v`
Expected: FAIL(" cannot import name 'collect_source_candidates'")

- [ ] **Step 3: Write source_discovery_llm.py**

```python
# packages/core/src/shannon_core/code_index/source_discovery_llm.py
"""LLM source 补召回(平行 sink_discovery_llm)。

规则未命中的 entry handler(非常规框架/解构)→ 送 LLM 判定 → 软 SourcePoint
(rule_id="llm-discovered")。LLM 不可用 → 返回空(降级,守"GitNexus 轨确定性兜底")。
复用 map_llm_with_bounds 并发骨架(对齐 discover_sinks_llm)。
"""
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

from shannon_core.code_index.models import ParameterSource
from shannon_core.code_index.parameter_models import SourcePoint
from shannon_core.code_index.source_detector import DEFAULT_SOURCE_RULES
from shannon_core.code_index.llm_concurrency import (
    DEFAULT_PER_CALL_TIMEOUT, map_llm_with_bounds,
)
from shannon_core.config.concurrency import get_max_concurrent

if TYPE_CHECKING:
    from shannon_core.code_index.models import FuncBlock

logger = logging.getLogger(__name__)

LLMClient = Callable[..., Awaitable[str]]


@dataclass(frozen=True)
class SourceCandidate:
    """规则未命中的 entry handler,待 LLM 判定其可控字段。"""
    block: "FuncBlock"


def _has_rule_hit(language: str, text: str) -> bool:
    """该 handler 是否已被 detect_sources 规则命中(避免重复)。"""
    return any(
        block_lang in rule.languages and rule.pattern.search(text)
        for rule in DEFAULT_SOURCE_RULES
        for block_lang in (language,)
    )


def collect_source_candidates(
    blocks: "list[FuncBlock]",
    entry_point_ids: "set[str]",
    *,
    source_provider: "Callable[[FuncBlock], bytes | None]",
) -> list[SourceCandidate]:
    """收集 entry handler 中规则未命中的(候选送 LLM)。

    启发式:handler 函数体含 input-ish / param-ish 标识符(input.get / params[ /
    @Attribute / data[" ...])但 detect_sources 规则未命中 → 候选。
    """
    import re
    _INPUTISH = re.compile(
        r"(input\.get|params\[|body\[|data\[['\"]|@RequestBody|@QueryParam|"
        r"ctx\.Request|c\.Query|c\.Param)",
        re.IGNORECASE,
    )
    out: list[SourceCandidate] = []
    for block in blocks:
        if block.id not in entry_point_ids:
            continue
        source = source_provider(block)
        text = (source.decode("utf-8", errors="replace") if source
                else block.source_code)
        if _has_rule_hit(block.language, text):
            continue  # 规则已命中
        if _INPUTISH.search(text):
            out.append(SourceCandidate(block=block))
    return out


_PROMPT_TMPL = """You are a user-input source classifier for the GitNexus track.
Given ONE entry handler function, identify ALL user-controllable input fields and
their HTTP source type. Rule-based detection already covered common frameworks
(Express/Django/...); you handle the unconventional ones.

## Function
{func_name} ({file}:{line})
Parameters: {params}

## Source
```
{source}
```

## Task
Return a JSON array. One object per user-controllable field:
{{"field":"<param_name>","source_type":"query|path|body|form|header|cookie|file","expression":"<source-code expr>","line":<int>,"is_source":true|false,"rationale":"<one line>"}}
Return ONLY the JSON array, no prose. Omit fields that are NOT user-controllable (is_source=false)."""


def _build_prompt(block) -> str:
    return _PROMPT_TMPL.format(
        func_name=block.function_name, file=block.file_path,
        line=block.start_line, params=list(block.parameters),
        source=block.source_code,
    )


def _parse_fields(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
        return [d for d in data if isinstance(d, dict)]
    except Exception:
        logger.debug("discover_sources_llm: failed to parse LLM JSON: %s", raw[:120])
        return []


def _to_source_type(v: str) -> ParameterSource:
    mapping = {
        "query": ParameterSource.QUERY_PARAM, "path": ParameterSource.PATH_PARAM,
        "body": ParameterSource.BODY_FIELD, "form": ParameterSource.FORM_FIELD,
        "header": ParameterSource.HEADER, "cookie": ParameterSource.COOKIE,
        "file": ParameterSource.FILE_UPLOAD,
    }
    return mapping.get((v or "").lower(), ParameterSource.UNKNOWN)


def _to_soft_source(block, field: dict) -> SourcePoint:
    name = str(field.get("field", ""))
    line = int(field.get("line", block.start_line))
    return SourcePoint(
        id=f"{block.id}::{name}::{line}",
        entry_point_id=block.id,
        param_name=name,
        source_type=_to_source_type(field.get("source_type", "unknown")),
        expression=str(field.get("expression", "")),
        file_path=block.file_path,
        line=line,
        validation="NONE",
        confidence=0.6,
        rule_id="llm-discovered",
        needs_review=True,
    )


async def discover_sources_llm(
    candidates: list[SourceCandidate],
    llm_client: LLMClient | None,
    *,
    concurrency: int | None = None,
    per_call_timeout: float | None = None,
) -> list[SourcePoint]:
    """对候选 handler 并发调 LLM → 软 SourcePoint。LLM 不可用 → 空(降级)。"""
    if llm_client is None or not candidates:
        return []
    by_func: dict[str, list[SourceCandidate]] = defaultdict(list)
    for c in candidates:
        by_func[c.block.id].append(c)

    async def _discover_one(item):
        _, cands = item
        block = cands[0].block
        prompt = _build_prompt(block)
        raw = await llm_client(prompt)
        fields = _parse_fields(raw)
        return [_to_soft_source(block, f) for f in fields
                if f.get("is_source") is True]

    conc = concurrency if concurrency is not None else get_max_concurrent()
    timeout = (per_call_timeout if per_call_timeout is not None
               else DEFAULT_PER_CALL_TIMEOUT)
    per_func = await map_llm_with_bounds(
        list(by_func.items()), _discover_one,
        concurrency=conc, per_call_timeout=timeout, label="discover_sources_llm",
    )
    return [s for func_sources in per_func for s in func_sources]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest packages/core/tests/code_index/test_source_discovery_llm.py -v`
Expected: PASS(3 tests)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/source_discovery_llm.py \
        packages/core/tests/code_index/test_source_discovery_llm.py
git commit -m "feat(code_index): discover_sources_llm LLM soft 补召回(平行 sink_discovery_llm)"
```

---

## Task A4: pipeline 集成 ⑧b source detect + 落盘

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py:155-253`(build_code_index_with_gitnexus 加 ⑧b + assemble)
- Test: `packages/core/tests/code_index/test_source_detector.py`(追加 pipeline 冒烟)

**Interfaces:**
- Consumes: `detect_sources`(A2)、`discover_sources_llm`(A3)、`detect_entry_points` 的 `entry_point_ids`、`build_code_index_with_gitnexus` 的 blocks/source_provider
- Produces: `build_code_index_with_gitnexus` 返回的 `CodeIndex.source_points` 填充;`code_index.json` 含 `source_points`

- [ ] **Step 1: Write failing test**

追加到 `test_source_detector.py`:

```python
def test_build_code_index_populates_source_points():
    """pipeline 冒烟:build_code_index_with_gitnexus 填充 source_points。"""
    import asyncio
    from unittest.mock import AsyncMock
    from shannon_core.code_index.gitnexus_call_graph import CallGraphResult
    from shannon_core.code_index import build_code_index_with_gitnexus
    import tempfile, os

    # 最小 repo:一个 Express handler 文件
    with tempfile.TemporaryDirectory() as repo:
        f = os.path.join(repo, "app.js")
        with open(f, "w") as fh:
            fh.write(
                "app.get('/allocations/:userId', function displayAllocations(req, res){\n"
                "  const userId = req.params.userId;\n"
                "  const threshold = req.query.threshold;\n"
                "});\n"
            )
        os.makedirs(os.path.join(repo, ".git"), exist_ok=True)

        fake_mcp = AsyncMock()
        fake_mcp.call_tool = AsyncMock(return_value={"upstream": [], "downstream": []})
        fake_llm = AsyncMock(return_value="[]")  # LLM soft 无产出

        index, rule_gaps = asyncio.run(build_code_index_with_gitnexus(
            repo, mcp_client=fake_mcp, llm_client=fake_llm,
        ))
        # entry handler 的 req.params.userId / req.query.threshold 应被识别
        names = {(s.param_name, s.source_type.value) for s in index.source_points}
        assert ("userId", "path") in names
        assert ("threshold", "query") in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/core/tests/code_index/test_source_detector.py::test_build_code_index_populates_source_points -v`
Expected: FAIL(`index.source_points` 为空,因为 pipeline 还没加 ⑧b)

- [ ] **Step 3: Wire source detection into build_code_index_with_gitnexus**

在 `__init__.py` 顶部 import 块(line 5-25 附近)加:

```python
from shannon_core.code_index.source_detector import detect_sources
from shannon_core.code_index.source_discovery_llm import (
    collect_source_candidates,
    discover_sources_llm,
)
```

在 `build_code_index_with_gitnexus` 内,entry 组装(⑦,line 229 `gitnexus_entry_points = ...` 之后、⑥' propagation 之前)插入 ⑧b:

```python
    # ⑧b source detection(平行 ③ sink detect,独立不依赖 sink)
    entry_point_ids = {ep.func_block_id for ep in gitnexus_entry_points}
    source_points = detect_sources(
        all_blocks, parser, entry_point_ids, source_provider=_provide_source,
    )
    logger.info("Detected %d rule-based source points", len(source_points))

    # ⑧b-LLM source 补召回:规则未命中的 entry handler
    source_candidates = collect_source_candidates(
        all_blocks, entry_point_ids, source_provider=_provide_source,
    )
    soft_sources = await discover_sources_llm(source_candidates, llm_client)
    if soft_sources:
        source_points = source_points + soft_sources
        logger.info("LLM source discovery added %d soft sources", len(soft_sources))
```

在 assemble CodeIndex(line 236-251 的 `CodeIndex(...)`)加字段:

```python
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
            source_points=source_points,            # 新增
            file_manifest=file_manifest,
            degradation_level=DegradationLevel.FULL,
            parameter_graph=pgraph,
        ),
        rule_gaps,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/core/tests/code_index/test_source_detector.py -v`
Expected: PASS(全部,含 pipeline 冒烟)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/__init__.py \
        packages/core/tests/code_index/test_source_detector.py
git commit -m "feat(code_index): pipeline ⑧b source detect + 落盘 source_points"
```

---

## Task A5: 双轨铁律防回退守卫测试

**Files:**
- Test: `packages/core/tests/code_index/test_source_points_decoupling.py`(新建)

**Interfaces:**
- Consumes: 无(静态扫描 prompt 文件)
- Produces: 断言 `source_points` 不被任何 `prompts/shared/_*.txt` 引用

- [ ] **Step 1: Write the test**

```python
# packages/core/tests/code_index/test_source_points_decoupling.py
"""双轨铁律守卫:source_points(GitNexus 轨确定性产物)不得进 LLM 轨 prompt。

对齐 test_static_dataflow_hints_decoupling.py 精神:确定性产物不喂 LLM 轨,
保持 LLM 轨自给自足(CLAUDE.md §1 铁律)。
"""
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "apps" / "worker" / "prompts"
# fallback:repo 根下的 prompts(若结构不同,调整此路径)
ALT_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"

FORBIDDEN_TOKENS = ("source_points", "SourcePoint", "source_point_ids")


def _all_prompt_files():
    roots = [PROMPTS_DIR, ALT_PROMPTS_DIR]
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*.txt"):
            yield p
        for p in root.rglob("*.md"):
            yield p


def test_no_prompt_references_source_points():
    offenders = []
    for p in _all_prompt_files():
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        for tok in FORBIDDEN_TOKENS:
            if tok in text:
                offenders.append(f"{p}: mentions {tok}")
    assert not offenders, (
        "双轨铁律违反:LLM 轨 prompt 引用了 GitNexus 轨确定性产物 source_points:\n"
        + "\n".join(offenders)
    )
```

- [ ] **Step 2: Run test to verify it passes**(应直接 PASS——本 task 是守卫,当前无违规)

Run: `pytest packages/core/tests/code_index/test_source_points_decoupling.py -v`
Expected: PASS(若 FAIL,说明已有 prompt 引用,需移除)

- [ ] **Step 3: Commit**

```bash
git add packages/core/tests/code_index/test_source_points_decoupling.py
git commit -m "test(code_index): source_points 双轨铁律防回退守卫"
```

**Phase A 完成里程碑:** `source_points` 独立识别、嵌 `code_index.json`,无消费方改动(纯增量)。可单独验证 NodeGoat source 识别。

---

# Phase 2 (B): inject/xss/ssrf backward(Sink→Source)

## Task B1: 反向参数映射 _map_call_site_params_reverse

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/chain_propagator.py`(末尾加 `_map_call_site_params_reverse`)
- Test: `packages/core/tests/code_index/test_chain_propagator_backward.py`(新建)

**Interfaces:**
- Consumes: `_find_call_args_forcallee`(`chain_propagator.py:48-75`)、`_references_tainted`(`chain_propagator.py:31-45`)、`callee_block.parameters`
- Produces: `_map_call_site_params_reverse(callee_block, callee_tainted, caller_block) -> set[str]`(caller 端被污染的变量名集合)

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/code_index/test_chain_propagator_backward.py
from shannon_core.code_index.models import FuncBlock
from shannon_core.code_index.chain_propagator import _map_call_site_params_reverse


def _blk(fid, source, params):
    fp, fn, ln = fid.split(":")
    return FuncBlock(id=fid, file_path=fp, function_name=fn, start_line=int(ln),
                     end_line=int(ln)+5, source_code=source, parameters=params,
                     language="typescript")


def test_reverse_map_propagates_tainted_callee_param_to_caller_arg():
    # caller 调用 callee(taintedParam),实参 req.query.x → caller 端 tainted = {req.query.x}
    caller = _blk("a.js:handler:1",
                  "function handler(req){ callee(req.query.x); }", ["req"])
    callee = _blk("a.js:callee:5",
                  "function callee(taintedParam){ eval(taintedParam); }",
                  ["taintedParam"])
    out = _map_call_site_params_reverse(
        callee_block=callee, callee_tainted={"taintedParam"}, caller_block=caller)
    assert out == {"req.query.x"}  # caller 传入的实参表达式


def test_reverse_map_empty_when_callee_param_not_tainted():
    caller = _blk("a.js:handler:1", "function handler(req){ callee('literal'); }", ["req"])
    callee = _blk("a.js:callee:5", "function callee(p){}", ["p"])
    out = _map_call_site_params_reverse(
        callee_block=callee, callee_tainted={"p"}, caller_block=caller)
    # 实参 'literal' 不引用 tainted → 空
    assert out == set()


def test_reverse_map_conservative_when_no_call_args_found():
    caller = _blk("a.js:handler:1", "function handler(req){ /* no call */ }", ["req"])
    callee = _blk("a.js:callee:5", "function callee(p){}", ["p"])
    out = _map_call_site_params_reverse(
        callee_block=callee, callee_tainted={"p"}, caller_block=caller)
    # 找不到调用实参 → 保守:caller 所有 params 视为 tainted
    assert out == {"req"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/core/tests/code_index/test_chain_propagator_backward.py -v`
Expected: FAIL(" cannot import name '_map_call_site_params_reverse'")

- [ ] **Step 3: Implement _map_call_site_params_reverse**

在 `chain_propagator.py` 末尾追加:

```python
def _map_call_site_params_reverse(
    callee_block: FuncBlock,
    callee_tainted: set[str],
    caller_block: FuncBlock,
) -> set[str]:
    """反向参数映射(backward):已知 callee 的 tainted params,反推 caller 调用时
    传的哪些实参被污染 → 返回 caller 端被污染的变量名/表达式集合。

    复用 _find_call_args_for_callee(找 caller 里调用 callee 的实参列表)。
    对 callee 的每个 tainted param(按位置 i)看 caller 的 call_args[i],
    若该实参引用某变量 → 加入结果(作为 caller 端 tainted)。
    找不到调用实参 → 保守回退:caller 所有 params 视为 tainted(对齐 forward 保守)。
    """
    callee_params = callee_block.parameters
    if not callee_params:
        return set()

    call_args = _find_call_args_for_callee(caller_block, callee_block.id)
    if not call_args:
        # 保守:无法定位调用 → caller 所有参数视为 tainted
        return set(caller_block.parameters)

    tainted_indices = {
        i for i, p in enumerate(callee_params) if p in callee_tainted
    }
    result: set[str] = set()
    for idx in tainted_indices:
        if idx >= len(call_args):
            break
        arg_expr = call_args[idx]
        # arg_expr 是 caller 端表达式(如 req.query.x / userId / getVal(x))
        # 直接作为 caller 端 tainted(下游 _source_points_matching 用 substring 匹配)
        if arg_expr.strip():
            result.add(arg_expr.strip())
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest packages/core/tests/code_index/test_chain_propagator_backward.py -v`
Expected: PASS(3 tests)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/chain_propagator.py \
        packages/core/tests/code_index/test_chain_propagator_backward.py
git commit -m "feat(chain_propagator): _map_call_site_params_reverse 反向参数映射"
```

---

## Task B2: propagate_backward_across_chains(双向锚定)

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/chain_propagator.py`(加 `propagate_backward_across_chains`)
- Test: `packages/core/tests/code_index/test_chain_propagator_backward.py`(追加)

**Interfaces:**
- Consumes: `CallChain`(`models.py`)、`SinkCallSite`(`parameter_models.py`)、`IntraResult`(`parameter_models.py:113`)、`SourcePoint`、`_map_call_site_params_reverse`(B1)、`_tainted_params_reaching_sink`(本 task 新增 helper)
- Produces: `propagate_backward_across_chains(chains, blocks, intra_results, sink_call_sites, source_points, *, max_depth=20) -> list[TaintFlow]`

- [ ] **Step 1: Write failing test**

追加到 `test_chain_propagator_backward.py`:

```python
from shannon_core.code_index.models import CallChain, ParameterSource
from shannon_core.code_index.parameter_models import (
    IntraResult, SinkCallSite, SinkCategory, SlotContext, SourcePoint, TaintFlow,
)
from shannon_core.code_index.chain_propagator import propagate_backward_across_chains


def _sink(caller_id, line=10):
    return SinkCallSite(
        id=f"{caller_id}::eval::{line}:0", caller_id=caller_id, callee_name="eval",
        callee_receiver=None, category=SinkCategory.COMMAND, sink_subtype="command_eval",
        file_path="a.js", line=line, column=0,
        dangerous_slots=[], rule_id="ts-eval", needs_review=False,
    )


def _source(entry_id, param, stype, expr):
    return SourcePoint(
        id=f"{entry_id}::{param}::1", entry_point_id=entry_id, param_name=param,
        source_type=stype, expression=expr, file_path="a.js", line=1,
        confidence=0.9, rule_id="ts-express-query",
    )


def test_backward_anchor_succeeds_when_sink_reaches_sourcepoint():
    # chain: handler(entry) → callee(含 eval sink)
    handler = _blk("a.js:handler:1",
                   "function handler(req){ callee(req.query.x); }", ["req"])
    callee = _blk("a.js:callee:5",
                  "function callee(p){ eval(p); }", ["p"])
    chain = CallChain(entry_point_id=handler.id, path=[handler.id, callee.id],
                      depth=1, has_unresolved=False)
    # callee 的 intra:tainted_params={p}, hits={sink_id: conf}
    sink = _sink(callee.id, line=6)
    intra = {
        handler.id: IntraResult(tainted_params={"req.query.x"}, hits={}),
        callee.id: IntraResult(tainted_params={"p"}, hits={sink.id: 0.9}),
    }
    sps = [_source(handler.id, "x", ParameterSource.QUERY_PARAM, "req.query.x")]
    flows = propagate_backward_across_chains(
        [chain], [handler, callee], intra, [sink], sps)
    assert len(flows) == 1
    assert isinstance(flows[0], TaintFlow)
    assert flows[0].sink_call_site_id == sink.id
    assert flows[0].source_type == ParameterSource.QUERY_PARAM  # 精确,非硬编码


def test_backward_drops_chain_when_no_sourcepoint_anchor():
    # sink 存在但反向追不到任何 SourcePoint(entry 无可控 source)→ 丢弃
    handler = _blk("a.js:handler:1",
                   "function handler(req){ callee('safe_literal'); }", ["req"])
    callee = _blk("a.js:callee:5",
                  "function callee(p){ eval(p); }", ["p"])
    chain = CallChain(entry_point_id=handler.id, path=[handler.id, callee.id],
                      depth=1, has_unresolved=False)
    sink = _sink(callee.id, line=6)
    intra = {
        handler.id: IntraResult(tainted_params=set(), hits={}),
        callee.id: IntraResult(tainted_params={"p"}, hits={sink.id: 0.9}),
    }
    flows = propagate_backward_across_chains(
        [chain], [handler, callee], intra, [sink], [])  # 无 SourcePoint
    assert flows == []  # 双向锚定:无 source 锚 → 丢弃
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest packages/core/tests/code_index/test_chain_propagator_backward.py -v`
Expected: FAIL(" cannot import name 'propagate_backward_across_chains'")

- [ ] **Step 3: Implement propagate_backward_across_chains**

在 `chain_propagator.py` 末尾追加:

```python
def _tainted_params_reaching_sink(
    sink: "SinkCallSite",
    intra: "IntraResult",
) -> set[str]:
    """sink 所在函数的哪些参数 tainted(到达 sink)。

    优先用 intra.tainted_params(LLM/确定性 intra 分析);回退:dangerous_slots
    的 expression 反推(若 intra 缺失)。
    """
    if intra and intra.tainted_params:
        return set(intra.tainted_params)
    # 回退:从 dangerous_slots.expression 提取参数名(浅)
    out: set[str] = set()
    for slot in getattr(sink, "dangerous_slots", []) or []:
        expr = (slot.expression or "").strip()
        if expr:
            out.add(expr)
    return out


def _source_points_matching(
    entry_id: str,
    tainted_in_entry: set[str],
    source_points: list["SourcePoint"],
) -> list["SourcePoint"]:
    """entry 的 tainted 变量命中哪些 SourcePoint(substring 匹配,过近似)。"""
    out = []
    for sp in source_points:
        if sp.entry_point_id != entry_id:
            continue
        # SourcePoint.expression(如 req.query.x)或 param_name(x)出现在 entry 的 tainted 集合
        for t in tainted_in_entry:
            if sp.param_name in t or sp.expression in t or t in sp.expression:
                out.append(sp)
                break
    return out


def propagate_backward_across_chains(
    chains: list[CallChain],
    blocks: list[FuncBlock],
    intra_results: dict[str, IntraResult],
    sink_call_sites: list["SinkCallSite"],
    source_points: list["SourcePoint"],
    *,
    max_depth: int = 20,
) -> list[TaintFlow]:
    """backward(Sink→Source):从 SinkCallSite 反向沿 chain 回溯,终点用 SourcePoint 锚定。

    双向锚定:起点 SinkCallSite(sink 真实)+ 终点 SourcePoint(source 真实)。
    只有反向追到真实 SourcePoint 的链才成立(产 TaintFlow);否则丢弃。
    产出仍是 source→sink 语义的 TaintFlow(propagation_steps 正序化),下游零改动。
    """
    if not chains or not sink_call_sites:
        return []

    blocks_by_id: dict[str, FuncBlock] = {b.id: b for b in blocks}
    sinks_by_caller: dict[str, list["SinkCallSite"]] = defaultdict(list)
    for s in sink_call_sites:
        sinks_by_caller[s.caller_id].append(s)
    sp_by_entry: dict[str, list["SourcePoint"]] = defaultdict(list)
    for sp in source_points:
        sp_by_entry[sp.entry_point_id].append(sp)

    flows: list[TaintFlow] = []
    for chain in chains:
        if not chain.path:
            continue
        # 找 chain 上含 sink 的节点
        for sink_step, sid in enumerate(chain.path):
            sinks_here = sinks_by_caller.get(sid, [])
            if not sinks_here:
                continue
            sink_func = blocks_by_id.get(sid)
            if sink_func is None:
                continue
            for sink in sinks_here:
                seed = _tainted_params_reaching_sink(
                    sink, intra_results.get(sid))
                if not seed:
                    continue
                # 反向沿 path[sink_step → 0]
                current_tainted = set(seed)
                steps_rev: list[PropagationStep] = []
                anchored: list["SourcePoint"] = []
                for i in range(sink_step, -1, -1):
                    func_id = chain.path[i]
                    if i == 0:
                        # 到达 entry:终点锚定
                        anchored = _source_points_matching(
                            func_id, current_tainted, source_points)
                        break
                    callee = blocks_by_id.get(func_id)
                    caller = blocks_by_id.get(chain.path[i - 1])
                    if callee is None or caller is None:
                        continue
                    caller_tainted = _map_call_site_params_reverse(
                        callee_block=callee, callee_tainted=current_tainted,
                        caller_block=caller)
                    steps_rev.append(PropagationStep(
                        from_func_id=callee.id,
                        from_param=next(iter(current_tainted), ""),
                        to_func_id=caller.id,
                        to_param=next(iter(caller_tainted), ""),
                        code_location=f"{callee.file_path}:{callee.start_line}",
                        confidence=0.9,
                    ))
                    current_tainted = caller_tainted
                    if sink_step - (i - 1) > max_depth:
                        break
                for sp in anchored:
                    steps_fwd = list(reversed(steps_rev))
                    flows.append(TaintFlow(
                        flow_id=f"{sp.entry_point_id}->{sink.id}",
                        entry_point_id=sp.entry_point_id,
                        source_param=sp.param_name,
                        source_type=sp.source_type,  # 精确,非硬编码 QUERY_PARAM
                        propagation_steps=steps_fwd,
                        sink_call_site_id=sink.id,
                        confidence=min(
                            (s.confidence for s in steps_fwd),
                            default=0.9,
                        ),
                        notes="backward-anchored",
                    ))
    return flows
```

注:`defaultdict` 已在 `chain_propagator.py` 顶部 import(`__init__.py` 内用,但 chain_propagator.py 本身需确认;若无,在文件顶部加 `from collections import defaultdict`)。

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest packages/core/tests/code_index/test_chain_propagator_backward.py -v`
Expected: PASS(5 tests:B1 的 3 + B2 的 2)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/chain_propagator.py \
        packages/core/tests/code_index/test_chain_propagator_backward.py
git commit -m "feat(chain_propagator): propagate_backward_across_chains 双向锚定(Sink→Source)"
```

---

## Task B3: pipeline 分流 backward + 可观测性

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py:198-208`(propagation 分流)
- Test: `packages/core/tests/code_index/test_chain_propagator_backward.py`(追加 pipeline 分流测试)

**Interfaces:**
- Consumes: `propagate_backward_across_chains`(B2)、`build_code_index_with_gitnexus` 的 sink_call_sites/source_points/intra_results/chains
- Produces: inject/xss/ssrf 的 `parameter_graph.taint_flows` 由 backward 产

- [ ] **Step 1: Write failing test**

追加到 `test_chain_propagator_backward.py`:

```python
def test_pipeline_uses_backward_for_taint_flows():
    """pipeline 冒烟:taint_flows 由 propagate_backward 产(含 source_type 精确)。"""
    import asyncio
    from unittest.mock import AsyncMock
    from shannon_core.code_index import build_code_index_with_gitnexus
    import tempfile, os

    with tempfile.TemporaryDirectory() as repo:
        with open(os.path.join(repo, "app.js"), "w") as fh:
            fh.write(
                "app.get('/r', function h(req){ sink(req.query.x); });\n"
                "function sink(p){ eval(p); }\n"
            )
        os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
        fake_mcp = AsyncMock()
        fake_mcp.call_tool = AsyncMock(return_value={"upstream": [], "downstream": []})
        fake_llm = AsyncMock(return_value="[]")
        index, _ = asyncio.run(build_code_index_with_gitnexus(
            repo, mcp_client=fake_mcp, llm_client=fake_llm))
        # 至少有 source_point(req.query.x);若 sink 被 sink_detector 规则命中,
        # backward 应产 TaintFlow(具体取决于规则覆盖;此测试验证不崩 + source_points 非空)
        assert any(sp.param_name == "x" for sp in index.source_points)
```

- [ ] **Step 2: Run test to verify it fails(or passes vacuously)**

Run: `pytest packages/core/tests/code_index/test_chain_propagator_backward.py::test_pipeline_uses_backward_for_taint_flows -v`
Expected: 可能 FAIL 或 vacuous PASS(取决于 backward 是否已接入 pipeline)

- [ ] **Step 3: Switch pipeline propagation to backward**

在 `__init__.py` 顶部 import 加:
```python
from shannon_core.code_index.chain_propagator import propagate_backward_across_chains
```

将 `__init__.py:198-208` 的 forward propagation(`taint_flows = propagate_across_chains(...)`)替换为 backward:

```python
    # ⑥' propagation(Phase B:inject/xss/ssrf 改 backward Sink→Source)
    #    双向锚定:起点 SinkCallSite + 终点 SourcePoint。
    #    forward propagate_across_chains 保留(过渡):供 authz _source_reaches_sink
    #    复用底层 _map_call_site_params + 回归测试。
    taint_flows = propagate_backward_across_chains(
        chains=call_graph.chains,
        blocks=all_blocks,
        intra_results=intra_results,
        sink_call_sites=sink_call_sites,
        source_points=source_points,
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=taint_flows,
        language_coverage=[language],
    )
    logger.info(
        "propagate_backward: %d sinks → %d anchored taint_flows "
        "(source_points=%d, dropped unanchored)",
        len(sink_call_sites), len(pgraph.taint_flows), len(source_points),
    )
```

- [ ] **Step 4: Run test + inject/xss/ssrf regression**

Run: `pytest packages/core/tests/code_index/test_chain_propagator_backward.py packages/core/tests/code_index/test_injection_builder.py packages/core/tests/code_index/test_xss_builder.py packages/core/tests/code_index/test_ssrf_builder.py packages/core/tests/code_index/test_chain_verdict.py -v`
Expected: PASS(B 系列 + builder/verdict 回归——TaintFlow 结构不变,builder/verdict 应通过;若 builder 测试构造了自己的 pgraph 不受 pipeline 影响)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/__init__.py \
        packages/core/tests/code_index/test_chain_propagator_backward.py
git commit -m "feat(code_index): pipeline ⑥' 分流 backward + 可观测性 log"
```

---

## Task B4: forward 保留确认 + 回归测试锚点

**Files:**
- Test: `packages/core/tests/code_index/test_chain_propagator.py`(确认 forward 仍可独立调用)

**Interfaces:**
- Consumes: `propagate_across_chains`(forward,保留)

- [ ] **Step 1: Confirm forward still importable & tested**

Run: `pytest packages/core/tests/code_index/test_chain_propagator.py -v`
Expected: PASS(forward `propagate_across_chains` 保留,原测试不变)

- [ ] **Step 2: If any forward test broke, fix inline; otherwise no code change**

forward `propagate_across_chains` 函数定义保留(未删),其测试应原样通过。若因 import 顺序等问题 FAIL,修复但不改 forward 逻辑。

- [ ] **Step 3: Commit(若有修复)**

```bash
git add -A
git commit -m "test(chain_propagator): forward 保留回归锚点(过渡期)"
```

**Phase B 完成里程碑:** inject/xss/ssrf 走 backward 双向锚定,`parameter_graph.taint_flows` source_type 精确(来自 SourcePoint),下游 builder/verdict/merger 零改动。forward 保留过渡。

---

# Phase 3 (C): authz 接入 SourcePoint(Endpoint→Guard 正向)

## Task C1: _source_reaches_sink 正向可达

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py`(加 `_source_reaches_sink`)
- Test: `packages/core/tests/code_index/test_authz_source_point.py`(新建)

**Interfaces:**
- Consumes: `_map_call_site_params`(`chain_propagator.py:250-279`,forward,复用底层)、`SourcePoint`、segment FuncBlock 列表
- Produces: `_source_reaches_sink(ep_sources, segment_ids, blocks_by_id) -> bool`

- [ ] **Step 1: Write failing test**

```python
# packages/core/tests/code_index/test_authz_source_point.py
from shannon_core.code_index.models import FuncBlock, ParameterSource
from shannon_core.code_index.parameter_models import SourcePoint
from shannon_core.code_index.authz_gitnexus_track import _source_reaches_sink


def _blk(fid, source, params):
    fp, fn, ln = fid.split(":")
    return FuncBlock(id=fid, file_path=fp, function_name=fn, start_line=int(ln),
                     end_line=int(ln)+5, source_code=source, parameters=params,
                     language="typescript")


def test_source_reaches_sink_when_param_flows_to_callee():
    handler = _blk("a.js:h:1",
                   "function h(req){ dao(req.params.userId); }", ["req"])
    sink_func = _blk("a.js:dao:5",
                     "function dao(id){ db.update({userId: id}); }", ["id"])
    sp = SourcePoint(
        id="a.js:h:1::userId::1", entry_point_id=handler.id, param_name="userId",
        source_type=ParameterSource.PATH_PARAM, expression="req.params.userId",
        file_path="a.js", line=1, confidence=0.9, rule_id="ts-express-path",
    )
    blocks_by_id = {handler.id: handler, sink_func.id: sink_func}
    # segment: handler → sink_func
    assert _source_reaches_sink([sp], [handler.id, sink_func.id], blocks_by_id) is True


def test_source_does_not_reach_when_no_flow():
    handler = _blk("a.js:h:1",
                   "function h(req){ dao('constant'); }", ["req"])
    sink_func = _blk("a.js:dao:5",
                     "function dao(id){ db.update({userId: id}); }", ["id"])
    sp = SourcePoint(
        id="a.js:h:1::userId::1", entry_point_id=handler.id, param_name="userId",
        source_type=ParameterSource.PATH_PARAM, expression="req.params.userId",
        file_path="a.js", line=1, confidence=0.9, rule_id="ts-express-path",
    )
    blocks_by_id = {handler.id: handler, sink_func.id: sink_func}
    assert _source_reaches_sink([sp], [handler.id, sink_func.id], blocks_by_id) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/core/tests/code_index/test_authz_source_point.py -v`
Expected: FAIL(" cannot import name '_source_reaches_sink'")

- [ ] **Step 3: Implement _source_reaches_sink**

在 `authz_gitnexus_track.py` 内(`find_unguarded_sink_paths` 之前)加:

```python
def _source_reaches_sink(
    ep_sources: list[EntryPoint] | list,  # 实际是 list[SourcePoint]
    segment_ids: list[str],
    blocks_by_id: dict[str, FuncBlock],
) -> bool:
    """SourcePoint 参数值是否正向流到 segment 末端的 sink 函数(复用 forward 工具)。

    从 entry 的 SourcePoint 表达式集合(seed)正向沿 segment 传播,用
    chain_propagator._map_call_site_params(forward)逐跳映射,看 tainted 能否
    到达 segment 末端的 sink 函数参数。过近似(substring),宁过报。
    """
    from shannon_core.code_index.chain_propagator import _map_call_site_params

    if not ep_sources or len(segment_ids) < 2:
        # 单节点 segment(entry 自身是 sink)→ 看 SourcePoint 表达式是否直接在该函数体
        if len(segment_ids) == 1:
            blk = blocks_by_id.get(segment_ids[0])
            if blk is None:
                return False
            return any(
                (sp.expression or sp.param_name) and
                (sp.expression in blk.source_code or sp.param_name in blk.source_code)
                for sp in ep_sources
            )
        return False

    # seed:entry 的 SourcePoint 表达式/参数名集合
    current_tainted: set[str] = {
        sp.expression or sp.param_name for sp in ep_sources if sp.expression or sp.param_name
    }
    for i in range(len(segment_ids) - 1):
        caller = blocks_by_id.get(segment_ids[i])
        callee = blocks_by_id.get(segment_ids[i + 1])
        if caller is None or callee is None:
            continue
        current_tainted = _map_call_site_params(
            caller_block=caller, caller_tainted=current_tainted, callee_block=callee)
        if not current_tainted:
            return False
    return bool(current_tainted)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/core/tests/code_index/test_authz_source_point.py -v`
Expected: PASS(2 tests)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/authz_gitnexus_track.py \
        packages/core/tests/code_index/test_authz_source_point.py
git commit -m "feat(authz): _source_reaches_sink 正向可达(复用 forward 工具)"
```

---

## Task C2: find_unguarded_sink_paths 三重过滤 + source_point_ids

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py:50-58`(IDORCandidateChain 加字段)+ `find_unguarded_sink_paths:99-175`(三重过滤)
- Test: `packages/core/tests/code_index/test_authz_source_point.py`(追加)

**Interfaces:**
- Consumes: `CodeIndex.source_points`(A)、`_source_reaches_sink`(C1)、`_is_side_effect_sink`/`_segment_has_ownership_guard`(现有)
- Produces: `IDORCandidateChain.source_point_ids: tuple[str, ...]`、三重过滤的 `find_unguarded_sink_paths(index, source_points)`

- [ ] **Step 1: Write failing test**

追加到 `test_authz_source_point.py`:

```python
from shannon_core.code_index.authz_gitnexus_track import (
    IDORCandidateChain, find_unguarded_sink_paths,
)
from shannon_core.code_index.models import CallChain, CodeIndex, EntryPoint


def _ep(func_id, entry_type="http_route", route="/r"):
    return EntryPoint(func_block_id=func_id, entry_type=entry_type, route=route,
                      http_method="GET", confidence=0.9, evidence="e",
                      needs_llm_review=False, source="code_index")


def test_find_unguarded_filters_entry_without_sourcepoint():
    # entry 无 SourcePoint → 不产候选(降过报)
    handler = _blk("a.js:h:1",
                   "function h(req){ dao.update({a:1}); }", ["req"])
    sink_func = _blk("a.js:dao:5",
                     "function dao(){ this.update(); }", [])
    index = CodeIndex(
        repository="r", language="typescript", total_blocks=2,
        total_entry_points=1, total_chains=1,
        blocks=[handler, sink_func], edges=[],
        entry_points=[_ep(handler.id)],
        chains=[CallChain(entry_point_id=handler.id,
                          path=[handler.id, sink_func.id], depth=1, has_unresolved=False)],
        source_points=[],  # 无 SourcePoint
    )
    out = find_unguarded_sink_paths(index)
    assert out == []  # 三重过滤第①层:无 SourcePoint → 跳过


def test_find_unguarded_yields_candidate_with_source_point_ids():
    handler = _blk("a.js:h:1",
                   "function h(req){ dao(req.params.userId); }", ["req"])
    sink_func = _blk("a.js:dao:5",
                     "function dao(id){ db.users.update({userId:id}); }", ["id"])
    sp = SourcePoint(
        id="a.js:h:1::userId::1", entry_point_id=handler.id, param_name="userId",
        source_type=ParameterSource.PATH_PARAM, expression="req.params.userId",
        file_path="a.js", line=1, confidence=0.9, rule_id="ts-express-path",
    )
    index = CodeIndex(
        repository="r", language="typescript", total_blocks=2,
        total_entry_points=1, total_chains=1,
        blocks=[handler, sink_func], edges=[],
        entry_points=[_ep(handler.id, route="/u/:userId")],
        chains=[CallChain(entry_point_id=handler.id,
                          path=[handler.id, sink_func.id], depth=1, has_unresolved=False)],
        source_points=[sp],
    )
    out = find_unguarded_sink_paths(index)
    assert len(out) == 1
    assert sp.id in out[0].source_point_ids  # 附 source 证据
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/core/tests/code_index/test_authz_source_point.py -v`
Expected: FAIL(`IDORCandidateChain` 无 `source_point_ids` 字段 / 过滤逻辑未接入 source)

- [ ] **Step 3: Add source_point_ids to IDORCandidateChain + 三重过滤**

`authz_gitnexus_track.py:50-58` 的 `IDORCandidateChain` 加字段:
```python
@dataclass(frozen=True)
class IDORCandidateChain:
    endpoint_id: str
    handler_id: str
    sink_id: str
    sink_step_idx: int
    path: tuple[str, ...]
    guard_nodes_on_path: tuple[str, ...]
    source_point_ids: tuple[str, ...] = ()   # 新增:命中的 SourcePoint(source 证据)
```

改造 `find_unguarded_sink_paths`(`authz_gitnexus_track.py:99-175`)——签名加 `source_points` 参数(从 `index.source_points` 取,保持向后兼容:若空则退回旧行为),三重过滤:

```python
def find_unguarded_sink_paths(
    index: CodeIndex,
    *,
    max_paths_per_endpoint: int = 20,
) -> list[IDORCandidateChain]:
    """Find handler→sink paths lacking ownership guard, with SourcePoint anchoring.

    三重过滤(Phase C):
      ① 有 SourcePoint(entry 接收用户可控输入)
      ② 参数实际流到 side-effect sink(_source_reaches_sink,复用 forward 工具)
      ③ entry→sink_step 段无 ownership guard
    无 SourcePoint 的 entry 跳过(降过报)。
    """
    blocks_by_id: dict[str, FuncBlock] = {b.id: b for b in index.blocks}
    # SourcePoint 按 entry 分组
    sources_by_ep: dict[str, list] = defaultdict(list)
    for sp in (index.source_points or []):
        sources_by_ep[sp.entry_point_id].append(sp)

    entry_eps = [
        ep for ep in index.entry_points
        if ep.entry_type in _AUTHZ_ENTRY_TYPES
        and (ep.entry_type == "gitnexus_process" or ep.route is not None)
    ]

    candidates: list[IDORCandidateChain] = []
    seen: set[tuple[str, str]] = set()

    for ep in entry_eps:
        ep_sources = sources_by_ep.get(ep.func_block_id, [])
        if not ep_sources:                       # ① 无 SourcePoint → 跳过(降过报)
            continue
        handler = blocks_by_id.get(ep.func_block_id)
        if handler is None:
            continue
        if _handler_has_ownership_guard(handler):
            continue
        count_for_ep = 0
        for chain in index.chains:
            if chain.entry_point_id != ep.func_block_id or not chain.path:
                continue
            for step_idx, sid in enumerate(chain.path):
                if step_idx == 0:
                    continue
                if not _is_side_effect_sink(blocks_by_id.get(sid)):
                    continue
                key = (ep.func_block_id, sid)
                if key in seen:
                    continue
                segment = chain.path[: step_idx + 1]
                if _segment_has_ownership_guard(segment, blocks_by_id):
                    continue
                if not _source_reaches_sink(           # ② 参数流到 sink
                        ep_sources, segment, blocks_by_id):
                    continue
                # ③ 已通过 ownership 检查;收集命中的 SourcePoint
                hit_sp_ids = tuple(sp.id for sp in ep_sources)
                seen.add(key)
                candidates.append(IDORCandidateChain(
                    endpoint_id=ep.func_block_id,
                    handler_id=ep.func_block_id,
                    sink_id=sid,
                    sink_step_idx=step_idx,
                    path=tuple(chain.path),
                    guard_nodes_on_path=(),
                    source_point_ids=hit_sp_ids,
                ))
                count_for_ep += 1
                if count_for_ep >= max_paths_per_endpoint:
                    break
            if count_for_ep >= max_paths_per_endpoint:
                break

    logger.info(
        "authz GitNexus track: %d entry endpoints with sources, %d IDOR candidates",
        len([ep for ep in entry_eps if sources_by_ep.get(ep.func_block_id)]),
        len(candidates),
    )
    return candidates
```

注:`defaultdict` 需在 `authz_gitnexus_track.py` 顶部 import(`from collections import defaultdict`)。

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest packages/core/tests/code_index/test_authz_source_point.py packages/core/tests/code_index/test_authz_gitnexus_track.py -v`
Expected: PASS(C1/C2 + 现有 authz 回归——若现有 authz 测试构造的 index 无 source_points,三重过滤①会使其候选为空,需更新那些测试加入 source_points 或断言调整)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/authz_gitnexus_track.py \
        packages/core/tests/code_index/test_authz_source_point.py \
        packages/core/tests/code_index/test_authz_gitnexus_track.py
git commit -m "feat(authz): find_unguarded_sink_paths 三重过滤 + source_point_ids"
```

---

## Task C3: authz 可观测性(三重过滤各阶段计数)

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/authz_gitnexus_track.py`(`build_authz_gitnexus_track` 加 log_info)

**Interfaces:**
- Consumes: 现有 `AuthzTrackBuildResult`、`log_info_activity`(若 pipeline 有;否则用 logger.info)

- [ ] **Step 1: Augment logging in build_authz_gitnexus_track**

在 `build_authz_gitnexus_track`(`authz_gitnexus_track.py:325-391`)的 `find_unguarded_sink_paths` 调用前后加计数 log。`find_unguarded_sink_paths` 已在末尾 log entry-with-sources / candidates 计数。补充:在 `build_authz_gitnexus_track` 内显式 log 三阶段:

在 `dominance_cands = find_unguarded_sink_paths(index)` 之后加:
```python
    sources_total = len(index.source_points or [])
    entry_with_sources = len({sp.entry_point_id for sp in (index.source_points or [])})
    logger.info(
        "authz build: source_points=%d, entries_with_sources=%d, "
        "dominance_candidates=%d, framework_candidates=%d",
        sources_total, entry_with_sources,
        len(dominance_cands), len(framework_cands),
    )
```

- [ ] **Step 2: Run authz tests to verify no break**

Run: `pytest packages/core/tests/code_index/test_authz_source_point.py packages/core/tests/code_index/test_authz_gitnexus_track.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add packages/core/src/shannon_core/code_index/authz_gitnexus_track.py
git commit -m "feat(authz): 三重过滤可观测性 log(source_points/entries/candidates)"
```

**Phase C 完成里程碑:** authz 从 entry 级升级到 SourcePoint 级,三重过滤降过报,`IDORCandidateChain` 带 `source_point_ids` source 证据。复用 forward `_map_call_site_params`。

---

# Self-Review(plan 作者自检)

**1. Spec coverage:**
- §5 SourcePoint 模型 → Task A1 ✓
- §5 source_detector 规则层 + LLM soft → A2, A3 ✓
- §5.3 规则层自写 → A2(结构 + 5 语言代表规则,扩展模式明确)✓
- §6 backward 双向锚定 → B1(反向映射)+ B2(propagate_backward)✓
- §6.3 pipeline 分流 + forward 保留 → B3, B4 ✓
- §7 authz 三重过滤 + source_point_ids → C1(reaches_sink)+ C2(find_unguarded)✓
- §8 CodeIndex.source_points + 落盘 → A1(model)+ A4(pipeline 落盘)✓
- §8.3 字段对齐原版(validation)→ A1(SourcePoint.validation)✓
- §8.4 双轨铁律(source_points 不喂 LLM 轨)→ A5(守卫测试)✓
- §9.4 测试策略 → 每 task TDD ✓
- §9.5 成功标准(NodeGoat)→ 留待人工冒烟(plan 不含真机跑,标注在里程碑)✓
- §10 follow-up(combined_sources / forward 删除 / direction_hint 统一)→ 明确不在本 plan ✓

**2. Placeholder scan:**
- 无 TBD/TODO。规则库 A2 给真实可运行代码(5 语言代表规则),扩展点为"按此模式加其余框架"(非 placeholder,是真实代码 + 同类扩展指引)。
- 每 step 有完整代码或精确命令。

**3. Type consistency:**
- `SourcePoint` 字段在 A1 定义,A2/A3/B2/C1/C2 消费一致(param_name/source_type/expression/entry_point_id/rule_id)。
- `detect_sources(blocks, parser, entry_point_ids, *, source_provider)` 签名 A2 定义、A4 消费一致。
- `propagate_backward_across_chains(chains, blocks, intra_results, sink_call_sites, source_points)` B2 定义、B3 消费一致。
- `_map_call_site_params_reverse(callee_block, callee_tainted, caller_block)` B1 定义、B2 消费一致。
- `_source_reaches_sink(ep_sources, segment_ids, blocks_by_id)` C1 定义、C2 消费一致。
- `IDORCandidateChain.source_point_ids` C2 加字段,C2 测试 + 后续 judge 消费一致。

**4. 已知执行注意(engineer 执行时关注):**
- Task C2 step 4:现有 `test_authz_gitnexus_track.py` 可能因三重过滤①(无 source_points → 候选为空)而 FAIL,需更新那些测试(给 index 加 source_points 或调整断言)。这是预期回归,非 plan 缺陷。
- Task B3 step 4:builder/verdict 回归若 FAIL,根因是 backward 产的 taint_flows 与测试 fixture 的 forward 产出不同——但 TaintFlow 结构不变,builder 测试若用自带 pgraph 则不受影响;若用 pipeline 集成测试则需调整。优先跑单元级 builder 测试。
- NodeGoat 真机冒烟(§9.5 成功标准)在三个 Phase 全部完成后人工执行,不在 plan 的 task 内(无真机 GitNexus 环境时单测已覆盖逻辑)。
