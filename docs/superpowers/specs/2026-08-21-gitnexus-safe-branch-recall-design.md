# GitNexus 轨 safe 枝召回（数据流视图剪断枝供数）设计

- 日期：2026-08-21
- 状态：已定稿（与用户逐轮确认：断流/剪断/打通三态语义 · intra 报告口径最小改 · 表达式兜底 · presumed-safe 不进 queue）
- 关联：`docs/superpowers/specs/2026-08-20-dataflow-view-design.md`（数据流视图消费端，safe 枝管道已就绪）、`docs/superpowers/specs/2026-08-21-gitnexus-track-hardening-design.md`（修复点 A 表达式回退先例）

---

## 1. 问题与目标

### 实测断链（NodeGoat-20260821-053502，新代码全管线扫描）

数据流视图 0 剪断枝：`{vc}_chain_verdicts.json` 全 vulnerable（inj 9 / xss 15 / ssrf 2，共 26 条，`sanitizer_annotations` 全空），`dataflow_view.json` 30 棵树 × 每树 1 枝 × 全打通。

safe 枝的**消费管道四层全部就绪**，等待一个从未到达的输入：

| 层 | 现状 |
|---|---|
| 判定（`chain_verdict.py`） | ✅ 完整支持判 safe（prompt 二选一、`_parse_verdict_json` 枚举校验） |
| 落盘（`_dump_chain_verdicts`） | ✅ safe 链也落盘（注释明示） |
| 组装（`dataflow_view.py`） | ✅ safe 枝进树、safe-only 树 `findings: []`、`_VALID_VERDICTS=(vulnerable, safe)` |
| 前端（PruningTreeFig） | ✅ 绿实线 ✂ 残端、灰靶心「无输入到达」渲染逻辑齐 |

**根因在候选产出端**：`llm_taint_analyzer` 的 intra prompt 报告口径——

> `tainted_params: list all parameters that can reach a sink` + `Only include paths you are confident about`

把两种本质不同的路径混为一谈、全部静默不报：

- **被防护阻断的污点路径**（数据流存在，污点被 sanitizer 终止）——应当报告（schema 的 `sanitized`/`sanitizer_description`/`post_sanitized_concat` 字段为此而设，parse 端 `sanitize_hint:xxx` 编码链路已通）；
- **断流**（参数外部不可控 / 数据流路径不存在）——正当不报。

LLM 看到 `escape()` 倾向把前一种也归入「reach 不了 / 没把握」而不报 → 防护流从未产出 TaintFlow → 从未成为候选 → chain_verdict 无从判 safe。

### 目标

重扫后 `dataflow_view.json` 出现 safe 枝（✂ 剪断枝渲染），断流继续由「排查过的入口 / 灰靶心」承载。

### 非目标

- 不改 chain_verdict 判定层（终审权不动）。
- 不改 schema（`sanitized` 等字段已有）、parse 层、落盘层、组装层、前端（四层零改动）。
- 不动断流承载（safe_vectors / 灰靶心现状已够）。
- 不动 LLM 轨（铁律：确定性产物不喂 LLM 轨 prompt——本设计全部改动在 GitNexus 轨内部）。

---

## 2. 三态语义（本设计的概念基座，与用户逐轮确认）

| 状态 | 定义 | intra 行为 | 数据流图归属 |
|---|---|---|---|
| **断流** | 参数外部不可控 / source→sink 数据流路径不存在 | **不报**（职责内正当否定） | 不进树；「排查过的入口」顶层区 / 灰靶心「无输入到达」 |
| **剪断** ✂ | 数据流路径存在，污点被防护终止 | **报**，标 `sanitized=true` + `sanitizer_description` | safe 候选 → chain_verdict 复核 → 剪断枝（绿实线 + ✂） |
| **打通** | 数据流存在，无防护或防护被绕过（`post_sanitized_concat`） | 正常报 | vulnerable 候选 → chain_verdict 终审 → 红虚线枝 |

职责分工（taint analysis 标准语义下 sanitizer 即污点边界，intra 判「污点终止」不越权）：

- **intra = 目击证人**：报路径事实 + 防护是什么（`sanitized=true` 只是「路上出现了防护」的客观标注，不判有效性）。
- **chain_verdict = 法官**：判防护有效性（slot/render_context 匹配、concat-after-sanitize、绕过），唯一终审。sanitized=true 候选可被翻成 vulnerable（防护被绕过 = 真阳性，照常进 queue）。

---

## 3. 设计

### P1（主修复）：intra prompt 报告口径三态化

`llm_taint_analyzer.build_taint_prompt` 的 Rules 段改写（一处文本，判定语义与 schema 零改动）：

- 删除 `Only include paths you are confident about`（它引导把防护路径归入「没把握」静默不报）；
- 新增三句：
  1. Report every path where the parameter's data **reaches the sink**（数据流路径存在即报）；
  2. If the path passes through a sanitizer/escape/validation that **stops the taint**, still report it with `sanitized=true` and `sanitizer_description`（防护阻断也报——这是安全侧证据，不是「不到达」）;
  3. Do NOT report parameters that are **not externally controllable** or have **no data-flow path** to any sink（断流不报）。

