# 认证审计效果差距分析

> 对比原始 Shannon（TypeScript, `/Users/mango/project/shannon-refactor/shannon`）与重构 Shannon-py（Python）在 **认证（Authentication / AuthN）安全审计效果**上的差距。
>
> **聚焦口径**：效果/效能——认证漏洞的检出广度与深度、动态验证（exploit）证据硬度、端到端实测闭环。刻意避开 `sink-gap-analysis-v2.md` §2.11 已覆盖的 auth **prompt 文本逐行对比**，也避开 `2026-06-21-vuln-agent-gap-analysis.md` 已覆盖的 vuln agent **结构对齐**（类型注册/schema/exploit 数量）。
>
> **AuthN vs Authz**：本文是 [`authz-effect-gap-analysis.md`](authz-effect-gap-analysis.md) 的姊妹篇。authz 回答"你能做什么"（IDOR / 越权 / 角色提升），auth 回答"**你是谁**"（登录 / 会话 / 令牌 / 密码 / 找回 / OAuth）。两者方法论、schema、exploit prompt 完全不同，本文专论 auth。
>
> **数据来源**：① 逐文件代码核验（prompt / schema / 注册 / 执行 / 调度）② 共享历史扫描产出（`workspaces/juice-shop_whitebox-1780587584138/`）③ 原始 Shannon 独立 NodeGoat 扫描（`shannon/workspaces/NodeGoat_whitebox-1780678757791/`）。所有数字经脚本核验，非 agent 转述。
>
> **日期**：2026-06-21

---

## 0. 与现有 gap 文档的关系定位

本文档**不重复**以下已有结论，仅在必要时交叉引用：

| 现有文档 | 已覆盖维度 | 本文档关系 |
|---|---|---|
| `sink-gap-analysis-v2.md` §2.11 | auth **prompt 文本**逐行对比（方法论层确定性/专家层差异） | **不重复**。本文聚焦 prompt 之外的**运行时效果与实测检出** |
| `2026-06-21-vuln-agent-gap-analysis.md` | auth 类型的**结构对齐**（枚举 / 注册表 / schema 字段 / exploit 数量 / queue 契约） | **引用**。结构已证明"逐维对齐"，本文在其之上回答"对齐之后效果如何" |
| `authz-effect-gap-analysis.md` | 越权（authz）效果 | **姊妹篇**。共享"juice-shop 数据两项目同源"的口径困境（见 §2.0） |
| `route-analysis-binding-gap-analysis.md` | 攻击链 confidence / 路由绑定 | **引用** RA-1（认证攻击链同样受 confidence 不升级影响） |

**核心问题**：现有文档要么从"prompt 文本"角度、要么从"结构对齐"角度对比 auth，缺一份从"**实际认证漏洞能不能检全、检准、动态证死**"角度的对比。本文填补这个空白。

---

## 1. 认证审计的三层能力框架

两项目的认证审计都是 **白盒静态（vuln-auth）+ 黑盒动态（auth-exploit / exploit-auth）** 两阶段，外加一层**预登录前置（validate-authentication）**。下表对比三层的实际效能。

| 层 | 原始 Shannon (TS) | 重构 Shannon-py | 效果差距定性 |
|---|---|---|---|
| **① 静态分析 vuln-auth**（纯代码白盒） | 9 节认证检查清单（Transport / Rate-limit / Session cookie / Token / Fixation / Password / Login response / Recovery / OAuth），code 审查强制委托 Task Agent | 方法论**逐字对齐**（`vuln-auth.txt` 262 行 vs 原始 266 行），9 节清单完全一致；额外 include `_static-dataflow-hints.txt` | **基本持平**（详见 §3） |
| **② 动态利用 auth-exploit**（live HTTP/playwright） | `exploit-auth.txt`（423 行），Level 1–4 证据体系，**Level 3+ 才算 EXPLOITED**；隐式 impersonation（访问 `/profile` 验证受害者身份） | `auth-exploit.txt`，Level 1–4 **逐字对齐**；显式 Stage 2 victim/attacker 模型（"证明你已成为另一个用户"） | **机制对齐，victim 模型更显式**（详见 §4） |
| **③ 预登录前置 validate-authentication** | credential preflight，登录成功后 `state-save` 复用 session（`session-manager.ts:178` 独占 agent1） | 同用途 preflight，`publish_session` 存 `AUTH_STATE_FILE`；`prerequisites=[]` 无依赖 | **完全对齐** |

