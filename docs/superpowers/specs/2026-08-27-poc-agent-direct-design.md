# 白盒 PoC 去 templated 化——poc-agent 直产文本（design）

> 2026-08-27 立项。触发：用户验收 `workspaces/__legacy__/scans/app-20260827-062331`
> （纯前端 Vue monorepo）发现 XSS PoC 四层全错；归因证明错误源头不在判定 Agent
> 而在报告阶段的确定性拼装层。决策（用户四轮拍板）：**curl/Burp raw 文本由
> 报告阶段统一 poc-agent 直产，渲染层原文透传，确定性拼装全部退役，失败诚实
> 缺失不降级**。

## 1. 背景与问题诊断

### 1.1 实证案例（app-20260827-062331 · XSS-VULN-01）

产出 PoC：

```
curl -i -X POST 'http://TARGET/modification-application/application-review' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -H 'Authorization: Bearer <AUTH_TOKEN>' \
  --data 'auditTaskId=<img src=x onerror=alert(document.domain)>'
```

四层错误（用户验收 + 源码复核全证实）：

1. **目标不是 API 端点**：`/modification-application/application-review` 是
   vue-router SPA 前端路由（`router/routes/index.ts:68`），POST 只命中静态
   服务器 / SPA fallback。
2. **投递模型错误**：存储型 XSS 的触发是审核员浏览器渲染（`v-html="tip.value"`，
   `application-review/index.vue:118`），POST body + Bearer 既不进受害者请求，
   curl 也不执行 JS。
3. **参数位置错误**：`auditTaskId` 实为 detail 页从 `route.query` 读
   （`use-application-detail.ts:54`）且只回传 API 不进渲染。
4. **真 sink 的链没被 PoC 触及**：唯一 HTML 注入 sink 是 audit tip 的
   `v-html` 存储型链（plant 点在仓库外 `@futu/modify-profile-service`）。

### 1.2 根因：错误全部在「finding 字段 → HttpRequestSpec」语义提取层

LLM 轨 vuln agent 的原始 finding（`xss_llm_queue.json`）质量不低：正确识别
v-html sink、正确判定存储型、path 明确标注 `(SPA)`、endpoints 两项各带说明、
notes 诚实记录 plant 点在仓库外可控性未证实（confidence=medium）。

确定性拼装层（`poc_structured.py`）四步失真：

| 失真 | 代码行为 |
|---|---|
| 丢 `(SPA)` 标注 | `_extract_route` 正则只提 `METHOD /path`，前端路由当 HTTP 端点 |
| 跨消费点缝合 | `affected_parameters[0]` 的 `(body)` 注记（针对 getAuditTip API 调用）被缝到 SPA 页面路由上 |
| method 覆写 | `if body and method == "GET": method = "POST"` |
| 单请求压扁两步链 | 存储型 plant→trigger 被压成单个 HTTP 请求 |

对照 XSS-VULN-02（`GET /open-account/ndid-verify?displayName=...`，同样 SPA
路由当端点）证明是**系统性失配**：被扫仓库是纯前端 monorepo，白盒 XSS 的
source（API 响应字段 / 前端路由 query）与 sink（浏览器 DOM）都不是本仓库的
HTTP 入口参数；poc 拼装器的请求模型（method+path+placement+单 curl）为服务端
taint 设计。而「spec → curl/Burp 文本」渲染本身无损、从未出错——**语义判定
与文本生成分层拆除**是本 spec 的核心判断。

## 2. 目标与非目标

**目标**：
- 白盒 PoC（curl + Burp raw + 多步链说明）由报告阶段统一 poc-agent 直产文本，
  渲染层原文透传（零格式改写逻辑）；
- poc-agent 产出前强制回读源码验证端点形态 / 消费点 / 投递模型 / 认证形态
  （治四层错误的对症步骤）；
- 正确性自检主位、格式自检次位（用户明确要求：不能只关注格式而舍弃正确性）；
- 确定性拼装层（poc_generator 整模块 + poc_structured 拼装部分）退役；
- 失败诚实缺失（卡片标注「生成失败」），retry / only_ids 回炉补。

**非目标**：
- **黑盒不动**：黑盒 PoC 是 exploit agent 真机执行 curl 经 `_parse_curl_command`
  解析回 `PocRequest` 再确定性重渲染（`report_data_blackbox.py`）——重放证据的
  格式归一转录，改它会把证据变成转述。
- **判定轨不动**：vuln agent / chain_verdict 的 finding 字段契约不变（判定
  prompt 不膨胀，PoC 职责整体后移）。
