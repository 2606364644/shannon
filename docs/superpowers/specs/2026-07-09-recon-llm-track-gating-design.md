# 侦察层 LLM 纳入 SHANNON_LLM_TRACK_ENABLED 开关设计

> ⚠️ **本 spec 已部分回退（2026-07-14，plan smooth-wandering-dolphin）。** 把 pre-recon / recon 纳入关 LLM 轨的前提「GitNexus 兜底 recon」对 authz 证伪 —— authz Vertical/Context 依赖 recon 的角色模型（§7）/ 多步工作流（§8.3），GitNexus 完全不产这些语义。故 pre-recon / recon / merge_sink_reports 重新移出 `SHANNON_LLM_TRACK_ENABLED` 门控（始终跑），开关收窄为「只关 inj/xss/ssrf vuln agent」（`DEGRADABLE_VULN_CLASSES`）；authz/auth vuln agent 也保留（GitNexus 只做 IDOR 不覆盖 Vertical/Context + auth 无轨）。下文 §4.1 矩阵 / §5 语义扩展句 / §4.6 skip message 均已过时，保留作历史记录。

## 0. 一句话结论

`SHANNON_LLM_TRACK_ENABLED=0` 当前只 gate 了 vuln agent,侦察层的 pre-recon / recon 两个纯 LLM agent 不受控、始终烧 token。本设计把这两个 agent 纳入同一开关,让「关 LLM 轨靠 GitNexus 确定性轨兜底」名副其实;改动集中在 `workflows.py` 内联 gate(复用 `is_llm_track_enabled()`),零新开关、零新抽象。

## 1. 背景

### 1.1 现状:开关名不副实

CLAUDE.md §1 双轨战略:「token 紧张时关闭 LLM 轨(靠 GitNexus 轨兜底),默认 LLM 轨开(env `SHANNON_LLM_TRACK_ENABLED`,默认 `"1"`)」。`PipelineInput.enable_llm_track`(`pipeline/shared.py:20`)注释亦写「False=只跑 GitNexus 轨」。

但代码里 `if input.enable_llm_track:` 只出现在两处(`packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`):

- `:173` —— 仅包裹 `run_merge_sink_reports`
- `:345` —— vuln agent(`run_vuln_agent`)

而侦察层的两个纯 LLM agent **完全在 gate 之外**:

- `:161` `run_agent PRE_RECON`——与 `run_code_index` 同处一个 `asyncio.gather`(`:155`)
- `:238` `run_agent RECON`——独立、无条件

结果:`SHANNON_LLM_TRACK_ENABLED=0` 只关了 vuln 判定,关不掉更上游、更烧 token 的侦察层。实测一次扫描 pre-recon agent 跑 17 分钟未完仍在调 LLM(主 agent 内部还 spawn 6 个 Task 子 agent)。

### 1.2 为什么是「局部 gap」而非「双轨解耦崩坏」

判定层的双轨解耦是成立的:`chain_verdict`/builder 只吃 `parameter_graph.json` + `code_index.json`(`chain_verdict.py:8,21`、`injection_builder.py:45`),纯确定性;`entry_point_fusion` 已做双源解耦(G6,`code_index/__init__.py:370,403`「deliverable 不存在则 skip LLM 源」);`externally_exploitable` 由 GitNexus 轨自产(`injection_builder.py:63`)。

gap 的本质:解耦边界划在 vuln 判定层,但「关 LLM 轨省 token」的开关承诺要求边界**上移到侦察层**——而侦察层的 LLM 部分没被开关化。前置条件(GitNexus 轨能否独立兜底)已满足,故可修。

### 1.3 关掉侦察层 LLM 的下游影响(explore 已验证:零硬依赖崩)

recon/pre-recon LLM agent 的所有下游消费者都带 `exists()` 守卫或 try/except,全是软依赖:

- vuln agent `RECON_CONTEXT`(`activities.py:314-315`)读 `recon_deliverable.md`,缺失注空串占位 → 但 vuln 也受 gate,**关轨时不触发**
- `attack_chain_llm_agent`(`attack-chain.txt:26,88`)读 recon md,缺失靠 grep 兜底,non-fatal(`workflows.py:465-480` try/except)
- PoC 生成(`poc_generator.py:563-564`)`endpoints={}` 降级
- `entry_point_fusion` skip LLM 源,只用确定性 schema 源(隐藏入口丢失,精度降级)
- `run_save_adjudication` / `run_route_chain_building` / `run_merge_dual_track_queues` / `run_risk_scoring` —— 完全不读 recon md,**无影响**

