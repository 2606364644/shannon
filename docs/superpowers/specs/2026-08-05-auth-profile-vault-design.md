# 认证档案库 + 独立验证 + 黑盒扫描复用 设计

- 日期:2026-08-05
- 分支:feat/fork-py
- 状态:待 plan
- 子项目:**1**(认证档案库 + 独立验证入口 + 黑盒扫描选档案复用)。子项目 2(多身份 IDOR 对比扫描)为后续独立 spec,依赖本子项目。

## 1. 背景与动机

### 1.1 用户需求

1. 登录模块独立化:可配置认证信息、**单独**验证登录可行性、用户选择保存验证成功的认证信息。
2. 黑盒扫描可选已保存的认证信息,作为黑盒扫描的登录模块走扫描流程。

### 1.2 现状

- **core 层已有"独立验证登录"的能力,但没有独立入口**:`validate-authentication` agent(prompt 在仓库根 `prompts/validate-authentication.txt`)+ `validate_authentication` 服务(`packages/core/src/supernova_core/services/validate_authentication.py`)能单独驱动 agent-browser 登录、判定成功、产出 `auth-state.json`。但它目前只被嵌在 `BlackboxScanWorkflow` 的 `run_blackbox_auth_validation` activity(定义于 `packages/blackbox/src/supernova_blackbox/pipeline/activities.py:164-227`;`workflows.py:152-164` 是其调用块)里,没有"不跑扫描、只测登录"的入口。
- **认证信息无可复用的持久化**:每次新黑盒扫描都要重填;web 端只把配置落进 per-scan 的 `scan-config.yaml`(`packages/web/src/supernova_web/components/scan_manager.py` 的 `_resolve_blackbox_inputs`,L289–326,落盘块 L305–316),仅供"重跑/resume"预填(`scans.py:90-106` 的 `_read_auth_config`),**不跨扫描共享、无凭据库**。
- **明文落盘安全债**:`scan-config.yaml` 的 `password`/`totp_secret`/`email_login.password`(嵌套)明文;同一仓库的 `WsConfigStore` 已有现成 Fernet 加密器 `CredentialVault`(`packages/web/src/supernova_web/components/credential_vault.py`),但其 `CREDENTIAL_FIELDS` 白名单(L19)是死代码——`WsConfigStore` 的 read/write 从不引用它,而是逐字段硬编码 `api_key`/`gitlab_token`(见 §5.2)。黑盒登录完全没接上。
- **现成可复用地基**:per-workspace `WsConfigStore`(`workspaces/<ws>/config.yaml`)+ `CredentialVault`(Fernet,key 同源)+ `workspace_member`/`workspace_manager` 权限体系(`packages/web/src/supernova_web/auth/dependencies.py:22-35`)。
- **两套独立 auth(确认不混淆)**:web 平台用户登录(`packages/web/src/supernova_web/auth/`,SQLite+bcrypt+cookie)与黑盒目标站点登录(本 spec)是两套,不重叠。

### 1.3 与 D1–D4 重构的关系

同日有 `success_condition` 简化重构(D1 删 `success_condition` 死字段 / D2 判定并入 `login_flow` / D3 去 cookie 兜底纯信 `login_success` 字段 / D4 改 `login-instructions.txt` VERIFICATION 段),已于 2026-08-05 **全部合入主线**(commit `df16fe2d`)。本子项目与 D1–D4 改的文件有重叠(core `Authentication` 模型 / 前端 `AuthFields` / `scan_manager` / i18n),但 D1–D4 已落地,**本子项目在其成果上做纯增量,不重新引入 `success_condition` 或 cookie 兜底**。原始 shannon(`/root/shannon/apps/worker/src/types/config.ts:57-63`)确有必填 `success_condition`,删除属合理偏离。

## 2. 目标与范围

### 2.1 目标(本期,子项目 1)

