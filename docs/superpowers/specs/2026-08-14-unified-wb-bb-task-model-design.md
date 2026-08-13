# 统一白盒/黑盒任务数据模型（Unified WB-BB Task Model）设计

> 日期：2026-08-14
> 状态：设计定稿（brainstorming + 全量代码核查产出，待 writing-plans 拆任务）
> 主题：消除白盒/黑盒扫描在**数据模型与目录布局**上的割裂——把「扫描任务 = 一次白盒运行（根）+ N 次版本化黑盒 run + per-run 融合报告」立为唯一范式；组合扫描降级为「白盒完自动接力 run-1」的便捷入口，终态与分步扫完全一致。
> 关联：演进 `2026-08-12-combined-wb-bb-scan-design.md`（组合扫描的编排/接力/进度机制），本 spec 只重塑**数据模型与目录布局**，编排核心（`_combined_orchestrator`/`_run_blackbox_phase`/`combined_report_renderer`）复用。
>
> **承接关系（显式）**：本 spec **推翻** 2026-08-12 spec 的以下不变量——①「纯黑盒入口仍是独立 `~N` 目录」（改为并入白盒任务目录）；②「单 `bb_phase` 单 run」（改为 `bb_runs[]` 版本化多 run）。2026-08-12 的编排/接力/预验证/进度机制（§5/§7/§9/§11）**继续生效**，仅其落点从单 run 迁到 per-run 子目录。

---

## 1. 背景与动机

当前 WEB 层的扫描模型有**三种割裂形态**（已代码核查）：

| 形态 | 目录 | scan 记录 | 产物 |
|---|---|---|---|
| 纯白盒 | `scans/<repo>-<ts>/` | 1 条顶层 | 仅 `deliverables/whitebox/` |
| 纯黑盒入口（复用白盒） | `scans/<wb>~<N>/`（**独立平级目录**） | 1 条独立顶层（`scan_id=<wb>~N`） | 仅 `deliverables/blackbox/` |
| 组合扫描（开关） | `scans/<repo>-<ts>/`（单目录） | 1 条顶层（`combined=true`） | `deliverables/{whitebox,blackbox,combined}/` |

割裂带来的问题：

- **同一白盒的黑盒结果散落**：复用白盒的黑盒落到**独立平级目录** `~N`，与白盒任务目录分离——既不同目录、也无融合报告；用户看不到「白盒+黑盒」的统一视图。
- **「能扫多次黑盒」语义割裂**：`~N` 机制虽留史（每次黑盒独立目录），但每个 `~N` 只产黑盒视角、与白盒割裂、无融合报告；组合扫描的续跑又是另一套（单 `bb_phase`、失败才续）。
- **融合报告只属组合扫描**：独立黑盒 `~N` 不产融合报告；只有组合扫描才有 `combined/`。

**用户诉求（三条）**：

1. 同一个扫描任务目录**同时存放**白盒、黑盒结果，按黑白盒子目录分开；展示报告时**可切换**黑白盒报告查看。
2. 一个仓库能扫多次白盒；**一个白盒扫描结果能扫多次黑盒**（多次黑盒**版本化共存**、可切换/对比）。
3. 黑盒白盒都扫完后有**一份融合报告**，内容与「一键组合扫描」的结果**完全一致**。

---

## 2. 设计决策

| 维度 | 决定 |
|---|---|
| **统一范式** | 扫描任务 = 一次白盒运行（根）+ N 次版本化黑盒 run（共存）+ per-run 融合报告 |
| **多黑盒语义** | **版本化共存**：每次黑盒 run 独立保留（per-run 隔离 session/events/产物），可切换查看/对比；融合报告默认最新 run，可看历史 |
| **任务根** | scan_id = `<repo>-<ts>`（白盒运行 id），是**唯一顶层 scan 记录** |
| **黑盒 run 形态** | 任务内子项 `run-K`，**非顶层 scan 记录**；由任务 session 的 `bb_runs[]` 索引（不靠扫盘发现） |
| **run 隔离** | 每 run 独立 `session.json`/`events.ndjson`/`deliverables/`；黑盒 workflow **零改动**（只认 `event_file.parent` 推 workspace_path） |
| **组合扫描定位** | 降级为便捷入口：白盒完自动接力首跑黑盒 = `run-1`；后续手动加黑盒 = `run-2/3…`，走**同一 `_run_blackbox_phase`** |
| **融合报告** | per-run：`f(白盒结果, 该run黑盒结果)`，产 `combined/run-K/`；与组合扫描一致是 **by-construction**（同函数、同输入语义） |
| **黑白盒收敛** | 三种发起方式（纯白盒 / 白盒后手动加黑盒 / 组合扫描）→ 同一终态：任务目录 = 白盒 + N 黑盒 run + per-run 融合报告 |

