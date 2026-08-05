# 认证档案库 + 独立验证 + 黑盒扫描复用 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 web 平台落 workspace 级、多角色、Fernet 加密的认证档案库 + 独立"认证管理"页(每角色可单独"测试登录")+ 黑盒扫描发起页可选已保存档案/角色复用登录。

**Architecture:** 后端新增独立 `AuthValidationWorkflow`(Temporal,复用 core `validate_authentication` 服务)驱动 agent-browser 真实登录,失败不抛异常(降级返回失败点);web 新增 `AuthProfileStore`(镜像 `WsConfigStore`,Fernet 字段级加密嵌套遍历)+ CRUD/验证 API + 扫描页选档案展开成单 `credentials` 喂 core(**core 扫描流程零改**)。前端新增 `/p/:workspace/auth-profiles` ws 级 tab + 扫描页 Step4 三态来源切换。core 完全不改(不变量 1/2)。

**Tech Stack:** Python(temporalio + FastAPI + pydantic + cryptography/Fernet + PyYAML)、React/TypeScript(Vite + Radix UI + react-i18next + vitest + msw)。

## Global Constraints

(spec §12 不变量 + 项目约定,每个 task 隐含遵守)

1. **core 扫描流程零改**:`BlackboxScanWorkflow` / exploit / 双轨不碰;`validate_authentication` 服务复用不重写(探针只是"不抛异常的包装")。
2. **`scan-config.yaml` 明文合流点不变**:web 写 YAML → core 读 YAML(CLI/Web 合流)⇒ per-scan / probe YAML 必须明文(core `Authentication.model_validate` 不解密)。明文债靠 0600 + 结束即删缓解,不在本计划消除。
3. **双引擎一致**:验证经 `run_claude_prompt` 统一抽象,不引引擎特化分支。
4. **WsConfigStore 不被污染**:认证档案走独立 `AuthProfileStore`(`workspaces/<ws>/auth-profiles.yaml`),不塞进 `config.yaml`。
5. **D1–D4 不回退**:不重新引入 `success_condition` 或 cookie 兜底。
6. **加密 key 同源**:复用 `CredentialVault` 的 Fernet 实例(`SUPERNOVA_MASTER_KEY` env > `workspaces/.master_key` 文件),不新造加密器。
7. **权限**:看/用 `Depends(workspace_member)`,改/删 `Depends(workspace_manager)`,ws 级隔离;admin 短路放行。
8. **脱敏 / 空串保留**:`GET` 返敏感字段 `MASKED = "••••"` if 值 else `None`;`PUT` 空串敏感字段 = 不改(保留原密文)。
9. **测试纪律**:只跑改动相关测试文件(memory `feat-fork-py-test-gotchas`:全套 pytest 有预存挂起/失败)。Python 测试从包目录跑 `uv run pytest tests/<file> -v`;前端在 `packages/web/frontend` 跑 `./node_modules/.bin/vitest run <file>`、`./node_modules/.bin/tsc --noEmit`、`./node_modules/.bin/vite build`(**别用 pnpm**)。
10. **改 web/worker src 生效须 rebuild `supernova-worker` + web 镜像**(部署/冒烟阶段注意,单测不受影响)。

## Plan-level 决策(收口 spec §6.5 待确认项)

- **workflow 同文件**:`AuthValidationWorkflow` 放 `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py`(与 `BlackboxScanWorkflow` 同文件,轻量)。
- **workflow 入参**:`BlackboxAuthValidationInput(BasePipelineInput)` 新增于 `shared.py`(字段:`web_url`/`workspace_path` + 继承的 `config_path`/`api_key`)。
- **探针 activity 入参**:复用现有 `BlackboxActivityInput`(已有 `web_url`/`config_path`/`workspace_path`/`api_key`,够用),不新造 activity 入参类型。
- **探针 workflow 轻量**:仅 `log_phase_start_activity(phase="auth-validation")` → `run_auth_validation_probe` → 返回 result。**不接 `setup_display`/`finalize_summary`**——已核验 `validate_authentication` 与 `AgentExecutor` 不触 `get_audit_session`,而 `log_phase_start_activity` 在无 session 时落到 `NullAuditSession`(安全 no-op 默认),故无需 AuditSession 注入。
- **嵌套加密**:按已知 schema 路径**显式遍历**(非泛型递归)——`credentials[].{password, totp_secret}` + `credentials[].email_login.{password, totp_secret}`。
- **AuthProfile 模型用 pydantic `BaseModel`**(校验 + `model_dump` 落盘干净)。
- **前端 AuthProfilesPage 是 ws-child tab**(`/p/:workspace/auth-profiles`,范式对齐 `WsSettingsTab`/`ReposTab`,在 `WorkspaceDetail` 的 `<Outlet/>` 内渲染),CRUD 对话框范式对齐 `CreateUserDialog`/`ConfirmDeleteUserDialog`。

## File Structure

**新建:**
- `packages/web/src/supernova_web/components/auth_profile_store.py` — AuthProfile pydantic 模型 + AuthProfileStore(读写 YAML + 显式路径加密/脱敏/空串保留)。
- `packages/web/src/supernova_web/api/auth_profiles.py` — CRUD + test + verify-status endpoints。
- `packages/web/frontend/src/api/authProfiles.ts` — 前端 API client(Pattern B 独立模块)。
- `packages/web/frontend/src/pages/AuthProfilesPage.tsx` + `packages/web/frontend/src/pages/AuthProfilesPage.test.tsx` — 认证管理页 + 测试。
- 测试文件(各 task 内列出)。

**修改:**
- `packages/blackbox/src/supernova_blackbox/pipeline/shared.py` — 加 `BlackboxAuthValidationInput`。
- `packages/blackbox/src/supernova_blackbox/pipeline/activities.py` — 加 `run_auth_validation_probe` activity。
- `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py` — 加 `AuthValidationWorkflow`。
- `packages/blackbox/src/supernova_blackbox/worker.py` — CLI worker 注册新 activity。
- `packages/worker/src/supernova_worker/runner.py` — WEB worker 注册新 activity + 新 workflow(🔴 无 test 兜底)。
- `packages/web/src/supernova_web/models.py` — `ScanRequest` 加 `auth_profile_id`/`auth_credential_id` + XOR validator。
- `packages/web/src/supernova_web/components/scan_manager.py` — `start_auth_validation`/`get_auth_validation_result`/profile 展开/stale probe reaper + 构造增 `auth_profile_store`。
- `packages/web/src/supernova_web/app.py` — 构造 `AuthProfileStore` 挂 `app.state` + 挂 router + lifespan reaper 钩子。
- `packages/web/frontend/src/api/types.ts` — `AuthProfile`/`AuthProfileCredential`/`VerifyStatus` 类型 + `ScanRequest` 增字段。
- `packages/web/frontend/src/router.tsx` — 加 `/p/:workspace/auth-profiles` child。
- `packages/web/frontend/src/routes/WorkspaceDetail/index.tsx` — header 加"认证管理"入口。
- `packages/web/frontend/src/pages/ScanNewPage.tsx` — `AuthFormState` 三态 + `buildBody`/`validateAuth`/`authFromPayload`。
- `packages/web/frontend/src/components/ScanFormFields.tsx` — `AuthFields` 三态来源切换。
- `packages/web/frontend/src/locales/{zh,en}.json` — 顶层 `authProfiles.*`(parity test 护栏)。

---

## Task 1: `run_auth_validation_probe` activity + 双 worker 注册

**Files:**
- Modify: `packages/blackbox/src/supernova_blackbox/pipeline/activities.py`(末尾追加 activity + 补 import)
- Modify: `packages/blackbox/src/supernova_blackbox/worker.py:137`(CLI activities list)
- Modify: `packages/worker/src/supernova_worker/runner.py:86`(WEB bb activities list)
- Test: `packages/blackbox/tests/test_auth_validation_probe.py`(新)

**Interfaces:**
- Consumes: core `validate_authentication`(`supernova_core.services.validate_authentication`,纯 keyword-only,必传 `prompt_manager`+`executor`);`AuthValidationResult`(`@dataclass`,`success: bool` / `failure_point: str | None` / `failure_detail: str | None`);`BlackboxActivityInput`(`.shared`,已有 `web_url`/`config_path`/`workspace_path`/`api_key`)。
- Produces: `async def run_auth_validation_probe(input: BlackboxActivityInput) -> AuthValidationResult`(`@activity.defn`,**不抛异常**,失败降级返回 `AuthValidationResult(success=False, ...)`)。

- [ ] **Step 1: 写失败测试 `test_auth_validation_probe.py`**

```python
"""run_auth_validation_probe:独立认证验证探针(失败不抛异常,降级返回)。"""
import pytest
from unittest.mock import AsyncMock, patch

from supernova_core.services.validate_authentication import AuthValidationResult
from supernova_blackbox.pipeline.activities import run_auth_validation_probe
from supernova_blackbox.pipeline.shared import BlackboxActivityInput


def _input():
    return BlackboxActivityInput(
        web_url="http://target/", config_path="/c.yaml", workspace_path="/wp"
    )


@pytest.mark.asyncio
async def test_probe_success_passes_through_result():
    expected = AuthValidationResult(success=True)
    with patch(
        "supernova_blackbox.pipeline.activities.validate_authentication",
        new=AsyncMock(return_value=expected),
    ):
        result = await run_auth_validation_probe(_input())
    assert result is expected  # 透传:不自判 structured output


@pytest.mark.asyncio
async def test_probe_failure_result_passed_through():
    """validate_authentication 内置 success=False 映射(如 no-structured-output),探针透传。"""
    expected = AuthValidationResult(
        success=False, failure_point="out_of_band", failure_detail="no verdict"
    )
    with patch(
        "supernova_blackbox.pipeline.activities.validate_authentication",
        new=AsyncMock(return_value=expected),
    ):
        result = await run_auth_validation_probe(_input())
    assert result.success is False
    assert result.failure_point == "out_of_band"


@pytest.mark.asyncio
async def test_probe_provider_exception_does_not_raise():
    """provider 异常(如引擎抛错)被吞,降级 success=False out_of_band(仿 run_endpoint_verify)。"""
    with patch(
        "supernova_blackbox.pipeline.activities.validate_authentication",
        new=AsyncMock(side_effect=RuntimeError("engine down")),
    ):
        result = await run_auth_validation_probe(_input())
    assert result.success is False
    assert result.failure_point == "out_of_band"
    assert "engine down" in (result.failure_detail or "")


@pytest.mark.asyncio
async def test_probe_calls_validate_with_prompt_manager_and_executor():
    """探针体内现造 prompt_manager + executor 并传入(对齐 run_blackbox_auth_validation)。"""
    with patch(
        "supernova_blackbox.pipeline.activities.validate_authentication",
        new=AsyncMock(return_value=AuthValidationResult(success=True)),
    ) as m:
        await run_auth_validation_probe(_input())
    _, kwargs = m.call_args
    assert kwargs["web_url"] == "http://target/"
    assert kwargs["config_path"] == "/c.yaml"
    assert kwargs["workspace_path"] == "/wp"
    assert "prompt_manager" in kwargs and "executor" in kwargs  # 必传,非 None
```

- [ ] **Step 2: 跑测试验证失败**

```bash
cd packages/blackbox && uv run pytest tests/test_auth_validation_probe.py -v
```
Expected: FAIL(`run_auth_validation_probe` 未定义 / ImportError)。

- [ ] **Step 3: 补 import + 实现 activity**

在 `activities.py` 顶部 import 区,把 `validate_authentication` 所在 import 行扩成同时引入 `AuthValidationResult`(`validate_authentication` 已被 `run_blackbox_auth_validation` 使用故已 import;只需在同一行加 `AuthValidationResult`):

```python
from supernova_core.services.validate_authentication import (
    validate_authentication,
    AuthValidationResult,
)
```
> 若现有 import 是单行 `from ... import validate_authentication`,改为上面两行形式。`AgentExecutor`/`PromptManager`/`Path`/`BlackboxActivityInput`/`activity`/`ApplicationFailure` 均已在文件头 import,无需新增。

在 `activities.py` 末尾追加:

```python
@activity.defn
async def run_auth_validation_probe(input: BlackboxActivityInput) -> AuthValidationResult:
    """独立认证验证探针:驱动 validate_authentication 真实登录,失败不抛异常(降级返回)。

    与 run_blackbox_auth_validation 区别:后者在扫描流程内,失败抛 ApplicationFailure
    触发 fail-fast;本探针供"认证管理页 测试登录"独立入口,失败只回失败点不触发扫描。
    AuditSession 不依赖:validate_authentication/AgentExecutor 不触 get_audit_session
    (无 session 时 log_phase_start 落 NullAuditSession 安全 no-op),故无需 setup_display。
    透传 validate_authentication 的 result(其内置 no-structured-output → success=False 映射)。
    """
    prompts_dir = Path(__file__).resolve().parents[5] / "prompts"
    prompt_manager = PromptManager(prompts_dir)
    executor = AgentExecutor(prompt_manager)
    try:
        return await validate_authentication(
            web_url=input.web_url,
            config_path=input.config_path,
            workspace_path=input.workspace_path or "",
            prompt_manager=prompt_manager,
            executor=executor,
            api_key=input.api_key,
        )
    except Exception as e:
        # 降级:不 raise(仿 run_endpoint_verify activities.py:349-356)
        return AuthValidationResult(
            success=False,
            failure_point="out_of_band",
            failure_detail=f"{type(e).__name__}: {e}",
        )
```

- [ ] **Step 4: 注册进两个 worker(让两个 activity 注册护栏 test 转绿)**

