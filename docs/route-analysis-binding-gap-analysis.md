# 路由分析服务与接口绑定差距分析

> 对比原始 Shannon（TypeScript, `/root/shannon`）与重构 Shannon-py（Python）在**路由级分析服务**（framework-analyzer / frontend-mapper / route-chain-builder / attack-chain-builder）及其 **Pipeline 接口绑定**上的能力差距。
>
> **数据来源**：逐行代码核验（非仅文档），以代码为准。
>
> **日期**：2026-06-11
>
> **与已有文档的关系**：
> - `docs/entry-point-gap-analysis.md` — 入口点**检测**差距（AST regex vs LLM）
> - `docs/sink-gap-analysis-v2.md` — Sink 点**检测**差距（确定性规则 vs LLM prompt）
> - **本文档** — 路由**分析**服务（框架推断 / 前端映射 / 攻击链构建）+ **Pipeline 接口绑定**差距

---

## 0. 服务架构总览

### 0.1 原始 Shannon（TypeScript）服务栈

```
framework-patterns.ts (81 行, 纯数据)
  ↓ 模式定义
framework-analyzer.ts (251 行) ──→ SharedKnowledge.frameworkAnalysis
  ↓                                    ↓
frontend-mapper.ts (241 行) ──→ SharedKnowledge.frontendRoutes
  ↓                                    ↓
route-chain-builder.ts (140 行) ←── 直接调用（框架端点 + 前端路由 + XSS 链）
  ↓
attack-chain-builder.ts (57 行) ←── SharedKnowledge（含漏洞上下文）
  ↓                                    ↓
                                    confidence 升级（probable → confirmed）
```

**数据通信机制**：`SharedKnowledge` JSON 文件（`shared-knowledge.json`），所有服务通过 `knowledge-store.ts` 读写同一个共享知识库。

### 0.2 重构 Shannon-py（Python）服务栈

```
framework_analyzer.py (275 行, 模式内嵌) ──→ framework_analysis.json
  ↓                                            ↓
frontend_mapper.py (229 行) ──→ frontend_mapping.json
  ↓                                            ↓
route_chain_builder.py (111 行) ←── 读 JSON 文件重建对象
  ↓
attack_chain_builder.py (40 行) ←── 仅读 FrameworkAnalysisResult + FrontendAnalysisResult
  ↓                                    ↓
                                    ❌ 无漏洞上下文增强
```

**数据通信机制**：独立 JSON 文件 + Activity 内手动反序列化（tuple 丢失后重建）。

### 0.3 核心差异

| 维度 | 原始 (TS) | 重构 (Python) | 差距定性 |
|---|---|---|---|
| **服务数量** | 4（含 `attack-chain-builder`） | 4（对应移植） | ✅ 持平 |
| **模式定义** | 独立文件 `framework-patterns.ts` | 内嵌 `framework_analyzer.py` | ⚠️ 可维护性降低 |
| **数据通信** | `SharedKnowledge` 集中式 | 独立 JSON 文件 | ⚠️ 见 §4 |
| **漏洞上下文** | `attack-chain-builder` 读取漏洞发现并升级置信度 | ❌ 完全缺失 | ❌ **关键差距** |
| **总代码量** | 770 行（4 文件 + 1 模式文件） | 655 行（4 文件） | ✅ 持平 |

---

## 1. Framework Analyzer 对比

### 1.1 数据模型

| 模型 | 原始 (TS interface) | 重构 (Python dataclass) | 差异 |
|---|---|---|---|
| `EndpointTemplate` | `readonly string[]` | `tuple[str, ...]` | ✅ 等价（frozen dataclass） |
| `FrameworkPattern` | 独立文件 `framework-patterns.ts` | 内嵌 `framework_analyzer.py:69-121` | ⚠️ 合并后不可独立引用 |
| `InferredEndpoint` | `readonly` 字段 | `frozen=True` dataclass | ✅ 等价 |
| `FrameworkAnalysisResult` | `readonly` 字段 | mutable dataclass | ⚠️ 重构版可变 |

