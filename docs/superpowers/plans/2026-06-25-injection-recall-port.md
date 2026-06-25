# Injection Recall Port 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把上游 TS injection-recall spec 移植到 shannon-py 双轨架构，让 SQLi/CMDi 能被真正扫出来——解封 LLM 轨（双引擎流程一致）、扩 GitNexus 轨 sink 规则、修跨服务注入入队语义。

**Architecture:** 严格按双轨消费模型（见 memory `dual-track-consumption-model`）：LLM 轨（`vuln-injection.txt` agent）自给自足（不吃确定性 hints），GitNexus 轨（`injection_builder`+`chain_verdict`）吃确定性产物，两轨 verdict OR 合并。改动按轨划分：1.2/3′ 落 code_index 与合并器（GitNexus 轨/合并点），4a 落 openai 引擎（双引擎流程一致），1.1/2/3/4b 落 prompts（LLM 轨）。

**Tech Stack:** Python 3.12, pydantic v2, pytest + pytest-asyncio, openai-agents SDK（`agents` 包）, tree-sitter, claude_agent_sdk。

## Global Constraints

- **分支：** `feat/fork-py`（当前分支，不开新分支）。
- **TDD：** 每个改动先写失败测试，再实现，再绿。frequent commits。
- **commit 前缀：** `feat(code_index):` / `feat(models):` / `feat(whitebox):` / `feat(agents):` / `docs(prompt):`。
- **双引擎约束：** glm-openai 与 glm-anthropic 都要支持、流程一致——改动 4a 给 openai 引擎补 Task/Agent tool，**prompt 不改**（两引擎共用 TS 原样 Task-delegation prompt）。
- **LLM 轨自给自足：** LLM 轨 prompt 不引确定性 hints（改动 4b 移除 include）。
- **prompts 不走 lint**；prompt 改动用文件内容断言测试。
- **预存测试陷阱：** 全套 pytest 有预存挂起/失败（memory `feat-fork-py-test-gotchas`）——只跑改动相关的测试文件，不广跑。
- **前置已验证：** `scripts/validate_glm_task_probe.py` 实测 GLM 在 glm-anthropic 能驱动 `Agent` 子代理委派（2/2），approach ① 模型侧成立。

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `packages/core/src/shannon_core/code_index/sink_detector.py` | AST sink 规则库 + 检测 | Task 1（加 ORM Raw 规则）、Task 2（arg-shape 标识符） |
| `packages/core/tests/code_index/test_sink_detector.py` | sink 规则单测 | Task 1/2 扩 |
| `packages/core/src/shannon_core/code_index/sanitizer_library.py` | 确定性防御函数库 | Task 3（shell=False AST 级） |
| `packages/core/tests/code_index/test_sanitizer_library.py` | sanitizer 单测 | Task 3 扩 |
| `packages/core/src/shannon_core/code_index/dual_track_merger.py` | 双轨合并（verdict OR） | Task 4（删 externally_exploitable 覆写） |
| `packages/core/tests/code_index/test_dual_track_merger.py` | 合并器单测 | Task 4 扩 |
| `packages/core/src/shannon_core/agents/tools_openai/__init__.py` | openai 工具集 + ToolContext | Task 5（ToolContext 加 subagent_run、build_tools 加 task） |
| `packages/core/src/shannon_core/agents/tools_openai/task.py` | **新建** Task/Agent 子代理委派工具 | Task 5 |
| `packages/core/src/shannon_core/agents/providers_openai.py` | openai 引擎 | Task 5（call 注入 subagent_run） |
| `packages/core/tests/agents/tools_openai/test_task.py` | task 工具单测 | Task 5 新建 |
| `packages/core/tests/agents/tools_openai/test_registry.py` | build_tools 注册测试 | Task 5（8→9） |
| `prompts/vuln-injection.txt` | LLM 轨注入 prompt | Task 6（sink 清单+契约）、Task 7（跨服务+移除 hints include） |
| `prompts/injection-exploit.txt` | exploit prompt | Task 8（externally_exploitable=false 分诊） |
| `packages/core/tests/prompts/test_vuln_injection_prompt.py` | prompt 内容断言 | Task 6/7 新建 |
| `packages/core/tests/prompts/test_injection_exploit_prompt.py` | prompt 内容断言 | Task 8 新建 |

---

### Task 1: sink_detector ORM Raw 规则 + 白名单守卫（改动 1.2 A + D）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/sink_detector.py:67-198`（`DEFAULT_RULES`，SQL 段后追加 9 条）
- Test: `packages/core/tests/code_index/test_sink_detector.py`

**Interfaces:**
- Consumes: `SinkRule` / `SlotContext.SQL_VALUE` / 现有 `_DB_CURSOR` 等 receiver regex（`sink_detector.py:36-62`）；`detect_sinks`（`:249`）。
- Produces: 9 条新 `SinkRule`（`py-django-raw` 等），category=`SinkCategory.SQL`，经 `detect_sinks` 产 `SinkCallSite`。

- [ ] **Step 1: 写失败测试（追加到 test_sink_detector.py 末尾）**

