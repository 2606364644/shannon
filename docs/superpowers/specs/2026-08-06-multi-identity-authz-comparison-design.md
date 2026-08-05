# 黑盒多身份越权对比扫描（Multi-Identity Authz Comparison）— 设计 spec

> 日期：2026-08-06 · 分支：`feat/fork-py` · 状态：设计待审
>
> **子项目 2**（多身份越权对比扫描）。**依赖**子项目 1（认证档案库 `2026-08-05-auth-profile-vault-design.md`，待 plan、未实现）的多角色 `AuthProfileStore`。
>
> **大量复用**前身 spec `2026-07-25-blackbox-dual-account-authz-design.md`（多身份 core 地基：auth-state 参数化 / identity-manifest / session 拓扑 / 比较协议 / 降级铁律，已设计未实现）。本 spec 在其上做两块增量：① web 认证档案衔接 ② 按 role 自动推导 tier（替代前身 spec 的显式 `victim/baseline`）。本 spec 落地后，`2026-07-25` 作为历史前身归档，以本 spec 为准。

## Problem（问题）

shannon-py 黑盒 `authz-exploit` agent 当前只持**单个**登录身份，对越权测试是根本缺陷（非便利性缺口）：

- preflight `validate_authentication` 登录一次，存**一份** `auth-state.json`（`packages/core/src/supernova_core/services/validate_authentication.py:42`，文件名硬编码、无 account_id 参数）。
- 所有 agent 经 `prompts/shared/_shared-session.txt` 共享同一 session / auth-state；`AgentExecutor` 注入单一 `AUTH_STATE_FILE`（`packages/core/src/supernova_core/agents/executor.py:87-92`）。
- core 是硬单身份模型：`Authentication.credentials` 是单个 `Credentials`（非 list，`packages/core/src/supernova_core/models/config.py:36-40`）。
- `authz-exploit.txt` 的 Task Agent 模板要 `Identity set: [list of user IDs/tokens/roles]`，但 config 层只供应一个身份——identity set 实际为空，靠 LLM 运行时自己抓 token 拼凑。

后果：
- **Horizontal (IDOR) 无法产硬证据**：证明"user A 访问了 user B 的私有数据"需要 B 的数据做 **baseline**——要登录成 B。单身份只能枚举 ID 观察 `200`，无法区分"别人的私有数据"和"我本就被允许看的数据/公开数据"。
- **Vertical 缺能力 baseline**：确认"只有 admin 能到这"需要 admin session 确立 admin-only 的样子。单身份做不到。
- **判定可信度低**：无 baseline 佐证的"成功访问"易误报 `EXPLOITED`。

同时，子项目 1 设计的 web 认证档案库（`AuthProfileStore`，每档案多角色 `credentials[]`，每 credential 有 `role` 标签）提供了多身份数据原语，但**只用于单角色验证/选用**（一次扫描展开单 `credentials` 喂 core，core 仍单身份）。多角色档案的越权对比价值未被利用。

## Goal（目标）

让操作者在 web 认证档案里配置多个角色（典型 1 admin + 2 user），黑盒扫描发起时**选档案即跑、不选角色**，系统自动用所有已配角色登录，按 role 自动推导出越权对比矩阵（垂直：低权↔高权；水平：低权↔低权），驱动 `authz-exploit` 跑 baseline↔attacker 对比协议，产出**硬授权证据**（`EXPLOITED`）。角色不足时优雅降级为现状行为（`POTENTIAL`）。

**完全向后兼容**：不配多角色（单身份）时，整条流水线 byte-identical 等同今天。

### Non-Goals

- **不动白盒**：`vuln-authz` 保持纯静态代码分析，不登录、不发请求、不吃 identity manifest（CLAUDE.md §1 双轨：白盒分析产假设、黑盒动态证实）。
- **不动候选来源**：黑盒 `authz-exploit` 仍吃白盒产的 `authz_exploitation_queue.json`。多身份只增强**已有候选的验证证据强度**，不独立发现 authz 候选（纯黑盒无源码仍跑不了 authz——`workflows.py` 无白盒产物会 fail-fast，本 spec 不改这点）。
- **不覆盖 auth（认证）**：多身份只服务 authz（vertical + horizontal）。
- **不自动注册账号**（ROE 风险、空账号问题）。身份由操作者在档案里提供。
- **不做多租户** `tenant` 字段 / 跨租户隔离。
- **不做 per-account 登录配置 override**：所有角色继承档案的 `login_type`/`login_url`/`login_flow`（覆盖"同一登录页、不同凭据"这个常见场景）。
- **不做确定性重放引擎**：多身份对比经 prompt 层 agent 驱动（沿用 2026-07-25），不新建代码层 capture-replay-diff 引擎（复杂多步场景靠 agent 灵活性覆盖；确定性重放作为未来可选增强）。

