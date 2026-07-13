# shannon-user 一键 provision 设计（换机器可重现部署）

- **日期**：2026-07-13
- **状态**：设计已确认，待写 plan
- **分支**：feat/fork-py
- **相关 memory**：`shannon-user-gitnexus-env-truth`、`pre-recon-gitnexus-blockage`、`gitnexus-1.6.7-real-machine-behavior`

---

## 1. 背景与动机

shannon-py 的白盒扫描可经两条路径运行：

- **WEB 页面扫描**：`shannon-py-web` 容器内 uvicorn 经 `create_subprocess_exec`（`scan_manager.py`）派生 `shannon-whitebox` 子进程，**不创建新容器**，继承容器 uid=root。决策（用户拍板）：**容器内不降权**，root 维持。
- **宿主 CLI 直跑** `uv run shannon-whitebox`：该用项目专属用户 `shannon-user`（用户拍板）。

`shannon-user` 在当前机器已存在（uid=1002，docker 组），但能跑扫描依赖一长串**这台机器长期手工调教出来的环境状态**。2026-07-13 排查发现：把这套环境搬到新机器，`shannon-user` 跑白盒会从多个点同时失败。

本 spec 设计一个**幂等、跨发行版的一键 provision 脚本**，让"换机器能用"从一堆散落在 memory 与手工操作里的步骤，变成 `bash scripts/provision.sh` 一把过。

## 2. 现状分析（换机器会挂的 8 项前提）

下表是 `shannon-user` 在新机器跑 `uv run shannon-whitebox` 必须满足的前提，及当前机器的状态来源：

| # | 前提 | 当前机器状态 | 状态来源 | 现有脚本覆盖 |
|---|------|-------------|---------|-------------|
| 1 | docker 已装（→ docker 组） | ✓ | 手工装 | ❌ |
| 2 | root 已装 uv | ✓ /root/.local/bin/uv | uv 官方安装器 | ⚠️ ensure 只拷贝不装 |
| 3 | 系统级 node（/usr/bin/node） | ✓ v22 | 手工系统级装 | ❌ |
| 4 | 系统级 gitnexus + ladybugdb binding | ✓ 1.6.8 | 手工 `--prefix=/usr` 装 | ⚠️ bootstrap 装到 root npm 全局，非系统级 |
| 5 | 项目目录 shannon-user 可达 | ⚠️ `/root` 是 777 | 非标准偶然 | ❌ 默认 /root=700，shannon-user 进不去 |
| 6 | 仓库 + .venv 归 shannon-user | ✓ | 历史演进 | ❌ root 全新 clone 会归 root |
| 7 | shannon-user 的 safe.directory | ✓ 手动设 `*` | 手工止血 | ❌ ensure 未加 |
| 8 | 建 shannon-user + docker 组 + 系统级 uv | ✓ | — | ✅ ensure-shannon-user.sh |

**8 项里现有脚本只覆盖第 8 项（部分）。** 1-7 全是手工/偶然状态。

## 3. 目标与非目标

### 目标
- `bash scripts/provision.sh`（root 跑）在**任意 Linux**（apt/dnf/yum）上幂等地把环境搭到"**就绪门槛**"：
  - `shannon-user` 能 `uv run shannon-whitebox`（端到端验证）
  - 用户能 `bash scripts/up.sh` 起 web
- 幂等：在已就绪的机器重跑无副作用、全 skip。
- 跨发行版：用官方跨发行版安装器消化大部分差异，最小包管理器分支。
- 复用现有 `ensure-shannon-user.sh` / `bootstrap.sh`，顺带把它们升级到"系统级、shannon-user 可用"。

### 非目标（明确不做）
- **不起服务**：不自动 `docker compose up`（留给 up.sh，职责分离）。
- **不碰密钥**：不填 `.env` / `.env.profiles` 的 GitLab token 与引擎密钥（用户机密，provision 只保证文件归属正确）。
- **不改容器内身份**：web 容器内维持 root（用户已定）。
- **不迁移现有 `/root/shannon-py`**：路径固定为 `/root/shannon-py`（用户已定），provision 处理 `/root` 权限而非改路径。
- **不负责 git clone shannon-py 本身**：provision 脚本在 `scripts/` 下，前提是代码已在目标机器。

## 4. 架构与组件职责（方案 C：编排现有 + 补全）

