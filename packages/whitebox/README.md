# shannon-whitebox

白盒源码漏洞分析扫描器

## 依赖

- shannon-core
- temporalio>=1.0
- click>=8.0
- aiofiles>=23.0

## 核心模块

| 模块 | 说明 |
|------|------|
| `agents/executor.py` | Agent 执行引擎，调用 LLM 完成分析任务 |
| `agents/runner.py` | Agent 运行编排，管理依赖和并发 |
| `prompts/manager.py` | Prompt 模板加载和渲染 |
| `session.py` | 工作区会话管理，支持断点续扫 |
| `git_manager.py` | Git 仓库检出和检查点管理 |
| `pipeline/workflows.py` | Temporal 工作流定义 |
| `pipeline/activities.py` | Temporal 活动定义 |
| `cli/main.py` | CLI 入口 |

## CLI

```bash
shannon-whitebox start --repo <path> [--output <dir>] [--workspace <name>] [--config <file>]
shannon-whitebox logs <workspace_name>
shannon-whitebox workspaces
```
