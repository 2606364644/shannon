# Shannon 路由纠正优化方案

## 背景

Shannon 项目当前的"路由纠正"机制是弱保障：依赖 recon 阶段浏览器观测 `{{WEB_URL}}` 的真实流量作为路由真值，再由 exploit agent 在自然语言层自行拼接 `WEB_URL + 路径`。代码层面没有路径前缀探测、网关识别、OpenAPI 自动发现等程序化逻辑，若部署存在网关转发导致路径前缀差异（如代码 `/api/users` 对外暴露为 `/app/api/users`），项目本身不做自动重写。

本文档在不改动主流程的前提下，提出一组可叠加的优化方案，按"实现成本 / 收益"排序。

---

## 现状回顾

| 环节 | 当前行为 | 缺陷 |
|---|---|---|
| `-u` URL 注入 | 经 `{{WEB_URL}}` 注入所有 prompt | 仅做 DNS + HEAD 可达性校验，不解析路径前缀 |
| 白盒 recon | 先浏览器观测流量，再回溯代码 handler | 仅覆盖浏览器爬到的端点；代码里写了但没被爬到的路由会被 `<scope_boundaries>` 过滤掉 |
| vuln queue | 输出 `source_endpoint` 为纯路径 | 不含 host，也不含 deployment_path |
| exploit agent | LLM 自行把 `WEB_URL + path` 拼成完整 URL | 无程序化校验；网关有 prefix 时易拼错 |
| renderer | 纯透传 | 无 URL 规范化钩子 |

全代码库检索 `baseUrl|rewrite|pathPrefix|gateway` 等关键词，无一与目标 URL 路由纠正相关。

---

## 优化方案

### 方案 1：preflight 加「路由探针」前置阶段 ⭐ 最高性价比

