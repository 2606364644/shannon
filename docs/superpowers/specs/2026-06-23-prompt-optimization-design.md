# Prompt 优化设计(对比原始 shannon 的退化修复)

- **日期:** 2026-06-23
- **状态:** 待实现(brainstorming 产出,待 writing-plans 细化)
- **分支:** feat/fork-py

## 1. 背景与评估结论

重构项目 `shannon-py`(Python)从原始项目 `shannon`(TypeScript)迁移而来,14+ 个安全审计 agent 的 prompt 文本几乎一一对应。本次对两个项目 `prompts/` 目录(顶层 + `shared/` 两层,`pipeline-testing/` 忽略)做了逐文件内容对比 + 代码查证,回答「重构项目的 prompt 需要优化吗」。

**整体结论:重构侧 prompt 质量总体持平或增强,真正需要动的只有 3 处。**

### 1.1 净增强项(无需改动,仅记录)

- 所有 `vuln-*` 分析 agent 补齐 `accessible_routes` / `authentication_required` 字段(原始 injection/xss 缺失),跨路由感知更强
- 新增 `_static-dataflow-hints.txt` + pre-recon 的 Phase 0 AST code index(确定性分析线索)
- 新增 `_output-language.txt`(统一中文输出)、`_exploit-methodology.txt`(共享方法论)
- 三个全新 agent:`audit-tier1`、`recon-blackbox`、`cross-repo-correlation`
- 中性适配:路径/CLI/工具 Python 化(`save-deliverable`→Write、`playwright-cli`→`{{BROWSER_COMMANDS}}`)、报告标题中文化

### 1.2 已澄清的「非退化」(查证排除,不在本次范围)

- **`_shared-knowledge.txt` / `{{SHARED_KNOWLEDGE}}` 缺失** →【有意设计】原始的累积注入被重构的「文件路径约定 + 确定性产物」(`framework_analysis.json` / `frontend_mapping.json` / `route_chains.json` 等)替代,五个维度的数据全覆盖,信息量不减。
- **`recon-static.txt` 缩减 82%** →【有意简化 + 半成品】grep 全仓零引用,不在 `AGENTS` 字典(17 个 agent 无 `RECON_STATIC`)。其 10 个 section 由 `pre-recon-code` + `recon` + 确定性 AST 层覆盖。但它是为「离线/纯静态模式」设计的(`git` commit `e7b0555` 有意添加,S2/S8),而该模式在重构里**未接通** —— 见第 4 节 B。

## 2. 范围

本次仅修复 3 处:

| 编号 | 类别 | 问题 | 方案 |
|---|---|---|---|
| **A** | prompt 内容 | `vuln-authz.txt` 丢失 finale-rest/epilogue 框架端点 IDOR 方法论 | A1:补回正文 |
| **B** | 功能补全 | `recon-static.txt`(离线/静态模式)未接通 | B1:promptOverride 机制接通 + 补内容 |
| **C** | 健壮性 | `prompt_manager._interpolate` 不检测残留占位符 | C1:log.warning(对齐原始 TS) |

### 2.1 非目标(YAGNI,明确不做)

- **不抽 `_framework-endpoint-guidance.txt` shared 片段** —— 当前只有 authz 一个消费者,直接补正文(对齐原始、避免过度抽象)。
- **不恢复 misconfig 漏洞类** —— 重构有意裁剪(5 类 vs 原始 6 类),见 `docs/gap/2026-06-21-vuln-agent-gap-analysis.md`。
- **不把框架端点指引挪到 `vuln-auth`** —— IDOR 是授权(authorization)漏洞,归 authz。`vuln-auth`=认证(broken auth/session/credential),`vuln-authz`=授权(IDOR/horizontal/vertical/privilege escalation)。framework endpoint guidance 关注 ownership validation,属授权范畴。

## 3. 设计 — A:补回 vuln-authz 框架端点 IDOR 方法论

### 3.1 目标

恢复 authz agent 对 ORM-to-REST 框架(finale-rest / epilogue)自动生成端点的 IDOR 检测方法论。这类端点默认**缺 ownership check**,是 IDOR 重灾区(典型:`DELETE /api/Feedbacks/:id`)。

### 3.2 证据(原始有,重构无)

原始 `shannon/apps/worker/prompts/vuln-authz.txt`(415 行)有两个**硬编码正文块**(不是靠 `{{SHARED_KNOWLEDGE}}` 注入):

