# GitNexus 轨 LLM 日志：层级归属与格式统一

- 日期：2026-07-02
- 状态：设计（待 review）
- 分支：`feat/py`
- 关联：
  - `2026-07-01-gitnexus-llm-progress-logging-design.md`（GitnexusLlmEvent / ProgressEmitter 链路本身，本 spec 只改其**显示层**，不动事件/计数/采样）
  - `2026-06-22-log-format-redesign.md`（DisplayEvent / `tag()` / `pad_rule` / `LABEL_WIDTH` 单一来源）
  - CLAUDE.md §1 双轨铁律（双轨平级、独立；本 spec 用视觉对偶表达这层关系）

## 1. 背景

`2026-07-01-gitnexus-llm-progress-logging-design.md` 落地了 GitNexus 轨 LLM 进度日志（`ProgressEmitter` → `log_gitnexus_progress` → `GitnexusLlmEvent` → dispatcher → rich/file renderer），消除了黑盒。但其**显示格式**有遗留问题，实战日志如下：

```
[23:17:11] GN-LLM  sink-discovery  1/7  · 0 so far
[23:18:54] GN-LLM  sink-discovery  done 7/7 → 0 soft sinks · 0 rule gaps · 0 timeouts
[23:19:36] GN-LLM  taint-analysis  done 1/1 → 0 taint_flows
```

对照（同段日志的其它行）：

```
[23:16:36] STEP   ○ 预检（环境 / 依赖就绪性）
[23:16:36] AGENT  ▶ pre-recon started (attempt 1)
[23:17:00] 💭 [Agent] Turn 2: 我将作为 Principal Engineer...
[23:19:36] INFO   GitNexus code-index：blocks=24, chains=8
```

三类问题：

1. **层级归属错位**。`events.py:124-127` 的 `GitnexusLlmEvent` 注释白纸黑字：*"与 LLM 轨 `LlmTurnEvent` 对偶"*——设计意图上它是 `💭 [Agent] Turn` 的对偶（LLM 活动），不是 `STEP` 的兄弟。`rich_renderer.py:28` 也把 `GN-LLM` 与 `LLM` 同设为 magenta。但它历史上走了 `[tag]` 结构标签栏，长得像 STEP，造成"语义属 LLM 族、形态属 STEP 族"的割裂。
2. **rich/file 两路不一致**。
   - progress 行：`rich_renderer.py:149` 是 `· {hits} so far`（**无 noun**）；`file_renderer.py:116` 却是 `· {hits} {noun} so far`（有 `_HITS_NOUN` 映射）。终端 `· 0 so far` 不说清"0"是 soft sinks 还是 taint_flows。
   - note 行：rich 用 `⚠`（`rich_renderer.py:144`），file 用 `!`（`file_renderer.py:114`）。
   - file 的 GN-LLM 还绕过 `tag()` 硬编码 `tag_label = "[GN-LLM]"`（`file_renderer.py:107`），不走统一补齐机制。
3. **标签列不对齐**。`formatters.py:211` `LABEL_WIDTH = 5`，`GN-LLM` 是 6 字符，`tag('GN-LLM')` 不补，导致 GN-LLM 行 body 比其它行右移 1 字符。`WARNING`（7 字符）同理溢出。

## 2. 目标 / 非目标

**目标**

- 把 GitNexus 轨 LLM 日志**归入 LLM 活动族**（与 `💭 [Agent] Turn` 并列），用符号 + 颜色的**双重对偶**表达 CLAUDE.md §1 的双轨平级关系。
- rich/file 两路显示**完全一致**（仅前缀形态不同），body 单一来源。
- 顺手修掉标签列错位（`LABEL_WIDTH` 调到容纳最长标签）。
- 进度行信息自洽（计数对象明确）。

**非目标**