---

## 3. 非目标与不变量

**非目标**：
- 不做单个漏洞级（vuln instance）白盒↔黑盒 1:1 对齐（继承组合扫描设计，最强为 vuln_class 级）。
- 不改变白盒 workflow / 黑盒 workflow 的内部逻辑（零代码改动，见 §7.2）。
- 不引入新 scan_type（任务根的 scan_type 仍是 `whitebox`）。
- 不做生产数据强制迁移（现有 `~N` / 已跑组合扫描为本地 dev 数据，见 §10）。

**不变量（铁律）**：
- **白盒 workflow 零改动**：白盒 `event_file = <wb_scan_dir>/events.ndjson` → workspace_path = scan_dir → 产物落 `deliverables/whitebox/`（核查实证，`whitebox/activities.py:46 _get_paths`）。
- **黑盒 workflow 零改动**：黑盒从 `event_file.parent` 推 workspace_path；run 的 event_file 指 `blackbox-runs/run-K/events.ndjson` → workspace_path = run-K → 产物落 `run-K/deliverables/blackbox/`；`repo_path` 指 wb 任务根 → 读 `deliverables/whitebox/` 的 queue（核查实证，`blackbox/workflows.py:71 run` + `:230-233` wb_queue_root）。
- **run 由 session 索引、非扫盘发现**：黑盒 run 不进顶层 scan 列表，不靠 `iterdir()` 发现，写进任务 session 的 `bb_runs[]`。
- **scan_end 语义**：每 run 的 events 各自唯一 `scan_end`（per-run 隔离天然消除组合扫描「三方共写一个 events」的协调负担，见 §8）。
- **纯白盒零回归**：开关关时行为/产物布局与今天完全一致（仅 `deliverables/whitebox/`）。

---

## 4. 物理布局

```
<workspaces_dir>/<ws>/scans/<repo>-<ts>/        ← 任务 = 白盒运行（唯一顶层 scan 记录）
  ├── session.json            ← 任务级：wb 状态 + bb_runs[] 索引 + combined 状态
  ├── events.ndjson           ← 白盒 workflow 事件流
  ├── scan-config.yaml        ← 认证快照（各 bb run 共用，黑盒登录读它）
  ├── deliverables/
  │   └── whitebox/           ← 白盒产物（queue / recon / assemble_report）
  ├── blackbox-runs/
  │   ├── run-1/
  │   │   ├── session.json          ← run 级 session（黑盒 workflow 写）
  │   │   ├── events.ndjson         ← 该 run 黑盒 workflow 流（event_file 指此）
  │   │   └── deliverables/blackbox/   ← 黑盒 workflow 产物（workspace_path=run-1）
  │   └── run-2/ …
  └── combined/
      ├── run-1/combined_report.md     ← 融合 = f(白盒, run-1)
      └── run-2/combined_report.md
```

**关键推导链（核查实证）**：

- 白盒：`event_file.parent = <wb_scan_dir>` → 产物 `<wb_scan_dir>/deliverables/whitebox/`。
- 黑盒 run-K：`event_file.parent = <wb_scan_dir>/blackbox-runs/run-K` → 产物 `run-K/deliverables/blackbox/`；`repo_path = <wb_scan_dir>` → 读 `<wb_scan_dir>/deliverables/whitebox/` 的 queue。
- 融合 run-K：读 `<wb_scan_dir>/deliverables/whitebox/` + `run-K/deliverables/blackbox/`，写 `<wb_scan_dir>/combined/run-K/combined_report.md`。

**三种场景终态对比**：

| 发起方式 | 终态任务目录 | 顶层记录 | run 数 |
|---|---|---|---|
| 纯白盒（开关关） | 仅 `deliverables/whitebox/` | 1（白盒任务） | 0 |
| 纯白盒后手动加黑盒 | whitebox + `blackbox-runs/run-1..K` + `combined/run-1..K` | 1（白盒任务） | K |
| 组合扫描（开关开） | whitebox + `blackbox-runs/run-1` + `combined/run-1`（= 手动加首跑） | 1（白盒任务） | 1（可继续加） |

