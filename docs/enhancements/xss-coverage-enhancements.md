# XSS 覆盖率增强

| 字段 | 值 |
|---|---|
| 分支 | `feat/fork` |
| 范围 | 白盒 XSS 漏报修复（流程结构 / prompt 方法论 / 服务层基础设施 / 跨 agent 协调） |
| 相关 commit | `ffbfd29f` · `f8e730bd` · `3354bfd8` · `d77ddc6e` · `50f64731` |
| 基线对照 | `git diff main...HEAD`（已剔除 upstream `#345`/`#350` 等官方 PR） |

## TL;DR

在 OWASP Juice Shop 实测发现 Shannon **漏报反射型 XSS**，根因有三：

1. **白盒模式根本不跑 xss agent** — `WHITEBOX_VULN_CLASSES` 原本排除了 `xss`；
2. **injection agent 收敛到白名单分支**，不溯源直接读用户输入的旁路；
3. **框架盲区 + 攻击链断裂 + 前后端脱节** — `finale-rest` 自动端点未识别，多步攻击链被拆解后无法重组。

围绕这三个缺口做了一整套 **prompt + 服务层 + 协调层** 的修复，XSS 官方挑战覆盖率 8/9（89%），漏报项见 [漏报分析报告](../shannon-xss-authz-missed-vulnerabilities-report.md)。

## 改动总览

| Commit | 日期 | 维度 | 一句话 |
|---|---|---|---|
| `ffbfd29f` | 2026-06-03 | 流程结构 | 把 `xss` 纳入白盒扫描 + injection 分支穷尽规则 |
| `f8e730bd` | 2026-06-04 | prompt | vuln-xss 引入 cross-route enumeration |
| `3354bfd8` | 2026-06-05 | prompt | vuln-xss/injection 引用 recon 端点安全上下文 |
| `d77ddc6e` | 2026-06-05 | 文档 | 漏报分析报告 + 三阶段修复 spec + 实施计划 |
| `50f64731` | 2026-06-05 | 服务层/协调 | 三阶段落地：框架识别 + 前端映射 + 共享知识 + 攻击链组装 |

---

## 1. 流程结构性修复（`ffbfd29f`）

**根因**：白盒扫描从未分析 XSS。

- `apps/worker/src/temporal/workflows.ts:649` — `WHITEBOX_VULN_CLASSES` 由 `['injection','auth','authz','ssrf']` 改为 `['injection','xss','auth','authz','ssrf']`，并在 `vulnAgents` 列表注册 `{ vulnType: 'xss', agentName: 'xss-vuln', runAgent: a.runXssVulnAgent }`。
- `apps/worker/prompts/vuln-injection.txt` — 加 **Branch Path Exhaustion 规则**：对每个条件分支独立溯源，避免收敛到白名单分支而漏掉直接读用户输入的旁路（漏报反射型 XSS 的根因之一）。
- `apps/worker/prompts/vuln-xss.txt:138` — 加 server-rendered template 提示（详见 §2）。
- 设计文档：`docs/superpowers/specs/2026-06-03-whitebox-xss-coverage-fix-design.md`。

## 2. vuln-xss prompt 方法论增强（`f8e730bd` + `3354bfd8` + `50f64731` Phase 1）

### 2.1 Server-rendered template 反射 XSS（`vuln-xss.txt:138`）

提示 agent 关注模板渲染调用 `ctx.render` / `res.render` 中来自 URL 查询参数（`ctx.query.*`）的模板变量。点明 **`JSON.stringify()` 在 `<script>` 标签内不转义 `</script>`**，对 `JAVASCRIPT_STRING` 上下文不安全。即使 injection agent 已分析过同一模板的 SSTI，xss agent 仍提供独立的 render-context 分析。

### 2.2 Cross-route enumeration（`vuln-xss.txt:151`）

`@include(shared/_cross-route-enumeration.txt)` — 同一 handler 共享的所有路由都要查；`affected_routes` 必须列全。`conclusion_trigger`（`vuln-xss.txt:249`）加 **Cross-Route Verification** 校验：缺 `affected_routes` 或 `authentication_required` 的 finding 判为 INCOMPLETE；`authentication_required: false` 的 finding 必须有对应 pre-auth 路由。

### 2.3 Endpoint Security Context（`vuln-xss.txt:42`）

starting_context 引用 recon Section 4.2「Endpoint Security Context」：HTTP 方法、鉴权级别（anon/user/admin）、所有权校验、框架来源（manual / finale-rest / epilogue auto-generated）、中间件链。用来判断端点可达性与 sink 是否落在中间件保护之后。共享 partial：`apps/worker/prompts/shared/_endpoint-security-context.txt`。

## 3. 服务层基础设施 + 跨 agent 协调（`50f64731` Phase 2 / Phase 3）

让 §2 的 prompt 真正有数据可用；同时打通 vuln → exploit 的攻击链。

### 3.1 框架自动端点识别（Phase 2）

