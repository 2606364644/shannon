# Auto Cross-Repo Topology Discovery Design

## Context

跨仓关联扫描链路已经可用：`MultiRepoConfig` 声明 `repos/relations`，编排器按配置执行子仓白盒，再对每条已声明 relation 运行 `cross-repo-correlation` Agent，产出 `cross-service-topology.json`、信任边界、候选攻击链和合并 queue。Web 端也已经恢复 ScanNewPage 的跨仓表单、YAML 面板、进度和结果视图。

当前瓶颈在扫描前的拓扑输入：

- `RepoSpec.role` 是单值 `entrypoint|backend`，前端用 segmented 单选让用户逐仓标注。
- 表单只自动生成“第一个入口 → 每个 backend”的星型边；两个及以上入口、一个入口调用多个后端、多个入口共享后端、backend→backend、多跳和环都要手写 YAML。
- 扫描中的 per-edge Agent 虽然能报告 `declared-missing`，但它每次只分析一条已声明边；发现未声明目标时只能把线索放在 `error/evidence`，不会把新边加入本轮 topology，也不能改变本轮已确定的子仓扫描输入。
- 现有结果图 `TopologyGraph` 是只读 SVG，不适合作为人工确认前的编辑器。
- YAML relation 本身并未限制星型，`from/to` 可以是任意已声明仓库；问题主要是数据建模和前端交互，而不是编排器无法执行复杂图。

因此本设计把“AI 建图”放在白盒扫描之前，作为可失败、可人工修正、可审计的预分析阶段；确认后的图仍转换为现有 `MultiRepoConfig`，避免重写扫描内核。

## Goals / Non-Goals

**Goals:**

- 用户只选择当前工作区中的仓库列表，不需要预先知道谁是入口、谁是后端。
- AI 基于源码/配置/proto 证据推断外部入口能力、服务端实现和有向调用边，支持一个仓库同时具备多种能力。
- 一等支持多入口、入口多扇出、多入口共享后端和多跳拓扑；入口数量、出度、入度不由 UI 或数据模型限制为 1。
- 提供可拖拽拓扑编辑器和证据面板，让人工确认成为启动扫描前的强制门禁。
- 确认结果完整落回现有 relations 协议和跨仓编排，不改变单仓白盒、per-edge 关联与黑盒验证语义。
- AI 建图失败时保留手工图编辑/YAML 兜底，不能让功能变成扫描阻塞点。
- 控制成本、并发、超时和缓存，并保留审计/计费信息。

**Non-Goals:**

- 不在预分析阶段发现或判定漏洞，不产生 exploitation queue，不改变双轨白盒检测。
- 不做全语言完整 RPC 框架解析器；第一版目标是可审计候选图，允许低置信和空结果。
- 不做跨工作区仓库选择。
- 不把 gRPC/HTTP/GraphQL 以外的传输自动纳入启动配置；未知协议只能显示为待映射线索。
- 不在第一版引入图可视化第三方依赖。

## Decisions

### 1. 预分析是独立拓扑 Agent，不复用扫描期 per-edge Agent

新增 `CROSS_REPO_TOPOLOGY_DISCOVERY` agent 和 `cross-repo-topology-discovery.txt` prompt。它接收全部选定仓库的绝对路径和导航 manifest，输出整个候选图的 JSON；而不是对 N×(N−1) 条边各跑一次现有 Agent。

理由：

- per-edge Agent 的前提是边已经存在，无法在扫描前补全服务图。
- 全图 Agent 可以同时看到 proto 定义、client import、服务注册、路由和配置，减少单边误判。
- 规模上限和 manifest 可以控制上下文，避免 O(N²) Agent 调用。

Agent 只输出候选，不做漏洞判定。该能力不属于 inj/xss/ssrf 的 LLM vuln 轨，也不向 vuln prompt 添加任何确定性 hints；manifest 只包含语言、框架、proto/服务名、路由/客户端线索等导航事实，明确不包含 source/sink/漏洞线索。

### 2. 先生成有界导航 manifest，再让 AI 自主读源码

后端在启动 LLM 前做轻量只读扫描：

- 仓库识别：语言、包名/module 名、主要框架；
- 服务端线索：proto service、gRPC server 注册、HTTP route/controller、GraphQL schema；
- 客户端线索：proto import、generated stub、RPC client 初始化、HTTP base URL/service name、GraphQL client；
- 配置线索：服务发现名、环境变量、部署配置中的 upstream 名称。

manifest 有文件数、行数和输出大小上限，只作为导航，不替代 AI 读码。AI 必须打开源码验证候选边，并在输出中给出 `repo/file/line/snippet` 证据。

备选方案是让 AI 从零 grep 全部仓库。实现简单，但大仓容易把轮次耗在文件发现上；manifest 可显著降低成本。完整静态解析则误报/框架覆盖成本高，不适合第一版。

