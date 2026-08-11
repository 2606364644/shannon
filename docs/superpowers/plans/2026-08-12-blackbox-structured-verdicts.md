# 黑盒结构化 verdict 落盘 + 工作区计数对齐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让黑盒 scan 产出结构化 `{vc}_exploit_verdicts.json`，使工作区扫描列表对黑盒显示"成功 exploit 数"，并让主线两个孤儿消费者（coverage/PoC）用上结构化数据。

**Architecture:** 黑盒 exploit 的结构化 verdict 已经在 renderer 链路的内存里（`VerdictValidation`），只是从未落盘。在 `renderers/__init__.py` 抽出 `_build_exploit_validation`（md 渲染 + payload 构造共用）并新增 `build_exploit_verdicts_payload`；executor 在写 evidence.md 的同处把 payload 写到 `deliverables/blackbox/{vc}_exploit_verdicts.json`；`get_workspace_vuln_counts` 加扫该文件数 `status=exploited`。

**实现调整（偏离 spec §4.1）：** spec 设想 `render_deliverable` 改返回 `(md, payload)` tuple。调研发现 `render_deliverable` 有 ~16 个调用点（15 测试 + executor），改签名牵连过大。改用**独立函数 `build_exploit_verdicts_payload`**，`render_deliverable` 签名零改、现有测试零改；代价是 exploit 路径 validation 算两次（queue 小、开销可忽略）。逻辑仍 DRY（`_build_exploit_validation` 一份）。

**Tech Stack:** Python 3.13 / pytest / pydantic / temporalio。packages：core + blackbox。

## Global Constraints

- **改 core/blackbox src 须 rebuild supernova-worker 才生效**（真机验证前必须 rebuild）。
- **只跑改动相关测试文件，勿跑全套 pytest**（全套有预存 hang，见 CLAUDE.md §3）。
- 测试 i18n：`SUPERNOVA_AGENT_NARRATION_LANG=en`（autouse fixture 已设）。
- **`accepted_ids` 字段名锁死**：`exploitation_checker.py:227` + `poc_generator.py:802` 已读此字段，不得重命名。
- verdicts.json schema 是现有孤儿消费者测试 schema 的**超集**：`{vuln_class, accepted_ids, verdicts, rejected}`（现有测试只构造前两者 + rejected，不构造 verdicts；新增 `verdicts` 供计数器，向后兼容）。
- §1 双轨铁律：本改动不触碰双轨、不喂确定性产物给 LLM 轨。

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `packages/core/src/supernova_core/renderers/__init__.py` | exploit deliverable 渲染 + verdicts payload 构造 | 抽 `_build_exploit_validation`；新增 `build_exploit_verdicts_payload`；`__all__` 加它 |
| `packages/core/src/supernova_core/agents/executor.py` | agent 执行 + 产物写盘 | render 分支（~199-202）对 exploit agent 写 verdicts.json |
| `packages/core/src/supernova_core/workspace.py` | 漏洞计数 | `get_workspace_vuln_counts` 加 verdicts 支（数 exploited） |
| `packages/core/tests/renderers/test_render_deliverable_exploit.py` | renderer 测试 | 加 payload schema 测试 |
| `packages/core/tests/test_executor_vuln_render.py` | executor e2e 测试 | 加 verdicts.json 落盘测试 |
| `packages/core/tests/test_workspace.py` | 计数器测试 | 加 verdicts.json 计数测试 |

孤儿消费者（`exploitation_checker.py` / `poc_generator.py`）**读代码 + 测试均已就绪**（test_exploitation_checker.py:424-487、test_poc_generator.py:476），本计划不改它们，只在 Task 4 回归确认。

---

### Task 1: renderer 层 — 抽取 `_build_exploit_validation` + 新增 `build_exploit_verdicts_payload`

**Files:**
- Modify: `packages/core/src/supernova_core/renderers/__init__.py:42-78`（`_render_exploit_deliverable`）+ 顶部 `__all__`
- Test: `packages/core/tests/renderers/test_render_deliverable_exploit.py`（加新测试）

**Interfaces:**
- Produces: `build_exploit_verdicts_payload(vc: str, data: dict, deliverables_path, queue_root=None) -> dict`，返回 `{"vuln_class", "accepted_ids", "verdicts", "rejected"}`；`_build_exploit_validation(vc, data, deliverables_path, queue_root=None) -> (VerdictValidation, dict, dict)`。Task 2 的 executor 依赖 `build_exploit_verdicts_payload`。

