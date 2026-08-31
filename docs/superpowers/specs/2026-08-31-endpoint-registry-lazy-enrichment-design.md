# Endpoint Registry + 惰性富化设计

- 日期：2026-08-31
- 状态：📐 设计完成、**未实施**（先沉淀职责边界与 token 优化方案）
- 前置：
  - `specs/2026-08-26-report-generation-agent-design.md`（现行 `endpoint-enrich-*` 的来源）
  - `specs/2026-08-26-vuln-card-seven-sections-design.md`（`problem_points` 挂在 endpoint enrichment 的来源）
  - `specs/2026-08-27-web-resume-breakpoint-design.md`（merge 后链式步骤的 resume 取舍）
- 关联：
  - `packages/core/src/supernova_core/code_index/models.py`
  - `packages/core/src/supernova_core/code_index/__init__.py`
  - `packages/core/src/supernova_core/models/queue_schemas.py`
  - `packages/whitebox/src/supernova_whitebox/pipeline/activities.py`
  - `packages/whitebox/src/supernova_whitebox/pipeline/workflows.py`

## 1. 背景

现行 `run_endpoint_enrichment` 在双轨 merge 与 GN-only 富化之后、报告渲染之前执行，按
`injection/xss/auth/ssrf/authz` 五类并行启动 `endpoint-enrich-*` 多轮 Agent。它同时承担四类职责：

1. 解析 `entry_points.json` 的路由表；
2. 补接口元数据：method/path/auth/params/route registration；
3. 把最终漏洞卡与接口关联；
4. 钉 finding 专属的 `source_location` / `sink_location` / `problem_points`。

这解决了旧报告中“两轨接口信息不同构、无行号链”的问题，但职责已经越过接口识别阶段：
接口 canonical 元数据在报告前才被反复推断；同一接口会因多个漏洞类、多张卡被重复读码；
`source/sink/problem_points` 又是漏洞卡专属信息，不能简单把整个步骤提前到 pre-recon。

### 1.1 现状链路

```text
code index / OpenAPI / pre-recon
  → entry point fusion
  → confidence adjudication
  → entry_points.json
  → vulnerability verdict / dual-track merge
  → endpoint-enrich-{injection,xss,auth,ssrf,authz}
  → report_endpoints / report_problem_points
  → report_data / markdown
```

`entry_points.json` 来自 `AdjudicationResult`，核心键是 `func_block_id`，已有
`entry_type/route/http_method/evidence/verdict/source`；`EntryPoint` 还有
`authentication`，但该字段没有被带入 adjudicated 结果。漏洞卡侧以 `endpoint`、
`endpoints[]`、`path`、`source_endpoint` 等字符串关联接口，`report_endpoints` 才是
报告用的一体表。

## 2. 目标 / 非目标

**目标**

- 接口 canonical 元数据在接口识别阶段建立，不再由报告阶段重复识别；
- 漏洞卡与接口用稳定 `endpoint_id` 关联，报告层做确定性 join；
- LLM 只处理确实缺失或无法确定性关联的少量接口；
- 保持旧 queue / 旧报告兼容，未完成新链路时可回退现行 endpoint enrichment；
- 输出 token 与命中率的可观测统计，能验证优化效果。

**非目标**

- 不把全仓库所有接口都送 LLM 富化；
- 不把 `source_location/sink_location` 挪成接口属性——它们是漏洞链属性；
- 不重构 GitNexus 调用图与 taint verdict；
- 不改变黑盒扫描链路；
- 不在本期处理跨仓库 endpoint 对齐。

## 3. 方案对比

| 方案 | 做法 | 结论 |
|---|---|---|
| A. 整体前移 | 把现行 endpoint enrichment 挪到 pre-recon | 否决：pre-recon 没有最终漏洞卡，无法产 finding 关联与 sink 链 |
| B. 全量早富化 | pre-recon 为所有接口跑 LLM 补 params/auth | 否决：大量接口最终无漏洞，token 可能增加 |
| C. 注册表 + 惰性富化 | 接口阶段确定性建 registry；merge 后确定性 join；只对缺失项跑全局 gap agent | 选定 |

