# GitNexus 轨多轮 agent 基础设施（spec-0） 设计

> 日期：2026-07-02　分支：`feat/fork-py`　所属 epic：`2026-07-02-gitnexus-deep-agent-auth-authz-design.md`（子项目 0）
>
> **背景**：epic 把 GitNexus 轨 auth/authz 判定从"轻量单次 `run_claude_prompt`"升级为"多轮深度 agent（带 grep/read 追链）"。本 spec 只做**通用基础设施**——让 GitNexus 调用层**具备**跑多轮 agent 的能力（脚手架）；具体 authz_judge 怎么用多轮判定是 spec-1，chain_verdict（inj/xss/ssrf）按 epic 非目标不动。

---

## 1. 目标 / 非目标

### 目标

- **G1（ABC 签名补齐）**：`BaseProvider.call()` 的 abstract 签名补 `max_turns` 参数（现 ABC 漏了，两个实现都有），恢复 Liskov 替换。
- **G2（多轮 verdict 调用契约）**：定义 GitNexus 多轮判定的统一调用方式——升级 `llm_client` 契约或新建 verdict-agent 调用入口，支持 `max_turns` / `structured_output_schema` / `tool_audit_logger`，不丢 `turns`/`cost`/`structured_output` 元信息。
- **G3（retry / env / 超时）**：新增 `GITNEXUS_VERDICT_RETRY`（max 3，区别于 PRODUCTION_RETRY 的 50）+ `SHANNON_GITNEXUS_VERDICT_MAX_TURNS`（默认 30）env；增大 GitNexus verdict activity 的 `start_to_close_timeout`。
- **G4（双引擎对齐验证）**：确认 glm-anthropic / glm-openai 双引擎下多轮 verdict 都能跑（探针实测）。

### 非目标

- **不改判定逻辑**：authz_judge 实际多轮用法（候选分发、自主探索）留给 spec-1；本 spec 只让调用层"能"多轮。
- **不改 `chain_verdict`（inj/xss/ssrf）判定深度**：epic 非目标。但 chain_verdict 的调用点会顺带获得多轮能力（契约升级的副产品），是否启用由 spec-1 / 后续决定。
- **不改 LLM 轨 vuln agent**：已多轮（`max_turns=500`）。
- **不改工具集**：双引擎工具已就绪（Anthropic 白嫖 CLI 内置 / OpenAI `build_tools()` 9 工具），多轮 verdict 复用即可。

---

## 2. 现状证据

| 现象 | 证据 |
|---|---|
| `run_claude_prompt` 已支持多轮 | `runner.py:106-118` 签名含 `max_turns: int \| None = None`；`:164-172` 透传给 `provider.call(max_turns=...)`。不传时走引擎默认（CLAUDE_MAX_TURNS=200 / SHANNON_OPENAI_MAX_TURNS=200） |
| `BaseProvider.call()` ABC 漏 `max_turns` | `providers.py:67-76` abstract 签名无 `max_turns`；但 `AnthropicProvider.call()`（`providers_anthropic.py:80-89`）和 `OpenAIProvider.call()`（`providers_openai.py:184-193`）实现都有 → Liskov 破缺 |
| GitNexus 3 调用点单次薄包装 | `_make_verdict_llm_client`（`activities.py:855-866`）、`_make_gitnexus_llm_client`（`:460-476`）：`async (prompt)->str`，不传 `max_turns`/`structured_output`/`audit_logger`，返回 `result.text` 丢元信息；`run_authz_gitnexus_judge`（`:331-342`）直接调 `run_claude_prompt` 无 `max_turns`，但有 `structured_output_schema` |
| `llm_client` 契约返 `str` | `chain_verdict.py:211-215` `judge_chain_verdict(llm_client: Callable[..., Awaitable[str]])`；`:240` `raw = await llm_client(prompt)`；`:252` 自己 `json.loads` |
| retry/超时现状 | authz_judge：`retry_for("standard")`=PRODUCTION_RETRY(max 50) + 10min（`workflows.py:370-374`）；chain_verdict：同 retry + 5min（`:391-395`）。对比 vuln agent：VULN_RETRY(max 8) + 2hr + max_turns 500 |
| retry policy 体系 | `models/retry.py:13-63`：PRODUCTION_RETRY(50)、VULN_RETRY(8)、CODE_INDEX_RETRY(3) |

---

## 3. 设计

### 3.1 补 `BaseProvider.call()` ABC 签名

`providers.py:67-76` 的 abstract 加 `max_turns: int | None = None`（对齐两个实现）。纯签名补齐，零行为变化（实现已有该参数）。

```python
# providers.py BaseProvider.call()
@abstractmethod
async def call(self, prompt, cwd, model_tier="medium", output_format=None,
               deliverables_subdir=None, audit_logger=None,
               max_turns: int | None = None,           # 新增
               structured_output_schema: dict | None = None,  # 新增（OpenAI 已用，Anthropic 顺带）
               ) -> ClaudeRunResult: ...
```

> **为何**：ABC 漏签名让"业务侧靠 ABC 类型推断"的代码看不到 `max_turns`（type checker 报错 / 自动补全缺失）。补齐恢复 Liskov，是 G2 契约升级的前提。

### 3.2 多轮 verdict 调用契约

