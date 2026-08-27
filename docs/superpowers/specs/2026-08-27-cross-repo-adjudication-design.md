# 跨仓关联两阶段化设计（关联 Agent 改造 + 跨仓裁决）

- **日期**：2026-08-27
- **分支**：feat/fork-py
- **状态**：Design（已与用户逐节确认 → 待用户审阅 spec）
- **作者**：brainstorming 会话产出
- **上游设计**：`docs/superpowers/specs/2026-06-22-cross-repo-microservice-scanning-design.md`（CLI 编排器 + 关联 Agent）、`docs/superpowers/specs/2026-08-24-cross-repo-correlation-web-revival-design.md`（web 复活三段接力，已全部落地）

---

## 1. 背景与动机

2026-08-27 对跨仓关联链路做了全面代码核实，发现以下问题与缺口：

**实现缺陷**：

- **P1（真缺陷，本设计主修）**：per-edge 关联 Agent 的 prompt 变量 `deliverables_path` 指向关联 workspace 的 `out_dlv`（`orchestrator.py` `edge_runner`），而合并 queue 在 gather **之后**（步骤 5 `write_correlation_deliverables`）才写入该目录——**Agent 运行时该目录为空**，prompt 引导的 "in the deliverables queues under `<deliverables-from-scans>`" 落空；各子仓 queue 路径从未进 prompt。后果：`flows.vuln_refs` 的"后端漏洞"是 Agent 重新读码再推断的 sink（title/severity/location 自造），与子仓扫描产物**无外键关联**——前端攻击链卡片对不上子仓漏洞详情。
- **P2**：spec 2026-06-22 §6.1 承诺的多跳链（gateway→A→B）未实现——`_merge_edge_results` 只做 per-edge 拼接，无串联。
- **P3**：设计字段未接线——`RepoSpec.proto_roots`（proto 搜索提示）未进 prompt；`cross_service_source` 恒 None 无写入方（死字段）。

**价值缺口**（用户 2026-08-27 需求）：

1. 投喂形态 = 多仓扫描结果的**目录导读**（列出哪些文件、起什么作用，引导关联 Agent 自己去读）；
2. 关联 Agent 要有**固定方法论大步骤**，每次都走同构流程；
3. **翻案审查**：后端部分漏洞因 RPC 开放性/可达性被判非漏洞，跨仓视角（经 gateway 可达）应重审；
4. **降误报**：前端仓漏洞可能是调用后端微服务产生的（单仓视角只见本仓代码判为漏洞），后端实际有防护（如预编译）——跨仓证伪/降级；
5. **留证为一等公民**：漏洞直接体现在关联报告；非漏洞也要有留档留证——记录详细分析过程、验证过程、为什么是非漏洞。

**可复用的既有事实**（本设计的地基）：

- `dismissed_findings.json` 已存在（`core/services/dismissed_archive.py`，spec 2026-08-27 §4 非漏洞留档）——**升案批投喂源现成**；
- `entry_points.json` 路由表（recon 产物，`METHOD route @ file:line` + handler）——from 仓对外入口锚点现成；
- `per_repo_queue` 在 gather 前已收集（`run_correlation_phase` 步骤 1）——喂给 Agent 只差 prompt 接线。

## 2. 目标与非目标

**目标（本次范围）**

- 修 P1：artifacts-guide 目录导读投喂 + `vuln_refs` 外键贯通（`vuln_id` 引用子仓 queue 的 `ID`）；
- 关联 Agent 方法论固化（固定大步骤，每轮同构）；
- 新增**阶段 B 跨仓裁决**：升案/降案/确认三向结论 + `adjudication-log.json` 双向留证档案；
- 合并层确定性校验（vuln_id 存在性，防幻觉）与多跳链拼装（P2 顺修，边邻接启发）；
- `proto_roots` 接线（P3 顺修）；`cross_service_source` 死字段清理（语义由裁决卡承接）；
- web 端：correlation 详情 API 组装裁决数据 + 前端裁决卡展示 + live 事件。

**非目标（显式排除）**

