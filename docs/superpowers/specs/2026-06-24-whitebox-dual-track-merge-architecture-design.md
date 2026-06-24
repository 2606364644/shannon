# Design: 白盒双轨合并架构（GitNexus 确定性链 + LLM）

> **Date:** 2026-06-24（v2）
> **Status:** Draft（待用户 review）
> **适用范围:** Shannon-py 白盒**分析层**（pre-recon / recon / 5 个 vuln agent：injection / xss / ssrf / authz / auth）。**不含 misconfig**（重构范围外）。exploit / report 作为下游消费方提及，不深入改造。
> **性质:** 架构设计 spec，定义双轨产出与合并机制。文件级实现步骤由后续 writing-plans 给出。
>
> **v2 变更**：明确 GitNexus 轨 verdict 来源（GitNexus 产链 + LLM 判该链）；补 findings 数据结构、LLM 不被锚定机制、taint 轨道归属、recon/vuln 两阶段合并区分、验收标准；纳入 GitNexus 索引超时风险。

---

## 1. 背景与动机

Shannon-py 重构引入了 GitNexus（调用图）+ AST（`sink_detector`）+ LLM taint 作为确定性预分析层，相对原始 Shannon（TS）"纯 LLM 流水线"是结构性进步。但：

1. **重构处于初期，确定性层效果尚未验证**——sink_detector 规则覆盖、调用图精度、taint 传播准确性都未经大规模实证。
2. **直接用确定性层替代 LLM 有风险**：若确定性层漏某类 sink/路径，而 LLM 被弱化成"只补充确定性没覆盖的"，LLM 会被确定性清单**锚定**，确定性漏什么它也跟着漏，双轨互补失效。
3. **原始项目自身是"伪结构化"**：SharedKnowledge 通道是死代码（`_shared-knowledge.txt` 零 @include），agent 间实际靠"LLM 产 markdown → 下个 LLM 读取"的文本传递（见 §7）。

**决策：采用"双轨并行 + 合并"策略，初期不替代 LLM。** 与评估文档 v2 早已确立的"加法层（LLM 仍跑）"原则一致。

---

## 2. 核心原则（6 条）

1. **双轨并行，各产 verdict**：
   - **LLM 轨**：LLM 自主探索代码，产"自主链 + verdict"。
   - **GitNexus 轨**：GitNexus 确定性产"链"（source→sink 候选 / 调用图路径 / sink+sanitizer 标注）→ **LLM 分析该链**产 verdict（"链判定 pass"）。
   - **两轨独立**，因为链来源不同（LLM 自主探索 vs GitNexus 确定性穷举）。两轨都有 verdict → 合并器可"取 vulnerable（OR）"。
2. **LLM 轨不被锚定**（关键前提）：
   - LLM 轨 **不注入** GitNexus 的链/候选（taint、source→sink 候选链）。
   - LLM 轨**可看**静态 sink 清单（`_static-dataflow-hints`）作为"**至少要覆盖的下限**"，但 prompt 明确"**必须独立探索清单外的 sink/路径**"——这是下限非上限。
   - GitNexus 轨的链判定 pass **只看** GitNexus 给的链，不看 LLM 轨结果（反向也独立）。
3. **合并器**：双轨 findings 去重合并，标来源（`gitnexus-only` / `llm-only` / `both`）与置信度。
4. **冲突取 vulnerable（OR）**：任一轨报 vulnerable 即最终 vulnerable（保守取并集，宁过报不漏报）。`both`=高置信直接信；`single`=低置信标 NEEDS_REVIEW；两轨都 safe 才 safe。
5. **结构化字段冲突取"危险侧"**：auth middleware 任一无→无；framework origin 任一 auto-generated→auto-generated；ownership 任一 none→none。
6. **初期不替代**：LLM 轨照常全跑（不省 token）；GitNexus 轨的链判定 pass 是新增 LLM 调用，但比自主探索省（链已给定）。未来验证成熟后才考虑弱化 LLM 轨（远期目标，本期不做）。

---

## 3. 架构总览

```
                         ┌─ LLM 轨 ──────────────────────────────────┐
                         │   代码 ──LLM 自主探索──→ 自主链 + verdict   │
vuln 类 (或 recon 环节) ──┤                                            ├─→ 合并器 ─→ 最终 findings
                         └─ GitNexus 轨 ──────────────────────────────┘
                             代码 ──GitNexus 确定性──→ 确定性链
                                     └── LLM 链判定 pass ──→ verdict ─┘
```

