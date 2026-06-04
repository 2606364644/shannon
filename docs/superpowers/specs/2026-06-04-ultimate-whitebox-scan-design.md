# Ultimate Whitebox Scan Design: 确定性全覆盖白盒扫描架构

> **核心目标**: 入口找的全 · 路由准确 · Sink 找的全 · 调用链横向全+纵向深 · 污点参数分析全
>
> **核心原则**: 确定性工具保证**不遗漏**（覆盖度），LLM 保证**深度分析**（准确度）。两者结合实现既完整又深入的白盒审计。

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

## 二、最终架构设计

### 2.1 Pipeline 全局视图

```
┌─────────────────────────────────────────────────────────────────────┐
│ Phase 0: 确定性全覆盖基础层 (Deterministic Complete Foundation)      │
│                                                                     │
│  0.1 全文件发现 (Multi-Type File Discovery)                         │
│      ├─ 源码文件 (.py/.ts/.go/.java/.php/.rb/.rs)                   │
│      ├─ 模板文件 (.html/.ejs/.pug/.hbs/.jinja2/.vue/.svelte/...)   │
│      ├─ 配置文件 (route configs: .yaml/.json/.xml)                  │
│      └─ Schema 文件 (.graphql/.proto/.swagger.yaml/.openapi.yaml)  │
│                                                                     │
│  0.2 多语言完整函数提取 (Multi-Language Function Extraction)         │
│      ├─ 具名函数、类方法、构造函数、嵌套函数                          │
│      ├─ Lambda/Arrow Function/匿名函数/闭包                         │
│      ├─ 完整参数（名称+类型+默认值+可变参数标记）                     │
│      └─ 装饰器/注解 + 框架识别                                      │
│                                                                     │
│  0.3 Import 图构建 (Import Graph)                                   │
│      ├─ 解析 import/require 语句 → 文件路径映射                     │
│      ├─ re-export / barrel file / __init__.py 处理                  │
│      └─ 别名解析 (import X as Y, const { a: b } = require(...))    │
│                                                                     │
│  0.4 多策略入口点检测 (Multi-Strategy Entry Point Detection)         │
│      ├─ 策略 A: 装饰器规则 (FastAPI/NestJS/Spring @RequestMapping)  │
│      ├─ 策略 B: 命令式路由注册 (Express router.get/Laravel Route::) │
│      ├─ 策略 C: 框架约定 (Next.js pages/api/、Django URL conf)     │
│      ├─ 策略 D: Schema 文件解析 (OpenAPI paths → handler 映射)     │
│      └─ 策略 E: Batch LLM 分类 (剩余不确定的函数批量判定)           │
│                                                                     │
│  0.5 多策略调用图构建 (Multi-Strategy Call Graph Construction)       │
│      ├─ 策略 1: AST 静态调用图 (直接调用)                           │
│      ├─ 策略 2: Import 解析跨文件调用                               │
│      ├─ 策略 3: 类型推断解析多态调用                                 │
│      ├─ 策略 4: 框架感知解析 (DI/中间件链/事件系统)                  │
│      └─ 策略 5: 菱形路径保留 (禁用 visited 剪枝)                    │
│                                                                     │
│  0.6 参数传播图 (Parameter Propagation Graph)                       │
│      ├─ 入口点参数 → HTTP 请求来源标记                               │
│      ├─ 调用链参数映射 (caller argument → callee parameter)         │
│      ├─ 参数变换追踪 (编码/解码/序列化/验证)                         │
│      └─ Sink 参数来源标记 (该 sink 参数来自哪个 HTTP 入口)           │
│                                                                     │
│  输出: code_index.json (函数+调用图+入口点+参数图)                   │
│        file_manifest.json (全文件清单+类型标记+覆盖状态)             │
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
│        pre_recon_deliverable.md (架构分析报告)                       │
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
│  每个 Agent 输出: 结构化 JSON (带 category/issue_type 白名单校验)    │
│                                                                     │
│  后处理: 五元组去重 + 覆盖度度量 (audited/total chains)              │
│                                                                     │
│  输出: *_analysis_deliverable.md + *_exploitation_queue.json         │
│        coverage_report.json (覆盖率报告)                             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 三、Phase 0 详细设计：确定性全覆盖基础层

### 3.1 全文件发现 (0.1)

#### 3.1.1 多类型文件清单

```python
FILE_TYPE_CATEGORIES = {
    "source": {
        # Python
        ".py", ".pyw", ".pyx", ".pyi",
        # TypeScript/JavaScript
        ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
        # Go
        ".go",
        # Java/Kotlin
        ".java", ".kt", ".kts",
        # PHP
        ".php",
        # Ruby
        ".rb", ".erb",
        # Rust
        ".rs",
        # C/C++
        ".c", ".cpp", ".h", ".hpp",
    },
    "template": {
        # 服务端模板 (含服务端逻辑，需要扫描)
        ".html", ".htm",
        ".ejs", ".hbs", ".handlebars", ".mustache",
        ".pug", ".jade",
        ".jinja2", ".j2", ".jinja",
        ".erb",
        ".vue", ".svelte",
        ".tsx",  # React TSX 既包含逻辑又包含模板
        ".php",  # PHP 文件本身就是模板
        ".tmpl", ".tpl",
        ".mhtml",
    },
    "config": {
        ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".conf",
        ".xml", ".env", ".properties",
    },
    "schema": {
        ".graphql", ".gql",
        ".proto", ".thrift",
        ".swagger.yaml", ".swagger.json",
        ".openapi.yaml", ".openapi.json",
    },
    "query": {
        ".sql", ".graphql", ".gql",
    },
}
```

#### 3.1.2 智能跳过策略

```python
# 取代硬编码 SKIP_DIRS
SKIP_DIRS = {
    # 版本控制
    ".git", ".hg", ".svn",
    # 依赖 (保留 node_modules 的 package.json 用于框架检测)
    "node_modules", "vendor", "third_party", "external",
    # 构建产物
    "dist", "build", "out", "target", "bin", "obj", ".next", ".nuxt",
    # 缓存
    "__pycache__", ".cache", ".parcel-cache", ".turbo",
    # 虚拟环境
    ".venv", "venv", ".env", "env",
    # IDE
    ".idea", ".vscode",
    # 测试快照/覆盖率
    "coverage", ".nyc_output", "__snapshots__",
}