```python
# packages/core/tests/code_index/test_sink_detector.py — 追加
from shannon_core.code_index.sink_detector import DEFAULT_RULES, detect_sinks
from shannon_core.code_index.parameter_models import SinkCategory

def _rule(rule_id: str):
    return next(r for r in DEFAULT_RULES if r.rule_id == rule_id)


def test_orm_raw_rules_present():
    ids = {r.rule_id for r in DEFAULT_RULES}
    for rid in ("py-django-raw", "py-sqlalchemy-text", "ts-knex-raw",
                "ts-sequelize-query", "go-gorm-raw", "go-gorm-exec",
                "java-jpa-createnativequery", "php-laravel-whereraw", "php-db-raw"):
        assert rid in ids, f"missing ORM Raw rule {rid}"


def test_go_gorm_raw_detects_string_built_query():
    # 内联 Go 源码片段 + 真 detect_sinks，断言命中 go-gorm-raw
    src = (
        "package main\n"
        "func h(name string) {\n"
        '    db.Raw("SELECT * FROM u WHERE n = \'" + name + "\'")\n'
        "}\n"
    )
    # 用现有测试已有的 Go parser/block 构造方式（见 test_sink_detector.py 顶部 helper）
    sites = detect_sinks(_go_blocks(src), _go_parser(), source_provider=_src_provider(src))
    raw = [s for s in sites if s.rule_id == "go-gorm-raw"]
    assert raw, "go-gorm-raw should fire on db.Raw(...) with concatenation"
    assert raw[0].category == SinkCategory.SQL
```

> 注：`_go_blocks` / `_go_parser` / `_src_provider` 是 test_sink_detector.py 已有的 helper（照搬现有 Go 用例的构造方式；若名字不同，用文件里现有的 Go sink 测试同款 helper）。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_sink_detector.py::test_orm_raw_rules_present -v`
Expected: FAIL — `next()` 抛 StopIteration（规则不存在）。

- [ ] **Step 3: 实现 9 条 ORM Raw 规则**

在 `sink_detector.py` 的 `DEFAULT_RULES` 里，紧跟现有 SQL 段（`php-db-select-static` 之后，`:88` 附近）追加：

```python
    # --- ORM Raw / string-built SQL (spec 改动 1.2 A) ---
    SinkRule("py-django-raw", ("python",), "raw", re.compile(r"^(objects)$"),
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),)),
    SinkRule("py-sqlalchemy-text", ("python",), "text", None,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),),
             needs_review_default=True),  # bare callee; receiver 未知
    SinkRule("ts-knex-raw", ("typescript",), "raw", re.compile(r"^(knex)$"),
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),)),
    SinkRule("ts-sequelize-query", ("typescript",), "query", re.compile(r"^(sequelize)$"),
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),)),
    SinkRule("go-gorm-raw", ("go",), "Raw", re.compile(r"^(db|gorm|DB)$"),
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),)),
    SinkRule("go-gorm-exec", ("go",), "Exec", re.compile(r"^(db|gorm|DB)$"),
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),)),
    SinkRule("java-jpa-createnativequery", ("java",), "createNativeQuery", None,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),),
             needs_review_default=True),
    SinkRule("php-laravel-whereraw", ("php",), "whereRaw", None,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),),
             needs_review_default=True),
    SinkRule("php-db-raw", ("php",), "raw", re.compile(r"^(DB)$"),
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),)),
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_sink_detector.py -v -k "orm_raw or go_gorm_raw"`
Expected: PASS。

- [ ] **Step 5: 白名单守卫（改动 1.2 D）**

追加守卫测试（确保 SQL/COMMAND subtype 不被 `VALID_INJECTION_CATEGORIES` 误拒）：

```python
# packages/core/tests/code_index/test_sink_detector.py — 追加
from shannon_core.code_index.finding_models import VALID_INJECTION_CATEGORIES

def test_sql_command_categories_in_whitelist():
    # 新 ORM Raw / 命令规则产出 SQL/COMMAND 类 finding，其 issue_type 须在白名单
    assert "sql_injection" in VALID_INJECTION_CATEGORIES
    assert "command_injection" in VALID_INJECTION_CATEGORIES
```

- [ ] **Step 6: 运行守卫测试**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_sink_detector.py::test_sql_command_categories_in_whitelist -v`
Expected: PASS（若 FAIL，说明白名单漂移——报给负责人，不在本 task 改）。

- [ ] **Step 7: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/sink_detector.py packages/core/tests/code_index/test_sink_detector.py
git commit -m "feat(code_index): ORM Raw sink rules + SQL/COMMAND whitelist guard (spec 改动 1.2 A/D)"
```

---

### Task 2: 动态标识符 arg-shape 检测（改动 1.2 B）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/sink_detector.py:339-362`（`_build_dangerous_slots`）+ 新增 `_looks_string_built`
- Test: `packages/core/tests/code_index/test_sink_detector.py`

**Interfaces:**
- Consumes: `SlotContext.SQL_IDENTIFIER`（`parameter_models.py:31`，已存在）；`SinkCategory.SQL`；`SinkRule.category`。
- Produces: SQL 类 sink 当 arg 为 string-built 时，危险槽改标 `SQL_IDENTIFIER`、site `needs_review=True`。

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/code_index/test_sink_detector.py — 追加
def test_sql_fstring_arg_marked_identifier():
    # db.Exec(f"CREATE TABLE {tn}") → SQL_IDENTIFIER + needs_review
    src = (
        "package main\n"
        "func m(tn string) {\n"
        '    db.Exec(f("CREATE TABLE %s", tn))\n'   # Go 无 f-string；用 Python 测更直接
        "}\n"
    )
    # 改用 Python fixture 测 arg-shape（Go f-string 不存在）
    # 见下方 Python 用例
```

实际用 Python `cursor.execute` 测（f-string 是 Python 概念）：

```python
def test_py_sql_fstring_arg_marked_identifier():
    src = (
        "import sqlite3\n"
        "def f(tn):\n"
        '    cur = sqlite3.connect(".").cursor()\n'
        '    cur.execute(f"SELECT * FROM {tn}")\n'
    )
    sites = detect_sinks(_py_blocks(src), _py_parser(), source_provider=_src_provider(src))
    ex = [s for s in sites if s.rule_id == "py-db-cursor-execute"]
    assert ex, "cursor.execute should still fire"
    ident_slots = [d for d in ex[0].dangerous_slots if d.slot == SlotContext.SQL_IDENTIFIER]
    assert ident_slots, "f-string arg into SQL sink should be marked SQL_IDENTIFIER"
    assert ex[0].needs_review is True


