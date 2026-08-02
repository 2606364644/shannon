"""黑盒 ScanRequest model_validator（阶段 2）：blackbox 必填 reuse_whitebox_scan_id 且禁 source。

黑盒 = 白盒下游 exploitation-only，恒复用白盒结果。model_validator 在 pydantic 反序列化层
拦截（POST /api/scan 的 ScanRequest 参数），非法 → ValidationError → FastAPI 422。
"""
import pytest
from pydantic import ValidationError

from supernova_web.models import ScanRequest


def test_blackbox_without_reuse_raises():
    """blackbox 无 reuse_whitebox_scan_id → ValidationError（422）。"""
    with pytest.raises(ValidationError):
        ScanRequest(type="blackbox", url="http://x.com", workspace="ws1")


def test_blackbox_with_reuse_is_valid():
    """blackbox 带 reuse_whitebox_scan_id → 合法。"""
    req = ScanRequest(
        type="blackbox", url="http://x.com", workspace="ws1",
        reuse_whitebox_scan_id="scan-123",
    )
    assert req.reuse_whitebox_scan_id == "scan-123"


def test_blackbox_with_repo_source_raises():
    """blackbox 禁 source（防 API 直传 repo 绕过前端 reuse）。"""
    with pytest.raises(ValidationError):
        ScanRequest(
            type="blackbox", url="http://x.com", workspace="ws1",
            reuse_whitebox_scan_id="scan-123",
            source={"kind": "repo", "value": "foo"},
        )


def test_whitebox_unchanged_by_blackbox_rule():
    """白盒不受黑盒约束（source.repo 仍合法，无 reuse 也行）。"""
    req = ScanRequest(
        type="whitebox", workspace="ws1",
        source={"kind": "repo", "value": "foo"},
    )
    assert req.source is not None
    assert req.reuse_whitebox_scan_id is None
