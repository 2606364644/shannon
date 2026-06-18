# 白盒扫描 Resume（断点续传）设计

- **日期**: 2026-06-18
- **状态**: Draft（待实现）
- **分支**: feat/fork-py
- **相关**: `docs/gap/2026-06-17-pre-recon-weakness-analysis.md` W-02（Resume 断点续扫形同实设）

---

## 1. 背景与问题

白盒扫描的 `pre-recon` / `recon` 阶段各耗时约 2 小时。扫描中断后，当前实现**只能从头重跑**，造成显著的 token 与时间浪费。

根因：项目里其实**存在**"跳过已完成 agent"的守卫代码（`workflows.py:131/213/279`）：

```python
if AgentName.PRE_RECON.value not in self._state.completed_agents:
    ...  # 执行该 agent
```

但这些守卫**永远空转**，因为 `self._state.completed_agents` 是 `PipelineState` 的实例字段（`shared.py:25`），每次 `WhiteboxScanWorkflow` 启动都是空的。**没有任何"开机时把已有进度读回来"的环节**——这是断掉的唯一一环。

由此带来的误导性声明：
- `README.md:9`、`docs/architecture.md:172`、`packages/whitebox/README.md:19` 都宣称"支持断点续扫"
- `cli/main.py:33` 的 `-w/--workspace` 标注 "supports resume"
- `models/base.py:14` 有 `resume_from_workspace` 字段，但全代码库从未被读取

TS 原版有完整 resume 链路（`loadResumeState → restoreGitCheckpoint → computeExpectedAgents → shouldSkip → saveCheckpoint`），Python 重构版未移植。

## 2. 目标与非目标

**目标：**
- **全量重建**：resume 完全不依赖 Temporal workflow history，纯靠磁盘（git checkpoint 历史 + session.json metrics + 产出物文件）重建 `completed_agents`。能力上是"进程级中断"的超集。
- **自动续扫**：重跑同一个 workspace 即自动检测进度并续扫；想全新扫用新 workspace 名或 `--fresh`。
- **agent 粒度**：已完成（已 commit deliverable）的 agent 绝不重跑；这是省 token 的核心。

**非目标（YAGNI）：**
- agent **内部**续传：recon 崩溃在 95% 仍重跑整个 recon。LLM agent 内部对话状态无法可靠持久化，ROI 极低。
- 主动清理 Temporal 里残留的旧 workflow：靠无 worker poll 自然超时终止。
- 跨 repo 共享同一 workspace 名。

## 3. 现成基础设施（已核实就位）

resume 不需要从零造轮子——三件套基础设施已经齐全：

| 基础设施 | 现状 | 证据 |
|----------|------|------|
| **git checkpoint 链** | agent 执行前 commit `checkpoint: before {agent}`，成功后 commit `deliverable: {agent}`；commit 链即进度日志 | `git_manager.py:67/108`，`executor.py:64` |
| **session.json 增量原子写** | 每个 agent 完成后**立即**通过 `MetricsTracker.end_agent → _atomic_write` 落盘 `metrics.agents[*].status` | `activities.py:102` → `audit/session.py:102` → `audit/metrics_tracker.py:73-95,158-162` |
| **产出物确定文件名 + 原子写** | 每个 agent 有固定 `deliverable_filename`（如 `pre_recon_deliverable.md`）；`atomic_write_json/text` 避免半成品 | `models/agents.py:26`，`utils/atomic_write.py` |
| **各阶段守卫齐全** | pre-recon / recon / **vuln 并行** 每个 agent 都有独立 `if X not in completed_agents` 守卫 | `workflows.py:131/213/279` |

> **关键澄清（纠正一个易混的前提）**：vuln 阶段守卫齐全，但 resume 能跳过已完成 agent **不是**靠"Temporal 自动恢复 PipelineState"——`PipelineState` 是 workflow 实例字段，跨 workflow 实例**不持久化**。这正是本设计要补的缺口。真正让跳过生效的是：**worker 侧把重建的 `completed_agents` 通过 workflow input 灌进去**，现有守卫读到非空集合后自然跳过。

**好消息：** session.json 写入已是增量原子（风险消除，`SessionManager` 无需改写入逻辑）；vuln 调度守卫齐全（无需改调度逻辑）。

## 4. 核心设计

### 4.1 设计洞察（最小改动路径）

> resume 的本质不是新造续传机制，而是——**在启动 workflow 之前，把磁盘上已有的进度读回来、对账成可信的 `completed_agents`，通过 workflow input 灌进去**。这样项目现成的"跳过已完成 agent"守卫代码就自然激活了。