`packages/blackbox/src/supernova_blackbox/worker.py:137` activities list 加 `run_auth_validation_probe`(与 `run_blackbox_auth_validation` 并列,需在文件头 import 它):

```python
        activities=[
            run_blackbox_preflight, run_blackbox_auth_validation,
            run_auth_validation_probe,
            run_exploit_agent, run_endpoint_verify,
            # ...其余不动
```

`packages/worker/src/supernova_worker/runner.py:86` bb_worker activities list 加 `run_auth_validation_probe`(不别名,范式同 `run_endpoint_verify`,需在 import 区从 blackbox activities 引入):

```python
        activities=[
            run_blackbox_preflight, run_blackbox_auth_validation,
            run_auth_validation_probe,
            run_exploit_agent, run_endpoint_verify, validate_exploitation_queue, bb_assemble_report,
            # ...其余不动
```

- [ ] **Step 5: 跑探针测试 + 两个注册护栏 test**

```bash
cd packages/blackbox && uv run pytest tests/test_auth_validation_probe.py tests/test_worker.py -v
cd packages/worker && uv run pytest tests/test_runner.py -v
```
Expected: 全 PASS。`test_worker.py::test_all_activities_registered` 与 `test_runner.py::test_run_worker_registers_all_defined_activities` 同时绿(新 `@activity.defn` 加入后,两个 worker list 都补齐才转绿——这正是护栏意图)。

- [ ] **Step 6: Commit**

```bash
git add packages/blackbox/src/supernova_blackbox/pipeline/activities.py \
        packages/blackbox/src/supernova_blackbox/worker.py \
        packages/worker/src/supernova_worker/runner.py \
        packages/blackbox/tests/test_auth_validation_probe.py
git commit -m "feat(blackbox): run_auth_validation_probe 探针 activity(不抛异常)+ 双 worker 注册"
```

---

## Task 2: `AuthValidationWorkflow` + `BlackboxAuthValidationInput` + workflow 注册

**Files:**
- Modify: `packages/blackbox/src/supernova_blackbox/pipeline/shared.py`(加 `BlackboxAuthValidationInput`)
- Modify: `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py`(加 `AuthValidationWorkflow`)
- Modify: `packages/worker/src/supernova_worker/runner.py:84`(WEB workflows list,🔴 无 test 兜底)
- Test: `packages/blackbox/tests/test_auth_validation_workflow.py`(新)

**Interfaces:**
- Consumes: `BasePipelineInput`(`supernova_core.models.base`,有 `config_path`/`api_key` 等);Task 1 的 `run_auth_validation_probe` + `log_phase_start_activity`;`retry_for("auth-validation")`(已存在,max_attempts=3)。
- Produces: `BlackboxAuthValidationInput`(dataclass,`web_url`/`workspace_path` + 继承字段);`AuthValidationWorkflow.run(input) -> AuthValidationResult`。

- [ ] **Step 1: 写失败测试 `test_auth_validation_workflow.py`**

```python
"""AuthValidationWorkflow 编排:log_phase(auth-validation) → probe → 透传 result。"""
import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from supernova_core.services.validate_authentication import AuthValidationResult
from supernova_blackbox.pipeline.shared import BlackboxAuthValidationInput
from supernova_blackbox.pipeline.workflows import AuthValidationWorkflow


@pytest.mark.asyncio
async def test_workflow_orchestration_returns_probe_result():
    phases = []

    @activity.defn
    async def log_phase_start_activity(i):
        phases.append(getattr(i, "phase", None) or (i.get("phase") if isinstance(i, dict) else None))

    @activity.defn
    async def run_auth_validation_probe(i):
        return AuthValidationResult(success=True)

    inp = BlackboxAuthValidationInput(
        web_url="http://target/", config_path="/c.yaml", workspace_path="/wp"
    )
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-auth",
            workflows=[AuthValidationWorkflow],
            activities=[log_phase_start_activity, run_auth_validation_probe],
        ):
            result = await env.client.execute_workflow(
                AuthValidationWorkflow.run, inp, id="w-auth", task_queue="tq-auth"
            )
    assert phases == ["auth-validation"]
    assert result["success"] is True


@pytest.mark.asyncio
async def test_workflow_requires_web_url():
    @activity.defn
    async def log_phase_start_activity(i):
        pass

    @activity.defn
    async def run_auth_validation_probe(i):
        return AuthValidationResult(success=True)

    inp = BlackboxAuthValidationInput(web_url="", config_path="/c.yaml", workspace_path="/wp")
    async with await WorkflowEnvironment.start_local() as env:
        async with Worker(
            env.client, task_queue="tq-auth2",
            workflows=[AuthValidationWorkflow],
            activities=[log_phase_start_activity, run_auth_validation_probe],
        ):
            with pytest.raises(Exception):
                await env.client.execute_workflow(
                    AuthValidationWorkflow.run, inp, id="w-auth2", task_queue="tq-auth2"
                )
```

- [ ] **Step 2: 跑测试验证失败**

```bash
cd packages/blackbox && uv run pytest tests/test_auth_validation_workflow.py -v
```
Expected: FAIL(`BlackboxAuthValidationInput`/`AuthValidationWorkflow` 未定义)。

- [ ] **Step 3: 在 `shared.py` 加 `BlackboxAuthValidationInput`**

`shared.py` 已 import `BasePipelineInput`(`from supernova_core.models.base import BasePipelineInput`)。在 `BlackboxPipelineInput` 定义后追加:

```python
@dataclass
class BlackboxAuthValidationInput(BasePipelineInput):
    """AuthValidationWorkflow 入参(独立认证验证探针,非扫描流程)。

    仅承载探针所需:web_url(=login_url)+ config_path(probe scan-config.yaml)+
    workspace_path(probe 目录,auth-state.json 落点)+ api_key。不跑扫描其余步骤。
    字段需有默认值(BasePipelineInput 字段均有默认,dataclass 不允许 default 后非 default)。
    """
    web_url: str = ""
    workspace_path: str | None = None
```

- [ ] **Step 4: 在 `workflows.py` 加 `AuthValidationWorkflow`**

在 `BlackboxScanWorkflow` 类定义之后追加(workflows.py 头部已 import `workflow`/`activities`/`retry_for`/`timedelta`/`BlackboxActivityInput`;需补 import `BlackboxAuthValidationInput` 与 `AuthValidationResult`):

```python
from .shared import BlackboxAuthValidationInput  # 加到现有 .shared import 行
from supernova_core.services.validate_authentication import AuthValidationResult


@workflow.defn
class AuthValidationWorkflow:
    """独立认证验证 workflow(认证管理页"测试登录"):只跑 auth 段,不跑扫描。

    不能复用 BlackboxScanWorkflow:后者强依赖白盒产物(workflows.py:248-280 无白盒 queue
    抛 DELIVERABLE_NOT_FOUND fail-fast)。本 workflow 仅 log_phase + probe + 返回 result。
    """

    @workflow.run
    async def run(self, input: BlackboxAuthValidationInput) -> AuthValidationResult:
        if not input.web_url:
            raise ValueError("BlackboxAuthValidationInput.web_url is required")
        act_input = BlackboxActivityInput(
            web_url=input.web_url,
            config_path=input.config_path,
            workspace_path=input.workspace_path,
            api_key=input.api_key,
        )
        await workflow.execute_activity(
            activities.log_phase_start_activity,
            BlackboxActivityInput(**{**act_input.__dict__, "phase": "auth-validation"}),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=retry_for("log"),
        )
        return await workflow.execute_activity(
            activities.run_auth_validation_probe, act_input,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_for("auth-validation"),
        )
```

> 注意 `AuthValidationResult` 是 `@dataclass`,Temporal 序列化为 dict 返回,故 Step 1 测试断言 `result["success"]`。

- [ ] **Step 5: 注册 workflow 进 WEB worker runner.py:84(🔴 无 test 兜底,极易漏)**

`packages/worker/src/supernova_worker/runner.py` import 区加 `AuthValidationWorkflow`(从 `supernova_blackbox.pipeline.workflows`,与 `BlackboxScanWorkflow` 并列),并把 `:84` 改为:

```python
        workflows=[BlackboxScanWorkflow, AuthValidationWorkflow],
```

> 这是本计划最高危 wiring:`workflows=[...]` 无注册护栏(只有 activity 护栏)。漏加 → `start_workflow(AuthValidationWorkflow.run, ..., task_queue=WEB_TASK_QUEUE_BLACKBOX)` 提交后无 worker 能跑 → 卡死/超时。CLI `worker.py:135` 的 `workflows=[BlackboxScanWorkflow]` **不改**(认证管理是 web 功能,CLI 不消费此 workflow)。

- [ ] **Step 6: 跑测试**

```bash
cd packages/blackbox && uv run pytest tests/test_auth_validation_workflow.py tests/test_auth_validation_probe.py -v
```
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add packages/blackbox/src/supernova_blackbox/pipeline/shared.py \
        packages/blackbox/src/supernova_blackbox/pipeline/workflows.py \
        packages/worker/src/supernova_worker/runner.py \
        packages/blackbox/tests/test_auth_validation_workflow.py
git commit -m "feat(blackbox): AuthValidationWorkflow + BlackboxAuthValidationInput + WEB worker 注册"
```

---

## Task 3: AuthProfile 模型 + AuthProfileStore(加密/脱敏/空串保留)

**Files:**
- Create: `packages/web/src/supernova_web/components/auth_profile_store.py`
- Test: `packages/web/tests/test_auth_profile_store.py`(新)

**Interfaces:**
- Consumes: `CredentialVault`(`components/credential_vault.py`,`encrypt/decrypt: str|None -> str|None`,InvalidToken→None 降级);core `Authentication`/`Credentials`/`EmailLogin`(`supernova_core.models.config`,字段对齐)。
- Produces: pydantic 模型 `VerifyStatus`/`AuthProfileCredential`/`AuthProfile`;`AuthProfileStore(workspaces_dir, vault)` 读写 `workspaces/<ws>/auth-profiles.yaml`(list[AuthProfile]);`credential_to_authentication(profile, cred) -> Authentication`(Task 4/8 复用)。

- [ ] **Step 1: 写失败测试 `test_auth_profile_store.py`**

```python
"""AuthProfileStore:读写 + 显式路径加密/解密 + 嵌套 email_login + 脱敏 + 空串保留。"""
from pathlib import Path
from cryptography.fernet import Fernet
from supernova_web.components.credential_vault import CredentialVault
from supernova_web.components.auth_profile_store import (
    AuthProfileStore, AuthProfile, AuthProfileCredential, EmailLoginCred,
    credential_to_authentication,
)


def _vault(tmp_path):
    return CredentialVault(tmp_path / ".master_key")


def _profile():
    return AuthProfile(
        id="prof_1", name="NodeGoat", login_url="http://t/", login_type="form",
        login_flow=["打开登录页", "成功标志:URL 含 /dashboard"],
        credentials=[
            AuthProfileCredential(id="cred_a", role="admin", username="admin",
                                  password="pw", email_login=EmailLoginCred(
                                      address="a@x.com", password="epw", totp_secret="et")),
            AuthProfileCredential(id="cred_b", role="user", username="u1", password=None),
        ],
    )


def test_roundtrip_decrypts_all_sensitive_paths(tmp_path):
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write("ws1", [_profile()])
    # 落盘密文(非明文)
    raw = (tmp_path / "ws1" / "auth-profiles.yaml").read_text("utf-8")
    assert "pw" not in raw and "epw" not in raw and "et" not in raw
    # 读回解密还原
    loaded = store.read("ws1")
    assert loaded[0].credentials[0].password == "pw"
    assert loaded[0].credentials[0].email_login.password == "epw"
    assert loaded[0].credentials[0].email_login.totp_secret == "et"


def test_mask_for_get_returns_masked_or_none(tmp_path):
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write("ws1", [_profile()])
    masked = store.read_masked("ws1")
    cred = masked[0].credentials[0]
    assert cred.password == "••••"          # 有值 → 掩码
    assert masked[0].credentials[1].password is None  # 无值 → None


def test_apply_update_empty_secret_keeps_existing(tmp_path):
    store = AuthProfileStore(tmp_path, _vault(tmp_path))
    store.write("ws1", [_profile()])
    # PUT 空串 password + totp_secret = 不改(保留原密文)
    store.apply_update("ws1", "prof_1", "cred_a",
                       username="admin2", password="", totp_secret="")
    cred = store.read("ws1")[0].credentials[0]
    assert cred.username == "admin2"          # 非敏感字段已更新
    assert cred.password == "pw"              # 原密文保留


def test_credential_to_authentication_aligns_core_schema(tmp_path):
    auth = credential_to_authentication(_profile(), _profile().credentials[0])
    assert auth.login_type == "form"
    assert auth.login_url == "http://t/"
    assert auth.credentials.username == "admin"
    assert auth.credentials.password == "pw"
    assert auth.credentials.email_login.address == "a@x.com"
    assert auth.login_flow == ["打开登录页", "成功标志:URL 含 /dashboard"]
```

- [ ] **Step 2: 跑测试验证失败**

```bash
cd packages/web && uv run pytest tests/test_auth_profile_store.py -v
```
Expected: FAIL(模块不存在)。

- [ ] **Step 3: 实现 `auth_profile_store.py`**

```python
"""workspace 级认证档案库:多角色凭据 + Fernet 加密落盘 + 脱敏/空串保留。

范式镜像 WsConfigStore(独立 store,不污染 config.yaml);加密复用 CredentialVault 的
Fernet 实例(key 同源)。CredentialVault.encrypt/decrypt 是字段级 str|None→str|None,不
支持嵌套——这里按已知 schema 路径显式遍历 credentials[].{password,totp_secret} 与
credentials[].email_login.{password,totp_secret}(非泛型递归,更稳)。
"""
from __future__ import annotations

