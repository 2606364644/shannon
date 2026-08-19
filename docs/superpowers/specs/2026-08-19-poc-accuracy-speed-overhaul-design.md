# PoC 准确性 + 速度治理设计（P0+P1）

> 日期：2026-08-19　分支：`feat/pre-scan-local-agent`　类型：**bug 修复 + 质量/性能重构**（报告增强层，黑白盒通用）
>
> 上游 spec：`2026-07-02-exploitable-poc-generation-design.md`（PoC 功能原始设计）、`2026-07-22-poc-deterministic-layered-design.md`（分层确定性化，本 spec 是其延续，并修正其落地实现的两处偏差：§4.2「缺路由进待补桶」未实现、checkpoint version 字段未校验）。

---

## 1. 背景与证据基线

5 个白盒扫描（NodeGoat / hk-user-view / delivery / hr / kol_mapping_service，2026-07-31 ~ 08-12，覆盖 Node.js、TS BFF、Go 后端）的 PoC 产物审计结论：

**准确性（P0/P1 问题，全部有实证）**：

| # | 问题 | 实证 |
|---|---|---|
| P0-1 | witness 自由文本（请求行/攻击步骤说明/带中文注解）整体塞进 fallback 参数 `id`/`q`/`url`，产出非法 HTTP 请求 | NodeGoat 19 条、hk 6、hr 2、kol 4——**恰是各扫描的全部「已确认」条目** |
| P0-2 | 置信虚标：`verdict=vulnerable` 无条件映 CONFIRMED，压过 `confidence=needs_review`，渲染「✓ 已确认可复现」 | hk 6、hr 2、kol 4 条 needs_review 虚标 |
| P0-3 | 路由/方法污染：`GET /` 塌缩（`path` 是函数流提不出路由，`_base_spec` 兜底 `/`）；path 混入 payload 残片 `,` `;` 全角 `）`；无效 method `LOCAL`；通配符 `*` 字面进请求 | NodeGoat 19 条塌缩；kol 全部 4 条「已确认」URL 带 `;`；hk 1 条 `LOCAL`、1 条 `,` |
| P0-4 | authz 成对 PoC 无鉴别力：legit/cross 请求逐字节相同（`:param` 不替换、body 全空、资源 ID 只在注释） | 4 扫描全部 88 条 authz |
| P1-5 | GN 轨 PoC 层净贡献≈0：3/4 扫描 GN 0 产出；delivery 4 条与 LLM 轨逐字节重复且 witness 未补回；kol gap-fill 0/3 全败（无重试） | 见各扫描 checkpoint / 事件日志 |
| P1-6 | 占位符泄漏：LLM guess 返回的 `witness_payload` / `${...}` / `{{...}}` 字面量进 header 值、JSON 键名、body 值 | 4 扫描共 26 处 |
| P1-7 | 渲染三连：Burp raw 不编码（空格/CJK 破坏请求行）；to_curl 单引号包裹（witness 含 `'` 即 shell 截断）；Content-Type 重复 13 处 + Host 重复 | 全扫描普遍 |

**速度（定量）**：PoC 阶段 6.1–13.2 min，其中 inj/xss/ssrf/authz 全部 0ms；**唯一大头是 auth 逐条串行 LLM**（NodeGoat 10.4min/12.6min = 82%；单条最差 5m12s = GLM 结构化输出 10 轮内空转；hr 一条 3m42s 跑完仍降级骨架）。

**两处 07-22 spec 实现偏差（本 spec 修正）**：
1. `build_template_spec` 对 inj/xss/ssrf 只检查 witness 非空即返回模板，`_base_spec` 里 `path or "/"` 静默兜底——**缺路由根本不进待补桶**（违背 07-22 §4.2「route 提取不到 → 进待补」的原设计），这是 `GET /` 塌缩的直接根因。
2. `_load_checkpoint` 不校验 `version`/`track` 字段——修复上线后对旧 deliverables 重跑永远命中旧错 spec，修复不生效。

## 2. 目标 / 非目标

### 目标

