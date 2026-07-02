# Shannon Web Platform Design

## 背景与目标

shannon-py 当前是纯 CLI 工具：`shannon-whitebox start` / `shannon-blackbox start` /
`shannon-multi start`。本设计提供一个**轻量 Web 平台**，让用户在浏览器里：

1. **开启扫描**（白盒 / 黑盒 / 联动 三种类型，统一入口表单）；
2. **浏览扫描结果**（Markdown 报告渲染成 HTML，按 workspace 组织，含中间产物与日志）；
3. **实时观察扫描过程**（复刻终端 rich 框的信息布局与颜色语义，SSE 推送）。

暂不需要登录鉴权。平台自动检索 `workspaces/` 目录，把内容呈现为 HTML。

### 非目标（YAGNI）

- 不做登录 / 鉴权 / 多租户。
- 不做跨 workspace 的合并视图（联动扫描只下钻到子 workspace 的实时事件，不合并展示）。
- 不做端到端真扫描的自动化测试（Temporal / GitNexus 慢且预存挂起，留人工冒烟）。
- 不替换 CLI；Web 是 CLI 之上的薄封装，扫描器业务逻辑零改。

---

## 总体架构

三层结构，**扫描器（core）业务逻辑零改动**，唯一新增一个 renderer：

```
┌──────────────────────────────────────────────────────────────────┐
│  浏览器 SPA  (React + Vite + TS)                                   │
│   / 项目列表   /scan/new 开启扫描   /p/{ws} 详情(多 tab)            │
└───────────────┬──────────────────────────────────────────────────┘
                │ REST(JSON) + SSE
┌───────────────▼──────────────────────────────────────────────────┐
│  FastAPI 后端  packages/web/  (单进程, asyncio)                    │
│   · WorkspacesIndexer   扫 workspaces/ 建列表/摘要                  │
│   · ScanManager         subprocess 起 shannon-whitebox/-blackbox/-multi │
│   · EventTailer         tail events.ndjson → SSE 推前端             │
│   · DeliverablesReader  读 deliverables/ (md/queue.json/log)       │
│   · MultiRepoConfigStore 管 configs/web-multi-*.yaml               │
│   · GitFetcher          git URL → clone 到 repos/ (注入 GitLab 凭证) │
└───────────────┬──────────────────────────────────────────────────┘
                │ subprocess + env(SHANNON_PROFILE … SHANNON_WEB_EVENT_FILE …)
┌───────────────▼──────────────────────────────────────────────────┐
│  扫描器子进程  (现有 CLI,业务零改)                                   │
│   shannon-whitebox start -r … --url … -w {ws}                     │
│   shannon-blackbox start --url … --repo … -w {ws}                 │
│   shannon-multi start -c {yaml}                                   │
│   └─【唯一新增】StructuredEventRenderer → workspaces/{ws}/events.ndjson │
│      经 env SHANNON_WEB_EVENT_FILE=… 启用                           │
└──────────────────────────────────────────────────────────────────┘
   依赖: Temporal(localhost:7233, docker-compose 已含)
```

### 关键选型

| 决策 | 选择 | 理由 |
|---|---|---|
| 后端 | FastAPI | async + SSE 原生 + 轻 |
| 扫描触发 | **subprocess 调 CLI（三种扫描统一）** | 复用 CLI 封装（env/profile/Temporal/`-w`恢复/优雅退出）+ 进程隔离（扫描崩不拖垮 Web）+ 多扫描并发天然隔离 + 解耦（CLI 是稳定契约） |
| 实时通道 | `events.ndjson` + SSE | 完整复刻 rich，且不耦合扫描进程内存 |
| MD→HTML | 前端渲染 | SPA 自然（react-markdown + highlight.js），后端只返 md 原文 |
| 部署 | docker-compose（复用现有） | Temporal/GitNexus 已在 |
| 前端 | React + Vite + TS | 生态最大，react-markdown/highlight.js/SSE/编辑器库选择多 |

### 对 core 的唯一改动：`StructuredEventRenderer`

这是本设计对扫描器的**唯一**侵入点，且是架构内的自然延伸：

