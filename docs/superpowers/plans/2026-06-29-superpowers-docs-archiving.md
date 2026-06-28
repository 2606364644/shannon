# docs/superpowers 目录归档与索引导航 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `docs/superpowers/` 中日期 ≤2026-06-15 的 109 个 spec/plan 归档到 `specs/archive/`、`plans/archive/`，新增 `README.md` 主题索引导航；不改任何文档内容、不删文件、不重命名。

**Architecture:** 纯文件移动（`git mv` 保留历史）+ 新增一个 README。按**文件名日期**切点 `≤ 2026-06-15` 判定归档；README 按 12 条主题主线分组活跃层文档。文档/移动任务无代码逻辑，验收改用 shell 不变量断言（顶层全 >切点、archive 全 ≤切点、文件守恒、`git diff --stat` 仅 rename/新增）。

**Tech Stack:** git、bash/zsh shell、Markdown。

**Spec:** [docs/superpowers/specs/2026-06-29-superpowers-docs-archiving-design.md](../specs/2026-06-29-superpowers-docs-archiving-design.md)

## Global Constraints

- **切点**：文件名日期 `≤ 2026-06-15` → 归档；`> 2026-06-15` → 活跃层。日期取文件名前缀 `YYYY-MM-DD`。
- **归档区结构**：`specs/archive/` + `plans/archive/`（保持 specs/plans 二分，不建统一 archive/）。
- **只移动 + 新增 README**：不改任何文档内容、不删文件、不重命名。
- **用 `git mv`**（保留历史、可逆）。
- **归档量**：specs 54 + plans 55 = 109（本次 archiving 的 spec/plan 日期 2026-06-29，属活跃层，不归档）。
- **工作目录**：repo 根 `/Users/mango/project/shannon-refactor/shannon-py`。所有命令在 repo 根执行（用相对路径 `docs/superpowers/...`）。
- **提交策略**：Task 4 一次性提交。先提交设计文档（spec + 本 plan，`docs(spec)`/`docs(plan)`），再提交归档+README（`docs(superpowers)`）。

---

### Task 1: 归档 specs（≤2026-06-15 → specs/archive/）

**Files:**
- Create dir: `docs/superpowers/specs/archive/`
- Move: 54 个 `docs/superpowers/specs/*.md`（日期 ≤2026-06-15）→ `docs/superpowers/specs/archive/`

**Interfaces:**
- Consumes: Spec §4 切点规则
- Produces: `specs/archive/` 含归档 spec；`specs/` 顶层只剩日期 >2026-06-15 的 spec

- [ ] **Step 1: 建归档目录**

```bash
mkdir -p docs/superpowers/specs/archive
```

- [ ] **Step 2: 生成归档清单并核对数量**

```bash
cd docs/superpowers/specs
for f in *.md; do
  d=$(echo "$f" | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}')
  if [[ "$d" < "2026-06-15" || "$d" == "2026-06-15" ]]; then echo "$f"; fi
done | tee /tmp/specs_archive_list.txt
echo "待归档 spec 数: $(wc -l < /tmp/specs_archive_list.txt)"
```

Expected: `待归档 spec 数: 54`

- [ ] **Step 3: git mv 归档**

```bash
cd docs/superpowers/specs
while read f; do git mv "$f" "archive/$f"; done < /tmp/specs_archive_list.txt
```

- [ ] **Step 4: 验证不变量（顶层全 >切点 / archive 全 ≤切点）**

```bash
cd docs/superpowers/specs
echo "顶层中最旧的日期(应 > 2026-06-15):"
ls *.md | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}' | sort | head -1
echo "archive 中最新的日期(应 ≤ 2026-06-15):"
ls archive/*.md | xargs -n1 basename | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}' | sort | tail -1
echo "顶层=$(ls *.md | wc -l | tr -d ' ') archive=$(ls archive/*.md | wc -l | tr -d ' ')"
```

Expected: 顶层最旧日期 `2026-06-16`；archive 最新日期 `2026-06-15`；计数顶层 44 / archive 54（顶层含本次 archiving-design spec，日期 2026-06-29，不归档）。

- [ ] **Step 5: 暂不提交（统一在 Task 4 提交）**

---

### Task 2: 归档 plans（≤2026-06-15 → plans/archive/）

**Files:**
- Create dir: `docs/superpowers/plans/archive/`
- Move: 55 个 `docs/superpowers/plans/*.md`（日期 ≤2026-06-15）→ `docs/superpowers/plans/archive/`

**Interfaces:**
- Consumes: Spec §4 切点规则
- Produces: `plans/archive/` 含归档 plan；`plans/` 顶层只剩日期 >2026-06-15 的 plan

