# GitNexus CLI + MCP 全自动集成设计

**日期**: 2026-06-10
**状态**: Draft
**分支**: feat/fork-py

## 背景

shannon-py 项目的调用链分析当前使用名称匹配 + LLM 混合方案，存在 30-50% 的调用丢失。GitNexus 官方推荐 CLI + MCP 模式作为最佳实践（vs 纯 Web UI），能提供精确的知识图谱构建和调用链追踪。

当前项目已有 GitNexus 相关代码，但使用 `_StubMCPClient`（始终返回 None），未真正连接 GitNexus。

## 目标

将 GitNexus CLI + MCP 集成为调用链分析的底层引擎，替代当前的名称匹配方案，同时保留 LLM 作为下游 taint 分析引擎。

**架构定位**：
- **GitNexus**（上游）：构建知识图谱、追踪调用链（函数级别）
- **LLM**（下游）：逐函数做参数级 taint 分析（GitNexus 无法做到的）

## 方案选择

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| **A: 全自动（选定）** | Pipeline 自动运行 analyze + MCP 查询 | 一键运行，全自动化 | 需要 gitnexus CLI |
| B: 离线+查询 | 索引是离线步骤 | Pipeline 更快 | 需要额外维护 |
| C: 混合模式 | 全自动 + Agent 配置 | 最大收益 | 配置复杂 |

## 架构

```
┌─────────────────────────────────────────────────────┐
│                  Shannon Pipeline                     │
│                                                       │
│  ① GitNexusIndexer                                   │
│     subprocess: gitnexus analyze [repo_path]          │
│     → .gitnexus/ 知识图谱                              │
│                                                       │
│  ② GitNexusMCPClient                                 │
│     subprocess: gitnexus mcp                          │
│     ← stdio MCP protocol →                           │
│     tools: impact / context / cypher / query          │
│                                                       │
│  ③ CallChainBuilder                                  │
│     sink → impact(upstream) → 调用链                  │
│     每个函数 → context → 详细签名                      │
│     复杂链路 → cypher 多跳查询                         │
│                                                       │
│  ④ LLM Taint Analyzer (现有)                          │
│     调用链 → 逐函数 taint 分析                         │
│     参数级污点传播                                     │
└─────────────────────────────────────────────────────┘
```

## 核心组件

### GitNexusIndexer

**职责**：封装 `gitnexus analyze` CLI 命令，管理本地知识图谱索引。

**位置**：`shannon/gitnexus_client/indexer.py`

**接口**：

```python
class GitNexusIndexer:
    def __init__(self, repo_path: Path): ...

    async def ensure_indexed(self, force: bool = False) -> IndexResult:
        """确保知识图谱存在且是最新的。

        步骤：
        1. 检查 gitnexus 是否安装 (which gitnexus / npm list -g gitnexus)
        2. 检查 .gitnexus/ 是否存在
        3. 如果不存在或 force=True，运行 gitnexus analyze
        4. 返回 IndexResult（文件数、符号数、是否过期）
        """

    async def check_stale(self) -> bool:
        """检查索引是否过期（基于 git diff 或 .gitnexus/ 时间戳）"""

    @staticmethod
    async def is_installed() -> bool:
        """检查 gitnexus CLI 是否可用"""
```

**IndexResult**：

```python
@dataclass
class IndexResult:
    success: bool
    file_count: int
    symbol_count: int
    is_stale: bool
    error_message: str | None = None
```

### GitNexusMCPClient

**职责**：通过 stdio MCP 协议连接 `gitnexus mcp` 子进程。

**位置**：`shannon/gitnexus_client/mcp_client.py`

**接口**：