**两轨 LLM 角色**：LLM 轨 = 探索者（找链 + 判）；GitNexus 轨 = 判定者（GitNexus 给链，只判）。同一 vuln 类两次 LLM 调用，输入不同。

**重构已具备的雏形**（理想态是升级雏形 + 接通死通道）：

| 环节 | 已有雏形 |
|---|---|
| pre-recon | `Phase 0 Code Index Review`（code_index.json 给 LLM 概览） |
| recon | 保留 §4.1/§4.2 + `{{TAINT_FLOW_SUMMARY}}` 注入 |
| vuln | `@include(_static-dataflow-hints.txt)`（确定性 sink 清单）+ `run_merge_sink_reports`（sink 合并雏形） |
| authz | `<framework_endpoint_guidance>`（finale-rest/epilogue IDOR 方法论） |

**当前雏形的局限**（理想态要解决的）：
- 确定性层多为"**给 LLM 看的参考**"，而非"**独立一轨产 verdict**"→ 合并不彻底。
- 多个确定性产物走**死通道**：`framework-analyzer`、`parameter_graph` 构建了不落盘/不读。
- LLM 有**锚定风险**：taint 经 `{{TAINT_FLOW_SUMMARY}}` 注入 LLM 轨，会锚定。

---

## 4. 双轨产物与合并器

### 4.1 findings 数据结构

每轨产 findings，合并后统一格式。复用现有 `*_exploitation_queue.json` 的 zod schema（每 vuln 类已有），**每条 finding 增加两个字段**：

- `source_track`: `"llm"` | `"gitnexus"`（产出轨道；合并后改为来源标记）
- `evidence_chain`（GitNexus 轨必填）：该 verdict 所基于的链（source→sink 路径 + sanitizer 标注），便于下游复核

**合并后 finding** 增加：
- `merge_source`: `"gitnexus-only"` | `"llm-only"` | `"both"`
- `confidence`: `"high"`（both）/ `"needs_review"`（single）

落地：两轨各自产 `<vuln>_llm_queue.json` 与 `<vuln>_gitnexus_queue.json`，合并器产出 `<vuln>_exploitation_queue.json`（下游 exploit/report 现有消费不变）。

### 4.2 vuln 阶段合并（verdict）

- **去重键**：`(vuln_type, sink_id, source, location)`
- **verdict**：vulnerable OR（任一 vulnerable → vulnerable）
- **来源/置信度**：both→high；single→needs_review
- **下游**：high→直接进 exploitation；needs_review→进 queue 标低置信

### 4.3 recon 阶段合并（情报，非 verdict）

recon（pre-recon/recon §4.1/§4.2）产**结构化情报**（entry points / 路由组 / endpoint context），**不是** vulnerable/safe verdict。合并性质不同：

- **去重键**：情报主键（entry point = `method+path`；route group = `handler_id`；endpoint context = `method+path`）
- **字段冲突**：取"危险侧"（auth 任一无→无；framework origin 任一 auto-generated→auto-generated；ownership 任一 none→none）——见原则 5
- **来源标记**：同 4.2，供下游（vuln agent 读情报时）知晓该情报是单轨还是双轨一致

> **两阶段区分**：recon 合并的是"情报字段"，用危险侧规则；vuln 合并的是"verdict"，用 OR。spec 之前混为一谈，v2 明确分开。

---

## 5. 逐环节设计

> 每节：目标产物 / LLM 轨 / GitNexus 轨（产链 + LLM 判）/ 合并要点 / vs 现状差距 / 成本

### 5.1 pre-recon（sink 穷举 + entry point + 模板转义）

- **目标产物**：entry points / sinks（分类+file:line+render context）/ 模板转义 / 架构概览（**情报合并**，§4.3）
- **LLM 轨**：跑 Phase 1/2（Entry Point Mapper + 两步模板 Sink Hunter + 变体审计），sink 清单作下限参考，独立探索清单外
- **GitNexus 轨**：确定性产 entry_points（路由注册点 AST）/ sinks（sink_detector）/ 模板转义（正则，补断路）/ 调用链 → **LLM 对每个 sink/entry 做语义确认**（是否真 network-reachable、render context）
- **合并**：entry/sink 去重 + 来源；情报字段冲突取危险侧
- **vs 现状**：`<phase0_data>` 只概览，Phase 1/2 仍 LLM 重跑；缺 GitNexus 轨明细 + 模板转义确定性（断路）
- **成本**：低（确定性层已实现，接线 + 模板转义正则）

