# 认证档案库 + 独立验证 + 黑盒扫描复用 设计

- 日期:2026-08-05
- 分支:feat/fork-py
- 状态:待 plan
- 范围:**子项目 1**(认证档案库 + 独立验证入口 + 黑盒扫描选档案复用)。子项目 2(多身份 IDOR 对比扫描)为后续独立 spec,依赖本子项目。

## 1. 背景与动机

### 1.1 用户需求

1. 登录模块独立化:可配置认证信息、**单独**验证登录可行性、用户选择保存验证成功的认证信息。
2. 黑盒扫描可选已保存的认证信息,作为黑盒扫描的登录模块走扫描流程。

### 1.2 现状(经代码核查)

- **core 层已有"独立验证登录"的能力,但没有独立入口**:`validate-authentication` agent + `validate_authentication` 服务(`packages/core/src/supernova_core/services/validate_authentication.py`)能单独驱动 agent-browser 登录、判定成功、产出 `auth-state.json`。但它目前只被嵌在 `BlackboxScanWorkflow` 的 `run_blackbox_auth_validation` activity 里(`packages/blackbox/src/supernova_blackbox/pipeline/workflows.py:152-164`),没有"不跑扫描、只测登录"的入口。
- **认证信息无可复用的持久化**:每次新黑盒扫描都要重填;web 端只把配置落进 per-scan 的 `scan-config.yaml`(`packages/web/src/supernova_web/components/scan_manager.py:289-326`),仅供"重跑/resume"预填(`scans.py:90-106`),**不跨扫描共享、无凭据库**。
- **明文落盘安全债**:`scan-config.yaml` 的 `password`/`totp_secret`/`email_password` 明文;同一仓库的 `WsConfigStore` 已有现成 Fernet 加密器 `CredentialVault`(`packages/web/src/supernova_web/components/credential_vault.py`,字段白名单甚至预留 `auth_token`),但黑盒登录完全没接上。
- **现成可复用地基**:per-workspace `WsConfigStore`(`workspaces/<ws>/config.yaml`)+ `CredentialVault` + `workspace_member` 权限体系。
- **两套独立 auth(确认不混淆)**:web 平台用户登录(`packages/web/src/supernova_web/auth/`,SQLite+bcrypt+cookie)与黑盒目标站点登录(本 spec)是两套,不重叠。

### 1.3 与 D1–D4 重构的关系(关键)

同日有 spec `docs/superpowers/specs/2026-08-05-login-success-check-simplification-design.md`(状态待 plan),其中:
- **D1**:`success_condition` 是死字段(收集/校验/写 YAML 但从不注入 prompt,对扫描零影响)→ 删 `SuccessCondition`(`config.py:25-27`)+ `Authentication.success_condition`(`config.py:45`)。
- **D2**:登录成功判定并入 `login_flow` 自然语言(零接通成本)。
- **D3**:去 cookie 兜底,纯信模型 `login_success` 字段判定。
- **D4**:改 `login-instructions.txt` VERIFICATION 段。

本子项目与 D1–D4 改同一批文件(`Authentication` 模型 / 前端 `AuthFields` / `scan_manager` / i18n)。协调方案见 §7。

## 2. 目标与范围

### 2.1 目标(本期,子项目 1)

1. workspace 级、多角色的**认证档案库**(可保存、可复用、加密落盘)。
2. 独立的**"认证管理"页**:CRUD 档案 + 每个角色凭据单独"测试登录"(真实驱动 agent-browser 登录,显示成功/失败/失败点),验证成功的可保存。
3. 黑盒扫描发起页可选**已保存档案/角色**(web 层把该角色凭据展开成单 `credentials` 喂 core,**core 扫描流程不改**);保留临时填写(向后兼容)。
4. 顺带消除"黑盒密码明文落盘"安全债。

### 2.2 范围外(YAGNI / 后续)

- **子项目 2:多身份 IDOR 对比扫描**(一次扫描多身份交叉检测越权)——需改 core 扫描流程(多 session + authz agent 重写),是独立大工程,**后续单独 spec**,依赖本子项目的多角色档案。
- **CLI**:不动(`scan-config.yaml` 是合流点,CLI 吃 YAML 天然兼容;认证档案 CRUD 暂只 web)。
- **`auth-state.json` 跨扫描复用**:仍是 per-scan、扫描结束清理(`workflows.py:517`),本 spec 不动。
- 自动探测认证模式、Python 端确定性硬断言。

## 3. 已确认设计决策(brainstorming 结论)