def discover_all_files(repo_root: Path) -> FileManifest:
    """
    发现项目中所有相关文件，按类型分类。

    关键改进:
    1. 支持 polyglot — 不过滤为单一语言
    2. 包含模板/配置/schema 文件
    3. 解析 .gitignore 排除不需要的文件
    4. 对语法错误文件标记为 needs_review 而非跳过
    """
    gitignore_patterns = parse_gitignore(repo_root)
    manifest = FileManifest()

    for file_path in repo_root.rglob("*"):
        if not file_path.is_file():
            continue
        if _should_skip(file_path, gitignore_patterns):
            continue

        file_type = classify_file(file_path)
        if file_type:
            manifest.add(file_path, file_type)

    return manifest
```

#### 3.1.3 文件覆盖度度量

```python
@dataclass
class FileManifest:
    files: dict[Path, FileType]  # 路径 → 文件类型

    def coverage_report(self) -> dict:
        return {
            "total_files": len(self.files),
            "by_type": {
                ft.value: len([f for f, t in self.files.items() if t == ft])
                for ft in FileType
            },
            "parsed_files": 0,      # 成功解析的文件数
            "parse_errors": 0,      # 解析失败但记录在案的文件数
            "skipped_binary": 0,    # 跳过的二进制文件
        }
```

---

### 3.2 多语言完整函数提取 (0.2)

#### 3.2.1 增强的 FuncBlock 模型

```python
@dataclass
class Parameter:
    """完整参数信息 — 支持污点分析的基础"""
    name: str
    type_annotation: str | None     # "int", "str", "Request", "User"
    default_value: str | None       # "None", "''", "0"
    is_variadic: bool = False       # *args
    is_keyword_variadic: bool = False  # **kwargs
    is_optional: bool = False       # TypeScript ? 修饰
    source: ParameterSource | None = None  # HTTP 来源标记 (见 3.6)

@dataclass
class FuncBlock:
    id: FuncBlockId                 # file_path:function_name:start_line:end_line
    function_name: str
    class_name: str | None          # 所属类名
    file_path: str
    start_line: int
    end_line: int
    source_code: str
    parameters: list[Parameter]     # 完整参数信息 (取代 list[str])
    decorators: list[str]           # ["@app.route('/api/users')", "@require_auth"]
    annotations: list[str]          # Java/Kotlin 注解
    docstring: str | None
    file_imports: list[str]
    function_type: FunctionType     # FUNCTION | METHOD | LAMBDA | ARROW | CONSTRUCTOR | CLOSURE
    is_async: bool
    return_type: str | None         # 返回类型注解
```

#### 3.2.2 全函数类型覆盖要求

每种语言的 parser 必须提取以下所有函数类型：

| 函数类型 | Python | TypeScript | Go | Java | PHP |
|---|---|---|---|---|---|
| 具名函数 | `def foo()` | `function foo()` | `func foo()` | 返回类型 方法名() | `function foo()` |
| 异步函数 | `async def foo()` | `async function foo()` | - | - | - |
| 类方法 | `def method(self)` | `method() {}` | `func (r Receiver) Method()` | 方法声明 | `public function method()` |
| 构造函数 | `__init__` | `constructor()` | - | 构造器声明 | `__construct()` |
| Lambda/Arrow | `lambda x: x+1` | `(x) => x+1` | `func literal` | - | `fn() => ...` |
| 匿名函数/闭包 | `def _inner():` (嵌套) | `function() {}` | `func() {}` | 匿名类 | `function() {}` |
| 静态方法 | `@staticmethod` | `static method()` | - | `static` | `static function` |
| 类属性方法 | - | `handler = () => {}` | - | - | - |
| Getter/Setter | `@property` | `get prop()` | - | - | `__get/__set` |

#### 3.2.3 关键修复：TypeScript Arrow Function

当前最大盲区：Express/Fastify/Koa 的路由 handler 通常是 arrow function：

```typescript
// 当前：完全遗漏
router.get('/users/:id', (req: Request, res: Response) => {
  const user = userService.findById(req.params.id);
  res.json(user);
});
```

**提取策略**：扫描 `call_expression` 的参数中的 `arrow_function` 和 `function_expression`：

```python
def extract_callback_functions(node, source_bytes, file_path):
    """
    从函数调用参数中提取回调和 handler 函数。
    覆盖: router.get('/path', handler), app.use(middleware), .then(callback)
    """
    callbacks = []
    if node.type == "call_expression":
        for arg in node.arguments:
            if arg.type in ("arrow_function", "function_expression"):
                # 提取为 FuncBlock，function_type=CLOSURE
                # 用调用上下文命名: "router.get_handler_42"
                name = _infer_callback_name(node, arg)
                block = _build_callback_block(arg, name, source_bytes, file_path)
                callbacks.append(block)
    return callbacks
```

#### 3.2.4 容错解析

```python
def parse_file_robust(file_path, repo_root, language_adapter):
    """
    容错解析：语法错误时尽力提取，而非跳过整个文件。
    """
    source = file_path.read_text(errors="replace")

    try:
        tree = language_adapter.parse(source)
        blocks = language_adapter.extract_functions(tree, source, file_path, repo_root)
        return ParseResult(blocks=blocks, has_errors=False)
    except ParseError:
        # 策略 1: 逐函数提取 — 跳过错误节点，提取其余函数
        blocks = language_adapter.extract_functions_best_effort(source, file_path)
        return ParseResult(blocks=blocks, has_errors=True)
