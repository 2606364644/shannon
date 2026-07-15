# 扫描结果症状 → 优化方向映射

> 上一篇 [`fork-scanning-enhancements.md`](./fork-scanning-enhancements.md) 讲「我做了哪些优化、机制是什么」。本文换你的视角：**我在实际扫描某系统时，报告里暴露了什么具体问题（漏报 / 误报 / 高估 / 链路不准），每个问题对应到哪个优化方向，以及——哪些问题 fork 还没有对应的优化（= 你的下一步）。**
>
> 证据来源：`/root/shannon/workspaces/` 下真实 workspace 的 `*_exploitation_queue.json`（端点级 ground truth）+ 报告 + 既往实测审计结论（memory）。

---

## 一、先分清四类「扫描结果问题」

| 类型 | 含义 | 你的体感 | fork 当前重心 |
| --- | --- | --- | --- |
| **漏报（recall）** | 真漏洞在报告里没有 / 没单独出现 | "扫漏了" | ✅ 主战场 |
| **误报（precision）** | 报告里的漏洞其实不存在 / 不可达 | "扫错了" | ❌ 短板 |
| **高估（severity inflation）** | 漏洞在，但严重度 / 可利用性被夸大 | "没说的那么严重" | ❌ 短板 |
| **链路 / 位置不准** | 漏洞对，但触发链或代码位置写错 | "方向对，细节错" | 🟡 部分 |

**一句话结论：fork 的 11 个优化方向几乎全压在「少漏报」+「报告可追溯」，但对「少误报 / 少高估」还缺少系统性方向。** 而你既往审计里最常做的「修正报告」，恰恰是 precision 和 severity 这两类——这正是 fork 下一步该补的。

---

## 二、症状 → 方向 总表

| # | 症状（你在 workspace 里观察到的） | 真实案例 | 对应方向 | 状态 |
| --- | --- | --- | --- | --- |
| S1 | **finding 级**折叠已防住，但**端点级**折叠仍在（enabler finding 把多路由写成 `"ALL routes"` 聚合，附录按 finding 渲染也展开不了） | paper_trading authz 13 端点里 4 个（31%）被塞进 "ALL backend-calling routes"；另一次扫描 91 条→10 条 | 方向 8（finding级✅）+ 方向 2（端点级待强化） | 🟡 finding级已解决，端点级未解决 |
| S2 | 需登录的端点根本扫不到 | 各 OA 系统（test-passport / ghostmode 门控） | **方向 6**（认证预检+会话） | ✅ 已做 |
| S3 | 白盒漏跑某类漏洞 agent | 早期白盒无 xss | **方向 9**（分支覆盖补齐） | ✅ 已补 |
| S4 | 框架/proto/网关隐藏端点没被发现 | question `/service/*` 8 端点、coupon `/guide/*` 11 topic | **方向 1+3**（多角度枚举+框架识别） | 🟡 有效（这俩 case 已覆盖） |
| S5 | 同一 handler 的多条路由只判了一条 | visitor/gateway srpc `method_option_http_api` 注解面 | **方向 2**（cross-route enumeration） | 🟡 机制已加，实测覆盖待持续验证 |
| S6 | 多步攻击链断裂（前端→API→渲染） | question Vue SSTI（Tpl.vue:301）、paper_trading XSS 链 | **方向 4**（知识共享） | 🟡 部分缓解 |
| S7 | **cookie/session 伪造被高估**（keys 硬编码≠可伪造登录态，session 在 Redis） | question AUTH-VULN-01 `app.keys=['crocodile']`、paper_trading AUTHZ-VULN-06、oa-com/performance/annual-meeting/visitor 等 ~10 系统 | 无直接方向 | ❌ **未覆盖 → 下一步** |
| S8 | **summary 与底层分析 deliverable / exploitation queue 打架**：底层已判 SAFE、queue 已剔除，summary 仍报「可利用」 | learn `INJ-VULN-02`（ORDER BY tuple）：底层 analysis 判误报、queue 已剔除，但 findings.md + comprehensive report 仍报「可利用 SQLi」 | 无直接方向 | ❌ **未覆盖 → 下一步** |
| S9 | SSRF / 开放重定向严重度定高 | paper_trading「开放重定向」实测 404 不成立、oa-com download-file SSRF 降 Low | 无直接方向 | ❌ **未覆盖 → 下一步** |
| S10 | XSS「stored / externally exploitable」与自身承认的 self-XSS 矛盾 | question XSS-VULN-01 vs INJ-VULN-04 | **方向 4** 间接 | 🟡 部分 |
| S11 | **同一 workspace 内 deliverable 互相矛盾**（是否跑过 exploitation、finding 编号、被剔除的 finding） | learn：findings.md 写「Exploitation not run」，comprehensive report 却写「Successfully Exploited」；三份文档 finding 编号不一致 | 无直接方向 | ❌ **未覆盖 → 下一步** |

