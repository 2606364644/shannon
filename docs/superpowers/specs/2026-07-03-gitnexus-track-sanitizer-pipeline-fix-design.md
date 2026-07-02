# GitNexus 轨 sanitizer 管道断链修复设计

> 日期:2026-07-03
> 分支:`feat/fork-py`
> 范围:GitNexus 轨 inj/xss/ssrf 判定链的 sanitizer 信息管道修复 + 判定层信息密度增强

---

## 1. 背景与问题

### 1.1 GitNexus 轨 inj/xss/ssrf 判定链(设计意图)

```
intra LLM(analyze_taint_llm, per-function)
  → TaintAnalysisResult{TaintPath[sanitized, sanitizer_description, intermediate_vars]}
  → _intra_result_from_llm → IntraResult{local_steps: PropagationStep[transformation]}
  → propagate_backward_across_chains → TaintFlow.propagation_steps
  → extract_candidate_chains → CandidateChain
  → judge_chain_verdict(LLM) 基于 sanitizer 标注判防护有效性 → verdict
```

`transformation` 字段(parameter_models.py:47)是 sanitizer 信息的设计载体;`annotate_sanitizers`(sanitizer_library.py:171)在 transformation 文本上匹配 `_TRANSFORMATION_FRAGMENTS` 产 `SanitizerAnnotation`;`_detect_post_sanitize_concat`(chain_verdict.py:141)检测「消毒后再拼接」;二者喂给 `judge_chain_verdict` 的 prompt(chain_verdict.py:49-69),LLM 据 Rules 判 vulnerable/safe。

### 1.2 实际:双重断链 → sanitizer 管道空转

静态读码 + grep 证实,生产路径里 `PropagationStep.transformation` **恒为 `None`**,导致整套 sanitizer 标注管道空转。根因是两处断点:

- **断点 A(intra 丢弃)**:`_intra_result_from_llm`(llm_taint_analyzer.py:219-223)把 LLM 返回的 `TaintAnalysisResult.propagation_paths` 转成 `IntraResult` 时,**只取 `tainted_params` 和 `hits`(confidence),完全丢弃 `sanitized`/`sanitizer_description`/`intermediate_vars`**,`local_steps` 硬编码 `[]`。LLM 其实判断了 sanitizer(prompt schema llm_taint_analyzer.py:147-160 要求返回),但被转换层扔掉。

- **断点 B(propagator 不合并)**:`propagate_backward_across_chains`(chain_propagator.py:413-461)构造 `TaintFlow.propagation_steps` 时(chain_propagator.py:436)**只用跨函数 hop step,完全不用 intra 的 `local_steps`**——intra 只提供 `tainted_params` 做 seed(line 414 `_tainted_params_reaching_sink`)。即使断点 A 修好让 `local_steps` 非空,propagator 也不会合并进 `TaintFlow`。

**连锁后果**:`transformation` 恒 None → `annotate_sanitizers` 每个 step `tf=""` → `continue` → 返回 `[]`(sanitizer_library.py:184-186)→ `_detect_post_sanitize_concat` 的 `seen_sanitizer` 永 False → 恒 False → chain_verdict prompt 的 `sanitizers_repr="(none)"`、`post_sanitize_concat="False"`、`steps_repr` 全 `noop` → LLM 按 Rules(「defense effective ONLY if…」,无任何 defense 标注)→ **几乎必然判 vulnerable**。

即:chain_verdict 形式上评估防护(prompt Rules 完备),实际**信息输入为空**,退化成「有链就报 vulnerable」,不做防护有效性区分。`sanitize_library` 的 22 条规则 + 6 条 `_TRANSFORMATION_FRAGMENTS` 在生产中匹配不到任何东西。

### 1.3 测试绿掩盖了断链(关键教训)

现有单元测试 `test_chain_verdict.py:102`、`test_sanitizer_library.py:54`、`test_injection_builder.py:57` **手动构造** `_step("sanitize_hint:html.escape")` 验证机制本身——机制在隔离单测里是通的,所以测试全绿;但生产链路两处断点导致没人产 transformation,机制在生产中空转。这是「测试绿、生产坏」的又一例(同 `provider-agnostic-turn-logging` 教训:测试用手动构造绕过了生产链路的断点)。