改动集中在"开机重建状态"这一段，workflow 内部跳过逻辑基本不动。

### 4.2 resume 路径数据流

```
CLI: start -w <workspace>
  │
  ▼
worker 启动前探测:这个 workspace 有未完成的 session.json 吗?
  │
  ├─ 否 / 用户加了 --fresh  ──►  全新扫描(空 completed_agents)
  │
  └─ 是  ──►  ResumeStateBuilder.load_resume_state(workspace, repo_path)
                 ├─ 扫 git log:所有 `deliverable: {agent}` commit  →  git_completed
                 ├─ 读 session.json:metrics.agents[*].status=success → json_completed
                 ├─ 查磁盘:git_completed 里每个 agent 的产出物文件在不在  →  file_ok
                 └─ 对账:以 git 为准,不一致记 warning  →  completed_agents + interrupted_agent
        │
        ▼
     git reset 到 interrupted_agent 的 checkpoint commit(丢弃半成品产出物)
        │
        ▼
     start_workflow( id = <workspace>-resume-{n},   ← 带 resume 计数,避开 Temporal 旧 workflow 冲突
                     input 携带 completed_agents )
        │
        ▼
     WhiteboxScanWorkflow.run:用 input 预填 self._state.completed_agents
        │
        ▼
     现有守卫 `if PRE_RECON not in completed_agents:`  ←  现在真的能跳过了 ✓
     从 interrupted_agent 继续往后跑
```

**设计选择 —— resume 探测放 worker 侧（`start_workflow` 前的同步函数），不放 Temporal activity：**
- 全新扫时直接跳过 resume 逻辑；
- `ResumeStateBuilder` 是普通 Python 函数，离线好测（不依赖 Temporal）；
- workflow 保持纯粹（"input 告诉我 `completed_agents`，我就预填"）；
- git/reset 失败的重试靠用户重跑命令兜底（可接受，启动期一次性操作）。

## 5. 对账规则（方案 C：双源对账）

对一个 agent A，三个信号：
- **G** = git log 里有 `deliverable: A` commit（agent 成功的最后一道原子关）
- **J** = session.json 里 `metrics.agents[A].status == "success"`
- **F** = A 的产出物文件真在磁盘上存在

**决策表（以 git 为权威）：**

| G | J | F | 判定 | 处理 |
|---|---|---|------|------|
| ✓ | ✓ | ✓ | 正常完成 | ✅ 进 `completed_agents` |
| ✓ | ✗ | ✓ | session.json 落盘晚了 | ✅ 算完成，`warn`（以 git 为准） |
| ✓ | ✓ | ✗ | 文件被误删 | 🔴 **中止** |
| ✓ | ✗ | ✗ | git 有 commit，文件和 session 都没有 | 🔴 **中止** |
| ✗ | ✓ | ✓ | session.json 误记 success | ⚠️ 不算完成，**重跑** + `warn` |
| ✗ | ✓ | ✗ | session.json 误记 | ⚠️ 重跑 + `warn` |
| ✗ | ✗ | ✓ | 文件在但没 commit（半成品/旧残留） | ⚠️ 重跑 + `warn` |
| ✗ | ✗ | ✗ | 未跑过 | 正常重跑 |

**一句话原则：**
- **"完成"的充要条件 = `G ∧ F`**（git 确认跑完 **且** 产出物还在）。
- `G ∧ ¬F` 是唯一的中止情形——**不能静默重跑**，因为文件丢失说明外部出过事，结果已不可信，必须让人介入。
- 其余所有 `¬G` 一律算未完成、重跑（session/file 顶多用来发 warning）。

**中断 agent 定位与工作区清理：**
1. 找 git log 里最后一个 `deliverable:` commit。
2. 若它之后还挂着 `checkpoint: before X`（无对应 deliverable）→ **X 就是中断 agent**，`git reset --hard` 到 X 的 checkpoint commit，丢弃 X 的半成品产出物，X 从干净状态重跑。
3. 若最后就是 deliverable、没悬空 checkpoint → 上个 agent 干净完成，`interrupted_agent` = 编排顺序里的下一个。

## 6. 错误处理 / abort 边界

resume 绝不静默退化成全新扫（那样既浪费又掩盖问题）：

