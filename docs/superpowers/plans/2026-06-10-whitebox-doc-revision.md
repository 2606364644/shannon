# Whitebox Analysis Internals 文档修订 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修订 `docs/whitebox-analysis-internals.md` 使其如实反映白盒路径的真实结构（4 阶段无 exploit、5 个 vuln、recon-static）并修正所有行号偏移与事实错误。

**Architecture:** 单文件文档修订。所有改动集中在 `docs/whitebox-analysis-internals.md`，使用 Edit 工具做内容匹配替换。按文档节序从顶到底逐段修改。每 Task 一 commit。

**Tech Stack:** Markdown, Edit tool (content-based string matching)

**Spec:** `docs/superpowers/specs/2026-06-10-whitebox-doc-revision-design.md`

---

## File Structure

**Modify:**
- `docs/whitebox-analysis-internals.md` — 唯一需要修改的文件

**Read-only reference (用于核对):**
- `apps/worker/src/temporal/workflows.ts`
- `apps/worker/src/session-manager.ts`
- `apps/worker/src/ai/claude-executor.ts`
- `apps/worker/prompts/pre-recon-code.txt`

---

### Task 1: Section 0 — Config block & entry point

**Files:**
- Modify: `docs/whitebox-analysis-internals.md` (Lines 13-21)

- [ ] **Step 1: Fix claude-executor.ts:139 entry point description**

Edit old_string:
```
核心入口在 `apps/worker/src/ai/claude-executor.ts:139` 的 `runClaudePrompt`，它调用 `@anthropic-ai/claude-agent-sdk` 的 `query()`，把 prompt 连同 `cwd: sourceDir`（目标仓库根目录）丢给一个 Claude Code 会话。会话配置见 `claude-executor.ts:234-244`：
```

Replace with:
```
核心入口在 `apps/worker/src/ai/claude-executor.ts:139` 的 `runClaudePrompt`——它准备 prompt 与配置，委托给 `processMessageStream`（`:362`）调用 `@anthropic-ai/claude-agent-sdk` 的 `query()`，在 `cwd: sourceDir`（目标仓库根目录）下启动一个 Claude Code 会话。会话配置见 `claude-executor.ts:234-244`：
```

- [ ] **Step 2: Supplement config code block**

Edit old_string:
```ts
model,                          // 由 modelTier 解析（pre-recon 用 large）
maxTurns: 10_000,               // 单个 phase 会话最多 1 万轮工具调用
cwd: sourceDir,                 // 直接在目标仓库根目录运行
permissionMode: 'bypassPermissions',
settingSources: ['user'],       // 继承用户级 MCP/设置
```

Replace with:
```ts
model,                          // 由 modelTier 解析（pre-recon 用 large）
maxTurns: 10_000,               // 单个 phase 会话最多 1 万轮工具调用
cwd: sourceDir,                 // 直接在目标仓库根目录运行
permissionMode: 'bypassPermissions',
allowDangerouslySkipPermissions: true,  // 配合 bypassPermissions
settingSources: ['user'],       // 继承用户级 MCP/设置
// 另有条件性字段：thinking({type:'adaptive'})、env、outputFormat
```

- [ ] **Step 3: Verify and commit**

Read lines 9-25 to verify changes look correct.

```bash
git add docs/whitebox-analysis-internals.md
git commit -m "docs(whitebox-internals): fix section 0 config block and entry point"
```

---

### Task 2: Section 1.1 — Phase agent table & definitions

**Files:**
- Modify: `docs/whitebox-analysis-internals.md` (Lines 42-53)

- [ ] **Step 1: Fix session-manager.ts line range**

Edit old_string:
```
关键的几个 phase agent（`session-manager.ts:14-71`）：
```

Replace with:
```
关键的几个 phase agent（`session-manager.ts:14-128`，agent 定义 `:15-113`）：
```

- [ ] **Step 2: Rewrite phase agent table**

Edit old_string:
```
| Phase agent | promptTemplate | modelTier | 角色 |
|---|---|---|---|
| `pre-recon` | `pre-recon-code` | `large` | **唯一拥有完整源码访问权的 agent**，建立架构基线 + sink/入口点清单 |
| `recon` | `recon` | 默认 | 攻击面映射，产出 `recon_deliverable.md`（含 Section 4.1/4.2 共享 handler、中间件链） |
| `injection-vuln` / `xss-vuln` / `auth-vuln` / `ssrf-vuln` / `authz-vuln` / `misconfig-vuln` | `vuln-*` | 默认 | 6 个漏洞分析专项，`prerequisites: ['recon']`，**并行执行** |
| `injection-exploit` 等 | `exploit-*` | 默认 | 漏洞利用验证，`prerequisites` 对应 vuln |
```