```

---

### 3.3 Import 图构建 (0.3)

**这是解决 C1 (跨文件调用断裂) 的核心模块。**

#### 3.3.1 Import 图数据模型

```python
@dataclass
class ImportEdge:
    """一条 import 语句的完整解析"""
    source_file: str            # 导入语句所在文件
    import_module: str          # 原始模块路径 (e.g., "utils.helpers")
    resolved_file: str | None   # 解析后的文件路径 (e.g., "utils/helpers.py")
    imported_symbols: dict[str, str]  # 符号映射: {"original_name": "local_alias"}
    import_type: str            # "import" | "from_import" | "require" | "dynamic_import"
    line_number: int

@dataclass
class ImportGraph:
    """全项目 import 关系图"""
    edges: list[ImportEdge]
    # 正向索引: 文件 → 它导入了什么
    outgoing: dict[str, list[ImportEdge]]
    # 反向索引: 文件 → 谁导入了它
    incoming: dict[str, list[ImportEdge]]
    # 符号解析: (file, local_name) → (resolved_file, original_name)
    symbol_resolution: dict[tuple[str, str], tuple[str, str]]
```

#### 3.3.2 各语言 Import 解析

**Python:**
```python
# import foo.bar          → foo/bar/__init__.py 或 foo/bar.py
# from foo.bar import baz → foo/bar.py 中的 baz 函数
# from . import sibling   → 同目录 sibling.py
# import foo.bar as fb    → fb = foo/bar/__init__.py
```

**TypeScript/JavaScript:**
```python
# import { X } from './utils'        → ./utils.ts 或 ./utils/index.ts
# import X from './utils'             → ./utils.ts (default export)
# const X = require('./utils')        → 同上
# import('./utils').then(m => m.X)    → 动态导入
```

**Go:**
```python
# import "github.com/pkg/foo"  → vendor/github.com/pkg/foo/*.go
# 同包内函数直接调用，无需 import
```

**Java:**
```python
# import com.example.service.UserService → com/example/service/UserService.java
```

#### 3.3.3 跨文件调用解析

```python
def resolve_cross_file_call(
    caller: FuncBlock,
    callee_name: str,
    import_graph: ImportGraph,
    all_blocks: dict[FuncBlockId, FuncBlock],
) -> list[FuncBlock]:
    """
    解析跨文件调用。返回所有可能的 callee。

    策略:
    1. 查 import_graph: caller 文件导入了什么符号
    2. 匹配 callee_name 到导入的符号
    3. 定位到 resolved_file 中的 FuncBlock
    4. 如果无 import，检查同文件定义
    """
    candidates = []

    # 策略 1: 通过 import 解析
    resolution = import_graph.symbol_resolution.get(
        (caller.file_path, callee_name)
    )
    if resolution:
        target_file, target_name = resolution
        candidates.extend(_find_blocks_in_file(target_file, target_name, all_blocks))

    # 策略 2: 同文件定义
    if not candidates:
        candidates.extend(_find_blocks_in_file(caller.file_path, callee_name, all_blocks))

    # 策略 3: 父类方法 (如果 caller 是类方法)
    if not candidates and caller.class_name:
        candidates.extend(_find_parent_class_method(
            caller, callee_name, import_graph, all_blocks
        ))

    return candidates
```

---

### 3.4 多策略入口点检测 (0.4)

#### 3.4.1 五策略检测矩阵

| 策略 | 覆盖场景 | 示例 | 实现 |
|---|---|---|---|
| **A: 装饰器规则** | 声明式路由 | `@app.route`, `@router.get`, `@GetMapping` | 正则匹配 decorators |
| **B: 命令式路由注册** | 命令式路由 | `router.get('/path', handler)`, `Route::get(...)` | AST 模式匹配 |
| **C: 框架约定** | 目录约定 | Next.js `pages/api/`, Django `urls.py` | 路径规则 |
| **D: Schema 解析** | API-first | OpenAPI `paths:` → 对应 handler | JSON/YAML 解析 |
| **E: Batch LLM** | 残余不确定 | 低置信度函数批量判定 | LLM 批量分类 |

#### 3.4.2 策略 B: 命令式路由注册检测（新增，解决 C2）

**Express/Fastify/Koa:**
```python
# 检测模式: router.get('/path', handler)
# AST: call_expression → member_expression (router.get) + arguments ['/path', handler]

EXPRESS_ROUTE_PATTERNS = [
    # router.get('/path', handler)
    {"method_pattern": r"(router|app|server)\.(get|post|put|delete|patch|all|use)",
     "route_arg_index": 0, "handler_arg_index": 1},
    # router.route('/path').get(handler)
    {"method_pattern": r"route\(['\"].*?['\"]\)\.(get|post|put|delete|patch)",
     "route_arg_index": -1, "handler_arg_index": 0},
]
```

**Laravel (PHP):**
```python
# 检测模式: Route::get('/path', [Controller::class, 'method'])
# AST: static_call_expression → Route::get + arguments

LARAVEL_ROUTE_PATTERNS = [
    # Route::get('/path', [Controller::class, 'method'])
    {"class": "Route", "methods": ["get", "post", "put", "delete", "patch", "any", "match"]},
    # $router->get('/path', 'Controller@method')
    {"class": "Router", "methods": ["get", "post", "put", "delete", "patch"]},
]
```

**Django:**
```python
# 检测模式: path('url/', view_func, name='...')
# 从 urls.py 文件解析 urlpatterns 列表

