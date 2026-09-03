# MR 扫描

MR 扫描是白盒主流程的增量包装：先解析 base..head diff，再复用完整 WhiteboxScanWorkflow，只在少数消费点按 `mr_meta` 和 `IncrementalScope` 收窄范围。它不是另起一套缩水扫描器。

## 目标

- 只对 MR 引入的新攻击面、修改链路和删除防护投入深判成本。
- 保留完整白盒索引/融合/报告管线，避免增量实现与全量行为漂移。
- GitNexus verdict 窗口按增量链数重估，避免全量 15 分钟窗口造成重复超时。
- 纯 LLM 轨只收到 git 派生的增量上下文，不读确定性 pgraph/scope 产物。

## 提交与 Workflow

Web 端解析 MR 链接得到 base/head ref 后提交 `MrScanWorkflow`。流程：

```text
run_mr_repo_prepare
  fetch -> checkout head -> merge-base
run_git_diff
  git diff -U3 merge-base..head
  -> diff.patch + diff_manifest.json + stats + vuln class heuristic
if empty diff:
  run_mr_empty_diff_finalize -> no-change report -> completed
run_protection_removal_analysis
  diff.patch -> LLM 判定被删安全防护
  -> removed_protections.json
WhiteboxScanWorkflow child
  input.mr_meta = base/head/vuln classes
  完整 pre-recon/code-index/recon/vuln/dual-track/report 主体
run_incremental_scope
  diff + head CodeIndex + pgraph + removed protections
  -> incremental_scope.json
```

`run_mr_repo_prepare` 不做 Temporal 重试：fetch/checkout/merge-base 失败通常是仓库状态或 ref 输入问题，重试无意义且可能放大副作用。

## Diff manifest

`diff_manifest.py` 解析 unified diff，而不是只看文件名：

- 保留 base/head 双侧行号。
- 记录 added/removed 行文本。
- 识别 new/deleted file。
- 识别 rename 并把 base 路径映射到 head 路径。
- 产出 files/insertions/deletions 统计。

因此增量 scope 能判断“第 N 行是否新增”、删除防护的 base 行号如何映射到 head 函数。

## 删除防护分析

`protection_removal.py` 只检查 diff 中被删除的行，识别是否移除了：

- sanitize/escape/encode
- 参数化查询
- 输出编码
- auth/authz 检查
- CSRF 防护
- rate limit
- 路径规范化
- schema 校验

LLM 必须输出文件、base 行号、删除原文、函数名、防护类型、理由和置信度。纯重构改名、删除无用代码、等价替换不算。

该步骤可降级：LLM 缺席、失败、超时或输出不可解析时 `degraded=True`，来源 C 缺席，但来源 A/B 继续工作。

## 三来源 IncrementalScope

`incremental_scope.py` 在 head 索引生成后运行，输入：

- `DiffManifest`
- `CodeIndex`
- `ParameterPropagationGraph`
- `RemovedProtection[]`

### 来源 A：新增代码引入漏洞

以下任一位置落在 added 行即纳入 flow：

- sink call site
- propagation step 的 `file:line`
- source point（按入口级命中）

### 来源 B：新增入口

若 entry 对应函数块行范围与 added 行相交，或所在文件是新文件，则该入口为新增攻击面，其全部 taint flows 纳入 scope。该规则只看 head 索引，不做 base 侧二次索引。

### 来源 C：删除防护

1. 将 LLM 报的 base 文件/行号映射到 head 函数：函数名精确匹配优先，hunk 区间线性映射兜底。
2. 直接级：flow 的 propagation step 命中该函数。
3. 扩展级：该函数出现在任一 `CallChain.path` 时，把对应 entry 的全部 flows 纳入。
4. 函数整体删除或无法定位时不追链，但保留防护记录。

最终：

```json
{
  "selected_vuln_classes": ["injection", "authz"],
  "new_entry_point_ids": [...],
  "verdict_flow_ids": [...],
  "source_a_flow_ids": [...],
  "source_b_flow_ids": [...],
  "source_c_flow_ids": [...],
  "removed_protection_flows": [...]
}
```

`verdict_flow_ids` 是 A∪B∪C 去重保序后的 GitNexus verdict 过滤集。空集表示 MR 内无可判定链，必须过滤为零候选；不能把空集当“未设置”而退化成全量判定。

## 漏洞类启发式

`select_vuln_classes(diff)` 是确定性小表，不是 prompt：

- auth/login/session/middleware/permission/role 路径 → `auth`, `authz`
- backend 文件或 route/controller/handler/api 路径 → `injection`, `ssrf`, `authz`
- frontend/模板文件 → `xss`
- 无信号 → 全类

优先级：用户显式选择 > MR 启发式 > YAML 配置 > 默认全类。

## 双轨增量接线

- **GitNexus 轨**：`run_incremental_scope` 后按 `verdict_flow_ids` 过滤 pgraph candidates，并计算 verdict timeout。
- **纯 LLM 轨**：`build_mr_incremental_guidance(mr_meta)` 只注入 base/head commit、diff 文件路径提示和“可自行读 diff.patch / git diff”的指引；不包含 flow/sink/scope。
- **报告**：`trigger_source_of` 按 C > B > A 给 GitNexus 卡打 `removed_protection` / `new_entry` / `new_code` 来源；纯 LLM 卡不伪造该标签。

## 空差异

base==head 或 stats.files==0 时：

- 不跑删防护判定；
- 不启动 child 白盒双轨；
- 直接复用报告组装器生成“无变更”报告；
- workflow 状态 completed。

## 产物

```text
whitebox/intermediate/mr/
  diff.patch
  diff_manifest.json
  removed_protections.json
  incremental_scope.json
```

报告还会读取 MR refs 和 manifest，显示增量上下文与触发来源。

## 验证入口

- `packages/core/tests/mr_scan/test_diff_manifest.py`
- `packages/core/tests/mr_scan/test_diff_manifest_git_integration.py`
- `packages/core/tests/mr_scan/test_protection_removal.py`
- `packages/core/tests/mr_scan/test_incremental_scope.py`
- `packages/core/tests/mr_scan/test_vuln_class_selection.py`
- `packages/whitebox/tests/pipeline/test_mr_activities.py`
- `packages/whitebox/tests/pipeline/test_mr_scan_workflow.py`
- `packages/whitebox/tests/pipeline/test_mr_workflow_wiring.py`
