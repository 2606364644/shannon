# Spec B：AST 精确 Sink 识别（替换正则 classify_sink）

> 本 spec 是三件套（B → A → C）的地基。它定义了贯穿三者的共享枢纽数据结构 `SinkCallSite`，Spec A（参数传播）和 Spec C（LLM 消费）都引用它。
>
> 关联 spec：
> - Spec A：`2026-06-09-spec-a-propagation-graph-design.md`（消费 `SinkCallSite` 作为传播终点）
> - Spec C：`2026-06-09-spec-c-llm-consumption-design.md`（把 `SinkCallSite` 摘要喂给 LLM）

---

## 1. 背景与现状

### 1.1 现有 sink 识别的三个缺陷

| 缺陷 | 现状代码 | 后果 |
|---|---|---|
| 正则粗糙 | `taint_propagator.py` 全文 38 行，7 条正则（`query\(`、`open\(`、`exec\s*\(`…）扫 `FuncBlock.source_code` | 误报高：`.query()`、注释里的 `query`、`execute` 出现在变量名都会命中；漏报：`cursor.execute` 这类 qualified call 靠泛 `execute` 勉强命中但丢失 receiver 上下文 |
| 函数粒度，非调用点粒度 | `risk_scorer.py:69-75` 只检查 `chain.path[-1]`（调用链终端函数）是否含 sink | sink 若在调用链中间函数被忽略；一个函数含多个 sink 调用无法区分 |
| 丢弃调用点参数 | `python_parser.py:111-123` 的 `_extract_call_edges` 遍历了 AST `call` 节点，但只取 `callee_name`，丢弃实参表达式 | 无法支持下游（A 的传播、LLM 的研判）知道"污染到达哪个参数槽" |

### 1.2 classify_sink 的真实消费者

grep 全仓库，`classify_sink` 在生产代码中**仅**被 `risk_scorer.py:74` 调用（给调用链终端函数打 `sink_danger` 分）。它不参与最终漏洞判定——判定仍 100% 由 LLM 完成。因此本 spec 的替换是低风险的：影响的只是风险打分的输入精度，打分变准只会让 tiered audit 排序更好。

### 1.3 不做的事

- **不替换 LLM 的最终判定**。slot 上下文匹配、concat-after-sanitize 失效检测仍由 LLM（Spec C 保持）。本 spec 只把"sink 调用点 + 危险参数槽"这件事从模糊正则变成精确 AST 事实。
- **不解析模板文件**。tree-sitter 不解析 `.ejs/.hbs/.vue`。XSS 模板转义指令（`<%- %>` vs `<%= %>`）仍由 LLM 处理，本 spec 只在能确定性判定的 code-level 输出 sink（如 `innerHTML=`、`document.write`）上 best-effort 标注。

---

## 2. 目标

1. 用 tree-sitter AST 在**调用点粒度**精确识别 sink，产出结构化的 `SinkCallSite` 列表。
2. sink 规则库从正则升级为 **qualified-name 匹配**（`receiver.method` 或 `function`），覆盖原始项目 prompt 的完整 sink 目录（injection / SSRF / XSS code-level / deserialization / file 等）。
3. 覆盖 **5 种语言**（Python / TypeScript / Go / Java / PHP）——AST `call` 节点提取在现有 parser 中已存在，本 spec 复用并扩展。
4. 明确**可判定性边界**：能确定性判定的精确定位 + 标 slot；不能的（模板 XSS、动态调用）标 `needs_review=True` 交给 LLM，绝不静默漏报或假精确。
5. 为 Spec A 提供传播终点契约（`SinkCallSite.id` + 危险参数槽），为 Spec C 提供 LLM 可消费的精确事实。

---

## 3. 数据契约（与其他 spec 共享的接口）

### 3.1 新增模型：`SinkCallSite`（本 spec 拥有，定义在 `parameter_models.py`）

