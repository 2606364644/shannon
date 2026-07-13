# WEB 扫描 worker 容器基建（C1 Phase A）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建独立 worker 容器（黑白盒共用镜像），常驻 temporal worker 消费固定 task queue，装齐 gitnexus+agent-browser+chrome 依赖——为 C1 后续阶段（run_scan 拆分 + web 改提交）打地基。

**Architecture:** 新建 `packages/worker` 包（`run_worker` 入口聚合 `WhiteboxScanWorkflow` + `BlackboxScanWorkflow` + 全部 activities，起两个常驻 Worker 消费固定 queue `shannon-py-wb-web` / `shannon-py-bb-web`）+ `packages/worker/Dockerfile`（python:3.12-slim + uv sync + node22 + gitnexus@1.6.8 + ladybugdb + agent-browser + chrome + safe.directory `*`）+ compose `worker` service。web 容器**不动**（仍 fork，仍 broken——这是 C1 中间状态，Plan 2 才让 web 改提交）。Plan 1 产出可独立验证的 worker 容器基建（装齐依赖 + 连 temporal + 注册 worker）。

**Tech Stack:** temporalio Python SDK / Docker / docker-compose / uv workspace / npm（gitnexus + agent-browser）

## Global Constraints

- **分支 `feat/fork-py`**（本地多项未 push；动代码前看 git log + memory）。
- **不做 B**：web 容器保持现状不胖化，依赖全装进 worker 容器（用户决策）。
- **黑白盒共用一个镜像**：单一 `packages/worker/Dockerfile`，不拆白盒/黑盒镜像。
- **safe.directory 用 `'*'`**（全信任），**不是**带路径通配（`78881cfa` 的 `'/app/repos/*'` 实测失效——git `*` 不跨 `/`）。
- **gitnexus@1.6.8 两步式**：`npm install -g --prefix=/usr --ignore-scripts gitnexus@1.6.8` 后必须补 `node @ladybugdb/core/install.js`（拷 lbugjs.node），否则 `gitnexus doctor` 报 native 缺失。
- **黑盒默认引擎 agent-browser**（`blackbox/pipeline/activities.py:520`），非 playwright。`agent-browser install` 自带 Chrome 下载。
- **CLI 零改动**：Plan 1 不碰 `shannon-whitebox` / `shannon-blackbox` 的 CLI 入口与 `run_scan`，不碰 `scan_manager`。
- **TDD + 只跑改动相关测试**：全套 pytest 有预存挂起/失败（见 memory `feat-fork-py-test-gotchas`），勿广跑全套。
- **国内镜像**：npm 用 `https://registry.npmmirror.com`，pip/uv 用 `https://mirrors.aliyun.com/pypi/simple/`，apt 用 aliyun。

## File Structure

| 文件 | 职责 | 动作 |
|------|------|------|
| `packages/core/src/shannon_core/services/temporal_infra.py` | 加 WEB 固定 task queue 常量（web 提交端 + worker 消费端共用单一来源） | Modify |
| `packages/core/tests/services/test_temporal_infra_web_queues.py` | 常量值 + 隔离测试 | Create |
| `packages/worker/pyproject.toml` | worker 包声明（deps: shannon-whitebox/blackbox/core + temporalio；script `shannon-worker`） | Create |
| `packages/worker/src/shannon_worker/__init__.py` | 包标识 | Create |
| `packages/worker/src/shannon_worker/runner.py` | `run_worker(temporal_address)` + `main()`：连接 temporal、起两个常驻 Worker（白盒/黑盒固定 queue） | Create |
| `packages/worker/tests/__init__.py` | 测试包标识 | Create |
| `packages/worker/tests/test_runner.py` | run_worker 注册逻辑（mock Client/Worker） | Create |
| `packages/worker/Dockerfile` | 共用镜像（python+uv sync+node+gitnexus+agent-browser+chrome+safe.directory） | Create |
| `docker-compose.yml` | 加 `worker` service | Modify |

> root `pyproject.toml` 的 `tool.uv.workspace.members = ["packages/*"]` 是 glob，`packages/worker/` 自动纳入，**无需改 root pyproject**。

---

### Task 1: WEB 固定 task queue 常量 + 隔离测试

**Files:**
- Modify: `packages/core/src/shannon_core/services/temporal_infra.py`（在 `generate_task_queue` 后加常量）
- Test: `packages/core/tests/services/test_temporal_infra_web_queues.py`

