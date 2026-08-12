# OpenAnt 实现审计结论

- **检查日期**：2026-08-12
- **检查对象**：`/root/OpenAnt`
- **检查版本**：`392de75b5d3a964285e13f586ce02a5fe673416e`（`master`）
- **检查范围**：source/sink、调用图与可达性、漏洞研判、防护判断、越权/未授权分析、白盒与动态验证
- **验证情况**：与入口识别、可达性、Stage 2 验证、动态测试相关的 78 个回归测试通过

## 1. 总体结论

OpenAnt 的实际定位是：

> **LLM 驱动的 SAST + 静态调用图可达性分析 + 可选 Docker 动态复现。**

它不是传统意义上具备完整变量级污点传播的 source-to-sink 分析器。OpenAnt 能较好地回答：

```text
某个外部入口是否可以通过静态调用链到达危险函数？
```

但不能仅凭静态调用图证明：

```text
攻击者控制的具体变量一定以可利用形式到达 sink。
```

具体的输入传播、校验绕过、防护有效性和攻击影响，主要依赖 Stage 1/Stage 2 的 LLM 语义判断。

## 2. Source 和 Sink 识别

### 2.1 Source：以入口点和外部输入启发式为主

核心代码：

- `libs/openant-core/utilities/agentic_enhancer/entry_point_detector.py`
- `libs/openant-core/core/llm_reachability.py`

`EntryPointDetector` 并不建立变量级 source 集合，而是把可能接收外部输入的函数标记为 entry point。

识别依据包括：

| 识别维度 | 代表性规则 |
|---|---|
| 函数类型 | `route_handler`、`view_function`、`websocket_handler`、`cli_handler`、`main`、Go `init`、HTTP handler、middleware |
| 路由/装饰器 | `@app.route`、`@app.get`、`@router.post`、`@Controller`、PHP `#[Route]`、Swift `@objc`/`@IBAction` |
| HTTP 输入 | `request.args`、`request.form`、`request.json`、`req.body`、`req.query`、`req.params` |
| CLI/stdin | `sys.argv`、`argparse`、`input()`、`sys.stdin`、`ARGV`、`STDIN` |
| 文件/环境变量 | `open(..., "r")`、`Path.read_*`、`os.environ`、`os.getenv` |
| WebSocket/IPC | `websocket.receive`、`message.data`、XPC、网络监听等 |
| PHP 输入 | `$_GET`、`$_POST`、`$_REQUEST`、`php://input` |

另外，`--llm-reachability` 可以让 LLM 补充识别：

- `entry_point`
- `external_input`
- `cross_process`

但高置信度的 LLM 入口信号主要用于增加 BFS seed，并不会自动构建完整的跨进程或变量级污点图。

### 2.2 Sink：没有统一的静态 Sink 规则库

OpenAnt 没有发现统一的：

```text
SOURCE_PATTERNS
SINK_PATTERNS
TAINT_RULES
```

危险操作主要通过 LLM 提示词进行语义识别，例如：

- `eval` / `exec`
- SQL 查询
- 文件读写
- 命令执行
- 反序列化
- `innerHTML`
- SSRF 相关网络请求

`context_enhancer.py` 中存在 `security_relevant_flows` 数据结构，可以让 LLM 输出类似：

```json
{
  "source": "req.body.query",
  "sink": "sql.query()",
  "type": "potential SQL injection"
}
```

但该结果主要写入 `unit["llm_context"]`，用于保存和统计，并没有转化成正式的、可计算的污点图，也不是后续 reachability 的主要判定依据。

### 2.3 CodeQL 的角色

JavaScript、Ruby 等语言的 parser 可以调用外部 CodeQL：

- 创建 CodeQL database
- 执行语言安全查询集
- 读取 SARIF
- 按文件和行号把结果映射到函数 unit

CodeQL 在 OpenAnt 中主要是 **可选预过滤器**，不是 OpenAnt 统一维护的 source/sink 引擎。且不同语言支持程度不完全一致，Python 中央 parser 路径明确提示 CodeQL/exploitable filter 尚未完整接入。

### 2.4 结论

