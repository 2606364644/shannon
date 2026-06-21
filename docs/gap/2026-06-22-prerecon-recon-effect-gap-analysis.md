# pre-recon / recon 阶段代码安全审计效果差距分析

> 对比原始 Shannon（TypeScript, `/Users/mango/project/shannon-refactor/shannon`）与重构 Shannon-py（Python）在 **pre-recon / recon 两个情报产出阶段**的安全审计效果差距。
>
> **聚焦口径**：效果/效能——这两个阶段**不直接产出漏洞**，而是为下游 vuln/exploit agent 产出"情报"（sink 清单、入口点、攻击面画像、污点流、框架/前端线索）。本文问的是：**这些情报的产出质量如何、确定性层是否真实有效、情报有多少真正到达下游 prompt、对下游漏洞检出有多大实际增益**。刻意避开 [`2026-06-18-prerecon-recon-gap-analysis.md`](2026-06-18-prerecon-recon-gap-analysis.md) 已覆盖的"机制接线断链诊断"（PR-01~PR-21）和 prompt 文本逐行对比。
>
> **数据来源**：① 逐文件核验两项目真实扫描产出 ② 两项目 pre-recon/recon 代码 + prompt 核验 ③ 3 个并行核验 agent 的 file:line 证据。关键数字均来自真实 deliverable 文件，非转述。
>
> **日期**：2026-06-22

---

## 0. 与现有 gap 文档的关系定位

本文档**不重复**以下已有结论，仅在必要时交叉引用：

| 现有文档 | 已覆盖维度 | 本文档关系 |
|---|---|---|
| [`2026-06-18-prerecon-recon-gap-analysis.md`](2026-06-18-prerecon-recon-gap-analysis.md)（PR-01~PR-21） | **机制/接线视角**：ParameterPropagationGraph 孤儿、LLM taint 桩、recon 漏传 retry_policy、route_chains 孤儿等断链诊断 | **互补**。06-18 答"机制哪里断了"，本文答"断了之后实测产出效果如何、对检出影响多大"。本文 RE- 编号与 06-18 PR- 编号独立 |
| [`sink-gap-analysis-v2.md`](sink-gap-analysis-v2.md) §2 | sink detector **prompt 文本**逐行对比 | **不重复**。本文聚焦运行时产出与下游消费 |
| [`2026-06-21-vuln-agent-gap-analysis.md`](2026-06-21-vuln-agent-gap-analysis.md) | vuln agent 全链路**结构对齐** | **引用**。vuln 是 pre-recon/recon 情报的下游消费者 |
| [`2026-06-21-auth-effect-gap-analysis.md`](2026-06-21-auth-effect-gap-analysis.md) AU-1 | 端到端实测闭环缺口 | **同构问题**。本文 §2.3 / RE-6 给出 pre-recon/recon 视角的同类发现 |
| [`route-analysis-binding-gap-analysis.md`](route-analysis-binding-gap-analysis.md) | 攻击链 confidence / 路由绑定 | **引用** RA-1 |

**核心问题**：现有文档要么从"机制接线"角度、要么从"prompt 文本"角度对比 pre-recon/recon，缺一份从"**情报产出质量 + 确定性层真实有效性 + 情报→下游消费 + 端到端实测**"四层效果角度的对比。本文填补。

---

## 1. pre-recon/recon 效果分析的四层框架

pre-recon/recon 与 auth/injection 不同：它**不直接检出漏洞**，而是产出情报供下游消费。因此其"安全审计效果"必须分四层评估，否则会误判——例如机制层断链（06-18 PR-01 是 P0）在效果层可能被 LLM 兜底掩盖。

| 层 | 评估问题 | 原始 Shannon (TS) | 重构 Shannon-py | 效果差距定性 |
|---|---|---|---|---|
| **L1 情报产出质量** | deliverable 厚度/具体性/覆盖 | 纯 LLM 单 agent（`pre-recon-code` 467 行 + `recon` 538 行） | LLM + 确定性层并行（`pre_recon` 372 行含 Phase 0 + `recon` 539 行） | **基本持平**，PY 字节密度略高（多 11-13KB file:line），见 §2 |
| **L2 确定性层有效性** | tree-sitter / call graph / sink detector / 污点流 的真实产出 | **无此层**（全仓零命中 tree-sitter/code_index；仅 2 个 regex 侧栏对 NodeGoat 产出全空） | 有完整层，但 **NodeGoat 实战全链降级**：语言误判 → call graph 失效 → 5 JSON 空壳 → sink 仅 3/12 有效 | **机制存在但实战失效**，见 §3 |
| **L3 情报→下游衔接** | 产出有多少真正进入下游 vuln/exploit prompt | 2 份 deliverable 自读 + `shared-knowledge.json` 半死（无 vuln prompt 读） | 2 份 deliverable 自读 + `static_dataflow_hints` @include + 6 类 JSON 孤儿 | **两边都有孤儿**，PY 多一条 sink 注入通道，见 §4 |
| **L4 端到端实测闭环** | pre-recon/recon→vuln 有无独立实测 | NodeGoat **完整跑完**（pre-recon→recon→vuln→exploit 全链） | 跑到 recon，**未到 vuln queue**（情报→检出因果链未端到端实测） | **TS 有 / PY 缺**，见 §2.3 / RE-6 |

**总裁决**：两项目 pre-recon/recon 的**情报产出方法论（L1）对齐**，下游消费方式（L3）都靠 deliverable 自读。重构的独有增量——确定性静态层（L2）——在 NodeGoat 这一硬案例上**实战失效**，但被高质量 LLM deliverable 兜底（§5），故**情报产出效果并未退化**，只是"确定性层 + LLM"路线实质退化成了"纯 LLM"路线。重构相对 TS 的唯一确凿下游可见增益是 `static_dataflow_hints` 的 sink 清单注入（RE-5），但质量打折。

