# GitNexus 集成设计：最终白盒扫描架构核心引擎

> **文档性质**: `2026-06-04-ultimate-whitebox-scan-design.md` 的补充更新
>
> **核心变更**: 以 GitNexus 替代自建 BFS 作为代码知识图谱引擎

---

## 一、为什么必须用 GitNexus

### 1.1 自建 BFS 的致命缺陷

当前 shannon-py 的 `call_graph.py` 自建 BFS 存在以下**不可修复的架构缺陷**：

| 缺陷 | 根因 | 修复成本 |
|---|---|---|
| 不解析 import | 无 import 解析模块 | 2-3 天 + 每种语言独立维护 |
| 函数名全局歧义 | `name_index` 按 function_name 索引 | 需重写整个解析层 |
| 动态分发断裂 | 无类型推断 | 需构建类型系统 |
| 菱形路径丢失 | visited 集合剪枝 | 需重写 BFS |
| 单语言锁定 | parser 只处理主语言 | 需每语言独立 parser |
| 5 语言 x N 个问题 | 每种语言独立维护 | 持续维护成本 |

### 1.2 GitNexus 直接解决的能力矩阵

| GitNexus 能力 | 解决的问题 | 对应根因编号 |
|---|---|---|
| **Import 解析** (14 语言) | 跨文件调用断裂 | C1 |
| **精确 filePath + symbol UID** | 函数名歧义 | C3 |
| **Constructor 推断 + self/this 解析** | 动态分发/多态 | C4 |
| **Heritage 解析** (extends/implements) | 接口调用断裂 | C4 |
| **Framework 检测** (AST-based) | 命令式路由遗漏 | C2 |
| **Entry Point Scoring** | 入口点不完整 | F3 |
| **Process 追踪** (execution flows) | 调用链不全 | C5, C7 |
| **Confidence 评分** | 调用关系不可信 | 全局 |
| **14 语言 Tree-sitter** | 单语言锁定 | F1 |
| **Community Detection** | 无模块级分析 | 新增能力 |

### 1.3 SCR-AI 对 GitNexus 的使用程度

SCR-AI 仅使用了 GitNexus 约 **10%** 的能力：

```
SCR-AI 使用:  gitnexus context --name X → callers/callees → 自建 BFS
GitNexus 未用: Import 解析、Heritage、Constructor 推断、Framework 检测、
              Entry Point Scoring、Process 追踪、Community Detection、
              Impact Analysis、Cypher 查询、Confidence 评分
```

我们的设计目标是利用 GitNexus **80%+** 的能力。

---

## 二、GitNexus 集成架构

### 2.1 数据流

```
源代码仓库
    │
    ▼
┌─────────────────────────────────────────────────┐
│ GitNexus analyze (一次索引)                       │
│                                                  │
│ 输入: repo_root                                  │
│ 命令: gitnexus analyze --force                   │
│ 超时: 300s (首次), 60s (增量)                     │
│                                                  │
│ 输出: .gitnexus/ 知识图谱                         │
│  ├─ LadybugDB 图数据库                           │
│  ├─ 全量函数/类/接口定义                          │
│  ├─ Import/Export 关系图                         │
│  ├─ 调用关系图 (含 confidence)                    │
│  ├─ 继承/实现关系                                 │
│  ├─ Entry Points (含打分)                         │
│  ├─ Processes (执行流)                            │
│  └─ Communities (功能聚类)                        │
└─────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────┐
│ GitNexus Query Layer (通过 CLI 或 MCP)            │
│                                                  │
│ gitnexus context --name X --repo Y               │
│   → symbol UID, filePath, kind, incoming/outgoing│
│                                                  │
│ gitnexus impact --target X --direction upstream  │
│   → blast radius, depth-grouped, confidence      │
│                                                  │
│ gitnexus query "authentication middleware"        │
│   → process-grouped hybrid search                │
│                                                  │
│ Cypher queries for complex relationships          │
└─────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────┐
│ 安全扫描扩展层 (我们自建，GitNexus 不提供)         │
│                                                  │
│ ├─ 全文件发现 (模板/配置/Schema)                   │
│ ├─ 完整参数提取 (类型+注解+默认值)                  │
│ ├─ 参数 HTTP 来源标记 (query/body/path/header)    │
│ ├─ 参数传播图 (taint tracking)                    │
│ ├─ 模板 Sink 枚举 (glob → 逐文件分析)             │
│ └─ 授权架构分析 (角色/权限/守卫)                   │
└─────────────────────────────────────────────────┘
```

