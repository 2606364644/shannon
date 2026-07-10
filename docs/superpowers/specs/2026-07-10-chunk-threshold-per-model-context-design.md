# chunk token threshold 按模型 context 自适应 — 设计

> spec 2026-07-10。状态：设计稿（待 writing-plans）。
> 关联：[[sink-source-file-level-aggregation]] 的后续调优（文件级聚合把 569 函数砍到 259 chunk，但 threshold 未跟进校准，仍撞 timeout）。

## 1. 背景与问题

真机 `kol_mapping_service`（Go 大仓）`run_code_index` activity 反复超时重试（attempt 1 `20m5s` 撞 `CODE_INDEX_ACTIVITY_TIMEOUT=20min` → `CODE_INDEX_RETRY` max 3 重试 → 从 `1/259` 重扫）。根因之一是 **sink/source LLM 补召回的 chunk 切分过细**：

- `CHUNK_TOKEN_THRESHOLD = 12_000`（`llm_concurrency.py:37`）硬编码，对当前模型 `glm-5.2`（1M context，`[1m]`）只占 **1.2%**。
- 文件级聚合把 569 函数砍到 259 chunk，**只减 55%**——潜力没榨干，因为 threshold 没跟着 context 校准。
- `_estimate_tokens = len//4`（`llm_concurrency.py:42`）对中文注释**严重低估 4-8x**（BPE 下中文常 1~2 token/char，`//4` 算成 0.25 token/char），违背 chunking 的「宁可高估防 prompt 爆 context」目标。

259 chunk ×（prompt+推理+网络）累加 ≫ 20min，幂等超时被重试放大（`retry.py` 的 `CODE_INDEX_RETRY` 注释已警告过此坑）。

## 2. 目标 / 非目标

**目标**
- chunk token threshold 按当前模型 context 自适应：默认/未知 128K，`glm-5.2` 走 1M，并可 per-profile 覆盖。
- threshold 从 context 安全派生（留 output + system prompt + 估算误差余量），结构上防 prompt 爆 context。
- token 估算改为 CJK 加权启发式，零依赖、O(n)、偏保守。
- 预期：`glm-5.2` 下 threshold 12K → 750K，kol 259 chunk → 个位数~十几个 chunk，调用次数降 ~30x，不再撞 20min timeout。

**非目标**
- 不追求计费级精确 token 计数（计费仍走真实 `usage`，本估算仅用于 chunking 装箱）。
- 不引入 tokenizer 依赖（tiktoken/GLM 官方 tokenizer）——chunking 防 context 爆用启发式足够。
- 不改 taint analyzer（`analyze_taint_llm` 不走 `chunk_items_by_file`，per-function 调用，不受影响）。
- 不动 LLM 轨 prompt、不引入确定性→LLM 耦合（守双轨铁律，见 §7）。

## 3. 设计

三模块，全部在 GitNexus 轨（`shannon_core.code_index`）内。

### 模块 1：model context 配置层（新增）

新文件 `packages/core/src/shannon_core/agents/model_caps.py`（与 `pricing.py` 并列，职责=模型能力元数据；`pricing.py` 是计费，语义分离）：

```python
# 内置已知模型真实 context window（待官网核对，见 §10）
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "glm-5.2": 1_000_000,
    "glm-4.5-air": 128_000,
}
DEFAULT_CONTEXT_WINDOW = 128_000          # 未知模型保守回落（= 用户「默认 128K」）
CHUNK_RESERVE_RATIO = 0.75                # 留 25% 给 output + system prompt + 估算误差

def get_model_context_window(model: str | None) -> int:
    """normalize_model(复用 pricing) + 内置表 + override JSON，未知 → DEFAULT。"""

def get_chunk_token_threshold(model: str | None) -> int:
    """= int(get_model_context_window(model) * CHUNK_RESERVE_RATIO)。"""
```

- `normalize_model` 直接复用 `pricing.py`（`glm-5.2[1m]` → `glm-5.2`），单一归一化来源。
- override 加载复用 `pricing._load_override` 同款模式，但读独立 env：

```python
def _load_context_override() -> dict[str, int]:
    path = os.environ.get("SHANNON_MODEL_CONTEXT_OVERRIDE")  # 指向 JSON {"models": {model: ctx}}
    ...  # 同 pricing._load_override 的容错（解析失败/顶层非 object → {} + warning）
```

