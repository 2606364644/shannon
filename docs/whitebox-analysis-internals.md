# Shannon 白盒分析内部机制

> 本文档描述 Shannon **白盒（源码）分析**的内部工作机制：sink 点、入口点、调用链、漏洞研判四件事分别是怎么完成的，以及贯穿其中的 **主 agent 与子 agent** 协作模型。
>
> 与 [`whitebox-blackbox-scan.md`](./whitebox-blackbox-scan.md) 互补：那篇讲扫描**模式**（命令、决策树、用法），本篇讲分析**机制**（agent 如何读代码、如何判定）。

---

## 0. 一句话架构

**白盒分析确实会逐文件读取目标源码——但做这件事的是 Claude Code 会话（LLM 调用内置的 Read/Grep/Glob 工具），不是 Shannon 自己的 TypeScript 代码。** Shannon 的 TS 层没有静态分析引擎（无 CodeQL / Semgrep / tree-sitter / AST / 调用图），也不直接读源码；它只负责调度（Temporal）、prompt 编排与产物校验，真正读代码、追链、下判断的是 Claude Code 会话本身。

核心入口在 `apps/worker/src/ai/claude-executor.ts:139` 的 `runClaudePrompt`，它调用 `@anthropic-ai/claude-agent-sdk` 的 `query()`，把 prompt 连同 `cwd: sourceDir`（目标仓库根目录）丢给一个 Claude Code 会话。会话配置见 `claude-executor.ts:234-244`：

```ts
model,                          // 由 modelTier 解析（pre-recon 用 large）
maxTurns: 10_000,               // 单个 phase 会话最多 1 万轮工具调用
cwd: sourceDir,                 // 直接在目标仓库根目录运行
permissionMode: 'bypassPermissions',
settingSources: ['user'],       // 继承用户级 MCP/设置
```

这个会话自带 **Claude Code 全套内置工具**：`Read` / `Glob` / `Grep` / `Bash` / `Edit` / `Write`，以及 **`Task`（spawn 子代理）**。Shannon **没有显式增删工具**（`claude-executor.ts` 中无 `mcpServers` / `allowedTools` 配置），`Task` 是 Claude Code 内置的子代理机制，不是 Shannon 自己实现的。

---

## 1. 主 agent 与子 agent（核心模型）

这是理解后续四点的基础。Shannon 的 agent 体系是**两层**结构。

### 1.1 第一层：phase agent（"主 agent"）

每个流水线阶段对应一个 **phase agent**，在 `apps/worker/src/session-manager.ts:14` 的 `AGENTS` 注册表里定义。每个 phase agent 字段如下：

| 字段 | 含义 | 示例 |
|---|---|---|
| `promptTemplate` | 指向 `apps/worker/prompts/*.txt` 的模板 | `pre-recon-code` |
| `deliverableFilename` | 该 agent 的输出文件名 | `pre_recon_deliverable.md` |
| `prerequisites` | 依赖的前置 agent（决定执行顺序） | `['recon']` |
| `modelTier` | 模型规模 | `pre-recon` 用 `large` |

关键的几个 phase agent（`session-manager.ts:14-71`）：

| Phase agent | promptTemplate | modelTier | 角色 |
|---|---|---|---|
| `pre-recon` | `pre-recon-code` | `large` | **唯一拥有完整源码访问权的 agent**，建立架构基线 + sink/入口点清单 |
| `recon` | `recon` | 默认 | 攻击面映射，产出 `recon_deliverable.md`（含 Section 4.1/4.2 共享 handler、中间件链） |
| `injection-vuln` / `xss-vuln` / `auth-vuln` / `ssrf-vuln` / `authz-vuln` / `misconfig-vuln` | `vuln-*` | 默认 | 6 个漏洞分析专项，`prerequisites: ['recon']`，**并行执行** |
| `injection-exploit` 等 | `exploit-*` | 默认 | 漏洞利用验证，`prerequisites` 对应 vuln |

**每个 phase agent 在运行期 = 一个独立的 Claude Code 会话**（一次 `query()` 调用）。Temporal activity 调用 `runClaudePrompt`，后者启动会话、流式接收消息、最后校验 deliverable 是否生成（`claude-executor.ts:83-135` 的 `validateAgentOutput`）。

phase agent 之间**通过 deliverable 文件传递信息**，不共享内存上下文。例如 `injection-vuln` 不会读到 `pre-recon` 会话里的对话历史，只能读 `pre_recon_deliverable.md` 这个磁盘文件。

