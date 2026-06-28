# Claude SDK 待对齐项行动计划

> 聚焦 shannon-py 在 Claude Agent SDK 使用上真正需要改进的差异项
>
> **筛选原则**: 功能完整、效果一致、日志检查/结果查看无差异 → 不需要对齐

**日期**: 2026-06-04
**基线文档**: [`2026-06-04-claude-sdk-diff-analysis-design.md`](./2026-06-04-claude-sdk-diff-analysis-design.md)（全量 11 维度对比）

---

## 筛选结果

原始 5 项待对齐项经审查后，保留 2 项：

| # | 项目 | 保留原因 |
|---|------|---------|
| 1 | 错误状态正确传播 | **影响结果查看** — 调用方拿到的 status 是 `completed`，但实际有 agent 失败了，结果不准确 |
| 2 | 不可重试错误类型分类 | **影响效率** — 认证失败等不可恢复错误最多多等 5 轮重试（每轮 5min），浪费时间和 API 调用 |

**已排除的 3 项及原因：**

| 排除项 | 原因 |
|--------|------|
| API 结构化账单错误检测 (Layer 4) | Layer 3 异常级检测已覆盖相同场景（`_is_spending_cap_error()` 已能匹配 `credit balance is too low` 等关键词），属于重复覆盖 |
| Testing/Subscription 重试模式 | testing mode 已通过 `get_retry_policy()` 影响重试策略，subscription 模式无实际需求 |
| OAuth token 获取与刷新 | API Key 模式满足所有场景，环境变量透传已支持外部 token 注入，无实际 OAuth 需求 |

---

## 1. 错误状态正确传播

**优先级**: P1 — 必须实施

### 现状

PY 的 workflow 层未实现错误状态的正确传播逻辑。

| 项 | TS | PY |
|----|----|-----|
| 完全成功 | `status = "completed"` | `status = "completed"` |
| 部分失败 | 抛异常 → Temporal 标记 `failed` | ⚠️ 仍返回 `"completed"` |
| 取消 | `isCancellation(error)` → `"cancelled"` | ❌ 无取消处理 |
| 错误码 | `state.errorCode` 分类存储 | ❌ 无 errorCode 字段 |
| 失败 agent | `state.failedAgent` 追踪 | ❌ 无此字段 |

### 实际行为

- `asyncio.gather(*tasks, return_exceptions=True)` 捕获异常存入 `self._state.errors`
- 但 workflow 最终仍返回 `status = "completed"`
- 调用方无法从返回结果中区分「全部成功」和「部分 agent 失败」

### 实施建议

1. 在 `PipelineState` 中增加 `error_code` 和 `failed_agent` 字段
2. 修改 workflow 完成 logic：存在失败 agent 时设置 `status = "failed"` 并抛异常
3. 增加 Temporal 取消信号处理

预估工作量：**小**。

---

## 2. 不可重试错误类型分类

**优先级**: P2 — 建议实施

### 现状

PY 的 `_handle_error` 只区分 spending cap 和普通异常，未实现结构化的错误类型分类。不可恢复错误（如认证失败）会被 Temporal 反复重试最多 50 次。

### 需要标记为不可重试的错误类型

| 错误类型 | 匹配模式 | 说明 |
|---------|---------|------|
| `AuthenticationError` | `authentication`、`invalid api key`、`api key` | API Key 无效/过期 |
| `PermissionError` | `permission`、`forbidden`、`access denied` | 权限不足 |
| `InvalidRequestError` | `invalid request`、`bad request` | 请求格式错误 |
| `ConfigurationError` | `configuration`、`config error` | 配置错误 |
| `InvalidTargetError` | `invalid target`、`not found` | 目标无效 |

> 注：参考 TS 的 8 种类型，精简为 PY 实际可能遇到的 5 种。`RequestTooLargeError`、`ExecutionLimitError`、`AuthLoginFailedError` 在 PY 当前架构中不太可能独立出现。

### 实施建议

1. 定义 `NON_RETRYABLE_PATTERNS: list[tuple[str, str]]` — (错误类型名, 匹配关键词) 列表
2. 在 `_handle_error` 中新增错误分类逻辑：匹配异常消息 → 标记 `retryable=False`
3. 将错误类型名存入 `ClaudeRunResult.error_code` 字段
4. 确保该分类结果传递到 `classify_error_for_temporal()`，使 Temporal 不再重试

预估工作量：**小**。

---

## 实施优先级

| 优先级 | 项目 | 工作量 | 依赖 | 顺序 |
|--------|------|--------|------|------|
| P1 | 错误状态正确传播 | 小 | 无 | 第 2 步 |
| P2 | 不可重试错误类型分类 | 小 | 无 | 第 1 步 |

建议先实施 P2（错误分类），因为 P1（状态传播）需要依赖错误分类的结果来判断哪些错误应标记为失败。

---

*基线文档: [`2026-06-04-claude-sdk-diff-analysis-design.md`](./2026-06-04-claude-sdk-diff-analysis-design.md)*