- **G1（P0-1）witness 契约与解析**：上游 prompt 加硬格式约束 + PoC 侧确定性解析（请求行/参数串/注解剥离），PoC 参数名与值来自 witness 自身结构，消灭「自由文本进参数」。
- **G2（P0-2）置信语义**：`needs_review` 不再映 CONFIRMED；白盒 CONFIRMED 文案改「已确认（静态判定）」，不再声称「可复现」。
- **G3（P0-3）确定性路由**：`entry_points.json` join（file+handler/line → method+route）+ 缺路由进待补桶（统一 inj/xss/ssrf 到分层路径）+ path/method 白名单 lint。
- **G4（P0-4）authz 鉴别力**：成对请求在真实请求位（path 段/body 字段）替换 `<OWNER_RESOURCE_ID>`/`<VICTIM_RESOURCE_ID>`；无资源对象时诚实降单请求+标注。
- **G5（P1-5/6）LLM 层加固**：gap-fill 有界重试、`file_key=None` prompt 修正、recon_ctx 裁剪、占位符黑名单 lint。
- **G6（P1-7）渲染合法**：curl 引号安全、Burp raw 最小编码、header 去重。
- **G7（速度）**：auth 并行化（cap+per-call 超时）、gap-fill 组并行；PoC 阶段目标 <3min（NodeGoat 规模重放）。
- **G8（配套）**：checkpoint version bump v2 且校验；相同请求去重合并（唯一率 57–72% → 100%）。

### 非目标

- **多步 PoC**（stored XSS「植入+触发」两步、`HttpRequestSpec.steps` 渲染）——P2 范畴，follow-up。
- **不碰判定链路**：chain_verdict / vuln agent prompt 的判定方法论 / merger 跨轨 dedup（O1 独立议题）——CLAUDE.md 铁律。
- **PoC 自动重放验证**（同上游非目标）。
- **PoC activity timeout 配置**（20min×3 维持；G7 落地后充裕）。
- **Web 报告页 PoC 展示层**。

## 3. 核心设计

### 3.1 witness 契约与解析（G1）

**上游契约（`prompts/vuln-injection.txt` / `vuln-xss.txt` / `vuln-ssrf.txt`）**——纯输出格式约束，不引任何确定性数据（守铁律）：

- `witness_payload`：**payload 值本身**——不含参数名前缀、不含请求行、不含说明文字/多行描述；投递上下文（哪个端点/参数、多步说明）写 `notes`。
- `path`：HTTP 可达时以 `METHOD /route` 开头（数据流描述续后）。

**PoC 侧确定性解析（新纯函数 `parse_witness(witness) -> WitnessParse`）**，优先级：

1. **请求行形态** `^(GET|POST|PUT|DELETE|PATCH)\s+(/\S+)`：解析出 method/path/query（query 按 `a=b&c=d` 全量展开），剩余说明文字 → note。治 `id=GET /api/v2/download-cer?uid=...`（hk）与 NodeGoat INJ-VULN-06。
2. **参数串形态**：整串为无空白 `a=b&c=d`（每个 key 合法标识符）→ 展开为参数 dict（placement 决定进 query 还是 body）。治 `firstName=<img...>&bankRouting=INVALID` 被塞单参数（NodeGoat XSS 全系）。
3. **纯值形态**（其余）：先剥尾部注解——末尾 `（...）`/`(...)` 且含 CJK 或触发词（触发/说明/注意/访问/提交）→ 剥离进 note；剩余为单参数值（现状行为）。

`WitnessParse{values: dict[str,str], method?, path?, note?}` 由 `_assemble` 统一消费（§3.2 统一后 inj/xss/ssrf 无独立模板路径）：有 `method/path` 则覆盖路由、参数集直接落位；`note` 合并进 spec.note。

### 3.2 确定性路由解析（G3）

**新 `RouteIndex`**（载入 `entry_points.json` 的 `adjudicated_entry_points`，报告层消费确定性产物，与 `parse_recon_endpoints` 同档）：

- **索引 A** `(file, handler) → (http_method, route)`：handler 名来自 GN 轨 `extract_gn_location` 第三段；LLM 轨 path 首 token `^(\w+)\(`。
- **索引 B** `file → [(line, http_method, route)]`：行号邻近兜底（LLM 轨 source 尾部 `@ (\S+\.\w+):(\d+)` 提供 file:line）。
- file 匹配用 basename 归一（两侧路径前缀不同）。

**统一分层路径（修实现偏差 ①）**：`build_template_spec` 的 inj/xss/ssrf 分支退役；`generate()` 对 inj/xss/ssrf 一律：

```
_extract_deterministic（含 RouteIndex join 补 method/path）
  → partial 完整（route+witness+param）？ → _assemble(partial, gap=None)  [0ms]
  → 否则 → gapped（按文件分组 gap-fill）
```

`_build_entry` 仅保留 authz（成对模板）/ auth（per-item LLM）两分支。authz/auth 行为不变（authz 见 §3.4 升级）。

### 3.3 输出 lint（G3/G5，写盘前统一关卡 `lint_spec`）

