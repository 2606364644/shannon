# 白盒+黑盒一键组合扫描（Combined Scan）设计

> 日期：2026-08-12
> 状态：设计稿（brainstorming 产出，待 writing-plans 拆任务）
> 主题：消除白盒/黑盒在 WEB 层的割裂——白盒扫描页一键发起「白盒→黑盒自动接力」组合扫描，单目录三桶，三视图报告（白盒/黑盒/融合）。

---

## 1. 背景与动机

当前白盒与黑盒在 WEB 层是「两类独立扫描 + 黑盒挂在某个白盒之下」的割裂模型：

- **发起割裂**：扫描页一个 Tab 三态切换（`ScanNewPage.tsx:15`），白盒/黑盒是两套完全不同的表单（`ScanFormFields.tsx:721` 白盒 vs `:736-881` 黑盒）。黑盒被 Pydantic 钉死成「必须复用某个已完成白盒」（`models.py:53-69`），用户要先提白盒、等完成、再回来填黑盒选那个白盒——两次提交、两次等待。
- **进度割裂**：白盒、黑盒是两个独立 Temporal workflow、两条独立事件流、两个 scan_id（白盒 `<repo>-<ts>`、黑盒 `<wb>~<N>`），列表里是两条独立记录。
- **结果割裂**：报告页/详情页 per-scan 渲染，不跨 track 合并（`ReportTab.tsx`、`DeliverablesTab.tsx`）。

仓库已有半成品 `packages/combined/`（`orchestrator.py:20` `run_combined_scan`，顺序 CLI），但**只接 CLI、未接 WEB、自起 Worker、无统一 session**。

**用户诉求**：WEB 提供一个渠道，一键同时跑白盒+黑盒，且不丢失任何单独视角的报告。底层产物与分开扫完全一致，一并扫描额外提供融合视图。

---

## 2. 设计决策（brainstorming 已确认）

| 维度 | 决定 |
|---|---|
| **范围** | 端到端：一键发起 + 单页统一进度 + 融合报告（两者都要） |
| **入口** | 白盒入口升级：白盒扫描页加开关按钮，打开展开填黑盒信息(URL+认证)→同时扫；关闭=纯白盒。不新增 scan_type |
| **接力** | 技术上只能先白盒后黑盒（黑盒强依赖白盒产物），自动串行 |
| **进度** | 白盒详情页单页统一进度，单事件流两段渲染（背后单 scan_dir） |
| **报告** | 三视图：白盒报告 / 黑盒报告 / 融合报告（vuln_class 交叉 + 顶部摘要）。单个漏洞级 1:1 对齐不可行（黑盒产物不带白盒漏洞 id），最强为漏洞类级 |
| **接力机制** | 后端编排接力（scan_manager `_combined_orchestrator` await 白盒完成 → 自动 `_submit_blackbox`），关浏览器也能接力 |
| **物理形态** | 单目录三桶：组合扫描只建一个 `scans/<wb>/`，下含 `deliverables/{whitebox,blackbox,combined}/` + 单 session + 单 events |

---

## 3. 非目标与不变量

**非目标**：
- 不做单个漏洞级（vuln instance）白盒↔黑盒 1:1 对齐（黑盒 exploit 产物不带白盒漏洞 id，`grep vuln_id/chain_id` 零命中；最强为 vuln_class 级）。
- 不改纯白盒、纯黑盒独立入口的行为（零回归）。
- 不引入新 scan_type（不新增 `combined` 类型；组合扫描的 scan_type 仍是 `whitebox`，靠 session `combined=true` 标记区分）。

