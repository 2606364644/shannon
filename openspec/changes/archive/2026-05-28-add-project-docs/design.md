## Context

Shannon-py 是一个 AI 驱动的自动化渗透测试框架，采用 Python monorepo（uv workspace）结构，包含三个包：

- `shannon-core`：共享模型、配置解析、工具函数
- `shannon-whitebox`：白盒扫描，基于源码分析发现漏洞
- `shannon-blackbox`：黑盒扫描，运行时探测 + 漏洞利用 + 报告生成

系统使用 Temporal.io 编排工作流，通过 Claude（Anthropic）作为 LLM 后端驱动 14 个专用 agent。当前 `run_claude_prompt()` 尚未实现（stub），但文档需假设其已完成来撰写。

项目目前无任何文档文件。

## Goals / Non-Goals

**Goals:**
- 为安全工程师提供清晰的使用指南，能独立完成安装、配置和运行扫描
- 为开发者提供架构理解，能快速定位模块、理解数据流、参与贡献
- 建立可维护的文档结构，后续功能迭代时容易更新
- 所有文档中文撰写，代码和命令保持英文

**Non-Goals:**
- 不生成自动化的 API 文档（如 Sphinx/autodoc），本次仅手写 Markdown
- 不涉及贡献指南（CONTRIBUTING.md）、变更日志（CHANGELOG.md）
- 不涉及 CI/CD 文档
- 不翻译已有代码中的英文注释

## Decisions

### D1: 文档结构——集中式 docs/ + 各包简短 README

**选择**: 根目录 `docs/` 统一管理所有文档，每个 package 各有一个简短 README（2-3 段）

**理由**: Shannon-py 的核心流程跨包协作（白盒分析结果流入黑盒利用），用户需要跨包视角。分散式文档会打破连贯性。各包 README 仅做定位说明。

**替代方案**: 每个 package 内部 `docs/` → 拒绝，原因如上。

### D2: 文档文件组织

```
README.md                          # 项目入口
packages/core/README.md            # 各包简短说明
packages/whitebox/README.md
packages/blackbox/README.md
docs/
  getting-started.md               # Quick Start
  architecture.md                  # 架构
  agents.md                        # Agent 详解
  api-reference.md                 # API Reference（单文件）
  prompt-engineering.md            # Prompt 工程指南
  configuration.md                 # 配置参考
```

**理由**: 7 个文档文件覆盖所有需求，无子目录嵌套，查找路径短。API 总量不大（~50 个 public 符号），单文件足够。

### D3: SDK 未完成状态的处理方式

**选择**: 文档按 SDK 已完成撰写，在 getting-started.md 中加 note 说明 `run_claude_prompt()` 当前为 stub。

**理由**: 文档的目标状态是系统完整可用后的样子，而非当前 WIP 快照。这样文档无需在 SDK 完成后重写。

### D4: Agent 详解的组织方式

**选择**: 按流水线阶段分组（pre-recon → recon → vuln analysis → exploit → report），而非按字母序或按包。

**理由**: 用户理解 agent 的自然方式是跟随扫描流程。分组展示能清晰体现依赖关系和并行结构。

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| 手写 API Reference 可能随代码变动过时 | 在 tasks 中标注需同步更新的触发点；后续可考虑自动化 |
| 中文文档对国际贡献者不友好 | 代码示例和命令保持英文，技术术语保留英文原文 |
| SDK stub 状态导致 Quick Start 无法实际运行 | 明确 note 标注，列出 stub 位置 |