Replace with:
```
| Phase agent | promptTemplate | modelTier | 角色 |
|---|---|---|---|
| `pre-recon` | `pre-recon-code` | `large` | **唯一被分配做全量源码扫描的 phase**，建立架构基线 + sink/入口点清单 |
| `recon` | `recon-static`（白盒，`workflows.ts:753 promptOverride`）；pentest 用 `recon` | 默认 | 攻击面映射，产出 `recon_deliverable.md`（含 Section 4.1/4.2 共享 handler、中间件链） |
| `injection-vuln` / `xss-vuln` / `auth-vuln` / `ssrf-vuln` / `authz-vuln` | `vuln-*` | 默认 | 白盒 **5 个**漏洞分析专项，`prerequisites: ['recon']`，**并行执行**（`workflows.ts:761-771`） |
| `injection-exploit` 等 | `exploit-*` | 默认 | **pentest 专属**；白盒 `exploit=false` 硬编码（`workflows.ts:713`），不跑 exploit 阶段 |
```

- [ ] **Step 3: Fix validateAgentOutput description**

Edit old_string:
```
**每个 phase agent 在运行期 = 一个独立的 Claude Code 会话**（一次 `query()` 调用）。Temporal activity 调用 `runClaudePrompt`，后者启动会话、流式接收消息、最后校验 deliverable 是否生成（`claude-executor.ts:83-135` 的 `validateAgentOutput`）。
```

Replace with:
```
**每个 phase agent 在运行期 = 一个独立的 Claude Code 会话**（一次 `query()` 调用）。Temporal activity 调用 `runClaudePrompt`，后者委托 `processMessageStream`（`:345-414`）流式接收消息、会话结束后由 `validateAgentOutput`（`claude-executor.ts:83-135`）校验 deliverable 是否生成。
```

- [ ] **Step 4: Verify and commit**

Read lines 42-53 to verify changes.

```bash
git add docs/whitebox-analysis-internals.md
git commit -m "docs(whitebox-internals): fix section 1.1 phase agent table and descriptions"
```

---

### Task 3: Sections 1.3 + 1.5 — maxTurns & 模式B

**Files:**
- Modify: `docs/whitebox-analysis-internals.md` (Lines 100, 131-135)

- [ ] **Step 1: Fix maxTurns "10万" → "约1万"**

Edit old_string:
```
**这正是 `maxTurns: 10_000` 存在的根因**：追一条调用链可能要几十次 Read/Grep，覆盖一个仓库要 spawn 几十上百个子 agent，每个子 agent 自己也有大量工具调用。10 万级 turn 预算对应的是这种"主 agent 持续派活、子 agent 持续读码"的长时间 agentic 循环。
```

Replace with:
```
**这正是 `maxTurns: 10_000` 存在的根因**：追一条调用链可能要几十次 Read/Grep，覆盖一个仓库要 spawn 几十上百个子 agent，每个子 agent 自己也有大量工具调用。约 1 万 turn 预算对应的是这种"主 agent 持续派活、子 agent 持续读码"的长时间 agentic 循环（注：此预算为主会话上限，Task 子 agent 有独立 turn 预算，不计入此数）。
```

- [ ] **Step 2: Fix 模式B Section 7 断裂描述**

Edit old_string:
```
派发单元 = 每个 source / 每条路径，数量由前置 deliverable 决定。以 `injection-vuln` 为例（`vuln-injection.txt:140-141`）：

> Create a To Do for each Injection Source ... section 7 ... create a task for each discovered Injection Source.

主 agent 读 `pre_recon_deliverable.md` Section 7 的 source 列表 → TodoWrite 建清单（每 source 一项）→ 逐 source spawn Task 追链。source 有几个派几条，path forking 时一条链还可能再拆（`vuln-injection.txt:145`）。
```

Replace with:
```
派发单元 = 每个 source / 每条路径，数量由前置 deliverable 决定。以 `injection-vuln` 为例（`vuln-injection.txt:140-141`）：

> Create a To Do for each Injection Source ... section 7 ... create a task for each discovered Injection Source.

`vuln-injection.txt:141` 指示主 agent 从 `pre_recon_deliverable.md` 的 "Section 7. Injection Sources" 读取 source 列表。**⚠️ 已知落差**：`pre-recon-code.txt` 的 deliverable 大纲里 Section 7 实际是 "Overall Codebase Indexing"（`:254`），并无 "Injection Sources"——这是 prompt 间的 Section 契约断裂（详见"已知落差"节）。实际运行时 injection agent 的注入源清单无确定上游契约。source 有几个派几条，path forking 时一条链还可能再拆（`vuln-injection.txt:145`）。
```

