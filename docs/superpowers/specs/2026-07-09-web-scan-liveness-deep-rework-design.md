# web scan liveness 深层重构：CLI 主动心跳 + 协作式取消 设计

## 0. 一句话结论

scan worker（whitebox/blackbox/multi）以 **worker 进程级独立 asyncio task** 每 30s 写 workspace 下纯时间戳 `heartbeat` 文件；web 据其 mtime 判活，把"卡 Running"窗口从 15min 压到 ~90s 且不误杀活 scan；cancel **三轨**（web 自起 SIGINT / 宿主协作式 `cancel.requested` 信号触发宿主 graceful 自退 / 已死直接标记）真正能停 scan；delete 与前端按钮/错误反馈一并修。**判活统一靠 heartbeat，pid 表只服务 cancel。**

## 1. 背景

### 1.1 现象

Web `/workspaces` 页面：scan 被 ctrl+c 停止后，页面仍显示 Running，Cancel 点击无反应、Delete 按钮不出现，直到约 15 分钟后才自愈。

### 1.2 根因（4 缺陷 + 1 宽窗，均已代码/运行时核实）

1. **判活靠 workflow.log mtime，窗口 900s**：`workspaces_indexer.py:67-79 _status_of` 对 session.status 只认 `completed`/`failed`，其余靠 `is_running(pid)` + `is_scan_recently_active(mtime, 900s)`（`scan_liveness.py:23`）。ctrl+c 后文件 mtime 仍 fresh，15min 内持续判 `running`。
2. **孤儿对账被同一 mtime 门挡且触发面窄**：`orphan_reconciler.py:76-77` 先过 `is_scan_recently_active`，recent→`return False`；且只在 web 启动（`app.py:16,37-43`）与开 SSE（`api/events.py:26-28`）触发，列表轮询/删除端点不触发。
3. **cancel 只查内存 `_procs` → 404**：`scan_manager.py:108-116 cancel()` 对宿主 CLI 起的 scan（从未进 `_procs`）/ web 重启后的孤儿 → `return False` → `api/scan.py:34-35` 抛 404。前端 `WorkspaceListPage.tsx:119-130 doAction()` **无 catch**，404 静默吞、确认弹窗卡死。
4. **Delete 按钮前端 XOR 隐藏**：`WorkspaceListPage.tsx:99-103`，`status==="running"` 时只渲染 Cancel，Delete 不出现（后端 `api/workspaces.py:51-63` 其实能删——`is_running` 只查 pid 已空）。

### 1.3 物理约束（设计起点）

web 跑在容器内，**非 host PID namespace**，看不到宿主 CLI 起的 scan 进程。跨容器边界唯一共享信号是 workspaces bind mount 上的**文件**。宿主 CLI 与 web 容器共享该 mount，故 CLI 写文件 web 可读。这一约束决定"真杀宿主 scan"只能走**协作式**（信号文件 + 宿主自退），`--pid host` 直杀因安全面大不取。

## 2. 方案选择

| 决策点 | 选项 | 选定 | 理由 |
|---|---|---|---|
| 存活信号来源 | CLI 主动心跳 / 纯 web 侧 / 分阶段 | **CLI 主动心跳** | 根治：心跳频率固定、独立于 LLM 卡顿，窗口可压到 ~90s 且不误杀 |
| 心跳载体 | heartbeat.json / events.ndjson / session.json / 纯时间戳文件 | **纯时间戳 `heartbeat` 文件** | 判活靠 mtime，内容次要；JSON 对该用途 overkill；稳定元数据归 session.json |
| cancel 对宿主 scan | 单向标记 / 协作式真杀 | **协作式真杀** | 用户要求"web Cancel 也要真杀"；复用现成 ShutdownController，几乎零新代价 |
| 历史数据兼容 | 回退 workflow.log mtime / 不兼容 | **不兼容** | 无历史数据；砍掉最乱的回退路径，判活逻辑极简 |

## 3. 范围

**改**：
- `packages/core`：新增 `HeartbeatManager`（心跳 + 取消监听双向桥）；重构 `scan_liveness`、`workspaces_indexer._status_of`。
- `packages/whitebox`、`packages/blackbox`、`packages/multi`：挂 `HeartbeatManager`；multi 补取消传播。
- `packages/web`：`scan_manager.cancel` 三轨、`api/scan` 不再 404、`api/workspaces` delete 活跃判定、`orphan_reconciler` 用 heartbeat。
- 前端：Delete 按钮始终可见、`doAction` catch+toast、cancel 语义提示。

**不改**：Temporal workflow/activity 内部逻辑；双引擎/双轨判定；`combined`（thin delegator 自动覆盖）；报告/deliverables 渲染。

## 4. 设计

### 4.1 心跳协议

