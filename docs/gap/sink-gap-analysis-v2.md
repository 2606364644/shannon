# Sink 点识别差距分析 v2（代码级修正版）

> 对比原始 Shannon (TypeScript, `/Users/mango/project/shannon-refactor/shannon`) 与重构 Shannon-py (Python) 在 **Sink 点识别**上的能力差距。
>
> **数据来源**：逐行代码核验（`sink_detector.py`、`pre-recon-code.txt`、`vuln-*.txt`、`recon.txt`），以代码为准。
>
> **日期**：2026-06-11（v3 更新：2026-06-11，v4 更新：2026-06-11，v5 更新：2026-06-13，v6 更新：2026-06-14，v7 更新：2026-06-15）
>
> **v2 修正要点**：基于对两个项目的全量 prompt grep 验证，修正了 v1（`entry-point-gap-analysis.md` §2）中关于 XXE/路径穿越/文件读取的三处错误判断。
>
> **v3 更新要点**：commit `b3c58bd` 已恢复模板分析方法论（两步流程 + 变体验证 + Coverage Audit 表），SK-1/SK-2/SK-3 评估需同步修正；同时更新 `pre-recon-code.txt` 行号偏移。
>
> **v4 更新要点**：代码级全量核验发现原文档仅覆盖 `sink_detector` 单模块，遗漏完整的 sink pipeline 架构。新增：① §4 补充 SK-12（`sink_merger` 已实现未接入 pipeline）、SK-13（fallback 路径 sink 真空）② §5 新增 §5.5 Pipeline 架构缺口 ③ §6 重构代码路径索引从 10 条扩充至 20 条（分三层：确定性检测 / 合并与下游消费 / LLM prompt）。
>
> **v5 更新要点**（2026-06-13 代码级复核，commit `04ad085`）：`sink_merger` 已正式接入 pipeline——`run_merge_sink_reports` activity 已定义（`activities.py:285`）、注册（`worker.py:15,76`）、调用（`workflows.py:136`），确定性 + LLM 双通道打通，**SK-12 断路已修复**；SK-13 fallback 真空因 LLM sink 合并而部分缓解（但 LLM-only sink `caller_id=""`、`needs_review=True`，仍无法挂入 call chain，**taint 传播层面仍真空**）；同步 prompt 行号偏移（`vuln-injection.txt` +2、`recon.txt` Section 9 `:440-460` → `:465-476`、`pre-recon-code.txt` SSRF/XSS section 整体后移）。47 条规则、SSRF/XSS/路径穿越/文件读取/XXE 的覆盖判定经复核**全部不变**。

> **v6 更新要点**（2026-06-14 代码级复核，HEAD `bf7a210`）：自 v5（`04ad085`）以来 sink 检测核心代码（`sink_detector`/`sink_merger`/pipeline/fallback）**无实质语义变化**，47 条规则与 SSRF/XSS/路径穿越/文件读取/XXE 覆盖判定**全部不变**。仅行号因无关 commit 间接偏移：`worker.py` 注册行 `:76`→`:77`、`workflows.py` 调用行 `:136`→`:137`（均由 commit `0196803` CLI summary 验证这一无关改动连带挪动 import/注册行）、pre-recon-code.txt URL Openers `:361`→`:360`、`file_discovery.py` 模板扩展名 `:15-17`→`:15-16`（10 种扩展名横跨两行）。架构观察：commit `27c06ea` 将 `gitnexus_call_graph` 从 stub 真实化（query→process_symbols→cypher `CALLS` 边 + 置信度分数），提升调用图质量但不改 sink 检测语义，fallback 逻辑（SK-13）行为不变；SK-13 中 LLM-only sink `caller_id=""`（`sink_merger.py:134`）仍硬编码、`parse_llm_sinks` 无推断逻辑，**taint 传播真空判定不变**。

> **v7 更新要点**（2026-06-15 代码级复核，HEAD `11f37e6`）：`git diff --stat bf7a210..11f37e6` 证实自 v6 以来 `prompts/` 与 `code_index/` **零变更**，47 条规则、SSRF/XSS/模板/路径穿越/文件读取覆盖判定与全部 pre-recon/vuln-injection 行号**仍全部有效**。本次扩展用户关注的 **auth / authz / injection / ssrf / xss** 五类：
> ① **新增 auth/authz 维度**（§2.11 / §2.12）——确定性层 0 条（设计如此：auth/authz 属 missing-control「缺失防护」而非 sink「危险汇点」，故 AST sink 引擎天然不适用），覆盖完全在 LLM 专家层（`vuln-auth.txt` 266 行 9 检查类 + 8 `vulnerability_type`、`vuln-authz.txt` 372 行 Horizontal/Vertical/Context 三维度、`validate-authentication.txt`、`auth-exploit` / `authz-exploit`）；
> ② **修正「LLM 两版完全一致」口径**——该口径仅在 **pre-recon sink-hint 层**成立；在 **`vuln-*.txt` 专家层**两边**互有胜负**（重构胜：`Source Completeness Rule` + 结构化 `authentication_required` / `accessible_routes` 字段 + 确定性 hint 注入；原始胜：framework auto-generated endpoint 即 finale-rest/epilogue ORM-to-REST 专项 IDOR 检测，`vuln-authz` 重构删约 43 行）；
> ③ §1 检测范式新增「漏洞分析专家层」行，§6 补充专家 prompt 文件索引（含 `exploit-{name}` vs `{name}-exploit` 命名差异）；
> ④ 附注 **misconfig 类别重构完全缺失**（原始有 `vuln-misconfig` + `exploit-misconfig`，重构 0 个，非本次 5 类重点）。

---

## 0. 修正摘要