- **位置**：`packages/core/src/shannon_core/display/structured_event_renderer.py`
- **契约**：实现 `async render(event: DisplayEvent)`，把事件序列化成一行 JSON 追加写到 env `SHANNON_WEB_EVENT_FILE` 指定的 `events.ndjson`，每事件 flush。
- **启用**：env `SHANNON_WEB_EVENT_FILE=<path>`；worker 起 dispatcher 时若该 env 存在则 `dispatcher.add(StructuredEventRenderer(path))`。未设 env = 行为完全不变（零影响现有 CLI）。
- **收尾**：扫描结束写一行 `{"type":"scan_end","status":"completed|failed",...}` 收尾标记。
- **架构依据**：`DisplayDispatcher`（`packages/core/src/shannon_core/display/dispatcher.py`）本就是多 renderer 事件总线，契约只有一个 `async render(event)`，且支持 `add(renderer)` 运行时挂载。项目已有写 `workflow.log` 的 `FileRenderer`，本 renderer 是同类活。
- **意义**：让扫描器的进度可观测性脱离终端——任何消费者（Web/CI/别的工具）都能 tail `events.ndjson`，不是 Web 专用。
- **业务扫描逻辑零改**：只是 dispatcher 多挂一个 renderer。

### 两个前提（spec 写死）

1. **Temporal 必须在跑**（`localhost:7233`，docker-compose 已含）。Web 平台不负责拉起 Temporal；`/api/scan` 前置检查，不通则 400 拒绝。
2. **本地路径模式**假定代码仓在服务器可访问；**git URL 模式**由后端 `GitFetcher` clone 到 `repos/`。

---

## 扫描类型与执行模型

### 三种扫描统一 subprocess

ScanManager 一个模型管三种扫描，**无不对称**：

| 类型 | CLI | 输入 | 复用逻辑 |
|---|---|---|---|
| 白盒 | `shannon-whitebox start -r … --url … -w …` | repo（本地路径 / git URL）+ url + ws 名 | — |
| 黑盒 | `shannon-blackbox start --url … --repo … -w …` | url + repo + ws 名 | 表单复选框「复用最新白盒结果」勾选则加 `--latest`（自动取同 workspace 最新白盒）；不勾则独立跑 |
| 联动 | `shannon-multi start -c …` | multi-repo.yaml（上传 / 选 / 手写） | **编排器自带**：声明了 workspace 的 repo 复用、没声明的现扫（`packages/multi/src/shannon_multi/orchestrator.py:16-31, 98-122`） |

执行伪代码：

```python
ScanManager.execute(scan_type, params):
  ws = params.workspace or 自生成
  event_file = workspaces/{ws}/events.ndjson
  env = {**os.environ, "SHANNON_WEB_EVENT_FILE": event_file, **profile_env}
  if scan_type == "whitebox":
      cmd = ["shannon-whitebox", "start", "-r", repo, "--url", url, "-w", ws]
  elif scan_type == "blackbox":
      cmd = ["shannon-blackbox", "start", "--url", url, "--repo", repo, "-w", ws]
  elif scan_type == "correlation":
      cmd = ["shannon-multi", "start", "-c", yaml_path]
  proc = Popen(cmd, env=env, stdout=PIPE, stderr=PIPE)  # 非阻塞
  记录 {ws: proc}
  return 202 {workspace: ws}
```

**为何联动也走 subprocess（统一）**：联动也有 CLI 入口 `shannon-multi start -c`（`packages/multi/src/shannon_multi/cli/main.py`），CLI 内部自己 `asyncio.run(run_cross_repo(...))`，对外是普通同步进程，与白盒/黑盒无异。统一 subprocess 带来：复用 CLI 封装 + 进程隔离 + 并发隔离 + Web 进程轻（不 import 扫描器）+ 三种扫描一套模型。内存事件的"劣势"已被 `StructuredEventRenderer` + ndjson tail 消除。

### 联动扫描的事件汇聚（需小增强）

联动内部跑 N 个白盒子扫描（各自 workspace）+ 关联阶段（per-edge asyncio），事件分散。处理：

