# 白盒 Live 显示 UX 优化 — 设计

- **日期**：2026-06-27
- **分支**：`feat/fork-py`
- **状态**：Design（待 writing-plans）
- **范围**：`packages/whitebox`、`packages/core`（显示 / 日志 / agent runner 层）

## 1. 背景

一次 NodeGoat 白盒实测（`uv run shannon-whitebox start`）暴露三个 live 显示 UX 问题：

1. **突兀的堆栈**：XSS vuln agent 第 1 次失败时，终端在 shannon-py 自己干净的 `[ERROR]` 行之后，又紧跟着打印了 ~15 行 Python 链式 traceback，用户以为"程序坏了"。实际该失败是瞬态（GLM 代理限流），被 Temporal 正确重试、第 2 次成功，扫描最终 COMPLETED。
2. **中英混杂的 💭 Turn 行**：`💭 [Agent] Turn N: …` 是模型（GLM）自发输出，prompt 全英文，GLM 随意中英切换。
3. **自相矛盾的失败标签**：失败行显示 `[TransientError · non-retryable]`，但实际却被重试了。

三者根源都已定位（见下）。本设计做三处**互相独立**的 UX 修正（Part A / B / C），可分开实现与测试。

## 2. 目标 / 非目标

**目标**
- A：activity 失败的详细 traceback 不再上终端，重定向到 per-workspace 日志文件；终端只留干净的 ERROR 行 + "堆栈见日志"指向；致命错误仍可查。
- B：让 agent 的口述（💭 Turn）与交付物**散文**倾向中文；代码消费的结构/受控词/标题保持英文，不破坏下游。
- C：失败标签与真正驱动 Temporal 重试的判定**同源**，准确标注"将重试 (N/M)"/"不可重试"。

**非目标（YAGNI）**
- 本地化 `FindingsRenderer` 写死的英文标签（`**Summary:**` 等）——更大改动，另议。
- 交付物语言的后置校验/告警——已选 best-effort。
- 修复 Rust core 那行 shutdown warning（`temporalio_sdk_core::worker`）——可选，文档提一句 `RUST_LOG=error` 即可，本 spec 不强制。
- 删除 `errors/classification.py`（Part C 后可能成死代码）——留 follow-up，不强制。

## 3. Part A — 失败堆栈重定向到 per-workspace 日志

### 3.1 根因
`temporalio` 1.27.2 在每个 activity 失败时执行：
`temporalio/worker/_activity.py:474` →
`temporalio.activity.logger.warning("Completing activity as failed", exc_info=True, …)`。

- `temporalio.activity.logger` = `LoggerAdapter(logging.getLogger("temporalio.activity"), None)`（`temporalio/activity.py:536`）。
- shannon-py **没有任何 logging 配置**（无 `basicConfig`/`dictConfig`），故该记录经 root logger 的 `lastResort` StreamHandler 直冲 stderr，连同 `exc_info` 的全链 traceback。
- 这与 shannon-py 自己的 display（`workflow_logger.log_error` → `ErrorEvent` → `rich_renderer._render_error`）渲染的干净 `[ERROR]` 行**重复且更吓人**。

### 3.2 机制
在 worker 启动时给 `logging.getLogger("temporalio.activity")` 装一个 per-workspace `FileHandler` 并 `propagate=False`——记录（含 `exc_info` traceback）只进文件、不上终端。

### 3.3 组件 / 接缝
- 新增 `install_temporalio_log_redirect(logs_dir: Path) -> Path`（放 `shannon_core.logging`）：
  ```python
  logger = logging.getLogger("temporalio.activity")
  logger.propagate = False
  logger.setLevel(logging.DEBUG)            # 不在 logger 层过滤
  fh = logging.FileHandler(logs_dir / "activity_failures.log")
  fh.setLevel(logging.WARNING)              # 只收 warning+（失败 traceback；丢弃 debug 心跳噪声）
  fh.setFormatter(logging.Formatter(
      "%(asctime)s %(levelname)s %(name)s: %(message)s"))  # 标准 Formatter 自动追加 exc_info traceback
  logger.addHandler(fh)
  return logs_dir / "activity_failures.log"
  ```
  - **幂等**：若已存在指向同路径的 FileHandler 则不重复添加（resume 场景）。
  - 返回日志路径，供 ERROR 行指向。
