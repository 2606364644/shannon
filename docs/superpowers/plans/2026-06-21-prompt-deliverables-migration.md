# prompt 模板 deliverables 迁移至 session（Phase 2）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 15 个 prompt 模板硬编码的 `{{REPO_PATH}}/.shannon/deliverables` 迁到 session 维度变量 `{{DELIVERABLES_PATH}}`/`{{SCRATCHPAD_PATH}}`，移除虚构 `save-deliverable` CLI 改 Write 工具，让 agent 不再写被扫 repo。

**Architecture:** 2 处变量注入（`_interpolate` 加 replace + executor variables 单点注入，白盒/黑盒都经 `AgentExecutor.execute`）+ 15 模板批量路径替换 + 11 模板 save-deliverable→Write。Python `validate_deliverable` 兜底。

**Tech Stack:** Python 3, prompt 模板 `.txt`（`shannon-py/prompts/`），pytest。

**Spec:** `docs/superpowers/specs/2026-06-21-prompt-deliverables-migration-design.md`

## Global Constraints

- deliverables/scratchpad 落 session 绝对路径（`{{DELIVERABLES_PATH}}`/`{{SCRATCHPAD_PATH}}`），不再 `{{REPO_PATH}}/.shannon/*`。
- 移除 save-deliverable CLI 引用，改 Write 工具（Python `validate_deliverable` 兜底）。
- 所有 agent 经 `AgentExecutor.execute`（白盒 `run_agent` + 黑盒 `recon_executor`/`exploit_executor`），variables 单点注入。
- **跑测试只跑子集**：`pytest packages/<pkg>/tests/<file>.py -v`，**绝不跑全量**（卡 Temporal hang）。
- prompt 模板是 `.txt`（无单测），验证靠 grep 无残留 + Task 4 人工冒烟。
- commit 只 `git add` 指定文件，**绝不** `git add .` / `git add uv.lock`（工作区有预存 `uv.lock` 改动）。

---

### Task 1: 变量注入基础设施（_interpolate + executor variables）

**Files:**
- Modify: `packages/core/src/shannon_core/prompts/manager.py`（`_interpolate`，约 `{{REPO_PATH}}` replace 行后）
- Modify: `packages/core/src/shannon_core/agents/executor.py:55`（`variables`）
- Test: `packages/core/tests/prompts/test_deliverables_path_interpolation.py`（新建）

**Interfaces:**
- Produces: `{{DELIVERABLES_PATH}}`/`{{SCRATCHPAD_PATH}}` 在 `_interpolate` 中可替换；executor variables 含 `deliverables_path`/`scratchpad_path`（session 绝对路径）。
- Consumes: Phase 1 的 `resolve_deliverables_path`（executor 已有 `deliverables = Path(deliverables_path)` 于 `executor.py:47`）。

- [ ] **Step 1: 写失败测试 — _interpolate 替换新变量**

新建 `packages/core/tests/prompts/test_deliverables_path_interpolation.py`：
```python
from pathlib import Path
from shannon_core.prompts.manager import PromptManager


def test_interpolate_deliverables_and_scratchpad_path(tmp_path):
    """{{DELIVERABLES_PATH}}/{{SCRATCHPAD_PATH}} 应被 variables 中对应值替换。"""
    (tmp_path / "t.txt").write_text(
        "out: {{DELIVERABLES_PATH}}/pre_recon_deliverable.md\n"
        "scratch: {{SCRATCHPAD_PATH}}/notes.md\n"
        "repo: {{REPO_PATH}}\n",
        encoding="utf-8",
    )
    mgr = PromptManager(tmp_path)
    result = mgr.load_sync(
        "t",
        variables={
            "web_url": "",
            "repo_path": "/data/NodeGoat",
            "deliverables_path": "/ws/NodeGoat_sess/deliverables",
            "scratchpad_path": "/ws/NodeGoat_sess/scratchpad",
        },
    )
    assert "/ws/NodeGoat_sess/deliverables/pre_recon_deliverable.md" in result
    assert "/ws/NodeGoat_sess/scratchpad/notes.md" in result
    assert "repo: /data/NodeGoat" in result
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run pytest packages/core/tests/prompts/test_deliverables_path_interpolation.py -v`
Expected: FAIL（`{{DELIVERABLES_PATH}}` 未被替换，原样输出）。