1. workspace 级、多角色的**认证档案库**(可保存、可复用、加密落盘)。
2. 独立的**"认证管理"页**:CRUD 档案 + 每个角色凭据单独"测试登录"(真实驱动 agent-browser 登录,显示成功/失败/失败点),验证成功的可保存。
3. 黑盒扫描发起页可选**已保存档案/角色**(web 层把该角色凭据展开成单 `credentials` 喂 core,**core 扫描流程不改**);保留临时填写(向后兼容)。
4. **消除凭据库明文存储**(`auth-profiles.yaml` 经 Fernet 加密落盘)。

> **明文边界**:目标 4 只覆盖**持久化存储**。per-scan `scan-config.yaml` 是 core 合流点,core 端 `Authentication.model_validate` 不解密 ⇒ per-scan YAML 必须明文(§12 不变量 3),其明文债在本子项目内不消除,仅靠扫描/probe 结束即删 + 0600 权限缓解。彻底消除需 core 接受密文,违反不变量,超范围。

### 2.2 范围外(YAGNI / 后续)

- **子项目 2:多身份 IDOR 对比扫描**——改 core 扫描流程(多 session + authz agent 重写),独立大工程,后续单独 spec,依赖本子项目的多角色档案。
- **CLI**:不动(`scan-config.yaml` 是合流点,CLI 吃 YAML 天然兼容;认证档案 CRUD 暂只 web)。
- **`auth-state.json` 跨扫描复用**:仍是 per-scan、扫描结束清理(`workflows.py:517` blackbox `finally` 调 `cleanup_auth_state_sync`)。
- 自动探测认证模式、Python 端确定性硬断言。

## 3. 设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 档案归属层级 | **workspace 级** | 复用 WsConfigStore/CredentialVault,与黑盒 per-ws 模型天然对齐 |
| 验证形态 | **独立"认证管理"页** | 贴合"独立功能";扫描页只做"选档案/角色" |
| 身份粒度 | **一档案多角色** | 一个 login_url 下多组凭据(多角色),为子项目 2 铺路 |
| 多角色本期用途 | 归拢 + 逐角色验证 + 选角色单身份登录 | 不动 core 扫描流程;多身份对比留子项目 2 |
| 验证执行架构 | **独立 AuthValidation workflow** | 复用现有 activity + Temporal 可观测;agent-browser 只在 worker;且 `BlackboxScanWorkflow` 强依赖白盒产物(`workflows.py:259-280` 无白盒 queue 抛 `DELIVERABLE_NOT_FOUND` fail-fast),**不能复用 `BlackboxScanWorkflow` 跑 auth 段,必须独立 workflow** |

## 4. 架构总览(数据流)

```
[认证管理页] ──CRUD──▶ [API /auth-profiles] ──▶ AuthProfileStore (auth-profiles.yaml + Fernet)
                         │                               ▲
            test 按钮    │                               │ 回填 verify_status
                         ▼                               │
        [API .../credentials/{cid}/test]                 │
                         │                               │
              scan_manager.start_auth_validation         │
                         │                               │
              写临时 probe scan-config.yaml(仅 auth 段)  │
                         ▼                               │
            AuthValidationWorkflow (新,Temporal)         │
                         │                               │
          run_auth_validation_probe (新 activity,不抛异常)│
                         │                               │
          validate_authentication (core 服务,复用不重写)  │
                         │                               │
          agent-browser 真实登录 → AuthValidationResult   │
                         ▼                               │
          web 取 workflow result ─────────────────────▶──┘
          删除 probe 临时目录

[扫描发起页 Step4] 选档案+角色 / 临时填 ──▶ [API /scan] ──▶ scan_manager
   选档案:scan_manager 把该角色凭据展开成单 credentials ──▶ scan-config.yaml(明文,core 合流点约束)
        ──▶ BlackboxScanWorkflow (不改, 仅 config_path)
```

## 5. 数据模型与存储

### 5.1 AuthProfile schema(per-ws)

存储路径:`workspaces/<ws>/auth-profiles.yaml`(独立 store,不塞进 `config.yaml`,避免污染 WsConfig 的 provider/git 配置语义)。

