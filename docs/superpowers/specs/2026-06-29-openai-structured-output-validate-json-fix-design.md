# openai 引擎 structured output 解析韧性修复 设计

> 日期：2026-06-29
> 分支：feat/fork-py
> 状态：设计待 review
> 关联：[2026-06-29 黑盒 exploit 产物结构化校验护栏](./2026-06-29-blackbox-exploit-structured-output-design.md)（其 §6 降级假设依赖本修复落地）、[2026-06-17 openai-agents 引擎设计](./2026-06-17-openai-agents-engine-design.md)
> 触发：白盒扫描 `uv run shannon-whitebox start --repo /root/code/frontend/invite_code_center/` 实测 bug（injection-vuln / xss-vuln 全失败）

---

## 0. 一句话结论

openai 引擎（glm-openai profile）下，带 structured output 的顶层 agent（`*-vuln`）在 GLM 跑完分析后最终输出非合法 JSON 时，`RawJsonSchemaOutputSchema.validate_json` 的零容错 `json.loads` 在 char 0 抛 `JSONDecodeError`，被外层 except 当成致命执行错误 → 整个 agent 判失败 + Temporal 重试 8 次都同样失败（重跑整个 agent 几十轮，纯烧 token）。修复 = 三层防线（L0 `validate_json` 容错解析 + L1 provider 层轻量重输模拟 Claude SDK 单次内部重试 + L2 Temporal 兜底），核心是「解析失败不该否定 agent 已完成的工作」，并补一个带 structured output 的顶层 agent 探针堵住 task-probe 盲区。

---

## 1. 背景

### 1.1 报错链路（systematic-debugging 全链路追证）

报错：`Expecting value: line 1 column 1 (char 0)`（Python `json.loads` 解析空/非 JSON 文本的精确错误）。

| 环节 | 位置 | 行为 |
|---|---|---|
| ① 报错 wrap | `executor.py:126` | `raise PentestError(result.error …)` —— `result.success=False` 且 `result.error="Expecting value..."` |
| ② vuln agent 传 schema | `activities.py:191` | `structured_output_schema=_vuln_output_schema(...)`，对 `*-vuln` 返回非 None schema（`activities.py:126-146`） |
| ③ 设 SDK output_type | `providers_openai.py:97` | `output_type=RawJsonSchemaOutputSchema(output_format)` |
| ④ SDK 调解析器 | `agents/run_internal/turn_resolution.py:810` | `final_output = output_schema.validate_json(potential_final_output_text)` |
| ⑤ **零容错解析（根因）** | `openai_output_schema.py:42-44` | `validate_json` 直接 `return json.loads(json_str)` —— 不剥 fence、不提取子串、不 catch |
| ⑥ 异常吞成执行失败 | `providers_openai.py:183` | 外层 except → `_handle_error` 把 `str(error)` 写进 `result.error`，`success=False` |
| ⑦ 重试 | `activities.py:203` → Temporal | `ApplicationFailure` + `AgentExecutionError`（默认可重试）→ vuln 8 次（`retry.py:58`） |

**坏值实证**：失败 run 的 agent 日志（`workspaces/invite_code_center_20260629-094355/agents/1782698824401_injection-vuln_attempt-1.log`）显示 injection-vuln 在 turn 38–40 写完了 9148 字节、138 行的完整 `injection_analysis_deliverable.md`，随后 `agent_end success=false` —— **agent 的分析工作全部完成，崩在最后一步「产出 JSON 收尾」**。GLM 那一步给的不是 `{"vulnerabilities":[...]}`（空 / 中文叙述 / 或塞进了文件），`json.loads` 在 char 0 失败。

### 1.2 为什么是系统性失败（不是偶发）

- injection-vuln 与 xss-vuln 都是 `*-vuln`，都走同一条 structured output 路径 → 两者都挂；
- 同一 prompt + 同一 GLM 收尾倾向 → Temporal 重试 8 次都在同一个 char 0 失败（重试无意义）；
- `_instructions()` 的 narration directive 进一步强化「中文叙述收尾」倾向。

### 1.3 测试盲区（bug 漏网根因）

