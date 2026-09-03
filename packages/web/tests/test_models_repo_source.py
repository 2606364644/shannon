import pytest
from pydantic import TypeAdapter, ValidationError
from supernova_web.models import ScanRequest, Source


def test_repo_source_accepted():
    req = ScanRequest(type="whitebox", source={"kind": "repo", "value": "foo"})
    assert req.source is not None and req.source.kind == "repo"
    assert req.source.value == "foo"


def test_git_source_rejected():
    with pytest.raises(ValidationError):
        ScanRequest(type="whitebox", source={"kind": "git", "value": "https://x.git"})


def test_source_union_discriminates():
    ta = TypeAdapter(Source)
    assert ta.validate_python({"kind": "path", "value": "/x"}).kind == "path"
    assert ta.validate_python({"kind": "repo", "value": "foo"}).kind == "repo"


def test_mr_requires_repo_source_and_refs():
    req = ScanRequest(type="mr", source={"kind": "repo", "value": "foo"},
                      base_ref="main", head_ref="feature/x")
    assert req.base_ref == "main" and req.head_ref == "feature/x"


def test_mr_rejects_without_head_ref():
    with pytest.raises(ValidationError):
        ScanRequest(type="mr", source={"kind": "repo", "value": "foo"}, base_ref="main")


def test_mr_rejects_non_repo_source():
    with pytest.raises(ValidationError):
        ScanRequest(type="mr", source={"kind": "path", "value": "/tmp/x"},
                    base_ref="main", head_ref="feature/x")
