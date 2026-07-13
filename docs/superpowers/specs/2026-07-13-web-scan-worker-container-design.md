# WEB 扫描执行模型重构：独立常驻 worker 容器（方案 C1）

> 状态：设计稿（2026-07-13 修订 v2，纳入：黑白盒共用镜像 / 黑盒 in-process browser 模型 / 并发选项 / run_scan 拆分展开 / B 作为前置）
> 日期：2026-07-13
> 分支：feat/fork-py

---

## 1. 背景

### 1.1 当前 WEB 扫描执行模型（白盒 + 黑盒同构）

WEB 页面启动扫描时，`scan_manager.start()`（`packages/web/src/shannon_web/components/scan_manager.py:63`）在 **web 容器内部** `asyncio.create_subprocess_exec` fork 出扫描 CLI 子进程：

- 白盒（`:181`）：`shannon-whitebox start -r <repo> --url <url> -w <ws> --temporal-address <host:port>`
- 黑盒（`:183-186`）：`shannon-blackbox start --url <url> --repo <repo> -w <ws> --temporal-address <host:port>`

两者都是 **self-contained 的「worker + starter 合一」进程**：
- 白盒 `shannon_whitebox/worker.py:106 run_scan`：connect → `generate_task_queue("shannon-py-wb")` 唯一 queue → 注册 `Worker` → `client.start_workflow` → await（自己提交、自己消费）。
- 黑盒 `shannon_blackbox/worker.py:99 run_scan`：同构，`TASK_QUEUE_PREFIX="shannon-py-bb"`（`:43`），`BlackboxScanWorkflow`（`:134`）。

**黑盒的 browser 验证是 in-process**（非独立容器）：默认引擎 `agent-browser`（`blackbox/pipeline/activities.py:520`），`check_available()` = `shutil.which("agent-browser")`（PATH 上的 npm 二进制），它派生**本地 headless Chrome 子进程**（cleanup 用 `pkill -f "headless.*profiles/{sid}"`，`agent_browser_engine.py:322`）。**全仓零 docker SDK / 零容器创建**（`import docker` / `docker.from_env` / `containers.run` 在 `packages/` 下零命中）。

宿主 CLI 扫描同理（self-contained），与 WEB 共享同一个 temporal 实例（`shannon-py-temporal`，`127.0.0.1:7233`），通过 `workspaces/` bind mount 的文件协作（`session.json[owner]` / `heartbeat` / `cancel.requested`）。

### 1.2 两个问题

- **问题 A（WEB 扫描 broken，白盒黑盒都 broken）**：web 容器镜像（`packages/web/Dockerfile`，`python:3.12-slim`）只 apt `git ca-certificates`，缺两套依赖：
  - 白盒：缺 **gitnexus + node** → `run_code_index` activity 硬失败、不降级（`whitebox/pipeline/activities.py:773-782`，注释 "no degradation"）。
  - 黑盒：缺 **chrome + agent-browser** → preflight `resolve_blackbox_engine`（`blackbox/pipeline/activities.py:529`）抛 `BROWSER_ENGINE_UNAVAILABLE`（非可重试）。
  - `scan_manager.py:92` 的 `SHANNON_SKIP_PREREQUISITES=1` 只跳过 `ensure_prerequisite` 的交互式二进制检查（非交互子进程无 TTY，`click.confirm` 会 EOF 崩），**救不了 activity 本身的硬闸**。近期 `78881cfa` 只加了容器内 `safe.directory`（且写法存疑，见 §9），**没装 gitnexus/chrome**，问题 A 仍在。
- **问题 B（扫描影响 web 服务）**：扫描是 web 容器的子进程，共享 web 容器的 CPU / 内存 / cgroup 限额。大仓扫描（如 kol 569 chunk）CPU 密集，会抢占 web 服务资源；扫描 OOM 可能触发容器 OOM 影响稳定性。

### 1.3 不走「WEB 触发宿主 CLI」的原因