### 1.2 框架模式覆盖

| 框架 | 原始 | 重构 | 差异 |
|---|---|---|---|
| **finale-rest** | ✅ 2 端点模板 + 3 漏洞模式 | ✅ 完全一致（逐字段核对） | ✅ 持平 |
| **epilogue** | ✅ 1 端点模板 + 2 漏洞模式 | ✅ 完全一致 | ✅ 持平 |

**检测模式对照**（以 finale-rest 为例）：

| 检测类型 | 原始 (TS) | 重构 (Python) |
|---|---|---|
| import | `require("express-finale")`, `require("finale-rest")`, `import.*finale.*from` | 完全一致 |
| initialize | `finale.initialize(`, `finale.resource(` | 完全一致 |
| config | `finale.resource(` | 完全一致 |

### 1.3 检测逻辑差异

| 函数 | 原始 | 重构 | 差异 |
|---|---|---|---|
| **检测匹配** | `content.includes(pattern)` — 精确子串 | `re.search(pattern, content)` — 正则匹配 | ⚠️ 重构版更强（`import.*finale.*from` 在原始也是字面量但在重构被当作正则） |
| **文件发现** | `fs.pathExists()` + `fs.readdir()` — 仅一级目录 | `Path.exists()` + `dir_path.rglob("*.js/*.ts")` — **递归扫描** | ✅ **重构更彻底** |
| **模型发现** | `.resource()` 正则提取 model 名 + endpoint 路径 | 仅 `.resource()` model 名提取 | ❌ **缺失 endpoint 路径发现** |

**原始 `discoverModels()`（`:156-185`）额外发现的内容**：

```typescript
// 原始额外提取 endpoint 路径
const endpointRegex = /\.resource\([^)]*?endpoints\s*:\s*\[([^\]]+)\]/g;
// 匹配如: finale.resource({ endpoints: ['/api/users', '/api/posts'] })
```

**重构版 `_discover_models()`（`:198-220`）**：仅提取 `model` 名，无 endpoint 路径发现。

**影响**：当框架配置用 `endpoints` 数组而非 `model` 名称时，重构版无法发现端点。

### 1.4 端点推断逻辑

| 逻辑 | 原始 | 重构 | 差异 |
|---|---|---|---|
| 路径模板替换 | `template.replace('{Model}', model).replace('{resource}', model.toLowerCase())` | 完全一致 | ✅ 持平 |
| 集合端点过滤 | `isCollection && method in ('PUT', 'DELETE') → skip` | 完全一致 | ✅ 持平 |
| 安全建议 | DELETE/PUT 统计 + 框架漏洞模式 | 完全一致 | ✅ 持平 |

### §1 裁决

框架分析服务**基本完整移植**。唯一功能差距是**缺失 endpoint 路径发现**（`:170-185` 的 `endpointRegex`）。重构版文件发现更彻底（`rglob` 递归），但模型发现更窄。

---

## 2. Frontend Mapper 对比

### 2.1 数据模型

| 模型 | 原始 (TS) | 重构 (Python) | 差异 |
|---|---|---|---|
| `UserInputPoint` | `type` + `field` + `sanitization?` | 完全一致 | ✅ |
| `ApiCall` | `endpoint` + `method` + `purpose` + `dataFlow[]` | 完全一致 | ✅ |
| `FrontendRoute` | `path` + `component` + `authenticated` + `apiCalls` + `userInputs` | 完全一致 | ✅ |
| `XssAttackChain` | `entryPoint` + `storageEndpoint` + `renderEndpoint` + `sink` + `confidence` | 完全一致 | ✅ |
| `FrontendAnalysisResult` | `routes` + `xssChains` | 完全一致 | ✅ |

### 2.2 前端框架检测