### 2.2 GitNexus CLI 集成方式

```python
class GitNexusEngine:
    """GitNexus 知识图谱引擎的 Python 封装"""

    def __init__(self, repo_root: Path, timeout: int = 300):
        self.repo_root = repo_root
        self.gitnexus_dir = repo_root / ".gitnexus"
        self.timeout = timeout

    def ensure_indexed(self) -> None:
        """确保仓库已索引。已有索引则跳过，否则执行 analyze。"""
        if not self.gitnexus_dir.exists():
            self._run_cli("analyze", "--force")

    def get_context(self, symbol_name: str) -> GitNexusContext:
        """获取符号的 360 度上下文 (callers + callees + processes)"""
        result = self._run_cli("context", "--name", symbol_name, "--repo", str(self.repo_root))
        return GitNexusContext.from_json(result)

    def get_impact(self, target: str, direction: str = "upstream",
                   min_confidence: float = 0.7) -> GitNexusImpact:
        """获取 blast radius 分析"""
        result = self._run_cli("impact", "--target", target,
                               "--direction", direction, "--repo", str(self.repo_root))
        return GitNexusImpact.from_json(result)

    def get_processes(self) -> list[GitNexusProcess]:
        """获取所有执行流 (从入口点的完整调用链)"""
        # 通过 Cypher 查询获取所有 process
        cypher = """
        MATCH (ep:EntryPoint)-[:STEP*]->(step)
        RETURN ep, step
        ORDER BY ep.name, step.stepIndex
        """
        result = self._run_cli("cypher", "--query", cypher, "--repo", str(self.repo_root))
        return [GitNexusProcess.from_json(p) for p in json.loads(result)]

    def get_entry_points(self) -> list[GitNexusEntryPoint]:
        """获取所有入口点（含打分）"""
        cypher = """
        MATCH (ep:EntryPoint)
        RETURN ep.name, ep.filePath, ep.score, ep.kind
        ORDER BY ep.score DESC
        """
        result = self._run_cli("cypher", "--query", cypher, "--repo", str(self.repo_root))
        return [GitNexusEntryPoint.from_json(ep) for ep in json.loads(result)]

    def get_communities(self) -> list[GitNexusCommunity]:
        """获取功能聚类"""
        cypher = """
        MATCH (c:Community)<-[:MEMBER_OF]-(fn)
        RETURN c.heuristicLabel, collect({name: fn.name, file: fn.filePath})
        """
        result = self._run_cli("cypher", "--query", cypher, "--repo", str(self.repo_root))
        return [GitNexusCommunity.from_json(c) for c in json.loads(result)]

    def query_search(self, query: str) -> list[dict]:
        """Process-grouped hybrid search (BM25 + semantic)"""
        result = self._run_cli("query", "--query", query, "--repo", str(self.repo_root))
        return json.loads(result)

    def _run_cli(self, command: str, *args: str) -> str:
        cmd = ["gitnexus", command, *args]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        if result.returncode != 0:
            raise GitNexusError(f"gitnexus {command} failed: {result.stderr}")
        return result.stdout
```

---

## 三、更新后的 Phase 0 详细设计

### 3.1 Phase 0.1: GitNexus 知识图谱构建

