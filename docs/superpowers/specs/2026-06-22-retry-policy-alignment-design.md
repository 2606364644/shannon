# Retry Policy 对齐 TS 设计

**日期**: 2026-06-22
**状态**: Draft → 待 review → 转 writing-plans
**分支**: feat/fork-py
**相关**: `2026-06-22-glm-529-retry-resilience-design.md`(其 retry policy 部分已收窄撤回,并入本 spec 独立处理)

---

## 1. 背景

TS 原版(shannon)用**单一 proxyActivities 模型**给所有 agent 统一挂 retry policy;PY 迁移时在多处 `execute_activity` 调用点**漏配 `retry_policy=`**,导致这些 activity 落到 Temporal server 默认(≈ `maximum_attempts` 不设限 + 短 `initial_interval`)——持续过载时会近 1s 间隔疯狂重试 + 无限空转,比 pre-recon 已配的 5min 更危险。

本 spec 的目标:**让 PY 的 retry policy 行为与 TS 对齐**,并固化为防回归的锚点。

> 注:这是"补漏配 + 统一来源"的修复 spec,**不**是 529/429 错误分类优化(那个见 529 spec,本 spec 不动错误分类逻辑)。

---

## 2. 现状核实(已读码确认)

### 2.1 TS 侧 — `shannon/apps/worker/src/temporal/workflows.ts`

- **单 proxy 模型**:`selectActivityProxy()` 返回一个 proxy,所有 agent(pre-recon/recon/vuln/exploit/report)**共享同一个 `PRODUCTION_RETRY`**。无按 agent 区分。
- `PRODUCTION_RETRY`(L66-81):`initialInterval=5min / maximumInterval=30min / backoff=2 / maximumAttempts=50`,`nonRetryableErrorTypes` 8 类(**不含 529/429**)。附带 `startToCloseTimeout=2h / heartbeatTimeout=60min`。
- 5 个 tier:PRODUCTION(默认全 agent)/ TESTING(`pipelineTestingMode`)/ SUBSCRIPTION(`retry_preset==='subscription'`,100/6h)/ PREFLIGHT / AUTH_VALIDATION(后两者独立 proxy,非 agent)。
- **pre-recon 与其他 agent 完全同源** —— pre-recon 用 5min 是 TS 原版设计,不是 PY 误用。

### 2.2 PY 侧

- `PRODUCTION_RETRY` 常量**已存在**(`packages/core/src/shannon_core/models/retry.py:28-34`),数值与 TS **完全一致**;已有 5 个 tier + `get_retry_policy(mode)` 工厂(L54-64,按 production/testing/subscription 选)。
- SDK:`temporalio==1.27.2`,retry 通过 `workflow.execute_activity(..., retry_policy=...)` 挂载;不传则吃 server 默认。
- **真正的火 = whitebox RECON agent**(`workflows.py:219`)—— 全 whitebox 唯一一个无 retry 保护的 LLM agent 调用。
- **blackbox 基本配齐**:靠 `get_retry_policy(mode)` 动态取值赋给变量,recon/exploit/report agent 都传了;仅 `assemble_report`/`finalize_report` + log marker 漏配。
- **whitebox 大面积漏配**(详见 §5 清单)。
- **whitebox vuln agent 用的是内联保守 policy**(`maximum_attempts=3 / 30s / max 5min`,L285-291),不是 `PRODUCTION_RETRY` —— 另一处需要决策的分歧。

### 2.3 vuln per-vt fan-out 结构(决策关键,`workflows.py:276-312`)

vuln 是**并发 fan-out**:`asyncio.gather(..., return_exceptions=True)` + `Semaphore(max_concurrent)`。

- `return_exceptions=True` → 卡住的 vt **不会崩 phase**,最终变 `failed_agents`。
- 但 `bounded(coro)` 跨重试持有信号量 → 持续重试的 vt **一直占一个并发槽**。
- `start_to_close_timeout=2h`(单次 attempt)→ vuln phase 健康情况下本就小时级。
- NON_RETRYABLE 错误两边都快速失败;差异只体现在**可重试的瞬时错误**。

**结论**:换 PRODUCTION_RETRY(50/5min)会让一个持续撞可重试错误的 vt 把 phase 拖到最坏 ~24h(50 次 backoff:5+10+20+30×47≈1445min)。这是 per-vt fan-out 下不可接受的尾部。→ vuln 需要独立的有界档(见 §4.2)。

---

## 3. 设计决策(已确认)

