# vuln findings 交付通道加固：截断修复 + 失败诊断 + submit_finding 单条上交

> 日期：2026-08-19　分支：`feat/pre-scan-local-agent`　类型：**bug 修复 + 可靠性重构**（provider 解析层 + vuln agent 交付通道，白盒 vuln 5 类通用，双引擎对称）
>
> 触发故障：NodeGoat-20260819-024451 白盒扫描 auth-vuln 三次 attempt 全部 `Missing exploitation queue`（另一环境，代码同源）。实施分两期：**Phase 1 = 截断修复 + 失败诊断**（止血，先行合入），**Phase 2 = submit_finding 单条上交**（治本，通道重构）。一个 spec、两份 implementation plan。

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

### 1.4 关键有利事实

queue 的全部下游消费方走 `VulnerabilityQueue.parse_lenient`（`models/queue_schemas.py:115-200`）——已是宽容解析（逐条校验、坏条目丢弃、invalid_json → 空 + warning）。**「截到最后一个完整元素」的半成品 queue 对 merge / renderer / check_coverage 天然兼容，消费方零改动。**

## 2. 目标 / 非目标

### 目标

- **G1（截断修复）**：流中断在消息尾部时，从半截 JSON 救回 N-1 条完整 findings，queue 落盘、validator 自然通过——**双引擎一次修好**（共享层落点）。
- **G2（失败诊断）**：防线报错时 PentestError context 携带 stop_reason / 文本长度与末尾片段 / 通道状态（structured_output 是否 None、collector 计数），静默跳过处补 warning 日志。
- **G3（submit_finding 单条上交）**：vuln agent 每确认一个 vulnerable finding **立即单条**经 collector 工具上交，host 确定性累积——findings 交付脱离对单条最终消息的依赖。
- **G4（union 仲裁）**：collector 主通道 + structured_output 兜底通道（带 G1 修复），by-ID union、collector 优先、召回优先。

### 非目标

- **断点续传**（检测截断后发廉价续写请求）——见 §8。
- **引 `json_repair` 库**——见 §8。
- **为省上下文做分批上交**——动机不成立：CLI 对话历史 append-only，tool_use block 上交后仍常驻上下文，分批 token 反而略贵（11k 提前进 history，后续每轮重复计 input）。G3 的立项理由**只有交付可靠性**。
- **queue json 加 recovery/来源元数据字段**——`parse_lenient` 虽忽略额外字段，但 merge 后 findings 可能带污染；可观测性走日志（§3.1/§3.4）。
- **TS 回移**——TS 走官方 API 无此故障；本设计是 PY 侧第三方网关适应性改造，明示偏离 TS（TS 的 vuln-collector.ts 无 findings 上交工具）。
- **改 `validate_deliverable` 签名**——它是纯函数拿不到 result，改签名波及全部调用方；诊断在 executor 层包装。
- **prompt 喂确定性产物**——本设计只加工具指令与输出格式约束，不引任何确定性层数据（CLAUDE.md §1 双轨铁律；`test_static_dataflow_hints_decoupling.py` 锁定面不受影响）。

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

- **包装 validate raise**：`executor.py:242-243` 调用 `validate_deliverable` 处包 `try/except PentestError`，re-raise 时 context 增补：`stop_reason`、`collected_text_len`、`collected_text_tail`（末 200 字符，判断截断的直接证据）、`structured_output_present: bool`、`collector_findings_count`（Phase 2 后有值，Phase 1 恒 0）、并合并现有 `_result_cost_context` 的 cost/tokens 字段。activities 侧 `except PentestError → end_agent → metrics` 链路已存在（error-path cost 修复先例），context 随之落 events.ndjson。
- **静默跳过补日志**：`executor.py:198-206` 的 `structured_output is None` 跳过分支补 `logger.warning`（agent 名、queue 文件名、text 长度）——现状零日志是排障全靠猜的根源。

### 3.3 Phase 2a — `submit_finding` collector 工具（`collectors/vuln.py`）