| 框架 | 原始检测条件 | 重构检测条件 | 差异 |
|---|---|---|---|
| Angular | `content.includes('@angular/core')` | `'"@angular/core"' in content` | ⚠️ 重构版用引号限定更精确 |
| React | `content.includes('react')` | `'"react"' in content` | ⚠️ **重构版更严格**（避免误匹配 "reactive" 等） |
| Next.js | `content.includes('next')` | `'"next"' in content` | ⚠️ 同上 |
| Vue | `content.includes('vue')` | `'"vue"' in content` | ⚠️ 同上 |
| Nuxt | `content.includes('nuxt')` | `'"nuxt"' in content` | ⚠️ 同上 |

**评估**：重构版的引号限定（`'"react"'`）实际上**更准确**——避免 `"reactive"` / `"next-hop"` 等误匹配。但有一个边界情况：如果 `package.json` 中 key 不带引号（如 `{react: "..."}`——虽然不合规范），重构版会漏检。实际 JSON 规范要求引号，因此**重构版更好**。

### 2.3 路由文件发现

| 维度 | 原始 | 重构 | 差异 |
|---|---|---|---|
| 搜索目录 | 5 个（完全一致） | 5 个（完全一致） | ✅ |
| 文件名模式 | Angular: 3 / React: 5 / Vue: 4 / Unknown: 5 | 完全一致 | ✅ |
| 文件系统 | `fs.pathExists()` 逐文件 | `Path.exists()` 逐文件 | ✅ 等价 |

### 2.4 路由解析正则

| 框架 | 原始 (TS RegExp) | 重构 (Python re.Pattern) | 差异 |
|---|---|---|---|
| Angular | `/path\s*:\s*['"\`]([^'"\`]+)['"\`][^}]*?component\s*:\s*([A-Za-z_]\w*)/g` | 完全一致 | ✅ |
| React | `/path\s*:\s*['"\`]([^'"\`]+)['"\`][^}]*?(?:element\|component)\s*:\s*(?:<\|([A-Za-z_]\w*))/g` | 完全一致 | ✅ |
| Vue | `/path\s*:\s*['"\`]([^'"\`]+)['"\`][^}]*?(?:component\|name)\s*:\s*['"\`]?([A-Za-z_]\w*)/g` | 完全一致 | ✅ |

### 2.5 认证守卫检测

| 守卫模式 | 原始 | 重构 | 差异 |
|---|---|---|---|
| `AuthGuard` | ✅ `content.includes()` | ✅ 提取为 `_has_auth_guard()` 函数 | ✅ 重构更清晰 |
| `canActivate` | ✅ | ✅ | ✅ |
| `requireAuth` | ✅ | ✅ | ✅ |

### 2.6 XSS 链检测

**两版算法完全一致**：

1. 找含 POST API 调用或 user_inputs 的路由（`input_routes`）
2. 找含 GET API 调用的路由（`render_routes`）
3. 对每对 POST/GET 路由比较 `extract_base_path()`
4. 匹配则生成 `XssAttackChain(confidence="medium")`

**`extract_base_path()` 逻辑完全一致**：`parts.filter(p => !p.startsWith(':')).join('/')`。

### 2.7 API 调用 / 用户输入追踪

| 功能 | 原始 | 重构 | 差异 |
|---|---|---|---|
| API 调用提取 | ❌ 模型有 `apiCalls` 字段但 `parseRoutes()` 总设为 `[]` | ❌ 同原始 | ✅ 两版均为空壳 |
| 用户输入提取 | ❌ 模型有 `userInputs` 字段但总设为 `[]` | ❌ 同原始 | ✅ 两版均为空壳 |

**实际影响**：`identifyXssChains()` 的 `input_routes` 过滤条件为 `r.userInputs.length > 0 || r.apiCalls.some(a => a.method === 'POST')`。由于两版的 `apiCalls` 和 `userInputs` 始终为空，**XSS 链检测在两版中均不会产生结果**。这是一个**原始设计中的未完成功能**，重构版忠实移植了这一局限。

### §2 裁决

前端映射服务**逐行忠实移植**。框架检测引号限定更严格。XSS 链检测两版均为空壳（API 调用/用户输入未实现提取）。无功能性差距。

---

## 3. Route Chain Builder 对比

### 3.1 数据模型

