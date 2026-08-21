# 黑白盒扫描中间产物清单（Intermediate Artifacts Inventory）

> 状态：依据当前代码（`feat/fork-py`）核对整理，2026-08-21。
> SSOT：`packages/core/src/supernova_core/models/deliverables.py`（tier 判定与文件名模式）、
> `packages/core/src/supernova_core/utils/paths.py`（目录布局）。
> 注意：`docs/whitebox-blackbox-handoff.md` 中 repo-centric（`<REPO>/.supernova/deliverables`）
> 的说法已过时，当前代码为 session-centric（`workspaces/<session>/deliverables`，
> `DEFAULT_DELIVERABLES_SUBDIR = "deliverables"`）。

---

## 1. 目录骨架（session 级）

```
workspaces/<session>/                        # 扫描任务根（CLI/WEB 均同）
├── session.json                             # 会话元数据 + metrics（cost/turns/agents 汇总）
├── events.ndjson                            # 统一事件流 SSOT（web 实时页 SSE 消费）
├── workflow.log                             # 人类可读工作流日志（display 流产物）
├── logs/
│   └── diagnostic.log                       # logging 流 WARNING/ERROR 沉淀（与 workflow.log 分流）
├── agents/                                  # 审计层：agent 执行日志（JSON Lines）
│   └── {timestamp}_{agent_name}_attempt-{n}.log
├── prompts/                                 # 审计层：agent prompt 快照
│   └── {agent_name}.md
├── scratchpad/                              # agent 工作草稿目录（prompt 变量 {{SCRATCHPAD_PATH}} 注入）
├── scan-config.yaml                         # WEB 提交的扫描配置（含认证凭据，收尾清理明文）
├── auth-state.json                          # 登录态快照（单身份；收尾清理）
├── auth-state-{id}.json                     # 登录态快照（多身份 per-account）
├── identity-manifest.json                   # 多身份清单（authz 越权对比用）
├── deliverables/                            # 产物根（三桶布局）
│   ├── .git/                                # deliverables git 隔离仓（GitManager per-agent commit）
│   ├── whitebox/                            # 白盒桶
│   │   ├── intermediate/                    # 白盒管线中间产物（tier 判定权威判据）
│   │   │   └── .poc_checkpoint.json         # PoC 断点续跑检查点
│   │   └── .whitebox-archive/<run_ts>/      # 白盒 resume rewind 归档（旧产物按 agent 移入）
│   ├── blackbox/                            # 黑盒桶
│   │   ├── intermediate/                    # 黑盒管线中间产物
│   │   └── .blackbox-archive/<run_ts>/      # 黑盒 rerun 归档（evidence/findings/report）
│   └── combined/                            # 组合桶（session 级融合报告占位）
├── blackbox-runs/<run_id>/                  # 每个黑盒 run 独立子任务根（web 组合扫描）
│   ├── session.json                         # run 级会话元数据
│   ├── events.ndjson                        # run 级事件流（web run 页消费）
│   ├── workflow.log / logs/diagnostic.log   # run 级日志
│   └── deliverables/blackbox/               # run 级黑盒产物（同 §3 结构）
├── combined/<run_id>/                       # per-run 融合报告目录
│   └── combined_report.md                   # 白黑盒融合报告
└── auth-probes/<probe_id>/                  # WEB 认证验证探针（"测试登录"）
    ├── scan-config.yaml                     # 探针配置（含凭据，收尾清理）
    ├── events.ndjson                        # 登录逐步事件流（verify-log 回看）
    └── auth-state.json                      # 验证成功后的登录态快照
```

tier 判定规则（`classify_tier`）：

1. 路径含 `intermediate/` 段 → 中间产物（新结构权威判据，未登记的新产物也命中）；
2. 桶平铺旧结构按 `INTERMEDIATE_FILE_PATTERNS` 文件名模式兜底（queue/index/graph/gap 报告类）；
3. 都不命中 → 交付物（给人看的安全结论，桶顶层）。

---

## 2. 白盒中间产物（`deliverables/whitebox/intermediate/`）

### 2.1 代码索引阶段（确定性 code-index）