**不变量（铁律）**：
- 纯白盒（开关关）：行为与产物布局与今天完全一致，只有 `deliverables/whitebox/`。
- 纯黑盒入口（对已有白盒追加黑盒）：仍是独立 `<wb>~N/` scan_dir + 独立 session/events（现有行为不变）。
- 黑盒 workflow **零代码改动**：单目录靠接力时控制 `event_file` / `repo_path` 两个入参实现（见 §5）。
- 白盒 workflow **最小改动**（方案 D 权衡）：PipelineInput 加 `combined: bool`，`finalize_summary` 组合模式写 `phase_end` 而非 `scan_end`（纯白盒分支不变）。用 finalize 一个分支，换 `scan_end` 语义不变量保持 + `_watch` 单一职责（见 §7.1）。比「白盒零改动但 _watch 剥 scan_end」更稳。
- **`scan_end` 语义不变量**：全场景 events 文件只有一个 `scan_end` = 真结束；组合模式阶段边界用 `phase_end` 表达，**不剥 scan_end**。
- 黑盒仍是「白盒下游 exploitation-only」单向数据依赖：读 `deliverables/whitebox/` 的 queue + `recon_deliverable.md`，无反向。

---

## 4. 物理布局（单目录三桶）

```
<workspaces_dir>/<ws>/scans/<repo>-<ts>/      ← 组合扫描：单一 scan 任务目录
  ├── session.json                              单 session（bb_phase 状态机 + 整体 status）
  ├── events.ndjson                             单事件流：白盒段 → (剥 scan_end) → 黑盒段 → scan_end
  ├── scan-config.yaml                          黑盒认证配置（接力时 scan_manager 写，复用现有 _dump_auth_payload）
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
| **组合扫描（开关开）** | `scans/<wb>/`（单目录） | 单 session + bb_phase | 单流两段 | `whitebox/` + `blackbox/` + `combined/` |

---

## 5. 核心机制：接力零改动黑盒 workflow

**关键事实**（已验证）：黑盒 workflow 的产物目录 = `event_file.parent`（`blackbox/workflows.py:91-101`，web 路径分支）：

```python
# blackbox/workflows.py:97
if input.event_file:
    workspace_path = str(Path(input.event_file).parent)   # ← scan_dir = event_file.parent
elif input.workspace_name:
    workspace_path = str(ws_root / input.workspace_name)