| # | v1 结论 | v2 修正 | 证据 |
|---|---|---|---|
| C1 | XXE："原始胜（LLM prompt 覆盖）" | **平手（均无）**：`grep -ri XXE` 两项目 prompts/ 零命中 | 原始 `pre-recon-code.txt` 无、`vuln-injection.txt` 无、`recon.txt` 无；重构同 |
| C2 | 路径穿越："LLM prompt 也无专门覆盖" | **仅确定性层缺失**：重构 `vuln-injection.txt:2` 列 "LFI/RFI, SSTI, Path Traversal"；`:108` 有 PathTraversal 枚举值；`:120` 有 `../../../../etc/passwd` witness | 与原始 `vuln-injection.txt` 完全一致 |
| C3 | 文件读取："完全缺失" | **确定性层完全缺失，LLM prompt 有覆盖**：`pre-recon-code.txt:142` 提及 "file inclusion/path traversal (fopen, include, require, readFile)"；`vuln-injection.txt:147` 列 `fopen`, `readFile` | 两版一致 |

---

## 1. 检测范式对照

| 子维度 | 原始 Shannon (TS) | 重构 Shannon-py | 差距定性 |
|---|---|---|---|
| **确定性检测引擎** | ❌ 无 | ✅ AST call node 遍历（`sink_detector.py:detect_sinks()`），匹配 47 条 `SinkRule`，O(1) 规则索引 | **重构更可靠**：确定性、可测试、可复现 |
| **LLM 检测引擎**（pre-recon sink 预识别） | LLM Agent（Sink Hunter 子 agent）：glob 枚举模板 → 逐文件 Read → LLM 判定；业务代码 Grep 危险 API | LLM 仍跑（prompt 保留完整 SSRF 13 子类 + XSS 5 上下文），确定性 hint 经 `_static-dataflow-hints.txt` 注入 | **平手（pre-recon sink-hint 两版一致）** |
| **漏洞分析专家层（`vuln-*.txt`）** | ✅ 5 类专家 prompt（`vuln-{auth,authz,injection,ssrf,xss}` + 对应 `exploit-*`），含 Endpoint Security Context inline 引导 + framework auto-gen endpoint 专项 | ✅ 5 类专家 prompt 完整保留 + 新增 Phase 0 code index 复核 + `Source Completeness Rule` + 结构化 `authentication_required`/`accessible_routes` 字段 + 确定性 hint 注入；但**删除 framework auto-gen endpoint（finale-rest/epilogue）专项**（`vuln-authz` −43 行） | **互有胜负**（详见 §2.4 / §2.5 / §2.11 / §2.12）← v7 新增 |
| **规则存储** | 自然语言 prompt（`pre-recon-code.txt:289-415`） | 代码化 `DEFAULT_RULES`（47 条 `SinkRule`，`sink_detector.py:67-198`），Pydantic 模型 + 单测 | **重构更可维护** |
| **模板分析方法论** | ✅ 强制两步流程（`:129-136`）+ 变体验证（`:135-136`）+ Coverage Audit（`:276-284`） | ⚠️ Prompt 层已在 `b3c58bd` **恢复**：两步流程（`:141-146`）+ 变体验证（`:148-149`）+ 审计表（`:293-301`）；确定性层仍不分析模板转义指令 | **差距缩小（仅确定性层缺失）** ← v3 修正 |
| **XXE 检测** | ❌ 两边 prompt 均无 XXE 专门覆盖 | ❌ 两边 prompt 均无 + 确定性层 0 条 | **平手（均无）** |

---

## 2. 确定性规则覆盖对比（精确 47 条）

规则来源：`sink_detector.py:67-198`，逐条验证。

### 2.1 SQL 注入 — 8 条

| rule_id | 语言 | callee | receiver_pattern | needs_review |
|---|---|---|---|---|
| `py-db-cursor-execute` | Python | `execute` | `_DB_CURSOR` (cursor/cnx/conn/db/database) | ✗ |
| `py-db-cursor-executemany` | Python | `executemany` | `_DB_CURSOR` | ✗ |
| `ts-db-query` | TypeScript | `query` | None (bare) | ✓ |
| `go-db-query` | Go | `Query` | None (bare) | ✓ |
| `java-stmt-executequery` | Java | `executeQuery` | None (bare) | ✓ |
| `java-stmt-execute` | Java | `execute` | None (bare) | ✓ |
| `php-mysqli-query` | PHP | `query` | `_PHP_DB_LIKE` (mysqli/pdo/db/DB) | ✗ |
| `php-db-select-static` | PHP | `select` | `^(DB)$` | ✗ |

**裁决**：✅ 持平。全 5 语言覆盖。

### 2.2 命令注入 — 14 条

| rule_id | 语言 | callee | needs_review |
|---|---|---|---|
| `py-os-system` | Python | `system` | ✗ |
| `py-os-popen` | Python | `popen` | ✗ |
| `py-subprocess-run` | Python | `run` | ✗ |
| `py-subprocess-popen` | Python | `Popen` | ✗ |
| `py-subprocess-call` | Python | `call` | ✗ |
| `py-subprocess-checkoutput` | Python | `check_output` | ✗ |
| `ts-eval` | TypeScript | `eval` | ✗ |
| `ts-child-process-exec` | TypeScript | `exec` | ✓ |
| `go-exec-command` | Go | `Command` | ✗ |
| `java-runtime-exec` | Java | `exec` | ✓ |
| `php-shell-exec` | PHP | `shell_exec` | ✗ |
| `php-system` | PHP | `system` | ✗ |
| `php-passthru` | PHP | `passthru` | ✗ |
| `php-proc-exec` | PHP | `exec` | ✗ |

**裁决**：✅ **重构确定性层优于原始**。Python 6 条为全语言最完整，PHP 4 条覆盖所有危险函数。

### 2.3 反序列化 — 5 条

| rule_id | 语言 | callee | needs_review |
|---|---|---|---|
| `py-pickle-loads` | Python | `loads` | ✗ |
| `py-pickle-load` | Python | `load` | ✗ |
| `py-yaml-load` | Python | `load` | ✗ |
| `php-unserialize` | PHP | `unserialize` | ✗ |
| `java-objectinput-readobject` | Java | `readObject` | ✓ |

