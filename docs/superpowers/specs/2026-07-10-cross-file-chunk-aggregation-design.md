# 跨文件 chunk 聚合 — 设计

> spec 2026-07-10。状态：设计稿（待 writing-plans）。
> 关联：[[chunk-threshold-per-model-context]] 的直接后续——threshold 自适应已实现但对 kol 无效，本 spec 解决真正的瓶颈（文件数）。

## 1. 背景与问题

前序 spec（2026-07-10 chunk threshold 按模型 context 自适应）已实现：`CHUNK_TOKEN_THRESHOLD` 从硬编码 12K 改为按模型 context 派生（glm-5.2 → 750K），token 估算改 CJK 加权。**但对 kol_mapping_service 仍无效**——重跑 sink-discovery 仍是 `259` chunk。

诊断脚本 `scripts/diagnose_kol_chunk.py` 一锤定音：

```
suspicious calls 总数: 631       有 suspicious call 的文件数: 259  (=259? True)
每文件 token: min=26 max=18861 avg=720 median=177 p90=1639
  <12K token 文件: 257/259      <96K token 文件: 259/259
各 threshold 下 chunk 数: 12K→259  96K→259  750K→259   ← 提到 750K 仍 259!
```

**根因**：`chunk_items_by_file` 按 `file_path` 分组、**绝不跨文件**。kol 是 257/259 个极小文件（avg 720 token），文件级聚合早就是「1 文件 = 1 chunk」的下限。threshold 提升只对「单文件过大被拆」有效，对「海量小文件」无效——**chunk 数瓶颈是文件数，不是单文件大小**。

**真正瓶颈**：259 个文件 = 259 次独立 LLM 调用，每次起 Claude Code CLI 子进程（固定开销 ~15s）+ 重复 system prompt。259 / 并发2 × ~35s ≈ 75min，远超 20min activity timeout → 幂等超时被 `CODE_INDEX_RETRY`(max3) 放大。

而 259 个文件总 token 才 ~186K，理论上 1 个 750K chunk 容得下——「不同文件不混」这个约束把 631 个 call 硬拆成 259 份。

## 2. 目标 / 非目标

**目标**
- chunking 从 per-file 演进为「**文件作装箱单位 + 跨文件贪心合并**」：同文件 block 不拆（保留 intra-file 优先），多个小文件包合并进一个 chunk。
- 双上限控制 chunk 大小：token threshold（沿用 context 派生）+ call 数软上限（`SHANNON_CHUNK_MAX_CALLS`，默认 100）。
- 预期：kol 631 calls → ~7 chunk，调用数 259→~7（降 ~37x），~75min → ~3-4min，不再撞 20min timeout。

**非目标**
- 不改 LLM 判定逻辑/prompt 语义（sink/source 判定照旧，prompt 已 per-block 标 file_path，跨文件对判定几乎无影响）。
- 不改 taint analyzer（不走 chunking，per-function 调用，不受影响）。
- 不动确定性 sink 规则 / sink_candidates.yml 候选模式表（631 这个量级是否合理是独立 follow-up，见 §9）。
- 守双轨铁律：只动 GitNexus 轨 chunking，不碰 LLM 轨 prompt 内容。

## 3. 设计

### 模块 1：chunking 算法（改 `llm_concurrency.chunk_items_by_file`）

原地改函数 + 加参数（不新增独立函数）。从「每文件独立装箱」演进为「文件作单位 + 跨文件贪心合并」：

```python
def chunk_items_by_file(
    items: list[Any],
    *,
    block_of: Callable[[Any], FuncBlock],
    token_threshold: int,   # 必填(沿用前序 spec)
    max_calls: int,         # 新增, 必填: chunk 内 call 数上限
) -> list[FileChunk]:
    """文件作装箱单位 + 跨文件贪心合并 + 双上限(spec 2026-07-10)。

    1. 按 file_path 分组 -> 每文件一个「文件包」(blocks + items + total_tokens + call_count)。
       同文件 block 不拆(保留 intra-file 优先语义)。
    2. 跨文件贪心装箱: 遍历文件包, 累加到当前 chunk; 加入后超 token_threshold
       或 call_count > max_calls -> 开新 chunk。
    3. 单文件包自身超任一上限 -> 文件内按 block 退化拆分(同文件 block 连续, 保序)。
    保证: 同文件 block 不被拆到不同 chunk(除非单文件超限退化); 不同文件可合并。
    """
```

装箱顺序：按 file_path 字典序遍历文件包（稳定可重现；同文件 block 因分组天然连续）。单文件包超限时的退化拆分复用现有「按 block 累加 token」逻辑，额外校验 call_count。

### 模块 2：双上限控制

- **token_threshold**（沿用前序 spec `get_chunk_token_threshold(model)`，glm-5.2 = 750K）。
- **`SHANNON_CHUNK_MAX_CALLS`**（新 env，默认 100）：chunk 内 suspicious/source call 数上限。
- 任一触顶即开新 chunk。两层防御：token 防爆 context，call 数防单 chunk 过大（LLM 疲劳漏判 + 失败粒度）。

读取方式（对齐 `concurrency.get_per_call_timeout` 容错契约）：

```python
# config/concurrency.py
def get_chunk_max_calls() -> int:
    """SHANNON_CHUNK_MAX_CALLS, 默认 100。非法/<=0 回落默认 + warning, 不崩 scan。"""
```

