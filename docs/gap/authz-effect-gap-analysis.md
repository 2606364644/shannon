# 越权分析效果差距分析

> 对比原始 Shannon（TypeScript, `/Users/mango/project/shannon-refactor/shannon`）与重构 Shannon-py（Python）在 **越权分析（Authorization / authz）检测效果**上的差距。
>
> **聚焦口径**：效果/效能（动态验证能力、攻击链质量、真实产出对比），**刻意避开** `sink-gap-analysis-v2.md` §2.12（v7）已覆盖的 authz prompt 文本逐行对比。
>
> **数据来源**：① 逐文件代码核验 ② 重构真实扫描产出（`workspaces/juice-shop_whitebox-1780587584138/`）③ 原始漏报报告（`shannon/docs/shannon-xss-authz-missed-vulnerabilities-report.md`）④ 原始多账户设计（`shannon/docs/superpowers/specs/2026-06-14-authz-multi-account-design.md`）。以代码与实测产出为准。
>
> **日期**：2026-06-16

---

## 0. 与现有 gap 文档的关系定位

本文档**不重复**以下已有结论，仅在必要时交叉引用：

| 现有文档 | 已覆盖维度 | 本文档关系 |
|---|---|---|
| `sink-gap-analysis-v2.md` §2.11/§2.12（v7） | auth/authz **prompt 文本**逐行对比（确定性层 0 条、专家层 auth 平手 / authz 互有胜负、SK-14/SK-15） | **不重复**。本文聚焦 prompt 之外的**运行时效果** |
| `route-analysis-binding-gap-analysis.md` RA-1/RA-9 | 攻击链 **confidence 升级缺失**（漏洞上下文不回灌） | **引用**。RA-1 对 authz IDOR 链同样适用，本文转述其对越权效果的具体影响 |
| `whitebox-refactoring-assessment.md` | 全维度架构评估 | **引用**其 authz 框架 IDOR / 路由级 auth 的判断 |

**核心问题**：现有文档多从「prompt 文本 / 服务移植完整性」角度对比，缺一份从「**实际能不能检出、检出后证据有多硬**」角度的对比。本文填补这个空白。

---

## 1. 三层能力框架对比

两项目的越权分析都是 **白盒静态（vuln-authz）+ 黑盒动态（exploit-authz）** 两阶段，外加一层确定性路由级 IDOR 链。下表对比三层的**实际效能**（非文本行数）。

| 层 | 原始 Shannon (TS) | 重构 Shannon-py | 效果差距定性 |
|---|---|---|---|
| **① 静态分析 vuln-authz**（纯代码，无 live） | guard-dominance + side-effect 数据流，H/V/Context 三类，强依赖 recon §4.2/§8；代码审查强制委托 Task Agent | 方法论逐字对齐（`prompts/vuln-authz.txt` 372 行 vs 原始 415 行），`@include _endpoint-security-context.txt` + `_static-dataflow-hints.txt`（Plan 3 恢复） | **基本持平**（详见 §2 实测产出） |
| **② 动态利用 authz-exploit**（live HTTP/playwright） | proof-based，要求 **Level 3+**（拿到实际未授权数据，不只看 200/403）；多身份迭代走 Task Agent（"≤5 identities/run"） | prompt 几乎逐字对齐（`prompts/authz-exploit.txt` 427 行 vs 原始 415 行）；exploitation_checker 校验 queue 非空 | **运行机制对齐，身份供给存在差距**（详见 §3） |
| **③ 确定性路由级 IDOR 链** | `route-chain-builder.ts` + `attack-chain-builder.ts`（后者读 SharedKnowledge 升级 confidence） | `route_chain_builder.py`（IDOR 链 confidence 固定 `probable`）+ `attack_chain_builder.py`（无漏洞上下文增强） | **重构缺 confidence 升级**（详见 §4，引用 RA-1） |

**裁决**：方法论与流水线**架构对齐**；真正的效果差距集中在 **动态身份供给（§3）** 和 **攻击链置信度（§4）**，而非静态分析能力本身。

---