### 5.2 recon §4.1（共享路由组）

- **目标产物**：共享 handler 路由组 + 每路由 auth + router 行号（**情报合并**）
- **LLM 轨**：LLM 产 4.1（现状）
- **GitNexus 轨**：调用图 handler→多路由 + 中间件注解检测 auth → LLM 确认可疑路由组（auth 标注冲突的）
- **合并**：按 handler 去重；auth 冲突取"无"（危险侧）
- **vs 现状**：保留 4.1（LLM）；缺 GitNexus 轨
- **成本**：中（调用图 + 注解）

### 5.3 recon §4.2（endpoint security context）

- **目标产物**：每端点 Auth / Middleware / Framework Origin / Ownership（**情报合并**）
- **LLM 轨**：LLM 产 4.2（现状）
- **GitNexus 轨**：framework-analyzer 接通（确定性 finale/epilogue，死通道）+ 中间件注解 + ORM 谓词 ownership → LLM 确认
- **合并**：按端点；framework origin 冲突取 auto-generated；ownership 冲突取 none
- **vs 现状**：保留 4.2（LLM）；framework-analyzer 死通道未接通
- **成本**：framework-analyzer 接通（低）+ ORM 谓词（中）

### 5.4 vuln-injection（正向 source→sink）

- **目标产物**：正向 source→sink findings（verdict 合并，§4.2）
- **LLM 轨**：Task Agent 正向 trace（slot 分型 + concat 失效判定），不被锚定
- **GitNexus 轨**：GitNexus 正向 taint 候选链（**依赖 §6 P0/P1**）+ 确定性 sanitizer 标注 → **LLM 链判定 pass**（给定链判 verdict + slot）
- **合并**：按 sink+source 去重；verdict OR
- **vs 现状**：LLM 读 hints 参考；缺 GitNexus 轨 findings
- **成本**：sanitizer 表 + concat 检测（中）；链判定 pass（per-链 LLM）；taint 依赖前置

### 5.5 vuln-xss（反向 + render context）

- **目标产物**：反向 sink→source findings（verdict 合并）
- **LLM 轨**：Task Agent 反向 trace（render-context×encoder + Stored DB-read checkpoint）
- **GitNexus 轨**：GitNexus 反向连边 + DB read↔write 跨表连线（补 Stored write 端）+ 确定性 encoder 标注 → **LLM 链判定 pass**
- **合并**：按 sink+source 去重；verdict OR
- **vs 现状**：LLM 读 hints；缺 GitNexus 轨 + Stored write 端补强
- **成本**：反向连边 + 跨表连线（中）；依赖 taint 前置

### 5.6 vuln-ssrf（反向 + 7 步）

- **目标产物**：反向 + 7 步 findings（verdict 合并）
- **LLM 轨**：Task Agent 反向 trace（7 步 + 5 类 context-match）
- **GitNexus 轨**：GitNexus 反向连边 + 确定性 SSRF-sanitizer 标注（URL allowlist/scheme/IP-CIDR/cloud-metadata）→ **LLM 链判定 pass**
- **合并**：按 sink 去重；verdict OR；**补 schema 缺字段**（path/verdict/witness_payload）
- **vs 现状**：LLM 读 hints；缺 GitNexus 轨；schema 缺字段
- **成本**：SSRF-sanitizer 表（中）

### 5.7 vuln-authz（IDOR / dominance）⭐

- **目标产物**：每端点 guard 充分性 findings（verdict 合并）
- **LLM 轨**：LLM dominance trace（现状 + framework guidance）
- **GitNexus 轨**：调用图必经节点（post-dominator）+ 中间件注解 + ORM 谓词 ownership + framework-analyzer 接通 → **LLM 对每条 GitNexus 候选链（handler→sink 无 ownership guard）判 verdict**
- **合并**：按 endpoint 去重；verdict OR；framework origin 取 auto-generated；ownership 取 none
- **vs 现状**：已加强 framework guidance；缺 GitNexus 轨 dominance + ORM ownership + framework-analyzer 接通
- **成本**：dominance 算法（中）+ ORM 谓词（中）+ framework-analyzer 接通（低）。**净进步方向**

### 5.8 vuln-auth（配置常量级）

