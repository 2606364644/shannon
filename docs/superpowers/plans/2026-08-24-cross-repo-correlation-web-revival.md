# 跨仓关联扫描 web 复活 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让跨仓微服务关联扫描（Node/TS 前端仓 → Go gRPC 后端仓）在 web 可用：ScanNewPage 第三类型（表单⇄YAML 双向）→ 三段接力（子仓白盒 → 关联阶段 → 可选黑盒 gateway 验证）→ 完整专属结果视图（拓扑 + 跨服务攻击链 + 合并漏洞）。

**Architecture:** 方案 A——web 接力编排（`_correlation_orchestrator` 镜像 `_combined_orchestrator`）：现扫子仓完全复用白盒链路（每仓标准 scan 行 + `_submit_whitebox`），全部完成后提交 worker 侧新增 `CorrelationScanWorkflow`（跑 multi 包重构出的 `run_correlation_phase`），可选黑盒验证 run 复用 `_run_blackbox_phase` 仅多传 `correlated_workspace`。关联 Agent 输出扩展 `flows` 字段供攻击链卡片结构化消费。

**Tech Stack:** Python 3.12 + uv workspace、Temporal SDK（workflow/activity/dataclass input）、Pydantic v2、click；React 19 + react-router + SWR + i18next + js-yaml + vitest + testing-library。

**Spec:** `docs/superpowers/specs/2026-08-24-cross-repo-correlation-web-revival-design.md`（决策/边界以 spec 为准，执行前先读）

## Global Constraints

- **CLI 零回归**：`supernova-multi start -c multi-repo.yaml` 重构后行为不变；白盒/黑盒/组合扫描 web 路径不变（现有测试套件绿）。
- **worker 部署形态**：`packages/worker` 容器消费固定 task queue（`supernova-wb-web`/`supernova-bb-web`，`temporal_infra.py:34-35`）；新增 `supernova-corr-web` 同模式。
- **合并 queue 四字段硬约束**（spec 上游 B1）：合并 entry 保留 `title/description/severity/location`，跨服务标注只用额外字段。
- **Provider 凭据穿线**：web 提交的 LLM 调用必须带 `provider_config`（`AgentExecutor.execute(..., provider_config=...)`，executor.py:224 起签名已有）；CLI 路径 None 走 env。
- **测试独立模块**：新测试不依赖 feat/fork-py 预存挂起 suite（`test_worker_progress`/`test_cli follow`/`test_audit_injection`/integration）；只跑改动相关文件。pytest 用 `uv run pytest <file> -v`；前端 `cd packages/web/frontend && npx vitest run <file>`。
- **前端语言/主题**：所有 UI 文案走 i18n（`src/locales/en.json` + `zh.json` 同步加）；不硬编码颜色，用现有 token class（主题系统见 memory `web-theme-system-architecture`）。
- **每 task 一 commit**，消息格式对齐仓库惯例（`feat(correlation): ...` / `feat(web): ...`）。
- **关键现状锚点**（执行者先核对再动手）：`scan_manager.py:302` raise"correlation 暂未 C1 化"（本计划替换）；`run_cross_repo` = `packages/multi/src/supernova_multi/orchestrator.py:82`；`_submit_whitebox` = scan_manager.py:460；`_submit_blackbox` = scan_manager.py:765；`_combined_orchestrator` = scan_manager.py:2099；`_run_blackbox_phase` = scan_manager.py:2209；`create_blackbox_run` = scan_store.py:483；worker 注册 = `packages/worker/src/supernova_worker/runner.py:63`；前端 `ScanNewPage.tsx`（黑盒分支 L339/L284-292、correlation 死分支 L263、yaml 幽灵字段 L346）。

## File Structure

### Phase A（core/multi 重构，行为不变 + flows 扩展）

| 文件 | 职责 |
|---|---|
| `packages/core/src/supernova_core/correlation/schemas.py` | **修改**：加 `CrossServiceFlow` dataclass + to_json |
| `packages/core/src/supernova_core/correlation/report.py` | **修改**：`write_correlation_deliverables` 落盘 `cross-service-flows.json` |
| `prompts/cross-repo-correlation.txt` | **修改**：输出契约加 `flows` 字段 |
| `prompts/pipeline-testing/cross-repo-correlation.txt` | **修改**：CI 简化版同步 |
| `packages/multi/src/supernova_multi/orchestrator.py` | **重构**：拆 `run_correlation_phase`；`run_cross_repo` 调它；edge schema 加 flows；`_merge_edge_results` 透传 flows |
| `packages/core/tests/correlation/test_schemas.py` | 扩展：flow 序列化 |
| `packages/multi/tests/test_orchestrator.py` | 扩展：phase 参数化/flows 透传/CLI 回归 |

### Phase B（worker workflow）

| 文件 | 职责 |
|---|---|
| `packages/core/src/supernova_core/services/temporal_infra.py` | **修改**：加 `WEB_TASK_QUEUE_CORRELATION = "supernova-corr-web"` |
| `packages/multi/src/supernova_multi/pipeline/__init__.py` | 新建（空） |
| `packages/multi/src/supernova_multi/pipeline/shared.py` | 新建：`CorrelationPipelineInput` |
| `packages/multi/src/supernova_multi/pipeline/workflows.py` | 新建：`CorrelationScanWorkflow` + `run_correlation_activity` |
| `packages/worker/src/supernova_worker/runner.py` | **修改**：注册 corr worker |
| `packages/worker/pyproject.toml` | **修改**：依赖加 `supernova-multi`（根 `[tool.uv.sources]` 已有则不重复） |
| `packages/multi/tests/test_corr_workflow.py` | 新建：workflow 输入序列化/activity 包装（不触真 Temporal） |

### Phase C（web 后端）

| 文件 | 职责 |
|---|---|
| `packages/web/src/supernova_web/components/scan_manager.py` | **修改**：correlation 分支接通（start/orchestrator/submit/cancel/resume）+ `_run_blackbox_phase`/`_submit_blackbox` 加 `correlated_workspace` |
| `packages/web/src/supernova_web/components/scan_store.py` | **修改**：ScanSummary 加 `corr_children` |
| `packages/web/src/supernova_web/api/scans.py` | **修改**：`GET /{ws}/scans/{id}/correlation` 端点 |
| `packages/web/tests/test_scan_manager.py` | 扩展：correlation 分支（提交/复用校验/接力/取消） |
| `packages/web/tests/test_api_scans_correlation.py` | 新建：correlation 详情 API |

### Phase D（前端）

| 文件 | 职责 |
|---|---|
| `packages/web/frontend/src/lib/correlation-yaml.ts` | 新建：表单⇄YAML 双向纯函数 |
| `packages/web/frontend/src/lib/correlation-yaml.test.ts` | 新建：双向同步测试 |
| `packages/web/frontend/src/api/types.ts` | **修改**：`CorrelationDetail` 等类型 + 清理 `config_yaml` |
| `packages/web/frontend/src/api/client.ts` | **修改**：`getCorrelationDetail`/multi-configs 调用 |
| `packages/web/frontend/src/components/correlation/CorrelationFormFields.tsx` | 新建：仓库卡片 + relations + gateway/auth/host |
| `packages/web/frontend/src/components/correlation/YamlPanel.tsx` | 新建：折叠 YAML 编辑器（双向） |
| `packages/web/frontend/src/pages/ScanNewPage.tsx` | **重构**：类型切换（白盒|跨仓关联）、删黑盒分支 |
| `packages/web/frontend/src/routes/WorkspaceDetail/ScanList.tsx` | **修改**：correlation 主行嵌套子行列 + 类型过滤 |
| `packages/web/frontend/src/routes/WorkspaceDetail/CorrelationTab.tsx` | 新建：专属结果视图（拓扑/攻击链/漏洞/边界/报告） |
| `packages/web/frontend/src/components/correlation/TopologyGraph.tsx` | 新建：SVG 拓扑图 |
| `packages/web/frontend/src/components/correlation/AttackChainCard.tsx` | 新建：跨服务攻击链卡片 |
| `packages/web/frontend/src/routes/WorkspaceDetail/ScanDetail.tsx` | **修改**：correlation 主行 tab 组路由 |
| `packages/web/frontend/src/locales/en.json` + `zh.json` | **修改**：correlation 词条（中英同步） |
| 各组件 `.test.tsx` | 新建：表单/列表/Tab 测试 |

---

## Phase A：core/multi 重构

### Task A1: `CrossServiceFlow` schema + 落盘

**Files:**
- Modify: `packages/core/src/supernova_core/correlation/schemas.py`
- Modify: `packages/core/src/supernova_core/correlation/report.py`
- Test: `packages/core/tests/correlation/test_schemas.py`

**Interfaces:**
- Produces:
  - `CrossServiceFlow(edge_from: str, edge_to: str, entry: str, method: str, call_site: CallSite, vuln_refs: list[dict], confidence: str, evidence: str)`（dataclass，`to_json()` 往返；`vuln_refs` 保持 list[dict] 不强 schema——LLM 概率性输出宽松收）
  - `write_correlation_deliverables(out_deliverables, topology, boundaries, merged_queues, report_md, flows: list[CrossServiceFlow] | None = None)`（新增末位可选参数，旧调用零破坏）
  - 落盘文件名：`cross-service-flows.json`（`[{...}]` 数组）

- [ ] **Step 1: Write failing test**

`packages/core/tests/correlation/test_schemas.py` 追加：

```python
def test_flow_serialization_roundtrip():
    from supernova_core.correlation.schemas import CrossServiceFlow, CallSite
    f = CrossServiceFlow(
        edge_from="gateway", edge_to="order-svc", entry="POST /orders",
        method="order.v1.OrderService/CreateOrder",
        call_site=CallSite(file="src/grpc-client.ts", line=42, snippet="client.createOrder(req)"),
        vuln_refs=[{"service": "order-svc", "title": "SQL Injection",
                     "severity": "high", "location": "internal/dao/order.go:88"}],
        confidence="high", evidence="handler concatenates SQL from request")
    data = json.loads(f.to_json())
    assert data["edge_from"] == "gateway"
    assert data["vuln_refs"][0]["service"] == "order-svc"
    rt = CrossServiceFlow.from_json(f.to_json())
    assert rt.call_site.line == 42


def test_flows_file_written(tmp_path):
    from supernova_core.correlation.report import write_correlation_deliverables
    from supernova_core.correlation.schemas import (
        CrossServiceTopology, ServiceNode, CrossServiceFlow, CallSite)
    topo = CrossServiceTopology(services=[ServiceNode("g", "entrypoint", "/r/g")], edges=[])
    flows = [CrossServiceFlow(edge_from="g", edge_to="o", entry="POST /x", method="m",
                               call_site=CallSite("a.ts", 1, "s"), vuln_refs=[],
                               confidence="low", evidence="e")]
    write_correlation_deliverables(tmp_path, topo, [], {}, "# r", flows=flows)
    data = json.loads((tmp_path / "cross-service-flows.json").read_text(encoding="utf-8"))
    assert data[0]["method"] == "m"
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest packages/core/tests/correlation/test_schemas.py -v`
Expected: FAIL — `ImportError: CrossServiceFlow`

- [ ] **Step 3: Implement**

`schemas.py` 末尾追加（风格对齐现有 dataclass）：

```python
@dataclass
class CrossServiceFlow:
    """候选跨服务攻击链（spec 2026-08-24 §5.4）：前端仓入口 → RPC method → 后端仓漏洞。

    概率性 Agent 推断产物，供人工复核；vuln_refs 宽松 dict（title/severity/location/service）。
    """
    edge_from: str
    edge_to: str
    entry: str
    method: str
    call_site: CallSite
    vuln_refs: list[dict] = field(default_factory=list)
    confidence: str = "low"
    evidence: str = ""

    def to_json(self) -> str:
        return json.dumps(_s(self), ensure_ascii=False)

    @staticmethod
    def from_json(s: str) -> "CrossServiceFlow":
        d = json.loads(s)
        d["call_site"] = CallSite(**d["call_site"])
        return CrossServiceFlow(**d)
```

`report.py` 的 `write_correlation_deliverables` 加末位可选参数，boundaries 落盘后追加：

