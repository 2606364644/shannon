# 白盒 → 黑盒 交接运行手册

本文档说明如何「先跑白盒源码扫描，再让黑盒基于白盒结果做运行时验证」。
所有命令与结论均来自对当前代码（`feat/fork-py`）的实际核对，并标注了已知缺口。

---

## 1. 交接原理（一句话版）

> 黑盒通过 **同一个 `--repo` 路径** 复用白盒结果。白盒和黑盒都把 deliverables
> 解析到 **`<repo>/.shannon/deliverables/`**；`--repo` 一致，黑盒就能读到白盒产出的
> `*_exploitation_queue.json`，从而跳过自己的侦察（recon）阶段，直接对队列里的漏洞做利用验证。

数据流：

```
白盒  shannon-whitebox start --repo <REPO> [--workspace <NAME>]
        └─ 产出  <REPO>/.shannon/deliverables/<vc>_exploitation_queue.json
                 （vc = injection / xss / auth / authz / ssrf）

黑盒  shannon-blackbox start --url <URL> --repo <REPO>     ← 关键：--repo 指向同一仓库
        └─ 读取  <REPO>/.shannon/deliverables/<vc>_exploitation_queue.json
           → 检测到白盒结果 → 跳过 RECON_BLACKBOX → 直接 exploit
```

代码依据：
- 白盒写入：`packages/whitebox/.../pipeline/activities.py:22` `_get_paths()` → `resolve_deliverables_path(repo_path=...)`
- 黑盒读取：`packages/blackbox/.../pipeline/workflows.py:116-129`，同样调用 `resolve_deliverables_path(repo_path=...)`
- `resolve_deliverables_path` 优先级 1 = `repo_path / deliverables_subdir`，`deliverables_subdir` 默认 `.shannon/deliverables`（`packages/core/.../constants.py:1`）

---

## 2. 前置准备（一次性）

```bash
# 2.1 进入项目、安装依赖（uv workspace）
cd /path/to/shannon-py
uv sync

# 2.2 配置 AI Provider
cp .env.example .env
#    编辑 .env，至少设置一项 provider，例如：
#      SHANNON_AI_PROVIDER=anthropic_api
#      SHANNON_API_KEY=sk-ant-...
#    其他 provider：bedrock / vertex / openai_compatible / litellm_router（见 .env.example）

# 2.3 启动 Temporal（白盒、黑盒都依赖它做工作流编排，需常驻）
#    三选一：
temporal server start-dev            # A. 本机 temporal CLI（最轻）
docker compose up -d                 # B. 项目自带 docker-compose.yml（含 Web UI :8233）
shannon-whitebox infra up            # C. 项目封装的启动器（也是 docker）
shannon-whitebox infra status        # 确认 healthy
```

