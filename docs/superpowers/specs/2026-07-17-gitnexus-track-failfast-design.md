# GitNexus 轨 fail-fast 改造设计

> 日期 2026-07-17 | 分支 `feat/fork-py` | 状态 design(待 review → plan)

## 1. 背景

GitNexus 轨的判定环节(`run_gitnexus_chain_verdict` / `run_authz_gitnexus_judge`)当前对失败采取 **graceful degradation + 跨轨兜底**:失败时不产 queue,寄望"另一条 LLM 轨补"。这有两个问题:

- **关轨假兜底**:`SHANNON_LLM_TRACK_ENABLED=0` 时 LLM 轨关,GitNexus 降级文案写的"靠 LLM 轨兜底"落空 → 两轨都空 → **静默全空的假报告**(比显式失败危险得多)。
- **开轨掩盖真问题**:GitNexus 轨的真问题(blockage / authz ID 丢 / chunk 超时)被 LLM 轨 OR 掉,永远不暴露、永远不修——GitNexus"保下限"的承诺实际由 LLM 轨代持。

**根因**:两个不同概念被混在一起——

- **概念 A**(GitNexus 轨**内部** LLM 补召回):source/sink/route/chain_verdict/authz 深判——GitNexus 轨的一等公民成员,**正常工作**,不是兜底。
- **概念 B**(GitNexus 轨**失败** → 跨轨靠 LLM 轨兜底):**异常处理**。

当前代码把 B 写进 A 的失败路径。

## 2. 目标 / 非目标

**目标:**
- GitNexus 轨判定失败时 **fail-fast**(显式暴露),删跨轨降级兜底。
- 开轨:该类 fail → 标红 + 其他类继续(LLM 轨补)。
- 关轨:`DEGRADABLE`(inj/xss/ssrf)任一 fail → 终止扫描(这些类关轨无 LLM 兜底);**authz 永不终止**(authz-vuln LLM 关轨仍跑兜底)。
- 区分「合法空结果」(跑通 0 findings)与「真失败」(流程断裂),不误伤。

**非目标:**
- 不改 GitNexus 轨**内部** LLM 补召回(source/sink/route/authz 0 候选探索)——概念 A,正常工作。
- 不改 attack_chain / source / sink / route 环节(其失败传导成"前置缺失"被判定环节捕获)。
- 不提升 GitNexus 轨可靠性本身(blockage / ID 丢等另立项)——fail-fast 只负责**诚实暴露**。
- 不动 LLM 轨 / host-rendered(另一条路径)。

## 3. fail 判据 = 流程完整性

**原则**:看 GitNexus 轨**是否完成了它该完成的流程**,而非结果是否非空。

| 情况 | 判定 |
|---|---|
| 前置产物缺(`parameter_graph`/`code_index`/`framework_analysis` 文件不存在) | **failed** |
| 前置产物 parse 失败(损坏) | **failed** |
| builder / verdict agent 抛异常 | **failed**(该类) |
| LLM 输出 invalid JSON,经现有三层防线(L0/L1/L2)仍不可用 | **failed** |
| 跑通后 0 findings / 0 候选 / 0 verdict(图正常、agent 正常返回) | **ok**(合法结论,产空 queue) |
| authz 0 候选 → 探索产软候选 | **ok**(概念 A,正常补召回) |

**不做"空壳图"检测**:`parameter_graph` 能 parse 即视为前置就绪,`taint_flows=0` 视为合法结论(信任流程;空壳检测会误伤"代码真没 taint",且阈值判定本身不可靠)。

## 4. 覆盖范围

- `run_gitnexus_chain_verdict`(inj/xss/ssrf 3 类)
- `run_authz_gitnexus_judge`(authz IDOR)

`attack_chain_assembler` / `source_discovery_llm` / sink discovery / route 绑定**不在本 spec**。

## 5. 实现:workflow 统一编排

**总原则**:业务 fail(GitNexus 判定流程断裂)→ 写状态产物 + workflow 决策,**activity 不 raise**;系统 error(代码 bug / 基础设施崩)→ `ApplicationFailure`(Temporal,行为不变)。

### 5.1 activity 返回值带 fail 信息(不 raise)

**`run_gitnexus_chain_verdict`**(`activities.py:1180`):
- 前置缺/无效:当前 `return {"skipped":...}` → 改为 `return {"per_class":{}, "failed_classes":["injection","xss","ssrf"], "fail_reason":"parameter_graph missing/invalid"}`(不 raise)。
- builder 异常:当前 `warning + continue` → 改为 `failed_classes.append(vc)` + reason,continue 其他类(不 raise)。
- 跑通 0 findings:`per_class[vc]=0`,`status=ok`(不进 failed)。

