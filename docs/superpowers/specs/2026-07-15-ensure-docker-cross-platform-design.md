# 2026-07-15 跨平台 docker 环境健康（ensure-docker.sh）设计

## 1. 背景

本机（WSL2 + Docker Desktop WSL 集成）的 `docker compose` 是来路不明的 **v5.1.4**
（非官方 compose —— 官方才 v2.x；装在 `/usr/local/lib/docker/cli-plugins/docker-compose`，
非 Docker Desktop symlink）。它探测 buildx 失败（误报 `requires buildx`，尽管 buildx
v0.35.0 已装）→ 退回 classic/legacy builder → `worker` Dockerfile 的
`RUN --mount=type=cache` 报 `the --mount option requires BuildKit`，worker 镜像构建失败。

**已做的止血（A，已实现并验证）**：worker/web Dockerfile 去掉 `--mount=cache`，legacy
builder 也能构建 —— 跨 builder 兼容，不依赖 BuildKit。

**本 spec 治根因**：让 `docker compose` 走官方 v2.x（驱动 BuildKit），消除 v5.1.4 /
legacy 退化 / warn，并让 **Linux / WSL2 Ubuntu / Mac 三平台** docker 环境 health 一致。

## 2. 目标 / 非目标

**目标**
- 三平台（Linux / WSL2 Ubuntu / Mac）跑 `up.sh` 前，确保 `docker compose` 是官方 v2.x、
  `buildx` 就位 → 走 BuildKit，干净 build + 起容器。
- runtime（Docker Desktop / docker engine）**只检测不装**（用户自己装）。
- 一份 bash 脚本，三平台同逻辑，非 root 可跑。

**非目标**
- 不装 docker runtime（Mac 的 Docker Desktop / Linux 的 docker-ce 由用户或现有
  `provision.sh` 装）。
- 不做 shannon-user 完整白盒 CLI provision 的三平台化（Mac 跑扫描有 arm64 架构坑，
  本次明确不做，列后续）。
- 不改 Dockerfile（A 已让构建不依赖 BuildKit；本 spec 是让 BuildKit 正常工作，
  与 A 构成双保险）。

## 3. 方案：`scripts/ensure-docker.sh`

单一职责小脚本（~60 行 bash），三平台同逻辑：

```
1. 检测 runtime：command -v docker；缺失 → 报错 + 三平台指引
   （Linux: get.docker.com / Mac: Docker Desktop）+ exit 1。
2. 平台/架构探测：uname -s → linux|darwin；uname -m → amd64(x86_64)/arm64(aarch64,arm64)。
3. 修 compose：docker compose version --short 主版本非 2.*（或 compose 不可用）→
     下官方 release docker-compose-<plat>-<arch> → ~/.docker/cli-plugins/docker-compose（chmod +x）。
   已是官方 v2.x（含 Docker Desktop 自带的）→ skip。
4. 确保 buildx：docker buildx version 不可用 →
     下官方 buildx release → ~/.docker/cli-plugins/docker-buildx。
   已有（如本机 v0.35.0）→ skip。
5. 验证：docker compose version（应 v2.x）+ docker buildx version，输出 OK。
```

**下载源 / 版本**
- compose: `https://github.com/docker/compose/releases/download/v$COMPOSE_VER/docker-compose-$PLAT-$ARCH`
- buildx: `https://github.com/docker/buildx/releases/download/v$BUILDX_VER/buildx-v$BUILDX_VER.$PLAT-$ARCH`
- 版本固定（可复现），env 可覆盖：`SHANNON_COMPOSE_VERSION` / `SHANNON_BUILDX_VERSION`。
- 中国网络：env `SHANNON_GH_MIRROR`（如 `https://ghproxy.com/`）可选 URL 前缀，默认直连。
  （YAGNI：默认直连，实测慢再加镜像。）

**为何放 `~/.docker/cli-plugins/`**
- 用户级，docker CLI 搜索路径优先于 `/usr/local/lib/docker/cli-plugins/`（v5.1.4 所在）→
  覆盖生效，不动系统文件，可逆。
