# 统一 Resume 设计（白盒 + 黑盒）

- **状态**: 设计稿（待评审）
- **日期**: 2026-06-19
- **范围**: 为白盒扫描与黑盒扫描提供统一的断点续传（resume）能力

---

## 1. 背景与问题

### 1.1 共同病根：空壳守卫

白盒 `WhiteboxScanWorkflow` 和黑盒 `BlackboxScanWorkflow` 都用 `PipelineState.completed_agents` 做"跳过已完成 agent"的守卫：

- 白盒 `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:131,213,279`：
  `if AgentName.PRE_RECON.value not in self._state.completed_agents:`
- 黑盒 `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:155,205,301`：同形态守卫

但 `self._state`（`PipelineState`）是 workflow 的**内存状态**，每次 `__init__`/`run` 都从空开始（白盒 `workflows.py:33-34`，黑盒 `workflows.py:38-39`）。后果：

- 守卫永远判定"未完成"
- 任何中断（worker 崩溃 / Ctrl+C / 网络断 / 机器重启）后重跑，**所有 agent 从头执行**
- 昂贵的 pre-recon / recon（白盒，各 ~2h）和 exploit（黑盒，真实打目标）被无谓重跑
- `models/base.py:14` 的 `resume_from_workspace` 字段、`audit/metrics_tracker.py:138` 的 `add_resume_attempt` 方法、CLI `-w` 的 "resume if exists" 帮助文本——**都是未接通的虚假信号**

### 1.2 两个目标场景

**场景 A — 白盒阶段性 resume**：白盒扫描中断后重跑，跳过已完成的 agent，从中断点继续；并支持 `--rewind <phase>` 手动回退到任意阶段重跑。

**场景 B — 黑盒网络中断重跑**：基于白盒结果运行黑盒扫描（黑盒通过 `{vt}_exploitation_queue.json` 消费白盒产出物，是独立 workflow / CLI / 包），网络中断后重跑黑盒，跳过已完成的 exploit agent，只重跑中断的。

### 1.3 为什么统一

两个场景**同病同治**——resume 骨架完全一致：

> 启动时从持久化源重建 `completed_agents` → 通过 workflow input 预填 `self._state` → 现有空壳守卫自然激活。

`packages/core` 本就是白盒/黑盒的共享层（`PipelineState` / `SessionManager` / `MetricsTracker` / `AgentExecutor` / `GitManager` 均在 core）。resume 是**跨白盒/黑盒的横切关注点**，放 core 天然合理。统一实现避免重复 + 行为漂移；分开做意味着对账 / CLI flag / workflow id / session 落盘各写两遍。

---

## 2. 目标与非目标

### 目标

- 白盒 + 黑盒统一 resume：中断后重跑跳过已完成 agent，从中断点继续
- 白盒 `--rewind <phase>` 阶段级回退 + `--fresh` 全新扫
- 黑盒 auto resume（网络中断重跑），workflow id 规避 `AlreadyStarted` 冲突
- **不依赖 Temporal workflow history 存活**（全量重建，覆盖"连 Temporal 也丢了"的最坏情况）
- 保守正确性：宁可中止也不静默丢结果

### 非目标（YAGNI）

- agent **内部**续传（recon 在 95% 崩溃仍需重跑整个 recon；LLM 对话状态不可靠持久化，ROI 低）
- 黑盒 `--rewind`（`ResumeSource` 接口在架构上预留支持，黑盒 rewind 语义作为后续扩展）
- 主动清理 Temporal 里残留的旧 workflow（靠 resume 计数 id 规避 + 无 worker poll 自然超时）
- 黑盒请求去重 / 幂等改造（resume 只缩小重跑范围，不改变 exploit 本身的非幂等性）

---

## 3. 统一架构

```
core/resume/                                ← 共享骨架
  ResumeState          (dataclass)          重建结果
  ResumeSource         (接口 / Strategy)     进度信号源
  ResumeStateBuilder   (对账框架)            CLI flag 解析 + 对账 + workflow id resume 计数
                                            + session.json completed_agents 读写

whitebox/
  WhiteboxResumeSource(ResumeSource)        git checkpoint 为主 + session + 文件 → 双源对账（强）

blackbox/
  BlackboxResumeSource(ResumeSource)        有 repo → git；无 repo → done marker + session + evidence
```