- **目标产物**：认证/会话 missing_defense findings（verdict 合并）
- **LLM 轨**：LLM 9 类检查（现状）
- **GitNexus 轨**：确定性配置扫描（header/cookie/HSTS/CORS）+ JWT claim 读取点 + rate-limit 检测 → **LLM 确认可疑配置项**
- **合并**：按 endpoint+defense 去重；verdict OR
- **vs 现状**：原始 9 类 LLM；缺 GitNexus 轨（**纯增益**）
- **成本**：配置扫描器（低-中）

---

## 6. 前置依赖（基础设施，必须先做）

- **P0**：`parameter_graph.json` 落盘（修 taint 断层 `code_index/__init__.py:212-312`：CodeIndex 加 `parameter_graph` 字段 + `write_index_files` 真落盘 + 改真 docstring）。**无此前置，GitNexus 轨的 source→sink 候选链为空**（inj/xss/ssrf 受影响）。
- **P1**：taint 上游接通（`activities.py:245-247` 空桩 → 改触发保守回退 `return None`；本期不接真实 LLM taint per-function，成本爆炸）。
- **framework-analyzer 接通**：确定性产 finale-rest/epilogue → 注入 GitNexus 轨（recon §4.2 + authz）。当前死通道。
- **GitNexus 索引硬失败**（⚠️ 自检修正，2026-06-24）：核实当前 `run_code_index`（activities.py:247-280）**已经是硬失败**——CLI 不可用 / 索引失败 / MCP 失败三处都 `raise PentestError(CODE_INDEX_FAILED)`，注释明示 "no degradation"。且 `_StubMCPClient` / `_build_code_index_fallback` **实际不存在**（grep 双确认；Plan 4 与 remove-minimal-fallback plan 都基于臆测的孤儿/降级路径）。**结论：硬失败方向已实现，Plan 4（降级）+ remove-plan（删孤儿改 raise）均废弃——目标已达成**。双轨架构要求 GitNexus 必须可用（失败即停，不半残）。**唯一相关待办**：修索引慢（memory `pre-recon-gitnexus-blockage` 记录 >10min 超时拖死 agent；stop() 兜底已修，上游同步阻塞/索引慢待修）——属性能问题，非降级问题，另开 plan。
- **合并器扩展**：`run_merge_sink_reports` → 通用双轨合并器（§4，覆盖 sink/entry/taint/authz/config 全产物类型）。

---

## 7. 评估文档更正（重要）

基于实际读代码（2026-06-24），`docs/whitebox-refactoring-assessment.md` §0.3 两处判断已过时：

- "重构删除 recon 4.1/4.2" → **实际保留**（`recon.txt:252` §4.1、`:291` §4.2）
- "authz 无确定性替代" → **已补回** framework guidance（`vuln-authz.txt:70-89`）
- 原始项目 SharedKnowledge 是**死代码通道**（`_shared-knowledge.txt` 零 @include），agent 间靠 markdown 文本传递。

---

## 8. 不做什么（YAGNI / 范围外）

- 不替代 LLM 轨（本期）；不优化省 token（本期，但 GitNexus 轨链判定天然比自主探索省）
- 不做完整 dominance 数学证明（用调用图启发式 + LLM 语义确认）
- 不含 misconfig（重构范围外）
- 不改 exploit agent / report 渲染（消费方按 merge_source/confidence 差异化即可）
- 不接真实 LLM taint per-function 全量（成本爆炸；本期 taint 用保守回退）

---

## 9. 验收标准

**Functional（架构跑通）**：
1. 每个 vuln 类双轨各自跑通，产出 `<vuln>_llm_queue.json` 与 `<vuln>_gitnexus_queue.json`
2. 合并器产出 `<vuln>_exploitation_queue.json`，每条带 `merge_source` + `confidence`
3. 冲突规则生效：verdict OR（vuln）+ 字段危险侧（recon）；both→high、single→needs_review 正确
4. 合并后 findings = 两轨并集（去重后），不丢任一轨的项
5. GitNexus 索引失败时优雅降级（GitNexus 轨空，只有 LLM 轨），不拖死 pipeline

**质量（基线，不退步）**：
- 相比纯 LLM 轨，合并后 findings 召回率 ≥ LLM 轨（OR 保证不降）
- 记录双轨差异（gitnexus-only/llm-only/both 比例）供长期验证