from dataclasses import asdict  # noqa: F401  (保持与 ws_config_store 风格一致可选)
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

import yaml
from pydantic import BaseModel

from supernova_core.models.config import Authentication, Credentials, EmailLogin
from supernova_web.components.credential_vault import CredentialVault

AUTH_PROFILES_FILENAME = "auth-profiles.yaml"
MASKED = "••••"
# 显式敏感路径(credential 级 + email_login 级)
_CRED_SECRET_FIELDS = ("password", "totp_secret")


class VerifyStatus(BaseModel):
    state: Literal["unverified", "success", "failed"] = "unverified"
    failure_point: str | None = None  # username_or_password | totp_secret | out_of_band
    failure_detail: str | None = None
    last_verified_at: str | None = None


class EmailLoginCred(BaseModel):
    address: str
    password: str | None = None
    totp_secret: str | None = None


class AuthProfileCredential(BaseModel):
    id: str
    role: str
    username: str
    password: str | None = None
    totp_secret: str | None = None
    email_login: EmailLoginCred | None = None
    verify_status: VerifyStatus = VerifyStatus()


class AuthProfile(BaseModel):
    id: str
    name: str
    login_url: str
    login_type: Literal["form", "sso", "api", "basic"]
    login_flow: list[str] | None = None
    credentials: list[AuthProfileCredential]
    created_at: str | None = None
    updated_at: str | None = None


def _validate_ws_segment(ws: str) -> None:
    if not ws or "/" in ws or ws in (".", ".."):
        raise ValueError("invalid workspace name")


def _encrypt_credential(cred: dict, vault: CredentialVault) -> dict:
    for f in _CRED_SECRET_FIELDS:
        cred[f] = vault.encrypt(cred.get(f))
    el = cred.get("email_login")
    if el:
        for f in _CRED_SECRET_FIELDS:
            el[f] = vault.encrypt(el.get(f))
    return cred


def _decrypt_credential(cred: dict, vault: CredentialVault) -> dict:
    for f in _CRED_SECRET_FIELDS:
        cred[f] = vault.decrypt(cred.get(f))
    el = cred.get("email_login")
    if el:
        for f in _CRED_SECRET_FIELDS:
            el[f] = vault.decrypt(el.get(f))
    return cred


def _mask_credential(cred: dict) -> dict:
    for f in _CRED_SECRET_FIELDS:
        cred[f] = MASKED if cred.get(f) else None
    el = cred.get("email_login")
    if el:
        for f in _CRED_SECRET_FIELDS:
            el[f] = MASKED if el.get(f) else None
    return cred


def credential_to_authentication(profile: AuthProfile, cred: AuthProfileCredential) -> Authentication:
    """把档案某角色展开成 core 单 credentials Authentication(scan-probe / 扫描复用)。"""
    email_login = None
    if cred.email_login:
        email_login = EmailLogin(
            address=cred.email_login.address,
            password=cred.email_login.password,
            totp_secret=cred.email_login.totp_secret,
        )
    return Authentication(
        login_type=profile.login_type,
        login_url=profile.login_url,
        credentials=Credentials(
            username=cred.username,
            password=cred.password,
            totp_secret=cred.totp_secret,
            email_login=email_login,
        ),
        login_flow=profile.login_flow,
    )


class AuthProfileStore:
    def __init__(self, workspaces_dir: Path, vault: CredentialVault):
        self._workspaces_dir = Path(workspaces_dir).resolve()
        self._vault = vault

    def _path(self, ws: str) -> Path:
        _validate_ws_segment(ws)
        p = (self._workspaces_dir / ws / AUTH_PROFILES_FILENAME).resolve()
        if not p.is_relative_to(self._workspaces_dir):
            raise ValueError("invalid workspace name")
        return p

    def read(self, ws: str) -> list[AuthProfile]:
        """读 + 解密 → list[AuthProfile](内存明文)。"""
        path = self._path(ws)
        if not path.exists():
            return []
        data = yaml.safe_load(path.read_text("utf-8")) or []
        for prof in data:
            for cred in prof.get("credentials") or []:
                _decrypt_credential(cred, self._vault)
        return [AuthProfile.model_validate(p) for p in data]

    def read_masked(self, ws: str) -> list[AuthProfile]:
        """读 + 解密 + 脱敏 → GET 响应态(敏感字段 MASKED if 值 else None)。"""
        profiles = self.read(ws)
        out = []
        for prof in profiles:
            d = prof.model_dump(mode="json")
            for cred in d["credentials"]:
                _mask_credential(cred)
            out.append(AuthProfile.model_validate(d))
        return out

    def get(self, ws: str, profile_id: str) -> AuthProfile | None:
        for p in self.read(ws):
            if p.id == profile_id:
                return p
        return None

    def write(self, ws: str, profiles: list[AuthProfile]) -> None:
        """加密敏感字段后落盘(整体覆盖写)。"""
        path = self._path(ws)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = []
        for prof in profiles:
            d = prof.model_dump(mode="json")
            for cred in d["credentials"]:
                _encrypt_credential(cred, self._vault)
            data.append(d)
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def upsert_profile(self, ws: str, profile: AuthProfile) -> AuthProfile:
        profiles = self.read(ws)
        if not profile.id:
            profile.id = f"prof_{uuid4().hex[:10]}"
        profile.updated_at = self._now()
        if not profile.created_at:
            profile.created_at = profile.updated_at
        for c in profile.credentials:
            if not c.id:
                c.id = f"cred_{uuid4().hex[:10]}"
        profiles = [p for p in profiles if p.id != profile.id] + [profile]
        self.write(ws, profiles)
        return profile

    def delete_profile(self, ws: str, profile_id: str) -> bool:
        profiles = self.read(ws)
        rest = [p for p in profiles if p.id != profile_id]
        if len(rest) == len(profiles):
            return False
        self.write(ws, rest)
        return True

    def apply_update(self, ws: str, profile_id: str, cred_id: str, **fields) -> None:
        """更新某 credential 的非敏感字段;空串 secret = 不改(保留原密文)。"""
        profiles = self.read(ws)
        for p in profiles:
            if p.id != profile_id:
                continue
            for c in p.credentials:
                if c.id != cred_id:
                    continue
                for k, v in fields.items():
                    if k in _CRED_SECRET_FIELDS:
                        if v:  # 非空 → 更新
                            setattr(c, k, v)
                        # 空串/None → 保留原值(不改)
                    elif hasattr(c, k):
                        setattr(c, k, v)
        self.write(ws, profiles)

    def set_verify_status(self, ws: str, profile_id: str, cred_id: str, status: VerifyStatus) -> None:
        profiles = self.read(ws)
        for p in profiles:
            if p.id == profile_id:
                for c in p.credentials:
                    if c.id == cred_id:
                        c.verify_status = status
        self.write(ws, profiles)
```

- [ ] **Step 4: 跑测试**

```bash
cd packages/web && uv run pytest tests/test_auth_profile_store.py -v
```
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/auth_profile_store.py \
        packages/web/tests/test_auth_profile_store.py
git commit -m "feat(web): AuthProfileStore 多角色加密档案库 + 脱敏/空串保留"
```

---

## Task 4: scan_manager 探针生命周期(start_auth_validation + 取结果 + 清理)

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`(构造增 `auth_profile_store` + 新方法)
- Test: `packages/web/tests/test_auth_validation_lifecycle.py`(新)

**Interfaces:**
- Consumes: Task 3 `AuthProfileStore`/`credential_to_authentication`;Task 2 `AuthValidationWorkflow`/`BlackboxAuthValidationInput`;`Client.connect`/`start_workflow`/`get_workflow_handle` 范式(镜像 `_submit_blackbox`);`WEB_TASK_QUEUE_BLACKBOX`;core `AuthValidationResult`。
- Produces: `ScanManager.start_auth_validation(ws, profile_id, cred_id) -> str`(workflow_id);`ScanManager.get_auth_validation_result(ws, workflow_id, profile_id, cred_id) -> VerifyStatus`(取 result + 回填 + 删 probe 目录)。

- [ ] **Step 1: 写失败测试 `test_auth_validation_lifecycle.py`**

```python
"""scan_manager 探针生命周期:写 probe YAML + 起 workflow + 取 result 回填 + 删 probe 目录。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from supernova_web.components.auth_profile_store import (
    AuthProfileStore, AuthProfile, AuthProfileCredential, VerifyStatus,
)
from supernova_web.components.credential_vault import CredentialVault
from supernova_web.components.scan_manager import ScanManager
from supernova_core.services.validate_authentication import AuthValidationResult


def _store(tmp_path):
    vault = CredentialVault(tmp_path / ".master.key")
    s = AuthProfileStore(tmp_path, vault)
    s.write("ws1", [AuthProfile(
        id="prof_1", name="NG", login_url="http://t/", login_type="form",
        credentials=[AuthProfileCredential(id="cred_a", role="admin", username="admin", password="pw")])])
    return s


def _mgr(tmp_path, store):
    # 最小构造:scan_manager 只用到 _workspaces_dir / auth_profile_store / _temporal_address
    return ScanManager(
        workspaces_dir=tmp_path, repos_dir=tmp_path / "repos", config_store=MagicMock(),
        max_concurrent=1, scan_timeout=0.0, ws_config_store=MagicMock(),
        auth_profile_store=store,
    )


@pytest.mark.asyncio
async def test_start_auth_validation_writes_probe_yaml_and_starts_workflow(tmp_path):
    store = _store(tmp_path)
    mgr = _mgr(tmp_path, store)
    fake_handle = MagicMock()
    fake_handle.id = "wf-123"
    with patch("supernova_web.components.scan_manager.Client") as ClientCls, \
         patch("supernova_web.components.scan_manager.validate_authentication", create=True):
        ClientCls.connect = AsyncMock(return_value=MagicMock(
            start_workflow=AsyncMock(return_value=fake_handle)))
        wf_id = await mgr.start_auth_validation("ws1", "prof_1", "cred_a")
    assert wf_id == "wf-123"
    # probe 目录 + scan-config.yaml 被写(含 authentication 段,明文)
    probe_yamls = list((tmp_path / "ws1" / "auth-probes").glob("*/scan-config.yaml"))
    assert probe_yamls, "probe scan-config.yaml 应被写"
    body = probe_yamls[0].read_text("utf-8")
    assert "authentication" in body and "admin" in body and "pw" in body


@pytest.mark.asyncio
async def test_get_result_backfills_verify_status_and_deletes_probe(tmp_path):
    store = _store(tmp_path)
    mgr = _mgr(tmp_path, store)
    # 预置一个 probe 目录(模拟 start 已写)
    probe_dir = tmp_path / "ws1" / "auth-probes" / "probe-1"
    probe_dir.mkdir(parents=True)
    (probe_dir / "scan-config.yaml").write_text("authentication: {}", "utf-8")
    with patch("supernova_web.components.scan_manager.Client") as ClientCls:
        ClientCls.connect = AsyncMock(return_value=MagicMock(
            get_workflow_handle=MagicMock(return_value=MagicMock(
                result=AsyncMock(return_value=AuthValidationResult(
                    success=False, failure_point="username_or_password", failure_detail="bad pw"))))))
        status = await mgr.get_auth_validation_result(
            "ws1", workflow_id="wf-1", probe_dir=str(probe_dir),
            profile_id="prof_1", cred_id="cred_a",
        )
    assert status.state == "failed"
    assert status.failure_point == "username_or_password"
    # 回填进 store
    cred = store.read("ws1")[0].credentials[0]
    assert cred.verify_status.state == "failed"
    # probe 目录被删
    assert not probe_dir.exists()
```

- [ ] **Step 2: 跑测试验证失败**

```bash
cd packages/web && uv run pytest tests/test_auth_validation_lifecycle.py -v
```
Expected: FAIL(`start_auth_validation` 不存在 / 构造无 `auth_profile_store` 形参)。

- [ ] **Step 3: 改 `ScanManager` 构造 + 新增两个方法**

`scan_manager.py` 构造(`def __init__` 约 `:68-86`)末尾增形参 `auth_profile_store: Any = None` 并存 `self._auth_profile_store = auth_profile_store`。

文件头补 import:

```python
from supernova_core.services.validate_authentication import AuthValidationResult
from supernova_blackbox.pipeline.shared import BlackboxAuthValidationInput
from supernova_blackbox.pipeline.workflows import AuthValidationWorkflow
from supernova_core.services.temporal_infra import WEB_TASK_QUEUE_BLACKBOX
from .auth_profile_store import credential_to_authentication, VerifyStatus
import yaml, shutil
from uuid import uuid4
```
> `Client`/`Path`/现有 `_temporal_address` 已在 scan_manager。若上述 blackbox import 与现有重复,合并不重复引入。

在类内新增方法(放 `_submit_blackbox` 附近):

```python
    async def start_auth_validation(self, ws: str, profile_id: str, cred_id: str) -> str:
        """认证管理页"测试登录":写 probe scan-config.yaml + 起 AuthValidationWorkflow。"""
        profile = self._auth_profile_store.get(ws, profile_id)
        if profile is None:
            raise ValueError(f"认证档案不存在: {profile_id}")
        cred = next((c for c in profile.credentials if c.id == cred_id), None)
        if cred is None:
            raise ValueError(f"角色凭据不存在: {cred_id}")
        probe_id = f"probe-{uuid4().hex[:8]}"
        probe_dir = self._workspaces_dir / ws / "auth-probes" / probe_id
        probe_dir.mkdir(parents=True, exist_ok=True)
        auth = credential_to_authentication(profile, cred)
        cfg_file = probe_dir / "scan-config.yaml"
        cfg_file.write_text(
            yaml.safe_dump({"authentication": auth.model_dump(exclude_none=True, mode="json")},
                           allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        client = await Client.connect(self._temporal_address())
        inp = BlackboxAuthValidationInput(
            web_url=profile.login_url,
            config_path=str(cfg_file),
            workspace_path=str(probe_dir),
            api_key=self._resolve_provider_config(ws).get("api_key"),
        )
        handle = await client.start_workflow(
            AuthValidationWorkflow.run, inp,
            id=f"authval-{ws}-{probe_id}", task_queue=WEB_TASK_QUEUE_BLACKBOX,
        )
        return handle.id

    async def get_auth_validation_result(
        self, ws: str, workflow_id: str, probe_dir: str,
        profile_id: str, cred_id: str,
    ) -> VerifyStatus:
        """取 workflow result → 回填 verify_status → 删 probe 目录(含明文 YAML)。"""
        client = await Client.connect(self._temporal_address())
        raw = await client.get_workflow_handle(workflow_id).result()
        # AuthValidationResult 经 Temporal 序列化为 dict
        success = raw.get("success") if isinstance(raw, dict) else getattr(raw, "success", False)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        if success:
            status = VerifyStatus(state="success", last_verified_at=now)
        else:
            status = VerifyStatus(
                state="failed",
                failure_point=(raw.get("failure_point") if isinstance(raw, dict) else getattr(raw, "failure_point", None)) or "out_of_band",
                failure_detail=(raw.get("failure_detail") if isinstance(raw, dict) else getattr(raw, "failure_detail", None)),
                last_verified_at=now,
            )
        self._auth_profile_store.set_verify_status(ws, profile_id, cred_id, status)
        shutil.rmtree(probe_dir, ignore_errors=True)  # 删明文 probe 目录
        return status
```

- [ ] **Step 4: 跑测试**

```bash
cd packages/web && uv run pytest tests/test_auth_validation_lifecycle.py tests/test_auth_profile_store.py -v
```
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py \
        packages/web/tests/test_auth_validation_lifecycle.py
git commit -m "feat(web): scan_manager 认证探针生命周期(起 workflow + 取 result 回填 + 清理)"
```

---

## Task 5: auth_profiles CRUD API + app.state 挂载

**Files:**
- Create: `packages/web/src/supernova_web/api/auth_profiles.py`
- Modify: `packages/web/src/supernova_web/app.py`(构造 AuthProfileStore + 挂 router + ScanManager 传 store)
- Test: `packages/web/tests/test_api_auth_profiles.py`(新)

**Interfaces:**
- Consumes: Task 3 `AuthProfileStore`;`workspace_member`/`workspace_manager`(`auth/dependencies.py`);router 挂载范式(`app.include_router(mod.router, dependencies=_require_auth)`)。
- Produces: `router`(prefix `/api/workspaces`),路由:`GET /{ws}/auth-profiles`、`POST /{ws}/auth-profiles`、`GET /{ws}/auth-profiles/{pid}`、`PUT /{ws}/auth-profiles/{pid}`、`DELETE /{ws}/auth-profiles/{pid}`(test/verify-status 在 Task 6 加)。

- [ ] **Step 1: 写失败测试 `test_api_auth_profiles.py`**

```python
"""auth_profiles CRUD API + 权限(workspace_member 看 / workspace_manager 改删)。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from supernova_web.api import auth_profiles
from supernova_web.auth.dependencies import current_user, workspace_member, workspace_manager
from supernova_web.components.auth_profile_store import AuthProfileStore
from supernova_web.components.credential_vault import CredentialVault


