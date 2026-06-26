# GitNexus 轨 LLM sink 补召回 + 双轨可配置 设计

**Date:** 2026-06-26
**Status:** Pending Review
**分支:** `feat/fork-py`

**相关 spec：**
- `2026-06-26-gitnexus-intra-taint-deterministic-fallback-design.md`（立场 B：GitNexus 轨不接 LLM）——本设计把它的 `is_entry_hint` 成果**保留为「LLM 不可用档」降级路径**，并演进立场 B 为「LLM 可用→召回+精度都上 LLM；不可用→退回确定性」。
- `2026-06-10-gitnexus-llm-taint-design.md`（`analyze_taint_llm` 逐函数 LLM 骨架）——本设计在其 `detect_sinks` → `analyze_taint_llm` 之间插入一个 LLM sink 发现 pass。
- `2026-06-25-injection-recall-port-design.md`（双轨消费模型 canonical）——本设计遵守「LLM 轨不吃确定性产物」铁律，只在 GitNexus 轨内部加 LLM。

---

## 0. 一句话结论

把 GitNexus 轨从「纯确定性」演进为「**确定性兜底 + LLM 补召回**」，使其成为可独立兜底的轨：对规则库没命中的可疑 call，用轻量 LLM 补发现 sink，产软 `SinkCallSite`（`rule_id="llm-discovered"`）与规则 sink 同流，并旁路产出 `rule_gap_report.json` 反哺规则库迭代。同时给 LLM 轨加可配置开关（**默认开**），实现「token 紧关 LLM 轨、靠 GitNexus 兜底」的成本控制策略。

---

## 1. 背景

### 1.1 痛点（规则盲区 = GitNexus 轨漏报源）

GitNexus 轨 sink 召回 = `detect_sinks`（`sink_detector.py:277-364`，AST + `DEFAULT_RULES` 规则库）。它遍历每个函数的所有 call 节点，但只有 **callee 在规则库 + receiver 匹配** 的 call 才产 `SinkCallSite`；规则没命中的 call 在 `:325`（`if not candidates: continue`）和 `:329`（`_rule_matches` 失败）被**直接丢弃**。

`build_code_index_with_gitnexus`（`code_index/__init__.py:51`）的 `analyze_taint_llm`（`:167`）**只对「有规则 sink 命中」的函数**跑（`sinks_by_func` 只含规则命中的函数）。**结论：规则库没覆盖的 sink 模式（新 ORM 变体、未覆盖 receiver、动态调用）→ 该函数 0 命中 → 不进 taint → GitNexus 轨漏报。**

当前这个盲区靠 LLM 轨（`vuln-*.txt` agent，全量 LLM 找 sink）双轨 OR 兜底。但 LLM 轨是重型 agent，token 消耗大，且用户希望「token 紧时关 LLM 轨」——届时盲区无人兜底。

### 1.2 与刚定稿立场 B 的关系（演进，不推翻）

`2026-06-26-gitnexus-intra-taint-deterministic-fallback` spec 选了**立场 B「GitNexus 轨不接 LLM」**，因为生产 `llm_client` 是 stub（`activities.py:368-376` 直接 raise）→ `analyze_taint_llm` 永远走确定性 fallback，接 LLM 也无处发挥。

本设计演进这个立场：**GitNexus 轨接 LLM 做召回（+精度），但 LLM 不可用时仍退回立场 B 的 `is_entry_hint` 确定性 fallback**。立场 B 的 `_deterministic_intra_fallback` 成果不浪费，成为「LLM 不可用档」。

### 1.3 用户战略（双轨可配置）

用户目标：**GitNexus 轨 = 可靠兜底（即使 LLM 轨关闭也能独立产出高质量召回）；LLM 轨 = 可选增强（纯 LLM 驱动，token 宽裕时开、紧张时关）**。因此 GitNexus 轨必须自给自足覆盖规则盲区——本设计的 LLM sink 补召回即服务于此。

---

## 2. 战略定位

| 轨 | 角色 | LLM 依赖 | 开关 |
|---|---|---|---|
| **GitNexus 轨** | 可靠兜底 | 含轻量 LLM 补召回；LLM 不可用退回纯确定性 | 常开 |
| **LLM 轨** | 可选增强（纯 LLM） | 重型 agent | **默认开**，env 可关 |