- [ ] **Step 1: 建归档目录**

```bash
mkdir -p docs/superpowers/plans/archive
```

- [ ] **Step 2: 生成归档清单并核对数量**

```bash
cd docs/superpowers/plans
for f in *.md; do
  d=$(echo "$f" | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}')
  if [[ "$d" < "2026-06-15" || "$d" == "2026-06-15" ]]; then echo "$f"; fi
done | tee /tmp/plans_archive_list.txt
echo "待归档 plan 数: $(wc -l < /tmp/plans_archive_list.txt)"
```

Expected: `待归档 plan 数: 55`

- [ ] **Step 3: git mv 归档**

```bash
cd docs/superpowers/plans
while read f; do git mv "$f" "archive/$f"; done < /tmp/plans_archive_list.txt
```

- [ ] **Step 4: 验证不变量**

```bash
cd docs/superpowers/plans
echo "顶层中最旧的日期(应 > 2026-06-15):"
ls *.md | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}' | sort | head -1
echo "archive 中最新的日期(应 ≤ 2026-06-15):"
ls archive/*.md | xargs -n1 basename | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}' | sort | tail -1
echo "顶层=$(ls *.md | wc -l | tr -d ' ') archive=$(ls archive/*.md | wc -l | tr -d ' ')"
```

Expected: 顶层最旧日期 `2026-06-16`；archive 最新日期 `2026-06-15`；计数顶层 51 / archive 55（顶层含本次 archiving plan，日期 2026-06-29，不归档）。

- [ ] **Step 5: 暂不提交（统一在 Task 4 提交）**

---

### Task 3: 写 README.md 主题索引

**Files:**
- Create: `docs/superpowers/README.md`

**Interfaces:**
- Consumes: Spec §5 索引设计；Task 1/2 归档后的活跃层清单
- Produces: `docs/superpowers/README.md`（组织规则 + 主题分组 + 归档区说明）

- [ ] **Step 1: 写 README.md 完整内容**

创建 `docs/superpowers/README.md`，内容如下（完整，无占位符）：

````markdown
# docs/superpowers — Spec & Plan 索引

本目录存放 shannon-py 各项工作的 **设计 spec**（`specs/`）与 **实现 plan**（`plans/`）。每个工作通常 spec+plan 成对、同名配对：plan = `plans/YYYY-MM-DD-<topic>.md`，spec = `specs/YYYY-MM-DD-<topic>-design.md`。

## 组织规则

- **活跃层**（`specs/`、`plans/` 顶层）：近期在途工作，文件名日期 `> 2026-06-15`。
- **归档区**（`specs/archive/`、`plans/archive/`）：历史已完成工作，文件名日期 `≤ 2026-06-15`。
- **归档切点**：`2026-06-15`（2026-06-29 设定）。定期把越过切点的活跃文档批量 `git mv` 进 archive。
- **从 archive 拉回**：`git mv specs/archive/<file> specs/<file>`（plans 同理）。
- **查 spec**：plan 链接里的 `plans/` 换 `specs/`、文件名加 `-design` 即对 spec（部分 topic 仅有 spec 或仅有 plan，见各条标注）。

## 活跃层（按主题主线）

> 状态：✅已merge ｜ 🔧待冒烟/待merge ｜ 📐设计中/进行中 ｜ (空)=未记录，查 memory / git log

### 双轨 dual-track
- [whitebox-dual-track-merge-architecture](specs/2026-06-24-whitebox-dual-track-merge-architecture-design.md) — 白盒双轨合并器（verdict OR）架构
- [dual-track-merger-plan](plans/2026-06-24-dual-track-merger-plan.md) — `dual_track_merger.py` 实现
- [auth-dual-track-plan](plans/2026-06-24-auth-dual-track-plan.md) — auth 双轨
- [authz-dual-track-plan](plans/2026-06-24-authz-dual-track-plan.md) — authz GitNexus 双轨（IDOR 候选+LLM 判定）🔧
- [inj-xss-ssrf-dual-track-plan](plans/2026-06-24-inj-xss-ssrf-dual-track-plan.md) — injection/xss/ssrf 双轨
- [recon-dual-track-plan](plans/2026-06-24-recon-dual-track-plan.md) / [pre-recon-dual-track-plan](plans/2026-06-24-pre-recon-dual-track-plan.md) — recon/pre-recon 双轨
- [framework-analyzer-wiring-plan](plans/2026-06-24-framework-analyzer-wiring-plan.md) — 框架分析器接线
- [dual-track-decoupling](plans/2026-06-27-dual-track-decoupling.md) / [spec](specs/2026-06-27-dual-track-decoupling-design.md) — 拆确定性→LLM 轨 prompt 注入 🔧

