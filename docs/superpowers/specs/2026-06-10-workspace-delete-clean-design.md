# Workspace Delete & Clean

Date: 2026-06-10

## Problem

Shannon-py 目前没有删除或清理工作空间的能力。工作空间一旦创建就永久保留在磁盘上，用户只能手动删除目录。随着扫描次数增加，工作空间会不断积累。

原始 shannon 项目也只有 `clean`（清理产物，不删除目录）和 `uninstall`（删除整个 ~/.shannon/），同样缺少按工作空间删除的能力。

## Solution

添加两个命令：

- **`workspace delete <name>`** — 删除整个工作空间目录及其所有数据
- **`workspace clean <name>`** — 只清理扫描产物，保留工作空间结构和 `session.json`

两个命令同时添加到 whitebox 和 blackbox CLI，`clean` 根据 scan_type 只清理对应的产物。

## Architecture

### Core Layer — SessionManager 扩展

在 `packages/core/src/shannon_core/session.py` 的 `SessionManager` 中新增三个方法：

#### `delete_workspace(workspace_name: str) -> bool`

1. 调用 `get_workspace(workspace_name)` 查找目录，不存在返回 `False`
2. 调用 `_handle_workspace_links()` 处理父子链接
3. `shutil.rmtree()` 删除整个目录
4. 返回 `True`

#### `clean_workspace(workspace_path: Path, scan_type: str) -> None`

根据 `scan_type` 决定清理范围：

**白盒 (`scan_type="whitebox"`) 清理**：
- `deliverables/` 目录下所有文件
- `agents/` 目录下所有文件
- `prompts/` 目录下所有文件
- `scratchpad/` 目录下所有文件
- `workflow.log` 文件
- `.playwright/` 和 `.playwright-cli/` 目录

**黑盒 (`scan_type="blackbox"`) 清理**：
- 黑盒相关 deliverables（`*_exploitation_evidence.md`、`*_findings.md`、`comprehensive_security_assessment_report.md`）
- 黑盒 agent logs（`*-exploit_*.log`、`*-validate-authentication_*.log`）
- `.playwright/` 和 `.playwright-cli/` 目录
- 截断 `workflow.log`

**两者都保留**：`session.json`（工作空间元数据）。

清理后更新 `session.json`：
- 重置 `completed_agents` 为空列表
- 清空 `deliverables_summary`

#### `_handle_workspace_links(workspace_path: Path) -> None`

**删除白盒工作空间时**：
- 遍历 `child_workspaces` 列表
- 将每个子黑盒工作空间的 `session.json` 中 `parent_workspace` 设为 `null`

**删除黑盒工作空间时**：
- 从父白盒工作空间的 `child_workspaces` 列表中移除自己
- 更新父工作空间的 `session.json`

如果链接的对方已被手动删除，静默忽略，不报错。

### CLI Layer — 命令定义

#### `workspace delete <name>`

```
Usage: shannon-whitebox workspace delete [OPTIONS] NAME

  Delete a workspace and all its data.

Options:
  --force  Skip confirmation prompt
```

行为：
1. 查找工作空间，不存在则报错退出 (`exit(1)`)
2. 显示工作空间摘要：类型、目标 URL、状态
3. 如有链接关系，显示警告（子工作空间数量 或 父工作空间名称）
4. `click.confirm()` 确认，默认 No
5. `--force` 跳过确认
6. 调用 `SessionManager.delete_workspace()` 执行删除
7. 输出结果（成功 ✅ / 失败 ❌）

#### `workspace clean <name>`

```
Usage: shannon-whitebox workspace clean [OPTIONS] NAME

  Clean scan artifacts from a workspace, preserving its structure.

Options:
  --force  Skip confirmation prompt
```

行为：
1. 查找工作空间，不存在则报错退出
2. 显示将要清理和保留的内容
3. `click.confirm()` 确认，默认 No
4. `--force` 跳过确认
5. 调用 `SessionManager.clean_workspace()`，传入当前 CLI 的 scan_type
6. 输出清理结果

### Whitebox vs Blackbox 差异

| 方面 | Whitebox CLI | Blackbox CLI |
|------|-------------|-------------|
| 命令 | `workspace delete`, `workspace clean` | `workspace delete`, `workspace clean` |
| `clean` 范围 | 白盒产物（deliverables、agents、prompts、scratchpad、workflow.log、.playwright*） | 黑盒产物（黑盒 deliverables、黑盒 agent logs、.playwright*、截断 workflow.log） |
| `delete` 范围 | 整个目录 | 整个目录 |

## Edge Cases

| 情况 | 行为 |
|------|------|
| 工作空间不存在 | 输出错误信息，`exit(1)` |
| 工作空间正在运行 (`status=running`) | 显示警告，仍允许删除（需确认或 `--force`） |
| 删除过程中文件被占用 | `shutil.rmtree` 的 `onerror` 回调报告错误，继续尝试 |
| `clean` 时 `session.json` 损坏 | 跳过清理，报告错误 |
| 链接的父/子工作空间已被手动删除 | 静默忽略，不报错 |
| `--force` 且工作空间不存在 | 报错退出（force 只跳过确认，不跳过校验） |

## Files to Modify

1. `packages/core/src/shannon_core/session.py` — 添加 `delete_workspace`、`clean_workspace`、`_handle_workspace_links` 方法
2. `packages/whitebox/src/shannon_whitebox/cli/main.py` — 添加 `workspace delete` 和 `workspace clean` 子命令
3. `packages/blackbox/src/shannon_blackbox/cli/main.py` — 添加 `workspace delete` 和 `workspace clean` 子命令

## Testing

- 单元测试 `SessionManager.delete_workspace()`：正常删除、不存在、链接处理
- 单元测试 `SessionManager.clean_workspace()`：白盒清理、黑盒清理、保留 session.json
- CLI 集成测试：确认流程、`--force`、错误处理
