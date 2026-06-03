# GitManager 对齐设计：Python 重构版 → 原始 TypeScript 版

> 日期：2026-06-03
> 状态：已确认
> 范围：完整对齐原始 `/root/shannon/apps/worker/src/services/git-manager.ts` 的全部能力

---

## 背景

Python 重构项目的 `GitManager`（`packages/core/src/shannon_core/git_manager.py`）仅实现了原始 TypeScript 版约 60% 的功能。当前系统已通过 `asyncio.gather` 并行执行多个漏洞检测/利用 agent，但 GitManager 缺少并发控制和容错机制，存在 `index.lock` 冲突风险。

## 对齐差距总览

| 功能 | 原始 TS | 当前 Python | 对齐目标 |
|------|---------|-------------|----------|
| `isGitRepository` | ✅ | ❌ | ✅ |
| `executeGitCommandWithRetry` | ✅ | ❌ | ✅ |
| `createGitCheckpoint`（两阶段策略）| ✅ | 部分 | ✅ |
| `commitGitSuccess` | ✅ | 简化版 | ✅ |
| `rollbackGitWorkspace`（含 isGit 检查）| ✅ | 简化版 | ✅ |
| `getGitCommitHash` | ✅ | ✅ | ✅ |
| 并发控制（Semaphore/Lock）| ✅ | ❌ | ✅ |
| 锁错误检测 + 重试 | ✅ | ❌ | ✅ |
| 变更文件追踪 | ✅ | ❌ | ✅ |
| 变更摘要日志 | ✅ | ❌ | ✅ |

## 方案选择

**选定方案 A：异步重写** — 全部公共方法改为 `async`，用 `asyncio.Lock` 做并发控制，与 Temporal asyncio 工作流一致。

淘汰方案：
- 方案 B（同步 + threading.Lock）：与 asyncio 架构冲突，会阻塞事件循环
- 方案 C（混合模式）：增加不必要复杂度

---

## 设计

### 1. 新增数据类 `GitResult`

文件：`packages/core/src/shannon_core/models/result.py`（已有，追加）

```python
from dataclasses import dataclass, field

@dataclass
class GitResult:
    """Git 操作的统一返回类型。"""
    success: bool
    changed_files: list[str] = field(default_factory=list)
    error: str | None = None
```

与现有 `WhiteboxScanResult`、`BlackboxScanResult` 同文件，保持结果类型集中管理。

### 2. 公共 API

文件：`packages/core/src/shannon_core/git_manager.py`（重写）

6 个公共异步方法，1:1 映射原始 TS：

| 方法 | 对应 TS | 说明 |
|------|---------|------|
| `is_git_repository(repo_path) → bool` | `isGitRepository` | 检查目录是否为 git 仓库 |
| `create_checkpoint(repo_path, agent_name, attempt) → GitResult` | `createGitCheckpoint` | 两阶段 checkpoint |
| `commit(repo_path, agent_name) → GitResult` | `commitGitSuccess` | 提交成功产物 |
| `rollback(repo_path, reason) → GitResult` | `rollbackGitWorkspace` | 硬重置 + 清理 |
| `get_commit_hash(repo_path) → str \| None` | `getGitCommitHash` | 获取当前 hash |
| `execute_with_retry(repo_path, *args, description, max_retries) → CompletedProcess` | `executeGitCommandWithRetry` | 带重试的 git 命令 |

Logger：复用 `logging.getLogger(__name__)`，不引入 ActivityLogger。

### 3. 并发控制

```python
class GitManager:
    _git_lock: ClassVar[asyncio.Lock] = asyncio.Lock()
```

- `asyncio.Lock` 互斥：同一进程内一次只允许一个 git 操作序列
- `create_checkpoint`、`commit`、`rollback` 内部的多步操作在同一 lock 下执行，保证原子性
- 与 TS 的 `GitSemaphore` 等价（TS 因无 Lock 原语才用 Semaphore 实现互斥）
- 多 worker 进程场景下各自操作独立 workspace，无需跨进程锁

### 4. 重试机制

指数退避重试，仅在检测到 git 锁冲突时触发：

```python
_GIT_LOCK_PATTERNS: ClassVar[list[str]] = [
    "index.lock",
    "unable to lock",
    "Another git process",
    "fatal: Unable to create",
    "fatal: index file",
]
```

退避策略：`delay = 2 ** attempt * 0.5`（0.5s → 1s → 2s → 4s → 8s），最多 5 次。

两层防护关系：
- 外层 `asyncio.Lock`：防止本进程内协程间的 git 冲突
- 内层重试机制：防御外部进程（如用户手动 git）导致的偶发锁冲突

### 5. 两阶段 Checkpoint 策略

```
attempt == 1 → 保留已有变更，直接 add -A + commit
attempt >  1 → 先 reset --hard + clean -fd，再 add -A + commit
```

首次尝试保留变更可避免丢失上一个 agent 的合法产出；重试时清理确保从干净状态开始。

### 6. 变更追踪与日志

- `_get_changed_files(repo_path) → list[str]`：用 `git status --porcelain` 提取变更文件列表
- `_log_change_summary(changed_files, action, max_show=5)`：摘要日志，超过 5 个文件时截断显示
- 日志写入标准 `logging`，与项目现有模式一致

### 7. 错误处理

- 所有 git 异常统一抛 `PentestError`
- 锁冲突等可恢复错误标记 `retryable=True`，让 Temporal RetryPolicy 自动重试
- 现有 `ErrorCode.GIT_CHECKPOINT_FAILED` 和 `GIT_ROLLBACK_FAILED` 复用，不新增

### 8. `is_git_repository` 防护

`rollback` 和 `create_checkpoint` 在操作前检查目标是否为 git 仓库。非 git 仓库时：
- `rollback`：跳过并返回成功（与 TS 行为一致）
- `create_checkpoint`：跳过并返回成功

---

## 调用点改造

`AgentExecutor`（`packages/core/src/shannon_core/agents/executor.py`）中 3 处调用改为 `await` 并接收 `GitResult`：

```python
# 改造前（同步）
GitManager.create_checkpoint(deliverables, agent_name)
GitManager.commit(deliverables, agent_name)
GitManager.rollback(deliverables, "reason")

# 改造后（异步 + 返回值）
checkpoint_result = await GitManager.create_checkpoint(deliverables, agent_name, attempt)
commit_result = await GitManager.commit(deliverables, agent_name)
await GitManager.rollback(deliverables, "reason")
```

AgentExecutor 的 `execute` 方法本身已是 `async`，不需要改签名。

---

## 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `packages/core/src/shannon_core/models/result.py` | 追加 | 新增 `GitResult` 数据类 |
| `packages/core/src/shannon_core/git_manager.py` | 重写 | 同步→异步，新增全部对齐功能 |
| `packages/core/src/shannon_core/agents/executor.py` | 小改 | 3 处调用加 `await`，处理返回值 |
| `packages/core/tests/test_git_manager.py` | 重写 | 适配异步 API，新增并发/重试测试 |

---

## 测试策略

使用 `pytest-asyncio` + `unittest.mock` 覆盖以下场景：

1. **基础功能**：checkpoint / commit / rollback / get_commit_hash 正常流程
2. **两阶段策略**：验证 attempt=1 保留变更，attempt>1 先清理
3. **并发安全**：多个协程同时调用 GitManager，验证 lock 互斥
4. **重试机制**：模拟锁冲突，验证指数退避重试
5. **is_git_repository**：非 git 目录的优雅降级
6. **变更追踪**：验证 changed_files 正确返回
7. **错误处理**：非零返回码 → PentestError，retryable 标记正确
