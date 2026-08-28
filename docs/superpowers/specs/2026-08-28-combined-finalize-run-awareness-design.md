# combined 收口 run 终态感知设计——根级 completed 不再掩盖黑盒 run 失败

> 日期：2026-08-28
> 分支：feat/fork-py
> 状态：设计待 review
> 关联：[2026-08-27 web-resume-breakpoint](./2026-08-27-web-resume-breakpoint-design.md)（resume 状态集，本设计**不改**其判定）、2026-08-28 NodeGoat-20260828-054537 injection-exploit 失败调查（commit `375ba4c2` / `2bc2d75c`，error 丢失治本与本 spec 同源——现场黑盒 run-1 failed 但根级 scan_end 写了 `combined completed`）
> 触发：用户明确「不想掩盖真实问题」——live 页直播里黑盒 run-1 失败（`■ DONE failed` / `◆ RUN run-1 · failed`），4 秒后根级却落「已完成」横幅；session.json 根级 `status=completed`，run 级失败只藏在 run 目录里。

---

## 0. 一句话结论

combined 扫描的根级收口 `_ensure_scan_end`（scan_manager）目前**只感知「白盒+报告是否成功」**（假完成保险丝只拦「从未开始」：bb_phase 前置 + 零 agent + 无产物），**完全不感知黑盒 run 的终态**——run-1 failed 依然写 `combined completed`。本设计：收口前**读全部 run 终态**，任一 run 失败时 status 保持 `completed` 但**强制携带 `failed_runs` 明细**（scan_end 事件 + 根 session.json 双落盘，前端 live 横幅/列表同步提示）；**全部 run 失败时降级 `failed`**。resume/重跑的状态机判定**不动**（`completed` 仍 completed）——「不掩盖」由结构化明细承载，不靠改状态语义。

---

## 1. 背景：现状的掩盖链

### 1.1 现场时间线（NodeGoat-20260828-054537）

| 时刻 | 事件 | 数据落点 |
|---|---|---|
| 06:14:38 | 白盒段完成（7 agents 全绿） | wb `events.ndjson` phase complete |
| 06:55:26 | 黑盒 injection-exploit agent 失败（error 丢失落 fallback，见 `2bc2d75c`） | run-1 `events.ndjson` AgentEvent end failed |
| 06:59:46 | 黑盒 run-1 判 failed（4/5 exploit 成功、report 成功，单 agent 失败 → run failed） | run-1 `events.ndjson` `scan_end{failed}` + SummaryEvent failed |
| 06:59:50 | **根级收口 `_ensure_scan_end` 写 `scan_end{completed, "combined completed"}`** | wb `events.ndjson` + 根 `session.json` status=completed |

### 1.2 代码现状（掩盖的机制）

`scan_manager._ensure_scan_end(scan_dir, status="completed")`：

- **幂等**：events 已有 scan_end → no-op（成功路径黑盒 finalize 已写的是 **run 级** scan_end，根级仍由本方法写——这是设计内的分工，不是 bug）。
- **假完成保险丝** `_fake_combined_completion_reason`：只拦「从未开始」（combined + bb_phase∈{precheck,pending} + completed_agents 空 + 无白盒产物）——**「部分失败」不在拦截集**。
- run 终态数据**完整可用**：run-K `events.ndjson` 的末条 `scan_end`（黑盒 finalize 写）+ 根 session `bb_runs[]` 条目 status（`_mark_run` 维护）——收口时**没读**。

前端：LiveTab「已完成」横幅只看 `scan_end.status==="completed"`；列表页/`runStatus` 只看根级 status。**run 级失败对任务级 UI 不可见**（live 日志流里能看到 `run_end failed` 红字，但状态语义上被 completed 盖掉）。

### 1.3 为什么 run failed 被设计为「不挡收口」

黑盒 run 内单 agent 失败 → run 判 failed（现状粒度），但：白盒轨独立成功、黑盒其余 4 个 exploit 成功、report/融合报告已产出——**成果是真实的**，标 failed 会：(1) 挡 resume（failed 在可续集，但语义上「整体失败」误导）；(2) 列表页用户看到 failed 会整任务重跑（浪费已成功的 5/6 成本）。所以「completed + 明细」是诚实的中间态——**缺的只是明细**。

---

## 2. 设计

### 2.1 run 终态读取（收口时点）

`_ensure_scan_end` 在假完成保险丝之后、`_write_scan_end` 之前，读全部 run 终态：

```python
run_states = self._collect_run_states(scan_dir)
# 返回 {run_id: status}，来源优先级：
# 1) run-K/events.ndjson 末条 scan_end.status（权威；黑盒 finalize 写）
# 2) 回落根 session bb_runs[] 条目 status（_mark_run 维护；events 缺失/竞态时）
# 3) 均无 → "unknown"（不计入 failed）
failed_runs = sorted(r for r, s in run_states.items() if s == "failed")
all_failed  = bool(failed_runs) and len(failed_runs) == len(run_states)
```