| # | 决策 | 定论 |
|---|---|---|
| 1 | 主方向 | **纯对齐 TS**:漏配点补现成 `PRODUCTION_RETRY`(常量已存在且与 TS 一致),行为可预测、零新设计风险。**不**做 529/429 区分优化(那是另一个 spec)。 |
| 2 | vuln | 新增 **`VULN_RETRY`** 有界中间档(~`attempts=5 / 1min / 5min`,最坏 ~12min),撑过常见瞬时抖动又封顶 fan-out 停滞。spec 记为**有意分歧**。 |
| 3 | 范围 | **全清扫**:LLM agent + 确定性 activity → standard;vuln → vuln;~14 个 log marker → 短 policy;whitebox + blackbox 都做。 |
| 4 | 落地方式 | **集中 helper**(`retry_for(category, mode)`),所有调用点统一走它,建立单一 category→policy 映射源(用户选定方案 2)。 |

---

## 4. 设计

### 4.1 `retry.py` 改动 — `packages/core/src/shannon_core/models/retry.py`

新增 `VULN_RETRY` 常量 + category helper:

```python
VULN_RETRY = RetryPolicy(
    maximum_attempts=5,
    initial_interval=timedelta(minutes=1),
    maximum_interval=timedelta(minutes=5),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE,
)  # 有意分歧于 TS PRODUCTION_RETRY:per-vt fan-out 下封顶 ~12min,见本 spec §2.3

Category = Literal["standard", "vuln", "log", "preflight", "auth-validation"]

def retry_for(category: Category, mode: str | None = None) -> RetryPolicy:
    """按 activity 类别选 retry policy。

    - standard: LLM agent + 确定性处理。委托 get_retry_policy(mode) 保留 mode 感知
      (testing/subscription);不传 mode 默认 production。
    - vuln:     per-vt vuln agent,有界 VULN_RETRY。
    - log:      phase log marker(10s 写),短 policy。
    - preflight / auth-validation: 现有短 tier。
    """
    if category == "standard":        return get_retry_policy(mode)
    if category == "vuln":            return VULN_RETRY
    if category == "log":             return PREFLIGHT_RETRY
    if category == "preflight":       return PREFLIGHT_RETRY
    if category == "auth-validation": return AUTH_VALIDATION_RETRY
    raise ValueError(f"unknown activity category: {category!r}")
```

> 设计要点:`get_retry_policy(mode)` 是**按部署 mode** 选 tier(正交轴),`retry_for` 的 `standard` **委托**给它,不是替代 —— 保留 blackbox 既有的 mode 感知。

### 4.2 分类法(单一映射源)

| category | policy | 覆盖 | mode 感知 |
|---|---|---|---|
| `standard` | PRODUCTION / TESTING / SUBSCRIPTION | 所有 LLM agent + 确定性处理 activity | ✓ |
| `vuln` | VULN_RETRY(5 / 1min / 5min) | vuln agent(per-vt) | ✗ |
| `log` | PREFLIGHT_RETRY(3 / 10s) | phase log marker(10s 写) | ✗ |
| `preflight` | PREFLIGHT_RETRY | preflight / credential check | ✗ |
| `auth-validation` | AUTH_VALIDATION_RETRY | auth validation | ✗ |

### 4.3 实现方式:逐调用点走 helper

每个 `execute_activity` 调用 `retry_policy=retry_for(<category>, mode?)`。所有调用点统一来源,改 policy 只动 `retry.py`。

---

## 5. 迁移清单

### 5.1 白盒 — `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`

| activity | category | 备注 |
|---|---|---|
| `run_preflight` / `run_credential_check` | preflight | 原已配,迁移(零行为变化) |
| `run_auth_validation` | auth-validation | 原已配,迁移 |
| `run_code_index` / `run_agent(PRE_RECON)` | standard | PRE_RECON 原已配(迁移);code_index **原裸奔→补配** |
| `run_merge_sink_reports` / `run_entry_point_fusion` / `run_save_adjudication` / `run_framework_analysis` / `run_frontend_mapping` / `run_route_chain_building` | standard | **原裸奔→补配**(pre-recon 后处理 6 个确定性 activity) |
| `run_agent(RECON)` | standard | **🔥 原裸奔→补配(最大的火)** |
| `run_risk_scoring` / `run_render_dataflow_hints` | standard | **原裸奔→补配** |
| `run_vuln_agent` | vuln | **原内联 3/30s → VULN_RETRY(行为变化,有意)** |
| `run_attack_chain_assembly` / `render_findings` | standard | **原裸奔→补配** |
| 所有 `log_phase_start/complete`(~14 处) | log | **原裸奔→补配** |