环境要求：Python 3.12+、[uv](https://docs.astral.sh/uv/)、Temporal Server（默认 `localhost:7233`）。

---

## 3. 完整运行序列

> 命名约定：下方 `<REPO>` 用**绝对路径**，指向待扫描的目标仓库。

### 3.1 白盒扫描

```bash
uv run shannon-whitebox start \
  --repo <REPO> \
  --workspace wb-myapp          # 建议带 --workspace，便于 workspace 管理与断点续扫
```

完成后产出（位于仓库内部，不在 workspaces 目录）：

```
<REPO>/.shannon/deliverables/injection_exploitation_queue.json
<REPO>/.shannon/deliverables/xss_exploitation_queue.json
<REPO>/.shannon/deliverables/auth_exploitation_queue.json
<REPO>/.shannon/deliverables/authz_exploitation_queue.json
<REPO>/.shannon/deliverables/ssrf_exploitation_queue.json
```

### 3.2 黑盒扫描（复用白盒结果）

```bash
uv run shannon-blackbox start \
  --url https://myapp.example.com \
  --repo <REPO> \                  # ← 关键！必须与白盒的 --repo 是同一个仓库
  -w bb-myapp                       # 黑盒自己的 workspace 名（可选但建议）
```

成功时控制台会提示：

```
Scan completed (leveraged whitebox results for: injection, xss, ...)
```

黑盒日志（`<CWD>/workspaces/bb-myapp/workflow.log` 或 `shannon-blackbox logs bb-myapp`）中会出现：

```
Whitebox results detected at <REPO>/.shannon/deliverables for classes: [...] — skipping RECON_BLACKBOX
```

看到这行，就证明黑盒确实复用了白盒结果。如果看到 `No whitebox results found ... running RECON_BLACKBOX from scratch`，说明 `--repo` 没对上（见第 5 节排查）。

### 3.3（可选）不跑利用，只做侦察/验证

```bash
uv run shannon-blackbox start --url https://myapp.example.com --repo <REPO> -w bb-myapp --no-exploit
```

---

## 4. 辅助命令

```bash
# 列出所有 workspace（白盒 / 黑盒分组，显示 target、状态、vuln queues）
uv run shannon-whitebox workspaces
uv run shannon-blackbox workspaces

# 查看某个 workspace 详情（deliverables 计数、关联的子/父扫描）
uv run shannon-blackbox workspace show wb-myapp

# 查看执行日志（--follow 实时跟踪，完成自动退出）
uv run shannon-whitebox logs wb-myapp --follow
uv run shannon-blackbox logs bb-myapp --follow
```

---

## 5. ⚠️ 已知缺口与陷阱（务必阅读）

以下都是当前 `feat/fork-py` 分支真实存在的问题，照 README 或 CLI 提示直接跑会踩坑：

### 缺口 1 — README / CLI 提示的「下一步」命令漏了 `--repo`
白盒完成后 CLI 会打印（`packages/whitebox/.../cli/main.py:81`）：
```
shannon-blackbox start --url <URL> -w <NAME>
```
README 的黑盒示例也只给 `shannon-blackbox start --url <URL>`。**这些命令都没带 `--repo`**，照抄后 `input.repo_path=None`，黑盒 workflow 会落到 `workspaces/<name>/.shannon/deliverables`（白盒没写在那里）→ 检测不到白盒结果 → **回退成 standalone 黑盒，不复用**。
→ **修正：黑盒命令必须显式加 `--repo <REPO>`。**

### 缺口 2 — `--latest` 当前失效
`shannon-blackbox start --url <URL> --latest` 走 `find_latest_workspace`，它用 `compute_deliverables_summary(workspace)` 判断是否有 deliverables，而该函数找的是 **`workspaces/<name>/deliverables/`**（`packages/core/.../workspace.py:74`）。但白盒实际写在 **`<REPO>/.shannon/deliverables/`**，目录不一致 → 判定「无 deliverables」→ 报 `Latest workspace has no deliverables`。
→ **当前请用 `--repo` + `-w`，不要依赖 `--latest`。**

### 缺口 3 — 裸 URL 自动检测失效
`shannon-blackbox start --url <URL>`（不带 `-w`/`--latest`）的自动检测走 `find_workspaces_by_url`，它同时受两个问题影响：①白盒 CLI 不传 `web_url`（`cli/main.py:38`），落库为空串 → URL 匹配失败；②同样受缺口 2 的目录不一致影响。→ 自动检测必然返回空 → standalone。

### 缺口 4 — 白盒 CLI 打印的 deliverables 路径与实际不符
白盒完成时打印的 `Deliverables:` 路径来自 `worker.py:102`（`workspaces/<name>/.shannon/deliverables`），与**实际写入位置** `<REPO>/.shannon/deliverables` 不一致。这只是显示误导，不影响黑盒（黑盒靠 `--repo` 解析）。→ 找白盒产出物请认准 `<REPO>/.shannon/deliverables/`。

---

## 6. 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 黑盒日志出现 `No whitebox results found ... running RECON_BLACKBOX from scratch` | 黑盒 `--repo` 未传，或与白盒 `--repo` 不是同一仓库 | 确认两条命令的 `--repo` 是同一个绝对路径 |
| `--latest` 报 `Latest workspace has no deliverables` | 缺口 2，目录不一致 | 改用 `--repo <REPO> -w <NAME>` |
| 黑盒 standalone（未复用）且无报错 | 用了 README 示例（缺 `--repo`），命中缺口 1 | 加 `--repo <REPO>` |
| Temporal 连接失败 | Temporal 未启动 / 地址不对 | `shannon-whitebox infra status`；必要时 `temporal server start-dev` 或 `docker compose up -d` |
| 白盒扫描失败：provider/auth | `.env` 未配置 API Key | 检查 `.env` 中 `SHANNON_AI_PROVIDER` / `SHANNON_API_KEY` |

---

## 7. 环境变量速查

| 变量 | 作用 | 默认 |
|------|------|------|
| `SHANNON_AI_PROVIDER` | AI provider 类型 | `anthropic_api` |
| `SHANNON_API_KEY` / `ANTHROPIC_API_KEY` | API Key | — |
| `SHANNON_BASE_URL` | 自定义 API 端点（openai_compatible / litellm） | — |
| `SHANNON_MAX_BUDGET` | 单次调用花费上限（美元） | — |
| `TEMPORAL_ADDRESS` | Temporal 地址 | `localhost:7233` |
| `SHANNON_DELIVERABLES_SUBDIR` | deliverables 子目录（白盒/黑盒共用，须保持一致） | `.shannon/deliverables` |
| `SHANNON_BROWSER_ENGINE` | 黑盒浏览器引擎 | `playwright` |