- **全部 run failed → 降级**：`status = "failed"`，`stderr_tail` 拼 `bb_failure_detail: all blackbox runs failed (...)`（复用现有失败详情透出通道）。
- **部分 failed → status 保持 completed**，`failed_runs` 明细随事件+session 落盘（2.2）。
- 纯白盒（无 run）：`run_states` 为空 → 行为与现状完全一致（零回归面）。

### 2.2 双落盘：scan_end 事件 + 根 session

- `_write_scan_end` payload 增补 `failed_runs: ["run-1"]`（空/无 run 不写字段，保持旧 payload 形态——旧消费方零感知）。
- 根 `session.json` 同步写 `failed_runs`（`SessionManager.update_session`，与 status 同事务语义；供列表页/详情页 API 读取，不依赖 events 解析）。
- **merged 流自动透传**：`MergedEventTailer` 扣发的 wb scan_end 是 `dict(self._held_end, src="wb")` 原样转发——新字段**无需改 tailer** 即达前端 SSE。

### 2.3 前端呈现（诚实但不惊吓）

- **LiveTab**「已完成」横幅：`scanEnd.failed_runs?.length` → 横幅加黄色警示行「⚠ 黑盒 run-1 失败（其余成果完整）」+ 保留「查看报告」按钮；`endedFailed` 逻辑不动（status 仍 completed）。
- **ScanProgressOverview / 列表页**：状态徽章 completed 保持绿色，角标 `⚠1`（有 failed_runs 时）。
- i18n：`workspaceDetail.live.partialFailure` 等 key（中英双语）。

### 2.4 不改的东西（防牵连）

| 不动 | 原因 |
|---|---|
| resume/重跑状态集 | `completed` 仍 completed（用重跑入口）；failed_runs 只是提示字段，不进门控 |
| `_ensure_run_scan_end` / run 级 scan_end 语义 | run 层已是权威，本设计只是让根级**读**它 |
| 黑盒「单 agent 失败 → run failed」粒度 | 粒度本身合理（exploit 失败=该类漏洞验证缺失）；若未来要细分 agent 级，另立 spec |
| 假完成保险丝 | 「从未开始」拦截继续有效，本设计在其后追加 run 感知，不重叠 |
| `_reconcile_combined_scan` 的 bb_phase 分支 | 崩溃恢复路径最终也走 `_ensure_scan_end` 收口 → 自动获得 run 感知（验证点见 §4） |

### 2.5 备选方案（已否决，留档）

**B：新状态 `completed_with_errors`**——状态机一眼见，但所有消费方（resume 门控、列表 runStatus、前端 END_LABEL、alarm 语义层）都要认识新值，不认识会当未知态处理；且「有错误」的语义最终仍要靠明细字段回答「哪个 run、为什么」——明细不可省，新状态是冗余信号。**否决**：字段承载明细，状态保持三态。

---

## 3. 边界与竞态

- **收口时 run 尚未终态**（_watch 与黑盒 workflow 竞态）：`_ensure_scan_end` 只在编排收口时调用（黑盒全部 run 已 finalize）；若发现某 run events 无 scan_end 且 bb_runs status 非终态 → 先走既有 `_ensure_run_scan_end` 兜底（幂等）再读——复用现有秩序，不新增等待。
- **多 run**（run-1 failed 后加 run-2 续跑成功）：`failed_runs=["run-1"]` 保持——历史失败不因续跑成功而消失（诚实原则）；全部 run 重跑成功后**是否清除**由「加 run」流程明确写 `failed_runs` 增量维护（run-2 成功 → failed_runs 仍含 run-1，除非产品决定按 latest 收敛——**待 review 定夺**，默认保留历史）。
- **旧数据兼容**：已收口的 scan（如本现场）无 failed_runs 字段 → 前端按无警示渲染；如需补标注，一次性脚本回填（读 run events → 补 session 字段），不在本 spec 范围。

---

## 4. 测试锚点（TDD 清单）

1. **收口感知**：黑盒 run events `scan_end{failed}` + 白盒产物齐 → 根 scan_end `completed` 且 `failed_runs=["run-1"]`，根 session 同步（scan_manager 单测，stub `_collect_run_states` 数据）。
2. **全部失败降级**：唯一 run failed → 根 `status=failed` + detail 透出。
3. **纯白盒零回归**：无 run → payload 无 failed_runs 字段，行为与现状 byte-level 一致。
4. **merged 透传**：tailer 转发的根 scan_end 含 failed_runs（merged_event_tailer 测试加字段断言）。
5. **前端横幅**：scan_end 带 failed_runs → 警示行出现；无字段 → 现状渲染（LiveTab 测试）。
6. **崩溃恢复路径**：`_reconcile_combined_scan` 补收口同样带 run 感知（restart 场景 stub）。
7. **resume 门控不变**：completed+failed_runs 的 scan 仍走「重跑」入口而非「续跑」（回归现有 resume 测试）。

---

## 5. 实施切分（建议）

- **Task 1（core 侧无改动，纯 web）**：`_collect_run_states` + `_ensure_scan_end`/`_write_scan_end`/session 双落盘 + 测试 1-3、6。
- **Task 2（前端）**：LiveTab 警示行 + 列表角标 + i18n + 测试 5。
- **Task 3（回归）**：测试 4、7 + 相邻收口/恢复测试全绿。
