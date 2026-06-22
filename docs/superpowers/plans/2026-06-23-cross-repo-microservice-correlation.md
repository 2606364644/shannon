# 跨仓微服务关联 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Shannon 支持"TS/Node gateway → Go gRPC 后端"多仓微服务场景：声明式编排 N 个 repo 白盒扫描，新增 `cross-repo-correlation` Agent 做跨仓关联分析（拓扑/信任边界/候选数据流/漏洞合并），产物落独立关联 workspace；黑盒侧加 `--correlated-workspace` 复用 topology 做 gateway 层关联验证。

**Architecture:** 纯 Python 编排器（仿 `packages/combined/src/shannon_combined/orchestrator.py`，**不用 Temporal child workflow**——项目零先例且每个 scan 是 self-contained worker 进程）：解析 `multi-repo.yaml` → 依次跑 N 个 repo 白盒（复用已有 workspace 或现扫）→ 关联 Agent 在编排器进程内用 `AgentExecutor` 执行，per-edge 推断用 `asyncio.gather` + `Semaphore` 并发（单边失败不拖垮全局）→ 三个产物 + 合并 queue 落独立关联 workspace（`scan_type="correlation"`）。黑盒侧加 `--correlated-workspace` flag 穿透 4 层，`exploit_executor` 注入 topology/boundaries。

**Tech Stack:** Python 3.12、Temporal SDK（白盒现扫复用现有 worker）、Pydantic v2（multi-repo schema）、click（CLI）、asyncio（per-edge 并发）、PyYAML。

**关联 Agent 执行形态决策（架构）**：关联 Agent 在**编排器进程内**用 `AgentExecutor.execute()` 执行，不走 Temporal activity。
- 依据：Agent 2 摸底——项目无 child workflow 先例；`combined/orchestrator.py` 证明纯 Python 串行编排可行；whitebox worker self-contained。
- per-edge 并发用 `asyncio.Semaphore`（呼应 spec B5，避免撞 LLM 并发上限；上限复用现有 `max_concurrent` 配置）。
- 收益：避免新增关联 Temporal worker/workflow 的测试负担（项目 memory 明确 pytest 跑 Temporal/网络慢测试会 hang）；且规避新 activity 的"定义/调用/worker 注册"三处同步（memory: `temporalio-activity-worker-registration`——第 3 处 worker 注册是易漏横切点，漏则真机崩、单测测不出）。
- 代价：无 Temporal 断点续跑/重试兜底——但关联 Agent 只读、失败重跑成本低（关联 workspace 是新建的），可接受。风险见 Task A4/A6。

## Global Constraints

- **单仓零回归**（spec §2/§12）：白盒 `--repo` / 黑盒单仓 `--repo`·`--latest` 路径行为完全不变。所有新代码在 `packages/multi/`、`packages/core/src/shannon_core/correlation/`、`packages/core/src/shannon_core/models/multi_repo_config.py`、关联 prompt、以及黑盒的**条件分支**（仅 `--correlated-workspace` 指定时走新路径）内。
- **关联 Agent 工具限定只读**（spec §9）：prompt 只用 `read_file` / `grep` / `glob`，**不依赖 Task tool / Browser**（OpenAI 引擎 `build_tools()` 无这些，glm-anthropic 冒烟须通过）。
- **合并 queue 四字段硬约束**（spec B1）：合并 N 仓 exploitation_queue 后，每条 entry 必须保留 `title`/`description`/`severity`/`location`（黑盒 `has_valid_whitebox_results` subset 检查），跨服务标注用额外字段 `service`/`cross_service_source`。
- **关联 workspace scan_type = `"correlation"`**（spec B2）：不走 `find_workspaces_by_url`（按 url + scan_type=whitebox 双重过滤，关联 workspace 都不满足）。
- **新测试独立模块**（spec §10）：全部新测试放 `packages/multi/tests/` 与 `packages/core/tests/correlation/`，**不依赖** feat/fork-py 预存挂起 suite（`test_worker_progress` / `test_cli follow` / `test_audit_injection` / integration）；全套广跑用 `--ignore`。
- **版本漂移统一时间戳粗判**（spec A2）：`session.json` 不存 git commit，统一用 workspace `created_at` vs repo 最近改动时间。
- **Python 3.12+，uv workspace monorepo**；新包 `packages/multi/` 须在根 `pyproject.toml` 注册 dependency + `[tool.uv.sources]` workspace。

---

## File Structure

### 新建 `packages/multi/` 包（编排器）

| 文件 | 职责 |
|---|---|
| `packages/multi/pyproject.toml` | 包声明 + `[project.scripts]` 注册 `shannon-multi` |
| `packages/multi/src/shannon_multi/__init__.py` | 空 |
| `packages/multi/src/shannon_multi/cli/__init__.py` | 空 |
| `packages/multi/src/shannon_multi/cli/main.py` | click 入口：`shannon-multi start --config multi-repo.yaml` |
| `packages/multi/src/shannon_multi/orchestrator.py` | 编排核心：配置→N repo 白盒（复用/现扫）→关联 Agent（per-edge asyncio）→产物落盘 |

### 新建 core 共享层

| 文件 | 职责 |
|---|---|
| `packages/core/src/shannon_core/models/multi_repo_config.py` | Pydantic schema：`MultiRepoConfig`/`RepoSpec`/`Relation`/`CorrelationConfig` + 校验（role 枚举/entrypoint 必填/relations 引用） |
| `packages/core/src/shannon_core/config/parser.py` | **修改**：加 `parse_multi_repo_config(path) -> MultiRepoConfig`（照搬 `parse_config` 模式） |
| `packages/core/src/shannon_core/models/agents.py` | **修改**：加 `AgentName.CROSS_REPO_CORRELATION` + `AGENTS` 条目 + `AGENT_PHASE_MAP` |
| `packages/core/src/shannon_core/correlation/__init__.py` | 空 |
| `packages/core/src/shannon_core/correlation/schemas.py` | 关联产物 schema：`TopologyEdge`/`CallSite`/`TrustBoundary`/`CorrelationResult`/`MergedVulnEntry` |
| `packages/core/src/shannon_core/correlation/queue_merge.py` | 合并 N 仓 exploitation_queue（保留 B1 四字段 + 加 service 标注） |
| `packages/core/src/shannon_core/correlation/drift.py` | 版本漂移检测（时间戳粗判 A2） |
| `packages/core/src/shannon_core/correlation/report.py` | 产物落盘：topology.json/boundaries.json/correlation-report.md |

### 新建关联 prompt

| 文件 | 职责 |
|---|---|
| `prompts/cross-repo-correlation.txt` | 关联 Agent prompt（只用 read_file/grep/glob，职责①–⑦） |
| `prompts/pipeline-testing/cross-repo-correlation.txt` | CI 简化 prompt |

### 新建测试

| 文件 | 职责 |
|---|---|
| `packages/core/tests/correlation/__init__.py` | 空 |
| `packages/core/tests/correlation/test_multi_repo_config.py` | 配置 schema + 校验（Task A1） |
| `packages/core/tests/correlation/test_schemas.py` | 产物 schema 序列化（Task A2） |
| `packages/core/tests/correlation/test_queue_merge.py` | 合并 queue 四字段约束（Task A3） |
| `packages/core/tests/correlation/test_drift.py` | 漂移检测（Task A3） |
| `packages/multi/tests/__init__.py` | 空 |
| `packages/multi/tests/test_orchestrator.py` | 复用/现扫分支 + per-edge 隔离（Task A5/A6） |

### Phase B 修改（黑盒）

