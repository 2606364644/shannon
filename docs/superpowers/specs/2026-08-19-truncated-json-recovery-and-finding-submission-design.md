# vuln findings 交付通道加固：截断修复 + 失败诊断 + submit_finding 单条上交 + roster 对账

> 日期：2026-08-19　分支：`feat/pre-scan-local-agent`　类型：**bug 修复 + 可靠性重构**（provider 解析层 + vuln agent 交付通道，白盒 vuln 5 类通用，双引擎对称）
>
> 触发故障：NodeGoat-20260819-024451 白盒扫描 auth-vuln 三次 attempt 全部 `Missing exploitation queue`（另一环境，代码同源）。实施分两期：**Phase 1 = 截断修复 + 失败诊断**（止血，先行合入，独立生效），**Phase 2 = submit_finding 单条上交 + roster 对账 + 定向重查**（治本，vuln queue 彻底脱离 structured_output 单点通道）。一个 spec、两份 implementation plan。

---

## 1. 背景与根因

### 1.1 故障链（已逐锚点验证）

profile `deepseek-ft-anthropic`（llm-proxy → dashscope anthropic 兼容层，模型 deepseek-v4-flash）在流式传输 agent **最终消息**（12 个 findings 的长 JSON，~11.5k 字符）时中途断流：三次截断点 11533 / 11072 / 11422 字符（高度一致），最后一条 `llm_response` 到 `agent_end` 挂起 39.4s / 120.0s / 47.2s（正常对照 15ms、JSON 完整闭合）。定性为当天该网关链路持续异常，非 supernova 代码 bug；但 supernova 缺乏截断容忍，把「丢半个 finding」放大成「整轨报废 + 三轮全量重跑（每次 6-13 分钟）」。

### 1.2 现状代码锚点（本 repo 已核对）

| # | 锚点 | 现状 |
|---|---|---|
| A1 | `agents/llm_json.py:68-81` | `_extract_json_payload` 第 3 步回退在截断时返回「首 `{` 到末 `}`」的**非法半截子串**（`obj_sub`） |
| A2 | `agents/providers_anthropic.py:443-457` | collected_text 兜底分支 `json.loads(payload)` 必败 → `structured_output = None`。CLI `--json-schema` 是第一道解析，collected_text 是第二道——**双通道同生共死**（都假设完整 JSON） |
| A3 | `agents/openai_result_mapper.py:80-85` | openai 引擎**对称死法**：同样 `_extract_json_payload` → `json.loads` 失败 → `None`（CLAUDE.md 双引擎一致铁律，两侧都要修） |
| A4 | `agents/executor.py:198-206` | `structured_output is not None` 才写 queue，None 时**静默跳过、零日志** |
| A5 | `agents/validators.py:41-47` | 防线 raise `OUTPUT_VALIDATION_FAILED`（文案即日志所见）→ retryable → Temporal 重跑。防线本身正常（把静默漏盘变成可重试错误） |
| A6 | `agents/message_dispatcher.py:107` | `stop_reason` 已提取并挂到 result（`providers_anthropic.py:407`），但 executor 失败 raise 的 `_result_cost_context` 只带 cost/tokens/model/turns——**stop_reason 与文本证据全丢**；且本故障实际走 A5 的 raise，其 context 连 cost 都没有 |
| A7 | `prompts/vuln-auth.txt:252`（5 个 vuln prompt 同构） | queue 数据本体「captured automatically from your final structured output at session end」——**全部 findings 押在一条消息的流式传输完整性上**（单点，继承 TS 设计） |

### 1.3 现状交付模式（A7 展开）

vuln agent 的两类产物走不同通道：**deliverable md** 经 4 个 `set_*` 工具（session 末尾每工具一次性调用、传全量数组，host 渲染 md）——只交**摘要**（key_outcome / patterns / safe vectors / blind spots）；**queue 数据本体**（vulnerable findings）则攒到 session 结束的最后一条 structured output 一次性输出。即：findings 的完整数据只有「末条消息」这一条交付路径（单点）。

### 1.4 关键事实：两种交付形态的失败语义不同

collector 工具（`set_*` / 本设计的 `submit_finding`）是 **host 进程内工具**：模型的输出流携带调用参数（唯一经过网关的路段，几百字符小流）→ CLI/SDK 在 host 进程内解析并本地执行（append 进 collector 内存列表）→ 结果回传。因此：