**裁决**：认证审计的**方法论与流水线架构两项目对齐**。真正的差距不在"认证检查清单是否完整"，而在三处——① 重构 shared 片段对认证的**概念错配**（§5）；② **端到端实测验证缺口**（§2.3，重构无独立 auth 扫描产出）；③ auth-exploit **依赖声明**弱于原始（§3.4）。

---

## 2. 真实产出对比 ★

> ⚠️ **口径警示（关键）**：与 authz 文档面临同样的困境——**juice-shop 数据两项目逐字节相同**（`diff` 确认 queue + evidence 完全一致），是重构前共享的历史扫描，**不可作两项目独立对比**，仅作"Shannon 体系在 Juice Shop 上的认证检出水平"参照。真正的独立对比基线只有原始 Shannon 的 NodeGoat 扫描——而**重构没有同靶场的独立 auth 产出**。

### 2.1 共享数据：Juice Shop（体系能力参照，非两项目对比）

`workspaces/juice-shop_whitebox-1780587584138/deliverables/`（两项目 diff 为空）：

| 产出 | 内容 | 核验 |
|---|---|---|
| `auth_exploitation_queue.json` | **24 条 AUTH-VULN**，全 `externally_exploitable=true` | High 22 / Medium 2 |
| `auth_exploitation_evidence.md`（775 行） | **20 条 Successfully Exploited + 1 条 Potential**（VULN-20 OAuth CSRF 需真实 IdP） | 另 3 条（08/18/24）未进 evidence |

**类型分布**（24 条）：

| vulnerability_type | 计数 | 典型代表 |
|---|---|---|
| Login_Flow_Logic | 6 | 硬编码 `admin:admin123`（14）、GET 改密口令入 URL（17）、注册 mass-assignment `role:admin`（22） |
| Abuse_Defenses_Missing | 6 | 登录无 rate limit（02）、rate-limit key 用 `X-Forwarded-For` 可绕过（06） |
| Token_Management_Issue | 5 | RSA-1024 私钥硬编码（10）、express-jwt@0.1.3 接受 `alg:none`（11）、无盐单轮 MD5（13） |
| Session_Management_Flaw | 2 | Cookie 无 HttpOnly/Secure/SameSite（01）、无服务端 logout JWT 6h 恒有效（09） |
| Transport_Exposure | 2 | 明文 HTTP 无 TLS/HSTS（07）、auth 响应无 `Cache-Control: no-store`（08） |
| OAuth_Flow_Issue | 2 | 无 state/PKCE/nonce（20）、OAuth 密码=`btoa(reversed(email))` 可接管（21） |
| Reset_Recovery_Flaw | 1 | HMAC 密钥硬编码可离线计算 reset token（19） |

**动态验证证据硬度**（evidence，20 条 EXPLOITED）——全部为**可复现 cURL + HTTP 状态码 + 响应体关键字段**：
- **HTTP 200 绕过证明**：forged JWT 返 200 + 28 用户（VULN-10）、`alg:none` 返 200（11）、admin 注册返 200（22）、OAuth 账户接管（21）
- **状态码序列证明**：前 100 个 401、第 101 起 429；rotating IP 全 401 证明 rate-limit 绕过（06）；50 次失败全 401 无锁定（02/24）
- **链式利用**：JWT→MD5→crack 出 `admin123`（13）；JWT 解码拿 password hash（12）

### 2.2 原始 Shannon 独立产出：NodeGoat

`shannon/workspaces/NodeGoat_whitebox-1780678757791/deliverables/`（**原始独有**，重构无对应）：

| 产出 | 内容 | 核验 |
|---|---|---|
| `auth_exploitation_queue.json` | **17 条 AUTH-VULN**，全 `externally_exploitable=true` | High 15 / Medium 2 |
| `auth_exploitation_evidence.md`（568 行） | **10 条 Successfully Exploited + 2 条 Potential** | 另 5 条（09/12/14/15/16）未进 evidence |