---

## 2. 真实产出对比 ★

> ⚠️ **口径警示**：重构 `NodeGoat_shannon-1782059117521`（06-22 00:54）是**近期首个跑到 recon 阶段的独立扫描**，产出了完整 pre-recon/recon deliverable，但**未跑到 vuln 阶段**（deliverables 无任何 vuln queue/findings）。TS `NodeGoat_whitebox-1780678757791` 是**完整跑完**的扫描。因此两边的 pre-recon/recon 情报质量可直接对比，但"情报→vuln 检出"的因果链**只有 TS 端到端实测过**，PY 未实测。

### 2.1 重构 NodeGoat pre-recon/recon 产出（`workspaces/NodeGoat_shannon-1782059117521/deliverables/`）

| 产出 | 大小 | 情报丰富度 | 对下游增益 | 关键内容（核验） |
|---|---|---|---|---|
| `pre_recon_deliverable.md` | 46.9KB / 372 行 | **厚** | **高** | 11 section（§1 Summary … §10 SSRF Sinks + **Phase 0 Indexing Coverage**）；逐 endpoint 点名真实 file:line：`contributions.js:32-34` eval×3、`allocations-dao.js:77-79` `$where`、`user-dao.js:60-67` 明文比较、`config/env/all.js:8` 公开 session secret；§9 覆盖 25 view + 2 client JS 声明"coverage complete"；多抓到 RSA 私钥 git tracked、secret dump、session regenerate 不对称、username enumeration oracle |
| `recon_deliverable.md` | 54.5KB / 539 行 | **厚** | **高** | 10 section（§0 How to read … §9 Injection Sources）；§4 端点表 22 行（Method/Path/Required Role/Object ID Params/Auth/Code Pointer 六列，直接圈定 IDOR 候选）；§9 12 条 source→sink 数据流含完整调用栈伪码 + §9.9 汇总表；§8 三级越权候选（横向 7/纵向 3/上下文 6） |
| `code_index.json` | 77.5KB | **中**（block 全 + sink 部分有效，call graph 空） | **低** | `degradation_level: "full"`；24 blocks 真实解析（含 source_code 片段）；`sink_call_sites` 61 条（仅 3 条 ts-eval 真实）；`edges/entry_points/chains` **全空数组** |
| `code_index_summary.md` | 527B | **薄** | **无** | Language: **typescript**（误判，NodeGoat 是 CommonJS JS）；24/24 函数 "Unreachable"；Call Edges 0 |
| `static_dataflow_hints.md` | 9.2KB | **中**（sink 厚 / 污点流空） | **中** | "Sink 调用点" 61 条（3 ts-eval + 58 llm-sink-hunter）；"污点流（entry→sink）" **一行「本仓库无可达 sink 的污点流」= 完全空** |
| `entry_points.json` | 124B | **空壳** | **无** | `{"adjudicated_entry_points": []}` — 0 入口点（语言误判所致） |
| `framework_analysis.json` | 85B | **空壳** | **无** | `{"detected_framework": null, ...}` — 未识别 Express |
| `frontend_mapping.json` | 38B | **空壳** | **无** | `{"routes": [], "xss_chains": []}` |
| `route_chains.json` | 2B | **空** | **无** | `[]` — 空数组（印证 06-18 PR-05，且比文档描述更严重：连生成都失败） |
| `audit_plan.json` | 133B | **空壳** | **无** | `{"total_chains": 0, "tier1/2/3_chains": [], "estimated_llm_calls": 0}` — tiered audit 退化为空 plan |

### 2.2 原始 TS NodeGoat pre-recon/recon 产出（`workspaces/NodeGoat_whitebox-1780678757791/deliverables/`）

| 产出 | 大小 | 情报丰富度 | 关键内容（核验） |
|---|---|---|---|
| `pre_recon_deliverable.md` | 33.6KB / 467 行 | **厚** | 10 section（§1 Summary … §10 SSRF Sinks）；§5 攻击面表 30 条端点（按匿名/认证分组，每行带 handler 文件 + 主要漏洞）；§3 点名 `session.js:36/25`、`index.js:57-60`、`user-dao.js:61`（`return fromDB === fromUser` 明文比较）、`config/env/all.js:8`；§9 覆盖 25 模板逐个审计；§10 完整贴 `research.js:14-28` 代码 |
| `recon_deliverable.md` | 43.1KB / 538 行 | **厚** | 10 section；§4 端点 31 条（18 主路由 + 12 tutorial + 1 error，含 Required Role / Object ID / Auth Mechanism）；§5.6 零验证参数 15 个；§9 11 类注入源完整 source→sink 数据流 |
| 确定性层产物 | **不存在** | — | 无 `code_index.json` / `parameter_graph.json` / `static_dataflow_hints.md` / `entry_points.json` / `route_chains.json`（TS 无此层） |

### 2.3 端到端闭环不对称 ★★

| 维度 | TS NodeGoat | PY NodeGoat（最新） |
|---|---|---|
| pre-recon 产出 | ✅ | ✅ |
| recon 产出 | ✅ | ✅（首次跑到） |
| **vuln 阶段产出** | ✅ 完整（auth/inj/xss/ssrf/authz 全部 queue + evidence） | ❌ **未跑到**（deliverables 无任何 vuln queue） |
| **情报→检出因果链实测** | ✅ 端到端 | ❌ 仅情报侧 |

