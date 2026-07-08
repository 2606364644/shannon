# per-profile cost 定价 + 双引擎统一自算 + 币种/ token 明细设计

> 日期：2026-07-09
> 分支：feat/fork-py
> 状态：设计待 review
> 关联：[2026-06-29 openai 引擎成本核算](./2026-06-29-openai-engine-cost-accounting-design.md)（**本设计是其演进**，推翻其「不动 claude 引擎 / 统一美元 / token 不落盘」三条）、[2026-06-17 openai-agents 引擎设计](./2026-06-17-openai-agents-engine-design.md)
> 触发：用户希望「每个 profile 配置自己的 cost 价格标准（输入/输出/缓存各自单价）」，切 profile 即切一套定价。

---

## 0. 一句话结论

当前 cost 计算是**双引擎不对称**的：openai 引擎项目自算（`pricing.compute_cost_usd`，内置 GLM 价表 ¥→$），claude 引擎直接读 SDK 的 `total_cost_usd`（Anthropic 官方价）。这导致两个问题：(1) 定价是**全局**的（`GLM_PRICING_CNY` 硬编码 + 全局 `SHANNON_PRICING_OVERRIDE`），不随 profile 切；(2) `glm-anthropic` / `deepseek` 这类「走 anthropic 兼容端点、模型却是别家」的 profile，被 SDK 按 Anthropic 价算 → **算错**。本设计：(A) **claude 引擎也改成项目自算**（`_extract_cost` 用 `tokens × 价表`，不再读 `total_cost_usd`），双引擎殊途同归；(B) 价表 **per-profile 化**（JSON 文件 + profile 指向，复用现有 `SHANNON_PRICING_OVERRIDE` 机制）；(C) 价表带 **币种**（CNY/USD），cost 全程用 profile 本币、**展示跟币种走**（¥/$）；(D) 顺带把 **token 明细落盘 + 展示**（4 档 token，现在被 `AgentEndResult` 截断丢弃）。前置地，统一 `TokenUsage` 的 `input_tokens` 语义（两引擎当前不一致，是统一自算的前提）。

---

## 1. 背景

### 1.1 当前 cost 数据流（双引擎不对称）

| 层 | claude 引擎（`glm-anthropic` / `deepseek`） | openai 引擎（`glm-openai`） |
|---|---|---|
| token 提取 | `providers_anthropic._extract_tokens` ← `ResultMessage.usage`（4 档全有） | `openai_result_mapper._usage_from` ← `context_wrapper.usage`（cache_read=cached_tokens，cache_creation=0） |
| cost 计算 | `providers_anthropic._extract_cost` ← SDK 的 `total_cost_usd`（**项目不参与**） | `pricing.compute_cost_usd` ← 内置 `GLM_PRICING_CNY` ¥→$（**项目自算**） |
| 单价来源 | claude-agent-sdk 内置（Anthropic 官方价） | `pricing.py` 硬编码常量（占位值） |
| 未知模型 | N/A（SDK 自处理） | `0.0` + 去重 warning |
| 汇合点 | 两边都产出 `ClaudeRunResult{cost: float(USD), tokens: TokenUsage}`，下游无差别消费 | 同左 |

### 1.2 三个被本设计推翻的旧决策（2026-06-29 spec）

旧 spec（`2026-06-29-openai-engine-cost-accounting-design.md`）在当时语境下成立，本次因需求变化而演进：

| 旧决策 | 旧理由 | 本次推翻原因 |
|---|---|---|
| **不动 claude 引擎** | claude 已有 SDK 真实成本 | 用 GLM/DeepSeek 走 anthropic 兼容端点时，SDK 按 Anthropic 价算 → 算错；要 per-profile 定价就必须自算 |
| **统一美元，不引入双币种** | 简化、cost 已是美元展示 | 用户要求「填美元显示美元、填人民币显示人民币」，需引入币种 |
| **token 明细不落盘** | 字段齐备但无需展示 | 落盘 token 能反查单价是否配对、做 token 级审计；且本次本来要动 `AgentEndResult`/`metrics_tracker`/events 链，顺手接通边际成本低 |

### 1.3 token 语义不一致（统一自算的前提，易踩坑）

两引擎 `TokenUsage.input_tokens` 语义不同：