def test_py_sql_bound_arg_stays_value():
    src = (
        "import sqlite3\n"
        "def f(name):\n"
        '    cur = sqlite3.connect(".").cursor()\n'
        '    cur.execute("SELECT * FROM u WHERE n = ?", (name,))\n'
    )
    sites = detect_sinks(_py_blocks(src), _py_parser(), source_provider=_src_provider(src))
    ex = [s for s in sites if s.rule_id == "py-db-cursor-execute"][0]
    val_slots = [d for d in ex.dangerous_slots if d.slot == SlotContext.SQL_VALUE]
    ident_slots = [d for d in ex.dangerous_slots if d.slot == SlotContext.SQL_IDENTIFIER]
    assert val_slots and not ident_slots, "bound ?-placeholder arg stays SQL_VALUE"
```

> `SlotContext` 从 `parameter_models` import；`_py_blocks`/`_py_parser`/`_src_provider` 用 test_sink_detector.py 现有 Python helper。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_sink_detector.py -v -k "fstring_arg_marked_identifier or bound_arg_stays_value"`
Expected: FAIL（f-string 用例：无 SQL_IDENTIFIER 槽；因尚未实现 arg-shape）。

- [ ] **Step 3: 实现 `_looks_string_built` + 改 `_build_dangerous_slots`**

在 `sink_detector.py` 加辅助（放在 `_build_dangerous_slots` 之前）：

```python
# arg 表达式是否形如 string-built（f-string / format / printf / 拼接）
# spec 改动 1.2 B：SQL 类 sink 的 string-built arg 改标 SQL_IDENTIFIER
_STRING_BUILT_RE = re.compile(
    r"(^[fFrR]['\"])"          # f-string 前缀
    r"|fmt\.Sprintf"            # Go fmt.Sprintf
    r"|\.format\("              # Python str.format
    r"|%\s*[sdbf]"              # printf 风格 %s/%d
    r"|['\"]\s*\+"              # 字符串字面量 + 拼接
)


def _looks_string_built(expr: str) -> bool:
    """best-effort：arg 表达式是否是动态字符串构建（暗示标识符/DDL 注入）。"""
    return bool(_STRING_BUILT_RE.search(expr or ""))
```

改 `_build_dangerous_slots`，对 SQL 类规则做后处理（返回前）。当前签名 `_build_dangerous_slots(rule, arg_expressions, block)`。在函数内，构造 `slots` 后追加 SQL 标识符改写：

```python
def _build_dangerous_slots(rule, arg_expressions, block):
    slots = []
    for idx, slot_ctx in rule.dangerous_slots:
        # ... 现有逻辑不变（idx==-1 variadic / 普通分支），收集 DangerousSlot ...

    # spec 改动 1.2 B：SQL 类 sink 的 string-built arg → SQL_IDENTIFIER
    if rule.category == SinkCategory.SQL:
        slots = [
            (type(d)(arg_index=d.arg_index, slot=SlotContext.SQL_IDENTIFIER,
                     expression=d.expression, is_entry_hint=d.is_entry_hint))
            if _looks_string_built(d.expression) else d
            for d in slots
        ]
    return slots
```

> `type(d)(...)` 重建 DangerousSlot 改 slot；若 DangerousSlot 是 frozen dataclass，用 `dataclasses.replace(d, slot=SlotContext.SQL_IDENTIFIER)`（更稳）。实现时优先用 `dataclasses.replace`。

同时 `needs_review` 覆写：在 `detect_sinks` 构造 `SinkCallSite` 处（`:306`），SQL 类且任一槽被改标时设 `needs_review=True`。在 `_build_dangerous_slots` 返回 slots 后，detect_sinks 无法直接知道是否改写——改为：让 `_build_dangerous_slots` 同时返回一个 `force_review` 标志，或 detect_sinks 内重算 `any(_looks_string_built(d.expression) and rule.category==SQL for d in dangerous)`。实现用后者（在 detect_sinks 构造 site 前算一次）：

```python
# detect_sinks 内，构造 site 前（:305 附近）：
force_review = rule.category == SinkCategory.SQL and any(
    _looks_string_built(d.expression) for d in dangerous
)
site = SinkCallSite(
    ...
    needs_review=rule.needs_review_default or force_review,
)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_sink_detector.py -v -k "fstring_arg_marked_identifier or bound_arg_stays_value or orm_raw or go_gorm"`
Expected: PASS。

- [ ] **Step 5: 回归现有 sink 测试**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_sink_detector.py -v`
Expected: PASS（arg-shape 只对 SQL 类、只在 string-built 时改标，不影响 COMMAND/SSRF 等）。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/sink_detector.py packages/core/tests/code_index/test_sink_detector.py
git commit -m "feat(code_index): dynamic-identifier arg-shape detection for SQL sinks (spec 改动 1.2 B)"
```

---

### Task 3: 命令侧 shell=False 假阳性降低（改动 1.2 C）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/sanitizer_library.py`（`DEFAULT_SANITIZER_RULES` + `match` 支持检测 shell=False 数组调用）
- Test: `packages/core/tests/code_index/test_sanitizer_library.py`

**Interfaces:**
- Consumes: `SanitizerLibrary.match(language, callee, receiver_text, arg_expr)`（`sanitizer_library.py:277`）。
- Produces: `subprocess_array` defense 标注，当 subprocess 类调用 arg 文本含 `shell=False`。

> 背景（spec 已述）：subprocess sink 已触发；`shell=True` 因不匹配 defense → 判 vulnerable（已正确）。本 task 降假阳性——`shell=False` + 数组参数时标 `subprocess_array` defense。现有 `:161` 只在 transformation 文本检测 `shell=False`；本 task 扩到 call-site arg 文本级（best-effort）。

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/code_index/test_sanitizer_library.py — 追加
from shannon_core.code_index.sanitizer_library import SanitizerLibrary

