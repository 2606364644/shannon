# 产物分层（deliverables tiering）+ WEB 产物页过滤 设计

> 日期：2026-08-18
> 状态：设计定稿（brainstorming + 全量代码核查产出，待 writing-plans 拆任务）
> 主题：把 deliverables 桶内的**中间产物与最终交付物分层**（`intermediate/` 下沉），拍平组合黑盒 run 的冗余桶层，并让 WEB 产物页**默认只展示本次任务的交付物**（中间产物折叠、归档/隐藏目录消失、大文件预览防御）。
> 关联：演进 `2026-08-14-unified-wb-bb-task-model-design.md`（三桶布局 `deliverables/{whitebox,blackbox,combined}` 与 per-run 结构**不变**，本 spec 只动桶内分层与读侧过滤）。

---

## 1. 背景与动机

三桶布局落地后，实际扫描（实证 `workspaces/__legacy__/scans/NodeGoat-20260812-024451/`）暴露两类问题：

**问题 A：桶内中间产物与交付物平铺。** 白盒桶 30+ 文件一层平铺——`code_index.json`（几十 MB）、`parameter_graph.json`、`attack_chains*.json`、`*_llm_queue.json`、`*_gitnexus_queue.json`、`rule_gap_report.json` 等管线中间文件，与 `comprehensive_security_assessment_report.md`、`*_findings.md`、`exploitable_poc_collection.md` 等最终交付物混排。

**问题 B：WEB 产物页全量 rglob 无分层过滤。** `deliverables_reader.py:60-69` 对 `scan_dir/deliverables` 递归枚举，仅排 `.git`/`__pycache__`/`schemas`：

- `.whitebox-archive/`、`.blackbox-archive/`（resume/rerun 归档）与 `.poc_checkpoint.json` 等 `.` 开头内容全部出现在列表里；
- 中间产物与交付物混排（问题 A 的直接投影），「本次任务的相关文件」淹没；
- `code_index.json` 这类几十 MB JSON 可被点开预览，全量读入浏览器会卡死。

另有两处**既有路径漂移**顺带修正：

- 黑盒 evidence md 写在桶外顶层（`executor.py:214` 收到的 `deliverables` 是无 track 后缀的根，与白盒传桶内路径的语义不一致——白盒 `whitebox/pipeline/activities.py:63-65` 拼 `WHITEBOX_SUBDIR`，黑盒 `blackbox/pipeline/activities.py:46-58` 不拼）；
- 组合黑盒 run 的产物埋 4 层（`blackbox-runs/run-K/deliverables/blackbox/`），run 目录内只有黑盒，桶名层冗余。

**用户诉求**：① 产物放置位置优化（研究方案已定：桶内分层 + run 内拍平，三桶结构不动）；② WEB 产物页只展示本次扫描任务的相关文件。

## 2. 设计决策

| 维度 | 决定 |
|---|---|
| **桶内分层** | 每桶内分两层：交付物留桶顶层，中间产物下沉 `intermediate/` |
| **分类 SSOT** | 中间产物清单常量落在 `core/models/deliverables.py`（文件名模式），core 写侧与 web 读侧共用，**不在 web 侧维护第二份黑名单** |
| **分类判据** | 给人看的安全结论 = 交付物；机器交接/管线数据 = intermediate |
| **黑盒桶拍平** | 组合 run：`run-K/deliverables/` 直接是黑盒桶根（去掉 `blackbox/` 冗余层）；纯黑盒 scan：`scan_dir/deliverables/blackbox/` **保留桶名**（三桶对称，`report_for` 零改动） |
| **写侧区分机制** | 黑盒 `activities._get_deliverables_path` 对齐白盒拼 `BLACKBOX_SUBDIR`；`BlackboxPipelineInput` 新增可选 `deliverables_track` 覆盖（组合 run 语境传空串 → 拍平，默认 `BLACKBOX_SUBDIR`） |
| **evidence md 进桶** | 黑盒 activities 传桶内路径后，`executor` 写 md/queue 的既有行自然落桶内，修正桶外顶层漂移 |
| **存量兼容** | 零迁移；读侧 fallback 链 `{track}/intermediate/{name}` → `{track}/{name}` → 老平铺 `{name}`（扩展既有 `resolve_track_deliverable` 模式） |
| **WEB 过滤** | reader 排除一切 `.` 开头条目；summary 加 `tier` 字段（新结构按目录、旧结构按 SSOT 清单兜底）；预览大文件截断；前端默认只展交付物 + 中间产物折叠分组 |
| **resume 语义** | 不受影响：deliverables git checkpoint 是整仓 commit（`deliverable: {agent}`），文件挪子目录不改 git 语义 |