| 能力 | 判断 |
|---|---|
| 入口/source 识别 | 有，规则较丰富，但本质是函数/代码模式启发式 |
| 变量级 source 提取 | 没有完整实现 |
| Sink 识别 | 主要依赖 LLM，CodeQL 可选 |
| Source-to-sink 污点传播 | 没有统一、确定性的实现 |
| Source/sink 数据流 | 可由 LLM 描述，但不是形式化证明 |

## 3. 调用链和可达性

### 3.1 调用图结构

各语言 parser 会生成两张图：

```text
call_graph:
    caller -> callee

reverse_call_graph:
    callee -> callers
```

Python、JavaScript、Go、C、PHP、Ruby、Rust、Swift、Zig 等 parser 都有对应的调用图构造逻辑。调用解析通常覆盖：

- 同文件函数
- import/include 依赖
- 类方法与 receiver
- `self.method()` / `super.method()`
- 构造函数
- 部分 callback、middleware、函数指针和框架注册
- JavaScript Express middleware 等显式调用边

Unit generator 会把直接调用者、直接被调用者以及一定深度的依赖代码放入分析 unit，供 Stage 1 使用。

### 3.2 单个目标的可达性查询：反向追踪

`ReachabilityAnalyzer.is_reachable_from_entry_point()` 的逻辑是：

```text
目标函数/sink
    <- 谁调用它
        <- 谁调用调用者
            <- 是否到达 entry point
```

也就是从目标函数开始，沿 `reverse_call_graph` 反向 BFS。

### 3.3 全量过滤：正向 BFS

`get_all_reachable()` 会从 entry points 开始，通过调用图进行正向 BFS：

```text
entry point/source
    -> callee
        -> callee
            -> sink/target
```

因此：

> **语义上是 source/entry -> sink；单目标实现采用 sink -> source 的反向查询；全量过滤采用 source -> sink 的正向 BFS。**

### 3.4 可达性判断的边界

#### 不是变量级数据流

调用图只能证明函数调用关系，不能证明同一个攻击者控制的变量在中间没有被：

- 覆盖
- 转换
- 编码
- 校验
- 截断
- 替换

这些判断交给 LLM。

#### 动态调用可能漏报

以下场景可能无法完全进入静态调用图：

- 反射
- 字符串函数名
- 依赖注入
- 事件总线
- 消息队列
- 动态路由注册
- 跨进程/异步调用
- 复杂函数指针或运行时生成代码

LLM reachability 主要是补充入口点，不能完全替代调用图补边。

#### 没有入口点时会安全降级

如果没有检测到入口点，parser 不会把全部函数静默裁剪掉，而是保留全部 unit 并输出警告。因此：

> “没有被判断为 reachable”不一定等价于“代码确实不可达”，可能只是入口点识别失败后触发了 pass-through。

#### 库模式

`library_mode` 可以把导出的公共 API 作为 reachability seed。否则纯库项目可能没有传统的 `main` 或路由入口。

## 4. 漏洞研判和防护判断

### 4.1 Stage 1：漏洞初筛

Stage 1 要求 LLM：

1. 说明目标函数行为；
2. 指出输入来自哪里以及谁可以控制；
3. 给出具体攻击 payload；
4. 说明 payload 是否通过校验；
5. 说明到达了哪个危险操作；
6. 说明攻击者获得什么未授权能力。

输出分类：

```text
safe
protected
bypassable
vulnerable
inconclusive
```

系统提示词要求只有在“具体 payload + 完整到达路径 + 攻击影响”都成立时才判定为 `vulnerable`。

### 4.2 防护识别

防护由 LLM 结合目标函数、依赖上下文和应用上下文判断，可能包括：

- 参数校验
- allowlist
- 路径规范化
- 编码/转义
- 参数化 SQL
- authentication/authentication middleware
- authorization/ownership check
- 输入过滤
- 沙箱
- 反序列化限制
- URL/命令白名单

`protected` 和 `security_control` 并不是形式化验证结果，而是 LLM 认为当前防护有效的判断。

### 4.3 Stage 2：白盒攻击路径验证

Stage 2 只处理 Stage 1 判为 `vulnerable` 或 `bypassable` 的结果。它通过源码搜索和读取工具补充上下文：

- `search_usages`
- `search_definitions`
- `read_function`
- `list_functions`

