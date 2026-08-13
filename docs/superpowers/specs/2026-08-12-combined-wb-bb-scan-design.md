# 白盒+黑盒一键组合扫描（Combined Scan）设计

> 日期：2026-08-12（2026-08-13 修订：D1-D5 + 进度分层）
> 状态：设计定稿（brainstorming + 代码核查产出，待 writing-plans 拆任务）
> 主题：消除白盒/黑盒在 WEB 层的割裂——白盒扫描页一键发起「白盒→黑盒自动接力」组合扫描，单目录三桶产物，三视图报告，列表层分段进度可见，黑盒失败可换认证续跑。

---

## 1. 背景与动机

当前白盒与黑盒在 WEB 层是「两类独立扫描 + 黑盒挂在某个白盒之下」的割裂模型：

- **发起割裂**：扫描页一个 Tab 三态切换（`ScanNewPage.tsx:15`），白盒/黑盒是两套完全不同的表单。黑盒被 Pydantic 钉死成「必须复用某个已完成白盒」（`models.py`），用户要先提白盒、等完成、再回来填黑盒选那个白盒——两次提交、两次等待。
- **进度割裂**：白盒、黑盒是两个独立 Temporal workflow、两条独立事件流、两个 scan_id，列表里是两条独立记录，看不出关联与整体进度。
- **结果割裂**：报告页/详情页 per-scan 渲染，不跨 track 合并。
- **不可补救**：黑盒失败（如认证过期）就要重扫，白盒的一两个小时白跑。

仓库已有半成品 `packages/combined/`（`orchestrator.py` `run_combined_scan`，顺序 CLI），但只接 CLI、未接 WEB、自起 Worker、无统一 session。

**用户诉求**：WEB 一键同时跑白盒+黑盒，不丢失任何单独视角的报告；列表就能看到分段进度（白盒/黑盒/联合 x%）；黑盒失败能调整认证后复用白盒结果续扫。

---

## 2. 设计决策

| 维度 | 决定 |
|---|---|
| **范围** | 端到端：一键发起 + t0 认证预验证 + 单页统一进度（含 x%）+ 融合报告 + 黑盒失败续跑 |
| **入口** | 白盒入口升级：白盒扫描页加开关，打开展开填黑盒信息(URL+认证)→同时扫；关闭=纯白盒。不新增 scan_type |
| **接力** | 技术上只能先白盒后黑盒（黑盒强依赖白盒产物），自动串行 |
| **t0 预验证** | 提交时先用认证登一次目标站（复用 `AuthValidationWorkflow`），失败 fail-fast 不跑白盒——避免白盒白跑 1-2h 后黑盒才发现认证错 |
| **进度** | 列表卡片**收起**显示粗略 `progress_pct` + 阶段名；**展开**显示步级详细（分段时间线 + 每段步进度）。靠 `bb_phase` 状态机切段 |
| **报告** | 三视图：白盒报告 / 黑盒报告 / 融合报告（vuln_class 交叉 + 摘要）。漏洞类级对齐，不做单漏洞 1:1 |
| **接力机制** | 后端编排接力（scan_manager `_combined_orchestrator` await 白盒完成 → 自动提交黑盒），关浏览器也能接力 |
| **黑盒续跑** | 黑盒阶段 failed 后，可（多次）换认证续跑黑盒，复用白盒产物不重跑 |
| **物理形态** | 单目录三桶：组合扫描只建一个 `scans/<wb>/`，下含 `deliverables/{whitebox,blackbox,combined}/` + 单 session + 单 events |

---

## 3. 非目标与不变量

**非目标**：
- 不做单个漏洞级（vuln instance）白盒↔黑盒 1:1 对齐（黑盒 exploit 产物不带白盒漏洞 id，最强为 vuln_class 级）。
- 不改纯白盒、纯黑盒独立入口的行为（零回归）。
- 不引入新 scan_type（组合扫描的 scan_type 仍是 `whitebox`，靠 session `combined=true` 标记区分）。

**不变量（铁律）**：
- **纯白盒（开关关）**：行为与产物布局与今天完全一致，只有 `deliverables/whitebox/`。
- **纯黑盒入口**（对已有白盒追加黑盒）：仍是独立 `<wb>~N/` scan_dir + 独立 session/events（现有行为不变）。
- **黑盒 workflow 零代码改动**：单目录靠接力时控制 `event_file` / `repo_path` 两个入参实现（见 §5）。
- **白盒 workflow 最小改动**：`PipelineInput` 加 `combined: bool`，`finalize_summary` 组合模式改调现有 `log_phase_complete` 写 `PhaseEvent`（非 `scan_end`）。**不新增事件类型**（D1，见 §7.1）。
- **`scan_end` 语义不变量**：全场景 events 文件只有一个 `scan_end` = 真结束；组合模式阶段边界用现有 `PhaseEvent` 表达，绝不剥 scan_end，且编排补写 scan_end 必须**幂等**（先 `_has_scan_end` 判定，见 §7.4）。
- **黑盒是白盒下游 exploitation-only 单向依赖**：读 `deliverables/whitebox/` 的 queue + `recon_deliverable.md`，无反向。

---

## 4. 物理布局（单目录三桶）

