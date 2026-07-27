# WEB 默认密码改密提醒（change-password + 登录后弹窗 + 顶栏 badge）

## 背景
默认账号 admin/123（弱密码）且登录页公开显示。需在登录后提醒用户改密码，并提供改密码能力。

现状（已调研）：
- 后端 `auth/routes.py` 只有 login/logout/me/csrf，**无改密码 API**；`store.py` 无 update_password。
- 前端 **无改密码入口**（SettingsPage 只有主题/系统状态）。
- `login` 响应只返回 `{user:{id,username,role}}`，**无"默认密码"标志**。
- `AuthUser` 类型无 must_change 字段。

## 设计决策
- **标记机制**：users 表加 `must_change_password` 列；`users.yaml` 加同名字段；seed 写入；login 响应带 flag；改密后清 0。配置驱动、不硬编码密码、不依赖"123"字面量。
- **提醒强度**（用户已选）：登录后弹 modal 建议改 + 顶栏持续 ⚠ badge，可跳过不阻塞。
- **改密 API**：`POST /api/auth/change-password`，需登录 + CSRF（已有机制自动注入），body `{old_password, new_password}`。
- **UI 入口**：SettingsPage 加"账户安全"卡片（常驻）+ 登录后 modal + TopBar badge，三者复用同一 `ChangePasswordDialog` 组件。

## 任务

### 后端
1. **store.py**：`_SCHEMA` users 表加 `must_change_password INTEGER NOT NULL DEFAULT 0` 列；`init_schema` 在 CREATE TABLE 之后加幂等 `ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0`（try/except `sqlite3.OperationalError`——列已存则跳过）；`create_user(username, password_hash, role, must_change=False)` 加参数写列；加 `update_password(user_id, new_hash)` 方法（UPDATE password_hash + 置 must_change=0）；`get_user_by_username`/`get_user` SELECT 带上 must_change 列。
2. **models.py**：`User` 加 `must_change_password: bool = False` 字段。
3. **seed.py**：读 yaml `must_change_password` 字段（默认 False），传给 `create_user`。已存在用户不覆盖逻辑不变（改密后 must_change=0 不会被重启重置）。
4. **auth/routes.py**：`_user_out` 加 `must_change_password` 字段；新增 `POST /api/auth/change-password`：`Depends(current_user)` + CSRF 校验 + 校验 old_password 正确（verify_password）+ new_password 非空、≠ old、长度 ≥ 8 + 调 `store.update_password`；返回 `{ok:true}`。失败 400/401。
5. **configs/users.yaml**：admin 项加 `must_change_password: true`。
6. **测试**：`test_auth_seed.py` 加 must_change 字段断言；新增 `test_change_password.py`（old 错拒 401、成功后 hash 变 + must_change 清 0、new==old 拒、长度不足拒、CSRF 校验）；补 login 响应带 flag 断言。

### 前端
7. **AuthContext.tsx**：`AuthUser` 加 `must_change_password: boolean`；login 返回存入 user state；加 `refreshUser()`（改密后刷新 `/auth/me`）。
8. **api/auth.ts**（新文件或并入 client）：加 `changePassword(old, new)` → `apiPost("/auth/change-password", {old_password, new_password})`。
9. **ChangePasswordDialog.tsx**（新组件）：Dialog 含 old/new/confirm 三字段 + 校验（new===confirm）+ 提交调 changePassword；成功后 toast + 调 refreshUser（must_change 置 false，badge/modal 自动消失）。受控 open/onOpenChange。
10. **TopBar.tsx**：若 `user.must_change_password`，UserMenu 旁显示 ⚠ badge 按钮（点击打开 ChangePasswordDialog，本组件持有 open state）。
11. **SettingsPage.tsx**：加"账户安全"卡片，含"修改密码"按钮（打开 ChangePasswordDialog）。
12. **登录后弹窗**：在布局层（App.tsx 或 router layout）用 AuthContext 的 `user.must_change_password` 驱动——登录后若 true，渲染一个 ChangePasswordDialog（open 默认 true，可点"稍后"关闭）。关闭后靠 TopBar badge 持续提醒。
13. **i18n**：zh.json/en.json 加 `auth.changePassword.*`（title/old/new/confirm/submit/mismatch/changed/wrongOld/tooShort/skip）+ `auth.mustChange.banner`（顶栏 badge tooltip 文案）。
14. **测试**：ChangePasswordDialog 新测试（提交校验、成功调 refreshUser、错 old 提示）；TopBar 测试加 must_change badge 渲染；LoginPage 现有测试不受影响。

### 部署
15. **重建 auth.db**：当前 admin 已被上一轮 seed 建入（无 must_change 列，加列后默认 0 不会触发提醒）。最干净：删 `workspaces/auth.db` 重启重 seed（admin 带 must_change=true 重建；当前仅 admin 一用户、无成员记录，删除无损失）。或手动 `UPDATE users SET must_change_password=1 WHERE username='admin'`。
16. `docker compose build web` + `up -d web`；验证：admin 首次登录弹 modal、TopBar 出现 ⚠ badge、改密成功后 badge 消失且重新登录不再弹、错 old 密码被拒、new==old 被拒。

## 风险/注意
- **migration 幂等**：SQLite `ALTER TABLE ADD COLUMN` 无 IF NOT EXISTS，用 try/except OperationalError。
- **seed 不覆盖已存在用户**：改密后 must_change=0，重启 seed 跳过已存在 admin，不会重新置 true。✓（任务 15 处理首次部署的现存 admin）
- **CSRF**：改密 API 经现有 `verify_csrf`（与 login/logout 同），前端 apiPost 自动注入 X-CSRF-Token。✓
- **不改铁律**：本改动全在 web auth 层，不碰双轨/确定性层/LLM 轨。✓