**骨架共享**（对账框架 / CLI flag / workflow id / session 读写）；**进度信号源**由各包以 Strategy 实现。

### 3.1 ResumeState（dataclass）

```python
@dataclass
class ResumeState:
    mode: Literal["auto", "rewind", "fresh"]
    completed_agents: list[str]          # 已确认完成、应跳过的 agent
    interrupted_agent: str | None        # 中断点 / rewind 起点（从这里重跑）
    base_commit: str | None              # 白盒 git reset 目标；黑盒为 None
    warnings: list[str]                  # 对账不一致告警
    resume_attempt: int                  # workflow id resume 计数（n）
    aborted: bool                        # 是否中止（见 §5）
    abort_reason: str | None
```

### 3.2 ResumeSource 接口

```python
class ResumeSource(Protocol):
    def agent_order(self) -> list[str]:
        """编排顺序，用于判定 X 之前 / 中断点。"""
    def authoritative_completed(self) -> set[str]:
        """权威正信号 G（白盒=git deliverable commit；黑盒=done marker / git）。"""
    def session_completed(self) -> set[str]:
        """session.json metrics.agents[*].status=success（J）。"""
    def file_exists(self, agent: str) -> bool:
        """产出物文件是否在磁盘（F）。"""
    def locate_interrupted(self, completed: set[str]) -> str | None: ...
    def cleanup_partial(self, interrupted: str) -> None:
        """清理中断 agent 的半成品（白盒 git reset；黑盒删 evidence）。"""
```

---

## 4. 详细设计

### 4.1 对账决策表（统一规则）

对一个 agent A，三个信号：

- **G** = 权威"完成"正信号（来源由 `ResumeSource` 决定，见 §4.2 / §4.3）
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

核心原则：

- 没有 G 一律不算完成（session/file 顶多发 warning）——G 是 agent 跑完的最后一道原子关，最严格。
- `G ∧ ¬F` 是唯一的中止情形——不能静默重跑，因为文件丢失说明外部出过事，结果已不可信，必须让人介入。
- 其余 `¬G` 一律重跑。

### 4.2 WhiteboxResumeSource（双源对账，git 为主）

- **G** = git log 里的 `deliverable: {agent}` commit（`git_manager.py:108` 的 commit message 格式）
- 对账：git 为权威，session.json + 文件作校验，不一致以 git 为准并 `warn`
- **中断定位**：git log 最后一个 `deliverable:` 之后若挂 `checkpoint: before X`（无对应 deliverable）→ X 中断；否则中断点 = 编排顺序里下一个 agent
- **清理（串行阶段）**：`git reset --hard` 到 X 的 `checkpoint: before X` commit（丢弃 X 的半成品）
- **清理（并行阶段，如 vuln）**：**不做整体 git reset**——线性 commit 链一 reset 会误伤同阶段已完成 agent 的 deliverable。改为仅删除 `¬G` agent 的产出物文件让其重跑生成，`G` agent 的产出物保留供守卫跳过。并行 checkpoint 的序列化行为须前置核实（§7）。
- 可靠性高：git commit 是 agent 成功的最后一道原子操作，commit 链天然有序

### 4.3 BlackboxResumeSource（自适应：有 repo 用 git，无 repo 用 done marker）

黑盒无统一的 git checkpoint（纯黑盒无 `--repo` 时 deliverables 不在 git 仓库，`git_manager.py:77-79` 会 skip checkpoint）。因此 G 信号**自适应**：

- **有 repo_path（复用白盒 workspace）**：exploit agent 走 `AgentExecutor` 会做 git checkpoint → **G = git `deliverable:` commit**，对账方式同白盒
- **无 repo_path（纯黑盒）**：**新增 `{vt}_exploit_done.json` marker**，exploit agent 成功且通过 `validate_deliverable` 后由 executor 写入 → **G = done marker 存在**

