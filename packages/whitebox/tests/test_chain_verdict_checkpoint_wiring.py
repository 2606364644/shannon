"""chain_verdict 逐链 checkpoint 活动侧接线（2026-08-28 事故修）。

源码级 wiring 断言（对齐 test_persist_completed_agents 的挂点测试模式）：
run_gitnexus_chain_verdict 经 _run_builder 包装器覆盖 3 个单跳 builder
（injection/xss/ssrf 共用一处调用点）+ second_order 直调，都必须传
verdict_checkpoint（漏一处则该类重试仍全量重跑）。
"""

import inspect

from supernova_whitebox.pipeline import activities


def test_activity_passes_checkpoint_to_all_builders():
    """_run_builder 包装器（覆盖 3 单跳 builder）+ second_order 直调都带 checkpoint。"""
    src = inspect.getsource(activities.run_gitnexus_chain_verdict)
    # 包装器一处：_run_builder 内 builder(...) 调用带 _verdict_ckpt(vc)
    # （injection/xss/ssrf 三类都经此路径，漏了则三类重试全量重跑）。
    assert src.count("verdict_checkpoint=_verdict_ckpt(vc)") == 1
    # second_order 直调一处。
    assert src.count('verdict_checkpoint=_verdict_ckpt("2nd")') == 1


def test_activity_ckpt_helper_per_class_file():
    """每类一文件（chain_verdict_checkpoint_{label}.json）——防跨 gather 写竞争。"""
    src = inspect.getsource(activities.run_gitnexus_chain_verdict)
    assert 'chain_verdict_checkpoint_{label}.json' in src
