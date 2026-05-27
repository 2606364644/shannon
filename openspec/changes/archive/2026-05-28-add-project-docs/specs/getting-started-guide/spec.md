## ADDED Requirements

### Requirement: 环境准备指南
文档 SHALL 列出所有前置依赖：Python >= 3.12、uv 包管理器、Temporal Server（含安装和启动命令）、Claude API Key 配置。

#### Scenario: 用户在全新环境准备
- **WHEN** 用户按文档从零准备环境
- **THEN** 能安装所有依赖并启动 Temporal Server

### Requirement: 安装步骤
文档 SHALL 提供完整的安装命令序列：克隆仓库、`uv sync` 安装依赖。

#### Scenario: 用户执行安装
- **WHEN** 用户按步骤执行安装命令
- **THEN** 三个包安装成功且可导入

### Requirement: 首次白盒扫描教程
文档 SHALL 提供白盒扫描的端到端教程：准备目标仓库、编写最小 YAML 配置（或无配置运行）、执行 CLI 命令、查看 deliverable 输出。

#### Scenario: 用户运行首次白盒扫描
- **WHEN** 用户按教程执行白盒扫描命令
- **THEN** 命令格式正确，输出路径和 deliverable 结构清晰

### Requirement: 首次黑盒扫描教程
文档 SHALL 提供黑盒扫描的端到端教程：指定目标 URL、执行 CLI 命令（含 `--url` 和可选参数）、查看报告输出。

#### Scenario: 用户运行首次黑盒扫描
- **WHEN** 用户按教程执行黑盒扫描命令
- **THEN** 命令格式正确，包含 `--url`、`--vuln-classes`、`--no-exploit` 等参数说明

### Requirement: SDK 状态说明
文档 SHALL 包含一个醒目的 note，说明 `run_claude_prompt()` 当前为 stub（尚未对接 Claude Agent SDK），标注其源码位置（`packages/whitebox/src/shannon_whitebox/agents/runner.py`）。

#### Scenario: 用户阅读 Quick Start
- **WHEN** 用户阅读 getting-started.md
- **THEN** 能注意到 SDK 未完成的状态说明，理解当前无法实际运行完整扫描

### Requirement: 查看结果说明
文档 SHALL 说明如何使用 `logs` 和 `workspaces` 子命令查看执行状态和输出。

#### Scenario: 用户查看扫描结果
- **WHEN** 扫描完成后用户执行 `logs` 命令
- **THEN** 能看到工作流日志和 deliverable 文件位置
