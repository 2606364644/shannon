# Shannon Web Platform — 子项目 1：后端 + core renderer + 契约

> 上位设计：`docs/superpowers/specs/2026-07-02-shannon-web-platform-design.md`（总体架构、ndjson 事件 schema 三方硬契约、扫描类型、错误处理、测试/部署策略均在那里定稿，本子 spec 不重复，只聚焦子项目 1 的实现细节）。

## 范围

子项目 1 交付**后端 + 对 core 的唯一改动 + 联动小增强 + 部署 wiring**，可独立冒烟（curl + SSE 客户端验证全流程，不依赖前端）：

1. `StructuredEventRenderer`（core 唯一改动）+ 单测
2. `packages/web` 后端全部：6 组件 + FastAPI app + 全部 API + SSE
3. 联动小增强：orchestrator 写 correlation ndjson（asyncio.Lock）
4. docker-compose 加 web 服务 + Dockerfile
5. 后端单测（测试策略表前 5 层）

**不含**：前端 SPA（子项目 2）、跨 workspace 合并视图、鉴权。

---

## 1. `StructuredEventRenderer`（core 改动）

### 1.1 位置与契约

- **文件**：`packages/core/src/shannon_core/display/structured_event_renderer.py`
- **契约**：`async render(event: DisplayEvent)`，把 event 序列化成一行 JSON 追加写到独立 aiofiles 句柄，每行 flush。
- **写盘方式（已定）**：**独立句柄**——renderer 自持 `aiofiles.open(path, "a")` 句柄，每事件 `await f.write(line + "\n"); await f.flush()`。不复用 `LogStream`/`FileLogRenderer`（它们写 `workflow.log` 纯文本，是不同消费者），独立句柄互不干扰，单测直接 mock 文件路径。
- **序列化**：按总体 spec「ndjson 事件 schema」节——通用字段 `{ts, category, type}` + 各 event 类型附加字段。dataclass → dict 用 `dataclasses.asdict`，但 `parameters`(any) / `steps`(tuple) 等需 JSON 安全转换（tuple→list）。
- **只写原子 `DisplayEvent`**，不写聚合快照（renderer 契约只能拿 event）。前端 reducer 复刻 `DashboardState.apply`。

### 1.2 挂载点（改 1 处）

`packages/core/src/shannon_core/audit/workflow_logger.py:62-83` 的 `initialize()` 里 renderers 组装段，在 `:82` 后加一个分支：

```python
renderers: list = [FileLogRenderer(self._stream)]
if self._console is not None:
    renderers.append(RichConsoleRenderer(...))
if self._use_rich and self._dashboard is not None:
    renderers.append(self._dashboard)
# 【新增】Web 事件落盘 renderer（env 启用）
web_event_file = os.environ.get("SHANNON_WEB_EVENT_FILE")
if web_event_file:
    from shannon_core.display.structured_event_renderer import StructuredEventRenderer
    renderers.append(StructuredEventRenderer(web_event_file))
self._dispatcher = DisplayDispatcher(renderers)
```

- env 读取范式对齐 `config/concurrency.py` 的散读风格（`os.environ.get`），不强行集中。
- 未设 env = 不挂 = 行为完全不变（零影响现有 CLI）。
- **`import os`** 需在 `workflow_logger.py` 顶部确认已导入（若无须补）。

### 1.3 生命周期与收尾

- **open**：renderer `__init__` 时打开句柄（或首次 render 时 lazy open，避免空文件——推荐 lazy，仅当有事件才创建文件）。
- **close**：renderer 提供 `async close()` flush+关句柄。挂在 `WorkflowLogger.close()`（`workflow_logger.py:243-247`，在 `run_with_display` 的 `finally` 里必然调）——遍历 renderers 调 close（FileLogRenderer 等无 close 的忽略/加空 close）。
- **`scan_end` 双路兜底**（总体 spec 已定，此处细化）：
  - ① renderer 收到 `SummaryEvent` → 写 `{"type":"scan_end","status":<SummaryEvent.status>,...}`。
  - ② ScanManager 监子进程退出，若 ndjson 无 `scan_end`（SIGINT/OOM/崩溃，`SummaryEvent` 没发——已核实 `worker.py:274` 在 ScanCancelled/未捕获异常时不发）→ **ScanManager 补写** `{"type":"scan_end","status":"killed|crashed","returncode":N,"stderr_tail":"<末 2KB>"}`。这是必需的，不是可选。

