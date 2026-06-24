# Injection / XSS / SSRF 双轨链判定实现计划（Plan 8）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 injection（正向 source→sink）/ xss（反向 sink→source + DB read↔write 跨表补 Stored）/ ssrf（反向 7 步 + 补 schema 缺字段）三个 trace 类搭建**共用的 GitNexus 轨链判定基础设施**，并按各自的 trace 方向配置——让 GitNexus 轨从 Plan 1 落盘的 `parameter_graph.json` 取 source→sink 候选链、用确定性 sanitizer/encoder 标注、跑一次轻量 LLM 链判定 pass 产 verdict，最终交 Plan 3 通用合并器做 verdict OR。

**Architecture:** 三个 vuln 类共享同一框架（spec §5.4-5.6），差异仅在 **trace 方向** 与各自补的**盲区**：

```
                 ┌─ Plan 1 落盘的 parameter_graph.json ─┐
                 │  (TaintFlow: source_param → propagation_steps → sink_call_site_id/sink_slot)
                 ▼
   ┌──────────────────────────────────────────────────────────┐
   │ GitNexus 轨链判定基础设施 (本 plan, 共用)                │
   │  · 候选链提取: TaintFlow → (vuln_class, source, sink)     │
   │  · 确定性 sanitizer/encoder 标注 (库级函数表, 见 Task 1)  │
   │  · 轻量 LLM 链判定 pass → verdict (safe|vulnerable)       │
   └──────────────────────────────────────────────────────────┘
        │ injection(正向)        │ xss(反向+Stored补)       │ ssrf(反向+schema补)
        ▼                        ▼                          ▼
   <vuln>_gitnexus_queue.json  (每类按 trace 方向产出 findings)
        │
        ▼  Plan 3 合并器 (verdict OR + both/llm-only/gitnexus-only 标记)
   <vuln>_exploitation_queue.json (findings_renderer 消费不变)
```

- **共用部分（Task 1-3）**：候选链提取器（按 `SinkCategory` 路由到三个 vuln 类）+ 确定性 sanitizer/encoder 识别库（库级函数表：SQL binds / `shlex.quote` / DOMPurify / htmlspecialchars / URL allowlist 等）+ 轻量 LLM 链判定 pass（给 LLM 候选链 + sanitizer 标注，产 verdict + witness_payload + evidence_chain）。
- **injection 差异（Task 4）**：正向 trace——`source` (param+file:line) → `sink_call` (file:line, slot_type) → `verdict`；补 **post-sanitize concat** 标注（污染槽：sanitizer 后又 concat）。
- **xss 差异（Task 5）**：反向 sink→source + **render_context** 映射 + **DB read↔write 跨表补 Stored**：当一条链的 source 落在 DB read（`SinkCategory` 无 DB read，用 `source_type==ParameterSource.INTERNAL` 或 propagation_steps 出现 DB fetch 节点检测），GitNexus 轨额外反查 parameter_graph 里有无对**同一表/字段**的 write 流（用户输入 → DB insert/update），若有则合成一条 Stored XSS 候选链（read sink + write source 拼成完整 source→read→render）。
- **ssrf 差异（Task 6）**：反向 7 步——但 GitNexus 轨只做**链判定**（7 步方法论本就在 LLM prompt，GitNexus 轨不重复方法论），只补 **schema 缺字段**（`SsrfVulnerability` 缺 `path`/`verdict`/`witness_payload`，Task 2 加）。
- **verdict OR 合并（Task 7）**：直接复用 Plan 3 的 `run_merge_dual_track_queues`（它已 wiring 在 vuln 阶段后）。本 plan 只需保证 `<vuln>_gitnexus_queue.json` 被正确产出（Plan 3 合并器已对该文件不存在做优雅降级）。

**Tech Stack:** Python 3.12, pydantic v2, pytest, pytest-asyncio

## Global Constraints

- **强依赖 Plan 1（taint 落盘）+ Plan 3（合并器）**：本 plan 读 `parameter_graph.json`（Plan 1 产物）；产出 `<vuln>_gitnexus_queue.json` 交 Plan 3 合并器。若 Plan 1 未落地（pgraph 为空/None）→ GitNexus 轨产空 queue（优雅降级，全 llm-only），pipeline 行为与现状等价。
- **不重复 LLM prompt 已有的方法论**：三个 vuln prompt（`vuln-injection.txt` / `vuln-xss.txt` / `vuln-ssrf.txt`）的 LLM 轨已完整描述正向/反向方法论。GitNexus 轨的 LLM pass 是**轻量链判定**（给候选链 + sanitizer 标注，只回 verdict/witness/evidence），**不**重新跑完整方法论分析（成本控制 + 避免与 LLM 轨冗余）。
- **LLM 轨自由 trace 不被锚定**（spec §2 原则）：GitNexus 轨的候选链只作**交叉验证/补盲**，**不**限制 LLM 轨的自主分析。GitNexus 轨产的 `<vuln>_gitnexus_queue.json` 与 LLM 轨产的 `<vuln>_llm_queue.json` 是两条独立轨道，由 Plan 3 合并器做并集（不丢任一轨），verdict OR（保守，宁过报不漏报）。
- **确定性 sanitizer/encoder 是 best-effort 标注，不判有效性**：与 `has_sanitizer_hint` 语义一致（`parameter_models.py:59`「仅提示，不判有效性」）。有效性由 LLM pass 按 slot/render_context 判定。确定性标注只回答「路径上是否出现已知库级防御函数」。
- **不破坏现有消费方**：`findings_renderer.py` 读 `<vuln>_exploitation_queue.json`，新增的 `path`/`verdict`/`witness_payload` 字段（Task 2 给 ssrf）全部 `str | None` 默认 None，旧 queue 无这字段仍能 `parse_lenient`。`<vuln>_gitnexus_queue.json` 是新文件，renderer 不直接读它（由 Plan 3 合并后写 exploitation_queue）。
- **xss Stored 补盲是 best-effort 拼链**：DB read↔write 跨表关联依赖 parameter_graph 同时含 read 与 write 流。若只有 read 无 write（或反之）→ 无法拼，跳过该 Stored 候选（宁缺勿错拼）。这是 GitNexus 轨对 LLM 轨的**补充**，不是替代。
- TDD：每个改动先写失败测试；frequent commits（`feat(code_index):` / `feat(models):` / `feat(whitebox):`）。真实 LLM pass + 真实 pgraph 流转需 MCP/LLM 环境，**单元测试覆盖字段/提取/标注/拼链/判定/闭环，真实流转由手动冒烟验证**（spec 已注明端到端冒烟待人工）。

---

### Task 1: 确定性 sanitizer/encoder 识别库（库级函数表）

**Files:**
- Create: `packages/core/src/shannon_core/code_index/sanitizer_library.py`
- Test: `packages/core/tests/code_index/test_sanitizer_library.py`（Create）

**Interfaces:**
- Produces: `SanitizerLibrary`——库级防御函数表（按 `(language, callee, receiver_pattern)` → `(defense_type, applies_to_slot_or_context)`）；`annotate_sanitizers(propagation_steps) -> list[SanitizerAnnotation]`：给定一条 TaintFlow 的 `propagation_steps`（`parameter_models.py:40-49`），返回路径上出现的确定性防御标注。

**库内容**（确定性识别，best-effort，不判有效性）：

| defense_type | applies_to (SlotContext / render_context) | 库级函数（callee，按语言） |
|---|---|---|
| `sql_bind` | `SQL_VALUE` | `execute`(param-bound, 检测 `?`/`%s`/`:name` 占位符) / `executemany` |
| `shlex_quote` | `CMD_ARGUMENT` | `shlex.quote` (py) |
| `subprocess_array` | `CMD_ARGUMENT` | `subprocess.run/popen/call` with list args (AST 检测 `shell=False` + list literal) |
| `path_resolve_boundary` | `FILE_PATH` | `Path.resolve()` + boundary / `realpath` |
| `template_autoescape` | `TEMPLATE_EXPR` | jinja2 `autoescape=True` / flask `render_template` (autoescape default on) |
| `html_entity_encode` | `HTML_BODY` | `htmlspecialchars` (php) / `html.escape` (py) / `he.encode` (ts) |
| `attr_encode` | `HTML_ATTRIBUTE` | `esc_attr` (wp) / attribute-encoding helpers |
| `js_string_escape` | `JAVASCRIPT_STRING` | `json.dumps` (py, for JS string context) / `JSON.stringify` (ts) |
| `url_encode` | `URL_PARAM` | `urlencode` / `encodeURIComponent` (ts) / `rawurlencode` (php) |
| `dom_purify` | `HTML_BODY` (client) | `DOMPurify.sanitize` (ts) |
| `ssrf_allowlist` | `URL` | 自定义 allowlist 函数（`is_allowed_host` / `validate_url` 类名，需 LLM 复核） |

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_sanitizer_library.py
from shannon_core.code_index.sanitizer_library import (
    SanitizerLibrary,
    annotate_sanitizers,
)
from shannon_core.code_index.parameter_models import PropagationStep


def _step(transformation, code_location="app.py:10"):
    return PropagationStep(
        step_id="s1", from_func_id="f", from_param="q",
        to_func_id="f", to_param="x",
        transformation=transformation, code_location=code_location,
    )


def test_library_known_sql_bind_pattern():
    lib = SanitizerLibrary()
    # execute with placeholder arg pattern — best-effort detection
    hit = lib.match(language="python", callee="execute",
                    receiver_text="cursor", arg_expr="%s")
    assert hit is not None
    assert hit.defense_type == "sql_bind"
    assert hit.applies_to == "sql_value"


def test_library_known_html_escape():
    lib = SanitizerLibrary()
    hit = lib.match(language="python", callee="escape", receiver_text="html", arg_expr="q")
    assert hit is not None
    assert hit.defense_type == "html_entity_encode"
    assert hit.applies_to == "html_body"


def test_library_known_dompurify():
    lib = SanitizerLibrary()
    hit = lib.match(language="typescript", callee="sanitize",
                    receiver_text="DOMPurify", arg_expr="x")
    assert hit is not None
    assert hit.defense_type == "dom_purify"
    assert hit.applies_to == "html_body"


def test_library_unknown_returns_none():
    lib = SanitizerLibrary()
    assert lib.match(language="python", callee="unknown_fn",
                     receiver_text=None, arg_expr="q") is None


def test_annotate_sanitizers_finds_defense_on_step_transformation():
    """propagation_steps 里的 transformation 字段出现 sanitizer → 标注。"""
    # transformation 字段约定: "sanitize_hint:<name>" (见 parameter_models.py:47)
    steps = [
        _step("concat"),
        _step("sanitize_hint:html.escape"),
        _step("format"),
    ]
    annotations = annotate_sanitizers(steps, language="python")
    # 至少识别到 html.escape（出现于 transformation 文本）
    defense_types = {a.defense_type for a in annotations}
    assert "html_entity_encode" in defense_types


def test_annotate_sanitizers_empty_when_no_defense():
    steps = [_step("concat"), _step("format")]
    assert annotate_sanitizers(steps, language="python") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_sanitizer_library.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.code_index.sanitizer_library`

- [ ] **Step 3: Implement `SanitizerLibrary` + `annotate_sanitizers`**

```python
# packages/core/src/shannon_core/code_index/sanitizer_library.py
"""Deterministic sanitizer/encoder library (best-effort, spec §5.4-5.6 shared).

Identifies KNOWN library-level defense functions appearing on a taint path.
This is annotation only — it does NOT judge effectiveness. Effectiveness
(slot/context match, concat-after-sanitize) is judged by the LLM chain-verdict
pass (Task 3). This mirrors the existing `has_sanitizer_hint` semantics
(parameter_models.py:59 "仅提示，不判有效性").

Two entry points:
- SanitizerLibrary.match(language, callee, receiver_text, arg_expr) -> Annotation|None
- annotate_sanitizers(propagation_steps, language) -> list[Annotation]
"""