```python
async def build_knowledge_graph(repo_root: Path, config: Config) -> GitNexusIndex:
    """
    使用 GitNexus 构建完整知识图谱。

    覆盖: 14 语言 AST 解析 + Import 解析 + Heritage + Constructor 推断 +
          Framework 检测 + Entry Point 打分 + Process 追踪 + Community Detection

    这一步替代了原设计中的:
    - 3.3 Import 图构建 (GitNexus 内置)
    - 3.5 多策略调用图构建 (GitNexus Process 追踪)
    - 部分 3.4 入口点检测 (GitNexus Framework Detection + EP Scoring)
    """
    engine = GitNexusEngine(repo_root, timeout=config.gitnexus_analyze_timeout)

    try:
        engine.ensure_indexed()
    except FileNotFoundError:
        raise RuntimeError(
            "GitNexus CLI not found. Install with: npm install -g gitnexus"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"GitNexus analyze timed out after {config.gitnexus_analyze_timeout}s"
        )

    # 提取结构化数据
    entry_points = engine.get_entry_points()      # 入口点 + 打分
    processes = engine.get_processes()             # 执行流 (完整调用链)
    communities = engine.get_communities()          # 功能聚类

    return GitNexusIndex(
        engine=engine,
        entry_points=entry_points,
        processes=processes,
        communities=communities,
    )
```

### 3.2 Phase 0.2: 全文件发现 (安全扫描扩展)

GitNexus 的 `analyze` 主要处理源码文件。安全扫描还需要扫描以下 GitNexus 可能不处理的文件：

```python
# GitNexus 处理: .py, .ts, .js, .go, .java, .php, .rb, .rs, .c, .cpp, ...
# 我们补充扫描:
SECURITY_FILE_TYPES = {
    "template": {".html", ".ejs", ".pug", ".hbs", ".jinja2", ".j2", ".vue", ".svelte"},
    "config": {".yaml", ".yml", ".json", ".toml", ".xml", ".env", ".ini"},
    "schema": {".graphql", ".gql", ".proto", ".thrift"},
    "query": {".sql"},
}

def discover_security_files(repo_root: Path, gitignore_patterns: list[str]) -> FileManifest:
    """发现 GitNexus 不覆盖的安全相关文件"""
    manifest = FileManifest()
    for file_path in repo_root.rglob("*"):
        suffix = file_path.suffix.lower()
        file_type = classify_security_file(suffix)
        if file_type and not _should_skip(file_path, gitignore_patterns):
            manifest.add(file_path, file_type)
    return manifest
```

### 3.3 Phase 0.3: 增强参数提取

GitNexus 提供函数定义和调用关系，但不提供完整的参数类型信息。我们需要在 GitNexus 索引之上补充参数提取：

```python
def enhance_parameters(
    gitnexus_index: GitNexusIndex,
    repo_root: Path,
) -> dict[str, list[Parameter]]:
    """
    在 GitNexus 函数索引之上，用 Tree-sitter 提取完整参数信息。

    GitNexus 提供: 函数列表、文件路径、位置、调用关系
    我们补充: 参数名称+类型注解+默认值+可变参数标记
    """
    enhanced = {}
    for entry_point in gitnexus_index.entry_points:
        # 用 Tree-sitter 解析入口点函数的完整参数
        params = extract_typed_parameters(
            Path(entry_point.file_path),
            entry_point.name,
            entry_point.start_line,
        )
        enhanced[entry_point.uid] = params

    return enhanced
```

### 3.4 Phase 0.4: 入口点融合

