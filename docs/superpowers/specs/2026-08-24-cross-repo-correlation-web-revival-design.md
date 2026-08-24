# 跨仓关联扫描 web 复活设计（ScanNewPage 第三类型 + 三段接力）

- **日期**: 2026-08-24
- **分支**: feat/fork-py
- **状态**: Design（已与用户逐节确认 → 待 writing-plans）
- **作者**: brainstorming 会话产出
- **上游设计**: `docs/superpowers/specs/2026-06-22-cross-repo-microservice-scanning-design.md`（CLI 编排器 + 关联 Agent，Phase A/B 已全部实现）

---

## 1. 背景与动机

跨仓微服务关联扫描（Node.js/TS 前端仓 → Go gRPC 后端微服务仓）的**CLI 链路自 2026-06 起完整可用**：

- `supernova-multi start -c multi-repo.yaml`：N 仓白盒（复用/现扫）→ per-edge `cross-repo-correlation` Agent 推断跨服务调用/信任边界/候选攻击链 → 独立关联 workspace 产物（`cross-service-topology.json` / `trust-boundaries.json` / `correlation-report.md` / 合并漏洞 queue 带 `service` 标注）。
- 黑盒消费链（spec 2026-06-22 §6.2 / Phase B）也已实现：`BlackboxPipelineInput.correlated_workspace` 穿透 4 层、recon-skip 消费合并 queue（`has_correlation_results()`）、`exploit_executor` 注入 `cross_service_topology`/`trust_boundaries`。

**但 web 端断链**：2026-07-14 scan_manager C1 化（fork CLI 子进程 → Temporal workflow 统一提交）只迁移了 whitebox/blackbox/组合，correlation 留下 `scan_manager.py:302 raise "correlation 暂未 C1 化"`；前端入口随后在 2026-08-17 扫描类型收窄中被移除（`ScanList.tsx:188` 注释"correlation 后端未实现，不入选项"）。残留资产：`/api/multi-configs` CRUD 路由（前端零调用）、`MultiRepoConfigStore`、`CorrelationProgressEvent` 前端事件 schema、`is_correlation`/`links` 血缘字段、`CorrelationEventWriter`（repo/phase/edge/scan_end ndjson 事件）。

**本设计目标**：接通执行链路（C1 化）+ 重做前端页面，让该功能在 web 上可用。核心用户价值：**发现跨仓库漏洞链路——后端仓的 SQL 注入由前端仓经 RPC 调用传入，系统能找到"哪个接口触发了后端微服务的注入"**。

## 2. 目标与非目标

**目标（本次范围）**

- ScanNewPage 恢复类型切换：`白盒 | 跨仓关联`（黑盒类型移除，黑盒只作为组合/跨仓任务的嵌套 run 存在）；
- 跨仓表单：纯表单向导（选仓/角色/复用或重扫）+ 折叠 YAML 双向同步；表单⇄YAML 状态一致，提交 YAML；
- 执行链路 C1 化（方案 A：web 接力编排 + worker 侧 CorrelationScanWorkflow）；
- 三段接力：子仓白盒（复用/现扫）→ 关联阶段 →（可选）黑盒 gateway 验证 run；
- 结果完整专属视图：服务拓扑图 + 跨服务攻击链卡片 + 按服务分组漏洞 + 信任边界 + 报告；
- 扫描列表 correlation 主行 + 嵌套子行列（现扫子仓行 + 黑盒验证 run 行）。

**非目标（显式排除）**

- 不改单仓白盒/黑盒扫描内核；CLI `supernova-multi` 行为不变（仅内部重构拆函数）；
- 不做全链路确定性数据流 / proto parser（上游 spec §13 维持非目标，关联仍走 Agent 推断）；
- 不做跨工作区选仓库/历史扫描（限当前 ws）；
- 不做后端互调等复杂拓扑的表单编辑（YAML 手写可达，表单只覆盖 entrypoint→N backend 星型）；
- 不做 gRPC 进程内验证（上游 §13）；
- 不重做 WorkspaceListPage correlation tree（已下线的旧形态不复活）。

