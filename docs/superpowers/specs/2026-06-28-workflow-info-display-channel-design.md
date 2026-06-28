# workflow → 用户提示显示通道（InfoEvent）

**日期**：2026-06-28
**状态**：design（brainstorming 产出，待 review）
**分支**：feat/fork-py
**关联**：`blackbox-workflow-sandbox-paths-invariant` memory、cb5a842、`2026-06-28 validate_queue` 修复（commit 66b8744）

---

## 1. 背景

黑盒 `--rerun` 进 exploitation 阶段前，终端出现三段输出堆叠在同一物理行：

```
— · 0 done · 0ms · $0.0000No whitebox results found at .../NodeGoat_202606[2026-06-28 14:28:41] PHASE  Starting recon-blackbox ─────────────
```

- `— · 0 done · 0ms · $0.0000`：LiveDashboardRenderer 的 footer（spinner），stdout，`\r` 原地重绘，**行尾无换行**。
- `No whitebox results found...`：`workflows.py:222` 的 `logger.warning`，走 Python logging **lastResort → sys.stderr**（该 logger 未配 handler）。
- `[ts] PHASE Starting recon-blackbox`：经 dispatcher → RichConsoleRenderer → Live console（本应被 scroll 到 footer 上方）。

## 2. 根因

workflow（sandbox 线程）里的 `logger.warning/info` 走 Python logging lastResort → `sys.stderr`。而 `display_lifecycle.run_with_display` 显式设 `redirect_stderr=False`（注释 display_lifecycle.py:30-43：redirect 会让 sandbox 线程 logging 触发 rich 重入 → 循环 import，每个 workflow task 崩）。

后果：任何 workflow 线程走 stderr 的输出**绕过 Live console**，直接落终端当前光标位置（此刻停在 footer 行末，footer 行无换行）→ 与 footer 粘连；紧跟的、经 console 的 PHASE 本应被 Live scroll 上去，也被拖在同一物理行。

**这是普遍问题**：不只是这一条 warning。受益面 = blackbox `workflows.py` 6 处（209/216/222/277/312/369）+ whitebox `workflows.py` 3 处（298/318/366）同类裸 `logger.info/warning`，都走 stderr、都可能粘连 footer。且这些目前**都不进 workflow.log**（文件由 dispatcher 管，logging 不管）——顺带是持久化缺口。

**已在显示通道覆盖的（不在本范围）**：
- 业务 ERROR：`session.log_error → ErrorEvent → _render_error`（rich_renderer.py:117）。
- temporalio activation 堆栈：`install_temporalio_log_redirect` 重定向到 `activity_failures.log`。

真正未被接管的是 **info/warning 级的流程提示**——没有对应显示事件类型（workflow_logger.py:163 注释自承："if Rich output of generic events becomes needed, introduce a GenericEvent type and route through the dispatcher"）。

## 3. 方案

新增 `InfoEvent` 显示事件 + `log_info` 通道：让 workflow 面向用户的流程提示经 activity → dispatcher → Live console（scroll 到 footer 上方）+ FileLogRenderer（补齐 workflow.log 持久化）。与现有 `PhaseEvent`/`AgentEvent`/`ErrorEvent` 完全对称。

单 `InfoEvent` + `level` 字段（`info`/`warning`）区分严重度（warning 着黄色），不拆成两个事件类型。

## 4. 数据流

```
workflow.run()  (sandbox 线程)
  → workflow.execute_activity(log_info_activity, input{info_message, info_level})
      → activity  (activity_executor 线程，非 sandbox)
          → session.log_info(message, level)
              → DisplayDispatcher.dispatch(InfoEvent)
                 ├→ FileLogRenderer     写 [ts] [INFO|WARNING] msg   ← 补齐持久化
                 └→ RichConsoleRenderer  console.print → Live scroll 到 footer 上方
```

workflow 在 sandbox，不能直接碰 session（同步对象），故经 activity——与 `log_phase_start_activity` 同一模式（也是 cb5a842 / validate_queue 的"sandbox 不能直接做的事挪 activity"范式）。

## 5. 组件改动

### 5.1 core（shannon_core，两 pipeline 共享）

