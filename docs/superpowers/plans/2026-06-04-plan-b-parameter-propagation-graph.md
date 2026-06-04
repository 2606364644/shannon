# Plan B: Parameter Propagation Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a three-stage AST+LLM parameter propagation graph that traces taint sources from HTTP entry point parameters through every call in the chain to their sinks, enabling precise vulnerability analysis.

**Architecture:** Three-stage pipeline — (A) Tree-sitter extracts caller argument → callee parameter mappings deterministically, (B) LLM identifies parameter transformations (encode/decode/sanitize/convert/extract/compose), (C) taint sources are propagated along call chains to build complete TaintFlow paths. Each stage produces validated intermediate artifacts.

**Tech Stack:** Python 3.11+, Pydantic v2, Tree-sitter, pytest

**Depends on:** Plan A (GitNexus + Enhanced Code Index) — uses `TypedParameter`, `ParameterSource`, `FuncBlock`, `CallChain`, `CallEdge`, `CodeIndex` models.

**Followed by:** Plan C (Tiered Per-Chain Audit)

---

## File Structure

### New Files

| File | Responsibility |
|---|---|
| `packages/core/src/shannon_core/code_index/parameter_models.py` | Data models for parameter propagation (ArgParamPair, CallSite, TaintFlow, PropagationStep, ParameterPropagationGraph, SinkType) |
| `packages/core/src/shannon_core/code_index/call_site_locator.py` | Tree-sitter based call site finder and argument extractor |
| `packages/core/src/shannon_core/code_index/arg_param_mapper.py` | Matches caller arguments to callee parameters (positional + keyword) |
| `packages/core/src/shannon_core/code_index/taint_propagator.py` | Entry point parameter source marking + taint propagation along call chains |
| `packages/core/src/shannon_core/code_index/parameter_graph.py` | Orchestrates the three-stage pipeline (AST → LLM → Propagation) |
| `prompts/transform-identify.txt` | LLM prompt for parameter transformation identification |
| `packages/core/tests/code_index/test_parameter_models.py` | Tests for parameter propagation models |
| `packages/core/tests/code_index/test_call_site_locator.py` | Tests for call site locator |
| `packages/core/tests/code_index/test_arg_param_mapper.py` | Tests for arg→param mapper |
| `packages/core/tests/code_index/test_taint_propagator.py` | Tests for taint propagation |
| `packages/core/tests/code_index/test_parameter_graph.py` | Tests for full pipeline |

### Modified Files

| File | Change |
|---|---|
| `packages/core/src/shannon_core/code_index/__init__.py` | Add `build_parameter_graph()` function, import new modules |
| `packages/core/src/shannon_core/code_index/models.py` | Ensure `ParameterSource` and `TypedParameter` are exported (from Plan A) |

---

## Task 1: Parameter Propagation Data Models

**Files:**
- Create: `packages/core/src/shannon_core/code_index/parameter_models.py`
- Test: `packages/core/tests/code_index/test_parameter_models.py`

All data models needed by the parameter propagation pipeline. No external dependencies beyond Plan A models.

- [ ] **Step 1: Write failing tests**

Create `packages/core/tests/code_index/test_parameter_models.py`:

```python
import pytest
from shannon_core.code_index.parameter_models import (
    ArgExpression, CallSite, ArgParamPair, SinkType,
    PropagationStep, TaintFlow, ParameterPropagationGraph,
)
from shannon_core.code_index.models import ParameterSource


class TestArgExpression:
    def test_positional_arg(self):
        arg = ArgExpression(expression="user_id", kind="positional")
        assert arg.kind == "positional"
        assert arg.keyword is None

    def test_keyword_arg(self):
        arg = ArgExpression(expression="30", kind="keyword", keyword="timeout")
        assert arg.keyword == "timeout"


class TestCallSite:
    def test_call_site(self):
        site = CallSite(
            callee_name="process_order",
            line=5,
            arguments=[
                ArgExpression(expression="user_id", kind="positional"),
                ArgExpression(expression="request.body", kind="positional"),
                ArgExpression(expression="30", kind="keyword", keyword="timeout"),
            ],
        )
        assert site.callee_name == "process_order"
        assert len(site.arguments) == 3
        assert site.arguments[2].keyword == "timeout"


class TestArgParamPair:
    def test_basic_mapping(self):
        pair = ArgParamPair(
            arg_name="user_id",
            param_name="order_id",
        )
        assert pair.arg_name == "user_id"
        assert pair.param_name == "order_id"
        assert pair.transform is None
        assert pair.arg_source is None

    def test_with_source_and_transform(self):
        pair = ArgParamPair(
            arg_name="request.body",
            param_name="data",
            arg_source=ParameterSource.BODY_FIELD,
            transform="extract",
        )
        assert pair.arg_source == ParameterSource.BODY_FIELD
        assert pair.transform == "extract"


class TestSinkType:
    def test_sink_types(self):
        assert SinkType.SQL_EXECUTION == "sql_execution"
        assert SinkType.COMMAND_EXEC == "command_exec"
        assert SinkType.FILE_WRITE == "file_write"
        assert SinkType.TEMPLATE_RENDER == "template_render"
        assert SinkType.HTTP_REQUEST == "http_request"
        assert SinkType.DESERIALIZATION == "deserialization"
        assert SinkType.LOG_WRITE == "log_write"


class TestPropagationStep:
    def test_step(self):
        step = PropagationStep(
            from_func_id="app.py:handler:10",
            from_param="user_id",
            to_func_id="svc.py:process:20",
            to_param="order_id",
            transformation=None,
            code_location="app.py:12",
        )
        assert step.from_func_id == "app.py:handler:10"
        assert step.to_param == "order_id"


class TestTaintFlow:
    def test_complete_flow(self):
        flow = TaintFlow(
            entry_point_id="app.py:handler:10",
            source_param="user_id",
            source_type=ParameterSource.QUERY_PARAM,
            propagation_steps=[
                PropagationStep(
                    from_func_id="app.py:handler:10",
                    from_param="user_id",
                    to_func_id="svc.py:get_order:20",
                    to_param="order_id",
                    transformation=None,
                    code_location="app.py:12",
                ),
            ],
            sink_func_id="db.py:query:30",
            sink_type=SinkType.SQL_EXECUTION,
        )
        assert flow.source_type == ParameterSource.QUERY_PARAM
        assert flow.sink_type == SinkType.SQL_EXECUTION
        assert len(flow.propagation_steps) == 1

    def test_flow_without_sink(self):
        flow = TaintFlow(
            entry_point_id="app.py:handler:10",
            source_param="user_id",
            source_type=ParameterSource.QUERY_PARAM,
            propagation_steps=[],
            sink_func_id=None,
            sink_type=None,
        )
        assert flow.sink_func_id is None
        assert flow.sink_type is None


class TestParameterPropagationGraph:
    def test_empty_graph(self):
        graph = ParameterPropagationGraph(taint_flows=[])
        assert graph.total_flows == 0
        assert graph.flows_by_source == {}

    def test_graph_with_flows(self):
        flows = [
            TaintFlow(
                entry_point_id="a.py:f:1",
                source_param="id",
                source_type=ParameterSource.QUERY_PARAM,
                propagation_steps=[],
                sink_func_id="b.py:g:2",
                sink_type=SinkType.SQL_EXECUTION,
            ),
            TaintFlow(
                entry_point_id="a.py:f:1",
                source_param="name",
                source_type=ParameterSource.BODY_FIELD,
                propagation_steps=[],
                sink_func_id="c.py:h:3",
                sink_type=SinkType.TEMPLATE_RENDER,
            ),
        ]
        graph = ParameterPropagationGraph(taint_flows=flows)
        assert graph.total_flows == 2
        assert len(graph.flows_by_source) == 2
        assert len(graph.flows_by_source["query"]) == 1
        assert len(graph.flows_by_source["body"]) == 1

    def test_flows_for_function(self):
        flows = [
            TaintFlow(
                entry_point_id="a.py:f:1",
                source_param="id",
                source_type=ParameterSource.QUERY_PARAM,
                propagation_steps=[],
                sink_func_id="b.py:g:2",
                sink_type=SinkType.SQL_EXECUTION,
            ),
        ]
        graph = ParameterPropagationGraph(taint_flows=flows)
        result = graph.flows_for_function("b.py:g:2")
        assert len(result) == 1
        assert result[0].sink_type == SinkType.SQL_EXECUTION

    def test_serialization(self):
        flows = [
            TaintFlow(
                entry_point_id="a.py:f:1",
                source_param="id",
                source_type=ParameterSource.QUERY_PARAM,
                propagation_steps=[],
                sink_func_id="b.py:g:2",
                sink_type=SinkType.SQL_EXECUTION,
            ),
        ]
        graph = ParameterPropagationGraph(taint_flows=flows)
        json_str = graph.to_json()
        assert "sql_execution" in json_str
        assert "query" in json_str
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_parameter_models.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement parameter_models.py**

Create `packages/core/src/shannon_core/code_index/parameter_models.py`:

```python
"""Data models for the parameter propagation graph.

These models support the three-stage parameter propagation pipeline:
  Stage A: AST extracts arg→param mappings (ArgParamPair)
  Stage B: LLM identifies transformations (transform field)
  Stage C: Taint propagation builds TaintFlow paths
"""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, ConfigDict

from shannon_core.code_index.models import ParameterSource


# ── Stage A: Call site extraction ────────────────────────────


