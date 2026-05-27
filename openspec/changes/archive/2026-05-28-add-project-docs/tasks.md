## 1. 根 README 和包 README

- [x] 1.1 编写 `README.md`：项目名称、一句话描述、特性列表、系统要求、安装步骤、CLI 命令示例、文档链接
- [x] 1.2 编写 `packages/core/README.md`：包用途、依赖（pydantic、pyyaml）、核心模块列表、与 whitebox/blackbox 的关系
- [x] 1.3 编写 `packages/whitebox/README.md`：包用途、依赖（shannon-core、temporalio、click）、核心模块列表、CLI 入口
- [x] 1.4 编写 `packages/blackbox/README.md`：包用途、依赖（shannon-core、shannon-whitebox、temporalio、click）、核心模块列表、CLI 入口

## 2. Quick Start 文档

- [x] 2.1 编写 `docs/getting-started.md` 环境准备部分：Python 3.12+、uv、Temporal Server 安装和启动、Claude API Key
- [x] 2.2 编写安装步骤：克隆仓库、uv sync
- [x] 2.3 编写白盒扫描教程：准备目标仓库、CLI 命令、查看 deliverable 输出
- [x] 2.4 编写黑盒扫描教程：指定目标 URL、CLI 命令（含 --url、--vuln-classes、--no-exploit）、查看报告
- [x] 2.5 添加 SDK 未完成的 note（`run_claude_prompt()` stub 状态说明）
- [x] 2.6 编写查看结果部分：logs 和 workspaces 子命令用法

## 3. 架构文档

- [x] 3.1 编写 `docs/architecture.md` 三层架构图（ASCII）：core ← whitebox ← blackbox 依赖关系
- [x] 3.2 编写完整数据流图：CLI 输入 → Config 解析 → Temporal Workflow → Agent 执行 → Deliverable 产出
- [x] 3.3 编写 Temporal Workflow 生命周期：白盒和黑盒工作流的阶段、活动、重试策略、任务队列
- [x] 3.4 编写关键设计决策：Git 状态管理、exploitation queue 桥接、pipeline-testing、workspace 隔离
- [x] 3.5 编写 Workspace 目录结构说明：session.json、deliverables/、日志文件

## 4. Agent 详解文档

- [x] 4.1 编写 `docs/agents.md` Agent 总览表：14 个 agent 的名称、阶段、前置条件、prompt 模板、deliverable 文件名、模型层级
- [x] 4.2 编写流水线阶段分组：预侦察 → 侦察 → 漏洞分析（5 并行） → 漏洞利用（5 并行） → 报告
- [x] 4.3 编写 pre-recon agent 详细说明：职责、输入、输出、prompt 策略要点
- [x] 4.4 编写 recon（白盒）和 recon-blackbox agent 详细说明
- [x] 4.5 编写 5 个 vuln 分析 agent 详细说明（injection、xss、auth、ssrf、authz）
- [x] 4.6 编写 5 个 exploit 利用 agent 详细说明
- [x] 4.7 编写 report agent 详细说明
- [x] 4.8 编写漏洞类型与 agent 映射关系和白盒/黑盒 agent 差异说明
- [x] 4.9 编写 Deliverable 文件格式说明（markdown 结构、exploitation queue JSON 格式）

## 5. API Reference 文档

- [x] 5.1 编写 `docs/api-reference.md` shannon-core 部分：所有模型类（Config、DistributedConfig、AgentDefinition、Vulnerability 模型、Metrics、Errors）、枚举（AgentName、DeliverableType、ErrorCode）、函数（parse_config、distribute_config、billing/concurrency/file_io/formatting 工具函数）
- [x] 5.2 编写 shannon-whitebox 部分：AgentExecutor、PromptManager、SessionManager、GitManager、Workflow/Activity/State 类、CLI
- [x] 5.3 编写 shannon-blackbox 部分：ReconExecutor、ExploitExecutor、ReportAssembler、ExploitationChecker、Workflow/Activity/State 类、CLI

## 6. Prompt Engineering Guide 文档

- [x] 6.1 编写 `docs/prompt-engineering.md` 模板系统概述：存放位置、文件格式、加载机制
- [x] 6.2 编写完整变量参考表：所有 `{{VAR}}` 变量的名称、数据来源、默认值、使用场景
- [x] 6.3 编写 @include 机制说明：语法、路径解析、安全限制、shared/ 片段目录
- [x] 6.4 编写自定义 prompt 指南：修改模板、注册新模板、添加新变量的步骤
- [x] 6.5 编写 pipeline-testing 模式说明：--pipeline-testing 行为、测试/生产 prompt 差异
- [x] 6.6 编写 prompt 调试技巧：audit 日志、快速验证、变量替换检查

## 7. 配置参考文档

- [x] 7.1 编写 `docs/configuration.md` 完整 YAML 字段参考：Config 所有字段及嵌套结构
- [x] 7.2 编写示例配置：最小配置和完整配置各一个
- [x] 7.3 编写规则系统详解：avoid/focus 规则、7 种规则类型的含义和 value 格式
- [x] 7.4 编写认证配置详解：4 种登录类型、Credentials、login_flow、SuccessCondition
- [x] 7.5 编写漏洞类型和报告配置说明：vuln_classes、exploit、ReportConfig
