# Workspace 目录名时间戳 → 人类可读日期时间 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把新建 workspace 目录名里的毫秒 epoch 时间戳（`NodeGoat_shannon-1782041072350`）换成人类可读的本地时区紧凑秒级日期时间（`NodeGoat_20260619-143000`），并保证同秒同名碰撞不串数据、resume 幂等语义不变。

**Architecture:** 唯一改动点在 `SessionManager.create_workspace` 的默认命名分支——把内联的 `session_id = f"shannon-{int(time.time()*1000)}"` 抽成私有方法 `_default_workspace_name(hostname)`，用 `datetime.now().strftime("%Y%m%d-%H%M%S")` 生成日期时间后缀，并在方法内做同名碰撞兜底（追加 `-2`/`-3`…）。显式传 `name`（resume 场景）不进该方法，保持既有 `session.json` 幂等 `return ws` 逻辑不变。whitebox / blackbox / multi 的默认入口都走 core 这个分支，改一处全部生效。

**Tech Stack:** Python 3、pytest、pathlib、datetime（标准库，无新依赖）。

## Global Constraints

- 时区：目录名时间用 `datetime.now()` = **服务器本地时区**（spec 确定）。
- 文件名安全：目录名**不含冒号**（跨平台 / Shell 安全）。
- 不迁移老目录：现有 `*_shannon-<毫秒>` 目录保留原样，新逻辑只对新建生效。
- `session.json` 内的 `created_at` / `completed_at` 保持 `time.time()` 浮点秒**不动**（程序消费）。
- 不改 Temporal workflow id 相关代码（`resolve_workflow_id` 等）——它们以 `workspace_name` 为前缀，会顺带变可读，属附带收益。
- 测试只跑改动相关文件：`pytest packages/core/tests/test_session.py -v`（CLAUDE.md 约定：勿广跑全套）。

---

## File Structure

- **Modify:** `packages/core/src/shannon_core/session.py`
  - 加 `from datetime import datetime` import。
  - 新增私有方法 `_default_workspace_name(self, hostname: str) -> str`。
  - `create_workspace` 的 `if not name:` 分支：删除 `session_id` 两行，改为调用 `self._default_workspace_name(hostname)`。
  - `get_scan_type` 的 fallback 处加一行注释说明新格式不含 `"blackbox"` 词。
- **Modify:** `packages/core/tests/test_session.py`
  - 顶部加 `import re` 和 `from datetime import datetime`。
  - 更新既有 line 43、line 53 的 `assert "shannon-" in ws.name` 断言为新格式断言。
  - 新增 4 个测试锚点。

---

## Task 1: 目录名改人类可读日期时间（核心格式 + 既有测试适配 + 回归锚点）

**Files:**
- Modify: `packages/core/src/shannon_core/session.py`（import 区 + `create_workspace` line 14-24 + `get_scan_type` line 89-100）
- Test: `packages/core/tests/test_session.py`（import 区 + line 43 / line 53 + 新增测试）

**Interfaces:**
- Consumes: 无（首个 task）。
- Produces: `SessionManager._default_workspace_name(self, hostname: str) -> str`——返回 `<hostname>_YYYYMMDD-HHMMSS`。Task 2 会在同一方法内加碰撞兜底循环。

- [ ] **Step 1: 写失败的格式测试**

在 `packages/core/tests/test_session.py` 顶部 import 区（line 1-4 之后）加：

```python
import re
from datetime import datetime
```

在文件末尾追加：

```python
def test_workspace_name_human_readable_format(tmp_path):
    """新 workspace 名为 <hostname>_YYYYMMDD-HHMMSS（本地时区紧凑秒级，无冒号）。"""
    mgr = SessionManager(tmp_path / "workspaces")
    before = datetime.now()
    ws = mgr.create_workspace(web_url="", repo_path="/repo/NodeGoat", name=None)
    after = datetime.now()
    # 格式：hostname_YYYYMMDD-HHMMSS
    assert re.match(r"^NodeGoat_\d{8}-\d{6}$", ws.name), ws.name
    # 日期部分 = 今天（本地时区），证明是真实当前时间而非占位
    parsed = datetime.strptime(ws.name.split("_", 1)[1], "%Y%m%d-%H%M%S")
    assert parsed.strftime("%Y%m%d") == before.strftime("%Y%m%d")


def test_legacy_timestamp_dirs_still_listable(tmp_path):
    """老格式目录（shannon-<毫秒>）仍能被 list_workspaces / get_session_data 处理。"""
    mgr = SessionManager(tmp_path / "workspaces")
    legacy = tmp_path / "workspaces" / "NodeGoat_shannon-1782041072350"
    legacy.mkdir()
    (legacy / "session.json").write_text(json.dumps({
        "web_url": "",
        "repo_path": "/repo",
        "created_at": 1782041072.350,
        "scan_type": "whitebox",
        "status": "completed",
    }))
    workspaces = mgr.list_workspaces()
    assert legacy in workspaces
    data = mgr.get_session_data(legacy)
    assert data["scan_type"] == "whitebox"
```