### GitNexus 轨
- [gitnexus-track-lifecycle-completion](plans/2026-06-27-gitnexus-track-lifecycle-completion.md) / [spec](specs/2026-06-27-gitnexus-track-lifecycle-completion-design.md) — GitNexus 轨生命周期（A1+A4 done，A2/A3/B open）📐
- [gitnexus-index-degradation-plan](plans/2026-06-24-gitnexus-index-degradation-plan.md) — 索引降级（detect_language 误判等）
- [gitnexus-intra-taint-deterministic-fallback](plans/2026-06-26-gitnexus-intra-taint-deterministic-fallback.md) / [spec](specs/2026-06-26-gitnexus-intra-taint-deterministic-fallback-design.md) — intra-taint 确定性 fallback（is_entry_hint 分层）🔧
- [gitnexus-llm-sink-discovery](plans/2026-06-26-gitnexus-llm-sink-discovery.md) / [spec](specs/2026-06-26-gitnexus-llm-sink-discovery-design.md) — 半 sink 模式 LLM 补召回 📐
- [taint-persist-plan](plans/2026-06-24-taint-persist-plan.md) — taint 落盘 ✅
- [injection-recall-port](plans/2026-06-25-injection-recall-port.md) / [spec](specs/2026-06-25-injection-recall-port-design.md) — injection 召回 port（跨服务全链 leak-free）🔧
- [llm-track-vuln-parity-restoration](plans/2026-06-28-llm-track-vuln-parity-restoration.md) / [spec](specs/2026-06-28-llm-track-vuln-parity-restoration-design.md) — LLM 轨 vuln 对齐 TS（max_turns/方法论补回）🔧

### 显示 UX
- [whitebox-display-clarity](plans/2026-06-16-whitebox-display-clarity.md) / [spec](specs/2026-06-16-whitebox-display-clarity-design.md) — 白盒 live 显示重设计 🔧
- [whitebox-live-step-intent-display](plans/2026-06-16-whitebox-live-step-intent-display.md) / [spec](specs/2026-06-16-whitebox-live-step-intent-display-design.md) — step intent 显示
- [rich-display-layout-fix](plans/2026-06-16-rich-display-layout-fix.md) / [spec](specs/2026-06-16-rich-display-layout-fix-design.md) — rich 布局修复
- [provider-agnostic-turn-logging](plans/2026-06-17-provider-agnostic-turn-logging.md) / [spec](specs/2026-06-17-provider-agnostic-turn-logging-design.md) — provider 无关逐轮日志 🔧
- [rich-log-visibility](plans/2026-06-19-rich-log-visibility.md) / [spec](specs/2026-06-19-rich-log-visibility-design.md) — rich 显示可见性 🔧
- [log-format-redesign](plans/2026-06-22-log-format-redesign.md) / [spec](specs/2026-06-22-log-format-redesign-design.md) — 日志格式重设计
- [log-label-alignment](plans/2026-06-23-log-label-alignment.md) / [spec](specs/2026-06-23-log-label-alignment-design.md) — 日志标签列对齐 🔧
- [report-render-stop-bleed](plans/2026-06-22-report-render-stop-bleed.md) — report 渲染停止 bleed（[spec](specs/2026-06-22-report-render-queue-format-fix.md)）
- [live-dashboard-ghost-frame-fix](plans/2026-06-25-live-dashboard-ghost-frame-fix.md) / [spec](specs/2026-06-25-live-dashboard-ghost-frame-fix-design.md) — dashboard 残影帧修复
- [chinese-comprehensive-report](plans/2026-06-22-chinese-comprehensive-report.md) / [spec](specs/2026-06-22-chinese-comprehensive-report-design.md) — 中文综合报告 🔧
- [display-ux-polish](plans/2026-06-27-display-ux-polish.md) / [spec](specs/2026-06-27-display-ux-polish-design.md) — 白盒显示 UX 优化 ✅
- [workflow-info-display-channel](plans/2026-06-28-workflow-info-display-channel.md) / [spec](specs/2026-06-28-workflow-info-display-channel-design.md) — workflow InfoEvent 显示通道 🔧
- [cli-workflow-failure-friendly-display](plans/2026-06-28-cli-workflow-failure-friendly-display.md) / [spec](specs/2026-06-28-cli-workflow-failure-friendly-display-design.md) — CLI workflow 失败友好展示 🔧