```python
def merge_entry_points(
    gitnexus_eps: list[GitNexusEntryPoint],  # GitNexus 的入口点打分
    schema_eps: list[EntryPoint],             # Schema 文件解析的入口点
    convention_eps: list[EntryPoint],         # 框架约定检测的入口点
) -> list[UnifiedEntryPoint]:
    """
    融合多来源的入口点，去重并标注来源。
    """
    unified = {}

    # 来源 1: GitNexus (主要) — 已包含 Framework Detection + EP Scoring
    for ep in gitnexus_eps:
        unified[ep.uid] = UnifiedEntryPoint(
            uid=ep.uid,
            name=ep.name,
            file_path=ep.filePath,
            confidence=ep.score,
            source="gitnexus",
            entry_type=ep.kind,
        )

    # 来源 2: Schema 文件 — OpenAPI/GraphQL/Proto 定义的 API
    for ep in schema_eps:
        key = f"{ep.file_path}:{ep.function_name}"
        if key not in unified:
            unified[key] = UnifiedEntryPoint(
                uid=key, name=ep.function_name, file_path=ep.file_path,
                confidence=0.80, source="schema_file",
                entry_type=ep.entry_type,
            )

    # 来源 3: 框架约定 — 目录结构推断
    for ep in convention_eps:
        key = f"{ep.file_path}:{ep.function_name}"
        if key not in unified:
            unified[key] = UnifiedEntryPoint(
                uid=key, name=ep.function_name, file_path=ep.file_path,
                confidence=0.75, source="framework_convention",
                entry_type=ep.entry_type,
            )

    # 来源 4: Batch LLM — 处理 GitNexus 低置信度 (< 0.5) 的候选
    low_confidence = [ep for ep in unified.values() if ep.confidence < 0.5]
    if low_confidence:
        llm_results = batch_classify_entry_points(low_confidence, config)
        for result in llm_results:
            # LLM 结果更新或补充
            ...

    return list(unified.values())
```

### 3.5 Phase 0.5: 调用图提取 (GitNexus Process)

```python
def extract_call_chains(
    gitnexus_index: GitNexusIndex,
    entry_points: list[UnifiedEntryPoint],
    min_confidence: float = 0.7,
) -> list[CallChainPath]:
    """
    从 GitNexus Process 追踪结果提取完整调用链。

    关键优势:
    1. 菱形路径保留 — GitNexus Process 追踪不剪枝
    2. Import 解析 — 跨文件调用由 GitNexus 保证
    3. Heritage 解析 — 继承/接口调用链不断裂
    4. Confidence 评分 — 过滤低质量调用关系
    5. 无需自建 BFS — GitNexus 已构建完整执行流
    """
    all_paths = []

    for process in gitnexus_index.processes:
        # process 已经是从入口点到 sink 的完整路径
        # 每个 step 包含: symbol UID, filePath, confidence
        path_nodes = []
        for step in process.steps:
            if step.confidence >= min_confidence:
                path_nodes.append(CallChainNode(
                    function_block=FuncBlock(
                        id=FuncBlockId(step.file_path, step.name, step.start_line, step.end_line),
                        function_name=step.name,
                        file_path=step.file_path,
                        source_code=step.source,  # 需要从文件读取
                    ),
                    depth=step.depth,
                    call_metadata=CallMetadata(
                        confidence=step.confidence,
                        call_type=step.relation_type,  # CALLS, IMPORTS, EXTENDS, etc.
                    ),
                ))

        if path_nodes:
            all_paths.append(CallChainPath(
                nodes=path_nodes,
                entry_point=path_nodes[0].function_block,
            ))

    return all_paths
```

### 3.6 Phase 0.6: 参数传播图 (不变，GitNexus 不提供)

参数传播图的设计与原文档 **Section 3.6 完全相同**，因为 GitNexus 不提供参数级别的污点追踪。这部分仍然需要自建。

唯一的改变是参数映射利用了 GitNexus 的精确调用关系（含 filePath），不再有函数名歧义问题。

---

## 四、更新后的实施优先级

### 原方案的 P0 项（已被 GitNexus 取消或大幅简化）

| 原方案 | 状态 | 说明 |
|---|---|---|
| Import 图构建 (2-3 天) | **取消** | GitNexus 内置 |
| 多策略调用图构建 (复杂) | **取消** | GitNexus Process 追踪 |
| 函数名歧义消除 (0.5 天) | **取消** | GitNexus 精确 filePath + UID |
| 菱形路径保留 (1 天) | **取消** | GitNexus 原生支持 |
| 多策略入口点检测 (2 天) | **大幅简化** | GitNexus EP Scoring 为主 |

### 新的 P0 项

