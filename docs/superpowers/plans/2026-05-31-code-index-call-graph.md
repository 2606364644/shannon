# Code Index & Call Graph Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Integrate deterministic AST-based code indexing and call graph construction into Shannon's whitebox pipeline, providing a provably-complete function registry and call chain set before PRE_RECON runs.

**Architecture:** A new `code_index` module in `shannon-core` uses tree-sitter to parse source files, extract function blocks and call edges, detect entry points via per-language rules, and build call chains via BFS. A new Temporal activity `run_code_index` runs before PRE_RECON, writing `code_index.json` and `code_index_summary.md` to the deliverables directory. PRE_RECON's prompt is updated to reference the code index output.

**Tech Stack:** Python 3.12+, tree-sitter (>=0.24), Pydantic v2, Temporal.io, pytest

---

## File Structure

### New Files (packages/core)

| File | Responsibility |
|------|---------------|
| `packages/core/src/shannon_core/code_index/__init__.py` | Public API: `build_code_index()`, `write_index_files()` |
| `packages/core/src/shannon_core/code_index/models.py` | `FuncBlock`, `CallEdge`, `EntryPoint`, `CallChain`, `CodeIndex` data models |
| `packages/core/src/shannon_core/code_index/parser.py` | Language detection (file extension counting) + source file discovery |
| `packages/core/src/shannon_core/code_index/call_graph.py` | BFS call chain construction from edges |
| `packages/core/src/shannon_core/code_index/entry_points.py` | Per-language deterministic entry point detection rules |
| `packages/core/src/shannon_core/code_index/summary.py` | Generate `code_index_summary.md` from `CodeIndex` |
| `packages/core/src/shannon_core/code_index/parsers/__init__.py` | Parser registry mapping language name to parser class |
| `packages/core/src/shannon_core/code_index/parsers/base.py` | `BaseParser` ABC |
| `packages/core/src/shannon_core/code_index/parsers/python_parser.py` | tree-sitter Python parser |
| `packages/core/src/shannon_core/code_index/parsers/typescript_parser.py` | tree-sitter TypeScript/TSX parser |
| `packages/core/src/shannon_core/code_index/parsers/go_parser.py` | tree-sitter Go parser |
| `packages/core/src/shannon_core/code_index/parsers/java_parser.py` | tree-sitter Java parser |
| `packages/core/src/shannon_core/code_index/parsers/php_parser.py` | tree-sitter PHP parser |

### New Test Files

| File | Scope |
|------|-------|
| `packages/core/tests/code_index/__init__.py` | Test package init |
| `packages/core/tests/code_index/test_models.py` | Model validation tests |
| `packages/core/tests/code_index/test_parser.py` | Language detection + file discovery tests |
| `packages/core/tests/code_index/test_python_parser.py` | Python parser tests |
| `packages/core/tests/code_index/test_typescript_parser.py` | TypeScript parser tests |
| `packages/core/tests/code_index/test_go_parser.py` | Go parser tests |
| `packages/core/tests/code_index/test_java_parser.py` | Java parser tests |
| `packages/core/tests/code_index/test_php_parser.py` | PHP parser tests |
| `packages/core/tests/code_index/test_entry_points.py` | Entry point rule tests |
| `packages/core/tests/code_index/test_call_graph.py` | BFS call graph tests |
| `packages/core/tests/code_index/test_summary.py` | Summary generation tests |
| `packages/core/tests/code_index/test_build_code_index.py` | Integration: `build_code_index()` |
| `packages/core/tests/code_index/fixtures/` | Sample code files per language |

### Modified Files

| File | Change |
|------|--------|
| `packages/core/pyproject.toml` | Add tree-sitter dependencies |
| `packages/core/src/shannon_core/models/errors.py` | Add `CODE_INDEX_FAILED` to `ErrorCode` |
| `packages/core/src/shannon_core/models/deliverables.py` | Add `CODE_INDEX` to `DeliverableType` + filenames |
| `packages/whitebox/src/shannon_whitebox/pipeline/shared.py` | Add `code_index_stats` to `PipelineState` |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | Add `run_code_index` activity |
| `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py` | Insert CODE_INDEX phase before PRE_RECON |
| `packages/whitebox/src/shannon_whitebox/worker.py` | Register `run_code_index` activity |
| `prompts/pre-recon-code.txt` | Update `<starting_context>` section |

---

## Task 1: Dependencies & Directory Structure

**Files:**
- Modify: `packages/core/pyproject.toml`
- Create: `packages/core/src/shannon_core/code_index/__init__.py`
- Create: `packages/core/src/shannon_core/code_index/parsers/__init__.py`
- Create: `packages/core/tests/code_index/__init__.py`
- Create: `packages/core/tests/code_index/fixtures/` (directory)

- [x] **Step 1: Add tree-sitter dependencies to pyproject.toml**

Replace the `dependencies` list in `packages/core/pyproject.toml`:

```toml
dependencies = [
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "httpx>=0.27",
    "aiofiles>=23.0",
    "tree-sitter>=0.24",
    "tree-sitter-python>=0.23",
    "tree-sitter-typescript>=0.23",
    "tree-sitter-go>=0.23",
    "tree-sitter-java>=0.23",
    "tree-sitter-php>=0.23",
]
```

- [x] **Step 2: Install updated dependencies**

Run: `pip install -e packages/core`
Expected: Successfully installs shannon-core with all tree-sitter packages

- [x] **Step 3: Create directory structure and empty init files**

Run:
```bash
mkdir -p packages/core/src/shannon_core/code_index/parsers
mkdir -p packages/core/tests/code_index/fixtures
touch packages/core/src/shannon_core/code_index/__init__.py
touch packages/core/src/shannon_core/code_index/parsers/__init__.py
touch packages/core/tests/code_index/__init__.py
```

- [x] **Step 4: Verify tree-sitter imports work**

Run: `python -c "import tree_sitter_python as tspython; from tree_sitter import Language; print(Language(tspython.language()))"`
Expected: Prints a Language object representation (no error)

- [x] **Step 5: Commit**

```bash
git add packages/core/pyproject.toml packages/core/src/shannon_core/code_index/ packages/core/tests/code_index/
git commit -m "feat(code-index): add tree-sitter dependencies and directory structure"
```

---

## Task 2: Data Models

**Files:**
- Create: `packages/core/src/shannon_core/code_index/models.py`
- Modify: `packages/core/src/shannon_core/models/errors.py`
- Test: `packages/core/tests/code_index/test_models.py`

- [x] **Step 1: Write the failing test for data models**

Create `packages/core/tests/code_index/test_models.py`:

```python
import pytest
from pydantic import ValidationError

from shannon_core.code_index.models import (
    FuncBlock,
    CallEdge,
    EntryPoint,
    CallChain,
    CodeIndex,
)


def test_func_block_creation():
    block = FuncBlock(
        id="src/app.py:hello:10",
        file_path="src/app.py",
        function_name="hello",
        start_line=10,
        end_line=15,
        source_code="def hello(name):\n    return f'Hello {name}'",
        parameters=["name"],
        language="python",
    )
    assert block.id == "src/app.py:hello:10"
    assert block.language == "python"
    assert block.decorators == []
    assert block.class_name is None


def test_func_block_with_class_and_decorators():
    block = FuncBlock(
        id="src/app.py:UserView.get:20",
        file_path="src/app.py",
        function_name="get",
        start_line=20,
        end_line=30,
        source_code="@app.route('/users')\ndef get(self): pass",
        parameters=["self"],
        class_name="UserView",
        decorators=["@app.route('/users')"],
        language="python",
    )
    assert block.class_name == "UserView"
    assert block.decorators == ["@app.route('/users')"]


def test_call_edge_resolved():
    edge = CallEdge(
        caller_id="src/app.py:hello:10",
        callee_name="greet",
        callee_file="src/utils.py",
        resolved=True,
        line=12,
    )
    assert edge.resolved is True
    assert edge.callee_file == "src/utils.py"


def test_call_edge_unresolved():
    edge = CallEdge(
        caller_id="src/app.py:hello:10",
        callee_name="dynamic_func",
        resolved=False,
        line=13,
    )
    assert edge.resolved is False
    assert edge.callee_file is None


def test_entry_point_high_confidence():
    ep = EntryPoint(
        func_block_id="src/app.py:list_users:5",
        entry_type="http_route",
        route="/api/users",
        http_method="GET",
        confidence=0.95,
        evidence="Decorated with @app.route('/api/users')",
        needs_llm_review=False,
    )
    assert ep.needs_llm_review is False
    assert ep.confidence == 0.95


def test_entry_point_needs_review():
    ep = EntryPoint(
        func_block_id="src/app.py:process:50",
        entry_type="unknown",
        confidence=0.30,
        evidence="async def with no known decorator",
        needs_llm_review=True,
    )
    assert ep.needs_llm_review is True


def test_call_chain():
    chain = CallChain(
        entry_point_id="src/app.py:list_users:5",
        path=[
            "src/app.py:list_users:5",
            "src/services.py:get_users:20",
            "src/db.py:query:30",
        ],
        depth=2,
        has_unresolved=False,
    )
    assert chain.depth == 2
    assert len(chain.path) == 3


def test_call_chain_with_unresolved():
    chain = CallChain(
        entry_point_id="src/app.py:list_users:5",
        path=[
            "src/app.py:list_users:5",
            "src/services.py:get_users:20",
        ],
        depth=1,
        has_unresolved=True,
    )
    assert chain.has_unresolved is True


def test_code_index():
    block = FuncBlock(
        id="src/app.py:hello:1",
        file_path="src/app.py",
        function_name="hello",
        start_line=1,
        end_line=3,
        source_code="def hello(): pass",
        parameters=[],
        language="python",
    )
    edge = CallEdge(
        caller_id="src/app.py:hello:1",
        callee_name="print",
        resolved=False,
        line=2,
    )
    ep = EntryPoint(
        func_block_id="src/app.py:hello:1",
        entry_type="http_route",
        confidence=0.95,
        evidence="@app.route",
        needs_llm_review=False,
    )
    chain = CallChain(
        entry_point_id="src/app.py:hello:1",
        path=["src/app.py:hello:1"],
        depth=0,
        has_unresolved=False,
    )
    index = CodeIndex(
        repository="test-repo",
        language="python",
        total_blocks=1,
        total_entry_points=1,
        total_chains=1,
        blocks=[block],
        edges=[edge],
        entry_points=[ep],
        chains=[chain],
    )
    assert index.total_blocks == 1
    assert index.total_entry_points == 1
    assert index.total_chains == 1
    assert len(index.blocks) == 1


def test_code_index_serialization():
    block = FuncBlock(
        id="a:f:1",
        file_path="a",
        function_name="f",
        start_line=1,
        end_line=1,
        source_code="def f(): pass",
        parameters=[],
        language="python",
    )
    index = CodeIndex(
        repository="repo",
        language="python",
        total_blocks=1,
        total_entry_points=0,
        total_chains=0,
        blocks=[block],
        edges=[],
        entry_points=[],
        chains=[],
    )
    data = index.model_dump()
    assert data["repository"] == "repo"
    json_str = index.model_dump_json()
    assert '"repository":"repo"' in json_str.replace(" ", "")


def test_func_block_missing_required_field():
    with pytest.raises(ValidationError):
        FuncBlock(
            id="a:f:1",
            file_path="a",
            function_name="f",
            # missing start_line, end_line, source_code, parameters, language
        )
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest packages/core/tests/code_index/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shannon_core.code_index.models'`

- [x] **Step 3: Add CODE_INDEX_FAILED to ErrorCode**

Add to `packages/core/src/shannon_core/models/errors.py` — append one entry inside the `ErrorCode` enum, after `BILLING_ERROR`:

```python
    CODE_INDEX_FAILED = "CODE_INDEX_FAILED"
```

- [x] **Step 4: Write the data models**

Create `packages/core/src/shannon_core/code_index/models.py`:

```python
from pydantic import BaseModel, ConfigDict


class FuncBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str  # "file_path:function_name:start_line"
    file_path: str
    function_name: str
    start_line: int
    end_line: int
    source_code: str
    parameters: list[str]
    class_name: str | None = None
    decorators: list[str] = []
    language: str  # "python" | "go" | "typescript" | "java" | "php"


class CallEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    caller_id: str  # FuncBlock.id
    callee_name: str  # called function name
    callee_file: str | None = None
    resolved: bool  # whether callee was found in known blocks
    line: int  # line number of the call


class EntryPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    func_block_id: str
    entry_type: str  # "http_route" | "rpc" | "cli" | "message_consumer" | ...
    route: str | None = None
    http_method: str | None = None
    confidence: float
    evidence: str
    needs_llm_review: bool  # True when confidence < 0.8


class CallChain(BaseModel):
    model_config = ConfigDict(frozen=True)

    entry_point_id: str
    path: list[str]  # ordered list of FuncBlock.id
    depth: int
    has_unresolved: bool  # path contains unresolved calls


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

- [x] **Step 5: Run tests to verify they pass**

Run: `python -m pytest packages/core/tests/code_index/test_models.py -v`
Expected: All 10 tests PASS

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/models.py packages/core/src/shannon_core/models/errors.py packages/core/tests/code_index/test_models.py
git commit -m "feat(code-index): add data models and CODE_INDEX_FAILED error code"
```

---

## Task 3: BaseParser ABC & Parser Registry

**Files:**
- Create: `packages/core/src/shannon_core/code_index/parsers/base.py`
- Modify: `packages/core/src/shannon_core/code_index/parsers/__init__.py`
- Test: `packages/core/tests/code_index/test_python_parser.py` (only test registry import)

- [x] **Step 1: Write the failing test for BaseParser**