- [ ] **Step 3: Verify and commit**

Read lines 86-145 to verify changes.

```bash
git add docs/whitebox-analysis-internals.md
git commit -m "docs(whitebox-internals): fix maxTurns number and section 7 gap in section 1"
```

---

### Task 4: Section 4 — 调用链修正

**Files:**
- Modify: `docs/whitebox-analysis-internals.md` (Lines 262, 288)

- [ ] **Step 1: Fix 4.2 Section 7 reference**

Edit old_string:
```
主 agent（如 `injection-vuln`）按 `vuln-injection.txt:140-153` 的方法论，先从 `pre_recon_deliverable.md` Section 7（Injection Sources）拿到入口源列表，为每个源 spawn / 指派 Task 子 agent 追踪。子 agent 的典型循环：
```

Replace with:
```
主 agent（如 `injection-vuln`）按 `vuln-injection.txt:140-153` 的方法论，从 `pre_recon_deliverable.md` 获取注入源清单（**⚠️ prompt 间断裂**：`vuln-injection.txt:141` 引用 "Section 7. Injection Sources"，但 pre-recon 大纲的 Section 7 实际是 "Overall Codebase Indexing"；详见"已知落差"节），为每个源 spawn / 指派 Task 子 agent 追踪。子 agent 的典型循环：
```

- [ ] **Step 2: Fix 4.4 path forking line reference :147→:145**

Edit old_string:
```
4.2 伪代码第 5、6 行的两条强制规则（`vuln-injection.txt:147` path forking、`:148` branch exhaustion）不是赘述 —— 它们是对纯 LLM 追链"容易只追一条主线、忽略分支"这一系统性弱点的直接补偿。没有这俩规则，模型会倾向于追到第一个 sink 就停，漏掉同一参数在其他分支 / 其他 sink 的暴露。
```

Replace with:
```
4.2 伪代码第 5、6 行的两条强制规则（`vuln-injection.txt:145` path forking、`:148` branch exhaustion）不是赘述 —— 它们是对纯 LLM 追链"容易只追一条主线、忽略分支"这一系统性弱点的直接补偿。没有这俩规则，模型会倾向于追到第一个 sink 就停，漏掉同一参数在其他分支 / 其他 sink 的暴露。
```

- [ ] **Step 3: Verify and commit**

Read lines 260-290 to verify changes.

```bash
git add docs/whitebox-analysis-internals.md
git commit -m "docs(whitebox-internals): fix section 4 call chain references"
```

---

### Task 5: Section 5 — 研判 & 白盒出口

**Files:**
- Modify: `docs/whitebox-analysis-internals.md` (Lines 337, 349-352)

- [ ] **Step 1: Fix witness_payload description**

Edit old_string:
```
- 若 vulnerable，生成 `mismatch_reason`（1-2 句白话）和 `witness_payload`（最小 PoC 输入，**留给 exploit 阶段，本阶段不执行**）
```

Replace with:
```
- 若 vulnerable，生成 `mismatch_reason`（1-2 句白话）和 `witness_payload`（最小 PoC 输入，**留给 exploit 阶段，本阶段不执行**；白盒 `exploit=false`，仅作记录）
```

- [ ] **Step 2: Rewrite 5.3 — whitebox 4-phase path**

Edit old_string:
```
`--whitebox-only` 模式跳过 exploit 阶段。此时 `apps/worker/src/services/findings-renderer.ts` 把每个 `*_exploitation_queue.json` **确定性地**转换成 `*_findings.md`（CLAUDE.md 所述 "no LLM in the loop"），报告里相应注明"vulnerability identified through static analysis; live exploitation steps and ... are omitted"（`findings-renderer.ts:34`）。
```

Replace with:
```
白盒路径是 **4 阶段**（pre-recon → recon(recon-static) → vuln(5 agent) → report），`exploit=false` 硬编码（`workflows.ts:713`），**根本不跑 exploit 阶段**。`apps/worker/src/services/findings-renderer.ts` 把每个 `*_exploitation_queue.json` **确定性地**转换成 `*_findings.md`（CLAUDE.md 所述 "no LLM in the loop"），报告里相应注明 "vulnerability identified through static analysis; live exploitation steps and proof of impact are not included"（DISCLAIMER 常量，`findings-renderer.ts:32-36`）。
```