- **path 白名单**：`A-Za-z0-9-._~:/@%!$&+` + `<>`（占位符段）；**不含** `,` `;` `'` `*`（残片/通配即截）与全角字符；从首个非法字符截断（尾残片 `,` `;` 全角 `）` 与通配 `*` 即被截）；截断后不以 `/` 开头 → `/`。
- **method 白名单** `{GET,POST,PUT,DELETE,PATCH,HEAD,OPTIONS}`：非白名单（如 `LOCAL`）按 placement 推（body→POST，query→GET），note 记原值。
- **占位符黑名单**：值含 `witness_payload` 字面量 / `${...}` / `{{...}}` → 清空该字段 + note「LLM 模板占位符，需手工补全」；body 整体为模板串 → 骨架降级。
- **header 归一**：spec.headers 剔除 `Content-Type`/`Host`（渲染层为唯一来源，见 §3.6）。
- lint 后仍非法（path 空/method 缺）→ 骨架 + 标注，不阻塞其余。

### 3.4 authz 配对鉴别力（G4）

`_build_authz_pair` 升级：

- **资源参数发现**：path 模板段 `:(\w+)`；或 source/slot_type 提 `req.params.X` / body 字段名。
- **命中 path 段**：legit 以 `<OWNER_RESOURCE_ID>` 替换该段、cross 以 `<VICTIM_RESOURCE_ID>` 替换——两请求在请求位真实不同。body 字段命中同理（值替换占位符）。
- **无资源参数**（如 `POST /login`）：降单请求 + note「无资源对象，合法/越权请求无差异，配对无鉴别力」。

### 3.5 置信档语义（G2）

`classify_confidence`：

- 黑盒 `accepted_ids` → CONFIRMED（有重放证据）不变。
- 白盒 `verdict=vulnerable` **且** `confidence ∉ {needs_review, low}` → CONFIRMED（缺省 confidence 视为可确认，向后兼容）；`needs_review`/`low` → SUSPECTED。
- 文案：白盒 track CONFIRMED 渲染「✓ 已确认（静态判定）」，不再用「已确认可复现」；黑盒保持「✓ 已确认」。

### 3.6 渲染修复（G6）

- `to_curl`：保持单引号包裹，`'` → `'\''` 转义（POSIX 标准，可读性与安全兼得）。
- `to_burp_raw`：query 值**最小编码**——空格/非 ASCII/CR/LF 强制 percent-encode，其余符号保 raw（请求行合法且保留 payload 可读性）。
- header 去重：`Content-Type` 由 body 形态推导为唯一来源（既有逻辑）；`Host` 只由生成器输出；spec.headers 侧在 lint 归一时剔除两者（§3.3）。

### 3.7 相同请求合并（G8）

渲染前按 `(method, path, 规范化 query, body, headers)` 分组（authz 成对以整个 list 为 key）：组内 >1 → 合并一节，标题/概览 ID 逗连（`INJ-VULN-01/02/03`），detail 一份。概览表行数=findings 口径对齐头部计数。

### 3.8 gap-fill 加固（G5）

- **有界重试**：unparseable/缺 items 时重发一次（prompt 加 JSON-only 强化指令）；env `SUPERNOVA_POC_GAPFILL_RETRIES` 默认 1（非判定层，保守；对齐 fd203e12 chain_verdict 重试模式）。
- **`file_key=None` 桶修正**：prompt 不再自相矛盾（"Handler file: unknown … Read that file"）；`PartialSpec` 增 `source_file`（LLM 轨 source 自带 file:line 提取），逐条给文件路径，指示读对应文件。
- **recon_ctx 裁剪**：gapped 条目本就缺路由，改为按组内 `source_file` basename 去扩展名 token 匹配端点 path 段（如 `contributions.js` ↔ `/contributions/...`）；无命中 → 省略端点表 section（不再全量灌入）。

## 4. 速度设计（G7）

- **auth 并行**：`asyncio.Semaphore(SUPERNOVA_POC_CONCURRENCY=3)` + gather；per-call `asyncio.wait_for(SUPERNOVA_POC_AUTH_TIMEOUT_S=180)`，超时→骨架 + note「LLM 超时」（治 3m42s 白烧仍降级）。
- **gap-fill 组并行**：同 semaphore。
- **checkpoint 写盘**：`asyncio.Lock` 保护（并行完成即写，保住断点续传语义）。
- **进度行**：并行下按完成时序打（顺序乱可接受）。
- 预期：NodeGoat 规模 12.6min → **<3min**（auth 并行下限≈最慢单条 + 超时上界 3min；gap-fill 趋零）。