- 文件：`<workspace_dir>/heartbeat`，内容 = 单行 unix 时间戳（scan 进程 `time.time()`）。
- 写：temp + `os.replace` 原子替换。
- 频率：默认 30s（`SHANNON_HEARTBEAT_INTERVAL_SECONDS`）。
- 生命周期：scan worker 启动时写**初始 heartbeat**（消除"workspace 刚建、未到首周期"的空窗）→ 周期写 → 正常退出 best-effort 删文件；ctrl+c/崩溃删不掉由 mtime stale 兜底（**判活不依赖删除**）。

### 4.2 来源区分（owner 标记）

scan 启动时在 `session.json` 写：
- web 自起：`scan_manager.start()` 写 `owner="web"`（pid 已在 `_procs`）。
- 宿主 CLI 起：CLI 启动写 `owner="host"`、`host_pid=os.getpid()`（仅供诊断，web 跨容器不可见）。

owner **只服务 cancel 分轨**；判活不靠 owner。

### 4.3 判活逻辑重构（`workspaces_indexer._status_of`）

```
session.status ∈ {completed,failed,interrupted,cancelled,killed,crashed} → 该终态（强信号，立即定）
heartbeat 存在 且 mtime ≤ 窗口(默认90s) → running
否则 → interrupted
```

- pid 表（`_procs`/`_active_pids`）**不再参与判活**，只服务 cancel（web 自起 SIGINT）。`_status_of` 不再依赖 `sync_active`。
- 终态集合显式化（取代现在"只认 completed/failed + 兜底推断 interrupted"的混乱）。

### 4.4 HeartbeatManager（core 统一组件）

位置：`packages/core/src/shannon_core/runtime/heartbeat.py`（与 `scan_runner.py` 同层，不依赖 Temporal 类型）。

接口（async context manager + 取消回调）：

```python
class HeartbeatManager:
    def __init__(self, ws_dir: Path, interval: float = 30,
                 on_cancel: Callable[[], None] | None = None): ...
    async def __aenter__(self)   # 写初始 heartbeat + 起周期写 task + 起取消监听 task
    async def __aexit__(self, *exc)  # cancel 两个 task + best-effort 删 heartbeat
```

职责：
1. 周期写 `heartbeat`（原子）。
2. 周期检测 `<ws_dir>/cancel.requested` 存在 → 调 `on_cancel` 回调 + 删 `cancel.requested`（避免重复触发）。

`on_cancel` 由各 pipeline 注入：whitebox/blackbox 传 `ctrl.set()`（复用现成双击 SIGINT 的整套 graceful 取消），multi 传补的取消传播。

### 4.5 三 pipeline 挂载（分散三处，combined 自动覆盖）

三 pipeline 不共享统一 worker 启动入口（`scan_runner.py:168 run_scan_graceful` 是死代码，无人调用），故分散挂载。

| pipeline | start 位置 | stop 位置 | on_cancel |
|---|---|---|---|
| whitebox | `worker.py` run_scan：`ctrl.install`（~275）后、`async with worker`（~277）前（ws_dir 已于 ~125 确定） | `finally` ~336 | `ctrl.set()` |
| blackbox | `worker.py` run_scan：`ctrl.install`（~173）后、`async with worker`（~175）前 | `finally` ~209 | `ctrl.set()` |
| multi | `orchestrator.py` run_cross_repo：`out_ws` 创建（~166）后 | 末尾 ~242（补 try/finally） | 补取消传播（cancel orchestrator tasks + 退出） |

心跳 task **进程级、独立于 Temporal workflow/activity 调度**——这是"不被 LLM 卡顿影响"的保证（worker 活就跳、worker 死就停）。`wire_web_event_file`（把 events.ndjson 落进 workspace）是现成同构先例。

### 4.6 cancel 三轨（协作式真杀）

`scan_manager.cancel(ws)` 重构（`api/scan.py` 不再对"管不到的 scan"抛 404；workspace 存在即处理）：

| 情况 | 动作 | HTTP |
|---|---|---|
| owner=web 且在 `_procs` | `SIGINT`（容器内直杀） | 200 `{cancelled}` |
| owner=host（在跑） | 写 `<ws>/cancel.requested` + 立即标记 `session.status=cancelled` + 写 `scan_end` | 200 `{cancelled, via:"signal"}` |
| 已 heartbeat stale（已死） | 直接标记 cancelled + `scan_end` | 200 `{cancelled, was_dead:true}` |
| workspace 不存在 | — | 404（**唯一** 404 情况） |

判据顺序（短路）：① workspace 不存在 → 404；② `_procs` 有该 ws 且 pid alive → SIGINT；③ `heartbeat` mtime fresh（≤窗口，必然 owner=host 在跑）→ 写 `cancel.requested` + 标 cancelled；④ 否则（stale，含 owner=web 但 web 重启后 `_procs` 空、容器内子进程已随重启死亡）→ 标 cancelled。

- **web 侧立即返回**（不等宿主真退）→ 状态立即翻转 → Delete 立即可用。
- 宿主 `HeartbeatManager` ≤30s（≤一个心跳周期）内检测 `cancel.requested` → `on_cancel=ctrl.set()`（白/黑）/ 取消传播（multi）→ graceful 退出 → heartbeat 停写。

