# Shannon-Py 架构文档

## 1. 三层架构图

Shannon-Py 采用分层单体仓库架构，由三个独立的 Python 包组成，各层职责清晰分离：

```
┌─────────────────────────────────────┐
│         shannon-blackbox            │
│    (黑盒扫描 + 漏洞利用 + 报告)      │
├─────────────────────────────────────┤
│         shannon-whitebox            │
│    (白盒扫描 + Agent 执行 + Prompt)  │
├─────────────────────────────────────┤
│          shannon-core               │
│  (共享模型 + 配置解析 + 工具函数)    │
└─────────────────────────────────────┘
```

**依赖方向：**

- `shannon-blackbox` 依赖 `shannon-whitebox`（复用 `AgentExecutor`、`PromptManager`）
- `shannon-whitebox` 依赖 `shannon-core`（使用共享模型、配置解析、错误定义）
- `shannon-core` 不依赖任何上层包

**各层职责：**

| 包 | 职责 |
|---|---|
| `shannon-core` | 定义共享数据模型（`AgentName`、`AgentDefinition`、`Config`、`DistributedConfig`）、配置解析（`parse_config`、`distribute_config`）、错误类型（`PentestError`、`ErrorCode`）、指标模型（`AgentMetrics`）|
| `shannon-whitebox` | 实现白盒扫描 Temporal Workflow、Agent 执行器（`AgentExecutor`）、Prompt 管理（`PromptManager`）、Git 状态管理（`GitManager`）、会话管理（`SessionManager`）|
| `shannon-blackbox` | 实现黑盒扫描 Temporal Workflow、漏洞利用执行器（`ExploitExecutor`）、侦察执行器（`ReconExecutor`）、报告组装（`ReportAssembler`）|

## 2. 完整数据流图

### 白盒扫描数据流

```
CLI 输入 (repo_path, web_url, config_path)
    │
    ▼
配置解析
    parse_config(config_path) → Config
    distribute_config(config) → DistributedConfig
    │
    ▼
SessionManager.create_workspace()
    创建 workspace 目录及 session.json
    │
    ▼
Temporal Workflow 启动 (WhiteboxScanWorkflow)
    任务队列: "shannon-whitebox"
    │
    ▼
run_preflight (验证仓库存在性)
    │
    ▼
run_agent (pre-recon)
    PromptManager 加载模板 → GitManager.checkpoint()
    → run_claude_prompt() → validate_deliverable()
    → GitManager.commit()
    │
    ▼
run_agent (recon)
    同上流程
    │
    ▼
run_vuln_agent × 5 (并行)
    injection-vuln ─┐
    xss-vuln       ─┤
    auth-vuln      ─┤ asyncio.gather()
    ssrf-vuln      ─┤
    authz-vuln     ─┘
    每个 Agent 输出:
      - {vuln_type}_analysis_deliverable.md
      - {vuln_type}_exploitation_queue.json (结构化 JSON)
    │
    ▼
PipelineState 返回 (status="completed")
    │
    ▼
SessionManager 更新 session.json
```

### 黑盒扫描数据流

```
CLI 输入 (web_url, config_path, exploit=True)
    │
    ▼
配置解析 (同白盒)
    │
    ▼
Temporal Workflow 启动 (BlackboxScanWorkflow)
    任务队列: "shannon-blackbox"
    │
    ▼
run_blackbox_preflight (空操作)
    │
    ▼
检测白盒结果
    扫描 deliverables 目录中是否存在
    {vuln_type}_exploitation_queue.json 文件
    │
    ├─ 有白盒结果 → 跳过 recon-blackbox
    │
    └─ 无白盒结果 → run_recon (recon-blackbox)
         │
         ▼
    run_exploit_agent × 5 (并行，受 exploit 标志控制)
      injection-exploit ─┐
      xss-exploit       ─┤
      auth-exploit      ─┤ asyncio.gather(return_exceptions=True)
      ssrf-exploit      ─┤
      authz-exploit     ─┘
    每个 Agent 读取对应的 exploitation_queue.json
    输出: {vuln_type}_exploitation_evidence.md
    │
    ▼
assemble_report
    收集所有 evidence 文件 → 拼装综合报告
    │
    ▼
run_report_agent
    基于拼装报告生成最终安全评估报告
    comprehensive_security_assessment_report.md
    │
    ▼
BlackboxPipelineState 返回 (status="completed")
```

## 3. Temporal Workflow 生命周期

### Whitebox Workflow (WhiteboxScanWorkflow)

**任务队列：** `shannon-whitebox`

**执行阶段：**

```
preflight → pre-recon → recon → [5x vuln agents 并行] → complete
```

| 阶段 | Activity | 超时 | 重试策略 |
|------|----------|------|----------|
| Preflight | `run_preflight` | 2 分钟 | 无 |
| Pre-recon | `run_agent` | 2 小时 | 最大 50 次，初始间隔 5 分钟，最大间隔 30 分钟，退避系数 2.0 |
| Recon | `run_agent` | 2 小时 | 默认重试 |
| Vuln Agents (×5) | `run_vuln_agent` | 各 2 小时 | 默认重试 |

**关键行为：**

- Pre-recon 使用激进的重试策略（50 次），应对 LLM 调用可能的临时失败
- 5 个漏洞分析 Agent 通过 `asyncio.gather` 并行执行
- 单个 Vuln Agent 失败会记录到 `PipelineState.error`，但不会中断其他 Agent
- Workflow 通过 `PipelineState.completed_agents` 跟踪已完成的 Agent，支持断点续跑

### Blackbox Workflow (BlackboxScanWorkflow)

**任务队列：** `shannon-blackbox`

**执行阶段：**

```
preflight → [recon-blackbox (条件性)] → [5x exploit agents 并行 (条件性)] → assemble_report → report_agent → complete
```