Stage 2 的 `exploit_path` 要求包含：

```json
{
  "entry_point": "...",
  "data_flow": ["...", "..."],
  "sink_reached": true,
  "attacker_control_at_sink": "full | partial | none",
  "path_broken_at": null
}
```

完整可利用路径要求：

- 有 entry point；
- `sink_reached == true`；
- sink 处仍有 `full` 或 `partial` 攻击者控制；
- 路径没有被防护打断。

如果 Stage 2 因模型输出错误、无 tool call、达到最大迭代次数等原因无法完成，不会直接当成安全，而是标记为 `unverified` / `needs_review`。

## 5. 越权和未授权漏洞

### 5.1 没有专门的越权分析器

当前未发现专门的：

- IDOR/BOLA 检测器
- 水平越权/垂直越权规则
- `principal-resource-action` 权限模型
- 资源归属传播分析
- 角色/租户/用户状态的程序化验证
- 专门的 CWE-862/CWE-863/CWE-639 分析流程

这类漏洞主要依赖通用 LLM 语义分析。

### 5.2 当前实际分析流程

1. 识别 HTTP、消息或任务入口；
2. 识别认证 middleware、session、token、角色和资源 ID；
3. 检查是否验证资源归属、租户边界或管理员权限；
4. Stage 2 尝试匿名、低权限、修改 `user_id`/`tenant_id`/`object_id` 等攻击方式；
5. 根据是否能够获得其他用户或更高权限资源来判断影响。

Express middleware 会被 parser 作为调用边保留，但 parser 只能说明 middleware 被挂载或调用，不能证明 middleware 的权限逻辑本身有效。

### 5.3 Threat Model 对越权分析的帮助

仓库可以通过 `OPENANT.THREATMODEL.md` 声明：

- 攻击者角色与能力；
- 攻击者不能做什么；
- 输入源及信任等级；
- 漏洞判定准则；
- 哪些行为不算漏洞；
- 成功攻击后的影响。

这对多租户、低权限用户、供应链攻击者、相邻攻击者等场景尤其重要。没有自定义 threat model 时，Stage 2 默认采用“互联网攻击者、只有浏览器、没有服务器权限和管理员凭据”的模型。

### 5.4 越权分析的主要风险

- 没有显式建模“当前用户—资源—动作—权限”；
- 不能程序化证明所有路径都经过同一个授权检查；
- Stage 2 依赖 LLM 是否主动尝试修改对象 ID 和角色边界；
- 如果使用较激进的 `exploitable` 过滤，纯权限逻辑可能被分类或过滤掉；
- Stage 2 工具中的 `search_usages` 主要是源码正则搜索，不等价于完整语义调用图。

因此，越权结论的可信度通常低于直接的命令注入、SQL 注入、路径遍历或 SSRF。

## 6. 黑盒/动态验证

### 6.1 有动态验证，但默认关闭

OpenAnt 提供 `dynamic-test` 和 scanner 中的动态测试阶段，默认：

```text
dynamic_test = false
```

流程为：

1. LLM 根据 finding 生成 Dockerfile 和 exploit test；
2. 在 Docker 中构建并运行；
3. 解析测试脚本输出的结构化 JSON；
4. 返回 `CONFIRMED`、`NOT_REPRODUCED`、`BLOCKED`、`INCONCLUSIVE` 或 `ERROR`；
5. 收集文件读取、HTTP 响应、命令输出、网络捕获等证据。

### 6.2 Docker 隔离

单容器执行使用：

- 无 host volume mount；
- 非 privileged；
- read-only root filesystem；
- `/tmp`、`/root` 使用 tmpfs；
- 512 MB 内存；
- 1 CPU；
- `no-new-privileges`；
- 构建和执行超时。

### 6.3 动态验证的限制

它更接近：

> **LLM 生成的隔离环境 exploit reproduction test**

而不是完整真实部署上的 DAST。常见限制：

- 测试由 LLM 自动生成，可能存在测试逻辑错误；
- 通常只预置 finding 对应源码文件，而不是完整仓库运行环境；
- 未必包含真实数据库、session、RBAC、反向代理和部署拓扑；
- 未必验证真实生产配置下的认证和授权行为；
- 不支持所有语言的动态模板；
- 运行失败可能来自构建或依赖问题，而不是漏洞不存在。

