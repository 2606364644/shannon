# deliverables 迁移至 session 目录

**日期**: 2026-06-19（2026-06-21 更新：纳入已实现的 rerun/resume 交互 + 删除 clean 功能）
**分支**: feat/fork-py
**状态**: 设计已确认，待编写实现计划

## 背景与问题

shannon 当前把扫描产出物（deliverables）写在**被扫描的目标仓库内**：`<repo>/.shannon/deliverables/`。这带来问题：

1. **污染被扫仓库**：扫描第三方/客户/自己维护的代码时，`.shannon/` 被写进对方仓库。`.gitignore` 只是缓解不是根治——扫描他人代码时尤其不可接受。
2. **存储根与 session 模型割裂**：过程文件（日志/prompts/scratchpad）在 shannon 自己的 `workspaces/<session>/` 下、按 session 隔离；而 deliverables 却锚定被扫 repo、跨 session 共享。两者生命周期不同。

deliverables 当前是**白盒与黑盒共享**的：白盒写漏洞队列，黑盒读写同一目录做动态验证。

## 目标

- deliverables 不再写入被扫仓库，迁到 shannon 自己的 `workspaces/` 下。
- **保留**白盒黑盒共享同一 deliverables 目录的现有语义（只换存储根，不改共享关系）。
- session 模型（scan_type / resume / parent-child links）**不变**；`clean` 功能删除（见决策 8）。
- 零配置默认：白盒无需指定 `-w`，黑盒默认接最近的白盒 session。

## 核心决策（已与用户确认）

