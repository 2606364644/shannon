# 多身份登录：按账号替换凭据 + 独立会话 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让黑盒多身份 preflight 登录按每个账号各自的凭据登录、并在独立浏览器会话里登录，使产出的 N 个 `auth-state-{id}.json` 是 N 个真正不同身份的会话（而非 N 份 primary 副本）。

**Architecture:** Approach A——改动只在 `validate_authentication.py` 的 Branch B（多身份循环）。每个身份 = 一份替换过凭据的 per-identity config（提升该 account 凭据到 top-level `authentication`）+ 一个独立 `browser_session_id`（`validate-auth-{id}`）+ 各自的 auth-state 文件。prompt builder / prompt 模板 / executor 签名 / 下游 exploit 全不动；Branch A（单身份、绝大多数扫描）byte-identical。

**Tech Stack:** Python 3 (pydantic v2 `model_copy`/`model_dump`)、PyYAML、pytest + pytest-asyncio、unittest.mock。

## Global Constraints（每个 task 隐含遵守）

- **只改一个源文件 + 其测试**：`packages/core/src/supernova_core/services/validate_authentication.py` 与 `packages/core/tests/test_validate_authentication.py`。不改 prompt / executor / 下游 exploit / 上游 scan_manager。
- **Branch A（无 accounts 的单次登录路径）byte-identical**：新增的 `browser_session_id` 参数默认 `None`，为 `None` 时不写入 `prompt_variables` → 回落 `agent1`。Branch A 调用处不改。
- **TDD**：每个实现前先写失败测试、跑红、再实现、跑绿、提交。
- **凭据卫生边界不变**：per-identity config 凭据来自 `scan-config.yaml` 同源同密级、同在 scan_dir、用后即删——不进加密 vault（CLAUDE.md §1）。
- 测试只跑改动相关文件：`pytest packages/core/tests/test_validate_authentication.py -v`（勿广跑全套，CLAUDE.md §3）。

## File Structure

| 文件 | 职责 | 本次改动 |
|---|---|---|
| `packages/core/src/supernova_core/services/validate_authentication.py` | 黑盒登录校验 + 多身份 preflight 循环 | 加 `import yaml`、TYPE_CHECKING 加 `Account, Config`、新增 helper `_write_per_identity_config`、`_build_validate_auth_executor_kwargs` 加 `browser_session_id` 参数、重写 Branch B 循环、finally 清理临时 config |
| `packages/core/tests/test_validate_authentication.py` | 单测 | 迁移 3 个现有多身份测试的 config_path、新增 helper 单测 + 凭据/会话/清理/凭据依赖回归测 |

**说明：** 设计 spec（`docs/superpowers/specs/2026-08-07-...`）中提及的内部 `_IdentityJob` dataclass 在本 plan 里精简为「内联有序 spec 元组循环」——数据契约（per-identity config + `browser_session_id` + 各自 auth-state）不变，仅内部脚手架更简。这是 plan 对设计实现细节的正常细化。

**类型锚点（来自 `packages/core/src/supernova_core/models/config.py`，供所有 task 复用）：**
```python
class Credentials(BaseModel):
    username: str
    password: str | None = None
    totp_secret: str | None = None
    email_login: EmailLogin | None = None
class Authentication(BaseModel):
    login_type: Literal["form","sso","api","basic"]; login_url: str
    credentials: Credentials; login_flow: list[str] | None = None
class Account(BaseModel):
    id: str; credentials: Credentials; role: str | None = None; tier: Literal["high","low"]
class Config(BaseModel):
    authentication: Authentication | None = None; accounts: list[Account] = []; ...
```

---

## Task 1: `_write_per_identity_config` helper（单测驱动）

**Files:**
- Modify: `packages/core/src/supernova_core/services/validate_authentication.py`（顶部 import + TYPE_CHECKING + 新增函数，置于 `_build_validate_auth_executor_kwargs` 之后、`validate_authentication` 之前）
- Test: `packages/core/tests/test_validate_authentication.py`

**Interfaces:**
- Produces: `_write_per_identity_config(scan_dir: Path, config: Config, account: Account) -> Path`。读 `config`（parse_config 产物）深拷贝 → 把 `account.credentials` 提到 `per.authentication.credentials`、`per.accounts=[]` → 写 `<scan_dir>/scan-config-{account.id}.yaml` → 返回该路径。

