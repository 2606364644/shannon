# 黑盒扫描：默认浏览器引擎切 agent-browser + 引擎无关化修复

- 日期：2026-06-28
- 分支：feat/fork-py
- 状态：设计已批准，待实现

## 背景与问题

对比原始 TS 项目（`/Users/mango/project/shannon-refactor/shannon`）与 shannon-py 的黑盒扫描，浏览器引擎层架构**对齐**（宿主代码只生成配置 + prompt 指令，真正浏览器操作由 LLM agent 在 shell 调外部 CLI 完成）。shannon-py 已把 TS 的单一 playwright 演进为可插拔双引擎（`BrowserEngine` Protocol + `BrowserEngineFactory`，注册 `playwright` / `agent-browser`）。

但发现 3 处引擎切换的遗留耦合点：

- **P1（bug）**：CLI 前置闸口 [main.py:131](../../../packages/blackbox/src/shannon_blackbox/cli/main.py) 硬编码 `ensure_prerequisite("playwright-cli", ...)`，无视 `browser_engine` 配置。agent-browser 模式下若本机未装 playwright-cli 会被误挡（或触发错误的降级引导）。配套 `scripts/bootstrap.sh` 的 blackbox profile 只装 playwright-cli + chromium，缺 agent-browser 安装链路。
- **P2（默认值）**：三处默认引擎仍是 `"playwright"`（[config.py:73](../../../packages/core/src/shannon_core/models/config.py)、[workflows.py:118](../../../packages/blackbox/src/shannon_blackbox/pipeline/workflows.py)、[manager.py:98](../../../packages/core/src/shannon_core/prompts/manager.py)）。用户意图是默认用 agent-browser。
- **P3（cosmetic）**：进度格式化 [formatters.py:128](../../../packages/core/src/shannon_core/display/formatters.py) 的 `maybe_browser_command` 正则只识别 `playwright-cli` 命令，agent-browser 命令不会渲染成 emoji 浏览器短语。

## 设计决策（已与用户确认）

1. **保留双引擎，改默认为 agent-browser**。`BrowserEngineType` Literal 不变（仍 `["playwright", "agent-browser"]`），`PlaywrightEngine` 及其测试保留作 fallback。仅改默认值。
2. **一并给 agent-browser 加 bootstrap 安装**，完整修复 P1 安装链路。
3. **P1a 用 `cli_binary` 属性**（而非 CLI 闸口内 name→binary 字典）让引擎自描述 binary 名，与 Protocol 抽象一致、未来加引擎零改 CLI。

## 详细设计

### P2 — 默认引擎改 agent-browser

仅改 3 处默认值，双引擎抽象/注册/Literal 全部不动：

- [config.py:73](../../../packages/core/src/shannon_core/models/config.py)：`browser_engine` 默认 `"playwright"` → `"agent-browser"`
- [workflows.py:118](../../../packages/blackbox/src/shannon_blackbox/pipeline/workflows.py)：无 config 时 fallback `"playwright"` → `"agent-browser"`
- [manager.py:98](../../../packages/core/src/shannon_core/prompts/manager.py)：`variables.get("browser_engine", "playwright")` → `"agent-browser"`

`parser.py:212` 的 `SHANNON_BROWSER_ENGINE` env override 不动（与默认值无关）。

### P1a — 引擎自描述 binary 名

给 `BrowserEngine` Protocol（[browser_engine.py](../../../packages/core/src/shannon_core/services/browser_engine.py)）新增 `cli_binary` 只读属性：返回该引擎在 PATH 上要 `which` 检查的 binary 名。

- `PlaywrightEngine.cli_binary` → `"playwright-cli"`（注意：与 `name="playwright"` 不同）
- `AgentBrowserEngine.cli_binary` → `"agent-browser"`

两引擎的 `check_available()` 已用对应 binary 做 `shutil.which`，本次把 binary 名提升为 Protocol 公共属性，闸口与 check 共用同一真值源。

### P1b — CLI 闸口引擎无关化

[main.py:131](../../../packages/blackbox/src/shannon_blackbox/cli/main.py) 改为按解析出的引擎检查对应 binary：

```python
from shannon_core.services.browser_engine import BrowserEngineFactory
engine_name = BrowserEngineFactory.resolve_name(input.config_path)
engine = BrowserEngineFactory.get_engine(engine_name)
ensure_prerequisite(engine.cli_binary, profile="blackbox")
```

