# MR 增量扫描——实施进度（2026-09-03 夜 · 全部任务完成）

> 状态：✅ **#11/#12/#13 + §3.3/§4.2 全部落地**（本文件原为交接文档，续做会话完成后改写为完成记录）。
> spec：`docs/superpowers/specs/2026-09-03-mr-incremental-scan-design.md`（已提交 `ac359c72`）
> 分支：`feat/fork-py`（注意：该分支另有并行会话在做双轨去重修复——git 操作须精确到文件，勿宽域 add/reset）

---

## 0. 完成总览（按提交序）

| commit | 内容 |
|---|---|
| `338716cd`/`924985a7`/`8de038d0`/`20f70c3b` | core 三层 + whitebox 前置 activities（交接前已提交） |
| `07b0221e`/`b40a1a5a`/`947d54d5` | web 后端/worker/前端半成品（交接前已提交） |
| `9d4bffc8` | **#11 收尾**：ScanNewPage MR 表单渲染分支（type=mr 排 corrMode===auto 之前）+ ScanList MR 徽标/base..head/类型档/重跑预填 + `_scan_detail`/`ScanSummary` 透出 mr refs + 前端 7 测试 |
| `1f5349dd` | **#12 报告层**：IncrementalScope 三来源明细 + `trigger_source_of`（C>B>A 归并、stored# 复合拆分）+ ReportData 增量段（ScanMeta refs/`IncrementalSummary`/`ReportVulnerability.trigger_source`）+ builder 组装打标（merge_source ∈ both/gitnexus-only 且 flow 命中）+ 前端 `MrIncrementalSummary`/∆ 徽章 |
| `ca08b179` | **#13**：NodeGoat 缩影 E2E fixture（真实 git diff → scope → 报告打标全链，三来源各标正确）+ 铁律前瞻锁定（mr_meta 污染不泄漏进 guidance） |
| `f69fd8f9` | **§3.3/§4.2**：空 diff 快速终态 + GN verdict 容量窗口重估 + workflow 级编排测试——**修 3 个潜伏 bug**（见 §2） |

测试口径：core mr_scan 27 + services（含新增 MR 组）+ whitebox mr 19 + 前端 vitest 218 + `npx tsc -b` 全绿；唯一失败 `test_build_report_data_maps_queue_fields` 为**预存失败**（断言已退役的 `poc.witness_payload` 字段，HEAD 上同挂，非本项目引入）。

---

## 1. 实现要点（偏离 spec 处与决策记录）

- **base/head 输入用手输文本框**（spec §6 原文写复用 BranchCombobox）：BranchCombobox 是仓库列表行内切换控件（无边框小按钮 trigger），非表单样式；i18n 键已按文本输入注入（"分支名或 commit sha"）。
- **trigger_source 打标在报告组装时反查**（spec/进度原文的另一选项是 verdict queue 产出处打标）：builder 读 `incremental_scope.json` 三来源明细反查——queue SSOT 保持薄、不动 merger/findings 模型链。LLM-only 卡一律不标（merge_source 双条件）。
- **空 diff 语义**（spec §7 兑现）：`stats.files==0` → 跳过删防护判定与 child，`run_mr_empty_diff_finalize` 复用 `_build_report_data_initial` 产「无变更」报告（scan 带增量 refs）。曾有的隐患：空 diff 时 `select_vuln_classes([])` 返全类 + scope 空集 filter 直通 → 双轨全量空烧，已一并修掉。
- **容量窗口**（§4.2 兑现）：`run_incremental_scope` 返 `verdict_timeout_minutes`（链数 ÷ 并发 × 60s/轮，下限 5min；activity 层算好——workflow 沙箱禁 env 读），child 据此收窄 `run_gitnexus_chain_verdict` 窗口；全量 15min 不变，未跑 scope 回落全量窗口。
- **空集 ≠ None**：`filter_flows_by_mr_scope` 空集过滤为零候选（曾直通致 MR 退化全量判定）。

## 2. f69fd8f9 修的 3 个潜伏 bug（MrScanWorkflow 此前只有语法验证）

1. `workflows.py` 构造 ActivityInput 读不存在的 `PipelineInput.workspace_path`（AttributeError 炸 workflow task）→ 抽 `_derive_workspace_path` 模块函数（child 三级派生共用）。
2. MR vuln 类消费点 `VulnType(c)` 实例化 `typing.Literal`（TypeError）→ 字符串直传。
3. `worker/runner.py` activities 列表引用 MR activity 名但**缺 import**（`run_worker()` 调用即 NameError）→ 补 import + 静态冒烟。

## 3. 已知边界 / 后续

- 同 repo 并发 MR 扫描 checkout 互斥（spec §7「提交前校验」）：未实现，靠现有并发治理兜底。
- MR resume 语义未定（前端 mr 行无续跑入口，`canResume` 限 whitebox）。
- **pytest WorkflowEnvironment + child workflow 在本机有预存挂起**（CLAUDE.md 测试陷阱；heartbeat 基准测试同挂）→ child 穿线由 `_mr_child_input` 纯函数单测锁定 + 独立脚本验证（`/tmp/mr_repro.py` 模式），未引入挂起测试。空 diff workflow 测试（无 child）正常。
- 真机 E2E（temporal + worker 容器 + 真 LLM 的 NodeGoat MR 扫描）待部署环境跑一次冒烟——单测层链路已全覆盖。

## 4. 复验命令（改动 mr 相关后）

```
uv run --no-sync python -m pytest packages/core/tests/mr_scan/ packages/whitebox/tests/pipeline/test_mr_* packages/core/tests/agents/test_executor_prompt_suffix.py
cd packages/web/frontend && npx vitest run src/pages/ScanNewPage.test.tsx src/routes/WorkspaceDetail/ScanList.test.tsx src/components/report/ && npx tsc -b
```
