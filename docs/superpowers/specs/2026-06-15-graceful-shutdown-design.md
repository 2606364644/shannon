# Ctrl+C 优雅退出设计：全链路取消 + 共享运行时

> 日期: 2026-06-15 | 状态: 设计待评审
>
> 关联文档:
> - 三个扫描入口 worker: [`packages/whitebox/src/shannon_whitebox/worker.py`](../../../packages/whitebox/src/shannon_whitebox/worker.py)、[`packages/blackbox/src/shannon_blackbox/worker.py`](../../../packages/blackbox/src/shannon_blackbox/worker.py)
> - combined 编排: [`packages/combined/src/shannon_combined/orchestrator.py`](../../../packages/combined/src/shannon_combined/orchestrator.py)
> - 子进程清理依赖（已存在）: [`code_index/gitnexus_mcp.py`](../../../packages/core/src/shannon_core/code_index/gitnexus_mcp.py)（`async with` → `stop()`）、[`blackbox pipeline/workflows.py`](../../../packages/blackbox/src/shannon_blackbox/pipeline/workflows.py)（`finally:` → playwright cleanup）

---

## 1. 背景与问题

`shannon-whitebox start` / `shannon-blackbox start` / `shannon scan`(combined) 三个扫描入口都跑在 **Temporal workflow** 上：入口经 click → `asyncio.run(run_scan(...))`，内部连接 Temporal server、起 `Worker`、启动一个长 workflow，然后 `await handle.result()`（一次扫描可能运行数十分钟）。

当前按 Ctrl+C 时，终端会**刷出一大堆错误堆栈后才退出**，且伴随副作用：

- 全项目**没有任何 SIGINT 处理**（`grep SIGINT/signal` 仅命中两处无关的「result-failure signal」误匹配，非 Unix 信号）。
- `KeyboardInterrupt` 是 `BaseException`，`worker.py` 里的 `except Exception:` 完全不捕获它 → `poll_task` 不会被优雅取消。
- `asyncio.run()` 关闭 event loop 时取消所有 pending 协程，`CancelledError`、gRPC channel 关闭、worker shutdown 异常**一股脑全打出来**。
- gitnexus mcp / playwright 等**子进程可能孤儿化残留**（`.playwright*/`、`gitnexus mcp`）。
- Temporal **server 端 workflow 仍在继续跑**——本地只是断了 client/worker，下次再起可能冲突或重复计费。

## 2. 目标 / 非目标

**目标**

1. Ctrl+C 时**不再刷错误堆栈**，打印一行干净的「正在取消…」，优雅退出。
2. **全链路取消**：本地清理（关闭 worker/client、停 poll、卸载 handler）**+ 向 Temporal 发 `handle.cancel()`**，让 server 端扫描也真正停下。
3. **双击语义**：第 1 次 Ctrl+C 优雅取消；取消过程中第 2 次 Ctrl+C 立即强制退出。
4. 三个扫描入口（whitebox / blackbox / combined）统一接入；抽取共享运行时，消除三处 `run_scan` / `poll_workflow_progress` 的复制粘贴。

**非目标（本次不做）**

- 可恢复的中断（cancel 后自动 resume）——Temporal 本身支持 continue-as-new，但 resume 交互是独立工程，不纳入。
- `logs --follow`、`infra up` 等其它长跑命令（结构不同、资源轻，后续按需）。
- Windows 平台（`loop.add_signal_handler` 仅 Unix；项目跑在 darwin/linux + docker）。
- SIGQUIT / SIGKILL 处理（不可捕获，无意义）。

## 3. 现状根因（已验证）

1. **无信号处理**：入口 `asyncio.run(run_scan(...))` 直接裸跑，SIGINT 走 Python 默认 handler → 抛 `KeyboardInterrupt`。
2. **`KeyboardInterrupt` 绕过清理**：`worker.py` 的 `try/except Exception` 捕不到它（`BaseException`），`async with worker:` 的 `__aexit__` 与 `poll_task` 取消逻辑被跳过。
3. **阻塞式等待**：`await handle.result()` 一行阻塞，中断只能靠外部硬打断，无法「主动醒来走取消流程」。
4. **临时资源清理路径已存在但被绕过**（清理的是临时注入配置 + 子进程，**不删 deliverables/workflow.log**）：
   - 子进程：gitnexus 用 `async with GitNexusMCPClient(...)`（`activities.py:203`），`__aexit__` 调 `stop()`（`terminate()` + `wait()`）；playwright 浏览器由 `engine.cleanup_config` 关闭。
   - 临时注入配置：`cleanup_settings` / `engine.cleanup_config` / `cleanup_auth_state_sync`（删注入目标仓库的 settings、playwright session 配置、auth-state.json；**正常完成也删**，目的是还原战场）。
   - **两个 workflow 都已有 `except CancelledError`**（whitebox `workflows.py:265-268`、blackbox 同构）→ 设 `status="cancelled"` 并跑 `finally` 清理。当前 `KeyboardInterrupt` 绕过它们，临时配置反而可能残留——优雅退出正好修复这点。
