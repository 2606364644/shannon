# Temporal 原生取消接管全流程（2026-08-28）

## 背景 / 根因

2026-08-28 NodeGoat-20260827-152204 事故：web 点取消（02:01:29）后前端立刻翻转
cancelled（按钮消失），后台 poc-agent 持续刷日志 9+ 分钟，靠手动 terminate 才停。

证据链（详见 plan `calm-snuggling-snowflake`）：

1. `handle.cancel()` 已送达 Temporal（describe `CancelRequested: true`）但
   Status=Running——阻塞在 pending activity `run_report_polish`（20min timeout ×
   3 retry），其内部逐类跑 poc-agent。
2. **全仓 activity 从不调用 `activity.heartbeat()`**——Temporal 投递取消给运行中
   activity 的唯一通道是 heartbeat；不心跳的 activity 收不到取消，跑满超时且失败
   重试，workflow 才有机会处理取消（最坏 ~1h）。
3. 协作式通道（`cancel.requested` 文件）双断：`_cancel_combined` 不写该文件；且
   owner=web 的扫描由常驻 worker 容器 poll task queue，无 per-scan 宿主进程——
   `HeartbeatManager(on_cancel=...)` 只存在于 host CLI 路径（且 `start_heartbeat`
   固定 on_cancel=None）。
4. `_mark_cancelled` 先行翻转 session 终态 → 界面已取消、后台照跑的幽灵态。

**决策（用户）**：治本——让 Temporal 原生取消接管全流程，一取消全都取消。

## 根因深挖修正（实施中，worker 容器日志实锤）

事故时刻（18:01:29Z cancel）worker 日志：

```
18:01:30 WARNING write structured poc failed (non-fatal): Activity cancelled
18:01:30 STEP ✓ 组装 report_data.json 初版 343ms   ← workflow 继续跑
18:01:30 STEP ○ report_data 终版                    ← 又起新 activity（polish）
18:02:45 AGENT ✗ poc-agent-auth failed (9m 11s)     ← 旧 activity 的 gather 子任务跑满
```

三层叠加（T1 实测佐证）：

1. **workflow 层吞取消（关键，原方案漏掉）**：whitebox workflows.py:685 等处的
   non-fatal 富化降级 `except Exception` 把 `ActivityError('Activity cancelled')`
   当普通失败吞掉 → workflow 不死，继续起 assemble/polish 新 activity。
   temporalio 的 cancel 注入是「一次信号」——被业务吞掉后，**server 不会对
   后续新 activity 自动重发 RequestCancelActivityTask**（实测 polish 跑满
   9min 无人管）。修复：所有吞点在吞之前放行取消类异常
   （`temporalio.exceptions.CancelledError` 及 ActivityError 的 cause 链）。
2. **activity 层不心跳**：write_agent_poc 的 gather 子 poc-agent 不 heartbeat
   → activity 本体收不到 cancel，跑满 9m11s（修 B 解决）。
3. workflow 沙箱对 cancel 传播：RequestCancelActivityTask 后 activity future
   **立即**抛 ActivityError（不等 activity 真死）——T1 反例实测 workflow 3s
   内 CANCELED（探针无 except）；这也解释 1 里 ActivityError 为何立即到达。

## SDK 机制核实（temporalio 1.27.2 源码，.venv）

1. cancel 到达 → worker poll 循环 cancel 消息 → `_RunningActivity.cancel()` →
   **cancel 整个 activity asyncio task** → CancelledError 抛在业务 await 点
   （LLM 调用处）。helper 无需 shield/传播。
2. `activity.heartbeat()` 同步函数永不抛（put_nowait 进队列）；contextvar 随
   `asyncio.create_task` 继承 → gather 子任务各自起 helper 均有效。
3. 上下文探测：`activity.info()` 上下文外抛裸 `RuntimeError` → except RuntimeError
   探测模式（项目三处同款惯例）。
4. **心跳节流**：`Worker(default_heartbeat_throttle_interval=30s)`——不设
   heartbeat_timeout 时每 30s 才真发心跳 RPC → 取消传播上限 ~30s；收紧 worker
   参数到 10s → ~10s 级。
5. heartbeat_timeout 与取消传递解耦（SDK 专门为无 timeout 定义节流参数佐证），
   T1 真 server 测试裁决 + web terminate 保险丝双保险。
6. activity 结束后 heartbeat no-op（`if activity and not activity.done`）。

## 设计

