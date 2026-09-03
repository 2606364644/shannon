# MR 增量扫描（合并请求 diff 扫描）设计

> 状态：📐 设计定稿（2026-09-03 brainstorming 三段确认）
> 主题：`scan_type="mr"` 增量白盒扫描——按 base..head diff 收窄双轨检测范围，覆盖新增代码引入漏洞、新增入口点接通新攻击面、删除安全防护的链路影响分析三类增量风险。

---

## 1. 背景与目标

现有扫描只支持全量（永远扫仓库当前 checkout 状态，`ScanRequest` 无 branch/commit 字段，全仓无 diff / commit-range / 增量概念）。本设计新增 MR（合并请求）扫描：给定 base_ref / head_ref，只对变更区域做安全检测，服务 MR 合并前风险把关的高频场景。

三个检测子方向（对应需求原文）：

| 子方向 | 语义 |
|---|---|
| A · 新增代码引入漏洞 | 新增/改写代码里的新 sink、新 source、被改写的污点传播路径 |
| B · 新增入口点 = 新攻击面 | 新增路由/handler 等入口点的**全部**链路都视为增量攻击面（含接通原死代码、绕过既有防护规则的链路——不严格判定 base 侧可达性） |
| C · 删除安全防护的链路影响 | diff 删除行里删掉的防护（sanitize/authz 检查/输入校验等）→ 定位所在函数 → 提取该函数涉及的所有链路重新判定 |

## 2. 已确认的关键决策（brainstorming 定稿）

1. **触发方式**：Web 手动发起起步（repo + base_ref/head_ref），webhook 自动触发为可能的二期，不在本 spec 范围。
2. **base 数据源：轻量单索引**。只对 head 建一次确定性索引（复用 `run_code_index`）。不做双索引严格对比；来源 B 采用「新入口的全部链路 = 新攻击面」语义，无需 base 索引。
3. **LLM 轨档位：默认开 + 按 diff 特征选 vuln 类**（如 diff 不动前端则不跑 xss）。双轨 OR 语义完整保留。
4. **删防护识别：纯 LLM 判定**（diff 删除 hunk 喂轻量 LLM 判「删的是否防护」），零规则维护。不扩展 `sanitizer_library` 规则表。
5. **产物呈现：`scan_type="mr"` + 复用现有报告结构 + `trigger_source` 来源标注 + 报告顶部增量摘要段**。不做独立 MR 视角报告模板。
6. **pipeline 接入：方案 X——独立 `MrScanWorkflow` + 复用现有 activities 库**。`WhiteboxScanWorkflow` 零改动，全量扫描零回归风险；与 `BlackboxScanWorkflow` / `CorrelationScanWorkflow` 多 workflow 并存的架构风格一致。

## 3. 总体架构

### 3.1 MrScanWorkflow 编排

```
MrScanWorkflow.run(input: MrPipelineInput)
│
├─ 1. run_preflight / run_credential_check          〔复用〕
├─ 2. run_mr_repo_prepare                  〔新〕fetch head_ref → checkout head，
│                                          解析 merge-base(base_ref, head) → base_commit
├─ 3. run_git_diff                         〔新〕git diff -U3 base..head → DiffManifest；
│                                          unified diff 全文落盘 deliverables（LLM 各步引用）
│
├─ 4. 并行（现有双发结构不变）：
│     ├─ run_code_index                    〔复用〕head 工作树确定性索引
│     ├─ run_agent(PRE_RECON)              〔复用〕+ 增量引导段（§5.2）
│     └─ run_protection_removal_analysis   〔新〕删除 hunk → 轻量 LLM 判定 → RemovedProtection[]
│
├─ 5. merge_sink_reports / entry_point_fusion / framework   〔复用〕
├─ 6. run_incremental_scope                〔新〕DiffManifest + code_index + entry_points
│                                          + RemovedProtection → IncrementalScope（§4.2-4.5）
├─ 7. run_agent(RECON) → risk_scoring      〔复用〕
│
├─ 8. run_vuln_agent × N                   〔复用〕N = IncrementalScope.selected_vuln_classes，
│                                          vuln prompt 增量引导段（§5.2）
├─ 9. run_authz_gitnexus_judge             〔复用〕IDOR 候选按增量范围收窄
├─ 10. run_gitnexus_chain_verdict          〔复用〕候选 = IncrementalScope 增量 flow 子集；
│                                          容量窗口按「增量链数 ÷ 并发 × 单链耗时」重估（容量铁律）
├─ 11. run_merge_dual_track_queues          〔复用〕verdict OR 不变
└─ 12. assemble_report → polish → export    〔复用+小改〕增量摘要段 + trigger_source 标注
```

