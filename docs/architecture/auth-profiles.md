# 认证档案

认证档案把可复用的多角色登录配置存储在 Web workspace 层，供认证预检、白盒/黑盒/组合扫描和 authz 多身份对比使用。它解决的是“同一目标反复手写 YAML 凭据、无法验证、明文散落”的问题。

## 数据模型

`AuthProfile`：

| 字段 | 说明 |
|---|---|
| `id` / `name` | 档案 ID 与 workspace 内唯一名称 |
| `login_url` | 登录入口 |
| `login_type` | `form` / `sso` / `api` / `basic` |
| `login_flow` | 可选步骤说明，供登录 agent 理解多步流程 |
| `credentials[]` | 多角色凭据 |
| `scope` | `workspace` 或 `system` |

`AuthProfileCredential`：

- `id`
- `role`
- `username`
- `password`
- `totp_secret`
- `email_login{address,password,totp_secret}`：邮箱侧二次登录
- `verify_status`：最近一次测试登录状态

`VerifyStatus`：

- `state`: `unverified` / `running` / `success` / `failed`
- `failure_point`: `username_or_password` / `totp_secret` / `out_of_band`，或内部 `engine` / `no_verdict`
- `failure_detail`
- `last_verified_at`
- `probe_dir` / `workflow_id`：定位实时日志与历史过程

`engine` 表示 LLM/provider 调用失败，与目标站账号密码无关；`no_verdict` 表示 agent 跑完但没有结构化登录结论。

## 作用域与存储

存储文件：

```text
workspaces/<ws>/auth-profiles.yaml
workspaces/.system/auth-profiles.yaml
```

- **workspace 档案**：仅当前 ws 可见、可编辑。
- **system 档案**：由启动时 `configs/*.yaml` seed 到 `.system`，所有 workspace 共享且只读。
- **fork**：系统档案可复制为同 id 的 workspace 副本；副本按 workspace 优先级遮蔽系统原型，可独立编辑。
- `.system` 是保留 workspace；用户 API 不能创建点开头 workspace，避免碰撞。
- 路径穿越由 workspace segment 校验和 resolved path 边界检查阻止。

敏感字段只包括 credential 级与 email_login 级的 `password`、`totp_secret`。落盘前用 `CredentialVault` 的 Fernet 加密；读取时解密为内存明文。加密是显式 schema 路径遍历，不做泛型递归，避免嵌套结构漏加密。

## API 读写语义

路由位于 `/api/workspaces/{ws}/auth-profiles`：

- 查看和使用：workspace member。
- 创建、更新、删除、fork：workspace manager。
- `GET /` 与 `GET /{pid}` 返回脱敏值：有值字段显示 `••••`，无值为 `null`。
- 更新 credential 时，敏感字段传空字符串表示“不修改”，不表示清空。
- payload 显式携带 `credentials[]` 时表示完整目标列表，缺失 id 会被删除；不携带则保留原列表，兼容局部更新。
- 系统档案 PUT/DELETE 拒绝 403；fork 已存在返回 409。

前端页面提供档案管理、角色凭据编辑、单角色/批量测试登录和实时日志恢复。

## 测试登录

认证测试不是简单 HTTP 探针，而是浏览器登录 agent：

1. Web 将选中 credential 展开为临时 `scan-config.yaml`。
2. 可选择 HOST 档案或 HOST URL；选中后为该 credential 启动独立 host proxy。
3. 提交 `AuthValidationWorkflow` 或批量 workflow。
4. agent 用当前浏览器引擎执行登录流程，可生成 TOTP，按 navigate/fill/submit/verify_session 里程碑上报。
5. 成功后保存 `auth-state.json`。
6. Web 读取 Temporal 终态并回写 `VerifyStatus`。
7. 临时明文 config 被删除；events 与 auth state 保留用于诊断和后续复用。

单角色与批量模式共用同一 core 验证链路。批量 workflow 串行逐 credential 验证，避免同时打开多个浏览器；每个 cred 的状态仍可独立恢复。

## 扫描展开

`ScanManager._dump_auth_config` 支持三种互斥引用方式：

- `auth_profile_id + auth_credential_ids[]`：选择角色子集。
- `auth_profile_id + auth_credential_id`：旧单角色契约。
- `auth_profile_id`：展开全部角色。

展开逻辑：

- 第一个 low-privilege credential 作为 primary authentication；没有 low 则回退第一个。
- 其余 credential 变成 `accounts[]`，携带 slug 化 id、role、tier、credentials。
- `derive_privilege_tier` 根据 role 判断 high/low；当前 high 权限名称表包含 `admin`。
- 输出写入 scan 目录的 `scan-config.yaml`，core `parse_config` 统一消费。
- `session.json` 只保存 `profile_id/cred_id/cred_ids` 引用，不保存明文。

authz exploitation 可读取 `identity-manifest.json`；至少两个 available 身份时，prompt 注入身份切换/对比上下文。

## 与认证漏洞检测的关系

认证档案是运行配置和身份准备，不是 auth 漏洞确定性轨。auth 漏洞仍由 `vuln-auth` 纯 LLM 轨分析；authz 的 Vertical/Context 由 `vuln-authz` 分析，IDOR 另有 GitNexus 候选轨。不要把档案字段或登录测试结论直接当作漏洞结论。

历史 deepsec matcher 借鉴方案曾放在本文档前身中，但它属于设计参考而非已实现功能；当前实现以 `AuthProfileStore`、Web API 和 `validate_authentication` 为准。

## 安全边界

- GET 永远脱敏；服务端读取时才解密。
- Fernet key 与 CredentialVault 同源，避免另建密钥体系。
- 临时验证 config 只在验证期间存在，终态后删除。
- probe_dir / workflow_id 校验必须限制在允许父目录，防 TOCTOU 路径注入。
- 登录 agent 的浏览器会话固定 `agent1`，验证结束回收。
- HOST proxy per-credential 独立启动和清理，避免端口/状态串扰。

## 验证入口

- `packages/web/tests/test_auth_profile_store.py`
- `packages/web/tests/test_api_auth_profiles.py`
- `packages/web/tests/test_scan_manager_profile_expansion.py`
- `packages/web/tests/test_scan_request_auth_profile.py`
- `packages/web/tests/test_auth_probe_timeout_env.py`
- `packages/core/tests/prompts/test_validate_auth_structured_verdict.py`