## 3. 已定决策（brainstorming 会话产出）

| # | 决策 | 说明 |
|---|---|---|
| 1 | **入口 = ScanNewPage 第三类型** | 恢复类型切换；**黑盒表单分支删除**（含历史黑盒重跑预填入口），黑盒只作为组合/跨仓嵌套 run |
| 2 | **配置 = 纯表单 + 折叠 YAML 双向同步** | 表单选完即时生成 YAML；编辑 YAML 即时回填表单；两边是同一份状态的视图；提交即 YAML（`config_content`） |
| 3 | **结果 = 完整专属视图** | 拓扑图 + 攻击链卡片 + 合并漏洞 + 边界 + 报告，第一版全做 |
| 4 | **仓库范围 = 限当前工作区** | 参与仓库须已在当前 ws；复用候选限本 ws 白盒 scan |
| 5 | **现扫子仓 = 独立 scan 行 + 嵌套** | 子仓是标准白盒 scan（详情/报告/数据流全可用）；主行下嵌套展开子行列；复用子仓不建行 |
| 6 | **三段接力 = 白盒跨仓 → 黑盒验证** | 黑盒无跨仓概念，只作为验证段：以 gateway URL 为入口验证关联发现的跨服务链路可达性（上游 spec §6.2 闭环） |
| 7 | **黑盒验证可选** | gateway URL 选填（同组合扫描开关模式），填了才开启段③；认证/HOST 复用组合扫描组件 |
| 8 | **执行架构 = 方案 A** | web 接力编排（镜像 `_combined_orchestrator`）+ worker 侧 CorrelationScanWorkflow 仅跑关联阶段；否决 B（child workflow 全程编排，须在 worker 复刻 scan 目录约定）与 C（web 进程内跑，违背 C1 方向） |

## 4. 架构总览（三段接力）

```
POST /api/scan {type:"correlation", workspace, config_content(yaml), url?(gateway), auth/host(可选)}
  │  web scan_manager.start() correlation 分支
  ├─ 建 correlation 主行 scan 目录（scan_type="correlation"，scan_id=<name>-YYYYMMDD-HHMMSS）
  ├─ session 写 corr_children[] 血缘 + 校验复用子仓产物完整性
  │
  ├─ 段① 子仓白盒（提交即启动，N 个并行提交、句柄上限自然排队）
  │    ├─ 现扫子仓：建标准 scan 行 + _submit_whitebox（复用现有链路）
  │    └─ 复用子仓：零动作，仅记录引用路径
  │    ▼  _correlation_orchestrator await 全部子仓 workflow 完成
  ├─ 段② 关联阶段：CorrelationScanWorkflow（worker，新 task queue WEB_TASK_QUEUE_CORRELATION）
  │    = run_correlation_phase(...)（multi 包重构出的纯关联阶段）
  │    收集各子 scan queue → 合并（service 标注）→ per-edge Agent → 产物落主行 deliverables/
  │    ▼ （可选：填了 gateway URL）
  ├─ 段③ 黑盒验证：create_blackbox_run(run-1) + _submit_blackbox(correlated_workspace=<主行scan_id>)
  │    recon-skip 消费合并 queue + exploit 注入 topology/boundaries（后端已实现）
  ▼
finalize：主行 scan_end（镜像 _combined_orchestrator 的 _ensure_scan_end 幂等收尾）
```

## 5. 执行链路设计

### 5.1 multi 包重构（前置，行为不变）

`packages/multi/src/supernova_multi/orchestrator.py` 的 `run_cross_repo`（L82）拆分：

