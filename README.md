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

## 配置

### AI Provider 配置

Shannon-py 支持多种 AI Provider，通过环境变量配置：

```bash
# 复制示例配置文件
cp .env.example .env

# 编辑 .env 文件，设置你的 API Key 和 Provider
```

**支持的 Provider:**

| Provider | 说明 | 配置示例 |
|----------|------|----------|
| `anthropic_api` | Anthropic 官方 API | `SHANNON_API_KEY=sk-ant-...` |
| `bedrock` | AWS Bedrock | `AWS_REGION=us-east-1` |
| `vertex` | Google Cloud Vertex AI | `SHANNON_PROJECT_ID=...` |
| `openai_compatible` | OpenAI 兼容接口 | `SHANNON_BASE_URL=https://...` |
| `litellm_router` | LiteLLM 路由器 | `SHANNON_AUTH_TOKEN=...` |

**环境变量优先级:**

1. `provider_config` 参数（代码中直接传入）
2. `SHANNON_*` 环境变量
3. `ANTHROPIC_*` 环境变量（向后兼容）

**成本估算:**

- Claude SDK Provider：返回精确成本
- OpenAI 兼容 Provider：基于公开定价估算
- 建议设置 `SHANNON_MAX_BUDGET` 限制单次调用花费

### Temporal 配置

默认使用本地 Temporal Server (`localhost:7233`)，可通过环境变量修改：

```bash
TEMPORAL_ADDRESS=your-temporal-server:7233
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