> 组合扫描 run-1 与手动 run-K **走同一 `_run_blackbox_phase`**，故终态/融合报告完全一致——需求 3 的 by-construction 保证。

---

## 5. 数据模型

### 5.1 scan_id 与 run_id

- **scan_id**（顶层）：白盒任务 = `<repo>-<ts>`（同今天白盒）。`scan_type="whitebox"`。组合扫描任务根仍是这个 scan_id，靠 session 标记区分（不新增 scan_type）。
- **run_id**（任务内）：黑盒 run = `run-K`，K = **per-task 单调序号**（替代旧 per-ws 的 `<wb>~N`）。run_id **不是顶层 scan_id**，是任务 session `bb_runs[]` 的条目键。

### 5.2 任务级 session.json 字段

在现有白盒 session flat dict 基础上，任务级 session 写入：

| 字段 | 写入者/时机 | 取值 |
|---|---|---|
| `combined` | scan_manager 提交时 | `True`（有黑盒）/ 无或 `False`（纯白盒） |
| `bb_runs` | 每次 create/run blackbox | `[{run_id, status, started_at, completed_at, auth_ref, reason, workflow_id}]`，**版本化共存** |
| `latest_bb_run` | 每次 run 状态变更 | 指向最新 run_id（报告/列表默认取此） |
| `combined_status` | 每次 run 完成刷新 | 任务级融合可用性（latest run 的 combined 是否就绪） |
| `expected_agents` | 提交时（白盒）/ 黑盒 submit 时（补黑盒部分） | `{"whitebox": N, "blackbox": M}`（进度分母，复用组合扫描口径） |
| `completed_agents` | 两 workflow `mark_agent_completed` merge | 白盒 + 当前活跃 run 黑盒 agents（AgentName 不冲突） |

> 原 `bb_phase`/`bb_reason`/`bb_rerun_attempts`（组合扫描单 run 字段）**语义下沉到 run 级** session（`blackbox-runs/run-K/session.json`）；任务级用 `bb_runs[]` + `latest_bb_run` 表达。

### 5.3 run 级 session.json 字段

`blackbox-runs/run-K/session.json` = 黑盒 workflow 自己写，字段同今天黑盒 session（`status`/`bb_phase`/`bb_reason`/`bb_rerun_attempts`/`expected_agents.blackbox`/`completed_agents`/`host_mappings` 等）。任务级不重复存。

### 5.4 认证快照

认证明文不进 session.json（继承组合扫描 D2）：任务级 session 的 run 条目 `auth_ref` 仅存 `profile_id`（非敏感）；明文快照 = `<wb_scan_dir>/scan-config.yaml`，各 run 共用（黑盒每次开跑重新登录，`run_blackbox_auth_validation`，不复用旧 session）。换认证续跑 = 重 dump scan-config.yaml 覆盖 + 新 run。

---

## 6. 组合扫描 = 自动接力 run-1（收敛）

组合扫描开关打开：

1. scan_manager.start 创建白盒任务目录 + dump scan-config.yaml。
2. t0 预验证（复用 `_run_precheck`）→ pass 才 submit 白盒。
3. `_combined_orchestrator` await 白盒完成 → `create_blackbox_run(task)` → `_run_blackbox_phase(run-1)` → `combined/run-1`。

**手动加黑盒**（新增入口，见 §7.1）：在已有白盒任务上 → 预验证 → `create_blackbox_run` → `_run_blackbox_phase(run-K)` → `combined/run-K`。

`run-1`（组合接力）与 `run-K`（手动）**调同一 `_run_blackbox_phase`**，参数化 run 目录（`event_file`/`repo_path`/`config_path`）。零分叉 → 融合报告 by-construction 一致（需求 3）。

---

## 7. 组件改动（代码核查钉死）

### 7.1 必须改（13 处，含精确位置）