```
<workspaces_dir>/<ws>/scans/<repo>-<ts>/      ← 组合扫描：单一 scan 任务目录
  ├── session.json                              单 session（bb_phase 状态机 + 进度字段）
  ├── events.ndjson                             单事件流：预验证段 → 白盒段 → 黑盒段 → scan_end
  ├── scan-config.yaml                          认证快照（t0 dump；黑盒读它登录）
  └── deliverables/
      ├── whitebox/   ← 白盒产物（白盒 workflow 写，现有逻辑不改）
      ├── blackbox/   ← 黑盒产物（黑盒 workflow 写，接力时 event_file 指向本目录 → 自动落此）
      └── combined/   ← 融合报告（接力完成后 scan_manager 生成，新增桶）
```

**三种场景对比**：

| 场景 | 目录 | session | events | 桶 |
|---|---|---|---|---|
| 纯白盒（开关关） | `scans/<wb>/` | 现有白盒 session | 现有白盒 events | 仅 `whitebox/` |
| 纯黑盒入口（复用已有白盒） | `scans/<wb>~N/`（独立） | 独立黑盒 session | 独立黑盒 events | 仅 `blackbox/` |
| **组合扫描（开关开）** | `scans/<wb>/`（单目录） | 单 session + bb_phase | 单流三段 | `whitebox/` + `blackbox/` + `combined/` |

> 预验证段不写主 events（用独立 events，见 §7.2），故主 events 流实际是「白盒段 → 黑盒段 → scan_end」两段+收尾。

---

## 5. 核心机制：接力零改动黑盒 workflow + 复用提交方法

**关键事实**（已代码核查）：黑盒 workflow 的产物目录 = `event_file.parent`（`blackbox/workflows.py`，web 路径分支）：

```python
if input.event_file:
    workspace_path = str(Path(input.event_file).parent)   # ← scan_dir = event_file.parent
elif input.workspace_name:
    workspace_path = str(ws_root / input.workspace_name)
...
deliverables = Path(workspace_path) / input.deliverables_subdir   # DEFAULT_DELIVERABLES_SUBDIR="deliverables"
```

黑盒读白盒 queue 的根 = `repo_path / deliverables_subdir`，queue 在其 `whitebox/` 子目录（`WHITEBOX_SUBDIR="whitebox"`，两端常量一致）。

**接力时 scan_manager 给黑盒传**：
- `event_file` = 白盒的 `<wb_scan_dir>/events.ndjson`
- `repo_path` = 白盒 scan_dir（`str(<wb_scan_dir>)`）

**后果（全部天然成立，黑盒 workflow 不改一行）**：
- 黑盒 `workspace_path` = `Path(白盒 event_file).parent` = 白盒 scan_dir
- 黑盒产物落 `白盒 scan_dir/deliverables/blackbox/`
- 黑盒读白盒 queue 走 `repo_path/deliverables/whitebox/`（同目录，天然成立）
- 三桶 `deliverables/{whitebox,blackbox,combined}/` 共存于白盒 scan_dir，互不冲突

### 5.1 复用现有 `_submit_blackbox`（D-复用）

**不新写 `_submit_blackbox_chained`**（原草案的手写版漏传 `host_mappings`/`max_concurrent` 等字段，且重复造轮子）。现有 `_submit_blackbox`（`scan_manager.py`）已封装好 `BlackboxPipelineInput` + `start_workflow` + `_mark_submitted_at`。给它加一个 `workflow_id_suffix: str = ""` 参数，组合黑盒传 `"-bb"`、续跑传 `"-bb-rerun-N"`（见 §11），其余字段（含 `host_mappings`，最新 HOST 档案功能）原样透传，零功能遗漏。

### 5.2 关联健壮性（白盒→黑盒）

关联是**纯路径耦合**（黑盒靠 `input.repo_path` 找白盒产物，无显式 `whitebox_scan_id` 键）。当前设计下够健壮：
- 路径链路自洽（`deliverables_subdir`/`WHITEBOX_SUBDIR` 两端常量一致）。
- 空/缺 queue **不 crash**：`ExploitationChecker.validate_queue` 返回 `valid=False`（graceful），不 raise。
- 接力前 `_whitebox_deliverables_ready` 预检兜底（白盒产物存在 + 至少一个非空 queue）。
- 固有脆弱：预检（编排层）与黑盒 activity 实际读 queue 之间有时间窗；`SUPERNOVA_DELIVERABLES_SUBDIR` 需白盒/黑盒 worker 配置一致（单容器部署天然满足）。

---

## 6. 数据模型

### 6.1 ScanRequest 扩展（`web/models.py`）

组合扫描 = `type="whitebox"` + `source`（repo）+ 黑盒接力参数。**复用现有黑盒认证字段**，不新增字段，仅扩展 validator 语义：

| 字段 | 组合扫描用法 | 来源 |
|---|---|---|
| `type` | `"whitebox"` | 现有 |
| `source` | repo（必填） | 现有白盒必填 |
| `url` | 黑盒目标 URL（组合模式必填） | 现有（白盒原本仅记录） |
| `authentication` / `auth_accounts` | inline 登录 | 现有黑盒字段 |
| `auth_profile_id` / `auth_credential_id` / `auth_credential_ids` | 档案登录（三模式） | 现有黑盒字段 |
| `host_profile_id` / `host_url` | HOST 解析（二选一，与黑盒同款互斥；disabled 不发） | 现有黑盒字段 |

**新增 model_validator `_whitebox_combined_optional`**：
- `type=="whitebox"` 且带 `url` → 组合扫描模式（`combined=True`）。
- 组合模式：认证字段走与黑盒相同的互斥校验（复用 `_auth_profile_xor_inline` 的逻辑，提取为共享校验函数）。
- 组合模式：HOST 字段走与黑盒相同的互斥校验（`_host_profile_xor_url` 条件从 `blackbox` 扩到 `blackbox ‖ (whitebox+url)`；host_profile_id 与 host_url 二选一，都不填=合法直连目标）。
- 非组合白盒（无 url）：禁认证字段（防误传），行为同今天。

