# 白盒 Resume + 黑盒重跑 设计

- **状态**: 设计稿（待评审）
- **日期**: 2026-06-19
- **范围**: 白盒扫描的断点续传（resume）+ 黑盒扫描的整体重跑（rerun）

---

## 1. 背景与问题

### 1.1 白盒：空壳守卫导致中断后从头跑

`WhiteboxScanWorkflow` 用 `PipelineState.completed_agents` 做"跳过已完成 agent"的守卫（`workflows.py:131,213,279`）。但 `self._state` 是 workflow 的**内存状态**，每次 `__init__`/`run` 都从空开始（`workflows.py:33-34`）。后果：守卫永远判定"未完成"，任何中断（worker 崩溃 / Ctrl+C / 网络断 / 机器重启）后重跑，**所有 agent 从头执行**，昂贵的 pre-recon / recon（各 ~2h）被无谓重跑。`models/base.py:14` 的 `resume_from_workspace` 字段、CLI `-w` 的 "resume if exists" 帮助文本都是未接通的虚假信号。

### 1.2 黑盒：跑完后无法干净地重跑

`BlackboxScanWorkflow` 是独立的 workflow / CLI / 包，通过 `{vt}_exploitation_queue.json` 消费白盒产出物。跑完一次白盒+黑盒后，想**基于已有白盒结果重跑黑盒**（换配置、目标环境变化、重新验证），当前有两个障碍：

1. **workflow id 冲突**：`worker.py:86` 用 `workspace_name` 做 workflow id，重跑直接 `WorkflowExecutionAlreadyStartedError`。
2. **无幂等检测**：再跑一次黑盒不会告知"已经跑过、有结果"，可能误触发重复打目标（黑盒 exploit 非幂等，有真实副作用）。

---

## 2. 两个独立功能（不做统一框架）

经澄清，这两个需求**本质不同**，独立设计，**不抽统一框架**：

| | 白盒 Resume | 黑盒 Rerun |
|---|---|---|
| 本质 | **断点续传**：跳过已完成，从中断点继续 | **整体重跑**：基于已有白盒结果，整个黑盒从头跑一遍 |
| 为什么 | 白盒昂贵且**确定性强**（分析代码），续传价值高 | 黑盒**非幂等、非确定性**（真打目标），断点续传复杂且价值低；整遍重跑更简单 |
| 进度信号 | git checkpoint + session + 文件（agent 级） | session 黑盒完成状态 + evidence 存在性（整体级） |
| 交集 | 仅"workflow id 规避冲突"这个小逻辑，不值得抽框架 | — |

> 之前一度设想"core 统一 resume 骨架 + ResumeSource 适配器"，因黑盒改为整体重跑、不做断点续传而**作废**——只有一个实现（白盒）的抽象是 YAGNI。

---

## 3. 白盒 Resume 设计（断点续传）

### 3.1 核心思路：重建 `completed_agents` 激活现有空壳守卫

> 不新造续传机制，而是在启动 workflow 之前，从磁盘把已有进度读回来、对账成可信的 `completed_agents`，通过 workflow input 灌进去。现有"跳过已完成 agent"守卫因此自然激活。改动集中在"开机重建状态"，workflow 内部跳过逻辑基本不动。

不依赖 Temporal workflow history 存活（覆盖"连 Temporal 也丢了"的最坏情况）。

### 3.2 对账信号与决策表（git 为主）

对一个 agent A，三个信号：

- **G** = git log 里的 `deliverable: {agent}` commit（`git_manager.py:108`，agent 成功的最后一道原子关）
- **J** = session.json `metrics.agents[A].status == "success"`
- **F** = A 的产出物文件在磁盘存在

判定"完成"的充要条件 = **`G ∧ F`**：