```python
def write_correlation_deliverables(
    out_deliverables: Path,
    topology: CrossServiceTopology,
    boundaries: list[TrustBoundary],
    merged_queues: dict[str, list[dict]],
    report_md: str,
    flows: list[CrossServiceFlow] | None = None,
) -> None:
    # ...原实现不动，末尾追加：
    if flows is not None:
        (out_deliverables / "cross-service-flows.json").write_text(
            json.dumps([json.loads(f.to_json()) for f in flows],
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
```

（`report.py` 顶部 import 加 `CrossServiceFlow`。）

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest packages/core/tests/correlation/ -v`
Expected: PASS（全部，含既有）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/correlation/{schemas,report}.py \
        packages/core/tests/correlation/test_schemas.py
git commit -m "feat(correlation): CrossServiceFlow schema + cross-service-flows.json 落盘(A1)"
```

---

### Task A2: prompt 输出契约加 flows

**Files:**
- Modify: `prompts/cross-repo-correlation.txt`
- Modify: `prompts/pipeline-testing/cross-repo-correlation.txt`

**Interfaces:**
- Produces: per-edge 输出 JSON 顶层加 `"flows":[...]`（空数组合法）；下游 edge schema/merge 在 A3 消费。

- [ ] **Step 1: 主 prompt `<output-format>` 的 JSON 示例加 flows（`"boundaries"` 行后）**

```
  "flows":[{"entry":"POST /orders",
            "method":"order.v1.OrderService/CreateOrder",
            "call_site":{"file":"src/routes/orders.ts","line":18,"snippet":"client.createOrder(req.body)"},
            "vuln_refs":[{"service":"<to>","title":"...","severity":"high|medium|low",
                          "location":"file.go:88"}],
            "confidence":"high|low","evidence":"how input reaches the sink"}]
```

`Rules:` 段加两行：

```
- flows = candidate attack chains crossing this edge: a from-repo entry (HTTP
  route/CLI/queue message) whose input reaches a `to`-repo sink via this RPC
  method. Only include flows where you located BOTH the from-side call_site
  AND a concrete to-repo sink/vulnerability (in code or in the deliverables
  queues under <deliverables-from-scans>). vuln_refs.location should point at
  the to-repo sink. No flow found → "flows":[] (valid).
```

- [ ] **Step 2: pipeline-testing 版同步加空 flows**

`prompts/pipeline-testing/cross-repo-correlation.txt` 输出 JSON 里 `"boundaries":[]` 后加 `"flows":[]`。

- [ ] **Step 3: 校验 prompt 测试不破**

Run: `uv run pytest packages/core/tests/prompts/ -k "not static_dataflow" -v`（若该目录无相关测试则跑 `uv run pytest packages/core/tests/correlation/ -v`）
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add prompts/cross-repo-correlation.txt prompts/pipeline-testing/cross-repo-correlation.txt
git commit -m "feat(correlation): 关联 Agent 输出契约加 flows 候选攻击链(A2)"
```

---

### Task A3: 拆 `run_correlation_phase`（CLI 行为不变）

**Files:**
- Modify: `packages/multi/src/supernova_multi/orchestrator.py`
- Test: `packages/multi/tests/test_orchestrator.py`

**Interfaces:**
- Consumes: A1 `CrossServiceFlow`/`write_correlation_deliverables(flows=)`；A2 edge payload `flows` 字段。
- Produces:
  - `async def run_correlation_phase(config: MultiRepoConfig, repo_workspace_paths: dict[str, Path], out_ws_dir: Path, event_file: Path, *, pipeline_testing: bool = False, provider_config: dict | None = None, write_scan_end: bool = True) -> dict`
    - 职责 = 现 `run_cross_repo` 的"收集各仓 queue → 关联 workspace → per-edge Agent → 合并落盘"段；返回 `{"edge_statuses": list[str], "deliverables_path": str}`；`write_scan_end=False` 时**不写 scan_end 事件**（web 编排收尾用）、heartbeat 照常进/出。
  - `run_cross_repo` 签名不变，内部：现扫段照旧 → 调 `run_correlation_phase(..., write_scan_end=True)`。

- [ ] **Step 1: Write failing tests（追加到 `packages/multi/tests/test_orchestrator.py`）**

```python
@pytest.mark.asyncio
async def test_run_correlation_phase_writes_flows_and_respects_paths(tmp_path, monkeypatch):
    """phase 参数化：repo_workspace_paths/out_ws_dir/event_file 全显式注入；
    write_scan_end=False 不写 scan_end；flows 落盘。Agent 以 stub 代（不打 LLM）。"""
    import json as _json
    from pathlib import Path
    from supernova_core.models.multi_repo_config import (
        MultiRepoConfig, RepoSpec, Relation, CorrelationConfig)
    from supernova_multi.orchestrator import run_correlation_phase

    # 两个子仓 workspace 目录：造 deliverables + 一个 queue
    gw_ws, be_ws = tmp_path / "gw-scan", tmp_path / "be-scan"
    for w in (gw_ws, be_ws):
        (w / "deliverables").mkdir(parents=True)
    (be_ws / "deliverables" / "injection_exploitation_queue.json").write_text(
        _json.dumps({"vulnerabilities": [
            {"title": "SQLi", "description": "d", "severity": "high",
             "location": "dao.go:8"}]}), encoding="utf-8")
    out_ws = tmp_path / "corr-scan"
    out_ws.mkdir()
    event_file = tmp_path / "corr-scan" / "events.ndjson"

    cfg = MultiRepoConfig(
        repos={"gateway": RepoSpec(path="/r/gw", role="entrypoint"),
               "order-svc": RepoSpec(path="/r/be", role="backend")},
        relations=[Relation(**{"from": "gateway", "to": "order-svc"})],
        correlation=CorrelationConfig(out_workspace="corr-scan"))

    async def fake_execute(self, **kw):
        from supernova_core.models.agents import AgentMetrics  # 若无该构造则用 SimpleNamespace
        class _M:  # 最小 metrics stub
            structured_output = {
                "from": "gateway", "to": "order-svc", "protocol": "grpc",
                "calls": [], "status": "ok", "boundaries": [],
                "flows": [{"entry": "POST /orders",
                           "method": "order.v1.OrderService/CreateOrder",
                           "call_site": {"file": "c.ts", "line": 1, "snippet": "x"},
                           "vuln_refs": [{"service": "order-svc", "title": "SQLi",
                                           "severity": "high", "location": "dao.go:8"}],
                           "confidence": "high", "evidence": "e"}]}
        return _M()

    from supernova_multi import orchestrator as orch  # noqa: F401 —— patch 源头类（orchestrator 函数内 import，模块级无该属性）
    import supernova_core.agents.executor as executor_mod
    monkeypatch.setattr(executor_mod.AgentExecutor, "execute", fake_execute)

    result = await run_correlation_phase(
        cfg, {"gateway": gw_ws, "order-svc": be_ws}, out_ws, event_file,
        write_scan_end=False)

    dlv = out_ws / "deliverables"
    flows = _json.loads((dlv / "cross-service-flows.json").read_text(encoding="utf-8"))
    assert flows[0]["method"] == "order.v1.OrderService/CreateOrder"
    merged = _json.loads((dlv / "injection_exploitation_queue.json").read_text(encoding="utf-8"))
    assert merged["vulnerabilities"][0]["service"] == "order-svc"
    events = [ _json.loads(l) for l in event_file.read_text(encoding="utf-8").splitlines() if l ]
    assert all(e["type"] != "scan_end" for e in events)   # write_scan_end=False
    assert result["edge_statuses"] == ["ok"]


@pytest.mark.asyncio
async def test_run_correlation_phase_write_scan_end_true(tmp_path, monkeypatch):
    """write_scan_end=True（CLI 默认）→ scan_end 事件落 ndjson。"""
    from supernova_multi.orchestrator import run_correlation_phase
    from supernova_core.models.multi_repo_config import (
        MultiRepoConfig, RepoSpec, Relation, CorrelationConfig)
    import json as _json
    cfg = MultiRepoConfig(
        repos={"gateway": RepoSpec(path="/r/gw", role="entrypoint"),
               "order-svc": RepoSpec(path="/r/be", role="backend")},
        relations=[],  # 无边：不调 Agent，纯事件路径
        correlation=CorrelationConfig(out_workspace="corr-scan"))
    out_ws = tmp_path / "corr-scan"; out_ws.mkdir()
    event_file = out_ws / "events.ndjson"
    await run_correlation_phase(cfg, {"gateway": out_ws}, out_ws, event_file,
                                write_scan_end=True)
    events = [_json.loads(l) for l in event_file.read_text(encoding="utf-8").splitlines() if l]
    assert any(e["type"] == "scan_end" and e["status"] == "completed" for e in events)
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest packages/multi/tests/test_orchestrator.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_correlation_phase'`

- [ ] **Step 3: Refactor orchestrator**

`orchestrator.py` 改造（保持公共符号与 CLI 行为不变）：

1. `run_cross_repo` 的现扫/复用段（L107-162）不动；把第 2 步起（关联 workspace 建立、heartbeat、per-edge、merge、落盘、scan_end）整体搬入新函数 `run_correlation_phase`，差异点：
   - 子仓 queue 收集改为参数 `repo_workspace_paths: dict[str, Path]` 驱动（`dlv = deliverables_dir_for_workspace(repo_workspace_paths[service])`，三处 glob 逻辑照搬）；漂移检测保留（`config.repos[service].path` 有值且存在时）。
   - `out_ws_dir` 直接用参数（`SessionManager(out_ws_dir.parent).create_workspace(...)` 幂等——web 已建主行不覆盖；CLI 传入 `resolve_workspaces_dir() / config.correlation.out_workspace` 等价原行为）。
   - `CorrelationEventWriter(event_file)`（不再自拼路径）。
   - `executor = AgentExecutor(PromptManager(_prompts_dir()))` 不变；`edge_runner` 里 `executor.execute(..., provider_config=provider_config)` 透传。
   - `edge_output_schema` 的 `properties` 加 `"flows": {"type": "array"}`（required 不加——旧 prompt 无 flows 也合法）。
   - `_merge_edge_results` 透传：返回 dict 加 `"flows": r.get("flows", [])`；组装处 `flows = [CrossServiceFlow(edge_from=e["from"], edge_to=e["to"], entry=f["entry"], method=f["method"], call_site=CallSite(**f["call_site"]), vuln_refs=f.get("vuln_refs", []), confidence=f.get("confidence", "low"), evidence=f.get("evidence", "")) for e in merged["edges"] for f in e.get("flows", [])]`，`write_correlation_deliverables(..., flows=flows)`。
   - 结尾：`if write_scan_end: await corr_writer.scan_end(...)`；heartbeat `__aexit__` 照旧（两分支都执行）。
   - 返回 `{"edge_statuses": [...], "deliverables_path": str(out_dlv)}`。
2. `run_cross_repo` 尾段改为：

```python
    return await run_correlation_phase(
        config,
        repo_workspace_paths={p.service: ws_path_for(p) for p in plans},  # 现扫/复用收集到的各仓目录
        out_ws_dir=resolve_workspaces_dir() / config.correlation.out_workspace,
        event_file=resolve_workspaces_dir() / config.correlation.out_workspace / "events.ndjson",
        pipeline_testing=pipeline_testing,
        write_scan_end=True,
    )
```

（`ws_path_for` = 现扫/复用分支已算出的 `ws_path`，搬到 dict 收集；`run_cross_repo` 返回值保持原三键——phase 返回多退少补：`{**phase_result, "out_workspace": config.correlation.out_workspace}`。）

- [ ] **Step 4: Run multi + correlation suites**

Run: `uv run pytest packages/multi/tests/ packages/core/tests/correlation/ -v`
Expected: PASS（含既有 CLI 回归测试全部绿）

- [ ] **Step 5: Commit**

```bash
git add packages/multi/src/supernova_multi/orchestrator.py packages/multi/tests/test_orchestrator.py
git commit -m "refactor(multi): 拆 run_correlation_phase(参数化 paths/event_file/provider/scan_end) + flows 透传,CLI 行为不变(A3)"
```

---

## Phase B：worker CorrelationScanWorkflow

### Task B1: pipeline 包 + workflow/activity

**Files:**
- Create: `packages/multi/src/supernova_multi/pipeline/__init__.py`（空）
- Create: `packages/multi/src/supernova_multi/pipeline/shared.py`
- Create: `packages/multi/src/supernova_multi/pipeline/workflows.py`
- Test: `packages/multi/tests/test_corr_workflow.py`

