# AZ-2：攻击链置信度升级 — Design Spec

> **优化项**：AZ-2（越权分析优化路线图 `docs/gap/authz-optimization-roadmap.md` §4.1 / `docs/gap/authz-effect-gap-analysis.md` §4）
>
> **一份 Spec 关闭三个 gap**：AZ-2（攻击链 confidence 升级）+ route-analysis-binding RA-1（漏洞上下文增强缺失）+ RA-9（activity 冗余）。
>
> **日期**：2026-06-17
>
> **数据来源**：逐行代码核验（`route_chain_builder.py` / `attack_chain_builder.py` / `activities.py` / `workflows.py` / `orchestrator.py` / `queue_schemas.py`）+ 原始 `attack-chain-builder.ts:36-52`。

---

## 1. 问题陈述

### 1.1 现状

- `AttackChain.confidence` 设计了三档（`route_chain_builder.py:38`）：`confirmed` / `probable` / `theoretical`。
- 但**没有任何代码路径产出 `confirmed`**：
  - IDOR 链 confidence 硬编码 `probable`（`route_chain_builder.py:105`）。
  - XSS 链 confidence 为 `probable`/`theoretical`（`:78`）。
- `attack_chain_builder.build_attack_chains()`（40 行）仅转发 `build_attack_chains_from_analysis()`，**无漏洞上下文增强**（RA-1）。
- `run_attack_chain_assembly`（`activities.py:663`）与 `run_route_chain_building`（`:594`）**读同样的输入、跑同样的逻辑**，产出的 `attack_chains.json` 与 `route_chains.json` 内容完全相同（RA-9 冗余）。

### 1.2 后果

即使下游漏洞 agent 已在 `authz_exploitation_queue.json` 中判定某 IDOR 为 `high` + `externally_exploitable`，对应攻击链的 confidence **仍停留在 `probable`**——报告读者看到的链可信度被低估；两个 activity 产出重复，`run_attack_chain_assembly` 当前是空操作。

### 1.3 目标

让"静态高置信 + 外部可利用"的漏洞把对应攻击链 confidence 升级为 `confirmed`；让 `run_attack_chain_assembly` 与 `run_route_chain_building` 职责区分（RA-9 自然关闭）。

---

## 2. 目标与非目标

### 2.1 目标（in）

- `attack_chain_builder.build_attack_chains()` 接入"已确认漏洞端点"，对命中的 chain 升级 confidence 为 `confirmed`。
- `run_attack_chain_assembly` activity 读取 `{class}_exploitation_queue.json`，提取已确认漏洞传入 builder。
- `attack_chains.json`（含升级）与 `route_chains.json`（不升级）产出区分（关闭 RA-9）。
- 端点匹配的规范化（method + path 对齐，容忍格式差异）。

### 2.2 非目标（out）

- **不**建立 SharedKnowledge 等价机制（属 RA-3 / AZ-6）。
- **不**用动态 EXPLOITED 证据作为 confirmed 信号（时序不允许，见 §3.2；动态信号属未来方案 B/C）。
- **不**为非 inferred 端点补建攻击链（当前 chain 只从 `inferred_endpoints` 建，见 §6.1）。
- **不**改 `run_route_chain_building` 的行为（保持为基础链产出）。

---

## 3. 背景与约束

### 3.1 数据模型

- `AttackChain`（`route_chain_builder.py:29`）：含 `steps: list[AttackChainStep]`、`vuln_type`、`confidence`。
- `AttackChainStep`（`:18`）：含 `endpoint`（path）、`method`。
- `build_attack_chains_from_analysis()` 产两类 chain：
  - `xss-chain`（vuln_type=xss，来自 frontend `xss_chains`）
  - `idor-chain`（vuln_type=authz，来自 `inferred_endpoints` 中 `:id` 且有 `vulnerability_indicators` 的端点）

### 3.2 时序约束（决定性）

```
whitebox: VULN agents(产 {class}_exploitation_queue.json)
            → attack_chain_assembly(workflows.py:326)  ← 本 Spec 改动点
            → reporting
                    ↓ (combined orchestrator 串行)
blackbox: exploitation(产 {class}_exploitation_evidence.md) → reporting
```

