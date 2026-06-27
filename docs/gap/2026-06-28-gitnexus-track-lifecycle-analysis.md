# GitNexus 轨全生命周期分析 —— 关闭 LLM 轨时能否独立跑通

- **日期**: 2026-06-28
- **类型**: 分析报告（不改代码；纯现状核查 + 判断 + 建议）
- **分支**: `feat/fork-py`
- **触发**: `/superpowers:brainstorming` 「分析双轨模式下 GitNexus 的全生命周期，注意关闭 LLM 轨的时候，GitNexus 轨能跑通吗？」
- **核查方法**: 开关语义与关键判断点由本会话**亲自核验**（`concurrency.py` / spec）；其余代码定位由 Explore agent 核查给出 `file:line` 证据。

---

## 0. 一句话结论

**关闭 LLM 轨（`SHANNON_LLM_TRACK_ENABLED=0`）时，GitNexus 轨能端到端独立跑通并产出报告** —— A1（activity 注册）+ A4（merger 独立产出）+ 三层内部 LLM 各自降级，均已就位。**唯一真正的「跑不通」单点是 A3**：GitNexus 基础设施（CLI/MCP）本身不可用时，`run_code_index` 硬失败、无 fallback、fail-fast 连带取消并行的 PRE_RECON → 整个白盒 run 崩。关不关 LLM 轨都救不了这一种。

---

## 1. 背景与问题

shannon-py 白盒检测是**双轨**（CLAUDE.md §1）：

- **GitNexus 轨**（确定性兜底）：`parameter_graph.json` / `SinkCallSite` → `vuln_chain_builders/*_builder.py` 提候选链 → `chain_verdict.py` 跑轻量 LLM 判定 → `<vuln>_gitnexus_queue.json`
- **LLM 轨**（纯 LLM 增强）：`vuln-*.txt` agent → `<vuln>_exploitation_queue.json`
- **合并**：`run_merge_dual_track_queues`（`dual_track_merger.py`）verdict OR

战略意图（CLAUDE.md §1 / spec `2026-06-26-gitnexus-llm-sink-discovery §1.3`）：**GitNexus 轨 = 可靠兜底（即使 LLM 轨关闭也独立产出）；LLM 轨 = 可选增强（token 紧时关、宽裕时开）**。

本报告回答：**「关闭 LLM 轨」时，GitNexus 轨这条兜底路径到底能不能跑通？卡点在哪？**

---

## 2. 双轨模型 + 两开关语义校准（⚠️ 文档/代码认知差）

### 2.1 关键校准：现在是**两个独立开关**，不是一个

CLAUDE.md §1 和 spec `2026-06-26-gitnexus-llm-sink-discovery §3.3` 都写着：「若未来需要更细的『连 GitNexus 轻量 LLM 也关』档位，可另加 `SHANNON_GITNEXUS_LLM_ENABLED`（本 spec 不做，留 future）。」

**但代码里这个「future」开关已经落地**（本会话亲验 `packages/core/src/shannon_core/config/concurrency.py:43-47`）。所以现状是**两个互相独立的开关**：

| 开关 | 定义 | 控制什么 | 默认 | 亲验证据 |
|---|---|---|---|---|
| `SHANNON_LLM_TRACK_ENABLED` | `is_llm_track_enabled()` | **只 gate LLM 轨重型 vuln agent**（injection/xss/ssrf/auth-vuln）+ sink report fusion + entry point fusion | 开（`True`） | `concurrency.py:38-40` |
| `SHANNON_GITNEXUS_LLM_ENABLED` | `is_gitnexus_llm_enabled()` | **gate GitNexus 轨内部三层 LLM**（`discover_sinks_llm` / `analyze_taint_llm` / `judge_chain_verdict`） | 开（`True`） | `concurrency.py:43-47` |

> 两个开关读取同一个 `_is_truthy_env`（`concurrency.py:30-35`）：`'0'/'false'/'no'/'off'` → False，未设 → default。

### 2.2 这意味着什么

- **关 `SHANNON_LLM_TRACK_ENABLED=0`**：LLM 轨 vuln agent 不启动、fusion 跳过，但 **GitNexus 轨的轻量 LLM 仍照跑**（默认 `SHANNON_GITNEXUS_LLM_ENABLED=1`）。这正是「token 紧关 LLM 轨、靠 GitNexus 兜底」的设计意图 —— **开关语义是对的**。
- **进一步关 `SHANNON_GITNEXUS_LLM_ENABLED=0`**：GitNexus 轨内部三层 LLM 全部降级为纯确定性（见 §5）。这是「零 LLM 兜底」极端档位，能跑但降召回。

