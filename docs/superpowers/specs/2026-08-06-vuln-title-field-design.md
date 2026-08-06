# 漏洞 title 字段补齐设计

> 日期：2026-08-06
> 主题：给漏洞报告（WEB + Markdown）补回"一句话概括"标题字段，对齐原始 TS 项目。

## 背景

当前 PY 的漏洞条目在 WEB 页面和 Markdown 报告里只有编号（如 `INJ-VULN-01`），没有一句话概括"这个漏洞是什么"。原始 TS 项目的漏洞标题是结构化字段 `title`（由下游 LLM agent 生成），渲染成 `### INJ-VULN-01: 漏洞标题`。PY 移植时数据层漏掉了这个字段：`BaseVulnerability` / `ExploitVerdict` 都没有 title，确定性渲染器只输出裸 `### {ID}`，前端靠 `file:line` 启发式拼副标题打补丁。`report-executive.txt` 虽带过来了 title cleanup 规则，但上游从未喂过 title，规则一直失效。

本设计把 title 作为结构化字段补回数据链，覆盖白盒 / 黑盒 / 双轨（LLM 轨 + GitNexus 轨）。

## 目标

- 每条漏洞在 Markdown 报告里稳定渲染为 `### {ID}: {title}`，title 是描述性短语（如 `PostgreSQL SQL Injection via Coupon Validation`）。
- WEB 前端漏洞卡片展示 title。
- title 覆盖所有产出轨：LLM vuln agent（inj/xss/ssrf/auth/authz）、GitNexus chain_verdict（inj/xss/ssrf）、Authz GitNexus judge。
- 旧 session.json / queue.json（无 title 字段）读取不崩。

## 非目标

- 不改漏洞判定逻辑、不改召回、不改 severity/confidence 语义。
- 不给 `ExploitVerdict` 加 title 字段（黑盒 verdict 靠 queue 的 `id→title` map 关联）。
- 不重建确定性→LLM 轨 hints 桥梁（守 CLAUDE.md 铁律）。

## 关键架构事实（决定实现形态）

**PY 的 report-executive 是 self-Edit md agent，不是 collector。** TS 的 report-executive 用 `add_finding` collector（结构化 title 字段）；PY 没有 `add_finding` collector——report-executive 读已渲染好的 `comprehensive_security_assessment_report.md`，用 Edit 工具原地改 heading 文字。

因此"两阶段 title"在 PY 落地为：

1. **第一道（生成）**：各产出 agent 在结构化数据（queue.json）里填 title。
2. **渲染**：渲染器把 `### {ID}` 拼成 `### {ID}: {title}` 写进 md。
3. **第二道（cleanup）**：report-executive 读到 `### {ID}: {title}`，对弱标题 / 空标题就地 Edit 改写为描述性短语。

**title 的 SSOT 是 `queue.json` 的 `BaseVulnerability`。** 无论白盒 / 黑盒 / GitNexus 轨，漏洞最终都汇聚到 queue；所有渲染从 queue 取 title。

## 设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| title 为 None 时渲染器行为 | 输出裸 `### {ID}`，留给 report-executive 第二道补 | 信任 LLM 第二道产出高质量描述性 title；前端 MarkdownView 已有 `vulnPreview`（file:line）兜底；避免 fallback 拼凑的低质量标题干扰第二道 |
| title 是否强制 | `BaseVulnerability.title: str \| None = None`（字段可选，兼容旧数据）；vuln agent 的 `_vuln_output_schema` 把 title 加进 `required`（新数据强制 LLM 必给） | 兼容旧 + 强制新兼得 |
| 黑盒 exploit title 来源 | 从 queue 的 `id→title` map 查，verdict 不加字段 | exploit verdict 只有 `vulnerability_id`，title 已在 queue 里，避免双源 |

## 详细设计

### ① 数据模型

`packages/core/src/supernova_core/models/queue_schemas.py` 的 `BaseVulnerability` 加字段：

```python
class BaseVulnerability(BaseModel):
    ID: str
    vulnerability_type: str
    externally_exploitable: bool
    confidence: str
    title: str | None = None          # 新增：一句话概括
    notes: str | None = None
    source_track: str | None = None
    evidence_chain: str | None = None
    merge_source: str | None = None
```

