# Ultimate Whitebox Scan Design: 确定性全覆盖白盒扫描架构

> **核心目标**: 入口找的全 · 路由准确 · Sink 找的全 · 调用链横向全+纵向深 · 污点参数分析全
>
> **核心原则**: 确定性工具保证**不遗漏**（覆盖度），LLM 保证**深度分析**（准确度）。两者结合实现既完整又深入的白盒审计。
>
> **核心引擎**: 以 **GitNexus** (https://github.com/nicepkg/GitNexus) 作为代码知识图谱引擎。
> GitNexus 提供 14 语言支持、Import 解析、继承链推断、Constructor 推断、Framework 检测、
> Entry Point 打分、Process 追踪、Community Detection、Confidence 评分等能力。
> 我们在此基础上叠加安全扫描专有层：参数污点追踪、Sink 发现、授权架构分析。

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
│  0.1 GitNexus 知识图谱构建 (核心引擎)                                │
│      ├─ gitnexus analyze → .gitnexus/ 知识图谱                      │
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
│      ├─ Processes → 每个入口点的完整执行流                           │
│      ├─ 菱形路径保留（GitNexus 原生）                                │
│      ├─ Confidence 评分过滤 (>0.7)                                   │
│      └─ 未解析调用标记 → Phase 1 LLM 补充                           │
│                                                                     │
│  0.6 参数传播图 (安全专有 — 自建，GitNexus 不提供)                   │
│      ├─ 入口点参数 → HTTP 请求来源标记                               │
│      ├─ 调用链参数映射 (caller argument → callee parameter)         │
│      ├─ 参数变换追踪 (编码/解码/序列化/验证)                         │
│      └─ Sink 参数来源标记 (该 sink 参数来自哪个 HTTP 入口)           │
│                                                                     │
│  输出: .gitnexus/ (知识图谱，全生命周期可用)                         │
│        code_index.json (函数+入口点+调用链)                          │
│        file_manifest.json (全文件清单+类型标记)                      │
│        parameter_graph.json (参数传播图)                             │
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
│ Phase 3: 逐链漏洞分析 (Per-Chain Vulnerability Analysis)            │
│                                                                     │
│  对每条 CallChainPath:                                               │
│  ├─ 注入 Agent (SQL/CMD/LFI/SSTI/反序列化)                          │
│  ├─ Auth Agent (认证绕过)                                            │
│  ├─ AuthZ Agent (水平/垂直提权)                                      │
│  ├─ XSS Agent (含模板上下文)                                         │
│  └─ SSRF Agent                                                      │
│                                                                     │
│  每个 Agent 输入: 完整调用链源码 + 参数传播图 + Sink 位置            │
│  每个 Agent 输出: 结构化 JSON (category/issue_type 白名单校验)       │
│  后处理: 五元组去重 + 覆盖度度量                                     │
│                                                                     │
│  输出: *_analysis_deliverable.md + *_exploitation_queue.json         │
│        coverage_report.json (覆盖率报告)                             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 四、Phase 0 详细设计

### 4.1 GitNexus 知识图谱构建 (0.1)

```python
class GitNexusEngine:
    """GitNexus 知识图谱引擎的 Python 封装"""

    def __init__(self, repo_root: Path, timeout: int = 300):
        self.repo_root = repo_root
        self.gitnexus_dir = repo_root / ".gitnexus"
        self.timeout = timeout

    def ensure_indexed(self) -> None:
        """确保仓库已索引。已有索引则跳过。"""
        if not self.gitnexus_dir.exists():
            self._run_cli("analyze", "--force")

    def get_context(self, symbol_name: str) -> GitNexusContext:
        """获取符号的 360° 上下文 (callers + callees + processes)"""
        result = self._run_cli("context", "--name", symbol_name,
                               "--repo", str(self.repo_root))
        return GitNexusContext.from_json(result)

    def get_processes(self) -> list[GitNexusProcess]:
        """获取所有执行流 (从入口点到 sink 的完整调用链)"""
        cypher = """
        MATCH (ep:EntryPoint)-[:STEP*]->(step)
        RETURN ep, step ORDER BY ep.name, step.stepIndex
        """
        result = self._run_cli("cypher", "--query", cypher,
                               "--repo", str(self.repo_root))
        return [GitNexusProcess.from_json(p) for p in json.loads(result)]

    def get_entry_points(self) -> list[GitNexusEntryPoint]:
        """获取所有入口点（含打分）"""
        cypher = """
        MATCH (ep:EntryPoint)
        RETURN ep.name, ep.filePath, ep.score, ep.kind
        ORDER BY ep.score DESC
        """
        result = self._run_cli("cypher", "--query", cypher,
                               "--repo", str(self.repo_root))
        return [GitNexusEntryPoint.from_json(ep) for ep in json.loads(result)]

    def get_impact(self, target: str, direction: str = "upstream",
                   min_confidence: float = 0.7) -> GitNexusImpact:
        """blast radius 分析"""
        result = self._run_cli("impact", "--target", target,
                               "--direction", direction,
                               "--repo", str(self.repo_root))
        return GitNexusImpact.from_json(result)

    def get_communities(self) -> list[GitNexusCommunity]:
        """获取功能聚类"""
        result = self._run_cli("query", "--query", "communities",
                               "--repo", str(self.repo_root))
        return [GitNexusCommunity.from_json(c) for c in json.loads(result)]

    def _run_cli(self, command: str, *args: str) -> str:
        cmd = ["gitnexus", command, *args]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        if result.returncode != 0:
            raise GitNexusError(f"gitnexus {command} failed: {result.stderr}")
        return result.stdout
```

**降级策略**: GitNexus 不可用时，降级到当前 shannon-py 的 AST BFS 方案：

```python
def build_code_index(repo_root: Path, config: Config) -> CodeIndex:
    try:
        gitnexus = GitNexusEngine(repo_root)
        gitnexus.ensure_indexed()
        return extract_from_gitnexus(gitnexus)
    except (FileNotFoundError, GitNexusError):
        logger.warning("GitNexus unavailable, falling back to AST BFS")
        return build_code_index_ast(repo_root, config)  # 当前实现
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
def merge_entry_points(
    gitnexus_eps: list[GitNexusEntryPoint],
    schema_eps: list[EntryPoint],          # Schema 文件解析
    convention_eps: list[EntryPoint],      # 框架约定
    config: Config,
) -> list[UnifiedEntryPoint]:
    """融合多来源入口点"""
    unified = {}

    # 来源 1: GitNexus EP Scoring (主要)
    for ep in gitnexus_eps:
        unified[ep.uid] = UnifiedEntryPoint(
            uid=ep.uid, name=ep.name, file_path=ep.filePath,
            confidence=ep.score, source="gitnexus", entry_type=ep.kind,
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
def extract_call_chains(
    gitnexus_index: GitNexusIndex,
    entry_points: list[UnifiedEntryPoint],
    min_confidence: float = 0.7,
) -> list[CallChainPath]:
    """从 GitNexus Process 追踪提取完整调用链"""
    all_paths = []

    for process in gitnexus_index.processes:
        path_nodes = []
        for step in process.steps:
            if step.confidence >= min_confidence:
                path_nodes.append(CallChainNode(
                    function_block=FuncBlock(
                        id=FuncBlockId(step.file_path, step.name,
                                       step.start_line, step.end_line),
                        function_name=step.name,
                        file_path=step.file_path,
                        source_code=step.source,
                    ),
                    depth=step.depth,
                    call_metadata=CallMetadata(
                        confidence=step.confidence,
                        call_type=step.relation_type,
                    ),
                ))

        if path_nodes:
            all_paths.append(CallChainPath(
                nodes=path_nodes,
                entry_point=path_nodes[0].function_block,
            ))

    return all_paths
```

### 4.6 参数传播图 (0.6 — 自建，GitNexus 不提供)

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

#### 框架感知参数来源标记

```python
def mark_parameter_sources(
    entry_point: EntryPoint, import_graph, blocks,
) -> list[Parameter]:
    """根据 framework 和 route 标记入口点参数的 HTTP 来源"""
    framework = detect_framework(entry_point)

    if framework in ("fastapi", "flask", "django"):
        return _mark_python_sources(entry_point.parameters, entry_point, framework)
    elif framework in ("express", "fastify", "koa", "nestjs"):
        return _mark_js_sources(entry_point.parameters, entry_point, framework)
    elif framework in ("spring",):
        return _mark_java_sources(entry_point.parameters, entry_point)
    # ... 其他框架
```

#### 参数传播追踪

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
4. `.gitnexus/` — 可通过 `gitnexus query/context` 实时查询

### 6.2 API 端点清单增强

恢复 Shannon 的 **Shared Controller Parameter Propagation** 和 **Parameter Completeness Verification**：

```
## 4. API Endpoint Inventory

| Method | Endpoint | Required Role | Object ID Params | Auth Mechanism |
        | All Parameters (from Parameter Graph) | Taint Sources |

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

## 七、Phase 3 详细设计：逐链漏洞分析

### 7.1 核心改变：从文档驱动改为调用链驱动

**当前 (Shannon)**: Vuln Agent 读取 `recon_deliverable.md`，自行搜索代码分析漏洞。

**改进 (SCR-AI 策略)**: 对每条 `CallChainPath`，以完整源码+参数传播图作为输入：

```python
async def audit_all_chains(
    call_chains: list[CallChainPath],
    parameter_graph: ParameterPropagationGraph,
    config: Config,
) -> list[VulnFinding]:
    """path × agent_type 的笛卡尔积并发"""
    semaphore = asyncio.Semaphore(config.max_concurrency)

    async def _audit_path(path: CallChainPath, agent_type: str):
        async with semaphore:
            taint_info = parameter_graph.by_entry_point.get(
                path.entry_point.id, []
            )
            prompt = format_audit_prompt(path, taint_info, agent_type)
            response = await agent.run(prompt)
            return parse_and_validate_findings(response, agent_type)

    tasks = []
    for path in call_chains:
        for agent_type in ["injection", "auth", "authz", "xss", "ssrf"]:
            tasks.append(_audit_path(path, agent_type))

    results = await asyncio.gather(*tasks)
    all_findings = [f for r in results for f in r]
    return deduplicate_findings(all_findings)
```

### 7.2 审计 Agent Prompt 格式

每个 Agent 收到的输入：

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
```

### 7.3 结构化输出校验

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

### 7.4 五元组去重

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

| Phase | GitNexus 用途 |
|---|---|
| **Phase 0** | 索引构建，入口点检测，调用图，功能聚类 |
| **Phase 1** | `query "authentication"` 搜索认证组件；`context` 获取安全函数调用关系 |
| **Phase 2** | `impact` 分析端点 blast radius；`get_communities` 映射功能模块 |
| **Phase 3** | 为每条调用链提供完整源码上下文；`impact` 验证 sink 影响范围 |
| **Phase 4** | `query` 生成架构概览；Community Detection 辅助模块化描述 |

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
Phase 3: 逐链 Vuln (LLM per chain)
    ├─ *_analysis_deliverable.md     (每类漏洞分析报告)
    ├─ *_exploitation_queue.json     (可利用漏洞队列)
    ├─ coverage_report.json          (最终覆盖率)
    └─ findings_deduped.json         (去重后的全部发现)
```

---

## 十、与现有系统的改造范围

| 模块 | 改造类型 | 说明 |
|---|---|---|
| `code_index/` | **重构** | 以 GitNexus 为核心，增强模型，新增 parameter_graph |
| `code_index/gitnexus_engine.py` | **新增** | GitNexus CLI 封装层 |
| `code_index/parameter_graph.py` | **新增** | 参数传播图构建 |
| `code_index/file_discovery.py` | **新增** | 安全文件发现 |
| `code_index/parser.py` | **增强** | 参数类型提取（在 GitNexus 索引上补充） |
| `code_index/entry_points.py` | **简化** | 入口点融合（GitNexus 为主） |
| `prompts/pre-recon-code.txt` | **增强** | 恢复两步 Sink 分析、覆盖度报告 |
| `prompts/recon.txt` | **增强** | 恢复 No Security Judgments、参数传播 |
| `prompts/recon-static.txt` | **重写** | 对齐 live 版本 9 章结构 |
| `prompts/vuln-*.txt` | **重构** | 调用链驱动 + 结构化输出 + 白名单校验 |
| `pipeline/workflows.py` | **增强** | Phase 3 逐链分析编排 |

**向后兼容**:
- GitNexus 不可用时降级到当前 AST BFS
- `code_index.json` 新增字段，旧字段保持不变
- `entry_points.json` 格式不变

---

## 十一、实施优先级

### P0 — 基础层核心（解决"不全"的根因） — ~5.5 天

| # | 任务 | 工期 | 说明 |
|---|---|---|---|
| 1 | GitNexus CLI 集成 | 1 天 | Python 封装层，索引构建 + 查询接口 |
| 2 | GitNexus Process → CallChainPath 转换 | 1 天 | 执行流转为内部数据模型 |
| 3 | GitNexus EP Scoring → EntryPoint 融合 | 0.5 天 | 入口点去重和合并 |
| 4 | 完整参数提取 (Tree-sitter) | 1 天 | 在 GitNexus 索引上补充参数类型 |
| 5 | 参数 HTTP 来源标记 | 1 天 | 框架感知的参数来源映射 |
| 6 | 全文件发现 (模板/配置/Schema) | 1 天 | GitNexus 不覆盖的安全文件 |

### P1 — 增强层（提升覆盖度和准确度） — ~3.5 天

| # | 任务 | 工期 |
|---|---|---|
| 7 | 参数传播图 | 2 天 |
| 8 | Batch LLM 入口点分类 | 0.5 天 |
| 9 | 恢复 Pre-Recon 两步 Sink 分析 | 0.5 天 |
| 10 | 恢复 Recon "No Security Judgments" | 0.5 天 |

### P2 — 深度优化 — ~5 天

| # | 任务 | 工期 |
|---|---|---|
| 11 | 恢复 Shared Controller 参数传播 + 完整性验证 | 0.5 天 |
| 12 | 重写 recon-static.txt | 1 天 |
| 13 | Vuln Agent 逐链分析改造 | 2 天 |
| 14 | 结构化输出校验 | 1 天 |
| 15 | 五元组去重 | 0.5 天 |
| 16 | 覆盖度报告 | 1 天 |

**总计**: P0 ~5.5 天 + P1 ~3.5 天 + P2 ~5 天 = **~14 天**

---

## 十二、验证方案

### 12.1 基准测试用例

| 场景 | 验证目标 | 期望 |
|---|---|---|
| Express 路由 `(req, res) => {}` | 入口点检测 + 参数提取 | GitNexus EP Scoring 检测到 |
| Python `**kwargs` 传递 | 参数传播 | 完整追踪到 sink |
| 多文件 `from utils import helper` | 跨文件调用 | GitNexus Import 解析连接 |
| `obj.process()` 多态 | 调用链完整性 | GitNexus Constructor 推断解析 |
| A→B→D + A→C→D 菱形 | 路径保留 | 两条路径都保留 |
| Laravel `Route::get('/path', ...)` | 命令式路由 | GitNexus Framework Detection |
| EJS `<%= %>` vs `<%- %>` | 模板 Sink 分析 | 区分 escaped/unescaped |
| `req.body.user_id → SQL query` | 完整污点追踪 | 参数传播图覆盖 |
| Next.js `pages/api/*.ts` | 框架约定 | 补充策略检测到 |

### 12.2 覆盖度度量

每个 Phase 结束后输出覆盖率：

```
Phase 0 → 文件覆盖率、函数覆盖率、入口点覆盖率、调用链路径数
Phase 1 → Sink 覆盖率 (发现的 Sink / 总模板文件)
Phase 2 → 参数覆盖率 (枚举的参数 / 完整参数列表)
Phase 3 → 调用链审计覆盖率 (已审计链 / 总链数)
```

---

*设计版本: v2.0 (merged) | 日期: 2026-06-04 | GitNexus + Shannon + SCR-AI 三方融合*
