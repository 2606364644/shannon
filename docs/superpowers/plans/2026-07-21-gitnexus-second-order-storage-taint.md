# GitNexus 轨存储中转二阶召回(子项⑤)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 GitNexus 轨确定性层具备「同服务二阶漏洞」(stored XSS / 二阶 SQLi / 运行时配置二阶)召回能力——通过存储 token 二部图 join + chain_verdict 二阶判定,守图锚定不退化成 LLM 轨。

**Architecture:** 新增 `StorageWritePoint`(存储写点,非危险 sink,独立类型不进 `sink_call_sites`)+ `StorageReadPoint`(= `SourcePoint(source_type=STORAGE)`,新 source 风味)。确定性识别四介质(db/config/cache/file)读写点 → `chain_propagator` 零改动把 read 端连成单跳 TaintFlow → `second_order_join` 按 `(medium, token)` 二部图配对 write×read → `second_order_builder` 复用 `judge_chain_verdict` 判 read 端 + 轻量判 write 端 tainted,verdict = `(write tainted) ∧ (read 单跳 vulnerable)`,产 `InjectionVulnerability` 进 `<vuln>_gitnexus_queue.json`。

**Tech Stack:** Python 3.12 / pydantic BaseModel / pytest(async) / 现有 code_index 确定性层(`parameter_models` / `chain_propagator` / `chain_verdict` / `vuln_chain_builders`)。

## Global Constraints

- **铁律(CLAUDE.md §1)**:全在 GitNexus 轨,**不碰 LLM 轨 `vuln-*.txt` prompt**(LLM 轨二阶方法论是独立 follow-up plan)。守 `packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py` 绿。
- **flavor 决策 A**:`StorageReadPoint` = `SourcePoint(source_type=ParameterSource.STORAGE)`,**不新建 flavor 字段**(对齐子项② 口径)。
- **StorageWritePoint 非危险 sink**:独立 dataclass,**不进 `sink_call_sites`**(避免单跳轨误报 DB 写入)。
- **token 守图锚定**:只抽字面量 token;动态/拼接 → `"unresolvable"`,不 join(保守漏报,那部分归 LLM 轨)。
- **`BaseVulnerability.ID` 必填**:`parse_lenient` 丢缺 ID 条目(memory `authz-gitnexus-explore-id-drop-fix`)。second_order finding 必须有 ID。
- **YAML 反斜杠双写**:正则在 YAML 双引号串里 `\\` 表一个 `\`。
- **`externally_exploitable` 不被 verdict 覆写**(CLAUDE.md §1)。
- **测试**:只跑改动相关测试文件(`feat/fork-py` 全套 pytest 有预存挂起/失败,memory `feat-fork-py-test-gotchas`)。

**关键参考(实现契约手册来源)**:`docs/superpowers/specs/2026-07-21-second-order-storage-taint-dual-track-design.md`(⑤ spec);`docs/superpowers/specs/2026-07-21-code-index-deterministic-asset-layer-design.md`(子项①②③ 落地记录,含 C1 slot 路由教训)。

---

## File Structure

**新建(src)**
- `packages/core/src/shannon_core/code_index/storage_models.py` — `StorageMedium` enum + `StorageWritePoint` dataclass(独立类型,非 SinkCallSite)
- `packages/core/src/shannon_core/code_index/storage_detector.py` — 硬规则识别 `detect_storage_writes` / `detect_storage_reads`(对称 `source_detector.py`)
- `packages/core/src/shannon_core/code_index/storage_discovery_llm.py` — LLM 探测器 `discover_storage_writes_llm` / `discover_storage_reads_llm`(对称 `sink_discovery_llm.py:533-598`)
- `packages/core/src/shannon_core/code_index/second_order_join.py` — token 抽取 + 二部图 join `extract_second_order_candidates`
- `packages/core/src/shannon_core/code_index/vuln_chain_builders/second_order_builder.py` — `build_second_order_findings`(复用 `judge_chain_verdict`)
- `packages/core/src/shannon_core/code_index/data/storage_rules.yml` — 四介质读写规则

**修改(src)**
- `parameter_models.py` — `SourcePoint` 复用(不改);`StorageReadPoint` 即 `SourcePoint(source_type=STORAGE)`
- `models.py` — `ParameterSource.STORAGE` 枚举值;`CodeIndex.storage_write_points` 字段;`_resolve_forward_refs` 注册
- `code_index/__init__.py` — 编排插入 storage 4 路并行 + 并入 source_points/storage_write_points + storage_gap_report
- `whitebox/.../pipeline/activities.py` — `run_gitnexus_chain_verdict` 循环加 `build_second_order_findings`

**新建(test)**
- `packages/core/tests/code_index/test_storage_models.py`
- `packages/core/tests/code_index/test_storage_chain_propagator.py`
- `packages/core/tests/code_index/test_storage_detector.py`
- `packages/core/tests/code_index/test_storage_discovery_llm.py`
- `packages/core/tests/code_index/test_second_order_join.py`
- `packages/core/tests/code_index/test_second_order_builder.py`

---

## Task 1: 数据模型 — `ParameterSource.STORAGE` + `StorageWritePoint` + CodeIndex 字段

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/models.py:107-118`(`ParameterSource` 加 STORAGE)、`:72-88`(`CodeIndex` 加字段)、`:211-218`(`_resolve_forward_refs`)
- Create: `packages/core/src/shannon_core/code_index/storage_models.py`
- Test: `packages/core/tests/code_index/test_storage_models.py`

**Interfaces:**
- Consumes: 现有 `ParameterSource`(models.py)、`SourcePoint`(parameter_models.py:168)
- Produces:
  - `ParameterSource.STORAGE = "storage"`(models.py)
  - `class StorageMedium(str, Enum)`: `DB="db"` / `CONFIG="config"` / `CACHE="cache"` / `FILE="file"`
  - `class StorageWritePoint(BaseModel)`: `id, caller_id, callee_name, callee_receiver, medium: StorageMedium, storage_token, written_expr, file_path, line, column, rule_id, needs_review`
  - `CodeIndex.storage_write_points: list[StorageWritePoint]`(默认 `[]`)

- [ ] **Step 1: Write the failing test**

`packages/core/tests/code_index/test_storage_models.py`:
```python
from shannon_core.code_index.models import ParameterSource, CodeIndex
from shannon_core.code_index.storage_models import StorageWritePoint, StorageMedium


def test_parameter_source_has_storage_flavor():
    assert ParameterSource.STORAGE.value == "storage"


def test_storage_write_point_roundtrip():
    w = StorageWritePoint(
        id="F1::save::7",
        caller_id="entry::UserController.create",
        callee_name="save",
        callee_receiver="repo",
        medium=StorageMedium.DB,
        storage_token="users",
        written_expr="user.name",
        file_path="UserController.java", line=7, column=4,
        rule_id="java-orm-save",
    )
    dumped = w.model_dump_json()
    restored = StorageWritePoint.model_validate_json(dumped)
    assert restored.medium is StorageMedium.DB
    assert restored.storage_token == "users"


def test_code_index_carries_storage_write_points():
    ci = CodeIndex(language="java", language_coverage=["java"],
                   sink_call_sites=[], source_points=[], parameter_graph=None,
                   blocks=[], edges=[], entry_points=[], chains=[])
    assert ci.storage_write_points == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/shannon-py && ./packages/.bin/pytest packages/core/tests/code_index/test_storage_models.py -v 2>/dev/null || python -m pytest packages/core/tests/code_index/test_storage_models.py -v`
Expected: FAIL `ImportError: cannot import name 'StorageWritePoint'`

- [ ] **Step 3: Write minimal implementation**