class _U:
    role = "admin"
    id = 1


def _client(tmp_path):
    store = AuthProfileStore(tmp_path, CredentialVault(tmp_path / ".mk"))
    app = FastAPI()
    app.state.auth_profile_store = store
    app.state.scan_manager = MagicMock()
    app.include_router(auth_profiles.router)
    # 测试绕过鉴权(admin 短路,但 dependency_overrides 更稳)
    app.dependency_overrides[current_user] = lambda: _U()
    app.dependency_overrides[workspace_member] = lambda: _U()
    app.dependency_overrides[workspace_manager] = lambda: _U()
    return TestClient(app), store


def test_create_list_get_delete(tmp_path):
    c, _store = _client(tmp_path)
    body = {"name": "NG", "login_url": "http://t/", "login_type": "form",
            "credentials": [{"role": "admin", "username": "admin", "password": "pw"}]}
    r = c.post("/api/workspaces/ws1/auth-profiles", json=body)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    # list 脱敏
    lst = c.get("/api/workspaces/ws1/auth-profiles").json()
    assert lst[0]["name"] == "NG"
    assert lst[0]["credentials"][0]["password"] == "••••"
    # get 脱敏
    assert c.get(f"/api/workspaces/ws1/auth-profiles/{pid}").status_code == 200
    # delete
    assert c.delete(f"/api/workspaces/ws1/auth-profiles/{pid}").status_code == 200
    assert c.get("/api/workspaces/ws1/auth-profiles").json() == []


def test_put_empty_secret_keeps_existing(tmp_path):
    c, store = _client(tmp_path)
    pid = c.post("/api/workspaces/ws1/auth-profiles", json={
        "name": "NG", "login_url": "http://t/", "login_type": "form",
        "credentials": [{"role": "admin", "username": "admin", "password": "pw"}]}).json()["id"]
    cred_id = c.get(f"/api/workspaces/ws1/auth-profiles/{pid}").json()["credentials"][0]["id"]
    # PUT 空串 password = 不改
    c.put(f"/api/workspaces/ws1/auth-profiles/{pid}", json={
        "name": "NG2", "login_url": "http://t/", "login_type": "form",
        "credentials": [{"id": cred_id, "role": "admin", "username": "admin", "password": ""}]})
    cred = store.read("ws1")[0].credentials[0]
    assert cred.password == "pw"  # 保留
```

- [ ] **Step 2: 跑测试验证失败**

```bash
cd packages/web && uv run pytest tests/test_api_auth_profiles.py -v
```
Expected: FAIL(`auth_profiles` 模块不存在)。

- [ ] **Step 3: 实现 `api/auth_profiles.py`**

```python
"""认证档案 CRUD API(workspace 级,脱敏读写,空串 secret = 不改)。

test/verify-status 在本文件追加(Task 6)。鉴权:看/用 workspace_member,改/删 workspace_manager。
"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request

from supernova_web.auth.dependencies import workspace_member, workspace_manager
from supernova_web.components.auth_profile_store import (
    AuthProfileStore, AuthProfile, AuthProfileCredential, EmailLoginCred,
)

router = APIRouter(prefix="/api/workspaces", tags=["auth-profiles"])


def _store(request: Request) -> AuthProfileStore:
    return request.app.state.auth_profile_store


@router.get("/{ws}/auth-profiles")
async def list_profiles(ws: str, user=Depends(workspace_member)):
    return [p.model_dump(mode="json") for p in _store(user and request_ws(ws)).read_masked(ws)] \
        if False else [p.model_dump(mode="json") for p in _store_get(user).read_masked(ws)]


# 注:上面占位为示意,实际如下(简洁版):
```

> 上面的 `list_profiles` 占位有误,实际实现用下方干净版本——把整个文件按下方写:

```python
"""认证档案 CRUD API(workspace 级,脱敏读写,空串 secret = 不改)。

test/verify-status 端点在本文件追加(Task 6)。鉴权:看/用 workspace_member,改/删 workspace_manager。
范式镜像 api/ws_config.py。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from supernova_web.auth.dependencies import workspace_member, workspace_manager
from supernova_web.components.auth_profile_store import (
    AuthProfileStore, AuthProfile, AuthProfileCredential, EmailLoginCred,
    _CRED_SECRET_FIELDS,
)

router = APIRouter(prefix="/api/workspaces", tags=["auth-profiles"])


def _store(request: Request) -> AuthProfileStore:
    return request.app.state.auth_profile_store


def _build_profile(payload: dict) -> AuthProfile:
    creds = [AuthProfileCredential(**c) for c in payload.get("credentials", [])]
    return AuthProfile(
        id=payload.get("id", ""),
        name=payload["name"],
        login_url=payload["login_url"],
        login_type=payload["login_type"],
        login_flow=payload.get("login_flow"),
        credentials=creds,
    )


@router.get("/{ws}/auth-profiles")
async def list_profiles(ws: str, request: Request, user=Depends(workspace_member)):
    return [p.model_dump(mode="json") for p in _store(request).read_masked(ws)]


@router.post("/{ws}/auth-profiles")
async def create_profile(ws: str, payload: dict, request: Request,
                         user=Depends(workspace_manager)):
    store = _store(request)
    # 唯一性:ws 内 name 唯一
    if any(p.name == payload.get("name") for p in store.read(ws)):
        raise HTTPException(422, f"档案名已存在: {payload.get('name')}")
    profile = store.upsert_profile(ws, _build_profile(payload))
    return store.get(ws, profile.id).model_dump(mode="json")  # 返脱敏? read_masked 单条:


@router.get("/{ws}/auth-profiles/{pid}")
async def get_profile(ws: str, pid: str, request: Request, user=Depends(workspace_member)):
    p = _store(request).get(ws, pid)
    if p is None:
        raise HTTPException(404, "认证档案不存在")
    # 脱敏:取 read_masked 中匹配
    masked = next((m for m in _store(request).read_masked(ws) if m.id == pid), None)
    return masked.model_dump(mode="json")


@router.put("/{ws}/auth-profiles/{pid}")
async def update_profile(ws: str, pid: str, payload: dict, request: Request,
                         user=Depends(workspace_manager)):
    store = _store(request)
    existing = store.get(ws, pid)
    if existing is None:
        raise HTTPException(404, "认证档案不存在")
    # profile 级字段覆盖
    existing.name = payload.get("name", existing.name)
    existing.login_url = payload.get("login_url", existing.login_url)
    existing.login_type = payload.get("login_type", existing.login_type)
    existing.login_flow = payload.get("login_flow", existing.login_flow)
    # credentials:逐条 upsert,空串 secret = 保留原值
    existing_by_id = {c.id: c for c in existing.credentials}
    for c_in in payload.get("credentials", []):
        cid = c_in.get("id")
        if cid and cid in existing_by_id:  # 更新现有
            c = existing_by_id[cid]
            c.role = c_in.get("role", c.role)
            c.username = c_in.get("username", c.username)
            for f in _CRED_SECRET_FIELDS:
                v = c_in.get(f, "")
                if v:
                    setattr(c, f, v)   # 非空 → 更新;空串 → 保留
            if c_in.get("email_login"):
                el = c.email_login or EmailLoginCred(address="", password=None)
                el.address = c_in["email_login"].get("address", el.address)
                for f in _CRED_SECRET_FIELDS:
                    v = c_in["email_login"].get(f, "")
                    if v:
                        setattr(el, f, v)
                c.email_login = el
        else:  # 新增 credential
            existing.credentials.append(AuthProfileCredential(**{k: v for k, v in c_in.items() if k != "id"}))
    store.upsert_profile(ws, existing)
    return {"ok": True}


@router.delete("/{ws}/auth-profiles/{pid}")
async def delete_profile(ws: str, pid: str, request: Request,
                         user=Depends(workspace_manager)):
    if not _store(request).delete_profile(ws, pid):
        raise HTTPException(404, "认证档案不存在")
    return {"ok": True}
```

> `create_profile` 的 return 行有占位笔误,实现时改为:`return next(m for m in store.read_masked(ws) if m.id == profile.id).model_dump(mode="json")`(返脱敏单条)。

- [ ] **Step 4: `app.py` 挂 store + router + 传 ScanManager**

`app.py` 在构造 `ws_config_store` 处(约 `:435-436`)后加:

```python
    from .components.auth_profile_store import AuthProfileStore
    app.state.auth_profile_store = AuthProfileStore(
        cfg.workspaces_dir, app.state.credential_vault)
```

`ScanManager(...)` 构造(约 `:445-448`)增 `auth_profile_store=app.state.auth_profile_store`。

API import 行(约 `:431`)加 `auth_profiles`,挂载区(约 `:452-466`)加:

```python
    app.include_router(auth_profiles.router, dependencies=_require_auth)
```

- [ ] **Step 5: 跑测试**

```bash
cd packages/web && uv run pytest tests/test_api_auth_profiles.py -v
```
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add packages/web/src/supernova_web/api/auth_profiles.py \
        packages/web/src/supernova_web/app.py \
        packages/web/tests/test_api_auth_profiles.py
git commit -m "feat(web): auth-profiles CRUD API + AuthProfileStore 挂 app.state"
```

---

## Task 6: auth_profiles test + verify-status 端点

**Files:**
- Modify: `packages/web/src/supernova_web/api/auth_profiles.py`(追加两端点)
- Test: `packages/web/tests/test_api_auth_profiles.py`(追加)

**Interfaces:**
- Consumes: Task 4 `ScanManager.start_auth_validation`/`get_auth_validation_result`(经 `request.app.state.scan_manager`)。
- Produces: `POST /{ws}/auth-profiles/{pid}/credentials/{cid}/test` → `{workflow_id}`;`GET /{ws}/auth-profiles/{pid}/credentials/{cid}/verify-status?workflow_id=...&probe_dir=...` → `VerifyStatus`。

