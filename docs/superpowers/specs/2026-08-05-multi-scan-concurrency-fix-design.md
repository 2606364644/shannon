# 多扫描并行卡死修复 — 设计（组合 X）

> 分支：`多扫描并行卡死修复`（由 `feat/fork-py` 开出）
> 主题：消除多 scan 并发时 risk-scoring（及同类轻量确定性 activity）被日志写盘阻塞导致的反复超时。
> 状态：已实现（2026-08-05，TDD 直接做，未写 plan；py-spy 真机多 scan 验证待人工）

---

## 1. 背景与已证实根因

### 1.1 现象
NodeGoat 与 delivery 两个白盒 scan 并发时，NodeGoat 的 `risk-scoring` 阶段在 `Starting risk-scoring`（PHASE start）之后静默 ~26 分钟，期间无 STEP 事件、无错误、无超时日志，最终在某次 retry 成功（`plan()` 实际仅耗时 **3ms**）。scan 最终跑完（82min）。

### 1.2 已证实根因链
- **计算不是原因**：`plan()` 输入极小（chains=8, sink_call_sites=14, taint_flows=1 → 112 次循环），events.ndjson 实测 `duration_ms=3`。
- **真正卡点**：`run_risk_scoring` 是 `async def`，但 `track_step` 进入时必须先 `await dispatcher.dispatch(StepEvent "start")` 才轮到 `yield`（plan）。该 dispatch 走 `DisplayDispatcher.dispatch` → `async with self._lock` → 逐 renderer `await render` → `StructuredEventRenderer`/`FileLogRenderer` 的 `await aiofiles.write` + **`await fh.flush()`（每事件 flush）**。
- **阻塞放大**：`aiofiles.write/flush` 提交到共享线程池（容器 2 核下默认 `min(32, cpu+4)` = 6 线程）。多 scan 并发 + 磁盘 iowait 53%（WSL2 vhdx）时，write/flush 系统调用阻塞变长，线程池被占满 → NodeGoat 的 dispatch 排队等线程 → 5 分钟 `start_to_close` 超时。
- **retry 放大（🔴严重）**：`run_risk_scoring` 套了 `retry_for("standard")` = `PRODUCTION_RETRY`（`maximum_attempts=8`、`initial_interval=5min`）。单次 5min 超时被放大成 ~26 分钟（≈2.5 个 retry 周期）静默卡死。这正是 `retry.py:69-81` 注释反复警告的反模式（确定性幂等快速 activity 绝不能套 PRODUCTION_RETRY）——`code_index`/`poc`/`gitnexus-verdict` 都已因此改短重试，**`risk-scoring` 是漏网之鱼**。

### 1.3 已排除的非主因
- **`run_code_index` 同步阻塞 event loop**：已修复（`ensure_indexed_async` + tree-sitter `to_thread`），worker 容器确认运行修复版，非本次主因。
- **CPU 物理超卖**：宿主 12 逻辑核 / 6 物理核（i5-12500），WSL2 未限核。worker 容器 cgroup 限 2 核（`SUPERNOVA_WORKER_CPUS=2`）造成**伪超卖**，调高即可，非硬件瓶颈。
- **磁盘物理瓶颈**：WSL2 vhdx 实测 iowait 53% 是真瓶颈，但本方案不依赖换盘——通过"业务不等磁盘写日志"绕开它。

### 1.4 核心问题定性
不是"计算慢"也不是"资源不足"，而是 **业务 activity 同步 `await` 日志写盘**这一结构耦合：观测路径（日志）阻塞了价值路径（扫描）。业界标准做法是日志与业务解耦（Python `QueueHandler`、Java `AsyncAppender`、Go 异步 logger），本项目当前是反模式。

---

## 2. 方案总览（组合 X）

保持单 worker 多 scan 架构（不上 per-scan worker），通过三处改动消除卡死：

| # | 改动 | 层级 | 作用 |
|---|---|---|---|
| 1 | **日志总线解耦** | 代码治本 | 业务 dispatch 非阻塞，不再等磁盘写日志（risk-scoring 卡死的直接根治） |
| 2 | **risk-scoring retry 改短重试** | 代码止血 | 即使极端情况超时，也不再被 PRODUCTION_RETRY 放大成数十分钟 |
| 3 | **`SUPERNOVA_WORKER_CPUS` 2→8** | 配置 | 消除 CPU 伪超卖；aiofiles 线程池 6→12，降低饱和 |
| 4 | **py-spy 验证** | 验收 | 落地后多 scan 复现，线程栈确认卡点消失 |