...
deliverables = Path(workspace_path) / input.deliverables_subdir   # :181
```

黑盒读白盒 queue 的根 = `repo_path / deliverables_subdir`（`blackbox/workflows.py:291`、`activities.py:291`），queue 在其 `whitebox/` 子目录。

**接力时 scan_manager 给黑盒传**：
- `event_file` = 白盒的 `<wb_scan_dir>/events.ndjson`
- `repo_path` = 白盒 scan_dir（`str(<wb_scan_dir>)`，同 `_resolve_blackbox_inputs:476` 的口径）

**后果（全部天然成立，黑盒 workflow 不改一行）**：
- 黑盒 `workspace_path` = `Path(白盒 event_file).parent` = 白盒 scan_dir
- 黑盒产物落 `白盒 scan_dir/deliverables/blackbox/`（经 `blackbox_dir()`，`paths.py:121`）
- 黑盒读白盒 queue 走 `repo_path/deliverables/whitebox/`（同目录，天然成立）
- 三桶 `deliverables/{whitebox,blackbox,combined}/` 共存于白盒 scan_dir，互不冲突

> 注：组合扫描的「黑盒」与纯黑盒入口的「黑盒」跑的是**同一个 `BlackboxScanWorkflow`**，区别仅在 scan_manager 提交时传的 `event_file` / `repo_path` / `workspace_name`。组合扫描黑盒的 `workspace_name` 仍传其自身 scan_id（= 白盒 scan_id，因单目录），仅作展示；产物落点由 `event_file.parent` 决定。

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

**新增 model_validator `_whitebox_combined_optional`**：
- `type=="whitebox"` 且带 `url` → 组合扫描模式（`combined=True`）。
- 组合模式：认证字段（authentication / auth_profile_id 等）走与黑盒相同的互斥校验（复用 `_auth_profile_xor_inline` 的逻辑，提取为共享校验函数）。
- 非组合白盒（无 url）：禁认证字段（防误传），行为同今天。

**不改 `_blackbox_requires_reuse`**（纯黑盒入口仍强制 `reuse_whitebox_scan_id`；组合扫描不走黑盒入口，走白盒入口 + 接力）。

### 6.2 session.json 字段（组合扫描）

在现有白盒 session（`session.py:34-45` flat dict）基础上，组合扫描额外写入：

| 字段 | 写入时机 / 者 | 取值 |
|---|---|---|
| `combined` | scan_manager 提交时 | `True`（组合扫描标记；纯白盒无此字段或 `False`） |
| `bb_phase` | scan_manager 接力/完成时 | `pending` → `running` → `completed` / `failed` / `skipped` |
| `bb_url` | 提交时（接力参数缓存） | 黑盒目标 URL；接力后保留供重跑预填 |
| `bb_auth_ref` | 提交时（接力参数缓存） | 认证模式快照（profile_id 或 inline hash）；接力展开后清除明文敏感参数 |
| `status` | 白盒 finalize 组合模式**不写终态**（保持 running）；黑盒 finalize 写 `completed`（唯一终态写者）；编排 task 接力失败/跳过时写 `failed`/`skipped` | 方案 D：白盒 finalize 组合分支跳过终态 status 写入（纯白盒仍写 completed）。plan 阶段确认 finalize 现有 status 写入点，组合分支据此跳过 |
| `completed_agents` | 两 workflow 各自 `mark_agent_completed` merge | 白盒 agents + 黑盒 agents（AgentName 不冲突，累积） |

> **三方写协调**（铁律）：白盒 `finalize_summary`、黑盒 `finalize_summary`、scan_manager 接力都 `update_session`。严格界定——白盒 finalize 只写白盒阶段产物指针与 `completed_agents`；接力只写 `bb_phase`/`status`；黑盒 finalize 写黑盒产物指针与终态 `status`。`status` 的唯一终态写者是黑盒 finalize（组合完成）或 scan_manager（接力失败/跳过）。

### 6.3 scan_id 命名

- 组合扫描**单 scan_id** = 白盒 scan_id `<repo>-<ts>`（`scan_store.py:_gen_scan_id` 白盒分支，不改）。
- 黑盒接力**复用同一 scan_id**（单目录），不再生成 `<wb>~<N>`。Temporal workflow_id 区分靠后缀（白盒 `{ws}-{scan_id}`、黑盒 `{ws}-{scan_id}-bb`，见 §7）。
- 列表里组合扫描是**一条记录**（scan_type 仍 `whitebox`，带 `combined=true` 徽标）。

---

## 7. 接力时序（方案 D：phase_end + 独立编排 + _watch 纯 tail）

### 7.1 设计原则（为何不剥 scan_end）

- **`scan_end` 语义不变量**：全场景（纯白盒/纯黑盒/组合）events 文件**只有一个 `scan_end` = 真结束**。`scan_end` 由 `finalize_summary` 的 `log_workflow_complete` → `StructuredEventRenderer` 写入（`structured_event_renderer.py:105`）。组合模式白盒用 **`phase_end`** 表达阶段边界，**绝不剥 scan_end**（剥是 hack，破坏全局不变量 + 脆弱）。
- **职责分离**：`_watch` 回归纯 tail（认 `phase_end`/`phase_boundary` 续 tail，认 `scan_end` 退出）；接力编排抽到独立 task（`_combined_orchestrator`）；白盒 `finalize_summary` 组合模式写 `phase_end`。
- **白盒 workflow 最小改动**：PipelineInput 加 `combined: bool = False`；`finalize_summary`（`activities.py:1806`）组合分支写 `{type:"phase_end", phase:"whitebox"}` 而非触发 `scan_end`；纯白盒不变（写 `scan_end`）。黑盒 workflow **零改动**（写 `scan_end` 作最终）。

### 7.2 时序

```
1. 用户白盒扫描页打开开关，填 repo + url + 认证，提交
   POST /api/scan { type:"whitebox", source, url, authentication/auth_profile_id }
2. scan_manager.start(whitebox):
   - create_scan → scans/<repo>-<ts>/（单目录）
   - session 写 combined=true, bb_phase=pending, bb_url, bb_auth_ref
   - _submit_whitebox → 白盒 workflow @ supernova-wb-web，workflow_id={ws}-{scan_id}
       白盒 PipelineInput.combined=True（通知 finalize 写 phase_end 而非 scan_end）
   - 起 _watch(scan_key, event_file, scan_dir)            ← 纯 tail
   - 起 _combined_orchestrator(scan_key, wb_handle, ...)  ← 接力编排 task
