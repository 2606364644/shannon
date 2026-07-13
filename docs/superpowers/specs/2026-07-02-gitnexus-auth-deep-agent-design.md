> ⚠️ auth 部分已回退 2026-07-14（对齐原始 shannon：`auth_config_scanner` 踩 §1 铁律「确定性产物不喂 LLM 轨 prompt」+ CORS 越界被裁的 misconfig；authz GitNexus 轨保留。详见 plan zazzy-roaming-shamir / memory auth-gitnexus-track-reverted）

# auth GitNexus 轨深度 agent（spec-2b） 设计

> 日期：2026-07-02（spec 定稿）/ 2026-07-02 框架草案　分支：`feat/fork-py`　所属 epic：`2026-07-02-gitnexus-deep-agent-auth-authz-design.md`（子项目 2b）
>
> **条件性已满足**：spec-2a spike 通过（裁决"可行"，见 `2026-07-02-auth-deterministic-candidate-model-spike-report.md`）。epic 不降级，本 spec 按 spike 报告 §7 建议限定范围启动。
>
> **本文件已定稿**：原【待 2a 细化】项已由 spike 报告填实（§2 schema / §3.5 产物关系 / §4 验收数据 / §5 端点识别 / §7 检查项+语言范围）。

---

## 1. 目标 / 非目标

### 目标

- **G1（auth 候选生成）**：按 spike 报告 §4 候选模型，在 GitNexus 轨确定性生成 auth 候选——独立 `auth_gitnexus_track.py`，三信号识别 auth handler + 6 检查器，产 `AuthCandidate` 列表。
- **G2（auth 多轮判定）**：候选经 `run_gitnexus_verdict_agent`（spec-0）多轮深判，产 `auth_gitnexus_queue.json`（与 `auth_config_scan` 的 config 类同 queue 追加，§3.5）。
- **G3（独立兜底）**：关 LLM 轨时，GitNexus 轨对 auth **逻辑类**（非 config）有深度判定产出（缺失/误用型子集）。

### 非目标

- **不改 `auth_config_scan`**：config 类（cookie/HSTS/CORS/JWT-claim/rate-limit）已覆盖；本 spec 只补逻辑类。
- **不改 LLM 轨 `vuln-auth.txt`**：保留为可选增强（双轨 OR）。**业务逻辑型缺陷（fail-open / OAuth race / 架构级）仍靠 LLM 轨**——spike 证伪的子类不在本 spec。
- **不改双轨 merger**：`auth_gitnexus_queue.json` schema 不变，merger 无感（§3.5 同 queue 追加）。
- **不覆盖 spike 证伪的子类**：认证流程绕过等纯业务逻辑确定性够不着（spike 报告 §3/§7 Q5），交 LLM 轨。
- **首批不覆盖 user_enumeration**：spike 标 low confidence（多 error code 差异需语义），本 spec 后置或产 low-confidence 候选直接交 agent。

---

## 2. 候选模型（spike 报告 §4 定稿）

### 2.1 方向

spike 聚焦的**方向 B**（认证 handler + 结构化检查清单），确定性高质量覆盖**缺失/误用型** auth 缺陷（spike 实测覆盖逻辑类 9 类中的 4 类，误报 <20%，Node.js + Go 跨语言成立）。

### 2.2 AuthCandidate schema（spike §4.1）

