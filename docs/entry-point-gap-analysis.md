# 入口点识别差距分析

> 对比原始 Shannon (TypeScript, `/root/shannon`) 与重构 Shannon-py (Python) 在**入口点识别**上的能力差距。
>
> **数据来源**：代码级核验（非仅文档），参考 `whitebox-refactoring-assessment.md` v7 但以实际代码为准。
>
> **日期**：2026-06-11
>
> **更新要点**（基于 2026-06-10~11 代码变更）：
> - EP-7 裁定修复：Phase 0 简化为 code index 审查 + 自动化置信度裁定（≥0.85 CONFIRMED / <0.50 REJECTED / 否则 NEEDS_REVIEW）
> - EP-10 多源融合部分激活：fusion 步骤已插入 pipeline，但实际仅 2 源合并（deterministic + LLM）；四源 `merge_entry_points()` 已编码未接入
> - EP-5 认证标注部分解决：模型有 `authentication` 字段，LLM fusion 填充，AST 检测未填充
> - EP-3/EP-4 LLM 层部分解决：Entry Point Mapper 升级为 comprehensive scan（含 webhook/upload/schema）
>
> **Sink 点差距分析**：见 `docs/sink-gap-analysis-v2.md`

---

## 1. 检测范式

| 子维度 | 原始 Shannon (TS) | 重构 Shannon-py | 差距定性 |
|---|---|---|---|
| **检测引擎** | LLM Agent（pre-recon Entry Point Mapper 子 agent）：Grep/Glob 找路由 → Read 提取 | regex 模式匹配（`entry_points.py`，基于 tree-sitter 解析的 FuncBlock），按语言匹配装饰器/参数签名 | **重构更可靠**：确定性、可测试、可复现 |
| **置信度评分** | 无 | ✅ 0.30-0.95 硬编码 + `needs_llm_review` 标记 | **重构新增** |
| **LLM 裁定层** | pre-recon LLM 直接产出完整入口列表 | ✅ 自动化置信度裁定：≥0.85 CONFIRMED / <0.50 REJECTED / 否则 NEEDS_REVIEW；Phase 0 简化为 code index 审查（不再要求 agent 手写 entry_points.json） | **已修复**：裁定层可用，但无 LLM 深度审查（靠置信度阈值） |

## 2. 框架/路由类型覆盖

| 框架/类型 | 原始 | 重构 | 差距 |
|---|---|---|---|
| Flask `@app.route()` | ✅ LLM 识别 | ✅ regex, conf=0.95 | 持平 |
| FastAPI `@router.get/post/...()` | ✅ LLM 识别 | ✅ regex, conf=0.95 | 持平 |
| Django `@api_view` | ✅ LLM 识别 | ✅ regex, conf=0.90 | 持平 |
| Express.js `app.get/post/...()` | ✅ LLM 识别 | ✅ 两阶段扫描（FuncBlock 内 + 文件系统顶层路由）, conf=0.80-0.90 | 持平 |
| NestJS `@Get/@Post/...` | ✅ LLM 识别 | ✅ regex, conf=0.95 | 持平 |
| Go std `http.Handler` | ✅ LLM 识别 | ✅ 参数签名匹配, conf=0.95 | 持平 |
| Go Gin `*gin.Context` | ✅ LLM 识别 | ✅ 参数签名匹配, conf=0.95 | 持平 |
| Java Spring `@GetMapping/...` | ✅ LLM 识别 | ✅ regex, conf=0.95 | 持平 |
| Java `@RabbitListener` | ✅ LLM 识别 | ✅ regex, conf=0.90 | 持平 |
| PHP Symfony `#[Route]` | ✅ LLM 识别 | ✅ regex, conf=0.95 | 持平 |
| Python Celery `@shared_task` | ✅ LLM 识别 | ✅ regex, conf=0.90 | 持平 |
| **自动生成 REST (finale-rest/epilogue)** | ✅ `framework-analyzer.ts` 专门检测，推断 CRUD 端点 | ❌ 无对应实现 | **原始胜** |
| **前端路由 (Angular/React/Vue)** | ✅ `frontend-mapper.ts` 专门映射前端路由 → API 调用 | ❌ 无对应实现 | **原始胜** |
| **Webhook 端点** | ✅ prompt 覆盖 | ⚠️ 确定性层无规则；LLM fusion 层已有 webhook 解析（`entry_point_fusion.py` Pattern 2） | ~~原始胜~~→LLM 层部分恢复 |
| **文件上传端点** | ✅ prompt 覆盖 | ❌ 确定性层和 LLM fusion 层均无对应规则 | **原始胜** |