5. **combined 误判**：`orchestrator.py:40` `if wb_result.get("status") != "completed"` 会把任何非 completed（含将来的 cancelled）一律当 failed。

> 结论：根因不是「缺一个 try/except」，而是「缺协作式取消入口」。优雅退出的价值在于让 workflow 收到 cancel，从而触发各 activity/workflow **已有的**清理代码，并让 `asyncio.run` 干净结束（而非被 `KeyboardInterrupt` 打断）。

## 4. 设计

### 4.1 架构

新增 `packages/core/src/shannon_core/runtime/scan_runner.py`，把现在三个 worker 复制粘贴的「连接 → 起 worker → start workflow → 轮询 → 等结果 → 取消」整坨收口，并叠加 SIGINT 双击处理。三个 worker 的 `run_scan` 改为调用它；combined 的 orchestrator 不动 worker 调用，自动受益。

**`run_scan` 边界划分**（关键决策）：
- `scan_runner` **只管 Temporal 连接 + workflow + 信号**这一层。
- workspace 创建、deliverables 后处理留在各自 worker（whitebox 特有）。
- **临时资源清理**（临时注入配置 + 子进程）由 activity/workflow **已有的** `async with` / `finally` 负责，由 `handle.cancel()` 触发；`scan_runner` 不直接管这些。
  - **清理不删 deliverables（扫描结果）和 workflow.log（日志）**：deliverables 由各 activity 增量写入并保留，workflow.log 已写内容保留，Ctrl+C 只是停止后续 activity 的产出。
  - 实际被清理的是：①临时注入目标仓库的配置（`cleanup_settings`、`engine.cleanup_config`、`cleanup_auth_state_sync`——正常完成也会删，目的是还原战场）；②子进程（gitnexus `async with` 的 `stop()`、playwright 浏览器）。

### 4.2 组件接口

**`ShutdownController` —— 双击语义**

```python
class ShutdownController:
    def install(self, loop) -> None   # loop.add_signal_handler(SIGINT/SIGTERM, ...)
    def is_set(self) -> bool          # 优雅退出是否已触发
    async def wait(self) -> None      # await 到中断事件
    def uninstall(self) -> None       # 还原（移除 handler）
```

- 第 1 次 SIGINT：set 事件 + 打印 `正在优雅取消…（再按一次 Ctrl+C 立即退出）`。
- 第 2 次 SIGINT：`os._exit(130)` 立即终止（绕过 asyncio 清理，这正是「强制」语义；无需还原 handler）。
- SIGTERM（`docker stop` / `kill` 默认信号）：直接 set 事件走优雅退出，**不参与双击计数**（确定性终止）。

**`run_scan_graceful(...)` —— 统一封装**

```python
class ScanCancelled(Exception): ...   # 预期中断信号

async def run_scan_graceful(
    *,
    temporal_address: str,
    task_queue_prefix: str,
    workflow_cls,                     # WhiteboxScanWorkflow / BlackboxScanWorkflow
    workflow_input,                   # PipelineInput / BlackboxPipelineInput
    activities: list,
    progress_total: int = 13,         # 进度显示 M/total
    cancel_grace_seconds: float = 15, # cancel 后本地等待上限
) -> Any:                             # 成功返回 workflow result；被取消抛 ScanCancelled
```

`poll_progress(handle, total)` 一并内化（取代三处复制的 `poll_workflow_progress`），仍每 30s `print()` 一行（live 仪表盘接入是另一 spec 的范围）。

### 4.3 取消与清理时序

核心控制流（把现在的 `await handle.result()` 阻塞改成可被中断唤醒）：