- **保留**：`plan_repo_scans`（纯函数）、现扫段（CLI 直跑 `run_whitebox`）——CLI 路径行为完全不变；
- **拆出** `run_correlation_phase(config, repo_workspace_paths, out_ws_dir, event_file, *, pipeline_testing=False) -> dict`：现第 2-5 步参数化——
  - `repo_workspace_paths: dict[service, Path]`：各子仓 scan 目录（替代现在自己 `resolve_workspaces_dir` 反推）；queue 三处 glob 收集逻辑照搬（`whitebox/intermediate/` → `whitebox/` → deliverables 根，同名去重）；
  - `out_ws_dir: Path`：关联 workspace 目录（CLI=独立目录；web=主行 scan_dir，`SessionManager.create_workspace` 已由 web 预建，phase 内幂等不覆盖）；
  - `event_file: Path`：ndjson 事件输出（替代现在硬编码 `resolve_workspaces_dir()/out_workspace/events.ndjson`）；
  - 心跳（`HeartbeatManager`）、per-edge `asyncio.Semaphore(get_max_concurrent())`、单边隔离、漂移检测照搬；
  - 返回 edge 状态汇总（编排据此定终态）。
- `CorrelationEventWriter` 不改（本来就接收显式路径）。

### 5.2 worker 侧 CorrelationScanWorkflow

- `packages/worker/src/supernova_worker/runner.py` 加 worker：`task_queue=WEB_TASK_QUEUE_CORRELATION`（常量与 `WEB_TASK_QUEUE_WHITEBOX` 同处定义）、`workflows=[CorrelationScanWorkflow]`；worker `pyproject.toml` 依赖加 `supernova-multi`；
- `CorrelationScanWorkflow`（放 `packages/multi/src/supernova_multi/pipeline/`，对齐 whitebox/blackbox 的 pipeline 包结构）：单 workflow，activity 化 `run_correlation_phase` 调用（或 workflow 内直调——对齐 WhiteboxScanWorkflow 现有形态，plan 阶段定）；
- 输入 `CorrelationPipelineInput`：`config_path`（yaml 落盘路径）、`repo_workspace_paths`、`event_file`、`provider_config`、`env_overrides`、`enable_llm_track`、`pipeline_testing`——字段语义与 `PipelineInput`（whitebox）对齐，Provider 凭据/env 注入走同一套机制。

### 5.3 web scan_manager correlation 分支

`scan_manager.py:302` 的 raise 替换为真实现：

1. `_resolve_correlation_yaml`（已在 L1851）解析 yaml → 校验 `MultiRepoConfig`（parse_multi_repo_config，422 带结构化错误）；
2. 复用子仓：读历史 scan 目录，校验 deliverables 完整性（至少一个有效 exploitation queue），不完整 422 指明仓与原因；
3. 建主行（`scan_store.create_scan`，`scan_type="correlation"` 已支持）+ session 写 `corr_children: [{service, scan_id, reused}]`；**建行后把 yaml 的 `correlation.out_workspace` 覆写为主行 scan_id 并落盘**（yaml 预览中该字段省略）；
4. 现扫子仓：逐仓 `create_scan` + `_submit_whitebox`（子仓 url 传空、combined=False——纯白盒）；子仓事件写各自 events.ndjson，**主行** events.ndjson 由编排经 `CorrelationEventWriter` 写 repo started/completed；
5. `asyncio.create_task(_correlation_orchestrator(...))`（镜像 `_combined_orchestrator` L2099 的 try/except/finally + `_ensure_scan_end` 幂等收尾）：
   - await 全部子仓 handle；任一子仓失败 → 主行 failed（已完成子仓行保留），**不进关联阶段**；
   - 全部成功 → dump config（out_workspace 已覆写）+ `_submit_correlation`（`Client.connect` + `start_workflow`，对齐 `_submit_whitebox` L460 的 provider_config/env_overrides 解析）；
   - 关联 workflow 完成 → 有 gateway URL 则 `create_blackbox_run(run-1)` + `_submit_blackbox(..., correlated_workspace=主行scan_id)`（穿透字段已在）；
   - 黑盒 run 完成 → `_ensure_scan_end`。
6. cancel 扩展：取消主行级联取消在跑子仓 workflow + correlation workflow + 黑盒 run（现有三轨 cancel 加 correlation 分支）；
7. resume：镜像组合扫描的恢复路径（re-attach handles / liveness 判活，plan 阶段对齐 `_combined_kickoff` 现状）。