DJANGO_ROUTE_PATTERNS = [
    {"function": "path", "route_arg": 0, "handler_arg": 1},
    {"function": "re_path", "route_arg": 0, "handler_arg": 1},
    {"function": "url", "route_arg": 0, "handler_arg": 1},
]
```

**Go (net/http, Chi, Echo, Fiber, Gorilla Mux):**
```python
GO_ROUTE_PATTERNS = [
    # http.HandleFunc("/path", handler)
    {"function": "HandleFunc"},
    # r.Get("/path", handler)  (Chi)
    # e.GET("/path", handler)  (Echo)
    # f.Get("/path", handler)  (Fiber)
    {"method_pattern": r"(r|e|f|router)\.(Get|Post|Put|Delete|Handle|GET|POST)"},
]
```

#### 3.4.3 策略 C: 框架约定检测

```python
FRAMEWORK_CONVENTIONS = {
    "nextjs": {
        "patterns": ["pages/api/**/*.ts", "pages/api/**/*.js",
                      "app/api/**/*.ts", "app/api/**/*.js"],
        "entry_type": "http_route",
        "confidence": 0.85,
    },
    "django": {
        "files": ["urls.py", "urls/**/*.py"],
        "parse_strategy": "urlpatterns_list",
    },
    "rails": {
        "files": ["config/routes.rb"],
        "parse_strategy": "rails_routes",
    },
    "spring": {
        "files": ["application.yml", "application.properties"],
        "parse_strategy": "spring_actuator",
    },
}
```

#### 3.4.4 策略 D: Schema 文件解析

```python
def extract_entry_points_from_schema(schema_path: Path) -> list[EntryPoint]:
    """从 API Schema 文件中提取入口点"""
    if schema_path.suffix in (".graphql", ".gql"):
        return _parse_graphql_schema(schema_path)
    elif "openapi" in schema_path.name or "swagger" in schema_path.name:
        return _parse_openapi_schema(schema_path)
    elif schema_path.suffix == ".proto":
        return _parse_proto_schema(schema_path)
    return []
```

#### 3.4.5 策略 E: Batch LLM 分类（来自 SCR-AI）

```python
async def batch_classify_entry_points(
    unclassified_blocks: list[FuncBlock],
    config: Config,
) -> list[EntryPoint]:
    """
    对确定性规则无法判定的函数，批量发给 LLM 分类。
    SCR-AI 策略: 每批 20 个，并行处理，结构化 JSON 输出。
    """
    batch_size = 20
    semaphore = asyncio.Semaphore(config.max_concurrency)

    async def _classify_batch(batch):
        prompt = format_entry_batch(batch)
        response = await llm.run(prompt)
        return parse_entry_response(response, batch)

    batches = [unclassified_blocks[i:i+batch_size]
               for i in range(0, len(unclassified_blocks), batch_size)]

    results = await asyncio.gather(*[_classify_batch(b) for b in batches])
    return [ep for r in results for ep in r]
```

---

### 3.5 多策略调用图构建 (0.5)

#### 3.5.1 菱形路径保留 (解决 C5)

**当前问题**: BFS 用 `visited` 集合剪枝，A→B→D 和 A→C→D 只保留一条。

**解决方案**: 用 `(node_id, parent_path_id)` 作为 visited key，而非单独的 `node_id`：

```python
@dataclass
class CallChainTree:
    root: CallChainNode
    max_depth_reached: bool = False
    max_width_reached: bool = False
    unresolved_calls: list[UnresolvedCall] = []  # 新增: 记录未解析的调用

@dataclass
class CallChainNode:
    function_block: FuncBlock
    depth: int
    children: list[CallChainNode]
    call_metadata: CallMetadata  # 新增: 调用上下文

@dataclass
class CallMetadata:
    """记录调用发生的上下文信息"""
    caller_arg_to_callee_param: dict[int, int]  # 实参位置 → 形参位置
    call_type: str  # "direct" | "method" | "callback" | "dynamic" | "super"
    confidence: float  # 解析置信度

def build_tree_preserving_diamonds(
    entry_point: EntryPoint,
    all_blocks: dict[FuncBlockId, FuncBlock],
    import_graph: ImportGraph,
    max_depth: int = 15,
    max_width: int = 100,  # 从 50 提高到 100
) -> CallChainTree:
    """
    构建调用链树，保留菱形路径。

    关键改变: visited 使用 (node_id, path_from_root) 而非 node_id。
    这意味着 A→B→D 和 A→C→D 两条路径都被保留，因为到达 D 的路径不同。
    """
    root = CallChainNode(entry_point.function_block, depth=0)
    max_depth_reached = False
    max_width_reached = False
    unresolved = []

    def _expand(node: CallChainNode, path: tuple[str, ...]):
        nonlocal max_depth_reached, max_width_reached

        if node.depth >= max_depth:
            max_depth_reached = True
            return

        calls = extract_calls_from_block(node.function_block)
        if len(calls) > max_width:
            max_width_reached = True
            calls = calls[:max_width]  # 截断但记录

        for call in calls:
            # 多策略解析 callee
            callees = resolve_call(
                caller=node.function_block,
                callee_name=call.call_name,
                import_graph=import_graph,
                all_blocks=all_blocks,
            )

            if not callees:
                unresolved.append(UnresolvedCall(
                    caller_id=node.function_block.id,
                    callee_name=call.call_name,
                    call_type=call.call_type,
                ))
                continue

            for callee in callees:
                child = CallChainNode(callee, depth=node.depth + 1)
                node.children.append(child)
                # 关键: 不使用全局 visited，而是传递路径
                new_path = path + (callee.id.key(),)
                _expand(child, new_path)

    _expand(root, (root.function_block.id.key(),))
    return CallChainTree(root, max_depth_reached, max_width_reached, unresolved)