**不改 `_blackbox_requires_reuse`**（纯黑盒入口仍强制 `reuse_whitebox_scan_id`；组合扫描走白盒入口 + 接力）。

### 6.2 session.json 字段（组合扫描）

在现有白盒 session flat dict 基础上，组合扫描额外写入：

| 字段 | 写入时机 / 者 | 取值 |
|---|---|---|
| `combined` | scan_manager 提交时 | `True`（组合标记；纯白盒无或 `False`） |
| `bb_phase` | scan_manager 接力/预验证/完成时 | `precheck` → `pending` → `running` → `completed` / `failed` / `skipped`（可从 `failed` 回退，见 §11） |
| `bb_url` | 提交时 | 黑盒目标 URL；保留供重跑预填 |
| `bb_auth_ref` | 提交时 | **仅 `profile_id`（非敏感引用）**；inline 模式存 `None`。认证明文不进 session.json（见 §6.3） |
| `bb_reason` | 失败/跳过时 | 原因（如 `auth_failed` / `白盒无可利用产物`） |
| `bb_rerun_attempts` | 每次黑盒续跑递增 | int，算 `-bb-rerun-N` workflow_id（见 §11） |
| `expected_agents` | 提交时（白盒部分）/ 黑盒 submit 时（补黑盒部分） | `{"whitebox": N, "blackbox": M}`，进度分母（见 §9） |
| `completed_agents` | 两 workflow 各自 `mark_agent_completed` merge | 白盒 agents + 黑盒 agents（AgentName 不冲突，累积） |
| `status` | 白盒 finalize 组合模式**不写终态**（保持 running）；黑盒 finalize 写 `completed`（唯一终态写者）；编排/预验证失败写 `failed`/`skipped` | 白盒 finalize 组合分支跳过终态写入（纯白盒仍写 completed） |

**三方写协调（铁律）**：白盒 finalize、黑盒 finalize、scan_manager 编排/预验证都 `update_session`。严格界定——白盒 finalize 只写白盒阶段产物指针 + `completed_agents`；编排只写 `bb_phase`/`bb_reason`/`status`；黑盒 finalize 写黑盒产物指针 + 终态 `status`。`status` 的唯一终态写者是黑盒 finalize（组合完成）或 scan_manager（预验证/接力失败/跳过）。

### 6.3 认证快照 = scan-config.yaml（D2）

**认证明文不进 session.json**。`bb_auth_ref` 只存 `profile_id`（非敏感）；真正的认证快照 = `scan_dir/scan-config.yaml`（黑盒本来就要读它登录）。

理由：`session.json` 是 web 列表/详情接口**经常 load、还会透传给前端**的状态文件（公告栏）；`scan-config.yaml` 是只有黑盒 `parse_config` 读的配置文件（保险柜），且有现成密码卫生清理机制。明文该放保险柜。崩溃恢复读 scan-config.yaml 重建，与现有纯黑盒 resume（`scan_manager.py` `cfg = scan_dir / "scan-config.yaml"`）完全同源。

`scan-config.yaml` 在 **t0 提交时就 dump 好**（白盒开跑前就在盘上），供 t0 预验证 + t2 黑盒接力 + 续跑共用。明文暴露窗口与纯黑盒入口一致（纯黑盒也是提交即 dump）。

---

## 7. 接力时序（方案 D：PhaseEvent + 独立编排 + _watch 纯 tail）

### 7.1 设计原则（复用现有 PhaseEvent，D1）

项目**已有** phase 事件机制：`PhaseEvent`（`events.py`）+ `AuditSession.log_phase_complete(phase)`（`session.py`）+ `WorkflowLogger.log_phase(phase,"complete")`。黑盒 `log_phase_complete_activity` 已在用。`StructuredEventRenderer._serialize` 通用路径对所有事件写 `"type": type(event).__name__`，故 `PhaseEvent` 写出 `{type:"PhaseEvent", phase, event:"complete", ...}`——`type != "scan_end"`，`_has_scan_end` 天然忽略，`_watch` 不退出。

因此白盒 finalize 组合模式想「不写 scan_end、写个阶段结束事件」，**直接调现有 `session.log_phase_complete("whitebox")`** 即可。**不新增 `PhaseEndEvent`、不覆盖现有方法签名**（避免砸掉黑盒 phase 日志）。

- **`scan_end` 语义不变量**：全场景 events 文件只有一个 `scan_end` = 真结束。组合模式白盒用 `PhaseEvent` 表达阶段边界，绝不剥 scan_end。
- **职责分离**：`_watch` 回归纯 tail（认 `scan_end` 退出）；接力编排抽到独立 task（`_combined_orchestrator`）；白盒 `finalize_summary` 组合模式写 `PhaseEvent`。
- **白盒 workflow 最小改动**：`PipelineInput` 加 `combined: bool = False`；`finalize_summary` 组合分支调 `log_phase_complete("whitebox")` 而非 `log_workflow_complete`；纯白盒不变（写 `scan_end`）。黑盒 workflow **零改动**（写 `scan_end` 作最终）。

### 7.2 时序