**Interfaces:**
- Consumes: A3 `run_correlation_phase`。
- Produces:
  - `CorrelationPipelineInput`（dataclass，可 JSON 序列化——Temporal 参数）：`config_path: str`、`repo_workspace_paths: dict[str, str]`、`out_ws_dir: str`、`event_file: str`、`provider_config: dict | None = None`、`env_overrides: dict[str, str] = field(default_factory=dict)`、`pipeline_testing: bool = False`、`write_scan_end: bool = False`
  - `CorrelationScanWorkflow`（`@workflow.defn`，`run(inp) -> dict`：单 `execute_activity(run_correlation_activity, inp, start_to_close_timeout=timedelta(hours=4))` 直通）
  - `@activity.defn async def run_correlation_activity(inp: CorrelationPipelineInput) -> dict`：`parse_multi_repo_config(inp.config_path)` → env_overrides 应用（对齐 whitebox activity 的 scan_env 覆盖模式：`os.environ.update(inp.env_overrides)` 若既有 helper 则复用）→ `run_correlation_phase(config, {k: Path(v) for v}, Path(inp.out_ws_dir), Path(inp.event_file), pipeline_testing=inp.pipeline_testing, provider_config=inp.provider_config, write_scan_end=inp.write_scan_end)`

- [ ] **Step 1: Write failing test**

`packages/multi/tests/test_corr_workflow.py`：

```python
"""CorrelationScanWorkflow 输入序列化 + activity 包装（不触真 Temporal server）。

workflow 逻辑仅一层 activity 直通——用 temporalio 的 Replayer/直接单测 activity 函数。
"""
import dataclasses
import json
from pathlib import Path


def test_pipeline_input_serializable():
    from supernova_multi.pipeline.shared import CorrelationPipelineInput
    inp = CorrelationPipelineInput(
        config_path="/tmp/multi-repo.yaml",
        repo_workspace_paths={"gateway": "/w/gw-scan"},
        out_ws_dir="/w/corr-1", event_file="/w/corr-1/events.ndjson",
        provider_config={"base_url": "x", "api_key": "k"},
        env_overrides={"FOO": "1"})
    # dataclass → dict → json 往返（Temporal 序列化等价）
    d = json.loads(json.dumps(dataclasses.asdict(inp)))
    assert d["repo_workspace_paths"]["gateway"] == "/w/gw-scan"
    assert d["write_scan_end"] is False


def test_activity_invokes_phase(tmp_path, monkeypatch):
    from supernova_multi.pipeline import workflows as wf
    from supernova_multi.pipeline.shared import CorrelationPipelineInput
    calls = {}

    async def fake_phase(config, repo_workspace_paths, out_ws_dir, event_file, **kw):
        calls["repo_paths"] = repo_workspace_paths
        calls["out_ws_dir"] = out_ws_dir
        calls["provider"] = kw.get("provider_config")
        calls["write_scan_end"] = kw.get("write_scan_end")
        return {"edge_statuses": [], "deliverables_path": str(out_ws_dir)}

    import asyncio
    monkeypatch.setattr(wf, "run_correlation_phase", fake_phase)
    cfg_file = tmp_path / "multi-repo.yaml"
    cfg_file.write_text(
        "repos:\n  gateway: {path: /r/gw, role: entrypoint}\n"
        "  b: {path: /r/b}\nrelations: []\n"
        "correlation: {out_workspace: corr-1}\n", encoding="utf-8")
    inp = CorrelationPipelineInput(
        config_path=str(cfg_file),
        repo_workspace_paths={"gateway": "/w/gw"},
        out_ws_dir="/w/corr-1", event_file="/w/corr-1/events.ndjson",
        provider_config={"api_key": "k"})
    result = asyncio.run(wf.run_correlation_activity(inp))
    assert calls["out_ws_dir"] == Path("/w/corr-1")
    assert calls["provider"] == {"api_key": "k"}
    assert calls["write_scan_end"] is False
    assert result["edge_statuses"] == []
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest packages/multi/tests/test_corr_workflow.py -v`
Expected: FAIL — `ModuleNotFoundError: supernova_multi.pipeline`

- [ ] **Step 3: Implement**

`pipeline/shared.py`：

```python
from dataclasses import dataclass, field


@dataclass
class CorrelationPipelineInput:
    """web → worker 的关联阶段入参（Temporal 参数，全字段可序列化）。"""
    config_path: str
    repo_workspace_paths: dict[str, str] = field(default_factory=dict)
    out_ws_dir: str = ""
    event_file: str = ""
    provider_config: dict | None = None
    env_overrides: dict[str, str] = field(default_factory=dict)
    pipeline_testing: bool = False
    # web 编排收尾（_ensure_scan_end 写终态）；CLI 直跑 True。默认 False（worker 由 web 提交）。
    write_scan_end: bool = False
```

`pipeline/workflows.py`：

```python
"""CorrelationScanWorkflow：web 提交的关联阶段（spec 2026-08-24 §5.2）。

形态对齐 whitebox/blackbox 的 pipeline 包；本 workflow 是单 activity 直通
（编排逻辑在 run_correlation_phase，无中间状态机）。
"""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    from supernova_core.config.parser import parse_multi_repo_config
    from supernova_multi.orchestrator import run_correlation_phase
    from supernova_multi.pipeline.shared import CorrelationPipelineInput


@activity.defn
async def run_correlation_activity(inp: CorrelationPipelineInput) -> dict:
    if inp.env_overrides:
        os.environ.update(inp.env_overrides)
    config = parse_multi_repo_config(Path(inp.config_path))
    return await run_correlation_phase(
        config,
        {svc: Path(p) for svc, p in inp.repo_workspace_paths.items()},
        Path(inp.out_ws_dir), Path(inp.event_file),
        pipeline_testing=inp.pipeline_testing,
        provider_config=inp.provider_config,
        write_scan_end=inp.write_scan_end,
    )


@workflow.defn
class CorrelationScanWorkflow:
    @workflow.run
    async def run(self, inp: CorrelationPipelineInput) -> dict:
        return await workflow.execute_activity(
            run_correlation_activity, inp,
            start_to_close_timeout=timedelta(hours=4),
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest packages/multi/tests/test_corr_workflow.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add packages/multi/src/supernova_multi/pipeline/ packages/multi/tests/test_corr_workflow.py
git commit -m "feat(multi): CorrelationScanWorkflow + run_correlation_activity(单 activity 直通,B1)"
```

---

### Task B2: task queue 常量 + worker 注册 + 依赖

**Files:**
- Modify: `packages/core/src/supernova_core/services/temporal_infra.py:34`（常量区）
- Modify: `packages/worker/src/supernova_worker/runner.py`
- Modify: `packages/worker/pyproject.toml`（dependencies 加 `"supernova-multi"`；根 `pyproject.toml` 的 `[tool.uv.sources]` 已含 `supernova-multi = { workspace = true }` 则不动，缺则补）
- Test: `packages/multi/tests/test_corr_workflow.py`（追加注册断言）

**Interfaces:**
- Produces: `WEB_TASK_QUEUE_CORRELATION = "supernova-corr-web"`（temporal_infra）；worker 进程常驻三个 Worker（wb/bb/corr）。

- [ ] **Step 1: Write failing test（追加）**

```python
def test_corr_task_queue_constant():
    from supernova_core.services.temporal_infra import WEB_TASK_QUEUE_CORRELATION
    assert WEB_TASK_QUEUE_CORRELATION == "supernova-corr-web"


def test_runner_registers_corr_worker():
    """runner.run_worker 构造三个 Worker：corr 含 CorrelationScanWorkflow + activity。"""
    import inspect
    from supernova_worker import runner
    src = inspect.getsource(runner.run_worker)
    assert "WEB_TASK_QUEUE_CORRELATION" in src
    assert "CorrelationScanWorkflow" in src
    assert "run_correlation_activity" in src
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest packages/multi/tests/test_corr_workflow.py -v`
Expected: FAIL — `ImportError: WEB_TASK_QUEUE_CORRELATION`

- [ ] **Step 3: Implement**

`temporal_infra.py` 常量区（L34-35 后）加：

```python
WEB_TASK_QUEUE_CORRELATION = "supernova-corr-web"
```

`runner.py`：
- import 区加：`from supernova_multi.pipeline.workflows import CorrelationScanWorkflow` 与 `from supernova_multi.pipeline.workflows import run_correlation_activity`；
- `bb_worker = Worker(...)` 块后加（对齐两 worker 同款参数）：

```python
    corr_worker = Worker(
        client=client,
        task_queue=WEB_TASK_QUEUE_CORRELATION,
        workflows=[CorrelationScanWorkflow],
        activities=[run_correlation_activity],
        max_concurrent_workflow_tasks=int(
            os.environ.get("SUPERNOVA_WORKER_MAX_CONCURRENT_WF", "4")
        ),
        graceful_shutdown_timeout=_GRACEFUL_SHUTDOWN,
    )
```

- 末行 gather 改：`await asyncio.gather(wb_worker.run(), bb_worker.run(), corr_worker.run())`；
- import 的 `WEB_TASK_QUEUE_*` 列表加 `WEB_TASK_QUEUE_CORRELATION`。

`packages/worker/pyproject.toml` dependencies 加 `"supernova-multi"`；随后 `uv sync`（workspace 解析）。

- [ ] **Step 4: Run + import 冒烟**

Run: `uv run pytest packages/multi/tests/test_corr_workflow.py -v && uv run python -c "from supernova_worker.runner import run_worker; print('ok')"`
Expected: PASS + `ok`（worker 包能 import multi pipeline，无循环依赖）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/supernova_core/services/temporal_infra.py \
        packages/worker/src/supernova_worker/runner.py packages/worker/pyproject.toml \
        packages/multi/tests/test_corr_workflow.py
git commit -m "feat(worker): supernova-corr-web queue + corr worker 注册(依赖 supernova-multi,B2)"
```

---

## Phase C：web 后端

### Task C1: `_run_blackbox_phase`/`_submit_blackbox` 加 `correlated_workspace` 穿透

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py:765`（`_submit_blackbox`）、`:2209`（`_run_blackbox_phase`）
- Test: `packages/web/tests/test_scan_manager.py`（追加）

**Interfaces:**
- Produces:
  - `_submit_blackbox(..., correlated_workspace: str | None = None)`：构造 `BlackboxPipelineInput(..., correlated_workspace=correlated_workspace)`（该字段已存在，blackbox shared.py；不传时 None，零回归）
  - `_run_blackbox_phase(..., correlated_workspace: str | None = None)`：透传给 `_submit_blackbox`

- [ ] **Step 1: Write failing test（追加到 test_scan_manager.py，风格对齐文件内既有 async 测试）**

```python
async def test_submit_blackbox_passes_correlated_workspace():
    """correlated_workspace 穿透到 BlackboxPipelineInput（None 默认零回归 + 显式传值）。"""
    sm = _make_manager()  # 复用文件内既有 fixture/helper；无则按现有测试构造 ScanManager
    captured = {}

    class _FakeWF:
        @staticmethod
        async def run(inp): return None

    class _FakeHandle:
        pass

    import inspect
    from supernova_web.components import scan_manager as m
    sig = inspect.signature(m.ScanManager._submit_blackbox)
    assert "correlated_workspace" in sig.parameters

    async def fake_start(self, wf, inp, **kw):
        captured["inp"] = inp
        return _FakeHandle()

    async def fake_client_connect(addr=None):
        return None

    monkeypatch = _pytest_monkeypatch()  # 见下：直接用 pytest fixture 传入更简
```

（实现时简化：本文件既有测试都用 `monkeypatch` fixture + 直接调私有方法。落地写法：）

```python
async def test_run_blackbox_phase_forwards_correlated_workspace(monkeypatch):
    from supernova_web.components import scan_manager as m
    sig = inspect.signature(m.ScanManager._run_blackbox_phase)
    assert "correlated_workspace" in sig.parameters
    captured = {}

    async def fake_submit(self, repo_path, ws, scan_id, scan_dir, event_file,
                          web_url, config_path, host_mappings=None,
                          workflow_id_suffix="", correlated_workspace=None):
        captured["correlated_workspace"] = correlated_workspace
        captured["suffix"] = workflow_id_suffix
        return object()

    async def fake_await(self, handle):
        return {"status": "completed"}

    monkeypatch.setattr(m.ScanManager, "_submit_blackbox", fake_submit)
    monkeypatch.setattr(m.ScanManager, "_await_workflow_result", fake_await)
    # _generate_combined_report / 预检等依赖按文件内既有组合测试的 mock 方式处理
    ...
    await m.ScanManager._run_blackbox_phase(
        sm, tmp_path, "ws", "scan-1", {}, "run-1",
        workflow_id_suffix="-bb-1", correlated_workspace="scan-1")
    assert captured["correlated_workspace"] == "scan-1"
    assert captured["suffix"] == "-bb-1"
```

