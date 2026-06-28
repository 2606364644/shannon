# Ultimate Whitebox Scan Design: 确定性全覆盖白盒扫描架构

> **核心目标**: 入口找的全 · 路由准确 · Sink 找的全 · 调用链横向全+纵向深 · 污点参数分析全
>
> **核心原则**: 确定性工具保证**不遗漏**（覆盖度），LLM 保证**深度分析**（准确度）。两者结合实现既完整又深入的白盒审计。
>
> **核心引擎**: 以 **GitNexus** (https://github.com/abhigyanpatwari/GitNexus) 作为代码知识图谱引擎。
> GitNexus 提供 14 语言支持、Import 解析、继承链推断、Constructor 推断、Framework 检测、
> Entry Point 打分、Process 追踪、Community Detection、Confidence 评分等能力。
> 我们在此基础上叠加安全扫描专有层：参数污点追踪、Sink 发现、授权架构分析。
>
> **集成方式**: CLI + MCP 双通道 — 基础操作 (analyze/context) 通过 CLI subprocess，
> 高级操作 (Cypher/Impact/Process 追踪) 通过 MCP stdio 协议。
>
> **版本**: v2.1 (7 缺陷修正版) | 日期: 2026-06-04

---

## 一、三大"不全"问题的根因诊断

### 1.1 代码文件覆盖不全 → Sink 不全

| # | 根因 | 严重度 | 影响范围 |
|---|---|---|---|
| F1 | **单语言锁定** — `detect_language()` 只返回一种语言，polyglot 项目次要语言全部忽略 | 致命 | Python+TS 全栈项目 |
| F2 | **模板文件不扫描** — .html/.ejs/.pug/.jinja2/.hbs 等模板中的服务端逻辑和 XSS 向量完全丢失 | 致命 | 所有使用模板引擎的项目 |
| F3 | **配置文件被排除** — .yaml/.json 路由定义、中间件配置、权限配置全部跳过 | 严重 | 配置驱动路由的框架（Laravel/Django/Spring） |
| F4 | **Schema 文件不扫描** — OpenAPI/Swagger/GraphQL/Proto 定义了真实 API 入口但不被解析 | 严重 | API-first 项目 |
| F5 | **语法错误导致整个文件跳过** — 一个语法错误就让文件的全部函数丢失 | 中等 | 生成代码、混合语法 |

### 1.2 调用链不全（多条调用链场景）

| # | 根因 | 严重度 | 影响范围 |
|---|---|---|---|
| C1 | **不解析 import** — 跨文件调用完全依赖名称匹配，无法区分同名的不同文件函数 | 致命 | 全语言 |
| C2 | **入口点只靠装饰器/参数模式** — 命令式路由注册（Express `router.get()`、Laravel `Route::get()`）完全遗漏 | 致命 | TS/PHP/Go |
| C3 | **函数名歧义解析** — `name_index` 按 function_name 索引，同名函数取第一个，不区分文件 | 严重 | 全语言 |
| C4 | **动态分发/间接调用断裂** — 多态 `obj.process()`、回调、事件、反射全部丢失 | 严重 | 全语言 |
| C5 | **菱形调用路径被剪枝** — BFS 的 `visited` 集合导致 A→B→D 和 A→C→D 只保留一条路径 | 严重 | 安全审计关键场景 |
| C6 | **TypeScript arrow function 处理严重不足** — Express 路由的核心模式 `(req, res) => {}` 参数为空 | 严重 | 现代 JS/TS 项目 |
| C7 | **max_width=50 静默截断** — 大型函数调用关系被截断且无标记 | 中等 | 大型项目 |

### 1.3 污点参数不全

| # | 根因 | 严重度 | 影响范围 |
|---|---|---|---|
| P1 | **参数无类型信息** — `FuncBlock.parameters` 只是 `list[str]`，类型注解/默认值全部丢弃 | 致命 | 无法做 taint analysis |
| P2 | **无参数来源追踪** — 不知道参数来自 HTTP 请求的哪个部分（query/body/path/header） | 致命 | 无法映射 taint source |
| P3 | **无参数流分析** — 不知道参数在函数内部流向了哪里 | 致命 | 无法构建 taint propagation |
| P4 | **TS arrow function 参数为空** — Express handler `(req, res) => {}` 提取到 `parameters=[]` | 严重 | Express/Fastify/Koa |
| P5 | **Python `**kwargs` 丢失** — 可变关键字参数未被提取 | 中等 | Python 项目 |

---

## 二、根因与解决方案映射

| 问题 | 根因 | 解决方案 | 提供者 |
|---|---|---|---|
| **代码文件覆盖不全** | F1-F5 | 全文件发现 + GitNexus 14 语言 | GitNexus + 自建 |
| **入口找不全** | C2 | GitNexus EP Scoring + Schema + 框架约定 + Batch LLM | GitNexus + 自建 |
| **路由不准确** | C2+C3 | GitNexus Framework Detection + 精确 filePath UID | GitNexus |
| **Sink 找不全** | F2+F4 | 模板文件扫描 + 两步 Sink 发现 + 跨变体验证 | 自建 |
| **调用链横向不全** | C1+C5 | GitNexus Import 解析 + Process 追踪（保留菱形路径） | GitNexus |
| **调用链纵向不深** | C4+C7 | GitNexus Constructor 推断 + Heritage + Confidence 过滤 | GitNexus |
| **污点参数不全** | P1-P5 | 完整参数提取 + 框架感知来源标记 + 参数传播图 | 自建 |

---

## 三、Pipeline 全局架构

```
┌─────────────────────────────────────────────────────────────────────┐
│ Phase 0: 确定性全覆盖基础层 (GitNexus + 安全扩展)                    │
│                                                                     │
│  0.1 GitNexus 知识图谱构建 (核心引擎, CLI+MCP 双通道)               │
│      ├─ CLI: gitnexus analyze → .gitnexus/ 知识图谱                 │
│      ├─ CLI: gitnexus context --name X → 符号 360° 上下文           │
│      ├─ MCP: cypher tool → 入口点/执行流/社区查询                   │
│      ├─ MCP: impact tool → blast radius 分析                        │
│      ├─ MCP: query tool → 混合搜索 + 功能聚类                       │
│      ├─ 14 语言 Tree-sitter AST 解析                                │
│      ├─ Import/Export 解析（跨文件符号追踪）                         │
│      ├─ Heritage 解析（继承/接口/mixin）                             │
│      ├─ Constructor 推断 + self/this 接收者类型解析                  │
│      ├─ Framework 检测 + Entry Point 打分                           │
│      ├─ Process 追踪（从入口点的执行流，保留菱形路径）               │
│      └─ Community Detection（功能聚类）                             │
│                                                                     │
│  0.2 全文件发现 (安全扩展 — 模板/配置/Schema)                        │
│      ├─ 模板文件 (.html/.ejs/.pug/.hbs/.jinja2/.vue/.svelte/...)   │
│      ├─ 配置文件 (.yaml/.json/.xml/.env)                            │
│      └─ Schema 文件 (.graphql/.proto/.openapi.yaml)                 │
│                                                                     │
│  0.3 增强参数提取 (安全扩展 — 完整参数信息)                          │
│      ├─ GitNexus 提供: 函数列表、位置、调用关系、入口点              │
│      └─ 自建补充: 参数名称+类型注解+默认值+可变参数标记              │
│                                                                     │
│  0.4 入口点融合 (GitNexus EP Scoring 为主 + 补充策略)               │
│      ├─ GitNexus Entry Point Scoring (主要来源，14 语言)            │
│      ├─ Schema 文件解析 (OpenAPI/GraphQL/Proto → handler)           │
│      ├─ 框架约定 (Next.js pages/api/、Django urls.py)               │
│      └─ Batch LLM 分类 (GitNexus 低置信度 + 残余不确定)             │
│                                                                     │
│  0.5 调用图提取 (GitNexus Process 追踪)                             │
│      ├─ MCP cypher: Processes → 每个入口点的完整执行流               │
│      ├─ 菱形路径保留（GitNexus 原生）                                │
│      ├─ Confidence 评分过滤 (>0.7)                                   │
│      └─ 未解析调用标记 → Phase 1 LLM 补充                           │
│                                                                     │
│  0.6 参数传播图 (安全专有 — AST+LLM 混合算法)                       │
│      ├─ 阶段 A: Tree-sitter AST 提取 arg→param 映射                 │
│      ├─ 阶段 B: LLM 识别参数变换 (编码/解码/验证/转换)              │
│      ├─ 阶段 C: 沿调用链传播污点来源                                 │
│      └─ 输出: 完整 TaintFlow[] + 参数传播步骤                        │
│                                                                     │
│  输出: .gitnexus/ (知识图谱，全生命周期可用)                         │
│        code_index.json (函数+入口点+调用链)                          │
│        file_manifest.json (全文件清单+类型标记)                      │
│        parameter_graph.json (参数传播图)                             │
│        degradation_report.json (降级报告，仅降级模式)                │
├─────────────────────────────────────────────────────────────────────┤
│ Phase 1: Pre-Recon (LLM 深度分析，基于确定性基础)                    │
│                                                                     │
│  1.0 入口点裁决 — LLM 审查低置信度入口点，补充新发现                 │
│  1.1 架构发现 (3 个并行 Task Agent)                                  │
│  1.2 Sink 发现 — 两步法 (glob 枚举 → 逐文件分析+跨变体验证)         │
│  1.3 安全模式发现                                                   │
│  1.4 数据流发现                                                     │
│  1.5 综合报告 → pre_recon_deliverable.md                            │
│                                                                     │
│  输出: entry_points.json (裁决结果)                                  │
│        pre_recon_deliverable.md (架构分析报告+覆盖率审计)            │
│        schemas/ (API Schema 文件)                                   │
├─────────────────────────────────────────────────────────────────────┤
│ Phase 2: Recon (攻击面映射，确定性+LLM 融合)                        │
│                                                                     │
│  2.1 综合确定性数据 + Pre-Recon 报告                                │
│  2.2 API 端点清单 (含参数传播 + 共享 Handler 参数传播)              │
│  2.3 授权架构 (角色/权限/守卫目录)                                   │
│  2.4 输入向量完整性验证 (交叉验证表)                                 │
│  2.5 注入源追踪 (参数传播图驱动，禁止安全判断)                       │
│                                                                     │
│  输出: recon_deliverable.md (攻击面地图)                             │
├─────────────────────────────────────────────────────────────────────┤
│ Phase 3: 分级逐链漏洞分析 (Tiered Per-Chain Audit)                  │
│                                                                     │
│  3.0 风险评分 — 对每条 CallChainPath 计算风险分                     │
│      ├─ sink_danger: 是否到达高危 Sink (0-10)                       │
│      ├─ taint_completeness: 参数传播是否覆盖到 Sink (0-10)         │
│      ├─ auth_gap: 是否缺少 auth middleware (0-10)                   │
│      └─ depth: 调用深度 (0-10)                                      │
│                                                                     │
│  3.1 Tier 3 全量深度审计 (score ≥ 30, ≤5 条链)                     │
│      ├─ 每条链 × 5 种 Agent (injection/auth/authz/xss/ssrf)        │
│      └─ 含完整源码 + 参数传播图 + Sink 位置                         │
│                                                                     │
│  3.2 Tier 2 标准审计 (score ≥ 15, ≤20 条链)                        │
│      ├─ 每条链 × 2 种 Agent (injection + auth)                      │
│      └─ 含完整源码 + 参数传播图                                      │
│                                                                     │
│  3.3 Tier 1 轻量筛选 (score < 15, 无上限)                           │
│      ├─ 每条链 × 1 个合并 Agent (all-in-one scan)                   │
│      └─ 只输出: {vulnerable: bool, confidence, brief_reason}        │
│      └─ vulnerable=true 且 confidence>0.7 的升级到 Tier 2           │
│                                                                     │
│  3.4 后处理                                                          │
│      ├─ 结构化输出校验 (category/issue_type 白名单)                 │
│      ├─ 五元组去重                                                   │
│      └─ 覆盖度度量                                                   │
│                                                                     │
│  预算控制: max_total_llm_calls=200, max_cost_usd=$50                │
│  预估: 5×5 + 20×2 + N×1 ≈ 65+N 次调用 (N≤135)                      │
│                                                                     │
│  输出: *_analysis_deliverable.md + *_exploitation_queue.json         │
│        coverage_report.json (覆盖率报告)                             │
│        audit_tier_report.json (分级审计统计)                         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 四、Phase 0 详细设计

### 4.1 GitNexus 双通道集成 (0.1)

#### 架构概览

GitNexus 集成采用 **CLI + MCP 双通道**：
- **CLI 通道**: `gitnexus analyze`, `gitnexus context` — subprocess 调用，与 SCR-AI 现有代码兼容
- **MCP 通道**: cypher, impact, query — 通过 stdio JSON-RPC 协议，访问 GitNexus 全部 16 个 MCP 工具

```python
class GitNexusEngine:
    """GitNexus 双通道集成引擎"""
    
    def __init__(self, repo_root: Path, timeout: int = 300):
        self.repo_root = repo_root
        self.gitnexus_dir = repo_root / ".gitnexus"
        self.timeout = timeout
        self._mcp_client: GitNexusMCPClient | None = None
    
    # ── CLI 通道 (subprocess, 同步) ──────────────────────────
    
    def ensure_indexed(self) -> None:
        """gitnexus analyze — CLI 直接调用"""
        if not self.gitnexus_dir.exists():
            self._run_cli("analyze", str(self.repo_root))
    
    def get_context(self, symbol_name: str) -> dict:
        """gitnexus context --name X — CLI 直接调用
        
        与 SCR-AI GitNexusChainBuilder._query_context() 兼容。
        返回: {"outgoing": {"calls": [...]}, "incoming": {...}, "processes": [...]}
        """
        result = self._run_cli("context", "--name", symbol_name,
                               "--repo", str(self.repo_root))
        return json.loads(result)
    
    # ── MCP 通道 (stdio JSON-RPC, 异步) ─────────────────────
    
    async def start_mcp(self) -> None:
        """启动 GitNexus MCP Server (gitnexus mcp 进程)"""
        self._mcp_client = GitNexusMCPClient(self.repo_root)
        await self._mcp_client.start()  # 启动 gitnexus mcp 子进程
    
    async def stop_mcp(self) -> None:
        if self._mcp_client:
            await self._mcp_client.stop()
    
    async def get_processes(self) -> list[dict]:
        """通过 MCP cypher 工具获取所有执行流"""
        return await self._mcp_client.call_tool("cypher", {
            "query": """
            MATCH (ep:EntryPoint)-[:STEP*]->(step)
            RETURN ep.name, ep.filePath, step.name, step.filePath,
                   step.stepIndex, step.confidence
            ORDER BY ep.name, step.stepIndex
            """
        })
    
    async def get_entry_points_with_scores(self) -> list[dict]:
        """通过 MCP cypher 工具获取入口点（含 EP Scoring）"""
        return await self._mcp_client.call_tool("cypher", {
            "query": """
            MATCH (ep:EntryPoint)
            RETURN ep.name, ep.filePath, ep.score, ep.kind
            ORDER BY ep.score DESC
            """
        })
    
    async def get_impact(self, target: str, direction: str = "upstream",
                         min_confidence: float = 0.7) -> dict:
        """通过 MCP impact 工具获取 blast radius 分析"""
        return await self._mcp_client.call_tool("impact", {
            "target": target,
            "direction": direction,
        })
    
    async def get_communities(self) -> list[dict]:
        """通过 MCP query 工具获取功能聚类"""
        return await self._mcp_client.call_tool("query", {
            "query": "communities"
        })
    
    # ── 内部实现 ─────────────────────────────────────────────
    
    def _run_cli(self, command: str, *args: str) -> str:
        cmd = ["gitnexus", command, *args]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=self.timeout)
        if result.returncode != 0:
            raise GitNexusError(f"gitnexus {command} failed: {result.stderr}")
        return result.stdout


