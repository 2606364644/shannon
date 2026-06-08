# Sink Detector: tree-sitter 快筛 + LLM 兜底

**Date:** 2026-06-08
**Status:** Approved
**Phase:** 1 (Python / JS / TS); Go / Java / C-family 延后到独立 spec

## 背景

当前 `packages/core/src/shannon_core/code_index/taint_propagator.py` 的 `classify_sink()` 用 7 条正则在函数源码全文上匹配，存在三个问题：

1. **粒度过粗**：函数级输出（`SinkType` 单值），下游拿不到 sink 调用的精确行号 / 参数位置
2. **正则误报面大**：`execute|query` 之类模式会命中注释、字符串、变量名（如 `executeQuery` 合法函数）
3. **扩展性差**：跨语言（JS 的 `subprocess` 形态和 Python 不同）、新增 sink 类型都要改正则

原 Shannon TS 项目的 sink 识别完全靠 prompt + LLM agent（pre-recon 阶段 Sink Hunter），没有本地静态分析。Shannon-py 想走 **正则快筛 + LLM 补充** 的分层路线，但现有正则层不够支撑这个分工。

## 目标

- 用 tree-sitter AST query 取代正则，把 sink 识别精度提升到 **行级 + 参数位置**
- 保持"LLM 只兜底 UNKNOWN"的分工：tree-sitter 命中即用，未命中且可达才调 LLM
- 输出 schema 统一：tree-sitter 与 LLM 产出同一种 `SinkHit`，下游无需区分来源
- 注册机制保证后续语言（Go / Java / C-family）零核心代码改动

## 非目标

- 不重写 source / sanitizer 识别（仍由现有 parameter 模型负责）
- 不改变漏洞分析 prompt（vuln-injection / vuln-xss / vuln-ssrf 内容不变）
- 不引入跨过程数据流分析（仍是调用链级）
- Phase 1 不支持 Go / Java / C-family（注册机制留好接口，查询文件留给后续 spec）

## 架构

```
                        ┌─────────────────────────────┐
                        │  FuncBlock (existing)        │
                        │  + source_code, file, line,  │
                        │    language                  │
                        └─────────────┬───────────────┘
                                      │
                       (Phase 1: Python/JS/TS)
                                      ▼
                ┌─────────────────────────────────────┐
                │  SinkDetector (new)                  │
                │  ├─ TreeSitterSinkRegistry           │
                │  │   (按语言查表 → 选 query set)      │
                │  ├─ TreeSitterSinkMatcher            │
                │  │   (跑 query, 收集 SinkHit)         │
                │  └─ LLMSinkFallback                  │
                │      (UNKNOWN 兜底)                  │
                └─────────────┬───────────────────────┘
                              │
                              ▼
              ┌────────────────────────────────┐
              │  SinkHit (new model)            │
              │  ├─ func_id: str                │
              │  ├─ sink_type: SinkType         │
              │  ├─ call_line: int              │
              │  ├─ call_text: str              │
              │  ├─ taint_arg_index: int|None   │
              │  └─ source: "tree_sitter"|"llm" │
              └─────────────┬──────────────────┘
                            │
            ┌───────────────┴────────────────┐
            ▼                                ▼
   ┌────────────────┐               ┌─────────────────┐
   │ TaintFlow      │               │ ChainRiskScore  │
   │ (extend)       │               │ (extend)        │
   │ + sink_hits[]  │               │ + use SinkHit   │
   └────────────────┘               │   for danger    │
                                     └─────────────────┘
```

核心契约：
- `SinkDetector.run(block: FuncBlock) -> list[SinkHit]` 是唯一入口
- 一个函数可能产出 0、1 或多个 SinkHit
- 0 个 SinkHit 且函数在 fallback 候选集内 → 走 LLM 兜底
- 下游消费 `TaintFlow.sink_hits[]` 列表，不依赖单个 sink_type 字段

## 组件

### 文件结构

