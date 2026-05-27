## ADDED Requirements

### Requirement: 模板系统概述
文档 SHALL 说明 prompt 模板的存放位置（`prompts/` 目录）、文件格式（`.txt`）、加载机制（`PromptManager.load_sync()`）。

#### Scenario: 用户理解模板系统结构
- **WHEN** 用户阅读模板系统概述
- **THEN** 能知道模板文件在哪、如何被加载和解析

### Requirement: 完整变量参考表
文档 SHALL 包含一个表格，列出所有模板变量（`{{WEB_URL}}`、`{{REPO_PATH}}`、`{{DESCRIPTION}}` 等），包括变量名、数据来源、默认值、使用场景。

#### Scenario: 用户查找模板变量
- **WHEN** 用户查看变量参考表
- **THEN** 能找到每个变量的含义和数据来源

### Requirement: @include 机制说明
文档 SHALL 说明 `@include(path)` 指令的用法、路径解析规则（相对于当前模板目录）、安全限制（路径穿越防护）、共享片段目录（`prompts/shared/`）。

#### Scenario: 用户使用 @include
- **WHEN** 用户阅读 @include 说明后创建新模板
- **THEN** 能正确引用 shared/ 目录下的片段文件

### Requirement: 自定义 prompt 指南
文档 SHALL 提供修改现有 prompt 和创建新 prompt 的步骤指南，包括：如何修改模板文件、如何为新 agent 注册 prompt 模板名、如何添加新模板变量。

#### Scenario: 用户自定义 prompt
- **WHEN** 用户按指南修改 vuln-injection.txt
- **THEN** 修改后的 prompt 能在下次扫描中生效

### Requirement: pipeline-testing 模式说明
文档 SHALL 说明 `--pipeline-testing` 标志的行为：切换到 `prompts/pipeline-testing/` 目录加载简化 prompt，用于 CI 测试和快速验证。

#### Scenario: 开发者使用测试模式
- **WHEN** 开发者阅读 pipeline-testing 说明
- **THEN** 能理解测试 prompt 和生产 prompt 的区别，知道何时使用测试模式

### Requirement: Prompt 调试技巧
文档 SHALL 提供调试 prompt 的方法：查看 audit 日志中的 prompt 归档、使用 pipeline-testing 模式快速验证、检查变量替换结果。

#### Scenario: 用户调试 prompt 问题
- **WHEN** 用户遇到 prompt 渲染异常
- **THEN** 能通过文档建议的方法定位问题