### 2.3 消费点（gate 住的位置，Explore 核查）

`SHANNON_LLM_TRACK_ENABLED` 的消费点：
- `cli/main.py:58` → 注入 `PipelineInput.enable_llm_track`（`shared.py:20`）
- `workflows.py:303` `if input.enable_llm_track:` → 创建 vuln agent activity；否则 `vuln_tasks = []`（`:316-320`）
- `workflows.py:166` sink report fusion、`:174` entry point fusion —— 关闭时跳过

`SHANNON_GITNEXUS_LLM_ENABLED` 的消费点（决定 GitNexus 轨三层 LLM client 是真 client 还是 raise stub）：
- `activities.py:362` `_make_gitnexus_llm_client`（喂 `discover_sinks_llm` + `analyze_taint_llm`）
- `activities.py:742` `_make_verdict_llm_client`（喂 `judge_chain_verdict`）

---

## 3. GitNexus 轨全生命周期（端到端数据流 + 证据）

```
pre-recon 阶段 (workflows.py:148, asyncio.gather 与 PRE_RECON 并行, 注释 # Fail-fast):
  run_code_index (activities.py:346-435)
    ├─ GitNexusEngine subprocess (gitnexus_engine.py:44 timeout=300s; 137-156 各失败→GitNexusError)
    ├─ GitNexus MCP 读取 (gitnexus_mcp.py:14 MCP_READ_TIMEOUT=30s; 118-124 超时→ConnectionError)
    └─ build_code_index_with_gitnexus (code_index/__init__.py:51)
         ① detect_sinks(blocks) ───────────────→ 规则 SinkCallSite[]
         ② discover_sinks_llm(...) (code_index/__init__.py:160)   [三层 LLM 之一: sink 补召回]
         ③ analyze_taint_llm(...) (code_index/__init__.py:180)    [三层 LLM 之二: intra taint]
         ④ chain_propagator → ParameterPropagationGraph → parameter_graph.json
         → code_index.json / parameter_graph.json / rule_gap_report.json

vuln 阶段:
  run_gitnexus_chain_verdict (activities.py:755-840, 已注册 worker)
    └─ injection_builder:44 / xss_builder:155 / ssrf_builder:31 → judge_chain_verdict (chain_verdict.py:211-270)  [三层 LLM 之三: 链判定]
       → <vuln>_gitnexus_queue.json
  run_auth_config_scan (auth 兜底, 已注册 + non-fatal)
  run_authz_gitnexus_judge (authz, 已注册)
  run_agent(vuln-*) [LLM 轨, 关闭时跳过] → <vuln>_exploitation_queue.json

合并:
  run_merge_dual_track_queues (activities.py:528-605, A4 已修)
    └─ merge_dual_track_queues (dual_track_merger.py:65-138, verdict OR; externally_exploitable 不覆写 52-57)
       → 最终 exploitation_queue.json (含 gitnexus-only 条目)
```

**注**：A1（`run_gitnexus_chain_verdict` + `run_auth_config_scan` 注册 worker）、A4（merger LLM queue 缺席仍独立产出）、配套（模块级 logger / auth scan non-fatal / 可观测日志）已在 `fed11ae..24e2475` 实现，final review Approved（见 memory `gitnexus-track-runtime-state-gap`）。

---

## 4. 关闭 LLM 轨时：各段能否独立跑通

设 `SHANNON_LLM_TRACK_ENABLED=0`（`SHANNON_GITNEXUS_LLM_ENABLED` 仍默认 `1`）：

| 阶段 / 组件 | 能否跑通 | 行为 | 证据 |
|---|---|---|---|
| `run_code_index` 建图 | ✅ | tree-sitter + GitNexus MCP + 三层 LLM（仍开）正常 | `workflows.py:148` |
| `discover_sinks_llm` | ✅（LLM 开） | 轻量 LLM 补召回规则盲区 sink，产 `rule_id="llm-discovered"` 软 sink | `sink_discovery_llm.py:225-257` |
| `analyze_taint_llm` | ✅（LLM 开） | 真 LLM intra taint 分析 | `llm_taint_analyzer.py:295-353` |
| `judge_chain_verdict` | ✅（LLM 开） | 逐链 LLM 判定 | `chain_verdict.py:211-270` |
| `run_gitnexus_chain_verdict` | ✅ | 产 `<vuln>_gitnexus_queue.json`（A1 已注册） | `activities.py:755` |
| LLM 轨 vuln agent | ⏭️ 跳过 | `vuln_tasks=[]`，不产 `<vuln>_exploitation_queue.json` | `workflows.py:303,316-320` |
| sink/entry fusion | ⏭️ 跳过 | 不合并 LLM 轨 sink/entry 发现 | `workflows.py:166,174` |
| `run_merge_dual_track_queues` | ✅ | LLM queue 缺席 → `gitnexus-only` 结果仍写入报告（A4 已修） | `activities.py:557-569` |

