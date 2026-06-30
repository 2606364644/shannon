# 黑盒 recon 登录态接线设计（executor 基层统一注入）

> 日期：2026-06-30 ｜ 分支：feat/fork-py ｜ 状态：设计待审

## 1. 背景与问题

黑盒 `BlackboxScanWorkflow` 的登录态（auth state）复用存在**移植遗漏**：

- **auth 阶段**（`validate-authentication`）登录成功后把 storageState 保存到 `<workspace>/auth-state.json` ✓
- **exploit 阶段**：`exploit_executor.py:65` 显式传 `prompt_variables["AUTH_STATE_FILE"]`，经 `prompts/shared/_shared-session.txt` partial + manager 注入 `state load` 命令，复用登录态 ✓
- **recon 阶段**：`recon_executor.py` **连 `prompt_variables` 都没传** → manager 无 `AUTH_STATE_FILE` → `<shared_authenticated_session>` block 被 strip/留空 → **recon 浏览器裸跑，不带登录态** ✗

`prompts/recon-blackbox.txt:16` 已 `@include(shared/_shared-session.txt)`（结构对齐 TS），但 executor 接线漏了——prompt 有 block、没喂数据。

## 2. TS 基线（决定性依据）

CLAUDE.md 铁律：黑盒"保持与原始 TS `/root/shannon` 一致"。TS 基线：

- `/root/shannon/apps/worker/prompts/recon.txt:37` `@include(shared/_shared-session.txt)` —— recon prompt 含登录态 partial
- `agent-execution.ts:133` `AUTH_STATE_FILE: authStateFile(...)` —— **统一注入，不区分 agent 类型**
- recon 与 exploit（除测认证绕过的 `exploit-auth`）**完全对称，都加载登录态**

→ PY recon 不带登录态是**移植遗漏**，不是设计选择。

## 3. 设计：executor 基层统一注入（方案 B）

对齐 TS `agent-execution.ts:133` 的"统一注入"架构——在 `AgentExecutor.execute`（core 共享层）构建 variables 时统一注入 `AUTH_STATE_FILE`，所有 agent 自动有；而非各 executor 分别传（正是 recon 漏接的同一根因）。

### 3.1 核心改动

**`packages/core/src/shannon_core/agents/executor.py`** —— variables 构建（约 line 79-84）追加：

```python
from shannon_core.services.validate_authentication import auth_state_path
...
variables = {
    "web_url": web_url,
    "repo_path": str(repo),
    "deliverables_path": str(deliverables),
    "scratchpad_path": str(deliverables.parent / "scratchpad"),
    # 统一注入 auth-state 路径（对齐 TS agent-execution.ts:133）。
    # workspace_path = deliverables.parent（见 §3.3 路径一致性）。
    # 仅"有 auth 配置 + prompt include shared-session partial"的 agent 生效；
    # 其余场景 manager strip block，完全 no-op（见 §4）。
    "AUTH_STATE_FILE": str(auth_state_path(deliverables.parent)),
}
```

### 3.2 配套清理：移除 exploit_executor 显式注入（单一来源）

**`packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`**：

- 移除 line 65 `prompt_variables["AUTH_STATE_FILE"] = ...`（基层已统一注入，避免双来源）
- 移除 line 15 `from shannon_core.services.validate_authentication import auth_state_path`（变未使用）
- 保留 `execute()` 的 `workspace_path` 参数（签名稳定，不改调用方 `activities.py:225`）；移除上述注入后该参数不再被使用，可接受（ruff 不报未使用的函数参数）

### 3.3 路径一致性（已验证）

| 路径 | 来源 | 值 |
|---|---|---|
| auth save | `auth_state_path(input.workspace_path)`（`activities.py:96`） | `<ws_root>/<session>/auth-state.json` |
| executor 注入 | `auth_state_path(deliverables.parent)` | `<ws_root>/<session>/auth-state.json` |

