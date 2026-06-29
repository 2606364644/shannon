# GitNexus MCP 调用层修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 GitNexus MCP 调用层与 GitNexus 1.6.7 的三重失配（`call_tool` 漏传 `repo` / `_parse_tool_result` 用 `json.loads` 不容 trailing 提示 / cypher 返回 markdown 表格），让 GitNexus 轨在多 repo 索引环境下产出非空调用图（`chains>0` → `taint_flows>0` → GitNexus 轨有结果）。

**Architecture:** 集中在 `GitNexusMCPClient`（`gitnexus_mcp.py`）一处修：`call_tool` 自动注入 `repo=str(self.repo_root)`（激活现有死字段，6 处调用全受益）；`_parse_tool_result` 用 `JSONDecoder().raw_decode()` 容忍「JSON+trailing 提示」、把 cypher 的 `{markdown,row_count}` 解析成 `rows`、解析失败/ambiguous 时 log+返 `None`。消费层仅 `build_call_graph_from_gitnexus` 的 cypher 改读 `rows`。可观测性 log 落在 `run_code_index` activity（core 包不持有 audit session）。

**Tech Stack:** Python 3、pytest + pytest-asyncio、temporalio、pydantic。

## Global Constraints

- **守 CLAUDE.md §1 双轨铁律**：只动 GitNexus 轨自己的 MCP 调用层；不碰 LLM 轨 prompt、不喂确定性产物给 LLM 轨。
- **GitNexus 轨保持 non-fatal**：失败 log warning + 空结果靠 LLM 轨兜底（`workflows.py:362-405` 的 try/except 不动），不引入硬失败。
- **死代码不动**：`find_sinks_by_patterns` / `trace_from_sink` / `get_function_context`（`gitnexus_call_graph.py`）零调用点，本 plan 不碰。
- **core 包不引 whitebox 依赖**：可观测性 log 用 whitebox 的 `get_audit_session`，落点在 `run_code_index` activity（whitebox 层），不在 core 函数里。
- **测试只跑改动相关文件**（memory: pytest 全量会 hang）。

**Spec:** `docs/superpowers/specs/2026-06-30-gitnexus-mcp-call-layer-fix-design.md`

---

## File Structure

| 文件 | 责任 | 本 plan 改动 |
|---|---|---|
| `packages/core/src/shannon_core/code_index/gitnexus_mcp.py` | MCP 客户端：JSON-RPC + 结果解析 | 新增 `_parse_md_table`；`_parse_tool_result`+`_parse_text` 健壮化；`call_tool` 注入 repo |
| `packages/core/src/shannon_core/code_index/gitnexus_call_graph.py` | 调用图构建（消费 MCP 结果） | `build_call_graph_from_gitnexus` cypher 消费改读 `rows`（仅 :233-263） |
| `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` | 白盒 activity | `run_code_index` build 后加 chains 统计 `log_info` |
| `packages/core/tests/code_index/test_gitnexus_mcp.py` | MCP 单测 | 加 `_parse_md_table` / `_parse_text` / repo 注入测试 |
| `packages/core/tests/code_index/test_gitnexus_call_graph.py` | 调用图单测 | 加 cypher→rows 回归锚点；更新 2 个现有测试的 cypher fixture |
| `packages/whitebox/tests/test_run_code_index.py` | run_code_index 单测 | 加 chains=0 warning log 测试 |

---

### Task 1: `_parse_md_table` — markdown 表格解析器

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/gitnexus_mcp.py`（模块级新增函数，放在 `MCP_READ_TIMEOUT` 常量后、`class GitNexusMCPClient` 前）
- Test: `packages/core/tests/code_index/test_gitnexus_mcp.py`

**Interfaces:**
- Produces: `_parse_md_table(markdown: str) -> list[dict]`（模块级函数，Task 2 的 `_parse_text` 调用）

- [ ] **Step 1: Write the failing test**

追加到 `test_gitnexus_mcp.py`（顶部 import 区加 `_parse_md_table`）：

```python
from shannon_core.code_index.gitnexus_mcp import GitNexusMCPClient, _parse_md_table


