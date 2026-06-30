# Shannon Pipeline Agent 编排全景

> 适用版本：当前 `feat/fork` 分支。漏洞类别为 **5 个**（injection / xss / auth / authz / ssrf）。

Shannon 是一个**两层 agent**体系：

- **第一层 — Phase Agent**：由 Temporal workflow 在 TypeScript 层编排，每个 agent 是一次 `@anthropic-ai/claude-agent-sdk` 的 `query()` 调用（`apps/worker/src/ai/claude-executor.ts:139`）。
- **第二层 — Subagent**：第一层 agent 在运行时，通过 Claude Agent SDK **原生的 Task 工具**派生的子 agent（prompts 里叫 "Task Agent" / "Task sub-agent"）。

---

## 第一层：Phase Agent 编排（Temporal / TS 层）

Agent 注册表：`apps/worker/src/session-manager.ts`（`AGENTS` record）。全量列表 `ALL_AGENTS`：`apps/worker/src/types/agents.ts:15`。漏洞类型 `VulnType`：`injection | xss | auth | ssrf | authz`（`apps/worker/src/types/agents.ts`）。

编排逻辑全在 `apps/worker/src/temporal/workflows.ts`，共 **三种工作流变体**。

### 1. 默认完整流水线 `pentestPipelineWorkflow`

```
pre-recon → recon → [5 个 vuln→exploit 流水线并行] → attack-chains → report
```

| 阶段 | agent 数 | 派发方式 |
|---|---|---|
| 预检（preflight / auth-validation） | 1 个 `validate-authentication` | 顺序；浏览器单次真实登录存 session，**不计入 `ALL_AGENTS`** |
| **Pre-Recon** | 1 个 `pre-recon` | 顺序（`runSequentialPhase`），建架构基线 |
| **Recon** | 1 个 `recon` | 顺序，依赖 pre-recon |
| **漏洞分析 + 利用** | 最多 **5 vuln + 5 exploit** | **5 路 pipelined 并行**（见下） |
| 攻击链组装 | 0 | `buildAttackChainsActivity`，非 agent，失败非致命 |
| **报告** | 1 个 `report` | 顺序 |

**Phase 3-4 核心逻辑**（`workflows.ts:474-557`）：每种漏洞类型是一条**独立流水线**，不是"先所有 vuln 再所有 exploit"两道栅栏：

```
对每个 vuln 类别 ∈ {injection, xss, auth, ssrf, authz}:
    vuln agent  →  mergeFindingsIntoQueue  →  checkExploitationQueue
                                                    ↓ shouldExploit && exploit=true
                                               exploit agent
```

要点：

- **无同步栅栏**——某类的 exploit 在它自己的 vuln 跑完后**立即**启动，不等其它类。
- **exploit 是条件性的**——`checkExploitationQueue` 判定该类是否有可利用发现，`shouldExploit=false` 则跳过。`exploit` 开关默认 `true`（`workflows.ts:273`），可在 config 关闭。
- **并发上限**：`max_concurrent = pipelineConfig.max_concurrent_pipelines ?? ALL_VULN_CLASSES.length`（默认 5 全开）。`runWithConcurrencyLimit` 按 limit 放行；结果是**完成顺序**而非输入顺序（`workflows.ts:407`）。
- **vuln_classes 可裁剪**——config 里 `vuln_classes` 缩减后，被排除的类整条流水线跳过（`workflows.ts:544`）。

完整跑（exploit 开启且全命中）= `1 + 1 + 5 + 5 + 1` = **13 个 agent**，正好对应 `ALL_AGENTS`（`agents.ts:15-31`）。`exploit: false` 时 = **8 个**。

### 2. 白盒流水线 `whiteboxPipelineWorkflow`

```
pre-recon → recon(static) → [5 个 vuln 并行] → report     （无 exploit）
```

- Phase 1 pre-recon：1 个，顺序
- Phase 2 **recon-static**：1 个，顺序（`promptOverride: 'recon-static'`，`workflows.ts:753`）
- Phase 3：**5 个 vuln agent 并行**——`WHITEBOX_VULN_CLASSES = ['injection','xss','auth','authz','ssrf']`（`workflows.ts:645`），**无 exploit**，用 `Promise.allSettled` 全并发
- Phase 4：1 个 report

白盒总计 **8 个 agent**。本地白盒 runner 的 vuln 并发还受 `SHANNON_CONCURRENCY=<n>` / `--concurrency` 限制（其它两种变体不受此 env 限制）。

### 3. 黑盒流水线 `blackboxPipelineWorkflow`