```

#### 3.5.2 多策略调用解析

```python
def resolve_call(
    caller: FuncBlock,
    callee_name: str,
    import_graph: ImportGraph,
    all_blocks: dict[FuncBlockId, FuncBlock],
) -> list[FuncBlock]:
    """
    多策略解析调用目标。返回所有可能的 callee（支持多态/接口）。

    策略优先级:
    1. 同文件直接匹配 (最高置信度)
    2. Import 解析跨文件匹配
    3. 类方法继承链解析
    4. 接口/抽象类 → 实现类枚举
    5. 框架感知 (DI 容器、事件监听器注册表)
    """
    candidates = []
    used_strategies = set()

    # 策略 1: 同文件直接匹配
    same_file = _find_in_file(caller.file_path, callee_name, all_blocks)
    if same_file:
        candidates.extend(same_file)
        used_strategies.add("same_file")

    # 策略 2: Import 解析
    if not candidates:
        imported = _resolve_via_import(caller, callee_name, import_graph, all_blocks)
        if imported:
            candidates.extend(imported)
            used_strategies.add("import_resolution")

    # 策略 3: 类继承链
    if caller.class_name and "." in callee_name:
        method_name = callee_name.split(".")[-1]
        inherited = _resolve_inherited_method(
            caller.class_name, method_name, import_graph, all_blocks
        )
        if inherited:
            candidates.extend(inherited)
            used_strategies.add("inheritance")

    # 策略 4: 接口实现枚举
    if not candidates:
        interface_impls = _resolve_interface_implementations(
            callee_name, import_graph, all_blocks
        )
        if interface_impls:
            candidates.extend(interface_impls)
            used_strategies.add("interface_resolution")

    # 去重 (同文件同名函数可能被多个策略发现)
    seen = set()
    unique = []
    for c in candidates:
        if c.id.key() not in seen:
            seen.add(c.id.key())
            unique.append(c)

    return unique
```

#### 3.5.3 消除函数名歧义 (解决 C3)

```python
def _find_in_file(file_path: str, func_name: str,
                  all_blocks: dict[FuncBlockId, FuncBlock]) -> list[FuncBlock]:
    """在指定文件中查找函数，避免全局名称歧义"""
    return [
        block for block in all_blocks.values()
        if block.file_path == file_path and block.function_name == func_name
    ]
```

核心改变: 所有查找都先按 `file_path` 过滤，再按 `function_name` 匹配。取代当前的全局 `name_index`。

---

### 3.6 参数传播图 (0.6)

**这是解决 P1-P5 的核心模块。**

#### 3.6.1 参数来源标记

```python
class ParameterSource(Enum):
    """参数的 HTTP 请求来源"""
    QUERY_PARAM = "query"       # ?user_id=123
    PATH_PARAM = "path"         # /users/{id}
    BODY_FIELD = "body"         # JSON body field
    FORM_FIELD = "form"         # form-urlencoded field
    HEADER = "header"           # X-Custom-Header
    COOKIE = "cookie"           # session_id
    FILE_UPLOAD = "file"        # uploaded file name/content
    SESSION_ATTR = "session"    # req.session.user_id
    CONTEXT = "context"         # framework-specific (request.state.user)
    INTERNAL = "internal"       # 内部生成，非用户输入
    UNKNOWN = "unknown"
```

#### 3.6.2 框架感知的参数来源标记

```python
def mark_parameter_sources(
    entry_point: EntryPoint,
    blocks: dict[FuncBlockId, FuncBlock],
    import_graph: ImportGraph,
) -> list[Parameter]:
    """
    根据 framework 和 route 信息，标记入口点参数的 HTTP 来源。

    示例:
    - FastAPI: @router.get('/users/{user_id}') → user_id 是 PATH_PARAM
    - Express: (req, res) → req.query.* 是 QUERY_PARAM, req.body.* 是 BODY_FIELD
    - Django: request.GET['key'] → QUERY_PARAM
    """
    params = entry_point.function_block.parameters
    framework = detect_framework(entry_point, import_graph)

    if framework in ("fastapi", "flask", "django"):
        return _mark_python_sources(params, entry_point, framework)
    elif framework in ("express", "fastify", "koa", "nestjs"):
        return _mark_js_sources(params, entry_point, framework)
    elif framework in ("spring",):
        return _mark_java_sources(params, entry_point)
    elif framework in ("gin", "echo", "fiber"):
        return _mark_go_sources(params, entry_point)
    elif framework in ("laravel", "symfony"):
        return _mark_php_sources(params, entry_point)

    return params  # fallback: 标记为 UNKNOWN
```

#### 3.6.3 参数传播追踪

```python
@dataclass
class TaintFlow:
    """一条完整的污点传播路径"""
    entry_point_id: FuncBlockId          # 入口点
    source_param: str                     # 原始参数名
    source_type: ParameterSource          # 参数来源 (query/body/path/...)
    propagation_steps: list[PropagationStep]  # 传播步骤
    sink_function: FuncBlockId | None     # 到达的 sink
    sink_type: SinkType | None            # sink 类型 (SQL/exec/render/...)

@dataclass
class PropagationStep:
    """一步参数传播"""
    from_function: FuncBlockId
    from_param: str                       # caller 的变量名
    to_function: FuncBlockId
    to_param: str                         # callee 的参数名
    transformation: str | None            # 变换类型 ("url_decode", "json_parse", "sanitize", ...)
    code_location: str                    # file:line

@dataclass
class ParameterPropagationGraph:
    """参数传播图 — 白盒扫描的核心数据结构"""
    taint_flows: list[TaintFlow]

    # 索引: 入口点 → 它的所有污点流
    by_entry_point: dict[FuncBlockId, list[TaintFlow]]

    # 索引: sink → 到达它的所有污点流
    by_sink: dict[FuncBlockId, list[TaintFlow]]

    # 索引: 函数 → 它参与的传播步骤
    by_function: dict[FuncBlockId, list[PropagationStep]]
```

#### 3.6.4 确定性参数传播追踪

```python
def build_parameter_propagation_graph(
    call_chains: list[CallChainTree],
    all_blocks: dict[FuncBlockId, FuncBlock],
    entry_points: list[EntryPoint],
    import_graph: ImportGraph,
) -> ParameterPropagationGraph:
    """
    对每条调用链，追踪参数从入口点到 sink 的传播。

    策略:
    1. 入口点参数标记 HTTP 来源
    2. 对每条 CallChainPath，逐函数追踪参数传递
    3. 识别参数变换 (编码/验证/序列化)
    4. 标记到达 sink 的参数
    """
    taint_flows = []

    for entry_point in entry_points:
        # 标记入口参数来源
        marked_params = mark_parameter_sources(entry_point, all_blocks, import_graph)

        for path in entry_point.call_chain_paths:
            # 逐函数追踪
            flows = _trace_taint_along_path(
                entry_point, marked_params, path, all_blocks
            )
            taint_flows.extend(flows)

    return ParameterPropagationGraph(taint_flows=taint_flows)