- 联动有一个 **correlation workspace**（`out_workspace`，`orchestrator.py:146`），是联动的"主"workspace。
- 子白盒扫描各写自己 workspace 的 `events.ndjson`。
- 联动的实时视图 = **tail correlation workspace 的 ndjson**（编排器层进度：N 个 repo 扫了几个、进入关联阶段、各 edge 状态），可展开下钻看子 workspace 的 ndjson。
- **小增强**：orchestrator 在关键节点（每个 repo 扫描开始/结束、关联阶段开始、每个 edge 完成）写事件到 correlation workspace 的 `events.ndjson`。这是 orchestrator 加几行写 ndjson 的小改动，不是执行方式问题。
- **诚实局限**：关联阶段的 per-edge agent 走 `AgentExecutor`（`orchestrator.py:181`），不一定经 `AuditSession` 的 dispatcher，所以 edge 内部的细粒度事件可能进不了 ndjson。但编排器层（repo 级 + edge 级状态）的进度能覆盖——对"看联动整体进度"够用。此局限在实现时写明，不藏。

### 开启扫描页表单（按类型动态切换字段）

```
扫描类型: ○ 白盒  ○ 黑盒  ● 联动
─────────────────────────────────
[白盒/黑盒字段区]
  代码来源: ○ 本地路径 [____]  ○ git URL [____]   (git URL 后端注入 GitLab 凭证)
  目标 URL: [____]
  workspace 名: [____]  (可空,自动生成)
  [仅黑盒] ☑ 复用最新白盒结果 (加 --latest)

[联动字段区]
  multi-repo.yaml: [上传文件] 或 [从已有选] 或 [手写编辑器]
  └ 手写编辑器: Monaco textarea + yaml 校验
     · 「保存为配置」→ 存 configs/web-multi-{name}.yaml (经 MultiRepoConfigStore 校验)
     · 「直接运行」→ 后端临时落盘到 configs/web-multi-tmp-{ts}.yaml,扫完保留供回看
```

### git clone 凭证（GitLab HTTP Basic）

用户在前端只填**裸 URL**（如 `https://gitlab.futunn.com/webinfra/passport_moomoo_nodejs.git`），后端 `GitFetcher`：

- 读 env `GITLAB_USER` / `GITLAB_TOKEN`（从 `.env` / `.env.profiles` 加载，**不进前端、不进日志**）。
- 自动注入 `https://${GITLAB_USER}:${GITLAB_TOKEN}@` 前缀 clone。
- clone 到 `repos/<repo_name>/`（URL 末段去 `.git`）。
- 凭证缺失 → 友好报错（不泄露是否私有仓）；clone 失败 → 透传 git stderr（**脱敏 token**）。

---

## 后端组件（`packages/web/src/shannon_web/`）

| 组件 | 职责 | 关键点 |
|---|---|---|
| `WorkspacesIndexer` | 扫 `workspaces/*/` 读 `session.json` 建列表 | 按 mtime 增量；**状态判定**：有 `events.ndjson` 且无 `scan_end` 行 = 进行中●；有 `scan_end` 且 `status=completed` = ✓；否则 ✗ |
| `ScanManager` | subprocess 起 CLI + 生命周期 | 并发限流 env `SHANNON_WEB_MAX_CONCURRENT`（默认 1）；SIGINT 优雅取消（复用 CLI 双击退出）；僵尸清理；wall-clock 超时 env `SHANNON_WEB_SCAN_TIMEOUT`（默认 0=不限） |
| `EventTailer` | tail `events.ndjson` → SSE | 记 offset 的 `tail -f`；读到 `scan_end` 关闭流；损坏行跳过计数 |
| `DeliverablesReader` | 读 `deliverables/` | md 原文返回（前端渲染）；queue.json 等返回 JSON；支持下载 |
| `MultiRepoConfigStore` | 管 `configs/web-multi-*.yaml` | 列/读/写/校验；手写新建的存这；用 `parse_multi_repo_config` 强校验 |
| `GitFetcher` | git URL → clone 到 `repos/` | 注入 GitLab 凭证；stderr 脱敏 |

