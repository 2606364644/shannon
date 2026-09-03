# 项目总览

supernova 是一个 Python monorepo，用 Temporal Workflow 编排白盒源码分析、黑盒动态验证、跨仓关联与报告生成。核心设计不是单一扫描器，而是多条可独立退化/合并的分析轨，以及可替换的浏览器/Agent 执行引擎。

## 包边界

| 包 | 主要职责 |
|---|---|
| `supernova-core` | 共享模型与配置、代码索引、source/sink/调用图、双轨合并、Agent provider、浏览器引擎、HOST 代理、报告模型、MR 纯函数 |
| `supernova-whitebox` | 白盒 Temporal workflow/activity、pre-recon/recon/vuln agent、chain verdict、PoC 写回、报告时序 |
| `supernova-blackbox` | 黑盒 exploitation-only workflow、认证预检、端点 live 验证、动态 exploit、黑盒报告 |
| `supernova-multi` | 跨仓白盒编排、per-edge 关联、多跳链与跨仓裁决 |
| `supernova-combined` | 白盒 + 黑盒组合编排与融合报告 |
| `supernova-web` | FastAPI/Web UI、工作区配置、认证/HOST 档案、扫描提交与进度事件 |
| `supernova-worker` | 统一注册 core/whitebox/blackbox/multi activities 与 workflows 的 Temporal worker |

依赖方向总体为 `web/worker/combined/multi -> whitebox/blackbox -> core`。core 不反向导入上层包；workflow 体内避免环境读取与文件 I/O，由 activity 或外层注入，保证 Temporal determinism。

## 当前扫描主链路

### 白盒

```text
repo/config
  -> preflight + credential check
  -> code index (tree-sitter + GitNexus process traces + rules + LLM assist)
     ∥ pre-recon LLM agent
  -> sink report merge + entry fusion + confidence adjudication
  -> framework/frontend route analysis + route chains
  -> [MR only] incremental scope
  -> recon + shared digest
  -> risk scoring
  -> vuln agents (pure LLM track)
  -> authz GitNexus judge
  -> inj/xss/ssrf GitNexus chain verdict
  -> dual-track merge + GN enrichment + endpoint enrichment + dataflow view
  -> attack-chain LLM + GitNexus assembly
  -> structured PoC writeback
  -> report_data SSOT -> polish -> markdown export
```

关键约束见 [双轨分析](dual-track-analysis.md)：GitNexus 产物可以进入 GitNexus 轨内部判定，但绝不能作为 hints 注入纯 LLM vuln prompt。

### 黑盒

黑盒不是独立侦察器。它必须找到白盒（或跨仓关联）交付物中的 `recon_deliverable.md` 和至少一个非空 exploitation queue；否则 fail-fast。流程为：

```text
HOST proxy -> target preflight -> browser engine resolve
  -> authentication validation (optional)
  -> detect whitebox/correlation queues
  -> endpoint live verification
  -> per-vuln queue validation
  -> exploit agents in parallel
  -> evidence coverage close + report_data + markdown report
```

详见 [黑盒验证](blackbox-verification.md)。

### 跨仓

跨仓扫描先可选地做只读自动拓扑预分析，人工确认后生成 `MultiRepoConfig`。扫描时逐仓复用或执行白盒，再对配置中的每条服务关系运行 per-edge 关联 agent，合并 queue、拓扑、攻击流与多跳链，最后基于发现做跨仓裁决。详见 [跨仓微服务扫描](cross-repo-microservice-scanning.md)。

## 交付物布局

白盒和黑盒都采用 workspace/repo 双层结构，核心目录由 `supernova_core.utils.paths` 统一解析：

```text
workspaces/<workspace>/
  session.json
  events.ndjson / workflow.log
  agents/
  prompts/
  deliverables/
    whitebox/
      intermediate/       # code_index.json, parameter_graph.json, queues, checkpoints
      *.md                # 人类可读分析交付物
    blackbox/
      endpoint_verify.json
      *_exploitation_evidence.md
      report_data.json
```

机器交接文件优先放在 track 子目录的 `intermediate/`，旧 session 的平铺路径由 `resolve_intermediate` / `resolve_track_deliverable` 兜底读取。

## 执行面

- **Agent 引擎**：`anthropic_api/bedrock/vertex` 走 `claude-agent-sdk` CLI 子进程；`openai_compatible/litellm_router` 走 `openai-agents`。业务侧统一调用 `run_claude_prompt`（历史命名），见 [双 Agent 引擎](agent-engines.md)。
- **浏览器引擎**：默认 `agent-browser`，可切 `playwright`；两者实现同一 `BrowserEngine` Protocol，见 [双浏览器引擎](browser-engines.md)。
- **成本**：两套 Agent 引擎都按 usage × per-profile 价目表自行计算 `CostAmount`；字段名 `cost_usd` 保留兼容，实际币种由 `cost_currency` 表达。

## 运行与观测

- Temporal workflow/activity 负责重试、取消、heartbeat 与进度；长 LLM activity 由 `activity_heartbeat()` 保持活性。
- Web 路径通过 `events.ndjson` / SSE 暴露阶段、agent、工具审计、成本与进度。
- 关键 LLM 产物使用 `atomic_write_json` 或 collector 校验后落盘，避免半写文件被下游消费。
- 断点恢复包含 agent 级 completed_agents、step cache、chain verdict checkpoint、PoC 已有 `report_poc` 跳过等层次。

## 详细主题

从 [README](README.md) 进入各专题文档。历史设计请查 `docs/superpowers/`，本文只保留当前行为。