**Interfaces:**
- Produces: `WEB_TASK_QUEUE_WHITEBOX`（`"shannon-py-wb-web"`）、`WEB_TASK_QUEUE_BLACKBOX`（`"shannon-py-bb-web"`）——后续 worker `run_worker`（Task 2）与 web 提交端（Plan 2 `scan_manager`）共用。

- [ ] **Step 1: Write the failing test**

```python
# packages/core/tests/services/test_temporal_infra_web_queues.py
"""WEB 固定 task queue 常量：web 提交端与 worker 消费端的单一来源。

CLI 路径用 generate_task_queue(prefix) 生成唯一随机 queue（self-contained）；
WEB 路径用固定 queue，worker 容器常驻消费。两者前缀同但值不同，互不消费。"""
from shannon_core.services.temporal_infra import (
    generate_task_queue,
    WEB_TASK_QUEUE_WHITEBOX,
    WEB_TASK_QUEUE_BLACKBOX,
)


def test_web_task_queue_constants_are_fixed_strings():
    """WEB queue 是固定值（非随机），worker 容器据此常驻注册。"""
    assert WEB_TASK_QUEUE_WHITEBOX == "shannon-py-wb-web"
    assert WEB_TASK_QUEUE_BLACKBOX == "shannon-py-bb-web"


def test_web_queues_distinct_from_cli_random_queues():
    """WEB 固定 queue 与 CLI 随机 queue 不同——worker 注册固定 queue 不会收到 CLI 提交。"""
    cli_wb = generate_task_queue("shannon-py-wb")
    cli_bb = generate_task_queue("shannon-py-bb")
    assert cli_wb != WEB_TASK_QUEUE_WHITEBOX  # 随机 hex 后缀 vs 固定 -web
    assert cli_bb != WEB_TASK_QUEUE_BLACKBOX
    assert not WEB_TASK_QUEUE_WHITEBOX.endswith(cli_wb[-8:])


def test_web_queues_whitebox_and_blackbox_distinct():
    """白盒/黑盒 WEB queue 不同，两个 worker 各消费各的。"""
    assert WEB_TASK_QUEUE_WHITEBOX != WEB_TASK_QUEUE_BLACKBOX
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/services/test_temporal_infra_web_queues.py -v`
Expected: FAIL with `ImportError: cannot import name 'WEB_TASK_QUEUE_WHITEBOX'`

- [ ] **Step 3: Write minimal implementation**

在 `packages/core/src/shannon_core/services/temporal_infra.py` 的 `generate_task_queue` 函数后追加：

```python
# WEB 固定 task queue：web 提交端（scan_manager）与 worker 容器消费端共用单一来源。
# CLI 路径仍用 generate_task_queue(prefix) 生成唯一随机 queue（self-contained，零改动）；
# WEB 路径用固定 queue，worker 容器常驻注册消费。前缀同（shannon-py-wb/bb）但值不同
# （-web 后缀 vs 8hex 后缀），temporal queue 精确匹配 → CLI 与 WEB 互不消费、互不干扰。
WEB_TASK_QUEUE_WHITEBOX = "shannon-py-wb-web"
WEB_TASK_QUEUE_BLACKBOX = "shannon-py-bb-web"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/services/test_temporal_infra_web_queues.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/services/temporal_infra.py packages/core/tests/services/test_temporal_infra_web_queues.py
git commit -m "feat(temporal): WEB 固定 task queue 常量(C1 worker 容器基建)"
```

---

### Task 2: worker 包 + run_worker 入口（聚合黑白盒）

**Files:**
- Create: `packages/worker/pyproject.toml`
- Create: `packages/worker/src/shannon_worker/__init__.py`
- Create: `packages/worker/src/shannon_worker/runner.py`
- Create: `packages/worker/tests/__init__.py`
- Create: `packages/worker/tests/test_runner.py`

**Interfaces:**
- Consumes: `WhiteboxScanWorkflow`（`shannon_whitebox.pipeline.workflows`）、`BlackboxScanWorkflow`（`shannon_blackbox.pipeline.workflows`）、白盒 activities（`shannon_whitebox.pipeline.activities`，25 个）、黑盒 activities（`shannon_blackbox.pipeline.activities`，16 个）、`WEB_TASK_QUEUE_WHITEBOX/BLACKBOX`（Task 1）、`temporalio.client.Client` / `temporalio.worker.Worker`。
- Produces: `async def run_worker(temporal_address: str = "localhost:7233") -> None`（常驻，起两个 Worker 并行 `run()`，永不返回除非取消）；`def main() -> None`（CLI 入口 `shannon-worker`，`asyncio.run(run_worker())`）。