**取舍**：组合 X 把"两 scan event loop 冲突"从"卡死级"降到"毫秒抖动级"（最大阻塞源 run_code_index 已 async + 日志解耦移出关键路径）。残留的短暂同步调用（read_text/atomic_write_json，page cache + 无 fsync，毫秒级）只会造成偶发毫秒抖动，不卡死。彻底零干扰需 per-scan worker（独立 event loop），本方案不采用——其复杂度（launcher 子系统）与收益不匹配。

---

## 3. 详细设计

### 3.1 日志总线解耦（§1，核心）

**解耦点**：`DisplayDispatcher`（`packages/core/src/supernova_core/display/dispatcher.py`）——事件分发枢纽。在此一处改，所有 dispatch 调用（`log_step`/`log_phase`/`log_info`/`log_error` 等）自动非阻塞，renderer 与业务 activity 零改动。

**行为变化**：

```python
class DisplayDispatcher:
    def __init__(self, renderers, queue_maxsize=1000):
        self._renderers = list(renderers)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
        self._dropped = 0          # 观测：被 drop 的事件计数
        self._drain_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._drain_task = asyncio.create_task(self._drain())

    async def dispatch(self, event: DisplayEvent) -> None:
        # 业务调用：非阻塞塞队列。满则 drop 当前事件 + 计数，绝不阻塞业务。
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1
            logger.warning("log event dropped (queue full): %s", type(event).__name__)

    async def _drain(self) -> None:
        # 后台单任务：FIFO 取、串行 render，保 events.ndjson 顺序与现状一致。
        while True:
            event = await self._queue.get()
            for renderer in self._renderers:
                await renderer.render(event)
            self._queue.task_done()

    async def close(self) -> None:
        # graceful：排空队列再退，正常退出不丢日志。
        await self._queue.join()
        if self._drain_task is not None:
            self._drain_task.cancel()
```

**设计要点**：
- **业务零改动**：activity 仍是 `await dispatcher.dispatch(event)`，但语义从"等写盘完成"变为"塞队列微秒返回"。risk-scoring 进入 `track_step` 不再触碰磁盘。
- **顺序保证**：FIFO 队列 + 单 drain task 串行 render，events.ndjson/workflow.log 事件顺序与现状一致。
- **丢失边界**：
  - 队列满 → drop **当前**事件 + 计数告警（保已有顺序完整，不阻塞业务）。
  - graceful 退出（`close` → `queue.join()`）→ 排空，**不丢**。
  - 进程被强杀（SIGKILL/OOM/native 崩溃）→ 丢队列尾部少量事件（业界可接受）。
- **scan_end 不特殊处理**：终态事件已有 web `ScanManager` 双路兜底（另一路独立写 scan_end），即使 dispatcher 侧 drop 也不影响终态判定。
- **per-scan 隔离**：每个 scan 的 `WorkflowLogger` 各自一个 dispatcher + 队列 + drain task，多 scan 不互相干扰（延续现状 per-scan dispatcher 语义）。
- **去掉 dispatcher 旧锁**：解耦后单 drain task 天然串行，原 `asyncio.Lock` 不再需要（drain 是唯一消费者）。

**接线点**：
- `WorkflowLogger.initialize`（`audit/workflow_logger.py`）：构造 dispatcher 后 `await dispatcher.start()` 起 drain task（在首个事件 dispatch 之前）。
- `WorkflowLogger.close`：`await dispatcher.close()` 排空后取消 drain（现有 close 已在收尾路径）。

**改动范围**：`display/dispatcher.py`（重写为队列+drain）、`audit/workflow_logger.py`（initialize 起 drain / close 排空）。**renderer、业务 activity、scan_manager、runner 全部零改动。**

### 3.2 risk-scoring retry 修正（§2，止血）

