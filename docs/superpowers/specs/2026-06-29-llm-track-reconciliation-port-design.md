# LLM 轨移植 TS 的两个对账机制（enumeration-completeness + coverage-reconciliation）

> 日期：2026-06-29　分支：`feat/fork-py`
>
> **背景**：原始 TS 项目（`/root/shannon`，100% 纯 LLM 单轨）为让白盒审计"更全"（recon 更全 / sink·source 更全 / API 更全 / 参数更全），核心手段是两个**机械化对账机制（reconciliation）**——把覆盖完整性从依赖 LLM 偶然的彻底性，变成 prompt 强制的集合对账。重构 PY 双轨下，LLM 轨的"清单层"（sink/source/API/参数）已基本对齐 TS、部分更优（PY `vuln-injection.txt` 的 slot 类型体系 `val/like/num/enum/ident` 比 TS 更系统），但**这两个对账机制未移植**——TS `prompts/shared/_enumeration-completeness.txt` 与 `_coverage-reconciliation.txt` 在 PY `prompts/shared/` 无对应文件。这是 LLM 轨当前 false-negative 的主要来源（recon 漏掉整条路由家族时无人对账 → 下游 authz/injection 永远看不到；authz 的非 object-id 向量如 tenant/region selector 无人覆盖清零）。
>
> **范围决策**：用户选"全面对齐 TS 所有更全措施"，但核实发现实际 gap 收敛——dynamic identifiers PY 已有且更强（`vuln-injection.txt:151` `slot=ident`）、shared-handler/hidden-param/sink 清单已对齐，真正缺的就是这两个对账机制 + recon 补 2 个枚举角度，**单 spec 可 cover**。
>
> **落地决策**：方案 A——纯 prompt 对齐 TS（零代码改动），完全守 CLAUDE.md §1 铁律（LLM 轨不吃确定性产物；对账是 LLM 自给 grep / 自给构建，anchor count 不取自 GitNexus）。

---

## 1. 目标 / 非目标

### 目标

- **G1（recon 枚举对账）**：移植 TS `_enumeration-completeness.txt` 到 PY，recon 阶段产出 `§4.3 Enumeration Reconciliation` 表格，pre-termination 自检零 `true-miss`，堵"整条路由家族被默默跳过"。
- **G2（recon 枚举角度补齐）**：recon 的 Route Mapper Agent 补 **frontend-call 反向推断** + **gateway 层**两个枚举角度（TS Angle 4/5）。
- **G3（authz 覆盖对账）**：移植 TS `_coverage-reconciliation.txt` 到 PY，authz 阶段构建 F/C/G 集合，`G = F \ C` 逐个出 verdict，pre-termination 自检 `G = ∅`，堵"非 object-id 向量（tenant/region selector）被跳过"。

### 非目标（明确排除）

- **不引入代码层产物校验**（方案 B 排除）：对账失败靠 prompt 层硬阻（"do NOT announce COMPLETE"），不在 harness / `conclusion_trigger` 加代码 block。
- **不做双轨交叉验证**（方案 C 排除）：不把 LLM 轨对账结果与 GitNexus 确定性端点在合并器交叉验证——属双轨架构演进，另立 spec。
- **不喂确定性产物给 LLM 轨**（CLAUDE.md §1 铁律）：anchor count 是 LLM 自给 grep 数的，F/C/G 是 LLM 自给构建的，**不取自 `parameter_graph` / `SinkCallSite`**。即使 GitNexus 已有确定性端点清单，也不反向喂 LLM 轨。
- **不动已对齐项**：dynamic identifiers（PY `slot=ident` 已有且更强）、shared-handler 组（PY `_cross-route-enumeration.txt` 已有）、hidden parameters（PY recon §5 已有）、sink/source 按语言分层（PY `vuln-*.txt` 已有）——本次不碰。
- **不扩到 GitNexus 轨**：本次只动 LLM 轨 prompt；GitNexus 轨自身的对账（如有）不在范围。