### 5.4 关联 Agent 输出 schema 小扩展（攻击链结构化）

per-edge prompt（`prompts/cross-repo-correlation.txt`）输出加 `flows` 字段，编排新增产物 `cross-service-flows.json`：

```json
{"flows": [{
  "entry": "POST /orders",                       // from 仓 HTTP/调用入口
  "method": "order.v1.OrderService/CreateOrder", // RPC method
  "call_site": {"file": "src/grpc-client.ts", "line": 42, "snippet": "client.createOrder(req)"},
  "vuln_refs": [{"service": "order-svc", "title": "SQL 注入", "severity": "high",
                  "location": "internal/dao/order.go:88"}],
  "confidence": "high", "evidence": "handler 内拼接 SQL，参数来自 CreateOrder request"
}]}
```

仅扩展输出契约（structured_output_schema 的 properties 加 `flows`），不改关联机制；Agent 没找到 flow 时输出空数组，前端降级为 calls 列表 + 按服务分组漏洞并排展示。edge merge 与落盘（`report.py`）同步透传。

## 6. 数据模型

| 模型 | 变更 |
|---|---|
| `ScanRequest`（web models.py） | correlation 分支接通：`url`（gateway，复用现有字段）、`config_content`/`config_name`/`save_as`（已有）；认证/HOST 字段复用现有 |
| 主行 session.json | 新增 `corr_children: [{service, scan_id, reused: bool, status?}]`；`combined`/`bb_runs[]` 复用现有（黑盒验证 run 沿用） |
| `ScanSummary`（scan_store） | `is_correlation`（已有）+ `corr_children` 数量（列表主行展示） |
| 目录 | 主行 `workspaces/<ws>/scans/<corr-scan_id>/deliverables/`（topology/boundaries/flows/report.md/合并 queue）；现扫子仓 = 标准白盒 scan 目录；黑盒验证 = 主行下 `blackbox-runs/run-1/`（模型全复用） |
| 复用语义 | `RepoSpec.workspace` = 历史 scan_id（C1 中 scan_id 即 workspace name，`deliverables_dir_for_workspace` 天然对齐） |

## 7. API

| API | 状态 | 说明 |
|---|---|---|
| `POST /api/scan`（type=correlation） | 改造 | 去掉 raise；payload `{type, workspace, url?, config_content, save_as?, authentication/auth_*?, host_*?}`；422 场景：yaml 校验失败/复用产物不完整/缺 entrypoint |
| `GET/POST /api/multi-configs`、`GET /api/multi-configs/{name}` | 复活 | 表单"保存/载入配置"接上（现零调用死路由；`MultiRepoConfigStore` 已有防路径遍历） |
| `GET /{ws}/scans`、`GET …/{id}` | 扩展 | summary 带 corr_children；复用候选 = 前端按 repo 过滤白盒 scan |
| `GET /{ws}/scans/{id}/correlation` | **新增** | 组装 `{topology, boundaries, flows, merged_vulns(按 vc 分组 + service 标注), drift_warnings, corr_children}`，读主行 deliverables 产物 |
| `GET …/events`（SSE） | 已在 | correlation ndjson 事件透传（`CorrelationProgressEvent` 前端 schema 已在 types.ts L85） |
| blackbox-runs CRUD | 已在 | 黑盒验证 run 全复用 |

## 8. 前端设计

### 8.1 ScanNewPage 类型切换与跨仓表单

