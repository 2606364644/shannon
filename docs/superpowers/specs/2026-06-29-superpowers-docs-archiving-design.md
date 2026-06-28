# docs/superpowers 目录归档与索引导航设计

> 日期：2026-06-29
> 分支：feat/fork-py
> 状态：设计待 review
> 关联：docs/superpowers 现有 202 文件平铺、无索引、无完成标记的整理

---

## 1. 背景

`docs/superpowers/` 长期作为 spec/plan 工作目录，已堆积 **202 个文件**（`specs/` 97 + `plans/` 105），时间跨度 2025-06 ~ 2026-06-29，其中 2026-06 单月占 186 个。现状痛点：

- 全平铺，**无 README / 索引导航**
- 文件内**无可靠的「已完成」标记**（仅 21 个文件有显式 `status` 字段），光看文件分不清「已 merge 的历史」与「在途活跃工作」
- **几乎无真冗余**（约 75 对 spec+plan 成对，仅 2 个文件提到「废弃」、1 个 superseded）——问题不在垃圾多要删，而在「东西都在但没结构、没导航、分不清死活」

`feat/fork-py` 领先 `main` 1102 commits，故「是否进 main」**不能**作为归档标准（否则几乎所有近期文档都得留顶层）。真正应使用**工作完成度**：2 周前（≤2026-06-15）的工作基本早已做完，最近 2 周的才是活跃在途。

## 2. 目标

把 `docs/superpowers/` 从「202 文件无结构平铺」整理为「**活跃层精简 + 历史归档 + 主题索引导航**」，降低顶层噪音、提供可查找的导航。**只移动文件 + 新增 README，不改文档内容、不删文件**。

## 3. 非目标（YAGNI / 安全边界）

- ❌ 不按主题拆子目录（会打破 spec↔plan 同级配对，归类判断成本高）
- ❌ 不删任何文件
- ❌ 不改任何文档内容（只移动 + 加 README）
- ❌ 不重命名（保持链接稳定）

## 4. 目录结构

归档后：

```
docs/superpowers/
├── README.md              ← 新增：主题索引 + 归档说明
├── specs/                 ← 活跃层：>2026-06-15 的 spec（43）
├── plans/                 ← 活跃层：>2026-06-15 的 plan（50）
├── specs/archive/         ← 新增：≤2026-06-15 的 spec（54）
└── plans/archive/         ← 新增：≤2026-06-15 的 plan（55）
```

**切点规则**：文件名日期 `≤ 2026-06-15` → 归档；`> 2026-06-15` → 活跃层。归档量 specs 54 + plans 55 = **109**；活跃层保留 specs 43 + plans 50 = **93**。

**归档区结构**：采用 `specs/archive/` + `plans/archive/`（而非统一 `archive/`）。理由：保持现有 `specs/` vs `plans/` 二分不变，同 topic 的 spec+plan 仍各自归位、靠同名配对，路径习惯不破。

归档用 `git mv`，完整保留历史、可逆；误归可一键 `git mv` 拉回。

## 5. README.md 索引设计

**顶部**：目录组织规则（活跃层 vs archive）、归档切点（≤2026-06-15）、维护规则（未来新工作进活跃层；定期把越过切点的批量归档）、从 archive 拉回文件的方法（`git mv specs/archive/<f> specs/<f>`）。

**活跃层主体**：按主题主线分组，每条 = 文件链接 + 一句话主题。主题主线（基于活跃层 93 个文件清单）：

| 主题主线 | 代表 topic |
|---|---|
| 双轨 dual-track | auth-dual-track / authz-dual-track / dual-track-merger / framework-analyzer-wiring / inj-xss-ssrf-dual-track / pre-recon-dual-track / recon-dual-track / dual-track-decoupling / whitebox-dual-track-merge-architecture |
| GitNexus 轨 | gitnexus-index-degradation / gitnexus-intra-taint-deterministic-fallback / gitnexus-llm-sink-discovery / gitnexus-track-lifecycle-completion / taint-persist / injection-recall-port / llm-track-vuln-parity-restoration |
| 显示 UX | rich-display-layout-fix / whitebox-display-clarity / whitebox-live-step-intent-display / provider-agnostic-turn-logging / rich-log-visibility / log-format-redesign / log-label-alignment / report-render-stop-bleed / live-dashboard-ghost-frame-fix / chinese-comprehensive-report / display-ux-polish / workflow-info-display-channel / cli-workflow-failure-friendly-display |
| 引擎 engine | openai-agents-engine（+smoke）/ dual-engine-decoupling-fix / blackbox-agent-browser-default-engine |
| env / config | env-config-profiles / anthropic-env-prefix / max-concurrent-env / token-caching / remove-minimal-fallback-hard-fail |
| resume / rerun | resume / whitebox-resume（+smoke）/ blackbox-rerun（+smoke） |
| deliverables / prompt | deliverables-to-session / prompt-deliverables-migration / prompt-optimization |
| retry / 健壮性 | retry-policy-alignment / glm-529-retry-resilience / exploit-coverage-closure |
| 黑盒 / 跨仓 | cross-repo-microservice-correlation / blackbox-exploit-outcome-field-mapping |
| audit / attribution | audit-session-agent-attribution |
| workspace | workspace-human-readable-timestamp |
| authz 演进 | authz-attack-chain-confidence / authz-optimization-roadmap |

**状态标注**：对 memory 里明确记录状态的高优项标「待merge」/「在途」/「设计中」；未记录的不强标。**归档区不标状态**（位置本身即「历史」）。

**归档区**：一行指向 `specs/archive`、`plans/archive`，附按月概览（2025-06 / 2026-05 / 2026-06 初~中），不展开 109 条。

## 6. 状态标注来源

活跃层状态从 **memory（覆盖近 2 周）+ plan 文件 checkbox + git log** 推断；推断不到的不硬标，避免凭空猜测。

## 7. 执行方式

单次 commit，顺序：
1. 建 `specs/archive/`、`plans/archive/`
2. `git mv` 109 个文件（specs 54 + plans 55，按切点 ≤2026-06-15）
3. 写 `README.md`
4. 一次提交

归档（步骤 1-2）与索引（步骤 3）互相独立，可分两步验证：先验证归档文件数与切点一致，再验证 README 内容。

## 8. 验收

- `git mv` 后 `specs/` + `plans/` 顶层只剩 `> 2026-06-15` 的文件（93 个）
- `specs/archive/`（54）+ `plans/archive/`（55）含对应归档文件，`git log --follow` 可追溯历史
- `README.md` 存在，包含：组织规则 + 主题分组 + 归档区说明
- **无文件被删 / 被改内容**：`git diff --stat` 仅显示 rename（109）+ 新增 README（1），无内容修改行