class GitNexusMCPClient:
    """GitNexus MCP 客户端 — stdio JSON-RPC 协议"""
    
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
    
    async def start(self) -> None:
        """启动 gitnexus mcp 子进程"""
        self._process = await asyncio.create_subprocess_exec(
            "gitnexus", "mcp", "--repo", str(self.repo_root),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # 发送 initialize 请求
        await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "shannon-py", "version": "1.0"},
        })
    
    async def stop(self) -> None:
        if self._process:
            self._process.terminate()
            await self._process.wait()
    
    async def call_tool(self, tool_name: str, arguments: dict) -> any:
        """调用 MCP 工具"""
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        return self._parse_tool_result(result)
    
    async def _send_request(self, method: str, params: dict) -> dict:
        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        self._process.stdin.write((json.dumps(request) + "\n").encode())
        await self._process.stdin.drain()
        
        response_line = await self._process.stdout.readline()
        return json.loads(response_line)
```

#### 降级策略：带缺陷标注的三级降级

```python
class DegradationLevel(Enum):
    FULL = "full"           # GitNexus + MCP 全量
    DEGRADED = "degraded"   # AST BFS 降级（已知缺陷标注）
    MINIMAL = "minimal"     # 纯 LLM 分析（最大缺陷标注）

class DegradationReport:
    """降级报告 — 标注降级模式下的不可用能力和预估覆盖损失"""
    
    level: DegradationLevel
    gaps: list[CoverageGap]
    
    # DEGRADED 模式下的已知缺陷
    DEGRADED_GAPS = [
        CoverageGap(
            capability="cross_file_call_resolution",
            reason="BFS 依赖函数名匹配，无法区分同名函数",
            affected_phases=["Phase 0", "Phase 3"],
            estimated_coverage_loss="30-50% of cross-file calls",
        ),
        CoverageGap(
            capability="diamond_path_preservation",
            reason="BFS visited set prunes diamond paths (A→B→D and A→C→D)",
            affected_phases=["Phase 0"],
            estimated_coverage_loss="10-20% of multi-path scenarios",
        ),
        CoverageGap(
            capability="framework_route_detection",
            reason="No Framework Detection, only decorator/annotation patterns",
            affected_phases=["Phase 0", "Phase 1"],
            estimated_coverage_loss="20-40% of imperative routes",
        ),
        CoverageGap(
            capability="entry_point_scoring",
            reason="No EP Scoring, all candidates treated equally",
            affected_phases=["Phase 0", "Phase 1"],
            estimated_coverage_loss="increased false positives",
        ),
        CoverageGap(
            capability="process_tracing",
            reason="No Process Tracing, BFS only follows direct calls",
            affected_phases=["Phase 0"],
            estimated_coverage_loss="missing dynamic dispatch paths",
        ),
    ]
    
    MINIMAL_GAPS = DEGRADED_GAPS + [
        CoverageGap(
            capability="any_static_call_graph",
            reason="No AST parsing, pure LLM analysis",
            affected_phases=["Phase 0", "Phase 1", "Phase 2", "Phase 3"],
            estimated_coverage_loss="60-80% overall",
        ),
    ]