```yaml
- id: prof_xxx              # 档案 id
  name: NodeGoat            # 档案名(用户可读, ws 内唯一)
  login_url: http://192.168.100.106:4000/
  login_type: form          # form/sso/api/basic
  login_flow:               # 自然语言登录步骤 + 成功标志(无 success_condition)
    - "打开登录页"
    - "填账号密码"
    - "成功标志:URL 含 /dashboard"
  credentials:              # 多角色
    - id: cred_admin
      role: admin
      username: admin
      password: <Fernet 密文>
      # totp_secret / email_login 按需(字段对齐 core Authentication)
      verify_status:
        state: success      # unverified | success | failed
        failure_point:      # username_or_password | totp_secret | out_of_band (仅 failed)
        failure_detail:     # 自然语言失败描述 (仅 failed)
        last_verified_at: "2026-08-05T09:12:33"
    - id: cred_user
      role: user
      username: user1
      password: <Fernet 密文>
      verify_status: {state: unverified}
  created_at / updated_at
```

> `failure_point` 枚举对齐 core `AUTH_VALIDATION_SCHEMA`(`validate_authentication.py:21-32`):`username_or_password | totp_secret | out_of_band`。`out_of_band` 在 core 侧承担多种语义(无 structured output / agent 未保存 auth-state 等),靠 `failure_detail` 自由文本区分。

### 5.2 AuthProfileStore(新)

- 文件:`packages/web/src/supernova_web/components/auth_profile_store.py`(新,平行于 `ws_config_store.py`)。
- 读写 `workspaces/<ws>/auth-profiles.yaml`(list[AuthProfile])。
- **加密**:`CredentialVault` 的 `encrypt/decrypt`(`credential_vault.py:42-54`)是字段级 `str|None → str|None`,不支持嵌套路径。AuthProfile schema 有嵌套 `credentials[].email_login.{password, totp_secret}`,须**新写递归遍历加密**:按 schema 路径遍历 `auth_profiles[].credentials[].{password, totp_secret, email_login.{password, totp_secret}}`,save 时 encrypt、load 时 decrypt;复用 `CredentialVault` 的 Fernet 实例(key 同源,容错降级 None 已具备),不新造加密器。
- **脱敏 / 空串保留**:对齐 `api/ws_config.py` 模式——`MASKED = "••••"`(`ws_config.py:21`),GET 返回 `MASKED if val else None`,PUT 时空串=不改(保留原密文,见 `ws_config.py:81,85` 三元写法)。
- 模型:pydantic dataclass,字段对齐 core `Authentication`(`models/config.py:36-40`)。

## 6. 独立验证 workflow(核心技术)

### 6.1 AuthValidationWorkflow(新)

- 位置:`packages/blackbox/src/supernova_blackbox/pipeline/workflows.py`(与 `BlackboxScanWorkflow` 同文件;待 plan 确认是否独立文件)。
- 输入 `BlackboxAuthValidationInput`(命名对齐 shared.py 的 `Blackbox*` 约定,见 §6.5):`web_url` + `config_path` + `workspace_path`。
- 流程:`log_phase_start_activity(phase="auth-validation")` → `run_auth_validation_probe(input)` → 返回 `AuthValidationResult`。
- **不跑**黑盒 workflow 的其余步骤(无白盒产物依赖、无 exploit)——这是必须新建独立 workflow、不能复用 `BlackboxScanWorkflow` 的根本原因(§3)。

> 🔴 **workflow 注册(无 test 兜底,极易漏)**:新 workflow 类必须注册进 worker 的 `workflows=[...]` 列表。WEB worker `packages/worker/src/supernova_worker/runner.py:84` 现在只有 `workflows=[BlackboxScanWorkflow]`;不加入,`start_workflow(AuthValidationWorkflow.run, ..., task_queue=WEB_TASK_QUEUE_BLACKBOX)` 提交后无 worker 能跑 → 卡死/超时。workflow 注册完整性没有 test 护栏(只有 activity 注册护栏),须显式任务。

### 6.2 run_auth_validation_probe activity(新)

