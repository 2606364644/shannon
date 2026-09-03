# 双轨分析：GitNexus 轨 / 纯 LLM 轨

supernova 的 injection/XSS/SSRF 白盒召回采用双轨模型。两轨在漏洞分析阶段之前可以共享 pre-recon/recon 等基础 LLM 产物，但 **vuln 分析输入必须保持独立**；最终只在 merger 做 verdict OR 与展示层去重。

## 最高不变量

> 确定性层产物（`parameter_graph.json`、`SinkCallSite`、候选链、chain verdict 结果等）不得作为 hints 注入纯 LLM vuln agent prompt。

原因：

- LLM 轨对齐原始 shannon 的自给自足方法论：自己 grep/read/委派子代理追链。
- GitNexus 可能超时、索引失败或规则缺口明显；若 LLM 轨依赖这些 hints，会一起失效。
- 双轨价值来自独立召回。喂 hints 会让 LLM 轨重复确定性偏差，而不是补盲区。

反向补充是允许且明确设计的：pre-recon LLM 发现的入口/sink 报告可在 pre-recon 后融合进 code index，服务 GitNexus 轨；这不等于把确定性 vuln 链喂给 LLM vuln prompt。`static_dataflow_hints` 桥梁已拆除，`packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py` 锁定不得重建。

## GitNexus 轨

GitNexus 轨是“确定性索引 + 轨内 LLM 补充/判定”的可靠兜底：

```text
tree-sitter 函数/调用解析
  + GitNexus process traces
  + source/sink/storage 规则
  + GitNexus 轨内 LLM source/sink/taint discovery
  -> ParameterPropagationGraph
  -> injection/xss/ssrf/second-order builders
  -> 每条候选链多轮 verdict agent
  -> <vuln>_gitnexus_queue.json
  -> dual-track merger
```

它不是纯静态扫描：LLM 可在 GitNexus 轨内部发现软 source/sink、分析函数内 taint、深判每条候选链。但这些 LLM 输入来自 GitNexus 轨自身候选与源码，不会写入纯 LLM vuln prompt。

关键产物：

- `code_index.json`
- `parameter_graph.json`
- `<vuln>_chain_verdicts.json`
- `<vuln>_gitnexus_queue.json`
- `chain_verdict_checkpoint_<vuln>.json`
- `rule_gap_report.json`
- `gitnexus_track_status.json`

## 纯 LLM 轨

每个 `prompts/vuln-*.txt` agent 独立分析一类漏洞：

- 输入：目标、recon 共享上下文、增量 MR 的 git diff 上下文（只含 git 派生物）。
- 工具：自己 grep/read/browser-safe 代码路径规则，并强烈要求委派 Task/Agent 子代理。
- 输出：collector 工具逐条提交结构化 finding，最终形成 `<vuln>_exploitation_queue.json`。
- 不读：`code_index.json`、`parameter_graph.json`、GitNexus queue、chain verdict、sink/source hints。

auth 是纯 LLM 轨；authz 的 Horizontal/Vertical/Context 也由 LLM 轨覆盖，同时另有 IDOR GitNexus 轨做独立兜底。

## auth / authz 特例

auth/authz 是 missing-control 类问题，不能套 source→sink taint 规则：

| 类 | 纯 LLM 轨 | GitNexus 轨 |
|---|---|---|
| injection/xss/ssrf | `vuln-*.txt` | code index + chain verdict |
| auth | `vuln-auth.txt` 9 类方法论 | 无 auth GitNexus 轨 |
| authz | `vuln-authz.txt` 覆盖 Horizontal/Vertical/Context | IDOR dominance/framework 候选 + 多轮 agent；0 候选时自主探索 |

历史上的 auth config scanner / auth GitNexus 深轨已删除，原因包括违反“确定性产物不喂 LLM vuln prompt”与误将 CORS 等配置问题越界进 auth。不要在未重新设计边界前恢复。

## 开关语义

`SUPERNOVA_LLM_TRACK_ENABLED`：

- 默认 `1`：全部 selected vuln agent 运行，形成双轨。
- `0`：只关闭 inj/xss/ssrf 的 `vuln-*.txt` agent，由 GitNexus chain verdict 兜底。
- 无论开关如何，pre-recon、recon、auth、authz LLM 保留。这些是 authz/auth 效果和角色模型的必要输入。

`SUPERNOVA_GITNEXUS_LLM_ENABLED`：

- 默认 `1`：允许 GitNexus 轨内软 sink/source、taint、chain verdict agent。
- `0`：保留规则/传播/entry hint 确定性部分，关闭 GitNexus 轨 LLM 补充与深判。

关轨 fail-fast 语义：当 `SUPERNOVA_LLM_TRACK_ENABLED=0` 且 inj/xss/ssrf GitNexus 轨失败时，扫描终止，因为没有兜底；开轨时对应类标红继续，由 LLM 轨结果保底。

## 合并与状态

`run_merge_dual_track_queues` 遍历 injection/xss/ssrf/authz/auth：

1. 读取 `<vuln>_exploitation_queue.json` 与 `<vuln>_gitnexus_queue.json`，优先 `intermediate/`，旧平铺结构兜底。
2. LLM queue 缺失时仍合并 GitNexus-only；两轨都空则跳过。
3. merger 做跨轨归一去重、verdict OR、字段融合与来源标注。
4. 高置信配对可把 GitNexus 卡并入/挂靠 LLM 卡；配对是展示层去重，不改变 verdict。
5. 输出合并后的 exploitation queue，并保留原始 LLM queue 备份，保证 resume 幂等。

`externally_exploitable` 是网络可达性标签，不是认证要求，也不能被 verdict OR 覆盖。authz 合并时对两轨 reachability 标签取 OR，但仍保持该语义。

`gitnexus_track_status.json` 只用于编排、merger 标红和报告，不进入 LLM vuln prompt。failed 类自然没有 GitNexus findings，不能伪造兜底成功。

## 容量与恢复铁律

- GitNexus verdict 单链通常 10–60 秒；activity 窗口必须按 `链数 ÷ 并发 × 单链耗时` 估算。
- 全量扫描默认 15 分钟窗口；MR 增量由 `IncrementalScope.verdict_timeout_minutes` 重估。
- `VerdictCheckpoint` 逐链落盘，指纹为 `(vuln_class, flow_id, sink_call_site_id, source_param)`；重试只补未判链。
- unadjudicated 是保守占位，不是真判定，不写 checkpoint。
- GitNexus 轨失败不得打 step cache 完成标记，避免 resume 直接复用失败结果。

详细机制见 [调用链研判](call-chain-verdict.md)。

## 常见误判与排查

| 现象 | 先查 |
|---|---|
| LLM 轨没发现，GitNexus 轨也没有 | 分轨日志/状态：code index 是否失败、chain verdict 是否 0 候选 |
| GitNexus-only 卡报告缺失 | queue 是否在 `intermediate/`，merger 是否运行，track status 是否标红 |
| LLM 轨重复依赖静态结果 | prompt partial / `@include`，跑 decoupling 测试 |
| 关 LLM 轨后 authz/auth 消失 | 不要用该开关关闭 pre-recon/recon/auth/authz |
| 重试重复烧 token | checkpoint 文件、指纹字段、unadjudicated 是否被误缓存 |

## 验证入口

- `packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py`
- `packages/core/tests/code_index/test_dual_track_chain_integration.py`
- `packages/core/tests/code_index/test_chain_verdict.py`
- `packages/whitebox/tests/test_run_gitnexus_chain_verdict.py`
- `packages/whitebox/tests/test_gitnexus_chain_verdict_failfast.py`
