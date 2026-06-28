# CLI workflow 失败友好展示 — 设计

> 日期：2026-06-28
> 分支：`feat/fork-py`
> 状态：设计中（brainstorming 已确认方案 A，待写实现 plan）

## 1. 背景

用户跑黑盒扫描：

```
uv run shannon-blackbox start --url http://localhost:4000 --repo .../NodeGoat -w ... --rerun
```

得到一大坨 temporalio 内部堆栈，根因埋在最深处：

```
temporalio.exceptions.ApplicationError: PentestError: Target http://localhost:4000 resolves to loopback address 127.0.0.1
The above exception was the direct cause of the following exception:
temporalio.exceptions.ApplicationError: InvalidTargetError: ...
The above exception was the direct cause of the following exception:
temporalio.exceptions.ActivityError: Activity task failed
... (一路 WorkflowFailureError + 整条 Python traceback)
```

## 2. 根因分析

**错误本身不是 bug，是安全设计。** `http://localhost:4000` 能解析（→ `127.0.0.1`），是黑盒 preflight 的 SSRF 防护主动拦下了 loopback 地址（`packages/core/src/shannon_core/utils/security.py` 的 `check_loopback()` → 抛 `PentestError(error_code=TARGET_UNREACHABLE)`）。pentest 工具禁止扫 loopback/内网，防止误伤本机服务或被当成 SSRF 跳板。**该保护保持不变。**

**真正的 UX 缺陷在展示链路：**

1. preflight activity 抛 `PentestError`，activity 的 except 块**已经**用 `classify_error_for_temporal` 把它转成 `ApplicationFailure(type="InvalidTargetError", non_retryable=True)`（`packages/blackbox/src/shannon_blackbox/pipeline/activities.py`）——语义信息一路都在
2. temporalio 把它层层包装：`ApplicationError` → `ActivityError` → `WorkflowFailureError`，每层加一句 "The above exception was the direct cause of…"
3. `worker.py` 的 `except Exception as e:` 接住，调失败收尾后 **`raise` 重抛**
4. CLI `main()` 顶层**没有任何 try/except**（黑盒 `cli/main.py`、白盒 `cli/main.py` 都是裸 `asyncio.run(run_scan(...))`）→ Python 默认把整条 traceback 打到终端

**讽刺点**：`InvalidTargetError` 这个语义一路上都在，CLI 层却完全没接住、没用它做友好展示。

## 3. 目标 / 非目标

### 目标
- 黑白盒 CLI 在 workflow 失败时打印**一行人话**（诊断 + 后续建议），不再裸抛 temporalio 堆栈
- 复用一路带着的语义分类（`ApplicationFailure.type` / `classify_error_for_temporal`）
- 完整 traceback 不丢：落 `activity_failures.log`，`--debug` 时也能在终端看
- 黑白盒共享同一个渲染层（抽到 `shannon_core`）

### 非目标（明确不做）
- 不改 loopback / SSRF 安全策略（保持禁止）
- 不加 `--allow-private` 之类放行开关（用户明确不要）
- 不改 worker 状态机（继续 `raise`）、不改 activity 层（已正确分类）、不动 temporalio retry policy
- 不为每个 error_type 写定制提示（仅高频的 `InvalidTargetError` 给定制，其余通用兜底）

## 4. 方案选择

选定 **方案 A：CLI 顶层 try/except + core 共享渲染函数**。

| 方案 | 做法 | 结论 |
|---|---|---|
| **A（选定）** | core 加共享渲染函数；黑白盒 CLI 的 `asyncio.run(run_scan(...))` 外各包一层 try/except 调它 | 直击根因、改动最小、最通用；worker 状态机不动 |
| B | worker 不再 `raise`，改把根因塞进返回的 failed state，复用 CLI 已有的 `Scan failed:` 分支 | 要对齐黑白盒两套 state 结构（黑盒 `BlackboxPipelineState` / 白盒 dict）、丢失异常对象只剩 str、白盒还得补 except 块 |
| C | A + B 结合 | 最稳但动两层，本次过度设计 |

