# 治本设计：恢复 TS host 渲染的 deliverable 机制（多工具 collector，双引擎）

> 日期：2026-07-17 ｜ 分支：feat/fork-py ｜ 关联 memory：[[pre-recon-md-deliverable-glm-forget-write]]、[[whitebox-exploitation-queue-persist-status]]

## 1. 背景与根因

2026-07-16 NodeGoat（GLM）pre-recon 跑 13min/Turn147 后报 `Missing deliverable: pre_recon_deliverable.md [OutputValidationError]`，扫描卡死。

**根因（查 `upstream/main` TS 推翻前推断）：重构把 TS 的「host 渲染」产物架构换成了「agent 自己 Write」。**

- **原始 TS**（`apps/worker/`）：所有 agent（pre-recon/recon/vuln/exploit）的 md deliverable **都不靠 agent Write**——agent 调一组 `set_*` 结构化工具，**host 的 renderer 确定性渲染**成 md。collector 注释明文：*"A skipped tool renders a placeholder rather than failing the activity"*——agent 漏调工具也渲染 placeholder、**不让 activity 失败**。TS prompt 原文：*"there is no Markdown for you to write yourself"*。
- **重构 PY**：prompt 改成「MUST save ... using the Write tool」，agent 自己 Write 整个 md，executor 只校验存在性、不渲染、不兜底。丢掉了 host 渲染兜底 + 容错，产物可靠性从「代码保证」降级为「agent 自觉」。
- **触发**：GLM 长任务 + 子代理委派 fan-out 后失忆，`end_turn`(success=True) 但没执行 Write → Missing deliverable。Claude 概率低但机制上仍有风险。

**结论**：这是**架构差异**，不是模型差异。TS 架构上不可能 Missing deliverable（host 一定渲染）。

## 2. 治本目标

恢复 TS host 渲染架构：agent 调结构化工具 → host 收集 + 确定性渲染 md。1:1 对齐 TS（CLAUDE.md §1 铁律：保持与原始项目一致），双引擎可互换（CLAUDE.md §2）。

## 3. 关键技术事实（已核查）

### 3.1 TS 架构（对齐目标）
- **collector**（`apps/worker/src/collectors/`）：pre-recon-collector / recon-collector / vuln-collector / exploit-collector。每个暴露一组 `set_*` 工具（write-once，duplicate→DuplicateError），提供 `getAll()` 收集 payload、`getCallStatus()` 记录调用模式。
- **renderer**（`apps/worker/src/services/`）：pre-recon-renderer / recon-renderer / vuln-renderer / exploit-renderer / findings-renderer。纯函数 `renderXxx(data) → md string`，skipped section 渲染 placeholder。
- **writeDeliverable hook**（`agent-execution.ts:58-59, 296`）：每个 agent 注入自己的渲染函数，在 validate 后、commit 前统一调用：`await writeDeliverable(deliverablesPath)`。
- **queue.json 不变**：TS 也写 `{vt}_exploitation_queue.json`（`agent-execution.ts:277-278`，structuredOutput 落盘）——PY 已对齐，本设计不动。

### 3.2 TS pre-recon collector 的 7 个 set_* 工具
`set_executive_summary` / `set_application_intelligence` / `set_auth_deep_dive` / `set_codebase_indexing` / `set_critical_file_paths` / `set_xss_sinks` / `set_ssrf_sinks`。每个对应 pre_recon_deliverable.md 一个 section，schema 在 collector 的 TypeBox 定义里。

### 3.3 PY 双引擎都支持自定义工具注入（纠正前误判）
- **claude 轨**：`claude_agent_sdk.create_sdk_mcp_server(name, tools=[...])` 创建**进程内** MCP server（`McpSdkServerConfig`，无子进程、无 IPC），配进 `ClaudeAgentOptions.mcp_servers` + `allowed_tools`。工具用 `@tool(name, desc, args_schema)` 装饰器定义，可直接访问应用状态（collector 对象）。
- **openai 轨**：`agents.function_tool`（`tools_openai/` 已有模式：bash/grep/web/task/read/write/edit），注入 `Agent(tools=...)`。
- **结论**：多工具 collector 在双引擎都可行，不违背 §2 可互换铁律。