**含义**：TS 的 pre-recon/recon 情报**已被一次完整 vuln/exploit 流程消费并验证**（产出 17 条 auth + 多类其他漏洞 queue，见 auth-effect §2.2）。PY 的 pre-recon/recon 情报质量虽可从文件评估，但"这些情报在重构版下游 vuln agent 里是否真能转化为检出"**未经端到端实测**。这与 auth-effect AU-1 同构——重构 pre-recon/recon→vuln 的整条因果链目前**只有情报侧背书，没有检出侧背书**。

### 2.4 关于 `NodeGoat_whitebox-1780678757791-deliverables/`（连字符目录）

TS workspace 下另有一个 `-deliverables` 后缀目录（33 文件，含 `code_index.json`/`parameter_graph.json`）。**已确认是 PY 产出混入 TS 仓库**（三连证据）：
1. `code_index.json` 顶字段是 PY 专属 schema：`"repository": "/Users/mango/project/vuln-range/NodeGoat"`（PY 跑的源路径，不在 TS 仓内）+ `total_blocks`/`blocks[].source_code`/`decorators`/`language`/`edges`/`degradation_level`
2. `parameter_graph.json` = `{"taint_flows": [], "language_coverage": ["typescript"], ...}` — PY 专属字段名（`taint_flows`/`language_coverage`），TS 全仓零命中
3. 文件 mtime = 6 月 9-10 日（晚于 TS 正常跑的 6 月 6 日），且 `pre_recon_deliverable.md` 仅 17.7KB 不完整、大量 `*_v2.md`/`*_final.md` 是 27-97 字节 stub —— PY 跑崩后的残骸

**注意**：这个混入目录的 `parameter_graph.json` 即便是 PY 产的，`taint_flows` 也是**空**——从另一个角度印证 PR-01/PR-02 断链。本文对比基线**只采用** TS 的 `/deliverables/` 子目录（真 TS 产出）与 PY 的 `1782059117521`（真 PY 产出）。

### 2.5 对比裁决

- **L1 情报产出（deliverable md）**：两版质量都达到实用水平（逐 file:line 点名、端点表/输入向量/角色/注入源全覆盖）。PY 字节密度略高（pre_recon 多 13.3KB、recon 多 11.4KB），多抓到若干 TS 漏报项（RSA 私钥 git tracked、secret dump、session regenerate 不对称、username enumeration oracle）。但**这些增量来自 PY 的 6 个并行 LLM Task agent，不来自确定性层**（见 §5）。
- **L2 确定性层**：TS 无；PY 有但在 NodeGoat 实战全链降级（§3）。
- **不对称**：**TS 有端到端实测，PY 没有**（§2.3）。不能据此说"PY 情报更弱"——PY 情报文件质量已验证——但能说"**PY 的情报→检出因果链未被端到端实测**"。

---

## 3. 确定性静态层的真实有效性（vs 设计承诺）★

这是重构相对 TS 的**头号增量能力**（06-18 §8 列为 PY 优势第 1 项）。本节用 NodeGoat 实战产出核验其**真实有效性 vs 设计承诺**——结论是：机制存在且 parser 健全，但**语言探测这一前置步骤就错了**，导致全链降级。

### 3.1 tree-sitter parser：部分有效

24 个 block 真实解析了 NodeGoat 的全部 handler 与 DAO，`source_code` 字段含完整函数体。抽样确认：
- `allocations-dao.js:searchCriteria:60`（行 60-84）—— 正确捕获含 `$where` 注入的闭包，注释「Fix for A1 - 2 NoSQL Injection」完整保留
- `contributions.js:ContributionsHandler:7` —— 正确识别构造函数 handler
- `user-dao.js:comparePassword:60` / `validateUserDoc:70` —— 正确拆出明文比较子函数

**即 parser 本身健全**。问题不在解析，在更前置的语言识别。

### 3.2 语言误判：全链降级的头号放大器（RE-1 核心）

`code_index.json` 顶字段 `language: "typescript"` —— **NodeGoat 实为 CommonJS JavaScript**。根因：`detect_language`（`code_index/parser.py:40` `most_common(1)`）被 `test/e2e/tsconfig.json` 等配置文件误导，把仓库主语言判成 TypeScript。

这一误判是整个确定性层失效的**头号放大器**：
- TypeScript parser 能解析 JS 语法（故 24 blocks 解析成功），但 **entry-point 识别规则、call graph 跨 `new`/闭包传 `db` 的调用边解析、framework 探测**都按 TS 模式跑，对 NodeGoat 的构造函数 + `app.get("/x", handler.method)` 注册模式**全部失效**。
- 结果：`entry_points: []`（0 入口点）→ `route_chains.json: []`（无原料构链）→ `audit_plan.json: total_chains=0`（tiered audit 无输入）→ `framework_analysis.json: detected_framework=null` → `frontend_mapping.json: routes=[]`。**5 个结构化产出连锁空壳**。

> 这是 06-18 PR-18（"多语言只索引主语言"）的**实战放大版**：PR-18 只说"多语言仓只索引主语言"，实测显示**单语言仓也会因配置文件干扰误判主语言**，且误判后果是整条确定性链路降级。

### 3.3 call graph：完全失效

`code_index_summary.md` 自报：24/24 函数 "Unreachable"、Call Edges 0、Max Chain Depth 0；`code_index.json` 的 `edges/chains/entry_points` 三字段全空数组；`degradation_level: "full"`。根因如 §3.2——tree-sitter AST 无法把 `new Handler(db)` + 方法引用识别为入口点和调用边。

### 3.4 sink detector：确定性 3/12 有效，LLM 58 条噪声

`code_index.json` 的 `sink_call_sites` 共 61 条，分两部分：