---

## 2. 现状证据

### 2.1 TS 有、PY 无（两个对账 partial）

| 机制 | TS 位置 | 内容 | PY 现状 |
|---|---|---|---|
| 枚举对账 | `apps/worker/prompts/shared/_enumeration-completeness.txt:1-63` | EC-A 5 角度(:11-23) / EC-B anchor-count 对账表(:25-33) / EC-C prefix-family gap(:35-39) / EC-D shared-handler(:41-45) / EC-E param completeness(:47-51) / EC-F source/sink handoff(:53-58) / self-check(:60-62) | ❌ `prompts/shared/` 无此文件 |
| 覆盖对账 | `apps/worker/prompts/shared/_coverage-reconciliation.txt:1-68` | CR-A USER 端点全集 F(:10-15) / CR-B 已判集 C(:17-23) / CR-C `G=F\C` 逐个 verdict(:25-35) / CR-D 数据所有权判向量(:37-55) / CR-E 每端点粒度(:57-64) / self-check(:66-67) | ❌ `vuln-authz.txt:314-317` 仅 `<coverage_requirements>` 一句 "Test all endpoints from recon section 8"，无集合对账、无向量分类 |

### 2.2 PY 已对齐 / 更优（本次不动）

| 项 | PY 证据 |
|---|---|
| dynamic identifiers | `vuln-injection.txt:151` `slot=ident`（表名/列名/ORDER BY/GROUP BY）；:154 slot 标签体系 `val/like/num/enum/ident`；:156 sanitization 按 slot context 匹配——比 TS 更系统 |
| shared-handler 组 + pre-auth | `prompts/shared/_cross-route-enumeration.txt:1-58`（Step CR-1~CR-4，`affected_routes`/`authentication_required` 字段） |
| hidden parameters | `recon.txt:307-317` §5 Parameter Completeness Verification（template 变量交叉引用） |
| sink/source 按语言分层 | `vuln-injection.txt:145-153` / `vuln-xss.txt` render context / `vuln-ssrf.txt` HTTP client + URL param |

### 2.3 recon 枚举角度 gap

PY `recon.txt:140-157` systematic_approach 步骤 3 是 4 个职能 agent（Route Mapper:143 / Authorization Checker:144 / Input Validator:145 / Session Handler:146）+ 3.5 Authorization Architecture(:148-150)。Route Mapper 当前覆盖 route-def + controller-method + interface-contract 三角度，**缺 TS Angle 4 frontend-call 反向推断** 与 **Angle 5 gateway（nginx `location`/`proxy_pass`、ingress）**。

---

## 3. 设计（4 个文件）

### 3.1 新增 `prompts/shared/_enumeration-completeness.txt`

移植 TS `_enumeration-completeness.txt`，**裁剪聚焦 EC-A/B/C/F**（EC-D/E 交予 PY 已有机制，不重复）：

- **EC-A（5 角度覆盖）**：适配为"Route Mapper Agent 须覆盖 5 个枚举角度并返回每角度 anchor count"（TS 原文是"启动 5 个独立 Task agent"；PY 用单 Route Mapper 覆盖，见 §6.2）。5 角度：route-definition / controller-method / interface-contract / **frontend-call** / **gateway**。
- **EC-B（anchor-count 对账表）**：产出 `### 4.3 Enumeration Reconciliation` 表格（Angle / Anchor Count / Reported (deduped) / Delta / Explanation），每个非零 delta 分类 `dedup` / `out-of-scope` / `true-miss`；`true-miss` 必须回补 §4。
- **EC-C（prefix-family gap）**：扫 §4 的 path-prefix 家族（如 `/asset-analysis/*`、`/account/*`），代码里 N 成员但 §4 少于 N → 每个缺员是 `true-miss`。
- **EC-F（source/sink handoff）**：§9 injection sources 已枚举；确认端点参数无 dropped，injection-side sources 完整交给下游 injection agent。
- **省略 EC-D**（shared-handler groups complete）：交予 PY `_cross-route-enumeration.txt` + recon §4.1，partial 里加一行交叉引用。
- **省略 EC-E**（parameter completeness）：交予 PY recon §5，partial 里加一行交叉引用。
- **self-check**：`### 4.3` 表格在场、每个 delta 已分类、零 `true-miss` → 否则 **do NOT announce `RECONNAISSANCE COMPLETE`**。