class TestParseMdTable:
    def test_normal_table(self):
        md = "| caller_file | caller_name |\n| --- | --- |\n| app.py | handler |\n| svc.py | get_users |"
        assert _parse_md_table(md) == [
            {"caller_file": "app.py", "caller_name": "handler"},
            {"caller_file": "svc.py", "caller_name": "get_users"},
        ]

    def test_empty_table(self):
        assert _parse_md_table("") == []
        assert _parse_md_table("| a |\n| --- |") == []  # 只有表头+分隔，无数据行

    def test_missing_separator_returns_empty(self):
        # 无 |---| 分隔行 → len(lines) < 3 → []
        assert _parse_md_table("| a |\n| 1 |") == []

    def test_column_mismatch_skipped(self):
        md = "| a | b |\n| --- | --- |\n| 1 | 2 |\n| 3 |"  # 末行列数不齐
        assert _parse_md_table(md) == [{"a": "1", "b": "2"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/core/tests/code_index/test_gitnexus_mcp.py::TestParseMdTable -v`
Expected: FAIL with `ImportError: cannot import name '_parse_md_table'`

- [ ] **Step 3: Write minimal implementation**

在 `gitnexus_mcp.py` 的 `MCP_STOP_TIMEOUT = 5` 行之后、`class GitNexusMCPClient` 之前插入：

```python
def _parse_md_table(markdown: str) -> list[dict]:
    """Parse a GitNexus cypher markdown table into list[dict].

    GitNexus 1.6.7 returns cypher results as ``{"markdown": "| col | col |\\n| --- |\\n| ... |"}``
    rather than raw records. Extract rows into dicts keyed by header name.
    Skip the header row and the ``| --- |`` separator. Rows with a column
    count mismatch are dropped.
    """
    lines = [ln for ln in markdown.strip().split("\n") if ln.strip().startswith("|")]
    if len(lines) < 3:
        return []
    headers = [h.strip() for h in lines[0].strip("|").split("|")]
    rows: list[dict] = []
    for line in lines[2:]:  # skip header (lines[0]) + separator (lines[1])
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells)))
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/core/tests/code_index/test_gitnexus_mcp.py::TestParseMdTable -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/gitnexus_mcp.py packages/core/tests/code_index/test_gitnexus_mcp.py
git commit -m "feat(gitnexus): add _parse_md_table for cypher markdown table parsing"
```

---

### Task 2: `_parse_tool_result` 健壮化（raw_decode + rows + 失败返 None）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/gitnexus_mcp.py:157-175`（`_parse_tool_result` 重构 + 新增 `_parse_text`）
- Test: `packages/core/tests/code_index/test_gitnexus_mcp.py`

**Interfaces:**
- Consumes: `_parse_md_table`（Task 1）
- Produces: `_parse_tool_result` 返回 `dict|list|None`（不再返 str）；`_parse_text(text) -> dict|list|None`（`@staticmethod`）

- [ ] **Step 1: Write the failing test**

追加到 `test_gitnexus_mcp.py`：

```python
class TestParseToolResultRobustness:
    def test_json_with_trailing_hint(self, tmp_path):
        """GitNexus 1.6.7: JSON + trailing 提示文本（json.loads 会 Extra data 失败）。"""
        client = GitNexusMCPClient(tmp_path)
        text = '{"processes": [], "definitions": [{"name": "handler"}]}\nUse context({...}) for details.'
        result = client._parse_tool_result({"content": [{"type": "text", "text": text}]})
        assert isinstance(result, dict)
        assert result["definitions"] == [{"name": "handler"}]

    def test_cypher_markdown_table_decoded_to_rows(self, tmp_path):
        client = GitNexusMCPClient(tmp_path)
        text = '{"markdown": "| caller_file | caller_name |\\n| --- | --- |\\n| app.py | handler |", "row_count": 1}\nhint'
        result = client._parse_tool_result({"content": [{"type": "text", "text": text}]})
        assert result["rows"] == [{"caller_file": "app.py", "caller_name": "handler"}]

    def test_error_text_returns_none_with_warning(self, tmp_path, caplog):
        client = GitNexusMCPClient(tmp_path)
        text = 'Error: Multiple repositories indexed. Specify which one with the "repo" parameter.'
        with caplog.at_level("WARNING", logger="shannon_core.code_index.gitnexus_mcp"):
            result = client._parse_tool_result({"content": [{"type": "text", "text": text}]})
        assert result is None
        assert "non-JSON" in caplog.text

    def test_ambiguous_returns_none_with_warning(self, tmp_path, caplog):
        client = GitNexusMCPClient(tmp_path)
        text = '{"status": "ambiguous", "message": "Found 4 symbols matching"}\nhint'
        with caplog.at_level("WARNING", logger="shannon_core.code_index.gitnexus_mcp"):
            result = client._parse_tool_result({"content": [{"type": "text", "text": text}]})
        assert result is None
        assert "ambiguous" in caplog.text

    def test_empty_result_returns_none(self, tmp_path):
        client = GitNexusMCPClient(tmp_path)
        assert client._parse_tool_result({}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/core/tests/code_index/test_gitnexus_mcp.py::TestParseToolResultRobustness -v`
