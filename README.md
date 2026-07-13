# shannon-py

AI 驱动的自动化渗透测试框架，融合白盒源码分析与黑盒运行时验证。

## 功能特性

- 白盒源码漏洞扫描与黑盒运行时漏洞验证，黑盒可复用白盒结果做定向验证
- 14 个专用 Agent 协同工作，覆盖侦察、漏洞分析、漏洞利用和报告生成全流程
- **双引擎**：同一套业务流程可经 [claude-agent-sdk](https://docs.claude.com/en/api/agent-sdk)（底层 Claude Code CLI）或 [openai-agents](https://github.com/openai/openai-agents)（OpenAI 兼容接口）运行，用环境变量切换
- **双轨检测**（白盒注入/XSS/SSRF）：GitNexus 确定性轨 + LLM 轨独立并行、结果 OR 合并，互为兜底
- 基于 Temporal.io 的工作流编排，支持**断点续扫**（resume）与并发控制
- 可定制的 prompt 模板系统，适配不同安全测试场景
- 支持 Injection、XSS、Auth、SSRF、Authz 五大漏洞类别
- YAML 配置文件驱动，支持范围限定、认证配置和报告过滤

## 系统要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) 包管理器
- Temporal Server（默认地址 `localhost:7233`）

## 安装

```bash
git clone <repo-url> && cd shannon-py
uv sync
```

启动 Temporal Server：

```bash
temporal server start-dev
```

## 配置

shannon-py 采用**两层环境配置**：共享配置放根目录 `.env`，引擎/账号配置放 `.env.profiles/<profile>.env`。

### 1. 根 `.env`（共享配置）

```bash
cp .env.example .env
```

编辑 `.env`，核心是选定当前 profile（对应 `.env.profiles/<name>.env`）：

```bash
SHANNON_PROFILE=glm-anthropic        # 改这一行切换引擎/账号
SHANNON_MAX_CONCURRENT=2             # 并发上限：白盒 vuln + 黑盒 exploit agents
SHANNON_BROWSER_ENGINE=playwright    # playwright(默认) | agent-browser
# git URL 扫描模式凭据(表单来源选「git URL」时需要;选「本地路径」无需);仅 https + user:token,不支持 ssh
GITLAB_USER=
GITLAB_TOKEN=
# TEMPORAL_ADDRESS=localhost:7233
```

### 2. profile 文件（引擎与账号）

从模板复制并填入 API Key/Token：

```bash
cp .env.profiles.example/glm-anthropic.env.example .env.profiles/glm-anthropic.env
```

profile 文件决定走哪个引擎和用哪个模型。**`SHANNON_AI_PROVIDER` 是双引擎的切换开关**：

| `SHANNON_AI_PROVIDER` | 引擎 | 需配置 |
|---|---|---|
| `anthropic_api` | claude-agent-sdk（Claude Code CLI） | `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` |
| `openai_compatible` | openai-agents（OpenAI 兼容接口） | `SHANNON_OPENAI_BASE_URL` + `SHANNON_OPENAI_API_KEY` |

> 注：`anthropic_api` 是 Claude Code CLI 的"第一方 API 部署模式"（与 `bedrock`/`vertex` 同组，三者都起 CLI 子进程，区别仅在 CLI 连哪个后端）。凭据与 `ANTHROPIC_BASE_URL` 经 SDK env 透传给 CLI 子进程，**不是 shannon-py 代码直连**；`ANTHROPIC_BASE_URL` 可指向任意 anthropic 兼容端点（如智谱 GLM，非官方）。

每个 profile 还需指定三档模型（large / medium / small），例如：

```bash
SHANNON_AI_PROVIDER=anthropic_api
ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic
ANTHROPIC_AUTH_TOKEN=your-token
SHANNON_LARGE_MODEL=GLM-5.2[1m]
SHANNON_MEDIUM_MODEL=GLM-5.2[1m]
SHANNON_SMALL_MODEL=GLM-4.5-Air
```

> 仓库内置 `deepseek` / `glm-anthropic` / `glm-openai` 三个 profile 模板（见 `.env.profiles.example/`）。可仿照格式新建自己的 profile。

### 3. 其他常用环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `SHANNON_LLM_TRACK_ENABLED` | `1`（开） | 关 LLM 轨开关。`0` = 只关 inj/xss/ssrf 的 vuln agent（taint，GitNexus chain_verdict 兜底）；pre-recon / recon / authz / auth 的 LLM 仍跑（GitNexus 做不了 authz Vertical/Context + auth，关了会降安全效果） |
| `TEMPORAL_ADDRESS` | `localhost:7233` | Temporal Server 地址 |

配置文件加载与 profile 自洽校验细节见 [配置指南](docs/configuration.md)。

## 使用方法

所有 CLI 命令通过 `uv run` 执行，或先激活虚拟环境：

```bash
# 方式一：uv run（推荐）
uv run shannon-whitebox start --repo /path/to/target-repo

# 方式二：激活 venv
source .venv/bin/activate
shannon-whitebox start --repo /path/to/target-repo
```

### 白盒扫描

```bash
# 基础扫描
uv run shannon-whitebox start --repo /path/to/target-repo

# 指定输出目录、工作区与配置文件
uv run shannon-whitebox start --repo /path/to/repo --output ./results --workspace my-scan --config scan.yaml

# --url 记录部署地址，黑盒可据此按 URL 自动复用白盒结果
uv run shannon-whitebox start --repo /path/to/repo --workspace my-scan --url https://target.example.com

# 断点续扫：同名 workspace 存在时自动从断点恢复
uv run shannon-whitebox start --repo /path/to/repo --workspace my-scan

# --fresh 忽略已有进度全新扫描；--rewind <checkpoint> 回退到指定检查点重跑
uv run shannon-whitebox start --repo /path/to/repo --workspace my-scan --fresh

# CI/管道环境关闭 Rich 实时面板，每事件打印一行
uv run shannon-whitebox start --repo /path/to/repo --plain
```

查看工作区和日志：

```bash
uv run shannon-whitebox workspaces
uv run shannon-whitebox logs my-scan          # --follow 实时跟踪
```

### 黑盒扫描

```bash
# 独立模式（无白盒结果）
uv run shannon-blackbox start --url https://target.example.com

# 复用白盒结果（推荐）—— 跳过侦察，直接对白盒发现的漏洞做运行时验证
uv run shannon-blackbox start --url https://target.example.com --repo /path/to/target-repo

# --latest 直接复用最近一次白盒 workspace 的结果
uv run shannon-blackbox start --url https://target.example.com --latest

# 指定漏洞类别、跳过利用阶段
uv run shannon-blackbox start --url https://target.example.com --repo /path/to/repo \
    --vuln-classes injection --vuln-classes xss --no-exploit

# --rerun 归档旧 evidence，基于已有白盒结果强制重跑黑盒
uv run shannon-blackbox start --url https://target.example.com --repo /path/to/repo --rerun

uv run shannon-blackbox start --url https://target.example.com --repo /path/to/repo --config scan.yaml --output ./results --workspace my-scan
```

> **注意**：`--repo` 必须与白盒扫描的 `--repo` 指向同一仓库，黑盒才能读取白盒产出的漏洞队列。详见 [白盒→黑盒交接运行手册](docs/whitebox-blackbox-handoff.md)。

查看工作区和日志：

```bash
uv run shannon-blackbox workspaces
uv run shannon-blackbox logs my-scan          # --follow 实时跟踪
```

## Web 平台（可选）

除 CLI 外，shannon-py 提供一个 Web 平台（`packages/web`）用于扫描调度与结果查看——前端 SPA（Vite + React）+ 后端 API（FastAPI），**单容器部署**：后端在 `:7878` 同时 serve 前端静态产物与 API，同源无 CORS。

### 一键部署（Docker）

```bash
docker compose up --build
# 浏览器访问 http://localhost:7878（前端 + API 同源）
```

compose 起两个服务：`temporal`（workflow 引擎，:7233 gRPC / :8233 Web UI）与 `web`（前端 + API，:7878）。

> **本机 7233 已被占用？** 若已有独立 temporal 容器在跑（常见于多项目共用一台机器），上面的命令会报
> `Bind for 127.0.0.1:7233 failed: port is already allocated`。改用下方 [复用本机已有的 temporal](#复用本机已有的-temporal) 即可。

### 复用本机已有的 temporal

`scripts/up.sh` 自动检测 `7233` 是否被占：被占则复用外部 temporal（不抢端口），空闲则自建——等价 `up -d --build web`。

```bash
./scripts/up.sh        # 自动判断复用 / 自建
./scripts/up.sh down   # 停掉 web（不影响外部 temporal）
```

若不走脚本、手动复用，等价命令：

```bash
docker compose -f docker-compose.yml \
               -f docker-compose.override.external-temporal.yml \
               up -d --build web
```

机制：override 把 compose 自带的 `temporal` 用 `profiles: ["disabled"]` 隐藏，`web` 改直连外部 temporal（经 `shannon-net` 网络用容器名解析，绕开 `127.0.0.1` 容器够不到的限制）。前提是外部 temporal 容器名为 `shannon-temporal` 且存在 `shannon-net` 网络，两处不同就改 override 文件。

> 若曾直接跑 `docker compose up` 失败、留有 `shannon-py-temporal` / `shannon-py-web`（`Created` 态空壳），直接跑 `./scripts/up.sh` 即可——它会在启动前自动清理本项目内非运行态的空壳容器。双重保险：仅限 compose 管辖的 `shannon-py-*`（`docker compose ps` 不列外部容器）、且只删 `created`/`exited`/`dead` 态，运行中的外部 `shannon-temporal` 不受影响。

### 本地开发（热更新）

前后端分离跑，前端走 Vite 热更新：

```bash
# 终端 1：后端
uv run uvicorn shannon_web.app:app --port 7878

# 终端 2：前端（:5173，proxy /api → 7878）
cd packages/web/frontend && npm install && npm run dev
```

浏览器访问 `http://localhost:5173`。

> 生产（单容器）与开发（分离）共用同一份后端代码：后端 serve 静态由 `SHANNON_WEB_FRONTEND_DIR` 控制，开发时不设此变量即跳过。详见 [设计 spec](docs/superpowers/specs/2026-07-03-web-single-container-deploy-design.md)。

## 架构概览

shannon-py 有两个核心架构特性，理解它们有助于调参与排障：

- **双引擎**：业务流程（白盒/黑盒）不感知底层用哪个 SDK；同一份 vuln prompt 在两引擎下行为对齐、可互换。切引擎 = 改 profile 里的 `SHANNON_AI_PROVIDER`。
- **双轨检测**（白盒注入/XSS/SSRF）：GitNexus 轨（确定性代码索引 → 候选链 → 轻量 LLM 判定）与 LLM 轨（纯 LLM agent 自给自足分析）**各自独立**，只在合并器做 verdict OR，互为兜底。token 紧张时可用 `SHANNON_LLM_TRACK_ENABLED=0` **只关 inj/xss/ssrf 的 vuln agent**（靠 GitNexus 轨兜底）；authz / auth 的 LLM agent 仍跑（GitNexus 做不了 authz Vertical/Context + auth，关了会降安全效果）。

深入设计见 [GitNexus 轨生命周期分析](docs/gitnexus-track-analysis.md) 与 [系统架构](docs/architecture.md)。架构不变量与开发约定见根目录 `CLAUDE.md`。

## 文档

- [快速开始](docs/getting-started.md)
- [系统架构](docs/architecture.md)
- [Agent 说明](docs/agents.md)
- [API 参考](docs/api-reference.md)
- [Prompt 工程](docs/prompt-engineering.md)
- [配置指南](docs/configuration.md)
- [GitNexus 轨生命周期分析](docs/gitnexus-track-analysis.md)
- [白盒→黑盒交接运行手册](docs/whitebox-blackbox-handoff.md)

## 项目结构

```
shannon-py/
├── packages/
│   ├── core/                    # 共享模型、配置解析、agent 集成层与工具函数
│   ├── whitebox/                # 白盒源码漏洞分析扫描器
│   ├── blackbox/                # 黑盒运行时漏洞验证和报告生成
│   ├── combined/                # 白盒+黑盒组合编排
│   ├── multi/                   # 多仓 / 跨仓扫描
│   └── web/                     # Web 平台：后端 FastAPI + 前端 SPA
│       └── frontend/            # Vite + React 前端（构建产物由后端单容器 serve）
├── apps/                        # 原始 TS 参考（cli / worker）
├── prompts/                     # Prompt 模板文件
├── scripts/                     # 验证 / 调试脚本（如 validate_*_task_probe.py）
├── docs/                        # 项目文档
├── docker-compose.yml           # temporal + web 单容器部署
├── .env.example                 # 共享配置模板
├── .env.profiles.example/       # 各 profile 的引擎/账号模板
└── pyproject.toml               # uv workspace 配置
```
