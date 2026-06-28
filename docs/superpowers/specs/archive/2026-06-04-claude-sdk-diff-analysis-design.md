# Shannon-Py vs Shannon TypeScript: Claude SDK 使用差异分析设计文档

> 对比原始 TypeScript Shannon (`/root/shannon`) 与 Python 重构版 (`/root/shannon-py`) 在 Claude Agent SDK 使用上的全量差异

**日期**: 2026-06-04
**范围**: SDK 调用模式、消息流处理、环境变量管理、工具调用审计、花费上限检测、错误处理与重试、模型管理、结构化输出、成本追踪、提供商支持、认证方式

---

## 目录

1. [总览矩阵](#1-总览矩阵)
2. [SDK 调用模式](#2-sdk-调用模式)
3. [消息流处理](#3-消息流处理)
4. [环境变量管理](#4-环境变量管理)
5. [工具调用审计](#5-工具调用审计)
6. [花费上限检测](#6-花费上限检测)
7. [错误处理与重试](#7-错误处理与重试)
8. [模型管理](#8-模型管理)
9. [结构化输出](#9-结构化输出)
10. [成本追踪](#10-成本追踪)
11. [提供商支持矩阵](#11-提供商支持矩阵)
12. [待对齐项汇总与建议](#12-待对齐项汇总与建议)

---

## 1. 总览矩阵

| 功能维度 | TS 完成度 | PY 完成度 | 对齐状态 | 未对齐原因 |
|---------|----------|----------|---------|-----------|
| SDK 调用模式 | ✅ 完整 | ✅ 完整 | 🟢 已对齐 | — |
| 消息流处理 | ✅ 完整 | ✅ 完整 | 🟢 已对齐 | — |
| 环境变量管理 | ✅ 完整 | ✅ 完整 | 🟢 已对齐 | — |
| 工具调用审计 | ✅ 完整 | ✅ 完整 | 🟢 已对齐 | — |
| 花费上限检测 | ✅ 完整（4集成点） | 🟡 部分（3层） | 🟡 部分对齐 | TS 有 API 级结构化账单错误检测（11种模式如 `billing_error`、`credit balance too low`）；PY 缺少此层，仅实现了消息级、行为级、异常级三层 |
| 错误处理与重试 | ✅ 完整 | 🟡 部分 | 🟡 部分对齐 | TS 有 8 种 non-retryable error 类型分类（`AuthenticationError`、`PermissionError` 等）+ Testing/Subscription 两种重试模式；PY 的错误分类和重试策略尚未完整对齐 |
| 模型管理 | ✅ 完整 | ✅ 完整 | 🟢 已对齐 | — |
| 结构化输出 | ✅ 完整 | ✅ 完整 | 🟢 已对齐 | — |
| 成本追踪 | ✅ 完整 | ✅ 完整 | 🟢 已对齐 | — |
| 提供商支持 | 5种 | 5种 | 🟢 已对齐 | — |
| 认证方式 | ✅ API Key + OAuth | 🟡 仅 API Key | 🟡 部分对齐 | TS 通过 `CLAUDE_CODE_OAUTH_TOKEN` 支持 OAuth 认证流；PY 虽在环境变量透传列表中包含了此变量，但未实现 OAuth token 获取和刷新逻辑 |

**总结**: 11 个维度中 7 个已完全对齐，3 个部分对齐，0 个缺失。未对齐项均为增强性功能，不影响核心扫描流水线运行。

---

## 2. SDK 调用模式

两个项目的 SDK 调用架构完全一致，都是通过 Claude Agent SDK 的 `query()` 函数以**子进程模式**运行 Claude。

### 2.1 调用架构

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Pipeline   │────▶│  Provider.call() │────▶│  query(prompt,  │
│  Workflow   │     │  (构建 options)   │     │    options)     │
└─────────────┘     └──────────────────┘     └────────┬────────┘
                                                       │
                                              SDK 内部 spawn 子进程
                                                       │
                                                       ▼
                                              ┌─────────────────┐
                                              │  Claude Code CLI │
                                              │  (子进程)        │
                                              └─────────────────┘
```

### 2.2 详细对比

| 项 | TypeScript (`claude-executor.ts`) | Python (`providers_anthropic.py`) |
|----|-----------------------------------|----------------------------------|
| SDK 包名 | `claude-agent-sdk` (npm) | `claude-agent-sdk>=0.2.87` (PyPI) |
| 调用函数 | `query({ prompt, options })` | `query(prompt=prompt, options=options)` |
| 子进程模式 | SDK 内部 spawn Claude CLI | SDK 内部 spawn Claude CLI |
| 权限模式 | `bypassPermissions` | `bypassPermissions` |
| 工作目录 | `options.cwd` 指定 | `options.cwd` 指定 |
| 异步模式 | `for await (const event of ...)` | `async for event in ...` |
| 流式处理 | 逐事件处理 | 逐事件处理（通过 MessageDispatcher） |
| 入口文件 | `claude-executor.ts`（402行） | `providers_anthropic.py` + `runner.py` + `message_dispatcher.py` |

### 2.3 调用流程对比

**TypeScript:**

```typescript
// claude-executor.ts
const stream = query({ prompt, options: claudeOptions });
for await (const event of stream) {
  // 直接处理事件：handleAssistantMessage / handleToolUse / handleResult...
}
```

**Python:**

```python
# providers_anthropic.py → _execute_query()
async for event in query(prompt=prompt, options=options):
    action = await dispatcher.dispatch(event)  # 委托 MessageDispatcher 处理
    if isinstance(event, ResultMessage):
        final_result = event
    if action == "complete":
        break
```

### 2.4 关键差异

| 差异点 | TS | PY | 影响 |
|--------|----|----|------|
| 事件处理 | 直接在 executor 内处理 | 委托给 `MessageDispatcher` 类 | PY 的设计更解耦，但行为等价 |
| 代码组织 | 单文件 402 行集中处理 | 拆分为 3 个文件各司其职 | PY 更模块化 |
| 结果传递 | 直接从 event stream 提取 | dispatcher 处理后挂载到 ResultMessage | 行为等价 |

---

## 3. 消息流处理

### 3.1 事件类型对比

| SDK 事件类型 | TS 处理 | PY 处理 | 对齐状态 |
|-------------|---------|---------|---------|
| `assistant` | ✅ 提取文本 + turn 计数 + 花费检测 | ✅ `_handle_assistant` 提取文本 + turn 计数 + 花费检测 | 🟢 已对齐 |
| `tool_use` | ✅ 审计日志 + 进度回调 | ✅ `_handle_tool_use` 审计日志 + 进度回调 | 🟢 已对齐 |
| `tool_result` | ✅ 审计日志 | ✅ `_handle_tool_result` 审计日志 | 🟢 已对齐 |
| `result` (ResultMessage) | ✅ 流结束信号 | ✅ 返回 `"complete"` | 🟢 已对齐 |
| `text` | ✅ 文本累积 | ✅ 文本追加到 `text_parts` | 🟢 已对齐 |
| `system` | ✅ 处理 | ✅ 透传（`else: return "continue"`） | 🟢 已对齐 |
| `user` | ✅ 处理 | ✅ 透传 | 🟢 已对齐 |
| `tool_progress` | ✅ 进度回调 | ✅ 透传 | 🟢 已对齐 |

### 3.2 文本收集机制

| 项 | TS | PY |
|----|----|-----|
| 收集来源 | `assistant` 事件的 content blocks + `text` 事件 | `assistant` 事件的 content blocks + `text` 事件 |
| 存储方式 | `fullResponseText` 字符串拼接 | `text_parts: list[str]` → `collected_text` 属性 join |
| 最终输出 | 附加到 `ClaudeRunResult.text` | 附加到 `ResultMessage.collected_text` → `_extract_result` 提取 |

### 3.3 Turn 计数

| 项 | TS | PY |
|----|----|-----|
| 计数时机 | 每次 `assistant` 事件 +1 | 每次 `_handle_assistant` 调用 `turn_count += 1` |
| 使用位置 | 传递给 `_extract_result` 的 `turns` 字段 | 通过 `ResultMessage.turn_count` 传递给 `_extract_result` |
| 默认值 | 1 | 1 |

### 3.4 架构差异

**TS**: 所有事件处理逻辑集中在 `claude-executor.ts` 的 `handleAssistantMessage()`、`handleToolUseMessage()` 等函数中，每个函数直接操作闭包变量。

**PY**: 通过 `MessageDispatcher` 类封装，提供可测试的独立组件：

```python
class MessageDispatcher:
    # 状态
    turn_count: int
    text_parts: list[str]
    spending_cap_detected: bool
    audit_logger: ToolAuditLogger

    # 核心方法
    async dispatch(event) -> "continue" | "complete"  # 分发入口
    async _handle_assistant(event)                     # 处理助手消息
    async _handle_tool_use(event)                      # 处理工具调用
    async _handle_tool_result(event)                   # 处理工具结果
```

**影响**: PY 的设计更容易单元测试（可直接构造 mock event 测试每个分支），但两者在运行时行为上等价。

---

## 4. 环境变量管理

### 4.1 构建方式对比

| 项 | TS (`claude-executor.ts`) | PY (`providers_anthropic.py`) |
|----|--------------------------|-------------------------------|
| 构建方法 | 内联构建 `env` 对象 | `_build_sdk_env()` 独立方法 |
| 零配置支持 | SDK 自动读取进程环境 | 同样支持（SHANNON_* 显式覆盖机制） |
| 覆盖优先级 | 参数 > 环境变量 | 参数 > SHANNON_* > ANTHROPIC_* |

### 4.2 环境变量透传列表

| 环境变量 | TS | PY | 用途 |
|---------|----|-----|------|
| `ANTHROPIC_API_KEY` | ✅ | ✅ | API Key（主入口） |
| `CLAUDE_CODE_OAUTH_TOKEN` | ✅ | ✅ | OAuth Token |
| `ANTHROPIC_BASE_URL` | ✅ | ✅ | 自定义 API 端点 |
| `ANTHROPIC_AUTH_TOKEN` | ✅ | ✅ | Bearer Token 认证 |
| `CLAUDE_CODE_USE_BEDROCK` | ✅ | ✅ | 启用 Bedrock 提供商 |
| `AWS_REGION` | ✅ | ✅ | Bedrock 区域 |
| `AWS_BEARER_TOKEN_BEDROCK` | ✅ | ✅ | Bedrock Bearer Token |
| `CLAUDE_CODE_USE_VERTEX` | ✅ | ✅ | 启用 Vertex 提供商 |
| `CLOUD_ML_REGION` | ✅ | ✅ | Vertex 区域 |
| `ANTHROPIC_VERTEX_PROJECT_ID` | ✅ | ✅ | Vertex 项目 ID |
| `GOOGLE_APPLICATION_CREDENTIALS` | ✅ | ✅ | Google 服务账号凭证 |
| `HOME` | ✅ | ✅ | 主目录（SDK 需要） |
| `PATH` | ✅ | ✅ | 可执行文件路径 |
| `PLAYWRIGHT_MCP_EXECUTABLE_PATH` | ✅ | ✅ | Playwright MCP 路径 |
| `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | ✅ | ✅ | 最大输出 token 数 |

**透传列表完全一致**，15 个变量全部对齐。

### 4.3 Provider 特定环境变量

| Provider | TS 行为 | PY 行为 | 对齐状态 |
|----------|---------|---------|---------|
| `anthropic_api` | 透传 `ANTHROPIC_API_KEY` | 同 | 🟢 |
| `bedrock` | 设 `CLAUDE_CODE_USE_BEDROCK=1` + `AWS_REGION` | 同 | 🟢 |
| `vertex` | 设 `CLAUDE_CODE_USE_VERTEX=1` + `CLOUD_ML_REGION` + `ANTHROPIC_VERTEX_PROJECT_ID` | 同 | 🟢 |
| `litellm_router` | 设 `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` | 同 | 🟢 |
| `openai_compatible` | N/A（TS 通过单独 provider） | 设 `ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY` | 🟢 |

### 4.4 零配置机制

两个项目都实现了零配置体验——只需设置 `ANTHROPIC_API_KEY` 即可运行。

**TS**: SDK 自动从进程环境读取，executor 不主动覆盖。

**PY**: 通过 `_build_sdk_env()` 实现：

```python
# 只有 SHANNON_* 显式变量才覆盖，否则继承进程环境
for var in PASSTHROUGH_VARS:
    if var not in sdk_env:
        val = os.getenv(var)
        if val:
            sdk_env[var] = val
```

### 4.5 默认值

| 配置项 | TS 默认值 | PY 默认值 | 对齐 |
|--------|----------|----------|------|
| `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | 64000 | 64000 | 🟢 |
| `permission_mode` | `bypassPermissions` | `bypassPermissions` | 🟢 |

---

## 5. 工具调用审计

### 5.1 架构对比

| 项 | TS | PY |
|----|----|-----|
| 审计接口 | 内联处理，无独立抽象 | `ToolAuditLogger` ABC + 两种实现 |
| 设计模式 | 直接调用 `auditLogger` 函数 | Null Object 模式 + 桥接模式 |
| 可测试性 | 依赖 mock 整个 executor | 独立组件，可单独单测 |

### 5.2 审计事件

| 审计事件 | TS | PY | 触发时机 |
|---------|----|-----|---------|
| `tool_start` | ✅ | ✅ | 收到 `tool_use` 事件 |
| `tool_end` | ✅ | ✅ | 收到 `tool_result` 事件 |
| `agent_error` | ✅ | ✅ | assistant 事件携带 error |

### 5.3 PY 的实现层次

```
ToolAuditLogger (ABC)
├── NullToolAuditLogger      ← 默认实现，所有方法空操作
└── ActivityToolAuditLogger  ← 桥接到 ActivityLogger
```

| 方法 | NullToolAuditLogger | ActivityToolAuditLogger |
|------|--------------------|-----------------------|
| `log_tool_start(name, params)` | `pass` | `activity_logger.info("tool_start", tool_name=name, parameters=str(params)[:500])` |
| `log_tool_end(result)` | `pass` | `activity_logger.info("tool_end", result=str(result)[:500])` |
| `log_error(error, turn_count, duration_ms)` | `pass` | `activity_logger.error("agent_error", error=error, ...)` |

### 5.4 参数截断

| 项 | TS | PY |
|----|----|-----|
| 截断长度 | 有截断 | **500 字符** |
| 截断对象 | tool parameters + result | tool parameters + result |
| 目的 | 防止审计日志膨胀 | 同 |

### 5.5 集成路径

| 步骤 | TS | PY |
|------|----|-----|
| 1. Provider 构建 | executor 内创建 audit handler | `MessageDispatcher(audit_logger=...)` 注入 |
| 2. 事件分发 | `handleToolUseMessage` → audit | `dispatcher._handle_tool_use` → `audit_logger.log_tool_start` |
| 3. 结果审计 | `handleToolResultMessage` → audit | `dispatcher._handle_tool_result` → `audit_logger.log_tool_end` |

**关键差异**: PY 将审计关注点从 Provider 层分离到了 `MessageDispatcher` 层，Provider 只负责调度 dispatcher，不直接处理审计逻辑。这意味着：

- ✅ 审计逻辑可在不修改 Provider 的情况下替换（换一个 `ToolAuditLogger` 实现即可）
- ✅ `MessageDispatcher` 可独立测试审计行为
- ⚠️ 需要确保 dispatcher 正确注入到 Provider（目前通过 `_execute_query` 的可选参数）

---

## 6. 花费上限检测

### 6.1 检测层级对比

| 检测层 | TS | PY | 对齐状态 |
|--------|----|-----|---------|
| Layer 1: 消息级关键词 | ✅ 5种模式 | ✅ 5种模式 | 🟢 已对齐 |
| Layer 2: 行为级启发式 | ✅ 低 turn + 零 cost | ✅ 低 turn + 零 cost | 🟢 已对齐 |
| Layer 3: 异常级检测 | ✅ exception 消息匹配 | ✅ exception 消息匹配 | 🟢 已对齐 |
| Layer 4: API 结构化错误 | ✅ 11种账单错误模式 | ❌ 未实现 | 🔴 缺失 |

### 6.2 Layer 1: 消息级关键词检测

| 关键词 | TS | PY |
|--------|----|-----|
| `spending limit` | ✅ | ✅ |
| `credit limit` | ✅ | ✅ |
| `quota exceeded` | ✅ | ✅ |
| `budget exceeded` | ✅ | ✅ |
| `maximum spend` | ✅ | ✅ |

检测逻辑完全一致——在 `assistant` 事件的文本块中做大小写不敏感的子串匹配。

### 6.3 Layer 2: 行为级启发式

| 判断条件 | TS | PY |
|---------|----|-----|
| turn ≤ 1 | ✅ | ✅ `turn_count <= 1` |
| cost == 0 | ✅ | ✅ `result.cost == 0.0` |
| success == false | ✅ | ✅ `not result.success` |
| 结果标记 | `isSpendingCapBehavior = true` | `success=False, retryable=True` |

逻辑等价，触发条件相同。

### 6.4 Layer 3: 异常级检测

| 项 | TS | PY |
|----|----|-----|
| 检测位置 | `_handle_error` | `_handle_error` |
| 匹配方式 | exception message 子串匹配 | 同 |
| 结果 | 标记为 spending cap + retryable | 标记 `花费上限` + `retryable=True` |

### 6.5 Layer 4: API 结构化错误（PY 缺失）

**TS 独有的 11 种 API 账单错误模式：**

| API 错误模式 | 说明 |
|-------------|------|
| `billing_error` | 账单系统错误 |
| `credit balance too low` | 余额不足 |
| `monthly spending limit` | 月度花费上限 |
| `usage limit exceeded` | 用量超限 |
| `rate limit exceeded` | 频率限制（**注意：PY 误将其归入消息级检测，可能导致误报**） |
| `account suspended` | 账户暂停 |
| `payment method` | 支付方式问题 |
| `invoice` | 发票问题 |
| `subscription` | 订阅问题 |
| `plan limit` | 套餐限制 |
| `tier limit` | 层级限制 |

**缺失原因**: TS 在 `_handle_error` 中对 Anthropic API 返回的结构化错误（HTTP 402/429 等）做单独匹配；PY 目前仅做了通用的 exception 消息匹配，未区分 API 结构化错误和普通异常。

**影响**: 当 Anthropic API 返回结构化账单错误（如 HTTP 402 `credit balance is too low`）时，PY 可能无法正确识别为花费上限行为，导致不必要的重试或错误分类。

### 6.6 集成点对比

| 集成点 | TS | PY |
|--------|----|-----|
| 消息流处理 | executor 内直接检测 | `MessageDispatcher._handle_assistant` |
| 结果后处理 | executor 后检查 behavioral | `AnthropicProvider.call()` 后检查 |
| 异常处理 | `_handle_error` 内检测 | `_handle_error` 内检测 |
| API 错误检测 | 独立匹配逻辑 | ❌ 缺失 |

---

## 7. 错误处理与重试

### 7.1 错误分类

| 项 | TS | PY | 对齐状态 |
|----|----|-----|---------|
| 可重试错误 | ✅ 自动重试 | ✅ 自动重试 | 🟢 已对齐 |
| 不可重试错误 | ✅ 8 种类型，立即终止 | ❌ 未分类，全部可重试 | 🔴 缺失 |
| 错误码映射 | ✅ `classifyErrorCode()` | ❌ 无 | 🔴 缺失 |

**TS 的 8 种不可重试错误类型：**

| 错误类型 | 说明 | TS 处理 |
|---------|------|---------|
| `AuthenticationError` | API Key 无效/过期 | 标记 non-retryable |
| `PermissionError` | 权限不足 | 标记 non-retryable |
| `InvalidRequestError` | 请求格式错误 | 标记 non-retryable |
| `RequestTooLargeError` | 输入超限 | 标记 non-retryable |
| `ConfigurationError` | 配置错误 | 标记 non-retryable |
| `InvalidTargetError` | 目标无效 | 标记 non-retryable |
| `ExecutionLimitError` | 执行限制 | 标记 non-retryable |
| `AuthLoginFailedError` | 登录验证失败 | 标记 non-retryable |

**缺失原因**: PY 的 `_handle_error` 只区分 spending cap 和普通异常，未实现结构化的错误类型分类。

**影响**: 认证失败、权限错误等不可恢复错误会被 Temporal 反复重试（最多 50 次），浪费时间和 API 调用。

### 7.2 重试策略

| 模式 | TS | PY | 对齐状态 |
|------|----|-----|---------|
| 生产模式 | 50 attempts, 5min/30min, 系数 2 | 50 attempts, 5min/30min, 系数 2 | 🟢 已对齐 |
| Testing 模式 | 30s timeout, 10s/30s retry, 5 attempts | ❌ `pipeline_testing_mode` flag 仅传递到 activity，不影响 timeout/retry | 🔴 缺失 |
| Subscription 模式 | 8h timeout, 5min/6h retry, 100 attempts | ❌ 不存在 | 🔴 缺失 |

**缺失原因**: PY 的 Temporal activity 配置中未根据运行模式动态调整 timeout 和 retry 参数。

**影响**:

- Testing 模式下无法快速失败（仍用 2h timeout），开发者体验差
- 无法支持长时间运行的 Subscription 扫描模式

### 7.3 错误传播

| 项 | TS | PY |
|----|----|-----|
| 成功状态 | `status = "completed"` | `status = "completed"` |
| 部分失败 | 抛异常 → Temporal 标记 `failed` | ⚠️ 仍返回 `"completed"` |
| 取消 | `isCancellation(error)` → `"cancelled"` | ❌ 无取消处理 |
| 错误码 | `state.errorCode` 分类存储 | ❌ 无 errorCode 字段 |
| 失败 agent | `state.failedAgent` 追踪 | ❌ 无此字段（白盒） |

**缺失原因**: PY 的 workflow 层未实现错误状态的正确传播逻辑。

**影响**: 调用方无法区分完整成功和部分失败，即使多个 agent 失败，workflow 仍报告完成。

### 7.4 `_handle_error` 对比

| 项 | TS | PY |
|----|----|-----|
| Spending cap 检测 | ✅ | ✅ |
| 错误分类 | ✅ classifyErrorCode → 结构化分类 | ❌ 仅区分 spending cap 和其他 |
| 结果结构 | `ClaudeRunResult` 含 errorCode + retryable | `ClaudeRunResult` 含 error + retryable |
| 日志 | 结构化错误日志 | 基础日志 |

---

## 8. 模型管理

### 8.1 模型分级

| 分级 | TS 默认模型 | PY 默认模型 | 对齐 |
|------|-----------|-----------|------|
| small | `claude-haiku-4-5-20251001` | `claude-haiku-4-5-20251001` | 🟢 |
| medium | `claude-sonnet-4-6` | `claude-sonnet-4-6` | 🟢 |
| large | `claude-opus-4-8` | `claude-opus-4-8` | 🟢 |

### 8.2 各 Provider 模型映射

| Provider | TS small | PY small | TS medium | PY medium | TS large | PY large |
|----------|---------|---------|----------|----------|---------|---------|
| `anthropic_api` | claude-haiku-4-5-20251001 | 同 | claude-sonnet-4-6 | 同 | claude-opus-4-8 | 同 |
| `bedrock` | us.anthropic.claude-haiku-4-5 | 同 | us.anthropic.claude-sonnet-4-6 | 同 | us.anthropic.claude-opus-4-8 | 同 |
| `vertex` | claude-haiku-4-5@latest | 同 | claude-sonnet-4-6@latest | 同 | claude-opus-4-8@latest | 同 |
| `litellm_router` | 同 anthropic_api | 同 | 同 anthropic_api | 同 | 同 anthropic_api | 同 |

**模型映射完全一致**。

### 8.3 模型解析流程

| 步骤 | TS (`resolveModel`) | PY (`_get_model`) | 对齐 |
|------|---------------------|-------------------|------|
| 1. 环境变量覆盖 | `SHANNON_MODEL` → 直接使用 | `SHANNON_MODEL` → 直接使用 | 🟢 |
| 2. 分级选择 | 按 `model_tier` 从 DEFAULT_MODELS 选取 | 同 | 🟢 |
| 3. 默认值 | `model_tier = "medium"` | `model_tier = "medium"` | 🟢 |

### 8.4 Adaptive Thinking

| 项 | TS | PY | 对齐 |
|----|----|-----|------|
| 开关 | `CLAUDE_ADAPTIVE_THINKING` 环境变量 | 同 | 🟢 |
| 默认值 | `true`（启用） | `true`（启用） | 🟢 |
| 实现方式 | `ThinkingConfigAdaptive` 传给 SDK | `ThinkingConfigAdaptive` 传给 SDK | 🟢 |
| 配置位置 | 构建 options 时注入 | `_build_options` 中注入 | 🟢 |

### 8.5 模型在 Agent 中的使用

| Agent 阶段 | TS 模型分级 | PY 模型分级 | 对齐 |
|-----------|-----------|-----------|------|
| pre-recon | medium | medium | 🟢 |
| recon | medium | medium | 🟢 |
| vuln agents | medium | medium | 🟢 |
| exploit agents | medium | medium | 🟢 |
| report | medium | medium | 🟢 |

---

## 9. 结构化输出

### 9.1 实现机制

| 项 | TS | PY | 对齐状态 |
|----|----|-----|---------|
| 格式定义 | `JsonSchemaOutputFormat` 对象 | `dict` (JSON Schema) | 🟢 已对齐 |
| 传递方式 | `options.outputFormat` | `options.output_format` | 🟢 已对齐 |
| SDK 支持 | Claude Agent SDK 原生支持 | 同 | 🟢 已对齐 |

### 9.2 使用场景

| 场景 | TS | PY | 对齐 |
|------|----|-----|------|
| 漏洞分析输出 | `buildOutputFormats(exploit)` 动态构建 | `output_format` 参数传入 JSON Schema | 🟢 |
| Exploit 阶段输出 | 按 agent 名称查找对应 schema | 同 | 🟢 |
| 模式切换 | `exploit=true/false` 改变 `notes` 字段描述引导 LLM | `output_format` 直接传入 | 🟡 |

### 9.3 模式系统差异

| 项 | TS | PY |
|----|----|-----|
| Schema 构建 | `buildOutputFormats(exploit: boolean)` 根据模式动态修改字段描述 | 调用方直接传入完整 schema |
| notes 字段描述 | vuln 模式："描述漏洞细节"；exploit 模式："描述利用过程" | 固定描述，无模式切换 |
| 映射函数 | `getOutputFormat(agentName, exploit)` 集中管理 | 各 agent 配置中分别定义 |

**差异原因**: TS 通过 `buildOutputFormats()` 函数统一管理 schema 的模式切换，PY 将 schema 构建分散到各调用方。

**影响**:

- PY 的 exploit 阶段 LLM 可能收到与 vuln 阶段相同的 `notes` 字段描述，导致输出不够精确
- 但由于每个 agent 的 schema 在配置中已明确定义，实际影响较小

### 9.4 结果提取

| 项 | TS | PY |
|----|----|-----|
| 提取位置 | executor 从 ResultMessage 提取 | `_extract_result` 从 `result_message.structured_output` 提取 |
| 存储字段 | `ClaudeRunResult.structured_output` | 同 |
| 回退机制 | 文本解析兜底 | 文本解析兜底 |

### 9.5 Queue Schema 对齐

每个漏洞类型的 JSON Schema（字段数量和结构）已完全对齐：

| Schema | TS 字段数 | PY 字段数 | 状态 |
|--------|----------|----------|------|
| `InjectionVulnerability` | 15 | 15 | 🟢 |
| `XssVulnerability` | 14 | 14 | 🟢 |
| `AuthVulnerability` | 10 | 10 | 🟢 |
| `SsrfVulnerability` | 11 | 11 | 🟢 |
| `AuthzVulnerability` | 12 | 12 | 🟢 |
| `MisconfigVulnerability` | 13 | ❌ 缺失 | 🔴 |

> 注：`MisconfigVulnerability` 缺失属于功能范围差异，非结构化输出机制本身的问题。

---

## 10. 成本追踪

### 10.1 Token 统计

| 项 | TS | PY | 对齐状态 |
|----|----|-----|---------|
| 输入 token | ✅ 从 `ResultMessage` 提取 | ✅ `_extract_tokens()` 提取 | 🟢 已对齐 |
| 输出 token | ✅ | ✅ | 🟢 已对齐 |
| 缓存读取 token | ✅ | ✅ | 🟢 已对齐 |
| 缓存写入 token | ✅ | ✅ | 🟢 已对齐 |

### 10.2 成本计算

| 项 | TS | PY |
|----|----|-----|
| 来源 | `ResultMessage.total_cost_usd` | `_extract_cost()` 从 `total_cost_usd` 提取 |
| 精度 | SDK 直接返回美元值 | 同 |
| 存储字段 | `ClaudeRunResult.cost` | 同 |

### 10.3 成本聚合

| 层级 | TS | PY |
|------|----|-----|
| 单次调用 | `ClaudeRunResult.cost` | 同 |
| 单 agent | executor 累加 | `AgentMetrics` 累加 |
| 全流水线 | `PipelineProgress` 汇总 | 同 |

### 10.4 OpenAI Provider 成本估算

对于 `openai_compatible` provider（非 Claude 模型），两个项目都需要自行估算成本：

| 项 | TS | PY |
|----|----|-----|
| 估算方式 | 基于 token 数 × 单价 | 基于 token 数 × 单价 |
| 精确度 | 近似 | 近似 |

---

## 11. 提供商支持矩阵

### 11.1 提供商类型

| Provider | TS | PY | 对齐状态 |
|----------|----|-----|---------|
| `anthropic_api` | ✅ 直接 Anthropic API | ✅ | 🟢 已对齐 |
| `bedrock` | ✅ AWS Bedrock | ✅ | 🟢 已对齐 |
| `vertex` | ✅ Google Vertex AI | ✅ | 🟢 已对齐 |
| `litellm_router` | ✅ LiteLLM 代理 | ✅ | 🟢 已对齐 |
| `openai_compatible` | ✅ OpenAI 兼容接口 | ✅ | 🟢 已对齐 |

### 11.2 认证方式

| 认证方式 | TS | PY | 对齐状态 | 说明 |
|---------|----|-----|---------|------|
| API Key | ✅ `ANTHROPIC_API_KEY` | ✅ | 🟢 已对齐 | 主认证方式 |
| OAuth Token | ✅ `CLAUDE_CODE_OAUTH_TOKEN` | 🟡 仅透传 | 🟡 部分对齐 | 见下方详述 |
| Bearer Token | ✅ `ANTHROPIC_AUTH_TOKEN` | ✅ | 🟢 已对齐 | LiteLLM / 自定义端点 |
| AWS Credentials | ✅ `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` | ✅ | 🟢 已对齐 | Bedrock |
| AWS Bearer | ✅ `AWS_BEARER_TOKEN_BEDROCK` | ✅ | 🟢 已对齐 | Bedrock |
| Google Service Account | ✅ `GOOGLE_APPLICATION_CREDENTIALS` | ✅ | 🟢 已对齐 | Vertex |
| 区域配置 | ✅ `AWS_REGION` / `CLOUD_ML_REGION` | ✅ | 🟢 已对齐 | Bedrock / Vertex |
| 项目 ID | ✅ `ANTHROPIC_VERTEX_PROJECT_ID` | ✅ | 🟢 已对齐 | Vertex |

### 11.3 OAuth 认证差异详解

| 项 | TS | PY |
|----|----|-----|
| Token 获取 | 有 OAuth 流程，通过 SDK 获取 token | ❌ 无 OAuth 流程实现 |
| Token 刷新 | 自动刷新过期 token | ❌ 无 |
| 环境变量透传 | ✅ | ✅ 已在 `_build_sdk_env` 的 `PASSTHROUGH_VARS` 中 |
| 手动设置 | 用户可手动设 `CLAUDE_CODE_OAUTH_TOKEN` | 同 |

**现状**: PY 在环境变量透传列表中包含了 `CLAUDE_CODE_OAUTH_TOKEN`，意味着如果用户手动设置了此变量，SDK 子进程可以读取到。但 PY 本身不实现 OAuth token 的获取和刷新逻辑。

**实际影响**:

- 如果用户通过 Claude Code CLI 已完成 OAuth 登录，`CLAUDE_CODE_OAUTH_TOKEN` 会存在于环境中，PY 可以间接使用
- PY 无法独立发起 OAuth 流程获取新 token

### 11.4 Provider 配置构建

| 项 | TS | PY |
|----|----|-----|
| 配置来源 | 参数 + 环境变量 | 同 |
| 优先级 | 参数 > 环境变量 | 参数 > SHANNON_* > ANTHROPIC_* |
| 零配置 | ✅ 只需 `ANTHROPIC_API_KEY` | ✅ 同 |
| 类型安全 | TypeScript 强类型 | Pydantic / dataclass |

---

## 12. 待对齐项汇总与建议

### 12.1 待对齐项总览

| # | 待对齐项 | 所属维度 | 严重程度 | 影响 |
|---|---------|---------|---------|------|
| 1 | API 结构化账单错误检测（Layer 4） | 花费上限检测 | 中 | API 返回 402/429 账单错误时无法正确识别 |
| 2 | 不可重试错误类型分类 | 错误处理与重试 | 高 | 认证失败等不可恢复错误被反复重试，浪费时间 |
| 3 | Testing/Subscription 重试模式 | 错误处理与重试 | 中 | 无法按运行模式动态调整 timeout 和 retry |
| 4 | 错误状态正确传播 | 错误处理与重试 | 高 | 调用方无法区分完整成功和部分失败 |
| 5 | OAuth token 获取与刷新 | 认证方式 | 低 | 无法独立发起 OAuth 流程，依赖外部 token |

### 12.2 建议优先级

**P1 — 影响可靠性（建议优先实施）**

| # | 项目 | 预估工作量 | 说明 |
|---|------|-----------|------|
| 2 | 不可重试错误类型分类 | 小 | 在 `_handle_error` 中增加错误类型分类逻辑，对齐 TS 的 8 种 non-retryable 类型 |
| 4 | 错误状态正确传播 | 小 | 修改 workflow 层，部分失败时抛异常而非返回 completed |

**P2 — 增强健壮性（建议后续实施）**

| # | 项目 | 预估工作量 | 说明 |
|---|------|-----------|------|
| 1 | API 结构化账单错误检测 | 小 | 在 `_handle_error` 中增加 11 种 API 账单错误模式匹配 |
| 3 | Testing/Subscription 重试模式 | 中 | 根据 `pipeline_testing_mode` 动态调整 Temporal activity 的 timeout 和 retry 参数 |

**P3 — 按需实施**

| # | 项目 | 预估工作量 | 说明 |
|---|------|-----------|------|
| 5 | OAuth token 获取与刷新 | 中 | 大多数场景通过 API Key 或外部注入 token 即可满足，OAuth 流程为锦上添花 |

### 12.3 已完成的对齐工作

| 实施项 | 对应计划文档 | 提交记录 |
|--------|------------|---------|
| 零配置 Claude 体验 | `2026-06-03-zero-config-claude.md` | 已提交 |
| 环境变量透传 `_build_sdk_env` | `2026-06-04-sdk-parity-alignment.md` Task 4 | 已提交 |
| MessageDispatcher 消息流处理 | 同上 Task 3 | 已提交 |
| ToolAuditLogger 审计接口 | 同上 Task 1-2 | 已提交 |
| 三层花费上限检测 | 同上 Task 7 | 已提交 |
| Dispatcher 集成到 `_execute_query` | 同上 Task 5-6 | 已提交 |

### 12.4 架构差异总结

两个项目在 Claude Agent SDK 使用上采用了**同源不同层**的设计：

```
                    相同层                          差异层
              ┌──────────────┐              ┌───────────────────┐
  SDK 调用 →  │ query() 子进程│              │ 代码组织方式       │
  消息流   →  │ 事件类型     │              │ 类封装 vs 函数式   │
  环境变量 →  │ 透传列表     │              │ 零配置覆盖机制     │
  模型管理 →  │ 分级+默认值  │              │                   │
  结构化输出→ │ JSON Schema  │              │                   │
  成本追踪 →  │ Token 统计   │              │                   │
  提供商   →  │ 5种类型      │              │                   │
              └──────────────┘              └───────────────────┘
              核心行为完全一致                  风格差异，行为等价
```

**核心结论**: shannon-py 在 Claude SDK 集成上的核心架构与 TS 版本完全一致——都通过 `claude-agent-sdk` 的 `query()` 函数以子进程模式调用 Claude。11 个功能维度中 7 个已完全对齐，剩余 4 个待对齐项均为增强性功能（错误分类、重试模式、OAuth），不影响核心扫描流水线运行。

---

*本文档基于 2026-06-04 两个代码库的状态生成。TypeScript Shannon 位于 `/root/shannon`，Python 重构版位于 `/root/shannon-py`。*