- **行 131 `### 0) Read Endpoint Security Context (REQUIRED — Do This First)`** —— 强制 authz 先读 recon deliverable 的 Section 4.2,关注 Framework origin(manual vs auto-generated)。
- **行 187 `**Framework Endpoint Guidance:**`** —— finale-rest/epilogue 自动端点方法论:ORM-to-REST 生成、`create.end`/`update.end`/`destroy.end` hooks 检查 ownership、默认 assume vulnerable、`framework_origin` finding 字段。

重构 `shannon-py/prompts/vuln-authz.txt`(369 行):grep 框架关键词仅命中行 259 一句通用警告("Don't assume a framework provides authorization unless explicitly configured")。`<starting_context>`(行 39–45)只引导读 recon 的 Horizontal/Vertical/Context,**无 Section 4.2 引导,无任何 finale-rest/epilogue 方法论**。

### 3.3 改动

从原始 `vuln-authz.txt`(行 131–200 区域)提取并适配后,补入重构 `vuln-authz.txt` 的 `<starting_context>`(行 39–45)之后:

1. **Section 0「Read Endpoint Security Context (REQUIRED — Do This First)」** —— 引导读 `{{DELIVERABLES_PATH}}/recon_deliverable.md` 的 Section 4.2,关注 framework origin。
2. **Framework Endpoint Guidance** —— finale-rest/epilogue 方法论 + `framework_origin` 字段要求。

**适配确认:** 重构 `recon.txt` 含 Section 4.2(grep 确认行 291 `## 4.2 Endpoint Security Context`),引用路径对得上。

**与交叉点①的关系:** A 补的 Section 0 引用 recon 的 Section 4.2。在 live 模式(recon.txt)下成立;在离线模式(recon-static.txt)下,recon-static 当前**没有** Section 4.2 —— 由第 4 节 B 的内容补全解决,否则离线下 authz Section 0 断链。

## 4. 设计 — B:接通 recon-static.txt 离线/静态模式

### 4.1 目标

重构支持「无 live web target 的纯静态侦察」场景(`worker.py:79` `web_url=input.web_url or ""`,允许 web_url 为空;`manager.py:48` `has_web_url = bool(variables.get("web_url"))`;`_target.txt` 有 `<if-static>` 块显示 "Offline static code analysis")。但该场景下 recon 阶段**没有合适的静态 prompt**:为静态场景准备的 `recon-static.txt` 未接通(无触发机制),而 `recon.txt` 是 live 导向。

### 4.2 机制(promptOverride,对齐原始)

在 recon agent 执行点,按 `has_web_url` 切换 template_name:

```python
# 伪代码,具体实现点在 writing-plans 定位
recon_template = "recon-static" if not has_web_url else "recon"
# 传给 prompt_manager.load_sync(template_name=recon_template, ...)
```

对齐原始 `shannon/apps/worker/src/local/runner.ts:189`:`promptOverride: agentName === 'recon' ? 'recon-static' : undefined`。

**实现点:** recon 执行链 —— `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:219`(`ActivityInput(phase="recon")`)与 activities 的 run_agent。template_name 当前由 `AGENTS` 字典(`packages/core/src/shannon_core/models/agents.py:49`,`RECON.prompt_template="recon"`)解析后传给 prompt_manager。需要在「解析 prompt_template → 调用 load_sync」之间插入 has_web_url 条件切换。精确位置在 writing-plans 阶段定位。

### 4.3 内容补全(因交叉点①,必做)

当前 `recon-static.txt`(165 行)的 output_format section 为:Architecture / Endpoint Map / Auth / Authorization / Input Vectors / File Paths(6.4 Guards / 6.5 Privilege Lattice / 6.6 Authz Candidates)/ Attack Surface Priority —— **缺「per-endpoint Endpoint Security Context(含 framework origin 标注)」**。

接通时补一个等价 section(对齐 `recon.txt` 的 Section 4.2),保证离线 deliverable 有 authz Section 0(第 3 节 A)要读的输入,使 live/离线 deliverable 结构一致、下游 agent 行为一致。

### 4.4 与 if-static 条件块机制的关系

不冲突,互补。`recon-static.txt` 行 15 `@include(shared/_target.txt)`,离线时其 `<if-static>` 正确显示 "Offline static code analysis (no live target)"。两套机制(template 条件块 + template 文件切换)各司其职:`_target.txt` 的 if-static 处理「URL/offline 文案」切换,promptOverride 处理「整份 prompt 方法论」切换。

## 5. 设计 — C:prompt_manager 补占位符警告

### 5.1 目标

变量拼错/漏传时不再 silent 渲染成带 `{{FOO}}` 的坏 prompt。对齐原始 TS `prompt-manager.ts:486-490`。

### 5.2 证据

