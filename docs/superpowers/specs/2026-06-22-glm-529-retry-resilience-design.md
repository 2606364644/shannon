# GLM 529 过载重试韧性修复设计

- **日期**:2026-06-22
- **分支**:`feat/fork-py`
- **状态**:待实现
- **关联**:`2026-06-09-sdk-result-handling-hardening-design.md`(SDK result failure 处理硬化,本主题上游)

## 背景与问题

一次 whitebox 扫描(`shannon-whitebox start --repo /root/code/backend/honor`)在 pre-recon 阶段失败,日志:

```
💭 [Agent] Turn 1: API Error: 529 [1305][该模型当前访问量过大,请您稍后再试]...
✗ pre-recon failed (2m 55s) — SDK result failure: subtype=success, api_error_status=529
ERROR PentestError: SDK result failure: subtype=success, api_error_status=529 (context: pre-recon) [TransientError · non-retryable]
```

用户把并发降到 2 仍触发,且看起来"直接死掉",于是中止。

## 根因分析

调查后确认 **Temporal 实际重试逻辑是正常工作的**,真正的缺陷有两处:

### 1. 529 是 GLM 服务端模型过载,与客户端并发无关

`529 [1305]` 的语义是「该模型(服务端模型实例)当前访问量过大」,是网关级全局过载信号,不是针对单个账号/并发数的配额限流(那是 `429`)。降到 1 并发在高峰期同样触发。

此外,`SHANNON_MAX_CONCURRENT` 只控制 vuln 阶段多 exploit agent 的并发(`shared.py: max_concurrent` 注释、`workflows.py:296` 的 semaphore),**pre-recon 是单 agent 顺序执行,根本不经过该 semaphore**。所以并发设置对 pre-recon 的 529 完全不起作用。

### 2. Display 标签误报 `non-retryable`(真 bug)

529 经 `_classify_result_failure`(`providers_anthropic.py:410`)正确判为 `("TransientError", True)` → 经 executor/activities 最终成 `ApplicationFailure(type="AgentExecutionError", non_retryable=False)` → 走 `PRODUCTION_RETRY`,`AgentExecutionError` 不在 `NON_RETRYABLE_TYPES`,**Temporal 本应重试 50 次**。

但 live display 显示的 `[TransientError · non-retryable]` 来自 `errors/classification.py:is_retryable_for_display`,其 `_RETRYABLE_KEYWORDS` 为 `("rate limit","429","timeout","network","ECONN","billing","transient","502","503","504","validation")` —— **漏了 `529`**。错误 message `SDK result failure: subtype=success, api_error_status=529` 不含任何列表内关键词,触发 fail-safe `return False` → 显示 `non-retryable`。

**结论**:display 标签与实际重试行为相反,误导用户以为「不可重试、直接失败」,叠加下方 backoff 问题导致用户中止。

### 3. pre-recon 的 backoff 太长(体感问题)

pre-recon 绑定 `PRODUCTION_RETRY`(`workflows.py:157`),其 `initial_interval=5 分钟`。attempt 1 失败后,attempt 2 要等 5 分钟。用户看到「non-retryable」误报 + 5 分钟无动静,自然以为挂了。`PRODUCTION_RETRY` 面向「重任务谨慎退避」,套在 pre-recon(只读、轻量、可快速重试)上语义不匹配。

> 注:`subtype=success` 却带 `api_error_status=529` 是 Claude Code CLI 在内部重试 GLM 529 几轮仍失败后的怪异返回;但因 `is_error=True` + `api_error_status=529`,分类逻辑仍正确识别,不需处理。

## 目标

1. **Display 如实**:529 错误显示 `retryable`,与 Temporal 实际重试一致,不再骗用户「挂了」。
2. **有界渐进退避**:pre-recon 的过载错误走短初始间隔 + 渐进退避;瞬时 529 秒级抓住恢复,持续过载不打爆网关,到窗口上限明确失败(而非空转几小时)。

## 非目标(YAGNI 边界)

- ❌ 改 `classify_error_for_temporal`(Temporal 实际重试已正确)
- ❌ 改 `PRODUCTION_RETRY`(保持谨慎语义,供未来重任务复用)
- ❌ 改 recon / vuln 的 retry policy(recon 用 Temporal 默认;vuln 用自定义 `maximum_attempts=3`)
- ❌ 模型 / provider 自动降级(接近新功能,超范围)
- ❌ activity 内本地重试循环(Temporal policy 已足够)
- ❌ blackbox 同步(blackbox 未用 `PRODUCTION_RETRY`;用户当前场景为 whitebox)
- ❌ 增强失败 message(GLM 原始「请您稍后再试」提示已在实时日志 turn event 行可见,无需冗余追加)

## 设计

三处改动互相独立,可单独合入;组合后完整解决体感问题。

### 改动 1:Display 关键词补 `529`

**文件**:`packages/core/src/shannon_core/errors/classification.py`

```python
_RETRYABLE_KEYWORDS = (
    "rate limit", "429", "timeout", "network", "ECONN", "billing",
    "transient", "502", "503", "504", "validation", "529",   # ← 新增
)
```

