"""Regression anchor: the authz GitNexus judge activity must be registered with
the Temporal worker (the 3-point-sync gotcha: define / call / register).
A unit test patches the activity, so it never exercises real dispatch — this
source-level check is the only thing that catches a missing worker registration
before a real run silently no-ops the GitNexus track."""
from pathlib import Path


def test_authz_gitnexus_judge_registered_in_worker():
    worker = Path(__file__).resolve().parents[1] / "src/supernova_whitebox/worker.py"
    src = worker.read_text()
    # Must appear in BOTH the import block AND the activities=[...] list.
    assert src.count("run_authz_gitnexus_judge") >= 2, (
        "run_authz_gitnexus_judge must be imported AND listed in worker.py activities"
    )


def test_merge_dual_track_queues_registered_in_worker():
    """The Plan 3 merger (consumes authz_gitnexus_queue.json) must be registered too."""
    worker = Path(__file__).resolve().parents[1] / "src/supernova_whitebox/worker.py"
    src = worker.read_text()
    assert src.count("run_merge_dual_track_queues") >= 2, (
        "run_merge_dual_track_queues must be imported AND listed in worker.py activities"
    )


def test_authz_gitnexus_judge_is_activity_defn():
    """运行时验证 @activity.defn 装饰器归属(源码计数测试抓不到)。

    d45bde7e 回归:在 run_authz_gitnexus_judge 上方插入 _parse_gitnexus_verdict_output
    helper 时,@activity.defn 装饰器(Python 装饰紧随其后的 def)错位到 helper 头上,
    真 activity run_authz_gitnexus_judge 失去装饰器 → worker 注册报
    'Activity run_authz_gitnexus_judge missing attributes, was it decorated with @activity.defn?'。
    源码 count 测试只数名字出现次数,不看装饰器归属,故漏网。"""
    from supernova_whitebox.pipeline import activities

    assert hasattr(
        activities.run_authz_gitnexus_judge, "__temporal_activity_definition"
    ), "run_authz_gitnexus_judge 缺 @activity.defn 装饰器 → worker 注册失败"


def test_parse_gitnexus_verdict_output_not_activity_defn():
    """装饰器错位的另一侧:普通同步 helper 不该被 @activity.defn 装饰。"""
    from supernova_whitebox.pipeline import activities

    assert not hasattr(
        activities._parse_gitnexus_verdict_output, "__temporal_activity_definition"
    ), "_parse_gitnexus_verdict_output 是普通 helper,不该被 @activity.defn 装饰"


def test_all_worker_registered_activities_are_defn():
    """通用防护:worker activities=[...] 里每个名字都必须真被 @activity.defn 装饰
    (运行时带 __temporal_activity_definition)。

    d45bde7e 教训:在已有 activity 上方插入 helper 时,@activity.defn 会错位装饰 helper
    而非原 activity(装饰器只装饰紧随其后的 def)。源码 count 测试只数名字、不看装饰器
    归属,漏网 → 真机扫描才暴 'missing attributes, was it decorated with @activity.defn?'。
    本测试遍历 worker 注册全集,任何 activity 缺装饰器都会在 CI 暴露,不必等真机。"""
    import ast

    import supernova_whitebox.worker as worker_mod

    tree = ast.parse(Path(worker_mod.__file__).read_text())
    registered = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.keyword)
            and node.arg == "activities"
            and isinstance(node.value, ast.List)
        ):
            registered += [
                elt.id for elt in node.value.elts if isinstance(elt, ast.Name)
            ]
    assert registered, "未解析到 worker activities=[...] 列表(测试自身失效?)"

    missing = [
        name
        for name in registered
        if not hasattr(getattr(worker_mod, name, None), "__temporal_activity_definition")
    ]
    assert not missing, f"这些 worker 注册的 activity 缺 @activity.defn 装饰器: {missing}"