```python
class AuthCheckType(str, Enum):
    # —— positive sink 缺失（应调用的安全函数没调）——
    SESSION_REGENERATE_MISSING = "session_regenerate_missing"      # session 固定
    LOGOUT_DESTROY_MISSING = "logout_destroy_missing"              # 无登出/会话撤销
    PASSWORD_HASH_MISSING = "password_hash_missing"                # 密码明文存储
    JWT_VERIFY_MISSING = "jwt_verify_missing"                      # OIDC id_token 未验签
    # —— negative sink 命中（误用弱原语）——
    WEAK_RANDOM_TOKEN = "weak_random_token"                        # math/rand 用于 token/盐
    OAUTH_STATE_MISSING = "oauth_state_missing"                    # OAuth state/nonce/PKCE 缺失
    # —— low-confidence（首批后置）——
    USER_ENUMERATION_DIFF_ERROR = "user_enumeration_diff_error"    # 多 error code 分支

class VerdictSignal(str, Enum):
    MISSING_POSITIVE = "missing_positive"   # 应有的安全函数缺失
    NEGATIVE_SINK_HIT = "negative_sink_hit" # 误用弱原语
    SEMANTIC_SUSPECT = "semantic_suspect"   # 语义可疑，low-confidence

class AuthCandidate(BaseModel):
    id: str                          # "{file}:{handler_id}:{check_type}:{line}"
    handler_id: str                  # auth handler 的 FuncBlock.id
    endpoint: str | None             # 路由路径（若识别），如 "GET /feishu-callback"
    check_type: AuthCheckType
    verdict_signal: VerdictSignal
    evidence_callee: str | None      # 检测的原语，如 "ctx.session.regenerate" / "math/rand" / "jwt.Verify"
    expected: str                    # 期望行为，如 "登录成功后调用 session.regenerate 轮换会话 ID"
    file_path: str
    line: int
    code_snippet: str
    confidence: Literal["high", "medium", "low"]   # 缺失/误用型 high；语义型 low
    needs_deep_agent: bool = True    # 全部候选默认交深度 agent 深判
```

### 2.3 集成方式（spike §4.3 裁决）

**独立 `auth_gitnexus_track.py`**（类比 `authz_gitnexus_track.py`），**不**扩 `auth_config_scanner.py`（config 正则扫描器，逻辑类塞进去污染纯度），**不**塞 `sink_rules.yml`（语义反转：现有 sink=危险函数命中=坏；auth positive sink=安全必需函数缺失=坏，强行建模扭曲 sink 语义且不参与 taint 传播）。

**复用 code_index 现有能力**：AST `parser.iter_calls` / `destructure_call`（handler 内原语检测，6 语言）、`CallChain.path` / `_source_reaches_sink`（可达路径，解 spike §7 Q1 "session.regenerate 在 helper 里"）、`_find_source_files`（文件发现）。

### 2.4 生成逻辑（spike §4.2）

```
1. 端点/handler 识别（三信号融合，§5）→ auth handler 候选集（FuncBlock 列表）
2. 对每个 auth handler 跑检查器（每 check_type 一个）：
   - MISSING_POSITIVE 型：iter_calls(handler) 内找 evidence_callee；
     handler 内无 且 可达路径（CallChain）上也无 → 产 MISSING_POSITIVE 候选
   - NEGATIVE_SINK_HIT 型：iter_calls 内命中弱原语（math/rand/MD5/...）→ 产候选
   - OAUTH_STATE_MISSING：OAuth handler 内 grep state/nonce/PKCE 零出现 → 产候选
3. 输出 AuthCandidate 列表 + markdown 渲染
```

### 2.5 首批检查器 + 语言范围（spike §7.1/7.2）

**首批 6 检查器**（高/中高 confidence）：
1. `SESSION_REGENERATE_MISSING`（session 固定）— spike moa-auth #1 实测可检
2. `LOGOUT_DESTROY_MISSING`（无登出）— moa-auth #9 实测可检
3. `PASSWORD_HASH_MISSING`（明文密码）— 原则可检（positive sink 缺失）
4. `JWT_VERIFY_MISSING`（JWT 未验）— futu #13 实测可检（0 调用）
5. `WEAK_RANDOM_TOKEN`（math/rand 弱随机）— futu #6 实测可检（negative sink 命中）
6. `OAUTH_STATE_MISSING`（OAuth state/nonce/PKCE 缺失）— futu #12 实测可检（0 命中）

`USER_ENUMERATION_DIFF_ERROR` 后置（low confidence，spike 标语义难）。

**语言范围**：**Node.js + Go 先行**（spike 实测两真实 repo 成立：moa-auth Egg.js / futu_auth_svr Gin）。检查器原语规则 per-language（见 §3.1）。Python/Java/PHP 的 AST parser 现成，原语规则后续扩展。

---

## 3. 设计

### 3.1 候选生成：`build_auth_gitnexus_track`

新建 `packages/core/src/shannon_core/code_index/auth_gitnexus_track.py`，结构对标 `build_authz_gitnexus_track`：

```python
def build_auth_gitnexus_track(deliverables_dir: str) -> AuthTrackResult:
    """读 code_index.json（entry_points/blocks/source_points/chains）→ 三信号识别
    auth handler → 跑 6 检查器 → 产 AuthCandidate 列表 + markdown。"""
```

