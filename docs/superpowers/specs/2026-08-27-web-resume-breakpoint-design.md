# Web 断点续跑（真 resume + step cache）设计

- **状态**: 设计稿（已与用户逐节确认）
- **日期**: 2026-08-27
- **范围**: web 扫描的断点续跑——终态（failed/cancelled/killed）与非终态（interrupted/crashed）都可从断点继续；agent 级 + LLM 重活步骤级跳过；断点详情 API 与前端两处入口
- **前作**: `2026-06-19-resume-and-rerun-design.md`（白盒 resume 对账机制——git deliverable commit + `WhiteboxResumeStateBuilder`，CLI 已落地）、`2026-06-19-whitebox-resume.md`（实现 plan）

---

## 1. 背景与问题

用户口径：**当前缺少续跑功能，只有重跑功能，希望加续跑按钮（断点续传）**。核实后发现现状是"机制大半已存在但没接通/没暴露"：

1. **CLI 白盒已有完整断点续传**：`WhiteboxResumeStateBuilder`（`packages/whitebox/src/supernova_whitebox/pipeline/whitebox_resume.py`）以 git `deliverable:` commit（G）∧ 产物文件存在（F）对账出 `completed_agents`，经 `PipelineInput.resume_completed_agents`（`shared.py:18`）预填进 workflow（`workflows.py:105-107`），激活 pre-recon（L211）/ recon（L299）/ 5 vuln agent（L385/L402）的跳过守卫。CLI 入口 `--fresh` / `--rewind <phase>`。
2. **web 的 resume 是"假续跑"**：`scan_manager.resume()`（`scan_manager.py:503-672`）只换 workflow id（`-resume-N`）重交，`_submit_whitebox`（L674-709）构造 `PipelineInput` 时**不设置** `resume_completed_agents`（全仓 grep 该字段唯一生产者是 CLI 路径 `whitebox/worker.py:261`）→ 即使点了"恢复"，所有 agent 从头跑。
3. **续跑入口缺位**：`_RESUMABLE_STATUSES = {interrupted, crashed}`（`scan_manager.py:80`）——最常见的中断出口 **failed（终态）被排除**；前端"恢复"按钮条件 `canResume = !isRunning && !isTerminal`（`ScanList.tsx:301`），failed 终态只显示"重跑"（预填配置新建 scan，从头跑）。
4. **步骤级无跳过**：LLM 重活步骤（GitNexus 链判定 15-20min、authz 深判 30min 窗口、report_polish 20-30min、双轨 merge 内 track-parity、gn/endpoint 富化多轮 agent、attack-chain LLM）每次 resume 全部重跑。其中 merge / 富化是**原地覆盖型**——`run_merge_dual_track_queues` 把合并版写回 `{vc}_exploitation_queue.json`（`activities.py:1056`，与 LLM agent 产物同名），`run_gn_finding_enrichment` 富化后写回同一文件（L1285-1288）——"产物存在"无法区分步骤进度，且上游重跑会污染跳过判断。

## 2. 需求（用户已澄清）

- **覆盖状态**：failed / interrupted / cancelled / killed / crashed（全部非 completed 的状态）；running 在跑、completed 已完成，均不可续跑。
- **跳过粒度**：agent 级（已有基建）**+ LLM 重活步骤扩展**（真断点续传）。
- **UI**：列表行内 + 详情页**两处入口**；详情页展示**断点详情**（各 agent/步骤完成状态、从哪继续）。
- **方案选型**：步骤跳过用 **activity 自检**（不动 workflow 编排、CLI/web 共享），实化为 marker + 输入指纹（见 §4.3）。

### 范围外（明确不做）

- 纯黑盒行的 agent 级续跑（黑盒非幂等真打目标，2026-06-19 spec 立场不变；黑盒失败走整体 rerun）。
- 组合扫描黑盒段失败：沿用已有"续扫黑盒"（`rerun-blackbox`，复用白盒产物 + run-K 版本化）。
- correlation 主行续跑：维持拒绝（提示重新提交，子仓白盒产物保留可复用）。
- web 侧 rewind（`--rewind <phase>` 阶段回退）——CLI 已有，web 暂不暴露。
- 本地快步骤（risk-scoring / dataflow view / assemble_report / export / attack_chain_assembly）的跳过——秒-分钟级幂等重跑，YAGNI。

## 3. 现状机制盘点（实现依据）

