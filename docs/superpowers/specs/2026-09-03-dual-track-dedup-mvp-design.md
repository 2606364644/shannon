# 双轨去重断裂修复 MVP 设计（spec 2026-09-03）

> 本文档定稿「漏洞报告去重失效」的根因与 MVP 修复方案。由 NodeGoat 20260903-071648 扫描报告的 15 条 XSS（6 条冗余）实证驱动。
> 配套实施计划：`docs/superpowers/plans/2026-09-03-dual-track-dedup-mvp.md`

## 1. 问题

### 1.1 现象（NodeGoat 20260903-071648 实证）

报告 23 条漏洞中 XSS 15 条，按漏洞本体人工归并后仅约 9 个独立漏洞，**6 条冗余**：

| 漏洞本体 | 报告条目 | 冗余 |
|---|---|---|
| `POST /login` userName 反射 XSS | XSS-VULN-01 (llm) + GN-28 + GN-32 + GN-37 (gn×3) | ×4 |
| `POST /benefits` benefitStartDate 存储 XSS | XSS-VULN-06 (llm) + GN-03 + GN-07 (gn×2) | ×3 |
| `POST /signup` userName/email | XSS-VULN-02 + GN-30 | ×2 |
| `GET /allocations/:userId` | XSS-VULN-05 + GN-01 | ×2 |
| `POST /profile` firstName | XSS-VULN-03 + GN-26 | ×2 |

且 15 条 XSS 中 **0 条 `merge_source="both"`**——双轨配对在本报告上完全失效。

### 1.2 归一化失效的量化（修复前的基线，夹具回归用）

对本次 18 个 queue JSON 实跑 `collapse_gn_entries` + `_finding_key`：

```
injection | LLM keys 2, GN keys 2 | 确定性 key 交集 0
ssrf      | LLM keys 1, GN keys 1 | 确定性 key 交集 0
xss       | LLM keys 6, GN keys 10| 确定性 key 交集 0
```

XSS GN 轨 33 条折成 10 组，其中 4 组本应合流：

```
('Reflected', 'POST /login', 'render')           -> GN-28,29,36,45,48
('Reflected', 'app/routes/session.js', 'render') -> GN-32,34,40,41,43,44,49,50  ← 同一漏洞
('Reflected', 'POST /login,', 'render')          -> GN-37                         ← 同一漏洞
```

## 2. 根因

**全部根因在确定性数据管道的 key 归一化断裂，不在 agent 执行质量**。本次扫描无任何 agent 失败（33 条链 verdict 全部完成、track-parity 正常执行），属「正常跑也会有」。

### R1 字段缺失：GN 卡 `endpoint` 永不赋值

`xss_builder.py:195-210` 构造 `XssVulnerability` 时**从未给 `endpoint` 赋值**（route 只拼进 `path` 文本前缀）。verdict agent 重写产物时 `endpoint=None`、`path` 变自由文本。先例：`sink_call` 缺失是同一病的上次发作（spec 2026-08-26 §7），修法为 builder 确定性回填 `chain.sink_call_site_id`——本次 `endpoint` 是第二发作。

### R2 join ID 错位：路由表与链记的不是同一个 ID

- 路由表 `entry_points`：注册点 `app/routes/index.js:index:11`（Express `app.post('/login', ...)` 在此）
- 链 `entry_point_id`：handler 函数 `app/routes/session.js:SessionHandler:8`

NodeGoat 是 handler 注册模式，两边天然对不上 → `http_route_label()` join 全 miss。

**附带发现（隐藏 bug）**：`activities.py:3334` `entry_point_map = {ep.func_block_id: ep for ep in index.entry_points if ep.route}` —— 同 `func_block_id` 的**多条路由被 dict 折叠只剩最后一条**（NodeGoat 23 条路由全挂 `index.js:index:11`）。即便 join key 对上也只会拿到错误的最后一条路由。回填白名单必须从 `index.entry_points` 全量构建，不走 `entry_point_map`。

### R3 形态不统一（三类，各自断一维）

| 维度 | LLM 轨 | GN 轨 | 断裂处 |
|---|---|---|---|
| vtype | `CommandInjection` | `injection` | injection 类第一维就断（其余两维都对） |
| 占位符 | `GET /allocations/{userId}` | `GET /allocations/:userId` | authz 类（`_normalize_endpoint` 不归一） |
| sink | `swig {{userName}} in value attribute` | `render` | xss 类（渲染语义 vs 函数名） |

其中 sink 维近乎 XSS 特有：injection 的 LLM sink（`'eval() @ ...:32'`）剥括号剥 receiver 后即 `eval`，与 GN 解析出的 `eval` 天然可撞（已验证）。