async def build_code_index(repo_root: Path, config: Config) -> CodeIndex:
    """带降级策略的代码索引构建"""
    engine = GitNexusEngine(repo_root)
    
    try:
        engine.ensure_indexed()          # CLI: gitnexus analyze
        await engine.start_mcp()         # MCP: 启动 gitnexus mcp
        index = await extract_from_gitnexus(engine)
        index.degradation = DegradationReport(level=DegradationLevel.FULL, gaps=[])
        return index
    except (FileNotFoundError, GitNexusError) as e:
        logger.warning("GitNexus unavailable: %s. Falling back to AST BFS", e)
        index = build_code_index_ast(repo_root, config)
        index.degradation = DegradationReport(
            level=DegradationLevel.DEGRADED,
            gaps=DegradationReport.DEGRADED_GAPS,
        )
        # 将降级报告写入输出目录
        report_path = config.output_dir / "degradation_report.json"
        report_path.write_text(index.degradation.to_json(indent=2))
        logger.warning("DEGRADED MODE — Coverage gaps documented in %s", report_path)
        return index
```

### 4.2 全文件发现 (0.2)

GitNexus 处理源码文件（14 语言），安全扫描额外需要：

```python
SECURITY_FILE_TYPES = {
    "template": {".html", ".ejs", ".pug", ".hbs", ".jinja2", ".j2",
                 ".vue", ".svelte", ".erb", ".tmpl"},
    "config":   {".yaml", ".yml", ".json", ".toml", ".xml", ".env", ".ini"},
    "schema":   {".graphql", ".gql", ".proto", ".thrift"},
    "query":    {".sql"},
}

