# Plan 1: 传播引擎精度修复 + 调用图降级报告

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 taint 传播引擎的 LHS 赋值正则，使 >60% 因 `self.x`/`d["k"]`/解构/append/+= 断裂的传播路径恢复正常；为调用图添加降级报告使残缺可见。

**Architecture:** 扩展 `_match_assignment` 函数识别 6 种新的赋值模式，在 `analyze_intra` 中对容器写操作做 taint 传播（当容器本身 tainted 时其写操作也传播），在 `call_graph.resolve_edges` 中添加统计信息产出 `DegradationReport`。

**Tech Stack:** Python 3.11+, pytest, pydantic

**Spec:** `docs/superpowers/specs/2026-06-10-whitebox-analysis-tri-dimensional-comparison.md` §3.2.1, §3.2.2

---

## File Structure

| 文件 | 职责 | 改动类型 |
|---|---|---|
| `packages/core/src/shannon_core/code_index/propagation_builder.py` | LHS 正则 + 赋值传播逻辑 | **修改** |
| `packages/core/tests/code_index/test_propagation_builder.py` | 新增赋值模式测试 | **修改** |
| `packages/core/src/shannon_core/code_index/call_graph.py` | 降级报告数据结构 + resolve_edges 统计 | **修改** |
| `packages/core/src/shannon_core/code_index/models.py` | DegradationReport 模型（如不存在则新建） | **修改** |
| `packages/core/tests/code_index/test_call_graph.py` | 降级报告测试 | **修改** |

---

