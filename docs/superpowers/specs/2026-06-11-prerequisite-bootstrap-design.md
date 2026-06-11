# Prerequisite Bootstrap Design

**Date:** 2026-06-11
**Status:** Draft
**Scope:** `scripts/bootstrap.sh` + `start` 内嵌自检提示 + `ensure_prerequisite` 辅助函数

---

## 问题

`shannon-whitebox start` 在 gitnexus 未安装时**静默降级**到 minimal AST-only 模式，仅打一行 warning 就继续跑，用户无法察觉扫描质量受损。同理，`shannon-blackbox start` 缺 playwright-cli 时在运行时才暴露。缺少一个统一的前置依赖检测与安装机制。

## 目标

1. `start` 启动前**自检必需的外部依赖**，缺失时交互提示安装（`[Y/n]`），确认后自动安装，装完重检继续。
2. 提供独立可运行的 `scripts/bootstrap.sh`，按 profile 批量装齐所有外部依赖。
3. 用户**拒绝安装**时，大声警告降级后果并二次确认（默认退出），不再静默降级。
4. 所有安装命令**集中在脚本里**，`start` 只调用脚本，不重复写安装逻辑。

## 范围

### 覆盖的依赖

| 依赖 | Profile | 检测方式 | 安装命令 |
|------|---------|---------|---------|
| **node / npm** | 共用前置 | `command -v npm` | ❌ 不替装，缺则报错 + 给指引 |
| **pnpm** | 共用前置 | `command -v pnpm` | `npm install -g pnpm` |
| **gitnexus** | whitebox | `command -v gitnexus` | `pnpm add -g gitnexus@latest`（配 build 审批） |
| **playwright-cli** | blackbox | `command -v playwright-cli` | 待确认 npm 包名（实现时核实） |
| **chromium 浏览器** | blackbox | 探测 playwright 浏览器目录 | `npx playwright install chromium` |
| **docker** | all | `command -v docker` | ❌ 不替装，缺则提示跑 `infra up` |

### 不覆盖

- Windows 原生（仅 WSL）
- Python / uv 本身（已有 `uv sync`）
- Temporal 服务本身（已有 `ensure_infra` + `infra up` 子命令覆盖）

---

## 架构

### 1. `scripts/bootstrap.sh`（独立安装脚本）

**调用方式：**
```bash
bash scripts/bootstrap.sh [whitebox|blackbox|all] [--yes]
```

- 默认 profile = `all`。
- `--yes`：跳过所有交互确认，直接装（供 `start` 调用时使用）。
- 不带 `--yes`：逐项交互确认 `[Y/n]`。

**行为流程：**

```
1. 检查 node/npm → 缺则报错退出 + 打印安装指引
2. 检查 pnpm → 缺则安装（npm install -g pnpm）
3. 按 profile 逐项检测：
   whitebox: gitnexus
   blackbox: playwright-cli, chromium
4. 缺失项 → 交互确认（--yes 跳过）→ 执行安装 → 流式打印输出
5. 全部装完 → 逐项重检 → 打印汇总表：
   ✅ gitnexus 1.6.7
   ✅ playwright-cli ...
   ❌ docker (not installed, run `infra up`)
6. 如有安装失败项 → 退出码 1 + 打印手动命令兜底
```

**gitnexus 安装细节：**

- 使用 pnpm 全局安装：`pnpm add -g gitnexus@latest`
- pnpm 10+ 默认限制 build scripts，需审批 `@ladybugdb/core`、`gitnexus`、`tree-sitter` 的 build 脚本：
  ```bash
  pnpm config set --global onlyBuiltDependencies "@ladybugdb/core" "gitnexus" "tree-sitter" 2>/dev/null || true
  pnpm add -g gitnexus@latest
  ```
- 安装后 `gitnexus` 二进制在 pnpm 全局 bin 目录（`~/.local/share/pnpm`），需确保该目录在 PATH。
- 如果 pnpm 全局 bin 不在 PATH，脚本尝试 `pnpm setup`（自动追加到 shell profile）。
- 可选：`GITNEXUS_SKIP_OPTIONAL_GRAMMARS=1` 跳过 Dart/Proto/Swift/Kotlin 语法（免 C++ 工具链）。

**playwright-cli 安装细节：**

- 精确 npm 包名实现时确认（当前代码用 `shutil.which("playwright-cli")` 检测）。
- 浏览器二进制：`npx playwright install chromium`（仅 chromium，PlaywrightEngine 硬编码 `"browserName": "chromium"`）。

**幂等性：** 已安装的依赖跳过（通过 `command -v` 检测）。

**脚本头：** `#!/usr/bin/env bash` + `set -euo pipefail`。

### 2. `ensure_prerequisite` 辅助函数

**位置：** `packages/core/src/shannon_core/runtime/prerequisites.py`（新文件）

```python
def ensure_prerequisite(name: str, profile: str) -> None:
    """Check a prerequisite binary; prompt to install via bootstrap.sh if missing.

    Raises SystemExit if user declines and does not confirm degraded run.
    """
```

**逻辑（≤50 行）：**

1. `shutil.which(name)` → 如果存在，直接返回。
2. `click.confirm(f"检测到 {name} 未安装。现在自动安装？ [Y/n]", default=True)`
3. No → 走降级确认路径（§4）。
4. Yes → 定位 `scripts/bootstrap.sh`（通过 `Path(__file__)` 上溯到 repo root，或 `SHANNON_BOOTSTRAP_SCRIPT` env 覆盖）。
5. `subprocess.run(["bash", str(script_path), profile, "--yes"], check=False)`
6. 重检 `shutil.which(name)`：
   - 仍缺 → 打印安装失败 + 手动命令兜底 → 走降级确认路径。
   - 已装 → 返回。