- [ ] **Step 1: Write the failing test**

```python
# packages/worker/tests/test_runner.py
"""run_worker：连接 temporal，起两个常驻 Worker（白盒/黑盒固定 queue），注册对应 workflow+activities。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shannon_core.services.temporal_infra import (
    WEB_TASK_QUEUE_WHITEBOX,
    WEB_TASK_QUEUE_BLACKBOX,
)
from shannon_whitebox.pipeline.workflows import WhiteboxScanWorkflow
from shannon_blackbox.pipeline.workflows import BlackboxScanWorkflow


@pytest.mark.asyncio
async def test_run_worker_connects_and_registers_two_workers():
    """run_worker 连 temporal + 起两个 Worker（白盒/黑盒固定 queue）+ 并行 run。"""
    from shannon_worker.runner import run_worker

    mock_client = AsyncMock()

    wb_worker = MagicMock()
    wb_worker.run = AsyncMock(return_value=None)
    bb_worker = MagicMock()
    bb_worker.run = AsyncMock(return_value=None)

    with (
        patch("shannon_worker.runner.Client.connect",
              AsyncMock(return_value=mock_client)) as mock_connect,
        patch("shannon_worker.runner.Worker",
              side_effect=[wb_worker, bb_worker]) as mock_worker_cls,
    ):
        await run_worker("temporal:7233")

    # 连接 temporal
    mock_connect.assert_awaited_once_with("temporal:7233")

    # 两个 Worker 创建，task_queue + workflows 正确
    assert mock_worker_cls.call_count == 2
    wb_call, bb_call = mock_worker_cls.call_args_list
    assert wb_call.kwargs["client"] is mock_client
    assert wb_call.kwargs["task_queue"] == WEB_TASK_QUEUE_WHITEBOX
    assert WhiteboxScanWorkflow in wb_call.kwargs["workflows"]
    assert len(wb_call.kwargs["activities"]) >= 20  # 白盒 ~25 activities
    assert bb_call.kwargs["client"] is mock_client
    assert bb_call.kwargs["task_queue"] == WEB_TASK_QUEUE_BLACKBOX
    assert BlackboxScanWorkflow in bb_call.kwargs["workflows"]
    assert len(bb_call.kwargs["activities"]) >= 10  # 黑盒 ~16 activities

    # 两个 worker 都 run（并行 gather）
    wb_worker.run.assert_awaited_once()
    bb_worker.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_worker_propagates_connect_failure():
    """temporal 连不上时 run_worker 抛错（fail-fast，不静默吞）。"""
    from shannon_worker.runner import run_worker

    with patch("shannon_worker.runner.Client.connect",
               AsyncMock(side_effect=RuntimeError("temporal down"))):
        with pytest.raises(RuntimeError, match="temporal down"):
            await run_worker("bad:7233")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/worker/tests/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shannon_worker'`

- [ ] **Step 3: Write minimal implementation**

