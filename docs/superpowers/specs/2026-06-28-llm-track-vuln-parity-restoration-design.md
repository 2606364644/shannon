# LLM 轨 vuln agent 对齐 TS 设计（生命周期对齐 + 弱点修复）

**日期**: 2026-06-28
**状态**: Draft → 待 review → 转 writing-plans
**分支**: feat/fork-py
**相关**: `2026-06-22-retry-policy-alignment-design.md`(VULN_RETRY 起源)、`2026-06-27-dual-track-decoupling-design.md`(LLM 轨独立性铁律)、`2026-06-23-prompt-optimization-design.md`(prompt 对比方法论)

---

## 1. 背景

基于"双轨模式下重构项目 LLM 轨全生命周期对比原始 TS 项目（`/Users/mango/project/shannon-refactor/shannon`）"的逐阶段分析，结论：**PY 重构的 LLM 轨整体与 TS 一致、且在双引擎 / verdict-OR 合并 / Source Completeness Rule 等多处净增强**；双轨铁律（LLM 轨不吃确定性产物）在 injection/xss/ssrf/authz 上已落实（`_static-dataflow-hints.txt` 桥已拆除）。

但对比发现两类**弱于原始**的退化点：

- **B1（prompt 方法论删减）**：`vuln-injection.txt` 删了 **Branch Path Exhaustion** 段（控制器多分支须独立 trace）；`vuln-xss.txt` 删了 **server-rendered templates** 段（render-context + `JSON.stringify`/`</script>` 绕过 + SSTI-vs-XSS 边界）。纯方法论丢失，可能漏报分支间校验不一致的注入与 render-context XSS。
- **B2（执行参数偏紧）**：vuln agent `max_turns=200`（TS 10000）、`VULN_RETRY` 5 次（TS 50 次）、openai 子代理 `max_turns=20`。相对 TS 的"宽裕安全网"明显偏紧，存在追链截断 / 重试不足风险。

本 spec 修复 B1 + B2，采用 **Approach B（对齐增强）**：补回方法论 + vuln 专用 max_turns + VULN_RETRY 调高 + openai 子代理 turn 调高 + 完整观测。

**不在 scope**（见 §6）：B3 并发模型、auth_config_context 不对称、pre-recon starting_context。

---

## 2. 现状核实（已读码确认）

### 2.1 TS 原版（shannon，TypeScript）

- **max_turns**：所有 agent `maxTurns: 10_000`（`apps/worker/src/ai/claude-executor.ts:238`），"无限"语义安全网（注释自承 tens of turns 完成）。
- **retry**：全 agent 共享 `PRODUCTION_RETRY`（50 次 / 5min / 30min / backoff 2.0，`workflows.ts:66-81`）。无 vuln 特例。
- **prompt**：`vuln-injection.txt` 含 Branch Path Exhaustion；`vuln-xss.txt` 含 server-rendered templates。
- **子代理**：claude-agent-sdk 原生 `Task`/`Agent` 工具，max_turns 用 SDK 默认（宽松）。
- **LLM 轨独立性**：vuln prompt 零确定性产物（只读 `recon_deliverable.md`/`pre_recon_deliverable.md` + 自 grep），证实 CLAUDE.md 铁律源头。

### 2.2 PY 重构（shannon-py）

- **max_turns**：
  - anthropic 主 agent：全局 `CLAUDE_MAX_TURNS`=200（`providers_anthropic.py:242`）
  - openai 主 agent：`SHANNON_OPENAI_MAX_TURNS`=200（`providers_openai.py:74`）
  - openai 子代理：`SHANNON_OPENAI_SUBAGENT_MAX_TURNS`=20（`providers_openai.py:122`）
  - vuln agent 未单独配置，吃全局 200。
- **VULN_RETRY**：`maximum_attempts=5`（`models/retry.py:56-62`），注释 self-declared "有意分歧于 TS PRODUCTION_RETRY"，封顶 ~12min。
- **prompt**：两段方法论缺失（B1）。
- **观测字段已就绪**：`ClaudeRunResult` 已有 `turns: int`（`runner.py:94`）+ `stop_reason` + `error_code`；vuln activity 已有 `session.log_error` + `tool_audit_logger` + `AgentEndResult`（`activities.py:99/152/164`）。但 `result.turns` **未被持久化记录**。
- **子代理防递归（关键）**：openai 子代理结构层硬限单层 —— `ToolContext` 不注入 `subagent_run`（`providers_openai.py:128`），工具集只读 `[read_file, glob, grep]`（`:118`）。**调大子代理 turn 无递归失控风险**，仅增单次子代理 token。
- **max_turns 命中检测**：openai → `stop_reason="max_turns"` + `error_code="ExecutionLimitError"` + `retryable=False`（`openai_result_mapper.py:54-68`）；anthropic → `error_max_turns` subtype（`providers_anthropic.py:406`）。两引擎均可判。