- **确定性 `ts-eval` 规则（3 条）—— 真实有效**：全部命中 `contributions.js:32/33/34` 的 `eval(req.body.preTax/afterTax/roth)`，`dangerous_slots` 正确标注 `arg_index:0, expression:"req.body.*", is_entry_hint:true`。这是高质量、可直接复用的 sink 证据。
- **LLM `llm-sink-hunter`（58 条）—— 噪声为主**：全部 `needs_review: true`、`dangerous_slots: []`、`callee_name: ""`、`column: 0`（无参数级定位）。分类明显错乱：
  - `config/env/all.js:8`（公开 session secret）标 `ssrf` —— 应是硬编码 secret
  - `_id:1`（file_path 居然是 `"_id"`）标 `ssrf` —— deliverable 文本里的 Mongo `_id` 字段被误当文件路径
  - `Dockerfile:1` 标 `xss`、`package.json:14` 标 `file` —— 依赖清单/容器描述当 sink
  - `server.js:82/84/85/89/94/96/98`（全是注释掉的 cookie 配置行）标 `xss` —— 注释行非真实 sink

**确定性 sink 覆盖率**：NodeGoat 有约 12 类真实 sink（eval×3、`$where` NoSQL 注入、needle SSRF、`res.redirect` 开放重定向、marked 存储型 XSS、`findOne` NoSQL operator、ReDoS、ESAPI 错配、autoescape off、明文口令比较、公开 secret）。确定性 `ts-eval` 规则**只覆盖 eval 这 1 类（3 条）≈ 25%**，漏掉其余 11 类。`llm-sink-hunter` 的 58 条像是 LLM 把 deliverable 文本里的 file:line 全量回扫贴标签，**信息密度低、误标率高**，下游读了仍要全量复核。

### 3.5 污点流：断链（实战印证 06-18 PR-01/PR-02）

`static_dataflow_hints.md` 的"污点流（entry→sink）"section = 一行「本仓库无可达 sink 的污点流」= **完全空**；`parameter_graph.json` **不产出**（文件不存在）。这是 06-18 PR-01（ParameterPropagationGraph 孤儿）+ PR-02（LLM taint 客户端 `return "{}"` 桩）两条 P0 断链的直接产物。

> **机制层 vs 效果层的严重度分离**：PR-01/PR-02 在 06-18 是 **P0（机制断链）**。但在效果层，污点流空**没有导致下游失能**——因为 LLM deliverable（§5）逐 file:line 画出了 12 条 source→sink 流（recon_deliverable.md §9），下游 vuln agent 读 deliverable 即可拿到等价情报。故污点流断链的**效果影响降为"中"**（RE-3）：损失的是"确定性、可复现、结构化的污点证据"，而非"污点情报本身"。

### 3.6 确定性层价值结论

NodeGoat 实战中，重构确定性层的**真实有效产出 = 3 条 eval sink + 24 个解析正确的 function block（含 source_code 片段）**。call graph、入口点、污点流三条核心能力全失效或断链。这与 06-18 §8 第 1 点"sink_danger 维是唯一真实生效的风险维度"**完全印证**。

**关键判断**：不能笼统说"PY 的确定性层让 pre-recon/recon 比 TS 强"。在 NodeGoat 这一硬案例上，确定性层对两边**都没贡献**（TS 没有，PY 有但全空/误判）。PY 的情报质量略优是**多 agent 并行 + LLM 提示工程**的功劳。**确定性层的真实价值需要换一个它能 work 的目标才能体现**——例如有 React/Angular 前端（`mapFrontendRoutes` 能抽路由）、或 `finale.resource` 自动 REST 框架（`analyzeFrameworks` 能识别）、或纯 TS/Go/Java/PHP 单语言仓（语言不误判）。NodeGoat（CommonJS JS + 配置文件干扰）恰好踩中了确定性层的全部盲区。

---

## 4. 情报→下游衔接效果

### 4.1 重构下游 vuln prompt 对 pre-recon/recon 产出的消费矩阵

对 `prompts/vuln-{auth,authz,injection,ssrf,xss}.txt` + `*-exploit.txt` + `shared/_*.txt` 全量核验，9 个 pre-recon/recon 产出的下游触达情况：

| pre-recon/recon 产出 | 进下游 vuln/exploit prompt? | 机制 | 证据（file:line） | 净效果 |
|---|---|---|---|---|
| `static_dataflow_hints.md` | ✅ 进 vuln（不进 exploit） | @include 静态拼接 | `shared/_static-dataflow-hints.txt` 被 `vuln-auth.txt:43`/`vuln-injection.txt:45`/`vuln-authz.txt:47`/`vuln-ssrf.txt:43`/`vuln-xss.txt:43` 全部 @include；5 个 `*-exploit.txt` 零 @include | **唯一进 prompt 的确定性产出**（RE-5） |
| `pre_recon_deliverable.md` | ✅ 进 vuln + exploit | prompt 文字指示自读 | vuln: `vuln-auth.txt:52,121`/`vuln-ssrf.txt:52,122,174`/`vuln-xss.txt:52,84,136` 等；exploit 全 5 个 | agent 自读指定 section（XSS/SSRF/Injection sink 清单） |
| `recon_deliverable.md` | ✅ 进 vuln + exploit | prompt 文字指示自读 | 5 个 vuln prompt ~line 40 声明 "primary source of truth"；`shared/_cross-route-enumeration.txt:11` 引用；exploit 全 5 个 | agent 自读端点/角色/越权清单 |
| `code_index.json` / `code_index_summary.md` | ❌ 不进 | 阶段内消费 | 仅 `pre-recon-code.txt:82,114,171,431` 读（pre-recon agent 自读）；pipeline `activities.py:357-358` load | 止于 pre-recon 阶段内 |
| `parameter_graph.json` | ❌ 不进 | 阶段内消费 | 仅 `recon.txt:120` 读（recon agent 自身）；且文件本就不产出 | 止于 recon 阶段内 |
| `entry_points.json` | ❌ 不进 | 孤儿 | prompt 零命中；`pre-recon-code.txt:89,118` 明示 agent "you do NOT need to write entry_points.json" | 纯 pipeline 内部产物 |
| `framework_analysis.json` | ❌ 不进 | 孤儿 | prompt 零命中；仅 `activities.py:545,607,694` 内部读 | 空转 |
| `frontend_mapping.json` | ❌ 不进 | 孤儿 | prompt 零命中；仅 `activities.py:575,625,704` 内部读 | 空转 |
| `route_chains.json` / `attack_chains.json` | ❌ 不进 | 孤儿 | prompt 零命中；`activities.py:647,715` 内部生成 | 空转（且 route_chains 实测 `[]`） |