## 2. 真实产出对比 ★

> ⚠️ 口径说明：原始数据来自其**漏报报告**（特定时间点、经 exploit 验证的覆盖率）；重构数据来自 **2026-06-04 一次 Juice Shop 实测扫描**（`workspaces/juice-shop_whitebox-1780587584138/`）。两者靶场实例、prompt 版本、时间点均不同，**只能作量级参考，不可严格等同**。

### 2.1 重构实测产出（一次扫描）

| 产出文件 | 内容 | 证据 |
|---|---|---|
| `authz_exploitation_queue.json` | **16 条 AUTHZ-VULN**（vuln 阶段静态候选） | 6 Horizontal + 6 Vertical + 4 Context_Workflow；14 high / 1 med / 1 low |
| `authz_exploitation_evidence.md` | **15/16 EXPLOITED**（exploit 阶段动态验证） | VULN-08（low-confidence `Number()` 类型强转绕过）未在 evidence 确认；其余 15 条均含完整 curl PoC + HTTP 响应 + 影响证明 |

**重构检出的 16 条**（按类别）：

| 类别 | AUTHZ-VULN | 端点 |
|---|---|---|
| Horizontal（水平/IDOR）×6 | 01 | `GET /rest/basket/:id`（basket.ts:19，无 UserId 过滤） |
| | 02 | `GET /api/Users/:id`（finale auto-gen，无 ownership） |
| | 05 | `PUT /api/BasketItems/:id`（仅 req.params.id，无 ownership） |
| | 06 | `PUT /rest/products/:id/reviews`（author 来自 body，无 auth） |
| | 07 | `PATCH /rest/products/reviews`（multi:true + NoSQL 注入，批量改 31 条 review） |
| | 08 | `POST /api/BasketItems`（Number() 类型强转绕过，low confidence） |
| Vertical（垂直/提权）×6 | 03 | `GET /api/Users`（仅 isAuthorized，无 requireAdmin，批量 PII） |
| | 04 | `PUT /api/Products/:id`（isAuthorized 被注释，无任何中间件） |
| | 09 | `POST /api/Users`（mass-assignment，`role:admin` 自助注册管理员） |
| | 10 | `GET /api/Complaints`（仅 isAuthorized） |
| | 11 | `POST /api/Products`（仅 isAuthorized） |
| | 12 | `POST /rest/deluxe-membership`（paymentMode 非 wallet/card 绕过付费） |
| Context_Workflow ×4 | 13 | `POST /rest/2fa/verify`（硬编码 RSA key 伪造 tmpToken） |
| | 14 | `GET /rest/user/change-password`（省略 `current` 参数短路校验） |
| | 15 | `POST /rest/user/reset-password`（硬编码 HMAC key + 无限枚举） |
| | 16 | `POST /rest/basket/:id/checkout`（无 ownership，跨用户代付） |

### 2.2 原始漏报基线

原始 `shannon-xss-authz-missed-vulnerabilities-report.md` 记录（Juice Shop）：

| 指标 | 数值 |
|---|---|
| 官方越权挑战 | 12 |
| Shannon 发现 | 16（含官方未覆盖变体） |
| 漏报 | 2+ |
| 覆盖率 | ~70%（含部分覆盖） |

**原始已覆盖**：basketAccess、registerAdmin、freeDeluxe、forgedFeedback、forgedReview、changeProduct。
**原始漏报**：① Five-Star Feedback（`DELETE /api/Feedbacks/:id`，recon 用 "ALL" 未明确列 DELETE）② Admin Section（`/administration` 前端路由，recon 未识别为越权目标）③ Manipulate Basket（部分，代码逻辑矛盾）。

### 2.3 对比裁决