| 模型 | 原始 (TS) | 重构 (Python) | 差异 |
|---|---|---|---|
| `AttackChainStep` | `order` + `phase` + `endpoint` + `method` + `description` | 完全一致 | ✅ |
| `AttackChain` | `id` + `name` + `description` + `steps` + `vulnType` + `severity` + `confidence` | 完全一致 | ✅ |
| 类型约束 | `phase: 'input' \| 'storage' \| 'retrieval' \| 'render'`（TS 字面量联合） | `phase: str`（无枚举约束） | ⚠️ 重构版宽松 |
| 类型约束 | `vulnType: 'xss' \| 'authz' \| 'injection'` | `vuln_type: str = ""` | ⚠️ 重构版宽松 + 默认空串 |

### 3.2 XSS 攻击链构建

**两版逻辑完全一致**：

| 步骤 | 原始 | 重构 | 差异 |
|---|---|---|---|
| chain id | `xss-chain-${i}` | `xss-chain-{i}` | ✅ |
| 步骤数 | 4 步（input → storage → retrieval → render） | 完全一致 | ✅ |
| severity | `"high"` | `"high"` | ✅ |
| confidence 映射 | `xssChain.confidence === 'high' ? 'probable' : 'theoretical'` | `xss_chain.confidence == "high" and "probable" or "theoretical"` | ✅ 等价 |

### 3.3 IDOR 攻击链构建

**两版逻辑完全一致**：

| 步骤 | 原始 | 重构 | 差异 |
|---|---|---|---|
| 筛选条件 | `ep.path.includes(':id') && ep.vulnerabilityIndicators.length > 0` | `":id" in ep.path and ep.vulnerability_indicators` | ✅ |
| 前端路由关联 | ✅ `frontendRoutes.find(r => r.apiCalls.some(...))` | ❌ **缺失** | ❌ **差距** |
| chain id | `idor-chain-${i}` | `idor-chain-{i}` | ✅ |
| severity | `endpoint.method === 'DELETE' ? 'high' : 'medium'` | `endpoint.method == "DELETE" and "high" or "medium"` | ✅ |
| confidence | `"probable"` | `"probable"` | ✅ |

**IDOR 前端路由关联差距详述**：

原始（`:98-101`）：
```typescript
const relatedRoute = frontendRoutes.find((r) =>
  r.apiCalls.some((a) => endpoint.path.startsWith(extractPathPrefix(a.endpoint))),
);
// relatedRoute 用于丰富 description："Triggered from frontend route {relatedRoute.path}."
```

重构（`:88-106`）：
```python
# 直接跳过前端路由关联
chains.append(AttackChain(
    ...
    description=(f"{endpoint.method} {endpoint.path} is auto-generated by "
                 f"{endpoint.source} with no ownership validation."),
    ...
))
```

**影响**：IDOR 链描述缺失"从前端路由触发"的上下文信息。但由于前端路由的 `apiCalls` 始终为空（§2.7），原始的 `relatedRoute` 在实际运行中也**总是 `undefined`**。因此**实际运行结果两版一致**，但原始代码展示了正确的关联意图。

### §3 裁决

路由链构建器**基本完整移植**。IDOR 前端路由关联代码缺失，但因上游 API 调用提取未实现，两版实际行为一致。类型约束从 TS 字面量联合退化为 Python `str`。

---

## 4. Attack Chain Builder 对比（关键差距）

### 4.1 接口签名

**原始** (`attack-chain-builder.ts`):
```typescript
export async function buildAttackChains(
  knowledge: SharedKnowledge,
  logger: ActivityLogger,
): Promise<AttackChain[]>
```

**重构** (`attack_chain_builder.py`):
```python
async def build_attack_chains(
    framework_result: FrameworkAnalysisResult,
    frontend_result: FrontendAnalysisResult,
    logger: logging.Logger,
) -> list[AttackChain]:
```

### 4.2 核心差距：漏洞上下文增强