Expected: FAIL（`test_json_with_trailing_hint` 等：当前实现 `json.loads` 抛 `JSONDecodeError` → 返 str，断言 `isinstance(result, dict)` 失败）

- [ ] **Step 3: Write minimal implementation**

替换 `gitnexus_mcp.py:157-175` 整个 `_parse_tool_result` 方法为：

```python
    def _parse_tool_result(self, result: dict) -> list | dict | str | None:
        """Parse MCP tool result content into Python objects.

        GitNexus 1.6.7 returns ``<JSON object> + trailing human hint`` in one
        text blob (strict ``json.loads`` fails with "Extra data"). Delegate to
        ``_parse_text`` which uses ``raw_decode`` to parse the leading JSON and
        tolerate the trailing hint, decode cypher markdown tables, and return
        ``None`` on non-JSON / ambiguous payloads so downstream ``isinstance``
        guards treat them as empty instead of silently iterating a string.
        """
        if not result:
            return None
        content = result.get("content", [])
        if not content:
            return result
        for item in content:
            if item.get("type") == "text":
                return self._parse_text(item.get("text", ""))
        return result

    @staticmethod
    def _parse_text(text: str) -> list | dict | str | None:
        """Parse one GitNexus tool text blob.

        Returns the leading JSON object (dict/list), with cypher markdown
        tables decoded into ``obj["rows"]``. Returns ``None`` on non-JSON text
        (e.g. ``"Error: Multiple repositories indexed..."``) or
        ``status:"ambiguous"`` so consumers see an empty result, not a string.
        """
        stripped = text.lstrip()
        try:
            obj, _end = json.JSONDecoder().raw_decode(stripped)
        except json.JSONDecodeError:
            logger.warning("GitNexus tool returned non-JSON text: %.120s", stripped)
            return None
        if isinstance(obj, dict):
            if obj.get("status") == "ambiguous":
                logger.warning(
                    "GitNexus tool returned ambiguous result: %.120s",
                    str(obj.get("message", "")),
                )
                return None
            if "markdown" in obj and "row_count" in obj:
                obj["rows"] = _parse_md_table(obj["markdown"])
        return obj
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/core/tests/code_index/test_gitnexus_mcp.py::TestParseToolResultRobustness packages/core/tests/code_index/test_gitnexus_mcp.py::TestParseMdTable -v`
Expected: PASS（全绿，且原有 `test_call_tool_sends_request` 不受影响——它喂的 `[{"name":"ep1"}]` 是合法 JSON 无 trailing，raw_decode 也成功）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/gitnexus_mcp.py packages/core/tests/code_index/test_gitnexus_mcp.py
git commit -m "fix(gitnexus): _parse_tool_result tolerate JSON+trailing, decode cypher markdown, fail loud on error/ambiguous"
```

---

### Task 3: `call_tool` 自动注入 `repo` 参数

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/gitnexus_mcp.py:85-99`（`call_tool` 方法）
- Test: `packages/core/tests/code_index/test_gitnexus_mcp.py`

**Interfaces:**
- Produces: `call_tool(tool_name, arguments)` 在发请求前注入 `arguments["repo"] = str(self.repo_root)`（调用方显式传时不覆盖）

- [ ] **Step 1: Write the failing test**

追加到 `test_gitnexus_mcp.py`：