```python
class GitNexusMCPClient:
    def __init__(self, repo_path: Path): ...

    async def connect(self) -> None:
        """启动 gitnexus mcp 子进程，建立 stdio MCP 连接。

        子进程命令: gitnexus mcp
        通信协议: JSON-RPC over stdio

        初始化流程：
        1. 启动子进程，捕获 stdin/stdout
        2. 发送 initialize 请求（含 capabilities）
        3. 收到 initialize 响应后，发送 initialized 通知
        4. 连接就绪，可开始 tools/call 请求
        """

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """调用 MCP 工具。

        支持的工具：
        - impact: 影响分析（上下游调用者）
        - context: 函数级上下文视图
        - query: 进程分组搜索
        - cypher: 原始图查询
        """

    async def disconnect(self) -> None:
        """关闭子进程，清理资源"""

    async def __aenter__(self) -> "GitNexusMCPClient": ...
    async def __aexit__(self, *args) -> None: ...
```

**MCP 协议交互格式**：

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "impact",
    "arguments": {
      "target": "execute_sql",
      "direction": "upstream",
      "maxDepth": 5
    }
  },
  "id": 1
}

// Response
{
  "jsonrpc": "2.0",
  "result": {
    "content": [
      {"type": "text", "text": "..."}
    ]
  },
  "id": 1
}
```

### CallChainBuilder

**职责**：基于 GitNexus 知识图谱构建调用链，供 LLM taint 分析使用。

**位置**：`shannon/gitnexus_client/call_chain_builder.py`

**接口**：

```python
class CallChainBuilder:
    def __init__(self, mcp_client: GitNexusMCPClient): ...

    async def trace_from_sink(
        self, sink_name: str, max_depth: int = 5
    ) -> CallChain:
        """从 sink 函数开始，向上追踪调用链。

        策略：
        1. impact(sink, direction="upstream", maxDepth=max_depth)
           → 获取所有调用者（按 depth 分组）
        2. 对链路上的每个函数调 context() 获取签名和参数
        3. 如果 impact 无法完全连接，用 cypher 多跳查询补充
        """

    async def get_function_context(
        self, function_name: str
    ) -> FunctionContext:
        """获取函数的完整上下文"""

    async def find_sinks(
        self, sink_patterns: list[str]
    ) -> list[Symbol]:
        """搜索项目中的 sink 函数"""
```

**CallChain 数据结构**：

```python
@dataclass
class CallChain:
    sink: Symbol                    # sink 函数
    chains: list[list[ChainStep]]   # 多条从 source 到 sink 的路径
    confidence: float               # 整体置信度

@dataclass
class ChainStep:
    symbol: Symbol                  # 函数/方法
    call_type: str                  # CALLS / IMPORTS / EXTENDS
    confidence: float               # 单步置信度
    file_path: str
    line_number: int

@dataclass
class Symbol:
    name: str
    kind: str                       # Function / Class / Method
    file_path: str
    start_line: int
    end_line: int
    signature: str | None = None    # 从 context() 获取

@dataclass
class FunctionContext:
    symbol: Symbol
    incoming_calls: list[Symbol]    # 谁调用了它
    outgoing_calls: list[Symbol]    # 它调用了谁
    processes: list[str]            # 所在的执行流