| # | 位置 | 改动 |
|---|---|---|
| 1 | `scan_store.py:417 get_scan_dir` | 拒含 `/` 的 scan_id，无法定位嵌套 run。**新增 run 级定位方法**（如 `get_blackbox_run_dir(ws, wb_scan_id, run_id)`），不动 scan_id 格式 |
| 2 | `scan_store.py:338 _scan_entries` + `list_scans` | `iterdir()` 只扫一层，嵌套 run 不枚举。列表**只枚举白盒任务**；run 从任务 session `bb_runs[]` 取（不扫盘） |
| 3 | `scan_store.py:294 _gen_scan_id` + `:322 _next_blackbox_seq` | 旧 `<wb>~N` 命名与嵌套不兼容。**新增 `create_blackbox_run`**：在 `blackbox-runs/` 下分配 per-task run-K（序号 lock 串行化从 per-ws 收窄到 per-task） |
| 4 | `scan_store.py:437 delete_scan` | 删白盒任务级联 rmtree `blackbox-runs/`+`combined/`；删单 run 精确到 run 子目录 |
| 5 | `combined_report_renderer.py:163 render_combined_report` | 单 `scan_dir` 假设破裂（白盒在任务根、黑盒在 run 子目录）。**改签名接收两个 deliverables 根**（白盒根 + 黑盒 run 根） |
| 6 | `scan_manager.py:1775 _generate_combined_report` | 适配上面签名，传白盒根 + run 黑盒根 |
| 7 | `scan_manager.py:1622 _run_blackbox_phase` | `event_file` 从 `scan_dir/events.ndjson` 改 `blackbox-runs/run-K/events.ndjson`；`repo_path` 保持指 wb 任务根；`config_path=scan-config.yaml` |
| 8 | `scan_manager.py:202-207 submit` 黑盒分支 | 不再 `create_scan`（平级），改 `create_blackbox_run`（任务内子目录）+ 走 `_run_blackbox_phase` |
| 9 | `scans.py:66 _scan_detail` + `:208 get_scan` | 支持 run 级路由（任务详情含 `bb_runs[]`；单 run 详情按 run_id 解析） |
| 10 | `scans.py:213-238` deliverables/report/logs/events 端点 | `scan_dir` 按上下文指 wb 任务根（白盒/融合）或 run 子目录（黑盒 run） |
| 11 | `scan_manager.py:1670 rerun_blackbox` + `:1726 _rerun_blackbox_orchestrator` | 续跑 = 新建下一个 run 子目录；`bb_rerun_attempts` 下沉到 run 级 session |
| 12 | `scan_manager.py:1165 _resolve_workflow_id` | 硬编码 `scans/scan_id`，不认 run workflow_id（`-bb-{K}`）。加 run 维度解析 |
| 13 | `scan_manager.py:1868 _reconcile_combined_scan` | 假设单层（`scan_dir.parent.name=="scans"`）。改为按 `bb_runs[]` 逐 run 兜底 |

### 7.2 零改动可复用（核查实证，12 处）

| # | 位置 | 复用理由 |
|---|---|---|
| A | `whitebox/workflows.py` 全文（`:92 run`） | event_file 不变 → workspace_path 不变 → 产物落点不变 |
| B | `whitebox/activities.py:46 _get_paths` | workspace_path → `deliverables/whitebox/` 推导链不变 |
| C | `blackbox/workflows.py:71-115 run` | 只认 `event_file.parent` 推 workspace_path，event_file 指 run 子目录即自动落位 |
| D | `blackbox/workflows.py:226-238` 白盒 queue 读取 | `repo_path/deliverables_subdir`，repo_path 仍指 wb 根 |
| E | `blackbox/activities.py:46 _get_deliverables_path` | 优先 workspace_path，自动落到 run 子目录 |
| F | `blackbox/activities.py:467 detect_whitebox_results` | 从 repo_path 推白盒根，不受 run 位置影响 |
| G | `paths.py` 全文（SUBDIR 常量 + `resolve_track_deliverable` + `*_dir`） | 纯路径拼接，与层级无关；新增 `BLACKBOX_RUNS_SUBDIR`/`blackbox_run_dir()`/`combined_run_dir()` helper |
| H | `deliverables_reader.py:71 _infer_track` + `read`/`summary` | 传对目录（run 子目录）即不变 |
| I | `scan_store.py:106 resolve_workflow_id` + `_read_workflow_id_from_ndjson` | 读 ndjson 首行 workflow_id，传对 scan_dir 即可 |
| J | `scan_manager.py:410 _submit_whitebox` + `:703 _submit_blackbox` | 只组装 PipelineInput + start_workflow，不关心层级（调用方传对参数） |
| K | `scan_manager.py:1746 _run_precheck` | 独立 AuthValidationWorkflow，event_file 独立，与 run 目录无关 |
| L | `scan_store.py:38 _compute_progress_pct` | 纯函数，从 session data 算进度 |