- **广度**：重构单次扫描的 authz 覆盖**广度不弱于原始报告的 70%**，甚至额外检出了 mass-assignment admin 注册（09）、2FA 伪造（13）、NoSQL 批量 review 篡改（07）等原始报告未明确归入 authz 的项。
- **共同漏报点**：重构 queue 未见 `DELETE /api/Feedbacks/:id`（Five-Star Feedback）——与原始漏报**同形**，提示这是**两项目共有的 prompt 层缺陷**（recon "ALL" 符号掩盖 DELETE 方法），非重构独有（详见 §6）。
- **不可比性提醒**：原始的 70% 是「经 exploit 验证的官方挑战覆盖率」，重构的 16 条是「单次扫描的检出数」。**不要简单得出"重构覆盖率 > 原始"的结论**——但可以确认：**重构静态+动态的越权检出能力在 Juice Shop 上已达到实用水平，不存在"越权分析基本失效"的系统性缺陷**。

---

## 3. 动态验证效能 ★★（核心差距之一，但需修正口径）

### 3.1 架构事实：两项目都是单账户

| 项 | 原始 | 重构 |
|---|---|---|
| 会话共享 | `_shared-session.txt`，全流水线共享单一 `auth-state.json`（`audit/utils.ts:81-82`） | 同架构，单会话 |
| config | `authentication` 仅一组凭据，**无 `accounts` 字段** | 同 |
| exploit prompt 残留 | `exploit-authz.txt` Task Agent 模板要求 `Identity set`，"≤5 identities/run"，但 config 只供 1 个 → identity 集合实际为空 | `authz-exploit.txt:169` 同样残留 "DO NOT exceed 5 identities per run"，**底层同样无多身份供给** |

### 3.2 关键修正：重构在开放注册靶场**自发绕过**了单账户限制

这是本节最重要的发现，**推翻了"单账户导致 IDOR 无法验证"的简单论断**。重构实测 evidence 中，动态验证**系统性使用现场注册的 attacker + victim 双账户对照**（水平 IDOR 与工作流类均如此）：

| AUTHZ-VULN | evidence 中的对照证据 |
|---|---|
| 01（basket IDOR） | PoC 注册 `attacker@test.com` + `victim@test.com`；"Attacker (ID 25, customer role) accessed victim's basket (ID 8, UserID 26)" |
| 05（BasketItems IDOR） | "Customer (ID 25) modified basket items belonging to victim (ID 26)" |
| 16（checkout IDOR） | "Customer (ID 25) checked out victim's basket (BID 8) using their own credit card" |
| 14（改密码） | 注册 `victim_exploit@test.com`，省略 `current` 改其密码，验证旧密码失效 |
| 15（重置密码） | 用已知 security answer 重置 `admin@juice-sh.op` 密码 |

**机制**：Juice Shop 开放注册，`authz-exploit` agent 自主注册了第二个账户充当 victim/baseline，完成了"victim 持有私有数据 → attacker 越权访问 → 对比"的协议。**这是单账户架构下、靠 agent 智能达成的等价多身份验证**。

### 3.3 那原始为什么还把它当"fundamental defect"？

原始 `2026-06-14-authz-multi-account-design.md` 的 Problem 段把单账户定性为 authz 测试的"fundamental defect"，并**明确把"自动 victim 注册"列为 Non-Goal**：

> "Automatic victim account registration (open-registration abuse, ROE risk, empty-account problem). Identities are **operator-supplied**."

原始主张的是 **operator 手动供给 `accounts[]`**（victim/baseline/attacker 三角，每个 identity 独立 Playwright session），而非 agent 现场注册。理由：

1. **ROE 合规**：agent 在生产目标上自主注册账户是违规/危险行为（`auth-exploit.txt:165` 也强调"visit as the victim"需可控身份）。
2. **封闭注册目标**：生产/企业应用通常不开放注册，现场注册失效 → 此时单账户缺陷真正暴露（无 victim baseline，水平 IDOR 只能枚举拿 200，无法证"是他人私有数据"）。
3. **垂直越权的 admin baseline**：admin 账户无法注册得到，需 operator 预置 → 单账户下"仅 admin 可达"无能力基线。
4. **身份污染**：单 Playwright session 反复登入登出有 cookie/storage 串扰风险。

### 3.4 两项目差距（修正后）

