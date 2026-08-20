# 漏洞数据流视图（dataflow view）设计

- 日期：2026-08-20
- 状态：已定稿（与用户逐节确认：双轨覆盖 / auth-authz 降级 / 全局 tab 入口 / 节点代码预览 / 方案 B 写时组装 / 双轨×双引擎横切不变量）
- 关联：`docs/architecture.md`（双轨/双引擎）、`docs/superpowers/specs/2026-08-19-vuln-queue-delivery-hardening*`（在途交付通道，本设计 append-only 不冲突）

---

## 1. 问题与目标

白盒扫描的研判者目前只能看到漏洞 finding 的扁平文本（`path` = `"POST /api/x → controller → fn → sink"`），看不到：

1. source→sink 之间**经过的每个节点**（函数、file:line、变换）；
2. **哪个节点有防护**（sanitizer 是什么、在哪、是否有效）；
3. **枝条级研判**——同一 sink 的多条路径中，哪根形成了漏洞、哪根被防护挡住及原因。

而结构化数据大部分已存在：`parameter_graph.json` 的 `taint_flows[].propagation_steps`（含 `code_location`、`transformation="sanitize_hint:<name>"`）、`code_index.json` 的 `FuncBlock.source_code` / `SinkCallSite` / `SourcePoint`。缺的是落盘（safe 判定、确定性 sanitizer 标注、LLM 结构化 steps、safe_vectors json）与读侧组装展示。

### 目标

- 扫描详情页新 tab「数据流」：每个 sink 一棵树，双轨枝条共存，枝条带 verdict（vulnerable / safe-被挡及原因），节点带防护标注与代码预览。
- 全景：safe-only 的 sink（所有枝条被防护挡住、未形成漏洞）也展示——「扫过且安全」是研判叙事的一半。
- auth/authz（missing-control 类，无 taint 流）以降级形态进本页：防护位链（ok / missing / ineffective）。

### 非目标

- 不引入前端可视化库（reactflow/d3 等）——分层枝条列表自研。
- 不改双轨判定/召回逻辑本身——本设计只加「落盘 + 组装 + 展示」。
- 不重建确定性→LLM 轨 hints 桥（铁律不动：LLM 轨 prompt 仍不引确定性产物）。

---

## 2. 横切不变量：双轨 × 双引擎

所有改动在每一段都要显式回答「两轨各怎么走、两引擎各怎么走」：

| 段 | LLM 轨 | GitNexus 轨 | 双引擎 |
|---|---|---|---|
| agent 产出 | `submit_finding` 加 `dataflow_steps`（仅 inj/xss/ssrf；auth/authz 无 taint 流不加） | n/a | collector `SectionSchema` 单点定义，`bridge.py` 一份 schema 出两套工具（openai `FunctionTool` + claude `SdkMcpTool`），零引擎分支 |
| 防护/安全证据 | `set_safe_vectors`（结构化） | `SanitizerAnnotation`（补落盘） | bridge 原样透传 dict，引擎无关 |
| 判定 | n/a | `chain_verdict` 经 `run_claude_prompt` 统一抽象 | 统一抽象层两引擎同代码；落盘在活动层，引擎无关 |
| 合并 | finding 为 base | evidence_chain 补充 | 引擎无关 |
| 组装 | LLM 枝（steps / 末节点匹配 / 独立树） | GitNexus 枝（flow_id→TaintFlow→FuncBlock） | 纯 Python，引擎无关 |
| Web/前端 | `track` 字段 + LLM-only 树 | GitNexus 树 + safe-only 树 | 纯读产物 |

全局降级语义（测试锁定）：

- `SUPERNOVA_LLM_TRACK_ENABLED=0`：LLM 枝全无；GitNexus verdicts（含 safe 枝）+ auth/authz（LLM 保留轨）照常 → 视图仍完整。
- 黑盒扫描无白盒桶 → tab 空态；组合扫描读 whitebox 桶。

---

## 3. 数据契约：`dataflow_view.json`