```
packages/core/src/shannon_core/code_index/sinks/
├── __init__.py                  # 导出 SinkDetector, SinkHit, SinkSource
├── models.py                    # SinkHit, SinkSource
├── detector.py                  # SinkDetector 主类
├── registry.py                  # TreeSitterSinkRegistry
├── llm_fallback.py              # LLMSinkFallback
├── parser_utils.py              # error_ratio 测量、大函数截断
└── queries/                     # 每语言一个 .scm 文件
    ├── python.scm
    ├── javascript.scm
    └── typescript.scm

prompts/
└── sink-classify.txt            # LLM 兜底 prompt

packages/core/src/shannon_core/llm/
└── sink_classifier_client.py    # Claude Haiku 4.5 客户端封装
```

### tree-sitter Query 注册机制

`TreeSitterSinkRegistry` 启动时加载各语言的 grammar + query 文件并缓存：

```python
class TreeSitterSinkRegistry:
    def __init__(self):
        self._languages: dict[str, Language] = {}
        self._queries: dict[str, Query] = {}

    def register(self, lang: str, grammar_pkg: str, query_file: str) -> None:
        """启动时调用一次，加载 grammar 和 query。编译失败立即抛出。"""
        ...

    def get(self, lang: str) -> tuple[Language, Query]:
        ...
```

启动注册：

```python
registry = TreeSitterSinkRegistry()
registry.register("python", "tree_sitter_python", "queries/python.scm")
registry.register("javascript", "tree_sitter_javascript", "queries/javascript.scm")
registry.register("typescript", "tree_sitter_typescript", "queries/typescript.scm")
```

后续语言支持只需一行注册 + 一个 `.scm` 文件。

### Query 文件格式

每个 `.scm` 文件按 sink 类型组织。Query 直接编码 `sink_type` 和 `taint_arg_index`，避免写 Python 后处理：

```scheme
;; python.scm（节选）

;; === SQL_EXECUTION ===
(call
  function: (attribute_expression
    attribute: (identifier) @attr (#eq? @attr "execute"))
  arguments: (argument_list
    . (_) @taint_arg))
  ::sink_type "sql_execution"
  ::taint_arg_index 0

;; === COMMAND_EXEC ===
(call
  function: (attribute_expression
    object: (identifier) @obj (#eq? @obj "subprocess")
    attribute: (identifier) @attr (#match? @attr "^(run|call|Popen|check_output)$"))
  arguments: (argument_list) @args)
  ::sink_type "command_exec"
  ::taint_arg_index 0
```

### SinkDetector 主类

```python
class SinkDetector:
    def __init__(self, registry: TreeSitterSinkRegistry,
                 llm: LLMSinkFallback | None = None):
        self._registry = registry
        self._llm = llm
        self._parser = Parser()
        self._fallback_candidates: set[str] = set()

    def set_fallback_candidates(self, func_ids: set[str]) -> None:
        """第二遍评分前由 pipeline 注入。"""
        self._fallback_candidates = func_ids

    def run(self, block: FuncBlock) -> list[SinkHit]:
        hits = self._tree_sitter_match(block)
        if hits:
            return hits
        if block.func_id in self._fallback_candidates and self._llm:
            llm_hit = self._llm.classify(block)
            return [llm_hit] if llm_hit else []
        return []
```

**关键约束**：tree-sitter 命中过就不再调 LLM，避免循环判定（tree-sitter 始终 confidence=1.0，不存在"低置信 tree-sitter 命中"）。

## 数据模型

### SinkHit（新增）

```python
class SinkSource(str, Enum):
    TREE_SITTER = "tree_sitter"
    LLM = "llm"

class SinkHit(BaseModel):
    func_id: str
    sink_type: SinkType
    call_line: int                # 1-based，对应 FuncBlock 源码行
    call_text: str                # 该行源码片段
    taint_arg_index: int | None   # 0-based；None = 整体受污染
    source: SinkSource
    confidence: float = 1.0       # tree_sitter 默认 1.0；LLM 兜底 0.0-1.0
```

### SinkType（不变）

