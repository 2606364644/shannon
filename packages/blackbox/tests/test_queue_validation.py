"""Tests for multi-level queue validation."""
import json
import pytest

from shannon_blackbox.services.exploitation_checker import ExploitationChecker, QueueValidationResult


class TestValidateQueue:
    @pytest.mark.asyncio
    async def test_valid_queue_with_deliverable(self, tmp_path):
        queue_data = {"vulnerabilities": [
            {"ID": "INJ-001", "vulnerability_type": "SQL Injection",
             "externally_exploitable": True, "confidence": "high"},
        ]}
        (tmp_path / "injection_exploitation_queue.json").write_text(json.dumps(queue_data))
        (tmp_path / "injection_analysis_deliverable.md").write_text("# Analysis")
        result = await ExploitationChecker.validate_queue("injection", tmp_path)
        assert result.valid is True
        assert result.vuln_count == 1
        assert result.reason == ""

    @pytest.mark.asyncio
    async def test_queue_file_missing(self, tmp_path):
        result = await ExploitationChecker.validate_queue("injection", tmp_path)
        assert result.valid is False
        assert result.reason == "queue_file_missing"
        assert result.retryable is False

    @pytest.mark.asyncio
    async def test_queue_invalid_json(self, tmp_path):
        (tmp_path / "xss_exploitation_queue.json").write_text("not json {{{")
        result = await ExploitationChecker.validate_queue("xss", tmp_path)
        assert result.valid is False
        assert result.reason == "json_parse_error"
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_queue_missing_vulnerabilities_key(self, tmp_path):
        (tmp_path / "auth_exploitation_queue.json").write_text(json.dumps({"data": "x"}))
        result = await ExploitationChecker.validate_queue("auth", tmp_path)
        assert result.valid is False
        assert result.reason == "invalid_vulnerabilities_array"

    @pytest.mark.asyncio
    async def test_queue_vulnerabilities_not_list(self, tmp_path):
        (tmp_path / "ssrf_exploitation_queue.json").write_text(
            json.dumps({"vulnerabilities": "not a list"})
        )
        result = await ExploitationChecker.validate_queue("ssrf", tmp_path)
        assert result.valid is False
        assert result.reason == "invalid_vulnerabilities_array"

    @pytest.mark.asyncio
    async def test_queue_missing_deliverable(self, tmp_path):
        queue_data = {"vulnerabilities": [
            {"ID": "INJ-001", "vulnerability_type": "SQL Injection",
             "externally_exploitable": True, "confidence": "high"},
        ]}
        (tmp_path / "injection_exploitation_queue.json").write_text(json.dumps(queue_data))
        # No deliverable file created
        result = await ExploitationChecker.validate_queue("injection", tmp_path)
        assert result.valid is False
        assert result.reason == "deliverable_missing"
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_queue_empty_vulnerabilities(self, tmp_path):
        queue_data = {"vulnerabilities": []}
        (tmp_path / "authz_exploitation_queue.json").write_text(json.dumps(queue_data))
        (tmp_path / "authz_analysis_deliverable.md").write_text("# Analysis")
        result = await ExploitationChecker.validate_queue("authz", tmp_path)
        assert result.valid is False
        assert result.reason == "empty_vulnerabilities"

    @pytest.mark.asyncio
    async def test_should_exploit_returns_bool(self, tmp_path):
        """Backward compatibility: should_exploit still returns bool."""
        queue_data = {"vulnerabilities": [
            {"ID": "INJ-001", "vulnerability_type": "SQL Injection",
             "externally_exploitable": True, "confidence": "high"},
        ]}
        (tmp_path / "injection_exploitation_queue.json").write_text(json.dumps(queue_data))
        (tmp_path / "injection_analysis_deliverable.md").write_text("# Analysis")
        result = await ExploitationChecker.should_exploit(
            deliverables_path=tmp_path, vuln_type="injection"
        )
        assert isinstance(result, bool)
        assert result is True

    @pytest.mark.asyncio
    async def test_should_exploit_disabled(self, tmp_path):
        result = await ExploitationChecker.should_exploit(
            deliverables_path=tmp_path, vuln_type="injection", exploit_enabled=False
        )
        assert result is False


def test_validate_exploitation_queue_roundtrips_as_dataclass():
    """Regression（真机崩溃，见 commit 66b8744 follow-up）：activity 必须声明
    ``-> QueueValidationResult`` 返回注解。

    根因：temporalio 默认 converter 序列化 dataclass→``json/plain``，反序列化时只有拿到
    ret_type 作 type_hint 才能还原 dataclass（``_workflow_instance.py``:
    ``ret_types = [ret_type] if ret_type else None``）。activity 无返回注解 → ret_type=None →
    workflow 侧 ``validation`` 拿到 dict → ``workflows.py:270`` ``validation.valid`` 抛
    ``AttributeError: 'dict' object has no attribute 'valid'``。

    本测试此前漏掉：进程内直调 ``validate_queue`` 不经 Temporal converter 往返，拿到的还是
    dataclass 对象，故 ``.valid`` 能用、单测绿、真机崩。这里强制走 converter 往返复现真实路径。
    """
    import typing

    from temporalio.converter import DataConverter

    from shannon_blackbox.pipeline.activities import validate_exploitation_queue

    # 1) temporalio 从函数注解提取 ret_type；无注解则 workflow 侧反序列化落回 dict。
    hints = typing.get_type_hints(validate_exploitation_queue)
    assert hints.get("return") is QueueValidationResult, (
        "validate_exploitation_queue 缺返回类型注解 -> QueueValidationResult —— temporalio "
        "反序列化会落回 dict，workflow 侧 validation.valid 抛 AttributeError"
    )

    # 2) 经默认 converter 往返（模拟 workflow execute_activity 返回路径），仍是 dataclass，
    #    且 workflow 消费的 .valid/.is_expected/.message/.context 全部可用。
    pc = DataConverter.default.payload_converter
    obj = QueueValidationResult(valid=True, message="ok", context={"queue_path": "x"})
    rt = pc.from_payloads([pc.to_payloads([obj])[0]], [hints["return"]])[0]
    assert isinstance(rt, QueueValidationResult)
    assert rt.valid is True
    assert rt.is_expected is True
    assert rt.message == "ok"