方案 C 的核心是把“接口是什么”和“漏洞如何触达接口”拆开：

```text
接口识别阶段：endpoint registry（确定性，全量、无 LLM）
漏洞分析阶段：finding → endpoint_ids（确定性优先）
merge 后：registry + finding 确定性展开 report_endpoints
gap 阶段：只补缺失元数据 / 无法关联卡（全局一次、unique 去重）
```

## 4. 数据模型

### 4.1 EndpointInventoryEntry

新增 Pydantic 模型，建议放在 `supernova_core.code_index.models`：

```python
class EndpointParameter(BaseModel):
    name: str
    location: str  # path/query/body/form/header/cookie/file/unknown
    required: bool | None = None
    provenance: str  # openapi/framework/source-rule/handler-signature/llm-gap


class EndpointInventoryEntry(BaseModel):
    endpoint_id: str
    entry_type: str
    method: str | None
    path: str | None
    handler_id: str | None
    route_registered_at: str | None
    authentication: str | None       # public/required/isLoggedIn/isAdmin/unknown
    roles: list[str] = []
    parameters: list[EndpointParameter] = []
    evidence: str
    provenance: list[str] = []
    adjudication_verdict: str
```

**endpoint_id 规则**

```text
endpoint_id = sha256(
  entry_type + "\0" + upper(method or "-") + "\0" +
  normalize_path(path or "") + "\0" + handler_id or ""
)[:16]
```

- `normalize_path` 去掉尾部 `/`，保留路径参数原貌；
- 同一 method/path/handler 生成稳定 ID，扫描内可重复引用；
- handler 为空时允许 schema-only endpoint，但不能与代码 endpoint 混淆；
- ID 冲突时追加 `-2/-3` 并在 `provenance` 记录 collision resolution。

### 4.2 endpoint_inventory.json

新产物：`intermediate/endpoint_inventory.json`。

```json
{
  "repository": "/repo",
  "schema_version": 1,
  "generated_at": "2026-08-31T00:00:00Z",
  "endpoints": [
    {
      "endpoint_id": "a1b2c3d4e5f60718",
      "entry_type": "http_route",
      "method": "POST",
      "path": "/memos",
      "handler_id": "app/routes/index.js:index:66",
      "route_registered_at": "app/routes/index.js:66",
      "authentication": "isLoggedIn",
      "roles": ["write"],
      "parameters": [{"name": "memo", "location": "body", "provenance": "framework"}],
      "evidence": "Express route: app.post('/memos')",
      "provenance": ["code_index", "framework_analysis"],
      "adjudication_verdict": "confirmed"
    }
  ],
  "by_handler": {"app/routes/index.js:index:66": ["a1b2c3d4e5f60718"]},
  "by_method_path": {"POST /memos": ["a1b2c3d4e5f60718"]}
}
```

索引 map 只作为文件内冗余索引；读侧也可在内存重建，避免 map 与列表漂移。

## 5. Registry 构建

### 5.1 构建时机

在 `run_save_adjudication` 之后新增确定性 activity：

```text
run_entry_point_fusion
  → run_save_adjudication
  → run_build_endpoint_inventory
```

`run_build_endpoint_inventory` 只读现有确定性产物，不启动 LLM：

- `code_index.json`：entry points、blocks、source points、参数图；
- `entry_points.json`：adjudication verdict；
- OpenAPI parser 产物：path/query/body 参数；
- framework analysis / route chains 可作为后续确定性补充来源。

若 framework analysis 在当前 workflow 中晚于该步骤执行，则允许 registry v1 先落盘，
framework analysis 完成后做一次确定性 upsert；两次都不调用 LLM。

### 5.2 字段来源