- [ ] **Step 1: 追加失败测试**

```python
@pytest.mark.asyncio
async def test_test_endpoint_starts_workflow(tmp_path, monkeypatch):
    c, store = _client(tmp_path)
    # 预置档案
    pid = c.post("/api/workspaces/ws1/auth-profiles", json={
        "name": "NG", "login_url": "http://t/", "login_type": "form",
        "credentials": [{"role": "admin", "username": "admin", "password": "pw"}]}).json()["id"]
    cred_id = store.read("ws1")[0].credentials[0].id
    sm = c.app.state.scan_manager
    sm.start_auth_validation = AsyncMock(return_value="wf-xyz")
    r = c.post(f"/api/workspaces/ws1/auth-profiles/{pid}/credentials/{cred_id}/test")
    assert r.status_code == 200
    assert r.json()["workflow_id"] == "wf-xyz"
    sm.start_auth_validation.assert_awaited_once_with("ws1", pid, cred_id)
```
> 顶部测试文件需 `from unittest.mock import AsyncMock`(若未 import 则补)。

- [ ] **Step 2: 跑测试验证失败**

```bash
cd packages/web && uv run pytest tests/test_api_auth_profiles.py::test_test_endpoint_starts_workflow -v
```
Expected: FAIL(404 路由不存在)。

- [ ] **Step 3: 追加端点到 `auth_profiles.py`**

```python
@router.post("/{ws}/auth-profiles/{pid}/credentials/{cid}/test")
async def test_credential(ws: str, pid: str, cid: str, request: Request,
                          user=Depends(workspace_member)):
    """触发真实登录验证 → 起 AuthValidationWorkflow,返 workflow_id(前端轮询)。"""
    workflow_id = await request.app.state.scan_manager.start_auth_validation(ws, pid, cid)
    return {"workflow_id": workflow_id}
```

> `verify-status` 端点需 `workflow_id` + `probe_dir`(probe_dir 由前端从 test 响应或固定推导)。为简化前端轮询契约,把 `probe_dir` 也由 scan_manager 跟踪:`start_auth_validation` 返回 `{workflow_id, probe_dir}`(改 Task 4 返回 dict)。**采纳此调整**:把 Task 4 的 `start_auth_validation` 返回值改为 `{"workflow_id": ..., "probe_dir": ...}`(同步改 Task 4 测试断言 `wf_id["workflow_id"]`)。然后追加:

```python
@router.get("/{ws}/auth-profiles/{pid}/credentials/{cid}/verify-status")
async def verify_status(ws: str, pid: str, cid: str, workflow_id: str,
                        probe_dir: str, request: Request,
                        user=Depends(workspace_member)):
    """轮询验证结果 → 回填 verify_status → 删 probe 目录。Temporal 未就绪抛错前端提示重试。"""
    try:
        status = await request.app.state.scan_manager.get_auth_validation_result(
            ws, workflow_id, probe_dir, pid, cid)
    except Exception as e:
        raise HTTPException(503, f"验证结果暂时不可用,请重试: {e}")
    return status.model_dump(mode="json")
```

- [ ] **Step 4: 同步改 Task 4 返回 dict(回填)**

`scan_manager.start_auth_validation` 末尾改:

```python
        return {"workflow_id": handle.id, "probe_dir": str(probe_dir)}
```

Task 4 测试 `test_start_auth_validation_*` 的断言改:`assert wf_id["workflow_id"] == "wf-123"`。

- [ ] **Step 5: 跑全部 auth_profiles + lifecycle 测试**

```bash
cd packages/web && uv run pytest tests/test_api_auth_profiles.py tests/test_auth_validation_lifecycle.py -v
```
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add packages/web/src/supernova_web/api/auth_profiles.py \
        packages/web/src/supernova_web/components/scan_manager.py \
        packages/web/tests/test_api_auth_profiles.py \
        packages/web/tests/test_auth_validation_lifecycle.py
git commit -m "feat(web): auth-profiles test + verify-status 端点(scan_manager 返 workflow+probe_dir)"
```

---

## Task 7: ScanRequest 二选一 validator(auth_profile vs inline authentication)

**Files:**
- Modify: `packages/web/src/supernova_web/models.py`
- Test: `packages/web/tests/test_scan_request_auth_profile.py`(新)

**Interfaces:**
- Consumes: 现有 `ScanRequest._blackbox_requires_reuse`(`models.py:41-57`,validator 范式)。
- Produces: `ScanRequest.auth_profile_id: str | None`、`auth_credential_id: str | None`,与 `authentication` dict **二选一**(新 `model_validator`)。

- [ ] **Step 1: 写失败测试**

```python
"""ScanRequest:auth_profile_id/credential_id 与 authentication 二选一(blackbox)。"""
import pytest
from pydantic import ValidationError
from supernova_web.models import ScanRequest


def _bb(**kw):
    base = {"type": "blackbox", "reuse_whitebox_scan_id": "wb-1"}
    base.update(kw)
    return ScanRequest(**base)


def test_blackbox_inline_auth_ok():
    r = _bb(authentication={"login_type": "form", "login_url": "http://t/",
                            "credentials": {"username": "a"}})
    assert r.authentication is not None


def test_blackbox_profile_auth_ok():
    r = _bb(auth_profile_id="prof_1", auth_credential_id="cred_a")
    assert r.auth_profile_id == "prof_1"


def test_blackbox_both_profile_and_inline_rejected():
    with pytest.raises(ValidationError):
        _bb(auth_profile_id="prof_1", auth_credential_id="cred_a",
            authentication={"login_type": "form", "login_url": "http://t/",
                            "credentials": {"username": "a"}})


def test_blackbox_profile_without_credential_rejected():
    with pytest.raises(ValidationError):
        _bb(auth_profile_id="prof_1")  # 缺 auth_credential_id
```

- [ ] **Step 2: 跑测试验证失败**

```bash
cd packages/web && uv run pytest tests/test_scan_request_auth_profile.py -v
```
Expected: FAIL(字段不存在)。

- [ ] **Step 3: 改 `models.py`**

`ScanRequest` 加两字段(紧挨 `authentication` 字段):

```python
    # 黑盒选已保存档案/角色(与 inline authentication 二选一):scan_manager 展开成单 credentials。
    auth_profile_id: str | None = None
    auth_credential_id: str | None = None
```

追加第二个 validator(与 `_blackbox_requires_reuse` 并列):

```python
    @model_validator(mode="after")
    def _auth_profile_xor_inline(self) -> "ScanRequest":
        """blackbox:auth_profile_id+auth_credential_id 与 authentication 二选一。"""
        if self.type == "blackbox":
            has_profile = self.auth_profile_id or self.auth_credential_id
            has_inline = self.authentication is not None
            if has_profile and has_inline:
                raise ValueError("blackbox 登录:不能同时指定认证档案与内联登录配置")
            if self.auth_profile_id and not self.auth_credential_id:
                raise ValueError("选认证档案时必须同时指定 auth_credential_id")
            if self.auth_credential_id and not self.auth_profile_id:
                raise ValueError("选认证档案时必须同时指定 auth_profile_id")
        return self
```

- [ ] **Step 4: 跑测试**

```bash
cd packages/web && uv run pytest tests/test_scan_request_auth_profile.py -v
```
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/models.py packages/web/tests/test_scan_request_auth_profile.py
git commit -m "feat(web): ScanRequest auth_profile_id/credential_id 与 authentication 二选一 validator"
```

---

## Task 8: scan_manager 选档案展开成 scan-config.yaml

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`(`_resolve_blackbox_inputs`)
- Test: `packages/web/tests/test_scan_manager_profile_expansion.py`(新)

**Interfaces:**
- Consumes: Task 7 `ScanRequest.auth_profile_id`/`auth_credential_id`;Task 3 `AuthProfileStore.get` + `credential_to_authentication`;现有 `_resolve_blackbox_inputs`(`:289-326`,写 `scan-config.yaml`)。
- Produces: `_resolve_blackbox_inputs` 在 `req.auth_profile_id` 时从 store 展开该角色 → `Authentication` → 写 scan-config.yaml(明文,core 合流点约束),返回 config_path。

- [ ] **Step 1: 写失败测试**

```python
"""选档案发起黑盒扫描:scan_manager 展开该角色 → scan-config.yaml(明文)。"""
import pytest
from unittest.mock import MagicMock
from supernova_web.models import ScanRequest
from supernova_web.components.scan_manager import ScanManager
from supernova_web.components.auth_profile_store import (
    AuthProfileStore, AuthProfile, AuthProfileCredential)
from supernova_web.components.credential_vault import CredentialVault


@pytest.mark.asyncio
async def test_resolve_blackbox_expands_selected_profile(tmp_path):
    store = AuthProfileStore(tmp_path, CredentialVault(tmp_path / ".mk"))
    store.write("ws1", [AuthProfile(
        id="prof_1", name="NG", login_url="http://t/", login_type="form",
        login_flow=["成功标志:/dashboard"],
        credentials=[AuthProfileCredential(id="cred_a", role="admin", username="admin", password="pw")])])
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock(), auth_profile_store=store)
    req = ScanRequest(type="blackbox", reuse_whitebox_scan_id="wb-1",
                      auth_profile_id="prof_1", auth_credential_id="cred_a")
    # 模拟 wb scan dir 存在
    wb_dir = tmp_path / "ws1" / "scans" / "wb-1"
    wb_dir.mkdir(parents=True)
    mgr._store.get_scan_dir = MagicMock(return_value=wb_dir)
    scan_dir = tmp_path / "ws1" / "scans" / "bb-1"
    scan_dir.mkdir(parents=True)
    config_path, repo_path = await mgr._resolve_blackbox_inputs(req, "ws1", scan_dir, None)
    body = (scan_dir / "scan-config.yaml").read_text("utf-8")
    assert "admin" in body and "pw" in body and "/dashboard" in body
    assert config_path is not None
