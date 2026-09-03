# 调用链研判

调用链研判是 GitNexus 轨的终审层。索引和传播只提供候选链，不直接判定漏洞；每条候选必须由多轮 verdict agent 在真实仓库中 read/grep 验证后输出结构化结论。

2026-09-01 后，旧的轻量单次 `llm_client` 判定路径已移除。生产路径只有 `run_gitnexus_verdict_agent` 多轮 agent；测试或极少数内部工具不应再假设存在单次判定通道。

## 候选构造

`chain_verdict.extract_candidate_chains` 依据漏洞类路由 `TaintFlow`：

- injection：`SQL_VALUE`、`SQL_IDENTIFIER`、`CMD_ARGUMENT`、`FILE_PATH`、`TEMPLATE_EXPR`、`DESERIALIZE`
- SSRF：`URL`
- XSS：按 `SinkCallSite.category == XSS`，render context 从 `sink_subtype` 派生
- second-order：读侧链作为判定链，写侧 tainted 语义参与 finding 组装

候选链包含：

- source 参数/type
- sink 精确 id、槽位与危险实参
- propagation steps
- deterministic sanitizer/encoder 注解
- post-sanitize concatenation 检测结果
- direction hint 与 render context

这些字段在 prompt 中明确标为“待验证线索，不是 ground truth”。

## 多轮 verdict agent 协议

`run_gitnexus_verdict_agent` 包装统一 Agent 引擎，具备工具与逐链审计。协议要求：

1. 读 sink 文件/行，确认污点表达式确实进入危险槽位。
2. grep/read 每个声称的 sanitizer，判断它是否匹配该槽位语义，且消毒后没有再拼接。
3. 读 handler/route 注册，确认入口和参数绑定可达。
4. 只验证本链，不重跑完整漏洞方法论。
5. 输出紧凑 JSON：

```json
{
  "verdict": "safe|vulnerable|needs_review",
  "witness_payload": "minimal attack input or null",
  "evidence_chain": "source->sink with file:line citations",
  "mismatch_reason": "optional",
  "confidence": "high|medium|low",
  "title": "one-line descriptive title",
  "source_param_location": "body|query|null"
}
```

`witness_payload` 在 vulnerable 时必须给最小具体攻击输入；safe 时为 null。`evidence_chain` 必须引用 agent 实际读到的 file:line，不能复述候选快照。`source_param_location` 服务后续 PoC 参数位置，缺失时退回 source_type 的确定性映射（body/form→body，query→query）。

输出 JSON 无效时，会以同形态 verdict agent 追加 JSON-only 提醒重跑；耗尽后落保守 `unadjudicated`，不做文本格式伪修复。

## 并发、预算与超时

| 配置 | 默认 | 语义 |
|---|---:|---|
| `SUPERNOVA_CHAIN_VERDICT_CONCURRENCY` | 4 | 多 builder 共享的全局并发信号量 |
| `SUPERNOVA_CHAIN_VERDICT_MAX_AGENTS` | 200 | 真实判定链数护栏 |
| `SUPERNOVA_CHAIN_VERDICT_MAX_TURNS` | 30 | inj/xss/ssrf 主链多轮上限 |
| `SUPERNOVA_GITNEXUS_VERDICT_MAX_TURNS` | 30 | authz judge / discovery 等同族 agent |
| `SUPERNOVA_PRESUMED_SAFE_MAX_PER_SINK` | 见配置 | presumed-safe 每 sink 候选上限 |

超出 `MAX_AGENTS` 的候选生成 `unadjudicated`，保守保留给人工复核，不消耗 LLM 调用。

容量铁律：单链 10–60 秒，`start_to_close_timeout` 必须按 `链数 ÷ 并发 × 单链耗时` 重估。全量扫描默认 15 分钟；MR 增量由 scope 计算最小 5 分钟窗口。修改链数、并发、单轮耗时后必须同步评估窗口，否则会出现三次重试全部超时的白盒失败。

## Checkpoint

`VerdictCheckpoint` 是逐链恢复机制：

- 指纹：SHA1 of `vuln_class|flow_id|sink_call_site_id|source_param`。
- 文件：`whitebox/intermediate/chain_verdict_checkpoint_<vuln>.json`。
- 每条真实判定完成后原子写盘。
- retry/resume 先查 checkpoint，命中直接返回，不再调 LLM。
- `unadjudicated`、预算占位、失败结果、畸形结果不写入。
- 文件损坏、条目 schema 不匹配、写盘失败都降级为 miss 并重判；checkpoint 永远不能阻塞主流程或造成循环重试。
- 缓存命中不消耗 `MAX_AGENTS` 预算。

这个设计的直接目标是避免 NodeGoat 事故中同一批链在 activity timeout/retry 下被重复判定多次。

## verdict 分流

chain verdict builder 完成后：

- `safe`：不进 `<vuln>_gitnexus_queue.json` / 报告 / 黑盒输入，转入 `dismissed_findings.json` 留档。
- `needs_review`：保守保留。
- `unadjudicated`：保守保留，confidence 显式为 `unadjudicated`。
- `vulnerable`：进入 queue；presumed-safe 来源的 vulnerable 卡不进主 queue，只在 `chain_verdicts.json` 数据流视图中保留，避免“初判已否定但 agent 翻案”的卡直接打扰主报告。
- 全量 findings（含 safe）写入 `<vuln>_chain_verdicts.json`，供数据流视图展示被剪断的安全枝。
- endpoint 缺失时由 endpoint backfill 基于 LLM 提名 + adjudicated route 白名单唯一命中回填。

## authz GitNexus 特判

authz 不是 taint source→sink，走独立候选与判定：

1. `build_authz_gitnexus_track` 从 code index 构造 dominance 候选：handler→side-effect sink 路径未检出 ownership 谓词。
2. 从 framework analysis 构造自动生成端点候选（默认无 ownership）。
3. 候选 > 0：渲染候选表，交给多轮 `authz_gitnexus_judge` agent 读 hook/middleware/ORM 谓词确认。
4. 候选 = 0：不写空队列了事，改用 `authz_gitnexus_explore` agent 自主搜索 IDOR。
5. 输出 `source_track="gitnexus"` 的 authz queue，与纯 LLM authz 轨合并。
6. explore 发现的软候选强制 `needs_review=True`。

authz GitNexus 轨 activity 有自己的 30 分钟活动窗口，并有 step cache；只有干净完成才缓存。

## 与 merger 的关系

chain verdict 不直接改写 LLM 轨。`run_merge_dual_track_queues` 在两轨都完成后执行 verdict OR、去重与字段融合；即使 GitNexus track status 为 failed，也只影响标红和兜底策略，不吞掉 LLM 轨结果。详见 [双轨分析](dual-track-analysis.md)。

## 验证入口

- `packages/core/tests/code_index/test_chain_verdict.py`
- `packages/core/tests/code_index/test_verdict_checkpoint.py`
- `packages/whitebox/tests/test_run_gitnexus_chain_verdict.py`
- `packages/whitebox/tests/test_chain_verdict_checkpoint_wiring.py`
- `packages/whitebox/tests/pipeline/test_gitnexus_verdict_agent.py`
- `packages/whitebox/tests/test_verdict_agent_delivery_rules.py`
