# 终局成功 🎉 装饰（Audit Complete Emoji）

- **日期**: 2026-07-03
- **状态**: 已批准设计（方案 A），待写实现计划
- **相关**: `docs/superpowers/specs/2026-06-22-log-format-redesign-design.md`（符号单一来源 / pad_rule）

## 1. 背景与动机

用户想在项目日志里加 🎉 emoji「因为好看」。但项目日志体系有严格约定：

- **状态符号收口**：`○✓✗▶` 集中在 `display/symbols.py`，承载进行中 / 完成 / 失败 / 启动语义。
- **emoji 全是事件驱动的**：`🚀`Task 启动、`✅`todo 完成、`🌐`浏览器导航、`📸`截图、`🔍`GitNexus、`💭`LLM turn、`🔧`tool。每个 emoji 绑定一种事件，**没有纯装饰 emoji**。
- **2026-06-22 日志格式 spec 把「emoji 行统一」明确列为非目标**（「未来独立 spec 治理」）。本 spec 即是那个独立治理的第一步：给 🎉 找一个不破坏体系的语义落点。

**核心张力**：动机是装饰（好看），但项目约定要求 emoji 必须有语义锚点 + 收口到单一来源。解法 = 找一个天然庆祝时刻，让 🎉 既满足装饰、又承载语义。

## 2. 目标与非目标

**目标**
- 给 🎉 一个语义锚点：**扫描成功收官**（`SummaryEvent` 且 `status == "completed"`）。
- 守住符号单一来源铁律：常量收口到 `symbols.py`，不散落字面量。
- 不污染 `workflow.log` 文件正文（护 grep + 守 2026-06-22 spec「文件正文不动」铁律）。

**非目标**
- 不统一现有 emoji 行（`🚀✅🌐📸🔍💭🔧`）——更大范围 spec，不在本 spec。
- 不加 emoji 渲染开关（YAGNI；运行环境已知支持 emoji）。
- 不动结构化 renderer（机器消费不掺 emoji）。
- 不改失败态展示（红色 `Panel` + `FAILED` 已够，不庆祝失败）。

## 3. 方案（A）

在 `SummaryEvent`（终局事件）的终端 `Panel.fit` 正文行首加 🎉，**仅当 `e.status == "completed"`**。失败态不加。

`SummaryEvent` 是 audit run 的终局事件，被两个 renderer 消费：终端 `_render_summary`（`Panel.fit`）、文件 `_summary`（`=` 分隔块）。本 spec 只动终端。

**终端效果（completed）**：
```
╭──────────────────────────────────────────╮
│ 🎉 Workflow COMPLETED                    │
│ Duration: 14m 25s    Total Cost: $3.4990 │
╰──────────────────────────────────────────╯
```

**失败态（不变，红框）**：
```
╭──────────────────────────────────────────╮
│ Workflow FAILED                          │
╰──────────────────────────────────────────╯
```

**文件 `workflow.log`（不变，纯 ASCII）**：
```
================================================================================
Workflow COMPLETED
--------------------------------------------------------------------------------
...
```

## 4. 符号收口

`display/symbols.py` 新增一个常量，与 `SUMMARY_OK` 同族（但注释说明它是终局装饰、非状态符号族）：

```python
# 终局成功装饰（非 STEP/AGENT 状态符号族，仅用于扫描收官的庆祝行）
AUDIT_COMPLETE_OK = "🎉"
```

## 5. 改动文件清单

| 文件 | 改动 |
|---|---|
| `packages/core/src/shannon_core/display/symbols.py` | 新增 `AUDIT_COMPLETE_OK = "🎉"` |
| `packages/core/src/shannon_core/display/rich_renderer.py` | `_render_summary`：import `AUDIT_COMPLETE_OK`；`e.status == "completed"` 时正文行首加 🎉 |
| `packages/core/src/shannon_core/display/file_renderer.py` | **不动**（护 grep + 守 spec 铁律） |
| `packages/core/src/shannon_core/display/structured_event_renderer.py` | **不动**（机器消费） |

`_render_summary` 改动点（当前 `rich_renderer.py:154-162`）：

```python
# before
def _render_summary(self, e) -> None:
    from rich.table import Table
    status = e.status.upper()
    self._console.print(Panel.fit(
        f"Workflow [bold]{status}[/]\n"
        f"Duration: {format_duration(e.total_duration_ms)}    "
        f"Total Cost: ${e.total_cost_usd:.4f}",
        border_style="green" if e.status == "completed" else "red",
    ))

# after
def _render_summary(self, e) -> None:
    from rich.table import Table
    status = e.status.upper()
    prefix = f"{AUDIT_COMPLETE_OK} " if e.status == "completed" else ""
    self._console.print(Panel.fit(
        f"{prefix}Workflow [bold]{status}[/]\n"
        f"Duration: {format_duration(e.total_duration_ms)}    "
        f"Total Cost: ${e.total_cost_usd:.4f}",
        border_style="green" if e.status == "completed" else "red",
    ))
```

## 6. 向后兼容

- `SummaryEvent` 数据结构不变。
- 失败态输出不变（红框 + FAILED）。
- `workflow.log` 文件不变（终端改动不波及；`Workflow COMPLETED` 仍可 grep）。
- 结构化日志不变。

## 7. 测试策略

- `packages/core/tests/display/test_symbols.py`：加 `assert AUDIT_COMPLETE_OK == "🎉"`。
- `packages/core/tests/display/test_rich_renderer.py`：加两例——
  - `status="completed"` 时 summary 渲染含 `"🎉"`；
  - `status="failed"`（或其他非 completed）时不含 `"🎉"`。
- **回归**：跑 `packages/core/tests/display/` 子集，**不跑全套**（全套 hang 在 Temporal / 网络慢测试，见 memory `feat-fork-py-test-gotchas`）。

## 8. 风险与边界

| 风险 | 缓解 |
|---|---|
| 不渲染 emoji 的终端（Windows cmd / 部分 CI log 收集器）显示 `?` 或方框 | 项目已大量用 emoji（`🚀✅🌐📸🔍💭🔧`），运行环境已知支持，**不增新风险** |
| 有人觉得 🎉 不专业 | YAGNI；用户明确诉求是「好看」；真有反对意见后续可加 `SHANNON_NO_EMOJI` env 开关，本 spec 不预设 |
| 误把 🎉 加进文件日志 | 已在非目标 + 改动清单双重声明「文件不动」；实现 PR review 时核对 |

## 9. 验收标准

- [ ] `symbols.py` 含 `AUDIT_COMPLETE_OK = "🎉"`，且 `_render_summary` 从该常量 import（无散落字面量）。
- [ ] 终端扫描成功完成时，summary `Panel` 正文行首出现 🎉。
- [ ] 扫描失败时无 🎉（红色 FAILED 框不变）。
- [ ] `workflow.log` 文件正文不含 🎉（`Workflow COMPLETED` 保持纯 ASCII）。
- [ ] `packages/core/tests/display/` 子集全绿。
- [ ] 人工冒烟（真仓库跑一次 completed 的 run）确认 🎉 显示正确。