### API（REST + SSE）

```
GET    /api/workspaces                     列表+摘要(状态/漏洞数/成本/时间)
                                           漏洞数:从 session.json 取计数字段;若无则
                                           聚合各 *_exploitation_queue.json 的 entries
                                           (实现时确认 session.json schema 后定)
POST   /api/scan                           {type, source{kind:path|git,value}, url, workspace?, reuse_latest?}
                                           type=correlation 时 {type:"correlation", config_name} 或 {type:"correlation", config_content, save_as?}
                                           · config_name: 跑已有 yaml
                                           · config_content+save_as: 存盘后跑
                                           · config_content 无 save_as: 临时落盘跑(扫完保留)
                                           → 202 {workspace}
GET    /api/workspaces/{ws}                概览(session.json 指标)
GET    /api/workspaces/{ws}/report         最终 md 报告原文(前端渲染)
GET    /api/workspaces/{ws}/deliverables   产物清单
GET    /api/workspaces/{ws}/deliverables/{file}   单产物(预览/下载)
GET    /api/workspaces/{ws}/logs           agent 日志/workflow.log
GET    /api/workspaces/{ws}/events         SSE: events.ndjson tail
DELETE /api/scan/{ws}                      取消(SIGINT)

GET    /api/multi-configs                  列已有 yaml
POST   /api/multi-configs                  手写新建 {name, content} → 校验+存盘
GET    /api/multi-configs/{name}           读
```

### 三条数据流

1. **开启扫描** → 前端表单 → `POST /api/scan` → ScanManager：（git 则先 GitFetcher clone）拼 CLI + env（含 `SHANNON_WEB_EVENT_FILE=`）→ `Popen` 非阻塞 → 记 pid → 202；前端跳 `/p/{ws}?tab=live`。
2. **实时推送** → 子进程 `StructuredEventRenderer` 写 ndjson（每事件 flush）↔ `EventTailer` tail → SSE → 前端还原 rich 布局 + 滚动日志。
3. **结果读取** → 进详情页 → 概览 / 报告(md) / 产物树 / 日志 分别 GET → 前端渲染。

---

## 前端（`packages/web/frontend/`，React + Vite + TS）

### 信息架构（3 层）

```
shannon-web/
├── 开启扫描页  (/scan/new)            ← 主页面 #1：表单触发新扫描
│     类型(白盒/黑盒/联动) · 代码(本地路径 / git URL) · url · workspace 名
│     └─ 提交 → subprocess 起 CLI → 跳到该项目的「实时」tab
│
└── 项目列表页  (/)                    ← 主页面 #2：自动检索 workspaces/
      每行: 项目名 · 状态(进行中●/完成✓/失败✗) · 漏洞数 · 成本 · 时间
      └─ 点击 → 项目详情页  (/p/{workspace})
                    │
                    ├─ 概览 tab    session.json 指标(成本/耗时/各阶段)
                    ├─ 报告 tab    MD → HTML(语法高亮 + 目录 + 锚点)
                    ├─ 产物 tab    queue.json / attack_chains.json 等文件树 + 预览/下载
                    ├─ 日志 tab    agents/*.log + workflow.log
                    └─ 实时 tab    (扫描进行中) rich 复刻框 + SSE 滚动日志流
```

"两个主页面"不变：**开启扫描页** + **项目列表页**；详情是点进去的子页，用 tab 承载"报告+产物+日志+实时"，不挤。

默认 tab：扫描进行中默认进"实时"tab，完成后默认进"报告"tab。

### 目录结构

