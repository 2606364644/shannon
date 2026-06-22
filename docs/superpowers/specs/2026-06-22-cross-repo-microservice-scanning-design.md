# 跨仓微服务扫描与关联分析设计

- **日期**:2026-06-22(评审修订 2026-06-23)
- **分支**:feat/fork-py
- **状态**:Design(待 review → writing-plans)
- **作者**:brainstorming 会话产出
- **修订**:2026-06-23 经代码事实核实修订(A1–A3 事实硬伤 + B1–B3 设计优化;A4 行号漂移留 plan 阶段复核)。各修订处以 `〔修订 2026-06-23·Xx〕` 标注

## 1. 背景与动机

Shannon 当前是**单仓库**扫描器:

- 白盒入口 `--repo` 硬编码单个 git 根(`packages/whitebox/.../cli/main.py:31`);
- `git_manager` 全程单 `repo_path`;`file_discovery.discover_security_files(repo_root)` 单根 `rglob`;
- `attack_chain_builder` 在单 repo 内、HTTP/前端向组装链(`framework_result` + `frontend_result`);
- `sink_merger` 只合并单 repo 内 sink 报告;`workspace_discovery` 的 `find_workspaces_by_url` 是"黑盒按 URL 复用同 repo 白盒队列",**不是**多 repo 聚合;
- 全仓 grep 对 `cross-repo` / `cross-service` / `multi-repo` **零命中**。

目标场景:**Node.js gateway 转发到 Go gRPC 后端**的微服务架构(可含多个后端、后端间互调)。用户诉求是**两仓各自单独扫描,再做跨仓结果关联分析**。当前架构无法支持——需要新建一条跨 workspace 的关联线。

〔修订 2026-06-23·A1〕原表述"pre-recon 能发现后端 gRPC RPC handler 入口"**被夸大**。经代码核实(`packages/core/src/shannon_core/code_index/`):`merge_entry_points()` 虽定义了 4 源融合(GitNexus / schema[Proto→handler] / framework convention / LLM),但**该函数在生产流水线从未被调用**——实际 `run_entry_point_fusion()` 只跑 2 源(deterministic + LLM),**schema 源(Proto→handler)从未接入**;`detect_entry_points()` 对 Go 只识别 `http.ResponseWriter`/`gin.Context`/`func main()`,**无任何 grpc/rpc 规则**;`parse_llm_entry_points()` 正则只认 `GET/POST/...`,LLM 若 free-text 提到 gRPC method 会被**硬编码成 `entry_type="http_route"`**(`entry_point_fusion.py:170`)。即**确定性层对 gRPC 全盲**(不止"错配")。这**强化而非推翻**决策 3——正因为确定性层是盲的,关联只能交给 Agent。两个仍成立的真问题:(a) LLM 偶发提及的 gRPC method 被**错配**成 http_route;(b) **拓扑误导**——单仓视角把后端 method 当对外攻击面,但真实可达性经 gateway 转发,单仓 exposure 判断错误。本设计不补确定性 proto parser(见 §3 决策 3),让关联 Agent 用拓扑正确可达性视图覆盖它(§6 职责⑥)。**附加局限(对应 §6 职责⑥ 的能力边界)**:关联层只能重标注 exposure、拓扑层串跨服务链,**无法补救单仓扫描因 gRPC 盲区已漏掉的 backend sink**——后端 sink 覆盖深度受限于单仓扫描本身的盲区。

## 2. 目标与非目标

**目标(本次范围)**

- 支持声明式多 repo 配置(gateway + N 个后端 + 后端间关系);
- 复用已有单仓扫描产物,或由编排器现扫;
- 由专用 Agent 推断跨服务调用关系、信任边界、候选跨服务数据流;
- 产出可人工复核的关联产物(候选 + 证据 + 置信度),合并两仓漏洞;
- **黑盒 gateway 层关联验证**:黑盒 `--url <gateway>` 复用关联 workspace,exploitation 消费 topology 在 gateway HTTP 层验证跨服务可达性与转发(§6.2)。

**非目标(显式排除,留待后续阶段)**

