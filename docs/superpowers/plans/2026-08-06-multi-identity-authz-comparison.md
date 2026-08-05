# 黑盒多身份越权对比扫描 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让黑盒扫描选认证档案即跑，自动用档案里所有角色登录、按 role 推导越权对比矩阵，驱动 authz-exploit 产 baseline 佐证的硬证据（EXPLOITED），单身份降级 POTENTIAL。

**Architecture:** 在 2026-07-25 多身份 core 地基上做两块增量——① 衔接子项目 1 已实现的 web AuthProfileStore（多角色 credentials[]）② 按 role 自动推导 tier 替代显式 victim/baseline。core 加 `Config.accounts` + tier；preflight 登录循环产 per-identity auth-state + identity-manifest；authz-exploit 单 call 内吃 manifest + N session 块，agent 按比较协议在 session 间切换；无 baseline 一律 POTENTIAL。白盒 vuln-authz 零改，候选来源不变。

**Tech Stack:** Python 3.11 / pydantic v2 / temporalio / pytest（core+blackbox+web 后端）；prompts 纯文本 + `@include` partial。

## Global Constraints

- **白盒 vuln-authz 纯静态零改**：不登录/不发请求/不吃 manifest（CLAUDE.md §1 双轨）。
- **候选来源不变**：黑盒 authz-exploit 仍吃白盒 `authz_exploitation_queue.json`。
- **无 accounts = 现状**：`Config.accounts` 默认空，整条流水线 byte-identical 等同今天；旧 config 零改动。
- **scan-config.yaml 合流点明文**：accounts 写进 YAML（core 经 config_path 读），core 不解密。
- **AUTH_STATE_FILE 由 AgentExecutor 基层注入**（`executor.py:91`）：exploit_executor **不得**显式传 AUTH_STATE_FILE（`test_executors.py:216-232` 守卫锁定）；多 session 走 manifest 变量。
- **N 身份 = 单次 ExploitExecutor call**：prompt 喂 manifest + N session 块，agent 切 session；**非循环 N 次 call**。
- **manifest 经 workspace_path 流转**：`<workspace_path>/identity-manifest.json` 约定位置；**不改 `BlackboxPipelineInput`/`shared.py`**。
- **accounts 经 config_path 流转**：web 写 scan-config.yaml（含 accounts）→ core parse_config 读；**不改 BlackboxPipelineInput**。
- **extra="ignore" 不变**：Account/Config 不加 `model_config = ConfigDict(extra="forbid")`（否则 `test_legacy_success_condition_ignored` 红）。
- **`distribute_config`（parser.py:236-253）须透传 accounts** 到 `DistributedConfig`。
- **判定铁律**：无 baseline 一律 POTENTIAL，不得 EXPLOITED。
- **改 web/worker src 须 rebuild `supernova-worker` 镜像**（冒烟前）。
- **只跑改动相关测试**：勿广跑全套（Temporal/网络慢测试会 hang）。

---

### Task 1: core 数据模型 — Account + Config.accounts + tier

**Files:**
- Modify: `packages/core/src/supernova_core/models/config.py`（L18 后加 AccountTier 别名；L40 后加 Account 类；Config L60 后加 accounts；DistributedConfig L54 后加 accounts）
- Modify: `packages/core/src/supernova_core/config/parser.py`（加 `_validate_accounts`，L232 后调用；`distribute_config` L236-253 透传 accounts）
- Test: `packages/core/tests/test_config.py`（模型构造）
- Test: `packages/core/tests/test_parser.py`（YAML 端到端校验）

**Interfaces:**
- Produces: `Account(id, credentials, role, tier)`, `AccountTier = Literal["high","low"]`, `Config.accounts: list[Account]`, `DistributedConfig.accounts: list[Account]`；`_validate_accounts(config)`（parser 内部）

- [ ] **Step 1: 写失败测试 — Account 模型构造（test_config.py 追加）**

```python
def test_account_model_construction():
    from supernova_core.models.config import Account, AccountTier, Credentials
    acct = Account(
        id="victim-b", credentials=Credentials(username="userB", password="x"),
        role="user", tier="low",
    )
    assert acct.id == "victim-b"
    assert acct.tier == "low"

def test_config_has_accounts_default_empty():
    from supernova_core.models.config import Config
    c = Config()
    assert c.accounts == []

def test_config_with_accounts():
    from supernova_core.models.config import Config, Account, Credentials
    c = Config(accounts=[Account(id="a1", credentials=Credentials(username="u"), role="admin", tier="high")])
    assert len(c.accounts) == 1
    assert c.accounts[0].tier == "high"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/core && python -m pytest tests/test_config.py::test_account_model_construction tests/test_config.py::test_config_has_accounts_default_empty tests/test_config.py::test_config_with_accounts -x`
Expected: FAIL（ImportError / 无 accounts 字段）

- [ ] **Step 3: 实现 — config.py 加 Account/tier/accounts**

L18（`Confidence` Literal 别名后）加：
```python
AccountTier = Literal["high", "low"]
```
L40（`Authentication` 类后、`PipelineConfig` 前）加：
```python
class Account(BaseModel):
    id: str
    credentials: Credentials
    role: str | None = None
    tier: AccountTier
```
`Config` 类 L60（`authentication` 字段后）加：`accounts: list[Account] = []`
`DistributedConfig` L54（`authentication` 字段后）加：`accounts: list[Account] = []`

- [ ] **Step 4: 跑测试确认通过**

Run: 同 Step 2
Expected: PASS

- [ ] **Step 5: 写失败测试 — parser accounts 校验（test_parser.py 追加）**

```python
def test_parse_config_valid_accounts(tmp_path):
    from supernova_core.config.parser import parse_config
    config_path = _write_config(tmp_path, """
authentication:
  login_type: form
  login_url: https://example.com/login
  credentials: { username: userA, password: "***" }
accounts:
  - id: victim-b
    role: user
    tier: low
    credentials: { username: userB, password: "***" }
  - id: admin-1
    role: admin
    tier: high
    credentials: { username: admin, password: "***" }
""")
    config = parse_config(config_path)
    assert len(config.accounts) == 2
    assert config.accounts[0].tier == "low"

def test_parse_config_rejects_bad_account_slug(tmp_path):
    config_path = _write_config(tmp_path, """
authentication:
  login_type: form
  login_url: https://x/login
  credentials: { username: u, password: p }
accounts:
  - { id: "Bad ID!", role: user, tier: low, credentials: { username: u2 } }
""")
    with pytest.raises(PentestError, match="must match"):
        parse_config(config_path)

def test_parse_config_rejects_duplicate_account_id(tmp_path):
    config_path = _write_config(tmp_path, """
authentication: { login_type: form, login_url: https://x/login, credentials: { username: u, password: p } }
accounts:
  - { id: dup, role: user, tier: low, credentials: { username: a } }
  - { id: dup, role: admin, tier: high, credentials: { username: b } }
""")
    with pytest.raises(PentestError, match="duplicate"):
        parse_config(config_path)

def test_parse_config_rejects_accounts_without_authentication(tmp_path):
    config_path = _write_config(tmp_path, """
accounts:
  - { id: a1, role: user, tier: low, credentials: { username: u } }
""")
    with pytest.raises(PentestError, match="authentication"):
        parse_config(config_path)
```
（`_write_config`、`PentestError`、`pytest` 已在 test_parser.py 顶部 import，沿用现有 helper。）

- [ ] **Step 6: 跑测试确认失败**