**裁决**：基本持平。微小差距：Python `marshal`（`_PICKLE_LIKE` regex 包含 marshal 但无 marshal 专用 callee）、JS/TS 反序列化无规则。

### 2.4 SSRF — 11 条（仅 HTTP Client 子类）

| rule_id | 语言 | callee | needs_review |
|---|---|---|---|
| `py-requests-get` | Python | `get` | ✗ |
| `py-requests-post` | Python | `post` | ✗ |
| `py-requests-put` | Python | `put` | ✗ |
| `py-urllib-urlopen` | Python | `urlopen` | ✓ |
| `ts-fetch` | TypeScript | `fetch` | ✗ |
| `ts-axios-get` | TypeScript | `get` | ✗ |
| `go-http-get` | Go | `Get` | ✗ |
| `go-http-post` | Go | `Post` | ✗ |
| `java-httpclient-send` | Java | `send` | ✗ |
| `php-curl-exec` | PHP | `curl_exec` | ✗ |
| `php-file-get-contents` | PHP | `file_get_contents` | ✗ |

**SSRF 确定性层 vs LLM prompt 层覆盖对比**：

> ⚠️ **v5 行号偏移说明**：下表 `pre-recon-code.txt` 行号为 v3 时期（commit `b3c58bd`）快照。2026-06-13 复核确认该 section 已整体后移——SSRF 13 子类现位于 `:340-430`（HTTP Clients `:346`、Raw Sockets `:355`、URL Openers `:360`、Redirect ~`:362-371`、Headless `:372`、Media `:379`、Link Preview `:385`、Webhook Testers `:392`、SSO/OIDC `:399`、Package `:413`、Monitoring `:420`、Cloud Metadata `:427`）；XSS 5 上下文现位于 `:306-339`；模板方法论 `:141-149`、Coverage Audit `:293-301` 仍有效。**结论（13 子类全保留、两版一致）不变。**

| SSRF 子类 | 确定性层 | LLM prompt（两版一致） | 差距定性 |
|---|---|---|---|
| HTTP(S) Clients | ✅ 11 条 | ✅ `pre-recon-code.txt:334-336` | 确定性+LLM 均覆盖 |
| Raw Sockets & Connect APIs | ❌ | ✅ `:338-341`（Socket.connect, net.Dial, TcpClient 等） | 确定性层缺失 |
| URL Openers & File Includes | ⚠️ 仅 `urlopen` 1 条 | ✅ `:343-347`（file_get_contents, fopen, loadHTML 等） | 确定性层窄 |
| Redirect & "Next URL" Handlers | ❌ | ✅ `:349-353` | 确定性层缺失 |
| Headless Browsers & Render Engines | ❌ | ✅ `:355-360`（Puppeteer, Playwright, Selenium） | 确定性层缺失 |
| Media Processors | ❌ | ✅ `:362-366`（ImageMagick, FFmpeg） | 确定性层缺失 |
| Link Preview & Unfurlers | ❌ | ✅ `:368-372` | 确定性层缺失 |
| Webhook Testers & Callback Verifiers | ❌ | ✅ `:375-380` | 确定性层缺失 |
| SSO/OIDC Discovery & JWKS Fetchers | ❌ | ✅ `:382-387` | 确定性层缺失 |
| Importers & Data Loaders | ❌ | ✅ `:389-394` | 确定性层缺失 |
| Package/Plugin/Theme Installers | ❌ | ✅ `:396-401` | 确定性层缺失 |
| Monitoring & Health Check Frameworks | ❌ | ✅ `:403-408` | 确定性层缺失 |
| Cloud Metadata Helpers | ❌ | ✅ `:410-415` | 确定性层缺失 |

**SSRF 裁决**：⚠️ 确定性层仅覆盖 HTTP Client（~1/13 子类），但 **LLM prompt 层 13/13 完整保留**。真实差距 = 确定性 hint 无 SSRF 非 HTTP 子类加持（降低可靠性，非完全缺失）。

> **v7 专家层核验**（`vuln-ssrf.txt`）：TS 316 行 / PY 314 行，实质 diff 11 行——均为变量占位符（`playwright-cli` → `{{BROWSER_COMMANDS}}`）+ Endpoint Security Context inline 引导（重构抽成 `shared/_cross-route-enumeration.txt` include，等价）。**专家层两边等价**，无 framework 专项删除（不同于 authz / xss）。

### 2.5 XSS — 2 条（仅 TS DOM 类）

| rule_id | 语言 | callee | receiver_pattern | needs_review |
|---|---|---|---|---|
| `ts-innerhtml` | TypeScript | `innerHTML` | None (bare) | ✓ |
| `ts-document-write` | TypeScript | `write` | `^(document)$` | ✓ |

**XSS 确定性层 vs LLM prompt 层覆盖对比**：

| 渲染上下文 | 确定性层 | LLM prompt（两版一致） | 差距定性 |
|---|---|---|---|
| HTML Body (innerHTML 等) | ✅ `ts-innerhtml` | ✅ `pre-recon-code.txt:290-296`（innerHTML, outerHTML, insertAdjacentHTML, jQuery 等） | 确定性层有 |
| HTML Attribute (onclick, href 等) | ❌ | ✅ `:297-302` | 确定性层缺失 |
| JavaScript Context (eval, setTimeout 等) | ❌ | ✅ `:303-308` | 确定性层缺失 |
| CSS Context (element.style 等) | ❌ | ✅ `:309-311` | 确定性层缺失 |
| URL Context (location, window.open 等) | ❌ | ✅ `:312-321` | 确定性层缺失 |
| document.write | ✅ `ts-document-write` | ✅ | 确定性层有 |

**XSS 裁决**：⚠️ 确定性层仅 2 条 TS 规则，但 **LLM prompt 层 5/5 上下文完整保留**。其他语言确定性层为零。

