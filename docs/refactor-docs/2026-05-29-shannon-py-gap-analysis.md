# Shannon-Py 重构差距分析报告

> 对比原始 TypeScript Shannon (`/root/shannon`) 与 Python 重构版 (`/root/shannon-refactor/shannon-py`) 在白盒/黑盒扫描功能及安全效果上的差距

**日期**: 2026-05-29
**范围**: Agent 定义、Queue Schema、Prompt 模板、Config 模型、Workflow 编排、安全机制、SDK 集成、扩展性

---

## 目录

1. [总览矩阵](#1-总览矩阵)
2. [致命差距 — 系统不可运行](#2-致命差距--系统不可运行)
3. [高危差距 — 安全扫描能力缺失](#3-高危差距--安全扫描能力缺失)
4. [中危差距 — 工作流可靠性](#4-中危差距--工作流可靠性)
5. [低危差距 — 配置/验证/扩展性](#5-低危差距--配置验证扩展性)
6. [Python 新增功能](#6-python-新增功能typescript-无对应)
7. [逐组件详细对比](#7-逐组件详细对比)
8. [修复优先级建议](#8-修复优先级建议)

---

## 1. 总览矩阵

| 维度 | 完成度 | 说明 |
|------|--------|------|
| Agent 注册表 | 87.5% | 14/16 agents，缺 `misconfig-vuln` 和 `misconfig-exploit` |
| Queue Schemas | 83% | 5/6 类型完整，缺 `MisconfigVulnerability` |
| Prompt 模板 | ~41% | 7/17 完全一致，7/17 大幅简化（骨架化），3/17 完全缺失 |
| Config 模型 | ~70% | 缺 `misconfig`、`EmailLogin`、`ProviderConfig`、`ContainerConfig` |
| Workflow 编排 | ~40% | 缺 `pentestPipeline`、heartbeat、non-retryable 错误、功能性 resume |
| 安全机制 | ~5% | Preflight 仅检查 repo 存在性；无 SSRF/DNS/代码路径防护 |
| SDK 集成 | 0% | `NotImplementedError` stub，整个流水线无法运行 |
| 扩展性 | 0% | 无 DI Container、无 Provider 接口 |

---

## 2. 致命差距 — 系统不可运行

### 2.1 Claude Agent SDK 集成 — 仅占位符

| 项 | TypeScript | Python |
|----|-----------|--------|
| 文件 | `apps/worker/src/ai/claude-executor.ts` (402行) | `packages/whitebox/src/shannon_whitebox/agents/runner.py` (28行) |
| 状态 | 完整生产实现 | `NotImplementedError("Claude Agent SDK Python integration pending")` |
| 提供商支持 | API Key / OAuth / Bedrock / Vertex AI / LiteLLM (5种) | 接受 `provider_config: dict` 但未使用 |
| 消息流 | 完整异步流 + 逐消息分发 | 未实现 |
| 结构化输出 | `JsonSchemaOutputFormat` 传给 SDK | `output_format: dict` 参数接受但未使用 |
| 成本跟踪 | 从 result messages 累加 | 未实现 |
| 模型分级 | `resolveModel()` 含 adaptive thinking 支持 | `model_tier` 参数接受但未使用 |
| 错误处理 | 可重试错误分类 + 错误日志 + 审计日志 | 未实现 |
| Spending cap | `isSpendingCapBehavior()` 集成到消息流 | 不适用（无流可检测） |

**影响**: 整个扫描流水线无法执行任何实际任务。`AgentExecutor.execute()` 第5步调用 `run_claude_prompt()` 必定失败。

### 2.2 Exploit Prompt 从专业方法论降级为骨架

| Prompt | TypeScript 行数 | Python 行数 | 缩减比 |
|--------|-----------------|-------------|--------|
| injection-exploit | 451 | 19 | 96% |
| xss-exploit | 442 | 19 | 96% |
| auth-exploit | 423 | 19 | 96% |
| authz-exploit | 425 | 19 | 96% |
| ssrf-exploit | 502 | 19 | 96% |

**TypeScript 版本内容**（以 injection-exploit 为例）:
- OWASP 3阶段 exploitation workflow
- Proof levels 定义（conclusive / probable / inconclusive）
- Bypass exhaustion protocol
- 证据检查清单
- WAF 规避指导
- 分类框架（EXPLOITED / BLOCKED_BY_SECURITY / OUT_OF_SCOPE / FALSE_POSITIVE）

**Python 版本内容**:
- 19行通用指令 + `{{VULNERABILITY_ENTRIES}}` 占位符
- 无方法论、无分类框架、无证据要求

**影响**: 即使 SDK 集成完成，exploit 阶段效果也会大幅下降。exploit agent 不知道应该如何分类结果、如何构建 PoC、如何规避 WAF。

### 2.3 Report Prompt 降级

| 项 | TypeScript | Python |
|----|-----------|--------|
| 行数 | 113 | 22 |
| 内容 | 结构化报告修改流程，含动态模板变量 | 通用"添加执行摘要"指令 |
| 动态变量 | `{{REPORT_FILTERS_BLOCK}}`, `{{REPORT_FILTER_RULES}}`, `{{VULN_SUMMARY_SUBSECTIONS}}` | 无 |
| 过滤规则 | 按 severity/confidence 过滤漏洞 | 未涉及 |

**影响**: 最终输出报告质量显著降低。

---

## 3. 高危差距 — 安全扫描能力缺失

### 3.1 整个 misconfig 漏洞类被删除

TypeScript 支持 6 个漏洞类，Python 仅 5 个：

| 漏洞类 | TypeScript | Python |
|--------|-----------|--------|
| injection | ✅ | ✅ |
| xss | ✅ | ✅ |
| auth | ✅ | ✅ |
| ssrf | ✅ | ✅ |
| authz | ✅ | ✅ |
| **misconfig** | ✅ | **❌ 完全缺失** |

**缺失组件清单**:
- Agent: `misconfig-vuln`, `misconfig-exploit`
- Prompt: `vuln-misconfig.txt` (285行), `exploit-misconfig.txt` (369行)
- Queue Schema: `MisconfigVulnerability` (8个字段)
- Config: `VulnClass` 中无 `"misconfig"`，`ALL_VULN_CLASSES` 仅5项
- Report agent prerequisites: TS依赖6个exploit agent，PY依赖5个

**无法检测的安全问题**:
- Open Redirect
- 缺失安全头（X-Frame-Options, CSP, HSTS 等）
- CORS 配置错误
- Cookie 安全标志缺失（Secure, HttpOnly, SameSite）
- Clickjacking 漏洞
- 信息泄露

### 3.2 认证预校验完全缺失

| 项 | TypeScript | Python |
|----|-----------|--------|
| 文件 | `services/validate-authentication.ts` (194行) | 不存在 |
| 方法 | 驱动真实浏览器 via playwright-cli + Claude Agent SDK | N/A |
| 结构化输出 | Zod schema: `login_success`, `failure_point`, `failure_detail` | N/A |
| 失败分类 | `username_or_password` / `totp_secret` / `out_of_band` | N/A |
| Prompt | `validate-authentication.txt` (25行) | 不存在 |

**影响**: pentest 可能以无效凭据运行数小时，在 exploit 阶段才发现登录失败。TS 版本在 preflight 阶段就捕获此问题。

### 3.3 白盒 Recon 不使用静态分析 Prompt

| 项 | TypeScript | Python |
|----|-----------|--------|
| Prompt override | `promptOverride: 'recon-static'` (380行纯静态分析) | 无 override |
| Recon prompt | 使用 `recon-static.txt` | 使用 `recon.txt`（动态分析版本） |

**影响**: 白盒扫描本应仅做静态分析（无目标URL），但 Python 版本可能尝试运行动态分析（浏览器探测），导致 recon 阶段行为不正确或失败。

### 3.4 Playwright 反检测与会话隔离缺失

**反检测配置**:

| 项 | TypeScript | Python |
|----|-----------|--------|
| 文件 | `ai/playwright-config-writer.ts` (90行) | 不存在 |
| 反检测特性 | 删除 `navigator.webdriver`、伪装插件数组、`chrome.runtime` mock、UA 欺骗、`--disable-blink-features=AutomationControlled` | N/A |
| 配置位置 | `<sourceDir>/.playwright/cli.config.json` + `scripts/stealth.js` | N/A |

**会话隔离**:

| 项 | TypeScript | Python |
|----|-----------|--------|
| 会话映射 | `PLAYWRIGHT_SESSION_MAPPING` — 6个独立浏览器会话 | 不存在 |
| 默认行为 | 按 prompt template 分配 `agent1`-`agent6` | 所有 agent 默认共享 `agent1` |

**影响**:
1. 黑盒 exploit 时浏览器自动化指纹可被 WAF/IDS 检测
2. 6个并发 agent 共享同一浏览器会话，互相干扰状态（cookies、localStorage、session）

### 3.5 代码路径访问控制缺失

| 项 | TypeScript | Python |
|----|-----------|--------|
| 文件 | `ai/settings-writer.ts` (41行) | 不存在 |
| 机制 | 写入 `~/.claude/settings.json` 的 `permissions.deny` 规则 | N/A |
| 生效范围 | 即使 `bypassPermissions` 模式也生效 | N/A |
| 映射 | `code_path` avoid globs → `Read()`/`Edit()` tool deny entries | N/A |

**影响**: 当用户配置 `code_path` avoid 规则（如排除 `./secrets/`、`./credentials/`）时，Python 版本无法执行此限制。Agent 可自由读取和编辑应排除的敏感文件。

### 3.6 Preflight 安全检查缺失

TypeScript preflight (`services/preflight.ts`, 655行) 执行 5 项检查：

| 检查项 | TypeScript | Python |
|--------|-----------|--------|
| Repo 路径存在 + `.git` | ✅ | ✅ |
| Config 解析验证 | ✅ | ❌ |
| `code_path` rule glob 匹配验证 | ✅ | ❌ |
| 凭据验证（API Key / OAuth / Bedrock / Vertex） | ✅ | ❌ |
| 目标 URL 可达性（DNS + HTTP HEAD） | ✅ | ❌ |
| SSRF 防护（169.254.0.0/16 黑名单） | ✅ | ❌ |
| DNS rebinding 防护（固定 DNS 查询） | ✅ | ❌ |
| Loopback 检测（127.0.0.1 / ::1 / 0.0.0.0） | ✅ | ❌ |

**Python preflight** (`pipeline/activities.py`, ~15行) 仅检查 repo 路径存在和 `.git` 目录。

---

## 4. 中危差距 — 工作流可靠性

### 4.1 无不可重试错误类型

| 项 | TypeScript | Python |
|----|-----------|--------|
| 非重试错误类型 | 8种：`AuthenticationError`, `PermissionError`, `InvalidRequestError`, `RequestTooLargeError`, `ConfigurationError`, `InvalidTargetError`, `ExecutionLimitError`, `AuthLoginFailedError` | 未设置 `non_retryable_error_types` |

**影响**: 认证失败、权限错误、配置错误等不可恢复的错误会被 Temporal 反复重试（最多50次），浪费时间和资源。

### 4.2 无心跳超时检测

| 项 | TypeScript | Python |
|----|-----------|--------|
| Heartbeat timeout | 所有 activity 设置 heartbeatTimeout（生产环境60分钟） | 未设置任何 heartbeat timeout |

**影响**: 如果 activity 卡死（如 LLM 无响应），Python 版本无法通过心跳超时检测到此情况。

### 4.3 错误状态下返回 "completed"

| 项 | TypeScript | Python |
|----|-----------|--------|
| 部分失败时 workflow 状态 | 抛出异常 → Temporal 标记为 failed | 返回 `status = "completed"` |
| 错误分类 | `classifyErrorCode` → `state.errorCode` | 无分类 |
| 失败 agent 追踪 | `state.failedAgent` | 无此字段（白盒） |
| 取消处理 | `isCancellation(error)` → `status = "cancelled"` | 无取消处理 |

**影响**: 调用方无法区分完整成功和部分失败。即使多个 agent 失败，workflow 仍报告完成。

### 4.4 Resume 逻辑形同虚设

| 项 | TypeScript | Python |
|----|-----------|--------|
| ResumeState 加载 | `loadResumeState` activity 验证 workspace + deliverables | 不存在 |
| Git 恢复 | `restoreGitCheckpoint` checkout `checkpointHash` + 清理不完整 deliverables | 不存在 |
| Checkpoint 保存 | 每个 agent 完成后 `saveCheckpoint` | 不存在 |
| 短路逻辑 | 所有 agent 已完成 → 立即返回 | 不存在 |
| Python `completed_agents` | — | 始终为空列表（无加载机制） |

**影响**: 中断的扫描无法恢复。`resume_from_workspace` 参数存在但从未使用。

### 4.5 无实时进度查询

| 项 | TypeScript | Python |
|----|-----------|--------|
| Query handler | `setHandler(getProgress, ...)` — Temporal query 支持实时轮询 | 无 `@workflow.query` 装饰器 |
| 进度信息 | `PipelineProgress`（含 workflowId, elapsedMs, currentPhase, currentAgent, completedAgents, agentMetrics） | 不存在 |

**影响**: 无法在扫描运行时监控进度。

### 4.6 无 per-agent Exploit Queue 门控

| 项 | TypeScript | Python |
|----|-----------|--------|
| Queue 检查 | 每个 exploit agent 前有 `checkExploitationQueue` — `decision.shouldExploit` | 无此检查 |
| 行为 | 仅当 queue 有可利用漏洞时运行 exploit | 对所有 selected_classes 无条件运行 exploit |

**影响**: Python 可能对空 queue 运行 exploit agent，浪费 LLM 调用。

### 4.7 Exploit Agent 依赖链错误

| 项 | TypeScript | Python |
|----|-----------|--------|
| Exploit prerequisites | 每个 exploit 依赖对应 vuln agent（如 `injection-exploit` → `injection-vuln`） | 所有 exploit 依赖 `recon`（绕过 vuln） |

**影响**: Python 中 exploit agent 可能在对应 vuln 分析完成前就开始运行。

### 4.8 无 Testing/Subscription 重试模式

| 项 | TypeScript | Python |
|----|-----------|--------|
| Testing 模式 | 30s startToClose, 10s/30s retry, 5 attempts | `pipeline_testing_mode` flag 仅传递到 activity，不影响 timeout/retry |
| Subscription 模式 | 8h startToClose, 5min/6h retry, 100 attempts | 不存在 |

---

## 5. 低危差距 — 配置/验证/扩展性

### 5.1 配置验证大幅削弱

**危险模式检查**:

| 检查字段 | TypeScript | Python |
|----------|-----------|--------|
| `description` | ✅ | ✅ |
| `rules_of_engagement` | ✅ | ✅ |
| `authentication.login_url` | ✅ | ✅ |
| `credentials.username` | ✅ | ✅ |
| `login_flow[*]` | ✅ | ❌ |
| `rules.*.value` | ✅ | ❌ |
| `rules.*.description` | ✅ | ❌ |
| `report.guidance` | ✅ | ❌ |

**Rule Type 特定验证**:

| Rule Type | TypeScript 验证 | Python 验证 |
|-----------|----------------|------------|
| `url_path` | 必须以 `/` 开头 | 必须以 `/` 开头 |
| `code_path` | 不得包含 `://` | 无 |
| `subdomain` | 不得包含 `/` | 无 |
| `domain` | 不得包含 `/`，必须包含 `.` | 无 |
| `method` | 必须是 GET/POST/PUT/DELETE/PATCH/HEAD/OPTIONS | 无 |
| `header` | 必须匹配 `^[a-zA-Z0-9\-_]+$` | 无 |
| `parameter` | 必须匹配 `^[a-zA-Z0-9\-_]+$` | 无 |

**其他缺失的验证**:
- 重复 rule 检测（同一 type+value 在 avoid/focus 中出现两次）
- Avoid/focus 冲突检测（同一 rule 同时出现在 avoid 和 focus 中）
- 废弃字段检测（`path` → `url_path` 迁移支持）
- 字段长度限制（username 1-255, login_flow items 1-500 等）
- 文件大小限制（1MB max）
- TOTP secret 格式验证（`^[A-Za-z2-7]+=*$`）
- URI 格式验证（login_url）
- 至少需要一个 steering field 的要求

### 5.2 Config 模型缺失项

| 缺失模型 | 说明 |
|----------|------|
| `EmailLogin` | Magic-link / OTP 邮件登录流程支持 |
| `ProviderConfig` | 多 LLM 提供商配置（anthropic_api, bedrock, vertex, litellm_router）— Python 仅有未类型化 `dict` |
| `ContainerConfig` | DI 容器配置（deliverablesSubdir, auditDir, apiKey override, promptDir） |
| `PipelineInput` 字段 | 缺少 17 个字段：`pipelineConfig`, `workflowId`, `sessionId`, `terminatedWorkflows`, `configYAML`, `configData`, `deliverablesSubdir`, `auditDir`, `promptDir`, `sastSarifPath`, `checkpointsEnabled`, `skipGitCheck`, `providerConfig`, `exploit`, `whiteboxOnly`, `blackboxOnly` |

### 5.3 三个 Provider 扩展接口缺失

| 接口 | TypeScript | Python |
|------|-----------|--------|
| `FindingsProvider` | 外部安全工具注入（如 Snyk/Semgrep 结果 merge 到 exploitation queue） | 不存在 |
| `CheckpointProvider` | 企业级 resume 控制（pre-agent skip guard + post-agent artifact persistence） | 不存在 |
| `ReportOutputProvider` | 额外报告格式输出（SARIF, PDF 等） | 不存在 |
| `DI Container` | Per-workflow 生命周期、工厂覆盖、Provider 注入 | 不存在 |

### 5.4 Findings Renderer 缺失

| 项 | TypeScript | Python |
|----|-----------|--------|
| 文件 | `services/findings-renderer.ts` (265行) | 不存在 |
| 功能 | `exploit=false` 时从 `*_exploitation_queue.json` 确定性渲染 `*_findings.md`（无 LLM 调用） | N/A |
| 每类渲染器 | 6个自定义渲染函数 | N/A |

**影响**: "仅分析"模式（exploit=false）无法产生任何 findings 输出。

### 5.5 审计系统完成度约 25%

| 项 | TypeScript | Python |
|----|-----------|--------|
| Agent 生命周期 | `startAgent`/`endAgent` 含结构化 `AgentEndResult` | 基础 `start_agent`/`end_agent`，仅 success bool |
| 结果结构 | attemptNumber, duration_ms, cost_usd, model, error | success + optional AgentMetrics |
| 错误日志 | 结构化错误日志 | 不存在 |
| 消息路由 | SDK 消息分发到 progress/audit/error handlers | 不存在 |
| 模型注入 | `injectModelIntoReport()` 将模型信息写入报告 | 不存在 |

### 5.6 Spending Cap 检测完成度约 25%

| 项 | TypeScript | Python |
|----|-----------|--------|
| 文本模式 | 5种 | 6种（多了 `rate limit`，但可能导致误报） |
| API 模式 | 11种（`billing_error`, `credit balance too low` 等） | 不存在 |
| 集成点 | 4个（text, API, behavioral, executor） | 1个（executor only） |

**影响**: Anthropic API 返回的结构化账单错误（如 `credit balance is too low`）不会被检测到。

### 5.7 Workflow 缺失功能

| 功能 | TypeScript | Python |
|------|-----------|--------|
| `pentestPipelineWorkflow` | 完整组合流水线（whitebox+blackbox 一次运行） | 不存在 |
| Phase transition logging | `logPhaseTransition` 每个阶段开始/完成 | 不存在 |
| Workflow completion logging | `logWorkflowComplete` 含 summary | 不存在 |
| Report metadata injection | `injectReportMetadataActivity`（模型元数据注入报告） | 不存在 |
| Report output generation | `generateReportOutputActivity`（最终输出 artifact） | 不存在（blackbox） |
| 并发控制 | `runWithConcurrencyLimit` + `maxConcurrentPipelines` | 无限制（`asyncio.gather`） |
| initDeliverableGit | 初始化 deliverables 目录的 git 仓库 | 不存在 |
| syncDenyRules | 同步 code_path deny 规则 | 不存在 |
| Stealth config sync | 同步 Playwright 反检测配置 | 不存在 |

### 5.8 Blackbox Workflow 行为差异

| 项 | TypeScript | Python |
|----|-----------|--------|
| 对 whitebox 结果的依赖 | **强制要求** — `validateDeliverablesExist` 确认 queue 文件存在 | **可选** — 无结果时运行自己的 recon |
| 独立运行 | 不可能 | 可能 |
| Deliverables 验证 | 通过 Temporal activity（服务端验证） | 本地文件系统检查 |

**影响**: TS blackbox 是严格的"后半段"，PY blackbox 可以独立运行。这是有意的设计差异，但意味着两个版本在相同输入下行为不同。

---

## 6. Python 新增功能（TypeScript 无对应）

| 功能 | 说明 |
|------|------|
| `recon-blackbox` agent | 独立黑盒侦察 agent，无前置依赖 |
| `recon-blackbox.txt` prompt | 23行纯黑盒侦察 prompt（浏览器/HTTP only，无源码访问） |
| Blackbox 独立运行模式 | 无需先运行 whitebox，blackbox 可独立完成侦察+exploit |
| 独立的 CLI 命令 | `shannon-whitebox` 和 `shannon-blackbox` 两个独立 Click 命令 |
| 中文文档体系 | 6份详细中文文档（架构、agents、配置、API参考、快速开始、Prompt工程） |

---

## 7. 逐组件详细对比

### 7.1 Agent 注册表

| Agent | TS | PY | 差异 |
|-------|----|----|------|
| `pre-recon` | ✅ | ✅ | 一致 |
| `recon` | ✅ | ✅ | 一致 |
| `injection-vuln` | ✅ | ✅ | 一致 |
| `xss-vuln` | ✅ | ✅ | 一致 |
| `auth-vuln` | ✅ | ✅ | 一致 |
| `ssrf-vuln` | ✅ | ✅ | 一致 |
| `authz-vuln` | ✅ | ✅ | 一致 |
| `misconfig-vuln` | ✅ | ❌ | **缺失** |
| `recon-blackbox` | ❌ | ✅ | **新增** |
| `injection-exploit` | ✅ | ✅ | prerequisites 不同（TS→vuln, PY→recon）；prompt命名反转（TS: `exploit-injection`, PY: `injection-exploit`） |
| `xss-exploit` | ✅ | ✅ | 同上 |
| `auth-exploit` | ✅ | ✅ | 同上 |
| `ssrf-exploit` | ✅ | ✅ | 同上 |
| `authz-exploit` | ✅ | ✅ | 同上 |
| `misconfig-exploit` | ✅ | ❌ | **缺失** |
| `report` | ✅ | ✅ | prerequisites: TS 6个, PY 5个；displayName 不同 |

### 7.2 Prompt 模板对比

| Prompt | TS 行数 | PY 行数 | 状态 |
|--------|---------|---------|------|
| `pre-recon-code.txt` | 417 | 417 | **完全一致** |
| `recon.txt` | 391 | 391 | **完全一致** |
| `recon-static.txt` / `recon-blackbox.txt` | 380 | 23 | **完全不同**（用途不同） |
| `vuln-injection.txt` | 372 | 372 | **完全一致** |
| `vuln-xss.txt` | 290 | 290 | **完全一致** |
| `vuln-auth.txt` | 262 | 262 | **完全一致** |
| `vuln-ssrf.txt` | 309 | 309 | **完全一致** |
| `vuln-authz.txt` | 367 | 367 | **完全一致** |
| `vuln-misconfig.txt` | 285 | — | **PY 缺失** |
| `exploit-injection.txt` / `injection-exploit.txt` | 451 | 19 | **大幅简化** (96%) |
| `exploit-xss.txt` / `xss-exploit.txt` | 442 | 19 | **大幅简化** (96%) |
| `exploit-auth.txt` / `auth-exploit.txt` | 423 | 19 | **大幅简化** (96%) |
| `exploit-authz.txt` / `authz-exploit.txt` | 425 | 19 | **大幅简化** (96%) |
| `exploit-ssrf.txt` / `ssrf-exploit.txt` | 502 | 19 | **大幅简化** (96%) |
| `exploit-misconfig.txt` | 369 | — | **PY 缺失** |
| `report-executive.txt` | 113 | 22 | **大幅简化** (81%) |
| `validate-authentication.txt` | 25 | — | **PY 缺失** |
| `shared/` (7个文件) | — | — | **全部完全一致** |

### 7.3 Queue Schema 对比

| Schema | TS 字段数 | PY 字段数 | 状态 |
|--------|----------|----------|------|
| `BaseVulnerability` | 5 | 5 | 匹配，但 PY 无 mode-dependent `notes` description |
| `InjectionVulnerability` | 10 + 5 base | 10 + 5 base | **完全一致** |
| `XssVulnerability` | 9 + 5 base | 9 + 5 base | **完全一致** |
| `AuthVulnerability` | 5 + 5 base | 5 + 5 base | **完全一致** |
| `SsrfVulnerability` | 6 + 5 base | 6 + 5 base | **完全一致** |
| `AuthzVulnerability` | 7 + 5 base | 7 + 5 base | **完全一致** |
| `MisconfigVulnerability` | 8 + 5 base | — | **PY 缺失** |

**结构差异**:
- TS 的 `VulnerabilityQueue.vulnerabilities` 是必需字段；PY 默认为空列表 `[]`
- TS 有 `buildOutputFormats(exploit: boolean)` 模式系统，改变 `notes` 字段描述以引导 LLM；PY 无此概念
- TS 有 `getOutputFormat(agentName, exploit)` 和 `VULN_AGENT_QUEUE_FILENAMES` 映射；PY 不在此文件中

### 7.4 Workflow Timeout & Retry 对比

**Whitebox Workflow:**

| Phase | TS Timeout | PY Timeout | TS Retry | PY Retry |
|-------|-----------|-----------|----------|----------|
| Preflight | 2 min | 2 min | 3 attempts, 10s/1min | 无 |
| Pre-recon | 2h | 2h | 50 attempts, 5min/30min, coeff 2 | 50 attempts, 5min/30min, coeff 2 |
| Recon | 2h | 2h | 50 attempts, 5min/30min | 无 |
| Vuln agents | 2h | 2h | 50 attempts, 5min/30min | 无 |

**Blackbox Workflow:**

| Phase | TS Timeout | PY Timeout | TS Retry | PY Retry |
|-------|-----------|-----------|----------|----------|
| Preflight | 2 min | 2 min | 3 attempts | 无（stub pass-through） |
| Recon | — | 2h | — | 3 attempts, 30s/5min |
| Exploit | 2h | 2h | 50 attempts, 5min/30min | 3 attempts, 30s/5min |
| Report assemble | 2h | 5 min | 50 attempts | 3 attempts, 30s/5min |
| Report agent | 2h | 1h | 50 attempts | 3 attempts, 30s/5min |

---

## 8. 修复优先级建议

### P0 — 阻塞运行（必须首先完成）

| # | 项目 | 预估工作量 |
|---|------|-----------|
| 1 | Claude Agent SDK Python 集成 | 大（核心功能） |
| 2 | Exploit prompt 完整移植（5个 prompt 从19行恢复到400+行） | 中（主要是复制+适配） |
| 3 | Report prompt 完整移植 | 小 |
| 4 | Whitebox recon `promptOverride` 机制 | 小 |

### P1 — 安全扫描效果（第二优先级）

| # | 项目 | 预估工作量 |
|---|------|-----------|
| 5 | 恢复 misconfig 漏洞类（agent + prompt + schema + config） | 中 |
| 6 | 认证预校验实现 | 中 |
| 7 | Playwright 反检测配置 + 会话隔离 | 中 |
| 8 | 代码路径 deny rule 执行 | 小 |
| 9 | Preflight 安全检查补全（SSRF/DNS/loopback/credential） | 中 |

### P2 — 生产可靠性（第三优先级）

| # | 项目 | 预估工作量 |
|---|------|-----------|
| 10 | Non-retryable error types 配置 | 小 |
| 11 | Heartbeat timeout 配置 | 小 |
| 12 | 错误状态正确传播（failed/cancelled） | 小 |
| 13 | Resume 逻辑完整实现 | 中 |
| 14 | Per-agent exploit queue 门控 | 小 |
| 15 | Exploit agent prerequisites 修正（依赖 vuln 而非 recon） | 小 |
| 16 | 实时进度 query handler | 小 |
| 17 | Testing/Subscription 重试模式 | 小 |

### P3 — 配置/扩展性（按需）

| # | 项目 | 预估工作量 |
|---|------|-----------|
| 18 | Config 验证补全（rule type validators, 危险模式检查, 字段约束） | 中 |
| 19 | FindingsRenderer 实现 | 中 |
| 20 | Provider 扩展接口（Findings/Checkpoint/ReportOutput） | 中 |
| 21 | DI Container | 中 |
| 22 | 审计系统补全 | 小 |
| 23 | Spending cap API 模式检测 | 小 |
| 24 | 缺失的 Config 模型（EmailLogin, ProviderConfig, ContainerConfig） | 中 |
| 25 | Workflow logging（phase transitions, completion） | 小 |

---

*本报告基于 2026-05-29 两个代码库的状态生成。TypeScript Shannon 位于 `/root/shannon`，Python 重构版位于 `/root/shannon-refactor/shannon-py`。*