| G | J | F | 判定 | 处理 |
|---|---|---|------|------|
| ✓ | ✓ | ✓ | 正常完成 | ✅ 进 `completed_agents` |
| ✓ | ✗ | ✓ | session 落盘晚 | ✅ 算完成，`warn`（以 G 为准） |
| ✓ | ✓ | ✗ | 文件被误删 | 🔴 **中止** |
| ✓ | ✗ | ✗ | G 有、文件和 session 都没有 | 🔴 **中止** |
| ✗ | ✓ | ✓ | session 误记 success | ⚠️ 不算完成，重跑 + `warn` |
| ✗ | ✓ | ✗ | session 误记 | ⚠️ 重跑 + `warn` |
| ✗ | ✗ | ✓ | 文件在但无 G（半成品/旧残留） | ⚠️ 重跑 + `warn` |
| ✗ | ✗ | ✗ | 未跑过 | 正常重跑 |

原则：没有 G 一律不算完成（J/F 顶多发 warning）；`G ∧ ¬F` 是唯一中止情形（不能静默重跑，文件丢失说明外部出过事，结果已不可信）；其余 `¬G` 一律重跑。

### 3.3 WhiteboxResumeStateBuilder（双源对账）

放 `packages/whitebox/.../pipeline/whitebox_resume.py`（只白盒用，不进 core）。

```python
@dataclass
class WhiteboxResumeState:
    mode: Literal["auto", "rewind", "fresh"]
    completed_agents: list[str]
    interrupted_agent: str | None
    base_commit: str | None          # git reset 目标
    warnings: list[str]
    resume_attempt: int              # workflow id resume 计数
    aborted: bool
    abort_reason: str | None

class WhiteboxResumeStateBuilder:
    def build(self, workspace, repo_path, mode, rewind_target) -> WhiteboxResumeState: ...
```

### 3.4 中断定位 + 清理（串行 vs 并行）

- **中断定位**：git log 最后一个 `deliverable:` 之后若挂 `checkpoint: before X`（无对应 deliverable）→ X 中断；否则中断点 = 编排顺序里下一个 agent。
- **清理（串行阶段）**：`git reset --hard` 到 X 的 `checkpoint: before X` commit（丢弃 X 半成品）。
- **清理（并行阶段，如 vuln）**：**不做整体 git reset**——线性 commit 链一 reset 会误伤同阶段已完成 agent 的 deliverable。改为仅删除 `¬G` agent 的产出物文件让其重跑生成，`G` agent 的产出物保留供守卫跳过。（并行 checkpoint 序列化行为须前置核实，见 §6.3）

### 3.5 `--rewind <phase>` 阶段级回退

阶段 → 起点映射（实现时按编排精确核实）：

| `--rewind <phase>` | 起点 agent | 重跑范围 |
|---|---|---|
| `pre-recon` | `code-index` | pre-recon 整段及之后全部 |
| `recon` | `recon` | recon 及之后全部 |
| `vuln` | injection 组 | 全部 5 个并行 vuln agent 及之后 |
| `attack-chain` | `attack-chain-assembly` | attack-chain + report |
| `report` | `render-findings` | 仅最终报告 |

rewind 对账（auto 规则变体）：`completed_agents` = 编排顺序里**严格在阶段起点之前**且 `G ∧ F` 的 agent；阶段起点及之后一律视为未完成、重跑；`git reset --hard` 到阶段起点的 checkpoint。**安全备份（默认开）**：reset 前打 tag `rewind-backup-<phase>-<n>`，可撤销；即便不打 tag，被丢弃的 commit 仍在 git reflog（默认保留 90 天）。

### 3.6 CLI flag 语义（互斥）

| 用户输入 | 行为 |
|---|---|
| `--fresh` | 全新扫，忽略一切历史，workflow id 不带 resume 计数 |
| `--rewind <phase>` | 基于已有 workspace 回退到指定阶段重跑（要求有历史） |
| 都不加 + 有未完成 session | 自动 resume（从断点） |
| 都不加 + 无 / 已完成 session | 全新扫 |

`--fresh` 与 `--rewind` 互斥，同时传报错。

### 3.7 workflow id resume 计数

白盒 worker 已有 `resolve_workflow_id`（带时间戳），扩展为 **`<workspace>-resume-{n}`**，`n` = `session.json resumeAttempts + 1`。旧 workflow 残留在 Temporal 不主动清理，靠无 worker poll 自然超时。

