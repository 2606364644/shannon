## Why

项目已完成核心架构重构（core / whitebox / blackbox 三包 monorepo），但缺少任何文档。新成员上手困难，用户无法理解如何使用，贡献者不清楚架构设计。需要完整的中文文档体系来服务安全工程师（使用者）和开发者（贡献者）两类受众。

## What Changes

- 新增项目根 `README.md`：项目概览、特性列表、安装步骤、快速命令示例、文档链接
- 新增 `packages/core/README.md`、`packages/whitebox/README.md`、`packages/blackbox/README.md`：各包简短说明
- 新增 `docs/getting-started.md`：环境准备、安装、配置、运行第一次扫描（白盒+黑盒）、查看结果。假设 Claude SDK 已集成，加 note 标注当前状态
- 新增 `docs/architecture.md`：三层架构图、数据流、Temporal workflow 生命周期、包依赖关系、关键设计决策
- 新增 `docs/agents.md`：14 个 agent 逐一详解——职责、输入/输出、前置条件、prompt 策略、deliverable 格式
- 新增 `docs/api-reference.md`：所有 public class/function 的签名和说明，按包组织（单文件）
- 新增 `docs/prompt-engineering.md`：模板系统、变量表、`@include` 机制、自定义/扩展 prompt 的方法、pipeline-testing 模式
- 新增 `docs/configuration.md`：完整 YAML 字段参考、示例配置、规则系统、认证配置

## Capabilities

### New Capabilities
- `project-readme`: 项目根 README 和各包 README，提供项目入口级介绍
- `getting-started-guide`: Quick Start 文档，引导用户完成首次扫描
- `architecture-docs`: 架构文档，面向开发者描述系统设计和数据流
- `agent-guide`: Agent 详解文档，描述 14 个 agent 的职责、流程和产出
- `api-reference`: API Reference 文档，列出所有 public 接口
- `prompt-engineering-guide`: Prompt 工程指南，描述模板系统和自定义方法
- `configuration-reference`: 配置参考文档，覆盖 YAML 所有字段

### Modified Capabilities

（无现有 capability 需要修改）

## Impact

- 仅新增文档文件（`.md`），不涉及任何代码变更
- 文档语言为中文，代码示例和命令保持英文
- 需要在 README 中妥善处理 `run_claude_prompt()` 未实现的状态（加 note 说明）