def test_subprocess_shell_false_list_args_annotated_as_array():
    lib = SanitizerLibrary()
    hit = lib.match(
        language="python", callee="run", receiver_text="subprocess",
        arg_expr='["ls", "-la"], shell=False',
    )
    assert hit is not None
    assert hit.defense_type == "subprocess_array"
    assert hit.applies_to == "cmd_argument"


def test_subprocess_shell_true_no_array_defense():
    lib = SanitizerLibrary()
    hit = lib.match(
        language="python", callee="run", receiver_text="subprocess",
        arg_expr='"ls " + x, shell=True',
    )
    # shell=True 不标 array defense（保持判 vulnerable）
    assert hit is None or hit.defense_type != "subprocess_array"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_sanitizer_library.py -v -k "shell_false_list or shell_true_no_array"`
Expected: FAIL（`match` 不识别 shell=False kwarg → 返回 None）。

- [ ] **Step 3: 实现 subprocess_array 的 arg 级检测**

在 `sanitizer_library.py` 的 `DEFAULT_SANITIZER_RULES` 追加（subprocess 段）：

```python
    # spec 改动 1.2 C：subprocess 数组参数 + shell=False → subprocess_array defense
    SanitizerRule("py-subprocess-shell-false-array", ("python",), "run", _SUBPROCESS,
                  re.compile(r"shell\s*=\s*False"), "subprocess_array", "cmd_argument"),
    SanitizerRule("py-subprocess-popen-shell-false-array", ("python",), "Popen", _SUBPROCESS,
                  re.compile(r"shell\s*=\s*False"), "subprocess_array", "cmd_argument"),
```

> `arg_hint` 用 `shell\s*=\s*False` 正则匹配 arg_expr 文本。`match`（`:284`）已支持 `arg_hint.search(arg_expr)`——照现有 sql_bind 占位符检测同款机制。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_sanitizer_library.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/sanitizer_library.py packages/core/tests/code_index/test_sanitizer_library.py
git commit -m "feat(code_index): subprocess shell=False array-arg defense annotation (spec 改动 1.2 C)"
```

---

### Task 4: dual_track_merger 解耦 externally_exploitable（改动 3′）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/dual_track_merger.py:41-57`（`_clone_with_merge_fields`）
- Test: `packages/core/tests/code_index/test_dual_track_merger.py`

**Interfaces:**
- Consumes: `_is_vulnerable`（`:34`）、`merge_dual_track_queues`（`:60`）。
- Produces: 合并后 `externally_exploitable` 保留 base finding 的可达性标签，不再被 verdict-OR 覆写。

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/code_index/test_dual_track_merger.py — 追加
from shannon_core.models.queue_schemas import InjectionVulnerability
from shannon_core.code_index.dual_track_merger import merge_dual_track_queues


def _inj(externally_exploitable, verdict="vulnerable", confidence="high"):
    return InjectionVulnerability(
        ID="INJ-1", vulnerability_type="injection",
        externally_exploitable=externally_exploitable,
        confidence=confidence, verdict=verdict,
    )


def test_cross_service_reachability_preserved_through_merge():
    # 跨服务 finding: externally_exploitable=False, verdict=vulnerable
    llm = [_inj(externally_exploitable=False, verdict="vulnerable")]
    merged = merge_dual_track_queues(llm, [], mode="verdict")
    assert len(merged) == 1
    assert merged[0].externally_exploitable is False, "可达性标签不能被 verdict 覆写"
    assert merged[0].verdict == "vulnerable"


def test_both_track_vulnerable_keeps_reachability_from_llm_base():
    llm = [_inj(externally_exploitable=False, verdict="vulnerable")]
    gn = [_inj(externally_exploitable=True, verdict="vulnerable")]
    merged = merge_dual_track_queues(llm, gn, mode="verdict")
    assert len(merged) == 1
    # base 是 llm（:100），保留其可达性 False
    assert merged[0].externally_exploitable is False
    assert merged[0].verdict == "vulnerable"  # verdict 仍是 OR 结果
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_dual_track_merger.py -v -k "reachability"`
Expected: FAIL（合并后 `externally_exploitable` 被覆写成 `True`）。

- [ ] **Step 3: 删覆写行**

`dual_track_merger.py:41-57` `_clone_with_merge_fields`，**删除** `data["externally_exploitable"] = vulnerable`（`:52`）：

```python
def _clone_with_merge_fields(
    finding: Vulnerability,
    *,
    merge_source: str,
    confidence: str,
    vulnerable: bool,
    evidence_chain: str | None = None,
) -> Vulnerability:
    data = finding.model_dump()
    data["merge_source"] = merge_source
    data["confidence"] = confidence
    # spec 改动 3′：不再覆写 externally_exploitable —— 保留 base finding 的可达性标签
    # （both/llm-only 分支 base 是 LLM 轨、可达性权威；gitnexus-only 分支 base 是 GitNexus 轨）
    if evidence_chain and not data.get("evidence_chain"):
        data["evidence_chain"] = evidence_chain
    if data.get("verdict") is not None:
        data["verdict"] = "vulnerable" if vulnerable else "safe"
    return type(finding).model_validate(data)
