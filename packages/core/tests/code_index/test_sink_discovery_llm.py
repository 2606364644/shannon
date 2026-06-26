"""sink_discovery_llm 单测 — 半 sink 收集 + LLM 补召回(spec 方案 A)."""
import pytest

from shannon_core.code_index.models import FuncBlock
from shannon_core.code_index.sink_discovery_llm import (
    SuspiciousCall,
    collect_suspicious_calls,
    discover_sinks_llm,
    RuleGap,
)


class _FakeCall:
    def __init__(self, line, column=0):
        self.line = line
        self.column = column


class _FakeParser:
    """记录预设的 call → (callee, receiver, arg_exprs), 命中 sink_detector 规则的用真规则名."""
    def __init__(self, calls):
        self._calls = calls  # [(callee, receiver, arg_exprs, line), ...]

    def iter_calls(self, block, source):
        return [_FakeCall(line) for *_, line in self._calls]

    def destructure_call(self, call):
        callee, receiver, _, line = self._calls[call.line - 1]
        return callee, receiver

    def extract_arg_expressions(self, call, source):
        _, _, arg_exprs, _ = self._calls[call.line - 1]
        return arg_exprs


def _block(name="handler", file="app.py", language="python", source="def handler(): pass"):
    return FuncBlock(
        id=f"{file}:{name}:1", file_path=file, function_name=name,
        start_line=1, end_line=10, source_code=source,
        parameters=["uid"], language=language,
    )


def test_collects_sinkish_unmatched_call():
    # raw_query 是 sink-ish(query) 但规则库无 raw_query@custom_db → 收集
    block = _block()
    parser = _FakeParser([("raw_query", "custom_db", ["\"SELECT \" + uid"], 1)])
    out = collect_suspicious_calls([block], parser, source_provider=lambda b: b"src")
    assert len(out) == 1
    assert out[0].callee == "raw_query"
    assert out[0].receiver == "custom_db"


def test_skips_rule_hit_call():
    # cursor.execute 命中 py-db-cursor-execute 规则 → 不收集
    block = _block()
    parser = _FakeParser([("execute", "cursor", ["uid"], 1)])
    out = collect_suspicious_calls([block], parser, source_provider=lambda b: b"src")
    assert out == []


def test_skips_non_sinkish_call():
    block = _block()
    parser = _FakeParser([("helper", None, ["uid"], 1)])
    out = collect_suspicious_calls([block], parser, source_provider=lambda b: b"src")
    assert out == []
