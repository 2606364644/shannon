# Temporalio Worker Activity 可观测性设计

> 日期：2026-07-13　分支：feat/fork-py　状态：design（待 plan）
> 关联：`packages/core/src/shannon_core/logging/temporalio_redirect.py`、`logging/setup.py`、`audit/workflow_logger.py`、`packages/whitebox/src/shannon_whitebox/worker.py`

---

## 1. 背景与问题

### 1.1 症状

一次白盒扫描（`hr` 仓，workflow `hr_20260713-104726`）出现一段 **10 分钟无任何日志的"空窗"**：

```
10:50:06  ✓ 映射前端路由 (frontend-mapping)        ← gather 并发的另一支秒完成
[10:50:06 → 11:00:06  共 10 分钟,无任何业务/INFO/STEP 日志]
11:00:06  ✓ 检测 REST 框架 (framework-analysis)     ← 突然恢复,3ms 完成
```

用户在空窗期看到状态行 `pre-recon · step 4/8 · 10m 36s · $0.0000` 无法判断"是卡死还是在干活"。

### 1.2 根因（硬证据，经 temporalio event history 确证）

拉取该 workflow 的 event history（`temporalio` Python 客户端 `fetch_history()`）得到不可辩驳的事实：

| 事实 | 证据 |
|---|---|
| `run_framework_analysis` 的 `attempt=1` **从未被 worker 执行** | history 中针对其 `scheduled_event_id=59` **只有 `SCHEDULED`，没有 `STARTED`** |
| 它以 **`attempt=2`** 在 11:00:06 才被执行（瞬时完成） | `ACTIVITY_TASK_STARTED attempt=2 @ 03:00:06 UTC` → `COMPLETED` 同秒 |
| **不是 retry backoff** | `ACTIVITY_TASK_TIMED_OUT: 0`、`ACTIVITY_TASK_FAILED: 0`，全程无任何 retry 事件 |
| **不是 start_to_close timeout** | `attempt=1` 无 `STARTED`，start_to_close 从未开始计时（配置值 `start_to_close_timeout=300s` 未触发） |
| **不是 `analyze_frameworks` 慢** | 该函数只扫固定几个路径（`server.js/app.js` + `routes/models`），未命中即秒退（日志 `No auto-generated REST framework detected`） |
| 同一秒 schedule 的 `run_frontend_mapping` `attempt=1` **瞬时成功** | 与 `framework_analysis` 同在 `asyncio.gather` 并发，前者秒过、后者晾 10 分钟 |
| 空窗期 workflow task 处理**正常** | `02:50:01–02:50:08` 秒级处理了 5 个 `WORKFLOW_TASK`；该窗口内 `WORKFLOW_TASK_TIMED_OUT = 0` |

**结论**：10 分钟空窗 = `framework_analysis` 的 activity task 投递后 **worker 长达 10 分钟未 poll 它**（schedule→start 延迟），temporalio server 内部回收"投了没人执行"的 task、重投为 `attempt=2` 才被执行。**根因在 worker 端的 activity task 消费机制**，该机制位于 temporalio poller 内部，**temporalio event history 不可见**。

### 1.3 为什么"看不到日志"

业务侧进度日志（`track_step`，`activities.py:1401`）只在 activity **真正进入执行**（`async with track_step(...)`）时才打 `○/✓`。`attempt=1` 既然从未被执行，`track_step` 自然什么都不打 → 用户看到 10 分钟死寂。

**worker 层（schedule→poll→execute 调度段）完全没有可观测日志**，这是"看不到日志"的直接根因。

### 1.4 反例记录（防止未来重蹈覆辙）

调查过程中出现过两个**错误诊断**，特此记录以防止重复踩坑：

1. ❌ "PRODUCTION_RETRY 的 5min `initial_interval` backoff" —— 反推自 `5min × 2 = 10min` 的数字巧合。**被 history 推翻**（无 retry 事件）。**结论：换 retry policy 对此现象零效果，因为根本没有 retry 在发生。**
2. ❌ "attempt=1 跑满 5min start_to_close timeout + 5min backoff" —— 同样被 `ACTIVITY_TASK_TIMED_OUT: 0` + `attempt=1` 无 `STARTED` 推翻。

**教训**：对 temporalio 行为下结论前，**先拉 event history 硬证据**，不要用数字反推。

---

