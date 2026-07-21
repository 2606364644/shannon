"""core CLI 错误渲染层单测：根因提取 / 映射 / 落盘。"""
from pathlib import Path

from temporalio.exceptions import ApplicationError

from supernova_core.cli.error_render import extract_root_cause


class _FakeTemporalError(Exception):
    """模拟 temporalio 异常的 .cause / .type 属性链（不依赖内部构造签名）。"""

    def __init__(self, message: str, cause=None, type: str | None = None):
        super().__init__(message)
        self.cause = cause
        self.type = type


def test_root_cause_via_cause_attr_chain():
    """temporalio .cause 属性链：外层无 type，内层 ApplicationError 带 type → 取内层 type/message。"""
    inner = ApplicationError(
        "Target http://localhost:4000 resolves to loopback address 127.0.0.1",
        type="InvalidTargetError",
    )
    outer = _FakeTemporalError("workflow failed", cause=inner)
    rc = extract_root_cause(outer)
    assert rc.error_type == "InvalidTargetError"
    assert "loopback" in rc.message


def test_root_cause_via_dunder_cause_chain():
    """Python __cause__ 链（raise X from Y）同样能挖到带 type 的内层。"""
    inner = ApplicationError("deep config err", type="ConfigurationError")
    outer = RuntimeError("wrap")
    outer.__cause__ = inner
    rc = extract_root_cause(outer)
    assert rc.error_type == "ConfigurationError"
    assert "deep config err" in rc.message


def test_root_cause_falls_back_to_classify_when_no_type():
    """链上无 .type 时，对最深层异常跑 classify 兜底分类。"""
    rc = extract_root_cause(ValueError("authentication failed boom"))
    assert rc.error_type == "AuthenticationError"  # classify 命中 "authentication"


def test_root_cause_message_from_deepest():
    rc = extract_root_cause(RuntimeError("shallow msg"))
    assert rc.message == "shallow msg"


def test_root_cause_prefers_shallowest_typed_in_multi_layer_chain():
    """temporalio 序列化 ``raise ApplicationFailure(type=语义) from e`` 的整条 cause：
    外层 AppError(type=activity 语义分类) → 深层 AppError(type=原始异常类名, 如 PentestError)。
    应选**最浅**的语义 type（activity 主动设的），而非深处被 worker 包装的异常类名噪声层。
    回归：真机 blackbox start loopback 曾因选错深层 PentestError 而走通用兜底（error_type=PentestError）。
    """
    deep = _FakeTemporalError("Target resolves to loopback", type="PentestError")
    shallow = _FakeTemporalError(
        "InvalidTargetError: Target resolves to loopback",
        cause=deep,
        type="InvalidTargetError",
    )
    rc = extract_root_cause(shallow)
    assert rc.error_type == "InvalidTargetError"
    assert "loopback" in rc.message


from supernova_core.cli.error_render import format_workflow_failure


def test_format_loopback_target():
    exc = ApplicationError(
        "Target http://localhost:4000 resolves to loopback address 127.0.0.1",
        type="InvalidTargetError",
    )
    out = format_workflow_failure(exc)
    assert "InvalidTargetError" in out
    assert "loopback" in out.lower() or "本机" in out
    assert "SSRF" in out or "ssrf" in out.lower()


def test_format_ssrf_target():
    exc = ApplicationError(
        "Target resolves to SSRF-sensitive IP 169.254.1.1", type="InvalidTargetError"
    )
    out = format_workflow_failure(exc)
    assert "169.254" in out or "SSRF" in out


def test_format_unresolvable_target():
    exc = ApplicationError("Cannot resolve hostname for http://x", type="InvalidTargetError")
    out = format_workflow_failure(exc)
    assert "解析" in out or "resolve" in out.lower()


def test_format_configuration_error():
    exc = ApplicationError("config missing field", type="ConfigurationError")
    out = format_workflow_failure(exc)
    assert "ConfigurationError" in out
    assert "配置" in out


def test_format_unknown_type_falls_back():
    """未命中映射的 error_type 走通用兜底（含原始 message）。"""
    out = format_workflow_failure(RuntimeError("something weird boom"))
    assert "TransientError" in out  # classify(RuntimeError 未知) → TransientError
    assert "something weird boom" in out


from supernova_core.cli.error_render import persist_workflow_traceback


def test_persist_writes_activity_failures_log(tmp_path):
    exc = ApplicationError("boom", type="InvalidTargetError")
    path = persist_workflow_traceback(exc, tmp_path)
    assert path == tmp_path / "activity_failures.log"
    content = path.read_text(encoding="utf-8")
    assert "boom" in content
    assert "workflow-level failure" in content


def test_persist_returns_none_when_no_workspace():
    assert persist_workflow_traceback(RuntimeError("x"), None) is None


def test_persist_appends_to_existing(tmp_path):
    log = tmp_path / "activity_failures.log"
    log.write_text("PREEXISTING\n", encoding="utf-8")
    persist_workflow_traceback(ApplicationError("second"), tmp_path)
    content = log.read_text(encoding="utf-8")
    assert "PREEXISTING" in content
    assert "second" in content
