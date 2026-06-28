# Claude SDK 结果处理与可观测性加固设计

> 补齐 shannon-py 在 `claude-agent-sdk` 集成中**系统性遗漏的一块**：`ResultMessage` 结果级元数据（失败语义 + 诊断字段）的处理，以及工具审计在生产路径的接线。

**日期**: 2026-06-09
**相关基线**: [`2026-06-04-claude-sdk-diff-analysis-design.md`](./2026-06-04-claude-sdk-diff-analysis-design.md)（11 维度全量对比）、[`2026-06-04-sdk-parity-alignment`](../plans/2026-06-04-sdk-parity-alignment.md)、[`2026-06-04-sdk-parity-error-handling`](../plans/2026-06-04-sdk-parity-error-handling.md)（均已完成）
**SDK 版本**: 当前 `claude-agent-sdk==0.2.87` → 目标 `0.2.94`

---

## 1. 背景与现状校准

### 1.1 已完成的对齐工作（不重复）

经核查，以下工作均已实现并提交，本次**不涉及**：

- 环境变量透传 `_build_sdk_env`（15 变量，与 TS 完全一致）
- `MessageDispatcher` 事件流处理（assistant/tool_use/tool_result/text/system）
- `ToolAuditLogger` ABC + `NullToolAuditLogger` + `ActivityToolAuditLogger`
- 三层 spending-cap 检测（消息级 / 行为级 / 异常级）
- 错误分类 `classify_error_for_temporal` + `ClaudeRunResult.error_code`
- workflow 错误传播（`failed_agents` + `status="failed"` + `CancelledError`）

### 1.2 现有文档的系统性盲区

现有 `claude-sdk-diff-analysis` 把"消息流处理"维度判为 🟢已对齐，但其判定只覆盖了 `assistant/tool_use/tool_result/text` 等**逐事件**的内容提取，把 `result` 事件仅当成"流结束信号"（`return "complete"`），**完全没读 `ResultMessage` 自身携带的结果级元数据**。

实测 Python SDK 0.2.87 的 `ResultMessage` 字段（来自 `.venv/.../claude_agent_sdk/types.py`）：

```python
class ResultMessage:
    subtype: str               # 含 error_max_turns / error_during_execution / error_max_structured_output_retries
    is_error: bool
    num_turns: int
    stop_reason: str | None    # 非 end_turn = 提前停止/budget 超限
    total_cost_usd: float | None
    usage / model_usage
    result: str | None
    structured_output
    permission_denials: list | None   # deny 规则实际触发记录
    errors: list[str] | None
    api_error_status: int | None      # HTTP 429/500/529（CLI v2.1.110+ emit）
```

而当前 `AnthropicProvider._extract_result`（`providers_anthropic.py:285-294`）**无条件 `success=True`**，上述字段一个都没读。

### 1.3 三处真实增量

| 增量 | 性质 | 现状 | 影响 |
|---|---|---|---|
| **A. ResultMessage 失败语义** | 正确性 bug | `subtype=error_max_turns` 等被当 `success=True` | agent 跑到上限崩溃/中途出错/结构化输出重试耗尽，下游当成功，结果不可信 |
| **B. 诊断字段未利用** | 可观测性 | `stop_reason`/`permission_denials`/`api_error_status` 全丢弃 | 无法诊断提前停止；无法感知 deny 是否真拦截；错误分类靠字符串嗅探而非 HTTP 状态 |
| **C. audit_logger 零接线** | 可观测性 | `ActivityToolAuditLogger` 仅定义，生产路径构造裸 `MessageDispatcher()` | 工具调用审计（tool_start/tool_end/agent_error）在生产环境是 no-op 死代码 |

> diff-analysis 第 5.5 节自己标注"⚠️ 需要确保 dispatcher 正确注入到 Provider"，但从未完成接线——这正是增量 C。

---

## 2. 目标与非目标

### 2.1 目标

1. **A**：`ResultMessage.is_error` / `subtype` 正确判定 `success`，使 agent 截断/出错不再被误判成功
2. **B**：利用 `subtype`/`api_error_status` 做精确 `error_code` 分类；持久化 `stop_reason`；记录 `permission_denials`
3. **C**：把 `ActivityToolAuditLogger` 接到生产路径，恢复工具审计
4. **L0**：升级 `claude-agent-sdk` 0.2.87 → 0.2.94，建立干净基线