- **不写确定性提取器**（proto parser / RPC client AST / handler 注册提取）——用户明确决策：保持 Agent 推断为主，确定性只做校验与拼装；
- **裁决结论不回写合并 queue**——adjudication-log 为独立叠加产物，黑盒段③输入不变（回写联动留后续 §14）；
- 不改段①子仓白盒内核与段③黑盒验证内核；
- 不做 gRPC 进程内验证（上游 §13 维持）；
- 全局侦察 Agent（方案 3：先产 method 锚点清单再 per-edge 深判）留作规模化演进。

## 3. 已定决策（brainstorming 产出）

| # | 决策 | 说明 |
|---|---|---|
| 1 | **Agent 推断为主** | 不写确定性提取器；确定性只做防幻觉校验与产物拼装 |
| 2 | **两阶段：关联（边视角）→ 裁决（发现视角）** | 翻案一条 dismissed finding 要看"是否经任一条边可达"（边视角会重复审或漏审）；证伪一条前端漏洞要看后端防护（方向未必与边对齐）——两视角正交，裁决独立成阶段 |
| 3 | **投喂 = 目录导读（artifacts-guide）** | 编排层确定性生成：列文件路径 + 作用说明 + 缺失标注，Agent 自己按需读——不塞内容、省 token、agent-native |
| 4 | **裁决三范围** | 升案（dismissed 重审）+ 降案（跨仓证伪）+ 确认（关联漏洞出卡）——用户三选确认 |
| 5 | **不回写合并 queue** | 翻案项进报告"翻案候选"节供人工复核；黑盒输入不变 |
| 6 | **留证两层分置** | 单仓层 = `dismissed_findings.json`（已有）；跨仓层 = `adjudication-log.json`（新） |

## 4. 架构总览（三段接力外壳不变，段②内部两阶段化）

```
段① N 仓白盒（不变：复用/现扫，产出 queue + dismissed_findings.json + entry_points.json）
  ↓
段② 关联阶段 = 阶段 A（关联）+ 确定性合并层 + 阶段 B（裁决）   ← 本次改造的全部
  ├─ A. per-edge 关联 Agent（改造：artifacts-guide + 方法论 + vuln_id 引用）
  ├─ 确定性合并层（vuln_id 校验 + 多跳拼装；零推断）
  └─ B. 裁决 Agent（新增：发现驱动，升案/降案/确认，adjudication-log 留证）
  ↓
段③ 黑盒 gateway 验证（不变：消费 topology + 原合并 queue；裁决 log 为叠加产物不进黑盒输入）
```

## 5. 阶段 A：per-edge 关联 Agent 改造

### 5.1 artifacts-guide 生成（编排层，确定性）

`run_correlation_phase` 步骤 1 已遍历 `repo_workspace_paths` 收集 queue——顺手探测每仓的产物文件集，生成 per-edge guide（每边只含本边两仓），作为 prompt 变量注入（**同时修掉 P1**：`deliverables_path` 不再指向空的 out_dlv）：

```
<artifacts-guide>
## from 仓 (gateway = /path/to/node-gw) 扫描产物：
- <path>/whitebox/intermediate/entry_points.json — HTTP 路由表（METHOD /path @ file:line + handler）。
  定位对外入口的首选锚点，须与源码交叉验证。
- <path>/{vc}_exploitation_queue.json — 已确认漏洞（字段 ID/title/severity/location）。
  flows 引用漏洞时必须优先引用这里的 ID。
- <path>/whitebox/intermediate/dismissed_findings.json — 判非漏洞留档（每条含
  vuln_class / dismiss_reason / evidence）。可用于理解后端已排除项；其重审由阶段 B
  负责，你不需要翻案。
（标注缺失的文件：…（缺失））
## to 仓 (order-svc = …) 扫描产物：同结构
## proto 提示：proto_roots = [...]（若声明）——proto/service 定义优先在这些目录找。
</artifacts-guide>
```

- 文件路径取各子仓 deliverables 实际探测结果（queue 三处 glob 回落链沿用），缺失如实标注；
- `proto_roots` 透传进 guide（修 P3 前半）。

### 5.2 方法论固定大步骤（prompt 固化，每轮同构）