web 容器**未挂 docker socket、无任何宿主通信通道**（`docker-compose.yml` web service volumes 实测确认）。「WEB 触发宿主 CLI」需新增宿主通信通道——挂 `docker.sock` 等于给容器 root + 容器逃逸面（公认反模式）；SSH / 宿主 launcher 引入宿主强耦合，破坏容器自包含与可移植性。**判定为反模式，不采用。**

---

## 2. 目标 / 非目标

### 目标
1. WEB 白盒 + 黑盒都能跑完整流程——worker 容器装齐 gitnexus（白盒）+ chrome/agent-browser（黑盒）。
2. 扫描不影响 web 服务——扫描逻辑移出 web 容器，独立 worker 容器 + resource limits。
3. 符合 temporal 标准架构——starter（web 提交）/ worker（worker 容器消费）分离。
4. **宿主 CLI 零改动**——CLI 路径保持 self-contained，行为不变。

### 非目标
- C2 按需「一扫描一容器」（需 docker.sock 或 K8s，反模式 / YAGNI）。
- 上 K8s / Nomad 编排器。
- 改动双轨判定逻辑。
- 改动黑盒 browser 验证模型（保持 in-process agent-browser，不引入 browser 容器）。
- 改动宿主 CLI 路径。
- correlation（`shannon-multi` 多仓编排）模型不同（orchestrator 编排多个白盒/黑盒扫描），本 spec 不展开；C1 落地后可比照复用 worker 容器。

---

## 3. 方案对比（B 重定位为 C1 前置）

| 方案 | WEB 能扫 | 扫描不影响 web | 改动量 | 结论 |
|------|:---:|:---:|:---:|------|
| A. WEB 触发宿主 CLI | ✓ | ✓ | 中 | docker.sock/SSH 反模式，破坏隔离 |
| **B. 扫描全栈装进 web 容器** | ✓ | ❌（仍共容器） | 小 | 不解问题 B，但**作为 C1 前置**（共用镜像先验证依赖齐全，run_scan 零改动） |
| **C1. 独立常驻 worker 容器** | ✓ | ✓ | 大 | **temporal 标准分离，推荐（本 spec 主体）** |
| C2. 每次扫描新容器 | ✓ | ✓✓ | 大 | docker.sock 陷阱或上 K8s，YAGNI |
| D. 维持现状（只用 CLI） | ❌ | n/a | 0 | WEB 扫描名存实亡 |

**选定 C1，B 作为前置第一步。** B 先把共用镜像做出来、验证依赖齐全（解问题 A，run_scan 零改动零回归），C1 再加 worker service + 拆 run_scan（解问题 B）。详见 §15 迁移。

---

## 4. 目标架构（C1）+ 黑白盒共用镜像

```
  浏览器             web 容器 (瘦)           temporal 容器          worker 容器 (新·胖·共用镜像)
    │  POST /scan      │   start_workflow()    │                      │ 跑 WhiteboxScanWorkflow
    │ ────────────────▶│ ────────────────────▶│ ─── dispatch task ──▶│ + BlackboxScanWorkflow
    │                  │                      │                      │ + 全部 activities
    │  SSE 进度         │                      │                      │ (装 gitnexus+node+chrome+agent-browser)
    │ ◀────────────────│ ◀── tail events.ndjson (workspaces 卷) ────│
```

**三条流：**
- **控制流**：浏览器 → web → `client.start_workflow()` → temporal → dispatch → worker 容器执行。
- **数据流**：worker 容器写 `workspaces/<ws>/events.ndjson` → web SSE tail → 浏览器。
- **状态流**：worker 容器写 `session.json` / `heartbeat` → web 读（判活 / 状态展示）。

| 容器 | 职责 | 变化 |
|------|------|------|
| **web（瘦）** | FastAPI + SSE，收请求→提交 workflow→tail 事件 | 去掉 `create_subprocess_exec`、`SHANNON_SKIP_PREREQUISITES` 妥协、`_build_argv`；新增 temporal `Client` + start_workflow 编排 |
| **worker（新·胖）** | 常驻 temporal worker，装完整扫描栈，注册 workflow+activities 消费固定 task queue | 全新容器 |
| **temporal（不变）** | workflow 编排 / 重试 / 超时 | 不动 |