下面挑三个最典型、最有优化空间的案例展开。

---

## 三、典型案例展开

### 案例 A（❌ 未覆盖，最重要）：cookie/session 伪造高估 —— 「keys 硬编码」被直接等价于「可伪造登录态」

**症状事实（question workspace，实测）**：
- 报告 `AUTH-VULN-01`：`app.keys = ['crocodile']` → 判 **Critical**，结论原文「lets any attacker mint a valid OA admin session cookie and fully bypass SSO」。
- 同一颗 report 里**自相矛盾**：
  - recon（`recon_deliverable.md`）和 `auth_analysis_deliverable.md` 都写 session 是 **Redis-backed**；
  - `AUTH-VULN-05` 又把 session 当 ID-based（session fixation）；
  - 但 `AUTH-VULN-01` / `INJ-VULN-03` / `XSS-VULN-01` / `AUTHZ-VULN-16` 这 4 个高危 finding **全部采用「cookie 携带 oa_user JSON body、伪造即控 ctx.user」模型**，从不 reconcile「session 其实在 Redis」。
- **技术真相**：koa-session 配了外部 store 时，cookie 只携带 externalKey（签名），session body 存在 Redis。伪造 Keygrip 签名只能让你提交任意 externalKey，`store.get(key)` 返回的还是 Redis 里那条，**不是攻击者控制的 JSON**。所以「伪造 cookie→admin」在 Redis-store 模型下不成立。

**为什么这是「高估」而非「误报」**：keys 硬编码是真问题（CSRF token 伪造、签名弱都成立），但「直接 = 完整绕过 SSO 登录态」是跳步了。对照 **coupon** `AUTH-VULN-04`：同样 keys 硬编码，但报告**诚实**地只判 CSRF 伪造、明确写「ctx.user is gateway-injected，koa-session 是 dead config」——**没高估**。所以问题不是普遍能力差，而是 question 这一次 **没有把 session 存储方式 reconcile 进判定**。

**同样的症状在多个系统复现**（既往审计）：oa-com `AUTH-VULN-01`、performance、annual-meeting、visitor、staff-punch、learn、paper_trading `AUTHZ-VULN-06`（该 finding 自己都标了 `confidence: MED`，承认「@futu/web-login may re-validate against SSO gateway, mitigating pure cookie tampering」，却仍作为 Critical enabler 提出）。

**对应优化缺口**：方向 3（端点安全上下文）要求记录「所有权校验是否存在」，但**没有要求记录 / reconcile session 存储类型**（RedisStore vs cookie-store）。→ 见第四节建议 1。

---

### 案例 B（🟡 部分解决）：finding 级折叠已防住，端点级折叠没防住

**症状事实（paper_trading workspace，实测）**：
- `authz_exploitation_queue.json`：**7 个 finding、13 个去重端点**（`GET|POST /paper-trade/common-api`、`/fego-common-api`、`/task-common-api`、`/ca/competition/fego-api` 等），其中 `AUTHZ-VULN-06`（session 伪造 enabler）和 `AUTHZ-VULN-07`（header 伪造 enabler）是**跨全部后端调用路由**的横向 finding。
- **finding 级折叠已被方向 8 防住**：Appendix A（`affected-endpoints-appendix.ts` 确定性渲染，按 finding 一行）把 authz 全部 7 条 finding 都保住了，LLM 摘要不丢 finding。
- **但端点级折叠仍在**：13 个去重端点里 **4 个（`GET /paper-trade/api-get-author`、`GET /paper-trade/api/current-ip-province`、`POST /paper-trade/api/get-short-url`、`GET /papertrading`，约 31%）没有独立 finding**，只埋在 `AUTHZ-VULN-06/07` 两条 `endpoint="ALL backend-calling routes"` 聚合 finding 的 `Notes.affected_routes` 小字里；Appendix A 因「按 finding 渲染」也**只是忠实复制了这个聚合，没把子端点展开**。`INJ-VULN-08` 同样把 `api-trade-search`/`api-quote-basic`/`api-quote-kline` 折叠进 CRLF enabler。
- 既往另一份扫描里 authz queue 有 **91 条**端点级 finding、报告只列约 **10 条**。