> **v7 专家层核验**（`vuln-xss.txt`）：TS 306 行 / PY 299 行，**互有胜负**——重构删 Endpoint Security Context inline 引导 + server-rendered template 反射 XSS 注意事项（`ctx.render`/`res.render` + `ctx.query.*` 进 `<script>` 的 `JSON.stringify` 不闭合 `</script>` 细节）；但**新增 `Source Completeness Rule`**（多 source 同 sink 须每 source 独立成条）+ `authentication_required` / `accessible_routes` 结构化字段 + 确定性 hint 注入。专家层结构化程度重构更高，原始对 server-rendered 反射 XSS 提示更细。

### 2.6 模板/SSTI — 2 条

| rule_id | 语言 | callee | receiver_pattern | needs_review |
|---|---|---|---|---|
| `py-render-template-string` | Python | `render_template_string` | None | ✗ |
| `py-jinja-template-render` | Python | `render` | `_TEMPLATE_LIKE` (flask/jinja2) | ✗ |

**模板检测差距详述**：

| 检测面 | 原始 | 重构 | 裁决 |
|---|---|---|---|
| Python 模板注入函数 | ✅ LLM 识别 | ✅ 2 条确定性规则 | 持平 |
| TS/PHP 模板注入函数 | ✅ LLM 识别 | ❌ 无确定性规则 | 低（LLM 兜底） |
| **模板文件转义指令分析** | ✅ **强制两步流程**（`pre-recon-code.txt:129-136`）：Step 1 glob 枚举模板 → Step 2 逐文件区分 escaped（EJS `<%= %>`、Jinja2 `{{ }}`）vs unescaped（EJS `<%- %>`、Jinja2 `{{\|safe}}`） | ⚠️ **Prompt 层已恢复（`b3c58bd`），确定性层仍缺失**：① 确定性层不分析模板文件转义指令 ② ~~Prompt 层全删~~ **已在 `b3c58bd` 恢复**：两步流程（`:141-146`）+ 变体验证（`:148-149`）+ 审计表（`:293-301`） ③ `file_discovery.py:15-16` 有 10 种模板扩展名分类但**未接 `sink_detector`（断路）** | **差距缩小（仅确定性层 + file_discovery 断路）** ← v3 修正 |
| Cross-Variant 验证 | ✅ `:135-136` 强制跨品牌/区域/主题验证 | ⚠️ Prompt 已恢复（`:148-149`），确定性层无 | **差距缩小** ← v3 修正 |
| Template Coverage Audit 表 | ✅ `:276-284` 完整性审计（每模板文件的 sink 数+转义模式+分析状态） | ⚠️ Prompt 已恢复（`:293-301`），确定性层无 | **差距缩小** ← v3 修正 |

### 2.7 文件操作 — 3 条（仅 PHP）

| rule_id | 语言 | callee | category | needs_review |
|---|---|---|---|---|
| `php-file-put-contents` | PHP | `file_put_contents` | FILE/file_write | ✗ |
| `php-include` | PHP | `include` | FILE/file_include | ✓ |
| `php-require` | PHP | `require` | FILE/file_include | ✓ |

**文件操作差距（修正）**：

| 操作 | 原始 LLM prompt | 重构确定性层 | 重构 LLM prompt | 裁决 |
|---|---|---|---|---|
| 文件写入 | ✅ | ✅ `file_put_contents` | ✅ | 部分 |
| 文件包含 | ✅ | ✅ `include`, `require` | ✅ | 部分 |
| **文件读取 (fopen/readFile/open)** | ✅ `vuln-injection.txt:147` 列举 `fopen`, `readFile` | ❌ 0 条确定性规则 | ✅ 同原始（`vuln-injection.txt:147`） | **仅确定性层缺失** |

### 2.8 XXE — 0 条

| 检测面 | 原始 | 重构 | 裁决 |
|---|---|---|---|
| XML 外部实体 | ❌ 全部 prompts/ 零 XXE 命中 | ❌ 确定性层 0 条 + 全部 prompts/ 零 XXE 命中 | **平手（均无）** ← v2 修正 |

### 2.9 路径穿越 — 0 条确定性规则

| 检测面 | 原始 LLM prompt | 重构确定性层 | 重构 LLM prompt | 裁决 |
|---|---|---|---|---|
| 路径穿越检测 | ✅ `vuln-injection.txt` LFI/RFI/PathTraversal 类别（`:2`, `:108`, `:120` witness `../../../../etc/passwd`, `:147` sink 列表）+ `recon.txt:440-460` Section 9 | ❌ 0 条 | ✅ **与原始完全一致**（`vuln-injection.txt:2/108/120/147`, `recon.txt:383/394`） | **仅确定性层缺失** ← v2 修正 |

### 2.10 重定向 — 2 条

| rule_id | 语言 | callee | needs_review |
|---|---|---|---|
| `ts-res-redirect` | TypeScript | `redirect` | ✓ |
| `py-flask-redirect` | Python | `redirect` | ✓ |

**裁决**：部分（缺 Go/Java/PHP 确定性规则，LLM 可识别）。

### 2.11 认证（auth）— 0 条确定性规则（设计如此）

> auth 属 **missing-control**（缺失防护）而非 sink（危险汇点）：漏洞形态是「该有的检查没做」（session 未轮换、rate limit 缺失、JWT 未验签、默认凭据），无可枚举的危险 callee。故 AST sink 引擎（`SinkCategory` 8 值：SQL/COMMAND/DESERIALIZATION/SSRF/TEMPLATE/XSS/FILE/REDIRECT）**天然不覆盖** auth——这是设计取舍而非缺口。覆盖完全在 LLM 专家层。

| 层 | 原始 Shannon (TS) | 重构 Shannon-py | 裁决 |
|---|---|---|---|
| 确定性层 | ❌ 无（同因） | ❌ 0 条 `SinkRule`（`SinkCategory` 无 AUTH 值） | 平手（设计如此） |
| LLM 专家层 | ✅ `vuln-auth.txt`（266 行） | ✅ `vuln-auth.txt`（266 行；两边 diff 仅 10 行，均为变量占位符 `{{BROWSER_COMMANDS}}` 等 + 措辞） | **平手（等价）** |
| 凭据登录预检 | ✅ `validate-authentication.txt`（33 行） | ✅ `validate-authentication.txt`（33 行，仅变量占位符差异） | 平手 |
| 利用阶段 | ✅ `exploit-auth.txt` | ✅ `auth-exploit.txt`（命名顺序差异） | 平手 |