`test_openai_output_schema.py` 只测了 `validate_json` 的 happy path + 「非 JSON 抛异常」契约（line 28 `test_validate_json_raises_on_invalid`），**没有测「final_output 非 JSON 时的韧性」**；`test_run_agent_vuln_schema.py` 只锁定 schema 透传到 executor，**没测解析失败的语义**。`validate_*_task_probe.py` 验证的是 task 子代理（`providers_openai.py:121-127` 无 `output_type`），**覆盖不到带 structured output 的顶层 agent**。这是本 bug 漏网的直接原因。

---

## 2. TS 对齐分析（决定修复策略）

### 2.1 原始 TS（`/root/shannon`）的做法

TS **自己从不解析裸 JSON**：`message-handlers.ts:228` 直接读 Claude SDK 给的**已解析对象** `message.structured_output`。解析 + 验证全在 Claude SDK 内部——SDK 对单次输出**多次内部重试**，耗尽才塞 `subtype: 'error_max_structured_output_retries'`，TS 据此抛 `OUTPUT_VALIDATION_FAILED`（`message-handlers.ts:355-366`），Temporal 外层再重试 `MAX_OUTPUT_VALIDATION_RETRIES = 3`（`activities.ts:57,224-234`）。**TS 的重试有意义**：每次都让 LLM 重输，可能这次就合法。TS 写盘（`agent-execution.ts:222-229`）对已解析对象 `JSON.stringify`，`structuredOutput === undefined` 时不写盘。

### 2.2 openai-agents 的决定性差异

- `validate_json` 在 `turn_resolution.py:810` 是**裸调用、无 try/except、无内部重试**；失败异常经 `run.py:1505 except BaseException ... raise` 直接冒泡。
- `model_retry.py` 只重试 **HTTP 传输层**（`APIConnectionError`/`APITimeoutError`/429/500），与 structured output 解析无关。
- `validate_json` 契约（`agent_output.py:46-51`）要求「失败抛 `ModelBehaviorError`」——SDK 把它当模型行为异常，非可重试瞬时错误。
- 对照：`tool.py:1492` 对 **tool 调用**的 JSON 错误有 `"Please try again with valid JSON"` 反馈重试，但 **agent final output** 的 JSON 错误**没有**——因为 SDK 假设 strict 模式已保证 final output 合法。

### 2.3 为什么 openai-agents 不提供结构化输出重试（根因）

openai-agents 的设计假设 = **模型端 `response_format`（OpenAI 原生 Structured Outputs）的 token 级约束保证输出合法**。它把 schema 经 `response_format` 透传给 OpenAI 后端（`openai_chatcompletions.py:439,489`），strict json_schema 在**解码时**强制合法。所以 SDK 认为「合法由模型端保证，`validate_json` 只是事后确认」，失败即冒泡。

**我们的困境**：`RawJsonSchemaOutputSchema.is_strict_json_schema()` 返回 **`False`**（`openai_output_schema.py:32-34`，注释「GLM 第三方 endpoint 用 non-strict，避免 strict 模式对额外字段拒收」）。non-strict 意味着 `response_format` 给 GLM 后端只是「尽力而为」，**不是 token 级强制**。GLM 后端不支持 strict（已知缺陷）→ 我们主动放弃了这层保证 → 模型端不保证合法 → `validate_json` 失败。夹在中间：GLM 无 strict 约束 + openai-agents 不提供解析重试 = 任何非纯 JSON 都致命。

### 2.4 对齐结论

照抄 TS 的「Temporal 外层重试」在 openai 引擎是**错误对齐**——TS 重试有意义是特定于 Claude SDK 有内部重试；openai 引擎重试 = 每次重跑整个 agent 几十轮（用户日志已证 8 次都同样失败），纯烧 token。真正「对齐 TS 精神」= ① 把「LLM→JSON 契约」做扎实（TS 靠 SDK 免费，Python 因 openai-agents 没这层，自己在 `validate_json` 补容错解析）；② 模拟 Claude SDK 的「单次内部重试」（provider 层轻量重输）；③ 解析失败不该否定 agent 已完成的工作。

---

## 3. 范围

