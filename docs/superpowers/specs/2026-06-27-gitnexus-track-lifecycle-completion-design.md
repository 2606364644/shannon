# GitNexus 轨生命周期完善（接通 + A4 独立产出 + 可观测加固）

- **日期**: 2026-06-27
- **状态**: Design（待用户审阅）
- **分支**: feat/fork-py（HEAD df33ec5）
- **方案**: Approach B — 聚焦 + 可观测加固

---

## 1. 背景与问题

shannon-py 白盒检测双轨：GitNexus 确定性轨 + LLM 轨，verdict OR 合并。GitNexus 轨的设计意图是「确定性兜底 + 可选 LLM 增强」，与 LLM 轨各自独立。

2026-06-27 全生命周期评估发现：GitNexus 轨在 `feat/fork-py@df33ec5` 真机上**生命周期断裂**——前半段（建图）工作，但后半段（链判定 → 漏洞队列 → 合并拾取）基本空跑：

1. **两个 activity 未注册 worker**（`worker.py` grep 零命中，已一手验证）：
   - `run_gitnexus_chain_verdict`（injection/xss/ssrf 链判定）→ workflow 调用挂 `start_to_close_timeout=5min` 超时 → 被 `workflows.py:354-363` try/except 静默吞（warning "non-fatal, LLM-only continues"）→ `<vuln>_gitnexus_queue.json` 从未产出，merger 只见 LLM-only。每次白盒白白多等 5min。
   - `run_auth_config_scan`（auth 兜底）→ `workflows.py:291-296` **无 try/except** → 抛 `ActivityTaskNotRegistered` → 中断 vulnerability-analysis 阶段。
2. **merger 丢弃 GitNexus-only**（`activities.py:536-537`）：LLM queue 缺席时 `continue`，连 GitNexus queue 一起跳过——最需要兜底时兜底产物反被丢。

**净效果**：injection/xss/ssrf 的「双轨」目前是名义上的，实质只有 LLM 单轨。GitNexus 工具建图（`run_code_index`）和 authz 判定（`run_authz_gitnexus_judge`）仍正常（均已注册）。CLAUDE.md §1 把 GitNexus 轨描述为正常运转，与此不符（认知偏差，已记入 memory `gitnexus-track-runtime-state-gap`）。

## 2. 范围

**纳入**：
- **A1 接通**：注册 `run_gitnexus_chain_verdict` + `run_auth_config_scan` 到 worker + anchor 测试防复发
- **A1 配套**：`run_auth_config_scan` 对齐 non-fatal（加 try/except，与 chain verdict 一致）
- **A4 独立产出**：merger 在 LLM queue 缺席时仍并入 GitNexus-only 发现
- **可观测加固**：GitNexus 轨各阶段执行/失败/跳过的明确日志 + GitNexus-only 并入日志（对症「静默降级 → 没人发现没跑通」）

**不纳入**（显式排除）：
- **A3 失败隔离**（`run_code_index` 硬失败不拖垮并行 PRE_RECON）——改动敏感（动被测试锁定的硬失败语义 `test_run_code_index_raises_when_gitnexus_unavailable`），保留现状，另议
- **detect_language 语言误判**（RE-1，`.js`→`ts`）——影响面超 GitNexus 轨（整个确定性层），另立 spec
- **B 类架构演进**（token 成本 / authz 轨统一 / rule_gap 反哺闭环）——非本次

## 3. 目标 / 非目标

**目标**：
1. injection/xss/ssrf 的 `<vuln>_gitnexus_queue.json` 在真机真正产出并被 merger 拾取
2. auth 的 config 扫描在真机执行，失败不中断阶段
3. LLM 轨缺席时，GitNexus 轨发现仍进报告（真兜底）
4. GitNexus 轨执行情况可观测（不再静默降级）

**非目标**：
- 提升 GitNexus 轨产物质量（detect_language）
- 改变 `run_code_index` 失败语义（A3）
- 重构 merger 其他逻辑 / verdict LLM 调用方式

## 4. 设计

### 4.1 接通注册（worker.py）

`worker.py` 两处同步加 `run_gitnexus_chain_verdict` 和 `run_auth_config_scan`：
- import 块（行 13-34）：按现有字母序惯例插入（确切行 plan 阶段定）
- activities 列表（行 93-102）：同步

**anchor 测试**（防复发，memory `temporalio-activity-worker-registration` 教训「每加 activity 配 count>=2 anchor test」）：worker 测试文件加断言，两 activity 名在 `worker.py` 文本出现 `>=2` 次（import + list）。

### 4.2 run_auth_config_scan 对齐 non-fatal（workflows.py:291-296）

现状无 try/except，失败中断阶段。照搬 `run_authz_gitnexus_judge`（338-347）既有模式：

```python
if "auth" in [str(vt) for vt in selected_classes]:
    try:
        await workflow.execute_activity(
            activities.run_auth_config_scan, act_input,
            start_to_close_timeout=timedelta(minutes=3),
            retry_policy=retry_for("standard"),
        )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Auth config scan failed (non-fatal, auth track degrades to LLM-only): %s", exc)
```

**理由**：与同轨 chain verdict 行为一致；auth scan 失败不该拖垮其他 vuln 类（vuln-auth agent 有自身方法论兜底，且 `auth_config_scan.json` 是 lead 不是 verdict）。

### 4.3 A4 merger 修复（activities.py:534-573）

**根因**：536-537 `exploitation_path`（LLM queue）不存在就 `continue`，连 GitNexus queue 一起跳过。改为 LLM 缺席用空 list 继续，仅两轨都空才 continue：