### 关键决策：黑白盒共用一个镜像

理由：
- **执行模型同构**：两者都是 `shannon-*-box start` → self-contained `run_scan`（connect→唯一 queue→Worker→start_workflow），连复用的 `ShutdownController` / `await_workflow_with_shutdown`（`core/runtime/scan_runner.py`）都同一套。
- **基础层完全公共**：python:3.12-slim + uv sync（同一份 root pyproject workspace）是两者的底；差异只在额外工具层——白盒 +gitnexus/node，黑盒 +chrome/agent-browser。这些是独立的 apt/npm 层，叠在一个镜像里互不干扰。
- **维护成本**：一个 Dockerfile 单一来源（而非两个 90% 重复的镜像）。
- **worker 复用**：worker 容器用这个镜像，可同时注册白盒 queue 和黑盒 queue（或分两个 service 同镜像不同 task_queue）。

**worker 容器依赖清单（一个镜像装齐）：**
- 白盒：gitnexus@1.6.8 + node + ladybugdb binding（参考 `scripts/provision.sh` 系统级装法 + memory `gitnexus-1.6.7-real-machine-behavior`）。
- 黑盒：agent-browser（npm 全局，复用 node）+ chrome（`agent-browser install`，参考 `scripts/bootstrap.sh:95-149`）。
- 通用：safe.directory `*`（见 §9）。

---

## 5. 关键改动（含 run_scan 拆分展开）

1. **worker Dockerfile（共用镜像）**：基于 web 镜像 python 环境，加 node + gitnexus@1.6.8 + ladybugdb + agent-browser + chrome。装法参考 `scripts/provision.sh` + `bootstrap.sh`。
2. **新增 compose `worker` service**：用共用镜像；挂 `workspaces` / `repos` / `configs` / `.env` / `.env.profiles`（与 web 同业务卷）；设 `deploy.resources` 限额；`depends_on: temporal`。
3. **`scan_manager` 改造**：`create_subprocess_exec(...)` → `client.start_workflow(WhiteboxScanWorkflow.run / BlackboxScanWorkflow.run, input, task_queue=<固定 queue>)`。web 容器变为 workflow 提交者——需引入 temporal `Client`（复用 `shannon_core.services.temporal_infra` 连接逻辑），新增「提交 workflow + tail `events.ndjson` 推 SSE」编排（替代当前 `_watch` 子进程 stdout 的进度回显）。去掉 `_build_argv` / `SHANNON_SKIP_PREREQUISITES`。
4. **`run_scan` 拆分（核心工作量，白盒 + 黑盒各一套，对称处理）**：

   `run_scan` 当前是 self-contained runner，把 **10 步进程级编排耦合在一个进程**：

   | 步骤 | 白盒位置 | C1 后归属 |
   |---|---|---|
   | session 创建 | `worker.py:121` | web submit 端（scan_manager 已部分做 `:80-83`）|
   | event_file wiring | `worker.py:130` | web submit 端 |
   | owner 标记 | `worker.py:132` | web submit 端（已做 `:83`）|
   | Client.connect + 唯一 queue | `worker.py:134-135` | 拆：web 连 client 提交 / worker 固定 queue |
   | Worker 注册 | `worker.py:137-158` | worker 容器常驻 |
   | **resume 探测**（start_workflow 前读本地 session.json + deliverables cleanup）| `worker.py:166-253` | **移进 workflow 作前导 activity** |
   | **heartbeat + ShutdownController** | `worker.py:274-283` | **移进 workflow（进程级→workflow 级）** |
   | start_workflow + await | `worker.py:294-302` | 拆：web 只提交不 await / worker 执行 |
   | **summary 计算** | `worker.py:312-328` | **移到 worker 端（workflow 完成时）** |

   **核心难点**：`WhiteboxScanWorkflow.run` / `BlackboxScanWorkflow.run` 目前只做 config 解析 + ActivityInput 构造（`workflows.py:38-83`），**session/resume/heartbeat/display/summary 全在 workflow 外的 run_scan 里**。C1 要让 web 提交、worker 执行 workflow，这些外围逻辑必须重新分配，其中 **resume 探测 / heartbeat / summary 要移进 workflow 作为前导/后置 activity**。白盒黑盒对称拆分（黑盒 `run_scan` 与白盒同构，Explore 确认，具体迁移在 plan 阶段逐行对齐）。