3. 白盒 workflow 跑，产物落 deliverables/whitebox/，events 追加 events.ndjson
4. 白盒 finalize_summary（combined=True）写 phase_end（events 末行，非 scan_end）
5. _combined_orchestrator: await wb_handle.result()  → 白盒完成确认
   - 预检白盒产物（recon_deliverable.md + 至少一个非空 queue）
     不全 → bb_phase=skipped + 写最终 scan_end + 标注 → 编排 task 结束（见 §11）
   - 产物 OK → 接力 _submit_blackbox:
       event_file = 白盒 events.ndjson（→ 黑盒 scan_dir = event_file.parent = 白盒 scan_dir）
       repo_path  = 白盒 scan_dir
       workflow_id = {ws}-{scan_id}-bb
       config_path = _dump_auth_payload(展开 bb_auth_ref) → scan-config.yaml
   - session: status=running, bb_phase=running, 清 bb_auth_ref 明文
   - 写 phase_boundary 控制事件（{type:"phase_boundary", phase:"blackbox"}）作前端分段锚点
6. 黑盒 workflow 跑 @ supernova-bb-web，产物落 deliverables/blackbox/，events 追加同一 events.ndjson
7. 黑盒 finalize_summary 写 scan_end（全场景唯一最终 scan_end）
8. _combined_orchestrator: await bb_handle.result()
   - 生成融合报告 → deliverables/combined/combined_report.md（见 §10）
   - session: status=completed, bb_phase=completed
   - 编排 task 结束
9. _watch（纯 tail）：检测到 scan_end → 退出 + finally 清理（现有逻辑不变）
```

### 7.3 `_watch` 改动（极小，可能零改动）

`_watch` 回归纯 tail。现有 `_has_scan_end`（`scan_manager.py:1154`）**只认 `type=="scan_end"`**：

```python
for line in event_file.read_text(...).splitlines()[-5:]:
    if json.loads(line).get("type") == "scan_end":   # ← 只认 scan_end
        return True
```

`phase_end` / `phase_boundary` 的 `type` ≠ `"scan_end"`，`_has_scan_end` 天然忽略它们 → `_watch` 的 `while not _has_scan_end(...)` 循环继续 tail，直到黑盒 `scan_end`。**`_watch` 主体可能零代码改动**，仅依赖白盒组合模式不写 `scan_end`（写 `phase_end`）。describe/超时/失败检测逻辑全不变。

### 7.4 `_combined_orchestrator`（新增独立编排 task）

scan_manager 新增方法，接力逻辑集中于此（不污染 _watch）：

```python
async def _combined_orchestrator(self, scan_key, wb_handle, scan_dir, bb_params):
    try:
        await wb_handle.result()                       # 等白盒完成
        if not _whitebox_deliverables_ready(scan_dir):
            await self._mark_bb(scan_dir, "skipped", "白盒无可利用产物")
            await self._write_final_scan_end(scan_dir); return
        bb_handle = await self._submit_blackbox_chained(scan_dir, bb_params)
        await self._mark_bb(scan_dir, "running")
        await self._emit_phase_boundary(scan_dir, "blackbox")
        await bb_handle.result()                       # 等黑盒完成
        await self._generate_combined_report(scan_dir)
        await self._mark_bb(scan_dir, "completed")
    except Exception as exc:
        await self._mark_bb(scan_dir, "failed", str(exc))
        await self._write_final_scan_end(scan_dir)     # 异常必写 scan_end，防 _watch 永久 tail