- [ ] **Step 1: 写失败测试（helper 单测）**

追加到 `packages/core/tests/test_validate_authentication.py` 末尾：

```python
@pytest.mark.asyncio
async def test_write_per_identity_config_swaps_credentials(tmp_path):
    """_write_per_identity_config 把 account 凭据提到 top-level authentication、清空 accounts。"""
    from supernova_core.models.config import Config, Authentication, Credentials, Account
    from supernova_core.services.validate_authentication import _write_per_identity_config
    import yaml as _yaml

    cfg = Config(
        authentication=Authentication(login_type="form", login_url="https://x/l",
            credentials=Credentials(username="userA", password="primary-pw"),
            login_flow=["enter $username", "enter $password"]),
        accounts=[Account(id="victim-b", role="user", tier="low",
            credentials=Credentials(username="userB", password="vic-pw"))])

    acct = cfg.accounts[0]
    path = _write_per_identity_config(tmp_path, cfg, acct)

    assert path.name == "scan-config-victim-b.yaml"
    data = _yaml.safe_load(path.read_text())
    # account 凭据被提到 top-level authentication
    assert data["authentication"]["credentials"]["username"] == "userB"
    assert data["authentication"]["credentials"]["password"] == "vic-pw"
    # login_url / login_flow 保留（来自 primary 的 authentication）
    assert data["authentication"]["login_url"] == "https://x/l"
    assert data["authentication"]["login_flow"] == ["enter $username", "enter $password"]
    # accounts 清空（单身份语义）
    assert data.get("accounts") == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/core/tests/test_validate_authentication.py::test_write_per_identity_config_swaps_credentials -v`
Expected: FAIL — `ImportError: cannot import name '_write_per_identity_config'`

- [ ] **Step 3: 实现 helper + import**

在 `validate_authentication.py` 顶部，`import json` 之后加：
```python
import yaml
```
在 `TYPE_CHECKING` 块末尾加：
```python
    from supernova_core.models.config import Account, Config
```
在 `_build_validate_auth_executor_kwargs` 函数之后、`async def validate_authentication` 之前插入：
```python
def _write_per_identity_config(scan_dir: Path, config: Config, account: Account) -> Path:
    """写一份单身份登录 config：把 account.credentials 提到 top-level authentication、
    accounts 清空。execute() 重新 parse 此文件后，_build_auth_context / build_login_instructions
    读到的是该身份的凭据（login_flow 的 $username/$password/$totp 占位符也按此身份替换）。
    """
    per = config.model_copy(deep=True)
    per.authentication = per.authentication.model_copy(deep=True)
    per.authentication.credentials = account.credentials
    per.accounts = []
    path = scan_dir / f"scan-config-{account.id}.yaml"
    path.write_text(yaml.safe_dump(per.model_dump(mode="json", exclude_none=True)))
    return path
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest packages/core/tests/test_validate_authentication.py::test_write_per_identity_config_swaps_credentials -v`
Expected: PASS

- [ ] **Step 5: 提交**
```bash
git add packages/core/src/supernova_core/services/validate_authentication.py packages/core/tests/test_validate_authentication.py
git commit -m "feat(core): add _write_per_identity_config helper for per-account login"
```

---

## Task 2: `_build_validate_auth_executor_kwargs` 加 `browser_session_id`（单测驱动）

**Files:**
- Modify: `packages/core/src/supernova_core/services/validate_authentication.py`（`_build_validate_auth_executor_kwargs` 约 `:128-156`）
- Test: `packages/core/tests/test_validate_authentication.py`

**Interfaces:**
- Produces: `_build_validate_auth_executor_kwargs(..., browser_session_id: str | None = None)`。`browser_session_id` 非 None 时写入 `prompt_variables["browser_session_id"]`；为 None 时 `prompt_variables` 不含该键（**Branch A byte-identical 不变量**）。

- [ ] **Step 1: 写失败测试**