```

- [ ] **Step 2: 跑测试验证失败**

```bash
cd packages/web && uv run pytest tests/test_scan_manager_profile_expansion.py -v
```
Expected: FAIL(选档案分支不存在,config_path None)。

- [ ] **Step 3: 改 `_resolve_blackbox_inputs`**

在现有 `if req.authentication:` 块(`:305` 附近)**之前**加选档案分支:

```python
        config_path: str | None = None
        # 选已保存档案:展开该角色 → Authentication(scan-config.yaml 明文,core 合流点约束)
        if req.auth_profile_id and req.auth_credential_id:
            profile = self._auth_profile_store.get(ws, req.auth_profile_id)
            if profile is None:
                raise ValueError(f"认证档案不存在: {req.auth_profile_id}")
            cred = next((c for c in profile.credentials if c.id == req.auth_credential_id), None)
            if cred is None:
                raise ValueError(f"角色凭据不存在: {req.auth_credential_id}")
            from .auth_profile_store import credential_to_authentication
            auth = credential_to_authentication(profile, cred)
            payload = {"authentication": auth.model_dump(exclude_none=True, mode="json")}
            cfg_file = scan_dir / "scan-config.yaml"
            cfg_file.write_text(
                yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
            config_path = str(cfg_file)
        elif req.authentication:
            # ...现有 inline 分支不动
```

> 把现有 `if req.authentication:` 改为 `elif req.authentication:`(互斥,validator 已保证二选一)。

- [ ] **Step 4: 跑测试**

```bash
cd packages/web && uv run pytest tests/test_scan_manager_profile_expansion.py tests/test_scan_request_auth_profile.py -v
```
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py \
        packages/web/tests/test_scan_manager_profile_expansion.py
git commit -m "feat(web): 黑盒选档案展开成 scan-config.yaml(core 扫描流程不改)"
```

---

## Task 9: stale probe reaper + app.py lifespan 钩子

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`(加 `reap_stale_probes`)
- Modify: `packages/web/src/supernova_web/app.py`(lifespan 加钩子)
- Test: `packages/web/tests/test_stale_probe_reaper.py`(新)

**Interfaces:**
- Consumes: `_reconcile_orphaned_scans` 范式(`app.py:68-95`,遍历 `cfg.workspaces_dir.iterdir()`);probe 目录约定 `workspaces/<ws>/auth-probes/*/`。
- Produces: `ScanManager.reap_stale_probes()`(启动期清所有 ws 的 auth-probes 孤儿目录);`app.py` lifespan 调用。

- [ ] **Step 1: 写失败测试**

```python
"""stale probe reaper:启动期清 workspaces/<ws>/auth-probes/*/ 残留目录(worker 异常残留)。"""
from pathlib import Path
from unittest.mock import MagicMock
from supernova_web.components.scan_manager import ScanManager


def test_reap_stale_probes_removes_orphan_probe_dirs(tmp_path):
    ws = tmp_path / "ws1"
    probe = ws / "auth-probes" / "probe-stale"
    probe.mkdir(parents=True)
    (probe / "scan-config.yaml").write_text("authentication: {}", "utf-8")
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock())
    mgr.reap_stale_probes()
    assert not probe.exists()
    assert (ws / "auth-probes").exists() is False  # 空目录一并清


def test_reap_stale_probes_no_dir_is_noop(tmp_path):
    mgr = ScanManager(tmp_path, tmp_path / "repos", MagicMock())
    mgr.reap_stale_probes()  # 无 auth-probes,不报错
```

- [ ] **Step 2: 跑测试验证失败**

```bash
cd packages/web && uv run pytest tests/test_stale_probe_reaper.py -v
```
Expected: FAIL(`reap_stale_probes` 不存在)。

- [ ] **Step 3: 实现 reaper**

`scan_manager.py` 加方法:

```python
    def reap_stale_probes(self) -> int:
        """启动期清所有 ws 的 auth-probes/*/ 残留(worker 异常残留的明文 probe 目录)。

        验证是即时操作(test → 取 result → 删),无长期运行态;启动时残留 = 上次 worker
        崩溃。整目录删(含明文 scan-config.yaml)。返清理数量。
        """
        import shutil
        n = 0
        if not self._workspaces_dir.is_dir():
            return 0
        for ws_dir in self._workspaces_dir.iterdir():
            probes = ws_dir / "auth-probes"
            if probes.is_dir():
                for probe in probes.iterdir():
                    if probe.is_dir():
                        shutil.rmtree(probe, ignore_errors=True)
                        n += 1
                try:
                    probes.rmdir()  # 空了删父目录
                except OSError:
                    pass
        return n
```

- [ ] **Step 4: app.py lifespan 加钩子**

`app.py` lifespan(`:58-62` 迁移序列后,`await _reconcile_orphaned_scans(app)` 之后)加:

```python
    # 清上次 worker 异常残留的认证 probe 目录(含明文凭据 YAML)
    app.state.scan_manager.reap_stale_probes()
```

- [ ] **Step 5: 跑测试**

```bash
cd packages/web && uv run pytest tests/test_stale_probe_reaper.py -v
```
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py \
        packages/web/src/supernova_web/app.py \
        packages/web/tests/test_stale_probe_reaper.py
git commit -m "feat(web): stale probe reaper + lifespan 启动钩子(清残留明文 probe 目录)"
```

---

## Task 10: 前端契约(types + API client + i18n keys)

**Files:**
- Modify: `packages/web/frontend/src/api/types.ts`(加 `AuthProfile`/`AuthProfileCredential`/`VerifyStatus` + `ScanRequest` 字段)
- Create: `packages/web/frontend/src/api/authProfiles.ts`
- Modify: `packages/web/frontend/src/locales/{zh,en}.json`(顶层 `authProfiles.*`)
- Test: `packages/web/frontend/src/api/types.test.ts`(追加)、`packages/web/frontend/src/locales/locales.test.ts`(parity 已存在,跑即可)

**Interfaces:**
- Consumes: `apiGet/apiPost/apiPut/apiDelete`(`./client`,Pattern B 范式 `members.ts`);`ScanRequest`(`types.ts`)。
- Produces: `AuthProfile`/`AuthProfileCredential`/`VerifyStatus` interface;`listAuthProfiles`/`createAuthProfile`/`getAuthProfile`/`updateAuthProfile`/`deleteAuthProfile`/`testCredential`/`getVerifyStatus` client;`ScanRequest.auth_profile_id`/`auth_credential_id`。

- [ ] **Step 1: 写失败测试 `types.test.ts`(追加)**

```typescript
import { describe, it, expect } from "vitest";
import type { AuthProfile, AuthProfileCredential, ScanRequest } from "./types";

describe("auth profile types", () => {
  it("AuthProfile 形状", () => {
    const p: AuthProfile = {
      id: "prof_1", name: "NG", login_url: "http://t/", login_type: "form",
      login_flow: ["x"], credentials: [
        { id: "cred_a", role: "admin", username: "admin", password: "••••",
          verify_status: { state: "unverified" } }],
    };
    expect(p.credentials[0].verify_status.state).toBe("unverified");
  });
  it("ScanRequest 接受 auth_profile_id", () => {
    const r: ScanRequest = { type: "blackbox", auth_profile_id: "p", auth_credential_id: "c" };
    expect(r.auth_profile_id).toBe("p");
  });
});
```

- [ ] **Step 2: 跑验证失败**

```bash
cd packages/web/frontend && ./node_modules/.bin/vitest run src/api/types.test.ts
```
Expected: FAIL(`AuthProfile` 未导出)。

- [ ] **Step 3: `types.ts` 加类型 + `ScanRequest` 字段**

`ScanAuthentication` 之后追加:

```typescript
export type VerifyState = "unverified" | "success" | "failed";
export interface VerifyStatus {
  state: VerifyState;
  failure_point?: "username_or_password" | "totp_secret" | "out_of_band";
  failure_detail?: string;
  last_verified_at?: string;
}
export interface AuthProfileCredential {
  id: string;
  role: string;
  username: string;
  password?: string;        // GET 返 "••••" if 有值
  totp_secret?: string;
  email_login?: { address: string; password?: string; totp_secret?: string };
  verify_status: VerifyStatus;
}
export interface AuthProfile {
  id: string;
  name: string;
  login_url: string;
  login_type: "form" | "sso" | "api" | "basic";
  login_flow?: string[];
  credentials: AuthProfileCredential[];
  created_at?: string;
  updated_at?: string;
}
```

`ScanRequest` interface 加:`auth_profile_id?: string;` `auth_credential_id?: string;`(与现有 `authentication?` 并列)。

- [ ] **Step 4: 新建 `api/authProfiles.ts`(Pattern B)**

```typescript
import { apiGet, apiPost, apiPut, apiDelete } from "./client";
import type { AuthProfile, VerifyStatus } from "./types";

const enc = encodeURIComponent;

export const listAuthProfiles = (ws: string) =>
  apiGet<AuthProfile[]>(`/workspaces/${enc(ws)}/auth-profiles`);
export const getAuthProfile = (ws: string, pid: string) =>
  apiGet<AuthProfile>(`/workspaces/${enc(ws)}/auth-profiles/${enc(pid)}`);
export const createAuthProfile = (ws: string, body: Partial<AuthProfile>) =>
  apiPost<AuthProfile>(`/workspaces/${enc(ws)}/auth-profiles`, body);
export const updateAuthProfile = (ws: string, pid: string, body: Partial<AuthProfile>) =>
  apiPut<{ ok: true }>(`/workspaces/${enc(ws)}/auth-profiles/${enc(pid)}`, body);
export const deleteAuthProfile = (ws: string, pid: string) =>
  apiDelete<{ ok: true }>(`/workspaces/${enc(ws)}/auth-profiles/${enc(pid)}`);
export const testCredential = (ws: string, pid: string, cid: string) =>
  apiPost<{ workflow_id: string; probe_dir: string }>(
    `/workspaces/${enc(ws)}/auth-profiles/${enc(pid)}/credentials/${enc(cid)}/test`, {});
export const getVerifyStatus = (
  ws: string, pid: string, cid: string, workflowId: string, probeDir: string,
) =>
  apiGet<VerifyStatus>(
    `/workspaces/${enc(ws)}/auth-profiles/${enc(pid)}/credentials/${enc(cid)}/verify-status`
    + `?workflow_id=${enc(workflowId)}&probe_dir=${enc(probeDir)}`);
```

- [ ] **Step 5: i18n `authProfiles.*`(两份同步)**

`zh.json` 顶层(与 `users`/`settings` 同级)加:

```json
  "authProfiles": {
    "title": "认证管理",
    "openLabel": "认证",
    "empty": "暂无认证档案,点击「新建」",
    "create": "新建档案",
    "edit": "编辑",
    "delete": "删除",
    "name": "档案名",
    "loginUrl": "登录地址",
    "loginType": "登录类型",
    "loginFlow": "登录步骤(每行一步)",
    "credentials": "角色凭据",
    "role": "角色",
    "username": "用户名",
    "password": "密码",
    "totpSecret": "TOTP 密钥",
    "addCredential": "添加角色",
    "test": "测试登录",
    "testing": "正在登录…",
    "testHint": "将真实登录一次(约几十秒~2 分钟)",
    "verify": { "unverified": "未验证", "success": "已验证", "failed": "验证失败" },
    "createFailed": "新建失败",
    "deleted": "已删除",
    "selectProfile": "选择认证档案",
    "selectCredential": "选择登录角色",
    "sourceInline": "临时填写",
    "sourceProfile": "使用档案"
  },
```

`en.json` 加同结构英文(键完全一致)。`scan.auth.*` 现有键不动。

- [ ] **Step 6: 跑 typecheck + types 测试 + parity 测试**

```bash
cd packages/web/frontend && ./node_modules/.bin/tsc --noEmit
cd packages/web/frontend && ./node_modules/.bin/vitest run src/api/types.test.ts src/locales/locales.test.ts
```
Expected: tsc 零错;PASS。

- [ ] **Step 7: Commit**

```bash
git add packages/web/frontend/src/api/types.ts \
        packages/web/frontend/src/api/types.test.ts \
        packages/web/frontend/src/api/authProfiles.ts \
        packages/web/frontend/src/locales/zh.json \
        packages/web/frontend/src/locales/en.json
git commit -m "feat(web): AuthProfile 契约(types+client)+ authProfiles i18n"
```

---

## Task 11: AuthProfilesPage CRUD(list + 新建/编辑对话框 + 删除)

**Files:**
- Create: `packages/web/frontend/src/pages/AuthProfilesPage.tsx`
- Create: `packages/web/frontend/src/components/AuthProfileDialog.tsx`(新建/编辑表单)
- Create: `packages/web/frontend/src/pages/AuthProfilesPage.test.tsx`

**Interfaces:**
- Consumes: Task 10 client/types/i18n;`useParams<{workspace:string}>()`;`UsersPage.tsx`/`CreateUserDialog.tsx`/`ConfirmDeleteUserDialog.tsx` 范式;`ReposTab` ws-scoped tab 范式。
- Produces: `AuthProfilesPage`(ws-child tab 内容:档案列表 + 新建/编辑/删除 + 凭据表)。

- [ ] **Step 1: 写失败测试 `AuthProfilesPage.test.tsx`**

```typescript
import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import { AuthProfilesPage } from "./AuthProfilesPage";

const server = setupServer(
  http.get("/api/workspaces/:ws/auth-profiles", () => HttpResponse.json([
    { id: "prof_1", name: "NG", login_url: "http://t/", login_type: "form",
      credentials: [{ id: "cred_a", role: "admin", username: "admin", password: "••••",
        verify_status: { state: "unverified" } }] },
  ])),
  http.post("/api/workspaces/:ws/auth-profiles", async ({ request }) => {
    const b = await request.json();
    return HttpResponse.json({ ...b, id: "prof_new", credentials: [{ id: "cred_new", role: b.credentials?.[0]?.role, username: b.credentials?.[0]?.username, verify_status: { state: "unverified" } }] });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderPage(ws = "ws1") {
  return render(<MemoryRouter initialEntries={[`/p/${ws}/auth-profiles`]}><AuthProfilesPage /></MemoryRouter>);
}

describe("AuthProfilesPage", () => {
  it("渲染档案列表", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("NG")).toBeInTheDocument());
  });
  it("新建档案提交后刷新列表", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("NG")).toBeInTheDocument());
    fireEvent.click(screen.getByText("新建档案"));
    fireEvent.change(screen.getByLabelText("档案名"), { target: { value: "App2" } });
    fireEvent.change(screen.getByLabelText("登录地址"), { target: { value: "http://x/" } });
    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "u" } });
    fireEvent.click(screen.getByText("新建档案", { selector: "button" }));
    await waitFor(() => expect(screen.getByText("App2")).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: 跑验证失败**

```bash
cd packages/web/frontend && ./node_modules/.bin/vitest run src/pages/AuthProfilesPage.test.tsx
```
Expected: FAIL(模块不存在)。

- [ ] **Step 3: 实现 `AuthProfileDialog.tsx`**

范式镜像 `CreateUserDialog.tsx`(open/onOpenChange/onSaved、busy、reset on close、`<form onSubmit>`)。字段:档案名 / login_url / login_type(Select)/ login_flow(Textarea)+ 初始 credential(role/username/password/totp_secret/email_login 折叠)。提交调 `createAuthProfile`/`updateAuthProfile`。

```tsx
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { createAuthProfile, updateAuthProfile } from "@/api/authProfiles";
import { ApiError, apiErrorMessage } from "@/api/client";
import type { AuthProfile } from "@/api/types";

interface Props {
  ws: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSaved: () => void;
  editing?: AuthProfile | null;
}

export function AuthProfileDialog({ ws, open, onOpenChange, onSaved, editing }: Props) {
  const { t } = useTranslation();
  const [name, setName] = useState(editing?.name ?? "");
  const [loginUrl, setLoginUrl] = useState(editing?.login_url ?? "");
  const [loginType, setLoginType] = useState(editing?.login_type ?? "form");
  const [loginFlow, setLoginFlow] = useState((editing?.login_flow ?? []).join("\n"));
  const [role, setRole] = useState(editing?.credentials[0]?.role ?? "admin");
  const [username, setUsername] = useState(editing?.credentials[0]?.username ?? "");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  function reset() { setName(""); setLoginUrl(""); setLoginType("form"); setLoginFlow(""); setRole("admin"); setUsername(""); setPassword(""); }
  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !loginUrl.trim() || !username.trim()) { toast.error(t("authProfiles.createFailed")); return; }
    setBusy(true);
    try {
      const flow = loginFlow.split("\n").map((s) => s.trim()).filter(Boolean);
      const body = { name: name.trim(), login_url: loginUrl.trim(), login_type: loginType,
        login_flow: flow.length ? flow : undefined,
        credentials: [{ role, username: username.trim(), ...(password ? { password } : {}), verify_status: { state: "unverified" as const } }] };
      if (editing) await updateAuthProfile(ws, editing.id, body);
      else await createAuthProfile(ws, body);
      toast.success(t("authProfiles.create"));
      reset(); onSaved(); onOpenChange(false);
    } catch (e) { toast.error(apiErrorMessage(e as ApiError, t("authProfiles.createFailed"))); }
    finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) reset(); onOpenChange(o); }}>
      <DialogContent>
        <DialogHeader><DialogTitle>{editing ? t("authProfiles.edit") : t("authProfiles.create")}</DialogTitle></DialogHeader>
        <form onSubmit={onSubmit} className="space-y-3">
          <div className="space-y-1.5"><Label htmlFor="ap-name">{t("authProfiles.name")}</Label>
            <Input id="ap-name" value={name} onChange={(e) => setName(e.target.value)} required /></div>
          <div className="space-y-1.5"><Label htmlFor="ap-url">{t("authProfiles.loginUrl")}</Label>
            <Input id="ap-url" value={loginUrl} onChange={(e) => setLoginUrl(e.target.value)} required /></div>
          <div className="space-y-1.5"><Label>{t("authProfiles.loginType")}</Label>
            <Select value={loginType} onValueChange={setLoginType}>
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>{(["form", "sso", "api", "basic"] as const).map((v) => (
                <SelectItem key={v} value={v}>{v}</SelectItem>))}</SelectContent>
            </Select></div>
          <div className="space-y-1.5"><Label htmlFor="ap-flow">{t("authProfiles.loginFlow")}</Label>
            <Textarea id="ap-flow" value={loginFlow} onChange={(e) => setLoginFlow(e.target.value)} rows={3} /></div>
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1.5"><Label htmlFor="ap-role">{t("authProfiles.role")}</Label>
              <Input id="ap-role" value={role} onChange={(e) => setRole(e.target.value)} /></div>
            <div className="space-y-1.5"><Label htmlFor="ap-user">{t("authProfiles.username")}</Label>
              <Input id="ap-user" value={username} onChange={(e) => setUsername(e.target.value)} required /></div>
          </div>
          <div className="space-y-1.5"><Label htmlFor="ap-pw">{t("authProfiles.password")}</Label>
            <Input id="ap-pw" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              placeholder={editing ? "••••" : ""} /></div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
            <Button type="submit" disabled={busy}>{busy ? "…" : t("authProfiles.create")}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
```

> `apiErrorMessage` 已存在于 `@/api/client` 或 `@/lib/apiError`(参照 `CreateUserDialog` 实际 import 路径;若在 `@/lib/apiError` 则改 import)。

- [ ] **Step 4: 实现 `AuthProfilesPage.tsx`**

范式镜像 `UsersPage.tsx`(`refresh`/loading/error/Card+Table/dialog 挂载)+ `ReposTab`(useParams)。结构:

```tsx
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Plus, Trash2, Pencil } from "lucide-react";
import { listAuthProfiles, deleteAuthProfile } from "@/api/authProfiles";
import type { AuthProfile } from "@/api/types";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { AuthProfileDialog } from "@/components/AuthProfileDialog";
import { CredentialRow } from "./CredentialRow";  // Task 12

export function AuthProfilesPage() {
  const { t } = useTranslation();
  const { workspace } = useParams<{ workspace: string }>();
  const [profiles, setProfiles] = useState<AuthProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<AuthProfile | null>(null);
  const [delTarget, setDelTarget] = useState<AuthProfile | null>(null);

  async function refresh() {
    if (!workspace) return;
    setLoading(true); setError(null);
    try { setProfiles(await listAuthProfiles(workspace)); }
    catch { setError(t("authProfiles.createFailed")); }
    finally { setLoading(false); }
  }
  useEffect(() => { void refresh(); }, [workspace]);

  async function onDelete() {
    if (!workspace || !delTarget) return;
    try { await deleteAuthProfile(workspace, delTarget.id); toast.success(t("authProfiles.deleted")); setDelTarget(null); void refresh(); }
    catch { toast.error(t("authProfiles.createFailed")); }
  }

  if (!workspace) return null;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-medium">{t("authProfiles.title")}</h3>
        <Button variant="cta" onClick={() => setCreateOpen(true)}><Plus className="size-4" /> {t("authProfiles.create")}</Button>
      </div>
      {loading ? <Skeleton className="h-20 w-full" />
       : error ? <div className="text-sm text-destructive">{error}</div>
       : profiles.length === 0 ? <Card className="p-6 text-sm text-muted-foreground">{t("authProfiles.empty")}</Card>
       : <Card><Table><TableHeader><TableRow>
            <TableHead>{t("authProfiles.name")}</TableHead>
            <TableHead>{t("authProfiles.loginUrl")}</TableHead>
            <TableHead>{t("authProfiles.credentials")}</TableHead>
            <TableHead></TableHead></TableRow></TableHeader>
            <TableBody>{profiles.map((p) => (
              <TableRow key={p.id}>
                <TableCell className="font-mono">{p.name}</TableCell>
                <TableCell className="font-mono text-xs">{p.login_url}</TableCell>
                <TableCell>
                  <div className="space-y-2">
                    {p.credentials.map((c) => <CredentialRow key={c.id} ws={workspace} profile={p} credential={c} onChanged={refresh} />)}
                  </div>
                </TableCell>
                <TableCell className="text-right">
                  <Button variant="ghost" size="icon" onClick={() => setEditTarget(p)}><Pencil className="size-4" /></Button>
                  <Button variant="ghost" size="icon" onClick={() => setDelTarget(p)}><Trash2 className="size-4" /></Button>
                </TableCell>
              </TableRow>))}</TableBody></Table></Card>}

      <AuthProfileDialog ws={workspace} open={createOpen} onOpenChange={setCreateOpen} onSaved={refresh} />
      {editTarget && <AuthProfileDialog ws={workspace} open onOpenChange={(o) => !o && setEditTarget(null)} onSaved={() => { setEditTarget(null); void refresh(); }} editing={editTarget} />}
      <Dialog open={!!delTarget} onOpenChange={(o) => !o && setDelTarget(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t("authProfiles.delete")}</DialogTitle></DialogHeader>
          <p className="text-sm text-destructive">{delTarget?.name}</p>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDelTarget(null)}>{t("common.cancel")}</Button>
            <Button variant="destructive" onClick={onDelete}>{t("authProfiles.delete")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
```

> 测试期望"新建档案"文案:`新建档案` 出现在按钮 + Dialog 提交按钮。测试用 `getByText("新建档案", { selector: "button" })` 精确取提交按钮。CredentialRow 在 Task 12 实现;此处先建一个占位 `CredentialRow`(渲染 role + verify badge 即可),Task 12 再补 test-login。占位:

```tsx
// pages/CredentialRow.tsx(Task 12 会扩展)
export function CredentialRow({ credential }: { ws: string; profile: any; credential: any; onChanged: () => void }) {
  return <div className="text-xs font-mono">{credential.role} · {credential.username}</div>;
}
```

- [ ] **Step 5: 跑测试 + tsc**

```bash
cd packages/web/frontend && ./node_modules/.bin/vitest run src/pages/AuthProfilesPage.test.tsx
cd packages/web/frontend && ./node_modules/.bin/tsc --noEmit
```
Expected: PASS;tsc 零错。

- [ ] **Step 6: Commit**

```bash
git add packages/web/frontend/src/pages/AuthProfilesPage.tsx \
        packages/web/frontend/src/pages/AuthProfilesPage.test.tsx \
        packages/web/frontend/src/pages/CredentialRow.tsx \
        packages/web/frontend/src/components/AuthProfileDialog.tsx
git commit -m "feat(web): AuthProfilesPage 档案 CRUD(列表+新建/编辑对话框+删除)"
```

---

## Task 12: 凭据"测试登录" + verify-status 轮询 + 状态徽章

**Files:**
- Modify: `packages/web/frontend/src/pages/CredentialRow.tsx`(扩展:测试按钮 + 轮询 + 徽章)
- Modify: `packages/web/frontend/src/pages/AuthProfilesPage.test.tsx`(追加)

**Interfaces:**
- Consumes: Task 10 `testCredential`/`getVerifyStatus`;`StatusBadge`/`Badge` color token 范式(green/red/yellow)。
- Produces: `CredentialRow`(每角色一行:role + username + 状态徽章 + "测试登录"按钮,轮询期间 disabled+spinner)。

- [ ] **Step 1: 追加失败测试**

```typescript
it("测试登录触发轮询并显示成功徽章", async () => {
  let testCalls = 0;
  server.use(
    http.post("/api/workspaces/:ws/auth-profiles/:pid/credentials/:cid/test", () => {
      testCalls++; return HttpResponse.json({ workflow_id: "wf-1", probe_dir: "/p" });
    }),
    http.get("/api/workspaces/:ws/auth-profiles/:pid/credentials/:cid/verify-status", () =>
      HttpResponse.json({ state: "success", last_verified_at: "2026-08-05T00:00:00Z" })),
  );
  renderPage();
  await waitFor(() => expect(screen.getByText("admin")).toBeInTheDocument());
  fireEvent.click(screen.getByText("测试登录"));
  await waitFor(() => expect(screen.getByText("已验证")).toBeInTheDocument());
  expect(testCalls).toBe(1);
});
```
> 顶部 import 补 `server`(从模块级 `setupServer` 实例引用——测试文件已声明 `server`,直接用;需 `import` 时确认在作用域)。

- [ ] **Step 2: 跑验证失败**

```bash
cd packages/web/frontend && ./node_modules/.bin/vitest run src/pages/AuthProfilesPage.test.tsx
```
Expected: FAIL(测试按钮/徽章未实现)。

- [ ] **Step 3: 实现 `CredentialRow.tsx`**

```tsx
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { testCredential, getVerifyStatus } from "@/api/authProfiles";
import type { AuthProfile, AuthProfileCredential } from "@/api/types";
import { ApiError, apiErrorMessage } from "@/api/client";

export function CredentialRow({ ws, profile, credential, onChanged }: {
  ws: string; profile: AuthProfile; credential: AuthProfileCredential; onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [testing, setTesting] = useState(false);
  const st = credential.verify_status?.state ?? "unverified";
  const badge = st === "success" ? "border-green/40 text-green"
    : st === "failed" ? "border-red/40 text-red" : "border-yellow/40 text-yellow";
  const icon = st === "success" ? "✓" : st === "failed" ? "✗" : "●";

  async function onTest() {
    setTesting(true);
    try {
      const { workflow_id, probe_dir } = await testCredential(ws, profile.id, credential.id);
      // 轮询(每 3s,最长 ~120s)
      for (let i = 0; i < 40; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        try {
          const s = await getVerifyStatus(ws, profile.id, credential.id, workflow_id, probe_dir);
          toast.success(t("authProfiles.verify.success"));
          onChanged(); setTesting(false); return;
        } catch (e) {
          if (e instanceof ApiError && e.status !== 503) { toast.error(apiErrorMessage(e, t("authProfiles.verify.failed"))); setTesting(false); return; }
          // 503 = 未就绪,继续轮询
        }
      }
      toast.error(t("authProfiles.verify.failed")); setTesting(false);
    } catch (e) { toast.error(apiErrorMessage(e as ApiError, t("authProfiles.verify.failed"))); setTesting(false); }
  }

  return (
    <div className="flex items-center gap-2">
      <Badge variant="outline" className={`gap-1 font-mono ${badge}`}>
        <span aria-hidden>{icon}</span>{t(`authProfiles.verify.${st}`)}
      </Badge>
      <span className="font-mono">{credential.role} · {credential.username}</span>
      {st === "failed" && credential.verify_status?.failure_detail && (
        <span className="text-xs text-red/80">{credential.verify_status.failure_detail}</span>
      )}
      <Button size="sm" variant="outline" onClick={onTest} disabled={testing} title={t("authProfiles.testHint")}>
        {testing ? <><Loader2 className="size-3 animate-spin" /> {t("authProfiles.testing")}</> : t("authProfiles.test")}
      </Button>
    </div>
  );
}
```

> 测试用 `setTimeout(3000)` 轮询会让单测变慢;测试中可用 `vi.useFakeTimers()` 加速,或把轮询间隔参数化。实现保留 3s;测试用 `vi.useFakeTimers()` + `vi.advanceTimersByTimeAsync` 推进。若测试不稳,可把间隔抽常量 `POLL_MS` 并在测试通过 props 注入小值——**采纳**:加可选 prop `pollMs?: number`(默认 3000),测试传 0。

补 `pollMs` prop:函数签名加 `pollMs = 3000`,循环用 `pollMs`。测试 renderPage 传入小 pollMs 需透传——简单起见测试用 `vi.useFakeTimers()`。**最终决定**:实现用 `pollMs = 3000`,测试用 fake timers 推进(避免网络风暴)。

- [ ] **Step 4: 跑测试 + tsc + build**

```bash
cd packages/web/frontend && ./node_modules/.bin/vitest run src/pages/AuthProfilesPage.test.tsx
cd packages/web/frontend && ./node_modules/.bin/tsc --noEmit
cd packages/web/frontend && ./node_modules/.bin/vite build
```
Expected: PASS;tsc 零错;build 成功。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/pages/CredentialRow.tsx \
        packages/web/frontend/src/pages/AuthProfilesPage.test.tsx
git commit -m "feat(web): 凭据测试登录 + verify-status 轮询 + 状态徽章"
```

---

## Task 13: 路由 `/p/:workspace/auth-profiles` + ws header 入口

**Files:**
- Modify: `packages/web/frontend/src/router.tsx`(`/p/:workspace` children 加 child)
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/index.tsx`(header 加"认证"按钮)
- Test: 现有 `WorkspaceDetail/index.test.tsx`(追加断言"认证"入口渲染)或新小测

**Interfaces:**
- Consumes: `router.tsx:76-92` children 范式(`repos`/`settings`);`WorkspaceDetail/index.tsx:103-109` repos 按钮范式;Task 11 `AuthProfilesPage`。

- [ ] **Step 1: 写失败测试(追加到 `WorkspaceDetail/index.test.tsx` 或新建)**

最小验证:渲染 WorkspaceDetail,断言 header 有"认证"链接(aria-label `t("authProfiles.openLabel")`)。

```typescript
it("渲染认证管理入口", async () => {
  // 复用 index.test.tsx 的 render harness(占位 ReposTab/settings)
  renderDetail();
  await waitFor(() => expect(screen.getByLabelText("认证")).toBeInTheDocument());
});
```
> 若 `index.test.tsx` 用占位替换子路由,按其现有 harness 调整选择器。

- [ ] **Step 2: 跑验证失败**

```bash
cd packages/web/frontend && ./node_modules/.bin/vitest run src/routes/WorkspaceDetail/index.test.tsx
```
Expected: FAIL(无"认证"入口)。

- [ ] **Step 3: router.tsx 加 child**

`router.tsx` 顶部 eager import 加:

```tsx
import { AuthProfilesPage } from "./pages/AuthProfilesPage";
```

`/p/:workspace` children(与 `repos`/`settings` 并列,`router.tsx:82` 附近)加:

```tsx
          { path: "auth-profiles", element: <AuthProfilesPage /> },
```

- [ ] **Step 4: WorkspaceDetail header 加入口(`index.tsx:103-109` repos 按钮后)**

```tsx
            {workspace && (
              <Button variant="outline" asChild>
                <Link to="auth-profiles" aria-label={t("authProfiles.openLabel")} title={t("authProfiles.openLabel")}>
                  <KeyRound className="size-4" /> {t("authProfiles.openLabel")}
                </Link>
              </Button>
            )}
```
> `KeyRound` 从 `lucide-react` import(加入顶部 `import { ..., KeyRound } from "lucide-react"`)。

- [ ] **Step 5: 跑测试 + tsc + build**

```bash
cd packages/web/frontend && ./node_modules/.bin/vitest run src/routes/WorkspaceDetail/index.test.tsx
cd packages/web/frontend && ./node_modules/.bin/tsc --noEmit
cd packages/web/frontend && ./node_modules/.bin/vite build
```
Expected: PASS;tsc 零错;build 成功。

- [ ] **Step 6: Commit**

```bash
git add packages/web/frontend/src/router.tsx \
        packages/web/frontend/src/routes/WorkspaceDetail/index.tsx \
        packages/web/frontend/src/routes/WorkspaceDetail/index.test.tsx
git commit -m "feat(web): /p/:workspace/auth-profiles 路由 + ws header 认证管理入口"
```

---

## Task 14: 扫描页 Step4 三态来源切换(选档案 / 临时填 / 关闭)

**Files:**
- Modify: `packages/web/frontend/src/pages/ScanNewPage.tsx`(`AuthFormState` 加 `source` + `profileId`/`credentialId`;`validateAuth`/`buildBody`/`authFromPayload`)
- Modify: `packages/web/frontend/src/components/ScanFormFields.tsx`(`AuthFields` 顶部三态 Select)
- Test: `packages/web/frontend/src/pages/ScanNewPage.test.tsx`(追加)

**Interfaces:**
- Consumes: Task 10 `AuthProfile` client(`listAuthProfiles`)+ `ScanRequest.auth_profile_id`/`auth_credential_id`;现有 `AuthFormState`/`buildAuthPayload`/`validateAuth`/`buildBody`/`authFromPayload`(`ScanNewPage.tsx`)。
- Produces: `AuthFormState.source: "disabled" | "inline" | "profile"` + `profileId`/`credentialId`;`AuthFields` 三态切换 UI;`buildBody` profile 模式发 `auth_profile_id`/`auth_credential_id`。

- [ ] **Step 1: 追加失败测试(`ScanNewPage.test.tsx`)**

```typescript
it("profile 模式发 auth_profile_id + auth_credential_id", async () => {
  server.use(http.get("/api/workspaces/:ws/auth-profiles", () => HttpResponse.json([
    { id: "prof_1", name: "NG", login_url: "http://t/", login_type: "form",
      credentials: [{ id: "cred_a", role: "admin", username: "a", verify_status: { state: "unverified" } }] }])));
  let posted: any;
  server.use(http.post("/api/scan", async ({ request }) => { posted = await request.json(); return HttpResponse.json({ scan_id: "s", workspace: "ws1" }); }));
  // 选 blackbox + reuse + profile 模式,提交
  // ...参照现有 ScanNewPage.test.tsx 选 ws/reuse 的范式,切到 profile,选档案+角色,提交
  await waitFor(() => expect(posted).toBeTruthy());
  expect(posted.auth_profile_id).toBe("prof_1");
  expect(posted.auth_credential_id).toBe("cred_a");
  expect(posted.authentication).toBeUndefined();
});
```
> 完整交互(选 ws/repo/reuse → 切 profile → 选档案/角色 → 提交)参照现有 `ScanNewPage.test.tsx` 的 `selectOption` helper 与 blackbox 流程。

- [ ] **Step 2: 跑验证失败**

```bash
cd packages/web/frontend && ./node_modules/.bin/vitest run src/pages/ScanNewPage.test.tsx
```
Expected: FAIL(profile 模式未实现)。

- [ ] **Step 3: 改 `ScanNewPage.tsx`**

`AuthFormState` 加字段:

```tsx
export interface AuthFormState {
  enabled: boolean;
  source: "inline" | "profile";   // enabled=false 时 source 无意义
  profileId: string;
  credentialId: string;
  // ...现有字段不动(loginType/loginUrl/username/...)
}
```
`DEFAULT_AUTH` 加 `source: "inline", profileId: "", credentialId: ""`。

`validateAuth` 加 profile 模式校验(在 `if (!a.enabled) return null;` 后):

```tsx
  if (a.source === "profile") {
    if (!a.profileId) return t("scan.errors.authLoginUrlEmpty"); // 复用或加 t("authProfiles.selectProfile")
    if (!a.credentialId) return t("authProfiles.selectCredential");
    return null;
  }
```

`buildBody` blackbox 分支改:

```tsx
  if (f.auth.enabled) {
    if (f.auth.source === "profile") {
      body.auth_profile_id = f.auth.profileId || undefined;
      body.auth_credential_id = f.auth.credentialId || undefined;
    } else {
      body.authentication = buildAuthPayload(f.auth);
    }
  }
```

`authFromPayload`(重跑预填):若 payload 带 `auth_profile_id` → `source:"profile"` + ids;否则 inline。

- [ ] **Step 4: 改 `ScanFormFields.tsx` `AuthFields`**

把顶部 `enabled` Switch 升级:`enabled` 控制展开/折叠;展开后顶部加三态来源 Select(`disabled` 用 `enabled=false` 表达,故 Select 仅 `inline`/`profile` 两态,折叠即 disabled)。展开块内:

```tsx
      <div className="space-y-1.5">
        <Label className="text-xs font-medium">{t("scan.auth.sourceLabel")}</Label>
        <Select value={auth.source} onValueChange={(v) => setAuth({ source: v as "inline" | "profile" })}>
          <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="inline">{t("authProfiles.sourceInline")}</SelectItem>
            <SelectItem value="profile">{t("authProfiles.sourceProfile")}</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {auth.source === "profile" ? (
        <ProfilePicker auth={auth} setAuth={setAuth} ws={ws} />  // 两级 Select:档案→角色
      ) : (
        /* 现有 inline 字段块(loginType/loginUrl/credentials/...)不动 */
      )}