**判断：开关层面、链路层面，关闭 LLM 轨后 GitNexus 轨端到端能独立产出报告。** 这是 A1 + A4 + 三层 LLM 默认开 三个条件共同保证的。

---

## 5. 关闭 GitNexus 内部 LLM 时：「零 LLM 兜底」降级链路

设 `SHANNON_GITNEXUS_LLM_ENABLED=0`（GitNexus 轨三层 LLM client → raise stub），三层各自的降级（Explore 核查，均有 try/except 兜底，**不崩**）：

| 层 | 降级行为 | 证据 | 代价 |
|---|---|---|---|
| `discover_sinks_llm` | `llm_client` raise → 单函数 try/except `continue` → 返回空 → **纯规则 sink** | `sink_discovery_llm.py:234-248` | 规则盲区 sink（新 ORM 变体/未覆盖 receiver/动态调用）**漏报** |
| `analyze_taint_llm` | LLM 失败 → `_deterministic_intra_fallback`：全部参数保守 tainted + `is_entry_hint` 分层（直达 0.9 / 间接 0.5 / 字面量过滤） | `llm_taint_analyzer.py:260-288, 343-353` | taint 精度下降（保守全 tainted） |
| `judge_chain_verdict` | LLM 失败/非 JSON → `ChainVerdict(verdict="vulnerable", confidence="low")` | `chain_verdict.py:239-261` | 每条候选链保守判 vulnerable → 大量 `needs_review` 噪声 |

**净效果**：「零 LLM 兜底」能产 queue（不崩），但：(a) 规则盲区 sink 漏报；(b) verdict 全 conservative vulnerable/low 抬高 needs_review 噪声。

> 这就是 **B5 张力**（memory `gitnexus-track-runtime-state-gap` B 类第 5 项）：GitNexus 轨名义「确定性兜底」，实含三层函数级 LLM、默认开。「token 紧关 LLM 轨靠 GitNexus 兜底」的真实含义是 **关重型 agent、保留轻量 LLM**；若要连轻量 LLM 也零，得接受上述降级代价。这是 token 战略需要文档化的核心。

---

## 6. 唯一真正的「跑不通」单点：A3

**`run_code_index` 硬失败、无 fallback、fail-fast。**

- `workflows.py:148` 用 `asyncio.gather` 让 `run_code_index` 与 `run_agent(PRE_RECON)` 并行，注释明写 `# Fail-fast: if either fails, cancel the other and propagate.`（**无** `return_exceptions=True`，对比 vuln agents 的 gather 用了 `return_exceptions=True` `:329-331`）。
- `run_code_index` 内部任一环节失败 → `PentestError` → `ApplicationFailure`（`activities.py:430-435`），**无降级路径**：
  - GitNexus CLI 不可用 / `ensure_indexed` 失败 → `PentestError("GitNexus indexing failed")`（`activities.py:394-400`）
  - MCP 连接失败 → `PentestError("GitNexus MCP query failed")`（`activities.py:410-417`）
  - subprocess 5min 超时 / 非 0 退出 → `GitNexusError`（`gitnexus_engine.py:143-156`）
  - MCP 读取 30s 超时 → `ConnectionError`（`gitnexus_mcp.py:118-124`）
- **后果**：GitNexus 基础设施不可用时（CLI 未装 / 大仓 >10min 超时 / MCP 挂），`run_code_index` 硬失败 → 连带取消并行的 PRE_RECON → **整个白盒 run 崩**。CLAUDE.md §3「GitNexus 不可用会影响所有确定性轨」即此。

**这是关闭 LLM 轨也救不了的场景**：兜底轨的根（`code_index`）依赖 GitNexus 基础设施，基础设施没了，兜底也兜不住。A3 在 spec `2026-06-27-gitnexus-track-lifecycle-completion §2` 被**显式排除**（理由：改动敏感，动被测试锁定的硬失败语义 `test_run_code_index_raises_when_gitnexus_unavailable`），仍 open。

---

## 7. 仍 open 卡点清单 + 优先级