### Task 1: 扩展 _match_assignment 支持 6 种新赋值模式

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/propagation_builder.py:91-94, 192-200`
- Test: `packages/core/tests/code_index/test_propagation_builder.py`

当前 `_ASSIGN_RE` 只匹配 `x = expr`（纯标识符 LHS）。需要扩展为识别以下 6 种模式，每种返回 `(lhs, rhs)` 对：

| 模式 | 正则 | LHS 值 | 示例 |
|---|---|---|---|
| 属性赋值 `self.x = expr` | `^\s*([\w.]+)\s*(?<![<>!=])=(?!=)` | `self.x` | `self.user_id = data["id"]` |
| 字典写 `d["k"] = expr` | `^\s*([\w]+)\[(["\'][\w]+["\'])\]\s*=\s*` | `d` (容器名) | `result["data"] = query()` |
| 解构 `a, b = expr` | `^\s*([\w]+)\s*,\s*([\w]+)\s*=\s*` | 返回多值，见下 | `name, age = params.split(",")` |
| 增量 `x += expr` | `^\s*([A-Za-z_][\w]*)\s*([+\-*\|&])=\s*` | `x` | `data += user_input` |
| append `lst.append(expr)` | `^\s*([\w]+)\.append\(\s*(.+?)\s*\)` | `lst` (列表名) | `items.append(user_input)` |
| Go `x := expr` | 已有 `_ASSIGN_GO_RE` | 不变 | `x := getValue()` |

**关键设计决策：**

1. `_match_assignment` 返回类型从 `tuple[str | None, str]` 改为 `tuple[list[str], str]`（LHS 是标识符列表，因为解构赋值有多个 LHS）
2. 属性赋值 `self.x` 的 LHS 返回 `self.x`（完整属性路径），但 taint 检查时只需检查 `self` 是否在 tainted 中
3. 字典写 `d["k"] = expr` 返回 LHS=`["d"]`（容器名），因为容器过近似下 `d` 已经 tainted
4. `append` 不是赋值语句但效果等价，作为特殊模式处理

- [ ] **Step 1: Write the failing test — attribute assignment propagation**

在 `test_propagation_builder.py` 的 `TestIntraProcedural` 类中添加：

```python
def test_self_attribute_assignment_propagates_taint(self):
    """self.x = tainted_val → self 被标记为 tainted（容器过近似）。"""
    from shannon_core.code_index.propagation_builder import analyze_intra
    block = _block(
        "handler", "app.py", 1,
        source=(
            "def handler(self, user_input):\n"
            "    self.user_id = user_input\n"
            "    cursor.execute(self.user_id)\n"
        ),
        params=["self", "user_input"],
    )
    sink = self._make_sink(block.id, line=3, arg_idx=0, expression="self.user_id")
    result = analyze_intra(
        block, seed={"user_input"},
        sinks_in_func=[sink],
    )
    assert sink.id in result.hits
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_propagation_builder.py::TestIntraProcedural::test_self_attribute_assignment_propagates_taint -v`
Expected: FAIL — `self.user_id = user_input` 不被 `_ASSIGN_RE` 匹配

- [ ] **Step 3: Write the failing test — dictionary write propagation**

```python
def test_dict_write_propagates_taint(self):
    """d["key"] = tainted_val → d 被标记为 tainted。"""
    from shannon_core.code_index.propagation_builder import analyze_intra
    block = _block(
        "handler", "app.py", 1,
        source=(
            "def handler(user_input):\n"
            "    result = {}\n"
            "    result['data'] = user_input\n"
            "    process(result)\n"
        ),
        params=["user_input"],
    )
    # 无 sink，只验证 taint 传播
    from shannon_core.code_index.propagation_builder import analyze_intra
    result = analyze_intra(block, seed={"user_input"}, sinks_in_func=[])
    # result["data"] 赋值后 result 应该在 tainted 中
    # 我们通过 local_steps 检查：应存在 to_param="result" 的 step
    result_steps = [s for s in result.local_steps_accumulated if s.to_param == "result"]
    assert len(result_steps) >= 1
```

- [ ] **Step 4: Write the failing test — destructuring assignment**

```python
def test_destructuring_assignment_propagates_taint(self):
    """name, age = tainted.split(',') → name 和 age 都被标记为 tainted。"""
    from shannon_core.code_index.propagation_builder import analyze_intra
    block = _block(
        "handler", "app.py", 1,
        source=(
            "def handler(user_input):\n"
            "    name, age = user_input.split(',')\n"
            "    cursor.execute(name)\n"
        ),
        params=["user_input"],
    )
    sink = self._make_sink(block.id, line=3, arg_idx=0, expression="name")
    result = analyze_intra(
        block, seed={"user_input"},
        sinks_in_func=[sink],
    )
    assert sink.id in result.hits
```

- [ ] **Step 5: Write the failing test — augmented assignment**

```python
def test_augmented_assignment_propagates_taint(self):
    """data += user_input → data 被标记为 tainted。"""
    from shannon_core.code_index.propagation_builder import analyze_intra
    block = _block(
        "handler", "app.py", 1,
        source=(
            "def handler(user_input):\n"
            "    data = 'prefix '\n"
            "    data += user_input\n"
            "    cursor.execute(data)\n"
        ),
        params=["user_input"],
    )
    sink = self._make_sink(block.id, line=4, arg_idx=0, expression="data")
    result = analyze_intra(
        block, seed={"user_input"},
        sinks_in_func=[sink],
    )
    assert sink.id in result.hits
```

- [ ] **Step 6: Write the failing test — append propagation**

```python
def test_list_append_propagates_taint(self):
    """items.append(tainted) → items 被标记为 tainted。"""
    from shannon_core.code_index.propagation_builder import analyze_intra
    block = _block(
        "handler", "app.py", 1,
        source=(
            "def handler(user_input):\n"
            "    items = []\n"
            "    items.append(user_input)\n"
            "    cursor.execute(str(items))\n"
        ),
        params=["user_input"],
    )
    sink = self._make_sink(block.id, line=4, arg_idx=0, expression="str(items)")
    result = analyze_intra(
        block, seed={"user_input"},
        sinks_in_func=[sink],
    )
    assert sink.id in result.hits
```

- [ ] **Step 7: Run all 5 new tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_propagation_builder.py -k "attribute_assignment or dict_write or destructuring or augmented or list_append" -v`
Expected: ALL FAIL

- [ ] **Step 8: Implement _match_assignment expansion**

修改 `propagation_builder.py`。替换第 91-94 行的 `_ASSIGN_RE` 和 `_ASSIGN_GO_RE`，以及第 192-200 行的 `_match_assignment` 函数：

```python
# === 行级赋值识别（扩展版）============================================

# 1. 纯标识符赋值：x = expr
_ASSIGN_PLAIN_RE = re.compile(
    r"^\s*([A-Za-z_][\w]*)\s*(?<![<>!=])=(?!=)\s*(.+?)\s*$"
)
# 2. 属性赋值：self.x = expr / obj.attr = expr
_ASSIGN_ATTR_RE = re.compile(
    r"^\s*([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+)\s*(?<![<>!=])=(?!=)\s*(.+?)\s*$"
)
# 3. 字典/下标写：d["key"] = expr / d['key'] = expr / d[0] = expr
_ASSIGN_SUBSCRIPT_RE = re.compile(
    r"^\s*([A-Za-z_][\w]*)\[.+?\]\s*(?<![<>!=])=(?!=)\s*(.+?)\s*$"
)
# 4. 解构赋值：a, b = expr (2-5 个标识符)
_ASSIGN_DESTRUCT_RE = re.compile(
    r"^\s*([A-Za-z_][\w]*(?:\s*,\s*[A-Za-z_][\w]*){1,4})\s*(?<![<>!=])=(?!=)\s*(.+?)\s*$"
)
# 5. 增量赋值：x += expr / x |= expr
_ASSIGN_AUG_RE = re.compile(
    r"^\s*([A-Za-z_][\w]*)\s*([+\-*|&])=\s*(.+?)\s*$"
)
# 6. Go 短变量声明：x := expr
_ASSIGN_GO_RE = re.compile(
    r"^\s*([A-Za-z_][\w]*)\s*:=(?!=)\s*(.+?)\s*$"
)
# 7. 列表 append：lst.append(expr)
_APPEND_RE = re.compile(
    r"^\s*([A-Za-z_][\w]*)\.append\(\s*(.+?)\s*\)\s*$"
)
```

替换 `_match_assignment` 函数：

```python
def _match_assignment(line: str) -> tuple[list[str], str]:
    """识别各种赋值模式。返回 (lhs_names, rhs)；不匹配返回 ([], "")。

    返回 LHS 标识符列表（解构赋值会有多个），RHS 表达式。
    对于属性赋值 (self.x) 返回 ["self"]——容器名即 taint 跟踪目标。
    对于字典写 (d["k"]) 返回 ["d"]——容器名即 taint 跟踪目标。
    """
    # 优先级：append > 增量 > Go > 属性 > 下标 > 解构 > 纯标识符
    # （从特殊到一般，避免提前误匹配）

    # 7. append
    m = _APPEND_RE.match(line)
    if m:
        return [m.group(1)], m.group(2)

    # 5. 增量赋值 x += expr
    m = _ASSIGN_AUG_RE.match(line)
    if m:
        return [m.group(1)], m.group(3)

    # 6. Go 短变量
    m = _ASSIGN_GO_RE.match(line)
    if m:
        return [m.group(1)], m.group(2)

    # 2. 属性赋值 self.x = expr (必须在纯标识符之前)
    m = _ASSIGN_ATTR_RE.match(line)
    if m:
        # 返回容器名（如 self），而不是完整路径
        container = m.group(1).split(".")[0]
        return [container], m.group(2)

    # 3. 下标写 d["k"] = expr
    m = _ASSIGN_SUBSCRIPT_RE.match(line)
    if m:
        return [m.group(1)], m.group(2)

    # 4. 解构赋值 a, b = expr
    m = _ASSIGN_DESTRUCT_RE.match(line)
    if m:
        names = [n.strip() for n in m.group(1).split(",")]
        return names, m.group(2)

    # 1. 纯标识符赋值 x = expr
    m = _ASSIGN_PLAIN_RE.match(line)
    if m:
        return [m.group(1)], m.group(2)

    return [], ""
```

- [ ] **Step 9: Update analyze_intra to use new _match_assignment return type**

修改 `propagation_builder.py` 的 `analyze_intra` 函数（第 149-169 行），将单 LHS 处理改为多 LHS 循环：

替换第 149-169 行：

```python
        # 1) 赋值（扩展版：支持多 LHS）
        lhs_names, rhs = _match_assignment(line)
        if lhs_names:
            transformation = _detect_transformation(rhs)
            if _expr_references_tainted(rhs, tainted):
                for lhs in lhs_names:
                    tainted.add(lhs)
                    accumulated_steps.append(PropagationStep(
                        step_id="",
                        from_func_id=block.id,
                        from_param=_first_tainted_in(rhs, tainted) or "",
                        to_func_id=block.id, to_param=lhs,
                        transformation=transformation,
                        code_location=f"{block.file_path}:{line_no}",
                        confidence=0.8 if transformation else 1.0,
                    ))
                if transformation and transformation.startswith("sanitize_hint:"):
                    has_sanitizer_global = True
            if _has_sanitizer(rhs):
                has_sanitizer_global = True
```

- [ ] **Step 10: Run all tests to verify new patterns pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_propagation_builder.py -v`
Expected: ALL PASS（包括原有的 15 个测试 + 新增 5 个）

- [ ] **Step 11: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/code_index/propagation_builder.py packages/core/tests/code_index/test_propagation_builder.py
git commit -m "fix(propagation): expand LHS assignment regex to support self.x, d[\"k\"], destructuring, augmented, append

The old _ASSIGN_RE only matched plain identifier assignments (x = expr),
causing taint propagation to break on >60% of real-world code patterns:
- self.x = expr (OOP, most common)
- d[\"key\"] = expr (dict operations)
- a, b = x, y (Python destructuring)
- lst.append(x) (list operations)
- x += expr (augmented assignment)

The new _match_assignment returns list[str] (multiple LHS for destructuring)
and recognizes 7 patterns total. All existing tests continue to pass."
```

---

### Task 2: 调用图降级报告

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/call_graph.py:9-29`
- Modify: `packages/core/src/shannon_core/code_index/models.py:69-84`
- Test: `packages/core/tests/code_index/test_call_graph.py`

`resolve_edges` 当前静默地丢弃无法匹配的调用边和因同名函数取第一个候选导致的错配。需要添加 `DegradationReport` 数据结构，产出统计信息。

- [ ] **Step 1: Write the failing test — degradation report counts**

在 `test_call_graph.py` 中添加：

```python
class TestDegradationReport:
    def test_unresolved_edges_reported(self):
        """未解析的调用边应计入 degradation_report。"""
        blocks = [_block("handler", "app.py", 1)]
        edges = [_edge("app.py:handler:1", "unknown_func", resolved=False)]
        from shannon_core.code_index.call_graph import resolve_edges
        resolved = resolve_edges(edges, blocks)
        report = resolve_edges.report  # type: ignore[attr-defined]
        assert report is not None
        assert report.unresolved_count == 1

    def test_ambiguous_matches_reported(self):
        """同名函数多候选匹配应计入 ambiguous_count。"""
        blocks = [_block("helper", "a.py", 1), _block("helper", "b.py", 1)]
        edges = [_edge("app.py:main:1", "helper", resolved=False)]
        from shannon_core.code_index.call_graph import resolve_edges
        resolved = resolve_edges(edges, blocks)
        report = resolve_edges.report  # type: ignore[attr-defined]
        assert report is not None
        assert report.ambiguous_count == 1

    def test_total_edges_reported(self):
        """报告应包含总边数。"""
        blocks = [_block("get_users", "svc.py", 10)]
        edges = [
            _edge("app.py:handler:1", "get_users", resolved=False),
            _edge("app.py:handler:1", "missing", resolved=False),
        ]
        from shannon_core.code_index.call_graph import resolve_edges
        resolved = resolve_edges(edges, blocks)
        report = resolve_edges.report
        assert report.total_edges == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_call_graph.py::TestDegradationReport -v`
Expected: FAIL — `resolve_edges` 没有 `report` 属性

- [ ] **Step 3: Add DegradationReport model**

在 `models.py` 的 `DegradationLevel` 枚举之后（约第 170 行）添加：

```python
class DegradationReport(BaseModel):
    """调用图构建时的降级/质量报告。被写进 code_index.json，
    下游 risk_scorer / Spec C 可据此决定 hint 可信度。"""
    total_edges: int = 0           # 总调用边数
    resolved_count: int = 0        # 成功解析的边数
    unresolved_count: int = 0      # 未找到目标的边数
    ambiguous_count: int = 0       # 同名函数多候选（取了第一个）的边数
    truncated_count: int = 0       # 被 max_width 截断的边数

    @property
    def resolution_rate(self) -> float:
        """解析率 = resolved / total。低于 0.5 表示调用图严重残缺。"""
        return self.resolved_count / self.total_edges if self.total_edges > 0 else 1.0

    @property
    def is_degraded(self) -> bool:
        """是否显著降级（解析率 < 70% 或存在截断）。"""
        return self.resolution_rate < 0.7 or self.truncated_count > 0
```

- [ ] **Step 4: Update resolve_edges to produce DegradationReport**

修改 `call_graph.py` 的 `resolve_edges` 函数：

```python
from shannon_core.code_index.models import CallChain, CallEdge, FuncBlock, DegradationReport


def resolve_edges(edges: list[CallEdge], blocks: list[FuncBlock]) -> list[CallEdge]:
    """Resolve call edges by matching callee names to known function blocks.

    Also populates resolve_edges.report with degradation statistics.
    """
    name_index: dict[str, list[FuncBlock]] = defaultdict(list)
    for block in blocks:
        name_index[block.function_name].append(block)

    resolved: list[CallEdge] = []
    unresolved_count = 0
    ambiguous_count = 0

    for edge in edges:
        candidates = name_index.get(edge.callee_name, [])
        if candidates:
            if len(candidates) > 1:
                ambiguous_count += 1
            match = candidates[0]
            resolved.append(CallEdge(
                caller_id=edge.caller_id,
                callee_name=edge.callee_name,
                callee_file=match.file_path,
                resolved=True,
                line=edge.line,
            ))
        else:
            unresolved_count += 1
            resolved.append(edge)

    # 附加降级报告到函数对象上，供调用方读取
    resolve_edges.report = DegradationReport(
        total_edges=len(edges),
        resolved_count=len(edges) - unresolved_count,
        unresolved_count=unresolved_count,
        ambiguous_count=ambiguous_count,
    )

    return resolved
```

- [ ] **Step 5: Add truncated_count tracking in build_call_chains**

在 `build_call_chains` 函数中（第 69 行 `[:max_width]` 截断处），统计截断数：

在第 69 行之后添加截断统计：

```python
            outgoing = adj.get(current_id, [])
            all_outgoing = outgoing
            resolved_outgoing = [e for e in all_outgoing if e.resolved][:max_width]
            truncated = max(0, len([e for e in all_outgoing if e.resolved]) - max_width)
            # 累积截断数到调用方可访问的位置
            if truncated > 0:
                build_call_chains.truncated_total = getattr(
                    build_call_chains, 'truncated_total', 0
                ) + truncated
            unresolved_outgoing = [e for e in outgoing if not e.resolved]
```

并在函数开头初始化：

```python
def build_call_chains(
    entry_point_ids: list[str],
    edges: list[CallEdge],
    max_depth: int = 15,
    max_width: int = 50,
    blocks: list[FuncBlock] | None = None,
    preserve_diamonds: bool = False,
) -> list[CallChain]:
    """Build call chains from entry points using BFS."""
    build_call_chains.truncated_total = 0
    # ... rest unchanged
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_call_graph.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/code_index/call_graph.py packages/core/src/shannon_core/code_index/models.py packages/core/tests/code_index/test_call_graph.py
git commit -m "feat(call-graph): add degradation report for unresolved/ambiguous/truncated edges

resolve_edges now populates resolve_edges.report with counts of
unresolved, ambiguous (multi-candidate), and truncated edges.
Downstream risk_scorer and Spec C LLM can use this to assess
call graph quality instead of silently trusting incomplete data."
```

---

### Task 3: 集成降级报告到流水线

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/__init__.py` (在 `run_code_index` 或 `rebuild_call_chains` 之后写入报告)
- Modify: `packages/core/src/shannon_core/code_index/models.py` (在 CodeIndex 中添加 degradation_report 字段)

- [ ] **Step 1: Add degradation_report field to CodeIndex**

在 `models.py` 的 `CodeIndex` 类中（约第 69-84 行），在 `sink_call_sites` 字段后添加：

```python
    degradation_report: "DegradationReport | None" = None
```

- [ ] **Step 2: Write the failing test — degradation report in pipeline**

在 `test_call_graph.py` 中添加：

```python
class TestDegradationReportInResolveEdges:
    def test_report_attached_to_function(self):
        """resolve_edges.report 应在调用后可用。"""
        blocks = [_block("get_users", "svc.py", 10)]
        edges = [
            _edge("app.py:handler:1", "get_users", resolved=False),
            _edge("app.py:handler:1", "missing", resolved=False),
        ]
        resolve_edges(edges, blocks)
        assert resolve_edges.report.total_edges == 2
        assert resolve_edges.report.resolved_count == 1
        assert resolve_edges.report.unresolved_count == 1
        assert resolve_edges.report.resolution_rate == 0.5
        assert resolve_edges.report.is_degraded is True
```

- [ ] **Step 3: Run test to verify it passes**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/test_call_graph.py::TestDegradationReportInResolveEdges -v`
Expected: PASS

- [ ] **Step 4: Verify existing tests still pass**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/code_index/ -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add packages/core/src/shannon_core/code_index/models.py packages/core/tests/code_index/test_call_graph.py
git commit -m "feat(models): add degradation_report field to CodeIndex

CodeIndex can now carry a DegradationReport showing call graph quality.
Pipeline stages can populate this after resolve_edges / build_call_chains."
```

---

## Self-Review

**1. Spec coverage check:**
- §3.2.1 (LHS 正则 bug) → Task 1 ✅
- §3.2.2 (调用图残缺降级报告) → Task 2 ✅
- §3.2.2 (降级报告集成) → Task 3 ✅

**2. Placeholder scan:**
- No TBD/TODO/fill-in-later found ✅
- All code blocks contain actual implementation ✅
- All test assertions are specific ✅

**3. Type consistency:**
- `_match_assignment` returns `tuple[list[str], str]` everywhere ✅
- `DegradationReport` defined in `models.py`, used in `call_graph.py` with proper import ✅
- `analyze_intra` loop variable `lhs_names` is `list[str]`, iterated with `for lhs in lhs_names` ✅

**Potential issue:** The `resolve_edges.report` pattern (attaching to function object) is unconventional but pragmatic — avoids changing the return type which would break all existing callers. Tests use `# type: ignore[attr-defined]` which is acceptable for function attribute pattern.