```
1. 用户白盒扫描页打开开关，填 repo + url + 认证，提交
   POST /api/scan { type:"whitebox", source, url, authentication/auth_profile_id }
2. scan_manager.start(whitebox + url):
   - create_scan → scans/<repo>-<ts>/（单目录）
   - dump scan-config.yaml 到 scan_dir（D2 认证快照，支持 inline + profile）
   -【t0 预验证 D4】起 AuthValidationWorkflow（config_path=scan-config.yaml）→ await 完成
       ├ pass  → 继续
       └ fail  → fail-fast：session bb_phase=failed/bb_reason=auth_failed，不 submit 白盒，返回错误
   - session 写 combined=true, bb_phase=pending, bb_url, bb_auth_ref={profile_id 或 None},
     expected_agents={whitebox: N}（算 expected，见 §9）
   - _submit_whitebox(combined=True) → 白盒 workflow @ supernova-wb-web，workflow_id={ws}-{scan_id}
   - 起 _watch(scan_key, event_file, scan_dir)            ← 纯 tail
   - 起 _combined_orchestrator(scan_key, wb_handle, ...)  ← 接力编排 task
3. 白盒 workflow 跑，产物落 deliverables/whitebox/，events 追加 events.ndjson
4. 白盒 finalize_summary（combined=True）写 PhaseEvent(phase=whitebox, complete)（非 scan_end）
5. _combined_orchestrator: await wb_handle.result()  → 白盒完成确认
     → _run_blackbox_phase(scan_dir, ws, scan_id, auth)（公共段，见 §7.3）
6. _run_blackbox_phase:
   - 预检白盒产物（recon_deliverable.md + 至少一个非空 queue）
       不全 → bb_phase=skipped + 写最终 scan_end（幂等）+ 标注 → 结束
   - 产物 OK → 复用 _submit_blackbox（workflow_id_suffix="-bb"）：
       event_file = 白盒 events.ndjson；repo_path = 白盒 scan_dir；config_path = scan-config.yaml
       按 queue 发现的 vuln 类补 expected_agents.blackbox
   - session: bb_phase=running
   - 黑盒 workflow 跑 @ supernova-bb-web，产物落 deliverables/blackbox/，events 追加同一 events.ndjson
   - 黑盒 finalize_summary 写 scan_end（全场景唯一最终 scan_end）
   - await bb_handle.result() → 生成融合报告 → deliverables/combined/combined_report.md（见 §10）
   - session: bb_phase=completed
7. _watch（纯 tail）：检测到 scan_end → 退出 + finally 清理（现有逻辑不变）
```

### 7.3 `_run_blackbox_phase`（公共段，编排 + 续跑共用）

抽公共段，`_combined_orchestrator`（await 白盒后）和续跑入口（§11，跳过白盒）都调它：

```python
async def _run_blackbox_phase(self, scan_dir, ws, scan_id, auth_ref):
    """预检白盒产物 → 提交黑盒（复用 _submit_blackbox）→ 等黑盒 → 融合报告。
    幂等收尾：成功路径黑盒 finalize 已写 scan_end，_ensure_scan_end 不重复写。"""
    if not self._whitebox_deliverables_ready(scan_dir):
        await self._mark_bb(scan_dir, "skipped", "白盒无可利用产物"); return
    suffix = "-bb-rerun-{N}".format(N=...) if 续跑 else "-bb"
    bb_handle = await self._submit_blackbox(
        repo_path=str(scan_dir), ws=ws, scan_id=scan_id, scan_dir=scan_dir,
        event_file=scan_dir / "events.ndjson", web_url=bb_url,
        config_path=str(scan_dir / "scan-config.yaml"),
        host_mappings=..., workflow_id_suffix=suffix)
    await self._mark_bb(scan_dir, "running")
    await bb_handle.result()
    await self._generate_combined_report(scan_dir)
    await self._mark_bb(scan_dir, "completed")
```

### 7.4 `_watch` + 幂等 scan_end（修原草案双重 scan_end bug）

`_watch` 回归纯 tail，`_has_scan_end` 只认 `type=="scan_end"`，`PhaseEvent` 天然被忽略 → `_watch` 续 tail 直到黑盒 `scan_end`。**`_watch` 主体零代码改动**。

**关键契约（修原草案 bug）**：成功路径黑盒 finalize 已写 `scan_end`，编排的收尾**绝不能再无条件写**。统一用幂等 helper：

```python
async def _ensure_scan_end(self, scan_dir):
    """编排收尾契约：仅在 events 无 scan_end 时补写（幂等）。
    成功路径黑盒已写 → no-op；异常/跳过/提交失败 → 补写 scan_end 防 _watch 永久 tail。"""
    if self._has_scan_end(scan_dir / "events.ndjson"):
        return
    await self._write_scan_end(...)  # 现有 _write_scan_end
```

`_combined_orchestrator` 的 try/except/finally 都调 `_ensure_scan_end`（幂等），不再裸调 `_write_final_scan_end`。`_watch` 的 finally 兜底（现有「worker 未写 scan_end」补写）仍作最后一道防线。

### 7.5 崩溃恢复（`orphan_reconciler` 扩展）

接力是 scan_manager 进程内（非 Temporal durable）。扩展现有 `orphan_reconciler` 兜底进程重启，按 `bb_phase` 分支：
- `bb_phase=precheck` 且预验证 workflow 已完成 → 据结果走 pass/fail 分支。
- `bb_phase=pending` 且白盒 workflow 已 completed → 走 `_run_blackbox_phase`（补黑盒）。
- `bb_phase=pending` 且白盒仍 running → 重建 handle 续跑。
- `bb_phase=running` 且黑盒 workflow（`{ws}-{scan_id}-bb` 或 `-bb-rerun-N`）已 completed → 补生成融合报告。
- `bb_phase=running` 且黑盒仍 running → 重建 handle 续跑。
- 任意 `bb_phase` + events 无 scan_end → `_ensure_scan_end` 补写。