| 机制 | 位置 | 状态 |
|---|---|---|
| A. 重跑按钮 | `ScanList.tsx:523-527`（终态显示）→ `getScan` 预填 → 新建 scan | 保留不动 |
| B. 续扫黑盒 | `ScanDetail.tsx` bbPhase=failed → `POST .../combined/rerun-blackbox` | 保留不动 |
| C. resume | `ScanList.tsx:505-509` + `POST .../resume` + `scan_manager.resume()` | 本次接通+扩展 |
| D. CLI resume | `WhiteboxResumeStateBuilder` + `whitebox/worker.py:228-319` 接线 | 零改动自动受益于 §4.3 |

关键既有基建：`GitManager.get_completed_agents`（git log `^deliverable:`）、`reconcile` 决策表（G∧F 完成 / G∧¬F 中止 / ¬G 重跑）、`cleanup`（auto 删 ¬G 半成品）、`atomic_write_json`（tmp + os.replace，产物完整才存在）、`is_scan_recently_active`（heartbeat 判活）、`_strip_trailing_scan_end`（续跑前剥旧收尾事件）。

## 4. 设计

### 4.1 状态集扩展与判活（web）

`_RESUMABLE_STATUSES` 扩为 `{interrupted, crashed, failed, cancelled, killed}`：

- **failed**：Temporal workflow 已 FAILED，无并发风险，直接放行。
- **cancelled / killed / crashed / interrupted**：resume 前先 `is_scan_recently_active(scan_dir)` 判活，心跳新鲜 → ValueError"仍在运行"（防与残留 workflow 撞车；对齐 correlation 分支既有口径）。
- API 层（`scans.py resume_scan`）同步放开状态校验；错误映射沿用现状（ValueError→422）。

### 4.2 web resume 接通 agent 级断点续传

`scan_manager.resume()` 在提交白盒 workflow 前增加对账（覆盖两个入口：非组合白盒行 L664、组合扫描白盒段 L603）：

```python
deliverables = scan_dir / "deliverables" / "whitebox"
rstate = await WhiteboxResumeStateBuilder().build(
    mode="auto", workspace=scan_dir, deliverables=deliverables,
    repo_path=Path(data.get("repo_path") or ""))
if rstate.aborted:
    raise ValueError(rstate.abort_reason)          # G∧¬F → 422 带 abort_reason
await builder.cleanup(mode="auto", deliverables=deliverables,
                      completed_agents=rstate.completed_agents)
handle = await self._submit_whitebox(
    ..., resume_completed_agents=rstate.completed_agents)
```

- `_submit_whitebox` 增可选参数透传进 `PipelineInput.resume_completed_agents`（workflow L105-107 已消费，零改动）。
- `rstate.warnings` / 摘要（跳过 N agent、从 X 继续）经 `log_info_activity` 同款机制写一条 InfoEvent 进 events.ndjson（用户可在 live 流看到续跑摘要）。
- builder 的 J 信号读 `scan_dir/session.json`（与 CLI 的 workspace 目录语义一致，builder `_session_success` 已按 `workspace/session.json` 读）。
- 黑盒行 / correlation 主行：维持现状分支不动（黑盒 resume 既有重交逻辑不变——`resume_completed_agents` 仅白盒管线消费）。

### 4.3 step cache：LLM 重活步骤级自检（marker + 输入指纹 + 返回值快照）

新模块 `packages/whitebox/src/supernova_whitebox/pipeline/step_cache.py`：

```python
def mark_done(step: str, deliverables: Path, inputs: list[Path],
              outputs: list[Path], ret: dict | None = None) -> None
    # 原子写 intermediate/.step-cache/{step}.json
    # {"step": ..., "ts": ...,
    #  "inputs":  {str(path): "mtime_ns:size"},   # 上游依赖指纹（防陈旧）
    #  "outputs": [str(path), ...],               # 本步骤产物清单（防缺失）
    #  "ret": <返回值快照 JSON>}

def should_skip(step: str, deliverables: Path,
                inputs: list[Path]) -> tuple[bool, dict | None]
    # marker 可解析 ∧ inputs 指纹全匹配 ∧ outputs 全部存在 → (True, ret 快照)
    # 任一不满足（marker 缺失/损坏、输入缺失或指纹变化、产物缺失）→ (False, None)
    #                                                     # 一律 fail-open 到重跑
```

**完成信号是 marker（显式记录），不是业务产物存在性**——`mark_done` 只在 activity 成功末尾调用，"步骤跑到一半失败"（如 chain_verdict 逐类写 3 个 queue 时第 2 类崩）必然无 marker → 整步重跑（部分旧产物被原地覆盖）；`atomic_write_json`（tmp + os.replace）防"写一半残缺文件"；marker 自身损坏解析失败即 fail-open。