### 1.2 第二层：Task 子 agent

每个 phase 会话（主 agent）在运行中，可以用内置 **`Task` 工具**再 spawn 出**子 agent**。子 agent 是一个**独立的 Claude Code 子会话，拥有自己的 context window**，干完活只把**结论**返回给主 agent。

两层关系如下：

```
Temporal workflow
  │
  │  调用 runClaudePrompt(prompt, cwd=目标仓库根目录)
  ▼
┌─────────────────────────────────────────────────────────┐
│  Phase Agent 会话（主 agent）  ← 一个 query() 会话        │
│  context window A，maxTurns=10_000                        │
│                                                           │
│  持有：前置 deliverable 内容、prompt 指令、汇总结论        │
│                                                           │
│  禁止直接 Read 源码（prompt 强制）                          │
│                                                           │
│     │  spawn                                               │
│     ▼                                                      │
│  ┌────────────────────────────────────┐  ┌─────────────┐ │
│  │ Task 子 agent（独立子会话）          │  │ Task 子 agent │ │
│  │ context window B（隔离）             │  │ ...           │ │
│  │                                      │  │               │ │
│  │ 用 Glob/Grep/Read 大量读代码          │  │ 并行           │ │
│  │ → 只返回结论（sink 列表/file:line）   │  │               │ │
│  └────────────────────────────────────┘  └─────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### 1.3 为什么强制主/子两层？

prompt 明确禁止主 agent 直接读源码（`pre-recon-code.txt:180`、`vuln-injection.txt:91`）：

> Do NOT use Read, Glob, or Grep tools for source code analysis. All code examination must be delegated to Task agents.
>
> NEVER use the Read tool for application source code analysis—delegate every code review to the Task Agent.

原因有三：

1. **Context 隔离**：白盒分析要读的代码量远超单会话 context window。子 agent 用完即弃，只回吐结论，主 agent 的 context 保持干净，可以容纳所有子 agent 的汇总 + 自己的推理。
2. **并行加速**：主 agent 一次性 spawn 多个 Task 子 agent 并行扫描（见 `pre-recon-code.txt:162`、`:165` 的"Launch all ... in parallel using multiple Task tool calls in a single message"）。
3. **聚焦判定**：主 agent 的职责被限制为"派活 + 汇总 + 下结论"，把繁琐的代码翻阅下放给子 agent，避免主 agent 在细节里跑偏。

**这正是 `maxTurns: 10_000` 存在的根因**：追一条调用链可能要几十次 Read/Grep，覆盖一个仓库要 spawn 几十上百个子 agent，每个子 agent 自己也有大量工具调用。10 万级 turn 预算对应的是这种"主 agent 持续派活、子 agent 持续读码"的长时间 agentic 循环。

### 1.4 主/子 agent 职责对照

| 维度 | 主 agent（phase 会话） | 子 agent（Task spawn） |
|---|---|---|
| 数量 | 每阶段 1 个 | 主 agent 按需 spawn，可并行多个 |
| Context window | 独立，保留全程 | 独立，用完即弃，只回结论 |
| 读源码 | **禁止**（prompt 强制） | 允许，且是主要工作 |
| 读 deliverable | 允许（前置产物） | 一般不读，只做主 agent 指派的窄任务 |
| 下漏洞判定 | 负责（slot 匹配、verdict） | 不负责，只提供事实（sink 位置、数据流路径） |
| 落盘 deliverable | 负责（`save-deliverable`） | 不负责 |
| 工具集 | Claude Code 内置全套 | Claude Code 内置全套（继承） |

### 1.5 子 agent 的派发：由谁、按什么、多少个

**派发是主 agent（LLM）的运行时行为，Shannon 的 TS 代码完全不参与。** `runClaudePrompt` 只启动一个 phase 会话；会话内 spawn 几个 Task、何时 spawn，全由主 agent 自主决定。Shannon 代码不追踪子 agent 数量，只看到一个 `query()` 会话跑完、deliverable 落盘。

prompt 不写"spawn N 个"，而是定义**派发单元**（按什么粒度切任务）。主 agent 读前置 deliverable，按这个粒度决定 spawn 数量。两种模式：

**模式 A —— 角色固定派发（pre-recon 阶段）**

prompt 直接列出要 spawn 的子 agent 角色，数量与仓库大小无关：

- Phase 1 固定 3 个（`pre-recon-code.txt:111-122`）：Architecture Scanner · Entry Point Mapper · Security Pattern Hunter
- Phase 2 固定 3 个（`pre-recon-code.txt:124-142`）：XSS/Injection Sink Hunter · SSRF Tracer · Data Security Auditor

不管目标代码是 100 行还是 100 万行，pre-recon 都 spawn 这 6 个角色子 agent（Phase 1 全返回才启 Phase 2，见 6.1）。

**模式 B —— 数据驱动派发（vuln 阶段）**

派发单元 = 每个 source / 每条路径，数量由前置 deliverable 决定。以 `injection-vuln` 为例（`vuln-injection.txt:140-141`）：

> Create a To Do for each Injection Source ... section 7 ... create a task for each discovered Injection Source.

主 agent 读 `pre_recon_deliverable.md` Section 7 的 source 列表 → TodoWrite 建清单（每 source 一项）→ 逐 source spawn Task 追链。source 有几个派几条，path forking 时一条链还可能再拆（`vuln-injection.txt:145`）。

**"分析 sink" 的派发要分两层**（对应 2.1 的发现/验证分工）：

| 层级 | 在哪 | 模式 | 数量 |
|---|---|---|---|
| 发现 sink | pre-recon Sink Hunter | A（角色固定） | 1 个 Sink Hunter 子 agent；它内部 glob 后逐文件 Read，是否对每文件/变体再 spawn **未强制**，由该子 agent 自主 |
| 验证 sink 调用链 | vuln `injection-vuln` 等 | B（数据驱动） | ≈ Section 7 的 source 数，每 source 一条追链子 agent |

**数量级**（无固定值，给量级感）：pre-recon 固定 6 个；每个 vuln agent ≈ 其前置 Section 的 source/sink 条数（小仓库几个，大仓库十几个）；6 个 vuln agent 并行 → 整个白盒子 agent 总数从十几个到上百个。这就是 `maxTurns: 10_000` 的实际压力来源（主 agent 持续 spawn + 子 agent 各自几十次工具调用）。

---

## 2. 如何分析 sink 点

### 2.1 在哪个阶段

sink 点的**发现**集中在 **pre-recon 阶段**（`pre-recon-code.txt`）—— 如 1.1 所述，它是唯一有完整源码访问权的 phase。下游 vuln agent 在此基础上做**验证**（见第 5 节），不重新发现 sink。

### 2.2 流程（主 agent 派给子 agent）

`pre-recon-code.txt:128-136` 规定了**强制两步流程**，由主 agent spawn 的 **"XSS/Injection Sink Hunter" 子 agent** 执行：

**Step 1 — 模板文件清单（glob 枚举）**
子 agent 用 **Glob 工具**枚举所有模板/视图文件，覆盖常见扩展名：`html, ejs, hbs, pug, jsx, tsx, vue, svelte, php, erb, jinja2, tmpl` 等。产出目录树形式的清单。若无模板文件，显式报告 "no template files found" 并跳过 Step 2。

**Step 2 — 逐文件 sink 分析（区分转义模式）**
对清单里**每个**模板文件，子 agent 用 **Read 工具**读全文，识别危险 sink 并**区分转义模式**：

- 服务端模板引擎：转义指令（EJS `<%= %>`、Jinja2 `{{ }}`）vs 不转义指令（EJS `<%- %>`、Jinja2 `{{|safe}}`）
- 裸的不转义输出（无 `JSON.stringify` 包裹）标为**最高风险**
- 非模板 sink 同步扫：XSS（`innerHTML`、`document.write`）、SQL 注入点、命令注入（`exec`、`system`）、文件包含/路径穿越（`fopen`、`include`、`require`、`readFile`）、反序列化（`pickle`、`unserialize`、`readObject`）

**强制变体覆盖审计**（`pre-recon-code.txt:135-136`）：模板若存在于变体目录（品牌、语言、主题、子应用），同名模板必须**逐一独立分析**，禁止假设变体模板内容相同。

### 2.3 分析粒度：模板文件 vs 业务代码两条路径

sink 发现按文件类型分两条路径，粒度不同：

| 文件类型 | 发现路径 | 粒度 |
|---|---|---|
| 模板/视图文件（`.ejs`/`.hbs`/`.jinja2`/`.vue`…） | Step 1 的 glob 清单 → **逐文件整文件 Read** | 整文件读，因为转义指令的判定依赖文件内相邻上下文 |
| 业务代码（`.js`/`.ts`/`.py`/`.go`…） | **跨仓库 Grep 危险 API 名**（`exec\(`、`\.query\(`、`eval\(`、`pickle.loads`）→ 命中后再 Read 那个文件确认上下文 | Grep 驱动，不逐文件整读 |

**为什么不把业务代码也逐文件整读？** 量大、慢、烧 token。Grep 先定位"哪些文件、哪些行有危险 API"，子 agent 只 Read 这些命中点。模板文件之所以整文件读，是因为 sink（转义指令）的判定依赖文件内相邻上下文，单点 Grep 命中无法判断转义模式。

**大文件整读会爆 context 吗？** 不会。整文件 Read 由子 agent 执行，子 agent 有独立 context window 且用完即弃（见 1.3）—— 塞满的是子 agent 的 context，不影响主 agent。

### 2.4 sink 判定的"规则库"

Shannon **没有 sink 规则数据库**。sink 类别清单**硬编码在 prompt 文本**里，作为 LLM 的检测目录：
- XSS sink 目录：`pre-recon-code.txt:289-321`（HTML body / 属性 / JS / CSS / URL 五类上下文，逐类列举 API）
- SSRF sink 目录：`pre-recon-code.txt:333-415`（HTTP client、原始 socket、URL opener、headless 浏览器、媒体处理器、link preview、webhook tester、JWKS fetcher、importer、cloud metadata 等 13 个子类）

子 agent 按这份目录去代码里找匹配，结果汇总写入 `pre_recon_deliverable.md` 的 **Section 9（XSS Sinks）** 和 **Section 10（SSRF Sinks）**，作为下游所有 agent 的"已知 sink 清单"。

### 2.5 网络可达性过滤

所有 sink 落盘前都要过**网络可达性**过滤（`pre-recon-code.txt:189-205`）：只有能被部署后的应用服务器通过网络请求（直接或间接）触发的组件才算 in-scope。本地 CLI 工具、CI/CD 脚本、数据库迁移脚本、本地开发服务器、需手动在浏览器打开的静态文件，一律 out-of-scope。

---

## 3. 如何分析入口点

### 3.1 在哪个阶段

入口点分析在 **pre-recon 阶段**，由主 agent spawn 的 **"Entry Point Mapper" 子 agent** 完成（`pre-recon-code.txt:118-119`），与 sink 发现同属 Phase 1（见 6.1）。和 sink 一样分两层：**pre-recon 发现**（Section 5）→ **recon 细化**（Section 4.1/4.2）。本节讲发现层，细化层见 3.5。

### 3.2 流程（主 agent 派 Entry Point Mapper 子 agent）

Entry Point Mapper 子 agent（`pre-recon-code.txt:118-119`）做三件事：

1. **枚举所有网络可达入口**：用 **Grep/Glob** 找路由定义（Express `app.get`/`router.post`、Flask `@app.route`、Spring `@RequestMapping`、Gin `router.GET` 等），再 **Read** 路由文件，提取每个 endpoint 的 HTTP 方法、路径、中间件链。
2. **识别 API schema 文件（半结构化捷径，优先）**：若项目有 OpenAPI/Swagger、GraphQL、JSON Schema 文件，子 agent 直接读取 —— schema 本身就是结构化入口清单，不必从代码逆向。主 agent 在汇总阶段把这些 schema 文件**复制**到 `.shannon/deliverables/schemas/`（`pre-recon-code.txt:149-152`），供下游 agent 直接消费。
3. **标注认证 + 排除本地工具**：每个入口标注 **public 还是需认证**（`pre-recon-code.txt:119` "Distinguish between public endpoints and those requiring authentication"）；同时**排除** local-only dev tools、CLI scripts、build processes（网络可达性过滤，与 sink 共用同一套，见 2.5）。

### 3.3 分析粒度：代码逆向 vs schema 直读两条路径

与 sink 类似，入口点发现按来源分两条路径，粒度不同：

| 来源 | 发现路径 | 粒度 |
|---|---|---|
| 代码里的路由声明 | **Grep 路由装饰器/方法名**（`app.get`、`@app.route`、`@RequestMapping`）→ Read 命中文件提取路由 | Grep 驱动，命中点 Read |
| API schema 文件 | **直接 Read 整个 schema**（OpenAPI / GraphQL / JSON Schema） | 整文件读，schema 即清单 |

**为什么 schema 路径优先？** schema 是声明式的结构化入口清单，一个文件就列全所有 endpoint + 参数 + 认证要求，比从分散代码逆向更完整、更快。代码逆向是 schema 缺失时的兜底，也用来交叉验证 schema 是否覆盖了实际路由（防止 schema 过时）。

### 3.4 入口点识别目录

和 sink 一样，Shannon **没有入口点规则数据库**，识别目录**硬编码在 prompt 文本**（`pre-recon-code.txt:119`）：

| 类别 | 目录内容 |
|---|---|
| **入口类型** | API endpoints · web routes · webhooks · file uploads · externally-callable functions |
| **schema 文件类型** | OpenAPI/Swagger（`*.json`/`*.yaml`/`*.yml`）· GraphQL（`*.graphql`/`*.gql`）· JSON Schema（`*.schema.json`） |
| **认证标注** | 每个入口标 public / 需认证 |
| **排除（out-of-scope）** | local-only dev tools · CLI scripts · build processes（详见 2.5 的网络可达性过滤） |

子 agent 按这份目录找匹配，汇总写入 `pre_recon_deliverable.md` 的 **Section 5（Attack Surface）**，含四项（`pre-recon-code.txt:243-246`）：External Entry Points（每个公开接口）、Internal Service Communication（服务间信任）、Input Validation Patterns（输入校验模式）、Background Processing（网络请求触发的异步任务）。

### 3.5 发现 → 细化两层（对应 sink 的发现/验证）

入口点处理和 sink 一样分两层，只是第二层叫"细化"而非"验证"：

| 层级 | 在哪 | 做什么 | 产出 |
|---|---|---|---|
| **发现** | pre-recon Entry Point Mapper | 枚举所有网络可达入口 + schema + 认证标注 | `pre_recon_deliverable.md` Section 5 |
| **细化** | recon 阶段 | 把入口按"共享 handler"分组，补全每个 endpoint 的 auth/middleware 链 | `recon_deliverable.md` Section 4.1（Shared Controller Route Groups）+ Section 4.2（Endpoint Security Context） |

细化层的 Section 4.1/4.2 是后续调用链追踪（第 4 节的 `_cross-route-enumeration.txt`）和漏洞研判（第 5 节的 `affected_routes`）的关键索引 —— 没有 4.1 的共享 handler 分组，主 agent 就无法判断一个漏洞影响几条路由。

---

## 4. 如何分析调用链

### 4.1 核心事实：没有调用图，纯 LLM 工具跳转

**Shannon 没有任何调用图（call graph）或数据流分析（taint analysis）引擎。** 调用链追踪完全是 **子 agent 用 Claude Code 内置的 `Read` / `Grep` 工具做"人工跳转"**。

> **澄清常见误解**：这里的 `Grep` 是 **Claude Code 内置的 Grep 工具**（结构化输入输出、支持 glob 过滤），**不是**通过 `Bash` 工具执行 `grep`/`rg` 命令。白盒分析的主力是内置 `Read` + `Grep`；`Bash` 工具基本不用于代码分析，只在 `save-deliverable`、`mkdir` 等辅助操作时用。

追链时主 agent 被禁止直接 Read 源码（如 1.3 所述），全部委派给 Task 子 agent（`vuln-injection.txt:91-93`）：

> ALWAYS direct the Task Agent to trace tainted data flow, sanitization/encoding steps, and sink construction before you reach a verdict.

### 4.2 子 agent 追一条链的循环

主 agent（如 `injection-vuln`）按 `vuln-injection.txt:140-153` 的方法论，先从 `pre_recon_deliverable.md` Section 7（Injection Sources）拿到入口源列表，为每个源 spawn / 指派 Task 子 agent 追踪。子 agent 的典型循环：

```
1. 在 controller 读到用户输入 req.body.x 传进 processData(x)
2. Grep 搜 "processData" 定义 → Read 命中文件
3. 发现 processData 把 x 拼进 db.query("SELECT ... WHERE id=" + x)
4. 命中 sink（db.query）；沿途记录：
     - 每次 sanitizer 的 name + file:line
     - 每次 concat/format/join 的 file:line（特别注意 sanitize 之后的）
5. 若数据流分叉到多个 sink → 拆成多条独立 path（path forking）
6. 若 controller 有 if/else 分支 → 每个分支独立追（branch path exhaustion）
```

每一步都是一次工具调用（Read 或 Grep），所以一条复杂链可能消耗几十次工具调用。

### 4.3 两个降低追踪成本的辅助机制

因为纯 LLM 追链昂贵且易漏，Shannon 用两个 shared partial 把**可预先结构化的部分**固化，避免重复追：

| 辅助文件 | 作用 |
|---|---|
| `prompts/shared/_endpoint-security-context.txt` | recon 产出 Section 4.2：每个 endpoint 的 auth 要求、中间件链、是否框架自动生成。vuln agent 直接查表判断可达性，不必追到中间件层 |
| `prompts/shared/_cross-route-enumeration.txt` | 漏洞落盘前的强制 checklist：查 Section 4.1 共享 handler 分组，把共享同一 handler 的**所有路由**填进 `affected_routes`，避免每个路由重复追一遍 |

### 4.4 为什么强制路径分叉与分支穷举

4.2 伪代码第 5、6 行的两条强制规则（`vuln-injection.txt:147` path forking、`:148` branch exhaustion）不是赘述 —— 它们是对纯 LLM 追链"容易只追一条主线、忽略分支"这一系统性弱点的直接补偿。没有这俩规则，模型会倾向于追到第一个 sink 就停，漏掉同一参数在其他分支 / 其他 sink 的暴露。

---

## 5. 如何研判漏洞

### 5.1 核心机制：slot 类型系统 + 上下文匹配

漏洞研判**不是**"路径上有没有 sanitizer"，而是 **"sanitizer 是否匹配 sink 的 slot 上下文"**。这套规则在 `vuln-injection.txt:153-161` 定义。

**主/子分工**（呼应 1.4）：下面第 1–3 步的**事实收集**（标 slot、列 sanitizer、记 concat）由子 agent 在第 4 节追链时完成，连同 source→sink 路径一起回吐给主 agent；第 4 步的**上下文匹配**与第 5 步的 **verdict** 由主 agent 基于这些事实做出。一句话：**事实归子 agent，判定归主 agent**。

**第 1 步：标记 slot 类型**

子 agent 追到 sink 后，给 sink 的输入位打 slot 标签（`vuln-injection.txt:121, 155`）：

```
SQL-val | SQL-like | SQL-num | SQL-enum | SQL-ident
CMD-argument | CMD-part-of-string
FILE-path | FILE-include
TEMPLATE-expression
DESERIALIZE-object
PATH-component
```

**第 2 步：查路径上的 sanitizer**

逐个记录 name + file:line，按顺序排列（`vuln-injection.txt:151`）。

**第 3 步：关键失效检测 —— sanitize 后的 concat**

> If concat occurred **after** sanitization, treat that sanitization as **non-effective** for this path.（`vuln-injection.txt:165`）

这是 Shannon 研判里**最重要的反误判规则**：很多漏洞看似有 sanitize，实际在 sanitize 之后又做了字符串拼接/格式化，使 taint 重新引入。子 agent 必须把每次 concat 的 file:line 记进 `concat_occurrences`，并显式标记 sanitize 之后的那些。

**第 4 步：上下文匹配判定**（`vuln-injection.txt:156-161`）

| Sink 上下文 | 正确防御 | 判为漏洞的 mismatch |
|---|---|---|
| SQL `val`/`like`/`num` | 参数绑定 | concat、regex 转义、错误的 slot 防御 |
| SQL `enum`/`ident` | 白名单 | 误以为参数绑定能防列名/关键字（**经典漏报源**） |
| Command | 数组参数 + `shell=False`，或 `shlex.quote()` | concat、黑名单、`shell=True` |
| File / Path | 白名单路径，或 `resolve()` + 边界检查 | concat、`../` 黑名单、无协议检查 |
| SSTI | 沙箱 + autoescape，表达式内无用户输入 | concat、弱沙箱 |
| Deserialize | 仅可信来源 + 安全格式 + HMAC | 不可信输入 + pickle/unserialize |

**第 5 步：verdict 与落盘**

- `verdict: safe | vulnerable`
- 若 vulnerable，生成 `mismatch_reason`（1-2 句白话）和 `witness_payload`（最小 PoC 输入，**留给 exploit 阶段，本阶段不执行**）
- 输出结构化 JSON（`vuln-injection.txt:112-129` 的 `exploitation_queue_format`）

### 5.2 研判的额外约束

| 约束 | 来源 | 作用 |
|---|---|---|
| `externally_exploitable` | `vuln-injection.txt:167, 170` | 只有公网可达的才进 exploitation queue；需 VPN/内网访问的一律排除 |
| `affected_routes` + `authentication_required` | `_cross-route-enumeration.txt` | 每条 finding 强制带这两个字段，否则判 INCOMPLETE；`authentication_required: false` 必须有对应的 pre-auth 路由 |
| 置信度评分 | `vuln-injection.txt:185-188` | HIGH/MED/LOW，影响下游 exploit 资源分配 |
| 负结果文档化 | `vuln-injection.txt:262-266` | 安全的向量也要记录（"Vectors Analyzed and Confirmed Secure"），防止下游重复测试 |

### 5.3 白盒模式下的特殊路径

`--whitebox-only` 模式跳过 exploit 阶段。此时 `apps/worker/src/services/findings-renderer.ts` 把每个 `*_exploitation_queue.json` **确定性地**转换成 `*_findings.md`（CLAUDE.md 所述 "no LLM in the loop"），报告里相应注明"vulnerability identified through static analysis; live exploitation steps and ... are omitted"（`findings-renderer.ts:34`）。

---

## 6. 横向支撑机制

前面四节讲"单点分析怎么做"。这一节讲支撑它们的横向机制 —— 决定白盒分析作为整体怎么跑起来。

### 6.1 pre-recon 的 6-agent 编排（Phase 1/2/3）

pre-recon 内部是严格三阶段编排（`pre-recon-code.txt:109-170`），每阶段主 agent spawn 多个 Task 子 agent 并行：

| 阶段 | 并行子 agent | 产出 deliverable Section |
|---|---|---|
| Phase 1 — Discovery | Architecture Scanner · Entry Point Mapper · Security Pattern Hunter | § 2 · § 5 · § 3 |
| Phase 2 — Vuln（Phase 1 完成后） | XSS/Injection Sink Hunter · SSRF Tracer · Data Security Auditor | § 9 · § 10 · § 4 |
| Phase 3 — Synthesis | 主 agent 自行汇总 | 全文 + § 8（关键文件路径） |

**Barrier 规则**（`pre-recon-code.txt:170`）：必须等 Phase 1 全部子 agent 返回才启动 Phase 2 —— sink hunter 需要先拿到 Phase 1 的架构/入口点才能定位要扫哪些文件。这与 vuln phase 的"无 barrier"（`workflows.ts:13`）正相反。

### 6.2 deliverable Section 作为 agent 间数据契约

phase agent 之间不共享内存，只通过磁盘 deliverable 通信。每个 Section 编号是**固定契约**，上游按编号写、下游按编号读：

| deliverable | Section | 内容 | 生产者 → 消费者 |
|---|---|---|---|
| `pre_recon_deliverable.md` | § 7 | Injection Sources | pre-recon → injection-vuln |
| 同上 | § 9 | XSS Sinks | Sink Hunter → xss-vuln / report |
| 同上 | § 10 | SSRF Sinks | SSRF Tracer → ssrf-vuln |
| `recon_deliverable.md` | § 4.1 | Shared Controller Route Groups | recon → 所有 vuln agent |
| 同上 | § 4.2 | Endpoint Security Context | recon → 所有 vuln agent |

这就是 `vuln-injection.txt:140` 能直接写"读 pre-recon Section 7"、`_cross-route-enumeration.txt` 能直接写"读 recon Section 4.1"的原因 —— Section 号是 prompt 里硬编码的契约。**渐进式分析的骨架就是这张 Section 表**。

### 6.3 code_path avoid 的工具层强制（白盒能读什么）

白盒 agent 跑在 `bypassPermissions` 下，但配置里的 `code_path` avoid 规则会被**硬强制**到工具层，确定性排除路径：

1. `syncCodePathDenyRules`（`activities.ts:578`）每 workflow 调一次 → `writeUserSettingsForCodePathAvoids`（`settings-writer.ts:24`）。
2. 每个 avoid pattern 转成两条 deny：`Read(./pattern)` + `Edit(./pattern)`（`settings-writer.ts:17-22`，`FILE_TOOLS = ['Read','Edit']`），写入 `~/.claude/settings.json` 的 `permissions.deny`。
3. SDK 经 `settingSources: ['user']` 读这份 settings，**`bypassPermissions` 下照样拦**（`settings-writer.ts:7-11`）。
4. 无 avoid 规则时删除该文件（`settings-writer.ts:28-31`），避免上一轮残留污染。

**影响**：被 avoid 的路径（`node_modules`、`vendor`、生成代码等）从 sink/调用链覆盖范围里**确定性排除** —— 靠 SDK 工具层硬拦，不是 prompt 纪律。这是白盒覆盖的硬边界。

### 6.4 并行 vuln agent 的隔离

漏洞分析阶段是 **6 条**独立 pipeline 并行。注意 `CLAUDE.md` 与 `workflows.ts:13` 注释里的"5 个"已过时 —— 实际 `workflows.ts:360-396` 列了 6 个 vuln→exploit 对：injection / xss / auth / ssrf / authz / misconfig（misconfig 后加，见 `openspec/changes/archive/2026-05-26-add-misconfig-agent`）。

**编排**（`workflows.ts:475-478`）：每条 pipeline `vuln → queue check → conditional exploit`，**无 barrier** —— 某条 exploit 在自己的 vuln 完成后立即启动，不等其他 pipeline。默认并发上限 ≥ pipeline 数，全部同跑（`workflows.ts:405`）。

**隔离**：
- **Per-workflow DI container**（`container.ts`）：每 workflow 一个，服务实例化一次、跨 agent 复用。
- **AuditSession 不进 container**（`container.ts:38-40, 53-58`）：它持有实例状态 `currentAgentName`，不能跨并行 agent 共享；每个 agent 执行时单独传入自己的实例。这是并行安全的关键 —— 6 个 agent 的审计日志不会串写。

**与 pre-recon 的对比**：pre-recon 内部有 Phase 1→2 barrier（子 agent 间有顺序依赖）；vuln phase 6 条 pipeline 互相独立、无 barrier。两种编排对应不同需求：前者是"先建索引再扫漏洞"，后者是"6 类漏洞互不相干"。

---

## 7. 关键设计权衡与局限

| 维度 | 设计选择 | 代价 / 局限 |
|---|---|---|
| 引擎 | 纯 LLM + 内置工具，无 AST/调用图 | 调用链追踪非确定、可能漏追；大型仓库 token 成本高 |
| 主/子两层 | 强制 Task 子代理隔离 context | spawn 开销；主 agent 完全依赖子 agent 回吐结论的准确性 |
| sink 规则 | 硬编码在 prompt 文本 | 新增 sink 类别需改 prompt，无插件化规则库 |
| 判定依据 | slot 上下文匹配 | 判定质量随模型能力波动；复杂框架的 sanitizer 识别仍可能出错 |
| 覆盖保障 | TodoWrite 强制每个源一个 todo + 变体审计 + 分支穷举 | 靠 prompt 纪律强制，非机制保证；模型可能跳过 |
| 白盒出口 | `findings-renderer.ts` 确定性转 md | 白盒模式无实际利用验证，所有漏洞都是“潜在可达” |
| 并行隔离 | per-workflow container + per-agent AuditSession | 容器内服务须无状态；AuditSession 须逐 agent 实例化，否则并行日志串写 |
| 覆盖边界 | `code_path` avoid 经 SDK 工具层硬拦 | 被排除路径的 sink/调用链不会被发现，且是确定性、非 prompt 纪律性排除 |

---

## 附：关键文件索引

**调度与执行**
- `apps/worker/src/ai/claude-executor.ts:139` — `runClaudePrompt`，SDK `query()` 调用点
- `apps/worker/src/ai/claude-executor.ts:234-244` — 会话配置（`maxTurns`、`cwd`、`bypassPermissions`）
- `apps/worker/src/session-manager.ts:14` — `AGENTS` 注册表（phase agent 定义）
- `apps/worker/src/ai/claude-executor.ts:83` — `validateAgentOutput`，deliverable 校验

**编排与隔离**
- `apps/worker/src/services/container.ts` — per-workflow DI container；`AuditSession` 逐 agent 注入（不进容器）
- `apps/worker/src/temporal/workflows.ts:350-396` — 6 条 vuln→exploit pipeline 并行编排（无 barrier）
- `apps/worker/src/temporal/activities.ts:578` — `syncCodePathDenyRules`，每 workflow 一次

**白盒分析 prompt**
- `apps/worker/prompts/pre-recon-code.txt` — sink/入口点发现（Sections 9/10）
- `apps/worker/prompts/vuln-injection.txt` — 调用链追踪 + slot 研判
- `apps/worker/prompts/vuln-ssrf.txt` / `vuln-xss.txt` / `vuln-authz.txt` / `vuln-misconfig.txt` — 各专项研判

**Shared partials（辅助机制）**
- `apps/worker/prompts/shared/_endpoint-security-context.txt` — endpoint 可达性查表
- `apps/worker/prompts/shared/_cross-route-enumeration.txt` — 共享 handler 路由枚举
- `apps/worker/prompts/shared/_code-path-rules.txt` — focus/avoid 规则路由

**白盒出口**
- `apps/worker/src/services/findings-renderer.ts` — queue JSON → findings.md 确定性转换
- `apps/worker/src/ai/settings-writer.ts` — 把 `code_path` deny 规则写入 `~/.claude/settings.json`，SDK 在工具层强制（即便 `bypassPermissions`）
