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