（`sm`/预检 mock 细节对齐文件内现有 `_combined_orchestrator` 测试的构造方式——执行者先读文件内既有黑盒 phase 测试再落笔，保持同构。）

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest packages/web/tests/test_scan_manager.py -k blackbox_phase -v`
Expected: FAIL — `KeyError: 'correlated_workspace'`（签名无该参数）

- [ ] **Step 3: Implement**

- `_submit_blackbox` 签名加末位 `correlated_workspace: str | None = None`；`BlackboxPipelineInput(...)` 构造加 `correlated_workspace=correlated_workspace`（其余不动）。
- `_run_blackbox_phase` 签名加末位同名参数；内部 `self._submit_blackbox(...)` 调用点透传。

- [ ] **Step 4: Run**

Run: `uv run pytest packages/web/tests/test_scan_manager.py -v`
Expected: PASS（全部，含既有组合扫描测试零回归）

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_scan_manager.py
git commit -m "feat(web): 黑盒 phase/submit 穿透 correlated_workspace(默认 None 零回归,C1)"
```

---

### Task C2: `_submit_correlation` + 复用校验 + `corr_children`

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py`
- Modify: `packages/web/src/supernova_web/components/scan_store.py`（ScanSummary `corr_children`）
- Test: `packages/web/tests/test_scan_manager.py`（追加）

**Interfaces:**
- Consumes: B1 `CorrelationPipelineInput`/`CorrelationScanWorkflow`、B2 `WEB_TASK_QUEUE_CORRELATION`、core `parse_multi_repo_config`。
- Produces:
  - `def _validate_reused_children(self, ws: str, config: MultiRepoConfig) -> dict[str, Path]`：校验每个 `spec.workspace` 复用子仓——`get_scan_dir(ws, spec.workspace)` 存在、session `scan_type == "whitebox"`、`deliverables/` 下至少一个 `*_exploitation_queue.json`（三处 glob 同 orchestrator：`whitebox/intermediate/` → `whitebox/` → 根）；返回 `{service: scan_dir}`；任一不合法 `raise ValueError(f"复用扫描不可用: {service}: <原因>")`（API 层转 422，前端提示改重新扫）
  - `async def _submit_correlation(self, config_path: Path, repo_workspace_paths: dict[str, Path], out_ws_dir: Path, event_file: Path, ws: str) -> Any`：镜像 `_submit_whitebox`（provider_config/env_overrides 解析 + `start_workflow(CorrelationScanWorkflow.run, CorrelationPipelineInput(...), task_queue=WEB_TASK_QUEUE_CORRELATION, run_timeout=workflow_run_timeout())`）
  - session 字段 `corr_children: [{service, scan_id, reused, status?}]`（`SessionManager.update_session` 写）；`ScanSummary.corr_children: list[dict] | None = None`（`_summarize` 透传）

- [ ] **Step 1: Write failing tests**

```python
async def test_validate_reused_children_ok(tmp_path):
    sm, store = _make_manager_with_store(tmp_path)  # 对齐文件内既有构造 helper
    scan_id, scan_dir = store.create_scan("ws", "", "repo-a", "whitebox")
    (scan_dir / "deliverables").mkdir(parents=True, exist_ok=True)
    (scan_dir / "deliverables" / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities": [{"title": "t", "description": "d", "severity": "high", "location": "f:1"}]}',
        encoding="utf-8")
    from supernova_core.models.multi_repo_config import MultiRepoConfig, RepoSpec, CorrelationConfig
    cfg = MultiRepoConfig(
        repos={"a": RepoSpec(workspace=scan_id, role="backend")},
        relations=[],
        correlation=CorrelationConfig(out_workspace="corr-1"))
    paths = sm._validate_reused_children("ws", cfg)
    assert paths["a"] == scan_dir


async def test_validate_reused_children_missing_queue(tmp_path):
    sm, store = _make_manager_with_store(tmp_path)
    scan_id, scan_dir = store.create_scan("ws", "", "repo-a", "whitebox")
    # 无 queue 文件 → 拒
    from supernova_core.models.multi_repo_config import MultiRepoConfig, RepoSpec, CorrelationConfig
    cfg = MultiRepoConfig(
        repos={"a": RepoSpec(workspace=scan_id, role="backend")},
        relations=[], correlation=CorrelationConfig(out_workspace="corr-1"))
    with pytest.raises(ValueError, match="复用扫描不可用"):
        sm._validate_reused_children("ws", cfg)


def test_scan_summary_carr_children(tmp_path):
    """summary 透传 corr_children。"""
    ...  # 对齐文件内 ScanSummary 既有测试构造：session 写 corr_children 后 list_scans 断言
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest packages/web/tests/test_scan_manager.py -k reused -v`
Expected: FAIL — `AttributeError: _validate_reused_children`

- [ ] **Step 3: Implement**

`scan_manager.py`：
- `_validate_reused_children`（同步方法，放 `_resolve_correlation_yaml` 附近）：逐 service 校验（如 Interfaces 述）；glob 帮助函数 `_find_queue_files(dlv: Path) -> list[Path]`（三处 glob，与 orchestrator.py:147-152 相同顺序），返回非空即通过。
- `_submit_correlation`（放 `_submit_blackbox` 后）：

```python
    async def _submit_correlation(self, config_path: Path,
                                  repo_workspace_paths: dict[str, Path],
                                  out_ws_dir: Path, event_file: Path,
                                  ws: str) -> Any:
        """提交关联阶段 workflow 到 supernova-corr-web queue（对齐 _submit_whitebox 模式）。"""
        from supernova_multi.pipeline.workflows import CorrelationScanWorkflow
        from supernova_multi.pipeline.shared import CorrelationPipelineInput
        from supernova_core.services.temporal_infra import WEB_TASK_QUEUE_CORRELATION

        provider_config = self._resolve_provider_config(ws)
        client = await Client.connect(self._temporal_address())
        workflow_id = self._resolve_workflow_id(ws, out_ws_dir.name) + "-corr"
        inp = CorrelationPipelineInput(
            config_path=str(config_path),
            repo_workspace_paths={k: str(v) for k, v in repo_workspace_paths.items()},
            out_ws_dir=str(out_ws_dir), event_file=str(event_file),
            provider_config=provider_config,
            env_overrides=self._resolve_env_overrides(ws),
        )
        handle = await client.start_workflow(
            CorrelationScanWorkflow.run, inp, id=workflow_id,
            task_queue=WEB_TASK_QUEUE_CORRELATION,
            run_timeout=workflow_run_timeout(),
        )
        self._mark_submitted_at(out_ws_dir)
        return handle
```

`scan_store.py`：`ScanSummary` 加 `corr_children: list[dict] | None = None`；`_summarize` 从 session 读 `corr_children` 填充（对齐 `bb_runs` 的读法）。

- [ ] **Step 4: Run**

Run: `uv run pytest packages/web/tests/test_scan_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py \
        packages/web/src/supernova_web/components/scan_store.py \
        packages/web/tests/test_scan_manager.py
git commit -m "feat(web): 复用子仓校验 + _submit_correlation + corr_children 血缘(C2)"
```

---

### Task C3: `start()` correlation 分支接通 + `_correlation_orchestrator`

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py:189-310`（start 分支）、`:302`（raise 替换）
- Test: `packages/web/tests/test_scan_manager.py`（追加）

**Interfaces:**
- Consumes: C1 `correlated_workspace`、C2 `_validate_reused_children`/`_submit_correlation`、既有 `_submit_whitebox`/`_run_blackbox_phase`/`_ensure_scan_end`/`_await_workflow_result`/`CorrelationEventWriter`。
- Produces:
  - `start()` correlation 分支：①解析 yaml（`_resolve_correlation_yaml`，已有）→ ②`_validate_reused_children` → ③建主行 + session `corr_children` → ④覆写 `out_workspace` 落盘 yaml → ⑤现扫子仓逐仓 `create_scan`+`_submit_whitebox`（子仓 repo 路径经既有 `_resolve_repo_path` 语义解析——`source.kind=="repo"` 时按仓库名）→ ⑥`create_task(_correlation_orchestrator(...))`
  - `async def _correlation_orchestrator(self, scan_key, child_handles: dict[str, tuple[str, Any]], scan_dir, req, config_path, repo_workspace_paths) -> None`（`child_handles` 值 = (scan_id, handle)；镜像 `_combined_orchestrator` 的 try/except/finally + `_ensure_scan_end`）

- [ ] **Step 1: Write failing tests（行为级，mock workflow 提交）**

```python
async def test_start_correlation_creates_main_and_children(tmp_path, monkeypatch):
    """提交 correlation：主行 + 2 现扫子仓行 + corr_children 血缘 + 接力任务登记。"""
    sm, store = _make_manager_with_store(tmp_path)
    _seed_repo(tmp_path, "ws", "frontend")   # helper：ws 下造 repos/frontend 目录（对齐既有 repo 测试）
    _seed_repo(tmp_path, "ws", "order-svc")
    yaml_text = (
        "repos:\n"
        "  frontend: {path: PLACEHOLDER_FRONTEND, role: entrypoint}\n"
        "  order-svc: {path: PLACEHOLDER_BACKEND}\n"
        "relations:\n  - {from: frontend, to: order-svc, protocol: grpc}\n")
    req = ScanRequest(type="correlation", workspace="ws",
                      config_content=yaml_text.replace("PLACEHOLDER_FRONTEND", str(tmp_path / "ws/repos/frontend"))
                                             .replace("PLACEHOLDER_BACKEND", str(tmp_path / "ws/repos/order-svc")))
    submitted = {"wb": [], "corr": 0}

    async def fake_submit_whitebox(self, target, ws, scan_id, scan_dir, event_file,
                                   web_url, combined=False):
        submitted["wb"].append((ws, scan_id, target))
        return object()

    async def fake_submit_correlation(self, config_path, repo_paths, out_ws, event_file, ws):
        submitted["corr"] += 1
        submitted["corr_paths"] = repo_paths
        return object()

    async def fake_await(self, handle):
        return {"status": "completed"}

    async def fake_bb_phase(self, scan_dir, ws, scan_id, auth_ref, run_id,
                            workflow_id_suffix="-bb-1", correlated_workspace=None):
        submitted["bb"] = (run_id, correlated_workspace)

    monkeypatch.setattr(type(sm), "_submit_whitebox", fake_submit_whitebox)
    monkeypatch.setattr(type(sm), "_submit_correlation", fake_submit_correlation)
    monkeypatch.setattr(type(sm), "_await_workflow_result", fake_await)
    monkeypatch.setattr(type(sm), "_run_blackbox_phase", fake_bb_phase)

    ws_name, scan_id = await sm.start(req)
    # 主行 + 2 子仓行（同 ws scans 下共 3 目录）
    scans = store.list_scans("ws")
    assert len(scans) == 3
    main = next(s for s in scans if s.scan_id == scan_id)
    assert main.is_correlation
    assert len(main.corr_children) == 2
    # 接力同步段已跑（fake await 即时完成）：corr 提交 1 次、paths 覆盖两子仓
    assert submitted["corr"] == 1
    assert set(submitted["corr_paths"]) == {"frontend", "order-svc"}


async def test_start_correlation_reuse_no_child_rows(tmp_path, monkeypatch):
    """复用子仓不建行：主行 only + corr_children reused=True。"""
    ...  # 同上构造，repos 里 order-svc 给 workspace=<已有白盒 scan_id>（造好 queue 文件）
    # 断言 list_scans("ws") 长度 == 1（仅主行）且 corr_children[0]["reused"] is True


async def test_start_correlation_failed_child_short_circuits(tmp_path, monkeypatch):
    """现扫子仓失败 → 不提交 correlation、主行 failed、scan_end 落盘。"""
    ...  # fake_await 对某子仓返回 {"status": "failed"}
    # 断言 submitted["corr"] == 0 且主行 status == "failed"


async def test_start_correlation_gateway_url_runs_blackbox(tmp_path, monkeypatch):
    """req.url 提供 → 关联后建 run-1 且 correlated_workspace=主行 scan_id。"""
    ...  # req 带 url="http://gw" + host 配置 mock；断言 submitted["bb"][1] == scan_id
```