```python
class SlotContext(str, Enum):
    """Sink 输入位的安全上下文 —— 呼应原始项目的 slot 类型系统。"""
    SQL_VALUE = "sql_value"            # SQL-val/like/num —— 需参数绑定
    SQL_IDENTIFIER = "sql_identifier"  # SQL-enum/ident —— 需白名单
    CMD_ARGUMENT = "cmd_argument"      # 需数组参数 + shell=False / shlex.quote
    FILE_PATH = "file_path"            # 需白名单路径 / resolve+边界检查
    TEMPLATE_EXPR = "template_expr"    # SSTI —— 需沙箱+autoescape
    URL = "url"                        # SSRF —— 需协议/主机白名单
    DESERIALIZE_OBJ = "deserialize"    # 需可信来源+HMAC
    GENERIC = "generic"               # 未细分

class DangerousSlot(BaseModel):
    """sink 调用中一个需要防御的参数位。"""
    arg_index: int            # 第几个实参（0-based）；-1 表示 variadic/spread 整体
    slot: SlotContext         # 该槽位的安全上下文
    expression: str           # 该实参的源码表达式文本（供 A/LLM 追踪）
    is_entry_hint: bool       # AST 能直接看出该实参源自函数参数/外部输入（浅判断）

class SinkCategory(str, Enum):
    SQL = "sql"
    COMMAND = "command"
    FILE = "file"
    TEMPLATE = "template"
    DESERIALIZE = "deserialization"
    SSRF = "ssrf"
    XSS = "xss"              # 仅 code-level（innerHTML/document.write 等）
    LOG = "log"
    REDIRECT = "redirect"

class SinkCallSite(BaseModel):
    """一次具体的危险函数调用 —— 三 spec 共享枢纽。"""
    id: str                          # "{file}:{caller_func}:{callee}:{line}:{col}"
    caller_id: str                   # 所在 FuncBlock.id
    callee_name: str                 # 方法/函数名，如 "execute"
    callee_receiver: str | None      # receiver，如 "cursor" / "subprocess" / "os"；裸函数为 None
    category: SinkCategory
    sink_subtype: str                   # 细分类型，如 "sql_raw_query" / "ssrf_http_client"（自由串，由规则库定义）
    file_path: str
    line: int
    column: int
    dangerous_slots: list[DangerousSlot]  # 规则库标注的危险参数位 + slot
    rule_id: str                     # 命中的规则 id（可追溯到规则库定义）
    needs_review: bool = False       # best-effort 判定 / 动态调用 / 模板类，需 LLM 复核
```

### 3.2 `SinkType` 兼容

现有 `SinkType` 枚举（`parameter_models.py:16`，7 值）被 `risk_scorer.SINK_DANGER_SCORES` 使用。本 spec **不删除** `SinkType`，而是：
- 新增 `SinkCallSite.category`（`SinkCategory`）作为主分类。
- 在 `sink_detector` 内提供 `category → SinkType` 的降级映射，供 `risk_scorer` 过渡期继续工作；后续可让 `risk_scorer` 直接消费 `SinkCallSite`（见 §6.3）。

### 3.3 `CodeIndex` 新增字段

`models.py` 的 `CodeIndex` 增加：

```python
class CodeIndex(BaseModel):
    ...
    sink_call_sites: list[SinkCallSite] = []   # 新增，Spec B 产出
```

写入 `code_index.json`，供 Spec A 读取（传播终点）、Spec C 读取（喂 LLM）。

### 3.4 `TaintFlow` 升级接口（由 Spec A 实现，本 spec 只定义契约）

`TaintFlow.sink_func_id: str` 将升级为指向 `SinkCallSite`：

```python
class TaintFlow(BaseModel):
    ...
    sink_call_site_id: str          # ← 替换 sink_func_id，指向 SinkCallSite.id
    sink_slot: SlotContext          # ← 新：污染实际到达的槽位上下文
    tainted_arg_index: int          # ← 新：污染到达第几个实参
    confidence: float               # ← 新：传播链可信度
```

> **本 spec 不实现 TaintFlow 升级**，只在 §3.1 的 `SinkCallSite` 里预留 `dangerous_slots`（带 slot + arg_index），使 Spec A 能直接引用。二者通过 `SinkCallSite.id` 关联。
>
> **完整的 `TaintFlow` 字段**（含 `flow_id`、`has_sanitizer_hint`、`notes`、`confidence` 等）由 **Spec A §3.2** 定义并实现；本节仅列出与 `SinkCallSite` 关联的核心字段（`sink_call_site_id` / `sink_slot` / `tainted_arg_index`），作为 Spec A 实现时必须遵守的契约。