`@include` 位置：`recon.txt` 的 `<conclusion_trigger>`（:470）**之前**；并在 `<deliverable_instructions>`（:159）的 deliverable structure 里加 §4.3 表格要求。

### 3.2 新增 `prompts/shared/_coverage-reconciliation.txt`

移植 TS `_coverage-reconciliation.txt` CR-A~E 全套，**适配 PY 产物结构**（C 集合来源）：

- **CR-A（USER 端点全集 F）**：从 recon §4 收集 Required Role=`user` 的端点，记 `METHOD /path`，含 Client-Controlled Parameters 为 `None` 或仅非显然 selector 的端点。
- **CR-B（已判集 C）**：= exploitation queue 的 `endpoint`（vulnerable verdict）+ **§四「已分析并确认安全的向量」**（`vuln-authz.txt:369`）的 endpoint（safe verdict）。【适配点：TS 用 `set_safe_vectors` 输出字段，PY 用 §四 safe section——用 PY 的，不新增字段】
- **CR-C（`G = F \ C` 逐个 verdict）**：G 中每个端点必须出 verdict（vulnerable → queue / safe → §四），**非空 G = INCOMPLETE**。
- **CR-D（数据所有权判向量）**：向量测试不是"有没有 client-controllable 参数"，而是"改参数值是否 return/mutate 非调用者数据且无 sufficient ownership/tenant guard"。非向量：pagination（`page`/`per_page`/`offset`/`limit`）、sort（`order_by`/`sort`）、own-record filter（`type`/`status`/`date_start`）、locale-ui（`lang`/`site`/`channel`）。向量：object id（`coupon_id`/`account_id`/`order_id`/`id`）+ tenant/region/identity selector（`brokerage`/`market`/`region`/`tenant_id`/`org_id`）当 server 无界转发调用者身份时。
- **CR-E（每端点粒度）**：禁止 "any /api/* with brokerage" 全局合并；N 个端点受同一 selector 影响 → N 个独立 finding，各有 `endpoint`/`vulnerable_code_location`/`minimal_witness`。
- **self-check**：`G = ∅`、每个 USER 端点在 queue 或 §四 → 否则 **do NOT announce `AUTHORIZATION ANALYSIS COMPLETE`**。

`@include` 位置：`vuln-authz.txt` 替换现有 `<coverage_requirements>`（:314-317），放 `<conclusion_trigger>`（:392）之前。

### 3.3 改 `prompts/recon.txt`

- **systematic_approach 步骤 3 Route Mapper Agent（:143）**：指令补 2 个枚举角度——
  - frontend-call 反向推断：搜前端 `axios`/`fetch`/`rpc` 调用，反向推断路由文件未声明的后端端点。
  - gateway 层：扫 nginx `location`/`proxy_pass`、ingress routes、网关配置里的额外端点。
  - 每角度返回 anchor count（grep pattern + file + count）。
- **deliverable structure（`<deliverable_instructions>` :159）**：在 §4.2（:276 Endpoint Security Context）之后、§5（:297）之前，新增 **§4.3 Enumeration Reconciliation** 表格结构要求。
- **`@include(shared/_enumeration-completeness.txt)`**：放 `<conclusion_trigger>`（:470）之前。

### 3.4 改 `prompts/vuln-authz.txt`