落盘 `deliverables/whitebox/intermediate/dataflow_view.json`，由 core 组装器产出（唯一产出物），web 只消费这一个 schema。

```jsonc
{
  "schema_version": 1,
  "summary": { "total_sinks": 12, "vulnerable_sinks": 5, "safe_only_sinks": 7 },

  "trees": [                          // taint 类（injection/xss/ssrf），每 sink 一棵
    {
      "tree_id": "<sink_call_site_id 或 LLM 合成 id>",
      "vuln_class": "injection",
      "sink": {
        "label": "cursor.execute", "file": "app/db.py", "line": 42,
        "rule_id": "py-sql-execute-raw", "category": "sql",
        "code": "cursor.execute(query)"
      },
      "findings": [                   // 挂此 sink 的漏洞 finding；safe-only 树为 []
        { "id": "INJ-VULN-01", "merge_source": "both", "title": "...",
          "confidence": "high", "witness_payload": "...", "mismatch_reason": "..." }
      ],
      "branches": [                   // 枝条 = 一条 source→sink 完整路径
        {
          "branch_id": "<flow_id 或 LLM finding ID>",
          "track": "gitnexus",        // gitnexus | llm
          "verdict": "safe",          // vulnerable | safe | unknown（unknown=无判定记录：verdicts 产物缺该 flow 或判定被跳过）
          "verdict_reason": "shlex.quote 覆盖拼接值",
          "source": { "label": "req.query.name", "type": "query",
                      "entry": "GET /api/users", "file": "...", "line": 10 },
          "nodes": [                  // 按传播顺序的中间节点
            { "func": "UserController.list", "file": "...", "line": 25,
              "transformation": "concat",          // concat/encode/format/null
              "intermediate_vars": ["q"],
              "code": "q = 'SELECT...' + name",   // ≤10 行；LLM 枝可能 null
              "has_code": true }
          ],
          "sanitizers": [             // 本枝防护标注（哪个节点有防护）
            { "name": "shlex.quote", "defense_type": "shlex_quote",
              "file": "...", "line": 30, "effective": true }
          ]
        }
      ]
    }
  ],

  "control_findings": [               // auth/authz 降级形态（防护位链，非树）
    { "id": "AUTHZ-VULN-01", "vuln_class": "authz", "endpoint": "PUT /api/orders/:id",
      "chain": [ { "label": "owner 检查", "status": "missing",   // ok|missing|ineffective
                   "detail": "guard_evidence 原文", "file": "...", "line": null } ] }
  ],

  "safe_vectors": [                   // 未能匹配到 sink 树的 LLM 安全向量
    { "subject": "...", "location": "file:line", "defense_mechanism": "...", "render_context": "..." }
  ]
}
```

### 聚合规则（组装器核心逻辑）

1. **树的粒度 = sink**。GitNexus 枝按 `sink_call_site_id` 精确聚合；verdicts 产物中 safe 枝同样进树（safe-only 树 `findings: []`）。
2. **LLM finding 挂树**：取其 `dataflow_steps` 末节点为 sink 位，按 `(vuln_class, sink file:line 规范化)` 与 GitNexus sink 对齐（复用 merger 的 location 规范化思路）；对不上则自立 `track=llm` 树（sink 只有位置无 rule_id）。LLM safe 枝来自 `set_safe_vectors`（有 defense_mechanism + location、无完整路径）：匹配到 sink 树 → 挂为单节点 safe 枝；匹配不上 → 顶层 `safe_vectors` 区。
3. **代码片段**：节点 `code` 从 `code_index.json` 的 `FuncBlock.source_code` 按 `code_location` 截 ±5 行（≤10 行/节点）。**体积控制**：只给有故事的节点存 code（source / sink / transformation 非空 / sanitizer 所在步），纯透传步只存位置（`has_code:false`）。LLM 枝节点无源码 → `has_code:false`。
4. **二阶链**（`2ND-GN-*`）：挂 read-side sink 树，`source.type="storage"`（write 侧 file:line 并入 source.label）。

---

## 4. 管线改动（P1–P6）

