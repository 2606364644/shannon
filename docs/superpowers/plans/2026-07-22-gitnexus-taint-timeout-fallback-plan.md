# Plan: GitNexus taint-analysis 超时跳过走确定性兜底(修 SSRF 漏报)

日期: 2026-07-22 | 分支: feat/fork-py | 关联 memory: gitnexus-ssrf-taint-flow-drop-root-cause

## 问题陈述

重构版扫 sentinel_dashboard 漏报 SSRF(原始版 10 -> 重构版 0)。根因链:

1. `taint-analysis` 阶段是 per-function 粒度(24 个含 sink 函数 = 24 次独立 LLM 调用,**未像 sink/source discovery 那样按文件聚合**)。
2. SSRF 核心 sink 函数 `PprofService.proxyPprofRequest` 与 `LogLevelService.getLogLevels` 因单次 LLM 调用 >60s(per_call_timeout 默认 60s)被 `map_llm_with_bounds` 跳过(events.ndjson 实证:`proxyPprofRequest: timed out (>60.0s), skipped` / `getLogLevels: timed out (>60.0s), skipped`)。
3. `__init__.py:323` `intra_results = {func_id: result for func_id, result in taint_pairs}` -- 被跳过函数不进 `taint_pairs` -> **不进 `intra_results`**。
4. backward 传播 `propagate_backward_across_chains`(`chain_propagator.py:421`)调 `_tainted_params_reaching_sink(sink, intra_results.get(sid))` 拿到 `intra=None` -> 回退 `dangerous_slots[0].expression="request"`(HttpGet 局部变量,非函数参数)。
5. `_map_call_site_params_reverse`(:440)无法把局部变量 "request" 映射到 caller 参数 -> 到 entry(controller)时 `_source_points_matching`(:367)用 "request" 匹配 source_points(ip/port/pprofPort)失败 -> anchored 空 -> **SSRF flow 丢弃**。

**这不是传播逻辑 bug,是 taint 编排的"超时跳过即丢弃"bug**。`_deterministic_intra_fallback`(`llm_taint_analyzer.py:284`)就是为此设计的兜底(docstring:"tainted_params 保守保留全部参数 -- 保 propagate_across_chains 的 chain seed 与跨函数传播,不损失召回"),但当前只在 `analyze_taint_llm` 内部 `raw_response is None`(LLM 抛异常)时触发;`map_llm_with_bounds` 的超时跳过发生在更外层(`_taint_one` 被 `wait_for` 包裹),`_taint_one` 没正常返回 -> 兜底根本没机会跑。

CLAUDE.md §1 明确设计:"LLM 不可用(stub / 超时)时退回纯规则 + is_entry_hint(deterministic-fallback 立场 B,作为'LLM 不可用档',不浪费)"。本次修复即兑现该设计。

## 修复方案

### P0(核心,必做):超时/异常跳过的 sink 函数走确定性兜底

**改动文件**:`packages/core/src/supernova_core/code_index/__init__.py`(行 315-323 附近)

**改动**:`map_llm_with_bounds` 返回后,对未进 `taint_pairs` 的 sink 函数(被跳过的)调 `_deterministic_intra_fallback` 产兜底 IntraResult 并填入 `intra_results`。

```python
taint_pairs = await map_llm_with_bounds(taint_items, _taint_one, ...)
await taint_emitter.finalize(...)
intra_results = {func_id: result for func_id, result in taint_pairs}

# 超时/异常跳过的 sink 函数走确定性兜底(CLAUDE.md §1: LLM 不可用档不浪费)。
# 否则 backward 拿不到 seed -> 跨函数 taint flow 全丢(如 SSRF controller->service)。
from supernova_core.code_index.llm_taint_analyzer import _deterministic_intra_fallback
analyzed_ids = set(intra_results)
for func_id, func_sinks in taint_items:
    if func_id in analyzed_ids:
        continue
    block = blocks_by_id.get(func_id)
    if block is None:
        continue
    intra_results[func_id] = _deterministic_intra_fallback(block, func_sinks)
```

**效果**:proxyPprofRequest 进 intra_results,tainted_params={app,ip,port,pprofPort,...},backward seed 能映射到 controller 参数 -> 锚定 source_point -> SSRF flow 产出(hits=0.5 间接,needs_review,进 chain_verdict 复核)。