**`vuln-auth.txt` 9 检查类别**（两边一致）：① Transport & caching（HTTPS/HSTS/`Cache-Control: no-store`）② Rate limiting / CAPTCHA / monitoring ③ Session management（HttpOnly/Secure/SameSite/登录后轮换/登出失效/超时/URL 不携带 session）④ Token/session properties（熵/HTTPS/过期失效）⑤ Session fixation（登录前后 session ID 对比）⑥ Password & account policy（默认凭据/强策略/单向哈希/MFA）⑦ Login/signup responses（无用户枚举）⑧ Recovery & logout（一次性短 TTL token）⑨ SSO/OAuth（state/nonce/redirect allowlist/签名验签/PKCE/**nOAuth mutable-attribute 检查**：须用 `sub` 而非 `email`/`preferred_username`）。

**8 `vulnerability_type`**：`Authentication_Bypass | Session_Management_Flaw | Login_Flow_Logic | Token_Management_Issue | Reset_Recovery_Flaw | Transport_Exposure | Abuse_Defenses_Missing | OAuth_Flow_Issue`。

**裁决**：✅ **平手**。auth 是两边 LLM 专家层覆盖最完整、最对等的类别（含 nOAuth 这类细分）。

### 2.12 授权（authz）— 0 条确定性规则（设计如此）；专家层互有胜负

> 同 auth，authz 属 missing-control（缺失所有权/角色检查），AST sink 引擎天然不覆盖。

| 层 | 原始 Shannon (TS) | 重构 Shannon-py | 裁决 |
|---|---|---|---|
| 确定性层 | ❌ 无 | ❌ 0 条 | 平手（设计如此） |
| LLM 专家层 | ✅ `vuln-authz.txt`（**415 行**） | ✅ `vuln-authz.txt`（**372 行，少 43 行**） | **互有胜负** ← v7 核验 |
| 利用阶段 | ✅ `exploit-authz.txt` | ✅ `authz-exploit.txt` | 平手 |

**`vuln-authz.txt` 3 分析维度**（两边一致）：① **Horizontal**（水平越权 / IDOR：trace 至 side effect 前是否遇 ownership / tenant guard）② **Vertical**（垂直提权：是否遇 role / capability guard）③ **Context / Workflow**（工作流：后续步骤是否校验前序状态）。形式化「Side Effect」+「Sufficient Guard」+「Proof Obligations」（guard 须**主导** sink，guard 出现在 side effect **之后**无效；UI-only 检查不算 guard）。

**两边实质差异（v7 逐行核验 `diff`）**：

| 差异项 | 原始 (TS) | 重构 (PY) | 性质 |
|---|---|---|---|
| `_static-dataflow-hints.txt` 注入 | ❌ 无 | ✅ `@include`（开头） | **重构胜**（SK+3） |
| `_cross-route-enumeration.txt` | ❌ inline 于 conclusion（引用 recon §4.1） | ✅ 抽成 `@include`（开头） | 等价重构 |
| **Section 0 "Read Endpoint Security Context"** | ✅ 读 recon §4.2，提取认证要求/中间件链/所有权验证/框架来源，按风险排序（finale-rest/epilogue + `none detected` 优先） | ❌ **删除** | 原始胜 |
| **Framework auto-gen endpoint 专项**（finale-rest/epilogue ORM-to-REST 的 IDOR：检查 `create.end`/`update.end`/`destroy.end` hooks 是否加 ownership） | ✅ 18 行指导 + JSON 字段示例（`framework_origin`/`recon_ownership_check`/`guard_evidence`） | ❌ **删除** | **原始胜**（重构去特定化） |
| Cross-Route Verification（finding 须列全 `affected_routes`，pre-auth route 配对） | ✅ inline 于 conclusion | ❌ 删除（移入 shared include，等价） | 等价 |

**裁决**：⚠️ **互有胜负**。方法论内核（Horizontal / Vertical / Context + Side Effect / Sufficient Guard / Proof Obligation）两边一致。差异全在「框架自动生成端点」这条线：重构为**去特定化**删除 finale-rest / epilogue 专项，**对该类 ORM-to-REST 框架的 IDOR 检测弱于原始**（其他框架不受影响）；作为补偿，重构新增确定性 hint 注入 + 把 cross-route 抽成共享 include，**结构化程度更高**。`vuln-xss` / `vuln-injection` 亦同此模式（删 Endpoint Security Context inline 引导 + Framework 指导，新增 `Source Completeness Rule` + 结构化字段，详见 §2.4 / §2.5 裁决）。

---

## 3. Sink 检测功能完整性

| 功能 | 原始 | 重构 | 差距 |
|---|---|---|---|
| **规则可维护性** | ⭐⭐ 自然语言 prompt | ⭐⭐⭐⭐⭐ Pydantic 模型 + 47 条 SinkRule + 单测覆盖 | **重构远胜** |
| **规则结构化** | LLM 输出 JSON Schema 验证 | `SinkCallSite` Pydantic + `SlotContext` 枚举 + `DangerousSlot` + `is_entry_hint` 标记 | **重构更结构化** |
| **模板分析方法论** | ⭐⭐⭐⭐⭐ 强制两步 + 变体验证 + 审计表 | ⭐⭐⭐ Prompt 层已恢复（`b3c58bd`），确定性层仍不分析 + file_discovery 断路 | **差距缩小** ← v3 修正 |
| **变体审计** | ⭐⭐⭐⭐ 强制 | ⭐⭐⭐ Prompt 已恢复（`:148-149`），确定性层无 | **差距缩小** ← v3 修正 |
| **SSRF LLM 覆盖** | ⭐⭐⭐⭐ 13 子类 | ⭐⭐⭐⭐ 13 子类（**完全一致**） | **平手** |
| **XSS LLM 覆盖** | ⭐⭐⭐⭐ 5 上下文 | ⭐⭐⭐⭐ 5 上下文（**完全一致**） | **平手** |
| **路径穿越 LLM 覆盖** | ⭐⭐⭐⭐ LFI/RFI/PathTraversal | ⭐⭐⭐⭐ **与原始一致** | **平手** ← v2 修正 |
| **文件读取 LLM 覆盖** | ⭐⭐⭐⭐ 列举 fopen/readFile | ⭐⭐⭐⭐ **与原始一致** | **平手** ← v2 修正 |
| **XXE 检测** | ☆ 无 | ☆ 无 | **平手（均无）** ← v2 修正 |
| **确定性 hint 注入** | ❌ 无 | ✅ `_static-dataflow-hints.txt` 注入 LLM | **重构新增** |
| **跨语言 taint 传播** | LLM 兜底（不限语言） | 仅 Python/TypeScript（Go/Java/PHP 零确定性传播） | **平手（各有局限）** |
| **auth / authz 覆盖** | `vuln-auth` / `vuln-authz` 专家 + exploit 阶段 | 确定性层 0 条（missing-control 非 sink，设计如此）；专家层 auth 等价 / authz 互有胜负 | **平手（auth）+ 互有胜负（authz）** ← v7 |

---

## 4. Sink 点总差距矩阵（v2 修正版）

| # | 差距项 | 原始能力 | 重构现状 | 严重度 | v2 修正说明 |
|---|---|---|---|---|---|
| SK-1 | **模板文件转义指令分析** | 强制两步流程（`:129-136`）+ 变体验证（`:135-136`）+ 审计表（`:276-284`） | Prompt 层已在 `b3c58bd` **恢复**（`:141-149` + `:293-301`）；确定性层仍不分析 + file_discovery 断路 | **中** | v3 修正：Prompt 层已恢复，差距缩小至确定性层 |
| SK-2 | 跨变体验证 | `:135-136` 强制跨品牌/区域/主题 | Prompt 已恢复（`:148-149`），确定性层无 | 低 | v3 修正：Prompt 层已恢复 |
| SK-3 | Coverage Audit 表 | `:276-284` 完整性审计 | Prompt 已恢复（`:293-301`），确定性层无 | 低 | v3 修正：Prompt 层已恢复 |
| ~~SK-4~~ | ~~XXE 检测~~ | ~~✅ LLM prompt 覆盖~~ | ~~确定性层 + LLM prompt 均无~~ | ~~中-高~~ | ❌ **撤回**：两边均无，非"原始胜" |
| SK-5 | **文件读取确定性规则** | LLM 列举 `fopen, readFile` | 确定性层 0 条；LLM prompt 与原始一致 | **中** | 修正：差距仅限确定性层 |
| SK-6 | **SSRF 确定性覆盖**（12/13 非HTTP子类） | prompt 13 子类 | 确定性层仅 HTTP Client（11 条）；LLM prompt 13/13 完整 | **中** | 不变 |
| SK-7 | **XSS 确定性覆盖**（非DOM类） | prompt 5 上下文 | 确定性层仅 2 条 TS；LLM prompt 5/5 完整 | **中** | 不变 |
| SK-8 | TS/PHP SSTI 模板注入 | LLM 可识别 | 无确定性规则 | 低 | 不变 |
| SK-9 | Go/Java/PHP 重定向 | LLM 可识别 | 无确定性规则 | 低 | 不变 |
| SK-10 | JS/TS 反序列化 | LLM 可识别 | 无规则 | 低 | 不变 |
| SK-11 | **路径穿越确定性规则** | LLM `vuln-injection.txt` 有完整覆盖 | 确定性层 0 条；LLM prompt **与原始完全一致** | **中** | 修正：差距仅限确定性层 |
| SK+1 | 确定性规则引擎 | 无 | 47 条 `SinkRule` + Pydantic + 单测 | 重构新增 ✨ | — |
| SK+2 | Slot 类型系统 | 自然语言 slot | `SlotContext` 枚举（8 值）+ `DangerousSlot` 模型 | 重构新增 ✨ | — |
| SK+3 | 确定性 hint 注入 | 无 | `_static-dataflow-hints.txt` → LLM | 重构新增 ✨ | — |
| SK+4 | is_entry_hint 标记 | 无 | 保守浅层判断（参数名/request.*/PHP 超全局） | 重构新增 ✨ | — |
| SK-12 | ~~sink_merger 未接入 pipeline~~ → ✅ **已修复** | LLM Sink Hunter 报告自然融入流程 | `sink_merger.py`（含 `merge_sink_reports()` + `parse_llm_sinks()` + 单测）已接入：`run_merge_sink_reports` activity（`activities.py:285`）→ `worker.py:15,77` 注册 → `workflows.py:137` 调用；确定性 + LLM sink 双通道已打通 | ~~中-高~~ → ✅ **已修复** | commit `04ad085` |
| SK-13 | **Fallback 路径 taint 传播仍真空**（sink 清单已部分缓解） | 无降级路径（依赖单一流程） | `_build_code_index_fallback()`（`__init__.py:230`）仍返回 `sink_call_sites=[]`、`degradation_level=MINIMAL`；但 SK-12 修复后 `run_merge_sink_reports` 会从 `pre_recon_deliverable.md` 补 LLM-only sink（`caller_id=""`、`needs_review=True`）——**sink 清单不再真空，但 LLM-only sink 无 caller_id 无法挂入 call chain，taint 传播 / sink-aware 风险评分仍失效** | ~~中~~ → **低-中** | v5 修正：SK-12 缓解清单真空，传播层仍失效 |
| SK-14 | **认证（auth）覆盖** | `vuln-auth.txt`（266 行，9 检查类 + 8 `vulnerability_type`，含 nOAuth）+ `validate-authentication.txt`（凭据登录预检）+ `exploit-auth.txt` | 确定性层 0 条（设计如此，auth 属 missing-control 非 sink）；`vuln-auth.txt` 两边等价（diff 10 行全为变量占位符） | 低（设计取舍，非缺口） | v7 新增：专家层平手 |
| SK-15 | **授权（authz）专家层 — framework auto-gen 专项** | `vuln-authz.txt`（415 行，含 finale-rest/epilogue ORM-to-REST IDOR 专项 + Section 0 Endpoint Security Context + Cross-Route Verification） | 确定性层 0 条；`vuln-authz.txt` 372 行（删 framework 专项 −43 行），新增确定性 hint + `Source Completeness Rule` + 结构化字段 | **中**（仅影响 finale-rest/epilogue 类 ORM-to-REST 目标） | v7 新增：专家层互有胜负 |
| SK-16 | **misconfig 类别整体** | ✅ `vuln-misconfig.txt` + `exploit-misconfig.txt` | ❌ **0 个文件，类别完全未实现** | 中-高（非本次 5 类重点） | v7 附注：重构漏移植 misconfig 整条链路 |