**结论**：9 个产出中**只有 3 个**（`static_dataflow_hints.md` + 两份 deliverable）真正触达下游 vuln/exploit agent；其余 6 类 JSON 全部止步于 pipeline 内部，**永远不进任何下游 prompt**。

### 4.2 PromptManager 不支持动态知识注入（RE-4 根因）

`packages/core/src/shannon_core/prompts/manager.py`：
- `@include`（`_process_includes` L53-72）：纯正静态文件原样拼接，正则匹配 → `read_text()` → 原文回填，**不做任何 `{{...}}` 插值**，带路径穿越保护。
- `_interpolate`（L74-157）支持的占位符**全部是静态 config 变量**：`{{WEB_URL}}`/`{{REPO_PATH}}`/`{{DELIVERABLES_PATH}}`/`{{SCRATCHPAD_PATH}}`/`{{PLAYWRIGHT_SESSION}}`/`{{AUTH_CONTEXT}}`/`{{RULES_AVOID}}`/`{{LOGIN_INSTRUCTIONS}}` 等。调用方 `executor.py:55-64` 只传 4 个 path/browser 字段。
- **不存在** `{{SHARED_KNOWLEDGE}}`/`{{ROUTE_CHAINS}}`/`{{STATIC_HINTS}}`/`{{ENTRY_POINTS}}`/`{{FRAMEWORK_ANALYSIS}}` 等动态知识占位符。

**含义**：即便把 `route_chains.json`/`entry_points.json` 等孤儿产出接通，**PromptManager 也无法把它们注入 prompt**——`@include` 只拼静态文件、`_interpolate` 只填静态 config。这是 06-18 PR-12 指出的能力缺口，也是 RE-4 的根因：6 类 JSON 孤儿不只"下游不读"，是"**下游想读也没有注入机制**"。

> 补充：`recon.txt:127` 的 `{{TAINT_FLOW_SUMMARY}}` 是 recon agent 自身 prompt 的装填，由 `audit_input_builder.py:59,117,122` 在生成 hints 文本时渲染，**不是** PromptManager 的通用注入能力，也不触达 vuln/exploit。

### 4.3 TS shared-knowledge.json：比 06-18 §7 记录更彻底的半死代码

TS 下游 vuln prompt 拿情报的方式**与重构完全相同**——纯文字指示 agent 自读 `.shannon/deliverables/recon_deliverable.md` + `pre_recon_deliverable.md`（`vuln-auth.txt:48`/`vuln-authz.txt:53`/`vuln-ssrf.txt:49`/`vuln-xss.txt:57`/`vuln-injection.txt:141` 等硬编码路径）。

但 TS 还有一条"结构化情报中转"通道——`shared-knowledge.json`（`audit/knowledge-store.ts:22`），被 pre-recon（`activities.ts:265` 写 frameworkAnalysis）、recon（`:293` 写 frontendRoutes）、attack-chain（`:1042` 写 attackChains）写入。**核验发现这条通道比 06-18 §7 记录的更彻底地死**：
- `{{SHARED_KNOWLEDGE}}` 占位符**全仓只在** `shared/_shared-knowledge.txt:7` **出现一次**
- **没有任何 vuln/exploit prompt `@include(shared/_shared-knowledge.txt)`** —— 全仓 grep 零命中
- `buildSharedKnowledgeContext()`（`prompt-manager.ts:296`）零调用，占位符即便触发也填兜底串 `'No shared knowledge available from prior agents.'`（`:480`）
- `loadSharedKnowledge` 唯一消费者是 `buildAttackChainsActivity` 自己（`activities.ts:1039`，写后自读用于报告拼装）

**即**：TS 的 frameworkAnalysis/frontendRoutes/attackChains 三类结构化情报**写后即弃，下游 vuln 零消费**——等价于重构的 JSON 孤儿问题，但 TS 更彻底（连 `static_dataflow_hints` 这层 sink 注入都没有）。

### 4.4 净衔接效果对比

| 维度 | 重构 PY | 原始 TS |
|---|---|---|
| 下游可见的 pre-recon/recon 情报 | 2 份 deliverable（自读）+ `static_dataflow_hints`（@include，**独有**） | 2 份 deliverable（自读） |
| 结构化 JSON 孤儿 | 6 类止步 pipeline（code_index/parameter_graph/entry_points/framework/frontend/route_chains/attack_chains） | shared-knowledge.json 3 字段写后即弃（更彻底） |
| 动态注入机制 | 无（PromptManager 只静态） | 无（buildSharedKnowledgeContext 零调用） |

**重构对 TS 在衔接链路上的净增益 = `static_dataflow_hints` 的 sink 清单注入**（5 个 vuln prompt @include 落地）。但这个增益在 NodeGoat 上**质量打折**：61 条 sink 里仅 3 条 ts-eval 真实有效，58 条 llm-sink-hunter 是噪声（§3.4）。