```toml
# packages/worker/pyproject.toml
[project]
name = "shannon-worker"
version = "0.1.0"
description = "Shannon 常驻 worker 容器入口（消费 WEB 固定 task queue，聚合白盒+黑盒 workflow）"
requires-python = ">=3.12"
dependencies = [
    "shannon-core",
    "shannon-whitebox",
    "shannon-blackbox",
    "temporalio>=1.7",
]

[project.scripts]
shannon-worker = "shannon_worker.runner:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/shannon_worker"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

```python
# packages/worker/src/shannon_worker/__init__.py
```

```python
# packages/worker/src/shannon_worker/runner.py
"""常驻 worker 容器入口：连接 temporal，起两个 Worker 消费 WEB 固定 task queue。

与 CLI 的 self-contained run_scan 不同——这里 worker 只消费、不提交：
- 白盒 Worker 消费 shannon-py-wb-web（web scan_manager 提交，Plan 2 接入）
- 黑盒 Worker 消费 shannon-py-bb-web

CLI 路径（shannon-whitebox/-blackbox start）零改动，仍用 generate_task_queue
唯一随机 queue 自己提交自己消费，与本 worker 容器互不干扰（queue 精确匹配）。
"""
import asyncio
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from shannon_core.services.temporal_infra import (
    WEB_TASK_QUEUE_WHITEBOX,
    WEB_TASK_QUEUE_BLACKBOX,
)
from shannon_whitebox.pipeline.workflows import WhiteboxScanWorkflow
from shannon_whitebox.pipeline.activities import (
    render_findings, assemble_report, run_agent,
    run_auth_config_scan, run_auth_gitnexus_judge, run_authz_gitnexus_judge,
    run_code_index, run_credential_check, run_merge_dual_track_queues,
    run_merge_sink_reports, run_entry_point_fusion, run_preflight, run_risk_scoring,
    run_save_adjudication, run_vuln_agent, run_attack_chain_llm_agent,
    run_attack_chain_assembly_v2, run_framework_analysis, run_frontend_mapping,
    run_gitnexus_chain_verdict, run_route_chain_building, generate_poc_report,
    log_phase_start_activity, log_phase_complete_activity, log_info_activity,
)
from shannon_blackbox.pipeline.workflows import BlackboxScanWorkflow
from shannon_blackbox.pipeline.activities import (
    run_blackbox_preflight, run_blackbox_auth_validation, run_recon,
    run_exploit_agent, validate_exploitation_queue, assemble_report as bb_assemble_report,
    run_report_agent, finalize_report, generate_poc_report as bb_generate_poc_report,
    log_phase_start_activity as bb_log_phase_start, log_phase_complete_activity as bb_log_phase_complete,
    log_info_activity as bb_log_info, load_correlation_context, resolve_blackbox_engine,
    detect_whitebox_results, write_engine_config_for_session, cleanup_engine_configs,
)

_GRACEFUL_SHUTDOWN = timedelta(seconds=10)


async def run_worker(temporal_address: str = "localhost:7233") -> None:
    """连接 temporal，起白盒+黑盒两个常驻 Worker 并行消费 WEB 固定 queue。

    永不主动返回（常驻）；temporal 连接失败 fail-fast 抛错。
    """
    client = await Client.connect(temporal_address)

    wb_worker = Worker(
        client=client,
        task_queue=WEB_TASK_QUEUE_WHITEBOX,
        workflows=[WhiteboxScanWorkflow],
        activities=[
            render_findings, assemble_report, run_agent,
            run_auth_config_scan, run_auth_gitnexus_judge, run_authz_gitnexus_judge,
            run_code_index, run_credential_check, run_merge_dual_track_queues,
            run_merge_sink_reports, run_entry_point_fusion, run_preflight, run_risk_scoring,
            run_save_adjudication, run_vuln_agent, run_attack_chain_llm_agent,
            run_attack_chain_assembly_v2, run_framework_analysis, run_frontend_mapping,
            run_gitnexus_chain_verdict, run_route_chain_building, generate_poc_report,
            log_phase_start_activity, log_phase_complete_activity, log_info_activity,
        ],
        graceful_shutdown_timeout=_GRACEFUL_SHUTDOWN,
    )
    bb_worker = Worker(
        client=client,
        task_queue=WEB_TASK_QUEUE_BLACKBOX,
        workflows=[BlackboxScanWorkflow],
        activities=[
            run_blackbox_preflight, run_blackbox_auth_validation, run_recon,
            run_exploit_agent, validate_exploitation_queue, bb_assemble_report,
            run_report_agent, finalize_report, bb_generate_poc_report,
            bb_log_phase_start, bb_log_phase_complete, bb_log_info,
            load_correlation_context, resolve_blackbox_engine, detect_whitebox_results,
            write_engine_config_for_session, cleanup_engine_configs,
        ],
        graceful_shutdown_timeout=_GRACEFUL_SHUTDOWN,
    )

    await asyncio.gather(wb_worker.run(), bb_worker.run())


def main() -> None:
    import os
    host = os.environ.get("SHANNON_TEMPORAL_HOST", "localhost")
    port = os.environ.get("SHANNON_TEMPORAL_PORT", "7233")
    asyncio.run(run_worker(f"{host}:{port}"))
```

```python
# packages/worker/tests/__init__.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/worker/tests/test_runner.py -v`
Expected: 2 PASS