> 为什么需要 done marker：黑盒产出物 `{vt}_exploitation_evidence.md` 内容**非确定性**（取决于网络交互），"文件存在"不能证明 agent 完整完成（可能写了半截）。纯黑盒无 git 兜底，故需一个明确的"完成"正信号。done marker 在 validate 通过后才写，是 agent 成功的最后一道原子关，等价于白盒的 git commit。

- 对账：G 为权威，session.json `metrics.agents[*].status` + evidence 文件作校验
- **中断定位**：exploit agent 并行执行；所有 `¬G` 的 exploit agent 都需重跑（`completed_agents` 含所有 `G` 的，workflow 守卫自动跳过已完成的）
- **清理**：删除每个 `¬G` agent 的 `{vt}_exploitation_evidence.md`（无 git reset，直接删半成品 evidence 让重跑生成；`G` agent 的 evidence 保留）
- **网络中断特例（重要边界）**：resume 只保证"不重跑已完成的 exploit agent"，**不保证目标网络已恢复**。重跑的中断 agent 若目标仍不可用会继续失败——这是 exploit 的固有风险；resume 的净收益在于**缩小重跑范围**（只重跑中断的，而非全部）。

### 4.4 CLI flag 语义（互斥，按优先级）

| 用户输入 | 行为 |
|---|---|
| `--fresh` | 全新扫，忽略一切历史，workflow id 不带 resume 计数 |
| `--rewind <phase>`（白盒） | 基于已有 workspace，回退到指定阶段重跑（要求该 workspace 有历史） |
| 都不加 + 有未完成 session | 自动 resume（从断点） |
| 都不加 + 无 / 已完成 session | 全新扫 |

`--fresh` 与 `--rewind` 互斥，同时传报错。

### 4.5 workflow id resume 计数

- 白盒 worker 已有 `resolve_workflow_id`（带时间戳），扩展为 **`<workspace>-resume-{n}`**，`n` = `session.json resumeAttempts + 1`
- 黑盒 `worker.py:86` 当前用 `workspace_name` 做 id（重跑会 `WorkflowExecutionAlreadyStartedError`），补同样的 resume 计数
- 旧 workflow 残留在 Temporal **不主动清理**，靠无 worker poll 自然超时终止

### 4.6 session.json completed_agents 读写

- **写**：每个 agent 成功后，**增量落盘** `completed_agents`（当前 `MetricsTracker.end_agent` 写 `metrics.agents` 但不写 `completed_agents`；本设计新增 `completed_agents` 字段的写入）
- **读**：`ResumeStateBuilder` 启动时读 session.json，作为对账的 J 信号 + `resume_attempt` 来源
- `resumeAttempts`（`add_resume_attempt`）首次真正被调用，记录每次 resume 的 workflow id / 终止的 agent / checkpoint

### 4.7 rewind 规则（白盒，阶段级）

阶段 → 起点映射（实现时按编排精确核实）：

| `--rewind <phase>` | 起点 agent | 重跑范围 |
|---|---|---|
| `pre-recon` | `code-index` | pre-recon 整段及之后全部 |
| `recon` | `recon` | recon 及之后全部 |
| `vuln` | injection 组 | 全部 5 个并行 vuln agent 及之后 |
| `attack-chain` | `attack-chain-assembly` | attack-chain + report |
| `report` | `render-findings` | 仅最终报告 |

rewind 对账（auto 规则的变体）：

- `completed_agents` = 编排顺序里**严格在阶段起点之前**且 `G ∧ F` 的 agent
- 阶段起点及之后，**无论 git/session 状态，一律视为未完成、重跑**
- `git reset --hard` 到阶段起点的 `checkpoint: before <起点>` commit
- **安全备份（默认开）**：reset 前打 tag `rewind-backup-<phase>-<n>`，rewind 可撤销；即便不打 tag，被丢弃的 commit 仍在 git reflog（默认保留 90 天）

---

## 5. 错误处理 / abort 边界

resume **绝不静默退化成全新扫**——遇到异常停下，让人介入：

