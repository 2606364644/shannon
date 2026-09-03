# 双浏览器引擎：Playwright / agent-browser

黑盒登录、端点验证和 exploit agent 需要真实浏览器。supernova 通过 `BrowserEngine` Protocol 抽象两套 CLI 型浏览器引擎，使 prompt 和编排层可以互换。

## 当前默认与切换

默认引擎是 **agent-browser**。

选择优先级：

1. `SUPERNOVA_BROWSER_ENGINE`（支持 per-workspace env override）
2. 配置文件 `browser_engine`
3. 默认 `agent-browser`

合法值：

```yaml
browser_engine: agent-browser # 或 playwright
```

黑盒 preflight 会解析引擎并 `check_available()`；不可用则 fail-fast `BROWSER_ENGINE_UNAVAILABLE`，不会静默切换。

## 统一契约

每个引擎必须实现：

- `name` / `cli_binary`
- `session_flag(session_id, proxy_url)`
- `commands_reference()`
- `auth_save_command` / `auth_load_command`
- `write_config(source_dir, session_id, proxy_url)`
- `cleanup_config(source_dir, session_id)`
- `cleanup_processes(source_dir, session_ids)`
- `check_available()`

`PromptManager` 会按当前引擎替换 prompt 中的浏览器命令参考和 session flag，因此同一 agent prompt 不需要感知引擎名。

## Session 隔离

session id 来自 `BROWSER_SESSION_MAPPING`：

- 每个 agent 有稳定 session 名，例如 `agent-injection`、`agent-xss`、`agent-authz`。
- auth validation 固定 `agent1`。
- endpoint verify 未显式映射时回落 `default`。
- 多身份 authz 可派生 `{base}-{account_id}`，但 prompt 强调优先在同一 session 中 state save/load 切换身份，避免浏览器进程爆炸。

并发扫描之间通过 repo/profile 路径和 session id 隔离。prompt 明确要求复用给定 session，不得发明新 session。

## agent-browser

实现：`services/engines/agent_browser_engine.py`，CLI 二进制 `agent-browser`。

特点：

- 命令形态：`agent-browser --session <id> ...`
- 每 session 持久 profile：`.agent-browser/profiles/<session>/`
- 选择器模型：先 `snapshot` 获取 accessibility tree，再使用 `@ref`；不使用 CSS/XPath。
- 内建反检测，无需 stealth.js。
- 状态：`state save/load <path>`。
- 代理：`session_flag` 追加 `--proxy <url>`。
- close：`agent-browser --session <id> close`，禁止并发场景使用 `close --all`。
- 可用性：`shutil.which("agent-browser")`。

扫描启动时会 `AGENT_BROWSER_IDLE_TIMEOUT_MS` 默认设为 300000（5 分钟），除非部署显式配置。这样 agent 结束后 daemon 不再长期占用窗口；后续命令可用 profile 自动恢复认证态。

清理策略：

1. 优先 per-session 优雅 close。
2. close 失败后按 profile 路径精准匹配 Chrome，注意 `agent-auth` 与 `agent-authz` 前缀隔离，并覆盖 identity 变体。
3. daemon 化后的 agent-browser 进程通过残留 Chrome 的 PPID 链定位，确认父进程名后 kill。
4. 全部清理 best-effort，不能反向阻塞或崩溃扫描。

## Playwright

实现：`services/engines/playwright_engine.py`，CLI 二进制 `playwright-cli`。

特点：

- 命令形态：`playwright-cli -s=<session> ...`
- 选择器模型：CSS/XPath 与 CLI 提供的 click/fill/get 命令。
- 配置写入 `.playwright/cli.config[.<session>].json`。
- 每 session 独立 `storageState` 目录。
- 注入 stealth init script：隐藏 `navigator.webdriver`、补 plugins/chrome runtime 等基础反检测。
- 状态：`state-save` / `state-load`。
- 代理：不能通过 CLI flag 注入，必须写入 config 的 `launchOptions.proxy`。
- 可用性：`shutil.which("playwright-cli")`。

`playwright_config_writer.py` 是历史兼容 facade，保留 session mapping 和 `get_session_id` 公共 API，具体配置逻辑委托给 engine。

## 能力差异与选择建议

| 维度 | agent-browser | playwright |
|---|---|---|
| 默认引擎 | 是 | 否 |
| 元素定位 | accessibility `@ref` | CSS/XPath |
| 反检测 | 内建 | stealth script |
| 认证持久化 | profile + state | storageState + state |
| 代理注入 | CLI `--proxy` | config `launchOptions.proxy` |
| 进程模型 | per-session daemon + Chrome | playwright-cli/Chrome |
| 生态倾向 | agent 友好、可访问性树 | 成熟 Playwright 语义 |

两套引擎都必须通过 prompt 中的资源纪律测试：及时 close、不发明 session、多身份优先状态切换。对复杂 SPA，agent-browser 的 accessibility snapshot 通常更贴近模型交互；对既有 Playwright 脚本或强 CSS 选择器场景，playwright 更直观。

## 生命周期与清理

黑盒 workflow 会：

1. 在 exploit agent 启动前写该 agent 的 engine config。
2. agent 成功/失败/异常后立即 `cleanup_processes(session_ids=[该 agent session])`。
3. endpoint verify 和 auth validation 也按阶段回收。
4. 扫描强退路径可粗粒度清理全部 session，但常规路径必须精准清理，避免杀掉并发扫描。

清理返回 `{closed, killed, errors}`；错误只记录，不抛出。

## 验证入口

- `packages/core/tests/test_browser_engine.py`
- `packages/core/tests/test_browser_engine_wiring.py`
- `packages/core/tests/test_agent_browser_engine.py`
- `packages/core/tests/test_playwright_engine.py`
- `packages/core/tests/test_playwright_config_writer.py`
- `packages/core/tests/test_browser_proxy.py`
