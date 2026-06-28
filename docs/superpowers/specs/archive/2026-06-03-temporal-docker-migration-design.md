# Temporal Docker 容器配置迁移设计

**日期**: 2026-06-03
**状态**: 已批准

## 背景

原始项目 shannon 通过 CLI（`apps/cli/src/docker.ts`）自动管理 Temporal 基础设施：
- `isTemporalReady()` — 健康检查
- `ensureInfra()` — 自动启动 + 等待就绪
- `start` / `stop` / `status` — 基础设施管理命令

重构项目 shannon-py 当前**没有任何基础设施管理能力**：
- 无健康检查，连接失败时用户只看到 "unknown error"
- 无自动启动，用户必须手动 `docker compose up`
- 无基础设施管理命令

## 目标

在 shannon-py 中对齐原始项目的所有 Temporal 基础设施管理能力，实现"开箱即用"。

## 能力对标

| # | 能力 | 原始项目 | shannon-py |
|---|------|---------|------------|
| 1 | Temporal 健康检查 | `isTemporalReady()` | `is_temporal_ready()` |
| 2 | 自动启动 Temporal | `ensureInfra()` | `ensure_infra()` |
| 3 | 等待就绪轮询 | 30 次 × 2s | 同样逻辑 |
| 4 | start 命令自动检测 | ✅ | ✅ |
| 5 | infra 子命令 | `status`, `stop` | `infra up/down/status` |
| 6 | 友好错误提示 | ✅ | ✅ |

## 架构设计

### 新增文件

```
packages/core/src/shannon_core/services/temporal_infra.py   # 核心基础设施管理
```

放在 `shannon-core` 中作为共享服务，whitebox 和 blackbox CLI 都可复用。

### 核心函数（`temporal_infra.py`）

```python
def is_temporal_ready(address: str = "localhost:7233") -> bool:
    """检查 Temporal 是否运行中且健康。
    尝试 Client.connect() 并检查集群健康状态。"""

async def ensure_infra(compose_file: Path | None = None) -> None:
    """确保 Temporal 基础设施可用。
    1. 如果已 ready，直接返回
    2. 执行 docker compose up -d
    3. 轮询等待就绪（30 次，2s 间隔）
    4. 超时则抛出明确异常"""

def start_temporal(compose_file: Path | None = None) -> None:
    """启动 Temporal 容器。subprocess 调用 docker compose up -d"""

def stop_temporal(compose_file: Path | None = None) -> None:
    """停止 Temporal 容器。subprocess 调用 docker compose down"""

def get_temporal_status(compose_file: Path | None = None) -> dict:
    """获取 Temporal 容器状态 + 健康检查结果"""
```

- `compose_file` 默认为 `Path(__file__).resolve().parents[4] / "docker-compose.yml"`（项目根目录）
- 使用 `subprocess` 调用 `docker compose`，无需新增 Python 依赖
- 健康检查使用 `temporalio.client.Client.connect()` 验证连接

### CLI 变更

#### whitebox CLI（`packages/whitebox/src/shannon_whitebox/cli/main.py`）

新增 `infra` 命令组：

```python
@cli.group()
def infra():
    """Manage Temporal infrastructure."""

@infra.command()
def up():
    """Start Temporal server."""

@infra.command()
def down():
    """Stop Temporal server."""

@infra.command()
def status():
    """Check Temporal server status."""
```

修改 `start` 命令：在调用 `run_scan` 前增加 `ensure_infra()`。

#### blackbox CLI（`packages/blackbox/src/shannon_blackbox/cli/main.py`）

同上，新增相同的 `infra` 命令组和修改 `start` 命令。

### docker-compose.yml

已在项目根目录创建。容器名 `shannon-py-temporal`，网络 `shannon-py-net`，与原始项目不冲突。

## 依赖

- 无新增 Python 依赖
- 使用标准库 `subprocess` 调用 `docker compose`
- 使用已有的 `temporalio` 客户端做健康检查

## 验证方式

1. `uv run shannon-whitebox infra up` — 启动 Temporal
2. `uv run shannon-whitebox infra status` — 确认 healthy
3. `uv run shannon-whitebox start --repo <path>` — 不手动启动 Temporal，验证自动管理
4. `uv run shannon-whitebox infra down` — 停止
5. 浏览器访问 `http://localhost:8233` 确认 Web UI
