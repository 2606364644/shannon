# MR 增量扫描——实施进度交接（2026-09-03 晚）

> 状态：📐 设计定稿 + 核心已落地，**剩余工作待续**（本人暂停，交交接）。
> spec：`docs/superpowers/specs/2026-09-03-mr-incremental-scan-design.md`（已提交 `ac359c72`）
> 主题索引：`docs/superpowers/README.md`「MR 增量扫描」节（已提交 `0900a404`）
> 分支：`feat/fork-py`

---

## 0. 一句话状态

**core 三层（diff 解析 / 增量范围合成 / 删防护判定）+ whitebox 前置 activities + 全量 workflow 消费点接线已提交；web 后端 API/models/scan_manager 与 worker 注册已改（待提交）；前端 MR 表单渲染分支未写；报告层（#12）与 E2E/铁律锁定（#13）未开始。**

---

## 1. 已提交（git log 按序）

| commit | 内容 | 测试 |
|---|---|---|
| `338716cd` | core `mr_scan.diff_manifest`：git diff -U3 解析（hunk 行号坐标系/新删文件 flag/rename 归一/added_line_set） | 6 单元 + 真实 git 契约测试 ✅ |
| `924985a7` | core `mr_scan.incremental_scope`：三来源合成（A 新增代码/B 新入口全链/C 删防护两级反向链+函数定位三分支）+ `select_vuln_classes` 启发式 | 10 测试 ✅ |
| `8de038d0` | core `mr_scan.protection_removal`：删防护轻量 LLM 判定（内联 prompt/schema/LLMClient 注入），失败·超时·垃圾三态降级 | 5 测试 ✅ |
| `1cdc3ded` | whitebox `mr_activities`（repo_prepare/git_diff/protection_removal/incremental_scope）+ `mr_wiring` 纯函数（vuln 类优先级/增量引导段/verdict 过滤/scope 读）+ executor `prompt_suffix` 通道 + GN verdict 按 scope 预过滤 | whitebox 6+4 测试、core executor 2 测试 ✅ |

**全部已提交测试均绿**（`packages/core/tests/mr_scan/`、`packages/whitebox/tests/pipeline/test_mr_*`、`packages/core/tests/agents/test_executor_prompt_suffix.py`）。

---

## 2. 工作区未提交改动（半成品，勿忘）

`git status` 显示以下 `M`（全部未提交）：

| 文件 | 改了什么 | 完成度 |
|---|---|---|
| `packages/whitebox/.../pipeline/workflows.py` | `MrScanWorkflow` 类（前置 3 activities → child `WhiteboxScanWorkflow`）+ 全量 workflow 消费点（vuln 类 MR 启发式优先 / act_input 灌 mr_meta / pre-recon 后插 `run_incremental_scope`） | 已写，**语法+import 验证过**，**无 workflow 级测试** |
| `packages/worker/src/supernova_worker/runner.py` | wb_worker 注册 `MrScanWorkflow` + 4 个 MR activities | 已写，**未验证注册加载** |
| `packages/web/src/supernova_web/models.py` | `ScanRequest.type` 加 `"mr"` + `base_ref`/`head_ref` + validator | ✅ 测试绿 |
| `packages/web/src/supernova_web/components/scan_manager.py` | import `MrScanWorkflow` + `_submit_mr` + `start()` 的 mr 分发分支 | 已写，**无单测** |
| `packages/web/tests/test_models_repo_source.py` | MR validator 3 测试 | ✅ 绿 |
| `packages/web/tests/test_api_scan_repo_source.py` | MR 422 两测试 | ✅ 绿 |
| `packages/web/frontend/src/api/types.ts` | `ScanRequest.type` 加 mr + `base_ref`/`head_ref` | ✅ |
| `packages/web/frontend/src/locales/en.json` + `zh.json` | MR i18n 键（type.mr / subtitleMr / refs / selectRefs） | ✅ |
| `packages/web/frontend/src/pages/ScanNewPage.tsx` | `ScanType` 加 mr / `FormState` 加 refs / `buildBody` mr 分支 / 类型切换数组 / isValid / 文案分支 | ⚠️ **缺 MR 表单渲染分支**（见 §4.1） |

---

## 3. 剩余任务清单

### #11（续）web + 前端收尾

#### 3.1 ⚠️ ScanNewPage MR 表单渲染分支（唯一编译不报错但运行会落错分支的缺口）

`packages/web/frontend/src/pages/ScanNewPage.tsx` 表单区（约 L645）现在只有三支：
```tsx
{type === "whitebox" ? ( <ScanFormFields .../> )
 : corrMode === "auto" ? ( <CorrelationTopologyFields .../> )
 : ( <CorrelationFormFields .../> )}
```
`type === "mr"` 时 `corrMode === "auto"` 恒真 → 会错误渲染跨仓拓扑表单。**需加 `type === "mr"` 优先分支**，内联最小表单：

- 工作区下拉（复刻 `ScanFormFields.tsx` L873-895 的 `wsSelectInner`：`Select value={workspace}` + `wsList`/`wsLoading`，i18n 键 `scan.fields.wsSelectLabel` / `scan.fields.wsSelectPlaceholder` / `scan.fields.wsEmptyOption` 已存在）；
- 仓库选择（`RepoCombobox`，`useRepos(workspace)` 已有 import，i18n 键 `scan.repo.selectPlaceholder` 等已存在）；
- 两个文本输入：`base_ref` / `head_ref`，绑定 `f.mrBaseRef` / `f.mrHeadRef`，i18n 键 `scan.mrBaseRef` / `scan.mrHeadRef`（已注入）；
- 错误提示：`mrRefsErr`（`scan.errors.selectRefs`）与 `sourceErr` 已接好。