- [ ] **Step 3: _interpolate 加 replace**

`packages/core/src/shannon_core/prompts/manager.py` 的 `_interpolate`，在 `result = result.replace("{{REPO_PATH}}", variables.get("repo_path", ""))` 这行之后插入：
```python
        result = result.replace("{{DELIVERABLES_PATH}}", variables.get("deliverables_path", ""))
        result = result.replace("{{SCRATCHPAD_PATH}}", variables.get("scratchpad_path", ""))
```

- [ ] **Step 4: executor variables 加 session 路径**

`packages/core/src/shannon_core/agents/executor.py:55`，把：
```python
        variables = {"web_url": web_url, "repo_path": str(repo)}
```
替换为（`deliverables` 已在 `:47` 定义为 `Path(deliverables_path)`）：
```python
        variables = {
            "web_url": web_url,
            "repo_path": str(repo),
            "deliverables_path": str(deliverables),
            "scratchpad_path": str(deliverables.parent / "scratchpad"),
        }
```

- [ ] **Step 5: 跑测试验证通过**

Run: `uv run pytest packages/core/tests/prompts/test_deliverables_path_interpolation.py -v`
Expected: PASS。

- [ ] **Step 6: 回归 prompt 渲染测试**

Run: `uv run pytest packages/core/tests/prompts/ -v`
Expected: PASS（新增变量不影响现有 `{{REPO_PATH}}`/`{{WEB_URL}}` 替换）。