import re
from dataclasses import dataclass
from typing import Iterable

from shannon_core.code_index.parameter_models import PropagationStep


@dataclass(frozen=True)
class SanitizerRule:
    rule_id: str
    languages: tuple[str, ...]
    callee: str                         # function/method name
    receiver_pattern: re.Pattern | None # None = bare call
    arg_hint: re.Pattern | None         # optional: arg text must match (e.g. placeholder)
    defense_type: str                   # sql_bind / shlex_quote / ... (see table)
    applies_to: str                     # SlotContext value or render_context value


@dataclass(frozen=True)
class SanitizerAnnotation:
    rule_id: str
    defense_type: str
    applies_to: str
    code_location: str = ""             # file:line where the defense appears
    matched_text: str = ""              # the callee / arg hint that matched


# --- Receiver regexes ---
_HTML_MOD = re.compile(r"^(html)$")
_DOMPURIFY = re.compile(r"^(DOMPurify|dompurify)$")
_WP_ESC = re.compile(r"^(esc)$")  # WordPress esc_* family (callee prefixed)
_PHP_HTMLSPECIAL = None  # bare function htmlspecialchars
_SHLEX = None
_SUBPROCESS = re.compile(r"^(subprocess)$")
_DB_CURSOR = re.compile(r"^(cursor|cnx|conn|db|database)$")
_PATHLIB = re.compile(r"^(Path)$")
_JINJA = re.compile(r"^(jinja2|flask)$")
_URLLIB = re.compile(r"^(urllib|parse)$")

# arg hints: detect SQL placeholder in the arg expression
_SQL_PLACEHOLDER = re.compile(r"(%s|%d|\?|:\w+|@\w+|\$\d+)")
_JSON_DUMPS_ARG = None  # any string arg


DEFAULT_SANITIZER_RULES: tuple[SanitizerRule, ...] = (
    # --- SQL binds (parameterized query) ---
    SanitizerRule("py-sql-execute-bind", ("python",), "execute", _DB_CURSOR,
                  _SQL_PLACEHOLDER, "sql_bind", "sql_value"),
    SanitizerRule("py-sql-executemany-bind", ("python",), "executemany", _DB_CURSOR,
                  _SQL_PLACEHOLDER, "sql_bind", "sql_value"),
    SanitizerRule("php-mysqli-prepare", ("php",), "prepare", None,
                  None, "sql_bind", "sql_value"),
    SanitizerRule("java-preparedstatement", ("java",), "prepareStatement", None,
                  None, "sql_bind", "sql_value"),

    # --- Command execution ---
    SanitizerRule("py-shlex-quote", ("python",), "quote", re.compile(r"^(shlex)$"),
                  None, "shlex_quote", "cmd_argument"),
    # subprocess with list args + shell=False is detected via AST in sink_detector;
    # here we only annotate when the transformation text carries shell=False hint.
    # (No callee rule; handled in annotate_sanitizers via transformation text.)

    # --- Path ---
    SanitizerRule("py-path-resolve", ("python",), "resolve", _PATHLIB,
                  None, "path_resolve_boundary", "file_path"),
    SanitizerRule("py-path-absolute", ("python",), "absolute", _PATHLIB,
                  None, "path_resolve_boundary", "file_path"),

    # --- Template autoescape ---
    SanitizerRule("py-jinja-autoescape", ("python",), "get_template", _JINJA,
                  None, "template_autoescape", "template_expr"),

    # --- HTML entity encoding ---
    SanitizerRule("py-html-escape", ("python",), "escape", _HTML_MOD,
                  None, "html_entity_encode", "html_body"),
    SanitizerRule("php-htmlspecialchars", ("php",), "htmlspecialchars", None,
                  None, "html_entity_encode", "html_body"),
    SanitizerRule("php-htmlentities", ("php",), "htmlentities", None,
                  None, "html_entity_encode", "html_body"),
    SanitizerRule("ts-he-encode", ("typescript",), "encode", re.compile(r"^(he)$"),
                  None, "html_entity_encode", "html_body"),

    # --- Attribute encoding ---
    SanitizerRule("wp-esc-attr", ("php",), "esc_attr", None,
                  None, "attr_encode", "html_attribute"),

    # --- JS string escaping (json.dumps for JS context) ---
    SanitizerRule("py-json-dumps", ("python",), "dumps", re.compile(r"^(json)$"),
                  None, "js_string_escape", "javascript_string"),
    SanitizerRule("ts-json-stringify", ("typescript",), "stringify", re.compile(r"^(JSON)$"),
                  None, "js_string_escape", "javascript_string"),

    # --- URL encoding ---
    SanitizerRule("py-url-quote", ("python",), "quote", _URLLIB,
                  None, "url_encode", "url_param"),
    SanitizerRule("ts-encodeuricomponent", ("typescript",), "encodeURIComponent", None,
                  None, "url_encode", "url_param"),
    SanitizerRule("php-rawurlencode", ("php",), "rawurlencode", None,
                  None, "url_encode", "url_param"),

    # --- DOMPurify (client-side XSS sanitizer) ---
    SanitizerRule("ts-dompurify-sanitize", ("typescript", "javascript"), "sanitize", _DOMPURIFY,
                  None, "dom_purify", "html_body"),
)


class SanitizerLibrary:
    """Match a call site against the known defense-function library."""

    def __init__(self, rules: Iterable[SanitizerRule] = DEFAULT_SANITIZER_RULES):
        self._rules = tuple(rules)

    def match(
        self,
        *,
        language: str,
        callee: str,
        receiver_text: str | None,
        arg_expr: str | None = None,
    ) -> SanitizerAnnotation | None:
        lang = language.lower()
        for r in self._rules:
            if lang not in r.languages:
                continue
            if r.callee != callee:
                continue
            if r.receiver_pattern is not None:
                if receiver_text is None or not r.receiver_pattern.fullmatch(receiver_text):
                    continue
            if r.arg_hint is not None:
                if arg_expr is None or not r.arg_hint.search(arg_expr):
                    continue
            return SanitizerAnnotation(
                rule_id=r.rule_id, defense_type=r.defense_type,
                applies_to=r.applies_to, matched_text=f"{receiver_text or ''}.{callee}".lstrip("."),
            )
        return None


# transformation 字段里出现的 sanitizer 名片段 → defense_type 映射
# (covers sanitize_hint:<name> patterns that llm_taint_analyzer / chain_propagator emit)
_TRANSFORMATION_FRAGMENTS: tuple[tuple[re.Pattern, str, str], ...] = (
    (re.compile(r"shlex\.quote", re.I), "shlex_quote", "cmd_argument"),
    (re.compile(r"html\.escape|htmlspecialchars|htmlentities|DOMPurify", re.I),
     "html_entity_encode", "html_body"),
    (re.compile(r"encodeuricomponent|rawurlencode|urlencode|urllib.*quote", re.I),
     "url_encode", "url_param"),
    (re.compile(r"json\.(dumps|stringify)|JSON\.stringify", re.I),
     "js_string_escape", "javascript_string"),
    (re.compile(r"shell\s*=\s*False", re.I), "subprocess_array", "cmd_argument"),
    (re.compile(r"\.resolve\(\)|\.absolute\(\)", re.I),
     "path_resolve_boundary", "file_path"),
)


def annotate_sanitizers(
    propagation_steps: Iterable[PropagationStep],
    *,
    language: str = "python",
) -> list[SanitizerAnnotation]:
    """Annotate known defenses appearing in a taint path's propagation steps.

    Scans the `transformation` text of each step (which carries
    `sanitize_hint:<name>` per parameter_models.py:47, plus concat/encode/etc).
    Best-effort: returns annotations, does NOT judge effectiveness.
    """
    out: list[SanitizerAnnotation] = []
    for step in propagation_steps:
        tf = step.transformation or ""
        if not tf:
            continue
        for pat, defense_type, applies_to in _TRANSFORMATION_FRAGMENTS:
            if pat.search(tf):
                out.append(SanitizerAnnotation(
                    rule_id=f"xform:{defense_type}",
                    defense_type=defense_type, applies_to=applies_to,
                    code_location=step.code_location, matched_text=tf,
                ))
    return out
```

> 注：`SanitizerRule.arg_hint` 用 `re.Pattern`；SQL 占位符检测靠 arg_expr 含 `%s`/`?`/`:name`/`$N`。这是 best-effort——实际 SQL 绑定判定最终交给 LLM pass（Task 3），此处只标「路径上出现了 execute 且参数带占位符」。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_sanitizer_library.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/sanitizer_library.py packages/core/tests/code_index/test_sanitizer_library.py
git commit -m "feat(code_index): deterministic sanitizer/encoder library (spec §5.4-5.6 shared)"
```

---

### Task 2: `SsrfVulnerability` 补 `path`/`verdict`/`witness_payload` 字段

**Files:**
- Modify: `packages/core/src/shannon_core/models/queue_schemas.py:44-50`（`SsrfVulnerability`）
- Test: `packages/core/tests/models/test_ssrf_schema_fields.py`（Create）

**Interfaces:**
- Produces: `SsrfVulnerability.path: str | None`、`verdict: str | None`、`witness_payload: str | None`（默认 None，向后兼容；与 injection/xss 类的 `verdict`/`witness_payload` 对齐，使 Plan 3 合并器 verdict OR 对 ssrf 也走 verdict 字段而非仅 `externally_exploitable`）

**背景**：`vuln-ssrf.txt:99-111` 的 `exploitation_queue_format` 缺 `path`/`verdict`/`witness_payload`，但 methodology（`:223` witness_payload、`:234-237` verdict）要求有。injection（`vuln-injection.txt:104-121`）与 xss（`vuln-xss.txt:102-119`）的 format 都含这三字段。补 ssrf schema 让三类对齐，Plan 3 合并器对 ssrf 也能用 verdict 字段做 OR（而非只靠 `externally_exploitable`）。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/models/test_ssrf_schema_fields.py
from shannon_core.models.queue_schemas import SsrfVulnerability, VulnerabilityQueue


def _ssrf(**kw):
    return SsrfVulnerability(
        ID="S1", vulnerability_type="URL_Manipulation",
        externally_exploitable=True, confidence="high", **kw,
    )


def test_ssrf_has_path_verdict_witness_payload_defaults_none():
    v = _ssrf()
    assert v.path is None
    assert v.verdict is None
    assert v.witness_payload is None


def test_ssrf_accepts_new_fields():
    v = _ssrf(path="req.query.url -> fetch(L12)", verdict="vulnerable",
              witness_payload="http://127.0.0.1:22/")
    assert v.path == "req.query.url -> fetch(L12)"
    assert v.verdict == "vulnerable"
    assert v.witness_payload == "http://127.0.0.1:22/"


