# 认证档案多角色 + 黑盒 inline 统一 设计

> 日期 2026-08-06 · 状态：Approved（待 writing-plans）· 分支 `feat/fork-py`

## 目标

让「认证档案新建/编辑」与「黑盒扫描页临时填写」两处认证凭据录入**支持多角色多账号**，并**统一为同一套录入组件与视觉语言**。顺带彻底删除从未生效的死字段 `email_login`。同时把黑盒页认证区收敛为**单一 disclosure（展开即启用）**，消除冗余的「需要登录」开关。

## 背景与现状（关键事实）

- **后端多身份基础设施已就绪**：`AuthProfile.credentials: list[AuthProfileCredential]` 是多账号列表（每条 `role / username / password / totp_secret? / verify_status`）；创建 POST 与更新 PUT 都已接收多条 `credentials[]`。本次**不改模型、不改创建语义**。
- **档案页 `AuthProfileDialog` 单账号硬限制**：提交 body 硬编码 `credentials: [cred]`（单元素），编辑态只读 `credentials[0]`，第 2 个起丢失（`AuthProfileDialog.tsx:60-73,36-37`）。`loginType` 用 shadcn `Select` 下拉，无 coral 竖条 / `GroupLabel` / `Segmented`，与黑盒页不是一套视觉密度。
- **黑盒页 inline 临时填写单角色**：`ScanNewPage.tsx` inline 模式发 `authentication`（单凭据，`AuthFormState` 仅单组 role/username/password），后端 inline 分支只写 `{"authentication": {...}}` 不写 `accounts[]`（`scan_manager.py:428-434`）。
- **PUT 不支持删角色**：`auth_profiles.py:59-101` 只 upsert（带 id 更新 / 不带 id 追加），payload 里没出现的已有 credential 不删——编辑态无法删角色。
- **非 primary 账号丢 totp**：`_expand_multi_identity` 的 accounts 条目只带 `{username, password}`，丢 `totp_secret`（`scan_manager.py:383-388`）。
- **`email_login` 是死字段**：模型/plumbing 忠实搬运，但唯一"消费"点 `prompts/manager.py:379-390` 把 `$email_*` 替换进 login_flow 的逻辑——这三个占位符只在该 manager 与其单测里出现，生产 login_flow / prompt 模板 / config seed 零命中，仓库无 IMAP/POP3 代码。从未生效。前端已于 2026-08-06 删除采集。

## 设计

### 1. 共享子组件（新增 `packages/web/frontend/src/components/auth/`）

| 组件 | 职责 | 来源 |
|---|---|---|
| `GroupLabel` | coral 竖条 + 小号 semibold 标题 + 可选 cap 副标题（中文去 uppercase） | 抽自 `ScanFormFields.tsx:84-91` |
| `Segmented` | 通用 segmented（`bg-muted/40` hairline track + active `bg-card shadow-sm`） | 统一 `SettingsPage.tsx:38-72` `ThemeSegmented` 与黑盒页两处内联 segmented |
| `CredentialRows` | 多角色增删行：每行 角色/用户名/密码/可选 TOTP/删除；底部「+ 添加角色」 | 新建 |

`CredentialRows` props：`value: CredentialDraft[]`、`onChange`、`allowMulti: boolean`、`showTotp: boolean`。TOTP 默认每行折叠（行内「+ 二步验证」展开），避免无 2FA 的角色白占整行。

### 2. 两处消费（统一）

**档案页 `AuthProfileDialog`**（保持 Dialog 形态）：
- `GroupLabel` 分「登录入口」「角色凭据」两段
- `loginType` 从 `Select` 换成 `Segmented`
- 角色凭据用 `<CredentialRows allowMulti showTotp />`
- 状态从单组 `role/username/password` 升为 `CredentialDraft[]`
- 编辑态加载 `editing.credentials` 全量成可编辑行

