# Spec B：AST 精确 Sink 识别实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 tree-sitter AST 在调用点粒度精确识别 5 种语言（Python/TypeScript/Go/Java/PHP）的安全 sink，产出结构化的 `SinkCallSite` 列表，写入 `code_index.json`，供 Spec A（参数传播）与 Spec C（LLM 研判）消费。

**Architecture:** 三层结构 — (1) `BaseParser` 扩展三个新方法（`iter_calls` / `destructure_call` / `extract_arg_expressions`），五个语言 parser 各自实现；(2) `sink_detector` 模块持有结构化的 `SinkRule` 规则库，遍历所有 FuncBlock 的 call 节点，按 qualified-name + receiver 模式匹配 sink；(3) 命中后产出 `SinkCallSite`（含 `dangerous_slots` 危险参数槽位），写入 `CodeIndex.sink_call_sites`。`risk_scorer` 升级为基于 chain 上**所有**节点的 SinkCallSite 取最大 danger 分。`classify_sink` 保留作兼容回退。

**Tech Stack:** Python 3.12+, Pydantic v2, tree-sitter 0.24 (python/typescript/go/java/php), pytest

**Spec:** `docs/superpowers/specs/2026-06-09-spec-b-sink-detector-design.md`

**Depended on by:** Spec A（`SinkCallSite` 作为传播终点）、Spec C（`SinkCallSite` 摘要喂 LLM）

---

## File Structure

### New Files

| File | Responsibility |
|---|---|
| `packages/core/src/shannon_core/code_index/sink_detector.py` | `SinkRule` dataclass、起始规则库、`detect_sinks()` 主算法、`is_entry_hint()` 浅判断、`category_to_sink_type()` 兼容映射 |
| `packages/core/tests/code_index/test_sink_detector.py` | `SinkCallSite` / `SlotContext` / `SinkCategory` 模型测试 + `detect_sinks` 跨语言测试 + 规则库正反例测试 + `is_entry_hint` 单测 |
| `packages/core/tests/code_index/fixtures/python/sinks.py` | Python sink fixture（cursor.execute / os.system / subprocess.run / pickle.loads / requests.get / render_template_string） |
| `packages/core/tests/code_index/fixtures/typescript/sinks.ts` | TypeScript sink fixture（eval / innerHTML / document.write / fetch / child_process.exec） |
| `packages/core/tests/code_index/fixtures/go/sinks.go` | Go sink fixture（exec.Command / http.Get / template.HTML） |
| `packages/core/tests/code_index/fixtures/java/Sinks.java` | Java sink fixture（Runtime.exec / ObjectMapper.readValue / Statement.executeQuery） |
| `packages/core/tests/code_index/fixtures/php/sinks.php` | PHP sink fixture（mysqli::query / unserialize / shell_exec / file_get_contents） |

### Modified Files

| File | Change |
|---|---|
| `packages/core/src/shannon_core/code_index/parameter_models.py` | 新增 `SlotContext`、`DangerousSlot`、`SinkCategory`、`SinkCallSite`（在 `SinkType` 之后），保留 `SinkType` 兼容 |
| `packages/core/src/shannon_core/code_index/models.py` | `CodeIndex` 增加 `sink_call_sites: list[SinkCallSite] = []` 字段；前向引用解决（SinkCallSite 在 parameter_models 定义） |
| `packages/core/src/shannon_core/code_index/parsers/base.py` | 新增 `CallNode` 数据类 + 三个抽象方法 `iter_calls` / `destructure_call` / `extract_arg_expressions` |
| `packages/core/src/shannon_core/code_index/parsers/python_parser.py` | 实现三个新方法（复用现有 `_get_callee_name` 和 `call` 节点遍历） |
| `packages/core/src/shannon_core/code_index/parsers/typescript_parser.py` | 实现三个新方法（`call_expression` + `member_expression`） |
| `packages/core/src/shannon_core/code_index/parsers/go_parser.py` | 实现三个新方法（`call_expression` + `selector_expression`） |
| `packages/core/src/shannon_core/code_index/parsers/java_parser.py` | 实现三个新方法（`method_invocation`） |
| `packages/core/src/shannon_core/code_index/parsers/php_parser.py` | 实现三个新方法（三种 call 节点） |
| `packages/core/src/shannon_core/code_index/__init__.py` | `build_code_index` 在解析 edges 之后调用 `sink_detector.detect_sinks`，把结果填入 `CodeIndex.sink_call_sites` |
| `packages/core/src/shannon_core/code_index/risk_scorer.py` | `ChainRiskScore.score` 改为：从 chain 上**所有**节点的 SinkCallSite 取最大 danger（降级回退到 `classify_sink`）；新增 `SINK_CATEGORY_DANGER_SCORES` |
| `packages/core/src/shannon_core/code_index/taint_propagator.py` | `classify_sink` 标注 deprecated 注释，内部签名不变（兼容回退用） |

---

## Task 1: 新增 SinkCallSite 数据模型

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/parameter_models.py`
- Test: `packages/core/tests/code_index/test_sink_detector.py`（新建）

定义 Spec B 共享枢纽数据结构。所有后续 task 依赖此模型。

- [ ] **Step 1: 写失败测试 — 模型构造与字段约束**

创建 `packages/core/tests/code_index/test_sink_detector.py`，写入：

```python
"""Tests for sink_detector module and SinkCallSite model."""
from shannon_core.code_index.parameter_models import (
    SinkCallSite, DangerousSlot, SlotContext, SinkCategory, SinkType,
)


class TestSlotContext:
    def test_values(self):
        assert SlotContext.SQL_VALUE == "sql_value"
        assert SlotContext.SQL_IDENTIFIER == "sql_identifier"
        assert SlotContext.CMD_ARGUMENT == "cmd_argument"
        assert SlotContext.FILE_PATH == "file_path"
        assert SlotContext.TEMPLATE_EXPR == "template_expr"
        assert SlotContext.URL == "url"
        assert SlotContext.DESERIALIZE_OBJ == "deserialize"
        assert SlotContext.GENERIC == "generic"


class TestSinkCategory:
    def test_values(self):
        assert SinkCategory.SQL == "sql"
        assert SinkCategory.COMMAND == "command"
        assert SinkCategory.FILE == "file"
        assert SinkCategory.TEMPLATE == "template"
        assert SinkCategory.DESERIALIZATION == "deserialization"
        assert SinkCategory.SSRF == "ssrf"
        assert SinkCategory.XSS == "xss"
        assert SinkCategory.LOG == "log"
        assert SinkCategory.REDIRECT == "redirect"


class TestDangerousSlot:
    def test_basic(self):
        slot = DangerousSlot(
            arg_index=0,
            slot=SlotContext.SQL_VALUE,
            expression="user_sql",
            is_entry_hint=False,
        )
        assert slot.arg_index == 0
        assert slot.slot == SlotContext.SQL_VALUE
        assert slot.expression == "user_sql"
        assert slot.is_entry_hint is False

    def test_variadic_index(self):
        slot = DangerousSlot(
            arg_index=-1,
            slot=SlotContext.CMD_ARGUMENT,
            expression="*args",
            is_entry_hint=False,
        )
        assert slot.arg_index == -1


class TestSinkCallSite:
    def test_basic(self):
        site = SinkCallSite(
            id="app.py:handler:execute:5:8",
            caller_id="app.py:handler:1",
            callee_name="execute",
            callee_receiver="cursor",
            category=SinkCategory.SQL,
            sink_subtype="sql_raw",
            file_path="app.py",
            line=5,
            column=8,
            dangerous_slots=[
                DangerousSlot(
                    arg_index=0,
                    slot=SlotContext.SQL_VALUE,
                    expression="user_sql",
                    is_entry_hint=False,
                ),
            ],
            rule_id="py-db-cursor-execute",
        )
        assert site.id == "app.py:handler:execute:5:8"
        assert site.callee_name == "execute"
        assert site.callee_receiver == "cursor"
        assert site.category == SinkCategory.SQL
        assert site.needs_review is False  # default
        assert len(site.dangerous_slots) == 1

    def test_needs_review_default_false(self):
        site = SinkCallSite(
            id="a:b:c:1:0",
            caller_id="a:b:1",
            callee_name="c",
            callee_receiver=None,
            category=SinkCategory.XSS,
            sink_subtype="xss_dom",
            file_path="a",
            line=1,
            column=0,
            dangerous_slots=[],
            rule_id="ts-innerhtml",
        )
        assert site.needs_review is False

    def test_serialization_roundtrip(self):
        site = SinkCallSite(
            id="a.py:foo:bar:1:0",
            caller_id="a.py:foo:1",
            callee_name="bar",
            callee_receiver=None,
            category=SinkCategory.COMMAND,
            sink_subtype="js_eval",
            file_path="a.py",
            line=1,
            column=0,
            dangerous_slots=[],
            rule_id="ts-eval",
            needs_review=True,
        )
        json_str = site.model_dump_json()
        assert '"js_eval"' in json_str
        assert '"needs_review":true' in json_str
        site2 = SinkCallSite.model_validate_json(json_str)
        assert site2.category == SinkCategory.COMMAND
        assert site2.needs_review is True


class TestSinkTypeCompatibility:
    """Spec B 保留 SinkType 作 risk_scorer 兼容。"""
    def test_sink_type_still_defined(self):
        assert SinkType.SQL_EXECUTION == "sql_execution"
        assert SinkType.COMMAND_EXEC == "command_exec"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_sink_detector.py::TestSlotContext -v`
Expected: FAIL — `ImportError: cannot import name 'SinkCallSite'`

- [ ] **Step 3: 实现 — 新增模型到 `parameter_models.py`**

打开 `packages/core/src/shannon_core/code_index/parameter_models.py`，在文件末尾（`ParameterPropagationGraph` 类之后）追加：

```python
# === Spec B: AST-precise sink detection ===


class SlotContext(str, Enum):
    """Sink 输入位的安全上下文 —— 呼应原始项目的 slot 类型系统。"""
    SQL_VALUE = "sql_value"            # SQL-val/like/num —— 需参数绑定
    SQL_IDENTIFIER = "sql_identifier"  # SQL-enum/ident —— 需白名单
    CMD_ARGUMENT = "cmd_argument"      # 需数组参数 + shell=False / shlex.quote
    FILE_PATH = "file_path"            # 需白名单路径 / resolve+边界检查
    TEMPLATE_EXPR = "template_expr"    # SSTI —— 需沙箱+autoescape
    URL = "url"                        # SSRF —— 需协议/主机白名单
    DESERIALIZE_OBJ = "deserialize"    # 需可信来源+HMAC
    GENERIC = "generic"                # 未细分


class DangerousSlot(BaseModel):
    """sink 调用中一个需要防御的参数位。"""
    arg_index: int            # 第几个实参（0-based）；-1 表示 variadic/spread 整体
    slot: SlotContext
    expression: str           # 该实参的源码表达式文本（供 Spec A/LLM 追踪）
    is_entry_hint: bool       # AST 能直接看出该实参源自函数参数/外部输入（浅判断）


class SinkCategory(str, Enum):
    """主分类，与 SinkType 并存。"""
    SQL = "sql"
    COMMAND = "command"
    FILE = "file"
    TEMPLATE = "template"
    DESERIALIZATION = "deserialization"
    SSRF = "ssrf"
    XSS = "xss"              # 仅 code-level（innerHTML/document.write 等）
    LOG = "log"
    REDIRECT = "redirect"


class SinkCallSite(BaseModel):
    """一次具体的危险函数调用 —— 三 spec 共享枢纽。

    id 格式："{file}:{caller_func}:{callee}:{line}:{col}"，Spec A 的
    `TaintFlow.sink_call_site_id` 必须用此格式。
    """
    id: str
    caller_id: str                          # 所在 FuncBlock.id
    callee_name: str                        # 方法/函数名，如 "execute"
    callee_receiver: str | None             # receiver，如 "cursor" / "subprocess"；裸函数为 None
    category: SinkCategory
    sink_subtype: str                       # 细分类型，如 "sql_raw_query" / "ssrf_http_client"
    file_path: str
    line: int
    column: int
    dangerous_slots: list[DangerousSlot]    # 规则库标注的危险参数位 + slot
    rule_id: str                            # 命中的规则 id（可追溯到规则库定义）
    needs_review: bool = False              # best-effort 判定 / 动态调用 / 模板类，需 LLM 复核
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_sink_detector.py -v`
Expected: PASS（全部 4 个测试类）

- [ ] **Step 5: 确认现有测试不破坏**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_risk_scorer.py packages/core/tests/code_index/test_tiered_audit.py -v`
Expected: PASS（SinkType 仍存在）

- [ ] **Step 6: 提交**