**原始**（`attack-chain-builder.ts:36-52`）：
```typescript
// 2. Enhance chains with vulnerability context if available
const vulnKnowledge = knowledge.vulnerabilityContext;
if (vulnKnowledge) {
  for (const chain of analysisChains) {
    for (const step of chain.steps) {
      const vulnEntries = vulnKnowledge.endpointVulnerabilities[step.endpoint];
      if (vulnEntries && vulnEntries.length > 0) {
        const confirmed = vulnEntries.some((v) => v.confirmed);
        if (confirmed && chain.confidence !== 'confirmed') {
          (chain as { confidence: string }).confidence = 'confirmed';
        }
      }
    }
  }
}
```

**重构**：❌ **完全缺失**。`attack_chain_builder.py` 仅调用 `build_attack_chains_from_analysis()` 后直接返回。

**影响**：
1. 攻击链置信度**永远无法升级为 `confirmed`**——即使下游漏洞 agent 已确认对应端点的漏洞。
2. 原始设计中，`attack-chain-builder` 在所有漏洞分析 agent 完成后运行，能读取已确认的漏洞发现来增强攻击链。重构版在漏洞分析之前就构建链，且无增强步骤。

### 4.3 数据源差距

| 数据源 | 原始 (`SharedKnowledge`) | 重构（直接参数） | 差异 |
|---|---|---|---|
| 框架端点 | `knowledge.frameworkAnalysis?.inferredEndpoints` | `framework_result.inferred_endpoints` | ✅ 等价 |
| 前端路由 | `knowledge.frontendRoutes?.routes` | `frontend_result.routes` | ✅ 等价 |
| XSS 链 | `knowledge.frontendRoutes?.xssVectors` | `frontend_result.xss_chains` | ✅ 等价 |
| **漏洞上下文** | `knowledge.vulnerabilityContext.endpointVulnerabilities` | ❌ 无对应参数 | ❌ **完全缺失** |

### §4 裁决

`attack_chain_builder` 的核心差距不在代码移植质量（基础逻辑完全一致），而在**数据流架构**：原始使用 `SharedKnowledge` 集中式知识库，重构使用独立参数传递。这导致漏洞上下文增强能力**完全缺失**。

---

## 5. Pipeline 接口绑定对比

### 5.1 原始 Shannon（TypeScript）绑定模式

**架构**：Temporal Activities → Container DI → Services → SharedKnowledge

```
Container (workflow-scoped)
  ├── ConfigLoaderService (构造函数注入)
  ├── AgentExecutionService ← ConfigLoader
  ├── ExploitationCheckerService
  ├── FindingsProvider (NoOp 默认)
  ├── CheckpointProvider (NoOp 默认)
  └── ReportOutputProvider (NoOp 默认)

Activities:
  runPreReconAgent()
    └── 内嵌调用 analyzeFrameworks(repoPath, logger)
    └── updateSharedKnowledge({ frameworkAnalysis: result })

  runReconAgent()
    └── 内嵌调用 mapFrontendRoutes(repoPath, logger)
    └── updateSharedKnowledge({ frontendRoutes: result })

  buildAttackChainsActivity()
    └── sharedKnowledge = loadSharedKnowledge()
    └── chains = buildAttackChains(sharedKnowledge, logger)  ← 含漏洞上下文
    └── updateSharedKnowledge({ attackChains: chains })
```

**关键特征**：
1. **服务嵌入 Agent**：`analyzeFrameworks` 和 `mapFrontendRoutes` 在 agent activity **内部**调用，不是独立 activity
2. **SharedKnowledge 集中式**：所有服务写入同一个 `shared-knowledge.json`
3. **构造函数 DI**：Container 显式注入依赖，NoOp 默认值
4. **漏洞上下文可用**：`attack-chain-builder` 在漏洞分析之后运行，可读取 `vulnerabilityContext`

### 5.2 重构 Shannon-py（Python）绑定模式

**架构**：Temporal Activities → 直接 import → Services → 独立 JSON 文件