- [ ] **Step 1: 写失败测试**

追加到 `packages/core/tests/renderers/test_render_deliverable_exploit.py` 末尾：

```python
def test_build_exploit_verdicts_payload_schema(tmp_path):
    """build_exploit_verdicts_payload 产 {vuln_class, accepted_ids, verdicts, rejected}。
    accepted_ids 含所有 accepted（非只 exploited）；verdicts 含 status 供计数器数 exploited；
    不在 queue 的 id 落 rejected（L2）。"""
    from supernova_core.renderers import build_exploit_verdicts_payload

    (tmp_path / "injection_exploitation_queue.json").write_text(json.dumps(
        {"vulnerabilities": [
            {"ID": "INJ-1", "vulnerability_type": "SQLi",
             "externally_exploitable": True, "confidence": "high"},
            {"ID": "INJ-2", "vulnerability_type": "SQLi",
             "externally_exploitable": True, "confidence": "high"}]}))
    data = {"verdicts": [
        {"vulnerability_id": "INJ-1", "status": "exploited", "severity": "critical",
         "impact": "i", "exploitation_steps": ["s"], "proof_of_impact": "p"},
        {"vulnerability_id": "INJ-2", "status": "blocked_by_security", "confidence": "high",
         "current_blocker": "b", "what_we_tried": "w", "evidence_of_vulnerability": "e",
         "expected_impact": "x"},
        {"vulnerability_id": "INJ-9", "status": "exploited", "severity": "low",
         "impact": "i", "exploitation_steps": ["s"], "proof_of_impact": "p"}]}
    payload = build_exploit_verdicts_payload("injection", data, deliverables_path=tmp_path)
    assert payload["vuln_class"] == "injection"
    assert set(payload["accepted_ids"]) == {"INJ-1", "INJ-2"}  # exploited + blocked 都算 accepted
    statuses = {v["status"] for v in payload["verdicts"]}
    assert statuses == {"exploited", "blocked_by_security"}
    assert {r["id"] for r in payload["rejected"]} == {"INJ-9"}  # INJ-9 不在 queue → L2 拒
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/renderers/test_render_deliverable_exploit.py::test_build_exploit_verdicts_payload_schema -v`
Expected: FAIL — `ImportError: cannot import name 'build_exploit_verdicts_payload'`

- [ ] **Step 3: 实现 — 抽取 `_build_exploit_validation` + 新增 `build_exploit_verdicts_payload`**

在 `packages/core/src/supernova_core/renderers/__init__.py`：

(a) 顶部 `__all__` 改为：
```python
__all__ = ["render_pre_recon", "render_deliverable", "build_exploit_verdicts_payload"]
```

(b) 把现有 `_render_exploit_deliverable`（第 42-78 行整段）替换为下面三个函数（逻辑原样拆分，`_render_exploit_deliverable` 行为不变 → 现有 exploit 测试零回归）：

