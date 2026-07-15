# 2026-07-15 跨平台 docker 环境健康（ensure-docker.sh）设计

## 1. 背景

用户在 WSL2 + Docker Desktop WSL 集成的机器跑 `bash scripts/up.sh`，`worker` 镜像构建报：
```
WARN Docker Compose requires buildx plugin to be installed
the --mount option requires BuildKit. Refer to https://docs.docker.com/go/buildkit/
```

### 真根因（受控实验证明）

**docker CLI 找不到 buildx plugin → compose 退 legacy builder → legacy 不认 `RUN --mount=type=cache`。**

因果链（在本机用「遮蔽 `~/.docker/cli-plugins/docker-buildx` → 逐字复现报错 → 恢复 → 正常」的受控实验证实）：
```
docker CLI 找 buildx plugin 的路径：
  ~/.docker/cli-plugins/docker-buildx    ← v0.35.0 真二进制（唯一可用）
  /usr/local/lib/.../docker-buildx       ← dangling symlink（指向 /mnt/wsl/docker-desktop/…，该挂载点不存在）
                                         ↓
  buildx 找不到 → "unknown command: docker buildx"
              → compose 报 "requires buildx plugin" → 退 legacy builder
              → legacy 不认 RUN --mount → "the --mount option requires BuildKit"
```

**这台机叠加了两套 docker 环境：**
- apt 装的 docker-ce engine 29.1.3（真正在用）+ `docker-compose-plugin` v5.1.4（apt 真二进制）。
- Docker Desktop WSL 集成残留：往 `/usr/local/lib/docker/cli-plugins/` 塞了一堆 `-> /mnt/wsl/docker-desktop/…` symlink，但 `/mnt/wsl/docker-desktop` **未挂载** → 全部 dangling；还创建了一个 `desktop-linux` docker context（端点 `npipe:////./pipe/dockerDesktopLinuxEngine`，Windows 命名管道在 WSL2 不可用 → `docker buildx ls` 报 "protocol not available"）。

### 关键认知纠正（初稿假设错误）

本 spec **初稿假设**「compose v5.1.4 是来路不明的非官方版本（官方才 v2.x），要换 v2.x」。**这是错的**：
- **Docker Compose v5 是 2025+ 官方主线**（与 v2 功能等同，新增官方 Go SDK；从 v2 直接跳 v5，无 v3/v4）。v5.1.4 是官方的、功能正常的。
- buildx v0.35.0 也是官方的。
- github 没被劫持（v5.3.1 是真 latest）。

**问题从来不在 compose，在 buildx plugin 找不到。** 所以 `ensure-docker.sh` **不换 compose**，聚焦「确保 buildx 在」。初稿里的 `_compose_is_official_v2`（强制主版本==2）判定逻辑基于错误前提，已删除。

### 已做的止血（A，已实现并验证）

worker/web Dockerfile 去掉 `--mount=cache`，legacy builder 也能构建——跨 builder 兼容，不依赖 BuildKit。A 与本 spec 双保险：A 让构建不挑 builder，本 spec 让 BuildKit 正常工作。

## 2. 目标 / 非目标

**目标**
- 三平台（Linux / WSL2 Ubuntu / Mac）跑 `up.sh` 前，确保 docker CLI 能找到 buildx plugin（`~/.docker/cli-plugins/docker-buildx`）→ compose 走 BuildKit，干净 build + 起容器。
- runtime（Docker Desktop / docker engine）**只检测不装**。
- 一份 bash 脚本，三平台同逻辑，非 root 可跑。

**非目标**
- 不换 compose（v5.x 官方正常，不动）。
- 不装 docker runtime。
- 不自动删 `desktop-linux` context（Docker Desktop 管理的 docker 配置，有副作用；仅提示）。
- 不做 shannon-user 白盒 CLI provision 三平台化（后续）。
- 不重建已拆除的 Dockerfile `--mount`（A 的铁律不变；ensure-docker 是环境侧）。

## 3. 方案：`scripts/ensure-docker.sh`

单一职责小脚本（~120 行 bash），三平台同逻辑：
```
1. 检测 runtime：command -v docker；缺失 → 报错 + 三平台指引 + exit 1。
2. 平台/架构：uname -s → linux|darwin；uname -m → amd64(x86_64)/arm64。
3. 确保 buildx（核心）：
     docker buildx version 退出 0 → 已就位 skip；
     否则下官方 buildx release → ~/.docker/cli-plugins/docker-buildx（chmod +x）。
4. 可选清理（容错，失败仅 warn 不阻断）：
     - dangling /usr/local/lib/.../docker-buildx symlink（Docker Desktop 未挂载）→ 删；
     - desktop-linux error context → 仅提示（不自动删 docker 配置）。
5. 验证：docker buildx version + docker compose version，输出 OK。
```

