# sink/source 补召回 文件级聚合 + timeout 调整 设计

> 日期: 2026-07-10
> 状态: 设计(brainstorming 产出,待 review → writing-plans)
> 分支: feat/fork-py
> 关联: `docs/superpowers/specs/2026-06-30-discover-sinks-llm-concurrency-design.md`(`map_llm_with_bounds` 并发骨架)、`source-recall-intra-first` spec(source_discovery_llm 出处)

---

## 1. 背景 / 问题

`run_code_index` activity 里的 LLM 补召回(sink-discovery + source-discovery)是 **per-function 粒度**:每个可疑函数一次 LLM 调用。大仓下函数数多,累加耗时远超 activity 的 `start_to_close_timeout`(10min)。

**2026-07-10 真机**:`kol_mapping_service`(Go,sink-discovery 569 个含可疑 call 的函数)跑到 40/569(约 9min)撞 10min timeout → activity 被 Temporal cancel(`CancelledError at sem.acquire`)→ 自动重试 → 重试期 Temporal server gRPC `http2 error`(瞬态)→ `TransientError` 扫描失败。

**根因链**:
- per-function 粒度 × 默认并发 `SHANNON_MAX_CONCURRENT=3` → 569 函数串行累加 ~95min,远超 10min。
- activity timeout 10min 容不下。

> 注:与 GitNexus 安装/可用性无关 —— 本次 gitnexus 索引、cypher 查询(300 process labels)、规则 sink 检出(147 个)均正常。纯属 **LLM 调用次数 × 粒度 × timeout** 失衡。

## 2. 目标 / 非目标

### 目标
1. sink-discovery + source-discovery 从 per-function → **文件级聚合**,大幅减少 LLM 调用次数(569 函数 → 文件 chunk 数)。
2. 调整 timeout(`per_call_timeout` + activity `start_to_close_timeout`),容下文件级(更重的单次)+ 三阶段累加。
3. 让 `run_code_index` 在大仓下不再撞 activity timeout。
4. 守住双轨铁律与降级契约(见 §3.4)。

### 非目标
- **不改 taint-analysis**(`analyze_taint_llm`):taint 是 intra-procedural source→sink **流**分析,对象是流(多对多),跨函数无意义,文件级聚合不适用。本次不动(§3.3)。
- **不改并发默认**(`SHANNON_MAX_CONCURRENT=3`):用户决策"先纯聚合 + timeout,跑实测再定";并发调整留作实测后的后续杠杆(§8)。
- 不改双轨合并、`chain_verdict`、source/sink 规则库本身。

## 3. 设计

### 3.1 文件级聚合(核心,sink + source 同构一起改)

**分组**:`discover_sinks_llm` / `discover_sources_llm` 的分组键 `block.id`(函数)→ `file_path`(文件)。同文件所有可疑 call/候选 → 一组 → 一次 LLM 调用。`map_llm_with_bounds` 框架不动(item 从"函数组"变"文件 chunk")。

**prompt(多函数文件级)**:
- sink:该文件所有"含可疑 call 的函数"源码(按 block 去重)+ 该文件全部可疑 call 列表(`call_ref` + callee/receiver + args)。LLM 一次判全文件 call,返回 JSON 数组,按 `call_ref` 归位。
- source:该文件所有候选函数源码(按 block 去重)。LLM 一次判全文件 source 字段。
- 收益同 source:同文件函数互相可见,上下文更全(质量可能略升)。

**大文件兜底(chunking,关键)**:
- 单文件可疑函数多/源码长 → prompt 可能超 LLM context。兜底:估算拼接后 token(源码字符数粗估),超 `CHUNK_TOKEN_THRESHOLD`(默认 ~12K token,留 response 余量)→ **按函数分 chunk**,每 chunk 一次调用。
- 即分组键是文件,文件过大时内部按函数拆分。保证:小文件 1 次(聚合收益)、大文件安全分批(不爆 context)。

**verdict 解析(基本不变)**:
- sink:按 `call_ref`(`callee:line`,文件内 line 唯一)归位 → `SinkCallSite`。
- source:按 `field` + `line` 归位 → `SourcePoint`。

**进度**:`emitter` total = chunk 数(文件级 + 拆分)。

### 3.2 timeout 调整