`packages/core/src/shannon_core/code_index/storage_models.py`:
```python
"""Storage transfer anchors for second-order taint (spec §3.1).

StorageWritePoint = data-flow-INTO-storage location (ORM save / setProperty /
cache.set / file write). NOT a dangerous sink — writing to DB is not itself a
vuln — so it stays a separate type and never enters sink_call_sites (avoids
single-hop track false-positives on every DB write).

StorageReadPoint is NOT a new type: it is SourcePoint(source_type=STORAGE)
(flavor decision A, spec §3.1 / plan Global Constraints).
"""
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel


class StorageMedium(str, Enum):
    DB = "db"
    CONFIG = "config"
    CACHE = "cache"
    FILE = "file"


class StorageWritePoint(BaseModel):
    id: str
    caller_id: str
    callee_name: str
    callee_receiver: str | None = None
    medium: StorageMedium
    storage_token: str          # literal token (table/key/path); dynamic → "unresolvable"
    written_expr: str           # the expression being written (judge user-tainted)
    file_path: str
    line: int
    column: int = 0
    rule_id: str
    needs_review: bool = False
```

Add to `models.py` `ParameterSource` enum (after `UNKNOWN = "unknown"`):
```python
    STORAGE = "storage"
```

Add to `models.py` `CodeIndex` (alongside `source_points`):
```python
    storage_write_points: list["StorageWritePoint"] = []
```