**类型分布**（17 条）：Token_Management_Issue 5 / Session_Management_Flaw 4 / Abuse_Defenses_Missing 3 / Login_Flow_Logic 3 / Transport_Exposure 2。**无 OAuth / Reset_Recovery / Authentication_Bypass**（NodeGoat 应用本身无 OAuth/找回流程，非漏检）。

**动态验证亮点**（含 2 条 Level 4 account takeover 实证）：
- **默认凭据接管**（VULN-13）：cURL POST `/login` → 302 + `Set-Cookie: connect.sid`，用 cookie 取全员 PII
- **Session fixation 全链路**（VULN-08/09）：取 pre-auth cookie → 受害者同 cookie 登录 → 攻击者复用同 cookie 200→dashboard + `/profile` 显示受害者 `lastName="Doe"`（完整 Level 4 实证）
- **暴力破解**（VULN-03）：Python 10 线程，87 次 0.12 秒（729.5 req/s）命中 `Admin_123`，全程无 429
- **诚实降级**（VULN-01/06）：明文 HTTP / 无 Secure flag 需 MITM，测试位于外部网络无中间设施 → 降级 Potential，不夸大

### 2.3 重构 Shannon-py：**无独立 auth 端到端产出** ★★

这是本文最重要的发现之一。重构近期 4 次 NodeGoat 独立扫描全部**未触达 auth agent**：

| workspace | deliverables 内容 | 阶段 |
|---|---|---|
| `NodeGoat_shannon-1782041072350` | 空 | 未产出 |
| `NodeGoat_shannon-1782041653865` | `code_index.json` + `code_index_summary.md` | 停在 code index |
| `NodeGoat_shannon-1782041779805` | 同上 | 停在 code index |
| `NodeGoat_shannon-1782053569103`（最新，06-21） | 同上 | 停在 code index |

**含义**：这些是 pre-recon / recon 阶段的冒烟运行（对应 memory 中"白盒 live 显示重设计""Provider 逐轮日志"等近期改动的验证），**不是完整白盒流水线**，因此未跑到 vulnerability-analysis 阶段的 auth-vuln。

**定性**：这是**验证缺口，不是能力退化**。重构 auth agent 的代码与 prompt 完整（§3 证明），shared juice-shop 数据也证明"Shannon 体系（重构前的同一套 prompt 演化）在 Juice Shop 上能检出 24 条认证漏洞"。但**重构后至今没有一次跑完 auth 阶段的独立实测**来确认重构本身没有引入回归。这与 authz 文档"15/16 EXPLOITED"的实测背书形成对比——authz 至少在 juice-shop 数据上验证过，auth 连这点独立背书都没有。

### 2.4 对比裁决

- **广度（参照 juice-shop）**：Shannon 体系的认证审计能覆盖 7 大类（Login/Abuse/Token/Session/Transport/OAuth/Reset），24 条全可外部利用，广度达到实用水平。
- **深度（NodeGoat 独立）**：原始能产出 Level 4 account takeover 实证（默认凭据、session fixation 全链路），证据为可复现 cURL，不是理论推演。
- **不对称**：**原始有独立 NodeGoat 完整 auth 实测，重构没有**。不能据此说"重构认证审计更弱"——但能说"**重构的认证审计能力未被端到端实测验证**"，这是面向交付的实质风险。

---

## 3. prompt 方法论与 schema 对齐度

### 3.1 vuln-auth 9 节方法论：逐字对齐

两项目 `vuln-auth.txt` 的核心 `<methodology>`（§1 Transport → §9 OAuth，含 confidence scoring + false_positives_to_avoid）**逐字一致**。认证检查清单完整度无差距。

**两项目共有的历史残留**：`vuln-auth.txt:36`（两版均有）"Finding one IDOR is merely the first data point"——这是认证 prompt 里残留的**越权（IDOR）用语**，系 auth/authz prompt 从同一模板派生的历史遗留，不影响功能，属文本瑕疵。

### 3.2 AuthVulnerability schema：字段完全对齐，均为弱约束