## Key Decisions

| 决策点 | 选择 | 理由 |
|---|---|---|
| 对比实现层 | **prompt 层 agent 驱动** | 沿用 2026-07-25；scope 最小；agent-browser 多 session 已验证可行；复杂多步场景也能做 |
| 越权目标来源 | **沿用白盒候选** | 多身份只增强验证证据，不动候选发现层；scope 可控；保持黑盒=白盒下游 exploitation-only |
| 角色配对规则 | **按 role 自动分层** | `admin`=高权 baseline，其余=低权 attacker，同低权互为 horizontal baseline；零额外标注 |
| 身份来源 | **web AuthProfileStore**（主）+ CLI `accounts[]`（兼容） | 衔接子项目 1 档案库；core 层用统一 `accounts` 抽象，web/CLI 都映射到它，core 不感知来源 |
| tier 推导归属 | **core helper，web/core 共用** | `derive_privilege_tier(role, high_priv_names)`，单一规则源，避免 web/core 各推一遍 |
| attacker 建模 | **每个低权角色都当 attacker（轮换）** | 每个低权对每个高权做垂直、对其他低权做水平；非 2026-07-25 的固定 primary |
| session 策略 | **N 身份 N session，同时驻留** | 消除 LLM 不擅长的登入登出切换；session_id 任意字符串，agent-browser 原生支持 |
| 登录/exploit 解耦 | **auth-state 文件桥接**（两阶段 session 不同名） | storageState 快照 session 无关，登录循环与 exploit 拓扑完全解耦 |
| 登录失败 | **attacker 必须（fail-fast）；其余降级** | 一个死掉的 baseline/victim 不能拖垮整次扫描 |
| manifest 落盘 | **`identity-manifest.json`（workspace）** | 解耦 temporal activity 间传参 + 可调试 + 降级状态可追溯 |
| 判定铁律 | **无 baseline 一律 `POTENTIAL`** | 收紧单身份下误报 `EXPLOITED` 的口子；多身份的价值正是产出 baseline 佐证的硬证据 |
| 单身份降级 | **仍跑动态尝试，给 `POTENTIAL`** | 不损失现有覆盖，等同现状 |
| POTENTIAL 表达 | **`ExploitStatus` 加 `potential` 档**（2026-07-25 选项 B） | 语义干净、降级可追溯；复用前身 spec 决策 |

## Architecture（数据流）

```
[web 扫描发起页] 选档案(不选角色)
    │
    ▼ 读 AuthProfileStore 该档案所有 credentials[]
[web scan_manager] 调 core derive_privilege_tier(role) 推导 tier
    │  role 命中高权名单 → tier=high; 其余 → tier=low
    │  构建 core Config: authentication(主attacker=首个low) + accounts[](其余身份,各带tier)
    ▼
写 scan-config.yaml (authentication + accounts[], 明文=core合流点约束)
    │
    ▼
[BlackboxScanWorkflow] (core, 复用 2026-07-25 改造 + 本 spec 增量)
    │
    ├─ preflight 登录循环: 每身份独立 session 登录
    │   → auth-state.json(attacker) + auth-state-{id}.json(其余)
    │   → identity-manifest.json (身份清单 + tier + 可用性 + 降级标记)
    │   attacker 失败 → fail-fast; 其余失败 → 标 unavailable 继续
    │
    ├─ 白盒产物检测 (候选来源不变: authz_exploitation_queue.json)
    │
    ├─ authz-exploit (黑盒, 吃白盒候选 + identity-manifest)
    │   注入 N session + 比较协议块(按 tier 矩阵)
    │   ├─ 垂直: low attacker × high baseline → baseline↔attacker 对比
    │   ├─ 水平: low 两两互为 attacker/victim → baseline 对比
    │   ├─ 有 baseline 佐证 → EXPLOITED
    │   └─ 无 baseline(单身份/unavailable) → 现状动态尝试 → POTENTIAL
    │
    └─ cleanup auth-state*.json
```