**现状**：`_make_verdict_llm_client` 返回 `async (prompt)->str`，`judge_chain_verdict` 吃这个 `Callable[..., Awaitable[str]]`。契约太薄（丢 structured_output/turns/cost），且单次语义写死。

**改动**：新建一个**多轮 verdict 调用入口**（不破坏现有 `llm_client` 单次契约，chain_verdict 留给 spec-1 决定是否切）：

```python
# activities.py 新增
async def run_gitnexus_verdict_agent(
    *, prompt: str, repo_path: str, structured_output_schema: dict | None = None,
    audit_session=None,
) -> ClaudeRunResult:
    """GitNexus 多轮 verdict agent：带 grep/read 自主追链。
    max_turns 走 SHANNON_GITNEXUS_VERDICT_MAX_TURNS（默认 30）。
    返回完整 ClaudeRunResult（含 turns/cost/structured_output），不截断为 str。"""
    from shannon_core.agents.runner import run_claude_prompt
    return await run_claude_prompt(
        prompt=prompt, repo_path=repo_path, model_tier="medium",
        max_turns=int(os.getenv("SHANNON_GITNEXUS_VERDICT_MAX_TURNS", "30")),
        structured_output_schema=structured_output_schema,
        tool_audit_logger=...,   # 接 audit_session（对齐 vuln agent 的 SessionToolAuditLogger）
    )
```

`_make_verdict_llm_client` / `_make_gitnexus_llm_client` **暂不改**（仍单次，供 chain_verdict/discover_sinks_llm 现状用）；spec-1 的 authz_judge 改用 `run_gitnexus_verdict_agent`。

> **为何新建入口而非改 `llm_client` 契约**：`llm_client: Callable[..., Awaitable[str]]` 被 3 个 builder（injection/xss/ssrf）+ chain_verdict 共用，改契约影响面大且 chain_verdict 不在本 epic 范围。新建专用入口隔离改动，authz_judge 单独切换。

### 3.3 新增 retry / env

`models/retry.py` 新增 category + policy：

```python
# retry.py
GITNEXUS_VERDICT_RETRY = RetryPolicy(
    maximum_attempts=3,                       # 多轮 agent 贵，不重试 50 次
    initial_interval=timedelta(seconds=30),
    maximum_interval=timedelta(minutes=2),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)
# retry_for() 映射加 "gitnexus-verdict" -> GITNEXUS_VERDICT_RETRY
```

env：`SHANNON_GITNEXUS_VERDICT_MAX_TURNS`（默认 30），对齐 `SHANNON_VULN_MAX_TURNS`（500）/ `CLAUDE_MAX_TURNS`（200）的命名模式。默认 30 是权衡：比 vuln agent 500 少（GitNexus 有候选指路，不用从零探索），比单次 1 多（够追 owner 检查/授权逻辑）。

### 3.4 增大 verdict activity 超时

`workflows.py` authz_judge / chain_verdict 的 `start_to_close_timeout` 从 10min/5min 增到 **30min/15min**（多轮 agent 比单次久）。retry_policy 视 spec-1 决定是否切 `"gitnexus-verdict"`（本 spec 只新增 policy，不强制切——chain_verdict 仍 standard，authz_judge 由 spec-1 切）。

### 3.5 双引擎对齐

无需额外代码。`run_gitnexus_verdict_agent` → `run_claude_prompt(max_turns=...)` → `provider.call(max_turns=...)`：
- Anthropic：CLI 子进程 `max_turns` 限轮，白嫖内置 bash/Read/Grep/Glob。
- OpenAI：`Runner.run_streamed(max_turns=...)` + `build_tools()` 9 工具 + `task` 子代理。

验收用双引擎探针实测（G4）。

---

## 4. 验收

- **V1**：`BaseProvider.call()` ABC 签名含 `max_turns`，两实现签名一致，type checker（mypy/pyright）无 Liskov 警告。
- **V2**：`run_gitnexus_verdict_agent` 能跑多轮——传一个需 grep 追链的 prompt，确认 `result.turns > 1`、`result.cost > 0`、`result.success=True`。
- **V3**：`SHANNON_GITNEXUS_VERDICT_MAX_TURNS` env 生效（设 5 时 turns ≤ 5）。
- **V4**：`GITNEXUS_VERDICT_RETRY` 注册，`retry_for("gitnexus-verdict")` 返回 max_attempts=3。
- **V5**：双引擎探针——`scripts/validate_*_task_probe.py` 类探针在 glm-anthropic / glm-openai 各跑一次多轮 verdict，均 success。
- **V6**：现有单次调用（chain_verdict / discover_sinks_llm / authz_judge 单次路径）**行为不变**（回归锚点——本 spec 不改它们）。

---

## 5. 风险

- **R1（成本）**：多轮 agent 每轮花 token，max_turns=30 若跑满是单次的数十倍。**对策**：默认 30（非 500）；spec-1 验收做 token/召回实测（epic R3）；后续可加 per-call token 上限。
- **R2（超时叠加）**：多候选逐条多轮会撑爆 timeout。**对策**：候选分发策略 + 并行化是 spec-1 的事；本 spec 只把超时窗口开够（30min）。
- **R3（ABC 签名变更影响面）**：补 ABC 签名理论上影响所有 `BaseProvider` 子类。**对策**：两实现已有该参数，纯签名补齐零行为变化；加测试锁签名一致。