- **调用点**（每个 Worker 构造处，统一调上面的 helper，workspace 路径已知）：
  - `packages/whitebox/src/shannon_whitebox/worker.py:66 run_scan`：workspace = `ws_path`（line 81 创建），`logs_dir = ws_path / "logs"`；在起 worker 前 install。
  - `packages/core/src/shannon_core/runtime/scan_runner.py:142 run_scan_graceful`：从 `workflow_input` 解析 workspace，install。
- **ERROR 行加指向**：
  - `ErrorEvent`（`packages/core/src/shannon_core/display/events.py:75`）增字段 `detail_path: str | None = None`。
  - `rich_renderer.py:117 _render_error` 与 `file_renderer.py:99` 末尾：`if e.detail_path: line += f"  (详细堆栈见 {e.detail_path})"`。
  - `workflow_logger.py:140 log_error`：把 redirect 安装时返回的日志路径填入 `detail_path`。接线：`install_temporalio_log_redirect` 返回路径 → 调用方（`run_scan`）注入 session 的 `WorkflowLogger`（新增 `activity_failure_log_path` 属性，`log_error` 读取它填 `detail_path`）。

### 3.4 数据流
activity 失败 → temporalio 打 `warning + exc_info` →（`propagate=False`）只到 FileHandler → 落 `activity_failures.log`；同时 shannon-py 照常渲染干净 `[ERROR] … (详细堆栈见 …)`。**终端零 traceback**。

### 3.5 错误处理
- install 本身用 try/except 包：失败只 `logger.warning`、不阻断扫描（回退到旧行为：traceback 上终端）。
- FileHandler 写失败（磁盘满等）也降级，不崩 worker（FileHandler 自身有 `emit` 异常处理， handleError 默认不打断）。

## 4. Part B — Agent 口述与交付物散文倾向中文（分语境）

### 4.1 根因
`💭 [Agent] Turn N: …` 由 `rich_renderer.py:111 _render_llm` 渲染模型当轮流式文本首行（`e.content`）。prompt 全英文（`prompts/vuln-*.txt`、`recon.txt`），GLM 自发中英切换，shannon-py 不干预。

### 4.2 机制
在 runner/provider 层注入一段**系统提示**语言指令（不动 `prompts/*.txt`）。**分语境**：口述 + 人读散文用中文；代码消费的结构/受控词/标题留英文。

下游消费事实（已核实，决定"分语境"边界）：
- `FindingsRenderer`（`findings_renderer.py:39-107`）：英文标签 `**Summary:**`/`- **Vulnerable Location:**` 等是**代码写死模板**，LLM 只填**值** → 标签恒英文（代码控制），LLM 填的**值可中文**。
- `ReportAssembler`（`report_assembler.py:72`）：按字面 `## Executive Summary` 英文标题识别章节 → **结构性标题必须英文**；标题下正文可中文。
- `queue_schemas`（`queue_schemas.py:9,11`）：`vulnerability_type`/`confidence` 是裸 `str`，pydantic **不校验枚举**；但下游按英文受控词匹配（如 `activities.py` `_CATEGORY_TO_VULN_TYPE`）→ **受控词字段值必须英文**。

### 4.3 指令定稿
```
<language>
- 用中文进行所有口述、推理过程与每轮总结（narration）。
- 人读散文用中文：notes / exploitation_hypothesis / missing_defense /
  evidence_chain 的叙述、报告正文、执行摘要正文。
- 以下必须保持英文（代码解析/匹配）：JSON 字段名、代码/文件路径/命令/ID；
  受控词汇字段的"值"——vulnerability_type、confidence、
  suggested_exploit_technique 等保持 prompt 给定的英文枚举；
  结构性 Markdown 标题，尤其 "## Executive Summary"。
</language>
```

