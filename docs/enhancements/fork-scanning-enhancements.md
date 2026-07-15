# Fork 扫描效果增强总结（官方代码之外）

> **基线**：官方上游 `KeygraphHQ/shannon` 的 `upstream/main`（分叉点 `ab2c400d`，2026-03-04；上游最新 `5a2f78c5`，2026-06-23）。本仓库 `main` 相对上游领先 227 个提交，`feat/fork` 在 `main` 之上再领先约 250 个提交。
>
> **范围**：本文档只归纳 fork 相对官方在 **「安全扫描效果」**（漏洞覆盖率 / 判定准确性 / 攻击面触达能力 / 报告完整性）上的方向性增强。**工程基础设施类改动**（npx 分发、Turborepo/pnpm/Biome、telemetry、Docker 隔离、CI/CD 等）不在本文档范围。
>
> **参考**：缺陷诊断见 [`shannon-defects.md`](../shannon-defects.md)；漏报修复原始设计见 [`shannon-missed-vulnerabilities-fix-spec.md`](../shannon-missed-vulnerabilities-fix-spec.md)；上游官方变更见 [`UPSTREAM_CHANGES.md`](../UPSTREAM_CHANGES.md)。

---

## 0. 核心问题意识

所有增强都围绕一个根本判断（见 [`shannon-defects.md`](../shannon-defects.md)）：

> **Shannon 把「发现什么代码需要审计」和「审计代码」都交给了 LLM，但 LLM 无法证明自己没有遗漏。** 代码发现本应是确定性问题，不应依赖概率性推理。

由此衍生三类历史漏报（见漏报修复 spec）：

1. **框架自动生成的端点漏识别** —— finale-rest / epilogue / proto `method_option_http_api` 等运行时生成的端点，静态分析抓不全。
2. **多步骤攻击链断裂** —— 前端路由 → API → 渲染的完整链路在 agent 之间断开。
3. **前后端脱节 + 三层信息衰减** —— PRE_RECON → RECON → Vuln 三次 LLM 传递，每层都可能丢信息，且丢失向后传播不可恢复。

fork 的扫描效果优化，本质上就是用 **确定性流程约束 + 跨 agent 知识流动 + 机械化的覆盖度对账**，把上述「无法自证的遗漏」逐个堵死。

---

## 方向一：Recon 阶段的「覆盖度机械化对账」

> 把「漏没漏端点」从「LLM 觉得扫完了」变成「可计算、可强制」的机械检查。对应缺陷 D1/D2（覆盖不可证明）。

- **多角度并行枚举**：recon 不再单一维度找端点，而是并行从 **5 个角度** 各扫一遍再去重合并（`feat(recon): multi-angle enumeration`，`95de7d1f`）：
  1. 路由定义层（`router.get/post`、装饰器、配置驱动路由表）
  2. Controller 方法层
  3. 接口契约层（proto `method_option_http_api` / `http_url`、OpenAPI/swagger、graphql schema）
  4. 前端调用层（axios/fetch/rpc 反推后端端点）
  5. 网关层（nginx `location` / `proxy_pass`、ingress）
- **枚举完整性对账 checklist**（`prompts/shared/_enumeration-completeness.txt`）：每个角度的 Task agent 必须同时返回 (a) 找到的端点 (b) **源锚点计数 source anchor count**（用了哪个 grep pattern、数了多少处路由来源）。recon 合并后产出 `### 4.2 Enumeration Reconciliation` 表，逐行分类每个 delta：`dedup` / `out-of-scope` / `true-miss`。**任何 `true-miss` 未清零前，禁止宣布 "RECONNAISSANCE COMPLETE"。**
- **USER 端点覆盖对账**（`prompts/shared/_coverage-reconciliation.txt`）：用集合运算 `G = F \ C`（F=recon 列出的全部 user 端点，C=已下过结论的端点）强制 authz 阶段对每个未判定端点都给出 vulnerable / safe 结论，**按「数据归属」而非「有没有参数」判定向量**（`brokerage`/`region`/`tenant_id` 等租户选择器也是向量），且 **N 个受影响端点必须产 N 条独立 finding，禁止用一条 `/api/*` 全局糊弄**。

这是 fork 最核心的一条扫描效果主线 —— 用 prompt 内嵌的「终止前置 checklist」把覆盖率变成硬约束。

---

## 方向二：Cross-Route Enumeration（共享 handler 路由全展开）

