# 日志格式重设计（PHASE / STEP / AGENT）

- **日期**: 2026-06-22
- **状态**: 已批准设计，待写实现计划
- **相关**: `docs/superpowers/specs/2026-06-19-rich-log-visibility-design.md`（上一轮 rich 可见性恢复）

## 1. 背景与动机

Rich 终端 live 日志当前有三类可见的格式问题，集中在 PHASE / STEP / AGENT 三种行：

1. **分隔线长度不一**：横线 `─` 在三处各自硬编码不同长度——Rich PHASE 行 `'─' * 20`（`packages/core/src/shannon_core/display/rich_renderer.py:95`）、文件日志 summary `"─" * 40`（`file_renderer.py:128`）、LiveDashboard 状态栏 `"─" * options.max_width`（`live_dashboard.py:68`）。PHASE 行文字（如 `Starting pre-recon`，20 字符）超出固定 20 字符横线，看起来"短了一截"。

2. **STEP 开始/完成文字割裂**：STEP 开始行用中文 `intent` 字段（`▸ 预检（环境 / 依赖就绪性）`），完成行切换为英文 `name` slug + 耗时（`✓ preflight (0ms)`）。同一个步骤两种语言，观感割裂。来源：`whitebox/pipeline/step_intents.py` 的 `StepSpec(name=英文 slug, intent=中文描述)`。

3. **符号风格不统一**：STEP 开始用 `▸`（U+25B8 三角）、完成用 `✓`（U+2713 对勾）、AGENT 启动用 `▶`（U+25B6 实心三角）。`▸` 与 `✓` 非同族，视觉跳。符号散落在 `rich_renderer.py`、`formatters.py`、`utils/progress.py` 多处硬编码，无统一收口。

**额外发现的两个 bug**（在范围内顺带修）：
- STEP 失败态当前仍用 `✓` 符号（`rich_renderer.py:87-90`：`e.error` 存在时仅改后缀，符号未变）。
- AGENT 结束行缺时间戳前缀（start 行有 `[时间戳]`，end 行 `Completed...` / `failed...` 没有）。

## 2. 目标与非目标

**目标**
- 统一 PHASE / STEP / AGENT 三类 Rich 终端行的格式。
- 消除上述四个问题。
- 把符号与分隔线常量收口到单一来源，防未来再次散落。

**非目标**
- 不改 🔧(tool) / 💭(llm) / ✅(todo) / 🔄 等 emoji 行——未来独立 spec 治理。
- 不改 `workflow.log` 文件日志正文（保留英文 slug 给 grep）。
- 不改 `DisplayEvent` 数据结构、dispatcher、事件流。
- 不处理极窄终端（<40 列）的分隔线裁剪（YAGNI）。

## 3. 新格式规范

### 3.1 符号表

| 事件 | 符号 | 含义 |
|---|---|---|
| STEP start | `○` | 进行中 |
| STEP complete | `✓` | 完成 |
| STEP fail | `✗` | 失败（`e.error` 非空） |
| AGENT start | `▶` | 启动派发 |
| AGENT end success | `✓` | 成功 |
| AGENT end fail | `✗` | 失败 |
| PHASE | （无符号） | verb `Starting`/`Completed` 表状态 |
| Summary 成功/失败 | `✓` / `✗` | 与 STEP/AGENT 复用 |

### 3.2 三类行最终样子

```
[2026-06-22 00:25:17] PHASE  Starting setup ──────────────────
[2026-06-22 00:25:17] PHASE  Completed setup ────────────────
[2026-06-22 00:25:17] PHASE  Starting pre-recon ─────────────

[2026-06-22 00:25:17] STEP   ○ 预检（环境 / 依赖就绪性）
[2026-06-22 00:25:17] STEP   ✓ 预检（环境 / 依赖就绪性）  0ms
[2026-06-22 00:25:17] STEP   ✗ 预检（环境 / 依赖就绪性）  — <error>

[2026-06-22 00:25:17] AGENT  ▶ [Recon] recon started (attempt 1)
[2026-06-22 00:39:44] AGENT  ✓ [Recon] recon Completed (14m 25s, $3.4990)
[2026-06-22 00:39:44] AGENT  ✗ [Recon] recon failed (14m 25s) — <error>
```

要点：
- STEP 完成/失败行文字复用开始行的 `e.intent or e.name`（中文 intent 优先），耗时紧跟文字（`✓ <intent>  0ms`），失败态后缀 `— <error>`。英文 slug 退出终端。
- AGENT end 行补 `[时间戳]` 前缀与 `✓`/`✗` 符号；颜色保留（成功绿 / 失败红）。
- PHASE 行无状态符号，分隔线 `─` 右端对齐到固定列。