### 3.4 PY 现有 structured_output 通道（本设计不复用，但并存）
executor.py:147-154 从 `result.structured_output` 落盘 vuln `{vt}_exploitation_queue.json`。本设计不动这条通道（queue.json 继续走它）；新增的是 analysis/evidence/report **md** 的 collector+renderer 通道。

**vuln/exploit agent 将同时走两通道**：analysis/evidence md（collector+renderer，本设计新增）+ exploitation_queue.json（structured_output，已有）。双引擎均支持同一 agent run 内并存——claude 轨 `output_format`（structured_output）+ `mcp_servers`（collector 工具）是 `ClaudeAgentOptions` 两个独立字段可同配；openai 轨 `Agent(tools=...)` + response_format/schema 可同配。两通道互不干扰，各自落盘。

## 4. 设计

### 4.1 声明式 collector 框架（核心抽象）

13 agent × 2 引擎不能手写 26 套工具。抽象：

```
packages/core/src/shannon_core/collectors/
├── base.py            # CollectorBase + SectionSchema + getAll/getCallStatus/placeholder
├── bridge.py          # 双引擎工具桥:SectionSchema → openai function_tool / claude @tool+sdk_mcp_server
├── pre_recon.py       # pre-recon 的 7 section schema 声明 + CollectorBase 子类
├── recon.py           # (Plan 2+)
├── vuln.py            # 5 class 共用 (Plan 2+)
└── exploit.py         # 5 class 共用 (Plan 2+)

packages/core/src/shannon_core/renderers/
├── pre_recon.py       # render_pre_recon(data) → md (移植 TS pre-recon-renderer.ts)
├── recon.py           # (Plan 2+)
├── vuln.py            # render_vuln_deliverable(vuln_class, data) (Plan 2+)
└── exploit.py         # (Plan 2+)
```

**`SectionSchema`**（声明式）：字段名/类型/描述（移植 TS TypeBox）。一个 agent = 一组 SectionSchema。

**`CollectorBase`**：
- 持有各 section 的已收集 payload（None = 未调用/skipped）
- `set_section(name, payload)`：write-once，重复→DuplicateError（对齐 TS）
- `get_all() → dict`：返回 payload bag（None 的 section 由 renderer 补 placeholder）
- `get_call_status()`：记录每个 section 是否被调用（诊断/日志）

**双引擎工具桥**（`bridge.py`）：
- `build_openai_tools(collector) → list[function_tool]`：每个 section → 一个 `@function_tool`，调用时写入 collector
- `build_claude_mcp_server(collector) → McpSdkServerConfig`：每个 section → 一个 `@tool`，`create_sdk_mcp_server` 包装，in-process
- **一套 SectionSchema 声明，双引擎各自生成工具**——这是消除 26 套手写的关键

### 4.2 数据流（对齐 TS agent-execution.ts）

```
activity.run_agent(agent_name):
  1. collector = make_collector(agent_name)        # 建收集器
  2. tools = bridge.build_<engine>_tools(collector) # 双引擎工具
  3. executor.execute(..., collector=collector, collector_tools=tools)
  4. provider 把 tools 注入 agent (openai Agent(tools=) / claude mcp_servers+allowed_tools)
  5. agent 跑,调 set_* → collector 收集 (write-once)
  6. agent 跑完 → executor 调 renderer.render_<agent>(collector.get_all()) → 写 md
  7. validate_deliverable (md 现在一定存在,host 渲染的)
  8. skipped section → renderer placeholder,不 fail
```

**落盘点**：executor.py 加 `writeDeliverable` 等价逻辑（对齐 TS agent-execution.ts:296）。当 agent 有 collector 时，executor 在 validate 前用 `collector.get_all()` + renderer 渲染并写 md；无 collector 的 agent（如 validate-auth/cross-repo/attack-chain，deliverable_filename=None）不受影响。

### 4.3 prompt 改造
13 个 agent prompt：
- 删「MUST save ... using the Write tool」「use the Write tool to create ...」
- 改「MUST emit findings by calling all `set_*` tools listed in `<deliverable_tools>` before terminating. The host renders the deliverable Markdown from those calls — there is no Markdown for you to write yourself.」（对齐 TS prompt 原文）
- 列出该 agent 的 set_* 工具名（对齐 TS `<deliverable_tools>` 块）