### 引擎 engine
- [openai-agents-engine](plans/2026-06-17-openai-agents-engine.md) / [spec](specs/2026-06-17-openai-agents-engine-design.md) — openai-agents 引擎（[smoke](plans/2026-06-17-openai-agents-engine-smoke.md)）
- [dual-engine-decoupling-fix](plans/2026-06-27-dual-engine-decoupling-fix.md) / [spec](specs/2026-06-27-dual-engine-decoupling-fix-design.md) — 双引擎解耦修复（契约硬化+语义对齐）🔧
- [blackbox-agent-browser-default-engine](plans/2026-06-28-blackbox-agent-browser-default-engine.md) / [spec](specs/2026-06-28-blackbox-agent-browser-default-engine-design.md) — 黑盒默认引擎切 agent-browser 🔧

### env / config
- [env-config-profiles](plans/2026-06-18-env-config-profiles.md) / [spec](specs/2026-06-18-env-config-design.md) · [anthropic-env-prefix](specs/2026-06-18-anthropic-env-prefix-design.md) — env profile 化 🔧
- [max-concurrent-env](plans/2026-06-22-max-concurrent-env.md) / [spec](specs/2026-06-22-max-concurrent-env-design.md) — 最大并发 env
- [token-caching](specs/2026-06-26-token-caching-design.md) — token 缓存（仅 spec）📐
- [remove-minimal-fallback-hard-fail](plans/2026-06-24-remove-minimal-fallback-hard-fail.md) / [spec](specs/2026-06-24-remove-minimal-fallback-hard-fail-design.md) — 移除 minimal fallback 硬失败

### resume / rerun
- [resume](plans/2026-06-18-resume.md) / [spec](specs/2026-06-18-resume-design.md) · [resume-and-rerun](specs/2026-06-19-resume-and-rerun-design.md) — resume/rerun 机制
- [whitebox-resume](plans/2026-06-19-whitebox-resume.md)（[smoke](plans/2026-06-19-whitebox-resume-smoke.md)）— 白盒 resume 🔧
- [blackbox-rerun](plans/2026-06-19-blackbox-rerun.md)（[smoke](plans/2026-06-19-blackbox-rerun-smoke.md)）— 黑盒 rerun 🔧

### deliverables / prompt
- [deliverables-to-session](plans/2026-06-21-deliverables-to-session.md) / [spec](specs/2026-06-19-deliverables-to-session-design.md) — deliverables 迁入 session
- [prompt-deliverables-migration](plans/2026-06-21-prompt-deliverables-migration.md) / [spec](specs/2026-06-21-prompt-deliverables-migration-design.md) — prompt deliverables 迁移
- [prompt-optimization](plans/2026-06-23-prompt-optimization.md) / [spec](specs/2026-06-23-prompt-optimization-design.md) — prompt 优化（authz IDOR/recon-static/manager 占位符）🔧
- [deliverables-git-isolation](plans/2026-06-22-deliverables-git-isolation.md) / [spec](specs/2026-06-22-deliverables-git-isolation-design.md) — deliverables git 隔离

### retry / 健壮性
- [retry-policy-alignment](plans/2026-06-22-retry-policy-alignment.md) / [spec](specs/2026-06-22-retry-policy-alignment-design.md) — retry policy 对齐 TS 🔧
- [glm-529-retry-resilience](specs/2026-06-22-glm-529-retry-resilience-design.md) — GLM 529 retry 韧性（仅 spec）
- [exploit-coverage-closure](plans/2026-06-22-exploit-coverage-closure.md) / [spec](specs/2026-06-22-exploit-coverage-closure-design.md) — exploit 覆盖收口

### 黑盒 / 跨仓
- [cross-repo-microservice-correlation](plans/2026-06-23-cross-repo-microservice-correlation.md) / [spec](specs/2026-06-22-cross-repo-microservice-scanning-design.md) — 跨仓微服务关联
- [blackbox-exploit-outcome-field-mapping](plans/2026-06-29-blackbox-exploit-outcome-field-mapping.md) / [spec](specs/2026-06-29-blackbox-exploit-outcome-field-mapping-design.md) — 黑盒 exploit AgentOutcome 字段映射 🔧

### audit / attribution
- [audit-session-agent-attribution](plans/2026-06-22-audit-session-agent-attribution.md) / [spec](specs/2026-06-22-audit-session-agent-attribution-design.md) — AuditSession 归因 race 🔧

### workspace
- [workspace-human-readable-timestamp](plans/2026-06-28-workspace-human-readable-timestamp.md) / [spec](specs/2026-06-28-workspace-human-readable-timestamp-design.md) — workspace 目录名人类可读化 🔧