## 2. 目标与非目标

### 2.1 目标

建立 **worker 端 activity 执行边界可观测性**，使下次复现"空窗"时能从日志精确判定：

- `attempt=1` 那 10 分钟，worker **有没有**取走执行该 activity（看 `Running activity <type>` 有无出现）；
- 那 10 分钟 worker **在执行别的什么**（看其他 activity 的 `Running`），从而推断根因（executor 被占 / poller 卡 / 其它）；
- （顺带）vuln 阶段 21 次 `WORKFLOW_TASK_TIMED_OUT` 是哪段 workflow 代码卡住。

### 2.2 非目标

- **不修复根因本身**（attempt=1 为何不被 worker poll 的精确机制）。该机制在 temporalio poller 内部，拿到本 spec 的可观测性数据后再立 follow-up spec。
- **不做"用户可见进度提示"**（把 worker 事件桥接到 CLI 状态行/display 流）。这是更大的 UX 改造，且依赖本 spec 先建好的理解，留作后续。
- **不做止血/通用防护**（进度探针、timeout 收紧、无日志超时告警）。根因未明，止血是猜测性的，可能掩盖真问题。
- **不碰 retry policy / start_to_close_timeout**（已证与此现象无关）。

---

## 3. 现状分析（事实，非推断）

### 3.1 现有 temporalio 日志重定向（`logging/temporalio_redirect.py`）

- **只管一个 logger**：`temporalio.activity`。
- `FileHandler` 级别硬编码 `WARNING`；`logger.setLevel(DEBUG)`（logger 不滤，handler 决定）。
- `propagate=False`（不到 root）。
- **设计意图**（文件头注释）：把 temporalio 1.27.2 每次 activity 失败打的全 traceback 噪音（`worker/_activity.py:474 "Completing activity as failed"`）从终端重定向到 per-workspace 文件，保持终端干净。
- 接入点：`audit/workflow_logger.py::_install_failure_redirect` 调 `install_temporalio_log_redirect(<audit_dir>/activity_failures.log)`。

### 3.2 关键的执行边界日志在另一个 logger 上

| temporalio 源行 | logger | 级别 | 内容 |
|---|---|---|---|
| `worker/_activity.py:315` | `temporalio.worker._activity` | DEBUG | **`Running activity <type> (token ...)`** ← 被 worker 取走执行的瞬间 |
| `worker/_activity.py:521` | `temporalio.worker._activity` | DEBUG | `Completing activity with completion: ...` |
| `worker/_activity.py:474` | `temporalio.activity` | WARNING | `Completing activity as failed`（现有 redirect 已挂此 logger） |

`temporalio.worker._activity`（`:315/:521`）**未被 redirect 管**，propagate 到 root。

### 3.3 shannon 的统一日志总线（`logging/setup.py`）

- root logger 挂单个 `LogBusHandler`：散落 getLogger 的 record 经它分流（session 活跃 → queue/event-loop drain → display 流；无 session → `diagnostic.log` fallback）。
- root level = `SHANNON_LOG_LEVEL`（默认 `INFO`）。
- 第三方噪声库（httpx/urllib3/httpcore/asyncio）固定 `WARNING`。
- `temporalio.activity` 由 redirect 独立管，setup.py 跳过。

### 3.4 后果

`temporalio.worker._activity` 的 `:315/:521` 是 DEBUG，propagate 到 root 但被 root `INFO` 过滤 → **执行边界日志全部丢失**。这正是空窗期"看不到任何东西"的直接机制原因。

### 3.5 一个诚实的限制

temporalio **不打 poller 循环本身**的日志（`PollActivityTaskQueue` 调用/返回）。因此 `:315 "Running activity"` 只能**证伪**"被执行了"（attempt=1 期间无此行 = 没被执行），**不能直接给出**"为何不被 poll"的机制。但结合"那 10 分钟别的 activity 在不在 `Running`"，多半能推断（若 executor 被长 activity 占据，会看到）。推断不出时，follow-up 上 pending-activity 周期探针。

---

## 4. 设计

### 4.1 核心思路

把 temporalio worker 的执行边界日志，从"被 root INFO 过滤 / 若放开会进 display 流刷屏"，变成 **"独立 per-workspace 文件 + env 控制级别 + 默认零回归"** —— 完全对称现有 `temporalio.activity` 的 redirect 模式。