```

#### 3.6.5 沿调用链追踪污点

```python
def _trace_taint_along_path(
    entry_point: EntryPoint,
    marked_params: list[Parameter],
    path: CallChainPath,
    all_blocks: dict[FuncBlockId, FuncBlock],
) -> list[TaintFlow]:
    """
    沿单条调用链追踪污点传播。

    核心逻辑:
    - entry_point 的参数是 taint source
    - 每一步调用: caller 的实参 → callee 的形参
    - 追踪变量赋值、属性访问、返回值传播
    - 到达 sink 时记录完整路径
    """
    flows = []
    tainted_vars = {}  # variable_name → TaintInfo

    # 初始化: 入口点参数为 taint source
    for param in marked_params:
        if param.source and param.source != ParameterSource.INTERNAL:
            tainted_vars[param.name] = TaintInfo(
                source_param=param.name,
                source_type=param.source,
            )

    for i, node in enumerate(path.nodes):
        block = node.function_block

        # 分析函数体内的数据流
        local_flows = _analyze_local_dataflow(block, tainted_vars)

        # 如果不是最后一个节点，将 taint 传播到下一个调用
        if i < len(path.nodes) - 1:
            next_node = path.nodes[i + 1]
            call_site = _find_call_to(block, next_node.function_block)
            if call_site:
                tainted_vars = _propagate_taint_at_call(
                    call_site, tainted_vars, next_node.function_block
                )

        # 检查是否到达 sink
        sinks = _check_sinks(block, tainted_vars)
        for sink in sinks:
            flows.append(TaintFlow(
                entry_point_id=entry_point.function_block.id,
                source_param=sink.tainted_var,
                source_type=tainted_vars[sink.tainted_var].source_type,
                propagation_steps=local_flows.steps,
                sink_function=block.id,
                sink_type=sink.sink_type,
            ))

    return flows
```

---

## 四、Phase 1 详细设计：Pre-Recon LLM 深度分析

### 4.1 入口点裁决 (1.0)

与当前设计相同，但增强：

```python
# Phase 0 产出的入口点进入裁决
for ep in entry_points:
    if ep.confidence >= 0.85 and ep.source != "llm_batch":
        # 高置信度确定性入口点: 自动确认
        verdict = Verdict.CONFIRMED
    elif ep.source == "llm_batch":
        # Batch LLM 分类结果: 需要主 Agent 复核
        verdict = Verdict.NEEDS_REVIEW
    else:
        # 低置信度: Task Agent 审查
        verdict = Verdict.NEEDS_REVIEW
```

### 4.2 Sink 两步发现 (1.2) — 恢复 Shannon 的强制两步法

```
Step 1 — 全文件 Sink 枚举 (glob + 确定性):
  基于 Phase 0 的 file_manifest.json，对每个源码文件和模板文件
  进行 glob 枚举，建立完整清单。

Step 2 — 逐文件分析 + 跨变体验证:
  对每个文件，分析 sink 类型，区分 escaped/unescaped 模板指令。
  当存在变体目录（brands/locales/themes），强制检查所有变体。
  输出 Template Coverage Audit 表格。
```

### 4.3 报告结构

保持 Shannon 的 10 章结构，但增加：

```
## 11. 覆盖度报告 (新增)
- 已分析文件: 234/250 (93.6%)
- 未分析文件: 16 (列表+原因)
- 已覆盖入口点: 42/45 (93.3%)
- 已覆盖调用链路径: 187/210 (89.0%)
- 已追踪参数: 312/350 (89.1%)
```

---

## 五、Phase 2 详细设计：Recon 攻击面映射

### 5.1 融合确定性数据

Recon Agent 读取以下输入：

1. `pre_recon_deliverable.md` — Pre-Recon 的 LLM 分析
2. `code_index.json` — 确定性代码索引（调用图、参数图）
3. `parameter_graph.json` — 参数传播图

### 5.2 API 端点清单增强

恢复 Shannon 的 **Shared Controller Parameter Propagation** 和 **Parameter Completeness Verification**，并增加：

```
## 4. API Endpoint Inventory

| Method | Endpoint | Required Role | Object ID Params | Auth Mechanism | All Parameters (from Parameter Graph) | Taint Sources |
|---|---|---|---|---|---|---|
| POST | /api/orders | user | order_id | Bearer | user_id(query), items(body), coupon(body) | user_id→PATH, items→BODY, coupon→BODY |

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
Missing parameters are flagged as coverage gaps.
```

### 5.3 注入源追踪 — 参数传播图驱动

```
## 9. Injection Sources

**CRITICAL — No Security Judgments:** (恢复 Shannon)
Your job is to IDENTIFY and REPORT facts about injection sources —
where user-controllable input enters, where it flows, and what sink
it reaches. You MUST NOT make security judgments.