**下载源 / 版本**
- buildx: `https://github.com/docker/buildx/releases/download/v$BUILDX_VER/buildx-v$BUILDX_VER.$PLAT-$ARCH`
- 版本：env `SHANNON_BUILDX_VERSION` 固定，否则运行时 `_resolve_latest` 解析 `releases/latest` redirect（避免硬编码过时版本号）。
- 中国网络：env `SHANNON_GH_BASE` 可指镜像前缀（默认 `https://github.com`）。

**为何放 `~/.docker/cli-plugins/`**
- docker CLI 搜索路径最高优先级，覆盖 `/usr/local/lib` 的 dangling。
- 三平台一致（Linux/Mac 都是这个路径）。
- 非 root 可写。

## 4. 集成（复用关系）

| 脚本 | 集成 | 说明 |
|---|---|---|
| **`up.sh`** | **`ACTION=up` 分支开头调 `ensure-docker.sh`** | 核心集成点：覆盖三平台所有 up.sh 用户；ensure-docker 失败（runtime 缺）→ `set -e` 中止，不带着坏环境硬 build。down/logs 不 build 不调。 |
| `provision.sh` | 不集成 | provision 装 docker-ce 自带 buildx（`docker-buildx-plugin` 包），无需修。 |
| 手动 | `bash scripts/ensure-docker.sh` | 单独修环境，不跑 up.sh。 |

## 5. 关键技术点

1. **buildx 可用性判定**：`docker buildx version` 退出 0 = 可用（docker CLI 把 buildx 当 plugin，找不到则 "unknown command: docker buildx"）。这是 compose 是否走 BuildKit 的**充分必要条件**（受控实验证明）。
2. **cli-plugins 路径优先级**：`~/.docker/cli-plugins/` > `/usr/local/lib/docker/cli-plugins/`。装到 `~/.docker` 覆盖系统的 dangling。
3. **dangling symlink 安全删**：目标本不存在，删了不影响任何东西（`~/.docker` buildx 已优先且有效）；Docker Desktop 集成恢复会重建。
4. **desktop-linux context 不自动删**：它是 docker context（需 `docker context rm`），Docker Desktop 管理的配置，擅自删有副作用；仅提示用户「不影响 build，如需清：`docker context rm desktop-linux`」。
5. **Mac bash 3.2**：脚本避免 bash 4+ 特性（`mapfile`、关联数组），POSIX 友好写法。
6. **幂等**：buildx 已在 / dangling 已清 / context 已提示 → 全部 skip，重复跑无副作用、无重复下载。

## 6. 测试

**纯函数单测**（source 后单测，仿 `provision.sh::detect_pkg_mgr` 的可测模式）：
- `_plat_for_uname_s`：Linux→linux、Darwin→darwin、其他→""。
- `_arch_for_uname_m`：x86_64→amd64、aarch64/arm64→arm64、其他→""。
- `_buildx_download_url`：base+plat+arch+ver → URL（asset 命名 `buildx-v<ver>.<plat>-<arch>`）。

**端到端集成测试**（opt-in，`SHANNON_RUN_ENSURE_DOCKER_INTEGRATION=1`）：
- 跑 `ensure-docker.sh` → buildx 可用（`docker buildx version` 退出 0）+ compose 走 BuildKit（最小 `RUN --mount` compose build 不报 "requires buildx plugin" / "requires BuildKit"）。**直接锁根因修复。**

**已验证（本机 WSL2）**：
- 纯函数单测 9 绿。
- 幂等：buildx 已在 → skip 不重下；dangling 清后重跑不触发。
- compose + `--mount` 走 BuildKit 成功（opt-in 集成测试 PASSED）。

## 7. 与现有工作的关系

- **依赖 A**（Dockerfile 去 `--mount`，已实现）：双保险。A 让构建不挑 builder，本 spec 让 BuildKit 正常工作。任一独立成立。
- 不动 `provision.sh::install_docker`（docker-ce 自带 buildx）。
- 不重建已拆除的 `--mount`。

## 8. 后续（非本次范围）

- `bootstrap.sh::check_docker` 升级为调用 ensure-docker（可选）。
- Mac 完整白盒 CLI provision（shannon-user + gitnexus/node/uv 宿主扫描，含 arm64 适配）——本次不做。