---

## 8. 错误 / 续跑 / resume / cancel

**per-run 隔离的红利**：原组合扫描最棘手的「单 events 三方写协调 + 幂等 scan_end」铁律自然消解——每个 run 独立 events/独立 scan_end，`_ensure_scan_end` 三方协调简化为 run 内自洽。

| 情形 | 行为 |
|---|---|
| t0 预验证失败 | fail-fast，不耗黑盒（不建 run 或建 run 标 failed）；换认证重试 = 新 run |
| 某 run 黑盒失败 | `run-K.status=failed` + 原因；白盒与其他 run **不受影响**（隔离）；可换认证加 `run-K+1` 续跑（复用白盒产物） |
| 白盒失败 | 任务 wb 状态 failed；不能加黑盒（`_whitebox_deliverables_ready` 预检拦） |
| resume | 任务级按 wb 状态 + 各 run 状态分别恢复（白盒 workflow resume / 活跃 run 黑盒 workflow resume）；`orphan_reconciler`/`_reconcile_combined_scan` 扩展按 `bb_runs[]` 逐 run 兜底 |
| cancel | cancel 当前活跃 run 的 workflow_id（`-bb-{K}`）；单 scan_id 一条记录，cancel 一次取消当前 run |
| 幂等 scan_end | 每 run events 各自唯一 scan_end；run 级隔离，无跨 run 冲突 |

---

## 9. 报告三视图（per-run）

任务详情页（`ReportTab.tsx`）三向 toggle：

| 视图 | 来源 |
|---|---|
| 白盒报告 | `<wb_scan_dir>/deliverables/whitebox/`（现有 `assemble_report`） |
| 黑盒报告（run-K） | `<wb_scan_dir>/blackbox-runs/run-K/deliverables/blackbox/`（现有黑盒 `assemble_report`） |
| 融合报告（run-K） | `<wb_scan_dir>/combined/run-K/combined_report.md`（`combined_report_renderer`，改双路径签名） |

- 默认取 `latest_bb_run` 的黑盒/融合报告；可切换历史 run（版本化对比）。
- 产物 tab（`DeliverablesTab.tsx`）同理三桶，黑盒/融合按 run 折叠。
- `DeliverablesReader` 构造时传对目录（wb 根 / run 子目录）即复用，`_infer_track` 不改。

---

## 10. 现有数据迁移

现有 `~N` 独立黑盒目录 + 已跑组合扫描（单 `bb_phase`）均为 **feat/fork-py 本地 dev 数据，无生产**。

- **倾向不迁移**（默认）：旧 `~N` 目录作只读遗留（列表隐藏/标 legacy），新扫描走新模型。迁移脚本收益低、风险高。
- **可选 best-effort 迁移**（若要保留历史）：`scans/<wb>~N/` → 对应白盒任务 `scans/<wb>/blackbox-runs/run-N/`，重建任务 session `bb_runs[]`。plan 阶段定。

---

## 11. 测试策略

| 层 | 测试 | 守卫 |
|---|---|---|
| scan_store | `create_blackbox_run` per-task 序号原子/并发；任务 session `bb_runs[]` 索引正确；`get_blackbox_run_dir` 定位；delete 级联 | scan_store 单测 |
| 编排 | `_run_blackbox_phase` run 参数化（`event_file` 指 run 子目录、`repo_path` 指 wb 根）；组合 run-1 与手动 run-K 走同路径 | scan_manager 单测 |
| 隔离 | 某 run failed 不波及白盒/其他 run；每 run 独立 scan_end | scan_manager 单测（**核心守卫**） |
| 融合 | per-run `combined_report_renderer`（双路径签名）；内容 = f(白盒, 该run) | renderer 单测 |
| 读取 | list 透传 `bb_runs[]`；detail 含各 run；报告端点按 run 取 | api 单测 |
| 前端 | 列表白盒任务卡 + 内嵌 run 列表；详情三向 toggle；"加黑盒"入口 | vitest 组件测试 |
| 端到端 | 纯白盒→加 run-1→融合；组合扫描 run-1；黑盒失败→加 run-2→对比；组合 run-1 vs 手动 run-1 融合一致 | 真机/容器冒烟 |
| 回归 | 纯白盒零变化；白盒/黑盒 workflow 零改动 | 现有测试守卫 |