- 不动 `GitnexusLlmEvent` / `ProgressEmitter` / `progress_cb` / `log_gitnexus_progress` 的**字段、签名、计数/采样语义**（只改渲染层措辞）。
- 不动 ERROR / TOOL / LLM / RESUME / SUMMARY 行（它们绕过 `tag()` 硬编码，属另一批，本 spec 不扩散）。
- 不动 summary 的 detail 文案（`0 soft sinks · 0 rule gaps · 0 timeouts` 由调用方拼）。
- 不改 phase 名（sink-discovery 等）；只可能显示 pad（本 spec 决定不做，见 §5）。
- 守 CLAUDE.md §1：不改 LLM 轨，不喂确定性产物给 LLM 轨。

## 3. 设计决策（已与用户确认）

| # | 维度 | 决策 | 理由 |
|---|------|------|------|
| D1 | **层级归属** | GN-LLM 退出结构标签栏，归入 LLM 活动族 | `events.py:124` 注释已明示是 `LlmTurnEvent` 对偶；颜色同 magenta 亦佐证。归族后视觉传达"双 LLM 轨并行"架构语义。 |
| D2 | **符号 + 颜色对偶** | agent Turn = `💭 magenta`（暖/思考）｜GitNexus = `🔍 cyan`（冷/扫描分析） | 双轨**平级**（CLAUDE.md §1）→ 必须**同档常规色**，不可一 bright 一正常（暗示主次）。`🔍` 与 `💭` 同为 emoji 风格统一（`⚙` 否决：单色 dingbat 与 emoji 字重不搭）。冷暖对偶 + 符号语义对偶（思考 ↔ 扫描分析，贴 GitNexus"代码索引 + 判定"）。 |
| D3 | **progress 措辞** | `{done}/{total}  · {hits} {noun}`（加 noun、去 so far） | 终端 `· 0 so far` 含义模糊；加 noun（sinks/sources/taint_flows）自洽。去 so far：`done<total` 已暗含进行中，summary 行另有完整汇总。 |
| D4 | **标签列宽度** | `LABEL_WIDTH = 5 → 7` | 容纳最长标签 `WARNING`(7)，所有走 `tag()` 的标签（PHASE/STEP/AGENT/INFO/WARNING）`ljust(7)` 对齐，顺手修 INFO(4)/WARNING(7) pre-existing 错位。GN-LLM 归族后退出标签栏，这批更干净。 |
| D5 | **body / noun 共享** | `_HITS_NOUN` 上提为 `formatters.gitnexus_hits_noun(phase)`；新增 `formatters.gitnexus_body(e)` | 对齐现有 `step_body`/`agent_body`/`phase_body` 模式（formatters.py:222+），rich/file 共用单一来源，消除"rich 漏 noun"根因。 |
| D6 | **grep 锚点** | `GN-LLM` → `[GitNexus]` | 归族后无 GN-LLM 标签；`[GitNexus]` 是 rich/file 两路共有的稳定子串，可 grep。更新 events 注释 + memory。 |

## 4. 详细规格

### 4.1 rich 渲染（`rich_renderer.py`）

`_render_gitnexus`（替换 `:133-149`；`gitnexus_body` 加入文件顶部既有 formatters import）：

```python
def _render_gitnexus(self, e) -> None:
    self._console.print(
        f"[{e.timestamp}] [cyan]🔍 [GitNexus] {gitnexus_body(e)}[/]",
        highlight=False)
```

对照 `_render_llm`（`:127-131`，**不改**）：

```python
f"[{e.timestamp}] [magenta]💭 {agent_prefix(e.agent_name)} Turn {e.turn}: {line}[/]"
```

→ 两行同 emoji 风格、同 `[频道]` 前缀结构、`magenta`↔`cyan` 冷暖对偶、`💭`↔`🔍` 符号对偶。

### 4.2 file 渲染（`file_renderer.py`）

`_gitnexus`（替换 `:101-117`，删去 `_HITS_NOUN` 与硬编码 `tag_label`；`gitnexus_body` 加入顶部既有 formatters import）：

```python
def _gitnexus(self, e) -> str:
    return f"[{e.timestamp}] [LLM]   [GitNexus] {gitnexus_body(e)}\n"
```

