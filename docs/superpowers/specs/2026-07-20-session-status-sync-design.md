# session-status 同步修复设计

- **日期**:2026-07-20
- **分支**:`feat/fork-py`
- **状态**:设计已批准,待写实现 plan
- **触发场景**:NodeGoat 白盒扫描 `NodeGoat_1784484485`,temporal workflow 在 GitNexus 轨完成后因 `write_track_status_activity` 未注册而 FAILED,但 `session.json` 的 `status` 一直停在 `"running"`,Web 实时页"幽灵卡住"19 小时。

---

## 1. 背景与根因

白盒扫描的 temporal workflow 进入 FAILED 时,**整个 codebase 没有任何代码路径**会把 `session.json` 顶层 `status` 写成 `"failed"`。三层缺失叠加导致幽灵卡住:

| 层 | 位置 | 现状 |
|---|---|---|
| **workflow 本源** | `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:629-646` | 无 `except Exception` 分支;`except CancelledError`(line 630)只设内存 `state.status`、不调 `finalize_summary`;`finally`(line 639)只 cleanup。workflow 一旦 raise → `finalize_summary` 永不调用 → `scan_end` 永不写、`session.status` 永不更新。 |
| **CLI worker** | `packages/whitebox/src/shannon_whitebox/worker.py:352-366` | 缺 `except Exception` 兜底(对照 `packages/blackbox/src/shannon_blackbox/worker.py:195-217` 已有,whitebox 漏抄)。`except ScanCancelled` 也只 `return`,不落盘。 |
| **Web 兜底** | `packages/web/src/shannon_web/components/scan_manager.py:_watch`(line 313) | 只 tail `events.ndjson` 等 `scan_end`,**不 query Temporal workflow 状态**。而 `scan_end` 又依赖 `finalize_summary` → workflow FAILED 时 `_watch` 永等不到,默认 `scan_timeout=0.0` 永不主动退出。 |

**关键事实**:顶层 `status` 枚举里**从无代码写入 `"failed"`**(grep 全 repo 确认)。显示层 `WorkspacesIndexer._status_of` 已认 `failed`、`metrics_tracker._TERMINAL_STATUSES` 已接受 `failed`,但就是没人写。

**连带发现**:
- `except CancelledError` 路径同样不落盘(用户 Cancel 后 session.json 也可能卡 running)。
- `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:427-430` 同构隐患(无 `except Exception`),但 blackbox CLI worker 已有兜底,且 blackbox web 未 C1 化(`scan_manager.start()` 里 `NotImplementedError`),当前不触发。

---

## 2. 目标

workflow 进入 FAILED / cancelled 时,`session.json` 顶层 `status` 必须同步成 `failed` / `cancelled`,Web 实时页不再幽灵卡住。覆盖三类失败场景:workflow 代码抛异常、worker 进程崩溃/容器死、CLI 跑扫描失败。

---

## 3. 架构:三层 defense-in-depth

### 3.1 一条 Temporal 铁律(决定分层)

- `except Exception` 块内**可以** `workflow.execute_activity(...)`:此时 workflow 仍存活(异常已被捕获),可继续 schedule activity。
- `except CancelledError` / cancellation 上下文的 `finally` **不可以** `execute_activity`:cancellation 是终止性的,Temporal 会拒绝在取消后再 schedule activity。

**推论**:
- **failed 路径** → workflow 内 `except Exception` 调 `finalize_summary` **可行**。
- **cancelled 路径** → workflow 内调不了 activity,改由 web `_mark_cancelled`(已有)+ CLI `worker.py` 的 `except` 兜底(待补)负责落盘。

### 3.2 三层改动

| 层 | 文件 | 改动 | 覆盖场景 |
|---|---|---|---|
| **workflow 本源(whitebox)** | `whitebox/pipeline/workflows.py:629`(try 末尾 return 之后、`except CancelledError` 之前) | 新增 `except Exception as e:` → 设 `self._state.status="failed"`、记 error;若 `is_worker_path`,构造 `summary(status="failed", error=str(e))` 调 `finalize_summary` activity(复用 line 608-627 的 summary 构造逻辑) | workflow 代码抛异常 / activity retry 耗尽(本次 NodeGoat) |
| **workflow 本源(blackbox)** | `blackbox/pipeline/workflows.py:426`(`except CancelledError` 之前) | 新增 `except Exception as e:` → 设 `state.status="failed"` + 记 error + `return self._state`;**不调 finalize_activity**(规避 `finalize_report` 签名依赖)。blackbox 的 session 落盘靠已有 CLI worker except(`blackbox/worker.py:195-217`)+ 未来 web `_watch` describe 兜底 | 防御性对齐;blackbox web 未 C1 化,当前仅 CLI 路径(已有兜底) |
| **CLI worker(whitebox)** | `whitebox/worker.py:358`(`except ScanCancelled` 之后) | 新增 `except Exception as e:` → `session.log_workflow_complete(_to_workflow_summary(status="failed", errors=[str(e)]))`(抄 `blackbox/worker.py:201-211`);同时给 `except ScanCancelled` 补 `log_workflow_complete(status="cancelled")` | CLI 进程层兜底 + cancelled 落盘 |
| **Web 兜底** | `web/.../scan_manager.py:_watch` | while 循环内周期 `handle.describe()`(默认每 15s,经累计 `asyncio.sleep(0.5)` 计数触发,避免额外 task);`describe().status` ∈ {FAILED, TIMED_OUT, TERMINATED} 时 → 写 `scan_end(status="failed")` + 更新 `session.status="failed"` + break | 进程崩溃 / 容器死 / 被 terminate(workflow 代码没机会跑 except) |