Run: `cd packages/core && python -m pytest tests/test_parser.py::test_parse_config_valid_accounts tests/test_parser.py::test_parse_config_rejects_bad_account_slug -x`
Expected: FAIL（无 accounts 解析/无校验）

- [ ] **Step 7: 实现 — parser.py 加 _validate_accounts + distribute_config 透传**

parser.py 顶部加 `import re`（若未有），加模块常量：
```python
_ACCOUNT_ID_RE = re.compile(r"^[a-z0-9-]+$")
```
加校验函数（放 `_validate_config_security` 附近）：
```python
def _validate_accounts(config: Config) -> None:
    seen: set[str] = set()
    if config.accounts and config.authentication is None:
        raise PentestError(
            "accounts requires top-level authentication (shared login_url/login_flow)",
            "config", error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
        )
    for i, acct in enumerate(config.accounts):
        if not _ACCOUNT_ID_RE.match(acct.id):
            raise PentestError(
                f"accounts[{i}].id '{acct.id}' must match ^[a-z0-9-]+$",
                "config", error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
                context={"index": i, "id": acct.id},
            )
        if acct.id in seen:
            raise PentestError(
                f"accounts[{i}].id '{acct.id}' is duplicate",
                "config", error_code=ErrorCode.CONFIG_VALIDATION_FAILED,
                context={"duplicate_id": acct.id},
            )
        seen.add(acct.id)
        _check_dangerous_patterns(acct.credentials.username, f"accounts[{i}].credentials.username")
```
`parse_config`（L232 `_validate_url_path_rules` 后、`return config` 前）加：`_validate_accounts(config)`
`distribute_config`（L236-253）构造 `DistributedConfig(...)` 时加 `accounts=config.accounts`。

- [ ] **Step 8: 跑测试确认通过**

Run: `cd packages/core && python -m pytest tests/test_parser.py -k "account" tests/test_config.py -k "account or accounts" -v`
Expected: PASS

- [ ] **Step 9: 回归 — 现有 config/parser 测试不破**

Run: `cd packages/core && python -m pytest tests/test_config.py tests/test_parser.py -x`
Expected: PASS（含 `test_legacy_success_condition_ignored`、`test_distribute_config_full`）

- [ ] **Step 10: Commit**

```bash
git add packages/core/src/supernova_core/models/config.py packages/core/src/supernova_core/config/parser.py packages/core/tests/test_config.py packages/core/tests/test_parser.py
git commit -m "feat(core): Account/tier 模型 + Config.accounts + parser 校验(子项目2 T1)"
```

---

### Task 2: tier 推导 + 对比矩阵 helper

**Files:**
- Create: `packages/core/src/supernova_core/utils/authz_identity.py`
- Test: `packages/core/tests/test_authz_identity.py`（flat，对齐 test_security.py 布局）

**Interfaces:**
- Consumes: `Account`、`AccountTier` from `supernova_core.models.config`
- Produces: `derive_privilege_tier(role, high_priv_names) -> AccountTier`；`ComparisonPair`（dataclass）；`build_comparison_matrix(identities) -> list[ComparisonPair]`

- [ ] **Step 1: 写失败测试 — derive_privilege_tier + build_comparison_matrix**

```python
from supernova_core.utils.authz_identity import (
    derive_privilege_tier, build_comparison_matrix, ComparisonPair)
from supernova_core.models.config import Account, Credentials

class TestDerivePrivilegeTier:
    def test_admin_is_high(self):
        assert derive_privilege_tier("admin", ["admin"]) == "high"

    def test_user_is_low(self):
        assert derive_privilege_tier("user", ["admin"]) == "low"

    def test_case_insensitive(self):
        assert derive_privilege_tier(" Admin ", ["admin"]) == "high"

    def test_none_role_is_low(self):
        assert derive_privilege_tier(None, ["admin"]) == "low"

    def test_empty_high_priv_names_all_low(self):
        assert derive_privilege_tier("admin", []) == "low"

    def test_custom_high_priv_names(self):
        assert derive_privilege_tier("root", ["root", "superuser"]) == "high"

def _acct(id_, role, tier):
    return Account(id=id_, credentials=Credentials(username=id_), role=role, tier=tier)

class TestBuildComparisonMatrix:
    def test_empty_identities(self):
        assert build_comparison_matrix([]) == []

    def test_single_identity_returns_empty(self):
        assert build_comparison_matrix([_acct("admin", "admin", "high")]) == []

    def test_admin_plus_two_users(self):
        admin = _acct("admin", "admin", "high")
        u1 = _acct("user1", "user", "low")
        u2 = _acct("user2", "user", "low")
        pairs = build_comparison_matrix([admin, u1, u2])
        kinds = {(p.attacker_id, p.baseline_id, p.kind) for p in pairs}
        # 垂直: user1→admin, user2→admin
        assert ("user1", "admin", "vertical") in kinds
        assert ("user2", "admin", "vertical") in kinds
        # 水平: user1↔user2 (两个方向)
        assert ("user1", "user2", "horizontal") in kinds
        assert ("user2", "user1", "horizontal") in kinds

    def test_no_pair_within_same_tier_high(self):
        a1 = _acct("a1", "admin", "high")
        a2 = _acct("a2", "admin", "high")
        assert build_comparison_matrix([a1, a2]) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/core && python -m pytest tests/test_authz_identity.py -x`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 — utils/authz_identity.py**

```python
"""Privilege-tier derivation and identity-pair matrix for authz multi-identity testing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from supernova_core.models.config import Account, AccountTier


def derive_privilege_tier(role: str | None, high_priv_names: list[str]) -> AccountTier:
    """Return 'high' if role (case/stripped-normalized) is in high_priv_names, else 'low'."""
    if not role:
        return "low"
    names = {n.lower().strip() for n in (high_priv_names or [])}
    return "high" if role.lower().strip() in names else "low"


@dataclass(frozen=True)
class ComparisonPair:
    attacker_id: str
    baseline_id: str
    kind: Literal["vertical", "horizontal"]


def build_comparison_matrix(identities: list[Account]) -> list[ComparisonPair]:
    """Build ordered (attacker, baseline) pairs: low×high=vertical, low×low=horizontal."""
    if len(identities) < 2:
        return []
    highs = [i for i in identities if i.tier == "high"]
    lows = [i for i in identities if i.tier == "low"]
    pairs: list[ComparisonPair] = []
    for attacker in lows:                       # vertical: low attacker × high baseline
        for baseline in highs:
            pairs.append(ComparisonPair(attacker.id, baseline.id, "vertical"))
    for i, a in enumerate(lows):                 # horizontal: low 两两（双向）
        for b in lows[i + 1:]:
            pairs.append(ComparisonPair(a.id, b.id, "horizontal"))
            pairs.append(ComparisonPair(b.id, a.id, "horizontal"))
    return pairs
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd packages/core && python -m pytest tests/test_authz_identity.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/utils/authz_identity.py packages/core/tests/test_authz_identity.py
git commit -m "feat(core): derive_privilege_tier + build_comparison_matrix(子项目2 T2)"
```

---

### Task 3: auth_state_path 参数化 + cleanup glob

**Files:**
- Modify: `packages/core/src/supernova_core/services/validate_authentication.py`（L42-43 auth_state_path；L46-57 cleanup）
- Test: `packages/core/tests/test_validate_authentication.py`

**Interfaces:**
- Produces: `auth_state_path(workspace_path, account_id=None) -> Path`（None→`auth-state.json`，有 id→`auth-state-{id}.json`）；`cleanup_auth_state`/`_sync` 改 glob `auth-state*.json`