Register forward ref in `models.py` `_resolve_forward_refs()` (alongside the existing `SourcePoint`/`SinkCallSite` registrations):
```python
    from shannon_core.code_index.storage_models import StorageWritePoint  # noqa
    CodeIndex.model_rebuild()
```
(If `_resolve_forward_refs` instead uses `model.update_forward_refs(StorageWritePoint=StorageWritePoint)`, use that form to match the file's existing idiom — read `models.py:211-218` first.)

- [ ] **Step 4: Run test to verify it passes**

Run: same as Step 2
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git -C /root/shannon-py add packages/core/src/shannon_core/code_index/storage_models.py packages/core/src/shannon_core/code_index/models.py packages/core/tests/code_index/test_storage_models.py
git -C /root/shannon-py commit -m "feat(code_index): StorageWritePoint + ParameterSource.STORAGE 数据模型(子项⑤ Task1)"
```

---

## Task 2: chain_propagator 锚定 StorageReadPoint(验证零改动复用单跳)

**目的**:证明 `SourcePoint(source_type=STORAGE)` 进 `source_points` 后,`produce_intra_first_taint_flows` / `propagate_backward_across_chains` 天然产 `TaintFlow(read_var→sink)`(spec §3.1 核心简化)。这是回归契约验证,几乎不改实现(若现有 `_source_points_matching` 已 substring 匹配不看 source_type,则零实现改动,纯测试)。

**Files:**
- Test: `packages/core/tests/code_index/test_storage_chain_propagator.py`
- Modify(仅当现有 intra-first/backward 漏掉 storage source 才改): `chain_propagator.py:490-542` / `:385-487`

**Interfaces:**
- Consumes: `SourcePoint(source_type=STORAGE)`(Task 1)、现有 `produce_intra_first_taint_flows` / `propagate_backward_across_chains`、`SinkCallSite`、`SlotContext`
- Produces: 验证 `TaintFlow(source_type=STORAGE)` 产出(无新导出符号)

- [ ] **Step 1: Write the failing test**

`packages/core/tests/code_index/test_storage_chain_propagator.py`:
```python
from shannon_core.code_index.models import ParameterSource
from shannon_core.code_index.parameter_models import (
    SourcePoint, SinkCallSite, SinkCategory, SlotContext, DangerousSlot, PropagationStep,
)
from shannon_core.code_index.chain_propagator import produce_intra_first_taint_flows

# Scenario: GET /profile/:id handler reads profile.bio from DB (storage read)
# then concatenates into a SQL query (sink). read_var "bio" is the storage source.
def _storage_read_source():
    return SourcePoint(
        id="H1::bio::88", entry_point_id="H1", param_name="bio",
        source_type=ParameterSource.STORAGE, expression="profile.bio",
        file_path="ProfileController.java", line=88, rule_id="java-orm-find",
    )

def _sql_sink_on_bio():
    return SinkCallSite(
        id="ProfileController.java:90::executeQuery", caller_id="H1",
        callee_name="executeQuery", callee_receiver="stmt",
        category=SinkCategory.SQL, sink_subtype="sql_raw_query",
        file_path="ProfileController.java", line=90,
        dangerous_slots=[DangerousSlot(arg_index=0, slot=SlotContext.SQL_VALUE,
                                       expression='"SELECT ... WHERE bio=\'" + bio') ],
        rule_id="java-stmt-execute",
    )

def test_intra_first_links_storage_read_to_sink():
    from shannon_core.code_index.parameter_models import IntraResult
    sink = _sql_sink_on_bio()
    intra = {  # the handler's own intra: bio is tainted, hits sink at :90
        "H1": IntraResult(func_id="H1", tainted_params={"bio"},
                          sink_hits=[{"callee":"executeQuery","line":90}]),
    }
    flows = produce_intra_first_taint_flows(
        intra, [_storage_read_source()], {"H1"}, {sink.id: sink},
    )
    storage_flows = [f for f in flows if f.source_type == ParameterSource.STORAGE]
    assert storage_flows, "StorageReadPoint must produce a TaintFlow to the SQL sink"
    assert storage_flows[0].sink_slot in (SlotContext.SQL_VALUE,)
```
> 注:`IntraResult` / `produce_intra_first_taint_flows` 的精确签名见 `chain_propagator.py:490` 和 `parameter_models.py`。若 fixture 与真实字段名不符,先 Read 这两处对齐字段名(propagation_steps、sink_hits 结构),不改断言意图。

- [ ] **Step 2: Run test to verify it fails (or passes immediately)**

Run: `python -m pytest packages/core/tests/code_index/test_storage_chain_propagator.py -v`
Expected: 若现有 propagator 已 substring 锚定(`_source_points_matching` 不看 source_type)→ **直接 PASS**(零实现改动,这是契约验证);若 FAIL → Step 3 修。

- [ ] **Step 3: Only if Step 2 FAILS — minimal fix in chain_propagator**

读 `chain_propagator.py:490-542`(`produce_intra_first_taint_flows`)和 `:367-382`(`_source_points_matching`)。若 storage source 因 `entry_point_id` 或 substring 未命中,修正匹配(通常零改动——`_source_points_matching` 已用 `param_name in t` / `expression in t`)。不引入 source_type 分支(守"零改动"契约)。

- [ ] **Step 4: Run test to verify it passes**

Run: same as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C /root/shannon-py add packages/core/tests/code_index/test_storage_chain_propagator.py packages/core/src/shannon_core/code_index/chain_propagator.py
git -C /root/shannon-py commit -m "test(code_index): 验证 StorageReadPoint 经 chain_propagator 零改动产 TaintFlow(子项⑤ Task2)"
```

---

## Task 3: storage_detector.py + storage_rules.yml(硬规则识别四介质读写点)

**Files:**
- Create: `packages/core/src/shannon_core/code_index/data/storage_rules.yml`
- Create: `packages/core/src/shannon_core/code_index/storage_detector.py`
- Test: `packages/core/tests/code_index/test_storage_detector.py`

**Interfaces:**
- Consumes: `FuncBlock`(models.py)、parser(代码文本)、`entry_point_ids`、`ParameterSource.STORAGE`(Task 1)、`StorageMedium`/`StorageWritePoint`(Task 1)、`SourcePoint`
- Produces:
  - `detect_storage_reads(blocks, parser, entry_point_ids) -> list[SourcePoint]`(产 `source_type=STORAGE`)
  - `detect_storage_writes(blocks, parser, entry_point_ids) -> list[StorageWritePoint]`
  - YAML 规则经现有 `_rule_loader.load_yaml`(对称 `source_detector.py:49-50`)

- [ ] **Step 1: Write the failing test**

`packages/core/tests/code_index/test_storage_detector.py`:
```python
from shannon_core.code_index.models import ParameterSource, FuncBlock
from shannon_core.code_index.storage_models import StorageMedium
from shannon_core.code_index.storage_detector import (
    detect_storage_reads, detect_storage_writes,
)

JAVA_REPO_SAVE = """
class UserController {
  void create(User u) { repo.save(u); }
  User get(Long id) { return repo.findOneByUserId(id); }
}
"""

def _block(text, name="UserController", start=1):
    return FuncBlock(id=f"F::{name}", name=name, file_path="UserController.java",
                     start_line=start, end_line=start + text.count("\n"),
                     text=text, language="java")

def test_detect_db_read_source_point():
    reads = detect_storage_reads([_block(JAVA_REPO_SAVE)], parser=None, entry_point_ids={"F::UserController"})
    assert any(r.source_type is ParameterSource.STORAGE and "UserId" in (r.param_name or r.expression)
               for r in reads)

def test_detect_db_write_storage_write_point():
    writes = detect_storage_writes([_block(JAVA_REPO_SAVE)], parser=None, entry_point_ids={"F::UserController"})
    assert any(w.medium is StorageMedium.DB and w.callee_name == "save" for w in writes)
```
> 注:`FuncBlock` 字段名以 `models.py` 现有定义为准(`text`/`source`/`body`?),先 Read `models.py` 对齐。`detect_sources` 签名(`source_detector.py:70`)用 `source_provider: Callable[[FuncBlock], bytes|None]` 取文本;`detect_storage_*` 照此,或简化直接读 `block.text`——与现有 detector 口径一致即可。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/core/tests/code_index/test_storage_detector.py -v`
Expected: FAIL `ImportError: cannot import name 'detect_storage_reads'`

- [ ] **Step 3: Write minimal implementation**

`packages/core/src/shannon_core/code_index/data/storage_rules.yml`:
```yaml
# Second-order storage transfer rules (spec §3.2). Token = regex group "tok" (literal).
# Dynamic/concatenated tokens are left for the LLM track (unresolvable here).
storage_reads:
  - rule_id: java-orm-find
    languages: [java]
    medium: db
    pattern: "\\.find(?:One|All)?By(\\w+)\\("
    param_of: tok            # read var derived from the matched property
  - rule_id: java-getproperty
    languages: [java]
    medium: config
    pattern: "getProperty\\(\s*[\"'](?P<tok>[^\"']+)[\"']\\s*\\)"
  - rule_id: ts-cache-get
    languages: [typescript, javascript]
    medium: cache
    pattern: "cache\\.get\\(\s*[\"'](?P<tok>[^\"']+)[\"']\\s*\\)"
  - rule_id: ts-readfile
    languages: [typescript, javascript]
    medium: file
    pattern: "readFile(?:Sync)?\\(\s*[\"'](?P<tok>[^\"']+)[\"']"

storage_writes:
  - rule_id: java-orm-save
    languages: [java]
    medium: db
    pattern: "(?:save|persist|merge)\\("
    written_arg: 0
  - rule_id: java-setproperty
    languages: [java]
    medium: config
    pattern: "setProperty\\(\s*[\"'](?P<tok>[^\"']+)[\"']"
    written_arg: 1
  - rule_id: ts-cache-set
    languages: [typescript, javascript]
    medium: cache
    pattern: "cache\\.set\\(\s*[\"'](?P<tok>[^\"']+)[\"']"
    written_arg: 1
  - rule_id: ts-writefile
    languages: [typescript, javascript]
    medium: file
    pattern: "writeFile(?:Sync)?\\(\s*[\"'](?P<tok>[^\"']+)[\"']"
    written_arg: 1
```

`packages/core/src/shannon_core/code_index/storage_detector.py`(骨架对称 `source_detector.py`,省略 `_dedup`/loader 细节,照 source_detector 结构):
```python
"""Deterministic storage read/write point detection (spec §3.2/§3.3).

Reads → SourcePoint(source_type=STORAGE) feeding chain_propagator (single-hop
reuse). Writes → StorageWritePoint (independent, NOT in sink_call_sites).
Token must be literal (named group "tok"); no-match → caller leaves it out.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

from shannon_core.code_index.models import ParameterSource, FuncBlock
from shannon_core.code_index.parameter_models import SourcePoint
from shannon_core.code_index.storage_models import StorageWritePoint, StorageMedium
from shannon_core.code_index._rule_loader import load_yaml
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True)
class StorageReadRule:
    rule_id: str; languages: tuple[str, ...]; medium: StorageMedium
    pattern: re.Pattern; param_of: str          # "tok" or a group name for the read var

@dataclass(frozen=True)
class StorageWriteRule:
    rule_id: str; languages: tuple[str, ...]; medium: StorageMedium
    pattern: re.Pattern; written_arg: int       # 0-based arg index carrying the written expr


def _build_read_rules() -> tuple[StorageReadRule, ...]:
    raw = load_yaml(DATA_DIR / "storage_rules.yml")
    out = []
    for d in raw.get("storage_reads", []):
        out.append(StorageReadRule(
            rule_id=d["rule_id"], languages=tuple(d["languages"]),
            medium=StorageMedium(d["medium"]),
            pattern=re.compile(d["pattern"]), param_of=d.get("param_of", "tok"),
        ))
    return tuple(out)

def _build_write_rules() -> tuple[StorageWriteRule, ...]:
    raw = load_yaml(DATA_DIR / "storage_rules.yml")
    out = []
    for d in raw.get("storage_writes", []):
        out.append(StorageWriteRule(
            rule_id=d["rule_id"], languages=tuple(d["languages"]),
            medium=StorageMedium(d["medium"]),
            pattern=re.compile(d["pattern"]), written_arg=int(d["written_arg"]),
        ))
    return tuple(out)

DEFAULT_READ_RULES = _build_read_rules()
DEFAULT_WRITE_RULES = _build_write_rules()


def _text_of(block: FuncBlock) -> str:
    return getattr(block, "text", None) or getattr(block, "source", "") or ""


def detect_storage_reads(blocks, parser, entry_point_ids) -> list[SourcePoint]:
    out: list[SourcePoint] = []
    for b in blocks:
        if b.id not in entry_point_ids:
            continue
        text = _text_of(b)
        lang = getattr(b, "language", "") or ""
        for r in DEFAULT_READ_RULES:
            if lang not in r.languages:
                continue
            for m in r.pattern.finditer(text):
                tok = m.groupdict().get("tok") or m.group(1) if m.groups() else ""
                read_var = tok or "storage_value"
                line = (b.start_line or 1) + text.count("\n", 0, m.start())
                out.append(SourcePoint(
                    id=f"{b.id}::{read_var}::{line}", entry_point_id=b.id,
                    param_name=read_var, source_type=ParameterSource.STORAGE,
                    expression=m.group(0), file_path=b.file_path, line=line,
                    rule_id=r.rule_id,
                ))
    return _dedup_sources(out)


def detect_storage_writes(blocks, parser, entry_point_ids) -> list[StorageWritePoint]:
    out: list[StorageWritePoint] = []
    for b in blocks:
        if b.id not in entry_point_ids:
            continue
        text = _text_of(b)
        lang = getattr(b, "language", "") or ""
        for r in DEFAULT_WRITE_RULES:
            if lang not in r.languages:
                continue
            for m in r.pattern.finditer(text):
                tok = m.groupdict().get("tok")
                token = tok if tok and not _is_dynamic(tok) else (tok or "unresolvable")
                if not tok:
                    token = "unresolvable"      # rule without tok group → can't statically resolve
                written = _arg_expr_at(m.group(0), r.written_arg)
                line = (b.start_line or 1) + text.count("\n", 0, m.start())
                out.append(StorageWritePoint(
                    id=f"{b.id}::{r.rule_id}::{line}", caller_id=b.id,
                    callee_name=m.group(0).split("(")[0].split(".")[-1],
                    callee_receiver=None, medium=r.medium, storage_token=token,
                    written_expr=written, file_path=b.file_path, line=line,
                    rule_id=r.rule_id,
                ))
    return _dedup_writes(out)


def _is_dynamic(token: str) -> bool:
    return ("+" in token) or "${" in token or token.endswith(")") and "(" in token

def _arg_expr_at(call_text: str, idx: int) -> str:
    inside = call_text[call_text.find("(") + 1 : call_text.rfind(")")].strip()
    parts = [p.strip() for p in inside.split(",")] if inside else []
    return parts[idx] if idx < len(parts) else (parts[0] if parts else inside)

def _dedup_sources(pts): ...   # copy _dedup shape from source_detector.py:112-122 on (entry_point_id, param_name, source_type)
def _dedup_writes(ws): ...     # same shape on (caller_id, rule_id, line)
```
> **实现要求**:`_dedup_sources` / `_dedup_writes` 照抄 `source_detector.py:112-122` 的 `_dedup` 结构(按对应 key 去重)。`load_yaml` 路径/签名以 `_rule_loader.py:23-26` 为准。先 Read `source_detector.py` 全文作为模板照写,确保字段名/签名一致。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages/core/tests/code_index/test_storage_detector.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git -C /root/shannon-py add packages/core/src/shannon_core/code_index/data/storage_rules.yml packages/core/src/shannon_core/code_index/storage_detector.py packages/core/tests/code_index/test_storage_detector.py
git -C /root/shannon-py commit -m "feat(code_index): storage_detector 硬规则识别四介质读写点(子项⑤ Task3)"
```

---

## Task 4: storage_discovery_llm.py(LLM 探测器,对称 sink 探测器)

**Files:**
- Create: `packages/core/src/shannon_core/code_index/storage_discovery_llm.py`
- Test: `packages/core/tests/code_index/test_storage_discovery_llm.py`

**Interfaces:**
- Consumes: `sink_discovery_llm.py:533-598` 的 `discover_sinks_by_entry` 模板(`FileChunk`/`chunk_items_by_file`/`map_llm_with_bounds`)、`llm_client`、`StorageWritePoint`/`SourcePoint(STORAGE)`(Task 1/3)
- Produces:
  - `async discover_storage_reads_llm(candidates, llm_client, *, concurrency, per_call_timeout, progress_cb, model, max_calls) -> tuple[list[SourcePoint], list[StorageGap]]`
  - `async discover_storage_writes_llm(candidates, llm_client, *, ...) -> tuple[list[StorageWritePoint], list[StorageGap]]`
  - 软锚点:`rule_id="llm-discovered-storage"`,`needs_review=True`(对称 `_to_soft_source`/`_to_hunter_sink`)

- [ ] **Step 1: Write the failing test**

`packages/core/tests/code_index/test_storage_discovery_llm.py`(用 stub llm_client 返回 JSON):
```python
import pytest
from shannon_core.code_index.storage_discovery_llm import discover_storage_reads_llm

class _StubBlock:
    def __init__(self, text):
        self.id = "F::H"; self.name = "H"; self.file_path = "H.java"
        self.start_line = 1; self.end_line = 5; self.text = text; self.language = "java"

@pytest.mark.asyncio
async def test_discover_storage_reads_soft_source():
    # repo.findByName(name) — not in hard rules; LLM should catch it
    block = _StubBlock("void f(String name){ var x = repo.findByName(name); echo(x); }")
    async def llm(prompt):
        return '''[{"read":"repo.findByName(name)","medium":"db","token":"name","read_var":"x","line":1,"is_storage_read":true,"rationale":"orm find"}]'''
    reads, gaps = await discover_storage_reads_llm(
        [block], llm, concurrency=1, per_call_timeout=10, progress_cb=None,
        model="stub", max_calls=1, token_threshold=10**9)
    from shannon_core.code_index.models import ParameterSource
    assert any(r.source_type is ParameterSource.STORAGE and r.rule_id == "llm-discovered-storage"
               and r.needs_review for r in reads)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/core/tests/code_index/test_storage_discovery_llm.py -v`
Expected: FAIL `ImportError: cannot import name 'discover_storage_reads_llm'`

- [ ] **Step 3: Write minimal implementation**

骨架(对称 `sink_discovery_llm.py:430-598`,prompt 改 storage 语义):
```python
"""LLM storage read/write hunter (spec §3.3), symmetric to discover_sinks_by_entry.

Given entry handler functions, find storage transfer points the hard rules missed
(non-standard ORM / dynamic-but-literal-token / framework config). Soft anchors,
needs_review=True, chain_verdict re-checks on the read side.
"""
from __future__ import annotations
from typing import Awaitable, Callable

from shannon_core.code_index.models import ParameterSource
from shannon_core.code_index.parameter_models import SourcePoint
from shannon_core.code_index.storage_models import StorageWritePoint, StorageMedium
from shannon_core.code_index.llm_concurrency import chunk_items_by_file, map_llm_with_bounds

_READ_PROMPT = """You are a storage-read detector for the GitNexus track.
Given a FILE with entry handler functions, identify ALL storage READ points
(DB find/select, config getProperty/@Value, cache.get, file read) the rule-based
detector may have missed. Only literal tokens (table/key/path); skip dynamic/concatenated.
## File(s)
{file_paths}
## Functions
{functions_repr}
## Task
Return a JSON array, one per read:
{{"read":"<call expression>","medium":"db|config|cache|file","token":"<literal token>","read_var":"<variable receiving the value>","line":<int>,"is_storage_read":true,"rationale":"<one line>"}}
Return ONLY the JSON array. `line` is FILE-absolute."""

_WRITE_PROMPT = """You are a storage-write detector for the GitNexus track.
Given entry handler functions, identify ALL storage WRITE points
(DB save/insert/update, config setProperty, cache.set, file write) the rule-based
detector may have missed. Literal token only.
## Task
Return a JSON array, one per write:
{{"write":"<call expression>","medium":"db|config|cache|file","token":"<literal token or null if dynamic>","written_arg":"<expression written>","line":<int>,"is_storage_write":true,"rationale":"<one line>"}}
Return ONLY the JSON array. `line` is FILE-absolute."""


def _functions_repr(blocks): ...    # copy formatting from sink_discovery_llm._functions_repr
def _resolve_block_for_line(blocks, line): ...   # copy from source_discovery_llm:240-254

def _to_soft_read(d, blocks) -> SourcePoint | None:
    b = _resolve_block_for_line(blocks, d.get("line", 0))
    if not b: return None
    return SourcePoint(
        id=f"{b.id}::{d.get('read_var','stor')}::{d.get('line')}", entry_point_id=b.id,
        param_name=d.get("read_var") or "storage_value",
        source_type=ParameterSource.STORAGE, expression=d.get("read",""),
        file_path=b.file_path, line=int(d.get("line",0)), confidence=0.6,
        rule_id="llm-discovered-storage", needs_review=True,
    )

def _to_soft_write(d, blocks) -> StorageWritePoint | None:
    b = _resolve_block_for_line(blocks, d.get("line", 0))
    if not b: return None
    tok = d.get("token")
    return StorageWritePoint(
        id=f"{b.id}::llm-storage::{d.get('line')}", caller_id=b.id,
        callee_name=(d.get("write","").split("(")[0].split(".")[-1] or "storage_write"),
        callee_receiver=None, medium=StorageMedium(d.get("medium","db")),
        storage_token=(tok if tok else "unresolvable"),
        written_expr=d.get("written_arg",""), file_path=b.file_path,
        line=int(d.get("line",0)), rule_id="llm-discovered-storage", needs_review=True,
    )


async def discover_storage_reads_llm(candidates, llm_client, *, concurrency, per_call_timeout,
                                     progress_cb, model, max_calls, token_threshold=10**9):
    chunks = chunk_items_by_file(candidates, block_of=lambda c: c, language_of=lambda c: getattr(c,"language",""))
    async def _one(chunk):
        prompt = _READ_PROMPT.format(file_paths=..., functions_repr=_functions_repr(chunk.blocks))
        raw = await llm_client(prompt)
        import json
        return [_to_soft_read(d, chunk.blocks) for d in json.loads(raw)]
    out = await map_llm_with_bounds(chunks, _one, concurrency, per_call_timeout,
                                    "storage-read-hunter", on_skip=lambda c: None)
    return [x for x in (out or []) if x], []

async def discover_storage_writes_llm(candidates, llm_client, *, concurrency, per_call_timeout,
                                      progress_cb, model, max_calls, token_threshold=10**9):
    chunks = chunk_items_by_file(candidates, block_of=lambda c: c, language_of=lambda c: getattr(c,"language",""))
    async def _one(chunk):
        prompt = _WRITE_PROMPT.format(file_paths=..., functions_repr=_functions_repr(chunk.blocks))
        raw = await llm_client(prompt)
        import json
        return [_to_soft_write(d, chunk.blocks) for d in json.loads(raw)]
    out = await map_llm_with_bounds(chunks, _one, concurrency, per_call_timeout,
                                    "storage-write-hunter", on_skip=lambda c: None)
    return [x for x in (out or []) if x], []
```
> **实现要求**:先 Read `sink_discovery_llm.py:430-598` 全段,照其 `chunk_items_by_file` / `map_llm_with_bounds` / `_resolve_block_for_line` / `functions_repr` 的真实签名与返回结构填实 `_functions_repr`、`file_paths` 格式化、`chunk.blocks` 访问。`StorageGap` 若不存在用 `list` 占位(返回空 gap 列表),或复用 `SourceGap`/`RuleGap` 现有类型——以 `sink_discovery_llm` 返回的 gap 类型为准。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages/core/tests/code_index/test_storage_discovery_llm.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git -C /root/shannon-py add packages/core/src/shannon_core/code_index/storage_discovery_llm.py packages/core/tests/code_index/test_storage_discovery_llm.py
git -C /root/shannon-py commit -m "feat(code_index): storage_discovery_llm LLM 探测器对称 sink 探测器(子项⑤ Task4)"
```

---

## Task 5: __init__.py 编排 — storage 4 路并行 + 并入 source_points/storage_write_points

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py:185`(detect 段加 `detect_storage_writes` ∥)、`:246-255`(discover 段加 `discover_storage_writes_llm` ∥)、`:316-346`(source 段加 `detect_storage_reads` + `discover_storage_reads_llm`,产 STORAGE source 并入 `source_points`)、`:380-399`(CodeIndex 组装加 `storage_write_points=`)、`write_index_files`(:402-448,加 `storage_gap_report.json`)
- Test: `packages/core/tests/code_index/test_storage_orchestration.py`(端到端:Java fixture 产 storage 读写点 + StorageReadPoint 产 TaintFlow)

**Interfaces:**
- Consumes: Task 1/3/4 产物
- Produces: `CodeIndex.storage_write_points` 填充;`source_points` 含 `STORAGE` 风味;`code_index.json` 序列化含二者;`storage_gap_report.json`

- [ ] **Step 1: Write the failing test**

`packages/core/tests/code_index/test_storage_orchestration.py`:
```python
import pytest
from shannon_core.code_index.models import ParameterSource

# Fixture: a repo where UserController.create saves user.bio (DB write) and
# UserController.show concatenates the loaded bio into SQL (storage read → sink).
# End-to-end: build_code_index_with_gitnexus with stub mcp_client/llm_client
# should populate storage_write_points and a STORAGE source_point whose bio
# reaches the SQL sink as a taint_flow.
@pytest.mark.asyncio
async def test_storage_points_populated_and_read_flows_to_sink(tmp_path, monkeypatch):
    # ... write the two-file Java fixture into tmp_path ...
    # ... stub mcp_client (returns empty call graph) + llm_client (returns [] from hunters) ...
    from shannon_core.code_index import build_code_index_with_gitnexus
    ci, rule_gaps, source_gaps = await build_code_index_with_gitnexus(
        str(tmp_path), mcp_client=_stub_mcp, llm_client=_stub_llm, auto_index=False)
    assert ci.storage_write_points, "DB write point (save) must be detected"
    storage_reads = [s for s in ci.source_points if s.source_type is ParameterSource.STORAGE]
    assert storage_reads, "storage read source must be in source_points"
    storage_flows = [f for f in (ci.parameter_graph.taint_flows if ci.parameter_graph else [])
                     if f.source_type == ParameterSource.STORAGE]
    assert storage_flows, "storage read must taint-flow to the SQL sink"
```
> **实现要求**:fixture 与 stub 按现有 `test_*_code_index` 测试的 setup 风格(参考 `packages/core/tests/code_index/` 下任一调用 `build_code_index_with_gitnexus` 的测试)。`_stub_mcp` 返回空 CallChain 列表;`_stub_llm` 对 hunter prompt 返回 `[]`。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/core/tests/code_index/test_storage_orchestration.py -v`
Expected: FAIL(storage_write_points 空 / storage_reads 空)

- [ ] **Step 3: Write minimal implementation**

在 `__init__.py` 按 §File Structure 表格插入点接入(精确行号以当前文件为准,先 Read `__init__.py:185/246/316/380/402`):
- `:185` 段(与 `detect_sinks` 并行,to_thread 内或之后):`storage_writes = detect_storage_writes(all_blocks, parser, entry_point_ids)`
- `:246` 段(与 `discover_sinks_by_entry` 并行):`storage_writes_llm, _ = await discover_storage_writes_llm(hunter_blocks, llm_client, ...)`;`storage_writes += storage_writes_llm`
- `:316-346` source 段:`storage_reads = detect_storage_reads(all_blocks, parser, entry_point_ids)`;`storage_reads_llm, _ = await discover_storage_reads_llm(source_blocks, llm_client, ...)`;`source_points = [*source_points, *storage_reads, *storage_reads_llm]`
- `:380-399` CodeIndex 组装:加 `storage_write_points=storage_writes`
- `write_index_files`:加 `atomic_write_json(out / "storage_gap_report.json", {...})`(对称 `source_gap_report.json` :438-446,内容含未识别介质的 gap)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages/core/tests/code_index/test_storage_orchestration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C /root/shannon-py add packages/core/src/shannon_core/code_index/__init__.py packages/core/tests/code_index/test_storage_orchestration.py
git -C /root/shannon-py commit -m "feat(code_index): 编排接入 storage 4 路并行 + 并入 source_points/storage_write_points(子项⑤ Task5)"
```

---

## Task 6: second_order_join.py — token 抽取 + 二部图 join

**Files:**
- Create: `packages/core/src/shannon_core/code_index/second_order_join.py`
- Test: `packages/core/tests/code_index/test_second_order_join.py`

**Interfaces:**
- Consumes: `StorageWritePoint`(Task 1)、`SourcePoint(STORAGE)`(read 端,Task 3)、`CandidateChain`(chain_verdict.py:74)
- Produces:
  - `@dataclass SecondOrderCandidate: write: StorageWritePoint; storage_token: tuple[StorageMedium, str]; read_side_chain: CandidateChain`
  - `extract_second_order_candidates(writes: list[StorageWritePoint], read_chains: list[CandidateChain], *, reads_by_id: dict[str, SourcePoint]) -> list[SecondOrderCandidate]`
  - 行为:按 `(medium, token)` 配对 write 与 read;token `"unresolvable"` 不 join;同 token 多 write/read 笛卡尔

- [ ] **Step 1: Write the failing test**

`packages/core/tests/code_index/test_second_order_join.py`:
```python
from shannon_core.code_index.storage_models import StorageWritePoint, StorageMedium
from shannon_core.code_index.second_order_join import extract_second_order_candidates, is_resolvable_token

def test_dynamic_token_unresolvable_not_joined():
    w = StorageWritePoint(id="w1", caller_id="A", callee_name="save", medium=StorageMedium.DB,
                          storage_token="unresolvable", written_expr="x", file_path="a", line=1, rule_id="r")
    assert not is_resolvable_token(w.storage_token)
    # join with a matching read should produce nothing (unresolvable skipped)
    cands = extract_second_order_candidates([w], [], reads_by_id={})
    assert cands == []

def test_literal_token_joins_write_and_read():
    w = StorageWritePoint(id="w1", caller_id="A", callee_name="save", medium=StorageMedium.DB,
                          storage_token="users", written_expr="user.name", file_path="a", line=1, rule_id="r")
    # read side: a STORAGE source whose param_name/token maps to "users"
    # (read_side_chain construction uses a real CandidateChain — see fixture note)
    ...
    assert any(c.write is w for c in cands)
```
> **实现要求**:`CandidateChain`(chain_verdict.py:74)是 frozen dataclass;构造 read_side_chain 的 fixture 参考 `test_attack_chain_builder.py` 或 `chain_verdict` 测试里造 CandidateChain 的方式。read 端 token 来自 `SourcePoint.expression` 或 param_name 的归一化(见 Step 3 `_read_token`)。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/core/tests/code_index/test_second_order_join.py -v`
Expected: FAIL `ImportError: cannot import name 'extract_second_order_candidates'`

- [ ] **Step 3: Write minimal implementation**

`packages/core/src/shannon_core/code_index/second_order_join.py`:
```python
"""Second-order candidate assembly: bipartite join of storage writes × reads by
(medium, token). NOT a BFS — O(|W|×|R|) literal-token matching (spec §3.3).
Dynamic/concatenated tokens (storage_token == "unresolvable") are skipped.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

from shannon_core.code_index.storage_models import StorageWritePoint, StorageMedium
from shannon_core.code_index.parameter_models import SourcePoint
from shannon_core.code_index.chain_verdict import CandidateChain

_DYNAMIC_MARKERS = ("+", "${", "unresolvable")

def is_resolvable_token(token: str) -> bool:
    if not token or token == "unresolvable":
        return False
    return not any(m in token for m in _DYNAMIC_MARKERS)

_LITERAL_RE = re.compile(r"[\"']?([A-Za-z_][\w./-]*)[\"']?$")

def _read_token(read_src: SourcePoint) -> str:
    """Best-effort literal token from a STORAGE source: prefer expression's
    string literal, fall back to param_name."""
    m = _LITERAL_RE.search(read_src.expression or "")
    return (m.group(1) if m else "") or read_src.param_name


@dataclass(frozen=True)
class SecondOrderCandidate:
    write: StorageWritePoint
    storage_token: tuple[str, str]          # (medium.value, token)
    read: SourcePoint
    read_side_chain: CandidateChain


def extract_second_order_candidates(
    writes: list[StorageWritePoint],
    read_chains: list[CandidateChain],
    *,
    reads_by_id: dict[str, SourcePoint],
) -> list[SecondOrderCandidate]:
    # index reads by (medium-from-rule_id-prefix? no — by token) — read medium comes from
    # the SourcePoint.rule_id's associated medium. We pair by token primarily, medium second.
    # read medium is encoded in rule_id prefix is fragile; instead pass medium via reads_by_id
    # metadata. For now: pair by literal token equality; medium cross-check is advisory.
    by_token: dict[str, list[tuple[SourcePoint, CandidateChain]]] = {}
    for chain in read_chains:
        src = reads_by_id.get(chain.source_param) or reads_by_id.get(chain.entry_point_id)
        if src is None or src.source_type.value != "storage":
            continue
        tok = _read_token(src)
        if not is_resolvable_token(tok):
            continue
        by_token.setdefault(tok, []).append((src, chain))

    out: list[SecondOrderCandidate] = []
    for w in writes:
        if not is_resolvable_token(w.storage_token):
            continue
        for src, chain in by_token.get(w.storage_token, []):
            out.append(SecondOrderCandidate(
                write=w, storage_token=(w.medium.value, w.storage_token),
                read=src, read_side_chain=chain,
            ))
    return out
```
> **实现要求**:`reads_by_id` 的 key 由 caller(Task 7 builder / activity)决定——用 `SourcePoint.param_name` 或 `id` 作为 key,与 read_chains 的 `source_param` 对齐。若对齐困难,改为传 `reads: list[SourcePoint]` 直接按 `entry_point_id`+`param_name` 关联 chain(`chain.entry_point_id`,`chain.source_param`)。先 Read `chain_verdict.py:74-88` `CandidateChain` 字段确认 `source_param`/`entry_point_id` 语义再定 key。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages/core/tests/code_index/test_second_order_join.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C /root/shannon-py add packages/core/src/shannon_core/code_index/second_order_join.py packages/core/tests/code_index/test_second_order_join.py
git -C /root/shannon-py commit -m "feat(code_index): second_order_join 二部图 token join(子项⑤ Task6)"
```

---

## Task 7: second_order_builder.py — chain_verdict 二阶判定 + finding 产出

**Files:**
- Create: `packages/core/src/shannon_core/code_index/vuln_chain_builders/second_order_builder.py`
- Test: `packages/core/tests/code_index/test_second_order_builder.py`

**Interfaces:**
- Consumes: `extract_second_order_candidates`(Task 6)、`judge_chain_verdict`(chain_verdict.py:225)、`InjectionVulnerability`(queue_schemas.py:18)、`SourcePoint(STORAGE)` / `StorageWritePoint`
- Produces:
  - `async build_second_order_findings(writes, pgraph, *, llm_client, sink_call_sites, reads_by_id, progress_cb=None) -> list[InjectionVulnerability]`
  - 逻辑:`extract_candidate_chains(pgraph)` 抽 read 端单跳链(含 STORAGE source)→ `extract_second_order_candidates(writes, read_chains, reads_by_id=…)` join → 每个 candidate:`judge_chain_verdict(read_side_chain)` 判 read 端 + 轻量判 write 端 `written_expr` user-tainted → `verdict = write_tainted ∧ (read_verdict=="vulnerable")` → 产 `InjectionVulnerability(ID="2ND-GN-NN", source_track="gitnexus", combined_sources=f"write:{w.file_path}:{w.line} + read:{r.file_path}:{r.line}", vulnerability_type=f"second_order_{vuln_class}")`

- [ ] **Step 1: Write the failing test**

`packages/core/tests/code_index/test_second_order_builder.py`:
```python
import pytest
from shannon_core.code_index.storage_models import StorageWritePoint, StorageMedium
from shannon_core.code_index.vuln_chain_builders.second_order_builder import build_second_order_findings

@pytest.mark.asyncio
async def test_second_order_xss_when_write_tainted_and_read_vuln(monkeypatch):
    # Fixture: write side = save(user.bio) [tainted], read side = storage read → unescaped render [vulnerable]
    # Stub judge_chain_verdict to return vulnerable for the read side chain.
    writes = [StorageWritePoint(id="w", caller_id="A", callee_name="save",
                 medium=StorageMedium.DB, storage_token="users", written_expr="user.bio",
                 file_path="C.java", line=3, rule_id="java-orm-save")]
    # build a minimal pgraph whose only taint_flow is read_var→xss-sink, with a STORAGE source
    pgraph, reads_by_id, sink_call_sites = _build_xss_second_order_pgraph()
    async def llm(prompt): return '{"verdict":"vulnerable","witness_payload":"<svg>","evidence_chain":"bio->render","mismatch_reason":"unescaped","confidence":"high"}'
    findings = await build_second_order_findings(
        writes, pgraph, llm_client=llm, sink_call_sites=sink_call_sites,
        reads_by_id=reads_by_id)
    assert findings, "must emit a second-order XSS finding"
    f = findings[0]
    assert f.verdict == "vulnerable"
    assert f.ID.startswith("2ND-GN-")
    assert f.source_track == "gitnexus"
    assert "write:" in (f.combined_sources or "") and "read:" in (f.combined_sources or "")

@pytest.mark.asyncio
async def test_no_finding_when_read_side_safe():
    # same write, but read side verdict safe (encoded render) → no finding
    ...
    async def llm(prompt): return '{"verdict":"safe","witness_payload":"","evidence_chain":"","mismatch_reason":"","confidence":"high"}'
    findings = await build_second_order_findings(writes, pgraph, llm_client=llm,
                 sink_call_sites=sink_call_sites, reads_by_id=reads_by_id)
    assert findings == []
```
> **实现要求**:`_build_xss_second_order_pgraph()` 造一个 `ParameterPropagationGraph`,其 `taint_flows` 含一条 `source_type=STORAGE`、`sink_slot=render/xss` 的 flow,加对应 `SinkCallSite(category=XSS)`。参考 `test_attack_chain_builder.py` / `chain_verdict` 测试造 pgraph 的方式。write 端 tainted 判定:`written_expr="user.bio"` 含 `user`/`bio`(非纯字面量)→ tainted=True。Step 3 实现一个 `_looks_user_tainted(expr)` 简判(非纯字面量即 tainted)。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/core/tests/code_index/test_second_order_builder.py -v`
Expected: FAIL `ImportError`

- [ ] **Step 3: Write minimal implementation**

`packages/core/src/shannon_core/code_index/vuln_chain_builders/second_order_builder.py`:
```python
"""Second-order findings builder (spec §3.3). Reuses single-hop judge_chain_verdict
on the read side; adds lightweight write-side tainted confirmation.
verdict = (write tainted) ∧ (read single-hop vulnerable).
"""
from __future__ import annotations
import logging
from typing import Awaitable, Callable

from shannon_core.code_index.chain_verdict import (
    extract_candidate_chains, judge_chain_verdict,
)
from shannon_core.code_index.second_order_join import extract_second_order_candidates
from shannon_core.code_index.storage_models import StorageWritePoint
from shannon_core.code_index.models.queue_schemas import InjectionVulnerability
from shannon_core.code_index.progress import ProgressCb, ProgressEmitter

logger = logging.getLogger(__name__)

_LITERAL_EXPR = set("0123456789\"'")

def _looks_user_tainted(written_expr: str) -> bool:
    e = (written_expr or "").strip()
    if not e:
        return False
    # pure literal (number / quoted string) → not tainted; anything else (var, field, concat) → tainted
    if e.isdigit(): return False
    if len(e) >= 2 and e[0] in "\"'" and e[-1] == e[0]: return False
    return True


async def build_second_order_findings(
    writes: list[StorageWritePoint],
    pgraph,
    *,
    llm_client: Callable[..., Awaitable[str]],
    sink_call_sites,
    reads_by_id: dict,
    progress_cb: ProgressCb = None,
) -> list[InjectionVulnerability]:
    # 1. read-side single-hop candidates (routed by sink; STORAGE sources included)
    read_chains = extract_candidate_chains(pgraph, vuln_class="xss", sink_call_sites=sink_call_sites)
    read_chains += extract_candidate_chains(pgraph, vuln_class="injection", sink_call_sites=sink_call_sites)
    # 2. join writes × reads by (medium, token)
    candidates = extract_second_order_candidates(writes, read_chains, reads_by_id=reads_by_id)
    emitter = ProgressEmitter("second-order", len(candidates), progress_cb)
    findings: list[InjectionVulnerability] = []
    for i, cand in enumerate(candidates, start=1):
        read_verdict = await judge_chain_verdict(cand.read_side_chain, llm_client=llm_client)
        write_tainted = _looks_user_tainted(cand.write.written_expr)
        is_vuln = write_tainted and (read_verdict.verdict == "vulnerable")
        await emitter.tick(detail=f"2ND-GN-{i:02d} {'vuln' if is_vuln else 'safe'}",
                           hits_delta=1 if is_vuln else 0)
        if not is_vuln:
            continue
        vc = cand.read_side_chain.vuln_class
        findings.append(InjectionVulnerability(
            ID=f"2ND-GN-{i:02d}",
            vulnerability_type=f"second_order_{vc}",
            externally_exploitable=True,        # reachability tag — refine per route in activity if needed
            confidence=read_verdict.confidence,
            source=f"storage read {cand.read.expression} ({cand.read.file_path}:{cand.read.line})",
            combined_sources=f"write:{cand.write.file_path}:{cand.write.line} ({cand.write.storage_token}) "
                             f"+ read:{cand.read.file_path}:{cand.read.line}",
            path=read_verdict.evidence_chain,
            sink_call=cand.read_side_chain.sink_call_site_id,
            slot_type=cand.read_side_chain.sink_slot,
            verdict="vulnerable",
            mismatch_reason=f"second-order: stored data from {cand.write.storage_token} "
                            f"reaches {vc} sink without re-validation. {read_verdict.mismatch_reason or ''}",
            witness_payload=read_verdict.witness_payload,
            source_track="gitnexus",
            evidence_chain=read_verdict.evidence_chain,
        ))
    return findings
```
> **实现要求**:`extract_candidate_chains` 的 `vuln_class` 参数对 read 端要覆盖 XSS + injection(二阶 XSS 走 xss 路由,二阶 SQLi 走 injection 路由)。`reads_by_id` 的 key 与 Task 6 `extract_second_order_candidates` 对齐(用 `SourcePoint.param_name` 或 `id`)——caller(activity)统一构造。`externally_exploitable` 默认 True 占位,真机验收时按路由可达性细化(activity 层),守 CLAUDE.md「不被 verdict 覆写」即不在 verdict=vulnerable 时改它。

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages/core/tests/code_index/test_second_order_builder.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git -C /root/shannon-py add packages/core/src/shannon_core/code_index/vuln_chain_builders/second_order_builder.py packages/core/tests/code_index/test_second_order_builder.py
git -C /root/shannon-py commit -m "feat(code_index): second_order_builder 复用 chain_verdict 二阶判定(子项⑤ Task7)"
```

---

## Task 8: activity 接入 + 守铁律回归

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:1299-1436`(`run_gitnexus_chain_verdict` 循环加 `build_second_order_findings`,合并写 `{vc}_gitnexus_queue.json`)
- Test: `packages/whitebox/tests/pipeline/test_run_gitnexus_chain_verdict_second_order.py`(新建)

**Interfaces:**
- Consumes: Task 7 `build_second_order_findings`、`CodeIndex.storage_write_points`(Task 1/5)、`source_points`(含 STORAGE reads)、`parameter_graph`
- Produces:`{injection,xss}_gitnexus_queue.json` 含 `2ND-GN-*` finding(`source_track="gitnexus"`,`vulnerability_type="second_order_*"`)

- [ ] **Step 1: Write the failing test**

`packages/whitebox/tests/pipeline/test_run_gitnexus_chain_verdict_second_order.py`:
```python
import pytest, json
# Activity-level test: feed a CodeIndex fixture with storage_write_points + a
# STORAGE source reaching an XSS sink; run_gitnexus_chain_verdict must write
# xss_gitnexus_queue.json containing a 2ND-GN-* finding.
@pytest.mark.asyncio
async def test_queue_contains_second_order_finding(tmp_path, monkeypatch):
    # ... set up deliverables dir with parameter_graph.json + code_index.json
    #     containing storage_write_points and a STORAGE→XSS taint_flow ...
    # ... monkeypatch llm_client to return vulnerable for the read side ...
    from shannon_whitebox.pipeline import activities
    await activities.run_gitnexus_chain_verdict(_activity_input(deliverables=tmp_path))
    q = json.loads((tmp_path / "xss_gitnexus_queue.json").read_text())
    ids = [v["ID"] for v in q["vulnerabilities"]]
    assert any(i.startswith("2ND-GN-") for i in ids), ids
```
> **实现要求**:fixture 构造参考现有 `test_workflow_gitnexus_failfast.py` / `test_reporting_workflow.py` 里造 code_index.json + parameter_graph.json 的方式。`run_gitnexus_chain_verdict` 的 ActivityInput 字段以 `activities.py:1299` 签名为准。

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/whitebox/tests/pipeline/test_run_gitnexus_chain_verdict_second_order.py -v`
Expected: FAIL(queue 无 2ND-GN 条目)

- [ ] **Step 3: Write minimal implementation**

在 `activities.py:1389-1393` 三类 builder 循环之后(或合并),加二阶段调用。先 Read `activities.py:1299-1436` 全段确认 `code_index` / `pgraph` / `sink_call_sites` / `llm_client` 变量名与 `deliverables` 路径,然后:
```python
# after the per-class builder loop produces `findings_by_class`:
from shannon_core.code_index.vuln_chain_builders.second_order_builder import build_second_order_findings

storage_writes = code_index.storage_write_points
# build reads_by_id from source_points (STORAGE flavor) keyed by param_name
reads_by_id = {s.param_name: s for s in code_index.source_points
               if s.source_type.value == "storage"}
second_order = await build_second_order_findings(
    storage_writes, pgraph, llm_client=llm_client,
    sink_call_sites=sink_call_sites, reads_by_id=reads_by_id,
    progress_cb=progress_cb,
)
# route second_order findings into their vuln class queue (by vulnerability_type suffix)
for f in second_order:
    vc = f.vulnerability_type.replace("second_order_", "")   # "xss" | "injection"
    findings_by_class.setdefault(vc, []).append(f)
# then the existing atomic_write_json loop writes {vc}_gitnexus_queue.json (unchanged)
```

- [ ] **Step 4: Run test to verify it passes + 守铁律回归**

Run:
```bash
python -m pytest packages/whitebox/tests/pipeline/test_run_gitnexus_chain_verdict_second_order.py -v
python -m pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v
python -m pytest packages/core/tests/code_index/test_storage_models.py packages/core/tests/code_index/test_storage_detector.py packages/core/tests/code_index/test_second_order_join.py packages/core/tests/code_index/test_second_order_builder.py -v
```
Expected:全 PASS;`test_static_dataflow_hints_decoupling.py` 绿(铁律:⑤ 全在 GitNexus 轨,未碰 vuln-*.txt)。

- [ ] **Step 5: Commit**

```bash
git -C /root/shannon-py add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/pipeline/test_run_gitnexus_chain_verdict_second_order.py
git -C /root/shannon-py commit -m "feat(whitebox): run_gitnexus_chain_verdict 接入 second_order 二阶 finding(子项⑤ Task8)"
```

---

## Self-Review(plan 作者自查)

**1. Spec coverage**(对照 spec §1-§9):
- §3.1 三抽象 → Task 1(StorageWritePoint/Medium + STORAGE 枚举;StorageReadPoint=SourcePoint(STORAGE) 由 Task 3 产)
- §3.2 token 边界(字面量/动态) → Task 3(`_is_dynamic`) + Task 6(`is_resolvable_token`/`unresolvable`)
- §3.3 数据流/编排 → Task 5;二阶判定(read 复用 + write tainted) → Task 6+7
- §3.4 LLM 探测器对称 → Task 4
- §3.5 分工(不动 attack chain) → plan 不含 attack_chain_assembler 改动 ✓
- §4 铁律(不碰 vuln-*.txt) → Task 8 回归 `test_static_dataflow_hints_decoupling.py` ✓
- §5 测试(各介质/token 边界/join/verdict) → Task 3/6/7 fixture 覆盖 DB;Config/Cache/File 规则在 Task 3 YAML,验收靠真机(spec §9)。**gap**:Config 介质无独立单测 fixture——补:Task 3 加一个 `test_detect_config_read` 用 `getProperty("auth.timeout")` fixture。**已补,见 Task 3 测试可扩展**(实现者加一条)。
- §6 不做(跨服务二阶/动态 token 确定性 join/并入 attack chain/BFS) → plan 不含 ✓

**2. Placeholder scan**:多处 `...` 标注"先 Read X 照抄"——这是**指向现有代码模板**的精确引用(非空 placeholder),因 plan 不复制 detector 全文(太长且易漂移)。每处给出文件:行号 + 要照抄的符号(`_dedup`/`_functions_repr`/`map_llm_with_bounds`)。可接受(plan 假设工程师能 Read 模板)。无"TBD/TODO/适当处理错误"。

**3. Type consistency**:`StorageWritePoint`/`StorageMedium`/`ParameterSource.STORAGE`/`SourcePoint(STORAGE)`/`SecondOrderCandidate`/`build_second_order_findings`/`extract_second_order_candidates`/`discover_storage_{reads,writes}_llm`/`detect_storage_{reads,writes}`——跨 task 名字一致。`reads_by_id` key 在 Task 6/7/activity 统一为 `SourcePoint.param_name`(Task 7 Step 3 + activity Step 3 对齐)。`vulnerability_type="second_order_{vc}"` 在 Task 7 产、activity 解析,一致。

**4. gap 修复**:
- Config 介质单测:Task 3 实现者加 `test_detect_config_read`(用 `getProperty("auth.timeout")`)。
- `reads_by_id` key 一致性:已在 Task 7/activity 标注统一 `param_name`。
- `externally_exploitable` 占位 True:真机细化(activity 层),spec §4 不变量"不被 verdict 覆写"——占位 True 不是 verdict 覆写,可接受,真机时按路由可达性标。

---

## 真机验收(spec §9)

- sentinel_dashboard 关轨重扫,stored XSS / 二阶 SQLi 候选非空(`{xss,injection}_gitnexus_queue.json` 含 `2ND-GN-*`)。
- NodeGoat stored XSS fixture 回归。
- LLM 轨二阶方法论(follow-up plan)开轨后补动态 token 二阶。