追加到测试文件末尾：
```python
@pytest.mark.asyncio
async def test_build_validate_auth_kwargs_browser_session_optional():
    """browser_session_id 非 None 时进 prompt_variables；None 时该键不存在（Branch A 不变量）。"""
    from supernova_core.services.validate_authentication import _build_validate_auth_executor_kwargs

    with_id = _build_validate_auth_executor_kwargs(
        web_url="https://x", config_path="/c.yaml", deliverables_path=None, api_key=None,
        repo_path="r", state_file=Path("/s.json"), audit_logger=None, tool_audit_logger=None,
        browser_session_id="validate-auth-victim-b")
    assert with_id["prompt_variables"]["browser_session_id"] == "validate-auth-victim-b"
    assert with_id["prompt_variables"]["AUTH_STATE_FILE"] == "/s.json"

    without_id = _build_validate_auth_executor_kwargs(
        web_url="https://x", config_path="/c.yaml", deliverables_path=None, api_key=None,
        repo_path="r", state_file=Path("/s.json"), audit_logger=None, tool_audit_logger=None)
    assert "browser_session_id" not in without_id["prompt_variables"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/core/tests/test_validate_authentication.py::test_build_validate_auth_kwargs_browser_session_optional -v`
Expected: FAIL — `TypeError: ... got an unexpected keyword argument 'browser_session_id'`

- [ ] **Step 3: 实现**

把 `_build_validate_auth_executor_kwargs` 的签名末尾加参数、返回值改为条件构造 `prompt_variables`：
```python
def _build_validate_auth_executor_kwargs(
    *,
    web_url: str,
    config_path: str | None,
    deliverables_path: str | None,
    api_key: str | None,
    repo_path: str,
    state_file: Path,
    audit_logger: "ActivityLogger | None",
    tool_audit_logger: "ToolAuditLogger | None",
    browser_session_id: str | None = None,
) -> dict:
    """Build the kwargs dict for one validate-authentication executor.execute call.

    Shared between the byte-identical single-identity path (no accounts) and the
    multi-identity loop so the per-call contract stays identical. browser_session_id
    is None on the single-identity path (byte-identical, falls back to agent1) and
    a per-identity id on the multi-identity path.
    """
    prompt_variables: dict[str, str] = {"AUTH_STATE_FILE": str(state_file)}
    if browser_session_id:
        prompt_variables["browser_session_id"] = browser_session_id
    return dict(
        agent_name=AgentName.VALIDATE_AUTH,
        repo_path=repo_path or "/tmp/shannon-auth-check",
        web_url=web_url,
        deliverables_path=deliverables_path,
        config_path=config_path,
        api_key=api_key,
        prompt_override="validate-authentication",
        prompt_variables=prompt_variables,
        structured_output_schema=AUTH_VALIDATION_SCHEMA,
        audit_logger=audit_logger,
        tool_audit_logger=tool_audit_logger,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest packages/core/tests/test_validate_authentication.py::test_build_validate_auth_kwargs_browser_session_optional -v`
Expected: PASS

- [ ] **Step 5: 提交**
```bash
git add packages/core/src/supernova_core/services/validate_authentication.py packages/core/tests/test_validate_authentication.py
git commit -m "feat(core): thread browser_session_id into validate-auth kwargs (optional)"
```

---

## Task 3: 迁移现有多身份测试到 tmp_path config_path（安全前置，行为不变）

**背景：** 现有 3 个多身份测试用 `config_path="/c.yaml"`。Task 4 的 Branch B 重写会把 per-identity config 写到 `Path(config_path).parent`——`"/c.yaml"` 的 parent 是 `"/"`，非 root 会 PermissionError、root 会向根目录泄漏垃圾文件。先把它们迁到 `tmp_path`，迁移本身不改任何生产行为（当前代码不读 parent）。

**Files:**
- Modify: `packages/core/tests/test_validate_authentication.py`（3 个测试：`test_multi_identity_login_loop_writes_manifest`、`test_victim_failure_degrades_but_scan_continues`、`test_primary_failure_is_fail_fast`，约 `:610`、`:648`、`:674`）

**Interfaces:** 无（仅测试 fixtures）。

- [ ] **Step 1: 改 3 处 config_path**

在 3 个测试里，把调用 `validate_authentication(..., config_path="/c.yaml", ...)` 的 `config_path` 改为 `config_path=str(tmp_path / "scan-config.yaml")`。

例（`test_multi_identity_login_loop_writes_manifest`）：
```python
        result = await validate_authentication(
            web_url="https://x", config_path=str(tmp_path / "scan-config.yaml"),
            workspace_path=str(tmp_path),
            prompt_manager=mock_pm, executor=mock_executor)
```
另外两个测试同理替换 `config_path="/c.yaml"` → `config_path=str(tmp_path / "scan-config.yaml")`。

- [ ] **Step 2: 跑这 3 个测试确认仍绿（迁移不应改变行为）**