> 修一类典型漏报：同一个 handler 被多个路由共享，其中某条路由缺鉴权（pre-auth），但 agent 只在一条路由上下结论，漏掉了 pre-auth 变体。

- 共享 partial `prompts/shared/_cross-route-enumeration.txt`：要求 vuln agent **在写任何 finding 之前**先读 `recon_deliverable.md` 的 Section 4.1（共享控制器路由组），定位自己刚分析的 handler 所属的路由组，把组内 **每一条路由** 都展开进 `affected_routes`：
  - pre-auth 路由 → 单独成 finding、`externally_exploitable: true`
  - 不同 auth tier → 按 tier 拆成多条 finding（保留下游利用上下文）
- 已注入 injection / xss / auth / authz / ssrf **全部五个** vuln agent（`f8e730bd` ~ `60483e25`）。
- 配套把 recon Section 4.1 从「一组一行（one-row-per-group）」改成「一路由一行（one-row-per-route）」，每行显式标注 auth middleware，杜绝把共享 handler 的多条路由塞进一个单元格（`a656811f` / `f42da7e0` / `7ed1720d`）。

---

## 方向三：端点安全上下文 + 框架自动端点识别

> 修「框架自动生成端点漏识别」漏报，同时划清 Recon（描述）/ Vuln（判断）的职责边界。

- 共享 partial `prompts/shared/_endpoint-security-context.txt`：recon 为每个端点建立 **描述性** 安全上下文 —— 完整 HTTP 方法列表（禁止 `ALL` 简写）、认证要求、middleware 链、**框架来源（finale-rest / epilogue / 其他）**、参数清单、所有权校验是否存在。
- **框架模式识别规则**：检测到 `finale.initialize()` / `epilogue.resource()` 时，对每个 model **假定** CRUD 五件套自动存在（`findAll` / `findOne` / `create` / `update` / `destroy`），再逐一核对有无 override —— 直接解决「DELETE /api/Feedbacks/:id 这类自动端点 Recon 漏列、authz agent 没东西可测」的历史漏报（漏报修复 spec 案例 1）。
- 注入 recon deliverable，并由 authz / auth / xss / injection agent 读取（`f578e011` / `4be0684c` / `3354bfd8`）。
- 职责边界：Recon 只描述「有什么保护」，Vuln agent 只判断「保护是否充分」—— 消除两者重复劳动（漏报 spec §3.1）。

---

## 方向四：跨 Agent 知识共享（对抗三层信息衰减）

> 修缺陷 D3：PRE_RECON → RECON → Vuln 三次 LLM 传递的信息衰减。

- 共享 partial `prompts/shared/_shared-knowledge.txt`：把前置 agent 已积累的上下文（`{{SHARED_KNOWLEDGE}}`）注入下游 agent，明确告知如何使用 —— 框架分析、端点清单、**前端路由 → 后端 API 映射**、预组装的攻击链。
- 前端路由 → API 映射用于追踪「用户输入 → 存储 → 渲染」的完整数据流，直接修「多步骤攻击链断裂」（漏报 spec 案例 2：Video XSS `/videos` → POST → `/videoManager` 渲染）。
- 一句话：让下游 agent **不必从零重新发现** 前序 agent 已确认的事实，减少重复推理、抑制衰减。

---

## 方向五：Injection Recall 精修

> 针对 injection 类漏洞做专项召回率提升（减少漏报）。

- `feat(prompts): improve vuln-injection SQLi/CMD recall`（`6ca84f7e`）。
- `exploit-injection` 对 `externally_exploitable=false` 的队列条目做 **可达性二次判定**（`58393e9c`），而不是直接跳过 —— 避免被误标为不可利用的真漏洞在利用阶段被无声丢弃。
- 细化 injection 可达性判定 + 跨服务 `witness_payload`（`3cd3649f`）。
- 厘清 `combined_sources` 是元数据、**不能替代逐源 tracing**（`be31142d`），防止 agent 用汇总信息糊弄掉逐条数据流追踪。

---

## 方向六：扫描触达能力 —— 认证预检 + 会话复用

> 扫描效果的前提是「能打到需要登录的端点」。这是 fork 在 auth 上投入最重的方向。