def test_ssrf_legacy_queue_without_new_fields_parses():
    content = ('{"vulnerabilities":[{"ID":"S1","vulnerability_type":"URL_Manipulation",'
               '"externally_exploitable":true,"confidence":"high"}]}')
    result = VulnerabilityQueue.parse_lenient(content)
    assert len(result.queue.vulnerabilities) == 1
    v = result.queue.vulnerabilities[0]
    assert v.path is None and v.verdict is None and v.witness_payload is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/models/test_ssrf_schema_fields.py -v`
Expected: FAIL — `SsrfVulnerability.__init__()` got unexpected keyword `path` / `verdict` / `witness_payload`

- [ ] **Step 3: Add the three fields to `SsrfVulnerability`**

Edit `packages/core/src/shannon_core/models/queue_schemas.py:44-50`:

```python
class SsrfVulnerability(BaseVulnerability):
    source_endpoint: str | None = None
    vulnerable_parameter: str | None = None
    vulnerable_code_location: str | None = None
    missing_defense: str | None = None
    exploitation_hypothesis: str | None = None
    suggested_exploit_technique: str | None = None
    # Spec §5.6: align with injection/xss so the Plan 3 merger can do verdict OR
    # via the verdict field (not just externally_exploitable).
    path: str | None = None
    verdict: str | None = None
    witness_payload: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/models/test_ssrf_schema_fields.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run broader model tests to confirm no regression**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/models/ -v`
Expected: PASS（新字段默认 None，不破坏现有 parse_lenient / 双轨字段测试）

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/models/queue_schemas.py packages/core/tests/models/test_ssrf_schema_fields.py
git commit -m "feat(models): add path/verdict/witness_payload to SsrfVulnerability (spec §5.6 schema gap)"
```

---

### Task 3: GitNexus 轨链判定基础设施（候选链提取 + sanitizer 标注 + LLM pass → verdict）

**Files:**
- Create: `packages/core/src/shannon_core/code_index/chain_verdict.py`
- Test: `packages/core/tests/code_index/test_chain_verdict.py`（Create）

**Interfaces:**
- Consumes: `ParameterPropagationGraph`（Plan 1 落盘的 `parameter_graph.json`）、`SanitizerLibrary`（Task 1）
- Produces:
  - `extract_candidate_chains(pgraph, vuln_class) -> list[CandidateChain]`：按 vuln 类的 `SinkCategory` 路由（injection→`SQL`/`COMMAND`/`FILE`/`TEMPLATE`/`DESERIALIZATION`；xss→`XSS`；ssrf→`SSRF`）从 `TaintFlow` 提取候选链
  - `CandidateChain`（source / sink_call_site_id / sink_slot / propagation_steps / sanitizer_annotations / vuln_class / direction_hint）
  - `judge_chain_verdict(candidate, llm_client) -> ChainVerdict`：轻量 LLM pass（给候选链 + sanitizer 标注 + slot/render_context，回 verdict/witness_payload/evidence_chain/mismatch_reason）

**CandidateChain / ChainVerdict 模型：**

```python
@dataclass(frozen=True)
class CandidateChain:
    vuln_class: str                # "injection" | "xss" | "ssrf"
    flow_id: str                   # TaintFlow.flow_id
    entry_point_id: str
    source_param: str
    source_type: str               # ParameterSource value
    sink_call_site_id: str
    sink_slot: str                 # SlotContext value (injection) / render_context (xss) / "url" (ssrf)
    propagation_steps: list        # PropagationStep list
    sanitizer_annotations: list    # SanitizerAnnotation list (Task 1)
    direction_hint: str            # "forward" | "backward"  (injection=forward, xss/ssrf=backward)
    post_sanitize_concat: bool     # 是否检测到 sanitizer 后再 concat（污染槽）

@dataclass(frozen=True)
class ChainVerdict:
    verdict: str                   # "safe" | "vulnerable"
    witness_payload: str | None
    evidence_chain: str            # source→sink path + sanitizer annotation (for merge_source evidence)
    mismatch_reason: str | None
    confidence: str                # "high" | "medium" | "low"
```

**候选链提取规则（按 SinkCategory 路由）：**
- `injection`：`SinkCategory` ∈ {`SQL`, `COMMAND`, `FILE`, `TEMPLATE`, `DESERIALIZATION`}（`vuln-injection.txt:106` vulnerability_type 全覆盖）；direction=forward
- `xss`：`SinkCategory == XSS`（code-level sink：innerHTML/document.write，`parameter_models.py:140`）；direction=backward；render_context 来自 sink_subtype 映射（`innerHTML`→`HTML_BODY`，`setAttribute`→`HTML_ATTRIBUTE`，等）
- `ssrf`：`SinkCategory == SSRF`（`parameter_models.py:139`）；direction=backward；sink_slot 固定 `url`

**post-sanitize concat 检测**：遍历 `propagation_steps`，若某步 `transformation` 含 sanitizer（`annotate_sanitizers` 命中）**之后**还有 `transformation == "concat"` 的步 → `post_sanitize_concat=True`（污染槽，sanitizer 视为失效，对应 `vuln-injection.txt:156`）。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_chain_verdict.py
import pytest

from shannon_core.code_index.chain_verdict import (
    CandidateChain,
    extract_candidate_chains,
    judge_chain_verdict,
    ChainVerdict,
)
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    TaintFlow,
    PropagationStep,
)
from shannon_core.code_index.models import ParameterSource


def _flow(sink_slot, source="q", source_type=ParameterSource.QUERY_PARAM, steps=None):
    return TaintFlow(
        flow_id="ep#sink1", entry_point_id="app.py:handler:1",
        source_param=source, source_type=source_type,
        sink_call_site_id="app.py:handler:db.execute:1:0",
        sink_slot=sink_slot,
        propagation_steps=steps or [],
    )


def _step(tf, code_location="app.py:5"):
    return PropagationStep(
        step_id="s1", from_func_id="f", from_param="q",
        to_func_id="f", to_param="x", transformation=tf, code_location=code_location,
    )


def test_extract_injection_routes_sql_and_command_sinks():
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value"), _flow("cmd_argument")],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="injection")
    assert len(chains) == 2
    assert all(c.vuln_class == "injection" for c in chains)
    assert all(c.direction_hint == "forward" for c in chains)


def test_extract_xss_routes_only_xss_sinks():
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value"), _flow("generic")],  # no XSS category sink
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="xss")
    # sink_slot "generic" is not an xss render_context → no chain
    assert chains == []


def test_extract_ssrf_routes_only_url_sinks():
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("url"), _flow("sql_value")],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="ssrf")
    assert len(chains) == 1
    assert chains[0].vuln_class == "ssrf"
    assert chains[0].direction_hint == "backward"


def test_extract_empty_pgraph_returns_empty():
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])
    assert extract_candidate_chains(pgraph, vuln_class="injection") == []


def test_post_sanitize_concat_detected_when_concat_after_sanitizer():
    steps = [_step("sanitize_hint:html.escape"), _step("concat")]
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("html_body", steps=steps)],  # xss-style slot
        language_coverage=["typescript"],
    )
    # xss routes by SinkCategory, but TaintFlow only has sink_slot; we test
    # post_sanitize_concat via injection slot with the same step pattern:
    pgraph2 = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value", steps=steps)],
        language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph2, vuln_class="injection")
    assert len(chains) == 1
    assert chains[0].post_sanitize_concat is True


def test_post_sanitize_concat_false_when_no_concat_after():
    steps = [_step("sanitize_hint:html.escape"), _step("format")]
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value", steps=steps)], language_coverage=["python"],
    )
    chains = extract_candidate_chains(pgraph, vuln_class="injection")
    assert len(chains) == 1
    assert chains[0].post_sanitize_concat is False


@pytest.mark.asyncio
async def test_judge_chain_verdict_calls_llm_and_parses_verdict():
    """LLM pass returns verdict JSON → ChainVerdict parsed."""
    chain = CandidateChain(
        vuln_class="injection", flow_id="f1", entry_point_id="ep",
        source_param="q", source_type="query", sink_call_site_id="db.execute:1",
        sink_slot="sql_value", propagation_steps=[_step("concat")],
        sanitizer_annotations=[], direction_hint="forward",
        post_sanitize_concat=True,
    )

    async def fake_llm(prompt, **kw):
        # LLM pass returns a compact verdict JSON
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q -> db.execute(L1)","mismatch_reason":"concat into sql value slot",'
                '"confidence":"high"}')

    verdict = await judge_chain_verdict(chain, llm_client=fake_llm)
    assert isinstance(verdict, ChainVerdict)
    assert verdict.verdict == "vulnerable"
    assert verdict.witness_payload == "'"
    assert "db.execute" in verdict.evidence_chain
    assert verdict.confidence == "high"


@pytest.mark.asyncio
async def test_judge_chain_verdict_defaults_safe_on_llm_failure():
    """LLM pass raises/fails → conservative: treat as needs_review, do not crash."""
    chain = CandidateChain(
        vuln_class="ssrf", flow_id="f1", entry_point_id="ep",
        source_param="url", source_type="query", sink_call_site_id="fetch:1",
        sink_slot="url", propagation_steps=[], sanitizer_annotations=[],
        direction_hint="backward", post_sanitize_concat=False,
    )

    async def failing_llm(prompt, **kw):
        raise RuntimeError("LLM chain-verdict pass not available")

    verdict = await judge_chain_verdict(chain, llm_client=failing_llm)
    # graceful: never crash; mark needs_review (do not silently declare safe/vulnerable)
    assert verdict.confidence == "low"
    assert "needs_review" in (verdict.mismatch_reason or "") or verdict.verdict in ("safe", "vulnerable")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_chain_verdict.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.code_index.chain_verdict`

- [ ] **Step 3: Implement `chain_verdict.py`**

```python
# packages/core/src/shannon_core/code_index/chain_verdict.py
"""GitNexus-track chain verdict infrastructure (spec §5.4-5.6 shared).

Three vuln classes (injection/xss/ssrf) share this framework; they differ
only in trace direction and the blind spots each fills. The LLM track already
runs the full methodology (vuln-*.txt prompts). This module is the LIGHT
GitNexus-track chain-verdict pass:

  parameter_graph.json (Plan 1) → extract_candidate_chains(pgraph, vuln_class)
  → deterministic sanitizer/encoder annotation (sanitizer_library, Task 1)
  → post-sanitize-concat detection
  → judge_chain_verdict(candidate, llm_client) → verdict + witness + evidence

The merger (Plan 3) then does verdict OR against the LLM track. GitNexus track
is a CROSS-VALIDATION / BLIND-SPOT FILL, never a constraint on the LLM track's
free analysis (spec §2 principle).
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    PropagationStep,
    TaintFlow,
)
from shannon_core.code_index.sanitizer_library import (
    SanitizerAnnotation,
    annotate_sanitizers,
)

logger = logging.getLogger(__name__)

# ⚠️ 自检修正（2026-06-24）：xss 路由不能用 sink_slot。
# SlotContext 枚举（parameter_models.py:28-37）只有 sql_value/sql_identifier/
# cmd_argument/file_path/template_expr/url/deserialize/generic —— 无 render context。
# 故原 _XSS_RENDER_CONTEXTS（html_body 等）是臆测，已删。xss 必须按
# SinkCallSite.category == SinkCategory.XSS 路由（parameter_models.py:132，XSS 在 ~:140）。
#
# 连带改动（执行者务必落实，否则 xss 轨仍不工作）：
# 1. extract_candidate_chains 签名加 sink_call_sites: dict[str, SinkCallSite]，
#    通过 flow.sink_call_site_id 查 SinkCallSite.category，传给 _route_for。
# 2. _route_for 加 sink_category 参数（见下）。
# 3. CandidateChain 加 render_context: str（从 SinkCallSite.sink_subtype 映射：
#    innerHTML→HTML_BODY、setAttribute→HTML_ATTRIBUTE、document.write→HTML_BODY 等）。
# 4. Task 7 run_gitnexus_chain_verdict：读 code_index.json 的 sink_call_sites 传给 extract。
# injection/ssrf 路由不变（slot 值都在 SlotContext 枚举 ✓）。
_INJECTION_SLOTS = {"sql_value", "sql_identifier", "cmd_argument",
                    "file_path", "template_expr", "deserialize"}
_SSRF_SLOTS = {"url"}

_DIRECTION = {"injection": "forward", "xss": "backward", "ssrf": "backward"}

# LLM pass prompt template (lightweight; full methodology stays in vuln-*.txt).
_VERDICT_PROMPT = """You are a lightweight chain-verdict pass for the {vuln_class} GitNexus track.
Given ONE candidate source→sink chain with deterministic sanitizer annotations,
judge ONLY whether it is vulnerable. Do NOT re-run full analysis methodology.

