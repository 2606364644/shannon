## ADDED Requirements

### Requirement: Start cross-repo topology analysis from selected repositories
系统 SHALL 允许 workspace 成员在跨仓扫描创建页选择当前 workspace 中至少两个已存在仓库，并启动只读拓扑预分析；该预分析 MUST NOT 创建子仓扫描、启动 correlation workflow 或修改源码仓库。

#### Scenario: Start analysis
- **WHEN** 用户选择 `web`、`order-svc`、`user-svc` 并点击自动关联分析
- **THEN** 系统创建 workspace 隔离的异步分析任务并返回可轮询的 analysis id
- **AND** 系统不创建任何白盒子扫描或关联主扫描

#### Scenario: Reject invalid selection
- **WHEN** 用户选择的仓库少于两个、包含不存在仓库或超过配置上限
- **THEN** 系统返回 422 且给出具体仓库名或上限原因
- **AND** 不启动 LLM 调用

### Requirement: AI proposes an evidence-backed service graph
拓扑分析 SHALL 使用只读代码阅读能力分析选定仓库的入口、服务端实现和跨仓调用，并返回节点能力、有向边、协议、置信度、服务/方法线索和代码证据。AI 输出 MUST 只作为候选建议，MUST NOT 被呈现为漏洞结论或已验证攻击链。

#### Scenario: Infer a directed edge
- **WHEN** `web` 中的路由初始化 generated gRPC client 并调用 `order.v1.OrderService/CreateOrder`，且 `order-svc` 注册了对应 handler
- **THEN** 分析结果包含 `web -> order-svc` 的 grpc 候选边
- **AND** 该边包含 client 和 handler 侧的 repo、file、line、snippet 证据及置信度

#### Scenario: One repository has multiple capabilities
- **WHEN** 同一仓库既暴露 HTTP routes 又调用下游 gRPC 服务
- **THEN** 分析结果将该仓库标记为同时具备 `entrypoint` 和 `backend` 能力
- **AND** 每项能力分别展示证据

#### Scenario: No confident relation
- **WHEN** AI 无法在选定仓库间找到可验证调用关系
- **THEN** 分析成功返回空候选边和 coverage/uncertain 说明
- **AND** 系统保留手工编辑入口而不伪造默认星型图

#### Scenario: Infer fan-out from one entrypoint
- **WHEN** `web` 同时代码调用 `order-svc`、`user-svc` 和 `payment-svc`
- **THEN** 分析结果保留三条从 `web` 出发的独立候选边
- **AND** 不会因为当前表单的星型假设而截断或合并出边

#### Scenario: Infer shared backend across entrypoints
- **WHEN** `web` 和 `admin` 都是对外入口且都调用 `order-svc`
- **THEN** 分析结果同时保留 `web -> order-svc` 与 `admin -> order-svc`
- **AND** 两条边分别保存各自入口侧证据和置信度

### Requirement: Analysis results are normalized and auditable
系统 MUST 对 AI 结构化输出执行确定性规范化：过滤未选择仓库、self-loop、非法协议和无效证据，合并重复边，降级证据不足的边，并保留原始候选与被过滤项供用户查看。分析任务 MUST 持久化状态、审计日志、token/cost usage 和最终候选图。

#### Scenario: Invalid evidence is downgraded
- **WHEN** AI 输出的边引用不存在的文件、越出仓库的路径或不在文件内的行号
- **THEN** 该边证据被标记 invalid
- **AND** 该边置信度降低或被过滤，同时可在审计详情中查看原始输出

#### Scenario: Unknown protocol stays non-executable
- **WHEN** AI 发现疑似 Thrift 或 Dubbo 调用但配置协议仅支持 grpc/http/graphql
- **THEN** 系统在 uncertain 列表展示协议线索和证据
- **AND** 该边不得进入确认后的 MultiRepoConfig relations

### Requirement: Multi-entry and many-to-many topologies are first-class
拓扑分析、编辑器和确认校验 SHALL 支持 generally useful directed service graphs，包括多个入口、一个入口调用多个后端、多个入口共享一个或多个后端、多跳、backend 互调和环。系统 MUST 以有序边身份保留 from/to/protocol，MUST NOT 将共享后端的多个 caller 合并、丢弃非第一个入口或把图限制为星型。

#### Scenario: Two entrypoints call different backends
- **WHEN** 候选图为 `web -> order-svc` 且 `admin -> user-svc`
- **THEN** `web` 与 `admin` 均可标记 entrypoint
- **AND** 两条边都被保留并可在确认后提交

#### Scenario: One entrypoint fans out to multiple services
- **WHEN** 候选图包含 `web -> order-svc`、`web -> user-svc` 和 `web -> payment-svc`
- **THEN** 编辑器和确认结果保留全部出边
- **AND** 每条边可单独查看证据、修改协议、禁用或删除