> 若 `bb_assemble_report` / `bb_generate_poc_report` 等 `as` 别名导致 import 歧义（白盒黑盒同名 activity），改用 `from shannon_blackbox.pipeline import activities as bb_act` 后逐个引用——但优先保留 `as` 别名（hatchling wheel 只打包 `src/shannon_worker`，import 仍按完整模块路径解析，别名安全）。

- [ ] **Step 5: Commit**

```bash
git add packages/worker/pyproject.toml packages/worker/src packages/worker/tests
git commit -m "feat(worker): run_worker 常驻入口聚合白盒+黑盒 workflow(C1 Phase A)"
```

---

### Task 3: worker Dockerfile（黑白盒共用镜像）

**Files:**
- Create: `packages/worker/Dockerfile`

**Interfaces:**
- Produces: 镜像 `shannon-py-worker`（python+uv sync 装全部 workspace 包 + node22 + gitnexus@1.6.8 + ladybugdb + agent-browser + chrome + safe.directory `*`），`CMD` 跑 `shannon-worker`（`run_worker`）。

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# packages/worker/Dockerfile
# C1 Phase A：黑白盒共用 worker 镜像（web 容器保持瘦，依赖全装这里）。
FROM python:3.12-slim

# ── apt：git + chrome 运行时依赖 + curl(gitnexus)/ca ──
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
       git ca-certificates curl gnupg \
       # headless Chrome 运行时依赖（agent-browser install 下载的 Chrome 需要）
       libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
       libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
       libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# ── safe.directory：全信任（修正 78881cfa 的 '/app/repos/*' 带路径通配失效）──
# git 的 * 不跨 /，带路径 pattern 不 glob；只有整值 '*' 或精确路径有效。
# worker 容器扫 repos 下任意属主仓库（root clone / shannon-user clone 挂载），靠全信任避免 dubious ownership。
RUN git config --global --add safe.directory '*'

# ── node 22（NodeSource，供 gitnexus + agent-browser）──
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── gitnexus@1.6.8 + ladybugdb native binding（白盒确定性轨）──
# --ignore-scripts 跳 onnxruntime post-install（HTTP 302 坑）；补 @ladybugdb/core/install.js 拷 lbugjs.node。
RUN npm config set registry https://registry.npmmirror.com \
    && npm install -g --prefix=/usr --ignore-scripts gitnexus@1.6.8 \
    && node "$(npm root -g --prefix=/usr)/gitnexus/node_modules/@ladybugdb/core/install.js" \
    && gitnexus --version

# ── agent-browser + Chrome（黑盒默认 browser 引擎）──
# agent-browser install 自带 Chrome 下载（参考 scripts/bootstrap.sh:142-153）。
RUN npm install -g agent-browser@latest \
    && agent-browser install \
    && agent-browser --version

# ── python：uv sync 装全部 workspace 包（含 shannon-worker）──
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/

RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages

# 不用 --frozen：让 uv 走清华源下载（与 web Dockerfile 同策略）。lockfile 仍锁版本。
RUN uv sync

# .venv/bin 让 shannon-worker 可解析；/usr/bin 让 gitnexus/agent-browser/node 可解析。
ENV PATH="/app/.venv/bin:/usr/bin:${PATH}"

