# 认证档案系统级 seed：configs/*.yaml → 全局共享档案 设计

> 配对 plan：`plans/2026-08-06-auth-profile-system-seed.md`（待写）
> 上游 spec：`specs/2026-08-05-auth-profile-vault-design.md`（per-ws 档案库基础，本文为增量）

## 1. 背景与动机

### 1.1 现状
认证档案库（AuthProfileStore，`packages/web/src/supernova_web/components/auth_profile_store.py`）是 **per-workspace** 的：每个 ws 一份 `workspaces/<ws>/auth-profiles.yaml`（Fernet 字段级加密）。

`configs/*.yaml`（`futunn.yaml`/`moomoo.yaml` 等黑盒登录配置）与认证档案库是**两个互不相交的世界**：
- configs 只被 CLI 路径消费（`shannon-blackbox start -c configs/xxx.yaml` → core `parse_config`）。
- 认证档案库只被 web 路径消费（ProfilePicker → `credential_to_authentication` → 明文 `scan-config.yaml` → worker）。
- web 端无法复用 configs 里已写好的登录配置，每次黑盒扫描都要在表单手填。

### 1.2 目标
启动时自动把 `configs/*.yaml` 里的 `authentication` 段 seed 成**系统级档案**，所有 workspace 共享可见、可选用，但**只读**（以 configs 文件为唯一真相源）。futunn/moomoo 等配置在 web 端 ProfilePicker 里直接可选，不必重建。

### 1.3 已定策略（用户）
- 触发：**启动自动 seed**（app lifespan 扫 configs/）。
- 归属：**全局共享**（引入系统级档案层，所有 ws 可见，非每个 ws 各一份）。
- 冲突：**跳过已存在**（`.system` 内同名档案不覆盖）。

## 2. 核心设计：store 层透明 fallback + `.system` 保留段

最小侵入的关键是 **`store.get(ws, id)` 做透明 fallback**：先查 ws 档案，miss 查系统档案（`.system`）。这让 scan_manager 的 resolve 链路（`_resolve_blackbox_inputs` 单/多角色 + `start_auth_validation`）**零改动**自动支持系统档案。

系统档案存 `workspaces/.system/auth-profiles.yaml`，复用现有 store 全部机制（同 `CredentialVault` 加密、同 `upsert_profile`/`write` 落盘）。

### 2.1 不变量
- `store.get(ws, id)`：ws 档案优先，miss → 系统。系统档案对所有 ws 可见。
- 系统档案**只读**：web 端 PUT/DELETE/POST 对 scope=system 拒 403。
- `.system` 是保留 workspace 段，用户不可创建（沿用项目既有 dot-dir 跳过约定：`app.py:144`、`repo_manager.py` 多处）。
- seed 按 **name 去重**：`.system` 内已有同名则跳过，不覆盖。

### 2.2 数据模型增量
`AuthProfile` 加一个字段（向后兼容，旧档案默认 workspace）：

```python
scope: Literal["workspace", "system"] = "workspace"
```

系统档案 `scope="system"`；ws 档案 `scope="workspace"`。前端据此渲染来源徽章 + 只读。

## 3. 详细设计

### 3.1 AuthProfileStore 改造（`auth_profile_store.py`）

**(a) `.system` 保留段**
- 常量 `SYSTEM_WS = ".system"`。
- `_path()` 对 `.system` 放行：当前 `_validate_ws_segment` 已允许（非空 / 不含 `/` / 非 `.`/`..`）。
- `write()` 已有 `mkdir(parents=True, exist_ok=True)`，自动建 `.system` 目录。

**(b) `get(ws, id)` 透明 fallback**
```python
def get(self, ws, profile_id):
    for p in self.read(ws):               # ws 档案
        if p.id == profile_id:
            return p
    for p in self.read(SYSTEM_WS):        # miss → 系统
        if p.id == profile_id:
            return p
    return None
```
所有 `store.get` 调用方（scan_manager 三处）自动支持系统档案，不改。

**(c) `read(ws)` / `read_masked(ws)` 合并系统档案**
- 返回 ws 档案（scope=workspace）+ 系统档案（scope=system）。
- read_masked 对系统档案同样脱敏（GET 响应不泄明文密码）。
- list_profiles / get_profile 天然透传 scope。

**(d) `seed_from_config(configs_dir: Path) -> int`**
- 扫 `configs/*.yaml`，排除 `web-multi-*`（multi-repo 配置，非登录配置）、`users.yaml*`（凭据）。
- 用 core `parse_config`（`packages/core/src/supernova_core/config/parser.py:196`）解析，取 `config.authentication`。
- 无 `authentication` 段 / parse 失败 → 跳过 + warn，不阻断。
- 映射成 AuthProfile（见 §3.4），跳过 `.system` 内同名，`upsert_profile(SYSTEM_WS, ...)` 加密落盘。
- 返回 seed 数。

**(e) `set_verify_status` 对称化**
- 当前写回传入的 ws。对系统 profile 会在 ws 创副本——**缺陷**。
- 改：先 resolve profile 的 source（ws 命中→ws，否则→`.system`），写回原 source。

### 3.2 启动 seed（`app.py` lifespan）
- lifespan 内、`reap_stale_probes()` 前（与其它 `_migrate_*`/`_reconcile_*` 并列）。
- 调 `app.state.auth_profile_store.seed_from_config(app.state.config.configs_dir)`，log seed 数。
- configs/ 已挂载进 web 容器（`docker-compose.yml:42`），无需改 compose。