- `G ∧ ¬F`（权威信号说完成但文件丢了）→ 中止
- git log 解析失败 / deliverables 仓库损坏 → 中止
- session.json `repoPath` 与当前 repo 不符 → 中止（workspace 名撞车但不是同一仓库）
- session.json `status == "completed"` → 不 resume，提示"已扫完，用新 workspace 或 `--fresh`"
- `--rewind` 到从未执行的阶段（无 checkpoint 痕迹）→ 报错"无历史可回退"
- `--rewind` 到 X，但 X 之前有 agent 不满足 `G ∧ F` → 报错"X 之前的 Y 未完成，建议 `--rewind Y` 或更早"
- 黑盒纯模式无 repo 且 done marker 体系未就绪 → 报错（不静默退化为"全部重跑"）

---

## 6. 测试策略（分层，避开 Temporal 全量 hang）

> 项目约束：pytest 跑全量 / 全包会卡在 Temporal 慢测试。resume 的自动化验证**不依赖真起 Temporal server**。

1. **单元测试（主力，纯离线）** — `test_resume_state_builder.py`：临时 git 仓库 + 临时 session.json + 临时产出物文件，`parametrize` 决策表全部 8 种 G/J/F 组合，逐一断言判定 / warning / abort；再测中断定位、`base_commit` 计算、rewind 阶段过滤。秒级、不碰 Temporal。
2. **守卫激活测试（链路验证）** — 验证"预填 `completed_agents` → workflow 守卫真的跳过"。mock 掉 agent 执行，只测编排跳过。⚠️ 已知陷阱：dispatch 用 `isinstance`，测试用 `MagicMock + .type` 会"测试绿生产坏"——mock 须按真实 SDK 事件形态构造。
3. **E2E resume** — 真 repo 跑到某阶段 → kill → resume → 验证从断点起。依赖 Temporal，**归入"人工冒烟"**（与项目其他 feature 一致），不进默认 pytest。
   - 白盒：跑到 pre-recon 完成 → kill → resume → 验证从 recon 起
   - 黑盒：跑到部分 exploit 完成 → 模拟网络中断 → resume → 验证只重跑未完成的 exploit

---

## 7. 改动清单

### Phase 1：core 骨架 + 白盒 source

| 文件 | 改动 |
|---|---|
| `packages/core/src/shannon_core/resume/__init__.py` 🆕 | 包入口 |
| `packages/core/src/shannon_core/resume/state.py` 🆕 | `ResumeState` dataclass |
| `packages/core/src/shannon_core/resume/source.py` 🆕 | `ResumeSource` 接口 |
| `packages/core/src/shannon_core/resume/builder.py` 🆕 | `ResumeStateBuilder`：对账框架 + CLI flag 解析 + workflow id resume 计数 + session 读写 |
| `packages/whitebox/.../pipeline/whitebox_resume_source.py` 🆕 | `WhiteboxResumeSource`（git 为主双源对账） |
| `packages/whitebox/.../pipeline/workflows.py` | `WhiteboxScanWorkflow.run` 从 input 预填 `completed_agents`（守卫不动） |
| `packages/whitebox/.../worker.py` | start_workflow 前调 builder；workflow id 加 `-resume-{n}` |
| `packages/whitebox/.../cli/main.py` | 加 `--fresh` / `--rewind <phase>` |
| `packages/core/.../audit/metrics_tracker.py` | agent 完成后增量写 `completed_agents`；接通 `add_resume_attempt` |
| `tests/.../test_resume_state_builder.py` 🆕 | 决策表 parametrize 测试 |
| （前置）`workflows.py` vuln 阶段 | 核实并行 agent 守卫齐全性，缺则补 |

### Phase 2：黑盒 source