### 2.2 非目标（YAGNI）

- 不实现 SDK 高级特性（hooks / canUseTool / mcp_servers / system_prompt / resume / fork / partial messages）—— TS 版同样未用，非本次回归
- 不实现 abort/timeout（两边都靠 Temporal `start_to_close_timeout`，非 Py 单独劣势）
- 不重新实现 action plan 已有意排除的项（Layer 4 API 账单错误的独立匹配层、testing/subscription 重试模式、OAuth 流程）
- 不改 `executor.execute()` 现有的 `if not result.success → raise PentestError` 统一失败包装（见 §6.3 失败传播链路）

---

## 3. 方案：分层渐进（A 方案）

四个独立层，每层独立 TDD、独立 commit、可逐层回滚。

| 层 | 内容 | 风险 |
|---|---|---|
| L0 | SDK 升级 0.2.87→0.2.94 + 全量回归 | 低（0.2.x 小版本，预期无 breaking，但需验证） |
| L1 | `MessageDispatcher` 采集 ResultMessage 元数据 | 低（纯新增采集，不改现有事件处理） |
| L2 | `AnthropicProvider` 消费失败语义 + 诊断字段 | 中（改 `_extract_result`/`call()` 的核心判定） |
| L3 | `audit_logger` 依赖注入接线 | 低（4 层签名加参数，机械改动） |

---

## 4. 数据流

```
query() 事件流
   │
   ▼
MessageDispatcher.dispatch()                       ← L1 增强
   ├─ assistant/tool_use/tool_result/text/system → 现有逻辑不变
   └─ ResultMessage → 新增 _handle_result_message():
        采集 is_error/subtype/stop_reason/
              permission_denials/api_error_status/errors
        存入 dispatcher 属性（仍 return "complete"）
   │
   ▼ _execute_query 沿用 monkeypatch 把元数据挂到 final_result
   │  （与现有 collected_text / turn_count / _dispatcher_spending_cap 同模式）
   │
AnthropicProvider.call()                           ← L2 增强
   ├─ _extract_result: 读 is_error/subtype 决定 success；存 stop_reason
   ├─ _detect_result_failure(): subtype/api_error_status 优先映射 error_code+retryable
   │  （与现有 spending-cap Layer 1/2 并列，作为新的失败检测层）
   ├─ api_error_status → 精确 error_code
   └─ stop_reason≠end_turn / permission_denials 非空 → 诊断日志
   │
   ▼ success=False 时（沿用现有链路，无需改 executor）
executor.execute() L79 既有逻辑 → PentestError(retryable=result.retryable)
   → activity → ApplicationFailure → Temporal 重试决策 → workflow 错误传播
```

---

## 5. L0：SDK 升级

### 5.1 步骤

1. `packages/core/pyproject.toml`: `claude-agent-sdk>=0.2.87` → `>=0.2.94`
2. `uv lock --upgrade-package claude-agent-sdk`
3. 核查 0.2.87→0.2.94 changelog，重点关注 `query()` / `ClaudeAgentOptions` / `ResultMessage` 的 API 变更
4. `uv run pytest`（全量）验证无回归
5. 遇 breaking 就地修复，全部通过后单独 commit

### 5.2 验证标准

- 全量测试通过
- `from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage` 及 `from claude_agent_sdk.types import ThinkingConfigAdaptive` 导入正常
- `ResultMessage` 仍含 §1.2 所列字段（升级后字段集只应扩大或不变）

---

## 6. L1：MessageDispatcher 采集 ResultMessage 元数据

### 6.1 改动点

`MessageDispatcher.dispatch()`（`message_dispatcher.py:594-611`）当前对 `ResultMessage` 直接 `return "complete"`。改为先调用新增的 `_handle_result_message(event)` 再返回。

### 6.2 新增 dispatcher 属性

```python
# MessageDispatcher.__init__ 新增
self.result_is_error: bool = False
self.result_subtype: str | None = None
self.stop_reason: str | None = None
self.permission_denials: list | None = None
self.api_error_status: int | None = None
self.result_errors: list[str] | None = None
```