白盒完全不动（queue 产物不变）；候选来源不变；黑盒仍是白盒下游 exploitation-only。多身份只动了「黑盒 authz-exploit 的验证证据强度」这一环。

## Design

### 1. Data Model & Config（复用 2026-07-25 + tier 增量）

**`packages/core/src/supernova_core/models/config.py`** 新增（对齐 2026-07-25 §1，加 `tier`）：

```python
PrivilegeTier = Literal["high", "low"]

class Account(BaseModel):
    id: str            # ^[a-z0-9-]+$，唯一；决定 auth-state-{id}.json 文件名
    role: str          # 自由文本：admin/user/viewer...，喂 prompt role context
    tier: PrivilegeTier | None = None   # high=admin级(baseline) / low=attacker；None 时 core 用 role 推导兜底
    credentials: Credentials            # 复用现有 Credentials shape（config.py:30-34）

class Config(BaseModel):
    ...
    authentication: Authentication | None = None   # = primary attacker（向后兼容）
    accounts: list[Account] | None = None           # 其余身份（baseline / victim / 低权 attacker）
```

`DistributedConfig` 同步加 `accounts`（黑盒经 `dist_config` 取值，和 `authentication` 同路分发）。

> 与 2026-07-25 的差异：去掉 `usage: victim|baseline` 显式字段，改为 `tier: high|low`。usage 的语义（谁是 attacker/victim/baseline）由 tier + 对比矩阵在运行时推导，不需操作者标注。`role` 保留（喂 prompt 的 role context + tier 推导输入）。

**校验**（`packages/core/src/supernova_core/config/parser.py`）：
- `id` 匹配 `^[a-z0-9-]+$` 且跨 `accounts` 唯一（文件名安全）。
- `accounts` 非空时 `authentication` 必须存在（primary attacker 必须）。
- 每个 `credentials.username` 走现有危险模式检查。
- `tier` 留空时由 core `derive_privilege_tier(role)` 兜底填充（manifest 构建时）。

### 2. role → tier 自动推导（核心增量）

**`packages/core/src/supernova_core/services/authz_identity.py`（新）** 提供 tier 推导 + 对比矩阵生成：

```python
DEFAULT_HIGH_PRIV_ROLES = ["admin"]   # 可经 config/env 扩展

def derive_privilege_tier(role: str, high_priv_roles: list[str] = None) -> PrivilegeTier:
    names = [r.lower().strip() for r in (high_priv_roles or DEFAULT_HIGH_PRIV_ROLES)]
    return "high" if role.lower().strip() in names else "low"
```

**精确匹配，不做子串启发式**：role 归一化（lower + strip）后精确匹配高权名单。不规范文本（"administrator"/"管理员"/"超级管理员"）→ 操作者要么把 role 标成 `admin`，要么扩名单（`SUPERNOVA_AUTHZ_HIGH_PRIV_ROLES` env 或 config）。脆弱的子串匹配（"含 admin 即高权"）刻意不用。

**对比矩阵生成**（`build_comparison_matrix(identities)`）：

- **垂直越权**：每个 `tier=low` 身份 × 每个 `tier=high` 身份。low 是 attacker，high 是 baseline。
- **水平越权**：所有 `tier=low` 身份两两。互为 attacker/victim。
- 配对结果喂比较协议 prompt（§6）。

**admin + user1 + user2 示例**：
```
身份:  admin(high)   user1(low)   user2(low)
垂直:  user1→admin, user2→admin      (low attacker × high baseline)
水平:  user1↔user2                   (low 两两)
```

**「至少 1 个就能跑」的精确语义**——配 1 个角色不阻断扫描，但越权能力取决于组合：

| 配置 | 垂直 | 水平 | authz 结果 |
|---|---|---|---|
| 单角色（任意 1 个） | — | — | 单身份，全 POTENTIAL（=现状） |
| admin + 1 user | ✓ user↔admin | ✗ 只 1 低权 | 垂直可 EXPLOITED，水平降级 |
| 2 user（无 admin） | ✗ 无高权 | ✓ user1↔user2 | 水平可 EXPLOITED，垂直降级 |
| admin + 2 user | ✓ | ✓ | 完整对比 |
| 2+ admin（无低权） | ✗ 无 attacker | ✗ | 单身份级，全 POTENTIAL |

