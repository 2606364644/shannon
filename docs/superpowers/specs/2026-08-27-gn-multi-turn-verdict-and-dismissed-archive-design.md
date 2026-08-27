# GN 轨多轮深判 + 非漏洞留档 + 黑盒非漏洞步骤 —— 设计 spec

日期：2026-08-27
状态：已确认（用户口径锁定）
分支：feat/fork-py

## 0. 背景与根因

用户观察：「报告展示的漏洞质量低」。排查实证出一条完整根因链：

1. **非漏洞卡混入报告（最大单一根因）**：GN 轨 builder 把每条候选链都
   append 进 findings（`code_index/vuln_chain_builders/injection_builder.py:74`，
   含 `verdict="not_vulnerable"` / `"needs_review"` 的）→ merge 是 union 不丢卡
   （`code_index/dual_track_merger.py:271-279`，GN-only 非漏洞卡 verdict 被改写成
   `"safe"` 但保留）→ `report_data_builder` 对 queue 卡零过滤全进报告
   （`services/report_data_builder.py:370-374`）。
2. **chain_verdict 判定形态上限低**：已是逐链判定（builder for 循环逐条调
   `judge_chain_verdict`），但单次调用、无工具、只看 builder 拼的链快照——
   不能自己读码验证 sanitize 是否真拦住、sink 实参是否真是危险值、可达性
   是否成立。误报与 needs_review 偏多。
3. **discovery 同形态**：sink/source/storage discovery 是 chunk 级单次调用、
   塞源码快照，不能跨文件追 callee 定义与框架包装。

用户决策（口径锁定，见 §1 口径表）：

- chain_verdict 与 sink/source/storage discovery 全部换成 Agent 多轮
  （质量优先；chain_verdict 逐条多轮）。
- 白盒判非漏洞 → 留档（人工分析用），不进报告。
- 黑盒验证成败都进黑盒报告，带分点验证步骤；黑盒结果不回写白盒。
- 白盒拿不准（needs_review / unadjudicated）保守进报告，带待复核标识。

顺带修复（同主题「成本可见性」，2026-08-27 计费检查发现）：轻量单次调用
cost 系统性漏记（§7）。

## 1. 口径表（用户口径锁定）

| 判定结果 | 白盒 | 黑盒 |
|---|---|---|
| 是漏洞 / 验证成功（`exploited`） | 进白盒报告 | 进黑盒报告，带分点验证步骤（在途工作已覆盖） |
| 非漏洞 / 验证失败 | **留档**（GN `not_vulnerable` + LLM 轨 dismissed → `dismissed_findings.json`），不进报告 | **进黑盒报告**，带分点验证步骤 + 非漏洞原因（本设计补齐） |
| 拿不准 / 没判成（`needs_review` / `unadjudicated`） | 保守进白盒报告，带待复核标识 | —（黑盒 4 档 status 无此态） |

needs_review 两层语义（均保留在白盒报告，靠现有 confidence/verdict 字段
渲染标识，不加新前端）：

- 判定层 `verdict="needs_review"`：读完码仍拿不准（sanitize 部分覆盖、
  依赖运行时配置等）；`confidence="unadjudicated"`：判定通道失败（没判成）。
- 合并层 `confidence="needs_review"`：单轨卡缺第二轨背书（双轨都报才
  `high`）。

黑盒 4 档 status 语义：`exploited`（验证成功）/ `blocked_by_security`
（有漏洞证据但被安全控制挡住，仍算漏洞展示）/ `out_of_scope_internal`
（非漏洞判定）/ `false_positive`（非漏洞判定）。

## 2. 总体数据流（改造后）

```
GN 轨（inj/xss/ssrf）
  候选链 N 条 ──逐条──▶ 多轮 verdict agent（grep/read 验证）
                          ├─ vulnerable / needs_review / unadjudicated
                          │    └─▶ <vuln>_gitnexus_queue.json（现状链路不动）
                          └─ not_vulnerable ─▶ dismissed_findings.json（新，不进 queue）

LLM 轨（白盒 vuln agent）
  探索 ─▶ submit_finding（确认漏洞 → queue，现状）
       ─▶ submit_dismissed（新 collector：排除候选+理由+证据 → dismissed_findings.json）

merge（union + verdict OR，现状零改动）
  └─▶ SSOT {vc}_exploitation_queue.json（输入已无非漏洞卡，输出天然干净）
        ├─▶ 白盒报告（不再混入 verdict=safe 卡）
        └─▶ 黑盒输入 queue（黑盒不再验证白盒已判非漏洞的卡，省 token）

黑盒 exploit agent
  └─ verdicts：exploited / blocked_by_security / false_positive /
     out_of_scope_internal 全部进黑盒报告（成败标识区分；
     false_positive / out_of_scope_internal 本设计补分点步骤 + 原因）
     不回写白盒。
```