| 决策 | 选择 | 理由 |
|---|---|---|
| 档案归属层级 | **workspace 级** | 复用 WsConfigStore/CredentialVault,与黑盒 per-ws 模型天然对齐 |
| 验证形态 | **独立"认证管理"页** | 贴合"独立功能";扫描页只做"选档案/角色" |
| 身份粒度 | **一档案多角色** | 一个 login_url 下多组凭据(多角色),为子项目 2 铺路 |
| 多角色本期用途 | 归拢 + 逐角色验证 + 选角色单身份登录 | 不动 core 扫描流程;多身份对比留子项目 2 |
| 验证执行架构 | **独立 AuthValidation workflow** | 复用现有 activity + Temporal 可观测;agent-browser 只在 worker |
| D1–D4 协调 | D1/D2/D4 合并;D3 建议同批(可拆) | 见 §7 |

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
   选档案:scan_manager 把该角色凭据展开成单 credentials ──▶ scan-config.yaml
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
  login_flow:               # 自然语言登录步骤 + 成功标志(D2; 无 success_condition)
    - "打开登录页"
    - "填账号密码"
    - "成功标志:URL 含 /dashboard"
  credentials:              # 多角色
    - id: cred_admin
      role: admin
      username: admin
      password: <Fernet 密文>
      # totp_secret / email_login 按需
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

### 5.2 AuthProfileStore(新)

- 文件:`packages/web/src/supernova_web/components/auth_profile_store.py`(新,平行于 `ws_config_store.py`)。
- 读写 `workspaces/<ws>/auth-profiles.yaml`(list[AuthProfile])。
- **加密**:`CredentialVault` 扩字段白名单 `CREDENTIAL_FIELDS` 增 `password`/`totp_secret`/`email_login.password`/`email_login.totp_secret`;store 在 save 时递归遍历 `auth_profiles[].credentials[]` 加密、load 时解密。
- **脱敏**:GET 返回时密码等字段返 `"••••"`(对齐 WsConfig 模式);PUT 时空串=不改(保留原密文)。
- 模型:用 dataclass 或 pydantic,字段对齐 core `Authentication`(剔除 `success_condition`,见 §7)。

## 6. 独立验证 workflow(核心技术)

### 6.1 AuthValidationWorkflow(新)

- 位置:`packages/blackbox/src/supernova_blackbox/pipeline/workflows.py`(加 `@workflow.defn class AuthValidationWorkflow`),或新文件。轻量,只编排一个 probe activity。
- 输入 `AuthValidationInput`:`web_url` + `config_path` + `workspace_path`(对齐 `validate_authentication(...)` 签名)。
- 流程:`log_phase_start_activity(phase="auth-validation")` → `run_auth_validation_probe(input)` → 返回 `AuthValidationResult`。
- **不跑**黑盒 workflow 的其余步骤(无白盒产物依赖、无 exploit)。

### 6.2 run_auth_validation_probe activity(新)

- 位置:`packages/blackbox/src/supernova_blackbox/pipeline/activities.py`。
- 调 core `validate_authentication(web_url, config_path, workspace_path, ...)`(**复用、不重写**)。
- **关键区别于 `run_blackbox_auth_validation`**:后者在 `success=False` 时抛 `PentestError(AUTH_LOGIN_FAILED, non-retryable)` 让扫描 fail-fast;本 probe **不抛异常**,把 `AuthValidationResult(success, failure_point, failure_detail)` 原样作为 activity 返回值 → workflow 永远 `success`,结果在返回值里。
- retry policy:复用 `retry_for("auth-validation")` 或更紧;provider 异常(无 structured output)仍按 D3 视为失败结果返回,不 fail workflow。

### 6.3 worker 注册(易漏点)

新 activity 必须加进 blackbox worker 的 activities 列表;`assert_all_activities_registered`(黑白盒共用,见 memory:temporalio-activity-worker-registration)`set(@activity.defn)==set(注册)` 会校验。plan 阶段确认 worker 注册点。

### 6.4 web 触发 / 取结果 / 清理

`scan_manager.start_auth_validation(ws, profile_id, cred_id)`:
1. 从 `AuthProfileStore` 读档案、定位 credential。
2. 在 `workspaces/<ws>/auth-probes/<probe_id>/` 写 minimal `scan-config.yaml`(仅 `authentication` 段,含该角色凭据 + 档案 login_url/login_type/login_flow)。
3. `config_path` 指向该 yaml,`workspace_path` 指向 probe 目录(`auth-state.json` 落点),`web_url = login_url`。
4. `client.start_workflow(AuthValidationWorkflow.run, input, task_queue=WEB_TASK_QUEUE_BLACKBOX)` → 返回 workflow_id。

取结果:`GET .../verify-status` → `client.get_workflow_handle(wfid).result()` 拿 `AuthValidationResult`(比 events 轻) → 回填 `verify_status` → 删除 probe 临时目录(`scan-config.yaml` 含明文密码,验证后立即清理)。