**脚本路径定位策略：**

- 默认：从 `prerequisites.py` 所在路径上溯 `../../../../../scripts/bootstrap.sh`（core 包 → packages/ → repo root）。
- 覆盖：`SHANNON_BOOTSTRAP_SCRIPT` 环境变量指定绝对路径。
- 找不到脚本 → warning + 降级继续（不因找不到安装脚本而阻断扫描）。

### 3. `start` 集成点

#### whitebox `start`

**文件：** `packages/whitebox/src/shannon_whitebox/cli/main.py`

**位置：** 在 `asyncio.run(ensure_infra(...))` 之后、`asyncio.run(run_scan(...))` 之前插入：

```python
from shannon_core.runtime.prerequisites import ensure_prerequisite
ensure_prerequisite("gitnexus", profile="whitebox")
```

#### blackbox `start`

**文件：** `packages/blackbox/src/shannon_blackbox/cli/main.py`

**位置：** 同样在 infra 确保之后、扫描之前：

```python
from shannon_core.runtime.prerequisites import ensure_prerequisite
ensure_prerequisite("playwright-cli", profile="blackbox")
```

（chromium 浏览器的检测与安装由 `bootstrap.sh --profile blackbox` 内部处理。）

### 4. 降级行为（拒绝安装 / 安装失败）

当用户拒绝安装或安装失败后：

```python
click.secho(
    f"⚠️  {name} 未安装。扫描将以降级模式运行，结果质量会显著下降。",
    fg="yellow", bold=True,
)
if not click.confirm("仍要继续运行（降级模式）？", default=False):
    raise SystemExit(1)
```

- **默认 No**（`default=False`）：不输入直接回车 = 退出。
- 这修掉了当前的"静默降级"根因：用户必须**显式确认**接受降级。

### 5. 错误处理

| 场景 | 行为 |
|------|------|
| node/npm 缺失 | 脚本打印 "Node.js/npm required. Install from nodejs.org" 并退出 (rc=1) |
| pnpm 安装失败 | 脚本打印 stderr + 退出；`ensure_prerequisite` 走降级确认 |
| gitnexus 安装失败 | 同上 + 打印手动命令 `pnpm add -g gitnexus@latest` |
| playwright-cli 安装失败 | 同上 |
| chromium 下载失败 | 同上 + 打印 `npx playwright install chromium` |
| docker 缺失 | 脚本标记 ❌ 并提示 `shannon-whitebox infra up`；`start` 仍走现有 `ensure_infra` 处理 |
| bootstrap.sh 找不到 | `ensure_prerequisite` 打 warning、降级继续（不阻断） |

### 6. 测试

**`ensure_prerequisite` 测试**（`packages/core/tests/test_prerequisites.py`）：

- `test_already_installed`：`shutil.which` mock 返回路径 → 直接通过，不弹提示。
- `test_user_confirms_install`：mock `click.confirm` → Yes → mock `subprocess.run` → `shutil.which` 重检通过 → 正常返回。
- `test_user_declines_install_exit`：mock `click.confirm` → No → 降级确认 → No → `SystemExit(1)`。
- `test_user_declines_accepts_degraded`：mock `click.confirm` → No → 降级确认 → Yes → 正常返回（降级）。
- `test_install_fails_fallback`：subprocess 非零 → 重检失败 → 降级确认。

**`bootstrap.sh` 测试：**

- 手动验证为主（脚本测试在 CI 中走 `--dry-run` 可选扩展，当前 YAGNI）。
- `ensure_prerequisite` 的 mock 测试覆盖脚本调用路径。

### 7. 平台

- **Linux + macOS**：bash 脚本，`set -euo pipefail`。
- **Windows**：仅 WSL（用户当前环境即 WSL2）。
- pnpm 全局 bin 目录在不同 OS 有差异，脚本中做 `pnpm setup` 兜底确保 PATH。

---

## 文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `scripts/bootstrap.sh` | 新建 | 独立安装脚本 |
| `packages/core/src/shannon_core/runtime/__init__.py` | 新建 | 包 init |
| `packages/core/src/shannon_core/runtime/prerequisites.py` | 新建 | `ensure_prerequisite` 辅助函数 |
| `packages/core/tests/test_prerequisites.py` | 新建 | 辅助函数测试 |
| `packages/whitebox/src/shannon_whitebox/cli/main.py` | 修改 | `start` 中加 `ensure_prerequisite("gitnexus", ...)` |
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | 修改 | `start` 中加 `ensure_prerequisite("playwright-cli", ...)` |

## 实现时需核实项

1. **pnpm `add -g` 的 build 审批机制**：pnpm 10+ 的 `onlyBuiltDependencies` 配置或 `--allow-build` flag 在 `add -g` 下是否生效，需在实现时验证具体 pnpm 版本行为。
2. **playwright-cli 的 npm 包名**：代码中用 `shutil.which("playwright-cli")`，需确认对应的全局 npm 包（可能是 `@executeautomation/playwright-cli` 或其他）。
3. **pnpm 全局 bin 是否自动进 PATH**：不同安装方式（npm 全局装 pnpm vs corepack）的 PATH 配置可能不同，需测试 `pnpm setup` 的兜底效果。