| 文件 | 职责 |
|---|---|
| `packages/blackbox/src/shannon_blackbox/cli/main.py` | 加 `--correlated-workspace` option + start 参数 + PipelineInput 赋值 |
| `packages/blackbox/src/shannon_blackbox/pipeline/shared.py` | `BlackboxPipelineInput` + `BlackboxActivityInput` 加 `correlated_workspace` 字段 |
| `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | 传 correlated_workspace + 检测关联 workspace deliverables 跳过 recon |
| `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py` | prompt_variables 加 `cross_service_topology`/`trust_boundaries` 两项（spec A3） |

---

## Phase A：白盒跨仓关联（独立可测、可先交付）

### Task A1: multi-repo.yaml 配置 schema + 解析 + 校验

**Files:**
- Create: `packages/core/src/shannon_core/models/multi_repo_config.py`
- Modify: `packages/core/src/shannon_core/config/parser.py`（加 `parse_multi_repo_config`）
- Test: `packages/core/tests/correlation/test_multi_repo_config.py`

**Interfaces:**
- Consumes: `packages/core/src/shannon_core/config/parser.py` 现有 `parse_config` 的模式（`yaml.safe_load` + Pydantic `model_validate`）
- Produces:
  - `MultiRepoConfig`（Pydantic），字段：`description: str | None`、`repos: dict[str, RepoSpec]`、`relations: list[Relation]`、`correlation: CorrelationConfig`
  - `RepoSpec`：`path: str | None`、`workspace: str | None`、`role: Literal["entrypoint","backend"] = "backend"`、`scan_config: str | None`、`proto_roots: list[str] = []`
  - `Relation`：`from_: str`（pydantic alias `from`）、`to: str`、`protocol: Literal["grpc","http","graphql"] = "grpc"`（spec B7）
  - `CorrelationConfig`：`out_workspace: str`
  - `parse_multi_repo_config(path: str | Path) -> MultiRepoConfig`

- [ ] **Step 1: Write failing test — schema 校验**

`packages/core/tests/correlation/test_multi_repo_config.py`:
```python
import textwrap
from pathlib import Path
import pytest
from pydantic import ValidationError

from shannon_core.models.multi_repo_config import MultiRepoConfig
from shannon_core.config.parser import parse_multi_repo_config


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "multi-repo.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_parse_valid_graph_config(tmp_path):
    p = _write(tmp_path, """
        description: "gw -> go grpc"
        repos:
          gateway:
            path: /r/gw
            role: entrypoint
          order-svc:
            path: /r/order
            role: backend
        relations:
          - from: gateway
            to: order-svc
            protocol: grpc
        correlation:
          out_workspace: my-corr
    """)
    cfg = parse_multi_repo_config(p)
    assert cfg.repos["gateway"].role == "entrypoint"
    assert cfg.repos["order-svc"].role == "backend"  # default
    assert cfg.relations[0].from_ == "gateway"
    assert cfg.relations[0].protocol == "grpc"
    assert cfg.correlation.out_workspace == "my-corr"


def test_missing_entrypoint_rejected(tmp_path):
    p = _write(tmp_path, """
        repos:
          a: {path: /r/a, role: backend}
        relations: []
        correlation: {out_workspace: o}
    """)
    with pytest.raises(ValidationError) as ei:
        parse_multi_repo_config(p)
    assert "entrypoint" in str(ei.value).lower()


def test_relation_ref_undeclared_rejected(tmp_path):
    p = _write(tmp_path, """
        repos:
          gateway: {path: /r/gw, role: entrypoint}
        relations:
          - {from: gateway, to: missing-svc}
        correlation: {out_workspace: o}
    """)
    with pytest.raises(ValidationError):
        parse_multi_repo_config(p)


def test_repo_without_path_or_workspace_rejected(tmp_path):
    p = _write(tmp_path, """
        repos:
          gateway: {role: entrypoint}
        relations: []
        correlation: {out_workspace: o}
    """)
    with pytest.raises(ValidationError):
        parse_multi_repo_config(p)


def test_protocol_enum(tmp_path):
    p = _write(tmp_path, """
        repos:
          gateway: {path: /r/gw, role: entrypoint}
          b: {path: /r/b}
        relations:
          - {from: gateway, to: b, protocol: http}
        correlation: {out_workspace: o}
    """)
    cfg = parse_multi_repo_config(p)
    assert cfg.relations[0].protocol == "http"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mango/project/shannon-refactor/shannon-py && uv run pytest packages/core/tests/correlation/test_multi_repo_config.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.models.multi_repo_config`

- [ ] **Step 3: Write schema model**

`packages/core/src/shannon_core/models/multi_repo_config.py`:
```python
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RepoSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = None
    workspace: str | None = None
    role: str = "backend"
    scan_config: str | None = None
    proto_roots: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_role_and_inputs(self):
        if self.role not in ("entrypoint", "backend"):
            raise ValueError(f"role must be entrypoint|backend, got {self.role!r}")
        if self.path is None and self.workspace is None:
            raise ValueError("repo must have at least one of path or workspace")
        return self


class Relation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: str = Field(alias="from")
    to: str
    protocol: str = "grpc"

    @model_validator(mode="after")
    def _check_protocol(self):
        if self.protocol not in ("grpc", "http", "graphql"):
            raise ValueError(f"protocol must be grpc|http|graphql, got {self.protocol!r}")
        return self


class CorrelationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    out_workspace: str


class MultiRepoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str | None = None
    repos: dict[str, RepoSpec]
    relations: list[Relation] = Field(default_factory=list)
    correlation: CorrelationConfig

    @model_validator(mode="after")
    def _check_graph(self):
        # 至少一个 entrypoint
        if not any(r.role == "entrypoint" for r in self.repos.values()):
            raise ValueError("at least one repo must have role: entrypoint")
        # relations 引用必须已声明
        names = set(self.repos.keys())
        for rel in self.relations:
            if rel.from_ not in names:
                raise ValueError(f"relation from {rel.from_!r} not in repos")
            if rel.to not in names:
                raise ValueError(f"relation to {rel.to!r} not in repos")
        return self
```

- [ ] **Step 4: Add parser function**

在 `packages/core/src/shannon_core/config/parser.py` 末尾追加（照搬现有 `parse_config` 的 yaml.safe_load 模式，不要 sanitizer——multi-repo 无 authentication）：
```python
def parse_multi_repo_config(path: str | Path) -> "MultiRepoConfig":
    from shannon_core.models.multi_repo_config import MultiRepoConfig
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return MultiRepoConfig.model_validate(raw)
```
（若 parser.py 顶部已 `import yaml` 则复用；否则加 `import yaml`。`MultiRepoConfig` 用局部 import 避免循环。）

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/correlation/test_multi_repo_config.py -v`
Expected: PASS（5 passed）

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/models/multi_repo_config.py \
        packages/core/src/shannon_core/config/parser.py \
        packages/core/tests/correlation/__init__.py \
        packages/core/tests/correlation/test_multi_repo_config.py
git commit -m "feat(correlation): multi-repo.yaml 配置 schema + 校验(A1)"
```

---

### Task A2: 关联产物 schema

**Files:**
- Create: `packages/core/src/shannon_core/correlation/schemas.py`
- Create: `packages/core/src/shannon_core/correlation/__init__.py`
- Test: `packages/core/tests/correlation/test_schemas.py`

**Interfaces:**
- Consumes: 无（基础 schema）
- Produces（dataclass，序列化为 JSON）：
  - `CallSite(file: str, line: int, snippet: str)`
  - `Call(method: str, call_site: CallSite, confidence: str, evidence: str)`
  - `TopologyEdge(from_: str, to: str, protocol: str, calls: list[Call], status: str, error: str | None)`（`status ∈ {ok, low, unverified, error, declared-missing}`）
  - `ServiceNode(name: str, role: str, repo: str)`
  - `CrossServiceTopology(services: list[ServiceNode], edges: list[TopologyEdge])`
  - `TrustBoundary(service: str, method: str, exposure: str, reachable_from: list[str], reason: str, confidence: str)`
  - `CorrelationResult(topology: CrossServiceTopology, boundaries: list[TrustBoundary])`

- [ ] **Step 1: Write failing test — schema 序列化往返**

`packages/core/tests/correlation/test_schemas.py`:
```python
import json
from shannon_core.correlation.schemas import (
    CallSite, Call, TopologyEdge, ServiceNode, CrossServiceTopology,
    TrustBoundary, CorrelationResult,
)