def discover_security_files(repo_root: Path) -> FileManifest:
    """发现 GitNexus 不覆盖的安全相关文件"""
    manifest = FileManifest()
    gitignore = parse_gitignore(repo_root)

    for file_path in repo_root.rglob("*"):
        if not file_path.is_file() or _should_skip(file_path, gitignore):
            continue
        file_type = classify_security_file(file_path.suffix.lower())
        if file_type:
            manifest.add(file_path, file_type)

    return manifest
```

### 4.3 增强参数提取 (0.3)

GitNexus 提供函数定义和调用关系，但不提供完整参数类型。用 Tree-sitter 补充：

```python
@dataclass
class Parameter:
    """完整参数信息 — 支持污点分析的基础"""
    name: str
    type_annotation: str | None      # "int", "str", "Request", "User"
    default_value: str | None        # "None", "''", "0"
    is_variadic: bool = False        # *args
    is_keyword_variadic: bool = False # **kwargs
    is_optional: bool = False        # TypeScript ? 修饰
    source: ParameterSource | None = None  # HTTP 来源标记

def extract_typed_parameters(file_path: Path, func_name: str,
                             start_line: int) -> list[Parameter]:
    """在 GitNexus 索引基础上，用 Tree-sitter 提取完整参数"""
    # 解析文件 → 定位函数 → 提取参数名+类型+默认值
    ...
```

### 4.4 入口点融合 (0.4)

```python
async def merge_entry_points(
    gitnexus_eps: list[dict],           # MCP cypher: 入口点查询结果
    schema_eps: list[EntryPoint],          # Schema 文件解析
    convention_eps: list[EntryPoint],      # 框架约定
    config: Config,
) -> list[UnifiedEntryPoint]:
    """融合多来源入口点"""
    unified = {}

    # 来源 1: GitNexus EP Scoring (主要)
    for ep in gitnexus_eps:
        key = f"{ep['filePath']}:{ep['name']}"
        unified[key] = UnifiedEntryPoint(
            uid=key, name=ep["name"], file_path=ep["filePath"],
            confidence=ep.get("score", 0.5), source="gitnexus",
            entry_type=ep.get("kind", "unknown"),
        )

    # 来源 2: Schema 文件 (OpenAPI/GraphQL/Proto)
    for ep in schema_eps:
        key = f"{ep.file_path}:{ep.function_name}"
        if key not in unified:
            unified[key] = UnifiedEntryPoint(
                uid=key, name=ep.function_name, file_path=ep.file_path,
                confidence=0.80, source="schema_file", entry_type=ep.entry_type,
            )

    # 来源 3: 框架约定 (Next.js pages/api/, Django urls.py)
    for ep in convention_eps:
        key = f"{ep.file_path}:{ep.function_name}"
        if key not in unified:
            unified[key] = UnifiedEntryPoint(
                uid=key, name=ep.function_name, file_path=ep.file_path,
                confidence=0.75, source="framework_convention",
                entry_type=ep.entry_type,
            )

    # 来源 4: Batch LLM (处理低置信度 < 0.5 的候选)
    low_conf = [ep for ep in unified.values() if ep.confidence < 0.5]
    if low_conf:
        llm_results = batch_classify_entry_points(low_conf, config)
        # LLM 结果更新 unified ...
    
    return list(unified.values())
```

### 4.5 调用图提取 (0.5)

```python
async def extract_call_chains(
    engine: GitNexusEngine,
    entry_points: list[UnifiedEntryPoint],
    min_confidence: float = 0.7,
) -> list[CallChainPath]:
    """从 GitNexus Process 追踪提取完整调用链"""
    
    # 通过 MCP cypher 获取所有 Process
    processes = await engine.get_processes()
    all_paths = []

    for process in processes:
        path_nodes = []
        for step in process.get("steps", []):
            if step.get("confidence", 0) >= min_confidence:
                path_nodes.append(CallChainNode(
                    function_block=FuncBlock(
                        id=FuncBlockId(step["filePath"], step["name"],
                                       step["startLine"], step["endLine"]),
                        function_name=step["name"],
                        file_path=step["filePath"],
                        source_code=step.get("source", ""),
                    ),
                    depth=step.get("stepIndex", 0),
                    call_metadata=CallMetadata(
                        confidence=step.get("confidence", 0.7),
                        call_type=step.get("relationType", "direct"),
                    ),
                ))

        if path_nodes:
            all_paths.append(CallChainPath(
                nodes=path_nodes,
                entry_point=path_nodes[0].function_block,
            ))

    return all_paths
```

### 4.6 参数传播图 (0.6 — AST+LLM 混合算法)

#### 设计概述

参数传播图采用 **三阶段混合算法**：
- **阶段 A (确定性)**: Tree-sitter AST 提取 caller 实参 → callee 形参映射
- **阶段 B (LLM)**: 识别参数变换（编码/解码/验证/转换）
- **阶段 C (确定性)**: 沿调用链传播污点来源，构建 TaintFlow

#### 参数来源标记

```python
class ParameterSource(Enum):
    QUERY_PARAM = "query"       # ?user_id=123
    PATH_PARAM = "path"         # /users/{id}
    BODY_FIELD = "body"         # JSON body field
    FORM_FIELD = "form"         # form-urlencoded field
    HEADER = "header"           # X-Custom-Header
    COOKIE = "cookie"           # session_id
    FILE_UPLOAD = "file"        # uploaded file
    SESSION_ATTR = "session"    # req.session.user_id
    INTERNAL = "internal"       # 内部生成
    UNKNOWN = "unknown"
```

#### 阶段 A: AST 确定性参数映射

```python
@dataclass
class ArgParamPair:
    """一条实参→形参映射"""
    arg_name: str           # caller 侧实参名或表达式
    param_name: str         # callee 侧形参名
    arg_source: ParameterSource | None = None  # 继承的污点来源
    transform: str | None = None               # 变换类型 (阶段 B 填充)

def build_arg_param_mapping(
    call_edge: CallEdge,
    caller_block: FuncBlock,
    callee_block: FuncBlock,
) -> list[ArgParamPair]:
    """阶段 A: 从 AST 提取 caller 实参 → callee 形参映射"""
    
    # 1. 在 caller 源码中定位调用点
    call_site_ast = locate_call_site(
        caller_block.source_code,
        call_edge.call_line - caller_block.id.start_line,
    )
    if not call_site_ast:
        return []
    
    # 2. Tree-sitter 提取实参列表
    arguments = extract_call_arguments(call_site_ast)
    # 例: process_order(user_id, request.body, timeout=30)
    # → [("user_id", "positional"), ("request.body", "positional"), ("timeout=30", "keyword")]
    
    # 3. 提取 callee 的形参列表 (含类型)
    parameters = extract_typed_parameters(
        callee_block.file_path, callee_block.function_name,
        callee_block.id.start_line,
    )
    # 例: [(order_id: int), (data: dict), (timeout: int = 60)]
    
    # 4. 位置匹配 + 关键字匹配
    pairs = []
    for i, arg in enumerate(arguments):
        if arg.kind == "positional" and i < len(parameters):
            pairs.append(ArgParamPair(
                arg_name=arg.expression,
                param_name=parameters[i].name,
            ))
        elif arg.kind == "keyword":
            match = next((p for p in parameters if p.name == arg.keyword), None)
            if match:
                pairs.append(ArgParamPair(
                    arg_name=arg.expression,
                    param_name=match.name,
                ))
    
    return pairs