```

- `_watch` 与 `_combined_orchestrator` **并行**：_watch tail events 给前端 live 显示，编排 task 驱动接力 + 报告。两者经 session（`bb_phase`）+ events（`phase_end`/`phase_boundary`/`scan_end`）间接协调。
- **关键契约**：无论编排 task 成功/失败/跳过，都必须写最终 `scan_end`（`_write_final_scan_end`），否则 `_watch` 永久 tail。`_watch` 的 finally 兜底（现有「worker 未写 scan_end」补写，`scan_manager.py:1146`）仍作最后一道防线。

### 7.5 崩溃恢复（`orphan_reconciler` 扩展）

接力是 scan_manager 进程内（非 Temporal durable）。扩展现有 `orphan_reconciler`（启动时处理中断 scan）兜底进程重启：
- `combined=true && bb_phase=pending` 且白盒 workflow 已 completed → 补起编排（预检 + submit blackbox）。
- `combined=true && bb_phase=running` 且黑盒 workflow 已 completed → 补生成融合报告。
- `bb_phase in {pending,running}` 且对应 workflow 仍 running → 重建 handle 登记，续跑。
- 编排 task 异常退出未写 scan_end → reconciler 补 scan_end + `bb_phase=failed`。

> 演进出口：若进程内接力可靠性不足，可升级到方案 B（Temporal 父 workflow）把接力彻底 durable 化。本设计的 `phase_end`/独立编排/单目录三桶机制可平滑迁移——编排 task 的逻辑直接搬进父 workflow。

### 7.6 workflow_id 命名

- 白盒：`{ws}-{scan_id}`（现有 `_resolve_workflow_id`，不改）
- 组合黑盒：`{ws}-{scan_id}-bb`（新增后缀；reconciler/resume 据此定位）

---

## 8. 入口（前端）

### 8.1 白盒扫描页开关

- `ScanNewPage.tsx` 白盒 Tab 内加「同时发起黑盒扫描」开关（Switch/Toggle）。
- **关闭**（默认）：白盒表单 = 今天的样子（repo 选择 + ws），`buildBody` 不带 url/认证 → 纯白盒。
- **打开**：展开黑盒信息区——目标 URL 输入 + 认证模块。**认证模块直接复用现有黑盒表单组件**（`ScanFormFields.tsx:126 RightAuthCore` / `:326 BottomInlineBlock` / `:385 BottomProfileBlock`），抽成共享组件供白盒组合模式复用。
- 校验（`ScanNewPage.tsx:258`）：打开时 url 必填 + 认证必填（与黑盒同规则）。

### 8.2 buildBody（`ScanNewPage.tsx:162`）

打开开关时，白盒请求额外带 `url` + 认证字段（`authentication` 或 `auth_profile_id` 系列）。后端 `_whitebox_combined_optional` validator 识别为组合模式。

### 8.3 correlation 死 Tab

前端 Tab 的 `correlation`（`scan_manager.py:215` 抛「暂未 C1 化」）与本设计无关，保持现状不动（组合扫描挂在白盒 Tab 内的开关，不占 correlation 位）。

---

## 9. 进度（单页统一）

### 9.1 详情页

白盒详情页（`ScanDetail.tsx`）检测 session `combined==true` → 渲染组合视图：
- **单时间线**：读单 `events.ndjson`，按事件时间戳排序，用阶段标签（`phase=whitebox` / `phase=blackbox`）分段着色。阶段边界 = 接力点（bb_phase pending→running）。
- **状态条**：`白盒✓ → 黑盒(进行中/✓/跳过/失败)` 两段式。
- 报告/产物 tab 见 §10。

### 9.2 阶段标注来源

events.ndjson 的事件本身不带 phase 字段。阶段由 scan_manager 接力时**写一条 `phase_boundary` 控制事件**到 events（`{type:"phase_boundary", phase:"blackbox", ts}`）作为分段锚点。前端据此切分；无此事件 = 纯白盒（单段）。

### 9.3 列表页

组合扫描在列表是**一条记录**，scan_type 徽标显示「组合」（`combined==true` 时）。不出现独立的黑盒行（因单 scan_id）。

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
| 漏洞类 | 白盒发现 | 黑盒验证 | 新增利用 |
|---|---|---|---|
| injection | 3 | 2 | 0 |
| xss | 2 | 1 | 0 |
| ssrf | 1 | 0 | 0 |
| authz | 4 | 3 | 1 |
...

## 按漏洞类详述
### injection
#### 白盒视角（代码证据）
  - <白盒 queue 项：sink/数据流/位置>
#### 黑盒视角（利用验证）
  - <黑盒 finding：endpoint/PoC/verdict>
...（每个 vuln_class 一节，白盒+黑盒并列）
```

