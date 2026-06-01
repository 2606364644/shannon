## Context

`validateDeliverablesExist`（`activities.ts:1009`）是 blackbox-only workflow 的第一步预检，用于筛选哪些 vulnType 有非空的 exploitation queue。它自写了 JSON 解析逻辑，期望 `*_exploitation_queue.json` 顶层是数组。

但 queue 文件的实际格式是 `{"vulnerabilities": [...]}` 对象，由 LLM agent 生成，格式定义在 `queue-validation.ts` 的 `QueueData` 接口中。exploit 阶段用的 `validateQueueSafe` 已正确处理此格式。

```
当前数据流（blackbox-only）:

  validateDeliverablesExist          checkExploitationQueue
  (activities.ts:1009)               (activities.ts:595)
  自写: Array.isArray(parsed)        → validateQueueSafe()
  ❌ 对象格式不识别                    ✅ 正确解析
         │                                    │
         VulnType[]                     ExploitationDecision
         │                                    │
         └───── vulnTypesWithQueues ──────────┘
                          │
                 exploit agents (per vulnType)
```

## Goals / Non-Goals

**Goals:**

- 消除两处校验逻辑的格式不一致
- 复用 `validateQueueSafe` 的完整校验管线
- 保持 blackbox-only workflow 的返回值语义不变（`VulnType[]`）

**Non-Goals:**

- 不修改 `queue-validation.ts` 本身
- 不修改 blackbox workflow 的编排逻辑
- 不修改 queue 文件的生成格式

## Decisions

### Decision 1: 用 `validateQueueSafe` 替代自写解析

**选择:** 在 `validateDeliverablesExist` 内部，对每个 `ALL_VULN_CLASSES` 调用 `validateQueueSafe(vulnType, delivPath)`，根据 `shouldExploit` 筛选。

**替代方案:** 只修 `JSON.parse` 那两行，改为读 `parsed.vulnerabilities`。

**理由:** 方案 A 彻底消除一致性隐患。`validateQueueSafe` 还会校验 deliverable 文件存在性（对称校验），这对 blackbox-only 也是合理的——如果没有 deliverable 文件，exploit agent 也没有上下文可读。方案 B 只修表面，未来格式再变两处又不同步。

### Decision 2: 错误处理策略

`validateQueueSafe` 返回 `Result<ExploitationDecision, PentestError>`。非 retryable 错误（如文件缺失）应跳过该类型；retryable 错误（如 JSON 格式损坏）应重新抛出让 Temporal 重试。这与当前 `ExploitationCheckerService.checkQueue` 的处理方式一致。

## Risks / Trade-offs

- **[重复校验]** → `validateDeliverablesExist` 和后续 `checkExploitationQueue` 都会读同一个 queue 文件。两次 I/O 开销可忽略（本地文件系统，文件 < 100KB）。
- **[deliverable 文件缺失导致跳过]** → `validateQueueSafe` 要求 deliverable 和 queue 同时存在。在 overlay 修复后两者都已复制，不会触发。如果用户手动删除了 deliverable 文件，跳过是正确行为。
