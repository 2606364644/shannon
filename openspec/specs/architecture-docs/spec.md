## ADDED Requirements

### Requirement: 三层架构图
文档 SHALL 包含 ASCII 架构图，展示 shannon-core、shannon-whitebox、shannon-blackbox 的分层关系和依赖方向。

#### Scenario: 开发者理解包依赖
- **WHEN** 开发者查看架构文档
- **THEN** 能通过架构图清晰看到 core ← whitebox ← blackbox 的依赖链

### Requirement: 完整数据流图
文档 SHALL 包含数据流图，展示从用户输入（CLI 命令）到最终报告产出的完整路径：Config 解析 → Temporal Workflow 启动 → Agent 执行序列 → Deliverable 产出 → Session 记录。

#### Scenario: 开发者追踪扫描流程
- **WHEN** 开发者查看数据流图
- **THEN** 能理解一个扫描请求从 CLI 到最终产出的每一步

### Requirement: Temporal Workflow 生命周期说明
文档 SHALL 分别描述白盒工作流（WhiteboxScanWorkflow）和黑盒工作流（BlackboxScanWorkflow）的阶段序列、活动注册、重试策略、任务队列名称。

#### Scenario: 开发者理解工作流差异
- **WHEN** 开发者阅读工作流部分
- **THEN** 能列出白盒和黑盒工作流各自的活动序列和重试配置

### Requirement: 关键设计决策说明
文档 SHALL 列出核心设计决策：Git-based 状态管理（checkpoint/commit/rollback）、exploitation queue 桥接分析到利用、pipeline-testing 模式、workspace 隔离。

#### Scenario: 开发者理解设计意图
- **WHEN** 开发者阅读设计决策部分
- **THEN** 能理解为什么选择 Git 管理状态、为什么用 queue 文件桥接 agent 等

### Requirement: Workspace 目录结构说明
文档 SHALL 说明 workspace 的目录布局：`workspaces/<name>/session.json`、`deliverables/` 目录、日志文件位置。

#### Scenario: 开发者定位 workspace 文件
- **WHEN** 开发者查看 workspace 结构说明
- **THEN** 能找到 session 数据、deliverable 文件和日志的确切路径