```python
def _build_exploit_validation(vc, data, deliverables_path, queue_root=None):
    """读 queue → validate，返回 (validation, id_to_type, id_to_title)。

    抽自 _render_exploit_deliverable，供 md 渲染 + verdicts.json payload 构造共用
    （spec 2026-08-12：renderer 保持纯函数，payload 构造复用同一 validation 源，
    避免改 render_deliverable 公共签名牵连 ~16 个调用点）。
    """
    import json
    from pathlib import Path

    from supernova_core.collectors.exploit import validate_exploit_verdicts

    valid_ids: set[str] = set()
    id_to_type: dict[str, str] = {}
    id_to_title: dict[str, str] = {}
    if deliverables_path is not None:
        from supernova_core.utils.paths import resolve_track_deliverable, WHITEBOX_SUBDIR

        # 读 queue 的根：queue_root 优先（黑盒 = 白盒 repo_path/deliverables，queue 在
        # whitebox/ 子目录）；缺省回落 deliverables_path（whitebox：已含 whitebox/ 或平铺）。
        read_root = queue_root if queue_root is not None else deliverables_path
        queue_path = resolve_track_deliverable(Path(read_root), WHITEBOX_SUBDIR, f"{vc}_exploitation_queue.json")
        if queue_path.exists():
            try:
                from supernova_core.models.queue_schemas import VulnerabilityQueue

                parsed = VulnerabilityQueue.parse_lenient(queue_path.read_text(encoding="utf-8"))
                for v in parsed.queue.vulnerabilities:
                    vid = getattr(v, "ID", None)
                    if vid:
                        valid_ids.add(vid)
                        id_to_type[vid] = getattr(v, "vulnerability_type", vc)
                        title = getattr(v, "title", None)
                        if title:
                            id_to_title[vid] = title
            except (json.JSONDecodeError, OSError):
                pass
    entries = (data or {}).get("verdicts", []) if isinstance(data, dict) else (data or [])
    validation = validate_exploit_verdicts(entries, valid_ids)
    return validation, id_to_type, id_to_title


def _render_exploit_deliverable(vc, data, deliverables_path, queue_root=None):
    from supernova_core.renderers.exploit import render_exploit

    validation, id_to_type, id_to_title = _build_exploit_validation(
        vc, data, deliverables_path, queue_root)
    return render_exploit(vc, validation, id_to_type, id_to_title)


def build_exploit_verdicts_payload(vc, data, deliverables_path, queue_root=None) -> dict:
    """构造 ``{vc}_exploit_verdicts.json`` payload（补全主线缺失产物，spec 2026-08-12）。

    schema = {vuln_class, accepted_ids, verdicts, rejected}（孤儿消费者测试 schema 的超集）：
    - accepted_ids：所有 accepted verdict 的 id（exploited+blocked+potential+other），
      coverage/PoC 消费者读此字段（凡 accepted 即算覆盖）。
    - verdicts：完整 accepted verdict（含 status），计数器据此数 exploited。
    - rejected：[{id, reason}]（L1/L2/L3 拒因，调试可见性）。
    复用 _build_exploit_validation → 与 evidence.md 渲染同源同口径。
    """
    validation, _, _ = _build_exploit_validation(vc, data, deliverables_path, queue_root)
    return {
        "vuln_class": vc,
        "accepted_ids": [v.vulnerability_id for v in validation.accepted],
        "verdicts": [v.model_dump() for v in validation.accepted],
        "rejected": [
            {
                "id": (raw.get("vulnerability_id", "<unknown>")
                       if isinstance(raw, dict) else "<unknown>"),
                "reason": reason,
            }
            for raw, reason in validation.rejected
        ],
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/renderers/test_render_deliverable_exploit.py -v`
Expected: PASS（新测试 + 全部既有 exploit renderer 测试零回归）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/renderers/__init__.py packages/core/tests/renderers/test_render_deliverable_exploit.py
git commit -m "feat(core): build_exploit_verdicts_payload 构造黑盒 verdicts.json payload"
```

---

### Task 2: executor 对 exploit agent 写 verdicts.json

**Files:**
- Modify: `packages/core/src/supernova_core/agents/executor.py:199-205`（render 分支）
- Test: `packages/core/tests/test_executor_vuln_render.py`（加新测试）

**Interfaces:**
- Consumes: Task 1 的 `build_exploit_verdicts_payload(vc, data, deliverables_path, queue_root)`；`atomic_write_json`（executor 已 import，见 :192）；`blackbox_dir`（paths.py:121）；`AgentName`（executor 处理 agent_name 已 import，若顶部未 import 则在分支内补 `from supernova_core.models.agents import AgentName`）。
- Produces: exploit agent 跑完后 `deliverables/blackbox/{vc}_exploit_verdicts.json` 落盘。

- [ ] **Step 1: 写失败测试**

追加到 `packages/core/tests/test_executor_vuln_render.py` 末尾（复用同文件 `_patch_executor_env` + `FakeResult`，仿既有 `test_injection_exploit_renders_evidence_using_queue_root`）：

```python
@pytest.mark.asyncio
async def test_injection_exploit_writes_verdicts_json(monkeypatch, tmp_path):
    """exploit agent 跑完后 verdicts.json 落盘 deliverables/blackbox/（spec 2026-08-12）。
    补全主线缺失产物：计数器数 exploited、coverage/PoC 读 accepted_ids。"""
    from supernova_core.agents import executor as exec_mod
    from supernova_core.models.agents import AgentName

    repo = tmp_path / "repo"
    repo.mkdir()
    deliverables = tmp_path / "deliverables"  # 黑盒产物落点

    queue_root = tmp_path / "whitebox-root"  # 白盒根：queue 在 whitebox/ 子目录
    (queue_root / "whitebox").mkdir(parents=True)
    (queue_root / "whitebox" / "injection_exploitation_queue.json").write_text(json.dumps(
        {"vulnerabilities": [
            {"ID": "INJ-VULN-01", "vulnerability_type": "SQLi",
             "externally_exploitable": True, "confidence": "high"}]}))

    async def fake_run(**kw):
        collector = kw.get("collector")
        if collector is not None:
            collector.append_section("add_exploit", {
                "vulnerability_id": "INJ-VULN-01", "status": "exploited", "severity": "critical",
                "impact": "i", "exploitation_steps": ["s"], "proof_of_impact": "p"})
        return FakeResult(structured_output=None)

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    executor = _patch_executor_env(monkeypatch, tmp_path)

    await executor.execute(
        agent_name=AgentName.INJECTION_EXPLOIT,
        repo_path=str(repo),
        deliverables_path=str(deliverables),
        queue_root=str(queue_root),
    )

    verdicts_file = deliverables / "blackbox" / "injection_exploit_verdicts.json"
    assert verdicts_file.exists(), "verdicts.json 应落盘 deliverables/blackbox/"
    payload = json.loads(verdicts_file.read_text(encoding="utf-8"))
    assert payload["vuln_class"] == "injection"
    assert "INJ-VULN-01" in payload["accepted_ids"]
    exploited = [v for v in payload["verdicts"] if v.get("status") == "exploited"]
    assert len(exploited) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_executor_vuln_render.py::test_injection_exploit_writes_verdicts_json -v`
Expected: FAIL — `assert verdicts_file.exists()` 断（文件未写）。

- [ ] **Step 3: 实现 — executor render 分支加 exploit verdicts.json 写盘**

在 `packages/core/src/supernova_core/agents/executor.py`，找到 render 分支（约 199-202 行）：

```python
        if not skip_artifact_postprocess and collector is not None:
            md = render_deliverable(agent_name, collector.get_all(), deliverables, queue_root=queue_root)
            if md is not None:
                (deliverables / defn.deliverable_filename).write_text(md, encoding="utf-8")