- [ ] **Step 1: 写失败测试 — auth_state_path 参数化**

```python
def test_auth_state_path_default_is_primary():
    from supernova_core.services.validate_authentication import auth_state_path
    assert auth_state_path("/ws").name == "auth-state.json"

def test_auth_state_path_with_account_id():
    from supernova_core.services.validate_authentication import auth_state_path
    assert auth_state_path("/ws", "victim-b").name == "auth-state-victim-b.json"

def test_auth_state_path_primary_explicit():
    from supernova_core.services.validate_authentication import auth_state_path
    assert auth_state_path("/ws", None).name == "auth-state.json"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/core && python -m pytest tests/test_validate_authentication.py::test_auth_state_path_with_account_id -x`
Expected: FAIL（auth_state_path 不接受 account_id）

- [ ] **Step 3: 实现 — auth_state_path 参数化**

L42-43 改：
```python
def auth_state_path(workspace_path: str | Path, account_id: str | None = None) -> Path:
    name = "auth-state.json" if account_id is None else f"auth-state-{account_id}.json"
    return Path(workspace_path) / name
```

- [ ] **Step 4: 写失败测试 — cleanup glob 多文件**

```python
def test_cleanup_auth_state_sync_removes_all_identity_files(tmp_path):
    from supernova_core.services.validate_authentication import cleanup_auth_state_sync
    (tmp_path / "auth-state.json").write_text("{}")
    (tmp_path / "auth-state-victim-b.json").write_text("{}")
    (tmp_path / "auth-state-admin-1.json").write_text("{}")
    (tmp_path / "other.json").write_text("{}")
    cleanup_auth_state_sync(str(tmp_path))
    assert not (tmp_path / "auth-state.json").exists()
    assert not (tmp_path / "auth-state-victim-b.json").exists()
    assert not (tmp_path / "auth-state-admin-1.json").exists()
    assert (tmp_path / "other.json").exists()  # 不误删
```

- [ ] **Step 5: 跑测试确认失败**

Run: `cd packages/core && python -m pytest tests/test_validate_authentication.py::test_cleanup_auth_state_sync_removes_all_identity_files -x`
Expected: FAIL（只删单文件）

- [ ] **Step 6: 实现 — cleanup 改 glob**

L46-57 改（async + sync 都用 glob）：
```python
async def cleanup_auth_state(workspace_path: str | Path) -> None:
    import glob as _glob, aiofiles.os
    for f in _glob.glob(str(Path(workspace_path) / "auth-state*.json")):
        await aiofiles.os.remove(f)

def cleanup_auth_state_sync(workspace_path: str | Path) -> None:
    import glob as _glob
    for f in _glob.glob(str(Path(workspace_path) / "auth-state*.json")):
        Path(f).unlink(missing_ok=True)
```

- [ ] **Step 7: 跑测试 + 回归**

Run: `cd packages/core && python -m pytest tests/test_validate_authentication.py -k "auth_state_path or cleanup" -v`
Expected: PASS

- [ ] **Step 8: 回归现有 validate_authentication 测试**

Run: `cd packages/core && python -m pytest tests/test_validate_authentication.py -x`
Expected: PASS（auth_state_path(None) 旧调用点零改动兼容）

- [ ] **Step 9: Commit**

```bash
git add packages/core/src/supernova_core/services/validate_authentication.py packages/core/tests/test_validate_authentication.py
git commit -m "feat(core): auth_state_path 参数化 + cleanup glob(子项目2 T3)"
```

---

### Task 4: identity-manifest + preflight 登录循环

**Files:**
- Modify: `packages/core/src/supernova_core/services/validate_authentication.py`（加 IdentityRecord/IdentityManifest dataclass；改 validate_authentication 为登录循环；落盘 identity-manifest.json）
- Test: `packages/core/tests/test_validate_authentication.py`

**Interfaces:**
- Consumes: `Config.accounts`、`derive_privilege_tier`、`auth_state_path(ws, account_id)`
- Produces: `IdentityRecord`、`IdentityManifest`（dataclass，落盘 `<workspace>/identity-manifest.json`）；`validate_authentication` 多身份循环（attacker 失败 fail-fast、其余 unavailable 降级）

- [ ] **Step 1: 加 IdentityRecord/IdentityManifest dataclass**

validate_authentication.py L39（`AuthValidationResult` 后）加：
```python
@dataclass
class IdentityRecord:
    account_id: str            # "primary" 或 account.id
    role: str | None
    tier: str                  # "high" | "low"
    auth_state_file: str       # "auth-state.json" | "auth-state-{id}.json"
    available: bool
    failure_detail: str | None = None

@dataclass
class IdentityManifest:
    identities: list[IdentityRecord]

    def write(self, workspace_path: str | Path) -> Path:
        import json
        p = Path(workspace_path) / "identity-manifest.json"
        p.write_text(json.dumps({
            "identities": [r.__dict__ for r in self.identities]
        }, ensure_ascii=False), encoding="utf-8")
        return p

def load_identity_manifest(workspace_path: str | Path) -> IdentityManifest | None:
    import json
    p = Path(workspace_path) / "identity-manifest.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return IdentityManifest(identities=[IdentityRecord(**d) for d in data.get("identities", [])])
```

- [ ] **Step 2: 写失败测试 — 多身份登录循环产 manifest**

```python
@pytest.mark.asyncio
async def test_multi_identity_login_loop_writes_manifest(tmp_path):
    """accounts 非空时，每个 identity 各登录一次，产 identity-manifest.json。"""
    from supernova_core.models.config import Config, Authentication, Credentials, Account
    from supernova_core.services.validate_authentication import load_identity_manifest

    async def fake_execute(**kwargs):
        sf = kwargs["prompt_variables"]["AUTH_STATE_FILE"]
        Path(sf).write_text(json.dumps({"cookies": [{"name": "s"}], "origins": []}))
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=100, structured_output={"login_success": True})

    mock_executor = MagicMock(); mock_executor.execute = AsyncMock(side_effect=fake_execute)
    mock_pm = MagicMock()
    cfg = Config(authentication=Authentication(login_type="form", login_url="https://x/login",
        credentials=Credentials(username="userA", password="p")),
        accounts=[
            Account(id="victim-b", role="user", tier="low", credentials=Credentials(username="userB")),
            Account(id="admin-1", role="admin", tier="high", credentials=Credentials(username="admin")),
        ])
    with patch("supernova_core.config.parser.parse_config", return_value=cfg), \
         patch("supernova_core.config.parser.distribute_config",
               return_value=MagicMock(authentication=cfg.authentication, accounts=cfg.accounts)):
        result = await validate_authentication(
            web_url="https://x", config_path="/c.yaml", workspace_path=str(tmp_path),
            prompt_manager=mock_pm, executor=mock_executor)
    assert result.success is True
    assert mock_executor.execute.call_count == 3  # primary + 2 accounts
    manifest = load_identity_manifest(tmp_path)
    assert manifest is not None
    ids = {r.account_id for r in manifest.identities}
    assert ids == {"primary", "victim-b", "admin-1"}
    assert all(r.available for r in manifest.identities)
    # 每个 identity 落不同 auth-state 文件
    call_paths = [c.kwargs["prompt_variables"]["AUTH_STATE_FILE"] for c in mock_executor.execute.call_args_list]
    assert len(set(call_paths)) == 3
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd packages/core && python -m pytest tests/test_validate_authentication.py::test_multi_identity_login_loop_writes_manifest -x`
Expected: FAIL（单次登录，无 manifest）

- [ ] **Step 4: 实现 — validate_authentication 改登录循环**