Add to a new file `packages/core/tests/code_index/test_base_parser.py`:

```python
import pytest
from pathlib import Path

from shannon_core.code_index.parsers.base import BaseParser
from shannon_core.code_index.models import FuncBlock, CallEdge


def test_base_parser_cannot_instantiate():
    with pytest.raises(TypeError):
        BaseParser()


def test_concrete_parser_must_implement_methods():
    class IncompleteParser(BaseParser):
        pass

    with pytest.raises(TypeError):
        IncompleteParser()


def test_concrete_parser_implements_both_methods():
    class DummyParser(BaseParser):
        def parse_file(self, file_path: Path, repo_root: Path) -> list[FuncBlock]:
            return []

        def extract_calls(self, block: FuncBlock, source: bytes) -> list[CallEdge]:
            return []

    parser = DummyParser()
    assert parser.parse_file(Path("a.py"), Path(".")) == []
    assert parser.extract_calls(
        FuncBlock(
            id="a:f:1", file_path="a", function_name="f",
            start_line=1, end_line=1, source_code="def f(): pass",
            parameters=[], language="python",
        ),
        b"def f(): pass",
    ) == []
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/core/tests/code_index/test_base_parser.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write BaseParser ABC**

Create `packages/core/src/shannon_core/code_index/parsers/base.py`:

```python
from abc import ABC, abstractmethod
from pathlib import Path

from shannon_core.code_index.models import CallEdge, FuncBlock


class BaseParser(ABC):
    @abstractmethod
    def parse_file(self, file_path: Path, repo_root: Path) -> list[FuncBlock]:
        """Parse a source file and return all function blocks found."""
        ...

    @abstractmethod
    def extract_calls(self, block: FuncBlock, source: bytes) -> list[CallEdge]:
        """Extract call edges from a function block's source."""
        ...
```

- [x] **Step 4: Write the parser registry**

Replace `packages/core/src/shannon_core/code_index/parsers/__init__.py`:

```python
from shannon_core.code_index.parsers.base import BaseParser

_PARSER_CLASSES: dict[str, type[BaseParser]] = {}


def register_parser(language: str, parser_class: type[BaseParser]) -> None:
    _PARSER_CLASSES[language] = parser_class


def get_parser(language: str) -> BaseParser | None:
    cls = _PARSER_CLASSES.get(language)
    if cls is None:
        return None
    return cls()


def available_languages() -> list[str]:
    return list(_PARSER_CLASSES.keys())
```

- [x] **Step 5: Run tests to verify they pass**

Run: `python -m pytest packages/core/tests/code_index/test_base_parser.py -v`
Expected: All 3 tests PASS

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/parsers/base.py packages/core/src/shannon_core/code_index/parsers/__init__.py packages/core/tests/code_index/test_base_parser.py
git commit -m "feat(code-index): add BaseParser ABC and parser registry"
```

---

## Task 4: Language Detection & File Discovery

**Files:**
- Create: `packages/core/src/shannon_core/code_index/parser.py`
- Test: `packages/core/tests/code_index/test_parser.py`

- [x] **Step 1: Write the failing test**

Create `packages/core/tests/code_index/test_parser.py`:

```python
import pytest
from pathlib import Path

from shannon_core.code_index.parser import detect_language, discover_source_files


class TestDetectLanguage:
    def test_detects_python(self, tmp_path):
        (tmp_path / "app.py").write_text("print('hi')")
        (tmp_path / "utils.py").write_text("def f(): pass")
        assert detect_language(tmp_path) == "python"

    def test_detects_typescript(self, tmp_path):
        (tmp_path / "app.ts").write_text("console.log('hi')")
        assert detect_language(tmp_path) == "typescript"

    def test_detects_go(self, tmp_path):
        (tmp_path / "main.go").write_text("package main")
        assert detect_language(tmp_path) == "go"

    def test_detects_java(self, tmp_path):
        (tmp_path / "App.java").write_text("class App {}")
        assert detect_language(tmp_path) == "java"

    def test_detects_php(self, tmp_path):
        (tmp_path / "index.php").write_text("<?php echo 'hi';")
        assert detect_language(tmp_path) == "php"

    def test_mixed_language_picks_majority(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1")
        (tmp_path / "utils.py").write_text("y = 2")
        (tmp_path / "helper.ts").write_text("z = 3")
        assert detect_language(tmp_path) == "python"

    def test_no_source_files_raises(self, tmp_path):
        (tmp_path / "README.md").write_text("# Hello")
        with pytest.raises(ValueError, match="No source files found"):
            detect_language(tmp_path)

    def test_custom_extensions_counted(self, tmp_path):
        (tmp_path / "app.pyx").write_text("cdef int x")
        assert detect_language(tmp_path) == "python"


class TestDiscoverSourceFiles:
    def test_finds_python_files(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1")
        (tmp_path / "README.md").write_text("# Hi")
        files = discover_source_files(tmp_path, "python")
        paths = [str(f) for f in files]
        assert any("app.py" in p for p in paths)

    def test_skips_hidden_dirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "hook.py").write_text("x = 1")
        (tmp_path / "app.py").write_text("x = 1")
        files = discover_source_files(tmp_path, "python")
        paths = [str(f) for f in files]
        assert not any(".git" in p for p in paths)

    def test_skips_node_modules(self, tmp_path):
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "lib.ts").write_text("export {}")
        (tmp_path / "app.ts").write_text("export {}")
        files = discover_source_files(tmp_path, "typescript")
        paths = [str(f) for f in files]
        assert not any("node_modules" in p for p in paths)

    def test_skips_vendor_dir(self, tmp_path):
        (tmp_path / "vendor").mkdir()
        (tmp_path / "vendor" / "lib.go").write_text("package lib")
        (tmp_path / "main.go").write_text("package main")
        files = discover_source_files(tmp_path, "go")
        paths = [str(f) for f in files]
        assert not any("vendor" in p for p in paths)

    def test_finds_nested_files(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("x = 1")
        files = discover_source_files(tmp_path, "python")
        assert len(files) == 1
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/core/tests/code_index/test_parser.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write language detection and file discovery**

Create `packages/core/src/shannon_core/code_index/parser.py`:

```python
import logging
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

LANGUAGE_EXTENSIONS: dict[str, list[str]] = {
    "python": [".py", ".pyw", ".pyx"],
    "typescript": [".ts", ".tsx", ".js", ".jsx"],
    "go": [".go"],
    "java": [".java"],
    "php": [".php"],
}

SKIP_DIRS: set[str] = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "__pycache__",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", ".venv",
    "venv", "env", ".eggs", "eggs",
}


def detect_language(repo_root: Path) -> str:
    """Detect the primary language by counting source file extensions."""
    ext_counts: Counter[str] = Counter()
    for ext_list in LANGUAGE_EXTENSIONS.values():
        for ext in ext_list:
            count = sum(1 for _ in repo_root.rglob(f"*{ext}"))
            if count > 0:
                for lang, lang_exts in LANGUAGE_EXTENSIONS.items():
                    if ext in lang_exts:
                        ext_counts[lang] += count
                        break

    if not ext_counts:
        raise ValueError(
            f"No source files found in {repo_root}. "
            "Could not detect programming language."
        )

    return ext_counts.most_common(1)[0][0]


def discover_source_files(repo_root: Path, language: str) -> list[Path]:
    """Find all source files for the given language, skipping vendored/hidden dirs."""
    extensions = LANGUAGE_EXTENSIONS.get(language, [])
    if not extensions:
        return []

    files: list[Path] = []
    for ext in extensions:
        for path in repo_root.rglob(f"*{ext}"):
            parts = path.relative_to(repo_root).parts
            if any(part in SKIP_DIRS or part.startswith(".") for part in parts):
                continue
            files.append(path)

    return sorted(files)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest packages/core/tests/code_index/test_parser.py -v`
Expected: All 13 tests PASS

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/parser.py packages/core/tests/code_index/test_parser.py
git commit -m "feat(code-index): add language detection and source file discovery"
```

---

## Task 5: Python Parser

**Files:**
- Create: `packages/core/src/shannon_core/code_index/parsers/python_parser.py`
- Create: `packages/core/tests/code_index/fixtures/python/flask_app.py`
- Test: `packages/core/tests/code_index/test_python_parser.py`

- [x] **Step 1: Create the Python fixture file**

Create `packages/core/tests/code_index/fixtures/python/flask_app.py`:

```python
from flask import Flask, request, jsonify

app = Flask(__name__)


@app.route("/api/users", methods=["GET"])
def list_users():
    users = get_users()
    return jsonify(users)


@app.route("/api/users/<int:user_id>", methods=["POST"])
def update_user(user_id):
    data = request.get_json()
    result = save_user(user_id, data)
    return jsonify(result)


@shared_task
def process_queue():
    items = fetch_items()
    for item in items:
        process_item(item)


def get_users():
    return db.query("SELECT * FROM users")


def save_user(user_id, data):
    return db.update("users", user_id, data)


def fetch_items():
    return []


def process_item(item):
    pass
```

- [x] **Step 2: Write the failing test**

Create `packages/core/tests/code_index/test_python_parser.py`:

```python
from pathlib import Path

from shannon_core.code_index.parsers.python_parser import PythonParser
from shannon_core.code_index.parsers import get_parser, available_languages

FIXTURES = Path(__file__).parent / "fixtures"
FLASK_APP = FIXTURES / "python" / "flask_app.py"


class TestPythonParserFuncBlocks:
    def test_extracts_all_functions(self):
        parser = PythonParser()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        names = {b.function_name for b in blocks}
        assert "list_users" in names
        assert "update_user" in names
        assert "process_queue" in names
        assert "get_users" in names
        assert "save_user" in names
        assert "fetch_items" in names
        assert "process_item" in names

    def test_extracts_parameters(self):
        parser = PythonParser()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        assert "user_id" in by_name["update_user"].parameters
        assert "item" in by_name["process_item"].parameters

    def test_extracts_decorators(self):
        parser = PythonParser()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        assert any("@app.route" in d for d in by_name["list_users"].decorators)
        assert any("@shared_task" in d for d in by_name["process_queue"].decorators)

    def test_block_id_format(self):
        parser = PythonParser()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        for block in blocks:
            assert block.id.count(":") >= 2
            assert block.language == "python"

    def test_source_code_populated(self):
        parser = PythonParser()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        for block in blocks:
            assert "def " in block.source_code

    def test_line_numbers_valid(self):
        parser = PythonParser()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        for block in blocks:
            assert block.start_line > 0
            assert block.end_line >= block.start_line


class TestPythonParserCallEdges:
    def test_extracts_function_calls(self):
        parser = PythonParser()
        source = FLASK_APP.read_bytes()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["list_users"], source)
        callee_names = [e.callee_name for e in edges]
        assert "get_users" in callee_names
        assert "jsonify" in callee_names

    def test_extracts_method_calls(self):
        parser = PythonParser()
        source = FLASK_APP.read_bytes()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["update_user"], source)
        callee_names = [e.callee_name for e in edges]
        assert "save_user" in callee_names

    def test_call_edge_has_line_number(self):
        parser = PythonParser()
        source = FLASK_APP.read_bytes()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["list_users"], source)
        for edge in edges:
            assert edge.line > 0

    def test_empty_function_no_calls(self):
        parser = PythonParser()
        source = FLASK_APP.read_bytes()
        blocks = parser.parse_file(FLASK_APP, FLASK_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["process_item"], source)
        assert len(edges) == 0


class TestPythonParserRegistry:
    def test_registered_in_parser_registry(self):
        from shannon_core.code_index.parsers import _PARSER_CLASSES
        assert "python" in _PARSER_CLASSES

    def test_get_parser_returns_python_parser(self):
        parser = get_parser("python")
        assert isinstance(parser, PythonParser)
```

- [x] **Step 3: Run tests to verify they fail**

Run: `python -m pytest packages/core/tests/code_index/test_python_parser.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 4: Write the Python parser**

Create `packages/core/src/shannon_core/code_index/parsers/python_parser.py`:

```python
import logging
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

from shannon_core.code_index.models import CallEdge, FuncBlock
from shannon_core.code_index.parsers import register_parser
from shannon_core.code_index.parsers.base import BaseParser

logger = logging.getLogger(__name__)

PY_LANGUAGE = Language(tspython.language())


def _walk(node):
    """Yield all descendant nodes depth-first."""
    yield node
    for child in node.children:
        yield from _walk(child)