---

## 5. LLM 兜底机制（关键反转）★

本节解释一个看似矛盾的现象：**确定性层大面积失效（§3），但下游情报产出效果并未退化（§2.1）**。

### 5.1 LLM deliverable 完全绕过失效的确定性层

重构 `pre_recon_deliverable.md` 的 **Phase 0 — Indexing Coverage** 章节明确自述（核验原文要义）：

> "The deterministic code index was severely degraded … all mapping above was performed by direct source review (six delegated analysis agents), not from the index."

即 **LLM 层完全自知确定性层失效，自行通过 6 个并行 Task agent 完成了全部情报采集**。`pre_recon_deliverable.md`（46KB）和 `recon_deliverable.md`（54KB）逐 file:line 点名的全部 12 类 sink——包括确定性层漏掉的 `$where`/SSRF/重定向/marked XSS/NoSQL operator——**全部由 LLM 源码直读发现**。

### 5.2 兜底的代价与隐性风险

LLM 兜底使确定性层失效**不影响当次情报产出效果**，但带来两个隐性后果：

1. **确定性层 bug 长期"隐形"**（RE-7）：因为 LLM 兜底，确定性层的语言误判、call graph 失效、污点流断链**不会以"情报缺失"的形式暴露**——pre_recon_deliverable 照常厚实。这让 RE-1（语言误判）这类 bug 即便存在也难以从产出质量察觉，长期不修。直到换一个 LLM 兜不住的目标（如超大仓库、LLM token 受限），确定性层失效才会暴露为情报降级。
2. **情报不可复现性**：LLM deliverable 的 sink 定位是概率性的（每次跑可能略有差异），而确定性层的 sink 清单本应是**可复现、可审计、可 diff**的。污点流断链（RE-3）的真正损失是"**确定性、可复现的污点证据**"——下游若想复核某条 source→sink 流，只能信任 LLM 的叙述，无法回溯到结构化数据。

### 5.3 反转结论

在 NodeGoat 这一硬案例上，"确定性层 + LLM"路线**实质退化成了"纯 LLM"路线**（与 TS 等价），但因 PY 用了 6 个并行 LLM Task agent，情报字节密度仍略优于 TS 单 agent 路线（§2.5）。**重构 pre-recon/recon 的情报产出效果不依赖确定性层**——这是好事（鲁棒），也是坏事（确定性层的投入在 NodeGoat 类目标上零回报）。

---

## 6. 两项目共有缺陷（非重构独有）

| # | 缺陷 | 根因 | 影响 | 证据 |
|---|---|---|---|---|
| **C-1** | **结构化情报进不了下游 prompt** | 两版 PromptManager 都只支持静态 @include + 静态 config 插值，无动态知识注入 | framework/frontend/route_chains/attack_chains/shared-knowledge 等结构化产出对下游 vuln 零贡献 | PY `manager.py:74-157`；TS `prompt-manager.ts:296`（零调用） |
| **C-2** | **下游情报全靠 deliverable 自读** | 两版 vuln/exploit prompt 都用硬编码路径指示 agent 读 md | 情报送达依赖 agent 自律读文件，非 prompt 强约束；agent 可能漏读 section | PY `vuln-*.txt:52` 系列；TS `vuln-*.txt:48` 系列 |
| **C-3** | **确定性 sink/线索覆盖率有限** | TS 无确定性层；PY 确定性规则只覆盖 eval（3/12） | 两版都依赖 LLM 发现大部分 sink；确定性层未提供"LLM 漏检兜底" | PY `code_index.json`（仅 ts-eval）；TS 无此层 |

---

## 7. 差距矩阵 + 严重度/紧急度评估

> **严重度** = 对 pre-recon/recon 情报效果（产出质量 / 确定性层有效性 / 衔送达下游 / 实测闭环）的影响；**紧急度** = 结合"PY 无端到端实测"的现状判断。RE- 编号独立于 06-18 的 PR- 编号。

