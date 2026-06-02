# 白盒-黑盒扫描衔接 Bug 修复设计

> 日期：2026-06-02
> 状态：待实施
> 范围：方案 A（最小修复）

## 背景

shannon-py 支持单独执行白盒扫描（`shannon-whitebox start --repo X`）和黑盒扫描（`shannon-blackbox start --url Y --repo X`）。用户先跑白盒、再跑黑盒时，黑盒需要复用白盒产出的 `exploitation_queue.json` 文件来跳过侦察阶段、直接进入漏洞利用。

代码分析发现白盒和黑盒之间的衔接存在 14 个问题，其中 4 个属于致命/严重级别，会导致黑盒找不到白盒结果或扫描产出不完整。

## 修复项

### 修复 1：统一 deliverables 路径解析函数

**问题**：白盒和黑盒中存在 3 处独立的 deliverables 路径解析逻辑，均使用相对路径 `Path("workspaces")`，解析结果依赖 Temporal worker 的 CWD。如果 CWD 不是项目根目录，三处解析会指向不同位置。

涉及文件：
- `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` 第 76-89 行
- `packages/blackbox/src/shannon_blackbox/pipeline/activities.py` 第 15-19 行
- `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` 第 18-22 行
- `packages/whitebox/src/shannon_whitebox/worker.py` 第 20-21 行

**修复方案**：

在 `shannon_core/utils/paths.py` 新增共享路径解析函数：

```python
from pathlib import Path
import json

def resolve_workspaces_dir(repo_path: str | None = None) -> Path:
    """解析 workspaces 根目录。
    
    如果提供 repo_path，使用 repo_path.parent / "workspaces"；
    否则使用项目根目录下的 "workspaces"（基于当前文件位置推断）。
    """
    if repo_path:
        return Path(repo_path).parent / "workspaces"
    # 项目根目录下的 workspaces
    return Path("workspaces")


def resolve_deliverables_path(
    repo_path: str | None,
    deliverables_subdir: str,
    workspace_name: str | None = None,
    workspaces_root: Path | None = None,
) -> Path:
    """统一的 deliverables 路径解析。
    
    优先级：
    1. repo_path 存在 → repo_path / deliverables_subdir
    2. workspace_name 存在 → 从 session.json 恢复 repo_path → repo_path / deliverables_subdir
    3. fallback → workspaces_root / workspace_name / deliverables_subdir
    """
    if repo_path:
        return Path(repo_path) / deliverables_subdir

    if workspace_name:
        ws_root = workspaces_root or resolve_workspaces_dir()
        session_file = ws_root / workspace_name / "session.json"
        if session_file.exists():
            session_data = json.loads(session_file.read_text())
            saved_repo = session_data.get("repo_path")
            if saved_repo:
                return Path(saved_repo) / deliverables_subdir
        # fallback
        return ws_root / workspace_name / deliverables_subdir

    raise ValueError("必须提供 repo_path 或 workspace_name 之一")
```

**变更文件**：
- 新增：`packages/core/src/shannon_core/utils/paths.py`
- 修改：`packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` — 用 `resolve_deliverables_path()` 替换内联路径逻辑
- 修改：`packages/blackbox/src/shannon_blackbox/pipeline/activities.py` — 用共享函数替换 `_get_deliverables_path()`
- 修改：`packages/whitebox/src/shannon_whitebox/pipeline/activities.py` — 用共享函数替换 `_get_paths()`
- 修改：`packages/whitebox/src/shannon_whitebox/worker.py` — 用 `resolve_workspaces_dir()` 替换 `Path(input.repo_path).parent / "workspaces"`

---

### 修复 2：统一白盒结果检测标准

**问题**：
- `blackbox/workflows.py` 第 94-97 行：仅检查 `queue_file.exists()` 判断是否有白盒结果
- `exploitation_checker.py` 第 17-42 行：检查文件存在 **且** `vulnerabilities` 列表非空

队列文件存在但内容为空时，黑盒会跳过 `RECON_BLACKBOX`，但 `ExploitationChecker` 也不执行 exploit，导致产出不完整。