---

## 3. 设计决策

| # | 决策 | 定论 |
|---|---|---|
| 1 | 范围 | B1（补回两段 prompt）+ B2（vuln 专用 max_turns + VULN_RETRY 调高 + openai 子代理 turn 调高 + 观测）。**Approach B（对齐增强）**，用户选定。 |
| 2 | B2 动因 | 预防性调高，锚定 TS 经验（非先观测）。为 vuln 单独配，不污染全局 pre-recon/recon。 |
| 3 | max_turns 定值 | vuln 专用 `SHANNON_VULN_MAX_TURNS`=**500**（2.5× 现值 200，远低于 TS 10000 的"无限"语义，给子代理追链留空间又控 token）。 |
| 4 | VULN_RETRY 定值 | `maximum_attempts 5→8`（interval 不变），封顶 ~12min→~20min，重试韧性提升但不至 TS 数小时尾部。 |
| 5 | openai 子代理 turn | `SHANNON_OPENAI_SUBAGENT_MAX_TURNS` 20→**40**。**无需额外递归护栏**（现有结构层硬限单层）。 |
| 6 | 观测 | 零新字段，复用 `ClaudeRunResult.turns`/`stop_reason`；vuln 完成记 `turns_used`，撞 max_turns 走 `log_error` + `activity_failures.log`。 |

---

## 4. 设计

### 4.1 B1 — prompt 方法论补回

**改动文件**：`prompts/vuln-injection.txt`、`prompts/vuln-xss.txt`

**`vuln-injection.txt` 补回 Branch Path Exhaustion**（源自 TS 原文，适配 `{{DELIVERABLES_PATH}}` 占位符）：
> 控制器方法若含 `if/else/early-return/switch` 等多分支，每个分支须独立 trace 到 sink。不得因某分支存在输入校验就判定整个参数安全 —— 其他分支可能绕过该校验。

放置：追链方法论段落附近（与 TS 文件位置语义对齐）。

**`vuln-xss.txt` 补回 server-rendered templates**（源自 TS 原文）：
> 关注 server-rendered templates（`ctx.render`/`res.render`）中 template 变量来自 URL query/body 的 reflected XSS。即便 injection agent 已分析 SSTI，xss agent 仍须独立做 render-context 分析。注意 `JSON.stringify()` 在 `<script>` tag 内不转义 `</script>`，可被注入闭合标签打断脚本上下文。

放置：sink / render-context 方法论段落附近。

**不做**：不改 sink 清单、不改输出格式、不引任何确定性产物（守铁律）。

### 4.2 B2 — vuln 专用 max_turns（传参链）

新增 `max_turns` override，不破坏现有签名默认值：

1. `runner.py:run_claude_prompt` 增参 `max_turns: int | None = None`
2. 透传到 `provider.call(max_turns=...)`（anthropic + openai 两 provider 的 `call` 增同名参）
3. provider 内部：`effective = max_turns or int(os.getenv(<引擎默认>))`（外部传入优先，否则沿用 env 默认）
4. `run_vuln_agent`（`activities.py:170`）调用 `run_claude_prompt` 处传入 `max_turns=int(os.getenv("SHANNON_VULN_MAX_TURNS", "500"))`
5. 其他 agent 调用不传，行为零变更