| 字段 | 确定性来源 | 缺失语义 |
|---|---|---|
| method/path | `EntryPoint` / OpenAPI / framework analyzer | 缺失则 entry_type 非 HTTP 或 unknown |
| handler_id | `func_block_id` | schema-only 可为空 |
| route_registered_at | `func_block_id` 尾部行号 / route evidence | unknown |
| authentication | EntryPoint.authentication、middleware、framework result | unknown |
| parameters | OpenAPI、framework analyzer、handler signature、source points | 空数组，不猜 |
| roles | source/write/trigger 语义由 sink/render 关系决定 | 不在 registry 强行推断 |
| verdict | adjudication | required |

Registry 不存 `source_location/sink_location`。这两者必须挂在漏洞卡的
`report_endpoints[].source_location/sink_location`，因为同一接口在不同漏洞链中的
source/sink 不同。

## 6. Finding ↔ Endpoint 关联

### 6.1 Queue schema

`BaseVulnerability` 新增 append-only 字段：

```python
endpoint_ids: list[str] | None = None
```

旧 queue 缺省 `None`，解析兼容。merge 时对同洞卡取并集并去重，顺序为主卡优先。

### 6.2 GitNexus 轨

- `CallChain.entry_point_id` / builder 输出时查找 registry 的 `by_handler`；
- 同一 handler 多路由时，先按链与路由可达性过滤；无法过滤则保留多个 endpoint_id；
- `affected_entries[].chain_id` 继续保留，作为漏洞链溯源；
- 不用 endpoint 字符串替代链 ID。

### 6.3 LLM 轨

漏洞 Agent 不必输入全量 registry——这会把 token 转移到漏洞分析阶段。LLM 卡继续输出
现有 `endpoint/endpoints/path/source_endpoint`，merge 后由确定性 resolver 归一化：

1. 精确解析 `METHOD /path`；
2. 查 `by_method_path`；
3. 单候选直接绑定 `endpoint_id`；
4. 多候选保留 ambiguity，不猜；
5. 无候选进入 gap 队列。

后续可让 prompt 输出 `endpoint_ids`，但只在已有轻量路由摘要的场景启用，不作为 Phase 1 前提。

### 6.4 auth / authz

- authz GitNexus 候选已有 handler 维度 endpoint 语义，统一映射到 registry ID；
- auth 卡优先解析 `source_endpoint`；
- 全局配置类问题允许 `endpoint_ids=[]`，报告中显示“全局配置问题”，不强行造 HTTP 接口；
- missing-control 位置用 `vulnerable_code_location` / `missing_defense` 表达，不伪造 sink。

## 7. Merge 后确定性展开

新增 activity：`run_resolve_finding_endpoints`，位置在 `run_gn_finding_enrichment` 之后：

```text
run_merge_dual_track_queues
  → run_gn_finding_enrichment
  → run_resolve_finding_endpoints
  → optional run_endpoint_gap_enrichment
  → report assembly
```

### 7.1 输入

- `endpoint_inventory.json`
- `{vc}_exploitation_queue.json`
- `route_chains.json`
- `code_index.json`

### 7.2 处理规则

对每张最终漏洞卡：

1. 若已有 `endpoint_ids`，从 registry 展开接口元数据；
2. 若无 ID，尝试字符串归一化与 exact match；
3. 若有 `affected_entries`，从漏洞链提取 finding 专属 `source_location/sink_location`；
4. 生成或更新 `report_endpoints`：registry 提供 method/path/auth/params/route_registered_at，finding 提供 source/sink/role；
5. 完全无法确认的接口不写入，避免幻觉；
6. 记录每卡 linkage 状态：`registry_id` / `normalized_string` / `unresolved`。

该 activity 不调用 LLM。运行失败与现行 enrichment 一样 non-fatal，但 activity 注册缺失必须 fail-fast。

## 8. 惰性 Gap 富化

`run_endpoint_gap_enrichment` 只处理确定性 resolver 留下的缺口，且必须先去重：

```text
unresolved finding references
  → unique endpoint candidates
  → missing metadata endpoints
  → global one-shot gap agent
```

