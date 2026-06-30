# 设计:从 exploitation queue 确定性渲染完整端点清单附录

**日期:** 2026-06-30
**状态:** 待审查
**范围:** `apps/worker` 报告层(prompt + `services/`)

## 1. 背景

Shannon 的漏洞分析 agent 为每个漏洞类(authz / auth / injection / xss / ssrf / misconfig)产出一个 `*_exploitation_queue.json`(结构化、经 SDK schema 校验),作为该类漏洞的 ground truth。但最终交付的安全报告在呈现时会出现"端点级 finding 被折叠后不可见"的问题。

**实测证据(paper_trading_frontend whitebox workspace):**

- `authz_exploitation_queue.json` 含 **91 条独立 finding**(`AUTHZ-VULN-01 ~ 91`,全部 `externally_exploitable=true`),每条带独立 `endpoint` 与 `minimal_witness`。例如 `GET /asset-analysis/stock-pos-preference` = `AUTHZ-VULN-49`,witness 为 `?account_id=<victim>&target_uid=<victim>`。
- `authz_findings.md`(由 authz agent 经 MCP 工具生成)把 91 条**重新折叠、重新编号**成 10 条 `AUTHZ-VULN-01 ~ 10`。其 `AUTHZ-VULN-05`("约 124 个共享 handler 端点")的 `affected_routes` 只列了 6 个代表性端点,**不含任何 asset-analysis 端点**。
- 综合报告由 `reporting.ts` 拼接各 class 的 findings,再由 `report-executive` agent 加 Executive Summary 并清理。`report-executive` 不读 queue、不补端点,其清理规则还会删除 "Additional Analysis"、"counts" 等段落。
- 结果:`stock-pos-preference` 在综合报告与中文报告中完全不可见,尽管它在 queue 里有完整 finding 与精确 witness。

**根因:** ground truth(queue)始终完整,但 per-class `*_findings.md` 是 LLM 折叠产物,折叠群体的成员在摘要里丢失;报告链路中没有任何步骤把 queue 的完整端点清单确定性铺回报告。prompt 中已有"affected_routes 必须列全"的指令(`shared/_cross-route-enumeration.txt` CR-3),但它只约束 queue 层,未约束 findings / report 渲染层。

## 2. 目标与非目标

**目标**

- 每个 `*_exploitation_queue.json` 中 `externally_exploitable=true` 的端点,在最终报告(英文 + 中文)里有显式、可定位、带 `minimal_witness` 的完整列举,不受 LLM findings 折叠影响。
- 覆盖全部漏洞类,只要对应 queue 存在。
- 确定性:不依赖 LLM 自觉列全。

**非目标(YAGNI)**

- 不改检测能力、不改 vuln agent、不动 queue 生成逻辑。
- 不做 queue ↔ findings 的 per-finding 映射(见 §4 关键约束)。
- 不做正则解析 LLM markdown 的护栏。
- 不动 `exploit=true` 的 evidence 路径(附录对两种路径都适用,queue 在两种路径下都存在)。
- 不做共享 handler 二级分组(各 class schema 字段不一致,定位字符串归一化不可靠;class 级分组已满足诉求)。

## 3. 设计

### 3.1 组件

**(a) 新增 `apps/worker/src/services/affected-endpoints-appendix.ts`**

纯函数模块。输入:deliverables 目录路径;读取各 class 的 `*_exploitation_queue.json`;输出:一段 markdown 附录字符串。

- 仅收集 `externally_exploitable === true` 的条目。
- 按 class 分组;每条输出 `| Queue ID | Endpoint | Witness | Location |`。
  - Witness 取 `minimal_witness`,缺则留空并 warn。
  - Location 取该 class schema 对应的定位字段(authz / auth / ssrf / misconfig → `vulnerable_code_location`;injection → `sink_call`;xss → `sink_function`),缺则留空。
- 不要求与 `*_findings.md` 的 finding 划分对齐。

**(b) 扩展 `apps/worker/src/services/reporting.ts`**

