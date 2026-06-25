# Token 消耗优化设计：打通 Prompt 缓存 + 可观测

- **日期**：2026-06-26
- **分支**：`feat/fork-py`
- **状态**：设计待审
- **范围**：在**不降低安全扫描效果（召回不变）**的前提下，降低单次白盒扫描的 LLM token 消耗；双引擎（claude-agent-sdk / openai-agents）通用。

---

## 1. 背景与问题

### 1.1 单次白盒扫描的 token 消耗全景

一次完整白盒扫描约 **8–10 次 LLM 调用**：pre-recon（large）1 + recon（medium）1 + **5 个并发 vuln agent（medium）** + report 1，auth/authz 条件触发再 +1~2。每个 vuln agent 是「带工具的多轮 agent」，不是单次 prompt。

**最大头是 5 个并发 vuln agent**，每个的上下文由三块堆出来：

1. **prompt 模板** ~34KB（含大量重复的方法论 + 输出格式模板；5 个 vuln 模板合计约 145KB）。
2. **每个 agent 各自用工具反复 Read 同一份** `recon_deliverable.md` / `pre_recon_deliverable.md`（合计常 50–150KB）——这份内容在 5 个 agent 里**重复全量进入各自的对话历史**。
3. **多轮 tool use** 让 input token 随轮次累积。

### 1.2 根因：缓存 100% 没开，且共享内容不在前缀位置

探查结论（详见 `docs/superpowers/specs/` 同目录其它记录与代码）：

- **没有任何 prompt caching**：没设 `system_prompt`（prompt 全以 user message 发，`packages/core/src/shannon_core/agents/providers_anthropic.py:282` 的 `query(prompt=prompt)`），错过了 Claude Code CLI 对 system prompt 的自动缓存；无任何 `cache_control` 断点；OpenAI 引擎也无 `cached_content`。
- **共享内容不在前缀位置**：5 个 vuln prompt 各自以**自己的角色定义开头**（injection/xss/authz 措辞不同），共享 `@include` 在后面 → 前缀第一个 token 就分叉。recon/pre-recon deliverable 是 agent 运行中靠工具 Read 进来的，进入对话历史的**中间位置**，**不在前缀**。
  - OpenAI/GLM 的**自动前缀缓存**只缓存从请求开头起的相同前缀；前缀一分叉就停，**永远扫不到后面的 recon**。
  - Anthropic 协议**没有**自动前缀缓存，必须显式 `cache_control`。
- **观测字段已存在但没接线**：`TokenUsage` 已有 `cache_creation_input_tokens` / `cache_read_input_tokens`（`packages/core/src/shannon_core/agents/runner.py:16-17`），provider 也提取它们（`providers_anthropic.py:365-366`），但**从没接进任何日志/汇总**。
- 附带：`max_turns=200` 偏高；OpenAI 引擎连 `max_tokens` 都没设（输出无上限）。

### 1.3 不变量约束（必须遵守）

- **双轨独立性**（CLAUDE.md §1）：LLM 轨 prompt 不得引入确定性层产物。**确定性→LLM 轨的 hints 桥梁（原 `static_dataflow_hints.md` + `@include`）已于 2026-06-26 彻底拆除，`packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py` 锁定此不变量。** 本设计**只注入 LLM 轨产物**（recon / pre-recon deliverable，分别由 RECON / PRE_RECON agent 产出），**不重建任何确定性 hints 桥梁**。
- **双引擎一致性**（CLAUDE.md §2）：两引擎流程一致、可互换，业务侧不感知用哪个引擎。优化必须**一套设计、两引擎通用**，差异只沉到 provider 层。
- **召回不降**：优化只改「怎么付钱 / 内容摆什么位置」，不改分析行为与信息来源。

---

## 2. 核心思路

> **把「5 个 vuln agent 共享的内容」重组成一个字节级一致、位于最前的稳定前缀**，然后让缓存机制去命中它。两引擎在「重排」这一步做法一致，只在「怎么标记缓存」上分叉，落在 provider 层。

两个互补的缓存层：

| 层 | 内容 | 跨什么复用 | 引擎相关？ |
|---|---|---|---|
| **system_prompt（静态）** | 角色 / 共享方法论 / shared `@include` | 跨**所有扫描、所有 agent、每一轮** | 否（两引擎都把静态部分放 system_prompt / instructions） |
| **共享 user-message 前缀（每扫描动态）** | recon + pre-recon deliverable **内容**（注入，非路径） | 跨**同一次扫描的 5 个并发 vuln agent** | 重排无关；Anthropic 需补 `cache_control`，OpenAI 靠自动前缀缓存 |

