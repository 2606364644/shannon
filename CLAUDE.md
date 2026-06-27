# CLAUDE.md — shannon-py 项目指南

> 本文件记录本 repo 的**架构不变量与关键约定**，给所有在此工作的 agent / 贡献者。每次会话自动加载。改动前请先读对应小节。

---

## 1. 双轨概念（最重要架构不变量）★

shannon-py 的注入 / xss / ssrf 白盒检测是**双轨**，两条轨**各自独立、只在合并器（verdict OR）交汇**：

- **GitNexus 轨**：确定性层产物（`parameter_graph.json` / `SinkCallSite`）→ `vuln_chain_builders/*_builder.py` 提候选链 → `chain_verdict.py` 跑**轻量 LLM 判定**（`run_claude_prompt` 单次结构化输出，**非 agent**）→ `<vuln>_gitnexus_queue.json`。**这条轨的产物由 LLM 分析。**
- **LLM 轨**：`vuln-*.txt` agent，**纯 LLM 分析**——读 recon + 自己 grep + 自己追链，**保持与原始项目 `/root/shannon` 一致**（TS 无确定性层，100% 自给自足）。→ `<vuln>_exploitation_queue.json`（LLM 产物）。

**铁律（易踩，反复强调）：**
- **不要把确定性层产物喂进 LLM 轨 prompt。** LLM 轨靠自身方法论 + 双轨 OR 由 GitNexus 轨独立补召回；把确定性结果喂 LLM 轨会让它依赖确定性层（而确定性层 / GitNexus 经常超时 / 不可用），破坏独立性。**确定性→LLM 轨的 hints 桥梁（原 `static_dataflow_hints.md` 产物 + `prompts/shared/_static-dataflow-hints.txt` partial + `@include`）已彻底拆除——勿重建**（2026-06-26 injection-recall follow-up）。任何新 prompt 不得 `@include` 确定性产物；`packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py` 锁定此不变量。
- **改 LLM 轨 prompt** 时，源只从 recon + grep 派生（TS 式），不引确定性 hints。
- **扩 sink 覆盖** 分两条路：LLM 轨改 prompt 清单（`vuln-*.txt`），GitNexus 轨改代码规则（`packages/core/src/shannon_core/code_index/sink_detector.py`）。
- **合并**：`run_merge_dual_track_queues`（`dual_track_merger.py`）做 verdict OR；`externally_exploitable` 是**可达性标签**（true=公网 / false=内部或跨服务），**不能被 verdict 覆写**。

**双轨可配置（演进方向，2026-06-26 提出，设计中 ★）：**
- **战略**：把双轨从"固定并行"演进为**可配置开关**——**GitNexus 轨做可靠兜底，LLM 轨做可选的纯 LLM 增强（创意轨）**。**token 紧张时关闭 LLM 轨**（靠 GitNexus 轨兜底），**token 宽裕时打开 LLM 轨**（双轨 OR）。默认 LLM 轨**开**（env `SHANNON_LLM_TRACK_ENABLED`，默认 `"1"`）。
- **为支撑"GitNexus 轨独立兜底"**，GitNexus 轨从纯确定性演进为"**确定性兜底 + 可选 LLM 补召回**"：`sink_detector`（`packages/core/src/shannon_core/code_index/sink_detector.py`）规则未命中的可疑函数（半 sink 模式）用轻量 LLM 找 sink，产 `rule_id="llm-discovered"` 的软 `SinkCallSite`（与规则 sink 同流、可区分）+ `rule_gap_report.json` 反哺规则库优化。LLM 不可用（stub / 超时）时退回纯规则 + `is_entry_hint`（`deterministic-fallback` 立场 B 成果，作为"LLM 不可用档"，不浪费）。
- **不变的铁律**：LLM 轨仍纯 LLM 自给自足、不吃确定性产物（上条铁律不动）；本次演进只动 GitNexus 轨自己接 LLM + 加 LLM 轨开关，**不破坏双轨独立性**。
- **设计 spec**：`docs/superpowers/specs/2026-06-26-gitnexus-llm-sink-discovery-design.md`（撰写中，brainstorming 进行时）。

**auth / authz 特殊：** 它们不是 source→sink taint（属 missing-control），确定性 sink 规则不覆盖。但 authz 有自己的"GitNexus 风格"轨（`run_authz_gitnexus_judge`：IDOR 候选 + LLM 判定），auth 有 config 扫描器（`auth_config_scan.json`）兜底。所以"扫不出"时先分清是哪条轨、哪个 vuln 类。

详见 `docs/architecture.md`、`docs/superpowers/specs/2026-06-25-injection-recall-port-design.md`。

---

## 2. 双引擎（claude-agent-sdk / openai-agents）

项目拥有**双引擎**，经 `SHANNON_AI_PROVIDER` 切换：
- **claude-agent-sdk**（profile `glm-anthropic`）：底层 Claude Code CLI。
- **openai-agents**（profile `glm-openai`）：openai 兼容 Chat Completions。

