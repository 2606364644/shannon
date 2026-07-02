# GitNexus 轨深度 agent 化（authz + auth）总览 设计

> 日期：2026-07-02　分支：`feat/fork-py`
>
> **背景**：token 紧张时关 LLM 轨（`SHANNON_LLM_TRACK_ENABLED=0`）能省 inj/xss/ssrf 的重型 vuln agent，但 auth/authz 是 **missing-control 类**（CLAUDE.md §1：非 source→sink taint），深度判定必须花 LLM token——GitNexus 轨当前的**轻量单次** `run_claude_prompt` 判定（`chain_verdict.py` / `authz_gitnexus_judge`）兜不住其深度，关 LLM 轨会丢 auth/authz 覆盖（authz 实测三目标 `entry_points=0` → queue 全空，详见 memory `authz-gitnexus-endpoint-param-coverage-gap`）。
>
> 两条治法：(A) per-class 豁免——关 LLM 轨时仍为 auth/authz 跑 vuln agent（治标，承认 auth/authz 永远依赖 LLM 轨）；(B) **本 epic**——把 GitNexus 轨对 auth/authz 的判定从"轻量单次"升级为"**吃确定性候选的深度 agent**"，让 GitNexus 轨独立兜 auth/authz 深度，关 LLM 轨不丢（治本）。用户知情后选 B，并坚持 auth 也搬（不止 authz）。
>
> **本文件是 epic 总览**：锁定愿景、子项目分解、依赖顺序、风险登记；各子项目独立 spec → plan → 实现（brainstorming 规范：大项目分解，各自周期）。

---

## 1. 目标 / 非目标

### 目标

- **G1（authz 深度）**：GitNexus 轨 authz 判定从轻量单次 `run_claude_prompt` 升级为**多轮深度 agent**（带 grep/read 工具），**仍吃确定性 IDOR 候选**（`authz_gitnexus_judge` 现产），对每个候选深判 owner 检查 / 授权逻辑。产出仍写 `authz_gitnexus_queue.json`，`dual_track_merger` 无感。
- **G2（auth 搬迁，条件性）**：为 auth 设计**确定性候选模型**（missing-control 无现成概念）+ auth 深度 agent，使 GitNexus 轨独立兜 auth 深度（不止 config 缺陷）。**条件**：依赖子项目 2a 候选模型 spike 通过；证伪则 epic 降级（§4 退路），auth 仍靠 LLM 轨。
- **G3（B 配套）**：补 authz 候选质量——`entry_points` 框架覆盖扩展、接 OpenAPI（`entry_point_fusion`）、参数发现，让深度 agent 有候选可吃（当前空）。
- **G4（定位同步）**：更新 CLAUDE.md §1——GitNexus 轨 auth/authz 判定从"轻量 LLM（非 agent）"改为"确定性候选 + 深度 agent"。

### 非目标（明确排除）

- **不改 LLM 轨 `vuln-authz.txt` / `vuln-auth.txt` prompt**：LLM 轨 vuln agent 保留为**可选增强**（双轨全开时 OR 互补：GitNexus 深度 agent 吃候选 vs LLM 轨 vuln agent 从零自主，两路召回）；关 LLM 轨时 GitNexus 深度 agent 兜（G3 完成后）。
- **不改 `chain_verdict`（inj/xss/ssrf）**：这三类是 source→sink taint，有确定性链兜底，轻量判定够用，深度收益低于 auth/authz。YAGNI。
- **不破坏双轨铁律**：LLM 轨仍纯 LLM 自给自足、不吃确定性产物（CLAUDE.md §1 铁律不动）；GitNexus 轨吃确定性候选是其本职（`chain_verdict`/`authz_judge` 现在就读候选判定），本次只把"判定"从单次加深成多轮 agent，**不改"确定性→LLM"的数据流方向**。
- **不做 per-class 豁免 A**：A 被本 epic 取代。过渡期（重构 + B 未完成前）LLM 轨常开，不具备"关 LLM 轨保 auth/authz"能力——接受此时序，奔战略目标。

---

## 2. 背景与约束

### 2.1 关 LLM 轨省 token 的本质

关 LLM 轨（`workflows.py:322-345` 的 `enable_llm_track=False` 分支）省的是：**inj/xss/ssrf/auth/authz 的重型 vuln agent**（`*-vuln`，max_turns ~500）+ sink/entry fusion。但：

- inj/xss/ssrf 有 GitNexus `chain_verdict`（轻量）兜底 → 省了不亏。
- auth/authz 是 missing-control，深度判定**必须花深度 LLM token**——不管放 LLM 轨还是 GitNexus 轨，这笔 token 省不掉。

