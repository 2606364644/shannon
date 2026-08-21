# 白盒 → 黑盒 交接运行手册

本文档说明如何「先跑白盒源码扫描，再让黑盒基于白盒结果做运行时验证」。
所有命令与结论均来自对当前代码（`feat/fork-py`）的实际核对。
产物清单明细见 `docs/scan-intermediate-artifacts.md`。

---

## 1. 交接原理

> deliverables 是 **session-centric**（`workspaces/<session>/deliverables/`）**三桶布局**：
> `whitebox/`、`blackbox/`、`combined/`。黑盒复用白盒结果 = 读同一 deliverables 根下
> `whitebox/` 桶的 `{vc}_exploitation_queue.json` 与 `recon_deliverable.md`，自己的产物
> 写 `blackbox/` 桶——两轨互不覆盖。

黑盒是 **exploitation-only 下游**，不独立发现漏洞：

- 检测到有效白盒 queue → 跳过侦察，直接对队列里的漏洞做利用验证；
- 没有白盒产物 → **fail-fast**（`DELIVERABLE_NOT_FOUND`），不会从零跑侦察；
- `recon_deliverable.md` 缺失（即使 queue 非空）→ **nonRetryable fail**（exploit agent
  需要它拿到 API inventory / input vectors / 技术栈，缺了会"失明"）。

数据流：

```
白盒  supernova-whitebox start --repo <REPO> -w wb-myapp [--url <URL>]
        └─ 产出  workspaces/wb-myapp/deliverables/whitebox/
                 ├── {vc}_exploitation_queue.json   （vc = injection / xss / auth / authz / ssrf）
                 └── recon_deliverable.md

黑盒  supernova-blackbox start --url <URL> -w wb-myapp
        └─ 读取  workspaces/wb-myapp/deliverables/whitebox/{vc}_exploitation_queue.json
           + recon_deliverable.md
           → 写出  workspaces/wb-myapp/deliverables/blackbox/（evidence / verdicts / 报告）
```

代码依据：

- 白盒写入：`packages/whitebox/.../pipeline/activities.py` `_get_paths()` → `deliverables/whitebox/`
- 黑盒读取：`packages/blackbox/.../pipeline/workflows.py`（`detect_whitebox_results` activity
  读 `whitebox/` 桶 queue + recon；无结果 fail-fast）
- 目录 SSOT：`packages/core/.../utils/paths.py`（`whitebox_dir` / `blackbox_dir` /
  `resolve_track_deliverable` 三级 fallback）

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
supernova-whitebox infra up          # C. 项目封装的启动器（也是 docker）
supernova-whitebox infra status      # 确认 healthy
```

环境要求：Python 3.12+、[uv](https://docs.astral.sh/uv/)、Temporal Server（默认 `localhost:7233`）。

---

## 3. 完整运行序列

> 命名约定：下方 `<REPO>` 用**绝对路径**，指向待扫描的目标仓库。

### 3.1 白盒扫描

```bash
uv run supernova-whitebox start \
  --repo <REPO> \
  -w wb-myapp \                       # 建议：命名 workspace，黑盒据此复用 + 支持断点续扫
  --url https://myapp.example.com     # 可选：记录部署 URL，黑盒软默认发现时按 URL 优先匹配
```

完成后产出（位于 `workspaces/wb-myapp/deliverables/whitebox/`）：

```
injection_exploitation_queue.json     # 及 xss / auth / authz / ssrf 同模式
recon_deliverable.md                  # 黑盒强制依赖（攻击面情报）
entry_points.json                     # 端点 live 验证（endpoint_verify）输入
...                                   # 其余产物见 docs/scan-intermediate-artifacts.md
```

断点续扫：`-w` 重跑同名 workspace 自动续扫；`--fresh` 全新扫描；`--rewind pre-recon|recon|vuln`
回退到指定阶段重跑（旧产物归档到 `deliverables/whitebox/.whitebox-archive/<run_ts>/`）。

### 3.2 黑盒扫描（复用白盒结果）

```bash
uv run supernova-blackbox start \
  --url https://myapp.example.com \
  -w wb-myapp                         # 与白盒同名 → 复用其 deliverables/whitebox/