### 1.4 附带偏差:direction_hint 标注 bug

`chain_verdict.py:46` `_DIRECTION = {"injection": "forward", "xss": "backward", "ssrf": "backward"}`——injection 标 `forward`,但实际链构造(`__init__.py:287` 唯一调用 `propagate_backward_across_chains`,注释明写「inject/xss/ssrf 改 backward」)已是 backward。这是 Phase B 改造时漏清理的遗留标注,误导判定 LLM。

### 1.5 判定层信息密度不足(独立于断链)

即便断链接通,chain_verdict prompt 当前仍缺两类信息:

- **`DangerousSlot.expression`**(parameter_models.py:128,实参源码表达式文本,注释明写「供 Spec A/LLM 追踪」)已落盘但 chain_verdict **没用**——`steps_repr`(chain_verdict.py:228-231)只取 `code_location:transformation`。判定 LLM 看不到实参代码,无法判 sanitizer 实现是否对症。
- **`intermediate_vars`**(函数内中间变量)同被断点 A 丢弃。
- **post-sanitize-concat 信号**:`TaintPath` 无 concat 标记 → `_detect_post_sanitize_concat` 即使 transformation 非空也测不到 post-concat → 该 prompt Rule 仍空转。

---

## 2. 范围

### 2.1 本次修复(5 项)

1. **断点 A 修复**:`_intra_result_from_llm` 流出 sanitizer/intermediate_vars 到 `local_steps`
2. **断点 B 修复**:`propagate_backward_across_chains` 合并 intra `local_steps` 进 `TaintFlow`
3. **direction_hint 标注修复**:injection `forward` → `backward`
4. **判定层信息密度**:`expression` + `intermediate_vars` 接进 chain_verdict prompt
5. **post-concat 信号**:`TaintPath` 加 `post_sanitized_concat`,打通 `_detect_post_sanitize_concat`

### 2.2 非目标(独立问题,不在本 spec)

- **auth 不走 source→sink**:auth 是 missing-control 检测(三信号端点 + 6 检查器 + config scan),设计如此,非 bug
- **sink 覆盖偏科**(file/path-traversal/XSS-非DOM/SSTI-TS-PHP/deser-JS-TS 等):规则库覆盖度问题,见 `docs/gap/` sink-gap-analysis,独立优化
- **entry_points 框架盲区 / 三靶场 entry_points=0**:见 `authz-gitnexus-endpoint-param-coverage-gap`,独立 spec
- **GitNexus 可用性单点故障(A3,无降级)**:`run_code_index` fail-fast,独立问题
- **finding_models 白名单错配(INJ-4,deser 丢弃)**:独立 bug

---

## 3. 方案选择

### 3.1 候选方案

| | 方案 A:正统管道修复 ✅选定 | 方案 B:chain_verdict 直读 intra ❌ | 方案 C:混合 ❌ |
|---|---|---|---|
| 思路 | 让 `transformation` 字段真正有值,兑现既有设计 | 绕过 transformation,IntraResult 加 `sink_sanitizers`,chain_verdict 经 sink_id→func_id 直读 | A+B 都做 |
| propagator 改动 | 要改(合并 local_steps) | 不动 | 要改 |
| transformation 字段 | ✅ 有值,语义兑现 | ❌ 仍空,sanitize_library 沦为死代码 | ✅ 有值 |
| 数据源 | 单一(transformation) | 单一(intra 直读)但与正则机制割裂 | 两处写,不一致风险 |

### 3.2 选 A 的理由

1. **问题本质**是「`transformation` 作为设计中的 sanitizer 载体,生产链路两处断点让它恒空」。方案 A 让该字段真正有值,既有 `sanitize_library`(22 规则)+ `annotate_sanitizers` + `_detect_post_sanitize_concat` 整套机制兑现——「修 bug 让设计意图落地」,非「打补丁绕过」。
2. **方案 B 留债**:`sanitize_library` 这套正则标注机制会变死代码(或需重新定位为 fallback),增加长期维护负担,且与 CLAUDE.md §1 不变量(transformation 作为 sanitizer 载体)冲突。
3. **方案 C 违反单一数据源**,同一信息写两处,不推荐。

