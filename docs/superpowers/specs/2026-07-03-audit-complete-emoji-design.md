# 终局 🎉/💥 装饰（Audit Complete Emoji）

- **日期**: 2026-07-03
- **状态**: 已实现（TDD）
- **相关**: `docs/superpowers/specs/2026-06-22-log-format-redesign-design.md`（符号单一来源 / pad_rule）

## 1. 背景与动机

用户想在项目日志里加 emoji「因为好看」。但项目日志体系有严格约定：

- **状态符号收口**：`○✓✗▶` 集中在 `display/symbols.py`，承载进行中 / 完成 / 失败 / 启动语义。
- **emoji 全是事件驱动的**：`🚀`Task 启动、`✅`todo 完成、`🌐`浏览器导航、`📸`截图、`🔍`GitNexus、`💭`LLM turn、`🔧`tool。每个 emoji 绑定一种事件，**没有纯装饰 emoji**。
- **2026-06-22 日志格式 spec 把「emoji 行统一」明确列为非目标**（「未来独立 spec 治理」）。本 spec 即是那个独立治理的第一步。

**核心张力**：动机是装饰（好看），但项目约定要求 emoji 必须有语义锚点 + 收口到单一来源。解法 = 找一个天然事件锚点（扫描收官），让 emoji 既满足装饰、又承载语义。

**成功 / 失败对仗**：终局有 `completed` / `failed` 两态。成功用 🎉（庆祝）、失败用 💥（爆破），形成视觉对仗，且不撞现有 `✗`（失败状态符号）与 `⚠️`（警告 emoji）。初版 spec 仅给成功态加 🎉、失败态不动；后用户要求失败也给 emoji，扩展为双态。

## 2. 目标与非目标

**目标**
- 给终局 emoji 一个语义锚点：`SummaryEvent`——`status == "completed"` → 🎉；`status != "completed"` → 💥。
- 守住符号单一来源铁律：常量收口到 `symbols.py`，不散落字面量。
- 不污染 `workflow.log` 文件正文（护 grep + 守 2026-06-22 spec「文件正文不动」铁律）。

**非目标**
- 不统一现有 emoji 行（`🚀✅🌐📸🔍💭🔧`）——更大范围 spec，不在本 spec。
- 不加 emoji 渲染开关（YAGNI；运行环境已知支持 emoji）。
- 不动结构化 renderer（机器消费不掺 emoji）。
- 不动 `workflow.log` 文件 / `file_renderer`（护 grep）。

## 3. 方案

在 `SummaryEvent`（终局事件）的终端 `Panel.fit` 正文行首加 emoji：`completed` → 🎉，`failed` → 💥。仅终端 renderer。

**终端 completed（绿框）**：
```
╭──────────────────────────────────────────╮
│ 🎉 Workflow COMPLETED                    │
│ Duration: 14m 25s    Total Cost: $3.4990 │
╰──────────────────────────────────────────╯
```

**终端 failed（红框）**：
```
╭──────────────────────────────────────────╮
│ 💥 Workflow FAILED                       │
│ Duration: 14m 25s    Total Cost: $3.4990 │
╰──────────────────────────────────────────╯
```

**文件 `workflow.log`（不变，纯 ASCII）**：
```
================================================================================
Workflow COMPLETED      （或 Workflow FAILED）
--------------------------------------------------------------------------------
```

## 4. 符号收口

`display/symbols.py` 新增两个常量，注释说明它们是终局装饰、非状态符号族：

```python
# 终局装饰（非 STEP/AGENT 状态符号族），扫描收官 Panel 行首：成功 🎉 / 失败 💥
AUDIT_COMPLETE_OK = "🎉"
AUDIT_COMPLETE_FAIL = "💥"
```

## 5. 改动文件清单

| 文件 | 改动 |
|---|---|
| `packages/core/src/shannon_core/display/symbols.py` | 新增 `AUDIT_COMPLETE_OK` / `AUDIT_COMPLETE_FAIL` |
| `packages/core/src/shannon_core/display/rich_renderer.py` | `_render_summary`：import 两常量；`ok = e.status == "completed"`；`prefix = f"{AUDIT_COMPLETE_OK if ok else AUDIT_COMPLETE_FAIL} "` |
| `packages/core/src/shannon_core/display/file_renderer.py` | **不动**（护 grep + 守 spec 铁律） |
| `packages/core/src/shannon_core/display/structured_event_renderer.py` | **不动**（机器消费） |

`_render_summary` 最终形态：

```python
def _render_summary(self, e) -> None:
    from rich.table import Table
    ok = e.status == "completed"
    status = e.status.upper()
    prefix = f"{AUDIT_COMPLETE_OK if ok else AUDIT_COMPLETE_FAIL} "
    self._console.print(Panel.fit(
        f"{prefix}Workflow [bold]{status}[/]\n"
        f"Duration: {format_duration(e.total_duration_ms)}    "
        f"Total Cost: ${e.total_cost_usd:.4f}",
        border_style="green" if ok else "red",
    ))
```

## 6. 向后兼容

- `SummaryEvent` 数据结构不变。
- `workflow.log` 文件不变（终端改动不波及；`Workflow COMPLETED` / `Workflow FAILED` 仍可 grep）。
- 结构化日志不变。

## 7. 测试策略

- `packages/core/tests/display/test_symbols.py`：`AUDIT_COMPLETE_OK == "🎉"` + `AUDIT_COMPLETE_FAIL == "💥"`。
- `packages/core/tests/display/test_rich_renderer.py`：
  - `status="completed"` → 含 🎉、不含 💥；
  - `status="failed"` → 含 💥、不含 🎉、含 `FAILED`。
- **回归**：跑 `packages/core/tests/display/` 子集，**不跑全套**（全套 hang，见 memory `feat-fork-py-test-gotchas`）。

## 8. 风险与边界

| 风险 | 缓解 |
|---|---|
| 不渲染 emoji 的终端（Windows cmd / 部分 CI）显示 `?` | 项目已大量用 emoji，运行环境已知支持，**不增新风险** |
| 失败 💥 显得戏谑 | 用户明确要求失败也给 emoji；💥 是与 🎉 对仗的最克制选择，且不撞现有 `✗` / `⚠️` |
| 误把 emoji 加进文件日志 | 已在非目标 + 改动清单双重声明「文件不动」；`test_file_renderer` 22 例全绿佐证 |

## 9. 验收标准

- [x] `symbols.py` 含 `AUDIT_COMPLETE_OK = "🎉"` 与 `AUDIT_COMPLETE_FAIL = "💥"`，`_render_summary` 从常量 import（无散落字面量）。
- [x] 终端 `completed` 时 summary `Panel` 正文行首出现 🎉。
- [x] 终端 `failed` 时 summary `Panel` 正文行首出现 💥（红框）。
- [x] `workflow.log` 文件正文不含 emoji（`Workflow COMPLETED` / `Workflow FAILED` 保持纯 ASCII）。
- [x] `packages/core/tests/display/` 子集全绿（192 passed）。
- [ ] 人工冒烟（真仓库跑一次 completed + 一次 failed）确认两态 emoji 显示正确。