```bash
git add packages/core/src/shannon_core/code_index/parameter_models.py packages/core/tests/code_index/test_sink_detector.py
git commit -m "feat(sink-detector): add SinkCallSite/DangerousSlot/SlotContext/SinkCategory models

Spec B 共享枢纽数据结构。SinkCallSite 是后续 Spec A 传播终点和 Spec C
LLM 研判消费的精确事实，包含 callee/receiver/category/dangerous_slots。
SinkType 保留作 risk_scorer 兼容。"
```

---

## Task 2: CodeIndex 增加 sink_call_sites 字段

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/models.py`
- Modify: `packages/core/tests/code_index/test_models.py`

让 `CodeIndex` 持有 Spec B 的产物。pydantic 自动序列化进 `code_index.json`。

- [ ] **Step 1: 写失败测试 — CodeIndex 默认空 sink_call_sites**

在 `packages/core/tests/code_index/test_models.py` 末尾追加：

```python
class TestCodeIndexSinkCallSites:
    def test_default_empty_sink_call_sites(self):
        from shannon_core.code_index.models import CodeIndex
        index = CodeIndex(
            repository="repo",
            language="python",
            total_blocks=0,
            total_entry_points=0,
            total_chains=0,
            blocks=[],
            edges=[],
            entry_points=[],
            chains=[],
        )
        assert index.sink_call_sites == []

    def test_sink_call_sites_serialized(self):
        from shannon_core.code_index.models import CodeIndex
        from shannon_core.code_index.parameter_models import (
            SinkCallSite, SinkCategory,
        )
        site = SinkCallSite(
            id="a.py:f:execute:1:0",
            caller_id="a.py:f:1",
            callee_name="execute",
            callee_receiver="cursor",
            category=SinkCategory.SQL,
            sink_subtype="sql_raw",
            file_path="a.py",
            line=1,
            column=0,
            dangerous_slots=[],
            rule_id="py-db-cursor-execute",
        )
        index = CodeIndex(
            repository="repo",
            language="python",
            total_blocks=1,
            total_entry_points=0,
            total_chains=0,
            blocks=[],
            edges=[],
            entry_points=[],
            chains=[],
            sink_call_sites=[site],
        )
        json_str = index.model_dump_json()
        assert '"sink_call_sites"' in json_str
        assert '"py-db-cursor-execute"' in json_str
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_models.py::TestCodeIndexSinkCallSites -v`
Expected: FAIL — `ValidationError: sink_call_sites` field not found

- [ ] **Step 3: 实现 — 增加 sink_call_sites 字段**

修改 `packages/core/src/shannon_core/code_index/models.py`，先在文件顶部 import 之后增加 TYPE_CHECKING 块（避免循环依赖），然后修改 `CodeIndex`：

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from shannon_core.code_index.parameter_models import SinkCallSite
```

然后在 `CodeIndex` 类（约 64 行）的字段末尾增加：

```python
class CodeIndex(BaseModel):
    repository: str
    language: str
    total_blocks: int
    total_entry_points: int
    total_chains: int
    blocks: list[FuncBlock]
    edges: list[CallEdge]
    entry_points: list[EntryPoint]
    chains: list[CallChain]
    # Extended fields for GitNexus integration (forward refs to avoid circular order)
    file_manifest: "FileManifest | None" = None
    degradation_level: "DegradationLevel | None" = None
    # Spec B: AST-precise sink detection (use forward ref; resolved at runtime via model_rebuild)
    sink_call_sites: list["SinkCallSite"] = []
```

在文件末尾追加（确保 `SinkCallSite` 在运行时被解析）：

```python
# Resolve forward references for sink_call_sites (Spec B)
def _resolve_forward_refs() -> None:
    try:
        from shannon_core.code_index.parameter_models import SinkCallSite  # noqa: F401
        CodeIndex.model_rebuild()
    except ImportError:
        pass


_resolve_forward_refs()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_models.py::TestCodeIndexSinkCallSites -v`
Expected: PASS

- [ ] **Step 5: 确认现有测试不破坏**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/ -v -k "not test_sink_detector"`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add packages/core/src/shannon_core/code_index/models.py packages/core/tests/code_index/test_models.py
git commit -m "feat(code-index): add sink_call_sites field to CodeIndex

Spec B 产物挂载点，pydantic 自动序列化进 code_index.json 供 Spec A/C 消费。"
```

---

## Task 3: BaseParser 接口扩展（CallNode + 三方法）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/parsers/base.py`
- Modify: `packages/core/tests/code_index/test_base_parser.py`

为五个 parser 定义统一的 call 节点遍历与拆分接口。此 task 仅定义接口与默认行为；后续 Task 4-8 各 parser 实现。

- [ ] **Step 1: 写失败测试 — BaseParser 新方法存在且为抽象**

在 `packages/core/tests/code_index/test_base_parser.py` 末尾追加：

```python
def test_call_node_dataclass():
    from shannon_core.code_index.parsers.base import CallNode
    node = CallNode(
        raw_call_node=None,
        raw_arg_nodes=[],
        line=5,
        column=4,
    )
    assert node.line == 5
    assert node.column == 4
    assert node.raw_arg_nodes == []


def test_concrete_parser_must_implement_iter_calls():
    from shannon_core.code_index.parsers.base import BaseParser

    class IncompleteParser(BaseParser):
        def parse_file(self, file_path, repo_root):
            return []

        def extract_calls(self, block, source):
            return []

        # missing: iter_calls, destructure_call, extract_arg_expressions

    with pytest.raises(TypeError):
        IncompleteParser()


def test_concrete_parser_with_new_methods_instantiates():
    from shannon_core.code_index.parsers.base import BaseParser

    class FullParser(BaseParser):
        def parse_file(self, file_path, repo_root):
            return []

        def extract_calls(self, block, source):
            return []

        def iter_calls(self, block, source):
            return iter([])

        def destructure_call(self, call):
            return ("foo", None)

        def extract_arg_expressions(self, call, source):
            return []

    p = FullParser()
    assert p is not None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_base_parser.py -v`
Expected: FAIL — `ImportError: cannot import name 'CallNode'` 或 `TypeError` 在 `IncompleteParser()` 处不抛（因为新方法未声明为 abstract）

- [ ] **Step 3: 实现 — 扩展 BaseParser**

替换 `packages/core/src/shannon_core/code_index/parsers/base.py` 全文：

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from shannon_core.code_index.models import CallEdge, FuncBlock


@dataclass(frozen=True)
class CallNode:
    """A tree-sitter call node plus its pre-extracted argument nodes.

    `raw_call_node` and `raw_arg_nodes` are language-specific tree_sitter Node
    objects. The parser methods `destructure_call()` and
    `extract_arg_expressions()` know how to handle them.
    """
    raw_call_node: object
    raw_arg_nodes: list[object] = field(default_factory=list)
    line: int = 0       # 1-based
    column: int = 0     # 0-based


class BaseParser(ABC):
    @abstractmethod
    def parse_file(self, file_path: Path, repo_root: Path) -> list[FuncBlock]:
        """Parse a source file and return all function blocks found."""
        ...

    @abstractmethod
    def extract_calls(self, block: FuncBlock, source: bytes) -> list[CallEdge]:
        """Extract call edges from a function block's source."""
        ...

    @abstractmethod
    def iter_calls(self, block: FuncBlock, source: bytes) -> Iterator[CallNode]:
        """Iterate call nodes within a function block.

        Each yielded CallNode must carry the raw tree-sitter call node and
        the raw argument subnodes (in positional order). line/column point at
        the call site.
        """
        ...

    @abstractmethod
    def destructure_call(self, call: CallNode) -> tuple[str, str | None]:
        """Return (callee_name, receiver_text) for a call.

        receiver_text is None for bare function calls (e.g. `eval(x)`).
        For `cursor.execute(sql)`, callee_name="execute", receiver_text="cursor".
        """
        ...

    @abstractmethod
    def extract_arg_expressions(self, call: CallNode, source: bytes) -> list[str]:
        """Return the source text of each positional argument.

        For `f(a, b=c)`, returns ["a", "b=c"]. Keyword args are kept verbatim;
        sink_detector decides how to interpret them.
        """
        ...
```

- [ ] **Step 4: 运行测试 — 旧测试会失败（5 个 parser 都缺新方法）**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_base_parser.py -v`
Expected: 3 个新测试 PASS；新测试之外，旧的 `test_*` 仍 PASS（它们不实例化具体 parser）

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/ -v -k "parser"`
Expected: 5 个 parser 的 `parse_file` 直接调用测试（`PythonParser()` 等）会 FAIL，因为新方法是 abstract。**这是预期的** — Task 4-8 会修复。先不要提交此 Task 的失败状态，继续 Task 4 紧接着修复 Python parser，按依赖顺序逐个修复。

- [ ] **Step 5: 提交（接口 + 测试）**

```bash
git add packages/core/src/shannon_core/code_index/parsers/base.py packages/core/tests/code_index/test_base_parser.py
git commit -m "feat(parser-base): add CallNode and iter_calls/destructure_call/extract_arg_expressions

Spec B §4.1.3 — 统一五个 parser 的 call 节点遍历接口。
注意：五个具体 parser 现在无法实例化，下个 task 修复 Python，依次修复其他四门。"
```

---

## Task 4: PythonParser 实现新方法

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/parsers/python_parser.py`
- Modify: `packages/core/tests/code_index/test_python_parser.py`

让 Python parser 实现 `iter_calls` / `destructure_call` / `extract_arg_expressions`，恢复可实例化。

- [ ] **Step 1: 写失败测试 — Python call 节点提取**

在 `packages/core/tests/code_index/test_python_parser.py` 末尾追加：

```python
class TestPythonParserIterCalls:
    def test_iter_calls_returns_call_nodes(self):
        parser = PythonParser()
        source = FLASK_APP.read_bytes()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["get_users"], source))
        # get_users() calls db.query("SELECT * FROM users")
        assert len(calls) == 1
        assert calls[0].line > 0

    def test_destructure_call_member(self):
        """db.query(...) → callee=query, receiver=db"""
        parser = PythonParser()
        source = FLASK_APP.read_bytes()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["get_users"], source))
        callee, receiver = parser.destructure_call(calls[0])
        assert callee == "query"
        assert receiver == "db"

    def test_destructure_call_bare(self):
        """jsonify(...) → callee=jsonify, receiver=None"""
        parser = PythonParser()
        source = FLASK_APP.read_bytes()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["list_users"], source))
        # First call is get_users() (bare), second is jsonify(users) (bare)
        callees = [parser.destructure_call(c) for c in calls]
        bare_callees = [(c, r) for c, r in callees if r is None]
        assert any(c == "get_users" for c, _ in bare_callees)
        assert any(c == "jsonify" for c, _ in bare_callees)

    def test_extract_arg_expressions(self):
        """query("SELECT * FROM users") → ['"SELECT * FROM users"']"""
        parser = PythonParser()
        source = FLASK_APP.read_bytes()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["get_users"], source))
        args = parser.extract_arg_expressions(calls[0], source)
        assert len(args) == 1
        assert "SELECT" in args[0]

    def test_extract_arg_expressions_multiple(self):
        """update_user has body: data = request.get_json()"""
        parser = PythonParser()
        source = FLASK_APP.read_bytes()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["update_user"], source))
        # find the save_user call: save_user(user_id, data)
        for call in calls:
            callee, _ = parser.destructure_call(call)
            if callee == "save_user":
                args = parser.extract_arg_expressions(call, source)
                assert len(args) == 2
                assert "user_id" in args[0]
                assert "data" in args[1]
                return
        assert False, "save_user call not found"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_python_parser.py::TestPythonParserIterCalls -v`
Expected: FAIL — `TypeError: Can't instantiate abstract class PythonParser` 或方法缺失

- [ ] **Step 3: 实现 — PythonParser 新增三方法**

打开 `packages/core/src/shannon_core/code_index/parsers/python_parser.py`，在 `_get_callee_name` 方法之后（约 136 行），追加：

```python
    def iter_calls(self, block: FuncBlock, source: bytes):
        """Yield CallNode for every `call` node inside this function."""
        tree = self._parser.parse(source)
        for node in _walk(tree.root_node):
            if node.type in ("function_definition", "async_function_definition"):
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text.decode("utf-8") == block.function_name:
                    if node.start_point[0] + 1 == block.start_line:
                        for call_node in self._iter_call_nodes(node):
                            yield call_node
                        break

    def _iter_call_nodes(self, func_node):
        """Walk function body and yield CallNode for each `call`."""
        for node in _walk(func_node):
            if node.type == "call":
                args_node = node.child_by_field_name("arguments")
                raw_args: list = []
                if args_node is not None:
                    for child in args_node.children:
                        # Skip punctuation: '(' ')' ','
                        if child.type in ("(", ")", ","):
                            continue
                        raw_args.append(child)
                yield CallNode(
                    raw_call_node=node,
                    raw_arg_nodes=raw_args,
                    line=node.start_point[0] + 1,
                    column=node.start_point[1],
                )

    def destructure_call(self, call) -> tuple[str, str | None]:
        """For Python: `cursor.execute(x)` → ('execute', 'cursor'); `eval(x)` → ('eval', None)."""
        func_node = call.raw_call_node.child_by_field_name("function")
        if func_node is None:
            return ("", None)
        if func_node.type == "identifier":
            return (func_node.text.decode("utf-8"), None)
        if func_node.type == "attribute":
            method = func_node.child_by_field_name("attribute")
            obj = func_node.child_by_field_name("object")
            method_name = method.text.decode("utf-8") if method else ""
            receiver = obj.text.decode("utf-8") if obj else None
            return (method_name, receiver)
        return ("", None)

    def extract_arg_expressions(self, call, source: bytes) -> list[str]:
        """Slice source bytes for each argument subnode."""
        result: list[str] = []
        for arg_node in call.raw_arg_nodes:
            text = source[arg_node.start_byte:arg_node.end_byte].decode("utf-8", errors="replace")
            result.append(text)
        return result
```