核心张力与化解：GitNexus 轨用 LLM 补召回**本身也烧 token**。化解靠**范围控制**——只对「规则没命中、但 callee/receiver 命中 sink-ish 模式」的 call 送 LLM（方案 A，详见 §3.1），典型项目去重后 30-80 个函数，远小于 LLM 轨全量重型 agent。

---

## 3. 设计

### 3.1 LLM sink 补召回 pass（范围 = 方案 A：半 sink 精准）

#### 收集粒度 = call 级，调用粒度 = function 级

- **收集（call 级）**：新增半 sink 收集器 `collect_suspicious_calls(blocks, parser, source_provider)`——**独立遍历**（复用 `parser.iter_calls`，与 `detect_sinks` 同样的 call 枚举；接受双遍历开销换 `detect_sinks` 零改动、可独立单测），在规则丢弃条件处（callee 不在规则库 `:325`、或 receiver 不匹配 `:329`）把「callee 或 receiver 命中 sink-ish 模式」的 call 收为可疑候选。
- **调用（function 级）**：可疑 call 按 `FuncBlock` 去重分组，**一个函数一次 LLM 调用**（列出该函数所有可疑 call）。

> 设计理由：call 级收集精准（只看规则没命中的可疑 call），function 级调用省 token（每函数一次）。同一函数既有规则 sink 又有可疑 call 时，可疑 call 仍被收集——补的是「规则没命中的 call」，与函数有无其他规则 sink 无关。

#### sink-ish 模式清单（初稿，可迭代）

新增 `_SUSPICIOUS_CALLEE_PATTERNS`（regex，放 `sink_detector.py`，与规则库同处）：

```
query | exec(ute)? | render | redirect | include | require |
unserialize | pickle | loads | system | popen | raw | where |
format | template | open | fetch
```

匹配 callee 名或 receiver 名。比扩完整规则库轻——精确 receiver 匹配 + taint 判定交给 LLM 兜底。

#### 输入（扫什么）

每个可疑候选 `SuspiciousCall`：

```python
@dataclass(frozen=True)
class SuspiciousCall:
    block: FuncBlock            # 含 source / parameters / file_path
    callee: str
    receiver: str | None
    arg_exprs: list[str]        # parser.extract_arg_expressions 的结果
    file_path: str
    line: int
    column: int
```

#### LLM 调用

```python
async def discover_sinks_llm(
    suspicious_by_func: dict[str, list[SuspiciousCall]],
    llm_client: LLMClient,
) -> tuple[list[SinkCallSite], list[RuleGap]]:
    """对每个含可疑 call 的函数调一次 LLM，判定哪些是真 sink。"""
```

per-function prompt：函数源码（走 `analyze_taint_llm` 同款截断：上限 1200 行，前 1000 + sink 行 ±30）+ 该函数可疑 call 清单 → **structured output**，每个 call 返回：

```python
class SuspectedSink(BaseModel):
    call_ref: str              # 对回 SuspiciousCall（callee+line）
    is_sink: bool
    category: SinkCategory | None
    slot: SlotContext | None
    arg_index: int = -1        # 可疑实参位
    rationale: str             # 判定理由（→ rule_gap_report）
```

#### 输出（产什么）—— 两层

**层 1：软 `SinkCallSite`（参与检测，进 `code_index.json`）**

LLM 判 `is_sink=True` 的 call → 构造 `SinkCallSite`：

| 字段 | 取值 |
|---|---|
| `id` | `_make_id(block, callee, call)`（复用现有生成） |
| `caller_id` / `callee_name` / `callee_receiver` / `file_path` / `line` / `column` | 来自 `SuspiciousCall` |
| `category` / `sink_subtype` | LLM 给；`category` ∈ `SinkCategory`（标准枚举，过下游 category 白名单），`sink_subtype` 由 LLM 给具体值（如 `sql_raw`/`cmd_shell`），给不出则回落 `category.value` |
| `dangerous_slots` | LLM 给的 `arg_index` + `slot`；`expression` 从 `arg_exprs` 取；`is_entry_hint` 用现有 `is_entry_hint()`（`sink_detector.py:247`）算 |
| `rule_id` | **`"llm-discovered"`** ← 区分规则 sink 的主键 |
| `needs_review` | `True` |

与规则 sink **合并**进 `CodeIndex.sink_call_sites`（`code_index/__init__.py:219`）→ 写 `code_index.json` → 同流（`analyze_taint_llm` intra / `chain_propagator` / `injection_builder` / `chain_verdict`）。

**层 2：`rule_gap_report.json`（旁路，驱动规则优化）** ★