```
auth-validation → [对每个有 queue 的类型跑 exploit] → report
```

黑盒**完全不跑 pre-recon / recon / vuln**，复用之前（完整或白盒）跑留下的 `*_exploitation_queue.json`：

- `validateDeliverablesExist` 返回哪些 vulnType 有队列文件
- 对这些类型 `Promise.allSettled` 全并发跑对应 **exploit agent**（每个仍先过 `checkExploitationQueue`）
- 1 个 report

agent 数 = `命中队列的类型数(≤5)` + 1 个 report。

### 派发的底层机制（三变体通用）

每个 phase agent 是一个 **Temporal activity**，链条：

1. **Activity wrapper**（`apps/worker/src/temporal/activities.ts`，如 `runInjectionVulnAgent`）——薄层，心跳 + 错误分类 + 容器生命周期。
2. **`AgentExecutionService`**（`apps/worker/src/services/agent-execution.ts`）——统一 agent 生命周期，由 `AGENTS` 注册表驱动，自动重试 **3 次/agent**。
3. **`claude-executor.ts`**——走 Claude Agent SDK（`maxTurns: 10_000`，`bypassPermissions`）真正执行。
4. **浏览器隔离**：每个 agent 固定分配一个 playwright session（`agent1`~`agent5`），见 `session-manager.ts:183` 的 `PLAYWRIGHT_SESSION_MAPPING`，按 `promptTemplate` 路由，保证并行不互相踩 session。
5. **resume**：已完成 agent 由 `shouldSkip()` 跳过，`computeExpectedAgents(vulnClasses, exploit)` 按本次 scope 算期望集（`workflows.ts:53`），全部完成即短路返回。

---

## 第二层：Subagent（Task 工具，prompt 驱动）

### 本质与机制

1. **subagent 不是 TypeScript 配置出来的**。`claude-executor.ts:236-247` 的 SDK options 只有 `model / maxTurns / cwd / permissionMode / settingSources / env / thinking / outputFormat / mcpServers`，没有自定义 subagent 注册，也没有 `disallowedTools` 限制 Task 工具。Task 工具是 Claude Code/SDK 原生自带，默认可用。
2. **subagent 完全由 prompt 指令驱动**，TS 代码对它无感知、不计数、不编排。Temporal 只看到第一层 activity 的完成。
3. **派发方式**：prompt 里反复出现的 "Launch ... in parallel using multiple Task tool calls **in a single message**" = 主 agent 在同一条消息里发多个 Task 调用，SDK 并行执行这些 subagent。这是 subagent 并行的唯一机制。
4. **硬性 delegate 规则**：几乎所有源码分析 agent 的 prompt 都写死 "PROHIBITED from using Read/Glob/Grep for source code analysis — 必须 delegate 给 Task Agent"。即**代码分析工作 100% 走 subagent**，主 agent 只做编排与综合。

### 逐个 agent 的 subagent 整理

#### Phase 1 — `pre-recon`（`apps/worker/prompts/pre-recon-code.txt`）⭐ 数量钉死

固定 **6 个** subagent，分两波、**波内并行、波间有栅栏**（Phase 1 全完成才进 Phase 2）：

| 波 | subagent 角色 | 职责 |
|---|---|---|
| Phase 1（3 个并行） | Architecture Scanner | 技术栈 / 架构 / 组件 |
| | Entry Point Mapper | 所有网络入口 + API schema 文件 |
| | Security Pattern Hunter | 鉴权 / 授权 / session / 安全中间件 |
| Phase 2（3 个并行） | XSS/Injection Sink Hunter | 模板 sink 枚举（强制两步 + 变体校验） |
| | SSRF/External Request Tracer | 用户输入 → 服务端请求 |
| | Data Security Auditor | 敏感数据流 / 加密 / 密钥管理 |

主 agent 综合 6 路输出后，通过 MCP collector 工具落库，自己不写 Markdown。

#### Phase 2 — `recon`（`apps/worker/prompts/recon.txt`，黑盒 / 完整）⭐ 角色固定

浏览器探索后，**6 个角色并行**（单消息多 Task 调用），每个角色对应一个 `recon-collector` MCP 工具：

| subagent | 输出归宿（Sub-agent → tool mapping） |
|---|---|
| Route Mapper | `add_endpoints` |
| Authorization Checker | `add_endpoints` / `set_network_map.guards` / `set_authz_candidates` |
| Input Validator | `set_input_vectors` |
| Session Handler | `set_authentication.session_flow` |
| Authorization Architecture | `set_role_architecture` / `set_authz_candidates` |
| Injection Source Tracer | `set_injection_sources` |

