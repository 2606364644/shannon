# Web 断点续跑（真 resume + step cache）设计

- **状态**: 设计稿 v2（2026-08-27 评审修订：step cache 7 步→2 步、新增 merge 幂等硬前置、段 3 重跑取舍显式化，见 §0）
- **日期**: 2026-08-27
- **范围**: web 扫描的断点续跑——终态（failed/cancelled/killed）与非终态（interrupted/crashed）都可从断点继续；agent 级跳过 + **两个独立大窗口步骤缓存**（authz-judge / chain-verdict）；段 3 链式五步接受每次重跑（安全性由 merge 幂等保证，§4.4）；断点详情 API 与前端两处入口
- **前作**: `2026-06-19-resume-and-rerun-design.md`（白盒 resume 对账机制——git deliverable commit + `WhiteboxResumeStateBuilder`，CLI 已落地）、`2026-06-19-whitebox-resume.md`（实现 plan）

---

## 0. 修订记录（2026-08-27 评审，对照代码逐条验证后）

1. **step cache 从 7 步砍到 2 步**。原设计对 merge → gn-enrich → endpoint-enrich → attack-chain → report-polish 五步的输入指纹校验**系统性失效**：五步共享 `{vc}_exploitation_queue.json` 串行读+原地覆写（merge 覆写 `activities.py:1056` → gn-enrich `:1285` → endpoint `:1372`），**后面步骤的合法覆写会让前面步骤的完成指纹恒失配**——中断点在 merge 之后时（最常见：死在 report_polish 20-30min 窗口），五步全部重跑，且重跑起点回退到链头 merge 而非中断点。指纹方案只对输入无人覆写的 authz-gitnexus-judge / gitnexus-chain-verdict 成立——两步的输入（parameter_graph / entry_points / sink 产物）只在 pre-recon 守卫块内写（`workflows.py:211-297`，挂在 pre-recon agent 的 G 守卫下整块跳过），块后无人再写，指纹跨 resume 稳定。
2. **merge 幂等修复成为硬前置**（§4.4）。agent 级跳过接通后，中断点在 merge 之内/之后的续跑，merge 重跑会读到"已合并/已富化"的 exploitation_queue 并把它当 LLM 轨原始输入**再合并一遍**（double-merge）：merge_source 漂移、GN-only 卡配对自己、llm 计数虚高，且 `activities.py:1004-1008` 无条件备份会用合并版覆盖唯一的 LLM 原始件（不可恢复）。CLI resume 今天在中断于 merge 之后时已有同一隐患；web 接通 agent 级跳过后将其推上主路径。与是否做 step cache 无关——重跑本身就触发。
3. **段 3 重跑取舍显式记录**（§7.7）：接受五步每次 resume 重跑，换取零链式缓存复杂度；成本随扫描规模增长（少发现扫描 ~5-15min，多卡片扫描几十分钟墙钟 + 全额 token），自觉接受。

## 1. 背景与问题

用户口径：**当前缺少续跑功能，只有重跑功能，希望加续跑按钮（断点续传）**。核实后发现现状是"机制大半已存在但没接通/没暴露"：

