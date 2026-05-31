## Why

重构后引入了确定性 code_index 阶段（AST 分析 + 正则规则匹配入口点），但 PRE_RECON agent 的角色没有相应调整。这导致三个问题：

1. **`needs_llm_review` 标记被生产但从未被结构化消费**——低置信度入口点（如 Python `async def` catch-all）被标记为需要 LLM 审查，但没有任何步骤实际执行这个审查。
2. **PRE_RECON 的 Entry Point Mapper 重复做 code_index 已做的事**——prompt 中存在三句互相矛盾的指令（"不用自己找" / "验证你自己的发现" / "找所有入口点"）。
3. **CallChain 从所有入口点（含噪声）构建**——包含从低置信度/错误入口点展开的无效调用链，浪费计算且污染数据。

## What Changes

- **code_index 阶段不再构建 CallChain**——`build_code_index()` 只产出 `FuncBlock[]`、`CallEdge[]`、`EntryPoint[]`（含 `needs_llm_review` 标记），CallChain 构建延迟到裁决之后。
- **code_index 源头收窄 `async def` catch-all 噪声**——通过文件名、目录路径、函数参数签名等启发式规则，在确定性阶段排除明显不是入口点的 async 函数。
- **PRE_RECON 增加 Phase 0 裁决步骤**——系统性地审查每个 `needs_llm_review=True` 的入口点，输出 confirmed / rejected / reclassified 判定。PRE_RECON 角色从"全知全能的发现者"变为"裁决者 + 补充发现者"。
- **PRE_RECON 输出 `entry_points.json`**——结构化的权威入口点列表，包含裁决结果和补充发现。此文件为 PRE_RECON 的过程性产物，只有 PRE_RECON 自己消费，裁决结论融入 `pre_recon_deliverable.md` 供下游使用。
- **PRE_RECON phase 内增加确定性收尾步骤 `rebuildCallChains`**——LLM agent 跑完后，读 `entry_points.json` 中的确认入口点，调用已有的 `build_call_chains()` 从干净的入口点集合构建 CallChain，更新 `code_index.json`。
- **PRE_RECON 的 Entry Point Mapper 角色变为"补充发现"**——专注于 code_index 确定性规则无法覆盖的模式（配置文件路由、动态注册、未知框架）。
- **消除 PRE_RECON prompt 中的矛盾指令**——统一入口点发现的权威来源。

## Capabilities

### New Capabilities

- `entry-point-adjudication`: 入口点裁决流程——code_index 产出候选，PRE_RECON 裁决 + 补充发现，确定性后处理构建最终 CallChain。

### Modified Capabilities

（无现有 spec 需要修改）

## Impact

- **`packages/core/src/shannon_core/code_index/`**：`__init__.py` 的 `build_code_index()` 去掉 CallChain 构建；`entry_points.py` 收窄 `async def` catch-all 规则。
- **`packages/whitebox/src/shannon_whitebox/pipeline/`**：`activities.py` 新增 `rebuild_call_chains` activity；`workflows.py` 在 PRE_RECON phase 内调用该 activity；`shared.py` 无需变动。
- **`packages/core/src/shannon_core/models/deliverables.py`**：新增 `ENTRY_POINTS` deliverable type。
- **`prompts/pre-recon-code.txt`**：重写 `<starting_context>` 和 `<task_agent_strategy>`，增加 Phase 0 裁决步骤，修改 Entry Point Mapper 的角色定义，消除矛盾指令。
- **下游 agent prompt（recon.txt, vuln-*.txt）**：无需修改——它们已经只读 `pre_recon_deliverable.md` 和 `recon_deliverable.md`。
- **测试**：新增裁决流程集成测试，更新现有 `test_entry_points.py` 和 `test_build_code_index.py`。