```

`ProfilePicker`(同文件内联组件或独立):`useEffect` 调 `listAuthProfiles(ws)` → 档案 Select(选 profileId)→ 角色 Select(该 profile.credentials → credentialId)。需 `ws` prop——`AuthFields` 当前无 ws;从 `ScanFormFields` Props 透传 `workspace` 进 `AuthFields`(ScanFormFields 已有 `workspace` prop)。

> `scan.auth.sourceLabel` i18n 键:在 Task 10 的 `scan.auth.*` 段补 `sourceLabel`(或复用 `authProfiles` 键)。补 `zh.json`/`en.json` 的 `scan.auth.sourceLabel`:「登录来源」/「Source」。

- [ ] **Step 5: 跑测试 + tsc + build**

```bash
cd packages/web/frontend && ./node_modules/.bin/vitest run src/pages/ScanNewPage.test.tsx
cd packages/web/frontend && ./node_modules/.bin/tsc --noEmit
cd packages/web/frontend && ./node_modules/.bin/vite build
```
Expected: PASS;tsc 零错;build 成功。

- [ ] **Step 6: Commit**

```bash
git add packages/web/frontend/src/pages/ScanNewPage.tsx \
        packages/web/frontend/src/pages/ScanNewPage.test.tsx \
        packages/web/frontend/src/components/ScanFormFields.tsx \
        packages/web/frontend/src/locales/zh.json \
        packages/web/frontend/src/locales/en.json