**为什么是真问题**：报告被 LLM 折叠后，端点级 ground truth 丢失；你每次人工复核都得回去翻 `*_exploitation_queue.json`。

**对应方向**：方向 8 解决了**上半段**（finding 级不丢）。但**下半段没解决** —— 端点级折叠发生在 queue 上游：vuln agent 把多条路由塞进一条 enabler finding、`endpoint` 写成 `"ALL backend-calling routes"` 聚合串。要堵住它得**强化方向 2**（cross-route enumeration）：让 enabler finding 也必须把每个 affected route 拆成独立条目、`endpoint` 禁止聚合，并让 Appendix A 改为「按去重端点一行」渲染。→ 见第四节建议 0。

---

### 案例 C（🟡 印证有效）：框架 / proto 注解隐藏端点被覆盖

**症状事实（实测）**：
- question 的 `/service/*` 匿名 RPC 面（8 个端点：getAnswer / getTemplateDetail / getTemplateList / checkStatus / submitAnswer / clientGetUserSubmitAnswer / workflow/finish / workflow/cancel）**全部被发现并标为 pre-auth**，跨 AUTH-VULN-02/03/04/08 与多个 AUTHZ finding。
- coupon 的 `/guide/<topic>?brokerage=` pre-auth XSS **11 个 topic 全枚举**（fee/fund/cash/trial/...）。

**为什么值得说**：这类端点不在显式的 `router.get` 里（question 走 RPC dispatcher、coupon 走框架路由 + 网关），正是历史上最容易漏的。能被覆盖到，说明**方向 1（多角度枚举：路由定义/控制器/接口契约/proto 注解/前端调用/网关五层）+ 方向 3（框架自动端点识别）** 在起作用。

**对应方向**：方向 1 + 3。状态 🟡 —— 机制有效，但覆盖度仍取决于每次 recon 是否真的把 5 个角度都跑齐（`_enumeration-completeness.txt` 的 `true-miss` 清零约束），偶发遗漏仍可能。

---

### 案例 D（❌ 未覆盖）：summary 与底层 deliverable 打架 —— learn 的 ORDER BY SQLi

**症状事实（learn workspace，实测 + 源码核验）**：
- `INJ-VULN-02`（`AdminController.getCoursePlanOfflineList` 的 ORDER BY tuple `[Model, sort, sortOrder]`）：
  - `injection_findings.md` + `comprehensive_security_assessment_report.md` **两份 summary 都报「可利用 SQLi」**；
  - 但同包 `injection_analysis_deliverable.md` 明确判 **SAFE**（Sequelize v5.16 反引号包裹标识符 + direction 白名单中和注入），`injection_exploitation_queue.json` **已剔除**该条；
  - 源码核验：`AdminController.ts:1294/1305` 代码和行号都真实存在 —— 这不是「文件不存在」型误报，而是 **「代码在、不可利用，summary 却报成可利用」型高估，且 summary 与底层 deliverable 直接打架**。
- 元症状：`injection_findings.md` 顶部写「Exploitation phase was not run」，comprehensive report 却写「Exploitation: enabled」且把 INJ-01..06 列入「Successfully Exploited」—— 两份 deliverable 对「到底跑没跑 exploitation」自相矛盾；三份文档 finding 编号还不一致（queue 丢掉了 ORDER BY SQLi / Redis / 存储 SSRF 并重新编号）。
- 对照修正：`statement_template_svr` 实测给出**正确的「无 injection」结论**（findings.md + 报告均明写无 SQLi），**不是**误报案例（既往「文件不存在」的说法指 `query_template_data.go`，但报告从未引用它）。

