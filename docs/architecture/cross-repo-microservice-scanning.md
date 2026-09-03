# 跨仓微服务扫描

跨仓能力分为两层：

1. **扫描前自动拓扑预分析**：只读 AI agent 提出候选服务图，用户确认后转为 `MultiRepoConfig`。
2. **跨仓关联扫描**：逐仓执行/复用白盒，再验证服务间调用、合并漏洞 queue、拼多跳攻击链并做跨仓裁决。

## 自动拓扑预分析

新建跨仓扫描页默认使用自动模式，用户选择同一 workspace 中至少 2 个 ready 仓库后触发 `TopologyAnalysisWorkflow`。

![Cross-repo topology discovery flow](images/cross-repo-topology-discovery.svg)

### 用户流程

1. 选择仓库，点击“自动关联分析”。
2. 轮询 queued/running/completed。
3. 在 SVG 图或关系/服务表中复核：
   - 调整节点、边、protocol；
   - 启用/禁用/删除边；
   - 切换 `entrypoint/backend` 能力；
   - 每仓选择现扫或复用 workspace；
   - 孤立仓库可标记参考仓库。
4. 确认拓扑。语义编辑后回到未确认状态；布局拖拽不影响确认。
5. 提交确认后的 YAML，启动跨仓扫描。

失败、超时、取消、重启中断或 malformed 输出不会自动重跑；用户可显式重试或切手工表单/YAML。

### 自动识别依据

分析前先构建 bounded navigation manifest：

- 框架线索：package.json、go.mod、pyproject、Next/React/Express/FastAPI/Django/Spring/grpc/GraphQL。
- 调用线索：HTTP client、gRPC client/stub、proto、GraphQL document、URL/host/service name。
- 服务端线索：handler/controller、gRPC service、GraphQL resolver、端口/容器配置。
- 每条 clue 必须带 repo 内 file:line 与 snippet。

限制：

| 项 | 默认 |
|---|---:|
| 每仓文本文件 | 1,200 |
| 单文件读取 | 256 KiB |
| prompt payload | 64 KiB |
| 每仓 clues | 120 |
| 单 clue snippet | 240 chars |

manifest 只含导航事实，不含 source/sink 或漏洞 hints。

Agent 使用 `tool_policy=readonly-code`：

- openai-agents：只暴露 `read_file/glob/grep`
- Claude 引擎：只允许 `Read/Glob/Grep`，严格限制在选中 repo roots

所有工具事件写入 analysis-local `tool-audit.ndjson`。

### 证据与归一化

结构化输出必须包含 nodes、edges、uncertain、coverage。可执行协议仅：

- `grpc`
- `http`
- `graphql`

边身份是 `(from, to, protocol)`；重复身份合并，M:N、fan-out、multi-hop、backend-to-backend 与 cycle 保持独立。

高置信 executable edge 需要**有效 client-side evidence**；handler evidence 只能增强 backend capability，不能替代 caller 证据。未知协议和不可验证线索进入 `uncertain`，不会变成 confirmed relation。空图是合法结论，后端不会发明默认 entrypoint 或星型图。

归一化会验证：

- repo/file 必须存在；
- line/snippet 有效；
- from/to 必须是选中 repo；
- protocol 枚举合法；
- 重复边合并；
- 证据无效则保留但降级。

### 缓存与限制

| 环境变量 | 默认 |
|---|---:|
| `SUPERNOVA_TOPOLOGY_MAX_REPOS` | 8 |
| `SUPERNOVA_TOPOLOGY_MAX_CONCURRENT` | 1 |
| `SUPERNOVA_TOPOLOGY_TIMEOUT_SECONDS` | 900 |
| `SUPERNOVA_TOPOLOGY_MAX_TURNS` | 30 |
| `SUPERNOVA_TOPOLOGY_CACHE_TTL_SECONDS` | 86400 |
| `SUPERNOVA_TOPOLOGY_MAX_STORED_ANALYSES` | 100 |

状态原子写在分析目录的 `state.json` 中，字段包含请求、fingerprint、navigation manifest、progress、normalized result、raw output、usage/cost 与错误信息；工具调用另追加到同目录 `tool-audit.ndjson`：

```text
workspaces/<ws>/correlation-topology/analyses/<analysis_id>/
  state.json
  tool-audit.ndjson
```

指纹输入包含协议版本、仓库名、Git HEAD、bounded dirty status 或非 git 元数据指纹。未显式 refresh 时，同指纹 completed 结果直接复用，不消耗 provider token。Web 重启将 queued/running 标为 interrupted，不自动重交。

## 跨仓扫描编排

确认后的配置进入 `run_cross_repo`：

### 1. 每仓白盒计划

`plan_repo_scans(config)`：

- repo spec 声明 workspace → 复用该 workspace；
- 无 workspace 且有 path → 现在执行白盒；
- 复用时若源码 mtime 晚于 session 创建时间，记录版本漂移 warning，不阻断。