**端点识别三信号融合（spike §5）**——现状 `_AUTH_ROUTE_RE`（`auth_config_scanner.py:281-286`）在真实生产 repo 召回 0（moa-auth `/feishu-auth`、futu `/authority/*` 业务命名），需多信号：
1. **路由正则**（扩 `_AUTH_ROUTE_RE`）：加 logout/callback/authorize + Gin group / Egg router / Django path，覆盖教科书命名 + 框架语法。
2. **handler 函数名语义**：login/logout/auth/passkey/token/salt/verify/callback 等（futu 的 `ClientLogout`/`QrcodeLogin`/`passkeyauth.Handle` 可命中）。
3. **反向定位（最鲁棒）**：handler 体内调用 auth 原语（`ctx.session.*` / 密码比较 / OAuth API / token 签发 / `bcrypt`/`argon2`）→ 该 handler 是 auth handler。**不依赖命名**，业务化命名也能抓。需 call graph（GitNexus）或全代码库 AST 扫描；GitNexus 不可用时退化为全库 AST 扫描（先 auth 原语 grep 定位文件，再 AST 精扫）。

**6 检查器 per-language 原语规则**（首批 Node.js + Go）：

| check_type | Node.js 原语 | Go 原语 |
|---|---|---|
| SESSION_REGENERATE_MISSING | `ctx.session.regenerate` / `req.session.regenerate` / `session.regenerate` | gorilla/sessions `session.Options.MaxAge(-1)` + 重建；库多样，**首批 Node 优先，Go session 类后置** |
| LOGOUT_DESTROY_MISSING | `ctx.session.destroy` / `req.session.destroy` | 同上，Go 后置 |
| PASSWORD_HASH_MISSING | `bcrypt.hash` / `argon2.hash` / `pbkdf2` / `crypto.scrypt`（positive sink） | `bcrypt.GenerateFromPassword` / `argon2id` / `crypto.scrypt`（Go 标准库明确） |
| JWT_VERIFY_MISSING | `jwt.verify` / `jsonwebtoken.verify`（缺失） | `jwt.Parse` / `jwt.Verify` / `ParseWithClaims`（缺失，futu #13） |
| WEAK_RANDOM_TOKEN | `Math.random` / `crypto.pseudoRandomBytes`（误用） | `math/rand`（非 crypto/rand，futu #6） |
| OAUTH_STATE_MISSING | state/nonce/code_challenge 零出现（OAuth handler） | 同 |

**Go session 类（regenerate/logout）首批后置**：Go session 库多样（gorilla/chi/gin-session/echo），无统一 API，spike 标"Go session 类需细化"。首批 Go 只做 JWT_VERIFY_MISSING / WEAK_RANDOM_TOKEN / OAUTH_STATE_MISSING / PASSWORD_HASH_MISSING（标准库明确）；Node.js 全 6 个。

### 3.2 多轮判定（spec-0 脚手架，对标 spec-1a G1）

新建 `run_auth_gitnexus_judge` activity（`activities.py`）：候选 >0 时切 `run_gitnexus_verdict_agent`（多轮，带 grep/read 追链），对标 `run_authz_gitnexus_judge`。

- 候选 ≤ 阈值全量塞 prompt；> 阈值分批并行（fan-out + Semaphore，对标 spec-1a）。
- 新 prompt `auth_gitnexus_judge_deep.txt`：含 check_type + evidence + 工具引导（"可用 grep/read 追 owner 检查、确认缺失是否为缺陷、排除等价安全原语"）。
- structured_output 产 `AuthVulnerability`（schema 对齐现有 auth queue）。

### 3.3 候选空探索（对标 spec-1a G2）

候选空（端点未识别 / 检查项全过）时，不静默写空 queue——agent 读认证相关源码（grep login/session/token/password/oauth）自主探索，产软候选（`needs_deep_agent=True`、`confidence=low`）。新 prompt `auth_gitnexus_explore.txt`。

### 3.4 render + 喂 LLM（对标 spec-1a G3）

`render_auth_gitnexus_candidates(candidates)`：候选描述含 check_type + verdict_signal + evidence（file:line + code_snippet）+ expected + endpoint route。喂深度 agent prompt。

