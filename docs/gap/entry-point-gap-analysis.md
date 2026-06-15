# 入口点识别差距分析

> 对比原始 Shannon (TypeScript, `/Users/mango/project/shannon-refactor/shannon`) 与重构 Shannon-py (Python) 在**入口点识别**上的能力差距。
>
> **数据来源**：代码级核验（非仅文档），参考 `whitebox-refactoring-assessment.md` v7 但以实际代码为准。
>
> **日期**：2026-06-11（v2 复核更新：2026-06-13；v3 复核更新：2026-06-14；v4 复核更新：2026-06-15）
>
> **更新要点**（基于 2026-06-10~11 代码变更）：
> - EP-7 裁定修复：Phase 0 简化为 code index 审查 + 自动化置信度裁定（≥0.85 CONFIRMED / <0.50 REJECTED / 否则 NEEDS_REVIEW）；`run_save_adjudication` 已接入 pipeline 并注册 worker
> - EP-10 多源融合：`run_entry_point_fusion` activity 已定义并在 workflow 调用；~~worker.py 漏注册该 activity（运行时未生效）~~ → ✅ **已补注册（2026-06-13）**；pipeline 实际仅 2 源合并（deterministic + LLM）；四源 `merge_entry_points()` 已编码未接入
> - EP-5 认证标注部分解决：模型有 `authentication` 字段（`models.py:59`），LLM fusion 填充，AST 检测未填充
> - EP-3/EP-4 LLM 层已恢复：Entry Point Mapper comprehensive scan（`pre-recon-code.txt:132`）明确 catalog webhook / file uploads / schema 文件
>
> **v2 复核要点**（2026-06-13，代码级复核）：
> - **EP-8/EP-9 推翻**：recon 4.1「Shared Controller Route Groups」（`recon.txt:256`，含 group detection + per-group 表格 + pre-auth 警告 + router line 交叉引用）与 4.2「Endpoint Security Context」（`recon.txt:295` + `shared/_endpoint-security-context.txt` 91 行）**均存在且有详尽内容**，原文档"整段删除"判定错误
> - **EP-3 修正**：Upload 在 LLM 层已覆盖（Mapper `:132` 明确 "file uploads"），不再是"两层均无"
> - **EP-10 修正**：~~发现 `run_entry_point_fusion` 未在 `worker.py` 注册~~ → ✅ **已补注册**（worker.py import + activities 列表，2026-06-13 同步修复）；现两源入口融合运行时生效，仅余"四源 `merge_entry_points()` 未接入"
> - **路径统一**：原始 TS 项目实际位于 `/Users/mango/project/shannon-refactor/shannon`
>
> **v3 复核要点**（2026-06-14，代码级复核）：
> - **EP-1 推翻**：确定性 `framework_analyzer.py`（274 行，注释明确 "ported from `framework-patterns.ts`"）已实现 finale-rest + epilogue 检测 + CRUD 端点推断（`/api/{Model}s` 与 `/api/{Model}s/:id` 模板 + vulnerability_patterns）；`run_framework_analysis` activity（`activities.py:463`）在 workflow 与前端映射并行调用（`workflows.py:163`）且 worker 已注册。原"无对应实现"判定错误
> - **EP-2 推翻**：确定性 `frontend_mapper.py`（228 行）已实现 Angular/React/Vue 框架检测（读 `package.json`）+ per-framework 路由文件发现 + 路由解析 + XSS 链识别；`run_frontend_mapping` activity（`activities.py:491`）+ workflow 并行调用（`workflows.py:167`）+ worker 注册齐全；另有 `route_chain_builder.py`（110 行）/`attack_chain_builder.py`（39 行）补全路由→API 链构建。原"无对应实现"判定错误
> - **EP-7/EP-10 再核验**：`worker.py` activity 列表（`worker.py:77-87`）已含 `run_merge_sink_reports`/`run_entry_point_fusion`/`run_save_adjudication`/`run_framework_analysis`/`run_frontend_mapping`/`run_route_chain_building`，5 个 entry-point 相关 activity 注册齐全
> - **file_discovery 断路再确认**：`discover_security_files`（`__init__.py:82,247`）结果仅写入 `CodeIndex.file_manifest`，从未喂给入口点检测；四源 `merge_entry_points()` 仍无**生产**调用点（仅定义于 `entry_point_fusion.py:20`，仅被单元测试直接调用；详见 v4 复核要点）
>
> **v4 复核要点**（2026-06-15，代码级复核）：
> - **无实质代码变更**：自 v3（2026-06-14）以来，entry-point 相关文件（`code_index/`、`services/framework_analyzer.py`/`frontend_mapper.py`、`pipeline/activities.py`/`workflows.py`、`worker.py`、`prompts/recon.txt`、`prompts/pre-recon-code.txt`、`shared/_endpoint-security-context.txt`）**零提交**；2026-06-15 的近期提交集中于 audit/logging（`workflow_logger`、`SessionToolAuditLogger`、shutdown），与入口点识别无关。v3 全部技术结论持续有效，无需推翻或下调任何差距项。
> - **`merge_entry_points()` 表述精确化**：该四源函数在生产代码中**仅定义、无调用**（`entry_point_fusion.py:20` 定义；`__init__.py` 的 `run_entry_point_fusion` 走 2 源合并、未调四源函数），仅被 `tests/code_index/test_entry_point_fusion.py` 直接调用。原 v3「grep 全仓零命中」表述过强，已修正为「生产代码无调用点」。
> - **新增第 5 节「已知覆盖盲区与维度补遗」**：扩展分析维度，识别出非 HTTP 入口（gRPC/WebSocket/Kafka/SQS/cron/GraphQL resolver/Lambda）属**原始 TS 与重构共同盲区**（确定性层与 LLM prompt 均未显式覆盖，非重构差距）、动态路由为确定性方法的结构性局限；并补录测试覆盖（6 个测试文件齐全）作为「可测试」论点的正面佐证，及三项「定性待深化」项。
>
> **Sink 点差距分析**：见 `docs/gap/sink-gap-analysis-v2.md`

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
| **自动生成 REST (finale-rest/epilogue)** | ✅ `framework-analyzer.ts` 专门检测，推断 CRUD 端点 | ✅ `framework_analyzer.py`（274 行，移植自 `framework-patterns.ts`）：finale-rest + epilogue 检测 + CRUD 端点推断（`/api/{Model}s`、`/api/{Model}s/:id` 模板 + vulnerability_patterns）；`run_framework_analysis` activity 已接入 workflow（`workflows.py:163`） | ~~原始胜~~ → ✅ **持平（v3 推翻）** |
| **前端路由 (Angular/React/Vue)** | ✅ `frontend-mapper.ts` 专门映射前端路由 → API 调用 | ✅ `frontend_mapper.py`（228 行）：Angular/React/Vue 框架检测 + 路由解析 + XSS 链识别；`run_frontend_mapping` activity 已接入 workflow（`workflows.py:167`）；`route_chain_builder.py` 构建路由→API 链 | ~~原始胜~~ → ✅ **持平（v3 推翻）** |
| **Webhook 端点** | ✅ prompt 覆盖 | ⚠️ 确定性层无规则；LLM 层已覆盖（Entry Point Mapper `:132` catalog "webhooks" + fusion `Pattern 2` 解析 webhook 条目） | ~~原始胜~~ → LLM 层已恢复 |
| **文件上传端点** | ✅ prompt 覆盖 | ⚠️ 确定性层无规则；LLM 层已覆盖（Entry Point Mapper `:132` catalog "file uploads"） | ~~原始胜~~ → LLM 层已恢复 |