### 7.6 workflow_id 命名

- 白盒：`{ws}-{scan_id}`（现有 `_resolve_workflow_id`，不改）
- 组合黑盒（首跑）：`{ws}-{scan_id}-bb`（`_submit_blackbox` 的 `workflow_id_suffix="-bb"`）
- 续跑黑盒：`{ws}-{scan_id}-bb-rerun-N`（N=`bb_rerun_attempts`，见 §11）
- 预验证：`{ws}-{scan_id}-authcheck`（独立 workflow，不与扫描 workflow 混 namespace）

---

## 8. 入口（前端）

### 8.1 白盒扫描页开关

- `ScanNewPage.tsx` 白盒 Tab 内加「同时发起黑盒扫描」开关。
- **关闭**（默认）：白盒表单 = 今天样子，`buildBody` 不带 url/认证/HOST -> 纯白盒。
- **打开**：展开黑盒信息区--目标 URL 输入 + 认证模块 + HOST 解析模块。三者均为直接复用现有黑盒表单的共享组件：认证抽成 `<AuthFields>`（原 `RightAuthCore` / `BottomInlineBlock` / `BottomProfileBlock` 的受控封装）、HOST 抽成 `<HostFields>`（原黑盒 HOST section 的受控封装：segmented 切换 profile/url + `HostProfilePicker` 或 URL 输入）。两者独立、非互斥（auth 管登录态，HOST 管 DNS 覆盖），任一 disabled 时不发对应字段。
- 校验：打开时 url 必填 + 认证必填（与黑盒同规则）；HOST 与黑盒同款互斥校验（host_profile_id 与 host_url 二选一，见 §6.1）。

### 8.2 buildBody + 提交流程

打开开关时，白盒请求额外带 `url` + 认证字段 + HOST 字段，后端 `_whitebox_combined_optional` validator 识别为组合模式（`_host_profile_xor_url` 互斥校验同步扩到组合模式，见 §6.1）。

提交后前端进入「预验证中」态（`bb_phase=precheck`）——同步等待 t0 预验证结果（几秒~几十秒）：
- 预验证 pass → 进入白盒 running。
- 预验证 fail → 弹错误（认证失败），不进入扫描。

### 8.3 correlation 死 Tab

前端 Tab 的 `correlation` 与本设计无关，保持现状不动。

---

## 9. 进度（分层：收起粗略% / 展开步级详细）

### 9.1 数据基础（已代码核查）

- `completed_agents` **已在 session.json 顶层**（`session.py`，`mark_agent_completed` 增量更新），list_scans 内部读得到（as_dict 加字段透传即可）。
- `AgentName` 是**可枚举 Enum**（`agents.py`：PRE_RECON/RECON/各 vuln agent/VALIDATE_AUTH/ENDPOINT_VERIFY…），每个有 prerequisites 拓扑——**预期总数可算**。
- events.ndjson 的 `PhaseEvent` 声明每个 phase 的 steps（`log_phase_start(phase, steps, ...)`），`StepEvent` 记每步 complete——**步级进度可推**。

### 9.2 收起态（列表所有卡片，轻）

一个 `progress_pct`（0-100）+ 阶段名 + 进度条。后端预算 `progress_pct`，list_scans as_dict 透传。分母 = `expected_agents`（提交时算，见 §6.2），分子 = `completed_agents`。

```
repo-C  联合  62%  ████████████░░░░░░  白盒中
repo-D  联合  88%  ████████████████░░  黑盒中
```

组合扫描按三阶段加权（预验 5% + 白盒 50% + 黑盒 45%）：
```
precheck        → 0–5%
白盒中(pending) → 5 + 50 × (wb_completed / wb_expected)
黑盒中(running) → 55 + 45 × (bb_completed / bb_expected)
completed       → 100%
```
纯白盒/纯黑盒：`completed / expected × 100`。

**收起态精度门槛低**（用户不展开不细看），`_compute_expected_agents(req)` 够准即可，不用完美对齐双轨/authz 口径。

### 9.3 展开态（单卡片按需，重，读 events）

点开某张卡片，按需读该 scan 的 events.ndjson，推**步级**详细进度：
- 分段时间线（白盒段 / 黑盒段，靠 `bb_phase` 切段，**无 `phase_boundary` 事件**——D3）。
- 每段步进度：`PhaseEvent.declared steps` vs `StepEvent complete` 计数 → 步级%（比 agent 均分平滑，recon 内部多步逐步推进，不卡）。
- 当前 agent / 当前步 / exploit N/M 等 vuln 类细分。

```
repo-C  联合  62%  ████████████░░░░░░
● 白盒  ─ pre-recon ✓ ─ recon 4/6 步 ─ injection ⋯
○ 黑盒  (待接力)
当前：recon · 第 4 步「分析路由表」
```

展开态**不依赖 `expected_agents`**（靠 declared steps），精度高、平滑。

### 9.4 切段靠 bb_phase（D3，砍 phase_boundary）

前端阶段切分**只读 session.bb_phase**（precheck/pending/running/completed/failed），**不依赖 events 里的边界事件**。故砍掉原草案的 `_emit_phase_boundary`（少一个 scan_manager 裸 aiofiles 并发写源、少一套事件名）。events 里的 `PhaseEvent` 仅作展开态时间线辅助。

### 9.5 `_compute_expected_agents` 的 4 个口径（实现注意）

