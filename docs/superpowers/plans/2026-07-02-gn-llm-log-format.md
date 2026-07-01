# GitNexus LLM 日志层级归属与格式统一 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 GitNexus 轨 LLM 日志从"伪 STEP 标签栏"重定为"LLM 活动族"（`🔍 [GitNexus] cyan` ↔ `💭 [Agent] magenta` 对偶），统一 rich/file 两路、修标签列错位。

**Architecture:** 纯显示层改动。新增 `formatters.gitnexus_body()` + `gitnexus_hits_noun()` 作 rich/file 共用的单一来源（对齐既有 `step_body`/`agent_body` 模式）；rich_renderer 走 emoji 前缀（`🔍 [GitNexus]`），file_renderer 走 `[LLM]` 标签栏（`[LLM]   [GitNexus]`，与既有 `_llm` 行一致）；`LABEL_WIDTH` 5→7 修标签列错位。事件层（`GitnexusLlmEvent`/`ProgressEmitter`/`log_gitnexus_progress`）零改动。

**Tech Stack:** Python 3、pytest（asyncio auto mode）、rich（`[color]markup[/]`）、DisplayEvent→dispatcher→renderer 架构。

## Global Constraints

（每个 task 的要求都隐含以下约束，来自 spec `2026-07-02-gn-llm-log-format-design.md`）

- **守 CLAUDE.md §1 双轨铁律**：不改 LLM 轨（vuln agent prompt/行为），不喂确定性产物给 LLM 轨。
- **只跑改动相关测试文件**（CLAUDE.md「测试陷阱」：勿广跑全套/全包，会卡 Temporal/网络慢测试）。
- **事件层零改动**：`GitnexusLlmEvent` 字段、`ProgressEmitter`/`progress_cb`/`log_gitnexus_progress` 签名与计数/采样语义不动；`category` 保持 `"GN-LLM"`。
- **rich/file body 单一来源**：四种 kind 措辞只在 `gitnexus_body()` 一处。
- **符号/颜色约定**：rich = `🔍 [GitNexus]` + `[cyan]`（对偶 `💭 [Agent]` + `[magenta]`，后者不改）；file = `[LLM]   [GitNexus] {body}`（无 emoji，与既有 `_llm` 一致）。
- **file 端断言是精确字符串相等**：改格式时新串必须逐字符精确（含空格数）。
- 当前分支 `feat/py`；commit 沿用此分支（项目惯例）。

## File Structure

| 文件 | 责任 | 本计划改动 |
|------|------|-----------|
| `packages/core/src/shannon_core/display/formatters.py` | rich/file 共享的纯文本格式化（`tag`/`step_body`/`agent_body`...） | 新增 `_GITNEXUS_HITS_NOUN` + `gitnexus_hits_noun()` + `gitnexus_body()`；`LABEL_WIDTH` 5→7 |
| `packages/core/src/shannon_core/display/rich_renderer.py` | 终端渲染（`[color]markup[/]`） | `_render_gitnexus` 改 `🔍 [GitNexus]`+cyan；顶部 import；删 STYLE_MAP dead 项 |
| `packages/core/src/shannon_core/display/file_renderer.py` | workflow.log 纯文本渲染 | `_gitnexus` 改 `[LLM]   [GitNexus]`；删 `_HITS_NOUN`；顶部 import |
| `packages/core/src/shannon_core/display/events.py` | DisplayEvent 数据类 | 仅 `GitnexusLlmEvent` docstring 更新（grep 锚点） |
| `packages/core/tests/display/test_formatters.py` | formatters 单元测试 | 新增 gitnexus_body/noun 测试；改 tag/LABEL_WIDTH 断言 |
| `packages/core/tests/display/test_rich_renderer.py` | rich 渲染测试 | 改 4 个 GN-LLM 断言（🔍/cyan/noun）；改 INFO 断言 |
| `packages/core/tests/display/test_file_renderer.py` | file 渲染测试 | 改 5 个 GN-LLM 精确断言；改 STEP/PHASE/AGENT/INFO 标签栏断言 |
| memory `gitnexus-llm-progress-logging-status.md` | 进度日志状态备忘 | grep 锚点 + 符号/颜色更新 |

