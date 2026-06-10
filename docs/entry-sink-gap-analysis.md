# 入口点 & Sink 点识别差距分析

> 对比原始 Shannon (TypeScript, `/root/shannon`) 与重构 Shannon-py (Python) 在**入口点识别**和 **Sink 点识别**两个维度上的能力差距。
>
> **数据来源**：代码级核验（非仅文档），参考 `whitebox-refactoring-assessment.md` v7 但以实际代码为准。
>
> **日期**：2026-06-10

---

## 1. 入口点识别

### 1.1 检测范式

| 子维度 | 原始 Shannon (TS) | 重构 Shannon-py | 差距定性 |
|---|---|---|---|
| **检测引擎** | LLM Agent（pre-recon Entry Point Mapper 子 agent）：Grep/Glob 找路由 → Read 提取 | AST 模式匹配（`entry_points.py`），按语言遍历 AST call node，匹配装饰器/参数签名 | **重构更可靠**：确定性、可测试、可复现 |
| **置信度评分** | 无 | ✅ 0.30-0.95 硬编码 + `needs_llm_review` 标记 | **重构新增** |
| **LLM 裁定层** | pre-recon LLM 直接产出完整入口列表 | `save_adjudication` 无条件全置 `CONFIRMED`（橡皮图章）；Phase 0 指令依赖不存在的 `save-deliverable` 工具 | **原始胜**：重构裁定层不可用 |

### 1.2 框架/路由类型覆盖

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
| **Webhook 端点** | ✅ prompt 覆盖 | ❌ 无对应规则 | **原始胜** |
| **文件上传端点** | ✅ prompt 覆盖 | ❌ 无对应规则 | **原始胜** |

### 1.3 入口点功能完整性

| 功能 | 原始 | 重构 | 差距 |
|---|---|---|---|
| HTTP 路由检测 | ✅ LLM Grep/Read | ✅ AST 模式匹配 | **重构更可靠** |
| **API Schema 优先读取** | ✅ pre-recon 明确指令：优先读 OpenAPI/Swagger/GraphQL → 提取端点 | ⚠️ `file_discovery.py` 有 `.graphql/.gql/.proto/.thrift` 分类，但**未接入入口点检测**；LLM prompt 也删了 schema 指令 | **原始胜** |
| **入口认证标注 (public/auth)** | ✅ 每入口标 public/需认证 | ❌ `EntryPoint` 模型无字段；LLM 指令也删了 | **原始胜** |
| **网络可达性过滤** | ✅ 系统性排除本地工具、内部 CLI | ⚠️ 部分（仅 Python `async def` 候选做了部分过滤） | **原始胜** |
| 多源融合 | ❌ 单源（LLM） | ✅ `entry_point_fusion.py` 三源去重（GitNexus > Schema > Framework），**但 `build_code_index` 从不调用（死代码）** | **重构设计更优但未激活** |
| **recon 4.1 共享 handler 分组** | ✅ 路由映射 agent 专门识别「映射到相同处理函数的路由」 | ❌ recon.txt 整段删除 | **原始胜（根因）** |
| **recon 4.2 端点安全上下文** | ✅ `_endpoint-security-context.txt` 每端点的 auth 中间件、框架来源、参数完整性 | ❌ recon.txt 整段删除 | **原始胜（根因）** |
| **recon Section 7 角色架构** | ❌ 无 | ✅ 完整角色层级 + 权限格 + 角色到代码映射 | **重构新增 ✨** |
| **recon Section 8 授权候选** | ❌ 无 | ✅ 三维预排序（水平/垂直/上下文） | **重构新增 ✨** |
| **recon Section 6.4 Guards** | ❌ 无 | ✅ Guard 分类语义（Auth/Authz/ObjectOwnership/Network/Protocol） | **重构新增 ✨** |

### 1.4 入口点总差距矩阵

| # | 差距项 | 原始能力 | 重构现状 | 严重度 |
|---|---|---|---|---|
| EP-1 | 自动生成 REST 框架检测 (finale-rest/epilogue) | `framework-analyzer.ts` 专门检测 | 无对应实现 | 中 |
| EP-2 | 前端路由→API 映射 | `frontend-mapper.ts` 完整映射 | 无对应实现 | 中 |
| EP-3 | Webhook/Upload 端点检测 | prompt 覆盖 | 无规则 | 低 |
| EP-4 | API Schema 优先读取 | pre-recon 明确指令 | `file_discovery.py` 分类但未接入 | 中 |
| EP-5 | 入口认证标注 (public/auth) | 每入口标 public/需认证 | 模型无字段 | 中 |
| EP-6 | 网络可达性系统性过滤 | 系统性排除本地工具 | 部分过滤 | 低 |
| EP-7 | 入口裁定把关 | LLM 两层裁定 | 橡皮图章 + Phase 0 不可执行 | 中 |
| EP-8 | 共享 handler 分组 (recon 4.1) | 路由映射 agent 专门识别 | 删除 | **高** |
| EP-9 | 端点安全上下文 (recon 4.2) | 完整 auth/middleware/framework 上下文 | 删除 | **高** |
| EP-10 | 多源融合 (已编码未激活) | — | `entry_point_fusion.py` 死代码 | 中（已有代码） |
| EP+1 | 角色架构/权限格 | 无 | Section 7 完整角色层级 | 重构新增 ✨ |
| EP+2 | 授权候选三维预排序 | 无 | Section 8 水平/垂直/上下文 | 重构新增 ✨ |
| EP+3 | Guard 分类语义 | 无 | Section 6.4 五类 guard | 重构新增 ✨ |
| EP+4 | 置信度评分 | 无 | 0.30-0.95 硬编码 | 重构新增 ✨ |