```python
ctrl = ShutdownController(); ctrl.install(asyncio.get_running_loop())
async with worker:
    handle = await client.start_workflow(...)
    poll_task = asyncio.create_task(poll_progress(handle, progress_total))
    result_task = asyncio.ensure_future(handle.result())
    shutdown_wait_task = asyncio.create_task(ctrl.wait())
    try:
        await asyncio.wait(
            {result_task, shutdown_wait_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if ctrl.is_set():                      # ← 中断唤醒，主动走取消流程
            click.echo("正在取消 Temporal workflow…")
            try:
                await handle.cancel()          # 协作式 cancel
            except Exception as e:
                click.echo(f"cancel 请求失败（忽略）: {e}")
            try:
                await asyncio.wait_for(result_task, timeout=cancel_grace_seconds)
            except asyncio.TimeoutError:
                click.echo(f"{cancel_grace_seconds}s 内 workflow 未响应取消，放弃等待"
                           f"（server 端 cancel 仍生效）")
            raise ScanCancelled()
        return result_task.result()            # 正常完成
    finally:
        for t in (poll_task, shutdown_wait_task):   # 正常完成路径也要回收 shutdown_wait_task
            t.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await t
        ctrl.uninstall()
# async with worker 退出 → worker.shutdown() 干净关闭 gRPC channel（消除报错刷屏的关键）
```

**第 1 次 Ctrl+C 完整时序**：

1. SIGINT → `ShutdownController` set 事件 + 打印提示。
2. 主协程从 `asyncio.wait` **主动醒来**（非外部打断）→ 进入取消分支。
3. `handle.cancel()` → Temporal server 给 workflow 发 cancel。
4. workflow 在 activity 边界收到 `CancelledError` → workflow 的 `except CancelledError` 设 `status="cancelled"`；activity 内 `async with GitNexusMCPClient` 的 `__aexit__` 执行 `stop()`（gitnexus 子进程）；workflow `finally` 执行 `cleanup_settings` / `engine.cleanup_config` / `cleanup_auth_state_sync`（清理临时注入配置，**不删 deliverables/log**）。
5. workflow 结束 → `result_task` 完成 → `run_scan_graceful` 抛 `ScanCancelled`。
6. `finally`：取消 `poll_task`、`uninstall` handler。
7. `async with worker` 的 `__aexit__` → `worker.shutdown()` 干净关闭 gRPC channel。
8. worker 的 `run_scan` 捕获 `ScanCancelled` → 返回 `{"status": "cancelled", ...}` → `asyncio.run` 干净结束 → 进程退出码 **130**。

**第 2 次 Ctrl+C**：handler 第二次触发 → `os._exit(130)`。

### 4.4 错误处理与退出码

| 场景 | 处理 |
|---|---|
| `Client.connect` 失败（Temporal 没起） | 不属优雅退出范畴，异常照旧上抛（退出码 1） |
| `handle.cancel()` 抛异常 | 捕获 + 打印警告，继续走本地清理 |
| `worker.shutdown()` 抛异常 | 捕获 + 打印，不阻塞退出 |
| `ScanCancelled`（预期中断） | `run_scan` 转 `{"status": "cancelled"}` + 退出码 **130** |
| workflow 真正失败（非中断） | 走原有 error 路径，退出码 1 |

**退出码**：正常完成 0；被 Ctrl+C 优雅取消 **130**（= 128 + SIGINT(2)）；第二次 Ctrl+C 强制 `os._exit(130)`。

**cancel 超时语义**（已决策：超时放弃等待，不 escalate）：`cancel_grace_seconds`（默认 15s）是**本地等待上限**，不是 server 取消时限。超时后本地不再阻塞、继续走本地清理与退出；server 端 cancel 请求仍有效，会在 activity 自然结束后取消。不调 `handle.terminate()`——terminate 会跳过 workflow `finally`，导致 gitnexus/playwright 子进程残留，与「优雅」相悖。

**返回类型兼容性**（实现注意）：blackbox 的 `run_scan` 正常路径返回 `BlackboxPipelineState`（dataclass），whitebox 返回 dict。被取消时两者统一返回 `{"status": "cancelled"}` dict。消费方（CLI、orchestrator）在判断 `status` 前先把结果归一化为 dict（orchestrator 已有 `asdict` 逻辑，line 67-71；blackbox CLI 需同步兼容）。

### 4.5 CLI / orchestrator 改动

三个入口的 start/scan 命令现在只有 `completed` / `failed` 分支，各加 `cancelled` 分支：

```python
# 例：whitebox cli/main.py start
if result.get("status") == "cancelled":
    click.echo("Scan cancelled.")
    raise SystemExit(130)
elif result.get("status") == "completed":
    ...
```

combined 的 `orchestrator.py:40` 现把所有非 completed 当 failed，需区分 cancelled：whitebox 阶段返回 cancelled 时短路返回 `{"status": "cancelled", "phase": "whitebox"}`（**不**伪装成 failed、**不**继续 blackbox）；blackbox 阶段同理。combined `cli/main.py` 的 scan 命令同步加 cancelled → `SystemExit(130)`。