> 静态方法论**只**进 system_prompt（缓存面最广：跨扫描/agent/轮次）；shared_prefix **只**放 per-scan 但跨 5 个 vuln agent 一致的内容（即 deliverable），避免两层重复。

**为什么必须「注入 + 前置」deliverable**：recon 内容只有进入**首条消息**才处于前缀位置；靠工具 mid-run Read 永远进不了前缀，无论端点是否支持自动缓存都命中不了。注入是把「信息不变、只换位置」——deliverable 本就是 LLM 轨产物，喂给 vuln agent 早就在做（只是从「工具 Read」改成「首条消息提供」），**不碰双轨独立性**。

---

## 3. 方案设计

### 3.1 组件总览

```
┌─ 业务层（引擎无关）──────────────────────────────────────────┐
│  PromptManager                                                  │
│   · load_sync() 现返回单个 str → 改为返回结构化 prompt：         │
│       StructuredPrompt(system_prompt, shared_prefix, specific)  │
│   · shared_prefix = 注入的 deliverable 内容 + 共享方法论          │
│     （5 个 vuln agent 间字节一致；标「缓存边界」）               │
│   · specific = vuln 类特定方法论 + 输出格式 + 动态变量            │
│   · 静态方法论 → system_prompt 字段                              │
└──────────────────────────┬──────────────────────────────────────┘
                           │ run_claude_prompt(structured_prompt)
┌─ provider 层（吸收协议差异）──────────────────────────────────┐
│  BaseProvider                                                   │
│   · 新增引擎无关的「缓存边界」表达                              │
│  AnthropicProvider        │  OpenAIProvider                     │
│   · 边界 → cache_control   │   · 边界 → no-op                    │
│     断点（TTL=1h 覆盖整轮） │     （靠自动前缀缓存）              │
│   · system_prompt 注入     │   · instructions 注入               │
└──────────────────────────┴──────────────────────────────────────┘
```

### 3.2 组件 1：Token / 缓存可观测（Step 0，先行）

**目的**：把现成的仪表盘接线 + 用受控探针一锤定音「端点吃不吃缓存」。这是后续一切优化的前提与验证手段。

- **逐轮日志 + 扫描汇总**：把 `TokenUsage.cache_creation_input_tokens` / `cache_read_input_tokens` 接进现有逐轮日志（provider-agnostic turn logging，见 memory `provider-agnostic-turn-logging`）+ 扫描结束的 token 汇总报告。每阶段（pre-recon / recon / vuln / report）分别统计 input / output / cache_creation / cache_read。
- **受控缓存探针脚本** `scripts/validate_cache_probe.py`（仿 `validate_glm_task_probe.py` 命名）：
  - 拿**同一个大前缀**（≥1024 token，可用一段固定长文本）在**每个引擎**上**连发两次**请求。
  - 看第二次请求的 `cache_read_input_tokens` 是否 >0。
  - 这把「端点支不支持缓存」与「我们的 prompt 有没有摆对位置」**两件事分开测**——直接跑现状扫描没意义，因为现状结构本身保证 cache_read≈0。
  - **判定规则**：
    - glm-openai 探针 cache_read>0 ⇒ 自动前缀缓存生效，重排即够用。
    - glm-anthropic 探针（带 cache_control）cache_read>0 ⇒ GLM 代理兑现 cache_control，重排+标记都生效。
    - 任一引擎探针恒为 0 ⇒ 该引擎缓存这条路走不通，回落到 §3.5 非缓存杠杆。
  - 额外信号：glm-openai 上单 agent 多轮内部若已能看到 cache_read>0，本身就是「端点会自动缓存」的旁证。

### 3.3 组件 2：Prompt 重排——共享稳定前缀（引擎无关）

**核心改动**：`PromptManager.load_sync()` 的返回契约从 `str` 升级为结构化对象：

```python
@dataclass
class StructuredPrompt:
    system_prompt: str        # 静态：角色 + 共享方法论 + shared @include（跨扫描稳定）
    shared_prefix: str        # 每扫描动态但跨 5 个 vuln agent 一致：注入的 recon/pre-recon deliverable 内容
    specific: str             # vuln 类特定：本类方法论 + 输出格式 + 动态变量（LOGIN_INSTRUCTIONS 等）
    cache_boundary: bool = True   # 引擎无关的「shared_prefix 之后打缓存断点」标记
```