```
packages/web/frontend/src/
├── pages/
│   ├── ScanNewPage.tsx       开启扫描页(类型切换 → 动态表单)
│   └── WorkspaceListPage.tsx 项目列表页
├── routes/WorkspaceDetail/   详情子页(Outlet tab 布局)
│   ├── index.tsx             路由壳 + tab 导航
│   ├── OverviewTab.tsx       概览(session.json 指标)
│   ├── ReportTab.tsx         MD→HTML 渲染
│   ├── DeliverablesTab.tsx   产物树 + 预览/下载
│   ├── LogsTab.tsx           agent 日志 + workflow.log
│   └── LiveTab.tsx           实时 dashboard(rich 复刻) + 滚动日志
├── components/
│   ├── DashboardPanel.tsx    ★ rich 框复刻
│   ├── LogStream.tsx         SSE 滚动日志
│   ├── MarkdownView.tsx      react-markdown + rehype-highlight + rehype-slug/autolink
│   ├── YamlEditor.tsx        @monaco-editor/react
│   └── FileTree.tsx          deliverables 产物树
├── api/                      fetch 封装 + SSE hook(useEventSource)
└── App.tsx + router.tsx      react-router
```

### 实时 dashboard 复刻（`DashboardPanel.tsx`）

复刻终端两层布局。数据来自 SSE 推的 ndjson 事件 / `DashboardState` 快照：

**① 底部状态条**（对应 `live_dashboard.py:52-88`）：

```
═══════════════════════════════════════════════════════════
 pre-recon · step 3/7 · 02:14 · $0.0234         ← 状态条(对应 rich 行1)
─────────────────────────────────────────────────────────
 ⠼ sink-discovery t2  grep *.py for sql sinks    ← 运行中 agent(对应 rich 行2)
 ⠼ recon t1  curl /api/users                      ← 多 agent 并行各一行
═══════════════════════════════════════════════════════════
```

| rich 终端元素 | 前端对应 | 数据源(ndjson snapshot 字段) |
|---|---|---|
| `current_phase` | 状态条左 · 阶段名 | `snapshot.current_phase` |
| `step N/M` | 状态条 · 进度 | `snapshot.completed_units`/`total_units` |
| elapsed | 状态条 · 计时 | 前端按 `start_monotonic` 本地 `setInterval(1s)` 自增（零后端 tick） |
| `$cost` | 状态条 · 成本 | `snapshot.total_cost` |
| running agents + spinner | 状态条下各行 | `snapshot.agents[name]`（status=running/turn/last_action） |
| 分隔线 `─×N` | CSS border | — |

**② 滚动日志区**（对应 `RichConsoleRenderer`，`rich_renderer.py`）：逐事件渲染成带颜色的日志行。

### 实时 dashboard 复刻保真度约定（重要）

终端实际显示是**两层**：滚动日志区（`RichConsoleRenderer` 逐事件 `console.print`，带颜色）+ 底部状态条（`LiveDashboardRenderer` 用 `rich.Live` 固定刷新）。前端复刻这两层：

- **信息同源**：事件是纯数据（`events.py`），颜色是 renderer 加的。前端按事件类别上色，与 rich 用同一套语义映射，**不是从 ANSI 码翻译**。
- **颜色按事件类别语义映射**：

| 事件类别 | rich 颜色 | 前端 CSS class |
|---|---|---|
| PHASE | bold cyan | `.ev-phase { color: cyan; font-weight: bold }` |
| AGENT start | blue | `.ev-agent { color: #58a6ff }` |
| AGENT success | green | `.ev-agent-ok { color: green }` |
| AGENT fail | red | `.ev-agent-fail { color: red }` |
| TOOL 🔧 | yellow | `.ev-tool { color: yellow }` |
| LLM 💭 | magenta | `.ev-llm { color: magenta }` |
| ERROR | bold red | `.ev-error { color: red; font-weight: bold }` |
| INFO / WARNING | cyan / yellow | `.ev-info` / `.ev-warn` |

- **像素近似（诚实局限）**：终端色板 ≠ 浏览器色域，前端用等价 hex 近似（如 cyan→深色主题下 `#58a6ff`），**语义同、色值近似但不像素级同**。这是诚实的近似复刻，不是像素级截图。
- **spinner 等价复刻**：终端 `Spinner("dots")` 是 braille 字符帧；前端用 CSS 动画复刻同样 braille 帧序列或 `react-spinners` dots，动画效果一致。
- **前端比终端多的**：可滚动回看历史、可搜索过滤、可折叠、复制友好。
- **elapsed 零后端 tick**：拿到首个事件后前端本地 `setInterval(1s)` 自增，省 SSE 带宽（ndjson 快照模型的好处）。