5. **worker 容器入口**：常驻 `Worker(client, task_queue=<固定 queue>, workflows=[...], activities=[...])`，不再是一次性 CLI。可单入口注册两套 queue，或两 service 各一。
6. **event_file 路径传递**：当前 `scan_manager` fork 时 env 注入 `SHANNON_WEB_EVENT_FILE`；C1 改为塞进 `PipelineInput`（新增字段），worker 容器跑 workflow 时从 input 读。

---

## 6. task queue 隔离（CLI 零改动的基础）

`generate_task_queue(prefix)`（`core/services/temporal_infra.py:24-27`）生成 `{prefix}-{8hex}` **唯一** queue。当前 CLI / WEB 每个扫描进程各自生成唯一 queue、self-contained 闭环。

- **CLI 路径**：保持 `generate_task_queue(TASK_QUEUE_PREFIX)`（per-scan 唯一，self-contained）——**零改动**。CLI 不依赖 worker 容器在线。
- **WEB 路径**：改用**固定** task queue（白盒 `shannon-py-wb-web` / 黑盒 `shannon-py-bb-web`），worker 容器常驻注册该 queue 消费。web 提交时指定该 queue。
- 两条路径共享 temporal 但 queue 不同，**互不消费、互不干扰**。

> 待 plan 阶段确认：是否拆 `TASK_QUEUE_PREFIX` 成 CLI 子前缀（随机）与 WEB 固定名两套常量。

---

## 7. 并发决策（含隔离增强选项）

三层并发控制：
1. **worker 级**：`max_concurrent_workflow_tasks` / `max_concurrent_activity_tasks`（当前 `worker.py:137` Worker 未显式设，用 SDK 默认；C1 显式设）。
2. **容器级**：docker `deploy.resources.limits`（cpu/memory），预留 web + temporal 份额。
3. **LLM 级**：复用 `SHANNON_MAX_CONCURRENT`，从「单扫描内并发」扩展到「跨扫描全局并发」（防 N 个扫描 LLM 调用叠加撞 API rate limit）。

**隔离增强选项（无需 docker.sock 拿到接近 C2 的隔离）：并发=1 + 多副本**
- 预起 N 个常驻 worker 容器（N=期望并发数），每个 `max_concurrent_workflow_tasks=1`（一个 worker 一次只跑一个扫描）。
- temporal 天然支持：多 worker 副本注册同一 task_queue，自动负载均衡，互不抢占。
- 需要更多并发就 `docker compose up --scale worker=N`，**不需要 docker.sock**。
- 隔离性 ≈ C2（单扫描独占一个 worker 进程的内存/event loop），唯一不如 C2 的是「同镜像 N 容器共享宿主内核」——单机 dev 机无所谓。

- **默认**：轻度并发（2~3），上限可经 env 调。特别在意扫描间隔离 → 用「并发=1 + 多副本」。
- C2（按需一扫描一容器）因 docker.sock 直接排除。

---

## 8. 并发隐患（设计要处理）

1. **gitnexus 多 repo 并发 index**：多扫描同时 `gitnexus index` 不同 repo，全局 `registry.json` 是否写竞争？→ plan 阶段验证；必要时 index 步骤加分布式锁或确认 gitnexus 自身并发安全。
2. **大仓资源**：kol 类大仓单跑吃满资源，并发时需排队 / 优先级（temporal task queue 优先级或专用 worker pool）。
3. **LLM rate limit**：跨扫描全局 `SHANNON_MAX_CONCURRENT`。

---

## 9. safe.directory（与 78881cfa 协同 + 修正）