---

## 4. 详细设计

### 4.1 新模块：`packages/core/src/shannon_core/code_index/sink_detector.py`

#### 4.1.1 规则库（结构化，可扩展）

```python
# 每条规则 = qualified-name 模式 + 分类 + 危险槽位
@dataclass(frozen=True)
class SinkRule:
    rule_id: str                       # "py-db-cursor-execute"
    languages: tuple[str, ...]         # ("python",) 或多语言
    callee: str                        # 方法/函数名，如 "execute"
    receiver: str | None               # receiver 前缀，如 "cursor" / "subprocess"；None=裸函数
    receiver_pattern: re.Pattern | None # receiver 的正则（覆盖 cursor/cnx/conn/db 等）
    category: SinkCategory
    sink_subtype: str
    dangerous_slots: tuple[tuple[int, SlotContext], ...]  # (arg_index, slot)
    needs_review_default: bool = False
```

规则库覆盖原始 prompt 目录（节选，完整表见附录 A）：

| rule_id | 语言 | callee / receiver | category | sink_subtype | 危险槽 (index, slot) |
|---|---|---|---|---|---|
| py-db-cursor-execute | python | `execute` @ `cursor\|cnx\|conn\|db` | sql | sql_raw | (0, SQL_VALUE) |
| py-db-cursor-executemany | python | `executemany` @ 同上 | sql | sql_raw | (0, SQL_VALUE) |
| py-os-system | python | `system` @ `os` | command | command_shell | (0, CMD_ARGUMENT) |
| py-subprocess-run | python | `run/Popen/call` @ `subprocess` | command | command_subprocess | (0, CMD_ARGUMENT) |
| py-pickle-loads | python | `loads/load` @ `pickle` | deserialization | deser_pickle | (0, DESERIALIZE_OBJ) |
| py-yaml-load | python | `load` @ `yaml` | deserialization | deser_yaml | (0, DESERIALIZE_OBJ) |
| py-requests-get | python | `get/post/...` @ `requests` | ssrf | ssrf_http_client | (0, URL) |
| ts-eval | typescript | `eval` (裸) | command | js_eval | (0, GENERIC) |
| ts-innerhtml | typescript | `innerHTML` (赋值) | xss | xss_dom | (0, GENERIC) needs_review |
| go-exec-command | go | `Command` @ `exec` | command | command_exec | (0, CMD_ARGUMENT) |
| java-runtime-exec | java | `exec` @ `Runtime`/`getRuntime` | command | command_exec | (0, CMD_ARGUMENT) |
| php-mysqli-query | php | `query` @ `mysqli`/`$db` | sql | sql_raw | (0, SQL_VALUE) |

#### 4.1.2 检测算法（复用现有 AST call 遍历）

```
detect_sinks(blocks: list[FuncBlock], parser) -> list[SinkCallSite]:
  for each block:
    call_nodes = parser.iter_calls(block)      # 复用/新增：返回 AST call 节点 + 实参节点
    for each call_node:
      (callee, receiver) = parser.destructure_call(call_node)  # 拆出 receiver.method
      for each rule where rule.callee == callee and rule.languages ∋ block.language:
        if rule.receiver is None or rule.receiver_pattern matches receiver:
          args = parser.extract_arg_expressions(call_node)     # 每个实参的源码文本
          dangerous = [DangerousSlot(idx, slot, args[idx], is_entry_hint(args[idx], block))
                       for (idx, slot) in rule.dangerous_slots if idx < len(args)]
          emit SinkCallSite(...)
```

关键：`is_entry_hint` 做浅判断——实参是否直接是函数参数标识符、`request.xxx`、`req.body.y` 等已知入口形态。这给 Spec A 一个传播起点提示，但**不做完整传播**（那是 A 的职责）。

#### 4.1.3 各语言 parser 扩展

现有 parser（`parsers/*.py`）需新增两个方法（`BaseParser` 扩接口）：