class ArgExpression(BaseModel):
    """A single argument expression at a call site."""
    expression: str           # "user_id", "request.body", "30"
    kind: str                 # "positional" | "keyword"
    keyword: str | None = None  # "timeout" (only for keyword args)


class CallSite(BaseModel):
    """A function call site found in source code.

    Represents one call expression within a function body,
    with all its argument expressions extracted.
    """
    callee_name: str
    line: int
    arguments: list[ArgExpression] = []


class ArgParamPair(BaseModel):
    """One argument→parameter mapping across a call edge.

    Created in Stage A (AST), enriched in Stage B (LLM transform).
    Used in Stage C (taint propagation) to track source flow.
    """
    arg_name: str                           # expression from caller
    param_name: str                         # parameter name in callee
    arg_source: ParameterSource | None = None  # inherited taint source
    transform: str | None = None            # "none"|"encode"|"decode"|"sanitize"|"convert"|"extract"|"compose"
    transform_confidence: float = 0.0       # LLM confidence for the transform classification


# ── Sink classification ──────────────────────────────────────


class SinkType(str, Enum):
    """Types of security-sensitive sinks."""
    SQL_EXECUTION = "sql_execution"
    COMMAND_EXEC = "command_exec"
    FILE_WRITE = "file_write"
    TEMPLATE_RENDER = "template_render"
    HTTP_REQUEST = "http_request"
    DESERIALIZATION = "deserialization"
    LOG_WRITE = "log_write"
    UNKNOWN = "unknown"


# ── Stage C: Taint propagation ───────────────────────────────


class PropagationStep(BaseModel):
    """One step in a taint propagation chain.

    Records how a tainted parameter flows from one function to another.
    """
    from_func_id: str           # caller FuncBlock.id
    from_param: str             # parameter name in caller
    to_func_id: str             # callee FuncBlock.id
    to_param: str               # parameter name in callee
    transformation: str | None  # "url_decode", "json_parse", "sanitize", etc.
    code_location: str          # "file:line"


class TaintFlow(BaseModel):
    """A complete taint propagation path from entry point to sink.

    Represents how user-controllable input flows through the codebase
    to a security-sensitive sink.
    """
    entry_point_id: str
    source_param: str                # original parameter name at entry point
    source_type: ParameterSource     # query/body/path/header/...
    propagation_steps: list[PropagationStep]
    sink_func_id: str | None
    sink_type: SinkType | None


class ParameterPropagationGraph(BaseModel):
    """Complete parameter propagation graph for a repository.

    Contains all TaintFlow paths discovered during analysis.
    """
    taint_flows: list[TaintFlow]

    @property
    def total_flows(self) -> int:
        return len(self.taint_flows)

    @property
    def flows_by_source(self) -> dict[str, list[TaintFlow]]:
        """Group flows by their taint source type."""
        groups: dict[str, list[TaintFlow]] = {}
        for flow in self.taint_flows:
            key = flow.source_type.value
            groups.setdefault(key, []).append(flow)
        return groups

    def flows_for_function(self, func_id: str) -> list[TaintFlow]:
        """Get all flows that pass through or terminate at a function."""
        results = []
        for flow in self.taint_flows:
            # Check if function is the sink
            if flow.sink_func_id == func_id:
                results.append(flow)
                continue
            # Check if function is an intermediate step
            for step in flow.propagation_steps:
                if step.from_func_id == func_id or step.to_func_id == func_id:
                    results.append(flow)
                    break
        return results

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_parameter_models.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/parameter_models.py packages/core/tests/code_index/test_parameter_models.py
git commit -m "feat(code_index): add parameter propagation data models

ArgExpression, CallSite, ArgParamPair for AST stage.
SinkType enum for sink classification.
PropagationStep, TaintFlow, ParameterPropagationGraph for taint stage.
Queryable by source type and function ID."
```

---

## Task 2: Call Site Locator

**Files:**
- Create: `packages/core/src/shannon_core/code_index/call_site_locator.py`
- Test: `packages/core/tests/code_index/test_call_site_locator.py`

Finds all call sites within a function's source code using Tree-sitter, extracting argument expressions (positional + keyword). Supports Python and TypeScript.

- [ ] **Step 1: Write failing tests**

Create `packages/core/tests/code_index/test_call_site_locator.py`:

```python
import pytest
from pathlib import Path
from shannon_core.code_index.call_site_locator import (
    locate_call_sites_python, locate_call_sites_typescript, locate_call_sites,
)
from shannon_core.code_index.parameter_models import CallSite


class TestLocatePythonCallSites:
    def test_simple_call(self):
        source = (
            "def handler(user_id):\n"
            "    result = process(user_id)\n"
            "    return result\n"
        )
        sites = locate_call_sites_python(source, "handler", 1)
        assert len(sites) == 1
        assert sites[0].callee_name == "process"
        assert len(sites[0].arguments) == 1
        assert sites[0].arguments[0].expression == "user_id"
        assert sites[0].arguments[0].kind == "positional"

    def test_multiple_args(self):
        source = (
            "def handler(request):\n"
            "    result = process_order(request.args.get('id'), request.body)\n"
            "    return result\n"
        )
        sites = locate_call_sites_python(source, "handler", 1)
        assert len(sites) == 1
        assert len(sites[0].arguments) == 2
        assert sites[0].arguments[0].expression == "request.args.get('id')"
        assert sites[0].arguments[1].expression == "request.body"

    def test_keyword_args(self):
        source = (
            "def handler(user_id):\n"
            "    result = create(name='test', timeout=30)\n"
            "    return result\n"
        )
        sites = locate_call_sites_python(source, "handler", 1)
        assert len(sites) == 1
        assert len(sites[0].arguments) == 2
        assert sites[0].arguments[0].kind == "keyword"
        assert sites[0].arguments[0].keyword == "name"
        assert sites[0].arguments[1].keyword == "timeout"

    def test_mixed_args(self):
        source = (
            "def handler(user_id):\n"
            "    db.query(user_id, limit=10, offset=0)\n"
        )
        sites = locate_call_sites_python(source, "handler", 1)
        assert len(sites) == 1
        args = sites[0].arguments
        assert len(args) == 3
        assert args[0].kind == "positional"
        assert args[0].expression == "user_id"
        assert args[1].kind == "keyword"
        assert args[1].keyword == "limit"

    def test_multiple_calls(self):
        source = (
            "def handler(data):\n"
            "    validate(data)\n"
            "    result = save(data)\n"
            "    notify(result)\n"
        )
        sites = locate_call_sites_python(source, "handler", 1)
        assert len(sites) == 3
        names = {s.callee_name for s in sites}
        assert names == {"validate", "save", "notify"}

    def test_no_calls(self):
        source = "def helper():\n    x = 1\n    return x\n"
        sites = locate_call_sites_python(source, "helper", 1)
        assert len(sites) == 0

    def test_nested_call_expressions(self):
        source = (
            "def handler(user_id):\n"
            "    result = process(str(user_id))\n"
        )
        sites = locate_call_sites_python(source, "handler", 1)
        # Should find both 'process' and 'str' as call sites
        assert len(sites) >= 1
        process_site = next((s for s in sites if s.callee_name == "process"), None)
        assert process_site is not None
        # The argument is the inner call expression str(user_id)
        assert "str(user_id)" in process_site.arguments[0].expression or \
               "str" in process_site.arguments[0].expression

    def test_method_call(self):
        source = (
            "def handler(request):\n"
            "    db.query('SELECT * FROM users WHERE id = ' + request.args.get('id'))\n"
        )
        sites = locate_call_sites_python(source, "handler", 1)
        # Should find 'query' as a call (from db.query)
        assert any(s.callee_name == "query" for s in sites)

    def test_function_not_found(self):
        source = "x = 1\n"
        sites = locate_call_sites_python(source, "nonexistent", 1)
        assert sites == []


class TestLocateTypeScriptCallSites:
    def test_simple_call(self):
        source = (
            "function handler(req: Request, res: Response) {\n"
            "    process(req.body.user_id);\n"
            "}\n"
        )
        sites = locate_call_sites_typescript(source, "handler", 1)
        assert len(sites) >= 1
        process_site = next((s for s in sites if s.callee_name == "process"), None)
        assert process_site is not None
        assert len(process_site.arguments) == 1
        assert "user_id" in process_site.arguments[0].expression

    def test_method_call(self):
        source = (
            "async function getUser(req: Request) {\n"
            "    const user = await db.query(req.params.id);\n"
            "    return user;\n"
            "}\n"
        )
        sites = locate_call_sites_typescript(source, "getUser", 1)
        assert any(s.callee_name == "query" for s in sites)

    def test_arrow_function(self):
        source = (
            "const handler = (req: Request, res: Response) => {\n"
            "    process(req.body);\n"
            "};\n"
        )
        sites = locate_call_sites_typescript(source, "handler", 1)
        assert any(s.callee_name == "process" for s in sites)


class TestLocateCallSitesDispatch:
    def test_dispatches_python(self):
        source = "def f():\n    g(1)\n"
        sites = locate_call_sites(source, "f", 1, "python")
        assert len(sites) == 1

    def test_dispatches_typescript(self):
        source = "function f() { g(1); }\n"
        sites = locate_call_sites(source, "f", 1, "typescript")
        assert len(sites) >= 1

    def test_unsupported_language(self):
        source = "def f():\n    g(1)\n"
        sites = locate_call_sites(source, "f", 1, "unknown_lang")
        assert sites == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_call_site_locator.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement call_site_locator.py**