| 项 | 处理 | 说明 |
|---|---|---|
| **L0 容错解析** | ✅ 主体 | `validate_json` 剥 fence + 子串提取 |
| **L1 provider 轻量重输** | ✅ 主体 | 模拟 Claude SDK 单次内部重试，1 turn 成本 |
| **L2 Temporal 兜底** | ✅ 小改 | `StructuredOutputParseError` → `OUTPUT_VALIDATION_FAILED` + executor error_code 透传 |
| **探针（堵盲区）** | ✅ 测试 | 带 structured output 的顶层 agent 探针 |
| retry 上限 | ❌ 不动 | vuln 8 次有意分歧于 TS 的 3（`retry.py:54-58`），保留 |
| vuln prompt | ❌ 不动 | deliverable md 必须保留（报告靠它），不照搬 blackbox `1f6a36c9` 删 md-writing 段 |
| blackbox exploit | ❌ 不扩 | 有独立 spec（见 §8），本修复更底层、让其未来落地自动受益 |
| 开 strict 模式 | ❌ 不动 | GLM 后端不支持，不可行 |
| openai-agents 内部塞重输循环 | ❌ 不动 | 复刻 Claude SDK 内部重试成本高且脆弱，靠 L1 替代 |

---

## 4. 设计

### 4.1 总体架构 —— 三层防线

```
GLM final output (turn_resolution.py:810 调 validate_json)
  │
  ▼ L0  validate_json 容错解析（剥 fence / 取首末花括号子串）   openai_output_schema.py
  │   成功 → 返回对象（SDK 正常结束，map_run_result 拿 dict）
  ▼    提取不到 → 抛 StructuredOutputParseError
  │
  ▼ L1  providers_openai.call() 捕获 StructuredOutputParseError
  │   → _lightweight_reparse(collector.text, output_format, model)
  │     同 client 发单个 chat completion（无 loop/工具/narration），prompt「转为纯 JSON」
  │     + response_format=json_schema(non-strict)，复用 _extract_json_payload 解析
  │   成功 → 伪造 final_output 走 map_run_result → structured_output             providers_openai.py
  ▼    仍失败 → re-raise
  │
  ▼ L2  _classify_error → OUTPUT_VALIDATION_FAILED (retryable)
  │   executor.py 透传 result.error_code
  │   classify_error_for_temporal → ("OutputValidationError", True)
  │   → Temporal vuln 8 次兜底（L0+L1 都失败的极端情况）                        errors.py / executor.py
```

每层职责单一、可独立测试：L0 是纯函数（输入字符串、输出对象或抛特定异常）；L1 是 provider 内部的恢复路径（输入文本+schema、输出对象或 None）；L2 是错误分类 + 重试编排（已有基础设施）。

### 4.2 L0 容错解析 —— `openai_output_schema.py`

**位置**：`packages/core/src/shannon_core/agents/openai_output_schema.py:42-44`

**当前**（零容错，bug 根因）：
```python
def validate_json(self, json_str: str) -> Any:
    return json.loads(json_str)
```

**改为**：
```python
def validate_json(self, json_str: str) -> Any:
    candidate = _extract_json_payload(json_str)   # 剥 fence + 子串提取
    if candidate is None:
        raise StructuredOutputParseError(json_str)  # 真失败 → OUTPUT_VALIDATION_FAILED 语义
    return json.loads(candidate)
```

