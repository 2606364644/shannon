# openai 引擎成本核算（cost 不再恒 $0）设计

> 日期：2026-06-29
> 分支：feat/fork-py
> 状态：设计待 review
> 关联：[2026-06-17 openai-agents 引擎设计](./2026-06-17-openai-agents-engine-design.md)、[2026-06-29 openai structured output 解析韧性修复](./2026-06-29-openai-structured-output-validate-json-fix-design.md)（同属 openai provider 层）
> 触发：用户报告「当前的计费似乎不生效」——openai 引擎（glm-openai profile）下，审计报告 / 仪表板 `Total Cost` 恒为 `$0.0`。

---

## 0. 一句话结论

openai 引擎下 `openai_result_mapper.map_run_result` 把 `cost` **写死成 `0.0`**（`openai_result_mapper.py:62`，文件头注释明示「GLM/openai endpoint 不支持计费归集，cost 留 0.0，不假估算」）——这是**有意的设计立场、不是疏漏**，但代价是 openai 引擎成本完全不可观测。本设计在不破坏双引擎流程对称、不动 claude 引擎、不动 `cost_usd` 数据流的前提下，给 openai 引擎**补全 token 提取（含 cache）+ 内置可配置 GLM 价目表换算真实成本（¥→$）**，让 `Total Cost` 不再恒 0。未知模型仍守「不假估算」回落 0.0 + warning。顺带厘清一个被掩盖的不变量：spending-cap 文本检测对 cost>0 的引擎本就不生效（claude 一直如此），本次改动让 openai 与之对齐，**接受 + 文档化**。

---

## 1. 背景

### 1.1 成本数据流（两引擎不对称）

| 层 | 位置 | claude 引擎 | openai 引擎 |
|---|---|---|---|
| token 提取 | `providers_anthropic.py:373-387` / `openai_result_mapper.py:16-23` | input/output + **cache_creation/cache_read** 全有 | input/output 有，**cache 缺**，cache_creation 无概念 |
| cost 提取 | `providers_anthropic.py:389-393` / `openai_result_mapper.py:62` | SDK `total_cost_usd`（真实 $） | **写死 `0.0`** |
| 聚合 | `metrics_tracker.py`（`total_cost_usd` + phase/agent `cost_usd`） | 真实成本累加 | 全 0 |
| 展示 | `rich_renderer.py` `Total Cost: $X` | 有值 | **恒 $0.0** |

`AgentMetrics` 数据模型（`metrics.py:3-13`）已含 `input_tokens`/`output_tokens`/`cache_read_tokens`/`cache_creation_tokens`/`cost_usd` 全字段——**字段齐备，只是 openai 引擎没填**。

### 1.2 为什么恒 0（有意立场，非 bug）

`openai_result_mapper.py:11-13` 文件头注释：

> GLM/openai endpoint 不支持计费归集，cost 留 0.0（不假估算），以 provider 账单为准。此 0 值对 spending-cap 兜底无害：`utils/billing.is_spending_cap_behavior` 的 `cost>0→False` 早退逻辑意味着 cost=0 时继续走 text 关键词匹配（C1，已核验）。

即原作者是**刻意**不估算：openai-agents SDK 不像 claude-agent-sdk 那样给 `total_cost_usd`，GLM 端点也不返回成本，于是选择「留 0、以账单为准」。这在「不做假数字」上是对的，但让 openai 引擎的成本完全不可观测——用户报告的「计费不生效」即此。

### 1.3 spending-cap 不变量发现（被掩盖的事实）

`utils/billing.py:27-35` `is_spending_cap_behavior(turns, cost, text)`：

```python
if turns > 2: return False
if cost > 0:  return False        # ← 早退
for pattern in _SPENDING_CAP_PATTERNS:
    if pattern.search(text): return True
return False
```

`cost>0→False` 早退意味着：**任何 cost>0 的引擎都永远不会被判 spending-cap**。claude 引擎 cost 一直非 0（SDK 给真实成本），所以**这个文本检测对 claude 引擎其实从未生效**——它实际只对「cost=0 的引擎」有意义，而目前唯一 cost=0 的就是 openai 引擎。本次让 openai cost 变非 0，**等于关掉 spending-cap 检测的最后一个适用场景**。这是必须显式处理的不变量影响（见 §4.6）。

---

## 2. 方案选择

| 方案 | 做法 | 取舍 | 结论 |
|---|---|---|---|
| **A（选定）** | 内置可配置 GLM 价目表 + env/配置覆盖 + 可配汇率；未知模型回落 0.0+warning | 自包含、可维护、不依赖外部 API；定价/汇率需人工维护（可配置缓解） | ✅ 选定 |
| B | 实时拉 provider pricing/账单 API | 永远准 | ❌ 智谱未必有公开 pricing API；增外部依赖 + 超时风险（项目已被 GitNexus 超时教训）；与离线审计哲学冲突。YAGNI |
| C | GLM 单价硬编码进 mapper | 改动最小 | ❌ 不可配置、不可审计，调价要改代码 |

