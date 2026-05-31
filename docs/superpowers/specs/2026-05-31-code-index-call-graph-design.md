# Code Index & Call Graph Integration Design

## Problem

Shannon's white-box audit pipeline relies entirely on LLM agents to discover entry points, trace call chains, and identify parameters. This creates two structural gaps:

1. **No complete call chains** — Agents explore code by reading files with Task Agents, but there is no deterministic call graph. The LLM may miss indirect calls (e.g., `routes/order.py → services/order.py → utils/db.py`) because no BFS traversal guarantees coverage.

2. **No parameter coverage guarantee** — There is no registry of all functions and their parameters. If PRE_RECON omits an injection source, it vanishes from the entire pipeline. There is no way to measure "audited parameters / total parameters."

SCR-AI solves these gaps with tree-sitter + BFS, but its analysis depth is shallow (generic prompts, no slot-type theory, no sanitization-context matching).

## Goal

Integrate SCR-AI's deterministic call graph construction into Shannon's pipeline as a new Phase before PRE_RECON. The call graph provides a provably-complete function registry and call chain set. PRE_RECON and downstream agents retain their deep analysis prompts, but now operate on a known-complete foundation.

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Where to put the code | `shannon-core` package | Infrastructure-level capability, shared by whitebox and blackbox |
| Pipeline position | New Temporal Activity before PRE_RECON | Deterministic, testable, no LLM dependency |
| Data injection method | File-based (`code_index.json` + `code_index_summary.md`) | No prompt length risk for large codebases |
| Downstream access | Only through PRE_RECON's deliverable | PRE_RECON does reasoned filtering, not accidental omission |
| Entry point detection | Deterministic rules (>=0.8 confidence) + low-confidence flagged for LLM review | Rules handle known frameworks; fuzzy cases get `needs_llm_review=True` |
| Language support | Python, TypeScript, Go, Java, PHP | All have mature tree-sitter grammars |
| Call graph algorithm | BFS, max_depth=15, max_width=50 | Consistent with SCR-AI's proven parameters |

## Architecture

### Pipeline Flow

```
Before:  Preflight → PRE_RECON → RECON → [6× Vuln Agents]

After:   Preflight → CODE_INDEX → PRE_RECON → RECON → [6× Vuln Agents]
                         │
                         ▼
               code_index.json
               code_index_summary.md
```

### Module Structure

```
packages/core/src/shannon_core/
  code_index/
    __init__.py            # public API: build_code_index()
    models.py              # FuncBlock, CallEdge, EntryPoint, CallChain, CodeIndex
    parser.py              # language detection + file discovery
    call_graph.py          # BFS call graph construction
    entry_points.py        # per-language deterministic rules
    parsers/
      __init__.py
      base.py              # BaseParser ABC
      python_parser.py     # tree-sitter Python
      typescript_parser.py # tree-sitter TS/TSX
      go_parser.py         # tree-sitter Go
      java_parser.py       # tree-sitter Java
      php_parser.py        # tree-sitter PHP
```

### Whitebox Pipeline Changes

```
packages/whitebox/src/shannon_whitebox/
  pipeline/
    activities.py          # + run_code_index activity
    workflows.py           # CODE_INDEX phase inserted before PRE_RECON
    shared.py              # PipelineState.code_index_stats field added
```

### Prompt Changes

`prompts/pre-recon-code.txt` — new `<starting_context>` section:

```xml
<starting_context>
- A complete call graph has been built via AST analysis, located at:
  .shannon/deliverables/code_index.json
  .shannon/deliverables/code_index_summary.md
- The call graph contains {N} function blocks, {M} entry points, {K} call chains
- You do NOT need to discover entry points yourself — all are deterministically extracted
- Focus on understanding the security semantics and attack surface of each entry point
- Entry points marked needs_llm_review=true require your judgment on whether they are real entry points
</starting_context>
```

## Data Models

### FuncBlock

```python
class FuncBlock(BaseModel):
    id: str                    # "file_path:function_name:start_line"
    file_path: str
    function_name: str
    start_line: int
    end_line: int
    source_code: str
    parameters: list[str]
    class_name: str | None = None
    decorators: list[str] = []
    language: str              # "python" | "go" | "typescript" | "java" | "php"
```

### CallEdge

```python
class CallEdge(BaseModel):
    caller_id: str             # FuncBlock.id
    callee_name: str           # called function name
    callee_file: str | None = None
    resolved: bool             # whether callee was found in known blocks
    line: int                  # line number of the call
```

### EntryPoint

```python
class EntryPoint(BaseModel):
    func_block_id: str
    entry_type: str            # "http_route" | "rpc" | "cli" | "message_consumer" | ...
    route: str | None = None
    http_method: str | None = None
    confidence: float
    evidence: str
    needs_llm_review: bool     # True when confidence < 0.8
```

### CallChain

```python
class CallChain(BaseModel):
    entry_point_id: str
    path: list[str]            # ordered list of FuncBlock.id
    depth: int
    has_unresolved: bool       # path contains unresolved calls
```

### CodeIndex

```python
class CodeIndex(BaseModel):
    repository: str
    language: str
    total_blocks: int
    total_entry_points: int
    total_chains: int
    blocks: list[FuncBlock]
    edges: list[CallEdge]
    entry_points: list[EntryPoint]
    chains: list[CallChain]
```

## Output Files

### code_index.json

Full structured data — the `CodeIndex` model serialized as JSON. Consumed by tooling or for debugging. Contains all blocks, edges, entry points, and chains.

### code_index_summary.md

Human/LLM-readable summary with three sections:

1. **Entry Points table** — endpoint, method, function, file:line, confidence
2. **Entry Points Needing Review** — functions where confidence < 0.8, with evidence for LLM to judge
3. **Coverage Metrics** — resolved vs unresolved edges, max chain depth, unreachable functions count