Run: `pytest packages/core/tests/test_validate_authentication.py::test_multi_identity_login_loop_writes_manifest packages/core/tests/test_validate_authentication.py::test_victim_failure_degrades_but_scan_continues packages/core/tests/test_validate_authentication.py::test_primary_failure_is_fail_fast -v`
Expected: PASS（3 个全绿——当前生产行为未变）

- [ ] **Step 3: 提交**
```bash
git add packages/core/tests/test_validate_authentication.py
git commit -m "test(core): move multi-identity auth tests to tmp_path config_path"
```

---

## Task 4: Branch B 重写——按账号换凭据 + 独立会话（核心修复，TDD）

**Files:**
- Modify: `packages/core/src/supernova_core/services/validate_authentication.py`（Branch B，约 `:240-293`，从 `# ── Branch B:` 注释到函数末尾 `return AuthValidationResult(success=True)`）
- Test: `packages/core/tests/test_validate_authentication.py`

**Interfaces:**
- Consumes: Task 1 的 `_write_per_identity_config`、Task 2 的 `browser_session_id` kwarg。
- Produces: 不变的外部契约——`IdentityManifest(identities=[...]).write(workspace_path)`、primary fail-fast、非 primary 降级；新增保证：每次 execute 收到该身份的 config_path（凭据已替换）+ `browser_session_id="validate-auth-{id}"`。

- [ ] **Step 1: 写失败回归测 1——每身份用各自凭据（核心 bug 捕手）**

追加到测试文件末尾。注意：需把 base config 真正写到 `tmp_path/scan-config.yaml`，使 fake_execute 能读出 primary 的 config_path 凭据：
```python
@pytest.mark.asyncio
async def test_multi_identity_uses_per_account_credentials(tmp_path):
    """每个身份的 execute 调用拿到的是【该身份自己】的凭据，而非全 primary。
    当前 buggy 代码下 FAIL（所有调用都拿 primary 的 config_path）→ 修复后 PASS。"""
    from supernova_core.models.config import Config, Authentication, Credentials, Account
    import yaml as _yaml

    cfg = Config(
        authentication=Authentication(login_type="form", login_url="https://x/l",
            credentials=Credentials(username="userA", password="pa")),
        accounts=[
            Account(id="victim-b", role="user", tier="low", credentials=Credentials(username="userB", password="pb")),
            Account(id="admin-1", role="admin", tier="high", credentials=Credentials(username="admin", password="pc")),
        ])
    # 写 base config，使 primary 的 config_path 可被 fake_execute 读出
    base = tmp_path / "scan-config.yaml"
    base.write_text(_yaml.safe_dump(cfg.model_dump(mode="json", exclude_none=True)))

    seen: dict[str, str] = {}
    async def fake_execute(**kwargs):
        cp = kwargs["config_path"]
        sf = kwargs["prompt_variables"]["AUTH_STATE_FILE"]
        ident = "primary" if sf.endswith("auth-state.json") else Path(sf).name.replace("auth-state-", "").replace(".json", "")
        data = _yaml.safe_load(Path(cp).read_text())
        seen[ident] = data["authentication"]["credentials"]["username"]
        Path(sf).write_text(json.dumps({"cookies": [{"name": "s"}], "origins": []}))
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=100, structured_output={"login_success": True})

    me = MagicMock(); me.execute = AsyncMock(side_effect=fake_execute); mp = MagicMock()
    with patch("supernova_core.config.parser.parse_config", return_value=cfg), \
         patch("supernova_core.config.parser.distribute_config",
               return_value=MagicMock(authentication=cfg.authentication, accounts=cfg.accounts)):
        await validate_authentication(web_url="https://x", config_path=str(base),
            workspace_path=str(tmp_path), prompt_manager=mp, executor=me)

    assert seen == {"primary": "userA", "victim-b": "userB", "admin-1": "admin"}, seen
```

- [ ] **Step 2: 写失败回归测 2——每身份独立 browser session**