```

`merge_dual_track_queues` 三分支的 `vulnerable`（verdict-OR）计算**不变**。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/code_index/test_dual_track_merger.py -v`
Expected: PASS（含新测试 + 现有回归）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/code_index/dual_track_merger.py packages/core/tests/code_index/test_dual_track_merger.py
git commit -m "feat(code_index): decouple externally_exploitable (reachability) from verdict in dual-track merger (spec 改动 3′)"
```

---

### Task 5: openai 引擎 Task/Agent 子代理工具（改动 4a）

**Files:**
- Modify: `packages/core/src/shannon_core/agents/tools_openai/__init__.py`（`ToolContext` 加 `subagent_run`、`build_tools` 加 `task`）
- Create: `packages/core/src/shannon_core/agents/tools_openai/task.py`
- Modify: `packages/core/src/shannon_core/agents/providers_openai.py`（`call` 注入 `subagent_run`）
- Test: `packages/core/tests/agents/tools_openai/test_task.py`（Create）
- Modify: `packages/core/tests/agents/tools_openai/test_registry.py`（8→9）

**Interfaces:**
- Consumes: `ToolContext`（cwd）、openai-agents `Agent`/`Runner`/`function_tool`、`read_file`/`glob`/`grep`（子代理代码阅读工具）。
- Produces: `task` function_tool（name `task`），签名 `(description, prompt, subagent_type="general-purpose")` → spawn 代码阅读子代理 → 返回其输出。经 `build_tools()` 注入 `Agent(tools=...)`。两引擎共用 TS 原样 Task-delegation prompt（不改 prompt）。

- [ ] **Step 1: 写失败测试 test_task.py**

```python
# packages/core/tests/agents/tools_openai/test_task.py
import pytest
from agents import RunContextWrapper

from shannon_core.agents.tools_openai import ToolContext
from shannon_core.agents.tools_openai.task import _task_impl


def _ctx(tmp_path, subagent_run=None):
    return RunContextWrapper(ToolContext(cwd=str(tmp_path), subagent_run=subagent_run))


@pytest.mark.asyncio
async def test_task_impl_delegates_to_subagent_run(tmp_path):
    seen = []

    async def fake_run(prompt: str) -> str:
        seen.append(prompt)
        return f"subagent: {prompt}"

    ctx = _ctx(tmp_path, subagent_run=fake_run)
    out = await _task_impl(ctx, "analyze app.py", "read app.py and report SQLi")
    assert "subagent: read app.py and report SQLi" in out
    assert seen == ["read app.py and report SQLi"]


@pytest.mark.asyncio
async def test_task_impl_graceful_when_no_subagent_run(tmp_path):
    ctx = _ctx(tmp_path)  # subagent_run=None
    out = await _task_impl(ctx, "d", "p")
    assert "error" in out.lower() or "unavailable" in out.lower()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/agents/tools_openai/test_task.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.agents.tools_openai.task`。

- [ ] **Step 3: 创建 task.py**

```python
# packages/core/src/shannon_core/agents/tools_openai/task.py
"""Task/Agent 子代理委派工具（spec 改动 4a）。

对齐 Claude Code CLI 的 Task 语义：给定 prompt → spawn 子代理读码 → 返回结果。
让 openai 引擎与 CLI 引擎流程一致（同一 Task-delegation prompt），vuln prompt 不用改。
"""
from __future__ import annotations

from agents import RunContextWrapper, function_tool

from . import ToolContext


async def _task_impl(
    ctx: RunContextWrapper[ToolContext],
    description: str,
    prompt: str,
    subagent_type: str = "general-purpose",
) -> str:
    """Delegate a code-analysis subtask to a fresh subagent (Task Agent).

    Use this for source code analysis to keep the parent context lean.
    Mirrors Claude Code's Task tool so the same vuln prompt works on both engines.

    Args:
        description: Short description of the subtask.
        prompt: Full instruction for the subagent (e.g. "read app.py, trace data flow to the sink").
        subagent_type: Subagent profile (default general-purpose).
    """
    runner = ctx.context.subagent_run
    if runner is None:
        return "[task error] subagent runner unavailable in this engine context"
    try:
        return await runner(prompt)
    except Exception as exc:  # noqa: BLE001 — 子代理失败不能拖垮父 agent
        return f"[task error] subagent failed: {exc}"


task = function_tool(_task_impl, name_override="task")
```

- [ ] **Step 4: ToolContext 加 subagent_run + build_tools 加 task**

`tools_openai/__init__.py`：

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class ToolContext:
    """Runner context：注入工具的工作目录 + 子代理 runner（改动 4a）。"""

    cwd: str
    # spec 改动 4a：子代理委派 runner。provider 注入（关 chat_model+cwd）；测试可 mock。
    subagent_run: Callable[[str], Awaitable[str]] | None = None


def build_tools():
    from .exec import bash, grep
    from .fs import edit_file, glob, read_file, write_file
    from .task import task
    from .web import web_fetch, web_search

    return [bash, read_file, write_file, edit_file, grep, glob, web_fetch, web_search, task]


__all__ = ["ToolContext", "build_tools"]
```

- [ ] **Step 5: 运行 task 测试确认通过**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/agents/tools_openai/test_task.py -v`
Expected: PASS。

- [ ] **Step 6: 更新 test_registry（8→9）**

```python
# packages/core/tests/agents/tools_openai/test_registry.py
from shannon_core.agents.tools_openai import build_tools


def test_build_tools_returns_nine():
    tools = build_tools()
    names = {t.name for t in tools}
    assert names == {
        "bash", "read_file", "write_file", "edit_file",
        "grep", "glob", "web_fetch", "web_search", "task",
    }
```

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/agents/tools_openai/test_registry.py -v`
Expected: PASS。

- [ ] **Step 7: provider 注入 subagent_run**

`providers_openai.py`，在 `call()`（`:85`）内构建 `chat_model` 后、`Runner.run_streamed` 前，构造 subagent runner 并传入 ToolContext。改 `call` 内：