**关键约定：**
- **两个引擎在代码流程上是一样的**——shannon-py 经统一抽象调用（`packages/core/src/shannon_core/agents/` 的 `BaseProvider` + `run_claude_prompt`），业务侧（whitebox / blackbox / core）不感知用哪个引擎。
- **差异只在核心智能体能力**：claude-agent-sdk（CLI）原生全套工具，含**子代理委派**（CLI v2.1.x 该 tool 名 `Agent`，原 `Task`）；openai-agents 经 `tools_openai/build_tools()` 暴露工具集（含 `task` 子代理委派，Task 5 已对齐 CLI）。
- **核心差异根因：CLI 运行时 vs 纯框架（易踩，别反复绕）**——claude-agent-sdk 底层是 **Claude Code CLI 子进程**，CLI **自带全套内置工具**（`Task`/`Agent` 子代理委派 + Read/Bash/Grep/Edit…），故 claude 轨像原始 TS（`shannon/apps/worker/src/ai/claude-executor.ts`：`permissionMode: bypassPermissions`、无 `allowedTools`、`maxTurns: 10_000`）**白嫖 CLI 内置、零工具代码**（`_build_options` 只配 permission/env，不碰工具）；openai-agents 是**纯框架**（`Agent`/`Runner`/`handoffs`/`as_tool`/`function_tool` 编排原语），**不附通用内置工具**（少数 hosted tools 要 OpenAI 后端，第三方 Chat Completions 端点用不了），故 openai 轨**必须自维护** `tools_openai/{bash,fs,web,task}.py`。**后果**：原始 shannon 的 subagent 分发 = CLI 内置、**零代码**（prompts 里 "delegate to Task Agent" 直接驱动 CLI 内置 Task 工具）；claude 轨 100% 对齐；**openai 轨注定要自造委派工具**（手写 `task` 或 SDK 原生 `as_tool` 都行，**都逃不掉维护子 agent 定义**），对齐是"功能性的"（同一份 prompt 能跑）+ 带自维护成本——SDK 哲学差异，**不可消除，别当退化去"修"**。`as_tool` 与手写 `task` 功能等价、无致命缺陷（as_tool 透传父业务 context / 自带 approval·tracing 隐式行为面 / input 默认单参 `{input}`）；选手写 `task` 为可控性更窄 + 已验证 PASS（`validate_openai_task_probe.py`），非能力差异。
- **引擎的智能体能力是 agent 方维护的，当前项目只是使用**——`packages/core/src/shannon_core/agents/` 是项目的 agent 集成层（对接两套 SDK、暴露统一工具/能力），上游 SDK（claude-agent-sdk / openai-agents）提供底层能力；业务侧只 `run_claude_prompt(...)`，不直接碰 SDK。
- **能力对齐已落实**（Task 5，`feat/fork-py`）：openai 引擎经 `tools_openai/task.py` 的 `task` function_tool + provider 注入 `_make_subagent_runner`，对齐 CLI 的 `Agent` 子代理委派——两引擎跑同一份 vuln prompt（**prompt 不改**），双引擎流程对齐。
- **两个引擎都要支持、流程一致（可互换）**——不要"切到 glm-anthropic 了事"丢 openai 引擎，也不要让 openai 退化成单 agent 使两引擎行为分叉。
- **实测**：GLM 在 claude-agent-sdk 能正确驱动 `Agent` 子代理委派（`scripts/validate_glm_task_probe.py`，2/2 可复现）；glm-anthropic 不瘫、与原始 TS 一致。openai 引擎已补 `task` 工具（Task 5），**glm-openai 侧 `scripts/validate_openai_task_probe.py` 真机已验证 PASS 且可复现**（GLM 正确发起 `task` 子代理委派，子代理读码追链，产出 SQLi 判定；2026-06-27 首跑 68.7s、2026-06-28 复跑 57.8s，均 3 turns/success=True/不违规直接读）。改 agent/tool 行为后，用 `scripts/validate_*_task_probe.py` 类探针在对应引擎实测。

---

## 3. 关键参考

- **设计 / 计划**：`docs/superpowers/specs/`、`docs/superpowers/plans/`（含 `2026-06-25-injection-recall-port-{design,plan}.md`）。
- **架构总览**：`docs/architecture.md`、`docs/whitebox-refactoring-assessment.md`、`docs/gap/`（gap 分析）。
- **测试陷阱**：全套 pytest 有预存挂起 / 失败（见各 package 的 test 说明）——只跑改动相关测试文件，勿广跑全套。
- **分支**：`feat/fork-py`（本地多项改动未 push；动代码前先看 `git log` 与 memory 了解在途工作）。
- **预存问题**：pre-recon 的 `run_code_index`(GitNexus) 对大仓易 >10min 超时（见相关 spec）；GitNexus 不可用会影响所有确定性轨。