> 水平配对口径：**所有低权两两**，不限同 role 名——水平越权本质是"不同身份访问对方私有数据"，不限 role 名相同。若低权里混了权限梯度（如 user + editor），两层模型刻意不细分（细分属 privilege_level 方案，本 spec 不采用）。

### 3. web 层衔接（扫描发起）

- **AuthProfileStore 零改**：子项目 1 的 `credentials[]` 已有 `role` 标签，tier 由 role 推导，不需额外字段。
- **scan_manager 新增多身份展开**（`packages/web/src/supernova_web/components/scan_manager.py`）：选档案后 → 读该档案所有 credentials → 调 `derive_privilege_tier` 推导 tier → 构建 `Config.accounts[]` + 主 `authentication` → 写 `scan-config.yaml`。
- **主 attacker 选择**：取第一个 `tier=low` credential 作 `authentication`（primary，向后兼容 core 字段）；无低权（纯 admin）→ 取第一个作 primary，单身份降级；其余进 `accounts[]`（各带 tier）。
- **扫描页交互**：选档案即跑（**取代**子项目 1 spec §8 的「选档案+选角色」——子项目 2 取消选角色步骤；子项目 1 实现时扫描页交互以本 spec 为准）。
- `ScanRequest` 增可选 `auth_profile_id`（与 inline `authentication` 二选一）；选档案时 scan_manager 展开多角色 → `scan-config.yaml` → 现有 `BlackboxScanWorkflow`。

### 4. Identity Lifecycle / Preflight 登录循环（复用 2026-07-25 §2）

**auth-state 路径参数化**（`validate_authentication.py:42`）：

```python
def auth_state_path(workspace, account_id: str | None = None) -> Path:
    name = "auth-state.json" if account_id is None else f"auth-state-{account_id}.json"
    return Path(workspace) / name
```

无 `account_id` → `auth-state.json`（旧调用点零改动）。`cleanup_auth_state` / `_sync` 改 glob `auth-state*.json`。

**登录循环**（`validate_authentication` 改造）：构建 `[primary(attacker), *accounts]`，逐个登录。每个 identity：
- 用**独立 session**（`validate-auth-{id}`）登录，避免 cookie/storage 串。
- 存对应 `auth-state-{id}.json`（attacker 存 `auth-state.json`）。
- 跑现有 `verify_auth_state`（cookies/origins 非空校验，复用）。

**失败处理**：attacker(primary) 失败 → fail-fast；其余失败 → 标 `available=False` 继续。

**产物 `identity-manifest.json`**（落盘 workspace）：

```python
@dataclass
class IdentityRecord:
    account_id: str        # "primary" / "victim_b" / "admin"
    role: str              # "user" / "admin"
    tier: str              # "high" / "low"
    auth_state_file: str   # "auth-state.json" / "auth-state-victim_b.json"
    session_id: str        # exploit 阶段 session 名（见 §5）
    available: bool
    failure_detail: str | None

@dataclass
class IdentityManifest:
    identities: list[IdentityRecord]   # attacker 恒 available（否则 fail-fast 不返回）
```

> 与 2026-07-25 的差异：`IdentityRecord` 加 `tier` 字段；`usage` 字段由 tier + 矩阵在运行时推导，不再持久化固定语义。

### 5. Session Topology（增量：N session）

shannon-py 的 `BrowserEngine.session_flag(session_id: str)`（`packages/core/src/supernova_core/services/browser_engine.py:36`）接受**任意字符串** session id——直接派生新 session 名，零枚举改动（优于原始 TS）。

**拓扑**（`authz-exploit` 运行时持 N 个独立 session，N=身份数）：

| 身份 | session id | auth-state 文件 |
|---|---|---|
| primary attacker | `authz-exploit`（现状不变） | `auth-state.json` |
| account `{id}` | `authz-exploit-{id}` | `auth-state-{id}.json` |

派生用 helper `get_identity_session_id(agent_name, account_id)`（扩 `packages/core/src/supernova_core/services/playwright_config_writer.py` 的 `get_session_id`）。

**auth-state 桥接（关键解耦）**：auth-state 文件是 playwright storageState 快照（cookies + origins），**session 无关**——可 load 进任意 session。登录阶段（`validate-auth-{id}`）与 exploit 阶段（`authz-exploit-{id}`）session 不同名，靠 auth-state 文件桥接，完全解耦。