追加到测试文件末尾：
```python
@pytest.mark.asyncio
async def test_multi_identity_uses_distinct_browser_sessions(tmp_path):
    """每个身份的 prompt_variables['browser_session_id'] 互异且==validate-auth-{id}。
    当前代码不设该键 → FAIL。"""
    from supernova_core.models.config import Config, Authentication, Credentials, Account

    cfg = Config(
        authentication=Authentication(login_type="form", login_url="https://x/l",
            credentials=Credentials(username="userA", password="pa")),
        accounts=[Account(id="victim-b", role="user", tier="low", credentials=Credentials(username="userB"))])

    sessions: list[str] = []
    async def fake_execute(**kwargs):
        sessions.append(kwargs["prompt_variables"].get("browser_session_id"))
        sf = kwargs["prompt_variables"]["AUTH_STATE_FILE"]
        Path(sf).write_text(json.dumps({"cookies": [{"name": "s"}], "origins": []}))
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=100, structured_output={"login_success": True})

    me = MagicMock(); me.execute = AsyncMock(side_effect=fake_execute); mp = MagicMock()
    with patch("supernova_core.config.parser.parse_config", return_value=cfg), \
         patch("supernova_core.config.parser.distribute_config",
               return_value=MagicMock(authentication=cfg.authentication, accounts=cfg.accounts)):
        await validate_authentication(web_url="https://x", config_path=str(tmp_path / "scan-config.yaml"),
            workspace_path=str(tmp_path), prompt_manager=mp, executor=me)

    assert sessions == ["validate-auth-primary", "validate-auth-victim-b"], sessions
```

- [ ] **Step 3: 跑两个新测试确认失败**

Run: `pytest packages/core/tests/test_validate_authentication.py::test_multi_identity_uses_per_account_credentials packages/core/tests/test_validate_authentication.py::test_multi_identity_uses_distinct_browser_sessions -v`
Expected: FAIL（凭据测：`seen == {... 'victim-b':'userA' ...}` 不等；会话测：sessions 含 None）

- [ ] **Step 4: 实现 Branch B 重写**

用以下整块替换现有 Branch B（从 `# ── Branch B: 多身份 preflight 登录循环` 注释到函数末尾的 `return AuthValidationResult(success=True)`）：
```python
    # ── Branch B: 多身份 preflight 登录循环 + identity-manifest.json ──
    # 每个身份用自己的凭据、自己的 browser session、自己的 auth-state：
    #   - primary 用原 config（其凭据即 top-level authentication），session=validate-auth-primary
    #   - 每个 account 用 _write_per_identity_config 把其凭据提到 top-level authentication，
    #     session=validate-auth-{id}（独立 profile，杜绝 cookie/storage 串）
    scan_dir = Path(config_path).parent
    # 有序身份描述：(account_id, role, tier, state_file, account | None)
    specs: list[tuple[str, str | None, str, Path, Account | None]] = [
        ("primary", None, "low", auth_state_path(workspace_path), None)
    ]
    for acct in accounts:
        specs.append((acct.id, acct.role, acct.tier or "low",
                      auth_state_path(workspace_path, acct.id), acct))

    identities: list[IdentityRecord] = []
    temp_configs: list[Path] = []
    primary_failed = False
    primary_failure_point: str = "out_of_band"
    primary_failure_detail: str = "primary attacker login failed"

    try:
        for account_id, role, tier, state_file, acct in specs:
            if acct is None:
                cfg_path, session_id = config_path, "validate-auth-primary"
            else:
                try:
                    per_cfg = _write_per_identity_config(scan_dir, config, acct)
                    temp_configs.append(per_cfg)
                    cfg_path, session_id = str(per_cfg), f"validate-auth-{acct.id}"
                except Exception as e:
                    # config 准备失败 → 该身份不可用，不拖垮整体（同 non-primary 登录失败降级语义）
                    identities.append(IdentityRecord(
                        account_id=account_id, role=role, tier=tier,
                        auth_state_file=state_file.name, available=False,
                        failure_detail=f"identity config preparation failed: {e}"))
                    continue

            try:
                metrics = await executor.execute(
                    **_build_validate_auth_executor_kwargs(
                        web_url=web_url, config_path=cfg_path,
                        deliverables_path=deliverables_path, api_key=api_key,
                        repo_path=repo_path, state_file=state_file,
                        browser_session_id=session_id,
                        audit_logger=audit_logger, tool_audit_logger=tool_audit_logger,
                    )
                )
                so = (metrics.structured_output or {}) if metrics and metrics.structured_output is not None else {}
                ok = bool(so.get("login_success"))
            except Exception:
                ok = False
                so = {}

            identities.append(IdentityRecord(
                account_id=account_id, role=role, tier=tier,
                auth_state_file=state_file.name, available=ok,
                failure_detail=None if ok else (so.get("failure_detail") or "login failed"),
            ))

            if not ok and account_id == "primary":
                primary_failed = True
                if so:
                    primary_failure_point = so.get("failure_point", "out_of_band")
                    primary_failure_detail = so.get("failure_detail", "primary attacker login failed")
                break  # attacker 必须，fail-fast
    finally:
        # best-effort 清理临时 per-identity config（凭据同源 scan-config.yaml，用后即删）
        for cfg_file in temp_configs:
            try:
                cfg_file.unlink(missing_ok=True)
            except Exception:
                pass

    IdentityManifest(identities=identities).write(workspace_path)

    if primary_failed:
        return AuthValidationResult(
            success=False,
            failure_point=primary_failure_point,
            failure_detail=primary_failure_detail,
        )
    return AuthValidationResult(success=True)
```