| 文件 | 说明 |
|---|---|
| `code_index.json` | 代码索引（FuncBlock / SinkCallSite / SourcePoint，dataflow 视图代码片段来源） |
| `code_index_summary.md` | 索引摘要（人可读） |
| `parameter_graph.json` | 参数图：source→sink 数据流（`taint_flows[].propagation_steps` 含 code_location / transformation） |
| `entry_points.json` | 入口点清单（endpoint 清单，黑盒端点验证也读它） |
| `rule_gap_report.json` | sink 规则缺口报告 |
| `source_gap_report.json` | source 召回缺口报告 |
| `storage_gap_report.json` | 二阶存储污点缺口报告 |

### 2.2 pre-recon / recon 阶段

| 文件 | 说明 |
|---|---|
| `pre_recon_deliverable.md` | pre-recon 代码分析交付物（桶顶层） |
| `recon_deliverable.md` | recon 侦察结论（黑盒复用输入之一） |
| `framework_analysis.json` | 框架识别结果（路由提取） |
| `frontend_mapping.json` | 前端路由→API 映射 |
| `route_chains.json` | 路由链构建结果 |

### 2.3 漏洞分析双轨（`vc ∈ injection / xss / auth / authz / ssrf / attack_chains`）

| 文件 | 说明 |
|---|---|
| `{vc}_llm_queue.json` | LLM 轨候选队列（含 `dataflow_steps` 结构化路径） |
| `{vc}_gitnexus_queue.json` | GitNexus 确定性轨队列 |
| `{vc}_exploitation_queue.json` | 双轨合并后漏洞队列（SSOT；黑盒 preflight 的核心输入） |
| `{vc}_analysis_deliverable.md` | 漏洞分析交付物（黑盒复用输入之一） |
| `{vc}_chain_verdicts.json` | 链路级判定（vulnerable / safe 及原因，safe 链也进） |
| `{vc}_safe_vectors.json` | LLM safe 向量（safe-only 树来源） |
| `attack_chains.json` | 攻击链双轨合并结果 |
| `gitnexus_track_status.json` | GitNexus 轨道 fail-fast 状态汇总（merger/report 读它开轨标红） |
| `audit_plan.json` | 审计计划 |
| `dataflow_view.json` | 数据流视图（web 扫描详情页「数据流」tab 唯一数据源，见 §6） |

### 2.4 PoC 生成阶段

| 文件 | 说明 |
|---|---|
| `exploitable_poc_collection.md` | PoC 集合（bucket 顶层交付物） |
| `.poc_checkpoint.json` | PoC 断点续跑检查点（隐藏文件，tier 模式 `.*checkpoint*.json` 命中） |

### 2.5 白盒最终报告

| 文件 | 说明 |
|---|---|
| `comprehensive_security_assessment_report.md` | 白盒综合安全评估报告（桶顶层） |

---

## 3. 黑盒中间产物（`deliverables/blackbox/`）

| 文件 | 说明 |
|---|---|
| `endpoint_verify.json` | 端点 live 验证结果（exploitation 前置，读白盒 `entry_points.json` + auth-state） |
| `{vc}_exploit_verdicts.json` | 结构化 exploit 判定（`intermediate/`，PoC 生成器输入） |
| `{vc}_exploitation_evidence.md` | 各漏洞类利用证据（coverage 检查基准） |
| `{vc}_findings.md` | findings 渲染分块（报告组装输入） |
| `comprehensive_security_assessment_report.md` | 黑盒综合安全评估报告（桶顶层） |

黑盒复用的白盒交接输入（读 `deliverables/whitebox/`）：
`{vc}_exploitation_queue.json`、`{vc}_analysis_deliverable.md`、`recon_deliverable.md`、`entry_points.json`。
preflight 校验见 `has_valid_whitebox_results`（文件存在 + `vulnerabilities` 非空数组）。

---

## 4. 组合扫描与多仓关联产物

| 文件 | 位置 | 说明 |
|---|---|---|
| `combined_report.md` | `combined/<run_id>/` | 白黑盒融合报告（per-run，web run 级报告接口读取） |
| `cross-service-topology.json` | 关联 deliverables 根 | 跨服务调用拓扑 |
| `trust-boundaries.json` | 同上 | 信任边界 |
| `correlation-report.md` | 同上 | 多仓关联报告 |
| `{vc}_exploitation_queue.json`（关联合并版） | 同上 | 跨仓合并后的漏洞队列 |

---

## 5. WEB 侧 / 运行时辅助产物

