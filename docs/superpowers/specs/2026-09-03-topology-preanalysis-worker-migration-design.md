# 跨仓预分析迁移 worker（执行段置换）设计

> 2026-09-03。状态：**已实施**（同日 TDD 落地）。前置事故：同日 web 容器缺 prompts
> 致预分析必失败（`Prompt file not found: /app/prompts/cross-repo-topology-discovery.txt`，
> 容器内实测复现），已止血（web Dockerfile 补 `COPY prompts`）；潜伏坑：claude-agent-sdk
> 引擎需 node/claude CLI，web 最终镜像不带 node（worker 带）。本 spec 是止血之上的根治。
>
> **实施勘误（与原稿三处偏差，均已按现状形态收敛）**：
> 1. activity 不新建 `pipeline/activities.py`——multi pipeline 现状是 workflows.py
>    单文件含 activity+workflow（CorrelationScanWorkflow 同款单 activity 直通），
>    `run_topology_analysis_activity` + `TopologyAnalysisWorkflow` 都进
>    `supernova_multi/pipeline/workflows.py`，编排逻辑在其模块级私有函数。
> 2. input 字段 `state_dir` 改为 `workspaces_dir`——store 构造需要 workspaces 根
>    （activity 内 `TopologyAnalysisStore(Path(inp.workspaces_dir))`），比单 analysis
>    目录更通用。
> 3. R6 提交失败取「写 failed/provider_failed 终态 + 返回 id」而非 503：202 契约与
>    前端零变化，用户轮询即见失败、重点一次（对齐失败重跑哲学），不引入新错误形态。
>
> 顺带修复（TDD 首次暴露的预存 bug）：`normalize_topology_result` 对 pydantic v2
> ValidationError 调不存在的 `error_message()`——schema 校验失败必炸 AttributeError、
> 被 web `_run` 吞成 provider_failed；已改 `str(exc)`。

## 1. 背景与问题

自动拓扑预分析（`TopologyAnalysisManager`，web 表单「自动关联分析」按钮）是 **web 进程内
执行**的 LLM agent 功能——全 web 包唯一的 agent 执行点（认证测试登录 / precheck / 扫描
本体均经 temporal 提交 worker）。web 进程执行带来的资源清单分叉已两次咬人：

| 坑 | 表现 | 状态 |
|---|---|---|
| prompts 漏拷 | web 镜像无 `/app/prompts`，预分析 100% failed | 已止血（Dockerfile 补 COPY） |
| node/claude 缺失 | ws config 切 claude-agent-sdk 引擎即失败（引擎需 CLI 子进程） | 潜伏（当前 `openai_compatible` 不触发） |

worker 容器天然资源齐（prompts/node/claude/chromium），且「web 提交、worker 执行」的
temporal 模式在项目里有成熟先例（`AuthValidationWorkflow`，认证管理页「测试登录」）。

**用户已拍板**：迁移走 AuthValidation 模式；韧性取「失败重跑」（agent 失败不自动重试、
用户重点一次，对齐 2026-06-23 cross-repo plan 的原设计哲学——借 temporal 拿隔离，不拿
心跳/重试）。

## 2. 需求

- 预分析的 agent 执行段从 web 进程迁到 worker（temporal workflow + activity）。
- **UI / API / 状态机 / 缓存 / store 位置全部不动**：前端轮询
  `GET /workspaces/{ws}/correlation-topology/analyses/{id}`、state.json 落盘路径
  （`workspaces/<ws>/correlation-topology/analyses/<id>/state.json`，共享卷）、fingerprint
  24h 缓存、`TooManyTopologyAnalyses` 429、`api_view` 输出形状——零变化。
- 迁移后 web 进程**不再执行任何 agent / 加载任何 prompt**（新架构不变量，加守护测试）。
- 历史 state.json 数据零迁移（store 代码挪包，磁盘路径不变）。

