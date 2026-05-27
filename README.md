# shannon-py

AI 驱动的自动化渗透测试框架

## 功能特性

- 白盒源码漏洞扫描与黑盒运行时漏洞验证
- 14 个专用 Agent 协同工作，覆盖侦察、漏洞分析、漏洞利用和报告生成全流程
- 基于 Temporal.io 的工作流编排，支持断点续扫和并发控制
- 可定制的 prompt 模板系统，适配不同安全测试场景
- 支持 Injection、XSS、Auth、SSRF、Authz 五大漏洞类别
- YAML 配置文件驱动，支持范围限定、认证配置和报告过滤

## 系统要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) 包管理器
- Temporal Server（默认地址 `localhost:7233`）

## 安装

```bash
git clone <repo-url> && cd shannon-py
uv sync
```

启动 Temporal Server：

```bash
temporal server start-dev
```

## 使用方法

### 白盒扫描

```bash
shannon-whitebox start --repo /path/to/target-repo

shannon-whitebox start --repo /path/to/repo --output ./results --workspace my-scan --config scan.yaml

shannon-whitebox start --repo /path/to/repo --pipeline-testing --temporal-address localhost:7233
```

查看工作区和日志：

```bash
shannon-whitebox workspaces
shannon-whitebox logs my-scan
```

### 黑盒扫描

```bash
shannon-blackbox start --url https://target.example.com

shannon-blackbox start --url https://target.example.com --vuln-classes injection --vuln-classes xss --no-exploit

shannon-blackbox start --url https://target.example.com --config scan.yaml --output ./results --workspace my-scan
```

查看工作区和日志：

```bash
shannon-blackbox workspaces
shannon-blackbox logs my-scan
```

## 文档

- [快速开始](getting-started.md)
- [系统架构](architecture.md)
- [Agent 说明](agents.md)
- [API 参考](api-reference.md)
- [Prompt 工程](prompt-engineering.md)
- [配置指南](configuration.md)

## 项目结构

```
shannon-py/
├── packages/
│   ├── core/          # 共享模型、配置解析和工具函数
│   ├── whitebox/      # 白盒源码漏洞分析扫描器
│   └── blackbox/      # 黑盒运行时漏洞验证和报告生成
├── prompts/           # Prompt 模板文件
└── pyproject.toml     # uv workspace 配置
```
