# Whitebox Analysis Internals 文档修订设计

> **目标文件**：`docs/whitebox-analysis-internals.md`
> **立场**：如实反映现状——只改文档，不动代码/prompt
> **方法**：聚焦白盒主线 + 落差附注（方法 A）

## 背景

`docs/whitebox-analysis-internals.md` 描述 Shannon 白盒分析的内部机制，含约 40 个 `file:line` 精确引用。经全面核对（4 并行 agent + 手动验证），发现 5 处实质性问题、11 处行号偏移/事实错误、3 处值得标注的已知落差。

其中最关键的结构性偏差：文档按"五阶段含 exploit + 6 个 vuln agent"叙述，但 `whiteboxPipelineWorkflow`（`workflows.ts:647-813`）实际是 **4 阶段无 exploit + 5 个 vuln**。

---

## 改动清单

### 1. 白盒路径真实结构（最大改动）

查证 `workflows.ts:647-813` 确认：

| 事实 | 代码位置 |
|---|---|
| `exploit = false` 硬编码——白盒不跑 exploit 阶段 | `:713` |
| Phase 2 用 `promptOverride: 'recon-static'`——白盒跑 `recon-static.txt` | `:753` |
| vulnAgents 5 个（无 misconfig） | `:761-771` |
| Phase 3 注释写 "(6 agents)" 但实际 5 个 | `:756` |
| 并发用 `Promise.allSettled`（无显式上限） | `:795` |

**改法：**

| 文档位置 | 改动 |
|---|---|
| 1.1 表格 recon 行 | 标注"白盒用 `recon-static`（`workflows.ts:753 promptOverride`），pentest 用 `recon`" |
| 1.1 表格 vuln 清单 | 只列白盒实际跑的 **5 个**（injection/xss/auth/authz/ssrf），删掉 misconfig 行（在已知落差节标注） |
| 1.1 表格 `exploit-*` 行 | 注明"**pentest 专属**；白盒 `exploit=false` 硬编码（`:713`），不跑 exploit 阶段" |
| 1.1 "唯一拥有完整源码访问权" | → "唯一被分配做全量源码扫描的 phase"（所有 phase 同 cwd，区别在任务分工非访问权限） |
| 5.1 第 5 步 | "witness_payload 留给 exploit 阶段" → 注明"白盒下仅作记录、不执行（`exploit=false`）" |
| 5.3 | 明确白盒 = 4 阶段（pre-recon → recon(recon-static) → vuln(5) → report），无 exploit（`:713`）；`findings-renderer` 确定性转 md |
| 6.4 并行 vuln agent | 重写：白盒 5 条（无 barrier，`Promise.allSettled @ :795`，无显式并发上限）；pentest 6 条含 misconfig（`runWithConcurrencyLimit @ :405`） |
| 6.4 | 修正 "`:13` 的'5'已过时"论述 → "`:13` 注释'5'对白盒恰好正确（5 个 vuln），对 pentest 过时（6 个含 misconfig）" |
| 附录 | 补白盒编排索引：`workflows.ts:761-771`（vulnAgents）、`:795`（Promise.allSettled） |

### 2. Section 7 "Injection Sources" 断裂如实标注

`vuln-injection.txt:140-141` 指示 injection agent 读 pre-recon 的 "7. Injection Sources"，但 `pre-recon-code.txt:254` 的 Section 7 实际是 "Overall Codebase Indexing"——prompt 间断裂，injection agent 的注入源清单无确定上游契约。

**改法：**

| 文档位置 | 改动 |
|---|---|
| 1.5 "模式 B" | 不再说"injection-vuln 读 Section 7 的 source 列表"；改为如实说明 `vuln-injection.txt:141` 引用 "Section 7 Injection Sources"，但 pre-recon 大纲的 Section 7 实际是 "Overall Codebase Indexing"（prompt 间断裂，已知落差） |
| 4.2 | 同上 |
| 6.2 契约表 | 删 "pre_recon §7 \| Injection Sources \| pre-recon → injection-vuln"；改为 §7 = "Overall Codebase Indexing"；另注断裂 |
| 6.2 契约表 | Section 3 标题 "Security Patterns" → 标注实际标题 "Authentication & Authorization Deep Dive" |

### 3. 行号偏移与事实性错误（机械修正）

| # | 位置 | 现状 | 改为 |
|---|---|---|---|
| 1 | 0 节配置块（L15-21 代码片段） | 列 5 项 | 补 `allowDangerouslySkipPermissions: true`；注另有条件性 `thinking`(adaptive)、`env`、`outputFormat` |
| 2 | 0 节 `claude-executor.ts:139` | "调用 SDK 的 `query()`" | "`runClaudePrompt` 函数入口（`:139`）；`query()` 由 `processMessageStream` 在约 `:362` 调用" |
| 3 | 1.1 `(session-manager.ts:14-71)` | 关键 phase agent | `:14-128`（关键 agent `:15-113`） |
| 4 | 1.3 | "10 万级 turn 预算" | "约 1 万 turn 预算"；注子 agent 有独立 turn 预算 |
| 5 | 4.4 | `vuln-injection.txt:147` path forking | `:145`（`:147` 是 `@include` 指令行） |
| 6 | L51（1.1 节末） | `validateAgentOutput` "流式接收消息" | 删除"流式接收消息"——只做 deliverable 校验；流式是 `processMessageStream` 的事 |
| 7 | 6.4 附录 `workflows.ts:360-396` | 6 条 pair | pentest 6 条 `:359-400`；补白盒 5 条 `:761-771` |
| 8 | 附录 `workflows.ts:350-396` | 并行编排范围 | `:351-402`（`buildPipelineConfigs` 函数整体） |
| 9 | 附录 `container.ts:38-40, 53-58` | AuditSession | `:35-39` 与 `:54-55` |
| 10 | 附录 `findings-renderer.ts:34` | "are omitted" | DISCLAIMER 跨 `:32-36`；实际措辞 "proof of impact are not included" |
| 11 | 6.2 契约表 | Section 3 "Security Patterns" | 实际标题 "Authentication & Authorization Deep Dive" |

### 4. 新增"已知落差与待办"节

在第 7 节（关键设计权衡与局限）之后、附录之前，新增一节：

| # | 落差 | 标注内容 |
|---|---|---|
| 1 | 白盒未纳入 misconfig | `WHITEBOX_VULN_CLASSES`（`workflows.ts:645`）= 5 个，不含 misconfig；misconfig 仅在 pentest 路径。openspec proposal `2026-05-26-add-misconfig-agent` 计划纳入但未落地。 |
| 2 | Section 7 "Injection Sources" prompt 间断裂 | `vuln-injection.txt:141` 引用 "7. Injection Sources"，但 `pre-recon-code.txt:254` 的 Section 7 实际是 "Overall Codebase Indexing"。injection agent 的注入源清单无确定上游契约。 |
| 3 | 源码注释滞后 | `workflows.ts:756` 注释 "(6 agents)" 白盒实际 5 个；`:13` 注释 "5" 对白盒正确对 pentest 过时。 |

语气：中性陈述事实 + 代码引用，不推测修复方案。

---

## 不改什么

- **不动任何代码/prompt**：白盒缺 misconfig、Section 7 断裂等标注为"已知落差"
- **不展开 pentest 黑盒机制**：文档定位是白盒，pentest 仅作对比标注
- **不改文档叙述结构**：保持 0-7 节 + 附录骨架，在其内修正内容

## 验证方法

修订完成后，对改动的每个 `file:line` 引用重新核对（Read 对应文件确认行号），确保文档引用与当前代码完全一致。