---

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| **GitNexus 索引超时拖死 pipeline** | GitNexus 轨优雅降级为空（§6）；stop() 兜底已修 |
| LLM 轨被锚定 | LLM 轨不注入 GitNexus 链/taint；prompt 强制"独立探索清单外"（原则 2） |
| 确定性层覆盖不全 | LLM 轨兜底；未覆盖语言明确标注 |
| 合并器冲突误报淹没 | merge_source+confidence 标记，needs_review 差异化，不淹没 high |
| GitNexus 轨链判定 pass 成本 | per-链 LLM，比自主探索省；链数受 taint/sink 数约束；可设上限 |
| taint 落盘后 LLM taint 成本 | 本期保守回退，非真实 LLM taint |

---

## 11. 实施分层（供 writing-plans 参考）

- **Phase 0 基础设施**：§6 P0/P1（taint 落盘）+ framework-analyzer 接通 + 通用合并器 + GitNexus 索引降级。所有环节 GitNexus 轨的前置。
- **Phase 1 逐环节应用**：§5.1-5.8。建议顺序：pre-recon（确定性产物最齐）→ recon §4.1/§4.2 → authz（净进步价值最高）→ inj/xss/ssrf（依赖 taint）→ auth（纯增益最稳）。

每个环节可独立交付（双轨合并天然增量：先上 LLM 轨 + 部分 GitNexus 轨，逐步补全）。

---

## 12. 整合与执行须知（自检后，2026-06-24）

自检发现：多个 plan 由不同 agent 写，存在**跨 plan 冲突 + 代码现状臆测**（已发现 `_StubMCPClient`/`_build_code_index_fallback`/Plan 1 fallback 引用三处臆测）。**执行前必读本节。**

### 12.1 已裁决/修正
- **Plan 4 + remove-plan 废弃**：核实当前 `run_code_index`（activities.py:247-280）已是硬失败（三处 `raise PentestError`，注释 "no degradation"），`_StubMCPClient`/`_build_code_index_fallback` 不存在（grep 双确认）。两 plan 基于臆测孤儿，目标已达成。
- **Plan 1 清理**：去掉对不存在的 `_build_code_index_fallback` 的引用（Plan 1 调研臆测）。
- **Plan 8 xss 路由修正**：xss 改用 `SinkCallSite.category == SinkCategory.XSS`（SlotContext 无 render context）；`extract_candidate_chains` 加 `sink_call_sites` 参数；`CandidateChain` 加 `render_context`（sink_subtype 映射）。injection/ssrf 路由不变。

### 12.2 run_agent 统一（Plan 2/5/6 都改 run_agent，避免互相覆盖）
- **Plan 2 是 run_agent prompt_variables 的唯一 owner**。
- 把 `_to_endpoint`（当前是 activities.py:634/698 的**嵌套闭包**，不在模块级）**提为模块级**，或新建模块级 helper `_framework_endpoints_from_json(deliverables) -> list[InferredEndpoint]`。
- **Plan 5/6 不重写 framework 注入**，只在自己的 if 分支追加 key：Plan 5 加 `pre_recon_gitnexus_track`（pre-recon），Plan 6 加 `recon_gitnexus_track`（recon）。framework 注入由 Plan 2 负责。
- 最终 run_agent 的 prompt_variables 形态：三个 if 分支按 `agent_name` 互斥——`{framework_endpoints_summary + recon_gitnexus_track (仅 recon)}` / `{pre_recon_gitnexus_track (仅 pre-recon)}`。

### 12.3 vuln 阶段顺序（Plan 3/7/8/9 都插 activity，固化顺序）
`(Plan 9 auth-config-scan，gather 前) → asyncio.gather(vuln agents) → Plan 7 authz-judge → Plan 8 chain-verdict → Plan 3 run_merge_dual_track_queues → log_phase_complete`
- **Plan 3 是 merger 唯一 wiring 方**。Plan 7/8 只插自己的 GitNexus 轨 activity（在 merger 之前），**不重复 wire merger、不写 hasattr 守卫**。
- Plan 9 不 wire merger（靠 Plan 3）。

### 12.4 执行前必做：核实代码（不信行号/臆测）
- agent 写的 plan（3/6/7/8/9）file:line/签名有臆测。每个任务执行前：**用符号 grep（函数名/类名）定位**，不信任 plan 写的行号（普遍漂移 5-17 行）。
- 占位符/key 命名规范：variables 键小写下划线 + 占位符全大写（manager.py:154-157 `key.upper()`）。

### 12.5 修正后可执行性
- **Ready**：Plan 1（去 fallback 引用）、2、5、9
- **校准后**：Plan 3、6、7（行号/路径核实）
- **已修关键 Task**：Plan 8（xss 路由）
- **废弃**：Plan 4、remove-plan