```python
    async def call(self, prompt, cwd, model_tier="medium", output_format=None,
                   deliverables_subdir=None, audit_logger=None):
        start_time = time.time()
        model = self._get_model(model_tier)
        try:
            agent = self.build_agent(model, output_format)
            collector = StreamCollector(audit_logger)
            stop_reason = None
            try:
                result = Runner.run_streamed(
                    agent,
                    input=prompt,
                    context=ToolContext(cwd=cwd, subagent_run=self._make_subagent_runner(model, cwd)),
                    max_turns=self._max_turns(),
                )
                # ... 其余 stream_events / close 不变 ...
```

新增方法 `_make_subagent_runner`（class OpenAIProvider 内）：

```python
    def _make_subagent_runner(self, model: str, cwd: str):
        """构建子代理 runner：代码阅读 Agent（read/glob/grep）跑 prompt，返回 final_output。"""
        from agents import Agent, ModelSettings, Runner

        from .tools_openai.exec import grep
        from .tools_openai.fs import glob, read_file

        client = self._get_client()
        chat_model = OpenAIChatCompletionsModel(model=model, openai_client=client)
        subagent = Agent(
            name="shannon-task-subagent",
            instructions=None,  # prompt 当 user input
            tools=[read_file, glob, grep],
            model=chat_model,
            model_settings=ModelSettings(include_usage=False),
        )
        max_turns = int(os.getenv("SHANNON_OPENAI_SUBAGENT_MAX_TURNS", "20"))

        async def run(prompt: str) -> str:
            res = await Runner.run(
                subagent, input=prompt,
                context=ToolContext(cwd=cwd),  # 子代理同 cwd，无 subagent_run（防递归）
                max_turns=max_turns,
            )
            return str(res.final_output)

        return run
```

> 注：`Runner.run`（非 streamed）阻塞等子代理完成，返回 `final_output`。子代理用 `ToolContext(cwd=cwd)`（**不带** `subagent_run`，防嵌套递归）；子代理工具 `read_file`/`glob`/`grep` 共享同一 cwd。

- [ ] **Step 8: 运行 provider 相关测试 + import 冒烟**

Run: `cd /root/shannon-py && .venv/bin/python -c "from shannon_core.agents.providers_openai import OpenAIProvider; print('import OK')"`
Expected: `import OK`（无语法/拼写错）。

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/agents/tools_openai/ -v`
Expected: PASS（test_task / test_registry / test_fs / test_exec / test_web 全绿）。

- [ ] **Step 9: Commit**

```bash
cd /root/shannon-py
git add packages/core/src/shannon_core/agents/tools_openai/ packages/core/src/shannon_core/agents/providers_openai.py packages/core/tests/agents/tools_openai/
git commit -m "feat(agents): Task/Agent subagent tool for openai engine — dual-engine flow parity (spec 改动 4a)"
```

---

### Task 6: vuln-injection prompt — sink 清单 + 契约（改动 1.1 + 2）

**Files:**
- Modify: `prompts/vuln-injection.txt:145`（sink 清单）、`:134-135`（契约）
- Test: `packages/core/tests/prompts/test_vuln_injection_prompt.py`（Create）

**Interfaces:**
- Consumes: 上游 TS spec 的 per-language sink 清单 + 契约修复文本。
- Produces: vuln-injection.txt 含新 per-language sink 清单 + 从真实攻击面 section 派生源（不引 hints）。

- [ ] **Step 1: 写失败测试（prompt 内容断言）**

```python
# packages/core/tests/prompts/test_vuln_injection_prompt.py
from pathlib import Path

PROMPT = Path("prompts/vuln-injection.txt")


def test_prompt_has_per_language_orm_raw_checklist():
    text = PROMPT.read_text()
    # 改动 1.1：per-language ORM Raw 清单
    assert "db.Raw" in text and "gorm.Expr" in text
    assert "knex.raw" in text and "sequelize.query" in text
    assert "createNativeQuery" in text and "whereRaw" in text
    # 动态标识符
    assert "ORDER BY" in text and "identifier" in text.lower()
    # 间接命令执行
    assert "shell=True" in text and "sh -c" in text


def test_prompt_contract_does_not_reference_nonexistent_section7():
    text = PROMPT.read_text()
    # 改动 2：不再从不存在的 "Section 7. Injection Sources" 派生
    assert "7. Injection Sources" not in text
    # 改为从真实攻击面 section + grep 派生
    assert "External Entry Points" in text or "Data Flow Security" in text
    assert "grep" in text.lower()
    # 不引确定性 hints（LLM 轨自给自足）
    assert "static_dataflow_hints" not in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/prompts/test_vuln_injection_prompt.py -v`
Expected: FAIL（当前 prompt 是粗清单 + 含 "7. Injection Sources"）。

- [ ] **Step 3: 改 sink 清单（`:145`）**

把 `prompts/vuln-injection.txt:145` 的粗清单：

```
		- **SQLi:** DB calls, raw SQL, string-built queries | **Command:** `exec`, `system`, `subprocess`, shell invocations | **File:** `include`, `require`, `fopen`, `readFile` | **SSTI:** template `render`/`compile` with user content | **Deserialize:** `pickle.loads`, `unserialize`, `readObject`, `yaml.load`
```

替换为 per-language 清单：

```
		- **SQLi (ORM Raw / string-built — highest-miss class):**
			- Go: `db.Raw(`, `gorm.Expr(`, `Where(fmt.Sprintf(`, `db.Exec(` + concat, sharded `tableName` interpolation
			- Node: `sequelize.query`/`.literal`, `knex.raw`, typeorm `.query`, mongoose string `where()`
			- Python: `cursor.execute(f"...")`, `%` formatting, `.raw(`, SQLAlchemy `text()`
			- Java: MyBatis `${}` vs `#{}`, JPA `createNativeQuery`, JDBC `Statement` + concat
			- PHP: `query()` + concat, Laravel `whereRaw` / `DB::raw`
		- **SQLi dynamic identifiers** (slot=ident; binds do NOT protect): table name / column name / `ORDER BY` / `GROUP BY` interpolation → MUST trace to origin; non-whitelisted = vulnerable
		- **Command:** `exec`, `system`, `subprocess`, shell invocations | **Indirect:** `sh -c`, `shell=True`, library-wrapped shell, SSH, job-scheduler concatenation of user strings
		- **File:** `include`, `require`, `fopen`, `readFile` | **SSTI:** template `render`/`compile` with user content | **Deserialize:** `pickle.loads`, `unserialize`, `readObject`, `yaml.load`