- **存量 session 不兼容**：旧 `report_poc.request`（PocRequest 结构）不再产出；
  旧 session 报告已渲染落盘，重扫即新 schema（用户确认）。

## 3. 架构

```
双轨合并 queue（不变）
    ↓
write_structured_poc activity（时序不变：render_findings 之前；内部重写）
    ★ poc-agent（多轮，每 vuln_class 一会话，双引擎同跑）
      - 读该类 exploitation_queue 全部卡
      - 回读源码验证（§4.2 四项）
      - 逐卡产 curl + raw_http 文本 + 自检
    ↓
collector 校验（L2 id ∈ queue / L3 去重 / self_check 透传——不改写文本）
    ↓
queue 卡 report_poc = {curl, raw_http, steps, preconditions,
                       expected_response, self_check, notes}
    ↓
报告导出（findings_renderer / report_data_builder / md exporter）原文透传
```

关键角色边界：**poc-agent 是翻译者不是判定者**——verdict 不容重审（判定轨
职责），只把已判定的链路翻译成可复现请求形态。

## 4. poc-agent 设计

### 4.1 会话与调度

- `prompts/poc-agent.txt`（新）；每 vuln_class 一会话，复用
  `{vc}_exploitation_queue.json` 发现逻辑（`_QUEUE_FILES`）；
- 双引擎一致：对齐 vuln agent 调度方式（claude-agent-sdk / openai-agents 同跑
  同一份 prompt）；model_tier 对齐 vuln agent 现行档位；
- 模板注入：`{{WEB_URL}}`（缺省 `http://TARGET`）、`{{DELIVERABLES_PATH}}`、
  queue 路径。

### 4.2 产出前强制源码验证（prompt 硬性步骤，治四层错误）

1. **端点形态**：目标路径是后端 API 还是前端路由？读 router 定义 / 后端路由
   注册验证；**SPA 路由不得作为 HTTP 投递点**；
2. **消费点**：参数被哪个端点、以什么位置（query/body/path）消费——从代码
   读，**不信 finding 注记**（实证：`auditTaskId (body)` 注记就是错的）；
3. **投递模型**：按漏洞类型匹配——反射型 = 受害者请求触发；存储型 =
   **plant + trigger 两步**（plant 点在仓库外时诚实标注可控性未证实）；
   DOM/浏览器渲染 = 导航形态而非 curl；
4. **认证形态**：查代码 / 配置确认 Bearer / Cookie / 自定义 header（不默认
   Bearer）。

### 4.3 正确性自检协议（主位）+ 格式自检（次位）

产出前逐项自证，verdict 带 `self_check` 字段：

- **正确性（主）**：curl 目标端点真实存在且消费该参数（file:line 证据）；
  投递模型与漏洞触发方式一致；sink 在该请求的触发路径上；
- **格式（次）**：shell 引号平衡；无模板占位符残留（`witness_payload` /
  `${}` / `{{}}`）；敏感值用 `<AUTH_TOKEN>` 类占位符；curl 与 raw_http 内容
  一致；
- **宁缺毋错**：自检不通过 → 修正后重产，或降级该卡为「无法产出正确 PoC」+
  原因（对齐 §6 诚实缺失哲学）。

### 4.4 输出契约：add_poc collector

append collector（对齐 `add_exploit` / `collectors/exploit.py` 模式）：

- `vulnerability_id`（必填；L2 校验 ∈ queue 防幻觉，L3 去重）；
- `curl`（完整可复现 curl，含占位符认证上下文）；
- `raw_http`（Burp Repeater 原始报文，与 curl 同一请求）；
- `steps`（多步链有序说明：plant / trigger / 导航）；
- `preconditions`（认证 / 角色 / plant 前提，含「plant 点在仓库外，可控性
  未证实」类诚实标注）；
- `expected_response`（顺产——退役现行独立 small-tier 调用）；
- `self_check`：`pass | fail` + 说明（fail 时写回但卡片带 ⚠）；
- `notes`。

## 5. 数据流与 report_poc schema

```python
# queue_schemas.py（字段签名不变，值形态变更）
report_poc: dict | None
# 值: {curl, raw_http, steps, preconditions, expected_response, self_check, notes}
```