**为什么是真问题**：复核者读 summary 看到「可利用 SQLi」，但底层 analysis 早已判 SAFE、exploit queue 也剔除了 —— 这类**内部不一致**比单纯误报更伤可信度，不同 deliverable 各说各话。

**对应优化缺口**：渲染 summary / comprehensive report 前，没有一步「与底层 analysis_deliverable + exploitation_queue 对账」的确定性校验。→ 见第四节建议 2。

---

## 四、针对「你发现的问题」的下一步优化建议

把未覆盖 / 未完全覆盖的症状翻译成可做的优化（按性价比排序）：

### 建议 0（堵漏最快，确定性规则）：enabler finding 禁止聚合端点
- **针对症状**：S1 的端点级折叠（paper_trading 4/13 端点被塞进 `"ALL backend-calling routes"`）。
- **做法**：(1) vuln agent 写 queue 时，`endpoint` 字段禁止写成 `"ALL ... / ALL routes"` 聚合串 —— 跨路由 enabler 必须在 `affected_routes` 里枚举每一个 `METHOD /path`；(2) Appendix A 的渲染单元从「按 finding 一行」改为「按去重端点一行」，让每个端点都有独立行。
- **价值**：把方向 2（cross-route enumeration）的「路由展开」从 finding 正文延伸到 queue 结构与附录，堵住方向 8 没覆盖的下半段。改动小、纯确定性、立竿见影。

### 建议 1（高价值，影响 ~10 个系统）：session 存储类型 reconcile，卡住 cookie 伪造高估
- **针对症状**：S7（影响 ~10 个系统的 cookie 伪造高估）。
- **做法**：在方向 3 的「端点安全上下文」里强制多记一项 **session 存储类型**（RedisStore / 内存 / cookie-store / 网关注入）。渲染 finding 前加一条确定性规则：**当 session 走外部 store（Redis/DB）时，禁止把「app.keys 硬编码」直接推导为「可伪造任意登录态」**，最多判到 CSRF / 签名弱。
- **配套**：加一个「同一系统内 finding 模型一致性」检查 —— 若 recon 写 Redis-backed，则 cookie-body 类 finding 必须显式 reconcile 或降级。

### 建议 2：summary 与底层 deliverable / exploitation queue 的一致性校验
- **针对症状**：S8 + S11（learn INJ-VULN-02：底层 analysis 判误报、queue 已剔除，但 findings.md + comprehensive report 仍报「可利用 SQLi」；且两份 summary 对「是否跑过 exploitation」自相矛盾）。
- **做法**：(1) 渲染 summary / comprehensive report 前，对每条 finding 校验它是否仍在 `*_analysis_deliverable.md`（底层判定）与 `*_exploitation_queue.json`（下游）里 —— 底层判 SAFE 或 queue 已剔除的，summary 必须同步降级 / 移除；(2) 顺带对 `vulnerable_code_location` 的 `文件:行号` 做存在性检查（agent 实测 learn 的行号是准的，但这是便宜的双重保险）。
- **价值**：消灭「summary 与底层 deliverable 打架」这类最伤可信度的内部矛盾 —— 复核者一眼能发现的不一致。

### 建议 3：严重度 / 可利用性 二次校准（exploit 阶段反哺）
- **针对症状**：S7、S9、S10（开放重定向实测 404、SSRF 降 Low、self-XSS 被标 stored）。
- **做法**：exploit 阶段已实测的结果（`externally_exploitable`、witness 是否真的打成功）回写到 finding，**作为严重度的硬约束**：实测打不通的，无论代码看起来多严重，强制降级或移入附录。当前 exploit 结果与 vuln 阶段严重度是松耦合的，应该收紧。

---

## 五、怎么用这张表

1. **下次扫描后复核报告时**，先按第二节四类给每个问题贴标签（漏报 / 误报 / 高估 / 链路）。漏报类大概率已被某方向覆盖（查 S1–S6）；误报 / 高估类（S7–S9）是目前 fork 的空白区。
2. **要决定下一个优化做什么**，直接看第四节三条建议 —— 它们各自绑定了你已经在多个系统里反复发现的具体症状，不是凭空设计。
3. **判断一个优化有没有效果**，去对应 workspace 的 `*_exploitation_queue.json` 看端点级真相（像案例 B/C 那样），而不是只看被折叠过的最终报告。