**预存陷阱**：前端命令须 `cd packages/web/frontend`（cwd 不持久），用 `./node_modules/.bin/vitest|tsc|vite`；改 web/worker src 须 rebuild `supernova-worker` 镜像（`uv sync --all-packages`）；pytest 只跑改动相关子集（全套会 hang）。

---

## 12. 风险与回退

| 风险 | 影响 | 缓解 |
|---|---|---|
| run 级路由改动波及 list/detail/deliverables 多端点 | 中 | §7.1 #9/#10 集中改 scan_store + scans.py；逐端点测试 |
| `render_combined_report` 双路径签名破坏现有组合扫描调用 | 中 | 同步改 `_generate_combined_report`（#6）；现有组合测试守卫 |
| `_resolve_workflow_id`/`_reconcile_combined_scan` 漏改 run 维度 | 中（orphan/resume 失灵） | §7.1 #12/#13 显式点名；reconcile 单测 |
| per-task run 序号并发竞争 | 低 | 复用 `_create_scan_lock` 思路，收窄到 per-task |
| 现有 `~N`/组合扫描数据失配 | 低（dev only） | §10 不迁移作只读遗留 |
| 列表 UI 嵌套重构 | 中 | 复用组合扫描已有的卡片/分段进度组件 |

**回退**：黑盒 run 嵌套是白盒入口的增量；最坏情况前端隐藏「加黑盒」入口、组合扫描保持现有单 run 行为，纯白盒完全不受影响。

---

## 13. 关键文件索引

| 主题 | 路径 |
|---|---|
| scan 创建/scan_id/lineage（改） | `packages/web/src/supernova_web/components/scan_store.py` |
| 列表/详情/产物端点（改） | `packages/web/src/supernova_web/api/scans.py` |
| scan_manager 编排/接力/resume/cancel/workflow_id/reconcile（改） | `packages/web/src/supernova_web/components/scan_manager.py` |
| 融合报告渲染器（改双路径签名） | `packages/web/src/supernova_web/components/combined_report_renderer.py` |
| DeliverablesReader track 推断（不改，传对目录） | `packages/web/src/supernova_web/components/deliverables_reader.py` |
| 路径常量/helper（加 run helper） | `packages/core/src/supernova_core/utils/paths.py` |
| 白盒 workflow（零改动） | `packages/whitebox/src/supernova_whitebox/pipeline/workflows.py` / `activities.py` |
| 黑盒 workflow（零改动） | `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py` / `activities.py` |
| ScanRequest + validators | `packages/web/src/supernova_web/models.py` |
| 前端扫描页/列表/详情/报告 | `packages/web/frontend/src/pages/ScanNewPage.tsx` / `routes/WorkspaceDetail/{ScanList,ScanDetail,ReportTab,DeliverablesTab}.tsx` |

---

## 14. 后续（writing-plans 接管）

本 spec 锁定数据模型、目录布局、组件改动清单（含精确位置）。实现拆任务交给 writing-plans，预期任务分组：

1. **scan_store 数据模型**：`create_blackbox_run`/`get_blackbox_run_dir`/per-task 序号；任务 session `bb_runs[]`/`latest_bb_run`/`combined_status`；delete 级联；`list_scans` 只枚举白盒任务。
2. **scan_manager 编排**：`_run_blackbox_phase` run 参数化；`_add_blackbox_run` 手动入口；`_combined_orchestrator` 接力 run-1；`_resolve_workflow_id`/`_reconcile_combined_scan` run 维度。
3. **融合报告**：`render_combined_report` 双路径签名 + `_generate_combined_report` 适配。
4. **API 透传 + run 级路由**：`_scan_detail` 含 `bb_runs[]`；deliverables/report/logs/events 端点按 run 路由。
5. **续跑/resume/cancel**：rerun = 新 run；resume 按 `bb_runs[]` 逐 run；cancel 活跃 run。
6. **前端**：列表嵌套 run；详情三向 toggle（per-run）；"给白盒任务加黑盒"入口。
7. **端到端冒烟**：含组合 run-1 vs 手动 run-1 融合一致性 + 失败续跑对比。