### 1.4 单测（独立绿，铁律）

`packages/core/tests/display/test_structured_event_renderer.py`：

- ndjson 格式：喂各类型 event，断言每行 JSON 含 `{ts,category,type}` + 附加字段；tuple→list 转换正确。
- env 开关：`SHANNON_WEB_EVENT_FILE` 未设 → renderer 不挂（验证 dispatcher renderers 列表）；设了 → 挂载。
- 并发安全：复用 dispatcher lock 测试模式（并发 dispatch N 事件，ndjson 行数 = N，无交错断行）。
- `scan_end` on SummaryEvent：喂 SummaryEvent，断言写出 scan_end 行。
- lazy open：无事件时不创建文件。
- **此测试独立绿 = Web 平台对 core 的侵入被 renderer 契约完全封死**。

---

## 2. `packages/web` 后端

### 2.1 包结构

```
packages/web/
├── pyproject.toml              shannon-web 包定义(fastapi/uvicorn/aiofiles 依赖)
├── src/shannon_web/
│   ├── __init__.py
│   ├── app.py                  FastAPI app + 路由注册 + lifespan
│   ├── config.py               env 读取(SHANNON_WEB_* / GITLAB_* / 复用 SHANNON_PROFILE)
│   ├── components/
│   │   ├── workspaces_indexer.py
│   │   ├── scan_manager.py
│   │   ├── event_tailer.py
│   │   ├── deliverables_reader.py
│   │   ├── multi_repo_config_store.py
│   │   └── git_fetcher.py
│   ├── api/
│   │   ├── workspaces.py       GET /api/workspaces, /api/workspaces/{ws}...
│   │   ├── scan.py             POST /api/scan, DELETE /api/scan/{ws}
│   │   ├── deliverables.py     产物读取/下载
│   │   ├── events.py           SSE /api/workspaces/{ws}/events
│   │   └── multi_configs.py    联动 yaml CRUD
│   └── models.py               Pydantic 请求/响应模型
└── tests/                      见 §4
```

### 2.2 组件细节

**`WorkspacesIndexer`**
- 扫 `workspaces/*/` 读 `session.json`。**兼容新旧两格式**（扁平 / 嵌套 `session` 子对象，`session.py:104-154` 同时处理两格式——后端用 `SessionManager` 或 `get_status()` 读，不自己 parse）。
- 状态判定（总体 spec 三态+一特殊）：✓completed / ✗failed / ●进行中（本会话启动且 pid 活）/ ⚠未正常结束。
- **漏洞数**：不在 session.json，调 `get_workspace_vuln_counts()`（`workspace.py:111-130`）聚合各 `*_exploitation_queue.json` 的 `vulnerabilities` 数组长度。
- 联动 workspace（scan_type=correlation）在列表显 🔗 主行；子白盒 ws 不平铺。

**`ScanManager`**（核心）
- 三种扫描统一 subprocess：`asyncio.create_subprocess_exec` 起 `shannon-whitebox`/`-blackbox`/`-multi` CLI。
- env 注入：`SHANNON_WEB_EVENT_FILE=workspaces/{ws}/events.ndjson` + 继承容器 env（`SHANNON_PROFILE`/`GITLAB_*`）。
- **stdout/stderr 异步读**：后台 asyncio task 读行，避免管道阻塞；stderr 末 2KB 缓存供崩溃时补写 `scan_end`。
- 并发限流：`asyncio.Semaphore(SHANNON_WEB_MAX_CONCURRENT, 默认1)`，超限 → 409。
- 超时：`SHANNON_WEB_SCAN_TIMEOUT`（默认0=不限），到点 SIGINT 优雅停（复用 CLI 双击退出语义）。
- 取消：`DELETE /api/scan/{ws}` → SIGINT 子进程。
- **子进程退出监控**：监 `returncode`，若 ndjson 无 `scan_end` 补写（见 1.3②）。
- pid 注册表：本会话启动的 ws → pid，供进行中状态判定；Web 重启后 pid 丢失，老 ws 落入 ⚠未正常结束。
- 僵尸清理：lifespan 启动时扫无主子进程。

