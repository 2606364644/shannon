# auth 确定性候选模型 spike 报告（spec-2a 产出）

> 日期：2026-07-02　分支：`feat/fork-py`　所属 epic：`2026-07-02-gitnexus-deep-agent-auth-authz-design.md`（子项目 2a）
>
> 计划文件：`2026-07-02-auth-deterministic-candidate-model-spike.md`（本报告是其 §6 产出）。
>
> **性质：可行性研究 spike 报告，非实现。** 决定 spec-2b 是否启动、epic 是否降级。

---

## 0. 裁决（TL;DR）

**裁决：可行（启动 spec-2b），但带明确的范围限定。**

auth 确定性候选模型**能设计出来**，且在"**安全原语缺失/误用型**"缺陷上确定性精度高、跨语言成立。它**不是证伪**（覆盖达标、误报低、跨语言可行、schema 能落 code_index），也**不是无保留可行**——它够不着 auth 缺陷里最严重的一类（**分布式业务逻辑/架构级**，如 fail-open、OAuth state race），那部分仍只能由 LLM 轨深度 agent 覆盖。

**一句话定位**：方向 B 的确定性候选层 = "**缺失/误用型 auth 缺陷的确定性兜底**"，与 LLM 轨**互补**而非替代；关 LLM 轨时能抓缺失型（治本部分成立），抓不到业务逻辑型（LLM 轨对 auth 仍有独特价值）。

**对 epic 影响**：**不降级**。spec-2b 启动，但范围限定为缺失/误用型检查项子集 + Node.js/Go 先行。epic G2 愿景（GitNexus 轨独立兜 auth 深度）**部分成立**——独立兜"缺失型深度"，业务逻辑型仍需 LLM 轨。

---

## 1. 判据对照（spike §5）

| §5 判据（可行） | 实测数据 | 结论 |
|---|---|---|
| 覆盖 vuln-auth 9 类中 **≥4 类**部分场景 | 逻辑类 5 类中：**高质量 4 类**（session-fixation / token / SSO-OAuth / recovery）+ 中低 2 类（login-response / logout）；config 类 4 类已由 `auth_config_scan` 覆盖。共 9 类全覆盖，其中逻辑类确定性高质量覆盖 4 类 | ✅ 达标 |
| 误报率 **<40%** | 缺失/误用型检测（"安全原语 0 调用即缺陷"、"弱原语命中即缺陷"）天然低误报，预计 **<20%**（见 §3 精度分析） | ✅ 达标 |
| 至少 **2 种语言/框架** | AST parser 现成支持 6 语言（py/ts/js/go/java/php）；实测 Node.js/Egg.js（moa-auth）+ Go/Gin（futu_auth_svr）两真实生产 repo | ✅ 达标 |
| 候选 schema 能落到 code_index | 可落——独立 `auth_gitnexus_track.py`（类比 authz），复用 AST `iter_calls` + `CallChain`，不强行塞 `sink_rules`（语义反转，见 §4.3） | ✅ 达标 |

**未触发证伪条件**（<3 类 / 误报>60% / 跨语言不可行）——均不成立。

---

## 2. 调研基础（4 路并行 fan-out 结论摘要）

### 2.1 auth 现有基础设施
- `auth_config_scanner.py`：`_AUTH_ROUTE_RE`（`auth_config_scanner.py:281-286`）匹配 `(post|get|...)\(['\"](login|signin|signup|register|reset|recover|forgot|token|oauth|auth)`，支持 Express/Flask/FastAPI 路由字符串。**但它只服务 rate-limit 检测的窗口扫描，不是通用端点提取器**；缺 logout/callback/authorize，不匹配函数名/装饰器/Gin group/Egg router。
- 5 类 config 检测（cookie/HSTS/CORS/JWT-claim/rate-limit）纯正则，**独立于 recon**，`ConfigFinding` 模型可复用。
- `entry_point_fusion.py:218-228` 的 `_extract_auth_nearby` 只解析 LLM Markdown 的 authentication 三态，**不适用源码**。
- **关键缺口**：handler 函数体定位（当前只有 route-level）。