#### Scenario: Multiple entrypoints share multiple backends
- **WHEN** 候选图包含 `web -> order-svc`、`web -> user-svc`、`admin -> order-svc` 和 `admin -> payment-svc`
- **THEN** 系统保留四条 M:N 有向边
- **AND** `order-svc` 的可达性记录同时包含 `web` 和 `admin` 来源

#### Scenario: Mixed multi-hop and shared backend topology
- **WHEN** 候选图包含 `web -> order-svc`、`admin -> order-svc` 和 `order-svc -> user-svc`
- **THEN** `order-svc` 同时作为入边目标与出边来源渲染
- **AND** 确认后的 relations 保留三边且多跳可达性不被合并

#### Scenario: Isolated repository warning
- **WHEN** 选定仓库中存在没有任何启用边的节点
- **THEN** 确认界面显示孤立节点警告
- **AND** 用户必须移除该仓库或显式保留为不参与关联边的参考仓库后才能确认

### Requirement: Users review and edit the candidate topology
系统 SHALL 提供拓扑确认界面，支持拖动节点、创建和删除边、启用/禁用候选边、修改协议、切换仓库入口/后端能力、查看 AI 证据、撤销/重做和重置布局。人工编辑 MUST NOT 删除 AI 原始证据，MUST 将对应元素标记为 manual 或 ai-modified。

#### Scenario: Adjust AI suggestion
- **WHEN** 用户删除低置信 AI 边并手动添加 `gateway -> user-svc` HTTP 边
- **THEN** 编辑器保留被删除 AI 边的原始证据
- **AND** 新边标记为 manual 且当前拓扑变为待确认

#### Scenario: Keyboard accessible editing
- **WHEN** 用户无法使用指针拖拽
- **THEN** 可以通过节点和关系表格完成同样的角色、协议、增删和确认操作

### Requirement: Confirmed topology gates scan submission
系统 SHALL 在跨仓扫描启动前要求用户确认有效拓扑。确认校验 MUST 至少包含一个 `entrypoint` 能力仓库、至少一条启用边、合法协议、已声明 from/to、无 self-loop，且每个仓库仍有重扫或可复用来源。确认后的拓扑 MUST 转换为现有 `MultiRepoConfig` repos/relations 格式，后续扫描行为 SHALL 复用现有跨仓编排。

#### Scenario: Confirm and start
- **WHEN** 用户确认包含 `gateway -> order-svc` 和 `order-svc -> user-svc` 的多跳拓扑并点击启动
- **THEN** 提交的 YAML 包含对应 relations
- **AND** 现有跨仓编排按两条边执行关联分析

#### Scenario: Unconfirmed topology blocks submission
- **WHEN** 用户在新增或编辑边后未再次确认拓扑
- **THEN** 启动跨仓扫描按钮保持禁用并提示需要确认

### Requirement: Configuration supports multiple repository capabilities
`MultiRepoConfig` SHALL 支持每个仓库声明多个 `entrypoint/backend` 能力，并保持仅使用旧 `role` 字段的 YAML 兼容。配置校验 MUST 基于有效能力集合判断是否至少一个入口，relation 引用 MUST 继续指向已声明仓库。

#### Scenario: Read legacy single-role YAML
- **WHEN** 配置只包含 `role: entrypoint` 或 `role: backend`
- **THEN** 系统将其解析为对应单能力集合
- **AND** 现有跨仓扫描结果不变

#### Scenario: Write dual-role YAML
- **WHEN** 仓库声明 `roles: [entrypoint, backend]`
- **THEN** 配置通过校验
- **AND** topology 和前端将该仓库展示为双能力节点

### Requirement: Analysis failures degrade to manual topology editing
系统 MUST 优雅处理 AI provider 失败、超时、取消、服务重启和结构化输出解析失败，返回可读状态并允许用户使用手工图编辑/YAML 完成拓扑。系统 MUST NOT 自动重复重跑已失败的高成本分析。

#### Scenario: Provider timeout
- **WHEN** 拓扑 Agent 超过配置的 wall-clock 或 turn 上限
- **THEN** 分析任务状态为 failed 并保留已产生 usage/cost
- **AND** 用户可以切换手工编辑或显式重试

#### Scenario: Web service restart
- **WHEN** 分析任务运行中 web 服务重启
- **THEN** 后续查询将该任务标记为 interrupted
- **AND** 系统不自动重新调用 LLM

### Requirement: Topology analysis is read-only and cost controlled
拓扑分析 MUST 以只读工具面执行，禁止写文件、Shell、子代理和浏览器工具；系统 MUST 应用仓库数量、并发、manifest 大小、turn 和超时限制，并复用相同 fingerprint 的近期 completed 结果除非用户显式刷新。

#### Scenario: Read-only enforcement
- **WHEN** Agent 尝试调用不在 readonly-code 工具白名单中的工具
- **THEN** 工具不可用或调用被拒绝
- **AND** 源码仓库内容未被修改

#### Scenario: Cache reuse
- **WHEN** 用户用未变更的仓库集合再次启动分析且未选择强制刷新
- **THEN** 系统复用近期 completed 结果
- **AND** 不产生新的 LLM token usage
