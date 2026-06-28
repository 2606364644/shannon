# authz GitNexus 轨可观测性 + AZ-4 防回退 设计

> 日期：2026-06-29　分支：`feat/fork-py`
>
> **背景**：W4 真机核查（`workspaces/NodeGoat_20260628-023125`）发现 authz GitNexus 轨在 `code_index` 空壳时**静默空转**——`run_authz_gitnexus_judge` 以 1ms 写空 queue，workflow.log 只留一行 `✓ authz-gitnexus-judge 1ms`，用户无法得知"GitNexus 轨为何零贡献"。同批核查发现 **AZ-4（recon 逐方法列 DELETE）已在 `_endpoint-security-context.txt` 落地**，`docs/gap/authz-effect-gap-analysis.md` AZ-4（写于 2026-06-16）已过时。

---

## 1. 目标 / 非目标

### 目标

- **G1（W1-B 可观测性）**：消除 authz GitNexus 轨的静默空转。`candidate_count==0` 时经 InfoEvent 通道发 `warning`，`candidate_count>0` 时发 `info`，让用户看到"GitNexus 轨 N 候选 → 跳过/调 LLM → verdict 数"及**空壳原因**（http_route 入口点数）。
- **G2（AZ-4 防回退）**：加测试锁定 `_endpoint-security-context.txt` 的"禁止 ALL / 逐方法列 DELETE"不被回退，并锁定 `recon.txt`/`recon-static.txt` 对它的 `@include`。

### 非目标（明确排除，含已证伪项）

- **不修 `detect_language`（W1-A 已证伪）**：`parser.py:9` 把 `.js/.jsx` 归到 `typescript` 是**刻意的**——项目只有 5 个 parser（python/typescript/go/java/php），**无 javascript parser**，tree-sitter typescript parser 兼任 JS 解析（NodeGoat 的 24 blocks 正是它提取的）。若把 `.js` 分出去标 `javascript`，`get_parser("javascript")` 返回 None → `__init__.py:126-130` 直接 raise `PentestError`，白盒崩。NodeGoat 空壳（entry_points=0 / chains=0）的真根因在 **GitNexus MCP 调用图（chains=0）+ `detect_entry_points` 对 Express/CommonJS 的识别**，不在语言标签。
- **不实现 post-dominator（W2）**：卡在同一调用图根因（NodeGoat chains=0 无从分析）+ 中成本。
- **不改双轨合并逻辑**、**不改 LLM 轨 prompt（`vuln-authz.txt`）**、**不喂确定性产物给 LLM 轨**（CLAUDE.md §1 铁律）。
- **不推广到注入轨** `run_gitnexus_chain_verdict`（NodeGoat 同样 0ms 空转，但属另一 vuln 类）→ 列为 follow-up。

---

## 2. 现状证据

| 现象 | 证据 |
|---|---|
| GitNexus 轨静默空转 | `workflow.log:369-370` `○ authz-gitnexus-judge / ✓ 1ms`；`authz_gitnexus_queue.json` = `{"vulnerabilities": []}`；`candidate_count==0` 分支（`activities.py:254`）无任何 `log_info` |
| 模块层有 log 但不进 workflow.log | `authz_gitnexus_track.py:131/329` 有 `logger.info`，但 Python logging 不经 InfoEvent → dispatcher → renderer，**不进 workflow.log** |
| InfoEvent 是唯一用户可见通道 | `audit_session.log_info() → workflow_logger.py:126 → dispatcher.dispatch(InfoEvent) → rich/file_renderer`；`log_info_activity`（`activities.py:213-220`）即调 `get_audit_session().log_info()` |
| AZ-4 已落地 | `_endpoint-security-context.txt:11-14` "Do NOT use ALL shorthand / List each method explicitly: GET, POST, PUT, PATCH, DELETE / Note if explicitly blocked (denyAll())"；`recon.txt:44` `@include`；端点表格有 DELETE 示例（`recon.txt:223/286`、`recon-static.txt:178`） |

---

## 3. 设计

### 3.1 `build_authz_gitnexus_track` 返回诊断字段（core 层）