- [ ] **Step 2: 跑格式测试确认它失败**

Run: `pytest packages/core/tests/test_session.py::test_workspace_name_human_readable_format -v`
Expected: FAIL —— 当前目录名是 `NodeGoat_shannon-<毫秒>`，`re.match(r"^NodeGoat_\d{8}-\d{6}$")` 不匹配（`shannon-` 不是 8 位数字）。

（`test_legacy_timestamp_dirs_still_listable` 此时预期 PASS——它是回归锚点，验证既有 `list_workspaces` / `get_session_data` 不挑格式。）

- [ ] **Step 3: 实现 `_default_workspace_name` + 改 `create_workspace`**

在 `packages/core/src/shannon_core/session.py` 顶部 import 区（`import time` 之后）加：

```python
from datetime import datetime
```

把 `create_workspace` 里的默认命名分支（原 line 22-24）：

```python
            hostname = Path(repo_path).name.replace(".", "-") or "repo"
            session_id = f"shannon-{int(time.time() * 1000)}"
            name = f"{hostname}_{session_id}"
```

改为：

```python
            hostname = Path(repo_path).name.replace(".", "-") or "repo"
            name = self._default_workspace_name(hostname)
```

在 `SessionManager` 类内、`create_workspace` 方法之后新增私有方法：

```python
    def _default_workspace_name(self, hostname: str) -> str:
        """生成默认 workspace 名：<hostname>_YYYYMMDD-HHMMSS（本地时区紧凑秒级，无冒号）。

        Task 2 会在此方法内追加同秒同名碰撞兜底（-2/-3 序号）。
        """
        return f"{hostname}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
```

- [ ] **Step 4: 更新依赖旧格式的既有断言（line 43 / line 53）**

`test_create_workspace_names_after_repo_basename_when_no_url` 中，把：

```python
    assert ws.name.startswith("myapp_")
    assert "shannon-" in ws.name
```

改为：

```python
    assert ws.name.startswith("myapp_")
    assert re.match(r"\d{8}-\d{6}$", ws.name), ws.name
```

`test_create_workspace_names_after_hostname_when_url_given` 中，把：

```python
    assert ws.name.startswith("git-example-com_")
    assert "shannon-" in ws.name
```

改为：

```python
    assert ws.name.startswith("git-example-com_")
    assert re.match(r"\d{8}-\d{6}$", ws.name), ws.name
```

- [ ] **Step 5: 在 `get_scan_type` fallback 处加注释**

`get_scan_type` 方法（原 line 89-100）的 fallback 分支前加注释，说明新格式目录名不含 `"blackbox"`：

```python
        name = workspace_path.name.lower()
        # 新格式目录名（<hostname>_YYYYMMDD-HHMMSS）不含 "blackbox" 词；
        # 此 fallback 仅在 session.json 缺 scan_type 时触发，新建均有 scan_type，不受影响。
        if "blackbox" in name:
            return "blackbox"
        return "whitebox"
```

- [ ] **Step 6: 跑 test_session.py 全部，确认通过**

Run: `pytest packages/core/tests/test_session.py -v`
Expected: PASS（全部既有测试 + 2 个新测试）。`time` import 仍被 `created_at`/`completed_at`/`mark_completed` 使用，保留。

- [ ] **Step 7: 提交**

```bash
git add packages/core/src/shannon_core/session.py packages/core/tests/test_session.py
git commit -m "refactor(session): workspace 目录名改人类可读日期时间

目录名 <hostname>_YYYYMMDD-HHMMSS（本地时区紧凑秒级，无冒号），
替换原 shannon-<毫秒epoch>。抽 _default_workspace_name 私有方法。
更新 2 处既有 shannon- 断言；加格式测试 + 老目录回归锚点。"
```

---

## Task 2: 同秒同名碰撞兜底 + resume 幂等保护

**Files:**
- Modify: `packages/core/src/shannon_core/session.py`（`_default_workspace_name` 方法加 while 循环）
- Test: `packages/core/tests/test_session.py`（新增 2 个测试）

**Interfaces:**
- Consumes: Task 1 的 `_default_workspace_name(self, hostname) -> str`（返回 `<hostname>_YYYYMMDD-HHMMSS`）。
- Produces: `_default_workspace_name` 行为升级——同名碰撞（目标目录已存在且含 `session.json`）时返回 `<base>-2` / `-3`…；`create_workspace` 显式 `name` 路径的幂等 `return ws` 不变。