- 位置:`packages/blackbox/src/supernova_blackbox/pipeline/activities.py`。
- 调 core `validate_authentication(...)`(**复用、不重写**)。实现要点:
  - **签名**:`validate_authentication`(`services/validate_authentication.py:97-109`)是**纯 keyword-only**(`*` 在前),必传 `prompt_manager: PromptManager` + `executor: AgentExecutor`。probe activity 须在体内现造这俩,**照搬 `run_blackbox_auth_validation`(`activities.py:180-182`)的构造方式**(含 `PromptManager(prompts_dir)` 路径解析口径——probe 在 worker 容器跑,prompts 目录解析须与现有一致)。完整 kwarg:`web_url`/`config_path`/`workspace_path`/`prompt_manager`/`executor`/`repo_path`/`deliverables_path`/`api_key`/`tool_audit_logger`。
  - **不抛异常**:`run_blackbox_auth_validation` 在 `success=False` 时经 activity catch 块转抛 `ApplicationFailure(non_retryable=True)`(`PentestError(retryable=False, AUTH_LOGIN_FAILED)` 是中间构造)。本 probe 要"不抛",须**在 activity 内 try/except 吞掉**,失败时 `return AuthValidationResult(success=False, ...)`——仿 `run_endpoint_verify` 降级模式(`activities.py:349-356`)。
  - **透传不自判**:`validate_authentication` 已内置"无 structured output → `AuthValidationResult(success=False, ...)`"映射(`:165-171`)。probe 直接 `return result`,不需自判 structured output。
- retry policy:复用 `retry_for("auth-validation")`——该 key 已存在(`models/retry.py:21-27,149-150`,`maximum_attempts=3`),`Category` Literal 已含 `"auth-validation"`(`retry.py:124`)。

### 6.3 worker 注册(两个 worker,两个 test 护栏)

新 activity 必须同时注册进两个 worker:

1. **CLI blackbox worker**:`packages/blackbox/src/supernova_blackbox/worker.py:136-147`(`activities=[...]` 手写 list)。
2. **WEB 常驻 worker**:`packages/worker/src/supernova_worker/runner.py:85-93`(bb_worker,task_queue=`WEB_TASK_QUEUE_BLACKBOX`)——WEB 路径真正消费 `WEB_TASK_QUEUE_BLACKBOX` 的 worker。runner.py 注释记有前科:`run_endpoint_verify` 漏改此文件致 web 黑盒 exploitation 整条 `FAILED`。

两个注册点各有独立 test 护栏(`assert_all_activities_registered`,定义在 `packages/core/src/supernova_core/testing/activity_registration.py:56-78`,pytest 期 AST 校验,既抓 missing 也抓 extra):
- `packages/blackbox/tests/test_worker.py:274-284`(校验 CLI worker)
- `packages/worker/tests/test_runner.py:61-107`(校验 WEB worker)

新 activity 加 `@activity.defn` 后,两个 test 会同时 fail,直到两个 worker 的 list 都补上。

> task queue 常量 `WEB_TASK_QUEUE_BLACKBOX`(`temporal_infra.py:35`,值 `"supernova-bb-web"`)。CLI blackbox worker 用另一条随机 queue(`TASK_QUEUE_PREFIX="supernova-bb"` + `generate_task_queue`),本子项目走 web,用 `WEB_TASK_QUEUE_BLACKBOX`。

### 6.4 web 触发 / 取结果 / 清理

`scan_manager.start_auth_validation(ws, profile_id, cred_id)`:
1. 从 `AuthProfileStore` 读档案、定位 credential。
2. 在 `workspaces/<ws>/auth-probes/<probe_id>/` 写 minimal `scan-config.yaml`(仅 `authentication` 段,含该角色凭据 + 档案 login_url/login_type/login_flow)。明文(core 合流点约束,见 §2.1)。
3. `config_path` 指向该 yaml,`workspace_path` 指向 probe 目录(`auth-state.json` 落点),`web_url = login_url`。
4. `client.start_workflow(AuthValidationWorkflow.run, BlackboxAuthValidationInput(...), id=..., task_queue=WEB_TASK_QUEUE_BLACKBOX)` → 返回 workflow_id。起 workflow 范式照搬 `scan_manager._submit_blackbox`(`scan_manager.py:355-358`,`Client.connect` + `start_workflow`)。