| 编号 | 卡点 | 类型 | 对「关 LLM 轨跑通」的影响 | 状态 |
|---|---|---|---|---|
| **A3** | `run_code_index` 硬失败无 fallback | **跑不通单点** | ★★★ 唯一致命：GitNexus 不可用时整 run 崩 | open（被 spec 显式排除，另议） |
| **A2** | `detect_language` 误判 `.js`→`ts`（`parser.py:9`） | 质量（非跑通） | ★★ 纯 JS 仓确定性产物 5 个空壳 | open（RE-1，另立 spec） |
| **B5** | token 战略：三层 LLM 默认开，「确定性兜底」名不副实 | 战略/文档 | ★ 两开关语义需文档化 + 默认策略对齐 | open |
| B6 | authz 轨不共享 chain_verdict 基建，另起一套 | 架构一致性 | —（authz 已能跑） | open |
| B7 | `rule_gap_report.json` 反哺闭环未闭合 | 演进 | —（YAGNI 有意） | open |

---

## 8. 文档认知差修正建议（不动代码，仅文档）

本次核查发现两处文档落后于代码，建议修正（**属文档维护，非代码改动；是否执行由用户决定**）：

1. **CLAUDE.md §1「双轨可配置」段**：把「`SHANNON_GITNEXUS_LLM_ENABLED`（本 spec 不做，留 future）」更新为「**已落地**（`concurrency.py:43-47`，默认开），与 `SHANNON_LLM_TRACK_ENABLED` 互相独立」。
2. **spec `2026-06-26-gitnexus-llm-sink-discovery §3.3 边界`**：同上，移除「留 future」表述，补充两开关独立 gate 的语义表（本报告 §2.1）。

---

## 9. 结论

| 问题 | 答案 |
|---|---|
| 关闭 LLM 轨（`SHANNON_LLM_TRACK_ENABLED=0`）时，GitNexus 轨能跑通吗？ | **能。** A1（注册）+ A4（merger 独立产出）+ 三层内部 LLM 默认开 + 各自降级兜底，共同保证端到端独立产 GitNexus-only 报告。 |
| 「关闭 LLM 轨」的精确语义？ | 只关 LLM 轨重型 vuln agent；GitNexus 轨轻量 LLM 由**独立开关** `SHANNON_GITNEXUS_LLM_ENABLED` 控制（默认仍开）。文档曾滞后称其「留 future」，实际已落地。 |
| 真正跑不通的场景？ | **A3**：GitNexus 基础设施（CLI/MCP）本身不可用时，`run_code_index` 硬失败、fail-fast 拖垮 PRE_RECON、整 run 崩。关 LLM 轨救不了。 |
| 「零 LLM 兜底」可行吗？ | 可行（关 `SHANNON_GITNEXUS_LLM_ENABLED=0`，三层降级不崩），但代价是规则盲区 sink 漏报 + verdict 全 conservative vulnerable/low 噪声。 |

**给「token 紧关 LLM 轨靠 GitNexus 兜底」战略的注脚**：该战略在「关重型 agent、保留轻量 LLM」语义下成立且已可跑通；但若 GitNexus 基础设施本身不可用（A3），任何开关组合都救不了 —— 兜底轨的根扎在 GitNexus 上，根断了兜底也断。A3 是该战略真正的可靠性短板。

---

## 10. 参考

- **代码**：`concurrency.py`（两开关，亲验）、`workflows.py:148,166,174,303,316-320`、`activities.py:346-435,359-377,528-605,740-751,755-840`、`sink_discovery_llm.py:225-257`、`llm_taint_analyzer.py:260-288,295-353`、`chain_verdict.py:211-270`、`gitnexus_engine.py:44,137-156`、`gitnexus_mcp.py:14,118-124`、`parser.py:7-13`、`dual_track_merger.py:52-57,65-138`
- **spec**：`2026-06-27-gitnexus-track-lifecycle-completion-design.md`（A1/A4/配套）、`2026-06-26-gitnexus-llm-sink-discovery-design.md`（两开关 + 三层 LLM）、`2026-06-26-gitnexus-intra-taint-deterministic-fallback-design.md`（立场 B `is_entry_hint`）
- **memory**：`gitnexus-track-runtime-state-gap`（4A+3B 评估，A1/A4 已实现）、`temporalio-activity-worker-registration`、`dual-track-decoupling-status`、`prerecon-recon-effect-gap-analysis-status`（RE-1）
- **CLAUDE.md**：§1 双轨概念 + 双轨可配置演进、§3 GitNexus 不可用影响