### 3. 服务角色建模为能力集合，兼容旧 `role`

`RepoSpec` 增加可选 `roles: [entrypoint|backend]`：

- 旧 YAML 只写 `role` 时，解析为单元素能力集合，完全兼容。
- 新 YAML 可写 `roles: [entrypoint, backend]`；`role` 保留为旧客户端兼容字段/展示主标签。
- 配置校验使用有效能力集合：至少一个 `entrypoint`，relation 引用仍必须已声明。
- `entrypoint` 表示该仓库存在外部入口能力；`backend` 表示其作为服务端/被调用方可参与图。二者不互斥。
- topology 输出同时提供 `roles` 和 legacy `role`，旧结果图仍可读 `role`，新编辑器读 `roles`。

备选方案是继续用单值 `role`，因为 relation 已允许任意节点作为 `from/to`。但这会把“可同时是对外入口和下游调用方”的仓库误压缩成一个标签，影响 Agent 方法论、入口可达性解释和 UI 展示，无法满足真实微服务场景。

### 3.1 拓扑按一般有向图建模，而不是按入口数或扇形建模

确认拓扑的数据形状是“节点 + 有向边集合”，不引入“唯一入口”“每个 backend 只有一个 caller”或星型假设：

- **两个入口**：`web` 与 `admin` 都可标记 `entrypoint`，可分别连接不同后端。
- **一个入口调用多个微服务**：`web -> order-svc`、`web -> user-svc`、`web -> payment-svc` 同屏展示，出度不受限制。
- **多个入口调用多个微服务 / M:N**：`web -> order-svc`、`web -> user-svc`、`admin -> order-svc`、`admin -> payment-svc` 同时存在；共享后端不合并。
- **多跳 / backend 互调**：`web -> order-svc -> user-svc` 中 `order-svc` 可同时是某条边的 `to` 和另一条边的 `from`。
- **共享可达性**：一个 backend 方法只要从任一入口可达即标 external，`reachable_from` 保留所有入口来源，攻击链不得折叠成单一入口。

边身份使用有序三元组 `(from, to, protocol)`。相同 `from/to` 但协议不同的边保留为独立候选；确认时如果同一有序仓库对存在多协议候选，要求用户保留一个或明确分别提交。AI normalizer 不得因为多个入口指向同一服务而合并边，也不得只选择第一个入口做默认 from。

编辑器初始布局可按能力/拓扑层级辅助排版，但布局不是语义：拖动节点不改变 roles/relations，重置布局也不会删边。若选中仓库形成孤立节点或多个连通分量，确认时显示警告；用户可以移除该仓库，或显式保留它作为参与白盒扫描但不参与任何关联边的参考仓库，不能静默浪费扫描或静默丢弃。

现有 `MultiRepoConfig.relations` 本身就是有向边列表，已经能表达上述形态；需要修正的是前端和预分析候选图的假设，而不是重造扫描配置协议。

### 4. 分析任务使用异步生命周期和 workspace 隔离

新增 API：

- `POST /api/workspaces/{ws}/correlation-topology/analyses`：body `{repos: string[], refresh?: boolean}`，返回 `202 + analysis_id`；
- `GET /api/workspaces/{ws}/correlation-topology/analyses/{id}`：返回 queued/running/completed/failed/cancelled、进度、错误、候选图、usage/cost；
- `DELETE .../{id}`：请求取消。

任务状态原子落盘在 workspace 专属分析目录，Agent 审计和结构化结果同目录保存。服务重启后 running 任务标记为 `interrupted`，不自动重跑烧 token；前端可显式重试。

分析 fingerprint 由排序后的仓库名、repo HEAD/dirty 状态（非 git 仓库用有界元数据指纹）和协议版本组成。未强制 refresh 且 fingerprint 命中近期 completed 结果时复用结果；“重新分析”可绕过缓存。

并发、仓库数量和超时使用环境配置，默认从紧：仓库数量上限、单任务并发、Agent max turns 和 wall-clock timeout 均可配置。失败返回可读错误并引导手工模式。

### 5. AI 输出必须经过确定性规范化与防幻觉校验

结构化输出至少包含：

- `nodes`: repo、建议 `roles`、入口/服务端证据、能力置信度；
- `edges`: from/to、`grpc|http|graphql`、confidence、service/method、client/handler evidence；
- `uncertain`: 无法确认或疑似未知协议的线索；
- `coverage`: 每仓是否完成关键文件检查，防止 Agent 只看 manifest 就编图。

后端规范化规则：