同时在文件顶部 import 处加：

```python
from shannon_core.code_index.parsers.base import BaseParser, CallNode
```

（替换原 `from shannon_core.code_index.parsers.base import BaseParser`）

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_python_parser.py -v`
Expected: PASS（全部新旧测试）

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/code_index/parsers/python_parser.py packages/core/tests/code_index/test_python_parser.py
git commit -m "feat(python-parser): implement iter_calls/destructure_call/extract_arg_expressions

Spec B — Python call 节点遍历复用 _get_callee_name 模式。
拆出 receiver (cursor) / callee (execute) / 实参表达式文本，供 sink_detector 使用。"
```

---

## Task 5: TypeScriptParser 实现新方法

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/parsers/typescript_parser.py`
- Modify: `packages/core/tests/code_index/test_typescript_parser.py`

注意：TypeScript arrow function 不在当前 `extract_calls` 内（`extract_calls` 只匹配 `function_declaration/method_definition`），但 sink 调用经常发生在 arrow function 内。本 task **不修复** `extract_calls` 的 arrow function 漏洞（那是另一处独立 bug），只让 `iter_calls` 在已识别的 block 内工作。Arrow function 的修复在 Spec B 范围外，留给后续。

- [ ] **Step 1: 写失败测试 — TS call 节点提取**

在 `packages/core/tests/code_index/test_typescript_parser.py` 末尾追加：

```python
class TestTypescriptParserIterCalls:
    def test_iter_calls_function_body(self):
        """getUsers() body has db.query('SELECT...')."""
        parser = TypeScriptParser()
        source = TS_APP.read_bytes()
        blocks = parser.parse_file(TS_APP, TS_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["getUsers"], source))
        assert len(calls) >= 1

    def test_destructure_member_call(self):
        """db.query(...) → callee=query, receiver=db"""
        parser = TypeScriptParser()
        source = TS_APP.read_bytes()
        blocks = parser.parse_file(TS_APP, TS_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["getUsers"], source))
        callees = [parser.destructure_call(c) for c in calls]
        assert ("query", "db") in callees

    def test_destructure_bare_call(self):
        """getUsers() → callee=getUsers, receiver=None"""
        parser = TypeScriptParser()
        source = TS_APP.read_bytes()
        blocks = parser.parse_file(TS_APP, TS_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["listOrders"], source))
        callees = [parser.destructure_call(c) for c in calls]
        assert ("getOrders", None) in callees

    def test_extract_arg_expressions(self):
        """query('SELECT * FROM users') → ['\"SELECT * FROM users\"']"""
        parser = TypeScriptParser()
        source = TS_APP.read_bytes()
        blocks = parser.parse_file(TS_APP, TS_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["getUsers"], source))
        for call in calls:
            callee, _ = parser.destructure_call(call)
            if callee == "query":
                args = parser.extract_arg_expressions(call, source)
                assert len(args) == 1
                assert "SELECT" in args[0]
                return
        assert False, "query call not found"
```

（如果 `TS_APP` 未在文件顶部定义，先在文件顶部加：`TS_APP = Path(__file__).parent / "fixtures" / "typescript" / "express_app.ts"`）

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_typescript_parser.py::TestTypescriptParserIterCalls -v`
Expected: FAIL

- [ ] **Step 3: 实现 — TypeScriptParser 新增三方法**

打开 `packages/core/src/shannon_core/code_index/parsers/typescript_parser.py`，在 `_get_callee_name` 方法之后追加：

```python
    def iter_calls(self, block: FuncBlock, source: bytes):
        tree = self._parser.parse(source)
        for node in _walk(tree.root_node):
            if node.type in ("function_declaration", "method_definition"):
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text.decode("utf-8") == block.function_name:
                    if node.start_point[0] + 1 == block.start_line:
                        for call_node in self._iter_call_nodes(node):
                            yield call_node
                        break

    def _iter_call_nodes(self, func_node):
        for node in _walk(func_node):
            if node.type == "call_expression":
                args_node = node.child_by_field_name("arguments")
                raw_args: list = []
                if args_node is not None:
                    for child in args_node.children:
                        if child.type in ("(", ")", ",", ";"):
                            continue
                        raw_args.append(child)
                yield CallNode(
                    raw_call_node=node,
                    raw_arg_nodes=raw_args,
                    line=node.start_point[0] + 1,
                    column=node.start_point[1],
                )

    def destructure_call(self, call) -> tuple[str, str | None]:
        func_node = call.raw_call_node.child_by_field_name("function")
        if func_node is None:
            return ("", None)
        if func_node.type == "identifier":
            return (func_node.text.decode("utf-8"), None)
        if func_node.type == "member_expression":
            prop = func_node.child_by_field_name("property")
            obj = func_node.child_by_field_name("object")
            callee = prop.text.decode("utf-8") if prop else ""
            receiver = obj.text.decode("utf-8") if obj else None
            return (callee, receiver)
        return ("", None)

    def extract_arg_expressions(self, call, source: bytes) -> list[str]:
        result: list[str] = []
        for arg_node in call.raw_arg_nodes:
            text = source[arg_node.start_byte:arg_node.end_byte].decode("utf-8", errors="replace")
            result.append(text)
        return result
```

同时改 import：

```python
from shannon_core.code_index.parsers.base import BaseParser, CallNode
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_typescript_parser.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/code_index/parsers/typescript_parser.py packages/core/tests/code_index/test_typescript_parser.py
git commit -m "feat(typescript-parser): implement iter_calls/destructure_call/extract_arg_expressions

Spec B — TypeScript call_expression 节点遍历。
member_expression 拆 receiver.method，identifier 为裸调用。"
```

---

## Task 6: GoParser 实现新方法

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/parsers/go_parser.py`
- Modify: `packages/core/tests/code_index/test_go_parser.py`

- [ ] **Step 1: 写失败测试**

在 `packages/core/tests/code_index/test_go_parser.py` 末尾追加：

```python
class TestGoParserIterCalls:
    def test_iter_calls_function_body(self):
        """listUsers calls getUsers() and json.NewEncoder(w).Encode(users)."""
        parser = GoParser()
        source = GO_FIXTURE.read_bytes()
        blocks = parser.parse_file(GO_FIXTURE, GO_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["listUsers"], source))
        assert len(calls) >= 1

    def test_destructure_bare_call(self):
        """getUsers() → callee=getUsers, receiver=None"""
        parser = GoParser()
        source = GO_FIXTURE.read_bytes()
        blocks = parser.parse_file(GO_FIXTURE, GO_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["listUsers"], source))
        callees = [parser.destructure_call(c) for c in calls]
        assert ("getUsers", None) in callees

    def test_destructure_selector_call(self):
        """json.NewEncoder(w).Encode(users) — chained: callee=Encode, receiver=users (last selector)"""
        parser = GoParser()
        source = GO_FIXTURE.read_bytes()
        blocks = parser.parse_file(GO_FIXTURE, GO_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["listUsers"], source))
        callees = [parser.destructure_call(c) for c in calls]
        # Encode is the terminal method
        callee_names = [c for c, _ in callees]
        assert "Encode" in callee_names
```

（确保 `GO_FIXTURE = Path(__file__).parent / "fixtures" / "go" / "http_handler.go"` 已在文件顶部）

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_go_parser.py::TestGoParserIterCalls -v`
Expected: FAIL

- [ ] **Step 3: 实现 — GoParser 新增三方法**

打开 `packages/core/src/shannon_core/code_index/parsers/go_parser.py`，在 `_get_callee_name` 方法之后追加：

```python
    def iter_calls(self, block: FuncBlock, source: bytes):
        tree = self._parser.parse(source)
        for node in _walk(tree.root_node):
            if node.type in ("function_declaration", "method_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text.decode("utf-8") == block.function_name:
                    if node.start_point[0] + 1 == block.start_line:
                        for call_node in self._iter_call_nodes(node):
                            yield call_node
                        break

    def _iter_call_nodes(self, func_node):
        for node in _walk(func_node):
            if node.type == "call_expression":
                args_node = node.child_by_field_name("arguments")
                raw_args: list = []
                if args_node is not None:
                    for child in args_node.children:
                        if child.type in ("(", ")", ","):
                            continue
                        raw_args.append(child)
                yield CallNode(
                    raw_call_node=node,
                    raw_arg_nodes=raw_args,
                    line=node.start_point[0] + 1,
                    column=node.start_point[1],
                )

    def destructure_call(self, call) -> tuple[str, str | None]:
        func_node = call.raw_call_node.child_by_field_name("function")
        if func_node is None:
            return ("", None)
        if func_node.type == "identifier":
            return (func_node.text.decode("utf-8"), None)
        if func_node.type == "selector_expression":
            field = func_node.child_by_field_name("field")
            obj = func_node.child_by_field_name("operand")
            callee = field.text.decode("utf-8") if field else ""
            receiver = obj.text.decode("utf-8") if obj else None
            return (callee, receiver)
        return ("", None)

    def extract_arg_expressions(self, call, source: bytes) -> list[str]:
        result: list[str] = []
        for arg_node in call.raw_arg_nodes:
            text = source[arg_node.start_byte:arg_node.end_byte].decode("utf-8", errors="replace")
            result.append(text)
        return result
```

同时改 import：

```python
from shannon_core.code_index.parsers.base import BaseParser, CallNode
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_go_parser.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/code_index/parsers/go_parser.py packages/core/tests/code_index/test_go_parser.py
git commit -m "feat(go-parser): implement iter_calls/destructure_call/extract_arg_expressions

Spec B — Go call_expression 节点遍历。
selector_expression (x.Y) 拆 receiver.method，identifier 为裸函数。"
```

---

