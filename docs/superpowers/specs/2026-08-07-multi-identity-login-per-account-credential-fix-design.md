# 多身份登录：按账号替换凭据 + 独立会话 修复设计

> 日期 2026-08-07 · 状态：Approved（待 writing-plans）· 分支 `feat/fork-py`
> 修复对象：`docs/superpowers/specs/2026-07-25-blackbox-dual-account-authz-design.md` 落地代码中**未实现**的「per-account 凭据替换」段（该 spec 第 114 行「各自的 credentials 填入 login_flow」）。

## 目标

让黑盒多身份（垂直/水平越权）preflight 登录**真正按每个账号各自的凭据登录、并在独立浏览器会话里登录**，使产出的 N 个 `auth-state-{id}.json` 是 N 个**真正不同身份**的会话——而非当前「N 次登录全用 primary 凭据、共享同一 session」产出的 N 份 primary 副本。

修复后，下游越权对比协议（已正确实现）才能拿到可用的跨用户证据。

## 背景与现状（已核实事实）

多身份 plumbing 端到端已就绪，唯独**登录环节**两处缺口：

### 缺口 1：登录时不换凭据（致命）

`validate_authentication.py` Branch B（约 `:240-293`）循环对 primary + 每个 account 各登一次、各写一个 `auth-state-{id}.json`，但：

- `_build_validate_auth_executor_kwargs`（约 `:128-156`）是单/多身份**共用**的唯一 kwargs 构造器，只接受 `config_path` 与 `state_file`，**从不接收 per-account 凭据**；循环里只变 `prompt_variables={"AUTH_STATE_FILE": str(state_file)}`。
- 循环构造 `id_list = [("primary", None, None), *(acct.id, acct.role, acct.tier) ...]`（约 `:243-245`），**没取 `acct.credentials`**；`role/tier` 仅进 manifest，不进登录。
- prompt 构造侧 `_build_auth_context(config)`（`prompts/manager.py` 约 `:320-338`）与 `build_login_instructions(authentication)`（约 `:340-397`）**只读 `config.authentication.credentials`（primary）**，`$username/$password/$totp` 占位符按 primary 替换。
- `Account.credentials` 除 config 加载时的危险模式校验（`config/parser.py` 约 `:60-80`）外，全 core/blackbox **无任何消费点**——是登录路径里的死数据。

**后果：** N 次登录全用 primary 的用户名/密码 → N 个 auth-state 文件全是 primary 会话 → 越权对比实为 primary 对 primary，无法产出真实跨用户证据。

### 缺口 2：登录期会话不隔离

所有登录跑在单一 `agent1` session（`models/agents.py` `BROWSER_SESSION_MAPPING[VALIDATE_AUTH] = "agent1"`）。即便修了凭据，登第二个账号时第一个的 session cookie 仍在，app 可能因「已认证」直接跳过登录页 → 第二身份根本没登进去。07-25 spec 本计划每身份独立 `validate-auth-{id}` session 防 cookie 串，未实现。

### 为什么下游没问题（本次不动）

exploit 期会话隔离与对比协议**已正确**：`get_identity_session_id`（`playwright_config_writer.py:34-37`）→ `agent-authz-{id}`；`build_identity_context`（`manager.py:399-461`）每身份一行 `session=… | state load auth-state-{id}.json`；独立 profile（`agent_browser_engine.py:117-143`）；对比矩阵 + `prompts/shared/_identities.txt`。**它只是消费了错误的（primary 副本）auth-state**。修好登录产出即可。

### 现有机制可白嫖（关键，决定方案零 prompt 改动）

`prompts/manager.py:132-137` 渲染时：
```python
session_id = (variables.get("browser_session_id")
              or variables.get("playwright_session")
              or BROWSER_SESSION_MAPPING.get(template_name, "agent1"))
```
即 `prompt_variables["browser_session_id"]` **已能覆盖默认 session**。validate-authentication prompt 已强制「Always pass `{{BROWSER_SESSION_FLAG}}` to every command」；`auth_save_command(session_id, auth_state_file)` 把该 session 的 cookie 存进对应 auth-state 文件。⇒ **session 隔离只需在循环里填 `browser_session_id`，零 prompt 改动。**

## 设计（Approach A）

改动**只在一个文件**：`packages/core/src/supernova_core/services/validate_authentication.py`。prompt builder、prompt 模板、executor 签名、下游 exploit——**全不动**。

### §1 核心机制：每身份一份 config + 每身份一个 session

