# 白盒去 browser + browser 进程生命周期清理设计

## 0. 一句话结论

两件事：(A) 白盒扫描**去除全部 browser 依赖**，回归纯静态（在线目标运行时验证是黑盒职责）；(B) 给 browser 进程加**生命周期清理**，覆盖正常结束 / ctrl+c 协作取消 / ctrl+c 强退三条退出路径，根除「扫描关掉后残留一堆 agent-browser + Chrome 孤儿进程」。整体用 TDD（每 task 先红后绿）。两 phase 独立可提交，Phase 1 先行（止血 + 黑盒受益），Phase 2 后行（白盒改造）。

## 1. 背景

### 1.1 现象与根因

用户反馈：白盒扫描 ctrl+c 关掉后，残留大量 `agent-browser` 进程需手动清理。

根因链（代码已查实）：

1. **白盒有意条件性使用 browser**——不是「白盒不用 browser」。`prompts/` 下 5 个白盒 vuln 模板（`vuln-auth/authz/injection/ssrf/xss.txt`）+ `validate-authentication.txt` + `recon.txt` + 各 `*-exploit.txt` **全部注入 `{{BROWSER_COMMANDS}}`**，且该注入在 vuln 模板的 "Available Tools" 清单里是**无条件**（不在 `<if-live>` 块内，见 `vuln-ssrf.txt:95`）。白盒配置分三档：

   | 白盒配置 | 是否启动 agent-browser |
   |---|---|
   | 纯静态（无 web_url、无 auth） | 一般不开，但 setup 仍硬性 `check_available` + prompt 仍带命令 → 设计异味 |
   | 有 web_url、无 auth | vuln/exploit agent 会开 browser 验证 |
   | 有 web_url + auth | 最密集：validate-auth 登录 + 各 vuln/exploit 复用登录态 |

2. **`AgentBrowserEngine` 自身不 spawn 进程**（`agent_browser_engine.py` 只生成 CLI 命令字符串 + 建 profile 目录 + 查 PATH）。真正启动 `agent-browser` 进程的是 **LLM agent 跑 Bash 工具执行注入的命令**——这些是 Python 主进程**不知道 PID 的孤儿子进程**。

3. **现有清理只删文件、不杀进程**：`engine.cleanup_config()` 删 profile 目录、`cleanup_auth_state_sync()` 删 `auth-state.json`，但**没有任何进程级回收**。

4. **ctrl+c 强退是残留最大来源**：`runtime/scan_runner.py::ShutdownController._force_exit` 第 2 次 SIGINT 走 `os._exit(130)`，**完全跳过 Python finally**。任何只挂在 finally 的清理都漏这条路径。

### 1.2 已有基础设施（非从零建）

- **优雅退出框架已存在**：`scan_runner.py` 实现 SIGINT 双击（第 1 次协作式取消、第 2 次 `os._exit(130)` 强退）+ SIGTERM 直接优雅。Phase 1 把进程清理**挂到现有取消流程**，不重建信号处理。
- **黑盒 workflow finally 已有框架**（`workflows.py:431`）：`cleanup_engine_configs` activity + `cleanup_auth_state_sync`，挂点现成，当前只删 config 不杀进程。
- **`agent-browser` 自带优雅关闭**：`agent-browser close`（按 session）/ `close --all`（全关）。清理可优先优雅 close，`pkill` 仅兜底。
- **session 精准隔离可行**：`session_flag` = `--session {sid} --profile .agent-browser/profiles/{sid}`，`BROWSER_SESSION_MAPPING` = `agent1`...`agentN`，进程命令行带 profile 路径 → `pkill -f "agent-browser.*profiles/agent1"` 可精准匹配，不误杀并发扫描。

### 1.3 为什么白盒要彻底去 browser

经确认：白盒的定位是**源码静态分析 + 确定性层**，在线目标的**运行时可利用性验证**是黑盒的职责。白盒当前条件性用 browser（auth 登录、在线目标验证）属职责越界，且带来：进程孤儿清理负担、对在线目标的网络副作用、纯静态场景被 prompt 诱导开 browser 的异味。Phase 2 把白盒收敛回纯静态，auth 配置降级为**纯 prompt 上下文**（辅助静态审计 authz/认证缺陷，不实际登录、不产 auth-state）。

## 2. 方案选择

### 2.1 Phase 1 清理机制（brainstorming 已定）