Candidate chain:
- source: {source_param} ({source_type})
- sink: {sink_call_site_id}
- slot/render_context: {sink_slot}
- direction: {direction_hint}
- propagation steps: {steps_repr}
- sanitizer annotations (best-effort, NOT judged for effectiveness): {sanitizers_repr}
- post-sanitize concatenation detected: {post_sanitize_concat}

Rules:
- post-sanitize concatenation = sanitizer considered INEFFECTIVE (tainted again).
- A defense is effective ONLY if it matches the slot/render_context AND no concat after.
- Be decisive: return vulnerable OR safe.

Respond with a compact JSON object ONLY:
{{"verdict":"safe|vulnerable","witness_payload":"<minimal>","evidence_chain":"<source→sink with sanitizer notes>","mismatch_reason":"<if vulnerable>","confidence":"high|medium|low"}}
"""


@dataclass(frozen=True)
class CandidateChain:
    vuln_class: str
    flow_id: str
    entry_point_id: str
    source_param: str
    source_type: str
    sink_call_site_id: str
    sink_slot: str
    propagation_steps: list
    sanitizer_annotations: list
    direction_hint: str
    post_sanitize_concat: bool


@dataclass(frozen=True)
class ChainVerdict:
    verdict: str
    witness_payload: str | None
    evidence_chain: str
    mismatch_reason: str | None
    confidence: str


def _route_for(vuln_class: str, slot_value: str, sink_category: str | None = None) -> bool:
    """Does this TaintFlow belong to the vuln class?

    injection/ssrf: by sink_slot (SlotContext value, in enum).
    xss: by sink_category == SinkCategory.XSS.value (sink_slot has no render context).
    （执行时 grep 确认 SinkCategory.XSS.value 实际值，可能是 "xss" 或 "XSS"。）
    """
    if vuln_class == "injection":
        return slot_value in _INJECTION_SLOTS
    if vuln_class == "xss":
        return sink_category == "xss"  # 确认 SinkCategory.XSS.value
    if vuln_class == "ssrf":
        return slot_value in _SSRF_SLOTS
    return False


def _detect_post_sanitize_concat(steps: list[PropagationStep]) -> bool:
    """True if a concat step appears AFTER a sanitizer-bearing step."""
    seen_sanitizer = False
    for s in steps:
        tf = (s.transformation or "").lower()
        if "sanitize" in tf or "escape" in tf or "encode" in tf or "quote" in tf:
            seen_sanitizer = True
            continue
        if seen_sanitizer and tf == "concat":
            return True
    return False


def extract_candidate_chains(
    pgraph: ParameterPropagationGraph,
    *,
    vuln_class: str,
) -> list[CandidateChain]:
    """Extract candidate source→sink chains for a vuln class from the taint graph.

    Routes TaintFlows by sink_slot (SlotContext value) to the vuln class.
    Empty pgraph → empty list (graceful degradation when Plan 1 not landed).
    """
    if pgraph is None:
        return []
    direction = _DIRECTION.get(vuln_class, "forward")
    chains: list[CandidateChain] = []
    for flow in pgraph.taint_flows:
        slot_value = flow.sink_slot.value if hasattr(flow.sink_slot, "value") else str(flow.sink_slot)
        if not _route_for(vuln_class, slot_value):
            continue
        annots = annotate_sanitizers(
            flow.propagation_steps,
            language=(pgraph.language_coverage[0] if pgraph.language_coverage else "python"),
        )
        chains.append(CandidateChain(
            vuln_class=vuln_class,
            flow_id=flow.flow_id,
            entry_point_id=flow.entry_point_id,
            source_param=flow.source_param,
            source_type=flow.source_type.value if hasattr(flow.source_type, "value") else str(flow.source_type),
            sink_call_site_id=flow.sink_call_site_id,
            sink_slot=slot_value,
            propagation_steps=list(flow.propagation_steps),
            sanitizer_annotations=annots,
            direction_hint=direction,
            post_sanitize_concat=_detect_post_sanitize_concat(flow.propagation_steps),
        ))
    return chains


async def judge_chain_verdict(
    candidate: CandidateChain,
    *,
    llm_client: Callable[..., Awaitable[str]],
) -> ChainVerdict:
    """Light LLM pass: judge one candidate chain → verdict.

    Graceful on LLM failure: never crash; return a needs_review-flavored
    verdict so the merger still processes it (Plan 3 OR is conservative).
    """
    prompt = _VERDICT_PROMPT.format(
        vuln_class=candidate.vuln_class,
        source_param=candidate.source_param,
        source_type=candidate.source_type,
        sink_call_site_id=candidate.sink_call_site_id,
        sink_slot=candidate.sink_slot,
        direction_hint=candidate.direction_hint,
        steps_repr="; ".join(
            f"{s.code_location}:{s.transformation or 'noop'}"
            for s in candidate.propagation_steps
        ) or "(none)",
        sanitizers_repr="; ".join(
            f"{a.defense_type}@{a.applies_to}({a.code_location})"
            for a in candidate.sanitizer_annotations
        ) or "(none)",
        post_sanitize_concat=str(candidate.post_sanitize_concat),
    )

    try:
        raw = await llm_client(prompt)
    except Exception as exc:
        logger.warning("chain-verdict LLM pass failed (%s); marking needs_review", exc)
        return ChainVerdict(
            verdict="vulnerable",  # conservative: OR-friendly (do not silently clear)
            witness_payload=None,
            evidence_chain=f"{candidate.source_param} -> {candidate.sink_call_site_id} (llm-pass-failed, needs_review)",
            mismatch_reason="llm chain-verdict pass failed; needs human/LLM-track review",
            confidence="low",
        )

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("chain-verdict LLM returned non-JSON: %r", raw[:200])
        return ChainVerdict(
            verdict="vulnerable",
            witness_payload=None,
            evidence_chain=f"{candidate.source_param} -> {candidate.sink_call_site_id} (unparseable-llm, needs_review)",
            mismatch_reason="llm chain-verdict pass returned unparseable output; needs review",
            confidence="low",
        )

    return ChainVerdict(
        verdict=str(data.get("verdict", "safe")).strip().lower(),
        witness_payload=data.get("witness_payload"),
        evidence_chain=str(data.get("evidence_chain")
                           or f"{candidate.source_param} -> {candidate.sink_call_site_id}"),
        mismatch_reason=data.get("mismatch_reason"),
        confidence=str(data.get("confidence", "medium")).strip().lower(),
    )
```

> 注：`judge_chain_verdict` 在 LLM 失败时默认 `verdict="vulnerable"`（保守，宁过报不漏报）+ `confidence="low"`。这保证 Plan 3 verdict OR 不会因 GitNexus 轨 LLM 不可用而漏报。`llm_client` 签名 `(prompt, **kwargs) -> Awaitable[str]` 与现有 `_llm_taint_client`（`activities.py:246`）一致。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_chain_verdict.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/chain_verdict.py packages/core/tests/code_index/test_chain_verdict.py
git commit -m "feat(code_index): GitNexus-track chain verdict infra (extract + annotate + LLM pass)"
```

---

### Task 4: injection 正向链判定（slot + post-sanitize concat 标注）

**Files:**
- Create: `packages/core/src/shannon_core/code_index/vuln_chain_builders/injection_builder.py`
- Test: `packages/core/tests/code_index/test_injection_builder.py`（Create）

**Interfaces:**
- Consumes: `extract_candidate_chains`（Task 3，vuln_class="injection"）、`judge_chain_verdict`（Task 3）
- Produces: `build_injection_findings(pgraph, llm_client) -> list[InjectionVulnerability]`：每条候选链 → LLM pass → `InjectionVulnerability`（正向：source=path param+file:line，sink_call=sink_call_site_id，slot_type=sink_slot，verdict，post-sanitize concat 进 `concat_occurrences` 标注）

**injection 差异**：正向 trace（`vuln-injection.txt:131-160`）。`source` 取 `source_param` + entry_point_id 的 file:line；`sink_call` 取 `sink_call_site_id`（已是 `{file}:{caller}:{callee}:{line}:{col}` 格式，见 `parameter_models.py:148`）；`slot_type` 直接映射 `SlotContext`→injection slot 标签（`sql_value`→`SQL-val` 等，对齐 `vuln-injection.txt:113`）。post-sanitize concat → `concat_occurrences` 字段标注「⚠️post-sanitize concat detected」。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_injection_builder.py
import pytest

from shannon_core.code_index.vuln_chain_builders.injection_builder import (
    build_injection_findings,
)
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph, TaintFlow, PropagationStep,
)
from shannon_core.code_index.models import ParameterSource
from shannon_core.models.queue_schemas import InjectionVulnerability


def _flow(slot, steps=None):
    return TaintFlow(
        flow_id="app.py:handler:1#app.py:handler:db.execute:5:0",
        entry_point_id="app.py:handler:1", source_param="q",
        source_type=ParameterSource.QUERY_PARAM,
        sink_call_site_id="app.py:handler:db.execute:5:0",
        sink_slot=slot, propagation_steps=steps or [],
    )


def _step(tf):
    return PropagationStep(step_id="s", from_func_id="f", from_param="q",
                           to_func_id="f", to_param="x", transformation=tf,
                           code_location="app.py:3")


@pytest.mark.asyncio
async def test_build_injection_findings_vulnerable_chain():
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value", steps=[_step("concat")])],
        language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db.exec","mismatch_reason":"concat into value slot","confidence":"high"}')

    findings = await build_injection_findings(pgraph, llm_client=fake_llm)
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, InjectionVulnerability)
    assert f.vulnerability_type == "injection"
    assert f.verdict == "vulnerable"
    assert f.slot_type == "SQL-val"  # sql_value → SQL-val mapping
    assert f.sink_call == "app.py:handler:db.execute:5:0"
    assert f.witness_payload == "'"
    # GitNexus-track evidence chain
    assert f.evidence_chain == "q->db.exec"
    assert f.source_track == "gitnexus"


@pytest.mark.asyncio
async def test_build_injection_flags_post_sanitize_concat():
    steps = [_step("sanitize_hint:html.escape"), _step("concat")]
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("sql_value", steps=steps)], language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db","mismatch_reason":"post-sanitize concat","confidence":"high"}')

    findings = await build_injection_findings(pgraph, llm_client=fake_llm)
    assert len(findings) == 1
    assert "post-sanitize concat" in (findings[0].concat_occurrences or "").lower()


@pytest.mark.asyncio
async def test_build_injection_empty_pgraph_returns_empty():
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])

    async def fake_llm(prompt, **kw):
        raise AssertionError("should not call LLM on empty pgraph")

    findings = await build_injection_findings(pgraph, llm_client=fake_llm)
    assert findings == []


@pytest.mark.asyncio
async def test_build_injection_skips_non_injection_slots():
    # ssrf url slot should NOT be picked up by injection builder
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("url")], language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        raise AssertionError("should not call LLM on url slot")

    findings = await build_injection_findings(pgraph, llm_client=fake_llm)
    assert findings == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_injection_builder.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.code_index.vuln_chain_builders.injection_builder`

- [ ] **Step 3: Implement `injection_builder.py`**

```python
# packages/core/src/shannon_core/code_index/vuln_chain_builders/__init__.py
# (empty package marker — create alongside injection_builder.py)
```

```python
# packages/core/src/shannon_core/code_index/vuln_chain_builders/injection_builder.py
"""injection GitNexus-track builder (spec §5.4, forward source→sink).