1. **读导读**：读 artifacts-guide，按需读产物文件（queue 优先记 `ID`/`location`）；
2. **from 仓定位入口与调用点**：以 entry_points.json 为锚点交叉验证源码；定位 RPC client 调用点（grpc-js / connect-es / proto-loader 等），提取 service/method + call_site；
3. **to 仓定位 handler**：定位 method 的 handler 实现，读 handler 内 sink；
4. **串攻击链**：`flows.vuln_refs` **必须优先引用 queue 已有 ID**（带 `source: "queue"`）；代码里新发现的 sink 标 `source: "agent-discovered"`，不与扫描结果混淆；
5. **拓扑与边界**：现有职责保留——calls / boundaries / declared-missing 边（输出 schema 既有字段不动）。

### 5.3 schema 变化

`flows[].vuln_refs[]` 扩展两个字段：

```json
{"vuln_id": "INJ-001", "service": "order-svc", "title": "…", "severity": "high",
 "location": "internal/dao/order.go:88", "source": "queue | agent-discovered"}
```

`vuln_id`/`source` 进 structured_output_schema 的 properties（不进 required——旧形态容错）。

## 6. 合并层确定性校验与拼装（零推断）

### 6.1 vuln_id 存在性校验（防幻觉）

收集阶段已建 `per_service_id_set: {service: set(entry["ID"])}`。合并时对每条 `vuln_refs[]`：`vuln_id` 非空且不在对应 service 的 ID 集 → 该 ref 标 `invalid_ref: true`（**不删**，透明单列进报告）。

### 6.2 多跳链拼装（P2 顺修，边邻接启发）

拼装规则（诚实标注依据，不做函数级可达声明）：服务 X 既是某 flow 的 `edge_to`（有 gateway→X 的攻击链），又是另一条边的 `from_`（X→Y 存在 calls）→ 产出候选多跳链 `gateway→X→Y`，`basis: "edge-adjacency"`、`confidence: "structural"`。落 `cross-service-flows.json` 顶层新字段 `multi_hop_chains`。函数级可达验证留 §14。

## 7. 阶段 B：跨仓裁决 Agent（新增）

### 7.1 触发时机与批组织

- 跑在**阶段 A 产物落盘之后**（裁决吃 topology/flows）；阶段 B 异常不影响阶段 A 产物交付与 scan 终态；
- 批 = **(service, vc) × 输入源**，两类输入源：
  - **queue 批**：该 service 该 vc 的 queue 条目 → 结论方向 `confirm`（跨仓可达性确认）或 `downgrade`（跨仓防护证伪）；
  - **dismissed 批**：`dismissed_findings.json` 是**单文件**（`{"dismissed": [...]}`，每条含 `vuln_class` 字段），按 `vuln_class` 过滤组织批；条目**全量进批**，`dismiss_reason` 含可达性/暴露面的排批内前部（排序只影响优先级，不影响覆盖）→ 结论方向 `upgrade`（翻案候选）或 `maintain`（维持）；
- 每批一个 `cross-repo-adjudication` Agent 调用，批内 finding 数上限（默认 15，防爆上下文，超出分片）；并发沿用 `asyncio.Semaphore(get_max_concurrent())`；
- 典型两仓星型规模：≤ services × vcs × 2 ≈ 10-20 批。

### 7.2 裁决 Agent 输入

本批 finding 原文（queue entry / dismissed entry，含否决理由）+ 关联产物摘要（topology 边 + 本 service 相关 flows + multi_hop_chains）+ artifacts-guide（同阶段 A）+ 双仓源码路径。工具仍限定 read-only（grep/read_file/glob），不依赖引擎专有能力。

**升案输入源覆盖面（如实标注）**：`dismissed_findings.json` 当前只含 GitNexus 轨 chain-verdict 判 safe 的项（`source_track: "gitnexus"`，`dismissed_at_stage: "chain-verdict"`）；LLM 轨探索排除项无留档是**既有缺口**（非本设计引入，不在本设计范围）。

### 7.3 裁决卡 schema（adjudication-log.json 每条）