### authz 演进
- [authz-attack-chain-confidence](specs/2026-06-17-authz-attack-chain-confidence-design.md) — authz 攻击链置信度（仅 spec）📐
- [authz-optimization-roadmap](specs/2026-06-17-authz-optimization-roadmap-design.md) — authz 优化路线图（仅 spec）📐

### 元 / 维护
- [superpowers-docs-archiving](plans/2026-06-29-superpowers-docs-archiving.md) / [spec](specs/2026-06-29-superpowers-docs-archiving-design.md) — 本目录的归档与索引导航（本次）📐

## 归档区

历史已完成工作位于 `specs/archive/`（54 个）与 `plans/archive/`（55 个），文件名日期 `≤ 2026-06-15`。按月概览：2025-06（2）/ 2026-05（14）/ 2026-06-01~06-15（93）。需要查阅时直接进入对应 archive 子目录按文件名查找。
````

- [ ] **Step 2: 验证 README 章节齐全**

```bash
grep -E "^## " docs/superpowers/README.md
```

Expected: 至少包含 `## 组织规则`、`## 活跃层（按主题主线）`、`## 归档区` 三节。

- [ ] **Step 3: 验证 README 内链接指向的文件存在（活跃层）**

```bash
cd docs/superpowers
# 提取 README 中所有 (plans/... 或 specs/... ) 相对链接，检查文件存在
grep -oE '\((plans|specs)/[^)]+\.md\)' README.md | tr -d '()' | while read p; do
  if [ ! -f "$p" ]; then echo "BROKEN LINK: $p"; fi
done
echo "链接检查完成(无 BROKEN LINK 输出=全部有效)"
```

Expected: 无 `BROKEN LINK` 输出。

- [ ] **Step 4: 暂不提交（统一在 Task 4 提交）**

---

### Task 4: 整体验收 + 提交

**Files:**
- 无新增/修改文件，仅验收 + commit Task 1-3 的成果

**Interfaces:**
- Consumes: Task 1/2/3 成果 + spec §8 验收标准
- Produces: 一个干净的提交（或两个：设计文档 + 归档执行）

- [ ] **Step 1: 验证文件守恒（无丢失/无内容修改，仅 rename + 新增 README）**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
echo "=== git diff --stat（应只含 rename + 新增 README/spec/plan）==="
git diff --stat HEAD
echo "=== git status 简表 ==="
git status --short | head -40
```

Expected: `git diff --stat` 仅显示 `=>` 重命名行（109 个）+ 新增 `docs/superpowers/README.md`（+ spec/plan 若未先提交）；**无任何既有文件的内容修改行**（即没有不带 rename 的 `|` 修改条目）。

- [ ] **Step 2: 验证 .md 文件总数守恒**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
echo "specs 总: $(find docs/superpowers/specs -name '*.md' | wc -l | tr -d ' ') (顶层+archive)"
echo "plans 总: $(find docs/superpowers/plans -name '*.md' | wc -l | tr -d ' ') (顶层+archive)"
echo "README 存在: $([ -f docs/superpowers/README.md ] && echo yes || echo no)"
```

Expected: specs 总 98（原 97 + archiving-design spec）、plans 总 106（原 105 + archiving plan）、README yes。

- [ ] **Step 3: 抽查 git mv 历史可追溯**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
# 任取一个归档 spec，确认 --follow 能追到移动前历史
git log --follow --oneline -1 docs/superpowers/specs/archive/2026-05-27-shannon-py-whitebox-design.md
```

Expected: 输出一行 commit（证明历史保留，非删除+新增）。

- [ ] **Step 4: 提交设计文档（spec + 本 plan）**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add docs/superpowers/specs/2026-06-29-superpowers-docs-archiving-design.md
git commit -m "docs(spec): docs/superpowers 目录归档与索引导航设计"
git add docs/superpowers/plans/2026-06-29-superpowers-docs-archiving.md
git commit -m "docs(plan): docs/superpowers 目录归档与索引导航实现计划"
```

- [ ] **Step 5: 提交归档执行 + README**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git add docs/superpowers/specs/archive docs/superpowers/plans/archive docs/superpowers/README.md
git status --short | wc -l   # 确认所有 rename + README 已暂存
git commit -m "docs(superpowers): 归档 ≤2026-06-15 历史 spec/plan + 新增索引导航 README"
```

- [ ] **Step 6: 最终核对**

```bash
cd /Users/mango/project/shannon-refactor/shannon-py
git log --oneline -3
git status --short   # 应为 clean
```

Expected: 最近 3 个 commit 为 archiving 的 spec/plan/执行；工作区 clean。