| 文件 | 改动 |
|---|---|
| `packages/blackbox/.../pipeline/blackbox_resume_source.py` 🆕 | `BlackboxResumeSource`（有 repo→git；无 repo→done marker） |
| `packages/blackbox/.../agents/exploit_executor.py` | exploit 成功 + validate 通过后写 `{vt}_exploit_done.json` marker |
| `packages/blackbox/.../pipeline/workflows.py` | `BlackboxScanWorkflow.run` 从 input 预填 `completed_agents`（守卫不动） |
| `packages/blackbox/.../worker.py` | start_workflow 前调 builder；workflow id 加 `-resume-{n}`（当前 `worker.py:86` 会冲突） |
| `packages/blackbox/.../cli/main.py` | 加 `--fresh`（黑盒 rewind 暂不做） |
| `tests/.../test_blackbox_resume_source.py` 🆕 | done marker / session / evidence 对账测试 |

### Phase 1 实现前必须核实的前置项

1. 白盒 vuln 阶段并行 agent（injection/xss/auth/ssrf/authz）是否**都有**独立 `if X not in completed_agents` 守卫——缺则补齐是 resume 前置
2. 白盒各阶段 → 起点 agent 的精确映射（§4.7 表格的核实版）
3. `MetricsTracker.end_agent` 当前落盘时机（确认是 agent 完成时而非 workflow 结束）
4. workflow input 结构能否扩展携带 `completed_agents`——能则走 input 预填；不能则改为 `run` 开头调 `load_resume_state` activity（两方案任一均可，实现时定）
5. **并行阶段（vuln / exploit）的 git checkpoint 序列化行为**——并行 agent 如何提交到同一 commit 链、reset 到阶段入口的精确 commit 如何定位；决定并行 cleanup 用"删文件"还是"reset 到阶段入口"

---

## 8. 风险登记

1. **vuln 并行阶段守卫可能不齐** → 若缺独立守卫，resume 在该阶段会重跑。前置核实 + 补齐（小改动）。
2. **`git reset --hard` 破坏性 + 并行误伤**（白盒）→ 串行阶段只 reset 到 interrupted agent 的 checkpoint（丢弃其半成品，本就要重跑），`repoPath` 校验 + `base_commit` 正确即安全，rewind 额外打 tag 可撤销；**并行阶段（vuln）禁用整体 reset**（线性 commit 链会误伤同阶段已完成 agent 的 deliverable），改用"删 `¬G` 文件"。并行 checkpoint 序列化行为须前置核实（§7）。
3. **黑盒网络中断后目标仍不可用** → resume 不解决"目标恢复"，只解决"不重跑已完成的"；中断 agent 重跑可能继续失败，属 exploit 固有风险。
4. **黑盒非幂等副作用** → resume 只重跑未完成 agent，是相对"全部重跑"的净收益；不改造 exploit 幂等性。
5. **Temporal 旧 workflow 残留** → resume 计数 id 规避 `AlreadyStarted`；旧 workflow 无 worker poll 自然超时，不主动清理。
6. **session.json 落盘时机** → 若当前是 workflow 结束才整体写，崩溃后 `metrics.agents` 缺；Phase 1 改为 agent 完成即增量写，是 resume 可靠性的关键依赖。
7. **黑盒 done marker 与 evidence 不一致** → done marker 仅在 validate 通过后写，evidence 文件先于 marker 落盘；若 marker 写入前崩溃，`¬G` 判定重跑（符合预期，evidence 被清理后重新生成）。

---

## 9. 分阶段实现计划

**Phase 1 — core 骨架 + 白盒 source**：建立 `core/resume` 共享骨架（`ResumeState` / `ResumeSource` / `ResumeStateBuilder`），实现 `WhiteboxResumeSource`（git 双源对账），接通白盒 workflow / worker / CLI（`--fresh` / `--rewind` / 自动），补 `MetricsTracker.completed_agents` 落盘。用白盒验证骨架跑通。

**Phase 2 — 黑盒 source**：实现 `BlackboxResumeSource`（自适应 git / done marker），新增 exploit done marker 写入，接通黑盒 workflow / worker（resume 计数 id）/ CLI（`--fresh`），复用 Phase 1 的骨架与对账框架。

每个 Phase 内部按"前置核实 → core/接口 → source 实现 → workflow/worker/CLI 接通 → 单元测试 → 人工冒烟"推进。详细步骤由后续 writing-plans 产出。