---

## 2. Sink 点识别

### 2.1 检测范式

| 子维度 | 原始 Shannon (TS) | 重构 Shannon-py | 差距定性 |
|---|---|---|---|
| **检测引擎** | LLM Agent（Sink Hunter 子 agent）：Glob 枚举 → 逐文件 Read → LLM 判定；业务代码 Grep 危险 API 名 | AST call node 遍历（`sink_detector.py:detect_sinks()`），匹配 `SinkRule` 规则库 | **重构更可靠**：确定性、O(1) 规则索引 |
| **规则存储** | 自然语言 prompt（`pre-recon-code.txt:289-415`） | 代码化 `DEFAULT_RULES`（47 条 `SinkRule`），Pydantic 模型 | **重构更可维护** |
| **LLM prompt 覆盖** | SSRF 13 子类 + XSS 5 上下文 + 全 Injection 类 | SSRF **13/13 完整保留** + XSS **5/5 完整保留** | **LLM 层平手** |

### 2.2 确定性规则覆盖对比

#### SQL 注入

| 语言 | 原始 (LLM) | 重构确定性层 | 差距 |
|---|---|---|---|
| Python | ✅ | ✅ `execute`, `executemany` (2 条) | 持平 |
| TypeScript | ✅ | ✅ `query` (1 条) | 持平 |
| Go | ✅ | ✅ `Query` (1 条) | 持平 |
| Java | ✅ | ✅ `executeQuery`, `execute` (2 条) | 持平 |
| PHP | ✅ | ✅ `mysqli.query`, `DB::select` (2 条) | 持平 |

**SQL 裁决**：✅ 持平

#### 命令注入

| 语言 | 原始 (LLM) | 重构确定性层 | 差距 |
|---|---|---|---|
| Python | ✅ | ✅ **6 条**: `system`, `popen`, `run`, `Popen`, `call`, `check_output` | **重构更完整** |
| TypeScript | ✅ | ✅ `eval`, `child_process.exec` (2 条) | 持平 |
| Go | ✅ | ✅ `exec.Command` (1 条) | 持平 |
| Java | ✅ | ✅ `Runtime.exec` (1 条) | 持平 |
| PHP | ✅ | ✅ **4 条**: `shell_exec`, `system`, `passthru`, `proc_open` | **重构更完整** |

**命令注入裁决**：✅ **重构确定性层优于原始**

#### 反序列化

| 语言 | 原始 | 重构确定性层 | 差距 |
|---|---|---|---|
| Python | ✅ | ✅ `pickle.loads`, `pickle.load`, `yaml.load` (3 条) | 持平 |
| Java | ✅ | ✅ `readObject` (1 条) | 持平 |
| PHP | ✅ | ✅ `unserialize` (1 条) | 持平 |
| Python marshal | ✅ LLM 可识别 | ❌ 无规则 | 微小差距 |
| JS/TS 反序列化 | ✅ LLM 可识别 | ❌ 无规则 | 微小差距 |

**反序列化裁决**：基本持平

#### SSRF

| 子类 | 原始 (LLM prompt 13 子类) | 重构确定性层 | 重构 LLM prompt | 差距 |
|---|---|---|---|---|
| HTTP Client | ✅ | ✅ **11 条** (py:requests.get/post/put + urlopen, ts:fetch+axios.get, go:http.Get+Post, java:HttpClient, php:curl_exec+file_get_contents) | ✅ | 确定性+LLM 均覆盖 |
| Socket | ✅ | ❌ | ✅ | 确定性层缺失 |
| URL Opener | ✅ | ⚠️ 仅 `urlopen` 1 条 (review) | ✅ | 确定性层窄 |
| Redirect Handler | ✅ | ❌ | ✅ | 确定性层缺失 |
| Headless Browser | ✅ | ❌ | ✅ | 确定性层缺失 |
| Media Processor | ✅ | ❌ | ✅ | 确定性层缺失 |
| Link Preview | ✅ | ❌ | ✅ | 确定性层缺失 |
| Webhook | ✅ | ❌ | ✅ | 确定性层缺失 |
| JWKS Fetcher | ✅ | ❌ | ✅ | 确定性层缺失 |
| Importer/Installer | ✅ | ❌ | ✅ | 确定性层缺失 |
| Monitoring | ✅ | ❌ | ✅ | 确定性层缺失 |
| Cloud Metadata | ✅ | ❌ | ✅ | 确定性层缺失 |