### 3.3 status 写入路径(复用现有)

`finalize_summary` activity(`whitebox/pipeline/activities.py:1722-1755`)→ `AuditSession.log_workflow_complete`(`core/audit/session.py:162-168`)→ `MetricsTracker.update_session_status`(`core/audit/metrics_tracker.py:170-183`)→ 原子写顶层 + 内嵌 `status` + `completed_at`,并触发 StructuredEventRenderer 写 `scan_end` 到 `events.ndjson`。

Web 兜底层(`_watch`)发现 FAILED 时,因 workflow 已无法跑 `finalize_summary`,需**自行**落盘。实现:扩展现有 `_write_scan_end(event_file, status, returncode, stderr_tail)` 增加可选参数 `session_status: str | None = None`;命中 FAILED 时传 `session_status="failed"`,在写 `scan_end` 到 `events.ndjson` 的同时,调 `SessionManager.update_session` 写顶层 `status` + `completed_at`(复用现有 read-modify-write,**不新增 helper**)。

---

## 4. 数据流(三种失败场景 → status=failed)

1. **workflow 内抛异常 / activity retry 耗尽**(本次 NodeGoat):
   workflow `except Exception` → `finalize_summary` activity → `log_workflow_complete` → `update_session_status("failed")` + 写 `scan_end` → `_watch` 见 `scan_end` 正常收尾。
2. **worker 进程崩溃 / 容器死 / 被 terminate**:
   workflow `except` 跑不到 → `_watch` 周期 `handle.describe()` 见 FAILED → 自写 `scan_end(status="failed")` + `session.status="failed"`。
3. **CLI 跑扫描失败**(无 web):
   `worker.py except Exception` → `log_workflow_complete(status="failed")` 直接写 session.json。

---

## 5. status 语义

| status | 含义 | 写入方 |
|---|---|---|
| `failed` | workflow 确定失败(`except` 捕获 / Temporal FAILED) | workflow except / CLI worker except / web `_watch` describe |
| `cancelled` | 用户主动取消 | web `_mark_cancelled`(已有)/ CLI worker `except ScanCancelled`(待补) |
| `interrupted` | 终态不确定(worker 崩溃且 Temporal 也查不到,如 web 长期断连后对账) | `reconcile_orphaned`(保持现状,不动) |
| `completed` | 正常完成 | 现有 `mark_completed` / `finalize_summary` |

`reconcile_orphaned` 继续写 `interrupted` —— 它是"web 重启后对账、终态不确定"的兜底,强行写 `failed` 会误判(worker 崩溃 ≠ workflow 失败)。

---

## 6. 测试策略(TDD)

- **workflow except**:AST 锚点测试断言 `workflows.py` 含 `except Exception` 且分支内调 `finalize_summary`;用 workflow test framework(mocker 使某 activity 抛异常)断言 `finalize_summary` 被调、`status="failed"`。
- **CLI worker except**:mocker `await_workflow_with_shutdown` 抛 `ApplicationFailure` → 断言 `log_workflow_complete(status="failed")` 被调;抛 `ScanCancelled` → 断言 `status="cancelled"` 被写。
- **`_watch` describe 轮询**:mocker `handle.describe()` 返回 `WorkflowExecutionStatus.FAILED` → 断言 `session.json` status 变 `failed`、`scan_end` 写入、`_handles` 清理;返回 RUNNING → 不触发。
- **回归**:现有 completed/cancelled/interrupted 路径不破(跑 `test_scan_manager`、`test_worker`、whitebox workflow 相关测试)。
- **护栏**:加 AST 守卫,锁定 `except Exception` 分支存在(类比现有 `test_worker_registers_*` 回归锚点模式)。

---

## 7. 不做(YAGNI)

- **不动 `reconcile_orphaned`**:`interrupted` 语义合理。
- **不碰历史数据**:受影响 workspace(`NodeGoat_1784484485` 等)与 temporal 僵尸 workflow 靠手动/重跑,本 plan 纯改代码。
- **不动 blackbox web C1 化**:独立 Phase C。
- **不改 `scan_timeout` 默认值**(保持 0.0):`_watch` 的 describe 轮询不依赖 scan_timeout。

---

## 8. 关键文件清单

### 必改
- `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`(加 `except Exception`)
- `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`(加 `except Exception`,对齐)
- `packages/whitebox/src/shannon_whitebox/worker.py`(加 `except Exception` + 补 `except ScanCancelled` 落盘)
- `packages/web/src/shannon_web/components/scan_manager.py`(`_watch` 加 `describe()` 轮询 + `_write_scan_end`/新增 helper 同步 session)

### 必读(契约)
- `packages/core/src/shannon_core/session.py`(SessionManager API)
- `packages/core/src/shannon_core/audit/session.py:162-173`(`log_workflow_complete`)
- `packages/core/src/shannon_core/audit/metrics_tracker.py:170-183`(`update_session_status`,原子写)
- `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:1722-1755`(`finalize_summary`)
- `packages/web/src/shannon_web/components/workspaces_indexer.py:72-84`(`_status_of` 显示态)

### 对照参考(已修对的同类路径)
- `packages/blackbox/src/shannon_blackbox/worker.py:195-217`(CLI `except Exception` 兜底模板)