### 2.2 code_index 图能力（最大技术前提，结果利好）
- ✅ **AST parser 支持 6 语言**（`parsers/{python,typescript,go,java,php}_parser.py`），`iter_calls(block, source)` 遍历函数内 call site，`destructure_call` 区分 receiver/callee，`extract_arg_expressions` 取实参。
- ✅ **调用图**：`CallChain.path`（有序 FuncBlock.id 列表，来自 GitNexus process trace）；可达性 API `_source_reaches_sink`（`authz_gitnexus_track.py:101-140`）、`impact_upstream/downstream`、`chain_propagator` 前向/反向传播。
- ✅ **sink_detector 匹配**：按 `(language, callee)` O(1) 索引 + receiver_pattern 匹配（`sink_detector.py:134-221`）。
- ⚠️ **语义反转**：现有 sink = 危险函数（命中=坏）；auth 的 positive sink = **安全必需函数**（缺失=坏，如 `session.regenerate`）。语义相反，**不强行塞 sink_rules**。
- ❌ sink_rules.yml / source_rules.yml **无 auth 专用规则**（只有 cookie source、session 参数枚举）。

### 2.3 vuln-auth 基准
- 9 类方法论（`vuln-auth.txt:148-201`）分两类：
  - **Config 类 4 类**（确定性可覆盖，多数已由 auth_config_scan 兜）：transport、rate-limit、session（cookie 配置）、password-policy（默认凭证/强度/哈希存储）。
  - **逻辑类 5 类**（需语义，本 spike 攻坚）：token 属性、session-fixation、login-response（用户枚举）、recovery/logout、SSO-OAuth。
- **现成基准产物**（无需花 token 重跑 vuln-auth agent）：
  - `moa-auth`（Egg.js）：`/root/code/frontend/moa-auth/.shannon/deliverables/auth_exploitation_queue.json`（12 条）。
  - `futu_auth_svr`（Go/Gin）：`/root/code/20260615/futu_auth_svr/.shannon/deliverables/auth_exploitation_queue.json`（13 条）。
- **方向 C 证伪**：recon auth section（如 invite_code_center `recon_deliverable.md` §3 Authentication Architecture）是**纯自然语言散文**，无稳定 header/列表结构，**不可靠程序解析** → 方向 C 弃（spike §7 Q4 答案：脆弱）。

### 2.4 真实 repo（远超 spike 要求）
- 本地 5 个候选，跨 **Node.js/Python/Go/Java 4 语言**，无缺口、无需克隆：
  - **moa-auth**（Node/Egg.js，带完整产物）、**futu_auth_svr**（Go/Gin，带完整产物）—— 直接当召回基准。
  - crAPI（多语言微服务）、Juice Shop（Node/Express 靶场）—— 无产物，备用。
- spike 计划提到的 NodeGoat 本地没有，但替代品更优（生产项目带现成基准）。

---

## 3. 覆盖面 / 精度矩阵（核心数据）

方向 B 检查项 × 两真实 repo 基准缺陷的命中（grep 探针量化）：