- **选定：engine `cleanup_processes()` API + 三路径挂载**。
  - 新增 `BrowserEngine.cleanup_processes()` 协议方法，`AgentBrowserEngine` / `PlaywrightEngine` 各实现。
  - 挂三条退出路径：① 各 workflow finally（正常结束 + 协作取消）② `scan_runner._do_cancel` grace 超时后 ③ `ShutdownController._force_exit` 的 `os._exit` 前（同步粗粒度）。
- 否决：只挂 workflow finally——ctrl+c 第 2 次强退 `os._exit` 跳过 finally，残留照旧，不解决核心场景。
- 否决：`run_claude_prompt` setsid 进程组隔离杀子树——claude-agent-sdk / openai-agents 内部 spawn 不可控 + agent-browser 可能 daemon 化脱离进程组，风险高、不可靠。

### 2.2 Phase 2 白盒去 browser 范围（brainstorming 已定）

- **选定：白盒彻底去 browser**（7 接触点，仅限白盒专用模板，见 §4.2.0 归属表）。auth 配置保留为 prompt 上下文、不实际登录。
- 否决：保留白盒在线目标验证——职责越界、与黑盒重叠、且残留根源。
- 否决：只清理不改造——治标，白盒仍会在纯静态场景被 prompt 诱导开 browser。

### 2.3 spec 组织（brainstorming 已定）

- **选定：合一个 spec，分两 phase**。Phase 1 先行（不依赖 Phase 2，黑盒必受益、立即止血），Phase 2 后行（白盒改造）。Phase 2 完成后白盒侧无 browser 进程，Phase 1 清理对白盒成 no-op（但保留兜底）。

## 3. 范围

### 3.1 In scope

- **Phase 1**：`BrowserEngine.cleanup_processes()` 协议 + 两引擎实现；三路径挂载（workflow finally / `_do_cancel` 超时 / `_force_exit` 前）；黑盒 + 白盒 workflow finally 接入（白盒在 Phase 2 去掉 engine 前仍需）。
- **Phase 2**：白盒 7 接触点去除 browser。**只动白盒专用模板**，黑盒专用模板（`*-exploit.txt` / `recon-blackbox.txt` / `validate-authentication.txt`）**绝不动**（见 §4.2.0 归属表）。接触点：workflow setup/finally、`run_auth_validation` activity + 编排、白盒专用 vuln/recon 模板、`_shared-session.txt` 白盒 @include。
- **TDD**：每 task 先写失败测试再实现。
- **铁律测试**：锁定「白盒 prompt 模板不得注入 browser 命令」「白盒 workflow 不得 resolve browser engine」两个不变量。

### 3.2 Out of scope

- 黑盒自身的 browser 使用（黑盒**保留** browser，Phase 1 只给它加进程清理）。
- 黑盒 exploit 闭环的 browser 逻辑改动。
- `agent-browser` / `playwright-cli` 二进制安装与版本管理。
- Temporal worker / 容器层面的进程 reap（依赖 OS，不在本 spec）。
- 「白盒 auth 配置完全忽略」的更激进选项（仅作 prompt 上下文，见 §4.2 边界）。

## 4. 设计

### 4.1 Phase 1：browser 进程生命周期清理

#### 4.1.1 新协议方法 `cleanup_processes()`

`BrowserEngine` Protocol 新增：

```python
def cleanup_processes(
    self,
    source_dir: str | None = None,
    session_ids: list[str] | None = None,
) -> dict:
    """Best-effort 回收 engine 拉起的浏览器进程。

    优先优雅关闭（engine CLI 的 close 命令），失败/残留再 pkill 兜底。
    清理失败一律 log + 吞（不反过来崩扫描）。

    - session_ids 非空：只清理这些 session（精准隔离，不误杀并发扫描）。
    - session_ids 为 None：清理 source_dir profile 下全部 session
      （_force_exit 强退路径用，粗粒度兜底）。

    返回 {"closed": [...], "killed": [...], "errors": [...]} 摘要。
    """
```

**AgentBrowserEngine 实现**：
1. 优雅：对每个 session_id（或 `--all`）跑 `agent-browser close`（带 `--session`/`--profile`），短超时（如 5s）。
2. 兜底：`pkill -f "agent-browser.*profiles/{session_id}"`；再 `pkill -f "headless chrome.*{profile_dir}"`（Chrome 子进程，匹配 profile 的 user-data-dir）。
3. session_ids=None 时跑 `agent-browser close --all` + `pkill -f agent-browser` + `pkill -f "headless chrome"`（粗粒度，仅 `_force_exit` 用）。

**PlaywrightEngine 实现**：对称实现，`playwright-cli` 的 close 语义 + `pkill -f playwright-cli` + Chrome 子进程兜底。

