# CLAUDE.md — shannon-py 项目指南

> 本文件记录本 repo 的**架构不变量与关键约定**，给所有在此工作的 agent / 贡献者。每次会话自动加载。改动前请先读对应小节。

---

## 1. 双轨消费模型（最重要架构不变量）★

shannon-py 的注入 / xss / ssrf 白盒检测是**双轨**，两条轨**各自独立、只在合并器（verdict OR）交汇**：

- **GitNexus 轨**：确定性层产物（`parameter_graph.json` / `SinkCallSite`）→ `vuln_chain_builders/*_builder.py` 提候选链 → `chain_verdict.py` 跑**轻量 LLM 判定**（`run_claude_prompt` 单次结构化输出，**非 agent**）→ `<vuln>_gitnexus_queue.json`。**这条轨的 LLM 分析 GitNexus 结果。**
- **LLM 轨**：`vuln-*.txt` agent，**纯 LLM 独立分析**——读 recon + 自己 grep + 自己追链，参照原始 TS 项目 `/root/shannon`（TS 无确定性层，100% 自给自足）。→ `<vuln>_exploitation_queue.json`（LLM 产物）。

**铁律（易踩，反复强调）：**
- **不要把确定性层产物（尤其 `static_dataflow_hints.md`）喂进 LLM 轨 prompt。** LLM 轨靠自身方法论 + 双轨 OR 由 GitNexus 轨独立补召回；把确定性结果喂 LLM 轨会让它依赖确定性层（而确定性层 / GitNexus 经常超时 / 不可用），破坏独立性。`prompts/shared/_static-dataflow-hints.txt` 的 `@include` 是历史遗留耦合，新工作不要再引入，且应逐步移除。
- **改 LLM 轨 prompt** 时，源只从 recon + grep 派生（TS 式），不引确定性 hints。
- **扩 sink 覆盖** 分两条路：LLM 轨改 prompt 清单（`vuln-*.txt`），GitNexus 轨改代码规则（`packages/core/src/shannon_core/code_index/sink_detector.py`）。
- **合并**：`run_merge_dual_track_queues`（`dual_track_merger.py`）做 verdict OR；`externally_exploitable` 是**可达性标签**（true=公网 / false=内部或跨服务），**不能被 verdict 覆写**（见 `dual_track_merger.py` 解耦约束）。

**auth / authz 特殊：** 它们不是 source→sink taint（属 missing-control），确定性 sink 规则不覆盖。但 authz 有自己的"GitNexus 风格"轨（`run_authz_gitnexus_judge`：IDOR 候选 + LLM 判定），auth 有 config 扫描器（`auth_config_scan.json`）兜底。所以"扫不出"时先分清是哪条轨、哪个 vuln 类。

详见 `docs/architecture.md`、`docs/superpowers/specs/2026-06-25-injection-recall-port-design.md`。

---

## 2. 双引擎约束（glm-openai 与 glm-anthropic）

- **两个引擎都要支持，且流程设计要一致（可互换）。** 不要"切到 glm-anthropic 了事"丢 openai 引擎，也不要让 openai 退化成单 agent 使两引擎行为分叉。
- **原始 TS** 单引擎跑 Claude Code CLI，子代理委派 tool 原生在；**CLI v2.1.x 该 tool 改名 `Task` → `Agent`**。vuln prompt 的 "delegate to Task Agent" 在 glm-anthropic 下映射到 `Agent` tool。
- **实测（2026-06-25，`scripts/validate_glm_task_probe.py`，2/2 可复现）：GLM 在 glm-anthropic 能正确驱动 `Agent` 子代理委派**（构造 description+subagent_type+prompt，子代理读码，产出正确判定）。**glm-anthropic 不瘫，与原始 TS 一致**；"LLM 轨跑不出结果" 仅限 glm-openai（`tools_openai/build_tools()` 历史上无 Task/Agent tool）。
- **解法 = approach ①：给 openai 引擎补 Task/Agent tool**（`packages/core/src/shannon_core/agents/tools_openai/task.py` + 接入 `build_tools()`），**prompt 不改**（两引擎共用 TS 原样 Task-delegation prompt）。实现见 `docs/superpowers/plans/2026-06-25-injection-recall-port.md` Task 5。
- 两个引擎都靠 **GLM 驱动 tool-use**；改 agent/tool 行为后，用 `scripts/validate_*_task_probe.py` 类探针在对应引擎实测。

---

## 3. 关键参考

- **设计 / 计划**：`docs/superpowers/specs/`、`docs/superpowers/plans/`（含 `2026-06-25-injection-recall-port-{design,plan}.md`）。
- **架构总览**：`docs/architecture.md`、`docs/whitebox-refactoring-assessment.md`、`docs/gap/`（gap 分析）。
- **测试陷阱**：全套 pytest 有预存挂起 / 失败（见各 package 的 test 说明）——只跑改动相关测试文件，勿广跑全套。
- **分支**：`feat/fork-py`（本地多项改动未 push；动代码前先看 `git log` 与 memory 了解在途工作）。
- **预存问题**：pre-recon 的 `run_code_index`(GitNexus) 对大仓易 >10min 超时（见相关 spec）；GitNexus 不可用会影响所有确定性轨。
