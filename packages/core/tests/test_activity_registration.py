"""assert_all_activities_registered 护栏的单测。

用合成源码验证三种情形：注册==定义通过、missing 报错、extra 报错，
以及两个纯提取函数只抓该抓的节点。
"""
import textwrap
from types import ModuleType

import pytest

from supernova_core.testing.activity_registration import (
    _activity_def_names,
    _registered_activity_names,
    assert_all_activities_registered,
)


def test_activity_def_names_picks_up_decorated_only():
    source = textwrap.dedent("""
        import activity
        @activity.defn
        async def alpha(input): ...
        @activity.defn(name="custom")
        async def beta(input): ...
        async def not_an_activity(input): ...
    """)
    assert _activity_def_names(source) == {"alpha", "beta"}


def test_registered_activity_names_collects_worker_list():
    source = "worker = Worker(client, activities=[alpha, beta, gamma])\n"
    assert _registered_activity_names(source) == {"alpha", "beta", "gamma"}


def _make_module(name: str, file_path, source: str) -> ModuleType:
    file_path.write_text(source, encoding="utf-8")
    mod = ModuleType(name)
    mod.__file__ = str(file_path)
    return mod


def test_assert_passes_when_registered_equals_defined(tmp_path):
    activities_src = textwrap.dedent("""
        import activity
        @activity.defn
        async def alpha(input): ...
        @activity.defn
        async def beta(input): ...
    """)
    worker_src = "worker = Worker(client, activities=[alpha, beta])\n"
    worker = _make_module("fake_worker", tmp_path / "worker.py", worker_src)
    activities = _make_module("fake_activities", tmp_path / "activities.py", activities_src)
    assert_all_activities_registered(worker, [activities])  # 不抛即通过


def test_assert_reports_missing(tmp_path):
    activities_src = textwrap.dedent("""
        import activity
        @activity.defn
        async def alpha(input): ...
        @activity.defn
        async def forgotten(input): ...
    """)
    worker_src = "worker = Worker(client, activities=[alpha])\n"
    worker = _make_module("fake_worker", tmp_path / "worker.py", worker_src)
    activities = _make_module("fake_activities", tmp_path / "activities.py", activities_src)
    with pytest.raises(AssertionError, match="missing.*forgotten"):
        assert_all_activities_registered(worker, [activities])


def test_assert_reports_extra(tmp_path):
    activities_src = textwrap.dedent("""
        import activity
        @activity.defn
        async def alpha(input): ...
    """)
    worker_src = "worker = Worker(client, activities=[alpha, ghost])\n"
    worker = _make_module("fake_worker", tmp_path / "worker.py", worker_src)
    activities = _make_module("fake_activities", tmp_path / "activities.py", activities_src)
    with pytest.raises(AssertionError, match="extra.*ghost"):
        assert_all_activities_registered(worker, [activities])