### 8.1 触发条件

默认开启，但满足以下条件才实际调用 Agent：

- 存在 unresolved 接口引用；
- 或最终漏洞卡引用的 registry entry 缺 `authentication` / `parameters`；
- 或同一接口在不同卡上的 `source/sink` 冲突且确定性链无法裁决。

若没有任何缺口，activity 返回 `{"llm_calls": 0}`。

### 8.2 Agent 形态

- 一个全局 Agent，一批发量处理 unique endpoints，不再按漏洞类开五个 Agent；
- 输出只允许补 registry 缺失元数据、为 unresolved 字符串绑定已有 endpoint_id、说明确实无 HTTP endpoint；
- Agent 输出先写 `intermediate/endpoint_gap_verdicts.json`；
- 校验通过后 upsert registry，并重跑确定性 resolver；
- 不直接改漏洞卡叙述字段，不直接产出 `problem_points`。

### 8.3 problem_points 拆分

`problem_points` 是 finding 专属信息，不再与接口元数据捆绑：

1. 确定性优先：从 `source_location/sink_location` 提取真实 snippet，生成基础问题点；
2. GN-only 卡继续由 `run_gn_finding_enrichment` 生成高质量说明；
3. LLM 卡仅在 description 缺失时进入轻量 finding-gap 队列；
4. 禁止因接口元数据完整而跳过问题点，也禁止因问题点缺失重跑全量接口识别。

## 9. 报告消费

`report_data_builder` / `findings_renderer` 数据优先级调整为：

1. `report_endpoints`（resolver / gap 后写回的最终形态）；
2. `endpoint_ids + endpoint_inventory.json` 的确定性展开；
3. 旧 `endpoint/endpoints/path` 确定性 parse 兜底；
4. 均失败则显示非 HTTP / unknown，不猜路径。

报告层不再调用 LLM，也不维护接口推断逻辑。接口 ID 稳定后，跨卡、跨漏洞类和后续融合报告都能复用同一 registry。

## 10. Token 与可观测性

### 10.1 Token 模型

旧模式：

```text
LLM calls = 有队列卡片的漏洞类数（最多 5 次）
prompt / tool / output 体量 ≈ Σ(每类卡片数 + 每类 route table)
同一接口会被同类多卡、多类重复读码
route table 会被多个 endpoint-enrich-* prompt 重复携带
```

新模式：

```text
registry 构建 = 0 LLM token
deterministic resolver = 0 LLM token
gap agent ≤ 1 次全局调用，输入只含 unique 缺口
problem points 仅对缺失卡做 finding-gap
```

预期收益在多漏洞类、多卡命中同一接口、route table 较大、OpenAPI/framework 元数据完整的场景显著。
单漏洞、单接口、registry 元数据全缺失的小扫描可能不省 token，但确定性 join 仍能减少重复幻觉和重复输出。

### 10.2 endpoint_linkage_summary.json

每次 resolver / gap 后写：

```json
{
  "schema_version": 1,
  "finding_count": 25,
  "findings_with_endpoint_ids": 22,
  "deterministic_resolved": 21,
  "gap_resolved": 1,
  "unresolved": 2,
  "referenced_unique_endpoints": 9,
  "missing_metadata_endpoints": 3,
  "llm_calls": 1,
  "token_input": 1234,
  "token_output": 567
}
```

验收必须对比 endpoint 相关 input/output token 总量、最终卡接口覆盖率、幻觉 path / 未知 ID warning 数、`report_endpoints` 行数差异。

## 11. 配置与回退

- `SUPERNOVA_ENDPOINT_REGISTRY_ENABLED`：默认开；关闭时走旧 `run_endpoint_enrichment`；
- `SUPERNOVA_ENDPOINT_GAP_ENRICH_ENABLED`：默认开；关闭时只保留确定性结果；
- `SUPERNOVA_ENDPOINT_GAP_MAX_ENDPOINTS`：单批默认 50；
- 旧 queue 无 `endpoint_ids` 仍可解析；
- registry 缺失时 report builder 用旧兜底，不让扫描失败；
- 新旧开关过渡期内保留旧 prompt，验证一个扫描周期后删除旧路径。