- [ ] **Step 3: Verify and commit**

Read lines 334-355 to verify changes.

```bash
git add docs/whitebox-analysis-internals.md
git commit -m "docs(whitebox-internals): fix section 5 verdict and whitebox export"
```

---

### Task 6: Section 6 — 契约表 & 并行 vuln 重写

**Files:**
- Modify: `docs/whitebox-analysis-internals.md` (Lines 373-406)

- [ ] **Step 1: Fix 6.2 deliverable 契约表**

Edit old_string:
```
| deliverable | Section | 内容 | 生产者 → 消费者 |
|---|---|---|---|
| `pre_recon_deliverable.md` | § 7 | Injection Sources | pre-recon → injection-vuln |
| 同上 | § 9 | XSS Sinks | Sink Hunter → xss-vuln / report |
| 同上 | § 10 | SSRF Sinks | SSRF Tracer → ssrf-vuln |
| `recon_deliverable.md` | § 4.1 | Shared Controller Route Groups | recon → 所有 vuln agent |
| 同上 | § 4.2 | Endpoint Security Context | recon → 所有 vuln agent |

这就是 `vuln-injection.txt:140` 能直接写"读 pre-recon Section 7"、`_cross-route-enumeration.txt` 能直接写"读 recon Section 4.1"的原因 —— Section 号是 prompt 里硬编码的契约。**渐进式分析的骨架就是这张 Section 表**。
```

Replace with:
```
| deliverable | Section | 内容 | 生产者 → 消费者 |
|---|---|---|---|
| `pre_recon_deliverable.md` | § 2 | Architecture & Technology Stack | pre-recon → 所有下游 |
| 同上 | § 5 | Attack Surface Analysis | pre-recon → recon |
| 同上 | § 7 | Overall Codebase Indexing | pre-recon → 所有下游 |
| 同上 | § 9 | XSS Sinks and Render Contexts | Sink Hunter → xss-vuln / report |
| 同上 | § 10 | SSRF Sinks | SSRF Tracer → ssrf-vuln |
| `recon_deliverable.md` | § 4.1 | Shared Controller Route Groups | recon → 所有 vuln agent |
| 同上 | § 4.2 | Endpoint Security Context | recon → 所有 vuln agent |

**⚠️ 已知落差**：`vuln-injection.txt:141` 引用 "Section 7. Injection Sources"，但上表 § 7 实际是 "Overall Codebase Indexing"（`pre-recon-code.txt:254`），并无 "Injection Sources"——prompt 间 Section 契约断裂，injection agent 的注入源清单无确定上游契约（详见"已知落差"节）。此外，§ 3 实际标题是 "Authentication & Authorization Deep Dive"（文档其他处简称为 "Security Patterns"）。

这就是 `_cross-route-enumeration.txt` 能直接写"读 recon Section 4.1"的原因 —— Section 号是 prompt 里硬编码的契约。**渐进式分析的骨架就是这张 Section 表**。
```

- [ ] **Step 2: Rewrite 6.4 — 并行 vuln agent**

Edit old_string:
```
### 6.4 并行 vuln agent 的隔离

漏洞分析阶段是 **6 条**独立 pipeline 并行。注意 `CLAUDE.md` 与 `workflows.ts:13` 注释里的"5 个"已过时 —— 实际 `workflows.ts:360-396` 列了 6 个 vuln→exploit 对：injection / xss / auth / ssrf / authz / misconfig（misconfig 后加，见 `openspec/changes/archive/2026-05-26-add-misconfig-agent`）。

**编排**（`workflows.ts:475-478`）：每条 pipeline `vuln → queue check → conditional exploit`，**无 barrier** —— 某条 exploit 在自己的 vuln 完成后立即启动，不等其他 pipeline。默认并发上限 ≥ pipeline 数，全部同跑（`workflows.ts:405`）。

**隔离**：
- **Per-workflow DI container**（`container.ts`）：每 workflow 一个，服务实例化一次、跨 agent 复用。
- **AuditSession 不进 container**（`container.ts:38-40, 53-58`）：它持有实例状态 `currentAgentName`，不能跨并行 agent 共享；每个 agent 执行时单独传入自己的实例。这是并行安全的关键 —— 6 个 agent 的审计日志不会串写。

**与 pre-recon 的对比**：pre-recon 内部有 Phase 1→2 barrier（子 agent 间有顺序依赖）；vuln phase 6 条 pipeline 互相独立、无 barrier。两种编排对应不同需求：前者是"先建索引再扫漏洞"，后者是"6 类漏洞互不相干"。
```

