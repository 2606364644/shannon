# 白盒报告单漏洞信息基准（canonical）

> 2026-08-26 用户口径记录。定义**理想白盒报告中单个漏洞应具备的信息结构**，
> 作为报告生成与渲染的长期产品基准——生成侧（md 卡 / report_data.json）与
> web 渲染侧（结构化卡）同构同序，都以本文为准。
>
> 实现落点：`docs/superpowers/specs/2026-08-26-vuln-card-seven-sections-design.md`
> （七节结构 design，含数据源与空数据兜底链）；前置归并口径见
> `docs/superpowers/specs/2026-08-26-vuln-card-consolidation-design.md`。

## 1. 七节结构

单个漏洞卡（漏洞条目）按以下七节组织，节序固定：

| # | 节 | 内容 | 用户口径注解 |
|---|---|---|---|
| 1 | 漏洞成因 | 研判依据——为什么判定这里是漏洞（source→sink 判定逻辑、证据链） | |
| 2 | 漏洞危害 | 该漏洞可被利用后造成的实际危害 | |
| 3 | 问题点 | **三要素：位置 / 说明 / 代码片段**——定位到 file:line，一句话说明此处为何危险，附 repo 里真实读到的源码片段（禁止杜撰） | |
| 4 | 相关接口 | 该漏洞涉及入口/接口（方法、路径、参数、认证等） | **格式自由**：不要为了表格把内容整乱；表格不好生成/不好读就换格式（紧凑块等） |
| 5 | POC | **curl 命令 + Burp 格式（raw HTTP）双格式**，可直接复制使用 | |
| 6 | 漏洞细节 | 数据流 / 防护措施 / CVSS / OWASP 分类 | |
| 7 | 修复建议 | 针对性修复方案 | |

## 2. 格式原则

- **内容优先于形式**：表格只是可选呈现形式，不是约束。任何节的渲染格式
  以可读性为准（相关接口一节已据此弃 7 列大表改紧凑块）。
- **禁止杜撰**：位置、代码片段等必须来自真实读取的 repo 内容，宁缺毋滥；
  与行号校验同一立场。
- **空数据整节省略**：某节无真实数据时省略整节，不出空壳节。
- **生成与渲染同构**：七节是报告生成本身的结构（md 卡与 report_data 原生
  如此），web 1:1 呈现，不是前端把另一种结构掰弯。

## 3. 现状矩阵（2026-08-26，七节落地后）

白盒的七节基准**已是现状**（commit `1c27ec13`，md 卡 + report_data + web
三路同构，TDD 150+93 绿，NodeGoat 真实数据目检通过）。三轨全景：

| 轨 | md 卡 | web 卡（VulnerabilityCard 七节） |
|---|---|---|
| 白盒 | ✅ 七节（`findings_renderer.render_vuln_card`） | ✅ 七节（problem_points 经兜底链） |
| 黑盒 | ⚠️ **非七节**——evidence 视角：`renderers/exploit.py` 按验证状态分组（exploited / blocked / potential / unverified），每条 `### ID: title` + 复现步骤 + fenced 命令 | ✅ 共用卡自动七节（见下降级） |
| 融合 | ✅ 七节（`report_markdown_exporter.export_report_markdown` 全卡走 `render_vuln_card`，**含黑盒卡**） | ✅ 七节（fused report_data） |

黑盒卡七节降级明细（web / 融合路径，符合"黑盒无源码、以实测为主"语义，
非缺陷）：

- **问题点**：黑盒不走 endpoint_enrichment → `problem_points` 恒空；
  `evidence.code_snippet` 亦空 → 兜底链尽头，节只剩位置行或整节省略。
- **漏洞成因 / 修复建议**：黑盒 `report_data` 的 narrative 只产 impact
  （exploited→impact，未利用→expected_impact），无 cause / remediation
  → 两节空省。
- **POC**：`poc.request` = 实际发出的请求、`expected_response` = 实测观察
  （比白盒更"实"）；`dynamic_evidence`（实测输出）在 POC/细节节间证据位
  突出显示。
- 黑盒 md（`comprehensive_security_assessment_report.md`）由
  `report_assembler` 拼 per-class deliverables 三级回退
  （`{vt}_exploitation_evidence.md` → `_findings.md` →
  `_analysis_deliverable.md`）+ report-executive agent 重写 +
  `verify_report_vuln_blocks` 自愈，不经 `render_vuln_card`。

兼容与防线：

- **旧扫描**（七节落地前的 report_data.json / md）：无 `problem_points` →
  前端兜底链生效；旧 md 走 legacy web md 渲染路径，行为不变。
- **report-executive 改写回归**（历史有先例）：`verify_report_vuln_blocks`
  重建底稿版自动恢复七节；验收时目检。

## 4. 边界

- **接口数与拆卡**：通常一个漏洞对应一个接口；多接口一般是不同漏洞
  （存储型 XSS 写+触发成对等合理场景除外）。拆卡口径另立 spec。
- **黑盒轨**：无源码，天然无代码片段——问题点节降级只剩位置行或省略，
  符合语义，非缺陷（见 §3 降级明细）。