```
Worker (显式 activity 列表注册)
  ├── run_framework_analysis  ← import from framework_analyzer
  ├── run_frontend_mapping    ← import from frontend_mapper
  ├── run_route_chain_building ← import from route_chain_builder
  └── run_attack_chain_assembly ← import from attack_chain_builder

Activities:
  run_framework_analysis()
    └── analyze_frameworks(repo)
    └── 写 framework_analysis.json

  run_frontend_mapping()
    └── map_frontend_routes(repo)
    └── 写 frontend_mapping.json

  run_route_chain_building()
    └── 读 framework_analysis.json → 反序列化
    └── 读 frontend_mapping.json → 反序列化
    └── build_attack_chains_from_analysis(endpoints, routes, xss_chains, logger)
    └── 写 route_chains.json

  run_attack_chain_assembly()
    └── 读 framework_analysis.json → 反序列化（重复）
    └── 读 frontend_mapping.json → 反序列化（重复）
    └── build_attack_chains(framework_result, frontend_result, logger)
    └── 写 attack_chains.json
```

**关键特征**：
1. **服务独立 Activity**：每个服务是独立 Temporal activity（更细粒度）
2. **文件通信**：独立 JSON 文件 + Activity 内手动反序列化
3. **无 DI 容器**：直接 import，无构造函数注入
4. **漏洞上下文不可用**：`attack_chain_assembly` 无漏洞分析结果输入

### 5.3 Pipeline 编排时序对比

**原始**（嵌入在 agent 内）：
```
PRE_RECON agent
  └── analyzeFrameworks()          ← 内嵌
RECON agent
  └── mapFrontendRoutes()          ← 内嵌
VULN agents × N                    ← 产出漏洞发现
buildAttackChainsActivity()        ← 读取完整 SharedKnowledge（含漏洞上下文）
EXPLOIT agents × N
```

**重构**（独立 activity）：
```
PRE_RECON agent  ─┐
Code Index        ─┤ asyncio.gather（并行）
                  ─┤
Sink Merge           ← 合并确定性 + LLM sink
Entry Fusion         ← 4 源入口融合
Adjudication         ← confidence 裁定
                  ─┤
Framework Analysis ─┐ asyncio.gather（并行）
Frontend Mapping   ─┘
Route Chain Build     ← 读前两个 JSON 文件
                  ─┤
RECON agent
Risk Scoring
Dataflow Hints
VULN agents × N
Attack Chain Assembly ← 读 JSON 文件（无漏洞上下文）
Render Findings
```

**关键时序差异**：

| 维度 | 原始 | 重构 | 影响 |
|---|---|---|---|
| 框架分析位置 | 嵌入 PRE_RECON agent | 独立 activity，与 PRE_RECON 并行 | ⚠️ 重构版在 PRE_RECON 前就运行，LLM 结果无法影响框架分析 |
| 前端映射位置 | 嵌入 RECON agent | 独立 activity，在 PRE_RECON 之后、RECON 之前 | ⚠️ 重构版在 RECON 前运行，无法利用 RECON 结果 |
| 攻击链构建位置 | **VULN 之后**（可读漏洞上下文） | **VULN 之前**（route_chain）+ **VULN 之后**（attack_chain） | ❌ `route_chain_building` 在 VULN 前运行，无漏洞上下文 |
| 攻击链增强 | ✅ 基于已确认漏洞升级 confidence | ❌ 无增强步骤 | ❌ **关键差距** |

### 5.4 数据传递机制对比