- **对齐粒度**：vuln_class（黑盒本就按 vuln_class 读白盒 `{vt}_exploitation_queue.json`，`activities.py:652`）。不做单个漏洞 1:1 对齐。
- **生成**：新增 `combined_report_renderer`（复用 `FindingsRenderer`/`ReportAssembler` 的读 queue 逻辑），由 `_combined_orchestrator` 在黑盒完成后调用（§7.4），产 `deliverables/combined/combined_report.md`。
- **归属**：`combined/` 是新桶，`DeliverablesReader._infer_track`（`deliverables_reader.py:71`）扩展识别 `combined` track；`resolve_track_deliverable`（`paths.py:129`）支持 `track="combined"`。

### 10.3 三视图的产品意义

回应「分开扫与一并扫产物一样」：一并扫描不丢任何单独视角（白盒报告、黑盒报告仍在），额外提供融合视图。三视图等价于「分开扫的两份报告 + 一份关联视图」。

---

## 11. 失败 / 取消 / resume 语义

`_combined_orchestrator`（§7.4）是失败/取消/resume 的协调中心——其 try/except 覆盖所有接力异常，且**契约保证写最终 scan_end**（否则 _watch 永久 tail）。

| 情形 | 行为 |
|---|---|
| 白盒失败/超时/crash | `wb_handle.result()` 抛 → 编排 except：`bb_phase=failed` + 标因 + 写 scan_end；_watch 收尾退出；UI「白盒失败，黑盒未启动」 |
| 白盒成功但无产物（空 queue / 缺 recon_deliverable.md） | 编排预检（§7.4）不全 → `bb_phase=skipped` + 标注「白盒无可利用产物」+ 写 scan_end |
| 黑盒失败/超时/crash | `bb_handle.result()` 抛 → 编排 except：`bb_phase=failed` + 标因 + 写 scan_end；白盒报告保留；融合报告不生成 |
| 接力提交本身失败（bb queue 异常） | `_submit_blackbox_chained` 抛 → 编排 except 重试有限次后 `bb_phase=failed` + 标注；白盒结果不受影响 |
| 用户取消（白盒阶段） | cancel 白盒 workflow（`{ws}-{scan_id}`）→ `wb_handle.result()` 抛取消 → 编排 except 收尾 |
| 用户取消（黑盒阶段） | cancel 黑盒 workflow（`{ws}-{scan_id}-bb`）→ `bb_handle.result()` 抛取消 → 编排 except 收尾；白盒结果保留 |
| **resume（白盒阶段中断）** | `bb_phase==pending`（状态 interrupted/crashed）→ resume 白盒 workflow；完成后重启编排 task 续走接力 |
| **resume（黑盒阶段中断）** | `bb_phase==running` → resume 黑盒 workflow（`{ws}-{scan_id}-bb`）；resume 前清障（`_strip_trailing_scan_end`，无 scan_end 则 no-op） |

**resume 判定**（扩展 `scan_manager.resume:225`）：读 session `combined` + `bb_phase`（仅 interrupted/crashed 可 resume；failed/completed/skipped/cancelled 不可，需重扫）：
- 非组合：现有逻辑（按 scan_type resume 对应 workflow）。
- 组合 + `bb_phase==pending`：resume 白盒 workflow_id `{ws}-{scan_id}`，完成后重启 `_combined_orchestrator` 续接力。
- 组合 + `bb_phase==running`：resume 黑盒 workflow_id `{ws}-{scan_id}-bb`，附一个新的编排 task 仅做「等黑盒完成 → 生成融合报告」（接力已发生，不重复 submit）。

**cancel**（扩展 `scan_manager.cancel:915`）：组合扫描按当前 `bb_phase` cancel 对应 workflow_id；单 scan_id 一条记录，cancel 一次取消当前阶段。

---

## 12. 测试策略