```
scripts/provision.sh                      # orchestrator（root 跑，幂等，任意 Linux）
  ├─ ensure-shannon-user.sh (已有/补)      # 用户 + docker 组 + 系统级 uv + safe.directory
  └─ provision 自包含 7 块（不调 bootstrap——其 install_gitnexus 装到 root npm 全局非系统级）：
       install_docker()         # get.docker.com 跨发行版装 docker + 启动 daemon
       install_node_system()    # NodeSource 装 node 22 到 /usr
       install_gitnexus_system()# npm --prefix=/usr 系统级 gitnexus + ladybugdb binding
       fix_root_acl()           # setfacl 让 shannon-user 穿越 /root
       fix_ownership()          # chown 仓库子树 + uv.lock/pyproject 给 shannon-user
       uv_sync_venv()           # 以 shannon-user 跑 uv sync
       verify()                 # 8 项自检 + 端到端 uv run --help
```

**职责边界**：
- `ensure-shannon-user.sh`：用户身份与 uv（已有；**补** safe.directory、**增强** uv 无源时用官方安装器）。
- `bootstrap.sh`：**（可选 follow-up）** `install_gitnexus` 升级系统级 `--prefix=/usr`——provision 自包含系统级 gitnexus、不依赖它，升级仅让 bootstrap 单独跑时也系统级（agent-browser/playwright/chromium 是黑盒宿主直跑的依赖，与本 provision 无关）。
- `provision.sh`：编排 ensure + 自包含 docker/node/gitnexus 系统级/ACL/归属/.venv/verify。

三块各自可独立跑，provision 串联。

## 5. 执行步骤序（每步幂等：已就绪则 skip）

1. **预检**：root 身份校验；发行版探测（`/etc/os-release` → `PKG=apt|dnf|yum`）；镜像参数化（`SHANNON_PYPI_INDEX` 默认 aliyun、`SHANNON_NPM_REGISTRY` 默认 npmmirror，env 可覆盖）；`set -euo pipefail`。
2. **docker**：`command -v docker || (curl -fsSL https://get.docker.com \| sh)`；启动 daemon——有 systemd 则 `systemctl enable --now docker`，WSL2 无 systemd 则 `service docker start`（失败仅 warn，不阻断——docker 可后置起）。
3. **shannon-user**：调 `ensure-shannon-user.sh`（useradd 不存在才建 + 补 docker 组）。
4. **系统级 uv**：调 ensure（增强后：优先拷 `/root/.local/bin/uv`，无则 `curl -LsSf https://astral.sh/uv/install.sh \| sh` 装到 root 再拷 `/usr/local/bin/uv`）。
5. **系统级 node**：`command -v /usr/bin/node || NodeSource`（apt: `setup_22.x`; dnf/yum: 对应 repo）装 node 22 到 /usr。
6. **系统级 gitnexus**：`command -v gitnexus || npm i -g --prefix=/usr --ignore-scripts gitnexus@${SHANNON_GITNEXUS_VERSION:-1.6.8}`；再跑 `node "$(npm root -g --prefix=/usr)/gitnexus/node_modules/@ladybugdb/core/install.js"`（**动态解析**系统级 npm 全局根，不写死路径）补 ladybugdb binding（失败 warn + 提示 `gitnexus doctor`）。
7. **/root ACL**：`setfacl -m u:shannon-user:x /root`（仅给 shannon-user 穿越权，不动 others；setfacl 不可用时 fallback `chmod o+x /root` 并 warn）。
8. **归属**：`chown -R shannon-user` repos/workspaces/configs/.venv **+** `chown` uv.lock/pyproject.toml/仓库根（uv sync 以 shannon-user 跑要写 lockfile，否则 `Permission denied`——集成测试实测捕获并修复）；`.env` / `.env.profiles` **保持 root:root**（密钥）。
9. **.venv**：`runuser -u shannon-user -- uv sync`（以 shannon-user 身份建/补 .venv，自然归它；走 `SHANNON_PYPI_INDEX`）。
10. **safe.directory**：`runuser -u shannon-user -- git config --global --add safe.directory '/root/shannon-py/repos/*'`（幂等：先 `--get-all` 查无再加）。
11. **verify**（硬门槛）：逐项自检 + 打印就绪报告；任一失败则 exit 1 并指出缺哪项。

## 6. 关键设计决策