**SSRF 裁决**：⚠️ 确定性层仅覆盖 HTTP Client (~1/13 子类)，但 **LLM prompt 层 13/13 完整保留**。真实差距 = 无确定性 hint 加持，非完全缺失。

#### XSS

| 渲染上下文 | 原始 (LLM 5 上下文) | 重构确定性层 | 重构 LLM prompt | 差距 |
|---|---|---|---|---|
| HTML Body | ✅ | ❌ | ✅ | 确定性层缺失 |
| HTML Attribute | ✅ | ❌ | ✅ | 确定性层缺失 |
| JavaScript Context | ✅ | ❌ | ✅ | 确定性层缺失 |
| CSS Context | ✅ | ❌ | ✅ | 确定性层缺失 |
| URL Context | ✅ | ❌ | ✅ | 确定性层缺失 |
| DOM: innerHTML | ✅ | ✅ `ts-innerhtml` (1 条) | ✅ | 确定性层有 |
| DOM: document.write | ✅ | ✅ `ts-document-write` (1 条) | ✅ | 确定性层有 |

**XSS 裁决**：⚠️ 确定性层仅 2 条 TS 规则，但 **LLM prompt 层 5/5 上下文完整保留**。其他语言确定性层为零。

#### 模板/视图文件 Sink (SSTI)

| 检测面 | 原始 | 重构 | 差距 |
|---|---|---|---|
| Python 模板注入 | ✅ | ✅ `render_template_string`, `jinja2.render` (2 条) | 持平 |
| TS/PHP 模板注入 | ✅ | ❌ 无规则 | **原始胜** |
| **模板文件转义指令分析** | ✅ **强制两步流程**：glob 枚举模板 → 逐文件区分转义 vs 未转义（EJS `<%= %>` vs `<%- %>`、Jinja2 `{{ }}` vs `{{|safe}}`） | ❌ **双层落空**：①确定性层不分析模板文件转义指令；②LLM prompt 删了强制两步流程 | **原始胜（最大差距之一）** |
| 跨变体验证 | ✅ 检查品牌/区域/主题变体 | ❌ 完全缺失 | **原始胜** |
| Coverage Audit 表 | ✅ 完整性审计 | ❌ 删除 | **原始胜** |
| 模板文件发现 | LLM glob | ✅ `file_discovery.py` 有 10 种模板扩展名分类，但**未接 `sink_detector`（断路）** | 重构编码了能力但未激活 |

#### 文件操作

| 操作 | 原始 | 重构 | 差距 |
|---|---|---|---|
| 文件写入 | ✅ | ✅ `php-file-put-contents` (1 条) | 部分 |
| 文件包含 | ✅ | ✅ `php-include`, `php-require` (2 条) | 部分 |
| **文件读取 (fopen/readFile/open)** | ✅ | ❌ **完全缺失** | **原始胜** |
| **路径穿越** | ✅ prompt 专门覆盖 | ❌ 确定性层盲区，LLM prompt 也无专门覆盖 | **原始胜** |

#### XXE

| 检测面 | 原始 | 重构 | 差距 |
|---|---|---|---|
| XML 外部实体 | ✅ LLM prompt 覆盖 | ❌ 确定性层 0 条 + LLM prompt 也无专门覆盖 | **原始胜** |

#### 重定向

| 检测面 | 原始 | 重构 | 差距 |
|---|---|---|---|
| Open Redirect | ✅ | ✅ `ts-res-redirect`, `py-flask-redirect` (2 条) | 部分（缺 Go/Java/PHP） |

### 2.3 Sink 检测功能完整性

| 功能 | 原始 | 重构 | 差距 |
|---|---|---|---|
| 规则可维护性 | ⭐⭐ 自然语言 prompt | ⭐⭐⭐⭐⭐ Pydantic 模型 + 单测 | **重构远胜** |
| 规则结构化 | LLM 输出 JSON Schema 验证 | `SinkCallSite` Pydantic + `SlotContext` 类型系统 + `is_entry_hint` 标记 | **重构更结构化** |
| 变体审计 | ✅ 强制变体覆盖审计 | ❌ 确定性无 + LLM 指令也删 | **原始胜** |
| 跨语言 taint 传播 | LLM 兜底（不限语言） | 仅 Python/TypeScript（Go/Java/PHP 零） | **平手（各有局限）** |
| 确定性 hint 注入 | ❌ 无 | ✅ `_static-dataflow-hints.txt` 注入给 LLM | **重构新增** |