| 层 | 改动 | 文件 |
|---|---|---|
| A | `activity_heartbeat()` async ctx：非 activity 上下文 no-op；否则起周期心跳 task，exit 只 cancel 不 await | core `runtime/temporal_heartbeat.py`（新增） |
| B | `run_claude_prompt` 的 `provider.call` 外包 `activity_heartbeat()`——白盒三层+黑盒 exploit/report/auth 全收敛点，一处覆盖 100% LLM 调用 | core `agents/runner.py` |
| C | claude 引擎 `_execute_query` 的 async for 包 `contextlib.aclosing`（cancel 时显式关 SDK 生成器 → CLI 子进程清理；兼修 break 靠 GC 缺口） | core `agents/providers_anthropic.py` |
| D | openai 引擎 call 内层 try 加 `finally: suppress(Exception) close()`；删两处显式 close（非幂等） | core `agents/providers_openai.py` |
| E1 | `_cancel_combined` 对 latest 非终态 run 无条件 best-effort cancel `-bb-{K}`（关 submit→标 running 竞态窗口） | web `scan_manager.py` |
| E2 | terminate 保险丝：cancel 后 60s 复查仍 RUNNING 且 run_id 未变 → terminate（防 -corr 固定 id 重建误杀）；挂 _cancel_combined/_cancel_correlation/① 轨 | web `scan_manager.py` |
| F | 常驻 worker 三队列 `default_heartbeat_throttle_interval=10s`（取消传播 30s→10s 级） | worker `runner.py` |

**不改**：54 处 `execute_activity` 不加 heartbeat_timeout（避免「event loop 饿死→
心跳停→误判失败→retry 从头重跑 LLM」回归，chain_verdict 容量铁律敏感）；黑盒
gather(return_exceptions=True)；workflow 层 except CancelledError 结构；host CLI
路径 HeartbeatManager/ShutdownController。

**残留窗口（有意取舍）**：非 LLM 长 activity（run_code_index 20min、
run_auth_validation_probe 10min、playwright 类）不经过 run_claude_prompt →
「跑完当前 activity」窗口仍在，由 60s terminate 保险丝兜底。

## T1 实测结果（packages/core/tests/runtime/test_temporal_cancel_propagation.py，3 绿）

1. **heartbeat 传递取消：成立且极快**——`activity.heartbeat()` 周期调用 +
   **不设 heartbeat_timeout**，cancel 后 0.84s 内 CancelledError 抛在业务
   await 点、workflow 终态 Canceled。回退路径（补 heartbeat_timeout）不需要。
2. **workflow cancel 不等 activity 终态**——RequestCancelActivityTask 后沙箱
   activity future 立即抛 ActivityError（即使 activity 本体还在跑）：「workflow
   终态」与「activity 本体存活」是两回事。
3. **吞取消泄漏 Running：钉死**——except Exception 吞掉
   ActivityError(cancelled) 后 workflow 推进（stage=phase2）且 15s 观察窗内
   终态不翻转；吞后新起的 activity 即使带 heartbeat 也可能收不到取消
   （worker poll 通道 cancel 推送时序有竞态，实测不稳定）。
4. **实施中发现**：worker poll 通道的 cancel 推送与 heartbeat 通道并存但时序
   不稳——heartbeat 是确定性通道（修 B 的价值），poll 通道不能依赖。

## 实施补充记录

- `supernova_core/runtime/__init__.py` 改 PEP 562 lazy re-export：workflow 沙箱
  import temporal_heartbeat 时旧版急切 re-export scan_runner → prerequisites
  顶层 `Path(__file__).resolve()` → RestrictedWorkflowAccessError（回归于
  whitebox 真 env 测试暴露，migration 测试复绿）。
- worker 容器 runner.py 补注册 `persist_completed_agents`（白盒+黑盒，另一会话
  遗留缺口，test_run_worker_registers_all_defined_activities 锁定）。
- 预存挂起（与本次无关，基线复现）：whitebox
  test_workflow_heartbeat_execution、blackbox test_workflow_proxy_orchestration；
  test_providers_openai_call_l1 预存失败（8eb53e10 提交信息已记录）。

## 回退路径

T1 证伪「无 heartbeat_timeout 时 heartbeat() 仍传 cancel」→ 长时 LLM activity
补 `heartbeat_timeout=10min`（节流=8min → 取消延迟劣化分钟级）+ 保险丝 delay 提
90s，并在此记录。
