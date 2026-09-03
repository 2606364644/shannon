# 黑盒验证缺口留痕与续跑（verification-gap traceability）设计

- 日期：2026-09-03
- 状态：定稿（brainstorming 完成，待实现）
- 事故样本：`workspaces/__legacy__/scans/NodeGoat-20260903-071648`（融合报告 7 verified / 16 untested）

## 1. 背景与问题

产品期望：**黑盒验证即使失败也要告诉用户失败原因；融合报告包含验证成功与验证失败的漏洞，用户能清晰看出每条是否验证成功；看到失败后用户可自行决定是否续跑。**

现状：失败留痕的管道设计其实已存在——`add_exploit` 5 档 status（exploited / blocked_by_security / out_of_scope_internal / false_positive / potential）带齐全的失败原因字段；`report_data_blackbox` 为全部 accepted 状态成卡；融合层 `failed-to-verify` 三态在 `report_fusion.py` 等着接——**但从未亮过**。断点在最上游：agent 没把结论登记进 collector 时，"测了一半"的事实整体丢失。

### 1.1 事故证据链（NodeGoat-20260903-071648）

16 个 untested 实为三种成因：

| 成因 | 条数 | 证据 |
|---|---|---|
| **agent 中断未登记** | 15（全部 XSS） | xss-exploit agent 跑 164 turns / 9.5min / ¥1.70，已实测确认 XSS-VULN-01/02 回显成功；turn 164 LLM premature stop（`success=true, error=null`，非 max_turns 默认 10000、非 40min 超时墙）；行为模式"全测完→最后统一 add_exploit"，死在登记前 → `xss_exploit_verdicts.json` 全空 |
| **登记被校验拒收** | 1（INJ-VULN-02） | injection agent turn 108 文字判定 "INJ-VULN-02 is fully EXPLOITED" 并调 add_exploit(status=exploited)，但缺 `severity` 必填字段被 L1 pydantic 拒 → 进 `rejected`（含 id + reason）→ 不成卡 |
| **类未跑** | 0（本次无此形态） | ExploitationChecker `queue_file_missing` 跳过类时，该类无 verdicts.json（如 ssti 不在范围） |

下游连锁：verdicts 空/被拒 → 黑盒 report_data 0 张对应卡 → 融合层 (type, path) 匹配不到 → 白盒卡标 `untested`"未覆盖"，`verification_gaps[].reason` 只有死文案"白盒发现，黑盒未覆盖（untested）"。

### 1.2 连带问题：续跑入口被锁死

- 续跑机制已有：`POST /{ws}/scans/{scan_id}/combined/rerun-blackbox` → `scan_manager.rerun_blackbox`（`scan_manager.py:2943`）新建版本化 run-K+1，旧 run 保留可对比。
- 但后端守卫"仅 latest run 失败/跳过时可续跑"（`scan_manager.py:2957`）+ 前端按钮 `blackboxFailed = bbPhase === "failed"`（`ScanDetail.tsx:88`）都只认 run 级 failed。
- agent 层 `success=true` 撒谎（`audit-agent-end-success-blindspot` 已知缺口）→ pipeline 认为 xss 成功 → run 收尾 completed → **按钮不渲染、API 拒绝**。用户唯一的补救入口被业务失败的不可见性锁死。

## 2. 目标与非目标

**目标：**
1. verdicts.json 成为验证闭环的完整 SSOT：登记成功 / 登记被拒（含原因）/ 未登记（含中断元数据 + 端点级实测痕迹）三类全部留痕。
2. 黑盒报告包含未验证记录（卡 + 原因）。
3. 融合报告验证状态拆为四态，速查表/卡片/缺口节清晰展示"哪些失败、为什么"。
4. 有缺口的 run 允许用户手动续跑（v1 整跑黑盒，不做 per-class）。

**非目标：**
- 不自动重试中断的 agent（用户看到原因后自行决定续跑）。
- 不修 runner 层 `success` 语义（`audit-agent-end-success-blindspot` 另案；本设计的 `agent_run` 元数据让 0-verdict 事实在产物层可见，不依赖 runner 契约）。
- 不做 per-class 续跑编排（v2 视需要）。
- 不对 rejected 的 exploited verdict 做 LLM 补全/重输（如实展示拒因，不编造缺失字段）。
- 不动白盒轨；不动 `add_exploit` 5 档 schema。

