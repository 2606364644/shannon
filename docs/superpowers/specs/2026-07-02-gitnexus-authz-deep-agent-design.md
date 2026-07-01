# authz GitNexus 轨深度 agent + B 补候选（spec-1） 设计

> 日期：2026-07-02　分支：`feat/fork-py`　所属 epic：`2026-07-02-gitnexus-deep-agent-auth-authz-design.md`（子项目 1）
>
> **背景**：epic 把 authz GitNexus 轨判定从轻量单次升级为吃 IDOR 候选的多轮深度 agent。本 spec 两件事：(a) `run_authz_gitnexus_judge` 判定改多轮 agent（用 spec-0 的 `run_gitnexus_verdict_agent` 脚手架）；(b) B 补候选质量——当前三目标 `entry_points=0` → 候选空 → 深度 agent 没东西判。
>
> **关键资产发现**：`source_detector` 已产出完整参数信息（param_name/expression/source_type），authz track 用它做三重过滤，但 **render 给 LLM 的 prompt 丢了这些字段**（`authz_gitnexus_track.py:309-387`）。补回去是小工作量高收益。

---

## 1. 目标 / 非目标

### 目标

- **G1（判定加深）**：`run_authz_gitnexus_judge` 判定从单次 `run_claude_prompt` → 多轮 agent（`run_gitnexus_verdict_agent`），带 grep/read 自主追 owner 检查 / 授权逻辑。
- **G2（候选空时自主探索）**：`candidate_count==0` 时不再直接写空 queue，改为让 agent 自主探索（读 route 定义、找 IDOR 模式），产软候选。
- **G3（补 render——SourcePoint 参数喂 LLM）**：render 函数加 SourcePoint 的 param_name/expression/source_type 列。**澄清 epic 措辞**：参数发现本身已由 `source_detector` 完成（产出 SourcePoint，写 `code_index.json`，见 §2 证据）；epic §3 子项目 1 的"参数发现"= 让 authz 判定**吃到**这些已产参数。本 spec 只补 render，**不新建参数发现**能力。
- **G4（扩框架 entry_points 识别）**：`detect_entry_points` 加 FastAPI / Laravel / JAX-RS / GraphQL 等模式（纯正则，低风险）。
- **G5（接 OpenAPI schema 解析）**：实现 OpenAPI parser（`merge_entry_points` 框架已就绪，缺 parser），schema_eps 接入 `run_entry_point_fusion`。
- **G6（fusion 门控评估）**：`entry_point_fusion` 现被 `enable_llm_track` 门控（`workflows.py:173`）——纯 GitNexus 模式下不跑。评估是否解耦（让 fusion 的 schema/convention 源在关 LLM 轨时仍跑）。

### 非目标

- **不改 chain_verdict（inj/xss/ssrf）**：epic 非目标（它们有确定性链兜底）。
- **不改 LLM 轨 `vuln-authz.txt`**：保留为可选增强（双轨 OR）。
- **不改双轨 merger**：`authz_gitnexus_queue.json` schema 不变，merger 无感。
- **不做 auth**：auth 在 spec-2a/2b。
- **不改 IDOR 候选生成算法**（`find_unguarded_sink_paths` / `find_framework_idor_candidates`）：dominance/framework 候选逻辑不变，只改判定深度 + 喂给 LLM 的信息 + 候选来源（entry_points/OpenAPI）。

---

## 2. 现状证据

| 现象 | 证据 |
|---|---|
| authz judge 单次 LLM | `activities.py:322-354`：`candidate_count>0` 时单次 `run_claude_prompt(prompt, structured_output_schema={"vulnerabilities":[...]})`；无 `max_turns` |
| 候选空直接跳过 | `activities.py:303-311`：`candidate_count==0` 走 log_info warning，写空 queue（`vulnerabilities=[]`） |
| 三目标空壳根因 | `entry_points.py:13-28` 只认 python/go/ts/java/php 5 语言基础模式；NodeGoat(Express 非 NestJS)/juice/crAPI 全 `entry_points=0` → 无 SourcePoints → `find_unguarded_sink_paths` 第一层过滤（`authz_gitnexus_track.py:179` `if not ep_sources: continue`）跳过 → dominance_candidates=[] |
| 框架盲区 | `entry_points.py`：FastAPI `@app.get` 无显式规则（仅 `@router.*` 部分覆盖）、Laravel `Route::get` 不认（PHP 只认 `#[Route(`）、JAX-RS `@Path/@GET` 不认（Java 只认 Spring）、GraphQL/gRPC 无识别 |
| SourcePoint 已产未渲染 | `source_detector.py:70-109` 产 SourcePoint（param_name/expression/source_type），写 `code_index.json`；`authz_gitnexus_track.py:162-165,179,209` 用它做三重过滤 + 存 `IDORCandidateChain.source_point_ids`（`:60`）；但 `render_authz_gitnexus_candidates()`（`:309-387`）表格**无 param/expression/source_type 列** |
| OpenAPI parser 不存在 | `entry_point_fusion.py:20-108` `merge_entry_points(gitnexus, schema_eps, convention_eps, llm_eps)` 框架就绪；但 `run_entry_point_fusion`（`__init__.py:363-425`）**没调它**，只做 LLM 源简化合并；全项目无 `parse_openapi` 实现；`schema_eps` 永远空 |
| fusion 被 LLM 轨门控 | `workflows.py:173-186`：`if input.enable_llm_track:` 才跑 `run_entry_point_fusion`——关 LLM 轨时连 LLM 发现的入口都不融合 |
| IDOR 候选字段缺口 | `IDORCandidateChain`（`authz_gitnexus_track.py:51-60`）：有 endpoint_id/handler_id/sink_id/path/guard_nodes_on_path/source_point_ids；缺 param_name/route/http_method（需从 SourcePoint/EntryPoint 间接查） |