**选 A**：正好落在「让 cost 生效」与「保留不假估算的克制」之间——价目表可配可覆盖、未知模型不瞎算、汇率透明。

---

## 3. 范围

| 项 | 处理 | 说明 |
|---|---|---|
| `openai_result_mapper.py` | **改** | `_usage_from` 补 cache_read；`map_run_result` 的 `cost=0.0` → `compute_cost_usd(...)`；改写文件头注释 |
| 新增 `agents/pricing.py` | **新增** | 纯函数 + 内置 GLM 价目表 + 汇率 + env 覆盖 |
| `providers_openai.py` | **核改**（若有独立 cost 写死处） | 主路径经 `map_run_result`；若 `call()` 内另有 `cost=0.0` 写死，一并改（实现时核实） |
| claude 引擎（`providers_anthropic.py`） | **不动** | 已有真实成本，零改动 |
| `metrics.py` / `metrics_tracker.py` / 报告展示 | **不动** | 字段齐备，复用现有 `cost_usd` 聚合链 |
| `utils/billing.py` | **不动** | spending-cap 仍对真正 cost=0 的失败请求生效（见 §4.6） |
| 双轨 / LLM 轨 prompt / 确定性层 | **不触及** | 纯 provider 层成本核算，与双轨铁律无关 |

---

## 4. 设计

### 4.1 计费公式（易踩坑，单列）

OpenAI 语义：`input_tokens` 是**总输入（含命中缓存部分）**，`input_tokens_details.cached_tokens` 是其中命中 prompt cache 的子集（`agents/usage.py:103-119`，openai-agents SDK 已暴露）。故不能把 `input_tokens` 全按 input 价算（漏 cache 折扣），也不能把 `cached_tokens` 同时算进 input 又算 cache（重复计费）。正确拆分：

```
billable_input = input_tokens - cached_tokens     # 按 input 价
cache_hit      = cached_tokens                    # 按 cache_read 折价（更便宜）
output         = output_tokens                   # 按 output 价
cost_cny = (billable_input*P_in + cache_hit*P_cache + output*P_out) / 1_000_000
cost_usd = cost_cny / USD_CNY_RATE
```

- **reasoning_tokens**（`output_tokens_details.reasoning_tokens`，GLM-4.6 思维链）：按 OpenAI 语义**已包含在 `output_tokens` 内**，按 output 价计费，不单独加价、不重复。若实测发现 GLM 对思考 token 另行定价，pricing 按 model 补 `reasoning` 单价字段即可（结构预留）。
- **cache_creation**：openai 协议无此概念（自动缓存、无创建费），`TokenUsage.cache_creation_input_tokens` 填 `0`、不计费——与 claude 的显式 cache_creation（更贵）区分。

### 4.2 新增 `agents/pricing.py`（纯函数，无副作用）

- `GLM_PRICING_CNY: dict[str, dict]`：`{model: {input, output, cache_read}}`，单位 **¥ / 百万 token**。**数值实现时按智谱官网核对填入，spec 不写假数值**。
- `USD_CNY_RATE`：默认常量（示例 `7.2`，实现时按当时汇率），可被 env `SHANNON_USD_CNY_RATE` 覆盖。
- `_normalize_model(name) -> str`：模型名归一化——去日期/快照后缀、大小写归一、别名映射（处理 `glm-4.6` / `GLM-4.6` / `glm-4.6-xxx` 等变体指向同一价目）。
- `compute_cost_usd(model: str, usage: TokenUsage) -> float`：归一化 model → 查价目表（合并 `SHANNON_PRICING_OVERRIDE` 覆盖）→ 按 §4.1 公式算 ¥ → ÷ 汇率 → $。未知 model 或 usage 为 None → `0.0`。
- 价目表按 `provider/model` 组织，未来扩其他 openai-compatible provider 时可演进出 `pricing/` 包（YAGNI，先单文件）。

### 4.3 `openai_result_mapper.py` 改动

- `_usage_from`（line 16-23）：补
  ```python
  cache_read_input_tokens=getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0,
  cache_creation_input_tokens=0,  # openai 协议无此概念
  ```
- `map_run_result`（line 62）：`cost=0.0` → `cost=compute_cost_usd(model, tokens)`。
- **未知 model**：`compute_cost_usd` 返回 0.0；mapper 在「model 非空且 cost==0.0」时发**去重 warning**——用模块级 `_warned_unknown_models: set[str]` 记已报过的归一化 model 名，同 model 进程内只 warning 一次（避免刷屏）。这是 mapper 唯一的一点模块级状态，仅为去重；不抛异常、不中断审计。
- **改写文件头注释**（line 11-13）：原论证「cost=0 对 spending-cap 无害」已过时——改为说明「cost 由 pricing 换算；未知 model 回落 0.0；spending-cap 影响见 §4.6 / spec」。

### 4.4 数据流（复用，不变）