聚合所有软 sink by `(language, callee, receiver_pattern, category, slot)`：

```json
[
  {"pattern": "raw_query@custom_db", "language": "python", "category": "sql",
   "slot": "sql_value", "count": 12,
   "sample_evidence": ["repo/service.py:42  db.raw_query(\"...\"+id)", "..."],
   "suggested_rule_id": "py-custom-db-raw-query"}
]
```

- **落点：** 由 `build_code_index_with_gitnexus` 在写 `code_index.json` 同处产出（deliverables 目录），与检测流程解耦——消费方可独立读取做规则优化，不参与 taint/verdict 主流。

闭环：**LLM 补召回 → 规则缺口信号 → 人工/脚本据此加 `SinkRule` 进 `DEFAULT_RULES` → 该模式今后由确定性规则召回（不再烧 LLM token）**。随规则库迭代，LLM 补召回的命中数应单调下降（可观测）。

#### 区分规则 sink vs LLM sink

- 主键 = `SinkCallSite.rule_id`（`"llm-discovered"` vs 真 rule_id）——**不改 `SinkCallSite` 结构**（共享模型零侵入，下游 `chain_verdict`/merger 无感）。
- 规则缺口只来自 `rule_gap_report.json`（只含 LLM sink），可独立于检测流程消费。

### 3.2 数据流（端到端）

```
build_code_index_with_gitnexus (code_index/__init__.py:51)
  │
  ① detect_sinks(blocks) ─────────────────→ 规则 SinkCallSite[]  ──┐
  ② collect_suspicious_calls(blocks) ──┐                            │
                                       ▼                            │
  ③ discover_sinks_llm(suspicious,     软 SinkCallSite[](rule_id=   │
       llm_client)  ─────────────→     "llm-discovered") ───────────┤
                                       │                            │
                                       ▼                            ▼
                              合并 SinkCallSite[] → CodeIndex.sink_call_sites
                                       │                  → code_index.json
                                       ▼
  ④ analyze_taint_llm(sinks_by_func 含软 sink, llm_client) → IntraResult
                                       │   (LLM 不可用 → _deterministic_intra_fallback)
                                       ▼
  ⑤ chain_propagator → ParameterPropagationGraph → parameter_graph.json
                                       │
                                       ▼
  ⑥ injection_builder → chain_verdict(judge_chain_verdict) → *_gitnexus_queue.json
                                       │   (merge OR LLM 轨, 若开)

  旁路：discover_sinks_llm → rule_gap_report.json（驱动规则优化）
```

关键顺序：`discover_sinks_llm`（③）必须在 `analyze_taint_llm`（④）**之前**完成——软 sink 要并入 `sinks_by_func` 才能进 intra taint 分析。LLM 不可用时 ③ 返回空，④ 的 `sinks_by_func` 只含规则 sink，流程不中断。

### 3.3 LLM 轨可配置开关（默认开）

- **接入点：** `workflows.py:296-307`（`vuln_tasks` 创建循环前）。
- **开关：** env `SHANNON_LLM_TRACK_ENABLED`，默认 `"1"`（**默认开**）。读取经现有 env 读取习惯（参照 `SHANNON_MAX_CONCURRENT`，`shared.py:19`）。
- **关闭时：** 不创建 vuln_tasks、不跑 `run_vuln_agent`；`run_merge_dual_track_queues` 只消费 `*_gitnexus_queue.json`（merger 的 llm-only/both 分支自然不触发）。
- **可观测：** workflow 日志明确输出 `llm_track=enabled|disabled`，避免「LLM 轨没跑」被误判为 bug。
- **边界（重要，回应省 token 理念）：** `SHANNON_LLM_TRACK_ENABLED` **只控 LLM 轨**（重型 vuln agent）。GitNexus 轨的 LLM（`discover_sinks_llm` / `analyze_taint_llm` / `chain_verdict`）接通 `llm_client` 后**默认开**，其「关闭」= `llm_client` 未接通/stub → 自然降级到纯规则 + `is_entry_hint`（§3.5）。即：**关 LLM 轨省的是「重型 agent」大头 token，GitNexus 轨的「轻量 LLM」仍跑**（这正是它作为兜底轨的价值——比 LLM 轨便宜得多，见 §2 量级）。若未来需要更细的「连 GitNexus 轻量 LLM 也关」档位，可另加 `SHANNON_GITNEXUS_LLM_ENABLED`（本 spec 不做，留 future）。