1. **CLI 白盒已有完整断点续传**：`WhiteboxResumeStateBuilder`（`packages/whitebox/src/supernova_whitebox/pipeline/whitebox_resume.py`）以 git `deliverable:` commit（G）∧ 产物文件存在（F）对账出 `completed_agents`，经 `PipelineInput.resume_completed_agents`（`shared.py:18`）预填进 workflow（`workflows.py:105-107`），激活 pre-recon（L211）/ recon（L299）/ 5 vuln agent（L385/L402）的跳过守卫。CLI 入口 `--fresh` / `--rewind <phase>`。
2. **web 的 resume 是"假续跑"**：`scan_manager.resume()`（`scan_manager.py:503-672`）只换 workflow id（`-resume-N`）重交，`_submit_whitebox`（L674-709）构造 `PipelineInput` 时**不设置** `resume_completed_agents`（全仓 grep 该字段唯一生产者是 CLI 路径 `whitebox/worker.py:261`）→ 即使点了"恢复"，所有 agent 从头跑。
3. **续跑入口缺位**：`_RESUMABLE_STATUSES = {interrupted, crashed}`（`scan_manager.py:80`）——最常见的中断出口 **failed（终态）被排除**；前端"恢复"按钮条件 `canResume = !isRunning && !isTerminal`（`ScanList.tsx:301`），failed 终态只显示"重跑"（预填配置新建 scan，从头跑）。
4. **步骤级无跳过**：LLM 重活步骤每次 resume 全部重跑。其中 merge / 富化是**原地覆盖型**——`run_merge_dual_track_queues` 把合并版写回 `{vc}_exploitation_queue.json`（`activities.py:1056`，与 LLM agent 产物同名），`run_gn_finding_enrichment` 富化后写回同一文件（L1285-1288）——"产物存在"无法区分步骤进度，且上游重跑会污染跳过判断。（评审修订：步骤级缓存范围收窄至 2 步 + 段 3 取舍，见 §0。）

## 2. 需求（用户已澄清）

- **覆盖状态**：failed / interrupted / cancelled / killed / crashed（全部非 completed 的状态）；running 在跑、completed 已完成，均不可续跑。
- **跳过粒度**：agent 级（已有基建）+ **两个独立大窗口步骤**（authz-gitnexus-judge / gitnexus-chain-verdict，§4.3）；**段 3 链式五步（merge / gn-enrich / endpoint-enrich / attack-chain / report-polish）明确不做缓存、接受每次重跑**（取舍 §7.7，重跑安全性由 §4.4 merge 幂等保证）。
- **UI**：列表行内 + 详情页**两处入口**；详情页展示**断点详情**（各 agent/步骤完成状态、从哪继续）。
- **方案选型**：步骤跳过用 **activity 自检**（不动 workflow 编排、CLI/web 共享），实化为 marker + 输入指纹（见 §4.3，仅 2 步）。

### 范围外（明确不做）

- 纯黑盒行的 agent 级续跑（黑盒非幂等真打目标，2026-06-19 spec 立场不变；黑盒失败走整体 rerun）。
- 组合扫描黑盒段失败：沿用已有"续扫黑盒"（`rerun-blackbox`，复用白盒产物 + run-K 版本化）。
- correlation 主行续跑：维持拒绝（提示重新提交，子仓白盒产物保留可复用）。
- web 侧 rewind（`--rewind <phase>` 阶段回退）——CLI 已有，web 暂不暴露。
- 本地快步骤（risk-scoring / dataflow view / assemble_report / export / attack_chain_assembly）的跳过——秒-分钟级幂等重跑，YAGNI。
- **段 3 链式五步的步骤级缓存**——原地覆盖链指纹恒失配（§0.1），接受每次重跑；merge 幂等修复（§4.4）不做"跳过"，只保证重跑干净。
- 段 3 缓存化的将来路径（merge/enrich 改写独立产物文件使数据流 DAG 化，或 git `step:` commit 信号）——本期不做，记录于 §7.7。

## 3. 现状机制盘点（实现依据）

| 机制 | 位置 | 状态 |
|---|---|---|
| A. 重跑按钮 | `ScanList.tsx:523-527`（终态显示）→ `getScan` 预填 → 新建 scan | 保留不动 |
| B. 续扫黑盒 | `ScanDetail.tsx` bbPhase=failed → `POST .../combined/rerun-blackbox` | 保留不动 |
| C. resume | `ScanList.tsx:505-509` + `POST .../resume` + `scan_manager.resume()` | 本次接通+扩展 |
| D. CLI resume | `WhiteboxResumeStateBuilder` + `whitebox/worker.py:228-319` 接线 | 零改动自动受益于 §4.3（2 步缓存）+ §4.4（merge 幂等，同修 CLI 中断于 merge 之后的既有 double-merge 隐患） |

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