**改动**：
- `packages/core/src/supernova_core/models/retry.py`：新增 `Category` 字面量 `"risk-scoring"`；新增 `RISK_SCORING_RETRY`（`maximum_attempts=3`、短 backoff，对齐 `CODE_INDEX_RETRY`/`POC_RETRY` 哲学）；`retry_for` 增加 `"risk-scoring"` 分支映射。
- `packages/whitebox/src/supernova_whitebox/pipeline/workflows.py:332`：`run_risk_scoring` 的 `retry_policy=retry_for("standard")` → `retry_for("risk-scoring")`。

**理由**：risk-scoring 是确定性、幂等、毫秒级的快速 activity。套 `PRODUCTION_RETRY`（给单次 ~6min LLM agent 设计）会把单次超时放大成 ~80min 卡死。与 `code_index`/`poc`/`gitnexus-verdict` 已落地的短重试同构。

**待 plan 确认项**：
- `RISK_SCORING_RETRY` 新建独立 policy，还是直接复用 `CODE_INDEX_RETRY`。
- backoff 具体参数（initial/maximum interval）。

### 3.3 CPU 调整（§3，配置）

- `docker-compose.yml` worker 段：`SUPERNOVA_WORKER_CPUS` 默认 `2` → `8`（或经 `.env` 覆盖）。
- 宿主 12 核富余，纯配置、零代码、免费。
- 副作用收益：aiofiles 默认线程池 `min(32, cpu+4)` 从 6 → 12 线程，进一步降低写盘饱和概率。

### 3.4 验证（§4，验收）

落地后执行，把"大概率不再超时"从推断变成确定：
1. 多 scan（≥2）并发复现 risk-scoring 阶段。
2. 容器内 `py-spy dump --pid <worker PID>` 抓线程栈。
3. 验收标准：
   - risk-scoring activity 不再出现在 `aiofiles.write/flush` 或 `dispatcher.dispatch` 的 await 栈上卡住。
   - events.ndjson 中 risk-scoring STEP start 紧跟 PHASE start（秒级内），duration_ms 仍为个位数毫秒。
   - 多 scan 并发时各 scan 的 events.ndjson 持续写入（drain 不饿死）。
4. 若仍卡 → py-spy 栈定位新卡点，针对性处理（本 spec 不预设）。

**py-spy 依赖**：worker 镜像需装 `py-spy`（Dockerfile 加一行 `pip install py-spy`），或验收时临时 `docker exec` 安装。

---

## 4. 测试策略

### 4.1 日志解耦单测（新建 `tests/display/test_dispatcher_decouple.py`）
- dispatch 非阻塞：注入慢 renderer（`render` 内 `await asyncio.sleep`），`dispatch` 仍微秒返回。
- 队列满 drop：填满队列后 dispatch → 不阻塞、`_dropped` 递增、事件不入队。
- drain 保顺序：连续 dispatch 多事件，renderer 收到顺序与发出一致。
- graceful close 排空：close 前塞入的事件全部 render 完成后再退。
- per-scan 隔离：两个 dispatcher 各自队列/drain 互不串扰。

### 4.2 retry 单测
- `retry_for("risk-scoring")` 返回新 policy（`maximum_attempts=3`）。
- risk-scoring activity non-retryable 错误 fail-fast（不重试）。

### 4.3 回归
- 现有 `display/`、`audit/workflow_logger`、`test_phase_marker_activities` 等全绿（renderer 行为不变，仅 dispatch 时序异步化）。

---

## 5. 不做（明确排除）

- **per-scan worker / launcher 子系统**：复杂度与收益不匹配。event loop 彻底隔离的收益（故障隔离 + 零抖动）在本场景（自用/偶发多 scan）不值 launcher 子系统的成本。留待"多用户生产级"诉求时再评估。
- **换 SSD / 磁盘分卷**：物理动作，且本方案已通过"业务不等磁盘"绕开磁盘瓶颈。
- **改 renderer**：解耦点在 dispatcher，renderer（aiofiles write+flush）保持不变，只是被 drain task 异步调用。

---

## 6. 待 plan 确认项汇总

- 🔴 §3.2 `RISK_SCORING_RETRY`：新建独立 policy vs 复用 `CODE_INDEX_RETRY`；backoff 参数。
- §3.1 队列 `maxsize` 具体值（默认建议 1000）。
- §3.1 drop 策略是否需可配（drop 当前 vs drop 最旧）。
- §3.4 py-spy 安装方式（镜像内置 vs 验收时临时装）。
