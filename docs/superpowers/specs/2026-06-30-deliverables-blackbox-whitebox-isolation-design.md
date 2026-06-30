# 黑白盒产物目录隔离设计

- **日期**：2026-06-30
- **状态**：设计待审（spec written, awaiting user review）
- **分支**：`feat/fork-py`
- **主题**：把白盒与黑盒扫描的产物（deliverables）从同一个 `deliverables/` 根目录，物理隔离到 `deliverables/whitebox/` 与 `deliverables/blackbox/` 两个子目录，消除最终报告互相覆盖与目录混乱；读路径自动 fallback 兼容老 workspace。

---

## 1. 背景与问题

### 1.1 现象

当前黑盒扫描在设计上会复用白盒 workspace（`-w` / `--latest` / rerun）：黑盒 preflight 检测到白盒的 `{vt}_exploitation_queue.json` 存在就跳过 recon，直接消费白盒候选队列（`packages/blackbox/.../activities.py:498` `detect_whitebox_results`）。这是核心桥接，**必须保留**。

但黑白盒产物**都落在同一个 `deliverables/` 根目录**，导致两类问题（用户确认的实际痛点）：

1. **最终报告互相覆盖**：白盒和黑盒都写 `comprehensive_security_assessment_report.md`
   - 白盒：`packages/whitebox/.../activities.py:824`
   - 黑盒：`packages/blackbox/.../activities.py:272`、`:392`
   - 文件名常量：`DeliverableType.REPORT`（`packages/core/.../models/deliverables.py:33`）
   - 黑盒一旦跑在白盒 workspace 上，**白盒的最终报告被黑盒报告直接覆盖丢失**。

2. **目录混在一起难区分**：白盒的 `*_analysis_deliverable.md` / `code_index.json` / `entry_points.json` 与黑盒的 `*_exploitation_evidence.md` / `*_findings.md` / report 全堆在同一 `deliverables/` 根，分不清归属、不利归档。

### 1.2 已有的局部缓解（仅覆盖 rerun 场景）

`packages/blackbox/.../pipeline/blackbox_rerun.py` 的 `archive_blackbox_deliverables` 在 rerun 时把黑盒产物挪到 `deliverables/.blackbox-archive/<run_ts>/`（注释明确"白盒产出物不归档"）。这证明项目**已意识到混目录问题**，但**首跑复用时的报告覆盖并未解决**。

### 1.3 非痛点（已与用户确认排除）

- `{vt}_exploitation_queue.json` 的"白盒候选 vs 黑盒验证后"语义混淆：用户确认黑盒**只读不回写** queue，此项不成立。
- 黑白盒漏洞结果重复计数：非本次目标。

---

## 2. 目标与非目标

### 2.1 目标

1. 白盒产物全部落 `deliverables/whitebox/`，黑盒产物全部落 `deliverables/blackbox/`。
2. 黑白盒最终报告物理隔离，**不再互相覆盖**。
3. 黑盒读白盒 queue 的桥接、resume（断点续跑）、rerun 归档全部继续工作。
4. 老 workspace（产物还在 `deliverables/` 根）通过**读路径自动 fallback** 仍可用、resume 不断。

### 2.2 非目标（YAGNI）

- 不改 correlation workspace 自身产物布局（独立 workspace，本就不与黑白盒混）。
- 不改 queue 文件内部 schema。
- 不做漏洞结果去重。
- 不碰 LLM/确定性双轨，不碰 CLAUDE.md 双轨独立性铁律。
- 不改 `DeliverableType` 模型（本设计做法 1 不需要）。
- 不写一次性迁移脚本（已选读路径 fallback，不需要）。

---

## 3. 现状：产物落点清单

### 3.1 白盒产物（写）

文件：`packages/whitebox/src/shannon_whitebox/pipeline/activities.py`（除注明外）

- `pre_recon_deliverable.md` / `recon_deliverable.md` / `{vt}_analysis_deliverable.md`
- `{vt}_exploitation_queue.json`（:658）/ `{vt}_gitnexus_queue.json`（:655、:955）/ `{vt}_llm_queue.json`（:675）
- `authz_gitnexus_queue.json`（:360）/ `auth_gitnexus_queue.json`（:1086）
- `code_index.json` / `entry_points.json` / `audit_plan.json`
- 最终报告 `comprehensive_security_assessment_report.md`（:824）

### 3.2 黑盒产物（写）

- `{vt}_exploitation_evidence.md`（`packages/blackbox/.../agents/exploit_executor.py:96`）
- `{vt}_findings.md`
- 最终报告 `comprehensive_security_assessment_report.md`（`packages/blackbox/.../pipeline/activities.py:272`、:392）
- `.blackbox-archive/<run_ts>/`（`blackbox_rerun.py`，rerun 归档）

### 3.3 桥接读点

