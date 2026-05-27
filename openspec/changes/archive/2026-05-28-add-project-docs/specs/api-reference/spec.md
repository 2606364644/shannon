## ADDED Requirements

### Requirement: 按包组织的 API 列表
文档 SHALL 分三个区域（shannon-core、shannon-whitebox、shannon-blackbox），列出每个包的所有 public 类和函数。

#### Scenario: 开发者查找 API
- **WHEN** 开发者打开 api-reference.md
- **THEN** 能按包快速定位到需要的类或函数

### Requirement: 每个 API 条目包含完整信息
每个 public 类/函数 SHALL 包含：签名（参数名、类型、默认值）、简短功能描述（一句话）、返回值类型、所在文件路径。

#### Scenario: 开发者了解函数用法
- **WHEN** 开发者查看 `AgentExecutor.execute()` 条目
- **THEN** 能看到完整参数列表、返回类型 `AgentMetrics` 和功能描述

### Requirement: 模型类包含字段列表
所有 Pydantic 模型类（Config、DistributedConfig、AgentDefinition、各种 Vulnerability 模型等）SHALL 列出所有字段名、类型、默认值。

#### Scenario: 开发者查看配置模型
- **WHEN** 开发者查看 `Config` 模型条目
- **THEN** 能看到所有字段（rules、authentication、description 等）及类型

### Requirement: 枚举类型包含所有值
所有 Enum 类型（AgentName、DeliverableType、ErrorCode）和 Literal 类型（VulnClass、Severity、Confidence）SHALL 列出所有可选值。

#### Scenario: 开发者查找枚举值
- **WHEN** 开发者查看 `AgentName` 条目
- **THEN** 能看到全部 14 个枚举值及其字符串值

### Requirement: 标注文件路径
每个 API 条目 SHALL 标注其源文件路径（相对于仓库根目录），方便开发者定位源码。

#### Scenario: 开发者跳转到源码
- **WHEN** 开发者看到 API 条目中的文件路径
- **THEN** 能直接打开对应源文件阅读实现