| # | 改动 | 轨/引擎 | 内容 |
|---|------|---------|------|
| P1 | verdicts 落盘 | GitNexus / 引擎无关 | `run_gitnexus_chain_verdict` 收集**每条**候选链判定 `{flow_id, verdict, reason, sanitizer_annotations, confidence}` → `intermediate/{vc}_chain_verdicts.json`（**safe 链也进**——安全枝条数据源；sanitizer 精确标注随之落盘，不再用完即丢）。`INTERMEDIATE_FILE_PATTERNS`（`models/deliverables.py`）加 `{vc}_chain_verdicts.json` 与 `dataflow_view.json` pattern，防 tiering 错位复发 |
| P2 | LLM `dataflow_steps` | LLM / 双引擎共用 | 三处：① collector `vuln.py` 的 `_INJ_XSS_FINDING_PROPS` + `_SSRF_FINDING_PROPS` 加扁平数组字段（元素 `{label:str, file:str, line:int\|null, protection:str\|null}`，全 optional，无深嵌套——压缩 GLM 结构化输出失败面）；② `prompts/vuln-{injection,xss,ssrf}.txt` 的 `finding_submission` 段加提交说明（按传播顺序列节点、防护节点标 protection；auth/authz prompt **不动**）；③ `queue_schemas.py` 的 `InjectionVulnerability`/`XssVulnerability`/`SsrfVulnerability` 加 `dataflow_steps: list[dict] \| None = None`（必须进模型，否则 merge `model_dump()` 丢字段），`parse_lenient` 加**宽容归一**：非 list→None、元素非 dict→丢弃该元素、字段类型错→忽略——畸形不拒收 finding。openai 侧既有防御不动并测试锁定：`repair_json_arguments` + 顶层 dict 检查（bridge）、`strict_json_schema=False`（litellm grammar 教训） |
| P3 | safe_vectors 落 json | LLM / 引擎无关 | executor 落 queue 时同步把 collector 的 `set_safe_vectors` 数据落 `intermediate/{vc}_safe_vectors.json`（目前只渲染进 md，组装器需结构化源）；pattern 注册并入 P1 |
| P4 | 组装器 + 活动 | 消费双轨 / 引擎无关 | core 新增 `services/dataflow_view.py::assemble_dataflow_view(deliverables_dir) -> dict`（纯函数，输入 5 类产物：SSOT queue、chain_verdicts、parameter_graph、code_index、safe_vectors；`{vc}_llm_queue.json` 为原始 dict 兜底源）。whitebox 管线 `run_merge_dual_track_queues` 之后新增 `run_assemble_dataflow_view` 活动，**失败不阻塞扫描**（warning + 不产物）。降级矩阵见 §6 |
| P5 | Web 端点 | 无关 | `GET /api/workspaces/{ws}/scans/{scan_id}/dataflow`，经 `resolve_intermediate` 读 `dataflow_view.json`（tiering 读侧 fallback 惯例：intermediate/ → 平铺兜底）；缺 → 404 前端空态 |
| P6 | 真机探针 | LLM / 双引擎 | 按惯例各加 claude / openai 探针脚本，验证 GLM 在两引擎下产出含 `dataflow_steps` 的 `submit_finding`——**硬验收项** |

在途工作兼容：P2 是 append-only 字段（roster 对账按 ID 不按字段），与 vuln queue 交付通道 Phase 2（submit_finding 单条 + roster 对账）不冲突。

---

## 5. 前端（ScanDetail 新 tab「数据流」）

> 视觉/交互定稿已与用户逐轮评审确认（mockup v5，白话文案口径同轮定稿）。

### 路由与入口

- `router.tsx` ScanDetail children 加 `dataflow`（lazy import），tab 名「数据流 / Dataflow」（i18n zh/en）。
- `VulnCard` 展开态加「查看数据流」链接 → `.../dataflow?tree={tree_id}`：DeliverablesTab 用 SWR 拉同一 dataflow API 建 `finding_id → tree_id` 映射传给卡片（共享缓存，零额外请求）；定位 = 目录同一锚点滚动 + 卡片描边闪烁。