把 Branch B 循环从「只换 state_file」升级为「每个身份 = (自己的 config_path, 自己的 session, 自己的 state_file)」：

```python
@dataclass
class _IdentityJob:
    account_id: str          # "primary" | acct.id
    role: str | None
    tier: str                # "high" | "low"
    config_path: str         # 该身份的登录 config（凭据已替换）
    session_id: str          # "validate-auth-{id}"
    state_file: Path         # auth-state.json | auth-state-{id}.json
```

```python
jobs: list[_IdentityJob] = []
# primary：用原 config（其凭据本就是 top-level authentication），不替换
jobs.append(_IdentityJob("primary", None, "low",
    config_path=config_path,
    session_id="validate-auth-primary",
    state_file=auth_state_path(workspace_path)))           # 无后缀，保持下游兼容
for acct in accounts:
    cfg = _write_per_identity_config(Path(config_path).parent, config, acct)
    jobs.append(_IdentityJob(acct.id, acct.role, acct.tier or "low",
        config_path=str(cfg),
        session_id=f"validate-auth-{acct.id}",
        state_file=auth_state_path(workspace_path, acct.id)))

for job in jobs:
    metrics = await executor.execute(
        **_build_validate_auth_executor_kwargs(
            web_url=web_url, config_path=job.config_path,
            deliverables_path=deliverables_path, api_key=api_key,
            repo_path=repo_path, state_file=job.state_file,
            browser_session_id=job.session_id,             # 新增 kwargs
            audit_logger=audit_logger, tool_audit_logger=tool_audit_logger,
        ))
    ...  # ok 判定 / 记 IdentityRecord / primary fail-fast —— 逻辑不变
```

**helper（凭据替换，不改 prompt 的关键）：**
```python
def _write_per_identity_config(scan_dir: Path, config: Config, account: Account) -> Path:
    """写一份单身份登录 config：把 account.credentials 提到 top-level authentication，
    accounts 清空。execute() 重新 parse 后 _build_auth_context / build_login_instructions
    自然读到该身份凭据（login_flow 的 $username/$password/$totp 占位符也按此身份替换）。"""
    per = config.model_copy(deep=True)
    per.authentication = per.authentication.model_copy(deep=True)
    per.authentication.credentials = account.credentials    # 仅换凭据
    per.accounts = []                                        # 单身份语义
    p = scan_dir / f"scan-config-{account.id}.yaml"
    p.write_text(yaml.safe_dump(per.model_dump(mode="json", exclude_none=True)))
    return p
```

**`_build_validate_auth_executor_kwargs` 加参数 `browser_session_id: str`，塞进 prompt_variables：**
```python
prompt_variables={"AUTH_STATE_FILE": str(state_file), "browser_session_id": browser_session_id}
```

Branch A（无 accounts 的单次登录路径，绝大多数扫描）**完全不动**：新增的 `browser_session_id` 参数默认 `None`；为 `None` 时不写入 `prompt_variables`，prompt 回落到 `BROWSER_SESSION_MAPPING` 默认（`agent1`）——Branch A 调用处无需改动、行为 byte-identical。仅 Branch B 显式传每身份 session。

### §2 primary 处理 + 循环统一

primary 也从共享 `agent1` 挪到 `validate-auth-primary`，**所有身份统一处理（无特判）**：

- 更干净：preflight recon 跑在 `agent1`，primary 挪走后登录不被 recon 残留 cookie 污染（attacker 身份更正确）。
- 安全：无下游依赖 primary 在 `agent1`——exploit 从 `auth-state.json` 文件 `state load`（session 无关）；primary 的 manifest 记录（`auth-state.json` 无后缀）不变。
- 循环无 `None if acct_id == "primary"` 特判，全部走 job 表。

### §3 错误处理 + 临时 config 清理

**语义不变，只吸收新失败模式：**
- primary 失败 → fail-fast（`break` + 返回 failure）。**不变**。
- 非 primary 失败 → `available=False`，扫描继续。**不变**。
- **新增**：`_write_per_identity_config` 写盘失败 → 该身份记 `IdentityRecord(available=False, failure_detail="identity config preparation failed: …")`，跳过其 `execute()`，扫描继续（与「非 primary 登录失败即降级」同语义）。primary 无需写 config，不引入此风险。