`deliverables.parent ≡ input.workspace_path`：`resolve_deliverables_path`（`utils/paths.py:53`）返回 `ws_root/<session>/deliverables`，`.parent` = `ws_root/<session>` = `input.workspace_path`（`workflows.py:79`）。黑盒带 `-w` 必走 workspace_name 分支；repo_path 兜底分支亦与 workspace_path 兜底（`workflows.py:81`）一致。**save/load 落同一文件**。

> 注：此一致性此前连 exploit 都未真机验证（memory `agent-browser-auth-state-save-load-status` 记"真机冒烟待人工"）；本次设计经静态推导确认等价，真机冒烟仍需人工。

## 4. no-op 安全性（关键）

基层注入影响所有 agent（含白盒），必须对非 auth 场景 no-op。逐层验证：

- **白盒 / 无 `-c`**：`manager.py:167` `if not (config and config.authentication)` **strip 整个 `<shared_authenticated_session>` block** → 注入 AUTH_STATE_FILE 但无 block 消费 → no-op
- **白盒 prompt 不 include 该 partial** → 即使不 strip 也无 `{{AUTH_LOAD_COMMAND}}` 占位符可替换 → 双重 no-op
- **黑盒 `-c`**：block 保留 → manager（`manager.py:109-118`）注入 `state load` → recon + exploit 都加载

## 5. YAGNI（不做什么）

- **不补 recon 的 `browser_session_id`**：`manager.py:90-95` 对空值有 fallback（`BROWSER_SESSION_MAPPING.get(template, "agent1")`），recon 总有可用 session；`state load` 灌入该 session 即带登录态。session 隔离规范化是独立可选优化，不属本 spec。
- **不改 `recon-blackbox.txt`**：已 include partial。
- **不新建 yaml**：moomoo SSO cookie domain `.moomoo.com` 跨子域，`invite-code.moomoo.com` 共享主站登录态。

## 6. 边界与风险

- **auth-state.json 不存在**（auth 未跑/失败）：正常流程 auth 成功才进 recon（auth-validation 失败则 workflow 终止），文件必存在。与 exploit 对称，不额外兜底。
- **stale session**：`_shared-session.txt` partial 已含 load 后 verify + fall-through 逻辑。但 `recon-blackbox.txt` 无 `{{LOGIN_INSTRUCTIONS}}`，recon 无登录流可 fall through——load 失败则裸跑探公开攻击面。**可接受**（recon 角色是探测，非认证测试；recon 不应自己执行登录）。
- **白盒测试套件**：注入变量不被未 include 的 prompt 消费（理论 no-op），但须跑白盒 prompt 相关测试确认无回归（见 §7.5）。

## 7. 测试策略

1. **守卫：executor 基层注入 AUTH_STATE_FILE**（防回归）——单测 `AgentExecutor.execute` 的 variables 含 `AUTH_STATE_FILE = <deliverables.parent>/auth-state.json`
2. **no-op：白盒/no-auth 场景 manager strip block**——渲染白盒 agent prompt（无 config.authentication），断言不含 `state load` / `<shared_authenticated_session>`
3. **recon 渲染含 state load**（有 auth 配置）——渲染 `recon-blackbox` prompt（带 config.authentication），断言含 `state load <...>/auth-state.json`
4. **路径一致性锁定**——断言 `resolve_deliverables_path(...).parent == input.workspace_path`，锁定 §3.3 隐含约定（防未来 deliverables 结构变更悄悄破坏）
5. **白盒 prompt 测试无回归**——跑现有白盒 prompt 渲染相关测试，确认注入不破坏

## 8. 改动文件清单

- `packages/core/src/shannon_core/agents/executor.py`（注入 `AUTH_STATE_FILE` + import auth_state_path）
- `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`（移除显式注入 line 65 + import line 15）
- 测试：新增/补充（§7.1–7.4），跑现有（§7.5）

## 9. 不变量

- 双轨独立性、白盒/黑盒可分开执行——不受影响（注入对白盒 no-op）
- TS 对齐——本设计正是补齐 TS `agent-execution.ts:133` 的统一注入
- recon/exploit 登录态对称——修复后两者都经基层统一注入，结构一致