（4 个用例的公共构造抽文件内 helper `_start_corr_env(tmp_path, monkeypatch, ...)`，避免复制。）

- [ ] **Step 2: Run to verify fail**

Run: `uv run pytest packages/web/tests/test_scan_manager.py -k correlation -v`
Expected: FAIL — `ValueError: correlation 暂未 C1 化`（现分支 raise）

- [ ] **Step 3: Implement**

`start()` 的 `req.type == "correlation"` 分支（替换 L302 raise；位置在 whitebox 分支后）：

```python
            elif req.type == "correlation":
                config = parse_multi_repo_config(yaml_path)
                repo_paths = self._validate_reused_children(ws, config)
                # 主行（scan_id 由 store 生成；repo 字段填 entrypoint 服务名做展示锚）
                entry_svc = next((s for s, r in config.repos.items()
                                  if r.role == "entrypoint"), "")
                scan_id, scan_dir = self._store.create_scan(
                    ws, req.url or "", entry_svc, "correlation")
                self._mark_owner(scan_dir, "web")
                # 覆写 out_workspace=主行 scan_id 落盘 yaml（phase/worker 读此文件）
                config.correlation.out_workspace = scan_id
                dumped = yaml_path.parent / f"{scan_id}-multi-repo.yaml"
                _dump_multi_repo_yaml(config, dumped)   # 本文件新增 helper：yaml.safe_dump(model_dump(by_alias=True))
                event_file = scan_dir / "events.ndjson"
                corr_writer = CorrelationEventWriter(event_file)

                children: list[dict] = []
                child_handles: dict[str, tuple[str, Any]] = {}
                plans = plan_repo_scans(config)
                for p in plans:
                    if p.reuse:
                        repo_paths[p.service] = self._store.get_scan_dir(ws, p.workspace)
                        children.append({"service": p.service, "scan_id": p.workspace,
                                         "reused": True})
                        await corr_writer.repo(p.service, "completed", detail="reused")
                        continue
                    repo_dir = self._resolve_repo_dir(ws, p.service)  # 仓库名→repos_dir 路径（既有 resolve 逻辑抽出）
                    c_scan_id, c_dir = self._store.create_scan(
                        ws, "", p.service, "whitebox")
                    self._mark_owner(c_dir, "web")
                    await corr_writer.repo(p.service, "started")
                    h = await self._submit_whitebox(
                        str(repo_dir), ws, c_scan_id, c_dir,
                        c_dir / "events.ndjson", "")
                    child_handles[p.service] = (c_scan_id, h)
                    repo_paths[p.service] = c_dir
                    children.append({"service": p.service, "scan_id": c_scan_id,
                                     "reused": False})
                SessionManager(scan_dir.parent).update_session(scan_dir, {
                    "corr_children": children,
                    "config_path": str(dumped),
                })
                orch = asyncio.create_task(self._correlation_orchestrator(
                    (ws, scan_id), child_handles, scan_dir, req, dumped, repo_paths))
                self._orchestrator_tasks[(ws, scan_id)] = orch
```

`_correlation_orchestrator`（放 `_combined_orchestrator` 后，同构收尾）：

```python
    async def _correlation_orchestrator(self, scan_key, child_handles, scan_dir,
                                        req, config_path, repo_workspace_paths) -> None:
        """跨仓三段接力（spec 2026-08-24 §5.3）：子仓白盒 → 关联 →（可选）黑盒验证。

        scan_end 不变量同 _combined_orchestrator：成功路径段③黑盒后由 _ensure_scan_end
        幂等收尾；关联阶段 write_scan_end=False（phase 不写终态）。
        """
        ws, scan_id = scan_key
        final_status = "completed"
        run_id: str | None = None
        event_file = scan_dir / "events.ndjson"
        corr_writer = CorrelationEventWriter(event_file)
        try:
            for svc, (c_scan_id, h) in child_handles.items():
                r = await self._await_workflow_result(h)
                status = (r.get("status") if isinstance(r, dict)
                          else getattr(r, "status", None))
                if status == "failed":
                    await corr_writer.repo(svc, "failed", detail="scan failed")
                    final_status = "failed"
                    return
                await corr_writer.repo(svc, "completed")
            await corr_writer.phase("correlation", "started")
            handle = await self._submit_correlation(
                Path(config_path), repo_workspace_paths, scan_dir, event_file, ws)
            await self._await_workflow_result(handle)
            await corr_writer.phase("correlation", "completed")
            if req.url:
                async with self._create_scan_lock:
                    run_id, _ = self._store.create_blackbox_run(
                        ws, scan_id, auth_ref=self._snapshot_auth_ref(req))
                k = int(run_id.split("-")[1])
                await self._run_blackbox_phase(
                    scan_dir, ws, scan_id, self._snapshot_auth_ref(req), run_id,
                    workflow_id_suffix=f"-bb-{k}",
                    correlated_workspace=scan_id)
        except Exception as exc:
            final_status = "failed"
            if run_id is not None:
                await self._mark_run(scan_dir, run_id, "failed",
                                     reason=str(exc), status="failed")
        finally:
            await self._ensure_scan_end(scan_dir, status=final_status)
            self._orchestrator_tasks.pop(scan_key, None)
```

配套：`_resolve_repo_dir(ws, repo_name)`（从既有 `_resolve_inputs` 的 repo 解析逻辑抽出：`self._workspaces_dir / ws / "repos" / repo_name`，存在性校验）；`_dump_multi_repo_yaml(config, path)`（`yaml.safe_dump(config.model_dump(by_alias=True), allow_unicode=True)`）；顶部 import `CorrelationEventWriter`、`plan_repo_scans`、`parse_multi_repo_config`。
注：`start()` correlation 分支不再走 `_resolve_out_workspace`（旧 L1843 函数保留但不再被调用，plan D 阶段随清理删除）。

- [ ] **Step 4: Run**

Run: `uv run pytest packages/web/tests/test_scan_manager.py -v`
Expected: PASS（全部，含既有零回归）

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_scan_manager.py
git commit -m "feat(web): correlation 提交接通 C1 — 三段接力编排(子仓白盒→关联→黑盒验证)(C3)"
```

---

### Task C4: cancel 级联 + resume

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py:1585`（cancel）、resume 相关分支（对齐 `_combined_kickoff`/`resume` 现有结构）
- Test: `packages/web/tests/test_scan_manager.py`（追加）

**Interfaces:**
- Produces:
  - `async def _cancel_correlation(self, ws, scan_id, scan_dir, scan_key) -> dict`：取消 `self._orchestrator_tasks[scan_key]`（编排协程）+ `self._handles` 中主行/子仓 handle 逐个 cancel + `_mark_cancelled(主行)`；`cancel()` 在 session `is_correlation`（或 `corr_children` 非空）时路由到它（对齐 `_cancel_combined` 的路由模式）
  - resume：`resume()` 对 correlation 主行——heartbeat 判活则 no-op，stale 标 interrupted（对齐现有白盒语义；接力恢复依赖重新提交子仓不在首版范围，行为 = `_ensure_scan_end` 已兜底终态）

- [ ] **Step 1: Write failing test**

```python
async def test_cancel_correlation_cascades(tmp_path, monkeypatch):
    """取消主行：编排 task + 全部子仓 handle + 主行标记 cancelled。"""
    sm, store = _make_manager_with_store(tmp_path)
    # 造主行 session: is_correlation 语义（scan_type=correlation）+ 一个 running 编排 task 占位
    scan_id, scan_dir = store.create_scan("ws", "", "frontend", "correlation")
    cancelled = []
    monkeypatch.setattr(type(sm), "_mark_cancelled",
                        lambda self, d: cancelled.append(d.name))
    fake_handles = {}
    for name in ("main", "c1", "c2"):
        h = types.SimpleNamespace(cancel=AsyncMock())
        fake_handles[(("ws", name))] = h
    sm._handles.update(fake_handles)
    # 只把主行 key 放进 _handles 的场景 + 子仓 key：cancel 遍历 corr_children 对应 key
    ...
    result = await sm.cancel("ws", scan_id)
    assert scan_id in cancelled
```

（细节对齐文件内 `_cancel_combined` 既有测试构造；核心断言：主行 `cancelled` + 编排 task 被 pop。）

- [ ] **Step 2: Run to verify fail** — `uv run pytest packages/web/tests/test_scan_manager.py -k cancel_corr -v`，Expected FAIL。

- [ ] **Step 3: Implement**

`cancel()`：读 session，`scan_type == "correlation"`（`get_scan_type(scan_dir)`，core 已有）→ `return await self._cancel_correlation(...)`。实现（镜像 `_cancel_combined` L1972）：

```python
    async def _cancel_correlation(self, ws, scan_id, scan_dir, scan_key) -> dict:
        """级联取消：编排协程 → 主/子仓在跑 handle → 协作式信号兜底 → 标 cancelled。"""
        orch = self._orchestrator_tasks.get(scan_key)
        if orch is not None:
            orch.cancel()
            self._orchestrator_tasks.pop(scan_key, None)
        keys = [scan_key]
        for child in SessionManager(scan_dir.parent).get_session_data(scan_dir).get("corr_children") or []:
            if child.get("scan_id"):
                keys.append((ws, child["scan_id"]))
        for k in keys:
            handle = self._handles.get(k)
            if handle is not None:
                try:
                    await handle.cancel()
                except Exception:
                    pass
        if is_scan_recently_active(scan_dir):
            (scan_dir / "cancel.requested").write_text("", encoding="utf-8")
        await self._mark_cancelled(scan_dir)
        return {"cancelled": scan_id}
```

resume：`resume()` 的 correlation 分支按白盒 stale 语义处理（`_ensure_scan_end` 已把中断主行收终态；首版不做接力重入——在 resume 的 correlation 分支写明注释 + 测试断言 no-op/标 interrupted 路径）。

- [ ] **Step 4: Run** — `uv run pytest packages/web/tests/test_scan_manager.py -v`，Expected PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/components/scan_manager.py packages/web/tests/test_scan_manager.py
git commit -m "feat(web): correlation 取消级联(编排/子仓/主行) + resume 语义收尾(C4)"
```

---

### Task C5: correlation 详情 API + multi-configs 对齐

**Files:**
- Modify: `packages/web/src/supernova_web/api/scans.py`（新增端点）
- Test: `packages/web/tests/test_api_scans_correlation.py`（新建）

**Interfaces:**
- Produces: `GET /api/workspaces/{ws}/scans/{scan_id}/correlation` → `CorrelationDetail`：

```python
{
  "topology": {...},            # cross-service-topology.json 原文（无文件 → None）
  "boundaries": [...],          # trust-boundaries.json 原文
  "flows": [...],               # cross-service-flows.json 原文（无 → []）
  "merged_vulns": {"injection": [...], ...},  # 各 {vc}_exploitation_queue.json 的 vulnerabilities
  "drift_warnings": [...],      # correlation-report.md 不解析；漂移警告由事件/report 提取首版返回 []（保守），从 session 可选读
  "corr_children": [...],       # session 字段
  "report_md": "..."            # correlation-report.md 原文（无 → None）
}
```

- 404 语义：scan 不存在 → 404；非 correlation scan → 422（`{"detail": "not a correlation scan"}`）；产物未生成（关联未跑完）→ 200 + 各字段 null/[]（前端据此显示"关联阶段进行中/未开始"）。

- [ ] **Step 1: Write failing test**

`packages/web/tests/test_api_scans_correlation.py`（对齐文件内既有 API 测试的 client/fixture 构造，参考 `test_api_scans.py` 的写法）：

```python
async def test_correlation_endpoint_assembles(tmp_path, ...):
    # 造 ws + correlation scan 目录 + deliverables 四产物 + session corr_children
    # GET /api/workspaces/ws/scans/<id>/correlation
    # 断言 topology.services[0].name、flows[0].method、merged_vulns["injection"][0]["service"]、report_md 含 "# Cross-Repo"

async def test_correlation_endpoint_pending(...):
    # 主行存在但 deliverables 空 → 200，flows==[]、topology is None