- **auth-validation 预检**：新增 `validate-authentication` 活动与 prompt，支持 `email_login` 等凭证类型，在流水线真正开工前先做一次 **真实登录** 验证凭据有效（对齐上游 #335，在 `feat/auth-validation-preflight` 分支深度定制）。
- **会话跨 agent 共享**：预检登录成功后把浏览器会话存入 `auth-state.json`，各 agent 通过 `prompts/shared/_shared-session.txt` 复用，验证失败再 fallback 到完整登录流程（对齐上游 #345）。
- **登录流程模板** `prompts/shared/login-instructions.txt` 覆盖 form / SSO / API / basic auth 四种认证；配合 `playwright-cli` 会话隔离与 `generate-totp` 处理 MFA。
- **预检加固**：URL 校验拦截云元数据 IP 段（`#337`），避免误扫 `169.254.*`。
- 价值：没有这套机制，所有认证后端点（绝大多数业务 API）对扫描器不可见 —— 这一条决定了「扫描能看见多大攻击面」。

---

## 方向七：白盒 / 黑盒分叉 + 本地 Runner

> 让同一套 pipeline 能跑纯白盒（只看代码）、纯黑盒（只打活体），并能脱离 Docker/Temporal 在本地直跑。

- **whitebox-only / blackbox-only 分叉模式**（`346dae27`）：pipeline 在 preflight 后按模式分叉，白盒不依赖运行实例。
- **白盒下 URL 可选 + 条件提示块**（`db674443`）：白盒场景没有活体 URL，prompt 用条件块适配。
- **`recon-static` 静态路由侦察**：白盒模式下用 `recon-static.txt`（仓库内最大 prompt，35KB）覆盖默认 recon —— 以纯静态路由/控制器/网关分析替代浏览器动态探索，把方向一的覆盖度对账机制全部带进白盒（`local/runner.ts:190`、`temporal/workflows.ts:757` 两处接线）。
- **本地 whitebox runner**（`apps/worker/src/local/runner.ts`）：绕过 Temporal + Docker 直接本地执行，`SHANNON_CONCURRENCY` 控制并行度，并补齐 **resume 能力**（恢复已完成 agent / phase，`aa93d1e4`）。
- **黑盒 resume 修复**：恢复时正确跳过已完成的 exploit agent（`e506ddd5` / `36ee230b`），会话注册提前到 preflight 之前防 CLI 超时（`6c3740a6`）。

---

## 方向八：确定性报告渲染 + 可利用端点附录

> 修「终报告被 LLM 折叠，端点级 ground truth 丢失」问题 —— 报告里 91 条端点 finding 被压成 10 条，单看报告无法复核。

- **确定性端点附录（Appendix A）**：`apps/worker/src/services/affected-endpoints-appendix.ts` 从各 `*_exploitation_queue.json` **机械渲染** 出「可利用端点清单」附录，注入到 `report-executive` 之后（`08d49a43` / `77ae084f` / `c0565e2d`），**全程无 LLM 参与**。
- report prompt 显式引用 Appendix A，让折叠后的主报告与端点级真相可对照（`ec34d586`）。
- `exploit: false` 时 `findings-renderer.ts` 确定性地把每个 `*_exploitation_queue.json` 转成 `*_findings.md` 供报告组装 —— 关掉利用阶段也不丢漏洞清单。
- 带 unit + integration 测试，并用真实 authz queue 做了集成校验（`d6c41918`）。
- 价值：端点级 ground truth 永远可追溯，复核时查 Appendix A 而不是被 LLM 压缩过的叙述。

---

## 方向九：分支 / 模板路径覆盖补齐

> 补齐官方默认配置里漏掉的 agent 与覆盖路径。

- **白盒模式补回 XSS agent**（`ffbfd29f`）：官方白盒 vuln agent 列表一度缺 xss，补上并改善分支路径覆盖。
- **vuln agents 从 `ALL_VULN_CLASSES` 派生**（`5b7e5849` / `9bb7e650`）：vuln agent 列表与并发默认值改为从单一真相源派生，新增/删除漏洞类别时不再靠手工同步多处常量，杜绝「加了一类漏洞但 pipeline 没跑」。
- **模板覆盖 / queue 校验 / 黑盒 overlay 播种**修复（`5c69195c`）。

---

## 方向十：Config 驱动的扫描范围控制

> 让「这次扫什么、报什么」可精细配置且可锁定。

- **run scoping**：`vuln_classes`（扫哪些漏洞类别）、`exploit`（是否跑利用阶段）、报告后置过滤 `min_severity` / `min_confidence` / `guidance`（对齐上游 #326，本地深度适配）。
- **`code_path` avoid 规则下沉到 SDK 工具层**：avoid 规则被写入 `~/.claude/settings.json` 的 `permissions.deny`（`Read`/`Edit`），由 SDK 在工具层强制，即使在 `bypassPermissions` 模式也生效（`apps/worker/src/ai/settings-writer.ts`）。
- **run scope 锁定**：scope 首次运行写入 `session.json`，resume 时 scope 不一致直接 fail-fast，防止「换个 scope 接着跑」污染结果。