- **OpenAI 语义**：`input_tokens` = **总输入（含缓存命中部分）**，`cached_tokens` 是其子集。旧 spec §4.1 在 `compute_cost_usd` 内部做 `billable_input = input_tokens - cached_tokens`。
- **Claude（Anthropic）语义**：`input_tokens` = **常规输入（不含 cache）**，`cache_creation_input_tokens` / `cache_read_input_tokens` 是额外的，不包含在 `input_tokens` 内。

→ 若直接用同一个 `compute_cost` 公式套两引擎，openai 侧会把 cached 重复计费（既算进 input 又算 cache_read）。**必须先统一 `TokenUsage.input_tokens` 语义**（见 §4.3），这是双引擎统一自算的前置。

### 1.4 SDK 注入价不可行（已验证）

claude-agent-sdk v0.2.94 / Claude Code CLI v2.1.169：`ClaudeAgentOptions` 无 pricing 入参；CLI 无价目表开关；`total_cost_usd` 是 CLI 二进制内置硬编码 Anthropic 价算好后透传（`message_parser.py:257` 纯读取），Python 层零计算、无 hook 可拦截。**结论：要让 claude 引擎按自定义价算，只能在项目 provider 层自算（绕过 `total_cost_usd`），无法让 SDK 代算。** 详见 brainstorming 调研记录。

---

## 2. 方案选择

| 方案 | 做法 | 取舍 | 结论 |
|---|---|---|---|
| **A（选定）** | 两引擎都项目自算（claude `_extract_cost` 接入价表）；per-profile JSON 价表（4 档 + 币种）；展示跟币种；token 明细落盘+展示 | 双引擎统一、修掉算错、per-profile 生效、可审计；代价是用 Claude 官方模型的 profile 要自己填价 | ✅ 选定 |
| B | 先探 SDK 能否注入价目表 | 已验证：SDK/CLI 均不支持注入价 | ❌ 技术不可行（§1.4） |
| C | 只把 openai 引擎的全局 override 改 per-profile，claude 引擎维持 SDK 价 | 改动最小；但 claude 引擎 per-profile 做不了、算错依旧 | ❌ 不满足需求 |

**选 A**：是唯一能同时满足「per-profile 定价」「claude 引擎也算准」的路径，且顺带消除双引擎不对称、补上 token 审计。

---

## 3. 范围

| 项 | 处理 | 说明 |
|---|---|---|
| `agents/pricing.py` | **改** | `compute_cost_usd` → `compute_cost`（返回 `CostAmount{cost, currency}`）；价表 schema 加 `currency` + `cache_creation`；`normalize_model` 扩展 claude 名 |
| `agents/providers_anthropic.py` | **核改** | `_extract_cost` 改自算（`tokens × 价表`），不再读 `total_cost_usd`；`_extract_tokens` 归一 input 语义（§4.3） |
| `agents/openai_result_mapper.py` | **改** | `_usage_from` 归一 input 语义（`input_tokens = raw - cached`）；`map_run_result` 复用 `compute_cost`；warning 推广 |
| `agents/runner.py` | **改** | `ClaudeRunResult` 加 `cost_currency` |
| `agents/executor.py` + `models/audit.py` | **改** | `AgentMetrics` 带 `cost_currency`；`AgentEndResult` **补 `cost_currency` + 4 档 token 字段**（修复当前截断丢弃） |
| `orchestrator/metrics_tracker.py` | **改** | `session.json` metrics 写 `total_cost` + `cost_currency` + 4 档 token（per-agent + total + phase） |
| `display/rich_renderer.py` / `file_renderer.py` | **改** | 按币种显示 ¥/$；补 token 列 |
| `events.py` / `workflow_logger.py` | **改** | `AgentEvent`/`SummaryEvent` 带 `cost_currency` + token 字段 |
| `web/.../metrics_normalizer.py` + 前端 | **改** | 读 `cost_currency` + token；前端按币种渲染 + 显示 token |
| profile 示例（`.env.profiles.example/*`） | **改** | 每个 profile 加 `SHANNON_PRICING_OVERRIDE` 指向 + 配套 `.pricing.json` 示例 |
| 双轨 / LLM 轨 prompt / 确定性层 | **不触及** | 纯 provider/展示层 cost 核算，与双轨铁律无关 |
| `utils/billing.py`（spending-cap） | **不动** | 见 §4.8 不变量 |

---

## 4. 设计

### 4.1 价表 schema（per-profile JSON）