## Task 7: JavaParser 实现新方法

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/parsers/java_parser.py`
- Modify: `packages/core/tests/code_index/test_java_parser.py`

- [ ] **Step 1: 写失败测试**

在 `packages/core/tests/code_index/test_java_parser.py` 末尾追加：

```python
class TestJavaParserIterCalls:
    def test_iter_calls_method_body(self):
        """listUsers() body has userService.getUsers()."""
        parser = JavaParser()
        source = JAVA_FIXTURE.read_bytes()
        blocks = parser.parse_file(JAVA_FIXTURE, JAVA_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["listUsers"], source))
        assert len(calls) >= 1

    def test_destructure_method_invocation(self):
        """userService.getUsers() → callee=getUsers, receiver=usersService"""
        parser = JavaParser()
        source = JAVA_FIXTURE.read_bytes()
        blocks = parser.parse_file(JAVA_FIXTURE, JAVA_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["listUsers"], source))
        callees = [parser.destructure_call(c) for c in calls]
        assert ("getUsers", "userService") in callees

    def test_destructure_method_no_receiver(self):
        """A method invocation without an object (e.g., this.x()) is also handled."""
        parser = JavaParser()
        source = JAVA_FIXTURE.read_bytes()
        blocks = parser.parse_file(JAVA_FIXTURE, JAVA_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        # processOrder body: orderService.handle(message)
        calls = list(parser.iter_calls(by_name["processOrder"], source))
        callees = [parser.destructure_call(c) for c in calls]
        assert ("handle", "orderService") in callees
```

（确保 `JAVA_FIXTURE = Path(__file__).parent / "fixtures" / "java" / "SpringController.java"` 已在文件顶部）

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_java_parser.py::TestJavaParserIterCalls -v`
Expected: FAIL

- [ ] **Step 3: 实现 — JavaParser 新增三方法**

打开 `packages/core/src/shannon_core/code_index/parsers/java_parser.py`，在 `_extract_call_edges` 之后追加：

```python
    def iter_calls(self, block: FuncBlock, source: bytes):
        tree = self._parser.parse(source)
        for node in _walk(tree.root_node):
            if node.type == "method_declaration":
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text.decode("utf-8") == block.function_name:
                    if node.start_point[0] + 1 == block.start_line:
                        for call_node in self._iter_call_nodes(node):
                            yield call_node
                        break

    def _iter_call_nodes(self, method_node):
        for node in _walk(method_node):
            if node.type == "method_invocation":
                args_node = node.child_by_field_name("arguments")
                raw_args: list = []
                if args_node is not None:
                    for child in args_node.children:
                        if child.type in ("(", ")", ","):
                            continue
                        raw_args.append(child)
                yield CallNode(
                    raw_call_node=node,
                    raw_arg_nodes=raw_args,
                    line=node.start_point[0] + 1,
                    column=node.start_point[1],
                )

    def destructure_call(self, call) -> tuple[str, str | None]:
        node = call.raw_call_node
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return ("", None)
        callee = name_node.text.decode("utf-8")
        # receiver: the `object` field of method_invocation (Java TS grammar)
        obj_node = node.child_by_field_name("object")
        receiver = obj_node.text.decode("utf-8") if obj_node else None
        return (callee, receiver)

    def extract_arg_expressions(self, call, source: bytes) -> list[str]:
        result: list[str] = []
        for arg_node in call.raw_arg_nodes:
            text = source[arg_node.start_byte:arg_node.end_byte].decode("utf-8", errors="replace")
            result.append(text)
        return result
```

同时改 import：

```python
from shannon_core.code_index.parsers.base import BaseParser, CallNode
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_java_parser.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/code_index/parsers/java_parser.py packages/core/tests/code_index/test_java_parser.py
git commit -m "feat(java-parser): implement iter_calls/destructure_call/extract_arg_expressions

Spec B — Java method_invocation 节点遍历。
name 字段取 callee，object 字段取 receiver。"
```

---

## Task 8: PhpParser 实现新方法

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/parsers/php_parser.py`
- Modify: `packages/core/tests/code_index/test_php_parser.py`

PHP 有三种 call 节点（`function_call_expression` / `member_call_expression` / `scoped_call_expression`），三种都要覆盖。

- [ ] **Step 1: 写失败测试**

在 `packages/core/tests/code_index/test_php_parser.py` 末尾追加：

```python
class TestPhpParserIterCalls:
    def test_iter_calls_function_body(self):
        """getUsers() has DB::select('SELECT...')."""
        parser = PhpParser()
        source = PHP_FIXTURE.read_bytes()
        blocks = parser.parse_file(PHP_FIXTURE, PHP_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["getUsers"], source))
        assert len(calls) >= 1

    def test_destructure_scoped_call(self):
        """DB::select('SELECT...') → callee=select, receiver=DB (static)."""
        parser = PhpParser()
        source = PHP_FIXTURE.read_bytes()
        blocks = parser.parse_file(PHP_FIXTURE, PHP_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["getUsers"], source))
        callees = [parser.destructure_call(c) for c in calls]
        # DB::select — static call
        assert ("select", "DB") in callees

    def test_destructure_member_call(self):
        """listOrders body: $this->getOrders() → callee=getOrders, receiver=$this"""
        parser = PhpParser()
        source = PHP_FIXTURE.read_bytes()
        blocks = parser.parse_file(PHP_FIXTURE, PHP_FIXTURE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        calls = list(parser.iter_calls(by_name["listOrders"], source))
        callees = [parser.destructure_call(c) for c in calls]
        assert ("getOrders", "$this") in callees
```

（确保 `PHP_FIXTURE = Path(__file__).parent / "fixtures" / "php" / "laravel_routes.php"` 已在文件顶部）

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_php_parser.py::TestPhpParserIterCalls -v`
Expected: FAIL

- [ ] **Step 3: 实现 — PhpParser 新增三方法**

打开 `packages/core/src/shannon_core/code_index/parsers/php_parser.py`，在 `_get_function_call_name` 方法之后追加：

```python
    def iter_calls(self, block: FuncBlock, source: bytes):
        tree = self._parser.parse(source)
        for node in _walk(tree.root_node):
            if node.type in ("function_definition", "method_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    name_text = name_node.text.decode("utf-8").lstrip("$")
                    if name_text == block.function_name:
                        if node.start_point[0] + 1 == block.start_line:
                            for call_node in self._iter_call_nodes(node):
                                yield call_node
                            break

    def _iter_call_nodes(self, func_node):
        call_types = (
            "function_call_expression",
            "member_call_expression",
            "scoped_call_expression",
        )
        for node in _walk(func_node):
            if node.type in call_types:
                args_node = node.child_by_field_name("arguments")
                raw_args: list = []
                if args_node is not None:
                    for child in args_node.children:
                        if child.type in ("(", ")", ","):
                            continue
                        raw_args.append(child)
                yield CallNode(
                    raw_call_node=node,
                    raw_arg_nodes=raw_args,
                    line=node.start_point[0] + 1,
                    column=node.start_point[1],
                )

    def destructure_call(self, call) -> tuple[str, str | None]:
        node = call.raw_call_node
        if node.type == "function_call_expression":
            func_node = node.child_by_field_name("function")
            if func_node is None:
                return ("", None)
            name = func_node.text.decode("utf-8").lstrip("$")
            return (name, None)
        if node.type == "member_call_expression":
            name_node = node.child_by_field_name("name")
            obj = node.child_by_field_name("object")
            callee = name_node.text.decode("utf-8").lstrip("$") if name_node else ""
            receiver = obj.text.decode("utf-8") if obj else None
            return (callee, receiver)
        if node.type == "scoped_call_expression":
            name_node = node.child_by_field_name("name")
            scope = node.child_by_field_name("scope")
            callee = name_node.text.decode("utf-8").lstrip("$") if name_node else ""
            receiver = scope.text.decode("utf-8") if scope else None
            return (callee, receiver)
        return ("", None)

    def extract_arg_expressions(self, call, source: bytes) -> list[str]:
        result: list[str] = []
        for arg_node in call.raw_arg_nodes:
            text = source[arg_node.start_byte:arg_node.end_byte].decode("utf-8", errors="replace")
            result.append(text)
        return result
```

同时改 import：

```python
from shannon_core.code_index.parsers.base import BaseParser, CallNode
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_php_parser.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/code_index/parsers/php_parser.py packages/core/tests/code_index/test_php_parser.py
git commit -m "feat(php-parser): implement iter_calls/destructure_call/extract_arg_expressions

Spec B — PHP 三种 call 节点（function_call/member_call/scoped_call）遍历。
scoped_call (DB::select) 的 scope 作为 receiver。"
```

---

## Task 9: Sink 规则库（SinkRule dataclass + 起始全集）

**Files:**
- Create: `packages/core/src/shannon_core/code_index/sink_detector.py`
- Modify: `packages/core/tests/code_index/test_sink_detector.py`

定义结构化规则库。覆盖 spec 附录 A 的起始全集（SQL / Command / Deserialization / SSRF / XSS code-level / SSTI / File / Redirect / Log），共约 25 条规则。

- [ ] **Step 1: 写失败测试 — 规则库结构**

在 `packages/core/tests/code_index/test_sink_detector.py` 末尾追加：

```python
class TestSinkRuleLibrary:
    def test_sink_rule_dataclass(self):
        from shannon_core.code_index.sink_detector import SinkRule
        import re
        rule = SinkRule(
            rule_id="py-db-cursor-execute",
            languages=("python",),
            callee="execute",
            receiver_pattern=re.compile(r"^(cursor|cnx|conn|db)$"),
            category=SinkCategory.SQL,
            sink_subtype="sql_raw",
            dangerous_slots=((0, SlotContext.SQL_VALUE),),
        )
        assert rule.rule_id == "py-db-cursor-execute"
        assert rule.languages == ("python",)
        assert rule.dangerous_slots == ((0, SlotContext.SQL_VALUE),)
        assert rule.needs_review_default is False  # default

    def test_default_rule_library_loaded(self):
        """起始规则库至少覆盖 5 语言 x 7 类 sink."""
        from shannon_core.code_index.sink_detector import DEFAULT_RULES, SinkRule
        assert len(DEFAULT_RULES) >= 20
        # Verify language coverage
        langs = {lang for r in DEFAULT_RULES for lang in r.languages}
        assert "python" in langs
        assert "typescript" in langs
        assert "go" in langs
        assert "java" in langs
        assert "php" in langs
        # Verify category coverage
        cats = {r.category for r in DEFAULT_RULES}
        assert SinkCategory.SQL in cats
        assert SinkCategory.COMMAND in cats
        assert SinkCategory.DESERIALIZATION in cats
        assert SinkCategory.SSRF in cats
        assert SinkCategory.XSS in cats

    def test_py_db_cursor_execute_rule_exists(self):
        from shannon_core.code_index.sink_detector import DEFAULT_RULES
        rule = next((r for r in DEFAULT_RULES if r.rule_id == "py-db-cursor-execute"), None)
        assert rule is not None
        assert rule.callee == "execute"
        assert rule.receiver_pattern.match("cursor")
        assert rule.receiver_pattern.match("cnx")
        assert rule.receiver_pattern.match("conn")
        assert rule.receiver_pattern.match("db")
        assert not rule.receiver_pattern.match("users")  # `.query()` of a model
        assert rule.category == SinkCategory.SQL

    def test_ts_innerhtml_rule_needs_review(self):
        from shannon_core.code_index.sink_detector import DEFAULT_RULES
        # innerHTML assignment handled via assignment-style rule; if present, must be needs_review
        rule = next((r for r in DEFAULT_RULES if r.rule_id == "ts-innerhtml"), None)
        assert rule is not None
        assert rule.needs_review_default is True
        assert rule.category == SinkCategory.XSS

    def test_py_render_template_string_rule_exists(self):
        from shannon_core.code_index.sink_detector import DEFAULT_RULES
        rule = next((r for r in DEFAULT_RULES if r.rule_id == "py-render-template-string"), None)
        assert rule is not None
        assert rule.callee == "render_template_string"
        assert rule.category == SinkCategory.TEMPLATE

    def test_rule_id_unique(self):
        from shannon_core.code_index.sink_detector import DEFAULT_RULES
        ids = [r.rule_id for r in DEFAULT_RULES]
        assert len(ids) == len(set(ids))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_sink_detector.py::TestSinkRuleLibrary -v`
Expected: FAIL — `ImportError: No module named 'shannon_core.code_index.sink_detector'`

- [ ] **Step 3: 实现 — 创建 sink_detector.py 与规则库**

创建 `packages/core/src/shannon_core/code_index/sink_detector.py`：

```python
"""AST-precise sink detector (Spec B).

Identifies dangerous-function call sites at call-point granularity using
tree-sitter AST nodes and a structured rule library. Produces SinkCallSite
records that downstream stages (Spec A propagation, Spec C LLM review)
consume as authoritative facts.

Design notes:
- Rule matching is qualified-name based: `receiver.method` or bare `function`.
- receiver_pattern is a regex; covers common DB cursor names (cursor/cnx/conn/db),
  HTTP clients, etc.
- dangerous_slots are (arg_index, SlotContext) pairs declared by the rule.
- needs_review_default=True for code-level XSS / dynamic sinks where static
  precision is impossible (the LLM in Spec C is told to double-check).
"""

import re
from dataclasses import dataclass

from shannon_core.code_index.parameter_models import (
    DangerousSlot,
    SinkCallSite,
    SinkCategory,
    SlotContext,
)


@dataclass(frozen=True)
class SinkRule:
    """One rule in the sink rule library."""
    rule_id: str
    languages: tuple[str, ...]
    callee: str
    receiver_pattern: re.Pattern | None   # None = bare function call (no receiver)
    category: SinkCategory
    sink_subtype: str
    dangerous_slots: tuple[tuple[int, SlotContext], ...]
    needs_review_default: bool = False


# ===== Helpers =====

_DB_CURSOR = re.compile(r"^(cursor|cnx|conn|db|database)$")
_REQUESTS_LIKE = re.compile(r"^(requests|httpx|urllib3)$")
_OS_LIKE = re.compile(r"^(os|commands)$")
_SUBPROCESS_LIKE = re.compile(r"^(subprocess)$")
_PICKLE_LIKE = re.compile(r"^(pickle|cPickle|marshal)$")
_YAML_LIKE = re.compile(r"^(yaml)$")
_TEMPLATE_LIKE = re.compile(r"^(flask|jinja2)$")
_PHP_DB_LIKE = re.compile(r"^(mysqli|pdo|db|DB)$")
_PHP_THIS_DB = re.compile(r"^\$?(db|conn|connection)$")
_JAVA_RUNTIME = re.compile(r"^(Runtime|getRuntime)$")
_JAVA_HTTP = re.compile(r"^(HttpURLConnection|OkHttpClient|HttpClient)$")
_GO_HTTP = re.compile(r"^(http|net)$")
_GO_EXEC = re.compile(r"^(exec)$")
_TS_HTTP = re.compile(r"^(fetch|axios|got)$")  # bare-named HTTP clients
_TS_DOM = re.compile(r"^(document|element|el|div)$")


# ===== Default rule library (Spec B 附录 A) =====

DEFAULT_RULES: tuple[SinkRule, ...] = (
    # --- SQL ---
    SinkRule("py-db-cursor-execute", ("python",), "execute", _DB_CURSOR,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),)),
    SinkRule("py-db-cursor-executemany", ("python",), "executemany", _DB_CURSOR,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),)),
    SinkRule("ts-db-query", ("typescript",), "query", None,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),),
             needs_review_default=True),  # receiver unknown for `db.query`
    SinkRule("go-db-query", ("go",), "Query", None,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),),
             needs_review_default=True),
    SinkRule("java-stmt-executequery", ("java",), "executeQuery", None,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),),
             needs_review_default=True),
    SinkRule("java-stmt-execute", ("java",), "execute", None,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),),
             needs_review_default=True),
    SinkRule("php-mysqli-query", ("php",), "query", _PHP_DB_LIKE,
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),)),
    SinkRule("php-db-select-static", ("php",), "select", re.compile(r"^(DB)$"),
             SinkCategory.SQL, "sql_raw", ((0, SlotContext.SQL_VALUE),)),

    # --- Command execution ---
    SinkRule("py-os-system", ("python",), "system", _OS_LIKE,
             SinkCategory.COMMAND, "command_shell", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("py-os-popen", ("python",), "popen", _OS_LIKE,
             SinkCategory.COMMAND, "command_shell", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("py-subprocess-run", ("python",), "run", _SUBPROCESS_LIKE,
             SinkCategory.COMMAND, "command_subprocess", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("py-subprocess-popen", ("python",), "Popen", _SUBPROCESS_LIKE,
             SinkCategory.COMMAND, "command_subprocess", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("py-subprocess-call", ("python",), "call", _SUBPROCESS_LIKE,
             SinkCategory.COMMAND, "command_subprocess", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("py-subprocess-checkoutput", ("python",), "check_output", _SUBPROCESS_LIKE,
             SinkCategory.COMMAND, "command_subprocess", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("ts-eval", ("typescript",), "eval", None,
             SinkCategory.COMMAND, "js_eval", ((0, SlotContext.GENERIC),)),
    SinkRule("ts-child-process-exec", ("typescript",), "exec", None,
             SinkCategory.COMMAND, "command_exec", ((0, SlotContext.CMD_ARGUMENT),),
             needs_review_default=True),  # callee common; receiver check via 'child_process'
    SinkRule("go-exec-command", ("go",), "Command", _GO_EXEC,
             SinkCategory.COMMAND, "command_exec", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("java-runtime-exec", ("java",), "exec", _JAVA_RUNTIME,
             SinkCategory.COMMAND, "command_exec", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("php-shell-exec", ("php",), "shell_exec", None,
             SinkCategory.COMMAND, "command_shell", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("php-system", ("php",), "system", None,
             SinkCategory.COMMAND, "command_shell", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("php-passthru", ("php",), "passthru", None,
             SinkCategory.COMMAND, "command_shell", ((0, SlotContext.CMD_ARGUMENT),)),
    SinkRule("php-proc-exec", ("php",), "exec", None,
             SinkCategory.COMMAND, "command_exec", ((0, SlotContext.CMD_ARGUMENT),)),

    # --- Deserialization ---
    SinkRule("py-pickle-loads", ("python",), "loads", _PICKLE_LIKE,
             SinkCategory.DESERIALIZATION, "deser_pickle", ((0, SlotContext.DESERIALIZE_OBJ),)),
    SinkRule("py-pickle-load", ("python",), "load", _PICKLE_LIKE,
             SinkCategory.DESERIALIZATION, "deser_pickle", ((0, SlotContext.DESERIALIZE_OBJ),)),
    SinkRule("py-yaml-load", ("python",), "load", _YAML_LIKE,
             SinkCategory.DESERIALIZATION, "deser_yaml", ((0, SlotContext.DESERIALIZE_OBJ),)),
    SinkRule("php-unserialize", ("php",), "unserialize", None,
             SinkCategory.DESERIALIZATION, "deser_unserialize", ((0, SlotContext.DESERIALIZE_OBJ),)),
    SinkRule("java-objectinput-readobject", ("java",), "readObject", None,
             SinkCategory.DESERIALIZATION, "deser_java", ((0, SlotContext.DESERIALIZE_OBJ),),
             needs_review_default=True),

    # --- SSRF ---
    SinkRule("py-requests-get", ("python",), "get", _REQUESTS_LIKE,
             SinkCategory.SSRF, "ssrf_http_client", ((0, SlotContext.URL),)),
    SinkRule("py-requests-post", ("python",), "post", _REQUESTS_LIKE,
             SinkCategory.SSRF, "ssrf_http_client", ((0, SlotContext.URL),)),
    SinkRule("py-requests-put", ("python",), "put", _REQUESTS_LIKE,
             SinkCategory.SSRF, "ssrf_http_client", ((0, SlotContext.URL),)),
    SinkRule("py-urllib-urlopen", ("python",), "urlopen", None,
             SinkCategory.SSRF, "ssrf_urllib", ((0, SlotContext.URL),),
             needs_review_default=True),
    SinkRule("ts-fetch", ("typescript",), "fetch", None,
             SinkCategory.SSRF, "ssrf_fetch", ((0, SlotContext.URL),)),
    SinkRule("ts-axios-get", ("typescript",), "get", re.compile(r"^(axios)$"),
             SinkCategory.SSRF, "ssrf_axios", ((0, SlotContext.URL),)),
    SinkRule("go-http-get", ("go",), "Get", _GO_HTTP,
             SinkCategory.SSRF, "ssrf_http", ((0, SlotContext.URL),)),
    SinkRule("go-http-post", ("go",), "Post", _GO_HTTP,
             SinkCategory.SSRF, "ssrf_http", ((0, SlotContext.URL),)),
    SinkRule("java-httpclient-send", ("java",), "send", _JAVA_HTTP,
             SinkCategory.SSRF, "ssrf_java_http", ((0, SlotContext.URL),)),
    SinkRule("php-curl-exec", ("php",), "curl_exec", None,
             SinkCategory.SSRF, "ssrf_curl", ((0, SlotContext.URL),)),
    SinkRule("php-file-get-contents", ("php",), "file_get_contents", None,
             SinkCategory.SSRF, "ssrf_fgmc", ((0, SlotContext.URL),)),

    # --- SSTI / Template ---
    SinkRule("py-render-template-string", ("python",), "render_template_string", None,
             SinkCategory.TEMPLATE, "ssti_flask", ((0, SlotContext.TEMPLATE_EXPR),)),
    SinkRule("py-jinja-template-render", ("python",), "render", _TEMPLATE_LIKE,
             SinkCategory.TEMPLATE, "ssti_jinja", ((0, SlotContext.TEMPLATE_EXPR),)),

    # --- Code-level XSS (best-effort, needs_review=True) ---
    # Note: `innerHTML` is typically an assignment, not a call. We catch the
    # rare `element.innerHTML(...)` call shape here; assignment-shaped XSS is
    # handled by Spec C / LLM (tree-sitter parsing of assignments varies).
    SinkRule("ts-innerhtml-call", ("typescript",), "innerHTML", None,
             SinkCategory.XSS, "xss_dom", ((0, SlotContext.GENERIC),),
             needs_review_default=True),
    SinkRule("ts-document-write", ("typescript",), "write", re.compile(r"^(document)$"),
             SinkCategory.XSS, "xss_dom", ((0, SlotContext.GENERIC),),
             needs_review_default=True),

    # --- File ---
    SinkRule("php-file-put-contents", ("php",), "file_put_contents", None,
             SinkCategory.FILE, "file_write", ((0, SlotContext.FILE_PATH),)),
    SinkRule("php-include", ("php",), "include", None,
             SinkCategory.FILE, "file_include", ((0, SlotContext.FILE_PATH),),
             needs_review_default=True),
    SinkRule("php-require", ("php",), "require", None,
             SinkCategory.FILE, "file_include", ((0, SlotContext.FILE_PATH),),
             needs_review_default=True),

    # --- Open redirect (needs_review: must combine with param source) ---
    SinkRule("ts-res-redirect", ("typescript",), "redirect", None,
             SinkCategory.REDIRECT, "open_redirect", ((0, SlotContext.URL),),
             needs_review_default=True),
    SinkRule("py-flask-redirect", ("python",), "redirect", None,
             SinkCategory.REDIRECT, "open_redirect", ((0, SlotContext.URL),),
             needs_review_default=True),
)
```

> **关于 SinkType 兼容：** `risk_scorer` 升级后直接用 `SINK_CATEGORY_DANGER_SCORES`（按 `SinkCategory` 查表），不再需要 `category_to_sink_type` 回退到旧 `SinkType`。`SinkType` 枚举本身保留作 risk_scorer 兜底（在无 `sink_call_sites` 参数时用 `classify_sink` 正则返回 `SinkType`）。如果后续 Spec A/C 需要把 `SinkCategory` 映射回 `SinkType`（极不可能，它们直接用新枚举），再单独添加该函数。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_sink_detector.py::TestSinkRuleLibrary -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/code_index/sink_detector.py packages/core/tests/code_index/test_sink_detector.py
git commit -m "feat(sink-detector): add SinkRule dataclass and default rule library (Spec B §4.1.1)

约 50 条规则覆盖 5 语言 x SQL/Command/Deserialization/SSRF/XSS/SSTI/File/Redirect。
receiver_pattern 用 regex 覆盖常见变体（cursor/cnx/conn/db 等）。
Code-level XSS / 动态 sink 默认 needs_review=True。"
```

---

## Task 10: detect_sinks 主算法 + is_entry_hint

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/sink_detector.py`
- Modify: `packages/core/tests/code_index/test_sink_detector.py`

实现 Spec §4.1.2 的核心算法。这是 sink detector 的主逻辑。

**设计要点：** `detect_sinks` 接受一个 `source_provider` 回调来获取每个 block 的源码 bytes。这避免了"detect_sinks 必须重新打开文件"的复杂性——`build_code_index` 已经有源码缓存，直接传给 detect_sinks。测试用 closure 提供固定源码。

- [ ] **Step 1: 写失败测试 — is_entry_hint + detect_sinks 主流程**

在 `packages/core/tests/code_index/test_sink_detector.py` 末尾追加。先添加一个共享 helper：

```python
def _src_provider(src: str):
    """Return a source_provider closure that always returns the same bytes."""
    src_bytes = src.encode("utf-8")
    def _provide(block):
        return src_bytes
    return _provide
```

然后追加测试类：

```python
class TestIsEntryHint:
    def test_function_param_identifier(self):
        from shannon_core.code_index.sink_detector import is_entry_hint
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.py:f:1", file_path="app.py", function_name="f",
            start_line=1, end_line=2, source_code="def f(user_id): pass",
            parameters=["user_id"], language="python",
        )
        assert is_entry_hint("user_id", block) is True

    def test_request_attr_python(self):
        from shannon_core.code_index.sink_detector import is_entry_hint
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.py:f:1", file_path="app.py", function_name="f",
            start_line=1, end_line=2, source_code="", parameters=[], language="python",
        )
        assert is_entry_hint("request.args.get('id')", block) is True
        assert is_entry_hint("request.form['x']", block) is True
        assert is_entry_hint("request.json", block) is True

    def test_request_attr_express(self):
        from shannon_core.code_index.sink_detector import is_entry_hint
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.ts:f:1", file_path="app.ts", function_name="f",
            start_line=1, end_line=2, source_code="", parameters=["req"], language="typescript",
        )
        assert is_entry_hint("req.params.id", block) is True
        assert is_entry_hint("req.body", block) is True
        assert is_entry_hint("req.query.x", block) is True

    def test_literal_not_hint(self):
        from shannon_core.code_index.sink_detector import is_entry_hint
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.py:f:1", file_path="app.py", function_name="f",
            start_line=1, end_line=2, source_code="", parameters=[], language="python",
        )
        assert is_entry_hint("'literal string'", block) is False
        assert is_entry_hint("42", block) is False

    def test_local_var_not_hint(self):
        from shannon_core.code_index.sink_detector import is_entry_hint
        from shannon_core.code_index.models import FuncBlock
        block = FuncBlock(
            id="app.py:f:1", file_path="app.py", function_name="f",
            start_line=1, end_line=2, source_code="", parameters=["x"], language="python",
        )
        # 'data' is not a parameter — not a hint
        assert is_entry_hint("data", block) is False


class TestDetectSinksPython:
    def test_python_cursor_execute_hit(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
        # Build a block with a known cursor.execute call
        src = (
            "def f(user_sql):\n"
            "    cursor.execute(user_sql)\n"
        )
        parser = PythonParser()
        # parse_file needs a real path; tmp_path provides one
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        # Use source_provider to feed bytes back in
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        assert len(sites) == 1
        site = sites[0]
        assert site.callee_name == "execute"
        assert site.callee_receiver == "cursor"
        assert site.category == SinkCategory.SQL
        assert site.rule_id == "py-db-cursor-execute"
        assert len(site.dangerous_slots) == 1
        assert site.dangerous_slots[0].arg_index == 0
        assert site.dangerous_slots[0].slot == SlotContext.SQL_VALUE
        assert site.dangerous_slots[0].expression == "user_sql"
        assert site.dangerous_slots[0].is_entry_hint is True
        assert site.needs_review is False

    def test_python_os_system_hit(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "import os\n"
            "def f(cmd):\n"
            "    os.system(cmd)\n"
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        rules = [s.rule_id for s in sites]
        assert "py-os-system" in rules

    def test_python_subprocess_run_hit(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "import subprocess\n"
            "def f(cmd):\n"
            "    subprocess.run(['ls', cmd])\n"
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        rules = [s.rule_id for s in sites]
        assert "py-subprocess-run" in rules

    def test_python_pickle_loads_hit(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "import pickle\n"
            "def f(blob):\n"
            "    pickle.loads(blob)\n"
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        rules = [s.rule_id for s in sites]
        assert "py-pickle-loads" in rules

    def test_python_render_template_string_hit(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "from flask import render_template_string\n"
            "def f(template_str):\n"
            "    return render_template_string(template_str)\n"
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        rules = [s.rule_id for s in sites]
        assert "py-render-template-string" in rules

    def test_python_requests_get_hit(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "import requests\n"
            "def f(url):\n"
            "    requests.get(url)\n"
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        rules = [s.rule_id for s in sites]
        assert "py-requests-get" in rules
        ssrf_site = next(s for s in sites if s.rule_id == "py-requests-get")
        assert ssrf_site.category == SinkCategory.SSRF
        assert ssrf_site.dangerous_slots[0].slot == SlotContext.URL

    def test_no_false_positive_model_query(self):
        """.query() on non-DB receiver (User.query) must NOT hit SQL rule
        (no receiver pattern match for 'User')."""
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "def f():\n"
            "    return User.query.all()\n"
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        sql_sites = [s for s in sites if s.category == SinkCategory.SQL]
        assert len(sql_sites) == 0

    def test_id_format(self):
        """SinkCallSite.id follows '{file}:{caller_func}:{callee}:{line}:{col}'."""
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "def f(user_sql):\n"
            "    cursor.execute(user_sql)\n"
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        assert len(sites) == 1
        # cursor.execute is on line 2, at column 4 (4-space indent)
        assert sites[0].id == "app.py:f:execute:2:4"

    def test_caller_id_links_back(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.python_parser import PythonParser
        import tempfile, pathlib
        src = (
            "def f(user_sql):\n"
            "    cursor.execute(user_sql)\n"
        )
        parser = PythonParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.py"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        assert sites[0].caller_id == "app.py:f:1"


class TestDetectSinksCrossLanguage:
    def test_ts_eval_hit(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.typescript_parser import TypeScriptParser
        import tempfile, pathlib
        src = (
            "function f(code: string) {\n"
            "    return eval(code);\n"
            "}\n"
        )
        parser = TypeScriptParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.ts"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        rules = [s.rule_id for s in sites]
        assert "ts-eval" in rules

    def test_go_exec_command_hit(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.go_parser import GoParser
        import tempfile, pathlib
        src = (
            "package main\n"
            "import \"os/exec\"\n"
            "func f(cmd string) {\n"
            "    exec.Command(\"sh\", \"-c\", cmd)\n"
            "}\n"
        )
        parser = GoParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.go"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        rules = [s.rule_id for s in sites]
        assert "go-exec-command" in rules

    def test_php_unserialize_hit(self):
        from shannon_core.code_index.sink_detector import detect_sinks
        from shannon_core.code_index.parsers.php_parser import PhpParser
        import tempfile, pathlib
        src = (
            "<?php\n"
            "function f($data) {\n"
            "    return unserialize($data);\n"
            "}\n"
        )
        parser = PhpParser()
        with tempfile.TemporaryDirectory() as td:
            fpath = pathlib.Path(td) / "app.php"
            fpath.write_text(src)
            blocks = parser.parse_file(fpath, pathlib.Path(td))
        sites = detect_sinks(blocks, parser, source_provider=_src_provider(src))
        rules = [s.rule_id for s in sites]
        assert "php-unserialize" in rules
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_sink_detector.py::TestDetectSinksPython -v`
Expected: FAIL — `ImportError: cannot import name 'detect_sinks'`

- [ ] **Step 3: 实现 — 添加 detect_sinks + is_entry_hint + 内部辅助函数**

在 `packages/core/src/shannon_core/code_index/sink_detector.py` 文件末尾追加：

```python
# ===== Detection algorithm =====

from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from shannon_core.code_index.models import FuncBlock
    from shannon_core.code_index.parsers.base import BaseParser


def _build_rule_index(
    rules: tuple[SinkRule, ...],
) -> dict[tuple[str, str], list[SinkRule]]:
    """Index rules by (language, callee) for O(1) lookup."""
    idx: dict[tuple[str, str], list[SinkRule]] = {}
    for r in rules:
        for lang in r.languages:
            idx.setdefault((lang, r.callee), []).append(r)
    return idx


# Module-level cache of the default rule index
_RULE_INDEX: dict[tuple[str, str], list[SinkRule]] = _build_rule_index(DEFAULT_RULES)


def is_entry_hint(expression: str, block: "FuncBlock") -> bool:
    """Lightweight heuristic: does this argument expression come straight from
    a known external input?

    Conservative — only returns True for clear cases:
      - The expression is exactly a function parameter name.
      - The expression starts with `request.` / `req.` (Flask / Express).
      - The expression starts with a PHP superglobal (`$_GET` etc.).

    Anything more complex (data.x, processed_id, ...) returns False. Spec A
    performs the real intraprocedural taint tracking; this is just a hint for
    downstream priority.
    """
    expr = expression.strip()

    # 1) Direct function parameter
    if expr in block.parameters:
        return True

    # 2) request.* / req.* (Flask / Express / similar)
    if expr.startswith("request.") or expr.startswith("req."):
        return True

    # 3) PHP superglobals
    if expr.startswith(("$_GET", "$_POST", "$_REQUEST", "$_COOKIE", "$_FILES")):
        return True

    return False


def detect_sinks(
    blocks: "list[FuncBlock]",
    parser: "BaseParser",
    *,
    source_provider: "Callable[[FuncBlock], bytes | None]",
    rules: tuple[SinkRule, ...] = DEFAULT_RULES,
) -> list[SinkCallSite]:
    """Detect sink call sites across all function blocks.

    Args:
        blocks: FuncBlocks to scan.
        parser: A parser whose iter_calls/destructure_call/extract_arg_expressions
            match the blocks' language.
        source_provider: Callable that returns source bytes for a given block
            (or None to skip). Caller is responsible for caching/reading files.
        rules: Rule library to use (defaults to DEFAULT_RULES).

    Returns:
        List of SinkCallSite in source order. No deduplication — one rule hit
        per call site, multiple rules with same callee can produce multiple
        SinkCallSites for one call (intentional).
    """
    rule_index = (
        _build_rule_index(rules) if rules is not DEFAULT_RULES else _RULE_INDEX
    )
    sites: list[SinkCallSite] = []

    for block in blocks:
        source = source_provider(block)
        if source is None:
            continue

        try:
            call_nodes = list(parser.iter_calls(block, source))
        except Exception:
            continue

        for call in call_nodes:
            try:
                callee, receiver = parser.destructure_call(call)
            except Exception:
                continue
            if not callee:
                continue

            candidates = rule_index.get((block.language, callee), [])
            if not candidates:
                continue

            for rule in candidates:
                if not _rule_matches(rule, receiver):
                    continue

                args = parser.extract_arg_expressions(call, source)
                dangerous = _build_dangerous_slots(rule, args, block)
                site = SinkCallSite(
                    id=_make_id(block, callee, call),
                    caller_id=block.id,
                    callee_name=callee,
                    callee_receiver=receiver,
                    category=rule.category,
                    sink_subtype=rule.sink_subtype,
                    file_path=block.file_path,
                    line=call.line,
                    column=call.column,
                    dangerous_slots=dangerous,
                    rule_id=rule.rule_id,
                    needs_review=rule.needs_review_default,
                )
                sites.append(site)

    return sites


def _rule_matches(rule: SinkRule, receiver: str | None) -> bool:
    """A rule matches if receiver_pattern is None (bare call) or receiver
    matches the pattern (qualified call)."""
    if rule.receiver_pattern is None:
        # Bare-function rule: only matches if there's no receiver.
        return receiver is None
    if receiver is None:
        return False
    return bool(rule.receiver_pattern.match(receiver))


def _build_dangerous_slots(
    rule: SinkRule,
    arg_expressions: list[str],
    block: "FuncBlock",
) -> list[DangerousSlot]:
    slots: list[DangerousSlot] = []
    for idx, slot_ctx in rule.dangerous_slots:
        if idx == -1:  # variadic marker — emit a single hint
            slots.append(DangerousSlot(
                arg_index=-1,
                slot=slot_ctx,
                expression=",".join(arg_expressions),
                is_entry_hint=any(is_entry_hint(a, block) for a in arg_expressions),
            ))
            continue
        if idx < len(arg_expressions):
            expr = arg_expressions[idx]
            slots.append(DangerousSlot(
                arg_index=idx,
                slot=slot_ctx,
                expression=expr,
                is_entry_hint=is_entry_hint(expr, block),
            ))
    return slots


def _make_id(block: "FuncBlock", callee: str, call) -> str:
    """SinkCallSite.id format: '{file}:{caller_func}:{callee}:{line}:{col}'.

    This format is the Spec A contract: TaintFlow.sink_call_site_id must
    match it exactly.
    """
    return (
        f"{block.file_path}:{block.function_name}:{callee}:{call.line}:{call.column}"
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_sink_detector.py -v`
Expected: PASS（全部测试）

如果 column 数字失败（如期望 4 实际 12）：tree-sitter 的 `start_point[1]` 是 0-based **byte** column，对 ASCII 缩进等同 char 列。`def f(x):\\n    cursor.execute(x)` 中 `cursor` 在第 2 行第 4 列（4 个空格缩进后），所以 column=4 是正确的。如果实际是 12，说明 `start_point` 取的是 `execute` 的位置而非 `cursor.execute` 整体——检查 `_iter_call_nodes` 中 `node.start_point` 是否取的是 `call` 节点本身（应该取开始位置，即 `cursor` 的位置）。

- [ ] **Step 5: 确认 Task 9 的旧测试仍通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_sink_detector.py::TestSinkRuleLibrary -v`
Expected: PASS（规则库未改）

- [ ] **Step 6: 提交**

```bash
git add packages/core/src/shannon_core/code_index/sink_detector.py packages/core/tests/code_index/test_sink_detector.py
git commit -m "feat(sink-detector): implement detect_sinks algorithm and is_entry_hint (Spec B §4.1.2)

主算法：遍历 FuncBlock 的 call 节点 → rule index 按 (language, callee)
查找候选 → receiver_pattern 校验 → 命中后产出 SinkCallSite（含 dangerous_slots
+ is_entry_hint）。

is_entry_hint 做浅判断（函数参数、request.*/req.*/PHP 超全局），
作为 Spec A 传播起点的提示，非完整传播。

source_provider 是必传 callback，由 build_code_index 提供源码缓存。"
```

---

## Task 11: 集成进 build_code_index

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py`
- Modify: `packages/core/tests/code_index/test_build_code_index.py`

把 `detect_sinks` 接入主 pipeline，让 `CodeIndex` 自动包含 `sink_call_sites`。

- [ ] **Step 1: 写失败测试 — build_code_index 产出 sink_call_sites**

在 `packages/core/tests/code_index/test_build_code_index.py` 末尾追加：

```python
class TestBuildCodeIndexSinks:
    def test_sink_call_sites_populated(self, tmp_path):
        """Repository with a known Python sink produces SinkCallSite."""
        from shannon_core.code_index import build_code_index
        # Minimal repo with one .py file containing cursor.execute
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text(
            "def f(user_sql):\n"
            "    cursor.execute(user_sql)\n"
        )
        index = build_code_index(str(repo))
        assert len(index.sink_call_sites) >= 1
        sql_sites = [s for s in index.sink_call_sites if s.category.value == "sql"]
        assert len(sql_sites) >= 1

    def test_sink_call_sites_empty_when_no_sinks(self, tmp_path):
        """Repository with no sinks produces empty list."""
        from shannon_core.code_index import build_code_index
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def f(x):\n    return x + 1\n")
        index = build_code_index(str(repo))
        assert index.sink_call_sites == []

    def test_code_index_json_contains_sink_call_sites(self, tmp_path):
        """code_index.json includes sink_call_sites after build + write."""
        from shannon_core.code_index import build_code_index, write_index_files
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text(
            "import os\n"
            "def f(cmd):\n"
            "    os.system(cmd)\n"
        )
        index = build_code_index(str(repo))
        out = tmp_path / "out"
        json_path, _ = write_index_files(index, str(out))
        content = json_path.read_text()
        assert '"sink_call_sites"' in content
        assert '"py-os-system"' in content
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_build_code_index.py::TestBuildCodeIndexSinks -v`
Expected: FAIL — `len(index.sink_call_sites) == 0`（未接入）

- [ ] **Step 3: 实现 — 在 build_code_index 中调用 detect_sinks**

修改 `packages/core/src/shannon_core/code_index/__init__.py` 的 `build_code_index` 函数（约 22-83 行）。

在文件顶部 import 块增加：

```python
from shannon_core.code_index.sink_detector import detect_sinks
```

修改 `build_code_index` 函数，让它在 `resolve_edges` 之后增加 sink 检测，并把结果填入 `CodeIndex`：

```python
def build_code_index(repo_path: str) -> CodeIndex:
    """Build a complete code index for the repository."""
    from shannon_core.models.errors import ErrorCode, PentestError

    repo = Path(repo_path).resolve()
    try:
        language = detect_language(repo)
    except ValueError as exc:
        raise PentestError(
            str(exc),
            category="code_index",
            error_code=ErrorCode.CODE_INDEX_FAILED,
        ) from exc

    logger.info("Detected language: %s", language)

    source_files = discover_source_files(repo, language)
    if not source_files:
        raise PentestError(
            f"No source files found for language '{language}' in {repo}",
            category="code_index",
            error_code=ErrorCode.CODE_INDEX_FAILED,
        )

    parser = get_parser(language)
    if parser is None:
        raise PentestError(
            f"No parser available for language '{language}'",
            category="code_index",
            error_code=ErrorCode.CODE_INDEX_FAILED,
        )

    # Cache: file_path → source bytes (so detect_sinks can read source without re-parsing)
    file_sources: dict[str, bytes] = {}
    all_blocks = []
    all_edges = []
    for file_path in source_files:
        try:
            source = file_path.read_bytes()
            rel = str(file_path.relative_to(repo))
            file_sources[rel] = source
            blocks = parser.parse_file(file_path, repo)
            all_blocks.extend(blocks)

            for block in blocks:
                edges = parser.extract_calls(block, source)
                all_edges.extend(edges)
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", file_path, exc)
            continue

    resolved_edges = resolve_edges(all_edges, all_blocks)

    entry_points = detect_entry_points(all_blocks, language, repo_path=str(repo))

    # Spec B: AST-precise sink detection
    def _provide_source(block):
        return file_sources.get(block.file_path)
    sink_call_sites = detect_sinks(all_blocks, parser, source_provider=_provide_source)
    logger.info("Detected %d sink call sites", len(sink_call_sites))

    return CodeIndex(
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
    )
```

同时在 `build_code_index_with_gitnexus` 的两个分支（GitNexus 可用 / 回退）也补上 `sink_call_sites=sink_call_sites`。最简单做法：在它构造 `index` 之前，从 `base_index` 复用：

修改 `build_code_index_with_gitnexus` 函数的两个 `index = CodeIndex(...)` 块，都加上 `sink_call_sites=base_index.sink_call_sites,`。同样地，`rebuild_call_chains` 末尾构造 `updated` 时也加上：

```python
    updated = CodeIndex(
        repository=index.repository,
        language=index.language,
        total_blocks=index.total_blocks,
        total_entry_points=index.total_entry_points,
        total_chains=len(chains),
        blocks=index.blocks,
        edges=index.edges,
        entry_points=index.entry_points,
        chains=chains,
        file_manifest=index.file_manifest,
        degradation_level=index.degradation_level,
        sink_call_sites=index.sink_call_sites,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_build_code_index.py -v`
Expected: PASS

- [ ] **Step 5: 跑完整 code_index 测试套件确认不破坏**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/ -v`
Expected: PASS（除已知 arrow function 漏报相关）

- [ ] **Step 6: 提交**

```bash
git add packages/core/src/shannon_core/code_index/__init__.py packages/core/tests/code_index/test_build_code_index.py
git commit -m "feat(code-index): wire sink_detector into build_code_index (Spec B §4.3)

build_code_index 在解析 blocks/edges 之后调用 detect_sinks，把 SinkCallSite
填入 CodeIndex.sink_call_sites。file_sources 缓存避免重复读盘。

build_code_index_with_gitnexus 和 rebuild_call_chains 都传递 sink_call_sites
字段，保证 code_index.json 持久化 Spec B 产物。"
```

---

## Task 12: risk_scorer 升级（chain-wide max danger）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/risk_scorer.py`
- Modify: `packages/core/tests/code_index/test_risk_scorer.py`

让 `risk_scorer` 从 chain 上**所有**节点的 SinkCallSite 取最大 danger 分（不只终端函数），降级回退到原 `classify_sink`。

- [ ] **Step 1: 写失败测试 — chain 上中间节点的 sink 也算分**

在 `packages/core/tests/code_index/test_risk_scorer.py` 末尾追加：

```python
class TestChainRiskScoreSinkCallSites:
    def test_score_uses_sink_call_sites_when_present(self):
        """When a chain has SinkCallSite records attached, use their category
        instead of falling back to classify_sink regex."""
        from shannon_core.code_index.parameter_models import (
            SinkCallSite, SinkCategory,
        )
        from shannon_core.code_index.models import CodeIndex

        # middle block has a SQL sink call site
        sql_site = SinkCallSite(
            id="svc.py:query:execute:2:4",
            caller_id="svc.py:query:1",
            callee_name="execute",
            callee_receiver="cursor",
            category=SinkCategory.SQL,
            sink_subtype="sql_raw",
            file_path="svc.py",
            line=2,
            column=4,
            dangerous_slots=[],
            rule_id="py-db-cursor-execute",
        )

        blocks = {
            "app.py:handler:1": _block("handler", "app.py", 1),
            "svc.py:query:1": _block("query", "svc.py", 1, source="def query(): pass"),
        }
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:query:1"],
            depth=1, has_unresolved=False,
        )

        score = ChainRiskScore.score(
            chain, blocks, [], set(),
            sink_call_sites=[sql_site],
        )
        assert score.sink_danger == 10  # SQL = 10

    def test_score_picks_max_danger_across_chain(self):
        """Chain has multiple sinks (one SQL, one LOG) — pick the max."""
        from shannon_core.code_index.parameter_models import (
            SinkCallSite, SinkCategory,
        )
        sql_site = SinkCallSite(
            id="svc.py:f:execute:2:4",
            caller_id="svc.py:f:1",
            callee_name="execute",
            callee_receiver="cursor",
            category=SinkCategory.SQL,
            sink_subtype="sql_raw",
            file_path="svc.py",
            line=2,
            column=4,
            dangerous_slots=[],
            rule_id="py-db-cursor-execute",
        )
        log_site = SinkCallSite(
            id="app.py:handler:info:3:4",
            caller_id="app.py:handler:1",
            callee_name="info",
            callee_receiver="logger",
            category=SinkCategory.LOG,
            sink_subtype="log_info",
            file_path="app.py",
            line=3,
            column=4,
            dangerous_slots=[],
            rule_id="py-log-info",
        )

        blocks = {
            "app.py:handler:1": _block("handler", "app.py", 1),
            "svc.py:f:1": _block("f", "svc.py", 1),
        }
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:f:1"],
            depth=1, has_unresolved=False,
        )

        score = ChainRiskScore.score(
            chain, blocks, [], set(),
            sink_call_sites=[sql_site, log_site],
        )
        assert score.sink_danger == 10  # SQL wins over LOG

    def test_score_falls_back_when_no_sink_call_sites(self):
        """Legacy mode: no SinkCallSite → fall back to classify_sink regex."""
        blocks = {
            "app.py:handler:1": _block("handler", "app.py", 1),
            "svc.py:query:1": _block("query", "svc.py", 1,
                                       source="def query(sql): cursor.execute(sql)"),
        }
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:query:1"],
            depth=1, has_unresolved=False,
        )

        # No sink_call_sites passed → backward-compatible behavior
        score = ChainRiskScore.score(chain, blocks, [], set())
        assert score.sink_danger == 10  # classify_sink regex still matches

    def test_score_xss_uses_template_render_score(self):
        """XSS category maps to TEMPLATE_RENDER (closest legacy fit)."""
        from shannon_core.code_index.parameter_models import (
            SinkCallSite, SinkCategory,
        )
        xss_site = SinkCallSite(
            id="app.ts:f:innerHTML:1:0",
            caller_id="app.ts:f:1",
            callee_name="innerHTML",
            callee_receiver=None,
            category=SinkCategory.XSS,
            sink_subtype="xss_dom",
            file_path="app.ts",
            line=1,
            column=0,
            dangerous_slots=[],
            rule_id="ts-innerhtml-call",
            needs_review=True,
        )
        blocks = {
            "app.ts:f:1": _block("f", "app.ts", 1),
        }
        chain = CallChain(
            entry_point_id="app.ts:f:1",
            path=["app.ts:f:1"],
            depth=0, has_unresolved=False,
        )

        score = ChainRiskScore.score(
            chain, blocks, [], set(),
            sink_call_sites=[xss_site],
        )
        # TEMPLATE_RENDER = 7
        assert score.sink_danger == 7
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_risk_scorer.py::TestChainRiskScoreSinkCallSites -v`
Expected: FAIL — `TypeError: score() got an unexpected keyword argument 'sink_call_sites'`

- [ ] **Step 3: 实现 — 升级 ChainRiskScore.score**

修改 `packages/core/src/shannon_core/code_index/risk_scorer.py`。首先在顶部 import 增加：

```python
from shannon_core.code_index.parameter_models import (
    SinkCallSite, SinkCategory, SinkType, TaintFlow,
)
```

（保留 `classify_sink` 的 import，作为 fallback。`SinkCategory` 直接用于新增的 `SINK_CATEGORY_DANGER_SCORES` 字典，无需 `category_to_sink_type` 转回 SinkType。）

然后在 `SINK_DANGER_SCORES` 之后新增 category-based scores：

```python
# Sink danger scores by category (Spec B). Falls back to legacy SINK_DANGER_SCORES
# via category_to_sink_type() when a category is not explicitly mapped here.
SINK_CATEGORY_DANGER_SCORES: dict[SinkCategory, int] = {
    SinkCategory.SQL: 10,
    SinkCategory.COMMAND: 10,
    SinkCategory.DESERIALIZATION: 9,
    SinkCategory.FILE: 8,
    SinkCategory.TEMPLATE: 7,
    SinkCategory.SSRF: 6,
    SinkCategory.XSS: 7,        # code-level XSS, treated same as template render
    SinkCategory.REDIRECT: 5,
    SinkCategory.LOG: 3,
}
```

修改 `ChainRiskScore.score` 方法签名 + 实现：

```python
    @classmethod
    def score(
        cls,
        chain: CallChain,
        blocks_by_id: dict[str, FuncBlock],
        taint_flows: list[TaintFlow],
        auth_middleware_ids: set[str],
        sink_call_sites: list[SinkCallSite] | None = None,
    ) -> "ChainRiskScore":
        """Score a call chain based on its risk characteristics.

        Args:
            sink_call_sites: Spec B output. If provided, used to compute
                sink_danger as the max danger across all chain nodes that
                contain a SinkCallSite. If None or empty, falls back to
                legacy classify_sink regex on the terminal node.
        """
        chain_id = "→".join(chain.path[:4])  # Truncate for display

        sink_danger = cls._compute_sink_danger(
            chain, blocks_by_id, sink_call_sites,
        )

        # Taint completeness: how many flows reach the sink
        sink_node_id = chain.path[-1] if chain.path else None
        reaching = [f for f in taint_flows if f.sink_func_id == sink_node_id]
        taint_completeness = min(10, len(reaching) * 10)

        # Auth gap: does the chain pass through auth middleware?
        chain_has_auth = any(
            node_id in auth_middleware_ids for node_id in chain.path
        )
        auth_gap = 0 if chain_has_auth else 8

        # Depth: call chain length
        depth = min(10, len(chain.path))

        score = cls(
            chain_id=chain_id,
            sink_danger=sink_danger,
            taint_completeness=taint_completeness,
            auth_gap=auth_gap,
            depth=depth,
        )
        logger.debug("Scored chain %s: sink=%d taint=%d auth=%d depth=%d total=%d tier=%d",
                     chain_id, sink_danger, taint_completeness, auth_gap, depth,
                     score.total, score.tier)
        return score

    @staticmethod
    def _compute_sink_danger(
        chain: CallChain,
        blocks_by_id: dict[str, FuncBlock],
        sink_call_sites: list[SinkCallSite] | None,
    ) -> int:
        """Spec B upgrade: take max danger across all chain nodes' SinkCallSites.
        Falls back to classify_sink on terminal node if no SinkCallSites."""
        if sink_call_sites:
            chain_node_ids = set(chain.path)
            dangers = [
                SINK_CATEGORY_DANGER_SCORES.get(s.category, 0)
                for s in sink_call_sites
                if s.caller_id in chain_node_ids
            ]
            if dangers:
                return max(dangers)

        # Fallback: legacy classify_sink on terminal node
        sink_node_id = chain.path[-1] if chain.path else None
        if sink_node_id:
            sink_block = blocks_by_id.get(sink_node_id)
            if sink_block:
                sink_type = classify_sink(sink_block)
                return SINK_DANGER_SCORES.get(sink_type, 0)
        return 0
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shanson-py && uv run pytest packages/core/tests/code_index/test_risk_scorer.py -v`
Expected: PASS（全部新旧测试）

- [ ] **Step 5: 跑下游 tiered_audit 测试确认不破坏**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_tiered_audit.py packages/core/tests/code_index/test_audit_input_builder.py -v`
Expected: PASS（新参数有默认值 None，不破坏旧调用）

- [ ] **Step 6: 提交**

```bash
git add packages/core/src/shannon_core/code_index/risk_scorer.py packages/core/tests/code_index/test_risk_scorer.py
git commit -m "feat(risk-scorer): use SinkCallSite danger across chain (Spec B §4.4)

新参数 sink_call_sites 让 score() 取 chain 上所有节点的最大 danger。
SINK_CATEGORY_DANGER_SCORES 按 SinkCategory 直接查表，避免 SinkType 兜底。
无 sink_call_sites 时降级到 classify_sink 正则（终端函数），保留兼容。

修复 plan-b 留下的'chain 中间节点 sink 被忽略'缺陷。"
```

---

## Task 13: classify_sink 兼容性标注 + 端到端集成测试

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/taint_propagator.py`
- Create: `packages/core/tests/code_index/test_sink_detector_integration.py`

最后一步：标注 `classify_sink` 为 deprecated（不删，保留兼容），并写一个端到端集成测试覆盖完整管线。

- [ ] **Step 1: 写端到端集成测试 — 单语言仓库覆盖全管线**

> **注意：** `build_code_index` 只选一个主语言（按文件数最多者）。`detect_language` 在
> 文件数并列时不稳定，所以**每个 fixture 用单语言仓库**避免 flaky 测试。多语言
> 覆盖通过多个 fixture 实现。

创建 `packages/core/tests/code_index/test_sink_detector_integration.py`：

```python
"""End-to-end integration tests for Spec B sink detector.

Covers: build_code_index → detect_sinks → sink_call_sites in code_index.json
        → risk_scorer uses SinkCallSite (chain-wide max danger).
"""
import json
from pathlib import Path

import pytest

from shannon_core.code_index import build_code_index, write_index_files
from shannon_core.code_index.models import CallChain
from shannon_core.code_index.parameter_models import SinkCategory
from shannon_core.code_index.risk_scorer import ChainRiskScore


@pytest.fixture
def python_repo(tmp_path) -> Path:
    """Python repo with SQL / Command / SSTI sinks."""
    repo = tmp_path / "pyrepo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "import os\n"
        "def handler(user_input):\n"
        "    cursor.execute(user_input)\n"
        "    os.system('echo ' + user_input)\n"
        "    return render_template_string(user_input)\n"
    )
    return repo


@pytest.fixture
def typescript_repo(tmp_path) -> Path:
    """TypeScript repo with eval + document.write sinks."""
    repo = tmp_path / "tsrepo"
    repo.mkdir()
    (repo / "service.ts").write_text(
        "function processInput(input: string) {\n"
        "    eval(input);\n"
        "    document.write(input);\n"
        "}\n"
    )
    return repo


class TestEndToEndPython:
    def test_python_sinks_detected(self, python_repo):
        index = build_code_index(str(python_repo))
        # Should detect at least: SQL (cursor.execute), Command (os.system), SSTI
        rules = {s.rule_id for s in index.sink_call_sites}
        assert "py-db-cursor-execute" in rules
        assert "py-os-system" in rules
        assert "py-render-template-string" in rules

    def test_code_index_json_serializes_sink_call_sites(self, python_repo, tmp_path):
        index = build_code_index(str(python_repo))
        out = tmp_path / "out"
        json_path, _ = write_index_files(index, str(out))
        data = json.loads(json_path.read_text())
        assert "sink_call_sites" in data
        assert len(data["sink_call_sites"]) >= 3

    def test_sink_call_site_id_format_in_json(self, python_repo, tmp_path):
        index = build_code_index(str(python_repo))
        out = tmp_path / "out"
        json_path, _ = write_index_files(index, str(out))
        data = json.loads(json_path.read_text())
        ids = [s["id"] for s in data["sink_call_sites"]]
        # All ids follow "{file}:{caller_func}:{callee}:{line}:{col}"
        for sid in ids:
            parts = sid.split(":")
            assert len(parts) == 5, f"Bad id format: {sid}"

    def test_risk_scorer_uses_sink_call_sites(self, python_repo):
        """Verify risk_scorer correctly consumes sink_call_sites from index."""
        index = build_code_index(str(python_repo))
        # Build a simple chain: handler → (handler itself is the sink caller)
        handler_block = next(b for b in index.blocks if b.function_name == "handler")
        chain = CallChain(
            entry_point_id=handler_block.id,
            path=[handler_block.id],
            depth=0, has_unresolved=False,
        )
        score = ChainRiskScore.score(
            chain,
            {b.id: b for b in index.blocks},
            [], set(),
            sink_call_sites=index.sink_call_sites,
        )
        # Handler has SQL + Command + SSTI in body → max danger = 10
        assert score.sink_danger == 10


class TestEndToEndTypeScript:
    def test_typescript_sinks_detected(self, typescript_repo):
        index = build_code_index(str(typescript_repo))
        rules = {s.rule_id for s in index.sink_call_sites}
        assert "ts-eval" in rules
        assert "ts-document-write" in rules

    def test_needs_review_propagated(self, typescript_repo):
        """document.write is needs_review_default=True."""
        index = build_code_index(str(typescript_repo))
        xss_sites = [s for s in index.sink_call_sites if s.category == SinkCategory.XSS]
        assert len(xss_sites) >= 1
        assert all(s.needs_review for s in xss_sites)


class TestFalsePositives:
    def test_commented_sink_not_detected(self, tmp_path):
        """Sink in a comment must not trigger a hit (tree-sitter skips comments)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text(
            "# cursor.execute(user_sql)  -- this is commented out\n"
            "def f():\n"
            "    pass\n"
        )
        index = build_code_index(str(repo))
        rules = {s.rule_id for s in index.sink_call_sites}
        # The commented execute is not in a function body, so won't be visited
        # at all. But just to be safe, no SQL hit expected.
        assert "py-db-cursor-execute" not in rules

    def test_variable_named_query_not_hit(self, tmp_path):
        """A variable named `query` (not a call) must not match the SQL rule."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text(
            "def f():\n"
            "    query = 'SELECT * FROM users'  # assignment, not call\n"
            "    return query\n"
        )
        index = build_code_index(str(repo))
        rules = {s.rule_id for s in index.sink_call_sites}
        assert "py-db-cursor-execute" not in rules
```

- [ ] **Step 2: 运行测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/test_sink_detector_integration.py -v`
Expected: PASS（全部测试）

- [ ] **Step 3: 标注 classify_sink 为 deprecated（不删除）**

修改 `packages/core/src/shannon_core/code_index/taint_propagator.py`，在文件顶部模块 docstring 之后追加 deprecation 提示，并在 `classify_sink` 函数 docstring 中标注：

```python
"""Sink classification for taint analysis.

Provides heuristic sink detection for common dangerous function patterns.

DEPRECATED: This module is superseded by sink_detector.detect_sinks (Spec B),
which produces precise SinkCallSite records via tree-sitter AST. classify_sink
is retained as a regex-based fallback used by risk_scorer when no
SinkCallSite records are available (e.g., before Spec B wiring completed).
"""
```

并在 `classify_sink` 函数 docstring 中加：

```python
def classify_sink(block: FuncBlock) -> SinkType:
    """[DEPRECATED: use sink_detector.detect_sinks] Classify a function block's
    sink type based on source code patterns.

    Used by risk_scorer as the regex fallback when no SinkCallSite records
    are passed to ChainRiskScore.score().
    """
    # ... implementation unchanged
```

- [ ] **Step 4: 跑全量测试套件确认无回归**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/code_index/ -v`
Expected: PASS（全部测试）

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/ -v -x`
Expected: PASS（全量不破坏）

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/code_index/taint_propagator.py packages/core/tests/code_index/test_sink_detector_integration.py
git commit -m "test(sink-detector): end-to-end integration tests + mark classify_sink deprecated

集成测试覆盖：多语言多 sink 仓库 → build_code_index → sink_call_sites
进 code_index.json → risk_scorer 用 SinkCallSite 取 max danger。
回归测试覆盖：commented sink、变量名 sink 不误报。

classify_sink 文档加 DEPRECATED 标注，但函数签名不变（risk_scorer 兜底用）。"
```

---

## 验收清单

完成本计划后，以下条件全部满足：

- [ ] `packages/core/src/shannon_core/code_index/parameter_models.py` 包含 `SinkCallSite`、`DangerousSlot`、`SlotContext`、`SinkCategory` 四个新模型；`SinkType` 仍存在。
- [ ] `CodeIndex.sink_call_sites` 字段存在，pydantic 自动序列化进 `code_index.json`。
- [ ] `BaseParser` 新增三个抽象方法 `iter_calls`/`destructure_call`/`extract_arg_expressions`，五种语言 parser 均实现。
- [ ] `sink_detector.detect_sinks` 能在给定 FuncBlock 列表 + parser + source provider 的情况下，按规则库产出精确的 SinkCallSite 列表。
- [ ] 规则库覆盖 5 语言 × SQL/Command/Deserialization/SSRF/XSS/SSTI/File/Redirect 共约 50 条规则，`needs_review_default=True` 正确应用在动态/模板类 sink。
- [ ] `build_code_index` 集成 sink_detector，`code_index.json` 含 `sink_call_sites` 字段。
- [ ] `risk_scorer.ChainRiskScore.score` 接受可选 `sink_call_sites` 参数；有则取 chain-wide max danger，无则降级回退到 `classify_sink`。
- [ ] 端到端集成测试通过：多语言 repo、误报用例（commented sink / 变量名 sink）、`code_index.json` 序列化格式、id 格式 `"{file}:{caller_func}:{callee}:{line}:{col}"`。
- [ ] 全量 `pytest packages/core/tests/code_index/` 通过，无回归。
- [ ] `classify_sink` 标注为 DEPRECATED 但函数签名不变（兼容）。

---

## 不在本计划范围内

按 spec §1.3 / §5：

- **不替换 LLM 最终判定**。slot 上下文匹配、concat-after-sanitize 检测仍由 LLM（Spec C 负责）。
- **不解析模板文件**（`.ejs/.hbs/.vue`）。XSS 模板转义指令仍由 LLM。
- **不修复 TypeScript arrow function 漏报**（`extract_calls` 当前只覆盖 `function_declaration/method_definition`，arrow function 内的 sink 检测不到）。这是独立 bug，本 spec 不动。
- **不实现 Spec A 的 TaintFlow 升级**。本计划只产出 `SinkCallSite`；`TaintFlow.sink_call_site_id` 等字段由 Spec A 在它的实施计划中实现。
- **不做 receiver 类型推断**。`x.execute()` 中 x 类型未知时，规则按 receiver 文本匹配；不命中则不产出（动态调用留给 LLM）。
- **不扩展规则库到 100% 覆盖**。本计划提供起始全集（~50 条）；附录 A 的 13 类 SSRF 子类等深度覆盖作为运维任务，按需新增。

---

## 与其他 spec 的衔接

| 接口 | 给谁 | 在本计划哪里实现 |
|---|---|---|
| `SinkCallSite` 模型 | Spec A 消费（传播终点）/ Spec C 消费（LLM 摘要） | Task 1 |
| `SinkCallSite.id` 格式 `"{file}:{caller_func}:{callee}:{line}:{col}"` | Spec A 的 `TaintFlow.sink_call_site_id` 必须用此格式 | Task 10 (`_make_id`) |
| `SlotContext` 枚举 | Spec A 的 `TaintFlow.sink_slot` / Spec C 的 prompt slot 词汇 | Task 1 |
| `CodeIndex.sink_call_sites` | Spec A/C 读 `code_index.json` 时取 | Task 2 + Task 11 |
| `dangerous_slots[].arg_index` | Spec A 传播到的 arg_index 必须在 dangerous_slots 范围内 | Task 10 (`_build_dangerous_slots`) |
| `needs_review` | Spec C 对 needs_review=True 提示 LLM 复核 | Task 9 + Task 10 |
