# Plan 4 接点设计增补（exploit agent append collector）

> 日期：2026-07-17 ｜ 分支：feat/fork-py ｜ 关联：[[pre-recon-md-deliverable-glm-forget-write]]、[[whitebox-exploitation-queue-persist-status]]、[[blackbox-exploit-verdict-drop-fix]]
> 父 spec：`2026-07-17-host-rendered-deliverables-design.md`（§6 Plan 4 = exploit）
> 状态：**改写 plan 4 的依据**。原 `2026-07-17-host-rendered-deliverables-plan-4.md` 系统性偏离本父 spec + 现有代码，本增补是修正后的接点设计。

## 0. 为什么需要本增补（原 plan 4 的偏离）

原 plan 4 的问题不是「exploit 用 append 语义」（这点对——exploit 产物是 list，append 自然，对齐 TS `getAll(): AddExploitInput[]`），而是**接点假设全部基于不存在的 Plan 1 骨架形态**：

| 维度 | 原 plan 4 假设 | 现有代码现实 |
|---|---|---|
| executor 接入 | `collector_spec` 参数 + `get_collector_spec()` + executor 按 isinstance 分支 | `make_collector(agent_name)` + `render_deliverable(agent_name, data)` 分发器（已落地） |
| renderer 签名 | `render_exploit_deliverable(vc, entries, id_to_type)` 双输入 | `render_deliverable(agent_name, data)` 单输入分发器（已落地） |
| bridge | 新建 `build_exploit_*` | generic `build_claude_mcp_server(collector)`/`build_openai_tools(collector)`（已落地，按 section_schemas 循环） |
| verdict 档位 | 2 档（exploited/blocked） | 4 档（exploited/blocked_by_security/out_of_scope_internal/false_positive，`exploit_verdict_schemas.py`） |
| queue schema | `{verdicts:[{vulnerability_id}]}` | `{vulnerabilities:[{ID, vulnerability_type,...}]}`（`parse_lenient`） |
| prompt 现状 | "删 MUST save ... Write tool" | **已无 Write 指示**（明文 "Do NOT write a free-text markdown file"），已用 structured verdicts JSON（4 档） |
| blackbox 路径 | 未提及 | `ExploitExecutor` 传 `skip_artifact_postprocess=True`，core 渲染落盘整段跳过；evidence 现由 blackbox `ExploitEvidenceRenderer`（3 section）渲染 |

**关键认知**：exploit **现状已经是 host-rendered 模式**——prompt 明文 "the system renders evidence from your structured verdicts"。只是它走的是 **structured_output verdicts JSON → blackbox renderer** 通道，不是 append 工具通道。所以 Plan 4 的本质 = **把 exploit 从 blackbox structured-output 通道迁移到 core append-collector 通道**（对齐 vuln 的 collector 模式，但 append 而非 write-once）。原 plan 4 漏了这条迁移——这是它的最大缺口。

## 1. 用户裁定（2026-07-17）

- **方向**：采纳 append 语义 + 修接点适配现有代码（不新建 `collector_spec`/`get_collector_spec` 骨架，复用已落地的 `make_collector`/`render_deliverable` 分发器模式）。
- **verdict 档位**：4 档（对齐现有 `exploit_verdict_schemas.py` + blackbox renderer + 现有 exploit prompt）。
- **迁移范围**：全迁移 + 保留验证（4 section evidence.md）。`validate_exploit_verdicts`（L0-L3 防幻觉）从 blackbox 迁到 core，renderer 渲 4 section（含 Rejected/Unverified + Unprocessed）。

## 2. 六项接点设计

### 2.1 ExploitCollector（独立 append 类，不复用 CollectorBase）

**文件**：`packages/core/src/shannon_core/collectors/exploit.py`（新建）

- `ExploitCollector`：`add(entry: dict) -> None`（append，无 DuplicateCallError）、`get_all() -> list[dict]`（深拷贝 list）。
- **不继承 `CollectorBase`**（CollectorBase 是 write-once section bag，`get_all()->dict` + `set_section` 重复抛 DuplicateCallError，与 append list 语义根本冲突）。
- **不暴露 `section_schemas`/`tool_names()`**（append collector 无 section 概念）——因此 generic `build_openai_tools`/`build_claude_mcp_server` 不能直接处理它，由 2.2 的专用桥函数处理。
- entry 校验：`add` 内对每条 entry 用现有 `ExploitVerdict` discriminated union（from `models/exploit_verdict_schemas.py`）做 L1 model_validate（status discriminated：exploited/blocked_by_security/out_of_scope_internal/false_positive）。**不在工具 impl 内做 L2 queue-ID 校验**（per-call 看不到整批 + 看不到 queue valid_ids），L0-L3 留到 render 时统一跑（2.5）。
- `make_exploit_collector() -> ExploitCollector`。