**现状**：返回 3-tuple `(markdown, dominance_cands, framework_cands)`（`authz_gitnexus_track.py:285-333`）。

**改动**：改为返回 `NamedTuple` `AuthzTrackBuildResult`，新增两个诊断字段（数据已在 `index` 里，零额外 I/O）：

```python
class AuthzTrackBuildResult(NamedTuple):
    markdown: str
    dominance_candidates: list[IDORCandidateChain]
    framework_candidates: list[FrameworkIDORCandidate]
    http_route_count: int       # 新增：entry_type=="http_route" 且 route 非空的入口点数（dominance 的直接输入）
    entry_point_total: int      # 新增：code_index entry_points 总数（含 gitnexus 合成项）
```

在 `build_authz_gitnexus_track` 末尾计算（对齐 `find_unguarded_sink_paths:92-93` 的过滤口径）：

```python
entry_point_total = len(index.entry_points)
http_route_count = sum(
    1 for ep in index.entry_points
    if ep.entry_type == "http_route" and ep.route is not None
)
```

`index is None`（code_index 缺失/坏）时走现有空 `CodeIndex` 分支（`:319-323`），两者皆为 0。

**影响面**（全部 tuple 解包，改属性或补解包变量）：
- 生产：`activities.py:250`
- 测试：`test_authz_build_track.py:49/61/76/84`、`test_authz_track_integration.py:62/79/96`（共 7 处）

> **为何 NamedTuple 而非 dataclass/4-tuple**：NamedTuple 兼容现有 tuple 解包语法、类型安全、字段自描述，且未来加诊断字段（如 framework detected name）不破坏位置语义。

### 3.2 `run_authz_gitnexus_judge` 加 InfoEvent log（whitebox 层）

**落点**：`activities.py:222-303`，`candidate_count` 计算后（`:251`）。

**机制**：activity 内直接 `await get_audit_session().log_info(message, level)`（与 `log_info_activity:217` 同路径），经 InfoEvent → renderer 进 workflow.log。**包 `try/except` best-effort**（对齐 `log_info_activity:218-219`：显示通道失败绝不影响扫描）。

**文案与 level**：

```python
# candidate_count == 0
await get_audit_session().log_info(
    f"authz GitNexus 轨：0 候选（dominance={len(dom_cands)}, framework={len(fw_cands)}；"
    f"http_route 入口点={http_route_count}/{entry_point_total}）→ 跳过 LLM 判定，"
    f"authz 全靠 LLM 轨兜底。http_route=0 常因 code_index 入口点未识别"
    f"（语言误判/调用图未就绪/纯静态页）。",
    "warning",
)
# candidate_count > 0（调 LLM 前）
await get_audit_session().log_info(
    f"authz GitNexus 轨：{candidate_count} 候选（dominance={len(dom_cands)}, "
    f"framework={len(fw_cands)}）→ 调 LLM 判定。",
    "info",
)
# 判定后（candidate_count > 0 分支末）
await get_audit_session().log_info(
    f"authz GitNexus 轨：产出 {len(vulnerabilities)} 条 verdict。",
    "info",
)
```

**渲染效果**（NodeGoat 将从）：
```
○ authz-gitnexus-judge
[warning] authz GitNexus 轨：0 候选（dominance=0, framework=0；http_route 入口点=0/0）→ 跳过 LLM 判定…
✓ authz-gitnexus-judge  1ms
```
即 `track_step` 的 STEP ○/✓ 之间插入诊断行，空壳原因（http_route=0）直接可见。

### 3.3 AZ-4 防回退测试（G2）

**新文件** `packages/core/tests/prompts/test_endpoint_method_enumeration.py`，照 `test_static_dataflow_hints_decoupling.py` 范式（`parents[4]` 定位 repo root）：

```python
PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"
ESC = PROMPTS_DIR / "shared" / "_endpoint-security-context.txt"

def test_endpoint_security_context_forbids_all_shorthand():
    text = ESC.read_text()
    assert "Do NOT use" in text and "ALL" in text
    assert "List each method explicitly" in text
    for verb in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        assert verb in text

def test_recon_prompts_include_endpoint_security_context():
    for name in ("recon.txt", "recon-static.txt"):
        assert "@include(shared/_endpoint-security-context.txt)" in (PROMPTS_DIR / name).read_text()
```

