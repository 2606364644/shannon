# Web 平台部署与运维脚本

supernova 的 Web 平台（`packages/web`）是**单容器部署**：后端 FastAPI 在 `:7878` 同时 serve 前端静态产物与 API（同源无 CORS），compose 另起一个 `temporal` 服务（workflow 引擎）。本文记录两个配套运维脚本——`scripts/up.sh`（启动）与 `scripts/cleanup-supernova.sh`（清理残留）。

> 速查与背景见根目录 [README.md](../README.md) 的「Web 平台」章节；单容器设计见 [设计 spec](superpowers/specs/2026-07-03-web-single-container-deploy-design.md)。

---

## 速查

```bash
./scripts/up.sh                     # 启动 web（自动判断 temporal 复用 / 自建）
./scripts/up.sh down                # 停掉 web（不影响外部 temporal）

bash scripts/cleanup-supernova.sh --dry-run   # 预览将要清理的内容
bash scripts/cleanup-supernova.sh             # 清理残留（stop 容器 + 杀前端/宿主后端进程）
bash scripts/cleanup-supernova.sh --rm        # 连容器实例一起删
```

---

## scripts/up.sh —— 一键启动 web

自动判断本机 `7233`（Temporal）是被占还是空闲：被占则复用外部 temporal（不抢端口），空闲则自建。默认 action 等价于 `docker compose up -d --build web`。

### 用法

```bash
./scripts/up.sh              # 启动（自动判断复用 / 自建）
./scripts/up.sh down         # 停掉
./scripts/up.sh logs web     # 看日志
./scripts/up.sh logs -f web  # 实时跟踪日志
```

第一个参数是 `docker compose` 子命令（`up` / `down` / `logs` / `ps` / `restart` …），默认 `up`；**其余参数透传给 `docker compose`**。

### 自动判断逻辑

| 宿主 `7233` | 模式 | 实际命令 |
|---|---|---|
| 已被占用 | **复用外部 temporal** | `docker compose -f docker-compose.yml -f docker-compose.override.external-temporal.yml up -d --build web` |
| 空闲 | **自建 temporal**（主 compose 默认） | `docker compose up -d --build web` |

- 复用模式下，override 用 `profiles: ["disabled"]` 隐藏 compose 自带的 `temporal`，`web` 改直连外部 temporal（经 `shannon-net` 网络用容器名解析，绕开 `127.0.0.1` 容器够不到的限制）。**前提**：外部 temporal 容器名为 `shannon-temporal` 且存在 `shannon-net` 网络，两处不同就改 override 文件。
- 不依赖本地 `docker-compose.override.yml`（那个被 `.gitignore` 排除、且会干扰自动判断）。

### 启动前自动清理空壳容器

若曾直接跑 `docker compose up` 失败、留下 `supernova-temporal` / `supernova-web` 的 `Created` 态空壳，这些空壳会在后续模式切换 / 端口检测 / 启动时捣乱。`up.sh` 在启动前会自动清掉本项目内的非运行态容器。

**安全保证（双重，绝不误删外部 `shannon-temporal`）：**

1. `docker compose ps` 只列本项目（`supernova`）管辖的容器，物理上排除外部 `shannon-temporal`；
2. 只删 state ∈ `{created, exited, dead}`，`running` / `restarting` / `paused` 一律保留。

### 排障

- **`Bind for 127.0.0.1:7233 failed: port is already allocated`**：说明已有独立 temporal 容器在跑（常见于多项目共用一台机器）。`up.sh` 会自动检测并切到复用模式；若仍报错，确认外部 temporal 容器名与网络是否符合上方前提。
- **检测到本地 `docker-compose.override.yml`**：脚本会打印警告——本地 override 会被 `.gitignore` 排除且干扰自动判断，建议 `rm docker-compose.override.yml` 后重试（脚本仍会继续，但模式判断可能不准）。

---

## scripts/cleanup-supernova.sh —— 清理运行残留

清理「重构项目 supernova」的运行残留（前端进程、宿主直跑的后端、Docker 容器），**可重复执行**。与 `up.sh down` 的区别：`down` 只停 compose 服务，本脚本还额外杀掉**宿主直跑**（非容器）的前端 vite/esbuild 与后端进程——开发模式（前后端分离跑）遗留的进程只有本脚本清得掉。

### 用法

```bash
bash scripts/cleanup-supernova.sh [选项]
```

| 选项 | 说明 |
|---|---|
| `-n`, `--dry-run` | 只打印将要清理的内容，不实际执行 |
| `--rm` | 删除容器实例（默认仅 `docker stop`，保留实例方便重启） |
| `-h`, `--help` | 显示帮助 |

### 清理范围（4 步）

1. **前端进程** vite / esbuild —— 路径锁死 `$REPO/packages/web/frontend`（`node_modules/.bin/vite`、`node_modules/@esbuild`）。
2. **宿主直跑的 `supernova_web` 后端**（非容器）—— 匹配 `supernova_web\.app:app`。容器内的后端不受影响。
3. **Docker 容器** —— 按 `label=com.docker.compose.project=supernova` 精确过滤，默认 `docker stop`，加 `--rm` 则 `docker rm -f`。
4. **验证** —— 检查是否还有 `supernova_web` / `supernova/packages/web` 进程残留、端口 `7878` / `5173` 是否仍在监听，并打印原始 TS `/root/shannon` 的运行进程数（**本脚本未触碰，保持原样**）。

### 安全铁律

> **本脚本绝不触碰 `/root/shannon`（原始 TS 项目）的任何进程，也绝不触碰 gitnexus 等共享组件。**

- 所有进程匹配一律用**脚本所在仓库解析出的绝对路径** / `supernova_web`，不会误伤 TS 的 `node ./shannon` / `runner.js` / claude-agent-sdk 子进程；仓库移动目录无需修改清理脚本。
- 容器按 compose `project=supernova` 精确过滤，只动本项目的。

### 注意事项

- **请用 `bash <file>` 或 `./<file>` 执行**，不要 `bash -c "$(cat <file>)"`——后者会让脚本字面量出现在 shell cmdline 里，被 `pgrep` 自匹配。脚本内置双保险（排除自身 PID 与父 PID），但正确执行方式更稳妥。
- **杀进程建议以 root 执行**——容器 + 跨用户进程才都清得掉。

---

## 典型工作流

```bash
# 1. 启动（首次或日常）
./scripts/up.sh

# 2. 停止（保留容器实例，方便下次快速重启）
./scripts/up.sh down

# 3. 彻底清理（开发模式遗留进程 / 想要干净状态）
bash scripts/cleanup-supernova.sh --dry-run   # 先预览
bash scripts/cleanup-supernova.sh --rm        # 再实清（连容器实例一起删）
```

> 生产单容器与开发分离模式共用同一份后端代码：后端 serve 静态由 `SUPERNOVA_WEB_FRONTEND_DIR` 控制，开发时不设此变量即跳过。