## 3. 功能完整性

| 功能 | 原始 | 重构 | 差距 |
|---|---|---|---|
| HTTP 路由检测 | ✅ LLM Grep/Read | ✅ AST 模式匹配 | **重构更可靠** |
| **API Schema 优先读取** | ✅ pre-recon 明确指令：优先读 OpenAPI/Swagger/GraphQL → 提取端点 | ⚠️ LLM 层已恢复：Entry Point Mapper 现明确要求 catalog API schema 文件（OpenAPI/Swagger *.json/*.yaml, GraphQL *.graphql/*.gql, Proto *.proto）；`file_discovery.py` 有分类但**未接入口点检测（断路）** | **LLM 层已恢复** |
| **入口认证标注 (public/auth)** | ✅ 每入口标 public/需认证 | ⚠️ `EntryPoint` 模型已有 `authentication` 字段，LLM fusion（`parse_llm_entry_points`）可从 deliverable 提取 auth 标记；但 AST 确定性检测不填充 | **部分解决** |
| **网络可达性过滤** | ✅ 系统性排除本地工具、内部 CLI | ⚠️ 部分（仅 Python `async def` 候选做了部分过滤） | **原始胜** |
| 多源融合 | ❌ 单源（LLM） | ⚠️ `entry_point_fusion.py` 有四源去重函数（GitNexus > Schema > Framework > LLM），**但 pipeline 实际仅 2 源合并**（deterministic + LLM）；`schema_eps`/`convention_eps` 从未生成 | **部分激活**：两源已运行，四源函数已编码未接入 |
| **recon 4.1 共享 handler 分组** | ✅ 路由映射 agent 专门识别「映射到相同处理函数的路由」 | ❌ recon.txt 整段删除 | **原始胜（根因）** |
| **recon 4.2 端点安全上下文** | ✅ `_endpoint-security-context.txt` 每端点的 auth 中间件、框架来源、参数完整性 | ❌ recon.txt 整段删除 | **原始胜（根因）** |
| **recon Section 7 角色架构** | ❌ 无 | ✅ 完整角色层级 + 权限格 + 角色到代码映射 | **重构新增 ✨** |
| **recon Section 8 授权候选** | ❌ 无 | ✅ 三维预排序（水平/垂直/上下文） | **重构新增 ✨** |
| **recon Section 6.4 Guards** | ❌ 无 | ✅ Guard 分类语义（Auth/Authz/ObjectOwnership/Network/Protocol） | **重构新增 ✨** |

## 4. 总差距矩阵

| # | 差距项 | 原始能力 | 重构现状 | 严重度 |
|---|---|---|---|---|
| EP-1 | 自动生成 REST 框架检测 (finale-rest/epilogue) | `framework-analyzer.ts` 专门检测 | 无对应实现 | 中 |
| EP-2 | 前端路由→API 映射 | `frontend-mapper.ts` 完整映射 | 无对应实现 | 中 |
| EP-3 | Webhook/Upload 端点检测 | prompt 覆盖 | ⚠️ Webhook: LLM 层已恢复（Entry Point Mapper 升级 + fusion 有 webhook pattern）；确定性层无规则。Upload: 两层均无规则 | ~~低~~→Webhook LLM 层已恢复 |
| EP-4 | API Schema 优先读取 | pre-recon 明确指令 | ⚠️ LLM 层已恢复（Mapper 明确要求 catalog schema 文件）；`file_discovery.py` 仍未接入入口点检测 | ~~中~~→LLM层已恢复 |
| EP-5 | 入口认证标注 (public/auth) | 每入口标 public/需认证 | ⚠️ 模型有 `authentication` 字段 + LLM fusion 填充；AST 确定性检测不填充 | ~~中~~→部分解决 |
| EP-6 | 网络可达性系统性过滤 | 系统性排除本地工具 | 部分过滤 | 低 |
| EP-7 | ~~入口裁定把关~~ | LLM 两层裁定 | ✅ **已修复**：Phase 0 简化 + 自动化置信度裁定（CONFIRMED/REJECTED/NEEDS_REVIEW） | ~~中~~→✅ 已修复 |
| EP-8 | 共享 handler 分组 (recon 4.1) | 路由映射 agent 专门识别 | 删除 | **高** |
| EP-9 | 端点安全上下文 (recon 4.2) | 完整 auth/middleware/framework 上下文 | 删除 | **高** |
| ~~EP-10~~ | ~~多源融合~~ | — | ⚠️ **部分激活**：pipeline 实际仅 2 源合并（deterministic + LLM via `run_entry_point_fusion`）；四源 `merge_entry_points()` 已编码但从未被调用（`schema_eps`/`convention_eps` 未生成） | ~~中~~→⚠️ 部分激活 |
| EP+1 | 角色架构/权限格 | 无 | Section 7 完整角色层级 | 重构新增 ✨ |
| EP+2 | 授权候选三维预排序 | 无 | Section 8 水平/垂直/上下文 | 重构新增 ✨ |
| EP+3 | Guard 分类语义 | 无 | Section 6.4 五类 guard | 重构新增 ✨ |
| EP+4 | 置信度评分 | 无 | 0.30-0.95 硬编码 | 重构新增 ✨ |
| EP+5 | LLM fusion source (4th) | 无 | `parse_llm_entry_points` 从 LLM deliverable 提取入口 | 重构新增 ✨ |
| EP+6 | Webhook pattern 解析 | 无 | LLM fusion 识别 webhook 条目并生成 `entry_type="webhook"` | 重构新增 ✨ |
| EP+7 | 确定性 + LLM 入口融合 | 无 | 两源合并（deterministic + LLM）已激活；四源 `merge_entry_points()` 已编码未接入 pipeline | 重构新增 ✨（部分激活） |

---

## 5. 关键代码路径索引

### 原始 Shannon (TS)

| 功能 | 文件 |
|---|---|
| 框架分析 (finale-rest/epilogue) | `apps/worker/src/services/framework-analyzer.ts` |
| 框架模式定义 | `apps/worker/src/services/framework-patterns.ts` |
| 前端路由映射 | `apps/worker/src/services/frontend-mapper.ts` |
| 攻击链构建 | `apps/worker/src/services/route-chain-builder.ts` |
| 漏洞类型 Schema | `apps/worker/src/ai/queue-schemas.ts` |
| Entry Point Mapper prompt | `apps/worker/prompts/pre-recon-code.txt` (Section 5) |
| recon 路由分组 (4.1) | `apps/worker/prompts/recon-static.txt` (Route Mapper Agent) |
| recon 端点安全上下文 (4.2) | `apps/worker/prompts/recon.txt` (Section 4.2) |

### 重构 Shannon-py

| 功能 | 文件 |
|---|---|
| 入口点检测 | `packages/core/src/shannon_core/code_index/entry_points.py` |
| 入口点融合（四源函数已编码 + LLM 解析；pipeline 仅用 2 源） | `packages/core/src/shannon_core/code_index/entry_point_fusion.py` |
| 入口点/裁定模型 | `packages/core/src/shannon_core/code_index/models.py` |
| 文件发现（schema 分类） | `packages/core/src/shannon_core/code_index/file_discovery.py` |
| 管线编排（含 fusion + adjudication） | `packages/core/src/shannon_core/code_index/__init__.py` |
| Temporal workflow（fusion 插入点） | `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` |
| Activity（fusion + adjudication） | `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` |
| Entry Point Mapper prompt（comprehensive scan） | `prompts/pre-recon-code.txt` (Phase 1 Agent 2) |

---

## 6. 交叉参考

- `docs/whitebox-refactoring-assessment.md` — 全维度评估（Sink/入口/漏洞 + 调用链/传播/prompt diff）
- `docs/sink-gap-analysis-v2.md` — Sink 点差距分析（XXE/路径穿越/文件读取修正版）
- 本文档专注于**入口点识别**的逐条代码级对比