```python
for vuln_class in ("injection", "xss", "ssrf", "authz", "auth"):
    exploitation_path = deliverables / f"{vuln_class}_exploitation_queue.json"
    gitnexus_path = deliverables / f"{vuln_class}_gitnexus_queue.json"

    gitnexus_findings = []
    if gitnexus_path.exists():
        gitnexus_findings = VulnerabilityQueue.parse_lenient(
            gitnexus_path.read_text(encoding="utf-8")).queue.vulnerabilities

    llm_findings, llm_warnings = [], []
    if exploitation_path.exists():
        llm_path = deliverables / f"{vuln_class}_llm_queue.json"
        llm_path.write_text(exploitation_path.read_text(encoding="utf-8"), encoding="utf-8")
        llm_parsed = VulnerabilityQueue.parse_lenient(llm_path.read_text(encoding="utf-8"))
        llm_findings = llm_parsed.queue.vulnerabilities
        llm_warnings = llm_parsed.warnings
    elif not gitnexus_findings:
        continue  # 两轨都空，真没东西

    merged = merge_dual_track_queues(llm_findings, gitnexus_findings, mode="verdict")
    atomic_write_json(exploitation_path, {"vulnerabilities": [f.model_dump() for f in merged]})

    merged_classes.append(vuln_class)
    per_class_counts[vuln_class] = {
        # ... 原有字段 ...
        "llm": len(llm_findings),
        "gitnexus": len(gitnexus_findings),
        "merged": len(merged),
        "both": ..., "llm_only": ..., "gitnexus_only": ...,
        "warnings": llm_warnings,  # 原 llm_parsed.warnings，LLM 缺席时为 []
    }
```

**不变量保持**（merger 既有逻辑，A4 不动）：
- GitNexus-only → `merge_source="gitnexus-only"` / `confidence="needs_review"`
- `externally_exploitable` 取 GitNexus 轨值（gitnexus-only 分支 base finding 是 GitNexus）
- `externally_exploitable` 不被 verdict 覆写（双轨铁律，`dual_track_merger.py:52-57`）

### 4.4 可观测加固

1. **chain verdict / auth scan 成功日志**（workflows.py）：现有 except 只打 "failed"。在 try 块成功后加 `workflow.logger.info(...)`（仿 312 行 `workflow.logger.info("llm_track=disabled...")` 模式），报告各 vuln 类产出计数。让「跑没跑」一眼可见。
2. **GitNexus-only 并入日志**（activities.py merger）：当某类有 GitNexus-only 发现被并入时打日志。放在 `per_class_counts` 构建后、循环内，计数从 `merged` 列表现算（与既有 569-571 算法一致）：
   ```python
   gn_only = sum(1 for f in merged if f.merge_source == "gitnexus-only")
   if gn_only:
       import logging
       logging.getLogger(__name__).info(
           "merge: vuln=%s merged %d gitnexus-only findings (LLM track did not cover)",
           vuln_class, gn_only)
   ```
   （`activities.py` 顶部无模块级 logger，必须内联 `import logging`——follow `workflows.py` except 块既有模式，勿假设模块 logger。）
3. **worker anchor test**：CI 防注册缺失（4.1 已含）。

## 5. 数据流（修复后）

```
vuln 阶段:
  run_auth_config_scan      [注册 + non-fatal] → auth_gitnexus_queue.json        ✅
  run_agent(vuln-*)         [LLM 轨]            → <vuln>_exploitation_queue.json (可能缺席)
  run_authz_gitnexus_judge  [已注册]            → authz_gitnexus_queue.json       ✅
  run_gitnexus_chain_verdict[注册]              → inj/xss/ssrf_gitnexus_queue.json ✅
  run_merge_dual_track_queues [A4]              → 即使 LLM 缺席，GitNexus-only 并入报告 ✅
```

## 6. 错误处理

- chain verdict / auth scan：non-fatal，失败降级 LLM-only（既有）；A4 后即使 LLM 也缺席，GitNexus 失败则该类无产物，不崩
- merger：`parse_lenient` 容错（既有）；A4 新增 LLM 缺席路径沿用同套容错
- 不改 `externally_exploitable` 不可覆写不变量

## 7. 测试

- **worker anchor test**：两 activity 名在 `worker.py` count>=2
- **merger A4 case 1**：LLM queue 缺席 + GitNexus queue 有发现 → 合并结果含 `gitnexus-only` 条目 + 写回 exploitation_path
- **merger A4 case 2**：两轨都空 → continue，不写空文件
- **merger A4 case 3**：LLM queue 存在（回归）→ 行为不变
- 遵守 memory 教训：**只跑改动相关测试文件，勿跑全套**（pytest 全量会 hang，见 `pytest-whitebox-hang`）

## 8. 顺手观察 / Follow-up（非本次范围）

- `workflows.py:351-353` 注释「No parameter_graph.json (Plan 1 not landed)」已过期（Plan 1 已 merge @30481e0），建议顺手改注释
- 探针怀疑 `activities.py:780` `run_gitnexus_chain_verdict` 内 `logger` 未定义（NameError，仅 code_index.json 解析失败路径触发）——待 follow-up 确认
- A3 失败隔离、detect_language RE-1、B 类架构演进：见 §2 排除项

## 9. 参考

- CLAUDE.md §1 双轨概念
- memory: `temporalio-activity-worker-registration`（anchor test 教训）、`pytest-whitebox-hang`（测试子集）、`gitnexus-track-runtime-state-gap`（本次评估来源）、`dual-track-decoupling-status`、`authz-dual-track-status`
- 相关代码：`worker.py`、`workflows.py`、`activities.py`、`dual_track_merger.py`