## 12. 实施拆分

### Phase 1：Registry

1. 新增 `EndpointInventoryEntry` / `EndpointParameter` 模型；
2. `save_adjudication` 或新 activity 生成 `endpoint_inventory.json`；
3. 将 `EntryPoint.authentication` 带入 registry；
4. 建立 handler / method-path 索引；
5. 单测覆盖稳定 ID、路径归一化、schema-only endpoint、重复路由。

### Phase 2：确定性关联

1. `BaseVulnerability.endpoint_ids`；
2. GitNexus builder 回填 endpoint ID；
3. merge 保并集；
4. 新增 `run_resolve_finding_endpoints`；
5. report builder 优先消费 registry；
6. E2E 对比旧 `report_endpoints`。

### Phase 3：Gap 收敛

1. 新增 `run_endpoint_gap_enrichment`；
2. unique 缺口批量输入；
3. gap verdict 校验、registry upsert、resolver 重跑；
4. 写 linkage summary；
5. 灰度对比 token 与覆盖率。

### Phase 4：下线旧路径

1. `problem_points` 确定性兜底与 GN enrichment 分流；
2. 删除五类并行 endpoint enrichment 的默认调度；
3. 保留兼容读取旧 report_endpoints；
4. 更新 CLAUDE.md 与报告生成 spec。

## 13. 测试计划

**模型 / registry**

- endpoint ID 稳定、路径归一化、method 大小写；
- 同 handler 多路由、同路由多 handler；
- OpenAPI 参数与 code endpoint 合并；
- rejected endpoint 不进入默认 registry；
- authentication unknown 不被伪造成 public/required。

**resolver**

- GitNexus chain → handler → endpoint_id；
- LLM `POST /foo` 字符串 exact match；
- 多候选不猜；
- auth 全局配置卡 endpoint_ids 为空；
- source/sink 从 finding 链写入而非 registry。

**merge / report**

- endpoint_ids 并集去重；
- 旧 queue 无 endpoint_ids 兼容；
- report_endpoints 优先级正确；
- 无 HTTP endpoint 渲染为空接口而不是虚构路由。

**gap / token**

- 无缺口时 `llm_calls=0`；
- 同接口多卡只出现一次 gap 输入；
- gap 输出未知 endpoint_id 被丢弃；
- gap 成功后 registry upsert 并触发 resolver 二次运行；
- summary 统计与实际 LLM accounting 一致。

## 14. 风险与决策

| 风险 | 处理 |
|---|---|
| framework/OpenAPI 信息不足，deterministic join 覆盖低 | gap agent 只补最终漏洞引用的 unique 接口，避免全量富化 |
| endpoint_id 在 handler 重构后变化 | ID 只要求扫描内稳定；跨扫描对齐走 method/path/handler 相似度，另立 spec |
| 同一 method/path 多 handler | handler_id 参与 ID；报告按实际可达链选择，不猜 |
| auth/authz 被误套 taint 模型 | 全局问题允许无 endpoint；missing-control 位置独立表达 |
| registry 与 entry_points 漂移 | registry 由 adjudication 后确定性派生，framework upsert 后版本号递增 |
| token 反而增加 | Phase 3 灰度指标不达标则关闭 gap，仅保留 0-LLM registry/resolver |

## 15. 验收标准

- 多类多卡扫描中 endpoint 相关 LLM 调用从 5 类并行降为 0 或 1 次全局 gap；
- 已有 `endpoint_ids` 的卡不再进入 LLM；
- registry 元数据完整的接口不再重复读码；
- `report_endpoints` 覆盖率不低于旧模式；
- 幻觉 path / 未知 ID 数不增加；
- endpoint input/output token 总量下降；
- 关闭 gap 后扫描仍能产出确定性报告。