### 范围外（明确不做）
- activity 心跳 + 自动重试（用户拍板失败重跑；agent 失败自动重试会重复烧 LLM 调用）。
- API/前端任何改动。
- `SUPERNOVA_TOPOLOGY_*` env 语义变化（单源沿用，仅消费位置部分移到 worker）。

## 3. 现状机制盘点（实现依据）

- `packages/web/src/supernova_web/components/topology_analysis.py`：
  - `_start`（:139）：锁内校验 → `_resolve_repo_path` → `_provider_config(ws)` →
    `collect_navigation_manifest`/`build_topology_fingerprint`（web 侧算，fingerprint 依赖
    manifest）→ 缓存查 `store.find_cached` → `store.create(queued)` →
    `asyncio.create_task(_run)`。
  - `_run`（:251）：`running` → `asyncio.wait_for(_run_agent, timeout)` → 结果分类
    （completed / timeout / cancelled / provider_failed / malformed_output）→ store 写终态。
  - `_run_agent`（:330）：**web 进程** `PromptManager(parents[5]/prompts).load_sync(
    "cross-repo-topology-discovery")` → `run_claude_prompt(prompt, tool_policy=
    "readonly-code", structured_output_schema=..., usage_sink=..., repo_path=..., ...)`。
  - `cancel`（:210）：`task.cancel()` + 状态写 cancelled（cancel 后 sink 晚到 usage 保留）。
  - `store`：`TopologyAnalysisStore`（web 包 `topology_analysis_store.py`），原子写
    （mkstemp+os.replace）；`recover_interrupted` 把 active 态标 interrupted。
- 队列全景（`worker/runner.py`）：`supernova-wb-web`（白盒）/ `supernova-bb-web`
  （黑盒 + **AuthValidationWorkflow + BatchAuthValidationWorkflow**，交互式轻任务的家）/
  `supernova-corr-web`（跨仓主扫描，小时级——预分析放这会被长扫描饿死，排除）。
- AuthValidationWorkflow 模板（`blackbox/pipeline/workflows.py:705`）：input 模型穿线
  provider_config/env_overrides → workflow 内 `execute_activity(..., start_to_close_timeout,
  retry_policy)`；输入校验用 `ApplicationError(non_retryable=True)` fail-fast（有真机卡死
  教训注释：plain ValueError 默认 retryable → workflow task 无限重试）。

## 4. 设计

### 4.1 新 workflow + activity（worker 侧，对齐 AuthValidation 形状）

- **`TopologyAnalysisWorkflow`**（放 `packages/multi/src/supernova_multi/pipeline/workflows.py`
  ——与 CorrelationScanWorkflow 同家；activity 放同包 activities）。
- **`TopologyAnalysisInput`**（shared.py）：`analysis_id`、`ws`、`repos`（有序名单）、
  `repo_paths: dict[str,str]`（web 已解析的绝对路径）、`manifest: dict`（web 已算）、
  `provider_config: dict | None`、`env_overrides: dict[str,str]`、`timeout_seconds: float`、
  `max_turns: int`、`state_dir: str`（该 analysis 的 store 目录，共享卷绝对路径）。
  prompt **不在** input 里——worker 侧组装（web 不再碰 prompts）。
- workflow `run`：
  - 输入校验（`analysis_id`/`state_dir` 缺失等）→ `ApplicationError(non_retryable=True)`。
  - `execute_activity(run_topology_analysis_activity, input, start_to_close_timeout=
    timedelta(seconds=timeout_seconds), retry_policy=RetryPolicy(maximum_attempts=1))`
    ——失败重跑哲学：activity 失败不重试，workflow 把异常转失败结果返回。
  - workflow `run_timeout` = timeout + 少量 buffer（env 单源：
    `SUPERNOVA_TOPOLOGY_TIMEOUT_SECONDS`，web 读同一 env 组 input——勿在两侧各写默认值）。