**pre-recon / recon 照跑不裁剪**——它们是 LLM 轨的输入（角色模型、工作流、接口清单），砍了 LLM 轨失明。增量收窄只发生在四处：vuln 类选择、GitNexus 轨候选预过滤、verdict 容量窗口、LLM 轨 prompt 增量引导段。

**web 零 agent 执行点约束（CLAUDE.md 2026-09-03）天然满足**：所有 LLM 步骤（含删防护判定）均为 temporal activity 在 worker 容器跑，web 只是 workflow 提交者。

### 3.2 scan 模型与 repo 状态

- web `ScanRequest.type` 加 `"mr"`：repo 必选 + 新字段 `base_ref` / `head_ref`（分支名或 commit sha）。
- `scan_type="mr"` 落 session.json；scan_id 形如 `<repo>-mr-YYYYMMDD-HHMMSS`。
- repo 由 scan_manager 提交 workflow 前 checkout 到 head_ref（复用 `repo_manager.checkout()`：fetch origin <ref> + checkout）；扫完停在 head_ref（对齐现有 checkout 持久切换行为）。
- `MrPipelineInput`（whitebox `pipeline/shared.py`）：现有 `PipelineInput` 字段全保留 + `base_ref` / `head_ref` + 解析后回填 `base_commit`。
- diff 用 **merge-base 语义**（三点 diff）：`git diff -U3 <merge-base>..<head>`。

## 4. 核心数据结构与增量范围合成

### 4.1 数据结构（新增，core 模型层）

```python
class DiffLine:    # head_line_no | base_line_no（int）+ text
class DiffHunk:    # file_path, old_start/old_lines, new_start/new_lines,
                   # added: list[DiffLine], removed: list[DiffLine],
                   # is_new_file / is_deleted_file / rename(old_path→new_path)
class DiffManifest:
    base_commit, head_commit, hunks: list[DiffHunk],
    stats: {files, insertions, deletions}
    # helper：added_line_set(file_path) → set[head 侧行号]；rename 映射查询

class RemovedProtection:      # LLM 判定产物
    file_path, base_line_no, removed_text,
    function_name: str | None,     # LLM 从 hunk 上下文推断
    protection_kind: str,          # sanitize / authz_check / input_validation / csrf / rate_limit / ...
    rationale: str, confidence: float

class IncrementalScope:
    selected_vuln_classes: list[str]
    new_entry_point_ids: list[str]                 # 来源 B（报告呈现用）
    verdict_flow_ids: set[str]                     # 三来源 flow_id 并集（GitNexus 轨过滤集）
    removed_protection_flows: list[{protection, flow_ids, func_block_ids}]  # 来源 C（报告呈现用）
```

### 4.2 来源 A · 新增代码引入漏洞

过滤 `parameter_graph.taint_flows`，命中任一即入 `verdict_flow_ids`：
- `sink_call_site` 的 `(file_path, line)` ∈ 该文件 added 行集合（新增 sink 调用）；
- 任一 `propagation_step.code_location` 落在 added 行（传播路径被改写）；
- `source_points` 的 `(file_path, line)` 落在 added 行（新增取参/source）。

坐标系：unified diff added 行号与 head 索引 `SinkCallSite.line` 同为 **head 侧**，直接比对，零映射。

### 4.3 来源 B · 新增入口点 = 新攻击面

- head 索引 `EntryPoint`，其 `func_block_id` 对应 `FuncBlock` 的行范围与 added 行集合相交（新增路由/handler/消费者），或所在文件为新文件；
- 命中 entry 的**全部** taint_flows 入集（已确认语义：新入口的一切链路都是增量攻击面，不判定 base 可达性）；
- entry 记入 `new_entry_point_ids`，报告呈现新增攻击面明细（route/method/authentication）。

### 4.4 来源 C · 删除防护的反向链