| 文件 | 位置 | 说明 |
|---|---|---|
| `scan-config.yaml` | `workspaces/<session>/` | WEB 扫描配置（认证凭据；启动清残留 + 收尾删明文） |
| `auth-probes/<probe_id>/*` | workspace 下 | 认证验证探针（scan-config.yaml / events.ndjson / auth-state.json） |
| `.whitebox-archive/<run_ts>/` | `deliverables/whitebox/` | 白盒 resume rewind：目标 agent 及之后的旧产物按 agent 归档 |
| `.blackbox-archive/<run_ts>/` | `deliverables/blackbox/` | 黑盒 rerun：旧 evidence / findings / report 归档（重名加序号） |
| `deliverables/.git/` | deliverables 根 | git 隔离仓：每个 agent 产物 commit 一次（resume/审计基础） |
| `.playwright/cli.config.{session_id}.json` | 被扫 repo 源码目录 | Playwright stealth 配置（按 agent session 隔离 storage/proxy） |
| `.playwright/scripts/stealth.js` | 被扫 repo 源码目录 | 反检测 init script |
| `.playwright/state/{session_id}/` | 被扫 repo 源码目录 | 浏览器会话存储目录 |

> Playwright 三项写在被扫 repo 内（browser engine 运行时需要），不属于 workspace 产物树，
> 但同属扫描过程落盘的中间文件，排查"repo 被污染"类问题时需要知道。

---

## 6. Web「数据流」页的数据来源

`dataflow_view.json` 是唯一数据源，**Web 端不解析其它产物**：

```
5 类白盒 intermediate 产物
  （{vc}_exploitation_queue.json 兜底 {vc}_llm_queue.json、{vc}_chain_verdicts.json、
   {vc}_safe_vectors.json、parameter_graph.json、code_index.json）
        │  扫描末期 activity: run_assemble_dataflow_view
        │  → core 纯函数 assemble_dataflow_view（方案 B 写时组装，失败不阻塞扫描）
        ▼
deliverables/whitebox/intermediate/dataflow_view.json
        │  GET /{ws}/scans/{scan_id}/dataflow（原样透传，缺产物 → 404 空态）
        ▼
前端扫描详情页「数据流」tab
```

组装规则（spec 2026-08-20）：taint 类（injection/xss/ssrf）每 sink 一棵树，双轨枝条共存
（`track: gitnexus | llm`）；auth/authz 无 taint 流，降级为防护位链（ok/missing/ineffective）；
全部产物缺 → 不落盘（`skipped`，非阻塞）。降级语义：黑盒-only 无白盒桶 → tab 空态；
组合扫描读 whitebox 桶；`SUPERNOVA_LLM_TRACK_ENABLED=0` 时 LLM 枝全无但视图仍完整。

---

## 7. 与 `INTERMEDIATE_FILE_PATTERNS` 的对应（tier SSOT 登记表）

`packages/core/src/supernova_core/models/deliverables.py` 登记的中间产物文件名模式，
新增管线产物时在此登记（web 读侧 tier 判定零改动）：

| 模式 | 实例 |
|---|---|
| `code_index.json` / `entry_points.json` / `code_index_summary.md` | §2.1 |
| `parameter_graph.json` | §2.1 |
| `attack_chains*.json` | `attack_chains.json`、`attack_chains_gitnexus_queue.json`、`attack_chains_llm_queue.json` |
| `route_chains.json` / `framework_analysis.json` / `frontend_mapping.json` | §2.2 |
| `*_llm_queue.json` / `*_gitnexus_queue.json` / `*_exploitation_queue.json` | §2.3 |
| `*_exploit_verdicts.json` | §3 |
| `endpoint_verify.json` | §3 |
| `rule_gap_report.json` / `source_gap_report.json` / `storage_gap_report.json` | §2.1 |
| `gitnexus_track_status.json` / `audit_plan.json` | §2.3 |
| `.*checkpoint*.json` | `.poc_checkpoint.json` |
| `dataflow_view.json` | §2.3 / §6 |
| `*_chain_verdicts.json` / `*_safe_vectors.json` | §2.3 |

不在模式清单、但按目录判据归 intermediate 的：`blackbox/intermediate/` 下所有文件
（`{vc}_exploit_verdicts.json` 已在清单；`endpoint_verify.json` 同）。
归 deliverable（桶顶层）：各 `*.md` 交付物与两份综合报告。