### 6.3 _execute_query 挂载

`providers_anthropic.py:_execute_query`（现有 L250-252 已 monkeypatch `collected_text`/`turn_count`/`_dispatcher_spending_cap`）扩展，把上述 6 个属性同样挂到 `final_result`，供 `_extract_result`/`call()` 读取。

---

## 7. L2：AnthropicProvider 消费失败语义

### 7.1 max_turns 设置（L2 前置）

`_build_options` 新增 `max_turns`（决策点①）：

```python
max_turns = int(os.getenv("CLAUDE_MAX_TURNS", "200"))
options.max_turns = max_turns
```

**默认 200**：pentest 单 agent（recon/vuln/exploit）正常扫描通常几十轮内完成，200 作为"失控兜底"阈值——撞 200 轮还没完视为真异常。可通过 `CLAUDE_MAX_TURNS` 环境变量调整（对齐 TS 的 `maxTurns=10000` 可设该值）。与 TS 10000 的差异理由：单 agent 成本控制；行为语义一致（高阈值 + 不重试）。

### 7.2 失败判定规则

`_extract_result` 增加判定（在现有 `success=True` 默认值之后）：

```python
# 读取 L1 采集的元数据
is_error = getattr(result_message, "result_is_error", False)
subtype = getattr(result_message, "result_subtype", None)
stop_reason = getattr(result_message, "stop_reason", None)

if is_error or (subtype and subtype.startswith("error_")):
    success = False
else:
    success = True   # 再由 call() 的 spending-cap 三层 + 本层进一步判定
```

### 7.3 error_code 映射表（决策点②：subtype/api_error_status 优先）

新增 `_classify_result_failure(subtype, is_error, api_error_status) -> tuple[str, bool]`，**结构化信号优先**，仅在无结构化信号时退化到 `classify_error_for_temporal`：

| 触发条件 | error_code（类型名） | retryable | 依据 |
|---|---|---|---|
| `subtype=error_max_turns` | `ExecutionLimitError` | **False** | 对齐 TS `error-handling.ts:226` + 现有 classify L172 |
| `subtype=error_during_execution` | `TransientError` | True | 执行中出错，可能 transient |
| `subtype=error_max_structured_output_retries` | `OutputValidationError` | True | 对齐 TS `OUTPUT_VALIDATION_FAILED` |
| `is_error` + `api_error_status=429` | `RateLimitError` | True | |
| `is_error` + `api_error_status` ∈ {500,502,503,529} | `TransientError` | True | server 侧 transient |
| `is_error` + `api_error_status=402` | `BillingError` | True | |
| `is_error` + `api_error_status=401` | `AuthenticationError` | False | 不可恢复 |
| `is_error` + `api_error_status=403` | `PermissionError` | False | 不可恢复 |
| `is_error` 且无 `api_error_status` | 退化 `classify_error_for_temporal(error)` | 按其结果 | 文本兜底 |

> `error_code` 取值与 `classify_error_for_temporal` 返回的类型名字符串一致（如 `"ExecutionLimitError"`），与现有 `_handle_error` 的 `error_code` 字段语义统一。

### 7.4 call() 失败检测层

`call()` 在现有 spending-cap Layer 1（消息级）/ Layer 2（行为级）之外，新增 **result-failure 层**（顺序在 spending-cap 之前，因为结构化失败信号更可靠）。`subtype`/`is_error`/`api_error_status` 在 `call()` 内从 `result_message` 的挂载属性读取（读取方式见 §7.2）：

```python
result = self._extract_result(...)   # _extract_result 内已据 is_error/subtype 设 success

# 新增：result-failure 层（结构化失败信号）
if not result.success:
    error_code, retryable = self._classify_result_failure(subtype, is_error, api_error_status)
    result.error_code = error_code
    result.retryable = retryable
    result.error = result.error or f"SDK result failure: subtype={subtype}, api_error_status={api_error_status}"
    return result

# 既有 Layer 1 / Layer 2 spending-cap 检测（不变）
...
```

### 7.5 诊断字段（决策点③）

