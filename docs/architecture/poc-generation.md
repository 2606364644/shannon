# PoC 生成

PoC 生成发生在白盒双轨合并之后、报告组装之前。它不重新判定漏洞，而是把最终 finding 翻译成人类可复制、可理解、可审计的攻击交付方式；动态验证由黑盒 exploitation 阶段负责。

## 定位与原则

1. **Verdict final**：PoC agent 是 Production Officer，不是 adjudicator；禁止重判、重分类或降级 finding。
2. **Correctness first**：端点真实存在、参数真的在该位置被消费、触发模型匹配漏洞机制，优先于 curl 格式美观。
3. **诚实缺失**：无法正确生成时不降级到确定性模板，不伪造 curl；卡片保持无 `report_poc` 并记录缺失。
4. **文本透传**：collector/渲染层不改写、不重排 agent 产出的 curl/raw_http/steps。
5. **已有即 checkpoint**：已有 `report_poc` 的卡默认跳过，不重打、不覆写。

## 输入与时机

Workflow 时序：

```text
dual-track merge
  -> GN finding enrichment
  -> endpoint enrichment
  -> dataflow view
  -> attack chains
  -> write_agent_poc
  -> assemble report_data
  -> report polish / rework
  -> markdown + poc_collection export
```

`write_agent_poc` 读取合并后的 `<vuln>_exploitation_queue.json`，因此输入同时包含：

- 纯 LLM track 卡
- GitNexus-only 卡
- both 卡
- auth / authz 卡
- second-order / stored 卡

## Agent 流程

`prompts/poc-agent.txt` 要求 agent 在仓库中验证：

- 目标是后端 HTTP endpoint 还是 SPA 前端路由；不能向 vue/react 页面路径 POST 表单。
- 参数实际由 query/body/path 哪个位置消费。
- 触发模型是 reflected、stored plant-then-trigger，还是 DOM/browser render。
- 认证载体按 finding 注解使用 `<AUTH_TOKEN>` 或 `<SESSION_COOKIE>`，不凭感觉添加 Bearer。
- 存储点在外部服务且不可控时，必须在 notes 中说明 controllability unverified。

agent 可多轮 grep/read 源码。与早期确定性拼装不同，`build_structured_poc` 已退役，当前 PoC 文本由 agent 直接生成。

## 分片与并发

为避免一个 vuln class 一锅端造成超大 prompt / 输出截断 / 服务过载，`_write_agent_pocs` 按以下策略分片：

1. 每类 queue 分开处理。
2. 目标卡按 sink 文件聚类：优先 `sink_call` / `sink_function` 中的 `file:line`，其次 `dataflow_steps` 末步文件。
3. 同文件卡片共享路由/handler/middleware 读码上下文。
4. 每片默认最多 3 张卡，超限同文件再裂片。
5. 五个 vuln class 与多个片共享同一个并发信号量。

| 配置 | 默认 | 语义 |
|---|---:|---|
| `SUPERNOVA_POC_SHARD_MAX_CARDS` | 3 | 每片最大卡数 |
| `SUPERNOVA_POC_AGENT_CONCURRENCY` | 3 | 类间 + 片间共享并发 |
| `SUPERNOVA_POC_AGENT_MAX_TURNS` | 40 | 每片多轮上限 |

容量估算遵循 `片数 ÷ 并发 × 单片耗时`；调整片大小或 turn 预算时必须同步评估 activity 窗口。

## 输出契约

每张卡输出：

| 字段 | 说明 |
|---|---|
| `vulnerability_id` | 必须存在于该片 queue |
| `curl` | 可复制的完整 HTTP 交付命令；SPA/DOM 场景可省略 |
| `raw_http` | 与 curl 同一请求的 Burp Repeater 原始请求 |
| `steps` | 有序多步触发说明（stored XSS、DOM XSS、多身份流程） |
| `preconditions` | 登录、角色、外部存储点等前置条件 |
| `expected_response` | 判断成功的观察特征 |
| `self_check` | `pass` / `fail`；非显式 pass 一律保守 fail |
| `notes` | 缺失原因、外部依赖、SPA/认证注意事项 |

`self_check` 的主位是正确性：必须实际读到 endpoint、参数消费点和触发路径；shell quoting 只是次要检查。

## Collector 校验

`collectors/poc.py` 提供 L0–L3 校验：

- **L0 lenient normalize**：steps 归一为字符串列表；`self_check` 非显式 `pass` 即 `fail`。
- **L1 必填与类型**：ID 非空字符串；至少有 curl/raw_http/steps/notes 之一；声明为字符串的字段不能传 dict/list。
- **L2 防幻觉**：`vulnerability_id` 必须在当前 queue ID 集合内。
- **L3 去重**：同一 ID 首份有效输出生效。
- 校验层不修改 curl/raw_http 内容。

若多轮 agent 因 turn 上限被截断且 structured output 缺失，`extract_pocs_payload` 会尝试从 final text 中提取完整平衡 JSON 对象；只解析，不做内容级修复。解析失败则该片诚实缺失。

## 写回与报告

有效输出写回 queue 卡片的 `report_poc` 字段，然后：

- `report_data_builder` 将其转换为统一 `PocBlock`。
- `report_polish` 检查缺失/不完整 PoC，可仅针对缺失 ID 回炉，复用同一分片路径。
- `export_poc_collection(report_data)` 生成 `exploitable_poc_collection.md`。
- comprehensive report 的漏洞卡直接渲染 `PocBlock`，保持前端/Markdown 同构。

白盒 `PocBlock` 不再使用旧 `witness_payload` 字段；chain verdict 的 witness 只是判定证据，不等于已验证 PoC。

## 与黑盒验证的关系

白盒 PoC 是“应如何触发”的交付说明。黑盒阶段会：

- 复用认证状态；
- 先做 endpoint live 验证；
- 由 exploit agent 在 live target 上执行/重放；
- 将实际证据写入 exploitation evidence 与黑盒 `report_data`。

黑盒动态结果可以修正白盒 PoC 的现实可达性，但不能在白盒阶段伪造“已验证”。详见 [黑盒验证](blackbox-verification.md)。

## 失败语义

- 单片失败：该片卡片保持无 `report_poc`，不影响其他片。
- 单类失败：不影响其他类。
- activity 失败：对主扫描 non-fatal，报告卡 POC 节缺失；Temporal cancellation 必须放行，不能被吞掉造成幽灵扫描。
- worker 未注册 `write_agent_poc` 属部署漂移，需 fail-fast 暴露，不能静默跳过。

## 验证入口

- `packages/core/tests/collectors/test_poc_collector.py`
- `packages/core/tests/prompts/test_poc_agent_prompt.py`
- `packages/whitebox/tests/test_write_agent_poc.py`
- `packages/whitebox/tests/test_poc_agent_sharding.py`
- `packages/whitebox/tests/test_run_report_polish.py`