```python
class TestCallToolInjectsRepo:
    @pytest.mark.asyncio
    async def test_injects_repo_path(self, tmp_path):
        """多 repo 索引时 GitNexus 要求 repo 参数；call_tool 必须自动注入 path 形式。"""
        client = GitNexusMCPClient(tmp_path)
        captured: dict = {}

        async def fake_send(method: str, params: dict):
            captured.update(params)
            return {"content": [{"type": "text", "text": "{}"}]}

        client._send_request = fake_send  # bypass subprocess
        await client.call_tool("query", {"query": "entry point"})
        assert captured["arguments"]["repo"] == str(tmp_path)

    @pytest.mark.asyncio
    async def test_does_not_override_explicit_repo(self, tmp_path):
        client = GitNexusMCPClient(tmp_path)
        captured: dict = {}

        async def fake_send(method: str, params: dict):
            captured.update(params)
            return {"content": [{"type": "text", "text": "{}"}]}

        client._send_request = fake_send
        await client.call_tool("query", {"query": "x", "repo": "explicit-name"})
        assert captured["arguments"]["repo"] == "explicit-name"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/core/tests/code_index/test_gitnexus_mcp.py::TestCallToolInjectsRepo -v`
Expected: FAIL（`KeyError: 'repo'`——当前 call_tool 不注入 repo）

- [ ] **Step 3: Write minimal implementation**

修改 `gitnexus_mcp.py:85-99` 的 `call_tool`，在 `result = await self._send_request(...)` 之前加一行 `setdefault`：

```python
    async def call_tool(self, tool_name: str, arguments: dict) -> list | dict | str | None:
        """Call an MCP tool and return the parsed result.

        Args:
            tool_name: One of "cypher", "impact", "query", etc.
            arguments: Tool-specific arguments.

        Returns:
            Parsed tool result (usually a dict; None on parse failure).
        """
        # Inject repo (path form; GitNexus schema accepts "name or path").
        # Required when multiple repos are indexed in the global registry
        # (~/.gitnexus/registry.json) — otherwise GitNexus returns
        # 'Error: Multiple repositories indexed...'. Harmless when only one.
        arguments.setdefault("repo", str(self.repo_root))
        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        return self._parse_tool_result(result)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/core/tests/code_index/test_gitnexus_mcp.py -v`