- **指纹 = `(mtime_ns, size)`**：轻量；无需内容 hash。
- **双向校验**：输入侧指纹匹配（上游任何重写 → 指数级变化 → 重跑）+ 输出侧存在校验（产物清单任一缺失 → 重跑，防人工误删/磁盘损坏——对齐 agent 级 G∧¬F 中止保护的步骤级对应物）。
- **返回值快照**：有返回值的 activity（如 `run_gitnexus_chain_verdict` 的 fail-fast 判定数据）跳过时直接还原缓存返回值，workflow 下游判断逻辑不变。
- **完成语义分级（防"降级完成"误标记）**：只有**完全成功**才 `mark_done`。两类路径必须区分：
  - **业务性失败结论**（干完了活、结论是失败，如 chain_verdict 的 `failed_classes`）→ 照常 `mark_done`，resume 跳过合理；
  - **降级完成**（异常被吞掉、产出残缺结果还继续，如 `run_gn_finding_enrichment` agent 失败降级为不富化 `activities.py:1271-1278`、merge 内 track-parity 增强层失败吞异常继续 `L1049-1052`）→ **不写 marker**，resume 时视为未完成、重试追求完整结果。
  - plan 阶段逐 activity 标注降级分支清单，`mark_done` 调用点必须在这些分支 return 之前不触发（放完全成功路径）。

**应用到 7 个 LLM 重活 activity**（每个 +6 行接线：开头 `should_skip` 早退，成功末尾 `mark_done`）：

| activity（activities.py 行号） | step 名 | 输入指纹清单 | 跳过收益 |
|---|---|---|---|
| `run_authz_gitnexus_judge` (441) | `authz-gitnexus-judge` | parameter_graph.json、entry_points.json、sink 产物 | 30min 窗口多轮 agent |
| `run_gitnexus_chain_verdict` (2738) | `gitnexus-chain-verdict` | parameter_graph.json、sink 产物（chain builder 输入） | 15-20min（27 链串行） |
| `run_merge_dual_track_queues` (944) | `merge-dual-track` | 5×`{vc}_exploitation_queue.json` + 5×`{vc}_gitnexus_queue.json` | track-parity LLM 调用 |
| `run_gn_finding_enrichment` (1205) | `gn-finding-enrichment` | 3×`{vc}_exploitation_queue.json`（inj/xss/ssrf） | 多轮 agent ×3 |
| `run_endpoint_enrichment` (1300) | `endpoint-enrichment` | 5×`{vc}_exploitation_queue.json` + entry_points.json | 多轮 agent ×5 |
| `run_report_polish` (2095) | `report-polish` | report_data.json + 5×`{vc}_exploitation_queue.json` | 20-30min |
| `run_attack_chain_llm_agent` (3123) | `attack-chain-llm` | recon_deliverable.md + 5×`{vc}_exploitation_queue.json` | LLM 攻击链 |

（输入清单在 plan 阶段逐 activity 按实际读文件精化；上表为实现锚点。）

**一致性论证**（双向校验为何安全）：
- 输入指纹变化 → 必然重跑（fail-open）。
- 上游 agent 被 resume 跳过（G 信号命中）→ 其产物未被重写 → 下游步骤指纹不变 → 跳过安全。
- 上游 agent 重跑（¬G，含"agent 失败但 workflow 继续跑了下游"的场景）→ 重写输入文件 → 指纹变化 → 下游步骤自动重跑，杜绝陈旧数据。
- 本步骤产物部分缺失（人工误删/磁盘损坏）→ outputs 校验不过 → 重跑重建。
- 注意 merge/enrichment 的原地覆盖性：同一文件被多步骤先后写——指纹记录的是"该步骤完成时刻"的状态，上游任何人重写都会破坏匹配。

**CLI 零改动受益**：step cache 在 activity 层，CLI resume（`--rewind`/auto）与 web resume 走同一批 activity。

### 4.4 断点详情 API（resume-preview）

新增 `GET /api/workspaces/{ws}/scans/{scan_id}/resume-preview`（`scans.py`，只读不动状态）：

```json
{
  "status": "failed",
  "resumable": true,
  "reason": null,
  "scan_type": "whitebox",
  "completed_agents": ["pre-recon", "recon", "injection-vuln"],
  "interrupted_agent": "xss-vuln",
  "steps": [
    {"step": "gitnexus-chain-verdict", "state": "done", "ts": 1756272000.0},
    {"step": "merge-dual-track", "state": "stale", "reason": "输入已变化"}
  ],
  "warnings": ["xss-vuln: 产出物文件存在但无 deliverable commit（半成品/旧残留），将重跑"],
  "abort_reason": null,
  "resume_attempts": 2
}
```