**`_extract_json_payload(json_str) -> str | None`** 算法（模块级纯函数，L0/L1 复用）：
1. `strip()`；
2. 若以 ` ``` ` 开头 → 剥 markdown fence（兼容 ` ```json ` / ` ``` ` 前缀与可选语言标签）；
3. 取首个 `{` 到末个 `}` 的子串（救「前导叙述 + JSON」）；
4. 全无 `{`/`}` → 返回 `None`。

**`StructuredOutputParseError(Exception)`**（同文件定义）：承载「结构化输出解析失败」语义，供 L1 捕获 / L2 分类识别。**不继承 `ModelBehaviorError`**（避免被 SDK 的 error handler 路径误吞，要由我们的 provider 显式处理）。

**契约反转**：现有测试 `test_openai_output_schema.py:28 test_validate_json_raises_on_invalid` 断言「非 JSON 抛 Exception」——修复后仍抛（`StructuredOutputParseError` is-a Exception），契约表面不变；但新增 fence / 子串能解析的 happy path，故测试需**补充**这几档，而非删除。

### 4.3 L1 provider 轻量重输 —— `providers_openai.py`

**位置**：`packages/core/src/shannon_core/agents/providers_openai.py`（`call()` 方法 + 新增 `_lightweight_reparse`）

**依赖（已确认可行）**：`StreamCollector.text`（`openai_stream_collector.py:33-34`）累积所有 `raw_response_event` 的 text delta；`validate_json` 在 stream 之后执行（`turn_resolution.py:810`），时序保证异常冒泡前 agent 的 final output 已在 `collector.text` 里。

**`call()` 改动**：在内层 `try`（当前 line 157-172）外包一层 `except StructuredOutputParseError`（置于 `MaxTurnsExceeded` 之后、外层 `except Exception` 之前，确保不被误吞）：
```python
try:
    result = Runner.run_streamed(...)
    async for event in result.stream_events():
        await collector.on_event(event)
    await collector.close()
    run_result = result
except MaxTurnsExceeded:
    ...
    stop_reason = "max_turns"
except StructuredOutputParseError:
    # L1: 容错解析失败 → 轻量重输，模拟 Claude SDK 单次内部重试
    await collector.close()
    reparsed = await self._lightweight_reparse(collector.text, output_format, model)
    if reparsed is None:
        raise  # 进 L2（外层 except → _handle_error → OUTPUT_VALIDATION_FAILED）
    run_result = reparsed  # _ReparsedRunResult(final_output=recovered, usage=resp.usage)
```

**`_lightweight_reparse(text, output_format, model) -> _ReparsedRunResult | None`**：
- `text` 空 / `output_format` 空 → 返回 `None`（不重输）；
- 用 `self._get_client()` 发单个 `chat.completions.create`（`stream=False`）：
  - `model=model`；`messages=[{"role":"user","content": f"将以下分析结论转为符合 schema 的纯 JSON，只输出 JSON 本体，不要任何解释或 markdown：\n{text}"}]`；
  - `response_format={"type":"json_schema","json_schema":{"name":"shannon_reparse","schema":output_format,"strict":False}}`（non-strict，复用 `output_format`；GLM 后端接受度见 §6 风险，不接受则降级为纯 prompt + `_extract_json_payload`）；
  - **不注入 narration directive**（`_instructions()`），强制纯 JSON 输出；
- 拿 `resp.choices[0].message.content` → `_extract_json_payload` → `json.loads`；任一步失败 → 返回 `None`；
- 成功 → 返回 `_ReparsedRunResult(recovered_dict, resp.usage)`（带上 L1 真实 token，cost 仍走 GLM 0.0 早退，但 input/output_tokens 计入，避免统计失真）。

**`_ReparsedRunResult`**：最小 stub，含 `final_output`（= recovered dict）+ `context_wrapper.usage`（L1 chat completion 的真实 token），供 `map_run_result`（`openai_result_mapper.py:46` `isinstance(final, (dict, list))` 分支 + `_usage_from` 取 usage）直接产出 `structured_output` + token 统计。

**成本**：L1 仅在 L0 失败时触发，且只发 1 个 chat completion（无 agent loop、无工具调用），token 成本远低于 Temporal 重跑整个 agent 几十轮。

### 4.4 L2 Temporal 兜底 —— `errors.py` + `executor.py`

**`providers_openai._classify_error`**（`providers_openai.py:201`）：新增分支
```python
if isinstance(error, StructuredOutputParseError):
    return ("OutputValidationError", True)