- 三平台路径一致（Linux/Mac 都是 `~/.docker/cli-plugins/`）。
- 非 root 可写（Mac / 普通用户都能跑，区别于需 root 的 provision.sh）。

## 4. 集成（复用关系）

| 脚本 | 集成 | 说明 |
|---|---|---|
| **`up.sh`** | **开头调用 `ensure-docker.sh`**（仅 `ACTION=up`） | 核心集成点：覆盖三平台所有 up.sh 用户（含 Docker Desktop 场景），无感确保环境；ensure-docker 失败（如 runtime 缺失）→ up.sh 经 `set -e` 中止，不带着坏环境硬 build |
| `provision.sh` | **不集成** | provision 装 docker-ce（get.docker.com）**自带官方 v2.x compose + buildx**，无需修；若未来 provision 支持「复用已有 runtime」再考虑调用 |
| `bootstrap.sh` | 可选：`check_docker` 升级为调用 `ensure-docker` | 锦上添花，非必须（本次可不做，列后续） |
| 手动 | `bash scripts/ensure-docker.sh` | 单独修环境，不跑 up.sh |

## 5. 关键技术点 / 风险（实现时需核实）

1. **cli-plugins 搜索路径优先级**：`~/.docker/cli-plugins/` 是否真的优先于
   `/usr/local/lib/docker/cli-plugins/`（v5.1.4 所在）。实现时验证：放完后
   `docker compose version` 切到 v2.x；若优先级不符，备选直接覆盖 `/usr/local/lib/`
   下的文件（需 root、动系统文件，作为 fallback）。
2. **compose/buildx 最新稳定版本号**：实现时取当前 stable（compose v2.x、buildx v0.x），
   固定写入默认值，env 可覆盖。
3. **版本判定边界**：`docker compose version --short` 在 v5.1.4 下输出什么需实测
   （可能 `5.1.4` 或别的内容）；部分老 compose 不支持 `--short` 标志。判定逻辑：
   `--short` 成功取其输出、失败则 fallback 到 `docker compose version` 完整输出，
   提取主版本号，**主版本 != 2 → 重装**；命令整体失败（compose 不可用）→ 重装。
4. **Mac bash 版本**：macOS 自带 bash 3.2，脚本避免 bash 4+ 特性（`mapfile`、关联数组等），
   用 POSIX 友好写法。
5. **幂等**：已是官方 v2.x / buildx 已在 → 全部 skip，重复跑无副作用、无重复下载。

## 6. 测试

**纯函数单测**（source 后单测，仿 `provision.sh::detect_pkg_mgr` 的可测模式）：
- `_plat_for_uname_s`：Linux→linux、Darwin→darwin、其他→""。
- `_arch_for_uname_m`：x86_64→amd64、aarch64/arm64→arm64、其他→""。
- `_compose_is_official_v2 <ver>`：版本字符串是否官方 v2.x（`2.*` → 是）。

**端到端**：
- 本机 WSL2：跑 `ensure-docker.sh` → v5.1.4 被官方 v2.x 覆盖 → `docker compose version`
  显 v2.x → `up.sh` 走 BuildKit（无 warn、无 `Step N/M` legacy 输出）。
- Linux（docker-ce）/ Mac（Docker Desktop）：compose 已 v2.x → skip，不破坏现有好环境。

## 7. 与现有工作的关系
- **依赖 A**（Dockerfile 去 `--mount`，已实现）：双保险 —— A 让构建不挑 builder，
  本 spec 让 BuildKit 正常工作。任一独立成立。
- 不动 `provision.sh::install_docker`（它装 engine，docker-ce 自带 v2.x compose）。
- 不重建已拆除的 `--mount`（A 的铁律不变；ensure-docker 是环境侧，Dockerfile 侧保持去 --mount）。

## 8. 后续（非本次范围）
- `bootstrap.sh::check_docker` 升级为调用 ensure-docker（可选）。
- Mac 上完整白盒 CLI provision（shannon-user + gitnexus/node/uv 宿主扫描，含 arm64 适配）——
  本次明确不做。
