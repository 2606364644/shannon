# 黑盒扫描 HOST 档案 + per-scan 代理设计

> 日期：2026-08-12
> 分支：feat/fork-py
> 状态：设计待 review（per-scan 代理可行性已端到端实测通过，2026-08-12，见 §7）
> 关联：[auth profile 档案库](./2026-08-10-auth-profile-system-fork-design.md)（HOST 档案镜像其 store/API/前端链路）、[exploitable PoC 生成](./2026-07-02-exploitable-poc-generation-design.md)
> 触发：多用户黑盒场景，不同用户要切换 host 扫不同地区环境——这些环境**域名相同、映射 IP 不同**。当前全 worker 共用一套 HOST（甚至代码层无任何域名→IP 注入机制），无法 per-scan 隔离。

---

## 0. 一句话结论

引入 **per-workspace 的 HOST 档案库**（镜像 auth profile，存 `域名→IP` 映射，支持手填或从 GET 链接 `/etc/hosts` 文本导入），每个黑盒扫描启动时**起一个 per-scan 本地代理**（proxy.py + 自定义 DNS 插件，持有该扫描的映射），让黑盒的**所有 HTTP 出口**（bash+curl、agent-browser、playwright、web_fetch、preflight httpx）统一走该代理，从而 per-scan 端口级隔离域名解析、多用户并发扫各环境互不冲突，且不破坏现有「单常驻 worker + Temporal 固定 queue」架构、无需每任务一容器。

---

## 1. 背景

### 1.1 容器/worker 架构（不变）

单常驻 worker 容器（`supernova-worker`）消费两个固定 Temporal queue（`supernova-wb-web` / `supernova-bb-web`，见 `packages/core/src/supernova_core/services/temporal_infra.py:30-35`），并发上限默认 4（`SUPERNOVA_WORKER_MAX_CONCURRENT_WF`）。worker 单进程内 `asyncio.gather` 并行跑白盒/黑盒两个 Worker（`packages/worker/src/supernova_worker/runner.py:103`）。**没有任何 per-scan 容器代码路径。**

### 1.2 当前无任何用户可配置的域名→IP 映射

穷尽搜索（`.env*`、CLI flag、scan-config schema、`ScanRequest`、docker-compose、chromium args）：无 `HOST`/`HOSTS`/`RESOLVE` env、无 `--resolve` flag、无 `extra_hosts`、无 scan config 字段、无 chromium `--host-resolver-rules`。目标完全由单个 `web_url` 经标准 DNS 解析，且 `validate_target_url` 会做 SSRF/loopback 拦截（`packages/core/src/supernova_core/utils/security.py:14-132`）。唯一存在的 host 处理是 preflight 的 DNS pinning，且 `pinned_ip` 只用于 reachability 探测、不传给下游 agent-browser。

### 1.3 黑盒所有 HTTP 出口清单（本设计必须全覆盖）

| # | 出口 | 用途 | 实现（触点已核实） | 代理注入方式（实测确认） |
|---|------|------|------|------|
| 1 | **bash + curl** | exploit 发 payload（LLM 主要手段） | `asyncio.create_subprocess_shell`（`tools_openai/exec.py:38`），**当前不传 env**（继承 worker env） | per-call `env={**os.environ,"HTTPS_PROXY":u,"HTTP_PROXY":u,"NO_PROXY":...}`；curl 读 env，**实测经代理落映射 IP**（HTTP + HTTPS CONNECT） |
| 2 | **agent-browser**（chromium） | 浏览器导航 | 命令前缀经 `{{BROWSER_SESSION_FLAG}}`（`prompts/manager.py:146`）**引擎层注入**，非 LLM 拼 | `session_flag`（`agent_browser_engine.py:117`）加 `--proxy <url>`；原生支持 `--proxy`/`--proxy-bypass`/`--args`/env，**实测 per-session hits 落点** |
| 3 | **playwright-cli**（备选引擎） | 浏览器导航 | `_build_stealth_config` 写 `.playwright/cli.config.json`（`playwright_engine.py:70`），CLI `--config` 读 | config 加 `browser.launchOptions.proxy={"server":url}`；**实测接受 + per-session 独立 browser 进程（不同 PID）** |
| 4 | **web_fetch**（LLM 工具） | 抓取页面 | httpx（`tools_openai/web.py:37`），**当前无 proxies** | per-call `proxies=`（`ToolContext` 加字段）；httpx 走代理 CONNECT，代理解析 |
| 5 | **preflight 可达性** | 扫描前探测 | httpx（`utils/security.py:102`）已用 pinned_ip+Host 头直连 | **不走代理**：IP 来源从 DNS 改查映射表，直连映射 IP（复用 pinned_ip） |
| 6 | **claude 引擎 Bash** | claude-agent-sdk 内置 bash+curl | CLI 子进程 env 由 `_build_sdk_env`（`providers_anthropic.py:193`）构造 | spawn 时注入 per-scan proxy env；CLI 内置 curl 读 env |

