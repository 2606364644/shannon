# 双引擎解耦修复设计（shannon-py）

**Date:** 2026-06-27
**Status:** Pending Review
**分支：** `feat/fork-py`
**本文件性质：** 针对 shannon-py 双引擎（claude-agent-sdk / openai-agents）解耦审计发现的「契约缺口 + 返回值语义不对齐 + 无对齐测试护栏」问题，给出完整闭环修复设计。所有结论已对代码逐条核验（§1 带行号锚点）。

**双引擎消费模型（canonical，见 CLAUDE.md §2）：**
- 业务侧（whitebox/blackbox/core）只调 `run_claude_prompt` → `create_provider(config)` → `provider.call()` → 统一返回 `ClaudeRunResult`，**不感知底层引擎**。
- `BaseProvider`(ABC) + `create_provider()` 工厂 + `ClaudeRunResult` 是抽象层三件套。
- 引擎经 `SHANNON_AI_PROVIDER` / profile 切换：`anthropic_api`（glm-anthropic，Claude Code CLI）/ `openai_compatible`（glm-openai，openai-agents Chat Completions）。

本 spec 的改动**只作用于 `agents/` 集成层**（provider 实现 + 抽象层 + 集成层测试），不改业务侧、不改 prompt、不破坏双轨独立性。

---

## 0. 一句话结论

双引擎的**编排骨架解耦是合格的**（SDK 零泄漏、统一入口、工厂集中、两实现隔离），但**统一返回类型 `ClaudeRunResult` 这个契约在 OpenAI 侧没被完整实现**，且**没有对齐测试把「两引擎语义一致」锁成不变量**。具体三处会让结果「静默失真」的真 bug：① OpenAI 侧 `success` 在跑满 max_turns 时仍为 `True`；② OpenAI 侧 `structured_output` 裸奔（`build_agent` 丢弃 `output_format`，纯靠 `json.loads(text)` 兜底）；③ OpenAI 侧 `error_code` 恒 `None`。另有一处类型契约硬伤：`AnthropicProvider` 未继承 `BaseProvider`。修复 = 契约硬化 + P0 语义对齐 + 双引擎对齐测试护栏 + openai 真机冒烟闭环。

---

## 1. 问题核实（逐条映射，已对代码核验）

### 1.1 类型契约硬伤：AnthropicProvider 未继承 BaseProvider

| 项 | 位置 | 现状 |
|---|---|---|
| `BaseProvider(ABC)` 定义 | `providers.py:59` | 有 `@abstractmethod call()`（:67-90）+ `_is_retryable_error` 默认实现（:100） |
| `OpenAIProvider(BaseProvider)` | `providers_openai.py:32` | ✅ 正确继承，`__init__` 调 `super().__init__(config)` |
| `class AnthropicProvider:` | `providers_anthropic.py:37` | ❌ **未继承**，`:40-42` 手动 `self.config=config; self.type=config.type`（鸭子类型） |

**危害：** `create_provider()` 返回注解是 `BaseProvider`，但 AnthropicProvider 实例 `isinstance(x, BaseProvider)` 为 `False`，不满足 Liskov 替换。ABC 的 `@abstractmethod` 运行时约束、基类新增方法时的实例化报错机制对它全失效。两引擎在「是否受抽象层约束」上不对等。

### 1.2 P0-① `success` 在 OpenAI 侧恒 True（跑满 max_turns 被当成功）

`success` 是**重度上游消费**字段（非边角）：
- 编排决策：`whitebox/activities.py:324,388`、`blackbox/activities.py:106` 的 `if not result.success`；`executor.py:121`
- metrics/audit：`metrics_tracker.py:81,92`、`audit/session.py:82`、`workflow_logger.py:111`
- 显示/状态：`rich_renderer.py:100,142`（OK/FAIL）、`file_renderer.py:118`、`dashboard_state.py:103`（done/failed）、`formatters.py:246`
- code_index：`code_index/__init__.py:101`

**现状：** `openai_result_mapper.py:47` 的 `map_run_result` 硬编码 `success=True`。OpenAI 侧只有 `call()` 的 `except Exception` 走 `_handle_error`（`success=False`）才报失败；而 `MaxTurnsExceeded` 在 `providers_openai.py:147-150` 被捕获后转成 `_MaxTurnsStub` + `stop_reason="max_turns"` 进入正常 `map_run_result` 流 → **`success=True`**。

**后果：** openai 引擎下「跑满 max_turns 没出结果」被当成成功 → 活动不重试、报告标 OK、dashboard 标 done、metrics 计入 success。