### 3.4 接通生产 llm_client（两个 stub → 真 client）

GitNexus 轨当前有**两个 LLM stub**，本设计都要接通：

| stub | 位置 | 用途 | 接通方式 |
|---|---|---|---|
| `_llm_taint_client` | `activities.py:368-376`（raise `RuntimeError`） | `analyze_taint_llm` intra | 用 `run_claude_prompt`（`runner.py:90`）封装成 `async (prompt, **kwargs) -> str` |
| `_gitnexus_verdict_llm_client` | `activities.py:710-721`（stub） | `chain_verdict` 判定 | 同上 |
| 新 `discover_sinks_llm` | — | sink 补召回 | 复用同一 client |

接通后，`analyze_taint_llm` 走真 LLM 成功路径（`llm_taint_analyzer.py:274-276`），`_deterministic_intra_fallback` 仅在 LLM 调用失败/返回不可解析时触发（保留为降级）。

### 3.5 降级 / 错误处理

| 情况 | 处理 |
|---|---|
| `llm_client` 仍 stub / 接通失败 | `discover_sinks_llm` 返回空（不补召回）；intra 走 `_deterministic_intra_fallback`（立场 B） |
| LLM 超时 / 限流 | `discover_sinks_llm` 对该函数跳过（返回空），其余函数继续；记日志 |
| LLM JSON 解析失败 | 重试 1 次（同 `analyze_taint_llm`），仍失败则该函数跳过 |
| LLM 误报软 sink | `needs_review=True` + 下游 `chain_verdict` 二次判定过滤 |

核心原则（继承 `gitnexus-llm-taint-design §7`）：**宁可漏（不补召回）不可崩**——`code_index` 必须产出，LLM 任一环节挂都不能阻断确定性骨架。

---

## 4. 不做什么（范围控制）

- ❌ **不改 `SinkCallSite` 结构**：靠 `rule_id="llm-discovered"` 区分，不新增字段（共享模型零侵入）。
- ❌ **不合并「找 sink」与「intra taint」为一次 LLM 调用**：保持两个独立 pass（关注点分离、可独立测试/降级）。合并为可选优化，留待实测 token 后评估。
- ❌ **不扩到入口函数全扫（方案 B）**：分层架构下入口函数多不含 sink、盲区 sink 多在下游，B 错位（详见 brainstorming 记录）。
- ❌ **不改 LLM 轨 prompt / 不喂确定性产物给 LLM 轨**：遵守双轨消费模型铁律；本设计的 LLM 全在 GitNexus 轨内部。
- ❌ **不改 `chain_verdict` / merger 逻辑**：软 sink 走现有 SinkCallSite 同流，下游无感。`externally_exploitable` 解耦（injection-recall 改动 3′）独立推进，不在此。
- ❌ **不做规则自动入库**：`rule_gap_report.json` 供人工/脚本消费，自动加规则属另题。

---

## 5. 测试策略

目标文件：`packages/core/tests/code_index/`（扩 `test_sink_detector.py`、新建 `test_sink_discovery_llm.py`）+ `packages/whitebox/tests/`。

1. **半 sink 收集器** `collect_suspicious_calls`：
   - `db.raw_query(x)`（callee `raw_query` 命中 sink-ish、规则无）→ 收集。
   - `cursor.execute(x)`（规则命中）→ **不**收集（已被规则吃）。
   - `helper(x)`（非 sink-ish）→ **不**收集。
2. **`discover_sinks_llm`**（mock llm_client）：
   - LLM 判 `is_sink=True` → 产软 `SinkCallSite`，`rule_id="llm-discovered"`、`needs_review=True`、`dangerous_slots` 来自 LLM。
   - LLM 判 `is_sink=False` → 不产。
   - 函数截断：>1200 行走截断策略。