### 模块 2：改进 token 估算（改 `llm_concurrency._estimate_tokens`）

```python
import math, re
_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")  # 中日韩

def _estimate_tokens(text: str) -> int:
    """CJK 字符 × 1.5 + 其余 / 4，向上取整。比 len//4 准 2-3x（中文不再被低估），偏保守。"""
    cjk = len(_CJK_RE.findall(text))
    return math.ceil(cjk * 1.5 + (len(text) - cjk) / 4)
```

- 仍在 `llm_concurrency.py`，仍只用于 `chunk_items_by_file` 装箱，**不进 `pricing` 计费**。
- CJK 系数 1.5 取中位（GLM 中文优化 ~1、OpenAI BPE ~2；偏保守防低估）。

### 模块 3：chunking 接 context（改 sink/source discovery + `chunk_items_by_file`）

**model 获取（关键，防 tier 错配）**：gitnexus 轨 LLM 固定用 `model_tier="medium"`（`activities.py:761`），而 `SHANNON_MODEL` env 是 large/default tier。**discovery 不裸读 env**，改由 activity 层 resolve medium tier 实际 model 名后传参：

- `_make_gitnexus_llm_client`（`activities.py:751`）构造时，用 provider 的 tier 解析逻辑 resolve 出 medium model 名（提取公共函数 `resolve_tier_model("medium")`，复用 `providers._get_model` 优先级：tier env > global env > `DEFAULT_MODELS`）。
- 该 model 名随 `llm_client` 一起传入 `build_code_index_with_gitnexus` → `discover_sinks_llm` / `discover_sources_llm`（两函数各增 `model: str | None = None` 参数）。

**threshold 接线**：
- 删代码常量 `CHUNK_TOKEN_THRESHOLD = 12_000`（`llm_concurrency.py:37`）。注意：这与 hard override env `SHANNON_CHUNK_TOKEN_THRESHOLD` 是**两回事**——env 保留（见 §4 优先级链最高档）；删的只是代码里的 12_000 默认常量。
- `discover_sinks_llm` / `discover_sources_llm` 内：`token_threshold = get_chunk_token_threshold(model)`，传给 `chunk_items_by_file`。
- `chunk_items_by_file` 的 `token_threshold` 默认参数从硬编码 `CHUNK_TOKEN_THRESHOLD` 改为必填或 `None`（由调用方显式传），避免误用旧默认。

**taint analyzer 不动**：`analyze_taint_llm` 不走 `chunk_items_by_file`，本改动不触及。

## 4. 配置优先级链

`get_chunk_token_threshold(model)` 最终值，高 → 低：

1. **`SHANNON_CHUNK_TOKEN_THRESHOLD`**（hard override，调试/止血用）：设了直接取此值，跳过派生。
2. **派生** = `get_model_context_window(model) × 0.75`，其中 context window 解析优先级：
   a. `SHANNON_MODEL_CONTEXT_OVERRIDE` JSON（经 env_loader 天然 `.profiles` 覆盖 `.env` —— **profile > .env**）
   b. 内置 `MODEL_CONTEXT_WINDOWS` 表
   c. `DEFAULT_CONTEXT_WINDOW = 128_000`（未知模型）

env_loader 的 profile 覆盖机制保证：`.env` 里设默认 override JSON 路径，`.env.profiles/<profile>` 里覆盖成 profile 专属路径，切 profile 即切 context 表——满足「默认写 .env、profile 写模型独立、profile 优先」。

## 5. 文件改动清单