**对齐目标：** Claude 侧 `providers_anthropic.py:389-390` 对 `subtype=="error_max_turns"` 返回 `("ExecutionLimitError", False)`，且 `:339` `success = not (is_error or subtype.startswith("error_"))` → success=False。OpenAI 侧应对齐为 `success=False, error_code="ExecutionLimitError", retryable=False`。

### 1.3 P0-② `structured_output` 在 OpenAI 侧裸奔

`structured_output` 上游**强依赖**：
- `validate_authentication.py:143-144`：`verdict = metrics.structured_output`（auth verdict 直接取它，None 即判定失败）
- `whitebox/activities.py:236,243`：vuln 链读 `result.structured_output`
- `multi/orchestrator.py:179-183`：强制单 edge JSON 输出

**现状：**
- Claude 侧 `providers_anthropic.py:241-242` `options.output_format = output_format` 真的喂给 SDK 强制结构化输出；`:331-332` 从 `result_message.structured_output` 原生取值。
- OpenAI 侧 `build_agent`（`providers_openai.py:74-82`）**接收 `output_format` 参数但完全没用它**——没传 `output_type` / `response_format` 给 `Agent`。`map_run_result`（`openai_result_mapper.py:39-43`）靠 `json.loads(text)` 兜底解析。

**后果：** openai 引擎下 Agent 没被约束输出 JSON，纯靠模型自觉。模型若输出 markdown ```json``` 包裹或带前后说明文字 → `json.loads` 失败 → `structured_output=None` → **auth verdict 静默失败**。比 1.2 更隐蔽（1.2 至少有 max_turns 路径，structured_output 是纯靠运气）。

### 1.4 P0-③ `error_code` 在 OpenAI 侧恒 None（已降级，非重试失效）

**纠正性发现：** Temporal retry 主要靠**异常类型**（`models/errors.py:102-135` 的 `classify_error_for_temporal` isinstance 链 + `models/retry.py` 的 `retry_for` 映射），**不靠** `ClaudeRunResult.error_code`。所以 OpenAI 侧 `error_code=None` 不会让重试失效。

**但仍有精度损失：** Claude 侧 `providers_anthropic.py:134-138` 用 `_classify_result_failure`（`:375-411`，三层：SDK subtype / HTTP status / 文本兜底）填 `error_code`；OpenAI 侧 `_handle_error`（`providers_openai.py:164-174`）不设 `error_code`（恒默认 None）。影响：workflow state（`workflows.py:458/408` 的 `error_code` 字段）在 openai 引擎下分类粗化。

### 1.5 无双引擎对齐测试（护栏缺失的根源）

`test_providers.py` 中 `TestAnthropicProvider`（:327）与 `TestOpenAIProvider`（:510）**完全独立**：各自 mock 各自 SDK（`claude_agent_sdk.query` vs `Runner.run_streamed`），无共享 fixture、无「同一结果场景两引擎对照」。`test_integration.py`（whitebox/blackbox）全部 `patch run_claude_prompt` 返回固定 mock，**从未用 `OpenAIProvider` 真跑过业务流程**。语义不对齐因此能长期潜伏。

### 1.6 已排除的「假问题」（避免 YAGNI，勿动）

- **`cost=0.0` 不破坏 spending-cap 兜底：** `utils/billing.py:27 is_spending_cap_behavior(turns, cost, text)` 的逻辑是 `turns>2→False` / `cost>0→False`（有花费=正常消费）/ 再走 text 关键词。`cost=0` 时**不触发** `cost>0` 早退，继续走 text 匹配 → 对 openai 安全。cost 仅影响 metrics 计费显示。
- **openai 不需补 Claude 专属 spending-cap 三层：** Claude 的 dispatcher/behavioral/exception 三层（`message_dispatcher.py` + `providers_anthropic.py:145-159,423-451`）是 CLI 事件流专属；provider 无关的 `utils/billing.is_spending_cap_behavior` 已覆盖 text 关键词场景，openai 复用它即可。

---

## 2. 设计

### 改动 A：契约硬化（低成本前置）

#### A1 AnthropicProvider 继承 BaseProvider
**落点：** `providers_anthropic.py:37,40-42`。
- `class AnthropicProvider(BaseProvider):`
- `__init__` 改调 `super().__init__(config)`，删除手动 `self.config = config; self.type = config.type`（基类 `providers.py:62-63` 已做）。
- 验证：`_is_retryable_error` 是否与基类默认实现语义一致；若 AnthropicProvider 已自定义同名方法（`providers_anthropic.py:463`），保留 override（合法）。