| # | 任务 | 工期 | 说明 |
|---|---|---|---|
| 1 | **GitNexus CLI 集成** | 1 天 | Python 封装层，索引构建 + 查询接口 |
| 2 | **GitNexus Process → CallChainPath 转换** | 1 天 | 将 GitNexus 执行流转为内部数据模型 |
| 3 | **GitNexus EP Scoring → EntryPoint 融合** | 0.5 天 | 入口点去重和合并 |
| 4 | **完整参数提取 (Tree-sitter)** | 1 天 | 在 GitNexus 索引上补充参数类型 |
| 5 | **参数 HTTP 来源标记** | 1 天 | 框架感知的参数来源映射 |
| 6 | **全文件发现 (模板/配置/Schema)** | 1 天 | GitNexus 不覆盖的安全文件 |

### P1 项（不变）

| # | 任务 | 工期 |
|---|---|---|
| 7 | 参数传播图 | 2-3 天 |
| 8 | Batch LLM 入口点分类 | 1 天 |
| 9 | 恢复 Pre-Recon 两步 Sink 分析 | 0.5 天 |

### P2 项（不变）

| # | 任务 | 工期 |
|---|---|---|
| 10 | 恢复 Recon "No Security Judgments" | 0.5 天 |
| 11 | 恢复 Shared Controller 参数传播 | 0.5 天 |
| 12 | 重写 recon-static.txt | 1 天 |
| 13 | Vuln Agent 逐链分析改造 | 2 天 |
| 14 | 结构化输出校验 | 1 天 |
| 15 | 五元组去重 | 0.5 天 |
| 16 | 覆盖度报告 | 1 天 |

### 总工期对比

| 方案 | P0 工期 | 全部工期 |
|---|---|---|
| 原方案 (自建 BFS) | ~8 天 | ~18 天 |
| **新方案 (GitNexus)** | **~5.5 天** | **~14 天** |

---

## 五、GitNexus 降级策略

### 5.1 GitNexus 不可用时的降级

```python
def build_code_index(repo_root: Path, config: Config) -> CodeIndex:
    """
    构建代码索引，支持 GitNexus → AST BFS 降级。
    """
    try:
        # 尝试使用 GitNexus (首选)
        gitnexus_index = build_knowledge_graph(repo_root, config)
        return convert_gitnexus_to_code_index(gitnexus_index)
    except (FileNotFoundError, GitNexusError) as e:
        logger.warning(f"GitNexus unavailable: {e}. Falling back to AST BFS.")
        # 降级到当前的 AST BFS 方案
        return build_code_index_ast(repo_root, config)
```

### 5.2 查询级别降级

```python
def resolve_call(caller: FuncBlock, callee_name: str, engine: GitNexusEngine | None):
    """查询级别: GitNexus 成功则用，失败则降级到名称匹配"""
    if engine:
        try:
            context = engine.get_context(callee_name)
            if context.outgoing.calls:
                return context.outgoing.calls
        except Exception:
            pass

    # 降级: 同文件名称匹配
    return _find_in_file(caller.file_path, callee_name, all_blocks)
```

---

## 六、GitNexus 在后续 Phase 中的持续使用

GitNexus 知识图谱不是一次性工具，在整个扫描生命周期中持续提供价值：

| Phase | GitNexus 用途 |
|---|---|
| **Phase 0** | 索引构建，入口点检测，调用图，功能聚类 |
| **Phase 1 (Pre-Recon)** | `query "authentication"` 搜索认证相关函数；`context` 获取安全组件的调用关系 |
| **Phase 2 (Recon)** | `impact` 分析 API 端点的 blast radius；`get_communities` 映射功能模块 |
| **Phase 3 (Vuln)** | 为每条调用链提供完整的源码上下文；`impact` 验证 sink 的影响范围 |
| **Phase 4 (Report)** | `query` 生成架构概览；Community Detection 辅助模块化描述 |

---

*文档版本: v1.1 | 日期: 2026-06-04 | 基于 GitNexus 1.6.5+ 的能力矩阵*