| # | 差距项 | 原始 TS | 重构 PY | 对效果的影响 | 严重度 | 紧急度 | 修复难度 | 建议 |
|---|---|---|---|---|---|---|---|---|
| **RE-1** | **确定性层语言误判致全链降级** | 无此层 | NodeGoat（CommonJS JS）被 `detect_language` 误判为 typescript → 5 JSON 空壳、call graph 全失效 | 确定性层在 JS+配置文件混合仓零产出，PY 头号增量能力失效 | **高** | **高** | 低（`detect_language` 排除配置文件 / 用 `package.json` 主字段） | 修 `parser.py:40` 语言探测，排除 `*.json`/tsconfig 等；单语言仓也应做主语言校验。最高性价比 |
| **RE-2** | **确定性 sink 覆盖率 3/12 + LLM sink 噪声** | 无此层 | 仅 `ts-eval`（eval）3 条有效；58 条 `llm-sink-hunter` 全 needs_review 且误标（package.json/Dockerfile/注释行） | sink 注入通道（RE-5 增益）质量打折；下游读了仍要全量复核 | **中-高** | 中 | 中（扩 sink 规则：`$where`/redirect/SSRF；降噪 llm-sink-hunter） | 扩充确定性 sink 规则覆盖 NoSQL/SSRF/redirect；llm-sink-hunter 加 file 类型白名单 |
| **RE-3** | **污点流断链（实战印证 PR-01/02）** | 无此层 | `parameter_graph.json` 不产出；`static_dataflow_hints` 污点流 section 空 | 损失"确定性、可复现的污点证据"；但 LLM deliverable §9 兜底 12 条流，下游未失能 | **中** | 中 | M（属 06-18 PR-01/02，机制层 P0） | 见 06-18 PR-01/02 修复方向；本文补充：效果层因 LLM 兜底可降为中 |
| **RE-4** | **6 类结构化 JSON 孤儿 + PromptManager 无动态注入** | shared-knowledge.json 写后即弃（更彻底） | code_index/parameter_graph/entry_points/framework/frontend/route_chains 止步 pipeline | 结构化情报对下游 vuln 零贡献；想接通也无注入机制 | **中** | 中 | M（先补 PromptManager 占位符，再接产出） | 先给 PromptManager 加动态知识占位符 + 渲染（06-18 PR-12），再逐个接通 JSON |
| **RE-5** | **重构独有净增益：sink 清单 @include 注入** | 无此通道 | `static_dataflow_hints` 经 `@include` 进 5 个 vuln prompt | PY 唯一确凿的下游可见增益（衔接链路） | **优势项** | — | — | 保留；配合 RE-2 提质后增益放大 |
| **RE-6** | **端到端实测闭环缺口** | NodeGoat 完整跑完（pre-recon→recon→vuln→exploit） | 跑到 recon，**未到 vuln queue**；情报→检出因果链未实测 | PY pre-recon/recon 情报的下游转化能力未验证（同 auth AU-1） | **中-高** | **高** | 低（跑一次完整白盒） | **补一次重构完整白盒扫描**（NodeGoat 或 juice-shop live），确认 pre-recon/recon→vuln→exploit 全链无回归。与 AU-1/INJ-1 同批 |
| **RE-7** | **LLM 兜底掩盖确定性层失效** | 无此层 | 确定性层失效但 deliverable 照常厚实 → bug 隐形 | 确定性层 bug 长期不暴露，直到换 LLM 兜不住的目标 | **中** | 中 | 低（加确定性层产出断言/CI 校验） | 给确定性层加产出质量断言（如 `entry_points` 非空校验、`degradation_level != "full"` 告警）入 CI |
| **C-1** | **结构化情报进不了下游 prompt（共有）** | 共有 | 共有 | 两版 PromptManager 都无动态注入 | **中** | 低 | M | 与 RE-4 合并修（PromptManager 占位符一份 Spec 解决 PY 孤儿 + TS shared-knowledge） |

### 7.1 优先级建议（性价比排序）

1. **RE-1（修语言误判）**：高影响、低难度。一行 `detect_language` 改动可解锁确定性层在 JS 仓库的整条链路。**最高性价比**。
2. **RE-6（补端到端实测）**：高影响、低难度。与 auth AU-1 / injection INJ-1 同批跑一次重构完整白盒，同时验证 pre-recon/recon→vuln 衔接 + auth + injection 三条因果链。
3. **RE-2（扩 sink 规则 + 降噪）**：中-高影响、中难度。配合 RE-1 后，确定性 sink 覆盖率从 25% 提升，RE-5 增益放大。
4. **RE-7（确定性层产出断言）**：中影响、低难度。让确定性层失效可被 CI 发现，避免 LLM 兜底导致的 bug 隐形。
5. **RE-3 / RE-4 / C-1**：中影响、中难度。污点流断链接 06-18 PR-01/02；PromptManager 注入能力是一份 Spec 解决多类孤儿的关键基建。

### 7.2 一句话总结

**两项目 pre-recon/recon 的情报产出方法论（LLM 产出 deliverable + 下游自读）对齐，deliverable 质量都达实用水平（逐 file:line 点名全部 sink）。重构相对 TS 没有情报产出退化——它的独有确定性静态层在 NodeGoat 实战中全链降级（语言误判 RE-1 是头号放大器），但被高质量 LLM deliverable 兜底（§5），情报效果未受影响。** 重构相对 TS 的三个实质问题：**① 确定性层语言误判致全链失效（RE-1，最高优先级）；② 情报→检出因果链未端到端实测（RE-6，与 AU-1/INJ-1 同批补）；③ 结构化 JSON 孤儿 + 无注入机制（RE-4/C-1，需 PromptManager 基建）**。重构唯一确凿的下游可见增益是 `static_dataflow_hints` sink 注入（RE-5），但当前质量打折（3/61 有效，RE-2），修 RE-1+RE-2 后增益才会兑现。**核心判断**：确定性层的真实价值需换它能 work 的目标（纯 TS/Go/Java 仓、或 React/Angular 前端、或 finale REST 框架）才能体现；NodeGoat 恰好踩中其全部盲区，使"确定性层 + LLM"路线在此退化成"纯 LLM"。

---

## 8. 关键证据索引

### 8.1 重构 Shannon-py（代码）

| 证据 | 路径 |
|---|---|
| PromptManager @include 静态拼接 | `packages/core/src/shannon_core/prompts/manager.py:53-72` |
| PromptManager 仅静态 config 插值（无动态注入） | `packages/core/src/shannon_core/prompts/manager.py:74-157` |
| PromptManager 调用方只传 4 个字段 | `packages/core/src/shannon_core/agents/executor.py:55-71` |
| 确定性层语言探测（误判源头） | `packages/core/src/shannon_core/code_index/parser.py:40`（`detect_language` `most_common(1)`） |
| 6 类 JSON 孤儿生成处 | `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:357-715` |
| hints 渲染器 | `packages/core/src/shannon_core/code_index/audit_input_builder.py:59,117,122` |
| vuln prompt @include hints（5 处） | `prompts/vuln-{auth,injection,authz,ssrf,xss}.txt:43/45/47/43/43` |
| vuln/exploit prompt 指示读 deliverable | `prompts/vuln-auth.txt:52,121` 等；`prompts/*-exploit.txt:119-134` 等 |
| pre-recon prompt 消费 code_index | `prompts/pre-recon-code.txt:82,114,171,431` |
| recon prompt 读 parameter_graph | `prompts/recon.txt:120` |