```

#### 阶段 B: LLM 变换识别

```python
TRANSFORM_IDENTIFICATION_PROMPT = """\
Analyze the following argument-to-parameter mapping and identify transformations.

## Caller function: {caller_name} ({caller_file}:{caller_line})
```{language}
{caller_source}
```

## Callee function: {callee_name} ({callee_file}:{callee_line})
```{language}
{callee_source}
```

## Argument-to-Parameter Mappings:
{mappings_text}

For each mapping, classify the transformation:
- "none": direct pass-through, no change
- "encode": URL-encoding, base64, HTML-entity encoding
- "decode": URL-decoding, base64-decode, JSON.parse
- "sanitize": escape_html, parameterize_sql, validate_int, allowlist
- "convert": int(), str(), float(), bool() type conversion
- "extract": accessing a field from an object (e.g., request.body.username)
- "compose": building a new value from multiple sources

Output JSON array:
[{"arg": "...", "param": "...", "transform": "none|encode|decode|sanitize|convert|extract|compose", "confidence": 0.0-1.0}]
"""
```

#### 阶段 C: 传播图构建

```python
@dataclass
class TaintFlow:
    """一条完整的污点传播路径"""
    entry_point_id: FuncBlockId
    source_param: str                # 原始参数名
    source_type: ParameterSource     # query/body/path/...
    propagation_steps: list[PropagationStep]
    sink_function: FuncBlockId | None
    sink_type: SinkType | None       # SQL/exec/render/...

@dataclass
class PropagationStep:
    from_function: FuncBlockId
    from_param: str
    to_function: FuncBlockId
    to_param: str
    transformation: str | None       # "url_decode", "json_parse", "sanitize"
    code_location: str               # file:line

def build_parameter_propagation_graph(
    call_chains: list[CallChainPath],
    arg_param_mappings: dict[str, list[ArgParamPair]],  # edge_key → mappings
) -> ParameterPropagationGraph:
    """阶段 C: 沿调用链传播污点来源，构建完整传播图"""
    
    taint_flows: list[TaintFlow] = []
    
    for chain in call_chains:
        # 入口点参数 → HTTP 来源标记
        entry_taints = mark_entry_parameter_sources(chain.entry_point)
        
        # 初始化: 入口点的每个参数携带 HTTP 来源
        current_taints: dict[str, ParameterSource] = {
            p.name: p.source for p in entry_taints if p.source
        }
        
        # 沿调用链逐步传播
        for i in range(len(chain.nodes) - 1):
            caller = chain.nodes[i]
            callee = chain.nodes[i + 1]
            edge_key = f"{caller.function_block.id}:{callee.function_block.id}"
            mappings = arg_param_mappings.get(edge_key, [])
            
            next_taints: dict[str, ParameterSource] = {}
            
            for pair in mappings:
                if pair.arg_name in current_taints:
                    # 污点传播: arg 的来源 → param
                    source = current_taints[pair.arg_name]
                    next_taints[pair.param_name] = source
                    
                    # 记录传播步骤
                    step = PropagationStep(
                        from_function=caller.function_block.id,
                        from_param=pair.arg_name,
                        to_function=callee.function_block.id,
                        to_param=pair.param_name,
                        transformation=pair.transform,
                        code_location=f"{caller.function_block.file_path}:{caller.function_block.id.start_line}",
                    )
                    # 添加到对应的 TaintFlow...
            
            current_taints = next_taints
        
        # 最终: current_taints 中的参数到达了 Sink
        sink_node = chain.nodes[-1]
        for param_name, source in current_taints.items():
            taint_flows.append(TaintFlow(
                entry_point_id=chain.entry_point.id,
                source_param=param_name,
                source_type=source,
                propagation_steps=[],  # 从步骤中填充
                sink_function=sink_node.function_block.id,
                sink_type=classify_sink(sink_node.function_block),
            ))
    
    return ParameterPropagationGraph(taint_flows=taint_flows)
```

**利用 GitNexus 的精确调用关系**：参数传播中的 `from_function → to_function` 映射直接使用 GitNexus 提供的 filePath 精确调用关系，不再有函数名歧义问题。

---

## 五、Phase 1 详细设计：Pre-Recon LLM 深度分析

### 5.1 入口点裁决 (1.0)

```python
for ep in entry_points:
    if ep.confidence >= 0.85 and ep.source == "gitnexus":
        verdict = Verdict.CONFIRMED       # 高置信度确定性入口点
    elif ep.source in ("schema_file", "framework_convention"):
        verdict = Verdict.CONFIRMED       # Schema/约定来源可信
    elif ep.source == "llm_batch":
        verdict = Verdict.NEEDS_REVIEW    # Batch LLM 结果需主 Agent 复核
    else:
        verdict = Verdict.NEEDS_REVIEW    # 低置信度: Task Agent 审查
```

### 5.2 Sink 两步发现 (1.2)

**恢复 Shannon 的强制两步法**：

```
Step 1 — 全文件 Sink 枚举 (glob + file_manifest):
  基于 Phase 0 的 file_manifest.json，对每个模板文件和源码文件
  进行 glob 枚举，建立完整清单。

Step 2 — 逐文件分析 + 跨变体验证:
  对每个文件分析 sink 类型，区分 escaped/unescaped 模板指令。
  变体目录（brands/locales/themes）中强制检查所有变体。
  输出 Template Coverage Audit 表格。
```

### 5.3 报告结构 (10 章 + 覆盖度报告)

保持 Shannon 的结构，增加覆盖度章节：

```
## 11. 覆盖度报告 (新增)
- 已分析文件: 234/250 (93.6%)
- 未分析文件: 16 (列表+原因)
- 已覆盖入口点: 42/45 (93.3%)
- 已覆盖调用链路径: 187/210 (89.0%)
- 已追踪参数: 312/350 (89.1%)
```

---

## 六、Phase 2 详细设计：Recon 攻击面映射

### 6.1 融合确定性数据

Recon Agent 读取以下输入：
1. `pre_recon_deliverable.md` — Pre-Recon 的 LLM 分析
2. `code_index.json` — GitNexus 提取的调用链
3. `parameter_graph.json` — 参数传播图
4. `.gitnexus/` — 可通过 MCP 实时查询

### 6.2 API 端点清单增强

恢复 Shannon 的 **Shared Controller Parameter Propagation** 和 **Parameter Completeness Verification**：

```
## 4. API Endpoint Inventory

| Method | Endpoint | Required Role | Object ID Params | Auth Mechanism | All Parameters (from Parameter Graph) | Taint Sources |

**Shared Controller Parameter Propagation:** (恢复 Shannon)
When multiple routes map to the same controller handler function,
ALL query/body parameters that the handler reads must be listed
for EVERY route that uses that handler.