---

## Task 1: 新增 `gitnexus_body()` + `gitnexus_hits_noun()`（formatters 共享层）

**Files:**
- Modify: `packages/core/src/shannon_core/display/formatters.py`（末尾，`agent_body` 之后）
- Test: `packages/core/tests/display/test_formatters.py`（末尾追加）

**Interfaces:**
- Produces: `gitnexus_body(e: GitnexusLlmEvent) -> str`（四种 kind 的纯文本 body，rich/file 共用）；`gitnexus_hits_noun(phase: str) -> str`（progress 计数对象名，未知兜底 `"hits"`）。Task 2/3 的 renderer 调用这两个函数。

- [ ] **Step 1: 写失败测试**（追加到 `test_formatters.py` 末尾）

```python
from shannon_core.display.formatters import gitnexus_body, gitnexus_hits_noun
from shannon_core.display.events import GitnexusLlmEvent


def _gn_evt(kind, **kw):
    base = dict(timestamp="t", category="GN-LLM", phase="sink-discovery",
                kind=kind, done=10, total=87, hits=3)
    base.update(kw)
    return GitnexusLlmEvent(**base)


def test_gitnexus_hits_noun_known_phases():
    assert gitnexus_hits_noun("sink-discovery") == "sinks"
    assert gitnexus_hits_noun("source-discovery") == "sources"
    assert gitnexus_hits_noun("taint-analysis") == "taint_flows"
    assert gitnexus_hits_noun("chain-verdict") == "vulnerable"


def test_gitnexus_hits_noun_unknown_falls_back():
    assert gitnexus_hits_noun("mystery-phase") == "hits"


def test_gitnexus_body_progress_includes_noun_no_so_far():
    assert gitnexus_body(_gn_evt("progress")) == "sink-discovery  10/87  · 3 sinks"


def test_gitnexus_body_progress_noun_varies_by_phase():
    e = _gn_evt("progress", phase="chain-verdict", hits=2, done=10, total=34)
    assert gitnexus_body(e) == "chain-verdict  10/34  · 2 vulnerable"


def test_gitnexus_body_hit_shows_checkmark_and_detail():
    e = _gn_evt("hit", detail="'pg.executeQuery' @ src/api/users.py:42 slot=args")
    assert gitnexus_body(e) == "sink-discovery  ✓ 'pg.executeQuery' @ src/api/users.py:42 slot=args"


def test_gitnexus_body_summary_shows_done_arrow_detail():
    e = _gn_evt("summary", done=87, hits=12,
                detail="12 soft sinks · 5 rule gaps · 2 timeouts")
    assert gitnexus_body(e) == "sink-discovery  done 87/87 → 12 soft sinks · 5 rule gaps · 2 timeouts"


def test_gitnexus_body_note_shows_warn_and_detail():
    e = _gn_evt("note", detail="src/api/users.py: timed out (>60s), skipped")
    assert gitnexus_body(e) == "sink-discovery  ⚠ src/api/users.py: timed out (>60s), skipped"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/core/tests/display/test_formatters.py -v -k gitnexus`
Expected: FAIL — `ImportError: cannot import name 'gitnexus_body'`