- **`run_topology_analysis_activity`**：
  1. 经下沉后的 store（见 4.2）读 state 做 status guard（非 `queued` 则直接返回——
     防 cancel 竞态复写，见风险 R3）。
  2. 写 `running`/`progress=20`。
  3. worker 侧 `PromptManager` 组装（路径解析抄 worker 现有 PromptManager 用法）。
  4. `run_claude_prompt(...)`（tool_policy/structured_output_schema/usage_sink 对齐现状
     `_run_agent`；usage_sink 换 worker 侧实例，usage 从返回值取）。
  5. 结果分类写终态（completed/failed + error code 对齐现状 `_run` 的分类表：
     timeout/provider_failed/malformed_output/校验失败 invalid_payload 等，**与现状
     `error.code` 字符串逐一对齐**——前端 TopologyAnalysisPanel 按这些渲染）。
- **注册**：`worker/runner.py` bb_worker 的 `workflows`/`activities` 各加一项。

### 4.2 store 下沉 core（方案对比后取 A）

| 方案 | 描述 | 取舍 |
|---|---|---|
| **A（选）** | `TopologyAnalysisStore` 挪到 `packages/core/src/supernova_core/topology/store.py`，worker activity 直写 running/progress/终态；web 写 create/cancel | progress 保真、UI 零变化、对齐 AuthValidation events 直写模式。代价：挪包 import 面更新 + 双写者按 status 分工 |
| B | worker 只返回结果，web await handle 后写全部状态 | store 留 web 包，但 progress 退化（无中间态）且 web 重启丢句柄要加重连恢复逻辑——复杂度反增 |
| C | store 关键态 + events.ndjson 进度流双轨 | 前端轮询 API 要改读 events，违背 UI 零变化 |

- 数据零迁移：store root 仍 `workspaces/<ws>/correlation-topology/analyses/`（共享卷，
  web/worker 同挂载），仅代码归属变化。
- 写卷分工（status guard 两侧都要做）：web 写 `create`(queued)、`cancel`(cancelled)、
  `interrupted`(recover，见 4.3 弱化)；worker 写 `running`/`progress`/`completed`/`failed`。
  **规则：写终态前必读当前 status，非 active（queued/running）则跳过**——单边收敛，
  化解 cancel vs activity 完成的竞态。

### 4.3 web 侧 `_run`/`_run_agent` 置换（topology_analysis.py）

- **temporal client 接入**：manager 构造注入 temporal 地址解析（同源
  `SUPERNOVA_TEMPORAL_HOST:PORT` env——与 `scan_manager._temporal_address()` 同语义：
  web 容器内是 compose 服务名 `temporal` 非 localhost）；`Client.connect` 懒加载 +
  复用（勿每次提交新建）。core `temporal_infra` 无共享 address helper（仅
  `is_temporal_ready`），地址解析在 manager 内实现同款三行即可，不为此抽公共层。
- `_start` 保留全部前置（校验/manifest/fingerprint/缓存/`store.create(queued)`），把
  `asyncio.create_task(self._run(...))` 换成 temporal 提交：
  `Client.connect(self._temporal_address())` → `start_workflow(TopologyAnalysisWorkflow.run,
  input, id=f"topo-{ws}-{analysis_id}", task_queue=WEB_TASK_QUEUE_BLACKBOX)` →
  保存 handle 到内存表（替代 `_tasks`）。提交失败 → `_fail(provider_failed)`（对齐现状
  「创建后失败」路径）。
- `_run` 改为 `await handle.result()`：拿到 workflow 返回值（成功/失败都正常返回，见
  4.1 失败转结果）后不再重复写终态（activity 已写；web 只做兜底：result 与 store 状态
  不一致时 warning，不改写）。
- `runner` 构造参数**删除**：单一执行路径，不留 web 侧第二路径（防再分叉）。测试改
  mock temporal client（见 §5）。