**清理：**
- `scan-config-{id}.yaml` 为临时输入，循环结束后 `finally` best-effort 删除（出错不影响扫描）。
- `auth-state-{id}.json` + `identity-manifest.json` **保留**（下游要用）。
- 敏感性：per-identity config 凭据来自 `scan-config.yaml` 同源同密级、同在 scan_dir、用后即删——符合现有「inline 凭据只留 scan_dir、不进加密 vault」卫生边界（CLAUDE.md §1），**不引入新暴露面**。

### §4 测试加固（抓住原始 bug 的关键）

当前测试致命伤：`fake_execute` 对每个身份都返回 `login_success=True`、只看 `AUTH_STATE_FILE`，**从不断言用了哪份凭据**——故「全用 primary 凭据」不可见。

`packages/core/tests/test_validate_authentication.py` 新增/改写：

1. **`test_multi_identity_uses_per_account_credentials`**（核心回归测）：fake executor 每次 capture `config_path`、parse 出 `authentication.credentials.username`。断言每身份 username **各不相同且==该账号 username**（非全 primary）。→ 当前 buggy 代码 FAIL，修复后 PASS。
2. **`test_multi_identity_uses_distinct_browser_sessions`**：capture 每次 `prompt_variables["browser_session_id"]`，断言全互异、==`validate-auth-{id}`、primary==`validate-auth-primary`。
3. **`test_per_identity_config_writer`**（单测 helper）：产出文件 `authentication.credentials`==account 的、`accounts==[]`、文件名 `scan-config-{id}.yaml`。
4. **`test_multi_identity_login_success_depends_on_credentials`**（更强 fake）：内置 account→password 映射，**仅当 config 凭据匹配对应账号才返回 success**——模拟真实「错密码→失败」，证明循环产出真正不同登录结果（某非 primary 给错凭据→`available=False`，其余成功）。
5. **保留并迁移**：现有 manifest 写入、primary fail-fast、非 primary 降级三测，重构后仍绿。
6. **清理测**：循环后 `scan-config-{id}.yaml` 已删，`auth-state-{id}.json` + manifest 仍在。

## 数据契约

**`_IdentityJob`**（模块内私有 dataclass）：
```python
{ account_id: str; role: str|None; tier: str; config_path: str; session_id: str; state_file: Path }
```

**per-identity config 文件**（`<scan_dir>/scan-config-{account.id}.yaml`）：base `Config` 的深拷贝，仅 `authentication.credentials` 替换为该 account 的、`accounts=[]`。其余（`login_url`/`login_type`/`login_flow`）不变。

**`prompt_variables`（每次 execute）**：`{"AUTH_STATE_FILE": str, "browser_session_id": "validate-auth-{id}"}`。

**`identity-manifest.json`**：结构不变（`account_id/role/tier/auth_state_file/available/failure_detail`），但 `available` 现在反映**真实**登录结果。

## 范围边界（不做）

- 不改 prompt builder / prompt 模板 / executor.execute 签名 / 下游 exploit（已正确）。
- 不改 `AuthProfile` → `accounts[]` 的上游传递链（已正确，见 2026-08-06 spec）。
- `high_priv_names=["admin"]` 硬编码（`scan_manager.py`）仍 deferred，不在本次。
- Branch A（单身份、无 accounts）**完全不动**：新增 `browser_session_id` 参数默认 `None`→回落 `agent1`，调用处不改、行为不变。仅 Branch B（多身份）每身份独立 session——改动面收缩到多身份路径，单身份零回归风险。
- 真机不同 cookie 的端到端证明不做单测，列为手动真机验证（下）。

## 真机验证（非 pytest，单列）

auth-state 文件是否真持不同 cookie 只能在真实多账号目标验。手动步：用 ≥2 真实账号（如 admin + 2 普通用户）跑多身份黑盒探针（扩展 `scripts/validate_*_task_probe.py` 或新增 multi-identity 探针），确认各 `auth-state-{id}.json` 含**不同**会话 cookie，且垂直/水平越权对比能产出预期判定。

## 依赖与风险

- **依赖**：`config.model_copy(deep=True)`（pydantic v2）、`Account.credentials` 为 `Credentials` 实例（已核实）。
- **风险**：per-identity config 重新过 `parse_config` 校验——因 `accounts=[]` 且 `authentication` 合法，校验必过；唯一注意 `login_flow` 占位符替换依赖 `authentication.credentials` 非空（account 必有 username，password 可空但通常非空）。
- **回滚**：改动集中单文件 + 测试，回滚成本低。

---

下一步：writing-plans 拆分实现计划（待启动）。