- **替换 `<coverage_requirements>`（:314-317）** 为 `@include(shared/_coverage-reconciliation.txt)`。
- 放 `<conclusion_trigger>`（:392）之前。

---

## 4. 数据流

### 4.1 recon（枚举对账）
```
LLM 自给 grep（5 角度，每角度返回 anchor count + pattern + file）
  → 合并去重进 §4 端点全集
  → 产出 §4.3 Enumeration Reconciliation 表格（Angle/Anchor/Reported/Delta/分类）
  → 每个 true-miss 回补 §4（及 §4.1 若共享 handler）
  → EC self-check：表格在场 + delta 全分类 + 零 true-miss
  → 通过 → announce RECONNAISSANCE COMPLETE
  → 下游 vuln agents 消费已完整的 §4
```

### 4.2 authz（覆盖对账）
```
LLM 自给从 §4 构建 USER 端点全集 F
  → 收集已判集 C（queue endpoint + §四 safe endpoint）
  → G = F \ C
  → G 中每个端点逐个 verdict（vulnerable→queue / safe→§四）
  → CR self-check：G = ∅
  → 通过 → announce AUTHORIZATION ANALYSIS COMPLETE
```

---

## 5. 错误处理（prompt 层硬阻）

- 两 partial 的 self-check 失败 → 指令明确 "do NOT announce COMPLETE"（recon: `RECONNAISSANCE COMPLETE`；authz: `AUTHORIZATION ANALYSIS COMPLETE`）。
- 这是 **TS 式 prompt 层硬阻**；方案 A 不引入代码层 block（不加 harness 校验、不加 `conclusion_trigger` 代码校验）。LLM 若仍偷懒宣布 COMPLETE，属 LLM 合规风险，留给方案 B（后续）兜底，本次不解决。
- **不新增异常 / 重试**：对账是 LLM 自给推理，无 I/O、无超时风险。

---

## 6. 关键设计决策

### 6.1 section 编号：新增 §4.3，不动 §4.1/§4.2
PY `recon.txt` §4.2 现为 Endpoint Security Context（:276）。新增 §4.3 Enumeration Reconciliation 插在 §4.2 之后、§5（:297）之前。**理由**：最小侵入，不破坏现有 deliverable 结构和下游消费（下游读 §4 端点全集、§4.1 shared-handler、§4.2 security context；§4.3 是 pre-termination 对账，位置语义正确）。

### 6.2 枚举角度补法：扩展现有 Route Mapper，不新增 agent
TS 用 5 个独立 Task agent 枚举；PY 现有 Route Mapper Agent（`recon.txt:143`）已覆盖 3 角度。决策：**扩展 Route Mapper 覆盖全部 5 角度**（补 frontend-call + gateway），不新增独立 Enumeration Agent。**理由**：保持 PY 现有 4+1 agent 结构，最小侵入；EC-A 表述从"启动 5 个独立 agent"适配为"Route Mapper 须覆盖 5 角度并返回每角度 anchor count"。

### 6.3 enumeration-completeness 裁剪：EC-D/E 交予已有机制
TS EC-D（shared-handler）由 PY `_cross-route-enumeration.txt` + recon §4.1 承担；EC-E（param completeness）由 PY recon §5 承担。partial 聚焦 EC-A/B/C/F，EC-D/E 处加交叉引用行（"见 `_cross-route-enumeration.txt` / §5"），不重复逻辑。**理由**：避免双份指令冲突，复用 PY 已验证机制。

### 6.4 coverage C 集合：用 PY §四 safe section，不引入 TS `set_safe_vectors`
TS CR-B 用 `set_safe_vectors` 输出字段；PY `vuln-authz.txt` safe verdict 记在 §四「已分析并确认安全的向量」（:369）。决策：C = queue endpoint + §四 endpoint，**不新增 `set_safe_vectors` 字段**。**理由**：对齐 PY 现有产物结构，零 schema 改动。