### 4.3 step cache：两个大窗口步骤（marker + 输入指纹 + 返回值快照）

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

**完成信号是 marker（显式记录），不是业务产物存在性**——`mark_done` 只在 activity 成功末尾调用，"步骤跑到一半失败"必然无 marker → 整步重跑；`atomic_write_json`（tmp + os.replace）防"写一半残缺文件"；marker 自身损坏解析失败即 fail-open。

- **指纹 = `(mtime_ns, size)`**：轻量；无需内容 hash。
- **双向校验**：输入侧指纹匹配 + 输出侧存在校验（产物清单任一缺失 → 重跑，防人工误删/磁盘损坏——agent 级 G∧¬F 中止保护的步骤级对应物）。
- **返回值快照（对这 2 步必需）**：两步的返回值都被 workflow 下游消费——chain-verdict 的 `per_class`/`failed_classes` 喂 `write_track_status` 与 fail-fast 判定（`workflows.py:463-492`）、authz-judge 的 `failed`/`verdict_count` 进 `_statuses`（L469-473）。跳过时直接还原缓存返回值，workflow 判断逻辑不变。

**完成语义（v2 修订）：只有"干净完成"才 `mark_done`**：

- `run_gitnexus_chain_verdict`：`failed_classes` 为空（全部类判完）→ 打点；**非空 → 不打**（resume 重试）。理由：关轨模式 fail-fast（`workflows.py:485-492` non_retryable raise）下若打点，resume 会用缓存返回值原地重判 fail-fast 再死一次，续跑永远无效；resume 的语义就是"再试一次"。预算护栏的 unadjudicated 保守条是"干完活的结论"（不是 failed_classes），随干净完成正常打点。
- `run_authz_gitnexus_judge`：`failed != True` → 打点；`failed=True` → 不打（authz fail 本就不终止 workflow，中断后续跑重试合理）。
- 原 v1"业务性失败结论照常打点"口径**撤销**，统一按"干净完成才打点"——规则更简单且避开 fail-fast 陷阱；代价记录于 §7.9。
- 降级不打点原则保留给未来接线者：吞异常产出残缺结果的路径，`mark_done` 不得在其之前触发。

**应用 2 个 activity**（每个 +6-10 行接线：开头 `should_skip` 早退，干净完成末尾 `mark_done`）：

| activity（activities.py 行号） | step 名 | 输入指纹清单 | 输出清单 | 跳过收益 |
|---|---|---|---|---|
| `run_authz_gitnexus_judge` (441) | `authz-gitnexus-judge` | parameter_graph.json、entry_points.json、sink 产物 | authz_gitnexus_queue.json | 30min 窗口多轮 agent |
| `run_gitnexus_chain_verdict` (2738) | `gitnexus-chain-verdict` | parameter_graph.json、code_index.json（sink_call_sites，chain builder 输入） | inj/xss/ssrf `gitnexus_queue.json` ×3 | 15-20min（27 链串行；并发后 ÷N） |

（输入清单在 plan 阶段逐 activity 按实际读文件精化；上表为实现锚点。）

**为什么只这 2 步（一致性论证）**：

- 两步的输入全部产自 pre-recon 守卫块（`workflows.py:211-297`），该块挂在 pre-recon agent 的 G 守卫下**整块跳过**，块后无人再写这些文件 → 输入指纹跨 resume 稳定，指纹匹配 ⇔ 输入真没变。
- pre-recon ¬G 整块重跑时，两步在首跑必然还没执行过（无 marker），不存在陈旧缓存。
- 反例即段 3 链（§0.1）：输入被链上后续步骤合法覆写，指纹恒失配——缓存既无收益（跳不过），还会掩盖 double-merge 风险（本可被跳过挡住），故明确不做。