- 新 section `submit_finding`，**一次调用只接受一条 finding**（参数是单个 finding object，不是 array；禁止攒批——单次 payload 最小化，网关畸形/断流影响面最小，去重语义最清晰）；
- per-class schema，与 `set_strategic_intelligence` 同法按 vc branching（5 类字段对齐 `queue_schemas.py` 的 `BaseVulnerability` 子类家族）：`ID` / `vulnerability_type` / `externally_exploitable` / `confidence` / `title` / `notes` + class 特有字段（auth: `source_endpoint`、`missing_defense` 等）；
- required 对齐 vuln agent 现行 output schema 策略（`title` 进 required——新数据必给；class 特有字段保持 optional）；
- collector 累积按 **ID 去重、后交覆盖**（模型修正同一条 finding 的场景）；重复覆盖时 warning 日志；
- 与现有 4 个 `set_*` 并存：`set_*` 管 deliverable md 渲染，`submit_finding` 管 queue 数据；`set_findings_summary.patterns.representative_finding_ids` 引用的 ID 即 `submit_finding` 上交的 ID，一致性由 agent 保证（与现状相同）。

### 3.4 Phase 2b — executor 仲裁写盘（`executor.py`）

queue 写盘条件从 `structured_output is not None` 改为两通道 union：

```
collector_findings = collector 的 submit_finding 累积（按 ID 去重后）   # 主通道
final_findings    = structured_output.vulnerabilities（经 G1 修复）      # 兜底通道
merged            = final_findings 起底，collector_findings 按 ID 覆盖   # collector 优先
```

- **仲裁规则**：by-ID union，collector 条目优先（数据点对点落 host，更可信），structured_output 补差集——典型场景：agent 中途上交 11 条，最后研判的第 12 条直接进了 final output，union 救回；
- 任一通道非空即写盘 `{"vulnerabilities": [...]}`（格式不变，下游零改动）；
- 写盘后 `logger.info` 记仲裁构成（`collector=N final=M merged=K` 三种形态：纯 collector / 纯 final / union）——G2 的 `collector_findings_count` 同源；
- 两通道皆空 → 不写盘 → validator 防线报错重试（现状语义）。

### 3.5 Phase 2c — prompt 改造（5 个 `vuln-*.txt`）

在「Your Output」节加 `submit_finding` 指令（英文，对齐现有 prompt 风格）：

- 每确认一个 verdict 为 **vulnerable** 的 finding，**立即**调用一次 `submit_finding` 上交该条（one finding per call）；
- **不得攒批**（do not batch multiple findings into one call）、**不得攒到 session 结束**（do not hold findings until the end）；
- session 结束的 structured output **保留为兜底通道**，仍按 `exploitation_queue_format` 返回完整 queue（全量，不裁剪——兜底通道的完整性优先于省 output token；token 代价见 §8 trade-off 记录）；
- 现有 4 个 `set_*` 工具的调用模式**不动**（现状：session 末尾每工具一次性调用、传全量数组，管 deliverable md 渲染——与 `submit_finding` 的逐条即时是两种节奏，互不影响）。

**双引擎**：`submit_finding` 经 bridge.py 现有 set_* 桥自动双引擎对称（collector 通道已验证）；Phase 2 收尾在 openai 引擎跑一轮真机探针（`scripts/validate_openai_task_probe.py` 同法）确认工具调用正常。

## 4. 数据流（改造后）

```
vuln agent 分析中（6-13 min）
  ├─ 判 vulnerable → submit_finding(ID) ×N（逐条即时）──→ host collector 确定性累积（主通道）
  └─ session end  ├→ 4 个 set_*（每工具一次全量，md 渲染通道，现状不动）
                 └→ final structured output（全量）──→ G1 截断修复 ──→ 兜底通道
                                                        │
                                    executor 仲裁：union by ID，collector 优先
                                                        │
                                    atomic_write_json(<vc>_exploitation_queue.json)
                                                        │
                                    validator（零改动）→ merge / renderer / check_coverage（零改动）
```

纵深防御链：collector 断不了（工具调用即落 host）→ final 断流有 G1 修复（丢尾部残缺 1-2 条）→ 都空有 validator 防线重试。

## 5. 错误处理与兼容