| 场景 | 处理 |
|------|------|
| `G ∧ ¬F`（git 有 deliverable commit 但文件不在磁盘） | 🔴 中止 + 明确错误（文件被误删，重跑会丢数据） |
| git log 解析失败 / deliverables 仓库损坏 | 🔴 中止 |
| session.json 的 `repoPath` 与当前 repo 不符 | 🔴 中止（workspace 名撞车但不是同一仓库） |
| session.json `status == "completed"` | 不 resume，提示"已扫完，用新 workspace 或 `--fresh`" |
| `--fresh` | 忽略磁盘状态全新扫，workflow id 不带 resume 计数 |

## 7. 组件设计

**新增：**

| 组件 | 职责 |
|------|------|
| `packages/whitebox/src/shannon_whitebox/pipeline/resume.py` | `ResumeState` dataclass + `ResumeStateBuilder` |
| `tests/.../test_resume_state_builder.py` | 对账决策表 parametrize 单元测试 |

`ResumeState` dataclass 字段：
- `completed_agents: list[str]` — 对账后可信的已完成集合
- `interrupted_agent: str | None` — 中断的 agent（需 reset + 重跑）
- `base_commit: str | None` — git reset 目标 commit
- `warnings: list[str]` — 对账不一致告警
- `resume_attempt_number: int` — 用于 workflow id 计数

`ResumeStateBuilder.load_resume_state(workspace, repo_path) -> ResumeState`，内部方法：
- `_parse_git_completed()` — 扫 git log 的 `deliverable:` commit
- `_read_session_completed()` — 读 session.json `metrics.agents[*].status`
- `_check_files_exist()` — 校验产出物文件存在性
- `_reconcile()` — 按决策表对账
- `_locate_interrupted_agent()` / `_compute_base_commit()`

**改动：**

| 文件 | 改动 |
|------|------|
| `worker.py` | `start_workflow` 前调 `ResumeStateBuilder`；workflow id 加 `-resume-{n}`；构造携带 `completed_agents` 的 input |
| `workflows.py` (`WhiteboxScanWorkflow.run`) | 从 workflow input 读取并预填 `self._state.completed_agents`（**现有守卫代码不动**） |
| `cli/main.py` | 加 `--fresh` option |
| `SessionManager` / `metrics_tracker` | 维护 `resumeAttempts` 计数（status 增量写**已具备**，无需改） |

## 8. 测试策略（分层，避开 Temporal 全量 hang）

> 项目约束：pytest 跑全量/全包会卡在 Temporal 慢测试。resume 自动化验证不依赖真起 Temporal server。

1. **单元测试（主力，纯离线）** — `test_resume_state_builder.py`：临时 git 仓库 + 临时 session.json + 临时产出物文件，`parametrize` 决策表全部 8 种 G/J/F 组合，断言判定 / warning / abort。再测中断 agent 定位、`base_commit` 计算。秒级、不碰 Temporal。
2. **守卫激活测试（链路验证）** — 验证"预填 `completed_agents` → workflow 守卫真的跳过"。mock 掉 agent 执行，只测编排跳过。⚠️ 已知陷阱：dispatch 用 `isinstance`，测试用 `MagicMock + .type` 会"测试绿生产坏"——mock 须按真实 SDK 事件形态。
3. **E2E resume（人工冒烟）** — 真 repo 跑到 pre-recon 完成 → kill → resume → 验证从 recon 起。依赖 Temporal，归入"人工冒烟"，不进默认 pytest（与项目其他 feature 一致）。

## 9. 风险登记

| # | 风险 | 状态 |
|---|------|------|
| ~~1~~ | ~~vuln 并行阶段守卫不齐~~ | ✅ 已消除：守卫齐全（`workflows.py:279`） |
| 2 | `git reset --hard` 的破坏性 | 只 reset 到 interrupted agent 的 checkpoint（丢弃其半成品，本就要重跑）；`repoPath` 校验 + `base_commit` 计算正确即安全 |
| 3 | Temporal 里旧 workflow 残留 | resume 计数 id 规避 `AlreadyStarted`；旧 workflow 无 worker poll 自然超时，不主动清理 |
| ~~4~~ | ~~session.json agent status 落盘时机~~ | ✅ 已消除：已是增量原子写 |
| 5 | resume 探测对 git message 格式的依赖 | `deliverable:` / `checkpoint: before` 是 `git_manager.py` 固定产出，约定稳定；对账时做格式校验 + 清晰报错 |

## 10. 实现时核实项（Open Questions）

- workflow input 的当前结构（`WhiteboxScanWorkflow.run` 的入参），确认能干净地塞入 `completed_agents`。
- `resumeAttempts` 字段在 session.json 中是否已存在；若不存在则新增维护。
- resume 计数 workflow id 的生成策略：从 session.json 读 vs 扫描已有 `<workspace>-resume-*` id 取 max+1。