- 实现 = 复用 `WhiteboxResumeStateBuilder.build()`（agent 对账，**不调 cleanup、不 abort 抛异常**——abort 映射为 `resumable:false` + `abort_reason`）+ 读 `.step-cache/` markers（`done` = marker 存在且指纹此刻仍匹配；`stale` = 存在但指纹已变；`missing` = 无 marker）。
- correlation 主行：`resumable:false` + 提示"关联扫描暂不支持断点恢复，请重新提交（子仓白盒产物可复用）"。
- 黑盒行：`resumable:false`（黑盒走 rerun 语义）。

### 4.5 前端（两处入口 + 断点详情）

**列表行内（`ScanList.tsx`）**：
- "恢复"按钮升级为"续跑"：显示条件 `!isRunning && status ∉ {completed, done}`（覆盖 failed/cancelled/killed/crashed/interrupted），白盒与组合行显示；correlation / blackbox 行不显示（对齐后端）。
- 点击 → `GET resume-preview` → 确认弹窗摘要：
  - 可跳过时："已完成 3 项（pre-recon / recon / injection-vuln），将从 xss-vuln 继续" + 步骤缓存条数；
  - 无可跳过时："无可跳过部分，将从头扫描"（续跑仍可用，等同原 resume 语义）；
  - `resumable:false` 时展示原因（产物缺失→引导重跑；仍在运行→提示等待）。
- 确认后 `POST .../resume`（现有 API，语义已升级），成功跳转详情 live tab。
- "重跑"按钮保留不动（新建 scan 语义），与"续跑"并存。

**详情页（`ScanDetail`）**：非 completed 状态且非 blackbox/correlation 行时新增"断点详情"区块：
- agent 完成状态列表：✅ 已完成（completed_agents）/ ▶ 将从此继续（interrupted_agent）/ ⏳ 未跑到；
- 步骤缓存状态（done/stale/missing 简表）；
- warnings 逐条展示；
- 续跑按钮（同一确认流）。

**i18n**：zh/en 词条约 10-15 条（`workspaceDetail.scans.resume*` 命名空间沿用扩展）。

### 4.6 错误处理汇总

| 场景 | 行为 |
|---|---|
| G∧¬F 产物丢失（builder abort） | resume → 422 带 abort_reason；preview → `resumable:false`；前端引导用重跑 |
| cancelled 等状态但心跳新鲜 | 422 "仍在运行，无需恢复" |
| step marker 损坏 / 输入缺失 | `should_skip` 返 False → 该步骤重跑（fail-open） |
| resume 提交 Temporal 失败 | 既有 `_mark_submission_failed` 路径不变 |
| 并发上限 | 既有 `TooManyScans` 不变 |
| 重复点击 | 前端 busy 态 + 后端 running 校验双保险 |

## 5. 测试策略（TDD）

1. **step_cache 单测**（新 `packages/whitebox/tests/test_step_cache.py`）：指纹一致跳过并还原快照 / 输入 mtime 变化不跳 / 输入缺失不跳 / **outputs 任一缺失不跳（产物不全→重跑）** / marker 损坏不跳 / 无 marker 不跳 / ret=None 步骤往返。
2. **activity 接线测试**：7 个 activity 各 1 条——预置 marker+输入 → 断言 LLM runner 未被调用、返回缓存值（mock runner 断言 not called）；enrichment/merge **降级路径 1 条**——agent 失败降级返回后断言未写 marker（resume 会重试）。
3. **scan_manager 测试**（扩展 `test_scan_manager*` 既有文件）：resume 白盒行传 `resume_completed_agents` 进 PipelineInput / abort → ValueError / 新状态集放行（failed/cancelled/killed）/ 心跳新鲜拒绝 / 组合白盒段同通路 / cleanup 被调用删半成品。
4. **API 测试**（`packages/web/tests/`）：resume-preview 响应形状 / abort 映射 / correlation 与 blackbox 的 `resumable:false` / resume 状态校验放开。
5. **前端测试**：按钮显示条件矩阵（failed/cancelled/killed/interrupted 显示，completed/blackbox/correlation 不显示）/ 确认弹窗摘要渲染 / 断点详情区块渲染。
6. **人工冒烟**：跑 NodeGoat 到 recon 完成后 kill worker → web 点续跑 → 验证 pre-recon/recon 跳过（live 无重跑日志）+ GitNexus 判定/merge 走 step cache（日志显示 skip）+ 扫描终态 completed。