- 黑盒读白盒 queue：`detect_whitebox_results`（`activities.py:515`）、`exploit_executor.py:39`、`exploitation_checker.py:78`、`coverage_renderer.py:97`、`blackbox/cli/main.py:317`
- 黑盒读自己 evidence：`coverage_renderer.py:98`
- multi 关联读子仓白盒 queue：`packages/multi/.../orchestrator.py`、`has_correlation_results`（`blackbox/.../pipeline/workflows.py:35`）

### 3.4 现有 core path 层（`packages/core/src/shannon_core/utils/paths.py`）

- `resolve_deliverables_path(...)`（:53）→ `workspaces/<session>/deliverables`
- `deliverables_dir_for_workspace(workspace_path)`（:79）→ `workspace_path / "deliverables"`
- `has_valid_whitebox_results(queue_file)`（:88）→ 校验单个 queue 文件有效性

---

## 4. 设计

### 4.1 §1 核心路径模型：引入 track 维度

在 core `paths.py` 新增 track 概念与 3 个 helper（**做法 1**，从三个候选中选定）：

```python
WHITEBOX_SUBDIR = "whitebox"
BLACKBOX_SUBDIR = "blackbox"

def whitebox_dir(workspace_path: Path) -> Path:
    """白盒产物目录：workspace/deliverables/whitebox/"""
    return deliverables_dir_for_workspace(workspace_path) / WHITEBOX_SUBDIR

def blackbox_dir(workspace_path: Path) -> Path:
    """黑盒产物目录：workspace/deliverables/blackbox/"""
    return deliverables_dir_for_workspace(workspace_path) / BLACKBOX_SUBDIR

def resolve_track_deliverable(workspace_path: Path, track: str, filename: str) -> Path:
    """读侧 fallback：先 track 子目录，找不到回退 deliverables 根（兼容老 workspace）。
    都不存在时返回新结构路径，让调用方按既定 not-found 语义处理。"""
    base = deliverables_dir_for_workspace(workspace_path)
    new = base / track / filename
    if new.exists():
        return new
    legacy = base / filename
    return legacy if legacy.exists() else new
```

- **写侧**：白盒 `whitebox_dir(ws) / filename`，黑盒 `blackbox_dir(ws) / filename`，**永远写新结构**。
- **读侧**：按归属走 `resolve_track_deliverable(ws, track, filename)`，fallback 集中在此一处，业务侧无感。

**为何选做法 1**（备选见 §8）：新增 3 个 helper，写侧直拼、读侧 fallback 集中在 core 一处。业务侧（whitebox/blackbox/multi）只换调用，最小侵入、兼容性可控。

### 4.2 §2 写侧 / 读侧适配点 + rerun 归档

原则：**写永远写新结构，读按归属走对应 fallback**。

#### ① 白盒写侧 → `whitebox_dir(ws)/`

`packages/whitebox/.../pipeline/activities.py` 所有写 deliverables 的点（清单见 §3.1）改用 `whitebox_dir(ws)`。

#### ② 黑盒写侧 → `blackbox_dir(ws)/`

`packages/blackbox/.../` 所有写点（§3.2）改用 `blackbox_dir(ws)`。最终报告与白盒报告物理隔离，**不再覆盖**。

#### ③ 读侧桥接（按归属选 resolver）

- **黑盒读白盒 queue** → `resolve_track_deliverable(ws, "whitebox", f"{vt}_exploitation_queue.json")`，覆盖 §3.3 所列读点。
- **黑盒读自己 evidence** → `resolve_track_deliverable(ws, "blackbox", f"{vc}_exploitation_evidence.md")`。
- **multi 关联读子仓白盒 queue** → 同 whitebox fallback。
- **correlation workspace 自身产物**（merged queue / topology / boundaries / correlation-report）：独立 workspace、不分轨，保持其 deliverables 根。黑盒 `has_correlation_results`（`workflows.py:35`）读 correlation workspace 的 merged queue 时，走 `resolve_track_deliverable(corr_ws, "whitebox", f"{vt}_exploitation_queue.json")`：先查 `corr_ws/deliverables/whitebox/`（不存在）→ fallback 到 `corr_ws/deliverables/` 根（命中 merged queue），**天然兼容，无需 correlation workspace 分轨**。
- **签名注意**：`resolve_track_deliverable` 与 `whitebox_dir`/`blackbox_dir` 均接收 **`workspace_path`**（即 `workspaces/<session>`），内部自行拼 `deliverables/`。调用方（含 multi/correlation 侧）必须传 workspace_path 而非已拼好的 deliverables 目录，否则路径会多拼一层。

#### ④ rerun 归档迁移到 `blackbox/` 内

`blackbox_rerun.py`：
- `detect_blackbox_completed`：glob `*_exploitation_evidence.md` 改在 `blackbox_dir(ws)` 下找。
- `archive_blackbox_deliverables`：归档源与目标都进 `blackbox_dir(ws)` → `blackbox/.blackbox-archive/<run_ts>/`。注释"白盒产物不归档"天然成立——白盒在另一子目录。

