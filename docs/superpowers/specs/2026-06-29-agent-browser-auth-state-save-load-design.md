# agent-browser 引擎 auth 路径对齐设计（state save/load）

> 日期：2026-06-29 ｜ 状态：设计（待实现）｜ 分支：feat/fork-py
> 相关：`eb5ff01e`（默认引擎切 agent-browser）、本会话修复的 blackbox workflow sandbox 违规、`configs/moomoo.yaml`

## Context（为什么改）

带 `-c <config>` 跑黑盒时，auth-validation 阶段**必崩**，两种表现：
- `'str' object has no attribute 'get'`（agent 写了双重编码 JSON 字符串）
- `Auth state contains no cookies or origins`（agent 写了语义化总结，无 cookies/origins）

根因是**双引擎 auth 持久化机制不一致**，agent-browser 路径未对齐：

1. **`verify_auth_state`**（`packages/core/src/shannon_core/services/validate_authentication.py:79-80`）期望 Playwright storageState 文件：`parsed.get("cookies")` / `parsed.get("origins")`。
2. **prompt**（`prompts/validate-authentication.txt` 的 `<publish_session>` + `prompts/shared/_shared-session.txt`）指示 agent "用浏览器 session state save/load 命令"保存/恢复到 `{{AUTH_STATE_FILE}}`。
3. **`AgentBrowserEngine`**（`packages/core/src/shannon_core/services/engines/agent_browser_engine.py:124-130`）的 `auth_save_command` / `auth_load_command` **返回空串**（设计上靠 `--profile` 自动持久化，无文件命令），`_COMMANDS_REFERENCE` 也声明 "No explicit save/load commands needed"。

后果：用 agent-browser 时，prompt 要的 "save-state 命令"**不存在** → agent 被迫自己 `write_file` 写语义化总结 → 格式不匹配 verify → 必败。playwright 引擎有 `state-save/state-load`，不受影响。

**更深层**：`--profile` 按 `session_id` 隔离（每个 agent 不同 profile），auth-validation 的登录态**无法被并发 exploit agent 复用**。playwright 靠 `auth-state.json` 共享文件解决（`_shared-session.txt`：所有 agent `load` 它、跳过登录）。

**目标**：让 agent-browser 的 auth 完整 work——auth-validation 登录后导出到 `auth-state.json`，所有 exploit agent 导入复用，verify 校验通过。**保留 `--profile` 运行时隔离（并发安全）。**

## 关键发现（决定方案）

agent-browser CLI **原生有** `state save/load <path>`（`commands.md:316-321`）：

```
agent-browser state save auth.json    # Save cookies, storage, auth state
agent-browser state load auth.json    # Restore saved state
```

与 playwright 的 `state-save/state-load` **一一对应**。agent-browser 官方文档（`references/session-management.md:76`）也明确：`state save/load` / `--state <path>` 用于 "explicit portable JSON file"，并建议 "prefer `--restore` for reusable agent sessions"——但 `--restore` 按 session key 持久化，不解决并发多 agent 共享（见决策记录）。

即：agent-browser **不需要手搓 cookies get/set + storage 组装**——它有原生的 portable state 文件命令。

## 方案

让 `AgentBrowserEngine` 用 `state save/load`，使 auth 走 `auth-state.json` 文件（既定复用机制）。`--profile` 继续管运行时 session 隔离（并发安全），`auth-state.json` 管跨 agent 登录态传递——**两职责正交，两引擎机制统一**。

## 改动

### 1. `AgentBrowserEngine`（`agent_browser_engine.py`）
- `auth_save_command(session, path)`：`""` → `f"state save {path}"`
- `auth_load_command(session, path)`：`""` → `f"state load {path}"`
- `_COMMANDS_REFERENCE` 的 AUTH STATE 段：从 "No explicit save/load commands needed（靠 --profile）" 改为列出 `state save/load <path>` 命令（对齐 `playwright_engine.py` 的 state-save/load 说明）

### 2. prompt 显式注入命令变量（直接修复当前 bug 病灶）
当前 bug 根因：prompt 用泛指 "browser's session state save/load command"，agent-browser 的 commands_reference 没列 → agent 找不到 → 自己 write_file。