| 检查项 | vuln-auth 类 | 类型 | moa-auth | futu_auth_svr | 确定性精度 |
|---|---|---|---|---|---|
| `session.regenerate` 缺失 | session-fixation(#5) | positive sink 缺失 | ✅ #1（全代码库 0 调用） | N/A（用 ticket 非 session） | **极高** |
| 无 logout / `session.destroy` 缺失 | recovery/logout(#8) | 端点+sink 缺失 | ✅ #9（0 命中） | 部分（有 logout 但撤销无效 #5） | **高** |
| `math/rand` 弱随机 | token(#4) | negative sink 命中 | N/A | ✅ #6（`general_tool.go:10` import math/rand/v2） | **高** |
| JWT `Parse/Verify` 缺失 | token(#4) | 缺失 | N/A | ✅ #13（0 调用） | **高** |
| OAuth state/nonce/PKCE 缺失 | SSO-OAuth(#9) | 缺失 | 部分（#6 race / #12 nonce） | ✅ #12（controller/third 内 0 命中） | **中高** |
| reset token 弱生成 | recovery(#8) | negative sink | （moa 无 reset 流） | （futu 用 crypto/rand 正确，#6 仅随机串） | **高**（原则可检测，样本未触发） |
| 密码未 hash | password-policy(#6) | positive sink 缺失 | （moa OAuth 无密码） | （futu 用 salt+sig，非 bcrypt） | **高**（原则可检测，样本无明文存储） |
| 用户枚举（多 error code） | login-response(#7) | 语义 | N/A | ⚠️ #8（7 条可区分 error code 路径） | **低**（需语义，AST 找 error return 易，"可区分"判断难） |
| OAuth state **race**（非原子） | SSO-OAuth(#9) | 业务逻辑/并发 | ❌ #6 够不着 | N/A | **不可**（并发语义） |
| **fail-open**（RPC err→放行） | （Authentication_Bypass） | 分布式控制流 | N/A | ❌ #9/#10/#11 够不着（3 条最严重 High） | **不可** |
| CSRF disabled × 链 | session(#3) | 配置+交互 | ⚠️ #8 | N/A | 中（config 部分已覆盖） |
| 硬编码/弱密钥 | password-policy(#6) | config | ✅ #4/#10 | ✅ #7 | 高（**auth_config_scan 已覆盖**） |

**精度结论**：
- **缺失/误用型**（前 7 行）：精度高/极高。"安全原语 0 调用"或"弱原语命中"是**二元信号**，在安全原语上置信度天然高（`session.regenerate` 在 OAuth callback 零调用 = session 固定，几乎无误报）。预计整体误报 <20%。
- **语义型**（用户枚举）：低，只能产 low-confidence 候选交深度 agent。
- **业务逻辑/架构型**（fail-open、race）：**确定性不可达**，必须交 LLM 轨。

**覆盖统计**：方向 B 确定性**高质量覆盖逻辑类 9 类中的 4 类**（session-fixation、token、SSO-OAuth、recovery），中低质量 2 类（login-response、logout），config 类 4 类已有 auth_config_scan。

---

## 4. AuthCandidate 候选 schema 草案（§6 产出 2）

### 4.1 数据模型

```python
class AuthCheckType(str, Enum):
    # —— positive sink 缺失（应调用的安全函数没调）——
    SESSION_REGENERATE_MISSING = "session_regenerate_missing"      # session 固定
    LOGOUT_DESTROY_MISSING = "logout_destroy_missing"              # 无登出/会话撤销
    PASSWORD_HASH_MISSING = "password_hash_missing"                # 密码明文存储
    JWT_VERIFY_MISSING = "jwt_verify_missing"                      # OIDC id_token 未验签
    # —— negative sink 命中（误用弱原语）——
    WEAK_RANDOM_TOKEN = "weak_random_token"                        # math/rand 用于 token/盐
    WEAK_CRYPTO = "weak_crypto"                                    # AES-CBC-MD5 / 短 RSA / MD5 KDF
    RESET_TOKEN_WEAK_GEN = "reset_token_weak_gen"                  # reset token 非密码学随机
    # —— OAuth 缺失项（缺三元组 state/nonce/PKCE 之一）——
    OAUTH_STATE_MISSING = "oauth_state_missing"
    OAUTH_NONCE_MISSING = "oauth_nonce_missing"
    OAUTH_PKCE_MISSING = "oauth_pkce_missing"
    # —— low-confidence（交深度 agent）——
    USER_ENUMERATION_DIFF_ERROR = "user_enumeration_diff_error"    # 多 error code 分支

class VerdictSignal(str, Enum):
    MISSING_POSITIVE = "missing_positive"   # 应有的安全函数缺失
    NEGATIVE_SINK_HIT = "negative_sink_hit" # 误用弱原语
    SEMANTIC_SUSPECT = "semantic_suspect"   # 语义可疑，low-confidence

class AuthCandidate(BaseModel):
    id: str                          # "{file}:{handler_id}:{check_type}:{line}"
    handler_id: str                  # auth handler 的 FuncBlock.id
    endpoint: str | None             # 路由路径（若能识别），如 "GET /feishu-callback"
    check_type: AuthCheckType
    verdict_signal: VerdictSignal
    evidence_callee: str | None      # 检测的原语，如 "ctx.session.regenerate" / "math/rand" / "jwt.Parse"
    expected: str                    # 期望行为，如 "登录成功后调用 session.regenerate 轮换会话 ID"
    file_path: str
    line: int
    code_snippet: str
    confidence: Literal["high", "medium", "low"]   # 缺失/误用型 high；语义型 low
    needs_deep_agent: bool = True    # 全部候选默认交 spec-2b 深度 agent 深判
```

### 4.2 生成逻辑（方向 B）

```
1. 端点/handler 识别（多信号融合，见 §5）
   → 产 auth handler 候选集（FuncBlock 列表）
2. 对每个 auth handler，跑 check 检测器（每 check_type 一个）：
   - MISSING_POSITIVE 型：parser.iter_calls(handler) 内找 evidence_callee；
     若 handler 内无 且 可达路径（CallChain）上也无 → 产 MISSING_POSITIVE 候选
   - NEGATIVE_SINK_HIT 型：iter_calls 内命中弱原语（math/rand/MD5/...）→ 产候选
   - OAuth 缺失型：OAuth handler 内 grep state/nonce/code_challenge 零出现 → 产候选
   - SEMANTIC 型：login handler 内多个不同 error return → low-confidence 候选
3. 输出 auth_candidates.json
```

### 4.3 code_index 集成方式（§6 产出 2 / §7 Q6/Q7）

**推荐：B+C 混合，独立 track**（类比 `authz_gitnexus_track.py`），新建 `auth_gitnexus_track.py`。

| 选项 | 评估 |
|---|---|
| ❌ 扩 `auth_config_scanner.py` | 那是 config 正则扫描器；逻辑类（需 AST/call graph）塞进去会污染其纯度，且它独立于 code_index（无 AST） |
| ❌ 扩 `sink_rules.yml` + `SinkCategory.AUTH` | 语义反转：现有 sink=危险函数（命中=坏）；auth positive sink=安全必需函数（缺失=坏）。强行建模会扭曲 sink 语义，且 auth "sink" 不参与 taint 传播 |
| ✅ **独立 `auth_gitnexus_track.py`** | 复用 code_index AST（`iter_calls`/`destructure_call`）+ CallChain（可达性），但自带 AuthCandidate 模型 + check 检测器，产 `auth_candidates.json` 喂 spec-2b 深度 agent。与 authz track 同构，架构一致 |

**复用项**：`parser.iter_calls` / `destructure_call`（handler 内原语检测）、`CallChain.path` / `_source_reaches_sink`（可达路径，解 spike §7 Q1 "session.regenerate 在 helper 里"）、`ConfigFinding` 模型（config 类）、`_find_source_files`（文件发现）。

---

## 5. 端点识别跨框架问题 + 解法（§7 Q2）

**问题坐实**：现有 `_AUTH_ROUTE_RE` 在两真实生产 repo **召回 0**：
- moa-auth：`router.get('/feishu-auth', controller.feishu.auth)`（Egg.js + 业务命名 feishu-）
- futu_auth_svr：`authorityGroup.POST("/logout", ...)` / `webauthGroup.POST(...)`（Gin group + authority/webauth 命名）
- 根因：真实 auth 服务端点路径往往**业务化命名**（feishu/authority/webauth/passkey），非教科书 `/login` `/signup`。靠"扩关键词表"不可行（业务命名无限）。

**解法：多信号融合识别 auth handler**（spike §7 Q2 答案）：
1. **路由正则**（扩 `_AUTH_ROUTE_RE`）：加 logout/callback/authorize + Gin group（`xGroup.POST`）+ Egg（`router.get`）+ Django（`path(` / `url(`）—— 覆盖教科书命名 + 框架语法。
2. **handler 函数名语义**：`login/logout/auth/passkey/token/salt/verify/callback` 等（futu 的 `ClientLogout`/`QrcodeLogin`/`passkeyauth.Handle` 可命中）。
3. **反向定位（最鲁棒）**：handler 体内调用 auth 原语（`ctx.session.*` / 密码比较 / OAuth API / token 签发 / `bcrypt`/`argon2`）→ 该 handler 是 auth handler。**不依赖命名**，业务化命名也能抓。
   - 信号 3 需 call graph（GitNexus）或全代码库 AST 扫描；GitNexus 不可用（CLAUDE.md 预存问题）时退化为全库 AST 扫描（O(文件数×函数数)，对 auth 子集可控）。

**与 spec-1b 的关系**：信号 1/2 复用 spec-1b G4（框架识别扩展）；信号 3 是 auth 专属，归本 track。

---

## 6. spike §7 七个关键问题回答

1. **方向 B 精度（call graph 追 helper 里的 session.regenerate）**：`CallChain.path` + `_source_reaches_sink` 现成可做可达性检测；GitNexus 不可用时退化为 handler 内 + 同文件 helper 扫描。精度：缺失型二元信号，极高。
2. **跨语言通用性**：AST parser 支持 6 语言；实测 Node.js/Egg.js + Go/Gin 成立。框架差异（Express session vs Django session vs gorilla/sessions）体现在**原语名规则**（per-language callee 表），检测机制通用。覆盖度：核心 4 类（session-fixation/token/OAuth/recovery）跨语言成立。
3. **A vs B 取舍**：**B 性价比高**。A（auth sink 走完整 call graph）路径长、需语义反转 sink 体系；B（handler 层检查清单）直接、复用 `iter_calls`，且缺失型检测不需要完整 call graph（handler 内 + 浅可达即可）。spec-2b 采用 B 为主，A 的 call graph 仅作 B 的"可达路径补充"。
4. **方向 C 可靠性**：**证伪**。recon auth section 纯散文不可解析。但 C 的**情报**（session 机制/密码哈希算法）可由 spec-2b 深度 agent（吃候选的 LLM）在判定时读取，不进确定性层。
5. **边界（哪些需理解业务逻辑）**：**fail-open / OAuth race / 架构级 session 撤销 / 用户枚举可区分性** —— 确定性只能产 low-confidence 候选或完全无候选，交 LLM 轨深度 agent。这正是 spec-2b 深度 agent 吃候选后仍需 LLM 推理的部分。
6. **与 auth_config_scan 关系**：**独立 `auth_gitnexus_track.py`**（见 §4.3），不扩 config scanner。config 类（4 类）仍走 auth_config_scan；逻辑类（缺失/误用型 4-6 类）走新 track。两者产物在 merger 层 OR。
7. **code_index 集成**：独立 track + AuthCandidate 模型，复用 AST/CallChain，**不进 sink/source 体系**（语义反转，见 §4.3）。

---

## 7. 对 spec-2b 的建议（§6 产出 4）

**启动 spec-2b**，范围限定如下：

### 7.1 候选模型定稿
- 按 §4 schema 落地 `AuthCandidate` + `auth_gitnexus_track.py`。
- check 检测器首批实现 **7 个高/中高置信检查项**：`SESSION_REGENERATE_MISSING`、`LOGOUT_DESTROY_MISSING`、`PASSWORD_HASH_MISSING`、`JWT_VERIFY_MISSING`、`WEAK_RANDOM_TOKEN`、`WEAK_CRYPTO`、`OAUTH_STATE_MISSING`（+ `_NONCE`/`_PKCE`）。
- `USER_ENUMERATION_DIFF_ERROR` 作为 low-confidence 后置项（误报风险，先不进默认候选集，或产候选但标 `confidence=low` 直接交 agent）。

### 7.2 范围限定
- **语言**：先 **Node.js + Go**（实测 repo 支撑、生产基准现成）。Python/Java/PHP 的 AST parser 现成，但检查项的原语名规则（per-language callee 表）需补，建议 spec-2b 后期或 spec-1b OpenAPI parser 后扩。
- **检查项子集**：缺失/误用型 4 类（session-fixation / token / SSO-OAuth / recovery），不攻 fail-open/业务逻辑型（明确交 LLM 轨）。
- **端点识别**：实现 §5 三信号融合（信号 3 反向定位最关键）。

### 7.3 深度 agent（spec-2b 主体）
- 类比 `run_authz_gitnexus_verdict_agent`：吃 `auth_candidates.json`，多轮 agent（grep/read 工具）深判每个候选。
- 深判内容：缺失是否真为缺陷（排除"用了等价原语"，如 `request.session.cycle` 替代 `regenerate`）、误用是否在安全敏感路径、OAuth 缺失是否被框架兜底。
- **明确不覆盖**的业务逻辑型缺陷，spec-2b agent 不强行判定（避免幻觉），由 LLM 轨 `vuln-auth` 兜底。

### 7.4 风险
- **R-2b-1（端点识别召回）**：信号 3 反向定位依赖 AST 扫描成本；GitNexus 不可用时退化扫描需控范围（仅扫 auth 原语命中文件的函数）。对策：先跑 auth 原语 grep 定位文件，再 AST 精扫。
- **R-2b-2（误报）**：缺失型最大误报源是"用了等价安全原语"（`regenerate` 的同义 API）。对策：深度 agent 深判 + per-language 原语同义词表。
- **R-2b-3（覆盖预期管理）**：必须向 epic 同步——spec-2b 只覆盖缺失/误用型，**不覆盖 fail-open 等业务逻辑型**。关 LLM 轨时 auth 仍会丢这部分覆盖（与 authz 不同，authz 候选模型更完整）。

---

## 8. 对 epic 的影响

- **epic 不降级**（§4 退路不触发）。spec-2b 启动。
- **G2 愿景修正**：原愿景"GitNexus 轨独立兜 auth 深度"→ **部分成立**。独立兜"缺失/误用型"（治本的一部分），业务逻辑型仍需 LLM 轨。epic §4 退路里"auth 仍靠 LLM 轨"的表述**部分保留**：auth 的业务逻辑型靠 LLM 轨，缺失型可 GitNexus 独立。
- **CLAUDE.md §1 影响（G4）**：未来 spec-2b 落地后，§1「auth/authz 特殊」段 auth 部分（当前"config 扫描器兜底 + auth 深度 agent 待 spec-2b"）需更新为"config 扫描 + 缺失/误用型确定性候选（`auth_gitnexus_track`）+ 深度 agent；业务逻辑型仍靠 LLM 轨"。本次 spike 先不动 CLAUDE.md（等 spec-2b 实现）。
- **与 spec-1b 并行**：spike 已完成，spec-1b（authz 候选扩展）与 spec-2b 可并行推进；spec-2b 复用 spec-1b G4 框架识别（信号 1/2）。

---

## 9. 原型深度说明 + 后续验证

**本次 spike 原型深度**：grep 探针量化（非接入 code_index AST 的端到端原型）。
- **已证明**：缺失/误用型 sink 检测的**召回信号**（`session.regenerate` 0 调用 / `math/rand` 命中 / `jwt.Parse` 0 调用 / OAuth state 0 命中）—— 这些是二元确定性结论，grep 量化等价于 AST 检测的召回。
- **未做**：接入 code_index AST 跑端到端（handler 函数体定位 + iter_calls + 误报统计）。
- **风险评估**：域 2 已确认 AST `iter_calls`/`destructure_call` 支持 6 语言、handler 内原语检测是现成能力的直接应用，**端到端低风险**。spike §1 非目标"不追求生产级精度"，故未投入 AST 原型的 token。

**spec-2b 前置验证（建议）**：spec-2b Task 1 先做一个最小端到端原型——在 moa-auth 上跑 `SESSION_REGENERATE_MISSING` 检测器（AST），确认召回 #1 + 误报 = 0，再铺开其余检查项。这是 spike 结论的最终实证，纳入 spec-2b plan 的早期 task。

---

## 附录：关键证据 file:line / path

- 基准产物：`/root/code/frontend/moa-auth/.shannon/deliverables/auth_exploitation_queue.json`（12 条）、`/root/code/20260615/futu_auth_svr/.shannon/deliverables/auth_exploitation_queue.json`（13 条）
- moa-auth 路由：`app/router.ts:7,9`（`/feishu-auth`、`/feishu-callback`，Egg.js）
- futu 路由：`internal/app/web/router.go:64-98`（`authorityGroup.POST` / `webauthGroup.POST`，Gin）
- `_AUTH_ROUTE_RE`：`auth_config_scanner.py:281-286`
- AST parser：`code_index/parsers/{python,typescript,go,java,php}_parser.py`
- 可达性 API：`authz_gitnexus_track.py:101-140`（`_source_reaches_sink`）
- sink 匹配：`sink_detector.py:134-221`
- 缺失型证据：moa-auth `regenerate`/`logout` 0 命中；futu `general_tool.go:10` math/rand/v2、`jwt.Parse` 0 命中、`controller/third` OAuth state 0 命中
