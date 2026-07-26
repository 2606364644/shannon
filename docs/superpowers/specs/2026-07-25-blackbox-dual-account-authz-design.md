# 黑盒双账号授权验证（Blackbox Dual-Account Authorization Verification）— 设计 spec

> 日期：2026-07-25 · 分支：`feat/fork-py` · 状态：设计待审
>
> 参考但不照搬原始 TS 项目 `docs/superpowers/specs/2026-06-14-authz-multi-account-design.md`（那份设计完整但**从未落地**，零实现 commit）。本 spec 针对 shannon-py 的真实结构做了简化与适配。

## Problem（问题）

shannon-py 黑盒模式的 `authz-exploit` agent 只持**单个**登录身份：

- preflight `validate_authentication` 登录一次，存一份 `auth-state.json`（`packages/core/src/supernova_core/services/validate_authentication.py:42`）。
- 所有 agent 经 `prompts/shared/_shared-session.txt` 共享同一 session / auth-state。
- `authz-exploit.txt:156-179` 的 Task Agent 模板要求 `Identity set: [list of user IDs/tokens/roles to iterate]`，但 config 层只供应**一个**身份——identity set 实际为空，靠 LLM 运行时自己抓 token 拼凑。

对授权越权测试这是**根本缺陷**，不是便利性缺口：

- **Horizontal (IDOR) 无法产硬证据**：要证明"user A 访问了 user B 的数据"，需要 B 的数据做 **baseline**——这要求登录成 B。单账号只能枚举 ID 观察 `200`，**无法区分**"别人的私有数据"和"我本就被允许看的数据 / 公开数据"，发现默认 weak `POTENTIAL` 或误报 `EXPLOITED`。
- **Vertical 缺能力 baseline**：要确认"只有 admin 能到这"，需要一个 admin session 来确立 admin-only 的样子。单账号做不到。
- **判定铁律缺位**：`authz-exploit.txt` 现有判定框架（line 52-70）允许单账号下"成功访问"判 `EXPLOITED`，但无 baseline 佐证的"成功访问"可信度低，易误报。

注：黑盒 `authz-exploit` 仍吃白盒产的 `authz_exploitation_queue.json`（候选来源不变，`packages/blackbox/src/supernova_blackbox/agents/exploit_executor.py:36-38`）。本设计**不改候选来源**，只增强动态验证的证据强度。

## Goal（目标）

让操作者可选地配置多个账号（victim / admin baseline）。preflight 把每个身份登录到独立 session，黑盒 `authz-exploit` 运行时同时持有 attacker / victim / baseline 三个 session，跑"baseline 捕获基线 ↔ attacker 攻击 → 对比"协议，产出**硬授权证据**（`EXPLOITED`）。单账号时优雅降级为现状行为（`POTENTIAL`）。

**完全向后兼容**：不配 `accounts` 时，整条流水线 byte-identical 等同今天。

### Non-Goals

- **不动白盒**：vuln-authz 保持纯静态分析，不吃 role context、不开多 session（用户选定"黑盒动态验证增强"范围）。
- **不覆盖 auth（认证）**：双账号只服务 authz（vertical + horizontal）。认证类漏洞不需要 baseline 对比。
- **不动候选来源**：黑盒 authz-exploit 仍吃白盒 `authz_exploitation_queue.json`。不为"纯黑盒独立发现 authz"扩展黑盒 recon。
- **不自动注册 victim 账号**（ROE 风险、空账号问题）。身份由操作者提供。
- **不做多租户** `tenant` 字段 / 跨租户隔离测试。
- **不做 per-account login 配置 override**：victim/baseline 继承全局 `authentication` 的 `login_type`/`login_url`/`login_flow`/`success_condition`（覆盖"同一登录页、不同凭据"这个常见场景）。

## Key Decisions