### 5.2 黑盒 — `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`

黑盒现有 `retry_policy = get_retry_policy(mode)` 变量改成 `retry_for("standard", mode)`(行为等价,纯迁移求一致);漏配点补上:

| activity | category | 备注 |
|---|---|---|
| `run_blackbox_preflight` | preflight | 原已配,迁移 |
| `run_blackbox_auth_validation` | auth-validation | 原已配,迁移 |
| `run_recon` / `run_exploit_agent` / `run_report_agent` | standard | 原已配,迁移 |
| `assemble_report` / `finalize_report` | standard | **原裸奔→补配** |
| 所有 `log_phase_start/complete` | log | **原裸奔→补配** |

---

## 6. 行为变化界定(精确)

- **真正的行为变化**只有两类:
  1. 所有原裸奔点:Temporal 默认(≈无限 / 1s)→ 各自 tier(standard/vuln/log)。
  2. vuln:内联 3/30s → VULN_RETRY(5/1min/5min)。
- **纯迁移、零行为变化**:pre-recon、preflight、auth-validation、黑盒各 agent —— policy 值不变,只改走 helper。
- 现有 394 单元测试应全绿(迁移不改语义);若存在断言 vuln 旧 policy 数值的测试,需同步更新。

---

## 7. 防回归锚点测试(spec 的长期价值)

1. **AST 锚点测试**:解析两个 `workflows.py`,断言**每个 `execute_activity` 调用都带 `retry_policy=` kwarg**。以后谁加新 activity 漏配,CI 即红。这是本 spec 最大的长期收益。
2. **category 校验测试**:对 `retry_for` 用到的每个 category 调一次,断言不抛 `ValueError`(防 category 字符串拼错)。
3. 现有白盒/黑盒 workflow 单测全绿(回归基线)。

> 测试只跑改动相关子集,别跑全包(memory: pytest 全量会 hang 在 Temporal/网络慢测试)。

---

## 8. 非目标(out of scope)

- **529 / 429 错误分类优化** —— 不动 `non_retryable_error_types`、不加 billing fail-fast。属 `2026-06-22-glm-529-retry-resilience-design.md` 后续工作。
- **whitebox 引入 mode 感知** —— whitebox 目前 retry 硬编码 production;`retry_for("standard")` 不传 mode 时默认 production,**保持现状**,不在此引入 testing/subscription 支持。
- **重构成 TS proxy 模型** —— temporalio Python 无 `proxyActivities` 等价物,不手搓 wrapper(过度设计)。
- **log marker 单独命名 tier(LOG_RETRY)** —— 复用 PREFLIGHT_RETRY 即可,语义够用。

---

## 9. 风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| VULN_RETRY 数值判断点 | 5/1min/5min(最坏 ~12min)是权衡值,非推导值 | spec 记为决策,实现时若 review 有更优数值可调,改 retry.py 一处即可(集中来源的价值) |
| 迁移触碰已工作代码 | pre-recon/blackbox 等改走 helper | policy 值不变;AST 锚点 + 现有单测兜底 |
| helper 名 / category 标签 bikeshed | `retry_for` / `standard` 等命名 | 实现时定,不影响行为 |
| log marker 用 5min backoff 过度 | 已规避:log 走 PREFLIGHT_RETRY(3/10s)非 PRODUCTION | — |

---

## 10. 验收标准

- [ ] `retry.py` 新增 `VULN_RETRY` + `retry_for(category, mode)`(+ `Category` Literal)。
- [ ] 白盒 + 黑盒所有 `execute_activity` 均带 `retry_policy=retry_for(...)`。
- [ ] AST 锚点测试存在且通过。
- [ ] category 校验测试通过。
- [ ] 现有白盒/黑盒 workflow 相关单测全绿。
- [ ] vuln policy 变化若有旧值断言已同步。
- [ ] 人工冒烟:真仓库跑一次 whitebox start,确认无裸奔 activity、retry 行为符合 tier(待 merge 前验证,与其它 spec 冒烟同批)。

---

## 11. 后续(本 spec 不做)

- 529/429 错误分类 → billing fail-fast(独立 spec)。
- whitebox mode 感知(若需要 testing/subscription 白盒)。
- 将 `non_retryable_error_types` 与 529 display 分类对齐审计(跨 spec)。