选 A 的理由：根因就是"CLI 顶层无 try/except 导致裸 traceback"，A 最直接最小；复用了已经一路带着的语义分类；且通用覆盖所有 workflow 失败类型（不只 loopback）；worker 保持"异常即失败"语义不动，降低耦合。

## 5. 详细设计

### 5.1 数据流

```
preflight activity 抛 PentestError
  → activity except 转成 ApplicationFailure(type=error_type, non_retryable)   [已有，不动]
  → temporalio 层层包装成 WorkflowFailureError (cause 链)
  → worker.run_scan except 块: log_workflow_complete(failed); raise            [已有，不动]
  → CLI start 的 try/except 接住                                               ★新增
      ├─ format_workflow_failure(exc) → 友好串                                 [core 新增]
      │     ├─ extract_root_cause(exc): 沿 __cause__ 链挖最深层
      │     │     ├─ 取 ApplicationFailure.type (优先)
      │     │     └─ 兜底: classify_error_for_temporal(最深层异常)
      │     └─ 查 FRIENDLY_HINTS[type] → 人话诊断 + 建议
      ├─ persist_workflow_traceback(exc, workspace_dir): 完整堆栈 append 到 activity_failures.log
      ├─ click.echo(友好串)
      ├─ --debug 时: 额外 traceback.print_exc() 到 stderr
      └─ raise SystemExit(1)
```

### 5.2 组件

**新增 `packages/core/src/shannon_core/cli/error_render.py`**

- `RootCause`（dataclass）：`error_type: str`、`message: str`
- `extract_root_cause(exc) -> RootCause`：遍历 `__cause__` 链到最深层；优先取带 `.type` 属性的 temporalio 异常的 `type`；`type` 缺失时对最深层异常跑 `classify_error_for_temporal` 兜底分类；`message` 取最深层异常的 `str()`
- `FRIENDLY_HINTS: dict[str, str | Callable[[str], str]]`：error_type → 提示文案
  - `InvalidTargetError` 用 callable，按 message 子串区分 loopback / SSRF / 不可解析三支
  - 其余 error_type 用静态串
  - 未命中走通用兜底模板
- `format_workflow_failure(exc) -> str`：组装多行友好串（✗ 诊断 + 建议 + 完整错误已记录到 `<path>`）
- `persist_workflow_traceback(exc, workspace_dir) -> Path | None`：append 完整 traceback 到 `<workspace>/activity_failures.log`（复用 `generate_workflow_log_path` 重算路径）；`workspace_dir` 不可得时返回 `None`（best-effort，不抛）

**改 `packages/blackbox/src/shannon_blackbox/cli/main.py` 与 `packages/whitebox/src/shannon_whitebox/cli/main.py` 的 `start` 命令**

- `start` 签名加 `--debug` flag（`is_flag=True`，help：失败时在终端打印完整堆栈）
- `asyncio.run(run_scan(...))` 外包 `try/except Exception as e`
- except 内：
  1. `persist_workflow_traceback(e, workspace_dir)` 落盘（best-effort）
  2. `click.echo(format_workflow_failure(e))`
  3. `if debug: traceback.print_exc()`（到 stderr）
  4. `raise SystemExit(1)`

### 5.3 友好提示映射表