def test_topology_serialization_roundtrip():
    topo = CrossServiceTopology(
        services=[ServiceNode(name="gateway", role="entrypoint", repo="/r/gw")],
        edges=[TopologyEdge(
            from_="gateway", to="order-svc", protocol="grpc",
            calls=[Call(method="order.v1.OrderService/CreateOrder",
                        call_site=CallSite(file="src/c.ts", line=42, snippet="client.createOrder(req)"),
                        confidence="high",
                        evidence="POST /orders handler calls CreateOrder")],
            status="ok", error=None,
        )],
    )
    data = json.loads(topo.to_json())
    assert data["services"][0]["role"] == "entrypoint"
    assert data["edges"][0]["calls"][0]["method"] == "order.v1.OrderService/CreateOrder"
    roundtrip = CrossServiceTopology.from_json(topo.to_json())
    assert roundtrip.edges[0].from_ == "gateway"


def test_boundary_serialization():
    b = TrustBoundary(service="order-svc", method="order.v1.OrderService/CreateOrder",
                      exposure="external", reachable_from=["gateway"],
                      reason="via POST /orders", confidence="high")
    data = json.loads(b.to_json())
    assert data["exposure"] == "external"


def test_edge_status_declared_missing():
    e = TopologyEdge(from_="gateway", to="ghost-svc", protocol="grpc",
                     calls=[], status="declared-missing", error=None)
    assert json.loads(e.to_json())["status"] == "declared-missing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/correlation/test_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: shannon_core.correlation.schemas`

- [ ] **Step 3: Write schema**

`packages/core/src/shannon_core/correlation/__init__.py`:（空文件）

`packages/core/src/shannon_core/correlation/schemas.py`:
```python
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict


def _s(o) -> dict:
    return asdict(o)


@dataclass
class CallSite:
    file: str
    line: int
    snippet: str


@dataclass
class Call:
    method: str
    call_site: CallSite
    confidence: str
    evidence: str


@dataclass
class TopologyEdge:
    from_: str
    to: str
    protocol: str
    calls: list[Call] = field(default_factory=list)
    status: str = "ok"            # ok | low | unverified | error | declared-missing
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(_s(self), ensure_ascii=False)

    @staticmethod
    def from_json(s: str) -> "TopologyEdge":
        d = json.loads(s)
        d["from_"] = d.pop("from")
        d["calls"] = [Call(method=c["method"],
                           call_site=CallSite(**c["call_site"]),
                           confidence=c["confidence"], evidence=c["evidence"]) for c in d["calls"]]
        return TopologyEdge(**d)


@dataclass
class ServiceNode:
    name: str
    role: str
    repo: str


@dataclass
class CrossServiceTopology:
    services: list[ServiceNode]
    edges: list[TopologyEdge]

    def to_json(self) -> str:
        return json.dumps({"services": [_s(s) for s in self.services],
                           "edges": [json.loads(e.to_json()) for e in self.edges]},
                          ensure_ascii=False)

    @staticmethod
    def from_json(s: str) -> "CrossServiceTopology":
        d = json.loads(s)
        return CrossServiceTopology(
            services=[ServiceNode(**s) for s in d["services"]],
            edges=[TopologyEdge.from_json(json.dumps(e)) for e in d["edges"]],
        )


@dataclass
class TrustBoundary:
    service: str
    method: str
    exposure: str
    reachable_from: list[str]
    reason: str
    confidence: str

    def to_json(self) -> str:
        return json.dumps(_s(self), ensure_ascii=False)


@dataclass
class CorrelationResult:
    topology: CrossServiceTopology
    boundaries: list[TrustBoundary]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/core/tests/correlation/test_schemas.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/correlation/__init__.py \
        packages/core/src/shannon_core/correlation/schemas.py \
        packages/core/tests/correlation/test_schemas.py
git commit -m "feat(correlation): 关联产物 schema(topology/boundary)(A2)"
```

---

### Task A3: 合并 queue（B1 四字段）+ 版本漂移检测（A2 时间戳）

**Files:**
- Create: `packages/core/src/shannon_core/correlation/queue_merge.py`
- Create: `packages/core/src/shannon_core/correlation/drift.py`
- Test: `packages/core/tests/correlation/test_queue_merge.py`
- Test: `packages/core/tests/correlation/test_drift.py`

**Interfaces:**
- Consumes: `packages/core/src/shannon_core/utils/paths.py` 的 `has_valid_whitebox_results`（`REQUIRED_VULN_FIELDS = {"title","description","severity","location"}`）
- Produces:
  - `merge_exploitation_queues(per_repo: dict[str, list[dict]]) -> list[dict]`：合并多仓 vuln entry，每条加 `service` + `cross_service_source`，**保留** title/description/severity/location
  - `detect_drift(workspace_created_at: float, repo_mtime: float) -> DriftReport`
  - `DriftReport(drifted: bool, note: str)`

- [ ] **Step 1: Write failing test — 合并保留四字段 + service 标注**

`packages/core/tests/correlation/test_queue_merge.py`:
```python
import json
from pathlib import Path
from shannon_core.correlation.queue_merge import merge_exploitation_queues
from shannon_core.utils.paths import has_valid_whitebox_results


def _entry(title="t", **kw):
    e = {"title": title, "description": "d", "severity": "high", "location": "f:1"}
    e.update(kw)
    return e


def test_merge_preserves_required_fields_and_adds_service(tmp_path):
    merged = merge_exploitation_queues({
        "gateway": [_entry("g1")],
        "order-svc": [_entry("o1", severity="medium")],
    })
    assert len(merged) == 2
    # 每条都有 service 标注
    services = {m["service"] for m in merged}
    assert services == {"gateway", "order-svc"}
    # B1 硬约束：合并产物仍通过黑盒 has_valid_whitebox_results
    queue_file = tmp_path / "injection_exploitation_queue.json"
    queue_file.write_text(json.dumps({"vulnerabilities": merged}), encoding="utf-8")
    assert has_valid_whitebox_results(queue_file) is True


def test_merge_drops_entries_missing_required_fields(tmp_path):
    bad = {"title": "x"}  # 缺 description/severity/location
    merged = merge_exploitation_queues({"order-svc": [bad, _entry("ok")]})
    # 缺字段的被丢弃，只留合法的
    assert len(merged) == 1
    assert merged[0]["title"] == "ok"
```

`packages/core/tests/correlation/test_drift.py`:
```python
from shannon_core.correlation.drift import detect_drift


def test_no_drift_when_repo_older():
    # workspace 创建晚于 repo 改动 → 无漂移
    r = detect_drift(workspace_created_at=2000.0, repo_mtime=1000.0)
    assert r.drifted is False


def test_drift_when_repo_newer():
    # repo 在 workspace 创建后改过 → 漂移
    r = detect_drift(workspace_created_at=1000.0, repo_mtime=2000.0)
    assert r.drifted is True
    assert "漂移" in r.note
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/correlation/test_queue_merge.py packages/core/tests/correlation/test_drift.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write queue_merge**

`packages/core/src/shannon_core/correlation/queue_merge.py`:
```python
from shannon_core.utils.paths import REQUIRED_VULN_FIELDS


def merge_exploitation_queues(per_repo: dict[str, list[dict]]) -> list[dict]:
    """合并 N 仓 exploitation_queue 的 vulnerabilities。

    B1 硬约束:每条 entry 必须含 title/description/severity/location
    (黑盒 has_valid_whitebox_results subset 检查);缺字段的丢弃。
    跨服务标注用额外 service 字段,不破坏检测。
    """
    merged: list[dict] = []
    for service, entries in per_repo.items():
        for e in entries:
            if not isinstance(e, dict):
                continue
            if not REQUIRED_VULN_FIELDS.issubset(e.keys()):
                continue
            tagged = dict(e)
            tagged["service"] = service
            tagged.setdefault("cross_service_source", None)
            merged.append(tagged)
    return merged
```

- [ ] **Step 4: Write drift**

`packages/core/src/shannon_core/correlation/drift.py`:
```python
from dataclasses import dataclass


@dataclass
class DriftReport:
    drifted: bool
    note: str


def detect_drift(workspace_created_at: float, repo_mtime: float) -> DriftReport:
    """A2: session.json 不存 git commit,用时间戳粗判。
    repo 最近改动 > workspace 创建 → 可能漂移。警告不阻断。
    """
    if repo_mtime > workspace_created_at:
        return DriftReport(drifted=True,
                           note="复用产物,源码版本可能漂移(repo 在扫描后改动),请人工确认")
    return DriftReport(drifted=False, note="")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/correlation/test_queue_merge.py packages/core/tests/correlation/test_drift.py -v`
Expected: PASS（4 passed）

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/correlation/queue_merge.py \
        packages/core/src/shannon_core/correlation/drift.py \
        packages/core/tests/correlation/test_queue_merge.py \
        packages/core/tests/correlation/test_drift.py
git commit -m "feat(correlation): 合并queue四字段约束 + 时间戳漂移检测(A3)"
```