async def test_correlation_endpoint_wrong_type(...):
    # 白盒 scan → 422
```

- [ ] **Step 2: Run to verify fail** — `uv run pytest packages/web/tests/test_api_scans_correlation.py -v`，Expected FAIL（404 路由不存在）。

- [ ] **Step 3: Implement**

`scans.py` 新增（组装逻辑放纯函数 `assemble_correlation_detail(scan_dir: Path) -> dict` 便于单测）：

```python
@router.get("/{ws}/scans/{scan_id}/correlation")
async def get_correlation_detail(ws: str, scan_id: str, ...deps) -> dict:
    scan_dir = ...  # 对齐既有 detail 端点的获取 + 鉴权模式
    if get_scan_type(scan_dir) != "correlation":
        raise HTTPException(422, "not a correlation scan")
    return assemble_correlation_detail(scan_dir)
```

`assemble_correlation_detail`：读 deliverables 下 4 类文件（缺文件 → None/[]），session 读 `corr_children`，返回 dict 如 Interfaces。

- [ ] **Step 4: Run** — `uv run pytest packages/web/tests/test_api_scans_correlation.py packages/web/tests/test_api_multi_configs.py -v`，Expected PASS（后者为既有 multi-configs 回归）。

- [ ] **Step 5: Commit**

```bash
git add packages/web/src/supernova_web/api/scans.py packages/web/tests/test_api_scans_correlation.py
git commit -m "feat(web): GET /scans/{id}/correlation 详情组装 API(topology/flows/merged_vulns,C5)"
```

---

## Phase D：前端

### Task D1: 表单⇄YAML 双向纯函数（`lib/correlation-yaml.ts`）

**Files:**
- Create: `packages/web/frontend/src/lib/correlation-yaml.ts`
- Test: `packages/web/frontend/src/lib/correlation-yaml.test.ts`

**Interfaces:**
- Consumes: js-yaml（package.json 已有则直接用，缺则 `npm i js-yaml @types/js-yaml`——先查）。
- Produces:

```ts
export type CorrRole = "entrypoint" | "backend";
export type CorrProtocol = "grpc" | "http" | "graphql";
export interface CorrRepoDraft {
  repo: string;            // 工作区仓库名
  role: CorrRole;
  protocol: CorrProtocol;  // entrypoint→该仓边的协议（backend 间互调边由 relations 自由编辑，仅 YAML）
  reuseScanId: string | null;  // null = 重新扫
  protoRoots?: string[];
}
export interface CorrRelation { from: string; to: string; protocol: CorrProtocol }
export interface CorrFormState {
  repos: CorrRepoDraft[];       // repo 名唯一
  relations: CorrRelation[];    // 完整边集（表单自动维护星型；YAML 可任意）
}
export function formToYaml(s: CorrFormState): string;
//   生成：repos.<name>: {path: repos/<name> 占位由后端覆写?} —— 见 Step 3 决策：前端生成
//   `workspace: <reuseScanId>`（复用）或 `path: <repo>`（现扫，值为仓库名——后端 C3 解析为 ws repos 路径），
//   + role + relations 图 + correlation: {}（out_workspace 后端生成，前端不写）。
export function yamlToForm(yaml: string): CorrFormState;   // 解析失败 throw CorrYamlError（含行信息）
export class CorrYamlError extends Error { issues: string[] }
export function validateForm(s: CorrFormState): string[];  // ≥1 entrypoint、repo 唯一、relations 引用存在
```

**path 字段决策（锁定）**：`RepoSpec.path` 在 web 语义 = **工作区仓库名**（C3 `_resolve_repo_dir` 按名解析）；CLI 语义 = 绝对路径。`path | None` schema 兼容两者，web 提交用仓库名。YAML 预览因此对用户可读（`path: frontend`）。

- [ ] **Step 1: Write failing tests**（`correlation-yaml.test.ts`，vitest）

```ts
import { describe, expect, it } from "vitest";
import { CorrFormState, formToYaml, yamlToForm, validateForm, CorrYamlError } from "./correlation-yaml";

const base: CorrFormState = {
  repos: [
    { repo: "frontend", role: "entrypoint", protocol: "grpc", reuseScanId: null },
    { repo: "order-svc", role: "backend", protocol: "grpc", reuseScanId: "frontend-20260801-120000" },
  ],
  relations: [{ from: "frontend", to: "order-svc", protocol: "grpc" }],
};