留档只收白盒两轨非漏洞；黑盒非漏洞在黑盒报告展示，不进留档。

## 3. chain_verdict 逐条多轮深判

- **载体**：复用 `run_gitnexus_verdict_agent`
  （`whitebox/pipeline/activities.py:2481`）——继承 22269e4a 已修的记账
  （`end_agent` 进 session metrics）、工具审计（SessionToolAuditLogger）、
  agent_name 唯一化、max_turns env。
- **依赖方向**：builders 在 core 层不能 import whitebox——沿用现有
  `llm_client` 注入模式，改为注入 **verdict agent runner 回调**：
  `async (prompt, *, output_format, agent_name) -> ClaudeRunResult`。
  whitebox activity 侧构造闭包（带 repo_path / provider_config /
  audit_session）。
- **prompt**：新文件 `prompts/chain-verdict-agent.txt`——保留现有判定要点
  （sanitize 有效性、sink 实参、可达性、witness、placement 输出），去掉
  "给你链快照你判定"的单次假设，改为"这是候选链，自己去 repo 里
  grep/read 验证每一步，再给判定"。
- **输出 schema 不变**（`CHAIN_VERDICT_SCHEMA`：verdict / witness_payload /
  evidence_chain / mismatch_reason / confidence / title /
  source_param_location）——下游 builder → merge → PoC 零改动。
- **needs_review / unadjudicated**：多轮读码后仍不确定 → `needs_review`
  （保守进 queue，进报告带待复核标识）；agent 调用失败 → 保守
  `unadjudicated`（现语义）。`_retry_verdict_parse` 有界重试保留。
- **护栏**：`SUPERNOVA_CHAIN_VERDICT_MAX_AGENTS`（默认 200）——候选链数
  超限时，超出的链直接 `unadjudicated` 保守进 queue（与口径表一致：没判成
  不等于非漏洞，不静默丢、不烧 token）+ warning + 报告 QA 标注
  「N 条链超限未判定」，防大仓 runaway。
- **降级**：`SUPERNOVA_GITNEXUS_LLM_ENABLED=0` 全关（现语义不变，纯规则
  兜底路径不动）。
- **second_order_builder** 复用 `judge_chain_verdict`，同一改造自动覆盖。

## 4. 非漏洞留档 dismissed_findings.json

- **路径**：`deliverables/intermediate/dismissed_findings.json`（tiering：
  中间产物桶）。
- **schema**：

```json
{"dismissed": [{
  "ID": "INJ-GN-07",
  "source_track": "gitnexus | llm",
  "vuln_class": "injection",
  "title": "...",
  "dismiss_reason": "...",
  "evidence": "...",
  "confidence": "...",
  "source": "...",
  "sink_call": "...",
  "dismissed_at_stage": "chain-verdict | llm-exploration"
}]}
```

- **写入点两处**（黑盒不写，报告已展示）：
  - GN builder：判非分流时写（`source_track=gitnexus`，
    `dismissed_at_stage=chain-verdict`）。
  - LLM 轨 activity 出口：collector 收集的 dismissed 条目写
    （`source_track=llm`，`dismissed_at_stage=llm-exploration`）。
  - 两处同文件追加写（同 activity 内聚合后一次落盘，避免并发写冲突）。
- **报告剔除双保险**：
  - 主修复：builder 分流——`<vuln>_gitnexus_queue.json` 只写 vulnerable +
    needs_review + unadjudicated，`not_vulnerable` 只进留档。merge 逻辑
    零改动（union 输入里已无非漏洞卡）。
  - 防线：`report_data_builder` 加防御性过滤（`verdict == "not_vulnerable"`
    跳过）——旧 session 产物 / schema 回归兜底，契约测试锁定。
- **黑盒输入变化**：SSOT 不再含白盒非漏洞卡 → 黑盒不再验证它们（省
  token，行为正确化）。

## 5. discovery 多轮化（sink / source / storage）

- 每 **chunk 一个多轮 agent**（同一注入模式 + `run_gitnexus_verdict_agent`
  载体），agent 名 `gn-discovery-{kind}-{chunk:03d}`。
- **prompt 瘦身**：不再塞 chunk 全部函数源码快照——只给可疑 call 清单
  （file:line / receiver / 表达式 / 所属函数名），agent 自己 `Read` 源码、
  跨文件追 callee 定义与框架包装。
- **产物形态不变**：`SinkCallSite`（`rule_id="llm-discovered"`）、
  source/storage 候选、`rule_gap_report.json`——下游确定性层零改动。
  output schema 沿用各 discovery 现有 schema。