```

在其**紧后**（`if md is not None:` 块之后、`if not skip_artifact_postprocess: await validate_deliverable` 之前）插入：

```python
            # exploit agent 额外写结构化 verdicts.json（补全主线缺失产物，spec 2026-08-12）。
            # 计数器数 exploited、coverage/PoC 读 accepted_ids；与 evidence.md 同源
            # （build_exploit_verdicts_payload 复用 _build_exploit_validation）。
            if isinstance(agent_name, AgentName) and agent_name.value.endswith("-exploit"):
                from supernova_core.renderers import build_exploit_verdicts_payload
                from supernova_core.utils.paths import blackbox_dir

                vc = agent_name.value.removesuffix("-exploit")
                payload = build_exploit_verdicts_payload(
                    vc, collector.get_all(), deliverables, queue_root=queue_root)
                atomic_write_json(
                    blackbox_dir(deliverables) / f"{vc}_exploit_verdicts.json", payload)
```

> 若 `AgentName` 未在 executor.py 顶部 import，在分支内补 `from supernova_core.models.agents import AgentName`（executor 处理 agent_name 通常已 import，先确认）。`atomic_write_json` 已在 :192 使用、已 import。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_executor_vuln_render.py -v`
Expected: PASS（新测试 + 既有 executor 测试零回归）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/agents/executor.py packages/core/tests/test_executor_vuln_render.py
git commit -m "feat(core): exploit agent 跑完写 verdicts.json 到 deliverables/blackbox/"
```

---

### Task 3: `get_workspace_vuln_counts` 加 verdicts 支（数 exploited）

**Files:**
- Modify: `packages/core/src/supernova_core/workspace.py:120-145`（`get_workspace_vuln_counts`）
- Test: `packages/core/tests/test_workspace.py`（`TestGetWorkspaceVulnCounts` 类内加测试）

**Interfaces:**
- Consumes: Task 2 产的 `deliverables/blackbox/{vc}_exploit_verdicts.json`。
- Produces: 黑盒 scan 的 `get_workspace_vuln_counts` 返回各 class 的 exploited 数。

- [ ] **Step 1: 写失败测试**

在 `packages/core/tests/test_workspace.py` 的 `class TestGetWorkspaceVulnCounts:` 内（紧跟 `test_empty_deliverables` 之后）加：

```python
    def test_counts_exploited_from_verdicts_json(self, tmp_path):
        """黑盒 scan：verdicts.json 的 exploited verdict 计入 vuln_count（spec 2026-08-12）。
        blocked/potential 不计；accepted_ids 含 3 条但 exploited 只 2 → 计 2。"""
        from supernova_core.session import SessionManager

        repo = tmp_path / "repo"
        repo.mkdir()
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://x.com", str(repo), name="bb")
        deliverables = ws / "deliverables" / "blackbox"
        deliverables.mkdir(parents=True)
        (deliverables / "injection_exploit_verdicts.json").write_text(
            json.dumps({
                "vuln_class": "injection",
                "accepted_ids": ["INJ-1", "INJ-2", "INJ-3"],
                "verdicts": [
                    {"vulnerability_id": "INJ-1", "status": "exploited"},
                    {"vulnerability_id": "INJ-2", "status": "blocked_by_security"},
                    {"vulnerability_id": "INJ-3", "status": "exploited"},
                ],
                "rejected": []}), encoding="utf-8")
        assert get_workspace_vuln_counts(ws) == {"injection": 2}

    def test_verdicts_and_queue_do_not_collide(self, tmp_path):
        """同 class 的 queue(白盒) 与 verdicts(黑盒) 共存时累加不互吞（用 +=）。
        实际同 scan 不共存，此测锁 += 语义防未来回归。"""
        from supernova_core.session import SessionManager

        repo = tmp_path / "repo"
        repo.mkdir()
        mgr = SessionManager(tmp_path / "workspaces")
        ws = mgr.create_workspace("https://x.com", str(repo), name="mix")
        deliverables = ws / "deliverables"
        deliverables.mkdir(parents=True)
        (deliverables / "injection_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": [
                {"title": "A"}, {"title": "B"}]}), encoding="utf-8")  # 白盒 2 条
        (deliverables / "blackbox").mkdir()
        (deliverables / "blackbox" / "injection_exploit_verdicts.json").write_text(
            json.dumps({"vuln_class": "injection", "accepted_ids": ["INJ-1"],
                        "verdicts": [{"vulnerability_id": "INJ-1", "status": "exploited"}],
                        "rejected": []}), encoding="utf-8")  # 黑盒 exploited 1
        assert get_workspace_vuln_counts(ws) == {"injection": 3}  # 2 (queue) + 1 (exploited)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_workspace.py::TestGetWorkspaceVulnCounts -v`