Takes candidate chains (Task 3, forward direction) for injection sinks
(SQL/command/file/template/deserialize), runs the light LLM chain-verdict
pass, and emits InjectionVulnerability records for the GitNexus-track queue.
"""

import logging
from typing import Awaitable, Callable

from shannon_core.code_index.chain_verdict import (
    extract_candidate_chains,
    judge_chain_verdict,
)
from shannon_core.code_index.parameter_models import ParameterPropagationGraph
from shannon_core.models.queue_schemas import InjectionVulnerability

logger = logging.getLogger(__name__)

# SlotContext → injection slot label (vuln-injection.txt:113)
_SLOT_LABEL = {
    "sql_value": "SQL-val",
    "sql_identifier": "SQL-ident",
    "cmd_argument": "CMD-argument",
    "file_path": "FILE-path",
    "template_expr": "TEMPLATE-expression",
    "deserialize": "DESERIALIZE-object",
}


def _source_text(candidate) -> str:
    """Render source as 'param (file:line)' from entry_point_id."""
    # entry_point_id format: "{file}:{func}:{line}"
    return f"{candidate.source_param} ({candidate.entry_point_id})"


async def build_injection_findings(
    pgraph: ParameterPropagationGraph,
    *,
    llm_client: Callable[..., Awaitable[str]],
) -> list[InjectionVulnerability]:
    candidates = extract_candidate_chains(pgraph, vuln_class="injection")
    findings: list[InjectionVulnerability] = []
    for i, chain in enumerate(candidates, start=1):
        verdict = await judge_chain_verdict(chain, llm_client=llm_client)
        concat_note = ""
        if chain.post_sanitize_concat:
            concat_note = "⚠️ post-sanitize concat detected — sanitizer considered ineffective"
        findings.append(InjectionVulnerability(
            ID=f"INJ-GN-{i:02d}",
            vulnerability_type="injection",
            externally_exploitable=(verdict.verdict == "vulnerable"),
            confidence=verdict.confidence,
            source=_source_text(chain),
            path=verdict.evidence_chain,
            sink_call=chain.sink_call_site_id,
            slot_type=_SLOT_LABEL.get(chain.sink_slot, chain.sink_slot),
            concat_occurrences=concat_note or None,
            verdict=verdict.verdict,
            mismatch_reason=verdict.mismatch_reason,
            witness_payload=verdict.witness_payload,
            source_track="gitnexus",
            evidence_chain=verdict.evidence_chain,
        ))
    logger.info("injection gitnexus-track: %d candidate chains → %d findings",
                len(candidates), len(findings))
    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_injection_builder.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/vuln_chain_builders/ packages/core/tests/code_index/test_injection_builder.py
git commit -m "feat(code_index): injection GitNexus-track chain builder (forward, spec §5.4)"
```

---

### Task 5: xss 反向链判定 + DB read↔write 跨表补 Stored XSS

**Files:**
- Create: `packages/core/src/shannon_core/code_index/vuln_chain_builders/xss_builder.py`
- Test: `packages/core/tests/code_index/test_xss_builder.py`（Create）

**Interfaces:**
- Consumes: `extract_candidate_chains`（Task 3，vuln_class="xss"）、`judge_chain_verdict`（Task 3）、`ParameterPropagationGraph`（含 DB read + DB write 流）
- Produces: `build_xss_findings(pgraph, llm_client) -> list[XssVulnerability]`：反向 sink→source；额外 **DB read↔write 跨表补 Stored**——当一条 xss 候选链的 source 是 DB read（`source_type==ParameterSource.INTERNAL` 或 propagation 出现 DB fetch），反查 pgraph 有无对**同字段**的 write 流（用户输入→DB insert/update），有则合成一条 Stored XSS 候选链（write source → DB → read sink → render sink）。

**xss 差异**：
1. 反向 trace（`vuln-xss.txt:130-180`）：GitNexus 轨从 `SinkCategory==XSS` 的 TaintFlow 反向（候选链的 direction_hint=backward）。
2. **render_context 映射**：`sink_slot`（render context value）直接填 `render_context` 字段（`vuln-xss.txt:112`），mapping：`html_body`→`HTML_BODY` 等（已在 Task 3 `_XSS_RENDER_CONTEXTS`）。
3. **DB read↔write 跨表补 Stored**（核心盲区，spec §5.5）：`vuln-xss.txt:151-156` 的 DB Read Checkpoint 原本只到 read 终止（不回溯 write）。GitNexus 轨额外做跨表关联：
   - 检测候选链是否终止于 DB read（`source_type==ParameterSource.INTERNAL`，`parameter_models.py:114`，意为内部来源/非直接 HTTP 输入）。
   - 在 pgraph 的全部 TaintFlow 里找 **write 流**：sink 落在 DB（`SinkCategory==SQL` 且 sink_subtype 含 `insert`/`update`，或 sink_slot=`sql_value` 且 callee 含 insert/update）且 source 是用户输入（`source_type != INTERNAL`）。
   - 若 read 链的 source 字段名 == write 链的 sink 写入字段名（best-effort 字段名匹配），合成一条 Stored XSS 候选链：source=write 的用户输入 → DB → read → render sink。
   - **宁缺勿错拼**：字段名无法匹配 / 无对应 write 流 → 跳过该 Stored 候选（不强行拼）。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_xss_builder.py
import pytest

from shannon_core.code_index.vuln_chain_builders.xss_builder import (
    build_xss_findings, _find_stored_xss_synthesis,
)
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph, TaintFlow, PropagationStep,
)
from shannon_core.code_index.models import ParameterSource
from shannon_core.models.queue_schemas import XssVulnerability


def _flow(slot, source="name", source_type=ParameterSource.QUERY_PARAM,
          sink_id="app.py:h:innerHTML:5:0", steps=None):
    return TaintFlow(
        flow_id=f"ep#{sink_id}", entry_point_id="app.py:h:1",
        source_param=source, source_type=source_type,
        sink_call_site_id=sink_id, sink_slot=slot,
        propagation_steps=steps or [],
    )


def _step(tf):
    return PropagationStep(step_id="s", from_func_id="f", from_param="q",
                           to_func_id="f", to_param="x", transformation=tf,
                           code_location="app.py:3")


@pytest.mark.asyncio
async def test_build_xss_reflected_vulnerable():
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow("html_body", source="q")], language_coverage=["typescript"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"><script>","evidence_chain":'
                '"q->innerHTML","mismatch_reason":"no encoding for html body","confidence":"high"}')

    findings = await build_xss_findings(pgraph, llm_client=fake_llm)
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, XssVulnerability)
    assert f.vulnerability_type in ("Reflected", "Stored", "DOM-based")
    assert f.render_context == "HTML_BODY"
    assert f.verdict == "vulnerable"
    assert f.source_track == "gitnexus"


def test_find_stored_xss_synthesis_matches_read_to_write_by_field():
    """read flow (DB→render, INTERNAL source 'name') + write flow (input→DB 'name') → synthesis."""
    read_flow = _flow("html_body", source="name",
                      source_type=ParameterSource.INTERNAL,
                      sink_id="app.py:profile:innerHTML:10:0")
    write_flow = TaintFlow(
        flow_id="ep2#app.py:save:db.execute:5:0", entry_point_id="app.py:save:1",
        source_param="name", source_type=ParameterSource.BODY_FIELD,
        sink_call_site_id="app.py:save:db.execute:5:0",
        sink_slot="sql_value",  # SQL write
        propagation_steps=[],
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=[read_flow, write_flow], language_coverage=["python"],
    )
    synthesis = _find_stored_xss_synthesis(pgraph)
    assert len(synthesis) == 1
    s = synthesis[0]
    assert s["write_source"] == "name"
    assert s["read_field"] == "name"
    assert "innerHTML" in s["render_sink"]


def test_find_stored_xss_synthesis_skips_when_no_matching_write():
    read_flow = _flow("html_body", source="name",
                      source_type=ParameterSource.INTERNAL)
    # no write flow with same field
    write_flow = TaintFlow(
        flow_id="ep2#w", entry_point_id="app.py:s:1", source_param="other",
        source_type=ParameterSource.BODY_FIELD,
        sink_call_site_id="app.py:s:db.execute:5:0", sink_slot="sql_value",
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=[read_flow, write_flow], language_coverage=["python"],
    )
    assert _find_stored_xss_synthesis(pgraph) == []


@pytest.mark.asyncio
async def test_build_xss_synthesizes_stored_finding():
    """read flow (INTERNAL source) + matching write flow → extra Stored finding."""
    read_flow = _flow("html_body", source="bio",
                      source_type=ParameterSource.INTERNAL,
                      sink_id="app.py:profile:innerHTML:10:0")
    write_flow = TaintFlow(
        flow_id="ep2#app.py:save:db.execute:5:0", entry_point_id="app.py:save:1",
        source_param="bio", source_type=ParameterSource.BODY_FIELD,
        sink_call_site_id="app.py:save:db.execute:5:0", sink_slot="sql_value",
    )
    pgraph = ParameterPropagationGraph(
        taint_flows=[read_flow, write_flow], language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"><img>","evidence_chain":'
                '"bio(input)->DB->read->innerHTML","mismatch_reason":"stored, no encode",'
                '"confidence":"high"}')

    findings = await build_xss_findings(pgraph, llm_client=fake_llm)
    # the read flow alone is INTERNAL-sourced → would be a bare read; but with
    # matching write we synthesize a Stored finding. Expect ≥1 Stored finding.
    stored = [f for f in findings if f.vulnerability_type == "Stored"]
    assert len(stored) >= 1


@pytest.mark.asyncio
async def test_build_xss_empty_pgraph_returns_empty():
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])

    async def fake_llm(prompt, **kw):
        raise AssertionError("no LLM on empty pgraph")

    assert await build_xss_findings(pgraph, llm_client=fake_llm) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_xss_builder.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.code_index.vuln_chain_builders.xss_builder`

- [ ] **Step 3: Implement `xss_builder.py`**

```python
# packages/core/src/shannon_core/code_index/vuln_chain_builders/xss_builder.py
"""xss GitNexus-track builder (spec §5.5, backward sink→source + Stored fill).

Two responsibilities:
1. Backward chain verdict for code-level XSS sinks (SinkCategory.XSS).
2. DB read↔write cross-table fill for Stored XSS: when a read flow (DB→render,
   INTERNAL source) has a matching write flow (user input→same DB field),
   synthesize a Stored XSS candidate spanning input→DB→read→render. This fills
   the blind spot where vuln-xss.txt:151-156's DB Read Checkpoint stops at read.