| error_type | 触发场景 | 文案（诊断 + 建议） |
|---|---|---|
| `InvalidTargetError`（message 含 "loopback"） | 本次案例 | 目标解析到本机 loopback 地址 127.0.0.1。黑盒扫描不允许扫 loopback/内网地址（SSRF 防护）。建议：用公网地址，或目标容器在宿主网络可达的地址。 |
| `InvalidTargetError`（"SSRF-sensitive"） | SSRF 网段 | 目标解析到 SSRF 敏感网段（169.254.x.x）。建议：换非链路本地地址。 |
| `InvalidTargetError`（"Cannot resolve"） | DNS 不通 | 无法解析目标域名。建议：检查 URL 拼写 / DNS / 目标是否启动。 |
| `ConfigurationError` | config / 缺文件 | 配置或必要文件有问题（见详情）。建议：检查 profile / config 文件。 |
| `AuthenticationError` | API key 错 | 鉴权失败。建议：检查 API key / profile 配置。 |
| `GitError` | git 操作失败 | Git 操作失败（见详情）。建议：检查仓库路径 / git 可用性。 |
| 其它 / 未命中 | 通用兜底 | 扫描因 {error_type} 失败：{原始 message}。加 --debug 可在终端查看完整堆栈。 |

### 5.4 traceback 落点

- 默认 append 到 `<workspace>/activity_failures.log`（与白盒 Part A 已有约定一致，用户只看一个错误文件）
- workspace 路径从各 CLI 可得的上下文取，两套取法不同：
  - 黑盒：`input.workspaces_root` + `input.workspace_name`
  - 白盒：`resolve_workspaces_dir(input.repo_path)` + `input.workspace_name`（白盒 `PipelineInput` 无 `workspaces_root` 字段，由 `repo_path` 推导）
- **任一 CLI 取不到 workspace 路径**（如 standalone 黑盒无 `-w` 无 repo、或 worker 自建 session 而 CLI 不知名）→ 跳过落盘，友好串提示"加 --debug 看堆栈"（已知降级，可接受）

### 5.5 `--debug` flag

- 黑白盒 CLI **均无**现成 debug / verbose flag，新增 `--debug`（`is_flag=True`）
- 语义：扫描失败时，除友好串外，额外把完整 Python traceback 打到 stderr
- 默认关闭（终端零堆栈）

## 6. 测试策略

- **`error_render` 单测**：
  - 根因提取（多层 `__cause__` 链，优先 `.type`，兜底 `classify_error_for_temporal`）
  - 各 error_type 映射命中正确文案
  - `InvalidTargetError` 按 message 子串分三支（loopback / SSRF / 不可解析）
  - 未命中走通用兜底
- **CLI `CliRunner` 测试**（黑白盒各一）：
  - mock `run_scan` 抛构造的 `WorkflowFailureError`（带嵌套 `ApplicationFailure(type="InvalidTargetError", message含loopback)`）
  - 断言：**不裸抛**、stdout 含友好串（"loopback"/"本机"/"建议"）、exit code=1
  - `--debug` 时 stderr 含完整 traceback
- **落盘测试**：断言 `activity_failures.log` 被 append（含 traceback）
- **AST 防回归锚点**（参考 sandbox AST 守卫风格，`test_*_cli_error_handling.py`）：黑白盒 CLI 的 `run_scan(...)` 调用必须位于 `try` 块内，防止日后有人删掉 try/except 导致裸抛回归

> 测试只跑本次改动相关文件，勿跑全套（见 CLAUDE.md「测试陷阱」）。

## 7. 范围边界 / 已知降级

- worker、activity、temporalio retry policy **都不动**
- standalone 黑盒（无 `-w` 无 repo）traceback 不落盘（无 workspace 路径），仅 `--debug` 终端可见
- temporalio 的 cancel/timeout 路径（`ScanCancelled` 已有独立 `SystemExit(130)` 处理）不受影响
- `ScanCancelled` 不进友好展示分支（它是用户主动取消，非失败）

## 8. 不变量 / 防回归

1. **CLI 顶层必须捕获 `run_scan` 异常** —— AST 锚点锁定（黑白盒 `run_scan(...)` 调用位于 `try` 内）
2. **loopback / SSRF 保护不变** —— `security.py` 不在本改动范围
3. **语义分类单一来源** —— 友好展示复用 `classify_error_for_temporal`，不另造分类逻辑
4. **traceback 不丢** —— 落盘或 `--debug` 至少二选一可见
