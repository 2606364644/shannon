## Context

白盒审计 pipeline 当前分为确定性 code_index 阶段和 LLM 驱动的 agent 阶段。code_index 通过 AST 解析和正则规则识别入口点，产出 `EntryPoint[]`（含 `needs_llm_review` 标记）和 `CallChain[]`。PRE_RECON 是唯一拥有源代码访问的 LLM agent。

当前问题：
- `needs_llm_review=True` 的入口点没有后续消费步骤
- PRE_RECON 的 Entry Point Mapper 与 code_index 功能重叠，prompt 存在矛盾指令
- CallChain 从所有入口点（含噪声）构建，无法区分有效和无效链
- Python `async def` catch-all 规则（confidence=0.30）在 async 项目中产生大量噪声

重构前（TypeScript 版本）中，入口点发现完全由 PRE_RECON 的 LLM 完成，没有确定性阶段。重构后引入了 code_index 但未调整 PRE_RECON 的角色。

## Goals / Non-Goals

**Goals:**

- PRE_RECON 系统性地裁决 `needs_llm_review` 入口点，产出权威的入口点列表
- CallChain 仅从裁决确认的入口点构建，消除噪声链
- 在 code_index 源头收窄 `async def` catch-all 噪声，减少 LLM 裁决负担
- PRE_RECON 的 Entry Point Mapper 转为补充发现角色
- 消除 prompt 中的矛盾指令
- 下游 agent（recon、vuln-*）无需修改

**Non-Goals:**

- 不改变 code_index 的 AST 解析和 CallEdge 提取逻辑
- 不让下游 agent 直接读取 `entry_points.json` 或 `code_index.json`——裁决结论通过 `pre_recon_deliverable.md` 传递
- 不创建新的 pipeline phase——裁决归入 PRE_RECON phase 内部
- 不在本次变更中系统性消费 CallChain（如攻击路径深度分析、sink 可达性标注）——留作后续

## Decisions

### D1: CallChain 构建时机后移到裁决之后

**决策**：`build_code_index()` 不再调用 `build_call_chains()`。CallChain 在 PRE_RECON 裁决完成后、由确定性 activity `rebuildCallChains` 从确认的入口点集合构建。

**理由**：CallChain 从入口点 BFS 展开，如果入口点集合包含噪声，展开的链也无效。后移构建时机确保只从权威入口点建链，避免无效计算。

**替代方案**：先建链再过滤（从 CodeIndex 中移除被拒绝入口点的链）。被否决——因为 BFS 的 `max_width=50` 剪枝可能因为噪声入口点占用宽度配额而截断有效链。

### D2: async def catch-all 在源头收窄

**决策**：在 `entry_points.py` 的 `_detect_python()` 中，`async def` catch-all 规则增加启发式过滤：
- 跳过文件名含 `test_` / `_test` / `conftest` 的文件
- 跳过目录含 `tests/` / `test/` / `spec/` 的文件
- 跳过函数名以 `_` 开头的 private 函数
- 跳过函数参数中无 `request` / `response` / `event` / `message` 等模式的函数

收窄后仍标记为 `needs_llm_review=True` 但 confidence 提升到 0.40，减少候选数量。

**理由**：在一个典型 FastAPI 项目中，`async def` catch-all 可能产生 50+ 候选，其中 80% 明显不是入口点。在源头排除可大幅降低 LLM 裁决成本。

**替代方案**：全推给 LLM 裁决。被否决——成本高且大多数情况 LLM 做的判断和简单启发式一样。

### D3: 裁决归入 PRE_RECON phase 内部，不创建独立 pipeline step

**决策**：裁决作为 PRE_RECON 的 Phase 0 执行。裁决后紧跟确定性 `rebuildCallChains` activity，两者同属 `pre-recon` phase。

**理由**：
- 裁决的输入是 code_index 的产出，裁决的输出是 rebuild 的输入——三者是因果关系
- 不增加对外可见的 pipeline step，resume 语义自然（PRE_RECON 完成 = 裁决 + rebuild 完成）
- 无需新增 agent name 或 DeliverableType

**替代方案**：创建独立 `entry-point-reviewer` agent 插在 code_index 和 PRE_RECON 之间。被否决——增加 pipeline 复杂度，且裁决需要的源码上下文分析与 PRE_RECON 已有的 Task Agent 基础设施重叠。

### D4: entry_points.json 为 PRE_RECON 过程性产物

**决策**：`entry_points.json` 由 PRE_RECON 在 Phase 0 输出，只有 PRE_RECON 自己和 `rebuildCallChains` activity 读取。裁决结论融入 `pre_recon_deliverable.md` 的 Section 5（Attack Surface Analysis）供下游使用。

**理由**：下游 agent 已有成熟的数据流（读 markdown deliverable），LLM 读自然语言比读 JSON 更有效。`entry_points.json` 的价值是过程性的——强制 PRE_RECON 系统性裁决，而非作为管道传递产物。

**替代方案**：让 recon/vuln agent 直接读 `entry_points.json`。被否决——需要修改所有下游 prompt，且 LLM 对 JSON 的理解不如对结构化 markdown 的理解。

### D5: Entry Point Mapper 角色转为补充发现

**决策**：PRE_RECON Phase 1 的 Entry Point Mapper Agent 从 "Find ALL network-accessible entry points" 改为 "Find entry points that the deterministic code_index may have missed"，重点关注配置文件路由、动态注册、未知框架模式。

**理由**：code_index 已覆盖已知框架的装饰器/注解模式。LLM 的边际价值在于发现 code_index 规则无法覆盖的模式。

## Risks / Trade-offs

**[R1] PRE_RECON Phase 0 增加执行时间** → 裁决需要 LLM 逐一审查候选入口点。通过源头收窄（D2）将候选数控制在合理范围（目标 <20）。若候选极少（0-2 个），Phase 0 可快速完成。

**[R2] async def catch-all 收窄可能误排除真正入口点** → 收窄规则只排除高置信度非入口点模式（test 文件、private 函数）。仍标记 `needs_llm_review=True` 的候选会经 LLM 二次确认。极少数边缘情况（如测试文件中定义的 webhook handler）可能在源头被跳过——可接受，因为这类模式违反常规项目结构。

**[R3] PRE_RECON 补充发现的新入口点可能无法匹配已有 FuncBlock** → LLM 发现的入口点可能指向 code_index 未解析到的函数（如动态生成的路由处理函数）。`rebuildCallChains` 需处理找不到匹配 FuncBlock 的情况——记录为 unresolved entry point，不构建链。

**[R4] code_index.json 被 PRE_RECON 和 rebuildCallChains 两步写入** → code_index 初始写入不含 CallChain，rebuild 后追加。需确保文件格式兼容（`chains` 字段初始为空列表 `[]`）。