在 `validate_authentication` 内，L130-171（单次登录块）改为：解析出 `dist_config.accounts`（list[Account]，可能空）后，构建身份列表 `[(primary_auth, "primary"), *[(a, a.id) for a in accounts]]`，逐个登录。结构：
```python
    # 解析后（dist_config.authentication 非空才走到这）
    await cleanup_auth_state(workspace_path)
    identities: list[IdentityRecord] = []
    primary_failed = False
    # 身份列表：(authentication, account_id, role, tier)
    id_list = [(dist_config.authentication, "primary", None, None)]
    for acct in (dist_config.accounts or []):
        id_list.append((dist_config.authentication, acct.id, acct.role, acct.tier))
    for idx, (auth, acct_id, role, tier) in enumerate(id_list):
        state_file = auth_state_path(workspace_path, None if acct_id == "primary" else acct_id)
        try:
            metrics = await executor.execute(
                agent_name=AgentName.VALIDATE_AUTH, repo_path=repo_path or "/tmp/shannon-auth-check",
                web_url=web_url, deliverables_path=deliverables_path, config_path=config_path,
                api_key=api_key, prompt_override="validate-authentication",
                prompt_variables={"AUTH_STATE_FILE": str(state_file)},
                structured_output_schema=AUTH_VALIDATION_SCHEMA,
                audit_logger=audit_logger, tool_audit_logger=tool_audit_logger)
            so = metrics.structured_output or {}
            ok = bool(so.get("login_success"))
        except Exception:
            ok = False; so = {}
        rec = IdentityRecord(
            account_id=acct_id, role=role, tier=tier or "low",
            auth_state_file=state_file.name, available=ok,
            failure_detail=None if ok else (so.get("failure_detail") or "login failed"))
        identities.append(rec)
        if not ok and acct_id == "primary":
            primary_failed = True
            break  # attacker 必须，fail-fast
    IdentityManifest(identities=identities).write(workspace_path)
    if primary_failed:
        return AuthValidationResult(success=False, failure_point="username_or_password",
                                    failure_detail="primary attacker login failed")
    return AuthValidationResult(success=True)
```
> 注：`dist_config.accounts` 字段在 Task 1 已加到 `DistributedConfig`。tier/role 对 primary 为 None（manifest 里 primary.tier 用 "low" 占位，或由 build_comparison_matrix 时按 role 推导——primary 的 tier 在 web 层展开时已定，见 Task 10；CLI 直填时 primary 继承 authentication 无 role，按 low 处理）。`config_path` 每身份复用同一份（含占位符的 login_flow）——agent 读 `authentication.credentials` 时需用该身份凭据；当前 validate-authentication prompt 从 config 读单 credentials，**多身份需 prompt 侧按 AUTH_STATE_FILE 区分**——本 Task 先保证循环 + manifest 落盘，凭据注入细化见 Task 7（build_identity_context）。若 Task 4 测试只用 login_success mock 不验凭据内容，循环 + manifest 可先绿。

- [ ] **Step 5: 写失败测试 — victim 失败降级、attacker 失败 fail-fast**

```python
@pytest.mark.asyncio
async def test_victim_failure_degrades_but_scan_continues(tmp_path):
    """victim/baseline 登录失败 → available=False，整体仍 success（不 fail-fast）。"""
    from supernova_core.models.config import Config, Authentication, Credentials, Account
    from supernova_core.services.validate_authentication import load_identity_manifest
    async def fake_execute(**kwargs):
        sf = kwargs["prompt_variables"]["AUTH_STATE_FILE"]
        Path(sf).write_text(json.dumps({"cookies":[{}],"origins":[]}))
        ok = "victim-b" not in sf  # victim 那次失败
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=10, structured_output={"login_success": ok})
    me = MagicMock(); me.execute = AsyncMock(side_effect=fake_execute); mp = MagicMock()
    cfg = Config(authentication=Authentication(login_type="form", login_url="https://x/l",
        credentials=Credentials(username="u")), accounts=[
        Account(id="victim-b", role="user", tier="low", credentials=Credentials(username="v"))])
    with patch("supernova_core.config.parser.parse_config", return_value=cfg), \
         patch("supernova_core.config.parser.distribute_config",
               return_value=MagicMock(authentication=cfg.authentication, accounts=cfg.accounts)):
        r = await validate_authentication(web_url="https://x", config_path="/c.yaml",
            workspace_path=str(tmp_path), prompt_manager=mp, executor=me)
    assert r.success is True  # victim 死不拖垮
    manifest = load_identity_manifest(tmp_path)
    vic = [x for x in manifest.identities if x.account_id == "victim-b"][0]
    assert vic.available is False

@pytest.mark.asyncio
async def test_primary_failure_is_fail_fast(tmp_path):
    """primary(attacker) 登录失败 → success=False，扫描终止信号。"""
    from supernova_core.models.config import Config, Authentication, Credentials, Account
    async def fake_execute(**kwargs):
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=10, structured_output={"login_success": False,
            "failure_point":"username_or_password","failure_detail":"bad"})
    me = MagicMock(); me.execute = AsyncMock(side_effect=fake_execute); mp = MagicMock()
    cfg = Config(authentication=Authentication(login_type="form", login_url="https://x/l",
        credentials=Credentials(username="u")), accounts=[
        Account(id="v", role="user", tier="low", credentials=Credentials(username="v2"))])
    with patch("supernova_core.config.parser.parse_config", return_value=cfg), \
         patch("supernova_core.config.parser.distribute_config",
               return_value=MagicMock(authentication=cfg.authentication, accounts=cfg.accounts)):
        r = await validate_authentication(web_url="https://x", config_path="/c.yaml",
            workspace_path=str(tmp_path), prompt_manager=mp, executor=me)
    assert r.success is False
    assert me.execute.call_count == 1  # primary 一失败就停，不登 victim
```

- [ ] **Step 6: 跑测试确认失败→实现→通过**

Run: `cd packages/core && python -m pytest tests/test_validate_authentication.py -k "multi_identity or victim_failure or primary_failure" -x`
Expected: 实现 Step 4 后 PASS

- [ ] **Step 7: 回归 — 无 accounts 时 byte-identical**

Run: `cd packages/core && python -m pytest tests/test_validate_authentication.py -x`
Expected: PASS（无 accounts 走单身份 primary 循环，行为等同现状；identity-manifest 落盘含单条 primary 记录——这是可接受的轻微行为变化，或加 `if not accounts: skip manifest` 严格兼容，二选一，倾向后者保 byte-identical）

> **决策（Step 7）**：无 accounts 时不落 manifest（`if not dist_config.accounts: return AuthValidationResult(success)` 在原单次路径），保 byte-identical。多 accounts 才走循环 + manifest。Step 4 实现按此分支。

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/supernova_core/services/validate_authentication.py packages/core/tests/test_validate_authentication.py
git commit -m "feat(core): identity-manifest + 多身份 preflight 登录循环(子项目2 T4)"
```

---

### Task 5: get_identity_session_id helper

**Files:**
- Modify: `packages/core/src/supernova_core/services/playwright_config_writer.py`（L31 后加 get_identity_session_id）
- Test: `packages/core/tests/test_playwright_config_writer.py`（若无则新建，flat）

**Interfaces:**
- Produces: `get_identity_session_id(agent_name, account_id) -> str`（如 `"agent-authz-victim-b"`）

- [ ] **Step 1: 写失败测试**

```python
def test_get_identity_session_id_appends_account():
    from supernova_core.services.playwright_config_writer import get_identity_session_id
    assert get_identity_session_id("authz-exploit", "victim-b") == "agent-authz-victim-b"