| 场景 | 行为 |
|---|---|
| 网关断流在 final 消息尾部 | G1 救回 N-1 条；无 collector 时（Phase 1 / LLM 不听话）也独立生效 |
| LLM 忤逆不调 `submit_finding` | 老通道完整工作（A 方案意义所在）；Phase 1 修复后老通道本身已带止血 |
| 截断发生在第一个完整元素前 | `repair_truncated_json` 返 None → 不写盘 → 防线报错重试（语义与现状一致） |
| 同一 ID 重复上交 / 双通道 ID 冲突 | 后交覆盖（collector 内）；collector 覆盖 final（仲裁层）；均发 warning/info |
| resume 旧 scan | queue 格式不变，新旧互认；`parse_lenient` 已宽容 |
| 双引擎 | 修复落 `llm_json.py` 共享层、工具走 bridge 双引擎桥——两侧对称，openai 侧真机探针收尾验证 |

## 6. 测试策略（TDD，只跑改动相关文件）

| 层 | 用例 |
|---|---|
| `llm_json.repair_truncated_json` 单测 | array 根 / object 根尾部截断；元素内部截断（丢残缺条）；围栏内提取后半截；截在首个完整元素前（返 None）；字符串字面量内假边界（含 `}` 的 notes）；完整 JSON 直通（返 None 由调用方处理语义） |
| `providers_anthropic` / `openai_result_mapper` 兜底集成 | 半截 collected_text → structured_output 含 N-1 条 + warning 日志断言 |
| executor 诊断 | validate raise 的 context 字段（stop_reason/tail/collector 计数）；静默跳过分支的 warning |
| collector `submit_finding` | 单条 schema 校验；ID 去重覆盖；per-class branching |
| executor 仲裁 | 纯 collector / 纯 final（含 G1 修复形态）/ union / ID 冲突覆盖 / 双空不写盘 |
| prompt 快照 | 5 个 `vuln-*.txt` 的 submit_finding 指令存在性锁定（`test_vuln_host_rendered.py` 同法）；铁律锁定测试（`test_static_dataflow_hints_decoupling.py`）维持绿 |

## 7. 分期与交付

- **Phase 1 = §3.1 + §3.2**（G1+G2）：provider 解析层止血，先行合入，独立生效（无 prompt / collector 改动）。
- **Phase 2 = §3.3 + §3.4 + §3.5**（G3+G4）：vuln agent 交付通道重构，依赖 Phase 1（兜底通道的可靠性建立在 G1 之上）。
- 一个 spec、两份 implementation plan（writing-plans 各出一份），Phase 1 先行。

## 8. Rejected alternatives 与 trade-off 记录

| 方案 | 拒绝理由 |
|---|---|
| 断点续传（截断时发续写请求） | CLI 子进程单次 query 无状态，续传需 provider 层重发整段 history + 续写指令，复杂且引入新失败模式；G1 已把最坏损失压到尾部残缺条目，边际收益小 |
| 引 `json_repair` 库 | `llm_json.py` 模块定位是无 SDK/第三方依赖的纯字符串工具（code_index 复用、不拖依赖）；需求窄（仅尾部截断补闭合，非任意畸形修复） |
| B 方案：彻底切换只走 submit_finding、删 final 兜底 | 兜底全押 collector，LLM 忤逆时直接踩防线；「消灭大消息」的收益在 G1 落地后不再必要。选 A（双通道纵深） |
| queue json 加 recovery 元数据字段 | 消费方污染风险（merge 后 findings 带杂字段）；可观测性走日志足够 |
| 「分批上交省上下文」作为立项理由 | append-only history 下不成立（tool_use 常驻上下文，token 反而略贵）；G3 立项理由只有交付可靠性 |
| 全量 final output 的双花 output token | A 方案的既定代价：兜底通道完整性 > 省一次 11k output；有 cache 计费减免，且 G3 落地后 final 断流也不丢数据 |

## 9. 运维 runbook 注记（非代码改动）

- 同类故障现场：换 `glm-anthropic` / `ark-coding` profile 重跑 auth 阶段即可止血；
- llm-proxy 若自维护，排查流式空闲超时 / 响应长度限制（120s 挂起指向 `proxy_read_timeout` 类配置）；
- 排障入口：agents/*.log 搜 `repair_truncated_json` / `structured_output recovered` / `queue arbitration` 关键字。