---

## 4. 设计

### 4.1 数据流改造:让 `transformation` 真正有值(断点 A + B)

**(1) `_intra_result_from_llm`(llm_taint_analyzer.py:195-223)**:不再 `local_steps=[]`。把每个 `TaintPath` 转成一个 summary `PropagationStep` 并入 `local_steps`:

```python
# 伪代码
sink_line_map = {s.id: s.line for s in sinks_in_func}
local_steps = []
for path in llm_result.propagation_paths:
    if path.sink_id not in valid_sink_ids or path.source_param not in valid_params:
        continue
    tf = None
    if path.sanitized:
        desc = path.sanitizer_description or "unknown"
        tf = f"sanitize_hint:{desc}"
        if path.post_sanitized_concat:          # 见 4.2(3)
            tf += "|post_concat"
    local_steps.append(PropagationStep(
        from_func_id=block.id,
        from_param=path.source_param,
        to_func_id=block.id,                    # sink 在本函数内
        to_param=path.sink_id,
        transformation=tf,
        code_location=f"{block.file_path}:{sink_line_map.get(path.sink_id, block.start_line)}",
        intermediate_vars=list(path.intermediate_vars),  # 见 4.2(2)
        confidence=path.confidence,
    ))
return IntraResult(tainted_params=tainted, hits=hits, local_steps=local_steps)
```

**(2) `propagate_backward_across_chains`(chain_propagator.py:413-461)**:在 sink 段(`for sink in sinks_here` 内,seed 计算后)取该 sink 的 intra summary step,插入 `TaintFlow.propagation_steps` 的 sink 端:

```python
# 伪代码:sink 段(line 413 循环内,steps_rev 构造后、reversed 前)
intra = intra_results.get(sid)
sink_local_steps = []
if intra:
    sink_local_steps = [s for s in intra.local_steps if s.to_param == sink.id]
# steps_fwd = reversed(跨函数 hops) + sink_local_steps(保持 entry→sink 方向)
```

产出 `TaintFlow.propagation_steps` = `[entry→...→sink_func 的跨函数 hops..., sink_func 内 param→sink 的 summary step(带 transformation)]`。`extract_candidate_chains`(chain_verdict.py:194-207)已 `list(flow.propagation_steps)` 拷进 `CandidateChain`,transformation 自动随流。

**结果**:`annotate_sanitizers`(chain_verdict.py:190)在 summary step 的 `"sanitize_hint:..."` 上匹配 `_TRANSFORMATION_FRAGMENTS`(sanitizer_library.py:157-168)→ `SanitizerAnnotation` 非空 → chain_verdict prompt 的 `sanitizers_repr` 非空 → LLM 判防护有效性。**sanitize_library 22 条规则不再空转。**

**(3) direction_hint 标注修复**(chain_verdict.py:46):

```python
_DIRECTION = {"injection": "backward", "xss": "backward", "ssrf": "backward"}
```

### 4.2 判定层改造:expression + intermediate_vars + post_concat 进 prompt

**(1) `PropagationStep`(parameter_models.py:40-49)**:加字段

```python
intermediate_vars: list[str] = []   # 默认空,旧 json 兼容
```

**(2) `TaintPath`(parameter_models.py:96-104)**:加字段

```python
post_sanitized_concat: bool = False   # 默认 False,旧 json 兼容
```

**(3) `build_taint_prompt`(llm_taint_analyzer.py:147-169)**:schema 加 `post_sanitized_concat`,Rules 加一条「若 path 先 sanitize 随后被污染(如消毒后拼接、或多个 source 合并)置 `post_sanitized_concat=true`」。

**(4) `_detect_post_sanitize_concat`(chain_verdict.py:141-156)**:新增对 summary step 编码标记的识别(保留原多 step 序列逻辑,向后兼容):