```

- [ ] **Step 4: 改契约（`:134-135`）**

把 methodology step 1 的：

```
	- **1) Create a To Do for each Injection Source found in the Pre-Recon Deliverable
			- inside of {{DELIVERABLES_PATH}}/pre_recon_deliverable.md under the section "7. Injection Sources (Command Injection and SQL Injection)" use the TodoWrite tool to create a task for each discovered Injection Source. 
			- Note: All sources are marked as Tainted until they Hit a Santiization that matches the sink context. normalizers (lowercasing, trimming, JSON parse, schema decode) — still **tainted**.
```

替换为：

```
	- **1) Derive candidate Injection Sources (do NOT rely on a non-existent "Injection Sources" section):**
			- a. From `{{DELIVERABLES_PATH}}/pre_recon_deliverable.md` real attack-surface sections — **External Entry Points**, **Data Flow Security**, **Input Validation Patterns** — extract input vectors as candidate sources.
			- b. **Actively grep the whole repo** for the sink checklist above (ORM Raw / dynamic identifiers / indirect command). Do NOT rely solely on an upstream target list.
			- Note: All sources are marked as Tainted until they Hit a Sanitization that matches the sink context. normalizers (lowercasing, trimming, JSON parse, schema decode) — still **tainted**.
```

> LLM 轨自给自足——**不引 `static_dataflow_hints`**（双轨消费模型：确定性 hints 只服务 GitNexus 轨，改动 4b 会移除该 include）。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/prompts/test_vuln_injection_prompt.py -v`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py
git add prompts/vuln-injection.txt packages/core/tests/prompts/test_vuln_injection_prompt.py
git commit -m "docs(prompt): per-language sink checklist + Section-7 contract fix in vuln-injection (spec 改动 1.1/2)"
```

---

### Task 7: vuln-injection prompt — 跨服务入队 + step5 + 移除 hints include（改动 3a + 3b + 4b）

**Files:**
- Modify: `prompts/vuln-injection.txt:161`（QUEUE INCLUSION CRITERIA）、`:153-156`（step 5）、`:45`（移除 hints include）
- Test: `packages/core/tests/prompts/test_vuln_injection_prompt.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
# packages/core/tests/prompts/test_vuln_injection_prompt.py — 追加
def test_prompt_queue_includes_cross_service_findings():
    text = PROMPT.read_text()
    # 改动 3a：externally_exploitable 是可达性标签，不挡入队
    assert "EVERY" in text and "vulnerable" in text
    # 不再含旧闸门措辞
    assert "ONLY include vulnerabilities where `externally_exploitable = true`" not in text


def test_prompt_step5_marks_cross_service_as_vulnerable():
    text = PROMPT.read_text()
    # 改动 3b：跨服务转发 = vulnerable
    assert "downstream" in text.lower() or "cross-service" in text.lower()
    assert "externally_exploitable=false" in text or "externally_exploitable = false" in text


def test_prompt_does_not_include_static_dataflow_hints():
    text = PROMPT.read_text()
    # 改动 4b：移除 hints include（LLM 轨自给自足）
    assert "@include(shared/_static-dataflow-hints.txt)" not in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/prompts/test_vuln_injection_prompt.py -v -k "cross_service or step5 or static_dataflow_hints"`
Expected: FAIL（旧闸门措辞仍在、include 仍在）。

- [ ] **Step 3: 改 QUEUE INCLUSION CRITERIA（`:161`）**

把：

```
    - **QUEUE INCLUSION CRITERIA:** ONLY include vulnerabilities where `externally_exploitable = true`. Exclude any vulnerability requiring internal network access, VPN, or direct server access.
```

替换为：

```
    - **QUEUE INCLUSION CRITERIA:** EVERY `vulnerable` finding enters the exploitation queue regardless of reachability. `externally_exploitable` is a per-entry REACHABILITY TAG, not an admission gate: `true` = reachable via public internet without internal access; `false` = internal / cross-service only. Do NOT exclude internal or cross-service findings — set their `externally_exploitable=false` and include them.
```

- [ ] **Step 4: 改 step 5（`:153-156`）**

在 step 5 "Make the call" 末尾追加一条：

```
    - **Cross-service sink:** a user-controlled SQL/command fragment that THIS service forwards to a DOWNSTREAM service for execution (e.g. an RPC/protobuf field whose value is a SQL fragment, or a sharded table name derived from user input) is a `vulnerable` cross-service sink with `externally_exploitable=false` — NOT `safe`. "Locally this binary doesn't execute it" is not a safe verdict when a downstream service will.
```

- [ ] **Step 5: 移除 hints include（`:45`）**

删除 `prompts/vuln-injection.txt:45` 整行：

```
@include(shared/_static-dataflow-hints.txt)
```

（LLM 轨自给自足，不消费确定性 hints——双轨消费模型。其余 `@include` 行保留。）

- [ ] **Step 6: 运行测试确认通过**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/prompts/test_vuln_injection_prompt.py -v`
Expected: PASS（全部）。

- [ ] **Step 7: Commit**

```bash
cd /root/shannon-py
git add prompts/vuln-injection.txt packages/core/tests/prompts/test_vuln_injection_prompt.py
git commit -m "docs(prompt): cross-service queue inclusion + remove static-dataflow-hints include (spec 改动 3a/3b/4b)"
```