原始 TS:
```ts
const remainingPlaceholders = result.match(/\{\{[^}]+\}\}/g);
if (remainingPlaceholders) {
  logger.warn(`Found unresolved placeholders in prompt: ${remainingPlaceholders.join(', ')}`);
}
```

重构 `packages/core/src/shannon_core/prompts/manager.py` 的 `_interpolate` 末尾(行 151–157)只有自定义变量替换 + 空行折叠,**无残留检测**。

### 5.3 改动

在 `_interpolate` 末尾(行 156 空行折叠附近)加残留检测:

```python
import logging
logger = logging.getLogger(__name__)
# ...
remaining = re.findall(r"\{\{[A-Z][A-Z0-9_]*\}\}", result)
if remaining:
    logger.warning(
        "Unresolved prompt placeholders in %s: %s",
        template_name, sorted(set(remaining)),
    )
```

### 5.4 误报规避(交叉点②)

`_build_vuln_summary_subsections`(manager.py:185–196)生成 `{{number of confirmed {vc} vulnerabilities}}` 等**合法填空提示**(给 agent 看的占位词,注入到 report),这些会留在渲染结果里。简单 `\{\{[^}]+\}\}` 会误报。

规避:正则限定为**全大写下划线格式** `\{\{[A-Z][A-Z0-9_]*\}\}` —— 真变量都是 `{{LIKE_THIS}}` 格式(见 manager.py 全文约定),自然语言填空提示含空格/小写,不匹配。此启发式覆盖所有真变量,不误报填空提示。

## 6. 交叉约束汇总

- **交叉点①(A↔B 断链):** A 的 authz Section 0 引用 recon Section 4.2;离线模式用 recon-static.txt 无 4.2。→ B 接通时必补 4.2 等价 section。A、B 必须配套,不可只做其一(只做 A 会在离线模式断链,只做 B 不补内容则离线 deliverable 缺 authz 输入)。
- **交叉点②(C 误报):** report 含合法 `{{自然语言}}` 填空提示。→ C 用全大写下划线正则规避。

## 7. 测试策略

> 注:跑测试只跑改动相关子集,不跑全套(memory:全量会 hang 在 Temporal/网络慢测试)。

- **A:** 扩展 `packages/whitebox/tests/test_vuln_prompts_chinese.py`,断言 vuln-authz 渲染后含:Section 0 文本、finale-rest/epilogue 关键词、`framework_origin` 字段要求、对 recon Section 4.2 的引用。
- **B:**
  - 单测(扩展 `packages/core/tests/test_prompt_manager.py` 或 whitebox pipeline 测试):`has_web_url=False` 时 recon 阶段 template 解析为 recon-static.txt 内容;`has_web_url=True` 时为 recon.txt。
  - 断言 `recon-static.txt` 渲染后含「per-endpoint Endpoint Security Context」section 要求。
- **C:** 扩展 `packages/core/tests/test_prompt_manager.py`:漏传已知变量 → 触发 warning(断言 `caplog`);渲染含合法填空提示(如 report 模板)→ **不**触发 warning。

## 8. 实现顺序

`C → A → B`

- **C 最先:** 最小、无依赖、纯加检测,立刻提升可观测性(后续改 prompt 时也能受益于占位符告警)。
- **A 其次:** 独立 prompt 改动,无代码依赖。
- **B 最后:** 工作量最大(接通机制 + 补内容),且依赖 A 的 Section 0 引用约定(交叉点①),顺序保证断链被即时暴露。

## 9. 风险与回滚

- **B 接通可能影响现有 live 模式:** 切换逻辑必须严格只在 `has_web_url=False` 生效,live 模式(template="recon")行为零变化。单测需覆盖 live 路径回归。
- **B 补内容的工作量:** recon-static 补 4.2 等价 section 是 prompt 文本撰写,需对照 `recon.txt` Section 4.2 的结构,保证下游 agent 能消费。若离线模式当前无实际使用方,可降级为「只接通机制 + 补内容拆到后续」—— **决策点,见第 10 节**。
- **C 正则误判:** 全大写下划线启发式若未来引入小写/含空格的变量名会漏报。当前所有变量均为大写下划线格式(已核对 manager.py 全文),风险低;后续若引入新格式需同步更新正则。
- 回滚:三处改动相互独立(A/C 各自单文件,B 涉及执行点+prompt 文件),均可独立 revert。

## 10. 已确定决策(brainstorming 阶段用户批准)

1. **B 的深度:** 采用「接通机制 + 补 Section 4.2 内容」(保证离线 deliverable 完整,避免交叉点①断链)。补内容不拆后续。
2. **实现顺序:** C → A → B。