- **不**做全链路确定性数据流追踪(gateway 输入一路追到 gRPC sink 的确定性传播);
- **不**给 Shannon 补 proto parser / RPC handler 确定性入口发现;
- **不**做 gRPC 进程内验证(后端进程内 sink 执行,需 gRPC 客户端 + 后端可观测,留后续 §13);
- **不改单仓扫描内核,零单仓回归**:白盒 `--repo` / 黑盒单仓 `--repo`·`--latest` 路径行为完全不变(已核 `workflows.py:128-171` 单仓复用路径)。§6.2 黑盒扩展仅在 deliverables 解析处加**条件分支**(指定关联 workspace 才走新路径,否则原逻辑);`exploit_executor` 注入 topology 仅在**有 topology 时**触发;关联 Agent 对 `code_index` **只读复用**,不改其实现;
- **不**测 Agent 推断的"准确率"(需标注数据集,超出本次范围)。

## 3. 已定决策(brainstorming 产出)

| # | 决策 | 说明 |
|---|---|---|
| 1 | **范围 = 拓扑 + 边界(中)** | 服务拓扑 / 信任边界标注 / 跨服务调用识别 / 报告合并;不追到 sink |
| 2 | **形态 = 声明式编排器** | multi-repo 配置 → 依次跑单仓白盒 → 关联;不改单仓内核 |
| 3 | **机制 = Agent 推断,不写确定性解析器** | 关联逻辑交给 Agent(grep/read 代码),不写 proto parser、grpc-js 调用点 AST 提取器等。红利:Agent 能直接读 Go 代码理解 RPC handler,部分绕过 gRPC 盲区 |
| 4 | **落地 = 新增专用 `cross-repo-correlation` Agent** | 独立 prompt + 通用 code 工具,职责单一、可独立测试 |
| 5 | **范围 = 白盒关联 + 黑盒 gateway 层验证(本次)** | 黑盒纳入本次:基于关联 topology 在 gateway HTTP 层做跨服务关联验证;gRPC 进程内验证 + 全链路确定性留后续(§13) |
| 6 | **产物性质 = 候选 + 证据 + 置信度** | 概率性推断,非确定性结论,供人工复核 |
| 7 | **复用已有 workspace** | 配置可声明已有 workspace,编排器跳过扫描直接关联;`path` 仍必填(Agent 要读源码) |
| 8 | **多后端 = 图拓扑** | `relations` 是图(非树),支持 gateway 多后端 + 后端互调;Agent 走 per-edge 推断 + 全局合并 |
| 9 | **role 是语义源,服务名只是 label** | Agent 据 `role: entrypoint|backend` 判断入口,不靠服务名;`role` 为受限枚举,缺省 `backend`,至少需一个 `entrypoint` |

## 4. 架构总览

```
multi-repo.yaml  (声明 gateway 仓 + N 个后端仓 + relations 图)
        │
        ▼
shannon-multi 编排器(新增,很薄;不改单仓内核)
        ├──▶ [复用] 或 shannon-whitebox start --repo <gateway>   ─▶ workspace-gw/deliverables
        ├──▶ [复用] 或 shannon-whitebox start --repo <backend-k> ─▶ workspace-k/deliverables
        ▼
cross-repo-correlation Agent(新增)
   读:N 个 repo 路径 + N 份 deliverables + relations 图 + role 标注
   工具:grep(跨任意仓) / read_file / (复用 code_index 摘要)
   策略:per-edge 推断 → 全局合并
        │
        ▼
<out_workspace>/deliverables/   (独立关联 workspace,不回写原始产物)
   ├── cross-service-topology.json
   ├── trust-boundaries.json
   └── correlation-report.md
```

**黑盒延续(§6.2,本次范围)**:

```
<关联workspace>/deliverables/ (topology + boundaries)
        │  shannon-blackbox --url <gateway> <复用关联workspace 的 flag>
        ▼
exploit_executor 读 topology → gateway HTTP 层验证跨服务可达性与转发
        ▼
{vc}_exploitation_evidence.md (跨服务路径标注)
```

编排器对每个 repo 的分支逻辑:

```
if 声明了 workspace 且 deliverables 完整:
        复用(跳过扫描)
elif 声明了 path:
        跑 shannon-whitebox start --repo <path>,产出 workspace
else:
        报错(workspace 与 path 至少给一个)
→ 收集 (repo_path, workspace.deliverables) 配对 + relations + role,喂给关联 Agent
```

三种用法:全复用(零扫描直接关联)/ 全现扫 / 混合。

## 5. multi-repo 配置 schema

```yaml
description: "Node.js gateway → Go gRPC 后端(含多后端 + 后端互调)"

repos:
  gateway:                         # 服务名 = map key = 显示 label,可任意命名
    path: /path/to/node-gateway    # 必填:Agent 要读源码
    workspace: my-gw-scan          # 可选:复用已有 workspace,跳过扫描
    role: entrypoint               # 受限枚举:entrypoint | backend;缺省 backend
    scan_config: scan-gw.yaml      # 可选:现扫时该仓的 Shannon 配置(scope/auth)
    proto_roots: [proto/]          # 可选:给 Agent 的搜索提示,非确定性输入

  order-svc:
    path: /path/to/order-service
    workspace: my-order-scan
    role: backend

  payment-svc:
    path: /path/to/payment-service # 不给 workspace → 现扫
    role: backend

  inventory-svc:
    path: /path/to/inventory-service
    role: backend

relations:                         # 图拓扑:支持多对多 + 后端互调
  - { from: gateway,       to: order-svc,     protocol: grpc }
  - { from: gateway,       to: payment-svc,   protocol: grpc }
  - { from: gateway,       to: inventory-svc, protocol: grpc }
  - { from: order-svc,     to: payment-svc,   protocol: grpc }
  - { from: order-svc,     to: inventory-svc, protocol: grpc }

correlation:
  out_workspace: my-stack-correlated
```

设计意图:

- `relations` 只声明**拓扑谁连谁**,**不要求**手工填"哪个 HTTP 端点转发到哪个 RPC method"——那正是 Agent 要推断的;
- `proto_roots` 是给 Agent 的**搜索提示**(缩小搜索面),不是确定性输入;
- 服务名是任意 label;`role` 承载语义。

配置完整性校验(编排器加载阶段):

- 每个 repo 至少有 `path` 或 `workspace`;
- 至少一个 repo `role: entrypoint`(否则"对外信任边界"无定义);
- `relations` 的 `from` / `to` 必须是 `repos` 里已声明的服务名。

## 6. cross-repo-correlation Agent 契约

| 维度 | 内容 |
|---|---|
| 输入 | N 个 repo 的 `(path, deliverables)` 配对、`relations` 图、各 repo 的 `role` |
| 工具 | `grep`(跨任意仓)、`read_file`、可选复用 `code_index` 摘要工具。**只读**,不依赖任何引擎专有能力(见 §9) |
| 职责(prompt) | ① 在 `from` 仓找 RPC client 调用点(grpc-js / connect-es / proto-loader 等),提取 service/method + 代码位置;② 在 `to` 仓定位 method 的 handler 实现,读 handler 内 sink;③ 推断信任边界(从 `role: entrypoint` 沿 relations 可达性);④ 产出候选跨服务数据流(HTTP 入口参数→method 参数→handler sink),带证据 + 置信度;⑤ 合并 N 仓漏洞 deliverables,按服务分组 + 跨服务上下文标注;⑥ **不信任后端仓单仓 pre-recon 的 exposure 判断**(单仓视角拓扑误导,见 §1)——用本 Agent 推断的 topology(经 gateway 可达性)对后端 method 的真实 exposure 重新标注;⑦〔修订 2026-06-23·B3〕**补全未声明的边**:在 from 仓 grep 出 RPC client 调用时,若目标 service 不在用户声明的 `relations` 里,作为 `declared-missing` 边单列报告(防漏报——exposure 推断依赖声明 relations 完整性,漏声明边→backend method 被误判 internal→假阴性,见 §7.2) |
| 输出 | `cross-service-topology.json` + `trust-boundaries.json` + 候选 `cross_service_flows` + `correlation-report.md`,全部带证据与置信度 |
| 性质 | 概率性推断,非确定性;产物供人工复核 |

### 6.1 per-edge 推断 + 全局合并(规模策略)