## 6. 改动清单

| 文件 | 改动 |
|---|---|
| `packages/web/src/supernova_web/components/scan_manager.py` | `_RESUMABLE_STATUSES` 扩集 + 判活；resume 白盒/组合两入口接 builder+cleanup；`_submit_whitebox` 加 `resume_completed_agents` 透传；InfoEvent 写续跑摘要 |
| `packages/web/src/supernova_web/api/scans.py` | 新增 `GET .../resume-preview`；`resume_scan` 状态校验放开 |
| `packages/whitebox/src/supernova_whitebox/pipeline/step_cache.py` 🆕 | `mark_done` / `should_skip`（marker+指纹+outputs 清单+快照，降级路径不打点） |
| `packages/whitebox/src/supernova_whitebox/pipeline/activities.py` | 7 个 LLM 重活 activity 接自检（各 +6-10 行；enrichment/merge 降级分支确认不触发 mark_done） |
| `packages/web/frontend/src/routes/WorkspaceDetail/ScanList.tsx` | "恢复"→"续跑"（条件+确认弹窗带 preview 摘要） |
| `packages/web/frontend/src/routes/WorkspaceDetail/ScanDetail.tsx`（或子组件 🆕） | 断点详情区块 + 续跑按钮 |
| `packages/web/frontend/src/api/client.ts` | `getResumePreview()` |
| zh/en i18n 词条 | 续跑相关 ~10-15 条 |
| 测试文件（§5） | 新增/扩展 |

**不动的部分**：workflow 编排（`workflows.py` 零改动——守卫已存在）、CLI resume（`whitebox/worker.py` 零改动）、黑盒 rerun（机制 B）、correlation 分支、重跑按钮（机制 A）。

## 7. 风险登记

1. **指纹误匹配**（mtime+size 碰撞）：概率极低（atomic_write 的 tmp+rename 使 mtime_ns 变化）；后果=跳过一次本应重跑的步骤，可接受；兜底：删除 `.step-cache/` 目录即强制全重跑。
1b. **"产物存在≠完整"的完整防线**（用户 2026-08-27 质疑推动）：跳过信号是 marker（成功末尾显式记录）而非业务产物存在性——中途失败必无 marker → 重跑；残缺文件被 atomic_write 原子性排除；产物缺失去重于 outputs 校验；**降级完成（吞异常产出残缺结果）不打 marker** → resume 重试。残余风险只剩"产物文件在盘且 JSON 合法但内容语义损坏（未被重写、指纹未变）"——等同于外部磁盘静默损坏，agent 级对账（git G 信号）同样无法防御，接受；兜底同上（删 `.step-cache/`）。
2. **builder abort 语义在 web 的呈现**：G∧¬F 中止曾只存在于 CLI（人读 stderr）；web 需把 abort_reason 透传到 422 与 preview，前端要有明确引导（本设计 §4.4/§4.6 已覆盖）。
3. **组合扫描白盒段的 repo_path 解析**：resume 组合分支现从 session 读 `repo_path`（L600），builder 需要同一值定位 deliverables git 仓——路径即 scan_dir 下的 deliverables/whitebox，不依赖 repo_path 本身（G 信号读 deliverables git 仓，`GitManager.get_completed_agents(deliverables)`）；plan 阶段核实组合行 deliverables 路径一致性。
4. **step cache 与 retry 交互**：activity 失败重试（Temporal retry policy）时 marker 未写（`mark_done` 仅成功末尾）→ 重试正常重跑，无脏 marker。
5. **老 session 兼容**：旧扫描目录无 `.step-cache/` → 全部 missing → 正常重跑；无 deliverables git 仓的更老目录 → G 集空 → 全部重跑（现 builder 语义）。
6. **`_watch` 与续跑后事件流**：`_strip_trailing_scan_end` 已处理旧 scan_end；resume 后 live tab 续流为既有机制，零新增。

## 8. 实施顺序（writing-plans 细化）

Phase 1 — step cache 模块 + 7 activity 接线（纯 whitebox，CLI 立即受益）
Phase 2 — web 状态集扩展 + resume 接通 builder（真续跑核心）
Phase 3 — resume-preview API + 前端两处入口 + 断点详情
每 Phase TDD + 回归（只跑相关测试文件，勿全量——项目预存挂起）。