## 3. 目标目录结构（终态）

```
scans/<scan_id>/                          ← 任务根（组合扫描 = 白盒运行）
├── session.json / events.ndjson / scan-config.yaml / workflow.log / agents/ / logs/
├── deliverables/whitebox/                ← 白盒桶
│   │                                      （交付物 ≈12 个文件）
│   ├── comprehensive_security_assessment_report.md
│   ├── {vc}_analysis_deliverable.md · {vc}_findings.md     （vc = inj/xss/ssrf/auth/authz）
│   ├── pre_recon_deliverable.md · recon_deliverable.md
│   ├── exploitable_poc_collection.md
│   └── intermediate/                     ← 中间产物
│       ├── code_index.json · entry_points.json · code_index_summary.md
│       ├── parameter_graph.json · attack_chains.json · attack_chains_llm_queue.json
│       ├── route_chains.json · framework_analysis.json · frontend_mapping.json
│       ├── {vc}_llm_queue.json · {vc}_gitnexus_queue.json · {vc}_exploitation_queue.json
│       ├── rule_gap_report.json · source_gap_report.json · storage_gap_report.json
│       └── gitnexus_track_status.json · audit_plan.json · .poc_checkpoint.json
├── blackbox-runs/run-K/
│   ├── session.json / events.ndjson      （run 隔离，不变）
│   └── deliverables/                     ← 黑盒桶（拍平：不再套 blackbox/）
│       ├── {vc}_exploitation_evidence.md · {vc}_findings.md
│       ├── comprehensive_security_assessment_report.md
│       └── intermediate/{vc}_exploit_verdicts.json · endpoint_verify.json
└── combined/run-K/combined_report.md     ← 不动
```

纯黑盒 scan（无组合）：`scan_dir/deliverables/blackbox/` 与上述黑盒桶同构（多一层桶名）。

CLI 路径（白盒/黑盒 `SessionManager` workspace，无 `scans/` 层）走同一份 executor/activities 代码，分层自动生效，无需单独处理。

## 4. 产物分类清单（tier SSOT）

`core/models/deliverables.py` 新增：

```python
# 文件名模式清单（fnmatch）：命中即 intermediate。core 写侧落盘与 web 读侧
# tier 判定共用；新增管线产物时在此登记，web 端零改动。
INTERMEDIATE_FILE_PATTERNS: tuple[str, ...] = (
    "code_index.json", "entry_points.json", "code_index_summary.md",
    "parameter_graph.json", "attack_chains*.json", "route_chains.json",
    "framework_analysis.json", "frontend_mapping.json",
    "*_llm_queue.json", "*_gitnexus_queue.json", "*_exploitation_queue.json",
    "*_exploit_verdicts.json", "endpoint_verify.json",
    "rule_gap_report.json", "source_gap_report.json", "storage_gap_report.json",
    "gitnexus_track_status.json", "audit_plan.json",
    ".*checkpoint*.json",
)
```

tier 判定规则：路径含 `intermediate/` 段 → intermediate（新结构权威判据）；否则按上述模式匹配 → intermediate（旧结构兜底）；其余 → deliverable。

## 5. 写侧改造（core）

1. **`utils/paths.py`**：`INTERMEDIATE_SUBDIR = "intermediate"`；`intermediate_dir(track_dir)` helper；queue/verdicts 落盘 helper（`intermediate_path(track_dir, filename)`）。
2. **`agents/executor.py`**：queue json（`:201-204`）改写 `intermediate/`；host 渲染 md（`:212-216`）留桶顶层；黑盒 verdicts（`:224-232`）改写桶内 `intermediate/`（activities 传桶内路径后 `blackbox_dir(deliverables)` 调整为 `intermediate_dir(deliverables)`）。
3. **确定性层产物写入点**（whitebox workflows 里 `code_index.json`、`parameter_graph.json`、`attack_chains*.json`、`route_chains.json`、`framework_analysis.json`、`frontend_mapping.json`、`gitnexus_track_status.json`、各 gap report、audit_plan 的落盘处）→ `intermediate/`。
4. **黑盒 activities**：`_get_deliverables_path` 拼 `BLACKBOX_SUBDIR`（对齐白盒语义：executor 收到桶内路径）；`BlackboxPipelineInput` 新增 `deliverables_track` 可选字段（组合 run 传空串拍平）。
5. **`.poc_checkpoint.json`**（PoC 断点续跑 checkpoint）→ `intermediate/`。