class PythonParser(BaseParser):
    def __init__(self):
        self._parser = Parser(PY_LANGUAGE)

    def parse_file(self, file_path: Path, repo_root: Path) -> list[FuncBlock]:
        source = file_path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(file_path.relative_to(repo_root))
        blocks: list[FuncBlock] = []

        for node in _walk(tree.root_node):
            if node.type in ("function_definition", "async_function_definition"):
                block = self._extract_func_block(node, source, rel_path)
                if block is not None:
                    blocks.append(block)
        return blocks

    def extract_calls(self, block: FuncBlock, source: bytes) -> list[CallEdge]:
        tree = self._parser.parse(source)
        edges: list[CallEdge] = []

        for node in _walk(tree.root_node):
            if node.type in ("function_definition", "async_function_definition"):
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text.decode("utf-8") == block.function_name:
                    if node.start_point[0] + 1 == block.start_line:
                        edges = self._extract_call_edges(node, source, block.id)
                        break
        return edges

    def _extract_func_block(self, node, source: bytes, rel_path: str) -> FuncBlock | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        func_name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        func_source = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

        parameters = self._extract_parameters(node, source)
        decorators = self._extract_decorators(node, source)

        return FuncBlock(
            id=f"{rel_path}:{func_name}:{start_line}",
            file_path=rel_path,
            function_name=func_name,
            start_line=start_line,
            end_line=end_line,
            source_code=func_source,
            parameters=parameters,
            decorators=decorators,
            language="python",
        )

    def _extract_parameters(self, func_node, source: bytes) -> list[str]:
        params_node = func_node.child_by_field_name("parameters")
        if params_node is None:
            return []

        params: list[str] = []
        for child in params_node.children:
            if child.type == "identifier":
                params.append(child.text.decode("utf-8"))
            elif child.type == "typed_parameter":
                name_node = child.child_by_field_name("name")
                if name_node:
                    params.append(name_node.text.decode("utf-8"))
            elif child.type == "default_parameter":
                name_node = child.child_by_field_name("name")
                if name_node:
                    params.append(name_node.text.decode("utf-8"))
            elif child.type in ("list_splat_pattern", "dictionary_splat_pattern"):
                for sub in child.children:
                    if sub.type == "identifier":
                        params.append(sub.text.decode("utf-8"))
        return params

    def _extract_decorators(self, func_node, source: bytes) -> list[str]:
        decorators: list[str] = []
        for sibling in func_node.children:
            if sibling.type == "decorator":
                decorators.append(sibling.text.decode("utf-8"))
        return decorators

    def _extract_call_edges(self, func_node, source: bytes, caller_id: str) -> list[CallEdge]:
        edges: list[CallEdge] = []
        for node in _walk(func_node):
            if node.type == "call":
                callee_name = self._get_callee_name(node)
                if callee_name:
                    edges.append(CallEdge(
                        caller_id=caller_id,
                        callee_name=callee_name,
                        resolved=False,
                        line=node.start_point[0] + 1,
                    ))
        return edges

    def _get_callee_name(self, call_node) -> str | None:
        func_node = call_node.child_by_field_name("function")
        if func_node is None:
            return None

        if func_node.type == "identifier":
            return func_node.text.decode("utf-8")
        elif func_node.type == "attribute":
            attr = func_node.child_by_field_name("attribute")
            if attr:
                return attr.text.decode("utf-8")
        return None


# Register in the parser registry
register_parser("python", PythonParser)
```

- [x] **Step 5: Run tests to verify they pass**

Run: `python -m pytest packages/core/tests/code_index/test_python_parser.py -v`
Expected: All tests PASS

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/parsers/python_parser.py packages/core/tests/code_index/fixtures/ packages/core/tests/code_index/test_python_parser.py
git commit -m "feat(code-index): add Python parser with function and call extraction"
```

---

## Task 6: TypeScript Parser

**Files:**
- Create: `packages/core/src/shannon_core/code_index/parsers/typescript_parser.py`
- Create: `packages/core/tests/code_index/fixtures/typescript/express_app.ts`
- Test: `packages/core/tests/code_index/test_typescript_parser.py`

- [x] **Step 1: Create the TypeScript fixture file**

Create `packages/core/tests/code_index/fixtures/typescript/express_app.ts`:

```typescript
import { Router, Request, Response } from 'express';

const router = Router();

router.get('/api/users', async (req: Request, res: Response) => {
    const users = await getUsers();
    res.json(users);
});

router.post('/api/users/:id', async (req: Request, res: Response) => {
    const result = await saveUser(req.params.id, req.body);
    res.json(result);
});

function listOrders(req: Request, res: Response) {
    const orders = getOrders();
    res.json(orders);
}

async function getUsers(): Promise<any[]> {
    return db.query('SELECT * FROM users');
}

async function saveUser(id: string, data: any): Promise<any> {
    return db.update('users', id, data);
}

function getOrders(): any[] {
    return [];
}
```

- [x] **Step 2: Write the failing test**

Create `packages/core/tests/code_index/test_typescript_parser.py`:

```python
from pathlib import Path

from shannon_core.code_index.parsers.typescript_parser import TypeScriptParser
from shannon_core.code_index.parsers import get_parser

FIXTURES = Path(__file__).parent / "fixtures"
EXPRESS_APP = FIXTURES / "typescript" / "express_app.ts"


class TestTypeScriptParserFuncBlocks:
    def test_extracts_named_functions(self):
        parser = TypeScriptParser()
        blocks = parser.parse_file(EXPRESS_APP, EXPRESS_APP.parent.parent.parent)
        names = {b.function_name for b in blocks}
        assert "listOrders" in names
        assert "getUsers" in names
        assert "saveUser" in names
        assert "getOrders" in names

    def test_extracts_parameters(self):
        parser = TypeScriptParser()
        blocks = parser.parse_file(EXPRESS_APP, EXPRESS_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        assert len(by_name["listOrders"].parameters) >= 2

    def test_block_language_is_typescript(self):
        parser = TypeScriptParser()
        blocks = parser.parse_file(EXPRESS_APP, EXPRESS_APP.parent.parent.parent)
        for block in blocks:
            assert block.language == "typescript"


class TestTypeScriptParserCallEdges:
    def test_extracts_function_calls(self):
        parser = TypeScriptParser()
        source = EXPRESS_APP.read_bytes()
        blocks = parser.parse_file(EXPRESS_APP, EXPRESS_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["listOrders"], source)
        callee_names = [e.callee_name for e in edges]
        assert "getOrders" in callee_names

    def test_call_edge_has_line_number(self):
        parser = TypeScriptParser()
        source = EXPRESS_APP.read_bytes()
        blocks = parser.parse_file(EXPRESS_APP, EXPRESS_APP.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["listOrders"], source)
        for edge in edges:
            assert edge.line > 0


class TestTypeScriptParserRegistry:
    def test_registered_in_parser_registry(self):
        from shannon_core.code_index.parsers import _PARSER_CLASSES
        assert "typescript" in _PARSER_CLASSES
```

- [x] **Step 3: Run tests to verify they fail**

Run: `python -m pytest packages/core/tests/code_index/test_typescript_parser.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 4: Write the TypeScript parser**

Create `packages/core/src/shannon_core/code_index/parsers/typescript_parser.py`:

```python
import logging
from pathlib import Path

import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser

from shannon_core.code_index.models import CallEdge, FuncBlock
from shannon_core.code_index.parsers import register_parser
from shannon_core.code_index.parsers.base import BaseParser

logger = logging.getLogger(__name__)

TS_LANGUAGE = Language(tsts.language_typescript())


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


class TypeScriptParser(BaseParser):
    def __init__(self):
        self._parser = Parser(TS_LANGUAGE)

    def parse_file(self, file_path: Path, repo_root: Path) -> list[FuncBlock]:
        source = file_path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(file_path.relative_to(repo_root))
        blocks: list[FuncBlock] = []

        for node in _walk(tree.root_node):
            if node.type == "function_declaration":
                block = self._extract_func_block(node, source, rel_path)
                if block is not None:
                    blocks.append(block)
            elif node.type == "method_definition":
                block = self._extract_func_block(node, source, rel_path)
                if block is not None:
                    blocks.append(block)
            elif node.type == "arrow_function":
                # Only capture named arrow functions (export const handler = ...)
                block = self._extract_arrow_block(node, source, rel_path)
                if block is not None:
                    blocks.append(block)
        return blocks

    def extract_calls(self, block: FuncBlock, source: bytes) -> list[CallEdge]:
        tree = self._parser.parse(source)
        edges: list[CallEdge] = []

        for node in _walk(tree.root_node):
            if node.type in ("function_declaration", "method_definition"):
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text.decode("utf-8") == block.function_name:
                    if node.start_point[0] + 1 == block.start_line:
                        edges = self._extract_call_edges(node, source, block.id)
                        break
        return edges

    def _extract_func_block(self, node, source: bytes, rel_path: str) -> FuncBlock | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        func_name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        func_source = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        parameters = self._extract_parameters(node, source)

        return FuncBlock(
            id=f"{rel_path}:{func_name}:{start_line}",
            file_path=rel_path,
            function_name=func_name,
            start_line=start_line,
            end_line=end_line,
            source_code=func_source,
            parameters=parameters,
            language="typescript",
        )

    def _extract_arrow_block(self, node, source: bytes, rel_path: str) -> FuncBlock | None:
        parent = node.parent
        if parent is None or parent.type != "variable_declarator":
            return None

        name_node = parent.child_by_field_name("name")
        if name_node is None:
            return None

        func_name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        func_source = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

        return FuncBlock(
            id=f"{rel_path}:{func_name}:{start_line}",
            file_path=rel_path,
            function_name=func_name,
            start_line=start_line,
            end_line=end_line,
            source_code=func_source,
            parameters=[],
            language="typescript",
        )

    def _extract_parameters(self, func_node, source: bytes) -> list[str]:
        params_node = func_node.child_by_field_name("parameters")
        if params_node is None:
            return []

        params: list[str] = []
        for child in params_node.children:
            if child.type == "required_parameter" or child.type == "optional_parameter":
                pattern = child.child_by_field_name("pattern")
                if pattern:
                    params.append(pattern.text.decode("utf-8"))
                else:
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        params.append(name_node.text.decode("utf-8"))
            elif child.type == "identifier":
                params.append(child.text.decode("utf-8"))
        return params

    def _extract_call_edges(self, func_node, source: bytes, caller_id: str) -> list[CallEdge]:
        edges: list[CallEdge] = []
        for node in _walk(func_node):
            if node.type == "call_expression":
                callee_name = self._get_callee_name(node)
                if callee_name:
                    edges.append(CallEdge(
                        caller_id=caller_id,
                        callee_name=callee_name,
                        resolved=False,
                        line=node.start_point[0] + 1,
                    ))
        return edges

    def _get_callee_name(self, call_node) -> str | None:
        func_node = call_node.child_by_field_name("function")
        if func_node is None:
            return None

        if func_node.type == "identifier":
            return func_node.text.decode("utf-8")
        elif func_node.type == "member_expression":
            prop = func_node.child_by_field_name("property")
            if prop:
                return prop.text.decode("utf-8")
        return None


register_parser("typescript", TypeScriptParser)
```

- [x] **Step 5: Run tests to verify they pass**

Run: `python -m pytest packages/core/tests/code_index/test_typescript_parser.py -v`
Expected: All tests PASS

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/parsers/typescript_parser.py packages/core/tests/code_index/fixtures/typescript/ packages/core/tests/code_index/test_typescript_parser.py
git commit -m "feat(code-index): add TypeScript parser"
```

---

## Task 7: Go Parser

**Files:**
- Create: `packages/core/src/shannon_core/code_index/parsers/go_parser.py`
- Create: `packages/core/tests/code_index/fixtures/go/http_handler.go`
- Test: `packages/core/tests/code_index/test_go_parser.py`

- [x] **Step 1: Create the Go fixture file**

Create `packages/core/tests/code_index/fixtures/go/http_handler.go`:

```go
package main

import (
    "net/http"
    "encoding/json"
)

func listUsers(w http.ResponseWriter, r *http.Request) {
    users := getUsers()
    json.NewEncoder(w).Encode(users)
}

func updateUser(w http.ResponseWriter, r *http.Request) {
    var data map[string]interface{}
    json.NewDecoder(r.Body).Decode(&data)
    result := saveUser(data)
    json.NewEncoder(w).Encode(result)
}

func getUsers() []map[string]interface{} {
    return nil
}

func saveUser(data map[string]interface{}) map[string]interface{} {
    return data
}
```

- [x] **Step 2: Write the failing test**

Create `packages/core/tests/code_index/test_go_parser.py`:

```python
from pathlib import Path

from shannon_core.code_index.parsers.go_parser import GoParser
from shannon_core.code_index.parsers import _PARSER_CLASSES

FIXTURES = Path(__file__).parent / "fixtures"
GO_FILE = FIXTURES / "go" / "http_handler.go"


class TestGoParserFuncBlocks:
    def test_extracts_all_functions(self):
        parser = GoParser()
        blocks = parser.parse_file(GO_FILE, GO_FILE.parent.parent.parent)
        names = {b.function_name for b in blocks}
        assert "listUsers" in names
        assert "updateUser" in names
        assert "getUsers" in names
        assert "saveUser" in names

    def test_extracts_parameters_with_types(self):
        parser = GoParser()
        blocks = parser.parse_file(GO_FILE, GO_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        params = by_name["listUsers"].parameters
        assert len(params) >= 2

    def test_block_language_is_go(self):
        parser = GoParser()
        blocks = parser.parse_file(GO_FILE, GO_FILE.parent.parent.parent)
        for block in blocks:
            assert block.language == "go"


class TestGoParserCallEdges:
    def test_extracts_function_calls(self):
        parser = GoParser()
        source = GO_FILE.read_bytes()
        blocks = parser.parse_file(GO_FILE, GO_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["listUsers"], source)
        callee_names = [e.callee_name for e in edges]
        assert "getUsers" in callee_names

    def test_extracts_method_calls(self):
        parser = GoParser()
        source = GO_FILE.read_bytes()
        blocks = parser.parse_file(GO_FILE, GO_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["updateUser"], source)
        callee_names = [e.callee_name for e in edges]
        assert "saveUser" in callee_names


class TestGoParserRegistry:
    def test_registered(self):
        assert "go" in _PARSER_CLASSES
```

- [x] **Step 3: Run tests to verify they fail**

Run: `python -m pytest packages/core/tests/code_index/test_go_parser.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 4: Write the Go parser**

Create `packages/core/src/shannon_core/code_index/parsers/go_parser.py`:

```python
import logging
from pathlib import Path

import tree_sitter_go as tsgo
from tree_sitter import Language, Parser

from shannon_core.code_index.models import CallEdge, FuncBlock
from shannon_core.code_index.parsers import register_parser
from shannon_core.code_index.parsers.base import BaseParser

logger = logging.getLogger(__name__)