### 2.2 bridge 扩展（append 工具，独立于 generic set_* 路径）

**文件**：`packages/core/src/shannon_core/collectors/bridge.py`（追加）

- 新增 `build_exploit_openai_tools(collector: ExploitCollector) -> list[FunctionTool]` + `build_exploit_claude_mcp_server(collector: ExploitCollector, server_name="exploit")`。
- 单工具 `add_exploit`，description="Record one exploitation verdict (call once per queue ID)"，input_schema = 单条 `ExploitVerdict` union 的 JSON Schema（4 档 discriminated on status，复用 `ExploitVerdictBatch` 内单条 union 的 schema 构造）。
- append 语义：impl 调 `collector.add(args)`，**每次 append（无 DuplicateCallError）**，返 `"added exploit {vulnerability_id}"`（openai 返 str，claude 返 `{"content":[{"type":"text","text":"added exploit {vid}"}]}`）。
- generic `build_openai_tools`/`build_claude_mcp_server` 保持不变（仍按 CollectorBase.section_schemas 循环，服务 pre-recon/vuln set_* agent）。

### 2.3 provider 分支（runner 透传不变）

**文件**：`packages/core/src/shannon_core/agents/providers_anthropic.py` + `providers_openai.py`（改造）

- `runner.run_claude_prompt(collector=collector)` 透传不变（已是 kwarg 透传给 `provider.call(collector=...)`）。
- 两个 provider 在现有 `if collector is not None` 块内加 `isinstance(collector, ExploitCollector)` 分支：
  - 是 ExploitCollector → 调 `build_exploit_claude_mcp_server` / `build_exploit_openai_tools`
  - 否则（CollectorBase） → 调 generic `build_claude_mcp_server` / `build_openai_tools`
- 单分支改造，不破坏 set_* agent 路径。

### 2.4 executor 落盘分支（render_deliverable 扩签名读 queue）

**文件**：`packages/core/src/shannon_core/renderers/__init__.py` + `agents/executor.py`（改造）

- `render_deliverable(agent_name, data, deliverables_path=None)` 扩签名（第 3 参可选）。set_* renderer（pre_recon/vuln）不读该参，向后兼容。
- exploit 分支（`endswith("-exploit")`）在 renderer 内部：
  1. 读 `{vt}_exploitation_queue.json`（`parse_lenient` 取 `valid_ids: set[str]` + `id_to_type: dict[str,str]`，对齐现有 blackbox `ExploitExecutor` 的 queue 读取）
  2. `validate_exploit_verdicts(entries, valid_ids)` → `VerdictValidation(accepted, rejected)`
  3. `render_exploit(vc, validation, id_to_type)` 渲 4 section（2.5）
- `executor.py` L169：`render_deliverable(agent_name, collector.get_all(), deliverables)` 多传 `deliverables`。
- `collectors/__init__.py::make_collector`：加 `endswith("-exploit")` 分支返 `make_exploit_collector()`（与 `-vuln` 分支对称）。

### 2.5 renderer（4 section，复用迁移后的 validator）

**文件**：`packages/core/src/shannon_core/renderers/exploit.py`（新建）+ validator 迁移