| 决策点 | 选择 | 理由 |
|---|---|---|
| 身份来源 | 手动 config（`accounts[]`） | 可控、非侵入、合规干净 |
| 覆盖范围 | Horizontal + Vertical（仅 authz） | 双账号 baseline 的核心受益场景 |
| attacker 建模 | 隐式 = primary `authentication` | 保证旧 config 零改动 |
| victim/baseline 建模 | `accounts[]` 里 `usage` 显式区分 | victim（horizontal baseline）与 baseline（vertical admin baseline）语义不同，分开建模 |
| session 策略 | **multi-slot，身份同时驻留**（on-demand 关浏览器） | 消除 LLM 最不擅长的登入登出状态切换；shannon-py 的 session_id 是任意字符串，扩 multi-slot **不用改枚举**（优于 TS） |
| 登录/exploit 解耦 | auth-state 文件桥接（两阶段 session 不同名） | storageState 快照 session 无关，登录循环与 exploit 拓扑完全解耦 |
| 登录失败 | attacker 必须（fail-fast）；victim/baseline 降级 | 一个死掉的 victim 不能拖垮整次扫描 |
| manifest 落盘 | `identity-manifest.json`（workspace） | 解耦 temporal activity 间传参 + 可调试 + 降级状态可追溯 |
| 判定铁律 | 无 baseline 一律 `POTENTIAL` | 收紧单账号下误报 `EXPLOITED` 的口子 |
| 单账号降级 | 仍跑动态尝试，给 `POTENTIAL` | 不损失现有覆盖，等同现状 |
| per-account 登录配置 | 继承全局 `authentication` | 覆盖常见场景；YAGNI per-account override |

## Architecture

```
config.accounts[] ──► 黑盒 preflight：每个 identity 登录到独立 session
                      auth-state.json (attacker=primary) + auth-state-{id}.json (victim/baseline)
                      + identity-manifest.json（可用身份清单 + 降级标记）
                      attacker 登录失败 → fail-fast；victim/baseline 失败 → 标 available=false 继续
                              │
                              ▼
                  黑盒 authz-exploit（仍吃白盒 authz_exploitation_queue.json）
                  读 identity-manifest.json → 注入多 session 变量 + manifest
                  ├─ victim+baseline 可用 → 比较协议 → 硬 EXPLOITED
                  └─ 单账号 / victim·baseline unavailable → 现状动态尝试 → POTENTIAL
```

白盒完全不动（queue 产物不变）。

## Design

### 1. Data Model & Config

**`packages/core/src/supernova_core/models/config.py`** 新增：

```python
AccountUsage = Literal["victim", "baseline"]  # attacker 隐式 = authentication，不出现在 accounts

class Account(BaseModel):
    id: str            # ^[a-z0-9-]+$，唯一；决定 auth-state-{id}.json 文件名
    role: str          # 自由文本：user/admin/viewer...，喂 prompt role context
    usage: AccountUsage
    credentials: Credentials   # 复用现有 Credentials shape（config.py:34）

class Config(BaseModel):
    ...
    authentication: Authentication | None = None   # = attacker（向后兼容）
    accounts: list[Account] | None = None           # 可选 victim/baseline
```

`DistributedConfig` 同步加 `accounts`（黑盒经 `dist_config` 取值，和 `authentication` 同路分发）。

**Config 示例**（`configs/*.yaml` / example）：

```yaml
authentication:                 # primary = attacker（向后兼容）
  login_type: form
  login_url: "https://app.example.com/login"
  credentials: { username: userA, password: "***" }
  login_flow: [...]
  success_condition: { type: url_contains, value: /dashboard }

accounts:                       # 可选额外身份
  - id: victim_b
    role: user
    usage: victim              # horizontal baseline；必须拥有私有资源
    credentials: { username: userB, password: "***" }
  - id: admin
    role: admin
    usage: baseline            # vertical baseline；高权能力参照
    credentials: { username: admin, password: "***" }
  # 所有 accounts 继承 authentication.login_type/login_url/login_flow/success_condition
  # 各自的 credentials 填入 login_flow 的 $username/$password/$totp 占位符
```

**校验**（`packages/core/src/supernova_core/config/parser.py`）：
- `id` 匹配 `^[a-z0-9-]+$` 且跨 `accounts` 唯一（文件名安全）。
- `usage` ∈ `{victim, baseline}`（attacker 隐式，不允许出现在 `accounts`）。
- `accounts` 非空时 `authentication` 必须存在（attacker 必须）。
- 每个 `credentials.username` 走现有危险模式检查。

### 2. Identity Lifecycle（Preflight）

**auth-state 路径参数化**（`validate_authentication.py:42`）：

```python
def auth_state_path(workspace, account_id: str | None = None) -> Path:
    name = "auth-state.json" if account_id is None else f"auth-state-{account_id}.json"
    return Path(workspace) / name
```

无 `account_id` → `auth-state.json`（旧调用点零改动）。`cleanup_auth_state` / `_sync`（line 46-57）改 glob `auth-state*.json`，清所有 identity 快照。