- [ ] **Step 5: 跑两个新测试确认通过**

Run: `pytest packages/core/tests/test_validate_authentication.py::test_multi_identity_uses_per_account_credentials packages/core/tests/test_validate_authentication.py::test_multi_identity_uses_distinct_browser_sessions -v`
Expected: PASS

- [ ] **Step 6: 回归——跑所有多身份 + Branch A 测试**

Run: `pytest packages/core/tests/test_validate_authentication.py -v -k "multi_identity or victim_failure or primary_failure or no_accounts or per_identity or distinct_browser or write_per_identity or build_validate_auth"`
Expected: 全 PASS（含 Task 3 迁移的 3 个、Branch A byte-identical、helper、kwargs、新回归测）

- [ ] **Step 7: 提交**
```bash
git add packages/core/src/supernova_core/services/validate_authentication.py packages/core/tests/test_validate_authentication.py
git commit -m "fix(core): multi-identity login uses per-account credentials + isolated sessions

Branch B 循环每个身份用自己的凭据（_write_per_identity_config 提升到 top-level
authentication）+ 独立 browser_session_id（validate-auth-{id}），使 N 个 auth-state
真正是 N 个不同身份。Branch A 单身份路径 byte-identical。"
```

---

## Task 5: 清理测 + 凭据依赖成功测 + 全文件回归

**Files:**
- Modify: `packages/core/tests/test_validate_authentication.py`（仅新增 2 个测试）

**Interfaces:** 无（验证 Task 4 已落地行为）。

- [ ] **Step 1: 写清理测**

追加到测试文件末尾：
```python
@pytest.mark.asyncio
async def test_per_identity_temp_configs_cleaned(tmp_path):
    """循环后临时 scan-config-{id}.yaml 已删；auth-state 文件 + identity-manifest.json 保留。"""
    from supernova_core.models.config import Config, Authentication, Credentials, Account
    from supernova_core.services.validate_authentication import load_identity_manifest
    import yaml as _yaml

    cfg = Config(
        authentication=Authentication(login_type="form", login_url="https://x/l",
            credentials=Credentials(username="userA", password="pa")),
        accounts=[Account(id="victim-b", role="user", tier="low", credentials=Credentials(username="userB"))])

    async def fake_execute(**kwargs):
        sf = kwargs["prompt_variables"]["AUTH_STATE_FILE"]
        Path(sf).write_text(json.dumps({"cookies": [{"name": "s"}], "origins": []}))
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=100, structured_output={"login_success": True})

    me = MagicMock(); me.execute = AsyncMock(side_effect=fake_execute); mp = MagicMock()
    with patch("supernova_core.config.parser.parse_config", return_value=cfg), \
         patch("supernova_core.config.parser.distribute_config",
               return_value=MagicMock(authentication=cfg.authentication, accounts=cfg.accounts)):
        await validate_authentication(web_url="https://x", config_path=str(tmp_path / "scan-config.yaml"),
            workspace_path=str(tmp_path), prompt_manager=mp, executor=me)

    # 临时 per-identity config 已清理
    assert not (tmp_path / "scan-config-victim-b.yaml").exists()
    # auth-state 文件保留（下游 exploit 要用）
    assert (tmp_path / "auth-state.json").exists()
    assert (tmp_path / "auth-state-victim-b.json").exists()
    # manifest 保留
    assert load_identity_manifest(tmp_path) is not None
```

- [ ] **Step 2: 写凭据依赖成功测（更强 fake：错凭据→失败）**