```python
class SinkType(str, Enum):
    SQL_EXECUTION = "sql_execution"
    COMMAND_EXEC = "command_exec"
    DESERIALIZATION = "deserialization"
    FILE_WRITE = "file_write"
    TEMPLATE_RENDER = "template_render"
    HTTP_REQUEST = "http_request"
    LOG_WRITE = "log_write"
    UNKNOWN = "unknown"
```

LLM 输出 `UNKNOWN` 表示 "分析过但确认不是 sink"（区别于"未分析"）。

### TaintFlow 扩展

```python
class TaintFlow(BaseModel):
    entry_point_id: str
    source_param: str
    source_type: ParameterSource
    propagation_steps: list[PropagationStep] = []
    sink_func_id: str = ""

    # 旧字段（保留兼容，deprecated）：
    sink_type: SinkType | None = None

    # 新字段：
    sink_hits: list[SinkHit] = []
```

兼容字段 `sink_type` 在 6 个版本后移除。新代码读 `sink_hits`。

### ChainRiskScore 微调

```python
def _compute_sink_danger(hits: list[SinkHit]) -> int:
    if not hits:
        return 0
    return max(SINK_DANGER_SCORES.get(h.sink_type, 0) * h.confidence for h in hits)
```

`effective_sink_danger = danger * confidence`，让 LLM 低置信结果打折计入。

## 两遍评分流程

```
┌─────────────────────────────────────────────┐
│ 第一遍：纯 tree-sitter                       │
│  1. 对所有 FuncBlock 跑 SinkDetector          │
│  2. 用 ChainRiskScore 算 tier                │
│  3. 记录所有 "UNKNOWN + 被任一调用链经过"      │
│     的函数 → fallback_candidates              │
└─────────────────┬───────────────────────────┘
                  ▼
┌─────────────────────────────────────────────┐
│ 第二遍：LLM 兜底                              │
│  4. 对 fallback_candidates 调 LLM（上限 200）  │
│  5. 用 LLM 结果重算受影响 chain 的 tier       │
│  6. 合并 SinkHit (source=llm) 到 TaintFlow   │
└─────────────────────────────────────────────┘
```

候选集上限 200 函数 / 仓库（可配置）。超过按 "出现在最多调用链 + chain 平均分最高" 排序截断。

## LLM 兜底设计

### 触发条件

- tree-sitter 返回空（无 SinkHit）
- 函数 ID 在 `_fallback_candidates` 集合内
- LLM feature flag 开启（`SHANNON_SINK_LLM_FALLBACK=on`，默认 off）

### 输入

```python
{
  "language": "python",
  "func_id": "user_service.py:getProfile:42",
  "source_code": "<FuncBlock.source_code 原文；当 source_code > 8KB 时截断到 6KB>",
  "context": {
    "callers": ["api.py:get_user_handler"],
    "parameter_sources": ["QUERY_PARAM"]
  }
}
```

### 输出（tool use 强制结构化）

```json
{
  "is_sink": true,
  "sink_type": "sql_execution",
  "call_line": 48,
  "taint_arg_index": 0,
  "confidence": 0.8,
  "rationale": "Builds SQL via f-string, passes to db.query at line 48."
}
```

### 模型选择

`claude-haiku-4-5-20251001`（Claude Haiku 4.5）。单次输入 ~500 tokens，输出 ~80 tokens，单次成本 ~$0.0005。200 函数上限 → 单仓兜底成本 ~$0.10。

### Prompt（`prompts/sink-classify.txt`）

```
<role>
You are a security analyzer. Given a function's source code, decide if it contains
a security-sensitive sink call. A sink is any call that touches:
- SQL execution (execute, query, raw SQL)
- Command execution (os.system, subprocess, exec, eval)
- Deserialization (pickle.loads, yaml.load, unserialize)
- File write (open in write mode, file_put_contents)
- Template render (render_template, innerHTML assignment)
- HTTP request (requests.get/post, fetch, urllib)
- Log write of user input (logger.info with tainted data)

If unsure, lean toward "is_sink: false". Your job is to catch what regex misses,
not to inflate coverage.
</role>

<input>
Language: {{LANGUAGE}}
Function: {{FUNC_ID}}
Caller context: {{CALLERS}}
Parameter sources: {{PARAM_SOURCES}}

Source code:
```
{{SOURCE_CODE}}
```
</input>

<output>
Call the `classify_sink` tool with your verdict.
</output>
```

