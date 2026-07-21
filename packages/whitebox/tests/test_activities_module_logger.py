"""Anchor: activities 模块必须有模块级 logger。
run_gitnexus_chain_verdict 的错误降级路径（code_index.json parse 失败 @780、
per-class builder 失败 @801）调用 logger.warning；若无模块级 logger 会 NameError，
导致接通后一个 class 失败整批中断（违背 per-class 隔离设计）。"""
import logging

from supernova_whitebox.pipeline import activities


def test_activities_has_module_logger():
    assert hasattr(activities, "logger"), (
        "activities 模块必须定义模块级 logger（run_gitnexus_chain_verdict 的 "
        "logger.warning 降级路径依赖它）"
    )
    assert isinstance(activities.logger, logging.Logger)