### 2.4 Sink 点总差距矩阵

| # | 差距项 | 原始能力 | 重构现状 | 严重度 |
|---|---|---|---|---|
| SK-1 | 模板文件转义指令分析 | 强制两步流程（glob→转义区分） | 双层落空（`file_discovery.py` 断路 + prompt 删除） | **高** |
| SK-2 | 跨变体验证 | 检查品牌/区域/主题变体 | 完全缺失 | 低 |
| SK-3 | Coverage Audit 表 | 完整性审计 | 删除 | 低 |
| SK-4 | XXE 检测 | LLM prompt 覆盖 | 确定性层 + LLM prompt 均无 | **中-高** |
| SK-5 | 路径穿越读取类 sink | prompt 专门覆盖 | 确定性层 fopen/readFile/open 全缺 | **高** |
| SK-6 | SSRF 确定性覆盖（12/13 非HTTP子类） | prompt 13 子类 | 确定性层仅 HTTP Client（LLM prompt 13/13 完整） | **中** |
| SK-7 | XSS 确定性覆盖（非DOM类） | prompt 5 上下文 | 确定性层仅 2 条 TS（LLM prompt 5/5 完整） | **中** |
| SK-8 | TS/PHP SSTI 模板注入 | LLM 可识别 | 无规则 | 低 |
| SK-9 | Go/Java/PHP 重定向 | LLM 可识别 | 无规则 | 低 |
| SK-10 | JS/TS 反序列化 | LLM 可识别 | 无规则 | 低 |
| SK+1 | 确定性规则引擎 | 无 | 47 条 `SinkRule` + Pydantic + 单测 | 重构新增 ✨ |
| SK+2 | Slot 类型系统 | 自然语言 slot | `SlotContext` 枚举 + `DangerousSlot` 模型 | 重构新增 ✨ |
| SK+3 | 确定性 hint 注入 | 无 | `_static-dataflow-hints.txt` → LLM | 重构新增 ✨ |
| SK+4 | is_entry_hint 标记 | 无 | 保守浅层判断（参数名/request.*） | 重构新增 ✨ |

---

## 3. 关键代码路径索引

### 原始 Shannon (TS)

| 功能 | 文件 |
|---|---|
| 框架分析 (finale-rest/epilogue) | `apps/worker/src/services/framework-analyzer.ts` |
| 框架模式定义 | `apps/worker/src/services/framework-patterns.ts` |
| 前端路由映射 | `apps/worker/src/services/frontend-mapper.ts` |
| 攻击链构建 | `apps/worker/src/services/route-chain-builder.ts` |
| 漏洞类型 Schema | `apps/worker/src/ai/queue-schemas.ts` |
| Entry Point Mapper prompt | `apps/worker/prompts/pre-recon-code.txt` (Section 5) |
| Sink Hunter prompt | `apps/worker/prompts/pre-recon-code.txt` (Section 6-7) |
| SSRF 分类 (13 子类) | `apps/worker/prompts/pre-recon-code.txt:333-415` |
| XSS 分类 (5 上下文) | `apps/worker/prompts/pre-recon-code.txt:289-321` |
| recon 路由分组 (4.1) | `apps/worker/prompts/recon-static.txt` (Route Mapper Agent) |
| recon 端点安全上下文 (4.2) | `apps/worker/prompts/recon.txt` (Section 4.2) |

### 重构 Shannon-py

| 功能 | 文件 |
|---|---|
| 入口点检测 | `packages/core/src/shannon_core/code_index/entry_points.py` |
| 入口点融合 | `packages/core/src/shannon_core/code_index/entry_point_fusion.py` |
| Sink 规则库 + 检测 | `packages/core/src/shannon_core/code_index/sink_detector.py` |
| 数据模型 | `packages/core/src/shannon_core/code_index/parameter_models.py` |
| Taint 传播 | `packages/core/src/shannon_core/code_index/chain_propagator.py` |
| 调用图 | `packages/core/src/shannon_core/code_index/gitnexus_call_graph.py` |
| 文件发现（模板/schema） | `packages/core/src/shannon_core/code_index/file_discovery.py` |
| 管线编排 | `packages/core/src/shannon_core/code_index/__init__.py` |
| LLM taint 分析 | `packages/core/src/shannon_core/code_index/llm_taint_analyzer.py` |

---

## 4. 交叉参考

本分析与以下文档互补：

- `docs/whitebox-refactoring-assessment.md` — 全维度评估（Sink/入口/漏洞 + 调用链/传播/prompt diff）
- 本文档专注于入口点 & Sink 点的**逐条代码级对比**，是 assessment 的细化数据源