```python
class BaseParser(ABC):
    def iter_calls(self, block: FuncBlock, source: bytes) -> Iterator[CallNode]: ...
    def destructure_call(self, call) -> tuple[str, str | None]: ...   # (callee, receiver)
    def extract_arg_expressions(self, call, source: bytes) -> list[str]: ...
```

- **Python**：复用 `python_parser._extract_call_edges` 的 `call` 节点遍历；`function` 字段是 `identifier`（裸函数）或 `attribute`（`receiver.method`，`object` 字段取 receiver 文本，`attribute` 取方法名）。
- **TypeScript**：`call_expression` 节点，`function` 字段同理（`member_expression` 取 property + object）。
- **Go**：`call_expression`，`function` 为 `selector_expression`（`x.Y`）或 `identifier`。
- **Java**：`method_invocation`（含 `object` + `name`）/ `object_creation_expression`。
- **PHP**：`method_call_expression` / `function_call_expression`。

五种语言的 call 节点结构不同，但提取模式一致（callee + receiver + arguments）。每个 parser 各加 ~20 行。

### 4.2 可判定性边界（诚实标注 `needs_review`）

| 情形 | 处理 | needs_review |
|---|---|---|
| 静态 qualified call（`cursor.execute(sql)`） | 精确识别 + 标 slot | False |
| 动态调用（`getattr(obj, name)()`、`obj[var]()`） | 无法静态判定 callee | 不产出（交给 LLM） |
| 字符串拼出的 sink 名 | 同上 | 不产出 |
| 模板文件 XSS（`.ejs` 转义指令） | tree-sitter 不解析模板 | 不产出（LLM 负责） |
| code-level DOM XSS（`innerHTML=`、`document.write`） | AST 能识别赋值/调用 | True（best-effort，因转义需语义判断） |
| receiver 不可识别（`x.execute()`，x 类型未知） | 命中规则但 receiver 不确定 | True |

规则不命中 ≠ 安全。`sink_detector` 只产出**确定命中的** SinkCallSite；未覆盖的 sink 由 LLM（Spec C）继续负责。spec 明确这一点，避免"静态没报 = 没漏洞"的误用。

### 4.3 集成进 `build_code_index`

`code_index/__init__.py:build_code_index` 在提取 blocks + edges 后，新增：

```python
sink_call_sites = sink_detector.detect_sinks(all_blocks, parser_registry)
index = CodeIndex(..., sink_call_sites=sink_call_sites)
```

`write_index_files` 自动把 `sink_call_sites` 序列化进 `code_index.json`（pydantic 自动处理）。

### 4.4 `classify_sink` 的去留

- **保留** `taint_propagator.classify_sink`，但标注 deprecated，内部改为：若该 FuncBlock 有对应 `SinkCallSite`，取其 `category`；否则回退正则。
- `risk_scorer.py:74` 的 `sink_danger` 升级为：基于 chain 上**所有**节点的 `SinkCallSite`（而非仅终端函数）取最大 danger 分。这是本 spec 的附带改进，让风险打分反映"链上任意 sink"。

---

## 5. 边界与局限

| 局限 | 说明 | 缓解 |
|---|---|---|
| 动态调用不可判定 | `getattr`/变量索引调用无法静态解析 callee | 交给 LLM（Spec C）；`needs_review` 不产出 |
| receiver 类型推断弱 | `x.execute()` 的 x 类型需类型推断 | receiver 未识别时 `needs_review=True` |
| 模板 sink 不覆盖 | tree-sitter 不解析模板 | LLM 负责（与原始项目一致） |
| 规则库维护成本 | 新增 sink 类别需加规则 | 规则库结构化（dataclass + 表），低门槛扩展；附录 A 是起始全集 |
| 不判定漏洞 | SinkCallSite 是"事实"非"结论" | slot/sanitizer 判定由 Spec A 标注 + LLM 复核 |

---

## 6. 测试策略

### 6.1 单元测试（`packages/core/tests/code_index/test_sink_detector.py`）

