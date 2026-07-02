# packages/web/tests/test_multi_repo_config_store.py
import pytest
from pydantic import ValidationError

from shannon_web.components.multi_repo_config_store import MultiRepoConfigStore

_VALID = """\
repos:
  svc-a:
    path: /code/a
    role: entrypoint
  svc-b:
    path: /code/b
    role: backend
relations:
  - from: svc-a
    to: svc-b
correlation:
  out_workspace: cor-out
"""


def test_write_read_list(tmp_path):
    store = MultiRepoConfigStore(tmp_path)
    store.write("demo", _VALID)
    assert "demo" in store.list_configs()
    assert "repos" in store.read("demo")


def test_invalid_yaml_raises_validation_error(tmp_path):
    store = MultiRepoConfigStore(tmp_path)
    with pytest.raises(ValidationError):
        store.write("bad", "repos: not-a-mapping\n")


def test_temp_write_validates_and_persists(tmp_path):
    store = MultiRepoConfigStore(tmp_path)
    p = store.write_temp(_VALID)
    assert p.exists()
    assert "tmp-" in p.stem


def test_path_traversal_rejected(tmp_path):
    store = MultiRepoConfigStore(tmp_path)
    with pytest.raises(ValueError):
        store.read("../etc")