## 4. 符号常量收口

新建 `packages/core/src/shannon_core/display/symbols.py`：

```python
"""Display status symbols — single source of truth for all renderers."""

STEP_PENDING = "○"
STEP_DONE = "✓"
STEP_FAIL = "✗"

AGENT_START = "▶"
AGENT_DONE = "✓"
AGENT_FAIL = "✗"

SUMMARY_OK = "✓"
SUMMARY_FAIL = "✗"
```

`rich_renderer.py` 的 `_render_step` / `_render_agent` / `_render_summary` 与 `file_renderer.py` 的 summary 改为 import 这些常量，消除散落字面量。

## 5. 分隔线对齐算法

新增 `display/formatters.py::pad_rule`，列宽常量同放此文件：

```python
from rich.cells import cell_len

PHASE_RULE_WIDTH = 36  # text + 横线的总显示列宽

def pad_rule(text: str, col: int = PHASE_RULE_WIDTH) -> str:
    """让 text 与 '─' 填充的右端对齐到 col 列。

    用 cell_len 按显示宽度计算（中文 intent 算 2 列）。文字超长时兜底至少 2 个 ─。
    """
    width = cell_len(text)
    n = max(2, col - width)
    return f"{text} {'─' * n}"
```

PHASE 行调用：
```python
body = pad_rule(f"{verb} {e.phase}")
self._console.print(f"[{e.timestamp}] [bold cyan]PHASE[/]  {body}", highlight=False)
```

实现首步需确认项目 rich 版本暴露 `rich.cells.cell_len`（rich >= 12.0 提供）。

## 6. 改动文件清单

| 文件 | 改动 |
|---|---|
| `display/symbols.py` | **新建**：状态符号常量 |
| `display/formatters.py` | 新增 `PHASE_RULE_WIDTH` + `pad_rule()`（+ `cell_len` import） |
| `display/rich_renderer.py` | `_render_step`：○/✓/✗ + 完成态文字改 `e.intent or e.name` + 失败态符号改 ✗；`_render_phase`：用 `pad_rule`；`_render_agent`：end 行补 `[时间戳]` 前缀 + ✓/✗ 符号；`_render_summary`：✓/✗ 改 import |
| `display/file_renderer.py` | summary 的 ✓/✗ 改 import（正文不动） |

## 7. 向后兼容

- `DisplayEvent` 数据结构不变（`StepEvent.intent`/`error`、`AgentEvent` 字段已存在）。
- dispatcher / 事件流不变。
- emoji 行不变。
- `workflow.log` 正文不变（终端改动不波及；slug 保留给 grep）。
- 排查有无下游依赖 STEP 完成行的英文 slug 终端输出——预期无（终端输出面向人）。

## 8. 测试策略

新增/扩充测试（`packages/core/tests/display/` 下）：
- `test_symbols.py`：符号常量值断言。
- `test_formatters.py`：`pad_rule` 三例——纯 ASCII（`Starting setup`）、含中文宽度、文字超长兜底（≥2 个 ─）。
- `test_rich_renderer.py`：三类行渲染断言——STEP start/complete/fail（文字为 intent、符号为 ○/✓/✗）、AGENT start/end-success/end-fail（含时间戳前缀断言）、PHASE start/complete（分隔线右端对齐断言）。

**回归**：跑 display 相关子集，**不跑全套**（全套 hang 在 Temporal/网络慢测试）。

## 9. 风险与边界

| 风险 | 缓解 |
|---|---|
| `rich.cells.cell_len` 在项目 rich 版本不可用 | 实现首步验证；不可用则退 `wcwidth` 或手写 East-Asian-Width 表 |
| 固定列宽 36 在窄终端偏长 | YAGNI，先不处理；必要时改可配置 |
| STEP 完成行去掉 slug 后失去 grep 价值 | `workflow.log` 仍保留 slug；终端面向人 |
| emoji 行未统一 | 非目标，未来独立 spec |

## 10. 验收标准

- [ ] PHASE 多行（不同 phase 的 start+complete）分隔线 `─` 右端对齐。
- [ ] STEP start/complete/fail 三态符号分别为 ○/✓/✗，文字均为中文 intent，失败态后缀 `— <error>`。
- [ ] AGENT start 行用 ▶，end 行带 `[时间戳]` 前缀 + ✓（成功）/ ✗（失败）。
- [ ] summary 表的 ✓/✗ 与 `symbols.py` 一致。
- [ ] 所有状态符号字面量从 `symbols.py` import，`rich_renderer.py` / `file_renderer.py` 内无散落符号硬编码（STEP/AGENT/summary 范围）。
- [ ] display 相关单测全绿；人工冒烟（真仓库跑 start）确认三类行观感符合 3.2。