**实测结论（2026-08-12）**：两个浏览器引擎 + HTTP/HTTPS 双链路 + per-scan 隔离全通过；proxy.py `resolve_dns` 对 HTTP 请求**和 HTTPS CONNECT 隧道**均生效（`curl -x proxy https://target.test` 落映射 IP，铁证）。浏览器命令前缀由引擎层注入（非 LLM 拼），故 §2 方案 C「LLM 拼 curl 不可控」对浏览器不成立。脚本见 §7。

### 1.4 并发隔离的核心：注入作用域

| 注入层级 | 作用域 | 多用户并发 |
|---------|--------|-----------|
| `/etc/hosts` | 容器级（同容器进程共享） | 同域名不同 IP 互相打架 → 唯一解是每任务一容器 |
| **per-scan 本地代理** | **每扫描一个**（独立端口+映射表） | **端口天然隔离，单容器并发不冲突** |
| 多点分别注入 | 每扫描各自工具配置 | 理论可隔离，但 curl 命令 LLM 拼、不可控，覆盖必漏 |

---

## 2. 方案选择

| 方案 | 做法 | 取舍 | 结论 |
|---|---|---|---|
| **A（选定）** | per-scan 本地代理（proxy.py + 自定义 DNS 插件）；所有出口经 env/flag/proxies 走本扫描代理 | 覆盖全出口；per-scan 端口隔离并发不冲突；不破坏单 worker 架构；HTTPS 无需 MITM。代价：代理子进程起停+端口管理 | ✅ 选定 |
| B | 每任务独立容器 + `/etc/hosts` | 覆盖全出口且隔离彻底；但单容器并发冲突→必须每任务一容器，需重构常驻 worker+Temporal 固定 queue 架构，每扫描起装齐 chrome+gitnexus 重容器，启动延迟+资源开销大。大炮打蚊子 | ❌ 不值得 |
| C | 多点分别注入（chromium `--host-resolver-rules` + httpx pinned IP + curl `--resolve`） | 看似轻；致命：curl 命令由 LLM 现场拼，无法强制带 `--resolve`，覆盖必漏 | ❌ 安全扫描不能漏出口 |