`run_attack_chain_assembly` 在 **whitebox VULN 之后**运行；动态 `EXPLOITED` 证据是 **blackbox 阶段**才产出的。**因此本阶段只能用 VULN queue 的静态结论作为 confirmed 信号，读不到动态证据。** 这是采用方案 A（静态信号）的根本原因。

### 3.3 字段约束

`BaseVulnerability`（`queue_schemas.py:5`）统一提供 confirmed 判定字段：`externally_exploitable: bool` + `confidence: str`。**全 5 类一致** ✓。

但 endpoint 字段 **per-class 不统一**：

| 类 | endpoint 字段 | 格式 |
|---|---|---|
| authz | `endpoint` | `"GET /rest/basket/:id"`（method + path） |
| auth / ssrf | `source_endpoint` | method + path |
| injection / xss | `path` | 可能仅 path（无 method） |

升级匹配需 per-class 字段适配；injection/xss 的 `path` 可能缺 method，匹配时 method 维度降级为"仅比 path"。

### 3.4 升级价值分布

- **idor-chain（authz）**：chain step 来自 `inferred_endpoints`，authz vuln 的 `endpoint` 含 method + path —— **匹配最稳健，升级价值最高**。
- **xss-chain**：chain step 来自 frontend `xss_chains`（entry/storage/render endpoint），与 xss vuln 的 `path` 来源不同 —— **匹配键不稳健**，升级效果存疑，作为次要。

---

## 4. 设计（方案 A：whitebox 内升级·静态信号）

### 4.1 confirmed 信号定义

一条漏洞被视为"已确认"（可升级对应链）当且仅当：

```
externally_exploitable == true  AND  confidence == "high"
```

> `med`/`low` 不触发升级（避免 over-claim，如 `authz-effect-gap-analysis` 实测中 VULN-08 的 low-confidence 类型强转绕过未被验证）。

### 4.2 改动点 1：`attack_chain_builder.build_attack_chains()` 增加升级逻辑

**新签名**：

```python
async def build_attack_chains(
    framework_result: FrameworkAnalysisResult,
    frontend_result: FrontendAnalysisResult,
    logger: logging.Logger,
    confirmed_endpoints: set[str] | None = None,  # 新增：规范化的 "METHOD /path" 集合
) -> list[AttackChain]:
```

**升级逻辑**（参照原始 `attack-chain-builder.ts:36-52`）：

1. 调 `build_attack_chains_from_analysis()` 得基础 chains（保持不变）。
2. 若 `confirmed_endpoints` 非空，遍历每个 chain 的 `steps`：
   - 计算 `key = _normalize_endpoint(step.method, step.endpoint)`。
   - 若 `key in confirmed_endpoints` 且 `chain.confidence != "confirmed"`：
     - `chain.confidence = "confirmed"`。
     - （可选）在 `chain.description` 追注证据来源，见 §4.5。
3. 返回 chains。

> 升级是**单向提级**（probable/theoretical → confirmed），不会降级。confidence 不存在显式序，用集合判定"已是 confirmed 则跳过"。

### 4.3 改动点 2：端点规范化函数

新增 `_normalize_endpoint(method: str, path: str) -> str`（模块私有）：

- 输出统一格式：`"METHOD /normalized/path"`，method 大写。
- 去 trailing slash（`/api/Users/` → `/api/Users`），但保留根 `/`。
- 参数占位符归一：`:id` / `{id}` / `<id>` → 统一为 `:id`（以 chain 侧 `:id` 为准）。
- 容忍 vuln endpoint 字段里 method 与 path 之间多空格。

**用途**：把 chain step 的 `(method, endpoint)` 与 vuln 的 endpoint（各类字段）归一到同一 key 比较。

### 4.4 改动点 3：`run_attack_chain_assembly` 读取 queue 并提取 confirmed

在现有读 `framework_analysis.json` + `frontend_mapping.json` 之后，新增：