> **实现约束（写进 spec，plan 落实）**：`_force_exit` 路径在 `os._exit` 前调用，**不能 await**——`cleanup_processes` 内部用同步 `subprocess.run`（短 timeout），不用 asyncio。三路径复用同一 `cleanup_processes()`，调用方按场景选同步/异步包装。

#### 4.1.2 三挂载点

| 路径 | 触发 | 调用方式 | 挂载位置 |
|---|---|---|---|
| ① 正常结束 / 协作取消 | workflow finally | async（activity 内） | 白盒 `workflows.py:588` finally、黑盒 `workflows.py:431` finally |
| ② cancel grace 超时 | `_do_cancel` 超时后 | async | `scan_runner.py::_do_cancel` 的 `except TimeoutError` 分支 |
| ③ ctrl+c 强退 | 第 2 次 SIGINT | **同步**（os._exit 前） | `scan_runner.py::ShutdownController._force_exit` 的 `os._exit(130)` 前 |

- 路径 ①：新增（或扩展）`cleanup_browser_processes` activity，参数 `(repo_path, engine_name, session_ids?)`，workflow finally 调用（best-effort try/except）。黑盒可在现有 `cleanup_engine_configs` activity 内**扩展**调 `cleanup_processes`（合并为「清 config + 杀进程」一步），或独立 activity——plan 阶段定。
- 路径 ②：`_do_cancel` 超时分支补一次 `await engine.cleanup_processes(...)`（此时 workflow 可能已僵，进程清理不依赖 workflow 响应）。
- 路径 ③（**核心**）：`_force_exit` 在 `os._exit(130)` 前同步 `engine.cleanup_processes(session_ids=None)`（粗粒度兜底，不依赖 session 精准匹配）。需让 `ShutdownController` 能拿到 repo_path/engine_name——经 `install(loop, repo_path, engine_name)` 传入，或模块级记录。

#### 4.1.3 Phase 1 TDD 任务骨架

每个 task 先写**失败测试**，再实现到绿：

1. **`cleanup_processes` 协议**：`test_browser_engine.py` 断言 Protocol 含 `cleanup_processes`（`hasattr` 或 `_StubEngine` 不实现则 `isinstance(BrowserEngine)` False）。→ 加协议方法。
2. **AgentBrowserEngine 实现**：`test_agent_browser_engine.py` 用 fake `subprocess.run`/monkeypatch 断言：优雅 close 先于 pkill；session_ids 非空只匹配该 session；None 走 `--all`/粗粒度；失败吞掉填 errors。→ 实现。
3. **PlaywrightEngine 实现**：对称测试。→ 实现。
4. **路径 ① activity 接入**：白盒/黑盒 workflow 测试断言 finally 调了 cleanup activity（mock activity 确认调用）。→ 接入。
5. **路径 ② `_do_cancel`**：`test_scan_runner.py` 断言 TimeoutError 分支调了 cleanup。→ 接入。
6. **路径 ③ `_force_exit`**：`test_scan_runner.py` 断言 `os._exit` 前调了同步 cleanup（monkeypatch `os._exit` 防真退出 + 捕获 cleanup 调用）。→ 接入。**这是覆盖用户核心场景的关键测试。**

### 4.2 Phase 2：白盒去 browser

#### 4.2.0 黑白模板归属表（先厘清边界，避免误伤黑盒）

经查实（`AGENTS` 字典 `prompt_template` + 各 workflow 编排）：

| 模板 | 归属 | 谁编排 | Phase 2 动作 |
|---|---|---|---|
| `vuln-{auth,authz,injection,ssrf,xss}.txt`（5） | **白盒专用** | 白盒 `{vt}-vuln` agent（`workflows.py:380`） | **移除 browser 注入 + @include(_shared-session)** |
| `recon.txt` | **白盒专用** | 白盒 `RECON` agent（黑盒用 `recon-blackbox.txt`） | **移除 browser 注入 + @include(_shared-session)** |
| `*-exploit.txt`（auth/authz/injection/ssrf/xss，5） | **黑盒专用** | 黑盒 `{vt}-exploit` agent（`blackbox/workflows.py:281`）；白盒**不跑** exploit | **绝不动**（黑盒 exploit 必须 browser） |
| `recon-blackbox.txt` | **黑盒专用** | 黑盒 `RECON_BLACKBOX` | **绝不动** |
| `validate-authentication.txt` | 黑白共用 | 白盒 `run_auth_validation` + 黑盒 `run_blackbox_auth_validation` 都跑 | 文件**保留**；仅移除白盒 workflow 对 `run_auth_validation` 的编排 |
| `shared/_shared-session.txt` | 黑白共用 include | 白盒 vuln/recon + 黑盒 exploit/recon-blackbox 都 `@include` | 文件**保留**（黑盒仍 include）；仅移除**白盒模板**里的 `@include` 行 |