---

### Task A4: cross-repo-correlation Agent 注册 + prompt 骨架

**Files:**
- Modify: `packages/core/src/shannon_core/models/agents.py`（加枚举 + AGENTS + AGENT_PHASE_MAP）
- Create: `prompts/cross-repo-correlation.txt`
- Create: `prompts/pipeline-testing/cross-repo-correlation.txt`

**Interfaces:**
- Consumes: 现有 `AgentDefinition` / `AGENTS` / `AGENT_PHASE_MAP`（`packages/core/src/shannon_core/models/agents.py`）；`AgentExecutor.execute(..., prompt_variables=...)` 注入点 `executor.py:64-65`
- Produces: `AgentName.CROSS_REPO_CORRELATION = "cross-repo-correlation"`；prompt 模板变量 `{{RELATIONS_JSON}}` / `{{ROLE_MAP}}` / `{{REPO_PATHS}}` / `{{DELIVERABLES_PATH}}`

**实现风险（执行者须验证）**：`AgentExecutor.execute` 流程含 git checkpoint/commit（executor.py 第 7 步），关联 workspace 非 git repo。执行者须确认 `repo_path` 传关联 workspace 路径时 git 步骤的行为（跳过/报错）；若报错，最小改动是给关联调用传一个已是 git repo 的路径（如 entrypoint repo path）或在 executor 增加跳过开关——此项在 Task A6 调用时落地，本 task 仅注册 + prompt。

- [ ] **Step 1: 注册 AgentName + AGENTS**

在 `packages/core/src/shannon_core/models/agents.py` 的 `AgentName` 枚举（`AUDIT_TIER1` 后）加：
```python
    CROSS_REPO_CORRELATION = "cross-repo-correlation"
```

在 `AGENTS` dict（`AUDIT_TIER1` 条目后）加：
```python
    AgentName.CROSS_REPO_CORRELATION: AgentDefinition(
        name=AgentName.CROSS_REPO_CORRELATION,
        display_name="Cross-Repo Correlation",
        prerequisites=[],  # 关联由编排器在外部触发,不在单仓流水线内
        prompt_template="cross-repo-correlation",
        deliverable_filename=None,  # 产物由编排器从 LLM 输出解析落盘
        model_tier="large",
    ),
```

在 `AGENT_PHASE_MAP` 加：
```python
    "cross-repo-correlation": "correlation",
```
（不加入 `BROWSER_SESSION_MAPPING`——关联 Agent 不用浏览器。）

- [ ] **Step 2: Write prompt（只用 read_file/grep/glob，§9）**

`prompts/cross-repo-correlation.txt`:
```
<role>
You are a Cross-Repository Correlation Analyst. You analyze how a microservice
stack communicates across repositories and infer cross-service attack surface.
You ONLY use read-only tools: read_file, grep, glob. Do NOT use Task tool or
browser tools (they are unavailable on some engines).
</role>

<objective>
Given N repositories (an entrypoint gateway + backends) and a declared service
graph, infer: (1) RPC/HTTP call sites in each `from` repo, (2) handler
implementations in each `to` repo, (3) trust boundaries from entrypoint
reachability, (4) candidate cross-service data flows, (5) any call edges you
discover that were NOT declared in the graph (declared-missing edges).
</objective>

<service-graph>
{{RELATIONS_JSON}}
</service-graph>

<roles>
{{ROLE_MAP}}
</roles>

<repos>
{{REPO_PATHS}}
</repos>

<deliverables-from-scans>
{{DELIVERABLES_PATH}}
</deliverables>

<output-format>
This invocation analyzes the SINGLE edge described in <service-graph> (its
from -> to). Respond with ONE JSON object (no prose outside it) for THAT edge:
{
  "from":"<from from graph>","to":"<to from graph>","protocol":"grpc",
  "calls":[{"method":"pkg.Svc/Method",
            "call_site":{"file":"...","line":N,"snippet":"..."},
            "confidence":"high|low","evidence":"..."}],
  "status":"ok|low|unverified|declared-missing",
  "boundaries":[{"service":"<to>","method":"...",
                 "exposure":"external|internal",
                 "reachable_from":["..."],"reason":"...","confidence":"high|low"}]
}
Rules:
- confidence high = call_site AND handler both located AND flow traceable;
  low = only single-side evidence.
- If you find a call to a service NOT equal to the graph's `to` (undeclared
  edge, spec B3), record it with status "declared-missing" and the real
  target in evidence.
- exposure external = the `to` service reachable from an entrypoint; else internal.
</output-format>
```

- [ ] **Step 3: Write pipeline-testing prompt（CI 简化）**

`prompts/pipeline-testing/cross-repo-correlation.txt`:
```
Correlation (pipeline-testing mode). Read the repos with read_file/grep/glob.
Output ONE JSON object:
{"edges":[{"from":"gateway","to":"order-svc","protocol":"grpc",
 "calls":[{"method":"order.Svc/M","call_site":{"file":"a","line":1,"snippet":"x"},
           "confidence":"low","evidence":"fixture"}],"status":"ok"}],
 "boundaries":[]}
```

- [ ] **Step 4: Verify registration loads**

Run: `uv run python -c "from shannon_core.models.agents import AgentName, AGENTS; d=AGENTS[AgentName.CROSS_REPO_CORRELATION]; print(d.display_name, d.model_tier)"`
Expected: 输出 `Cross-Repo Correlation large`（无 ImportError）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/models/agents.py \
        prompts/cross-repo-correlation.txt \
        prompts/pipeline-testing/cross-repo-correlation.txt
git commit -m "feat(correlation): 注册 cross-repo-correlation Agent + prompt(A4)"
```

---

### Task A5: multi 包脚手架 + 编排器骨架（配置→复用/现扫分支→收集配对）

**Files:**
- Create: `packages/multi/pyproject.toml`
- Create: `packages/multi/src/shannon_multi/__init__.py`
- Create: `packages/multi/src/shannon_multi/cli/__init__.py`
- Create: `packages/multi/src/shannon_multi/cli/main.py`
- Create: `packages/multi/src/shannon_multi/orchestrator.py`
- Modify: 根 `pyproject.toml`（dependencies + `[tool.uv.sources]`）
- Test: `packages/multi/tests/__init__.py` + `packages/multi/tests/test_orchestrator.py`

**Interfaces:**
- Consumes:
  - `shannon_whitebox.worker.run_scan(input: PipelineInput, temporal_address: str) -> dict`（`packages/whitebox/src/shannon_whitebox/worker.py:62`）
  - `shannon_whitebox.pipeline.shared.PipelineInput`（字段：`repo_path`/`workspace_name`/`config_path`/...）
  - `shannon_core.config.parser.parse_multi_repo_config`
  - `shannon_core.session.SessionManager(...).create_workspace(web_url, repo_path, name, *, scan_type)`
- Produces:
  - `plan_repo_scans(config: MultiRepoConfig) -> list[RepoScanPlan]`：纯函数，决定每个 repo 复用还是现扫（**可单测，不打 Temporal**）
  - `RepoScanPlan(service: str, repo_path: str | None, workspace: str | None, reuse: bool, scan_config: str | None)`

- [ ] **Step 1: Write failing test — 复用/现扫分支决策（纯函数）**

`packages/multi/tests/__init__.py`:（空）

`packages/multi/tests/test_orchestrator.py`:
```python
from shannon_core.models.multi_repo_config import MultiRepoConfig, RepoSpec, Relation, CorrelationConfig
from shannon_multi.orchestrator import plan_repo_scans, RepoScanPlan


def _cfg(**overrides):
    repos = {
        "gateway": RepoSpec(path="/r/gw", role="entrypoint"),
        "order-svc": RepoSpec(path="/r/order", workspace="existing-order", role="backend"),
        "payment-svc": RepoSpec(path="/r/pay", role="backend"),
    }
    return MultiRepoConfig(
        repos=repos,
        relations=[Relation(**{"from": "gateway", "to": "order-svc"})],
        correlation=CorrelationConfig(out_workspace="out"),
        **overrides,
    )