### R4 兜底层静默

- `track-parity` 配对 0 产出时三种情况（无对 / 全中低置信 / 解析失败）无法区分（本次 XSS 类 61s 调用静默零产出）
- LLM 轨 `merge_dual_track_queues` 同 key 多卡 `setdefault()` **静默留第一张丢其余**
- 报告出现 `endpoints: ['/memos)']`、`POST /login,` 脏值（`extract_endpoint` 正则 `(/\S*)` 贪婪吞尾标点）

## 3. 修复方案（MVP 6 项）

> 原则：修 R1-R4 上游根因，下游重复/缺料随之消失。**只放宽「key 碰撞半径」的方向上，误合并危害 > 重复，逐项带风险缓解。**

### F1 正则剥尾标点 —— 治 R4 脏值

`gn_collapse.py` `_METHOD_PATH` 提取的 route 剥尾标点 `),.;'"）`（含全角）。同时消 `/login,`、`/memos)` 脏 key 与报告脏展示。

### F2 vtype 类级归一 —— 治 R3 vtype 维

`dual_track_merger.py` 新增 `_canonical_vtype()`：细分 → 类级映射表（`CommandInjection`/`RCE`/`SQLi`/… → `injection`；`URL_Manipulation`/`SSRF` → `ssrf`；`Reflected`/`Stored` → `xss`）。**只作用于 key 计算（`_strict_key`/`_finding_key`/`gn_collapse._unit_key`），不动卡上展示字段**。authz 类原样返回（`Horizontal` 的 endpoint-only 特判依赖其形态）。

### F3 XSS Stored/Reflected 类级化 + 轨内折叠化 —— 治 R3 + R4 LLM 轨吞卡

- XSS 的 `Stored`/`Reflected` 细型不进 key（同一洞 LLM 写 Stored、GN 写 Reflected 是常态——benefitStartDate 实例），类级 `xss` 进 key
- **必须配套**：LLM 轨同 key 多卡从 `setdefault()` 静默吞卡改为**折叠**——主卡保留、其余卡 ID 挂靠 `merged_from` + 打 log（对齐 GN collapse / attach 语义，不丢不同的漏洞本体）

> ⚠️ 风险（F2/F3 组合）：`Stored/Reflected` 类级化 + sink 归一（F9，本 MVP 不做）后可能轨内误撞。本 MVP **sink 维维持文本归一不变**，`swig {{firstnamesafestring}}…` 与 `html` 文本不同不撞——故本 MVP 内可控。若未来做 F9 需重新评估轨内折叠粒度。

### F4 占位符归一 —— 治 R3 占位符维

`gn_collapse.py` 新增 `_normalize_placeholders(path)`：`:userId` → `{userId}`（Express ↔ OpenAPI 同义路由）。`extract_endpoint`（GN 侧）与 `_normalize_endpoint`（merger 侧）统一接入。**保留参数名**（`{userId}` vs `{id}` 是不同路由，不得都变 `{param}`）。

### F5 观测 —— 治 R4 静默

1. `track_parity` 0 产出三态显式区分（无对 / 全中低置信 / 解析失败）各打一行 log
2. F3 折叠化 log（见上）
3. `collapse_gn_entries` 分组统计 log：endpoint 分支 vs 文件回退分支占比（分叉率 = F6 体温计）

### F6 endpoint 确定性回填 —— 治 R1/R2 主杠杆

**F6a builder 赋值（静态 join）**：`xss/injection/ssrf` 三个 builder 构造卡时加 `endpoint=route_label`（`route_label` 变量已算出，仅拼进 path；一行加参）。`ssrf_builder.py:85` 的 `source_endpoint=route_label or chain.entry_point_id` 改为 `source_endpoint=route_label`（消除 handler-id 脏值兜底）。

**F6-B 白名单验证回填（verdict 后处理）**：新文件 `endpoint_backfill.py`，纯函数：

```
对 endpoint 仍为 None 的卡：
  从 title/source_detail/evidence_chain/path 提取所有 "METHOD /path" 提名
  → 归一（F1 剥标点 + F4 占位符）
  → 全量路由白名单验证（index.entry_points 全量构建的 set，绕开 entry_point_map dict 折叠 bug）
  → 唯一命中才采信回填；白名单外丢弃；多候选歧义不采信（宁缺勿错拼）
```

**风险缓解（分级采信）**：本 MVP 仅「白名单验证」单级采信，凭 ground truth 夹具回归把关（见 §4）。「文件一致性交叉检查 / B 裸采信仅允许 attach」记为后续项（F6-B2），不阻塞 MVP。