### 3.5 与 auth_config_scan 产物关系（定 (a) 同 queue 追加）

**选项 (a)**：逻辑类候选判定结果与 config 类同写一个 `auth_gitnexus_queue.json`。
- activity 顺序：`run_auth_config_scan`（config 类，产初始 queue）→ `run_auth_gitnexus_judge`（逻辑类，读 queue + 追加逻辑类判定，写回）。
- schema 兼容：两类都产 `AuthVulnerability`（`source_track` 区分 config_scan vs gitnexus_logic）。
- **merger 无感**：`dual_track_merger` 消费 `auth_gitnexus_queue.json` 不变（spec 非目标"不改 merger"）。

弃 (b)（分文件 `auth_config_queue.json` + `auth_logic_queue.json`）：要求 merger 改消费两个 auth queue，违反非目标。

---

## 4. 验收（spike 数据填实）

- **V1（多轮深判）**：候选非空时，`run_auth_gitnexus_judge` 跑多轮 agent（`result.turns > 1`），产含 evidence 的 `auth_gitnexus_queue.json`。
- **V2（自主探索）**：候选空时 agent 探索产软候选（`needs_review=True`），queue 非空（或"已探索无发现"证据，非静默空）。
- **V3（覆盖面，spike 数据）**：spike 确认的 4 类（session-fixation / token / SSO-OAuth / recovery 的缺失/误用型）在实现后达成；首批 6 检查器覆盖。
- **V4（精度，spike 数据）**：缺失/误用型误报 <20%（spike §3 实测二元信号低误报）。spike §9 建议：Task 1 先做 moa-auth `SESSION_REGENERATE_MISSING` 端到端原型，确认召回 #1 + 误报=0，再铺开。
- **V5（独立兜底）**：`SHANNON_LLM_TRACK_ENABLED=0` 时 auth 逻辑类有 GitNexus 轨产出（非空或已探索证据）。
- **V6（双引擎）**：双引擎探针实测（`scripts/validate_*_task_probe.py` 类）。
- **V7（与 config scan 共存）**：`run_auth_config_scan` 行为不变；本 spec 产物（逻辑类）与 config 类同 queue 不冲突（`source_track` 区分）。
- **V8（端到端）**：moa-auth（Node/Egg）白盒跑，`SESSION_REGENERATE_MISSING` 候选生成 + 深判产 queue 条目，对照 moa-auth 基准 #1。

---

## 5. 风险

- **R1（spike 已除）**：候选模型可行性——spike 已裁决可行（不降级）。剩余风险是**实现**复杂度（端点识别三信号 + 6 检查器 × 语言原语）。
- **R2（auth 候选确定性弱于 authz）**：authz 有 ownership guard 明确模式；auth 逻辑类"guard"是流程语义，确定性更弱。**对策**：候选全标 `needs_deep_agent=True`，交深度 agent 深判（conservative）；缺失/误用型二元信号 confidence=high，语义型 low。
- **R3（端点识别召回）**：三信号融合的反向定位（信号 3）依赖 AST 扫描成本；GitNexus 不可用时退化全库扫描需控范围。**对策**：先 auth 原语 grep 定位文件，再 AST 精扫（O(可控)）；V4 原型验证召回。
- **R4（Go session 类后置）**：Go session 库多样无统一 API，首批 Go session 类（regenerate/logout）后置，仅做标准库明确的 JWT/random/oauth/hash。**对策**：spec 明示首批 Go 范围；Node.js 全覆盖，Go 部分覆盖（spike 中间态：某语言盲的子集）。
- **R5（覆盖预期管理）**：spec-2b 只覆盖 spike 确认的缺失/误用型子集，**不覆盖 fail-open / OAuth race / 业务逻辑型**（交 LLM 轨）。关 LLM 轨时 auth 仍丢这部分（与 authz 不同）。**对策**：CLAUDE.md §1 + memory 同步此定位（epic G2 愿景修正：GitNexus 独立兜缺失型，业务逻辑型仍靠 LLM 轨）。
- **R6（与 auth_config_scan 顺序耦合）**：同 queue 追加要求 `run_auth_config_scan` 先于 `run_auth_gitnexus_judge`。**对策**：workflows 编排定顺序 + 回归锚点。