CMD ["shannon-worker"]
```

- [ ] **Step 2: Build the image（冒烟）**

Run: `docker build -f packages/worker/Dockerfile -t shannon-py-worker:phase-a .`
Expected: build 成功，末尾 `gitnexus --version` / `agent-browser --version` 输出版本号（build 内联验证）。

> 若 `agent-browser install`（Chrome 下载）因网络失败：临时 `--build-arg` 加 HTTP proxy，或先 `npm install -g agent-browser@latest` 不跑 install（chrome 后续手动补）。失败时记入 Task 5 验证项跟进。

- [ ] **Step 3: Commit**

```bash
git add packages/worker/Dockerfile
git commit -m "feat(worker): 共用镜像 Dockerfile(gitnexus+agent-browser+chrome+safe.directory *)"
```

---

### Task 4: compose worker service

**Files:**
- Modify: `docker-compose.yml`（在 `web` service 后加 `worker` service）

**Interfaces:**
- Produces: compose `worker` service（同业务卷 + depends_on temporal healthy + resource limits + temporal host env）。

- [ ] **Step 1: Add worker service**

在 `docker-compose.yml` 的 `web` service 块之后、`volumes:` 之前插入：

```yaml
  worker:
    # C1 Phase A：常驻 worker 容器，消费 WEB 固定 task queue（白盒 shannon-py-wb-web / 黑盒 shannon-py-bb-web）。
    # 装齐 gitnexus+agent-browser+chrome（共用镜像）；web 容器保持瘦。
    # Plan 1 阶段 web 仍 fork（仍 broken），worker 起着暂无提交者——Plan 2 scan_manager 改 start_workflow 后才消费。
    build:
      context: .
      dockerfile: packages/worker/Dockerfile
    container_name: shannon-py-worker
    volumes:
      - ./workspaces:/app/workspaces
      - ./repos:/app/repos
      - ./configs:/app/configs
      - ./.env:/app/.env:ro
      - ./.env.profiles:/app/.env.profiles:ro
    env_file:
      - .env
    environment:
      - SHANNON_TEMPORAL_HOST=temporal
      - SHANNON_TEMPORAL_PORT=7233
      - SHANNON_TEMPORALIO_LOG_LEVEL=${SHANNON_TEMPORALIO_LOG_LEVEL:-WARNING}
    depends_on:
      temporal:
        condition: service_healthy
    deploy:
      resources:
        limits:
          cpus: '${SHANNON_WORKER_CPUS:-2}'
          memory: '${SHANNON_WORKER_MEMORY:-4g}'
    restart: unless-stopped
```

- [ ] **Step 2: Validate compose config**

Run: `docker compose config --services`
Expected: 输出含 `temporal`、`web`、`worker` 三者，无 YAML 报错。

Run: `docker compose config | grep -A3 'worker:'`
Expected: 看到 worker service 块，resource limits 正确渲染。

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(compose): worker service(C1 Phase A 常驻 worker 容器)"
```

---

### Task 5: build 冒烟 + safe.directory 验证（容器内依赖齐全）

**Files:**
- 验证脚本（手动跑，不落 repo）：无新建文件

**Interfaces:**
- 验证 Task 3 镜像在容器内依赖齐全 + safe.directory `*` 对非 root 属主仓库生效。

- [ ] **Step 1: 起容器验证依赖 + safe.directory**

Run（一条命令验全部）:

```bash
docker run --rm -v "$PWD/repos:/app/repos" shannon-py-worker:phase-a bash -c '
  echo "=== gitnexus ===" && gitnexus --version &&
  echo "=== agent-browser ===" && agent-browser --version &&
  echo "=== node ===" && node --version &&
  echo "=== safe.directory config ===" && git config --global --get-all safe.directory &&
  echo "=== shannon-worker 入口 ===" && which shannon-worker &&
  echo "=== dubious ownership 测试(非 root 属主 repo) ===" &&
  for r in /app/repos/*/; do
    git -C "$r" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
      && echo "OK: $r" \
      || echo "FAIL(dubious ownership): $r"
  done
'
```

Expected:
- `gitnexus` / `agent-browser` / `node` / `shannon-worker` 各输出版本/路径
- `safe.directory` 输出 `*`
- repos 下每个仓库 `OK:`（**不**出现 `FAIL(dubious ownership)`——这是修正 78881cfa 失效通配的关键验证）

- [ ] **Step 2: 若有 FAIL，定位**

- `FAIL(dubious ownership)` → 检查 Dockerfile 的 `git config --global --add safe.directory '*'` 是否生效（容器内 `git config --global --get-all safe.directory` 必须输出 `*`）。
- `gitnexus: command not found` → npm --prefix=/usr 装的路径没进 PATH（检查 `ENV PATH` 含 `/usr/bin`）。
- `agent-browser install` 失败 → 见 Task 3 Step 2 注（网络/Chrome 下载），手动补。

- [ ] **Step 3: 无代码改动则跳过 commit；有 Dockerfile/compose fix 则 commit**

```bash
# 仅当本 task 触发 Dockerfile/compose 修改时：
git add packages/worker/Dockerfile docker-compose.yml
git commit -m "fix(worker): 容器内依赖/safe.directory 冒烟修正"
```

---

### Task 6: worker 消费 smoke（连 temporal + 注册 poller）

**Files:**
- 无新建文件（集成验证）

**Interfaces:**
- 验证 worker 容器能连 temporal、注册到两个固定 queue、poller 活跃。

- [ ] **Step 1: 起 temporal + worker**