```
`_handle_error`（`providers_openai.py:187`）据此设 `result.error_code = ErrorCode.OUTPUT_VALIDATION_FAILED`、`result.error = "structured output parse failed after L0+L1"`。

**`executor.py:126-131` 透传 error_code**：当前硬编码 `error_code=ErrorCode.AGENT_EXECUTION_FAILED` → 改为 `error_code=result.error_code or ErrorCode.AGENT_EXECUTION_FAILED`，让 provider 设的 `OUTPUT_VALIDATION_FAILED` 透传。

**`classify_error_for_temporal`**（`errors.py:126-127`）：**已就位**，`OUTPUT_VALIDATION_FAILED → ("OutputValidationError", True)`，无需改。Temporal 据此走 vuln 既有的 8 次重试（`retry.py:58`）。

### 4.5 数据流（端到端）

```
run_vuln_agent → run_agent（activities.py:191 传 _vuln_output_schema）
  → executor.execute（透传 structured_output_schema）
  → run_claude_prompt（output_format=...）
  → OpenAIProvider.call(output_format=...)
    → build_agent(output_type=RawJsonSchemaOutputSchema) → Runner.run_streamed
      → SDK turn_resolution.py:810 validate_json(final_output)
        ├ L0 成功 → final_output=dict → map_run_result → structured_output
        └ L0 StructuredOutputParseError
           ├ L1 _lightweight_reparse 成功 → _ReparsedRunResult → structured_output
           └ L1 None → re-raise → _handle_error → error_code=OUTPUT_VALIDATION_FAILED
  → executor: result.success=False → raise PentestError(error_code=OUTPUT_VALIDATION_FAILED)
  → classify_error_for_temporal → ("OutputValidationError", True) → Temporal vuln 8 次
  → structured_output 落盘 {vt}_exploitation_queue.json（executor.py:133-140，L0/L1 成功路径）