- 现状（`78881cfa`）：宿主 shannon-user 由 `ensure-shannon-user.sh` 设 safe.directory；web 容器 root 由 `Dockerfile` 设 `git config --global --add safe.directory '/app/repos/*'`。
- ⚠️ **待验证 / 倾向修正**：`78881cfa` 用的 `'/app/repos/*'` **带路径通配**，按本会话前期实测「git safe.directory 带路径通配不工作（`*` 不跨 `/`），只有整值 `*` 或精确路径有效」。worker 容器要用**确定有效**的方式——**倾向 `'*'`（全信任，与宿主 shannon-user 已落地方案一致）**。plan 阶段需实测容器内 git 行为定夺。
- worker 容器装 gitnexus 后扫 root / shannon-user 属主仓库，靠 safe.directory 信任避免 dubious ownership（`gitnexus_engine.py:92` index 调用内部走 git 判仓库）。
- 关联 memory `shannon-user-gitnexus-env-truth`（dubious ownership 根因链 + `*` 全信任方案）。

---

## 10. owner / heartbeat / cancel 协议调整

| | 当前 | C1 后 |
|---|---|---|
| web cancel | 持 pid → SIGINT（`scan_manager.py:124-130`） | 无 pid → `handle.terminate()` + `cancel.requested` 兜底 |
| owner 标记 | scan_manager fork 时 `_mark_owner(ws,"web")`（`:83`） | web 提交 workflow 时仍标 owner=web（worker 不覆盖） |
| 存活判定 | web 持 pid（自起）/ heartbeat（host 起）双轨 | 统一靠 heartbeat 文件 mtime（web 无 pid） |
| heartbeat 写入 | 扫描子进程 `HeartbeatManager` | worker 容器 `HeartbeatManager`（机制不变，迁移进 workflow） |

`scan_liveness` 跨容器判活协议天然适配 C1（本就为「web 看不到 host 进程」设计）。

---

## 11. 错误处理

- **worker 容器 OOM**：cgroup 隔离，OOM killer 只杀 worker 内超限进程，**web 不受影响**；temporal activity retry policy 兜底。
- **workflow 失败**：temporal 记录失败，web 读 `session.json` 状态展示给用户。
- **worker 容器宕机**：temporal task 重新分发（多副本时给其他 worker；单副本重启后续跑）。
- **gitnexus / browser 缺失**：activity 抛对应错误（`PentestError` / `BROWSER_ENGINE_UNAVAILABLE`），workflow 失败，web 展示（与宿主 CLI 同行为）。

---

## 12. 测试策略（TDD，项目惯例）

1. **scan_manager 改造**：单测 `create_subprocess_exec` → `start_workflow`（mock temporal client，断言 task_queue + input 含 event_file）。
2. **worker Dockerfile**：build 冒烟（容器内 `gitnexus --version` / `agent-browser --version` 可用、`gitnexus index` 测试仓 EXIT=0）。
3. **task queue 隔离**：集成测试（CLI 用 generate queue、WEB 用固定 queue，互不消费）。
4. **并发**：`max_concurrent_workflow_tasks` 控制测试 + `--scale` 多副本。
5. **cancel / 存活**：`handle.terminate()` + heartbeat mtime 判活测试。
6. **safe.directory 有效性**：worker 容器内扫非 root 属主仓库不触发 dubious ownership（覆盖 `78881cfa` 的 `/app/repos/*` 通配存疑点）。
7. **run_scan 拆分回归**：resume 探测 / heartbeat / display / summary 迁进 workflow 后，扫描端到端行为等价（这是拆分的核心风险点，重点回归）。
8. **CLI 回归**：宿主 CLI 扫描行为零改动回归（run_scan 拆分不能破坏 CLI self-contained 路径）。

---

## 13. 与近期改动的协同

- `78881cfa`（safe.directory + provision）：worker 容器 safe.directory 复用思路（但用 `*` 有效写法）；worker 容器装 gitnexus 参考 `provision.sh` 系统级装法。
- `c045c3a8`（temporalio worker 可观测性）：worker 容器复用 `SHANNON_TEMPORALIO_LOG_LEVEL`，常驻 worker 的 activity 日志可观测。
- `docs/superpowers/specs/2026-07-09-web-scan-liveness-deep-rework-design.md`（scan_liveness）：heartbeat 协议 C1 直接复用。
- `whitebox-browser-removal-cleanup-spec-plan`：白盒已去 browser，黑盒保留 in-process agent-browser——本 spec 不改此边界。