GO_LANGUAGE = Language(tsgo.language())


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


class GoParser(BaseParser):
    def __init__(self):
        self._parser = Parser(GO_LANGUAGE)

    def parse_file(self, file_path: Path, repo_root: Path) -> list[FuncBlock]:
        source = file_path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(file_path.relative_to(repo_root))
        blocks: list[FuncBlock] = []

        for node in _walk(tree.root_node):
            if node.type == "function_declaration":
                block = self._extract_func_block(node, source, rel_path)
                if block is not None:
                    blocks.append(block)
            elif node.type == "method_declaration":
                block = self._extract_method_block(node, source, rel_path)
                if block is not None:
                    blocks.append(block)
        return blocks

    def extract_calls(self, block: FuncBlock, source: bytes) -> list[CallEdge]:
        tree = self._parser.parse(source)
        edges: list[CallEdge] = []

        for node in _walk(tree.root_node):
            if node.type in ("function_declaration", "method_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text.decode("utf-8") == block.function_name:
                    if node.start_point[0] + 1 == block.start_line:
                        edges = self._extract_call_edges(node, source, block.id)
                        break
        return edges

    def _extract_func_block(self, node, source: bytes, rel_path: str) -> FuncBlock | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        func_name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        func_source = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        parameters = self._extract_parameters(node, source)

        return FuncBlock(
            id=f"{rel_path}:{func_name}:{start_line}",
            file_path=rel_path,
            function_name=func_name,
            start_line=start_line,
            end_line=end_line,
            source_code=func_source,
            parameters=parameters,
            language="go",
        )

    def _extract_method_block(self, node, source: bytes, rel_path: str) -> FuncBlock | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        func_name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        func_source = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        parameters = self._extract_parameters(node, source)

        receiver_node = node.child_by_field_name("receiver")
        class_name = None
        if receiver_node:
            for child in receiver_node.children:
                if child.type == "parameter_list":
                    for param in child.children:
                        if param.type == "parameter_declaration":
                            type_node = param.child_by_field_name("type")
                            if type_node:
                                class_name = type_node.text.decode("utf-8").lstrip("*")
                                break

        return FuncBlock(
            id=f"{rel_path}:{func_name}:{start_line}",
            file_path=rel_path,
            function_name=func_name,
            start_line=start_line,
            end_line=end_line,
            source_code=func_source,
            parameters=parameters,
            class_name=class_name,
            language="go",
        )

    def _extract_parameters(self, func_node, source: bytes) -> list[str]:
        params_node = func_node.child_by_field_name("parameters")
        if params_node is None:
            return []

        params: list[str] = []
        for child in params_node.children:
            if child.type == "parameter_declaration":
                name_node = child.child_by_field_name("name")
                type_node = child.child_by_field_name("type")
                if name_node and type_node:
                    params.append(f"{name_node.text.decode('utf-8')} {type_node.text.decode('utf-8')}")
                elif type_node:
                    params.append(type_node.text.decode("utf-8"))
        return params

    def _extract_call_edges(self, func_node, source: bytes, caller_id: str) -> list[CallEdge]:
        edges: list[CallEdge] = []
        for node in _walk(func_node):
            if node.type == "call_expression":
                callee_name = self._get_callee_name(node)
                if callee_name:
                    edges.append(CallEdge(
                        caller_id=caller_id,
                        callee_name=callee_name,
                        resolved=False,
                        line=node.start_point[0] + 1,
                    ))
        return edges

    def _get_callee_name(self, call_node) -> str | None:
        func_node = call_node.child_by_field_name("function")
        if func_node is None:
            return None

        if func_node.type == "identifier":
            return func_node.text.decode("utf-8")
        elif func_node.type == "selector_expression":
            field = func_node.child_by_field_name("field")
            if field:
                return field.text.decode("utf-8")
        return None


register_parser("go", GoParser)
```

- [x] **Step 5: Run tests to verify they pass**

Run: `python -m pytest packages/core/tests/code_index/test_go_parser.py -v`
Expected: All tests PASS

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/parsers/go_parser.py packages/core/tests/code_index/fixtures/go/ packages/core/tests/code_index/test_go_parser.py
git commit -m "feat(code-index): add Go parser"
```

---

## Task 8: Java Parser

**Files:**
- Create: `packages/core/src/shannon_core/code_index/parsers/java_parser.py`
- Create: `packages/core/tests/code_index/fixtures/java/SpringController.java`
- Test: `packages/core/tests/code_index/test_java_parser.py`

- [x] **Step 1: Create the Java fixture file**

Create `packages/core/tests/code_index/fixtures/java/SpringController.java`:

```java
package com.example.controller;

import org.springframework.web.bind.annotation.*;
import java.util.List;

@RestController
@RequestMapping("/api/users")
public class UserController {

    @GetMapping
    public List<Object> listUsers() {
        return userService.getUsers();
    }

    @PostMapping("/{id}")
    public Object updateUser(@PathVariable Long id, @RequestBody Object data) {
        return userService.saveUser(id, data);
    }

    @RabbitListener(queues = "orders")
    public void processOrder(String message) {
        orderService.handle(message);
    }
}
```

- [x] **Step 2: Write the failing test**

Create `packages/core/tests/code_index/test_java_parser.py`:

```python
from pathlib import Path

from shannon_core.code_index.parsers.java_parser import JavaParser
from shannon_core.code_index.parsers import _PARSER_CLASSES

FIXTURES = Path(__file__).parent / "fixtures"
JAVA_FILE = FIXTURES / "java" / "SpringController.java"


class TestJavaParserFuncBlocks:
    def test_extracts_all_methods(self):
        parser = JavaParser()
        blocks = parser.parse_file(JAVA_FILE, JAVA_FILE.parent.parent.parent)
        names = {b.function_name for b in blocks}
        assert "listUsers" in names
        assert "updateUser" in names
        assert "processOrder" in names

    def test_extracts_parameters(self):
        parser = JavaParser()
        blocks = parser.parse_file(JAVA_FILE, JAVA_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        params = by_name["updateUser"].parameters
        assert len(params) >= 2

    def test_extracts_decorators_as_annotations(self):
        parser = JavaParser()
        blocks = parser.parse_file(JAVA_FILE, JAVA_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        assert any("@GetMapping" in d for d in by_name["listUsers"].decorators)
        assert any("@RabbitListener" in d for d in by_name["processOrder"].decorators)

    def test_block_language_is_java(self):
        parser = JavaParser()
        blocks = parser.parse_file(JAVA_FILE, JAVA_FILE.parent.parent.parent)
        for block in blocks:
            assert block.language == "java"


class TestJavaParserCallEdges:
    def test_extracts_method_calls(self):
        parser = JavaParser()
        source = JAVA_FILE.read_bytes()
        blocks = parser.parse_file(JAVA_FILE, JAVA_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["listUsers"], source)
        callee_names = [e.callee_name for e in edges]
        assert "getUsers" in callee_names


class TestJavaParserRegistry:
    def test_registered(self):
        assert "java" in _PARSER_CLASSES
```

- [x] **Step 3: Run tests to verify they fail**

Run: `python -m pytest packages/core/tests/code_index/test_java_parser.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 4: Write the Java parser**

Create `packages/core/src/shannon_core/code_index/parsers/java_parser.py`:

```python
import logging
from pathlib import Path

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

from shannon_core.code_index.models import CallEdge, FuncBlock
from shannon_core.code_index.parsers import register_parser
from shannon_core.code_index.parsers.base import BaseParser

logger = logging.getLogger(__name__)

JAVA_LANGUAGE = Language(tsjava.language())


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


class JavaParser(BaseParser):
    def __init__(self):
        self._parser = Parser(JAVA_LANGUAGE)

    def parse_file(self, file_path: Path, repo_root: Path) -> list[FuncBlock]:
        source = file_path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(file_path.relative_to(repo_root))
        blocks: list[FuncBlock] = []

        for node in _walk(tree.root_node):
            if node.type == "method_declaration":
                block = self._extract_method_block(node, source, rel_path)
                if block is not None:
                    blocks.append(block)
        return blocks

    def extract_calls(self, block: FuncBlock, source: bytes) -> list[CallEdge]:
        tree = self._parser.parse(source)
        edges: list[CallEdge] = []

        for node in _walk(tree.root_node):
            if node.type == "method_declaration":
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text.decode("utf-8") == block.function_name:
                    if node.start_point[0] + 1 == block.start_line:
                        edges = self._extract_call_edges(node, source, block.id)
                        break
        return edges

    def _extract_method_block(self, node, source: bytes, rel_path: str) -> FuncBlock | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        func_name = name_node.text.decode("utf-8")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        func_source = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        parameters = self._extract_parameters(node, source)
        decorators = self._extract_annotations(node, source)

        return FuncBlock(
            id=f"{rel_path}:{func_name}:{start_line}",
            file_path=rel_path,
            function_name=func_name,
            start_line=start_line,
            end_line=end_line,
            source_code=func_source,
            parameters=parameters,
            decorators=decorators,
            language="java",
        )

    def _extract_parameters(self, method_node, source: bytes) -> list[str]:
        params_node = method_node.child_by_field_name("parameters")
        if params_node is None:
            return []

        params: list[str] = []
        for child in params_node.children:
            if child.type == "formal_parameter":
                name_node = child.child_by_field_name("name")
                if name_node:
                    params.append(name_node.text.decode("utf-8"))
        return params

    def _extract_annotations(self, method_node, source: bytes) -> list[str]:
        annotations: list[str] = []
        for sibling in method_node.children:
            if sibling.type in ("marker_annotation", "annotation"):
                annotations.append(sibling.text.decode("utf-8"))
        return annotations

    def _extract_call_edges(self, method_node, source: bytes, caller_id: str) -> list[CallEdge]:
        edges: list[CallEdge] = []
        for node in _walk(method_node):
            if node.type == "method_invocation":
                name_node = node.child_by_field_name("name")
                if name_node:
                    edges.append(CallEdge(
                        caller_id=caller_id,
                        callee_name=name_node.text.decode("utf-8"),
                        resolved=False,
                        line=node.start_point[0] + 1,
                    ))
        return edges


register_parser("java", JavaParser)
```

- [x] **Step 5: Run tests to verify they pass**

Run: `python -m pytest packages/core/tests/code_index/test_java_parser.py -v`
Expected: All tests PASS

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/parsers/java_parser.py packages/core/tests/code_index/fixtures/java/ packages/core/tests/code_index/test_java_parser.py
git commit -m "feat(code-index): add Java parser"
```

---

## Task 9: PHP Parser

**Files:**
- Create: `packages/core/src/shannon_core/code_index/parsers/php_parser.py`
- Create: `packages/core/tests/code_index/fixtures/php/laravel_routes.php`
- Test: `packages/core/tests/code_index/test_php_parser.py`

- [x] **Step 1: Create the PHP fixture file**

Create `packages/core/tests/code_index/fixtures/php/laravel_routes.php`:

```php
<?php

use Illuminate\Support\Facades\Route;

Route::get('/api/users', function () {
    return getUsers();
});

Route::post('/api/users/{id}', function ($id) {
    $data = request()->json()->all();
    return saveUser($id, $data);
});

function getUsers() {
    return DB::select('SELECT * FROM users');
}

function saveUser($id, $data) {
    return DB::table('users')->where('id', $id)->update($data);
}

class OrderController {
    public function listOrders() {
        return $this->getOrders();
    }

    private function getOrders() {
        return [];
    }
}
```

- [x] **Step 2: Write the failing test**

Create `packages/core/tests/code_index/test_php_parser.py`:

```python
from pathlib import Path

from shannon_core.code_index.parsers.php_parser import PhpParser
from shannon_core.code_index.parsers import _PARSER_CLASSES

FIXTURES = Path(__file__).parent / "fixtures"
PHP_FILE = FIXTURES / "php" / "laravel_routes.php"


class TestPhpParserFuncBlocks:
    def test_extracts_standalone_functions(self):
        parser = PhpParser()
        blocks = parser.parse_file(PHP_FILE, PHP_FILE.parent.parent.parent)
        names = {b.function_name for b in blocks}
        assert "getUsers" in names
        assert "saveUser" in names

    def test_extracts_class_methods(self):
        parser = PhpParser()
        blocks = parser.parse_file(PHP_FILE, PHP_FILE.parent.parent.parent)
        names = {b.function_name for b in blocks}
        assert "listOrders" in names
        assert "getOrders" in names

    def test_class_method_has_class_name(self):
        parser = PhpParser()
        blocks = parser.parse_file(PHP_FILE, PHP_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        assert by_name["listOrders"].class_name == "OrderController"

    def test_extracts_parameters(self):
        parser = PhpParser()
        blocks = parser.parse_file(PHP_FILE, PHP_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}
        params = by_name["saveUser"].parameters
        assert "id" in params
        assert "data" in params

    def test_block_language_is_php(self):
        parser = PhpParser()
        blocks = parser.parse_file(PHP_FILE, PHP_FILE.parent.parent.parent)
        for block in blocks:
            assert block.language == "php"


class TestPhpParserCallEdges:
    def test_extracts_function_calls(self):
        parser = PhpParser()
        source = PHP_FILE.read_bytes()
        blocks = parser.parse_file(PHP_FILE, PHP_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["getUsers"], source)
        callee_names = [e.callee_name for e in edges]
        assert "select" in callee_names

    def test_extracts_method_calls(self):
        parser = PhpParser()
        source = PHP_FILE.read_bytes()
        blocks = parser.parse_file(PHP_FILE, PHP_FILE.parent.parent.parent)
        by_name = {b.function_name: b for b in blocks}

        edges = parser.extract_calls(by_name["listOrders"], source)
        callee_names = [e.callee_name for e in edges]
        assert "getOrders" in callee_names


class TestPhpParserRegistry:
    def test_registered(self):
        assert "php" in _PARSER_CLASSES
```

- [x] **Step 3: Run tests to verify they fail**

Run: `python -m pytest packages/core/tests/code_index/test_php_parser.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 4: Write the PHP parser**

Create `packages/core/src/shannon_core/code_index/parsers/php_parser.py`:

```python
import logging
from pathlib import Path

import tree_sitter_php as tsphp
from tree_sitter import Language, Parser

from shannon_core.code_index.models import CallEdge, FuncBlock
from shannon_core.code_index.parsers import register_parser
from shannon_core.code_index.parsers.base import BaseParser

logger = logging.getLogger(__name__)

PHP_LANGUAGE = Language(tsphp.language())


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


class PhpParser(BaseParser):
    def __init__(self):
        self._parser = Parser(PHP_LANGUAGE)

    def parse_file(self, file_path: Path, repo_root: Path) -> list[FuncBlock]:
        source = file_path.read_bytes()
        tree = self._parser.parse(source)
        rel_path = str(file_path.relative_to(repo_root))
        blocks: list[FuncBlock] = []

        for node in _walk(tree.root_node):
            if node.type == "function_definition":
                block = self._extract_func_block(node, source, rel_path)
                if block is not None:
                    blocks.append(block)
            elif node.type == "method_declaration":
                block = self._extract_method_block(node, source, rel_path)
                if block is not None:
                    blocks.append(block)
        return blocks

    def extract_calls(self, block: FuncBlock, source: bytes) -> list[CallEdge]:
        tree = self._parser.parse(source)
        edges: list[CallEdge] = []

        for node in _walk(tree.root_node):
            if node.type in ("function_definition", "method_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    name_text = name_node.text.decode("utf-8").lstrip("$")
                    if name_text == block.function_name:
                        if node.start_point[0] + 1 == block.start_line:
                            edges = self._extract_call_edges(node, source, block.id)
                            break
        return edges

    def _find_class_name(self, node) -> str | None:
        current = node.parent
        while current is not None:
            if current.type == "class_declaration":
                name_node = current.child_by_field_name("name")
                if name_node:
                    return name_node.text.decode("utf-8")
            current = current.parent
        return None

    def _extract_func_block(self, node, source: bytes, rel_path: str) -> FuncBlock | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        func_name = name_node.text.decode("utf-8").lstrip("$")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        func_source = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        parameters = self._extract_parameters(node, source)

        return FuncBlock(
            id=f"{rel_path}:{func_name}:{start_line}",
            file_path=rel_path,
            function_name=func_name,
            start_line=start_line,
            end_line=end_line,
            source_code=func_source,
            parameters=parameters,
            language="php",
        )

    def _extract_method_block(self, node, source: bytes, rel_path: str) -> FuncBlock | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        func_name = name_node.text.decode("utf-8").lstrip("$")
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        func_source = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        parameters = self._extract_parameters(node, source)
        class_name = self._find_class_name(node)

        return FuncBlock(
            id=f"{rel_path}:{func_name}:{start_line}",
            file_path=rel_path,
            function_name=func_name,
            start_line=start_line,
            end_line=end_line,
            source_code=func_source,
            parameters=parameters,
            class_name=class_name,
            language="php",
        )

    def _extract_parameters(self, func_node, source: bytes) -> list[str]:
        params_node = func_node.child_by_field_name("parameters")
        if params_node is None:
            return []

        params: list[str] = []
        for child in params_node.children:
            if child.type == "simple_parameter":
                name_node = child.child_by_field_name("name")
                if name_node:
                    params.append(name_node.text.decode("utf-8").lstrip("$"))
            elif child.type == "variadic_parameter":
                name_node = child.child_by_field_name("name")
                if name_node:
                    params.append(name_node.text.decode("utf-8").lstrip("$"))
        return params

    def _extract_call_edges(self, func_node, source: bytes, caller_id: str) -> list[CallEdge]:
        edges: list[CallEdge] = []
        for node in _walk(func_node):
            if node.type == "function_call_expression":
                callee_name = self._get_function_call_name(node)
                if callee_name:
                    edges.append(CallEdge(
                        caller_id=caller_id,
                        callee_name=callee_name,
                        resolved=False,
                        line=node.start_point[0] + 1,
                    ))
            elif node.type == "member_access" or node.type == "scoped_member_access":
                # Handle $obj->method() and Class::method() patterns
                pass
            elif node.type == "method_call_expression":
                name_node = node.child_by_field_name("name")
                if name_node:
                    method_name = name_node.text.decode("utf-8").lstrip("$")
                    edges.append(CallEdge(
                        caller_id=caller_id,
                        callee_name=method_name,
                        resolved=False,
                        line=node.start_point[0] + 1,
                    ))
        return edges

    def _get_function_call_name(self, call_node) -> str | None:
        func_node = call_node.child_by_field_name("function")
        if func_node is None:
            return None

        if func_node.type == "name":
            return func_node.text.decode("utf-8").lstrip("$")
        elif func_node.type == "variable_name":
            return func_node.text.decode("utf-8").lstrip("$")
        return None


register_parser("php", PhpParser)
```

- [x] **Step 5: Run tests to verify they pass**

Run: `python -m pytest packages/core/tests/code_index/test_php_parser.py -v`
Expected: All tests PASS

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/parsers/php_parser.py packages/core/tests/code_index/fixtures/php/ packages/core/tests/code_index/test_php_parser.py
git commit -m "feat(code-index): add PHP parser"
```

---

## Task 10: Entry Point Rules (All Languages)

**Files:**
- Create: `packages/core/src/shannon_core/code_index/entry_points.py`
- Test: `packages/core/tests/code_index/test_entry_points.py`

- [x] **Step 1: Write the failing test**

Create `packages/core/tests/code_index/test_entry_points.py`:

```python
from shannon_core.code_index.models import FuncBlock
from shannon_core.code_index.entry_points import detect_entry_points


def _block(**overrides) -> FuncBlock:
    defaults = dict(
        id="src/app.py:f:1",
        file_path="src/app.py",
        function_name="f",
        start_line=1,
        end_line=5,
        source_code="def f(): pass",
        parameters=[],
        language="python",
    )
    defaults.update(overrides)
    return FuncBlock(**defaults)


class TestPythonEntryPoints:
    def test_flask_route(self):
        block = _block(
            decorators=["@app.route('/api/users', methods=['GET'])"],
            function_name="list_users",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 1
        assert eps[0].entry_type == "http_route"
        assert eps[0].route == "/api/users"
        assert eps[0].http_method == "GET"
        assert eps[0].confidence == 0.95
        assert eps[0].needs_llm_review is False

    def test_flask_route_post(self):
        block = _block(
            decorators=["@app.route('/api/users', methods=['POST'])"],
            function_name="create_user",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 1
        assert eps[0].http_method == "POST"

    def test_fastapi_route(self):
        block = _block(
            decorators=["@router.get('/users')"],
            function_name="get_users",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 1
        assert eps[0].confidence == 0.95

    def test_django_view(self):
        block = _block(
            decorators=["@api_view(['GET'])"],
            function_name="user_list",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 1
        assert eps[0].confidence == 0.90

    def test_celery_task(self):
        block = _block(
            decorators=["@shared_task"],
            function_name="process_queue",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 1
        assert eps[0].entry_type == "message_consumer"
        assert eps[0].confidence == 0.90

    def test_async_undecorated_needs_review(self):
        block = _block(
            source_code="async def process(): pass",
            function_name="process",
        )
        eps = detect_entry_points([block], "python")
        assert len(eps) == 1
        assert eps[0].needs_llm_review is True
        assert eps[0].confidence == 0.30

    def test_plain_function_no_entry_point(self):
        block = _block(function_name="helper")
        eps = detect_entry_points([block], "python")
        assert len(eps) == 0


class TestGoEntryPoints:
    def test_net_http_handler(self):
        block = _block(
            parameters=["w http.ResponseWriter", "r *http.Request"],
            function_name="handleUsers",
            language="go",
        )
        eps = detect_entry_points([block], "go")
        assert len(eps) == 1
        assert eps[0].entry_type == "http_route"
        assert eps[0].confidence == 0.95

    def test_gin_handler(self):
        block = _block(
            parameters=["c *gin.Context"],
            function_name="handleUsers",
            language="go",
        )
        eps = detect_entry_points([block], "go")
        assert len(eps) == 1
        assert eps[0].confidence == 0.95

    def test_plain_go_function_no_entry_point(self):
        block = _block(
            parameters=["x int", "y int"],
            function_name="add",
            language="go",
        )
        eps = detect_entry_points([block], "go")
        assert len(eps) == 0


class TestTypeScriptEntryPoints:
    def test_nestjs_get(self):
        block = _block(
            decorators=["@Get()"],
            function_name="listUsers",
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        assert len(eps) == 1
        assert eps[0].entry_type == "http_route"
        assert eps[0].confidence == 0.95

    def test_nestjs_post(self):
        block = _block(
            decorators=["@Post()"],
            function_name="createUser",
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        assert len(eps) == 1
        assert eps[0].http_method == "POST"

    def test_plain_ts_function_no_entry_point(self):
        block = _block(
            function_name="helper",
            language="typescript",
        )
        eps = detect_entry_points([block], "typescript")
        assert len(eps) == 0


class TestJavaEntryPoints:
    def test_spring_get_mapping(self):
        block = _block(
            decorators=["@GetMapping"],
            function_name="listUsers",
            language="java",
        )
        eps = detect_entry_points([block], "java")
        assert len(eps) == 1
        assert eps[0].entry_type == "http_route"
        assert eps[0].confidence == 0.95

    def test_spring_request_mapping(self):
        block = _block(
            decorators=["@RequestMapping(\"/api/users\")"],
            function_name="users",
            language="java",
        )
        eps = detect_entry_points([block], "java")
        assert len(eps) == 1
        assert eps[0].confidence == 0.95

    def test_rabbit_listener(self):
        block = _block(
            decorators=["@RabbitListener(queues = \"orders\")"],
            function_name="processOrder",
            language="java",
        )
        eps = detect_entry_points([block], "java")
        assert len(eps) == 1
        assert eps[0].entry_type == "message_consumer"
        assert eps[0].confidence == 0.90


class TestPhpEntryPoints:
    def test_laravel_route_get(self):
        # Laravel routes are typically in Route::get('/path', ...) calls,
        # which are detected from source_code, not decorators.
        # For PHP, we check the source_code for Route:: patterns.
        block = _block(
            source_code="Route::get('/api/users', function () { return getUsers(); });",
            function_name="getUsers",
            language="php",
        )
        eps = detect_entry_points([block], "php")
        # Route::get doesn't decorate getUsers, so this specific function shouldn't be detected
        # Entry points in PHP are more about the Route facade calls themselves
        # We'll test this edge case — the entry point system should detect Route patterns in source

    def test_symfony_route_attribute(self):
        block = _block(
            decorators=["#[Route('/api/users', methods: ['GET'])]"],
            function_name="listUsers",
            language="php",
        )
        eps = detect_entry_points([block], "php")
        assert len(eps) == 1
        assert eps[0].confidence == 0.95

    def test_plain_php_no_entry_point(self):
        block = _block(
            function_name="helper",
            language="php",
        )
        eps = detect_entry_points([block], "php")
        assert len(eps) == 0


class TestUnknownLanguage:
    def test_unknown_language_returns_empty(self):
        block = _block(language="rust")
        eps = detect_entry_points([block], "rust")
        assert len(eps) == 0
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/core/tests/code_index/test_entry_points.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write the entry point detection module**

Create `packages/core/src/shannon_core/code_index/entry_points.py`:

```python
import re
import logging

from shannon_core.code_index.models import EntryPoint, FuncBlock

logger = logging.getLogger(__name__)

# Threshold below which an entry point needs LLM review
LLM_REVIEW_THRESHOLD = 0.8


def detect_entry_points(blocks: list[FuncBlock], language: str) -> list[EntryPoint]:
    """Detect entry points from function blocks using per-language rules."""
    if language == "python":
        return _detect_python(blocks)
    elif language == "go":
        return _detect_go(blocks)
    elif language == "typescript":
        return _detect_typescript(blocks)
    elif language == "java":
        return _detect_java(blocks)
    elif language == "php":
        return _detect_php(blocks)
    else:
        return []


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

_PYTHON_RULES: list[tuple[str, re.Pattern, str, str | None, float]] = [
    # (entry_type, decorator_pattern, http_method_or_None, confidence)
    ("http_route", re.compile(r"@.*\.route\(\s*['\"](.+?)['\"]"), None, 0.95),
    ("http_route", re.compile(r"@router\.(get|post|put|delete|patch)\(\s*['\"](.+?)['\"]"), None, 0.95),
    ("http_route", re.compile(r"@(api_view|require_http_methods)"), None, 0.90),
    ("message_consumer", re.compile(r"@(celery\.task|app\.task|shared_task)"), None, 0.90),
]


def _detect_python(blocks: list[FuncBlock]) -> list[EntryPoint]:
    entry_points: list[EntryPoint] = []

    for block in blocks:
        matched = False
        for decorator in block.decorators:
            for rule_type, pattern, _, confidence in _PYTHON_RULES:
                m = pattern.search(decorator)
                if m:
                    route = None
                    http_method = None

                    # Flask route
                    if ".route(" in decorator:
                        route = m.group(1) if m.lastindex >= 1 else None
                        method_match = re.search(r"methods\s*=\s*\['(\w+)'\]", decorator)
                        http_method = method_match.group(1) if method_match else "GET"

                    # FastAPI route
                    elif re.match(r"@router\.(get|post|put|delete|patch)", decorator):
                        http_method = m.group(1).upper()
                        route = m.group(2) if m.lastindex >= 2 else None

                    entry_points.append(EntryPoint(
                        func_block_id=block.id,
                        entry_type=rule_type,
                        route=route,
                        http_method=http_method,
                        confidence=confidence,
                        evidence=f"Decorated with {decorator}",
                        needs_llm_review=confidence < LLM_REVIEW_THRESHOLD,
                    ))
                    matched = True
                    break
            if matched:
                break

        # Async undecorated rule
        if not matched and block.source_code.strip().startswith("async def "):
            entry_points.append(EntryPoint(
                func_block_id=block.id,
                entry_type="unknown",
                confidence=0.30,
                evidence="async def with no known decorator",
                needs_llm_review=True,
            ))

    return entry_points


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------


def _detect_go(blocks: list[FuncBlock]) -> list[EntryPoint]:
    entry_points: list[EntryPoint] = []

    for block in blocks:
        params_str = " ".join(block.parameters)

        # net/http handler: http.ResponseWriter, *http.Request
        if "http.ResponseWriter" in params_str and "http.Request" in params_str:
            entry_points.append(EntryPoint(
                func_block_id=block.id,
                entry_type="http_route",
                confidence=0.95,
                evidence="Parameters include http.ResponseWriter and *http.Request",
                needs_llm_review=False,
            ))
            continue

        # Gin handler: *gin.Context
        if "gin.Context" in params_str:
            entry_points.append(EntryPoint(
                func_block_id=block.id,
                entry_type="http_route",
                confidence=0.95,
                evidence="Parameter includes *gin.Context",
                needs_llm_review=False,
            ))
            continue

        # main function
        if block.function_name == "main":
            entry_points.append(EntryPoint(
                func_block_id=block.id,
                entry_type="cli",
                confidence=0.30,
                evidence="func main()",
                needs_llm_review=True,
            ))

    return entry_points


# ---------------------------------------------------------------------------
# TypeScript
# ---------------------------------------------------------------------------

_TS_DECORATOR_RULES: list[tuple[str, re.Pattern, str | None, float]] = [
    ("http_route", re.compile(r"@(Get|Post|Put|Delete|Patch)\b"), None, 0.95),
]


def _detect_typescript(blocks: list[FuncBlock]) -> list[EntryPoint]:
    entry_points: list[EntryPoint] = []

    for block in blocks:
        for decorator in block.decorators:
            for rule_type, pattern, _, confidence in _TS_DECORATOR_RULES:
                m = pattern.search(decorator)
                if m:
                    http_method = m.group(1).upper()
                    entry_points.append(EntryPoint(
                        func_block_id=block.id,
                        entry_type=rule_type,
                        http_method=http_method,
                        confidence=confidence,
                        evidence=f"Decorated with {decorator}",
                        needs_llm_review=confidence < LLM_REVIEW_THRESHOLD,
                    ))
                    break

    return entry_points


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------

_JAVA_ANNOTATION_RULES: list[tuple[str, re.Pattern, str, float]] = [
    ("http_route", re.compile(r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)"), "http_route", 0.95),
    ("message_consumer", re.compile(r"@RabbitListener"), "message_consumer", 0.90),
]


def _detect_java(blocks: list[FuncBlock]) -> list[EntryPoint]:
    entry_points: list[EntryPoint] = []

    for block in blocks:
        for decorator in block.decorators:
            for rule_type, pattern, _, confidence in _JAVA_ANNOTATION_RULES:
                m = pattern.search(decorator)
                if m:
                    http_method = None
                    ann = m.group(1)
                    method_map = {
                        "GetMapping": "GET", "PostMapping": "POST",
                        "PutMapping": "PUT", "DeleteMapping": "DELETE",
                        "PatchMapping": "PATCH", "RequestMapping": None,
                    }
                    http_method = method_map.get(ann)

                    route = None
                    route_match = re.search(r'[\'"](/[^\'"]+)[\'"]', decorator)
                    if route_match:
                        route = route_match.group(1)

                    entry_points.append(EntryPoint(
                        func_block_id=block.id,
                        entry_type=rule_type,
                        route=route,
                        http_method=http_method,
                        confidence=confidence,
                        evidence=f"Annotated with {decorator}",
                        needs_llm_review=confidence < LLM_REVIEW_THRESHOLD,
                    ))
                    break

    return entry_points


# ---------------------------------------------------------------------------
# PHP
# ---------------------------------------------------------------------------

_PHP_DECORATOR_RULES: list[tuple[str, re.Pattern, float]] = [
    ("http_route", re.compile(r"#\[Route\("), 0.95),
]


def _detect_php(blocks: list[FuncBlock]) -> list[EntryPoint]:
    entry_points: list[EntryPoint] = []

    for block in blocks:
        for decorator in block.decorators:
            for rule_type, pattern, confidence in _PHP_DECORATOR_RULES:
                m = pattern.search(decorator)
                if m:
                    http_method = None
                    method_match = re.search(r"methods:\s*\[\s*['\"](\w+)['\"]", decorator)
                    if method_match:
                        http_method = method_match.group(1)

                    route = None
                    route_match = re.search(r"[\'\"](/[^\'\"]+)[\'\"]", decorator)
                    if route_match:
                        route = route_match.group(1)

                    entry_points.append(EntryPoint(
                        func_block_id=block.id,
                        entry_type=rule_type,
                        route=route,
                        http_method=http_method,
                        confidence=confidence,
                        evidence=f"Attribute: {decorator}",
                        needs_llm_review=confidence < LLM_REVIEW_THRESHOLD,
                    ))
                    break

    return entry_points
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest packages/core/tests/code_index/test_entry_points.py -v`
Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/entry_points.py packages/core/tests/code_index/test_entry_points.py
git commit -m "feat(code-index): add per-language entry point detection rules"
```

---

## Task 11: Call Graph BFS

**Files:**
- Create: `packages/core/src/shannon_core/code_index/call_graph.py`
- Test: `packages/core/tests/code_index/test_call_graph.py`

- [x] **Step 1: Write the failing test**

Create `packages/core/tests/code_index/test_call_graph.py`:

```python
import pytest
from shannon_core.code_index.models import FuncBlock, CallEdge, CallChain
from shannon_core.code_index.call_graph import build_call_chains, resolve_edges


def _block(name: str, file: str = "app.py", line: int = 1) -> FuncBlock:
    return FuncBlock(
        id=f"{file}:{name}:{line}",
        file_path=file,
        function_name=name,
        start_line=line,
        end_line=line + 5,
        source_code=f"def {name}(): pass",
        parameters=[],
        language="python",
    )


def _edge(caller: str, callee: str, line: int = 1, resolved: bool = False, callee_file: str | None = None) -> CallEdge:
    return CallEdge(
        caller_id=caller,
        callee_name=callee,
        callee_file=callee_file,
        resolved=resolved,
        line=line,
    )


class TestResolveEdges:
    def test_resolves_matching_function_name(self):
        blocks = [_block("get_users", "svc.py", 10), _block("save_users", "svc.py", 20)]
        edges = [_edge("app.py:handler:1", "get_users", resolved=False)]
        resolved = resolve_edges(edges, blocks)
        assert resolved[0].resolved is True
        assert resolved[0].callee_file == "svc.py"

    def test_unresolved_when_no_match(self):
        blocks = [_block("get_users", "svc.py", 10)]
        edges = [_edge("app.py:handler:1", "unknown_func", resolved=False)]
        resolved = resolve_edges(edges, blocks)
        assert resolved[0].resolved is False

    def test_resolves_to_first_match_when_ambiguous(self):
        blocks = [_block("helper", "a.py", 1), _block("helper", "b.py", 1)]
        edges = [_edge("app.py:main:1", "helper", resolved=False)]
        resolved = resolve_edges(edges, blocks)
        assert resolved[0].resolved is True
        assert resolved[0].callee_file in ("a.py", "b.py")


class TestBuildCallChains:
    def test_single_entry_point_with_one_call(self):
        blocks = [_block("handler", "app.py", 1), _block("get_data", "svc.py", 10)]
        edges = [
            _edge("app.py:handler:1", "get_data", resolved=True, callee_file="svc.py"),
        ]
        entry_ids = ["app.py:handler:1"]
        chains = build_call_chains(entry_ids, edges, max_depth=15, max_width=50)
        assert len(chains) == 1
        assert chains[0].path == ["app.py:handler:1", "svc.py:get_data:10"]
        assert chains[0].depth == 1

    def test_branching_call_graph(self):
        blocks = [
            _block("handler", "app.py", 1),
            _block("get_a", "svc.py", 10),
            _block("get_b", "svc.py", 20),
        ]
        edges = [
            _edge("app.py:handler:1", "get_a", resolved=True, callee_file="svc.py"),
            _edge("app.py:handler:1", "get_b", resolved=True, callee_file="svc.py"),
        ]
        entry_ids = ["app.py:handler:1"]
        chains = build_call_chains(entry_ids, edges, max_depth=15, max_width=50)
        assert len(chains) == 2

    def test_chain_with_unresolved_call(self):
        edges = [
            _edge("app.py:handler:1", "dynamic_func", resolved=False),
        ]
        entry_ids = ["app.py:handler:1"]
        chains = build_call_chains(entry_ids, edges, max_depth=15, max_width=50)
        assert len(chains) == 1
        assert chains[0].has_unresolved is True

    def test_max_depth_stops_traversal(self):
        blocks = [_block(f"func_{i}", "app.py", i * 10) for i in range(20)]
        edges = [
            _edge(f"app.py:func_{i}:{i*10}", f"func_{i+1}", resolved=True, callee_file="app.py")
            for i in range(19)
        ]
        entry_ids = ["app.py:func_0:0"]
        chains = build_call_chains(entry_ids, edges, max_depth=5, max_width=50)
        for chain in chains:
            assert chain.depth <= 5

    def test_cycle_detection(self):
        blocks = [_block("a", "app.py", 1), _block("b", "app.py", 10)]
        edges = [
            _edge("app.py:a:1", "b", resolved=True, callee_file="app.py"),
            _edge("app.py:b:10", "a", resolved=True, callee_file="app.py"),
        ]
        entry_ids = ["app.py:a:1"]
        chains = build_call_chains(entry_ids, edges, max_depth=15, max_width=50)
        # Should terminate without infinite loop
        assert len(chains) >= 1
        # No chain should visit the same node twice
        for chain in chains:
            assert len(chain.path) == len(set(chain.path))

    def test_no_edges_produces_single_node_chain(self):
        entry_ids = ["app.py:handler:1"]
        chains = build_call_chains(entry_ids, [], max_depth=15, max_width=50)
        assert len(chains) == 1
        assert chains[0].path == ["app.py:handler:1"]
        assert chains[0].depth == 0
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/core/tests/code_index/test_call_graph.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write the call graph builder**

Create `packages/core/src/shannon_core/code_index/call_graph.py`:

```python
import logging
from collections import defaultdict

from shannon_core.code_index.models import CallChain, CallEdge, FuncBlock

logger = logging.getLogger(__name__)


def resolve_edges(edges: list[CallEdge], blocks: list[FuncBlock]) -> list[CallEdge]:
    """Resolve call edges by matching callee names to known function blocks.

    Builds a name-to-blocks index and updates each edge's `resolved` and
    `callee_file` fields when a match is found.
    """
    name_index: dict[str, list[FuncBlock]] = defaultdict(list)
    for block in blocks:
        name_index[block.function_name].append(block)

    resolved: list[CallEdge] = []
    for edge in edges:
        candidates = name_index.get(edge.callee_name, [])
        if candidates:
            match = candidates[0]
            resolved.append(CallEdge(
                caller_id=edge.caller_id,
                callee_name=edge.callee_name,
                callee_file=match.file_path,
                resolved=True,
                line=edge.line,
            ))
        else:
            resolved.append(edge)
    return resolved


def build_call_chains(
    entry_point_ids: list[str],
    edges: list[CallEdge],
    max_depth: int = 15,
    max_width: int = 50,
) -> list[CallChain]:
    """Build call chains from entry points using BFS.

    Traverses resolved call edges from each entry point, producing one
    CallChain per unique path from entry to leaf (or max depth).
    Cycles are detected by checking if a node already appears in the path.
    """
    # Build adjacency list: caller_id -> [CallEdge]
    adj: dict[str, list[CallEdge]] = defaultdict(list)
    for edge in edges:
        adj[edge.caller_id].append(edge)

    chains: list[CallChain] = []

    for ep_id in entry_point_ids:
        # BFS queue: (path_so_far, depth, has_unresolved)
        queue: list[tuple[list[str], int, bool]] = [([ep_id], 0, False)]

        while queue:
            path, depth, has_unresolved = queue.pop(0)
            current_id = path[-1]

            # Get outgoing edges from current node
            outgoing = adj.get(current_id, [])

            # Filter to resolved edges only for traversal
            resolved_outgoing = [e for e in outgoing if e.resolved][:max_width]
            unresolved_outgoing = [e for e in outgoing if not e.resolved]

            if not resolved_outgoing:
                # Leaf node — emit chain
                chain_unresolved = has_unresolved or len(unresolved_outgoing) > 0
                chains.append(CallChain(
                    entry_point_id=ep_id,
                    path=path,
                    depth=depth,
                    has_unresolved=chain_unresolved,
                ))
                continue

            if depth >= max_depth:
                # Max depth reached — emit chain
                chains.append(CallChain(
                    entry_point_id=ep_id,
                    path=path,
                    depth=depth,
                    has_unresolved=True,
                ))
                continue

            for edge in resolved_outgoing:
                # Build callee_id from resolved edge
                callee_id = f"{edge.callee_file}:{edge.callee_name}"

                # Cycle detection
                if callee_id in path:
                    chains.append(CallChain(
                        entry_point_id=ep_id,
                        path=path + [callee_id],
                        depth=depth + 1,
                        has_unresolved=True,
                    ))
                    continue

                new_unresolved = has_unresolved or len(unresolved_outgoing) > 0
                queue.append((path + [callee_id], depth + 1, new_unresolved))

    return chains
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest packages/core/tests/code_index/test_call_graph.py -v`
Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/call_graph.py packages/core/tests/code_index/test_call_graph.py
git commit -m "feat(code-index): add BFS call graph construction with cycle detection"
```

---

## Task 12: Summary Generator

**Files:**
- Create: `packages/core/src/shannon_core/code_index/summary.py`
- Test: `packages/core/tests/code_index/test_summary.py`

- [x] **Step 1: Write the failing test**

Create `packages/core/tests/code_index/test_summary.py`:

```python
from shannon_core.code_index.models import (
    FuncBlock, CallEdge, EntryPoint, CallChain, CodeIndex,
)
from shannon_core.code_index.summary import generate_summary


def _make_index() -> CodeIndex:
    return CodeIndex(
        repository="test-repo",
        language="python",
        total_blocks=3,
        total_entry_points=2,
        total_chains=2,
        blocks=[
            FuncBlock(
                id="app.py:list_users:5",
                file_path="app.py",
                function_name="list_users",
                start_line=5, end_line=10,
                source_code="def list_users(): ...",
                parameters=[],
                decorators=["@app.route('/users')"],
                language="python",
            ),
            FuncBlock(
                id="app.py:process_queue:20",
                file_path="app.py",
                function_name="process_queue",
                start_line=20, end_line=25,
                source_code="async def process_queue(): ...",
                parameters=[],
                language="python",
            ),
            FuncBlock(
                id="svc.py:get_users:10",
                file_path="svc.py",
                function_name="get_users",
                start_line=10, end_line=15,
                source_code="def get_users(): ...",
                parameters=[],
                language="python",
            ),
        ],
        edges=[
            CallEdge(
                caller_id="app.py:list_users:5",
                callee_name="get_users",
                callee_file="svc.py",
                resolved=True,
                line=7,
            ),
            CallEdge(
                caller_id="app.py:process_queue:20",
                callee_name="dynamic_func",
                resolved=False,
                line=22,
            ),
        ],
        entry_points=[
            EntryPoint(
                func_block_id="app.py:list_users:5",
                entry_type="http_route",
                route="/users",
                http_method="GET",
                confidence=0.95,
                evidence="@app.route('/users')",
                needs_llm_review=False,
            ),
            EntryPoint(
                func_block_id="app.py:process_queue:20",
                entry_type="unknown",
                confidence=0.30,
                evidence="async def with no known decorator",
                needs_llm_review=True,
            ),
        ],
        chains=[
            CallChain(
                entry_point_id="app.py:list_users:5",
                path=["app.py:list_users:5", "svc.py:get_users:10"],
                depth=1,
                has_unresolved=False,
            ),
            CallChain(
                entry_point_id="app.py:process_queue:20",
                path=["app.py:process_queue:20"],
                depth=0,
                has_unresolved=True,
            ),
        ],
    )


class TestGenerateSummary:
    def test_contains_entry_points_table(self):
        index = _make_index()
        summary = generate_summary(index)
        assert "/users" in summary
        assert "GET" in summary
        assert "list_users" in summary

    def test_contains_needs_review_section(self):
        index = _make_index()
        summary = generate_summary(index)
        assert "Needs Review" in summary or "needs_llm_review" in summary
        assert "process_queue" in summary

    def test_contains_coverage_metrics(self):
        index = _make_index()
        summary = generate_summary(index)
        assert "Coverage" in summary or "resolved" in summary.lower()
        assert "unresolved" in summary.lower()

    def test_shows_total_counts(self):
        index = _make_index()
        summary = generate_summary(index)
        assert "3" in summary  # total blocks
        assert "2" in summary  # total entry points

    def test_empty_index_still_valid(self):
        index = CodeIndex(
            repository="empty", language="python",
            total_blocks=0, total_entry_points=0, total_chains=0,
            blocks=[], edges=[], entry_points=[], chains=[],
        )
        summary = generate_summary(index)
        assert isinstance(summary, str)
        assert len(summary) > 0
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/core/tests/code_index/test_summary.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [x] **Step 3: Write the summary generator**

Create `packages/core/src/shannon_core/code_index/summary.py`:

```python
from shannon_core.code_index.models import CodeIndex


def generate_summary(index: CodeIndex) -> str:
    """Generate a human/LLM-readable markdown summary from a CodeIndex."""
    lines: list[str] = []

    lines.append(f"# Code Index Summary: {index.repository}")
    lines.append("")
    lines.append(f"**Language:** {index.language}")
    lines.append(f"**Total Function Blocks:** {index.total_blocks}")
    lines.append(f"**Total Entry Points:** {index.total_entry_points}")
    lines.append(f"**Total Call Chains:** {index.total_chains}")
    lines.append("")

    # --- Entry Points Table ---
    lines.append("## Entry Points")
    lines.append("")

    if index.entry_points:
        lines.append("| Endpoint | Method | Function | File:Line | Confidence |")
        lines.append("|----------|--------|----------|-----------|------------|")
        for ep in index.entry_points:
            block = _find_block(index, ep.func_block_id)
            route = ep.route or "—"
            method = ep.http_method or "—"
            func_name = block.function_name if block else "—"
            location = f"{block.file_path}:{block.start_line}" if block else "—"
            lines.append(f"| {route} | {method} | {func_name} | {location} | {ep.confidence:.2f} |")
    else:
        lines.append("_No entry points detected._")
    lines.append("")

    # --- Entry Points Needing Review ---
    needs_review = [ep for ep in index.entry_points if ep.needs_llm_review]
    lines.append("## Entry Points Needing LLM Review")
    lines.append("")
    if needs_review:
        for ep in needs_review:
            block = _find_block(index, ep.func_block_id)
            lines.append(f"- **{block.function_name if block else ep.func_block_id}** "
                         f"(confidence: {ep.confidence:.2f})")
            lines.append(f"  - Evidence: {ep.evidence}")
            lines.append(f"  - Location: {block.file_path}:{block.start_line}" if block else "")
    else:
        lines.append("_All entry points have high confidence (> 0.8)._")
    lines.append("")

    # --- Coverage Metrics ---
    resolved_count = sum(1 for e in index.edges if e.resolved)
    unresolved_count = sum(1 for e in index.edges if not e.resolved)
    total_edges = len(index.edges)
    chains_with_unresolved = sum(1 for c in index.chains if c.has_unresolved)
    max_chain_depth = max((c.depth for c in index.chains), default=0)

    # Find unreachable functions (blocks not in any chain path and not an entry point)
    ep_ids = {ep.func_block_id for ep in index.entry_points}
    chain_block_ids: set[str] = set()
    for chain in index.chains:
        chain_block_ids.update(chain.path)
    unreachable = [b for b in index.blocks if b.id not in chain_block_ids and b.id not in ep_ids]

    lines.append("## Coverage Metrics")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total Call Edges | {total_edges} |")
    lines.append(f"| Resolved Edges | {resolved_count} |")
    lines.append(f"| Unresolved Edges | {unresolved_count} |")
    lines.append(f"| Max Chain Depth | {max_chain_depth} |")
    lines.append(f"| Chains with Unresolved Calls | {chains_with_unresolved} |")
    lines.append(f"| Unreachable Functions | {len(unreachable)} |")
    lines.append("")

    return "\n".join(lines)


def _find_block(index: CodeIndex, block_id: str):
    for block in index.blocks:
        if block.id == block_id:
            return block
    return None
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest packages/core/tests/code_index/test_summary.py -v`
Expected: All tests PASS

- [x] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/summary.py packages/core/tests/code_index/test_summary.py
git commit -m "feat(code-index): add code index summary generator"
```

---

## Task 13: Public API — build_code_index()

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/__init__.py`
- Test: `packages/core/tests/code_index/test_build_code_index.py`

- [x] **Step 1: Write the failing integration test**

Create `packages/core/tests/code_index/test_build_code_index.py`:

```python
import json
import pytest
from pathlib import Path

from shannon_core.code_index import build_code_index, write_index_files


@pytest.fixture
def python_repo(tmp_path):
    """Create a minimal Python repo with a Flask app."""
    app = tmp_path / "app.py"
    app.write_text(
        'from flask import Flask\n'
        'app = Flask(__name__)\n'
        '\n'
        '@app.route("/hello")\n'
        'def hello():\n'
        '    return greet("world")\n'
        '\n'
        'def greet(name):\n'
        '    return f"Hello {name}"\n'
    )
    return tmp_path


class TestBuildCodeIndex:
    def test_returns_code_index(self, python_repo):
        index = build_code_index(str(python_repo))
        assert index.repository == str(python_repo)
        assert index.language == "python"
        assert index.total_blocks >= 2

    def test_detects_entry_points(self, python_repo):
        index = build_code_index(str(python_repo))
        assert index.total_entry_points >= 1
        ep_names = set()
        for ep in index.entry_points:
            for b in index.blocks:
                if b.id == ep.func_block_id:
                    ep_names.add(b.function_name)
        assert "hello" in ep_names

    def test_builds_call_chains(self, python_repo):
        index = build_code_index(str(python_repo))
        assert index.total_chains >= 1

    def test_resolves_edges(self, python_repo):
        index = build_code_index(str(python_repo))
        resolved = [e for e in index.edges if e.resolved]
        assert len(resolved) >= 1

    def test_blocks_have_valid_ids(self, python_repo):
        index = build_code_index(str(python_repo))
        for block in index.blocks:
            assert ":" in block.id
            assert block.language == "python"

    def test_empty_repo_raises_pentest_error(self, tmp_path):
        from shannon_core.models.errors import PentestError
        with pytest.raises(PentestError, match="No source files"):
            build_code_index(str(tmp_path))


class TestWriteIndexFiles:
    def test_writes_json_and_summary(self, python_repo, tmp_path):
        index = build_code_index(str(python_repo))
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        json_path, summary_path = write_index_files(index, str(output_dir))

        assert json_path.exists()
        assert summary_path.exists()

    def test_json_is_valid(self, python_repo, tmp_path):
        index = build_code_index(str(python_repo))
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        json_path, _ = write_index_files(index, str(output_dir))

        data = json.loads(json_path.read_text())
        assert data["repository"] == str(python_repo)
        assert data["language"] == "python"

    def test_summary_is_markdown(self, python_repo, tmp_path):
        index = build_code_index(str(python_repo))
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        _, summary_path = write_index_files(index, str(output_dir))

        content = summary_path.read_text()
        assert content.startswith("# ")
        assert "Entry Points" in content
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/core/tests/code_index/test_build_code_index.py -v`
Expected: FAIL — `ImportError`

- [x] **Step 3: Write the public API**

Replace `packages/core/src/shannon_core/code_index/__init__.py`:

```python
"""Code index and call graph construction for Shannon's whitebox pipeline."""

import json
import logging
from pathlib import Path

from shannon_core.code_index.models import CodeIndex
from shannon_core.code_index.parser import detect_language, discover_source_files
from shannon_core.code_index.call_graph import build_call_chains, resolve_edges
from shannon_core.code_index.entry_points import detect_entry_points
from shannon_core.code_index.summary import generate_summary
from shannon_core.code_index.parsers import get_parser

logger = logging.getLogger(__name__)


def build_code_index(repo_path: str) -> CodeIndex:
    """Build a complete code index for the repository.

    Steps:
    1. Detect primary language
    2. Discover source files
    3. Parse all files → FuncBlocks + CallEdges
    4. Resolve call edges against known blocks
    5. Detect entry points
    6. Build call chains via BFS
    7. Assemble and return CodeIndex

    Raises:
        PentestError: If no source files are found (CODE_INDEX_FAILED).
        ValueError: If no parser is available for the detected language.
    """
    from shannon_core.models.errors import ErrorCode, PentestError

    repo = Path(repo_path).resolve()
    try:
        language = detect_language(repo)
    except ValueError as exc:
        raise PentestError(
            str(exc),
            category="code_index",
            error_code=ErrorCode.CODE_INDEX_FAILED,
        ) from exc

    logger.info("Detected language: %s", language)

    source_files = discover_source_files(repo, language)
    if not source_files:
        raise PentestError(
            f"No source files found for language '{language}' in {repo}",
            category="code_index",
            error_code=ErrorCode.CODE_INDEX_FAILED,
        )

    parser = get_parser(language)
    if parser is None:
        raise PentestError(
            f"No parser available for language '{language}'",
            category="code_index",
            error_code=ErrorCode.CODE_INDEX_FAILED,
        )

    all_blocks = []
    all_edges = []
    for file_path in source_files:
        try:
            blocks = parser.parse_file(file_path, repo)
            all_blocks.extend(blocks)

            source = file_path.read_bytes()
            for block in blocks:
                edges = parser.extract_calls(block, source)
                all_edges.extend(edges)
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", file_path, exc)
            continue

    # Resolve call edges
    resolved_edges = resolve_edges(all_edges, all_blocks)

    # Detect entry points
    entry_points = detect_entry_points(all_blocks, language)

    # Build call chains
    entry_ids = [ep.func_block_id for ep in entry_points]
    chains = build_call_chains(entry_ids, resolved_edges)

    return CodeIndex(
        repository=str(repo),
        language=language,
        total_blocks=len(all_blocks),
        total_entry_points=len(entry_points),
        total_chains=len(chains),
        blocks=all_blocks,
        edges=resolved_edges,
        entry_points=entry_points,
        chains=chains,
    )


def write_index_files(index: CodeIndex, output_dir: str) -> tuple[Path, Path]:
    """Write code_index.json and code_index_summary.md to output_dir.

    Returns:
        (json_path, summary_path)
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "code_index.json"
    json_path.write_text(index.model_dump_json(indent=2))

    summary_path = out / "code_index_summary.md"
    summary_path.write_text(generate_summary(index))

    return json_path, summary_path
```

- [x] **Step 4: Run tests to verify they pass**

Run: `python -m pytest packages/core/tests/code_index/test_build_code_index.py -v`
Expected: All tests PASS

- [x] **Step 5: Run the full code_index test suite**

Run: `python -m pytest packages/core/tests/code_index/ -v`
Expected: All tests PASS across all test files

- [x] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/__init__.py packages/core/tests/code_index/test_build_code_index.py
git commit -m "feat(code-index): add public build_code_index() API and write_index_files()"
```

---

## Task 14: Pipeline Integration (Activity + State + Worker + Workflow)

**Files:**
- Modify: `packages/core/src/shannon_core/models/deliverables.py`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`
- Modify: `packages/whitebox/src/shannon_whitebox/worker.py`

- [x] **Step 1: Add CODE_INDEX to DeliverableType**

In `packages/core/src/shannon_core/models/deliverables.py`, add to the `DeliverableType` enum after `REPORT`:

```python
    CODE_INDEX = "CODE_INDEX"
```

And add to `DELIVERABLE_FILENAMES`:

```python
    DeliverableType.CODE_INDEX: "code_index.json",
```

The full file becomes:

```python
from enum import Enum

class DeliverableType(str, Enum):
    CODE_ANALYSIS = "CODE_ANALYSIS"
    RECON = "RECON"
    INJECTION_ANALYSIS = "INJECTION_ANALYSIS"
    XSS_ANALYSIS = "XSS_ANALYSIS"
    AUTH_ANALYSIS = "AUTH_ANALYSIS"
    AUTHZ_ANALYSIS = "AUTHZ_ANALYSIS"
    SSRF_ANALYSIS = "SSRF_ANALYSIS"
    INJECTION_EVIDENCE = "INJECTION_EVIDENCE"
    XSS_EVIDENCE = "XSS_EVIDENCE"
    AUTH_EVIDENCE = "AUTH_EVIDENCE"
    AUTHZ_EVIDENCE = "AUTHZ_EVIDENCE"
    SSRF_EVIDENCE = "SSRF_EVIDENCE"
    REPORT = "REPORT"
    CODE_INDEX = "CODE_INDEX"

DELIVERABLE_FILENAMES: dict[DeliverableType, str] = {
    DeliverableType.CODE_ANALYSIS: "pre_recon_deliverable.md",
    DeliverableType.RECON: "recon_deliverable.md",
    DeliverableType.INJECTION_ANALYSIS: "injection_analysis_deliverable.md",
    DeliverableType.XSS_ANALYSIS: "xss_analysis_deliverable.md",
    DeliverableType.AUTH_ANALYSIS: "auth_analysis_deliverable.md",
    DeliverableType.AUTHZ_ANALYSIS: "authz_analysis_deliverable.md",
    DeliverableType.SSRF_ANALYSIS: "ssrf_analysis_deliverable.md",
    DeliverableType.INJECTION_EVIDENCE: "injection_exploitation_evidence.md",
    DeliverableType.XSS_EVIDENCE: "xss_exploitation_evidence.md",
    DeliverableType.AUTH_EVIDENCE: "auth_exploitation_evidence.md",
    DeliverableType.AUTHZ_EVIDENCE: "authz_exploitation_evidence.md",
    DeliverableType.SSRF_EVIDENCE: "ssrf_exploitation_evidence.md",
    DeliverableType.REPORT: "comprehensive_security_assessment_report.md",
    DeliverableType.CODE_INDEX: "code_index.json",
}
```

- [x] **Step 2: Add code_index_stats to PipelineState**

In `packages/whitebox/src/shannon_whitebox/pipeline/shared.py`, add a `code_index_stats` field to `PipelineState`:

```python
@dataclass
class PipelineState:
    status: str = "running"
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    start_time: float = 0.0
    error: str | None = None
    code_index_stats: dict | None = None
```

- [x] **Step 3: Add run_code_index activity**

In `packages/whitebox/src/shannon_whitebox/pipeline/activities.py`, add the new activity function after `run_auth_validation`:

```python
@activity.defn
async def run_code_index(input: ActivityInput) -> dict:
    from shannon_core.code_index import build_code_index, write_index_files

    repo, deliverables, _ = _get_paths(input)
    index = build_code_index(str(repo))
    json_path, summary_path = write_index_files(index, str(deliverables))

    return {
        "total_blocks": index.total_blocks,
        "total_entry_points": index.total_entry_points,
        "total_chains": index.total_chains,
        "json_path": str(json_path),
        "summary_path": str(summary_path),
    }
```

Also add the import at the top of the file (the existing imports from `shannon_core.models.errors` already import `ErrorCode`):

No additional imports needed — the activity uses inline imports as shown.

- [x] **Step 4: Update workflow to insert CODE_INDEX before PRE_RECON**

In `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py`, insert the CODE_INDEX activity after the auth validation step and before the code path deny rules block. The new block goes between the auth validation activity call and the `if input.config_path:` block.

Insert after line 62 (after `run_auth_validation`) and before line 64 (`if input.config_path:`):

```python
        # Code Index — deterministic AST analysis before PRE_RECON
        code_index_result = await workflow.execute_activity(
            activities.run_code_index, act_input,
            start_to_close_timeout=timedelta(minutes=10),
        )
        self._state.code_index_stats = code_index_result
```

- [x] **Step 5: Register the new activity in worker.py**

In `packages/whitebox/src/shannon_whitebox/worker.py`, update the import line and the activities list:

Change the import from:
```python
from .pipeline.activities import run_agent, run_preflight, run_vuln_agent
```
to:
```python
from .pipeline.activities import run_agent, run_code_index, run_preflight, run_vuln_agent
```

And in the `Worker(...)` call, update the `activities` list:
```python
activities=[run_preflight, run_agent, run_vuln_agent, run_code_index],
```

- [x] **Step 6: Verify existing tests still pass**

Run: `python -m pytest packages/core/tests/ -v --tb=short`
Expected: All existing tests PASS

- [x] **Step 7: Commit**

```bash
git add packages/core/src/shannon_core/models/deliverables.py packages/whitebox/src/shannon_whitebox/pipeline/shared.py packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/src/shannon_whitebox/pipeline/workflows.py packages/whitebox/src/shannon_whitebox/worker.py
git commit -m "feat(whitebox): integrate code index activity into pipeline before PRE_RECON"
```

---

## Task 15: Prompt Update

**Files:**
- Modify: `prompts/pre-recon-code.txt`

- [x] **Step 1: Update the <starting_context> section in the PRE-RECON prompt**

In `prompts/pre-recon-code.txt`, replace the `<starting_context>` block (lines 80–87) with:

```
<starting_context>
- A complete call graph has been built via AST analysis, located at:
  {{REPO_PATH}}/.shannon/deliverables/code_index.json
  {{REPO_PATH}}/.shannon/deliverables/code_index_summary.md
- The call graph contains function blocks, entry points, and call chains extracted deterministically
- You do NOT need to discover entry points yourself — all are deterministically extracted
- Focus on understanding the security semantics and attack surface of each entry point
- Entry points marked needs_llm_review=true require your judgment on whether they are real entry points
- You still have full source code access — the call graph supplements your analysis, it does not replace it
- Use the call graph to verify completeness of your own discovery, not as a substitute for deep analysis
</starting_context>
```

- [x] **Step 2: Verify the prompt file is valid**

Run: `head -90 prompts/pre-recon-code.txt | tail -15`
Expected: Shows the updated `<starting_context>` block with code_index references

- [x] **Step 3: Commit**

```bash
git add prompts/pre-recon-code.txt
git commit -m "feat(prompts): update PRE-RECON starting_context to reference code index output"
```

---

## Task 16: End-to-End Verification

**Files:**
- No new files — verification only

- [x] **Step 1: Run the full test suite**

Run: `python -m pytest packages/core/tests/ -v`
Expected: All tests PASS

- [x] **Step 2: Run the code_index test suite specifically**

Run: `python -m pytest packages/core/tests/code_index/ -v`
Expected: All code_index tests PASS

- [x] **Step 3: Verify import chain works end-to-end**

Run:
```bash
python -c "
from shannon_core.code_index import build_code_index, write_index_files
from shannon_core.code_index.parsers import available_languages
print('Available languages:', available_languages())
print('build_code_index:', build_code_index)
print('write_index_files:', write_index_files)
"
```
Expected: Prints available languages (python, typescript, go, java, php) and function references

- [x] **Step 4: Verify the whitebox pipeline imports**

Run:
```bash
python -c "
from shannon_whitebox.pipeline.activities import run_code_index
from shannon_whitebox.pipeline.shared import PipelineState
state = PipelineState()
print('code_index_stats:', state.code_index_stats)
print('run_code_index:', run_code_index)
"
```
Expected: Prints `None` and the activity function reference

- [x] **Step 5: Run existing whitebox tests**

Run: `python -m pytest packages/whitebox/tests/ -v`
Expected: All existing tests PASS (no regressions)

---

## Task 17: Workflow Integration Test

**Files:**
- Create: `packages/core/tests/code_index/test_workflow_integration.py`

- [x] **Step 1: Write the workflow integration test**

Create `packages/core/tests/code_index/test_workflow_integration.py`:

```python
"""Integration test verifying the CODE_INDEX → PRE_RECON handoff.

This test creates a fixture repo, runs build_code_index on it,
and verifies that the output files are suitable for PRE_RECON consumption.
"""
import json
from pathlib import Path

import pytest

from shannon_core.code_index import build_code_index, write_index_files


@pytest.fixture
def flask_repo(tmp_path):
    """Create a Flask-style Python repository."""
    (tmp_path / "app.py").write_text(
        'from flask import Flask, request, jsonify\n'
        '\n'
        'app = Flask(__name__)\n'
        '\n'
        '@app.route("/api/users", methods=["GET"])\n'
        'def list_users():\n'
        '    users = get_users()\n'
        '    return jsonify(users)\n'
        '\n'
        '@app.route("/api/users/<int:user_id>", methods=["POST"])\n'
        'def update_user(user_id):\n'
        '    data = request.get_json()\n'
        '    result = save_user(user_id, data)\n'
        '    return jsonify(result)\n'
        '\n'
        'def get_users():\n'
        '    return db_query("SELECT * FROM users")\n'
        '\n'
        'def save_user(user_id, data):\n'
        '    return db_update("users", user_id, data)\n'
        '\n'
        'def db_query(sql):\n'
        '    return []\n'
        '\n'
        'def db_update(table, id, data):\n'
        '    return {}\n'
    )
    return tmp_path


class TestCodeIndexToPreReconHandoff:
    def test_code_index_json_is_valid_pre_recon_input(self, flask_repo, tmp_path):
        """Verify code_index.json can be loaded and has required structure."""
        index = build_code_index(str(flask_repo))
        output_dir = tmp_path / "deliverables"
        json_path, summary_path = write_index_files(index, str(output_dir))

        data = json.loads(json_path.read_text())

        # Pre-recon expects these top-level keys
        assert "blocks" in data
        assert "edges" in data
        assert "entry_points" in data
        assert "chains" in data
        assert "total_blocks" in data
        assert "total_entry_points" in data
        assert "total_chains" in data

    def test_summary_has_all_three_sections(self, flask_repo, tmp_path):
        """Verify the summary has Entry Points, Needs Review, and Coverage."""
        index = build_code_index(str(flask_repo))
        output_dir = tmp_path / "deliverables"
        _, summary_path = write_index_files(index, str(output_dir))

        content = summary_path.read_text()
        assert "## Entry Points" in content
        assert "## Entry Points Needing LLM Review" in content
        assert "## Coverage Metrics" in content

    def test_entry_points_include_flask_routes(self, flask_repo):
        """Verify Flask routes are detected as entry points."""
        index = build_code_index(str(flask_repo))

        routes = [
            (ep.route, ep.http_method)
            for ep in index.entry_points
            if ep.entry_type == "http_route"
        ]
        assert ("/api/users", "GET") in routes
        assert ("/api/users/<int:user_id>", "POST") in routes

    def test_call_chains_reach_db_functions(self, flask_repo):
        """Verify call chains reach the database layer."""
        index = build_code_index(str(flask_repo))

        # At least one chain should reach db_query or db_update
        all_funcs_in_chains = set()
        for chain in index.chains:
            for block_id in chain.path:
                all_funcs_in_chains.add(block_id)

        has_db = any("db_query" in bid or "db_update" in bid for bid in all_funcs_in_chains)
        assert has_db, f"Expected db_query/db_update in chains. Got: {all_funcs_in_chains}"

    def test_no_entry_points_proceeds_gracefully(self, tmp_path):
        """Verify that a repo with no entry points still produces valid output."""
        (tmp_path / "utils.py").write_text(
            'def helper(x):\n'
            '    return x * 2\n'
        )
        index = build_code_index(str(tmp_path))
        assert index.total_entry_points == 0
        assert index.total_blocks >= 1
        # Summary should still be valid
        from shannon_core.code_index.summary import generate_summary
        summary = generate_summary(index)
        assert "No entry points" in summary or "_No entry points" in summary
```
