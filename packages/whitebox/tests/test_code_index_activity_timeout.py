"""防回退: run_code_index activity start_to_close_timeout = 20min。

文件级聚合后 sink+source+taint 三阶段累加仍偏紧, 10min 容不下大仓(真机
kol_mapping_service 撞 timeout)-> 曾提至 45min(spec 2026-07-10 §3.2)。
2026-08-04 降回 20min: GitNexusMCPClient 已给 create_subprocess_exec / stdin.drain
加超时(P0+P1), MCP 子进程启动偶发卡死 30s 内自拔 -> activity 重试, 不再裸等 45min。
正常查询实测 ~6min(delivery 仓), 20min 留足余量。常量化(非内联 timedelta)以便此处
锁定 + 未来单点调整。LLM-track 开/关两条 run_code_index 路径共用此常量(workflows.py)。
"""
from datetime import timedelta

from supernova_whitebox.pipeline.workflows import CODE_INDEX_ACTIVITY_TIMEOUT


def test_code_index_activity_timeout_is_20_minutes():
    """run_code_index activity timeout = 20min(P0+P1 落地后, MCP 卡死 30s 内自拔重试)。"""
    assert CODE_INDEX_ACTIVITY_TIMEOUT == timedelta(minutes=20)