def test_get_identity_session_id_default_base():
    from supernova_core.services.playwright_config_writer import get_identity_session_id
    assert get_identity_session_id("unknown-agent", "admin-1") == "default-admin-1"

def test_get_session_id_unchanged():
    from supernova_core.services.playwright_config_writer import get_session_id
    assert get_session_id("authz-exploit") == "agent-authz"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/core && python -m pytest tests/test_playwright_config_writer.py -x`
Expected: FAIL（无 get_identity_session_id；若无此测试文件则整体 no tests，先建文件）

- [ ] **Step 3: 实现 — get_identity_session_id**

playwright_config_writer.py L31（`get_session_id` 后）加：
```python
def get_identity_session_id(agent_name: str, account_id: str) -> str:
    """Browser session id for a specific identity slot (multi-identity authz)."""
    base = AGENT_SESSION_MAPPING.get(agent_name, "default")
    return f"{base}-{account_id}"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd packages/core && python -m pytest tests/test_playwright_config_writer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/services/playwright_config_writer.py packages/core/tests/test_playwright_config_writer.py
git commit -m "feat(core): get_identity_session_id helper(子项目2 T5)"
```

---

### Task 6: POTENTIAL verdict 档（6 处同步）

**Files:**
- Modify: `packages/core/src/supernova_core/models/exploit_verdict_schemas.py`（L15-17 ExploitStatus + PotentialVerdict + L54 Union）
- Modify: `packages/core/src/supernova_core/collectors/exploit.py`（L38-41 schema enum + L42-49 description + L133-162 _normalize_verdict）
- Modify: `packages/core/src/supernova_core/renderers/exploit.py`（L112 分支 + helper + section 标签）
- Modify: `prompts/authz-exploit.txt`（L126-132 verdict 说明段加 potential）
- Test: `packages/blackbox/tests/test_exploit_verdict_validator.py`（potential 档校验）
- Test: renderer 测试（找现有 `test_*exploit*render*` 或新建）

**Interfaces:**
- Produces: `ExploitStatus` 加 `"potential"`；`PotentialVerdict(vulnerability_id, status="potential", severity, confidence, downgrade_reason, evidence_of_vulnerability)`

- [ ] **Step 1: 写失败测试 — PotentialVerdict 校验**

test_exploit_verdict_validator.py 追加：
```python
def _potential(vid="AZ-VULN-1"):
    return {"vulnerability_id": vid, "status": "potential", "severity": "medium",
            "confidence": "low", "downgrade_reason": "victim baseline unavailable",
            "evidence_of_vulnerability": "attacker got 200 but no baseline to prove cross-user"}

def test_potential_verdict_accepted():
    res = validate_exploit_verdicts([_potential()], VALID_IDS)
    assert len(res.accepted) == 1
    assert res.accepted[0]["status"] == "potential"

def test_potential_verdict_missing_downgrade_reason_rejected():
    bad = _potential(); del bad["downgrade_reason"]
    res = validate_exploit_verdicts([bad], VALID_IDS)
    assert res.accepted == []
    assert len(res.rejected) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/blackbox && python -m pytest tests/test_exploit_verdict_validator.py::test_potential_verdict_accepted -x`
Expected: FAIL（status 不在 enum）

- [ ] **Step 3: 实现 — exploit_verdict_schemas.py**

L15-17 `ExploitStatus` 加 `"potential"`：
```python
ExploitStatus = Literal[
    "exploited", "blocked_by_security", "out_of_scope_internal", "false_positive", "potential"
]
```
L48（`FalsePositiveVerdict` 后）加：
```python
class PotentialVerdict(_VerdictBase):
    status: Literal["potential"]
    severity: Severity
    confidence: str
    downgrade_reason: str
    evidence_of_vulnerability: str
```
L54-57 `_VerdictUnion` Union 加 `PotentialVerdict`。

- [ ] **Step 4: 实现 — collectors/exploit.py（schema enum + description + normalize）**

L38-41 `_SINGLE_VERDICT_SCHEMA["properties"]["status"]["enum"]` 加 `"potential"`。
L42-49 status description 块加 potential 字段说明（仿其它档）。
L133-162 `_normalize_verdict` 加 potential 分支（归一化 severity/confidence/downgrade_reason/evidence_of_vulnerability）。

- [ ] **Step 5: 实现 — renderers/exploit.py**

L112 后加：`potential = [v for v in validation.accepted if v.status == "potential"]`
加 helper `_potential(items)`（仿 `_blocked`，L75-84）。
`_M`（L17-47）加 `sec_potential` 双语标签（如 `"Potential (No Baseline)"` / `"疑似(无基线佐证)"`）。
L125 后加 potential section 渲染（在 blocked 之后、other 之前）。

- [ ] **Step 6: 实现 — authz-exploit.txt verdict 说明段（L126-132）**

加 `potential` 档说明：何时用（无 baseline 佐证）、字段要求（severity/confidence/downgrade_reason/evidence_of_vulnerability）。

- [ ] **Step 7: 跑测试 + 回归**

Run: `cd packages/blackbox && python -m pytest tests/test_exploit_verdict_validator.py -x`
Expected: PASS
Run: `cd packages/core && python -m pytest tests/ -k "verdict or exploit_render" -x`（找 renderer 测试，若无则手验 render 不崩）
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/supernova_core/models/exploit_verdict_schemas.py packages/core/src/supernova_core/collectors/exploit.py packages/core/src/supernova_core/renderers/exploit.py prompts/authz-exploit.txt packages/blackbox/tests/test_exploit_verdict_validator.py
git commit -m "feat(core): POTENTIAL verdict 档(无baseline降级,6处同步)(子项目2 T6)"
```

---

### Task 7: identity context prompt 渲染 + _identities.txt partial

**Files:**
- Create: `prompts/shared/_identities.txt`
- Modify: `packages/core/src/supernova_core/prompts/manager.py`（加 build_identity_context + _interpolate 注入 `{{IDENTITY_CONTEXT}}`）
- Test: `packages/core/tests/test_prompt_manager.py`（找现有或新建）

**Interfaces:**
- Consumes: `IdentityManifest`、`build_comparison_matrix`、`get_identity_session_id`、engine `auth_load_command`
- Produces: `PromptManager.build_identity_context(manifest, engine) -> str`；`{{IDENTITY_CONTEXT}}` 渲染（多身份→manifest 表 + 比较协议；无 manifest→空串）

- [ ] **Step 1: 写失败测试 — build_identity_context 渲染 manifest + 协议**

```python
def test_build_identity_context_renders_multi_identity(tmp_path):
    from supernova_core.prompts.manager import PromptManager
    from supernova_core.services.validate_authentication import IdentityManifest, IdentityRecord
    from supernova_core.services.engines.agent_browser_engine import AgentBrowserEngine
    pm = PromptManager(str(tmp_path))
    manifest = IdentityManifest(identities=[
        IdentityRecord("primary", "user", "low", "auth-state.json", True),
        IdentityRecord("victim-b", "user", "low", "auth-state-victim-b.json", True),
        IdentityRecord("admin-1", "admin", "high", "auth-state-admin-1.json", True),
    ])
    engine = AgentBrowserEngine()
    ctx = pm.build_identity_context(manifest, engine)
    assert "primary" in ctx and "victim-b" in ctx and "admin-1" in ctx
    assert "state load" in ctx  # auth_load_command 嵌入
    assert "vertical" in ctx and "horizontal" in ctx  # 比较协议矩阵

def test_build_identity_context_empty_when_no_manifest(tmp_path):
    from supernova_core.prompts.manager import PromptManager
    pm = PromptManager(str(tmp_path))
    assert pm.build_identity_context(None, None) == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/core && python -m pytest tests/test_prompt_manager.py -k identity_context -x`