```
openai SDK Usage.input_tokens_details.cached_tokens
  → mapper._usage_from           (提取 cache_read；cache_creation=0)
  → pricing.compute_cost_usd     (¥ → $)
  → ClaudeRunResult.cost
  → Executor → MetricsTracker.total_cost_usd
  → 报告 Total Cost              (不再恒 $0)
```

### 4.5 边缘 / fallback

- 未知 model → cost `0.0` + 去重 warning。
- 汇率非法（≤0 / 非数）→ 落回默认常量 + warning，不崩。
- usage 为 None / 字段缺失 → 全 0 → cost `0.0`，沿用现有 `TokenUsage()` 兜底。
- `SHANNON_PRICING_OVERRIDE` JSON 解析失败 → 忽略覆盖 + warning，用内置价目表。

### 4.6 spending-cap 不变量处理（接受 + 文档化）

- 改动后 openai cost 非零 → `is_spending_cap_behavior` 的 `cost>0→False` 早退使该检测对 openai 也失效，**与 claude 引擎行为对齐**（claude 一直如此）。
- **决策：接受，不改 `billing.py`**。理由：
  1. 它本就是弱兜底（靠错误文本猜），非核心能力；
  2. claude 引擎长期无它稳定运行，证明非必需；
  3. 真正的限额检测应靠**结构化错误码**（`executor.py` 已有 `api_error_status` 处理 402/429 等），不靠 cost 猜；
  4. 边缘情况（GLM 余额不足且部分计费 → cost>0 漏判）概率低，且原 claude 引擎同样漏判，不构成回归。
- `utils/billing.py` 保持不动——它仍对「真正 cost=0 的失败请求」（如请求直接被拒、未产生 token）生效。

---

## 5. 配置 / env 清单

| env | 作用 | 默认 |
|---|---|---|
| `SHANNON_USD_CNY_RATE` | 覆盖 ¥→$ 汇率 | 内置常量（如 7.2） |
| `SHANNON_PRICING_OVERRIDE` | 指向 JSON 价目表，与内置 `GLM_PRICING_CNY` 合并；**同 key 以 override 为准**（调价/加模型不改代码） | 未设 = 纯内置 |

env 前缀沿用项目惯例（`SHANNON_AI_PROVIDER` / `SHANNON_LLM_TRACK_ENABLED` 等）。两项均**可选**，未设走内置默认。

---

## 6. 测试

- **`pricing` 单测**（新增 `test_pricing.py`）：
  - 已知 model + token → 预期 $（**验证 §4.1 cache 拆分折价**：含 cached_tokens 时成本低于全按 input 价）；
  - 未知 model → `0.0`；
  - `SHANNON_USD_CNY_RATE` 覆盖生效；
  - 模型名归一化（`GLM-4.6` / `glm-4.6-snapshot` → 同价）；
  - 汇率非法 → 落回默认；
  - `SHANNON_PRICING_OVERRIDE` 合并 + 解析失败忽略。
- **mapper 测**（扩 `test_openai_result_mapper.py`）：
  - `_usage_from` 提取 `cache_read_input_tokens=cached_tokens` 正确、`cache_creation=0`；
  - `map_run_result` cost 非零且与 `compute_cost_usd` 一致；
  - 未知 model → cost `0.0` + warning（去重）。
- **不变量回归**（扩 `test_billing.py`）：`is_spending_cap_behavior(turns≤2, cost>0, spending_cap_text)` 仍 `False`——锁住现有行为不被误改。
- **真机冒烟（人工）**：`scripts/validate_openai_task_probe.py` 跑一次，确认结果 `cost=` 字段非 0；记 memory。

---

## 7. 不做（YAGNI）

- ❌ 不动 claude 引擎（已有真实成本）。
- ❌ 不改 `cost_usd` 字段语义 / 不引入 `cost_cny` 双币种（统一美元，§货币决策）。
- ❌ 不做实时 pricing API（方案 B，超时风险）。
- ❌ 不为 spending-cap 边缘情况改 `billing.py`（§4.6 接受）。
- ❌ 不预置其他 provider 价目表（先 GLM，结构预留扩展）。

---

## 8. 风险 / 开放问题

- **定价漂移**：内置价目表会随智谱调价过时。缓解：`SHANNON_PRICING_OVERRIDE` 让用户不改代码更新；文档注明「数值需定期核对官网」。
- **reasoning 计费假设**：§4.1 假设 reasoning 含在 output_tokens 内、按 output 价。若 GLM 实际对思考 token 另行计价，需补 `reasoning` 单价字段——实现时核对智谱计费文档确认。
- **cache 计费假设**：假设 GLM 对 prompt cache 命中按折价（`cache_read` 单价 < `input` 单价）。若 GLM 不折扣缓存或计费口径不同，实现时按官网调整 `cache_read` 单价（结构已支持设为与 input 同价）。
- **`providers_openai.py` 是否另有 cost 写死处**：spec 以 `map_run_result:62` 为主改动点；若 `call()` 内还有独立 `cost=0.0`，实现时一并改（§3 已列）。