| 维度 | 原始 | 重构 | 评估 |
|---|---|---|---|
| **通信载体** | `SharedKnowledge` JSON（单文件） | 独立 JSON 文件（`framework_analysis.json` / `frontend_mapping.json` / `route_chains.json` / `attack_chains.json`） | 重构更碎片化，但文件间无耦合 |
| **读写 API** | `loadSharedKnowledge()` / `updateSharedKnowledge()` — 原子合并 | 各 Activity 内手动读 `json.loads()` + 写 `atomic_write_json()` | 原始更 DRY；重构有**重复反序列化**（`route_chain_building` 和 `attack_chain_assembly` 各读一次） |
| **tuple/list 丢失** | TypeScript `readonly` → JSON `Array` → 反序列化无损失 | Python `tuple` → JSON `list` → 需手动 `tuple()` 转换 | ⚠️ 重构版需手动重建（见 `activities.py:516-521`） |
| **dataclass dict 化** | TS interface → `JSON.stringify` → 结构保留 | Python dataclass → `dataclasses.asdict()` → 写 JSON | ✅ 等价 |
| **错误恢复** | Activity 失败后 SharedKnowledge 保留已写入的部分 | Activity 失败后 JSON 文件可能不存在或不完整 | ⚠️ 重构版需每个 Activity 处理文件缺失（`if path.exists()` 守卫） |

### 5.5 重复代码分析

重构版 `activities.py` 中存在明显的代码重复：

| 重复代码块 | 位置 1 | 位置 2 | 行数 |
|---|---|---|---|
| `_to_endpoint()` 反序列化 | `:516-521` | `:578-583` | 6 行 × 2 |
| `_to_route()` 反序列化 | `:533-535` | `:585-588` | 3 行 × 2 |
| `_to_xss()` 反序列化 | `:537-541` | `:590-594` | 5 行 × 2 |
| 加载 `framework_analysis.json` | `:511-526` | `:597-604` | 16 行 × 2 |
| 加载 `frontend_mapping.json` | `:529-544` | `:606-612` | 16 行 × 2 |

**根因**：`run_route_chain_building` 和 `run_attack_chain_assembly` 各自独立读取并反序列化相同的 JSON 文件。

### §5 裁决

Pipeline 绑定的核心差距是**数据流架构差异**：
1. 原始用 `SharedKnowledge` 集中式知识库，支持跨 phase 数据积累（特别是漏洞上下文）
2. 重构用独立 JSON 文件，数据碎片化，**漏洞上下文无法传递到攻击链构建**
3. 编排时序差异导致 `attack_chain_assembly` 缺失置信度升级能力
4. 重复的反序列化代码（约 40 行 × 2）可通过提取共享工具函数消除

---

## 6. 差距矩阵

| # | 差距项 | 原始能力 | 重构现状 | 严重度 | 类型 |
|---|---|---|---|---|---|
| RA-1 | **漏洞上下文增强** | `attack-chain-builder` 读 SharedKnowledge 中已确认漏洞，升级攻击链 confidence 为 `confirmed` | ❌ 完全缺失 | **高** | 功能差距 |
| RA-2 | **endpoint 路径发现** | `discoverModels()` 提取 `.resource({ endpoints: [...] })` 中的显式路径 | ❌ 仅提取 model 名 | 中 | 功能差距 |
| RA-3 | **SharedKnowledge 集中式数据流** | 所有服务读写同一个知识库，跨 phase 数据积累 | 独立 JSON 文件，无跨 phase 共享 | **中-高** | 架构差距 |
| RA-4 | **API 调用 / 用户输入提取** | 模型有字段但未实现（两版均为空壳） | 同原始 | 低 | 两版均未完成 |
| RA-5 | **IDOR 前端路由关联** | 代码存在但 `apiCalls` 为空导致实际无效 | 代码缺失 | 低 | 代码完整性 |
| RA-6 | **重复反序列化代码** | N/A（用 SharedKnowledge API） | `run_route_chain_building` 和 `run_attack_chain_assembly` 各自重复约 40 行 | 低 | 代码质量 |
| RA-7 | **类型约束退化** | TS 字面量联合类型（`'xss' \| 'authz' \| 'injection'`） | Python `str` 无枚举约束 | 低 | 类型安全 |
| RA-8 | **框架模式独立文件** | `framework-patterns.ts` 独立可引用 | 内嵌 `framework_analyzer.py` | 低 | 可维护性 |
| RA+1 | **文件发现递归扫描** | `readdir()` 仅一级 | `rglob("*.js/*.ts")` 递归 | — | 重构增强 ✨ |
| RA+2 | **框架检测引号限定** | `content.includes('react')` 可能误匹配 | `'"react"' in content` 精确匹配 | — | 重构增强 ✨ |
| RA+3 | **服务独立 Activity** | 嵌入 agent 内 | 独立 Temporal activity，可独立重试 | — | 重构增强 ✨ |
| RA+4 | **并行执行** | 框架/前端分析嵌入 agent，串行 | `asyncio.gather` 并行运行 | — | 重构增强 ✨ |