**Parameter Propagation Graph Drive:** (新增)
For each injection source, include the complete taint flow:
- Entry point: POST /api/orders (user_id → PATH_PARAM)
- Propagation: req.params.user_id → order_service.get(user_id) → db.query(sql)
- Sink: db.query() in services/order.py:42
- Taint variables along path: user_id → sql (unsanitized)
```

---

## 六、Phase 3 详细设计：逐链漏洞分析

### 6.1 核心改变：从文档驱动改为调用链驱动

**当前 (Shannon)**: Vuln Agent 读取 `recon_deliverable.md`，自行搜索代码分析漏洞。

**改进 (SCR-AI 策略)**: 对每条 `CallChainPath`，以完整源码+参数传播图作为输入：

```python
async def audit_all_chains(
    call_chains: list[CallChainPath],
    parameter_graph: ParameterPropagationGraph,
    config: Config,
) -> list[VulnFinding]:
    """
    对每条调用链路径运行所有审计 Agent。

    SCR-AI 核心策略: path × agent_type 的笛卡尔积并发。
    """
    semaphore = asyncio.Semaphore(config.max_concurrency)

    async def _audit_path(path: CallChainPath, agent_type: str):
        async with semaphore:
            # 构建包含完整上下文的 prompt
            taint_info = parameter_graph.by_entry_point.get(
                path.entry_point.function_block.id, []
            )
            prompt = format_audit_prompt(path, taint_info, agent_type)
            response = await agent.run(prompt)
            findings = parse_and_validate_findings(response, agent_type)
            return findings

    # 并发执行: path × agent_type
    tasks = []
    for path in call_chains:
        for agent_type in ["injection", "auth", "authz", "xss", "ssrf"]:
            tasks.append(_audit_path(path, agent_type))

    results = await asyncio.gather(*tasks)
    all_findings = [f for r in results for f in r]

    # 五元组去重 (来自 SCR-AI)
    return deduplicate_findings(all_findings)
```

### 6.2 审计 Agent Prompt 格式

每个审计 Agent 收到的输入包含：

```
## Call Chain
entry_point: POST /api/orders/:id → delete_order(id) → cancel_order(order_id) → db.raw_query(sql)

## Function 1: delete_order (routes/order.py:15-20)
[完整源码]

## Function 2: cancel_order (services/order.py:42-55)
[完整源码]

## Function 3: db.raw_query (utils/db.py:10-15)
[完整源码]

## Taint Flow (from Parameter Propagation Graph)
- id (PATH_PARAM) → order_id (param) → order_id (param) → sql (variable)

## Sinks in this chain (from Pre-Recon)
- db.raw_query(): SQL execution sink at utils/db.py:12

## Framework Context
- Python/FastAPI with SQLAlchemy ORM
- Parameter id is extracted from URL path
```

### 6.3 结构化输出校验 (来自 SCR-AI)

```python
VALID_INJECTION_CATEGORIES = {
    "injection": [
        "sql_injection", "command_injection", "path_traversal",
        "ssti", "ssrf", "insecure_deserialization",
        "ldap_injection", "xpath_injection",
    ],
}

VALID_AUTH_CATEGORIES = {
    "auth_missing": ["no_auth_middleware", "auth_bypass", "broken_authentication"],
    "credential_handling": ["weak_password_storage", "insecure_token", "session_fixation"],
}

VALID_AUTHZ_CATEGORIES = {
    "horizontal_privilege_escalation": ["idor", "tenant_isolation", "cross_org_access"],
    "vertical_privilege_escalation": ["missing_role_check", "role_tampering"],
}

def parse_and_validate_findings(
    response: str,
    agent_type: str,
) -> list[VulnFinding]:
    """
    解析 LLM 输出并校验 category/issue_type 合法性。
    不在白名单中的 finding 直接丢弃。
    """
    data = extract_json(response)
    valid_categories = CATEGORY_MAP[agent_type]

    findings = []
    for item in data.get("findings", []):
        category = item.get("category")
        issue_type = item.get("issue_type")

        if category in valid_categories and issue_type in valid_categories[category]:
            findings.append(VulnFinding(**item))
        else:
            logger.warning(
                "Invalid category/issue_type: %s/%s for agent %s",
                category, issue_type, agent_type
            )

    return findings
```

### 6.4 五元组去重 (来自 SCR-AI)

```python
def deduplicate_findings(findings: list[VulnFinding]) -> list[VulnFinding]:
    """五元组去重: (入口点, 类别, 问题类型, 脆弱函数, 调用链路径)"""
    seen: set[tuple] = set()
    unique: list[VulnFinding] = []

    for f in findings:
        key = (
            f.entry_point_id,
            f.category,
            f.issue_type,
            f.vulnerable_function_id,
            tuple(n.function_name for n in f.call_chain_path),
        )
        if key not in seen:
            seen.add(key)
            unique.append(f)

    return unique
```

### 6.5 覆盖度报告

```python
def generate_coverage_report(
    total_chains: int,
    audited_chains: int,
    total_params: int,
    traced_params: int,
    total_files: int,
    analyzed_files: int,
) -> CoverageReport:
    return CoverageReport(
        chain_coverage=audited_chains / total_chains,
        parameter_coverage=traced_params / total_params,
        file_coverage=analyzed_files / total_files,
        unanalyzed_chains=total_chains - audited_chains,
        untraced_params=total_params - traced_params,
        unanalyzed_files=total_files - analyzed_files,
    )
```

---

## 七、关键改进与现有问题的映射

| 问题 | 根因 | 解决方案 | 所在阶段 |
|---|---|---|---|
| **代码文件覆盖不全** | F1-F5 | 3.1 全文件发现 + 多类型支持 | Phase 0.1 |
| **入口找不全** | C2 | 3.4 五策略入口点检测（含命令式路由+框架约定+Schema解析） | Phase 0.4 |
| **路由不准确** | C2+C3 | 3.4 策略B命令式检测 + 3.5.3 消除函数名歧义 | Phase 0.4-0.5 |
| **Sink 找不全** | F2+F4 | 3.1 模板文件扫描 + 4.2 两步Sink发现 + 跨变体验证 | Phase 0.1 + 1.2 |
| **调用链横向不全** | C1+C5 | 3.3 Import图 + 3.5.1 菱形路径保留 | Phase 0.3 + 0.5 |
| **调用链纵向不深** | C4+C7 | 3.5.2 多策略调用解析 + max_width 提升 | Phase 0.5 |
| **污点参数不全** | P1-P5 | 3.6 参数传播图（完整参数+来源标记+传播追踪） | Phase 0.6 |
| **参数来源不明确** | P2 | 3.6.2 框架感知参数来源标记 | Phase 0.6 |
| **XSS 覆盖率盲区** | Shannon 遗漏 | 4.2 两步法+Template Coverage Audit | Phase 1.2 |
| **Recon 做安全判断** | Shannon 遗漏 | 5.3 No Security Judgments 禁令 | Phase 2 |
| **参数覆盖不完整** | Shannon 遗漏 | 5.2 Shared Controller 传播+完整性验证 | Phase 2 |
| **Vuln Agent 不消费调用链** | 架构缺失 | 6.1 逐链分析取代文档分析 | Phase 3 |
| **Finding 噪声** | SCR-AI 遗漏 | 6.3 结构化输出校验 + 6.4 五元组去重 | Phase 3 |

---

## 八、数据流总结

```
源代码仓库
    │
    ▼