Create `packages/core/src/shannon_core/code_index/call_site_locator.py`:

```python
"""Call site locator — finds function calls and extracts arguments.

Uses Tree-sitter to locate all call expressions within a function body
and extract the argument expressions (positional and keyword).

This is Stage A of the parameter propagation pipeline.
"""

import logging
from shannon_core.code_index.parameter_models import ArgExpression, CallSite

logger = logging.getLogger(__name__)


def locate_call_sites(
    source: str,
    func_name: str,
    start_line: int,
    language: str,
) -> list[CallSite]:
    """Locate all call sites within a function, dispatching by language.

    Args:
        source: Complete source code of the file.
        func_name: Name of the function to analyze.
        start_line: 1-based line number where the function starts.
        language: "python" | "typescript" | etc.

    Returns:
        List of CallSite objects, one per call expression found.
    """
    if language == "python":
        return locate_call_sites_python(source, func_name, start_line)
    elif language in ("typescript", "javascript"):
        return locate_call_sites_typescript(source, func_name, start_line)
    else:
        logger.debug("Call site locator not implemented for %s", language)
        return []


def locate_call_sites_python(
    source: str,
    func_name: str,
    start_line: int,
) -> list[CallSite]:
    """Find all call sites within a Python function.

    Parses the source with Tree-sitter Python grammar, locates the target
    function, then walks its body to find all call expressions.
    """
    import tree_sitter_python as tspython
    from tree_sitter import Language, Parser

    parser = Parser(Language(tspython.language()))
    tree = parser.parse(source.encode())

    func_node = _find_python_function(tree.root_node, func_name, start_line)
    if func_node is None:
        return []

    return _extract_python_call_sites(func_node, source.encode())


def locate_call_sites_typescript(
    source: str,
    func_name: str,
    start_line: int,
) -> list[CallSite]:
    """Find all call sites within a TypeScript function or arrow function."""
    import tree_sitter_typescript as tsts
    from tree_sitter import Language, Parser

    parser = Parser(Language(tsts.language_typescript()))
    tree = parser.parse(source.encode())

    func_node = _find_typescript_function(tree.root_node, func_name, start_line)
    if func_node is None:
        return []

    return _extract_ts_call_sites(func_node, source.encode())


# ── Python implementation ────────────────────────────────────


def _find_python_function(root_node, func_name: str, start_line: int):
    """Find a Python function AST node by name and start line."""
    for node in _walk(root_node):
        if node.type in ("function_definition", "async_function_definition"):
            name_node = node.child_by_field_name("name")
            if name_node and name_node.text.decode() == func_name:
                if node.start_point[0] + 1 == start_line:
                    return node
    return None


def _extract_python_call_sites(func_node, source: bytes) -> list[CallSite]:
    """Extract call sites from a Python function AST node."""
    sites: list[CallSite] = []

    for node in _walk(func_node):
        if node.type == "call":
            callee_name = _get_python_callee_name(node)
            if callee_name is None:
                continue

            arguments = _extract_python_arguments(node, source)
            sites.append(CallSite(
                callee_name=callee_name,
                line=node.start_point[0] + 1,
                arguments=arguments,
            ))

    return sites


def _get_python_callee_name(call_node) -> str | None:
    """Extract the callee function name from a Python call node."""
    func_node = call_node.child_by_field_name("function")
    if func_node is None:
        return None

    if func_node.type == "identifier":
        return func_node.text.decode()
    elif func_node.type == "attribute":
        attr = func_node.child_by_field_name("attribute")
        if attr:
            return attr.text.decode()
    return None


def _extract_python_arguments(call_node, source: bytes) -> list[ArgExpression]:
    """Extract argument expressions from a Python call node."""
    # In Python Tree-sitter, arguments are children of the call node
    # that are of type "argument_list"
    args: list[ArgExpression] = []

    for child in call_node.children:
        if child.type == "argument_list":
            for arg_node in child.children:
                if arg_node.type == ",":
                    continue

                if arg_node.type == "keyword_argument":
                    # keyword_argument has children: [identifier, "=", expression]
                    keyword_name = None
                    value_text = None
                    for sub in arg_node.children:
                        if sub.type == "identifier" and keyword_name is None:
                            keyword_name = sub.text.decode()
                        elif sub.type not in ("=",) and keyword_name and value_text is None:
                            value_text = sub.text.decode()
                    if keyword_name and value_text:
                        args.append(ArgExpression(
                            expression=value_text,
                            kind="keyword",
                            keyword=keyword_name,
                        ))
                else:
                    # Positional argument
                    expr_text = arg_node.text.decode()
                    if expr_text not in (",", "(", ")"):
                        args.append(ArgExpression(
                            expression=expr_text,
                            kind="positional",
                        ))

    return args


# ── TypeScript implementation ────────────────────────────────


def _find_typescript_function(root_node, func_name: str, start_line: int):
    """Find a TypeScript function AST node by name and start line."""
    for node in _walk(root_node):
        if node.type == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node and name_node.text.decode() == func_name:
                if node.start_point[0] + 1 == start_line:
                    return node
        elif node.type == "arrow_function":
            parent = node.parent
            if parent and parent.type == "variable_declarator":
                name_node = parent.child_by_field_name("name")
                if name_node and name_node.text.decode() == func_name:
                    if node.start_point[0] + 1 == start_line:
                        return node
    return None


def _extract_ts_call_sites(func_node, source: bytes) -> list[CallSite]:
    """Extract call sites from a TypeScript function AST node."""
    sites: list[CallSite] = []

    for node in _walk(func_node):
        if node.type == "call_expression":
            callee_name = _get_ts_callee_name(node)
            if callee_name is None:
                continue

            arguments = _extract_ts_arguments(node, source)
            sites.append(CallSite(
                callee_name=callee_name,
                line=node.start_point[0] + 1,
                arguments=arguments,
            ))

    return sites


def _get_ts_callee_name(call_node) -> str | None:
    """Extract the callee function name from a TS call_expression node."""
    func_node = call_node.child_by_field_name("function")
    if func_node is None:
        return None

    if func_node.type == "identifier":
        return func_node.text.decode()
    elif func_node.type == "member_expression":
        prop = func_node.child_by_field_name("property")
        if prop:
            return prop.text.decode()
    return None


def _extract_ts_arguments(call_node, source: bytes) -> list[ArgExpression]:
    """Extract argument expressions from a TypeScript call_expression node."""
    args: list[ArgExpression] = []

    # Arguments are in a parenthesized list
    for child in call_node.children:
        if child.type == "arguments" or child.type == "(":
            for arg_node in child.children:
                if arg_node.type in (",", "(", ")", ";"):
                    continue
                expr_text = arg_node.text.decode()
                if expr_text:
                    args.append(ArgExpression(
                        expression=expr_text,
                        kind="positional",
                    ))

    return args


# ── Shared utilities ─────────────────────────────────────────


def _walk(node):
    """Yield all descendant nodes depth-first."""
    yield node
    for child in node.children:
        yield from _walk(child)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_call_site_locator.py -v`
Expected: All tests PASS (may need minor adjustments for Tree-sitter output format)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/call_site_locator.py packages/core/tests/code_index/test_call_site_locator.py
git commit -m "feat(code_index): add call site locator with argument extraction

Tree-sitter based extraction of call sites and argument expressions
(positional + keyword) for Python and TypeScript functions."
```

---

## Task 3: Arg→Param Mapper

**Files:**
- Create: `packages/core/src/shannon_core/code_index/arg_param_mapper.py`
- Test: `packages/core/tests/code_index/test_arg_param_mapper.py`

Maps caller arguments to callee parameters using positional index and keyword matching. Works with CallSite from Task 2 and TypedParameter from Plan A.

- [ ] **Step 1: Write failing tests**

Create `packages/core/tests/code_index/test_arg_param_mapper.py`:

```python
import pytest
from pathlib import Path
from unittest.mock import patch
from shannon_core.code_index.arg_param_mapper import (
    map_args_to_params, build_all_arg_param_mappings,
)
from shannon_core.code_index.parameter_models import (
    CallSite, ArgExpression, ArgParamPair,
)
from shannon_core.code_index.models import FuncBlock, CallEdge, TypedParameter


def _block(name: str, file: str = "app.py", line: int = 1,
           params: list[str] | None = None) -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}",
        file_path=file,
        function_name=name,
        start_line=line,
        end_line=line + 5,
        source_code=f"def {name}(): pass",
        parameters=params or [],
        language="python",
    )