| 决策 | 方案 | 理由 |
|------|------|------|
| `/root` 权限 | ACL `setfacl -m u:shannon-user:x /root`，fallback `chmod o+x` | 比当前 777 安全得多：只让 shannon-user 穿越，不暴露给其他用户 |
| 发行版抽象 | docker/uv/gitnexus 用官方跨发行版安装器；node 用 NodeSource；仅 git/ca-certificates/acl 按 apt/dnf 分支 | 避免维护多套包管理器逻辑，差异最小化 |
| 镜像 | `SHANNON_PYPI_INDEX`(默认 aliyun) / `SHANNON_NPM_REGISTRY`(默认 npmmirror)，env 覆盖 | 复用当前机器配置，又支持切官方源 |
| gitnexus 版本 | 固定 `1.6.8`，`SHANNON_GITNEXUS_VERSION` 可覆盖 | 与当前机器一致，memory 记录的稳定版 |
| 归属 | 仓库子树 + uv.lock/pyproject/仓库根 归 shannon-user；`.env`/`.env.profiles` 保持 root:root | uv sync 以 shannon-user 跑要写 lockfile（实测 Permission denied 已修）；密钥保持 root 拥有 |
| uv 安装 | 优先拷现有；无源则官方安装器 | 新机器 root 多半没 uv，ensure 必须能自举 |
| WSL2 docker | 检测无 systemd 时 `service docker start`，失败仅 warn | WSL2 默认无 systemd，provision 不应因 daemon 启动方式硬失败 |

## 7. 错误处理与幂等

- `set -euo pipefail`；每步失败即停，报"哪步失败 + 怎么手动修"。
- 每步幂等判定：`command -v X` / `[ -x path ]` / `id` / `getfacl` / `git config --get-all`，已就绪 skip。
- 关键步骤（docker、gitnexus、binding）失败时给出手动命令（参考 bootstrap.sh 现有提示风格）。
- **verify 是硬门槛**：不 green 不算成功，明确指出缺哪项（不静默通过）。

## 8. 测试策略

shell 脚本无 pytest，采用三层验证：

1. **当前机器幂等重跑**（最快回归）：`bash scripts/provision.sh` 应全 skip、verify green、不破坏现状。
2. **干净 Debian 容器验证**（最接近"换机器"）：`docker run --rm -it debian:12` → 拷 provision 进去 → 跑 → verify。
   - **注意**：容器内不装 docker（docker-in-docker 复杂且偏离真实）。容器验证聚焦**非 docker 部分**（用户/uv/node/gitnexus 系统级/ACL/归属/.venv/safe.directory/verify + `runuser uv run shannon-whitebox --help`）。docker 步骤靠第 1 层（本机已装 docker）+ 真机新机器验证。
   - 容器内跑 provision 时 docker 步骤（步骤 2）会因无 daemon 而 warn——属预期；提供 `SHANNON_SKIP_DOCKER=1` env 让容器验证跳过该步，聚焦验证其余部分。
3. **shellcheck** 静态检查 provision/ensure/bootstrap。

## 9. 涉及文件改动

| 文件 | 改动 |
|------|------|
| `scripts/provision.sh` | **新增** orchestrator + install_docker/install_node_system/**install_gitnexus_system**/fix_root_acl/fix_ownership/uv_sync_venv/verify |
| `scripts/ensure-shannon-user.sh` | **补** safe.directory 步骤；**增强** uv 无源时用官方安装器自举 |
| `scripts/bootstrap.sh` | **（可选 follow-up，本轮未做）** `install_gitnexus` 升级系统级 `--prefix=/usr`——provision 自包含系统级 gitnexus、不依赖它 |
| `docs/superpowers/specs/2026-07-13-shannon-user-provision-design.md` | 本 spec |

## 10. 风险与 follow-up

- **docker-in-docker 测试缺口**：第 8 节已说明容器验证不含 docker 步骤；真机新机器首次部署仍需人工确认 docker 部分正确。可在 plan 中加"真机冒烟"为人工 gate。
- **`get.docker.com` / NodeSource / astral.sh 依赖公网**：离线环境不适用（已在本 spec 非目标排除；离线 provision 是更大独立工作）。
- **registry.json 明文 GitLab token**（`glpat-3sb2kj8...`）：与本 provision 无关，但仍是最高优先级安全 follow-up——**须到 GitLab 后台轮换**。provision 不处理（不碰密钥）。
- **gitnexus_engine.py 吞 stderr**：dubious ownership 等失败真因被吞，provision 的 safe.directory 步骤预防了最常见的触发，但代码层"吞 stderr"问题独立存在，可另立 follow-up。
- **bootstrap `install_gitnexus` 系统级升级（可选）**：provision 自包含系统级 gitnexus 安装，故 bootstrap 该升级非必需，仅让 bootstrap 单独跑时也产出系统级 gitnexus。本轮未做。

## 11. 成功标准

`bash scripts/provision.sh` 在一台干净 Debian 机器上跑完后：
- verify 全 green（8 项自检 + `runuser -l shannon-user -c 'uv run shannon-whitebox --help'` exit 0）
- 用户 `bash scripts/up.sh` 能起 web 容器
- 在当前机器幂等重跑不破坏现状
