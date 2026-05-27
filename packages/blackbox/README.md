# shannon-blackbox

黑盒运行时漏洞验证和报告生成

## 依赖

- shannon-core
- shannon-whitebox
- temporalio>=1.0
- click>=8.0
- aiofiles>=23.0

## 核心模块

| 模块 | 说明 |
|------|------|
| `agents/recon_executor.py` | 黑盒侦察 Agent 执行器 |
| `agents/exploit_executor.py` | 漏洞利用 Agent 执行器 |
| `services/report_assembler.py` | 综合安全评估报告组装 |
| `services/exploitation_checker.py` | 漏洞利用结果验证 |
| `pipeline/workflows.py` | Temporal 工作流定义 |
| `pipeline/activities.py` | Temporal 活动定义 |
| `cli/main.py` | CLI 入口 |

## CLI

```bash
shannon-blackbox start --url <target_url> [--output <dir>] [--workspace <name>] [--config <file>] [--vuln-classes <class>] [--no-exploit]
shannon-blackbox logs <workspace_name>
shannon-blackbox workspaces
```