**`run_authz_gitnexus_judge`**(`activities.py:420`):
- 当前末尾 `except → raise ApplicationFailure`(:582-587)→ 改为:业务 fail(`code_index`/`framework` 缺、verdict agent 异常、parse 三层防线后仍坏)→ `return {..., "failed":True, "fail_reason":...}`(不 raise);仅真系统异常 raise。
- 0 候选→探索、探索产软候选 → `failed:False`(概念 A 保留)。

### 5.2 状态产物 `gitnexus_track_status.json`

workflow 在两 activity 返回后汇总写 `deliverables/gitnexus_track_status.json`:
```json
{
  "injection":{"status":"ok","findings":3},
  "xss":{"status":"failed","reason":"builder raised: KeyError ..."},
  "ssrf":{"status":"ok","findings":0},
  "authz":{"status":"ok","findings":2}
}
```

### 5.3 workflow 决策(`workflows.py`)

读状态产物 + `is_llm_track_enabled()`(`concurrency.py:40`):
- **关轨(`False`)且 `DEGRADABLE_VULN_CLASSES`(inj/xss/ssrf,`agents.py:13`)中任一 `failed` → raise,扫描终止。** 这三类关轨后 LLM 轨也关,GitNexus fail = 真·无兜底。
- **authz 的 GitNexus fail 永不终止**——authz-vuln LLM 轨**关轨时仍跑**(`authz ∉ DEGRADABLE`,做 GitNexus 做不了的 Vertical/Context),GitNexus authz fail 永远有 LLM 兜底 → 仅标红。
- 开轨 → 所有 GitNexus fail 仅标红(LLM 轨补),由 merger/report 读状态产物呈现。

> 不变量:终止判定 = 「GitNexus fail **且** 该类在当前模式下无 LLM 兜底」。inj/xss/ssrf 关轨时无兜底→可终止;authz 的 LLM 轨不可关→永不终止。

### 5.4 merger 适配(`dual_track_merger.py:65` / `run_merge_dual_track_queues` `activities.py:824`)

读状态产物区分:
- GitNexus `failed` 的类 → **不静默退 llm-only**(那是我们要删的降级);开轨下标红 + 退 llm-only(LLM 轨补,状态产物已标 failed 供报告知情)。
- GitNexus `ok` 的类(含合法空)→ 正常合并(空→llm-only / 非空→both 或 gitnexus-only)。

### 5.5 报告标红

报告阶段读状态产物,`failed` 的类章节头部加注记:"GitNexus 轨判定失败(reason),结果由 LLM 轨提供"。

## 6. 删除项

`run_gitnexus_chain_verdict` / `run_authz_gitnexus_judge` 里所有"靠 LLM 轨兜底"log 文案 → 改陈述事实:"GitNexus 轨本类失败:reason"或"GitNexus 轨本类 0 findings(合法结论)"。

## 7. 不变量(铁律)

- **双轨独立性**:`gitnexus_track_status.json` **只给 workflow/merger/report 编排用,绝不喂 LLM 轨 prompt**。新增 AST/grep 不变量测试锁定(对齐 `test_static_dataflow_hints_decoupling.py` 风格)。
- **业务 fail vs 系统 error**:业务 fail 不 raise(状态产物);系统 error raise `ApplicationFailure`。
- **authz 探索保留**:0 候选→探索是概念 A(内部补召回),不视为 fail。
- **双引擎 / host-rendered 不受影响**:改动在 workflow/activity/merger/report 层,不涉引擎差异与 LLM 轨 `.md` 路径。

## 8. 测试(TDD)

1. fail 判据:前置缺/无效/builder 异常/LLM 三层防线后坏 → `failed`;跑通 0/探索产软候选 → `ok`。
2. `chain_verdict` 返回值:`failed_classes` 正确、不 raise。
3. `authz` 返回值:业务 fail → `failed:True` 不 raise;系统异常 → `ApplicationFailure`。
4. 状态产物:workflow 正确汇总写 4 类状态。
5. 开轨:某类 `failed` → 其他类继续 + merger 退 llm-only + 报告标红。
6. 关轨:`DEGRADABLE`(inj/xss/ssrf)任一 `failed` → workflow raise 终止;**authz `failed` → 不终止**(authz-vuln LLM 常驻兜底)。
7. merger:区分 `failed`(标红退 llm-only)vs 合法空(正常合并)。
8. 铁律:LLM 轨 prompt 不含/不读 fail 状态(AST/grep 锁定)。

## 9. 影响面 / 风险

- **改动文件**:`activities.py`(chain_verdict/authz)、`workflows.py`、`dual_track_merger.py` / `run_merge_dual_track_queues`、报告渲染、新增状态产物读写。
- **行为变化(期望)**:关轨下原本"静默跑完出全空假报告"的扫描会**更早 fail 显式暴露**——倒逼修 GitNexus 本身。需告知用户:关轨 + GitNexus 不稳 → 扫描会频繁 fail(这是诚实暴露,非回归)。
- **双轨开场景不回归**:GitNexus fail → 标红 + LLM 补,行为升级。