参考已有 i18n 键确认：`grep scan.mr packages/web/frontend/src/locales/zh.json`。

#### 3.2 ScanList mr 标识

`ScanList.tsx` 现在按 `scan_type` 渲染类型标签。加 `scan_type === "mr"` 时显示「MR」徽标 + `base..head`（`session.json` 里 `_submit_mr` 已写 `mr_base_ref`/`mr_head_ref`，`_scan_detail` 需透出或用现有字段）。规格：`specs/...-design.md` §6 前端。

#### 3.3 workflow 级测试（TDD 缺口，建议补）

- `MrScanWorkflow` 编排测试：mock 前置 3 activities + child，验证 mr_meta 穿线（`event_file`/`provider_config` 透传、`selected_vuln_classes` 来自 diff_result）。参照 `packages/whitebox/tests/pipeline/test_attack_chain_workflow.py` 的 Temporal test-env 模式。
- 空 diff 快速终态（spec §7：diff 为空不跑双轨）：当前实现**未做**——`MrScanWorkflow` 无条件跑 child；child 里 `selected_vuln_classes=[]` 会让 vuln agents 不 fan-out，但 pre-recon/recon 仍跑。若严格执行 spec 需在 `run_git_diff` 返回空 stats 时短路（产空报告）。**决定后再实现**。

### #12 报告层（未开始）

- `ReportData`（`packages/core/src/supernova_core/models/report_data.py`）：`scan` 加可选 `base_commit`/`head_commit`/`diff_stat`；新顶层可选段 `incremental_summary`（`new_entry_points[]`/`removed_protections[]`/三来源分布）；每条 vulnerability 加可选 `trigger_source`（`new_code`|`new_entry`|`removed_protection`，**只标 GitNexus 轨可归因发现**，C>B>A 归并）。
- `assemble_report`（`packages/whitebox/.../activities.py` L2053）读 `intermediate/mr/*.json` → report_data 增量段。
- **`trigger_source` 打标时机**：`run_gitnexus_chain_verdict` 产出 `<vuln>_gitnexus_queue.json` 时按 flow 命中哪个来源过滤集打标（`IncrementalScope` 需携带 flow→来源映射，当前 `incremental_scope.json` 只有 `verdict_flow_ids` 平铺——**需在 scope 模型加来源明细或打标时从三来源集合反查**）。
- 前端 `MrIncrementalSummary` 组件 + 漏洞卡片徽章。

### #13 E2E 冒烟 + 铁律锁定（未开始）

- NodeGoat MR fixture：base 为带 sanitize/无新路由，head 删 sanitize + 加新路由 → 验证来源 A/B/C 各出标注正确的发现 + 增量摘要段。
- 铁律锁定测试：`build_mr_incremental_guidance` 输出不含确定性产物字段（**已测**，见 `test_mr_workflow_wiring.py`）；补：`run_vuln_agent` 的 prompt_suffix 全链路不掺 scope 产物。
- 前端 vitest + `npx tsc -b`。

---

## 4. 关键实现笔记（续做时必读）

### 4.1 架构决策回顾
- **child workflow 复用**（对 spec 方案 X 的实现层细化）：`MrScanWorkflow` = 前置 3 activities（repo_prepare → git_diff → protection_removal，串行）+ `workflow.execute_child_workflow(WhiteboxScanWorkflow.run, child_input)`；全量 workflow 只加可选消费点（mr_meta=None 零行为变化）。
- **增量产物落盘**：`deliverables/whitebox/intermediate/mr/{diff_manifest.json, diff.patch, removed_protections.json, incremental_scope.json}`（`mr_activities._mr_dir`）。
- **双轨合规**：LLM 轨增量信息 = 只有 `build_mr_incremental_guidance(mr_meta)`（git 派生：ref/路径/命令提示），经 executor `prompt_suffix` 尾拼；GN 轨按 scope 过滤在 `run_gitnexus_chain_verdict` 内 `filter_flows_by_mr_scope`。

### 4.2 容量铁律未兑现项
GN verdict 窗口在 workflows.py 仍固定 15min（增量链数少时自然快，不会撞窗，但 spec §5.1 承诺「按增量链数重估」）。`run_incremental_scope` 返回 `verdict_flow_count`，child 可在 `mr_meta` 拿到——**若要兑现，需在 GN verdict 前算窗口**（链数 ÷ 并发 × 60s 上界，下限 5min）。非必须，标注决定。

### 4.3 已知边界/后续
- 同 repo 并发 MR 扫描 checkout 互斥（spec §7 承诺「提交前校验」）：**未实现**，当前靠现有并发治理兜底。
- `_submit_mr` 未走 `resume` 路径（MR 扫描 resume 语义后续定）。

---

## 5. 建议的续做顺序

1. **ScanNewPage MR 表单渲染分支**（§3.1）——不补则运行错分支。
2. 提交工作区半成品（分两笔：whitebox+worker 一笔，web+前端一笔；或一笔也可）。
3. #12 报告层（含 scope 来源明细的模型小改）。
4. #13 E2E 冒烟 + 铁律锁定 + 前端 tsc。
5. 按需补 §3.3 workflow 级测试与 §4.2 容量窗口。

> 测试纪律（CLAUDE.md）：只跑改动相关测试文件，勿广跑全套。改动 mr 相关后跑：
> `packages/core/tests/mr_scan/` + `packages/whitebox/tests/pipeline/test_mr_*` + `packages/web/tests/test_models_repo_source.py` + `test_api_scan_repo_source.py` + `packages/core/tests/agents/test_executor_prompt_suffix.py`。