### 6.5 铁律边界：对账纯 LLM 自给，不取 GitNexus
TS 单轨下 anchor count 是 LLM grep 数的。PY 双轨下，LLM 轨对账同样自给——**不取 `parameter_graph` / `SinkCallSite` 的确定性端点数**。即使 GitNexus 有更准的端点清单，也不反向喂 LLM 轨（守 CLAUDE.md §1）。双轨 OR 由合并器在 verdict 层兜底，不在 LLM 轨 prompt 内耦合。**理由**：保 LLM 轨独立性（GitNexus 超时 / 不可用时 LLM 轨仍能对账）。

---

## 7. 铁律与测试

### 7.1 铁律（CLAUDE.md §1）
- 两 partial 纯方法论：anchor count（LLM grep）、F/C/G（LLM 构建），**零确定性占位符**（不出现 `FORBIDDEN_PLACEHOLDERS` 任一：`PRE_RECON_GITNEXUS_TRACK`/`RECON_GITNEXUS_TRACK`/`FRAMEWORK_ENDPOINTS_SUMMARY`/`TAINT_FLOW_SUMMARY`/`CHAIN_AUDIT_INPUT`/…）、**不 include 确定性产物**（不 include `_static-dataflow-hints.txt`、不引用 `code_index.json` / `parameter_graph`）。
- 仅用合法用户配置占位符（`{{DELIVERABLES_PATH}}` 等，对齐 `_cross-route-enumeration.txt:11` 现有用法）。

### 7.2 测试
- **现有解耦测试自动覆盖**（无需改）：`test_static_dataflow_hints_decoupling.py` 的 `test_no_prompt_includes_static_dataflow_hints`（:28-37，rglob :31）+ `test_no_llm_track_prompt_has_forbidden_placeholders`（:64-74，rglob :66）扫所有 prompt txt，新 partial 不写 forbidden token 即自动合规。
- **新增 prompt 内容断言**（对齐 `test_vuln_injection_prompt.py` 风格，新文件 `packages/core/tests/prompts/test_reconciliation_partials.py`）：
  - `recon.txt` 含 `@include(shared/_enumeration-completeness.txt)`；
  - `vuln-authz.txt` 含 `@include(shared/_coverage-reconciliation.txt)`；
  - `recon.txt` deliverable structure 含 `### 4.3 Enumeration Reconciliation`；
  - `recon.txt` Route Mapper Agent 指令含 frontend-call + gateway 两角度关键词；
  - 两 partial 文件存在且非空。

---

## 8. 验收标准

- recon 真机跑后，`recon_deliverable.md` 含 §4.3 Enumeration Reconciliation 表格，且零 `true-miss`（或所有 `true-miss` 已回补）。
- authz 真机跑后，`G = ∅`（每个 USER 端点在 queue 或 §四）。
- 解耦测试全绿（现有 + 新增），新 partial 不触发 forbidden。
- 双引擎（claude-agent-sdk / openai-agents）跑同一份改后 prompt，行为一致（对账指令是 prompt 层，引擎无关）。

---

## 9. 任务分解提示（给 writing-plans）

1. 新增 `prompts/shared/_enumeration-completeness.txt`（裁剪 EC-A/B/C/F + EC-D/E 交叉引用 + self-check）。
2. 新增 `prompts/shared/_coverage-reconciliation.txt`（CR-A~E，C 集合用 §四）。
3. 改 `prompts/recon.txt`（Route Mapper 补 2 角度 + §4.3 表格结构 + `@include`）。
4. 改 `prompts/vuln-authz.txt`（替换 `<coverage_requirements>` + `@include`）。
5. 新增 `packages/core/tests/prompts/test_reconciliation_partials.py`（5 条断言）。
6. 跑解耦测试 + 新测试，确认绿。
7. 真机冒烟（待人工）——recon 看 §4.3 表格、authz 看 `G = ∅`。
