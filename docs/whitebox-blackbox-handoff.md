# 白盒 → 黑盒 交接运行手册

本文档说明如何「先跑白盒源码扫描，再让黑盒基于白盒结果做运行时验证」。
所有命令与结论均来自对当前代码（`feat/fork-py`）的实际核对，并标注了已知缺口。

---

## 1. 交接原理（一句话版）

> 黑盒通过 **同一个 `--repo` 路径** 复用白盒结果。白盒和黑盒都把 deliverables
> 解析到 **`<repo>/.supernova/deliverables/`**；`--repo` 一致，黑盒就能读到白盒产出的
> `*_exploitation_queue.json`，从而跳过自己的侦察（recon）阶段，直接对队列里的漏洞做利用验证。

数据流：

```
白盒  supernova-whitebox start --repo <REPO> [--workspace <NAME>]
        └─ 产出  <REPO>/.supernova/deliverables/<vc>_exploitation_queue.json
                 （vc = injection / xss / auth / authz / ssrf）

黑盒  supernova-blackbox start --url <URL> --repo <REPO>     ← 关键：--repo 指向同一仓库
        └─ 读取  <REPO>/.supernova/deliverables/<vc>_exploitation_queue.json
           → 检测到白盒结果 → 跳过 RECON_BLACKBOX → 直接 exploit
```

代码依据：
- 白盒写入：`packages/whitebox/.../pipeline/activities.py:22` `_get_paths()` → `resolve_deliverables_path(repo_path=...)`
- 黑盒读取：`packages/blackbox/.../pipeline/workflows.py:116-129`，同样调用 `resolve_deliverables_path(repo_path=...)`
- `resolve_deliverables_path` 优先级 1 = `repo_path / deliverables_subdir`，`deliverables_subdir` 默认 `.supernova/deliverables`（`packages/core/.../constants.py:1`）

---

## 2. 前置准备（一次性）

```bash
# 2.1 进入项目、安装依赖（uv workspace）
cd /path/to/supernova
uv sync

# 2.2 配置 AI Provider
cp .env.example .env
#    编辑 .env，至少设置一项 provider，例如：
#      SUPERNOVA_AI_PROVIDER=anthropic_api
#      SUPERNOVA_API_KEY=sk-ant-...
#    其他 provider：bedrock / vertex / openai_compatible / litellm_router（见 .env.example）

# 2.3 启动 Temporal（白盒、黑盒都依赖它做工作流编排，需常驻）
#    三选一：
temporal server start-dev            # A. 本机 temporal CLI（最轻）
docker compose up -d                 # B. 项目自带 docker-compose.yml（含 Web UI :8233）
supernova-whitebox infra up            # C. 项目封装的启动器（也是 docker）
supernova-whitebox infra status        # 确认 healthy
```