- **单个工具调用断流** → CLI 判定**整轮失败**（自动重试该轮或 query 报错走 executor 失败路径）——**不存在「网关吞掉第 7 个提交、agent 继续跑、host 一无所知」的静默丢失**；
- **末条大 JSON 断流**（故障实际形态）→ CLI 认为 session 正常结束，带半截文本回来——**静默丢半个**。

单个上交的真实丢失源只有一个：**模型行为遗漏**（研判了但忘了发起调用，内容只存在于模型上下文、从未到达 host）。Phase 2 的 roster 对账（§3.4）即为此而设。

另一个有利事实：queue 的全部下游消费方走 `VulnerabilityQueue.parse_lenient`（`models/queue_schemas.py:115-200`）——已是宽容解析（逐条校验、坏条目丢弃）。半成品 queue（Phase 1 修复产出）天然兼容；Phase 2 的 queue 由 collector 确定性写盘，天然合法 JSON。**消费方零改动。**

## 2. 目标 / 非目标

### 目标

- **G1（截断修复）**：流中断在消息尾部时，从半截 JSON 救回 N-1 条完整 findings，queue 落盘、validator 自然通过——**双引擎一次修好**（共享层落点）。保护对象是**全部** structured_output 消费者（vuln queue / precheck / poc_generator / validate_authentication 等），不只 vuln。
- **G2（失败诊断）**：防线报错时 PentestError context 携带 stop_reason / 文本长度与末尾片段 / 通道状态（structured_output 是否 None、collector submitted/roster 计数），静默跳过处补 warning 日志。
- **G3（submit_finding 单条上交）**：vuln agent 每确认一个 vulnerable finding **立即单条**经 collector 工具上交，host 确定性累积——findings 交付脱离对单条最终消息的依赖。
- **G4（roster 对账 + 定向重查）**：session 末尾 agent 声明全量 findings 名单（ID+title），host 与 collector 账本确定性对账；漏交的条目经**定向重查小 agent**（拿 `(ID, title)` 线索重新研判）补齐，一轮封顶、降级接受不整跑重试。

### 非目标

- **断点续传**（检测截断后发廉价续写请求）——见 §8（定向重查是其廉价正确变体，已吸收进 G4）。
- **引 `json_repair` 库**——见 §8。
- **为省上下文做分批上交**——动机不成立：CLI 对话历史 append-only，tool_use block 上交后仍常驻上下文，分批 token 反而略贵（11k 提前进 history，后续每轮重复计 input）。G3 的立项理由**只有交付可靠性**。
- **queue json 加 recovery/来源元数据字段**——`parse_lenient` 虽忽略额外字段，但 merge 后 findings 可能带污染；可观测性走日志（§3.1/§3.4）。
- **TS 回移**——TS 走官方 API 无此故障；本设计是 PY 侧第三方网关适应性改造，明示偏离 TS（TS 的 vuln-collector.ts 无 findings 上交工具）。
- **改 `validate_deliverable` 签名**——它是纯函数拿不到 result，改签名波及全部调用方；诊断在 executor 层包装。
- **prompt 喂确定性产物**——本设计只加工具指令与输出格式约束，不引任何确定性层数据（CLAUDE.md §1 双轨铁律；`test_static_dataflow_hints_decoupling.py` 锁定面不受影响）。定向重查的输入是 LLM 自身产物（deliverable md）+ repo 代码，同样合规。

## 3. 核心设计

### 3.1 Phase 1a — `repair_truncated_json`（`llm_json.py` 共享层）

新函数（纯字符串工具，无新依赖，与 `_extract_json_payload` 同居）：

```python
def repair_truncated_json(payload: str) -> str | None:
    """尾部截断的 JSON 补闭合修复；救不回返 None。

    只处理「尾部不完整」一种畸形（流中断的实际形态），不做任意畸形修复。
    loads 当裁判：任何产出必须能 json.loads 通过才返回。
    """
```

算法：

1. `json.loads(payload)` 能过 → 不属于截断问题，直接返 None（调用方已处理）；
2. `json.JSONDecoder().raw_decode` 定位首个解析失败位置；
3. 从失败位置**向前回溯**候选截断点（顶层容器内最后一个完整元素的 `}` / `]` 边界——跳过字符串字面量内的假边界，靠转义感知扫描）；
4. 每个候选点试 `payload[:cut] + 闭合括号串`（按实际嵌套深度补 `"`/`}`/`]`），`json.loads` 通过即返回；
5. 救不回（截断发生在第一个完整元素之前、或顶层结构本身残缺）→ 返 None，走现有 validator 防线（可接受：重试语义不变）。

**接入点两处**（兜底分支内，`json.loads` 失败之后）：