每仓完成状态写入 correlation workspace 的 `events.ndjson`。

### 2. 收集产物

`run_correlation_phase` 从每个 repo workspace 解析：

- `<vuln>_exploitation_queue.json`（优先 whitebox/intermediate，旧平铺兜底）
- `entry_points.json`
- `dismissed_findings.json`

并构造 `ServiceArtifacts` 目录导读，告诉后续 agent 每个文件的作用与缺失状态，而不是把所有文件内容塞进 prompt。

### 3. per-edge 关联

对配置中每条 `from -> to` 关系并发运行 `cross-repo-correlation` agent。输入：

- 声明 protocol
- role map / multi roles
- 两仓 repo path
- artifacts guide
- 输出 schema

agent 必须定位：

- from 侧 client call site；
- to 侧 handler/method；
- boundary/exposure；
- 可选跨仓 flow：from 入口输入经 RPC 方法到达 to 仓 sink/queue finding。

输出状态：

- `ok`：调用和 handler 均有证据
- `low`：单侧证据
- `unverified`：无有效结构化输出
- `declared-missing`：发现实际调用目标与声明不符
- `error`：异常，单边隔离

per-edge 失败不会拖垮整个关联阶段；错误边透明保留在报告。

### 4. 确定性校验与多跳链

`merge_validation.py` 做零推断防幻觉：

- `validate_vuln_refs`：flow 中 `vuln_id` 若不在对应 service queue ID 集合，标 `invalid_ref`，不删除。
- `assemble_multi_hop_chains`：只有首边已有攻击 flow、下游邻接边有 calls 时，才按边邻接拼候选多跳链；标记 `basis=edge-adjacency`、`confidence=structural`，不宣称函数级可达。
- 防环：路径节点不重复。

### 5. 交付物

```text
<correlation-workspace>/deliverables/
  cross-service-topology.json
  trust-boundaries.json
  <vuln>_exploitation_queue.json
  cross-service-flows.json        # 同时包含 flows 与 multi_hop_chains
  correlation-report.md
  adjudication-log.json           # 阶段 B 成功后追加
```

merged queue 中每条 finding 标注 `service` 与 `cross_service_source`。跨仓 flow 的 `vuln_refs` 保留 queue/agent-discovered 来源与位置。

## 阶段 B：跨仓裁决

单仓结论可能被跨服务上下文改变：

- 单仓判非漏洞，但 gateway/RPC 可达 → 翻案候选；
- 单仓报漏洞，但远程服务有防护 → 降级/证伪；
- queue finding 需要确认跨服务可达；
- dismissed finding 需要维持原判或升级。

`build_adjudication_batches` 按 `(service, vuln_class, origin)` 组织发现，queue 与 dismissed 都进入；可达性/暴露面相关 dismiss 排前。每批最多 15 条，超出分片。

`run_adjudication_phase` 给 agent：

- full artifacts guide
- topology / flows / multi-hop chains
- finding batch

每张卡必须输出 direction、finding_ref、conclusion、跨服务上下文、分析过程、验证证据、论证和置信度。批失败/漏判用 error/needs-review 占位卡补齐，不静默丢失。`sanitize_adjudication_cards` 会拦截 direction 与 conclusion 矛盾的输出。

阶段 B 失败不阻断阶段 A 交付；若成功，追加 adjudication 产物和报告章节。

## 黑盒闭环

黑盒扫描可通过 `correlated_workspace` 使用跨仓 merged queue 和 topology：

- 关联 workspace 中任一有效 queue 可作为 recon-skip 来源；
- topology/trust boundaries 注入 exploitation prompt；
- 动态 exploit 仍按单仓目标/认证/HOST 配置执行。

详见 [黑盒验证](blackbox-verification.md)。

## 故障排查

| 现象 | 处理 |
|---|---|
| 422 invalid repositories | 至少 2 个同名 ready 仓库且属同一 workspace |
| 429 too many analyses | 等待 active job 或审慎调大并发 |
| provider_failed / timeout | 查看 state error/usage；显式重试或手工模式 |
| interrupted | Web 重启预期状态；需显式重试 |
| empty graph | 查看 coverage/uncertain，手工补边或换更多仓库 |
| unknown protocol | 保留 clue，只有证据充分才手工映射为 grpc/http/graphql |
| edge unverified/error | 查 per-edge agent 产物与 artifacts guide |
| flow vuln_id 标 invalid_ref | 修正/忽略幻觉引用，不能自动套用 finding |

## 验证入口

- `packages/core/tests/topology/test_discovery.py`
- `packages/core/tests/topology/test_agent_and_policy.py`
- `packages/multi/tests/test_cross_repo_topology_matrix.py`
- `packages/multi/tests/test_topology_analysis_worker.py`
- `packages/multi/tests/` 中 orchestrator / adjudication 相关测试