### 4.2 机制（4 点）

1. **扩展 redirect 覆盖范围**：从单个 `temporalio.activity` 扩到 `{temporalio.activity, temporalio.worker}`（`temporalio.worker` 子树整体纳入，一网打尽 `_activity` / `_workflow` / `_worker` 等）。

2. **`propagate=False` 截断**：所有被管 logger 设 `propagate=False`，截断到 root `LogBusHandler`。**关键**：否则放开 DEBUG 后 worker 日志会经 LogBus 进 display 流刷屏终端，违背"终端干净"意图。截断后 DEBUG 只进文件。

3. **级别 env 控制**：新增 `SHANNON_TEMPORALIO_LOG_LEVEL`，默认 `WARNING`（= 现状，只收 failure trace）；排错时设 `DEBUG` → 收 `Running activity` / `Completing` / heartbeat / workflow worker 调度。沿用现有 redirect 的"logger=DEBUG 不丢记录、handler 按 env 决定"模式。

4. **合并到 per-workspace 文件**：写到 `<audit_dir>/temporalio-activity.log`（由现有 `activity_failures.log` 演进/改名，反映不再只是 failure），一处看全 failure trace + 执行边界。

### 4.3 铁不变量（默认零回归）

`SHANNON_TEMPORALIO_LOG_LEVEL` 未设 → 行为与现在**完全一致**：
- handler 级别 `WARNING`，DEBUG 被滤；
- 文件只剩 failure trace；
- 终端干净、display 流零改动；
- cost / 双轨 / retry policy 全不受影响。

开 env 是**纯增量可观测性**。

### 4.4 复现验证预期

设 `SHANNON_TEMPORALIO_LOG_LEVEL=DEBUG` 重跑 `hr` 扫描，`<audit_dir>/temporalio-activity.log` 应出现：

```
... DEBUG temporalio.worker._activity: Running activity run_framework_analysis (token ...)
... DEBUG temporalio.worker._activity: Running activity run_frontend_mapping (token ...)
... DEBUG temporalio.worker._activity: Completing activity with completion: ...
```

对比时间戳即可判定：`run_frontend_mapping` 的 `Running` 出现时刻 vs `run_framework_analysis` 的 `Running` 出现时刻。若后者延后 10 分钟（且前者准点），即证实"worker 延迟取走 framework_analysis"。

---

## 5. 改动组件（4 处，均小）

1. **`packages/core/src/shannon_core/logging/temporalio_redirect.py`**（核心）：
   - 被管 logger 集合从 `{"temporalio.activity"}` 扩到 `{"temporalio.activity", "temporalio.worker"}`。
   - handler 级别从硬编码 `WARNING` 改读 `SHANNON_TEMPORALIO_LOG_LEVEL`（缺省 `WARNING`）。
   - 所有被管 logger `propagate=False`。
   - 函数名 `install_temporalio_log_redirect` **保持不变**（调用点零改动），仅内部扩展被管 logger 集合 + handler 级别来源改读 env；保持幂等。

2. **`packages/core/src/shannon_core/audit/workflow_logger.py:114-116`**（调用点）：
   - 文件名 `activity_failures.log` → `temporalio-activity.log`。
   - 同步更新 live display ERROR 行的 `detail_path` hint（`_activity_failure_log_path` 用途）。
   - `_install_failure_redirect` 方法名/注释语义微调（不再只针对 failure）。

3. **`packages/core/src/shannon_core/logging/setup.py`**：注释更新（redirect 现管整个 `temporalio.worker` 子树，不止 `temporalio.activity`）。

4. **env 文档**：`.env.profiles.example`（或 docs）注明 `SHANNON_TEMPORALIO_LOG_LEVEL`（默认 `WARNING`，排错设 `DEBUG`）。

---

## 6. 数据流

```
temporalio worker 打 DEBUG (:315 Running / :521 Completing / workflow worker 调度)
  → logger temporalio.worker._activity / temporalio.worker._workflow / ...
  → propagate=False (截断,不进 root LogBus → display 流保持干净)
  → 独立 FileHandler (级别 = SHANNON_TEMPORALIO_LOG_LEVEL)
  → <audit_dir>/temporalio-activity.log

排错姿势:  SHANNON_TEMPORALIO_LOG_LEVEL=DEBUG uv run shannon-whitebox start -r <repo>
          扫描中/后 tail <workspace>/.../temporalio-activity.log

默认 (env 未设):  handler=WARNING → DEBUG 被滤 → 文件只剩 failure trace = 现状
```