| 维度 | 原始 | 重构 |
|---|---|---|
| 开放注册靶场（Juice Shop）实测 | （报告未明示是否现场注册；漏报主因在 recon 非 identity） | **实测通过现场注册达成高质量验证（15/16 EXPLOITED，唯一未确认项为 low-confidence 类型强转绕过）** |
| 多账户**正规设计** | ✅ 完整设计蓝图（`Account` 类型、`accounts[]` config、多 slot session、victim/baseline/attacker 协议），**未实现** | ❌ **无任何设计文档**（仅有继承自原始的 "≤5 identities" prompt 残留） |
| 封闭注册/生产目标能力 | 设计蓝图指向 operator 供给（未落地，仍单账户） | 同样单账户，且无改进路线 |
| ROE 合规 | 设计明确排斥自动注册 | 依赖 agent 自主注册（开放靶场奏效，生产场景违规） |

**裁决（修正版）**：
- 在**开放注册靶场**上，两项目的动态验证效果**实际接近**（重构实测甚至更完整），单账户缺陷被 agent 现场注册**事实绕过**。
- 真正的差距是**面向生产/封闭注册目标的可用性**：原始有 operator 供给多账户的**设计蓝图**（虽未实现），重构**连蓝图都没有**。这是一条**面向真实目标的能力债**，而非"重构越权分析在靶场上不行"。

---

## 4. 攻击链置信度质量 ★★（引用 route-binding RA-1）

> 完整代码级论证见 `route-analysis-binding-gap-analysis.md` §4 / RA-1 / RA-9。本节只转述其对**越权效果**的具体影响。

### 4.1 事实

- 原始 `attack-chain-builder.ts:36-52` 读 `SharedKnowledge.vulnerabilityContext`，对攻击链每个 step 查 `endpointVulnerabilities`，若存在 `confirmed` 漏洞则把链 confidence 升级为 `confirmed`。
- 重构 `attack_chain_builder.py` **无此增强步骤**（仅读 framework_result + frontend_result），IDOR 链构建时 confidence 固定为 `probable`（`route_chain_builder.py` IDOR 分支）。

### 4.2 对越权效果的影响

| 影响面 | 说明 |
|---|---|
| **不影响检出** | authz 漏洞的检出由 vuln-authz（静态）+ authz-exploit（动态）完成，攻击链只是**报告编排**层。重构实测仍检出 16 条。 |
| **影响报告 confidence 标注** | 重构的 IDOR 攻击链**永远 `probable`，无法升 `confirmed`**，即使下游已 EXPLOITED。报告读者看到的链可信度被低估。 |
| **影响跨漏洞串联** | 原始可用已确认漏洞增强链；重构的 `attack_chains.json` 实为 `route_chains.json` 副本（RA-9，activity 冗余）。 |

**裁决**：严重度**中**。这是**报告质量/可读性**问题，不直接降低越权检出能力。与 route-binding 共担修复（修 RA-1 即同时解决 authz 链）。

---

## 5. recon→authz 数据通路质量

### 5.1 事实

| 项 | 原始 | 重构 |
|---|---|---|
| recon §4.2 Endpoint Security Context | 显式列 `Framework Origin`（finale-rest/epilogue）+ `Ownership Check` | 简化（`whitebox-refactoring-assessment.md:210,215` 指出框架来源列弱于原始） |
| vuln-authz 的 framework 专项 | `vuln-authz.txt` 415 行，含 finale-rest/epilogue ORM-to-REST IDOR 专项（18 行指导 + JSON 字段示例） | 372 行（**删 framework 专项 −43 行**，sink-gap SK-15） |
| vuln-authz 查表依据 | recon §4.2 框架来源 + ownership → 高危信号 | `@include _endpoint-security-context.txt`（Plan 3 恢复）+ 通用 IDOR 方法论 |

### 5.2 关键修正：删 framework 专项**未明显拉低实测检出**

sink-gap SK-15 判定"重构对 finale-rest 类 IDOR 检测弱于原始"。但**重构实测 queue 恰恰检出大量 finale auto-gen 端点的 IDOR**：