1. 读取 `deliverables / "{class}_exploitation_queue.json"`（`{class}` ∈ authz, xss；injection/ssrf/auth 暂不读，无对应 chain）。
2. 解析 `vulnerabilities[]`，对每条：
   - 判定 confirmed（§4.1）。
   - 提取 endpoint 字段（authz=`endpoint`，xss=`path`）。
   - 解析出 (method, path)：若字段含 method（`"GET /..."`）则拆分；若仅 path（xss 的 `path`），method 置为通配标记（如 `*`）。
   - 规范化后加入 `confirmed_endpoints` 集合。
3. 调 `build_attack_chains(..., confirmed_endpoints=confirmed_endpoints)`。
4. 写 `attack_chains.json`（含升级）。

> 文件缺失（如某类 queue 不存在）时优雅降级——`confirmed_endpoints` 为空集，行为退化到现状（不升级），不报错。

### 4.5 over-claim 缓解（报告措辞）

方案 A 的 `confirmed` 语义是"静态高置信 + 外部可利用"，**非动态已验证**。为避免误导报告读者：

- 升级为 `confirmed` 的 chain，在 `description` 末尾追注：`" (confidence upgraded: static high-confidence + externally exploitable; pending dynamic verification)"`。
- 或在 `AttackChain` 增加可选字段 `confirmation_basis: str`（默认空，升级时填 `"static"`），报告层据此措辞。**建议先采用 description 追注（不改模型），若报告层需要再扩字段。**

### 4.6 RA-9 处理

- `run_route_chain_building`（`:594`）**保持不变**：不接 vuln 数据，产出基础 `route_chains.json`（无 confirmed）。
- `run_attack_chain_assembly`（`:663`）**接入 vuln 数据**，产出升级后的 `attack_chains.json`（含 confirmed）。
- 二者职责区分：`route_chains.json` = 基础链，`attack_chains.json` = 经漏洞确认增强的链。**RA-9 自然关闭**，无需删 activity。

---

## 5. 数据流

```
authz_exploitation_queue.json ─┐
xss_exploitation_queue.json  ──┤  run_attack_chain_assembly
                               ├─► 提取 confirmed vuln endpoint
framework_analysis.json ───────┤    (externally_exploitable && confidence==high)
frontend_mapping.json ─────────┘                  │
                                                  ▼
                          build_attack_chains(..., confirmed_endpoints)
                                                  │
                          build_attack_chains_from_analysis()  → 基础 chains
                                                  │
                          遍历 steps，命中 confirmed_endpoints → 升级 confirmed
                                                  │
                                                  ▼
                                         attack_chains.json (含 confirmed)
```

`route_chains.json` 路径不变（run_route_chain_building 不读 queue，无升级）。

---

## 6. 范围限制（诚实标注）

### 6.1 升级只覆盖已有 chain

当前 chain 只从 `inferred_endpoints`（finale-rest 推断的 `:id` 端点）和 frontend `xss_chains` 构建。**非 inferred 的 authz vuln 没有对应 chain，不会因本次升级出现新链**——本次只升级已存在的链。补建链不在 AZ-2 范围（属后续，可能并入 AZ-5/AZ-6）。

### 6.2 confirmed 是静态语义

`confirmed` ≠ 动态 EXPLOITED。它是"静态分析判定 high 且外部可利用"。动态已验证的严格 confirmed 需方案 B/C（blackbox 后升级），超出 AZ-2 范围。§4.5 的措辞标注用于缓解误读。

### 6.3 XSS chain 升级效果存疑

xss-chain 的 step endpoint 来自 frontend 推断，xss vuln 的 `path` 来自分析，两者来源不同，规范化后命中概率低。**主效果在 idor-chain（authz）**；XSS 升级作为同机制的次要用例，不保证覆盖率。

---

## 7. 验收标准