---

## 7. 错误处理

- redirect install 失败**静默降级**（复用现有 `_install_failure_redirect` 的 `try/except`，`logger.warning(...)` + `detail_path=None`）—— 永不破坏扫描，traceback 最坏回到终端。
- env 值非法（非合法 level 名）→ 回落默认 `WARNING` 并 warning，不抛。
- logger/handler 配置异常不影响 worker 运行（logging 故障绝不上行成扫描故障）。

---

## 8. 测试（TDD）

### 8.1 单元（`temporalio_redirect`）

- env 未设 → handler 级别 == `WARNING`（**零回归**）。
- `SHANNON_TEMPORALIO_LOG_LEVEL=DEBUG` → handler 级别 == `DEBUG`。
- env 非法值 → 回落 `WARNING` + 不抛。
- 被管 logger 集合（`temporalio.activity` + `temporalio.worker`）全部 `propagate=False`。
- `temporalio.worker._activity` 的 DEBUG record：env=DEBUG 时进文件、env 未设时不进。
- `temporalio.activity` 现有行为不回归（WARNING failure trace 仍进文件）。
- 幂等：同路径重复 install 不重复挂 handler。

### 8.2 防回退不变量测试

- 默认（env 未设）下，`temporalio.worker._activity` 的 DEBUG record **不进入** root LogBus / display 流（终端干净不变量）。

### 8.3 集成（可选 / 视 plan）

- 跑一个 mini workflow（已注册 activity），env=DEBUG 下断言 `<audit_dir>/temporalio-activity.log` 含 `Running activity` 行；env 未设下不含。

---

## 9. 已知限制与 Follow-up

- **限制**：temporalio 不打 poller 循环本身，故"task 为何不被 poll"的精确机制仍需推断。
- **Follow-up A（数据不足时）**：方案 3 —— periodic pending-activity 探针（worker 进程内每 N 秒查 temporal server 当前 pending activity task，记录其排队时长），直接回答"晾了多久"。仅在本 spec 数据不足以推断根因时启动。
- **Follow-up B（UX）**：把 worker 调度事件桥接到 CLI 状态行/display 流，做空窗期的用户可见进度提示。
- **Follow-up C（根因修复）**：依据本 spec 拿到的数据，定位 attempt=1 为何不被 worker poll，立专门 spec 修复。
- **Follow-up D（关联现象）**：vuln 阶段 21 次 `WORKFLOW_TASK_TIMED_OUT`，本 spec 的 workflow worker DEBUG 日志会提供初步线索，根因修复另立。

---

## 10. 决策记录

| 决策点 | 选择 | 理由 |
|---|---|---|
| spec 范围 | **先建可观测性**，非止血 | 根因未明（temporalio poller 内部，history 不可见）；无仪表则无法定位，盲改 risk（曾误判为 retry policy） |
| 观测受众 | **开发排错为主**（文件 trace），非用户可见进度 | 用户可见进度依赖 display 桥接（更大改造）且依赖本 spec 先建的理解；开发 trace 是定位根因的最短路径 |
| 实现方案 | **扩展 redirect**，非 interceptor / 非探针 | interceptor 盲区与 redirect 相同（都只覆盖执行边界），收益不更高但工作量更大；探针最全但最复杂，应作 follow-up |
| logger 覆盖 | **整个 `temporalio.worker` 子树** | 成本与单 logger 相同（都是 propagate=False+FileHandler+env 级别），顺带覆盖 21 次 workflow task timeout 的观测 |
| 默认级别 | **WARNING（零回归）** | 保持"终端干净"设计意图；开 env 是纯增量 |

---

## 11. 不变量清单（实现须守）

- I1：`SHANNON_TEMPORALIO_LOG_LEVEL` 未设时，所有行为与现状一致（零回归）。
- I2：被管 logger（`temporalio.activity` + `temporalio.worker.*`）一律 `propagate=False`，DEBUG 不污染 display 流。
- I3：redirect install 失败静默降级，绝不破坏扫描。
- I4：纯 logging 改动，不触碰 retry policy / 双轨 / cost / workflow 编排。
- I5：`temporalio.activity` 的 failure trace 行为不回归。