5 个子类（`InjectionVulnerability` / `XssVulnerability` / `SsrfVulnerability` / `AuthVulnerability` / `AuthzVulnerability`）继承，无需各自声明。

`ExploitVerdict`（`exploit_verdict_schemas.py`）**不改**。

### ② Prompt — 第一道生成 title

**LLM vuln 轨（5 个 prompt）**：`prompts/vuln-injection.txt` / `vuln-xss.txt` / `vuln-ssrf.txt` / `vuln-auth.txt` / `vuln-authz.txt` 的 `<exploitation_queue_format>` 块，在 JSON 结构里 `ID` 之后加 `title` 字段：

```
"ID": "unique ID for each vulnerability (e.g., INJ-VULN-XX)",
"title": "one-line descriptive name encoding category + where it lives (e.g., 'SQL Injection via User Search (q param → raw query)')",
```

要求 LLM 给每条漏洞一句描述性标题（类别 + 位置），不得只写短分类标签。

**`_vuln_output_schema`**（`packages/whitebox/src/supernova_whitebox/pipeline/activities.py` 的 `_vuln_output_schema`，约 :161-179）：把 `title` 加进 `required`，强制 LLM 必给。

**GitNexus chain_verdict（inj/xss/ssrf）**：`packages/core/src/supernova_core/code_index/chain_verdict.py`
- `CHAIN_VERDICT_SCHEMA`（约 :44-54）：加 `"title": {"type": ["string", "null"]}`。
- `_VERDICT_PROMPT`（约 :65-87）：输出 JSON 模板加 `title`，要求给一句描述性标题（概括这条候选漏洞链的类别 + 位置，不论最终判定结果；non-vulnerable 链也进 queue，同样需要 title）。
- `ChainVerdict` dataclass（约 :107-113）：加 `title: str | None = None`。
- 3 个 builder（`vuln_chain_builders/injection_builder.py` / `xss_builder.py` / `ssrf_builder.py`，构造 `InjectionVulnerability(...)` 等处）：传 `title=verdict.title`。

**Authz GitNexus judge**：`prompts/authz_gitnexus_judge.txt` 的 output_format（约 :22-39）加 `title` 字段。

（auth 走纯 LLM 轨 `vuln-auth.txt`，无 GitNexus 轨，不涉及 chain_verdict。）

### ③ 渲染器 — 拼 `### {ID}: {title}`

**`packages/core/src/supernova_core/services/findings_renderer.py`** 5 个 render 函数（`render_injection_entry` / `render_xss_entry` / `render_ssrf_entry` / `render_auth_entry` / `render_authz_entry`，约 :101/:132/:153/:171/:193）：

```python
heading = f"### {vuln.ID}: {vuln.title}" if vuln.title else f"### {vuln.ID}"
lines = [heading, "", _M.get("summary")]
```

**`packages/core/src/supernova_core/renderers/exploit.py`**（约 :70/:79/:90/:104/:114，5 档 entry）：`### {v.vulnerability_id}` → 带 title。title 经 `id_to_title` map 查（map 由调用方传入）：

```python
title = id_to_title.get(v.vulnerability_id)
heading = f"### {v.vulnerability_id}: {title}" if title else f"### {v.vulnerability_id}"
```

**`packages/core/src/supernova_core/renderers/__init__.py`**（约 :49-64）：构建 `id_to_title`（仿现有 `id_to_type`，读 queue 的 `ID→title`），传给 `render_exploit`（扩展 `render_exploit` 签名加 `id_to_title` 参数，约 :120）。

### ④ report-executive — 第二道 cleanup

`prompts/report-executive.txt`（约 :102）的 title 规则微调措辞，明确：

- 渲染器已拼好 `### {ID}: {title}`，**必须保留 vulnerability ID 原样不变**（对齐 line 103 的 preserve ID 铁律）。
- 若 title 是弱标签（仅 `SSTI` / `Reflected` 这类短分类词）或冒号后为空（裸 `### {ID}`），就地 Edit 改写为基于该 finding 的 Vulnerable location + Overview 的描述性短语。