```

## MCP 工具使用策略

| 场景 | 工具 | 参数示例 | 用途 |
|------|------|----------|------|
| 找 sink 的所有调用者 | `impact` | `target="execute_sql", direction="upstream", maxDepth=5` | 追踪完整调用链 |
| 找 source 的所有被调用者 | `impact` | `target="user_input", direction="downstream"` | 反向验证 |
| 获取函数签名/参数 | `context` | `name="validate_user"` | LLM taint 分析需要函数签名 |
| 搜索危险函数 | `query` | `query="execute_sql OR eval OR system"` | 自动发现 sink |
| 多跳链路追踪 | `cypher` | `MATCH (caller)-[:CALLS*1..5]->(sink:Function {name: 'execute_sql'}) RETURN path` | impact 无法连接时的补充 |

## Pipeline 集成

改造现有的 `build_code_index_with_gitnexus` 函数：

```python
async def build_code_index_with_gitnexus(
    repo_path: Path,
    llm_client,
    sink_patterns: list[str] | None = None,  # e.g. ["execute_sql", "eval", "os.system"]
):
    """改造后的 pipeline 入口"""
    # 1. 检查 GitNexus 是否可用
    if not await GitNexusIndexer.is_installed():
        logger.warning("GitNexus not installed, falling back to name matching")
        return await build_code_index_fallback(repo_path)

    # 2. 自动索引
    indexer = GitNexusIndexer(repo_path)
    index_result = await indexer.ensure_indexed()
    if not index_result.success:
        logger.warning(f"Index failed: {index_result.error_message}")
        return await build_code_index_fallback(repo_path)

    # 3. 连接 MCP 并构建调用链
    async with GitNexusMCPClient(repo_path) as mcp:
        builder = CallChainBuilder(mcp)

        # 3a. 自动发现 sinks（或使用传入的 patterns）
        sinks = await builder.find_sinks(sink_patterns or DEFAULT_SINK_PATTERNS)

        # 3b. 对每个 sink 追踪调用链
        all_chains: list[CallChain] = []
        for sink in sinks:
            chain = await builder.trace_from_sink(sink.name)
            all_chains.append(chain)

    # 4. 交给 LLM 做 taint 分析（现有逻辑）
    taint_results = await llm_taint_analyze(all_chains, llm_client)
    return taint_results

# 默认 sink 模式列表（可被调用方覆盖）
DEFAULT_SINK_PATTERNS = [
    "execute_sql", "cursor.execute", "eval", "exec",
    "os.system", "subprocess.call", "subprocess.run",
    "open", "write", "pickle.loads", "yaml.load",
]
```

## 降级策略

```
GitNexus 可用？
  ├── Yes → 全功能模式（CLI + MCP）
  │     ├── analyze 成功？→ 继续查询
  │     └── analyze 失败？→ 重试 --force，再失败则降级
  └── No → 降级到当前名称匹配方案
       ↓
  记录降级原因到日志
  发出 WARNING 但不阻塞 pipeline
```

**降级触发条件**：
1. `gitnexus` CLI 未安装（`which gitnexus` 失败）
2. `gitnexus analyze` 失败（重试一次后）
3. MCP 连接超时（30s）
4. 查询返回空结果

## 错误处理

| 错误场景 | 处理方式 |
|----------|----------|
| CLI 未安装 | WARNING + 安装提示 + 降级到 fallback |
| 索引失败 | 重试一次 (`--force` 全量重建) + 降级 |
| MCP 连接断开 | 自动重连，最多 3 次 |
| 查询超时 | 30s 超时，跳过该查询 |
| 查询返回空 | 记录日志，继续处理其他 sink |

## 文件结构

```
shannon/
  gitnexus_client/
    __init__.py                  # 公开 API
    indexer.py                   # GitNexusIndexer
    mcp_client.py                # GitNexusMCPClient
    call_chain_builder.py        # CallChainBuilder
    models.py                    # 数据模型 (CallChain, Symbol, etc.)
  pipeline/
    __init__.py                  # 改造 build_code_index_with_gitnexus
tests/
  gitnexus_client/
    test_indexer.py              # 单元测试
    test_mcp_client.py           # 单元测试（Mock MCP 响应）
    test_call_chain_builder.py   # 单元测试
    test_integration.py          # 集成测试（需要 gitnexus CLI）
```

## 测试策略

1. **单元测试**：Mock MCP JSON-RPC 响应，验证 CallChainBuilder 逻辑
2. **集成测试**：用小型测试仓库，验证端到端 `analyze → query → taint`
3. **降级测试**：模拟 gitnexus 不可用，验证 fallback 行为
4. **性能测试**：测量索引 + 查询的总耗时

## 依赖

- `gitnexus` CLI（npm 包，`npm install -g gitnexus`）
- Python 3.11+
- 无额外 Python 包依赖（使用 stdio + subprocess，不引入 MCP SDK）

## 实施优先级

1. `models.py` — 数据模型
2. `mcp_client.py` — MCP 协议客户端
3. `indexer.py` — CLI 封装
4. `call_chain_builder.py` — 调用链构建
5. Pipeline 集成 + 降级
6. 测试