- **`stop_reason`**：`ClaudeRunResult` 新增 `stop_reason: str | None = None` 字段；`_extract_result` 写入；`executor.execute()` 映射到 `AgentMetrics`（新增 `stop_reason` 字段）持久化
- **`stop_reason != "end_turn"`** → `logger.warning`（诊断提前停止/budget 超限）
- **`permission_denials` 非空** → `logger.info`（感知 settings.json deny 是否实际拦截工具，验证增量安全机制有效性）

### 7.6 失败传播链路（关键，含验证点）

L2 设定 `ClaudeRunResult(success=False, retryable=X)` 后，沿用现有链路：

```
ClaudeRunResult.retryable
  → executor.execute() L81: PentestError(retryable=result.retryable, error_code=AGENT_EXECUTION_FAILED)
  → activity 捕获 → classify_error_for_temporal(PentestError)
      AGENT_EXECUTION_FAILED → ("AgentExecutionError", error.retryable)   # 继承 retryable
  → ApplicationFailure(type="AgentExecutionError", non_retryable=not retryable)
  → Temporal 重试决策
```

**⚠️ 实现验证点**：需确认 Temporal Python SDK 尊重 `ApplicationFailure(non_retryable=True)` 标志（独立于 `RetryPolicy.non_retryable_error_types` 的 type 列表）。因 `AgentExecutionError` 不在 `NON_RETRYABLE_TYPES` 里，error_max_turns（`retryable=False`）能否真正不重试，依赖此标志生效。

**备选路径**（若验证发现 `non_retryable` 标志不可靠）：让 provider 对结构化失败直接 `raise PentestError(error_code=<对应 ErrorCode>)`，使 classify 映射到 `NON_RETRYABLE_TYPES` 内的 type（如 error_max_turns → 映射到 `ExecutionLimitError` type）。此路径改动较大，仅在验证失败时启用。

---

## 8. L3：audit_logger 生产路径接线

### 8.1 依赖注入路径

`ActivityLogger` 实现已就绪（`TemporalActivityLogger` / `ConsoleActivityLogger` + 工厂）。4 层签名加 `audit_logger` 透传：

```
activity: logger = ActivityLogger 工厂()
  → executor.execute(audit_logger=logger)                              [新增参数]
  → run_claude_prompt(audit_logger=ActivityToolAuditLogger(logger))    [新增参数]
  → AnthropicProvider.call(audit_logger=...)                           [新增参数]
  → _execute_query → MessageDispatcher(audit_logger=...)
```

### 8.2 改动点

- `executor.execute()`: 新增 `audit_logger: ActivityLogger | None = None`，透传给 `run_claude_prompt`
- `runner.run_claude_prompt()`: 新增 `audit_logger` 参数，透传给 `provider.call()`
- `AnthropicProvider.call()`: 新增 `audit_logger` 参数，传给 `_execute_query`
- `_execute_query()`: 用传入的 `audit_logger` 构造 `MessageDispatcher(audit_logger=audit_logger)`（替换现有裸 `MessageDispatcher()`）
- activity 层（`run_agent` 等）：用 `ActivityLogger` 工厂获取实例，传入 `executor.execute(audit_logger=...)`

> 默认 `None` 时仍构造裸 dispatcher（`NullToolAuditLogger`），保持向后兼容；现有单测不受影响。

---

## 9. 关键决策记录

| # | 决策 | 结论 | 依据 |
|---|---|---|---|
| ① | `max_turns` + `error_max_turns` | 设高值（默认 200，`CLAUDE_MAX_TURNS` 可配）+ `error_max_turns → ExecutionLimitError, retryable=False` | 对齐 TS `error-handling.ts:226` + 现有 classify L172。撞高阈值=真异常，不重试省钱 |
| ② | 失败分类路径 | subtype/api_error_status 优先精确映射，绕过 `classify_error_for_temporal`；无结构化信号时退化兜底 | 结构化数据比字符串嗅探可靠 |
| ③ | `stop_reason`/`permission_denials` | `stop_reason` 持久化进 `ClaudeRunResult`+`AgentMetrics`；`permission_denials` 写日志 | 便于事后诊断提前停止；验证 deny 有效性 |
| ④ | SDK 升级时机 | L0 升级先行，遇 breaking 就地修 | 干净基线；新字段/修复可被 L1-L3 直接利用 |
| ⑤ | executor 失败包装 | 不改 `executor.execute()` 的统一 `raise PentestError` | YAGNI；依赖 retryable 透传（见 §7.6 验证点） |