### 6.4 动态结果不会自动改写最终 verdict

动态结果会作为 `dynamic_testing` 字段合并到报告中，但当前实现不会自动执行：

```text
CONFIRMED       -> 自动升级为最终漏洞
NOT_REPRODUCED  -> 自动降级为安全
```

因此动态测试提供的是证据和人工复核依据，不是最终 verdict gate。

此外，动态测试只针对以下 Stage 2 结果：

```text
confirmed
agreed
vulnerable
```

`safe`、`protected`、`inconclusive`、`unverified`、`rejected` 等结果不会自动进入动态测试。

## 7. 工程能力评价

| 能力 | 评价 | 说明 |
|---|---|---|
| 入口/source 识别 | 中等偏强 | 框架和输入模式覆盖较丰富，但属于启发式函数级识别 |
| Sink 识别 | 中等 | 依赖 LLM，CodeQL 可选，没有统一 sink 规则库 |
| 直接调用可达性 | 中等偏强 | 双向调用图和 BFS 完整，直接调用场景较实用 |
| 动态调用/跨进程可达性 | 中等偏弱 | 依赖 parser 能力和可选 LLM reachability |
| 变量级 source-to-sink | 偏弱 | 没有完整的确定性污点传播 |
| 常见注入漏洞研判 | 中等偏强 | Stage 1 + Stage 2 能提供具体 payload 和 exploit path |
| 防护判断 | 中等 | 主要依赖 LLM 语义判断，不是形式化验证 |
| 越权/未授权 | 中等偏弱 | 缺少权限模型和专门规则，依赖 threat model 与 LLM |
| 黑盒验证 | 有，但受限 | Docker 动态复现可用，但默认关闭且不等价真实部署测试 |

## 8. 最终使用建议

如果目标是验证 SQL 注入、命令注入、路径遍历、SSRF、反序列化等危险操作，OpenAnt 的推荐使用方式是：

1. 保留完整的 reachable dataset；
2. 运行 Stage 1；
3. 使用 Stage 2 attacker simulation；
4. 对 confirmed/agreed/vulnerable 结果启用 Docker dynamic test；
5. 对动态测试结果进行人工复核。

如果目标是越权、IDOR、BOLA 或多租户权限边界：

1. 不要只依赖 `exploitable` 过滤；
2. 提供详细的 `OPENANT.THREATMODEL.md`；
3. 确保 route、middleware、controller、service 和 resource ownership 代码都进入分析上下文；
4. 让 Stage 2 明确尝试匿名用户、低权限用户、跨租户 ID 和高权限动作；
5. 最终使用真实 API/集成环境进行黑盒验证。

**一句话结论：**

> OpenAnt 能较好地证明“静态调用路径存在”，也能让 LLM 对攻击路径和防护进行白盒推理；但它目前不是完整的 source-to-sink 污点分析器，越权判断主要依赖 LLM，动态测试是可选的隔离复现而不是自动决定最终漏洞结论。

## 9. 关键代码位置

| 文件 | 作用 |
|---|---|
| `utilities/agentic_enhancer/entry_point_detector.py` | 入口点和外部输入启发式识别 |
| `utilities/agentic_enhancer/reachability_analyzer.py` | reverse/forward BFS 可达性分析 |
| `parsers/python/call_graph_builder.py` | Python 调用图构造示例 |
| `parsers/javascript/dependency_resolver.js` | JavaScript 调用图和 middleware 边 |
| `core/parser_adapter.py` | reachability filter 和 parser 编排 |
| `prompts/vulnerability_analysis.py` | Stage 1 漏洞分析提示词 |
| `prompts/verification_prompts.py` | Stage 2 attacker simulation 提示词 |
| `utilities/finding_verifier.py` | Stage 2 tool loop、exploit path 和失败安全处理 |
| `core/verifier.py` | Stage 2 结果筛选和合并 |
| `core/llm_reachability.py` | 可选 LLM reachability 补充 |
| `utilities/dynamic_tester/` | Docker 动态 exploit 测试 |
| `report/generator.py` | 动态测试结果合并到报告 |