**位置**：[apps/worker/src/services/preflight.ts](file:///workspace/apps/worker/src/services/preflight.ts) 的 `validateTargetUrl` 之后

**动作**：新增 `probeRoutingBaseline`，输出 `RoutingProfile`：

- HEAD/GET 一组高出现率路径：`/`、`/api`、`/api/v1`、`/swagger.json`、`/openapi.json`、`/v3/api-docs`、`/health`、`/actuator`
- 对每个路径记录：响应码、响应头里的 `Server` / `Via` / `X-Gateway`（识别 nginx/kong/traefik/ingress）、最终重定向 Location
- 路径差异反推 prefix：若 `/api/users` 返回 404 但 `/app/api/users` 返回 401，prefix 极可能是 `/app`

**注入路径**：通过新的 `{{ROUTING_PROFILE}}` 占位符注入所有 prompt（参考 [prompt-manager.ts](file:///workspace/apps/worker/src/services/prompt-manager.ts) 现有 `{{DESCRIPTION}}` 注入路径，约第 296 行）。

**收益**：一次探测，全流水线复用；探测失败自动回退到现有浏览器观测机制。

---

### 方案 2：OpenAPI / Swagger 自动发现 ⭐ 几乎零成本

**位置**：方案 1 的探针扩展

**动作**：试以下路径，命中即解析：

- `/openapi.json`、`/swagger.json`、`/v3/api-docs`、`/swagger/v1/swagger.json`、`/api-docs`

**解析字段**：

- `paths` → 作为 `RoutingProfile.authoritative_paths`，这是网关转发后的真实路径，比浏览器爬还全
- `servers` → 往往就是带 prefix 的 base URL，直接给出 deployment root

**收益**：最权威的"真实路径"来源；项目目前完全没用。

---

### 方案 3：recon 加「代码路径 ↔ 部署路径」对齐矩阵

**位置**：[apps/worker/prompts/recon.txt](file:///workspace/apps/worker/prompts/recon.txt) 的 Route Mapper Agent

**当前行为**：浏览器端点 → 代码 handler 单向回溯

**优化**：加一个反向通道：

1. pre-recon-code 阶段已能拿到代码所有路由声明（[pre-recon-collector.ts](file:///workspace/apps/worker/src/mcp-server/pre-recon-collector.ts)）
2. recon 阶段把这份清单批量 HEAD/GET 探测，对每条记录 `reachable: true/false` + `resolved_path`
3. 在 [recon-collector.ts](file:///workspace/apps/worker/src/mcp-server/recon-collector.ts) 的 `EndpointSchema` 加 `deployment_path` 字段，与 `path` 并列

**对齐算法**：

- 先试原路径
- 404 则试常见前缀组合（`/api`、`/app`、`/v1`、`/api/v1`）
- 用编辑距离 / 子串包含匹配浏览器观测到的路径
- 命中即记录映射

---

### 方案 4：跨模块批量可达性探测

**位置**：recon 阶段末尾

**当前问题**：`<scope_boundaries>` 的 Network-Reachable Only 过滤太"被动"——只信浏览器爬到的

**优化**：

- 对所有代码发现的端点做一轮轻量探测（HEAD 优先，405/401 都算"可达"，仅 404/connection refused 算"未暴露"）
- 探测结果分两组写进交付物：
  - `verified_reachable`（带 deployment_path）
  - `code_only_unexposed`
- exploit agent 拿到前者直接打；后者可作为 SSRF / 内部端点类漏洞的线索（不能直接打但可能在 SSRF 链里被利用）

---

### 方案 5：网关配置文件主动获取

**位置**：[pre-recon-collector.ts](file:///workspace/apps/worker/src/mcp-server/pre-recon-collector.ts) 第 250 行附近

**当前行为**：仅从代码仓库读 `nginx.conf`、`gateway-ingress.yaml`

**优化**：若 `-u` 环境同机或可达，试：

- `/nginx.conf`、`/.env`、`/config/nginx.conf`（很多 misconfig 会暴露）
- Docker labels / k8s ingress（如果 worker 有访问权限）

**注意**：必须放在 `rules_of_engagement` 允许范围内。

---

### 方案 6：path_mapping 作为「战略情报」显式传递

**位置**：[vuln-collector.ts](file:///workspace/apps/worker/src/mcp-server/vuln-collector.ts) 的 `set_strategic_intelligence`

**当前字段**：`request_architecture`（描述 proxy/middleware patterns）

**优化**：加结构化字段：

```json
{
  "path_mapping": [
    {
      "code_path": "/api/users",
      "deployment_path": "/app/api/users",
      "verified": true
    },
    {
      "code_path": "/internal/admin",
      "deployment_path": null,
      "verified": false,
      "reason": "404 via gateway"
    }
  ]
}
```

**配套约束**：[exploit-collector.ts](file:///workspace/apps/worker/src/mcp-server/exploit-collector.ts) 的 `exploitation_steps` 校验加一条："URL 必须使用 path_mapping 里的 deployment_path，不得自行拼接 code_path"。

**收益**：exploit agent 拼 URL 时有确定性依据，不再靠 LLM 推理。

---

### 方案 7：增量探测 + 缓存

**位置**：`.shannon/deliverables/`

**动作**：

- 把 `RoutingProfile` + `path_mapping` 存到 `.shannon/deliverables/routing-profile.json`
- recon 阶段生成一次，exploit 阶段只读
- 若 `-u` 变化或显式 `--reprobe` 才重新跑

**收益**：避免每次 agent 调用都重新探测。

---

## 落地优先级

| 优先级 | 改动 | 改动量 | 收益 |
|---|---|---|---|
| P0 | preflight 加 OpenAPI/Swagger 发现 + 常见路径探针 | ~100 行 | 立即解决 80% 的 prefix 问题 |
| P0 | `{{ROUTING_PROFILE}}` 注入所有 prompt | ~20 行 | exploit agent 不再盲拼 |
| P1 | recon 加 code_path ↔ deployment_path 对齐矩阵 | ~150 行 | 覆盖浏览器没爬到的端点 |
| P1 | path_mapping 作为 strategic_intelligence 显式传递 | schema 扩展 | 消除 LLM 拼接的不确定性 |
| P2 | 跨模块批量可达性探测 | ~80 行 | 扩大攻击面发现 + SSRF 线索 |
| P2 | 网关配置文件主动获取 | ~50 行 | 直接拿到权威路由规则 |
| P3 | 增量探测 + 缓存 | ~60 行 | 性能优化，避免重复探测 |

---

## 最小可行版本（MVP）

只做 P0 两项即可把当前的"弱保障"升级为"强保障"，且完全向后兼容：

1. **OpenAPI 发现 + 路由探针**：在 preflight 阶段探测 OpenAPI/Swagger 端点 + 常见路径，生成 `RoutingProfile`
2. **prompt 注入**：通过 `{{ROUTING_PROFILE}}` 把探测结果注入所有 prompt

探测失败时自动回退到现有的浏览器观测机制，不破坏现有流程。

---

## 关键文件索引

| 文件 | 作用 |
|---|---|
| [apps/cli/src/index.ts](file:///workspace/apps/cli/src/index.ts) | `-u` URL 解析 |
| [apps/worker/src/services/preflight.ts](file:///workspace/apps/worker/src/services/preflight.ts) | URL 校验（扩展点） |
| [apps/worker/src/services/prompt-manager.ts](file:///workspace/apps/worker/src/services/prompt-manager.ts) | 变量注入（新增 `{{ROUTING_PROFILE}}`） |
| [apps/worker/prompts/shared/_target.txt](file:///workspace/apps/worker/prompts/shared/_target.txt) | 共享 target 片段 |
| [apps/worker/prompts/recon.txt](file:///workspace/apps/worker/prompts/recon.txt) | recon 流程（扩展对齐矩阵） |
| [apps/worker/src/mcp-server/recon-collector.ts](file:///workspace/apps/worker/src/mcp-server/recon-collector.ts) | 端点 schema（加 `deployment_path`） |
| [apps/worker/src/mcp-server/pre-recon-collector.ts](file:///workspace/apps/worker/src/mcp-server/pre-recon-collector.ts) | 代码路由清单来源 |
| [apps/worker/src/mcp-server/vuln-collector.ts](file:///workspace/apps/worker/src/mcp-server/vuln-collector.ts) | strategic_intelligence（加 `path_mapping`） |
| [apps/worker/src/mcp-server/exploit-collector.ts](file:///workspace/apps/worker/src/mcp-server/exploit-collector.ts) | exploitation_steps 校验（约束使用 deployment_path） |