**`EventTailer`**
- tail `events.ndjson`，记 byte offset，`tail -f` 语义。
- SSE 编码：每行 JSON → `data: {json}\n\n`；`scan_end` → 发完关闭流。
- 损坏行跳过计数，不中断流。
- 断连重连：`Last-Event-ID`（用 byte offset）从断点续 tail。

**`DeliverablesReader`**
- 读 `deliverables/`。**兼容新旧布局**：新分轨 `deliverables/{whitebox|blackbox}/*`，旧平铺 `deliverables/*`——用 `resolve_track_deliverable()`（`paths.py:129-140`）回退。
- md 原文返回（前端渲染）；queue.json 返回 JSON；支持下载。
- 产物清单：调 `compute_deliverables_summary()`（`workspace.py:73-99`）返回 `{vuln_queues, reports}`。
- 空文件优雅处理（如 `attack_chains.json` 常为 `[]`）。

**`MultiRepoConfigStore`**
- 管 `configs/web-multi-*.yaml`：列/读/写。
- 写入用 `parse_multi_repo_config`（`config/parser.py`）强校验，失败 → 422 + 行号。
- 临时落盘（联动「直接运行」）：`configs/web-multi-tmp-{ts}.yaml`，扫完保留。

**`GitFetcher`**
- 裸 URL → 注入 `https://${GITLAB_USER}:${GITLAB_TOKEN}@` clone 到 `repos/<name>/`。
- **branch/commit**（总体 spec 已定）：指定分支 `git clone --branch <branch>`；指定 commit → clone 后 `git fetch --all && git checkout <commit>`；commit 优先级高于 branch。
- 重复策略：已存在 → `git pull`；pull 失败 → 删了重 clone；「强制重新 clone」跳过 pull。
- stderr 脱敏 token（正则替换 `GITLAB_TOKEN` 字面量）。
- 凭证缺失 → git 模式标记不可用（启动检查）。

### 2.3 API

按总体 spec「API」节实现，全部 REST + 一个 SSE。关键点：
- `POST /api/scan` 前置检查 Temporal `localhost:7233` 不通 → 400。
- `POST /api/scan` 并发超限 → 409；yaml 校验失败 → 422。
- SSE 端点 `GET /api/workspaces/{ws}/events` 用 `StreamingResponse` + `text/event-stream`。

### 2.4 配置（`config.py`）

```
SHANNON_WEB_PORT=7878
SHANNON_WEB_MAX_CONCURRENT=1
SHANNON_WEB_SCAN_TIMEOUT=0
GITLAB_USER / GITLAB_TOKEN
# SHANNON_PROFILE / SHANNON_AI_PROVIDER 复用现有 env_loader
```

---

## 3. 联动小增强（orchestrator）

`packages/multi/src/shannon_multi/orchestrator.py` 加 ndjson 写入：

- correlation workspace 的 `events.ndjson` 路径 = `deliverables_dir_for_workspace(out_ws).parent / "events.ndjson"`（即 correlation workspace 根，与子白盒 ws 同位）。
- **asyncio.Lock 保护 append**（多 repo 顺序 + edge 并发，append 需串行化）。
- 关键节点写 `correlation_progress` 事件（总体 spec 已定 schema）：
  - 每个 repo 扫描开始/结束：`{"node":"repo","name":<service>,"status":"started|completed|failed"}`
  - 关联阶段开始：`{"node":"phase","name":"correlation","status":"started"}`
  - 每个 edge 完成：`{"node":"edge","name":"<from->to>","status":"<edge status>"}`
  - 全部完成：写 `scan_end`（status 取整体结果）。
- **不动 orchestrator 业务逻辑**，只在关键节点加几行 ndjson append。
- 诚实局限（总体 spec 已定）：edge 内部 agent 细粒度事件不进 ndjson（走 AgentExecutor 不经 dispatcher）。