**登录循环**（`validate_authentication` 改造）：构建 `[primary(attacker), *accounts]`，逐个登录。每个 identity：
- 用**独立 session**（`validate-auth-{id}`）登录，避免 cookie/storage 串。
- 存对应 `auth-state-{id}.json`（attacker 存 `auth-state.json`）。
- 跑现有 `verify_auth_state`（cookies/origins 非空校验，line 60-88，复用）。

**失败处理**：
- **attacker(primary) 失败 → fail-fast**，返回 `AuthValidationResult(success=False)`，扫描终止。
- **victim/baseline 失败 → 标 `available=False`，继续**。

**产物 `identity-manifest.json`**（落盘 workspace）：

```python
@dataclass
class IdentityRecord:
    account_id: str        # "primary" / "victim_b" / "admin"
    role: str              # "user" / "admin"
    usage: str             # "attacker" / "victim" / "baseline"
    auth_state_file: str   # "auth-state.json" / "auth-state-victim_b.json"
    session_id: str        # exploit 阶段 session 名（见 §3）
    available: bool
    failure_detail: str | None

@dataclass
class IdentityManifest:
    identities: list[IdentityRecord]   # attacker 恒 available（否则 fail-fast 不返回）
```

> **§2 细化决策**：brainstorming 阶段曾倾向"manifest 走 prompt 变量、不落盘"。设计阶段改为落盘 `identity-manifest.json`——避免 temporal activity 间穿透传 manifest、便于调试、降级状态（`available=false`）可追溯。`authz-exploit` 是唯一消费者，读取后注入 prompt。不违背"只服务 authz-exploit"的精神。

### 3. Session Topology（shannon-py 简化版）

**核心简化**：原始 TS 要扩 `PlaywrightSession` 枚举（`agent1..5` → `agent7..10`）。shannon-py 的 `BrowserEngine.session_flag(session_id: str)`（`packages/core/src/supernova_core/services/browser_engine.py:36`）接受**任意字符串** session id——直接派生新 session 名，**零枚举改动**。

**拓扑**（`authz-exploit` 运行时最多持 3 个独立 session）：

| usage | session id | auth-state 文件 | 来源 |
|---|---|---|---|
| attacker | `authz-exploit`（现状不变） | `auth-state.json` | primary `authentication` |
| victim | `authz-exploit-victim` | `auth-state-{victim_id}.json` | `accounts` 里 `usage=victim` |
| baseline | `authz-exploit-baseline` | `auth-state-{baseline_id}.json` | `accounts` 里 `usage=baseline` |

派生用新 helper `get_identity_session_id(agent_name, usage)`（扩 `packages/core/src/supernova_core/services/playwright_config_writer.py` 的 `get_session_id`）。

**auth-state 桥接（关键解耦）**：auth-state 文件是 playwright storageState 快照（cookies + origins，`validate_authentication.py:79-80`），**session 无关**——可 load 进任意 session。所以：
- **登录阶段**（validate-authentication 循环）：每 identity 用 `validate-auth-{id}` 登录 → 存 `auth-state-{id}.json`。
- **exploit 阶段**（authz-exploit）：自己的 victim/baseline session `load` 对应 auth-state 文件。

两阶段 session 不同名，靠 auth-state 文件桥接，完全解耦（比原始 TS 想做的"同名 session"更干净）。

**资源控制**：峰值 `authz-exploit` 开 3 个 chromium（attacker + victim + baseline）。协议指导 agent 不比较时关 session 浏览器（保留 auth-state 按需重开），降并发足迹。

### 4. Comparison Protocol & Degradation（核心）

落到 `prompts/authz-exploit.txt` 现有的 horizontal/vertical/context 攻击模式（line 288-327）——方法论已齐全，只插一块比较协议。

**比较协议（identity manifest 非空时强制执行）**：

- **Horizontal (IDOR) — victim 做 baseline**：
  1. victim session 读**自己的**资源 `/orders/42` → baseline 数据（victim 合法拥有）。
  2. attacker session 读同一 `/orders/42`。
  3. attacker 可读 + 数据匹配 victim baseline ⇒ **EXPLOITED**（硬证据：跨用户私有数据访问）。
- **Vertical — baseline(admin) 做能力基准**：
  1. baseline(admin) session 访问目标端点 `/admin/users` → 确认端点存在且为 admin 能力（正常响应 / 数据形状）。
  2. attacker session 访问同一端点。
  3. attacker 可访问 + 获得等价能力 ⇒ **EXPLOITED**（baseline 确立"admin-only 能力"，attacker 能 reach 即越权）。