对照 `_llm`（`:96-99`，**不改**）：`f"[{e.timestamp}] [LLM]   {who}: Turn {e.turn}: {content}\n"` → 同 `[LLM]` 栏 + `[频道]` 结构。

> file 端不用 emoji（与既有 `_llm` 一致：file 的 LLM 族走 `[LLM]` 标签栏无 emoji，rich 的 LLM 族走 emoji）。两路各自内部一致。

### 4.3 formatters 新增（`formatters.py`）

```python
_GITNEXUS_HITS_NOUN = {
    "sink-discovery": "sinks",
    "source-discovery": "sources",
    "taint-analysis": "taint_flows",
    "chain-verdict": "vulnerable",
}


def gitnexus_hits_noun(phase: str) -> str:
    """progress 计数行的计数对象名；未知 phase 兜底 'hits'。"""
    return _GITNEXUS_HITS_NOUN.get(phase, "hits")


def gitnexus_body(e) -> str:
    """GitNexus LLM 行正文（纯文本，rich/file 共用）。

    对齐 step_body/agent_body 模式：四种 kind 的措辞在此单一来源。
    """
    if e.kind == "hit":
        return f"{e.phase}  ✓ {e.detail}"
    if e.kind == "summary":
        return f"{e.phase}  done {e.done}/{e.total} → {e.detail}"
    if e.kind == "note":
        return f"{e.phase}  ⚠ {e.detail}"
    return f"{e.phase}  {e.done}/{e.total}  · {e.hits} {gitnexus_hits_noun(e.phase)}"
```

`LABEL_WIDTH`（`:211`）：`5 → 7`。

### 4.4 body 四 kind 格式（rich = file，单一来源）

| kind | body |
|------|------|
| progress | `{phase}  {done}/{total}  · {hits} {noun}` |
| summary | `{phase}  done {done}/{total} → {detail}` |
| hit | `{phase}  ✓ {detail}` |
| note | `{phase}  ⚠ {detail}` |

### 4.5 归族后整组日志样貌

终端（rich，去 markup 示意）：

```
[23:16:36] PHASE   Starting pre-recon ──────────────
[23:16:36] STEP    ○ 构建调用图与代码索引
[23:16:36] AGENT   ▶ pre-recon started (attempt 1)
[23:17:00] 💭 [Agent] Turn 2: 我将作为 Principal Engineer...
[23:17:11] 🔍 [GitNexus] sink-discovery  1/7  · 0 sinks
[23:18:54] 🔍 [GitNexus] sink-discovery  done 7/7 → 0 soft sinks · 0 rule gaps · 0 timeouts
[23:19:36] 🔍 [GitNexus] taint-analysis  done 1/1 → 0 taint_flows
[23:19:36] INFO    GitNexus code-index：blocks=24, chains=8
```

workflow.log（file）：

```
[2026-07-02 23:17:11] [LLM]   [GitNexus] sink-discovery  1/7  · 0 sinks
[2026-07-02 23:18:54] [LLM]   [GitNexus] sink-discovery  done 7/7 → 0 soft sinks · 0 rule gaps · 0 timeouts
```

## 5. 取舍记录

- **cyan 与 STEP/INFO 撞色**：D2 选 cyan（平级对偶的最优解）必然撞 STEP/PHASE/INFO 的 cyan。范围内（不动其它行颜色）无法回避，靠 `🔍` + `[GitNexus]` 前缀区分（STEP 是 `○/✓` 标签栏无 emoji）。彻底解套需把 STEP/INFO 换色，超出本 spec 范围，留作后续。
- **phase 列 pad 不做**：曾考虑把 phase 名 `ljust(16)` 让 done/total 列对齐。归族后 `[GitNexus]` 前缀已做视觉分隔，phase pad 边际收益下降且行长增加 → YAGNI，不做。
- **`category` 保持 `"GN-LLM"`**：`workflow_logger.py:185` 的 `category="GN-LLM"` 是内部 subtype，renderer 不依赖它显示（按 event 类型 `isinstance` 分发）。保持最小改动、不破坏可能断言 category 的测试。`STYLE_MAP` 的 `"GN-LLM": "magenta"`（`rich_renderer.py:28`）归族后不再被 `_render_gitnexus` 查（直接写 `[cyan]`），成为 dead entry，可顺手删（可选，非必须）。
- **符号演进**：`⚙`（否决，单色 dingbat 与 emoji `💭` 字重不搭）→ `🔍`（采纳，emoji 风格统一 + 扫描语义贴 GitNexus）。
- **颜色演进**：`bright_cyan`（否决，一亮一常规暗示主次，违双轨平级）→ `cyan`（采纳，常规档平级冷暖对偶）。

