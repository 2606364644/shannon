# Worker OOM 韧性修复 Plan

> 状态:设计稿(2026-08-06)。审批后可按 P0 → P1 → P2 实现,或仅采纳设计由用户自行实现。

## 背景 / 根因(基于真机 dmesg 硬证据)

扫描 `hk-user-view-20260806-035435` 在 vuln 分析阶段卡死、永卡 `running`。系统化调试推翻了"GLM 重试挂死 worker"的初步判断,dmesg 给出真根因:

```
Memory cgroup out of memory: Killed process (supernova-worke) anon-rss:2149116kB
```

**根因链:**

1. **内存膨胀**:vuln 阶段 `max_concurrent=3`(`concurrency.py:_DEFAULT=3`)个 in-process openai agent 并行(各带 task subagent)+ `glm-5.2[1m]` 巨大 context(pre-recon/recon 已耗 3.4M cache_read tokens),RSS 累积至 2.1GB,叠加 page cache + 子进程,超 cgroup 限制。
2. **4GB worker 内存限制不足**:`SUPERNOVA_WORKER_MEMORY` 默认 `4g`(`docker-compose.yml:93` `memory: '${SUPERNOVA_WORKER_MEMORY:-4g}'`),cgroup OOM kill(SIGKILL,**无 graceful shutdown**)。worker `RestartPolicy=unless-stopped` 重启,但 `ExitCode=0` 是重启策略记录假象,掩盖了 OOM。
3. **OOM 后扫描不恢复**:worker 重启后,原 workflow 的 activity 丢失、workflow 终止(`temporal workflow describe` -> `workflow not found`),`session.json` 停在 `running`。
4. **收尸缺失**(放大成"永卡 running"):`orphan_reconciler` 只在 web 启动时跑一次(`app.py:62`)+ `/events` 惰性触发(`events.py:35-37`),**无周期性兜底**。web 容器早于 scan 变孤儿就启动(启动 reconcile 够不着);惰性 reconcile 对本 scan 又因 `reconcile_orphaned` 的 `except Exception: return False`(`orphan_reconciler.py:183`)静默吞异常 + 无日志,失效。→ OOM 变孤儿后永卡 `running`。

**纠错记录**:此前判断"GLM `/chat/completions` 重试挂死 worker"是错的。`Retrying request to /chat/completions in 0.479790 seconds` 只是时间线上最后一条日志的巧合(worker 12:34:14 最后一条日志,12:38:44 OOM 重启);dmesg 证明 worker 是被 cgroup OOM kill。openai 引擎缺超时是真实隐患但非本次直接根因,列为 P2。

---

## 修复设计(分层,按优先级)

### P0 — L2:周期性孤儿收尸(直接解决"永卡 running")

**问题**:reconcile 只启动 + 惰性,无主动周期扫描;OOM/崩溃变孤儿后两者都够不着。

**方案**:web lifespan 加周期性后台 reconcile task,复用现有 `_reconcile_orphaned_scans`(`app.py:82`)。

- `app.py` lifespan:`asyncio.create_task(_periodic_reconcile(app))`,与现有 `_purge_task` 并列。
- `_periodic_reconcile`:循环 `await asyncio.sleep(interval)` + `_reconcile_orphaned_scans(app)`;`interval` = `SUPERNOVA_RECONCILE_INTERVAL_SECONDS`(默认 60)。
- shutdown 时 cancel(对齐 `app.py:78` 的 `_purge_task.cancel()`)。
- **安全**:`reconcile_orphaned` 内部已有 `is_scan_alive`(heartbeat 90s fresh)+ `_workflow_still_running` 双门控(`orphan_reconciler.py:147,155`),活 scan 不会被误杀;周期任务只补写已死 scan 的 `scan_end`。
- **效果**:OOM/崩溃后,最多 `heartbeat 宽限 90s + reconcile 间隔 60s ≈ 150s` 内,scan 被标 `interrupted` + 写 `scan_end`,live 页显"已中断"+原因,不再永卡。

### P0 — L1:防 OOM(env 调整,治本,零逻辑改动)

**问题**:4GB 不够 3 并行大 context agent。

**方案**(全是 env / 默认值,不改执行逻辑):