pre_recon_deliverable.md 仅两处消费:`entry_point_fusion`(条件)与 `run_merge_sink_reports`(`activities.py:947-949`,且该 activity 已在 `:173` gate 内,关轨时本就不跑)。

## 2. 方案选择

- **选定:内联 gate** —— 在 `workflows.py` 用 `input.enable_llm_track` gate `PRE_RECON`/`RECON`,复用 `is_llm_track_enabled()`,写法对齐现有 vuln gate(`:345`)。
- 否决:抽「侦察层」为可选子流程 —— 过度工程(YAGNI),内联已足够清晰且与现有模式一致。
- 否决:新增独立开关 `SHANNON_RECON_LLM_ENABLED` 分层控制 —— 粒度细但开关变多、用户认知负担,且不解决现有开关「名不副实」的核心问题(brainstorming 已排除)。

## 3. 范围

- **仅 whitebox**。blackbox 无确定性轨兜底概念(无确定性层),关 LLM = 没法跑,不在范畴。
- 覆盖:`PRE_RECON` agent、`RECON` agent。
- 不覆盖(明确):`run_code_index`、`entry_point_fusion`、3 个 GitNexus judge(`run_auth_gitnexus_judge` / `run_authz_gitnexus_judge` / `run_gitnexus_chain_verdict`)、`merge`、`risk_scoring`、`attack_chain_llm_agent`、`report`。

## 4. 设计

### 4.1 开关覆盖矩阵(`SHANNON_LLM_TRACK_ENABLED=0` 时)

| activity | workflows.py | 关? | 理由 |
|---|---|---|---|
| `run_agent PRE_RECON` | :161 | ✅ **关(新增)** | 纯 LLM,最大 token 单点(主 + 6 Task 子 agent) |
| `run_agent RECON` | :238 | ✅ **关(新增)** | 纯 LLM |
| `run_vuln_agent` 5 类 | :352 | ✅ 关(现状) | 纯 LLM 判定 |
| `run_code_index` | :157 | ❌ 保 | GitNexus 确定性兜底根基 |
| `entry_point_fusion` | :185 | ❌ 保 | G6 已解耦,确定性 schema 源兜底 |
| 3 个 GitNexus judge | :331 / :394 / :414 | ❌ 保 | 确定性轨,误关塌双轨 |
| `attack_chain_llm_agent` | :466 | ❌ 不动 | 下游消费,关 vuln 后靠 GitNexus queue 降级 |
| `report` | :532 | ❌ 不动 | 最终报告,仍基于 GitNexus queue 产出 |

**原则:开关只关「纯 LLM 分析 agent」,所有确定性轨 + 下游报告产出保持运行。**

### 4.2 pre-recon 阶段重构(`workflows.py:155-171`)

把 `asyncio.gather(run_code_index, run_agent PRE_RECON)` 拆成条件分支:

- **关轨**:`code_index_result = await run_code_index(...)`(独立 await,不再 gather);PRE_RECON agent skip;保留 `:169` `code_index_stats` 赋值(code_index 实跑了),**跳过** `:170`(append `PRE_RECON`)与 `:171`(`agent_metrics[PRE_RECON]`)——故无需 `pre_recon_metrics` 默认值。
- **开轨**:维持现有 `asyncio.gather(code_index, PRE_RECON)` 双支 + fail-fast。

fail-fast 语义:关轨时无 gather,`code_index` 独立跑,无副作用(原「取消另一支」的对象本就不存在)。因 skip 分支整段跳过 `:170-171`,不存在双赋值解构 NameError。

### 4.3 recon 阶段重构(`workflows.py:225-252`)

- **关轨**:不调度 `run_agent RECON`,不 append `RECON` 到 `completed_agents`;phase 边界日志(`log_phase_start/complete_activity`)由实现保证后端 phase 状态机一致(建议打 `skipped` 标记,避免前端出现空 phase 卡死)。
- **开轨**:维持现状。

### 4.4 resume 语义(关键不变量)

**关轨 skip 时不把 `PRE_RECON`/`RECON` 标进 `completed_agents`**。

- 语义:关轨 = 当次跳过,不污染持久 workflow 状态。
- 若下次开轨重跑/resume 同一 workflow,守卫 `:140` `if PRE_RECON.value not in completed_agents` / `:225` 同理放行 → **会补跑**侦察 LLM。
- 状态诚实:`completed_agents` 只记真跑完的 agent,与对应 deliverable 是否存在保持一致(避免「标记完成但 deliverable 缺失」的静默降级)。