真实百分比分母须与 `completed_agents` 同口径：
1. **双轨**：白盒 inj/xss/ssrf 是 GitNexus+LLM 双轨（`SUPERNOVA_LLM_TRACK_ENABLED`，默认开），关轨时 expected 减半。
2. **黑盒 expected 动态**：黑盒只 exploit 白盒发现的类，`expected_agents.blackbox` 在黑盒 submit 时补写（白盒完成后 queue 已知）。
3. **百分比是 agent 数比例，非时间**：recon 可能跑很久但只算 1/N——收起态轻微卡可接受，展开态用步级化解。
4. **authz/auth 特殊**：authz 有 GitNexus 深度 agent + LLM 轨；auth 纯 LLM 轨，expected 按各类实际轨数算。

收起态精度门槛低，这 4 点做到「够准」即可；展开态用步级绕开。

---

## 10. 报告三视图（vuln_class 交叉融合）

### 10.1 三视图

报告 tab（`ReportTab.tsx`）在 `combined==true` 时提供三个子 tab：

| 子 tab | 来源 | 状态 |
|---|---|---|
| 白盒报告 | `deliverables/whitebox/`（现有 `assemble_report` 产物） | 已有 |
| 黑盒报告 | `deliverables/blackbox/`（现有黑盒 `assemble_report` 产物） | 已有 |
| **融合报告** | `deliverables/combined/combined_report.md`（**新增**） | 新增 |

产物 tab（`DeliverablesTab.tsx`）同理合并展示三个桶。

### 10.2 融合报告结构（vuln_class 交叉）

```
# 组合扫描融合报告

## 组合摘要
| 漏洞类 | 白盒发现 | 黑盒验证 |
|---|---|---|
| injection | 3 | 2 |
| xss | 2 | 1 |
...

## 按漏洞类详述
### injection
#### 白盒视角（代码证据）
  - <白盒 queue 项：sink/数据流/位置>
#### 黑盒视角（利用验证）
  - <黑盒 finding：endpoint/PoC/verdict>
...（每个 vuln_class 一节，白盒+黑盒并列）
```

- **对齐粒度**：vuln_class（黑盒本就按 vuln_class 读白盒 queue）。不做单个漏洞 1:1。
- **生成**：新增 `combined_report_renderer`（复用 `FindingsRenderer`/`ReportAssembler` 的读 queue 逻辑），由 `_run_blackbox_phase` 在黑盒完成后调用，产 `deliverables/combined/combined_report.md`。
- **归属**：`combined/` 是新桶，`DeliverablesReader._infer_track`（`deliverables_reader.py`）扩展识别 `combined` track；`resolve_track_deliverable`（`paths.py`）支持 `track="combined"`；`COMBINED_SUBDIR` 常量。

---

## 11. 失败 / 续跑 / resume / cancel

### 11.1 t0 预验证失败（fail-fast）

预验证 fail → session `bb_phase=failed`/`bb_reason=auth_failed`，**不 submit 白盒**，返回错误给前端。用户改认证重提即可（未产生白盒开销）。

### 11.2 接力失败语义

`_combined_orchestrator` 的 try/except 覆盖所有接力异常，`_ensure_scan_end`（幂等）保证写最终 scan_end：

| 情形 | 行为 |
|---|---|
| t0 预验证失败 | fail-fast，不跑白盒（§11.1） |
| 白盒失败/超时/crash | `wb_handle.result()` 抛 → 编排 except：`bb_phase=failed` + 标因 + `_ensure_scan_end`；UI「白盒失败，黑盒未启动」 |
| 白盒成功但无产物（空 queue / 缺 recon） | 预检不全 → `bb_phase=skipped` + 标注 + `_ensure_scan_end` |
| 黑盒失败/超时/crash（含 t2 登录失败） | `bb_handle.result()` 抛 → 编排 except：`bb_phase=failed` + 标因 + `_ensure_scan_end`；白盒报告保留；**可续跑**（§11.3） |
| 用户取消（白盒阶段） | cancel `{ws}-{scan_id}` → 编排 except 收尾 |
| 用户取消（黑盒阶段） | cancel `{ws}-{scan_id}-bb`（或 `-bb-rerun-N`）→ 编排 except 收尾；白盒结果保留 |

> 注：t0 预验证通过**不保证** t2 黑盒登录成功（账号可能在白盒跑的 1-2h 里失效）——预验证的价值是「早失败」，t2 失败靠续跑（§11.3）补救。黑盒每次开跑都用 scan-config.yaml 重新登录（`run_blackbox_auth_validation`），不复用旧 session，故 session 过期不是问题；真正风险是账号本身失效。

### 11.3 黑盒续跑（D5，可多次换认证）

**前提**：`bb_phase=failed`（黑盒阶段失败）+ 白盒产物完好（`_whitebox_deliverables_ready` 为真）。白盒本身 failed 不走这条（那是 §11.4 resume）。

**入口**：失败组合 scan 详情页加「续扫黑盒」→ 用户**可选**重新提交认证（换 profile / 改 inline；不换就用原 scan-config）。后端：

```
1. （可选）重 dump scan-config.yaml（新认证覆盖旧的）
2. 预验证新认证（复用 AuthValidationWorkflow，pass 才继续——被坑过一次先验）
3. session.bb_rerun_attempts += 1
4. 起新黑盒 workflow：workflow_id = {ws}-{scan_id}-bb-rerun-{N}（_submit_blackbox suffix）
5. 调 _run_blackbox_phase（跳过白盒，白盒产物已就绪）
6. bb_phase: failed → precheck → running → completed/failed（可多次，N 递增）
```