Replace with:
```
### 6.4 并行 vuln agent 的隔离

白盒路径的漏洞分析阶段是 **5 条**独立 pipeline 并行（`WHITEBOX_VULN_CLASSES` = injection / xss / auth / authz / ssrf，`workflows.ts:645, 761-771`）。pentest 路径有 **6 条**（含 misconfig，`buildPipelineConfigs` `workflows.ts:351-402`）。注意 `workflows.ts:13` 注释里的"5 个"对白盒恰好正确、对 pentest 已过时（pentest 已是 6 个含 misconfig）。

**白盒编排**（`workflows.ts:795`）：`Promise.allSettled`，**无 barrier、无显式并发上限** —— 5 条全部同跑。每条 pipeline `vuln → queue check`（无 exploit，`exploit=false @ :713`）。pentest 编排不同（`workflows.ts:475-535`）：每条 `vuln → queue check → conditional exploit`，使用 `runWithConcurrencyLimit`（`:405`）。

**隔离**（白盒与 pentest 共享）：
- **Per-workflow DI container**（`container.ts`）：每 workflow 一个，服务实例化一次、跨 agent 复用。
- **AuditSession 不进 container**（`container.ts:35-39, 54-55`）：它持有实例状态 `currentAgentName`，不能跨并行 agent 共享；每个 agent 执行时单独传入自己的实例。这是并行安全的关键 —— 并行 agent 的审计日志不会串写。

**与 pre-recon 的对比**：pre-recon 内部有 Phase 1→2 barrier（子 agent 间有顺序依赖）；vuln phase 的 pipeline 互相独立、无 barrier。两种编排对应不同需求：前者是"先建索引再扫漏洞"，后者是"各漏洞类互不相干"。
```

- [ ] **Step 3: Verify and commit**

Read lines 370-410 to verify changes.

```bash
git add docs/whitebox-analysis-internals.md
git commit -m "docs(whitebox-internals): rewrite section 6 contract table and vuln pipeline"
```

---

### Task 7: New section + Appendix — 已知落差 & 索引修正

**Files:**
- Modify: `docs/whitebox-analysis-internals.md` (after Line 422, Lines 425-451)

- [ ] **Step 1: Insert 已知落差 section**

Edit old_string:
```
---

## 附：关键文件索引
```

Insert before it:
```
## 7.5. 已知落差与待办

以下落差在文档修订时如实记录。均为当前代码/prompt 的现状描述，不代表文档修订立场。

| # | 落差 | 详情 |
|---|---|---|
| 1 | **白盒未纳入 misconfig** | `WHITEBOX_VULN_CLASSES`（`workflows.ts:645`）= 5 个（injection/xss/auth/authz/ssrf），不含 misconfig。misconfig 仅在 pentest 路径（`buildPipelineConfigs` `:351-402`）。openspec proposal `2026-05-26-add-misconfig-agent` 计划纳入白盒但未落地。 |
| 2 | **Section 7 "Injection Sources" prompt 间断裂** | `vuln-injection.txt:141` 指示 injection agent 读 pre-recon 的 "Section 7. Injection Sources (Command Injection and SQL Injection)"，但 `pre-recon-code.txt:254` 的 deliverable 大纲里 Section 7 实际是 "Overall Codebase Indexing"。injection agent 的注入源清单无确定上游契约。 |
| 3 | **源码注释滞后** | `workflows.ts:756` 注释写 "(6 agents)"，白盒实际 5 个；`:13` 注释写 "5"，对白盒正确但对 pentest 过时。 |

---

## 附：关键文件索引
```

- [ ] **Step 2: Fix appendix — 调度与执行 section**

Edit old_string:
```
**调度与执行**
- `apps/worker/src/ai/claude-executor.ts:139` — `runClaudePrompt`，SDK `query()` 调用点
- `apps/worker/src/ai/claude-executor.ts:234-244` — 会话配置（`maxTurns`、`cwd`、`bypassPermissions`）
- `apps/worker/src/session-manager.ts:14` — `AGENTS` 注册表（phase agent 定义）
- `apps/worker/src/ai/claude-executor.ts:83` — `validateAgentOutput`，deliverable 校验
```