---

## 5. 综合裁决

### 5.1 重构明确胜出

- **确定性检测引擎**：质的进步（47 条 AST 精确规则 vs 自然语言 prompt）
- **规则可维护性**：Pydantic 模型 + 单测 vs 自然语言
- **命令注入覆盖**：确定性层 Python 6 条 + PHP 4 条，为全语言最完整
- **结构化输出**：SinkCallSite Pydantic + SlotContext 类型系统

### 5.2 原始明确胜出

- **模板文件 Sink 确定性检测**：确定性层不分析模板转义指令 + `file_discovery` 断路 — **差距已缩小**（Prompt 层方法论已在 `b3c58bd` 恢复：两步流程 + 变体验证 + Coverage Audit）

### 5.3 确定性层差距（pre-recon sink-hint 层平手）

以下差距**仅存在于确定性层**，pre-recon sink-hint 两版一致（专家层 `vuln-*.txt` 的细微差异见各 §2.x 裁决与 §5.6）：
- SSRF（确定性仅 HTTP Client，LLM 13/13）
- XSS（确定性仅 2 条 TS，LLM 5/5 上下文）
- 路径穿越（确定性 0 条，LLM 有完整 LFI/RFI/PathTraversal）
- 文件读取（确定性 0 条，LLM 列举 fopen/readFile）