### 不做（记录在案，避免无限扩张）

- F9 XSS sink 模板族归一（待 F6 落地后实测 pairing 残余率再定）
- F11 边界对 agent 深验（纯兜底，数据说话后再上；且涉容量铁律窗口重估，见 §5）
- F7-A 静态解析注册行语言 adapter（随真实大仓形态驱动）

## 4. 验证策略

### 4.1 夹具回归（Task 7，核心验收）

本次 4 个 queue JSON（xss_llm / xss_gitnexus / injection_llm / injection_gitnexus）固化为测试 fixture，断言：

- XSS GN 33 条 `collapse_gn_entries` 后 ≤7 组（修前 10 组；`POST /login`、`/benefits` 全折叠无跨组分叉）
- `_finding_key` 确定性交集：injection ≥1、xss ≥1（修前全 0）
- **ground truth**：`/login` 的全部 GN 卡 collapse 成 1 组、`/benefits` 全 1 组（防「重复数下降但错合并」）

**验收标准不是「重复数下降」而是「合并对全部正确」**。

### 4.2 单测

F1/F4 归一化函数表驱动（`POST /login,`、`/memos)`、`{userId}`↔`:userId`、`https://` 不误伤）；F2 vtype 映射表；F3 折叠化；F5 三态 log（caplog）；F6-B 白名单验证（唯一命中 / 多歧义 / 白名单外 / endpoint 已有值不动）。

### 4.3 端到端（可选，非门禁）

重扫 NodeGoat，XSS 15 条 → 预期 ~9 条、`both` 占比回升。

## 5. 风险与缓解（评估结论）

| 风险 | 等级 | 缓解 |
|---|---|---|
| F6-B 白名单保证「路由存在」不保证「链归属该路由」，可能给错误穿确定性外衣 | **高** | MVP 用 ground truth 夹具把关；分级采信（F6-B2）记录在案；白名单外丢弃 / 多歧义不采信 |
| F2/F3 轨内误吞（Stored 吞 Reflected 类洞） | 中 | F3 配套折叠化（挂靠不吞卡）；sink 维 MVP 不归一 |
| F7-A 白名单污染（把注释/测试文件路由解析进来） | 中 | MVP 不做 A；B 的白名单来自 index.entry_points（GitNexus 确定性产，已过滤） |
| F11 涉 Temporal 窗口（容量铁律） | 中 | MVP 不做；做时须重估 `start_to_close_timeout` + 对数护栏 |
| 现有测试断言当前分组行为，F1-F4 改动致红 | 低 | 安排同批更新（Task 7 夹具回归先行，行为变更以新基线为准） |
| 幂等（verdict checkpoint 重试从头重跑） | 低 | 回填是纯函数无副作用；重跑重算，不重不漏 |

## 6. 铁律合规

- 本修复全在 GN 轨产物处理 + 合并层，**不把确定性产物喂 LLM 轨 prompt**（F6-B 的提名源是 verdict agent 自己的 title/evidence，非确定性产物；`test_static_dataflow_hints_decoupling.py` 锁定不变量不受影响）
- web 进程零 agent 执行点：全部改动在 `packages/core`（归一/回填纯函数）+ `packages/whitebox`（activity worker 侧）
- cost 字段语义、`cost_currency` 不变量不受影响

## 7. 实施范围

**改动文件**
- `packages/core/src/supernova_core/code_index/gn_collapse.py`（F1/F4/观测）
- `packages/core/src/supernova_core/code_index/dual_track_merger.py`（F2/F3/观测）
- `packages/core/src/supernova_core/code_index/endpoint_backfill.py`（**新建**，F6-B）
- `packages/core/src/supernova_core/services/track_parity.py`（F5）
- `packages/core/src/supernova_core/code_index/vuln_chain_builders/{xss,injection,ssrf}_builder.py`（F6a）
- `packages/whitebox/src/supernova_whitebox/pipeline/activities.py`（F6-B 接线）
- `packages/core/tests/code_index/{test_gn_collapse,test_dual_track_merger,test_track_parity,test_endpoint_backfill,test_dedup_regression_nodegoat}.py`（新建/修改）
- 新增 fixture：`packages/core/tests/code_index/fixtures/nodegoat_20260903/`

**不做**：F9、F11、F7-A、F6-B2（见 §3 不做项）。

**验收门**：Task 7 夹具回归全绿（分组 ≤7 / 交集 ≥1 / ground truth 全折叠无错合并）+ 改动相关单测全绿 + 现有 `test_gn_collapse.py`/`test_dual_track_merger.py`/`test_track_parity.py` 同批更新后全绿。