Replace with:
```
**调度与执行**
- `apps/worker/src/ai/claude-executor.ts:139` — `runClaudePrompt` 函数入口；`query()` 由 `processMessageStream` 在约 `:362` 调用
- `apps/worker/src/ai/claude-executor.ts:234-244` — 会话配置（`maxTurns`、`cwd`、`bypassPermissions`、`allowDangerouslySkipPermissions`）
- `apps/worker/src/session-manager.ts:14` — `AGENTS` 注册表（phase agent 定义，agent 定义至 `:128`）
- `apps/worker/src/ai/claude-executor.ts:83` — `validateAgentOutput`，deliverable 校验
```

- [ ] **Step 3: Fix appendix — 编排与隔离 section**

Edit old_string:
```
**编排与隔离**
- `apps/worker/src/services/container.ts` — per-workflow DI container；`AuditSession` 逐 agent 注入（不进容器）
- `apps/worker/src/temporal/workflows.ts:350-396` — 6 条 vuln→exploit pipeline 并行编排（无 barrier）
- `apps/worker/src/temporal/activities.ts:578` — `syncCodePathDenyRules`，每 workflow 一次
```

Replace with:
```
**编排与隔离**
- `apps/worker/src/services/container.ts` — per-workflow DI container；`AuditSession` 逐 agent 注入（不进容器，`NOTE @ :35-39, :54-55`）
- `apps/worker/src/temporal/workflows.ts:645` — `WHITEBOX_VULN_CLASSES`（白盒 5 个 vuln）
- `apps/worker/src/temporal/workflows.ts:761-771` — 白盒 vulnAgents 定义（5 条，`Promise.allSettled @ :795`）
- `apps/worker/src/temporal/workflows.ts:351-402` — pentest `buildPipelineConfigs`（6 条含 misconfig，`runWithConcurrencyLimit @ :405`）
- `apps/worker/src/temporal/activities.ts:578` — `syncCodePathDenyRules`，每 workflow 一次
```

- [ ] **Step 4: Fix appendix — 白盒出口 section**

Edit old_string:
```
**白盒出口**
- `apps/worker/src/services/findings-renderer.ts` — queue JSON → findings.md 确定性转换
- `apps/worker/src/ai/settings-writer.ts` — 把 `code_path` deny 规则写入 `~/.claude/settings.json`，SDK 在工具层强制（即便 `bypassPermissions`）
```

Replace with:
```
**白盒出口**
- `apps/worker/src/services/findings-renderer.ts:32-36` — DISCLAIMER 常量；queue JSON → findings.md 确定性转换（措辞 "proof of impact are not included"）
- `apps/worker/src/ai/settings-writer.ts` — 把 `code_path` deny 规则写入 `~/.claude/settings.json`，SDK 在工具层强制（即便 `bypassPermissions`）
```

- [ ] **Step 5: Verify and commit**

Read the new section and appendix to verify.

```bash
git add docs/whitebox-analysis-internals.md
git commit -m "docs(whitebox-internals): add known gaps section and fix appendix references"
```

---

### Task 8: Final verification — 全文核对

**Files:**
- Read-only: `docs/whitebox-analysis-internals.md`

- [ ] **Step 1: Re-read entire document**

Read the full document (should be ~470 lines now with the new section). Verify:
1. All `file:line` references in the document match current code (cross-check against the files listed below)
2. No remaining "6 个 vuln" or "Section 7 Injection Sources" references (except in the "已知落差" section)
3. "exploit 阶段" references correctly qualified as "pentest 专属"
4. New section renders correctly in markdown

**Cross-check targets** (read these files to verify line numbers still match):
- `apps/worker/src/ai/claude-executor.ts` — verify `:139`, `:234-244`, `:83`, `:362`, `:345-414`
- `apps/worker/src/session-manager.ts` — verify `:14`, `:15-113`, `:128`
- `apps/worker/src/temporal/workflows.ts` — verify `:645`, `:713`, `:753`, `:761-771`, `:795`, `:351-402`, `:405`, `:578`
- `apps/worker/src/services/container.ts` — verify `:35-39`, `:54-55`
- `apps/worker/src/services/findings-renderer.ts` — verify `:32-36`
- `apps/worker/prompts/pre-recon-code.txt` — verify `:254` (Section 7)

- [ ] **Step 2: Fix any discrepancies found**

If any line number drifted, use Edit to correct.

```bash
git add docs/whitebox-analysis-internals.md
git commit -m "docs(whitebox-internals): final verification fixes"  # only if changes needed
```