锁定：①partial 禁止 ALL 且逐方法列全 5 动词；②两个 recon prompt 都 @include 它。任一回退（删 partial、改回 ALL、漏 @include）测试即红。

---

## 4. 不变量（不得违反）

1. **双轨独立性**（CLAUDE.md §1）：不改 `vuln-authz.txt`、不喂确定性产物给 LLM 轨。本次只动 GitNexus 轨 activity 的**可观测性** + core 返回类型。
2. **best-effort log**：`log_info` 失败不影响扫描（try/except 吞掉）。
3. **不改合并**：`run_merge_dual_track_queues` / `dual_track_merger.py` 不碰；`externally_exploitable` 仍是不被覆写的可达性标签。
4. **lenient 不变**：`code_index.json`/`framework_analysis.json` 缺失仍走空候选，不崩。

---

## 5. 测试策略

| 测试 | 文件 | 覆盖 |
|---|---|---|
| `run_authz_gitnexus_judge` candidate_count==0 发 warning | `packages/whitebox/tests/`（新建或扩 `test_authz_gitnexus_judge*.py`） | mock `get_audit_session`，断言 `log_info` 被以 `"warning"` 调用、message 含 "0 候选" + http_route 数 |
| `run_authz_gitnexus_judge` candidate_count>0 发 info + verdict 数 | 同上 | mock，喂非空 code_index（http_route 候选），断言 info 调用 + LLM 调用仍发生 |
| `build_authz_gitnexus_track` 返回诊断字段 | 扩 `test_authz_build_track.py` | 断言 `result.http_route_count`/`entry_point_total` 正确（空壳=0/0、有候选>0） |
| AZ-4 防回退 | `packages/core/tests/prompts/test_endpoint_method_enumeration.py`（新） | 见 §3.3 |
| 回归 | 现有 `test_authz_*` 全绿 | 解包签名变更后 7 处调用点同步 |

> 测试口径：只跑改动相关测试文件（CLAUDE.md §3：勿广跑全套，有预存 hang）。

---

## 6. 风险 / 取舍

| 风险 | 评估 | 缓解 |
|---|---|---|
| 改 `build_authz_gitnexus_track` 签名破坏调用点 | 低（1 生产 + 7 测试，全在本仓） | NamedTuple 兼容 tuple 解包；7 处逐一同步 + 测试守护 |
| `log_info` 在 `track_step` block 内调用时序 | 低 | `get_audit_session()` 单例，block 内重取同一 session；InfoEvent 在 STEP ○/✓ 间渲染，符合预期 |
| 诊断 message 过长挤占终端 | 低 | 单行，含关键数值；renderer 已有 InfoEvent 折行 |
| 用户误以为修了 detect_language 就能救 NodeGoat | 中（认知） | warning 文案点明"http_route=0 常因入口点未识别"，引导到真根因；本 spec §1 非目标显式记录 W1-A 证伪 |

---

## 7. Follow-up（本次不做，记录待排期）

1. **真根因：GitNexus 调用图对 Express/CommonJS 提取（chains=0）**——需诊断 `build_call_graph_from_gitnexus` 对 NodeGoat 返回空的原因（GitNexus MCP 返回空 vs `detect_entry_points` 对 Express 路由失效）。这才是 NodeGoat 空壳的解。
2. **W2 post-dominator**：待调用图根因解决（有非空 chains）后实现 spec §5.7 设想的调用图必经节点 + 中间件注解。
3. **finale DELETE 识别**：juice-shop 漏 `DELETE /api/Feedbacks/:id` 的真因是 finale-rest 自动生成 DELETE 不在 router.js 显式行 → 属 `framework_analysis`（Plan 2）识别 + 双轨 framework 候选补召回，非 prompt 缺陷。
4. **可观测性推广**：把同样的 candidate_count==0 空转 log 推广到 `run_gitnexus_chain_verdict`（注入/xss/ssrf 轨，NodeGoat 同样 0ms 空转）。