| 文件 | 改动 |
|---|---|
| `agents/model_caps.py`（新） | context 表 + override 加载 + `get_model_context_window` / `get_chunk_token_threshold` |
| `agents/providers.py` | 提取 `resolve_tier_model(tier)` 公共函数（复用两引擎 `_get_model` 优先级：tier env > global env > `DEFAULT_MODELS`；`providers.py:262` 已有 `SHANNON_MODEL` env 读取，天然落点） |
| `code_index/llm_concurrency.py` | `_estimate_tokens` 改 CJK 加权；删 `CHUNK_TOKEN_THRESHOLD` 常量；`chunk_items_by_file` 的 `token_threshold` 改显式传入 |
| `code_index/sink_discovery_llm.py` | 增 `model` 参数；`token_threshold = get_chunk_token_threshold(model)` |
| `code_index/source_discovery_llm.py` | 同上 |
| `whitebox/pipeline/activities.py` | `_make_gitnexus_llm_client` resolve medium model 名，经 `build_code_index_with_gitnexus` 传入 |
| `code_index/__init__.py`（`build_code_index_with_gitnexus`） | 透传 `model` 到两个 discovery 函数 |
| `.env.profiles.example/*` | 示例：`SHANNON_MODEL_CONTEXT_OVERRIDE` + `SHANNON_CHUNK_TOKEN_THRESHOLD` 注释说明 |

## 6. 错误处理

- override JSON 解析失败/顶层非 object → 返回 `{}` + warning（同 pricing 容错契约），回落内置表。**绝不崩 scan**。
- `model=None`（调用方未传）→ `get_model_context_window` 走 `DEFAULT_CONTEXT_WINDOW`，保守安全。
- `resolve_tier_model` 失败 → 回落 `None` → discovery 用默认 context，不阻断。
- 守现有「畸形 env 不崩 scan」契约（对齐 `concurrency.get_max_concurrent` / `get_per_call_timeout`）。

## 7. 双轨铁律边界（CLAUDE.md §1）

本改动只动 **GitNexus 轨** chunking 的参数派生（threshold 从哪来），**不改 LLM 轨 prompt、不把确定性产物喂 LLM 轨、不引入确定性→LLM hints 桥梁**。`_static-dataflow-hints` 解耦铁律（`test_static_dataflow_hints_decoupling.py`）不受影响。LLM 轨仍纯 LLM 自给自足。

## 8. 测试策略（TDD）

- `model_caps`：内置表查表（含 `normalize_model` 后缀剥离）、override JSON 合并、未知模型回落 128K、`get_chunk_token_threshold` = ctx × 0.75。
- `_estimate_tokens`：纯 ASCII ≈ `len//4`；纯中文 ≈ `len × 1.5`；混合按比例；CJK 范围覆盖中日韩。
- chunking 接线：`discover_sinks_llm(model="glm-5.2")` 的 `token_threshold` = 750K，切出的 chunk 数 ≪ 旧 12K；`model=None` 走默认 96K。
- 优先级链：`SHANNON_CHUNK_TOKEN_THRESHOLD` > 派生；override JSON > 内置表。
- 防回退：现有 sink/source discovery + concurrency + 解耦铁律测试全绿（无回归）。

## 9. 向后兼容与风险

- **向后兼容**：无 `SHANNON_MODEL_CONTEXT_OVERRIDE` 时，glm-5.2 走内置 1M（自动大幅降 chunk 数），其他模型走 128K 默认——行为变化是「chunk 更少、更快」，无 API/schema 破坏。
- **风险①：内置 context 数值错** → threshold 估错。缓解：数值标「待官网核对」（§10），`SHANNON_MODEL_CONTEXT_OVERRIDE` 可纠正。
- **风险②：tier resolve 拿错 model** → 用了 large 的 context 但实际跑 medium（context 更小）→ 爆 context。缓解：`resolve_tier_model("medium")` 精确取 medium；`CHUNK_RESERVE_RATIO=0.75` 留余量；真机冒烟核实 chunk 不爆。
- **风险③：chunk 变大 → 单 chunk 失败丢函数多**。缓解：`per_call_timeout` 配套（建议 `SHANNON_LLM_PER_CALL_TIMEOUT=180~240`）；失败粒度在 spec follow-up 评估，必要时 per-file activity 化。

## 10. 待核对项

- `MODEL_CONTEXT_WINDOWS` 数值按智谱官网核对（bigmodel.cn）：`glm-5.2`（`[1m]` ≈ 1M？）、`glm-4.5-air`（128K？）。实现时定稿。
- `resolve_tier_model` 提取后，确认 medium tier 在 glm-anthropic / glm-openai 两引擎解析一致。
- 真机 kol 冒烟：核实 chunk 数（259 → ?）、是否进 20min、sink 召回数回归（不因 chunk 变大漏召回）。
