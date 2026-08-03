# 黑盒端点 live 验证 agent + 白盒去动态 recon — 设计 spec

> 日期：2026-08-03 · 分支：`feat/fork-py` · 状态：设计待审
>
> 自包含：另一个会话读本文件 + 代码即可理解与实现，无需其他上下文。

## 一、背景与问题

当前白盒 recon：有 `web_url` 时跑动态（`recon.txt`，correlating live behavior with source code），无 `web_url` 时回退静态（`recon-static.txt`，纯源码）。黑盒 exploitation-only，吃白盒 queue，**不做独立 recon**（commit a1b917c1 删 `recon-blackbox` 回归 TS）。

**痛点场景**：操作者跑白盒时常**不填 url**（纯代码审计，只要仓库）→ 白盒跑 `recon-static`，端点全从源码推断、**无 live 验证** → 之后黑盒复用，exploit agent 拿未经验证的静态情报打 live target：

1. 源码有但运行时未暴露的端点（网关挡 / 配置没开 / feature flag 关）→ 盲打浪费预算。
2. **路由转发前缀**：源码 `/api/users`，经 gateway / ingress 实际 `/v2/app/api/users` → 打源码路径得 404 → **漏报**。

这是 TS/PY 共有的设计盲区（TS 黑盒也不补动态 recon，其 exploitation-only 隐含假设"白盒带 url 跑"，没覆盖"白盒离线 → 黑盒"场景）。本设计在 PY 补此盲区——不是对齐 TS，是补一个 TS 也没做好的真实场景。

## 二、架构方向（已拍板）

- **白盒去动态**：白盒永远跑 `recon-static`（纯静态）。`web_url` 降为可选元数据，不再驱动动态 recon。**白盒只要仓库就开扫**。
- **黑盒补动态验证**：黑盒 exploitation 前插入"端点 live 验证" agent（LLM 驱动），复用 preflight 已建立的 auth-state，对白盒端点做 live 验证 + 路由转发前缀智能探测，产 `live_status` + `resolved_path`。
- **只验证不发现**：只验证白盒已给端点，不主动 spider / fuzz 新端点（克制、可控、不触及 scope 外）。

## 三、关键决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 验证形态 | **LLM agent**（非确定性 HTTP 探测） | 路由转发前缀需智能探测；确定性探测遇 404 即判 not_live 丢弃 → 漏报 |
| 验证范围 | **只验证白盒端点**，不 spider 新端点 | 克制、低成本、可控，不触及 scope 外 |
| 登录态 | **复用黑盒 preflight 的 auth-state** | 不重新登录，带 cookie 探测已认证端点 |
| 产出落点 | `deliverables/blackbox/endpoint_verify.json`，**不改写白盒 queue** | 守黑白盒产物隔离原则 |
| `resolved_path` | 经验证的实际可达路径 | 默认=白盒源码路径；仅路由转发/变形时**谨慎改**；not_live 时无 |
| `not_live` vs 功能性失败 | **严格区分** | not_live=验证正常结果 → exploit 跳过；功能性失败(agent 崩/超时/无产出) → 降级 exp 全打 |

## 四、Architecture / 数据流

```
白盒(纯静态,只要仓库) → 静态 recon_deliverable.md + {vt}_exploitation_queue.json
        │  黑盒复用(reuse_whitebox_scan_id)
        ▼
黑盒 preflight(url 可达性 + 登录) → auth-state.json
        ▼
【端点 live 验证 agent（新）】
  读白盒 queue 端点清单 + 白盒 recon 路由信息(锚点) + auth-state
  对每端点发已认证请求验证 live + 参数；遇 not-found 智能试路由转发前缀
        ▼ 产出(落 blackbox/)
deliverables/blackbox/endpoint_verify.json
        ▼
exploitation: 读白盒 queue 候选 + endpoint_verify.json
  not_live → 跳过；live → 用 resolved_path 打；无记录(降级) → 照打
```

## 五、Design

### 5.1 白盒侧去动态（影响面极小）
- `packages/core/src/supernova_core/agents/executor.py`：`_resolve_template`（约 :41-42）删 `if agent_name == AgentName.RECON and not web_url: return "recon-static"` 分支，**无条件返回 `"recon-static"`**。
- `prompts/recon.txt`：**删除**（白盒不再用；动态侦察职责移交黑盒验证 agent）。
- `web_url` 字段：保留（黑盒复用按 url 匹配 workspace 仍需；CLI `--url` 保持 optional）。
- 影响面已核实：白盒 `web_url` 无任何功能必需用途（仅元数据记录 / CLI 提示 / 黑盒匹配），`externally_exploitable` 标签是白盒静态分析自产（`vuln_chain_builders` 里 `externally_exploitable=(verdict=="vulnerable")`），不依赖 `web_url`。