- **shared_prefix 组装**：在 prompt 构建时**读** `recon_deliverable.md` + `pre_recon_deliverable.md` 文件内容（而非路径），**作为 user message 的最前块**。5 个 vuln agent 的这块**字节完全一致**（方法论不拼这里，它在 system_prompt）。
- **specific 在后**：vuln 类特定方法论、输出格式、`{{LOGIN_INSTRUCTIONS}}` / `{{BROWSER_COMMANDS}}` 等动态变量放 `specific`，接在 shared_prefix 之后。
- **system_prompt**：把跨扫描稳定的静态部分（角色、`shared/_*.txt` 方法论）从 user message 移入 `system_prompt`。
- **prompt 措辞微调**：告诉 agent「recon / pre-recon deliverable 已在你上下文最前提供；你仍可按需 grep / read 具体文件」——保留工具自由，只是不再需要花一轮去 Read deliverable。
- **向后兼容**：对不需要结构化的 prompt（小 prompt、非 vuln agent），`StructuredPrompt` 可退化为 `system_prompt=""`、`shared_prefix=""`、`specific=<原 str>`，调用方无感。

**关键不变量（测试锁定）**：

- 5 个 vuln 类的 `shared_prefix` **字节一致**。
- `shared_prefix` **不得包含任何确定性层产物**（扩展 `test_static_dataflow_hints_decoupling.py` 的断言范围到注入内容）。

### 3.4 组件 3：Provider 适配（吸收协议差异）

- **BaseProvider** 新增引擎无关的「缓存边界」表达（如 `run_claude_prompt` 接受 `StructuredPrompt`，把 `cache_boundary` 透传给具体 provider）。
- **AnthropicProvider**（`providers_anthropic.py`）：
  - 把 `cache_boundary=True` 翻译成在 shared_prefix 内容块上打 `cache_control: {type: "ephemeral", ttl: "1h"}`（TTL 1h 覆盖整轮扫描，避免 5min 默认过期）。
  - 设置 `ClaudeAgentOptions.system_prompt`（当前未设，`providers_anthropic.py:229-233`）。
  - **SDK 机制待实现期核实**：claude-agent-sdk 能否对 user-message 内容块打 `cache_control`（system_prompt 字段肯定可用；content-block 级 cache_control 需在 plan 阶段确认 SDK 暴露面）。若 SDK 仅支持 system_prompt 级缓存，则 shared_prefix 走 system_prompt 注入路径作为兜底。
- **OpenAIProvider**（`providers_openai.py`）：
  - `cache_boundary` 为 **no-op**（OpenAI/GLM 协议靠自动前缀缓存，无需显式标记）。
  - 设置 `Agent(instructions=system_prompt)`（当前 `instructions=None`，`providers_openai.py:79`）。
  - 可选：若 GLM 兼容端点暴露 cache hint 参数，再透传。

### 3.5 组件 4：非缓存引擎的回落（同一套设计，按实测结果走）

若 §3.2 探针测出某引擎 cache_read 恒为 0：

- 该引擎**不投入** cache_control plumbing（Anthropic 侧）或**不期待**自动缓存收益（OpenAI 侧）。
- 重排本身**不亏**（内容没变，只换位置），照做。
- 额外杠杆（additive，单独任务、gated on 探针结果）：**prompt 精简**——压缩 5 个 vuln prompt 里重复的方法论 / 输出格式模板（~145KB），需小心不动语义、保持双轨独立性。

---

## 4. 数据流

```
RECON agent → recon_deliverable.md (LLM 轨产物, 落盘 deliverables/)
PRE_RECON agent → pre_recon_deliverable.md (LLM 轨产物, 落盘 deliverables/)

[vuln 阶段] PromptManager.load_sync("vuln-<class>", vars):
  1. 读 deliverable 文件内容（非路径）
  2. 组装 StructuredPrompt(
       system_prompt = 静态方法论,
       shared_prefix = recon/pre-recon deliverable 内容,   # 5 个 class 字节一致
       specific      = 本 class 方法论 + 输出格式 + 动态变量,
     )
  3. 返回

run_claude_prompt(structured_prompt):
  → BaseProvider 透传 cache_boundary
  → AnthropicProvider: shared_prefix 块打 cache_control(ttl=1h) + 设 system_prompt
  → OpenAIProvider:   no-op(自动缓存) + 设 instructions
  → API

5 个并发 vuln agent (asyncio.Semaphore, max_concurrent=3):
  · 第 1 个: cache_creation（≈1.25×）建缓存
  · 第 2~5 个: cache_read（≈0.1×）命中
  · 每个 agent 多轮: system_prompt 每轮命中缓存

TokenUsage(cache_creation/cache_read) → 逐轮日志 + 扫描汇总
```