| 子维度 | 原始 Shannon (Zod) | 重构 Shannon-py (Pydantic) | 定性 |
|---|---|---|---|
| 定义位置 | `queue-schemas.ts:66-72` | `queue_schemas.py:35-40` | 对应 |
| 专有字段 | `source_endpoint` / `vulnerable_code_location` / `missing_defense` / `exploitation_hypothesis` / `suggested_exploit_technique` | **同 5 字段** | ✅ 完全对齐 |
| required 字段 | 仅 `ID` / `vulnerability_type` / `externally_exploitable` / `confidence` | 同 | 对齐 |
| 5 个专有字段是否 required | **全 optional** | **全 optional** | 🟡 两项目共有：schema 不强制填写 missing_defense 等 |
| `vulnerability_type` 约束 | `z.string()`（**无 enum**）`queue-schemas.ts:30` | 开放 `str`（**无 Literal**）`queue_schemas.py:7` | 🟡 两项目共有：8 类枚举仅 prompt 约定，schema 不校验 |
| 与 authz schema 分离 | ✅（字段集完全不同，见 vuln-agent-gap §2.4） | ✅ | 对齐 |

**auth vs authz schema 设计差异**（佐证"认证非越权"定位）：auth 用 `missing_defense` + `exploitation_hypothesis`（关注"缺什么防御 + 怎么打"），authz 用 `role_context` + `guard_evidence` + `minimal_witness`（关注"角色对照 + 最小可复现"）。auth **无** `role_context`/`minimal_witness`。

### 3.3 exploit prompt：动态验证标准对齐

| 子维度 | 原始 `exploit-auth.txt`（423 行） | 重构 `auth-exploit.txt` | 定性 |
|---|---|---|---|
| 证据级别 | Level 1–4（`:52-70`） | Level 1–4（`:52-70`） | ✅ 逐字对齐 |
| 成功门槛 | Level 3+ 才 EXPLOITED（`:70`） | 同 | ✅ |
| Mandatory Checklist | 账户接管 / 认证绕过 / 逻辑缺陷 三选一（`:218-222`） | 同（`:167-171`） | ✅ |
| Bypass Exhaustion | 强制穷尽多种绕过技术（`:226-232`） | 同（`:173-181`） | ✅ |
| victim/attacker 模型 | **隐式** impersonation（访问 `/profile` 验证受害者，`:214-216`） | **显式** Stage 2"证明你已成为另一个用户"（`:163-165`） | 🟢 重构更显式 |
| 频率约束 | 不设上限，鼓励暴力（`:87`） | 同（rate-limit 是障碍非 FP，`:213`） | ✅ |
| 覆盖攻击 | session hijack / credential / logic / JWT `alg:none`（`:284-309`） | 同 + rate-limit key 绕过（X-Forwarded-For） | 🟢 重构略广 |

**核心定位原文**（两版精神一致，原始 `exploit-auth.txt:78-79`）：
> "Focus on the Gate, Not the Rooms: Your sole responsibility is to break the lock on the door (authentication). What lies in the rooms beyond (authorization) is out of scope."

### 3.4 exploit agent 依赖声明：真 gap 🟡

| 维度 | 原始 | 重构 |
|---|---|---|
| auth-exploit prerequisite | `['auth-vuln']`（`session-manager.ts:89`，**显式依赖**） | `[RECON]`（`agents.py:110`，**未声明 auth-vuln**） |
| queue 获取方式 | 显式依赖保证 auth-vuln 先跑产出 queue | prompt **硬编码**读 `auth_exploitation_queue.json`（`auth-exploit.txt:115-121`），**软依赖** |

**影响**：重构下若 auth-vuln 失败/被跳过，auth-exploit 仍会被调度，读不到 queue → 空跑或报错。原始的显式 prerequisite 在调度层保证了顺序。属**调度健壮性 gap**（见 AU-2）。

---

## 4. 动态验证效能

### 4.1 Level 3+ 证据门槛：两项目一致且被实测兑现

两项目 exploit prompt 都要求"必须以未授权身份访问到受保护功能（Level 3）或完成账户接管（Level 4）"才算 EXPLOITED，仅 Level 1/2（理论/部分绕过）不算。NodeGoat evidence 的 session fixation 全链路（pre-auth cookie → 受害者登录 → 攻击者复用拿 `/profile` PII）是 Level 4 的典范兑现。

### 4.2 victim/attacker 身份对照：与 authz 同源的单账户困境