### MD→HTML 渲染（`MarkdownView.tsx`）

`react-markdown` + `rehype-highlight`（代码块语法高亮）+ `rehype-slug` + `rehype-autolink-headings`（目录锚点）。左 TOC 右正文，长报告可折叠。最终报告 `comprehensive_security_assessment_report.md` 直接渲染。**后端只返 md 原文，纯前端渲染。**

### 联动 yaml 编辑器（`YamlEditor.tsx`）

`@monaco-editor/react`（VS Code 同款，yaml 语法 + 校验）。手写新建存 `configs/web-multi-{name}.yaml`。提交前前端用 `yaml` 库 parse 校验，后端 `MultiRepoConfigStore` 再用 `parse_multi_repo_config` 强校验。

### SSE hook（`useEventSource`）

封装 `EventSource`，自动重连，事件 → 累积到 React state → 触发 dashboard 重渲染。`scan_end` 事件关闭流 + 标记完成。断连时带 `Last-Event-ID` 从断点续 tail。

---

## 错误处理

| 场景 | 处理 | 用户可见 |
|---|---|---|
| Temporal 未运行 | `/api/scan` 前置检查 `localhost:7233`，不通 → 400 拒绝 | "Temporal 服务未运行，请先 `docker-compose up -d`" |
| git clone 失败 | `GitFetcher` 捕获，stderr 脱敏 token 后透传 | "clone 失败：<脱敏后的 git 错误>"；凭证缺失单独提示 |
| GitLab 凭证缺失 | 启动时检查 env，缺则标记 git 模式不可用 | 表单 git 选项 disabled + tooltip |
| 扫描子进程崩溃 | ScanManager 监 `returncode≠0`，读 stderr 写 `events.ndjson` 的 `scan_error` 事件 | 实时 tab 显示失败状态 + 错误摘要；列表页标 ✗ |
| 扫描超时 | `SHANNON_WEB_SCAN_TIMEOUT` 非 0 时，到点 SIGINT 优雅停（0=不限，永不触发） | 实时 tab 标"已超时取消" |
| events.ndjson 损坏行 | `EventTailer` 跳过无法 parse 的行，计数告警 | 该行显示灰色"(events 日志解析失败)"，不中断流 |
| 客户端 SSE 断连 | `EventSource` 自动重连；重连时带 `Last-Event-ID` 从断点续 tail | 无感重连；断连期间状态条冻结 |
| workspace 目录被外部删 | `WorkspacesIndexer` 容错跳过；详情页 404 | 列表不显示该 ws；详情页"项目不存在" |
| 并发超限 | 超过 `SHANNON_WEB_MAX_CONCURRENT` → 409 | "已有 N 个扫描在跑，请等待或调高并发上限" |
| 联动 yaml 校验失败 | `parse_multi_repo_config` 强校验，失败 → 422 + 行号 | 编辑器标红 + 错误信息 |

**核心原则**：Web 进程绝不因扫描崩溃而崩——subprocess 隔离保证这点。所有扫描侧异常都被捕获、序列化成事件、在前端以状态呈现，不抛回 Web 主循环。

---

## 测试策略

分层 + **不广跑预存挂起套件**（遵循 `CLAUDE.md` 测试陷阱约定）：

| 层 | 范围 | 方式 |
|---|---|---|
| **core 单元** | `StructuredEventRenderer` ndjson 格式 / env 开关 / 并发安全 / `scan_end` 收尾 | 纯单测，复用 dispatcher lock 测试模式 |
| **web 单元** | `WorkspacesIndexer`(状态判定) / `DeliverablesReader`(md/json/log 读取) / `MultiRepoConfigStore`(yaml 校验存取) / `GitFetcher`(URL 注入凭证 + stderr 脱敏) | 纯单测，tmp workspace fixture |
| **ScanManager** | subprocess 启停 / 并发限流 / 超时取消 / 崩溃捕获 | 用 mock CLI 子进程（短 sleep 脚本）替代真扫描，**不真跑 Temporal** |
| **EventTailer** | offset 续传 / `scan_end` 关闭 / 损坏行跳过 / SSE 编码 | tmp ndjson 文件 |
| **API 集成** | FastAPI TestClient 打各端点 / SSE 流（httpx async） | mock ScanManager + 真实 WorkspacesIndexer |
| **前端** | 组件渲染 / SSE hook / dashboard 状态机 / yaml 编辑器校验 | vitest + testing-library |
| **不做** | 端到端真扫描 | Temporal/GitNexus 慢+预存挂起，留人工冒烟 |