Run: `docker compose up -d temporal && docker compose up -d worker`

- [ ] **Step 2: 看 worker 日志确认连 temporal + 起 Worker**

Run: `docker compose logs worker --tail 30`
Expected: 看到 worker 连上 `temporal:7233` + 两个 Worker 启动（白盒/黑盒 queue）。若 `SHANNON_TEMPORALIO_LOG_LEVEL=DEBUG` 可见 poller 线程启动。

- [ ] **Step 3: temporal 侧确认 worker 注册到固定 queue**

Run: `docker compose exec temporal temporal task-queue describe --task-queue shannon-py-wb-web --address localhost:7233`
Expected: `pollers` 区有非空条目（worker 容器的 poller 在 poll 该 queue）。

Run: `docker compose exec temporal temporal task-queue describe --task-queue shannon-py-bb-web --address localhost:7233`
Expected: 同上，黑盒 queue 也有 poller。

- [ ] **Step 4: 若 poller 为空，定位**

- worker 日志报连接错误 → `SHANNON_TEMPORAL_HOST=temporal` 是否解析（compose 网络内服务名）。
- worker 容器 Exited → `docker compose logs worker` 看启动异常（import 错误 / Client.connect 失败）。

- [ ] **Step 5: 无代码改动则跳过 commit**

Phase A 完成——worker 容器基建可验证：装齐依赖 + 连 temporal + 注册固定 queue poller。此时 web 仍 fork（Plan 2 才让 web 改 start_workflow 提交到这些 queue）。

---

## Self-Review

**1. Spec coverage（对照 spec 本 plan 覆盖的 Phase A 部分）:**
- spec §5.1 worker Dockerfile（共用镜像）→ Task 3 ✓
- spec §5.2 compose worker service → Task 4 ✓
- spec §5.5 worker 容器入口（常驻 Worker 固定 queue）→ Task 2 ✓
- spec §6 task queue 隔离（WEB 固定 queue）→ Task 1 ✓
- spec §9 safe.directory `*` → Task 3 Dockerfile + Task 5 验证 ✓
- spec §7 并发（resource limits）→ Task 4 deploy.resources ✓（worker 级 max_concurrent 留 Plan 2/3）
- spec §13 协同（c045c3a8 SHANNON_TEMPORALIO_LOG_LEVEL）→ Task 4 env ✓

**本 plan 明确不覆盖（留给后续 plan，spec §5.3/5.4/5.6/§10/§15 Step2-4）:**
- scan_manager fork→start_workflow（Plan 2）
- run_scan 拆分（session/event_file/owner/resume/heartbeat/summary 重新分配，白盒+黑盒各一套）（Plan 2，spec §14-5 最高风险）
- PipelineInput event_file 字段（Plan 2）
- cancel temporal terminate（Plan 3）
- 黑盒对称拆分回归（Plan 2/3）
- worker 级 max_concurrent_workflow_tasks（Plan 3）

**2. Placeholder scan:** 无 TBD/TODO。Task 3 的 chrome apt 依赖列表是 headless Chrome 标准集（若缺漏真机补，Task 5 Step 2 有定位）。Task 5/6 是集成验证（命令 + 期望输出齐全），非 placeholder。

**3. Type consistency:** `run_worker(temporal_address: str) -> None` 在 Task 2 定义，Task 6 `main()` 调用一致。`WEB_TASK_QUEUE_WHITEBOX/BLACKBOX` 在 Task 1 定义，Task 2 import 使用，命名一致。白盒/黑盒 activities 的 `as` 别名（`bb_assemble_report` 等）在 Task 2 实现内统一。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-13-web-scan-worker-container-phase-a.md`. Two execution options:

**1. Subagent-Driven (recommended)** - 我每个 task 派一个新 subagent，task 间 review，快速迭代。

**2. Inline Execution** - 本会话内按 executing-plans 批量执行 + checkpoint review。

Which approach?

> 注：Plan 1（Phase A）完成后，C1 还有 Plan 2（run_scan 拆分 + scan_manager 改提交，spec §14-5 最高风险）+ Plan 3（黑盒对称 + cancel + 并发收尾）待写——建议 Plan 1 真机验证（Task 5/6）通过后再写 Plan 2，因 run_scan 拆分的具体形式依赖 worker 容器验证结果（依赖装法、temporal worker 常驻行为）。
