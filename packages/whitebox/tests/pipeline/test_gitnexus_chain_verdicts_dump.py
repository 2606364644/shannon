"""P1: chain_verdicts 落盘，safe 链也进产物（按 controller 裁决修正：吃 findings 不吃 pairs）。

Controller 裁决背景：brief 假设活动层能收集 (candidate, verdict) pairs，但真实
数据流是 builder 内部 extract→judge→转 finding，活动层只拿 findings（拿不到裸
pairs）。本测试按修正后的 _dump_chain_verdicts(deliverables, vc, findings) 签名
喂假 finding 对象，断言 safe 链也进产物 + 零 finding 不落盘。产物 shape 为
``{"verdicts": [...]}``（对齐 Task 7 组装器读 ``verdicts.get("verdicts", [])``）。
"""
import json
from pathlib import Path

import pytest


@pytest.fixture
def deliverables(tmp_path: Path) -> Path:
    (tmp_path / "intermediate").mkdir()
    return tmp_path


class _FakeFinding:
    """Minimal stand-in for taint finding fields the dump reads."""

    def __init__(self, flow_id, sink_call, verdict, reason="", confidence="high"):
        self.flow_id = flow_id
        self.sink_call = sink_call
        self.verdict = verdict
        self.mismatch_reason = reason
        self.confidence = confidence


def test_chain_verdicts_dump_includes_safe_chains(deliverables: Path):
    """safe 链也落盘，不再用完即丢。"""
    from supernova_whitebox.pipeline.activities import _dump_chain_verdicts

    findings = [
        _FakeFinding("u1->s1", "s1", "vulnerable", "none"),
        _FakeFinding("u2->s1", "s1", "safe", "shlex.quote 覆盖"),
    ]
    _dump_chain_verdicts(deliverables, "injection", findings)

    path = deliverables / "intermediate" / "injection_chain_verdicts.json"
    assert path.exists()
    rows = json.loads(path.read_text(encoding="utf-8"))["verdicts"]
    assert len(rows) == 2
    assert {r["verdict"] for r in rows} == {"vulnerable", "safe"}
    assert rows[1]["reason"] == "shlex.quote 覆盖"


def test_chain_verdicts_empty_when_no_findings(deliverables: Path):
    """零 finding 不落盘（不产空文件）。"""
    from supernova_whitebox.pipeline.activities import _dump_chain_verdicts
    _dump_chain_verdicts(deliverables, "ssrf", [])
    assert not (deliverables / "intermediate" / "ssrf_chain_verdicts.json").exists()


def test_chain_verdicts_dump_ssrf_reason_falls_back_to_missing_defense(deliverables: Path):
    """F1+F2 守卫：ssrf finding 无 mismatch_reason → reason 兜底取 missing_defense；
    sanitizer_annotations 真传（不再硬编码 []）。

    SsrfVulnerability 字段名与 injection/xss 不同：sink 用
    vulnerable_code_location、reason 字段名用 missing_defense（builder 把
    verdict.mismatch_reason 写进 missing_defense）。dump 侧兜底取两者。
    """
    from supernova_whitebox.pipeline.activities import _dump_chain_verdicts

    class _FakeSsrfFinding:
        flow_id = "u3->s3"
        sink_call = None  # ssrf 无 sink_call
        vulnerable_code_location = "s3"
        verdict = "safe"
        mismatch_reason = None  # ssrf 无此字段
        missing_defense = "url-allowlist 覆盖"
        confidence = "high"
        sanitizer_annotations = ["url-allowlist"]

    _dump_chain_verdicts(deliverables, "ssrf", [_FakeSsrfFinding()])
    rows = json.loads(
        (deliverables / "intermediate" / "ssrf_chain_verdicts.json").read_text(
            encoding="utf-8"))["verdicts"]
    assert rows[0]["reason"] == "url-allowlist 覆盖"  # F1 兜底取 missing_defense
    assert rows[0]["sanitizer_annotations"] == ["url-allowlist"]  # F2 真传
    assert rows[0]["flow_id"] == "u3->s3"
    assert rows[0]["verdict"] == "safe"
    # sink_call=None → 兜底取 vulnerable_code_location
    assert rows[0]["sink_call_site_id"] == "s3"


def test_chain_verdicts_dump_sanitizer_annotation_objects(deliverables: Path):
    """Fix round 2：sanitizer_annotations 元素是 SanitizerAnnotation dataclass
    实例（非 dict）时落盘不崩且转 dict。

    真实数据流里 CandidateChain.sanitizer_annotations 元素是 frozen dataclass
    （sanitizer_library.py SanitizerAnnotation），finding 原始属性原样持有这些
    实例——原样塞 json.dumps 抛 TypeError。恰好 safe 链才带 sanitizer → 整个
    chain_verdicts.json 不产 → Task 7 组装器 GN 枝（含 safe-only 树）静默降级。
    本用例用真实 SanitizerAnnotation 实例（真实 bug 触发路径）+ dict 原样保留
    + pydantic 风格 model_dump 对象（防御分支）三形态验证。
    """
    from supernova_whitebox.pipeline.activities import _dump_chain_verdicts
    from supernova_core.code_index.sanitizer_library import SanitizerAnnotation

    class _FakePydanticAnn:
        def model_dump(self):
            return {"rule_id": "legacy-pydantic", "defense_type": "x"}

    class _FakeFindingObj:
        flow_id = "u4->s4"; sink_call = "s4"; verdict = "safe"
        mismatch_reason = None; missing_defense = None; confidence = "high"
        sanitizer_annotations = [
            SanitizerAnnotation(  # 真实 dataclass 实例（真实 bug 触发路径）
                rule_id="py-shlex-quote", defense_type="shlex_quote",
                applies_to="cmd_argument", code_location="a.py:10",
                matched_text="shlex.quote",
            ),
            {"rule_id": "legacy-dict", "defense_type": "x"},  # dict 原样保留
            _FakePydanticAnn(),  # pydantic 风格对象走 model_dump 分支
        ]

    _dump_chain_verdicts(deliverables, "injection", [_FakeFindingObj()])
    rows = json.loads(
        (deliverables / "intermediate" / "injection_chain_verdicts.json").read_text(
            encoding="utf-8"))["verdicts"]
    assert rows[0]["sanitizer_annotations"] == [
        {"rule_id": "py-shlex-quote", "defense_type": "shlex_quote",
         "applies_to": "cmd_argument", "code_location": "a.py:10",
         "matched_text": "shlex.quote"},
        {"rule_id": "legacy-dict", "defense_type": "x"},
        {"rule_id": "legacy-pydantic", "defense_type": "x"},
    ]