Expected: FAIL（无 build_identity_context）

- [ ] **Step 3: 实现 — _identities.txt partial**

```text
<identity_set>
You have MULTIPLE authenticated sessions available (one per identity).
For each identity, restore its saved session before acting as that identity:

{{IDENTITY_SESSION_ROWS}}

<comparison_protocol>
Pairwise comparison matrix (attacker vs baseline) for authorization testing:
{{IDENTITY_COMPARISON_PAIRS}}

Rules:
- VERTICAL (low attacker × high baseline): high-baseline session establishes the admin-only
  capability; if low-attacker session can reach equivalent capability ⇒ EXPLOITED.
- HORIZONTAL (low ↔ low): victim session reads ITS OWN private resource as baseline;
  if attacker session reads same resource and data matches victim baseline ⇒ EXPLOITED.
- NO BASELINE available for a direction (victim/baseline unavailable) ⇒ that "successful access"
  MUST be reported as status=potential, NEVER exploited.
</comparison_protocol>
</identity_set>
```

- [ ] **Step 4: 实现 — manager.py build_identity_context + _interpolate**

manager.py 加方法（仿 build_login_instructions 范式）：
```python
def build_identity_context(self, manifest, engine) -> str:
    from supernova_core.services.validate_authentication import IdentityManifest
    from supernova_core.utils.authz_identity import build_comparison_matrix
    from supernova_core.services.playwright_config_writer import get_identity_session_id
    from supernova_core.models.config import Account, Credentials
    if manifest is None:
        return ""
    avail = [r for r in manifest.identities if r.available]
    if len(avail) < 2:
        return ""  # 单身份走 _shared-session，不注入 identity context
    # 用 IdentityRecord 构造 Account 喂 build_comparison_matrix
    accts = [Account(id=r.account_id, credentials=Credentials(username=r.account_id),
                     role=r.role, tier=r.tier) for r in avail]
    rows = []
    for r in avail:
        sid = "authz-exploit" if r.account_id == "primary" else get_identity_session_id("authz-exploit", r.account_id)
        load = engine.auth_load_command(sid, r.auth_state_file) if engine else f"state load {r.auth_state_file}"
        rows.append(f"- identity={r.account_id} role={r.role} tier={r.tier} session={sid} | {load}")
    pairs = build_comparison_matrix(accts)
    pair_lines = [f"- {p.kind}: attacker={p.attacker_id} baseline={p.baseline_id}" for p in pairs]
    tpl = (Path(self._prompts_dir) / "shared" / "_identities.txt").read_text(encoding="utf-8")
    tpl = tpl.replace("{{IDENTITY_SESSION_ROWS}}", "\n".join(rows))
    tpl = tpl.replace("{{IDENTITY_COMPARISON_PAIRS}}", "\n".join(pair_lines) or "(none)")
    return tpl
```
`_interpolate`（L147-159 附近）加 `{{IDENTITY_CONTEXT}}` 渲染：从 `variables.get("IDENTITY_CONTEXT")` 取（由 executor 在 prompt_variables 注入，见 Task 8）。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd packages/core && python -m pytest tests/test_prompt_manager.py -k identity_context -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add prompts/shared/_identities.txt packages/core/src/supernova_core/prompts/manager.py packages/core/tests/test_prompt_manager.py
git commit -m "feat(core): identity context prompt 渲染 + _identities.txt partial(子项目2 T7)"
```

---

### Task 8: authz-exploit executor 读 manifest + 注入 identity context

**Files:**
- Modify: `packages/blackbox/src/supernova_blackbox/agents/exploit_executor.py`（L47-66 间读 identity-manifest + 注入 IDENTITY_CONTEXT；仅 authz-exploit）
- Modify: `prompts/authz-exploit.txt`（L91 @include 条件化：多身份用 _identities 替代 _shared-session；L166-180 Task Agent 模板改读 manifest）
- Test: `packages/blackbox/tests/test_executors.py`

**Interfaces:**
- Consumes: `load_identity_manifest(workspace_path)`、`PromptManager.build_identity_context`
- Produces: authz-exploit 的 `prompt_variables["IDENTITY_CONTEXT"]`（多身份非空，单身份空）

- [ ] **Step 1: 写失败测试 — authz-exploit 多身份注入 IDENTITY_CONTEXT**

test_executors.py 追加（mock_repo fixture 同现有）：
```python
@pytest.mark.asyncio
async def test_authz_exploit_injects_identity_context_when_manifest_present(mock_repo):
    """identity-manifest.json 存在且 ≥2 可用身份 → authz-exploit 注入 IDENTITY_CONTEXT。"""
    repo, deliverables = mock_repo
    # 写 manifest 到 workspace_path（= deliverables.parent）
    import json
    (deliverables.parent / "identity-manifest.json").write_text(json.dumps({"identities": [
        {"account_id":"primary","role":"user","tier":"low","auth_state_file":"auth-state.json","available":True},
        {"account_id":"victim-b","role":"user","tier":"low","auth_state_file":"auth-state-victim-b.json","available":True},
        {"account_id":"admin-1","role":"admin","tier":"high","auth_state_file":"auth-state-admin-1.json","available":True},
    ]}))
    mock_executor = AsyncMock(); mock_executor.execute.return_value = AgentMetrics(duration_ms=10)
    ex = ExploitExecutor(mock_executor)
    await ex.execute(agent_name=AgentName.AUTHZ_EXPLOIT, vuln_type="authz",
        workspace_path=deliverables.parent, deliverables_path=deliverables, web_url="https://x")
    pv = mock_executor.execute.call_args.kwargs.get("prompt_variables", {})
    assert "IDENTITY_CONTEXT" in pv and "victim-b" in pv["IDENTITY_CONTEXT"]

@pytest.mark.asyncio
async def test_authz_exploit_no_identity_context_when_single_identity(mock_repo):
    repo, deliverables = mock_repo
    # 无 manifest 文件
    mock_executor = AsyncMock(); mock_executor.execute.return_value = AgentMetrics(duration_ms=10)
    ex = ExploitExecutor(mock_executor)
    await ex.execute(agent_name=AgentName.AUTHZ_EXPLOIT, vuln_type="authz",
        workspace_path=deliverables.parent, deliverables_path=deliverables, web_url="https://x")
    pv = mock_executor.execute.call_args.kwargs.get("prompt_variables", {})
    assert pv.get("IDENTITY_CONTEXT", "") == ""  # 单身份不注入
    # AUTH_STATE_FILE 仍不在此显式注入（守卫不变）
    assert "AUTH_STATE_FILE" not in pv
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/blackbox && python -m pytest tests/test_executors.py::test_authz_exploit_injects_identity_context_when_manifest_present -x`
Expected: FAIL（无 IDENTITY_CONTEXT 注入）

- [ ] **Step 3: 实现 — exploit_executor.py 读 manifest + 注入**

L47-66（endpoint_verify 读块后、browser_session_id 前）加（仅 authz-exploit）：
```python
    identity_context = ""
    if agent_name == AgentName.AUTHZ_EXPLOIT:
        from supernova_core.services.validate_authentication import load_identity_manifest
        manifest = load_identity_manifest(workspace_path)
        if manifest is not None:
            # 复用 executor 的 prompt_manager（经 base executor 暴露）或现造
            from supernova_core.prompts.manager import PromptManager
            from supernova_core.services.engines.agent_browser_engine import AgentBrowserEngine
            pm = PromptManager(str(workspace_path))  # prompts_dir 解析口径对齐 run_blackbox_auth_validation
            identity_context = pm.build_identity_context(manifest, AgentBrowserEngine())
    prompt_variables["IDENTITY_CONTEXT"] = identity_context