`.env.profiles/<name>.pricing.json`：
```json
{
  "currency": "CNY",
  "models": {
    "glm-5.2": {"input": 50.0, "output": 50.0, "cache_read": 12.5, "cache_creation": 0},
    "glm-4.6": {"input": 50.0, "output": 50.0, "cache_read": 12.5, "cache_creation": 0}
  }
}
```
- `currency`：`"CNY"` | `"USD"`，**整表统一币种**（一个 profile 一个 provider，币种天然统一）。
- 单价单位：**每百万 token 的本币金额**。
- 4 档：`input` / `output` / `cache_read` / `cache_creation`。`cache_creation` 对 openai 引擎恒 0（协议无此概念）；省略按 0。
- 模型名经 `normalize_model` 归一化后查表。
- 兼容旧 override schema（无 `currency`/`models` 包裹的 flat `{model: {input,output,cache_read}}`）：回落 `currency="CNY"`（保持旧 GLM 表语义），无 `cache_creation` 按 0。

### 4.2 profile 指向（复用现有机制）

`.env.profiles/<name>.env` 加一行：
```
SHANNON_PRICING_OVERRIDE=.env.profiles/<name>.pricing.json
```
`config/env_loader.py` 加载 profile 时 `override=True`，`SHANNON_PRICING_OVERRIDE` 天然 per-profile，**无需新机制**。未设时回落内置 `GLM_PRICING_CNY` + 全局 override（保持向后兼容）。

### 4.3 统一 TokenUsage 语义（前置，关键）

将 `TokenUsage.input_tokens` 统一为 **Anthropic 语义：常规输入（不含 cache）**：

- **openai `_usage_from`**：`input_tokens = max(raw_input_tokens - cached_tokens, 0)`（扣除缓存命中），`cache_read_input_tokens = cached_tokens`，`cache_creation_input_tokens = 0`。
- **claude `_extract_tokens`**：保持（已是不含 cache 的常规输入）。

统一后 `compute_cost` 公式对所有引擎一致（§4.4）。需核实无其他消费方依赖 openai 侧「input 含 cached」的原始语义（调研：token 明细此前不落盘不展示，仅用于算 cost，影响面可控）。

### 4.4 计费公式（4 档，统一）

```
cost_local = ( input_tokens        * P_input
             + cache_creation      * P_cache_creation
             + cache_read          * P_cache_read
             + output_tokens       * P_output ) / 1_000_000
```
- `cost_local` 单位 = 价表 `currency` 的本币（CNY → ¥，USD → $）。
- `input_tokens` 已是「常规未缓存输入」（§4.3），不重复计 cache。
- `cache_creation`（claude 写缓存，通常比 input 贵）单独按 `P_cache_creation` 计；openai 恒 0。
- `cache_read`（命中缓存，便宜）按 `P_cache_read`。
- 未知模型 → `CostAmount(0.0, currency)` + 去重 warning（两引擎一致）。

### 4.5 计算层改动

`pricing.py`：
- 新增 `@dataclass CostAmount: cost: float; currency: str`。
- `compute_cost_usd(model, usage) -> float` 改为 `compute_cost(model, usage) -> CostAmount`：读价表 → 按 §4.4 算本币 → 返回 `(cost, currency)`。**不再 /汇率**（单 session 用本币直达）。
- 价表加载：合并「内置 `GLM_PRICING_CNY`（回落 currency=CNY）∪ `SHANNON_PRICING_OVERRIDE` 文件」，override 同 key 以 override 为准；读取 override 文件的 `currency` 字段。
- `normalize_model` 扩展：支持 `claude-sonnet-*` / `claude-opus-*` / `claude-haiku-*` / `deepseek-*` 等别名归一（按 model family 折叠到价表 key）。

`providers_anthropic._extract_cost`（**核心改动**）：
```python
def _extract_cost(self, result_message, model) -> CostAmount:
    tokens = self._extract_tokens(result_message)       # 已归一 input 语义
    return compute_cost(model, tokens)                   # 不再读 total_cost_usd
```
SDK 的 `total_cost_usd` 彻底不读（`_extract_cost` 是它进入项目数据流的唯一通道，SDK 不回写，见 §1.4）。

`openai_result_mapper.map_run_result`：`cost=compute_cost_usd(...)` → `cost=compute_cost(...).cost`、currency 透传；复用同一公式（消除双引擎不对称）。

### 4.6 内部存储 schema（session.json）