- `SUPERNOVA_WORKER_MEMORY` 默认 `4g` → `8g`(`docker-compose.yml:93` `:-4g` 改 `:-8g`)。给 `glm-5.2[1m]` 多 agent 并行留余量。
- `SUPERNOVA_MAX_CONCURRENT` 默认 `3` → `2`。减同时驻留的 in-process agent 数,直接降峰值内存。**实现位置二选一**(plan 确认项):
  - (a) 改 `concurrency.py:6` `_DEFAULT = 3` → `2`(影响 CLI + web 全局);或
  - (b) 仅 web 部署降:`docker-compose.yml` worker `environment` 段加 `SUPERNOVA_MAX_CONCURRENT=2`(CLI 不动,更稳妥)。
  - 推荐采用 (b),并在 plan 确认 web scan_manager 提交 workflow 时确实经 `get_max_concurrent()` 注入 `ActivityInput.max_concurrent`。
- 二者配合:8GB 内存 + 2 并发,覆盖 vuln 阶段峰值。

### P1 — L3:reconcile 可观测性 + 排查惰性失效

**问题**:`reconcile_orphaned` 每个 `return False` 与 `except Exception` 都静默,无法排查本 scan 惰性 reconcile 为何失效。

**方案**:

- `orphan_reconciler.py:reconcile_orphaned` 加 `logging.debug`:每个 `return False` 记原因(is_running / is_scan_alive / workflow_running / 已结案 / 已有 scan_end);`except Exception`(`:183`)改 `logger.exception(...)`(不再静默吞)。
- `_workflow_still_running` 的 `Client.connect` / `describe` 异常补 `logger.debug`(现有 best-effort 降级保留,补可观测)。
- 排查本 scan:启用 DEBUG 后访问 `/events`,看 reconcile 卡在哪个门控或异常 → 修对应缺陷(可能是 `SessionManager(ws_dir.parent)` 路径语义,`:158`)。

### P2 — L4:openai 引擎超时/重试可控(防御性,非本次主因)

**问题**:`AsyncOpenAI(**kwargs)`(`providers_openai.py:133`)未传 `timeout`/`max_retries`,用 SDK 默认 600s/2 次;`_call_timeout` 默认 2400s(`:166`)兜底流式消费,stall 时偏长。

**方案**(可单独立项,不阻塞 P0):

- `_get_client`:`AsyncOpenAI(**kwargs, timeout=<env>, max_retries=<env>)`,新增 `SUPERNOVA_OPENAI_HTTP_TIMEOUT`(默认 300s)/ `SUPERNOVA_OPENAI_MAX_RETRIES`(默认 1)。给 httpx 层 connect/read 熔断,避免单请求无限挂。
- `_call_timeout` 默认 2400s 评估调短(如 1200s)或按 agent tier 分级。
- **风险**:timeout 过短会误杀 `glm-5.2[1m]` 大 context 正常长请求,需真机标定后再定值。

---

## 验证

- **L2**:`docker kill supernova-worker`(模拟 OOM)→ ~150s 内 `session.json` status → `interrupted`、`events.ndjson` 末尾有 `scan_end`;live 页显"已中断"。单测:mock heartbeat stale + workflow not running,断言周期 task 写 scan_end。
- **L1**:重跑 scan,`docker stats` 看 worker RSS 峰值 < 8GB、`dmesg` 无 OOM。
- **L3**:DEBUG 日志看 reconcile 决策路径;本 scan /events 触发后能看到卡点。
- **L4**:单测 `AsyncOpenAI` 收到 timeout/max_retries 参数。

## 不做 / 待定

- 不改 workflow execution timeout(`workflow not found` 的精确机制待定,但 L2 周期收尸已兜底状态一致性)。
- 不改 temporal `retry_policy`(`maximum_attempts=3` 合理;OOM 是 worker 级非 activity 级,重试无济于事)。
- L4 若判定次要,可单独立项,不阻塞 P0。

## 改动文件

- **P0 L2**:`packages/web/src/supernova_web/app.py`(lifespan 加 `_periodic_reconcile` task + shutdown cancel)+ 测试。
- **P0 L1**:`docker-compose.yml`(`WORKER_MEMORY:-8g` + worker `environment` 加 `SUPERNOVA_MAX_CONCURRENT=2`)。
- **P1 L3**:`packages/web/src/supernova_web/components/orphan_reconciler.py`(加日志 + `logger.exception`)。
- **P2 L4**:`packages/core/src/supernova_core/agents/providers_openai.py`(`_get_client` timeout/max_retries)+ 测试。

## 部署生效

- L1 改 compose → `docker compose up -d --force-recreate worker` 重建。
- L2/L3 改 web 代码 → rebuild web 镜像。
- L4 改 core 代码 → rebuild worker 镜像(core 进 worker)。