### 3.8 session.json completed_agents 读写

- **写**：每个 agent 成功后**增量落盘** `completed_agents`（当前 `MetricsTracker.end_agent` 写 `metrics.agents` 但不写 `completed_agents`；本设计新增写入）。
- **读**：Builder 启动时读 session.json，作为对账的 J 信号 + `resume_attempt` 来源。
- `resumeAttempts`（`add_resume_attempt`）首次真正被调用。

---

## 4. 黑盒 Rerun 设计（整体重跑）

### 4.1 默认行为：幂等检测

跑黑盒前先检测是否已跑过黑盒：

- **已跑过（有结果）**→ 告知用户"该 workspace 已跑过黑盒，结果在 X；如需重跑请加 `--rerun`"，**不重复跑**（避免误触发重复打目标）。
- **没跑过** → 正常跑黑盒（消费已有白盒 queue）。

### 4.2 `--rerun` 强制重跑

`shannon-blackbox start --rerun`：即使已有结果，强制重跑整个黑盒。

### 4.3 归档目录（时间后缀保留历史）

重跑时**不清掉也不覆盖**，而是把当前黑盒产出物整体归档：

- 把 `deliverables/` 下的黑盒产出物（`{vt}_exploitation_evidence.md` × 5 + `comprehensive_security_assessment_report.md` + `{vt}_findings.md`）移到 `deliverables/.blackbox-archive/<run-ts>/`（`run-ts` = 本次重跑运行时间戳）。
- 新结果放回 `deliverables/` 顶层。
- 顶层永远是**最新完整结果**（读 evidence 的路径不用改）；`.blackbox-archive/` 里是历史重跑记录，可追溯对比两次黑盒结果。

### 4.4 workflow id 时间戳

修 `worker.py:86`：workflow id 带运行时间戳（`<workspace>-blackbox-<ts>`），规避 `AlreadyStarted`，每次重跑独立 workflow。

### 4.5 幂等检测信号

- **主信号**：session.json 黑盒 workflow 完成状态（黑盒跑完写 `completed`）。前置核实黑盒是否已写 session status。
- **辅助**：`{vt}_exploitation_evidence.md` 文件存在性。
- 任一表明"已跑过" → 触发 §4.1 的告知逻辑。

---

## 5. 测试策略（分层，避开 Temporal 全量 hang）

> 项目约束：pytest 跑全量 / 全包会卡在 Temporal 慢测试。自动化验证**不依赖真起 Temporal server**。

1. **单元测试（主力，纯离线）**：
   - 白盒 `test_whitebox_resume_builder.py`：临时 git 仓库 + session.json + 产出物文件，`parametrize` 决策表 8 种 G/J/F 组合 + 中断定位 + rewind 阶段过滤 + 并行 cleanup。
   - 黑盒 `test_blackbox_rerun.py`：幂等检测（已跑过/没跑过）、归档目录生成、`--rerun` 行为。
2. **守卫激活测试**：验证白盒"预填 `completed_agents` → 守卫跳过"。⚠️ 已知陷阱：dispatch 用 `isinstance`，`MagicMock + .type` 会"测试绿生产坏"，mock 须按真实 SDK 事件形态。
3. **E2E（人工冒烟，不进默认 pytest）**：
   - 白盒：跑到 pre-recon 完成 → kill → resume → 验证从 recon 起。
   - 黑盒：跑完黑盒 → `--rerun` → 验证旧 evidence 归档、新结果在顶层、workflow 不冲突。

---

## 6. 改动清单

### 6.1 白盒 Resume