> 注:本 spec 改 web/worker src,生效须 rebuild `supernova-worker`(及 web)镜像(见 memory 同类 web 配置注入约束)。

## 7. 后端 API

- `GET    /api/workspaces/{ws}/auth-profiles` — 列表(脱敏)
- `POST   /api/workspaces/{ws}/auth-profiles` — 新建
- `GET    /api/workspaces/{ws}/auth-profiles/{pid}` — 详情(脱敏)
- `PUT    /api/workspaces/{ws}/auth-profiles/{pid}` — 更新(空串敏感字段=不改)
- `DELETE /api/workspaces/{ws}/auth-profiles/{pid}` — 删除
- `POST   /api/workspaces/{ws}/auth-profiles/{pid}/credentials/{cid}/test` — 触发验证 → `{workflow_id}`
- `GET    /api/workspaces/{ws}/auth-profiles/{pid}/credentials/{cid}/verify-status` — 轮询结果
- 鉴权:`workspace_member` 依赖;manager 角色才能改/删(对齐多租户既有角色,见 memory:web-multitenant-platform-plan)。
- `ScanRequest`(`packages/web/src/supernova_web/models.py`)增可选 `auth_profile_id` + `auth_credential_id`,与 inline `authentication` dict **二选一**(model_validator 校验互斥);scan_manager 选档案时展开该角色 → `scan-config.yaml` → 现有 `BlackboxScanWorkflow`(core 不改)。

## 8. 前端

- **新路由 `/p/:ws/auth-profiles`**(认证管理页):档案列表 + 新建/编辑/删除;详情里多角色 credentials 表,每行"测试登录"按钮 + 状态徽章(未验证 / ✓成功 / ✗失败+失败点);实时轮询 verify-status(测试进行中 disabled + spinner)。
- **扫描页 Step4 改造**(`ScanFormFields.tsx` / `ScanNewPage.tsx`):加"来源"切换——【选已保存档案 → 选档案下拉 → 选角色下拉】/【临时填写(保留现有 AuthFields)】。选档案时前端只传 `auth_profile_id`+`auth_credential_id`,由后端展开。
- **删 `success_condition` UI**(D1):`scType`/`scValue`、相关 i18n、校验、`buildAuthPayload`/`authFromPayload` 对应项。
- i18n:`zh.json`/`en.json` 增 `authProfiles.*`,删 `scan.auth.success*`。
- 契约:`api/types.ts` 增 `AuthProfile`/`AuthProfileCredential`,删 `ScanAuthentication.success_condition`。

## 9. D1–D4 协调

- **D1(删 `success_condition` 死字段)+ D2(判定并入 login_flow)+ D4(改 VERIFICATION 段)→ 合并进本子项目**:低风险、同批文件,避免二次改。AuthProfile schema 本就不含 `success_condition`;core `Authentication`/parser/前端 AuthFields/i18n 一并删。
- **D3(去 cookie 兜底、纯信字段判定)→ 建议同批做**:`validate_authentication` 正是验证 probe 调的服务,D3 让"验证"更可靠(宁严勿假阳,符合"验证成功才保存"语义)。但 D3 改变判定行为、有 R1 假阴性风险(见 D1–D4 spec §5);**若希望 D3 独立 plan/单独冒烟,可拆出**,本子项目在其未合时仍可工作(当前 cookie 兜底仍在,验证结果仍正确,只是偶有假阳)。
- plan 阶段需核实:原始 shannon(`/root/shannon`)Authentication schema 是否有 `success_condition`(D1–D4 spec §4 不变量 5 已记)。

## 10. 改动范围(按模块 / 文件级)

### core
- `models/config.py`:删 `SuccessCondition` + `Authentication.success_condition`(D1);`Authentication` 其余字段不动(子项目 1 不改 core 扫描流程)。
- `config/parser.py`:删 `success_condition` 清洗(D1)。
- `services/validate_authentication.py`:D3(若同批)— 仲裁改纯信字段;`AuthValidationResult` 已存在(`:35-39`),复用。
- `prompts/shared/login-instructions.txt`:D4 VERIFICATION 段。
- **不改** `BlackboxScanWorkflow`、`validate_authentication` 主体逻辑、双轨。

### blackbox
- `pipeline/workflows.py`:新增 `AuthValidationWorkflow`。
- `pipeline/activities.py`:新增 `run_auth_validation_probe` activity(复用 `validate_authentication`,不抛异常)。
- `pipeline/shared.py`:新增 `AuthValidationInput`。
- worker 注册:加新 activity(`assert_all_activities_registered`)。
- **不改** `BlackboxScanWorkflow`。