---

## 7. 关键代码路径索引

### 7.1 原始 Shannon (TypeScript)

| 功能 | 文件 |
|---|---|
| 框架模式定义 | `apps/worker/src/services/framework-patterns.ts` |
| 框架分析服务 | `apps/worker/src/services/framework-analyzer.ts` |
| 前端路由映射 | `apps/worker/src/services/frontend-mapper.ts` |
| 路由链构建 | `apps/worker/src/services/route-chain-builder.ts` |
| 攻击链构建（含漏洞增强） | `apps/worker/src/services/attack-chain-builder.ts` |
| 共享知识库类型 | `apps/worker/src/types/shared-knowledge.ts` |
| 知识库读写 | `apps/worker/src/audit/knowledge-store.ts` |
| DI 容器 | `apps/worker/src/services/container.ts` |
| Temporal activities | `apps/worker/src/temporal/activities.ts` |
| Temporal workflow | `apps/worker/src/temporal/workflows.ts` |

### 7.2 重构 Shannon-py (Python)

| 功能 | 文件 |
|---|---|
| 框架分析（含模式内嵌） | `packages/core/src/shannon_core/services/framework_analyzer.py` |
| 前端路由映射 | `packages/core/src/shannon_core/services/frontend_mapper.py` |
| 路由链构建 | `packages/core/src/shannon_core/services/route_chain_builder.py` |
| 攻击链构建（无漏洞增强） | `packages/core/src/shannon_core/services/attack_chain_builder.py` |
| 服务导出 | `packages/core/src/shannon_core/services/__init__.py` |
| Temporal activities（含路由分析） | `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` |
| Temporal workflow（编排） | `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` |
| Worker 注册 | `packages/whitebox/src/shannon_whitebox/worker.py` |

---

## 8. 修复优先级建议

按"性价比 = 影响面 × 修复难度倒数"：

1. **RA-1 漏洞上下文增强**（高影响、中难度）：在 `attack_chain_builder.py` 中添加漏洞上下文参数，`run_attack_chain_assembly` activity 从 deliverables 读取漏洞发现结果，执行 confidence 升级逻辑。需定义 `VulnerabilityContext` 模型或复用现有漏洞 deliverable 格式。

2. **RA-3 SharedKnowledge 等价机制**（高影响、高难度）：为重构版引入轻量级共享知识层，或至少确保 `attack_chain_assembly` 能读取漏洞 agent 的 deliverables。可作为 RA-1 的基础设施。

3. **RA-6 提取共享反序列化工具**（低影响、低难度）：将 `_to_endpoint()` / `_to_route()` / `_to_xss()` 提取到 `services/route_io.py`，消除约 40 行重复代码。

4. **RA-2 endpoint 路径发现**（中影响、低难度）：在 `_discover_models()` 中添加 `endpointRegex`，与原始对齐。

5. **RA-4 API 调用提取**（中影响、中难度）：实现前端路由中的 API 调用追踪，使 XSS 链检测真正产生结果。这是两版共同的未完成功能。

---

## 9. 交叉参考

- `docs/whitebox-refactoring-assessment.md` — 全维度评估（v8），§2.4 提及 recon 4.1/4.2 路由级索引差距
- `docs/entry-point-gap-analysis.md` — 入口点**检测**差距（AST regex vs LLM）
- `docs/sink-gap-analysis-v2.md` — Sink 点**检测**差距（确定性规则 vs LLM prompt）
- 本文档专注于**路由分析服务**（框架推断 / 前端映射 / 攻击链构建）和 **Pipeline 接口绑定**差距