**Parameter Completeness Verification:** (恢复 Shannon)
For each endpoint, cross-reference parameters from:
1. Route definition (path params)
2. Validation schema (Pydantic/Zod/Joi)
3. Template variables (if renders template)
4. Function parameters (from code_index.json)
```

### 6.3 注入源追踪

```
## 9. Injection Sources

**CRITICAL — No Security Judgments:** (恢复 Shannon)
Your job is to IDENTIFY and REPORT facts — where user-controllable
input enters, where it flows, and what sink it reaches. You MUST NOT
make security judgments.

**Parameter Propagation Graph Drive:** (新增)
For each injection source, include the complete taint flow:
- Entry point: POST /api/orders (user_id → PATH_PARAM)
- Propagation: req.params.user_id → order_service.get(user_id) → db.query(sql)
- Sink: db.query() in services/order.py:42
```

### 6.4 重写 recon-static.txt

对齐 live 版本的 9 章结构（当前只有 7 章），恢复：
- Section 0 HOW TO READ THIS
- Section 6.4 Guards Directory
- Section 7 Privilege Lattice
- Section 8 Authorization Vulnerability Candidates
- "No Security Judgments" 禁令
- Input Validator Agent 含具体验证库枚举 (Zod/Joi/Pydantic)

---

## 七、Phase 3 详细设计：分级逐链漏洞分析

### 7.1 核心改变：从文档驱动改为分级调用链驱动

**当前 (Shannon)**: Vuln Agent 读取 `recon_deliverable.md`，自行搜索代码分析漏洞。

**改进**: 对每条 `CallChainPath`，先计算风险评分，再按 Tier 决定审计深度：

### 7.2 风险评分

```python
@dataclass
class ChainRiskScore:
    """调用链风险评分 → 决定审计层级"""
    chain: CallChainPath
    taint_flows: list[TaintFlow]
    
    # 评分维度 (每项 0-10)
    sink_danger: int = 0        # 是否到达高危 Sink
    taint_completeness: int = 0 # 参数传播是否覆盖到 Sink
    auth_gap: int = 0           # 是否缺少 auth middleware
    depth: int = 0              # 调用深度
    
    @property
    def total(self) -> int:
        return self.sink_danger + self.taint_completeness + self.auth_gap + self.depth
    
    @property
    def tier(self) -> int:
        if self.total >= 30: return 3  # 全量深度
        if self.total >= 15: return 2  # 标准审计
        return 1                        # 轻量筛选
    
    @classmethod
    def score(cls, chain: CallChainPath, taint_flows: list[TaintFlow],
              auth_middleware_ids: set[str]) -> "ChainRiskScore":
        sink_node = chain.nodes[-1] if chain.nodes else None
        
        # sink_danger: 检查终点是否是高危 Sink
        sink_type = classify_sink(sink_node.function_block) if sink_node else None
        sink_danger = {
            "sql_execution": 10, "command_exec": 10, "file_write": 8,
            "template_render": 7, "http_request": 6, "deserialization": 9,
        }.get(sink_type, 3) if sink_type else 0
        
        # taint_completeness: 参数传播是否到达 Sink
        reaching_flows = [f for f in taint_flows
                          if f.sink_function == sink_node.function_block.id]
        taint_completeness = min(10, len(reaching_flows) * 2)
        
        # auth_gap: 调用链中是否缺少 auth
        chain_has_auth = any(
            node.function_block.id in auth_middleware_ids
            for node in chain.nodes
        )
        auth_gap = 0 if chain_has_auth else 8
        
        # depth: 调用深度
        depth = min(10, len(chain.nodes))
        
        return cls(chain=chain, taint_flows=taint_flows,
                   sink_danger=sink_danger, taint_completeness=taint_completeness,
                   auth_gap=auth_gap, depth=depth)
```

### 7.3 分级审计执行

```python
@dataclass
class AuditBudget:
    """Phase 3 审计预算控制"""
    max_total_llm_calls: int = 200
    max_cost_usd: float = 50.0
    tier3_max_chains: int = 5
    tier2_max_chains: int = 20
    tier1_combined_agent: bool = True

async def audit_all_chains(
    call_chains: list[CallChainPath],
    parameter_graph: ParameterPropagationGraph,
    auth_middleware_ids: set[str],
    config: Config,
    budget: AuditBudget = AuditBudget(),
) -> list[VulnFinding]:
    """分级审计: Tier 3 (高风险) → Tier 2 (标准) → Tier 1 (轻量)"""
    
    # 1. 风险评分 + 分级
    scores = [ChainRiskScore.score(c, parameter_graph.by_chain(c), auth_middleware_ids)
              for c in call_chains]
    
    tier3 = sorted([s for s in scores if s.tier == 3], key=lambda s: -s.total)[:budget.tier3_max_chains]
    tier2 = sorted([s for s in scores if s.tier == 2], key=lambda s: -s.total)[:budget.tier2_max_chains]
    tier1 = [s for s in scores if s.tier == 1]
    
    calls_used = 0
    all_findings: list[VulnFinding] = []
    
    # 2. Tier 3: 全量深度审计 (≤5 链 × 5 Agent = ≤25 调用)
    semaphore = asyncio.Semaphore(config.max_concurrency)
    for score in tier3:
        if calls_used >= budget.max_total_llm_calls: break
        for agent_type in ["injection", "auth", "authz", "xss", "ssrf"]:
            finding = await _audit_single(score.chain, agent_type, parameter_graph, semaphore)
            all_findings.extend(finding)
            calls_used += 1
    
    # 3. Tier 2: 标准审计 (≤20 链 × 2 Agent = ≤40 调用)
    for score in tier2:
        if calls_used >= budget.max_total_llm_calls: break
        for agent_type in ["injection", "auth"]:
            finding = await _audit_single(score.chain, agent_type, parameter_graph, semaphore)
            all_findings.extend(finding)
            calls_used += 1
    
    # 4. Tier 1: 轻量筛选 (每链 1 个合并 Agent)
    for score in tier1:
        if calls_used >= budget.max_total_llm_calls: break
        preliminary = await _audit_combined(score.chain, parameter_graph, semaphore)
        calls_used += 1
        # 升级: vulnerable=true 且 confidence>0.7 → 补充 injection+auth
        if preliminary and preliminary[0].get("vulnerable") and preliminary[0].get("confidence", 0) > 0.7:
            for agent_type in ["injection", "auth"]:
                if calls_used >= budget.max_total_llm_calls: break
                finding = await _audit_single(score.chain, agent_type, parameter_graph, semaphore)
                all_findings.extend(finding)
                calls_used += 1
        else:
            all_findings.extend(preliminary)
    
    logger.info("Audit complete: %d LLM calls used (budget: %d), %d findings",
                calls_used, budget.max_total_llm_calls, len(all_findings))
    
    return deduplicate_findings(all_findings)
```

### 7.4 审计 Agent Prompt 格式

#### Tier 2/3 专用 Agent 输入

```
## Call Chain
POST /api/orders/:id → delete_order(id) → cancel_order(order_id) → db.raw_query(sql)

## Function 1: delete_order (routes/order.py:15-20)
[完整源码]

