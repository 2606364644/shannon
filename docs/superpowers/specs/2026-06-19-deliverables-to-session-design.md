# deliverables 迁移至 session 目录

**日期**: 2026-06-19
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
- session 模型（scan_type / clean / resume / parent-child links）**不变**。
- 零配置默认：白盒无需指定 `-w`，黑盒默认接最近的白盒 session。

## 核心决策（已与用户确认）

1. **存储位置**：deliverables 共享目录从 `<repo>/.shannon/deliverables` 迁到 `workspaces/<白盒session>/deliverables`。白盒黑盒继续共享该目录。

2. **组织方式：按 session 组织**（非按 repo 累积）。每次白盒扫描一个独立 session；不追求跨 session 累积/diff——shannon 当前无消费累积的能力，累积反而引入旧产物残留。讨论中曾考虑的"按 repo 组织"方案已否决。

3. **白盒无 `-w` 自动生成 session name**，回填到 `input.workspace_name`。命名规则：有 `--url` 用其 hostname，否则用 repo basename，统一拼 `+ shannon-<毫秒时间戳>`。

4. **黑盒发现白盒**：默认 `--latest`（**软默认**——找到最近的白盒 session 就接其 deliverables，找不到则退回纯黑盒，不报错）；`-w` 手动覆盖。

5. **产物集中（用户选 A）**：黑盒的 deliverables（`*_exploitation_evidence.md`、`*_findings.md`、`comprehensive_security_assessment_report.md`）写到**白盒 session 的 deliverables/**，与白盒 deliverables 同处。黑盒**过程文件**（workflow.log/agents/prompts）仍写黑盒自己的 child session（session 模型不动）。

6. **纯黑盒退化**：无白盒、`--latest` 找不到时，黑盒自建 session，deliverables 写自己 session（既当过程容器又当产物容器），不报错。

7. **向后兼容**：**不自动迁移**旧 `<repo>/.shannon/deliverables`，新位置为准，旧残留由用户自行清理。

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
│       └── comprehensive_security_assessment_report.md (黑盒)
└── <黑盒child-session>/               ← links.parent_workspace = 白盒session
    ├── session.json                   ← scan_type=blackbox
    ├── workflow.log / agents/ ...     ← 黑盒过程文件
    （无 deliverables/，产物集中在白盒 session）
```

纯黑盒场景：`<黑盒session>/` 自带 `deliverables/`，无 child/parent 关系。

## 实现落点

| # | 改动 | 文件 | 说明 |
|---|------|------|------|
| 1 | 重写 `resolve_deliverables_path` | `packages/core/.../utils/paths.py:41` | 去掉 repo 优先级；改为 `workspace_name → workspaces/<name>/deliverables`。`repo_path` 参数**保留以维持签名兼容，内部不再用于定位 deliverables** |
| 2 | 白盒无 `-w` 自动生成 name 回填 | `packages/whitebox/.../worker.py:69` | 去掉 `if input.workspace_name:` 守卫；`create_workspace` 返回的 name 回填 `input.workspace_name`（后续 deliverables/session_id 都依赖它） |
| 3 | 自动命名 web_url 缺失兜底 | `packages/core/.../session.py:13` `create_workspace` | web_url 为空时用 repo basename 作 hostname 段 |
| 4 | deliverables_path 回填改 session 维度 | `worker.py:260`、白盒 `pipeline/activities.py:24` `_get_paths` | 指向白盒 session 下的 deliverables |
| 5 | 黑盒读写 deliverables 指向白盒 session | `packages/blackbox/.../pipeline/workflows.py:125`、`agents/exploit_executor.py:33`、`services/exploitation_checker.py:52`、黑盒 `pipeline/activities.py:19` | `resolve_deliverables_path` 传入白盒 workspace_name；黑盒写入也落白盒 session |
| 6 | `clean_workspace` 跟改 | `session.py:174` | 见下方 clean 语义小节 |
| 7 | `deliverables_dir_for_workspace` 跟随 #1 | `paths.py:73` | 改为返回 `workspaces/<name>/deliverables` |
| 8 | `SHANNON_DELIVERABLES_SUBDIR` / `DEFAULT_DELIVERABLES_SUBDIR` | `constants.py`、`paths.py:8` | session 下不再需要 `.shannon` 隐藏前缀；deliverables 子目录固定 `deliverables`。旧 env var 退役（或重定义为 session 下子目录名，默认 `deliverables`） |

## clean 语义（产物集中后，需在实现时定稿）

产物集中后，黑盒 deliverables 落在白盒 session，clean 边界变复杂。**倾向定义**（待实现时确认）：

- `clean_workspace(scan_type="whitebox")`：清白盒 session 整个 `deliverables/`（含白盒+黑盒产物）+ 白盒过程文件。
- `clean_workspace(scan_type="blackbox")`：只清黑盒 **child session 的过程文件**（agent logs/workflow.log/playwright），**不动**白盒 session 的 deliverables。黑盒产物只能通过清白盒 session 清除。

理由：黑盒 child session 不再持有 deliverables，清它不该跨 session 去动白盒产物。若需单独清黑盒产物，可作为后续增强（通过 links 定位 parent 的 deliverables 删黑盒 pattern），本次不做。

## 现状澄清（更正前期误判）

> 设计讨论中曾认为 `clean_workspace` 已按 workspace 维度清理 deliverables、与 repo-centric 写入存在"矛盾"。核实 `paths.py` 后更正：`deliverables_dir_for_workspace`（`paths.py:73`）内部调用 `resolve_deliverables_path` 走优先级 2——从 session.json 恢复 repo_path，白盒 session 总有 repo_path，故 clean 实际清的是 `<repo>/.shannon/deliverables`，**与写入一致，并无矛盾**。因此本设计落点 #6/#7 是把 clean 一并迁到新位置，而非"利用现成的 workspace 维度实现"。

## 不在范围内（YAGNI）

- session 模型重设计（混合 scan_type、按阶段 clean、resume 混合 agent 集合）——用户选 A，产物集中但过程仍分 session。
- 旧 `<repo>/.shannon/deliverables` 自动迁移。
- 跨 session deliverables 累积 / diff / 漏洞演进对比功能。
- 单独清理黑盒产物的能力（见 clean 语义）。
- 按 repo 组织的 deliverables 索引（曾讨论的方案 B，已否决）。

## 测试要点

- **白盒无 `-w`**：生成合法 session name（有/无 `--url` 两种），deliverables 落 `workspaces/<session>/deliverables`，被扫 repo 内不再出现 `.shannon/`。
- **白盒指定 `-w`**：用指定 name，行为一致。
- **黑盒默认 `--latest`**：自动接最近白盒 session 的 deliverables；无白盒时自建 session 不报错。
- **黑盒 `-w`**：接指定白盒 session。
- **产物集中**：黑盒 deliverables 落白盒 session 的 deliverables/；黑盒过程日志落黑盒 child session。
- **clean**：清白盒 session 删其 deliverables（含黑盒产物）；清黑盒 child session 不误删白盒 deliverables。
- **resume**：白盒 resume 从新位置读 deliverables / completed_agents。