def test_reuse_when_workspace_declared():
    plans = plan_repo_scans(_cfg())
    by_svc = {p.service: p for p in plans}
    # order-svc 声明了 workspace → 复用
    assert by_svc["order-svc"].reuse is True
    assert by_svc["order-svc"].workspace == "existing-order"
    # gateway 只给 path → 现扫
    assert by_svc["gateway"].reuse is False
    assert by_svc["gateway"].repo_path == "/r/gw"


def test_all_three_repos_planned():
    plans = plan_repo_scans(_cfg())
    assert {p.service for p in plans} == {"gateway", "order-svc", "payment-svc"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/multi/tests/test_orchestrator.py -v`
Expected: FAIL — 包未安装 / import error

- [ ] **Step 3: Create package**

`packages/multi/pyproject.toml`（照抄 `packages/combined/pyproject.toml` 结构，改 name/scripts）:
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "shannon-multi"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "shannon-core",
    "shannon-whitebox",
]

[project.scripts]
shannon-multi = "shannon_multi.cli.main:main"

[tool.hatch.build.targets.wheel]
packages = ["src/shannon_multi"]
```

`packages/multi/src/shannon_multi/__init__.py`:（空）
`packages/multi/src/shannon_multi/cli/__init__.py`:（空）

在根 `pyproject.toml` 的 `dependencies` 加 `"shannon-multi"`，并在 `[tool.uv.sources]` 加：
```toml
shannon-multi = { workspace = true }
```
（照抄根 pyproject.toml 里 `shannon-combined` 的注册方式。）

- [ ] **Step 4: Install + write orchestrator skeleton**

Run: `uv sync`（让 workspace 识别新包）

`packages/multi/src/shannon_multi/orchestrator.py`:
```python
from __future__ import annotations
from dataclasses import dataclass
from shannon_core.models.multi_repo_config import MultiRepoConfig


@dataclass
class RepoScanPlan:
    service: str
    repo_path: str | None
    workspace: str | None
    reuse: bool
    scan_config: str | None


def plan_repo_scans(config: MultiRepoConfig) -> list[RepoScanPlan]:
    """纯函数:决定每个 repo 复用已有 workspace 还是现扫。
    复用条件:声明了 workspace(交付物完整性由编排器后续检查)。
    否则需要 path → 现扫。
    """
    plans: list[RepoScanPlan] = []
    for service, spec in config.repos.items():
        if spec.workspace:
            plans.append(RepoScanPlan(service=service, repo_path=spec.path,
                                      workspace=spec.workspace, reuse=True,
                                      scan_config=spec.scan_config))
        else:
            plans.append(RepoScanPlan(service=service, repo_path=spec.path,
                                      workspace=None, reuse=False,
                                      scan_config=spec.scan_config))
    return plans
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest packages/multi/tests/test_orchestrator.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: Write CLI entry (click)**

`packages/multi/src/shannon_multi/cli/main.py`:
```python
import asyncio
from pathlib import Path
import click
from shannon_multi.orchestrator import run_cross_repo


@click.group()
def cli():
    """Shannon cross-repo microservice correlation orchestrator."""


@cli.command()
@click.option("-c", "--config", "config_path", required=True,
              type=click.Path(exists=True, dir_okay=False),
              help="multi-repo.yaml path")
@click.option("--temporal-address", default="localhost:7233")
@click.option("--pipeline-testing", is_flag=True, help="Use minimal prompts (CI)")
def start(config_path, temporal_address, pipeline_testing):
    """Orchestrate multi-repo whitebox scans + cross-repo correlation."""
    result = asyncio.run(run_cross_repo(Path(config_path), temporal_address,
                                        pipeline_testing=pipeline_testing))
    click.echo(f"Correlation workspace: {result['out_workspace']}")


def main():
    cli()


if __name__ == "__main__":
    main()
```
（`run_cross_repo` 在 Task A6 实现；本步先让 CLI 可解析参数。`run_cross_repo` 先写一个抛 `NotImplementedError` 的桩，Task A6 填充——但为避免 lint 失败，本步在 orchestrator.py 加桩：）

在 `orchestrator.py` 追加：
```python
async def run_cross_repo(config_path: Path, temporal_address: str, *, pipeline_testing: bool = False) -> dict:
    raise NotImplementedError  # Task A6
```
（并在文件顶部加 `from pathlib import Path`）

- [ ] **Step 7: Verify CLI installs**

Run: `uv run shannon-multi --help`
Expected: 输出 help，含 `start` 子命令（无 traceback）

- [ ] **Step 8: Commit**

```bash
git add packages/multi/ pyproject.toml
git commit -m "feat(multi): multi 包脚手架 + 编排器复用/现扫分支(A5)"
```

---

### Task A6: 编排器关联执行（per-edge asyncio + 单边隔离 + 产物落盘）

**Files:**
- Modify: `packages/multi/src/shannon_multi/orchestrator.py`（实现 `run_cross_repo` + per-edge 并发 + 落盘）
- Create: `packages/core/src/shannon_core/correlation/report.py`
- Test: `packages/multi/tests/test_orchestrator.py`（加 per-edge 隔离测试）

**Interfaces:**
- Consumes:
  - Task A1 `parse_multi_repo_config` / A3 `merge_exploitation_queues` / `detect_drift` / A4 `AgentName.CROSS_REPO_CORRELATION`
  - `shannon_core.agents.executor.AgentExecutor` + `shannon_core.prompts.manager.PromptManager`
  - `shannon_core.session.SessionManager(...).create_workspace(..., scan_type="correlation")`
  - `shannon_core.correlation.schemas.CrossServiceTopology` / `TrustBoundary`
  - `shannon_core.utils.paths.deliverables_dir_for_workspace`
- Produces: `<out_workspace>/deliverables/` 下 `cross-service-topology.json` / `trust-boundaries.json` / `correlation-report.md` / `{vc}_exploitation_queue.json`（合并，B1 四字段）

**实现要点（per-edge 并发，B5）**：用 `asyncio.Semaphore(max_concurrent)` 限并发；每条 edge 一个 `AgentExecutor.execute` 调用（`prompt_variables` 注入该 edge 的 relations/role/repo paths）；单边 `try/except` 失败标 `status="error"`，其余继续（spec §8）。

- [ ] **Step 1: Write failing test — per-edge 隔离（单边失败不拖垮）**

在 `packages/multi/tests/test_orchestrator.py` 追加（mock 掉 LLM 调用，不真跑 Agent）:
```python
import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from shannon_multi.orchestrator import _run_edge, _merge_edge_results


def _edge_result(from_, to, status="ok"):
    return {"from": from_, "to": to, "protocol": "grpc",
            "calls": [], "status": status, "boundaries": []}


@pytest.mark.asyncio
async def test_single_edge_failure_does_not_break_others():
    edges = [("gateway", "order-svc"), ("gateway", "payment-svc"), ("gateway", "broken-svc")]

    async def fake_edge(f, t):
        if t == "broken-svc":
            raise RuntimeError("boom")
        return _edge_result(f, t)

    results = await asyncio.gather(*[_run_edge(f, t, runner=fake_edge) for f, t in edges],
                                   return_exceptions=False)
    statuses = {r["status"] for r in results}
    # 失败的边标 error,其余 ok,不抛
    assert "error" in statuses
    assert "ok" in statuses
    assert len(results) == 3


def test_merge_edges_collects_all():
    merged = _merge_edge_results([_edge_result("g", "a"), _edge_result("g", "b")])
    assert len(merged["edges"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/multi/tests/test_orchestrator.py -v`
Expected: FAIL — `_run_edge`/`_merge_edge_results` 未定义

- [ ] **Step 3: Write report落盘 helper**

`packages/core/src/shannon_core/correlation/report.py`:
```python
from __future__ import annotations
import json
from pathlib import Path
from shannon_core.correlation.schemas import CrossServiceTopology, TrustBoundary


def write_correlation_deliverables(
    out_deliverables: Path,
    topology: CrossServiceTopology,
    boundaries: list[TrustBoundary],
    merged_queues: dict[str, list[dict]],
    report_md: str,
) -> None:
    out_deliverables.mkdir(parents=True, exist_ok=True)
    (out_deliverables / "cross-service-topology.json").write_text(
        topology.to_json(), encoding="utf-8")
    (out_deliverables / "trust-boundaries.json").write_text(
        json.dumps([json.loads(b.to_json()) for b in boundaries], ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out_deliverables / "correlation-report.md").write_text(report_md, encoding="utf-8")
    for vc, entries in merged_queues.items():
        (out_deliverables / f"{vc}_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": entries}, ensure_ascii=False, indent=2),
            encoding="utf-8")
```

- [ ] **Step 4: Implement per-edge runner + merge in orchestrator**

在 `packages/multi/src/shannon_multi/orchestrator.py` 模块级追加 `_run_edge` + `_merge_edge_results`（供 Step 6 `run_cross_repo` 调用；`run_cross_repo` 桩的替换在 Step 6）:
```python
import asyncio
import json
from shannon_core.correlation.schemas import (
    CrossServiceTopology, ServiceNode, TopologyEdge, Call, CallSite, TrustBoundary,
)
from shannon_core.correlation.queue_merge import merge_exploitation_queues
from shannon_core.correlation.drift import detect_drift


async def _run_edge(from_svc: str, to_svc: str, *, runner) -> dict:
    """单条 edge 推断。runner 是 async(f,t)->dict(真实=AgentExecutor 调用)。
    失败 → 标 status=error,不抛(spec §8 单边隔离)。"""
    try:
        return await runner(from_svc, to_svc)
    except Exception as e:  # noqa: BLE001
        return {"from": from_svc, "to": to_svc, "protocol": "grpc",
                "calls": [], "status": "error", "error": str(e), "boundaries": []}


def _merge_edge_results(edge_results: list[dict]) -> dict:
    edges, boundaries = [], []
    for r in edge_results:
        edges.append({"from": r["from"], "to": r["to"], "protocol": r.get("protocol", "grpc"),
                      "calls": r.get("calls", []), "status": r.get("status", "ok"),
                      "error": r.get("error")})
        boundaries.extend(r.get("boundaries", []))
    return {"edges": edges, "boundaries": boundaries}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest packages/multi/tests/test_orchestrator.py -v`
Expected: PASS（4 passed：原 2 + 新 2）

- [ ] **Step 6: Implement run_cross_repo (编排主线)**

在 `orchestrator.py` 替换 `run_cross_repo` 桩为（含 N repo 白盒现扫 + per-edge 并发 + 落盘；复用分支调 `run_scan` 传 `workspace_name` 触发现白盒 resume）:
```python
async def run_cross_repo(config_path: Path, temporal_address: str, *, pipeline_testing: bool = False) -> dict:
    from shannon_core.config.parser import parse_multi_repo_config
    from shannon_core.session import SessionManager
    from shannon_core.utils.paths import (resolve_deliverables_path, deliverables_dir_for_workspace)
    from shannon_core.agents.executor import AgentExecutor
    from shannon_core.prompts.manager import PromptManager
    from shannon_core.models.agents import AgentName
    from shannon_core.correlation.report import write_correlation_deliverables
    from shannon_whitebox.worker import run_scan as run_whitebox
    from shannon_whitebox.pipeline.shared import PipelineInput

    config = parse_multi_repo_config(config_path)
    plans = plan_repo_scans(config)
    workspaces_root = Path("workspaces")

    # 1. N repo 白盒:复用 or 现扫
    per_repo_deliverables: dict[str, Path] = {}
    per_repo_queue: dict[str, list[dict]] = {}
    drift_warnings: list[str] = []
    for p in plans:
        if p.reuse:
            ws_path = workspaces_root / p.workspace
            # A2 版本漂移检测(时间戳粗判,仅复用且 repo path 已知时)
            if p.repo_path and (ws_path / "session.json").exists():
                import os
                sess = json.loads((ws_path / "session.json").read_text(encoding="utf-8"))
                rpt = detect_drift(sess.get("created_at", 0.0), os.path.getmtime(p.repo_path))
                if rpt.drifted:
                    drift_warnings.append(f"{p.service}: {rpt.note}")
        else:
            wb_input = PipelineInput(repo_path=p.repo_path, workspace_name=p.workspace,
                                     config_path=p.scan_config,
                                     pipeline_testing_mode=pipeline_testing)
            result = await run_whitebox(wb_input, temporal_address)
            ws_path = workspaces_root / result["workspace_name"]
        dlv = deliverables_dir_for_workspace(ws_path)
        per_repo_deliverables[p.service] = dlv
        # 收集该仓所有 exploitation_queue(spec §7 合并, B1)
        for q in dlv.glob("*_exploitation_queue.json"):
            vc = q.stem.replace("_exploitation_queue", "")
            try:
                entries = json.loads(q.read_text(encoding="utf-8")).get("vulnerabilities", [])
            except Exception:
                entries = []
            per_repo_queue.setdefault(vc, []).extend(
                [{"__service": p.service, **e} for e in entries])

    # 2. 关联 workspace
    mgr = SessionManager(workspaces_root)
    out_ws = mgr.create_workspace(web_url="", repo_path="",
                                  name=config.correlation.out_workspace,
                                  scan_type="correlation")
    out_dlv = deliverables_dir_for_workspace(out_ws)

    # 3. per-edge 关联 Agent(asyncio.Semaphore 限并发, B5)
    role_map = {s: spec.role for s, spec in config.repos.items()}
    repo_paths = {s: spec.path for s, spec in config.repos.items()}
    sem = asyncio.Semaphore(3)  # 并发上限:plan 用 3;接 max_concurrent 配置见风险登记(spec B5)
    edge_output_schema = {
        "type": "object",
        "properties": {
            "from": {"type": "string"}, "to": {"type": "string"},
            "protocol": {"type": "string"}, "status": {"type": "string"},
            "calls": {"type": "array"}, "boundaries": {"type": "array"},
        },
        "required": ["from", "to", "status"],
    }

    async def edge_runner(f: str, t: str) -> dict:
        async with sem:
            executor = AgentExecutor(PromptManager(Path("prompts")))
            prompt_vars = {
                "relations_json": json.dumps({"from": f, "to": t,
                    "protocol": next((r.protocol for r in config.relations
                                      if r.from_ == f and r.to == t), "grpc")}),
                "role_map": json.dumps(role_map),
                "repo_paths": json.dumps({f: repo_paths.get(f), t: repo_paths.get(t)}),
                "deliverables_path": str(out_dlv),
            }
            metrics = await executor.execute(
                agent_name=AgentName.CROSS_REPO_CORRELATION,
                repo_path=str(out_ws),  # 注:非 git repo,见 Task A4 风险
                deliverables_path=str(out_dlv),
                pipeline_testing=pipeline_testing,
                prompt_variables=prompt_vars,
                structured_output_schema=edge_output_schema,  # 强制单 edge JSON 输出
            )
            # 执行者确认(见风险登记):AgentExecutor.execute 返回 AgentMetrics,
            # 确认 LLM 结构化输出挂在 metrics 的哪个属性(如 metrics.output)。
            # 取不到合法 payload 则降级 unverified(spec §8 per-edge 隔离)。
            payload = getattr(metrics, "output", None)
            if isinstance(payload, dict) and "from" in payload:
                return payload
            return {"from": f, "to": t, "protocol": "grpc", "calls": [],
                    "status": "unverified", "boundaries": []}

    edge_pairs = [(r.from_, r.to) for r in config.relations]
    edge_results = await asyncio.gather(
        *[_run_edge(f, t, runner=edge_runner) for f, t in edge_pairs])
    merged = _merge_edge_results(edge_results)

    # 4. 组装 topology + boundaries
    topology = CrossServiceTopology(
        services=[ServiceNode(name=s, role=spec.role, repo=spec.path or spec.workspace or "")
                  for s, spec in config.repos.items()],
        edges=[TopologyEdge(from_=e["from"], to=e["to"], protocol=e["protocol"],
                            calls=[Call(method=c["method"],
                                        call_site=CallSite(**c["call_site"]),
                                        confidence=c["confidence"], evidence=c["evidence"])
                                   for c in e.get("calls", [])],
                            status=e["status"], error=e.get("error"))
               for e in merged["edges"]])
    boundaries = [TrustBoundary(**b) for b in merged["boundaries"]]

    # 5. 合并 queue(B1 四字段)+ 落盘
    merged_queues = {vc: merge_exploitation_queues(
        _group_by_service(entries)) for vc, entries in per_repo_queue.items()}
    report_md = _render_report(topology, boundaries, merged_queues, drift_warnings)
    write_correlation_deliverables(out_dlv, topology, boundaries, merged_queues, report_md)

    return {"out_workspace": config.correlation.out_workspace,
            "deliverables_path": str(out_dlv),
            "edge_statuses": [e["status"] for e in merged["edges"]]}


def _group_by_service(entries: list[dict]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = {}
    for e in entries:
        svc = e.pop("__service", "unknown")
        g.setdefault(svc, []).append(e)
    return g


def _render_report(topology, boundaries, merged_queues, drift_warnings) -> str:
    lines = ["# Cross-Repo Correlation Report", "",
             "## 服务拓扑", ""]
    for e in topology.edges:
        lines.append(f"- {e.from_} → {e.to} ({e.protocol}) [{e.status}]")
    lines += ["", "## 未验证/低置信/失败项(透明单列)", ""]
    for e in topology.edges:
        if e.status in ("low", "unverified", "error", "declared-missing"):
            lines.append(f"- {e.from_}→{e.to}: {e.status} {e.error or ''}")
    if drift_warnings:
        lines += ["", "## 版本漂移警告(A2)", ""]
        lines += [f"- {w}" for w in drift_warnings]
    return "\n".join(lines)
```

- [ ] **Step 7: Run full correlation test suite (no Temporal touched)**

Run: `uv run pytest packages/core/tests/correlation/ packages/multi/tests/ -v --ignore=packages/whitebox/tests --ignore=packages/blackbox/tests`
Expected: PASS（所有 Phase A 单测绿；不触 Temporal）

- [ ] **Step 8: 单仓零回归冒烟（手动）**

Run: `uv run shannon-whitebox start --repo <任一现有 repo> --pipeline-testing`（用一个已有 fixture repo）
Expected: 单仓白盒扫描行为不变（无 ImportError / 无新 trace），证明 Phase A 改动（agents.py 加枚举）未破坏单仓流水线。

- [ ] **Step 9: Commit**

```bash
git add packages/multi/src/shannon_multi/orchestrator.py \
        packages/core/src/shannon_core/correlation/report.py \
        packages/multi/tests/test_orchestrator.py
git commit -m "feat(multi): per-edge 并发关联执行 + 产物落盘关联 workspace(A6)"
```

---

## Phase B：黑盒 gateway 层关联验证（依赖 Phase A 的 topology）

### Task B1: `--correlated-workspace` flag 穿透 4 层

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/cli/main.py`（option + start 参数 + PipelineInput 赋值）
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/shared.py`（`BlackboxPipelineInput` + `BlackboxActivityInput` 加字段）
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`（act_input 传递）

**Interfaces:**
- Consumes: 现有 `BlackboxPipelineInput`（`shared.py:7-15`，dataclass 继承 `BasePipelineInput`）/ `BlackboxActivityInput`（`shared.py:33-45`）
- Produces: `correlated_workspace: str | None` 字段贯通 CLI → PipelineInput → ActivityInput → workflow

- [ ] **Step 1: Write failing test — flag 到 input 的传递**

`packages/multi/tests/test_blackbox_flag.py`（放 multi 测试里，避免碰黑盒挂起 suite）:
```python
from shannon_blackbox.pipeline.shared import BlackboxPipelineInput


def test_correlated_workspace_field_default_none():
    inp = BlackboxPipelineInput(web_url="http://x")
    assert inp.correlated_workspace is None


def test_correlated_workspace_field_set():
    inp = BlackboxPipelineInput(web_url="http://x", correlated_workspace="my-corr")
    assert inp.correlated_workspace == "my-corr"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/multi/tests/test_blackbox_flag.py -v`
Expected: FAIL — `correlated_workspace` 字段不存在

- [ ] **Step 3: Add field to both dataclasses**

`packages/blackbox/src/shannon_blackbox/pipeline/shared.py`：
- `BlackboxPipelineInput`（line 7-15）末尾加字段：
```python
    correlated_workspace: str | None = None
```
- `BlackboxActivityInput`（line 33-45）末尾加字段：
```python
    correlated_workspace: str | None = None
```

- [ ] **Step 4: Add CLI option + 参数 + 赋值**

`packages/blackbox/src/shannon_blackbox/cli/main.py`：
- 在 `start` 的 `@click.option` 列表（`--rerun` 后）加：
```python
@click.option("--correlated-workspace", default=None,
              help="Cross-repo correlation workspace (reuse topology for gateway-layer validation)")
```
- `def start(...)` 参数列表末尾加 `correlated_workspace`。
- `BlackboxPipelineInput(...)` 构造（main.py:89-100 附近）加：
```python
       correlated_workspace=correlated_workspace,
```

- [ ] **Step 5: Pass through workflow act_input**

`packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`（line 49-58 构建 `act_input` 处）加：
```python
       correlated_workspace=input.correlated_workspace,
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest packages/multi/tests/test_blackbox_flag.py -v`
Expected: PASS（2 passed）

- [ ] **Step 7: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/cli/main.py \
        packages/blackbox/src/shannon_blackbox/pipeline/shared.py \
        packages/blackbox/src/shannon_blackbox/pipeline/workflows.py \
        packages/multi/tests/test_blackbox_flag.py
git commit -m "feat(blackbox): --correlated-workspace flag 穿透 4 层(B1)"
```

---

### Task B2: 黑盒 workflow 检测关联 workspace deliverables + 跳过 recon

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`（correlated_workspace 指定时，读 topology/boundaries 作为上下文 + 跳过 from-scratch recon）

**Interfaces:**
- Consumes: `correlated_workspace`（Task B1）；`<corr_ws>/deliverables/cross-service-topology.json` / `trust-boundaries.json`（Phase A 产物）；`resolve_deliverables_path`
- Produces: workflow 在 `act_input.correlated_workspace` 非空时，把关联 workspace 的 topology/boundaries 读入 workflow state 供 exploitation 消费

- [ ] **Step 1: Write failing test — 检测关联 deliverables**

`packages/multi/tests/test_blackbox_reuse.py`:
```python
import json
from pathlib import Path
from shannon_blackbox.pipeline.workflows import _load_correlation_context


def test_load_correlation_context_when_files_exist(tmp_path):
    dlv = tmp_path / "deliverables"
    dlv.mkdir()
    (dlv / "cross-service-topology.json").write_text(
        json.dumps({"services": [], "edges": []}), encoding="utf-8")
    (dlv / "trust-boundaries.json").write_text("[]", encoding="utf-8")
    ctx = _load_correlation_context(tmp_path)
    assert ctx is not None
    assert ctx["topology"]["edges"] == []
    assert ctx["boundaries"] == []


def test_load_correlation_context_none_when_absent(tmp_path):
    assert _load_correlation_context(tmp_path) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/multi/tests/test_blackbox_reuse.py -v`
Expected: FAIL — `_load_correlation_context` 未定义

- [ ] **Step 3: Implement loader in workflows.py**

在 `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` 加纯函数（在 workflow 类外，可单测）:
```python
def _load_correlation_context(corr_workspace_path: Path) -> dict | None:
    """读关联 workspace 的 topology/boundaries 作为 exploitation 上下文。
    文件缺失返回 None(workflow 退回原逻辑)。"""
    import json
    dlv = corr_workspace_path / "deliverables"
    topo_f, bound_f = dlv / "cross-service-topology.json", dlv / "trust-boundaries.json"
    if not (topo_f.exists() and bound_f.exists()):
        return None
    return {
        "topology": json.loads(topo_f.read_text(encoding="utf-8")),
        "boundaries": json.loads(bound_f.read_text(encoding="utf-8")),
    }
```
在 `BlackboxScanWorkflow.run` 内，`has_whitebox_results` 检测段（workflows.py:132-153）**之前**加条件分支：
```python
        corr_ctx = None
        if input.correlated_workspace:
            corr_ctx = _load_correlation_context(Path("workspaces") / input.correlated_workspace)
        self._state.correlation_context = corr_ctx  # 供 exploitation 读取
```
（`correlation_context` 字段若 `BlackboxScanState` 无，在该 state dataclass 加 `correlation_context: dict | None = None`。）

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/multi/tests/test_blackbox_reuse.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/workflows.py \
        packages/multi/tests/test_blackbox_reuse.py
git commit -m "feat(blackbox): 检测关联 workspace topology 并加载为 exploitation 上下文(B2)"
```

---

### Task B3: exploit_executor 注入 topology/boundaries（spec A3）

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`（prompt_variables 加两项）

**Interfaces:**
- Consumes: `exploit_executor.execute`（已知 `exploit_executor.py:33-40` 注入 `vulnerability_entries` + `browser_session_id`）；`BlackboxActivityInput.correlated_workspace`（Task B1）；workflow state 的 `correlation_context`（Task B2）
- Produces: prompt 模板变量 `{{CROSS_SERVICE_TOPOLOGY}}` / `{{TRUST_BOUNDARIES}}`

**注**：`exploit_executor.execute` 当前签名只收 `deliverables_path`，不收 `correlated_workspace`/`correlation_context`。需在 `execute` 加可选参数 `correlation_context: dict | None = None`，调用方（activities.py 的 exploitation activity）从 act_input/state 传入。本 task 改 executor + 调用点。

- [ ] **Step 1: Write failing test — topology 注入 prompt_variables**

`packages/multi/tests/test_exploit_injection.py`:
```python
import inspect
from shannon_blackbox.agents.exploit_executor import ExploitExecutor


def test_execute_accepts_correlation_context():
    sig = inspect.signature(ExploitExecutor.execute)
    assert "correlation_context" in sig.parameters


def test_prompt_variables_include_topology(monkeypatch):
    captured = {}
    class FakeExecutor:
        async def execute(self, **kw):
            captured.update(kw.get("prompt_variables", {}))
            return None
    ex = ExploitExecutor(FakeExecutor())
    import asyncio
    asyncio.run(ex.execute(
        agent_name=None, vuln_type="injection", workspace_path=None,
        deliverables_path=None, web_url="http://x",
        correlation_context={"topology": {"edges": []}, "boundaries": []}))
    assert "cross_service_topology" in captured
    assert "trust_boundaries" in captured
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/multi/tests/test_exploit_injection.py -v`
Expected: FAIL — 无 `correlation_context` 参数 / 无 topology 注入

- [ ] **Step 3: Modify exploit_executor.execute**

`packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py`，`execute` 签名加参数 + prompt_variables 注入两项（spec A3：复用现有注入点，新增两项）:
```python
    async def execute(
        self,
        agent_name: AgentName,
        vuln_type: str,
        workspace_path: Path,
        deliverables_path: Path,
        web_url: str,
        config_path: str | None = None,
        api_key: str | None = None,
        pipeline_testing: bool = False,
        audit_logger: "ActivityLogger | None" = None,
        tool_audit_logger: "ToolAuditLogger | None" = None,
        correlation_context: dict | None = None,
    ) -> AgentMetrics:
        import json
        queue_path = deliverables_path / f"{vuln_type}_exploitation_queue.json"
        prompt_variables: dict[str, str] = {}
        if await async_path_exists(queue_path):
            content = await async_read_file(queue_path)
            prompt_variables["vulnerability_entries"] = content
        # spec A3: 注入跨服务 topology/boundaries(关联 workspace 提供)
        if correlation_context:
            prompt_variables["cross_service_topology"] = json.dumps(
                correlation_context.get("topology", {}), ensure_ascii=False)
            prompt_variables["trust_boundaries"] = json.dumps(
                correlation_context.get("boundaries", []), ensure_ascii=False)
        prompt_variables["browser_session_id"] = get_session_id(agent_name.value)
        return await self._executor.execute(
            agent_name=agent_name,
            repo_path=str(deliverables_path),
            web_url=web_url,
            deliverables_path=str(deliverables_path),
            config_path=config_path,
            api_key=api_key,
            pipeline_testing=pipeline_testing,
            prompt_variables=prompt_variables,
            audit_logger=audit_logger,
            tool_audit_logger=tool_audit_logger,
        )
```

- [ ] **Step 4: Wire correlation_context into the exploitation call site**

在调用 `ExploitExecutor.execute` 的 exploitation activity（`packages/blackbox/src/shannon_blackbox/pipeline/activities.py`）处，把 state 的 `correlation_context`（Task B2 存入）传入 `correlation_context=`。（执行者：grep `ExploitExecutor(` / `exploit_executor.execute` 定位调用点，加 `correlation_context=getattr(state, "correlation_context", None)`。）

- [ ] **Step 5: Add prompt placeholders to exploit prompt**

在 `prompts/injection-exploit.txt`（及其他 `*-exploit.txt`）的合适位置加（仅当 topology 非空时有意义，占位符不存在时 PromptManager 不替换也不报错）:
```
<cross-service-context>
{{CROSS_SERVICE_TOPOLOGY}}
{{TRUST_BOUNDARIES}}
</cross-service-context>
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest packages/multi/tests/test_exploit_injection.py -v`
Expected: PASS（2 passed）

- [ ] **Step 7: Run Phase B full test suite**

Run: `uv run pytest packages/multi/tests/ -v --ignore=packages/blackbox/tests`
Expected: PASS（Phase B 全部新增测试绿）

- [ ] **Step 8: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/agents/exploit_executor.py \
        packages/blackbox/src/shannon_blackbox/pipeline/activities.py \
        prompts/injection-exploit.txt \
        packages/multi/tests/test_exploit_injection.py
git commit -m "feat(blackbox): exploit_executor 注入 cross-service topology/boundaries(A3/B3)"
```

---

## 端到端冒烟（验收，spec §12）

完成 A1–A6 + B1–B3 后，人工冒烟（**非自动化 task**，memory 警告真机 Temporal/LLM 路径无自动测试）：

1. **白盒关联（Phase A）**：造 fixture（迷你 Node/TS gateway + 迷你 Go gRPC 后端，调用关系已知），写 `multi-repo.yaml`，跑 `uv run shannon-multi start -c multi-repo.yaml --pipeline-testing`，核对 `<out_workspace>/deliverables/` 下三个产物 + 合并 queue 结构符合 spec §7（关键边存在、entrypoint→backend exposure=external、合并 queue 四字段齐全）。
2. **glm-anthropic 引擎冒烟（spec §9）**：用 glm-anthropic profile 跑同一 fixture（去掉 `--pipeline-testing`），确认关联 Agent 的 read_file/grep/glob 工具调用正常、不依赖 Task tool。
3. **黑盒关联验证（Phase B）**：`uv run shannon-blackbox start --url <gateway-url> --correlated-workspace <out_workspace> --pipeline-testing`，确认 exploitation 上下文含 topology、在 gateway HTTP 层验证跨服务转发。
4. **单仓零回归（spec §12）**：跑现有单仓白盒 `--repo` + 黑盒单仓 `--repo`/`--latest` 各一次，行为不变。

## 风险登记（实现期关注）

| 风险 | 来源 | 对策 |
|---|---|---|
| `AgentExecutor` git checkpoint 对非 git repo（关联 workspace）报错 | Task A4/A6 | 执行者验证 executor.py git 步骤；必要时 repo_path 传 entrypoint repo 路径或加跳过开关 |
| 关联 Agent LLM 输出如何回流到编排器（structured_output vs 落盘 json） | Task A6 | 确认 AgentExecutor 是否支持 `structured_output_schema`；若支持用它解析 edge 结果，否则让 prompt 写中间 `_edge_*.json` |
| per-edge 并发上限与现有 max_concurrent 关系（spec B5） | Task A6 | `asyncio.Semaphore` 上限接配置（plan 内标 TODO） |
| 关联 workspace 被 `find_workspaces_by_url` 误匹配 | spec B2 | scan_type="correlation" 已隔离；冒烟确认黑盒 `--latest` 不误选关联 workspace |
| Phase B `correlation_context` 字段在 BlackboxScanState 是否存在 | Task B2 | 执行者确认 state dataclass，缺则加字段 |
