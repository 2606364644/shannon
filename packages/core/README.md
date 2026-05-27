# shannon-core

共享模型、配置解析和工具函数

## 依赖

- pydantic>=2.0
- pyyaml>=6.0

## 核心模块

| 模块 | 说明 |
|------|------|
| `config/parser.py` | `parse_config` 解析 YAML 配置，`distribute_config` 分发配置到各 Agent |
| `models/config.py` | `Config`、`DistributedConfig`、`Authentication`、`Rules` 等配置模型 |
| `models/agents.py` | `AgentName` 枚举（14 个 Agent）、`AgentDefinition`、`AGENTS` 注册表 |
| `models/deliverables.py` | `DeliverableType` 枚举和文件名映射 |
| `models/queue_schemas.py` | `Vulnerability` 联合类型及各类漏洞模型（Injection/XSS/Auth/SSRF/Authz） |
| `models/result.py` | `WhiteboxScanResult`、`BlackboxScanResult` 扫描结果模型 |
| `models/metrics.py` | `AgentMetrics`、`SessionMetadata` 性能指标模型 |
| `models/errors.py` | `PentestError`、`ErrorCode` 统一错误处理 |
| `utils/billing.py` | 消费上限检测 |
| `utils/concurrency.py` | `run_with_concurrency_limit` 异步并发控制 |
| `utils/file_io.py` | 异步文件读写工具 |
| `utils/formatting.py` | 时间戳格式化和文本截断 |

## 关系

shannon-core 是基础层，被 shannon-whitebox 和 shannon-blackbox 共同依赖。