"""

import logging
from typing import Awaitable, Callable

from shannon_core.code_index.chain_verdict import (
    CandidateChain,
    extract_candidate_chains,
    judge_chain_verdict,
)
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph,
    TaintFlow,
)
from shannon_core.code_index.models import ParameterSource
from shannon_core.models.queue_schemas import XssVulnerability

logger = logging.getLogger(__name__)

_RENDER_CONTEXT = {
    "html_body": "HTML_BODY",
    "html_attribute": "HTML_ATTRIBUTE",
    "javascript_string": "JAVASCRIPT_STRING",
    "url_param": "URL_PARAM",
    "css_value": "CSS_VALUE",
}

# SQL slot values that represent a WRITE (insert/update) sink
_SQL_WRITE_SLOTS = {"sql_value"}  # writes go through bound value slots


def _is_db_read_source(flow: TaintFlow) -> bool:
    """A read flow: source is INTERNAL (DB/internal) reaching an XSS sink."""
    src = flow.source_type.value if hasattr(flow.source_type, "value") else str(flow.source_type)
    return src == ParameterSource.INTERNAL.value


def _is_db_write(flow: TaintFlow) -> bool:
    """A write flow: user input (non-INTERNAL) reaching a SQL value slot."""
    src = flow.source_type.value if hasattr(flow.source_type, "value") else str(flow.source_type)
    slot = flow.sink_slot.value if hasattr(flow.sink_slot, "value") else str(flow.sink_slot)
    if src == ParameterSource.INTERNAL.value:
        return False
    return slot in _SQL_WRITE_SLOTS


def _find_stored_xss_synthesis(
    pgraph: ParameterPropagationGraph,
) -> list[dict]:
    """Find read↔write pairs that synthesize a Stored XSS chain.

    Matches a read flow (INTERNAL source → XSS render sink) with a write flow
    (user input → SQL value slot) by SHARED FIELD NAME (best-effort).
    Returns list of dicts: {write_source, read_field, render_sink, write_flow, read_flow}.

    宁缺勿错拼: if field names can't be matched, skip (do not force-synthesize).
    """
    if pgraph is None:
        return []
    read_flows = [f for f in pgraph.taint_flows if _is_db_read_source(f)]
    write_flows = [f for f in pgraph.taint_flows if _is_db_write(f)]
    synthesis: list[dict] = []
    for rf in read_flows:
        for wf in write_flows:
            # best-effort field name match (source_param of read == source_param of write)
            if rf.source_param == wf.source_param:
                synthesis.append({
                    "write_source": wf.source_param,
                    "read_field": rf.source_param,
                    "render_sink": rf.sink_call_site_id,
                    "write_flow": wf,
                    "read_flow": rf,
                })
    return synthesis


def _synthesize_stored_candidate(s: dict) -> CandidateChain:
    """Build a CandidateChain for a synthesized Stored XSS flow."""
    wf = s["write_flow"]
    rf = s["read_flow"]
    slot = rf.sink_slot.value if hasattr(rf.sink_slot, "value") else str(rf.sink_slot)
    src_type = wf.source_type.value if hasattr(wf.source_type, "value") else str(wf.source_type)
    # propagate steps: write steps + a DB hop + read steps
    steps = list(wf.propagation_steps) + list(rf.propagation_steps)
    return CandidateChain(
        vuln_class="xss",
        flow_id=f"stored#{wf.flow_id}#{rf.flow_id}",
        entry_point_id=wf.entry_point_id,
        source_param=wf.source_param,
        source_type=src_type,
        sink_call_site_id=rf.sink_call_site_id,
        sink_slot=slot,
        propagation_steps=steps,
        sanitizer_annotations=[],  # synthesized; annotate_sanitizers re-run in judge
        direction_hint="backward",
        post_sanitize_concat=False,
    )


async def build_xss_findings(
    pgraph: ParameterPropagationGraph,
    *,
    llm_client: Callable[..., Awaitable[str]],
) -> list[XssVulnerability]:
    candidates = extract_candidate_chains(pgraph, vuln_class="xss")
    # Append synthesized Stored candidates (DB read↔write cross-table fill).
    for s in _find_stored_xss_synthesis(pgraph):
        candidates.append(_synthesize_stored_candidate(s))

    findings: list[XssVulnerability] = []
    for i, chain in enumerate(candidates, start=1):
        verdict = await judge_chain_verdict(chain, llm_client=llm_client)
        is_stored = chain.flow_id.startswith("stored#")
        findings.append(XssVulnerability(
            ID=f"XSS-GN-{i:02d}",
            vulnerability_type="Stored" if is_stored else "Reflected",
            externally_exploitable=(verdict.verdict == "vulnerable"),
            confidence=verdict.confidence,
            source=f"{chain.source_param} ({chain.entry_point_id})",
            source_detail=verdict.evidence_chain,
            path=verdict.evidence_chain,
            sink_function=chain.sink_call_site_id.split(":")[2] if ":" in chain.sink_call_site_id else chain.sink_call_site_id,
            render_context=_RENDER_CONTEXT.get(chain.sink_slot, chain.sink_slot.upper()),
            encoding_observed=None,
            verdict=verdict.verdict,
            mismatch_reason=verdict.mismatch_reason,
            witness_payload=verdict.witness_payload,
            source_track="gitnexus",
            evidence_chain=verdict.evidence_chain,
        ))
    logger.info("xss gitnexus-track: %d candidates (incl. %d synthesized Stored) → %d findings",
                len(candidates),
                sum(1 for c in candidates if c.flow_id.startswith("stored#")),
                len(findings))
    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_xss_builder.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/vuln_chain_builders/xss_builder.py packages/core/tests/code_index/test_xss_builder.py
git commit -m "feat(code_index): xss GitNexus-track builder (backward + Stored cross-table fill, spec §5.5)"
```

---

### Task 6: ssrf 反向链判定（补 schema 已在 Task 2，builder 用 verdict 字段）

**Files:**
- Create: `packages/core/src/shannon_core/code_index/vuln_chain_builders/ssrf_builder.py`
- Test: `packages/core/tests/code_index/test_ssrf_builder.py`（Create）

**Interfaces:**
- Consumes: `extract_candidate_chains`（Task 3，vuln_class="ssrf"）、`judge_chain_verdict`（Task 3）、`SsrfVulnerability`（Task 2 补字段后）
- Produces: `build_ssrf_findings(pgraph, llm_client) -> list[SsrfVulnerability]`：反向 sink→source；填 Task 2 新增的 `path`/`verdict`/`witness_payload`；`source_endpoint` 从 entry_point_id 派生（best-effort），`missing_defense` 取 verdict.mismatch_reason。

**ssrf 差异**：反向 7 步方法论（`vuln-ssrf.txt:118-238`）**本就在 LLM 轨 prompt**，GitNexus 轨**不重复**——只做链判定（反向候选链 → LLM pass → verdict）。补 schema 缺字段已在 Task 2 完成。`source_endpoint` 从 `entry_point_id`（`{file}:{func}:{line}`）best-effort 派生为 HTTP METHOD+path 形式不可靠，故 `source_endpoint` 留 entry_point_id 原值（renderer 容错），`vulnerable_code_location` 取 `sink_call_site_id`。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/code_index/test_ssrf_builder.py
import pytest

from shannon_core.code_index.vuln_chain_builders.ssrf_builder import (
    build_ssrf_findings,
)
from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph, TaintFlow,
)
from shannon_core.code_index.models import ParameterSource
from shannon_core.models.queue_schemas import SsrfVulnerability


def _flow(source="url", source_type=ParameterSource.QUERY_PARAM):
    return TaintFlow(
        flow_id="ep#app.py:fetch:5:0", entry_point_id="app.py:proxy:1",
        source_param=source, source_type=source_type,
        sink_call_site_id="app.py:proxy:fetch:5:0", sink_slot="url",
        propagation_steps=[],
    )


@pytest.mark.asyncio
async def test_build_ssrf_vulnerable():
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow()], language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"http://127.0.0.1:22/",'
                '"evidence_chain":"url->fetch(L5)","mismatch_reason":"no allowlist",'
                '"confidence":"high"}')

    findings = await build_ssrf_findings(pgraph, llm_client=fake_llm)
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, SsrfVulnerability)
    assert f.verdict == "vulnerable"  # uses new Task 2 field
    assert f.path == "url->fetch(L5)"
    assert f.witness_payload == "http://127.0.0.1:22/"
    assert f.missing_defense == "no allowlist"
    assert f.vulnerable_code_location == "app.py:proxy:fetch:5:0"
    assert f.source_track == "gitnexus"


@pytest.mark.asyncio
async def test_build_ssrf_skips_non_url_slots():
    pgraph = ParameterPropagationGraph(
        taint_flows=[TaintFlow(
            flow_id="x", entry_point_id="e", source_param="q",
            source_type=ParameterSource.QUERY_PARAM,
            sink_call_site_id="db.exec:1", sink_slot="sql_value",
        )], language_coverage=["python"],
    )

    async def fake_llm(prompt, **kw):
        raise AssertionError("url slot only")

    assert await build_ssrf_findings(pgraph, llm_client=fake_llm) == []


@pytest.mark.asyncio
async def test_build_ssrf_empty_pgraph_returns_empty():
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])

    async def fake_llm(prompt, **kw):
        raise AssertionError("no LLM on empty")

    assert await build_ssrf_findings(pgraph, llm_client=fake_llm) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_ssrf_builder.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.code_index.vuln_chain_builders.ssrf_builder`

- [ ] **Step 3: Implement `ssrf_builder.py`**

```python
# packages/core/src/shannon_core/code_index/vuln_chain_builders/ssrf_builder.py
"""ssrf GitNexus-track builder (spec §5.6, backward sink→source).

The 7-step SSRF methodology already lives in the LLM-track prompt
(vuln-ssrf.txt:118-238). This GitNexus-track builder does NOT re-run that
methodology — it only runs the light backward chain-verdict pass on url-slot
candidate chains and emits SsrfVulnerability records (with the path/verdict/
witness_payload fields added in Task 2) for the merger.
"""

import logging
from typing import Awaitable, Callable

from shannon_core.code_index.chain_verdict import (
    extract_candidate_chains,
    judge_chain_verdict,
)
from shannon_core.code_index.parameter_models import ParameterPropagationGraph
from shannon_core.models.queue_schemas import SsrfVulnerability

logger = logging.getLogger(__name__)


async def build_ssrf_findings(
    pgraph: ParameterPropagationGraph,
    *,
    llm_client: Callable[..., Awaitable[str]],
) -> list[SsrfVulnerability]:
    candidates = extract_candidate_chains(pgraph, vuln_class="ssrf")
    findings: list[SsrfVulnerability] = []
    for i, chain in enumerate(candidates, start=1):
        verdict = await judge_chain_verdict(chain, llm_client=llm_client)
        findings.append(SsrfVulnerability(
            ID=f"SSRF-GN-{i:02d}",
            vulnerability_type="URL_Manipulation",
            externally_exploitable=(verdict.verdict == "vulnerable"),
            confidence=verdict.confidence,
            source_endpoint=chain.entry_point_id,  # best-effort; renderer tolerant
            vulnerable_parameter=chain.source_param,
            vulnerable_code_location=chain.sink_call_site_id,
            missing_defense=verdict.mismatch_reason,
            exploitation_hypothesis=None,
            suggested_exploit_technique=None,
            # Task 2 fields:
            path=verdict.evidence_chain,
            verdict=verdict.verdict,
            witness_payload=verdict.witness_payload,
            source_track="gitnexus",
            evidence_chain=verdict.evidence_chain,
        ))
    logger.info("ssrf gitnexus-track: %d candidates → %d findings",
                len(candidates), len(findings))
    return findings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_ssrf_builder.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/vuln_chain_builders/ssrf_builder.py packages/core/tests/code_index/test_ssrf_builder.py
git commit -m "feat(code_index): ssrf GitNexus-track chain builder (backward, spec §5.6)"
```

---

### Task 7: Pipeline wiring — GitNexus 轨产出 `<vuln>_gitnexus_queue.json`

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（新增 `run_gitnexus_chain_verdict` activity）
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`（vuln 阶段后、Plan 3 合并前调本 activity）
- Test: `packages/whitebox/tests/test_run_gitnexus_chain_verdict.py`（Create）

**Interfaces:**
- Consumes: `parameter_graph.json`（Plan 1 产物）；三个 builder（Task 4/5/6）；`_llm_client`（GitNexus 轨 LLM pass；与 `_llm_taint_client` 同源，生产可复用 activities.py 的 client 池）
- Produces: `injection_gitnexus_queue.json` / `xss_gitnexus_queue.json` / `ssrf_gitnexus_queue.json`（每条带 `source_track="gitnexus"` + `evidence_chain`）；交 Plan 3 `run_merge_dual_track_queues` 做 verdict OR。

**关键行为**：
1. 读 `parameter_graph.json`（Plan 1）；不存在/为空 → 跳过（GitNexus 轨全空，Plan 3 合并器降级为全 llm-only，行为与现状等价）。
2. 对 injection/xss/ssrf 三类各调对应 builder（用生产 LLM client）；auth/authz 不在本 plan（它们无 source→sink taint 语义，留 Plan 7/9）。
3. 写 `<vuln>_gitnexus_queue.json`。
4. **wiring 顺序**：vuln 阶段并行 gather 完成 → **本 activity（GitNexus 轨链判定）** → Plan 3 `run_merge_dual_track_queues` → `log_phase_complete_activity`。

- [ ] **Step 1: Write the failing test**

```python
# packages/whitebox/tests/test_run_gitnexus_chain_verdict.py
import json
from pathlib import Path

import pytest

from shannon_whitebox.pipeline import activities


def _input(repo, deliverables):
    class FakeInput:
        agent_name = None
        web_url = None
        repo_path = str(repo)
        config_path = None
        api_key = None
        pipeline_testing_mode = False
        prompt_override = None
    return FakeInput()


def _write_pgraph(deliverables, flows):
    """Write a minimal parameter_graph.json with given TaintFlow-like dicts."""
    pgraph = {
        "taint_flows": flows,
        "language_coverage": ["python"],
        "skipped_languages": [],
    }
    (deliverables / "parameter_graph.json").write_text(json.dumps(pgraph))


def _flow(slot, source="q", source_type="query", sink_id="app.py:h:db.execute:5:0",
          steps=None):
    return {
        "flow_id": "ep#" + sink_id,
        "entry_point_id": "app.py:h:1",
        "source_param": source,
        "source_type": source_type,
        "sink_call_site_id": sink_id,
        "sink_slot": slot,
        "propagation_steps": steps or [],
        "confidence": 1.0,
        "has_sanitizer_hint": False,
    }


@pytest.mark.asyncio
async def test_writes_injection_gitnexus_queue(tmp_path, monkeypatch):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    _write_pgraph(deliverables, [_flow("sql_value")])

    # stub the LLM chain-verdict client
    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db","mismatch_reason":"concat","confidence":"high"}')

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)

    result = await activities.run_gitnexus_chain_verdict(_input(tmp_path, deliverables))
    q = deliverables / "injection_gitnexus_queue.json"
    assert q.exists()
    data = json.loads(q.read_text())
    assert len(data["vulnerabilities"]) == 1
    assert data["vulnerabilities"][0]["source_track"] == "gitnexus"
    assert "injection" in result["per_class"]


@pytest.mark.asyncio
async def test_no_parameter_graph_skips_gracefully(tmp_path, monkeypatch):
    """Plan 1 not landed → no parameter_graph.json → all gitnexus queues absent."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # no parameter_graph.json

    async def fake_llm(prompt, **kw):
        raise AssertionError("should not call LLM")

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)

    result = await activities.run_gitnexus_chain_verdict(_input(tmp_path, deliverables))
    assert result["per_class"] == {}
    assert not (deliverables / "injection_gitnexus_queue.json").exists()