**所以"把 auth/authz 深度从 LLM 轨挪到 GitNexus 轨"不会多省 token**（auth/authz 该花还花）。挪的真正动机是**架构愿景**：让 GitNexus 轨成为独立主模式，LLM 轨整个可选（关了也不影响 auth/authz）。

### 2.2 GitNexus 轨深度 agent 省 token 的唯一杠杆：吃确定性候选

LLM 轨 `vuln-authz` agent 是**从零自主 grep**（TS 式自给自足，不吃确定性产物——铁律要求），花 token 探索。GitNexus 轨深度 agent 若吃**确定性候选**（IDOR 点 / 参数 / 链），有方向指路，**可能**用更少 token 达到类似深度——这是唯一能比 LLM 轨省的来源。且不违反铁律（铁律只保护 LLM 轨；GitNexus 轨吃候选是本职）。

**但前提是候选非空**——当前 authz `entry_points=0`（三目标实测），深度 agent 没候选可判。这正是 G3（B 配套）要修的。

### 2.3 为什么选 B（治本）而非 A（治标）

| | A：per-class 豁免 | B：本 epic（GitNexus 深度 agent） |
|---|---|---|
| 改动 | workflows.py 一处分支 + 一个 env | 多子项目重构（见 §3） |
| auth/authz 深度谁做 | LLM 轨 vuln agent（豁免跑） | GitNexus 轨深度 agent |
| 关 LLM 轨后 auth/authz | 保深度（vuln agent 仍跑） | 保深度（GitNexus 兜，G3 完成后） |
| LLM 轨对 auth/authz | 必需（永远豁免） | 可选（独立后可关） |
| 时序 | 立即生效 | 重构+B完成后生效，有空窗 |
| 性质 | 治标（承认长期依赖 LLM 轨） | 治本（GitNexus 独立主模式） |

用户选 B：接受大 scope + 时序空窗，换 GitNexus 轨独立。

---

## 3. 子项目分解（epic）

| 子项目 | 内容 | 依赖 | 风险 | 产出 |
|---|---|---|---|---|
| **0 基础设施** | GitNexus 轨 LLM 调用层从单次 `run_claude_prompt` 改造为支持**多轮 agent**（带 grep/read 工具，复用 `packages/core/src/shannon_core/agents/` 现有 agent 基础设施）。`_make_verdict_llm_client`（`activities.py:854-865`）等入口改造。 | 无 | 低（最确定，纯基础设施） | spec-0 |
| **1 authz 深度 + B** | (a) 加深 `authz_gitnexus_judge` 判定为多轮 agent（吃 IDOR 候选）；(b) B 补候选：扩 `entry_points` 框架识别、接 `entry_point_fusion`（OpenAPI）、补参数发现。 | 0 | 中（authz 有现成候选框架 `authz_gitnexus_track.py`，链路现成） | spec-1 |
| **2a auth 候选模型 spike** | **可行性研究**：探索 auth（missing-control）的确定性候选模型——认证相关 sink？session/token 处理函数？config 缺陷已由 `auth_config_scan` 覆盖，逻辑缺陷的"候选"是什么？产出"可行/证伪"结论 + 若可行的候选 schema 草案。 | 无（研究，不阻塞 0/1） | **高（开放问题，可能 dead-end）** | spike 报告 |
| **2b auth 深度 agent** | （条件：2a 通过）按 2a 候选模型建 auth 深度 agent。 | 0 + 2a 通过 | 中（依赖 2a） | spec-2 |
| **贯穿** | CLAUDE.md §1 定位更新（见 §6）+ 各子项目测试锚点 + AST/回归防回退锁。 | 各阶段 | — | 随各 spec |

---

## 4. 依赖顺序与并行

```
子项目0（基础设施）────────────┐
                              ├─→ 子项目1（authz 深度 + B）
子项目2a（auth 候选 spike）────┘    ‖（并行）
                                   ↓
                              子项目2b（auth 深度 agent，2a 通过后）
```

- **0 与 2a 并行**：0 是实现（基础设施），2a 是研究（spike），互不阻塞。
- **1 依赖 0**：深度 agent 要跑在多轮 agent 调用层上。
- **2b 依赖 0 + 2a**：候选模型设计通过才有意义实现。
- **2a 先行证伪**：若 auth 候选模型证伪，2b 取消，epic 降级为"只 authz"（auth 仍靠 LLM 轨，回退到部分 A 式依赖）——**这是 epic 的退路**。