- `cancel`：`handle.cancel()`（temporal cancel → activity 取消）+ 保留现状「先写
  cancelled 终态再 cancel」的顺序 + status guard 兜晚到结果。
- `recover_interrupted` **语义修正**：现状把 active 标 interrupted（执行者就是 web 进程，
  重启即死）。迁移后 running 的执行者是 worker——**web 重启不得再标 interrupted**，让
  workflow 跑完自写终态。recover 弱化为：仅清理「queued 且查无 workflow handle」的孤儿
  （提交前 web 崩的窗口）；`interrupted` 状态与前端出口保留（历史 state 兼容）。
- 并发门 `_active_count`：从内存计数改为锁内数 store 的 active（queued/running）——
  跨进程后内存计数在 web 重启后归零但 worker 仍在跑，store 计数才正确。
  `SUPERNOVA_TOPOLOGY_MAX_CONCURRENT` 语义不变。
- `_provider_config` / `ProviderConfigIncomplete` 校验保留在 web `_start`（提交前
  fail-fast 422，省 temporal 往返）。

### 4.4 架构不变量 + 守护测试

- 新增守护测试（模式抄 `test_static_dataflow_hints_decoupling.py`）：断言
  `packages/web/src/supernova_web/` 下不 import `PromptManager` /
  `supernova_core.agents.runner.run_claude_prompt`——「web 进程不执行 agent」入测试锁定。
- CLAUDE.md §2 附注一行：web 侧 LLM 能力一律经 temporal 提交 worker（预分析 2026-09-03
  迁出后 web 零 agent 执行点）。

### 4.5 清理（迁移落地同 PR）

- `packages/web/Dockerfile` 删 `COPY prompts ./prompts`（2026-09-03 止血加的，迁移后
  web 不再需要——回环保留反而是资源清单噪音）。
- `docker-compose.dev.yml` 删 web 的 `./prompts:/app/prompts` 挂载（同上）。
- `topology_analysis.py` 删 `PromptManager`/`run_claude_prompt` import 与 `runner` 参数。

## 5. 测试策略（TDD）

- **store 下沉**（§4.2 第 1 步）：现有 `test_correlation_topology.py` store 用例随包
  路径跟走，行为断言零变化（含 `_safe_ws` 一致性对照测试）。
- **activity 单测**：mock `run_claude_prompt`（monkeypatch），断言 status guard、
  running/progress 写入、结果分类与 `error.code` 字符串、usage 回填。
- **web manager 单测**：mock temporal client（`start_workflow` 返 fake handle，
  `result()` 可控返回/抛错），覆盖：提交成功、提交失败→provider_failed、cancel 顺序、
  并发门读 store、recover 弱化（running 不再标 interrupted）。
- **守护测试**（§4.4）。
- **真机验证**（部署后手动，worker 先 web 后）：
  1. docker 下点「自动关联分析」→ 202 → completed，产物 topology 正常（此刻的止血镜像
     已能验证基线；迁移镜像复验）。
  2. **web 容器无 prompts 仍跑通**（守护测试的真机版）。
  3. 分析中 `docker restart supernova-web` → 分析仍 completed（recover 语义修正的验收）。
  4. cancel：分析中点取消 → 状态 cancelled、worker 侧 agent 子进程终止（日志佐证）。
- e2e（`test_cross_repo_topology_end_to_end.py`）：改走 fake temporal handle（项目已知
  temporal 测试 hang 痛点，不在 CI 起真 temporal——对齐 AuthValidation 的测试形态）。

## 6. 改动清单（文件级）