### 模块 3：FileChunk 结构改造

```python
@dataclass(frozen=True)
class FileChunk:
    file_paths: tuple[str, ...]   # str(file_path) -> tuple(跨文件多文件)
    blocks: tuple[FuncBlock, ...] # 不变
    items: tuple[Any, ...]        # 不变
```

**消费方适配**（探索已确认影响面小）：
- `sink_discovery_llm._build_discovery_prompt`：模板 `{file_path}` 占位 → `{file_paths}`（join 多文件，如 `"a.go, b.go"`）。prompt 已 per-block 标 `### func (file_path:line)`，判定语义不变。
- `source_discovery_llm._build_prompt`：同上对称改。
- `_on_skip` 诊断（两处）：`chunk.file_path` → `chunk.file_paths`（多文件标注，如 `"a.go, b.go: timeout"`）。

### 模块 4：discovery 对称接线

`sink_discovery_llm.discover_sinks_llm` 与 `source_discovery_llm.discover_sources_llm`：
- 读 `get_chunk_max_calls()` 传给 `chunk_items_by_file`。
- `model` 参数透传（前序 spec 已做）不变。

## 4. 配置优先级

`SHANNON_CHUNK_MAX_CALLS`：env 直接读，默认 100。畸形（非 int / <=0）回落 100 + warning，不崩 scan。
（与前序 spec 的 token threshold 优先级链独立，两者共同决定 chunk 切分。）

## 5. 文件改动清单

| 文件 | 改动 |
|---|---|
| `code_index/llm_concurrency.py` | `chunk_items_by_file` 加跨文件贪心 + `max_calls`；`FileChunk.file_path` → `file_paths: tuple` |
| `config/concurrency.py` | 新增 `get_chunk_max_calls()`（默认 100，容错） |
| `code_index/sink_discovery_llm.py` | `_build_discovery_prompt` 模板 `{file_paths}`；`_on_skip` 诊断；读 `get_chunk_max_calls` 传入 |
| `code_index/source_discovery_llm.py` | 同上对称改 |
| `tests/code_index/test_llm_chunking.py` | 适配 `file_paths`；加跨文件/双上限/退化测试 |
| `tests/code_index/test_sink_discovery_llm.py` + `test_source_discovery_llm.py` | 适配 + max_calls 透传测试 |
| `.env.profiles.example/*.env.example` + `.env` | 补 `SHANNON_CHUNK_MAX_CALLS` 注释 |

## 6. 错误处理

- `SHANNON_CHUNK_MAX_CALLS` 非法 → 回落 100 + warning（对齐 `get_per_call_timeout` / `get_max_concurrent`）。
- `max_calls` / `token_threshold` 必填（底层工具纯函数语义，默认值在 discovery 层从 env 读）。
- 守降级契约：LLM 不可用仍返回空；chunking 切分变化不影响「soft sink/source 产出语义」。

## 7. 双轨铁律边界（CLAUDE.md §1）

只动 GitNexus 轨 chunking 的**切分算法**（chunk 含哪些 block/items），**不改 LLM 轨 prompt 内容、不把确定性产物喂 LLM 轨**。`test_static_dataflow_hints_decoupling.py` 必须仍绿。sink/source 判定逻辑（`_parse_verdicts` / `_parse_fields`）不变，只是单次 prompt 含多文件 block。

## 8. 测试策略（TDD）

- **跨文件合并**：2 个小文件（各 < threshold、各 < max_calls）→ 1 chunk（`file_paths` 含两文件，blocks 含两文件函数）。
- **call 上限触发**：max_calls=5 + 多文件多 call → 按 5 拆多个 chunk。
- **token 上限触发**：沿用现有大文件拆分逻辑（跨文件累加超 threshold 开新 chunk）。
- **同文件不拆**：同文件多 block 不被拆到不同 chunk（除非单文件超限）。
- **单文件超限退化**：单文件 > threshold 或 call 数 > max_calls → 文件内按 block 拆，block 连续保序。
- **source 对称**：`discover_sources_llm` 同行为。
- **prompt 多文件标注**：`{file_paths}` 正确 join。
- **容错**：`get_chunk_max_calls` 非法回落 100。
- 防回退：现有 sink/source discovery + chunking 测试全绿（适配 `file_paths`）。

## 9. 风险与 follow-up

- **风险①：跨文件 prompt 混淆 LLM**。缓解：prompt 已 per-block 标 file_path；max_calls=100 限制单 chunk 规模；真机冒烟核实召回数不回归。
- **风险②：失败粒度变粗**（1 chunk 失败丢多文件）。缓解：max_calls 上限 + Temporal activity 重试；真机观测失败率。
- **follow-up（独立，不在本 spec）**：631 suspicious call 的量级是否合理？`sink_candidates.yml` 候选模式表对 Go 是否过宽泛（大量误报进 LLM）？若能从源头收紧候选，suspicious 数下降，chunking 压力同步下降。需另开分析。

## 10. 待验证（真机）

- kol 重跑：chunk 数 259 → ?（预期 ~7）、是否进 20min、sink 召回数回归（不因跨文件漏召回）。
- `scripts/diagnose_kol_chunk.py` 复用：实现后跑确认 chunk 数下降到预期。
- 单 chunk ~100 calls 的 LLM 判定质量（漏判率 vs per-file）。