---

## 4. 测试（前 5 层）

| 层 | 文件 | 范围 |
|---|---|---|
| core 单元 | `packages/core/tests/display/test_structured_event_renderer.py` | 见 §1.4 |
| web 单元 | `packages/web/tests/test_workspaces_indexer.py` | 新旧 session.json 格式 / 状态四态 / 漏洞数聚合 |
| | `test_deliverables_reader.py` | 新旧布局 / md/json 读取 / 空文件 |
| | `test_multi_repo_config_store.py` | yaml CRUD / 校验失败 422 |
| | `test_git_fetcher.py` | URL 注入凭证 / branch/commit checkout / stderr 脱敏 / 重复 clone 策略 |
| ScanManager | `test_scan_manager.py` | mock CLI 子进程(短 sleep 脚本)启停 / 并发限流 / 超时取消 / 崩溃捕获 + scan_end 补写 |
| EventTailer | `test_event_tailer.py` | offset 续传 / scan_end 关闭 / 损坏行跳过 / SSE 编码 |
| API 集成 | `test_api_*.py` | FastAPI TestClient 打各端点 / SSE 流(httpx async)；mock ScanManager + 真实 WorkspacesIndexer |

**遵循 CLAUDE.md 测试陷阱**：只跑 `packages/web/tests/` + `packages/core/tests/display/test_structured_event_renderer.py`，不广跑全套。不真跑 Temporal（mock CLI 子进程）。

---

## 5. 部署 wiring

- `packages/web/Dockerfile`：基于现有 Python 镜像，装 `shannon-whitebox`/`-blackbox`/`-multi`（同一 venv）+ `shannon-web` + fastapi/uvicorn。
- `docker-compose.yml` 加 `web` 服务（总体 spec 已给 yaml）：ports/volumes/environment/depends_on。
- **本地开发模式**：`uv run uvicorn shannon_web.app:app --reload` + 手动起 Temporal，绕过 docker。

---

## 6. 冒烟验收（独立于前端）

子项目 1 完成的验收标准（curl + SSE 客户端，不需前端）：

1. `curl localhost:7878/api/workspaces` → 返回现有 workspace 列表（含状态/漏洞数）。
2. `curl -X POST localhost:7878/api/scan -d '{"type":"whitebox","source":{"kind":"path","value":"/root/code/foo"},"url":"http://example.com"}'` → 202 + workspace 名。
3. `curl -N localhost:7878/api/workspaces/{ws}/events` → SSE 流，看到 PHASE/STEP/AGENT 事件 + scan_end 收尾。
4. 扫描中 `curl localhost:7878/api/workspaces/{ws}` → 状态 ●进行中。
5. 扫描完 `curl localhost:7878/api/workspaces/{ws}/report` → 返回 md 原文。
6. `curl localhost:7878/api/workspaces/{ws}/deliverables` → 产物清单。
7. 联动：`POST /api/scan` type=correlation + 手写 yaml → correlation workspace 事件流含 correlation_progress。
8. git URL 模式：clone + branch/commit checkout 正确。
9. 崩溃场景：杀子进程 → ScanManager 补写 scan_end，curl SSE 流收到 `killed`/`crashed` 并关闭流。

---

## 7. 风险

1. **`workflow_logger.py` 改动波及现有 CLI**：加 renderer 分支是纯 additive（env 未设不挂），但 `import os` / close 遍历需谨慎不破坏现有 renderers。缓解：单测覆盖 env 未设路径 + 现有 display 测试全绿。
2. **subprocess 管道阻塞**：stdout/stderr 必须异步读，否则子进程写满管道缓冲区会卡死。缓解：后台 asyncio 读行 task + 测试用大量输出的 mock CLI。
3. **联动 ndjson 与子白盒 ndjson 路径区分**：correlation ws 的 events.ndjson 在 correlation ws 根，子白盒的在各子 ws 根——EventTailer 按 ws 路径定位，不混淆。
4. **session.json 新旧格式**：用 SessionManager 读不自己 parse，避免格式漂移 bug。