**bb_phase 状态机可回退**：`failed →（续跑）→ precheck → running → completed/failed`。与「failed 是终态、需重扫」的旧语义不同。归档旧黑盒 evidence 的策略（覆盖 / `blackbox.archive-N/`）在 plan 阶段定。

**API**：新增 `POST /{ws}/scans/{id}/combined/rerun-blackbox`，body 带新认证（可选）。

### 11.4 resume（中断续跑，不换参数）

扩展 `scan_manager.resume`，读 session `combined` + `bb_phase`（仅 interrupted/crashed 可 resume；failed 走 §11.3 续跑）：
- 非组合：现有逻辑（按 scan_type resume 对应 workflow）。
- 组合 + `bb_phase=precheck`：重做预验证或 resume 预验证 workflow。
- 组合 + `bb_phase=pending`：resume 白盒 `{ws}-{scan_id}`，完成后重启 `_combined_orchestrator` 续接力。
- 组合 + `bb_phase=running`：resume 黑盒 workflow（`{ws}-{scan_id}-bb` 或 `-bb-rerun-N`），附一个仅「等黑盒完成 → 生成融合报告」的编排 task（接力已发生，不重复 submit）。
- resume 前清障（`_strip_trailing_scan_end`，无 scan_end 则 no-op）。

### 11.5 cancel

扩展 `scan_manager.cancel`：组合扫描按当前 `bb_phase` + `bb_rerun_attempts` cancel 对应 workflow_id（白盒 / 黑盒首跑 / 黑盒续跑 N）；单 scan_id 一条记录，cancel 一次取消当前阶段。

---

## 12. 测试策略

| 层 | 测试 | 守卫 |
|---|---|---|
| **validator** | `_whitebox_combined_optional`：白盒+url=组合；白盒无 url=纯白盒禁认证；组合模式认证互斥 | models 单测 |
| **t0 预验证** | mock AuthValidationWorkflow pass/fail → pass 才 submit 白盒；fail fail-fast 不 submit | scan_manager 单测 |
| **编排 task（接力）** | mock `wb_handle.result()` 完成 + 产物就绪 → 断言 `_submit_blackbox` 被调（带 `-bb` suffix）、event_file/repo_path 指向白盒 scan_dir、bb_phase=running | scan_manager 单测 |
| **幂等 scan_end** | 成功路径（黑盒已写 scan_end）→ `_ensure_scan_end` no-op（**不写第二条**）；异常/跳过 → 补写 | scan_manager 单测，**核心守卫** |
| **_watch 纯 tail** | PhaseEvent 不触发退出（`_has_scan_end` 只认 scan_end）；scan_end 退出；纯白盒/纯黑盒零回归 | scan_manager 单测 |
| **白盒 finalize PhaseEvent** | `combined=True` 调 `log_phase_complete`（写 PhaseEvent，非 scan_end）；`combined=False` 写 scan_end（纯白盒零回归） | whitebox finalize 单测 |
| **黑盒续跑（D5）** | bb_phase=failed + 白盒产物在 → 换认证 → 新 workflow `-bb-rerun-1`；多次续跑 N 递增 | scan_manager 单测 |
| **进度计算** | 给定 completed/expected → progress_pct 正确（三阶段加权）；expected 双轨/黑盒动态口径 | 单测 |
| **融合报告** | 给定白盒+黑盒 queue 样例 → vuln_class 交叉输出 + 摘要计数 | combined_report_renderer 单测 |
| **后端透传（G1）** | list_scans as_dict + _scan_detail 含 combined/bb_phase/bb_reason/progress_pct | api 单测 |
| **前端** | 开关关=纯白盒 body；开关开=带 url+认证 + 预验证态；列表卡片收起%/展开步级；详情两段时间线；报告三子 tab | vitest 组件测试 |
| **端到端冒烟** | 开关打开跑真实小仓 → 预验✓→白盒✓→黑盒✓→三报告齐；黑盒认证失败→续跑换认证→黑盒✓ | 真机/容器冒烟 |

**预存陷阱规避**：
- 前端命令须 `cd packages/web/frontend`（cwd 不持久）；用 `./node_modules/.bin/vitest|tsc|vite`，别用 `pnpm test`。
- 改 web/worker src 须 rebuild `supernova-worker` 镜像（`uv sync` 须 `--all-packages`）。
- pytest 只跑改动相关子集，勿广跑全套（会 hang）。
- 路径基准：`/root/shannon-py`（Linux）。

---

## 13. 风险与回退

| 风险 | 影响 | 缓解 |
|---|---|---|
| 白盒 `finalize_summary` 组合分支破坏纯白盒 scan_end | 高（白盒 live 收尾全瘫） | combined 分支严格隔离 + 纯白盒写 scan_end 的回归测试守卫 |
| 编排成功路径写第二条 scan_end（原草案 bug） | 高（违反 scan_end 不变量 + _watch 行为异常） | `_ensure_scan_end` 幂等（先 `_has_scan_end`）；核心单测守卫「成功路径不重复写」 |
| session 三方写冲突（status 抖动） | 中 | 严格界定写者（§6.2 铁律）；终态 status 唯一写者 |
| 黑盒产物落点假设失效 | 中（单目录破裂） | spec 锁定 `scan_dir=event_file.parent` 不变量；改黑盒 workflow 路径推导须同步本设计 |
| 黑盒 expected 动态导致进度%不准 | 低 | 收起态精度门槛低（够准即可）；展开态用步级绕开 |
| t0 预验证 + t2 登录重复登录风控 | 低 | 两次登录隔白盒全程（1-2h），风控窗口一般分钟级；t2 失败可续跑 |
| 续跑多次产生多份黑盒 evidence | 低 | 归档策略（覆盖 / archive-N）plan 阶段定 |
| 接力是进程内（非 Temporal durable） | 中 | `orphan_reconciler` 扩展兜底（§7.5）；演进出口：升级到 Temporal 父 workflow 把接力 durable 化（本设计机制可平滑迁移） |