多后端时代码量大,Agent 不能一次性塞所有仓上下文。推断分两层:

1. **per-edge 推断**:对 `relations` 每条 `from→to` 边,Agent 单独聚焦这两个仓(读 from 的调用点 + to 的 handler),产出该边的调用关系 / 数据流候选。每条边独立一轮、上下文可控、可并行。
2. **全局合并**:所有边的候选汇总成拓扑图,标注信任边界,串出**多跳跨服务链**(如 `gateway → order-svc → payment-svc`)。

这是多后端能 scale 的核心,也使"单边失败不拖垮全局"成为可能(见 §8)。

### 6.2 黑盒 gateway 层关联验证(消费 topology)

本次范围含黑盒:基于关联 topology,在 gateway HTTP 层做跨服务关联验证,形成"白盒关联 → 黑盒验证"闭环。

**触发**(扩展黑盒复用源):

Shannon 黑盒现有的 deliverables 复用机制是 `--repo`(复用同 repo 白盒队列)+ `--latest` / `find_workspaces_by_url`(按 URL 找最近白盒 workspace 复用其 deliverables);注意 `--workspace` 是黑盒**自身** workspace 的 resume,**不是**复用源(`cli/main.py:35` 已核实)。本次扩展复用源:从"单仓 deliverables"→"**关联 workspace** deliverables"。〔修订 2026-06-23·B2〕**推荐新增 `--correlated-workspace <path>`**(显式指定关联 workspace,绕开 url/scan_type 匹配),**不**扩展 `find_workspaces_by_url`——后者有两个隐藏约束使其不适合:它按 `urls_match(ws_url,url)` **且** `scan_type=="whitebox"` 双重过滤(`workspace.py:178-198`),而关联 workspace 跨多 repo **无单一 web_url**、也**不是 whitebox 类型**;强行扩展要同时解决 scan_type 归属 + web_url 填什么,改动面大、语义乱。`--correlated-workspace` 直接定位路径,最简。

```
shannon-blackbox start --url <gateway-url> <复用关联workspace 的 flag>
```

黑盒已有"检测 deliverables、有则跳过 recon 直接 exploitation"机制(`workflows.py:136-150`),扩展后该机制识别关联 workspace 的 topology/boundaries 并复用。

**exploitation 消费 topology**:`exploit_executor`(黑盒 exploitation 执行器)读关联 workspace deliverables 里的 `cross-service-topology.json` + `trust-boundaries.json` 作为上下文,据此在 **gateway HTTP 层**构造触达后端 method/sink 的 payload,验证可达性与转发行为(如 gateway 的 `POST /orders` 是否真能把恶意输入转发到 `order-svc.CreateOrder`)。〔修订 2026-06-23·A3〕经核实 `agents/exploit_executor.py:33-40`:`exploit_executor` **本就有 deliverables 注入机制**——它读 `{vuln_type}_exploitation_queue.json` 原文注入 `prompt_variables["vulnerability_entries"]`,另注入 `browser_session_id`。故 topology 注入**无需新建环节**,在同一 `prompt_variables` 字典**新增两项**(`cross_service_topology` / `trust_boundaries`,读关联 workspace 对应文件)即可。原 §11"注入点待确认"风险据此**降为低**。

**能力边界**:✅ gateway HTTP 层(可达性 / 转发行为 / gateway 侧注入·ssrf·authz);❌ gRPC 进程内(后端进程内 sink 真实执行需 gRPC 客户端 + 后端可观测,留后续 §13)。

**产物**:复用黑盒现有 `{vc}_exploitation_evidence.md`,增加跨服务路径标注(验证了哪条 gateway→backend 路径)。

## 7. 产物形态(字段名为草案,实现可调)

