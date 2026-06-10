"""Tests for sink report merger (deterministic + LLM)."""
import pytest
from shannon_core.code_index.sink_merger import merge_sink_reports, parse_llm_sinks
from shannon_core.code_index.parameter_models import (
    SinkCallSite, SinkCategory, DangerousSlot, SlotContext,
)


def _make_sink(file_path: str, line: int, rule_id: str = "py-os-system",
               category: SinkCategory = SinkCategory.COMMAND) -> SinkCallSite:
    return SinkCallSite(
        id=f"{file_path}:func:system:{line}:0",
        caller_id=f"{file_path}:func",
        callee_name="system",
        callee_receiver="os",
        category=category,
        sink_subtype="command_shell",
        file_path=file_path,
        line=line,
        column=0,
        dangerous_slots=[],
        rule_id=rule_id,
    )


LLM_REPORT_WITH_SINKS = """## Sink Findings

### SQL Injection
- `src/db.py:42`: cursor.execute(user_input)
- `src/db.py:87`: cursor.execute(filter_query)

### XSS
- `templates/index.ejs:15`: <%- userData %> (unescaped EJS output)
- `src/render.ts:33`: element.innerHTML = response.data

### Template Coverage Audit
| Template File | Sink Count | Escaping Modes | Analysis Status |
|---|---|---|---|
| `templates/index.ejs` | 1 | 1 unescaped | Analyzed |
| `templates/layout.ejs` | 0 | - | Analyzed |
"""

LLM_REPORT_NO_SINKS = """## Sink Findings

No dangerous sinks found in the analyzed codebase.
"""


class TestParseLlmSinks:
    def test_extracts_file_line_pairs(self):
        sinks = parse_llm_sinks(LLM_REPORT_WITH_SINKS)
        # Should find at least 4 file:line pairs
        assert len(sinks) >= 4
        file_lines = {(s.file_path, s.line) for s in sinks}
        assert ("src/db.py", 42) in file_lines
        assert ("src/db.py", 87) in file_lines
        assert ("templates/index.ejs", 15) in file_lines
        assert ("src/render.ts", 33) in file_lines

    def test_empty_report_returns_empty(self):
        sinks = parse_llm_sinks(LLM_REPORT_NO_SINKS)
        assert sinks == []

    def test_category_inference_sql(self):
        sinks = parse_llm_sinks(LLM_REPORT_WITH_SINKS)
        sql_sinks = [s for s in sinks if s.file_path == "src/db.py" and s.line == 42]
        assert len(sql_sinks) == 1
        assert sql_sinks[0].category == SinkCategory.SQL

    def test_category_inference_xss(self):
        sinks = parse_llm_sinks(LLM_REPORT_WITH_SINKS)
        xss_sinks = [s for s in sinks if s.file_path == "src/render.ts"]
        assert len(xss_sinks) == 1
        assert xss_sinks[0].category == SinkCategory.XSS

    def test_category_inference_template(self):
        sinks = parse_llm_sinks(LLM_REPORT_WITH_SINKS)
        tmpl_sinks = [s for s in sinks if s.file_path == "templates/index.ejs"]
        assert len(tmpl_sinks) == 1
        # Template file sink should be TEMPLATE or XSS
        assert tmpl_sinks[0].category in (SinkCategory.TEMPLATE, SinkCategory.XSS)


class TestMergeSinkReports:
    def test_deduplicates_overlapping_sinks(self):
        deterministic = [
            _make_sink("src/db.py", 42, category=SinkCategory.SQL),
        ]
        llm_report = "- `src/db.py:42`: cursor.execute(user_input)\n"
        merged = merge_sink_reports(deterministic, llm_report)
        # Should have exactly 1 sink at that location (deterministic wins)
        db_42 = [s for s in merged if s.file_path == "src/db.py" and s.line == 42]
        assert len(db_42) == 1
        assert db_42[0].rule_id == "py-os-system"  # deterministic record kept

    def test_adds_llm_only_sinks(self):
        deterministic = []
        llm_report = "- `templates/index.ejs:15`: <%- userData %> (unescaped)\n"
        merged = merge_sink_reports(deterministic, llm_report)
        assert len(merged) == 1
        assert merged[0].file_path == "templates/index.ejs"
        assert merged[0].line == 15
        assert merged[0].rule_id == "llm-sink-hunter"
        assert merged[0].needs_review is True

    def test_preserves_all_deterministic(self):
        deterministic = [
            _make_sink("src/a.py", 10),
            _make_sink("src/b.py", 20),
        ]
        merged = merge_sink_reports(deterministic, "")
        assert len(merged) == 2

    def test_merges_mixed(self):
        deterministic = [
            _make_sink("src/a.py", 10),
            _make_sink("src/b.py", 20),
        ]
        llm_report = "- `src/b.py:20`: system(cmd)\n- `src/c.py:30`: eval(x)\n"
        merged = merge_sink_reports(deterministic, llm_report)
        # a:10 deterministic, b:20 deduped (deterministic wins), c:30 LLM-only
        assert len(merged) == 3
        file_lines = {(s.file_path, s.line) for s in merged}
        assert ("src/a.py", 10) in file_lines
        assert ("src/b.py", 20) in file_lines
        assert ("src/c.py", 30) in file_lines
        # c:30 should be LLM-sourced
        c_30 = [s for s in merged if s.file_path == "src/c.py"][0]
        assert c_30.rule_id == "llm-sink-hunter"

    def test_llm_sink_id_format(self):
        llm_report = "- `src/x.py:99`: pickle.loads(data)\n"
        merged = merge_sink_reports([], llm_report)
        assert len(merged) == 1
        assert merged[0].id == "llm:src/x.py:99"

    def test_llm_sink_needs_review(self):
        llm_report = "- `src/x.py:99`: pickle.loads(data)\n"
        merged = merge_sink_reports([], llm_report)
        assert merged[0].needs_review is True

    def test_empty_both(self):
        merged = merge_sink_reports([], "")
        assert merged == []