**选 A**：唯一同时满足「全覆盖」「per-scan 并发隔离」「不动架构」的路径。技术可行性已端到端实测通过：proxy.py 官方 [custom DNS resolution 示例插件](https://github.com/abhinavsingh/proxy.py)，扩展点 `HttpProxyBasePlugin.resolve_dns(self, host, port) -> (ip, interface)`——对 HTTP 与 HTTPS CONNECT 均生效（2026-08-12 实测，见 §7）。

---

## 3. 范围

| 项 | 处理 | 说明 |
|---|------|------|
| `web/components/host_profile_store.py` | **新增** | 镜像 `auth_profile_store.py`；存 `workspaces/<ws>/host-profiles.yaml`；**不加密**（IP/域名非敏感凭据） |
| `web/api/host_profiles.py` | **新增** | CRUD（upsert）+ `POST /parse?url=`（GET 拉取+解析 `/etc/hosts`，不落盘，预览用）+ `POST /refresh/{pid}`（按 source_url 刷新快照）；权限同 auth profile（member 读/manager 写） |
| `web/models.py` | **改** | `ScanRequest` 加 `host_profile_id` / `host_url`（二选一，与 auth 互斥逻辑独立） |
| `web/components/scan_manager.py` | **改** | `_resolve_blackbox_inputs` 解析 host 档案/链接 → 刷新映射 → 传给 pipeline input |
| `web/app.py` | **改** | 装配 `HostProfileStore` + include router |
| `blackbox/pipeline/shared.py` | **改** | `BlackboxPipelineInput` / `BlackboxActivityInput` 加 `host_mappings` 字段 |
| `blackbox/pipeline/workflows.py` | **改** | preflight 前插 `run_host_proxy_setup` activity；exploit 后 cleanup |
| `blackbox/pipeline/activities.py` | **新增 activity** | `run_host_proxy_setup`（起代理+探活，失败 fail-fast）+ cleanup；`run_blackbox_preflight` 用映射 IP |
| `core/services/host_proxy.py` | **新增** | proxy.py 子进程管理 + 自定义 `HttpProxyBasePlugin`（按域名查映射）+ 端口分配 + 探活 |
| `core/services/engines/agent_browser_engine.py` | **改** | `session_flag` 接受 per-scan proxy_url，加 `--proxy` |
| `core/services/engines/playwright_engine.py` | **改** | `_build_stealth_config` 写 config 时加 `browser.launchOptions.proxy`（per-scan） |
| `core/agents/tools_openai/exec.py` | **改** | `_bash_impl` 从 ToolContext 取 proxy_url，`create_subprocess_shell` 传 env |
| `core/agents/tools_openai/web.py` | **改** | `_web_fetch_impl` 从 ctx 取 proxy_url，httpx client 带 `proxies` |
| `core/utils/security.py` | **改** | `check_url_reachable` / `validate_target_url` 接受可选映射表，IP 来源从 DNS 改为查映射（命中时） |
| `core/agents/executor.py` + `ToolContext` | **改** | 携带 per-scan proxy_url，下传到工具 |
| 前端 `api/hostProfiles.ts` / `types.ts` | **改/新增** | 镜像 authProfiles |
| 前端 `pages/HostProfilesPage.tsx` + `components/HostProfileDialog.tsx` | **新增** | 镜像 auth profile 页/表单；含「GET 链接导入」+「刷新」按钮 |
| 前端 `components/ScanFormFields.tsx` / `pages/ScanNewPage.tsx` | **改** | 黑盒表单加 HOST 选择（选档案 或 填 GET 链接） |
| 前端 `router.tsx` / `WorkspaceDetail/index.tsx` | **改** | 加 `host-profiles` 路由 + 命令栏按钮 |
| 白盒扫描 / 双轨 LLM prompt / 确定性层 | **不触及** | HOST 档案只在黑盒消费 |
| auth profile / credential vault | **不动**（复用 vault 实例可选） | HOST 不加密，不依赖 vault |

---

## 4. 设计

### 4.1 HOST 档案模型与存储

镜像 `AuthProfileStore`（`web/components/auth_profile_store.py`），per-workspace、YAML 落盘、路径穿越双防线、`.system` 共享段 + fork 同构。

```python
# web/components/host_profile_store.py
class HostMapping(BaseModel):
    ip: str          # "10.0.0.1"
    host: str        # "example.com"

class HostProfile(BaseModel):
    id: str                       # "host_xxx"
    name: str                     # "华南生产环境"
    source_url: str | None        # GET 链接；手填档案为 None
    mappings: list[HostMapping]   # 域名→IP 列表
    scope: Literal["workspace", "system"] = "workspace"
    created_at: str
    updated_at: str

HOST_PROFILES_FILENAME = "host-profiles.yaml"
```

- **不加密**：IP/域名非敏感凭据，落盘明文（区别于 auth profile 的 Fernet）。
- **Store 方法**（对齐 auth profile）：`read(ws)`（合并 ws + `.system`，按 id 去重）、`read_one`、`upsert_profile`、`delete_profile`、`apply_update`、`fork_from_system`。新增 `import_from_url(ws, url, name?)`（GET + 解析 + 落盘 + 存 source_url）、`refresh(ws, pid)`（按 source_url 重新拉取 + 更新 mappings 快照）。
- **结构统一**：手填 / GET 导入 / 表单填链接入库，产出的都是同一个 `HostProfile` 模型，不区分来源类型字段。

### 4.2 GET 链接获取、解析与刷新

GET 接口（如 `https://hosts.futuoa.com/hosts/get-hosts-content?id=6001`）**无需鉴权**，返回标准 `/etc/hosts` 文本。

**解析规则**（`/etc/hosts` 标准）：
- 跳过空行与 `#` 开头注释行。
- 每行 `IP host1 [host2 ...]`：IP + 每个 host 各生成一条 `HostMapping`（别名同指该 IP）。
- 非法行（解析不出 IP）跳过并计入 warnings。
- `/etc/hosts` 不含端口；端口保留原请求 URL 的（代理 CONNECT 时按原端口连映射 IP）。

**刷新语义（关键）**：
- **每次扫描启动时**：若选中档案带 `source_url` → 尝试重新 GET 拉取 → 成功：用最新 mappings（**回写快照**到档案）；**失败：用落盘快照 fallback + 日志记 warning**（`HOST 档案 <name> 刷新失败（<原因>），使用上次快照`），**不阻断扫描**。
- 档案页导入 = `/parse`（拉取解析、不落盘）预览 + upsert（落盘 + 存 `source_url`）两步（见 §4.6 B）。
- 表单「填 GET 链接跑」：扫描启动时 GET 解析得 mappings 用于本次扫描；**扫描结束按 `source_url` 去重入库**（已存在→更新 mappings，不存在→新建 `name=env-{id}`），与导入档案结构一致。

**后端复用**：所有「GET URL → 解析 `/etc/hosts`」场景共用一个核心函数 `fetch_and_parse_hosts(url) -> list[HostMapping]`（纯拉取解析、不落盘）：`/parse` 端点（预览）、`store.refresh(pid)`（按 `source_url` 覆盖快照）、scan_manager 表单 `host_url`（拉取用于本次扫描）各自调用、按需落盘。

### 4.3 per-scan 本地代理（proxy.py 子进程 + 自定义 DNS 插件）

```python
# core/services/host_proxy.py（proxy.py 2.4.x API，实测确认）
from proxy.http.proxy import HttpProxyBasePlugin
from proxy.common.types import HostPort

class HostResolverPlugin(HttpProxyBasePlugin):
    """按域名查映射表返回指定 IP；未命中走 proxy.py 默认 DNS。
    映射表经 env HOST_MAP_JSON 注入（每个 proxy 子进程独立 env → per-scan 隔离）。
    resolve_dns 对 HTTP 请求与 HTTPS CONNECT 隧道均生效（实测）。"""
    def resolve_dns(self, host: str, port: int) -> tuple[str | None, HostPort | None]:
        import json, os
        mapping = json.loads(os.environ.get("HOST_MAP_JSON", "{}"))
        return (mapping.get(host), None)   # 命中→映射 IP；未命中→(None,None) 走默认 DNS

async def start_host_proxy(mappings: dict[str, str]) -> ProxyHandle:
    """起 proxy.py 子进程，bind 127.0.0.1:<OS 分配端口>，加载映射。
    返回 {proxy_url, process}。探活失败 raise PentestError → 扫描 fail-fast。"""
    ...

async def stop_host_proxy(handle: ProxyHandle) -> None: ...
```

- **子进程**跑（不在 worker 进程内）：崩了不拖垮 worker，探活清晰，符合「起不来 fail-fast」语义。
- **启动命令（实测必需 flag）**：
  ```
  HOST_MAP_JSON='{"host1":"ip1",...}' PYTHONPATH=<plugin 目录> proxy \
    --plugins host_resolver.HostResolverPlugin \
    --hostname 127.0.0.1 --port 0 --port-file <path> \
    --num-workers 1 --num-acceptors 1 --local-executor 1 --log-level WARNING
  ```
  **必须 `--num-workers 1 --num-acceptors 1 --local-executor 1`**——否则 proxy.py 按 CPU 核数 fork 多 acceptor/worker 进程，per-scan 爆 N×2 进程。
- **端口回显**：`--port 0`（OS 分配）+ `--port-file <path>`，worker 从 port-file 读实际端口供各出口注入（实测稳定）。
- **映射表传参**：env `HOST_MAP_JSON`（JSON dict），插件 `resolve_dns` 读；每个 proxy 子进程独立 env → per-scan 隔离（实测）。插件 stateless + per-request 实例，**不能用 Python 全局变量**（proxy.py 官方警告，多 worker 进程不共享）。
- **HTTPS**：走 CONNECT 透传，按域名查映射 IP 建连、保 SNI/Host，**无 MITM、不要证书**。HTTP 明文同理透传；实测 `curl -x proxy https://target.test:18443` 落映射 IP。WebSocket/长连接 CONNECT 是 TCP 级天然支持。
- **未映射域名**：走 proxy.py 默认 DNS 正常透传（不影响第三方 CDN 等）。

### 4.4 各出口注入（per-scan，结构性隔离）

代理地址 = `http://127.0.0.1:<port>`。env 只注入扫描出口工具，**worker 主进程的 LLM/temporal 流量不经代理**（结构性隔离）。

| 出口 | 注入方式 | 触点 |
|------|---------|------|
| bash+curl | `create_subprocess_shell(..., env={**os.environ, "HTTPS_PROXY":u,"HTTP_PROXY":u,"NO_PROXY":bypass})` | `tools_openai/exec.py:38`（env 从 ToolContext 取） |
| agent-browser | `session_flag` 拼 `--proxy <url>`（per-session，实测） | `agent_browser_engine.py:117` |
| playwright-cli | 写 config `browser.launchOptions.proxy={"server":url}`（实测） | `playwright_engine.py:70`（`_build_stealth_config`） |
| web_fetch | `httpx.AsyncClient(proxies=url)` | `tools_openai/web.py:37` |
| preflight httpx | **不走代理**，pinned_ip 直连映射 IP | `utils/security.py:102` |

- `NO_PROXY` = `127.0.0.1,localhost,<LLM API host>,<temporal host>`。
- **穿线**：`BlackboxPipelineInput` / `BlackboxActivityInput` / `ToolContext` 携带 `proxy_url`（+ mappings 备用），`executor.py` 下传到工具；浏览器经 `PromptManager` 的 `variables` 注入（见下）。
- **per-scan 穿线不绑 session_id（关键）**：`get_session_id(agent_name)` 按 **agent 名**映射（`AGENT_SESSION_MAPPING`），并发 scan 同一 agent **共享 session_id / chrome profile**。故 proxy_url 必须经 manager `variables`（`session_flag(proxy_url=...)` / `write_config(proxy_url=...)`）**按扫描维度**传入，不能挂在 session_id 上。

### 4.5 preflight 与 SSRF（语义澄清）

- SSRF/loopback 拦截**保持不变**：仍拦 `169.254.0.0/16`（`check_ssrf`）与 loopback/unspecified（`check_loopback`）。内网 `10.x`/`192.168.x` **本就放行**，无需任何绕过。
- 有 HOST 映射时：preflight 可达性探测改用**映射 IP**（替代 `web_url` 的 DNS 解析 IP）——复用现有 `pinned_ip` 机制，仅 IP 来源从 `resolve_host(DNS)` 改成查映射表（命中映射用映射 IP，未命中走原 DNS）。
- 映射里若误填 `127.x`/`169.254.x`，preflight 照拦（保护机制不因映射而退化）。

### 4.6 前端交互设计

镜像 auth profile 前端链路（`pages/AuthProfilesPage.tsx` / `components/AuthProfileDialog.tsx` / `api/authProfiles.ts` / `types.ts` / 路由 / 命令栏按钮），新增 HOST 档案对应物 + 黑盒表单 HOST 选择区。

**A. HOST 档案管理页 `HostProfilesPage`**（新建，镜像 `AuthProfilesPage`）
- 入口：workspace header 命令栏加「HOST 档案」按钮（`Globe` 图标），路由 `/p/:workspace/host-profiles`（`router.tsx:88` 旁加 child，`routes/WorkspaceDetail/index.tsx:110-116` 旁加按钮）。
- 表格列：名称（system 档案带徽章）/ 来源（手填=「手动」；GET 链接=显示 `id` 或截断 url + Tooltip + 复制）/ 映射条数（如「5 条」，点开看明细）/ 更新时间 / 操作（编辑·刷新[仅 source_url 存在]·删除；system 仅 fork）。
- api client `api/hostProfiles.ts` + `types.ts` 加 `HostProfile` / `HostMapping`（镜像 `api/authProfiles.ts`）。

**B. 档案对话框 `HostProfileDialog`**（新建，镜像 `AuthProfileDialog`）
- 字段：`name` + `mappings`（行编辑器，每行 `ip` + `host`，增删行；子组件镜像 `components/auth/CredentialRows.tsx`）+ 可选 `source_url`。
- **手填与导入统一**：两者走同一条保存路径（upsert：`name` + `mappings` + 可选 `source_url`）。「从 GET 链接导入」只是「用 URL 自动填 mappings」的快捷方式：
  - 填 URL → 点「拉取」→ `POST /api/workspaces/{ws}/host-profiles/parse?url=` → 后端 GET + 解析 `/etc/hosts`（**不落盘**）→ 返回 mappings 填进行编辑器，**可微调**，`source_url` 自动记为该 URL。
  - 点保存 → `POST /host-profiles`（upsert 带 `mappings` + `source_url`）。
- 排除项：客户端预览（CORS——浏览器跨域 fetch hosts 服务被拦）；直接导入无预览（看不到脏数据）。
- 语义：`source_url` = 来源链接（供刷新），`mappings` = 当前生效快照（可手改；刷新按 `source_url` 重新拉取覆盖）。

**C. 黑盒扫描表单 HOST 选择**（改 `components/ScanFormFields.tsx:750-880` 黑盒区 + `pages/ScanNewPage.tsx:139-193`）
- 新增「HOST 解析」区，**可选**（不启用 = 不起代理、走原 DNS，**向后兼容**现有扫描）：
  - 模式「选档案」/「填 GET 链接」：
    - 选档案：下拉选当前 ws 的 host profile（镜像认证 profile 选择交互）。
    - 填 GET 链接：URL 输入（扫描启动时后端拉取，跑完按 `source_url` 去重自动入库）。
  - 与认证区并列、互不影响；`buildBody` 加 `host_profile_id` 或 `host_url` 字段（`ScanNewPage.tsx:162-193`），对应后端 `ScanRequest` 新字段（§3）。

**D. i18n**：新增页 / 表单 / 字段的中英 locales（对齐 web-frontend-i18n epic，`frontend/src/locales/`）。

**E. 自动入库反馈**：填链接扫描结束后，档案页 refresh 即可见新档案（`name=env-{id}`），与导入档案结构一致。

---

## 5. 数据流

```
[工作区 HOST 档案库 host-profiles.yaml]  （per-workspace，镜像 auth-profiles）
   │ 手填 mappings / 从 GET 链接导入（落快照+存 source_url）
   ▼
[黑盒扫描表单] ──选 host_profile_id 或 填 host_url──> POST /api/scan
   │ scan_manager._resolve_blackbox_inputs:
   │   选档案→按 source_url 刷新(失败 fallback+日志) / 填链接→GET 解析
   ▼
[BlackboxPipelineInput.host_mappings] ──> workflow
   │ preflight 前: run_host_proxy_setup activity
   ▼
[per-scan 本地代理] proxy.py 子进程 + HostResolverPlugin, 127.0.0.1:<port>
   │ 探活失败 → PentestError → 整个扫描 fail-fast
   ├─────────┬───────────┬────────────┬─────────────┐
   ▼         ▼           ▼            ▼             ▼
 bash+curl  agent-browser playwright  web_fetch   preflight
 (env)      (--proxy)    (launchOpt)  (httpx      (映射 IP
                                     proxies)    可达性探测)
   ── 全部走本扫描代理 → per-scan 端口隔离，多用户并发不冲突 ──
   │ exploit 后: stop_host_proxy（cleanup activity）
```

---

## 6. 错误处理

| 情形 | 处理 |
|------|------|
| 代理子进程起不来 / 探活失败 | **fail-fast**，整个扫描失败（PentestError，category=preflight） |
| 档案 source_url 刷新失败 | 用落盘快照 fallback + 日记 warning，**不阻断**扫描 |
| GET 链接导入/解析无有效映射 | 返回 422（导入时）/ fail-fast（扫描启动时，无映射则不起代理、按原 DNS） |
| 映射含 `127.x`/`169.254.x` | preflight 照拦（SSRF 不退化） |
| 扫描异常退出 | cleanup activity best-effort 停代理（复用现有 `cleanup_processes` 容错模式，绝不因清理失败拖垮 worker） |
| 代理运行中挂掉 | 🔴 待 plan 定：是否在 exploit 各 activity 前探活 / 还是仅在启动探活一次（权衡频率与开销） |

---

## 7. 测试策略（TDD，只跑相关文件）

**2026-08-12 端到端可行性实测（已通过，脚本归档 `scripts/validate_host_proxy_probe/`）**：
- 双 session 同域名 `target.test`：scanA→127.0.0.1、scanB→127.0.0.2，互不串（serverA 计数 scanB 后 3→3 不变）。
- 覆盖 agent-browser（`--proxy`）、playwright-cli（`--config browser.launchOptions.proxy`，per-session 独立 PID）、curl（env）。
- HTTP + HTTPS(CONNECT) 双链路：`resolve_dns` 对 CONNECT 隧道生效（`curl -x proxy https://target.test` 落映射 IP）。
- proxy.py 启动 flag `--num-workers 1 --num-acceptors 1 --local-executor 1 --port 0 --port-file`，映射经 env `HOST_MAP_JSON`。

**TDD 单元/集成**：
- **档案 store**：CRUD + `/etc/hosts` 解析（注释/别名/非法行）+ 刷新成功/失败 fallback + `.system` 合并去重（单元）。
- **代理**：起停、映射命中/未命中、HTTPS CONNECT 透传、未映射域名正常 DNS（本地 mock 目标，单元+集成）。
- **出口注入**：bash env / httpx proxies / agent-browser `--proxy` 各出口确实走代理（集成，断言请求落到映射 IP）。
- **preflight**：映射 IP 用于可达性探测；映射含 loopback 仍被拦（集成）。
- **fail-fast**：代理起不来 → 扫描失败（集成）。
- **并发隔离**：两扫描各持不同映射、同域名不同 IP，互不影响（集成）。
- 前端：`HostProfilesPage` CRUD + 导入 + 刷新；黑盒表单 HOST 选择（vitest，用 `./node_modules/.bin/vitest`）。

对齐 CLAUDE.md 测试陷阱：只跑改动相关文件，勿广跑全套。

---

## 8. 待 plan 确认项

- 代理运行中探活策略（§6）：启动探活已定，运行中是否周期/按 activity 探活待权衡（实测未覆盖长时稳定性）。
- 入库时机（§4.2）默认「扫描结束按 source_url 去重入库（成功/失败都入）」，若需改为「仅成功入库」或「启动即入库」，plan 阶段确认。
- `proxy.py`（实测用 2.4.10）作为新运行时依赖：加入 `packages/worker` 依赖（worker 镜像装），版本锁定与镜像 rebuild 待 plan。
- chrome 经 proxy 取页面 body 偶发 `ConnectionReset`（落点正确、hits 铁证，响应回传偶断）：plan 评估是否调 proxy 配置（threaded 模式 / `--num-acceptors`）缓解；exploit 主路径 curl 不受影响。
- 已消除（实测确认，不再待 plan）：端口回显（`--port-file`）、映射传参（env `HOST_MAP_JSON`）、resolve_dns 对 HTTP/HTTPS CONNECT、agent-browser / playwright-cli 代理支持与 per-session 独立 launch。