**改动文件**：
- `packages/core/src/shannon_core/agents/runner.py`（签名 + 透传）
- `packages/core/src/shannon_core/agents/providers_anthropic.py`（`call` 增参，`_build_options` 用 override）
- `packages/core/src/shannon_core/agents/providers_openai.py`（`call` 增参，`Runner.run_streamed` 用 override）
- `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（vuln 调用处传 max_turns）

**env 默认值**：`SHANNON_VULN_MAX_TURNS=500`（写入 docker-compose.yml env + 文档；不硬编码，可调）。

### 4.3 B2 — VULN_RETRY 调高

**改动**：`packages/core/src/shannon_core/models/retry.py:56-62`

```python
# vuln agent 专用:per-vt fan-out 下封顶 ~20min,有意分歧于 TS PRODUCTION_RETRY。
# 详见 docs/superpowers/specs/2026-06-22-retry-policy-alignment-design.md §2.3
# 及 2026-06-28-llm-track-vuln-parity-restoration-design.md §4.3。
VULN_RETRY = RetryPolicy(
    maximum_attempts=8,            # 5→8
    initial_interval=timedelta(minutes=1),
    maximum_interval=timedelta(minutes=5),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)
```

### 4.4 B2 — openai 子代理 max_turns 调高

**改动**：`packages/core/src/shannon_core/agents/providers_openai.py:122`

```python
max_turns = int(os.getenv("SHANNON_OPENAI_SUBAGENT_MAX_TURNS", "40"))  # 20→40
```

**无额外护栏**：子代理结构层已硬限单层（无 `subagent_run` + 只读工具集 `[read_file, glob, grep]`），调大仅增单次 token，不引入递归。双引擎一致性：anthropic 子代理用 SDK 默认（宽松），openai 提到 40，缩小差距。

### 4.5 B2 — 观测埋点

**改动**：`run_vuln_agent`（`activities.py`）在 `run_claude_prompt` 返回后：

- 读 `result.turns`、`result.stop_reason`、`result.error_code`
- **正常完成**：把 `turns_used` 记入 audit log（`AgentEndResult` 增 `turns` 字段，或 metrics dict 补 `turns_used`）
- **撞 max_turns**（`stop_reason=="max_turns"` 或 `error_code=="ExecutionLimitError"`）：
  - 调 `session.log_error(...)`（已有，surface 到 live display）
  - 记 `activity_failures.log`（已有载体）

**双引擎一致**：观测逻辑引擎无关（两 provider 都设 `stop_reason`/`error_code`）。

---

## 5. 测试策略

| 部分 | 锚点测试 |
|---|---|
| 4.1 B1 | 断言 `vuln-injection.txt` 含 "branch"/"early-return" 关键句；`vuln-xss.txt` 含 "render"/`JSON.stringify`/`</script>`；两文件仍不含确定性产物引用（守铁律，复用 decoupling 测试模式） |
| 4.2 max_turns | `run_claude_prompt(max_turns=500)` 透传到 provider；vuln activity 传 500；其他 agent 不传时沿用 env 200 |
| 4.3 VULN_RETRY | `retry.py` 断言 `VULN_RETRY.maximum_attempts==8` |
| 4.4 子代理 | `SHANNON_OPENAI_SUBAGENT_MAX_TURNS` 默认 40；子代理工具集仍只 `[read_file, glob, grep]`（防递归锚点不回归） |
| 4.5 观测 | mock `ClaudeRunResult(turns=42)` 验证 `turns_used` 记录；mock `stop_reason="max_turns"` 验证 `log_error` 触发 |

**只跑改动相关测试文件**（守 `pytest-whitebox-hang` 约定，不跑全套）。

---

## 6. Scope 边界（不在本次）

- **B3 并发模型**：`Semaphore(max_concurrent)`（`workflows.py:323`）比 TS 全并发更安全，保留。
- **auth_config_context 不对称**：auth 类有意设计（`workflows.py:287` spec §5.8 GitNexus track for vuln-auth），非弱点。
- **pre-recon starting_context 删除**：轻微独立项，另案。
- **B2 不含 anthropic 子代理 turn**：anthropic 子代理用 SDK 默认（宽松），无需调。

---

## 7. 风险与回退

| 风险 | 缓解 |
|---|---|
| max_turns=500 + retry 8 叠加 → token 上升 | 500 远低于 TS 10000；观测埋点提供 turn 消耗数据，过紧/过松都可后续调 env，无需改代码 |
| prompt 补回措辞与 PY 风格不协 | 源自 TS 原文 + 适配占位符，review 把关；锚点测试锁定关键句 |
| 传参链改动面较大（4 文件） | override 走"外部传入优先"默认 None，非 vuln 调用零行为变更；单测覆盖透传 |
| 回退 | 全部经 env / 常量 / 文本段，回退 = 改默认值 / 删补回段，无数据迁移 |
