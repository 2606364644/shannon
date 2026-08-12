"""Task 6: shared.py input dataclasses gain host_mappings / proxy_url fields.

These fields are optional with safe defaults so existing scans that do not set
them remain unaffected (full backward compat). T7 (activities) and T8 (workflow)
will populate/consume them.
"""

from supernova_blackbox.pipeline.shared import (
    BlackboxActivityInput,
    BlackboxPipelineInput,
)


def test_pipeline_input_host_mappings_default_empty():
    inp = BlackboxPipelineInput(web_url="http://x.test")
    assert inp.host_mappings == {}


def test_pipeline_input_host_mappings_set():
    inp = BlackboxPipelineInput(
        web_url="http://x.test",
        host_mappings={"x.test": "10.0.0.1"},
    )
    assert inp.host_mappings == {"x.test": "10.0.0.1"}


def test_pipeline_input_host_mappings_factory_isolation():
    """Each instance gets its own host_mappings dict (field(default_factory=dict))."""
    a = BlackboxPipelineInput(web_url="http://x.test")
    b = BlackboxPipelineInput(web_url="http://y.test")
    a.host_mappings["x.test"] = "10.0.0.1"
    assert b.host_mappings == {}


def test_activity_input_defaults():
    a = BlackboxActivityInput(web_url="http://x.test")
    assert a.host_mappings == {}
    assert a.proxy_url is None


def test_activity_input_host_mappings_set():
    a = BlackboxActivityInput(
        web_url="http://x.test",
        host_mappings={"x.test": "10.0.0.1"},
    )
    assert a.host_mappings == {"x.test": "10.0.0.1"}


def test_activity_input_proxy_url():
    a = BlackboxActivityInput(
        web_url="http://x.test",
        proxy_url="http://127.0.0.1:9090",
    )
    assert a.proxy_url == "http://127.0.0.1:9090"


def test_activity_input_host_mappings_factory_isolation():
    """Each instance gets its own host_mappings dict."""
    a = BlackboxActivityInput(web_url="http://x.test")
    b = BlackboxActivityInput(web_url="http://y.test")
    a.host_mappings["x.test"] = "10.0.0.1"
    assert b.host_mappings == {}