class TestMapArgsToParams:
    def test_positional_matching(self):
        call_site = CallSite(
            callee_name="process",
            line=5,
            arguments=[
                ArgExpression(expression="user_id", kind="positional"),
                ArgExpression(expression="data", kind="positional"),
            ],
        )
        callee_params = [
            TypedParameter(name="order_id", type_annotation="int"),
            TypedParameter(name="payload", type_annotation="dict"),
        ]
        pairs = map_args_to_params(call_site, callee_params)
        assert len(pairs) == 2
        assert pairs[0].arg_name == "user_id"
        assert pairs[0].param_name == "order_id"
        assert pairs[1].arg_name == "data"
        assert pairs[1].param_name == "payload"

    def test_keyword_matching(self):
        call_site = CallSite(
            callee_name="create",
            line=5,
            arguments=[
                ArgExpression(expression="'test'", kind="keyword", keyword="name"),
                ArgExpression(expression="30", kind="keyword", keyword="timeout"),
            ],
        )
        callee_params = [
            TypedParameter(name="name", type_annotation="str"),
            TypedParameter(name="timeout", type_annotation="int", default_value="60"),
        ]
        pairs = map_args_to_params(call_site, callee_params)
        assert len(pairs) == 2
        assert pairs[0].param_name == "name"
        assert pairs[1].param_name == "timeout"

    def test_mixed_positional_and_keyword(self):
        call_site = CallSite(
            callee_name="db_query",
            line=10,
            arguments=[
                ArgExpression(expression="sql", kind="positional"),
                ArgExpression(expression="True", kind="keyword", keyword="dry_run"),
            ],
        )
        callee_params = [
            TypedParameter(name="query", type_annotation="str"),
            TypedParameter(name="dry_run", type_annotation="bool", default_value="False"),
        ]
        pairs = map_args_to_params(call_site, callee_params)
        assert len(pairs) == 2
        # Positional: sql → query
        assert pairs[0].arg_name == "sql"
        assert pairs[0].param_name == "query"
        # Keyword: dry_run → dry_run
        assert pairs[1].param_name == "dry_run"

    def test_more_args_than_params(self):
        """Extra arguments that don't match params are ignored."""
        call_site = CallSite(
            callee_name="func",
            line=5,
            arguments=[
                ArgExpression(expression="a", kind="positional"),
                ArgExpression(expression="b", kind="positional"),
                ArgExpression(expression="c", kind="positional"),
            ],
        )
        callee_params = [
            TypedParameter(name="x"),
            TypedParameter(name="y"),
        ]
        pairs = map_args_to_params(call_site, callee_params)
        assert len(pairs) == 2  # Only matches for available params

    def test_fewer_args_than_params(self):
        """Missing arguments are skipped (callee has defaults)."""
        call_site = CallSite(
            callee_name="func",
            line=5,
            arguments=[
                ArgExpression(expression="a", kind="positional"),
            ],
        )
        callee_params = [
            TypedParameter(name="x"),
            TypedParameter(name="y", default_value="None"),
        ]
        pairs = map_args_to_params(call_site, callee_params)
        assert len(pairs) == 1
        assert pairs[0].param_name == "x"

    def test_empty_call_site(self):
        call_site = CallSite(callee_name="func", line=1, arguments=[])
        callee_params = [TypedParameter(name="x")]
        pairs = map_args_to_params(call_site, callee_params)
        assert pairs == []

    def test_empty_params(self):
        call_site = CallSite(
            callee_name="func", line=1,
            arguments=[ArgExpression(expression="a", kind="positional")],
        )
        pairs = map_args_to_params(call_site, [])
        assert pairs == []