## 3. 功能完整性

| 功能 | 原始 | 重构 | 差距 |
|---|---|---|---|
| HTTP 路由检测 | ✅ LLM Grep/Read | ✅ AST 模式匹配 | **重构更可靠** |
| **API Schema 优先读取** | ✅ pre-recon 明确指令：优先读 OpenAPI/Swagger/GraphQL → 提取端点 | ⚠️ LLM 层已恢复：Entry Point Mapper 现明确要求 catalog API schema 文件（OpenAPI/Swagger *.json/*.yaml, GraphQL *.graphql/*.gql, Proto *.proto）；`file_discovery.py` 有分类但**未接入口点检测（断路）** | **LLM 层已恢复** |
| **入口认证标注 (public/auth)** | ✅ 每入口标 public/需认证 | ⚠️ `EntryPoint` 模型已有 `authentication` 字段，LLM fusion（`parse_llm_entry_points`）可从 deliverable 提取 auth 标记；但 AST 确定性检测不填充 | **部分解决** |
| **网络可达性过滤** | ✅ 系统性排除本地工具、内部 CLI | ⚠️ 部分（仅 Python `async def` 候选做了部分过滤） | **原始胜** |
| 多源融合 | ❌ 单源（LLM） | ⚠️ `entry_point_fusion.py` 有四源去重函数（GitNexus > Schema > Framework > LLM），**但 pipeline 实际仅 2 源合并**（deterministic + LLM）；`schema_eps`/`convention_eps` 从未生成 | **部分激活**：两源已运行，四源函数已编码未接入 |
| **recon 4.1 共享 handler 分组** | ✅ 路由映射 agent 专门识别「映射到相同处理函数的路由」 | ✅ `recon.txt:256`「Shared Controller Route Groups」+ Route Mapper Agent group detection（`:162`）：per-group 表格、pre-auth 警告、router line 交叉引用 | ~~原始胜（根因）~~ → ✅ **已恢复（v2 推翻）** |
| **recon 4.2 端点安全上下文** | ✅ `_endpoint-security-context.txt` 每端点的 auth 中间件、框架来源、参数完整性 | ✅ `recon.txt:295`「Endpoint Security Context」+ `@include(shared/_endpoint-security-context.txt)`（`:46`，91 行） | ~~原始胜（根因）~~ → ✅ **已恢复（v2 推翻）** |
| **recon Section 7 角色架构** | ❌ 无 | ✅ 完整角色层级 + 权限格 + 角色到代码映射 | **重构新增 ✨** |
| **recon Section 8 授权候选** | ❌ 无 | ✅ 三维预排序（水平/垂直/上下文） | **重构新增 ✨** |
| **recon Section 6.4 Guards** | ❌ 无 | ✅ Guard 分类语义（Auth/Authz/ObjectOwnership/Network/Protocol） | **重构新增 ✨** |

## 4. 总差距矩阵

| # | 差距项 | 原始能力 | 重构现状 | 严重度 |
|---|---|---|---|---|
| EP-1 | ~~自动生成 REST 框架检测 (finale-rest/epilogue)~~ | `framework-analyzer.ts` 专门检测 | ✅ **已恢复**：`framework_analyzer.py`（274 行，移植自 `framework-patterns.ts`）检测 finale-rest + epilogue + 推断 CRUD 端点；`run_framework_analysis` activity（`activities.py:463`）+ workflow 调用（`workflows.py:163`）+ worker 注册（`worker.py:85`）齐全 | ~~中~~ → ✅ 已恢复（v3 推翻） |
| EP-2 | ~~前端路由→API 映射~~ | `frontend-mapper.ts` 完整映射 | ✅ **已恢复**：`frontend_mapper.py`（228 行）Angular/React/Vue 检测 + 路由解析 + XSS 链；`run_frontend_mapping` activity（`activities.py:491`）+ workflow 调用（`workflows.py:167`）+ worker 注册（`worker.py:86`）；`route_chain_builder.py`（110 行）补全链构建 | ~~中~~ → ✅ 已恢复（v3 推翻） |
| EP-3 | Webhook/Upload 端点检测 | prompt 覆盖 | ⚠️ Webhook: LLM 层已恢复（Entry Point Mapper `:132` + fusion Pattern 2）；Upload: **LLM 层已恢复**（Mapper `:132` "file uploads"）；确定性层两类均无规则 | ~~低~~ → LLM 层 webhook+upload 均已恢复（v2 修正 upload 判定） |
| EP-4 | API Schema 优先读取 | pre-recon 明确指令 | ⚠️ LLM 层已恢复（Mapper 明确要求 catalog schema 文件）；`file_discovery.py` 仍未接入入口点检测 | ~~中~~→LLM层已恢复 |
| EP-5 | 入口认证标注 (public/auth) | 每入口标 public/需认证 | ⚠️ 模型有 `authentication` 字段 + LLM fusion 填充；AST 确定性检测不填充 | ~~中~~→部分解决 |
| EP-6 | 网络可达性系统性过滤 | 系统性排除本地工具 | 部分过滤 | 低 |
| EP-7 | ~~入口裁定把关~~ | LLM 两层裁定 | ✅ **已修复**：Phase 0 简化 + 自动化置信度裁定（CONFIRMED/REJECTED/NEEDS_REVIEW） | ~~中~~→✅ 已修复 |
| EP-8 | ~~共享 handler 分组 (recon 4.1)~~ | 路由映射 agent 专门识别 | ✅ **已恢复**：`recon.txt:256`「Shared Controller Route Groups」+ Route Mapper Agent group detection（`:162`） | ~~高~~ → ✅ 已恢复（v2 推翻） |
| EP-9 | ~~端点安全上下文 (recon 4.2)~~ | 完整 auth/middleware/framework 上下文 | ✅ **已恢复**：`recon.txt:295` + `shared/_endpoint-security-context.txt`（91 行，@include 于 `:46`） | ~~高~~ → ✅ 已恢复（v2 推翻） |
| EP-10 | 多源融合 | — | ✅ **worker 注册已补**（2026-06-13）：`run_entry_point_fusion` activity（`activities.py:247`）+ workflow 调用（`workflows.py:146`）+ worker 注册齐全，两源融合运行时生效；仅余四源 `merge_entry_points()` 已编码但从未在生产管线中被调用（`schema_eps`/`convention_eps` 未生成；详见 v4 复核要点） | ~~中~~ → **低（仅四源未接入）** |
| EP+1 | 角色架构/权限格 | 无 | Section 7 完整角色层级 | 重构新增 ✨ |
| EP+2 | 授权候选三维预排序 | 无 | Section 8 水平/垂直/上下文 | 重构新增 ✨ |
| EP+3 | Guard 分类语义 | 无 | Section 6.4 五类 guard | 重构新增 ✨ |
| EP+4 | 置信度评分 | 无 | 0.30-0.95 硬编码 | 重构新增 ✨ |
| EP+5 | LLM fusion source (4th) | 无 | `parse_llm_entry_points` 从 LLM deliverable 提取入口 | 重构新增 ✨ |
| EP+6 | Webhook pattern 解析 | 无 | LLM fusion 识别 webhook 条目并生成 `entry_type="webhook"` | 重构新增 ✨ |
| EP+7 | 确定性 + LLM 入口融合 | 无 | ✅ 两源合并（deterministic + LLM）已编码 + 调用 + worker 注册齐全，运行时生效（2026-06-13 修复漏注册）；四源 `merge_entry_points()` 已编码未接入 | ~~重构新增 ✨（部分激活）~~ → **重构新增 ✨（两源已生效，四源未接入）** |

---

## 5. 已知覆盖盲区与维度补遗

> v4 复核扩展出的维度。多数为**原始 TS 与重构的共同盲区**（不构成「重构差距」，故不纳入第 4 节矩阵），另含此前未单独核验的定性项。

### 5.1 非 HTTP 入口的共同盲区

`EntryPoint.entry_type` 注释（`models.py:53`）声明 `"http_route" | "rpc" | "cli" | "message_consumer" | ...`，但确定性层**实际仅产出两类**：

| 层 | 已覆盖 | 缺口 |
|---|---|---|
| 确定性层（`entry_points.py`） | `http_route`（4 条 Python 规则）+ `message_consumer`（Celery `@shared_task` / Java `@RabbitListener`） | `rpc`/`cli` 注释有、检测器无 |
| LLM 层（`pre-recon-code.txt:132`） | API endpoints / web routes / webhooks / file uploads / schema 文件 | 未显式列 gRPC/WS/Kafka/cron/resolver |

**两边均未覆盖**（核对：原始 TS `apps/worker/prompts/` + `framework-patterns.ts` 亦零命中，属共同盲区而非重构差距）：

- **gRPC service** 方法（proto `service` + impl）
- **WebSocket / SSE handler**（`@app.websocket` / `socket.on`）
- **Kafka / SQS consumer**（`@KafkaListener` / `confluent_kafka.Consumer`）
- **Lambda / FaaS handler**（`def handler(event, context)`）
- **定时任务**（Spring `@Scheduled` / APScheduler / cron 驱动）
- **GraphQL resolver**（`@Query`/`@Mutation`/`@Resolver`——schema 文件覆盖定义，但 resolver 函数本身未作入口）

**定性**：共同盲区；`entry_type` 注释的 `rpc`/`cli` 暴露了未实现的检测意图。明确划界以避免「入口点 = Web 路由」的窄化误读。

### 5.2 动态 / 运行时入口点（确定性方法的结构性局限）

AST/regex 对**静态注册**路由可靠，但天然看不到：配置/数据驱动的路由表（从 DB、YAML 加载）、反射/动态注册（`add_url_rule` 循环、装饰器工厂批量生成）、运行时由中间件/plugin 注入的端点。原始 TS 的 LLM agent 方式有概率通过 Read 推断部分动态路由，重构确定性层**结构上无法发现**——这是「重构更可靠」叙事的反面，此前未定性。

### 5.3 测试覆盖（补遗：正面佐证）

文档以「可测试、可复现」为重构优势，此前未核验测试存在性。实际**齐全**：

| 测试文件 | 覆盖 |
|---|---|
| `tests/code_index/test_entry_points.py` | 各框架 regex 检测 |
| `tests/code_index/test_entry_point_fusion.py` | 四源 `merge_entry_points` + 2 源 pipeline 合并 |
| `tests/test_framework_analyzer.py` | finale-rest/epilogue CRUD 推断 |
| `tests/test_frontend_mapper.py` | Angular/React/Vue 路由解析 |
| `tests/test_route_chain_builder.py` | 路由→API 链构建 |
| `tests/code_index/test_file_discovery.py` | schema 文件分类 |

「可测试」由声明转为证据；亦解释了 `merge_entry_points()` 为何仅被单元测试调用（见 v4 复核要点）。

### 5.4 定性待深化项

| 项 | 当前定性 | 待深化 |
|---|---|---|
| 2 源融合去重 | EP-10「四源未接入」 | 已生效的 `func_block_id` 去重对 LLM 发现的**无函数锚点**入口（如 webhook）如何合并？路径归一化（trailing slash / 参数命名）是否处理？ |
| EP-5 认证粒度 | 「部分解决 / 中」 | 原始给 auth 中间件链 + 参数完整性 + 框架来源；重构仅 `public/required/unknown` 三态字符串，粒度差距或 >「中」，且直接影响 pre-auth 路由优先级 |
| 入口→Sink 下游契约 | 仅交叉引用 | `entry_points.json` / 裁定产物如何被调用链/sink 阶段消费的契约未交代 |

---

## 6. 关键代码路径索引

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
| 入口点融合（四源 `merge_entry_points()` 已编码未接入；pipeline 2 源合并已注册生效） | `packages/core/src/shannon_core/code_index/entry_point_fusion.py` |
| 入口点/裁定模型 | `packages/core/src/shannon_core/code_index/models.py` |
| 文件发现（schema 分类） | `packages/core/src/shannon_core/code_index/file_discovery.py` |
| 管线编排（含 fusion + adjudication） | `packages/core/src/shannon_core/code_index/__init__.py` |
| Temporal workflow（fusion 插入点） | `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` |
| Activity（fusion + adjudication + framework/frontend） | `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` |
| 自动 REST 框架检测（finale-rest/epilogue） | `packages/core/src/shannon_core/services/framework_analyzer.py` |
| 前端路由→API 映射（Angular/React/Vue） | `packages/core/src/shannon_core/services/frontend_mapper.py` |
| 路由→API 攻击链构建 | `packages/core/src/shannon_core/services/route_chain_builder.py` + `attack_chain_builder.py` |
| Worker activity 注册清单 | `packages/whitebox/src/shannon_whitebox/worker.py` |
| Entry Point Mapper prompt（comprehensive scan） | `prompts/pre-recon-code.txt` (Phase 1 Agent 2) |

---

## 7. 交叉参考

- `docs/whitebox-refactoring-assessment.md` — 全维度评估（Sink/入口/漏洞 + 调用链/传播/prompt diff）
- `docs/gap/sink-gap-analysis-v2.md` — Sink 点差距分析（XXE/路径穿越/文件读取修正版）
- `docs/gap/route-analysis-binding-gap-analysis.md` — 路由分析服务与接口绑定差距
- 本文档专注于**入口点识别**的逐条代码级对比