- `providers_anthropic.py` `_extract_result` 的 collected_text 兜底（A2）；
- `openai_result_mapper.py` 的 final 文本兜底（A3）。

修复产出直接作为 `structured_output`，后续链路（executor 写盘 → validator → 下游）零改动。两处接入点各发一条 `logger.warning`：原始长度、截断点、救回条数（`len(vulnerabilities)` 或顶层键数）、engine 标识——排障第一现场是 agents/*.log。

**边界**：围栏内截断（` ```json ` 未闭合或围栏内 JSON 半截）经 `_extract_json_payload` 提取后同样汇入本函数（它拿到的是 payload 子串）；元素**内部**截断（如第 12 条 findings 的 notes 写一半）会连同该残缺元素一起被丢弃（回溯到第 11 条的完整边界）。

### 3.2 Phase 1b — 失败路径诊断（`executor.py`）

- **包装 validate raise**：`executor.py:242-243` 调用 `validate_deliverable` 处包 `try/except PentestError`，re-raise 时 context 增补：`stop_reason`、`collected_text_len`、`collected_text_tail`（末 200 字符，判断截断的直接证据）、`structured_output_present: bool`、`collector_submitted_count` / `collector_roster_count`（Phase 2 后有值，Phase 1 恒 0）、并合并现有 `_result_cost_context` 的 cost/tokens 字段。activities 侧 `except PentestError → end_agent → metrics` 链路已存在（error-path cost 修复先例），context 随之落 events.ndjson。
- **静默跳过补日志**：`executor.py:198-206` 的 `structured_output is None` 跳过分支补 `logger.warning`（agent 名、queue 文件名、text 长度）——现状零日志是排障全靠猜的根源。

### 3.3 Phase 2a — collector 扩展（`collectors/vuln.py`）

**新增 `submit_finding` section**：

- **一次调用只接受一条 finding**（参数是单个 finding object，不是 array；禁止攒批——单次 payload 最小化，去重语义最清晰）；
- per-class schema，与 `set_strategic_intelligence` 同法按 vc branching（5 类字段对齐 `queue_schemas.py` 的 `BaseVulnerability` 子类家族）：`ID` / `vulnerability_type` / `externally_exploitable` / `confidence` / `title` / `notes` + class 特有字段（auth: `source_endpoint`、`missing_defense` 等）；
- required 对齐 vuln agent 现行 output schema 策略（`title` 进 required——新数据必给；class 特有字段保持 optional）；
- collector 累积按 **ID 去重、后交覆盖**（模型修正同一条 finding 的场景）；重复覆盖时 warning 日志。

**扩展 `set_findings_summary`：`finding_roster` 声明字段**：

```json
"finding_roster": [ {"id": "AUTH-VULN-01", "title": "..."},
                    {"id": "AUTH-VULN-07", "title": "..."} ]
```

- **required**：新数据必给；**空数组 = 声明「本类无 vulnerable finding」**（不是缺省）；
- payload 只有几十行 ID+title（几百字符），断流面可忽略；title 是 required 字段，天然为定向重查提供「丢失条目研判的是什么」的线索；
- 语义：session 末尾声明的**全量** vulnerable findings 名单，host 以此为账本对账（§3.4）。

与现有 4 个 `set_*` 的关系：它们仍管 deliverable md 渲染（调用节奏不动：session 末尾每工具一次性全量）；`submit_finding` 是逐条即时的**数据**通道，`finding_roster` 是末尾的**对账**声明，互不替代。

### 3.4 Phase 2b — executor 对账编排（`executor.py`）

vuln agent 结束后，executor 在写盘前做确定性对账：

```
roster     = set_findings_summary.finding_roster（ID 集合）
submitted  = submit_finding 累积（by ID 去重后）
missing    = roster - submitted          # 漏交（模型行为遗漏）
extra      = submitted - roster          # 多交（未声明）→ 保留（召回优先）+ warning
```

| 对账结果 | 动作 |
|---|---|
| roster N 条，submitted N 条，ID 一致 | 完整：写盘 `{"vulnerabilities": submitted}`，完 |
| roster N 条，submitted M<N（缺 ID） | **定向重查**（下述），重查产出并入后写盘 |
| roster 若干，submitted 多出 extra | 保留 + warning |
| roster = []，submitted = [] | **真·无漏洞**：写 `{"vulnerabilities": []}`，validator 自然通过 |
| roster 字段缺失（summary 没调/没给该字段），submitted = [] | 完全忚逆：不写盘 → 防线报错 → 整跑重试（现状语义） |
| roster 缺失，submitted 非空 | 写盘（已收数据不丢）+ warning，跳过对账 |

**定向重查**（missing 非空时）：

- executor 追发一个**廉价小 agent**（`run_claude_prompt` 单次调用，tier 与主 vuln agent 同档起步）；
- 输入：missing 的 `(ID, title)` 清单 + 该 agent 自己产出的 deliverable md（**LLM 自身产物，非确定性层产物，守铁律**）+ repo 代码访问权 + 该 vc 的研判格式要求（复用 `vuln-*.txt` 的输出格式节）；
- 产出：structured_output 返回补交的 finding 条目（1-2 条小 payload，断流风险低；有 Phase 1 修复兜底）；不覆盖已收 ID，产出非 missing ID 时追加 + warning；
- **一轮封顶**：重查后仍有缺口 → warning + 降级接受（记录缺了哪些 ID），**不**为 1-2 条丢失触发 6-13 分钟整跑重试、把已到手的 N-1 条置于风险。

**写盘**：merged（submitted ∪ 重查产出，by ID）非空或 roster 显式为空 → `atomic_write_json`，格式不变，下游零改动。

### 3.5 Phase 2c — prompt 改造（5 个 `vuln-*.txt`）

- **加 `submit_finding` 指令**（英文，对齐现有 prompt 风格）：每确认一个 verdict 为 **vulnerable** 的 finding，**立即**调用一次 `submit_finding` 上交该条（one finding per call）；**不得攒批**（do not batch multiple findings into one call）、**不得攒到 session 结束**（do not hold findings until the end）；
- **加 `finding_roster` 指令**：session 结束调 `set_findings_summary` 时必须给出全量 ID+title 名单，与已提交条目一致；无 vulnerable finding 时给空数组；
- **删 final structured output 要求**：移除「session must conclude by returning a structured object of the form `{"vulnerabilities": [...]}`」一节及 `exploitation_queue_format` 相关指引，改为「session 以调用 4 个 `set_*`（含 roster）收尾」；
- executor 侧同步：vuln agent 调用的 `structured_output_schema` **停传**（不再要求结构化输出）；`-exploit` 等其他 agent 不动；
- 现有 4 个 `set_*` 工具的调用模式**不动**（session 末尾每工具一次性调用、传全量数组，管 deliverable md 渲染）。

**双引擎**：`submit_finding` / `finding_roster` 经 bridge.py 现有 set_* 桥自动双引擎对称（collector 通道已验证）；Phase 2 收尾在 openai 引擎跑一轮真机探针（`scripts/validate_openai_task_probe.py` 同法）确认工具调用正常。

## 4. 数据流（改造后）

```
vuln agent 分析中（6-13 min）
  ├─ 判 vulnerable → submit_finding(ID) ×N（逐条即时，host 进程内落袋）──→ collector 账本
  └─ session 末尾  ├→ 4 个 set_*（每工具一次全量，md 渲染通道，现状不动）
                  │    └─ set_findings_summary 携带 finding_roster（ID+title 全量声明）
                  └→ （不再要求 final structured output；final 消息只剩自由文本收尾）
                        │
        executor 确定性对账：roster vs submitted
                        │
        漏交 → 定向重查小 agent（(ID,title) 线索 + 自家 md + 代码）──→ 并入
                        │
        atomic_write_json(<vc>_exploitation_queue.json)（格式不变）
                        │
        validator（零改动）→ merge / renderer / check_coverage（零改动）
```

防御纵深：单条上交断流 = 整轮失败重试（无静默丢失）→ 漏交由 roster 对账捕获并由定向重查补齐 → 重查后仍缺则降级接受（warning）→ 完全忚逆（无 roster 无提交）由 validator 防线整跑重试。末条大 JSON 这一流中断的原始形态在 vuln 通道**不复存在**。

## 5. 错误处理与兼容

| 场景 | 行为 |
|---|---|
| 网关断流在单个 submit_finding 调用 | CLI 整轮失败重试/报错——无静默丢失（§1.4） |
| 模型漏交部分条目（roster 12 收 11） | 对账捕获 → 定向重查补齐 → 并入写盘 |
| 模型完全忚逆（不调 submit_finding 也不调 summary） | queue 缺失 → 防线报错 → 整跑重试（现状语义） |
| roster 缺失但 collector 非空 | 写盘（已收数据不丢）+ warning，跳过对账 |
| 多交（submitted 有 roster 无的 ID） | 保留（召回优先）+ warning |
| 真·无漏洞 | roster=[] + submitted=[] → 写空 queue，validator 过 |
| 重查后仍有缺口 | warning + 降级接受，不整跑重试 |
| 其他 agent 的 structured_output 断流 | Phase 1 修复独立生效（G1 保护全部 schema 消费者） |
| 同一 ID 重复上交 | collector 内后交覆盖 + warning |
| resume 旧 scan | queue 格式不变，新旧互认；`parse_lenient` 已宽容 |
| 双引擎 | 修复落 `llm_json.py` 共享层、工具走 bridge 双引擎桥——两侧对称，openai 侧真机探针收尾验证 |

## 6. 测试策略（TDD，只跑改动相关文件）

| 层 | 用例 |
|---|---|
| `llm_json.repair_truncated_json` 单测 | array 根 / object 根尾部截断；元素内部截断（丢残缺条）；围栏内提取后半截；截在首个完整元素前（返 None）；字符串字面量内假边界（含 `}` 的 notes）；完整 JSON 直通（返 None 由调用方处理语义） |
| `providers_anthropic` / `openai_result_mapper` 兜底集成 | 半截 collected_text → structured_output 含 N-1 条 + warning 日志断言 |
| executor 诊断 | validate raise 的 context 字段（stop_reason/tail/collector 计数）；静默跳过分支的 warning |
| collector `submit_finding` / `finding_roster` | 单条 schema 校验；ID 去重覆盖；roster required（缺字段拒收）；空数组语义 |
| executor 对账 | 完整通过 / 漏交触发重查 / 多交保留 / roster 缺失 collector 空（防线）/ roster 缺失 collector 非空（写盘+跳过对账）/ 双空写空 queue / 重查产出并入 / 一轮封顶降级 |
| 定向重查 agent | prompt 构造（missing (ID,title) + md + 格式节）；产出解析；非 missing ID 追加 + warning |
| prompt 快照 | 5 个 `vuln-*.txt` 的 submit_finding + roster 指令存在性锁定（`test_vuln_host_rendered.py` 同法）；**负面断言**：final structured output 指引无残留；铁律锁定测试（`test_static_dataflow_hints_decoupling.py`）维持绿 |

## 7. 分期与交付

- **Phase 1 = §3.1 + §3.2**（G1+G2）：provider 解析层止血，先行合入，独立生效（无 prompt / collector 改动）。
- **Phase 2 = §3.3 + §3.4 + §3.5**（G3+G4）：vuln agent 交付通道重构（单条上交 + roster 对账 + 定向重查），与 Phase 1 无实施依赖（Phase 1 保护的是其余 structured_output 消费者与重查 agent 的小 payload 兜底）。
- 一个 spec、两份 implementation plan（writing-plans 各出一份），Phase 1 先行。

## 8. Rejected alternatives 与 trade-off 记录

| 方案 | 拒绝理由 |
|---|---|
| 断点续传（截断时对最终消息发续写请求） | CLI 子进程单次 query 无状态，续传需 provider 层重发整段 history + 续写指令，复杂且引入新失败模式。其目标（补回丢失尾部）已由更便宜的定向重查实现（§3.4）：只重产丢失条目、有 `(ID, title)` 线索不盲查、不依赖半截输出 |
| 引 `json_repair` 库 | `llm_json.py` 模块定位是无 SDK/第三方依赖的纯字符串工具（code_index 复用、不拖依赖）；需求窄（仅尾部截断补闭合，非任意畸形修复） |
| A 方案：保留 final structured output 作全量兜底通道（与 collector union 仲裁） | roster 对账补上了「LLM 忝逆无兜底」这一 A 的唯一存在理由，且更便宜更精准（几百字符声明 vs 11k JSON 断流面；定位到条 vs union 碰运气）。保留 final 全量 = 双花 11k output token + 徒留大消息断流面 |
| queue json 加 recovery 元数据字段 | 消费方污染风险（merge 后 findings 带杂字段）；可观测性走日志足够 |
| 「分批上交省上下文」作为立项理由 | append-only history 下不成立（tool_use 常驻上下文，token 反而略贵）；G3 立项理由只有交付可靠性 |

## 9. 运维 runbook 注记（非代码改动）

- 同类故障现场：换 `glm-anthropic` / `ark-coding` profile 重跑 auth 阶段即可止血；
- llm-proxy 若自维护，排查流式空闲超时 / 响应长度限制（120s 挂起指向 `proxy_read_timeout` 类配置）；
- 排障入口：agents/*.log 搜 `repair_truncated_json` / `structured_output recovered` / `finding_roster` / `targeted recheck` 关键字。
