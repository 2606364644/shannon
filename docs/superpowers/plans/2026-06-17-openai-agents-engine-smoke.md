# OpenAI 引擎手动冒烟 checklist

> 本 checklist 整合了 Task 1-9 review 中积累的所有 Minor / ⚠️ 项 + 基础冒烟。
> agent loop 集成测全程用 mock Runner，真实 GLM 事件流只能靠手动冒烟验证。
> memory：pytest 全量会 hang，集成验证走手动冒烟，不写自动全量测试。

## 前置（.env 切到 OpenAI 引擎）

```bash
SHANNON_AI_PROVIDER=openai_compatible
SHANNON_OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4   # 智谱 OpenAI 兼容端点（核对文档为准）
SHANNON_OPENAI_API_KEY=<glm-key>                               # 缺失时回退 SHANNON_API_KEY / OPENAI_API_KEY
SHANNON_LARGE_MODEL=GLM-5.2[1m]
SHANNON_MEDIUM_MODEL=GLM-5.2[1m]
SHANNON_SMALL_MODEL=GLM-4.5-Air
# SHANNON_OPENAI_MAX_TURNS=200（默认，对齐 AnthropicProvider 的 CLAUDE_MAX_TURNS）
```

## 1. 最小冒烟（单 agent，验 loop + tool calling）
- [ ] 跑一个会触发 `bash` 工具的简单 agent（如 pre-recon 子步），确认：
  - [ ] `ClaudeRunResult.success == True`
  - [ ] `ClaudeRunResult.turns > 1`（证明 tool use loop 跑了多轮）
  - [ ] audit 落库含 `log_tool_start("bash", ...)` + `log_tool_end(...)` + `log_assistant_turn`
  - [ ] `ClaudeRunResult.tokens.input_tokens/output_tokens > 0`
- [ ] `ClaudeRunResult.cost == 0.0`（GLM 定价未知，cost 留空是**预期**，非 bug——Task 7 已如此设计）

## 2. 双引擎对照
- [ ] 同一 agent 同一输入，分别 `SHANNON_AI_PROVIDER=anthropic_api` 和 `openai_compatible` 各跑一次
- [ ] 两者都能正常结束、产出可比结果

## 3. Task 6 验证点（真实事件流）
- [ ] 观察 Runner 事件序列，确认 `tool_call_count` **不重复计数**（Task 6 用 `or` 合并 tool_called/tool_call_item，真实 SDK 若同时发两类 event 会 +2；若复现，按 item 身份去重）
- [ ] 确认空 turn（纯工具轮、无文本 delta）的 audit 行为可接受（`log_assistant_turn` 不上报空 turn，turn 号可能跳号）

## 4. Task 8 验证点（引擎核心）
- [ ] `ModelSettings(include_usage=True)` 下 GLM 流式**回传 usage**（tokens>0）；若 usage 为 0，排查该端点流式 usage 支持
- [ ] `instructions=None` 下 agent 行为正常（prompt 已含 system prompt，整段当 user input）
- [ ] `max_turns` 触顶时 `stop_reason == "max_turns"`（确认 `MaxTurnsExceeded` 真在 `stream_events()` 迭代时 raise，而非 `run_streamed` 调用时）
- [ ] `set_tracing_disabled(True)` 生效，无 trace 上传 401

## 5. 工具专项
- [ ] `web_search` DDG Lite 正则真实抓取有效（Task 4 已知正则脆，真实 HTML 结构变化会失配）
- [ ] `web_fetch` 真实 URL 去标签 + 截断正常
- [ ] `grep`（本机有 ripgrep 走 rg 分支）；可选 `monkeypatch shutil.which("rg")→None` 补测 python fallback（Task 3 遗留未覆盖）

## 6. 兼容性核实
- [ ] 确认 `open.bigmodel.cn/api/paas/v4` 是智谱当前 OpenAI 兼容端点（查智谱最新文档）
- [ ] 确认 `GLM-5.2[1m]` 在该端点支持 function calling（tool calling）

## 7. 回归
- [ ] `SHANNON_AI_PROVIDER=anthropic_api` 下原有渗透流水线不受影响（AnthropicProvider / message_dispatcher / GitNexus 均未改动）
- [ ] **已知隐患**：`tests/agents/` 目录名与 SDK 包名 `agents` 同名导致遮蔽，Task 1 用空 `packages/core/tests/__init__.py` 局部修复；若在 whitebox/blackbox 等其它 package 也加 `tests/agents/`，会复发（需根治：改 testpaths/importmode 或重命名测试子目录）

## 8. 已知限制（设计取舍，非 bug）
- OpenAI 引擎 `cost` 恒为 0.0（GLM 定价未知，不假估算）。
- `web_search` 用无 key 的 DuckDuckGo Lite，结果质量有限；如需更强搜索，后续接智谱 web_search tool 或其它源。
- OpenAI 引擎未实现 `Task`(subagent) / `TodoWrite` / `MultiEdit` / `NotebookEdit`（shannon 不依赖，已与用户确认不做）。