#### A2 ClaudeRunResult 字段语义不变量文档化
**落点：** `runner.py:76-88`（`ClaudeRunResult` dataclass）+ `providers.py:67-90`（`BaseProvider.call` docstring）。
写明 provider 实现义务（作为注释/docstring）：
- `success`：必须真实反映完成/失败；跑满 max_turns / 异常 → `False`。
- `error_code` + `retryable`：必须分类（成功路径 None/True；max_turns → `("ExecutionLimitError", False)`；限流/超时 → `(RateLimitError|TimeoutError, True)`；鉴权 → `(AuthenticationError, False)`）。
- `structured_output`：当调用方传入 `output_format` 时，provider 有义务产出非 None（走原生结构化输出，不靠 json.loads 兜底）。
- `cost`：尽力归集；不支持计费的 provider 允许 0.0（spending-cap 兜底不受影响）。

### 改动 B：P0 语义对齐（核心）

#### B1 success / error_code 在 max_turns 对齐
**落点：** `openai_result_mapper.py:25-55`（集中改）+ `providers_openai.py:147-150`（call 传参）。
- `map_run_result` 增加：当 `stop_reason=="max_turns"` 时 → `success=False, error_code="ExecutionLimitError", retryable=False`（其余路径维持 `success=True`）。
- 或在 `call()` 的 `except MaxTurnsExceeded` 分支 map 后覆盖（二选一，**优先 mapper 内集中判定**，便于测试）。
- 同步更新测试：`test_providers.py:566 test_call_handles_max_turns`（补 `assert res.success is False` + `error_code=="ExecutionLimitError"` + `retryable is False`）；`test_openai_result_mapper.py test_map_stop_reason_max_turns` 同步。

#### B2 structured_output 走原生 output_type（用户已选定）
**落点：** `providers_openai.py:74-82 build_agent` + `openai_result_mapper.py:39-43`。
- `build_agent` 把 `output_format`（JSON Schema dict）转成 openai-agents `Agent(output_type=...)` 能接受的形式，让 openai-agents 强制模型输出合规 JSON。
- **转换方式（plan 阶段验证选定）：** 候选 (a) `pydantic.create_model` 动态建 Pydantic 模型；(b) 若 openai-agents / `OpenAIChatCompletionsModel` 暴露 `response_format={"type":"json_schema",...}`，直接透传 JSON Schema。
- `final_output` 为 Pydantic 实例时 → `structured_output = model_dump()`；为 str 时仍 `json.loads` 兜底（保留容错）。
- `map_run_result` 的 `output_format` 分支保留作 fallback，但主路径走原生。

#### B3 error_code 分类补齐（OpenAI 异常路径）
**落点：** `providers_openai.py:164-174 _handle_error`。
- 补 `error_code`：复用现有 `_is_retryable_error`（`:176`）的关键词分类，同时产出 `error_code`（rate_limit→`RateLimitError`、timeout→`TimeoutError`、auth→`AuthenticationError`、其余→`AgentExecutionError`）。
- 不照搬 Claude 三层（YAGNI），只对齐「关键几类」即可。

### 改动 C：天然差异显式标注（不强行对齐）

#### C1 cost=0 保留 + 注释
**落点：** `openai_result_mapper.py:50`。
保留 `cost=0.0`，注释升级为：「GLM/openai endpoint 不支持计费归集；`is_spending_cap_behavior` 的 `cost>0→False` 早退逻辑使 0 值对 spending-cap 兜底无害（见 `utils/billing.py:31`）」。

#### C2 spending-cap best-effort 写明
在 A2 不变量里注明：「spending-cap 检测是 best-effort，provider 可差异；openai 复用 provider 无关的 `utils/billing.is_spending_cap_behavior`，不补 Claude CLI 专属的 dispatcher/behavioral 层」。

### 改动 D：双引擎对齐测试护栏（防回退）

**落点：** 新建 `packages/core/tests/agents/test_dual_engine_alignment.py`。

#### D1 公共「结果场景」fixture
定义场景枚举（每个场景给出两 provider 的 mock SDK 行为 + 期望 `ClaudeRunResult` 字段）：
- `SUCCESS`：正常返回文本 / 结构化输出
- `MAX_TURNS`：跑满 max_turns
- `RATE_LIMIT`：限流异常
- `TIMEOUT`：超时异常
- `STRUCTURED_OUTPUT`：传入 output_format，期望 structured_output 非 None

#### D2 parameterized 对齐断言
对每个场景，分别用 mock 驱动 `AnthropicProvider.call` 与 `OpenAIProvider.call`，断言两者产出的 `success` / `error_code` / `retryable` / `structured_output is not None` **语义一致**（值或等价类相等）。

#### D3 isinstance 契约锁定
`assert isinstance(AnthropicProvider(cfg), BaseProvider)` + `assert isinstance(OpenAIProvider(cfg), BaseProvider)`（锁 A1 不变量）。