| 文件 | 职责 |
|---|---|
| `apps/worker/src/services/framework-patterns.ts` | `finale-rest` / `epilogue` 框架检测模式 |
| `apps/worker/src/services/framework-analyzer.ts` | 推断框架自动生成的 CRUD 端点（补齐手动路由表里没有的 PUT/DELETE 等） |
| `apps/worker/src/services/frontend-mapper.ts` | 前端路由 → XSS 攻击链识别（前后端脱节修复） |
| `apps/worker/src/services/route-chain-builder.ts` | 多步攻击链重组（如 Video XSS：配置修改 + 字幕注入） |

### 3.2 共享知识与攻击链组装（Phase 3）

| 文件 | 职责 |
|---|---|
| `apps/worker/src/types/shared-knowledge.ts` | agent 间共享知识类型 |
| `apps/worker/src/audit/knowledge-store.ts` | workspace 审计目录 JSON 持久化 |
| `apps/worker/src/services/attack-chain-builder.ts` | 从累积共享知识构建攻击链 |
| `apps/worker/prompts/shared/_shared-knowledge.txt` + `{{SHARED_KNOWLEDGE}}` | prompt 注入共享知识 |

流程改动：在 **vuln 阶段与 exploit 阶段之间新增攻击链组装步骤**（`activities.ts` + `workflows.ts`），framework/frontend 分析接入 pre-recon/recon activities。

## 4. 分析文档（`d77ddc6e`）

| 文档 | 行数 | 内容 |
|---|---|---|
| `docs/shannon-xss-authz-missed-vulnerabilities-report.md` | 1450 | Juice Shop XSS/越权漏报逐项分析（XSS 9 官方挑战发现 8，漏报 Video XSS 等多步链） |
| `docs/shannon-missed-vulnerabilities-fix-spec.md` | 1073 | 三阶段修复架构 spec（prompt 优化 / 服务层 / 协调层） |
| `docs/superpowers/plans/2026-06-05-missed-vulnerabilities-fix.md` | 2083 | 分阶段任务计划 |

---

## 完整文件清单

```
流程结构
  apps/worker/src/temporal/workflows.ts        WHITEBOX_VULN_CLASSES 纳入 xss

prompt（vuln-xss / vuln-injection）
  apps/worker/prompts/vuln-xss.txt             server-rendered note / cross-route / endpoint context / verification
  apps/worker/prompts/vuln-injection.txt       Branch Path Exhaustion / endpoint context
  apps/worker/prompts/shared/_cross-route-enumeration.txt
  apps/worker/prompts/shared/_endpoint-security-context.txt
  apps/worker/prompts/shared/_shared-knowledge.txt
  apps/worker/prompts/recon.txt                endpoint security context 产出

服务层 / 协调层
  apps/worker/src/services/framework-patterns.ts
  apps/worker/src/services/framework-analyzer.ts
  apps/worker/src/services/frontend-mapper.ts
  apps/worker/src/services/route-chain-builder.ts
  apps/worker/src/services/attack-chain-builder.ts
  apps/worker/src/services/prompt-manager.ts   {{SHARED_KNOWLEDGE}} 变量
  apps/worker/src/audit/knowledge-store.ts
  apps/worker/src/types/shared-knowledge.ts
  apps/worker/src/temporal/activities.ts       framework/frontend 接入 + 攻击链组装
  apps/worker/src/temporal/workflows.ts        vuln→exploit 间攻击链阶段
```

## 与 upstream 官方改动的边界

`apps/worker/prompts/vuln-xss.txt` 与 `exploit-xss.txt` 相对 `main` 是 **净删除**（+46 / −167），但其中大段删除**不属于本组增强**：

- **`#350` MCP collectors（`0a1a2eb1`）** — 把原 `<deliverable_instructions>` 手写 Markdown + `save-deliverable` CLI 整体替换为 `<mcp_tools>` 一次性调用：`set_findings_summary` / `set_strategic_intelligence` / `set_safe_vectors` / `set_blind_spots`；exploit 侧改为 `add_exploit`。
- **`#345` 共享登录会话（`81546c9a`）** — `vuln-xss.txt:24` 加 `@include(shared/_shared-session.txt)`。
- 其余 `#256` / `#273` / `#267` / `#326` 为早期官方 PR。

本文档只覆盖 `ffbfd29f` / `f8e730bd` / `3354bfd8` / `d77ddc6e` / `50f64731` 这 5 个自有 commit。

## 相关文档

- [Shannon XSS / 越权漏报分析报告](../shannon-xss-authz-missed-vulnerabilities-report.md)
- [三阶段修复 spec](../shannon-missed-vulnerabilities-fix-spec.md)
- [白盒 XSS 覆盖率修复设计](../superpowers/specs/2026-06-03-whitebox-xss-coverage-fix-design.md)
- [漏报修复实施计划](../superpowers/plans/2026-06-05-missed-vulnerabilities-fix.md)
- [Fork 扫描增强总览](../fork-scanning-enhancements.md)