---

## 10. 测试策略

沿用项目 TDD 传统（每层先写失败测试）。所有 SDK 调用 mock `query()` 产出构造的 `ResultMessage`。

### 10.1 各层测试

| 层 | 测试文件 | 关键用例 |
|---|---|---|
| L0 | 全量回归 | 升级后 `pytest` 全绿 |
| L1 | `test_message_dispatcher.py` | `_handle_result_message` 正确采集 6 字段；ResultMessage 仍返回 "complete" |
| L2 | `test_providers.py` | is_error→success=False；subtype 映射表全分支；api_error_status 映射；无结构化信号退化 classify；stop_reason 持久化；spending-cap 三层仍有效（无回归） |
| L3 | `test_providers.py` + activity 测试 | audit_logger 从 activity 透传到 dispatcher；tool_use/tool_result 触发 `log_tool_start/end`；默认 None 时 Null 兜底 |

### 10.2 关键回归断言

- 现有 spending-cap 三层检测测试（`TestSpendingCapDetection`）全部仍通过
- 正常成功路径（无 is_error、subtype=result）仍 `success=True`
- `executor.execute()` 对 `success=False` 仍 raise `PentestError(retryable=...)`

---

## 11. 文件改动清单

| 文件 | 层 | 改动 |
|---|---|---|
| `packages/core/pyproject.toml` | L0 | SDK 版本 `>=0.2.94` |
| `uv.lock` | L0 | 升级锁定 |
| `packages/core/src/shannon_core/agents/message_dispatcher.py` | L1 | 新增 `_handle_result_message` + 6 属性 |
| `packages/core/src/shannon_core/agents/providers_anthropic.py` | L2 | `_build_options` 加 `max_turns`；`_extract_result` 失败判定 + stop_reason；新增 `_classify_result_failure`；`call()` result-failure 层；`_execute_query` 挂载元数据 + audit_logger |
| `packages/core/src/shannon_core/agents/runner.py` | L3 | `run_claude_prompt` 加 `audit_logger` 参数；`ClaudeRunResult` 加 `stop_reason` 字段 |
| `packages/core/src/shannon_core/agents/executor.py` | L3 | `execute()` 加 `audit_logger` 参数；映射 stop_reason 到 AgentMetrics |
| `packages/core/src/shannon_core/models/metrics.py` | L2 | `AgentMetrics` 加 `stop_reason` 字段 |
| `packages/{whitebox,blackbox}/src/.../pipeline/activities.py` | L3 | `run_agent` 等用 ActivityLogger 工厂注入 |
| `packages/core/tests/agents/test_message_dispatcher.py` | L1 | 新增 ResultMessage 采集测试 |
| `packages/core/tests/agents/test_providers.py` | L2/L3 | 失败语义映射 + audit 接线测试 |
| 各 activity 测试 | L3 | audit_logger 透传测试 |

---

## 12. 风险与缓解

| 风险 | 缓解 |
|---|---|
| SDK 升级引入 breaking | L0 单独验证 + 全量回归；遇 breaking 就地修，不阻塞后续 |
| `ApplicationFailure(non_retryable)` 标志不被 Temporal 尊重（§7.6） | 实现时验证；不可靠则启用备选路径（provider 直接 raise 带 ErrorCode 的 PentestError） |
| L2 改 `_extract_result` 影响现有成功路径 | 显式回归断言（§10.2）；spending-cap 三层测试保留 |
| max_turns=200 对极复杂目标过早截断 | `CLAUDE_MAX_TURNS` 可调；错误码 ExecutionLimitError 可观测，便于发现并调参 |

---

*本文档基于 2026-06-09 代码状态。TypeScript Shannon 位于 `/Users/mango/project/shannon-refactor/shannon`，Python 重构版位于本仓库。基线 diff 分析见 [`2026-06-04-claude-sdk-diff-analysis-design.md`](./2026-06-04-claude-sdk-diff-analysis-design.md)。*
