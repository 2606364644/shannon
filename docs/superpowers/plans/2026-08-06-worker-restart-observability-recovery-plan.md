# Worker 重启后可观测信号恢复 Plan(方案 A)

> 状态:设计稿(2026-08-06)。基于真机 hk-user-view-20260806-035435 排查。
> 上一轮 OOM 立项的后续:worker OOM 重启后 temporal workflow 恢复执行,但进程内可观测信号丢失致 live 页失明。

## 背景 / 根因

扫描 `hk-user-view-20260806-035435` worker 12:38 OOM 重启(cgroup 4GB 限制)后:

- **temporal 侧**:workflow `__legacy__-hk-user-view-20260806-035435` 仍 RUNNING,15:02-15:19 CST activity 持续 schedule/start/complete,xss/auth-vuln agent 失败重试(attempt 3/8)。**扫描没卡死,在跑。**
- **live 页侧**:events.ndjson / heartbeat / workflow.log 全停在 12:34-12:38,看起来像卡住。**实为失明,非停摆。**

**根因**:可观测信号(AuditSession 写 events.ndjson + HeartbeatManager 写 heartbeat)是**进程内全局单例**,由 `setup_display`(workflow 首个 activity)初始化一次。temporal 恢复 workflow 时只重新调度**失败的 activity**(如 xss-vuln),**不重跑已 completed 的 `setup_display`**。新 worker 进程的 `_SESSIONS` dict 空 -> `get_audit_session()` 返回 `NullAuditSession`(`session_registry.py:111-114`) -> 所有事件 no-op 静默丢弃、heartbeat 不写。

**违反 temporal 最佳实践**:temporal 原则要求 activity 无状态、可重入、不依赖进程内状态。本设计将 AuditSession 重建做成 activity 入口的幂等 guard,逼近"activity 自包含"语义。

## 设计目标

worker 重启后,temporal 恢复执行的 activity 能**幂等重建 AuditSession + heartbeat**,恢复 events/heartbeat 写入,使 live 页不再失明。

## 关键事实(设计依据)

1. `session_registry._SESSIONS` 是按 workflow_id 索引的进程内 dict(`session_registry.py:21`)。worker 重启即清空。
2. `get_audit_session()` 在无 session 时返回 `NullAuditSession`(no-op,`session_registry.py:111-114`)--**这是判别"需重建"的信号**:`isinstance(session, NullAuditSession)`。
3. `AuditSession.initialize`(`session.py:35`)无幂等保护,但重建时是**新对象**,不存在重复初始化问题。
4. **events.ndjson 是 append 模式**(`structured_event_renderer.py:79` `aiofiles.open(self._path, "a")`)--重建后新事件**追加到原文件末尾,不覆盖**,天然接上旧事件流。
5. `ActivityInput` 含重建所需全部字段:`event_file` / `workspace_path` / `workspace_name` / `repo_path` / `web_url`(`shared.py:43-61`)。每个 activity 都收到 `ActivityInput`,入口可重建。
6. 黑盒同构:`blackbox/pipeline/activities.py:setup_display` 一次性 + 多 activity 入口 `get_audit_session()`。

## 方案 A:Activity 入口幂等恢复

### 核心:抽 `_ensure_audit_session(input)` helper

在白盒/黑盒各自 `activities.py` 抽一个共享 helper(或放 core 复用),每个 LLM activity 入口调一次:

```python
async def _ensure_audit_session(input: ActivityInput) -> None:
    """幂等:进程内无本 workflow 的 AuditSession(worker 重启后)则重建。

    setup_display 只在 workflow 首跑一次;temporal 恢复 workflow 时不重跑它,
    重启后的 worker 进程 _SESSIONS 空 -> get_audit_session() 返 NullAuditSession ->
    事件/heartbeat 静默丢。本 guard 在每个 LLM activity 入口检测 Null 则按 workflow_id
    重建 AuditSession + 重启 heartbeat,恢复 events.ndjson(append,接旧流) + heartbeat 写入。

    幂等:非 Null 则直接返回(setup_display 已建);同 workflow 多 activity 并发时 dict
    写入按 workflow_id 索引,后到者命中已建 session 跳过。
    """
    from supernova_core.audit.session_registry import get_audit_session, NullAuditSession, set_audit_session
    session = get_audit_session()
    if not isinstance(session, NullAuditSession):
        return  # setup_display 已建,正常路径
    # worker 重启后恢复路径:重建
    ...  # 复用 setup_display 的 AuditSession 构造逻辑(抽公共 _build_audit_session)
    await start_heartbeat(ws_path)
```