**资源控制**：峰值 `authz-exploit` 开 N 个 chromium（admin+2user=3）。协议指导 agent 不比较时关 session 浏览器（保留 auth-state 按需重开），降并发足迹。N 大时成本线性涨（见 Risks）。

> 与 2026-07-25 的差异：前身固定 attacker/victim/baseline 3 session；本 spec 是 N 身份 N session（attacker 轮换，每个低权都作 attacker 对比高权 + 其他低权）。

### 6. Comparison Protocol & Degradation（复用 2026-07-25 §4 + tier 矩阵）

落到 `prompts/authz-exploit.txt` 现有的 horizontal/vertical/context 攻击模式——方法论已齐全，插一块按 tier 推导的比较协议。

**比较协议（identity manifest 含 ≥2 可用身份时强制执行）**：

- **Horizontal (IDOR) — low 身份互为 baseline**：
  1. victim(low) session 读**自己的**资源 `/orders/42` → baseline 数据（victim 合法拥有）。
  2. attacker(low) session 读同一 `/orders/42`。
  3. attacker 可读 + 数据匹配 victim baseline ⇒ **EXPLOITED**（硬证据：跨用户私有数据访问）。
- **Vertical — high 身份做能力基准**：
  1. baseline(high/admin) session 访问目标端点 `/admin/users` → 确认端点存在且为 admin 能力（正常响应/数据形状）。
  2. attacker(low) session 访问同一端点。
  3. attacker 可访问 + 获得等价能力 ⇒ **EXPLOITED**（baseline 确立"admin-only 能力"，attacker 能 reach 即越权）。

**矩阵驱动**：对比对由 §2 `build_comparison_matrix` 产出的 (attacker, baseline/victim) 对决定，agent 按 manifest 的 tier 关系执行，不固定单一 victim/baseline。

**降级判定**：

| 场景 | 行为 | 判定 |
|---|---|---|
| 对应方向 baseline/victim available | 走比较协议 | 满足 ⇒ EXPLOITED |
| horizontal 无 low victim（只 1 低权） | 退"枚举 ID 观察 200"——能读但无法证明是别人私有数据 | 强制 POTENTIAL |
| vertical 无 high baseline（无 admin） | 退"低权 reach 高权端点"——能 reach 但无法确认是 admin-only 能力 | 强制 POTENTIAL |
| 完全单身份（无 accounts） | 全降级 = 现状行为 | POTENTIAL |

**判定铁律**：EXPLOITED 必须有 baseline 对比佐证（horizontal: victim 数据 baseline；vertical: admin 能力 baseline）。**无 baseline 的任何"成功访问"一律 POTENTIAL，不得 EXPLOITED**——比现状更严（现状单身份可能误报 EXPLOITED）。

**prompt 落地（insert-only，向后兼容）**：
- `prompts/authz-exploit.txt` 在 `<attack_patterns>` 前插入 `<comparison_protocol>` 块（上述协议文本 + tier 矩阵说明）。
- 新增 `prompts/shared/_identities.txt` partial：渲染 identity manifest 表（含 tier）+ 比较协议块。
- `prompts/manager.py` 新增 `build_identity_context(manifest)`：多身份 → manifest 表 + 协议块；单身份 → 降级提示（明确告诉 agent"无 baseline，成功访问只能给 POTENTIAL"）。
- `authz-exploit.txt` 的 Task Agent "Identity set" 模板：从"LLM 自己拼"改成"读 manifest，用系统注入的真实 identity session"。
- 单身份时 `@include(shared/_shared-session.txt)` 保留不变（向后兼容）；多身份时替换为 `@include(shared/_identities.txt)`。

**POTENTIAL 在 verdict schema**：`ExploitStatus`（`packages/core/src/supernova_core/models/exploit_verdict_schemas.py:15-17`）加 `potential` 档 + 新 `PotentialVerdict`（severity/confidence/downgrade_reason/evidence_of_vulnerability）+ renderer（`packages/core/src/supernova_core/renderers/exploit.py:73-75`）加分类。沿用 2026-07-25 选项 B（语义干净、降级可追溯）。

### 7. Error Handling & Backward Compatibility（复用 2026-07-25 §5）

**失败矩阵**：