与 [`authz-effect-gap-analysis.md`](authz-effect-gap-analysis.md) §3 同源：两项目 config 的 `authentication` 仅一组凭据，全流水线共享单一 session（原始 `audit/utils.ts:81-82`，重构同等架构）。auth exploit 的 victim 身份同样依赖 agent **现场注册**（开放注册靶场奏效）或 `LOGIN_INSTRUCTIONS` 注入的单账户——**无 operator 供给的多身份三角**。

**对认证的影响**：认证审计的多数检查（cookie flag / rate limit / JWT 弱点 / 密码策略）**不依赖多账户对照**（单账户即可观测 Set-Cookie 属性、暴力破解响应、解码 JWT），故单账户困境对 auth 的拖累**远小于 authz**（authz 的水平 IDOR 必须双账户对照）。这是 auth 相对 authz 的一个结构性优势——多账户能力债（AZ-1）对 auth 几乎不适用。

### 4.3 诚实降级机制：两项目共同的优点

认证审计有一类固有难点：**传输层漏洞（明文 HTTP / 缺 Secure flag）需要 MITM 才能动态证明**。两项目 exploit prompt 都允许将其标为 `Potential / Validation Blocked` 而非强行 EXPLOITED（NodeGoat VULN-01/06 即如此）。这是**避免假阳性、保持证据诚实**的正确机制，两项目均有，属共性优点。

---

## 5. shared 片段错配（重构特有，认证维度质量隐患）★

重构在统一 vuln prompt 模板时，把多个 shared 片段机械 include 进 `vuln-auth.txt`，但其中两个对"认证审计"存在**概念错配或字段不匹配**——这是重构相对原始在 auth 维度独有的质量隐患。

| 片段 | 被 vuln-auth include? | 对认证审计的适用性 | 定性 |
|---|---|---|---|
| `_static-dataflow-hints.txt` | 重构 **是**（`vuln-auth.txt:43`）；原始 **否** | **概念错配**。该片段讲 source→sink 污点分析、sanitizer、concat-after-sanitize——本质是**注入**方法论。认证审计是逐端点清单式逻辑检查（HTTPS / rate limit / cookie flag / JWT），**不是污点流**。对 auth 几乎无适用场景，可能误导 agent 把认证当污点问题处理 | 🟡 AU-1 |
| `_cross-route-enumeration.txt` | 两项目**均** include（重构 `:45`；原始 `:178`） | **字段不匹配**。该片段要求每条 finding 列 `affected_routes[]` + `authentication_required`。但 **AuthVulnerability schema（两项目）根本没有这两个字段**——片段要求的字段无处落盘。主要服务 authz（按路由组枚举越权） | 🟡 AU-3（两项目共有） |
| `_endpoint-security-context.txt` | 两项目**均否**（vuln-auth 未引用） | 讲 finale-rest 自动端点 + 所有权校验，核心服务 IDOR/authz。vuln-auth 不 include 它是**合理**的 | ✅ 正确取舍 |

**一个反转**：原始 `vuln-auth.txt` 的 `conclusion_trigger`（`:261`）曾要求"每条 finding 必须列 `affected_routes` 并 cross-route 校验"（Cross-Route Verification）——但原始 schema 同样无此字段，这条要求是**悬空的**（prompt 要求了 schema 不支持的字段）。重构**删除了这条 conclusion_trigger 要求**（`vuln-auth.txt:245-262` 无 Cross-Route Verification），反而**消除了 prompt-schema 不一致**。故重构在这一点上**更自洽**，不算 gap。

---

## 6. 两项目共有缺陷（非重构独有）

