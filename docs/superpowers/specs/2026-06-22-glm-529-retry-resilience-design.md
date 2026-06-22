# GLM 529 display 标签修复设计

> **范围收窄说明**:本 spec 原名「GLM 529 过载重试韧性修复」,曾包含 retry policy 改动(新增 `OVERLOAD_RETRY` + pre-recon 改用)。后经查 TS 原版(`/root/shannon/apps/worker/src/temporal/workflows.ts`)发现 pre-recon 用 `PRODUCTION_RETRY`(5min)是 **TS 故意设计**(所有 agent 经 `acts` proxy 统一配 PRODUCTION),非 PY bug —— 该部分前提错误,已撤回。本 spec 现仅保留 display 标签修复(独立真 bug)。retry policy 的 PY 偏离问题另开 spec,见末尾「关联」。

- **日期**:2026-06-22
- **分支**:`feat/fork-py`
- **状态**:待实现
- **关联**:`2026-06-09-sdk-result-handling-hardening-design.md`(SDK result failure 处理硬化,上游);retry policy 对齐 TS(另开 spec)

## 背景与问题

一次 whitebox 扫描(`shannon-whitebox start --repo /root/code/backend/honor`)在 pre-recon 阶段失败,日志:

```
💭 [Agent] Turn 1: API Error: 529 [1305][该模型当前访问量过大,请您稍后再试]...
✗ pre-recon failed (2m 55s) — SDK result failure: subtype=success, api_error_status=529
ERROR PentestError: SDK result failure: subtype=success, api_error_status=529 (context: pre-recon) [TransientError · non-retryable]
```

用户把并发降到 2 仍触发,且看起来"直接死掉",于是中止。

## 根因分析

### 1. 529 是 GLM 服务端模型过载(背景,非客户端 bug)

`529 [1305]` 的语义是「该模型(服务端模型实例)当前访问量过大」,是网关级全局过载信号,不是针对单个账号/并发数的配额限流(那是 `429`)。降到 1 并发在高峰期同样触发。

`SHANNON_MAX_CONCURRENT` 只控制 vuln 阶段多 exploit agent 的并发,**pre-recon 是单 agent 顺序执行,根本不经过该 semaphore**。所以并发设置对 pre-recon 的 529 完全不起作用。

### 2. Display 标签误报 `non-retryable`(真 bug,本次唯一修复点)

529 经 `_classify_result_failure`(`providers_anthropic.py:410`)正确判为 `("TransientError", True)` → 经 executor/activities 最终成 `ApplicationFailure(type="AgentExecutionError", non_retryable=False)` → 走 `PRODUCTION_RETRY`,`AgentExecutionError` 不在 `NON_RETRYABLE_TYPES`,**Temporal 本应重试**。

但 live display 显示的 `[TransientError · non-retryable]` 来自 `errors/classification.py:is_retryable_for_display`,其 `_RETRYABLE_KEYWORDS` 为 `("rate limit","429","timeout","network","ECONN","billing","transient","502","503","504","validation")` —— **漏了 `529`**。错误 message `SDK result failure: subtype=success, api_error_status=529` 不含任何列表内关键词,触发 fail-safe `return False` → 显示 `non-retryable`。

**结论**:display 标签与 Temporal 实际重试行为相反,误导用户以为「不可重试、直接失败」,叠加 pre-recon 的长 backoff(见下)导致用户中止。

### 3. pre-recon 的 5min backoff 是 TS 设计,不是 bug(澄清,不在本次范围)

pre-recon 绑定 `PRODUCTION_RETRY`(`workflows.py:157`,`initial_interval=5min`)。曾误判为「pre-recon 误用 PRODUCTION」,但查 TS 原版后纠正:TS 用 `proxyActivities` 创建统一 proxy(`acts`),**所有 agent(pre-recon/recon/vuln/report)都走 PRODUCTION_RETRY(5min/50次)**,注释明说「long intervals for billing recovery」。PY 的 pre-recon 与 TS 一致,不是 bug。

PY 真正偏离 TS 的是 **recon / vuln / 后处理漏配 retry policy**(落到 Temporal 默认 `maximum_attempts=0`/`initial_interval=1s`),但那是独立的更大问题,另开 spec(见末尾)。

> 注:`subtype=success` 却带 `api_error_status=529` 是 Claude Code CLI 内部重试 GLM 529 失败后的怪异返回;但因 `is_error=True` + `api_error_status=529`,分类逻辑仍正确识别,不需处理。

## 目标

1. **Display 如实**:529 错误显示 `retryable`,与 Temporal 实际重试一致,不再骗用户「挂了」。

## 非目标(YAGNI 边界)

- ❌ retry policy 改动(pre-recon 用 PRODUCTION 是 TS 设计;recon/vuln/后处理漏配另开 spec)
- ❌ 改 `classify_for_temporal`(同文件,产生 display `error_type`)—— 529 走其 fallback 已正确得到 `TRANSIENT + True`
- ❌ 改 `classify_error_for_temporal`(`models/errors.py`,Temporal 实际重试用)—— 已正确
- ❌ 增强失败 message(GLM 原始「请您稍后再试」提示已在实时日志 turn event 行可见)

## 设计

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

## 测试

| 文件 | 新增断言 |
|---|---|
| `packages/core/tests/errors/test_classification.py` | `is_retryable_for_display(RuntimeError("api_error_status=529")) is True`;并验证 `classify_for_temporal` 对含 `529` 的错误仍 fallback 到 `TRANSIENT`(确保改动只影响 display、不误伤 error_type) |

`packages/core/tests/agents/test_providers.py:test_529_overloaded_transient` 保持原样。

## 风险与权衡

- **风险极低**:仅 display,单文件单消费点,不影响 Temporal 重试决策。最坏情况:其它含「529」字样的错误也被标 retryable,但 529 本就是可恢复错误,语义正确。

## 关联:retry policy 偏离 TS(另开 spec,不在本次范围)

本次调查中发现 PY 的 retry policy 整体偏离 TS,是独立的更大问题:

| agent | TS retry | PY retry | 一致? |
|---|---|---|---|
| 预检 / 鉴权 | PREFLIGHT / AUTH(10s/3) | 同 | ✓ |
| pre-recon | PRODUCTION(5min/50)via `acts` | PRODUCTION(5min/50) | ✓ |
| recon | PRODUCTION(5min/50)via `acts` | **Temporal 默认(1s/无限)** | ✗ 漏配 |
| vuln | PRODUCTION(5min/50)via `acts` | 自定义(3次/1s) | ✗ 偏离 |
| 后处理(merge/route/risk/report 10+) | PRODUCTION(5min/50)via `acts` | **Temporal 默认(1s/无限)** | ✗ 漏配 |

TS 用 `proxyActivities` 统一给所有 agent 配 PRODUCTION_RETRY;PY 迁移时只给 pre-recon 配了,其余漏配落到 Temporal 默认(`maximum_attempts=0`= 无限、`initial_interval=1s`)—— 持续过载时会 1s 疯狂重试打爆网关 + 无限空转直到 2h 超时,比 pre-recon 的 5min 更危险。

这需要单独 spec 决定:对齐 TS(全 PRODUCTION 5min)还是针对 LLM agent 优化(529 服务端过载 vs billing 429 区分退避)。不在本 spec 范围。