### 4.4 GLM probe（Plan 1 第一步，降风险）
`scripts/validate_glm_mcp_tool_probe.py`：在 claude 轨（glm-anthropic）跑一个最小 agent，注入 2-3 个 `set_*` MCP 工具（in-process），验证 GLM 能：
- 正确识别并调用 MCP 工具
- 传符合 schema 的结构化参数
- 多次调用（write-once 语义）

对标 `validate_glm_task_probe.py`（验证 GLM 驱动 Agent 子代理委派）。**probe 通过才铺开 13 agent**，消掉最大未知。

### 4.5 诊断去留
治本后 host 一定渲染（不再 Missing deliverable），之前加的 `_enrich_missing_deliverable_error`（executor.py）诊断**移除**（治本后无意义、减噪音）。其 3 个测试随之移除/改造。

## 5. 不变量（守 CLAUDE.md 铁律）

- **§1 双轨独立性**：本设计只动产物落盘机制（md 怎么来），不碰双轨判定/合并/LLM 轨 prompt 的 source 派生。renderer 是纯函数，不引确定性层产物。
- **§2 双引擎可互换**：双引擎工具桥保证一套 SectionSchema 双引擎都生成工具，流程一致。
- **queue.json 通道不动**：vuln `{vt}_exploitation_queue.json` 继续走 structured_output（executor.py:147-154），本设计只加 md 的 collector+renderer 通道。
- **TS 对齐**：collector/renderer/writeDeliverable/prompt 文案均 1:1 移植 TS。

## 6. 分阶段（Plan 划分）

一个 plan 做完 13 agent + 框架太大。分阶段：

- **Plan 1**（本 spec 的首个 plan）：collector 框架 + 双引擎工具桥 + GLM probe + **pre-recon 端到端**（7 section schema + renderer + prompt 改 + 落盘 + 测试）。验证整条链 + GLM 可靠性。
- **Plan 2**：recon
- **Plan 3**：5 vuln class（共用 vuln collector/renderer，branching on class）
- **Plan 4**：5 exploit class
- **Plan 5**：report（findings-renderer）+ 移除诊断

框架在 Plan 1 稳定后，Plan 2-5 每个 agent = 一份 section schema + renderer + prompt 改，增量低风险。

## 7. 测试策略

- **renderer 单测**：每个 renderer 给固定 payload bag → 校验输出 md（byte-stability，对齐 TS renderer 注释「.md byte-stability」）。含 skipped→placeholder 用例。
- **collector 单测**：write-once（duplicate→DuplicateError）、get_all、get_call_status、skipped section。
- **双引擎工具桥单测**：同一 SectionSchema 在 openai 生成 function_tool、在 claude 生成 sdk_mcp 工具，调用都正确写入 collector。
- **executor 落盘单测**：mock agent（调 set_*）→ 校验 md 落盘 + 内容；skipped → placeholder md 仍落盘不 fail。
- **GLM probe**：scripts 级真机探针（非 pytest），验证 GLM 驱动 MCP 工具。

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| GLM 驱动 MCP 工具/传结构化参数不可靠 | Plan 1 第一步 probe 验证，不通过则回退讨论 |
| 13 agent 改 prompt 回归面大 | 分阶段（Plan 1 只 pre-recon），每阶段独立验证 |
| 双引擎工具桥抽象不当致双引擎分叉 | 桥单测强制同一 schema 双引擎一致 + AST 守卫（对齐项目惯例）|
| collector 状态在并发 agent 下 race | collector 是 per-agent-run 实例（非全局），无共享（对齐 TS SessionToolAuditLogger per-agent 模式）|

## 9. 参考

- TS 代码：`upstream/main:apps/worker/src/collectors/pre-recon-collector.ts`、`services/pre-recon-renderer.ts`、`services/agent-execution.ts`
- PY 现状：`packages/core/src/shannon_core/agents/executor.py`、`validators.py`、`models/agents.py`
- SDK 能力：`claude_agent_sdk.create_sdk_mcp_server`（`__init__.py:311`）、`McpSdkServerConfig`（in-process）
- 前置修复（已合）：executor 诊断 `_enrich_missing_deliverable_error`（治本后移除）