- `render_exploit(vuln_class: str, validation: VerdictValidation, id_to_type: dict[str,str]) -> str`。**5 个可见 section**（对齐现有 blackbox `ExploitEvidenceRenderer` 的 3 section + 新增 Unprocessed，rejected 保留）：
  1. `## Successfully Exploited`（`accepted` 中 status=exploited，按 severity 排序 critical→low；字段 vulnerability_id(###)、severity、impact、exploitation_steps、proof_of_impact）
  2. `## Potential Vulnerabilities (Validation Blocked)`（accepted 中 blocked_by_security；字段 confidence、current_blocker、what_we_tried、evidence_of_vulnerability、expected_impact）
  3. `## Other Verdicts`（accepted 中 out_of_scope_internal + false_positive；字段 reason、evidence）
  4. `## Unverified Findings (校验未通过，待人工复核)`（rejected：agent 调了 add_exploit 但 L1 schema 错 / L2 queue-ID 幻觉 / L3 重复；带 reason；对齐现有 blackbox renderer 标题）
  5. `## Unprocessed Vulnerabilities`（queue `valid_ids` 中**既不在 accepted 也不在 rejected** 的 ID，即 agent 完全没 attempt 的）
- **Rejected 与 Unprocessed 正交**：rejected=调了 add_exploit 但验证失败；unprocessed=没调。空 section 不渲染（保持 md 紧凑）。
- **validator 迁移**：`validate_exploit_verdicts` + `VerdictValidation` 从 `packages/blackbox/src/shannon_blackbox/services/exploit_verdict_validator.py` 迁到 core（就近放 `collectors/exploit.py` 同模块或 `services/exploit_verdict_validator.py`）。blackbox 改 `from shannon_core... import`（re-export 兼容旧测试，或迁测试）。blackbox `ExploitEvidenceRenderer`（旧 3-section renderer）留死代码，**Plan 5 删**（对齐父 spec §4.5 诊断/遗留移除节奏）。

### 2.6 5 prompt 改造

**文件**：`prompts/exploit-{injection,xss,auth,ssrf,authz}.txt`

- `<system_architecture>` 的 "Your Output: structured verdicts — ... JSON object of shape `{"verdicts":[...]}`" → "Your Output: call the `add_exploit` tool ONCE per vulnerability in your queue, with the verdict fields for its status (exploited / blocked_by_security / out_of_scope_internal / false_positive). The host renders the exploitation evidence deliverable from your calls — there is no Markdown for you to write yourself."
- `<deliverable_instructions>` 的 "emit your structured verdicts" → "call `add_exploit` once per queue ID"。
- **保留**：queue 读取、TodoWrite、4 档字段说明、severity 排序指示、"Do NOT write a free-text markdown file"（已存在，保留）。
- **删**：`ExploitVerdictBatch` JSON shape 描述（不再产 JSON 批量）。

## 3. Task 3-5 改写要点（给 writing-plans）

- **Task 1**：entry model 改 4 档（ExploitEntry=exploited / BlockedEntry=blocked_by_security / OutOfScopeEntry / FalsePositiveEntry），或直接复用 `exploit_verdict_schemas.py` 的 4 档 union（优先复用，避免字段漂移）。
- **Task 2**：renderer 改 5 section（Exploited / Blocked / Other / Unverified-rejected / Unprocessed）+ 接 `VerdictValidation` 输入（非裸 entries）。
- **Task 3**：bridge `build_exploit_*` + make_collector `-exploit` 分支 + render_deliverable 扩签名 + executor 多传 deliverables + 两个 provider isinstance 分支 + **validator 从 blackbox 迁 core**。
- **Task 4**：5 prompt 改 add_exploit（4 档）。
- **Task 5**：GLM 冒烟 + **blackbox ExploitExecutor 迁移**（skip_artifact_postprocess=False、删 structured_output_schema、删 L78-101 兜底/validate/render/write_verdicts_json）+ blackbox 测试改 import。

## 4. 不变量（守 CLAUDE.md 铁律）

- **§1 双轨独立**：renderer 读 queue 是读 vuln agent 的 LLM 产物（exploitation_queue.json），不引 GitNexus 确定性层。append collector 是 LLM 轨自身结构化通道，不喂确定性产物。
- **§2 双引擎可互换**：`build_exploit_openai_tools` + `build_exploit_claude_mcp_server` 双引擎对称（同 input_schema、同 append 语义），provider isinstance 分支双引擎一致。
- **append ≠ set**：ExploitCollector 不继承 CollectorBase，不复用 set_* bridge/section_schemas。
- **queue 是 vuln 的，只读不改**：exploit renderer 只读 `{vt}_exploitation_queue.json`，不写。
- **单通道**：exploit agent 只产 `{vt}_exploitation_evidence.md`（不产 queue；queue 由 vuln agent 产）。
- **TS 对齐**：add_exploit schema、renderer section、prompt 文案 1:1 移植 TS `exploit-collector.ts`/`exploit-renderer.ts`（TS 字段以 `exploit_verdict_schemas.py` 4 档为准，二者已对齐）。

## 5. 风险与缓解

| 风险 | 缓解 |
|---|---|
| validator 跨包迁移破坏 blackbox 测试 | Task 3 同步改 blackbox import / 迁测试；core re-export 兜底 |
| blackbox ExploitExecutor 迁移回归（structured_output 兜底是 invite_code_center bug 修的） | Task 5 GLM 真机冒烟验证 verdicts 不丢（append 通道 + validator 兜底） |
| add_exploit union schema 在 GLM/双引擎接受度 | Task 3 bridge 测试 + Task 5 probe；oneOf 不接受则改 discriminated（required status + conditional fields） |
| Unprocessed 与 Rejected 语义混淆 | renderer section 标题 + 注释明示正交；测试覆盖两类 |