落在 `<out_workspace>/deliverables/`,**独立关联 workspace,不回写各仓原始 workspace**(职责 ⑥ 对后端 method 的 exposure 重标注仅落在此关联 workspace,后端单仓 deliverables 原样保留——是叠加视图,非原地覆盖)。deliverables 另含合并后的 `{vc}_exploitation_queue.json`(职责 ⑤ 合并 N 仓漏洞),供 §6.2 黑盒 `has_whitebox_results` 检测复用(`workflows.py:136`)——这是黑盒闭环的必要产物,非可选。〔修订 2026-06-23·B1〕**硬约束**:合并 queue 每条 entry **必须保留** `title`/`description`/`severity`/`location` 四字段——`has_valid_whitebox_results`(`paths.py:88-111`)用 subset 检查 `REQUIRED_VULN_FIELDS.issubset(entry.keys())`,缺任一字段即判无效→黑盒退回 from-scratch recon。跨服务标注用**额外**字段(如 `service`/`cross_service_source`),不破坏检测(subset 检查允许多字段)。

### 7.1 `cross-service-topology.json`(服务调用图)

```json
{
  "services": [
    {"name": "gateway",    "role": "entrypoint", "repo": "/path/to/node-gateway"},
    {"name": "order-svc",  "role": "backend",    "repo": "/path/to/order-service"}
  ],
  "edges": [{
    "from": "gateway", "to": "order-svc", "protocol": "grpc",
    "calls": [{
      "method": "order.v1.OrderService/CreateOrder",
      "call_site": {"file": "src/grpc-client.ts", "line": 42, "snippet": "client.createOrder(req)"},
      "confidence": "high",
      "evidence": "gateway 的 POST /orders 路由 handler 内调用 OrderService.CreateOrder"
    }]
  }]
}
```

### 7.2 `trust-boundaries.json`(信任边界标注)

```json
{
  "boundaries": [{
    "service": "order-svc",
    "method": "order.v1.OrderService/CreateOrder",
    "exposure": "external",
    "reachable_from": ["gateway"],
    "reason": "经 gateway 的 POST /orders 可达 → 外部信任边界",
    "confidence": "high"
  }]
}
```

`exposure` 由"从 `role: entrypoint` 沿 relations 可达性"推断:entrypoint 自身 HTTP 路由对外;backend 的 method 默认 internal,除非存在 entrypoint→…→它的可达路径。〔修订 2026-06-23·B3〕**漏报风险**:此推断依赖用户声明的 `relations` 完整;漏声明一条 entrypoint→backend 边,该 backend method 被误判 internal(实际 external)。对策见职责⑦(Agent 主动发现并报告未声明边)。

### 7.3 `correlation-report.md`(人读报告,章节)

1. 服务拓扑概览(图);
2. 按服务分组的 N 仓漏洞(每个 sink 标注跨服务数据来源);
3. 候选跨服务攻击链(多跳);
4. **未验证 / 低置信项单列**(透明性:声明了但未证实的关系);
5. 置信度图例 + 人工复核建议。

## 8. 错误处理

| 场景 | 处理 |
|---|---|
| deliverables 缺失/不全 | 编排器报错,指明哪个 repo 缺,提示现扫或修 workspace 路径;不进入关联 |
| **版本漂移**(复用模式特有) | 〔修订 2026-06-23·A2〕经核实 `session.py:34-46` `create_workspace()`:session.json **不持久化 git commit**(只存 web_url/repo_path/created_at/scan_type/status 等;`GitManager.get_commit_hash()` 存在但无调用方写入)。故 commit 比对主路径**不可行**,统一走降级:用 workspace `created_at` vs repo 最近改动时间粗判;发现不一致即**警告不阻断**并在报告标注"复用产物,源码版本漂移,请人工确认"。可选增强(plan 阶段,非前提):在 session.json 增补 `scanned_commit` 字段以支持精确比对——成本很低,可根治降级 |
| per-edge 推断失败/低置信 | 该边标 `confidence: low` 或 `unverified`,报告单列"声明了但未证实的关系",**不丢弃** |
| 单条边 Agent 超时/中断 | 该边标 `error`,其余边继续(per-edge 独立设计的回报) |
| 缺 entrypoint | 配置加载阶段报错(§5 校验) |
| relations 引用未声明服务 | 配置加载阶段报错(§5 校验) |

## 9. 引擎兼容风险与对策

**风险**(从项目 memory:`vuln-task-agent-engine-divergence`):Shannon 的 Agent 引擎有已知分歧——某些 prompt 强制 Task Agent,但 openai 引擎的 `build_tools` 没有 Task tool。新增 Agent 可能在特定引擎下不可用。