### 页面骨架：左目录 + 右内容两栏

- 左侧**目录侧栏**（约 232px，sticky 吸顶、自身可滚动 `max-height:100vh`——多漏洞时目录内滚不撑页面）：
  - 分组镜像页面三区：**漏洞数据流树 (N)** / **认证·授权风险 (N)** / **排查过的入口 (N)**；
  - 每棵树一条：状态图标（●红=有打通枝 / ✂绿=全部剪断 / ▲黄=认证授权风险）+ sink 名 + 次行小字（finding IDs · N打通/M剪断）；
  - **scrollspy**（IntersectionObserver）滚动到哪棵树对应条目高亮；点击平滑滚动 + 目标卡 coral 描边闪烁；
  - 窄屏（<1000px）侧栏退化为顶部块。
- 右侧内容区：汇总条 → 图例 → 三区。

### 汇总条与图例

- 汇总条（枝条叙事口径）：`N 条数据流 · N 条打通到危险点 · N 条被防护剪断 · N 个认证/授权风险` + 筛选器（vuln_class 下拉、「只看有漏洞的 ⇄ 全部」toggle）。
- 图例条教读图（打通/剪断/绕过盾/有效盾/两种靶心），位于树区上方。

### 区 1：漏洞数据流树

标题精炼为「漏洞数据流树」，组织方式放说明段（「每个危险点（sink）一棵树：红色流动线=打通（漏洞）；绿色线被 ✂ 剪断=防护拦下（安全）；节点竖向对齐，绿线断得越靠右说明输入走得越远才被拦下」）。每 sink 一卡，卡内 = **SVG 剪枝树**（水平汇聚：source 左列 → 向右汇聚到 sink 靶心）+ 枝条明细列表，两层数据联动。

**剪枝视觉语言（签名元素）**：

| 元素 | 视觉 | 语义 |
|---|---|---|
| 打通枝 | 红色虚线 + 流动动画（stroke-dashoffset） | 链级 verdict=vulnerable：一路无有效防护，污点流到 sink |
| 剪断枝 | 绿色实线至防护节点 + ✂ 剪刀标记 + 渐隐虚线残端，不到 sink | 链级 verdict=safe：防护在此节点把链剪断（剪枝深度即信息） |
| 黄盾节点 | 🛡 黄圈 | 节点级防护存在但被绕过（线继续红） |
| 绿盾节点 | 🛡 绿圈 + ✂ | 节点级防护有效 = 剪断点 |
| sink 靶心 | 红色实线圆环 + 脉动 | 有打通枝到达（=漏洞） |
| sink 靶心（灰） | 灰色虚线圆环 | 无输入到达（safe-only 树） |
| source | 青色 pill | 用户输入入口（参数名 + type + METHOD /route） |
| 公共函数 | 节点下标 `⟳ 公共函数 · N 枝经过` | 同名函数被多枝共用（前端按树内 `nodes[].func` 重名统计，无需 schema 新字段） |
| 同一函数虚线 | ⟳ 青色点线弧连接同名节点 + `同一函数` 小标 | 多枝经过的同一个函数不合并节点，用点线弧提示「这是同一个函数」（hover 说明剪断了哪几条枝） |

**链级 ⇄ 节点级双层语义**（与用户确认）：verdict（打通/剪断）是**链级**判定，挂枝条头（红/绿左缘色条 + `打通 · 一路无有效防护` / `剪断 · 在 X 被拦下`——剪断点函数名直接进标签）；防护（盾）是**节点级**标注，画在图上。