新增 `injectAffectedEndpointsAppendix(...)`,在 `report-executive` agent **之后**执行(复用现有 `injectModelIntoReport` 的后置注入模式,确保不被 `report-executive` 当作 "Additional Analysis" 清理)。在报告末尾追加 "附录 A:完整可利用端点清单"(由组件 (a) 渲染);附录顶部附一张总览计数表(逐 class 列出 `externally_exploitable=true` 条目数,如 `authz: 91`),计数由代码从 queue 计算。

不向 report 正文的 class 段落注入计数:避免用正则定位会被 `report-executive` 改写的 class heading(脆弱)。计数集中在附录顶部,摘要侧靠 §3.5 的 prompt 引导 LLM 引用附录。

注入点须在中文翻译之前(英文报告)。中文报告的生成链路(整篇翻译 vs 独立生成)在实现阶段确认后决定是注入一份还是两份(见 §6)。

**(c) 纯 JSON 完整性断言**

附录生成后断言:附录条目数 == Σ(各 queue 中 `externally_exploitable=true` 条目数)。不符则 warn,不阻断。

### 3.2 数据流

```
*_exploitation_queue.json (ground truth, 经 schema 校验)
  → affected-endpoints-appendix.ts 确定性渲染
  → reporting.injectAffectedEndpointsAppendix (report-executive 之后)
  → comprehensive_security_assessment_report.md (摘要折叠保留 + 附录全量 + 附录顶部计数)
  → 现有中文翻译流程
```

### 3.3 错误处理

- queue 缺失或 JSON 解析失败:跳过该 class,warn,不阻断。
- 条目缺 `endpoint` 或 `minimal_witness`:跳过该条,warn。
- 附录注入失败:warn,主报告照常产出(附录是增强项)。
- 完整性断言不符:warn 差异计数,不阻断。

### 3.4 测试

- **单元(appendix 渲染)**:fixture queue → 快照断言附录含指定 endpoint 与 witness;覆盖多 class、`externally_exploitable=false` 过滤、空 queue、缺字段、各 class 的 Location 字段映射。
- **单元(计数)**:给定 queue → 附录顶部总览表中逐 class 计数正确。
- **集成**:用真实 `authz_exploitation_queue.json`(91 条)跑,断言 91 个 endpoint 全部出现、`stock-pos-preference` / `AUTHZ-VULN-49` 命中。
- **回归**:`report-executive` 清理之后注入(附录不被删、顺序正确)。

### 3.5 prompt 强化(轻量,不依赖)

在 `vuln-authz.txt`、`report-executive.txt` 加入如下指令(措辞以此为准):

> 若一个 finding 合并了多个共享同一越权模式的端点,必须在该 finding 中写明"本条为合并摘要,逐端点完整清单见附录 A"。禁止使用 `representative`、`等`、`例如`、`包括但不限于` 这类只列部分端点的措辞。**受影响端点的具体数量由附录给出,你不要在摘要里自行计数。**

此项为辅助;即使 LLM 不遵守,附录仍由代码确定性保证完整。

## 4. 关键约束与决策

- **queue 与 findings 无 ID 映射**:queue 用 `AUTHZ-VULN-01~91`,findings 用 `AUTHZ-VULN-01~10`,两套独立编号;findings 的 finding 边界是 LLM 产物,queue 无对应字段。因此 **per-finding 精确计数不可靠**,附录与计数均以 queue 自身为准(按 class 分组),不与 findings 的 finding 对齐。
- **计数归代码,LLM 不报数**:附录顶部总览计数由代码从 queue 计算;prompt 明确禁止 LLM 在摘要自行计数(它因无映射而会报错)。
- **后置注入**:附录必须在 `report-executive` 之后注入,否则会被其清理规则删除。
- **附录为增强项**:任何失败只 warn 不阻断主报告。

## 5. 验收标准

- 给定含 `externally_exploitable=true` 端点的 queue,最终报告附录里该端点 + witness 必现。
- 用 paper_trading 真实 authz queue 验证:`stock-pos-preference` 与其余 90 个 authz 端点在附录全部出现。
- 附录顶部总览计数与 queue 实际条目数一致。
- `report-executive` 清理后附录仍在。
- queue 缺失 / 损坏时报告仍正常产出。

## 6. 待实现阶段确认

- 中文报告生成链路(整篇翻译 vs 独立生成),决定附录注入一份(英文,翻译时带上)还是两份(英中分别注入)。