1. **存储位置**：deliverables 共享目录从 `<repo>/.shannon/deliverables` 迁到 `workspaces/<白盒session>/deliverables`。白盒黑盒继续共享该目录。
2. **组织方式：按 session 组织**（非按 repo 累积）。每次白盒扫描一个独立 session；不追求跨 session 累积/diff——shannon 当前无消费累积的能力，累积反而引入旧产物残留。讨论中曾考虑的"按 repo 组织"方案已否决。
3. **白盒无 `-w` 自动生成 session name**，回填到 `input.workspace_name`。命名规则：有 `--url` 用其 hostname，否则用 repo basename，统一拼 `+ shannon-<毫秒时间戳>`。
4. **黑盒发现白盒**：默认 `--latest`（**软默认**——找到最近的白盒 session 就接其 deliverables，找不到则退回纯黑盒，不报错）；`-w` 手动覆盖。
5. **产物集中（用户选 A）**：黑盒的 deliverables（`*_exploitation_evidence.md`、`*_findings.md`、综合报告）写到**白盒 session 的 deliverables/**，与白盒 deliverables 同处。黑盒**过程文件**（workflow.log/agents/prompts）仍写黑盒自己的 child session。
6. **纯黑盒退化**：无白盒、`--latest` 找不到时，黑盒自建 session，deliverables 写自己 session（既当过程容器又当产物容器），不报错。
7. **向后兼容**：**不自动迁移**旧 `<repo>/.shannon/deliverables`，新位置为准，旧残留由用户自行清理。
8. **删除 clean 功能**：`clean_workspace` 的"重跑前清理"职责已被 `--fresh`（白盒全新扫）/ `--rerun`（黑盒归档重跑）取代，且与 rerun 的 `.blackbox-archive/` 归档在 deliverables 上直接冲突（一个要删 deliverables、一个要往 deliverables 里归档）。删除 `SessionManager.clean_workspace` + 白盒/黑盒 `clean` CLI 子命令 + 相关测试。需要清理过程文件/释放磁盘时改用 `delete_workspace`（删整个 session）。

## 新存储模型

```
workspaces/
├── <白盒session>/                     ← hostname/basename + shannon-<ts>
│   ├── session.json                   ← scan_type=whitebox, repo_path, web_url...
│   ├── workflow.log                   ← 白盒过程日志
│   ├── agents/  prompts/  scratchpad/ ← 白盒过程文件
│   └── deliverables/                  ← ★ 白盒黑盒共享产物目录
│       ├── injection_exploitation_queue.json   (白盒)
│       ├── injection_analysis_deliverable.md   (白盒)
│       ├── code_index.json / entry_points.json (白盒)
│       ├── injection_exploitation_evidence.md  (黑盒)
│       ├── injection_findings.md               (黑盒)
│       ├── comprehensive_security_assessment_report.md (黑盒)
│       └── .blackbox-archive/<run_ts>/         (rerun 归档历史)
└── <黑盒child-session>/               ← links.parent_workspace = 白盒session
    ├── session.json                   ← scan_type=blackbox
    ├── workflow.log / agents/ ...     ← 黑盒过程文件
    （无 deliverables/，产物集中在白盒 session）
```

纯黑盒场景：`<黑盒session>/` 自带 `deliverables/`，无 child/parent 关系。

## 与 rerun / resume 的交互（2026-06-21 新增）

spec 初版写于黑盒 rerun / 白盒 resume 实现之前。核实现状：

- 黑盒 **rerun**（`blackbox_rerun.py` 的 `detect_blackbox_completed` / `archive_blackbox_deliverables`）和白盒 **resume**（`WhiteboxResumeStateBuilder`）都已通过 `resolve_deliverables_path` 间接定位 deliverables，**未硬编码 `repo/.shannon`**。迁移后自动跟随到 session，**rerun/resume 逻辑本身不动**。
- rerun 的归档目录 `deliverables/.blackbox-archive/<run_ts>/` 随之落在白盒 session 的 deliverables 下。
- 幂等检测信号（`*_exploitation_evidence.md` 存在性）迁移后指向 session deliverables，行为不变。
- 这也是删除 clean 的依据之一：rerun 已接管 deliverables 的生命周期（归档保留历史），clean 再删 deliverables 与之冲突。

## 实现落点

| # | 改动 | 文件 | 说明 |
|---|------|------|------|
| 1 | 重写 `resolve_deliverables_path` | `packages/core/.../utils/paths.py:41` | 去掉 repo 优先级；改为 `workspace_name → workspaces/<name>/deliverables`。`repo_path` 参数**保留签名兼容，内部不再用于定位 deliverables** |
| 2 | 白盒无 `-w` 自动生成 name 回填 | `packages/whitebox/.../worker.py:69` | 去掉 `if input.workspace_name:` 守卫；`create_workspace` 返回的 name 回填 `input.workspace_name` |
| 3 | 自动命名 web_url 缺失兜底 | `packages/core/.../session.py:13` `create_workspace` | web_url 为空时用 repo basename 作 hostname 段 |
| 4 | 白盒 deliverables_path 回填改 session | `worker.py:260`、白盒 `pipeline/activities.py:24` `_get_paths` | 指向白盒 session 下的 deliverables |
| 5 | 所有 deliverables 读写跟随 #1 | 黑盒 `pipeline/workflows.py:125`、`agents/exploit_executor.py:33`、`services/exploitation_checker.py:52`、黑盒 `pipeline/activities.py:19`；**rerun detect（`cli/main.py:~130`）、rerun archive（`worker.py:~76`）、白盒 resume Builder（`worker.py:112`）** | 全部走 `resolve_deliverables_path`，自动指向白盒 session，逻辑不动 |
| 6 | **删除 clean** | `session.py:174` `clean_workspace`；白盒 `cli/main.py:350` `clean` 命令；黑盒 `cli/main.py:409` `clean` 命令；`test_session.py` 的 `test_clean_workspace_*` | "重跑清理"被 `--fresh`/`--rerun` 取代；与 rerun archive 冲突（决策 8） |
| 7 | `deliverables_dir_for_workspace` 跟随 #1 | `paths.py:73`；消费侧 `workspace.py:108/113`、白盒 `cli/main.py:261`、黑盒 `cli/main.py:320`、`test_paths.py` | 返回 session 维度；删 clean 后仍被 summary/消费侧使用，必须跟改 |
| 8 | `BB_DELIVERABLE_PATTERNS` 迁位 | `session.py:11` → `blackbox_rerun.py` | 删 clean 后只剩 rerun 用，从 core 挪到 blackbox 包；更新 `blackbox_rerun.py` 注释 |
| 9 | `SHANNON_DELIVERABLES_SUBDIR` 退役 | `constants.py`、`paths.py:8` | session 下不再需 `.shannon` 隐藏前缀；deliverables 子目录固定 `deliverables` |

## 现状澄清（更正前期误判）

> 核实 `paths.py` 后确认：`deliverables_dir_for_workspace`（`paths.py:73`）内部走 `resolve_deliverables_path` 优先级 2——从 session.json 恢复 repo_path，最终指回 `<repo>/.shannon/deliverables`（**非** workspace 下）。讨论中曾以为它"已按 workspace 维度"是误读。删 clean 后该函数仍被消费侧（`workspace.py` summary、CLI show 命令）使用，故落点 #7 需把它跟改到 session 维度。

## 不在范围内（YAGNI）

- session 模型重设计（混合 scan_type、resume 混合 agent 集合）——产物集中但过程仍分 session。
- 旧 `<repo>/.shannon/deliverables` 自动迁移。
- 跨 session deliverables 累积 / diff / 漏洞演进对比。
- 保留 clean 或其"按阶段清理"能力（已决定删除，决策 8）。
- 按 repo 组织的 deliverables 索引（方案 B，已否决）。

## 测试要点

- **白盒无 `-w`**：生成合法 session name（有/无 `--url` 两种），deliverables 落 `workspaces/<session>/deliverables`，被扫 repo 内不再出现 `.shannon/`。
- **白盒指定 `-w`**：用指定 name，行为一致。
- **黑盒默认 `--latest`**：自动接最近白盒 session 的 deliverables；无白盒时自建 session 不报错。
- **黑盒 `-w`**：接指定白盒 session。
- **产物集中**：黑盒 deliverables 落白盒 session 的 deliverables/；黑盒过程日志落黑盒 child session。
- **rerun/resume 跟随**：rerun detect/archive、resume Builder 迁移后定位 session deliverables，行为不变；rerun archive 落白盒 session 的 `.blackbox-archive/`。
- **消费侧**：`deliverables_dir_for_workspace`（`workspace.py` summary、CLI show）迁移后指向 session deliverables；`test_paths.py` 相应断言更新。
- **clean 已删**：白盒/黑盒 `clean` CLI 子命令不再存在；`test_session.py` 的 clean 测试删除；`delete_workspace` 仍可用。
- **resume**：白盒 resume 从新位置读 deliverables / completed_agents。