| attacker | 高权 baseline | 低权 victim | 行为 |
|---|---|---|---|
| ✓ | —（无 accounts） | — | 现状：单身份，POTENTIAL |
| ✓ | ✓ | ✓ | 完整比较协议 → 可达 EXPLOITED |
| ✓ | ✗ | ✓ | vertical 降级 POTENTIAL，horizontal 正常 |
| ✓ | ✓ | ✗ | horizontal 降级 POTENTIAL，vertical 正常 |
| ✓ | ✗ | ✗ | 全降级 = 现状 |
| ✗ | * | * | fail-fast，扫描终止 |

**向后兼容铁律**：
- 无 `accounts` → byte-identical 等同今天。
- 旧 config 零改动；`auth-state.json` 主文件名不变；单 session 拓扑保留。
- `auth_state_path(None)` / `cleanup_auth_state` 旧调用点零改动。
- `exploit_executor` 单身份时注入单 `browser_session_id`（现状），多身份时注入 N session 变量。

## Files Changed

| 文件 | 改动 | 类型 |
|---|---|---|
| `packages/core/src/supernova_core/models/config.py` | `Account`（含 `tier`）、`PrivilegeTier`；`Config.accounts` + `DistributedConfig.accounts` | Modify |
| `packages/core/src/supernova_core/config/parser.py` | `accounts` 校验（slug/唯一/依赖 authentication）+ 分发 | Modify |
| `packages/core/src/supernova_core/services/authz_identity.py` | `derive_privilege_tier` + `build_comparison_matrix` | **New** |
| `packages/core/src/supernova_core/services/validate_authentication.py` | `auth_state_path` 参数化；登录循环；`IdentityManifest`/`IdentityRecord`（含 tier）；`identity-manifest.json` 落盘；cleanup glob | Modify |
| `packages/core/src/supernova_core/services/playwright_config_writer.py` | `get_identity_session_id(agent_name, account_id)` helper | Modify |
| `packages/core/src/supernova_core/prompts/manager.py` | `build_identity_context(manifest)`；多 session 变量注入；manifest 渲染 | Modify |
| `packages/blackbox/src/supernova_blackbox/agents/exploit_executor.py` | 读 `identity-manifest.json`；多身份注入 N session 变量；单身份回归 | Modify |
| `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py` | preflight 登录循环编排；manifest 落盘传递 | Modify |
| `prompts/shared/_identities.txt` | identity manifest partial（manifest 表含 tier + 比较协议块） | **New** |
| `prompts/authz-exploit.txt` | `<comparison_protocol>` 块（insert-only）+ Task Agent identity 模板改注入 | Insert/Modify |
| `packages/core/src/supernova_core/models/exploit_verdict_schemas.py` | `ExploitStatus` 加 `potential` 档 + `PotentialVerdict` | Modify |
| `packages/core/src/supernova_core/renderers/exploit.py` | POTENTIAL 分类渲染（line 73-75 加分类） | Modify |
| `packages/web/src/supernova_web/components/scan_manager.py` | 多身份展开（AuthProfileStore → accounts，调 `derive_privilege_tier`） | Modify |
| `packages/web/src/supernova_web/models.py` | `ScanRequest` 增 `auth_profile_id`（与 inline authentication 二选一） | Modify |
| `configs/*.example.yaml` | `accounts` 示例（additive） | Modify |

> worker activity 注册护栏：本 spec 不新增 blackbox activity（`run_exploit_agent`/`run_blackbox_auth_validation` 已存在，只改实现），故 `worker.py:137-149` / `runner.py` 注册列表不动。若 plan 阶段拆出新 activity，须同步两个 worker + 两个 `assert_all_activities_registered` test（`test_worker.py` / `test_runner.py`）。

## Testing（TDD）