---

## 14. 风险 / Open Questions

1. **gitnexus 多 repo 并发 index 安全性**（§8）——plan 阶段验证。
2. **`*` safe.directory 有效性**（§9）——plan 阶段实测，worker 容器定有效写法。
3. **worker 容器镜像大小**：node + gitnexus + chrome + agent-browser ≈ 1GB，可接受（build 一次）。
4. **`.env` 语义变化**：`SHANNON_WEB_MAX_CONCURRENT` 从「web 并发起几个子进程」转为「worker 并发上限」，需迁移说明。
5. **run_scan 拆分回归风险**（§5/§12-7）：resume/heartbeat/display/summary 是当前 work 的现有逻辑（memory 多个 epic 围绕），迁进 workflow 要逐行保证等价，是本 spec 最高风险点。
6. **agent-browser 容器内安装**：参考 `bootstrap.sh`，但容器内首次 `agent-browser install`（装 chrome）需验证网络/权限；plan 阶段冒烟。

---

## 15. 迁移 / 兼容（B 作为前置第一步）

**Step 1（B 前置，解问题 A，run_scan 零改动）**：
- 扩展 web Dockerfile 装齐两套依赖（gitnexus@1.6.8 + node + ladybugdb + agent-browser + chrome）+ safe.directory `*`（修正 `78881cfa` 失效通配）。
- `bash scripts/up.sh rebuild` 重建 web 镜像。
- 结果：**WEB 立即能扫白盒 + 黑盒**（确定性轨 + LLM 轨 + browser 验证全通），run_scan 零改动、零回归。问题 A 解决，问题 B 未解（仍共容器）。

**Step 2（C1 worker service，解问题 B 隔离）**：
- 新增 compose `worker` service（用 Step 1 的共用镜像），`depends_on: temporal`，挂业务卷，设 resource limits。
- worker 容器常驻入口注册固定 task queue。

**Step 3（拆 run_scan，web 变提交者）**：
- `scan_manager` fork → `start_workflow`（提交到固定 queue，tail events.ndjson 推 SSE）。
- run_scan 外围逻辑迁移：session/event_file/owner → web submit；resume 探测/heartbeat/summary → 移进 workflow 前导/后置 activity。
- 白盒黑盒对称拆分。

**Step 4（cancel / 并发收尾）**：
- web cancel 改 `handle.terminate()` + `cancel.requested` 兜底。
- 并发：`max_concurrent_workflow_tasks` + resource limits + `SHANNON_MAX_CONCURRENT` 跨扫描全局；按需用「并发=1 + 多副本」。

- 旧 `session.json`（owner=web，pid 模式）：C1 后 web 无 pid，但 heartbeat 协议在，`scan_liveness` 已处理跨边界判活，兼容。
- **宿主 CLI：零迁移**（self-contained 路径 + `generate_task_queue` 唯一 queue 全不动）。

---

## 16. 决策摘要（供快速 review）

1. **走 C1，B 作为前置**（§3/§15）：先 B 让 WEB 立即能扫（共用镜像 + run_scan 零改动），C1 再解隔离。
2. **黑白盒共用一个镜像**（§4）：执行模型同构，单一 Dockerfile，worker 复用。
3. **黑盒 browser 保持 in-process**（§1.1/§2）：不引入 browser 容器，worker 容器装 agent-browser+chrome 即可。
4. **并发默认轻度，隔离强需求用「并发=1 + 多副本」**（§7）：不用 docker.sock 拿到接近 C2 的隔离。
5. **CLI 零改动**（§6/§15）：`generate_task_queue` 唯一 queue 不动，CLI self-contained 路径不受影响。
6. **最高风险 = run_scan 拆分回归**（§5/§14-5）：resume/heartbeat/display/summary 迁进 workflow 要逐行保证等价。