describe("formToYaml / yamlToForm roundtrip", () => {
  it("生成含 workspace 复用与 path 现扫", () => {
    const y = formToYaml(base);
    expect(y).toContain("frontend:");
    expect(y).toContain("path: frontend");
    expect(y).toContain("workspace: frontend-20260801-120000");
    expect(y).toContain("role: entrypoint");
  });
  it("roundtrip 无损（含多边自由拓扑）", () => {
    const s: CorrFormState = {
      repos: base.repos.concat([{ repo: "pay-svc", role: "backend", protocol: "http", reuseScanId: null }]),
      relations: [
        { from: "frontend", to: "order-svc", protocol: "grpc" },
        { from: "order-svc", to: "pay-svc", protocol: "http" },   // 后端互调
      ],
    };
    expect(yamlToForm(formToYaml(s))).toEqual(s);
  });
  it("坏 YAML throw CorrYamlError 带 issues", () => {
    expect(() => yamlToForm("repos: [")).toThrow(CorrYamlError);
    expect(() => yamlToForm("relations:\n  - {from: a, to: ghost}\nrepos:\n  a: {path: a, role: entrypoint}"))
      .toThrow(/ghost/);
  });
  it("validateForm：缺 entrypoint / 重复 repo 报错", () => {
    expect(validateForm({ repos: [{ repo: "a", role: "backend", protocol: "grpc", reuseScanId: null }], relations: [] }))
      .toEqual([expect.stringContaining("entrypoint")]);
    const dup = { repos: [base.repos[0], { ...base.repos[0] }], relations: [] };
    expect(validateForm(dup).length).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Run to verify fail**

Run: `cd packages/web/frontend && npx vitest run src/lib/correlation-yaml.test.ts`
Expected: FAIL — 模块不存在

- [ ] **Step 3: Implement**

```ts
import yaml from "js-yaml";

export type CorrRole = "entrypoint" | "backend";
export type CorrProtocol = "grpc" | "http" | "graphql";

export interface CorrRepoDraft { repo: string; role: CorrRole; protocol: CorrProtocol; reuseScanId: string | null; protoRoots?: string[]; }
export interface CorrRelation { from: string; to: string; protocol: CorrProtocol }
export interface CorrFormState { repos: CorrRepoDraft[]; relations: CorrRelation[] }

export class CorrYamlError extends Error {
  constructor(public issues: string[]) { super(issues.join("; ")); }
}

export function formToYaml(s: CorrFormState): string {
  const repos: Record<string, unknown> = {};
  for (const r of s.repos) {
    const spec: Record<string, unknown> = {
      path: r.repo,               // web 语义：工作区仓库名（后端 _resolve_repo_dir 解析）
      role: r.role,
    };
    if (r.reuseScanId) { delete spec.path; spec.workspace = r.reuseScanId; }
    if (r.protoRoots?.length) spec.proto_roots = r.protoRoots;
    repos[r.repo] = spec;
  }
  return yaml.dump({
    repos,
    relations: s.relations.map((e) => ({ from: e.from, to: e.to, protocol: e.protocol })),
  }, { noRefs: true, lineWidth: 120 });
}

export function yamlToForm(y: string): CorrFormState {
  const issues: string[] = [];
  let doc: any;
  try { doc = yaml.load(y); } catch (e: any) { throw new CorrYamlError([`YAML 语法错误: ${e.message}`]); }
  if (!doc || typeof doc !== "object" || !doc.repos) throw new CorrYamlError(["缺少 repos 段"]);
  const repos: CorrRepoDraft[] = [];
  for (const [name, raw] of Object.entries<any>(doc.repos)) {
    const reuse = raw?.workspace ?? null;
    repos.push({
      repo: name,
      role: raw?.role === "entrypoint" ? "entrypoint" : "backend",
      protocol: ["grpc", "http", "graphql"].includes(raw?.protocol) ? raw.protocol : "grpc",
      reuseScanId: typeof reuse === "string" ? reuse : null,
      protoRoots: Array.isArray(raw?.proto_roots) ? raw.proto_roots : undefined,
    });
  }
  const names = new Set(repos.map((r) => r.repo));
  const relations: CorrRelation[] = [];
  for (const e of doc.relations ?? []) {
    for (const side of ["from", "to"] as const) {
      if (!names.has(e?.[side])) issues.push(`relations 引用未声明服务: ${e?.[side]}`);
    }
    if (names.has(e?.from) && names.has(e?.to)) {
      relations.push({ from: e.from, to: e.to,
        protocol: ["grpc", "http", "graphql"].includes(e?.protocol) ? e.protocol : "grpc" });
    }
  }
  if (!repos.some((r) => r.role === "entrypoint")) issues.push("至少需要一个 entrypoint 仓库");
  if (issues.length) throw new CorrYamlError(issues);
  return { repos, relations };
}

export function validateForm(s: CorrFormState): string[] {
  const issues: string[] = [];
  if (!s.repos.some((r) => r.role === "entrypoint")) issues.push("至少需要一个 entrypoint 仓库");
  const seen = new Set<string>();
  for (const r of s.repos) {
    if (!r.repo.trim()) issues.push("存在未命名的仓库卡片");
    if (seen.has(r.repo)) issues.push(`仓库重复: ${r.repo}`);
    seen.add(r.repo);
    if (r.role === "backend" && r.reuseScanId === null && !r.repo.trim()) issues.push(`仓库 ${r.repo} 缺少来源`);
  }
  return issues;
}
```

（若 `js-yaml` 不在依赖：`cd packages/web/frontend && npm i js-yaml && npm i -D @types/js-yaml`，先 grep package.json。）

- [ ] **Step 4: Run** — `npx vitest run src/lib/correlation-yaml.test.ts`，Expected PASS。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/lib/correlation-yaml.ts packages/web/frontend/src/lib/correlation-yaml.test.ts packages/web/frontend/package.json packages/web/frontend/package-lock.json
git commit -m "feat(web): 表单⇄YAML 双向纯函数(路径=ws 仓库名语义,roundtrip 无损,D1)"
```

---

### Task D2: types + client 扩展

**Files:**
- Modify: `packages/web/frontend/src/api/types.ts`
- Modify: `packages/web/frontend/src/api/client.ts`
- Test: `packages/web/frontend/src/lib/api-correlation.test.ts`（纯类型/mock fetch 测试）

**Interfaces:**
- Produces:

```ts
// types.ts
export interface CorrelationDetail {
  topology: { services: { name: string; role: string; repo: string }[];
              edges: { from: string; to: string; protocol: string; status: string;
                       calls: CorrCall[]; error?: string | null }[] } | null;
  boundaries: { service: string; method: string; exposure: string;
                reachable_from: string[]; reason: string; confidence: string }[];
  flows: CorrFlow[];
  merged_vulns: Record<string, CorrVuln[]>;
  corr_children: { service: string; scan_id: string; reused: boolean }[];
  report_md: string | null;
}
export interface CorrCall { method: string; call_site: { file: string; line: number; snippet: string };
                             confidence: string; evidence: string }
export interface CorrFlow { edge_from: string; edge_to: string; entry: string; method: string;
                             call_site: { file: string; line: number; snippet: string };
                             vuln_refs: { service: string; title: string; severity: string; location: string }[];
                             confidence: string; evidence: string }
export interface CorrVuln { title: string; description?: string; severity?: string;
                             location?: string; service?: string; [k: string]: unknown }
// client.ts
export function getCorrelationDetail(ws: string, scanId: string): Promise<CorrelationDetail>;
export function listMultiConfigs(): Promise<{ name: string; ... }[]>;
export function saveMultiConfig(name: string, content: string): Promise<...>;   // 对齐后端 multi_configs.py 载荷
```

同步清理：`ScanRequest` 前端类型删 `config_yaml?`（后端无此字段）；`ScanSummary` 加 `corr_children?: {...}[] | null`。

- [ ] **Step 1: Write failing test**（`api-correlation.test.ts`，mock 全局 fetch，对齐仓库既有 client 测试模式）

```ts
import { describe, expect, it, vi, beforeEach } from "vitest";
import { getCorrelationDetail } from "../api/client";

describe("getCorrelationDetail", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
  it("GET /api/workspaces/{ws}/scans/{id}/correlation", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ topology: null, flows: [] }), { status: 200 }));
    await getCorrelationDetail("ws1", "scan-1");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/workspaces/ws1/scans/scan-1/correlation");
  });
});
```

- [ ] **Step 2: Run to verify fail** — `npx vitest run src/lib/api-correlation.test.ts`，Expected FAIL（函数不存在）。

- [ ] **Step 3: Implement** — types.ts 加 Interfaces 所列类型；client.ts：

```ts
export function getCorrelationDetail(ws: string, scanId: string): Promise<CorrelationDetail> {
  return apiGet<CorrelationDetail>(`/workspaces/${ws}/scans/${scanId}/correlation`);
}
export function listMultiConfigs(): Promise<MultiConfigSummary[]> {
  return apiGet<MultiConfigSummary[]>("/multi-configs");
}
export function saveMultiConfig(name: string, content: string): Promise<void> {
  return apiPost("/multi-configs", { name, content });
}
```

（`MultiConfigSummary` 字段对齐后端 `api/multi_configs.py` 返回结构——执行者读该文件后定，勿臆造。）

- [ ] **Step 4: Run** — `npx vitest run src/lib/api-correlation.test.ts`，Expected PASS。

- [ ] **Step 5: Commit** — `feat(web): CorrelationDetail 类型 + API client(D2)`。

---

### Task D3: ScanNewPage 类型切换 + 跨仓表单组件

**Files:**
- Create: `packages/web/frontend/src/components/correlation/CorrelationFormFields.tsx`
- Create: `packages/web/frontend/src/components/correlation/YamlPanel.tsx`
- Modify: `packages/web/frontend/src/pages/ScanNewPage.tsx`
- Test: `packages/web/frontend/src/components/correlation/CorrelationFormFields.test.tsx`、`packages/web/frontend/src/pages/ScanNewPage.test.tsx`（若存在则扩展）

**Interfaces:**
- Consumes: D1 `CorrFormState`/`formToYaml`/`yamlToForm`/`validateForm`；既有 `RepoCombobox`、`AuthFormState`/`HostFormState` 表单块（从 `ScanFormFields` 抽出复用或引用其子组件——执行者读 ScanFormFields.tsx 后选择最小改动方式：优先把认证/HOST 块抽成独立组件双处复用，抽不出则 CorrelationFormFields 内嵌同结构副本并注明）。
- Produces:
  - `CorrelationFormFields(props: { state: CorrFormState; onState: (s: CorrFormState) => void; yaml: string; onYaml: (y: string) => void; yamlError: CorrYamlError | null; workspace: string; wsList: Workspace[]; onWorkspaceChange; wsLoading; gatewayUrl: string; onGatewayUrl; auth: AuthFormState; setAuth; host: HostFormState; setHost; })`
  - 仓库卡片增删/角色/复用下拉（复用候选用 `useScans(workspace)` 过滤 `scan_type==="whitebox" && repo===卡片仓库名`）；表单态变更后 `onState` + 自动重生成 YAML（父层持 yaml 为派生态：表单交互路径 yaml=formToYaml(state)，YAML 编辑路径仅 blur 时 parse 回 state——单向数据流防回路）
  - `YamlPanel`：折叠（默认收起）+ textarea + 错误行提示 + "应用到表单"按钮（显式，非实时回填——防打字中间态抖动）
  - `ScanNewPage`：`type: "whitebox" | "correlation"` 切换 segmented（i18n 词条 `scan.type.whitebox`/`scan.type.correlation`）；**删除**黑盒只读分支（L339 的 preset.type==="blackbox" 三元、L284-292 buildBody blackbox 分支、`reuseScanId` 校验路径、RerunPreset 黑盒语义——历史黑盒扫描的重跑入口移除，ScanList 黑盒行不再提供 onRerun 到表单）；correlation 提交 body：`{type:"correlation", workspace, url: gatewayUrl||undefined, config_content: yaml, save_as?}` + 认证/HOST（gatewayUrl 非空时 assign，复用既有 assignAuthToBody/assignHostToBody）

- [ ] **Step 1: Write failing tests**

`CorrelationFormFields.test.tsx` 核心用例（对齐仓库既有组件测试风格——render + userEvent + i18n 测试 wrapper）：

```tsx
it("添加两个仓库 + 角色默认第一个 entrypoint → 生成星型 YAML", async () => { ... });
it("复用模式选历史扫描 → YAML 含 workspace 字段", async () => { ... });
it("YAML 编辑非法引用 → 错误提示 + 应用按钮禁用", async () => { ... });
it("缺 entrypoint 提交校验拦截", async () => { ... });
```

`ScanNewPage.test.tsx`：

```tsx
it("类型切换到跨仓关联渲染跨仓表单（含 YAML 面板）", async () => { ... });
it("黑盒预填 preset 不再触发黑盒表单（渲染白盒）", async () => { ... });   // 分支删除回归
it("提交 correlation body 含 config_content + workspace", async () => { ... });
```

- [ ] **Step 2: Run to verify fail** — `npx vitest run src/components/correlation/ src/pages/ScanNewPage.test.tsx`，Expected FAIL。

- [ ] **Step 3: Implement**（组件骨架——样式 token 对齐 ScanFormFields 既有 class；此处列结构要点）

`CorrelationFormFields.tsx`：
- ① 工作区下拉（复用既有 Select 模式）
- ② 仓库卡片列表：`state.repos.map` → 卡片（`RepoCombobox` 单选；role `RadioGroup`；来源 `Tabs`/`RadioGroup`：重新扫 | 复用历史扫描（复用时 `Select` 列 `listScans(workspace)` 过滤结果，选项文案 `<scan_id>（时间 · 状态）`）；协议 `Select` grpc/http/graphql）；「添加仓库」按钮（push `{repo:"", role:"auto", protocol:"grpc", reuseScanId:null}`，role 逻辑：无 entrypoint 时默认 entrypoint 否则 backend + 自动补星型边）；删除按钮（清关联边）
- ③ relations 只读摘要（`frontend → order-svc (grpc)` chip 列表 + "在 YAML 中编辑复杂拓扑"提示）
- ④ 黑盒验证开关（gateway URL input + 展开 AuthFormState/HostFormState 块）
- ⑤ `YamlPanel`（props 传 yaml/yamlError/apply）

`YamlPanel.tsx`：

```tsx
export function YamlPanel({ yaml, onChange, error, onApply }: {
  yaml: string; onChange: (y: string) => void;
  error: CorrYamlError | null; onApply: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <div data-testid="corr-yaml-panel">
      <button type="button" onClick={() => setOpen(!open)}>{t("scan.correlation.yamlToggle")}</button>
      {open && (
        <>
          <textarea aria-label={t("scan.correlation.yamlEditor")} value={yaml}
                    onChange={(e) => onChange(e.target.value)} rows={14}
                    className="font-mono text-xs w-full ..." spellCheck={false} />
          {error && <p role="alert" className="text-destructive text-xs">{error.message}</p>}
          <Button variant="outline" onClick={onApply} disabled={!!error}>
            {t("scan.correlation.applyYaml")}
          </Button>
        </>
      )}
    </div>
  );
}
```

`ScanNewPage.tsx` 改造要点：
- `const [type, setType] = useState<"whitebox" | "correlation">("whitebox")`（segmented 顶部）；删 preset blackbox 逻辑；
- correlation 状态：`const [corrState, setCorrState] = useState<CorrFormState>(EMPTY_CORR)`、`const [corrYaml, setCorrYaml] = useState(() => formToYaml(EMPTY_CORR))`、`const [yamlErr, setYamlErr] = useState<CorrYamlError | null>(null)`；
- 表单路径 setState 包装：`updateCorr = (s) => { setCorrState(s); setCorrYaml(formToYaml(s)); setYamlErr(null); }`；YAML 路径：`onYaml = (y) => { setCorrYaml(y); try { /* 仅校验 */ yamlToForm(y); setYamlErr(null); } catch (e) { setYamlErr(e as CorrYamlError); } }`；`applyYaml = () => updateCorr(yamlToForm(corrYaml))`；
- `buildBody` correlation 分支重写（替换 L263 死分支）：

```ts
if (type === "correlation") {
  const body: ScanRequest = { type, workspace: workspace || undefined };
  body.config_content = corrYaml;
  if (gatewayUrl.trim()) {
    body.url = gatewayUrl.trim();
    if (auth.enabled) assignAuthToBody(body, auth);
    assignHostToBody(body, host);
  }
  return body;
}
```

（`gatewayUrl` 存 `f.url` 复用现有 FormState.url 字段即可，避免新 state。）

- 校验：`isValid` 增加 correlation 分支（`validateForm(corrState)` 空 + `!yamlErr` + workspace；gatewayUrl 开时 auth/host 校验复用）。

- [ ] **Step 4: Run** — `npx vitest run src/components/correlation/ src/pages/ScanNewPage.test.tsx`，Expected PASS；随后全量 `npm test`（确认无既有回归——ScanList 等对 ScanNewPage 的依赖）。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/components/correlation/ packages/web/frontend/src/pages/ScanNewPage.tsx packages/web/frontend/src/pages/ScanNewPage.test.tsx
git commit -m "feat(web): ScanNewPage 白盒|跨仓关联类型切换 + 表单⇄YAML 双向表单(删黑盒分支,D3)"
```

---

### Task D4: 扫描列表 correlation 主行 + 嵌套子行列