---

## 方向十一：报告中文翻译

- **ReportTranslationProvider**（`89063c57` / `31399934`）：实现 `ReportOutputProvider` 接口，报告生成后翻译成中文，并上报成功/失败计数（`da56725c`）。
- CLI 侧挂载 `deliverables-cn` overlay 让黑盒翻译产物可写（`efa922b0` / `35d3d964`）。
- 属于交付物本地化，间接提升报告对中文读者的可读性与可复核性。

---

## 附：对齐上游的扫描相关改动（移植，非 fork 原创）

下列为 fork 已合并、但源自官方 PR 的扫描相关改动，列出以完整起见（详见 [`UPSTREAM_CHANGES.md`](../UPSTREAM_CHANGES.md)）：

| 上游 PR | 内容 | 性质 |
| --- | --- | --- |
| #267 | vuln agent exploitation queue 改用 structured outputs | 移植 |
| #274 | pre-recon deliverable 文件名不匹配修复 | 移植 |
| #326 | config 驱动 run scoping + report 过滤 | 移植 + 本地深度适配（见方向十） |
| #335 | auth-validation 预检 + email_login 凭证 | 移植 + 本地深度定制（见方向六） |
| #345 | preflight 认证 session 跨 agent 共享 | 移植 + 本地深度定制（见方向六） |
| #337 | URL 校验拦截云元数据 IP 段 | 移植 |

---

## 附：已回退的尝试 —— 自实现 misconfig agent

fork 曾自研一个独立的 **misconfig（误配置）agent** 并加入 vuln 类别。近期评估后判定：自实现的误配置检测 **效果不如让现有 vuln agent 在其类别内顺带覆盖配置问题**，遂整体移除 —— 从 `vuln_classes` schema / example、worker agent 实现、白盒 vuln agent 列表、项目文档与样例报告中清除（`674c949f` / `07d8fa72` / `7635eca9`，原引入 `597dade3`）。

> 这是一次有意识的「做减法」：扫描效果优化不是只加 agent，也包括识别出 **低效的自建分类** 并回归上游模型。

---

## 一句话总览

| # | 方向 | 解决的扫描效果问题 | 关键载体 |
| - | --- | --- | --- |
| 1 | Recon 覆盖度机械化对账 | 端点漏发现、覆盖不可证明 | `_enumeration-completeness.txt` / `_coverage-reconciliation.txt` / 多角度枚举 |
| 2 | Cross-route enumeration | 共享 handler 的 pre-auth 路由漏判 | `_cross-route-enumeration.txt` + Section 4.1 one-row-per-route |
| 3 | 端点安全上下文 + 框架识别 | 框架自动端点漏识别、职责混淆 | `_endpoint-security-context.txt` |
| 4 | 跨 agent 知识共享 | 三层信息衰减、攻击链断裂 | `_shared-knowledge.txt` |
| 5 | Injection recall 精修 | 注入类漏报、误弃不可利用项 | vuln-injection / exploit-injection prompt |
| 6 | 认证预检 + 会话复用 | 认证后端点不可见 | `validate-authentication` / `_shared-session.txt` |
| 7 | 白盒/黑盒分叉 + 本地 runner | 场景覆盖、脱离 Docker 运行 | `local/runner.ts` / `recon-static.txt` / 模式分叉 |
| 8 | 确定性端点附录 | LLM 折叠丢端点级真相 | `affected-endpoints-appendix.ts` |
| 9 | 分支/模板路径覆盖补齐 | 白盒缺 agent、类别常量散落 | xss 回归 / `ALL_VULN_CLASSES` 派生 |
| 10 | Config 驱动范围控制 | 范围不可控、avoid 规则被绕过 | run scoping + settings.json deny |
| 11 | 报告中文翻译 | 报告可读性 | `ReportTranslationProvider` |

**主线**：fork 没有走「堆更多 vuln 类别」的路线，而是系统性地给 LLM 套上 **确定性的覆盖度约束、跨阶段的知识流动、可追溯的报告渲染**，把 Shannon 从「LLM 尽力而为」推向「覆盖率可对账、结论可复核」。