取结果:`GET .../verify-status` → `client.get_workflow_handle(wfid).result()` 拿 `AuthValidationResult` → 回填 `verify_status` → 删除 probe 临时目录(`scan-config.yaml` 含明文密码,验证后立即清理)。

> **auth-state.json 清理**:两段——(a) `validate_authentication` 执行 agent **前**先 `cleanup_auth_state(workspace_path)` 删 stale(`:131`,probe 复用自动得到);(b) `BlackboxScanWorkflow` 收尾在 `workflows.py:517` `finally` 调 `cleanup_auth_state_sync`。新 probe workflow **不需要自己的 finally 清理**——probe 目录由 web 层取 result 后整目录删(§10)。不要在 probe workflow 里照抄 517。

> 改 web/worker src,生效须 rebuild `supernova-worker`(及 web)镜像。

### 6.5 待 plan 确认项

1. 新 workflow 放 `workflows.py` 同文件还是独立文件(倾向同文件,轻量)。
2. `BlackboxAuthValidationInput` 继承:它是 workflow 入参,倾向继承 core `BasePipelineInput`;probe activity 入参复用现有 `BlackboxActivityInput`(`shared.py:41-61`,已有 `web_url`/`config_path`/`workspace_path`/`repo_path`/`api_key` 等字段,够用)。
3. 嵌套字段加密遍历方案(§5.2,倾向按 schema 路径递归)。

## 7. 后端 API

- `GET    /api/workspaces/{ws}/auth-profiles` — 列表(脱敏)
- `POST   /api/workspaces/{ws}/auth-profiles` — 新建
- `GET    /api/workspaces/{ws}/auth-profiles/{pid}` — 详情(脱敏)
- `PUT    /api/workspaces/{ws}/auth-profiles/{pid}` — 更新(空串敏感字段=不改)
- `DELETE /api/workspaces/{ws}/auth-profiles/{pid}` — 删除
- `POST   /api/workspaces/{ws}/auth-profiles/{pid}/credentials/{cid}/test` — 触发验证 → `{workflow_id}`
- `GET    /api/workspaces/{ws}/auth-profiles/{pid}/credentials/{cid}/verify-status` — 轮询结果
- 鉴权:`Depends(workspace_member)`(看/用)+ `Depends(workspace_manager)`(改/删),ws 级隔离(`auth/dependencies.py:22-35`)。
- router 挂载范式照搬 `app.py:454-466`(`app.include_router(mod.router, dependencies=_require_auth)`)。
- `ScanRequest`(`packages/web/src/supernova_web/models.py`,L35 `authentication: dict | None = None`)增可选 `auth_profile_id` + `auth_credential_id`,与 inline `authentication` dict **二选一**(新 model_validator,与现有 `_blackbox_requires_reuse` L41-57 并列);scan_manager 选档案时展开该角色 → `scan-config.yaml` → 现有 `BlackboxScanWorkflow`(core 不改)。

## 8. 前端

- **新路由 `/p/:workspace/auth-profiles`**(认证管理页):档案列表 + 新建/编辑/删除;详情里多角色 credentials 表,每行"测试登录"按钮 + 状态徽章(未验证 / ✓成功 / ✗失败+失败点);实时轮询 verify-status(测试进行中 disabled + spinner)。
  > 路由参数是 `:workspace`。新路由加进 `router.tsx:76-92` 的 `/p/:workspace` children(与 `repos`/`settings` 并列,`useParams<{workspace:string}>()`)。router 全 eager import(无 `React.lazy`),`AuthProfilesPage` 沿用范式。
- **扫描页 Step4 改造**(`ScanFormFields.tsx` / `ScanNewPage.tsx`):加"来源"切换。
  > 插入点:在 `AuthFields`(`ScanFormFields.tsx:71-182`,内联组件)顶部,把现有 `enabled: boolean` Switch 升级为三态 `disabled | inline | profile`。联动改 `AuthFormState`(`ScanNewPage.tsx:20-32`)、`validateAuth`(`:96-102`,profile 模式校验 profileId/credentialId)、`buildBody`(`:127-140`,profile 模式发 `auth_profile_id`+`auth_credential_id`,inline 模式发 `authentication` dict)、`authFromPayload`(`:68-84`,重跑预填 profile 标识)。下拉复用现有 Radix `Select` 范式(`ScanFormFields.tsx:298-309`)。