`post_sanitized_concat` 引导保持现状（防护后再拼原始输入 = 复染，报）。

成本：intra 调用次数零增加（per-function 批量问答不变），仅每次输出多几条 `sanitized=true` path 的输出 token 增量。NodeGoat 实测 intra 仅 8 次 LLM 调用（23 函数块 / 33 sink 位点 / 8 个含 sink 函数）。

### P2（兜底）：表达式级 presumed-safe 补候选

LLM 仍可能漏报防护路径（prompt 引导非强制）。确定性兜底，与 2026-08-21 修复点 A（intra 无信息时表达式回退）同构、不冲突：

`produce_intra_first_taint_flows` 现有逻辑为「intra 有非空 `tainted_params` 但该 sink 不在 `hits` = LLM 有依据的否定，尊重不回退」。本设计扩一条：**否定也送二审**——

- 对每个 sink 的 `dangerous_slots[].expression` 与同函数 `SourcePoint` 参数做匹配（substring，与修复点 A 同口径）；
- 匹配上但该 (source, sink) 对不在 `intra.hits` → 产一条 presumed-safe 候选流：`propagation_steps=[]`、`needs_review=True`、低置信（对齐 `_EXPR_FALLBACK_CONFIDENCE`）、`notes="presumed-safe"`；
- 与修复点 A 的边界：A 管「intra 无信息」（缺失/空判定），P2 管「intra 有信息但否定」；两路产物都 needs_review，notes 区分。

**规模上限**：per-sink presumed-safe 对数上限 `SUPERNOVA_PRESUMED_SAFE_MAX_PER_SINK`（默认 3）——防大仓 source×sink 笛卡尔积爆炸；超限丢弃并 log（no silent caps）。

**语义澄清**（对「尊重 LLM 有依据的否定」的修订）：presumed-safe 候选不是推翻 intra 否定，是否定也进入 chain_verdict 二审定生死——intra 终审权降为初审线索，终审统一归 chain_verdict。

### P3（安全阀）：presumed-safe 判 vulnerable 不进 queue

- **intra 报的 `sanitized=true` 候选**被 chain_verdict 翻成 vulnerable（防护被绕过）→ 真阳性，**照常进 gitnexus_queue**（现状行为）。
- **P2 presumed-safe 候选**（intra 明确否定过）被判 vulnerable → **只进 `chain_verdicts.json`**（数据流视图可见该枝）**不进 exploitation queue**（报告零影响）——防确定性兜底引入假阳漏洞。

builder 层按候选来源分流（读 `flow.notes == "presumed-safe"`）。

---

## 4. 数据流与分层职责（改动面）

```
build_taint_prompt（P1 文本改） ──┐
                                  ├→ TaintFlow 产出（P2 扩产流条件 + notes）
chain_propagator / intra-first ──┘        │
                                          ↓
                          extract_candidate_chains（零改动）
                                          ↓
                          chain_verdict 复核（零改动，素材变厚：sanitize_hint 步骤）
                                          ↓
                          builder（P3：presumed-safe 来源分流）
                                          ↓
                          _dump_chain_verdicts / dataflow_view / 前端（零改动）
```

改动文件：`llm_taint_analyzer.py`（P1 prompt）、`chain_propagator.py`（P2 产流）、`vuln_chain_builders/*_builder.py`（P3 分流）+ 各自测试。

### 横切不变量

| 维度 | 说明 |
|---|---|
| 双轨 | 全部改动在 GitNexus 轨内部；LLM 轨 prompt / collector 零触碰（铁律不动） |
| 双引擎 | intra / chain_verdict 均经 `run_claude_prompt` 统一抽象，无引擎分支 |
| 关轨（`SUPERNOVA_LLM_TRACK_ENABLED=0`） | safe 枝照常产出（GitNexus 轨自足），数据流视图完整 |

---

## 5. 风险与边界

- **intra prompt 是多轮调优敏感区**：P1 只加报告引导、删一句误导措辞，不动判定语义；回归靠 prompt 文本锁定测试 + 真机。若真机显示 LLM 仍漏报（P1 失效），P2 兜底独立成立。
- **假阳进报告**：仅 P2 来源有此风险，P3 阈断。
- **假阴（safe 枝仍缺）**：chain_verdict 把真 safe 判 vulnerable → 枝显示红——可接受（安全侧保守），不属于本设计范围。
- **大仓爆炸**：P2 上限 env 控制；intra 调用数不变。

---

## 6. 验证

- **单测**：
  - P1：prompt 文本锁定（含三态引导句；不含 `confident about` 措辞）。
  - P2：presumed-safe 产流条件（表达式匹配 + 不在 hits + notes/needs_review 标记 + 上限生效）；修复点 A 行为回归锁定。
  - P3：presumed-safe 来源 vulnerable 不进 queue、intra sanitized 来源 vulnerable 照常进 queue。
- **真机验收**：重扫 NodeGoat（双引擎任一）→ `dataflow_view.json` 含 `verdict=safe` 枝 → 前端 ✂ 剪断枝渲染；断流入口照常在「排查过的入口」区。