- [ ] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/prompts/manager.py packages/core/src/shannon_core/agents/executor.py packages/core/tests/prompts/test_deliverables_path_interpolation.py
git commit -m "feat(prompts): 注入 {{DELIVERABLES_PATH}}/{{SCRATCHPAD_PATH}} session 路径"
```

---

### Task 2: 模板路径变量化（15 模板 .shannon/deliverables + scratchpad）

把模板里所有 `.shannon/deliverables`（含 `{{REPO_PATH}}/.shannon/deliverables` 和裸的）替换为 `{{DELIVERABLES_PATH}}`，`.shannon/scratchpad` 替换为 `{{SCRATCHPAD_PATH}}`。

**Files:**
- Modify: `prompts/*.txt`（15 个含 deliverables，3 个含 scratchpad）

**Interfaces:**
- Consumes: Task 1 的 `{{DELIVERABLES_PATH}}`/`{{SCRATCHPAD_PATH}}` 变量。

- [ ] **Step 1: 记录迁移前命中数（基线）**

Run:
```bash
echo "deliverables 含 REPO_PATH:"; grep -rc "{{REPO_PATH}}/.shannon/deliverables" prompts/*.txt | grep -v ":0" | wc -l
echo "deliverables 裸:"; grep -rn "\.shannon/deliverables" prompts/*.txt | grep -v "{{REPO_PATH}}" | wc -l
echo "scratchpad:"; grep -rc "{{REPO_PATH}}/.shannon/scratchpad" prompts/*.txt | grep -v ":0" | wc -l
```
记录数字，迁移后对比。

- [ ] **Step 2: 替换 {{REPO_PATH}}/.shannon/deliverables → {{DELIVERABLES_PATH}}**

Run:
```bash
sed -i '' 's|{{REPO_PATH}}/.shannon/deliverables|{{DELIVERABLES_PATH}}|g' prompts/*.txt
```
（先替换完整路径形式，避免下一步误伤。）

- [ ] **Step 3: 替换裸 .shannon/deliverables → {{DELIVERABLES_PATH}}**

Run:
```bash
sed -i '' 's|\.shannon/deliverables|{{DELIVERABLES_PATH}}|g' prompts/*.txt
```
（此时 `{{REPO_PATH}}/.shannon/deliverables` 已变成 `{{DELIVERABLES_PATH}}`，不含 `.shannon/deliverables`，故此步只替换剩余裸形式，如 exploit 模板的输入/输出路径。）

- [ ] **Step 4: 替换 .shannon/scratchpad → {{SCRATCHPAD_PATH}}**

Run:
```bash
sed -i '' 's|{{REPO_PATH}}/.shannon/scratchpad|{{SCRATCHPAD_PATH}}|g' prompts/*.txt
```

- [ ] **Step 5: 验证无残留 .shannon/deliverables 或 .shannon/scratchpad**

Run:
```bash
grep -rn "\.shannon/deliverables\|\.shannon/scratchpad" prompts/*.txt
```
Expected: **无输出**（零残留）。若有输出，逐个手动改为 `{{DELIVERABLES_PATH}}`/`{{SCRATCHPAD_PATH}}`。

- [ ] **Step 6: 抽查替换正确（pre-recon + 一个 exploit）**

Run:
```bash
grep -n "DELIVERABLES_PATH\|SCRATCHPAD_PATH" prompts/pre-recon-code.txt prompts/auth-exploit.txt | head
```
Expected: 看到 `{{DELIVERABLES_PATH}}/pre_recon_deliverable.md`、`{{DELIVERABLES_PATH}}/auth_exploitation_queue.json` 等，路径语义正确（不再有 `{{REPO_PATH}}/.shannon` 前缀）。

- [ ] **Step 7: Commit**

```bash
git add prompts/
git commit -m "refactor(prompts): deliverables/scratchpad 路径迁至 {{DELIVERABLES_PATH}}/{{SCRATCHPAD_PATH}}"
```

---

### Task 3: 移除 save-deliverable CLI，改 Write 工具（11 模板）

11 个模板引用 shannon-py 不存在的 `save-deliverable` CLI（agent 跨项目找 JS 版写 repo/.shannon）。改为"用 Write 工具写到 `{{DELIVERABLES_PATH}}/<file>`"，Python `validate_deliverable` 兜底。

**Files:**
- Modify: `prompts/{pre-recon-code,recon,vuln-auth,vuln-authz,vuln-injection,vuln-ssrf,vuln-xss,authz-exploit,injection-exploit,ssrf-exploit,xss-exploit}.txt`

**Interfaces:**
- Consumes: Task 2 的 `{{DELIVERABLES_PATH}}`（save-deliverable 指示里的路径已是 `{{DELIVERABLES_PATH}}/X`）。

**改法模式（三类引用，逐模板处理）：**

1. **工具描述块**（11 模板逐字相同，2-4 行）：
   ```
   - **save-deliverable (CLI Tool):** Saves your deliverable files with automatic validation.
     - **Usage:** `save-deliverable --type <TYPE> --file-path <path>` or `--content '<text>'`
     - **Returns:** ...（仅 pre-recon 有）
     - **For large reports:** ...（仅 pre-recon 有）
   ```
   → 整块删除（Write 是 SDK 标准工具，不需在 prompt 描述）。

2. **指示句 "save it using the save-deliverable CLI with --type X"**：
   → 改为 `write it to {{DELIVERABLES_PATH}}/<filename> using the Write tool`（filename 对应该 agent 的 deliverable，如 `pre_recon_deliverable.md`）。

3. **步骤句 "Run save-deliverable --type X --file-path {{DELIVERABLES_PATH}}/Y"**（Task 2 后路径已是变量）：
   → 改为 `Use the Write tool to write {{DELIVERABLES_PATH}}/Y`。

- [ ] **Step 1: 删除工具描述块（11 模板逐字相同的前两行）**

Run:
```bash
for f in prompts/pre-recon-code.txt prompts/recon.txt prompts/vuln-auth.txt prompts/vuln-authz.txt prompts/vuln-injection.txt prompts/vuln-ssrf.txt prompts/vuln-xss.txt prompts/authz-exploit.txt prompts/injection-exploit.txt prompts/ssrf-exploit.txt prompts/xss-exploit.txt; do
  grep -n "save-deliverable (CLI Tool)" "$f"
done
```
对每个命中文件，删除 `- **save-deliverable (CLI Tool):**` 及其紧随的 `  - **Usage:** ...` 行（pre-recon 还删 `Returns`/`For large reports` 行）。用编辑器或 `sed` 按行号删。

- [ ] **Step 2: 改指示句（逐模板，按上面模式 2/3）**

Run:
```bash
grep -rn "save-deliverable" prompts/*.txt
```
对每个剩余命中，按模式改成 Write 工具。例如：
- pre-recon-code.txt:26 `MUST save ... using the save-deliverable CLI tool with --type CODE_ANALYSIS` → `MUST save your complete analysis report to {{DELIVERABLES_PATH}}/pre_recon_deliverable.md using the Write tool`
- pre-recon-code.txt:170 `Run save-deliverable with --type CODE_ANALYSIS --file-path "{{DELIVERABLES_PATH}}/pre_recon_deliverable.md"` → `Use the Write tool to write {{DELIVERABLES_PATH}}/pre_recon_deliverable.md`
- pre-recon-code.txt:465 `via save-deliverable with --file-path, not inline --content` → `via the Write tool`
- vuln-*.txt 的 `Save your deliverable markdown via save-deliverable first` → `Write your deliverable markdown via the Write tool first`
- recon.txt:495 `Do NOT pass your report as inline --content to save-deliverable` → `Write the report directly with the Write tool`

（implementer 逐条 grep 定位 + 按语义改；目标是所有 save-deliverable 引用消失，agent 改用 Write。）

- [ ] **Step 3: 验证无 save-deliverable 残留**

Run:
```bash
grep -rn "save-deliverable" prompts/*.txt
```
Expected: **无输出**（零残留）。

- [ ] **Step 4: 抽查 Write 指示语义正确**

Run:
```bash
grep -n "Write tool" prompts/pre-recon-code.txt prompts/vuln-auth.txt | head
```
Expected: 看到 `write ... using the Write tool` 指示，指向 `{{DELIVERABLES_PATH}}/<file>`。

- [ ] **Step 5: Commit**

```bash
git add prompts/
git commit -m "refactor(prompts): 移除 save-deliverable CLI 引用改 Write 工具"
```

---

### Task 4: 人工冒烟（不进 pytest）

> 验证 agent 真的写 session 不写 repo（Phase 1 核心目标 + Phase 2 修复）。subagent 无法真跑 Temporal，用户手动。

**Files:** 无代码改动。

- [ ] **Step 1: 清理 NodeGoat 旧残留**

Run: `rm -rf /Users/mango/project/vuln-range/NodeGoat/.shannon`（迁移前 repo/.shannon 残留，spec 决策不迁移，手动清）。

- [ ] **Step 2: 白盒 pre-recon 冒烟**

Run: `uv run shannon-whitebox start -r /Users/mango/project/vuln-range/NodeGoat`
验证（pre-recon 阶段完成后即可 Ctrl+C）：
- agent 日志**不再出现** `save-deliverable` 或寻找 save-deliverable.js
- `workspaces/<session>/deliverables/pre_recon_deliverable.md` 生成
- **被扫 repo 内不出现 `.shannon/`**

- [ ] **Step 3: 黑盒冒烟（可选，pre-recon 通过后）**

Run: `uv run shannon-blackbox start --url <NodeGoat 部署 URL>`
验证：黑盒接白盒 session deliverables，evidence 落 `workspaces/<白盒session>/deliverables/`。

- [ ] **Step 4: 记录冒烟结果**

冒烟通过后 commit 一行说明到 spec 末尾"验证状态"，或 commit message 注明。