| # | 缺陷 | 根因 | 影响 | 证据 |
|---|---|---|---|---|
| C-1 | **`vulnerability_type` 无 schema 级 enum** | 两项目均为开放 string，8 类枚举仅 prompt 约定 | agent 可能产出枚举外的类型值（如拼写变体），下游 queue 消费/统计脆弱 | `queue-schemas.ts:30` / `queue_schemas.py:7` |
| **C-2** | **5 个专有字段全 optional** | schema 不强制 `missing_defense` / `exploitation_hypothesis` 等 | 可能产出"有空 ID 无实质内容"的 finding；下游 exploit agent 拿不到 `exploitation_hypothesis` 难以武器化 | `queue_schemas.py:35-40` |
| C-3 | **queue 部分条目未进 evidence** | exploit 阶段未穷尽所有 queue 项 | 闭环缺口：juice-shop 24 条中 3 条（08/18/24）、NodeGoat 17 条中 5 条未动态验证 | §2.1/§2.2 核验 |
| C-4 | **认证 prompt 残留越权用语** | "Finding one IDOR..." 模板派生遗留 | 文本瑕疵，不影响功能 | `vuln-auth.txt:36`（两版） |

**裁决**：C-1/C-2 是 schema 弱约束，两项目共有，可通过给 `vulnerability_type` 加 Literal/enum、关键字段 required 一并解决。C-3 是 exploit 闭环问题，两项目均有，影响"动态证死率"。

---

## 7. 差距矩阵 + 严重度/紧急度评估

> **严重度** = 对认证审计效果（检出能力 / 证据质量 / 交付可信度）的影响；**紧急度** = 结合"重构无独立实测"的现状判断。

| # | 差距项 | 原始 | 重构 | 对效果的影响 | 严重度 | 紧急度 | 修复难度 | 建议 |
|---|---|---|---|---|---|---|---|---|
| **AU-1** | **重构无独立 auth 端到端实测** | 有 NodeGoat 完整产出（17 queue + 12 evidence） | 4 次 NodeGoat 全停在 code_index，**无 auth 产出** | 重构 auth 能力**未被实测验证**，回归风险不可见 | **中-高** | **高** | 低（跑一次完整白盒） | **立即补一次重构完整白盒扫描**（NodeGoat 或 juice-shop live），确认 auth-vuln→auth-exploit 全链路无回归。最高性价比 |
| **AU-2** | auth-exploit 依赖声明弱 | `prerequisite=['auth-vuln']` 显式 | `prerequisite=[RECON]` 软依赖 | auth-vuln 失败时 auth-exploit 空跑/报错 | **中** | 中 | 低（agents.py 改一行 prerequisite） | 把 `AUTH_EXPLOIT.prerequisites` 改为 `[AUTH_VULN]`（或 `[RECON, AUTH_VULN]`） |
| **AU-3** | `_static-dataflow-hints` 认证概念错配 | vuln-auth 未 include | vuln-auth include 了污点分析片段 | 可能误导 agent 把认证当污点问题；噪音 | **低-中** | 低 | 低（vuln-auth.txt 删一行 include） | 从 `vuln-auth.txt:43` 移除该 include（污点提示只服务于 injection） |
| **AU-4** | `vulnerability_type` 无 enum（C-1） | 共有 | 共有 | 类型值不受校验，下游脆弱 | **低** | 低 | 低（加 Literal/enum） | 两项目通用改进，给 schema 加枚举 |
| **AU-5** | 专有字段全 optional（C-2） | 共有 | 共有 | 可能产出空内容 finding | **低** | 低 | 低 | `missing_defense` / `exploitation_hypothesis` 设为 required |
| **AU-6** | queue→evidence 闭环缺口（C-3） | 共有 | 共有 | 部分认证漏洞未动态证死 | **低-中** | 低 | 中（exploit 穷尽性） | exploit prompt 强化"每条 queue 必须追到结论"（已有，执行层面） |
| AU-7 | 攻击链 confidence 不升级（RA-1） | ✅ SharedKnowledge 增强 | ❌ 缺失 | 认证攻击链报告 confidence 永远 probable；不降检出 | **中** | 中 | 中 | **与 route-binding RA-1 合并修**，一份 Spec 同时解决 auth/authz 链 |

### 7.1 优先级建议（性价比排序）

1. **AU-1（补独立实测）**：高影响、低难度。重构 auth 至今无端到端背书，这是面向交付的最大风险。**先跑一次再谈其他**。
2. **AU-2（auth-exploit prerequisite）**：中影响、极低难度（改一行）。与 AU-1 同批验证。
3. **AU-7（攻击链 confidence，合并 RA-1）**：中影响、中难度、一份 Spec 解决 auth + authz 两个文档。与 authz AZ-2 共担。
4. **AU-3（删错配 include）**：低-中影响、极低难度，可作快速 win。
5. AU-4 / AU-5 / AU-6：低紧急度，schema 强化可统一排期。