**降级判定**：

| 场景 | 行为 | 判定 |
|---|---|---|
| victim + baseline 都 available | 走比较协议 | 满足 ⇒ EXPLOITED |
| victim unavailable（horizontal） | 退"枚举 ID 观察 200"——能读但无法证明是别人私有数据 | 强制 POTENTIAL |
| baseline unavailable（vertical） | 退"低权 reach admin 端点"——能 reach 但无法确认是 admin-only 能力 | 强制 POTENTIAL |
| 完全单账号（无 accounts） | horizontal + vertical 都走降级 = 现状行为 | POTENTIAL |

**判定铁律**：EXPLOITED 必须有 baseline 对比佐证（horizontal: victim 数据 baseline；vertical: admin 能力 baseline）。**无 baseline 的任何"成功访问"一律 POTENTIAL，不得 EXPLOITED**——收紧单账号下误报 EXPLOITED 的口子（现状可能误报，本设计比现状更严）。

**prompt 落地（insert-only，向后兼容）**：
- `prompts/authz-exploit.txt` 在 `<attack_patterns>` 前插入 `<comparison_protocol>` 块（上述协议文本）。
- 新增 `prompts/shared/_identities.txt` partial：渲染 identity manifest 表 + 比较协议块。
- `prompts/manager.py` 新增 `build_identity_context(manifest)`：多账号 → manifest 表 + 协议块；单账号 → 降级提示（明确告诉 agent"无 baseline，成功访问只能给 POTENTIAL"）。
- `authz-exploit.txt:156-179` 的 Task Agent "Identity set" 模板：从"LLM 自己拼"改成"读 manifest，用系统注入的真实 identity session"。
- 单账号时 `@include(shared/_shared-session.txt)`（line 91）保留不变（向后兼容）；多账号时替换为 `@include(shared/_identities.txt)`。

**降级可追溯**：verdict 的 `current_blocker` / `evidence_of_vulnerability` 字段写降级原因（如"victim baseline unavailable, downgraded to POTENTIAL"）。

> **POTENTIAL 在 verdict schema 里的表达（需 plan 决策）**：当前 `ExploitStatus`（`packages/core/src/supernova_core/models/exploit_verdict_schemas.py:15-17`）只有 4 档 `exploited`/`blocked_by_security`/`out_of_scope_internal`/`false_positive`，**无 `potential`**；`ExploitedVerdict`（line 25-30）也无 confidence/downgrade 字段。降级到 POTENTIAL 的表达两个选项：
> - **选项 A（复用，零 schema 改动）**：降级时 `status=blocked_by_security` + `confidence=low` + `current_blocker="baseline unavailable, no hard evidence"`。但 `blocked_by_security` 语义是"被安全机制挡"，降级是"被账号资源限"，语义不符会污染 blocked 统计。
> - **选项 B（新增档，推荐）**：`ExploitStatus` 加 `potential` 档 + 新 `PotentialVerdict`（severity/confidence/downgrade_reason/evidence_of_vulnerability）+ renderer（`packages/core/src/supernova_core/renderers/exploit.py:73-75`）加分类。语义最准，扩 schema 影响面略大。
>
> 推荐 B（语义干净、降级可追溯）。留 plan 阶段最终决策。

### 5. Error Handling, Backward Compatibility

**失败矩阵**：

| attacker | victim | baseline | 行为 |
|---|---|---|---|
| ✓ | —（无 accounts） | — | 现状：单账号，POTENTIAL |
| ✓ | ✓ | ✓ | 完整比较协议 → 可达 EXPLOITED |
| ✓ | ✗ | ✓ | horizontal 降级 POTENTIAL，vertical 正常 |
| ✓ | ✓ | ✗ | vertical 降级 POTENTIAL，horizontal 正常 |
| ✓ | ✗ | ✗ | 全降级 = 现状 |
| ✓ | ✓（只配 victim） | — | horizontal 正常，vertical 永远降级 |
| ✗ | * | * | fail-fast，扫描终止 |

**向后兼容铁律**：
- 无 `accounts` → byte-identical 等同今天。
- 旧 config 零改动；`auth-state.json` 主文件名不变；单 session 拓扑保留。
- `auth_state_path(None)` / `cleanup_auth_state` 旧调用点零改动。
- `exploit_executor` 单账号时注入单 `browser_session_id`（现状），多账号时注入多 session 变量。