新增 `BrowserEngineFactory.resolve_name(config_path=None) -> str`，解析优先级：

1. `SHANNON_BROWSER_ENGINE` env（最高，对齐 `parser.py:212` 语义）
2. `parse_config(config_path).browser_engine`（若 config_path 提供）
3. 默认 `"agent-browser"`

`ensure_prerequisite` 本身（软检查 + bootstrap 交互安装 + 降级退路）逻辑不动，仅调用方传正确的 binary。

> 范围克制：workflow / manager 现有 engine name 解析保持原样（已正确工作），不强行收敛到 `resolve_name`，避免范围蔓延。

### P1c — bootstrap.sh 加 agent-browser 安装

[scripts/bootstrap.sh](../../../scripts/bootstrap.sh) 仿 `install_playwright_cli` 新增 `install_agent_browser()`：

```
npm install -g agent-browser
agent-browser install   # 下载配套 Chrome
```

blackbox profile（[bootstrap.sh:130](../../../scripts/bootstrap.sh)）改为装 `playwright-cli` + `chromium` + `agent-browser` 三个——保留双引擎意味着两者都能装，fallback 才真正可用。

### P3 — 显示层加 agent-browser 分支

[formatters.py:128](../../../packages/core/src/shannon_core/display/formatters.py) 的 `maybe_browser_action` 增加 agent-browser 正则分支。agent-browser 命令形如 `agent-browser --session s1 open <url>` / `click @e5`（session flag `--session <id>` 在前、子命令在后，与 playwright-cli 的 `-s=<id>` 不同），需独立正则分支，映射到相同 emoji 短语（🌐 navigate / 🖱️ click / 等）。

## 测试策略（TDD，先红后绿）

| 测试文件 | 验证 |
|---|---|
| `test_browser_engine.py` | `BrowserEngine.cli_binary`：playwright→`playwright-cli`、agent-browser→`agent-browser`；`resolve_name` 三级优先级（env > config > 默认 agent-browser） |
| `test_prompt_manager.py` | P2 默认渲染：[test_prompt_manager.py:409](../../../packages/core/tests/test_prompt_manager.py) 默认断言同步改 agent-browser |
| `test_workflows.py` | P2 fallback：[test_workflows.py:183-185](../../../packages/blackbox/tests/test_workflows.py) 默认断言同步改 agent-browser |
| `test_formatters.py` | P3：`maybe_browser_action` 加 agent-browser navigate/click 用例 |
| `test_browser_engine_wiring.py`（回归） | env override 显式配 playwright 保持绿，证明双引擎都在 |

显式测 playwright 引擎本身的测试（`test_playwright_config_writer.py`、`test_agent_browser_engine.py` 里 `assert engine.name == "playwright"` 等）**保留不动**——playwright 引擎未删。

按 CLAUDE.md「只跑改动相关测试」：跑 `test_browser_engine*.py` / `test_prompt_manager.py` / `test_formatters.py` / blackbox `test_workflows.py` 子集，不跑全套。

## 范围与非目标

- **不做**：重构 workflow/manager 现有 engine name 解析（已正确工作）。
- **不做**：删除 playwright 引擎及其测试（用户选择保留双引擎）。
- **不做**：触碰白盒双轨不变量（CLAUDE.md §1，与黑盒浏览器引擎无关）。

## 实现时核实项

- ✅ agent-browser npm 包名 = `agent-browser`，安装 `npm install -g agent-browser` + `agent-browser install`（来源 npmjs.com/package/agent-browser，vercel-labs 出品）。
- 实现后用 `validate_*_task_probe.py` 类探针在对应引擎实测（CLAUDE.md §2），但本修复不改变 LLM agent 行为，主要靠单元测试 + 真机黑盒冒烟验证。

## 风险

- 改默认值后，**已部署环境若无 agent-browser**，默认配置会触发降级引导（P1c 修复后 bootstrap 可装）或 workflow `check_available` 硬失败。属预期行为（agent-browser 现在是一等引擎）。
- bootstrap.sh 加装 agent-browser 会增加 blackbox profile 安装时长与磁盘（多下一个 Chrome）。可接受。