**黑盒页 `ScanFormFields`**：
- 内联 `GroupLabel` / source-loginType segmented / inline 凭据三列 → 替换为共享组件
- **inline 临时填写升级多角色**（`CredentialRows allowMulti`）；底部对齐 profile 模式给「已添加 N 个角色，将分别验证」计数
- 角色→tier 提示：行内标注 `admin` = 高权、其余按低权（后端 `derive_privilege_tier` 现仅认 `admin`），避免操作者误以为自定义 role 名跑的是高权对比
- 「保存为档案」(`SaveAsProfileInline`) 同步存多角色 `credentials[]`
- profile 模式角色多选（`BottomProfileBlock`）**不动**（那是「选已建档案角色」，语义与「录入」不同）

**黑盒认证区单一 disclosure（展开即启用，scan 页专属）**：

当前 `authExpanded`（UI 展开）与 `auth.enabled`（提交是否带 auth）是**双状态、双控件**——`配置登录/收起` 按钮 + `RightAuthCore` 内 `需要登录` Switch。问题：不需要登录根本不会展开，展开已隐含「要登录」，Switch 冗余；且「收起保留 enabled」是 footgun（UI 藏起、提交却仍带登录）。

- **目标**：单一 disclosure，**展开 == 启用**。删 `RightAuthCore` 内 `需要登录` Switch；`authExpanded` 整个并入 `auth.enabled`（保留 `enabled`——它是 `buildBody` 的判据）。
- **收起 = 停用但留草稿**：`收起` 置 `enabled=false`，**不清** `AuthFormState` 字段（role/username/password/totp/loginUrl/loginFlow 全留），再展开恢复原值。collapsed 态若草稿非空，按钮给存稿标记（如 `配置登录 · 已配置` 或小圆点），提示「有存稿、当前未启用」。
- **不变量**：重跑预填 `enabled=true` → 自动展开露出预填配置（现有 `useState(() => f.auth.enabled)` 行为保留）；`buildBody` 仍 `if (f.auth.enabled)` 才发 auth；`BottomProfileBlock`（profile 选取）与档案页 Dialog（无展开/折叠概念）不涉及。
- **测试点**：收起后 `buildBody` 不带 auth 字段；再展开字段值不变；collapsed 有草稿时按钮显标记；rerun `enabled=true` 自动展开。

### 3. 后端改动

#### 3.1 `email_login` 彻底删（死代码清理，无运行时行为变化）

- 模型：`packages/core/src/supernova_core/models/config.py:26-29`（`EmailLogin` 类）+ `:35`（`Credentials.email_login`）；`packages/web/src/supernova_web/components/auth_profile_store.py:43-46`（`EmailLoginCred`）+ `:55`（字段）
- Plumbing：`auth_profile_store.py:82-105`（加密/解密/脱敏 email 分支）、`:109-128`（`credential_to_authentication`）、`:287-292,307`（`seed_from_config`）；`packages/core/src/supernova_core/config/parser.py:113-123,131,184-189`（sanitize）+ `:12` import；`packages/core/src/supernova_core/prompts/manager.py:379-390`（`$email_*` 替换块）；`packages/web/src/supernova_web/api/auth_profiles.py:11-12,85-92,97`
- 前端：`api/types.ts:258,281`；`ScanFormFields.tsx:22`、`ScanNewPage.tsx:59` 注释
- 测试：`core/tests/test_config.py:47-85`、`test_parser.py:12,254-285,337-352`、`test_prompt_manager.py:190-213`；`web/tests/test_auth_profile_store.py`（docstring + fixture L23-24 + 断言 L39-44,49-50,79）；`frontend/src/pages/ScanNewPage.test.tsx:750-753`
- 数据：磁盘老 `auth-profiles.yaml` 残留 `email_login` 密文，pydantic 默认 `ignore` extras 静默丢弃，不报错（可选一次性清理脚本，非必须）

#### 3.2 inline 多角色（core 零改动）

