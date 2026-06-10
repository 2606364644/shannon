# Sink 点识别差距分析 v2（代码级修正版）

> 对比原始 Shannon (TypeScript, `/Users/mango/project/shannon-refactor/shannon`) 与重构 Shannon-py (Python) 在 **Sink 点识别**上的能力差距。
>
> **数据来源**：逐行代码核验（`sink_detector.py`、`pre-recon-code.txt`、`vuln-*.txt`、`recon.txt`），以代码为准。
>
> **日期**：2026-06-11（v3 更新：2026-06-11）
>
> **v2 修正要点**：基于对两个项目的全量 prompt grep 验证，修正了 v1（`entry-point-gap-analysis.md` §2）中关于 XXE/路径穿越/文件读取的三处错误判断。
>
> **v3 更新要点**：commit `b3c58bd` 已恢复模板分析方法论（两步流程 + 变体验证 + Coverage Audit 表），SK-1/SK-2/SK-3 评估需同步修正；同时更新 `pre-recon-code.txt` 行号偏移。

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
| **LLM 检测引擎** | LLM Agent（Sink Hunter 子 agent）：glob 枚举模板 → 逐文件 Read → LLM 判定；业务代码 Grep 危险 API | LLM 仍跑（prompt 保留完整 SSRF 13 子类 + XSS 5 上下文），确定性 hint 经 `_static-dataflow-hints.txt` 注入 | **平手（LLM 层两版一致）** |
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

**SSRF 裁决**：⚠️ 确定性层仅覆盖 HTTP Client（~1/13 子类），但 **LLM prompt 层 13/13 完整保留且两版完全一致**。真实差距 = 确定性 hint 无 SSRF 非 HTTP 子类加持（降低可靠性，非完全缺失）。

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

**XSS 裁决**：⚠️ 确定性层仅 2 条 TS 规则，但 **LLM prompt 层 5/5 上下文完整保留且两版完全一致**。其他语言确定性层为零。

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
| **模板文件转义指令分析** | ✅ **强制两步流程**（`pre-recon-code.txt:129-136`）：Step 1 glob 枚举模板 → Step 2 逐文件区分 escaped（EJS `<%= %>`、Jinja2 `{{ }}`）vs unescaped（EJS `<%- %>`、Jinja2 `{{\|safe}}`） | ⚠️ **Prompt 层已恢复（`b3c58bd`），确定性层仍缺失**：① 确定性层不分析模板文件转义指令 ② ~~Prompt 层全删~~ **已在 `b3c58bd` 恢复**：两步流程（`:141-146`）+ 变体验证（`:148-149`）+ 审计表（`:293-301`） ③ `file_discovery.py:15-17` 有 10 种模板扩展名分类但**未接 `sink_detector`（断路）** | **差距缩小（仅确定性层 + file_discovery 断路）** ← v3 修正 |
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

---

## 5. 综合裁决

### 5.1 重构明确胜出

- **确定性检测引擎**：质的进步（47 条 AST 精确规则 vs 自然语言 prompt）
- **规则可维护性**：Pydantic 模型 + 单测 vs 自然语言
- **命令注入覆盖**：确定性层 Python 6 条 + PHP 4 条，为全语言最完整
- **结构化输出**：SinkCallSite Pydantic + SlotContext 类型系统

### 5.2 原始明确胜出

- **模板文件 Sink 确定性检测**：确定性层不分析模板转义指令 + `file_discovery` 断路 — **差距已缩小**（Prompt 层方法论已在 `b3c58bd` 恢复：两步流程 + 变体验证 + Coverage Audit）

### 5.3 确定性层差距（LLM 层平手）

以下差距**仅存在于确定性层**，LLM prompt 两版完全一致：
- SSRF（确定性仅 HTTP Client，LLM 13/13）
- XSS（确定性仅 2 条 TS，LLM 5/5 上下文）
- 路径穿越（确定性 0 条，LLM 有完整 LFI/RFI/PathTraversal）
- 文件读取（确定性 0 条，LLM 列举 fopen/readFile）

**影响**：确定性 hint 注入给 LLM 的结构化数据不含这些类别 → LLM 检测可靠性降低（靠自由发挥而非确定性 hint + 强制方法论），但非完全缺失。

### 5.4 两边均无

- **XXE**：两边 prompt 均无 XXE 专门覆盖 + 确定性层 0 条

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

### 重构 Shannon-py

| 功能 | 文件 |
|---|---|
| Sink 规则库（47 条） | `packages/core/src/shannon_core/code_index/sink_detector.py:67-198` |
| Sink 检测算法 | `packages/core/src/shannon_core/code_index/sink_detector.py:249-325` |
| 数据模型（SlotContext/SinkCallSite） | `packages/core/src/shannon_core/code_index/parameter_models.py` |
| 文件发现（模板/schema） | `packages/core/src/shannon_core/code_index/file_discovery.py` |
| LLM prompt SSRF（13 子类） | `prompts/pre-recon-code.txt:340-432` |
| LLM prompt XSS（5 上下文） | `prompts/pre-recon-code.txt:306-339` |
| 模板分析方法论（两步流程+变体验证） | `prompts/pre-recon-code.txt:141-149` |
| Template Coverage Audit | `prompts/pre-recon-code.txt:293-301` |
| LFI/RFI/PathTraversal | `prompts/vuln-injection.txt:2/108/120/147/210-211` |
| recon Section 9 注入源 | `prompts/recon.txt:383-394` |

---

## 7. 交叉参考

- `docs/whitebox-refactoring-assessment.md` — 全维度评估（v7），本分析是其 §1 Sink 部分的代码级修正
- `docs/entry-point-gap-analysis.md` — v1 差距分析，本分析修正其 §2 的三处错误（C1/C2/C3）