- [ ] **Step 1: 写失败的碰撞兜底测试**

在 `packages/core/tests/test_session.py` 末尾追加：

```python
def test_workspace_name_collision_appends_suffix(tmp_path):
    """同秒同名二次创建追加 -2，不覆盖既有 session.json，两目录独立。"""
    mgr = SessionManager(tmp_path / "workspaces")
    ws1 = mgr.create_workspace(web_url="", repo_path="/repo/NodeGoat", name=None)
    # 同一秒、同一 hostname 再建一个 → 默认名相同，应追加 -2
    ws2 = mgr.create_workspace(web_url="", repo_path="/repo/NodeGoat", name=None)
    assert ws2.name == f"{ws1.name}-2", (ws1.name, ws2.name)
    assert ws1 != ws2
    assert (ws1 / "session.json").exists()
    assert (ws2 / "session.json").exists()


def test_explicit_name_keeps_idempotent_return(tmp_path):
    """显式传 name（resume 场景）+ session.json 已存在 → 幂等 return 同一目录，不追加序号、不覆盖。"""
    mgr = SessionManager(tmp_path / "workspaces")
    ws1 = mgr.create_workspace(web_url="", repo_path="/repo", name="myapp_run1")
    assert ws1.name == "myapp_run1"
    # 同名 resume → 应返回同一目录，不得变成 myapp_run1-2
    ws2 = mgr.create_workspace(web_url="", repo_path="/repo", name="myapp_run1")
    assert ws2 == ws1
    assert ws2.name == "myapp_run1"
```

- [ ] **Step 2: 跑碰撞测试确认它失败**

Run: `pytest packages/core/tests/test_session.py::test_workspace_name_collision_appends_suffix -v`
Expected: FAIL —— Task 1 的 `_default_workspace_name` 无兜底，第二次创建得到与 `ws1` 同名目录，`create_workspace` 的幂等 `return ws` 让 `ws2 == ws1`，于是 `ws2.name == f"{ws1.name}-2"` 断言失败。

（`test_explicit_name_keeps_idempotent_return` 预期 PASS——显式 `name` 本就走幂等 return，它是锁定该语义不被兜底逻辑误伤的锚点。）

- [ ] **Step 3: 给 `_default_workspace_name` 加碰撞兜底循环**

把 Task 1 写的 `_default_workspace_name`：

```python
    def _default_workspace_name(self, hostname: str) -> str:
        """生成默认 workspace 名：<hostname>_YYYYMMDD-HHMMSS（本地时区紧凑秒级，无冒号）。

        Task 2 会在此方法内追加同秒同名碰撞兜底（-2/-3 序号）。
        """
        return f"{hostname}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
```

改为：

```python
    def _default_workspace_name(self, hostname: str) -> str:
        """生成默认 workspace 名：<hostname>_YYYYMMDD-HHMMSS（本地时区紧凑秒级，无冒号）。

        同秒同名碰撞（目标目录已存在且含 session.json）时追加 -2/-3… 序号，
        避免错误复用别人的目录。显式 name（resume）不走本方法，幂等 return 不受影响。
        """
        base = f"{hostname}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        name = base
        i = 2
        while (self.workspaces_dir / name / "session.json").exists():
            name = f"{base}-{i}"
            i += 1
        return name
```

- [ ] **Step 4: 跑 test_session.py 全部，确认通过**

Run: `pytest packages/core/tests/test_session.py -v`
Expected: PASS（Task 1 全部 + `test_workspace_name_collision_appends_suffix` + `test_explicit_name_keeps_idempotent_return`）。注意碰撞测试依赖两次调用落在同一秒生成相同 `base`——pytest 默认足够快，若偶发跨秒导致 `ws2.name` 不含 `-2`，重跑即可（真实使用中人不会 1 秒内手建两个）。

- [ ] **Step 5: 提交**

```bash
git add packages/core/src/shannon_core/session.py packages/core/tests/test_session.py
git commit -m "feat(session): 同秒同名 workspace 碰撞兜底 + resume 幂等保护

_default_workspace_name 检测目录已存在且含 session.json 时追加 -2/-3 序号；
显式 name（resume）仍走 create_workspace 既有幂等 return，不进兜底循环。
加碰撞测试 + resume 幂等锚点。"
```

---

## 人工冒烟（plan 实现完成后，可选但推荐）

真机跑一次白盒扫描，确认新目录名落地：

```bash
# 用一个小仓跑白盒（具体命令以项目 CLI 为准），观察 workspaces/ 下新目录名
# 预期：workspaces/<hostname>_YYYYMMDD-HHMMSS/  （而非 shannon-<毫秒>）
ls -1 workspaces/ | tail -5
```

确认目录名形如 `NodeGoat_20260628-143000`，`session.json` 正常生成，老目录仍在。