`GitManager.ensure_repository` / `commit`（`git_manager.py:67-227`）不动：中间产物仍在同一 git 仓内，`deliverable: {agent}` commit 与 `get_completed_agents` 的 resume 完成信号语义不变。

## 6. 读侧兼容（存量零迁移）

`utils/paths.py::resolve_track_deliverable` 扩展为三级 fallback：

```
{deliverables}/{track}/intermediate/{name}   ← 新
→ {deliverables}/{track}/{name}              ← 旧三桶平铺
→ {deliverables}/{name}                      ← 更老 workspace 平铺
```

走这条链的读方（按名找 queue/verdicts 类文件的）：黑盒接力读白盒 queue（`blackbox/pipeline/activities.py:307`）、融合报告 renderer（`combined_report_renderer.py`，读 `{vt}_exploitation_queue.json` + `{vt}_exploit_verdicts.json`）、黑盒 evidence md 按名读方（桶内 → 根顶层兜底）。md 交付物位置不动（新旧结构都在 `{track}/` 顶层），`report_for` / `read_poc` 等 md 读方零感知。

组合 run 读侧（web run 级 endpoint）：`run_dir/deliverables/`（新）→ `run_dir/deliverables/blackbox/`（旧 run）双路径 fallback。

## 7. WEB 产物页

**reader（`deliverables_reader.py`）**：

- `_iter_files` 排除**一切 `.` 开头目录与文件**（归档、checkpoint、`.git` 一并由这条通配覆盖，`_EXCLUDE_DIRS` 保留 `__pycache__`/`schemas`）；
- `summary()` 每文件加 `tier: "deliverable" | "intermediate"`（判据见 §4）；
- 文件内容 endpoint 加大小防御：超过阈值（默认 2 MB，env 可调）的文件返回截断内容 + 元数据标注（`truncated: true`、总大小），前端展示截断提示。`big_json` 类点开不再整读几十 MB。

**前端（`DeliverablesTab` / `FileTree` / `FileStage`）**：

- 默认只展示 `tier=deliverable` 文件；「中间产物」折叠分组收起其余（排障时可展开，queue/gap report 保留入口）；
- run 级视图适配拍平路径（文件无 `blackbox/` 前缀，`FileTree` 按首段分组的逻辑对 run 级退化为单组）；
- 截断文件展示提示横幅。

效果：产物页从 30+ 混排文件 → **本次任务的白盒交付物 + 当前选中 run 的黑盒交付物**（融合报告物理在 `scan_dir/combined/`、经报告 tab 展示，本就不在产物页列表内），约 10-15 个文件。

## 8. 边界（不做）

- 三桶结构（`whitebox`/`blackbox`/`combined`）与 `blackbox-runs/run-K` per-run 版本化**不动**；
- `combined/` 仍在 `scan_dir/` 下与 `deliverables/` 平级，**不动**；
- 存量扫描目录**不迁移**（读侧 fallback 兜底）；
- correlation multi 产物（`out_workspace` 体系）out of scope；
- WEB scan 级列表本就不含 `blackbox-runs/`、`combined/` 内容（二者在 `scan_dir/` 下、不在 rglob 根内），无需额外过滤。

## 9. 测试策略

- **core**：`paths` 新 helper 单测；executor 写盘位置（queue→intermediate、md→顶层、verdicts→桶内 intermediate）；三级 fallback 链单测（用 `NodeGoat-20260812-024451` 旧结构 fixture 验证读侧兼容）；
- **web**：reader 隐藏条目排除、tier 判定（新结构目录判据 + 旧结构模式兜底）、大文件截断；run 级双路径 fallback；
- **前端**：默认过滤 + 折叠分组交互、run 级树退化、截断提示（vitest，`./node_modules/.bin/vitest`）。

## 10. 待 plan 确认项

- 🔴 黑盒桶拍平若实现时破坏面超预期（写侧区分波及面大），降级方案：**仅展示层**剥掉 `blackbox/` 路径前缀、磁盘结构不动——reader 层改一处即可，spec 其余部分不受影响。
- 🔴 截断阈值默认 2 MB 是否合适，plan 时定（可先 2 MB + `SUPERNOVA_DELIVERABLES_PREVIEW_MAX_BYTES` env 覆盖）。