## 3. 概念模型：验证四态

融合报告 `cross_verification` 值域从 {verified, failed-to-verify, untested, blackbox-only} 演进为：

| 态 | 中文标签 | 判定（全部确定性，不猜） |
|---|---|---|
| `verified` | 已实证 | 黑盒卡 verdict ∈ {vulnerable, exploited} |
| `failed-to-verify` | 复验失败 | 黑盒卡存在且 verdict 为 agent 登记的失败档（blocked_by_security / false_positive / out_of_scope_internal / potential）——agent 给的安全原因 |
| `interrupted` | 中断未结论 | 黑盒未验证卡（gaps 产物）匹配上白盒卡；detail 带成因：中断未登记（agent 元数据 + 端点痕迹）或 登记被拒（rejected.reason） |
| `not-covered` | 未覆盖 | 黑盒侧无卡可匹配 **且** 该类 verdicts.json 不存在（agent 未跑该类） |

`blackbox-only`（黑盒独有）语义不变。原 `untested` 值退役（读侧遇旧值映射 `not-covered`，向后兼容历史 session）。

**诚实性约束**：`interrupted` 的 detail 只描述事实（登记进度 M/N、agent 停止形态、端点是否被请求过、rejected 拒因），不推断安全结论。"curl 过该端点"表述为"已对该端点发起过请求，未产出结论"，不表述为"已尝试利用该漏洞"。

## 4. 数据管道：verdicts.json 扩展

**产生时机**：executor 在 agent 结束后落盘 verdicts payload 的现有调用点（`executor.py:588` `build_exploit_verdicts_payload`），一次补齐——此刻 queue、collector 状态、agent metrics、工具轨迹全在作用域内。

**schema 扩展**（`renderers/__init__.py::build_exploit_verdicts_payload`；只增不改，现有消费者 `check_coverage` / PoC / 计数器零影响）：

```json
{
  "vuln_class": "xss",
  "accepted_ids": ["..."],
  "verdicts": ["..."],
  "rejected": ["..."],
  "agent_run": {
    "turns": 164,
    "duration_ms": 570936,
    "success": true,
    "stop_reason": null,
    "error": null
  },
  "gaps": [
    {
      "id": "XSS-VULN-01",
      "reason_type": "unregistered",
      "attempted": true,
      "detail": "agent 未完成验证闭环（登记 0/15）；工具轨迹显示已对该端点发起过请求，未产出结论"
    },
    {
      "id": "INJ-VULN-02",
      "reason_type": "rejected",
      "attempted": null,
      "detail": "agent 已登记 status=exploited 但缺 severity 字段被 L1 校验拒收"
    }
  ]
}
```

- `gaps` = queue_ids − accepted_ids 逐条展开：
  - id ∈ rejected → `reason_type="rejected"`，detail 拼接 rejected.reason（真实拒因：L1 schema / L2 id 不在 queue / L3 重复）。
  - 其余 → `reason_type="unregistered"`，detail 带登记进度（accepted 数 / queue 总数）+ attempted 痕迹。
- **端点痕迹提取（attempted）**：从本 agent 工具轨迹（tool audit 记录）正则提取请求 URL（bash command 内 curl 目标 / 浏览器导航），与 queue 条目端点（`endpoint`/`endpoints`/`path` 字段）做 path 后缀匹配。轨迹数据源取 executor 内存态；不可得时 attempted=null（未知，不猜）。
- `agent_run` 元数据从本 agent 的 result/metrics 取（turns/duration/success/stop_reason/error）。
- queue 文件缺失（类未跑，无 verdicts.json）不产生文件——`not-covered` 由融合层按文件缺席判定。

## 5. 黑盒报告：未验证卡

`report_data_blackbox.build_blackbox_report_data` 为 gaps 条目成卡（与 accepted 全档成卡同层）：

- `evidence.verdict = "interrupted"`；
- `evidence.notes = gap.detail`（成因 + 进度 + attempted）；
- endpoints 取 queue 条目端点（保证融合层 (type, path) 能匹配白盒卡）；
- severity/confidence 从 queue 条目透传（白盒口径），不新造。