**对策**:

- 关联 Agent 的工具集**限定为通用 `grep` / `read_file`(跨仓)**,不依赖任何引擎专有能力(Task tool 等);
- 首次实跑必须在当前 profile(**glm-anthropic**,走 anthropic 通道)下冒烟验证工具调用正常;
- 该项写进风险登记与验收清单(§12)。

## 10. 测试策略

- **编排器单测**:配置解析(role 枚举 / 默认值 / entrypoint 必填校验 / relations 引用校验)、复用 vs 现扫分支、deliverables 完整性检查、版本漂移检测。
- **关联 Agent 契约测试**:fixture(迷你 Node gateway + 迷你 Go gRPC 后端,调用关系已知)跑 Agent,断言**产物结构 schema 正确 + 关键边存在**——不断言 Agent 具体推断文本(概率性,非目标)。
- **per-edge 隔离测试**:多后端 fixture,断言单边失败不影响其他边。
- **引擎冒烟(集成)**:glm-anthropic profile 端到端跑 fixture,确认工具调用正常(覆盖 §9)。
- **避坑**:新测试独立成模块,不依赖 feat/fork-py 已知预存挂起的 suite(`test_worker_progress` / `test_cli follow` / `test_audit_injection` / integration),广跑用 `--ignore`。

## 11. 风险登记

| 风险 | 等级 | 对策 |
|---|---|---|
| Agent 引擎兼容(grep/read 在 glm-anthropic 下是否可用) | 中 | §9 工具集限定通用 + 首跑冒烟 |
| Agent 推断质量(概率性) | 中 | 产物形态固化为"候选 + 证据 + 置信度",不强阻断;低置信项透明单列 |
| 多后端规模(上下文爆炸) | 中 | §6.1 per-edge 推断 + 全局合并 |
| 复用模式版本漂移 | 低 | §8 警告 + 报告标注 |
| 本次 gRPC 后端"扫得浅" | 已知接受 | 单仓静态分析照常跑;深度入口发现留后续(§13) |
| 黑盒 exploit_executor 注入 topology | 低〔A3 修订〕 | §6.2:复用现有 `prompt_variables` 注入点(`exploit_executor.py:33-40`),新增 topology/boundaries 两项,无需新建环节 |

## 12. 验收清单

- [ ] 编排器加载 multi-repo.yaml,完成配置校验(§5);
- [ ] 三种用法(全复用 / 全现扫 / 混合)均可跑通;
- [ ] 复用时跳过扫描,正确读取 deliverables;
- [ ] 版本漂移检测 + 警告;
- [ ] cross-repo-correlation Agent 产出三个产物文件,结构符合 §7;
- [ ] per-edge 单边失败不影响其余;
- [ ] 缺 entrypoint / relations 引用错误 → 配置阶段报错;
- [ ] glm-anthropic profile 下端到端冒烟通过(覆盖 §9);
- [ ] 黑盒 `--url <gateway>` 复用关联 workspace deliverables,exploitation 消费 topology 在 gateway HTTP 层验证(§6.2);
- [ ] **单仓零回归**:现有白盒 `--repo` / 黑盒单仓扫描在本次改动后行为不变(跑现有单仓测试套件 + 冒烟无回归);
- [ ] 新测试独立模块,全套广跑用 `--ignore` 避开预存挂起 suite。

## 13. 后续阶段(本设计不实现)

- **全链路确定性数据流**:给 Shannon 补 Go proto 解析 + RPC handler 确定性入口发现 + 跨服务确定性传播,让 gateway 输入能确定性地追到 gRPC sink。〔A1 修订〕注意:schema 源(Proto→handler)**从未接入**流水线,故此项是**全新建设**而非修补现有错配。届时关联 Agent 可作为"确定性传播 + 概率补全"混合层底座。
- **gRPC 进程内验证(§6.2 能力边界外)**:加 gRPC 客户端 + proto payload 构造 + 后端可观测,真正在 gRPC 后端进程内验证 sink 执行。本次 §6.2 只到 gateway HTTP 层。
- **更细 role**:如区分"聚合层 gateway"vs"最外层 edge",当前 entrypoint/backend 两值够用。
