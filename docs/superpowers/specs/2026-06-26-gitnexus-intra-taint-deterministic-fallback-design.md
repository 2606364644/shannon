# GitNexus 轨 intra-taint 确定性 fallback 改造设计

> 评估命题:GitNexus 轨在 pre-recon 阶段需要 LLM 吗?
> 结论:**不需要**。它是确定性轨,LLM 仅可选精度增强。本设计不接 LLM,而是把当前粗暴的 intra-taint fallback 改成确定性的 `is_entry_hint` 判断。

---

## 1. 背景

- 双轨架构(CLAUDE.md):GitNexus 轨 = 确定性层,LLM 轨 = 纯 LLM,二者只在 merger(verdict OR)交汇。铁律:GitNexus 轨保持确定性,不引 LLM 依赖。
- pre-recon 阶段 `run_code_index`(GitNexus 轨建图)内嵌**唯一 LLM 触点**:`analyze_taint_llm`(逐函数 taint,判断哪些参数能到 sink)。
- 生产环境 `llm_client` 是 **stub**(`activities.py:368` 直接 raise)→ 永远走 fallback。
- 当前 fallback(`llm_taint_analyzer.py:286-289`):
  ```python
  return IntraResult(
      tainted_params=set(block.parameters),              # 全参数标 tainted
      hits={s.id: 1.0 for s in sinks_in_func},          # 全 sink 标命中 1.0
      local_steps=[],
  )
  ```
  即"每个有 sink 的函数,所有参数都是污点源、所有 sink 都命中"——粗暴 over-approximation,产出海量过近似 taint_flows。

## 2. 评估结论(整轨 LLM 依赖度)

GitNexus 轨在 pre-recon 建图阶段对 LLM 的真实依赖 = **零必需**。

- **骨架全确定性**:GitNexus call graph + tree-sitter + sink_detector 规则库 + `propagate_across_chains`(正则匹配调用实参,无 LLM)即可产出 `code_index.json` / `parameter_graph.json`。
- **当前全 stub 在「召回安全」上自洽**(over-approximation 不漏,merger OR 保守),但在「精度」上是粗暴降级。
- **确定性正解的料已备好却未用**:`DangerousSlot.is_entry_hint`(`parameter_models.py:129`,注释明示"为无 LLM 时确定性判断准备")已在 `sink_detector.py:247/408/417` 填充,但全仓没有任何地方消费它做 intra 判断。
- **即使接 intra LLM 当前也无处发挥**:下游 `chain_verdict` 的 llm_client 同为 stub、`run_gitnexus_chain_verdict` 未在 worker 注册 → intra 的精度产物走不到真判定。

**方向**:不接 LLM,把 fallback 改用 `is_entry_hint` 做确定性 intra 判断(立场 B)。

## 3. 设计

### 3.1 改造范围(最小爆炸半径)

仅改 `analyze_taint_llm` 的 **fallback 分支**(`llm_taint_analyzer.py:278-290`),提取为独立函数:

```python
def _deterministic_intra_fallback(
    block: FuncBlock,
    sinks_in_func: list[SinkCallSite],
) -> IntraResult: ...
```

- LLM 成功路径(`:274-276`)不变;`llm_client` 为 None 或调用失败/返回不可解析时 → 走新 fallback(替换原"全标"逻辑)。
- 不改 `propagate_across_chains`、不改 builder、不改 `chain_verdict`、不改 LLM prompt。

### 3.2 新 fallback 逻辑

输入:`block`(含 `parameters`)、`sinks_in_func`(每个 `SinkCallSite` 含 `dangerous_slots: list[DangerousSlot]`,每个 slot 有 `is_entry_hint: bool` + `expression: str`)。

输出 `IntraResult`:

- **`tainted_params = set(block.parameters)`** —— 保持不变。理由:`propagate_across_chains:164` 要求 `tainted_params` 非空才 seed chain;保守全保可保证 seed + 跨函数传播(`_map_call_site_params`)不漏召回。
- **`hits`**(遍历每个 sink,查其 `dangerous_slots`):
  | dangerous_slots 状况 | 处理 | 置信度 |
  |---|---|---|
  | 任一 slot `is_entry_hint=True`(实参直达参数/`request.*`/superglobal) | 进 hits | `0.9`(AST 直达) |
  | 全部 slot 的 expression 均为字面量(引号字符串/纯数字/`true`/`false`/`null`/`None`) | **不进 hits**(过滤明确安全的常量 sink) | — |
  | 否则(变量引用但非直达,如 `data.x`、`processed_id`) | 进 hits | `0.5`(间接,需 LLM/LLM 轨复核) |