**实现要点**:
- 把 `setup_display` 里 AuditSession 构造段(meta + Console + initialize + LogBus.attach + set_audit_session + start_heartbeat)抽成 `_build_audit_session(input)`,`setup_display` 和 `_ensure_audit_session` 共用,避免逻辑重复。
- 判别用 `isinstance(session, NullAuditSession)`--NullAuditSession 是单例语义的 no-op,是"无 session"的明确信号。
- 重建的 session 是全新 metrics 累积(从 0 开始),**已知代价**:跨重启的 cost/metrics 偏低(见"取舍")。

### 接入点(每个 LLM activity 入口)

**白盒**(`pipeline/activities.py`):
- `run_agent`(:184) -- 覆盖 pre-recon/recon/vuln 全部(pre-recon/recon 在 worker 重启后也可能被重试)
- `run_authz_gitnexus_judge`(:452) -- 多轮深度 agent
- `run_attack_chain_llm_agent`、`run_gitnexus_chain_verdict` 等 LLM activity -- 评估接入(凡调 `get_audit_session()` 且可能 worker 重启后被调度的)

**黑盒**(`blackbox/pipeline/activities.py`):
- `run_blackbox_auth_validation`(:170)、`run_exploit_agent`(:238)、`run_report_agent`(:443)、`run_endpoint_verify`、`run_auth_validation_probe` -- 同构接入。

**接入位置**:每个 activity 函数体首行(`get_audit_session()` 之前)`await _ensure_audit_session(input)`。

### 不接入的(保持现状)

- `setup_display`:已是首次建 session 的入口,本身不需 guard(它就是 builder)。但改为调 `_build_audit_session` 共用逻辑。
- 纯确定性/日志 activity(`run_code_index`、`run_merge_*`、`log_phase_*`、`run_preflight`):worker 重启后这些若被重试,失明影响小(无 LLM 事件可看),且它们多数幂等快。**初版只接 LLM activity**(失明主痛点),确定性 activity 评估后定。← 待 plan 确认范围。
- `finalize_summary`:终态 activity,写 scan_end。worker 重启后若 workflow 推进到 finalize,也需能写 scan_end--**应接入**(否则 scan 跑完但 scan_end 不写,live 页不收尾)。

## 取舍(诚实记录)

1. **跨重启 metrics/cost 偏低**:重建的 session 从 0 累积,重启前 pre-recon/recon 的 metrics(本 case ¥9.5/3.4M tokens)不在新 session 里。`finalize_summary` 从 `MetricsTracker` 取 session 生命周期累积 -> 最终报告 cost 偏低。**这是 A 的已知代价**。可接受:扫描仍跑完、漏洞仍产出、单 agent 内 metrics 仍准;仅全局 total 偏低。彻底修需把 metrics 持久化到磁盘(属 B 范畴,不做)。
2. **重复事件风险**:重建后 `start_agent` 会再写一条 agent-start 事件。因 events 是 append,live 页会看到同一 agent 两条 start(重启前一条 + 重启后一条)。**可接受**:语义上 agent 确实被重试了,两条 start 反映真实重试;前端按 agent 名+attempt 去重展示即可(评估前端是否需调)。
3. **heartbeat 恢复**:重建即 `start_heartbeat`,heartbeat mtime 恢复 fresh -> web `is_scan_alive` 重新判 True -> live 页不再误判 interrupted。**正收益**。

## 验证(TDD)

1. **单元**:mock worker 重启场景--`_SESSIONS` 清空 -> 调 `_ensure_audit_session(input)` -> 断言 `get_audit_session()` 非 Null、events.ndjson 追加写入、heartbeat 文件 mtime fresh。
2. **幂等**:已建 session 时 `_ensure_audit_session` 不重建(断言不重复构造)。
3. **append 接旧流**:预写 events.ndjson 旧事件 -> 重建 session 写新事件 -> 断言文件含旧+新、无覆盖。
4. **集成**(可选,重):temporal worker 重启模拟--起 workflow -> kill worker -> 重启 -> 断言 events.ndjson 在重启后继续增长、heartbeat 恢复。

## 改动文件

- `packages/whitebox/src/supernova_whitebox/pipeline/activities.py`:抽 `_build_audit_session` + `_ensure_audit_session`,`setup_display` 复用,LLM activity 入口接入。
- `packages/blackbox/src/supernova_blackbox/pipeline/activities.py`:同构改动。
- 共用 helper 放 `packages/core/src/supernova_core/audit/`(若抽公共,白盒黑盒共用)。
- 测试:白盒/黑盒 activity 测试 + `_ensure_audit_session` 单测。

## 部署生效

改 core + whitebox + blackbox 代码 -> rebuild worker 镜像(worker 跑这些 activity)。

## 待 plan 确认项

1. **接入范围**:初版只接 LLM activity + finalize_summary,确定性 activity(code_index/merge/preflight)不接?还是全接?
2. **helper 归属**:放 core 公共(白黑盒共用)还是各自 activities.py 内?
3. **前端去重**:重建致的重复 agent-start 事件,前端是否需调(按 agent+attempt 去重展示)?