## Function 2: cancel_order (services/order.py:42-55)
[完整源码]

## Function 3: db.raw_query (utils/db.py:10-15)
[完整源码]

## Taint Flow (from Parameter Propagation Graph)
- id (PATH_PARAM) → order_id → order_id → sql (unsanitized)

## Sinks in this chain
- db.raw_query(): SQL execution sink at utils/db.py:12

## Your Focus: {vuln_type}
[...具体漏洞类型的分析指引...]
```

#### Tier 1 合并 Agent 输入

```
## Quick Security Scan

Analyze this call chain for ALL vulnerability types at a high level.

## Call Chain: {chain_summary}
[完整源码，同上]

## Taint Flow: {taint_summary}

For each vulnerability type, output:
{"type": "injection|auth|authz|xss|ssrf", "vulnerable": bool, "confidence": 0.0-1.0, "brief_reason": "one sentence"}

Only provide detailed analysis for types where vulnerable=true AND confidence>0.7.
```

### 7.5 结构化输出校验

```python
VALID_INJECTION_CATEGORIES = {
    "injection": [
        "sql_injection", "command_injection", "path_traversal",
        "ssti", "ssrf", "insecure_deserialization",
    ],
}
VALID_AUTHZ_CATEGORIES = {
    "horizontal_privilege_escalation": ["idor", "tenant_isolation", "cross_org_access"],
    "vertical_privilege_escalation": ["missing_role_check", "role_tampering"],
}

def parse_and_validate_findings(response: str, agent_type: str) -> list[VulnFinding]:
    """校验 category/issue_type 合法性，不在白名单的丢弃"""
    data = extract_json(response)
    valid = CATEGORY_MAP[agent_type]
    return [VulnFinding(**item) for item in data.get("findings", [])
            if item.get("category") in valid
            and item.get("issue_type") in valid.get(item["category"], [])]
```

### 7.6 五元组去重

```python
def deduplicate_findings(findings: list[VulnFinding]) -> list[VulnFinding]:
    """(入口点, 类别, 问题类型, 脆弱函数, 调用链路径)"""
    seen: set[tuple] = set()
    unique = []
    for f in findings:
        key = (f.entry_point_id, f.category, f.issue_type,
               f.vulnerable_function_id,
               tuple(n.function_name for n in f.call_chain_path))
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique
```

---

## 八、GitNexus 在全生命周期中的持续使用

| Phase | 通道 | GitNexus 用途 |
|---|---|---|
| **Phase 0** | CLI+MCP | 索引构建(analyze)、调用关系(context)、入口点/执行流(cypher)、社区(communities) |
| **Phase 1** | CLI+MCP | `query` 搜索认证组件；`context` 获取安全函数调用关系；`impact` 分析 Sink blast radius |
| **Phase 2** | MCP | `impact` 分析端点 blast radius；`query` 映射功能模块 |
| **Phase 3** | MCP | 为每条调用链提供完整源码上下文；`impact` 验证 sink 影响范围 |
| **Phase 4** | MCP | `query` 生成架构概览；Community Detection 辅助模块化描述 |

---

## 九、数据流总结

```
源代码仓库
    │
    ▼
Phase 0: 确定性基础
    ├─ .gitnexus/                    (GitNexus 知识图谱)
    ├─ code_index.json               (FuncBlock + EntryPoint + CallChain)
    ├─ file_manifest.json            (全文件清单+类型)
    ├─ parameter_graph.json          (TaintFlow[] + 参数传播步骤)
    ├─ degradation_report.json       (降级报告，仅降级模式)
    └─ coverage_baseline.json        (覆盖率基线)
    │
    ▼
Phase 1: Pre-Recon (LLM)
    ├─ entry_points.json             (裁决结果)
    ├─ pre_recon_deliverable.md      (架构分析+Sink清单+覆盖率审计)
    └─ schemas/                      (API Schema 文件)
    │
    ▼
Phase 2: Recon (LLM)
    └─ recon_deliverable.md          (攻击面地图+参数完整性+注入源)
    │
    ▼
Phase 3: 分级逐链 Vuln (LLM per chain, tiered)
    ├─ *_analysis_deliverable.md     (每类漏洞分析报告)
    ├─ *_exploitation_queue.json     (可利用漏洞队列)
    ├─ coverage_report.json          (最终覆盖率)
    ├─ audit_tier_report.json        (分级审计统计)
    └─ findings_deduped.json         (去重后的全部发现)