**第一步 · 函数定位**（`RemovedProtection` → head 索引 `FuncBlock`）：
1. 优先 `(file_path, function_name)` 精确匹配（file_path 经 DiffManifest rename 映射表转到新路径）；
2. 兜底 hunk 区间线性映射：`base_line_no` 经 hunk `old_start↔new_start` 映射到 head 侧行号，落进哪个 FuncBlock 行范围即哪个；
3. 函数整体被删（head 无匹配）：**不追链**（函数没了链即断），`followed_by_chains=false` 记入报告供人审。

**第二步 · 反向链提取两级**（复用现有原语）：
- 直接级：`propagation_steps` 任一 step 的 `from/to_func_id` 命中该 FuncBlock 的 flows；
- 扩展级：head 索引 `CallChain.path` 含该 FuncBlock 的调用链 → 其 `entry_point_id` 的全部 flows（防防护函数不在 taint 主路径、仅在调用链路上的漏网）；
- 这些 flow **重新走 verdict**——基线时因有防护被判安全的链，防护删除后结论可能翻转。

### 4.5 vuln 类选择启发式（确定性小表，非 prompt）

diff 触及后端路由/controller → inj/ssrf/authz（有服务端渲染加 xss）；触及前端渲染组件 → xss；触及 auth/权限路径 → auth/authz；判不准 → 全类。硬编码起步，后续可配置化。

## 5. 双轨增量接入（铁律合规）

### 5.1 GitNexus 轨

- `extract_candidate_chains` 前按 `IncrementalScope.verdict_flow_ids` 预过滤 `taint_flows`；
- 候选数收窄后，`run_gitnexus_chain_verdict` 的 `start_to_close_timeout` 按「增量链数 ÷ 并发 × 单链耗时」重估（容量铁律；增量链数远小于全量，窗口自然小）；
- `run_authz_gitnexus_judge` 的 IDOR 候选按增量范围收窄；
- `run_protection_removal_analysis` 是 GitNexus 轨侧组成步骤（产物只流向 `IncrementalScope`），复用 `run_claude_prompt` 抽象，temporal activity 在 worker 跑；LLM 失败/超时**降级不阻塞**：跳过来源 C 自动追链，报告标注「防护删除分析降级」，来源 A/B 照常。

### 5.2 LLM 轨

- vuln prompt 渲染时追加**增量引导段**：告知本次为 MR 扫描（base..head）、unified diff 落盘产物路径、可用 `git diff` 自查，聚焦新增/修改/删除区域。**不贴 diff 全文**——大 diff 由 agent 自主读文件（对齐 TS 自给自足精神）。pre-recon 同样带增量引导段。
- **铁律声明**：LLM 轨拿到的增量信息**只有 git 派生物**（diff 文本/路径/ref 引用）。`IncrementalScope`、filtered flows、`RemovedProtection` 等 GitNexus 轨 / diff 分析轨产物**一概不进 LLM 轨 prompt**。两轨各自独立处理 diff，OR 合并语义不变。新增锁定测试（对齐 `test_static_dataflow_hints_decoupling.py` 模式）。

## 6. 报告层变更（向后兼容，全量扫描零感知）

**Schema（`ReportData`）**：
- `scan` 加可选 MR 元信息：`base_commit` / `head_commit` / `diff_stat`；
- 新顶层可选段 `incremental_summary`（仅 MR 扫描）：`new_entry_points[]`（route/method/authentication/函数）、`removed_protections[]`（file/line/kind/rationale/`followed_by_chains`）、受影响链计数 + 三来源分布；
- 每条 vulnerability 加可选 `trigger_source: "new_code" | "new_entry" | "removed_protection"`——**只标 GitNexus 轨可归因发现**（flow 命中哪个来源过滤集即标哪个；一 flow 多来源时按 C > B > A 优先级取一）；LLM 轨产物不标。

**前端**：
- `ReportView` 顶部新组件 `MrIncrementalSummary`：三统计卡（新增入口/删除防护/受影响链）+ 明细折叠区；
- 漏洞卡片 `trigger_source` 徽章（沿用 gutter 结构信号色语言，对齐 live 视觉约定）;
- `ScanNewPage` 加 MR 类型（repo 复用 `RepoCombobox`；base_ref/head_ref 复用 `BranchCombobox`，允许手输 commit sha）；
- `ScanList` 显示 mr 类型 + `base..head` 标识。