| 文件 | 改动 |
|---|---|
| `packages/core/src/supernova_core/topology/store.py` | 新（自 web `topology_analysis_store.py` 平移，含 `_safe_ws`） |
| `packages/web/src/supernova_web/components/topology_analysis_store.py` | 删（import 改 core；`_safe_ws` 一致性测试 import 路径跟走） |
| `packages/multi/src/supernova_multi/pipeline/workflows.py` | 新增 `TopologyAnalysisWorkflow` + `run_topology_analysis_activity`（单文件，勘误 1） |
| `packages/multi/src/supernova_multi/pipeline/shared.py` | 新增 `TopologyAnalysisInput`（含 `workspaces_dir`，勘误 2） |
| `packages/worker/src/supernova_worker/runner.py` | bb_worker 注册 workflow + activity |
| `packages/web/src/supernova_web/components/topology_analysis.py` | `_run`/`_run_agent` 置换、cancel、recover 弱化、并发门、删 runner 参数 |
| `packages/web/pyproject.toml` | dependencies 补 `supernova-multi`（预存缺口：scan_manager 早已 import） |
| `packages/core/src/supernova_core/topology/discovery.py` | 修预存 bug：ValidationError `error_message()` → `str(exc)` |
| `packages/web/Dockerfile` | 删 `COPY prompts` |
| `docker-compose.dev.yml` | 删 web prompts 挂载 |
| `packages/web/tests/...` | store 用例跟走 + manager mock temporal + 守护测试 |
| `packages/multi/tests/test_topology_analysis_worker.py` | 新：activity 编排单测（12 例） |
| `CLAUDE.md` | §2 附注 web 零 agent 执行点不变量 |

## 7. 风险登记

- **R1 cancel 传播**：`handle.cancel()` → activity 取消 → agent 子进程（claude 引擎为 CLI
  子进程）终止及时性。黑盒取消链路有先例（temporal-native-cancel spec 2026-08-28），
  复用其取消识别（`is_cancellation`）；真机验收 §5-4。
- **R2 双写者竞态**：cancel 写 cancelled 后 activity 晚到结果。两侧「写终态前读 status
  guard，非 active 跳过」单边收敛；cancel 后晚到的 usage 经 status guard 跳过（现状
  `_run` 的 CancelledError 分支有 sink usage 保留语义，activity 侧对齐实现）。
- **R3 recover 语义变化**（行为变更，正向）：web 重启不再打断在跑分析。历史 interrupted
  state 兼容（前端出口保留）；「queued 无 handle 孤儿」判定要防误伤（提交成功但 web 在
  start_workflow 返回前崩——窗口内 analysis 无 handle 可查，标 interrupted 合理）。
- **R4 队列共享**：预分析与黑盒扫描/认证验证共享 bb worker 的
  `SUPERNOVA_WORKER_MAX_CONCURRENT_WF`（默认 4）。长黑盒时段预分析可能排队（现状同理：
  web 进程 max_concurrent=1 更窄）；可接受，不为本 spec 引入新队列。
- **R5 env 单源**：`SUPERNOVA_TOPOLOGY_TIMEOUT_SECONDS` 由 web 读入 input，worker 不再
  读 env（防两侧默认值漂移）；`MAX_TURNS` 同。
- **R6 提交侧可用性**：temporal 不可达时 `_start` 抛错——对齐扫描提交侧的
  TemporalUnavailable 处理（api 层已有先例），预分析 API 转 5xx/503 结构化错误。

## 8. 实施顺序（writing-plans 细化）

1. store 下沉 core（纯平移 + import 跟走，测试绿）。
2. workflow + activity + worker 注册（TDD，mock runner）。
3. web 置换 `_run`/`_run_agent` + cancel + recover 弱化 + 并发门（TDD，mock temporal）。
4. 守护测试 + 清理（Dockerfile/dev.yml/import）。
5. 部署验证（§5 真机四项）。

## 9. 验收标准

- docker 部署（web 容器**无 prompts**）自动关联分析 202 → completed，前端产物正常。
- 分析中重启 web 容器，分析仍 completed（不 interrupted）。
- cancel 后状态 cancelled，无晚到结果复写。
- 守护测试绿：web 包零 PromptManager / run_claude_prompt import。
- 现有 topology 相关测试全绿（CLAUDE.md 测试纪律：只跑改动相关文件）。