- **`local_steps = []`** —— 确定性 fallback 不产跨步信息(与原 fallback 一致)。

字面量判断 helper(新增,模块私有):

```python
def _is_literal_expression(expr: str) -> bool:
    """保守判断 expression 是否为字面量常量(明确非注入源)。"""
    e = expr.strip()
    if not e:
        return True
    if e[0] in "\"'" and e[-1] == e[0]:   # 引号字符串
        return True
    if e.lstrip("-+").isdigit() or _is_float(e):  # 数字
        return True
    return e in {"true", "false", "null", "None", "True", "False"}
```

### 3.3 为何这样设计(权衡)

- **召回不漏**:`tainted_params` 全保(保 seed + 传播);非字面量 sink 全进 hits(仅分层,不过滤)。
- **精度提升**:直达(0.9)vs 间接(0.5)分层 → 下游 `chain_verdict`/merger 可据 `confidence` 区分;纯字面量 sink 过滤(明确非注入,直接降噪)。
- **双轨铁律**:不引 LLM 依赖,确定性轨自洽。LLM 增强留待「整轨接通后」(`run_gitnexus_chain_verdict` 注册 + verdict LLM client)作可选补强,不在本设计。

### 3.4 propagate 契约验证(不破坏)

`propagate_across_chains`(`chain_propagator.py:133-210`)消费 `IntraResult`:

1. `head_intra.tainted_params` 非空才 seed chain(`:164`)→ `tainted_params` 全保满足。
2. 遍历 `hits.items()` 产 `TaintFlow`(`:190`),`flow.confidence = min(steps_confidence, sink_confidence)`(`:192-198`)→ 新分层 0.9/0.5 传入 `TaintFlow.confidence`,语义不破坏。
3. `source_param = next(iter(tainted_params))`(`:171`)→ 行为不变(仅 hits 的精度/数量变化)。

新 fallback 不改 `tainted_params` 语义 → propagate 行为不变,仅 hits 精度/数量改善。

## 4. 不做什么(范围控制)

- ❌ 不接 intra-taint 真 LLM(立场 A,留待整轨接通后单独评估)。
- ❌ 不改 `propagate_across_chains` / builder / `chain_verdict` / LLM prompt。
- ❌ 不注册 `run_gitnexus_chain_verdict`(独立 follow-up,见 CLAUDE.md worker 注册坑)。
- ❌ 不改 `tainted_params` 的全参数保守性(精确化 `source_param` 是另一议题,避免漏召回)。

## 5. 测试策略

目标文件:`packages/core/tests/code_index/test_llm_taint_analyzer.py`(若无则新建)。

1. `fallback_direct_param_sink`:`execute(userId)`(userId 是参数)→ hits 含该 sink @ 0.9。
2. `fallback_request_object_sink`:`query(request.body)`(`is_entry_hint=True` via `request.`)→ hits @ 0.9。
3. `fallback_local_var_sink`:`execute(processed)`(非字面量变量,非直达)→ hits @ 0.5。
4. `fallback_literal_sink`:`execute("SELECT * FROM users")`(字面量)→ hits **不含**该 sink(过滤)。
5. `fallback_preserves_all_tainted_params`:`tainted_params == set(block.parameters)`(保 seed)。
6. **LLM 成功路径回归**:现有 `analyze_taint_llm`(llm_client 返回有效 JSON)测试不破。
7. **契约集成 smoke**:新 fallback 产出的 `IntraResult` 喂 `propagate_across_chains`,能正常 emit `TaintFlow`(用构造的 CallChain + blocks)。

## 6. 风险

- `is_entry_hint` 是浅判断(只认直接参数/`request.*`/superglobal),间接流被判 0.5 而非精确——可接受(LLM vuln 轨双轨 OR 兜底召回)。
- `_is_literal_expression` 误判风险(把变量当字面量会漏):helper 保守,仅认明确字面量形态。
- 置信度 0.9/0.5 当前下游不自动过滤(只写入 `TaintFlow.confidence`),价值体现在数据质量 + 未来 `chain_verdict` 接通后的精度分层。

## 7. 完成定义

- `_deterministic_intra_fallback` 替换原 fallback,`analyze_taint_llm` 在 stub 下走新逻辑。
- 第 5 节全部测试通过(含 LLM 成功路径回归)。
- 真机冒烟(可选,follow-up):真实仓库跑 `run_code_index`,确认 `parameter_graph.json` 的 `taint_flows` 数量下降(字面量 sink 被过滤)、`confidence` 出现 0.9/0.5 分层。