- 类型切换：`白盒 | 跨仓关联`（segmented，同 2026-07-02 launcher 形态但只有两项）；**黑盒只读渲染分支与重跑预填入口删除**；
- 新组件组 `frontend/src/components/correlation/`：
  - **仓库卡片列表**：每卡 = `RepoCombobox`（复用，选 ws 已有仓库）+ role 单选（`entrypoint|backend`，首仓默认 entrypoint，≥1 entrypoint 提交校验）+ 来源单选（`重新扫`默认 / `复用历史扫描`→下拉列该仓历史白盒 scan：时间+状态+漏洞数）+ 删除；添加仓库按钮；
  - **relations 表单态**：星型默认（entrypoint→各 backend），protocol 默认 `grpc` 可改 `http`/`graphql`；复杂拓扑走 YAML；
  - **黑盒验证开关**：gateway URL（选填）+ `AuthFormState` + `HostFormState`（全复用组合扫描组件）；
  - **折叠 YAML 编辑器**：表单 ⇄ YAML 双向实时同步——表单变更即时生成 YAML（`js-yaml` dump，键序稳定）；编辑 YAML 即时 parse 回填表单；解析错误就地标行号、表单区置灰禁提交。YAML 省略 `correlation.out_workspace`（后端生成）；
  - `save_as` 存配置 / 从 `/api/multi-configs` 载入回填。
- 提交 → 202 → 跳主行 live 页（`bb_phase` 语义不引入，correlation 直接 live）。

### 8.2 扫描列表

- 主行：🔗 类型徽标（`is_correlation`）；类型过滤加 `correlation` 档；
- 嵌套子行列（复用黑盒 run 子行列网格模式）：现扫子仓白盒行（repo + 状态 + 漏洞数，可点进详情）+ 黑盒验证 run 行；默认收起；
- 复用子仓不建行（详情内显示引用链接）。

### 8.3 ScanDetail（correlation 主行）

tab 组：`概览 | 跨仓关联 | 产物 | 日志`（ReportTab/DataFlowTab 不适用 correlation 主行——单仓报告在子仓 scan 行看）。

**跨仓关联 tab**（`CorrelationTab`，核心交付）：

- **服务拓扑图**（`TopologyGraph`，SVG/D3 轻量渲染）：节点=服务（role 徽标），有向边=调用（protocol 标签 + status 着色 ok 绿/low 黄/unverified 灰/error 红/declared-missing 虚线紫）；点边展开 calls（method、`file:line`、snippet、confidence、evidence）；
- **跨服务攻击链卡片**（`AttackChainCard`）：`前端仓入口(call_site) → RPC method → 后端仓漏洞(title/severity/location)` 三段式 + confidence/evidence；数据源 `cross-service-flows.json`；空则降级 calls+分组漏洞并排；
- **按服务分组的合并漏洞**：复用 `VulnCard` + service 徽标（保留双轨/可达徽标）；
- **信任边界列表** + **漂移警告横幅** + **correlation-report.md**（复用 `MarkdownView`）。

**概览 tab**：阶段进度（子仓 N/M → 关联边 N/M → 黑盒验证状态）+ corr_children 链接（替代 agent 瀑布）。

**live 页**：消费 `CorrelationProgressEvent`——repo 进度网格 + edge 状态实时刷新。

### 8.4 清理（本次一并）

- 删除 `ScanNewPage` 黑盒只读渲染分支与 `buildBody` 的 correlation 死分支（L263，字段名错误本就 422）、`config_yaml` 幽灵字段；
- `ScanList.tsx:188`"correlation 后端未实现"注释更新；`StatusBadge` correlation prop 接回生产路径；`scan.cardTitle.correlation` 等死词条清理或启用。

## 9. 错误处理

| 场景 | 处理 |
|---|---|
| yaml 校验失败 / 缺 entrypoint / relations 引用错误 | 提交 422 + 结构化错误（`MultiRepoConfig` 校验信息透传） |
| 复用子仓产物不完整 | 422 指明仓 + 建议改重新扫 |
| 现扫子仓失败 | 主行 failed，已完成子仓行保留；不进关联阶段（产物不全会误导） |
| 关联 edge 失败/超时 | 该边 `status=error`，其余边继续（已实现的单边隔离）；边状态在拓扑图/概览透出 |
| 黑盒验证 run 失败 | run 行 failed；主行终态按关联阶段结果（镜像组合扫描 run 失败语义） |
| 取消主行 | 级联取消子仓 workflow + correlation workflow + 黑盒 run |
| 版本漂移（复用子仓） | `detect_drift` 警告（已实现）→ 详情横幅 + `drift_warnings` API 字段，不阻断 |
| web 重启 | resume 镜像组合扫描恢复路径 |
| worker 未部署 correlation queue | 提交后 workflow pending 超时 → liveness 判失败，live 页提示（对齐现有 worker 缺位行为） |