## Files Changed

| 文件 | 改动 | 类型 |
|---|---|---|
| `packages/core/src/supernova_core/models/config.py` | `Account`、`AccountUsage`；`Config.accounts` + `DistributedConfig.accounts` | Modify |
| `packages/core/src/supernova_core/config/parser.py` | `accounts` 校验（slug/唯一/usage/依赖 authentication）+ 分发 | Modify |
| `packages/core/src/supernova_core/services/validate_authentication.py` | `auth_state_path` 参数化；登录循环；`IdentityManifest`/`IdentityRecord`；`identity-manifest.json` 落盘；cleanup glob | Modify |
| `packages/core/src/supernova_core/services/playwright_config_writer.py` | `get_identity_session_id(agent_name, usage)` helper | Modify |
| `packages/core/src/supernova_core/prompts/manager.py` | `build_identity_context(manifest)`；多 session 变量注入；manifest 渲染 | Modify |
| `packages/blackbox/src/supernova_blackbox/agents/exploit_executor.py` | 读 `identity-manifest.json`；多账号注入 victim/baseline session 变量；单账号回归 | Modify |
| `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py` | preflight 登录循环编排；manifest 落盘传递 | Modify |
| `prompts/shared/_identities.txt` | identity manifest partial（manifest 表 + 比较协议块） | **New** |
| `prompts/authz-exploit.txt` | `<comparison_protocol>` 块（insert-only）+ Task Agent identity 模板改注入 | Insert/Modify |
| `configs/*.example.yaml` | `accounts` 示例（additive） | Modify |
| `packages/core/src/supernova_core/models/exploit_verdict_schemas.py` | （选项 B）`ExploitStatus` 加 `potential` 档 + `PotentialVerdict` | Modify（待 plan 决策 A/B） |
| `packages/core/src/supernova_core/renderers/exploit.py` | POTENTIAL 分类渲染（line 73-75 加分类） | Modify（选项 B 时） |

## Testing

1. **config parser**：valid `accounts`；invalid（重复 id、坏 slug、错 usage、attacker 出现在 accounts、accounts 无 authentication）。
2. **validate_authentication**：全成功 / victim 失败降级 / attacker 失败 fail-fast / `auth-state-{id}.json` 命名正确 / `identity-manifest.json` 落盘内容正确。
3. **auth_state_path 参数化**：None → `auth-state.json`，有 id → `auth-state-{id}.json`；旧调用点回归。
4. **cleanup**：glob `auth-state*.json` 清所有快照（含多 identity）。
5. **prompt manager**：多账号注入 manifest + 协议块；单账号注入降级提示；manifest 渲染格式。
6. **exploit_executor**：多账号注入 victim/baseline session 变量；单账号注入单 session（回归）。
7. **端到端**：双账号 config（attacker + victim + admin baseline）跑 `authz-exploit` 产比较协议 verdict；单账号 config 回归现状行为。

## Risks & Assumptions to Verify

- **agent-browser 引擎并发多 session**（必须 smoke check）：attacker / victim / baseline 同时开 3 个浏览器是否被支持。shannon-py 默认 `agent-browser`（非 TS 的 playwright）。类比原始 TS 的 `playwright-cli` session 名假设——本设计依赖 `session_flag(任意字符串)` 支持并发，**plan 阶段首项实测**。
- **GLM 驱动三 session 比较协议**：`authz-exploit` agent 能否正确多 turn 切换 session 做对比——用 `scripts/validate_*_task_probe.py` 类探针在对应引擎实测。
- **并发浏览器成本**：峰值 authz-exploit 3 chromium。on-demand close 缓解。
- **操作者须给 victim 预置私有数据**：否则 horizontal 无可对比资源，协议提示 agent 标 POTENTIAL。
- **`add_exploit` schema 适配**：POTENTIAL 降级的 verdict 表达待定型（见 §4）。

## Out of Scope / Future Extensions

- 自动 victim 账号注册（ROE 敏感、空数据问题）。
- 多租户 `tenant` 字段 / 跨租户隔离测试。
- per-account `login_url`/`login_flow`/`success_condition` override（当前继承全局；真实目标需要独立 admin 登录入口时再加）。
- 白盒 role context（vuln-authz 吃 identity manifest 的 role 列提升静态准确度）——未来可扩展，当前 YAGNI。
- auth（认证）双账号。
- 黑盒独立发现 authz 候选（不依赖白盒 queue）——更大的架构改动，不在本设计范围。