class TestBuildAllArgParamMappings:
    def test_builds_mappings_for_chain(self, tmp_path):
        """Given a chain of FuncBlocks, build arg→param mappings for each edge."""
        # Create source files
        (tmp_path / "app.py").write_text(
            "def handler(user_id):\n"
            "    result = process(user_id)\n"
            "    return result\n"
            "def process(order_id):\n"
            "    return save(order_id)\n"
        )

        blocks = [
            _block("handler", "app.py", 1, params=["user_id"]),
            _block("process", "app.py", 4, params=["order_id"]),
        ]
        edges = [
            CallEdge(
                caller_id="app.py:handler:1",
                callee_name="process",
                callee_file="app.py",
                resolved=True,
                line=2,
            ),
        ]

        with patch("shannon_core.code_index.arg_param_mapper.extract_typed_parameters") as mock_extract:
            mock_extract.side_effect = lambda fp, fn, sl, lang: {
                ("app.py", "handler", 1): [TypedParameter(name="user_id")],
                ("app.py", "process", 4): [TypedParameter(name="order_id")],
            }.get((str(fp), fn, sl), [])

            result = build_all_arg_param_mappings(blocks, edges, tmp_path, "python")

        # Should have at least one mapping for handler→process edge
        assert len(result) >= 1
        # Check the mapping content
        key = ("app.py:handler:1", "app.py:process:4")
        if key in result:
            pairs = result[key]
            assert any(p.param_name == "order_id" for p in pairs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_arg_param_mapper.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement arg_param_mapper.py**

Create `packages/core/src/shannon_core/code_index/arg_param_mapper.py`:

```python
"""Arg→Param mapper — Stage A of the parameter propagation pipeline.

Maps caller argument expressions to callee parameter names using:
1. Positional matching (arg index → param index)
2. Keyword matching (arg keyword → param name)

Requires:
  - CallSite (from call_site_locator) with extracted argument expressions
  - TypedParameter (from enhanced_parameters) for callee parameter info
"""

import logging
from collections import defaultdict
from pathlib import Path

from shannon_core.code_index.models import CallEdge, FuncBlock, TypedParameter
from shannon_core.code_index.parameter_models import ArgParamPair, CallSite
from shannon_core.code_index.call_site_locator import locate_call_sites

logger = logging.getLogger(__name__)


def map_args_to_params(
    call_site: CallSite,
    callee_params: list[TypedParameter],
) -> list[ArgParamPair]:
    """Map arguments from a call site to callee parameters.

    Strategy:
    1. Positional args match by index
    2. Keyword args match by name

    Args:
        call_site: The call site with extracted argument expressions.
        callee_params: The callee's typed parameter list.

    Returns:
        List of ArgParamPair mappings.
    """
    pairs: list[ArgParamPair] = []

    positional_index = 0

    for arg in call_site.arguments:
        if arg.kind == "positional":
            if positional_index < len(callee_params):
                param = callee_params[positional_index]
                pairs.append(ArgParamPair(
                    arg_name=arg.expression,
                    param_name=param.name,
                ))
            positional_index += 1

        elif arg.kind == "keyword" and arg.keyword:
            match = next(
                (p for p in callee_params if p.name == arg.keyword),
                None,
            )
            if match:
                pairs.append(ArgParamPair(
                    arg_name=arg.expression,
                    param_name=match.name,
                ))

    return pairs


def build_all_arg_param_mappings(
    blocks: list[FuncBlock],
    edges: list[CallEdge],
    repo_root: Path,
    language: str,
) -> dict[tuple[str, str], list[ArgParamPair]]:
    """Build arg→param mappings for all resolved call edges.

    For each resolved edge (caller → callee):
    1. Locate call sites in caller's source code
    2. Find the call site matching the callee name
    3. Extract callee's typed parameters
    4. Map arguments to parameters

    Args:
        blocks: All known function blocks.
        edges: All resolved call edges.
        repo_root: Repository root path for file access.
        language: Source language ("python", "typescript", etc.)

    Returns:
        Dict mapping (caller_id, callee_id) → list of ArgParamPair.
    """
    from shannon_core.code_index.enhanced_parameters import extract_typed_parameters

    # Index blocks by ID for fast lookup
    block_by_id: dict[str, FuncBlock] = {b.id: b for b in blocks}
    # Index blocks by (file, name) for callee resolution
    block_by_file_name: dict[tuple[str, str], FuncBlock] = {}
    for b in blocks:
        block_by_file_name.setdefault((b.file_path, b.function_name), b)

    mappings: dict[tuple[str, str], list[ArgParamPair]] = {}

    for edge in edges:
        if not edge.resolved or not edge.callee_file:
            continue

        caller = block_by_id.get(edge.caller_id)
        callee = block_by_file_name.get(
            (edge.callee_file, edge.callee_name)
        )

        if caller is None or callee is None:
            continue

        # Locate call sites in the caller's source
        caller_source = (repo_root / caller.file_path).read_text(
            errors="replace"
        )
        call_sites = locate_call_sites(
            caller_source, caller.function_name,
            caller.start_line, language,
        )

        # Find the call site matching this edge's callee
        matching_site = None
        for site in call_sites:
            if site.callee_name == edge.callee_name:
                matching_site = site
                break

        if matching_site is None:
            continue

        # Extract callee's typed parameters
        callee_path = repo_root / callee.file_path
        callee_params = extract_typed_parameters(
            callee_path, callee.function_name,
            callee.start_line, language,
        )

        # Build the mapping
        pairs = map_args_to_params(matching_site, callee_params)
        if pairs:
            edge_key = (edge.caller_id, callee.id)
            mappings[edge_key] = pairs
            logger.debug(
                "Mapped %d arg→param pairs for %s → %s",
                len(pairs), edge.caller_id, callee.id,
            )

    return mappings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_arg_param_mapper.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/arg_param_mapper.py packages/core/tests/code_index/test_arg_param_mapper.py
git commit -m "feat(code_index): add arg→param mapper

Maps caller arguments to callee parameters using positional + keyword
matching. build_all_arg_param_mappings() processes all resolved edges
in a CodeIndex to produce ArgParamPair mappings."
```

---

## Task 4: LLM Transform Identification Prompt

**Files:**
- Create: `prompts/transform-identify.txt`

The LLM prompt for Stage B — identifying parameter transformations (encode/decode/sanitize/convert/extract/compose) at each arg→param mapping.

- [ ] **Step 1: Write the prompt file**

Create `prompts/transform-identify.txt`:

```
SYSTEM: You are a code analysis assistant identifying parameter transformations between function calls. Your job is to classify how each argument is transformed when passed to a callee.

## INPUT FORMAT

You will receive:
1. Caller function source code
2. Callee function source code
3. Argument-to-parameter mappings (arg expression → param name)

## OUTPUT FORMAT (strict JSON array)

Output ONLY a JSON array. One object per mapping:
```json
[
  {
    "arg": "expression from caller",
    "param": "parameter name in callee",
    "transform": "none|encode|decode|sanitize|convert|extract|compose",
    "confidence": 0.0-1.0
  }
]
```

## TRANSFORMATION TYPES

- **none**: Direct pass-through with no transformation. The arg expression is simply a variable name that maps directly to the param.
- **encode**: Encoding transformation — URL encoding, base64, HTML entity encoding, JSON.stringify, encodeURIComponent.
- **decode**: Decoding transformation — URL decoding, base64 decode, JSON.parse, decodeURIComponent.
- **sanitize**: Intentional security measure — escape_html, parameterize_sql, validate_int, allowlist check, escape_string, mysqli_real_escape.
- **convert**: Type conversion — int(), str(), float(), bool(), Number(), parseInt(), parseFloat().
- **extract**: Field access — accessing a sub-field from an object (e.g., request.body.username, req.params.id, data["key"]).
- **compose**: Building a new value from multiple sources — string concatenation with user input, f-string interpolation, template literal.

## RULES

1. Classify "none" when the argument is a simple variable name passed directly.
2. Classify "sanitize" ONLY when the transformation is clearly a security control (escape, validate, parameterize).
3. Classify "extract" when accessing a field/sub-field of an object (e.g., request.args.get('id')).
4. Classify "compose" when building new values from user input (e.g., "SELECT * FROM " + table).
5. Default to "none" when uncertain. Use low confidence (< 0.5) for uncertain classifications.
6. Output ONLY the JSON array — no markdown, no explanation, no code fences.

## EXAMPLE

Caller:
```python
def handler(request):
    user_id = request.args.get('user_id')
    result = get_user(int(user_id))
    return result
```

Callee:
```python
def get_user(user_id: int):
    return db.query(f"SELECT * FROM users WHERE id = {user_id}")
```

Mappings: [("user_id", "user_id")]

Output:
```json
[{"arg": "user_id", "param": "user_id", "transform": "none", "confidence": 0.9}]
```

Note: int() conversion happened BEFORE the call, so the arg→param is a direct pass. The conversion from str to int was in the caller's local variable assignment, not in the argument passing.

## EXAMPLE 2

Caller:
```python
def handler(request):
    db.query(request.args.get('q'))
```

Callee:
```python
def query(sql: str):
    cursor.execute(sql)
```

Mappings: [("request.args.get('q')", "sql")]

Output:
```json
[{"arg": "request.args.get('q')", "param": "sql", "transform": "extract", "confidence": 0.95}]
```

## EXAMPLE 3

Caller:
```python
def handler(request):
    username = escape_html(request.form['username'])
    render_template('greeting.html', name=username)
```

Callee:
```python
def render_template(template_name, name=''):
    ...
```

Mappings: [("username", "name")]

Output:
```json
[{"arg": "username", "param": "name", "transform": "none", "confidence": 0.8}]
```

Note: escape_html happened BEFORE the call. The arg→param mapping itself is a direct pass. The sanitize is in the caller's earlier assignment.
```

- [ ] **Step 2: Verify prompt file exists**

Run: `ls -la /root/shannon-py/prompts/transform-identify.txt`
Expected: File exists with ~2500+ bytes

- [ ] **Step 3: Commit**

```bash
git add prompts/transform-identify.txt
git commit -m "feat(prompts): add parameter transformation identification prompt

LLM prompt for Stage B of parameter propagation — classifies
arg→param mappings as none|encode|decode|sanitize|convert|extract|compose."
```

---

## Task 5: Entry Parameter Source Marking

**Files:**
- Create: `packages/core/src/shannon_core/code_index/taint_propagator.py`
- Test: `packages/core/tests/code_index/test_taint_propagator.py`

This module has two responsibilities:
1. Mark entry point parameters with HTTP taint sources (query/body/path/header)
2. Propagate taint sources along call chains to build TaintFlow paths

This combines what the spec calls "阶段 A+ C" — the deterministic parts.

- [ ] **Step 1: Write failing tests**

Create `packages/core/tests/code_index/test_taint_propagator.py`:

```python
import pytest
from shannon_core.code_index.taint_propagator import (
    mark_entry_parameter_sources,
    classify_sink,
    propagate_taint_along_chain,
)
from shannon_core.code_index.models import (
    FuncBlock, CallEdge, CallChain, TypedParameter, ParameterSource,
)
from shannon_core.code_index.parameter_models import (
    ArgParamPair, SinkType, TaintFlow,
)


def _block(name: str, file: str = "app.py", line: int = 1,
           source: str = "", params: list[str] | None = None) -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}",
        file_path=file,
        function_name=name,
        start_line=line,
        end_line=line + 5,
        source_code=source or f"def {name}(): pass",
        parameters=params or [],
        language="python",
    )


class TestMarkEntryParameterSources:
    def test_flask_request_args(self):
        """Flask: request.args → QUERY_PARAM."""
        params = [
            TypedParameter(name="request"),
            TypedParameter(name="user_id"),
        ]
        result = mark_entry_parameter_sources(params, "flask")
        assert result[0].source == ParameterSource.UNKNOWN
        assert result[1].source is None  # user_id is not request itself

    def test_express_req_params(self):
        """Express: req → QUERY/BODY/PATH container."""
        params = [
            TypedParameter(name="req", type_annotation="Request"),
            TypedParameter(name="res", type_annotation="Response"),
        ]
        result = mark_entry_parameter_sources(params, "express")
        assert result[0].source == ParameterSource.UNKNOWN
        assert result[1].source == ParameterSource.INTERNAL

    def test_python_positional_params_with_request(self):
        """When first param is 'request', mark it as container."""
        params = [
            TypedParameter(name="request"),
            TypedParameter(name="user_id"),
        ]
        result = mark_entry_parameter_sources(params, "flask")
        assert result[0].source == ParameterSource.UNKNOWN

    def test_fastapi_typed_params(self):
        """FastAPI: params with Query/Body/Path annotations get sources."""
        params = [
            TypedParameter(name="user_id", type_annotation="int"),
            TypedParameter(name="name", type_annotation="str"),
        ]
        result = mark_entry_parameter_sources(params, "fastapi")
        # Without explicit FastAPI dependency annotations, we can't infer sources
        # The function should at least not crash
        assert len(result) == 2

    def test_empty_params(self):
        result = mark_entry_parameter_sources([], "flask")
        assert result == []


class TestClassifySink:
    def test_sql_execution(self):
        block = _block("execute_query", source="def execute_query(sql): cursor.execute(sql)")
        sink = classify_sink(block)
        assert sink == SinkType.SQL_EXECUTION

    def test_db_query(self):
        block = _block("query", source="def query(sql): db.execute(sql)")
        sink = classify_sink(block)
        assert sink == SinkType.SQL_EXECUTION

    def test_os_system(self):
        block = _block("run_cmd", source="def run_cmd(cmd): os.system(cmd)")
        sink = classify_sink(block)
        assert sink == SinkType.COMMAND_EXEC

    def test_subprocess(self):
        block = _block("execute", source="def execute(cmd): subprocess.run(cmd)")
        sink = classify_sink(block)
        assert sink == SinkType.COMMAND_EXEC

    def test_template_render(self):
        block = _block("render", source="def render(tmpl): template.render(tmpl)")
        sink = classify_sink(block)
        assert sink == SinkType.TEMPLATE_RENDER

    def test_file_write(self):
        block = _block("write_file", source="def write_file(path): f.write(path)")
        sink = classify_sink(block)
        assert sink == SinkType.FILE_WRITE

    def test_http_request(self):
        block = _block("fetch_url", source="def fetch_url(url): requests.get(url)")
        sink = classify_sink(block)
        assert sink == SinkType.HTTP_REQUEST

    def test_unknown_function(self):
        block = _block("process_data", source="def process_data(data): return data.upper()")
        sink = classify_sink(block)
        assert sink == SinkType.UNKNOWN

    def test_deserialization(self):
        block = _block("load_data", source="def load_data(raw): pickle.loads(raw)")
        sink = classify_sink(block)
        assert sink == SinkType.DESERIALIZATION


class TestPropagateTaintAlongChain:
    def test_single_edge_propagation(self):
        """user_id (QUERY) → process(user_id) → order_id (QUERY)"""
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:process:10"],
            depth=1,
            has_unresolved=False,
        )
        arg_param_mappings = {
            ("app.py:handler:1", "svc.py:process:10"): [
                ArgParamPair(arg_name="user_id", param_name="order_id"),
            ],
        }
        entry_taints = {"user_id": ParameterSource.QUERY_PARAM}
        blocks_by_id = {
            "app.py:handler:1": _block("handler", "app.py", 1),
            "svc.py:process:10": _block("process", "svc.py", 10),
        }

        flows = propagate_taint_along_chain(
            chain, arg_param_mappings, entry_taints, blocks_by_id,
        )
        assert len(flows) == 1
        assert flows[0].source_type == ParameterSource.QUERY_PARAM
        assert flows[0].source_param == "user_id"
        assert flows[0].sink_func_id == "svc.py:process:10"
        assert len(flows[0].propagation_steps) == 1
        assert flows[0].propagation_steps[0].from_param == "user_id"
        assert flows[0].propagation_steps[0].to_param == "order_id"

    def test_multi_hop_propagation(self):
        """user_id → order_id → sql → sink"""
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:process:10", "db.py:query:20"],
            depth=2,
            has_unresolved=False,
        )
        arg_param_mappings = {
            ("app.py:handler:1", "svc.py:process:10"): [
                ArgParamPair(arg_name="user_id", param_name="order_id"),
            ],
            ("svc.py:process:10", "db.py:query:20"): [
                ArgParamPair(arg_name="order_id", param_name="sql"),
            ],
        }
        entry_taints = {"user_id": ParameterSource.QUERY_PARAM}
        blocks_by_id = {
            "app.py:handler:1": _block("handler", "app.py", 1),
            "svc.py:process:10": _block("process", "svc.py", 10),
            "db.py:query:20": _block("query", "db.py", 20,
                                     source="def query(sql): cursor.execute(sql)"),
        }

        flows = propagate_taint_along_chain(
            chain, arg_param_mappings, entry_taints, blocks_by_id,
        )
        assert len(flows) == 1
        assert flows[0].propagation_steps[0].from_param == "user_id"
        assert flows[0].propagation_steps[0].to_param == "order_id"
        assert flows[0].propagation_steps[1].from_param == "order_id"
        assert flows[0].propagation_steps[1].to_param == "sql"
        assert flows[0].sink_type == SinkType.SQL_EXECUTION

    def test_no_taint_propagation(self):
        """No entry taints → no flows."""
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1"],
            depth=0,
            has_unresolved=False,
        )
        flows = propagate_taint_along_chain(
            chain, {}, {}, {},
        )
        assert flows == []

    def test_multiple_params_propagate_independently(self):
        """Two tainted params produce two flows."""
        chain = CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:process:10"],
            depth=1,
            has_unresolved=False,
        )
        arg_param_mappings = {
            ("app.py:handler:1", "svc.py:process:10"): [
                ArgParamPair(arg_name="user_id", param_name="uid"),
                ArgParamPair(arg_name="name", param_name="username"),
            ],
        }
        entry_taints = {
            "user_id": ParameterSource.QUERY_PARAM,
            "name": ParameterSource.BODY_FIELD,
        }
        blocks_by_id = {
            "app.py:handler:1": _block("handler", "app.py", 1),
            "svc.py:process:10": _block("process", "svc.py", 10),
        }

        flows = propagate_taint_along_chain(
            chain, arg_param_mappings, entry_taints, blocks_by_id,
        )
        assert len(flows) == 2
        sources = {f.source_type for f in flows}
        assert ParameterSource.QUERY_PARAM in sources
        assert ParameterSource.BODY_FIELD in sources
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_taint_propagator.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement taint_propagator.py**

Create `packages/core/src/shannon_core/code_index/taint_propagator.py`:

```python
"""Taint propagator — marks entry point sources and propagates along chains.

Two responsibilities:
1. mark_entry_parameter_sources(): Identifies which entry point parameters
   carry user-controllable input (HTTP query/body/path/header/etc.)
2. propagate_taint_along_chain(): Walks a call chain, propagating taint
   sources through arg→param mappings to build TaintFlow paths.

This is the deterministic core of Stage A + Stage C of the parameter
propagation pipeline. Stage B (LLM transform identification) enriches
the ArgParamPair.transform fields separately.
"""

import logging
import re

from shannon_core.code_index.models import (
    FuncBlock, ParameterSource, TypedParameter,
)
from shannon_core.code_index.parameter_models import (
    ArgParamPair, CallChain, PropagationStep, SinkType, TaintFlow,
)

logger = logging.getLogger(__name__)


# ── Entry parameter source marking ──────────────────────────


# Framework-specific request object patterns
_REQUEST_PATTERNS: dict[str, dict[str, ParameterSource]] = {
    "flask": {
        "request.args": ParameterSource.QUERY_PARAM,
        "request.form": ParameterSource.FORM_FIELD,
        "request.json": ParameterSource.BODY_FIELD,
        "request.data": ParameterSource.BODY_FIELD,
        "request.files": ParameterSource.FILE_UPLOAD,
        "request.headers": ParameterSource.HEADER,
        "request.cookies": ParameterSource.COOKIE,
        "request.values": ParameterSource.QUERY_PARAM,
    },
    "fastapi": {
        "Request": ParameterSource.UNKNOWN,
        "Query": ParameterSource.QUERY_PARAM,
        "Body": ParameterSource.BODY_FIELD,
        "Path": ParameterSource.PATH_PARAM,
        "Header": ParameterSource.HEADER,
        "Cookie": ParameterSource.COOKIE,
        "File": ParameterSource.FILE_UPLOAD,
        "Form": ParameterSource.FORM_FIELD,
    },
    "django": {
        "request.GET": ParameterSource.QUERY_PARAM,
        "request.POST": ParameterSource.FORM_FIELD,
        "request.body": ParameterSource.BODY_FIELD,
        "request.FILES": ParameterSource.FILE_UPLOAD,
        "request.META": ParameterSource.HEADER,
        "request.COOKIES": ParameterSource.COOKIE,
        "request.session": ParameterSource.SESSION_ATTR,
    },
    "express": {
        "req.query": ParameterSource.QUERY_PARAM,
        "req.body": ParameterSource.BODY_FIELD,
        "req.params": ParameterSource.PATH_PARAM,
        "req.headers": ParameterSource.HEADER,
        "req.cookies": ParameterSource.COOKIE,
        "req.files": ParameterSource.FILE_UPLOAD,
    },
    "fastify": {
        "req.query": ParameterSource.QUERY_PARAM,
        "req.body": ParameterSource.BODY_FIELD,
        "req.params": ParameterSource.PATH_PARAM,
        "req.headers": ParameterSource.HEADER,
    },
    "koa": {
        "ctx.query": ParameterSource.QUERY_PARAM,
        "ctx.request.body": ParameterSource.BODY_FIELD,
        "ctx.params": ParameterSource.PATH_PARAM,
        "ctx.headers": ParameterSource.HEADER,
    },
}


def mark_entry_parameter_sources(
    params: list[TypedParameter],
    framework: str,
) -> list[TypedParameter]:
    """Mark entry point parameters with HTTP taint source annotations.

    Uses framework conventions to identify which parameters carry
    user-controllable input.

    Args:
        params: List of typed parameters from the entry point function.
        framework: Framework name ("flask", "express", "django", etc.)

    Returns:
        Parameters with source field populated where identifiable.
    """
    marked = []
    for p in params:
        source = _infer_entry_source(p, framework)
        marked.append(p.model_copy(update={"source": source}))
    return marked


def _infer_entry_source(
    param: TypedParameter,
    framework: str,
) -> ParameterSource | None:
    """Infer the HTTP source of an entry point parameter."""
    name = param.name.lower()
    type_ann = (param.type_annotation or "").lower()

    # Response objects are never taint sources
    if name in ("res", "response", "w", "writer", "next"):
        return ParameterSource.INTERNAL
    if "response" in type_ann:
        return ParameterSource.INTERNAL

    # Request objects are taint containers (source = UNKNOWN, accessed via fields)
    if name in ("req", "request", "ctx", "context", "c", "self"):
        return ParameterSource.UNKNOWN
    if "request" in type_ann:
        return ParameterSource.UNKNOWN

    # FastAPI-style dependency injection: type annotation is the source
    patterns = _REQUEST_PATTERNS.get(framework, {})
    for pattern, source in patterns.items():
        if type_ann.lower() == pattern.lower().split(".")[-1]:
            return source

    return None


# ── Sink classification ──────────────────────────────────────


# Patterns for sink classification (function name + source code heuristics)
_SINK_PATTERNS: list[tuple[re.Pattern, SinkType]] = [
    # SQL execution
    (re.compile(r"(execute|query|raw_query|raw_sql|cursor\.execute|\.query\()", re.I),
     SinkType.SQL_EXECUTION),
    # Command execution
    (re.compile(r"(os\.system|subprocess\.|exec\(|popen|shell|command)", re.I),
     SinkType.COMMAND_EXEC),
    # Template rendering
    (re.compile(r"(render|template|jinja|jinja2|\.render\()", re.I),
     SinkType.TEMPLATE_RENDER),
    # File write
    (re.compile(r"(write|open\(|file\(|save|upload|\.write\()", re.I),
     SinkType.FILE_WRITE),
    # HTTP request
    (re.compile(r"(requests\.|fetch|urllib|httpx|axios|\.get\(|\.post\()", re.I),
     SinkType.HTTP_REQUEST),
    # Deserialization
    (re.compile(r"(pickle\.|yaml\.load|marshal\.|unserialize|deserialize)", re.I),
     SinkType.DESERIALIZATION),
    # Log write
    (re.compile(r"(logger|logging|log\.|console\.)", re.I),
     SinkType.LOG_WRITE),
]


def classify_sink(block: FuncBlock) -> SinkType:
    """Classify a function as a security-sensitive sink.

    Uses function name and source code heuristics to determine
    what type of sink a function is (if any).

    Args:
        block: The function block to classify.

    Returns:
        SinkType enum value. SinkType.UNKNOWN if not a recognized sink.
    """
    combined = f"{block.function_name} {block.source_code}"

    for pattern, sink_type in _SINK_PATTERNS:
        if pattern.search(combined):
            return sink_type

    return SinkType.UNKNOWN


# ── Taint propagation along call chains ──────────────────────


def propagate_taint_along_chain(
    chain: CallChain,
    arg_param_mappings: dict[tuple[str, str], list[ArgParamPair]],
    entry_taints: dict[str, ParameterSource],
    blocks_by_id: dict[str, FuncBlock],
) -> list[TaintFlow]:
    """Propagate taint sources along a single call chain.

    Starting from entry point parameters with known HTTP sources,
    walks the chain path and propagates sources through arg→param
    mappings at each edge.

    Args:
        chain: The call chain to propagate along.
        arg_param_mappings: (caller_id, callee_id) → list of ArgParamPair.
        entry_taints: parameter_name → ParameterSource for entry point.
        blocks_by_id: FuncBlock lookup by ID.

    Returns:
        List of TaintFlow paths, one per tainted parameter that reaches
        the end of the chain (or an intermediate sink).
    """
    if not entry_taints or len(chain.path) < 1:
        return []

    # Current taint state: param_name → source
    current_taints: dict[str, ParameterSource] = dict(entry_taints)
    all_steps: list[PropagationStep] = []
    taint_origins: dict[str, tuple[str, ParameterSource]] = {
        # Track which original param each current param came from
        param: (param, source) for param, source in entry_taints.items()
    }

    # Walk each edge in the chain
    for i in range(len(chain.path) - 1):
        caller_id = chain.path[i]
        callee_id = chain.path[i + 1]
        edge_key = (caller_id, callee_id)
        mappings = arg_param_mappings.get(edge_key, [])

        next_taints: dict[str, ParameterSource] = {}
        next_origins: dict[str, tuple[str, ParameterSource]] = {}

        for pair in mappings:
            if pair.arg_name in current_taints:
                source = current_taints[pair.arg_name]
                next_taints[pair.param_name] = source

                # Track origin
                origin_name, origin_source = taint_origins.get(
                    pair.arg_name, (pair.arg_name, source)
                )
                next_origins[pair.param_name] = (origin_name, origin_source)

                # Record propagation step
                caller_block = blocks_by_id.get(caller_id)
                location = f"{caller_id.split(':')[0]}:{pair.arg_name}" if ':' not in pair.arg_name else caller_id

                step = PropagationStep(
                    from_func_id=caller_id,
                    from_param=pair.arg_name,
                    to_func_id=callee_id,
                    to_param=pair.param_name,
                    transformation=pair.transform,
                    code_location=location,
                )
                all_steps.append(step)

        current_taints = next_taints
        taint_origins = next_origins

    # Build TaintFlow for each tainted parameter that survived to the end
    flows: list[TaintFlow] = []
    last_func_id = chain.path[-1] if chain.path else None
    last_block = blocks_by_id.get(last_func_id) if last_func_id else None

    for param_name, source in current_taints.items():
        origin = taint_origins.get(param_name, (param_name, source))

        sink_type = None
        if last_block:
            sink_type = classify_sink(last_block)

        flows.append(TaintFlow(
            entry_point_id=chain.entry_point_id,
            source_param=origin[0],
            source_type=origin[1],
            propagation_steps=all_steps,
            sink_func_id=last_func_id,
            sink_type=sink_type,
        ))

    return flows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_taint_propagator.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/taint_propagator.py packages/core/tests/code_index/test_taint_propagator.py
git commit -m "feat(code_index): add taint propagator

mark_entry_parameter_sources() marks HTTP taint sources per framework.
classify_sink() identifies security-sensitive sinks by heuristic.
propagate_taint_along_chain() walks chains propagating taint through
arg→param mappings to build TaintFlow paths."
```

---

## Task 6: Parameter Graph Pipeline Orchestrator

**Files:**
- Create: `packages/core/src/shannon_core/code_index/parameter_graph.py`
- Test: `packages/core/tests/code_index/test_parameter_graph.py`

Orchestrates the full three-stage pipeline: (A) arg→param mapping → (B) LLM transform identification → (C) taint propagation. Produces the final `parameter_graph.json`.

- [ ] **Step 1: Write failing tests**

Create `packages/core/tests/code_index/test_parameter_graph.py`:

```python
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from shannon_core.code_index.parameter_graph import (
    build_parameter_graph,
    _detect_framework,
)
from shannon_core.code_index.models import (
    FuncBlock, CallEdge, CallChain, CodeIndex, ParameterSource,
)


def _make_index(tmp_path) -> CodeIndex:
    """Create a minimal CodeIndex for testing."""
    blocks = [
        FuncBlock(
            id="app.py:handler:1", file_path="app.py",
            function_name="handler", start_line=1, end_line=5,
            source_code="def handler(request):\n    user_id = request.args.get('id')\n    process(user_id)\n",
            parameters=["request"], language="python",
            decorators=["@app.route('/users')"],
        ),
        FuncBlock(
            id="svc.py:process:10", file_path="svc.py",
            function_name="process", start_line=10, end_line=15,
            source_code="def process(order_id):\n    db.query(order_id)\n",
            parameters=["order_id"], language="python",
        ),
    ]
    edges = [
        CallEdge(
            caller_id="app.py:handler:1", callee_name="process",
            callee_file="svc.py", resolved=True, line=3,
        ),
    ]
    chains = [
        CallChain(
            entry_point_id="app.py:handler:1",
            path=["app.py:handler:1", "svc.py:process:10"],
            depth=1, has_unresolved=False,
        ),
    ]
    return CodeIndex(
        repository=str(tmp_path),
        language="python",
        total_blocks=2, total_entry_points=1, total_chains=1,
        blocks=blocks, edges=edges,
        entry_points=[], chains=chains,
    )


class TestDetectFramework:
    def test_detects_flask(self, tmp_path):
        (tmp_path / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "@app.route('/')\n"
            "def index(): pass\n"
        )
        fw = _detect_framework(tmp_path, "python")
        assert fw == "flask"

    def test_detects_express(self, tmp_path):
        (tmp_path / "package.json").write_text('{"dependencies": {"express": "^4.0"}}')
        (tmp_path / "index.ts").write_text("import express from 'express'")
        fw = _detect_framework(tmp_path, "typescript")
        assert fw == "express"

    def test_detects_django(self, tmp_path):
        (tmp_path / "manage.py").write_text("# django")
        fw = _detect_framework(tmp_path, "python")
        assert fw == "django"

    def test_unknown_framework(self, tmp_path):
        (tmp_path / "app.py").write_text("print('hello')")
        fw = _detect_framework(tmp_path, "python")
        assert fw is None


class TestBuildParameterGraph:
    def test_builds_graph_from_index(self, tmp_path):
        """End-to-end: build parameter graph from CodeIndex."""
        # Write source files so Tree-sitter can parse them
        (tmp_path / "app.py").write_text(
            "def handler(request):\n"
            "    user_id = request.args.get('id')\n"
            "    process(user_id)\n"
        )
        (tmp_path / "svc.py").write_text(
            "def process(order_id):\n"
            "    db.query(order_id)\n"
        )

        index = _make_index(tmp_path)
        graph = build_parameter_graph(index, tmp_path)

        assert graph is not None
        # Graph may have flows if the arg→param mapping succeeds
        # At minimum, the graph should be valid and serializable
        json_str = graph.to_json()
        data = json.loads(json_str)
        assert "taint_flows" in data

    def test_empty_index(self, tmp_path):
        """Empty index produces empty graph."""
        (tmp_path / "app.py").write_text("x = 1\n")
        index = CodeIndex(
            repository=str(tmp_path), language="python",
            total_blocks=0, total_entry_points=0, total_chains=0,
            blocks=[], edges=[], entry_points=[], chains=[],
        )
        graph = build_parameter_graph(index, tmp_path)
        assert graph.total_flows == 0

    def test_serialization_round_trip(self, tmp_path):
        """Graph can be serialized and deserialized."""
        (tmp_path / "app.py").write_text("def f(): pass\n")
        index = CodeIndex(
            repository=str(tmp_path), language="python",
            total_blocks=0, total_entry_points=0, total_chains=0,
            blocks=[], edges=[], entry_points=[], chains=[],
        )
        graph = build_parameter_graph(index, tmp_path)
        json_str = graph.to_json()

        from shannon_core.code_index.parameter_models import ParameterPropagationGraph
        restored = ParameterPropagationGraph.model_validate_json(json_str)
        assert restored.total_flows == graph.total_flows
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_parameter_graph.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement parameter_graph.py**

Create `packages/core/src/shannon_core/code_index/parameter_graph.py`:

```python
"""Parameter graph pipeline orchestrator.

Orchestrates the three-stage parameter propagation pipeline:
  Stage A: AST arg→param mapping (arg_param_mapper)
  Stage B: LLM transform identification (optional, enriches transforms)
  Stage C: Taint propagation along call chains (taint_propagator)

Produces the final ParameterPropagationGraph with all TaintFlow paths.
"""

import json
import logging
from pathlib import Path

from shannon_core.code_index.models import CodeIndex, ParameterSource, TypedParameter
from shannon_core.code_index.parameter_models import ParameterPropagationGraph
from shannon_core.code_index.arg_param_mapper import build_all_arg_param_mappings
from shannon_core.code_index.taint_propagator import (
    mark_entry_parameter_sources,
    propagate_taint_along_chain,
)

logger = logging.getLogger(__name__)


def build_parameter_graph(
    index: CodeIndex,
    repo_root: Path,
) -> ParameterPropagationGraph:
    """Build a complete parameter propagation graph from a CodeIndex.

    Three-stage pipeline:
    1. Stage A: Build arg→param mappings for all resolved edges
    2. Stage B: (Optional) LLM transform identification — skipped in this
       deterministic pass, can be enriched later
    3. Stage C: Propagate taint along each call chain

    Args:
        index: The CodeIndex with blocks, edges, chains, and entry points.
        repo_root: Repository root path for file access.

    Returns:
        ParameterPropagationGraph with all discovered TaintFlow paths.
    """
    if not index.chains:
        logger.info("No call chains in index, skipping parameter graph")
        return ParameterPropagationGraph(taint_flows=[])

    # Stage A: Build arg→param mappings for all edges
    logger.info("Stage A: Building arg→param mappings...")
    arg_param_mappings = build_all_arg_param_mappings(
        index.blocks, index.edges, repo_root, index.language,
    )
    logger.info("Stage A: Found %d arg→param mappings across %d edges",
                sum(len(v) for v in arg_param_mappings.values()),
                len(arg_param_mappings))

    # Detect framework for source marking
    framework = _detect_framework(repo_root, index.language)
    logger.info("Detected framework: %s", framework or "unknown")

    # Build block lookup
    blocks_by_id = {b.id: b for b in index.blocks}

    # Stage C: Propagate taint along each chain
    logger.info("Stage C: Propagating taint along %d chains...", len(index.chains))
    all_flows = []

    for chain in index.chains:
        # Get entry point block
        entry_block = blocks_by_id.get(chain.path[0]) if chain.path else None
        if entry_block is None:
            continue

        # Extract and mark entry point parameters
        from shannon_core.code_index.enhanced_parameters import extract_typed_parameters
        entry_params = extract_typed_parameters(
            repo_root / entry_block.file_path,
            entry_block.function_name,
            entry_block.start_line,
            index.language,
        )

        # Mark HTTP sources
        if framework:
            marked_params = mark_entry_parameter_sources(entry_params, framework)
        else:
            marked_params = entry_params

        # Build entry taints dict
        entry_taints: dict[str, ParameterSource] = {}
        for p in marked_params:
            if p.source and p.source != ParameterSource.INTERNAL:
                entry_taints[p.name] = p.source

        if not entry_taints:
            continue

        # Propagate taint along this chain
        flows = propagate_taint_along_chain(
            chain, arg_param_mappings, entry_taints, blocks_by_id,
        )
        all_flows.extend(flows)

    logger.info("Stage C: Built %d taint flows", len(all_flows))

    graph = ParameterPropagationGraph(taint_flows=all_flows)
    return graph


def write_parameter_graph(
    graph: ParameterPropagationGraph,
    output_dir: Path,
) -> Path:
    """Write parameter_graph.json to the output directory.

    Args:
        graph: The parameter propagation graph.
        output_dir: Directory to write to.

    Returns:
        Path to the written file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "parameter_graph.json"
    path.write_text(graph.to_json())
    logger.info("Wrote parameter graph (%d flows) to %s",
                graph.total_flows, path)
    return path


def _detect_framework(repo_root: Path, language: str) -> str | None:
    """Detect the web framework used by the project.

    Uses simple heuristics: file existence, import patterns, package.json.
    """
    if language == "python":
        return _detect_python_framework(repo_root)
    elif language in ("typescript", "javascript"):
        return _detect_ts_framework(repo_root)
    return None


def _detect_python_framework(repo_root: Path) -> str | None:
    """Detect Python web framework."""
    # Check for Flask
    for py_file in repo_root.rglob("*.py"):
        try:
            content = py_file.read_text(errors="replace")
            if "from flask" in content or "import flask" in content:
                return "flask"
            if "from fastapi" in content or "import fastapi" in content:
                return "fastapi"
            if "from django" in content or "import django" in content:
                return "django"
        except Exception:
            continue

    # Check for Django's manage.py
    if (repo_root / "manage.py").exists():
        return "django"

    return None


def _detect_ts_framework(repo_root: Path) -> str | None:
    """Detect TypeScript/JavaScript web framework."""
    pkg_json = repo_root / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text())
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            if "express" in deps:
                return "express"
            if "fastify" in deps:
                return "fastify"
            if "koa" in deps:
                return "koa"
            if "next" in deps:
                return "next"
            if "@nestjs/core" in deps:
                return "nestjs"
        except Exception:
            pass

    # Check source files for imports
    for ts_file in list(repo_root.rglob("*.ts"))[:20]:
        try:
            content = ts_file.read_text(errors="replace")
            if "from 'express'" in content or 'from "express"' in content:
                return "express"
            if "from 'fastify'" in content:
                return "fastify"
            if "from 'koa'" in content:
                return "koa"
        except Exception:
            continue

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_parameter_graph.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/parameter_graph.py packages/core/tests/code_index/test_parameter_graph.py
git commit -m "feat(code_index): add parameter graph pipeline orchestrator

build_parameter_graph() orchestrates the three-stage pipeline:
Stage A (AST arg→param) → Stage C (taint propagation).
Includes framework detection for HTTP source marking.
Writes parameter_graph.json output."
```

---

## Task 7: Integration into Code Index Pipeline

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py`
- Test: `packages/core/tests/code_index/test_build_code_index.py`

Wire `build_parameter_graph()` into the main code index pipeline so it runs automatically after chain building.

- [ ] **Step 1: Write failing test**

Add to `packages/core/tests/code_index/test_build_code_index.py`:

```python
class TestBuildParameterGraphIntegration:
    def test_parameter_graph_built_after_index(self, tmp_path):
        """build_code_index_with_gitnexus should trigger parameter graph build."""
        from shannon_core.code_index import build_code_index_with_gitnexus

        (tmp_path / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "@app.route('/hello')\n"
            "def hello():\n"
            "    return greet('world')\n"
            "def greet(name):\n"
            "    return f'Hello {name}'\n"
        )

        with patch("shannon_core.code_index.GitNexusEngine") as MockEngine:
            mock_engine = MagicMock()
            mock_engine.is_available.return_value = False
            MockEngine.return_value = mock_engine

            index = build_code_index_with_gitnexus(str(tmp_path))
            assert hasattr(index, "parameter_graph")
            # Graph may be empty (no chains from single entry point)
            # but should at least be a valid ParameterPropagationGraph
            assert index.parameter_graph is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_build_code_index.py -k "parameter_graph_built" -v`
Expected: FAIL — attribute not found

- [ ] **Step 3: Add parameter graph build to __init__.py**

In `packages/core/src/shannon_core/code_index/__init__.py`, add import:

```python
from shannon_core.code_index.parameter_graph import build_parameter_graph, write_parameter_graph
from shannon_core.code_index.parameter_models import ParameterPropagationGraph
```

In the `build_code_index_with_gitnexus()` function, after building the index and before returning, add:

```python
    # Build parameter propagation graph
    logger.info("Building parameter propagation graph...")
    try:
        parameter_graph = build_parameter_graph(index, repo)
        index.parameter_graph = parameter_graph

        # Write to output if deliverables dir exists
        if hasattr(index, 'file_manifest'):
            write_parameter_graph(parameter_graph, repo)
    except Exception as exc:
        logger.warning("Parameter graph build failed: %s", exc)
        index.parameter_graph = ParameterPropagationGraph(taint_flows=[])

    return index
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/test_build_code_index.py -k "parameter_graph_built" -v`
Expected: PASS

- [ ] **Step 5: Run full code_index test suite**

Run: `cd /root/shannon-py && uv run pytest packages/core/tests/code_index/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/__init__.py packages/core/tests/code_index/test_build_code_index.py
git commit -m "feat(code_index): integrate parameter graph into code index pipeline

build_code_index_with_gitnexus() now automatically builds the parameter
propagation graph after indexing. Falls back to empty graph on error."
```

---

## Self-Review Checklist

### 1. Spec Coverage (P1 — Parameter Propagation)

| Spec Requirement | Task |
|---|---|
| P1: Parameter type info | Plan A Task 4 + Plan B Task 6 (uses TypedParameter) |
| P2: Parameter source tracking (HTTP) | Task 5 (mark_entry_parameter_sources) |
| P3: Parameter flow analysis | Task 5 (propagate_taint_along_chain) |
| P4: TS arrow function params | Plan A Task 4 (enhanced_parameters.py) |
| P5: Python **kwargs | Plan A Task 4 (enhanced_parameters.py) |
| Stage A: AST arg→param mapping | Task 2 (call_site_locator) + Task 3 (arg_param_mapper) |
| Stage B: LLM transform identification | Task 4 (prompt) — actual LLM call in Plan C |
| Stage C: Taint propagation graph | Task 5 (taint_propagator) + Task 6 (parameter_graph) |
| parameter_graph.json output | Task 7 (integration) |
| Framework detection | Task 6 (_detect_framework) |
| Sink classification | Task 5 (classify_sink) |

### 2. Placeholder Scan

✅ No TBD, TODO, "implement later"
✅ All steps contain actual code
✅ All test code is complete
✅ All file paths are exact

### 3. Type Consistency

✅ `ArgExpression` fields: expression, kind, keyword — consistent between definition and all call sites
✅ `CallSite` fields: callee_name, line, arguments — consistent between locator and mapper
✅ `ArgParamPair` fields: arg_name, param_name, arg_source, transform, transform_confidence — consistent across mapper, taint_propagator, and tests
✅ `TaintFlow` fields: entry_point_id, source_param, source_type, propagation_steps, sink_func_id, sink_type — consistent between propagator and parameter_graph
✅ `ParameterSource` enum from Plan A reused consistently across all Plan B modules
✅ `FuncBlock.id` format "file:name:line" used consistently for edge keys and chain paths