- 消费方仅 `workflow_logger.py:148` 一处,影响面极小。
- `classify_for_temporal`(同文件,产生 display 的 `error_type`)不改 —— 529 走其 fallback 已正确得到 `TRANSIENT + True`,显示 `TransientError` 是对的(529 是过载,非配额限流,`TransientError` 比 `RateLimitError` 更准)。
- 不改 `classify_error_for_temporal`(`models/errors.py`,Temporal 实际重试用)—— 已正确。

### 改动 2:新增 `OVERLOAD_RETRY` policy

**文件**:`packages/core/src/shannon_core/models/retry.py`

```python
OVERLOAD_RETRY = RetryPolicy(
    maximum_attempts=12,
    initial_interval=timedelta(seconds=15),
    maximum_interval=timedelta(seconds=60),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)
```

退避间隔序列(11 个间隔,12 次尝试):

```
15s → 30s → 60s → 60s → 60s → 60s → 60s → 60s → 60s → 60s → 60s
```

退避间隔总和 = `15 + 30 + 60×9 = 585s ≈ 9.75 分钟`。

- `non_retryable_error_types` 复用 `NON_RETRYABLE`(`max_turns` 等执行限制仍不重试)。
- 不改其他 policy 定义。

### 改动 3:pre-recon 改用 `OVERLOAD_RETRY`

**文件**:`packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`

- 第 27 行 import:`PRODUCTION_RETRY` → 增加/替换为 `OVERLOAD_RETRY`(pre-recon 之外若有其它对 `PRODUCTION_RETRY` 的引用则保留)。
- 第 157 行:`retry_policy=PRODUCTION_RETRY` → `retry_policy=OVERLOAD_RETRY`。

仅 pre-recon 的 `run_agent` activity 改用新 policy;其余 activity 不动。

## 退避数值依据

| 参数 | 值 | 理由 |
|---|---|---|
| `initial_interval` | 15s | 瞬时 529 几秒即恢复,15s 给网关喘息且快速探活 |
| `backoff_coefficient` | 2.0 | 标准:`15 → 30 → 60`,平滑过渡到 cap |
| `maximum_interval` | 60s | 持续过载时每分钟最多探一次,避免打爆网关 |
| `maximum_attempts` | 12 | 退避间隔总和 ≈ 9.75 分钟,符合「~10 分钟有界窗口」诉求 |

**墙钟时间说明**:`maximum_attempts=12` 对应**退避间隔总和** ≈ 9.75 分钟。实际到最终失败的墙钟时间还需叠加每次 attempt 的 agent 执行时间 —— 529 时 Claude Code CLI 内部会先重试若干轮(通常每次 1–3 分钟)才返回失败。因此持续过载场景下总墙钟约 **20–40 分钟**。若希望更短的墙钟上限,可调低 `maximum_attempts`(代价是抓住间歇恢复的机会减少)。此数值为默认,实现后可根据真实跑测调整。

## 测试

| 文件 | 新增/扩展断言 |
|---|---|
| `packages/core/tests/errors/test_classification.py` | `is_retryable_for_display(RuntimeError("api_error_status=529")) is True`;并验证 `classify_for_temporal` 对含 `529` 的错误仍 fallback 到 `TRANSIENT`(确保改动只影响 display、不误伤 error_type) |
| `packages/core/tests/test_retry_profiles.py` | `OVERLOAD_RETRY` 存在 + 参数:`maximum_attempts=12`、`initial_interval=15s`、`maximum_interval=60s`、`backoff_coefficient=2.0`、`non_retryable_error_types == NON_RETRYABLE` |
| `packages/whitebox/tests/test_workflows.py` | pre-recon 的 `run_agent` activity 绑定 `OVERLOAD_RETRY`(参照现有 retry_policy 断言模式) |

`packages/core/tests/agents/test_providers.py:test_529_overloaded_transient` 保持原样(本次不改 message,不断言 message 内容)。

## 风险与权衡

- **改动 1 风险极低**:仅 display,单文件单消费点,不影响 Temporal 重试决策。最坏情况:其它含「529」字样的错误也被标 retryable,但 529 本就是可恢复错误,语义正确。
- **改动 2/3 风险低**:新 policy 独立,pre-recon 是只读分析(不改目标、无副作用),快速重试安全。`NON_RETRYABLE` 复用确保 `max_turns` 等确定性失败不会被无谓重试。
- **权衡**:有界窗口(~10 分钟退避 / 20–40 分钟墙钟)意味着高峰期长持续过载时 pre-recon 会明确失败而非无限等待 —— 这是预期行为(用户选择「明确失败」优于「空转几小时」)。失败后可凭 `completed_agents` 机制跳过已完成阶段单独重跑。

## 未来工作(非本次范围)

- 若高峰期 529 持续时间常超窗口,考虑**模型/provider 自动降级**(预先配多模型,某模型持续过载时切换)。
- 将渐进退避 + 有界窗口模式推广到 recon / 其他 LLM agent 的 policy(目前各自使用默认或自定义 policy)。
- 评估 Claude Code CLI 对 529 的内部重试时长,若过长可在 SDK 层调参以缩短单次 attempt 的墙钟开销。