- i18n:`zh.json`/`en.json` 增 `authProfiles.*`(顶层已有 `auth` L16 是平台登录,加 `authProfiles` 不冲突)。
- 契约:`api/types.ts` 增 `AuthProfile`/`AuthProfileCredential`;`ScanAuthentication`(`types.ts:243-255`)字段对齐 core `Authentication`。
- 测试范式:vitest + @testing-library/react + msw + MemoryRouter(骨架见 `pages/ScanNewPage.test.tsx`,`onUnhandledRequest: "error"` 严格拦截)。

## 9. 改动范围(按模块 / 文件级)

### core — 零改
- `validate_authentication` 服务复用不改(§12 不变量 2);`AuthValidationResult` 已存在(`:35-39`),直接 import。
- `Authentication` 模型不动(不改 core 扫描流程,不变量 1)。

### blackbox
- `pipeline/workflows.py`:新增 `AuthValidationWorkflow`(§6.5)。
- `pipeline/activities.py`:新增 `run_auth_validation_probe` activity(§6.2)。
- `pipeline/shared.py`:新增 `BlackboxAuthValidationInput`(对齐 `Blackbox*` 命名,继承 `BasePipelineInput`;§6.5)。
- worker 注册(三处,§6.1+§6.3):
  - `worker/runner.py:84` `workflows=[...]` 加 `AuthValidationWorkflow`(无 test 兜底)
  - `worker/runner.py:85-93` activities list 加 `run_auth_validation_probe`(`test_runner.py:61` 护栏)
  - `blackbox/worker.py:136-147` activities list 加(`test_worker.py:274` 护栏)
- 不改 `BlackboxScanWorkflow`。

### web 后端
- `components/auth_profile_store.py`(新):AuthProfileStore + 递归加密遍历(§5.2)+ 脱敏/空串保留(抄 `ws_config.py`)。
- `components/credential_vault.py`:不改 key/加密器,AuthProfileStore 复用其 Fernet 实例。
- `components/scan_manager.py`:`start_auth_validation`(照搬 `_submit_blackbox` 起 workflow)+ 选档案展开 scan-config;新增 stale probe reaper(启动期清 `workspaces/<ws>/auth-probes/*/` 孤儿,§10)。
- `api/auth_profiles.py`(新):CRUD + test + verify-status endpoints。
- `models.py`:`ScanRequest` 增 `auth_profile_id`/`auth_credential_id`(二选一 validator,与 `_blackbox_requires_reuse` 并列)。
- `app.py`:挂载新 router;lifespan 加 stale probe reaper 钩子。

### web 前端
- `pages/AuthProfilesPage.tsx`(新) + 组件 + 测试。
- `router.tsx`:加 `/p/:workspace/auth-profiles`。
- `pages/ScanNewPage.tsx` / `components/ScanFormFields.tsx`:Step4 来源切换(`AuthFields` `enabled` 升级三态)。
- `api/types.ts` + 新 `api/authProfiles.ts`。
- `locales/{zh,en}.json`:增 `authProfiles.*`。

## 10. 安全 / 权限 / 错误处理

- **权限**:`workspace_member`(看/用)+ `workspace_manager`(改/删),ws 级隔离。
- **加密**:敏感字段 Fernet 落盘(`auth-profiles.yaml`,消除凭据库明文债)。
- **脱敏**:GET 返 `••••`;空串=不改。
- **验证失败 ≠ 异常**:probe 返回 failure_point,workflow 正常完成,前端显示失败点;不触发扫描 fail-fast(它不在扫描流程里)。
- **probe 临时 YAML 清理**:验证后立即删 probe 目录(含明文密码,§2.1);**stale probe reaper**:worker 异常残留由 `scan_manager` 启动时扫 `workspaces/<ws>/auth-probes/*/` 清孤儿——无现成钩子(现有 lifespan 只有 `_migrate_legacy_*` / `_reconcile_*`),需新写。
- **Temporal 不可用/超时**:`verify-status` 返回错误,前端提示重试,`verify_status` 留 `unverified`。
- **重跑/restore 兼容**:现有 `scan-config.yaml` resume 回填逻辑(`scan_manager.py:237-250` 读 YAML 回填 `config_path`)不变;选档案发起的扫描同样落 `scan-config.yaml`,resume 仍工作。