## 5. checkpoint v2（G8）

`version: 2`；`_load_checkpoint` 校验 `version==2` 且 `track` 匹配，不符 → 丢弃返回 `{}`（当全新跑）。旧 v1 文件自动失效，修复对存量 deliverables 重跑即生效。写盘结构不变。

## 6. 降级矩阵

| 故障 | 行为 |
|---|---|
| RouteIndex 缺失/entry_points 无路由 | join miss → gapped → gap-fill；再败 → 骨架/skip（既有 `_assemble` 语义），不劣于现状 |
| witness 解析三形态全不匹配 | 按纯值处理（现状）+ lint 兜底 |
| lint 后仍非法 | 骨架 + 标注 |
| authz 提不到资源参数 | 单请求 + 「无鉴别力」标注 |
| gap-fill 重试耗尽 | 该组条目降级骨架（既有），不阻塞 |
| auth per-call 超时 | 骨架 + 「LLM 超时」标注 |
| checkpoint v1 / track 不符 / 损坏 | 丢弃从头跑 |
| 并行 429 | 依赖 runner 既有重试；cap=3 保守 + env 可调（R2） |

## 7. 测试策略

只跑改动相关测试文件（CLAUDE.md §3）。fixtures 取自 5 扫描真实产物（裁剪脱敏）入 `packages/core/tests/fixtures/poc_overhaul/`。

**单测**：`parse_witness`（请求行/参数串/注解剥离/纯值/CRLF 边界）；`RouteIndex` join（handler 命中/行号邻近/basename 归一/miss）；`lint_spec`（path 截断 `,` `;` `）` `*`、method `LOCAL`→推、占位符三种形态、header 剔除）；authz 替换/无资源降级；`classify_confidence`（needs_review→suspected）；`to_curl` 引号转义；`to_burp_raw` 空格/CJK 编码、header 去重；去重合并；checkpoint v1 丢弃/v2 校验/track 不符；gap-fill 重试、`file_key=None` prompt、recon_ctx 裁剪；auth 并行（mock 各 2s×7 条 → 总时长 <2×串行、cap 生效、checkpoint 全量）。

**量化验收（fixture 重放断言）**：
- 路由解析率：NodeGoat fixture inj/xss/ssrf 非 `/` 塌缩 ≥90%
- 占位符泄漏 = 0（渲染文本 grep `witness_payload`/`${`/`{{`）
- 逐字节重复 curl 块 = 0
- needs_review 条目不出现「已确认」字样
- authz 成对：有资源参数 fixture 两请求不相等；无资源 fixture 单请求+note
- curl 行单引号配平；Burp 请求行无空格/非 ASCII
- 现有 `test_poc_generator.py` 全量保持绿

## 8. 关键不变量（守 CLAUDE.md 铁律）

- PoC 属**报告增强层**（07-22 §9 定性不变）：`RouteIndex` 读 `entry_points.json` 是报告层消费确定性产物（先例：`parse_recon_endpoints` 读 recon deliverable、fd203e12 builder 吃 entry_points map），**不喂 vuln 判定轨 prompt**。
- `vuln-*.txt` 改动仅限输出格式契约（witness/path 形态），不引确定性 hints；`test_static_dataflow_hints_decoupling.py` 锁定面不触碰。
- 不碰 chain_verdict / merger / verdict 轨；不覆写 `externally_exploitable`；真实凭证不持久化；Fix A/B 失败隔离不变；产物文件名/双格式/三档结构不变。
- 黑盒轨：渲染/置信/去重/lint 共享改动自动受益；accepted→CONFIRMED 语义不变。

## 9. 风险与开放问题

- **R1 RouteIndex 覆盖率**：依赖 entry_points 质量（kol Go 仓 http_route=0/53 → join 全 miss → gapped→LLM；行为不劣于现状的 `/` 塌缩）。
- **R2 并发 429**：delivery 实测见过 429；cap=3 保守起步，env 可调，必要时降 1。
- **R3 统一分层路径重构回归**：现有 63+ 测试 + 5 扫描 fixture 重放护栏；`build_template_spec` inj/xss/ssrf 分支退役在独立 task 完成。
- **R4 注解误剥**：payload 本体含括号文本（如 CRLF/注释类 payload）——剥离规则要求注解含 CJK/触发词且位于尾部，测试覆盖 ASCII-only payload 不被剥。
- **O1** stored XSS 多步语义、**O2** merger 跨轨 dedup（治 GN/LLM 重复条目的上游解）、**O3** fd203e12 未真机验证（本 spec 冒烟一并覆盖）——均 follow-up。