- `packages/web/src/supernova_web/models.py` `ScanRequest` 加 `auth_accounts: list[dict] | None = None`
- `_auth_profile_xor_inline`（`:68-88`）声明 `auth_accounts` 仅在 `authentication` 存在时合法
- `scan_manager._resolve_blackbox_inputs` inline 分支（`:428-434`）：`req.auth_accounts` 非空时复用 `_expand_multi_identity`（`:361-393`）的展开逻辑，产 `{"authentication": primary_auth, "accounts": [...]}`，调 `_dump_auth_payload` 写盘
- `accounts[]` 条目形状对齐 profile 模式：`{id, role, tier, credentials:{username, password, totp_secret?}}`；`tier` 由后端 `derive_privilege_tier(role, ["admin"])` 推导；`id` 由后端按 role slug 生成（满足 `^[a-z0-9-]+$`，重名序号去重）
- core `validate_authentication` 已 accounts-aware、YAML 驱动，inline 与 profile 在 YAML 合流点合流，**零改动**
- inline 凭据仍只留 `scan_dir/scan-config.yaml`，不进加密 vault（保持 inline 卫生边界）

#### 3.3 PUT 支持删角色（全量 diff 语义）

`auth_profiles.py:update_profile`（`:59-101`）改为：
- payload.credentials 视为**完整目标列表**
- existing 有但 payload 没有的 credential id → 删除
- 带 id 命中 → upsert（保留「空串 secret = 保留原值」语义，`:81-84`；编辑态密码脱敏不改则发空）
- 不带 id / 未命中 → 追加
- 系统档案（`scope == "system"`）仍 403 只读

#### 3.4 非 primary 补 totp

`_expand_multi_identity` accounts 条目（`scan_manager.py:383-388`）带上 `totp_secret`（`email_login` 已删，不带）。

### 4. `login_flow` 定位

`login_flow`（多行文本）定位为**自然语言登录流程描述**，承载所有非结构化登录步骤（含邮箱取 OTP、SSO 跳转等），由登录 agent 自行理解执行。替代被删的 `email_login` 结构化字段。

### 5. 范围边界（不做）

- profile 模式角色多选（`BottomProfileBlock`）
- `high_priv_names=["admin"]` 硬编码（`scan_manager.py:369`）——deferred follow-up，可选 env 化

## 数据契约

**前端 `CredentialDraft`**（共享组件内部态）：
```ts
{ id?: string; role: string; username: string; password: string; totpSecret: string }
```
- 新建 `id` 空（后端分配）；编辑 `id` 透传原值
- `password` 空串 = 不改（编辑态）；`totpSecret` 空串 = 无

**ScanRequest 新字段**：
```py
auth_accounts: list[dict] | None = None  # inline 多角色；每条 {role, username, password, totp_secret?}
```

**PUT body**（全量 diff）：`credentials[]` 是完整目标列表，靠 id 决定 upsert / delete / add。

## 测试策略

**前端**：
- `AuthProfileDialog`：多角色增删行 / 提交 body `credentials[]` 多条 / 编辑加载全量 credentials / 删角色提交后该 id 消失 / 每行 TOTP 可选
- 共享组件 `GroupLabel` / `Segmented` / `CredentialRows` 单测
- 黑盒页 `ScanFormFields` 回归：inline 多角色录入 / inline 多角色保存为多角色档案 / inline 多角色提交发 `auth_accounts` / 现有 54 测同步迁移后全绿
- tsc 零 + vite build

**后端**：
- `scan_manager`：inline `auth_accounts` 非空 → `scan-config.yaml` 含 `accounts[]` 且 tier/id 正确；空 → 退回单 authentication
- `auth_profiles` PUT：全量 diff 删角色（payload 不含的 id 被删）/ 空串密码保留 / 追加新角色 / 系统 403
- `_expand_multi_identity`：非 primary accounts 带 `totp_secret`
- `email_login` 删除回归：相关 model / parser / store / prompt 测试移除后套件绿；老 yaml 残留 `email_login` 加载不报错

## 待 plan 细化

- 共享组件 prop 接口精确签名与样式 token 对齐（参考 `SettingsPage` `ThemeSegmented` / `Section`）
- inline accounts 的 id slug 去重边界（多自定义 role 同名）
- `CredentialRows` 在档案 dialog 与黑盒 inline 两场景的布局微调（行宽 / 删除按钮位置）
- PUT 全量 diff 是否需要前端「删除确认」（当前定：编辑态显式删行，无额外确认）

---

下一步：writing-plans 拆分实现计划（待启动）。