| 文件 | 改动 |
|---|---|
| `packages/whitebox/.../pipeline/whitebox_resume.py` 🆕 | `WhiteboxResumeState` + `WhiteboxResumeStateBuilder`（双源对账 + 中断定位 + 串行/并行清理） |
| `packages/whitebox/.../pipeline/workflows.py` | `WhiteboxScanWorkflow.run` 从 input 预填 `completed_agents`（守卫不动）；补齐 vuln 并行守卫（若缺） |
| `packages/whitebox/.../worker.py` | start_workflow 前调 Builder；workflow id 加 `-resume-{n}` |
| `packages/whitebox/.../cli/main.py` | 加 `--fresh` / `--rewind <phase>` |
| `packages/core/.../audit/metrics_tracker.py` | agent 完成后增量写 `completed_agents`；接通 `add_resume_attempt` |
| `tests/.../test_whitebox_resume_builder.py` 🆕 | 决策表 parametrize 测试 |

### 6.2 黑盒 Rerun

| 文件 | 改动 |
|---|---|
| `packages/blackbox/.../pipeline/blackbox_rerun.py` 🆕 | 幂等检测 + 归档目录（移旧 evidence 到 `.blackbox-archive/<run-ts>/`） |
| `packages/blackbox/.../worker.py` | workflow id 带运行时间戳（修 `worker.py:86`） |
| `packages/blackbox/.../cli/main.py` | 加 `--rerun` flag；默认走幂等检测 |
| `packages/blackbox/.../pipeline/workflows.py` | 黑盒跑完写 session completed status（若未写） |
| `tests/.../test_blackbox_rerun.py` 🆕 | 幂等检测 + 归档测试 |

### 6.3 前置核实项（实现前必须确认）

1. 白盒 vuln 阶段并行 agent 是否**都有**独立 `if X not in completed_agents` 守卫——缺则补齐是 resume 前置。
2. 白盒各阶段 → 起点 agent 的精确映射（§3.5 表格核实版）。
3. `MetricsTracker.end_agent` 当前落盘时机（确认是 agent 完成时而非 workflow 结束）。
4. workflow input 结构能否扩展携带 `completed_agents`——能则走 input 预填；不能则改为 `run` 开头调 `load_resume_state` activity。
5. **白盒并行阶段（vuln）的 git checkpoint 序列化行为**——并行 agent 如何提交到同一 commit 链；决定并行 cleanup 用"删文件"还是"reset 到阶段入口"。
6. 黑盒是否已写 session completed status（决定 §4.5 是否需新增）。

---

## 7. 风险登记

1. **vuln 并行守卫可能不齐** → resume 在该阶段会重跑。前置核实 + 补齐（小改动）。
2. **`git reset --hard` 破坏性 + 并行误伤**（白盒）→ 串行阶段只 reset 到 interrupted agent 的 checkpoint；**并行阶段禁用整体 reset**（误伤同阶段已完成 deliverable），改用"删 `¬G` 文件"。并行 checkpoint 序列化行为须前置核实。
3. **Temporal 旧 workflow 残留** → resume 计数 id / 黑盒时间戳 id 规避 `AlreadyStarted`；旧 workflow 无 worker poll 自然超时，不主动清理。
4. **session.json 落盘时机** → 若当前是 workflow 结束才整体写，崩溃后 `metrics.agents` 缺；白盒 Phase 改为 agent 完成即增量写，是 resume 可靠性关键依赖。
5. **黑盒重跑的副作用** → `--rerun` 会重新打目标（非幂等）；幂等检测默认防呆（已跑过不重复），`--rerun` 是用户显式确认。归档保留历史可追溯。
6. **黑盒归档目录膨胀** → 多次 `--rerun` 会在 `.blackbox-archive/` 堆积；暂不自动清理（YAGNI），必要时加保留策略。

---

## 8. 分阶段实现计划

**Phase 1 — 白盒 Resume**：前置核实 → `WhiteboxResumeStateBuilder`（双源对账）→ workflow/worker/CLI 接通（`--fresh` / `--rewind` / 自动）→ `MetricsTracker.completed_agents` 落盘 → 单元测试 → 人工冒烟。

**Phase 2 — 黑盒 Rerun**：幂等检测 → `--rerun` + 归档目录 → workflow id 时间戳 → session completed status → 单元测试 → 人工冒烟。

每个 Phase 内部按"前置核实 → 实现 → 接通 → 单元测试 → 人工冒烟"推进。详细步骤由后续 writing-plans 产出。