---

## 3. 设计

### 3.1 判定加深：单次 → 多轮 agent（G1）

`activities.py:322-354` 的判定段改为调 `run_gitnexus_verdict_agent`（spec-0）：

```python
# activities.py run_authz_gitnexus_judge 判定段
if candidate_count > 0:
    prompt = prompt_manager.load_sync("authz_gitnexus_judge_deep", variables={...})  # 新多轮 prompt
    result = await run_gitnexus_verdict_agent(
        prompt=prompt, repo_path=str(repo), audit_session=_session,
        structured_output_schema={"type":"object","properties":{"vulnerabilities":{"type":"array"}}},
    )
    raw = result.structured_output or {}
```

**候选分发策略**（多轮 agent 吃候选的方式）：
- 候选 ≤ 阈值（如 5）：全量塞 prompt，agent 多轮逐条深判。
- 候选 > 阈值：分批（每批 ≤5），并行跑多个 verdict agent（fan-out，对齐 vuln agent 的 `asyncio.gather` + Semaphore 模式）。

**新 prompt** `authz_gitnexus_judge_deep.txt`：在现 `authz_gitnexus_judge` 基础上加工具引导（"你可以用 grep/read 追 owner 检查、授权 middleware、跨文件调用链"）+ 候选的 SourcePoint 细节（G3 产出）。

### 3.2 候选空时自主探索（G2）

`activities.py:303-311` 的 `candidate_count==0` 分支改为调 agent 自主探索：

```python
if candidate_count == 0:
    # 不再直接写空 queue；让 agent 读 route 定义 / code_index 自主找 IDOR 模式
    result = await run_gitnexus_verdict_agent(
        prompt=prompt_manager.load_sync("authz_gitnexus_explore",
            variables={"entry_points_summary": ..., "routes": ...}),
        repo_path=str(repo), audit_session=_session,
        structured_output_schema=...,
    )
    # agent 产软候选（needs_review=True）写 queue
```

> **为何**：候选空常因 entry_points 漏识别（G4/G5 修），但即便如此，agent 读源码仍可能自主发现 IDOR（LLM 轨 vuln-authz 就是这么干的）。这让 GitNexus 轨在候选生成失效时仍有产出，而非静默空转。

### 3.3 补 render：SourcePoint 参数喂 LLM（G3，小工作量高收益）

`authz_gitnexus_track.py:309-387` `render_authz_gitnexus_candidates()` 加列：按 `candidate.source_point_ids` 从 `index.source_points` 查 param_name/expression/source_type，加进 markdown 表格 + 喂 prompt 的候选描述。

```python
# render 时（index 已含 source_points）
for cand in candidates:
    sps = [sp for sp in index.source_points if sp.id in cand.source_point_ids]
    params = ", ".join(f"{sp.param_name}({sp.source_type}): {sp.expression}" for sp in sps)
    # 加进候选行：Param 列 = params
```

同时补 route/http_method（从 `EntryPoint` 按 `endpoint_id` 查）。这些数据都已存在，纯 render 改动。

### 3.4 扩框架 entry_points 识别（G4）

`entry_points.py` 各语言规则补：
- Python：FastAPI `@app.(get|post|put|delete|patch)(...)`、`@*.api_route(...)`。
- PHP：Laravel `Route::(get|post|put|delete|patch|any|match)\(...\)`、`$router->(get|post|...)`。
- Java：JAX-RS `@Path` + `@(GET|POST|PUT|DELETE)`、Spring `@RestController` 类级 `@RequestMapping`。
- TypeScript：Koa `router.(get|post|...)`、GraphQL `type Query/Mutation` resolver（标 needs_llm_review）。
- Go：echo `e.(GET|POST|...)`、chi `r.(Get|Post|...)`。

纯正则/签名匹配，低风险。每条规则加单测。

### 3.5 接 OpenAPI schema 解析（G5）