Expected: PASS（含 TestCallToolInjectsRepo + 此前所有测试）

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/code_index/gitnexus_mcp.py packages/core/tests/code_index/test_gitnexus_mcp.py
git commit -m "fix(gitnexus): call_tool auto-inject repo param (multi-repo registry support)"
```

---

### Task 4: `build_call_graph_from_gitnexus` cypher 消费改读 `rows`（核心回归锚点）

**Files:**
- Modify: `packages/core/src/shannon_core/code_index/gitnexus_call_graph.py:233-263`（cypher 消费段）
- Test: `packages/core/tests/code_index/test_gitnexus_call_graph.py`（新增回归锚点 + 更新 2 个现有测试的 cypher fixture）

**Interfaces:**
- Consumes: `call_tool`（Task 3）返回的 dict，cypher 形如 `{"markdown":..., "row_count":..., "rows":[{caller_file, caller_name, caller_line, callee_file, callee_name}]}`
- Produces: `build_call_graph_from_gitnexus` 返回非空 `edges`/`chains`（生产里一直为 0，本 task 后必须非空）

- [ ] **Step 1: Write the failing test（新回归锚点）**

追加到 `test_gitnexus_call_graph.py`：

```python
    @pytest.mark.asyncio
    async def test_cypher_rows_produce_edges_and_chains(self):
        """核心回归锚点：GitNexus 1.6.7 cypher 返回 {markdown,row_count}（_parse_tool_result
        填 rows）。build_call_graph 必须从 rows 构建非空 edges/chains——生产里一直为 0。"""
        blocks = [
            _block("handler", "app.py", 1),
            _block("get_users", "svc.py", 10),
        ]
        mcp = FakeMCPClient(responses={
            "query": {
                "process_symbols": [],
                "definitions": [{"name": "handler", "filePath": "app.py", "startLine": 1}],
            },
            "cypher": {
                "markdown": "| caller_file | caller_name | callee_file | callee_name |\n| --- | --- | --- | --- |\n| app.py | handler | svc.py | get_users |",
                "row_count": 1,
                "rows": [{
                    "caller_file": "app.py", "caller_name": "handler", "caller_line": 1,
                    "callee_file": "svc.py", "callee_name": "get_users",
                }],
            },
        })
        result = await build_call_graph_from_gitnexus(
            repo_path="/tmp/repo", mcp_client=mcp, blocks=blocks,
        )
        assert len(result.edges) == 1
        assert result.edges[0].callee_name == "get_users"
        assert len(result.entry_points) == 1
        assert len(result.chains) >= 1

    @pytest.mark.asyncio
    async def test_cypher_none_or_no_rows_yields_no_edges(self):
        """_parse_tool_result 失败返 None / cypher 无 rows 时，edges 必须为空且不崩。"""
        blocks = [_block("handler", "app.py", 1)]
        for bad in (None, "Error: multiple repos", {"markdown": "| x |"}):
            mcp = FakeMCPClient(responses={
                "query": {"definitions": []}, "cypher": bad,
            })
            result = await build_call_graph_from_gitnexus("/tmp/repo", mcp, blocks)
            assert result.edges == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/core/tests/code_index/test_gitnexus_call_graph.py::TestBuildCallGraphFromGitNexus::test_cypher_rows_produce_edges_and_chains -v`
Expected: FAIL（当前实现 `if isinstance(cypher_result, list):` —— cypher 是 dict 不是 list，永不进分支 → `edges==[]`，断言 `len(result.edges)==1` 失败）

- [ ] **Step 3: Write minimal implementation**

替换 `gitnexus_call_graph.py:233-263`（从 `edges: list[CallEdge] = []` 到 `except Exception as exc:` 之前的整个 cypher 消费块）为：

```python
    edges: list[CallEdge] = []
    try:
        cypher_result = await mcp_client.call_tool(
            "cypher",
            {"query": "MATCH (caller)-[r:CodeRelation {type: 'CALLS'}]->(callee) RETURN caller.filePath AS caller_file, caller.name AS caller_name, caller.startLine AS caller_line, callee.filePath AS callee_file, callee.name AS callee_name, r.confidence AS confidence LIMIT 5000"},
        )
        # GitNexus 1.6.7 cypher 返回 {markdown, row_count}（_parse_tool_result
        # 已把 markdown 表格解析成 rows）。失败/ambiguous 时 cypher_result 为 None。
        cypher_rows = cypher_result.get("rows", []) if isinstance(cypher_result, dict) else []
        for record in cypher_rows:
            if not isinstance(record, dict):
                continue
            caller_name = record.get("caller_name")
            callee_name = record.get("callee_name")
            if not caller_name or not callee_name:
                continue
            caller_file = record.get("caller_file", "")
            caller_line = record.get("caller_line", 0) or 0
            callee_file = record.get("callee_file")
            if isinstance(caller_line, str):
                try:
                    caller_line = int(caller_line)
                except (ValueError, TypeError):
                    caller_line = 0
            caller_id = f"{caller_file}:{caller_name}:{caller_line}" if caller_file else caller_name
            resolved = callee_file is not None
            edges.append(CallEdge(
                caller_id=caller_id,
                callee_name=callee_name,
                callee_file=callee_file,
                resolved=resolved,
                line=caller_line,
            ))
    except Exception as exc:
        logger.warning("Cypher query for call edges failed (%s); edge list will be empty", exc)
```

- [ ] **Step 4: Run new test + check existing tests break, then update fixtures**

Run: `pytest packages/core/tests/code_index/test_gitnexus_call_graph.py -v`
Expected: 新测试 PASS；但 `test_builds_call_graph_from_mcp` 和 `test_builds_chains_from_edges` **FAIL**（它们喂的 cypher 是 `list` 老格式，新实现读 `rows` 后 list 无 `.get` → edges 空 → 断言失败）。

更新这两个现有测试的 cypher fixture，从 list 改成 dict-with-rows（反映 GitNexus 1.6.7 真实格式）。在 `test_gitnexus_call_graph.py` 中：

`test_builds_call_graph_from_mcp`（约 :81-96）的 `"cypher"` 响应改为：
```python
            "cypher": {
                "markdown": "| caller_file | caller_name | callee_file | callee_name |\n| --- | --- | --- | --- |\n| app.py | handler | svc.py | get_users |",
                "row_count": 1,
                "rows": [{"caller_file": "app.py", "caller_name": "handler", "caller_line": 5, "callee_file": "svc.py", "callee_name": "get_users"}],
            },