涉及文件：
- `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` 第 94-97 行
- `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py` 第 17-42 行

**修复方案**：

在 `shannon_core/utils/paths.py` 中新增共享检测函数：

```python
def has_valid_whitebox_results(queue_file: Path) -> bool:
    """检查 exploitation queue 文件是否包含有效漏洞条目。"""
    if not queue_file.exists():
        return False
    try:
        data = json.loads(queue_file.read_text())
        return bool(data.get("vulnerabilities"))
    except (json.JSONDecodeError, KeyError, OSError):
        return False
```

- `workflows.py` 中的 `queue_file.exists()` 替换为 `has_valid_whitebox_results(queue_file)`
- `exploitation_checker.py` 中的检测逻辑替换为调用同一函数

**变更文件**：
- 修改：`packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`
- 修改：`packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py`

---

### 修复 3：修复 workspace_path 语义不一致

**问题**：
- 白盒 `workflows.py` 第 31-34 行：`workspace_path` = `repo_path.parent / "workspaces" / workspace_name`
- 黑盒 `workflows.py` 第 42 行：`workspace_path` = `repo_path`（仓库根目录）

两边语义不同，影响 `auth-state.json` 的写入和清理位置。

涉及文件：
- `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` 第 42 行

**修复方案**：

黑盒 `workflows.py` 中统一 `workspace_path` 的计算方式，与白盒保持一致：

```python
# 黑盒 workflows.py 中
if input.workspace_name:
    ws_dir = resolve_workspaces_dir(input.repo_path)
    workspace_path = str(ws_dir / input.workspace_name)
else:
    workspace_path = input.repo_path

# 传递给 ActivityInput
act_input = BlackboxActivityInput(
    ...
    workspace_path=workspace_path,
)
```

**变更文件**：
- 修改：`packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`

---

### 修复 4：统一 deliverables_subdir 常量

**问题**：
- 白盒 `shared.py`：`deliverables_subdir=".shannon/deliverables"`（独立默认值）
- 黑盒 `shared.py`：`deliverables_subdir=".shannon/deliverables"`（独立默认值）

默认值目前相同，但独立定义存在漂移风险。

涉及文件：
- `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`
- `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`

**修复方案**：

在 `shannon_core/constants.py` 中定义常量：

```python
DEFAULT_DELIVERABLES_SUBDIR = ".shannon/deliverables"
```

白盒和黑盒的 `shared.py` 都从 `shannon_core.constants` 导入该常量作为 `deliverables_subdir` 的默认值。

**变更文件**：
- 新增或修改：`packages/core/src/shannon_core/constants.py`
- 修改：`packages/whitebox/src/shannon_whitebox/pipeline/shared.py`
- 修改：`packages/blackbox/src/shannon_blackbox/pipeline/shared.py`

---

## 变更范围汇总

| 修复项 | 新增文件 | 修改文件 | 风险等级 |
|--------|---------|---------|---------|
| 1. 统一路径解析 | `core/utils/paths.py` | blackbox workflows, activities; whitebox activities, worker | 中 |
| 2. 统一检测标准 | — | blackbox workflows, exploitation_checker | 低 |
| 3. workspace_path 语义 | — | blackbox workflows | 低 |
| 4. 统一常量 | `core/constants.py` | whitebox shared, blackbox shared | 低 |

## 未修复项（方案 A 范围外）

以下问题已知但不在本次修复范围内：
- `--output` 参数在白盒和黑盒中都无效（问题 5）
- 白盒不持久化 agent 完成状态到 session.json（问题 11）
- 缺少衔接失败的诊断提示
- `workspace_name` 在白盒 `run_vuln_agent` 中被覆盖为 `agent_name`（问题 14）

## 测试策略

- 现有单元测试应全部通过（路径解析统一后行为不变）
- 新增单元测试验证 `resolve_deliverables_path()` 的各种路径组合
- 新增单元测试验证 `has_valid_whitebox_results()` 的边界情况
- 手动验证：白盒扫描后黑盒扫描能正确找到 exploitation_queue.json