```json
{
  "direction": "upgrade | downgrade | confirm | maintain",
  "finding_ref": {"service": "order-svc", "vuln_id": "INJ-001", "origin": "queue | dismissed"},
  "conclusion": "vulnerable | not-vulnerable | downgraded | needs-review",
  "cross_service_context": "经 gateway POST /orders → order.v1.OrderService/CreateOrder 可达",
  "analysis_process": ["① 读 dismissed 档案：否决理由 = internal 不可达", "② 查 topology：gateway→order-svc 边存在该 method 调用", "…"],
  "verification_evidence": [{"repo": "order-svc", "location": "internal/dao/order.go:88",
                              "snippet": "db.QueryContext(ctx, sql, args…)", "note": "参数化查询"}],
  "reasoning": "完整自然语言论证：为什么翻案 / 为什么是非漏洞",
  "confidence": "high | low"
}
```

- `direction` 与 `conclusion` 矛盾（如 upgrade 但 not-vulnerable）→ 合并层校验拦下标 `needs-review`；
- **每条 finding 必须出卡**（无论正反结论）——留证为一等公民（需求 5）；批内 Agent 未覆盖的 finding → 编排层补 `error` 占位卡（该 finding 的裁决失败透明可见，不静默丢失）。

### 7.4 prompt（`prompts/cross-repo-adjudication.txt` 新建）

结构：role（Cross-Repository Adjudicator，只读工具）→ 输入块（finding 批原文 + topology/flows 摘要 + artifacts-guide）→ 方法论（①逐条读原始判定与理由 → ②查跨仓可达性（topology/flows）→ ③必要时读双仓源码验证防护/可达 → ④出卡：正反结论都须 `analysis_process` + `verification_evidence`（file:line）+ `reasoning` 完整）→ output-format（裁决卡 JSON 数组，structured_output_schema 强制）。

### 7.5 事件流

`CorrelationEventWriter` 增 `phase("adjudication", started/completed)` 与批级进度事件（web live 页显示两阶段）。

## 8. 产物全景

| 产物 | 变化 |
|---|---|
| `cross-service-topology.json` / `trust-boundaries.json` | 不变 |
| `cross-service-flows.json` | `vuln_refs[]` 扩 `vuln_id`/`source`/`invalid_ref`；顶层新增 `multi_hop_chains` |
| `adjudication-log.json` | **新增**：裁决卡列表（§7.3 schema） |
| `correlation-report.md` | 增"跨仓裁决"章节：升案候选 / 降级 / 确认三表，每条带论证摘录与证据 location——**非漏洞与漏洞同表留证**；invalid_ref 单列 |
| 合并 `{vc}_exploitation_queue.json` | **不变**（裁决不回写，决策 #5） |
| artifacts-guide | 中间产物，落 `whitebox/intermediate/` 供 debug（可选） |

## 9. web 端

- `GET /scans/{id}/correlation` 组装加 `adjudication`（裁决卡）与 `multi_hop_chains` 字段；
- CorrelationTab 增**跨仓裁决区**：按 direction 三向分组卡片（徽标 + conclusion + `analysis_process`/`verification_evidence` 可展开）+ invalid_ref 标注；
- live 页消费 adjudication phase/批事件；i18n 双语（zh/en 词条）。

## 10. 错误处理

| 场景 | 处理 |
|---|---|
| 子仓产物缺失（queue / dismissed / entry_points） | guide 如实标（缺失）；对应裁决批目标为空则跳过该批 |
| 阶段 A 单边失败 | 现有单边隔离不变（status=error） |
| vuln_id 幻觉引用 | 合并层标 `invalid_ref`，报告单列 |
| 阶段 B 单批失败/超时 | 该 finding 出 `error` 占位卡留档，不拖垮其它批与 A 产物 |
| 阶段 B 整体异常 | A 产物照常交付；`adjudication-log.json` 写 `{"error": …}`；scan 终态不变（报告透明标注裁决段失败） |
| direction/conclusion 矛盾 | 合并层校验拦下标 `needs-review` |
| 批内 Agent 漏判 finding | 编排层补 `error` 占位卡，不静默丢失 |

## 11. 测试策略

全部独立模块，避开 feat/fork-py 预存挂起 suite：