- 写回：`apply_structured_poc` 薄写回保留（或等价内联）；
- 时序：`write_structured_poc` 在 `render_findings` 之前（现状不变）；
- `only_ids` 回炉保留：重跑只补指定卡、不覆写已有 report_poc；
- 消费端：`findings_renderer.py`（POC 独立节）、`report_data_builder.py`
  （前端数据）、`report_markdown_exporter.py` 改读文本字段透传；概览表
  method/path 列取 finding 的 path 字段（非 PoC）；
- 前端 `types.ts` / `VulnerabilityCard.tsx` 同步新字段。

## 6. 错误处理

- poc-agent 会话失败 / 超时 / LLM 不可用（stub）→ 该类 queue 卡 PoC 全缺；
  activity 吞异常不阻塞主报告（对齐现行 non-fatal 哲学）+ warning 记账。
  呈现口径（二选一，plan 定）：activity 写回降级占位
  `{self_check: "fail", notes: "agent 不可用"}`（渲染层有标注），或卡片
  POC 节按现行「无 report_poc → 整节省略」自然缺失 + events 记 warning；
- collector 校验拒绝（id ∉ queue / 重复）→ rejected 记账不写回（对齐
  `validate_exploit_verdicts` L2/L3 模式）；
- `self_check: fail` → 写回 + 卡片 ⚠ 标注（agent 自报不通过好过静默错）；
- 断点续传：写回 queue 即天然 checkpoint（`.poc_checkpoint.json` 机制随
  poc_generator 退役）；retry / 回炉走 `only_ids`。

## 7. 退役清单

| 退役项 | 说明 |
|---|---|
| `poc_generator.py` 整模块（~1750 行） | `PoCGenerator` / `HttpRequestSpec` / `to_curl` / `to_burp_raw` / `_build_request` 语义提取 / `RouteIndex` / gap-fill / `auth_header` / `lint_spec` / checkpoint 全下。外部消费者仅 `poc_structured.py:31` 工具 import + 已退役 activity stub |
| `poc_structured.py` 确定性拼装 | `_build_request` / `_resolve_placement` / `_extract_route` / `render_curl` / `render_raw_http` / `_auth_placeholder_header` / `_content_type_for_body` / `derive_preconditions` / `build_expected_response_prompt` 退役；保留 `apply_structured_poc` 薄写回。模块缩成 <100 行写回+校验层或删空 |
| `whitebox/activities.py::generate_poc_report` | 已是退役 stub（2026-08-26），连函数体带 `PoCGenerator` import 清掉 |
| `write_structured_poc` 内部实现 | `build_structured_poc` 确定性拼装 + expected_response 独立 small-tier 调用 → 重写为 poc-agent 会话 |
| 测试退役 | `test_poc_generator.py` / `test_poc_structured.py` / `test_poc_overhaul*.py` / `test_poc_generator_stage1.py` 随模块退役；`test_queue_schemas.py` report_poc 断言、prompts 契约测试中 poc 拼装断言改写 |

**不退役**：`report_data_blackbox.py` 全套（黑盒证据转录）；report_poc 消费
逻辑（改字段不改机制）。

## 8. 测试策略（TDD，先红后绿）

1. **collector 单测**：`add_poc` append 语义、L2 id 校验、L3 去重、
   self_check 透传不改写；
2. **契约测试**（锁 prompt 不变量，对齐 `test_static_dataflow_hints_decoupling`
   模式）：`prompts/poc-agent.txt` 必含——端点形态验证步骤（SPA ≠ HTTP 投递
   点）、投递模型按漏洞类型匹配（存储型两步）、正确性自检主位 / 格式自检次位、
   宁缺毋错降级路径；
3. **渲染侧**：`findings_renderer` / `report_data_builder` / md exporter 对
   `{curl, raw_http, steps}` 文本透传；失败卡 POC 节标注；self_check fail 的 ⚠；
4. **workflow 接线**：mock agent 会话 → 写回 → only_ids 回炉不覆写；
5. **验收用例（真机）**：`app-20260827-062331` 的 queue 重跑 poc-agent——
   XSS-VULN-01 应产出「plant(仓库外) + 浏览器导航 trigger」或诚实降级，
   **不得**再产出 `POST SPA-路由 --data auditTaskId=`（本案例作回归锚点）。

## 9. 实施顺序概要（writing-plans 细化）

1. `add_poc` collector + 校验层（TDD）；
2. `prompts/poc-agent.txt` + 契约测试；
3. `write_structured_poc` 重写接线（mock 测试）；
4. report_poc schema / 渲染三消费端 + 前端字段；
5. 退役 poc_generator / poc_structured 拼装 / 旧测试；
6. 真机验收（app-20260827-062331 回归锚点）。
