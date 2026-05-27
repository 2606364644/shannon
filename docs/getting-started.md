# Shannon-py 快速入门指南

## 1. 环境准备

在开始之前，请确保你的开发环境满足以下要求：

- **Python >= 3.12**
- **uv** 包管理器 — 安装方式请参考 [uv 官方文档](https://docs.astral.sh/uv/)
- **Temporal Server** — 通过 `temporal cli install` 安装，或从 [https://temporal.io](https://temporal.io) 下载。安装后使用以下命令启动开发服务器：

  ```bash
  temporal server start-dev
  ```

- **Claude API Key（Anthropic）** — 设置为环境变量：

  ```bash
  export ANTHROPIC_API_KEY="your-api-key-here"
  ```

## 2. 安装步骤

克隆仓库并安装所有依赖：

```bash
git clone <repo-url>
cd shannon-py
uv sync
```

`uv sync` 会自动根据工作区配置安装 `shannon-core`、`shannon-whitebox` 和 `shannon-blackbox` 三个包及其全部依赖。

## 3. 白盒扫描教程

白盒扫描通过分析目标仓库的源代码来发现潜在的安全漏洞。

### 准备目标仓库

确保目标仓库是一个本地 Git 仓库，路径可被本工具访问。

### 启动扫描

```bash
shannon-whitebox start --repo /path/to/target
```

可选参数：

| 参数 | 说明 |
|------|------|
| `-o` / `--output` | 产出物输出目录 |
| `-w` / `--workspace` | 工作区名称（支持恢复已有工作区） |
| `-c` / `--config` | YAML 配置文件路径 |
| `--pipeline-testing` | 使用最小化提示词进行测试 |
| `--temporal-address` | Temporal 服务器地址（默认 `localhost:7233`） |

### 查看产出物

默认情况下，扫描产出物存放在目标仓库的 `<repo>/.shannon/deliverables/` 目录下。

## 4. 黑盒扫描教程

黑盒扫描针对运行中的 Web 应用进行运行时漏洞验证，无需访问源代码。

### 启动扫描

```bash
shannon-blackbox start --url https://example.com
```

可选参数：

| 参数 | 说明 |
|------|------|
| `--url` | （必填）目标 URL |
| `-o` / `--output` | 产出物输出目录 |
| `-w` / `--workspace` | 工作区名称（支持恢复已有工作区） |
| `-c` / `--config` | YAML 配置文件路径 |
| `--vuln-classes` | 指定要测试的漏洞类型（默认：全部），可多次使用 |
| `--no-exploit` | 跳过漏洞利用阶段 |
| `--pipeline-testing` | 使用最小化提示词进行测试 |
| `--temporal-address` | Temporal 服务器地址（默认 `localhost:7233`） |

### 按漏洞类型过滤

```bash
shannon-blackbox start --url https://example.com --vuln-classes injection --vuln-classes xss
```

### 仅检测不利用

```bash
shannon-blackbox start --url https://example.com --no-exploit
```

### 查看报告

扫描完成后，报告将保存在对应的工作区目录中。

## 5. SDK 集成状态说明

> **⚠️ 注意：`run_claude_prompt()` 当前为桩函数，尚未集成 Claude Agent SDK。**
>
> 该函数位于 `packages/whitebox/src/shannon_whitebox/agents/runner.py`，调用时会直接抛出 `NotImplementedError`。这意味着目前无法运行完整的端到端扫描流程，直到 Claude Agent SDK 的 Python 集成完成。

## 6. 查看结果

### 白盒扫描

```bash
shannon-whitebox workspaces
shannon-whitebox logs <workspace_name>
```

### 黑盒扫描

```bash
shannon-blackbox workspaces
shannon-blackbox logs <workspace_name>
```

### 工作区目录结构

```
workspaces/
└── <name>/
    ├── session.json
    ├── workflow.log
    ├── agents/
    └── prompts/
```