```

`test_builds_chains_from_edges`（约 :133-145）的 `"cypher"` 响应改为：
```python
            "cypher": {
                "markdown": "| caller_file | caller_name | callee_file | callee_name |\n| --- | --- | --- | --- |\n| app.py | handler | svc.py | get_users |\n| svc.py | get_users | db.py | execute |",
                "row_count": 2,
                "rows": [
                    {"caller_file": "app.py", "caller_name": "handler", "caller_line": 5, "callee_file": "svc.py", "callee_name": "get_users"},
                    {"caller_file": "svc.py", "caller_name": "get_users", "caller_line": 15, "callee_file": "db.py", "callee_name": "execute"},
                ],
            },
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest packages/core/tests/code_index/test_gitnexus_call_graph.py -v`
Expected: PASS（全部，含新回归锚点 + 更新后的 2 个现有测试）

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/shannon_core/code_index/gitnexus_call_graph.py packages/core/tests/code_index/test_gitnexus_call_graph.py
git commit -m "fix(gitnexus): build_call_graph consume cypher rows (markdown table) instead of raw list"
```

---

### Task 5: `run_code_index` 可观测性 log（chains 统计）

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/activities.py:515-519`（`write_index_files` 后、`return` 前插入 log）
- Test: `packages/whitebox/tests/test_run_code_index.py`

**Interfaces:**
- Consumes: `index.total_chains/total_entry_points/total_blocks/degradation_level`（CodeIndex 字段，已在 :520-522 return 中使用）+ `get_audit_session().log_info(msg, level)`
- Produces: `workflow.log` 出现一条 `GitNexus code-index：blocks=… chains=…` 的 info/warning，chains=0 时为 warning 并点明"调用图空壳"

- [ ] **Step 1: Write the failing test**

追加到 `test_run_code_index.py`：

```python
@pytest.mark.asyncio
async def test_run_code_index_logs_chains_warning_when_empty(tmp_path):
    """chains=0 时 log_info 发 warning（调用图空壳 → GitNexus 轨无结果的核心信号）。
    对齐 06-29 authz/injection-gitnexus-track-observability 的 InfoEvent 模式。"""
    input = ActivityInput(repo_path=str(tmp_path), workspace_name="test")
    fake_index = MagicMock(
        total_blocks=10, total_entry_points=0, total_chains=0, degradation_level="full",
    )

    with patch("shannon_whitebox.audit.session_registry.get_audit_session") as mock_sess, \
         patch("shannon_core.code_index.gitnexus_engine.GitNexusEngine") as mock_engine_cls, \
         patch("shannon_whitebox.pipeline.activities._get_paths") as mock_paths, \
         patch("shannon_core.code_index.gitnexus_mcp.GitNexusMCPClient"), \
         patch("shannon_core.code_index.build_code_index_with_gitnexus",
               new=AsyncMock(return_value=(fake_index, []))), \
         patch("shannon_core.code_index.write_index_files",
               return_value=(tmp_path / "code_index.json", tmp_path / "code_index_summary.md")):
        cm = mock_sess.return_value.track_step.return_value
        cm.__aenter__ = AsyncMock(return_value=None)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_engine = MagicMock()
        mock_engine.is_available.return_value = True
        mock_engine.ensure_indexed.return_value = MagicMock(success=True)
        mock_engine_cls.return_value = mock_engine
        mock_paths.return_value = (tmp_path, tmp_path / "deliverables", tmp_path)

        await run_code_index(input)

        mock_sess.return_value.log_info.assert_awaited()
        args = mock_sess.return_value.log_info.await_args
        assert args.args[1] == "warning"
        assert "chains=0" in args.args[0]