```json
"metrics": {
  "total_cost": 0.0886,
  "cost_currency": "CNY",
  "total_input_tokens": 1234567,
  "total_output_tokens": 89012,
  "total_cache_read_tokens": 234567,
  "total_cache_creation_tokens": 0,
  "agents": {
    "recon": {"cost": 0.0295, "input_tokens": ..., "output_tokens": ..., ...},
    ...
  },
  "phases": {"recon": {"cost": ..., "input_tokens": ..., ...}, ...}
}
```
- `total_cost` + `cost_currency`：本币直达（不再强制美元）。
- per-agent / per-phase / total 三级都带 `cost`（本币）+ 4 档 token。
- **向后兼容**：`metrics_normalizer` 读旧 schema（`total_cost_usd`）时视作 `cost_currency="USD"`、`total_cost=total_cost_usd`、token 字段缺失按 0/未知。

### 4.7 展示层（跟币种 + token 列）

- **CLI `rich_renderer` / `file_renderer`**：summary 面板按 `cost_currency` 显示 `¥` 或 `$`（替换硬编码 `$`）；agent 表格补 token 列（input/output/cache）。
- **events.ndjson**（`AgentEvent`/`SummaryEvent`）：带 `cost_currency` + token 字段。
- **Web dashboard**：后端经 `metrics_normalizer` 归一新旧 schema（含 currency + token）；前端按 `cost_currency` 渲染符号 + 显示 token。
- 货币符号映射：`CNY→¥`、`USD→$`（其他币种按 ISO 4217 扩展，YAGNI 先这两种）。

### 4.8 fallback 与不变量

- **未知模型**（价表无此 model）：`CostAmount(0.0, currency)` + 去重 warning（模块级 `_warned_unknown_models: set`，两引擎共用机制）。**注意**：claude 引擎改自算后，若用 Claude 官方模型却没在 profile 价表配价，cost 会从原来的「SDK 准价」退化成 0——这是用户已确认接受的取舍（严格自算，不回落 SDK）。
- **汇率 `SHANNON_USD_CNY_RATE`**：单 session 不再使用（本币直达）。保留 env，仅文档化为「跨 session/跨币种聚合」预留（如 Web 总览加总多 session cost，本次不做）。
- **spending-cap（`utils/billing.py`）**：不动。`cost>0→False` 早退对两引擎一致生效（claude 自算出非零 cost 后与 openai 行为对齐）；真正 cost=0 的失败请求仍走文本兜底。
- **边缘**：override JSON 解析失败 → 忽略 override + warning + 用内置表；usage 为 None → 全 0 → `CostAmount(0.0, currency)`。

### 4.9 数据流（改后，统一）

```
LLM 返回 usage
  ├─[claude] ResultMessage.usage ── _extract_tokens(归一 input) ──┐
  └─[openai] context_wrapper.usage ─ _usage_from(归一 input) ─────┤
                                                                   ▼
                              pricing.compute_cost(model, tokens) → CostAmount(cost, currency)
                                  · normalize_model → 查 per-profile 价表（含 currency）
                                  · §4.4 四档公式 → 本币 cost
                                  · 未知模型 → (0.0, currency) + warning
                                                                   ▼
                ClaudeRunResult{ cost, cost_currency, tokens: TokenUsage }
                                                                   ▼
                executor → AgentMetrics{ cost, cost_currency, 4 档 token }
                                                                   ▼
                AgentEndResult{ cost, cost_currency, 4 档 token }   ← 修复当前截断
                                                                   ▼
                MetricsTracker → session.json{ total_cost, cost_currency, token 明细 }
                                                                   ▼
                DisplayDispatcher → Rich/File/events/Web（按币种 ¥/$ + token 列）
```

---

## 5. 配置 / env 清单

| env | 作用 | 默认 |
|---|---|---|
| `SHANNON_PRICING_OVERRIDE` | 指向 per-profile JSON 价表（含 `currency` + 4 档单价）；与内置 `GLM_PRICING_CNY` 合并，同 key 以 override 为准 | 未设 = 纯内置（CNY） |
| `SHANNON_USD_CNY_RATE` | （保留）跨 session/跨币种聚合预留；单 session 不再使用 | 7.2 |

profile 侧（dotenv）：每个 `.env.profiles/<name>.env` 加 `SHANNON_PRICING_OVERRIDE=<path>`；配套 `<name>.pricing.json` 示例随 profile example 提交。

---

## 6. 测试