## 11. 测试策略(TDD)

- **core**:零改,仅 `AuthValidationResult` 复用断言。
- **blackbox**:
  - `run_auth_validation_probe` 单测(成功 / 失败 / 无 structured output / provider 异常 四分支,断言不抛异常、返回 result、`validate_authentication` 调用参数含 `prompt_manager`+`executor`);
  - `AuthValidationWorkflow` 编排测试;
  - 两个 activity 注册护栏 test 同步绿(`test_worker.py` + `test_runner.py`);
  - workflow 注册自检 `runner.py:84` 含 `AuthValidationWorkflow`(无 test 兜底)。
- **web 后端**:AuthProfileStore 读写 / 递归加密落盘(含嵌套 `email_login.password`)/ 解密 / 脱敏 / 空串保留;CRUD + test + verify-status API;scan_manager 选档案展开 payload;`ScanRequest` 二选一 validator;stale probe reaper。
- **web 前端**:认证管理页组件(列表/CRUD/测试按钮/状态徽章/轮询);扫描页选档案→传 profile_id(`AuthFields` 三态切换)。

## 12. 不变量

1. **core 扫描流程不改**:`BlackboxScanWorkflow` / exploit / 双轨(authz/inj/xss/ssrf)不碰(CLAUDE.md §1)。
2. **`validate_authentication` 服务复用不重写**:probe 只是"不抛异常的包装"(透传其 result)。
3. **`scan-config.yaml` 合流点不变**:web 写 YAML → core 读 YAML(CLI/Web 合流)⇒ per-scan YAML 必须明文(core 不解密),其明文债子项目 1 内不消除(§2.1)。
4. **双引擎一致**:验证经 `run_claude_prompt` 统一抽象(CLAUDE.md §2)。
5. **WsConfigStore 不被污染**:认证档案走独立 `AuthProfileStore`,provider/git 配置语义不混。
6. **D1–D4 不回退**:本子项目不重新引入 `success_condition` / cookie 兜底。

## 13. 风险与缓解

- **R1 probe 成本认知**:每次"测试登录"=真实 agent-browser 登录,有 LLM token 成本 + 几十秒~~2min。缓解:UI 按钮文案明确"将真实登录一次";轮询期间 spinner;失败点清晰。
- **R2 per-scan 明文债未消除**:子项目 1 只加密持久化层,per-scan `scan-config.yaml` 仍明文(不变量 3)。缓解:0600 权限 + 扫描/probe 结束即删;彻底消除留待 core 接受密文(超范围)。
- **R3 多角色档案的误用预期**:用户可能期望多身份对比(子项目 2)。缓解:管理页/扫描页文案明确"一次扫描选一个角色登录";多身份对比标为"规划中"。
- **R4 activity 注册遗漏(双 worker)+ workflow 注册(无 test 兜底)**:→ worker 起不来 / 提交后无 worker 能跑。缓解:§6.3 双 worker 双 test + §6.1 workflow 注册显式任务。
- **R5 改 web/worker src 须 rebuild 镜像**:plan/冒烟阶段注意。
- **R6 `validate_authentication` 签名/executor 构造**:probe 必传 `prompt_manager`+`executor`,容器内 prompts 路径解析口径须对齐。缓解:照搬 `run_blackbox_auth_validation`(`activities.py:180-182`)。

## 14. 与子项目 2 的关系

子项目 2(多身份 IDOR 对比扫描)前置依赖本子项目的多角色认证档案(没有多角色档案就没有多身份可用)。子项目 2 改 core 扫描流程(多 browser session + authz-exploit agent 多身份交叉检测),风险高(动 CLAUDE.md §1 authz 双轨),单独 spec/plan。本子项目为其铺好"凭据供给"地基,但本身不实现对比扫描。