**Files:**
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/ScanList.tsx`
- Test: `packages/web/frontend/src/routes/WorkspaceDetail/ScanList.test.tsx`（扩展）

**Interfaces:**
- Consumes: D2 `ScanSummary.corr_children`；既有黑盒 run 嵌套子行列模式（同网格对齐 + 默认收起，见 commit e4fa954b 的实现结构）。
- Produces:
  - 类型过滤加 `correlation` 档（分段按钮，i18n `workspaces.filter.correlation`）；更新 L188-189 注释
  - correlation 主行：类型徽标 `🔗`（`StatusBadge` 的 correlation prop 接回）；展开行 = 子仓白盒行（`corr_children` 中 `reused=false` 的，列对齐主表：仓库名/状态/漏洞数/时间，行链接 `/p/{ws}/scans/{child.scan_id}`）+ 黑盒验证 run 行（读主行 `bb_runs`，既有渲染）+ 复用子仓引用行（`reused=true`，链接历史 scan，标注"复用"）

- [ ] **Step 1-5**：测试（渲染 corr 主行展开三种子行/过滤档位/复用行链接）→ 挂 → 实现 → 绿 → commit `feat(web): 扫描列表 correlation 主行嵌套子行列 + 类型过滤(D4)`。

---

### Task D5: CorrelationTab 结果视图

**Files:**
- Create: `packages/web/frontend/src/routes/WorkspaceDetail/CorrelationTab.tsx`
- Create: `packages/web/frontend/src/components/correlation/TopologyGraph.tsx`
- Create: `packages/web/frontend/src/components/correlation/AttackChainCard.tsx`
- Test: 对应 `.test.tsx`

**Interfaces:**
- Consumes: D2 `getCorrelationDetail`；`VulnCard`（既有）、`MarkdownView`（既有）。
- Produces:
  - `CorrelationTab({ ws, scanId })`：SWR 拉取详情；区块顺序 = 漂移警告横幅 → 服务拓扑图 → 跨服务攻击链 → 按服务分组漏洞 → 信任边界 → 报告 md；产物未生成时（topology null）显示阶段占位（"关联阶段进行中/未开始"，含 corr_children 子仓状态）
  - `TopologyGraph({ topology })`：纯 SVG——entrypoint 节点列左（x=80）、backend 网格列右（x=420，垂直均分）；节点 = 圆角矩形（`<rect rx>` + 服务名 + role 徽标文字）；边 = `<path>` 直线箭头 + 中点 `<text>` protocol + status 着色 class（`ok:text-emerald-500 / low:text-amber-500 / unverified:text-muted-foreground / error:text-destructive / declared-missing:stroke-dashed`）；点边 → 下方展开该边 calls 表（method / file:line / snippet / confidence / evidence）
  - `AttackChainCard({ flow })`：三段式横排（`entry@call_site` → `method` → `vuln_refs` 列表），confidence 徽标 + evidence 折叠

- [ ] **Step 1: Write failing tests**

`CorrelationTab.test.tsx`：

```tsx
const detail: CorrelationDetail = { /* fixture：2 服务 + 1 边(ok) + 1 flow + merged_vulns 两条 + boundaries 一条 */ };
it("渲染拓扑节点与边", async () => { /* getByText("frontend")/("order-svc")，边 protocol "grpc" */ });
it("渲染攻击链三段", async () => { /* getByText("POST /orders") + method + vuln title */ });
it("flows 为空降级提示", async () => { /* rerender flows:[] → 显示降级文案 */ });
it("按服务分组漏洞 + service 徽标", async () => { /* VulnCard 出现 + "order-svc" 徽标 */ });
it("topology null 显示进行中占位", async () => { /* getByText 占位文案 */ });
```

`TopologyGraph.test.tsx` / `AttackChainCard.test.tsx`：组件级渲染断言（节点/边/calls 展开/三段内容）。

- [ ] **Step 2: Run to verify fail** — `npx vitest run src/routes/WorkspaceDetail/CorrelationTab.test.tsx ...`，Expected FAIL。

- [ ] **Step 3: Implement**（关键骨架）

`TopologyGraph.tsx`（布局计算纯函数导出便于测试）：

```tsx
export interface NodePos { name: string; x: number; y: number; role: string }
export function layout(services: { name: string; role: string }[],
                       width = 560, heightPerNode = 90): { nodes: NodePos[]; height: number } {
  const eps = services.filter((s) => s.role === "entrypoint");
  const bes = services.filter((s) => s.role !== "entrypoint");
  const nodes: NodePos[] = [];
  const colH = (n: number) => Math.max(n, 1) * heightPerNode;
  const height = Math.max(colH(eps.length), colH(bes.length)) + 40;
  eps.forEach((s, i) => nodes.push({ name: s.name, role: s.role, x: 90, y: 40 + i * heightPerNode + 20 }));
  bes.forEach((s, i) => nodes.push({ name: s.name, role: s.role, x: 430, y: 40 + i * heightPerNode + 20 }));
  return { nodes, height };
}
// 组件：<svg viewBox={`0 0 560 ${height}`}> 节点 rect+text、edges path+marker 箭头、
// edge onClick → onSelect(edge)；declared-missing 边 strokeDasharray="6 4"。
```

`AttackChainCard.tsx`：

```tsx
export function AttackChainCard({ flow }: { flow: CorrFlow }) {
  const { t } = useTranslation();
  return (
    <Card data-testid="attack-chain">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs">{flow.entry}</span>
        <span aria-hidden>→</span>
        <span className="font-mono text-xs">{flow.method}</span>
        <span aria-hidden>→</span>
        {flow.vuln_refs.map((v, i) => (
          <span key={i} className="text-xs">
            [{v.service}] {v.title} · {v.severity} · {v.location}
          </span>
        ))}
        <ConfidenceBadge value={flow.confidence} />
      </div>
      <details><summary className="text-xs text-muted-foreground">{t("scan.correlation.evidence")}</summary>
        <p className="text-xs">{flow.evidence}</p>
        <p className="font-mono text-xs">{flow.call_site.file}:{flow.call_site.line}</p>
      </details>
    </Card>
  );
}
```

`CorrelationTab.tsx`：

```tsx
export function CorrelationTab({ ws, scanId }: { ws: string; scanId: string }) {
  const { t } = useTranslation();
  const { data } = useSWR(["corr-detail", ws, scanId],
    () => getCorrelationDetail(ws, scanId), { refreshInterval: 15000 });  // 关联运行中轮询
  if (!data) return <Skeleton rows={3} />;
  if (!data.topology) return <EmptyState label={t("scan.correlation.pending")}
                                          hint={t("scan.correlation.pendingHint")} />;
  return (
    <div className="space-y-6">
      <TopologyGraph topology={data.topology} />
      <section>
        <h3>{t("scan.correlation.flows")}</h3>
        {data.flows.length
          ? data.flows.map((f, i) => <AttackChainCard key={i} flow={f} />)
          : <p className="text-sm text-muted-foreground">{t("scan.correlation.flowsEmpty")}</p>}
      </section>
      <section>{/* 按服务分组：groupedVulns(data.merged_vulns) → VulnCard + service 徽标 */}</section>
      <section>{/* boundaries 表：service/method/exposure/reachable_from */}</section>
      {data.report_md && <MarkdownView content={data.report_md} />}
    </div>
  );
}
```

- [ ] **Step 4: Run** — `npx vitest run src/routes/WorkspaceDetail/CorrelationTab.test.tsx src/components/correlation/`，Expected PASS。

- [ ] **Step 5: Commit** — `feat(web): CorrelationTab 专属视图(拓扑图+攻击链+分组漏洞+边界+报告,D5)`。

---

### Task D6: 路由接线 + live 事件渲染

**Files:**
- Modify: `packages/web/frontend/src/router.tsx`（`/p/:workspace/scans/:scanId/correlation` 子路由）
- Modify: `packages/web/frontend/src/routes/WorkspaceDetail/ScanDetail.tsx`（correlation 主行 tab 组：`概览 | 跨仓关联 | 产物 | 日志`——tab 列表按 `scan_type` 分支）
- Modify: live 页/`dashboardReducer.ts`（`CorrelationProgressEvent` 处理：repo/edge 状态渲染——live 页已有 ndjson SSE 消费框架，加 correlation_progress case 渲染阶段网格）
- Test: `ScanDetail.test.tsx`（扩展）、live reducer 测试（扩展）

**Interfaces:**
- Produces: correlation 主行详情导航四 tab；live 页消费 `correlation_progress` 事件（node=repo/phase/edge → 网格行 + 状态徽标）。
- 注意 `ScanDetail.tsx` 的 DefaultScanTab 对 correlation 主行默认落在"概览"（阶段进度 + corr_children 链接列表——本 task 实现一个简版 `CorrelationOverview`：children 状态网格 + 阶段横幅，数据源 `getScan` + `getCorrelationDetail`）。

- [ ] **Step 1-5**：测试（correlation scan 详情渲染四 tab + 默认概览 / reducer 处理 correlation_progress 事件）→ 挂 → 实现 → 绿 → commit `feat(web): correlation 详情路由接线 + live correlation_progress 渲染(D6)`。

---

### Task D7: i18n 词条 + 清理

**Files:**
- Modify: `packages/web/frontend/src/locales/en.json` + `zh.json`
- Modify: 清理点（见下）
- Test: 全量 `npm test`

**Interfaces:** 无新接口。

- [ ] **Step 1: 词条**（`scan.correlation.*` 命名空间，en/zh 成对；此处列关键词条，执行者按组件实际 `t()` 调用补全）：

```json
"correlation": {
  "typeLabel": "Cross-repo correlation | 跨仓关联",
  "addRepo": "Add repository | 添加仓库",
  "roleEntrypoint": "Entrypoint (frontend) | 入口（前端）",
  "roleBackend": "Backend service | 后端服务",
  "sourceRescan": "Scan fresh | 重新扫描",
  "sourceReuse": "Reuse past scan | 复用历史扫描",
  "protocol": "Protocol | 协议",
  "relationsTitle": "Service relations | 服务关系",
  "relationsHint": "Star topology is auto-generated; edit YAML for backend-to-backend edges | 星型关系自动生成；后端互调请在 YAML 中编辑",
  "gatewayUrl": "Gateway URL (optional, enables black-box verification) | Gateway 地址（选填，开启黑盒验证）",
  "yamlToggle": "YAML | YAML 配置",
  "yamlEditor": "Multi-repo YAML | 多仓 YAML",
  "applyYaml": "Apply to form | 应用到表单",
  "pending": "Correlation phase not finished | 关联阶段未完成",
  "pendingHint": "Sub-repo scans or correlation analysis in progress | 子仓扫描或关联分析进行中",
  "flows": "Cross-service attack chains | 跨服务攻击链",
  "flowsEmpty": "No cross-service chain inferred (check topology edges for unverified relations) | 未推断出跨服务链路（未验证关系见拓扑边）",
  "evidence": "Evidence | 证据",
  "boundaries": "Trust boundaries | 信任边界",
  "childrenTitle": "Sub-repo scans | 子仓扫描",
  "reused": "Reused | 复用"
}
```

（写入时 en.json 放英文值、zh.json 放中文值；同时删除死词条：`workspaces.noChild`、`workspaces.table.*`、`workspaces.stats.*`、`workspaces.searchPlaceholder`、`workspaces.statusFilterAria`、`scan.cardTitle.*` 整组——先 grep 确认无引用再删。）

- [ ] **Step 2: 清理**：
  - `types.ts`：删 `config_yaml?`（ScanRequest 前端类型）；
  - `ScanList.tsx:188-189` 注释改为"correlation 已接通（spec 2026-08-24），过滤档见上"；
  - `scan_manager.py`：删 `_resolve_out_workspace`（L1843，已无调用方）；
  - grep `config_yaml`/`cardTitle`/`noChild` 全前端确认零残留。

- [ ] **Step 3: 全量前端测试**

Run: `cd packages/web/frontend && npm test`
Expected: PASS（全量，无回归）

- [ ] **Step 4: Commit** — `feat(web): correlation i18n 词条 + 死词条/幽灵字段清理(D7)`。

---

## 端到端冒烟（人工验收，非自动化 task）

前置：fixture 两仓（迷你 Node/TS gateway + Go gRPC 后端，调用关系已知——可造最小 fixture：gateway 含 `client.createOrder(req.body)` 路由、backend handler 拼接 SQL）。

1. `docker compose up`（或项目惯用启动方式）起 temporal + worker + web；worker 容器确认三 queue 注册（日志含 supernova-corr-web）。
2. web 建仓：ws 内添加 `frontend`/`order-svc` 两仓库（clone 或 zip 上传）。
3. `/scan/new` 切"跨仓关联"：两仓卡片（frontend=entrypoint 现扫、order-svc=backend 现扫）→ 核对 YAML 面板生成正确 → 提交 → 跳主行 live。
4. 观察：子仓两行独立白盒扫描跑完 → 关联阶段 edge 事件 →（不填 gateway URL）主行 completed。
5. 详情页"跨仓关联" tab：拓扑两节点一边、攻击链卡片（fixture 应产出 ≥1 flow：POST /orders → CreateOrder → SQLi）、按服务分组漏洞、报告 md。
6. 复用路径：再发起一次，order-svc 选"复用历史扫描"→ 不建子行、直接关联 → 漂移警告（改 backend 文件后）验证。
7. 黑盒验证：gateway URL 填可访问地址（+认证可选）→ run-1 创建、exploit 上下文含 topology（worker 日志或黑盒 deliverables 佐证 `cross_service_topology` 注入）。
8. 取消：运行中取消主行 → 子仓/关联 workflow 全停、状态 cancelled。
9. 回归：普通白盒扫描、组合扫描（白盒+黑盒）各跑一次行为不变；`uv run pytest packages/multi/tests/ packages/core/tests/correlation/ -v` + `npm test` 全绿。

## Self-Review 记录

- **Spec 覆盖**：§5.1（A3）/§5.2（B1/B2）/§5.3（C1-C4）/§5.4（A1/A2 flows）/§6（C2 corr_children、D2 类型）/§7（C5 API、multi-configs D3 载入）/§8.1（D1/D3）/§8.2（D4）/§8.3（D5/D6）/§8.4（D7）/§9（C3/C4 错误路径 + D5 占位）/§10（各 task 测试 + 冒烟）/§12 验收清单逐项映射冒烟步骤。✓
- **占位符扫描**：C 系列测试里 `...` 仅出现在"构造对齐文件内既有 helper"的说明性位置，执行者需读既有测试后补齐构造——这是引用既有代码模式的约定，非缺实现；关键断言均给出。✓
- **类型一致性**：`run_correlation_phase(config, repo_workspace_paths, out_ws_dir, event_file, *, pipeline_testing, provider_config, write_scan_end)` 在 A3/B1/C3 三处签名一致；`CorrelationPipelineInput` 字段 B1 定义、C2 构造一致；`corr_children: [{service, scan_id, reused}]` 在 C2/C3/C5/D2/D4 一致；前端 `CorrFormState`/`formToYaml`/`yamlToForm` 在 D1/D3 一致。✓