1. **功能**：当 `authz_exploitation_queue.json` 含 `AUTHZ-VULN-01`（basket IDOR，`externally_exploitable=true`, `confidence=high`）时，`attack_chains.json` 中对应 `idor-chain` 的 `confidence == "confirmed"`。
2. **不降级**：`confidence` 非 high（med/low）的 vuln 不触发升级，链保持 `probable`。
3. **RA-9 区分**：`attack_chains.json` 至少有一条 `confirmed` 链时，`route_chains.json` 中对应链仍为 `probable`（两文件可区分）。
4. **降级安全**：queue 文件缺失时，`run_attack_chain_assembly` 不报错，`confirmed_endpoints` 为空，行为同现状。
5. **单测**（见 §8）全绿。

---

## 8. 测试策略

> 遵循项目惯例：跑改动相关子集，**不跑全套**（`MEMORY.md`：pytest 全量会 hang）。

新增/扩展测试（`packages/core/tests/`）：

| 测试 | 覆盖 |
|---|---|
| `test_attack_chain_builder.py::test_upgrade_to_confirmed` | confirmed_endpoints 命中 → 链升级 confirmed |
| `test_attack_chain_builder.py::test_no_upgrade_for_low_confidence` | med/low vuln 不升级 |
| `test_attack_chain_builder.py::test_normalize_endpoint` | `:id`/`{id}` 归一、trailing slash、method 大写、多空格 |
| `test_attack_chain_builder.py::test_already_confirmed_skipped` | 已 confirmed 不重复处理 |
| `test_attack_chain_builder.py::test_empty_confirmed_endpoints` | 空集时行为同现状（回归保护） |

可选：`packages/whitebox/tests/` 加一个 `run_attack_chain_assembly` 的集成测试（mock queue 文件，验证 confirmed_endpoints 提取 + per-class 字段适配）。

---

## 9. 工作量与里程碑

| 阶段 | 内容 | 估时 |
|---|---|---|
| M1 | `_normalize_endpoint` + `build_attack_chains` 升级逻辑 + 单测 | 1-2 天 |
| M2 | `run_attack_chain_assembly` 读 queue + per-class 字段适配 + 降级处理 | 1-2 天 |
| M3 | over-claim 措辞 + RA-9 验证 + 端到端跑 Juice Shop 验证 confirmed 链 | 1 天 |

**合计**：M（3-5 天）。

---

## 10. 风险与取舍

| 风险 | 取舍 |
|---|---|
| over-claim（静态 confirmed 被误读为动态已验证） | §4.5 措辞标注；未来方案 B/C 提供严格动态 confirmed |
| XSS chain 升级命中率低 | §6.3 诚实标注，主价值聚焦 IDOR；不为此扩范围 |
| endpoint 规范化边界（参数格式、大小写、前缀） | §4.3 规则 + 单测覆盖；以 chain 侧 `:id` 为基准 |
| queue 字段未来变动 | per-class 适配集中在 `run_attack_chain_assembly` 一处，便于维护 |

---

## 11. 交叉参考

- `docs/gap/authz-effect-gap-analysis.md` §4 / §7（AZ-2 差距判定）
- `docs/gap/authz-optimization-roadmap.md` §4.1（AZ-2 Spec 草案）
- `docs/gap/route-analysis-binding-gap-analysis.md` §4 / RA-1 / RA-9（代码级论证）
- 原始参照：`shannon/apps/worker/src/services/attack-chain-builder.ts:36-52`（升级逻辑模板）

---

## 12. 实现落点速查（供 writing-plans）

| 改动 | 文件 | 要点 |
|---|---|---|
| 升级逻辑 + 规范化 | `packages/core/src/shannon_core/services/attack_chain_builder.py` | 加 `confirmed_endpoints` 参数 + `_normalize_endpoint` |
| 读 queue + 提取 confirmed | `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:663`（`run_attack_chain_assembly`） | 读 authz/xss queue，per-class 字段适配，传 builder |
| 模型（可选扩字段） | `packages/core/src/shannon_core/services/route_chain_builder.py:29`（`AttackChain`） | 仅在采用 `confirmation_basis` 方案时改 |
| 单测 | `packages/core/tests/test_attack_chain_builder.py` | 见 §8 |
| **不改** | `activities.py:594`（`run_route_chain_building`） | 保持基础链产出 |