- 节点必须是请求中的仓库名，不能新增或改名；
- 禁止 self-loop，重复有向边合并并保留最强证据；
- 未知协议/引用泄漏到 `uncertain`，不进入可启动 relations；
- evidence 路径必须限制在该 repo 内且文件存在，行号/文本无效则降置信并标记；
- 高置信边至少有 client 侧证据；建议 `backend` 能力优先看服务端实现或入边证据；
- 无法推断入口时返回空建议和解释，不由后端默认捏造 entrypoint。

规范化后的候选图是“建议”，不是扫描结论。每条边保留 `origin=ai|manual` 和编辑状态，人工增删改不覆盖 AI 原始 evidence。

### 6. 图编辑器复用 SVG 路线并支持多角色/多跳

新增 `TopologyEditor`，不引入 React Flow/D3：

- 初始布局支持多入口列/区和入口多扇出、共享后端的边汇聚；用户可自由拖动节点，位置仅存前端 draft；
- 节点展示 repo、能力 chips、入/出度；可切换 `entrypoint/backend` 能力；
- 边可选中、删除、禁用、修改协议；支持从节点连接柄拖出新边，或用边表键盘操作；
- 侧栏展示 AI 证据、置信度、服务/方法、coverage 与 uncertain 线索；
- 支持撤销/重做、自动布局重置；
- YAML 专家模式保留，编辑后进入未确认状态。

任何图编辑都会使“已确认”状态失效，需再次点击确认。确认校验至少一个入口能力、至少一条可执行边、协议合法、relation 引用存在、未启用 self-loop。通过后才生成 `CorrFormState`/YAML 并启用“启动跨仓扫描”。

### 7. 只读工具面需要双引擎硬约束

拓扑 Agent 的工具面限定 `read_file/glob/grep`，禁止 write/edit/bash/task/browser。为此扩展 provider 调用参数的 engine-neutral `tool_policy="readonly-code"`：

- openai 引擎注入现有 read/glob/grep 工具集；
- Codex/Claude 引擎禁用写入、Shell、网络和子代理工具，仅保留读/搜索内置工具；
- 审计日志记录工具调用，源码仓库本身保持只读。

仅靠 prompt 声明“只读”不足以防御源码中的 prompt injection，因此这是预分析上线前的硬要求。现有 cross-repo correlation Agent 可后续迁移同一 policy，但不属于本 change 的行为回归前提。

## Risks / Trade-offs

- [大仓分析慢且贵] → 仓库数/manifest 输出/turns/timeout 全部设上限；结果缓存；显式重试；失败降级手工。
- [多入口/M:N 图边数增长] → 全图 Agent 一次输出而非按边调用；normalizer 保留每条独立有向边；布局按扇出/扇入聚类并提供边表兜底。
- [AI 漏边] → 候选图必经人工确认；扫描期 per-edge Agent 继续运行并报告 declared-missing；编辑器保留手工加边。
- [AI 幻觉边] → 节点/路径/协议/行号确定性校验，无有效证据自动降级；高置信标准固定。
- [多角色改变配置兼容性] → `role` 继续可解析，`roles` 为增量字段；旧 YAML 不迁移也有效。
- [异步任务在 web 重启后丢失] → 状态落盘，重启标记 interrupted，不自动重跑；前端显式 retry。
- [SVG 编辑器复杂度] → 第一版只做节点拖拽、连接柄、边表和证据面板；自动布局可重置；不做自动排版优化。
- [只读工具策略双引擎差异] → 用双引擎单测/探针锁定同一可用工具集；无法硬限制时该引擎返回配置错误而不是降级为可写工具。
- [未知 RPC 框架] → 显示为 uncertain/protocol hint，不伪装成 grpc/http/graphql，不阻塞用户手工映射。

## Migration Plan

1. 落地 core 模型与 Agent：新增 roles 兼容解析、结构化模型、normalizer、readonly prompt；先不改前端主路径。
2. 落地 web 分析任务 API 与持久化状态；用 fixture 和 pipeline-testing 验证生命周期、缓存、失败和取消。
3. 增加 frontend draft state 与 TopologyEditor；先与现表单并存，通过 feature flag/dev route 验证交互。
4. 替换 ScanNewPage 跨仓分支为“多选 → 分析 → 编辑/确认 → 扫描”流程，保留 YAML 与手工兜底入口。
5. 更新 orchestrator/prompt 读取有效 roles，并跑既有跨仓回归，确保旧单角色 YAML 行为不变。
6. 双引擎真机探针验证 readonly-code 工具面和结构化输出。

回滚时可以隐藏新入口，保留旧手工表单；`roles` 字段向后兼容，无需迁移删除。

## Open Questions

- 默认最大仓库数建议先设 8 还是 12，需要用真实微服务工作区实测成本后定稿。
- 拓扑分析结果缓存 TTL 默认值（建议 24h）是否需要 workspace 配置覆盖。
- 现有 cross-repo correlation 结果 Agent 是否在本次同步迁移 readonly-code，还是在后续维护 change 中处理。