新建 `code_index/schema_entry_parser.py`：
- 扫 repo 找 `openapi.yaml/.json`、`swagger.yaml/.json`（含 `paths` 字段）。
- 解析 `paths` → 每条 `(method, path)` 产 `UnifiedEntryPoint(source="schema_file", method, path, ...)`。
- 接入：`run_entry_point_fusion`（`__init__.py:363-425`）改为调完整 `merge_entry_points(gitnexus_eps, schema_eps, convention_eps, llm_eps)`，传 schema_eps。

> **为何**：OpenAPI 是"金矿"——显式列出所有路由 + 参数 + 认证要求，比正则扫代码准得多。`merge_entry_points` 框架已为它留位（schema_eps），只缺 parser。

### 3.6 fusion 门控评估（G6）

`workflows.py:173-186` 的 `if input.enable_llm_track:` 拆分：
- **LLM 源融合**（读 pre_recon_deliverable）：仍受 `enable_llm_track` 门控（关 LLM 轨时无 LLM 产物）。
- **schema/convention 源融合**（G5 OpenAPI + 框架 convention）：**解耦**，关 LLM 轨时仍跑（纯确定性，不依赖 LLM 轨）。

```python
# workflows.py
# schema/convention fusion 无条件跑（纯确定性）
await workflow.execute_activity(activities.run_entry_point_fusion, act_input, ...)  # 内部 schema/convention 源
if input.enable_llm_track:
    # LLM 源融合（读 pre_recon）单独跑或合并
    ...
```

> **为何**：epic 的目标是"关 LLM 轨时 GitNexus 轨独立兜底"。若 fusion 被 LLM 轨门控，关 LLM 轨时 GitNexus 轨连 entry_points 都融合不全，兜底失效。schema/convention 是确定性的，不该被 LLM 轨开关拖累。

---

## 4. 验收

- **V1（判定加深）**：候选非空时，`run_authz_gitnexus_judge` 跑多轮 agent（`result.turns > 1`），产含 owner 检查/授权逻辑证据的 `authz_gitnexus_queue.json`。
- **V2（自主探索）**：候选空时，agent 自主探索产软候选（`needs_review=True`），queue 非空（或在源码确无 IDOR 时给出"已探索无发现"证据，而非静默空）。
- **V3（SourcePoint 喂 LLM）**：render 的候选描述含 param_name/expression/source_type；prompt 里候选带参数细节。
- **V4（框架扩展）**：FastAPI/Laravel/JAX-RS 测试样本下 `detect_entry_points` 能识别（单测覆盖每条新规则）。
- **V5（OpenAPI）**：含 `openapi.yaml` 的 repo，`run_entry_point_fusion` 后 `code_index.json` 的 entry_points 含 schema 源条目。
- **V6（fusion 解耦）**：`SHANNON_LLM_TRACK_ENABLED=0` 时 schema/convention fusion 仍跑（AST/回归锚点）。
- **V7（R3 token 实测，epic 关键）**：GitNexus 深度 agent（吃候选）vs LLM 轨 vuln-authz（从零）在 同 repo 上的 token/召回对比——确认"吃候选省 token"杠杆成立（或证伪，则 epic R3 触发降级评估）。
- **V8（双引擎）**：glm-anthropic / glm-openai 双引擎探针实测多轮 authz verdict PASS。
- **V9（回归）**：现有 authz 单测（`test_authz_*.py`）不破（candidate_count>0 路径行为兼容；candidate_count==0 路径从"写空"变"探索"，相关测试更新）。

---

## 5. 风险

- **R1（候选分发复杂度）**：多候选分批并行跑多轮 agent，编排复杂 + token 大。**对策**：阈值分批 + Semaphore 限并发；max_turns 默认 30 封顶。
- **R2（自主探索产噪）**：候选空时 agent 自主找 IDOR 可能误报多。**对策**：软候选全标 `needs_review=True` + `confidence="low"`，merger 不清除（对齐 chain_verdict conservative fallback）。
- **R3（epic 级，token 杠杆证伪）**：若 V7 实测 GitNexus 深度 agent 不比 LLM 轨 vuln-authz 省 token（候选质量差导致 agent 仍从零探索），则"吃候选省 token"杠杆不成立，epic 战略价值打折。**对策**：V7 必须在合并前实测；证伪则回 epic 重新评估（可能退回 A 或调整）。
- **R4（OpenAPI parser 健壮性）**：OpenAPI spec 版本/写法多样（v2/v3/嵌入式），parser 可能解析失败。**对策**：解析失败 non-fatal（记 warning，跳过 schema 源，不阻塞主管道）；单测覆盖典型 spec。
- **R5（框架正则误报）**：扩框架识别可能误匹配（如变量名含 `get`）。**对策**：每条规则单测 + needs_review 标记低置信候选。
- **R6（fusion 解耦影响）**：把 schema/convention fusion 移出 `enable_llm_track` 门控，改变关 LLM 轨时的行为。**对策**：V6 回归锚点；确认 schema/convention 源纯确定性（不依赖 LLM 轨产物）。
