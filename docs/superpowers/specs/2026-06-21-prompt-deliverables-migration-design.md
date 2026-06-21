# prompt 模板 deliverables 迁移至 session（Phase 2）

**日期**: 2026-06-21
**分支**: feat/fork-py
**状态**: 设计已确认，待编写实现计划
**前置**: Phase 1（`docs/superpowers/specs/2026-06-19-deliverables-to-session-design.md`）已完成 Python 侧路径迁移

## 背景与问题

Phase 1 把 Python 侧 deliverables 路径从 `<repo>/.shannon/deliverables` 迁到 `workspaces/<session>/deliverables`。但 **agent（LLM）写 deliverable 的指令源——prompt 模板——仍硬编码 `{{REPO_PATH}}/.shannon/deliverables`**（15 模板，~129 处），并引用 shannon-py 不存在的 `save-deliverable` CLI（agent 跨项目找 JS 版 `shannon/apps/worker/dist/scripts/save-deliverable.js` 写 `repo/.shannon`）。

后果：agent 按 prompt 写 `repo/.shannon/deliverables`，但 Python `validate_deliverable` 检查 session 维度 → `Missing deliverable: pre_recon_deliverable.md`。**Phase 1 的核心目标（不污染 repo）实际未达成**——agent 仍写 repo。NodeGoat 冒烟抓到（pre-recon agent 用 save-deliverable.js 写 `NodeGoat/.shannon/deliverables`，validate 查 `workspaces/<session>/deliverables` 失败）。

> Phase 1 final review 的"生产 grep `.shannon`=0 命中"只查了 `packages/*.py`，漏了 `prompts/*.txt`。这是 review 盲区。

## 目标

- prompt 模板的 deliverables/scratchpad 路径迁到 session 维度变量 `{{DELIVERABLES_PATH}}`/`{{SCRATCHPAD_PATH}}`（绝对路径）。
- 移除虚构 save-deliverable CLI 引用，改 Write 工具，Python `validate_deliverable` 兜底校验。
- agent 不再写被扫 repo（deliverables + scratchpad 都落 session），真正达成 Phase 1 核心目标。

## 核心决策（已与用户确认）

1. **save-deliverable 移除（用户选 A）**：prompt 删 save-deliverable CLI 描述（11 模板），改"用 Write 工具写到 `{{DELIVERABLES_PATH}}/<filename>`"。Python `validate_deliverable`（`executor.py:106`）兜底校验 deliverable 文件存在。
2. **路径变量化**：`{{REPO_PATH}}/.shannon/deliverables` → `{{DELIVERABLES_PATH}}`；`{{REPO_PATH}}/.shannon/scratchpad` → `{{SCRATCHPAD_PATH}}`。绝对路径（session 在 repo 外，相对 cwd 脆弱）。
3. **scratchpad 一起迁**：同 deliverables 模式，避免 repo 污染。
4. **不用 symlink**（repo/.shannon → session）：hack，掩盖问题、跨机器失效、git 追踪 symlink。变量化根治。

## 变量注入（2 处）

| # | 改动 | 文件 |
|---|------|------|
| 1 | `_interpolate` 加 `{{DELIVERABLES_PATH}}`/`{{SCRATCHPAD_PATH}}` 的 replace | `packages/core/src/shannon_core/prompts/manager.py` |
| 2 | executor variables 加 `deliverables_path` + `scratchpad_path`（**单点覆盖**：白盒 `run_agent` + 黑盒 `recon_executor`/`exploit_executor` 都经 `AgentExecutor.execute`） | `packages/core/src/shannon_core/agents/executor.py:55` |

> 调用链确认：白盒 `activities.run_agent`、黑盒 `recon_executor.py:29` / `exploit_executor.py:42` 均调 `self._executor.execute(...)`，故 variables 只在 `executor.execute` 一处注入即覆盖全部 agent，无需逐 executor 单独注入。

**变量值**：
- `deliverables_path` = `resolve_deliverables_path(...)` 返回值（session 维度，Phase 1 已有）
- `scratchpad_path` = session 目录下的 `scratchpad`（即 `workspaces/<session>/scratchpad`）。executor 从 `deliverables_path.parent / "scratchpad"` 推导（deliverables 默认单级在 session 下，parent 即 session 目录）。

`_interpolate` 现有机制是简单 `result.replace("{{REPO_PATH}}", ...)`，新增两个 replace 同模式。

## 模板迁移（15 模板）

| 替换模式 | 命中 |
|---|---|
| `{{REPO_PATH}}/.shannon/deliverables` → `{{DELIVERABLES_PATH}}` | ~129 处 |
| `{{REPO_PATH}}/.shannon/scratchpad` → `{{SCRATCHPAD_PATH}}` | 3 模板（pre-recon-code/recon/report-executive） |
| 裸 `.shannon/deliverables`（schemas 子目录等，无 `{{REPO_PATH}}` 前缀）→ `{{DELIVERABLES_PATH}}` | 散见 |
| save-deliverable CLI 描述/指示 → Write 工具指示 | 11 模板 |

**含 `.shannon/deliverables` 的 15 模板**：pre-recon-code, auth-exploit, authz-exploit, injection-exploit, report-executive, recon-static, recon-blackbox, vuln-authz, ssrf-exploit, recon, vuln-ssrf, vuln-injection, vuln-auth, xss-exploit, vuln-xss。

**含 save-deliverable 的 11 模板**：pre-recon-code, vuln-authz, injection-exploit, authz-exploit, recon, ssrf-exploit, xss-exploit, vuln-auth, vuln-ssrf, vuln-xss, vuln-injection。

## save-deliverable 移除细节

以 pre-recon-code.txt 为例（line 26/101-104/170/465）：
- 删 save-deliverable CLI 工具描述块（line 101-104 的 Usage/Returns）
- line 26（"MUST save report using save-deliverable CLI"）、line 170（"Run save-deliverable --type CODE_ANALYSIS --file-path ..."）、line 465（"via save-deliverable with --file-path"）→ 改"用 Write 工具写到 `{{DELIVERABLES_PATH}}/pre_recon_deliverable.md`"
- 其他 10 模板的 save-deliverable 引用同样改 Write 指示

Python `validate_deliverable`（`executor.py:106`）已校验 deliverable 文件存在——agent 写错位置/文件名时 validate 报 `Missing deliverable`，兜底。

## 测试要点

- **`_interpolate`**：`{{DELIVERABLES_PATH}}`/`{{SCRATCHPAD_PATH}}` 替换为传入的 session 路径
- **executor variables**：注入 session 维度 `deliverables_path` + `scratchpad_path`（绝对路径，含 session 名）
- **grep 验证**：`prompts/*.txt` 无残留 `.shannon/deliverables`、`save-deliverable`、`.shannon/scratchpad`
- **人工冒烟**（Phase 1 Task 6 续）：白盒 pre-recon 写 `workspaces/<session>/deliverables/pre_recon_deliverable.md`，被扫 repo 内不出现 `.shannon/`；agent 不再找 save-deliverable.js

## 不在范围内（YAGNI）

- save-deliverable Python CLI 实现（移除引用，不替代——validate 兜底足够）
- scratchpad 的 Python 侧主动管理（agent 自建；`session.py` clean_workspace 已删 `workspace/scratchpad`，位置已对齐）
- prompt 内容/分析流程调整（只迁路径变量 + save-deliverable→Write，不改分析逻辑）
- 黑盒 deliverables 路径（Phase 1 已迁；黑盒 executor 经 `AgentExecutor.execute`，落点 2 单点覆盖，无需单独注入）