**CLI 零改动受益**：step cache 在 activity 层，CLI resume（`--rewind`/auto）与 web resume 走同一批 activity。

### 4.4 merge 幂等修复（硬前置，纯 whitebox，CLI 同受益）★ v2 新增

**问题**：`run_merge_dual_track_queues`（`activities.py:981-1059`）读 `{vc}_exploitation_queue.json` 当 LLM 轨原始输入、**无条件**把它备份到 `{vc}_llm_queue.json`（L1004-1008）、合并后覆写回同一文件（L1056）。agent 级跳过生效后的续跑中，merge 重跑读到的"LLM 轨输入"实为自己上轮输出（合并+富化版）→ **double-merge**（merge_source 漂移、GN-only 卡配对自己、llm 计数虚高、track-parity 重复计费），且无条件备份会用合并版**覆盖唯一的 LLM 原始件**，不可恢复。

**修复（~10 行，merge 逐类循环内生效）**：

```python
findings = parse(exploitation_queue)
already_merged = any(getattr(f, "merge_source", None) for f in findings)
    # merge_source 是 merge 自己打的标（queue schema 中由 merger 设置），
    # LLM agent 产的卡没有 → 可靠判别"该文件是否已是合并版"

if already_merged and llm_backup.exists():
    llm_input = 读 llm_backup      # 重跑路径：用真·LLM 原始件重建，不二次合并；
                                   # 备份不重写（原件得以保全）
else:
    llm_input = 读 exploitation    # 首跑 或 ¬G agent 重跑产了新件（无 merge_source）
    写 llm_backup(llm_input)       # 备份只在此路径发生（替换现 L1004-1008 无条件备份）
```

- **为什么内容判别而非 mtime**：mtime 区分不了"上游 agent 重写"和"下游 gn-enrich/endpoint 覆写"（两者都让 exploitation 比备份新）；`merge_source` 是 merge 语义独有标记，两个方向都可靠。
- **半途中断天然按类分流**：merge 逐类处理，已合并的类（带标）走备份、没合并的类（无标）走原件。
- **重跑输出仍写回 exploitation**（SSOT 语义不变）：段 3 其余步骤在此干净底座上重跑（gn-enrich 重新富化、endpoint 重写接口表、attack-chain / report-polish 重跑），收敛到正确报告——这是"接受段 3 整链重跑"方案的安全性支柱。
- **附带兜住既有缺口**：¬G agent 重跑又失败时，其残留的陈旧 queue 若带合并标会被正确路由到备份（等于用上轮原件），不会把半成品当新 LLM 产物二次合并；builder `cleanup` 无需扩到 intermediate queue。
- **确定性**：备份是 collapse 前的原始件（现 L1004-1008 在 collapse L1020 之前备份），重跑从同一原始件 + 未变的 `{vc}_gitnexus_queue.json` 重建，merge 结果与首跑一致；track-parity LLM 层非确定，重跑可能配对不同——接受，属重跑语义。
- **CLI 同受益**：CLI resume 中断于 merge 之后的 double-merge 隐患同源同修。

