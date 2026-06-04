# Claude SDK 待对齐项行动计划

> 聚焦 shannon-py 与 TS Shannon 在 Claude Agent SDK 使用上尚未对齐的差异项，作为后续实施的直接输入

**日期**: 2026-06-04
**基线文档**: [`2026-06-04-claude-sdk-diff-analysis-design.md`](./2026-06-04-claude-sdk-diff-analysis-design.md)（全量 11 维度对比）

---

## 待对齐项总览

| # | 待对齐项 | 所属维度 | 严重程度 | 影响 |
|---|---------|---------|---------|------|
| 1 | API 结构化账单错误检测（Layer 4） | 花费上限检测 | 中 | API 返回 402/429 账单错误时无法正确识别 |
| 2 | 不可重试错误类型分类 | 错误处理与重试 | 高 | 认证失败等不可恢复错误被反复重试，浪费时间 |
| 3 | Testing/Subscription 重试模式 | 错误处理与重试 | 中 | 无法按运行模式动态调整 timeout 和 retry |
| 4 | 错误状态正确传播 | 错误处理与重试 | 高 | 调用方无法区分完整成功和部分失败 |
| 5 | OAuth token 获取与刷新 | 认证方式 | 低 | 无法独立发起 OAuth 流程，依赖外部 token |

---

## 1. 花费上限检测 — Layer 4 缺失

### 现状

PY 已实现 3 层检测（消息级 / 行为级 / 异常级），缺少 TS 的第 4 层：**API 结构化账单错误检测**。

### TS 的 11 种 API 账单错误模式

| API 错误模式 | 说明 |
|-------------|------|
| `billing_error` | 账单系统错误 |
| `credit balance too low` | 余额不足 |
| `monthly spending limit` | 月度花费上限 |
| `usage limit exceeded` | 用量超限 |
| `rate limit exceeded` | 频率限制 |
| `account suspended` | 账户暂停 |
| `payment method` | 支付方式问题 |
| `invoice` | 发票问题 |
| `subscription` | 订阅问题 |
| `plan limit` | 套餐限制 |
| `tier limit` | 层级限制 |

### 差异原因

TS 在 `_handle_error` 中对 Anthropic API 返回的结构化错误（HTTP 402/429 等）做单独匹配；PY 目前仅做了通用的 exception 消息匹配，未区分 API 结构化错误和普通异常。

### 影响

当 Anthropic API 返回结构化账单错误（如 HTTP 402 `credit balance is too low`）时，PY 可能无法正确识别为花费上限行为，导致不必要的重试或错误分类。

### 实施建议

在 `_handle_error` 中新增 `API_SPENDING_CAP_PATTERNS` 列表，与现有的 `SPENDING_CAP_PATTERNS`（消息级）并行，在异常处理阶段匹配 API 结构化错误关键词。预估工作量：**小**。

---

## 2. 不可重试错误类型分类

### 现状

PY 的 `_handle_error` 只区分 spending cap 和普通异常，未实现结构化的错误类型分类。

### TS 的 8 种不可重试错误类型

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

### 影响

认证失败、权限错误等不可恢复错误会被 Temporal 反复重试（最多 50 次），浪费时间和 API 调用。

### 实施建议

1. 定义 `NON_RETRYABLE_PATTERNS: list[tuple[str, str]]` — (错误类型名, 匹配模式) 列表
2. 在 `_handle_error` 中新增错误分类逻辑：匹配异常消息 → 标记 `retryable=False`
3. 将错误类型名存入 `ClaudeRunResult.error_code` 字段

预估工作量：**小**。

---

## 3. Testing/Subscription 重试模式

### 现状

PY 的 Temporal activity 配置中未根据运行模式动态调整 timeout 和 retry 参数。

### TS 的模式对比

| 模式 | Timeout | Retry Interval | Max Attempts | 使用场景 |
|------|---------|---------------|-------------|---------|
| 生产 | 2h | 5min/30min, 系数 2 | 50 | 正式扫描 |
| Testing | 30s | 10s/30s | 5 | 开发调试 |
| Subscription | 8h | 5min/6h | 100 | 长期订阅扫描 |

### PY 现状

`pipeline_testing_mode` flag 仅传递到 activity，不影响 Temporal 的 timeout 和 retry 配置。Subscription 模式不存在。

### 影响

- Testing 模式下无法快速失败（仍用 2h timeout），开发者体验差
- 无法支持长时间运行的 Subscription 扫描模式

### 实施建议

1. 创建 `build_retry_policy(mode: str) -> RetryPolicy` 函数
2. 创建 `build_activity_timeout(mode: str) -> timedelta` 函数
3. 在 workflow 编排层根据模式动态应用

预估工作量：**中**。

---

## 4. 错误状态正确传播

### 现状

PY 的 workflow 层未实现错误状态的正确传播逻辑。

### 对比

| 项 | TS | PY |
|----|----|-----|
| 完全成功 | `status = "completed"` | `status = "completed"` |
| 部分失败 | 抛异常 → Temporal 标记 `failed` | ⚠️ 仍返回 `"completed"` |
| 取消 | `isCancellation(error)` → `"cancelled"` | ❌ 无取消处理 |
| 错误码 | `state.errorCode` 分类存储 | ❌ 无 errorCode 字段 |
| 失败 agent | `state.failedAgent` 追踪 | ❌ 无此字段（白盒） |

### 影响

调用方无法区分完整成功和部分失败。即使多个 agent 失败，workflow 仍报告完成。

### 实施建议

1. 在 `PipelineState` 中增加 `error_code` 和 `failed_agent` 字段
2. 修改 workflow 完成 logic：存在失败 agent 时抛异常而非返回 completed
3. 增加 Temporal 取消信号处理

预估工作量：**小**。

---

## 5. OAuth Token 获取与刷新

### 现状

PY 在环境变量透传列表中包含了 `CLAUDE_CODE_OAUTH_TOKEN`，但不实现 OAuth token 的获取和刷新逻辑。

### 影响

- 如果用户通过 Claude Code CLI 已完成 OAuth 登录，`CLAUDE_CODE_OAUTH_TOKEN` 会存在于环境中，PY 可以间接使用
- PY 无法独立发起 OAuth 流程获取新 token

### 实施建议

P3 优先级 — 大多数场景通过 API Key 或外部注入 token 即可满足，OAuth 流程为锦上添花。

如需实施：
1. 集成 Claude Agent SDK 的 OAuth 模块（如果 SDK Python 版支持）
2. 实现 token 缓存和自动刷新逻辑
3. 在 `_build_sdk_env` 中优先使用缓存的 OAuth token

预估工作量：**中**。

---

## 实施优先级

**P1 — 影响可靠性（建议优先实施）**

| # | 项目 | 工作量 | 依赖 |
|---|------|--------|------|
| 2 | 不可重试错误类型分类 | 小 | 无 |
| 4 | 错误状态正确传播 | 小 | #2（错误分类） |

**P2 — 增强健壮性**

| # | 项目 | 工作量 | 依赖 |
|---|------|--------|------|
| 1 | API 结构化账单错误检测 | 小 | 无 |
| 3 | Testing/Subscription 重试模式 | 中 | 无 |

**P3 — 按需实施**

| # | 项目 | 工作量 | 依赖 |
|---|------|--------|------|
| 5 | OAuth token 获取与刷新 | 中 | SDK Python 版 OAuth 支持 |

---

*基线文档: [`2026-06-04-claude-sdk-diff-analysis-design.md`](./2026-06-04-claude-sdk-diff-analysis-design.md)*
