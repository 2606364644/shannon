# Spec 2: Docker Worker 容器化

**日期**: 2026-06-07
**状态**: 已批准

## 背景

原始 `shannon` 项目的 Worker 运行在 Docker 容器内，提供网络隔离、文件权限映射、环境一致性等能力。`shannon-py` 目前 Worker 在本地运行，仅 Temporal 基础设施运行在 Docker 中。

本 spec 将 Worker 容器化，使 `shannon-py` 对齐原始项目的隔离运行架构。

## 架构

```
┌─────────────────────────────────────────────┐
│  宿主机                                      │
│  ┌─────────┐   docker run   ┌─────────────┐ │
│  │   CLI   │ ──────────────▶│   Worker    │ │
│  │ (本地)  │                │  (容器内)    │ │
│  └────┬────┘                └──────┬──────┘ │
│       │                            │        │
│       │  docker compose            │        │
│       └──────────▶┌─────────┐      │        │
│                   │ Temporal│◀─────┘        │
│                   │ (容器)  │                │
│                   └─────────┘                │
└─────────────────────────────────────────────┘
```

- CLI 在宿主机运行，负责构建镜像、启动 Worker 容器
- Worker 在容器内运行，通过 Temporal 网络连接 Temporal Server
- Temporal Server 保持现有 docker-compose 管理

## 运行模式

CLI 驱动容器模式：

1. 用户运行 CLI 命令（如 `shannon-whitebox start --repo /path --docker`）
2. CLI 检测 `--docker` 标志或 `SHANNON_DOCKER=true` 环境变量
3. CLI 构建或拉取 Worker 镜像（仅本地构建）
4. CLI 启动 Worker 容器，传入必要的环境变量和 volume 挂载
5. Worker 容器内执行扫描任务

## 新增文件

### `Dockerfile.worker`

基于 Python 的 Worker 镜像：

```dockerfile
FROM python:3.12-slim

# 安装系统依赖（Playwright + Chrome 所需）
RUN apt-get update && apt-get install -y \
    wget gnupg2 libnss3 libnspr4 libatk1.0-0 \
    libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

# 安装项目依赖
WORKDIR /app
COPY . .
RUN pip install -e ".[all]"

# 安装 Playwright 浏览器
RUN playwright install chromium

# 创建非 root 用户
RUN groupadd -g 1001 shannon && \
    useradd -u 1001 -g shannon -s /bin/bash -M shannon && \
    mkdir -p /app/sessions /app/workspaces /tmp/.claude && \
    chown -R shannon:shannon /app/sessions /app/workspaces /tmp/.claude

COPY apps/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
```

### `apps/entrypoint.sh`

容器入口点，负责 UID/GID 重映射：

```bash
#!/bin/bash
set -e

TARGET_UID="${SHANNON_HOST_UID:-}"
TARGET_GID="${SHANNON_HOST_GID:-}"

if [ -n "$TARGET_UID" ] && [ "$TARGET_UID" != "$(id -u shannon)" ]; then
    userdel shannon 2>/dev/null || true
    groupdel shannon 2>/dev/null || true
    groupadd -g "$TARGET_GID" shannon
    useradd -u "$TARGET_UID" -g shannon -s /bin/bash -M shannon
    chown -R shannon:shannon /app/sessions /app/workspaces /tmp/.claude
fi

exec su -m shannon -c "exec $*"
```

### `packages/core/src/shannon_core/docker/` 目录

```
docker/
├── __init__.py
├── container.py   # 容器生命周期管理
├── hosts.py       # /etc/hosts 转发
└── volumes.py     # Volume 挂载策略
```

#### `container.py` — 容器生命周期

```python
class WorkerContainer:
    """管理 Worker Docker 容器的生命周期"""

    async def build(self) -> str: ...
    async def start(self, opts: WorkerOptions) -> str: ...
    async def stop(self, container_id: str) -> None: ...
    async def logs(self, container_id: str) -> AsyncIterator[str]: ...
    async def wait(self, container_id: str) -> int: ...

@dataclass
class WorkerOptions:
    repo_path: str
    network: str = "shannon-py-net"
    env_vars: dict[str, str] = field(default_factory=dict)
    volumes: list[VolumeMount] = field(default_factory=list)
    add_hosts: list[str] = field(default_factory=list)
```

#### `hosts.py` — /etc/hosts 转发

```python
SKIP_NAMES = frozenset([
    "localhost", "ip6-localhost", "host.docker.internal",
    "gateway.docker.internal", "kubernetes.docker.internal",
])

def parse_etc_hosts(path: str = "/etc/hosts") -> list[tuple[str, str]]: ...
def should_skip_ip(ip: str) -> bool: ...
def to_docker_add_hosts(entries: list[tuple[str, str]]) -> list[str]: ...
```

逻辑：
1. 读取 `/etc/hosts`，解析 IP-host 映射
2. 过滤内置条目（localhost 等）和无效 IP（0.0.0.0、link-local）
3. 将 loopback 地址（127.x.x.x、::1）转为 `host-gateway`
4. 生成 `--add-host` 参数列表

#### `volumes.py` — Volume 挂载

```python
@dataclass
class VolumeMount:
    host_path: str
    container_path: str
    read_only: bool = False

def build_volume_mounts(
    repo_path: str,
    workspace_path: str | None = None,
) -> list[VolumeMount]: ...
```

挂载策略：
```
目标目录:ro                          → 扫描目标（只读）
workspaces/                          → /app/workspaces（可写）
目标目录/.shannon/deliverables       → overlay（可写）
目标目录/.shannon/scratchpad         → overlay（可写）
```

## 环境变量

```bash
# =============================================================================
# Docker Worker 配置
# =============================================================================

# 启用容器化 Worker（默认本地运行）
# SHANNON_DOCKER=false

# Worker 镜像名（默认 shannon-py-worker）
# SHANNON_DOCKER_IMAGE=shannon-py-worker

# 转发 /etc/hosts 到容器（仅 Linux 有效，macOS 不需要）
# SHANNON_FORWARD_HOSTS=true

# 容器内 UID/GID（自动检测，通常不需要手动设置）
# SHANNON_HOST_UID=
# SHANNON_HOST_GID=
```

## CLI 集成

在现有 CLI 命令中添加 `--docker` 标志：

```bash
# 本地运行（默认）
shannon-whitebox start --repo /path/to/repo

# Docker 容器化运行
shannon-whitebox start --repo /path/to/repo --docker
```

当 `--docker` 启用时：
1. 检查 Docker 是否可用
2. 构建 Worker 镜像（如果不存在）
3. 确保 Temporal 容器运行
4. 创建 `shannon-py-net` 网络
5. 解析 hosts 转发（Linux）
6. 构建 volume 挂载
7. 启动 Worker 容器
8. 流式输出容器日志
9. 等待容器完成，返回退出码

## 平台兼容性

| 功能 | Linux | macOS |
|---|---|---|
| Worker 容器化 | ✅ | ✅ |
| Hosts 转发 | ✅ | ❌（Docker Desktop 自带 host.docker.internal） |
| UID/GID 映射 | ✅ | ❌（macOS Docker 不需要） |

## 测试要点

- `--docker` 模式下 Worker 容器正确启动并连接 Temporal
- Hosts 转发：自定义 /etc/hosts 条目在容器内可解析
- UID/GID 映射：容器内产出的文件宿主机用户可读写
- Volume 挂载：只读目标 + 可写 deliverables/scratchpad overlay
- macOS 下 hosts 转发和 UID/GID 映射被正确跳过
- 容器异常退出时 CLI 正确报告错误