| 层 | 测试 | 守卫 |
|---|---|---|
| **validator** | `_whitebox_combined_optional`：白盒+url=组合；白盒无 url=纯白盒禁认证；组合模式认证互斥校验 | models 单测 |
| **编排 task（接力）** | mock `wb_handle.result()` 完成 + 产物就绪 → 断言 `_submit_blackbox` 被调、event_file/repo_path 指向白盒 scan_dir、workflow_id=`{ws}-{scan_id}-bb`、bb_phase=running、写 phase_boundary | scan_manager 单测 |
| **_watch 纯 tail** | phase_end/phase_boundary 不触发退出（`_has_scan_end` 只认 scan_end）；scan_end 退出；纯白盒/纯黑盒 _watch 零回归 | scan_manager 单测，**回归守卫** |
| **白盒 finalize phase_end** | `combined=True` 写 phase_end（非 scan_end）；`combined=False` 写 scan_end（纯白盒零回归） | whitebox finalize 单测 |
| **编排异常契约** | 编排 task 任意异常 → 必写最终 scan_end + `bb_phase=failed`（防 _watch 永久 tail） | scan_manager 单测 |
| **失败语义** | 白盒失败→不接力；白盒无产物→bb_phase=skipped；黑盒失败→白盒报告保留；接力提交失败→重试+标注 | scan_manager 单测 |
| **单目录三桶** | 接力后断言黑盒产物落 `<wb_scan_dir>/deliverables/blackbox/`，白盒 queue 读自 `./deliverables/whitebox/`，融合报告落 `combined/` | 集成测试 |
| **融合报告** | 给定白盒+黑盒 queue 样例 → 断言 vuln_class 交叉输出 + 摘要计数正确 | combined_report_renderer 单测 |
| **前端** | 开关关=纯白盒 body（回归零差异）；开关开=带 url+认证；详情页两段时间线 + phase_boundary 切分；报告三子 tab | vitest 组件测试 |
| **端到端冒烟** | 开关打开跑真实小仓 → 白盒✓→黑盒✓→三报告齐；纯白盒开关关跑同一仓 → 零回归 | 真机/容器冒烟 |

**预存陷阱规避**（见 memory）：
- 前端命令须 `cd packages/web/frontend`（cwd 不持久）。
- 改 web/worker src 须 rebuild supernova-worker 镜像。
- pytest 只跑改动相关子集，勿广跑全套（会 hang）。

---

## 13. 风险与回退

| 风险 | 影响 | 缓解 |
|---|---|---|
| 白盒 `finalize_summary` 组合分支破坏纯白盒 scan_end | 高（白盒 live 收尾全瘫） | combined 分支严格隔离 + 纯白盒写 scan_end 的回归测试守卫（finalize 单测） |
| 编排 task 异常未写 scan_end → _watch 永久 tail | 中（前端假死） | 编排 try/except 契约必写 scan_end；_watch finally 兜底（现有「worker 未写 scan_end」补写，`scan_manager.py:1146`）+ reconciler 补 |
| session 三方写冲突（status 抖动） | 中（状态误判） | 严格界定写者（§6.2 铁律）；终态 status 唯一写者 |
| 黑盒产物落点假设失效（若未来黑盒 workflow 改 scan_dir 推导） | 中（单目录破裂） | spec 锁定 `scan_dir=event_file.parent` 不变量；改动黑盒 workflow 路径推导须同步本设计 |
| 黑盒 workflow 内 `workspace_name` 用于定位 session/heartbeat 等（非仅展示） | 中（单 session 落点偏移） | plan 阶段首项验证：grep 黑盒 workflow 对 `workspace_name` 的所有用途，确认 session/heartbeat 落点 = `event_file.parent`（白盒 scan_dir）；若不一致，接力传参调整或小幅适配 |
| 接力时黑盒读不到白盒产物（时序） | 低 | 接力在 `await wb_handle.result()` 后（workflow complete = 含产物落盘的 activity 均已完成），产物必就绪 |
| 融合报告 vuln_class 聚合偏差 | 低 | 单测覆盖计数逻辑 |
| events 单文件两段过大 | 低 | 单 scan events 量与今天两 scan 相当；不额外膨胀 |

**回退**：组合扫描是白盒入口的**附加模式**（开关默认关）。最坏情况禁用开关（前端隐藏 / env gate），纯白盒+纯黑盒入口完全不受影响。

---

## 14. 关键文件索引