- `PromptManager`（`packages/core/src/shannon_core/prompts/manager.py`）新增变量注入：
  - `{{AUTH_SAVE_COMMAND}}` → `engine.auth_save_command(session, AUTH_STATE_FILE)`
  - `{{AUTH_LOAD_COMMAND}}` → `engine.auth_load_command(session, AUTH_STATE_FILE)`
- `prompts/validate-authentication.txt` 的 `<publish_session>`：泛指文字 → 显式 `{{AUTH_SAVE_COMMAND}}`（agent 直接拿到 `state save <path>` / `state-save <path>`）
- `prompts/shared/_shared-session.txt`：泛指文字 → 显式 `{{AUTH_LOAD_COMMAND}}`
- 注：`engine.auth_save_command` 已存在（playwright 用），agent-browser 补完后两引擎统一经此变量注入

### 3. `verify_auth_state`（`validate_authentication.py`）
- **实现首步**：跑一次 `agent-browser --session probe state save /tmp/probe.json` 确认 schema。
- 预期：agent-browser 基于 Playwright，`state save` = Playwright `storage_state()` = `{cookies: [...], origins: [...]}` → verify（L79-80）**不用改**。
- 若 schema ≠ storageState：verify 按 engine 格式分支（engine 提供 `validate_auth_state(parsed)` 方法，或 verify 兼容多种顶层结构）。

### 4. 测试
- engine 单测：agent-browser `auth_save/load_command` 返回 `state save/load`；`commands_reference` 含这些命令；playwright 不回归
- prompt 注入测试：`{{AUTH_SAVE_COMMAND}}` / `{{AUTH_LOAD_COMMAND}}` 对两引擎注入正确命令
- verify 兼容测试（格式确认后按需）

## 不改
- `--profile` 运行时隔离（保留，并发安全）
- playwright 引擎路径（已 work，仅经新变量统一注入）
- `_shared-session.txt` 复用语义（load → 校验 → 跳过登录 / 失败则自登）
- `configs/moomoo.yaml` 的临时 `browser_engine: playwright`（修复后改回 agent-browser 或保留，由用户决定）

## 决策记录
- **选 `state save/load` 文件（方案 A）而非 `--restore` 共享 name**：`--restore` 按 session key 持久化，但 blackbox exploit **并发**跑（xss/ssrf/authz，`asyncio.gather`+信号量），共用 profile/restore-key 在多浏览器进程下冲突。storageState 文件是数据快照，每个 agent load 到自己 session，并发安全。且文件模式是既定设计（`_shared-session.txt`），playwright 已用，agent-browser 对齐即统一。
- **prompt 显式注入命令变量（不靠 commands_reference 让 agent 自己找）**：直接修复 "agent 找不到命令 → 自己 write_file" 病灶，比 "在参考里列出、靠 agent 自己找" 可靠。
- **不手搓 cookies get/set + storage 组装**：agent-browser 有原生 `state save/load`，无需手搓（手搓还有格式摩擦 + agent 组装易错，attempt 1 已示范）。
- **`verify_auth_state` 大概率不用改**：agent-browser 基于 Playwright，`state save` 预期 = storageState。留实现首步确认作为退路。

## 风险
- agent-browser `state save` 文件格式未 100% 确认（dist minified grep 不到）→ 实现首步确认；预期 Playwright 格式，verify 不改；不符则按 engine 分支（已纳入设计）。
- prompt 变量注入需 `manager.py` 配合（新增 2 个 replace）——小改动，注意 `_shared-session.txt` 是 partial（被 `@include` 进各 exploit prompt），变量要在 include 之后替换。
- 真机冒烟待人工（带 `-c` 跑通 auth-validation → exploit 复用）。

## 验证
1. engine/prompt 单测绿
2. 手动：`agent-browser --session probe state save /tmp/probe.json`，确认文件格式（cookies/origins）
3. 真机：`uv run shannon-blackbox start -c <agent-browser config> --repo ... --url ... -w ... --rerun`，确认：
   - auth-validation 过（agent 用 `state save` 产 auth-state.json，verify 通过）
   - exploit agent `state load` 复用登录态（跳过登录，不重复开户/触发风控）