Phase 0: 确定性基础
    ├─ file_manifest.json          (全文件清单+类型)
    ├─ code_index.json             (FuncBlock[] + CallEdge[] + EntryPoint[] + CallChain[])
    ├─ import_graph.json           (ImportEdge[] + 符号解析表)
    ├─ parameter_graph.json        (TaintFlow[] + 参数传播步骤)
    └─ coverage_baseline.json      (覆盖率基线)
    │
    ▼
Phase 1: Pre-Recon (LLM)
    ├─ entry_points.json           (裁决结果)
    ├─ pre_recon_deliverable.md    (架构分析+Sink清单+覆盖率审计)
    └─ schemas/                    (API Schema 文件)
    │
    ▼
Phase 2: Recon (LLM)
    └─ recon_deliverable.md        (攻击面地图+参数完整性+注入源)
    │
    ▼
Phase 3: 逐链 Vuln 分析 (LLM per chain)
    ├─ *_analysis_deliverable.md   (每类漏洞分析报告)
    ├─ *_exploitation_queue.json   (可利用漏洞队列)
    ├─ coverage_report.json        (最终覆盖率)
    └─ findings_deduped.json       (去重后的全部发现)
```

---

## 九、与现有系统的兼容性

### 9.1 对 shannon-py 的改造范围

| 模块 | 改造类型 | 说明 |
|---|---|---|
| `code_index/` | **重构** | 增强模型、新增 import_graph 和 parameter_graph |
| `code_index/parser.py` | **增强** | 多语言、全函数类型、完整参数 |
| `code_index/entry_points.py` | **大幅增强** | 从单策略 → 五策略检测 |
| `code_index/call_graph.py` | **重写** | 菱形保留、多策略解析、跨文件 |
| `code_index/` 新增文件 | **新增** | `import_graph.py`, `parameter_graph.py`, `file_discovery.py` |
| `prompts/pre-recon-code.txt` | **增强** | 恢复两步 Sink 分析、覆盖度报告 |
| `prompts/recon.txt` | **增强** | 恢复 No Security Judgments、参数传播、完整性验证 |
| `prompts/recon-static.txt` | **重写** | 对齐 live 版本结构 |
| `prompts/vuln-*.txt` | **重构** | 从文档驱动改为调用链驱动 |
| `pipeline/workflows.py` | **增强** | 新增 Phase 3 逐链分析编排 |
| 新增模块 | **新增** | `coverage/` 覆盖度报告生成 |

### 9.2 向后兼容

- `code_index.json` 格式扩展（新增字段），旧字段保持不变
- `entry_points.json` 格式不变
- Pre-Recon / Recon deliverable 格式兼容
- 新增 `parameter_graph.json` 为可选输入，不存在时降级到当前行为

---

## 十、实施优先级

### P0 — 基础层核心（解决"不全"的根因）

1. **Import 图构建** (解决 C1 跨文件调用断裂) — 2-3 天
2. **多策略入口点检测** (解决 C2 命令式路由遗漏) — 2 天
3. **函数名歧义消除** (解决 C3) — 0.5 天
4. **菱形路径保留** (解决 C5) — 1 天
5. **完整参数提取** (解决 P1) — 1 天
6. **参数来源标记** (解决 P2) — 1 天

### P1 — 增强层（提升覆盖度和准确度）

7. **全文件发现** (解决 F1-F4) — 1 天
8. **TS Arrow Function 提取** (解决 C6) — 1 天
9. **参数传播图** (解决 P3) — 2-3 天
10. **Batch LLM 入口点分类** — 1 天
11. **恢复 Pre-Recon 两步 Sink 分析** — 0.5 天

### P2 — 深度优化

12. **恢复 Recon "No Security Judgments"** — 0.5 天
13. **恢复 Shared Controller 参数传播** — 0.5 天
14. **重写 recon-static.txt** — 1 天
15. **Vuln Agent 逐链分析改造** — 2 天
16. **结构化输出校验** — 1 天
17. **五元组去重** — 0.5 天
18. **覆盖度报告** — 1 天

---

## 十一、验证方案

### 11.1 覆盖度度量

每个 Phase 结束后输出覆盖率：

```
Phase 0 → 文件覆盖率、函数覆盖率、入口点覆盖率
Phase 1 → Sink 覆盖率 (发现的 Sink / 总模板文件)
Phase 2 → 参数覆盖率 (枚举的参数 / 完整参数列表)
Phase 3 → 调用链审计覆盖率 (已审计链 / 总链数)
```

### 11.2 基准测试用例

| 场景 | 验证目标 |
|---|---|
| Express 路由 `(req, res) => {}` | 入口点检测 + 参数提取 |
| Python `**kwargs` 传递 | 参数传播 |
| 多文件 `from utils import helper` | Import 解析 + 跨文件调用 |
| `obj.process()` 多态 | 多策略调用解析 |
| A→B→D + A→C→D 菱形 | 路径保留 |
| Laravel `Route::get('/path', ...)` | 命令式路由检测 |
| EJS `<%= %>` vs `<%- %>` | 模板 Sink 分析 |
| `req.body.user_id → SQL query` | 完整污点追踪 |
| Next.js `pages/api/*.ts` | 框架约定检测 |

---

*设计版本: v1.0 | 日期: 2026-06-04 | 基于 Shannon (TS) + SCR-AI (Py) + shannon-py (Py) 三方融合*