git commit -m "feat(web): 扫描页 Step4 三态来源切换(选档案/临时填/关闭)"
```

---

## Self-Review

**1. Spec coverage(spec 各节 → task):**
- §2.1 目标 1(workspace 级多角色加密档案库)→ Task 3。✓
- §2.1 目标 2(认证管理页 CRUD + 每角色测试登录)→ Task 5/6(后端)+ Task 11/12(前端)。✓
- §2.1 目标 3(扫描页选档案复用,core 不改)→ Task 7/8/14。✓
- §2.1 目标 4(消除凭据库明文)→ Task 3(auth-profiles.yaml Fernet);per-scan 明文债按不变量 3 不消除(Global Constraints 2)。✓
- §6.1 AuthValidationWorkflow → Task 2。✓
- §6.2 run_auth_validation_probe(不抛异常)→ Task 1。✓
- §6.3 双 worker 注册 + 两护栏 + workflow 注册无护栏 → Task 1(activity)+ Task 2 Step 5(🔴 workflow)。✓
- §6.4 web 触发/取结果/清理 → Task 4/6。✓
- §6.5 待确认三项 → Plan-level 决策收口(同文件 / BasePipelineInput / 显式路径)。✓
- §7 后端 API(CRUD + test + verify-status + 鉴权)→ Task 5/6。✓
- §8 前端(路由 / Step4 / i18n / 契约 / 测试)→ Task 10/11/12/13/14。✓
- §10 stale probe reaper → Task 9。✓
- §12 不变量 1-6 → Global Constraints + 各 task 遵守(core 零改 / validate 复用 / YAML 明文 / 双引擎 / 独立 store / D1-D4 不回退)。✓
- §13 风险 R4(双 worker + workflow 注册)→ Task 1/2 显式护栏 + 🔴 标记。✓
- §13 风险 R6(validate 签名/executor 构造)→ Task 1 照搬 `activities.py:180-182`。✓

**2. 占位扫描:** Task 5 的 `auth_profiles.py` 首版含一处示意占位(已在同 task 内用干净版整体替换 + return 行修正说明),执行时以干净版为准;Task 11 `CredentialRow` 占位在 Task 12 扩展。其余无 TBD/TODO。

**3. 类型一致性:**
- `AuthValidationResult` 字段(`success`/`failure_point`/`failure_detail`)跨 Task 1/2/4 一致;`VerifyStatus.failure_point` 枚举(`username_or_password`/`totp_secret`/`out_of_band`)与 core `AUTH_VALIDATION_SCHEMA` 对齐(Task 3/前端 types)。
- `BlackboxAuthValidationInput` 字段跨 Task 2/4 一致(`web_url`/`config_path`/`workspace_path`)。
- `start_auth_validation` 返回 `dict{workflow_id, probe_dir}`(Task 4 Step 3 返回 str → Task 6 Step 4 改 dict,同步 Task 4 测试)。✓ 已显式标注同步点。
- `ScanRequest.auth_profile_id`/`auth_credential_id` 后端(Task 7)↔ 前端 `ScanRequest`(Task 10)↔ scan_manager 展开(Task 8)字段名一致。✓
- 前端 `AuthProfile`/`AuthProfileCredential`/`VerifyStatus`(Task 10)↔ 后端脱敏 payload(`read_masked` Task 3)形态一致(password = `"••••"` if 有值)。✓

**残留风险(执行时注意,非 plan 缺陷):**
- `run_auth_validation_probe` 在 worker 容器真机首次跑才知 `validate_authentication` + agent-browser 链路通(单测 mock 了 validate_authentication)。冒烟须 rebuild worker 镜像(Global Constraints 10),真机点一次"测试登录"。
- Task 6 `verify-status` 的 `probe_dir` 经 query string 传——路径含 `/` 需 `encodeURIComponent`(前端 `getVerifyStatus` 已 enc;后端 FastAPI 自动解 query)。✓
- 前端轮询用 fake timers(Task 12)——若 `AuthProfilesPage.test.tsx` 的 msw server 与 fake timers 冲突,降级为注入小 `pollMs`(已在 Task 12 标注备选)。

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-08-05-auth-profile-vault.md`. Two execution options:**

**1. Subagent-Driven(推荐)** — 每个 task 派发独立 subagent,task 间两阶段 review,快速迭代(本 plan 14 task,适合逐个 gate,尤其 Task 1/2 的双 worker wiring + Task 5/6 的 API 契约)。

**2. Inline Execution** — 本会话内用 executing-plans 批量执行 + checkpoint review。

**Which approach?**