**影响**：确定性 hint 注入给 LLM 的结构化数据不含这些类别 → LLM 检测可靠性降低（靠自由发挥而非确定性 hint + 强制方法论），但非完全缺失。

### 5.4 两边均无

- **XXE**：两边 prompt 均无 XXE 专门覆盖 + 确定性层 0 条

### 5.5 重构 Pipeline 架构缺口（v5 修订）

重构的 sink 检测 pipeline 已形成完整的 8 步链路（`__init__.py:51`）：

```
tree-sitter 解析 → GitNexus 调用图 → detect_sinks() → LLM 污点分析
  → 跨函数传播 → 入口点融合 → CodeIndex 组装
```

> **v5 复核**：原"两个架构级缺口"中 SK-12（`sink_merger` 断路）已在 commit `04ad085` 修复，当前仅余 SK-13（fallback taint 真空，且已被部分缓解）。

1. ~~**`sink_merger` 断路**（SK-12）~~ → ✅ **已修复**（commit `04ad085`）：`sink_merger.py`（含 `parse_llm_sinks()` + `_infer_category()` + `merge_sink_reports()` 碰撞去重 + 完整单测）现已通过 `run_merge_sink_reports` activity 接入 pipeline（`activities.py:285` → `worker.py:15,77` → `workflows.py:137`）。该 activity 在 `run_code_index`（确定性 `detect_sinks`）与 PRE_RECON（LLM Sink Hunter）并行完成后执行，从 `code_index.json` 读确定性 sink、从 `pre_recon_deliverable.md` 读 LLM sink，按 `(file_path, line)` 去重后回写。**影响**：两条检测通道已打通，LLM-only sink 以 `rule_id="llm-sink-hunter"`、`needs_review=True` 补入清单。

2. **Fallback 路径 taint 传播仍真空**（SK-13，严重度下调）：GitNexus 不可用时 `_build_code_index_fallback()` 仍返回 `sink_call_sites=[]`、`degradation_level=MINIMAL`；但 SK-12 修复后 `run_merge_sink_reports` 会补 LLM-only sink。**残余影响**：LLM-only sink `caller_id=""` 无法挂入 call chain，`risk_scorer` / `tiered_audit` 的 sink-aware 评分仍部分失效（sink 清单非空但缺 taint 关联）。

### 5.6 auth / authz 维度与专家层互有胜负（v7 新增）

- **auth / authz 确定性层**：均 0 条（设计如此——属 missing-control「缺失防护」而非 sink「危险汇点」，不在 AST sink 引擎职责内）。**非缺口**。
- **auth 专家层**：`vuln-auth.txt` 两边等价（9 检查类 + 8 `vulnerability_type`，含 nOAuth mutable-attribute 检查），平手。
- **authz 专家层**：`vuln-authz.txt` 互有胜负——重构去特定化删除 finale-rest/epilogue 框架专项（原始胜），新增确定性 hint + 结构化字段（重构胜）。**对 ORM-to-REST 框架目标的 IDOR 检测重构弱于原始**。
- **专家层总体（5 类 `vuln-*.txt`）**：重构统一新增 `Source Completeness Rule`（多 source 同 sink 须每 source 独立成条）+ `authentication_required` / `accessible_routes` 结构化字段 + `@include(_static-dataflow-hints.txt)` 确定性 hint；authz / xss / injection 删 Endpoint Security Context inline 引导 + Framework auto-gen 指导（去特定化），ssrf 仅变量/inline→include 等价调整。
- **misconfig（SK-16 附注）**：重构完全未移植该类别（原始 `vuln-misconfig` + `exploit-misconfig`），属真实缺口，但非本次 5 类重点。

---