### 改动 E：openai 真机冒烟闭环

**落点：** `scripts/validate_openai_task_probe.py`（已就绪，122 行，完整 PASS/FAIL 判定）。
- 跑该脚本验证 GLM 经 openai-agents 能驱动 `task` function_tool（子代理委派）。
- **同时验证 B2：** 真机确认 GLM openai endpoint 是否支持结构化输出（`response_format` json_schema / openai-agents `output_type`）。
- **降级策略：** 若 GLM 不支持结构化输出 → B2 降级为「`json.loads` 容错（提取 ```json``` 块、剥离说明文字）+ prompt 强化『必须输出纯 JSON』」，并在 spec follow-up 记录。
- **执行约束：** 需 `.env.profiles/glm-openai.env` 真实凭证 + 网络访问 GLM endpoint。实施时与用户确认执行方式（agent 跑 / 用户人工跑）。

---

## 3. 不变量与防回退

1. **SDK 隔离不变量：** `claude_agent_sdk` / `openai` / `agents` 的 import 仍只在 `agents/` 目录内（业务侧零泄漏）。本次改动不新增跨目录 SDK import。
2. **双引擎契约不变量（D2/D3 锁定）：** 两 provider 对同类结果场景产出语义一致的 `ClaudeRunResult` 关键字段；且都 `isinstance BaseProvider`。
3. **structured_output 可靠性不变量：** 传入 `output_format` 时，两 provider 的 `structured_output` 都非 None（D1 STRUCTURED_OUTPUT 场景锁定）。
4. **不破坏双轨独立性：** 本 spec 只动 `agents/` 集成层，不碰 prompt、不碰确定性层、不碰合并器。

---

## 4. 测试策略

- **单元（改/新增）：** `test_openai_result_mapper.py`（B1 max_turns success）、`test_providers.py`（B1 call max_turns、B3 error_code、A1 isinstance）、`test_dual_engine_alignment.py`（D1/D2/D3 新建）。
- **只跑改动相关子集：** 遵循 memory `pytest-whitebox-hang`，不跑全套（会卡 Temporal/网络慢测试）。
- **真机（E）：** `validate_openai_task_probe.py`，人工/agent 冒烟。

---

## 5. 风险与降级

| 风险 | 影响 | 降级 |
|---|---|---|
| B2 openai-agents `output_type` 对动态 JSON Schema 支持不确定 | structured_output 仍裸奔 | plan 阶段先验证转换方式；真机不支持则降级 json.loads 容错（§2.E） |
| GLM openai endpoint 不支持 `response_format` 结构化输出 | B2 原生路径无效 | 降级 json.loads + prompt 强化（§2.E） |
| B1 改 `map_run_result` 影响现有 mock 测试 | 测试红 | 已核查 `test_map_plain_text`(success=True 正常路径不受影响)、`test_call_handles_max_turns`(只断言 stop_reason，需同步补断言) |
| 真机冒烟需凭证/网络 | E 无法在当前会话跑 | 留作用户人工跑；脚本就绪 |

---

## 6. 范围边界（不做什么）

- **不**改业务侧（whitebox/blackbox activities、workflows）——它们已引擎无关。
- **不**改 prompt、确定性层、合并器——不动双轨。
- **不**给 openai 补 Claude 专属 spending-cap 三层（YAGNI，§1.6）。
- **不**强行填 `cost`（GLM 定价未知，0 值无害，§1.6）。
- **不**重命名 `run_claude_prompt` / `ClaudeRunResult`（命名残留但 CLAUDE.md 全程沿用，改名是独立大改，超出本 spec）。
- **不**引入 provider capability 新抽象（capability 模型属过度设计；`ClaudeRunResult` 已是统一契约，只需补齐实现 + 加护栏）。

---

## 7. 落点清单（供 writing-plans）

| 改动 | 文件 | 行号 |
|---|---|---|
| A1 | `providers_anthropic.py` | 37, 40-42 |
| A2 | `runner.py` / `providers.py` | 76-88 / 67-90 |
| B1 | `openai_result_mapper.py` / `providers_openai.py` | 25-55 / 147-150 |
| B2 | `providers_openai.py` / `openai_result_mapper.py` | 74-82 / 39-43 |
| B3 | `providers_openai.py` | 164-174 |
| C1 | `openai_result_mapper.py` | 50 |
| D | `tests/agents/test_dual_engine_alignment.py`（新建） | — |
| D 同步 | `tests/agents/test_providers.py` / `test_openai_result_mapper.py` | 566 / max_turns 用例 |
| E | `scripts/validate_openai_task_probe.py`（已就绪，跑） | — |