**交互**：
- 节点 hover tooltip（函数 + 位置 + 防护速览，暗色浮层）；节点/明细行点击展开代码片段（`has_code:false` 降级为「LLM 扫描的节点不带源码，agent 原话」）；
- 枝条（SVG path）hover ↔ 明细行双向高亮联动；点枝条展开对应明细；
- 树头徽章：sink 名 + file:line + rule_id/class + finding ID + **打通/剪断迷你比例条**（红绿 minibar）；
- **剪断枝折叠**：剪断枝 >N 条（默认阈值 4）折叠为「+N 条枝被剪断」行，点击展开（viewBox 动态调高）；
- **图区缩放平移**：容器限高（520px）+ wheel 缩放（鼠标锚点）+ 拖拽平移 + 重置/百分比控件——大仓大树不撑开页面；
- 动画克制：红枝流动、有漏洞 sink 脉动、载入 stagger；`prefers-reduced-motion` 全关。

### 区 2：认证 / 授权风险

标题精炼为「认证 / 授权风险」（关卡分析方法在说明段）。auth/authz 无数据流——不画树，逐接口检查防护关卡链：endpoint + 关卡卡序列（🟢 正常 / 🔴 缺失 / 🟡 失效，dashed 红边 + 流动断线指示污点穿过的缺口），detail 引 finding 原文（`guard_evidence`/`missing_defense`/`mismatch_reason`）+ file:line。

### 区 3：排查过的入口

未匹配到树的 `safe_vectors` 平铺（subject + 防护机制 + 位置），区头带说明：这些输入没有流向任何危险调用点（或已被防护拦下），只有起点、没有危险终点，不成树不成漏洞——列出证明扫过、查过。

### 渲染形态决策

- **不引可视化库**（reactflow/d3 都不用）：数据形态是「多条线性链汇聚」而非自由 DAG，自研 SVG（参照 `FileTree` 的组件惯例 + `tokens.css` 语义色 cyan=GitNexus / magenta=LLM / red=漏洞 / green=安全）零新依赖。Reactflow 留作未来真需要交互式大 DAG 时的升级路径。
- 树布局：枝条纵向堆叠、节点水平排布（source 左 → sink 右），尺寸由折叠 + 限高 + 缩放治理。
- **列对齐**：同一传播步骤的节点对齐到同一列（`x = step_index × 列宽`，入口列 / 第 N 步列 / sink 列全树统一），汇入 sink 的边统一贝塞尔曲线——「断在第几列」横向可比，一眼看出输入走了多远才被拦下（白话：断得越靠右，走得越远）。
- **同名函数不合并节点**（明确取舍）：合并成 DAG 节点会砸掉两个语义——verdict 是枝级判定（汇入同一边的枝可能一红一绿，边着色冲突）；同函数在不同枝身份可不同（一处普通传递、另一处是有效防护）。故每枝自包含重复绘制，同名节点间画青色点线弧 + `⟳ 同一函数` 标注（见视觉语言表）。
- **跨树 source 提示**：同一入口流向多个 sink 时会在多棵树各出现一次（按 sink 分组的必然结果），source tooltip 注明「同一入口还流向：XX 树」，避免误读为重复数据。

### 白话文案规范（面向研判用户，与工程产物字段解耦）

展示层文案统一口径（i18n zh/en 双语），工程字段名只出现在代码标识与首次配注：

| 概念 | 展示文案（zh） | 禁用（AI 腔/术语直译） |
|---|---|---|
| vulnerable 枝 | 打通 · 一路无有效防护 | 贯通、污点传播 |
| safe 枝 | 剪断 · 在 X 被拦下 | 被防护截断、净化生效 |
| sink | 危险点（sink）首次配注后可用 sink | 危险汇聚点、污点终点 |
| sink 无枝到达 | 无输入到达 | 未被触及 |
| sanitizer | 节点防护 · 有效/被绕过 | 净化器、脱敏 |
| auth/authz 区 | 认证 / 授权风险（说明段解释关卡分析方法） | 防护缺口（missing control 直译） |
| safe_vectors 区 | 排查过的入口（说明段解释「有起点、无危险终点」） | 安全向量 |
| LLM finding 无码 | LLM 扫描的节点不带源码，agent 原话 | LLM 轨无源码预览 |
| 存储中转 | 经过存储中转：先存进数据库，读出来才发起请求 | 二阶链、storage taint |

---