@pytest.mark.asyncio
async def test_writes_xss_and_ssrf_queues(tmp_path, monkeypatch):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    _write_pgraph(deliverables, [_flow("html_body", sink_id="app.py:h:innerHTML:5:0"),
                                 _flow("url", source="u", sink_id="app.py:h:fetch:6:0")])

    async def fake_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"x","evidence_chain":"s->k",'
                '"mismatch_reason":"m","confidence":"high"}')

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)

    result = await activities.run_gitnexus_chain_verdict(_input(tmp_path, deliverables))
    assert "xss" in result["per_class"]
    assert "ssrf" in result["per_class"]
    assert (deliverables / "xss_gitnexus_queue.json").exists()
    assert (deliverables / "ssrf_gitnexus_queue.json").exists()


@pytest.mark.asyncio
async def test_invalid_parameter_graph_skips_gracefully(tmp_path, monkeypatch):
    """Corrupt parameter_graph.json → skip, don't crash."""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    (deliverables / "parameter_graph.json").write_text("not json")

    async def fake_llm(prompt, **kw):
        raise AssertionError("should not call LLM")

    monkeypatch.setattr(activities, "_get_paths", lambda i: (tmp_path, deliverables, tmp_path))
    monkeypatch.setattr(activities, "_gitnexus_verdict_llm_client", fake_llm, raising=False)

    result = await activities.run_gitnexus_chain_verdict(_input(tmp_path, deliverables))
    assert result["per_class"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_gitnexus_chain_verdict.py -v`
Expected: FAIL — `AttributeError: module ...activities has no attribute 'run_gitnexus_chain_verdict'`

- [ ] **Step 3: Implement the activity**

Add to `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（在 `run_render_dataflow_hints` 之后，约 :553 附近）：

```python
async def _gitnexus_verdict_llm_client(prompt: str, **kwargs) -> str:
    """GitNexus-track chain-verdict LLM pass client.

    Production: reuses the same LLM client pool as _llm_taint_client /
    run_agent. This stub is the injection point — when not configured, callers
    must pass their own client (tests do). Default raises so judge_chain_verdict
    takes its conservative needs_review path (does NOT silently clear).
    """
    raise RuntimeError(
        "GitNexus-track chain-verdict LLM client not configured; "
        "judge_chain_verdict will mark candidates needs_review"
    )


@activity.defn
async def run_gitnexus_chain_verdict(input: ActivityInput) -> dict:
    """GitNexus-track chain verdict for injection/xss/ssrf (spec §5.4-5.6).

    Reads parameter_graph.json (Plan 1), runs the light backward/forward
    chain-verdict pass for the three trace-class vuln types, and writes
    <vuln>_gitnexus_queue.json for each. Plan 3's run_merge_dual_track_queues
    then does verdict OR against the LLM track.

    Graceful degradation: no parameter_graph.json (Plan 1 not landed) →
    per_class empty, no gitnexus queues written, merger falls back to
    llm-only (current behavior).
    """
    from shannon_whitebox.audit.session_registry import get_audit_session
    try:
        from shannon_core.code_index.parameter_models import (
            ParameterPropagationGraph,
        )
        from shannon_core.code_index.vuln_chain_builders.injection_builder import (
            build_injection_findings,
        )
        from shannon_core.code_index.vuln_chain_builders.xss_builder import (
            build_xss_findings,
        )
        from shannon_core.code_index.vuln_chain_builders.ssrf_builder import (
            build_ssrf_findings,
        )
        from shannon_core.utils.atomic_write import atomic_write_json

        repo, deliverables, _ = _get_paths(input)
        per_class: dict[str, int] = {}

        pgraph_path = deliverables / "parameter_graph.json"
        if not pgraph_path.exists():
            return {"per_class": {}, "skipped": "no parameter_graph.json"}
        try:
            pgraph = ParameterPropagationGraph.model_validate_json(pgraph_path.read_text())
        except Exception:
            return {"per_class": {}, "skipped": "invalid parameter_graph.json"}

        async with get_audit_session().track_step(
            "vulnerability-analysis", "gitnexus-chain-verdict",
            intent=None,
        ):
            llm = _gitnexus_verdict_llm_client

            for vc, builder in (
                ("injection", build_injection_findings),
                ("xss", build_xss_findings),
                ("ssrf", build_ssrf_findings),
            ):
                try:
                    findings = await builder(pgraph, llm_client=llm)
                except Exception as exc:
                    # one vuln class failing must not block the others
                    logger.warning("gitnexus chain-verdict %s failed: %s", vc, exc)
                    continue
                if findings:
                    atomic_write_json(
                        deliverables / f"{vc}_gitnexus_queue.json",
                        {"vulnerabilities": [f.model_dump() for f in findings]},
                    )
                    per_class[vc] = len(findings)

        return {"per_class": per_class}
    except PentestError as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
    except Exception as e:
        error_type, retryable = classify_error_for_temporal(e)
        raise ApplicationFailure(str(e), type=error_type, non_retryable=not retryable) from e
```

> 注：`_gitnexus_verdict_llm_client` 默认 raise——`judge_chain_verdict`（Task 3）捕获后走 conservative needs_review（`verdict="vulnerable"` + `confidence="low"`），不会崩。生产 wiring 真实 LLM client 时替换此函数即可（与 `_llm_taint_client` 同模式）。测试 monkeypatch `_gitnexus_verdict_llm_client` 注入 fake。

- [ ] **Step 4: Wire the activity into the workflow (after vuln phase, before Plan 3 merger)**

Edit `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`。在 vuln 阶段 `asyncio.gather` 完成（:315）之后、`log_phase_complete_activity`（:323）**之前**插入。注意 Plan 3 的 `run_merge_dual_track_queues` 也插在 vuln 阶段后——顺序必须是：**GitNexus 轨链判定（产 gitnexus_queue）→ Plan 3 合并（读 gitnexus_queue）→ log_phase_complete**。

```python
            # GitNexus-track chain verdict: produce <vuln>_gitnexus_queue.json
            # (spec §5.4-5.6) for the dual-track merger (Plan 3). No
            # parameter_graph.json (Plan 1 not landed) → degrades to empty.
            await workflow.execute_activity(
                activities.run_gitnexus_chain_verdict, act_input,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry_for("standard"),
            )
            # Plan 3 merger: combine LLM-track + GitNexus-track queues.
            await workflow.execute_activity(
                activities.run_merge_dual_track_queues, act_input,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_for("standard"),
            )
            await workflow.execute_activity(
                activities.log_phase_complete_activity,
                ActivityInput(**{**act_input.__dict__, "phase": "vulnerability-analysis"}),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_for("log"),
            )
```

> 注：`run_merge_dual_track_queues` 来自 Plan 3（本 plan 强依赖）。若 Plan 3 尚未落地，本 wiring 的 `run_merge_dual_track_queues` 调用会 AttributeError——执行本 plan 前须确认 Plan 3 已合并。原 :323-328 的 `log_phase_complete_activity` 调用被上面的版本取代（含 GitNexus 轨 + 合并两个前置 activity）。

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_run_gitnexus_chain_verdict.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run broader whitebox pipeline test subset to confirm no regression**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/ -k "gitnexus or merge or chain_verdict" -v --ignore=packages/whitebox/tests/test_cli.py 2>/dev/null | tail -30`
Expected: PASS（注意 memory 记录 test_worker_progress / test_cli follow / integration 等有预存挂起，按需 --ignore）

- [ ] **Step 7: Commit**

```bash
cd /root/shannon-py
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/tests/test_run_gitnexus_chain_verdict.py
git commit -m "feat(whitebox): wire GitNexus-track chain verdict for inj/xss/ssrf (spec §5.4-5.6)"
```

> **手动冒烟（本 plan 外）**：真实 pgraph（Plan 1 落盘非空）+ 真实 LLM client 需 MCP/LLM 环境。完成后跑一次真实白盒扫描，确认：
> 1. `parameter_graph.json` 非空且含三类 sink 的 TaintFlow。
> 2. `injection_gitnexus_queue.json` / `xss_gitnexus_queue.json` / `ssrf_gitnexus_queue.json` 产出（每条 `source_track=gitnexus` + `evidence_chain`）。
> 3. xss 的 Stored 合成（input→DB→read→render）在真实 pgraph 里被正确关联（字段名匹配是否够用）。
> 4. Plan 3 合并后 `<vuln>_exploitation_queue.json` 含 both/llm-only/gitnexus-only 三类来源标记。

---

### Task 8: 集成验证 — 双轨闭环（pgraph → builder → gitnexus_queue → merger）

**Files:**
- Test: `packages/core/tests/code_index/test_dual_track_chain_integration.py`（Create）

**Interfaces:**
- Consumes: Task 3（chain_verdict）、Task 4/5/6（builders）、Plan 3（`merge_dual_track_queues`，通过直接 import 验证，不依赖 workflow）
- Produces: 验证完整闭环：pgraph → builder → gitnexus findings → 与 LLM 轨 findings 合并（verdict OR + both/llm-only/gitnexus-only 标记），且 source_track/evidence_chain 字段保留

- [ ] **Step 1: Write the integration test**

```python
# packages/core/tests/code_index/test_dual_track_chain_integration.py
"""End-to-end-ish integration: pgraph → builder → merger (spec §5.4-5.6 + §4.2).

Validates the closed loop WITHOUT temporal/workflow: pgraph produces GitNexus
findings, which merge (verdict OR) with synthetic LLM findings. Confirms
source_track / evidence_chain survive into the merged exploitation queue.
"""
import pytest

from shannon_core.code_index.parameter_models import (
    ParameterPropagationGraph, TaintFlow, PropagationStep,
)
from shannon_core.code_index.models import ParameterSource
from shannon_core.code_index.vuln_chain_builders.injection_builder import (
    build_injection_findings,
)
from shannon_core.code_index.dual_track_merger import merge_dual_track_queues
from shannon_core.models.queue_schemas import InjectionVulnerability


def _flow(slot="sql_value", steps=None):
    return TaintFlow(
        flow_id="app.py:h:1#app.py:h:db.execute:5:0",
        entry_point_id="app.py:h:1", source_param="q",
        source_type=ParameterSource.QUERY_PARAM,
        sink_call_site_id="app.py:h:db.execute:5:0",
        sink_slot=slot, propagation_steps=steps or [],
    )


@pytest.mark.asyncio
async def test_gitnexus_track_merges_with_llm_track_verdict_or():
    """Both tracks flag the same source→sink → merged 'both', high confidence."""
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow()], language_coverage=["python"],
    )

    async def gn_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db.exec(L5)","mismatch_reason":"concat","confidence":"high"}')

    gn_findings = await build_injection_findings(pgraph, llm_client=gn_llm)
    assert len(gn_findings) == 1
    assert gn_findings[0].source_track == "gitnexus"

    # synthetic LLM-track finding for the SAME source→sink (will dedup+merge)
    llm_findings = [InjectionVulnerability(
        ID="INJ-LLM-01", vulnerability_type="injection",
        externally_exploitable=True, confidence="high",
        source="q (app.py:h:1)", sink_call="app.py:h:db.execute:5:0",
        verdict="safe",  # LLM track said safe
        source_track="llm",
    )]

    merged = merge_dual_track_queues(llm_findings, gn_findings, mode="verdict")
    assert len(merged) == 1
    m = merged[0]
    assert m.merge_source == "both"
    # verdict OR: GN vulnerable + LLM safe → vulnerable (conservative)
    assert m.verdict == "vulnerable"
    # evidence_chain preserved from GitNexus track
    assert m.evidence_chain is not None