追加到测试文件末尾：
```python
@pytest.mark.asyncio
async def test_multi_identity_login_success_depends_on_credentials(tmp_path):
    """fake 只认特定凭据；victim 给错密码 → 该身份 available=False，其余成功。
    证明循环产出的是真正不同的登录结果（非全 primary）。"""
    from supernova_core.models.config import Config, Authentication, Credentials, Account
    from supernova_core.services.validate_authentication import load_identity_manifest
    import yaml as _yaml

    valid_passwords = {"userA": "pa", "userB": "pb"}  # 注意：victim 故意给错密码 "WRONG"
    cfg = Config(
        authentication=Authentication(login_type="form", login_url="https://x/l",
            credentials=Credentials(username="userA", password="pa")),
        accounts=[Account(id="victim-b", role="user", tier="low",
            credentials=Credentials(username="userB", password="WRONG"))])

    async def fake_execute(**kwargs):
        cp = kwargs["config_path"]
        sf = kwargs["prompt_variables"]["AUTH_STATE_FILE"]
        data = _yaml.safe_load(Path(cp).read_text())
        u = data["authentication"]["credentials"]["username"]
        p = data["authentication"]["credentials"].get("password")
        ok = valid_passwords.get(u) == p
        if ok:
            Path(sf).write_text(json.dumps({"cookies": [{"name": "s"}], "origins": []}))
        from supernova_core.models.metrics import AgentMetrics
        return AgentMetrics(duration_ms=100, structured_output={"login_success": ok,
            "failure_detail": "bad password"})

    me = MagicMock(); me.execute = AsyncMock(side_effect=fake_execute); mp = MagicMock()
    with patch("supernova_core.config.parser.parse_config", return_value=cfg), \
         patch("supernova_core.config.parser.distribute_config",
               return_value=MagicMock(authentication=cfg.authentication, accounts=cfg.accounts)):
        r = await validate_authentication(web_url="https://x", config_path=str(tmp_path / "scan-config.yaml"),
            workspace_path=str(tmp_path), prompt_manager=mp, executor=me)

    assert r.success is True  # primary 成功即整体成功，victim 死不拖垮
    manifest = load_identity_manifest(tmp_path)
    by_id = {x.account_id: x for x in manifest.identities}
    assert by_id["primary"].available is True
    assert by_id["victim-b"].available is False  # 错密码 → 真的失败
```

- [ ] **Step 3: 跑这两个新测试**

Run: `pytest packages/core/tests/test_validate_authentication.py::test_per_identity_temp_configs_cleaned packages/core/tests/test_validate_authentication.py::test_multi_identity_login_success_depends_on_credentials -v`
Expected: PASS

- [ ] **Step 4: 跑整个测试文件做最终回归**

Run: `pytest packages/core/tests/test_validate_authentication.py -v`
Expected: 全 PASS（无 skip/xfail 异常、无回归）

- [ ] **Step 5: 提交**
```bash
git add packages/core/tests/test_validate_authentication.py
git commit -m "test(core): cover per-identity config cleanup + credential-dependent login"
```

---

## 真机验证（非 pytest，落地后人工跑）

auth-state 文件是否真持不同 cookie 只能在真实多账号目标验。用 ≥2 真实账号（如 admin + 2 普通用户）跑多身份黑盒探针，确认各 `auth-state-{id}.json` 含**不同**会话 cookie，且垂直/水平越权对比能产出预期判定。可扩展 `scripts/validate_*_task_probe.py` 或新增 multi-identity 探针。

## Self-Review（已完成）

- **Spec 覆盖**：§1 核心机制→Task 1（helper）+ Task 2（kwargs）+ Task 4（循环重写）；§2 primary 统一→Task 4 内联 specs（primary 走 validate-auth-primary，无特判）；§3 错误处理+清理→Task 4 try/except + finally、Task 5 清理测；§4 测试加固→Task 1/2/4/5 全部测。Branch A byte-identical→Task 2 不变量测 + 现有 `test_no_accounts_skips_manifest_byte_identical`。
- **Placeholder 扫描**：无 TBD/TODO；每步含可运行代码或命令。
- **类型一致**：`_write_per_identity_config(scan_dir, config, account)` 在 Task 1 定义、Task 4 消费签名一致；`browser_session_id` 在 Task 2 定义、Task 4 消费一致；`Account`/`Config` 来自 `models/config.py` 锚点。
