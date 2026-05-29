## Why

`validateDeliverablesExist`（blackbox-only 启动校验）和 `validateQueueSafe`（exploit 阶段校验）对 `*_exploitation_queue.json` 的格式理解不一致。前者期望顶层是 JSON 数组，后者正确期望 `{"vulnerabilities": [...]}` 对象格式。导致 blackbox-only 模式即使在 overlay 修复后仍无法启动——所有 queue 文件被判为空。

## What Changes

- 修改 `activities.ts` 中的 `validateDeliverablesExist`，用 `validateQueueSafe` 替代自写的 JSON 解析逻辑
- 删除 `validateDeliverablesExist` 中手写的 `JSON.parse` + `Array.isArray` 检查
- 复用 `queue-validation.ts` 已有的完整校验管线（文件存在性、JSON 格式、vulnerabilities 数组）

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

(none — 纯内部 bug 修复，不改变外部行为契约)

## Impact

- **`apps/worker/src/temporal/activities.ts`**: `validateDeliverablesExist` 函数重写内部逻辑
- **`apps/worker/src/services/queue-validation.ts`**: 已有代码，不改动（被引用）
- **无 API / 配置 / Docker 变更**