- **multi 单测**：guide 生成（文件存在/缺失两态、proto_roots 透传）、vuln_id 校验（有效/无效/空）、多跳拼装（邻接成链/孤立不成链）、批组织（分片/空批跳过/两类输入源）、adjudication-log 落盘（含 error 占位卡）、direction-conclusion 矛盾拦截、阶段 B 失败不影响 A 产物；
- **prompt 契约**：`cross-repo-correlation.txt` 含方法论五步/artifacts-guide 占位；`cross-repo-adjudication.txt` 输出契约（structured_output_schema 对齐裁决卡）；
- **web 后端**：correlation 详情 API 组装 adjudication/multi_hop_chains；
- **前端**：裁决卡三向分组渲染/证据链展开/error 卡降级态/i18n；
- **端到端 fixture 冒烟**：迷你 Node gateway + Go gRPC 后端 fixture（含已知攻击链 + 预置 dismissed 项），pipeline-testing 跑通两阶段，断言产物结构与关键卡存在（upgrade/downgrade/confirm 各至少一张）——不断言 Agent 具体论证文本（概率性）。

## 12. 风险登记

| 风险 | 等级 | 对策 |
|---|---|---|
| 裁决质量概率性（翻案/证伪是 Agent 判断） | 中 | 产物性质固化为"候选 + 证据 + 置信度"（对齐上游决策 #6）；`needs-review` 透明单列；不回写 queue（误判不污染下游） |
| 阶段 B token 成本增量（每批多轮读码） | 中 | 批上限 15 条；两仓星型 ≤ 20 批；Semaphore 同源限并发；dismissed 项按否决理由筛选（可达性/暴露面类优先）控制批数 |
| 升案筛选漏（否决理由分类不可靠） | 低 | dismissed 全量进批（筛选只影响优先级不影响覆盖）；批内 Agent 亦可对非可达性理由自主重审 |
| artifacts-guide 路径失配（子仓 deliverables 结构变化） | 低 | 探测复用 queue 三处 glob 回落链（tiering 对齐）；缺失如实标注 |
| 多跳链边邻接启发过宽（结构性假链） | 低 | `basis`/`confidence: structural` 显式标注；供人工复核非结论 |
| prompt 膨胀（guide + 方法论 + 产物职责） | 低 | guide 是路径清单非内容；方法论五步紧凑；契约测试锁定结构 |

## 13. 验收清单

- [ ] per-edge Agent prompt 含 artifacts-guide（两仓产物路径 + 作用 + 缺失标注 + proto_roots）与五步方法论；
- [ ] `flows.vuln_refs` 带 vuln_id/source；引用不存在 ID 被标 `invalid_ref`（报告单列）；
- [ ] `cross-service-flows.json` 含 `multi_hop_chains`（边邻接标注）；
- [ ] 阶段 B 跑在 A 产物落盘后；(service, vc)×两类源 分批，批上限与 Semaphore 生效；
- [ ] `adjudication-log.json`：每条目标 finding 都有卡（含 error 占位）；upgrade/downgrade/confirm 三向可出；
- [ ] `correlation-report.md` 增"跨仓裁决"章节，非漏洞结论与漏洞同表留证（分析过程 + 验证证据 + reasoning）；
- [ ] 合并 queue 字节不变（决策 #5 回归断言）；段①/段③行为零回归；
- [ ] web：详情 API adjudication 字段 + 前端裁决卡渲染 + live adjudication 事件；
- [ ] fixture 端到端冒烟（含 dismissed 预置项）产物结构与关键卡断言通过；
- [ ] CLI `supernova-multi` 与 web 编排两路径行为一致（同走 `run_correlation_phase`）。

## 14. 后续阶段（本设计不实现）

- **裁决结论回写联动**：翻案项入合并 queue（带 `cross_service_source`）供黑盒段验证、证伪项标 `suppressed`——待裁决质量经真机验证后再议；
- **函数级可达验证**：Go 侧轻量调用图（`go/ast`）支撑多跳链与 handler→sink 可达性的确定性验证（parameter_graph 对 Go 跳过 typed-param 传播，当前不可白嫖）；
- **全局侦察 Agent**（方案 3）：大仓/多后端场景先产 method 锚点清单，per-edge 吃锚点省重复读仓；
- **gRPC 进程内验证**（上游 2026-06-22 §13 维持）。