---

## 5. 错误处理

- **deliverable 文件缺失**：`shared_prefix` 退化为不含该 deliverable（或退回原路径引用让 agent 工具 Read），记 warning，**不阻塞扫描**。
- **cache_control 标记失败 / API 拒绝**：provider 回退为不带标记的 prompt（等价现状），扫描继续，仅无缓存收益，记 warning。
- **探针脚本**：内容必须 hermetic（固定文本），结果可复现；不依赖外部仓库。
- **system_prompt 过大**：静态方法论体量在 KB 级，远低于任何模型 system prompt 上限，无风险。

---

## 6. 测试策略

> 遵循 CLAUDE.md §3：只跑改动相关测试文件，勿广跑全套（预存 hang / 失败）。

**单元测试（可自动化，核心）**：

- `PromptManager` 产出：5 个 vuln 类的 `shared_prefix` **字节一致**；`specific` 各不相同。
- `cache_boundary` 标记正确落在 shared_prefix 之后。
- `shared_prefix` **不含确定性层产物**（扩展 `test_static_dataflow_hints_decoupling.py`）。
- AnthropicProvider：`cache_boundary=True` → 生成 `cache_control` 断点（ttl=1h）；`system_prompt` 被设置。
- OpenAIProvider：`cache_boundary=True` → 不产生显式缓存参数；`instructions` 被设置。
- 向后兼容：非结构化 prompt 退化为 `specific=<原 str>`，行为不变。

**探针（半自动，需真端点）**：

- `scripts/validate_cache_probe.py`：两引擎各跑，cache_read>0 即通过；作为人工冒烟脚本，不进 CI 硬断言（无真实端点会 flaky）。

**召回回归（人工冒烟）**：

- 在一个已知有漏洞的小仓上，改前/改后各跑一次完整白盒，比对 findings 集合**不变**（验证注入 deliverable + 重排不降召回）。
- 用 §3.2 的汇总报告对比改前/改后总 token 与 cache_read 占比，确认实际收益。

---

## 7. 分阶段实施

| 阶段 | 内容 | 风险 | 前置 |
|---|---|---|---|
| **P0** | 可观测：逐轮 + 汇总接 cache 字段；受控探针脚本 | 低 | — |
| **P1** | system_prompt 迁移（静态方法论进 system_prompt / instructions） | 低（行为零变化） | P0 探针结果 |
| **P2** | 共享前缀重排 + deliverable 注入 + provider cache_control 适配 | 中（改 PromptManager 契约 + prompt 措辞） | P0 证实目标引擎缓存可生效 |
| **P3** | 召回回归 + 实测量化收益；条件触发 prompt 精简（仅缓存不生效引擎） | 中（精简需保语义） | P2 完成 |

**决策门**：P0 探针结果是 P2 是否值得投入的 gate。若两引擎都不吃缓存 → 跳过 P2 的 cache_control 部分，直接走 P3 的 prompt 精简。

---

## 8. 风险与未决

- **GLM 端点是否真兑现缓存折扣**：核心未知，P0 受控探针一锤定音。不靠猜。
- **claude-agent-sdk 的 cache_control 暴露面**：user-message content-block 级是否可标记，需 plan 阶段核实 SDK；若不可，shared_prefix 走 system_prompt 兜底（收益略降但可行）。
- **重排对 agent 注意力的影响**：recon 前置、类特定指令后置，理论上「指令在后 = 更显著」是好事，但需 P3 回归坐实不降召回。
- **注入 deliverable 增大首条消息**：缓存生效时廉价；不生效时≈现状工具 Read 成本（不亏）。

---

## 9. 不做（YAGNI）

- **不**做「按确定性 sink 跳过 vuln agent」——违反双轨独立性。
- **不**重建确定性→LLM 轨 hints 桥梁——已被显式拆除并锁定。
- **不**为单引擎做特化分支——差异只沉到 provider。
- **不**改 `max_turns` / `max_tokens` 作为本期范围（可作后续独立任务，与缓存正交）。