1. **tier 推导**（`authz_identity.py`）：`derive_privilege_tier`（admin→high、user→low、归一化、名单扩展、空 role）；`build_comparison_matrix`（admin+2user、单角色、2 user、2 admin 各产正确对）。
2. **config parser**：valid `accounts`；invalid（重复 id、坏 slug、accounts 无 authentication）；`tier` 留空时 core 兜底推导。
3. **validate_authentication**：全成功 / 高权失败降级 / 低权 victim 失败降级 / attacker 失败 fail-fast / `auth-state-{id}.json` 命名 / `identity-manifest.json`（含 tier）落盘内容正确。
4. **auth_state_path 参数化**：None → `auth-state.json`，有 id → `auth-state-{id}.json`；旧调用点回归。
5. **cleanup**：glob `auth-state*.json` 清所有快照。
6. **prompt manager**：多身份注入 manifest（含 tier）+ 协议块；单身份注入降级提示；manifest 渲染格式。
7. **exploit_executor**：多身份注入 N session 变量；单身份注入单 session（回归）。
8. **web scan_manager**：选档案 → 读多 credentials → 推导 tier → 构建 accounts + 主 authentication；单角色档案降级。
9. **端到端**：admin+2user config 跑 `authz-exploit` 产比较协议 EXPLOITED verdict；单身份 config 回归现状 POTENTIAL。
10. **GLM 驱动 N session 比较协议探针**（能力风险实测）：类 `scripts/validate_*_task_probe.py`，在对应引擎验证 GLM 能正确多 turn 切换 session 做 baseline↔attacker 对比。

## Risks & Assumptions to Verify

- **R1 GLM 驱动 N session 比较协议**（核心能力风险）：`authz-exploit` agent 能否正确多 turn 切换 N session 做对比。plan 阶段首项探针实测（类 `validate_*_task_probe.py`）。
- **R2 agent-browser 引擎并发多 session**：N 个浏览器同时 load 不同 auth-state 是否被支持。设计上支持（session_id 任意字符串 + per-session profile），未实测 N≥3。plan 阶段 smoke check。
- **R3 N chromium 并发成本**：峰值 N 个 chromium。on-demand close 缓解；N 大时成本线性涨（建议档案角色数 ≤4）。
- **R4 依赖子项目 1 前置**：`AuthProfileStore` 多角色档案未实现。子项目 1 须先落地（或本 spec 的 web 衔接部分待子项目 1 就绪）。
- **R5 role 文本规范化**：高权默认认 `admin`（精确匹配）。非标 role 名需操作者改标签或扩名单。
- **R6 改 web/worker src 须 rebuild 镜像**：plan/冒烟阶段注意（`supernova-worker` + web）。
- **R7 scan-config.yaml 多账号明文债**：core 合流点约束（core 不解密），per-scan YAML 须明文。缓解：0600 权限 + 扫描结束即删（同子项目 1 §2.1 明文边界）。
- **R8 操作者须给 victim 预置私有数据**：否则 horizontal 无可对比资源，协议提示 agent 标 POTENTIAL。

## 不变量

1. **白盒 vuln-authz 纯静态零改**：不登录、不发请求、不吃 identity manifest（CLAUDE.md §1 双轨）。
2. **候选来源不变**：黑盒 authz-exploit 仍吃白盒 `authz_exploitation_queue.json`；黑盒=白盒下游 exploitation-only。
3. **无 accounts = 现状**：byte-identical 等同今天；单身份全 POTENTIAL。
4. **scan-config.yaml 合流点明文**：core 不解密，per-scan YAML 须明文（core 合流点约束）。
5. **双引擎一致**：多身份对比经 `run_claude_prompt` 统一抽象（CLAUDE.md §2）。
6. **判定铁律**：无 baseline 一律 POTENTIAL，不得 EXPLOITED。

## 与子项目 1 / 2026-07-25 的关系

- **子项目 1**（`2026-08-05-auth-profile-vault-design.md`）：本 spec 的前置依赖。提供 web 多角色 `AuthProfileStore`（`credentials[]` + `role` 标签 + Fernet 加密）。本 spec **不改** AuthProfileStore schema（tier 由 role 推导），只在其上做 scan_manager 多身份展开。**扫描页交互**：子项目 1 §8 原设计「选档案+选角色」，本 spec 取消选角色步骤（选档案即跑，自动用所有角色）；子项目 1 实现时扫描页以本 spec 为准。子项目 1 的认证管理页「逐角色测试登录」不变。
- **2026-07-25**（`blackbox-dual-account-authz-design.md`）：本 spec 的技术地基前身。复用其 auth-state 参数化 / 登录循环 / identity-manifest / session 桥接 / 比较协议 / 降级铁律 / POTENTIAL verdict / 向后兼容。**差异**：① 身份来源从 CLI `accounts[]` 扩展为 web AuthProfileStore + CLI 双兼容；② 角色建模从显式 `usage: victim|baseline` 改为 `tier: high|low` + role 自动推导；③ attacker 从固定 primary 改为低权轮换；④ session 从固定 3 改为 N。本 spec 落地后 2026-07-25 归档为历史前身。