## 6. 错误处理（全流程降级矩阵）

| 层 | 场景 | 行为 |
|---|---|---|
| 组装器 | parameter_graph 缺 | GitNexus 枝保留、无中间节点（verdict 摘要仍在） |
| 组装器 | code_index 缺 / 纯透传节点 | 节点 `has_code:false` |
| 组装器 | LLM finding 无 steps | 枝条降级为 source→sink 直连 |
| 组装器 | 全部产物缺 | 不产文件 |
| 组装活动 | 任何异常 | warning + 不产文件，**不阻塞扫描** |
| Web API | 产物缺（旧扫描） | 404 → tab 空态「该扫描无数据流视图（需新版扫描）」 |
| 前端 | API 5xx / 数据残缺 | SWR 错误态 + 重试；空枝/无码节点按降级渲染 |
| 全局 | `SUPERNOVA_LLM_TRACK_ENABLED=0` | LLM 枝全无，GitNexus safe 枝 + auth/authz 照常（测试锁定） |

---

## 7. 测试矩阵（双轨×双引擎贯穿）

| 层 | 覆盖 |
|---|---|
| core pytest | P1 verdicts 落盘（safe 链进产物 + pattern 注册）；P2 pydantic 三模型 `dataflow_steps` 经 merge `model_dump()` 保留 + `parse_lenient` 宽容归一（非 list / 元素非 dict / 字段类型错）+ **bridge 双引擎 schema 一致**（`FunctionTool.params_json_schema` 与 `SdkMcpTool.input_schema` 同含 `dataflow_steps`）+ openai 既有防御锁定（repair / 顶层 dict / strict=False）；P4 组装器 fixture 矩阵（双轨全量 / 单轨缺位 / safe-only 树 / both / LLM 自立树 / 二阶链 / control_findings / safe_vectors 匹配 / code 体积控制） |
| whitebox pytest | P3 safe_vectors 落盘；P4 活动接线（merge 后触发、失败不阻塞） |
| web pytest | 端点 200 / 404 / tier fallback |
| 前端 vitest+msw | 剪枝树渲染（打通流动线/剪断残端/黄绿盾/两种靶心）、列对齐与断点列位、同一函数点线弧（不合并节点）、跨树 source tooltip、链级⇄节点级标签文案、目录 scrollspy 与点击定位、剪断枝折叠、图区缩放平移、节点 tooltip、代码展开与 LLM 无码降级、认证/授权关卡链、排查过的入口区、筛选、空态、VulnCard 跳转、白话文案 zh/en 快照 |
| 真机探针 | claude / openai 两引擎各一，验证 GLM 产出含 `dataflow_steps` 的提交（硬验收） |

测试陷阱遵守 CLAUDE.md：只跑改动相关测试文件，勿广跑全套。

---

## 8. 涉及文件清单（实施索引）

- core：`code_index/chain_verdict.py`（收集点）、`whitebox pipeline activities.py`（P1 落盘 + P4 活动）、`collectors/vuln.py`（P2 schema）、`prompts/vuln-{injection,xss,ssrf}.txt`（P2 说明）、`models/queue_schemas.py`（P2 模型+归一）、`agents/executor.py`（P3）、`services/dataflow_view.py`（新增）、`models/deliverables.py`（pattern）
- web：`api/scans.py`（P5 端点）、`components/deliverables_reader.py`（如需 kind 分类）
- 前端：`router.tsx`、`routes/WorkspaceDetail/DataFlowTab.tsx`（新增，页面骨架+两栏布局）+ `components/dataflow/*`（新增：TocSideBar 目录+scrollspy、PruningTreeFig SVG 剪枝树+缩放平移+折叠、BranchRow 枝条明细+代码展开、GuardChain 关卡链、SafeEntries 排查过的入口）、`VulnCard.tsx`（跳转链接）、`api/client.ts`/`api/types.ts`、locales（白话文案 zh/en）
- 探针：`scripts/validate_claude_dataflow_probe.py`、`scripts/validate_openai_dataflow_probe.py`（新增）