- **并发**：沿用 `map_llm_with_bounds` semaphore（`get_max_concurrent`
  默认 3）。
- **超时**：新 env `SUPERNOVA_GN_DISCOVERY_AGENT_TIMEOUT`（默认 300s/
  chunk）——原 `SUPERNOVA_LLM_PER_CALL_TIMEOUT`（60s）对多轮失效；该 env
  保留给仍为单次的 taint 分析。
- **降级**：总开关关 → 纯规则 + `is_entry_hint`（现语义）；单 chunk agent
  失败 → 该 chunk 降纯规则（现逻辑）。

## 6. LLM 轨 submit_dismissed

- 5 个 `vuln-*.txt` prompt（injection/xss/ssrf/auth/authz）加输出契约：
  探索过但排除的候选必须上交——`id` / `title` / `reason` / `evidence` /
  `code_location`，并声明"这是分析留档不是免责通道——排除必须有具体
  代码证据"。
- 新 collector（对齐 `add_exploit` 的 L0 归一 → L1 pydantic → L2 id 校验 →
  L3 去重模式），独立 section_key（如 `dismissed`），不参与
  `submit_finding` 的 roster 对账（保持简单）。
- activity 出口写 `dismissed_findings.json`（§4）。
- **铁律合规**：只加输出通道，不引任何确定性产物；新 prompt 不
  `@include` 确定性产物——`test_static_dataflow_hints_decoupling` 照常
  锁定（对 `chain-verdict-agent.txt` 一并适用）。

## 7. 黑盒非漏洞 verdict 带步骤

- `models/exploit_verdict_schemas.py`：`false_positive` /
  `out_of_scope_internal` verdict 加 `exploitation_steps: ExploitStep[]`
  （复用在途工作的 `ExploitStep{action, command, result}` 结构）。
- `prompts/*-exploit.txt`：false_positive / out_of_scope_internal 的 shape
  加 steps 说明（与在途 exploited steps 契约同风格）。
- `renderers/exploit.py` other 节：渲染分点步骤 + 非漏洞原因标识。
- 与在途工作（exploited 步骤结构化）同主题、无冲突：在途改 exploited，
  本设计扩展到非漏洞 verdict。

## 8. 记账顺带修（2026-08-27 计费检查发现）

多轮化后 chain_verdict / discovery 走 runner 自动记账（audit_session 注入）。
剩余轻量单次调用按 22269e4a 模式补记账：

- `_make_track_parity_client`（配对归并 + light 档补全）
- `poc_generator.llm_fill_gaps`（gap-fill）
- write_structured_poc 的 expected_response 补齐
  （`whitebox/pipeline/activities.py:1957`）
- report_polish 执行摘要（`activities.py:2193`）
- `_make_recon_summary_llm_client`（`activities.py:280`）

模式：client 工厂接受可选 audit_session，闭包内聚合累计器
（cost/tokens/调用数），activity 出口一次 `end_agent` 记总账（agent_name
如 `track-parity` / `poc-gapfill` / `expected-response` / `report-summary` /
`recon-summary`），`AGENT_PHASE_MAP` 补对应 phase 映射。

## 9. 测试与契约锁定

- **builder 分流契约**：GN queue 不含 `not_vulnerable` 卡；留档含之且
  字段完整。
- **report_data_builder 防线**：`not_vulnerable` 卡被过滤。
- **dismissed collector**：L0-L3 校验测试（对齐 exploit verdict validator
  测试模式）。
- **decoupling 铁律**：`chain-verdict-agent.txt` / `vuln-*.txt` 改动过
  `test_static_dataflow_hints_decoupling`。
- **runner 注入**：mock `run_claude_prompt` 验证 agent_name / max_turns /
  记账（成功/失败两路）——对齐 22269e4a 的 `gitnexus_verdict_agent` 测试
  模式。
- **护栏**：候选链超限 → 超出链 unadjudicated 进留档 + warning。
- **黑盒非漏洞步骤**：schema 校验 + renderer 渲染测试。
- **CLAUDE.md §1 更新**：chain_verdict 从"轻量单次"改"逐条多轮深判"
  （GN 轨描述、计费描述同步）。

## 10. 不做（scope 边界）

- taint 分析（`analyze_taint_llm`）保持单次（用户未点名；per-function
  批量分类器，多轮化成本不可行）。
- 融合报告（`report_fusion` 在途工作，本设计不碰）。
- 前端零改动（剔除在数据层，ReportView 读 report_data.json 自动跟随）。
- needs_review 展示不加新前端标识（现有 confidence/verdict 渲染承担）。
- 跨轨分歧配对（GN 非漏洞卡与 LLM 漏洞卡的分歧记录）——YAGNI。