**风险**:over-approximation(全参 tainted)会为非直接流向 sink 的参数(如 app/params)也产 flow,引入候选。但 chain_verdict 轻量 LLM 判定会复核过滤误报,且这是 docstring 承诺的"避免 false negative"预期行为。可接受。

### P1(应做):LLM 响应解析失败也走兜底(修 under-approximation)

**改动文件**:`packages/core/src/supernova_core/code_index/llm_taint_analyzer.py`(`analyze_taint_llm` 行 363-386)

**改动**:当前 `raw_response is not None` 但 `parse_llm_response` 解析失败/返回空时,走 `:363` 分支返回空 `tainted_params`(under-approximation,违背 docstring "On LLM failure conservatively mark all params tainted")。改为解析失败时 fallback 到 `_deterministic_intra_fallback`。

GLM 常返回带 markdown fence / 额外字段 / 非 strict JSON,极易触发解析失败。22 个被分析函数 hits=0 中,部分可能就是解析失败导致(待 P0 落地后看是否还有 hits=0)。

**改动点**:`parse_llm_response`(`:182-192`)解析失败时打 WARNING + raw_response 片段(当前仅 DEBUG,看不到失败),并在 `analyze_taint_llm` 解析失败分支调 `_deterministic_intra_fallback`。

### P2(可选,降低超时概率,follow-up)

- **taint 阶段按文件聚合**:像 sink/source discovery 用 `chunk_items_by_file`,减少 LLM 调用次数 + 单 chunk 共享上下文。但 per-function 是当前语义,按文件聚合需评估是否改变 taint 判定质量。**列为 follow-up,不在本 plan**。
- **补 typed_params**:接活死代码 `_build_typed_params_by_block`(`__init__.py:58`,无调用方)并传给 `analyze_taint_llm`(`:293-297` 没传),让 prompt 带 source 注解(`@RequestParam` 等),降低弱模型漏判。**列为 follow-up**。
- **per_call_timeout**:60s 偏紧,但 memory 显示 kol 场景已撞 timeout 并做过 chunk 自适应。单纯加超时非长久之计,P0 兜底已覆盖超时后果。**不单独改**。

## 测试计划(TDD)

**新增** `packages/core/tests/code_index/test_taint_timeout_fallback.py`:

1. `test_taint_timeout_falls_back_to_deterministic`:mock `map_llm_with_bounds` 跳过某 sink 函数(模拟超时),断言该函数进 `intra_results` 且 `tainted_params` 非空(=全部参数),`hits[sink.id]=0.5`。
2. `test_timeout_fallback_enables_backward_ssrf_flow`:端到端--构造 controller(source @RequestParam ip) -> service(proxyPprofRequest,SSRF sink,expression="request" 局部变量)的调用链,模拟 service 函数 taint 超时跳过,断言 `propagate_backward_across_chains` 产出 ip->execute 的 SSRF flow(当前 fail,修复后 pass)。
3. `test_parse_failure_falls_back_to_deterministic`(P1):mock LLM 返回非 JSON,断言 `analyze_taint_llm` fallback 到全参 tainted 而非空。

**回归**:跑 `test_chain_propagator_backward.py` / `test_intra_first_taint_flow.py` / `test_llm_taint_analyzer.py` / `test_build_code_index_orchestration.py`(只跑改动相关,勿广跑全套--预存挂起/失败)。

## 端到端验证

P0+P1 落地后,`uv run shannon-whitebox start --repo /root/shannon-py/repos/frontend/sentinel_dashboard`,确认:
- `gitnexus_track_status.json` 的 ssrf > 0(当前 0)
- `injection_gitnexus_queue.json` 出现 SSRF 类链(当前只有 deserialization)
- 对照原始版 10 个 SSRF,召回率回升(proxyPprofService/getLogLevels/SentinelApiClient/machineResource 等链)

## 铁律边界(守)

- 不改 LLM 轨(纯 LLM 自给自足,不吃确定性产物)。
- 不改 sink_rules.yml / source_rules.yml(规则层无缺失,source_points=269/sink_call_sites=51 都识别到了,问题是传播)。
- 只动 code_index 确定性层的 taint 编排 + intra 兜底。
- 双引擎无关(core 层)。

## 回滚

P0 改动是 `__init__.py` 一个循环块,可独立 revert。P1 是 `llm_taint_analyzer.py` 分支调整。两者互不依赖,P0 可单独先上。