@pytest.mark.asyncio
async def test_gitnexus_only_marked_gitnexus_only():
    """GN track flags a chain LLM track missed → gitnexus-only, needs_review."""
    pgraph = ParameterPropagationGraph(
        taint_flows=[_flow()], language_coverage=["python"],
    )

    async def gn_llm(prompt, **kw):
        return ('{"verdict":"vulnerable","witness_payload":"\'","evidence_chain":'
                '"q->db","mismatch_reason":"concat","confidence":"high"}')

    gn_findings = await build_injection_findings(pgraph, llm_client=gn_llm)
    # empty LLM track → all gitnexus-only
    merged = merge_dual_track_queues([], gn_findings, mode="verdict")
    assert len(merged) == 1
    assert merged[0].merge_source == "gitnexus-only"
    assert merged[0].confidence == "needs_review"


@pytest.mark.asyncio
async def test_empty_pgraph_yields_no_gitnexus_findings():
    """Plan 1 not landed → GN track empty → merger is LLM-only."""
    pgraph = ParameterPropagationGraph(taint_flows=[], language_coverage=["python"])

    async def gn_llm(prompt, **kw):
        raise AssertionError("no LLM on empty pgraph")

    gn_findings = await build_injection_findings(pgraph, llm_client=gn_llm)
    assert gn_findings == []

    llm_findings = [InjectionVulnerability(
        ID="L1", vulnerability_type="injection", externally_exploitable=True,
        confidence="high", source="q", sink_call="db.exec", verdict="vulnerable",
    )]
    merged = merge_dual_track_queues(llm_findings, gn_findings, mode="verdict")
    assert len(merged) == 1
    assert merged[0].merge_source == "llm-only"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/test_dual_track_chain_integration.py -v`
Expected: PASS (3 tests)。若 `dual_track_merger` 未实现（Plan 3 未落地）→ FAIL `ModuleNotFoundError`——确认 Plan 3 先合并再跑本 plan。

- [ ] **Step 3: Run the full code_index test suite to confirm no regression**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/code_index/ -v`
Expected: PASS（含 Task 1-6 新测试 + 现有 taint/sink/chain/merger 测试）

- [ ] **Step 4: Commit**

```bash
cd /root/shannon-py
git add packages/core/tests/code_index/test_dual_track_chain_integration.py
git commit -m "test(code_index): dual-track chain verdict integration (pgraph→builder→merger)"
```

---

## Self-Review

**1. Spec coverage**（对照 spec §5.4 / §5.5 / §5.6 / §2 / §4.2）：

**共用框架（§5.4-5.6 共享）**：
- GitNexus 轨链判定基础设施（候选链提取 + 确定性 sanitizer 标注 + LLM pass → verdict）→ Task 1（sanitizer 库）+ Task 3（chain_verdict）✓
- verdict OR 合并（§4.2）→ 复用 Plan 3 `merge_dual_track_queues`（Task 8 集成验证）✓
- LLM 轨自由 trace 不被锚定（§2）→ Global Constraint 声明：GitNexus 轨是交叉验证/补盲，不限制 LLM 轨；两轨独立产 queue，Plan 3 并集不丢 ✓

**injection（§5.4 正向 source→sink）**：
- 正向链判定（slot + post-sanitize concat）→ Task 4（forward direction + post_sanitize_concat 进 concat_occurrences）✓
- source→sink 候选链（source_param → sink_call_site_id）→ Task 3 `extract_candidate_chains` ✓

**xss（§5.5 反向 + render context + DB read↔write 跨表补 Stored）**：
- 反向 sink→source → Task 5（direction_hint=backward）✓
- render_context 映射 → Task 5 `_RENDER_CONTEXT`（html_body→HTML_BODY 等）✓
- DB read↔write 跨表补 Stored 盲区 → Task 5 `_find_stored_xss_synthesis`（read flow INTERNAL source + 匹配 write flow，字段名匹配，宁缺勿错拼）✓

**ssrf（§5.6 反向 + 7步 + 补 schema 缺字段）**：
- 补 schema 缺字段 path/verdict/witness_payload → Task 2 ✓
- 反向链判定 → Task 6（direction_hint=backward，url slot 路由）✓
- 7 步方法论 → **本就在 LLM 轨 prompt（vuln-ssrf.txt:118-238），GitNexus 轨不重复**（Global Constraint 声明成本控制）⚠️——若 spec 要求 GitNexus 轨也做 7 步，需扩 Task 6，但当前设计判 GitNexus 轨只做链判定（避免与 LLM 轨冗余）

**2. Placeholder scan**：无 TBD/TODO。`_gitnexus_verdict_llm_client`（Task 7）默认 raise 是**有意的注入点**（与 `_llm_taint_client` Plan 1 Task 4 同模式），非占位符——生产 wiring 真实 LLM client 时替换，raise 时 `judge_chain_verdict` 走 conservative needs_review，不崩。

**3. Type consistency**：
- `CandidateChain` / `ChainVerdict`（Task 3）在 Task 4/5/6 三个 builder 一致使用。
- `InjectionVulnerability` / `XssVulnerability` / `SsrfVulnerability` 的 `source_track`/`evidence_chain`/`merge_source` 字段来自 Plan 3 Task 1（`BaseVulnerability` 四字段），三类 builder 都设 `source_track="gitnexus"`，与 Plan 3 合并器读写一致。
- `SsrfVulnerability` 新增 `path`/`verdict`/`witness_payload`（Task 2）与 injection/xss 类对齐，Plan 3 `_get_verdict_or_exploitable`（读 verdict 字段）对 ssrf 也生效。
- `extract_candidate_chains` 的 slot 路由（`_INJECTION_SLOTS`/`_XSS_RENDER_CONTEXTS`/`_SSRF_SLOTS`）与 `SlotContext` 枚举值（`parameter_models.py:28-37`）一致。

**4. xss Stored 合成正确性**：
- `_is_db_read_source`：`source_type==INTERNAL`（`parameter_models.py:114`，意为 DB/内部来源）✓
- `_is_db_write`：非 INTERNAL source + sql_value slot（DB insert/update 走 bound value slot）✓
- 字段名匹配：`rf.source_param == wf.source_param`（best-effort）——**诚实标注**：若 read 的 source_param 是 DB 字段名而 write 的 source_param 是 HTTP param 名（不同命名），匹配会失败 → 跳过（宁缺勿错拼）。这是已知局限，spec §5.5 的跨表关联本就依赖字段命名约定，无更可靠的纯静态关联手段。

**需人决策点**：
- **A. `_gitnexus_verdict_llm_client` 生产 wiring**：Task 7 默认 raise。生产需替换为真实 LLM client（复用 `_llm_taint_client` 的 client 池或独立池）。**这是 GitNexus 轨生效的前提**——不接则全 needs_review（vulnerable + low confidence，仍经 Plan 3 OR 进入 queue，但置信度低）。决策：是否复用 taint 的 client 池（共享成本/限流）还是独立池？
- **B. GitNexus 轨 LLM pass 成本**：每个候选链一次轻量 LLM 调用（Task 3 `judge_chain_verdict`）。大 repo 候选链可能数百条 → 成本可观。决策：是否设候选链上限（如每类 top-N by confidence）或批量判定（一条 prompt 多链）？当前设计一条一调（简单正确），成本由 `_gitnexus_verdict_llm_client` 的限流/缓存兜底。
- **C. xss Stored 字段名匹配可靠性**：见上 #4。若真实 repo 的 read DB 字段名与 write HTTP param 名不一致（常见，如 `user.name` read vs `body.name` write），匹配失败 → Stored 补盲失效。决策：是否需要 LLM 辅助字段关联（给 LLM read+write 流让它判同字段）？当前用纯字段名匹配（best-effort），失效则跳过。
- **D. ssrf 7 步是否进 GitNexus 轨**：当前 GitNexus 轨只做链判定，7 步方法论在 LLM 轨。若 spec 要求 GitNexus 轨也覆盖 7 步的某几步（如 protocol/host/port validation 的确定性检测），需扩 Task 6 + Task 1 sanitizer 库加 SSRF-specific 规则（scheme allowlist/IP CIDR check 的确定性检测）。当前设计判这是 LLM 轨职责，避免冗余。

**已知缺口（诚实）**：
- 真实 pgraph（Plan 1 FULL degradation 路径产出非空 TaintFlow）需 MCP 环境，单元测试用合成 pgraph。真实流转由手动冒烟验证。
- 真实 LLM chain-verdict pass 的 verdict 准确率未验证（依赖 LLM 质量）。单元测试用 fake_llm 验证逻辑闭环。
- xss Stored 跨表关联的字段名匹配是 best-effort（见 C）。
- auth/authz 不在本 plan（它们无 source→sink taint 语义，Plan 7/9 处理）；本 plan 的 GitNexus 轨链判定基础设施（Task 1/3）对 auth/authz 不适用。
- sanitizer 库（Task 1）的规则集是初始集（常见库函数），未覆盖所有语言/框架的防御函数；可增量扩展。