## 6. 关键代码路径索引

### 原始 Shannon (TS)

| 功能 | 文件 |
|---|---|
| Sink Hunter 两步流程 | `apps/worker/prompts/pre-recon-code.txt:129-136` |
| 变体验证 | `apps/worker/prompts/pre-recon-code.txt:135-136` |
| Coverage Audit 表 | `apps/worker/prompts/pre-recon-code.txt:276-284` |
| SSRF 分类（13 子类） | `apps/worker/prompts/pre-recon-code.txt:333-415` |
| XSS 分类（5 上下文） | `apps/worker/prompts/pre-recon-code.txt:289-322` |
| LFI/RFI/PathTraversal | `apps/worker/prompts/vuln-injection.txt:2/115/126/154/216` |
| recon Section 9 注入源 | `apps/worker/prompts/recon.txt:440-460` |

#### 漏洞分析专家层（`vuln-*.txt`，v7 新增）

| 类别 | 专家 prompt（analysis） | 利用 prompt（exploitation） |
|---|---|---|
| auth / authz / injection / ssrf / xss | `apps/worker/prompts/vuln-{name}.txt` | `apps/worker/prompts/exploit-{name}.txt` |
| misconfig | `apps/worker/prompts/vuln-misconfig.txt` | `apps/worker/prompts/exploit-misconfig.txt` |
| 凭据登录预检 | `apps/worker/prompts/validate-authentication.txt` | — |

### 重构 Shannon-py

#### 确定性检测层

| 功能 | 文件 |
|---|---|
| Sink 规则库（47 条） | `packages/core/src/shannon_core/code_index/sink_detector.py:67-198` |
| Sink 检测算法 | `packages/core/src/shannon_core/code_index/sink_detector.py:249-325` |
| 数据模型（SlotContext/SinkCallSite/SinkCategory） | `packages/core/src/shannon_core/code_index/parameter_models.py` |
| 文件发现（模板/schema） | `packages/core/src/shannon_core/code_index/file_discovery.py` |

#### Sink 合并与下游消费

| 功能 | 文件 | 备注 |
|---|---|---|
| Sink 合并（确定性 + LLM 去重） | `packages/core/src/shannon_core/code_index/sink_merger.py:99` | ✅ 已接入（SK-12，commit `04ad085`） |
| LLM Sink 报告解析 | `packages/core/src/shannon_core/code_index/sink_merger.py:71` | ✅ 同上 |
| 链路风险评分（Spec B category + legacy 双路径） | `packages/core/src/shannon_core/code_index/risk_scorer.py:96` | ✅ 已接入 |
| 逐函数 LLM 污点分析 | `packages/core/src/shannon_core/code_index/llm_taint_analyzer.py:230` | ✅ 已接入 |
| 跨函数污点传播 | `packages/core/src/shannon_core/code_index/chain_propagator.py:133` | ✅ 已接入 |
| 分层审计规划 | `packages/core/src/shannon_core/code_index/tiered_audit.py:48` | ✅ 已接入 |
| 审计输入构建（sink 清单渲染） | `packages/core/src/shannon_core/code_index/audit_input_builder.py:27` | ✅ 已接入 |
| Pipeline 主编排（8 步流程） | `packages/core/src/shannon_core/code_index/__init__.py:51` | ✅ |

#### LLM Prompt 层

| 功能 | 文件 |
|---|---|
| LLM prompt SSRF（13 子类） | `prompts/pre-recon-code.txt:340-432` |
| LLM prompt XSS（5 上下文） | `prompts/pre-recon-code.txt:306-339` |
| 模板分析方法论（两步流程+变体验证） | `prompts/pre-recon-code.txt:141-149` |
| Template Coverage Audit | `prompts/pre-recon-code.txt:293-301` |
| LFI/RFI/PathTraversal | `prompts/vuln-injection.txt:2/110/122/149/211-213`（v5：较 v2 `:2/108/120/147/210-211` 偏移 +2） |
| recon Section 9 注入源 | `prompts/recon.txt:465-476`（v5：较 v2 `:383-394` 偏移约 +80） |

#### 漏洞分析专家层（`vuln-*.txt`，v7 新增）

| 类别 | 专家 prompt | 利用 prompt | 备注 |
|---|---|---|---|
| 认证 auth | `prompts/vuln-auth.txt`（266 行） | `prompts/auth-exploit.txt` | + `prompts/validate-authentication.txt`（凭据预检）；两边等价 |
| 授权 authz | `prompts/vuln-authz.txt`（372 行） | `prompts/authz-exploit.txt` | 重构 −43 行（删 finale-rest/epilogue framework 专项）；互有胜负 |
| 注入 injection | `prompts/vuln-injection.txt`（382 行） | `prompts/injection-exploit.txt` | 重构新增 `Source Completeness Rule` + 结构化 `authentication_required`/`accessible_routes` 字段 |
| SSRF | `prompts/vuln-ssrf.txt`（314 行） | `prompts/ssrf-exploit.txt` | 两边等价（仅变量占位符 + inline→include） |
| XSS | `prompts/vuln-xss.txt`（299 行） | `prompts/xss-exploit.txt` | 互有胜负（删 server-rendered 反射提示，增结构化字段） |
| misconfig | ❌ 无 | ❌ 无 | **重构完全缺失**（原始 `vuln-misconfig`/`exploit-misconfig`） |

> **命名差异**：原始为 `exploit-{name}.txt`，重构为 `{name}-exploit.txt`（前缀顺序颠倒），功能等价。
> **行数对照**：vuln-auth 266=266 / vuln-authz 415→372 / vuln-injection 387→382 / vuln-ssrf 316→314 / vuln-xss 306→299 / validate-authentication 33=33。

---

## 7. 交叉参考

- `docs/whitebox-refactoring-assessment.md` — 全维度评估（v7），本分析是其 §1 Sink 部分的代码级修正
- `docs/gap/entry-point-gap-analysis.md` — v1 差距分析，本分析修正其 §2 的三处错误（C1/C2/C3）