### 5.2 黑盒端点验证 agent（核心）
- **workflow 位置**：`BlackboxScanWorkflow.run` 在 `detect_whitebox_results`（确认有白盒产物）之后、exploitation 循环之前插入验证阶段。
- **新 activity**：`run_endpoint_verify`（`worker.py` 注册）。
- **prompt**：新写 `prompts/blackbox-endpoint-verify.txt`，要点：
  - 复用 `{{AUTH_STATE_FILE}}`（preflight 登录态），探测带 cookie。
  - 读白盒 `{vt}_exploitation_queue.json` 端点清单 + `recon_deliverable.md` 路由信息作为锚点。
  - 核心能力：对每端点发已认证请求判 live（业务响应 vs 404/超时/连接失败）；**遇 not-found 智能试路由转发前缀**（从 base url 路径段、已成功端点反推实际前缀，试 `/api` `/v1` `/v2` `/app` 等常见前缀，找到实际可达路径）。
  - 产出每端点 `live_status` + `resolved_path`，调 collector 落 `endpoint_verify.json`。
  - 守 scope：只探测白盒已识别端点 + 其合理路由前缀变体，不 spider 外部。
- **工具**：curl/bash（发已认证 HTTP 请求看响应）；复杂交互场景可选浏览器（agent-browser / playwright）。
- **双引擎**：claude / openai 都跑（复用现有 `AgentExecutor`，prompt 不分引擎）。

### 5.3 产出 schema
落 `deliverables/blackbox/endpoint_verify.json`：
```json
{
  "<endpoint_key>": {
    "live_status": "live | not_live | param_invalid",
    "resolved_path": "/actual/path",
    "source_path": "/source/code/path",
    "evidence": "探测依据：响应特征 / 前缀尝试记录"
  }
}
```
- `endpoint_key` = 归一化 `METHOD /path`（**白盒源码路径**），关联白盒 queue 候选。
- `resolved_path`：live 时必填；not_live 时无。
- `source_path`：白盒给的原始路径，审计对照用。

### 5.4 resolved_path 语义（详解）
- **验证直接命中源码路径**（live）→ `resolved_path = source_path`（不改）。
- **发现路由转发 / 路径变形**（源码路径 404 但加前缀后 live）→ `resolved_path = 实际可达路径`（**谨慎改**：agent 须有响应证据支撑，不臆测）。
- **验不出来** → `not_live`（无 resolved_path）。
- exploit 统一用 `resolved_path` 打（它就是"经验证能打到的路径"）。

### 5.5 衔接 exploit
exploit agent 读白盒 queue 候选时，查 `endpoint_verify.json`（按 `endpoint_key`）：
- `not_live` → **跳过**（验证生效，省预算）。
- `live` → 用 `resolved_path`（替代源码路径）打。
- `param_invalid`（端点 live 但白盒给的参数无效）→ **仍打**（端点存在，exploit agent 自行调整参数）。
- **无验证记录**（功能性失败降级）→ 照打（= 现状，零回归）。

### 5.6 错误处理（严格区分两类失败）
- **`not_live`（验证正常结果）**：agent 正常跑完、判定端点确实不在线 → exploit 跳过。**这不是降级，是验证的价值。**
- **功能性失败**（agent 崩溃 / 超时 / LLM 不可用 / 无 `endpoint_verify.json` 产出）→ **降级：exploit 全打**（等同没做验证，零回归）。
- **auth-state 缺失**：agent 裸跑探测（无登录态），与现状 exploit 一致。

## 六、Files Changed

| 文件 | 改动 | 类型 |
|---|---|---|
| `packages/core/src/supernova_core/agents/executor.py` | 删 RECON 动态分支，永远 `recon-static` | Modify |
| `prompts/recon.txt` | 删除（白盒不再用） | Delete |
| `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py` | 插入端点验证阶段（`detect_whitebox_results` 后） | Modify |
| `packages/blackbox/src/supernova_blackbox/pipeline/activities.py` | 新 `run_endpoint_verify` activity | Modify |
| `packages/blackbox/src/supernova_blackbox/worker.py` | 注册 `run_endpoint_verify` | Modify |
| `prompts/blackbox-endpoint-verify.txt` | 新验证 agent prompt | New |
| exploit prompt / `exploit_executor` | 读 `endpoint_verify.json`：`not_live` 跳过 + `resolved_path` 打 | Modify |
| collector / renderer | `endpoint_verify.json` 落盘渲染 | Modify / New |

## 七、Testing
1. **白盒去动态**：无 `web_url` 与有 `web_url` 都跑 `recon-static`（executor 单测）。
2. **验证 agent**：
   - 直接命中 → `live` + `resolved_path` = 源码路径。
   - 路由转发（源码 404、加前缀后 live）→ `live` + `resolved_path` = 实际路径。
   - 不在线 → `not_live`。
3. **衔接**：`not_live` 被 exploit 跳过；`live` 用 `resolved_path` 打。
4. **降级**：验证功能性失败（无 `endpoint_verify.json`）→ exploit 全打（零回归）。
5. **产物隔离**：`endpoint_verify.json` 落 `blackbox/`，白盒 queue 不被改写。

## 八、不变量 / 边界
- **双轨独立性 / 黑白盒产物隔离**：不破坏。验证结果落 `blackbox/`，不改白盒产物。
- **exploitation-only**：验证 agent 只验证白盒端点，不独立发现漏洞/端点（不 spider）。不违反"黑盒不独立发现"。
- **降级零回归**：功能性失败 = 现状行为。
- **scope 合规**：只探测白盒端点 + 合理前缀变体，不外扩。