### 7.2 一句话总结

**两项目认证审计的方法论（9 节检查清单）、schema、动态验证标准（Level 3+）逐字对齐，在 Juice Shop 共享数据上体系能力达实用水平（24 条全可外部利用、20 条动态 EXPLOITED）。** 重构相对原始没有"认证检出能力退化"，但存在三个面向交付的实质问题：**① 至今无独立 auth 端到端实测（AU-1，最高优先级）；② auth-exploit 依赖声明弱于原始（AU-2）；③ shared 片段对认证概念错配（AU-3）**。这些是"工程质量/验证闭环"层面，而非"能力缺失"。认证审计相对越权的一个结构性优势：**多数认证检查不依赖多账户对照**，故 authz 的多账户能力债（AZ-1）对 auth 几乎不适用。

---

## 8. 关键证据索引

### 8.1 重构 Shannon-py（代码）

| 证据 | 路径 |
|---|---|
| vuln-auth prompt（9 节方法论 + 错配 include） | `prompts/vuln-auth.txt:43,45,117-190` |
| auth-exploit prompt（Level 1-4 + Stage 2 victim） | `prompts/auth-exploit.txt:52-70,163-165,167-171` |
| validate-authentication prompt | `prompts/validate-authentication.txt` |
| AuthVulnerability schema（全 optional、无 enum） | `packages/core/src/shannon_core/models/queue_schemas.py:7,35-40` |
| agent 注册（auth-exploit prereq=[RECON] 软依赖） | `packages/core/src/shannon_core/models/agents.py:107-113`（:110） |
| agent 执行 + queue 捕获 | `packages/core/src/shannon_core/agents/executor.py:26-126,106-109` |
| 无独立 auth 产出（NodeGoat 停在 code_index） | `workspaces/NodeGoat_shannon-1782053569103/deliverables/` |

### 8.2 原始 Shannon（代码 + 独立产出）

| 证据 | 路径 |
|---|---|
| vuln-auth prompt（conclusion_trigger Cross-Route Verification 悬空） | `apps/worker/prompts/vuln-auth.txt:261` |
| exploit-auth prompt（Level 1-4 + attack_patterns） | `apps/worker/prompts/exploit-auth.txt:52-70,214-216,218-222,284-309` |
| validate-authentication prompt | `apps/worker/prompts/validate-authentication.txt` |
| AuthVulnerability Zod schema（无 enum） | `apps/worker/src/ai/queue-schemas.ts:30,66-72` |
| auth-exploit 显式 prerequisite | `apps/worker/src/session-manager.ts:89` |
| 独立 NodeGoat auth queue（17 条） | `shannon/workspaces/NodeGoat_whitebox-1780678757791/deliverables/auth_exploitation_queue.json` |
| 独立 NodeGoat auth evidence（10 EXPLOITED + 2 Potential，含 Level 4 takeover） | `shannon/workspaces/NodeGoat_whitebox-1780678757791/deliverables/auth_exploitation_evidence.md` |

### 8.3 共享数据（两项目逐字节相同，仅作体系参照）

| 证据 | 路径 |
|---|---|
| juice-shop auth queue（24 条） | `workspaces/juice-shop_whitebox-1780587584138/deliverables/auth_exploitation_queue.json` |
| juice-shop auth evidence（20 EXPLOITED + 1 Potential） | `workspaces/juice-shop_whitebox-1780587584138/deliverables/auth_exploitation_evidence.md` |

### 8.4 交叉参考

- `docs/gap/authz-effect-gap-analysis.md` — 越权效果（姊妹篇；多账户能力债 AZ-1、攻击链 AZ-2 共担）
- `docs/gap/2026-06-21-vuln-agent-gap-analysis.md` — vuln agent 全链路结构对齐（auth 类型注册/schema/exploit 数量）
- `docs/gap/sink-gap-analysis-v2.md` §2.11 — auth **prompt 文本**逐行对比
- `docs/gap/route-analysis-binding-gap-analysis.md` RA-1 — 攻击链 confidence 升级（AU-7 共担）