`_shared-session.txt` 内容 = 纯 auth-state 复用指令（`{{AUTH_LOAD_COMMAND}}` 等，依赖 browser+auth-state）。白盒去掉 auth-state 后此 include 对白盒无意义 -> 移除白盒 @include 正确；文件保留给黑盒。

#### 4.2.1 接触点表（7 项，均限白盒）

| # | 接触点 | 文件 | 改动 |
|---|---|---|---|
| 1 | workflow setup engine resolve/check/write_config | `whitebox/.../workflows.py:108-132` | 删除整段；setup 不再碰 browser |
| 2 | workflow finally cleanup_config/cleanup_auth_state | `whitebox/.../workflows.py:588-590` | 删除（engine=None 后无对象）；Phase 1 的进程清理对白盒成 no-op |
| 3 | `run_auth_validation` activity + 编排 | `whitebox/.../activities.py:738` + `workflows.py:96` | 删除 activity + workflow 编排调用；白盒不 browser 登录、不产 auth-state |
| 4 | 白盒 vuln 模板 browser 注入 | `prompts/vuln-{auth,authz,injection,ssrf,xss}.txt`（5，白盒专用） | 移除 "Browser Automation" 行 + `{{BROWSER_COMMANDS}}` 占位符 + `@include(_shared-session.txt)` |
| 5 | `recon.txt` browser 注入（白盒专用） | `prompts/recon.txt` | 移除 browser 注入 + `@include(_shared-session.txt)` |
| 6 | `_shared-session.txt` 白盒 include | 上述白盒模板的 `@include` 行 | 见接触点 4/5；文件本身不动 |
| 7 | `validate-authentication.txt` | 保留（黑白共用，黑盒仍跑） | 仅确认白盒 workflow 不再触发 `VALIDATE_AUTH` agent |

> **不动清单（铁律）**：`*-exploit.txt` / `recon-blackbox.txt` / `validate-authentication.txt` / `_shared-session.txt` 文件本身--这些归黑盒或黑白共用，移除其 browser 注入会误伤黑盒。

#### 4.2.2 auth 配置的关键边界（已确认）

- **保留**：`config.authentication`（username / login_url / totp_secret / login_flow）作为白盒 vuln agent 的 **prompt 上下文**注入（`{{AUTH_CONTEXT}}` / `{{LOGIN_INSTRUCTIONS}}` 仍渲染），辅助静态审计（如识别 authz 缺失、认证逻辑缺陷）。
- **去除**：不实际登录、不产 `auth-state.json`、不开 browser、不注入 `{{AUTH_SAVE_COMMAND}}`/`{{AUTH_LOAD_COMMAND}}`/`{{BROWSER_COMMANDS}}`。
- **去留划分**：`validate_authentication.py`（核心逻辑 + `auth_state_path` / `cleanup_auth_state` helper）**保留**（黑盒仍用）；白盒只是**不再调用** `validate_authentication`、不再触发 `VALIDATE_AUTH` agent。
- 在线目标的运行时验证 → **黑盒**。

#### 4.2.3 prompt_manager 处理

- browser 占位符替换逻辑（`manager.py:104-105` `{{BROWSER_COMMANDS}}`/`{{BROWSER_SESSION_FLAG}}`、`:110-121` AUTH_*）**保留**（黑盒用）。
- 白盒模板不再含这些占位符 → 对白盒 no-op，核心逻辑不动。`_interpolate` 末尾的残留占位符检测会确认白盒模板无残留 browser 占位符。

#### 4.2.4 Phase 2 TDD 任务骨架