```

---

## 十、与现有系统的改造范围

| 模块 | 改造类型 | 说明 |
|---|---|---|
| `code_index/` | **重构** | 以 GitNexus 为核心，增强模型，新增 parameter_graph |
| `code_index/gitnexus_engine.py` | **新增** | GitNexus CLI+MCP 双通道封装层 |
| `code_index/gitnexus_mcp.py` | **新增** | MCP stdio JSON-RPC 客户端 |
| `code_index/parameter_graph.py` | **新增** | 三阶段 AST+LLM 参数传播图构建 |
| `code_index/arg_param_mapper.py` | **新增** | Tree-sitter arg→param 映射 |
| `code_index/file_discovery.py` | **新增** | 安全文件发现 |
| `code_index/parser.py` | **增强** | 参数类型提取（在 GitNexus 索引上补充） |
| `code_index/entry_points.py` | **简化** | 入口点融合（GitNexus 为主） |
| `code_index/risk_scorer.py` | **新增** | 调用链风险评分 (Phase 3 Tier 分级) |
| `code_index/degradation.py` | **新增** | 降级报告 + Coverage Gap 标注 |
| `prompts/pre-recon-code.txt` | **增强** | 恢复两步 Sink 分析、覆盖度报告 |
| `prompts/recon.txt` | **增强** | 恢复 No Security Judgments、参数传播 |
| `prompts/recon-static.txt` | **重写** | 对齐 live 版本 9 章结构 |
| `prompts/vuln-*.txt` | **重构** | 调用链驱动 + 分级审计 + 结构化输出 |
| `prompts/audit-tier1.txt` | **新增** | Tier 1 合并 Agent prompt |
| `prompts/transform-identify.txt` | **新增** | 参数变换识别 prompt |
| `pipeline/workflows.py` | **增强** | Phase 3 分级审计编排 + 预算控制 |

**向后兼容**:
- GitNexus 不可用时降级到当前 AST BFS（带缺陷标注）
- `code_index.json` 新增字段，旧字段保持不变
- `entry_points.json` 格式不变

---

## 十一、实施优先级

### P0 — 基础层核心（解决"不全"的根因） — ~6 天

| # | 任务 | 工期 | 说明 |
|---|---|---|---|
| 1 | GitNexus CLI 集成 | 1 天 | subprocess 封装（analyze + context），与 SCR-AI 兼容 |
| 2 | GitNexus MCP 客户端 | 1 天 | stdio JSON-RPC 客户端，cypher/impact/query 工具调用 |
| 3 | GitNexus Process → CallChainPath 转换 | 0.5 天 | MCP cypher 执行流转为内部数据模型 |
| 4 | GitNexus EP Scoring → EntryPoint 融合 | 0.5 天 | 入口点去重和合并 |
| 5 | 完整参数提取 (Tree-sitter) | 1 天 | 在 GitNexus 索引上补充参数类型 |
| 6 | 参数 HTTP 来源标记 | 0.5 天 | 框架感知的参数来源映射 |
| 7 | 全文件发现 (模板/配置/Schema) | 1 天 | GitNexus 不覆盖的安全文件 |
| 8 | 降级策略 + Coverage Gap 报告 | 0.5 天 | 带缺陷标注的降级 + 报告输出 |

### P1 — 增强层（提升覆盖度和准确度） — ~4 天

| # | 任务 | 工期 | 说明 |
|---|---|---|---|
| 9 | 阶段 A: AST arg→param 映射 | 1.5 天 | Tree-sitter 提取实参/形参 + 匹配算法 |
| 10 | 阶段 B: LLM 参数变换识别 | 1 天 | transform-identify prompt + 解析 |
| 11 | 阶段 C: 参数传播图构建 | 1 天 | 沿调用链传播污点来源 |
| 12 | 恢复 Pre-Recon 两步 Sink 分析 | 0.5 天 | glob + 跨变体验证 |
| 13 | Batch LLM 入口点分类 | 0.5 天 | 低置信度候选处理 |
| 14 | 恢复 Recon "No Security Judgments" | 0.5 天 | prompt 修改 |

### P2 — 深度优化 — ~5 天

| # | 任务 | 工期 | 说明 |
|---|---|---|---|
| 15 | 调用链风险评分 (ChainRiskScore) | 1 天 | sink_danger + taint_completeness + auth_gap + depth |
| 16 | 分级审计编排 (Tier 1/2/3) | 1.5 天 | 预算控制 + Tier 升级逻辑 |
| 17 | Tier 1 合并 Agent prompt | 0.5 天 | all-in-one scan prompt |
| 18 | Tier 2/3 专用 Agent 改造 | 1 天 | 调用链驱动 + 参数传播图输入 |
| 19 | 结构化输出校验 | 0.5 天 | category/issue_type 白名单 |
| 20 | 五元组去重 | 0.5 天 | |
| 21 | 恢复 Shared Controller 参数传播 | 0.5 天 | |
| 22 | 重写 recon-static.txt | 1 天 | 对齐 9 章结构 |
| 23 | 覆盖度报告 | 0.5 天 | audit_tier_report.json |

**总计**: P0 ~6 天 + P1 ~4 天 + P2 ~5 天 = **~15 天**

**成本预估**: Phase 3 审计 ≈ 65+N 次 LLM 调用 (N≤135)，按 Claude Sonnet ~$3/M input 计算 ≈ $15-40

---

## 十二、验证方案

### 12.1 基准测试用例

| 场景 | 验证目标 | 期望 |
|---|---|---|
| Express 路由 `(req, res) => {}` | 入口点检测 + 参数提取 | GitNexus EP Scoring 检测到 |
| Python `**kwargs` 传递 | 参数传播 | AST 提取 + 传播到 sink |
| 多文件 `from utils import helper` | 跨文件调用 | GitNexus Import 解析连接 |
| `obj.process()` 多态 | 调用链完整性 | GitNexus Constructor 推断解析 |
| A→B→D + A→C→D 菱形 | 路径保留 | 两条路径都保留 |
| Laravel `Route::get('/path', ...)` | 命令式路由 | GitNexus Framework Detection |
| EJS `<%= %>` vs `<%- %>` | 模板 Sink 分析 | 区分 escaped/unescaped |
| `req.body.user_id → SQL query` | 完整污点追踪 | 参数传播图覆盖 |
| Next.js `pages/api/*.ts` | 框架约定 | 补充策略检测到 |
| GitNexus 不可用 | 降级策略 | Coverage Gap Report 输出 |
| 50 入口点 × 4 链场景 | Phase 3 成本控制 | ≤200 LLM 调用 |

### 12.2 覆盖度度量

每个 Phase 结束后输出覆盖率：

```
Phase 0 → 文件覆盖率、函数覆盖率、入口点覆盖率、调用链路径数
           degradation_report.json (如果降级)
Phase 1 → Sink 覆盖率 (发现的 Sink / 总模板文件)
Phase 2 → 参数覆盖率 (枚举的参数 / 完整参数列表)
Phase 3 → 调用链审计覆盖率 (已审计链 / 总链数)
           audit_tier_report.json (Tier 分布 + 调用数)
```

---

## 附录 A：Prompt 模板骨架

### A.1 Tier 1 合并审计 Prompt

```
SYSTEM: You are a security auditor performing a quick multi-type vulnerability scan.

INPUT FORMAT:
- Call chain: {chain_summary}
- Full source code for each function in the chain
- Taint flow summary (if available)

OUTPUT FORMAT (strict JSON):
{{
  "findings": [
    {{
      "type": "injection|auth|authz|xss|ssrf",
      "vulnerable": true|false,
      "confidence": 0.0-1.0,
      "brief_reason": "one sentence explaining why"
    }}
  ]
}}

RULES:
1. Only report findings where vulnerable=true AND confidence>0.7
2. For each such finding, include the specific code location
3. Do NOT make security judgments about sanitization adequacy
4. Do NOT report issues where a clear security control exists
```

### A.2 参数变换识别 Prompt

```
SYSTEM: You are a code analysis assistant identifying parameter transformations.

INPUT:
- Caller function source
- Callee function source  
- Argument-to-parameter mappings

OUTPUT (strict JSON array):
[{{
  "arg": "expression from caller",
  "param": "parameter name in callee",
  "transform": "none|encode|decode|sanitize|convert|extract|compose",
  "confidence": 0.0-1.0
}}]

RULES:
1. "none" = direct pass-through with no transformation
2. "sanitize" = any intentional security measure (escape, validate, parameterize)
3. "extract" = accessing a sub-field (e.g., request.body.username)
4. Only mark "sanitize" when the transformation is clearly a security control
```

### A.3 Phase 1 Pre-Recon Prompt 增强

在现有 pre-recon-code.txt 基础上新增：

```
## Phase 0 Coverage Data (from code_index.json + file_manifest.json)

### Entry Points (from GitNexus EP Scoring)
{entry_points_table}

### Call Chain Statistics
- Total chains: {total_chains}
- Average depth: {avg_depth}
- Max depth: {max_depth}
- Unresolved calls: {unresolved_count}

### File Coverage
- Source files (GitNexus): {gitnexus_files}
- Template files: {template_files}
- Config files: {config_files}
- Schema files: {schema_files}
- Total: {total_files}

### Degradation Status
{degradation_warning_or_none}
```

---

*v2.1 (7 缺陷修正版) | 日期: 2026-06-04 | 修正: D1 GitNexus URL、D2 CLI+MCP 双通道、D3 SCR-AI 兼容、D4 参数传播算法、D5 分级审计+成本控制、D6 带缺陷标注降级、D7 Prompt 骨架*