- **`pricing` 单测**（扩 `test_pricing.py`）：
  - 4 档公式（含 cache_creation 非零的 claude 场景）；
  - 双币种：CNY 价表 → `CostAmount(cost, "CNY")`，USD 价表 → `"USD"`；
  - 未知模型 → `(0.0, currency)` + 去重 warning；
  - `normalize_model` 扩展（`claude-sonnet-4-5` / `deepseek-chat` 等）；
  - override JSON 含 `currency` 字段读取；旧 flat schema 兼容（回落 CNY）；
  - override 解析失败 → 忽略 + warning。
- **TokenUsage 语义统一单测**：openai `_usage_from` 的 `input_tokens = raw - cached`；claude `_extract_tokens` input 不含 cache。
- **claude 引擎自算单测**（扩对应 provider 测）：mock `ResultMessage.usage`，断言 `_extract_cost` 用 `tokens×价表` 算、**不再读 `total_cost_usd`**。
- **双引擎对齐测试**（扩 `test_dual_engine_alignment.py`）：同 tokens + 同价表 → 两引擎 cost 一致（锁定不对称消除）。
- **`AgentEndResult` / `metrics_tracker` 单测**：cost_currency + 4 档 token 正确落 session.json（per-agent/phase/total 三级）。
- **`metrics_normalizer` 兼容单测**：旧 schema（`total_cost_usd`、无 token）→ `cost_currency="USD"` + token 缺省。
- **展示层单测**：renderer 按 currency 显示 ¥/$；token 列渲染。
- **真机冒烟（人工）**：`scripts/validate_*_task_probe.py` 在 glm-anthropic / glm-openai 两 profile 各跑一次，确认 cost 非零、币种正确、token 明细落盘；记 memory。

---

## 7. 不做（YAGNI / follow-up）

- ❌ **跨 session / 跨币种 cost 聚合**（Web 总览加总多 session）：边界场景，单 session 已本币直达；需要时再启用 `SHANNON_USD_CNY_RATE` 折算。
- ❌ **实时 pricing API**：超时风险、与离线审计哲学冲突（旧 spec 方案 B 已否决）。
- ❌ **per-model 异币种**（同表混 CNY+USD）：整表统一币种已够，混币种 YAGNI。
- ❌ **reasoning 单独计价字段**：假设 reasoning 含在 output_tokens 内（旧 spec §4.1）；若实测 GLM 另行计价再补字段（结构预留）。
- ❌ **不改 `utils/billing.py`**：spending-cap 不变量保持（§4.8）。

---

## 8. 风险 / 开放问题

- **Claude 官方模型 profile 退化**：改自算后，用真 Claude 模型且没配价的 profile，cost 从 SDK 准价退化成 0。缓解：在示例 profile + 文档强调「用 Claude 官方模型需在价表配 Anthropic 官方价」；提供一份 Claude 官方价的参考价表片段。
- **openai `input_tokens` 语义变更影响面**：§4.3 把 openai 侧 input 改成「不含 cached」，需核实无其他消费方依赖原始「含 cached」语义（调研显示此前 token 不落盘、仅算 cost 用，影响可控；实现时 grep 确认）。
- **定价漂移**：per-profile JSON 价表会随厂商调价过时。缓解：价表独立文件、不改代码即可调；文档注明「数值需定期核对官网」。
- **cache 计费假设**：假设 claude cache_creation 比 input 贵、cache_read 比 input 便宜（Anthropic 口径）；openai 无 cache_creation。实现时按各厂商计费文档核对 4 档单价。
- **币种符号 / locale**：先只支持 ¥/$；未来扩 EUR 等按 ISO 4217。

---

## 9. 决策记录

- **为什么路线 A（两引擎自算）而非 B（SDK 注入价）**：§1.4 已验证 SDK/CLI 不支持注入价，B 技术不可行；A 是唯一满足需求的路径。
- **为什么严格自算（无价→0）而非回落 SDK**：用户拍板（接受 Claude 官方模型不配价退化成 0），换两引擎 fallback 一致 + 不依赖 SDK 价。
- **为什么展示跟币种走**：用户明确要求「填美元显示美元、填人民币显示人民币」（brainstorming 确认）。
- **为什么 token 明细纳入本次**：与 cost_currency 同一条改动链（`AgentEndResult`/`metrics_tracker`/events），顺手接通边际成本低，且有审计/校验单价独立价值；用户拍板「落盘+展示都纳入」。
- **为什么统一 TokenUsage input 语义**：两引擎 input 语义不一致（§1.3），是双引擎用同一计费公式的前提；统一为 Anthropic 语义（不含 cache）最干净。