Expected: FAIL — `test_counts_exploited_from_verdicts_json` 断 `{} == {"injection": 2}`（计数器还没扫 verdicts）。

- [ ] **Step 3: 实现 — 加 verdicts 扫描支**

在 `packages/core/src/supernova_core/workspace.py` 的 `get_workspace_vuln_counts`（120-145 行），把 docstring 补一行黑盒说明，并在现有 `for f in sorted(deliverables_dir.rglob("*_exploitation_queue.json")):` 循环**之后**、`return counts` **之前**插入：

```python
    # 黑盒：成功 exploit 数（status=exploited 的 verdict 计数，spec 2026-08-12）。
    # 与白盒 queue 不同 stem（*_exploit_verdicts.json），同 scan 不共存 → 不碰撞；用 += 保险。
    for f in sorted(deliverables_dir.rglob("*_exploit_verdicts.json")):
        if not f.is_file():
            continue
        vuln_class = f.name.replace("_exploit_verdicts.json", "")
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            verdicts = data.get("verdicts", [])
            exploited = sum(
                1 for v in verdicts
                if isinstance(v, dict) and v.get("status") == "exploited"
            )
            counts[vuln_class] = counts.get(vuln_class, 0) + exploited
        except (json.JSONDecodeError, OSError):
            pass
```

并把函数 docstring 末尾（"or the production track-scoped layout..."之后）补：
```
    黑盒 scan 数 *_exploit_verdicts.json 的 exploited verdict（成功 exploit 数）。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/core/tests/test_workspace.py::TestGetWorkspaceVulnCounts -v`