### 4.5 兜底一致性

见 §1.3。关轨后 GitNexus 轨完整自给链:`run_code_index` → `entry_point_fusion`(确定性源) → builder/`chain_verdict`(吃 parameter_graph) → `<vuln>_gitnexus_queue.json` → `merge` → 报告。侦察 md 缺失全部走软依赖降级,零崩溃。

### 4.6 可观测性(UX)

两处 skip 各打一条 `info_message`(经 `activities.log_info_activity`),对齐现有 `:363` `"llm_track=disabled (SHANNON_LLM_TRACK_ENABLED=0); running GitNexus track only"` 模式,并点明精度损失:

- pre-recon skip:`"llm_track=disabled: pre-recon LLM agent skipped; code_index (GitNexus) still runs; entry points degrade to deterministic schema source only"`
- recon skip:`"llm_track=disabled: recon LLM agent skipped; GitNexus track continues independently"`

## 5. 配置 / env 清单

- **无新 env**。复用 `SHANNON_LLM_TRACK_ENABLED`(`concurrency.py:39` `is_llm_track_enabled()`,默认 `True`,`"0"/"false"/"no"/"off"` → `False`)。
- 语义扩展(向后兼容):`=0` 从「只关 vuln」扩展为「关 pre-recon + recon + vuln」。默认仍为开(`"1"`),关轨是 opt-in。
- follow-up:用户本地 `.env` 第 27 行注释「开启LLM轨」与值 `=0` 矛盾,建议同步修正注释为「LLM 轨开关(侦察+vuln);0=关靠 GitNexus 兜底」。

## 6. 测试

- **workflow gate 测试**(新增):`enable_llm_track=False` 时断言
  - **不**调度 `PRE_RECON` / `RECON` / `run_vuln_agent`
  - **仍**调度 `run_code_index` / 3 个 GitNexus judge / `run_gitnexus_chain_verdict` / `run_merge_dual_track_queues`
- **不变量防回退测试**:锁定「3 个 GitNexus judge 不受 `enable_llm_track` 影响」(最易踩坑,误关塌双轨)。
- **resume 语义测试**:`enable_llm_track=False` 跑完后,`completed_agents` 不含 `PRE_RECON`/`RECON`。
- 复用:`test_concurrency_config.py` 已锁定 env 解析(`"0"→False`),无需改。

## 7. 不做(YAGNI / follow-up)

- 不新增分层开关、不抽侦察子流程。
- 不给 `attack_chain`/`report` 加 gate(用户已确认保持运行;若未来要更激进省 token,另起 spec)。
- 不为侦察 md 缺失产 sentinel 文件(现有 `exists()` 守卫已足够,sentinel 是 over-engineering)。
- 不动 blackbox。
- follow-up:本设计是 CLAUDE.md §1「双轨可配置」演进(spec 2026-06-06-gitnexus-llm-sink-discovery-design)的一部分,把「关 LLM 轨」从「半关」推进到「关纯 LLM 分析 agent」,GitNexus 轨(含其 LLM 补召回,另由 `SHANNON_GITNEXUS_LLM_ENABLED` 独立控制)兜底。

## 8. 风险 / 开放问题

- **精度损失**:关侦察 LLM 后 entry points 仅剩确定性 schema 源(OpenAPI/约定),丢失 LLM 发现的隐藏入口/路由 → GitNexus 轨 chain_verdict 的候选链召回面变窄。这是「可靠兜底 vs 精度」的权衡,符合 §1 战略方向,用 info_message 告知用户。
- **`completed_agents` 语义**:skip 不标记后,需确认没有其他下游逻辑依赖「PRE_RECON/RECON 必在 completed_agents」(explore 未发现,实现时 grep 复核)。
- **真机冒烟**:实现后需在 glm-anthropic 引擎实跑一次 `SHANNON_LLM_TRACK_ENABLED=0`,核实 pre-recon/recon 确实跳过、GitNexus queue 仍产出、报告仍生成。

## 9. 决策记录

- **2026-07-09**:覆盖范围定为「补关侦察层」(pre-recon + recon),`attack_chain`/`report` 保持运行;resume 语义定为「skip 不标 completed,开轨重跑补跑」。方案选内联 gate(对齐 vuln gate 模式),否决独立分层开关与抽子流程。scope 限 whitebox。