### 3.3 只读守卫（`api/auth_profiles.py`）
- `update_profile` / `delete_profile` / `create_profile`：对 scope=system 的 pid 拒 403（"系统档案只读，请修改 configs 文件"）。
- 检测：`store.get(ws, pid).scope == "system"`。

### 3.4 `.system` 保留名防御（`api/workspaces.py` + `workspaces_indexer.py`）
- `create_workspace`：拒 `.` 开头的 ws 名（含 `.system`），422。
- `workspaces_indexer.list_workspaces`：跳过 `.` 开头目录（防 out-of-band 创建的 `.system` 出现在 UI），对齐 `app.py:144`。

🔴 **严重**：保留名防御是安全前提——若漏配，用户可创建 `.system` ws 与系统档案路径碰撞，或 `.system` 出现在 ws 列表造成混淆。必须与 store 改造同批落地。

### 3.5 前端（只读 + 来源标记）
- `api/types.ts`：`AuthProfile` 加 `scope?: "workspace" | "system"`（向后兼容）。
- `ScanFormFields.tsx`（ProfilePicker）：档案卡按 scope 渲染"系统/共享"徽章；选用逻辑不变。
- `AuthProfilesPage.tsx`：scope=system 行隐藏 Edit/Delete 按钮。
- `AuthProfileDialog.tsx`：system scope 全字段只读 + 隐藏提交。
- CredentialRow：系统档案"测试登录"保留（fallback 自动生效），编辑禁用。

### 3.6 scan_manager：零改
`_resolve_blackbox_inputs`（L354/372）+ `start_auth_validation`（L482）靠 `store.get` 透明 fallback 自动生效，不改。`set_verify_status`（L590）的对称化在 store 层（§3.1e）处理，scan_manager 调用不变。

## 4. 字段映射（yaml → AuthProfile）

| yaml `Authentication` | AuthProfile |
|---|---|
| login_type / login_url / login_flow | 直接（枚举一致） |
| 单 `credentials{username,password,totp_secret,email_login}` | `credentials=[{role:"primary", ...}]` |
| — | `verify_status=unverified`, `scope="system"` |
| 文件 stem | `name`（futunn/moomoo） |
| `success_condition` | 丢弃（死字段，全代码无消费者） |

role 默认值 `"primary"`（seed 的单角色档案）。name 用文件 stem；与 `.system` 内现有同名比较决定跳过。

## 5. 测试策略

### 5.1 store 单测（`test_auth_profile_store.py`）
- `seed_from_config` 解析 configs/futunn.yaml → 系统 AuthProfile（name=futunn, role=primary, scope=system）。
- 跳过已存在同名（二次 seed 不覆盖、不重复）。
- `get(ws, id)` fallback：ws 无、系统有 → 返回系统档案（scope=system）。
- `get(ws, id)` ws 优先：ws 有同 id → 返回 ws 档案。
- `read(ws)` 合并：返回 ws + 系统档案。
- `set_verify_status` 对系统 profile 写回 `.system`（不在 ws 创副本）。
- 无 authentication 段 / parse 失败的 yaml 跳过不崩。

### 5.2 API 单测
- `test_api_workspaces.py`：拒 `.` 开头 ws 名（`.system`/`.foo`）。
- `test_api_auth_profiles.py`：PUT/DELETE 系统 pid → 403。

### 5.3 前端单测
- ProfilePicker 渲染系统徽章；AuthProfilesPage system 行无 Edit/Delete。

## 6. 端到端验证

1. `docker compose up --build web` 启动 → log "seeded N auth profiles from configs"。
2. 任意 ws 的认证管理页 / 扫描页 ProfilePicker → 见 futunn/moomoo（系统徽章、只读）。
3. 选 futunn 系统档案发起黑盒扫描 → `scan-config.yaml` 正确生成（fallback 生效）。
4. "测试登录"系统档案 → verify_status 写回 `.system`，ws 无副本。
5. PUT/DELETE 系统档案 → 403。
6. 创建 `.system` / `.foo` ws → 拒绝。

真机前提：web 镜像 rebuild（后端+前端）；worker 零改（读 web 写的明文 scan-config.yaml）；configs/ 已挂载。

## 7. 不做（明确排除）

- **不监听 configs/ 变更自动重导**（watchdog）：运行态副作用复杂，重启 seed 已够。
- **不 seed 到每个 ws**：用户选了全局共享，避免重复。
- **不让 web 端编辑系统档案**：configs 文件是唯一真相源，改 configs + 重启。
- **不动 worker**：它读明文 scan-config.yaml，与档案来源无关。
- **不处理 success_condition**：死字段，丢弃。

## 8. 待 plan 确认项

- **role 默认值**：seed 单角色档案用 `"primary"`（还是 `"admin"`/`"default"`？）。倾向 `primary`（中性，与多身份 tier 推导语义不冲突）。
- **无 authentication 段的 configs 文件**：当前是跳过。是否 log 一行提示？（倾向：DEBUG 级，不噪音）。
- **`set_verify_status` 对称化的实现**：在 store 内 resolve source（推荐，调用方零改）vs scan_manager 传入 source 标记。倾向前者。