## 5. 测试策略

**单元测试（core，纯逻辑，mock Worker/Client/handle）** —— 新增 `packages/core/tests/runtime/test_scan_runner.py`：

1. `ShutdownController`：第 1 次 SIGINT → `is_set()` True、event set；第 2 次 → 调到 `os._exit`（mock 验证，不真退出）；SIGTERM → 直接优雅；`uninstall` 还原。
2. `run_scan_graceful` 正常完成：workflow 完成 → 返回 result、不抛 `ScanCancelled`、handler 未触发。
3. `run_scan_graceful` 中断路径：shutdown event 触发 → `handle.cancel()` 被调、`poll_task` 被取消、`worker.shutdown()` 被调（`async with` 退出）、抛 `ScanCancelled`。
4. cancel 超时：`result_task` 超时不完成 → 打印放弃等待、仍抛 `ScanCancelled`。
5. `handle.cancel()` 抛异常 → 被捕获、仍抛 `ScanCancelled`、不向上漏。

**集成测试**（扩展现有 `test_runner.py` / `test_temporal_infra.py`）：

6. 起一个真实短 workflow，进程内触发 controller 中断（用 controller 内部方法，**不发真实键盘事件**），验证 workflow 被 cancel、worker 干净关闭、`run_scan` 返回 `{"status": "cancelled"}`。
7. combined orchestrator：whitebox 返回 cancelled 时短路、不进入 blackbox 阶段。

**不测**：真实键盘 Ctrl+C 事件；`os._exit` 真退出（mock）。

## 6. 改动清单

| 文件 | 改动 |
|---|---|
| `packages/core/src/shannon_core/runtime/scan_runner.py` | **新增**：`ShutdownController`、`run_scan_graceful`、`poll_progress`、`ScanCancelled` |
| `packages/core/src/shannon_core/runtime/__init__.py` | 导出上述 |
| `packages/whitebox/src/shannon_whitebox/worker.py` | `run_scan` 改调 `run_scan_graceful`；删本地 `poll_workflow_progress` 副本；捕 `ScanCancelled` → 返回 cancelled dict |
| `packages/blackbox/src/shannon_blackbox/worker.py` | 同上 |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | `start` 加 `cancelled` → `SystemExit(130)` |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | `start` 加 `cancelled` → `SystemExit(130)`；result 归一化兼容 dataclass/dict |
| `packages/combined/src/shannon_combined/orchestrator.py` | 识别子扫描 `cancelled` → 短路返回 cancelled（不伪装 failed、不继续下一阶段） |
| `packages/combined/src/shannon_combined/cli/main.py` | `scan` 加 `cancelled` → `SystemExit(130)` |
| `packages/core/tests/runtime/test_scan_runner.py` | **新增**：单元测试 1–5 |
| `tests/`（whitebox/blackbox 现有 runner 测试） | 扩展：集成测试 6–7 |

combined 的 worker 调用与 orchestrator 结构**无需改动**即可复用（已验证：orchestrator 串行调用两个 `run_scan`）。

## 7. 决策记录

- **共享运行时（方案 A） vs 每 worker 原地加 vs CLI 层包裹**：选 A。三个 `run_scan`/`poll` 是复制粘贴，抽公共一次写好、三处复用，顺带还债；CLI 层包裹拿不到 workflow handle 发 cancel，最终仍要回头改 worker，拧巴。
- **双击语义 vs 单次优雅 vs 确认提示**：选双击。业界主流（uvicorn / docker / gunicorn），既给清理留时间、又留强制逃生口；确认提示会拦截所有 Ctrl+C，与用户中断习惯冲突。
- **超时放弃等待 vs escalate terminate vs 不设超时**：选放弃等待。terminate 跳过 `finally` 会留子进程孤儿，与「优雅」相悖；不设超时可能卡死，只能靠第二次 Ctrl+C。
- **`os._exit(130)` 作为第二次 Ctrl+C 行为**：绕过 asyncio 清理直接终止，正是「强制」语义；普通 `raise KeyboardInterrupt` 会再次触发那套混乱清理，违背初衷。
- **临时资源清理放 activity/workflow、不放 scan_runner**：清理路径已存在且健全（`async with` / `finally`），且**只清临时注入配置 + 子进程、不删 deliverables/log**；scan_runner 只需保证 `handle.cancel()` 触发它们，重复实现会制造第二个真相源。