## 6. 不改边界（YAGNI / 守铁律）

- ERROR / TOOL / LLM / RESUME / SUMMARY 行的 renderer 分支不动。
- `GitnexusLlmEvent` 字段、`ProgressEmitter`/`progress_cb`/`log_gitnexus_progress` 签名与计数/采样语义不动。
- summary detail 文案（调用方拼）不动。
- LLM 轨（vuln agent prompt / 行为）不动。
- `cli/progress.py` spinner 不动。

## 7. 测试策略

只跑改动相关测试文件（CLAUDE.md「测试陷阱」：勿广跑全套）。

- `packages/core/tests/display/test_rich_renderer.py`：
  - 现有 GN-LLM 用例（搜 `GN-LLM` / `sink-discovery`）断言改为 `🔍 [GitNexus]` + cyan、四种 kind body、noun 注入、note `⚠`。
  - 新增：`💭 [Agent] Turn` 与 `🔍 [GitNexus]` 同 emoji 风格、`magenta`/`cyan` 对偶的回归锚点。
- `packages/core/tests/display/test_file_renderer.py`：
  - 断言改为 `[LLM]   [GitNexus] {body}`、四种 kind、note `⚠`（原 `!`）。
- 标签列对齐回归（新增或并入上两文件）：PHASE/STEP/AGENT/INFO/WARNING 行 body 起点同列（`LABEL_WIDTH=7`），`WARNING`(7) 不再溢出。
- `packages/core/tests/audit/test_workflow_logger_gitnexus.py`：`log_gitnexus_progress` 仍发 `GitnexusLlmEvent(category="GN-LLM", ...)`，字段断言不变，跑通即可。
- `packages/core/tests/code_index/test_progress.py`：`ProgressEmitter` 不变，跑通即可。

## 8. 实现注记（文件 : 行号）

| 文件 | 改动 |
|------|------|
| `packages/core/src/shannon_core/display/formatters.py:211` | `LABEL_WIDTH = 5 → 7` |
| `packages/core/src/shannon_core/display/formatters.py`（新增） | `_GITNEXUS_HITS_NOUN` + `gitnexus_hits_noun()` + `gitnexus_body()` |
| `packages/core/src/shannon_core/display/rich_renderer.py:133-149` | `_render_gitnexus` 改为 `🔍 [GitNexus] {gitnexus_body(e)}` + `[cyan]` |
| `packages/core/src/shannon_core/display/rich_renderer.py:28` | `STYLE_MAP` 的 `"GN-LLM"` 项可删（dead，可选） |
| `packages/core/src/shannon_core/display/file_renderer.py:101-117` | `_gitnexus` 改为 `[LLM]   [GitNexus] {gitnexus_body(e)}`，删 `_HITS_NOUN` |
| `packages/core/src/shannon_core/display/events.py:124-127` | 注释更新：去掉"专属标签 GN-LLM 便于 grep"，说明归 LLM 族、grep 锚点 `[GitNexus]` |
| memory `gitnexus-llm-progress-logging-status.md` | 更新：grep 锚点 `GN-LLM`→`[GitNexus]`、符号 `🔍`、颜色 `cyan`、与 agent Turn 对偶 |

`GitnexusLlmEvent.category`（`events.py` / `workflow_logger.py:185`）保持 `"GN-LLM"` 不变。