### 4.5 断点详情 API（resume-preview）

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
    {"step": "authz-gitnexus-judge", "state": "stale", "reason": "输入已变化"}
  ],
  "warnings": ["xss-vuln: 产出物文件存在但无 deliverable commit（半成品/旧残留），将重跑"],
  "abort_reason": null,
  "resume_attempts": 2
}
```

- 实现 = 复用 `WhiteboxResumeStateBuilder.build()`（agent 对账，**不调 cleanup、不 abort 抛异常**——abort 映射为 `resumable:false` + `abort_reason`）+ 读 `.step-cache/` markers（`done` = marker 存在且指纹此刻仍匹配；`stale` = 存在但指纹已变；`missing` = 无 marker）。steps 仅含 §4.3 的 2 步（段 3 无缓存不进列表）。
- correlation 主行：`resumable:false` + 提示"关联扫描暂不支持断点恢复，请重新提交（子仓白盒产物可复用）"。
- 黑盒行：`resumable:false`（黑盒走 rerun 语义）。

### 4.6 前端（两处入口 + 断点详情）

**列表行内（`ScanList.tsx`）**：
- "恢复"按钮升级为"续跑"：显示条件 `!isRunning && status ∉ {completed, done}`（覆盖 failed/cancelled/killed/crashed/interrupted），白盒与组合行显示；correlation / blackbox 行不显示（对齐后端）。
- 点击 → `GET resume-preview` → 确认弹窗摘要：
  - 可跳过时："已完成 3 项（pre-recon / recon / injection-vuln），将从 xss-vuln 继续" + 步骤缓存命中条数（≤2）；
  - 无可跳过时："无可跳过部分，将从头扫描"（续跑仍可用，等同原 resume 语义）；
  - `resumable:false` 时展示原因（产物缺失→引导重跑；仍在运行→提示等待）。
- 确认后 `POST .../resume`（现有 API，语义已升级），成功跳转详情 live tab。
- "重跑"按钮保留不动（新建 scan 语义），与"续跑"并存。

**详情页（`ScanDetail`）**：非 completed 状态且非 blackbox/correlation 行时新增"断点详情"区块：
- agent 完成状态列表：✅ 已完成（completed_agents）/ ▶ 将从此继续（interrupted_agent）/ ⏳ 未跑到；
- 步骤缓存状态（done/stale/missing 简表，仅 2 步）；
- warnings 逐条展示；
- 续跑按钮（同一确认流）。

**i18n**：zh/en 词条约 10-15 条（`workspaceDetail.scans.resume*` 命名空间沿用扩展）。

### 4.7 错误处理汇总

| 场景 | 行为 |
|---|---|
| G∧¬F 产物丢失（builder abort） | resume → 422 带 abort_reason；preview → `resumable:false`；前端引导用重跑 |
| cancelled 等状态但心跳新鲜 | 422 "仍在运行，无需恢复" |
| step marker 损坏 / 输入缺失 | `should_skip` 返 False → 该步骤重跑（fail-open） |
| 段 3 链式五步 | 无缓存，resume 时从 merge 起整链重跑（显式取舍 §7.7；merge 幂等 §4.4 保证重跑干净） |
| resume 提交 Temporal 失败 | 既有 `_mark_submission_failed` 路径不变 |
| 并发上限 | 既有 `TooManyScans` 不变 |
| 重复点击 | 前端 busy 态 + 后端 running 校验双保险 |

## 5. 测试策略（TDD）

1. **step_cache 单测**（新 `packages/whitebox/tests/test_step_cache.py`）：指纹一致跳过并还原快照 / 输入 mtime 变化不跳 / 输入缺失不跳 / outputs 任一缺失不跳（产物不全→重跑）/ marker 损坏不跳 / 无 marker 不跳 / ret=None 步骤往返。
2. **activity 接线测试**：2 个 activity 各 1 条——预置 marker+输入 → 断言 LLM runner 未被调用、返回缓存值（mock runner 断言 not called）；**failed 语义 2 条**——chain-verdict 返 `failed_classes` 非空 → 未写 marker、authz-judge 返 `failed=True` → 未写 marker（resume 会重试）。
3. **merge 幂等单测（新，`test_merge_dual_track*` 扩展）**：already_merged 路由备份（mock 断言输入取自 `{vc}_llm_queue.json`）/ 无标走原件并写备份 / **半途中断按类分流**（inj 带标走备份、xss 无标走原件）/ **备份只首跑写**（重跑路径断言备份未被覆盖）/ 首跑行为零回归（无备份文件时与现行为完全一致）。
4. **scan_manager 测试**（扩展 `test_scan_manager*` 既有文件）：resume 白盒行传 `resume_completed_agents` 进 PipelineInput / abort → ValueError / 新状态集放行（failed/cancelled/killed）/ 心跳新鲜拒绝 / 组合白盒段同通路 / cleanup 被调用删半成品。
5. **API 测试**（`packages/web/tests/`）：resume-preview 响应形状（steps 仅 2 步）/ abort 映射 / correlation 与 blackbox 的 `resumable:false` / resume 状态校验放开。
6. **前端测试**：按钮显示条件矩阵（failed/cancelled/killed/interrupted 显示，completed/blackbox/correlation 不显示）/ 确认弹窗摘要渲染 / 断点详情区块渲染。
7. **人工冒烟（v2 修订场景）**：
   - **主场景**：NodeGoat 跑到 **report_polish 窗口内 kill**（最常见中断点）→ web 续跑 → 断言：vuln agents 无重跑日志（agent 级跳过生效）、chain-verdict/authz 日志显示 skip（缓存生效）、**merge 从 `{vc}_llm_queue.json` 备份重建——产物无二次合并**（merge_source 分布与首跑一致、per_class llm 计数不虚高、备份文件保持原始件）、gn-enrich/endpoint/attack-chain/report-polish 重跑、终态 completed。
   - 简版：recon 完成后 kill → 续跑 → 全链无缓存场景下 agent 跳过 + 正常完成（原 v1 场景，保留）。

## 6. 改动清单

| 文件 | 改动 |
|---|---|
| `packages/web/src/supernova_web/components/scan_manager.py` | `_RESUMABLE_STATUSES` 扩集 + 判活；resume 白盒/组合两入口接 builder+cleanup；`_submit_whitebox` 加 `resume_completed_agents` 透传；InfoEvent 写续跑摘要 |
| `packages/web/src/supernova_web/api/scans.py` | 新增 `GET .../resume-preview`；`resume_scan` 状态校验放开 |
| `packages/whitebox/src/supernova_whitebox/pipeline/step_cache.py` 🆕 | `mark_done` / `should_skip`（marker+指纹+outputs 清单+快照，干净完成才打点） |
| `packages/whitebox/src/supernova_whitebox/pipeline/activities.py` | **2 个** activity 接自检（各 +6-10 行；`failed_classes`/`failed=True` 不打点）+ **merge 幂等修复**（~10 行，L981-1059 区：merge_source 判别路由 + 备份只首跑写） |
| `packages/web/frontend/src/routes/WorkspaceDetail/ScanList.tsx` | "恢复"→"续跑"（条件+确认弹窗带 preview 摘要） |
| `packages/web/frontend/src/routes/WorkspaceDetail/ScanDetail.tsx`（或子组件 🆕） | 断点详情区块 + 续跑按钮 |
| `packages/web/frontend/src/api/client.ts` | `getResumePreview()` |
| zh/en i18n 词条 | 续跑相关 ~10-15 条 |
| 测试文件（§5） | 新增/扩展 |

**不动的部分**：workflow 编排（`workflows.py` 零改动——守卫已存在）、CLI resume（`whitebox/worker.py` 零改动）、黑盒 rerun（机制 B）、correlation 分支、重跑按钮（机制 A）、**段 3 五步不接缓存**（仅 merge 输入判别 ~10 行）。

## 7. 风险登记

1. **指纹误匹配**（mtime+size 碰撞）：概率极低（atomic_write 的 tmp+rename 使 mtime_ns 变化）；后果=跳过一次本应重跑的步骤，可接受；兜底：删除 `.step-cache/` 目录即强制全重跑。
1b. **"产物存在≠完整"防线**（范围缩到 2 步后依然成立）：跳过信号是 marker（成功末尾显式记录）而非业务产物存在性——中途失败必无 marker → 重跑；残缺文件被 atomic_write 原子性排除；产物缺失去重于 outputs 校验；干净完成才打点（§4.3）排除 failed 语义误标记。残余风险只剩"产物在盘且 JSON 合法但内容语义损坏（未被重写、指纹未变）"——等同外部磁盘静默损坏，agent 级对账（git G 信号）同样无法防御，接受；兜底同上。
2. **builder abort 语义在 web 的呈现**：G∧¬F 中止曾只存在于 CLI（人读 stderr）；web 需把 abort_reason 透传到 422 与 preview，前端要有明确引导（本设计 §4.5/§4.7 已覆盖）。
3. **组合扫描白盒段的 repo_path 解析**：resume 组合分支现从 session 读 `repo_path`（L600），builder 需要同一值定位 deliverables git 仓——路径即 scan_dir 下的 deliverables/whitebox，不依赖 repo_path 本身（G 信号读 deliverables git 仓，`GitManager.get_completed_agents(deliverables)`）；plan 阶段核实组合行 deliverables 路径一致性。
4. **step cache 与 retry 交互**：activity 失败重试（Temporal retry policy）时 marker 未写（`mark_done` 仅干净完成末尾）→ 重试正常重跑，无脏 marker。activity 成功但结果回传丢失触发重试时，marker 已写 → 重试直接命中缓存返回快照（幂等，反而省一次重跑）。
5. **老 session 兼容**：旧扫描目录无 `.step-cache/` → 全部 missing → 正常重跑；无 deliverables git 仓的更老目录 → G 集空 → 全部重跑（现 builder 语义）。旧 session 无 `{vc}_llm_queue.json` 备份 → merge 判别走"无标原件"路径，与现行为一致。
6. **`_watch` 与续跑后事件流**：`_strip_trailing_scan_end` 已处理旧 scan_end；resume 后 live tab 续流为既有机制，零新增。
7. **段 3 整链重跑的成本取舍（v2 显式记录）**：merge / gn-enrich / endpoint-enrich / attack-chain / report-polish 每次 resume 从 merge 起全部重跑——量级随扫描规模增长（merge ~1-2min + gn-enrich 0-10min（≤3 个多轮 agent，无 GN-only 卡则跳过）+ endpoint 5 类并行（墙钟=最慢类）+ attack-chain 单 agent + report-polish 20-30min 含 QA 回炉）：少发现扫描 ~5-15min、多卡片扫描几十分钟墙钟 + 全额 token。**接受该权衡以换取零链式缓存复杂度**；将来大扫描续跑慢属已知取舍而非 bug。若要优化：merge/enrich 改写独立产物文件（数据流 DAG 化）或 git `step:` commit 信号（天然区分"上游重写"与"下游消费"），均非本期范围。
8. **merge 幂等依赖 merge_source 判别假设**：LLM 轨 agent 产的卡不带 `merge_source` 字段（queue schema 中该字段由 merger 设置）。plan 阶段用真实 agent 产物验证此假设并加回归测试；若假设被打破（agent 也会产该字段），判别降级为"备份存在 ∧ exploitation 与备份内容一致 → 视为已合并"兜底。
9. **failed_classes 不打点的代价**：resume 重付 chain-verdict 全额窗口（27 链串行 15-20min，并发后 ÷N）；接受——resume 语义="再试一次"，且关轨 fail-fast 场景下打点会让续跑用缓存返回值原地复活失败（§4.3），得不偿失。

## 8. 实施顺序（writing-plans 细化）

Phase 1 — **merge 幂等修复**（先行：独立可测、CLI 立即受益、Phase 2 硬前置）+ step cache 模块 + 2 activity 接线（纯 whitebox）
Phase 2 — web 状态集扩展 + resume 接通 builder（真续跑核心；前置 = Phase 1 merge 幂等已合入）
Phase 3 — resume-preview API + 前端两处入口 + 断点详情
每 Phase TDD + 回归（只跑相关测试文件，勿全量——项目预存挂起）。