#### Phase 2 — `recon-static`（`apps/worker/prompts/recon-static.txt`，白盒版）⭐ 角色固定

白盒无 live target，**5 个角色并行**（比黑盒版少 Injection Source Tracer，injection 深挖留给下游 vuln agent）：

Route Mapper / Authorization Checker / Input Validator / Session Handler（step 2，4 并行）+ Authorization Architecture（step 2.5）。

#### Phase 3 — `vuln-*`（5 个：injection / xss / auth / authz / ssrf）⭐ 数量动态

每个 vuln agent 都是「**强制 delegate** + 数量随代码规模动态决定」：

- prompt 硬规则："NEVER use the Read tool for application source code analysis — delegate every code review to the Task Agent. MANDATORY for all source code analysis."
- 主 agent 自己只负责综合分析 + 通过 MCP collector 写 `*_exploitation_queue.json`。
- subagent 数量**不固定**——主 agent 按目标代码量自行决定，可能几个到十几个。Shannon 没有任何代码层限制。

#### Phase 4 — `exploit-*`（5 个）⭐ 数量动态，用法不同

exploit agent 的 subagent **不是用来读代码，而是用来跑自动化脚本**：

| agent | delegate 给 Task Agent 的内容 |
|---|---|
| exploit-injection | 每个 payload loop / 枚举 workflow / 自定义脚本 |
| exploit-xss | payload sweep / 浏览器交互循环 / listener 搭建 |
| exploit-auth | 多步认证自动化脚本 |
| exploit-authz | 多用户迭代 / 角色切换测试 / 工作流自动化 |
| exploit-ssrf | 内网扫描 / cloud metadata / 端口扫 |

数量动态，按需派生；主 agent 用 TodoWrite 管理任务清单。

#### `_code-path-rules.txt`（focus / avoid 规则的另一个 subagent 入口）

config 里 `rules.focus` 写的 `[FILE]` / `[GLOB]` 条目也走 Task 工具：

- `[FILE]` → 直接 delegate 到 Task 分析
- `[GLOB]` → 先 Glob 枚举匹配，再**对每个匹配 delegate 到 Task**

按 config 规则条目数派生，叠加在上述每个 agent 之上。

#### `report`

`report-executive` **没有强制 Task delegate**——主要综合已有 deliverables + 注入执行摘要，不直接分析代码，基本不派 subagent。

---

## 总账：一次完整扫描的 agent 规模

### 第一层（Temporal 编排，确定）

| 变体 | agent 数 |
|---|---|
| 默认完整（exploit 全命中） | **13**（pre-recon 1 + recon 1 + 5 vuln + 5 exploit + report 1） |
| 默认完整（`exploit: false`） | **8** |
| 白盒 `whiteboxPipelineWorkflow` | **8**（pre-recon 1 + recon 1 + 5 vuln + report 1） |
| 黑盒 `blackboxPipelineWorkflow` | `命中队列类型数(≤5)` + report 1 |

### 第二层（prompt 驱动，估算）

| 第一层 phase agent | 典型 subagent 数 |
|---|---|
| pre-recon | **固定 6**（3 + 3 两波） |
| recon | **固定 6 角色**（黑盒）/ **5 角色**（白盒 static） |
| 5 个 vuln-* | 每个 **3 ~ 10+**（动态） |
| 5 个 exploit-* | 每个 **2 ~ 8**（动态，按 exploit 复杂度） |
| report | ~0 |

一次完整扫描，第一层最多 **13 个**，第二层 subagent 至少 **12 个固定角色 + 动态几十个**，总量在 **50 ~ 150 个 subagent 量级**，完全由 prompt 驱动、TS 不感知。白盒 / 黑盒变体按各自缺省的 phase 对应缩减。

---

## 关键文件索引

| 关注点 | 文件 |
|---|---|
| Agent 注册表 / 浏览器 session 映射 / 校验器 | `apps/worker/src/session-manager.ts` |
| Agent 全量列表 / 类型定义 | `apps/worker/src/types/agents.ts` |
| 三种工作流编排 | `apps/worker/src/temporal/workflows.ts` |
| Activity 薄包装 | `apps/worker/src/temporal/activities.ts` |
| Agent 生命周期服务 | `apps/worker/src/services/agent-execution.ts` |
| SDK 执行（`query()`） | `apps/worker/src/ai/claude-executor.ts` |
| Phase prompts（含 Task Agent 指令） | `apps/worker/prompts/` |
| 代码路径 focus/avoid 规则 partial | `apps/worker/prompts/shared/_code-path-rules.txt` |