## 10. 测试策略

全部独立模块，避开 feat/fork-py 预存挂起 suite：

- **multi 重构**：`packages/multi/tests/` 扩展——`run_correlation_phase` 参数化（repo_workspace_paths/out_ws_dir/event_file 注入断言，mock AgentExecutor）；CLI `run_cross_repo` 回归（重构后行为等价）；
- **worker**：CorrelationScanWorkflow 注册 + 输入序列化（不触真 Temporal）；
- **web 后端**：`packages/web/tests/`——scan_manager correlation 分支（提交/复用校验 422/编排接力/子仓失败短路/取消级联/黑盒 run 衔接）、`/correlation` 详情 API 组装、multi-configs 复活回归；
- **前端**：表单⇄YAML 双向同步（生成/回填/错误态/置灰）、嵌套子行列、CorrelationTab 渲染（拓扑/攻击链/降级态）、live 事件、类型切换与黑盒分支删除回归；
- **端到端冒烟**：迷你 Node/TS gateway + Go gRPC 后端 fixture，pipeline-testing 模式跑通三段接力（子仓 2 行 + 主行 + run-1），核对产物结构（topology/boundaries/flows/合并 queue 四字段）。

## 11. 风险登记

| 风险 | 等级 | 对策 |
|---|---|---|
| 多子仓并行提交撞 LLM 并发/成本上限 | 中 | 并行提交但句柄受 `_max_concurrent` 排队；子仓本身是标准白盒，成本模型与用户手动逐仓扫一致 |
| web 重启后接力任务丢失（编排态在 web 内存） | 中 | 镜像组合扫描 resume 路径（已解决的同类问题）；liveness 心跳兜底判死 |
| CorrelationScanWorkflow 与 WhiteboxScanWorkflow 形态差异（activity 化 vs 直调） | 低 | plan 阶段对齐 whitebox 现有形态，优先最小改动 |
| 表单⇄YAML 双向同步的状态回路（互相触发） | 中 | 单一数据源（表单状态为源生成 YAML；YAML 编辑仅在显式 blur/保存时 parse 回填），禁止受控回路 |
| 攻击链 flows 依赖 Agent 输出质量（概率性） | 中 | flows 为增强非依赖：空则前端降级并排展示；置信度/证据透出供人工复核 |
| 黑盒验证段复杂度（认证/HOST/precheck） | 中 | 全复用组合扫描既有组件与 `_run_blackbox_phase` 路径，仅多传 `correlated_workspace` |

## 12. 验收清单

- [ ] ScanNewPage 类型切换 `白盒 | 跨仓关联`；黑盒表单分支已删；组合扫描行为不变；
- [ ] 跨仓表单：选仓/角色/复用或重扫/relations/gateway URL/认证/HOST；表单⇄YAML 双向一致；
- [ ] 提交 → 主行 + 现扫子仓行（独立白盒 scan）创建，live 页实时进度（repo 网格 + edge 状态）；
- [ ] 复用子仓：不建行、产物被关联消费、漂移警告显示；
- [ ] 关联完成：`/correlation` API 返回 topology/boundaries/flows/merged_vulns；跨仓关联 tab 四视图渲染；
- [ ] 攻击链卡片正确展示"前端入口 → RPC method → 后端漏洞"（fixture 验证；Agent 未产出时降级态可用）；
- [ ] 填 gateway URL：黑盒验证 run-1 创建、recon-skip 消费合并 queue、exploit 上下文含 topology（日志佐证）；
- [ ] 取消/子仓失败/edge 失败/run 失败各错误路径行为符合 §9；
- [ ] CLI `supernova-multi` 重构后行为不变（回归测试 + 冒烟）；
- [ ] 单仓白盒/组合扫描零回归（现有 web 测试套件绿）。