The summary file is what PRE_RECON's prompt references directly.

## Multi-Language Parser Strategy

Each language has a dedicated parser implementing `BaseParser`:

```python
class BaseParser(ABC):
    @abstractmethod
    def parse_file(self, file_path: Path, repo_root: Path) -> list[FuncBlock]: ...

    @abstractmethod
    def extract_calls(self, block: FuncBlock, source: bytes) -> list[CallEdge]: ...
```

| Language | Function Extraction | Call Extraction | Unresolved Patterns |
|---|---|---|---|
| Python | `function_definition`, `async_function_definition`, decorators | `func_name(args)`, `obj.method(args)` | Dynamic dispatch, `getattr()` |
| Go | `function_declaration`, `method_declaration` (receiver) | `pkg.Func()`, `s.Method()` | Interface calls |
| TS/JS | `function_declaration`, `method_definition`, arrow functions, callbacks | `func()`, `obj.method()`, `router.get(path, handler)` | Dynamic property access |
| Java | `method_declaration`, annotations | `obj.method()`, `Class.method()` | DI, reflection, Spring proxies |
| PHP | `function_definition`, class methods, `__invoke` | `$func()`, `$obj->method()` | `$obj->$var()`, dynamic calls |

Language detection uses file extension counting. Only the detected primary language's parser runs.

## Entry Point Rules

Per-language rule sets match decorators, parameter signatures, and function name patterns. Rules with confidence >= 0.8 are accepted deterministically. Rules with confidence < 0.8 set `needs_llm_review=True` for PRE_RECON to decide.

### Python

| Rule | Pattern | Confidence | LLM Review |
|---|---|---|---|
| Flask route | `@app.route(`, `@blueprint.route(` | 0.95 | No |
| FastAPI route | `@router.(get|post|put|delete|patch)(` | 0.95 | No |
| Django view | `@api_view(`, `@require_http_methods(` | 0.90 | No |
| Celery task | `@celery.task`, `@app.task`, `@shared_task` | 0.90 | No |
| Async undecorated | `async def` with no known decorator | 0.30 | Yes |

### Go

| Rule | Pattern | Confidence | LLM Review |
|---|---|---|---|
| net/http handler | params: `http.ResponseWriter, *http.Request` | 0.95 | No |
| Gin handler | params: `*gin.Context` | 0.95 | No |
| gRPC method | method on gRPC service interface struct | 0.85 | No |
| main function | `func main()` | 0.30 | Yes |

### TypeScript

| Rule | Pattern | Confidence | LLM Review |
|---|---|---|---|
| Express route | `router.(get|post|put|delete|patch)(path, handler)` | 0.95 | No |
| NestJS decorator | `@Get(`, `@Post(`, `@Put(`, `@Delete(` | 0.95 | No |
| AWS Lambda | `export handler`, `export const handler` | 0.90 | No |
| Anonymous callback | anonymous function in router call | 0.70 | Yes |

### Java

| Rule | Pattern | Confidence | LLM Review |
|---|---|---|---|
| Spring controller | `@RequestMapping`, `@GetMapping`, `@PostMapping`, etc. | 0.95 | No |
| JAX-RS | `@GET`, `@POST`, `@PUT`, `@DELETE`, `@Path` | 0.95 | No |
| RabbitMQ listener | `@RabbitListener` | 0.90 | No |
| Servlet do-method | `do(Get|Post|Put|Delete)` on `HttpServlet` subclass | 0.85 | Yes |

### PHP

| Rule | Pattern | Confidence | LLM Review |
|---|---|---|---|
| Laravel route | `Route::(get|post|put|delete|patch)` | 0.95 | No |
| Symfony route | `#[Route(`] | 0.95 | No |
| Laravel controller | public method in `*Controller` class | 0.60 | Yes |
| WordPress action | `add_action(`, `add_filter(` | 0.70 | Yes |

## Dependencies

```toml
# packages/core/pyproject.toml additions
dependencies = [
    # ... existing ...
    "tree-sitter>=0.24",
    "tree-sitter-python>=0.23",
    "tree-sitter-typescript>=0.23",
    "tree-sitter-go>=0.23",
    "tree-sitter-java>=0.23",
    "tree-sitter-php>=0.23",
]
```

## Error Handling

- **No source files found** — raise `PentestError(CODE_INDEX_FAILED)` with message indicating language detection failure
- **tree-sitter parse error on a file** — skip that file, log warning, continue with remaining files
- **No entry points detected** — proceed with empty entry_points list; PRE_RECON will note this and do its own discovery
- **CODE_INDEX activity timeout** — 10 minute `start_to_close_timeout`; large repos may need tuning
- **Unresolved calls** — not an error; tracked as `has_unresolved=True` on affected chains, surfaced to PRE_RECON via summary

## Testing Strategy

| Test Type | Scope | Key Assertions |
|---|---|---|
| Unit tests per parser | Each parser extracts correct FuncBlocks from sample code | block count, parameter names, decorator strings |
| Unit tests for call extraction | Each parser identifies call edges from source | caller/callee names, resolved flag |
| Unit tests for entry point rules | Rules match decorators/signatures correctly | confidence levels, needs_llm_review flags |
| Integration test for build_code_index | Full pipeline on a fixture repo | total_blocks, total_chains, chain completeness |
| Integration test for workflow | CODE_INDEX → PRE_RECON handoff | code_index_summary.md exists and contains expected sections |
| Regression test on real codebases | Python (Flask/Django/FastAPI), Go (net/http/Gin), TS (Express/NestJS), Java (Spring), PHP (Laravel/Symfony) | framework-specific entry points detected correctly |