**待实现时核实**：黑盒 CLI `main.py:317` 的 `{vc}_exploitation_queue.json` 默认按"读白盒 queue（展示/导出）"处理（黑盒只读不回写，与用户排除的语义混淆痛点一致）；若 plan 阶段发现黑盒有回写，再按"白盒候选 vs 黑盒验证后"分开命名。

### 4.3 §3 兼容边界 + 测试 + 范围

#### 兼容边界（fallback 精确语义）

1. fallback **只在读侧**；写侧永远写新结构、不 fallback。
2. **resume 不断**：resume 读已落盘产物走 fallback，老/新 workspace 都能续跑。
3. **已知局限（文档化接受）**：老 workspace 黑白盒 report 本就同名混在根、黑盒早已覆盖白盒 report——fallback **救不回已丢失的历史 report**，也**无法在老 workspace 上区分**根里那个 report 是哪轨的（queue 无此问题，黑盒不写 queue）。**新 workspace 才有保真隔离**。这是选 fallback（不迁移）的固有代价。

#### 测试策略（只跑改动相关文件，避开预存挂起）

1. **core path helper 单测**：`whitebox_dir`/`blackbox_dir` 路径正确；`resolve_track_deliverable` 三分支（新结构命中 / 老结构 fallback 命中 / 都不存在返回新路径）。
2. **白盒写**：产物落 `whitebox/`（改现有写测试断言路径）。
3. **黑盒写**：产物落 `blackbox/`。
4. **黑盒读白盒 fallback**：新 workspace 从 `whitebox/` 读、老 workspace（queue 在根）从根读。
5. **report 不覆盖集成**：黑白盒同 workspace 跑，`whitebox/report.md` 与 `blackbox/report.md` **共存**。
6. **rerun 归档**：归档落 `blackbox/.blackbox-archive/`、`detect_blackbox_completed` 在 `blackbox/` 找 evidence。
7. **回归守卫**：现有黑白盒产物读写相关测试不挂。

> 预存挂起说明：全套 pytest 有预存挂起/失败（`test_worker_progress` / `test_cli follow` / `test_audit_injection` / integration 挂起），广跑需 `--ignore`。只跑本次改动相关测试文件。

---

## 5. 涉及包

| 包 | 改动 |
|---|---|
| `packages/core` | path helper 新增 `whitebox_dir`/`blackbox_dir`/`resolve_track_deliverable`（心脏） |
| `packages/whitebox` | 写侧全部产物落 `whitebox/` |
| `packages/blackbox` | 写侧落 `blackbox/`；读白盒 queue 走 whitebox fallback；读自己 evidence 走 blackbox fallback；rerun 归档迁入 `blackbox/` |
| `packages/multi` | 关联读子仓白盒 queue 走 whitebox fallback |

---

## 6. 风险与已知局限

| 风险 | 缓解 |
|---|---|
| 老 workspace 同名 report fallback 歧义（根里那个 report 无法区分轨） | 文档化接受；新 workspace 保真隔离；queue 无此问题 |
| 漏改某个读点导致读不到产物 | §3.3 已穷举桥接读点；测试 4/5 守卫 |
| resume 读老产物断点丢失 | fallback 保证老 workspace 可续；测试 4 覆盖 |
| rerun 归档目录变了，老 `.blackbox-archive/` 失联 | 历史归档为只读快照；rerun 重新归档进 `blackbox/.blackbox-archive/`，可接受 |

---

## 7. 验收标准

1. 新 workspace 跑完白盒：所有白盒产物在 `deliverables/whitebox/`，根目录无白盒文件。
2. 新 workspace 跑完黑盒（复用白盒 workspace）：黑盒产物在 `deliverables/blackbox/`，`whitebox/comprehensive_security_assessment_report.md` 与 `blackbox/comprehensive_security_assessment_report.md` **共存**。
3. 黑盒能从 `whitebox/{vt}_exploitation_queue.json` 读到白盒候选队列并跳过 recon。
4. 老 workspace（产物在根）的黑盒/resume 仍能通过 fallback 读到产物。
5. rerun 归档落 `blackbox/.blackbox-archive/<run_ts>/`。
6. 改动相关测试全绿；预存挂起测试不受影响。

---

## 8. 备选方案（已否决）

- **做法 2**：给现有 `resolve_deliverables_path` / `deliverables_dir_for_workspace` 加 `track` 参数。每个调用点都要传 track，且 fallback 无处集中、散到各读点。侵入大，否决。
- **做法 3**：改 `DeliverableType` 加 track 维度。模型驱动，但 `DeliverableType` 是"文件类型"非"轨归属"（evidence 属黑盒、analysis 属白盒），逐类型厘清归属改动大、耦合两层概念，否决。
- **老 workspace 硬切换 / 迁移脚本**：用户选读路径 fallback，不采用。