顺序铁律不变：`inject_attack_chains` / `inject_gitnexus_track_status` 仍在 report agent 之后跑（否则被 self-Edit 覆写）。

### ⑤ 前端

- `packages/web/frontend/src/lib/vuln-block.ts`（约 :112）：**已支持** `### ID — title` / `### ID: title` 解析，零改。
- `packages/web/frontend/src/api/types.ts` `Vulnerability` 接口（约 :186-202）：加 `title?: string`。
- `packages/web/frontend/src/components/VulnCard.tsx`（约 :48-49）：头部展示 ID + title；title 为空时退化到 `vulnerability_type`。
- `packages/web/frontend/src/routes/.../MarkdownView.tsx` 的 `vulnPreview`（file:line）fallback 保留——裸 ID 时的展示兜底。

### ⑥ 数据兼容

- 统一 queue load 点是 `VulnerabilityQueue.parse_lenient`（`queue_schemas.py` 约 :112-197）。`title` 缺失时 pydantic 默认 None，旧文件不崩。
- merge 的 `_clone_with_merge_fields`（`dual_track_merger.py` 约 :93-119）用 `model_dump()` → `model_validate`，title 自动随 dump 流转，三分支（both / llm-only / gitnexus-only）均透传，零额外改动。

## 测试策略（TDD）

- **`queue_schemas`**：title 字段读写；旧 queue JSON 无 title 字段时 `parse_lenient` 不崩且 title=None；merge 三分支 title 流转（both 取 LLM 轨 title、llm-only/gitnexus-only 各取自身 title）。
- **`findings_renderer`**：有 title 渲染 `### ID: title`；无 title 渲染裸 `### ID`。
- **`exploit` renderer**：`id_to_title` map 查询；有 title 拼 `### ID: title`；queue 无该 ID title 时裸 `### ID`。
- **`chain_verdict`**：`CHAIN_VERDICT_SCHEMA` 含 title；`ChainVerdict` dataclass 含 title；builder 把 `verdict.title` 透传到 finding。
- **prompt 锁定测试**：5 个 vuln prompt 的 `exploitation_queue_format` 含 `title` 字段定义（对齐现有 prompt 结构锁定测试模式）；守铁律测试（确定性产物不喂 LLM 轨 prompt）不破坏。
- **前端**：`VulnCard` 有 title 展示 title、无 title 退化 type；`vuln-block` 解析 `### ID: title`（已有覆盖，确认不回归）。

## 改动清单（文件级）

**core**
- `packages/core/src/supernova_core/models/queue_schemas.py` — `BaseVulnerability` + `title`
- `packages/core/src/supernova_core/code_index/chain_verdict.py` — schema + prompt + dataclass + title
- `packages/core/src/supernova_core/code_index/vuln_chain_builders/{injection,xss,ssrf}_builder.py` — 传 title
- `packages/core/src/supernova_core/services/findings_renderer.py` — 5 函数拼 title
- `packages/core/src/supernova_core/renderers/exploit.py` — 拼 title + 签名加 `id_to_title`
- `packages/core/src/supernova_core/renderers/__init__.py` — 构建 `id_to_title` map

**prompts**
- `prompts/vuln-{injection,xss,ssrf,auth,authz}.txt` — queue_format 加 title
- `prompts/authz_gitnexus_judge.txt` — output_format 加 title
- `prompts/report-executive.txt` — cleanup 措辞微调

**whitebox**
- `packages/whitebox/src/supernova_whitebox/pipeline/activities.py` — `_vuln_output_schema` required 加 title

**前端**
- `packages/web/frontend/src/api/types.ts` — `Vulnerability.title?`
- `packages/web/frontend/src/components/VulnCard.tsx` — 展示 title

**测试**
- core / whitebox / 前端对应测试文件（见测试策略）

## 真机生效前提

- 改 `packages/core` / `packages/whitebox` / prompts → 须 rebuild worker 镜像。
- 改前端 → 须 rebuild web 镜像。