```
> 注：PromptManager 的 prompts_dir 解析口径须与 `run_blackbox_auth_validation`（activities.py:180-182）一致——plan 实现时照搬那个构造（容器内 prompts 路径）。若 executor 已持有 prompt_manager 引用，优先复用。

- [ ] **Step 4: 实现 — authz-exploit.txt prompt 改造**

L91 `@include(shared/_shared-session.txt)` 改为条件化说明（prompt 是静态文本，无法运行时分支；改法：在 `_shared-session.txt` 渲染后，由 `{{IDENTITY_CONTEXT}}` 在其后注入——多身份时 IDENTITY_CONTEXT 含 `<identity_set>` 块覆盖单 session 语义，并加一句"if IDENTITY_CONTEXT below is non-empty, use those sessions instead"）。在 L91 后加：
```text
{{IDENTITY_CONTEXT}}
```
（_interpolate 会把空串替换为无，多身份替换为完整 identity_set 块。）

L166-180 Task Agent "Identity set" 模板：把 "Identity set: [list of user IDs/tokens/roles to iterate]" 改为 "If {{IDENTITY_CONTEXT}} provided identities, use those exact sessions; otherwise iterate as before."

- [ ] **Step 5: 跑测试 + 守卫回归**

Run: `cd packages/blackbox && python -m pytest tests/test_executors.py -x`
Expected: PASS（含 `test_exploit_executor_no_longer_injects_auth_state_file` 守卫）

- [ ] **Step 6: Commit**

```bash
git add packages/blackbox/src/supernova_blackbox/agents/exploit_executor.py prompts/authz-exploit.txt packages/blackbox/tests/test_executors.py
git commit -m "feat(blackbox): authz-exploit 读 identity-manifest + 注入多 session 协议(子项目2 T8)"
```

---

### Task 9: BlackboxScanWorkflow preflight 登录循环编排 + cleanup

**Files:**
- Modify: `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py`（L152-164 auth-validation 块：单次 → 多身份循环经 config_path；L517 cleanup 已 glob 化由 Task 3 覆盖）
- Test: `packages/blackbox/tests/test_workflows.py`（状态机/AST 守卫）；`test_auth_validation_workflow.py`（e2e 范式）

**Interfaces:**
- Consumes: `run_blackbox_auth_validation` activity（内部调 Task 4 的 validate_authentication 登录循环）
- Produces: preflight 经 config_path 读 accounts → 循环登录 → manifest 落 workspace_path → exploit 阶段消费

> **关键**：`run_blackbox_auth_validation` activity（activities.py:170-232）调 `validate_authentication`，Task 4 已让它读 `dist_config.accounts` 走循环。workflow 侧 L152-164 **不需改结构**（仍单次调 `run_blackbox_auth_validation`），多身份循环在 activity 内部。本 Task 主要是回归验证 + 确认 manifest 落 workspace_path 被 Task 8 的 exploit_executor 读到（workspace_path 一致性）。

- [ ] **Step 1: 写测试 — workflow preflight 后 manifest 可被 exploit 消费（e2e 范式）**

参照 `test_auth_validation_workflow.py` 的 WorkflowEnvironment 范式，写一个最小 e2e：mock `run_blackbox_auth_validation` 写 manifest 到 workspace_path，mock `run_exploit_agent` 读 manifest，断言 exploit 拿到 manifest。若 e2e 过重，退化为：断言 `run_blackbox_auth_validation` 与 `run_exploit_agent` 用同一 `workspace_path`（activities 入参一致性，AST 或调用点检查）。

- [ ] **Step 2: 实现/确认**

确认 `act_input.workspace_path`（L152-164 + exploitation L305-352 一致）——manifest 落 `workspace_path`，exploit_executor 读 `workspace_path`，一致则无需改 workflow。若不一致（如 exploit 用 deliverables.parent），加透传。

> 风险点：`executor.py:91` AUTH_STATE_FILE 用 `auth_state_path(deliverables.parent)`，而 manifest 落 `workspace_path`。须确认 `workspace_path == deliverables.parent`（blackbox 语义），否则 manifest/state 路径分叉。本 Task 核心就是核对并统一这俩路径。

- [ ] **Step 3: 跑回归**

Run: `cd packages/blackbox && python -m pytest tests/test_workflows.py tests/test_auth_validation_workflow.py -x`
Expected: PASS

- [ ] **Step 4: Commit（若有改动）**

```bash
git add packages/blackbox/src/supernova_blackbox/pipeline/workflows.py packages/blackbox/tests/
git commit -m "feat(blackbox): preflight 多身份 manifest 与 exploit workspace_path 统一(子项目2 T9)"
```

---

### Task 10: web scan_manager 多身份展开 + ScanRequest 放宽

**Files:**
- Modify: `packages/web/src/supernova_web/models.py`（L62-74 `_auth_profile_xor_inline`：profile_id 无 cred_id = 多身份模式，合法）
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`（L338-354 `_resolve_blackbox_inputs`：profile_id 无 cred_id → 展开所有 credentials + tier 推导 → accounts 写盘）
- Test: `packages/web/tests/test_scan_request_auth_profile.py`（validator 放宽）
- Test: `packages/web/tests/test_scan_manager_profile_expansion.py`（多身份展开）

**Interfaces:**
- Consumes: `AuthProfileStore.get(ws, profile_id)`、`derive_privilege_tier`、`credential_to_authentication`
- Produces: ScanRequest 多身份模式（仅 profile_id）；scan-config.yaml 含 `authentication` + `accounts[]`

- [ ] **Step 1: 写失败测试 — validator 放宽（profile_id 无 cred_id 合法）**