### 8.2 重构 Shannon-py（真实产出，`NodeGoat_shannon-1782059117521/deliverables/`）

| 证据 | 内容 |
|---|---|
| `pre_recon_deliverable.md`（46.9KB） | 11 section + Phase 0 降级自述；逐 file:line 点名 12 类 sink |
| `recon_deliverable.md`（54.5KB） | 10 section；§4 端点表 22 行；§9 12 条 source→sink 流 |
| `code_index.json`（77.5KB） | 24 blocks / 0 entry_points / 0 chains / 0 edges / `degradation_level:"full"` / 61 sink_call_sites（3 ts-eval + 58 llm-sink-hunter） |
| `code_index_summary.md`（527B） | `language: typescript`（误判）；24/24 Unreachable |
| `static_dataflow_hints.md`（9.2KB） | 61 sink；"污点流（entry→sink）"= 空 |
| `entry_points.json`/`framework_analysis.json`/`frontend_mapping.json`/`route_chains.json`/`audit_plan.json` | 124B/85B/38B/2B/133B，全空壳（合计 382B） |

### 8.3 原始 Shannon（代码）

| 证据 | 路径 |
|---|---|
| TS 无 tree-sitter/code_index/parameter_graph（零命中） | `apps/worker/src`、`apps/cli/src` 全仓 grep 零命中（仅 docs 提及） |
| TS pre-recon = 纯 LLM 单 agent | `apps/worker/src/temporal/activities.ts:257-283`（`runPreReconAgent`） |
| TS recon = LLM + regex 前端映射 | `apps/worker/src/temporal/activities.ts:285-309`（`runReconAgent`） |
| TS regex 框架探测侧栏 | `apps/worker/src/services/framework-analyzer.ts:39`（`analyzeFrameworks`，finale/REST substring） |
| TS regex 前端路由侧栏 | `apps/worker/src/services/frontend-mapper.ts:60`（`mapFrontendRoutes`，SPA 路由 regex） |
| TS 白盒强制 recon-static | `apps/worker/src/temporal/workflows.ts:753`；`apps/worker/src/local/runner.ts:189` |
| TS shared-knowledge store（半死） | `apps/worker/src/audit/knowledge-store.ts:22`；写入 `temporal/activities.ts:265,293,1042` |
| TS `buildSharedKnowledgeContext` 零调用 | `apps/worker/src/services/prompt-manager.ts:296,473-480` |
| TS `{{SHARED_KNOWLEDGE}}` 占位符孤立 | `apps/worker/prompts/shared/_shared-knowledge.txt:7`（无 @include 引用者） |
| TS vuln prompt 读 deliverable（硬编码路径） | `apps/worker/prompts/vuln-{auth,authz,ssrf,xss,injection}.txt:48/53/49/57/141` 等 |

### 8.4 原始 Shannon（真实产出，`NodeGoat_whitebox-1780678757791/deliverables/`）

| 证据 | 内容 |
|---|---|
| `pre_recon_deliverable.md`（33.6KB / 467 行） | 10 section；§5 攻击面表 30 条端点 |
| `recon_deliverable.md`（43.1KB / 538 行） | 10 section；§4 端点 31 条；§9 11 类注入源 |
| 确定性层产物 | 不存在（TS 无此层） |

### 8.5 混入目录（PY 残骸，**非对比基线**）

| 证据 | 路径 |
|---|---|
| `NodeGoat_whitebox-1780678757791-deliverables/`（连字符） | 确认为 PY 产出混入 TS 仓：`code_index.json` 含 PY schema（`repository:"/Users/mango/project/vuln-range/NodeGoat"`、`total_blocks`/`decorators`）、`parameter_graph.json`=`{"taint_flows":[]}`（PY 字段名）、mtime 6 月 9-10 日、`*_v2.md` stub |

### 8.6 交叉参考

- [`2026-06-18-prerecon-recon-gap-analysis.md`](2026-06-18-prerecon-recon-gap-analysis.md) — 机制/接线视角断链诊断（PR-01~PR-21），本文 RE- 编号与之独立互补
- [`2026-06-21-auth-effect-gap-analysis.md`](2026-06-21-auth-effect-gap-analysis.md) AU-1 — 端到端实测缺口（RE-6 同构，建议同批补实测）
- [`2026-06-22-injection-effect-gap-analysis.md`](2026-06-22-injection-effect-gap-analysis.md) INJ-1 — 同类实测缺口
- [`sink-gap-analysis-v2.md`](sink-gap-analysis-v2.md) §2 — sink detector prompt 文本对比
- [`2026-06-21-vuln-agent-gap-analysis.md`](2026-06-21-vuln-agent-gap-analysis.md) — vuln agent 结构对齐（pre-recon/recon 情报的下游消费者）
- [`route-analysis-binding-gap-analysis.md`](route-analysis-binding-gap-analysis.md) — 攻击链 confidence / 路由绑定

---

*本报告基于 2026-06-22 两代码库（`shannon` TS @ `feat/fork`、`shannon-py` @ `feat/fork-py`）pre-recon/recon 阶段的真实产出核验 + 代码核验生成，由 3 个并行核验 agent（产出质量 / TS 确定性层 / 下游衔接）+ 主控复核（语言误判头号放大器、LLM 兜底反转、混入目录定性）共同产出。建议后续每次大改动或换靶场后按本结构做「v2 复核更新」——尤其换一个确定性层能 work 的目标（纯 TS/Go/Java 仓）来验证 RE-5 增益的兑现。*