```

workspace 发现优先级（`-w` > 软默认自动发现 > 显式 `--latest`）：

| 方式 | 行为 |
|---|---|
| `-w wb-myapp` | 显式指定，直接复用该 workspace 的白盒产物 |
| 无任何 flag（软默认） | 自动找最近的白盒 workspace（`--url` 匹配的优先排序）；找到 → 接上并打印 `Found white-box results in workspace '...'`；找不到 → 黑盒自建 session，preflight 阶段 fail-fast |
| `--latest` | 同软默认，但找不到白盒 workspace / 无 deliverables 时**报错退出**（明确要复用却没结果） |

成功时控制台会提示，黑盒日志（`supernova-blackbox logs wb-myapp`）中会出现：

```
Whitebox results detected at <wb_queue_root> for classes: [...] — skipping RECON_BLACKBOX
```

看到这行，就证明黑盒确实复用了白盒结果。如果扫描直接失败并提示
`Blackbox scan requires existing whitebox scan deliverables ...`，说明没找到有效白盒产物
（见第 5 节排查）。

幂等与重跑：默认（非 `--rerun`）若该 workspace 已跑过黑盒（evidence 文件存在）→ 告知、不启动。
重跑加 `--rerun`，旧 evidence / findings / report 归档到 `deliverables/blackbox/.blackbox-archive/<run_ts>/`。

### 3.3 一键组合扫描（推荐）

```bash
uv run supernova-combined scan \
  --repo <REPO> \
  --url https://myapp.example.com
```

白盒 → 黑盒顺序编排，无需手动衔接。WEB 端也可提交组合扫描：黑盒以
`blackbox-runs/run-K/` per-run 子目录落在白盒任务根下，融合报告
`combined/run-K/combined_report.md`。

### 3.4（可选）不跑利用，只做验证

```bash
uv run supernova-blackbox start --url <URL> -w wb-myapp --no-exploit
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
# 诊断日志（logging WARNING/ERROR 流，落 <workspace>/logs/diagnostic.log）
uv run supernova-blackbox logs bb-myapp --diagnostic
```

---

## 5. 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 扫描失败：`Blackbox scan requires existing whitebox scan deliverables ...` | 未找到有效白盒 queue（黑盒 exploitation-only，无白盒产物直接 fail-fast，不会从零侦察） | 先跑白盒并完成；确认 `-w` 指向白盒 workspace，或去掉 `-w` 让软默认自动发现 |
| 扫描失败：`Blackbox scan requires whitebox recon_deliverable.md ...` | 白盒 recon 产物缺失（queue 非空也不放行） | 重跑白盒（recon 阶段产出 `recon_deliverable.md`）后再复用 |
| `--latest` 报 `No white-box workspaces found` / `Latest workspace has no deliverables` | 无任何白盒 workspace / 最近白盒 workspace 无产物 | 先跑白盒；或用 `-w` 显式指定 |
| 软默认没接上白盒（自建 session 后 fail-fast） | 最近白盒 workspace 与目标 URL 不匹配或无 deliverables | 白盒扫描带 `--url`，或黑盒显式 `-w <白盒名>` |
| `该 workspace 已跑过黑盒 ...`（未启动） | 幂等检测命中已有黑盒结果 | 需要重跑加 `--rerun`（旧产物归档到 `.blackbox-archive/`） |
| Temporal 连接失败 | Temporal 未启动 / 地址不对 | `supernova-whitebox infra status`；必要时 `temporal server start-dev` 或 `docker compose up -d` |
| 白盒扫描失败：provider/auth | `.env` 未配置 API Key | 检查 `.env` 中 `SUPERNOVA_AI_PROVIDER` / `SUPERNOVA_API_KEY` |

---

## 6. 环境变量速查

| 变量 | 作用 | 默认 |
|------|------|------|
| `SUPERNOVA_AI_PROVIDER` | AI provider 类型 | `anthropic_api` |
| `SUPERNOVA_API_KEY` / `ANTHROPIC_API_KEY` | API Key | — |
| `SUPERNOVA_BASE_URL` | 自定义 API 端点（openai_compatible / litellm） | — |
| `SUPERNOVA_MAX_BUDGET` | 单次调用花费上限（美元） | — |
| `TEMPORAL_ADDRESS` | Temporal 地址 | `localhost:7233` |
| `SUPERNOVA_DELIVERABLES_SUBDIR` | deliverables 子目录（session 下，白盒/黑盒共用） | `deliverables` |
| `SUPERNOVA_WORKER_ROOT` | workspaces 根前缀（默认 `<项目根>/workspaces`） | — |
| `SUPERNOVA_BROWSER_ENGINE` | 黑盒浏览器引擎 | `playwright` |
| `SUPERNOVA_VULN_CLASSES` | 白盒 vuln 类选择（CLI `--vuln-classes` 优先） | 全部 |
| `SUPERNOVA_MAX_CONCURRENT` | 黑盒并发 exploit agent 上限 | `3` |