| 文件 | 改动 |
|---|---|
| `display/events.py` | 新增 `InfoEvent(DisplayEvent)`：`message: str` + `level: Literal["info","warning"] = "info"`（frozen dataclass） |
| `audit/workflow_logger.py` | 新增 `async def log_info(self, message, level="info")` → `dispatch(InfoEvent(...))`（照 `log_phase`） |
| `display/rich_renderer.py` | match/case 加 `case InfoEvent(): self._render_info(event)`；`_render_info` → `console.print(f"[{ts}] [{color}]{LABEL}[/]  {msg}")`，`info`→cyan/`INFO`，`warning`→yellow/`WARNING` |
| `display/file_renderer.py` | InfoEvent → 写 `[ts] [INFO|WARNING] msg`（照现有 phase 行格式） |
| session 抽象 | `log_info` 接口：base/real（透传 WorkflowLogger.log_info）+ NullAuditSession no-op（照 `log_phase_start` 在两处的分布） |

### 5.2 blackbox

| 文件 | 改动 |
|---|---|
| `pipeline/shared.py` | `BlackboxActivityInput` 加 `info_message: str \| None = None` + `info_level: str = "info"`（同 `phase` 字段模式，shared.py:48） |
| `pipeline/activities.py` | `log_info_activity(input)` → `await get_audit_session().log_info(input.info_message, input.info_level)`（照 `log_phase_start_activity` activities.py:403，无 try/except） |
| `worker.py` | 注册 log_info_activity（import + activities 列表） |
| `pipeline/workflows.py` | 6 处 `logger.info/warning` → `workflow.execute_activity(activities.log_info_activity, BlackboxActivityInput(**{**act_input.__dict__, "info_message": ..., "info_level": ...}))` |

### 5.3 whitebox

| 文件 | 改动 |
|---|---|
| `pipeline/shared.py` | `ActivityInput` 加 `info_message`/`info_level`（shared.py:39，照 `phase` shared.py:50） |
| `pipeline/activities.py` | `log_info_activity(input: ActivityInput)`（照 `log_phase_start_activity` activities.py:191，但**不需要** steps/intents 参数，签名仅 `(input)`） |
| `worker.py` | 注册（import worker.py:34 + 列表 worker.py:106） |
| `pipeline/workflows.py` | 3 处 `workflow.logger.info` → `execute_activity(log_info_activity, ...)` |

## 6. 错误处理

提示是 **best-effort**：
- `log_info` 内部 check dispatcher，None 时 no-op（已有模式，workflow_logger.py:120 等）。
- activity 不 raise（与 `log_phase_start_activity` 一致，无 try/except）；即便 session/disceptor 异常，提示丢失也不影响扫描结果。
- 多行 message（blackbox workflows.py:312 validation summary、369 exploit summary）：`console.print` 原生支持多行，无需特殊处理。

## 7. 测试策略（TDD，每组件先红后绿）

- **InfoEvent 数据契约**：字段（message/level 默认值、frozen）。
- **WorkflowLogger.log_info**：dispatch 出 InfoEvent（mock dispatcher 断言 event 类型 + 字段）。
- **RichConsoleRenderer._render_info**：输出含时间戳 + `INFO`/`WARNING` label + message；level=warning 走 yellow（断言 markup 含 yellow）。
- **FileLogRenderer**：InfoEvent 写出 `[ts] [INFO|WARNING] msg` 行。
- **log_info_activity**：调 `session.log_info(message, level)`（mock session 断言参数）。
- **worker 注册守卫**（两 pipeline 各一）：`log_info_activity` 在 worker.py count >= 2（照 test_sandbox_safety.py `test_worker_registers_*` 范式）。

**不加** "workflow 不裸调 logger.warning" 守卫——`logger.warning` 也用于真诊断 log，无法机械区分"用户提示 vs 诊断"，强行匹配会误伤。靠迁移 + review。

## 8. 范围

**本批**：core（events/workflow_logger/rich_renderer/file_renderer/session）+ blackbox（6 处 + activity/shared/worker）+ whitebox（3 处 + activity/shared/worker）。

**不在范围**：
- ERROR 行（已走 `ErrorEvent`）。
- temporalio activation 堆栈（已重定向文件）。
- "workflow 不裸调 logger.warning" 机械守卫（见上）。

## 9. 风险与权衡

- **whitebox `workflow.logger` 行为**：whitebox 现用 temporalio 的 `workflow.logger.info`，其 stderr 走向待落地验证。改经 activity → dispatcher 后，与 blackbox 行为一致（同走 RichConsoleRenderer），不再依赖 `workflow.logger` 的通道。
- **InfoEvent 序列化**：frozen dataclass，但 **不跨 activity 边界**——InfoEvent 在 activity 内 `dispatch`，不作为 activity 返回值，故无 temporalio data_converter 序列化问题（规避了 validate_queue 修复里 `QueueValidationResult` 注解求值 NameError 那类坑）。
- **不加守卫的代价**：未来新写的 workflow 裸 `logger.warning` 仍会粘连，靠 review 把关。可接受的代价（守卫无法精确区分）。