```python
for s in steps:
    tf = (s.transformation or "").lower()
    if "post_concat" in tf:          # 新增:summary step 编码的 post_concat 标记
        return True
    # ...原 seen_sanitizer + tf=="concat" 逻辑保留
```

**(5) `CandidateChain`(chain_verdict.py:72-86)**:加字段

```python
sink_expressions: list[str] = []   # 从 SinkCallSite.dangerous_slots[].expression 取
```

**(6) `extract_candidate_chains`(chain_verdict.py:159-208)**:构造 `CandidateChain` 时填 `sink_expressions`(从 `sink_call_sites[flow.sink_call_site_id].dangerous_slots` 取 expression 列表)。注意调用链路差异:`extract_candidate_chains` 已支持 `sink_call_sites` 参数(可选),但 **xss_builder(xss_builder.py:148)传了,injection_builder(:43)/ssrf_builder(:30)当前不传**——需让这两个 builder 接收 `sink_call_sites` 并透传给 `extract_candidate_chains`,`run_gitnexus_chain_verdict` activity 把 `sink_call_sites` 喂给 builder(配套见改动 #11)。

**(7) chain_verdict prompt(chain_verdict.py:49-69)**:新增字段 + 扩展 steps_repr:

```
- slot expressions (source code of sink args): {sink_expressions}      # 新增
- propagation steps: {steps_repr}   # steps_repr 扩展:含 intermediate_vars
...
```

`steps_repr`(chain_verdict.py:228-231)扩展格式:`f"{code_location}:{transformation}|vars={intermediate_vars}"`。

### 4.3 修复后数据流(完整)

```
intra LLM
  → TaintAnalysisResult{TaintPath[sanitized, sanitizer_description,
                                  intermediate_vars, post_sanitized_concat]}
  → _intra_result_from_llm(4.1(1))
  → IntraResult.local_steps[PropagationStep{
        transformation="sanitize_hint:<desc>|post_concat"?,   # 非空
        intermediate_vars=[...]}]
  → propagate_backward_across_chains(4.1(2)) 合并
  → TaintFlow.propagation_steps   # 含 transformation
  → extract_candidate_chains(4.2(6)) → CandidateChain{sink_expressions=[...], ...}
  → judge_chain_verdict prompt{
        steps_repr(含 intermediate_vars),
        sanitizers_repr(非空,经 annotate_sanitizers),
        post_sanitize_concat(可能 True,经 _detect_post_sanitize_concat),
        sink_expressions}
  → verdict(真正基于防护有效性,非机械 vulnerable)
```

---

## 5. 改动清单

| # | 文件:位置 | 改动 | 断点/项 |
|---|---|---|---|
| 1 | llm_taint_analyzer.py:195-223 | `_intra_result_from_llm` 构造 `local_steps`(summary step) | 断点 A |
| 2 | llm_taint_analyzer.py:147-169 | `build_taint_prompt` schema + Rules 加 `post_sanitized_concat` | post-concat(项5) |
| 3 | parameter_models.py:40-49 | `PropagationStep` 加 `intermediate_vars` | 项4 |
| 4 | parameter_models.py:96-104 | `TaintPath` 加 `post_sanitized_concat` | post-concat(项5) |
| 5 | chain_propagator.py:413-461 | `propagate_backward` sink 段合并 intra `local_steps` | 断点 B |
| 6 | chain_verdict.py:46 | `_DIRECTION["injection"]="backward"` | direction_hint(项3) |
| 7 | chain_verdict.py:141-156 | `_detect_post_sanitize_concat` 加 `post_concat` 标记识别 | post-concat(项5) |
| 8 | chain_verdict.py:72-86 | `CandidateChain` 加 `sink_expressions` | expression(项4) |
| 9 | chain_verdict.py:159-208 | `extract_candidate_chains` 填 `sink_expressions`,inj/ssrf 也传 `sink_call_sites` | expression(项4) |
| 10 | chain_verdict.py:49-69, 228-231 | prompt 加 `sink_expressions`,steps_repr 含 `intermediate_vars` | 项4 |
| 11 | injection_builder.py:43 / ssrf_builder.py:30 + `run_gitnexus_chain_verdict` activity | inj/ssrf builder 接收并透传 `sink_call_sites`(对齐 xss_builder) | 项4(配套) |

---

## 6. 测试策略(防测试绿生产空转)

### 6.1 端到端集成测试锚点(新增,核心)

**集成测试(mock LLM 全链)**:mock `llm_client` 返回带 `sanitized=True, sanitizer_description="html.escape", intermediate_vars=["raw","escaped"], post_sanitized_concat=True` 的 `TaintAnalysisResult` → `analyze_taint_llm` → `propagate_backward_across_chains` → 断言:
- `pgraph.taint_flows[].propagation_steps` 存在某 step `transformation` 含 `"sanitize_hint:html.escape"` 且 `intermediate_vars` 非空
- 经 `extract_candidate_chains` → `CandidateChain.sink_expressions` 非空
- `judge_chain_verdict` 喂给 LLM 的 prompt 含非空 `sanitizers_repr`、`post_sanitize_concat=True`、`sink_expressions`

**防回退锚点(护栏)**:断言「生产路径(mock 一个含 sink 的函数 + mock LLM 返回 sanitized)产出的 `propagation_steps` 至少有一个 `transformation` 非空」——直接针对两处断点重现(防 `_intra_result_from_llm` 再次 `local_steps=[]`、或 propagator 再次不合并)。

### 6.2 现有单元测试(保留)

`test_chain_verdict.py`、`test_sanitizer_library.py`、`test_injection_builder.py` 手动喂 step 测判定逻辑本身,仍有效,不删(它们验证机制正确性,集成测试验证管道接通)。

---

## 7. 向后兼容

全靠 pydantic 默认值,旧 `parameter_graph.json` 反序列化不破:

- `PropagationStep.intermediate_vars: list[str] = []`
- `TaintPath.post_sanitized_concat: bool = False`
- `CandidateChain.sink_expressions: list[str] = []`(dataclass field 默认)

旧 json 的 `transformation=null`:新代码消费时 `annotate_sanitizers` 仍返回空(对旧数据预期如此——需重跑 `code_index` 才产带 transformation 的新数据)。**无数据迁移**。

---

## 8. 真机验证(对齐 memory 口径)

跑 NodeGoat 白盒后:

1. `jq '.taint_flows[].propagation_steps[].transformation' parameter_graph.json | sort | uniq -c` → 出现非 null(`sanitize_hint:...`)
2. `jq '.[].verdict' injection_gitnexus_queue.json | sort | uniq -c` → 不再 ~100% vulnerable(有 `safe` 出现,因 sanitizer 流通后 LLM 能判防护有效)
3. (可观测性)确认 `[GN-LLM]`/chain-verdict 相关日志无「sanitizers_repr=(none)」占满

---

## 9. 非目标 / Follow-up

- **forward `propagate_across_chains`(chain_propagator.py:136)**:当前注释「保留过渡,供 authz `_source_reaches_sink` 复用」。本 spec 不动它。若后续确认 authz 不再依赖,可单独清理。
- **intra LLM schema 进一步增强**(如逐步 transformation 而非路径级 summary):本 spec 用路径级 summary step(每条 param→sink 路径一个 step),足够让 sanitizer/post_concat/intermediate_vars 流通;更细粒度的函数内 step 拆分留 follow-up。
- **`sink_expressions` 的 inj/ssrf 路径传参**:已确认 injection_builder/ssrf_builder 不传 `sink_call_sites`(仅 xss_builder 传)。实现时需让两个 builder 接收并透传,activity 把 `sink_call_sites` 喂给 builder(改动 #11)。

---

## 10. 验收标准

- [ ] 改动清单 11 项全部落地
- [ ] 端到端集成测试 + 防回退锚点通过
- [ ] 现有 `test_chain_verdict`/`test_sanitizer_library`/`test_injection_builder` 单元测试仍绿
- [ ] 真机 NodeGoat 验证:transformation 非 null + verdict 不再 ~100% vulnerable(待真机冒烟,记 follow-up 若 GitNexus chains=0 阻塞)