### 4.4 组件 / 接缝
- 新增 `narration_directive() -> str | None`（新模块 `shannon_core/agents/narration.py`）：读 env `SHANNON_AGENT_NARRATION_LANG`（默认 `"zh"`=开，`"en"`=关），返回上述指令或 `None`。**单一字符串源**，两引擎 provider 各自调用。
- **claude-agent-sdk 轨**（`providers_anthropic.py:222 _build_options`）：指令存在时
  `options.system_prompt = {"type": "preset", "append": directive}`。
  - 已核实：SDK `subprocess_cli.py:235-238` 把该 preset 映射到 CLI `--append-system-prompt`，**真·系统提示位追加**，不替换 base system prompt（`append_system_prompt` 字段在此 SDK 版本不存在，`system_prompt` 字符串形式会整体替换，故必须用 preset/append 形式）。
- **openai 轨**（`providers_openai.py:77,99`）：当前 `Agent(instructions=None, …)`（prompt 整段当 user input）。指令存在时 `instructions=directive`（Agent instructions 即 openai-agents 的 system message）。
- **范围**：全 agent（pre-recon / recon / 5×vuln / report）。

### 4.5 预期（已与用户确认）
- FindingsRenderer 渲染的漏洞条目段呈「英文标签 + 中文值」（标签代码写死，本 spec 不动）。
- report agent 的分析/执行摘要段为中文正文（标题保留英文）。
- best-effort：GLM 偶尔蹦英文可接受。prompt 本身用英文枚举词（如 `vulnerability_type: Session_Management_Flaw`），模型倾向照抄英文，受控词泄漏风险低。

## 5. Part C — 失败标签与重试判定同源

### 5.1 根因
失败标签由 `workflow_logger.py:140 log_error` 用 `shannon_core.errors.classification` 的两个**字符串嗅探**函数算：
- `classify_for_temporal(error)` → 按 `str(error)` 关键词匹配，无匹配兜底 `TransientError`；
- `is_retryable_for_display(error)` → 同样嗅探，**兜底 False（fail-safe）**；两者兜底语义**相反**。

而**真正驱动 Temporal 重试**的是 `activities.py:148` 的 `classify_error_for_temporal(e)`（`models/errors.py:94`），按 **`error_code`**（Level 1）映射：`AGENT_EXECUTION_FAILED` → `("AgentExecutionError", error.retryable)`，作为 `ApplicationFailure` 的 `type`；`"AgentExecutionError"` 不在 `VULN_RETRY.non_retryable_error_types` 内 → 重试。

两套分类脱钩，故失败消息为空泛的 `"Agent xss-vuln execution failed"`（`executor.py:124` fallback）时，字符串嗅探打出自相矛盾的 `[TransientError · non-retryable]`，实际却被重试。

### 5.2 机制
让显示标签用**同一个** `models/errors.py:classify_error_for_temporal`（与 `ApplicationFailure` 同源），并友好化为"将重试 (N/M)"/"不可重试"。

### 5.3 组件 / 接缝
- `workflow_logger.py:140 log_error`：把 `from shannon_core.errors.classification import classify_for_temporal, is_retryable_for_display` 换成 `from shannon_core.models.errors import classify_error_for_temporal`；
  `etype, retryable = classify_error_for_temporal(error)`；
  `ErrorEvent(classified=etype, display_retryable=retryable, …)`。
  - 对 `PentestError`（带 `error_code`）走 Level 1 准确映射；对裸 `Exception`（`except Exception` 路径）走 Level 2 字符串兜底——与 activities.py 行为一致。
- **attempt 透传**：`activities.py run_agent` 已有 `attempt = activity.info().attempt`（line 101）；新增经 `log_error(..., attempt=attempt)` → `ErrorEvent.attempt: int | None`。
- **M（max_attempts）**：`models/retry.py` 新增 `agent_retry_category(agent_name) -> Category`（单一映射：vuln→`vuln`、recon/pre-recon/report→`standard`、setup→`preflight`/`auth-validation`、log marker→`log`）；`run_agent` 调 `retry_for(agent_retry_category(agent_name)).maximum_attempts` 得 M，一并透传 → `ErrorEvent.max_attempts`。`workflows.py` 的 `retry_for("vuln"/"standard")` 硬编码可改用该 helper（DRY，消除漂移），属可选改进。
- **渲染**（`rich_renderer._render_error` / `file_renderer`）：
  - `retryable=True` → `[<type> · 将重试 <attempt>/<max_attempts>]`
  - `retryable=False` → `[<type> · 不可重试]`

