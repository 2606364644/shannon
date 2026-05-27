## ADDED Requirements

### Requirement: Agent 总览表
文档 SHALL 包含一个总览表，列出所有 14 个 agent 的名称、所属阶段、前置条件、使用的 prompt 模板、产出 deliverable 文件名、模型层级。

#### Scenario: 用户快速查找 agent 信息
- **WHEN** 用户查看 agent 总览表
- **THEN** 能看到每个 agent 的完整定义信息

### Requirement: 流水线阶段分组说明
文档 SHALL 按流水线阶段分组描述 agent：预侦察 → 侦察 → 漏洞分析（5 个并行） → 漏洞利用（5 个并行） → 报告。每组包含阶段目的、包含的 agent、并行/串行关系。

#### Scenario: 用户理解扫描流程
- **WHEN** 用户阅读阶段分组说明
- **THEN** 能画出完整的 agent 执行顺序和并行关系图

### Requirement: 逐 agent 详细说明
每个 agent SHALL 有独立小节，包含：职责描述（一段话）、输入（需要什么数据）、输出（deliverable 格式和内容概述）、prompt 策略要点（prompt 模板中的核心指令方向）。

#### Scenario: 用户理解特定 agent
- **WHEN** 用户阅读 injection-vuln agent 小节
- **THEN** 能了解该 agent 分析哪些注入类型、使用什么方法论、产出什么格式的结果

### Requirement: 漏洞类型与 agent 映射
文档 SHALL 明确说明 5 种漏洞类型（injection、xss、auth、authz、ssrf）与对应的 2 个 agent（vuln 分析 + exploit 利用）的映射关系。

#### Scenario: 用户理解漏洞类型覆盖
- **WHEN** 用户查看映射关系
- **THEN** 能看到每种漏洞类型都有对应的分析 agent 和利用 agent

### Requirement: 白盒与黑盒 agent 差异说明
文档 SHALL 说明哪些 agent 仅用于白盒（pre-recon、vuln agents）、哪些仅用于黑盒（recon-blackbox、exploit agents、report）、哪些共享（recon）。

#### Scenario: 用户区分扫描模式
- **WHEN** 用户阅读差异说明
- **THEN** 能列出白盒扫描和黑盒扫描各自使用的 agent 集合

### Requirement: Deliverable 文件格式说明
文档 SHALL 说明每种 deliverable 的文件名和预期内容格式（markdown 结构、exploitation queue JSON 格式）。

#### Scenario: 用户理解产出物
- **WHEN** 用户查看 deliverable 说明
- **THEN** 能知道每个 agent 产出的文件名和大致内容结构