3. **rule_id 区分断言**：规则 sink `rule_id != "llm-discovered"`；LLM sink `rule_id == "llm-discovered"`。
4. **`rule_gap_report.json` 聚合**：N 个同模式软 sink → 聚合成 1 条 gap（count=N、sample_evidence 非空）。
5. **降级**：llm_client 为 None / raise → `discover_sinks_llm` 返回空、不抛；intra 走 `_deterministic_intra_fallback`。
6. **合并流集成 smoke**：软 sink 并入 `sinks_by_func` → `analyze_taint_llm` 能对其产 `IntraResult.hits` → `chain_propagator` emit `TaintFlow`（构造 CallChain + blocks）。
7. **LLM 轨开关**：`SHANNON_LLM_TRACK_ENABLED=0` → `vuln_tasks` 不创建、workflow 日志输出 `llm_track=disabled`；`=1`（默认）→ 正常创建。
8. **llm_client 接通回归**：`_llm_taint_client` / `_gitnexus_verdict_llm_client` 不再 raise，返回 `run_claude_prompt` 结果；现有 `analyze_taint_llm` LLM 成功路径测试不破。
9. **现有 `detect_sinks` + intra 回归**：规则 sink 检测、`_deterministic_intra_fallback` 行为不变。

---

## 6. 风险

- **token 成本**：sink-ish 模式过宽 → 可疑 call 过多 → LLM 调用爆。缓解：初稿模式偏保守（只高频 sink 词）；可观测每仓 LLM 调用数 + token，超阈值告警；`rule_gap_report` 驱动规则迭代应让 LLM 命中单调下降。
- **LLM 误报软 sink**：可能抬高 GitNexus 轨假阳性。缓解：`needs_review=True` + `chain_verdict` 二次判定；merger verdict OR 不放大假阳性（LLM 轨独立判定）。
- **软 sink 下游白名单**：软 sink `category` 是标准 `SinkCategory`（SQL/COMMAND 等，过 category 白名单），但 `sink_subtype` 是新值（如 `sql_raw` 经 LLM 给）。须确认 `injection_builder` → finding 不被 `VALID_INJECTION_CATEGORIES`（`finding_models.py:28`）按 subtype 误拒（plan 加断言，同 injection-recall 改动 1.2 D）。
- **接通 llm_client 的成本回归**：intra + verdict + discovery 三处接 LLM，单仓 LLM 调用从 0 涨到「函数数」级；且含软 sink 的函数会先后调 discovery（找 sink）+ intra（追 taint）**两次** LLM（§4 选择不合并）。缓解：三处都有「失败即降级」兜底；实测 token 后若过高，可评估合并 discovery+intra 为一次调用（§4 已留口）；必要时加并发限流（复用 `SHANNON_MAX_CONCURRENT` 模式）。
- **`_SUSPICIOUS_CALLEE_PATTERNS` 漏模式**：模式清单不全 → 某些盲区 sink 仍漏。缓解：这是「尽力补召回」非「全量保证」，LLM 轨（若开）仍双轨 OR 兜底；`rule_gap_report` 也能反向暴露「LLM 轨发现但 GitNexus 没召回」的缺口（future：对比两轨 sink 集合）。

---

## 7. 完成定义

- `collect_suspicious_calls` + `discover_sinks_llm` 落地，接入 `build_code_index_with_gitnexus`（`detect_sinks` 之后、`analyze_taint_llm` 之前）。
- 软 `SinkCallSite`（`rule_id="llm-discovered"`）与规则 sink 合并进 `code_index.json`，能走完 intra → propagate → verdict 全流。
- `rule_gap_report.json` 旁路产出，含聚合缺口 + sample evidence。
- 两个 llm_client stub 接通真 client（`run_claude_prompt`），失败降级保留。
- `SHANNON_LLM_TRACK_ENABLED` 开关生效（默认开），关闭时不跑 LLM 轨。
- 第 5 节全部测试通过（含降级 + 现有回归）。
- 真机冒烟（follow-up）：真实仓库跑 `run_code_index`，确认 `code_index.json` 出现 `rule_id="llm-discovered"` 条目、`rule_gap_report.json` 非空、`taint_flows` 数量较纯规则上涨；关 LLM 轨后 GitNexus 轨仍独立产 queue。

---

## 8. 与相关 spec 的关系

| spec | 关系 |
|---|---|
| `2026-06-26-gitnexus-intra-taint-deterministic-fallback` | 演进其立场 B：`is_entry_hint` fallback 保留为「LLM 不可用档」降级；本设计在其 `detect_sinks`→`analyze_taint_llm` 间插 LLM sink 发现 |
| `2026-06-10-gitnexus-llm-taint` | 复用其 `detect_sinks` / `analyze_taint_llm` / `chain_propagator` 骨架与截断/重试策略 |
| `2026-06-25-injection-recall-port` | 遵守双轨消费模型；其改动 1.2（扩规则）与本设计的 `rule_gap_report` 形成规则迭代闭环（前者人工扩，后者 LLM 驱动扩） |