黑盒报告从此三分：实测成功（exploited）/ 实测失败（agent 登记的 4 档）/ 中断未结论（gaps 卡）。

## 6. 融合层四态

`report_fusion.py`：

- `_CROSS_LABELS` 扩为五键：verified=已实证 / failed-to-verify=复验失败 / interrupted=中断未结论 / not-covered=未覆盖 / blackbox-only=黑盒独有。
- 匹配逻辑复用现有 id → (type, path) 两级键：interrupted 黑盒卡带 queue 端点可匹配。
- 匹配不到时区分：该类 verdicts.json 存在 → `not-covered`（读 gaps 判断该 id 是否 gap；理论上 gap 卡都能匹配上，此分支兜底）；不存在 → `not-covered`。两者 reason 文案区分"黑盒未跑该类"。
- `verification_gaps[].reason` 换真实原因（gap.detail / 未跑类），保持 `[{vuln_id, reason}]` 结构，前端消费友好。
- 摘要 narrative 更新为四态计数："X 个已实证，Y 个复验失败，Z 个中断未结论，W 个未覆盖"。
- 旧 report_data 兼容：读侧遇 `untested` 标 `not-covered`。

## 7. run 级状态传导与续跑守卫

- 黑盒 workflow 收尾时聚合各类 verdicts gaps：`任一类有 gaps → run 状态 completed-with-gaps`（不标 failed——多数类可能已成功）。run 状态写入复用现有 `_mark_run`/session status 链。
- 后端守卫：`rerun_blackbox` 允许 `failed / skipped / completed-with-gaps`（`scan_manager.py:2957` 处扩条件）。
- 前端：`bbPhase` 识别 completed-with-gaps 时渲染续跑按钮（文案"续跑黑盒补缺口"）；`blackboxFailed` 条件扩为 failed ∨ completed-with-gaps。
- v1 续跑=整跑黑盒（全部 vuln class），复用现有 rerun 编排；已成功类会重测，属可接受成本。

## 8. 前端展示

- **速查表**（`QuickReferenceTable.tsx`）：验证列四态视觉分级——已实证=绿（复用 isDynamicVerification 语言）、复验失败=红/警示、中断未结论=amber、未覆盖=灰。
- **卡片**（`VulnerabilityCard.tsx`）：interrupted 卡 evidence 区显示 detail 原因（notes 已有渲染位）。
- **验证缺口节**：`verification_gaps` 前端目前未渲染——新增"验证缺口"列表节（ID + 四态标签 + reason + attempted），放融合报告卡片区后。
- i18n：四态标签 + 新节文案，zh/en 双语（kebab→camel 陷阱见 memory）。

## 9. 测试策略

- **core**：`build_exploit_verdicts_payload` 扩展（gaps 三成因：unregistered / rejected / 全登记无 gap；agent_run 元数据透传）；`report_data_blackbox` gaps 成卡（endpoints 匹配键保真）。
- **融合**：`report_fusion` 四态单测——verified / failed-to-verify（黑盒失败档卡）/ interrupted（gap 卡匹配）/ not-covered（verdicts.json 缺席 + 存在两分支）；旧 `untested` 兼容映射。
- **blackbox**：run 收尾 gaps 聚合 → completed-with-gaps 传导；verdicts 全登记 → 纯 completed。
- **web**：rerun 守卫放行 completed-with-gaps；速查表四态分级；验证缺口节渲染；tsc 门（`npx tsc -b`，vitest 不查类型）。
- 只跑改动相关测试文件（全套 pytest 有预存挂起，CLAUDE.md §3）。

## 10. 实现顺序（供 plan 展开）

1. core：payload 扩展（gaps/agent_run/痕迹提取）——纯函数，TDD 起点。
2. core：黑盒 gaps 成卡 + 融合四态 + 兼容映射。
3. blackbox：run 收尾聚合 + 状态传导。
4. web 后端：rerun 守卫。
5. web 前端：四态视觉 + 验证缺口节 + i18n + tsc。
6. 真机验证：docker compose 重启 worker/web，重跑一次组合扫描观察四态与续跑按钮（生产烧镜像需 --build，见 memory `prompts-effectiveness-rules`）。