### 缓存

按 `(model_version, source_code_hash)` 缓存到 `.shannon/cache/sink_llm_<hash>.json`。
- 模型升级自动失效
- 函数未改动 → 直接读缓存

### 失败处理

- LLM 超时 / API 错误 → 函数保持 UNKNOWN，不阻塞管线
- 输出格式错误 → tool use 自动重试 2 次；最终失败 → 保持 UNKNOWN
- 单仓失败数 > 20% → 报警日志（WARN）

## 边界情况

| 场景 | 处理 |
|---|---|
| tree-sitter 解析错误率 > 30% | 跳过 tree-sitter，直接走 LLM 兜底 |
| query 文件编译失败 | fail-fast（启动报错） |
| 语言未注册 | 返回空 SinkHit → 走 LLM 兜底 |
| `source_code > 8KB` | tree-sitter 正常跑；LLM 输入截断到 6KB |
| 单函数命中 > 20 | 截断到前 20，记 WARN 日志（query 质量护栏） |
| LLM 置信度 < 0.5 | SinkHit 仍生成，`effective_danger = danger * confidence` |
| tree-sitter 命中后某 hit 被丢弃 | 不触发 LLM 二次兜底 |

## 可观测性

```
sink_detector_tree_sitter_hits_total{lang,sink_type}
sink_detector_tree_sitter_errors_total{lang}
sink_detector_llm_calls_total
sink_detector_llm_errors_total
sink_detector_llm_low_confidence_total
sink_detector_unknown_functions_total        # 全仓 UNKNOWN 函数总数
sink_detector_llm_fallback_invoked_total     # 实际调用了 LLM 的
```

最后一对指标量化"候选集上限"的成本控制效果。

## 测试策略

### 单元测试分层

```
tests/unit/code_index/sinks/
├── test_models.py              # SinkHit 序列化、字段校验
├── test_registry.py            # 加载、缓存、fail-fast
├── test_detector_python.py     # Python query 命中
├── test_detector_javascript.py # JS query 命中
├── test_detector_typescript.py # TS query 命中
├── test_detector_fallback.py   # 触发条件、不触发条件
├── test_llm_fallback.py        # LLM 输出解析、置信度、缓存、失败容错
└── test_risk_scorer.py         # SinkHit 多命中下的 danger 计算
```

### Query 黄金测试集

每个 sink_type × 每个语言，至少 3 正例 + 2 负例：

```
tests/fixtures/sinks/python/sql_execution/
├── positive_01_cursor_execute.py
├── positive_02_django_raw.py
├── positive_03_sqlalchemy_text.py
├── negative_01_comment.py        # 注释里的 execute，不能命中
└── negative_02_string_literal.py
```

每个 fixture 配 `.expected.yaml`，参数化测试：

```python
@pytest.mark.parametrize("fixture_dir", discover_fixtures("python/sql_execution"))
def test_python_sql_execution_query(registry, fixture_dir):
    source = (fixture_dir / "source.py").read_text()
    block = make_func_block(source, language="python")
    hits = SinkDetector(registry, llm=None).run(block)
    expected = load_expected(fixture_dir)
    assert len(hits) == 1
    assert hits[0].sink_type == expected.sink_type
    assert hits[0].call_line == expected.call_line
    assert hits[0].taint_arg_index == expected.taint_arg_index
```

**纪律**：
- 负例必须断言 `len(hits) == 0`
- 改 `.scm` 文件 CI 跑全套 fixture
- 新语言至少补齐 7 类 × (3+2) = 35 个 fixture

### LLM Fallback 测试

用 `MockLLMClient` 注入期望响应，不依赖真实 API。覆盖：
- `is_sink: true` → SinkHit 生成
- `is_sink: false` → 返回空
- 超时 / 异常 → 返回空，记 error metric
- 非法 JSON → 重试后失败 → 返回空
- 同 func_id 第二次 → 命中缓存
- 低置信度（< 0.5）→ SinkHit 仍生成但 confidence 字段正确