| 阶段 | Activity | 超时 | 重试策略 |
|------|----------|------|----------|
| Preflight | `run_blackbox_preflight` | 2 分钟 | 无 |
| Recon (条件) | `run_recon` | 2 小时 | 最大 3 次，初始间隔 30 秒，最大间隔 5 分钟，退避系数 2.0 |
| Exploit (×5) | `run_exploit_agent` | 各 2 小时 | 同上 |
| Report Assembly | `assemble_report` | 5 分钟 | 同上 |
| Report Agent | `run_report_agent` | 1 小时 | 同上 |

**关键行为：**

- Preflight 为空操作（`pass`），仅作为 Workflow 占位符
- `recon-blackbox` 仅在不存在白盒结果时执行——检测 `{vuln_type}_exploitation_queue.json` 是否存在
- Exploit Agents 受 `exploit` 标志控制，默认为 `True`
- 使用 `return_exceptions=True`，单个 Exploit Agent 失败不影响其他 Agent
- 全局统一重试策略：最大 3 次、30 秒初始间隔、5 分钟最大间隔

## 4. 关键设计决策

### Git-based 状态管理

`GitManager` 为 deliverable 目录提供基于 Git 的原子性状态管理，确保每个 Agent 执行的副作用可追溯、可回滚：

```
Agent 执行前: GitManager.create_checkpoint(deliverables, agent_name)
    → git add -A && git commit -m "checkpoint: before {agent_name}"

Agent 成功: GitManager.commit(deliverables, agent_name)
    → git add -A && git commit -m "deliverable: {agent_name}"

Agent 失败: GitManager.rollback(deliverables, reason)
    → git reset --hard HEAD && git clean -fd
```

此设计确保：
- 任何失败都会将 deliverable 目录恢复到 Agent 执行前的干净状态
- 花费超限（spending cap）时同样触发回滚
- 每个 Agent 的输出都有对应的 Git 提交记录

### Exploitation Queue 桥接

白盒和黑盒之间通过结构化 JSON 文件实现解耦通信：

1. 白盒阶段的 Vuln Agent（如 `injection-vuln`）在完成分析后，将结构化的漏洞利用队列写入 `{vuln_type}_exploitation_queue.json`
2. 该文件存储在 `.shannon/deliverables/` 目录下
3. 黑盒阶段的 Exploit Agent（如 `injection-exploit`）通过 `ExploitExecutor` 读取对应的队列文件作为输入
4. 桥接过程完全通过文件系统完成，无需额外的消息队列或 API 调用

```
Whitebox                        Blackbox
┌──────────────────┐            ┌──────────────────┐
│ injection-vuln   │            │ injection-exploit │
│ agent            │            │ agent             │
│        │         │            │        ▲          │
│        ▼         │            │        │          │
│  injection_      │  文件桥接   │  读取队列文件      │
│  exploitation_   │───────────▶│                   │
│  queue.json      │            │                   │
└──────────────────┘            └──────────────────┘
```

### Pipeline Testing 模式

`pipeline_testing_mode` 标志（CLI 通过 `--pipeline-testing` 传入）用于切换 Prompt 加载目录：

- 正常模式：从 `prompts/` 目录加载完整 Prompt 模板
- 测试模式：从 `prompts/pipeline-testing/` 目录加载简化版 Prompt

`PromptManager.load_sync()` 接收 `pipeline_testing` 参数，在测试模式下使用简化 Prompt，实现 CI 环境下的快速验证。

### Workspace 隔离

每次扫描创建独立的 Workspace 目录，实现完整的会话隔离：

- 每个 Workspace 包含独立的 `session.json`（元数据）、`workflow.log`（执行日志）、`agents/`（Agent 日志）、`prompts/`（归档 Prompt）、`.shannon/deliverables/`（输出文件）
- `SessionManager` 负责创建和管理 Workspace 生命周期
- Workspace 命名格式：`{hostname}_shannon-{timestamp}`，确保全局唯一

## 5. Workspace 目录结构

```
workspaces/
  <hostname>_shannon-<timestamp>/
    session.json           # 会话元数据 (web_url, repo_path, completed_agents, metrics)
    workflow.log           # 带时间戳的 Workflow 执行日志
    agents/                # 各 Agent 的日志文件
      pre-recon.log
      recon.log
      injection-vuln.log
      xss-vuln.log
      auth-vuln.log
      ssrf-vuln.log
      authz-vuln.log
      injection-exploit.log
      xss-exploit.log
      auth-exploit.log
      ssrf-exploit.log
      authz-exploit.log
      report.log
    prompts/               # 归档发送给各 Agent 的 Prompt
      pre-recon.txt
      recon.txt
      vuln-injection.txt
      vuln-xss.txt
      vuln-auth.txt
      vuln-ssrf.txt
      vuln-authz.txt
      injection-exploit.txt
      xss-exploit.txt
      auth-exploit.txt
      ssrf-exploit.txt
      authz-exploit.txt
      report-executive.txt
    .shannon/
      deliverables/        # Agent 输出文件
        pre_recon_deliverable.md
        recon_deliverable.md
        injection_analysis_deliverable.md
        xss_analysis_deliverable.md
        auth_analysis_deliverable.md
        ssrf_analysis_deliverable.md
        authz_analysis_deliverable.md
        injection_exploitation_queue.json
        xss_exploitation_queue.json
        auth_exploitation_queue.json
        ssrf_exploitation_queue.json
        authz_exploitation_queue.json
        injection_exploitation_evidence.md
        xss_exploitation_evidence.md
        auth_exploitation_evidence.md
        ssrf_exploitation_evidence.md
        authz_exploitation_evidence.md
        comprehensive_security_assessment_report.md
```