- [ ] **Step 3: 实现**（追加到 `formatters.py` 末尾，`agent_body` 函数之后）

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
    progress 加 noun（sinks/sources/taint_flows/vulnerable），去 so far。
    """
    if e.kind == "hit":
        return f"{e.phase}  ✓ {e.detail}"
    if e.kind == "summary":
        return f"{e.phase}  done {e.done}/{e.total} → {e.detail}"
    if e.kind == "note":
        return f"{e.phase}  ⚠ {e.detail}"
    return f"{e.phase}  {e.done}/{e.total}  · {e.hits} {gitnexus_hits_noun(e.phase)}"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest packages/core/tests/display/test_formatters.py -v -k gitnexus`
Expected: PASS（7 passed）

- [ ] **Step 5: 跑 formatters 全量确认无回归**

Run: `pytest packages/core/tests/display/test_formatters.py -v`
Expected: PASS（原有 + 新增全绿）

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/display/formatters.py packages/core/tests/display/test_formatters.py
git commit -m "feat(display): 新增 gitnexus_body/gitnexus_hits_noun 作 rich/file 共享 body 单一来源"
```

---

## Task 2: rich renderer 归 LLM 活动族（`🔍 [GitNexus]` + cyan）

**Files:**
- Modify: `packages/core/src/shannon_core/display/rich_renderer.py:22-31`（STYLE_MAP）、`11-15`（顶部 import）、`133-149`（`_render_gitnexus`）
- Test: `packages/core/tests/display/test_rich_renderer.py:348-414`（4 个 GN-LLM 用例 + 组注释）

**Interfaces:**
- Consumes: `gitnexus_body(e)`（Task 1 产出）

- [ ] **Step 1: 改测试断言**（`test_rich_renderer.py`）

把 `:348` 组注释改为：
```python
# --- GitnexusLlmEvent (归 LLM 活动族: 🔍 [GitNexus] cyan, 对偶 💭 [Agent] magenta) ---
```

替换 `test_rich_gitnexus_progress_uses_magenta_and_gn_llm_tag`（`:350`）整函数为：
```python
async def test_rich_gitnexus_progress_uses_cyan_gitnexus_prefix_and_noun():
    from shannon_core.display.rich_renderer import RichConsoleRenderer
    from shannon_core.display.events import GitnexusLlmEvent
    from unittest.mock import MagicMock
    console = MagicMock()
    await RichConsoleRenderer(console=console).render(GitnexusLlmEvent(
        timestamp="2026-07-01 14:32:05", category="GN-LLM",
        phase="sink-discovery", kind="progress", done=10, total=87, hits=3))
    printed = console.print.call_args.args[0]
    assert "🔍 [GitNexus]" in printed   # 归 LLM 族前缀
    assert "cyan" in printed            # 冷暖对偶（agent Turn=magenta）
    assert "magenta" not in printed     # 不再与 agent Turn 同色
    assert "sink-discovery" in printed
    assert "10/87" in printed
    assert "3 sinks" in printed         # 加 noun
    assert "so far" not in printed      # 去 so far
```

`test_rich_gitnexus_hit_shows_checkmark_and_detail`（`:366`）末三行断言改为：
```python
    assert "✓" in printed
    assert "pg.executeQuery" in printed
    assert "cyan" in printed
    assert "magenta" not in printed
```
（删去原来的 `assert "magenta" in printed`，换成上面两行）

`test_rich_gitnexus_summary_shows_done_arrow_detail`（`:381`）在现有断言后追加一行：
```python
    assert "done 87/87" in printed
    assert "→" in printed
    assert "12 soft sinks" in printed
    assert "cyan" in printed            # 新增：归族后 cyan
```

`test_rich_gitnexus_note_shows_warn_symbol_and_detail`（`:395`）把 `assert "magenta" in printed` 改为：
```python
    assert "⚠" in printed
    assert "✓" not in printed
    assert "timed out" in printed
    assert "cyan" in printed            # 改：原 "magenta" in printed
    assert "sink-discovery" in printed
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/core/tests/display/test_rich_renderer.py -v -k gitnexus`
Expected: FAIL — 断言 `"🔍 [GitNexus]"` / `"cyan"` / `"magenta" not in printed` 不满足（实现仍输出 `GN-LLM` magenta）

- [ ] **Step 3: 顶部 import 加 `gitnexus_body`**

`rich_renderer.py:11-15` 现有：
```python
from shannon_core.display.formatters import (
    agent_body, agent_prefix, format_duration,
    format_error_block, humanize_tool_call, first_nonempty_line,
    pad_rule, phase_body, step_body, tag,
)
```
改为（按字母序插入 `gitnexus_body`）：
```python
from shannon_core.display.formatters import (
    agent_body, agent_prefix, format_duration,
    format_error_block, gitnexus_body, humanize_tool_call, first_nonempty_line,
    pad_rule, phase_body, step_body, tag,
)
```

- [ ] **Step 4: 删 STYLE_MAP dead 项**

`rich_renderer.py:23-31` 的 `STYLE_MAP` 删去 `"GN-LLM": "magenta",` 一行（归族后 `_render_gitnexus` 直接写 `[cyan]`，不再查此表）。结果：
```python
    STYLE_MAP = {
        "PHASE": "bold cyan",
        "AGENT": "blue",
        "TOOL": "yellow",
        "LLM": "magenta",
        "ERROR": "bold red",
        "RESUME": "dim yellow",
    }
```

- [ ] **Step 5: 重写 `_render_gitnexus`**

替换 `rich_renderer.py:133-149` 整函数为：
```python
    def _render_gitnexus(self, e) -> None:
        self._console.print(
            f"[{e.timestamp}] [cyan]🔍 [GitNexus] {gitnexus_body(e)}[/]",
            highlight=False)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest packages/core/tests/display/test_rich_renderer.py -v -k gitnexus`
Expected: PASS（4 passed）

- [ ] **Step 7: 跑 rich 全量确认无回归**

Run: `pytest packages/core/tests/display/test_rich_renderer.py -v`
Expected: PASS（注意：`test_rich_renderer_info_event_info_level_cyan` 仍 pass——Task 4 才动 LABEL_WIDTH，当前仍是 5，`"INFO " in printed` 成立）

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/shannon_core/display/rich_renderer.py packages/core/tests/display/test_rich_renderer.py
git commit -m "refactor(display): rich GitNexus 行归 LLM 族 🔍[GitNexus] cyan 对偶 💭[Agent] magenta"
```

---

## Task 3: file renderer 归 `[LLM]` 栏（`[LLM]   [GitNexus]`）

**Files:**
- Modify: `packages/core/src/shannon_core/display/file_renderer.py:9-12`（顶部 import）、`101-117`（`_gitnexus` + 删 `_HITS_NOUN`）
- Test: `packages/core/tests/display/test_file_renderer.py:274-331`（5 个 GN-LLM 精确断言 + 组注释）

**Interfaces:**
- Consumes: `gitnexus_body(e)`（Task 1 产出）

- [ ] **Step 1: 改测试断言**（`test_file_renderer.py`）

把 `:274` 组注释改为：
```python
# --- GitnexusLlmEvent (归 LLM 族: [LLM]   [GitNexus], 对偶 _llm 的 [LLM]   [Agent]) ---
```

`test_gitnexus_progress_line`（`:293`）改为：
```python
async def test_gitnexus_progress_line():
    out = await _gn_render(_gn_evt("progress"))
    assert out == (
        "[2026-07-01 14:32:05] [LLM]   [GitNexus] sink-discovery  10/87  · 3 sinks\n")
```

`test_gitnexus_hit_line`（`:299`）改为：
```python
async def test_gitnexus_hit_line():
    e = _gn_evt("hit", done=5, hits=1,
                detail="'pg.executeQuery' @ src/api/users.py:42 slot=args")
    out = await _gn_render(e)
    assert out == (
        "[2026-07-01 14:32:05] [LLM]   [GitNexus] sink-discovery  ✓ 'pg.executeQuery' "
        "@ src/api/users.py:42 slot=args\n")
```

`test_gitnexus_summary_line`（`:308`）改为：
```python
async def test_gitnexus_summary_line():
    e = _gn_evt("summary", done=87, hits=12,
                detail="12 soft sinks · 5 rule gaps · 2 timeouts")
    out = await _gn_render(e)
    assert out == (
        "[2026-07-01 14:32:05] [LLM]   [GitNexus] sink-discovery  done 87/87 → "
        "12 soft sinks · 5 rule gaps · 2 timeouts\n")
```

`test_gitnexus_progress_noun_varies_by_phase`（`:317`）改为：
```python
async def test_gitnexus_progress_noun_varies_by_phase():
    e = _gn_evt("progress", phase="chain-verdict", hits=2, done=10, total=34)
    out = await _gn_render(e)
    assert "· 2 vulnerable" in out      # 去 so far
```

`test_gitnexus_note_line`（`:323`，连 docstring 里的 `!` 描述一起改）改为：
```python
async def test_gitnexus_note_line():
    """note 行: per-skip timeout/error 诊断, 用 ⚠ 区别 hit 的 ✓(与 rich 一致)。"""
    e = _gn_evt("note", done=5, hits=1,
                detail="src/api/users.py:raw_query: timed out (>60s), skipped")
    out = await _gn_render(e)
    assert out == (
        "[2026-07-01 14:32:05] [LLM]   [GitNexus] sink-discovery  ⚠ "
        "src/api/users.py:raw_query: timed out (>60s), skipped\n")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest packages/core/tests/display/test_file_renderer.py -v -k gitnexus`
Expected: FAIL — 精确串不匹配（实现仍输出 `[GN-LLM] ... so far` / `! `）

- [ ] **Step 3: 顶部 import 加 `gitnexus_body`**

`file_renderer.py:9-12` 现有：
```python
from shannon_core.display.formatters import (
    agent_body, agent_title, format_duration, format_error_block,
    humanize_tool_call, phase_body, step_body, tag,
)
```
改为：
```python
from shannon_core.display.formatters import (
    agent_body, agent_title, format_duration, format_error_block,
    gitnexus_body, humanize_tool_call, phase_body, step_body, tag,
)
```

- [ ] **Step 4: 重写 `_gitnexus`，删 `_HITS_NOUN`**

替换 `file_renderer.py:101-117`（含 `_HITS_NOUN` 字典 + `_gitnexus` 方法）为：
```python
    def _gitnexus(self, e) -> str:
        return f"[{e.timestamp}] [LLM]   [GitNexus] {gitnexus_body(e)}\n"
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest packages/core/tests/display/test_file_renderer.py -v -k gitnexus`
Expected: PASS（5 passed）

- [ ] **Step 6: 跑 file 全量确认无回归**

Run: `pytest packages/core/tests/display/test_file_renderer.py -v`
Expected: PASS（`[STEP ]`/`[PHASE]`/`[AGENT]`/`[INFO ]` 断言此时仍为 LABEL_WIDTH=5，Task 4 才改）

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/display/file_renderer.py packages/core/tests/display/test_file_renderer.py
git commit -m "refactor(display): file GitNexus 行归 [LLM] 栏 [LLM]   [GitNexus] body 走共享 gitnexus_body"
```

---

## Task 4: `LABEL_WIDTH` 5→7（标签列对齐，顺带修 INFO/WARNING 错位）

> 此任务触及多个测试文件的精确断言，全部是同一变更（标签栏 5→7）的连锁字符串更新，故归一个 task。

**Files:**
- Modify: `packages/core/src/shannon_core/display/formatters.py:211`（`LABEL_WIDTH` 常量）
- Test: `packages/core/tests/display/test_formatters.py:205-216`（tag/LABEL_WIDTH 断言）、`test_file_renderer.py`（STEP/PHASE/AGENT/INFO 精确断言）、`test_rich_renderer.py:326-334`（INFO 断言）

**Interfaces:**
- Produces: `LABEL_WIDTH = 7`（所有走 `tag()` 的标签 ljust 到 7；GN-LLM 已归族不用 tag）

- [ ] **Step 1: 改 test_formatters.py 的 tag/LABEL_WIDTH 断言**

`test_tag_pads_short_label_to_width`（`:205`）改为：
```python
def test_tag_pads_short_label_to_width():
    assert tag("STEP") == "STEP   "          # 4 -> 7
```

`test_tag_no_pad_when_already_full_width`（`:209`）改为（PHASE/AGENT 不再满宽，唯一 7 字符是 WARNING）：
```python
def test_tag_no_pad_when_already_full_width():
    assert tag("WARNING") == "WARNING"       # 唯一 7 字符标签，ljust(7) 不补
```

`test_tag_all_core_labels_equal_width`（`:214`）改为：
```python
def test_tag_all_core_labels_equal_width():
    assert {len(tag(l)) for l in ("PHASE", "STEP", "AGENT", "INFO", "WARNING")} == {LABEL_WIDTH}
    assert LABEL_WIDTH == 7
```

- [ ] **Step 2: 改 test_file_renderer.py 受影响精确断言**

`test_step_event_renders_step_line`（`:171-172`）两条 assert 改为：
```python
    assert "[STEP   ] ○ code-index\n" in out
    assert "[STEP   ] ✓ code-index  12.0s\n" in out
```

`test_step_file_line_includes_intent_when_present`（`:187`）改为：
```python
    assert "[STEP   ] ○ 构建调用图与代码索引\n" in out
```

`test_file_renderer_info_event_info_level`（`:239`）改为：
```python
    assert "[INFO   ]" in written and "hi" in written and written.endswith("\n")
```

`test_phase_step_agent_labels_align_in_file`（`:264-266`）三行 `next(...)` 改为去掉 `]`（匹配补空格后的标签）：
```python
    phase_line = next(ln for ln in lines if "[PHASE" in ln)
    step_line = next(ln for ln in lines if "[STEP" in ln)
    agent_line = next(ln for ln in lines if "[AGENT" in ln)
```
（对齐断言 `assert p == s == a` 不变——body 起点仍同列）

`test_phase_start_prepends_blank_line`（`:37`）改为：
```python
    assert "[PHASE  ] Starting reconnaissance" in out
```

`test_phase_complete_no_blank_prefix`（`:45`）改为：
```python
    assert "[PHASE  ] Completed recon" in out
```

`test_agent_start_with_prefix`（`:57`）改为：
```python
    assert "[AGENT  ] ▶ [Injection] injection-vuln started (attempt 2)\n" in renderer._writer.text
```

`test_agent_start_no_prefix_for_unknown`（`:65`）改为：
```python
    assert "[AGENT  ] ▶ pre-recon started (attempt 1)\n" in renderer._writer.text
```

`test_agent_end_completed_with_metrics`（`:73`）改为：
```python
    assert "[AGENT  ] ✓ [XSS] xss-vuln Completed (5.2s, $0.1500)\n" in renderer._writer.text
```

`test_agent_end_failed`（`:81`）改为：
```python
    assert "[AGENT  ] ✗ [XSS] xss-vuln failed (100ms) — boom" in renderer._writer.text
```

- [ ] **Step 3: 改 test_rich_renderer.py 的 INFO 断言**

`test_rich_renderer_info_event_info_level_cyan`（`:334`）改为（精确化 pad 后的串）：
```python
    assert "INFO   " in printed and "cyan" in printed and "hi" in printed
```

> 注：rich 端其它测试用 `"AGENT" in out`/`"STEP" in out` 子串匹配，补空格后子串仍成立，无需改。`test_phase_step_agent_bodies_align_same_column` 测 body 对齐，逻辑不变。

- [ ] **Step 4: 跑测试确认失败（常量还是 5）**

Run: `pytest packages/core/tests/display/test_formatters.py packages/core/tests/display/test_file_renderer.py packages/core/tests/display/test_rich_renderer.py -v`
Expected: FAIL — 上一步改的断言期望 7 宽产物，但 `LABEL_WIDTH` 仍是 5。

- [ ] **Step 5: 改常量**

`formatters.py:211`：
```python
LABEL_WIDTH = 7  # PHASE/STEP/AGENT/INFO/WARNING ljust 到 7（含 GN-LLM 归族后退出标签栏）
```
（原值为 `LABEL_WIDTH = 5`，注释原样保留前半，更新数字与括注）

- [ ] **Step 6: 跑 display 三测试文件确认全绿**

Run: `pytest packages/core/tests/display/test_formatters.py packages/core/tests/display/test_file_renderer.py packages/core/tests/display/test_rich_renderer.py -v`
Expected: PASS

- [ ] **Step 7: 跑 workflow_logger gitnexus 测试确认事件层未破**

Run: `pytest packages/core/tests/audit/test_workflow_logger_gitnexus.py -v`
Expected: PASS（3 passed；事件字段/category 不变）

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/shannon_core/display/formatters.py packages/core/tests/display/test_formatters.py packages/core/tests/display/test_file_renderer.py packages/core/tests/display/test_rich_renderer.py
git commit -m "feat(display): LABEL_WIDTH 5→7 修标签列错位(顺带 INFO/WARNING)"
```

---

## Task 5: 更新 docstring 与 memory（grep 锚点）

**Files:**
- Modify: `packages/core/src/shannon_core/display/events.py:124-127`（`GitnexusLlmEvent` docstring）
- Modify: `/Users/mango/.claude/projects/-Users-mango-project-shannon-refactor-shannon-py/memory/gitnexus-llm-progress-logging-status.md`

**Interfaces:** 无（文档/备忘更新，无代码契约变化）

- [ ] **Step 1: 更新 events.py docstring**

`events.py:124-127` 现有 docstring：
```python
    """GitNexus 轨 LLM 环节的进度行 —— 与 LLM 轨 LlmTurnEvent 对偶：
    LLM 轨是单个 agent 的 turn 流，GitNexus 轨是批量函数/候选的并发判定。
    专属标签 GN-LLM 便于 grep 所有 LLM 活动。"""
```
改为：
```python
    """GitNexus 轨 LLM 环节的进度行 —— 与 LLM 轨 LlmTurnEvent 对偶：
    LLM 轨是单个 agent 的 turn 流，GitNexus 轨是批量函数/候选的并发判定。
    归 LLM 活动族渲染：终端 🔍 [GitNexus] (cyan, 冷暖对偶 💭 [Agent] magenta)，
    workflow.log [LLM]   [GitNexus]；grep 锚点 [GitNexus]。category 字段保留 GN-LLM 作内部 subtype。"""
```

- [ ] **Step 2: 更新 memory 备忘**

`gitnexus-llm-progress-logging-status.md` 把摘要里"专属标签 GN-LLM"/grep 相关表述更新为：grep 锚点 `[GitNexus]`；终端符号 `🔍` + 颜色 `cyan`（对偶 agent Turn 的 💭 magenta）；workflow.log `[LLM]   [GitNexus]`。progress 行 `· {hits} {noun}`（加 noun 去 so far）。具体措辞参照该文件既有风格，保持「状态 + 待冒烟 + 不变量」三段式，补一行链接到本设计 spec `2026-07-02-gn-llm-log-format-design.md`。

- [ ] **Step 3: 跑 events 测试确认 docstring 改动未碰字段**

Run: `pytest packages/core/tests/display/test_events.py -v`
Expected: PASS（docstring 不影响 dataclass 字段）

- [ ] **Step 4: Commit**

```bash
git add packages/core/src/shannon_core/display/events.py
git commit -m "docs(display): GitnexusLlmEvent docstring 更新 grep 锚点 [GitNexus] 与归族渲染"
```
（memory 文件在 `~/.claude/...` 不在 repo 内，无需 git add；直接保存即可）

---

## Self-Review（计划作者自检，已完成）

**1. Spec coverage：**
- D1 层级归属 → Task 2/3（rich/file 归族）✓
- D2 符号+颜色对偶（🔍 cyan vs 💭 magenta）→ Task 2（rich cyan/🔍）、Task 3（file [LLM] 栏，rich 端 emoji 对偶）✓
- D3 progress 加 noun 去 so far → Task 1（gitnexus_body）✓
- D4 LABEL_WIDTH=7 → Task 4 ✓
- D5 noun/body 共享 → Task 1 ✓
- D6 grep 锚点 [GitNexus] + events 注释 + memory → Task 5 ✓
- 不改边界（事件层/category/ERROR·TOOL·LLM·RESUME·SUMMARY/summary detail/phase 名/LLM 轨）→ 各 task Interfaces 与 Global Constraints 明示 ✓

**2. Placeholder scan：** 无 TBD/TODO/"similar to"；每个代码 step 含完整代码；命令含 expected。✓

**3. Type consistency：** `gitnexus_body(e)`/`gitnexus_hits_noun(phase)` 在 Task 1 定义，Task 2/3 消费，签名一致；`GitnexusLlmEvent` 字段（phase/kind/done/total/hits/detail）在 Task 1 测试构造与 formatters 实现中一致。✓
