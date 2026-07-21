"""防回退: run_code_index activity start_to_close_timeout = 45min(spec 2026-07-10 §3.2)。

文件级聚合后 sink+source+taint 三阶段累加仍偏紧, 10min 容不下大仓(真机
kol_mapping_service 撞 timeout)→ 提至 45min(2026-07-17 Koa+Sequelize 治本:三阶段
串行最坏 30+min)。常量化(非内联 timedelta)以便此处锁定 + 未来单点调整。LLM-track
开/关两条 run_code_index 路径共用此常量(workflows.py)。
"""
from datetime import timedelta

from supernova_whitebox.pipeline.workflows import CODE_INDEX_ACTIVITY_TIMEOUT


def test_code_index_activity_timeout_is_45_minutes():
    """run_code_index activity timeout = 45min(治本: 容下文件级三阶段累加 + Koa 治本)。"""
    assert CODE_INDEX_ACTIVITY_TIMEOUT == timedelta(minutes=45)