每语言一个 fixture 文件，断言：
- 精确调用点位置（file:line:col）。
- receiver + callee 拆分正确。
- `dangerous_slots` 的 arg_index + slot 正确。
- `is_entry_hint` 对 `request.x`、函数参数、字面量分别判对。
- 误报用例：`.query()`（非 DB）、注释里的 `execute`、变量名含 `query` → 不命中。

### 6.2 规则库覆盖测试

每条 `SinkRule` 至少一个正例 + 一个反例 fixture。

### 6.3 集成测试

`build_code_index` 对一个多语言样例仓库产出 `sink_call_sites`，验证：
- `code_index.json` 含 `sink_call_sites` 字段。
- `risk_scorer` 升级后，含中间 sink 的 chain 得到更高 `sink_danger`。

### 6.4 回归

现有 `test_risk_scorer` / `test_taint_propagator` 不破坏（`classify_sink` 保留兼容）。

---

## 7. 与其他 spec 的接口约定

| 接口 | 方向 | 约定 |
|---|---|---|
| `SinkCallSite` 模型 | B 定义 → A/C 引用 | 字段见 §3.1，A/C 不得假设未定义字段 |
| `SinkCallSite.id` 格式 | B 定义 → A 引用 | `"{file}:{caller_func}:{callee}:{line}:{col}"`，A 的 `TaintFlow.sink_call_site_id` 必须用此格式 |
| `SlotContext` 枚举 | B 定义 → A/C 引用 | A 的 `TaintFlow.sink_slot`、C 的 prompt slot 词汇都用此枚举 |
| `CodeIndex.sink_call_sites` | B 写 → A/C 读 | 序列化进 `code_index.json` |
| `dangerous_slots[].arg_index` | B 产出 → A 校验 | A 传播到的 arg_index 必须在 dangerous_slots 范围内才算到达 sink |
| `needs_review` | B 标注 → C 区分 | C 对 `needs_review=True` 的 SinkCallSite 提示 LLM "静态 best-effort，请复核" |

---

## 附录 A：起始规则库全集（按原始 prompt 目录）

> 以下为规则库起点，非穷尽。每类列出代表性规则，实现时按语言补全常见 API 变体。

**SQL**：`cursor/cnx/conn/db.execute|executemany` (py)、`mysqli/pdo.query/prepare-exec` (php)、`Statement.executeQuery` (java)、`db.Query/Exec` (go)、`connection.query` (ts)。

**Command**：`os.system/popen`、`subprocess.run/Popen/call/check_output`、`commands.getoutput` (py)；`child_process.exec/spawn` (ts)；`exec.Command` (go)；`Runtime.exec` (java)；`shell_exec/system/passthru/exec` (php)。

**Deserialization**：`pickle.loads/load`、`yaml.load` (unsafe)、`marshal.loads`、`cPickle` (py)；`unserialize` (php)；`ObjectInputStream.readObject` (java)；`xml.Unmarshal` 的 XXE 变体 (go)。

**File / Path**：`open()` 写模式、`shutil`、`os.remove` (py)；`fs.writeFile` (ts)；`os.OpenFile` (go)；`fopen/file_put_contents/include/require` (php)。

**SSRF（13 子类对齐原始 pre-recon-code.txt:333-415）**：`requests/urllib3/httpx.get/post`、`urllib.urlopen`、`http.client` (py)；`fetch/axios/got` (ts)；`http.Get/Post`、`net.Dial` (go)；`HttpURLConnection`、`OkHttpClient` (java)；`curl_exec/file_get_contents` (php)；headless 浏览器 `playwright/selenium`；媒体处理器 `Pillow/ImageMagick`；JWKS fetcher；importer/cloud metadata（`169.254.169.254`）。

**XSS（code-level only）**：`innerHTML/outerHTML` 赋值、`document.write`、`eval`/`Function`、`insertAdjacentHTML` (ts) —— 全部 `needs_review=True`。

**SSTI**：`render_template_string` (py/flask，区分 `render_template`）、`Template().render` (jinja2)；ejs/jade 的非转义输出由 LLM 处理。

**Open Redirect**：`redirect(url)` / `res.redirect`（需结合参数来源，标 `needs_review`）。

**Log**（低危，仅记录）：`logging.info/debug`、`console.log`、`logger` —— 可配置开关，默认产出但 danger 分最低。