**回退**：组合扫描是白盒入口的**附加模式**（开关默认关）。最坏情况禁用开关（前端隐藏 / env gate），纯白盒+纯黑盒入口完全不受影响。

---

## 14. 关键文件索引

| 主题 | 路径 |
|---|---|
| 白盒 workflow 入口 | `packages/whitebox/src/supernova_whitebox/pipeline/workflows.py` |
| 白盒 finalize_summary（组合模式写 PhaseEvent） | `packages/whitebox/src/supernova_whitebox/pipeline/activities.py` |
| 黑盒 workflow scan_dir 推导（`event_file.parent`） | `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py` |
| AuthValidationWorkflow（t0 预验证 + 续跑预验证复用） | `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py` |
| ScanManager.start（决策分发） | `packages/web/src/supernova_web/components/scan_manager.py` |
| _submit_whitebox / _submit_blackbox（加 workflow_id_suffix） | `scan_manager.py` |
| _resolve_blackbox_inputs（认证 dump scan-config） | `scan_manager.py` |
| _watch（纯 tail） / _has_scan_end / _strip_trailing_scan_end / _write_scan_end | `scan_manager.py` |
| _combined_orchestrator + _run_blackbox_phase + _ensure_scan_end（新增） | `scan_manager.py` |
| orphan_reconciler（扩展组合恢复） | `packages/web/src/supernova_web/components/orphan_reconciler.py` |
| resume / cancel（按 bb_phase + bb_rerun_attempts 分阶段） | `scan_manager.py` |
| ScanRequest + validators | `packages/web/src/supernova_web/models.py` |
| list_scans as_dict + _scan_detail（透传 combined/bb_phase/progress_pct） | `packages/web/src/supernova_web/components/scan_store.py` / `packages/web/src/supernova_web/api/scans.py` |
| PhaseEvent / log_phase_complete（复用，不改） | `packages/core/src/supernova_core/display/events.py` / `audit/session.py` |
| StructuredEventRenderer（_serialize 通用路径，type=类名） | `packages/core/src/supernova_core/display/structured_event_renderer.py` |
| AgentName 枚举（算 expected_agents） | `packages/core/src/supernova_core/models/agents.py` |
| completed_agents（session 顶层，已有） | `packages/core/src/supernova_core/session.py` |
| WHITEBOX/BLACKBOX_SUBDIR + whitebox_dir/blackbox_dir + resolve_track_deliverable | `packages/core/src/supernova_core/utils/paths.py` |
| 前端扫描页 / buildBody / 表单字段 | `packages/web/frontend/src/pages/ScanNewPage.tsx` / `components/ScanFormFields.tsx` |
| 前端列表卡片（收起%/展开步级） | `packages/web/frontend/src/routes/WorkspaceDetail/ScanList.tsx` |
| 详情页 / 报告 tab / 产物 tab | `packages/web/frontend/src/routes/WorkspaceDetail/ScanDetail.tsx` / `ReportTab.tsx` / `DeliverablesTab.tsx` |
| DeliverablesReader（track 推断，扩展 combined） | `packages/web/src/supernova_web/components/deliverables_reader.py` |
| combined_report_renderer（新增） | `packages/web/src/supernova_web/components/combined_report_renderer.py` |

---

## 15. 后续（writing-plans 接管）

本 spec 锁定架构与数据流。实现拆任务交给 writing-plans，预期任务分组：
1. **ScanRequest validator + session 字段 + 后端透传（G1）**：validator 扩展；session 加 `combined`/`bb_phase`/`bb_url`/`bb_auth_ref`/`bb_reason`/`bb_rerun_attempts`/`expected_agents`；`_scan_detail` + list_scans as_dict 透传 `combined`/`bb_phase`/`bb_reason`/`progress_pct`；`_compute_expected_agents`。
2. **白盒 finalize PhaseEvent**：`PipelineInput.combined` + `finalize_summary` 组合分支调现有 `log_phase_complete`（纯白盒写 scan_end 回归守卫）。**不新增事件类型**。
3. **t0 预验证（D4）**：start 插 dump scan-config + AuthValidationWorkflow + await + fail-fast。
4. **scan_manager 接力编排**：`_combined_orchestrator` + `_run_blackbox_phase`（复用 `_submit_blackbox` + suffix）+ `_ensure_scan_end`（幂等）+ `orphan_reconciler` 扩展。
5. **黑盒续跑（D5）**：`rerun-blackbox` API + `_run_blackbox_phase` 续跑入口 + `bb_rerun_attempts`。
6. **resume/cancel**：`bb_phase` + `bb_rerun_attempts` 分阶段。
7. **进度（前端）**：列表卡片收起 `progress_pct` / 展开步级（读 events PhaseEvent+StepEvent）。
8. **融合报告**：`combined_report_renderer` + `combined` track 识别。
9. **前端入口/详情/报告**：开关 + 共享认证 + 预验证态；详情两段时间线（bb_phase 切段）；报告三子 tab + 产物三桶。
10. **端到端冒烟**：含黑盒认证失败→续跑换认证路径。