---

### Task 8: injection-exploit prompt — externally_exploitable=false 分诊（改动 3c）

**Files:**
- Modify: `prompts/injection-exploit.txt`（新增 triage 段）
- Test: `packages/core/tests/prompts/test_injection_exploit_prompt.py`（Create）

- [ ] **Step 1: 写失败测试**

```python
# packages/core/tests/prompts/test_injection_exploit_prompt.py
from pathlib import Path

PROMPT = Path("prompts/injection-exploit.txt")


def test_exploit_has_externally_exploitable_false_triage():
    text = PROMPT.read_text()
    # 改动 3c：externally_exploitable=false 走短证据分诊，不 live exploit
    assert "externally_exploitable=false" in text or "externally_exploitable = false" in text
    assert "cross-service" in text.lower()
    # 关键：不尝试利用，发短证据
    assert "short evidence" in text.lower() or "referred to report" in text.lower()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/prompts/test_injection_exploit_prompt.py -v`
Expected: FAIL（当前无此 triage 规则）。

- [ ] **Step 3: 新增 triage 段**

在 `prompts/injection-exploit.txt` 靠近 "no skipping" / "minimum 3 payload" 规则处，插入：

```
<externally_exploitable_false_triage>
For queue entries with `externally_exploitable=false` (internal / cross-service sinks): do NOT attempt live exploitation — there is no local sink in this binary to exploit. Instead emit a SHORT evidence entry ("cross-service exposure: this service forwards a user-controlled <SQL/command> fragment to <downstream service>; not exploitable in this binary, referred to report for cross-service follow-up") and advance immediately. This is correct classification, not skipping — it is exempt from the "no skipping" and "minimum 3 payload attempts" rules.
</externally_exploitable_false_triage>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /root/shannon-py && .venv/bin/python -m pytest packages/core/tests/prompts/test_injection_exploit_prompt.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py
git add prompts/injection-exploit.txt packages/core/tests/prompts/test_injection_exploit_prompt.py
git commit -m "docs(prompt): externally_exploitable=false triage in injection-exploit (spec 改动 3c)"
```

---

### Task 9: 端到端验证（glm-openai Task probe + prompt 闭环）

**Files:**
- Reuse: `scripts/validate_glm_task_probe.py`（已存在，glm-anthropic 侧已通过）
- New: `scripts/validate_openai_task_probe.py`（glm-openai 侧，验证 Task tool 实现）

> 此 task 是**人工/冒烟验证**，非单测。验证 Task 5 的 openai Task tool 在 glm-openai 下真的能被 GLM 驱动。

- [ ] **Step 1: 复制 probe 脚本到 openai 版**

```bash
cd /root/shannon-py
cp scripts/validate_glm_task_probe.py scripts/validate_openai_task_probe.py
```

改 `scripts/validate_openai_task_probe.py` 的 `load_profile`：
- profile 路径改 `.env.profiles/glm-openai.env`
- `os.environ["SHANNON_AI_PROVIDER"] = "openai_compatible"`（覆盖默认）
- 删 `IS_SANDBOX=1` 依赖（openai 引擎不走 CLI，不需要）
- 输出标注 `provider=openai_compatible (glm-openai)`

- [ ] **Step 2: 运行 openai probe（人工，需 GLM openai key）**

Run: `cd /root/shannon-py && .venv/bin/python scripts/validate_openai_task_probe.py 2>&1 | tail -30`
Expected: `TOOLS CALLED` 含 `task`（GLM 经 openai 引擎发起新 Task tool 调用），最终产出 SQLi 判定。

- [ ] **Step 3: 失败处置**

若 GLM 在 glm-openai 下不发起 `task`：
- 检查 `build_tools()` 是否含 `task`（test_registry 应已绿）。
- 检查 `providers_openai._make_subagent_runner` 是否被注入（ToolContext.subagent_run 非 None）。
- 若 GLM 仍不发起——记录现象，可能 GLM openai 端点 tool-use 行为差异，反馈负责人。

- [ ] **Step 4: Commit probe 脚本**

```bash
cd /root/shannon-py
git add scripts/validate_openai_task_probe.py
git commit -m "test(agents): glm-openai Task-tool validation probe (spec 改动 4a checkpoint)"
```

---

## Self-Review（plan 写完后自检）

**1. Spec 覆盖：**
- 改动 1.1（sink 清单）→ Task 6 ✓
- 改动 1.2 A（ORM Raw 规则）→ Task 1 ✓
- 改动 1.2 B（动态标识符 arg-shape）→ Task 2 ✓
- 改动 1.2 C（命令侧 FP）→ Task 3 ✓
- 改动 1.2 D（白名单守卫）→ Task 1 Step 5 ✓
- 改动 2（契约）→ Task 6 ✓
- 改动 3a（queue criteria）→ Task 7 ✓
- 改动 3b（step 5）→ Task 7 ✓
- 改动 3c（exploit 分诊）→ Task 8 ✓
- 改动 3′（合并器解耦）→ Task 4 ✓
- 改动 4a（openai Task tool）→ Task 5 + Task 9 验证 ✓
- 改动 4b（移除 hints include）→ Task 7 ✓

**2. 占位符扫描：** 无 TBD/TODO；每步含具体代码/命令。Task 2 的 `_looks_string_built` 是 best-effort 正则（有 pinned 测试），非占位符。

**3. 类型一致性：** `task` function_tool name 统一 `task`（test_registry `:test_build_tools_returns_nine` 与 task.py `name_override="task"` 一致）；`ToolContext.subagent_run: Callable[[str], Awaitable[str]] | None` 在 `__init__.py`、`task.py`、`providers_openai.py` 三处一致；`_task_impl(ctx, description, prompt, subagent_type="general-purpose")` 签名一致。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-25-injection-recall-port.md`.