### 4.7 delete + 前端

- 后端 `DELETE /api/workspaces`：活跃判定改用 `_status_of(ws) == "running"`（终态优先，故 cancel 标记后立即可删）。`running` → 409 "先 cancel"；否则 `shutil.rmtree`。
- 前端（`packages/web/frontend/src/pages/WorkspaceListPage.tsx`）：
  1. Delete 按钮**始终可见**（去掉 `:99-103` 的 running XOR）。
  2. `doAction()`（`:119-130`）加 try/catch + toast（全局已挂 `<Toaster/>`/sonner）——API 失败时 toast 错误、复位 busy、不卡弹窗。
  3. cancel 经 `via:"signal"` / `was_dead:true` 时 toast 提示语义（"已发停止信号，宿主进程 ≤30s 退出" / "该 scan 已不在运行，已标记"）。

### 4.8 数据流（改后）

- **启动**：scan worker 起 → `HeartbeatManager.__aenter__` 写初始 heartbeat + 周期 task。
- **运行**：每 30s 刷 heartbeat mtime；web 5s 轮询 `_status_of` → heartbeat fresh → `running`。
- **ctrl+c 停宿主 scan**：heartbeat 停写 → ≤90s `_status_of` → `interrupted`（或用户点 Cancel 秒级标记 `cancelled`）。
- **web Cancel 宿主 scan**：写 `cancel.requested` + 标 `cancelled` → 宿主 ≤30s 自退。
- **列表/删除端点**：直接读 heartbeat + session.status 判活（不再靠启动/SSE 触发对账）。`orphan_reconciler` 改用 heartbeat 判定，窗口 90s。

## 5. 配置 / env 清单

| env | 默认 | 说明 |
|---|---|---|
| `SHANNON_HEARTBEAT_INTERVAL_SECONDS` | 30 | scan worker 写 heartbeat 周期 |
| `SHANNON_SCAN_LIVENESS_SECONDS` | 900 → **90** | web 判活窗口（=interval×3 容差），复用现有 env 名、改默认值 |

## 6. 测试（TDD，先红后绿）

- `HeartbeatManager`：周期写 / 原子写（并发读不读到半截）/ `stop` 删文件 / 检测 `cancel.requested` 触发 `on_cancel` 且不重复触发。
- `scan_liveness`：heartbeat 存在+mtime fresh→活；终态优先；stale→interrupted；无 heartbeat 文件→interrupted。
- `_status_of`：显式认全终态集合；pid 表不参与判活（注入 alive pid 仍判 interrupted 当无 heartbeat）。
- `cancel` 三轨：web SIGINT / 宿主写 `cancel.requested` / 已死标记；workspace 不存在→404（唯一）。
- `delete`：`_status_of==running`→409；cancel 后立即可删。
- 前端：Delete 始终可见 / `doAction` catch+toast / cancel 语义提示。
- **回归铁律**：活 scan（heartbeat fresh）绝不被判 interrupted（反向保护 `kol_mapping_service_20260708-193139`）。

## 7. 不做（YAGNI / follow-up）

- 容器开 `--pid host` 直杀宿主 pid（安全面大，协作式已够）。
- whitebox/blackbox 收敛到 `run_scan_graceful`（死代码）统一挂载（可选后续重构）。
- 给 multi 补完整 ShutdownController（本次只补取消传播的最小退出）。
- 心跳承载 phase/进度等富信息（events.ndjson 已有 AgentEvent，不重复）。
- 双击 SIGINT→`os._exit` 强退路径删 heartbeat（mtime stale 兜底已够，不污染 `_force_exit` 回调）。

## 8. 风险 / 开放问题

- **multi 取消传播**：multi 无 ShutdownController，取消传播需新机制（cancel orchestrator asyncio tasks）。实现时确认 `run_cross_repo` 的 task 结构能否干净 cancel。
- **宿主 scan 卡死不响应取消**：若宿主 CLI 在不可中断的系统调用阻塞，`cancel.requested` 检测延迟。心跳协程是 asyncio task（不阻塞），正常 ≤30s 响应；极端阻塞时降级为 mtime stale 兜底。
- **窗口 90s 的容差**：=interval×3。若宿主机/容器高负载致心跳协程调度延迟，可能偶发误判。env 可调。
- **Temporal workflow 残留**：worker 退出不等于 temporal server 侧 workflow 终止（可能 timeout 等待）。web 不关心（看 heartbeat/终态），但 temporal zombie 可能堆积（既有问题，本 spec 不解决）。

## 9. 决策记录

- 2026-07-09：用户选"深层重构 liveness" → "CLI 主动心跳" → "纯时间戳 heartbeat 文件" → "不考虑历史兼容" → "cancel 也要真杀"（协作式）。
- 物理约束（容器非 host PID namespace）决定"真杀"只能协作式（信号文件 + 宿主自退），`--pid host` 直杀因安全面不取。