**关键测试铁律**：`StructuredEventRenderer` 是唯一碰 core 的改动，**它的单测必须独立绿**——证明 Web 平台对 core 的侵入被这个 renderer 的契约完全封死。

---

## 部署

复用现有 `docker-compose.yml`，新增 web 服务：

```yaml
services:
  # 现有: temporal / gitnexus / ...
  web:
    build: packages/web
    ports: ["${SHANNON_WEB_PORT:-7878}:7878"]
    volumes:
      - ./workspaces:/app/workspaces        # 共享扫描产物
      - ./repos:/app/repos                  # git clone 目标
      - ./configs:/app/configs              # multi-repo.yaml
      - ./.env:/app/.env:ro                 # GITLAB_USER/TOKEN + SHANNON_PROFILE
    environment:
      - SHANNON_WEB_MAX_CONCURRENT=1
      - SHANNON_WEB_SCAN_TIMEOUT=0
    depends_on: [temporal]
```

### 部署约束

1. web 容器要能 `exec` 扫描器 CLI → 同一镜像装 `shannon-whitebox`/`-blackbox`/`-multi`（或共享 venv）。
2. `workspaces/` 卷共享 = web 读产物 + 子进程写产物（同卷，无跨进程传递）。
3. 子进程 env 继承 web 容器 env（`SHANNON_PROFILE`/`GITLAB_*`/`SHANNON_WEB_EVENT_FILE` 由 ScanManager 注入）。
4. **本地开发模式**：`uv run uvicorn shannon_web:app` + 前端 `npm run dev`（Vite proxy），绕过 docker。

### 环境变量清单（附录）

```
SHANNON_WEB_PORT=7878
SHANNON_WEB_MAX_CONCURRENT=1        # 并发扫描上限
SHANNON_WEB_SCAN_TIMEOUT=0          # 单扫描 wall-clock 超时秒(0=不限)
SHANNON_WEB_EVENT_FILE=<由 ScanManager 注入,非用户配>
GITLAB_USER / GITLAB_TOKEN          # git clone 凭证
# 其余 SHANNON_PROFILE/SHANNON_AI_PROVIDER 复用现有
```

---

## 关键风险与缓解

1. **联动子扫描 events 不全**：关联阶段 per-edge agent 走 `AgentExecutor` 不经 dispatcher，edge 内部细粒度事件可能缺失。缓解：编排器层（repo 级 + edge 级）进度能覆盖，对"看整体进度"够用；局限实现时写明。
2. **subprocess stdout/stderr 处理**：子进程 `workflow.log` 已落盘，stdout/stderr 要异步读取避免管道阻塞。ScanManager 用 `asyncio.create_subprocess_exec` + 后台读行任务。
3. **events.ndjson 与现有 `workflow.log` 关系**：`workflow.log` 是 `FileRenderer` 写的纯文本日志（现有）；`events.ndjson` 是 `StructuredEventRenderer` 写的结构化事件（新增）。两者并行，不互斥——`workflow.log` 给人读，`events.ndjson` 给 Web 消费。LogsTab 同时展示两者。
4. **Temporal 预存慢/挂起**：Web 不解决，前置检查 + 友好报错，不阻塞 Web 进程。

---

## 范围与拆分

本 spec 聚焦"Web 平台 v1"：开启扫描（三类型）+ 结果展示（HTML/产物/日志）+ 实时 dashboard 复刻。后续可演进项（不在本 spec）：跨 workspace 合并视图、漏洞趋势统计、多用户/鉴权、报告导出 PDF。规模适中，单个实现计划可覆盖，无需进一步拆分。