### web 后端
- `components/auth_profile_store.py`(新):AuthProfileStore + 加密/脱敏。
- `components/credential_vault.py`:`CREDENTIAL_FIELDS` 扩敏感字段。
- `components/scan_manager.py`:`start_auth_validation` + 选档案展开 scan-config。
- `api/auth_profiles.py`(新):CRUD + test + verify-status endpoints。
- `models.py`:`ScanRequest` 增 `auth_profile_id`/`auth_credential_id`(二选一 validator)。
- `app.py`:挂载新 router。

### web 前端
- `pages/AuthProfilesPage.tsx`(新) + 组件。
- `router.tsx`:加 `/p/:ws/auth-profiles`。
- `pages/ScanNewPage.tsx` / `components/ScanFormFields.tsx`:Step4 来源切换 + 删 success_condition(D1)。
- `api/types.ts` + 新 `api/authProfiles.ts`。
- `locales/{zh,en}.json`:增删文案。

## 11. 安全 / 权限 / 错误处理

- **权限**:`workspace_member`(看/用)+ `workspace_manager`(改/删),ws 级隔离(对齐多租户)。
- **加密**:敏感字段 Fernet 落盘(消除明文债)。
- **脱敏**:GET 返 `••••`;空串=不改。
- **验证失败 ≠ 异常**:probe 返回 failure_point,workflow 正常完成,前端显示失败点;不触发扫描 fail-fast(它根本不在扫描流程里)。
- **probe 临时 YAML 清理**:验证后立即删 probe 目录(含明文密码);worker 异常残留由 scan_manager 启动时清 stale probe。
- **Temporal 不可用/超时**:`verify-status` 返回错误,前端提示重试,`verify_status` 留 `unverified`。
- **重跑/restore 兼容**:现有 `scan-config.yaml` resume 回填逻辑(`scan_manager.py:238-240`)不变;选档案发起的扫描同样落 `scan-config.yaml`,resume 仍工作。

## 12. 测试策略(TDD)

- **core**:`validate_authentication` D3 分支单测(若同批);`AuthValidationResult` 复用断言。
- **blackbox**:`run_auth_validation_probe` 单测(成功/失败/无 structured output 三分支,断言不抛异常、返回 result);`AuthValidationWorkflow` 编排测试;`assert_all_activities_registered`。
- **web 后端**:AuthProfileStore 读写/加密落盘/脱敏/空串保留;CRUD + test + verify-status API;scan_manager 选档案展开 payload;`ScanRequest` 二选一 validator。
- **web 前端**:认证管理页组件(列表/CRUD/测试按钮/状态徽章/轮询);扫描页选档案→传 profile_id;删除 success_condition 相关断言。
- **D1–D4 回归**:success_condition 删除后 `test_scans_api.py` fixture 改 login_flow;`ScanNewPage` 测试删 scType/scValue 断言。

## 13. 不变量

1. **core 扫描流程不改**:`BlackboxScanWorkflow` / exploit / 双轨(authz/inj/xss/ssrf)不碰(CLAUDE.md §1)。
2. **`validate_authentication` 服务复用不重写**:probe 只是"不抛异常的包装"。
3. **`scan-config.yaml` 合流点不变**:web 写 YAML → core 读 YAML(CLI/Web 合流)。
4. **双引擎一致**:验证经 `run_claude_prompt` 统一抽象(CLAUDE.md §2)。
5. **WsConfigStore 不被污染**:认证档案走独立 `AuthProfileStore`,provider/git 配置语义不混。

## 14. 风险与缓解

- **R1 probe 成本认知**:每次"测试登录"=真实 agent-browser 登录,有 LLM token 成本 + 几十秒~~2min。缓解:UI 按钮文案明确"将真实登录一次";轮询期间 spinner;失败点清晰。
- **R2 D3 假阴性(若同批)**:去 cookie 兜底后偶发假阴(验证判失败但实际成功)。缓解:可见失败可重试;D1–D4 spec §5 R1 已述,远优于假阳。
- **R3 多角色档案的误用预期**:用户可能期望多身份对比(子项目 2)。缓解:管理页/扫描页文案明确"一次扫描选一个角色登录";多身份对比标为"规划中"。
- **R4 activity 注册遗漏**:新 activity 漏注册 → worker 起不来/调用失败。缓解:`assert_all_activities_registered` + TDD。
- **R5 改 web/worker src 须 rebuild 镜像**(memory 同类约束):plan/冒烟阶段注意。

## 15. 与子项目 2 的关系

子项目 2(多身份 IDOR 对比扫描)**前置依赖**本子项目的多角色认证档案(没有多角色档案就没有多身份可用)。子项目 2 改 core 扫描流程(多 browser session + authz-exploit agent 多身份交叉检测),风险高(动 CLAUDE.md §1 authz 双轨),单独 spec/plan。本子项目为其铺好"凭据供给"地基,但本身不实现对比扫描。
