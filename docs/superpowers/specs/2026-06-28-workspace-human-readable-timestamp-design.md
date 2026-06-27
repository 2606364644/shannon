# 2026-06-28 · workspace 目录名时间戳 → 人类可读日期时间（设计）

## 背景与动机

`workspaces/` 下的 workspace 目录名形如 `NodeGoat_shannon-1782041072350`，其中 `1782041072350` 是毫秒级 Unix epoch 时间戳，人眼无法直接读出具体日期时间。本设计把目录名里的时间部分改成人类可读的日期时间，方便在文件管理器 / 终端里一眼识别。

## 目标 / 非目标

**目标**

- 新建的 workspace 目录名用本地时区、人类可读的紧凑秒级日期时间。
- 同秒同名创建有兜底，绝不串数据（错误复用别人的目录）。

**非目标**

- 不迁移已有老目录（保留原样，用户自行清理或保留）。
- 不改 `session.json` 内的 `created_at` / `completed_at`（程序消费的 `time.time()` 浮点秒）。
- 不改 Temporal workflow id 里的 `whitebox-{epoch}` / `blackbox-{loop.time()}`（不在 "workspaces 目录" 范围；它们以 `workspace_name` 作前缀，会顺带变可读，属附带收益，不单独改代码）。

## 现状分析

**目录名时间戳的唯一来源**：`packages/core/src/shannon_core/session.py:23` 的 `SessionManager.create_workspace`，`name=None` 默认分支：

```python
session_id = f"shannon-{int(time.time() * 1000)}"
name = f"{hostname}_{session_id}"
```

whitebox / blackbox / multi 在用户不传 `-w`（`workspace_name`）时都走 core 这个默认分支：

- `packages/whitebox/src/shannon_whitebox/worker.py:81` 传 `name=input.workspace_name`（无 `-w` 时为 `None`）
- `packages/blackbox/src/shannon_blackbox/worker.py:61` 传 `name=None`
- `packages/multi/src/shannon_multi/orchestrator.py:138` 未传 `name`

**无任何代码从目录名反解析时间戳**：`list_workspaces` 按 `st_mtime` 排序；`get_created_at` 从 `session.json` 读。目录名里的时间戳不承载程序语义，仅起唯一标识 + 粗略时间感知作用 → 改格式安全。

**hostname 来源**：`web_url` 的 host（去协议、`.`→`-`），或 `repo_path` 的目录名。

## 设计

### 1. 改动点（唯一代码改动）

`session.py` 的 `create_workspace`：把内联的 `session_id` 生成抽成私有方法 `_default_workspace_name(hostname)`，用本地时区紧凑秒级日期时间，带碰撞兜底。

```python
from datetime import datetime

def _default_workspace_name(self, hostname: str) -> str:
    """生成默认 workspace 名：<hostname>_YYYYMMDD-HHMMSS，同名碰撞追加序号。"""
    base = f"{hostname}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    name = base
    i = 2
    while (self.workspaces_dir / name / "session.json").exists():
        name = f"{base}-{i}"
        i += 1
    return name
```

`create_workspace` 内：

```python
if not name:
    if web_url:
        hostname = (
            web_url.replace("https://", "").replace("http://", "")
            .split("/")[0].replace(".", "-")
        ) or "repo"
    else:
        hostname = Path(repo_path).name.replace(".", "-") or "repo"
    name = self._default_workspace_name(hostname)   # 原: f"{hostname}_{session_id}"
```

### 2. 新目录名格式

- 格式：`<hostname>_YYYYMMDD-HHMMSS`
- 例：`NodeGoat_20260619-143000`、`juice-shop_20260619-143000`、`192-168-100-106_20260619-143000`
- 时区：`datetime.now()` = **本地时区**，符合"人类可读"。
- 无冒号 → 跨平台 / Shell 安全；字典序天然即时间序。
- 去掉原 `shannon-` 前缀（hostname 已标识仓库，前缀无语义）。

### 3. 唯一性兜底（方案 A：秒级 + 同名追加序号）★

原 `int(time.time()*1000)` 毫秒戳天然唯一；改秒级后，**同秒创建两个同名 workspace 会撞目录**（`mkdir(exist_ok=True)` + `session.json` 幂等检查会错误复用别人的目录、串数据）。

兜底：生成 `base` 后，若目录已存在且含 `session.json`，追加 `-2`/`-3`… 重试。绝大多数情况目录名干净，碰撞极罕见（人不会 1 秒内手建两个）才出后缀。

**必须区分两种"`session.json` 已存在"的情况**（关键正确性约束）：

| 情况 | 触发 | 应有行为 |
|---|---|---|
| (a) resume | 用户**显式**传 `name`（`-w` resume），目录是自己的旧 session | 复用：幂等 `return ws`（保持既有逻辑不变） |
| (b) 碰撞 | **自动**生成名恰好撞到别人 | 追加序号 `_default_workspace_name` 循环 |

区分依据 = 是否在"自动生成 name"分支：显式 `name` 不进兜底循环，仍走 `create_workspace` 既有 `if (ws/"session.json").exists(): return ws`；自动 `name` 才进 `_default_workspace_name` 的 while 循环。两者互不干扰。

### 4. 影响面

- `get_scan_type` 的 fallback（从目录名 lowercase 找 `"blackbox"`）：新格式不含该词，但 fallback 仅在 `session.json` 缺 `scan_type` 时触发，新建均有 → **不受影响**（加注释说明）。
- Temporal workflow id：whitebox `resolve_workflow_id`（`worker.py:63`）用 `workspace_name` 作前缀，新 `workspace_name` 可读 → workflow id 顺带可读，附带收益，不改代码。
- `list_workspaces` 按 `st_mtime` 排序，不受目录名格式影响；新老目录混存时仍按修改时间正常排序。

### 5. 测试锚点（TDD）

- `test_workspace_name_human_readable_format`：新名匹配 `^.+_\d{8}-\d{6}$`，且时间 ≈ `now`。
- `test_workspace_name_collision_appends_suffix`：同秒同名二次创建得 `...-2`，不覆盖既有 `session.json`。
- `test_explicit_name_keeps_idempotent_return`：显式 `name` + 已有 `session.json` → 仍幂等 `return ws`，不追加序号（保护 resume 语义）。
- `test_legacy_timestamp_dirs_still_listable`：老格式目录（如 `NodeGoat_shannon-1782041072350`）仍能 `list_workspaces` / `get_session_data`（回归保护）。

## 风险

- **碰撞兜底漏判"显式 vs 自动 name"** → 破坏 resume 幂等。由 `test_explicit_name_keeps_idempotent_return` 锁定。
- **时区**：`datetime.now()` = 服务器本地时区；跨时区部署时目录名时间跟随服务器。符合"人类可读"默认预期，可接受。
- **新老目录混存**：仅视觉差异，`list_workspaces` 按 mtime 排序正常，无功能影响。

## 开放问题

无。目录名格式（紧凑秒级）、范围（只改新创建、不迁移老目录）、唯一性兜底（方案 A 序号）均已确定。