1. **铁律测试 1**（先红）：`test_whitebox_no_browser_in_prompts` 断言**白盒专用模板**（`recon.txt` + 5 个 `vuln-*.txt`，见 §4.2.0 归属表）**不含** `{{BROWSER_COMMANDS}}`/`{{BROWSER_SESSION_FLAG}}`/`@include(shared/_shared-session.txt)`/agent-browser 字样。**反向断言**锁定黑盒专用模板（`*-exploit.txt`/`recon-blackbox.txt`/`validate-authentication.txt`）**仍含** browser 占位符，防回归误删。-> 改白盒模板到绿。
2. **铁律测试 2**（先红）：`test_whitebox_workflow_no_browser_engine` 断言白盒 workflow 不 resolve/check/write_config browser engine（grep workflow 源码无 `BrowserEngineFactory`/`check_available`/`write_config`）。→ 改 workflow 到绿。
3. 接触点 1-3：workflow setup/finally/activity 移除——测试断言白盒 PipelineInput 无 auth validation 编排（`run_auth_validation` 不在调用链）。→ 改到绿。
4. 接触点 4-6：模板改动由铁律测试 1 覆盖（含 `@include(_shared-session.txt)` 移除）。
5. 回归：现有白盒 workflow 测试（`test_workflows.py`）需更新断言（移除 engine/auth-validation 相关 mock）。

## 5. 错误处理

- `cleanup_processes()` 全程 best-effort：每步 try/except，失败 log + 填 `errors`，**绝不抛**（清理不能反过来崩扫描/阻塞退出）。
- 路径 ③ `_force_exit` 同步清理：设硬超时（如 `subprocess.run(..., timeout=8)`），超时则放弃（已 `os._exit`，进程终被容器/OS 回收，但至少尽力优雅 close）。
- Phase 2：白盒移除 auth validation 后，带 auth 配置的白盒扫描**不再 fail-fast**（原 `validate_authentication` 失败会阻断 setup）——auth 仅作上下文，无 runtime 失败路径。需更新对应测试断言。

## 6. 不变量 / 铁律（测试锁定）

1. **白盒 prompt 模板不得注入 browser 命令**——`test_whitebox_no_browser_in_prompts` 锁定（类比 `static-dataflow-hints` 解耦铁律）。
2. **白盒 workflow 不得 resolve/check browser engine**——`test_whitebox_workflow_no_browser_engine` 锁定。
3. **`cleanup_processes` 是 best-effort、永不抛**——测试覆盖失败路径。
4. **`os._exit` 前必有同步进程清理**——`test_scan_runner.py` 路径 ③ 测试锁定。

## 7. 顺序与依赖

```
Phase 1（止血 + 黑盒，不依赖 Phase 2）
  ├─ cleanup_processes 协议 + 两引擎实现（TDD 1-3）
  ├─ 三路径挂载（TDD 4-6）          ← 独立可提交
  └─ 黑盒 + 白盒 workflow finally 接入
Phase 2（白盒改造，依赖 Phase 1 的清理兜底已就位更稳，但非硬依赖）
  ├─ 铁律测试 1-2（先红）
  ├─ 接触点 1-3（workflow/activity）
  ├─ 接触点 4-7（模板）
  └─ 回归测试更新                    ← 独立可提交
```

- Phase 1 完成即解决用户的核心痛点（ctrl+c 残留），黑白盒都受益。
- Phase 2 完成后白盒无 browser 进程，Phase 1 清理对白盒成 no-op，但保留兜底（防御未来回退或边界场景）。
- 两 phase 可独立 PR。

## 8. 待 plan 验证 / 风险

- **agent-browser 进程命令行是否真带 profile 串**（pkill -f 可匹配）——plan 阶段用 `ps` 实测确认；若 daemon 化脱离命令行匹配，fallback 走进程树（PPID 链）或 `agent-browser close --all` 粗粒度。
- **并发扫描互不误杀**：session_ids 精准隔离是前提，需测试覆盖两并发扫描清理不互扰。
- **`_force_exit` 传参**：repo_path/engine_name 如何传到 `ShutdownController`（经 `install` 参数 or 模块级）——plan 定具体接线。
- **Phase 2 对白盒检出率影响**：失去在线目标运行时验证，但白盒本应以静态为主；真机冒烟验证关键 vuln 类（xss/ssrf）静态判定未退化。
- **`SHANNON_BROWSER_ENGINE` env**：Phase 2 后白盒忽略此 env（白盒不碰 engine）；黑盒仍读。需文档化。

## 9. 相关

- 设计依据：`docs/superpowers/specs/2026-06-15-graceful-shutdown-design.md`（现有优雅退出框架，本 spec 复用其 `ShutdownController`）。
- CLAUDE.md §1 双轨、§2 双引擎（本 spec 不动双轨/双引擎铁律）。
- 记忆 `blackbox-live-display-status`（黑盒 graceful-shutdown 未接的同源问题，Phase 1 顺带补）。
- 实现用 TDD（项目既有工作流，如 `pre-recon iter_calls` 缓存修复 `155e802c`）。