```

---

## 5. 测试策略

1. **L0 单测**（更新 `packages/core/tests/agents/test_openai_output_schema.py`）：
   - 现有 `test_validate_json_parses_valid_json` 不变；
   - `test_validate_json_raises_on_invalid` 仍通过（`StructuredOutputParseError` is-a Exception）；
   - 新增：` ```json{...}``` ` fence 包裹 → 解析成功；
   - 新增：前导叙述 + `{...}` → 解析成功（取首末花括号子串）；
   - 新增：纯叙述（无 `{`/`}`）→ 抛 `StructuredOutputParseError`；
   - 新增：`_extract_json_payload` 直接单测（各档输入 → 候选子串 / None）。
2. **L1 单测**（`packages/core/tests/agents/test_providers_openai_reparse.py` 新增）：mock `AsyncOpenAI.chat.completions.create`，验证：
   - L0 失败 → 触发 `_lightweight_reparse`；
   - GLM 重输合法 JSON → 恢复 structured_output；
   - GLM 仍不配合 → `_lightweight_reparse` 返回 None → re-raise `StructuredOutputParseError`；
   - `output_format=None` / `text` 空 → 不重输，直接 None。
3. **分类单测**（`test_providers.py` / `test_errors.py`）：`StructuredOutputParseError` → `OUTPUT_VALIDATION_FAILED` → `("OutputValidationError", True)`。
4. **executor 透传单测**（`test_executor_*.py`）：provider 返回 `error_code=OUTPUT_VALIDATION_FAILED` 时，executor 抛的 `PentestError.error_code` = `OUTPUT_VALIDATION_FAILED`（非硬编码 `AGENT_EXECUTION_FAILED`）。
5. **探针（堵 task-probe 盲区）**（`scripts/validate_openai_structured_output_probe.py` 新增，`validate_*_task_probe.py` 风格）：带 `output_type=RawJsonSchemaOutputSchema` 的顶层 agent，模拟 GLM final_output = fence JSON / 前导叙述+JSON / 纯叙述，验证三层防线逐级生效（fence/叙述 → L0/L1 恢复；纯叙述 → `OUTPUT_VALIDATION_FAILED` 而非 `AgentExecutionError`）。
6. **回归锚点**：`test_openai_result_mapper.py`（mapper fallback 保留作第二道防线）、`test_run_agent_vuln_schema.py`（schema 透传不变）、`test_dual_engine_alignment.py`（双引擎对齐）、`test_narration_injection.py`（L1 不注 narration 不影响主路径）。

**测试陷阱**（CLAUDE.md §3）：只跑改动相关测试文件，勿广跑全套（Temporal / 网络慢测试会 hang）。

---

## 6. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| **L1 的 `response_format` GLM 后端不接受**（`output_format` 是 Claude 风格 schema） | L1 重输的 response_format 约束失效 | 实现首步用探针验证 GLM 接受度；不接受则 L1 降级为纯 prompt「只输出 JSON」+ `_extract_json_payload`（仍优于无 L1） |
| **`collector.text` 含全部 turn 文本（含工具调用过程）**，喂给 L1 噪音多 | L1 重输可能受历史噪音干扰 | L1 prompt 明确「提取分析结论转 JSON」；实现期可优化为只取尾部 message_output（plan 阶段细化）；噪音不影响 L2 兜底 |
| **GLM final output 为空**（连叙述都没有） | `collector.text` 无 final 内容，L1 拿不到可转文本 | L1 `text` 空时直接返回 None 进 L2；属极端情况，Temporal 兜底 |
| **`StructuredOutputParseError` 冒泡路径被其他 except 误吞** | L1/L2 失效 | L1 在 `call()` 内层显式 `except StructuredOutputParseError`（在 `MaxTurnsExceeded` 之后、外层 `except Exception` 之前）；不继承 `ModelBehaviorError` 避免 SDK error handler 路径误吞 |
| **L1 额外 token 成本**（1 chat completion / 次 L0 失败） | 极少数 L0 失败的 agent 多花 1 turn | 远低于 Temporal 重跑整个 agent；可接受 |
| **mapper 死代码**（`openai_result_mapper.py:46-52` fallback 在 L0 修复后到不了） | 无害但冗余 | 保留作第二道防线（L0/L1 都返回 dict 时 mapper 走 `isinstance` 分支，fallback 永不触发但零成本）；不在本 spec 清理 |
| **契约反转错觉**（`test_validate_json_raises_on_invalid`） | 误以为改了抛错契约 | 实际仍抛（`StructuredOutputParseError` is-a Exception），测试无需删，仅补充 happy path |

---

## 7. 完成定义

- L0 `_extract_json_payload` + 容错 `validate_json` + `StructuredOutputParseError` 落地，单测全绿（fence / 前导叙述 / 纯叙述 / happy path）；
- L1 `_lightweight_reparse` + `call()` 恢复分支 + `_ReparsedRunResult` 落地，单测全绿（恢复 / 仍失败 / 空输入）；
- L2 `_classify_error` 识别 + `executor.py` error_code 透传落地，单测全绿；
- 探针 `validate_openai_structured_output_probe.py` 在 glm-openai 真机验证三层防线逐级生效（fence/叙述 → 恢复；纯叙述 → `OUTPUT_VALIDATION_FAILED`）；
- 回归锚点不退化（mapper / vuln schema / 双引擎对齐 / narration）；
- 改动相关测试绿（只跑改动文件，不广跑全套）；
- 真机冒烟（随 feat/fork-py）：重跑 `invite_code_center` 白盒，injection-vuln / xss-vuln 不再因 char 0 失败（L0/L1 恢复或 L2 受控重试，不再 8 次徒劳）。

---

## 8. 与相关 spec 的关系

- **[2026-06-29 黑盒 exploit 产物结构化校验护栏](./2026-06-29-blackbox-exploit-structured-output-design.md)**：本修复是**更底层的前置**。该 spec §6 假设「LLM 不产 structured_output → `result.structured_output=None` 优雅降级」，但该降级路径在 openai 引擎下因 `validate_json` 零容错而坏（本 bug 证明）。本修复落地后，该假设才成立；blackbox exploit spec 未来落地自动受益。两者范围不重叠（本修复只动 core 的 openai 引擎 structured output 解析；blackbox spec 动 blackbox exploit 产物管线）。
- **[2026-06-17 openai-agents 引擎设计](./2026-06-17-openai-agents-engine-design.md)**：本修复在其落地的 `RawJsonSchemaOutputSchema`（B2 双引擎解耦修复）之上补解析韧性，不改引擎抽象。
- **TS 对齐**：本修复对齐 TS 的「SDK 接管 LLM→JSON 契约 + 单次内部重试 + Temporal 外层兜底」三层精神，用 L0（容错解析）+ L1（provider 轻量重输）+ L2（Temporal）对应 TS 的 SDK 内部接管 + SDK 内部多次重试 + Temporal 外层 3 次。