| 重构检出的 finale 端点 | 类别 |
|---|---|
| `GET /api/Users/:id`（finale） | Horizontal |
| `PUT /api/Products/:id`（finale auto-gen PUT） | Vertical |
| `POST /api/BasketItems`（finale） | Horizontal |
| `POST /api/Users`（finale，mass-assignment） | Vertical |

**原因**：finale-rest 端点本质是常规 REST CRUD，通用 IDOR 方法论（"side effect 前是否有 ownership guard"）已覆盖。删 framework 专项损失的是**针对该框架的显式查表提示**，但 recon 的 endpoint security context + 通用方法论**实测兜住了**。

### 5.3 裁决

严重度**低**。删 framework 专项是"去特定化"取舍，实测未造成 finale IDOR 系统性漏检。真正的残余风险在 §6 的 **DELETE 方法枚举**（与 framework 端点的非 GET 方法相关，两项目共有）。

---

## 6. 两项目共有缺陷（非重构独有）

| # | 缺陷 | 根因 | 影响 | 证据 |
|---|---|---|---|---|
| C-1 | **DELETE 方法漏报**（Five-Star Feedback 类） | recon 用 "ALL" 符号表示端点，未逐方法列出 DELETE；`isAuthorized()` 只验 JWT 不验所有权 | `DELETE /api/Feedbacks/:id` 等变更操作可能漏测；重构 queue 未见此端点 | 原始报告 §2.1/§7.2.1；重构 queue 无 Feedbacks DELETE |
| C-2 | **前端路由↔后端 API 映射缺失**（Admin Section 类） | recon 聚焦后端 REST；`AdminGuard` 客户端校验未被评估；`frontend_mapper` 的 `apiCalls`/`userInputs` 提取是**空壳**（两版均为 `[]`） | `/administration` 类前端路由越权无法检测；XSS 链检测两版均无产出 | 原始报告 §2.3/§7.2.2；`route-binding-gap` §2.7 |
| C-3 | **跨 agent 攻击链缺失**（Video XSS 类） | 各 vuln agent 独立工作，无跨 agent 数据共享/攻击链重组 | 需多漏洞组合的多步攻击（越权改配置 + 上传 + XSS 触发）无法检出 | 原始报告 §3.2/§5.3 |

**裁决**：这三项是**架构级共同短板**，非重构相对原始的退化。修复需较大工程（尤其 C-3 的跨 agent 知识共享）。

---

## 7. 差距矩阵 + 严重度/紧急度评估

> 服务于后续 Spec 决策。**严重度** = 对越权分析效果（检出能力/证据质量）的影响；**紧急度** = 结合"靶场已很强、生产才暴露"的现状判断。

| # | 差距项 | 原始 | 重构 | 对效果的影响 | 严重度 | 紧急度 | 修复难度 | 建议 |
|---|---|---|---|---|---|---|---|---|
| **AZ-1** | 多账户身份对照（operator 供给） | 有设计蓝图，未实现 | 无设计、无实现 | 仅在**封闭注册/生产目标**暴露（开放靶场被现场注册绕过） | **中**（生产）/ 低（靶场） | **中** | 中-高（config schema + 多 session + 协议） | **移植原始设计蓝图**（`2026-06-14-authz-multi-account-design.md`），先落 config + 多 session，再做 victim/baseline 协议 |
| **AZ-2** | 攻击链 confidence 升级（RA-1） | ✅ SharedKnowledge 增强 | ❌ 缺失 | 报告中 IDOR 链永远 `probable`；不降检出 | **中** | 中 | 中（attack_chain_builder 加漏洞上下文参数） | **与 route-binding RA-1 合并修**，一份 Spec 同时解决 |
| **AZ-3** | recon 框架来源标注（§4.2） | ✅ 完整 | ⚠️ 简化 | 实测未明显拉低 finale IDOR 检出 | **低** | 低 | 低 | 可选；优先级低于 AZ-1/AZ-2 |
| **AZ-4** | DELETE 方法枚举（C-1） | 共有缺陷 | 共有缺陷 | `DELETE /api/Feedbacks/:id` 类漏测 | 低-中 | 低 | 低（recon prompt 明确逐方法列出） | 短期 prompt 优化即可，两项目通用 |
| **AZ-5** | 前端路由 authz（C-2） | 共有缺陷 | 共有缺陷 | `/administration` 类前端路由越权盲区 | 中 | 低-中 | 中（依赖 frontend_mapper apiCalls 提取落地） | 与 route-binding RA-4 同源，需先实现 API 调用提取 |
| **AZ-6** | 跨 agent 攻击链（C-3） | 共有缺陷 | 共有缺陷 | Video XSS 类多步组合攻击漏报 | 中 | 低 | 高（SharedKnowledge + 重组引擎） | 长期项，单独 Spec |