| 参数 | 现状 | 调整 | 理由 |
|---|---|---|---|
| `per_call_timeout`(单次 LLM) | 60s | **120s** | 文件级 prompt 更重,单次响应更慢,60s 易误触发降级 |
| activity `start_to_close_timeout`(`workflows.py:129`) | 10min | **20min** | sink+source+taint 三阶段累加,文件级聚合后仍偏紧 |

- `per_call_timeout`:**局部覆盖**(在 `discover_sinks_llm`/`discover_sources_llm` 调 `map_llm_with_bounds` 时显式传 `per_call_timeout=120`),**不动** `concurrency.py` 全局默认 —— 精准、不影响 taint(taint 保持 60s 够)。
- activity timeout:改 `workflows.py:129` `minutes=10` → `minutes=20`。
- 均可经 env 覆盖。

### 3.3 taint-analysis 不动

`analyze_taint_llm` 保持 per-function。理由:taint 是 intra-procedural source→sink **流**分析,对象是流(source/sink 多对多),跨函数无意义 → 文件级聚合不适用。本次不改;若实测 ⑤ taint 也慢,单独处理(§8)。

### 3.4 守护的不变量

- **双轨铁律**:GitNexus 轨 LLM 补召回产物(软 sink/source)仍只进 `chain_verdict`/合并,**不喂 LLM 主轨 prompt**。本次只改粒度,不改产物去向。
- **降级契约**:LLM 不可用/超时/不可解析 → 该文件 chunk 跳过(`map_llm_with_bounds._Skip`),返回空,降级到纯规则 + `is_entry_hint`。不变。
- **`map_llm_with_bounds` 复用**:不改框架,只改 item 粒度和 `fn`。

## 4. 代码改动点

- `packages/core/src/shannon_core/code_index/sink_discovery_llm.py`:
  - `discover_sinks_llm`:`by_func` → `by_file`;`_discover_one` 改文件级 prompt;加 chunking;emitter total 改 chunk 数;调 `map_llm_with_bounds` 传 `per_call_timeout=120`。
  - `_build_discovery_prompt` + `_DISCOVERY_PROMPT_TMPL`:扩展多函数(文件路径 + 多函数源码 + 全文件可疑 call)。
  - 新增 `_chunk_file_by_token`(或类似):按 token 阈值分 chunk。
- `packages/core/src/shannon_core/code_index/source_discovery_llm.py`:同构改造(`by_func` → `by_file`;`_build_prompt` 多函数;chunking;emitter;`per_call_timeout=120`)。
- `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:129`:`minutes=10` → `minutes=20`。

## 5. 测试(TDD)

- `sink_discovery_llm`:
  - 文件分组:同文件多 `SuspiciousCall` → 一组。
  - 多函数 prompt:含该文件所有可疑函数源码。
  - verdict 归位:文件级 JSON 按 `call_ref` 归位多 sink。
  - chunking:超 `CHUNK_TOKEN_THRESHOLD` → 分 chunk;小文件 1 chunk。
  - 降级:单 chunk 超时/失败 → 跳过,不影响其他(map 行为)。
- `source_discovery_llm`:同构测试。
- 现有 per-function 测试适配为文件级。
- workflows.py timeout:配置值,无需单测(可加断言防回退)。

## 6. 验证计划

- 单测全绿。
- 真机:跑 `kol_mapping_service` 白盒扫描(`GITNEXUS_LLM` 开),记录:
  - sink/source 文件级聚合后调用次数(569 函数 → ? 文件 chunk)。
  - `run_code_index` 总耗时(目标 < 20min activity)。
  - 是否仍撞 timeout。
  - sink/source 召回数 vs 改前(质量回归检查)。
- 不够(<20min 仍超)→ 后续加杠杆(提并发,§8)。

## 7. 风险 / trade-off

- **文件级 prompt 更大**:单次 token↑、响应更慢 → `per_call_timeout` 同步加(60→120)补偿。
- **chunking 增加复杂度**:但防 context 爆,必要;阈值可调。
- **召回质量**:同文件函数互见可能略升(更多上下文),也可能因 prompt 变长 LLM 注意力分散略降 → 实测对比(§6)。
- **activity timeout 加到 20min**:延长单次扫描最坏等待;可接受(治本前权衡)。

## 8. 后续(follow-up,不在本次)

- **并发调整**(`SHANNON_MAX_CONCURRENT` 3→8~10):实测后若"文件级 + timeout"仍不够,再加。
- **taint-analysis 粒度优化**:若 ⑤ taint 实测也慢,单独看(per-function 是否够,或按 source/sink 调整)。