@pytest.mark.asyncio
async def test_run_code_index_logs_info_when_chains_present(tmp_path):
    """chains>0 时 log_info 发 info（调用图正常）。"""
    input = ActivityInput(repo_path=str(tmp_path), workspace_name="test")
    fake_index = MagicMock(
        total_blocks=10, total_entry_points=3, total_chains=5, degradation_level="none",
    )

    with patch("shannon_whitebox.audit.session_registry.get_audit_session") as mock_sess, \
         patch("shannon_core.code_index.gitnexus_engine.GitNexusEngine") as mock_engine_cls, \
         patch("shannon_whitebox.pipeline.activities._get_paths") as mock_paths, \
         patch("shannon_core.code_index.gitnexus_mcp.GitNexusMCPClient"), \
         patch("shannon_core.code_index.build_code_index_with_gitnexus",
               new=AsyncMock(return_value=(fake_index, []))), \
         patch("shannon_core.code_index.write_index_files",
               return_value=(tmp_path / "code_index.json", tmp_path / "code_index_summary.md")):
        cm = mock_sess.return_value.track_step.return_value
        cm.__aenter__ = AsyncMock(return_value=None)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_engine = MagicMock()
        mock_engine.is_available.return_value = True
        mock_engine.ensure_indexed.return_value = MagicMock(success=True)
        mock_engine_cls.return_value = mock_engine
        mock_paths.return_value = (tmp_path, tmp_path / "deliverables", tmp_path)

        await run_code_index(input)

        args = mock_sess.return_value.log_info.await_args
        assert args.args[1] == "info"
        assert "chains=5" in args.args[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/whitebox/tests/test_run_code_index.py -v`
Expected: FAIL（`log_info.assert_awaited` 失败——run_code_index 当前不发 log_info）

- [ ] **Step 3: Write minimal implementation**

在 `activities.py:517`（`write_index_files(...)` 调用之后、`return {` 之前）插入：

```python
            # 可观测性：调用图统计。chains=0 是 GitNexus 轨空壳的核心信号
            #（→ taint_flows=0 → 3 类 builder 全空 → GitNexus 轨无结果）。
            # 对齐 06-29 authz/injection-gitnexus-track-observability 的 InfoEvent 风格。
            try:
                empty_call_graph = index.total_chains == 0
                await get_audit_session().log_info(
                    f"GitNexus code-index：blocks={index.total_blocks}, "
                    f"entry_points={index.total_entry_points}, chains={index.total_chains}, "
                    f"degradation={index.degradation_level}"
                    + (" → ⚠️ 调用图空壳（chains=0 → taint_flows=0 → GitNexus 轨将无结果）"
                       if empty_call_graph else ""),
                    "warning" if empty_call_graph else "info",
                )
            except Exception:
                pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest packages/whitebox/tests/test_run_code_index.py -v`
Expected: PASS（含原有 `test_run_code_index_raises_when_gitnexus_unavailable` + 2 个新测试）

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/activities.py packages/whitebox/tests/test_run_code_index.py
git commit -m "feat(gitnexus): run_code_index logs chains/entry_points count (empty-call-graph warning)"
```

---

## Self-Review（plan 作者自查记录）

- **Spec 覆盖**：spec 4.1（call_tool 注入 repo）→ Task 3；4.2（raw_decode + 失败返 None）→ Task 2；4.3（cypher markdown 解析）→ Task 1+2（_parse_md_table + _parse_text 调用）+ Task 4（消费 rows）；4.4（cypher 消费改 rows）→ Task 4；4.5（run_code_index log）→ Task 5；§6 测试策略 → 各 Task 测试。全覆盖。
- **占位符**：无 TBD/TODO；每步含完整代码。
- **类型一致**：`_parse_md_table` 在 Task 1 定义、Task 2 `_parse_text` 调用、签名一致；`rows` 字段在 Task 2（_parse_text 填）与 Task 4（cypher 消费）一致；`call_tool` 注入 repo 在 Task 3，Task 4 FakeMCPClient 不验 repo（FakeMCPClient 绕过 call_tool 直接返 canned，repo 注入由 Task 3 单测覆盖）。
- **依赖顺序**：Task 1→2（2 用 _parse_md_table）→3（独立）→4（4 用 2/3 语义但 FakeMCP 喂解析后形状）→5（独立）。可顺序执行。