### 5.4 数据流
activity 失败 → `run_agent` 捕获 PentestError → `classify_error_for_temporal(e)`（同源）→ 同时：① `ApplicationFailure(type, non_retryable)` 给 Temporal 决定重试；② `log_error(e, attempt, max)` → `ErrorEvent` → 渲染准确标签。终端/文件标签与真实重试**永远一致**。

## 6. 横切：CLAUDE.md 不变量

- **双轨铁律不动**：Part B 是**语言指令**，在 system-prompt / instructions 层注入，**不 `@include` 任何确定性产物、不改 `prompts/*.txt` 内容、不建确定性→LLM 轨数据桥梁**。锚点测试 B4 锁定"`prompts/*.txt` 不含指令文本"。
- **双引擎一致**：Part B 在 claude 与 openai 两条轨各自用原生缝注入同一份 `narration_directive()`，两引擎行为一致。
- **改 agent/工具行为后实测**：Part B 改了 system-prompt 注入，落地后用 `scripts/validate_*_task_probe.py` 在两引擎冒烟。

## 7. 测试

**Part A**
- A1：`install_temporalio_log_redirect(tmp)` 后向 `temporalio.activity` 发带 `exc_info` 的 WARNING 记录 → 断言写入 `activity_failures.log`（含 traceback）、且**不**出现在捕获的 stderr（`propagate=False`）。
- A2：DEBUG 心跳记录**不**进文件（handler level=WARNING）。
- A3：`ErrorEvent.detail_path` 有值时 `_render_error` 追加 `(详细堆栈见 …)`；幂等：重复 install 不加重复 handler。

**Part B**
- B1：`narration_directive()`：env=`zh` 返回指令、env=`en` 返回 None、未设=zh（默认开）。
- B2（安全锚点）：指令串断言含 `vulnerability_type`、`## Executive Summary`、英文约束——锁"结构/受控词/标题留英文"。
- B3：`_build_options` 指令存在时 `options.system_prompt == {"type":"preset","append":<指令>}`；不存在时该字段保持 unset。
- B4（防回退锚点）：`prompts/*.txt` 全体不含该指令文本（语言指令只在 system 层注入）。

**Part C**
- C1：`PentestError(error_code=AGENT_EXECUTION_FAILED, retryable=True)` 经 `log_error` → 标签为 `AgentExecutionError · 将重试 …`（不再 TransientError/non-retryable）。
- C2：`retryable=False`（如 `AUTH_FAILED`）→ 标签含"不可重试"。
- C3：`attempt`/`max_attempts` 透传断言（如 `2/5`）。
- C4：标签 type 与 `activities.py` 实际抛的 `ApplicationFailure.type` 一致（同源校验）。

**注意**：仅跑改动相关测试文件（CLAUDE.md：全套 pytest 有预存 hang/失败）。

## 8. 风险 / Follow-up

- Part B 受控词泄漏：低（prompt 用英文枚举，模型倾向照抄）；若实测发现 `vulnerability_type` 被写中文导致下游漏匹配，再加后置校验（当前 best-effort 不做）。
- Part C `agent_retry_category` 映射需与 `workflows.py` 实际 `retry_for` 调用保持一致；若映射易漂移，M 可降级为仅显示 attempt（`第N次`）。`errors/classification.py` 两函数 Part C 后若它处无用即死代码，可删（follow-up）。
- Part A 文件路径相对显示：ERROR 行指向用相对路径（`<workspace>/logs/activity_failures.log`）便于阅读。

## 9. 实现顺序建议（供 writing-plans）

三块独立，建议顺序 C → A → B（C 最小且独立，A 中等，B 涉及两引擎冒烟）。每块独立 TDD + 冒烟。