### 集成测试

迷你仓库（5 个函数，混合 Python/JS）验证 tree-sitter 命中 + LLM mock 兜底 + TaintFlow.sink_hits 填入 + ChainRiskScore 算正确 tier。

### 性能基准

`tests/perf/test_sink_detector_bench.py`：
- 1000 funcs 单核吞吐量基线 > 500 funcs/sec（M1）
- < 100 funcs/sec → 性能告警（CI 信息性指标，不阻塞 merge）

### 端到端验证

复用 `tests/e2e/`，跑已知漏洞 mini repo（juice-shop 片段）：
- 关键 sink 出现在 findings 输出里
- 改动前后报告 diff，sink 召回率不下降

### 覆盖率门槛

- `sinks/` 模块行覆盖率 ≥ 90%
- query fixture 覆盖 = 7 类 × 3 语言 = 21 组合全部有正负例
- LLM fallback 分支覆盖 = 100%

## 实施顺序

| 步骤 | 内容 | 可验证产物 |
|---|---|---|
| 1 | 加 `SinkHit` / `SinkSource` 模型 + 单测 | 模型可序列化、字段校验通过 |
| 2 | 实现 `TreeSitterSinkRegistry`（仅 Python query）+ Python 黄金 fixture（35 个 = 7 类 × (3 正 + 2 负)） | `test_detector_python.py` 全绿 |
| 3 | 实现 `SinkDetector.run()`（不带 LLM 兜底） | 集成测试：Python 函数能产出 SinkHit |
| 4 | 让 `taint_propagator.classify_sink` 内部走 SinkDetector，保持函数级单 sink 行为 | 现有 risk_scorer 不变，全管线行为一致 |
| 5 | `TaintFlow` 加 `sink_hits`，`risk_scorer` 切到 sink_hits | TaintFlow 双字段共存 |
| 6 | 加 JS / TS query + 黄金 fixture | JS/TS detector 测试全绿 |
| 7 | 实现 `LLMSinkFallback` + sink-classify.txt prompt + mock 测试 | `test_llm_fallback.py` 全绿 |
| 8 | 两遍评分管线集成（worker pipeline / activity） | 集成测试：mock LLM 兜底正确触发 |
| 9 | `audit_input_builder` / `findings_renderer` 切到 sink_hits | E2E：findings 输出含精确行号 |
| 10 | 性能基准 + metrics 接入 | benchmark 报告 + worker /metrics 暴露 |

每步可独立 merge。

## 回滚预案

- 步骤 4 是安全网：LLM 兜底出问题，`classify_sink` 仍能给函数级 sink_type
- LLM 兜底 feature flag `SHANNON_SINK_LLM_FALLBACK` 默认 off，问题可立即关闭
- 步骤 4 之前的状态可直接 revert（无新依赖介入）

## 弃用计划

| 时间 | 动作 |
|---|---|
| 本 spec merge | `classify_sink` 标 deprecated，内部走 SinkDetector |
| +3 个版本 | `TaintFlow.sink_type` 标 deprecated |
| +6 个版本 | 移除 `classify_sink` 和 `TaintFlow.sink_type` |

`warnings.warn(DeprecationWarning)` + CI 警告数趋势监控。

## 后续 spec 衔接

- **Phase 2 (Go + Java)**：`queries/go.scm` + `queries/java.scm` + 黄金 fixture + 启动注册一行
- **Phase 3 (C-family)**：`queries/c.scm` + `queries/cpp.scm` + `queries/c_sharp.scm` + 黄金 fixture + 注册

零核心代码改动，每个语言一个独立 spec。

## 依赖变更

`pyproject.toml`：
- `+ tree-sitter>=0.21`
- `+ tree-sitter-python`
- `+ tree-sitter-javascript`
- `+ tree-sitter-typescript`（ts + tsx grammar）
- `+ anthropic`（升级到支持 tool use 的版本）

每个 grammar ~5MB，总加包体 ~20MB。