test_scan_request_auth_profile.py 追加：
```python
def test_auth_profile_id_without_credential_id_is_valid_multi_identity():
    """只给 profile_id（无 cred_id）= 多身份模式，应合法。"""
    req = ScanRequest(type="blackbox", reuse_whitebox_scan_id="wb-1", auth_profile_id="prof_1")
    assert req.auth_profile_id == "prof_1"
    assert req.auth_credential_id is None
```
（原 `_auth_profile_xor_inline` L70-71 在 profile_id 无 cred_id 时 raise，此测试会 FAIL。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/web && python -m pytest tests/test_scan_request_auth_profile.py::test_auth_profile_id_without_credential_id_is_valid_multi_identity -x`
Expected: FAIL（ValidationError: 必须同时指定 auth_credential_id）

- [ ] **Step 3: 实现 — models.py validator 放宽**

`_auth_profile_xor_inline`（L62-74）改：去掉"profile_id 必须配 cred_id"硬约束，改为：profile_id 与 inline authentication 二选一；cred_id 可选（有=单角色，无=多身份）。保留"不可同时给 profile_id 和 authentication"。

- [ ] **Step 4: 写失败测试 — 多身份展开写 accounts**

test_scan_manager_profile_expansion.py 追加：
```python
@pytest.mark.asyncio
async def test_resolve_blackbox_expands_all_credentials_multi_identity(tmp_path):
    from supernova_web.components.auth_profile_store import (
        AuthProfileStore, AuthProfile, AuthProfileCredential)
    from supernova_web.components.credential_vault import CredentialVault
    store = AuthProfileStore(tmp_path, CredentialVault(tmp_path / ".mk"))
    store.write("ws1", [AuthProfile(
        id="prof_1", name="NG", login_url="http://t/", login_type="form",
        login_flow=["成功标志:/dashboard"],
        credentials=[
            AuthProfileCredential(id="cred_admin", role="admin", username="admin", password="pw"),
            AuthProfileCredential(id="cred_u1", role="user", username="u1", password="pw"),
            AuthProfileCredential(id="cred_u2", role="user", username="u2", password="pw"),
        ])])
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock(), auth_profile_store=store)
    req = ScanRequest(type="blackbox", reuse_whitebox_scan_id="wb-1", auth_profile_id="prof_1")  # 无 cred_id
    wb_dir = tmp_path / "ws1" / "scans" / "wb-1"; wb_dir.mkdir(parents=True)
    mgr._store.get_scan_dir = MagicMock(return_value=wb_dir)
    scan_dir = tmp_path / "ws1" / "scans" / "bb-1"; scan_dir.mkdir(parents=True)
    config_path, _ = await mgr._resolve_blackbox_inputs(req, "ws1", scan_dir, None)
    body = (scan_dir / "scan-config.yaml").read_text("utf-8")
    import yaml; cfg = yaml.safe_load(body)
    assert "accounts" in cfg and len(cfg["accounts"]) == 2  # 其余 2 个（primary 进 authentication）
    tiers = {a["id"]: a["tier"] for a in cfg["accounts"]}
    assert tiers["cred_admin"] == "high"
    assert tiers["cred_u1"] == "low" or tiers.get("cred_u2") == "low"  # primary 选首个 low
```

- [ ] **Step 5: 跑测试确认失败**

Run: `cd packages/web && python -m pytest tests/test_scan_manager_profile_expansion.py::test_resolve_blackbox_expands_all_credentials_multi_identity -x`
Expected: FAIL（无 cred_id 走旧单角色分支报错/无 accounts）

- [ ] **Step 6: 实现 — scan_manager._resolve_blackbox_inputs 多身份分支**

L338-354 改：当 `req.auth_profile_id` 且 `req.auth_credential_id is None`（多身份模式）：
```python
    profile = self._auth_profile_store.get(ws, req.auth_profile_id)
    if profile is None:
        raise ValueError("认证档案不存在")
    if req.auth_credential_id:   # 单角色（现状）
        cred = next((c for c in profile.credentials if c.id == req.auth_credential_id), None)
        if cred is None: raise ValueError("角色凭据不存在")
        auth = credential_to_authentication(profile, cred)
        payload = {"authentication": auth.model_dump(exclude_none=True, mode="json")}
    else:                         # 多身份（新）
        from supernova_core.utils.authz_identity import derive_privilege_tier
        from supernova_core.models.config import Credentials
        high_names = ["admin"]  # 或读 env/config
        creds = profile.credentials
        # primary = 首个 low（无 low 则首个）
        def tier_of(c): return derive_privilege_tier(c.role, high_names)
        lows = [c for c in creds if tier_of(c) == "low"]
        primary = (lows[0] if lows else creds[0])
        primary_auth = credential_to_authentication(profile, primary)
        accounts = []
        for c in creds:
            if c.id == primary.id: continue
            accounts.append({"id": c.id, "role": c.role, "tier": tier_of(c),
                             "credentials": {"username": c.username, "password": c.password}})
        payload = {"authentication": primary_auth.model_dump(exclude_none=True, mode="json"),
                   "accounts": accounts}
```
（Fernet 解密：`store.get` 已解密，c.password 是明文——对齐单角色分支。）

- [ ] **Step 7: 跑测试 + 回归**

Run: `cd packages/web && python -m pytest tests/test_scan_manager_profile_expansion.py tests/test_scan_request_auth_profile.py -x`
Expected: PASS（含现有单角色测试回归）

- [ ] **Step 8: Commit**

```bash
git add packages/web/src/supernova_web/models.py packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_scan_request_auth_profile.py packages/web/tests/test_scan_manager_profile_expansion.py
git commit -m "feat(web): scan_manager 多身份展开(选档案即跑) + ScanRequest 放宽(子项目2 T10)"
```

---

### Task 11: 端到端 + GLM N session 探针

**Files:**
- Probe: `scripts/validate_authz_multi_identity_probe.py`（新，仿 `validate_*_task_probe.py`）
- Test: `packages/blackbox/tests/test_multi_identity_e2e.py`（新，若可行用 WorkflowEnvironment；否则降级为集成断言）

**Interfaces:**
- Consumes: T1-T10 全部产物

- [ ] **Step 1: 写 e2e 测试 — admin+2user 产比较协议 verdict**

用 WorkflowEnvironment 或直接调 ExploitExecutor（mock executor 返回 structured_output 含 manifest 场景的 verdict），断言：多身份 manifest 存在时 authz-exploit 的 prompt 含 IDENTITY_CONTEXT + 比较协议；单身份时不含。verdict 层断言：有 baseline 的可达 EXPLOITED、无 baseline 的 POTENTIAL（mock agent 行为）。

- [ ] **Step 2: GLM N session 能力探针**

仿 `scripts/validate_openai_task_probe.py` 写探针：真实起 agent-browser 多 session（admin/user1/user2 各 load 不同 auth-state），给 authz-exploit prompt + manifest，验证 GLM 能否正确多 turn 切 session 做 baseline↔attacker 对比、产出结构化 verdict。**这是 R1 风险的实测**——若 GLM 不能正确切 session，回退评估（可能需简化为 Task 6 方案 B 确定性重放，或限制 N=2）。

- [ ] **Step 3: rebuild 镜像 + 冒烟**

```bash
# 改了 web+worker src，须 rebuild
# 按 memory [[web-llm-track-switch-not-wired]] / [[llm-tool-input-schema-must-be-flat-object]] 同类约束
docker compose build supernova-worker
# NodeGoat 冒烟：配 admin+2user 档案，选档案即跑，验 authz 出 EXPLOITED/POTENTIAL
```

- [ ] **Step 4: Commit**

```bash
git add scripts/validate_authz_multi_identity_probe.py packages/blackbox/tests/test_multi_identity_e2e.py
git commit -m "test(blackbox): 多身份越权 e2e + GLM N session 探针(子项目2 T11)"
```

---

## Self-Review 笔记（plan 作者）

**Spec 覆盖**：§1 数据模型→T1；§2 tier 推导→T2；§3 web 衔接→T10；§4 登录循环/manifest→T3/T4；§5 session 拓扑→T5/T8；§6 比较协议/降级→T6/T7/T8；§7 兼容/失败→T4 Step5-7；Files Changed 全覆盖；不变量 6 条均落地。

**风险留白**：T4 primary 凭据注入（validate-authentication prompt 读单 credentials）需 T7 build_identity_context 配合——T4 先绿循环+manifest（mock login_success），凭据注入在 T7/T8 收口。T9 workspace_path vs deliverables.parent 路径一致性须实测核对。T11 GLM N session 是最大不确定性。

**手动核对项**（plan 执行者注意）：PromptManager prompts_dir 在 exploit_executor 容器内解析口径（T8 Step3）；`dist_config.accounts` 在 blackbox `run_blackbox_auth_validation` activity 内确实透传到 validate_authentication（T4 依赖 T1 的 distribute_config 透传 + activity 传参）；high_priv_names 配置化（T10 硬编码 ["admin"]，env 化留后续）。