### 7.1 优先级建议（性价比排序）

1. **AZ-2（攻击链 confidence，合并 RA-1）**：中影响、中难度、一份 Spec 解决两个 gap 文档的问题。**最高性价比**。
2. **AZ-1（多账户身份对照）**：面向生产的关键能力债，但有完整蓝图可移植。若项目目标含真实目标扫描，**紧急度上调至中-高**；若仅靶场，可降优先级。
3. **AZ-4（DELETE 方法 prompt 优化）**：低难度、两项目通用，可作为快速 win。
4. AZ-3 / AZ-5 / AZ-6：低紧急度，按需排期。

### 7.2 一句话总结

**重构的越权分析在开放注册靶场（Juice Shop）上实测效果已达实用水平（15/16 EXPLOITED，含双账户对照证据），不存在系统性检出缺陷。** 真正的 gap 集中在 **面向生产/封闭注册目标的多账户能力债（AZ-1）** 和 **攻击链报告置信度（AZ-2，与 route-binding 共担）**，属"工程质量/生产可用性"层面，而非"检出能力退化"。原始项目相对的优势主要是"有设计蓝图"（多账户），但蓝图本身也未落地。

---

## 8. 关键证据索引

### 8.1 重构（实测产出 + 代码）

| 证据 | 路径 |
|---|---|
| 静态 vuln 产出（16 条） | `workspaces/juice-shop_whitebox-1780587584138/deliverables/authz_exploitation_queue.json` |
| 动态 exploit 证据（15/16 EXPLOITED，含双账户对照；VULN-08 未确认） | `workspaces/juice-shop_whitebox-1780587584138/deliverables/authz_exploitation_evidence.md` |
| vuln-authz prompt | `prompts/vuln-authz.txt`（372 行） |
| authz-exploit prompt（含 "≤5 identities" 残留） | `prompts/authz-exploit.txt:169`（427 行） |
| 攻击链构建（无漏洞增强） | `packages/core/src/shannon_core/services/attack_chain_builder.py` |
| 路由链构建（IDOR confidence=probable） | `packages/core/src/shannon_core/services/route_chain_builder.py` |

### 8.2 原始（设计 + 漏报报告 + 代码）

| 证据 | 路径 |
|---|---|
| 多账户设计蓝图（未实现） | `shannon/docs/superpowers/specs/2026-06-14-authz-multi-account-design.md` |
| 越权漏报报告（基线） | `shannon/docs/shannon-xss-authz-missed-vulnerabilities-report.md` |
| 攻击链漏洞上下文增强 | `shannon/apps/worker/src/services/attack-chain-builder.ts:36-52` |
| 单会话共享 | `shannon/apps/worker/prompts/shared/_shared-session.txt`、`audit/utils.ts:81-82` |
| recon §4.2 框架来源标注 | `shannon/apps/worker/prompts/recon.txt`（§4.2） |

### 8.3 交叉参考

- `docs/gap/sink-gap-analysis-v2.md` §2.11/§2.12（SK-14/SK-15）— auth/authz **prompt 文本**对比
- `docs/gap/route-analysis-binding-gap-analysis.md` RA-1/RA-4/RA-9 — 攻击链 confidence / 前端 API 提取 / activity 冗余
- `docs/whitebox-refactoring-assessment.md` — 全维度架构评估（authz 框架 IDOR / 路由级 auth）