环境要求：Python 3.12+、[uv](https://docs.astral.sh/uv/)、Temporal Server（默认 `localhost:7233`）。

---

## 3. 完整运行序列

> 命名约定：下方 `<REPO>` 用**绝对路径**，指向待扫描的目标仓库。

### 3.1 白盒扫描

```bash
uv run supernova-whitebox start \
  --repo <REPO> \
  --workspace wb-myapp \          # 建议带 --workspace，便于 workspace 管理与断点续扫
  --url https://myapp.example.com # 可选：记录部署 URL，黑盒可据此按 URL 自动检测并复用本扫描
```

完成后产出（位于仓库内部，不在 workspaces 目录）：

```
<REPO>/.supernova/deliverables/injection_exploitation_queue.json
<REPO>/.supernova/deliverables/xss_exploitation_queue.json
<REPO>/.supernova/deliverables/auth_exploitation_queue.json
<REPO>/.supernova/deliverables/authz_exploitation_queue.json
<REPO>/.supernova/deliverables/ssrf_exploitation_queue.json
```

### 3.2 黑盒扫描（复用白盒结果）

```bash
uv run supernova-blackbox start \
  --url https://myapp.example.com \
  --repo <REPO> \                  # ← 关键！必须与白盒的 --repo 是同一个仓库
  -w bb-myapp                       # 黑盒自己的 workspace 名（可选但建议）
```

成功时控制台会提示：

```
Scan completed (leveraged whitebox results for: injection, xss, ...)
```

黑盒日志（`<CWD>/workspaces/bb-myapp/workflow.log` 或 `supernova-blackbox logs bb-myapp`）中会出现：

```
Whitebox results detected at <REPO>/.supernova/deliverables for classes: [...] — skipping RECON_BLACKBOX
```

看到这行，就证明黑盒确实复用了白盒结果。如果看到 `No whitebox results found ... running RECON_BLACKBOX from scratch`，说明 `--repo` 没对上（见第 5 节排查）。

### 3.3（可选）不跑利用，只做侦察/验证

```bash
uv run supernova-blackbox start --url https://myapp.example.com --repo <REPO> -w bb-myapp --no-exploit
```

---

## 4. 辅助命令

```bash
# 列出所有 workspace（白盒 / 黑盒分组，显示 target、状态、vuln queues）
uv run supernova-whitebox workspaces
uv run supernova-blackbox workspaces

# 查看某个 workspace 详情（deliverables 计数、关联的子/父扫描）
uv run supernova-blackbox workspace show wb-myapp

# 查看执行日志（--follow 实时跟踪，完成自动退出）
uv run supernova-whitebox logs wb-myapp --follow
uv run supernova-blackbox logs bb-myapp --follow
```

---

## 5. ✅ 历史陷阱与修复状态

下列问题曾在 `feat/fork-py` 早期存在，现已修复。**统一根因**：消费侧（summary/discovery/
展示/clean）的 deliverables 定位此前是 workspace-centric（`workspaces/<name>/deliverables`），
与写入侧的 repo-centric（`<REPO>/.supernova/deliverables`）脱节；现已通过
`deliverables_dir_for_workspace`（从 workspace 的 session.json 恢复 repo_path）统一对齐。

### 陷阱 1 — CLI/README「下一步」命令漏 `--repo`  → ✅ 已修复
白盒完成时 CLI 打印的「Next steps」与 README 黑盒示例均已显式带 `--repo <REPO>`，
黑盒据此复用白盒结果。`-w <白盒名>` 也安全（黑盒不创建/覆盖 session，仅用作定位）。

### 陷阱 2 — `--latest` 失效  → ✅ 已修复
`compute_deliverables_summary` 现经 session 解析到真实 `<REPO>/.supernova/deliverables`。
`supernova-blackbox start --url <URL> --repo <REPO> --latest` 可正常跳过侦察。

### 陷阱 3 — 裸 URL 自动检测失效  → ✅ 已修复
白盒新增可选 `--url`（`supernova-whitebox start --repo <REPO> --url <URL>`），写入 session.web_url；
黑盒 `find_workspaces_by_url` 据此按 URL 自动匹配。
> 要在白盒时带 `--url`，自动检测才有信息可用；否则用 `--repo`/`-w`。
> `--url` 仅用于记录关联，**不**触发目标 URL 可达性校验（白盒源码扫描不联网）。

### 陷阱 4 — 白盒打印的 deliverables 路径与实际不符  → ✅ 已修复
白盒完成时返回的 `deliverables_path` 统一为 `<REPO>/.supernova/deliverables`，与实际写入一致；
完成摘要、`workspaces`、`workspace show` 均显示正确路径与计数。

### 连带修复 — `workspace clean`
此前 `clean_workspace` 在 `workspaces/<name>/deliverables` 清理（找不到 repo-centric 产物），
现已解析到真实 deliverables 目录。注意：deliverables 在 repo 内；`agents`/`prompts`/`scratchpad`
仍在 workspace 内（保持原行为）。

### 设计说明 — repo-centric 共享语义
deliverables 固定写在 `<REPO>/.supernova/deliverables`（`SUPERNOVA_DELIVERABLES_SUBDIR` 默认
`.supernova/deliverables`）。同一 repo 的多次白盒扫描会**共享/覆盖**同一份 deliverables
（符合「一 repo 一最新结果」）。如需每次独立保留，可设置不同的 `SUPERNOVA_DELIVERABLES_SUBDIR`。

---

## 6. 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 黑盒日志出现 `No whitebox results found ... running RECON_BLACKBOX from scratch` | 黑盒 `--repo` 未传，或与白盒 `--repo` 不是同一仓库；或白盒 deliverables 为空 | 确认两条命令的 `--repo` 是同一个绝对路径；确认白盒已完成且有 queue 文件 |
| 裸 URL 自动检测未命中（standalone） | 白盒扫描时未带 `--url`，session.web_url 为空 | 白盒带 `--url <URL>` 重扫，或直接用 `--repo`/`-w` |
| `workspace clean` 清不掉 deliverables | （历史问题，已修复）若仍出现，确认 session.repo_path 正确指向目标仓库 | 检查 `workspace show` 的 Repo 字段 |
| Temporal 连接失败 | Temporal 未启动 / 地址不对 | `supernova-whitebox infra status`；必要时 `temporal server start-dev` 或 `docker compose up -d` |
| 白盒扫描失败：provider/auth | `.env` 未配置 API Key | 检查 `.env` 中 `SUPERNOVA_AI_PROVIDER` / `SUPERNOVA_API_KEY` |

---

## 7. 环境变量速查

| 变量 | 作用 | 默认 |
|------|------|------|
| `SUPERNOVA_AI_PROVIDER` | AI provider 类型 | `anthropic_api` |
| `SUPERNOVA_API_KEY` / `ANTHROPIC_API_KEY` | API Key | — |
| `SUPERNOVA_BASE_URL` | 自定义 API 端点（openai_compatible / litellm） | — |
| `SUPERNOVA_MAX_BUDGET` | 单次调用花费上限（美元） | — |
| `TEMPORAL_ADDRESS` | Temporal 地址 | `localhost:7233` |
| `SUPERNOVA_DELIVERABLES_SUBDIR` | deliverables 子目录（白盒/黑盒共用，须保持一致） | `.supernova/deliverables` |
| `SUPERNOVA_BROWSER_ENGINE` | 黑盒浏览器引擎 | `playwright` |