Expected: PASS（新 2 测 + 既有 2 测全绿）。

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/workspace.py packages/core/tests/test_workspace.py
git commit -m "feat(core): get_workspace_vuln_counts 数黑盒 verdicts.json 的 exploited"
```

---

### Task 4: 回归确认 — 孤儿消费者测试 + 全链测试绿

**Files:**
- 无源码改动（孤儿消费者读代码 + 测试均已就绪）。仅运行测试确认。

孤儿消费者现状（不改，仅验证 verdicts.json 被产出后它们的"假设性"测试仍绿）：
- `exploitation_checker.py:219-231`：verdicts.json 优先读 `accepted_ids`、缺失回落正则。测试 `test_exploitation_checker.py:424/446/475` 已覆盖三路径。
- `poc_generator.py:796-804` `_load_accepted_ids`：读 `accepted_ids`、缺失返回空。测试 `test_poc_generator.py:476` 已覆盖。

- [ ] **Step 1: 跑孤儿消费者测试**

Run:
```bash
cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest packages/blackbox/tests/test_exploitation_checker.py packages/core/tests/test_poc_generator.py -v
```
Expected: PASS（全绿）。这些测试手动构造 verdicts.json 验证读路径，与 verdicts.json 现在被真正产出无关，应保持绿。

- [ ] **Step 2: 跑本 plan 全部受影响测试（回归）**

Run:
```bash
cd /Users/mango/project/shannon-refactor/shannon-py && python -m pytest \
  packages/core/tests/renderers/test_render_deliverable_exploit.py \
  packages/core/tests/test_executor_vuln_render.py \
  packages/core/tests/test_workspace.py::TestGetWorkspaceVulnCounts \
  packages/blackbox/tests/test_exploitation_checker.py \
  packages/core/tests/test_poc_generator.py -v
```
Expected: PASS（全绿）。

- [ ] **Step 3: 若有失败，定位**

若有测试红：检查是否 `accepted_ids` 字段名被误改、或 verdicts.json schema 与测试构造不一致。不修改孤儿消费者源码（它们的读代码已正确）。

- [ ] **Step 4: 无源码改动则跳过 commit；否则 commit 修复**

```bash
git commit -am "test: 黑盒 verdicts 全链回归确认"
```
（仅当 Step 3 有修复时；无改动则跳过。）

---

### Task 5: rebuild worker + 真机验证

**Files:** 无（部署 + 手动验证）

> 改了 core/blackbox src，**必须 rebuild supernova-worker** 才在真机生效（memory：`blackbox-auth-validation-two-root-causes`、`web-llm-track-switch-not-wired`）。

- [ ] **Step 1: rebuild supernova-worker 镜像**

Run（按项目实际 rebuild 方式；典型 docker compose build）:
```bash
cd /Users/mango/project/shannon-refactor/shannon-py && docker compose build supernova-worker
```
Expected: 构建成功。

- [ ] **Step 2: 起服务 + 跑一个新黑盒 scan**

启动 web/worker（`docker compose up -d`），在 web UI 对一个有白盒 queue 的 target 发起黑盒扫描，等其跑完 exploit 阶段。

- [ ] **Step 3: 验证产物**

检查新黑盒 scan 的 `deliverables/blackbox/{vc}_exploit_verdicts.json` 存在 + schema 正确（含 `vuln_class`/`accepted_ids`/`verdicts`/`rejected`）。

- [ ] **Step 4: 验证工作区列表显示**

在新黑盒 scan 的工作区扫描列表，确认 `vuln_count` = 各 class exploited 数之和（非 0）。老 scan（如 NodeGoat-20260729-194022~3）按设计仍显示 0（只管新 scan）。

- [ ] **Step 5: 记录冒烟结果**

把冒烟结果（scan id、exploited 数、截图/数值）记到 commit message 或 memory。

---

## Self-Review（plan 作者自检，已完成）

**1. Spec coverage:**
- spec §4.1 写盘 → Task 1（payload 构造）+ Task 2（executor 写盘）。✓（实现调整为独立函数，已注明偏离 spec §4.1 的理由）
- spec §4.2 schema → Task 1 payload 含 `vuln_class`/`accepted_ids`/`verdicts`/`rejected`。✓
- spec §4.3 计数器 → Task 3。✓
- spec §4.4 孤儿消费者 → Task 4（读代码已就绪，回归确认）。✓
- spec §4.5 老 scan 不兼容 → Task 5 Step 4 明确老 scan 仍 0。✓
- spec §6 测试 → Task 1-4 各有 TDD 测试。✓
- spec §7 rebuild worker → Task 5。✓

**2. Placeholder scan:** 无 TBD/TODO；每步含实际测试代码 + 实现代码 + 运行命令。✓

**3. Type consistency:**
- `build_exploit_verdicts_payload` 在 Task 1 定义、Task 2 消费，签名一致 `(vc, data, deliverables_path, queue_root=None) -> dict`。✓
- `_build_exploit_validation` 返回 `(VerdictValidation, dict, dict)`，Task 1 两处消费一致。✓
- schema 字段名 `accepted_ids`/`verdicts`/`vuln_class`/`rejected` 跨 Task 1/3/4 一致，与现有孤儿消费者测试构造（`test_exploitation_checker.py:433`、`test_poc_generator.py:486`）对齐。✓