| 主题 | 路径:行号 |
|---|---|
| 白盒 workflow 入口 | `packages/whitebox/src/supernova_whitebox/pipeline/workflows.py:91` |
| 黑盒 workflow scan_dir 推导（`event_file.parent`） | `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py:97` |
| 黑盒读白盒 queue 根（`repo_path/deliverables_subdir`） | `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py:291`、`activities.py:291` |
| ScanManager.start（决策分发） | `packages/web/src/supernova_web/components/scan_manager.py:148` |
| _submit_whitebox | `scan_manager.py:299` |
| _submit_blackbox | `scan_manager.py:479` |
| _resolve_blackbox_inputs（认证展开 + wb scan_dir） | `scan_manager.py:328` |
| _watch（纯 tail；方案 D 不在此接力，认 scan_end 退出） | `scan_manager.py:1102` |
| _combined_orchestrator（接力编排 task，**新增**） | `scan_manager.py`（新增方法） |
| 白盒 finalize_summary（组合模式写 phase_end 而非 scan_end） | `packages/whitebox/src/supernova_whitebox/pipeline/activities.py:1806` |
| StructuredEventRenderer（scan_end 写入点；加 phase_end 事件支持） | `packages/core/src/supernova_core/display/structured_event_renderer.py:105` |
| _has_scan_end / _strip_trailing_scan_end | `scan_manager.py:1154` / `:1184` |
| _write_scan_end | `scan_manager.py:1166` |
| resume | `scan_manager.py:225` |
| cancel | `scan_manager.py:915` |
| ScanRequest + validators | `packages/web/src/supernova_web/models.py:23` |
| WEB task queues | `packages/core/src/supernova_core/services/temporal_infra.py:34-35` |
| session.json 结构 / update_session | `packages/core/src/supernova_core/session.py:34` / `:84` |
| WHITEBOX/BLACKBOX_SUBDIR + whitebox_dir/blackbox_dir | `packages/core/src/supernova_core/utils/paths.py:109-126` |
| resolve_track_deliverable（读侧 fallback） | `packages/core/src/supernova_core/utils/paths.py:129` |
| 前端扫描页 / buildBody / 校验 | `packages/web/frontend/src/pages/ScanNewPage.tsx:162/220/258` |
| 前端表单字段（白盒/黑盒分支 + 认证组件） | `packages/web/frontend/src/components/ScanFormFields.tsx:126/326/385/721/736` |
| 详情页 / 报告 tab / 产物 tab | `packages/web/frontend/src/routes/WorkspaceDetail/ScanDetail.tsx` / `ReportTab.tsx` / `DeliverablesTab.tsx` |
| DeliverablesReader（track 推断，需扩展 combined） | `packages/web/src/supernova_web/components/deliverables_reader.py:71` |
| 现有 combined CLI（参考，不直接用） | `packages/combined/src/supernova_combined/orchestrator.py:20` |

---

## 15. 后续（writing-plans 接管）

本 spec 锁定架构与数据流。实现拆任务交给 writing-plans，预期任务分组：
1. **后端数据模型**：ScanRequest validator 扩展 + session 字段（`combined`/`bb_phase`/`bb_url`/`bb_auth_ref`）。
2. **白盒 finalize phase_end**：PipelineInput 加 `combined` + `finalize_summary` 组合分支写 `phase_end`（StructuredEventRenderer 加 phase_end 事件支持）；纯白盒写 `scan_end` 回归守卫。
3. **scan_manager 接力编排**：`_combined_orchestrator`（await 白盒 → 预检 → `_submit_blackbox_chained` → await 黑盒 → 融合报告）+ 异常契约（必写 scan_end）+ `orphan_reconciler` 扩展。
4. **融合报告**：`combined_report_renderer` + `combined` track 识别（`DeliverablesReader`/`resolve_track_deliverable`）。
5. **resume/cancel**：`bb_phase` 分阶段分支（`pending`→白盒、`running`→黑盒）。
6. **前端入口**：开关 + 认证组件抽共享 + buildBody。
7. **前端进度/报告**：详情页两段时间线 + phase_boundary + 报告三子 tab + 产物三桶。
8. **测试**：§12 矩阵 + 端到端冒烟。