## 7. 错误处理与边界

| 场景 | 行为 |
|---|---|
| head_ref/base_ref fetch 失败或解析不到 | repo prepare **fail-fast**，扫描不开始，web 返回明确错误 |
| 无 merge-base（不相关历史） | fail-fast |
| diff 为空（base==head） | 快速终态：产「无变更」报告（含 ref 信息），**不跑双轨** |
| 文件 rename | DiffManifest 记 rename 映射；added 行判定用新路径；RemovedProtection.file_path 经映射转新路径再匹配 |
| 整文件删除 | 来源 A/B 无产出；来源 C 照判——函数没了不追链，`followed_by_chains=false` 记录供人审 |
| 超大 diff | 无硬限制；链窗口按实际增量链数算，自然应对；stats 呈现规模 |
| 防护判定 LLM 失败/超时 | 来源 C 降级不阻塞（A/B 照常），报告标注降级 |
| `SUPERNOVA_LLM_TRACK_ENABLED=0` | MR 照常——GitNexus 轨兜底语义不变（仅关 inj/xss/ssrf vuln agent 的收窄语义照旧） |
| 同 repo 并发 MR 扫描互相 checkout 干扰 | 已知限制：提交前校验同 repo 无进行中扫描，冲突则拒绝（实施时核对现有并发治理现状再收口） |

## 8. 测试策略（TDD）

- **单元（core，纯函数为主）**：DiffManifest 解析（真实 `git diff -U3` fixture：普通/rename/新文件/删文件）；added 行集合判定；三来源过滤（fixture code_index + parameter_graph）；RemovedProtection 函数定位三分支（函数名命中/区间映射兜底/函数被删）；vuln 类启发式。
- **铁律锁定**：MR 增量引导段不含任何确定性产物（新建测试，对齐 `test_static_dataflow_hints_decoupling.py` 模式）；`trigger_source` 只出现在 GitNexus 轨产物。
- **workflow 级（whitebox）**：MrScanWorkflow 编排测试（activity mock + pipeline_testing_mode）。
- **web**：`type="mr"` 参数校验 / `scan_type` 落盘 / repo checkout 提交前钩子。
- **E2E 冒烟**：NodeGoat 造 MR fixture——base 为带 sanitize/无新路由版本，head 删 sanitize + 加新路由 → 验证来源 A/B/C 各出至少一条 `trigger_source` 标注正确的发现 + 增量摘要段完整。
- **前端**：vitest（MrIncrementalSummary 渲染 / MR 表单校验）；提交前 `npx tsc -b`。
- 遵守 CLAUDE.md 测试纪律：只跑改动相关测试文件，勿广跑全套（预存挂起/失败）。

## 9. 实施切分（writing-plans 骨架）

1. core：diff 解析 + 数据模型（DiffManifest / RemovedProtection / IncrementalScope）
2. core：增量范围合成三来源（纯函数）+ 函数定位 + 反向链提取
3. core：删防护 LLM 判定 prompt + 封装 + 降级路径
4. whitebox：MrPipelineInput + MrScanWorkflow + 新 activities（run_mr_repo_prepare / run_git_diff / run_protection_removal_analysis / run_incremental_scope）+ prompt 增量引导段机制 + 容量窗口重估
5. web + 前端：API（type=mr）/ scan_manager 提交 + repo checkout / ScanNewPage 表单 / ScanList
6. 报告层：ReportData 增量段 + trigger_source + MrIncrementalSummary 等前端呈现
7. E2E 冒烟 + 铁律锁定测试

## 10. 二期展望（不在本 spec 范围）

- GitLab webhook 自动触发（MR open/update 回调 → 解析 base/head → 自动扫描）：需 webhook 接收器、token 鉴权、防重入，待手动形态验证后立项。
- 双索引严格对比（git worktree 隔离对 base 建索引，严格判定「base 不可达 → head 可达」的死代码复活）：来源 B 语义的升级路径，`DiffManifest` 已预留 base_commit 字段。
- 复用历史全量扫描产物做 base 对比（repo-snapshot commit 严格匹配时免费开严格对比）。