---

## 5. 风险登记

- **R1（最高，dead-end）**：auth 确定性候选模型是开放问题。missing-control 的"候选点"很难定义（broken auth / session 管理 / 密码重置逻辑没有 source/sink/IDOR 那样的明确锚点）。**对策**：子项目 2a 先 spike 证伪，通过才做 2b；证伪则 epic 降级（§4 退路）。
- **R2（空转）**：深度 agent 依赖候选质量。G3（B）未完成时，`entry_points=0` → 深度 agent 无候选可判 → 关 LLM 轨仍丢 authz。**对策**：1 的 (a) 加深判定 与 (b) B 补候选 同步推进；空窗期 LLM 轨常开。
- **R3（token 反噬）**：多轮 agent 比轻量单次贵。深度 agent 若吃不到好候选（R2），退化为从零探索，token 不比 LLM 轨省，等于白搬。**对策**：子项目 1 验收时实测"GitNexus 深度 agent（吃候选）vs LLM 轨 vuln agent（从零）"的 token/召回对比，确认杠杆成立才合并。
- **R4（文档漂移）**：GitNexus 轨 auth/authz 定位变化若不同步 CLAUDE.md §1，后续 agent 会被旧定位误导（"轻量非 agent"）。**对策**：§6 列出具体改动行，随子项目 1 合并时同步。
- **R5（双引擎）**：多轮 agent 改造要走 `run_claude_prompt` 统一抽象（CLAUDE.md §2），双引擎（claude-agent-sdk / openai-agents）都要支持。**对策**：子项目 0 验收用 `scripts/validate_*_task_probe.py` 类探针双引擎实测。

---

## 6. CLAUDE.md §1 影响（定位更新）

当前 §1 定位 GitNexus 轨判定为"**轻量 LLM 判定**（`run_claude_prompt` 单次结构化输出，**非 agent**）"，且 §1「auth/authz 特殊」段写 authz = "IDOR 候选 + LLM 判定"。本 epic 把 authz/auth 判定升级为深度 agent，需同步：

- §1 GitNexus 轨定义（第 11 行附近）：auth/authz 判定从"轻量单次"改为"确定性候选 + 深度 agent（多轮）"；inj/xss/ssrf 仍轻量。
- §1「双轨可配置」段（第 20-24 行）：战略愿景"GitNexus 轨做可靠兜底"补一句——auth/authz 兜底经深度 agent（吃候选）实现，非轻量判定。
- §1「auth/authz 特殊」段（第 26 行）：authz "IDOR 候选 + LLM 判定" → "IDOR 候选 + 深度 agent 判定"；auth "config 扫描器兜底" → "config 扫描 + 深度 agent（2b 完成后）"。

**铁律不动**：§1 铁律"不喂确定性产物给 LLM 轨"不变；GitNexus 轨吃候选是其本职，不冲突。

---

## 7. 验收（epic 级）

- **V1**：`SHANNON_LLM_TRACK_ENABLED=0` + 候选非空（G3 完成）时，GitNexus 轨对 authz 产出深度判定 `authz_gitnexus_queue.json`（非空、含 owner 检查/授权逻辑证据），merger 正常 OR。
- **V2**：双轨全开时，authz 双路（GitNexus 深度 agent + LLM 轨 `vuln-authz`）OR 互补，召回 ≥ 单轨。
- **V3**（2b 通过后）：auth 同 V1/V2。
- **V4**：CLAUDE.md §1 同步更新，无文档/代码漂移。
- **V5**：R3 实测——GitNexus 深度 agent（吃候选）token/召回 ≥ LLM 轨 vuln agent（从零）的性价比。
- **V6**：双引擎（glm-anthropic / glm-openai）多轮 agent 探针实测 PASS。

---

## 8. 后续（各子项目独立 spec → plan）

本 epic 批准后，按 §4 顺序逐子项目 brainstorm → spec → plan → 实现：

- `2026-07-02-gitnexus-deep-agent-infra-design.md`（子项目 0，基础设施）
- `2026-07-02-gitnexus-authz-deep-agent-design.md`（子项目 1，authz 深度 + B）
- `2026-07-02-auth-deterministic-candidate-model-spike.md`（子项目 2a，spike 报告）
- `2026-07-02-gitnexus-auth-deep-agent-design.md`（子项目 2b，2a 通过后）

**建议起点**：子项目 0（基础设施）与 2a（auth spike）并行启动——0 最确定无风险、是所有深度 agent 的基础；2a 先除最大风险（dead-end），决定 epic 是否降级。
